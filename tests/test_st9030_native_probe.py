from __future__ import annotations

import hashlib

import pytest

from ds2_fast_contracts import LinkRate, ResponseStatus, decode_ds2_frame, encode_ds2_frame
from ds2_fast_read import SELECTOR_COMMAND, SELECTOR_LOW, SELECTOR_MID, UnsafeReadOnlyCommand
from ds2_write_authorization import (
    AUTHORIZATION_STATE_ADDRESS,
    NATIVE_FAST_REENTRY_LATCH_ADDRESS,
    NATIVE_FAST_REENTRY_TIMER_ADDRESS,
    WRONG_KEY_COUNTER_ADDRESS,
)
from st9030_native_probe import (
    E620_ADDRESS,
    E646_ADDRESS,
    E62E_ADDRESS,
    EXPECTED_TOKEN,
    F732_ADDRESS,
    NativeSt9030ProbeError,
    NativeSt9030ProbeSession,
    NativeSt9030ProbeTransport,
    TARGET_SIZE,
    TELEGRAM_COMMAND,
    TELEGRAM_PAYLOAD,
    _admit_target,
    run_native_st9030_probe,
)
from tests.test_ds2_fast_read import ReadOnlyStockSerial


class ProbeSerial(ReadOnlyStockSerial):
    def __init__(self, *, drop_telegram=False):
        super().__init__(token=EXPECTED_TOKEN)
        self.drop_telegram = bool(drop_telegram)
        self.memory[F732_ADDRESS : F732_ADDRESS + 3] = b"\x55\x26\x55"
        self.memory[E620_ADDRESS : E620_ADDRESS + 0x0E] = bytes(range(0x0E))
        self.memory[E646_ADDRESS : E646_ADDRESS + 0x14] = bytes(range(0x20, 0x34))
        self.memory[E62E_ADDRESS : E62E_ADDRESS + 1] = b"\xA0"

    def write(self, raw):
        frame = decode_ds2_frame(bytes(raw))
        if frame.command != TELEGRAM_COMMAND:
            return super().write(raw)

        args = frame.payload
        self.writes.append(bytes(raw))
        self.requests.append((self._baud, frame.command, args))
        self._pending.extend(bytes(raw))
        if self._rate_matches() and not self.drop_telegram:
            self._pending.extend(
                encode_ds2_frame(ResponseStatus.ACK, b"\x12\x34")
            )
        return len(raw)


def _session(serial):
    transport = NativeSt9030ProbeTransport(serial)
    return NativeSt9030ProbeSession(
        transport,
        post_cleanup_delay=0,
        post_cleanup_poll=0,
        reentry_required=True,
        sleeper=lambda _seconds: None,
    )


