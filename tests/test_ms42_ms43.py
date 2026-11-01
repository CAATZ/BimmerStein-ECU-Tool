"""All transports in this module are fakes; no serial device is enumerated."""

from dataclasses import replace
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

import ms42_ms43 as protocol
from ds2 import DS2Error, DS2Timeout, DS2NegativeResponse, _xor
from ms42_ms43 import MS42MS43ECU, FlashInterrupted


def reply(data=b"", status=0xA0, address=0x12):
    body = bytes((address, len(data) + 4, status)) + data
    return body + bytes((_xor(body),))


class Link:
    port = "FAKE_ONLY"
    baud = 9600
    is_open = True
    uses_d2xx = False
    echo = True
    operation_check = None
    diagnostic_fn = None
    native_fast_capable = False

    def __init__(self, responder=lambda command, args: reply()):
        self.responder = responder
        self.calls = []
        self.stopbits = []
        self._ser = SimpleNamespace(baudrate=9600)

    def execute_frame(self, command, args=b"", timeout=None):
        self.calls.append((0x12, command, bytes(args)))
        return self.responder(command, bytes(args))

    def send_frame(self, frame, address, timeout=None, *, strict_echo=False):
        assert strict_echo
        assert _xor(frame) == 0
        self.calls.append((address, frame[2], frame[3:-1]))
        return reply(address=address)

    def _check_operation(self):
        if self.operation_check:
            self.operation_check()

    def _checked_io(self, operation, *args):
        self._check_operation()
        result = operation(*args)
        self._check_operation()
        return result

    def set_stopbits(self, two_stop):
        self.stopbits.append(two_stop)

    def close(self):
        self.is_open = False


class Journal:
    def __init__(self):
        self.events = []
        self.closed = False

    def append(self, event, **fields):
        self.events.append((event, fields))

    def finish(self, outcome, **fields):
        self.events.append(("finished", dict(outcome=outcome, **fields)))
        self.closed = True


@pytest.fixture(autouse=True)
def offline_clock(monkeypatch):
    clock = SimpleNamespace(now=0.0, waits=[])
    def sleep(delay):
        clock.waits.append(delay)
        clock.now += delay
    monkeypatch.setattr(protocol.time, "monotonic", lambda: clock.now)
    monkeypatch.setattr(protocol.time, "sleep", sleep)
    return clock


@pytest.mark.parametrize("family,pointer,hw", [
    ("MS42", 0x50048, b"11101102"), ("MS43", 0xEFFCC, b"11143053"),
])
def test_pointer_detection_without_family_assumptions(family, pointer, hw):
    directory = bytearray(63)
    directory[54:57] = pointer.to_bytes(3, "big")
    link = Link(lambda command, args: reply(directory) if command == 0x0D else reply(hw))
    assert protocol.detect_family(link) == family
    assert link.calls == [(0x12, 0x0D, b""), (0x12, 6, pointer.to_bytes(4, "big") + b"\x08")]


def test_ms41_detection_does_not_change_framing_or_probe_ms42():
    link = Link(lambda *_: pytest.fail("Known MS41 should not be reprobed"))
    assert protocol.detect_connected(link, b"1406464" + b" " * 35) is link
    assert link.stopbits == []


def test_unknown_family_does_not_fall_into_ms41_probes():
    link = Link(lambda command, args: reply(bytes(63)) if command == 0x0D else reply(b"00000000"))
    with pytest.raises(DS2Error, match="Unsupported ECU"):
        protocol.detect_connected(link, b"unknown")
    assert link.stopbits == [False]
    assert [c[1] for c in link.calls] == [0x0D, 6]


@pytest.mark.parametrize("nonreply", [False, True])
def test_factory_shaped_ms41_unknown_id_can_use_independent_read_only_probes(nonreply):
    ident = (b"???????" + b"12" + b"34" + b"56" + b"78" + b"40" + b"97" +
             b"SIEMENS585" + b"10" + b"11" + b"012345678")
    def respond(command, args):
        if nonreply:
            raise DS2Timeout("no reply at 8E1")
        return reply(bytes(63)) if command == 0x0D else reply(b"00000000")
    link = Link(respond)
    assert protocol.detect_connected(link, ident) is link
    assert link.stopbits == [False, True]
    assert link.ms41_identity_unconfirmed


def test_positive_ms43_never_uses_ms41_shaped_unknown_id_fallback():
    ident = (b"???????" + b"12" + b"34" + b"56" + b"78" + b"40" + b"97" +
             b"SIEMENS585" + b"10" + b"11" + b"012345678")
    link = Link(lambda command, args: reply(bytes(63)) if command == 0x0D else reply(b"11143053"))
    result = protocol.detect_connected(link, ident)
    assert result.family == "MS43"
    assert result.raw_ident == ident
    assert link.stopbits == [False]


@pytest.mark.parametrize("frame", [reply(status=0xFF), reply(b"short"), bytes.fromhex("12 04 A0 00")])
def test_detection_rejects_bad_status_length_or_checksum(frame):
    with pytest.raises(DS2Error):
        protocol.detect_family(Link(lambda *_: frame))


