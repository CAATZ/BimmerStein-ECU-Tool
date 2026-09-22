"""Wire faults at the production owners; firmware execution is a separate probe."""
from types import SimpleNamespace

import pytest

import ds2
from ds2_fast_contracts import (
    CommitUnknownError, ProgramReadbackMismatch, FlashRequest, LinkRate, SessionState,
    program_readback_window, decode_ds2_frame, encode_ds2_frame,
)
from ds2_fast_partial_write import (
    NativeFastPartialWriteSession, NativeFastPartialWriteTransport, PartialWriteTiming,
)
from ds2_fast_full_write import NativeFastFullWriteTransport, FullWriteTiming
from ds2_fast_slim_write import SlimNativeFastFullWriteSession


class CalibrationPeer:
    """A byte-stream peer, with faults around echo and response boundaries."""
    native_fast_capable = True
    transport_name, port, index = "d2xx", "COM_TEST", 0

    def __init__(self, address, *, fault="lost", partial=False, fragment=4096, full=False):
        self.address, self.fault, self.partial, self.fragment = address, fault, partial, fragment
        self.memory = bytearray(b"\xff" * 0x40000)
        self.baudrate, self.timeout, self.is_open = 9600, 1.5, True
        self.pending = bytearray()
        self.late = None
        self.requests = []
        self.full = full

    def reset_input_buffer(self):
        self.pending.clear()

    def flush(self):
        pass

    def close(self):
        self.is_open = False

    def read(self, count):
        count = min(count, self.fragment)
        data = bytes(self.pending[:count])
        del self.pending[:count]
        return data

    def program(self, operation, address, data):
        if operation == 6:
            ranges = ((0x2000, 0x8000), (0x20000, 0x40000)) if address == 0x2000 else ((0x10000, 0x20000),)
            for start, end in ranges:
                self.memory[start:end] = b"\xff" * (end - start)
        else:
            written = data[:min(2, len(data) - 1)] if self.partial and address == self.address else data
            self.memory[address:address + len(written)] = written
        return encode_ds2_frame(0xA0, bytes((operation,))
                                + (address + len(data)).to_bytes(3, "big")
                                + bytes((len(data), 1)))

    def read_memory(self, address, count):
        return bytes(self.memory[address:address + count])

    def write(self, raw):
        frame = decode_ds2_frame(raw)
        self.requests.append(frame)
        self.pending.extend(raw)
        if frame.command == 7:
            op, address, data = frame.payload[0], int.from_bytes(frame.payload[1:4], "big"), frame.payload[5:]
            reply = self.program(op, address, data)
            # Native partial erase replies with operation zero.
            if op == 6 and self.baudrate == 187500 and not self.full:
                reply = encode_ds2_frame(0xA0, b"\x00" + address.to_bytes(3, "big") + b"\x00\x01")
            if address == self.address and op in (0, 2):
                self.late = reply
                if self.fault == "partial_header":
                    self.pending.extend(reply[:1])
                return len(raw)
            self.pending.extend(reply)
        elif frame.command == 6:
            address, count = int.from_bytes(frame.payload[:4], "big"), frame.payload[4]
            if self.fault == "echo":
                self.pending[0] ^= 1
            if self.late is not None and self.fault in ("late", "wrong_cursor", "bad_late_crc", "double_late", "before_echo"):
                late = bytearray(self.late)
                if self.fault == "wrong_cursor":
                    late[6] ^= 1
                    late[-1] ^= 1
                if self.fault == "bad_late_crc":
                    late[-1] ^= 1
                if self.fault == "before_echo":
                    self.pending[:0] = late
                else:
                    self.pending.extend(late)
                if self.fault == "double_late":
                    self.pending.extend(late)
            if self.fault == "read_timeout":
                return len(raw)
            payload = self.read_memory(address, count)
            if self.fault == "six_byte_impostor":
                payload = self.late[3:-1]
            if self.fault == "short_read":
                payload = payload[:-1]
            reply = bytearray(encode_ds2_frame(0xA2 if self.fault == "read_nak" else 0xA0, payload))
            if self.fault == "read_crc":
                reply[-1] ^= 1
            self.pending.extend(reply)
            self.late = None
        else:
            self.pending.extend(self.control_response(frame))
        return len(raw)

    def control_response(self, frame):
        raise AssertionError(frame)


def native_session(peer, request):
    transport = NativeFastPartialWriteTransport(peer)
    transport.set_baud(187500, reason="offline fault test")
    session = NativeFastPartialWriteSession()
    session.transport, session.link, session.state = transport, LinkRate.HIGH, SessionState.HIGH_PARTIAL_WRITE
    session.timing = PartialWriteTiming()
    session._sleep = lambda _seconds: None
    session._record = lambda *_args, **_kwargs: None
    session._progress = lambda *_args: None
    next_address = 0x10000 if request.address != 0x10000 else request.address + request.count
    session.plan = SimpleNamespace(erase=FlashRequest(6, 0x10000), program=(
        request, FlashRequest(0, next_address, b"\xa5" * 11),
    ))
    return session


