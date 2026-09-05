"""
live_data.py — MS41 live data parameter definitions and polling engine.

Standard mode: DS2 command 0x06 range reads from RAM.
Telegram mode: MS41 DS2 command 0x0B/0x01 — one registered-address response
  containing every displayed RAM parameter.

Standard and telegram modes use the same selected XML logger definition.
Addresses are absolute 24-bit C166 logical addresses for the Siemens 80C166
processor; the definition selects the applicable address for the detected ECU
ID and owns storage, scaling, units, and display formatting.

  Parameters without a mapped RAM address show "—". Standard and telegram
  acquisition use the same selected address/scaling table; standard mode is a
  transport fallback, not an independent validation source.
"""

import threading
import time
import csv
import os
import datetime
from collections import deque
from dataclasses import dataclass, field
from typing import Optional, Dict, Tuple, List

from logger_definition import (
    LoggerDefinition,
    LoggerParameter,
    bundled_logger_definition_path,
    load_logger_definition,
)


# ──────────────────────────── Definition-driven parameters ────────────────────

PROFILE_STANDARD = "standard"
PROFILE_WIDEBAND = "wideband"

_WBO2_ENABLE_ADDR = 0xFD22
_WBO2_ENABLE_MASK = 0x0100
_NARROWBAND_EMULATION_ADDR = 0xFD5A
_NARROWBAND_EMULATION_MASK = 0x0004
# Calibration SA 0x33C0 is visible to a DS2 CPU-memory read at 0x133C0.
# Its little-endian word is the live 10-bit ADC source pointer used by FUN_024570.
_WBO2_INPUT_SELECT_DS2_ADDR = 0x133C0
_DEFAULT_WBO2_INPUT_ADDR = 0xFA9A

_WBO2_INPUT_SOURCES = {
    0xE8DA: "TPS Voltage",
    0xFAA2: "TPS Base Voltage",
    0xFA96: "Rear O2 Bank 1",
    0xFA94: "Rear O2 Bank 2",
    0xFA98: "Front O2 Bank 2",
    0xFA9A: "Front O2 Bank 1",
    0xF5A8: "EVAP Pressure",
}


_PROFILE_DISPLAY_ROWS = [
    ("EVAP Purge Duty", "%"),
    ("Closed Throttle", ""),
    ("Part Load", ""),
    ("Full Load", ""),
    ("Decel Fuel Cut", ""),
    ("Engine Start", ""),
    ("Front O2 B1 Voltage", "V"),
    ("Front O2 B2 Voltage", "V"),
    ("MAF Sensor Voltage", "V"),
    ("Wideband Mode", ""),
    ("Wideband Input Source", ""),
    ("Narrowband Emulation", ""),
    ("Wideband Input Voltage", "V"),
    ("Wideband AFR", "AFR"),
    ("AFR Target", "AFR"),
]
PROFILE_DISPLAY_NAMES = frozenset(name for name, _unit in _PROFILE_DISPLAY_ROWS)
_PROFILE_STATUS_NAMES = frozenset({
    "Wideband Mode", "Wideband Input Source", "Narrowband Emulation",
})

# Non-gauge presentation kinds remain code-owned for runtime probes;
# every dial range comes from the selected definition.
_LIVE_STATUS_CHANNELS = frozenset({
    ("Closed Throttle", ""), ("Part Load", ""), ("Full Load", ""),
    ("Decel Fuel Cut", ""), ("Engine Start", ""),
    ("Wideband Mode", ""), ("Narrowband Emulation", ""),
})

_LIVE_VALUE_CHANNELS = frozenset({
    ("Injector PW", "ms"), ("Wideband Input Source", ""),
})