def test_read_chunks_have_four_address_bytes_and_fail_on_short_reply():
    link = Link(lambda command, args: reply(bytes([args[-1]]) * args[-1]))
    ecu = MS42MS43ECU(link, "MS43")
    assert ecu.read_memory(0x90000, 124) == bytes([123]) * 123 + b"\x01"
    assert link.calls == [(0x12, 6, bytes.fromhex("00 09 00 00 7B")),
                          (0x12, 6, bytes.fromhex("00 09 00 7B 01"))]
    link.responder = lambda *_: reply(b"\x00")
    with pytest.raises(DS2Error, match="expected 6"):
        ecu.read_memory(0, 2)


def test_read_only_timeout_retries_are_bounded():
    link = Link()
    def timeout(*_):
        raise DS2Timeout("missing reply")
    link.responder = timeout
    with pytest.raises(DS2Timeout):
        MS42MS43ECU(link, "MS42").read_memory(0, 1)
    assert len(link.calls) == 5


@pytest.mark.parametrize("capable,force_slow,expected", [
    (True, False, 125000), (True, True, 9600), (False, False, 9600),
])
def test_transfer_uses_original_default_only_with_exact_backend(capable, force_slow, expected):
    link = Link()
    link.native_fast_capable = capable
    ecu = MS42MS43ECU(link, "MS43")
    with ecu.transfer(force_slow=force_slow) as active:
        assert active is ecu
        assert ecu.baud == link._ser.baudrate == expected
    assert ecu.baud == link._ser.baudrate == 9600
    assert not ecu.baud_uncertain
    expected_calls = ([(0x12, 0x91, protocol.BAUD_PAYLOADS[125000]),
                       (0x12, 0x91, protocol.BAUD_PAYLOADS[9600])]
                      if expected == 125000 else [])
    assert link.calls == expected_calls


def test_transfer_keeps_rate_and_link_when_body_fails():
    link = Link()
    link.native_fast_capable = True
    ecu = MS42MS43ECU(link, "MS43")
    with pytest.raises(DS2Timeout, match="program response missing"):
        with ecu.transfer():
            raise DS2Timeout("program response missing")
    assert link.baud == link._ser.baudrate == 125000
    assert link.is_open and not ecu.baud_uncertain
    assert link.calls == [(0x12, 0x91, protocol.BAUD_PAYLOADS[125000])]


def test_retry_transfer_retains_rate_despite_changed_force_slow_choice():
    link = Link()
    link.native_fast_capable = True
    link.baud = link._ser.baudrate = 125000
    ecu = MS42MS43ECU(link, "MS43")
    ecu._pending_image = b"retained immutable image"
    with ecu.transfer(force_slow=True):
        assert ecu.baud == 125000
        assert link.calls == []
        ecu._pending_image = None
    assert ecu.baud == 9600
    assert link.calls == [(0x12, 0x91, protocol.BAUD_PAYLOADS[9600])]


@pytest.mark.parametrize("host_failure", [False, True])
def test_ambiguous_baud_handoff_blocks_further_commands(host_failure):
    link = Link()
    if host_failure:
        def fail_host(*_):
            raise OSError("driver failed")
        link._checked_io = fail_host
    else:
        def no_reply(*_):
            raise DS2Timeout("rate acknowledgement lost")
        link.responder = no_reply
    ecu = MS42MS43ECU(link, "MS42")
    with pytest.raises(protocol.BaudTransitionError, match="unconfirmed"):
        ecu.set_baud(125000)
    assert ecu.baud_uncertain
    with pytest.raises(protocol.BaudTransitionError, match="reconnect"):
        ecu.read_voltage()
    with pytest.raises(protocol.BaudTransitionError, match="reconnect"):
        ecu.set_baud(9600)
    assert len(link.calls) == 1
    assert link.is_open


def test_explicit_baud_rejection_keeps_known_rate_without_fallback():
    link = Link(lambda *_: reply(status=0xA2))
    ecu = MS42MS43ECU(link, "MS42")
    with pytest.raises(protocol.BaudTransitionError, match="rejected"):
        ecu.set_baud(125000)
    assert link.baud == link._ser.baudrate == 9600
    assert not ecu.baud_uncertain
    assert len(link.calls) == 1


def test_failed_restore_after_completed_flash_never_requests_write_retry():
    def respond(command, args):
        if args == protocol.BAUD_PAYLOADS[9600]:
            raise DS2Timeout("restore acknowledgement lost")
        return reply()
    link = Link(respond)
    link.native_fast_capable = True
    ecu = MS42MS43ECU(link, "MS43")
    with pytest.raises(protocol.BaudTransitionError, match="Programming completed") as error:
        with ecu.transfer():
            ecu.last_write = protocol.WriteState("MS43", "tune", completed=True, finalized=True)
    assert error.value.programming_completed
    assert ecu.write_state.completed
    assert ecu.baud_uncertain and link.is_open
    with pytest.raises(DS2Error, match="No incomplete write"):
        ecu.retry_write()


@pytest.mark.parametrize("family", ["MS42", "MS43"])
def test_full_read_uses_exact_profile_spans_and_ff_gaps(family):
    ecu = MS42MS43ECU(Link(), family)
    calls = []
    def read(address, length, callback=None):
        calls.append((address, length))
        return bytes([len(calls)]) * length
    ecu.read_memory = read
    result = ecu.read_full()
    assert len(result) == 0x80000
    for index, (address, offset, length) in enumerate(ecu.profile.read_regions, 1):
        assert calls[index - 1] == (address, length)
        assert result[offset:offset + length] == bytes([index]) * length
    assert result[0xE000:0x10000] == b"\xff" * 0x2000


