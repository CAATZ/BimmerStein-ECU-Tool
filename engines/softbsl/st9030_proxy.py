"""Bounded C166 ASC1 reconnaissance proxy for the fitted ST9030."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from dataclasses import asdict, is_dataclass
from pathlib import Path

import softbsl_service
from engines.softbsl import checksum
from engines.softbsl import eeprom_ram
from engines.softbsl import softbsl_host as _sb


TARGET_IMAGE_SIZE = 0x40000
TARGET_IMAGE_SHA256 = (
    "b0b3e9b4b2bb72bb507908c078ce542f42a90999f55c3a1c8e967d314b4e1ae3"
)
AGENT_FILENAME = "st9030_agent.hex"
AGENT_VERSION = 5
AGENT_CAPABILITIES = 0x0F
MAX_AGENT_SIZE = 0x800
MAX_RX_WORDS = 12
MAX_REPLAY_REPLY = 29
GATE_REQUEST_BODY = b"gST90"
GATE_REPLY_SIZE = 40
TELEMETRY_REQUEST_BODY = b"tST0B"
TELEMETRY_MAX_ATTEMPTS = 15
TELEMETRY_REPLY_SIZE = 66
ACK = 0x06

UART_REGISTERS = ("S1CON", "S1BG", "S1TIC", "S1RIC", "S1EIC")
REPLAY_SLOTS = {
    0: (0x0102, 2),
    1: (0x0103, 2),
    2: (0x0105, 4),
    3: (0x0108, 5),
    4: (0x0109, 5),
    5: (0x010A, 12),
    6: (0x010E, 1),
}
REPLAY_STATUS = {
    0x00: "ok",
    0x01: "slot_denied",
    0x02: "request_crc_failed",
    0x03: "asc1_tx_timeout",
    0x04: "asc1_rx_timeout",
    0x05: "asc1_error_interrupt",
}
GATE_STATUS = {
    0x00: "ok",
    0x01: "request_crc_failed",
    0x02: "request_magic_failed",
    0x03: "command_10a_tx_timeout",
    0x04: "command_10a_rx_timeout",
    0x05: "command_10a_error_interrupt",
    0x06: "command_10a_payload_ninth_bit_set",
    0x07: "command_10a_status_not_a0",
    0x08: "command_10c_header_tx_timeout",
    0x09: "command_10c_payload_tx_timeout",
    0x0A: "command_10c_error_interrupt",
    0x0B: "command_10e_tx_timeout",
    0x0C: "command_10e_rx_timeout",
    0x0D: "command_10e_error_interrupt",
    0x0E: "command_10e_ninth_bit_set",
    0x0F: "command_10e_pending_a1",
    0x10: "command_10e_explicit_fail_ff",
    0x11: "command_10e_unexpected_status",
}
TELEMETRY_STATUS = {
    0x00: "ready_a0_observed",
    0x01: "request_crc_failed",
    0x02: "request_magic_failed",
    0x03: "command_10b_header_tx_timeout",
    0x04: "command_10b_payload_tx_timeout",
    0x05: "command_10b_error_interrupt",
    0x06: "poll_10e_tx_timeout",
    0x07: "poll_10e_rx_timeout",
    0x08: "poll_10e_error_interrupt",
    0x09: "poll_10e_high_bits_set",
    0x0A: "poll_10e_explicit_fail_ff",
    0x0B: "poll_10e_unexpected_status",
    0x0C: "fe52_stall_guard",
    0x0D: "fe52_overall_expired",
    0x0E: "poll_attempt_cap",
}


class St9030Error(RuntimeError):
    """The proxy rejected an unsafe input or an invalid agent reply."""


class St9030ResetRequired(St9030Error):
    """The RAM-agent exit could not be confirmed over normal DS2."""


def validate_target_image(image_or_path) -> bytes:
    """Admit only the exact reviewed MS41.3 AMD full-read."""
    try:
        image = (
            Path(image_or_path).expanduser().read_bytes()
            if isinstance(image_or_path, (str, Path))
            else bytes(image_or_path)
        )
    except (OSError, TypeError, ValueError) as exc:
        raise St9030Error(f"could not read target image: {exc}") from exc
    digest = hashlib.sha256(image).hexdigest()
    if len(image) != TARGET_IMAGE_SIZE or digest != TARGET_IMAGE_SHA256:
        raise St9030Error(
            "ST9030 proxy target mismatch: expected "
            f"{TARGET_IMAGE_SIZE} bytes / {TARGET_IMAGE_SHA256}, got "
            f"{len(image)} bytes / {digest}"
        )
    return image


def _validate_agent_payload(payload) -> bytes:
    try:
        payload = bytes(payload)
    except (TypeError, ValueError) as exc:
        raise St9030Error(f"invalid ST9030 agent payload: {exc}") from exc
    if not 0 < len(payload) <= MAX_AGENT_SIZE:
        raise St9030Error(
            f"ST9030 agent {len(payload)} B out of range (expected 1..2048)"
        )
    return payload


def load_st9030_agent() -> bytes:
    """Load the frozen payload only when its manifest record is installed."""
    root = Path(__file__).resolve().parent
    try:
        records = json.loads(
            (root / "agent_manifest.json").read_text(encoding="utf-8")
        )["agents"]
        matches = [
            record for record in records.values()
            if record.get("payload") == AGENT_FILENAME
        ]
        if len(matches) != 1:
            raise KeyError(
                f"expected one manifest record for {AGENT_FILENAME}, found {len(matches)}"
            )
        record = matches[0]
        payload = _validate_agent_payload(_sb.load_agent(str(root / AGENT_FILENAME)))
    except (OSError, KeyError, ValueError, _sb.SoftBSLError) as exc:
        raise St9030Error(f"ST9030 agent is unavailable or unregistered: {exc}") from exc
    if (
        len(payload) != record.get("payload_size")
        or hashlib.sha256(payload).hexdigest() != record.get("payload_sha256")
    ):
        raise St9030Error("ST9030 agent failed its packaged manifest integrity check")
    return payload


def _crc_frame(body: bytes) -> bytes:
    return body + checksum._crc(body, 0xFFFF).to_bytes(2, "big")


class St9030Protocol:
    """Wire owner for ``i/s/r/g/t/q/R``; no arbitrary ASC1 API exists."""

    def __init__(self, softbsl):
        self.sb = softbsl
        self.identity = None

    def _read_frame(self, length: int, label: str, timeout: float = 2.0) -> bytes:
        reply = self.sb.ds2._read_exact(length, timeout)
        if len(reply) != length:
            raise St9030Error(f"short {label} reply ({len(reply)}/{length})")
        body, received = reply[:-2], int.from_bytes(reply[-2:], "big")
        computed = checksum._crc(body, 0xFFFF)
        if received != computed:
            raise St9030Error(
                f"{label} CRC mismatch ({received:04X}!={computed:04X})"
            )
        return body

    def identify(self) -> dict:
        self.sb._tx(ord("i"))
        body = self._read_frame(6, "ST9030-agent identify")
        version, capabilities, slot_count, marker = body
        if (
            version != AGENT_VERSION
            or capabilities != AGENT_CAPABILITIES
            or slot_count != len(REPLAY_SLOTS)
        ):
            raise St9030Error(
                "unsupported ST9030 agent "
                f"v{version}, caps=0x{capabilities:02X}, "
                f"slots={slot_count}"
            )
        if marker not in (0, 1, 3):
            raise St9030Error(f"unsafe ST9030-agent entry marker E740=0x{marker:02X}")
        self.identity = {
            "version": version,
            "capabilities": capabilities,
            "entry_marker": marker,
            "slot_count": slot_count,
        }
        return dict(self.identity)

    def snapshot(self) -> dict:
        self.sb._tx(ord("s"))
        body = self._read_frame(13, "ST9030 ASC1 snapshot")
        status = body[0]
        if status != 0:
            raise St9030Error(f"ST9030 ASC1 snapshot failed with status {status}")
        words = [int.from_bytes(body[index:index + 2], "big")
                 for index in range(1, 11, 2)]
        return {
            "status": status,
            "registers": dict(zip(UART_REGISTERS, words)),
        }

    def replay(self, slot: int) -> dict:
        if isinstance(slot, bool) or slot not in REPLAY_SLOTS:
            raise St9030Error(
                f"ST9030 replay slot denied: {slot!r}; allowed={tuple(REPLAY_SLOTS)}"
            )
        command_word, expected_len = REPLAY_SLOTS[slot]
        self.sb._txs(_crc_frame(bytes((ord("r"), slot))))
        header = self.sb.ds2._read_exact(3, 2.0)
        if len(header) != 3:
            raise St9030Error(f"short ST9030 replay header ({len(header)}/3)")
        actual_len = header[2]
        if actual_len > MAX_RX_WORDS:
            raise St9030Error(
                f"ST9030 replay length {actual_len} exceeds {MAX_RX_WORDS} words"
            )
        tail = self.sb.ds2._read_exact(actual_len * 2 + 2, 2.0)
        reply = header + tail
        if len(reply) > MAX_REPLAY_REPLY:
            raise St9030Error(f"ST9030 replay reply exceeds {MAX_REPLAY_REPLY} bytes")
        body, received = reply[:-2], int.from_bytes(reply[-2:], "big")
        computed = checksum._crc(body, 0xFFFF)
        if received != computed:
            raise St9030Error(
                f"ST9030 replay CRC mismatch ({received:04X}!={computed:04X})"
            )
        status = header[0]
        echoed_slot = header[1]
        if status not in REPLAY_STATUS:
            raise St9030Error(f"unknown ST9030 replay status {status}")
        if echoed_slot != slot:
            raise St9030Error("ST9030 replay reply does not match the requested slot")
        if status == 0 and actual_len != expected_len:
            raise St9030Error(
                f"successful ST9030 replay returned {actual_len}/{expected_len} words"
            )
        if status != 0 and actual_len != 0:
            raise St9030Error(
                f"failed ST9030 replay returned unexpected count {actual_len}"
            )
        words = [int.from_bytes(body[index:index + 2], "big")
                 for index in range(3, len(body), 2)]
        return {
            "status": status,
            "status_name": REPLAY_STATUS[status],
            "slot": slot,
            "command_word": command_word,
            "expected_len": expected_len,
            "actual_len": actual_len,
            "words": words,
        }

    def stock_gate(self) -> dict:
        """Run only the fixed stock-derived 10A/10C/10E token exchange."""
        self.sb._txs(_crc_frame(GATE_REQUEST_BODY))
        body = self._read_frame(
            GATE_REPLY_SIZE, "ST9030 stock-derived gate", timeout=5.0
        )
        status = body[0]
        if status not in GATE_STATUS:
            raise St9030Error(f"unknown ST9030 gate status {status}")
        challenge_words = [
            int.from_bytes(body[index:index + 2], "big")
            for index in range(1, 25, 2)
        ]
        derived_response = body[25:36]
        acknowledgement_word = int.from_bytes(body[36:38], "big")

        if status == 0 or status >= 8:
            if any(word > 0xFF for word in challenge_words):
                raise St9030Error("ST9030 gate transcript has a set 10A ninth bit")
            challenge = bytes(word for word in challenge_words[:11])
            expected = challenge[3:] + challenge[:3]
            if challenge_words[11] != 0xA0 or derived_response != expected:
                raise St9030Error("ST9030 gate transcript violates the fixed rotation")
        if status == 0 and acknowledgement_word != 0xA0:
            raise St9030Error("successful ST9030 gate transcript lacks 10E/A0")
        if status == 0x0F and acknowledgement_word != 0xA1:
            raise St9030Error("pending ST9030 gate transcript lacks 10E/A1")
        if status == 0x10 and acknowledgement_word != 0xFF:
            raise St9030Error("failed ST9030 gate transcript lacks 10E/FF")

        return {
            "status": status,
            "status_name": GATE_STATUS[status],
            "completed": status == 0,
            "pending": status == 0x0F,
            "challenge_words": challenge_words,
            "challenge": [word & 0xFF for word in challenge_words[:11]],
            "challenge_status": challenge_words[11] & 0xFF,
            "derived_response": list(derived_response),
            "response_transmit_complete": status == 0 or status >= 0x0B,
            "acknowledgement_word": acknowledgement_word,
        }

    def stock_telemetry(self) -> dict:
        """Observe bounded stock-derived 10E readiness after one fixed 10B."""
        self.sb._txs(_crc_frame(TELEMETRY_REQUEST_BODY))
        body = self._read_frame(
            TELEMETRY_REPLY_SIZE, "ST9030 stock telemetry", timeout=5.0
        )
        status = body[0]
        if status not in TELEMETRY_STATUS:
            raise St9030Error(f"unknown ST9030 telemetry status {status}")
        attempt_count = body[1]
        if attempt_count > TELEMETRY_MAX_ATTEMPTS:
            raise St9030Error(f"ST9030 telemetry attempt count {attempt_count} exceeds 15")
        terminal_delta_ticks = int.from_bytes(body[2:4], "big")
        words_end = 4 + 2 * TELEMETRY_MAX_ATTEMPTS
        status_words = [
            int.from_bytes(body[index:index + 2], "big")
            for index in range(4, words_end, 2)
        ]
        observation_delta_ticks = [
            int.from_bytes(body[index:index + 2], "big")
            for index in range(words_end, len(body), 2)
        ]

        transport_failure = status in (0x06, 0x07, 0x08)
        received_count = attempt_count - int(transport_failure)
        if received_count < 0:
            raise St9030Error("ST9030 telemetry transport status has no issued poll")
        if status in (0x01, 0x02, 0x03, 0x04, 0x05) and attempt_count:
            raise St9030Error("ST9030 telemetry pre-poll failure has attempts")
        if status not in (0x01, 0x02, 0x03, 0x04, 0x05, 0x0D) and not attempt_count:
            raise St9030Error("ST9030 telemetry terminal status has no attempt")
        if any(status_words[received_count:]) or any(
            observation_delta_ticks[received_count:]
        ):
            raise St9030Error("ST9030 telemetry unused transcript fields are not zero")

        received_words = status_words[:received_count]
        received_times = observation_delta_ticks[:received_count]
        if any(word > 0x1FF for word in received_words):
            raise St9030Error("ST9030 telemetry raw status exceeds nine bits")
        if any(
            later < earlier
            for earlier, later in zip(received_times, received_times[1:])
        ):
            raise St9030Error("ST9030 telemetry FE52 timestamps are not monotonic")
        if any(word != 0xA1 for word in received_words[:-1]):
            raise St9030Error("ST9030 telemetry continued after a non-A1 reply")
        if transport_failure and any(word != 0xA1 for word in received_words):
            raise St9030Error("ST9030 telemetry transport failure followed non-A1")
        if received_times and terminal_delta_ticks < received_times[-1]:
            raise St9030Error("ST9030 telemetry terminal FE52 delta precedes transcript")
        paced_times = received_times[:-1] if status == 0x0C else received_times
        if paced_times and paced_times[0] < 0x19:
            raise St9030Error("ST9030 telemetry first poll was not FE52-paced")
        if any(
            later - earlier < 0x19
            for earlier, later in zip(paced_times, paced_times[1:])
        ):
            raise St9030Error("ST9030 telemetry retries were not FE52-paced")

        last_word = received_words[-1] if received_words else None
        if status == 0 and last_word != 0xA0:
            raise St9030Error("ST9030 ready status lacks terminal 10E/A0")
        if status == 0x09 and (
            last_word is None or not (last_word & 0x100)
        ):
            raise St9030Error("ST9030 high-bit status lacks a ninth-bit reply")
        if status == 0x0A and last_word != 0xFF:
            raise St9030Error("ST9030 explicit-fail status lacks terminal 10E/FF")
        if status == 0x0B and (
            last_word is None
            or last_word > 0xFF
            or last_word in (0xA0, 0xA1, 0xFF)
        ):
            raise St9030Error("ST9030 unexpected-status transcript is inconsistent")
        if status == 0x0E and (
            attempt_count != TELEMETRY_MAX_ATTEMPTS or last_word != 0xA1
        ):
            raise St9030Error("ST9030 attempt-cap transcript is inconsistent")
        if status == 0x0D and terminal_delta_ticks < 0x177:
            raise St9030Error("ST9030 overall-expiry transcript is below its deadline")
        if status == 0x0D and received_times and (
            terminal_delta_ticks != received_times[-1] and last_word != 0xA1
        ):
            raise St9030Error("ST9030 expiry advanced after a non-A1 observation")
        if status in (0x01, 0x02) and terminal_delta_ticks:
            raise St9030Error("ST9030 request failure leaked FE52 timing")
        if status not in (0x0C, 0x0D) and received_times and any(
            timestamp > 0x176 for timestamp in received_times
        ):
            raise St9030Error("ST9030 accepted a reply beyond the overall deadline")
        if status in (0x00, 0x09, 0x0A, 0x0B, 0x0C, 0x0E) and (
            not received_times or terminal_delta_ticks != received_times[-1]
        ):
            raise St9030Error("ST9030 terminal timestamp does not match its reply")

        return {
            "status": status,
            "status_name": TELEMETRY_STATUS[status],
            "ready_observed": status == 0,
            "attempt_count": attempt_count,
            "received_count": received_count,
            "terminal_delta_ticks": terminal_delta_ticks,
            "status_words": received_words,
            "observation_delta_ticks": received_times,
        }

    def _exit_to_normal(self, frame: bytes) -> bool:
        try:
            self.sb._txs(frame)
            self.sb._rx(timeout=1.0)
        except Exception:
            pass
        try:
            self.sb._ser().baudrate = 9600
            self.sb.ds2.baud = 9600
            self.sb._ser().reset_input_buffer()
        except Exception:
            pass
        for _ in range(25):
            try:
                if self.sb.ds2.read_mem(0xE740, 1) in (b"\x00", b"\x03"):
                    return True
            except Exception:
                pass
            time.sleep(0.02)
        return False

    def quit_to_normal(self) -> bool:
        return self._exit_to_normal(b"q\xC3\x3C")

    def recover_to_normal(self) -> bool:
        return self._exit_to_normal(b"R\x9C\x9C")


def _tiers(baud: str) -> tuple[str, ...]:
    if baud not in ("auto", "high", "low"):
        raise ValueError(f"unknown ST9030 agent baud tier {baud!r}")
    return eeprom_ram._agent_tiers(baud)


def _open_agent(port, baud, log, payload):
    payload = _validate_agent_payload(payload)
    admission = eeprom_ram.preflight(port)
    if admission.program_variant != "MS41.3":
        raise St9030Error(
            "the reviewed ST9030 proxy target is MS41.3; live preflight detected "
            f"{admission.program_variant}"
        )
    last_error = None
    for tier in _tiers(baud):
        try:
            interface, softbsl = softbsl_service._open_session(
                port,
                log,
                require_d2xx=tier != "low",
                baud_tier=tier,
                entry_mode="auto",
                agent_payload=payload,
            )
        except (Exception, KeyboardInterrupt) as error:
            last_error = error
            if isinstance(error, KeyboardInterrupt) or tier == "low":
                raise
            log(
                f"ST9030 agent '{tier}' entry failed before any proxy probe "
                f"({error}); retrying the complete entry at 9600 baud."
            )
            continue

        protocol = St9030Protocol(softbsl)
        try:
            protocol.identity = protocol.identify()  # first proxy probe: no fallback after this
        except (Exception, KeyboardInterrupt) as error:
            returned = False
            try:
                returned = protocol.quit_to_normal()
            except Exception:
                returned = False
            finally:
                try:
                    interface.close()
                except Exception:
                    pass
                if not returned:
                    raise St9030ResetRequired(
                        "ST9030 agent probe failed and normal DS2 reset was not confirmed"
                    ) from error
            raise
        protocol.baud_tier = tier
        protocol.agent_sha256 = hashlib.sha256(payload).hexdigest()
        return admission, interface, protocol
    raise St9030Error(f"ST9030 agent entry failed: {last_error}")


def _admission_dict(admission) -> dict:
    if is_dataclass(admission):
        return asdict(admission)
    return dict(vars(admission))


def reconnaissance(
    port: str,
    target_image,
    *,
    slots=(),
    stock_gate=False,
    stock_telemetry=False,
    baud="auto",
    log=print,
) -> dict:
    """Snapshot ASC1, then run either fixed receive slots or the fixed gate."""
    image = validate_target_image(target_image)  # fail before payload or serial access
    requested_slots = tuple(slots)
    for slot in requested_slots:
        if isinstance(slot, bool) or slot not in REPLAY_SLOTS:
            raise St9030Error(
                f"ST9030 replay slot denied: {slot!r}; allowed={tuple(REPLAY_SLOTS)}"
            )
    active_operations = bool(requested_slots) + bool(stock_gate) + bool(stock_telemetry)
    if active_operations > 1:
        raise St9030Error(
            "fixed ST9030 replay, gate, and telemetry operations cannot be combined"
        )
    payload = load_st9030_agent()
    admission, interface, protocol = _open_agent(port, baud, log, payload)
    result = None
    operation_error = None
    try:
        snapshot = protocol.snapshot()
        replies = [protocol.replay(slot) for slot in requested_slots]
        gate = protocol.stock_gate() if stock_gate else None
        telemetry = protocol.stock_telemetry() if stock_telemetry else None
        result = {
            "target_sha256": hashlib.sha256(image).hexdigest(),
            "agent_sha256": protocol.agent_sha256,
            "baud_tier": protocol.baud_tier,
            "admission": _admission_dict(admission),
            "identity": protocol.identity,
            "snapshot": snapshot,
            "replies": replies,
            "stock_gate": gate,
            "stock_telemetry": telemetry,
        }
    except (Exception, KeyboardInterrupt) as error:
        operation_error = error
    returned = False
    close_error = None
    try:
        returned = protocol.quit_to_normal()
    finally:
        try:
            interface.close()
        except Exception as error:
            close_error = error
        if not returned:
            raise St9030ResetRequired(
                "ST9030 reconnaissance ended, but normal DS2 reset was not confirmed"
            ) from (operation_error or close_error)
    if operation_error is not None:
        raise operation_error
    if close_error is not None:
        raise St9030Error("ST9030 serial interface close failed") from close_error
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Bounded MS41.3 C166-to-ST9030 ASC1 reconnaissance proxy"
    )
    parser.add_argument("port")
    parser.add_argument("target_image")
    parser.add_argument(
        "--baud", choices=("auto", "high", "low"), default="auto"
    )
    active = parser.add_mutually_exclusive_group()
    active.add_argument(
        "--replay",
        action="append",
        type=int,
        choices=tuple(REPLAY_SLOTS),
        default=[],
        metavar="SLOT",
        help="replay a fixed known receive slot (0..6); omitted means snapshot only",
    )
    active.add_argument(
        "--stock-gate",
        action="store_true",
        help="run the fixed CRC+magic-protected 10A/10C/10E token exchange once",
    )
    active.add_argument(
        "--stock-telemetry",
        action="store_true",
        help="run one fixed 10B then bounded FE52-paced 10E readiness polls",
    )
    args = parser.parse_args(argv)
    print(json.dumps(
        reconnaissance(
            args.port,
            args.target_image,
            slots=args.replay,
            stock_gate=args.stock_gate,
            stock_telemetry=args.stock_telemetry,
            baud=args.baud,
            log=lambda message, *_args: print(message, file=sys.stderr),
        ),
        indent=2,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
