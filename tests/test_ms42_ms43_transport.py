"""Framing regressions with injected transports and a fake FTDI driver only."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
import ds2
from ds2 import DS2Interface, DS2Error


class Serial:
    is_open = True
    transport_name = "fake"

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.stopbits = 2 if kwargs.get("two_stop", True) else 1
        self.pending = b""
        self.bad_echo = False
        self.response = bytes.fromhex("12 04 A1 B7")

    def reset_input_buffer(self):
        self.pending = b""

    def write(self, frame):
        echo = bytes(len(frame)) if self.bad_echo else frame
        self.pending = echo + self.response
        return len(frame)

    def read(self, count):
        result, self.pending = self.pending[:count], self.pending[count:]
        return result

    def flush(self):
        pass


@pytest.mark.parametrize("two_stop,bits", [(True, 12), (False, 11)])
def test_injected_open_preserves_default_and_accepts_8e1(two_stop, bits):
    link = DS2Interface("FAKE_ONLY", serial_factory=Serial, two_stop=two_stop)
    link.open()
    assert link._ser.kwargs["two_stop"] is two_stop
    assert link._BITS_PER_CHAR == bits
    handle = link._ser
    link.set_stopbits(not two_stop)
    assert link._ser is handle
    assert link._ser.stopbits == (1 if two_stop else 2)
    assert link._BITS_PER_CHAR == (11 if two_stop else 12)


@pytest.mark.parametrize("lifecycle_only", [False, True])
def test_raw_exchange_validates_echo_and_returns_busy_status(monkeypatch, lifecycle_only):
    monkeypatch.setattr(ds2.time, "sleep", lambda _: None)
    monkeypatch.setattr(ds2, "_echo_mismatch_offset", lambda *_: pytest.fail("Tracing was disabled"))
    link = DS2Interface("FAKE_ONLY", serial_factory=Serial, two_stop=False)
    if lifecycle_only:
        def lifecycle(event, details):
            pytest.fail("Wire event sent to lifecycle listener")
        lifecycle.exchange_trace = "off"
        link.diagnostic_fn = lifecycle
    link.open()
    assert link.execute_frame(7, bytes.fromhex("0F 07 00 00 00")) == bytes.fromhex("12 04 A1 B7")
    link._ser.bad_echo = True
    with pytest.raises(DS2Error, match="echo mismatch"):
        link.execute_frame(0x0D)


@pytest.mark.parametrize("response", [
    bytes.fromhex("13 04 A0 B7"),  # wrong address
    bytes.fromhex("12 03 11"),     # invalid total length
    bytes.fromhex("12 06 A0 B4"),  # truncated body
    bytes.fromhex("12 04 A0 00"),  # bad XOR
])
def test_raw_exchange_rejects_malformed_frame(monkeypatch, response):
    monkeypatch.setattr(ds2.time, "sleep", lambda _: None)
    link = DS2Interface("FAKE_ONLY", serial_factory=Serial, two_stop=False)
    link.open()
    link._ser.response = response
    with pytest.raises(DS2Error):
        link.execute_frame(0x0D)


def _recorded_exchange(monkeypatch, source, fault, enabled, echo=True):
    from types import SimpleNamespace
    from ms42_ms43_resident import ResidentReader

    now, calls, records = [0.0], [], []
    def sleep(delay):
        calls.append(("sleep", delay))
        now[0] += delay
    monkeypatch.setattr(ds2.time, "sleep", sleep)
    monkeypatch.setattr(ds2.time, "monotonic", lambda: now[0])

    class Wire:
        is_open, parity, stopbits = True, "N", 1
        transport_name = "test"
        pending = b""
        def __setattr__(self, name, value):
            if name == "timeout":
                calls.append(("timeout", value))
            object.__setattr__(self, name, value)
        def reset_input_buffer(self):
            calls.append(("purge",))
            self.pending = b""
        def write(self, frame):
            calls.append(("write", bytes(frame)))
            now[0] += 0.002
            echoed = bytes(frame) if echo else b""
            reply = bytes.fromhex("03 01 A0 A2" if source == "resident" else "12 04 A0 B6")
            if source == "send_no_response":
                reply = b""
            if fault == "short_echo":
                echoed, reply = echoed[:2], b""
            elif fault == "wrong_echo":
                echoed = echoed[:1] + bytes((echoed[1] ^ 1,)) + echoed[2:]
            elif fault == "short_response":
                reply = reply[:-1]
            self.pending = echoed + reply
            return len(frame) - int(fault == "short_write")
        def flush(self):
            calls.append(("flush",))
            now[0] += 0.001
        def read(self, count):
            calls.append(("read", count))
            now[0] += 0.003
            result, self.pending = self.pending[:count], self.pending[count:]
            return result

    link = DS2Interface("FAKE_ONLY", echo=echo, two_stop=False)
    link._ser = Wire()
    link._BITS_PER_CHAR = 10
    def callback(event, fields):
        calls.append(("diagnostic", event))
        records.append((event, fields))
        if enabled == "raises":
            raise OSError("synthetic diagnostic sink failure")
    link.diagnostic_fn = callback if enabled else None
    private = b"PRIVATE_KEY_VIN"
    result, failure = None, None
    try:
        if source == "resident":
            result = ResidentReader(SimpleNamespace(transport=link, family="MS43"))._exchange(0x02, private)
        elif source == "send_no_response":
            result = link.send_no_response(0x53, private)
        else:
            result = link.execute_frame(0x90, private)
    except DS2Error as error:
        failure = type(error), str(error)
    return calls, records, result, failure


@pytest.mark.parametrize("source,fault", [
    (source, fault) for source in ("send_frame", "send_no_response", "resident")
    for fault in (None, "short_write", "short_echo", "wrong_echo", "short_response")
    if source != "send_no_response" or fault != "short_response"
])
def test_exchange_diagnostics_are_terminal_payload_free_and_preserve_io(monkeypatch, source, fault):
    import json

    plain_calls, _, plain_result, plain_error = _recorded_exchange(monkeypatch, source, fault, False)
    calls, records, result, error = _recorded_exchange(monkeypatch, source, fault, True)
    assert (result, error) == (plain_result, plain_error)
    assert len(records) == 1
    event, fields = records[0]
    assert event == ("request_failed" if fault else "request_completed")
    assert calls[-1] == ("diagnostic", event)
    assert calls[:-1] == plain_calls
    assert fields["source"] == source
    assert (fields["baud"], fields["parity"], fields["stopbits"]) == (9600, "N", 1)
    assert fields["request_length"] == 19
    assert fields["write_length"] == 19 - int(fault == "short_write")
    assert fields["write_duration_s"] == pytest.approx(0.002 if fault == "short_write" else 0.003)
    assert fields["duration_s"] >= fields["echo_duration_s"]
    assert "PRIVATE_KEY_VIN" not in json.dumps(records)
    assert not {"error", "request", "echo", "response", "payload", "args", "vin", "key"} & fields.keys()
    if fault == "short_write":
        assert fields["phase"] == "write" and fields["echo_length"] == 0
        assert not any(call[0] in ("flush", "read") for call in calls)
    elif fault in ("short_echo", "wrong_echo"):
        assert fields["phase"] == "echo"
        assert fields["echo_complete"] is False
        assert fields["echo_mismatch_offset"] == (2 if fault == "short_echo" else 1)
        assert fields["echo_length"] == (2 if fault == "short_echo" else 19)
    elif fault == "short_response":
        assert fields["phase"] == "response_body"
        assert fields["response_length"] == 3 and fields["declared_response_length"] == 4
        assert fields["response_duration_s"] > 0
    else:
        assert fields["echo_complete"] is True
        assert fields["echo_mismatch_offset"] is None
        assert fields["echo_length"] == 19
        if source != "send_no_response":
            assert fields["status"] == 0xA0


@pytest.mark.parametrize("source", ("send_frame", "send_no_response", "resident"))
def test_exchange_diagnostics_preserve_echo_suppressed_reads(monkeypatch, source):
    plain_calls, _, result, failure = _recorded_exchange(monkeypatch, source, None, False, echo=False)
    calls, records, actual, error = _recorded_exchange(monkeypatch, source, None, True, echo=False)
    assert (actual, error) == (result, failure)
    assert len(records) == 1 and calls[:-1] == plain_calls
    assert records[0][1]["echo_length"] == 0
    assert records[0][1]["echo_complete"] is None
    assert records[0][1]["echo_mismatch_offset"] is None
    if source == "send_no_response":
        assert not any(call[0] == "read" for call in calls)


@pytest.mark.parametrize("source", ("send_frame", "send_no_response", "resident"))
@pytest.mark.parametrize("fault", (None, "wrong_echo"))
def test_diagnostic_sink_failure_cannot_change_wire_outcome(monkeypatch, source, fault):
    plain_calls, _, result, failure = _recorded_exchange(monkeypatch, source, fault, False)
    calls, records, actual, error = _recorded_exchange(monkeypatch, source, fault, "raises")
    assert (actual, error) == (result, failure)
    assert len(records) == 1 and calls[:-1] == plain_calls




def test_d2xx_runtime_stop_count_preserves_data_bits_and_parity(monkeypatch):
    from engines.softbsl import d2xx_serial
    calls = []
    class Driver:
        def FT_SetDataCharacteristics(self, *args):
            calls.append(args)
            return 0
    monkeypatch.setattr(d2xx_serial, "_driver", lambda: Driver())
    adapter = object.__new__(d2xx_serial.D2XXSerial)
    adapter._h = "fake handle"
    adapter._data_bits = 8
    adapter._parity = d2xx_serial._PAR_EVEN
    adapter._stopbits = 2
    adapter.stopbits = 1
    assert calls == [("fake handle", 8, d2xx_serial._STOP_1, d2xx_serial._PAR_EVEN)]
    assert adapter.stopbits == 1
    with pytest.raises(ValueError):
        adapter.stopbits = 1.5


def test_cancel_after_frame_read_cannot_accept_response(monkeypatch):
    monkeypatch.setattr(ds2.time, "sleep", lambda _: None)
    link = DS2Interface("FAKE_ONLY", serial_factory=Serial)
    link.open()
    original = link._ser.read
    cancelled = [False]
    reads = [0]
    def read(count):
        result = original(count)
        reads[0] += 1
        if reads[0] == 3:
            cancelled[0] = True
        return result
    def check():
        if cancelled[0]:
            raise RuntimeError("cancelled")
    link._ser.read = read
    link.operation_check = check
    with pytest.raises(RuntimeError, match="cancelled"):
        link.execute_frame(0x0D)


def test_d2xx_parity_handoff_uses_same_handle_and_preserves_stop_bits(monkeypatch):
    from engines.softbsl import d2xx_serial
    calls = []

    class Driver:
        def FT_SetDataCharacteristics(self, *args):
            calls.append(args)
            return 0

    monkeypatch.setattr(d2xx_serial, "_driver", lambda: Driver())
    adapter = object.__new__(d2xx_serial.D2XXSerial)
    adapter._h = "fake handle"
    adapter._data_bits = 8
    adapter._parity = d2xx_serial._PAR_EVEN
    adapter._stopbits = 1
    adapter.parity = "N"
    assert adapter.parity == "N"
    adapter.parity = "E"
    assert adapter.parity == "E"
    assert calls == [("fake handle", 8, d2xx_serial._STOP_1, value)
                     for value in (d2xx_serial._PAR_NONE, d2xx_serial._PAR_EVEN)]
    with pytest.raises(ValueError):
        adapter.parity = "invalid"
    assert len(calls) == 2