@pytest.mark.parametrize("family", ["MS42", "MS43"])
def test_unlock_indexes_complete_frame_and_accepts_five_byte_reply(monkeypatch, family):
    seed = bytearray(range(46))
    seed[:3] = bytes.fromhex("12 2E A0")
    seed[-1] = _xor(seed[:-1])
    link = Link(lambda command, args: bytes(seed) if args.startswith(b"BMW") else reply(b"\x00"))
    ecu = MS42MS43ECU(link, family)
    monkeypatch.setattr(protocol.secrets, "randbelow", lambda bound: 22)
    ecu.unlock()
    assert link.calls == [(0x12, 0x90, bytes.fromhex("42 4D 57 17")),
                          (0x12, 0x90, bytes.fromhex("52 55 58 5B"))]
    link.calls.clear()
    link.responder = lambda *_: reply(b"\x00")
    ecu.unlock()
    assert len(link.calls) == 1


def test_unlock_rejects_any_other_seed_length():
    link = Link(lambda *_: reply(b"\x00\x00"))
    with pytest.raises(DS2Error, match="5 or 46"):
        MS42MS43ECU(link, "MS42").unlock()
    assert len(link.calls) == 1


@pytest.mark.parametrize("family", ["MS42", "MS43"])
def test_unlock_waits_for_readiness_without_changing_challenge_or_key(family, monkeypatch):
    seed = bytearray(range(46))
    seed[:3] = bytes.fromhex("12 2E A0")
    seed[-1] = _xor(seed[:-1])
    responses = iter((reply(status=0xA1), reply(status=0xA1), bytes(seed),
                      reply(status=0xA1), reply(status=0xA1), reply(b"\x00")))
    link = Link(lambda *_: next(responses))
    challenges, waits = [], []
    monkeypatch.setattr(protocol.secrets, "randbelow", lambda bound: challenges.append(bound) or 22)
    monkeypatch.setattr(protocol.time, "sleep", waits.append)

    MS42MS43ECU(link, family).unlock()

    assert challenges == [23]
    assert link.calls == ([(0x12, 0x90, b"BMW\x17")] * 3 +
                          [(0x12, 0x90, bytes.fromhex("52 55 58 5B"))] * 3)
    assert waits == [0.2] * 4


@pytest.mark.parametrize("family", ["MS42", "MS43"])
@pytest.mark.parametrize("phase", ["seed", "key"])
def test_unlock_allows_native_startup_readiness_without_new_challenge(
        family, phase, monkeypatch, offline_clock):
    seed = bytearray(range(46))
    seed[:3] = bytes.fromhex("12 2E A0")
    seed[-1] = _xor(seed[:-1])
    challenge, key = b"BMW\x17", bytes.fromhex("52 55 58 5B")
    ready_after = 10 if family == "MS42" else 9
    def respond(command, args):
        assert command == 0x90 and args in (challenge, key)
        if args == (challenge if phase == "seed" else key) and offline_clock.now < ready_after:
            return reply(status=0xA1)
        return bytes(seed) if args == challenge else reply(b"\x00")
    link = Link(respond)
    challenges = []
    monkeypatch.setattr(protocol.secrets, "randbelow", lambda bound: challenges.append(bound) or 22)

    MS42MS43ECU(link, family).unlock()

    assert ready_after <= offline_clock.now <= ready_after + 0.2
    assert challenges == [23]
    payloads = [args for _, command, args in link.calls]
    assert payloads.count(key if phase == "seed" else challenge) == 1
    assert all(args == (challenge if phase == "seed" else key)
               for args in (payloads[:-1] if phase == "seed" else payloads[1:]))


@pytest.mark.parametrize("family", ["MS42", "MS43"])
@pytest.mark.parametrize("phase", ["seed", "key"])
def test_unlock_readiness_wait_is_bounded_in_each_phase(family, phase, offline_clock):
    responses = iter([reply(bytes(42))] if phase == "key" else [])
    request_times = []
    def respond(*_):
        request_times.append(offline_clock.now)
        return next(responses, reply(status=0xA1))
    link = Link(respond)

    with pytest.raises(DS2Timeout, match="A1") as error:
        MS42MS43ECU(link, family).unlock()

    assert 15 <= offline_clock.now <= 15.2
    assert isinstance(error.value, protocol.AuthorizationBusyError)
    attempts = link.calls[1:] if phase == "key" else link.calls
    assert len(set(attempts)) == 1 and 70 <= len(attempts) <= 78
    assert all(0 < delay <= 0.2 for delay in offline_clock.waits)
    assert all(timestamp < 15 for timestamp in request_times)


@pytest.mark.parametrize("phase", ["seed", "key"])
@pytest.mark.parametrize("after_busy", [False, True])
@pytest.mark.parametrize("failure", [reply(status=0xA2), reply(status=0xB0),
                                    bytes.fromhex("12 04 A1 00"), reply(b"\x00", status=0xA1),
                                    DS2Timeout("response missing")])
