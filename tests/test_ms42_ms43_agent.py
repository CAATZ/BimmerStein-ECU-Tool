"""Custom-agent host contract tests using an independent CRC and byte pipe.

No native execution or physical serial timing is implied by these tests. Native
payload execution is covered separately by the C167 emulator tests.
"""
from types import SimpleNamespace

import pytest

import ds2
import ms42_ms43 as protocol
import ms42_ms43_agent as agent
from tests.test_ms42_ms43 import Journal


def crc16(data):
    """Bitwise MODBUS/IBM CRC, independent of the production lookup table."""
    value = 0xFFFF
    for byte in data:
        value ^= byte
        for _ in range(8):
            value = (value >> 1) ^ (0xA001 if value & 1 else 0)
    return value


@pytest.mark.parametrize("family", ("MS42", "MS43"))
def test_upload_verifier_covers_every_body_byte(family):
    from engines.ms42_ms43.payload import SYMBOLS, UPLOAD_READY, UPLOAD_REJECTED, VERIFY_PREFIX_SIZE, payload

    code, symbols = payload(family), SYMBOLS[family]
    assert VERIFY_PREFIX_SIZE == 150 and symbols["body_start"] == 0xE096
    assert (UPLOAD_READY, UPLOAD_REJECTED) == (0xA5, 0x5A)
    assert 150 < len(code) <= 2048 and not len(code) & 1
    for name, expected in (("upload_length", len(code) - 150),
                           ("upload_crc", crc16(code[150:]))):
        operand = symbols[name] - 0xE000 + 2
        assert 2 <= operand <= 148
        assert int.from_bytes(code[operand:operand + 2], "little") == expected
    assert crc16(b"123456789") == 0x4B37


def packet(command, offset, count, data=b"", *, status=None):
    body = bytes((command,)) + offset.to_bytes(3, "big") + count.to_bytes(2, "big")
    if status is not None:
        body += bytes((status,))
    body += bytes(data)
    return body + crc16(body).to_bytes(2, "big")


class BytePipe:
    """An injected half-duplex pipe; it cannot enumerate or open a real port."""
    is_open = True
    native_fast_capable = True
    transport_name = "injected"
    timeout = 1.0
    write_timeout = 1.0
    parity = "N"
    stopbits = 1

    def __init__(self, family):
        self.family = family
        self.baudrate = 166667 if family == "MS42" else 187500
        self.requests = []
        self.pending = b""
        self.fault = None
        self.fragment_size = 13
        self.events = []

    def reset_input_buffer(self):
        self.pending = b""

    def flush(self):
        pass

    def read(self, count):
        count = min(count, self.fragment_size)
        data, self.pending = self.pending[:count], self.pending[count:]
        return data

    def write(self, request):
        request = bytes(request)
        assert crc16(request[:-2]) == int.from_bytes(request[-2:], "big")
        command = request[0]
        offset = int.from_bytes(request[1:4], "big")
        count = int.from_bytes(request[4:6], "big")
        data = request[6:-2]
        assert len(data) == (count if command == ord("P") else 0)
        self.requests.append(request)
        self.events.append(("wire", command, offset, count))
        payload = (self.family.replace("MS", "BS").encode("ascii") if command == ord("I")
                   else bytes((offset + i) & 0xFF for i in range(count))
                   if command == ord("R") else b"")
        reply = packet(command, offset, count, payload, status=0)
        fault = self.fault
        if fault == "crc":
            reply = reply[:-1] + bytes((reply[-1] ^ 1,))
        elif fault == "command":
            reply = packet(command ^ 1, offset, count, payload, status=0)
        elif fault == "offset":
            reply = packet(command, offset + 2, count, payload, status=0)
        elif fault == "count":
            reply = packet(command, offset, count + 2, payload, status=0)
        elif fault == "short":
            reply = reply[:-1]
        elif fault == "missing":
            reply = b""
        elif isinstance(fault, int):
            reply = packet(command, offset, count, status=fault)
        echo = request
        if fault == "echo":
            echo = bytes((request[0] ^ 1,)) + request[1:]
        self.pending = echo + reply
        return len(request) - int(fault == "short_write")


@pytest.fixture(params=("MS42", "MS43"))
def service(request, monkeypatch):
    family = request.param
    pipe = BytePipe(family)
    link = ds2.DS2Interface("FAKE_CUSTOM_AGENT_ONLY", two_stop=False)
    link._ser = pipe  # Deliberately do not call open(), even with a fake factory.
    link.transport_name = "injected"
    link.baud = pipe.baudrate
    link._BITS_PER_CHAR = 10
    monkeypatch.setattr(ds2, "_import_d2xx_serial", lambda: pytest.fail("Real driver access"))
    monkeypatch.setattr(ds2.time, "sleep", lambda _: None)
    ecu = protocol.MS42MS43ECU(link, family)
    return agent.AgentReader(ecu), pipe


def test_independent_crc_known_vector():
    assert crc16(b"123456789") == 0x4B37