def live_display_spec(name: str, unit: str, definition_path=None) -> dict:
    """Return evidence-bounded viewer metadata for an exact live-data channel."""
    key = (name, unit)
    for parameter in _load_definition(definition_path).parameters:
        if (parameter.name, parameter.unit) != key:
            continue
        minimum, maximum, step = (
            parameter.gauge_min, parameter.gauge_max, parameter.gauge_step)
        if (minimum is not None and maximum is not None and step is not None
                and minimum < maximum and 0 < step <= maximum - minimum):
            return {
                "kind": "dial", "minimum": minimum, "maximum": maximum,
                "step": step, "source": parameter.id,
                "evidence": "definition",
            }
        break
    if key in _LIVE_STATUS_CHANNELS:
        return {"kind": "status", "evidence": "core"}
    return {
        "kind": "value",
        "evidence": "core" if key in _LIVE_VALUE_CHANNELS else "unknown",
    }

# Definition-derived axis locations converted to live DS2 CPU addresses.
# Only variants with proven Knock Tables X/Y definitions are listed.
_ADAPTATION_AXES = {
    "1437806": (0x12606, 0x125D9),
    "1438068": (0x12606, 0x125D9),
    "1429861": (0x1239A, 0x1236D),
    "1432401": (0x1239A, 0x1236D),
    "1429373": (0x12144, 0x12117),
    "1406464": (0x12388, 0x1235B),
    "SHINDE1": (0x12388, 0x1235B),
}
# Fuel-adaptation RAM addresses are separate from the selectable live logger.
_ADAPTATION_FUEL_ADDRS = {
    "1437806": ((0xF040, 0xF048), (0xF0FC, 0xF104)),
    "1429861": ((0xED66, 0xED6E), (0xEDA0, 0xEDA8)),
    "1406464": ((0xF028, 0xF030), (0xF0D4, 0xF0DC)),
    "SHINDE1": ((0xF028, 0xF030), (0xF0D4, 0xF0DC)),
}
_KNOCK_ADAPTATION_ADDR = 0xD840


def _load_definition(definition_path=None) -> LoggerDefinition:
    return load_logger_definition(
        definition_path or bundled_logger_definition_path())


def telegram_params_for(ecu_id, profile: str = PROFILE_STANDARD,
                        wideband_input_addr: int = _DEFAULT_WBO2_INPUT_ADDR,
                        definition_path=None,
                        definition: LoggerDefinition | None = None
                        ) -> List[LoggerParameter]:
    """Resolve the selected XML definition for one exact ECU ID/profile."""
    if profile not in {PROFILE_STANDARD, PROFILE_WIDEBAND}:
        raise ValueError(f"unknown live-data profile {profile!r}")
    definition = definition or _load_definition(definition_path)
    params = []
    for param in definition.parameters_for(str(ecu_id or "")):
        # The XML format also permits ADC selectors and predefined group offsets;
        # this owner only issues absolute DS2 memory reads for those channels.
        if param.address < 0x20 or param.groupsize:
            continue
        if param.id.startswith("BS_STD_") and profile != PROFILE_STANDARD:
            continue
        if param.id.startswith("BS_WB_") and profile != PROFILE_WIDEBAND:
            continue
        if param.id == "BS_WB_INPUT":
            param = param.with_address(wideband_input_addr)
        params.append(param)
    return params


def live_data_supported(ecu_id, definition_path=None) -> bool:
    """Whether the selected definition has at least one exact-ID live channel."""
    return bool(telegram_params_for(ecu_id, definition_path=definition_path))


def adaptation_read_supported(ecu_id) -> bool:
    """Whether the packaged definition owner has exact knock-table axes."""
    return str(ecu_id or "") in _ADAPTATION_AXES


