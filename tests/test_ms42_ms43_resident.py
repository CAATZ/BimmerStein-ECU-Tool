"""Resident framing and interruption checks, with an injected port only."""
from types import SimpleNamespace

import pytest

from ds2 import DS2Error, DS2Interface, _xor
from ms42_ms43_resident import ResidentReader


class Serial:
    is_open = True

    def __init__(self, **kwargs):
        self.pending = b""
        self.requests = []
        self.failure = None
        self.parity = "E"
        self.baudrate = kwargs["baudrate"]

    def reset_input_buffer(self):
        self.pending = b""

    def write(self, message):
        self.requests.append(message)
        assert len(message) == message[0] + 1 and _xor(message) == 0
        assert message[1] == 1
        if self.failure == "short_write":
            return len(message) - 1
        data = b""
        if message[2] == 0x71:
            assert len(message) == 9
            address = int.from_bytes(message[3:6], "big")
            words = int.from_bytes(message[6:8], "big")
            assert address % 2 == 0 and 1 <= words <= 126
            data = bytes((address + offset) % 256
                         for word in range(words) for offset in (word * 2 + 1, word * 2))
        reply = bytes((len(data) + 3, 1, 0xA0)) + data
        reply += bytes((_xor(reply),))
        if self.failure == "xor":
            reply = reply[:-1] + bytes((reply[-1] ^ 1,))
        elif self.failure == "short_reply":
            reply = reply[:-1]
        elif self.failure == "negative":
            reply = bytes.fromhex("03 01 B0 B2")
        elif self.failure == "bad_length":
            reply = b"\x02\x01\x03"
        elif self.failure == "timeout":
            reply = b""
        self.pending = (bytes(len(message)) if self.failure == "echo" else message) + reply
        return len(message)

    def read(self, count):
        result, self.pending = self.pending[:count], self.pending[count:]
        return result

    def flush(self):
        pass


@pytest.fixture
def reader(monkeypatch):
    monkeypatch.setattr("ds2.time.sleep", lambda _: None)
    link = DS2Interface("FAKE_ONLY", serial_factory=Serial, two_stop=False)
    link.open()
    return ResidentReader(SimpleNamespace(transport=link, family="MS43"))


def test_word_order_chunking_and_segment_crossing(reader):
    events = []
    assert reader._read(0x8FFF0, 508, lambda *args: events.append(args)) == bytes(
        (0xF0 + offset) % 256 for offset in range(508))
    assert [int.from_bytes(frame[3:6], "big") for frame in reader.transport._ser.requests] == [
        0x8FFF0, 0x900EC, 0x901E8]
    assert [event[0] for event in events] == [252, 504, 508]


@pytest.mark.parametrize("failure", ["short_write", "xor", "short_reply", "negative",
                                     "bad_length", "timeout", "echo"])
def test_fault_stops_read_without_replay_or_any_flash_request(reader, failure):
    reader.transport._ser.failure = failure
    with pytest.raises(DS2Error):
        reader._read(0x80000, 512)
    assert len(reader.transport._ser.requests) == 1
    assert reader.transport._ser.requests[0][2] == 0x71


@pytest.mark.parametrize("address,length", [(-2, 2), (1, 2), (0, 3), (0, -2), (0xFFFFFE, 4)])
def test_invalid_range_is_rejected_before_io(reader, address, length):
    with pytest.raises(ValueError):
        reader._read(address, length)
    assert not reader.transport._ser.requests


def test_flash_commands_are_not_exposed(reader):
    for command in (0x70, 0x73, 0x74, 0x75, 0x76, 0x7A):
        with pytest.raises(ValueError):
            reader._exchange(command)
    assert not reader.transport._ser.requests


def test_cancellation_after_one_chunk_does_not_send_another(reader):
    cancelled = False

    def check():
        if cancelled:
            raise RuntimeError("cancelled")

    def progress(*_):
        nonlocal cancelled
        cancelled = True

    reader.transport.operation_check = check
    with pytest.raises(RuntimeError, match="cancelled"):
        reader._read(0x80000, 512, progress)
    assert len(reader.transport._ser.requests) == 1
