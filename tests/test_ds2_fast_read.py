"""Simulation and fault-injection tests for the standalone read-only service."""

from __future__ import annotations

from collections import Counter
import json
import os

import pytest

import ds2_fast_safety as safety
import ds2_native_fast_reentry as native_reentry
from ds2_fast_contracts import (
    ContractViolation,
    FastOperation,
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
    FastReadTimeout,
    NativeFastReadReentryNotReady,
    NativeFastReadSession,
    NativeFastReadTransport,
    SELECTOR_COMMAND,
    SELECTOR_HIGH,
    SELECTOR_LOW,
    SELECTOR_MID,
    TOKEN_ADDRESS,
    TOKEN_LENGTH,
    UnsafeReadOnlyCommand,
    read_full_d2xx,
    read_partial_d2xx,
)
from ds2_write_authorization import (
    AUTHORIZATION_STATE_ADDRESS,
    NATIVE_FAST_REENTRY_LATCH_ADDRESS,
    NATIVE_FAST_REENTRY_TIMER_ADDRESS,
    WRONG_KEY_COUNTER_ADDRESS,
)


@pytest.fixture(autouse=True)
def _clean_native_reentry_registry(monkeypatch, tmp_path):
    monkeypatch.setattr(safety, "NATIVE_JOURNAL_DIR", tmp_path / "journals")
    native_reentry._reset_for_tests()
    yield
    native_reentry._reset_for_tests()


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
        drop_high_token_response=False,
        change_identity_after_cleanup=False,
        reentry_states=None,
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
        self.drop_high_token_response = bool(drop_high_token_response)
        self.change_identity_after_cleanup = bool(change_identity_after_cleanup)
        self.reentry_states = list(
            reentry_states
            or [
                {
                    "e658": 0,
                    "e659": 0xCC,
                    "e74b": 0,
                    "e72e": 0,
                }
            ]
        )
        self.reentry_index = 0
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

    def _reentry_state(self):
        return self.reentry_states[min(self.reentry_index, len(self.reentry_states) - 1)]

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

    def queue_status(self):
        return len(self._pending), 0, 0

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
            if (
                self.drop_high_token_response
                and self._baud == int(self.ECU_RATES[SELECTOR_HIGH])
                and address == TOKEN_ADDRESS
            ):
                return len(raw)
            marker = self._reentry_state()
            if address == AUTHORIZATION_STATE_ADDRESS and count == 1:
                data = bytearray((marker["e658"],))
            elif address == WRONG_KEY_COUNTER_ADDRESS and count == 1:
                data = bytearray((marker["e74b"],))
            elif address == NATIVE_FAST_REENTRY_TIMER_ADDRESS and count == 2:
                data = bytearray(int(marker["e72e"]).to_bytes(2, "little"))
            elif address == NATIVE_FAST_REENTRY_LATCH_ADDRESS and count == 1:
                data = bytearray((marker["e659"],))
                self.reentry_index += 1
            else:
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