def read_adaptations(ds2, ecu_id):
    """Read stored fuel, throttle, and six 16x4 knock-adaptation tables."""
    ecu_id = str(ecu_id or "")
    axes = _ADAPTATION_AXES.get(ecu_id)
    if axes is None:
        raise ValueError(
            f"adaptation-table addresses are not mapped for ECU ID {ecu_id or 'unknown'}")

    def read_exact(address, length):
        data = bytes(ds2.read_mem(address, length))
        if len(data) != length:
            raise ValueError(
                f"short adaptation read at 0x{address:05X}: {len(data)}/{length} bytes")
        return data

    additive, ltft = [None, None], [None, None]
    if ecu_id in _ADAPTATION_FUEL_ADDRS:
        for index, (add_address, lt_address) in enumerate(
                _ADAPTATION_FUEL_ADDRS[ecu_id]):
            block = read_exact(add_address, lt_address - add_address + 2)
            add_raw = int.from_bytes(block[:2], "little")
            lt_raw = int.from_bytes(
                block[lt_address - add_address:lt_address - add_address + 2],
                "little")
            additive[index] = (add_raw - 32768) * 0.00534
            ltft[index] = (lt_raw - 32768) * 100 / 65535

    throttle_raw = int.from_bytes(read_exact(0xE8DE, 2), "little", signed=True)
    load_address, rpm_address = axes
    load = [value * 1389 / 255 for value in read_exact(load_address, 4)]
    rpm = [value * 32 for value in read_exact(rpm_address, 16)]

    tables = []
    for index in range(6):
        raw = read_exact(_KNOCK_ADAPTATION_ADDR + index * 0x40, 0x40)
        values = [(value - 128) * 0.375 for value in raw]
        tables.append([values[row:row + 4] for row in range(0, 64, 4)])

    return {
        "ecu_id": ecu_id,
        "additive": additive,
        "ltft": ltft,
        "throttle": throttle_raw * 0.001526,
        "load": load,
        "rpm": rpm,
        "knock": tables,
    }


# Default bundled metadata keeps the desktop logger unchanged. Callers can pass
# a selected definition path into the same functions.
_BUNDLED_DEFINITION = _load_definition()
_TELEGRAM_PARAMS: List[LoggerParameter] = telegram_params_for(
    "1437806", definition=_BUNDLED_DEFINITION)
TELEGRAM_PARAM_NAMES = frozenset(
    param.name for param in _BUNDLED_DEFINITION.parameters
) | _PROFILE_STATUS_NAMES


def display_rows(definition_path=None) -> List[Tuple[str, str]]:
    """Ordered rows supplied by the selected definition plus runtime probes."""
    definition = _load_definition(definition_path)
    rows = list(dict.fromkeys(
        (param.name, param.unit) for param in definition.parameters))
    existing = {name for name, _unit in rows}
    rows.extend(
        row for row in _PROFILE_DISPLAY_ROWS
        if row[0] in _PROFILE_STATUS_NAMES and row[0] not in existing
    )
    return rows


# The proven DS2 0x0B transport has exactly 24 positional slots. Static
# addresses, widths, storage, and conversions still come from the definition.
_BATCH_HEAD_NAMES = (
    "Idle Valve Pos", "Injector PW", "Ignition Advance", "Knock Retard",
    "Vehicle Speed", "Throttle Position", "Engine RPM", "Mass Air Flow",
    "Coolant Temp", "Intake Air Temp", "Battery Voltage",
    "Knock Retard (Global)", "VANOS Measured Angle",
)
_BATCH_TRIM_NAMES = (
    "Fuel Trim LT", "Fuel Trim LT B2", "Fuel Trim ST", "Fuel Trim ST B2",
)
_BATCH_STATE_GROUPS = (
    ("Closed Throttle", "Full Load"),
    ("Part Load", "Decel Fuel Cut", "Engine Start"),
)


def _batch_entry(param: LoggerParameter, *, address=None, length=None,
                 signed=None, convert=None):
    return (
        param.name,
        param.address if address is None else address,
        param.length if length is None else length,
        param.signed if signed is None else signed,
        param.convert if convert is None else convert,
        param.unit,
        param.fmt,
    )