def test_unlock_does_not_retry_nonbusy_or_ambiguous_failures(phase, failure, after_busy, monkeypatch):
    responses = iter(([reply(bytes(42))] if phase == "key" else [])
                     + ([reply(status=0xA1)] if after_busy else []) + [failure])
    def respond(*_):
        response = next(responses)
        if isinstance(response, Exception):
            raise response
        return response
    link = Link(respond)
    waits = []
    monkeypatch.setattr(protocol.time, "sleep", waits.append)

    with pytest.raises(DS2Error):
        MS42MS43ECU(link, "MS43").unlock()
    assert len(link.calls) == 1 + (phase == "key") + after_busy
    assert waits == [0.2] * after_busy


def test_unlock_cancellation_during_readiness_wait_prevents_next_request(monkeypatch):
    link = Link(lambda *_: reply(status=0xA1))
    def cancel(_):
        def check():
            raise RuntimeError("Cancelled")
        link.operation_check = check
    monkeypatch.setattr(protocol.time, "sleep", cancel)

    with pytest.raises(RuntimeError, match="Cancelled"):
        MS42MS43ECU(link, "MS43").unlock()
    assert len(link.calls) == 1


def test_unlock_cancellation_remains_responsive_after_repeated_busy(offline_clock):
    link = Link(lambda *_: reply(status=0xA1))
    def check():
        if offline_clock.now >= 4:
            raise RuntimeError("Cancelled")
    link.operation_check = check

    with pytest.raises(RuntimeError, match="Cancelled"):
        MS42MS43ECU(link, "MS43").unlock()

    assert 4 <= offline_clock.now <= 4.2
    assert 20 <= len(link.calls) <= 21


@pytest.mark.parametrize("initial_busy", [0, 9])
@pytest.mark.parametrize("family", ("MS42", "MS43"))
def test_ram_entry_changes_authorization_once_then_waits_for_new_readiness(
        offline_clock, initial_busy, family):
    from ms42_ms43_resident import PROFILES
    profile = PROFILES[family]
    transition = None
    def respond(command, payload):
        nonlocal transition
        assert command == 0x90
        if payload == profile["key"]:
            return reply(b"\x00")
        assert payload == b"\xFC\x00\x00\x00"
        if offline_clock.now < initial_busy:
            return reply(status=0xA1)
        if transition is None:
            transition = offline_clock.now
            return reply(status=0xA2)
        if offline_clock.now < transition + 9:
            return reply(status=0xA1)
        return reply(profile["challenge"])
    link, logs = Link(respond), []
    ecu = MS42MS43ECU(link, family)

    assert ecu._authorization_frame(b"\xFC\x00\x00\x00", phase="RAM challenge",
                                    log_fn=logs.append)[3:-1] == profile["challenge"]
    assert ecu._authorization_frame(profile["key"], phase="RAM key")[3:-1] == b"\x00"
    assert transition + 9 <= offline_clock.now < transition + 9.2
    assert len(set(link.calls[:-1])) == 1
    assert link.calls[-1] == (0x12, 0x90, profile["key"])
    assert sum("after A2" in message for message in logs) == 1
    assert ecu.rate_confirmed and link.baud == 9600


@pytest.mark.parametrize("family", ("MS42", "MS43"))
@pytest.mark.parametrize("phase,payload", [
    ("RAM key", bytes.fromhex("54237DB2")),
    ("authorization", b"\xFC\x00\x00\x00"),
    ("RAM challenge", b"BMW\x17"),
])
def test_ram_a2_reentry_does_not_change_other_authorization_paths(
        family, phase, payload, offline_clock):
    link = Link(lambda *_: reply(status=0xA2))
    with pytest.raises(DS2NegativeResponse) as caught:
        MS42MS43ECU(link, family)._authorization_frame(payload, phase=phase)
    assert caught.value.status == 0xA2
    assert link.calls == [(0x12, 0x90, payload)] and not offline_clock.waits


@pytest.mark.parametrize("busy_between", [False, True])
@pytest.mark.parametrize("family", ("MS42", "MS43"))
def test_ram_reentry_never_repeats_a_second_a2(busy_between, family):
    responses = iter([reply(status=0xA2)] +
                     ([reply(status=0xA1)] if busy_between else []) + [reply(status=0xA2)])
    link = Link(lambda *_: next(responses))
    with pytest.raises(DS2NegativeResponse) as caught:
        MS42MS43ECU(link, family)._authorization_frame(b"\xFC\x00\x00\x00", phase="RAM challenge")
    assert caught.value.status == 0xA2
    assert link.calls == [(0x12, 0x90, b"\xFC\x00\x00\x00")] * (2 + busy_between)


@pytest.mark.parametrize("after_transition", [False, True])
@pytest.mark.parametrize("family", ("MS42", "MS43"))
@pytest.mark.parametrize("failure", [
    reply(status=0xB0), reply(b"\x00", status=0xA2),
    bytes.fromhex("12 04 A2 00"), reply(status=0xA2, address=0x32),
    DS2Timeout("response missing"),
])
def test_ram_reentry_does_not_replay_malformed_lost_or_wrong_key_replies(
        failure, after_transition, family):
    responses = iter(([reply(status=0xA2)] if after_transition else []) + [failure])
    def respond(*_):
        result = next(responses)
        if isinstance(result, Exception):
            raise result
        return result
    link = Link(respond)
    with pytest.raises(DS2Error):
        MS42MS43ECU(link, family)._authorization_frame(b"\xFC\x00\x00\x00", phase="RAM challenge")
    assert link.calls == [(0x12, 0x90, b"\xFC\x00\x00\x00")] * (1 + after_transition)