def full_session(peer):
    session = SlimNativeFastFullWriteSession.__new__(SlimNativeFastFullWriteSession)
    session.transport = NativeFastFullWriteTransport(peer)
    session.transport.set_baud(187500, reason="offline full-write fault test")
    session.link, session.state = LinkRate.HIGH, SessionState.HIGH_FULL_PROGRAM
    session.timing = FullWriteTiming()
    session._sleep = lambda _seconds: None
    session._record = lambda *_args, **_kwargs: None
    session._progress = lambda *_args: None
    session.variant_conversion = False
    return session


@pytest.mark.parametrize("fast", (False, True))
@pytest.mark.parametrize("fault,partial", (("lost", False), ("late", False), ("lost", True)))
@pytest.mark.parametrize("address,count", ((0x2000, 128), (0x20F3, 243), (0x3FFA, 6),
                                         (0x5FFF, 1), (0x200F3, 243), (0x3FFFF, 1), (0x13FFA, 6)))
def test_full_blocks_recover_without_resend(monkeypatch, fast, fault, partial, address, count):
    monkeypatch.setattr(ds2.time, "sleep", lambda _seconds: None)
    # A one-byte write cannot be partly committed; model it as not committed.
    payload = bytes((i * 17 + 3) % 254 for i in range(count))
    peer = CalibrationPeer(address, fault=fault, partial=partial, fragment=1, full=True)
    if fast:
        session = full_session(peer)
        session.state = SessionState.HIGH_FULL_TUNE if 0x10000 <= address < 0x16000 else SessionState.HIGH_FULL_PROGRAM
        execute = lambda: session._flash_full(FlashRequest(2, address, payload), "full_fault")
    else:
        client = ds2.DS2Interface(peer.port)
        client._ser = peer
        execute = lambda: client._write_block(address, payload)
    if partial:
        with pytest.raises(ProgramReadbackMismatch if fast else ds2.DS2Error):
            execute()
    else:
        execute()
    assert [r.command for r in peer.requests] == [7, 6] * (3 if partial and count == 1 else 1)
    start, length, _offset = program_readback_window(address, count, full=True)
    assert peer.requests[-1].payload == start.to_bytes(4, "big") + bytes((length,))
    assert peer.is_open and peer.baudrate == (187500 if fast else 9600)


@pytest.mark.parametrize("address,count", ((0x1FFF, 1), (0x6000, 1), (0x16000, 1),
                                         (0x3FFF, 2), (0x3FFFF, 2), (0x2000, 244)))
def test_full_readback_refuses_boot_gaps_crossings_and_oversized_blocks(address, count):
    with pytest.raises(ValueError):
        program_readback_window(address, count, full=True)


@pytest.mark.parametrize("fast", (False, True))
@pytest.mark.parametrize("fault", ("lost", "late"))
@pytest.mark.parametrize("address,count", ((0x1370E, 243), (0x13FFA, 6), (0x15FFF, 1)))
@pytest.mark.parametrize("fragment", (1, 62, 4096))
def test_committed_block_continues_without_resend(monkeypatch, fast, fault, address, count, fragment):
    monkeypatch.setattr(ds2.time, "sleep", lambda _seconds: None)
    payload = bytes((index * 17 + 3) % 254 for index in range(count))
    peer = CalibrationPeer(address, fault=fault, fragment=fragment)
    if fast:
        native_session(peer, FlashRequest(0, address, payload))._erase_and_program()
    else:
        client = ds2.DS2Interface(peer.port)
        client._ser = peer
        client._write_block(address, payload)
    programs = [r for r in peer.requests if r.command == 7 and r.payload[0] in (0, 2)]
    assert sum(int.from_bytes(r.payload[1:4], "big") == address for r in programs) == 1
    reads = [r for r in peer.requests if r.command == 6]
    start, length, _offset = program_readback_window(address, count)
    assert [r.payload for r in reads] == [start.to_bytes(4, "big") + bytes((length,))]
    assert peer.memory[address:address + count] == payload
    assert peer.is_open and peer.baudrate == (187500 if fast else 9600)


