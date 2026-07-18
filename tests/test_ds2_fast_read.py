"""Simulation and fault-injection tests for the standalone read-only service."""

from __future__ import annotations

from collections import Counter

import pytest

from ds2_fast_contracts import (
    ContractViolation,
    FrameValidationError,
    LinkRate,
    ResponseStatus,
    SessionState,
    StatusResponseContract,
    decode_ds2_frame,
    encode_ds2_frame,
)
from ds2_fast_plans import (
    FULL_IMAGE_SIZE,
    TUNE_END,
    TUNE_START,
    UNMAPPED_RANGE,
    ds2_image_to_file_layout,
)
from ds2_fast_read import (
    CAPTURED_RATE_PROFILE,
    FastReadError,
    NativeFastReadSession,
    NativeFastReadTransport,
    SELECTOR_COMMAND,
    SELECTOR_HIGH,
    SELECTOR_LOW,
    SELECTOR_MID,
    TOKEN_ADDRESS,
    TOKEN_LENGTH,
    UnsafeReadOnlyCommand,
)


class ReadOnlyStockSerial:
    """Byte-level stock-path model; it implements no write/flash commands."""

    ECU_RATES = {
        SELECTOR_LOW: 9615.4,
        SELECTOR_MID: 19736.8,
        SELECTOR_HIGH: 187500.0,
    }

    def __init__(
        self,
        *,
        token=b"6577205163",
        post_cleanup_busy=0,
        corrupt_selector=None,
        bad_echo_command=None,
        change_identity_after_cleanup=False,
    ):
        assert len(token) == TOKEN_LENGTH
        self.token = bytes(token)
        self.identity = b"SIMULATED-STOCK-MS41-READ-IDENTITY".ljust(42, b" ")[:42]
        self.post_cleanup_busy = int(post_cleanup_busy)
        self.cleanup_busy_remaining = 0
        self.corrupt_selector = corrupt_selector
        self.corrupt_selector_used = False
        self.bad_echo_command = bad_echo_command
        self.bad_echo_used = False
        self.change_identity_after_cleanup = bool(change_identity_after_cleanup)
        self._baud = CAPTURED_RATE_PROFILE.low
        self.timeout = 1.5
        self._open = True
        self._pending = bytearray()
        self.selector = SELECTOR_LOW
        self.ecu_baud = self.ECU_RATES[SELECTOR_LOW]
        self.fast_state = False
        self.writes = []
        self.requests = []
        self.read_hits = Counter()
        self.dtr = None
        self.rts = None
        self.memory = bytearray(
            ((address * 17 + 3) & 0xFF) for address in range(FULL_IMAGE_SIZE)
        )
        self.memory[TOKEN_ADDRESS : TOKEN_ADDRESS + TOKEN_LENGTH] = self.token

    @property
    def baudrate(self):
        return self._baud

    @baudrate.setter
    def baudrate(self, value):
        self._baud = int(value)

    @property
    def is_open(self):
        return self._open

    def close(self):
        self._open = False

    def setDTR(self, value):
        self.dtr = value

    def setRTS(self, value):
        self.rts = value

    def reset_input_buffer(self):
        self._pending.clear()

    def flush(self):
        pass

    def read(self, count):
        data = bytes(self._pending[:count])
        del self._pending[:count]
        return data

    def _rate_matches(self):
        return abs(self._baud - self.ecu_baud) / self.ecu_baud <= 0.04

    def _response(self, status, payload=b"", *, corrupt=False):
        response = bytearray(encode_ds2_frame(status, payload))
        if corrupt:
            response[-1] ^= 0x01
        self._pending.extend(response)

    def write(self, raw):
        raw = bytes(raw)
        self.writes.append(raw)
        frame = decode_ds2_frame(raw)
        args = frame.payload
        self.requests.append((self._baud, frame.command, args))

        echo = bytearray(raw)
        if (
            self.bad_echo_command == frame.command
            and not self.bad_echo_used
        ):
            echo[0] ^= 0x01
            self.bad_echo_used = True
        self._pending.extend(echo)

        if not self._rate_matches():
            return len(raw)

        if frame.command == 0x00:
            if self.cleanup_busy_remaining:
                self.cleanup_busy_remaining -= 1
                self._response(ResponseStatus.READINESS_A2)
            else:
                self._response(ResponseStatus.ACK, self.identity)
        elif frame.command == 0x06:
            assert len(args) == 5
            address = int.from_bytes(args[:4], "big")
            count = args[4]
            data = bytearray(self.memory[address : address + count])
            self.read_hits[address] += 1
            self._response(ResponseStatus.ACK, data)
        elif frame.command == SELECTOR_COMMAND:
            if (
                len(args) == TOKEN_LENGTH + 1
                and args[0] in self.ECU_RATES
                and args[1:] == self.token
            ):
                selector = args[0]
                corrupt = (
                    selector == self.corrupt_selector
                    and not self.corrupt_selector_used
                )
                self.corrupt_selector_used |= corrupt
                self._response(ResponseStatus.ACK, corrupt=corrupt)
                self.selector = selector
                self.ecu_baud = self.ECU_RATES[selector]
                self.fast_state = True
            elif args == b"BMW" and self.fast_state:
                self._response(ResponseStatus.CONTEXT_B0)
                self.fast_state = False
                self.cleanup_busy_remaining = self.post_cleanup_busy
                if self.change_identity_after_cleanup:
                    self.identity = b"UNEXPECTED-POST-CLEANUP-IDENTITY".ljust(
                        42, b"!"
                    )[:42]
            else:
                self._response(ResponseStatus.CONTEXT_A1)
        else:
            raise AssertionError(
                f"read-only model received forbidden command 0x{frame.command:02X}"
            )
        return len(raw)