def _session(
    serial,
    *,
    progress=None,
    event_cb=None,
    sleeper=None,
    monotonic=None,
    **session_kwargs,
):
    transport = NativeFastReadTransport(serial, event_cb=event_cb)
    timing_kwargs = {}
    if monotonic is not None:
        timing_kwargs["monotonic"] = monotonic
    return NativeFastReadSession(
        transport,
        progress_cb=progress,
        event_cb=event_cb,
        post_cleanup_delay=0,
        post_cleanup_poll=0,
        sleeper=sleeper or (lambda _seconds: None),
        **timing_kwargs,
        **session_kwargs,
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


def test_partial_read_restores_low_and_confirms_identity_after_b0():
    serial = ReadOnlyStockSerial(post_cleanup_busy=2)
    progress = []
    sleeps = []
    session = _session(
        serial,
        progress=lambda done, total, label: progress.append((done, total, label)),
        sleeper=sleeps.append,
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
    cleanup_index = serial.requests.index((9600, SELECTOR_COMMAND, b"BMW"))
    assert serial.requests[cleanup_index + 1 :] == [
        (9600, 0x00, b""),
        (9600, 0x00, b""),
        (9600, 0x00, b""),
    ]
    assert sleeps[-1] == pytest.approx(0)


def test_partial_read_records_empty_queues_after_captured_b0_cleanup():
    serial = ReadOnlyStockSerial()
    events = []
    session = _session(
        serial,
        event_cb=lambda event, fields: events.append((event, dict(fields))),
    )

    session.read_partial()

    queue_event = next(
        fields for event, fields in events
        if event == "d2xx_queue_status"
        and fields["phase"] == "post_read_cleanup_b0"
    )
    assert queue_event == {
        "phase": "post_read_cleanup_b0",
        "available": True,
        "rx_bytes": 0,
        "tx_bytes": 0,
        "event_count": 0,
    }


def test_standalone_read_arms_same_port_and_next_read_consumes_latch(monkeypatch):
    first_serial = ReadOnlyStockSerial()
    second_serial = ReadOnlyStockSerial()
    serials = [first_serial, second_serial]

    def open_transport(_port, *, echo=True, event_cb=None):
        del echo
        return NativeFastReadTransport(serials.pop(0), event_cb=event_cb)

    monkeypatch.setattr(
        NativeFastReadTransport,
        "open_d2xx",
        staticmethod(open_transport),
    )

    read_partial_d2xx("COM1")
    assert native_reentry.reentry_required("com1")
    assert first_serial.read_hits[NATIVE_FAST_REENTRY_TIMER_ADDRESS] == 0
    assert first_serial.read_hits[NATIVE_FAST_REENTRY_LATCH_ADDRESS] == 0

    read_partial_d2xx("com1")
    assert second_serial.read_hits[NATIVE_FAST_REENTRY_TIMER_ADDRESS] == 1
    assert second_serial.read_hits[NATIVE_FAST_REENTRY_LATCH_ADDRESS] == 1
    assert native_reentry.reentry_required("COM1")


@pytest.mark.parametrize(
    ("reader", "operation", "result_field", "expected_size"),
    [
        (
            read_partial_d2xx,
            FastOperation.PARTIAL_READ,
            "data",
            TUNE_END - TUNE_START,
        ),
        (read_full_d2xx, FastOperation.FULL_READ, "file_image", FULL_IMAGE_SIZE),
    ],
)
def test_standalone_reads_mirror_events_and_seal_success_journal(
    monkeypatch,
    reader,
    operation,
    result_field,
    expected_size,
):
    serial = ReadOnlyStockSerial()
    monkeypatch.setattr(
        NativeFastReadTransport,
        "open_d2xx",
        staticmethod(
            lambda _port, *, echo=True, event_cb=None: NativeFastReadTransport(
                serial, echo=echo, event_cb=event_cb
            )
        ),
    )
    observed = []

    result = reader(
        "COM1",
        event_cb=lambda event, fields: observed.append((event, dict(fields))),
    )

    assert len(getattr(result, result_field)) == expected_size
    path, = safety.NATIVE_JOURNAL_DIR.glob("*.jsonl")
    inspection = safety.inspect_operation_journal(path)
    assert inspection.operation == operation.value
    assert inspection.complete
    assert inspection.outcome == "success"
    records = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
    ]
    assert [event for event, _fields in observed] == [
        record["event"] for record in records[1:-1]
    ]
    assert records[-1]["fields"] == {
        "destructive_started": False,
        "outcome": "success",
    }


def test_failed_read_journal_supersedes_stale_write_journal(monkeypatch):
    stale = safety.new_operation_journal("COM1", FastOperation.FULL_WRITE)
    stale.finish("success", destructive_started=True)
    os.utime(stale.path, (1, 1))
    serial = ReadOnlyStockSerial(drop_high_token_response=True)
    monkeypatch.setattr(
        NativeFastReadTransport,
        "open_d2xx",
        staticmethod(
            lambda _port, *, echo=True, event_cb=None: NativeFastReadTransport(
                serial, echo=echo, event_cb=event_cb
            )
        ),
    )

    with pytest.raises(
        FastReadTimeout,
        match="high_token_liveness at 187500 baud",
    ):
        read_partial_d2xx("COM1")

    newest = max(
        safety.NATIVE_JOURNAL_DIR.glob("*.jsonl"),
        key=lambda path: path.stat().st_mtime_ns,
    )
    inspection = safety.inspect_operation_journal(newest)
    assert newest != stale.path
    assert inspection.operation == FastOperation.PARTIAL_READ.value
    assert inspection.complete
    assert inspection.outcome == "failed"
    terminal = json.loads(
        newest.read_text(encoding="utf-8").splitlines()[-1]
    )
    assert terminal["fields"]["destructive_started"] is False
    assert terminal["fields"]["phase"] == FastOperation.PARTIAL_READ.value
    assert "high_token_liveness at 187500 baud" in terminal["fields"]["error"]


def test_selector_ack_arms_reentry_when_high_liveness_recovery_stays_unknown(
    monkeypatch,
):
    serial = ReadOnlyStockSerial(drop_high_token_response=True)

    monkeypatch.setattr(
        NativeFastReadTransport,
        "open_d2xx",
        staticmethod(
            lambda _port, *, echo=True, event_cb=None: NativeFastReadTransport(
                serial, echo=echo, event_cb=event_cb
            )
        ),
    )

    with pytest.raises(
        FastReadTimeout,
        match="high_token_liveness at 187500 baud",
    ):
        read_partial_d2xx("COM1")

    assert serial.selector == SELECTOR_HIGH
    assert native_reentry.reentry_required("COM1")
    assert not serial.is_open


def test_recovered_selector_attempt_still_arms_reentry_without_valid_ack(
    monkeypatch,
):
    serial = ReadOnlyStockSerial(corrupt_selector=SELECTOR_HIGH)

    monkeypatch.setattr(
        NativeFastReadTransport,
        "open_d2xx",
        staticmethod(
            lambda _port, *, echo=True, event_cb=None: NativeFastReadTransport(
                serial, echo=echo, event_cb=event_cb
            )
        ),
    )

    with pytest.raises(FrameValidationError, match="checksum"):
        read_partial_d2xx("COM1")

    assert serial.selector == SELECTOR_LOW
    assert native_reentry.reentry_required("COM1")


def test_pending_read_reentry_waits_for_shared_latch_before_selector():
    serial = ReadOnlyStockSerial(
        reentry_states=[
            {"e658": 0, "e659": 0, "e74b": 0, "e72e": 3},
            {"e658": 0, "e659": 0, "e74b": 0, "e72e": 2},
            {
                "e658": 0,
                "e659": 0xCC,
                "e74b": 0,
                "e72e": 0,
            },
        ]
    )
    progress = []
    events = []
    sleeps = []
    session = _session(
        serial,
        progress=lambda done, total, label: progress.append((done, total, label)),
        event_cb=lambda event, fields: events.append((event, dict(fields))),
        sleeper=sleeps.append,
        reentry_required=True,
    )

    session.read_partial()

    marker_events = [
        fields
        for event, fields in events
        if event == "native_fast_read_reentry_marker"
    ]
    assert [(item["e72e"], item["e659"]) for item in marker_events] == [
        (3, 0),
        (2, 0),
        (0, 0xCC),
    ]
    assert any(
        event == "native_fast_read_reentry_ready" for event, _fields in events
    )
    assert [item[2] for item in progress if item[:2] == (0, 0)] == [
        "Waiting for ECU native-fast readiness (E72E=3, E659=0x00)",
        "Waiting for ECU native-fast readiness (E72E=2, E659=0x00)",
    ]
    first_selector = next(
        index
        for index, (_baud, command, _args) in enumerate(serial.requests)
        if command == SELECTOR_COMMAND
    )
    last_gate_read = max(
        index
        for index, (_baud, command, args) in enumerate(serial.requests)
        if command == 0x06
        and int.from_bytes(args[:4], "big")
        in (NATIVE_FAST_REENTRY_TIMER_ADDRESS, NATIVE_FAST_REENTRY_LATCH_ADDRESS)
    )
    assert last_gate_read < first_selector
    assert sum(1 for value in sleeps if value == pytest.approx(1.0)) == 2


def test_ready_shared_latch_enters_fast_mode_without_artificial_delay():
    serial = ReadOnlyStockSerial(
        reentry_states=[{"e658": 0, "e659": 0xCC, "e74b": 0, "e72e": 0}]
    )
    session = _session(serial, reentry_required=True)

    result = session.read_partial()

    assert len(result.data) == TUNE_END - TUNE_START
    assert session.fast_selector_attempted is True
    assert session.link is LinkRate.LOW
    assert session.state is SessionState.COMPLETE
    assert any(command == SELECTOR_COMMAND for _baud, command, _args in serial.requests)
    assert serial.baudrate == CAPTURED_RATE_PROFILE.low


def test_reentry_latch_timeout_fails_before_selector_without_recovery_probe():
    serial = ReadOnlyStockSerial(
        reentry_states=[{"e658": 0, "e659": 0, "e74b": 0, "e72e": 5}]
    )
    clock = [0.0]

    def sleep(seconds):
        clock[0] += seconds

    session = _session(
        serial,
        sleeper=sleep,
        monotonic=lambda: clock[0],
        reentry_timeout=2.0,
        reentry_poll=1.0,
        reentry_required=True,
    )

    with pytest.raises(NativeFastReadReentryNotReady, match="within 2 seconds"):
        session.read_full()

    assert clock[0] == pytest.approx(2.0)
    assert session.fast_selector_attempted is False
    assert session.link is LinkRate.LOW
    assert session.state is SessionState.LOW_READY
    assert all(command != SELECTOR_COMMAND for _baud, command, _args in serial.requests)


@pytest.mark.parametrize(
    ("marker", "message"),
    (
        ({"e658": 2, "e659": 0, "e74b": 0, "e72e": 0}, "E658=2"),
        ({"e658": 0, "e659": 0, "e74b": 2, "e72e": 0}, "E74B >= 2"),
    ),
)
def test_reentry_safety_marker_blocks_before_selector(marker, message):
    serial = ReadOnlyStockSerial(reentry_states=[marker])
    session = _session(serial, reentry_required=True)

    with pytest.raises(NativeFastReadReentryNotReady, match=message):
        session.read_partial()

    assert session.fast_selector_attempted is False
    assert session.link is LinkRate.LOW
    assert session.state is SessionState.LOW_READY
    assert all(command != SELECTOR_COMMAND for _baud, command, _args in serial.requests)


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
    cleanup_index = serial.requests.index((9600, SELECTOR_COMMAND, b"BMW"))
    assert serial.requests[cleanup_index + 1 :] == [(9600, 0x00, b""), (9600, 0x00, b"")]


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

    assert session.state is not SessionState.COMPLETE


def test_failed_read_recovery_still_rejects_changed_identity():
    serial = ReadOnlyStockSerial(
        corrupt_selector=SELECTOR_HIGH,
        change_identity_after_cleanup=True,
    )
    session = _session(serial)

    with pytest.raises(FrameValidationError, match="checksum"):
        session.read_partial()

    assert session.recovery_used is True
    assert session.link is LinkRate.UNKNOWN
    assert session.state is not SessionState.COMPLETE