@pytest.mark.parametrize("command,offset,count,data", [
    (ord("I"), 0, 4, b""),
    (ord("R"), 0, 2, b""),
    (ord("R"), 0x7FFFE, 2, b""),
    (ord("P"), 0x10000, 1024, bytes(range(256)) * 4),
    (ord("E"), 0x10000, 0, b""),
    (ord("X"), 0, 0, b""),
])
def test_wire_uses_big_endian_header_count_and_crc(service, command, offset, count, data):
    reader, pipe = service
    result = reader._transfer(command, offset, count, data)
    assert pipe.requests == [packet(command, offset, count, data)]
    expected = (pipe.family.replace("MS", "BS").encode("ascii") if command == ord("I")
                else bytes((offset + i) & 0xFF for i in range(count))
                if command == ord("R") else b"")
    assert result == expected
    assert not pipe.pending


@pytest.mark.parametrize("command,offset,count,data", [
    (ord("R"), -2, 2, b""),
    (ord("R"), 1, 2, b""),
    (ord("R"), 0, 1, b""),
    (ord("R"), 0, 0, b""),
    (ord("R"), 0, 1026, b""),
    (ord("R"), 0x7FFFE, 4, b""),
    (ord("R"), 0x80000, 2, b""),
    (ord("R"), 0, 2, b"xx"),
    (ord("P"), 0x10001, 2, b"xx"),
    (ord("P"), 0x10000, 2, b"x"),
    (ord("P"), 0x7FFFE, 4, b"xxxx"),
    (ord("E"), 0x10002, 0, b""),
    (ord("E"), 0x10000, 2, b""),
    (ord("I"), 2, 4, b""),
    (ord("I"), 0, 2, b""),
    (ord("X"), 2, 0, b""),
    (ord("X"), 0, 2, b""),
    (0x75, 0, 0, b""),
])
def test_invalid_request_is_rejected_before_serial_io(service, command, offset, count, data):
    reader, pipe = service
    with pytest.raises((ValueError, ds2.DS2Error)):
        reader._transfer(command, offset, count, data)
    assert pipe.requests == []


@pytest.mark.parametrize("fault", ["crc", "command", "offset", "count", "short", "missing", "echo", "short_write", 0xD1, 0xD2])
@pytest.mark.parametrize("command,count,data", [(ord("R"), 2, b""), (ord("P"), 2, b"\x12\x34")])
def test_corrupt_rejected_or_lost_reply_is_not_replayed(service, fault, command, count, data):
    reader, pipe = service
    pipe.fault = fault
    with pytest.raises(ds2.DS2Error):
        reader._transfer(command, 0x10000, count, data)
    assert len(pipe.requests) == 1


def test_read_chunks_are_1024_bytes_with_exact_tail_and_progress(service):
    reader, pipe = service
    progress = []
    result = reader._read(0x8FFFE, 1026, lambda *entry: progress.append(entry))
    assert result == bytes((0xFFFE + i) & 0xFF for i in range(1026))
    assert pipe.requests == [packet(ord("R"), 0xFFFE, 1024), packet(ord("R"), 0x103FE, 2)]
    assert [entry[:2] for entry in progress] == [(1024, 1026), (1026, 1026)]


@pytest.mark.parametrize("address,length", [(0x7FFFE, 2), (0xFFFFE, 4), (0x80001, 2), (0x80000, 1)])
def test_high_alias_read_does_not_wrap_or_reinterpret_invalid_ranges(service, address, length):
    reader, pipe = service
    with pytest.raises((ValueError, ds2.DS2Error)):
        reader._read(address, length)
    assert pipe.requests == []


def test_cancel_before_request_sends_nothing(service):
    reader, pipe = service
    reader.transport.operation_check = lambda: (_ for _ in ()).throw(RuntimeError("cancelled"))
    with pytest.raises(RuntimeError, match="cancelled"):
        reader._transfer(ord("R"), 0, 2)
    assert pipe.requests == []


def test_program_journals_one_durable_intent_and_only_acknowledged_bytes(service):
    reader, pipe = service
    writer = agent.AgentWriter(reader.ecu)
    journal = Journal()
    state = SimpleNamespace(address=None, destructive_started=False, bytes_written=0)
    writer._program(0x10000, b"\x12\x34", journal, state)
    assert state.destructive_started and state.bytes_written == 2
    assert journal.events == [("program_requested", {"address": 0x10000, "length": 2}),
                              ("program_accepted", {"address": 0x10000, "length": 2})]
    pipe.fault = "missing"
    with pytest.raises(ds2.DS2Error):
        writer._program(0x10002, b"\x56\x78", journal, state)
    assert state.bytes_written == 2
    assert journal.events[-1] == ("program_requested", {"address": 0x10002, "length": 2})
    assert len(pipe.requests) == 2


@pytest.mark.parametrize("operation", ["program", "erase"])
def test_boot_requests_require_explicit_writer_permission(service, operation):
    reader, pipe = service
    writer = agent.AgentWriter(reader.ecu)
    journal = Journal()
    state = SimpleNamespace(address=None, destructive_started=False, bytes_written=0)
    args = (0, b"\x12\x34", journal, state) if operation == "program" else (0, journal, state)
    with pytest.raises((ValueError, ds2.DS2Error)):
        getattr(writer, "_" + operation)(*args)
    assert pipe.requests == [] and not state.destructive_started
    writer.write_boot = True
    getattr(writer, "_" + operation)(*args)
    assert len(pipe.requests) == 1