@pytest.mark.parametrize("family", ("MS42", "MS43"))
def test_ram_reentry_readiness_remains_bounded(offline_clock, family):
    requests = []
    def respond(*_):
        requests.append(offline_clock.now)
        return reply(status=0xA2 if len(requests) == 1 else 0xA1)
    link = Link(respond)
    with pytest.raises(protocol.AuthorizationBusyError):
        MS42MS43ECU(link, family)._authorization_frame(b"\xFC\x00\x00\x00", phase="RAM challenge")
    assert offline_clock.now == pytest.approx(15)
    assert all(timestamp < 15 for timestamp in requests)
    assert all(0 < delay <= 0.2 for delay in offline_clock.waits)
    assert len(set(link.calls)) == 1


def test_flash_result_is_raw_index_eight_and_busy_is_bounded(monkeypatch):
    replies = iter((reply(status=0xA1), reply(status=0xA1), reply(b"\x00" * 5 + b"\x01")))
    ecu = MS42MS43ECU(Link(lambda *_: next(replies)), "MS43")
    ecu._flash(0x0F, 0x70000)
    ecu.transport.responder = lambda *_: reply(b"\x00" * 5 + b"\x02" + b"\x00\x00\x01")
    with pytest.raises(DS2Error, match="verification failed"):
        ecu._flash(2, 0x70000, b"ab")
    now = [0.0]
    monkeypatch.setattr(protocol.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(protocol.time, "sleep", lambda delay: now.__setitem__(0, now[0] + delay))
    ecu.transport.responder = lambda *_: reply(status=0xA1)
    with pytest.raises(DS2Timeout, match="remained busy"):
        ecu._flash(0x0F, 0x70000, busy_timeout=0.15)
    assert now[0] == pytest.approx(0.15)


def make_write(family):
    ecu = MS42MS43ECU(Link(), family)
    image = bytearray(b"\xff" * 0x80000)
    hardware = "11143053" if family == "MS43" else "11101102"
    software = "430069" if family == "MS43" else "0110C6"
    image[ecu.profile.hw_offset:ecu.profile.hw_offset + 8] = hardware.encode()
    image[ecu.profile.sw_offset:ecu.profile.sw_offset + 6] = software.encode()
    calibration = ecu.profile.calibration_offset
    image[calibration + 8:calibration + 14] = software.encode()
    image[calibration + 0x20:calibration + 0x28] = (hardware[:6] + "00").encode()
    ecu._identity_fields = lambda: {"family": family, "hardware_id": hardware, "software_id": software}
    ecu.profile = replace(ecu.profile,
                          program_write_regions=tuple((a, o, 8) for a, o, _ in ecu.profile.program_write_regions),
                          calibration_write_length=7)
    for _address, offset, _length in ecu.profile.program_write_regions:
        image[offset + 2:offset + 4] = b"ab"
    image[ecu.profile.calibration_offset + 2:ecu.profile.calibration_offset + 4] = b"cd"
    events = []
    ecu.unlock = lambda: events.append(("unlock",))
    ecu._flash = lambda op, address, data=b"", **kw: events.append((op, address, data, kw.get("checked", True)))
    return ecu, bytes(image), events


@pytest.mark.parametrize("family", ["MS42", "MS43"])
def test_full_write_preserves_boot_and_orders_both_ms42_program_spans(family):
    ecu, image, events = make_write(family)
    journal = Journal()
    state = ecu.write_full(image, correct_checksum=False, journal=journal)
    program_start = ecu.profile.program_write_regions[0][0]
    cal = ecu.profile.calibration_offset
    assert events[:3] == [("unlock",), (15, program_start, b"", False), (6, program_start, b"", True)]
    erases = [event[1] for event in events if event[0] == 6]
    assert erases == [program_start, cal]
    assert [(event[1], event[3]) for event in events if event[0] == 15] == [
        (program_start, False), (program_start, False), (cal, True)]
    assert [(event[1], event[2]) for event in events if event[0] == 2] == [
        *((a + 2, b"ab") for a, _o, _n in ecu.profile.program_write_regions),
        (cal + 2, b"cd\xff")]
    assert state.completed and state.finalized and not state.verified
    assert journal.closed and journal.events[-1][1]["outcome"] == "success"
    assert ecu.is_open


@pytest.mark.parametrize("family", ["MS42", "MS43"])
@pytest.mark.parametrize("becomes_ready", [False, True])
def test_full_write_waits_for_authorization_before_any_flash_command(family, becomes_ready):
    ecu, image, events = make_write(family)
    del ecu.unlock  # Exercise the production unlock through the full write workflow.
    responses = iter([reply(status=0xA1)] * 2 + ([reply(b"\x00")] if becomes_ready else []))
    ecu.transport.responder = lambda *_: next(responses, reply(status=0xA1))
    journal = Journal()

    if becomes_ready:
        state = ecu.write_full(image, correct_checksum=False, journal=journal)
        assert state.completed
        assert len(ecu.transport.calls) == 3
        assert events[0][0] == 15
        assert any(event[0] == 6 for event in events)
    else:
        with pytest.raises(FlashInterrupted, match="authorization remained busy.*A1") as error:
            ecu.write_full(image, correct_checksum=False, journal=journal)
        assert not error.value.completion_unknown
        assert not ecu.write_state.destructive_started
        assert ecu.write_state.phase == "unlock"
        assert ecu._pending_image is None
        assert journal.events[-1][1]["outcome"] == "aborted"
        assert events == []
        assert 70 <= len(ecu.transport.calls) <= 78
    assert ecu.is_open


@pytest.mark.parametrize("family", ["MS42", "MS43"])
@pytest.mark.parametrize("phase", ["initial", "key"])
@pytest.mark.parametrize("payload", [b"", b"\x7f", b"\x00\x00"])
def test_unexpected_unlock_ack_stops_full_write_without_retry_or_flash(family, phase, payload):
    ecu, image, events = make_write(family)
    del ecu.unlock
    ecu.transport.responder = lambda command, args: (
        reply(bytes(42)) if phase == "key" and args.startswith(b"BMW") else reply(payload))
    journal = Journal()

    with pytest.raises(FlashInterrupted, match="Unlock") as error:
        ecu.write_full(image, correct_checksum=False, journal=journal)

    assert not error.value.completion_unknown and not ecu.write_state.destructive_started
    assert ecu.write_state.phase == "unlock" and ecu._pending_image is None
    assert journal.events[-1][1]["outcome"] == "aborted"
    assert events == []
    assert len(ecu.transport.calls) == (2 if phase == "key" else 1)
    assert all(command == 0x90 for _, command, _ in ecu.transport.calls)


def test_tune_full_input_is_not_programmed_from_offset_zero():
    ecu, image, events = make_write("MS42")
    ecu.write_tune(image, correct_checksum=False, journal=Journal())
    assert not any(event[0] == 2 and event[1] < 0x48000 for event in events)
    assert [event[1] for event in events if event[0] == 6] == [0x48000]


def test_write_chunk_bound_and_erased_word_skipping():
    ecu = MS42MS43ECU(Link(), "MS43")
    calls = []
    ecu._flash = lambda op, addr, data: calls.append((addr, data))
    state = protocol.WriteState("MS43", "tune")
    data = b"\xff\xff" + b"a" * 120 + b"\xff\xff" + b"b"
    ecu._write_region(data, 0x70000, 0, len(data), None, 0, len(data), Journal(), state)
    assert all(len(chunk) <= 118 for _address, chunk in calls)
    assert calls[0] == (0x70002, b"a" * 118)
    reconstructed = bytearray(b"\xff" * len(data))
    for address, chunk in calls:
        reconstructed[address - 0x70000:address - 0x70000 + len(chunk)] = chunk
    assert reconstructed == data


def test_ambiguous_write_is_not_retried_and_retains_explicit_recovery(monkeypatch):
    ecu, image, events = make_write("MS43")
    original = ecu._flash
    def fail(op, address, data=b"", **kw):
        original(op, address, data, **kw)
        if op == 2:
            raise DS2Timeout("lost ACK")
    ecu._flash = fail
    journal = Journal()
    with pytest.raises(FlashInterrupted) as raised:
        ecu.write_full(image, correct_checksum=False, journal=journal)
    assert raised.value.completion_unknown
    assert sum(event[0] == 2 for event in events) == 1
    assert ecu.is_open and ecu.write_state.destructive_started
    assert journal.events[-1][1]["outcome"] == "commit_unknown"
    with pytest.raises(DS2Error, match="explicitly retry"):
        ecu.write_full(image, correct_checksum=False, journal=Journal())
    monkeypatch.setattr(protocol, "_hardware_from_directory",
                        lambda link: pytest.fail("Program identity may be erased during recovery"))
    ecu._identity_fields = lambda: pytest.fail("Program identity was erased")
    ecu._flash = original
    result = ecu.retry_write(journal=Journal())
    assert result.completed and ecu._pending_image is None


def test_recovery_does_not_reuse_a_reopened_transport():
    ecu, image, _events = make_write("MS43")
    def fail(op, address, data=b"", **kw):
        if op == 6:
            raise DS2Timeout("lost erase response")
    ecu._flash = fail
    with pytest.raises(FlashInterrupted):
        ecu.write_full(image, correct_checksum=False, journal=Journal())
    ecu.transport._ser = SimpleNamespace(baudrate=9600)
    with pytest.raises(DS2Error, match="original retained connection"):
        ecu.retry_write(journal=Journal())
    assert ecu._pending_image is not None


def test_failed_retry_preserves_prior_incomplete_state(monkeypatch):
    ecu, image, _events = make_write("MS43")
    ecu._pending_image = image
    ecu._pending_full = True
    ecu._pending_live = ecu._identity_fields()
    ecu._pending_serial = ecu.transport._ser
    def fail():
        raise DS2Timeout("unlock timeout")
    ecu.unlock = fail
    with pytest.raises(FlashInterrupted) as raised:
        ecu.retry_write(journal=Journal())
    assert raised.value.completion_unknown and ecu.write_state.destructive_started


def test_cancellation_before_erase_never_sends_destructive_request():
    ecu, image, events = make_write("MS42")
    cancelled = [False]
    def check():
        if cancelled[0]:
            raise RuntimeError("cancelled")
    ecu.operation_check = check
    ecu.diagnostic_fn = lambda event, fields: cancelled.__setitem__(0, True)
    with pytest.raises(FlashInterrupted) as raised:
        ecu.write_full(image, correct_checksum=False, journal=Journal())
    assert not raised.value.completion_unknown
    assert not any(event[0] in (2, 6) for event in events)


def test_optional_verification_detects_first_difference():
    ecu, image, _events = make_write("MS42")
    ecu.read_memory = lambda address, length, callback=None: b"\x00" * length
    with pytest.raises(FlashInterrupted, match="Readback differs") as raised:
        ecu.write_full(image, correct_checksum=False, verify=True, journal=Journal())
    assert raised.value.state.finalized and not raised.value.state.verified
    assert raised.value.state.address == 0x11000


def test_journal_failure_after_programming_reports_completion_without_retry():
    ecu, image, _events = make_write("MS42")
    class BrokenJournal(Journal):
        def finish(self, outcome, **fields):
            raise OSError("disk full")
    with pytest.raises(protocol.ProgrammingCompletedError, match="follow-up failed: disk full") as raised:
        ecu.write_full(image, correct_checksum=False, journal=BrokenJournal())
    assert raised.value.programming_completed
    assert raised.value.state.finalized and raised.value.state.completed
    assert ecu._pending_image is None
    with pytest.raises(DS2Error, match="No incomplete write"):
        ecu.retry_write(journal=Journal())


def test_success_log_failure_keeps_programming_completed_and_success_journal():
    ecu, image, _events = make_write("MS42")
    journal = Journal()
    failure = RuntimeError("log sink closed")

    def log(message):
        if message.startswith("Write complete."):
            raise failure

    with pytest.raises(protocol.ProgrammingCompletedError, match="follow-up failed") as raised:
        ecu.write_full(image, correct_checksum=False, journal=journal, log_fn=log)
    assert raised.value.original_error is raised.value.__cause__ is failure
    assert ecu.last_write.completed and not ecu.can_retry_write
    assert ecu._pending_image is ecu._pending_writer is None
    assert journal.closed and journal.events[-1][1]["outcome"] == "success"


def test_mismatched_calibration_software_is_rejected_before_unlock():
    ecu, image, events = make_write("MS42")
    image = bytearray(image)
    image[ecu.profile.calibration_offset + 8:ecu.profile.calibration_offset + 14] = b"0110C7"
    with pytest.raises(ValueError, match="software ID"):
        ecu.write_tune(image, correct_checksum=False, journal=Journal())
    assert events == []


def test_full_write_rejects_mismatched_program_calibration_identity():
    ecu, image, events = make_write("MS43")
    image = bytearray(image)
    image[0x70008:0x7000E] = b"430055"
    with pytest.raises(ValueError, match="program and calibration"):
        ecu.write_full(image, correct_checksum=False, journal=Journal())
    assert events == []


@pytest.mark.parametrize("family", ["MS42", "MS43"])
def test_complete_fake_wire_write_then_readback_keeps_boot_unchanged(family):
    setup, target, _events = make_write(family)
    original_profile = protocol.PROFILES[family]
    memory = bytearray(target)
    memory[:0x10000] = b"\xA5" * 0x10000
    boot_before = bytes(memory[:0x10000])
    written = []
    def file_address(address):
        return address - 0x80000 if family == "MS43" and address >= 0x90000 else address
    def respond(command, args):
        if command == 6:
            address = file_address(int.from_bytes(args[:4], "big"))
            return reply(memory[address:address + args[4]])
        if command == 0x90:
            return reply(b"\x00")
        assert command == 7
        sub, address, count = args[0], int.from_bytes(args[1:4], "big"), args[4]
        if sub == 6:
            spans = (original_profile.program_write_regions
                     if address == original_profile.program_write_regions[0][0]
                     else ((address, original_profile.calibration_offset,
                            original_profile.calibration_write_length),))
            for _ecu, offset, length in spans:
                memory[offset:offset + length] = b"\xff" * length
        elif sub == 2:
            assert 0 < count <= 118 and len(args) == count + 5
            offset = file_address(address)
            memory[offset:offset + count] = args[5:]
            written.append((address, count))
        else:
            assert sub == 15
        return reply(b"\x00" * 5 + b"\x01")
    ecu = MS42MS43ECU(Link(respond), family)
    state = ecu.write_full(target, verify=True, correct_checksum=False, journal=Journal())
    assert state.completed and state.verified
    assert memory[:0x10000] == boot_before
    assert all(address >= 0x10000 for address, _count in written)
    for _address, offset, length in original_profile.program_write_regions:
        assert memory[offset:offset + length] == target[offset:offset + length]
    start = original_profile.calibration_offset
    length = original_profile.calibration_write_length
    assert memory[start:start + length] == target[start:start + length]


def test_full_to_tune_checksums_use_live_context_and_calibration_identity(monkeypatch):
    ecu, image, events = make_write("MS43")
    seen = []
    ecu.read_memory = lambda address, count: seen.append((address, count)) or bytes(count)
    def correct(data, family, context=None):
        assert len(data) == 0x10000
        assert data[0x20:0x28] == b"11143000"
        assert context == bytes(40)
        return bytes(data), []
    monkeypatch.setattr(protocol, "correct_checksums", correct)
    ecu.write_tune(image, journal=Journal())
    assert seen == [(0xEFDB8, 40)]


def test_partial_ms43_obtains_only_monitor_context_when_no_backup(monkeypatch):
    ecu, _image, events = make_write("MS43")
    image = bytearray(b"\xff" * 0x10000)
    image[8:14] = b"430069"
    image[0x20:0x28] = b"11143000"
    reads = []
    ecu.read_memory = lambda address, count: reads.append((address, count)) or bytes(count)
    def correct(data, family, context=None):
        assert context == bytes(40)
        return bytes(data), []
    monkeypatch.setattr(protocol, "correct_checksums", correct)
    ecu.write_tune(image, journal=Journal())
    assert reads == [(0xEFDB8, 40)]
    assert any(event[0] == 6 and event[1] == 0x70000 for event in events)


@pytest.mark.parametrize("family,index,frame_length,scale", [
    ("MS42", 23, 38, 0.10196078), ("MS43", 43, 45, 0.10156),
])
@pytest.mark.parametrize("method", ["read_voltage", "read_battery_voltage"])
@pytest.mark.parametrize("raw", [0, 128, 255])
def test_battery_voltage_uses_family_field_and_scale(family, index, frame_length, scale, method, raw):
    data = bytearray(frame_length - 4)
    data[25 - 3] = 17  # MS43 KL15 must not be reported as battery voltage.
    data[index - 3] = raw
    ecu = MS42MS43ECU(Link(lambda *_: reply(data)), family)
    assert getattr(ecu, method)() == pytest.approx(raw * scale)
    assert ecu.transport.calls == [(0x12, 0x0B, b"\x03")]


@pytest.mark.parametrize("frame_length", [27, 44])
@pytest.mark.parametrize("method", ["read_voltage", "read_battery_voltage"])
def test_ms43_battery_voltage_rejects_response_without_battery_byte(frame_length, method):
    # A valid shorter frame may contain KL15 or a checksum at index 43, not VB.
    ecu = MS42MS43ECU(Link(lambda *_: reply(bytes(frame_length - 4))), "MS43")
    with pytest.raises(DS2Error, match="Invalid MS42/MS43 response to 0x0B"):
        getattr(ecu, method)()
    assert ecu.transport.calls == [(0x12, 0x0B, b"\x03")]


def test_dtc_pages_and_raw_fields():
    raw = bytes.fromhex("13 57 34 12 78 56 BC 9A F0 DE")
    link = Link(lambda cmd, args: reply(bytes((11,)) + raw * (10 if args == b"\x01" else 1)))
    records = MS42MS43ECU(link, "MS42").read_dtcs()
    assert len(records) == 11
    assert records[0] == {"code": 0x13, "status": 0x57, "fields": (0x1234, 0x5678, 0x9ABC, 0xDEF0), "raw": raw}
    assert link.calls == [(0x12, 4, b"\x01"), (0x12, 4, b"\x0b")]


def test_dtc_rejects_partial_record_and_negative_clear_status():
    ecu = MS42MS43ECU(Link(lambda *_: reply(b"\x01\x00")), "MS43")
    with pytest.raises(DS2Error, match="incomplete ten-byte"):
        ecu.read_dtcs()
    ecu.transport.responder = lambda *_: reply(status=0xA2)
    with pytest.raises(DS2NegativeResponse):
        ecu.clear_dtcs()


@pytest.mark.parametrize("family,group,mask", [("MS42", "Height Correction", 16), ("MS43", "Throttle", 8)])
def test_adaptation_masks_and_empty_selection(family, group, mask):
    link = Link()
    ecu = MS42MS43ECU(link, family)
    with pytest.raises(ValueError, match="at least one"):
        ecu.clear_adaptations([])
    ecu.clear_adaptations([group, "Vanos", group])
    assert link.calls == [(0x12, 0x43, bytes((mask, 4)))]


def test_ews_sync_prepares_only_after_a2_and_reset_conditional_second_step():
    responses = iter((reply(status=0xA2), reply(b"\x00")))
    ecu = MS42MS43ECU(Link(lambda *_: next(responses)), "MS43")
    assert ecu.sync_ews()["code"] == 0
    assert ecu.transport.calls == [(0x12, 0x6D, b""), (0x44, 0x67, b""), (0x12, 0x6D, b"")]
    responses = iter((reply(b"\x02"), reply(b"\x00")))
    ecu.transport.calls.clear()
    assert ecu.reset_ews_start_value()["code"] == 0
    assert ecu.transport.calls == [(0x44, 0x67, b""), (0x12, 0x6C, b"\x01"), (0x12, 0x6C, b"\x00")]


def test_unsigned_packed_vin_and_invalid_digits():
    alphabet = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    vin = "WBA123456789ABCD0"
    value = 0
    for char in vin:
        value = value * 64 + alphabet.index(char)
    assert protocol.decode_vin(value.to_bytes(13, "big")) == vin
    assert protocol.decode_vin(bytes(13)) is None
    with pytest.raises(ValueError, match="alphabet"):
        protocol.decode_vin((63).to_bytes(13, "big"))