def batch_layout_for(ecu_id, profile: str = PROFILE_STANDARD,
                     wideband_input_addr: int = _DEFAULT_WBO2_INPUT_ADDR,
                     definition_path=None,
                     definition: LoggerDefinition | None = None):
    """Build the fixed telegram layout only when the definition fits it exactly."""
    params = telegram_params_for(
        ecu_id, profile, wideband_input_addr,
        definition_path=definition_path, definition=definition)
    by_name = {param.name: param for param in params}
    profile_name = (
        "Wideband AFR" if profile == PROFILE_WIDEBAND else "EVAP Purge Duty")
    tail_names = (
        ("Wideband Input Voltage", "MAF Sensor Voltage", "AFR Target")
        if profile == PROFILE_WIDEBAND else
        ("Front O2 B1 Voltage", "Front O2 B2 Voltage", "MAF Sensor Voltage")
    )
    required = (
        _BATCH_HEAD_NAMES + (profile_name,) + _BATCH_TRIM_NAMES
        + tuple(name for group in _BATCH_STATE_GROUPS for name in group)
        + ("Engine Load",) + tail_names
    )
    if len(params) != len(required) or set(by_name) != set(required):
        return None

    state_entries = []
    for names in _BATCH_STATE_GROUPS:
        state_params = tuple(by_name[name] for name in names)
        if (len({param.address for param in state_params}) != 1
                or any(param.length != 1 for param in state_params)):
            return None
        state_entries.append((
            state_params, state_params[0].address, 1, False, None, "", ""))

    layout = [_batch_entry(by_name[name]) for name in _BATCH_HEAD_NAMES]
    layout.append(_batch_entry(by_name[profile_name]))
    layout.extend(_batch_entry(by_name[name]) for name in _BATCH_TRIM_NAMES[:2])
    for name in _BATCH_TRIM_NAMES[2:]:
        param = by_name[name]
        layout.append(_batch_entry(
            param, address=param.address + 1, length=1, signed=False,
            convert=lambda raw, owner=param: owner.convert(raw << 8)))
    layout.extend(state_entries)
    layout.append(_batch_entry(by_name["Engine Load"]))

    if profile == PROFILE_WIDEBAND:
        layout.append(_batch_entry(by_name["Wideband Input Voltage"]))
        layout.append(_batch_entry(by_name["MAF Sensor Voltage"]))
        target = by_name["AFR Target"]
        layout.append(_batch_entry(
            target, address=target.address - 1, length=2, signed=False,
            convert=lambda raw, owner=target: owner.convert(raw >> 8)))
    else:
        layout.extend(_batch_entry(by_name[name]) for name in tail_names)
    return tuple(layout)


DS2_BATCH_LAYOUT = batch_layout_for(
    "1437806", definition=_BUNDLED_DEFINITION)


def batch_wire_entries(layout):
    """Return the transport-only ``(address, byte_length)`` batch plan."""
    return tuple((entry[1], entry[2]) for entry in layout)

# Byte offsets in the 38-byte payload where each param value starts.
# Groups separated by 2-byte status headers at data[0:2] and data[28:30].
_G1_START = 2     # group 1 data begins after 2-byte group 1 status
_G2_START = 30    # group 2 data begins after group 1 data + 2-byte group 2 status


def _parse_ds2_batch(raw: bytes, latest: dict, lock, csv_row: dict,
                     layout=None) -> None:
    """Parse the 38-byte DS2 batch poll response into latest values."""
    if len(raw) < 38:
        return
    layout = layout or DS2_BATCH_LAYOUT

    # Build offset table: skip 2-byte group headers at boundaries
    offsets = []
    off = _G1_START
    for i, entry in enumerate(layout):
        if i == 20:           # group 2 starts at data[30]
            off = _G2_START
        offsets.append(off)
        off += entry[2]

    with lock:
        for i, (name, _addr, nbytes, signed, convert, unit, fmt) in enumerate(layout):
            start = offsets[i]
            end   = start + nbytes
            if end > len(raw):
                continue
            chunk = raw[start:end]
            # The 0x0B/0x01 telegram response serializes multi-byte values
            # BIG-endian (MSB first) — unlike a raw read_mem(), which returns
            # native little-endian RAM bytes. Parsing big-endian here makes the
            # batch (telegram) values match standard (cmd 0x06) mode. (Confirmed
            # on hardware: little-endian here gives wrong Fuel Trim LT/LT B2.)
            val   = int.from_bytes(chunk, "big")
            if signed and val >= (1 << (8 * nbytes - 1)):
                val -= 1 << (8 * nbytes)
            if isinstance(name, tuple):
                for parameter in name:
                    state_disp = parameter.display(parameter.convert(val))
                    latest[parameter.name] = (state_disp, parameter.unit)
                    csv_row[parameter.name] = state_disp
            elif convert is not None:
                physical = convert(val)
                disp = fmt.format(physical)
                latest[name] = (disp, unit)
                csv_row[name] = disp


