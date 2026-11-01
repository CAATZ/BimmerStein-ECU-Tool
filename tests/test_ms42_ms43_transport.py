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


def test_raw_exchange_validates_echo_and_returns_busy_status(monkeypatch):
    monkeypatch.setattr(ds2.time, "sleep", lambda _: None)
    link = DS2Interface("FAKE_ONLY", serial_factory=Serial, two_stop=False)
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