def test_exact_one_shot_transcript_and_cleanup():
    serial = ProbeSerial()
    result = _session(serial).execute(port="COM1", target_sha256="admitted")

    assert result.value_hex == "1234"
    assert result.value_u16_be == 0x1234
    assert result.f732_f734_hex == "552655"
    assert result.e62e_followup_hex == "a0"
    assert result.cleanup_confirmed is True
    assert result.final_link == "low"
    assert result.telegram_attempts == 1
    assert result.frames == {
        "selector_0x12": "12 0f 90 12 36 35 37 37 32 30 35 31 36 33 9f",
        "telegram_0x0B": "12 09 0b 02 00 00 00 10 02",
        "read_F732_F734": "12 09 06 00 00 f7 32 03 db",
        "read_E620_E62D": "12 09 06 00 00 e6 20 0e d5",
        "read_E646_E659": "12 09 06 00 00 e6 46 14 a9",
        "read_E62E_post_cleanup": "12 09 06 00 00 e6 2e 01 d4",
        "selector_0x26": "12 0f 90 26 36 35 37 37 32 30 35 31 36 33 ab",
        "bare_BMW": "12 07 90 42 4d 57 dd",
        "identify": "12 04 00 16",
    }

    assert serial.requests == [
        (9600, 0x00, b""),
        (9600, 0x06, b"\x00\x00\x20\x40\x20"),
        (9600, 0x06, b"\x00\x00\x20\x60\x12"),
        (9600, 0x06, AUTHORIZATION_STATE_ADDRESS.to_bytes(4, "big") + b"\x01"),
        (9600, 0x06, WRONG_KEY_COUNTER_ADDRESS.to_bytes(4, "big") + b"\x01"),
        (9600, 0x06, NATIVE_FAST_REENTRY_TIMER_ADDRESS.to_bytes(4, "big") + b"\x02"),
        (9600, 0x06, NATIVE_FAST_REENTRY_LATCH_ADDRESS.to_bytes(4, "big") + b"\x01"),
        (9600, SELECTOR_COMMAND, bytes((SELECTOR_MID,)) + EXPECTED_TOKEN),
        (19737, 0x06, b"\x00\x00\x20\x5E\x0A"),
        (19737, TELEGRAM_COMMAND, TELEGRAM_PAYLOAD),
            (19737, 0x06, b"\x00\x00\xF7\x32\x03"),
            (19737, 0x06, b"\x00\x00\xE6\x20\x0E"),
            (19737, 0x06, b"\x00\x00\xE6\x46\x14"),
                (19737, SELECTOR_COMMAND, bytes((SELECTOR_LOW,)) + EXPECTED_TOKEN),
                (9600, SELECTOR_COMMAND, b"BMW"),
                (9600, 0x00, b""),
                (9600, 0x06, b"\x00\x00\xE6\x2E\x01"),
        ]
    assert sum(command == TELEGRAM_COMMAND for _, command, _ in serial.requests) == 1


def test_wrong_0x0b_payload_is_rejected_before_write():
    serial = ProbeSerial()
    transport = NativeSt9030ProbeTransport(serial)
    with pytest.raises(UnsafeReadOnlyCommand):
        transport.request(
            TELEGRAM_COMMAND,
            b"\x02\x00\x00\x00\x11",
            contract=None,
            label="forbidden",
            rate=LinkRate.MID,
            state=None,
        )
    assert serial.writes == []


def test_timeout_never_retries_0x0b_and_recovers_low():
    serial = ProbeSerial(drop_telegram=True)
    with pytest.raises(Exception, match="no complete response header"):
        _session(serial).execute(port="COM1", target_sha256="admitted")

    assert sum(command == TELEGRAM_COMMAND for _, command, _ in serial.requests) == 1
    assert (19737, SELECTOR_COMMAND, bytes((SELECTOR_LOW,)) + EXPECTED_TOKEN) in serial.requests
    assert serial.requests[-2:] == [
        (9600, SELECTOR_COMMAND, b"BMW"),
        (9600, 0x00, b""),
    ]


def test_target_admission_happens_before_com_open(tmp_path, monkeypatch):
    target = tmp_path / "wrong.bin"
    target.write_bytes(b"\xFF" * TARGET_SIZE)
    opened = False

    def forbidden_open(*_args, **_kwargs):
        nonlocal opened
        opened = True
        raise AssertionError("COM must not open")

    monkeypatch.setattr(NativeSt9030ProbeTransport, "open_d2xx", forbidden_open)
    with pytest.raises(NativeSt9030ProbeError, match="not admitted"):
        run_native_st9030_probe("COM1", target)
    assert opened is False


def test_admission_checks_size_hash_and_token(tmp_path, monkeypatch):
    target = tmp_path / "target.bin"
    image = bytearray(TARGET_SIZE)
    image[0x605E : 0x605E + len(EXPECTED_TOKEN)] = EXPECTED_TOKEN
    target.write_bytes(image)
    monkeypatch.setattr(
        "st9030_native_probe.TARGET_SHA256",
        hashlib.sha256(image).hexdigest(),
    )
    assert _admit_target(target) == hashlib.sha256(image).hexdigest()