@dataclass
class _TelBlock:
    """A contiguous memory range to be read in one DS2 command-0x06 call."""
    start:  int
    size:   int
    params: List[LoggerParameter] = field(default_factory=list)


def _build_telegram_blocks(params, max_span=120) -> List[_TelBlock]:
    """Greedily group params into block reads whose span stays within max_span bytes."""
    blocks = []
    for p in sorted(params, key=lambda q: q.address):
        end = p.address + p.length
        if blocks and end - blocks[-1].start <= max_span:
            b = blocks[-1]
            b.size = max(b.size, end - b.start)
            b.params.append(p)
        else:
            blocks.append(_TelBlock(start=p.address, size=p.length, params=[p]))
    return blocks

_TELEGRAM_BLOCKS: List[_TelBlock] = _build_telegram_blocks(_TELEGRAM_PARAMS)


# ──────────────────────────── Poller ─────────────────────────────────────────

class LiveDataPoller:
    """
    Polls MS41 live data on a background thread.

    Standard mode (use_telegram=False):
      Reads the selected RAM-address set via DS2 command 0x06, grouped into
      contiguous ranges (multiple round trips per sample).

    Telegram mode (use_telegram=True):
      Registers the selected MS41 RAM-address set via DS2 0x0B/0x01 and then
      retrieves the complete sample with one 0x0B/0x00 response.

    Stores latest values in a thread-safe dict; the GUI reads via a QTimer.
    Optionally writes every poll cycle to a CSV file.
    """

    def __init__(self, interval: float = 0.5, use_telegram: bool = False,
                 ecu_id=None, ecu_variant=None, ds2=None, log_columns=None,
                 telegram_fallback: bool = True, definition_path=None):
        self._ds2          = ds2          # DS2Interface — the live ECU connection
        self._interval     = interval
        self._use_telegram = use_telegram
        self._telegram_fallback = telegram_fallback
        self._ecu_id       = ecu_id
        self._ecu_variant  = ecu_variant
        self._profile      = PROFILE_STANDARD
        self._profile_ready = False
        self._wideband_input_addr = _DEFAULT_WBO2_INPUT_ADDR
        self._logger_definition = _load_definition(definition_path)
        # Resolve only parameters explicitly mapped for this ECU ID.
        self._tel_params   = telegram_params_for(
            ecu_id, definition=self._logger_definition)
        self._tel_blocks   = _build_telegram_blocks(self._tel_params)
        self._batch_layout = batch_layout_for(
            ecu_id, definition=self._logger_definition)
        self._log_columns = tuple(log_columns) if log_columns is not None else None
        self._active_profile_names = set()
        self._stop         = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock         = threading.Lock()
        self._latest:  Dict[str, Tuple[str, str]] = {}  # name → (value_str, unit)
        self._errors:  List[str] = []
        self._csv_file   = None
        self._csv_writer = None
        self._log_start  = 0.0   # time.time() at CSV open — used for elapsed-seconds column
        self._csv_rows   = 0     # rows written so far (GUI reads this for status display)
        self._csv_last_flush = 0.0
        self._samples = 0
        self._sample_started = 0.0
        self._sample_sequence = 0
        self._sample_channels = ()
        self._completed_samples = deque(maxlen=256)
        self._pending_log_path = None
        self._terminal_error = None

    # ── Public API ─────────────────────────────────────────────────────────

    def start(self, log_path: Optional[str] = None):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        with self._lock:
            self._latest.clear()
            self._errors.clear()
            self._terminal_error = None
            self._csv_rows = 0
            self._sample_sequence = 0
            self._sample_channels = ()
            self._completed_samples.clear()
        self._samples = 0
        self._sample_started = time.monotonic()
        self._pending_log_path = log_path
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=4)
            self._thread = None
        self._close_csv()

    def latest_values(self) -> Dict[str, Tuple[str, str]]:
        """Return a snapshot of the latest parameter values (thread-safe)."""
        with self._lock:
            return dict(self._latest)

    def resolved_rows(self) -> List[Tuple[str, str]]:
        """Ordered channels selected by the exact ECU/profile owner."""
        rows = [(param.name, param.unit) for param in self._tel_params]
        rows.extend(
            row for row in _PROFILE_DISPLAY_ROWS
            if row[0] in self._active_profile_names
        )
        return list(dict.fromkeys(rows))

    def display_values(self) -> List[Tuple[str, str, str]]:
        """Ordered display-ready values for the currently resolved channels."""
        latest = self.latest_values()
        return [
            (name, latest[name][0], latest[name][1])
            for name, _unit in self.resolved_rows()
            if name in latest and latest[name][0] != "—"
        ]

    def pop_errors(self) -> List[str]:
        with self._lock:
            errs, self._errors = list(self._errors), []
        return errs

    def completed_samples_since(self, after_sequence: int):
        """Return exact completed poll cycles newer than a bounded sequence cursor."""
        if type(after_sequence) is not int or after_sequence < 0:
            raise ValueError("Live Data sample cursor must be a non-negative integer")
        with self._lock:
            if after_sequence > self._sample_sequence:
                raise ValueError("Live Data sample cursor is ahead of the poller")
            oldest = (
                self._completed_samples[0][0]
                if self._completed_samples else self._sample_sequence + 1
            )
            dropped = max(0, oldest - after_sequence - 1)
            samples = tuple(
                sample for sample in self._completed_samples
                if sample[0] > after_sequence
            )
            return (
                self._sample_sequence,
                dropped,
                self._csv_rows,
                self._sample_channels,
                samples,
            )

    @property
    def terminal_error(self):
        with self._lock:
            return self._terminal_error

    @property
    def active_profile_names(self):
        """Optional live-table rows used by the detected profile."""
        return frozenset(self._active_profile_names)

    # ── Internal ────────────────────────────────────────────────────────────

    def _is_ms413(self) -> bool:
        return self._ecu_variant == "MS41.3" or str(self._ecu_id) == "SHINDE1"

    def _read_word_le(self, address: int) -> int:
        raw = bytes(self._ds2.read_mem(address, 2))
        if len(raw) != 2:
            raise ValueError(f"short read at 0x{address:05X}: {len(raw)}/2 bytes")
        return int.from_bytes(raw, "little")

    def _prepare_live_profile(self) -> None:
        """Select the universal or MS41.3 WBO2 profile once per polling session."""
        if self._profile_ready:
            return

        profile = PROFILE_STANDARD
        input_addr = _DEFAULT_WBO2_INPUT_ADDR
        status_values = {}
        if self._is_ms413():
            try:
                flags = self._read_word_le(_WBO2_ENABLE_ADDR)
                wideband_enabled = bool(flags & _WBO2_ENABLE_MASK)
                status_values["Wideband Mode"] = (
                    "Enabled" if wideband_enabled else "Disabled", "")
            except Exception as error:
                wideband_enabled = False
                status_values["Wideband Mode"] = ("Unavailable", "")
                with self._lock:
                    self._errors.append(
                        f"MS41.3 wideband status unavailable ({error}); using standard profile")

            try:
                nb_flags = self._read_word_le(_NARROWBAND_EMULATION_ADDR)
                status_values["Narrowband Emulation"] = (
                    "Enabled" if nb_flags & _NARROWBAND_EMULATION_MASK else "Disabled", "")
            except Exception as error:
                status_values["Narrowband Emulation"] = ("Unavailable", "")
                with self._lock:
                    self._errors.append(f"Narrowband-emulation status unavailable ({error})")

            if wideband_enabled:
                profile = PROFILE_WIDEBAND
                try:
                    selected = self._read_word_le(_WBO2_INPUT_SELECT_DS2_ADDR)
                    if selected == 0xFFFF or not 0xE000 <= selected <= 0xFFFF:
                        raise ValueError(f"invalid input pointer 0x{selected:04X}")
                    input_addr = selected
                    source = _WBO2_INPUT_SOURCES.get(selected, f"RAM 0x{selected:04X}")
                    status_values["Wideband Input Source"] = (
                        f"{source} (0x{selected:04X})", "")
                except Exception as error:
                    input_addr = _DEFAULT_WBO2_INPUT_ADDR
                    status_values["Wideband Input Source"] = (
                        "Front O2 Bank 1 fallback (0xFA9A)", "")
                    with self._lock:
                        self._errors.append(
                            f"Wideband input selection unavailable ({error}); "
                            "showing the Front O2 Bank 1 input")

        key = self._ecu_id
        self._profile = profile
        self._wideband_input_addr = input_addr
        self._tel_params = telegram_params_for(
            key, profile, input_addr,
            definition=self._logger_definition)
        self._tel_blocks = _build_telegram_blocks(self._tel_params)
        self._batch_layout = batch_layout_for(
            key, profile, input_addr, definition=self._logger_definition)
        active_names = {
            p.name for p in self._tel_params if p.name in PROFILE_DISPLAY_NAMES
        }
        active_names.update(status_values)
        self._active_profile_names = active_names
        with self._lock:
            self._latest.update(status_values)
        self._profile_ready = True

    def _poll_loop(self):
        # Live data is read over DS2: batch telegram (0x0B/0x01) or individual
        # block reads (0x06).  (MS41 has no working KWP2000 path.)
        if self._use_telegram:
            self._poll_loop_ds2_batch()
        else:
            self._poll_loop_ds2_reads()

    def _poll_loop_ds2_reads(self):
        """DS2 standard mode: individual block reads via DS2 cmd 0x06 (READ_MEM).

        Groups nearby RAM addresses into contiguous blocks and issues one read_mem
        per block.  More round-trips than the batch telegram, but simpler and more
        reliable.  It uses the same addresses and scaling as batch mode.
        """
        self._prepare_live_profile()
        self._ensure_csv()

        while not self._stop.is_set():
            cycle_started = time.monotonic()
            csv_row = self._csv_row_base()
            interrupted = False
            for block in self._tel_blocks:
                if self._stop.is_set():
                    interrupted = True
                    break
                try:
                    data = self._ds2.read_mem(block.start, block.size)
                    with self._lock:
                        for tp in block.params:
                            val  = tp.parse(data, block.start)
                            disp = tp.display(val)
                            self._latest[tp.name] = (disp, tp.unit)
                            csv_row[tp.name] = disp
                except Exception as e:
                    with self._lock:
                        for tp in block.params:
                            self._latest[tp.name] = ("ERR", tp.unit)
                        self._errors.append(
                            f"DS2 read block 0x{block.start:05X}: {e}"
                        )

            if interrupted:
                break            # Stop pressed mid-cycle — don't log a partial row
            self._write_csv_row(csv_row)
            self._samples += 1
            self._stop.wait(max(0.0, self._interval - (time.monotonic() - cycle_started)))

    def _poll_loop_ds2_batch(self):
        """DS2 telegram mode: batch read via DS2 cmd 0x0B/0x01 (MS41-specific).

        Sends the one-time setup frame, then polls ~112 ms apart.
        Response layout (38 bytes):
          [2b grp1_status][26b grp1_data][2b grp2_status][8b grp2_data]
        Parameter order and byte-widths match DS2_BATCH_LAYOUT.
        Every recurring sample is one poll response. Confirmed MS41.3 firmware uses
        small one-time reads before setup to select the correct live-data profile.
        Auto mode falls back to individual cmd 0x06 reads if setup is unsupported;
        forced Telegram stops with an error. Transient poll errors are retried without
        writing incomplete CSV rows.
        """
        self._prepare_live_profile()
        self._ensure_csv()
        if self._batch_layout is None:
            self._telegram_unavailable(
                "Telegram logging is not mapped for this ECU ID")
            return
        # Try to run the setup frame; if it fails, fall back to individual reads.
        try:
            self._ds2.setup_telegram_batch(
                ecu_id=self._ecu_id or "",
                entries=batch_wire_entries(self._batch_layout),
            )
        except Exception as e:
            self._telegram_unavailable(f"Telegram setup failed ({e})")
            return

        while not self._stop.is_set():
            cycle_started = time.monotonic()
            csv_row = self._csv_row_base()
            try:
                raw = self._ds2.poll_telegram_batch()
                if len(raw) != 38:
                    raise ValueError(f"short batch payload {len(raw)}/38 bytes")
                _parse_ds2_batch(
                    raw, self._latest, self._lock, csv_row, self._batch_layout)
            except Exception as e:
                with self._lock:
                    self._errors.append(f"DS2 batch poll: {e}")
                self._stop.wait(max(0.0, self._interval - (time.monotonic() - cycle_started)))
                continue
            self._write_csv_row(csv_row)
            self._samples += 1
            self._stop.wait(max(0.0, self._interval - (time.monotonic() - cycle_started)))

    def _telegram_unavailable(self, message):
        with self._lock:
            if self._telegram_fallback:
                self._errors.append(f"{message} — using standard DS2 reads")
            else:
                self._terminal_error = message
                self._errors.append(message)
        if self._telegram_fallback:
            self._poll_loop_ds2_reads()
        else:
            self._stop.set()

    @property
    def csv_rows(self) -> int:
        return self._csv_rows

    @property
    def sample_rate(self) -> float:
        elapsed = time.monotonic() - self._sample_started
        return self._samples / elapsed if self._sample_started and elapsed > 0 else 0.0

    def _open_csv(self, path: str):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self._csv_file  = open(path, "w", newline="", encoding="utf-8")
        self._log_start = time.time()
        self._csv_rows  = 0
        self._csv_last_flush = time.monotonic()
        if not self._sample_started:
            self._sample_started = time.monotonic()
        # Fixed schema regardless of polling mode so any CSV can be opened in MLV:
        #   Time     — elapsed seconds (float); MLV uses this as the primary time axis
        #   Datetime — human-readable full timestamp for post-processing reference
        #   then all standard params + telegram-only extras in display order
        param_cols = self._log_columns or [name for name, _unit in self.resolved_rows()]
        headers = ["Time", "Datetime"] + list(param_cols)
        self._csv_writer = csv.DictWriter(
            self._csv_file, fieldnames=headers, extrasaction="ignore"
        )
        self._csv_writer.writeheader()

    def _ensure_csv(self):
        if self._pending_log_path and self._csv_file is None:
            path, self._pending_log_path = self._pending_log_path, None
            self._open_csv(path)

    def _csv_row_base(self) -> dict:
        """Return the per-row dict pre-populated with Time and Datetime fields."""
        row = {
            "Time":     f"{time.time() - self._log_start:.3f}",
            "Datetime": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
        }
        with self._lock:
            for name in _PROFILE_STATUS_NAMES:
                if name in self._latest:
                    row[name] = self._latest[name][0]
        return row

    def _write_csv_row(self, row: dict):
        if not self._sample_started:
            self._sample_started = time.monotonic()
        wrote_csv = self._csv_writer is not None
        if self._csv_writer:
            self._csv_writer.writerow(row)
            # Buffer logger output: avoid a synchronous disk flush
            # in the serial acquisition loop for every sample, while still making an active
            # log visible on disk at least once per second.
            now = time.monotonic()
            if now - self._csv_last_flush >= 1.0:
                self._csv_file.flush()
                self._csv_last_flush = now
        channels = tuple(self.resolved_rows())
        elapsed = max(0.0, time.monotonic() - self._sample_started)
        with self._lock:
            if wrote_csv:
                self._csv_rows += 1
            self._sample_sequence += 1
            self._sample_channels = channels
            values = tuple(
                row.get(name) if row.get(name) not in (None, "") else None
                for name, _unit in channels
            )
            self._completed_samples.append(
                (self._sample_sequence, elapsed, values)
            )

    def _close_csv(self):
        self._pending_log_path = None
        if self._csv_file:
            self._csv_file.close()
            self._csv_file   = None
            self._csv_writer = None