@pytest.mark.parametrize("fast", (False, True))
@pytest.mark.parametrize("full", (False, True))
@pytest.mark.parametrize("fault,partial", (
    ("lost", True), ("echo", False), ("before_echo", False), ("wrong_cursor", False),
    ("bad_late_crc", False), ("double_late", False), ("read_timeout", False),
    ("read_nak", False), ("read_crc", False), ("short_read", False),
    ("six_byte_impostor", True),
))
def test_unconfirmed_block_stops_without_another_write(monkeypatch, fast, full, fault, partial):
    monkeypatch.setattr(ds2.time, "sleep", lambda _seconds: None)
    address = 0x200F3 if full else 0x1370E
    # Deliberately equal to the original flash ACK's six data bytes.
    op = 0 if fast and not full else 2
    payload = bytes((op,)) + (address + 6).to_bytes(3, "big") + bytes((6, 1))
    peer = CalibrationPeer(address, fault=fault, partial=partial, fragment=1, full=full)
    if fast and not full and partial and fault == "lost":
        native_session(peer, FlashRequest(op, address, payload))._erase_and_program()
        programs = [r for r in peer.requests if r.command == 7 and r.payload[0] == 0]
        assert [(int.from_bytes(r.payload[1:4], "big"), r.payload[5:]) for r in programs[:2]] == [
            (address, payload), (address + 2, payload[2:])]
        assert peer.memory[address:address + len(payload)] == payload
        return
    if fast:
        with pytest.raises(ProgramReadbackMismatch if fault == "lost" and partial else CommitUnknownError):
            if full:
                full_session(peer)._flash_full(FlashRequest(op, address, payload), "full_fault")
            else:
                native_session(peer, FlashRequest(op, address, payload))._erase_and_program()
    else:
        client = ds2.DS2Interface(peer.port)
        client._ser = peer
        with pytest.raises(ds2.DS2Error, match="completion unknown"):
            client._write_block(address, payload)
    programs = [r for r in peer.requests if r.command == 7 and r.payload[0] in (0, 2)]
    assert len(programs) == 1
    assert peer.is_open and peer.baudrate == (187500 if fast else 9600)


def test_partial_program_header_checks_data_before_continuing():
    peer = CalibrationPeer(0x1370E, fault="partial_header")
    native_session(peer, FlashRequest(0, 0x1370E, b"a" * 243))._erase_and_program()
    assert sum(r.command == 6 for r in peer.requests) == 1
    assert peer.memory[0x1370E:0x1370E + 243] == b"a" * 243


@pytest.mark.parametrize("finalizer_fails", (False, True))
def test_full_partial_session_still_requires_finalization(tmp_path, finalizer_fails):
    from tests.test_ds2_fast_partial_write import _authorization_session
    from ds2_fast_contracts import ContractViolation
    session, serial, journal = _authorization_session(tmp_path)
    session.target_tune = bytes(index % 251 for index in range(0x6000))
    serial.missing_flash_response_at = 4
    original = serial._handle_flash
    def flash(args):
        if args[0] == 15 and finalizer_fails:
            serial._response(0xA2)
        else:
            original(args)
    serial._handle_flash = flash
    if finalizer_fails:
        with pytest.raises(ContractViolation):
            session.execute()
        assert not session.flash_completed and journal.outcome == "failed"
        assert session.link is LinkRate.HIGH and session.transport.is_open
    else:
        result = session.execute()
        assert session.flash_completed and result.final_state is SessionState.COMPLETE
        assert journal.outcome == "success" and not result.verified
        assert serial.memory[0x10000:0x16000] == session.target_tune
    assert serial.final_key_accepted
    assert sum(event == "program_commit_confirmed_by_readback" for event, _ in journal.events) == 1
    assert sum(command == 7 and args[0] == 15 for _baud, command, args in serial.requests) == 1


@pytest.mark.parametrize("program_only,location", ((False, "primer"), (False, "program"),
                                                 (False, "tune"), (True, "program")))
@pytest.mark.parametrize("finalizer_fails", (False, True))
def test_full_host_session_requires_finalization_after_recovery(tmp_path, program_only, location, finalizer_fails):
    from tests.test_ds2_fast_full_write import _authorization_full_session
    from ds2_fast_contracts import ContractViolation
    session, peer, journal = _authorization_full_session(tmp_path)
    target = bytearray(session.target_file_image)
    target[0x6000:0x6400] = b"\x55" * 0x400
    target[0x24000:0x24100] = b"\x33" * 0x100
    target[0x14000:0x14100] = b"\xaa" * 0x100
    session.target_file_image = bytes(target)
    address = {"primer": 0x2000, "program": 0x20F3, "tune": 0x10000}[location]
    original = peer._handle_flash
    lost = False
    def flash(args):
        nonlocal lost
        op, current = args[0], int.from_bytes(args[1:4], "big")
        if not lost and op == 2 and current == address:
            peer.missing_flash_response_at = len(peer.flash_requests) + 1
            lost = True
        if op == 15 and current == 0x1D07 and finalizer_fails:
            peer.flash_outer_status_at[len(peer.flash_requests) + 1] = 0xA2
        original(args)
    peer._handle_flash = flash
    execute = session.execute_program_only if program_only else session.execute
    if finalizer_fails:
        with pytest.raises(ContractViolation):
            execute()
        assert not session.flash_completed and journal.outcome == "failed"
    else:
        result = execute()
        assert session.flash_completed and journal.outcome == "success"
        assert not result.verified and result.power_cycle_required
    assert lost and session.link is LinkRate.HIGH and session.transport.is_open
    assert sum(event == "program_commit_confirmed_by_readback" for event, _ in journal.events) == 1
    programs = [request for request in peer.flash_requests if request[0] == 2]
    assert len(programs) == len(set(programs))


