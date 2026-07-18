"""Read-only helpers for replaying the private Saleae and raw-state evidence."""

from __future__ import annotations

import bisect
import csv
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Tuple


@dataclass(frozen=True)
class CapturedByte:
    time: float
    direction: str
    value: int


@dataclass(frozen=True)
class CapturedFrame:
    time: float
    direction: str
    data: bytes


def _parse_direction_stream(
    items: list[CapturedByte],
) -> tuple[list[CapturedFrame], int]:
    frames = []
    rejects = 0
    index = 0
    while index + 3 < len(items):
        item = items[index]
        if item.value != 0x12:
            index += 1
            continue
        length = items[index + 1].value
        if length < 4 or length > 0xFC or index + length > len(items):
            index += 1
            continue
        data = bytes(entry.value for entry in items[index : index + length])
        checksum = 0
        for byte in data:
            checksum ^= byte
        if checksum:
            rejects += 1
            index += 1
            continue
        frames.append(CapturedFrame(item.time, item.direction, data))
        index += length
    return frames, rejects


def load_saleae_frames(path: Path) -> tuple[list[CapturedFrame], int]:
    """Remove K-Line echo and parse valid DS2 frames from a UART CSV export."""

    rows = []
    with Path(path).open(newline="", encoding="utf-8-sig") as stream:
        for row in csv.DictReader(stream):
            direction = row.get("name", "").strip().strip('"')
            if direction not in ("TX", "RX"):
                continue
            try:
                rows.append(
                    (
                        float(row["start_time"]),
                        direction,
                        int(row["data"].strip().strip('"'), 16),
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
    rows.sort()

    tx = []
    ecu = []
    recent_tx = []
    for time, direction, value in rows:
        if direction == "TX":
            item = CapturedByte(time, "HOST", value)
            tx.append(item)
            recent_tx.append(item)
            recent_tx = recent_tx[-8:]
            continue
        echo = any(
            candidate.value == value and 0 <= time - candidate.time <= 0.00003
            for candidate in reversed(recent_tx)
        )
        if not echo:
            ecu.append(CapturedByte(time, "ECU", value))

    tx_frames, tx_rejects = _parse_direction_stream(tx)
    ecu_frames, ecu_rejects = _parse_direction_stream(ecu)
    return (
        sorted(tx_frames + ecu_frames, key=lambda frame: frame.time),
        tx_rejects + ecu_rejects,
    )


def saleae_window(path: Path) -> tuple[float, float]:
    starts = []
    ends = []
    with Path(path).open(newline="", encoding="utf-8-sig") as stream:
        for row in csv.DictReader(stream):
            try:
                start = float(row["start_time"])
                duration = float(row.get("duration", 0) or 0)
            except (KeyError, TypeError, ValueError):
                continue
            starts.append(start)
            ends.append(start + duration)
    if not starts:
        raise ValueError(f"capture has no decoded byte rows: {path}")
    return min(starts), max(ends)


class _Signal:
    def __init__(self, times: list[float], values: list[int]):
        self.times = times
        self.values = values

    def at(self, time: float) -> int:
        index = bisect.bisect_right(self.times, time) - 1
        return self.values[index] if index >= 0 else 1

    def falling_edges(self, start: float, end: float) -> list[float]:
        first = bisect.bisect_left(self.times, start)
        last = bisect.bisect_right(self.times, end)
        return [
            self.times[index]
            for index in range(max(1, first), last)
            if self.values[index - 1] == 1 and self.values[index] == 0
        ]


@dataclass(frozen=True)
class _UartByte:
    time: float
    channel: int
    value: int


@lru_cache(maxsize=4)
def _load_signals(path: Path) -> Tuple[_Signal, _Signal]:
    times = [[0.0], [0.0]]
    values = [[1], [1]]
    previous = [1, 1]
    with Path(path).open(newline="", encoding="utf-8-sig") as stream:
        for row in csv.DictReader(stream):
            time = float(row["Time [s]"])
            state = [int(row["Channel 0"]), int(row["Channel 1"])]
            for channel in (0, 1):
                if state[channel] != previous[channel]:
                    times[channel].append(time)
                    values[channel].append(state[channel])
                    previous[channel] = state[channel]
    return _Signal(times[0], values[0]), _Signal(times[1], values[1])


def _decode_8e2(
    signal: _Signal,
    channel: int,
    start: float,
    end: float,
    baud: float,
) -> list[_UartByte]:
    period = 1.0 / baud
    decoded = []
    cursor = start
    for edge in signal.falling_edges(start, end):
        if edge < cursor or edge + 11.5 * period > end:
            continue
        if signal.at(edge + 0.5 * period) != 0:
            continue
        bits = [signal.at(edge + (1.5 + offset) * period) for offset in range(8)]
        parity = signal.at(edge + 9.5 * period)
        stop1 = signal.at(edge + 10.5 * period)
        stop2 = signal.at(edge + 11.5 * period)
        if (sum(bits) + parity) % 2 or stop1 != 1 or stop2 != 1:
            continue
        value = sum(bit << offset for offset, bit in enumerate(bits))
        decoded.append(_UartByte(edge, channel, value))
        cursor = edge + 11.75 * period
    return decoded


def _remove_raw_echo(
    tx: list[_UartByte],
    rx: list[_UartByte],
    tolerance: float,
) -> list[_UartByte]:
    tx_index = 0
    ecu = []
    for item in rx:
        while tx_index < len(tx) and tx[tx_index].time < item.time - tolerance:
            tx_index += 1
        candidates = tx[max(0, tx_index - 2) : tx_index + 3]
        if any(
            candidate.value == item.value
            and 0 <= item.time - candidate.time <= tolerance
            for candidate in candidates
        ):
            continue
        ecu.append(item)
    return ecu


def decode_raw_phase(
    raw_path: Path,
    *,
    start: float,
    end: float,
    host_baud: float,
    ecu_baud: float,
) -> tuple[list[CapturedFrame], int]:
    """Reconstruct one fixed-rate phase from a raw two-channel state capture."""

    tx_signal, rx_signal = _load_signals(Path(raw_path))
    tx = _decode_8e2(tx_signal, 0, start, end, host_baud)
    rx = _decode_8e2(rx_signal, 1, start, end, ecu_baud)
    tolerance = max(3e-6, 0.08 / min(host_baud, ecu_baud))
    ecu = _remove_raw_echo(tx, rx, tolerance)
    tx_items = [CapturedByte(item.time, "HOST", item.value) for item in tx]
    ecu_items = [CapturedByte(item.time, "ECU", item.value) for item in ecu]
    tx_frames, tx_rejects = _parse_direction_stream(tx_items)
    ecu_frames, ecu_rejects = _parse_direction_stream(ecu_items)
    return (
        sorted(tx_frames + ecu_frames, key=lambda frame: frame.time),
        tx_rejects + ecu_rejects,
    )


def decode_raw_like_saleae(
    raw_path: Path,
    saleae_path: Path,
    *,
    host_baud: float,
    ecu_baud: float,
) -> tuple[list[CapturedFrame], int]:
    start, end = saleae_window(saleae_path)
    padding = 16.0 / min(host_baud, ecu_baud)
    return decode_raw_phase(
        raw_path,
        start=max(0.0, start - padding),
        end=end + padding,
        host_baud=host_baud,
        ecu_baud=ecu_baud,
    )