def _session(serial, *, progress=None):
    transport = NativeFastReadTransport(serial)
    return NativeFastReadSession(
        transport,
        progress_cb=progress,
        post_cleanup_delay=0,
        post_cleanup_poll=0,
        sleeper=lambda _seconds: None,
    )


@pytest.mark.parametrize(
    "command,args",
    (
        (0x07, b""),
        (0x0D, b""),
        (0xA2, b""),
        (0x90, b"BMW\x1E"),
        (0x90, b"\x01short"),
    ),
)
def test_transport_hard_rejects_every_non_read_only_command(command, args):
    serial = ReadOnlyStockSerial()
    transport = NativeFastReadTransport(serial)
    with pytest.raises(UnsafeReadOnlyCommand):
        transport.request(
            command,
            args,
            contract=StatusResponseContract(
                "unused", frozenset((ResponseStatus.ACK,))
            ),
            label="forbidden",
            rate=LinkRate.LOW,
            state=SessionState.LOW_READY,
        )
    assert serial.writes == []


def test_open_d2xx_uses_the_selected_com_port_and_8e2_without_fallback():
    calls = []
    serial = ReadOnlyStockSerial()

    def factory(**kwargs):
        calls.append(kwargs)
        return serial

    transport = NativeFastReadTransport.open_d2xx(
        "COM1",
        serial_factory=factory,
    )
    assert calls == [
        {
            "port": "COM1",
            "baudrate": 9600,
            "timeout": 1.5,
            "write_timeout": 3.0,
            "two_stop": True,
        }
    ]
    assert serial.dtr is False
    assert serial.rts is False
    transport.close()
    assert not serial.is_open


def test_open_d2xx_propagates_factory_failure_without_pyserial_fallback():
    def factory(**_kwargs):
        raise OSError("D2XX adapter unavailable")

    with pytest.raises(OSError, match="D2XX adapter unavailable"):
        NativeFastReadTransport.open_d2xx("COM1", serial_factory=factory)


def test_partial_read_uses_old_rate_acks_and_finishes_at_confirmed_low():
    serial = ReadOnlyStockSerial(post_cleanup_busy=2)
    progress = []
    session = _session(
        serial,
        progress=lambda done, total, label: progress.append((done, total, label)),
    )

    result = session.read_partial()

    assert result.data == bytes(serial.memory[TUNE_START:TUNE_END])
    assert result.final_link is LinkRate.LOW
    assert result.recovery_used is False
    assert session.state is SessionState.COMPLETE
    assert progress[-1][:2] == (TUNE_END - TUNE_START, TUNE_END - TUNE_START)
    assert {command for _baud, command, _args in serial.requests} <= {
        0x00,
        0x06,
        0x90,
    }

    selectors = [
        (baud, args[0])
        for baud, command, args in serial.requests
        if command == SELECTOR_COMMAND and len(args) == TOKEN_LENGTH + 1
    ]
    assert selectors == [
        (9600, SELECTOR_HIGH),
        (187500, SELECTOR_LOW),
    ]
    cleanup = [
        baud
        for baud, command, args in serial.requests
        if command == SELECTOR_COMMAND and args == b"BMW"
    ]
    assert cleanup == [9600]


def test_production_full_read_is_one_pass_with_three_high_rate_probes():
    serial = ReadOnlyStockSerial(post_cleanup_busy=1)
    progress = []
    session = _session(
        serial,
        progress=lambda done, total, label: progress.append((done, total, label)),
    )

    result = session.read_full()

    expected_ds2 = bytearray(serial.memory)
    expected_ds2[UNMAPPED_RANGE.start : UNMAPPED_RANGE.end] = (
        b"\xFF" * UNMAPPED_RANGE.size
    )
    assert result.ds2_image == bytes(expected_ds2)
    assert result.file_image == ds2_image_to_file_layout(expected_ds2)
    assert result.readable_bytes == 240 * 1024
    assert result.final_link is LinkRate.LOW
    assert progress[-1][:2] == (240 * 1024, 240 * 1024)
    assert serial.read_hits[0x20000] == 1
    assert serial.read_hits[TOKEN_ADDRESS] == 3
    assert all(command != 0x07 for _baud, command, _args in serial.requests)


def test_corrupt_high_selector_ack_recovers_by_probing_the_actual_high_rate():
    serial = ReadOnlyStockSerial(corrupt_selector=SELECTOR_HIGH)
    session = _session(serial)

    with pytest.raises(FrameValidationError, match="checksum"):
        session.read_partial()

    assert session.recovery_used is True
    assert session.link is LinkRate.LOW
    assert session.state is SessionState.LOW_READY
    assert serial.selector == SELECTOR_LOW


def test_echo_mismatch_fails_before_any_response_is_treated_as_valid():
    serial = ReadOnlyStockSerial(bad_echo_command=0x00)
    session = _session(serial)

    with pytest.raises(FastReadError, match="echo mismatch"):
        session.read_partial()

    assert session.token is None
    assert session.recovery_used is False


def test_post_cleanup_identity_change_is_never_reported_as_success():
    serial = ReadOnlyStockSerial(change_identity_after_cleanup=True)
    session = _session(serial)

    with pytest.raises(ContractViolation, match="identity differs"):
        session.read_partial()

    assert session.recovery_used is True
    assert session.link is LinkRate.UNKNOWN
    assert session.state is not SessionState.COMPLETE