@pytest.mark.parametrize("full", (False, True))
@pytest.mark.parametrize("status", (0, 7, 8, 12, 15))
def test_explicit_phase_rejection_never_becomes_successful_readback(full, status):
    address = 0x2000 if full else 0x10000
    operation = 2 if full else 0
    peer = CalibrationPeer(-1, full=full)
    original = peer.program
    def reject(op, start, data):
        original(op, start, data)
        return encode_ds2_frame(0xA0, bytes((op,)) + (start + len(data)).to_bytes(3, "big")
                                + bytes((len(data), status)))
    peer.program = reject
    request = FlashRequest(operation, address, b"abc")
    session = full_session(peer) if full else native_session(peer, request)
    from ds2_fast_contracts import ContractViolation
    with pytest.raises(ContractViolation) as error:
        session._flash(request, label="explicit_phase_rejection")
    assert error.value.flash_status == status
    assert [r.command for r in peer.requests] == [7]


@pytest.mark.parametrize("full", (False, True))
def test_blank_packet_retry_stops_after_three_total_attempts(full):
    address = 0x2000 if full else 0x10000
    operation = 2 if full else 0
    peer = CalibrationPeer(-1, full=full)
    peer.program = lambda op, start, data: encode_ds2_frame(
        0xA0, bytes((op,)) + start.to_bytes(3, "big") + bytes((0, 2)))
    request = FlashRequest(operation, address, b"abcdef")
    session = full_session(peer) if full else native_session(peer, request)
    with pytest.raises(ProgramReadbackMismatch) as error:
        session._flash(request, label="persistent_blank_failure")
    assert error.value.blank
    assert [r.command for r in peer.requests] == [7, 6] * 3
    assert peer.memory[address:address + 6] == b"\xff" * 6


def test_native_suffix_retries_share_the_original_three_attempt_budget():
    peer = CalibrationPeer(-1)
    def partial(op, start, data):
        peer.memory[start] = data[0]
        return encode_ds2_frame(0xA0, bytes((op,)) + (start + 1).to_bytes(3, "big")
                                + bytes((1, 2)))
    peer.program = partial
    request = FlashRequest(0, 0x10000, b"abcdef")
    session = native_session(peer, request)
    with pytest.raises(ProgramReadbackMismatch) as error:
        session._flash(request, label="repeated_partial_failure")
    assert not error.value.blank
    programs = [r for r in peer.requests if r.command == 7]
    assert [int.from_bytes(r.payload[1:4], "big") for r in programs] == [0x10000, 0x10001, 0x10002]
    assert [r.payload[5:] for r in programs] == [b"abcdef", b"bcdef", b"cdef"]
    assert peer.memory[0x10000:0x10006] == b"abc\xff\xff\xff"


@pytest.mark.parametrize("actual", (b"xbcdef", b"ab\xff\xffx\xff"))
def test_native_suffix_retry_rejects_wrong_bits_or_fragmented_data(actual):
    peer = CalibrationPeer(-1)
    def partial(op, start, data):
        peer.memory[start:start + len(data)] = actual
        return encode_ds2_frame(0xA0, bytes((op,)) + start.to_bytes(3, "big") + bytes((0, 2)))
    peer.program = partial
    request = FlashRequest(0, 0x10000, b"abcdef")
    with pytest.raises(ProgramReadbackMismatch):
        native_session(peer, request)._flash(request, label="unrepairable_partial_failure")
    assert [r.command for r in peer.requests] == [7, 6]


def test_native_readback_a2_is_preserved_for_recovery_classification():
    peer = CalibrationPeer(0x10000, fault="read_nak")
    request = FlashRequest(0, peer.address, b"abcdef")
    with pytest.raises(CommitUnknownError) as error:
        native_session(peer, request)._flash(request, label="readback_a2")
    assert error.value.response_status == 0xA2
    assert [r.command for r in peer.requests] == [7, 6]
