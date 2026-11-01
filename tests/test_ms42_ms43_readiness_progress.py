"""Observe real write-tail orchestration over injected peers; no hardware.

The virtual clock tests host waiting and feedback, not native scheduler timing.
"""
from types import SimpleNamespace

import pytest

import ds2
import ms42_ms43 as protocol
from tests.test_ms42_ms43 import Journal, Link, reply
from tests.test_ms42_ms43_agent_workflow import agent_workflow as agent_workflow
from tests.test_ms42_ms43_ram_workflow import response
from tests.test_ms42_ms43_ram_write import target_image, write_workflow as write_workflow


TAIL_PHASES = (
    ("restoring diagnostics", "restore diagnostics"),
    ("checking firmware", "firmware validation"),
    ("waiting for authorization", "authorization readiness"),
    ("finalizing calibration", "finalize calibration"),
    ("checking completion markers", "completion markers"),
)


@pytest.fixture
def readiness_clock(monkeypatch):
    clock = SimpleNamespace(now=0.0, waits=[])

    def sleep(delay):
        clock.waits.append(delay)
        clock.now += delay

    monkeypatch.setattr(protocol.time, "monotonic", lambda: clock.now)
    monkeypatch.setattr(protocol.time, "sleep", sleep)
    return clock


@pytest.mark.parametrize("family,address", (("MS42", 0xF736), ("MS43", 0xF6B0)))
@pytest.mark.parametrize("phase,payload", (("RAM challenge", b"\xFC\0\0\0"),
                                          ("RAM key", b"\x11\x22\x33\x44"),
                                          ("authorization", b"BMW\x03")))
def test_clean_busy_polls_only_one_readiness_byte_before_same_authorization(
        family, address, phase, payload, readiness_clock):
    requests = []

    def respond(command, args):
        requests.append((command, args, readiness_clock.now))
        if command == 0x06:
            assert args == address.to_bytes(4, "big") + b"\x01"
            return reply(bytes((4 if readiness_clock.now >= 1.0 else 1,)))
        assert command == 0x90 and args == payload
        if len([row for row in requests if row[0] == 0x90]) == 1:
            return reply(status=0xA1)
        assert readiness_clock.now >= 1.0
        return reply(b"\x00")

    ecu = protocol.MS42MS43ECU(Link(respond), family)
    assert ecu._authorization_frame(payload, phase=phase, readiness_address=address) == reply(b"\x00")
    assert [row[:2] for row in requests if row[0] == 0x90] == [(0x90, payload)] * 2
    assert [row[0] for row in requests[1:-1]] == [0x06] * 5
    assert readiness_clock.now == pytest.approx(1.0)


@pytest.mark.parametrize("family,address", (("MS42", 0xF736), ("MS43", 0xF6B0)))
@pytest.mark.parametrize("lost", ("first_authorization", "readiness", "second_authorization"))
def test_only_read_only_readiness_timeout_can_retry(family, address, lost, readiness_clock):
    counts = {0x90: 0, 0x06: 0}

    def respond(command, args):
        counts[command] += 1
        if command == 0x06:
            assert args == address.to_bytes(4, "big") + b"\x01"
            if lost == "readiness" and counts[command] == 1:
                raise ds2.DS2Timeout("injected read-only timeout")
            return reply(b"\x04")
        assert command == 0x90 and args == b"\xFC\0\0\0"
        if (lost == "first_authorization" and counts[command] == 1
                or lost == "second_authorization" and counts[command] == 2):
            raise ds2.DS2Timeout("injected stateful timeout")
        return reply(status=0xA1) if counts[command] == 1 else reply(b"\x00")

    ecu = protocol.MS42MS43ECU(Link(respond), family)
    if lost == "readiness":
        assert ecu._authorization_frame(b"\xFC\0\0\0", phase="RAM challenge",
                                        readiness_address=address) == reply(b"\x00")
        assert counts == {0x90: 2, 0x06: 2}
    else:
        with pytest.raises(ds2.DS2Timeout, match="stateful"):
            ecu._authorization_frame(b"\xFC\0\0\0", phase="RAM challenge", readiness_address=address)
        assert counts == ({0x90: 1, 0x06: 0} if lost == "first_authorization" else {0x90: 2, 0x06: 1})


@pytest.mark.parametrize("family,address", (("MS42", 0xF736), ("MS43", 0xF6B0)))
def test_busy_readiness_deadline_does_not_send_another_stateful_request(family, address, readiness_clock):
    link = Link(lambda command, _args: reply(status=0xA1) if command == 0x90 else reply(b"\x01"))
    ecu = protocol.MS42MS43ECU(link, family)
    with pytest.raises(protocol.AuthorizationBusyError):
        ecu._authorization_frame(b"\xFC\0\0\0", phase="RAM challenge", readiness_address=address)
    assert readiness_clock.now == pytest.approx(15.0)
    assert sum(command == 0x90 for _module, command, _args in link.calls) == 1
    assert all(args == address.to_bytes(4, "big") + b"\x01"
               for _module, command, args in link.calls if command == 0x06)


@pytest.mark.parametrize("family,address", (("MS42", 0xF736), ("MS43", 0xF6B0)))
def test_scoped_a2_polls_readiness_but_does_not_retry_a_second_a2(readiness_clock, family, address):
    link = Link(lambda command, _args: reply(status=0xA2) if command == 0x90 else reply(b"\x04"))
    ecu = protocol.MS42MS43ECU(link, family)
    with pytest.raises(ds2.DS2NegativeResponse) as raised:
        ecu._authorization_frame(b"\xFC\0\0\0", phase="RAM challenge", readiness_address=address)
    assert raised.value.status == 0xA2
    assert [command for _module, command, _args in link.calls] == [0x90, 0x06, 0x90]


@pytest.mark.parametrize("family,address", (("MS42", 0xF736), ("MS43", 0xF6B0)))
def test_ready_authorization_sends_no_readiness_probe_or_delay(family, address, readiness_clock):
    link = Link(lambda *_: reply(b"\x00"))
    ecu = protocol.MS42MS43ECU(link, family)
    assert ecu._authorization_frame(b"\xFC\0\0\0", phase="RAM challenge",
                                    readiness_address=address) == reply(b"\x00")
    assert link.calls == [(0x12, 0x90, b"\xFC\0\0\0")]
    assert readiness_clock.waits == []


@pytest.mark.parametrize("family,address", (("MS42", 0xF736), ("MS43", 0xF6B0)))
@pytest.mark.parametrize("fault", ("length", "negative", "checksum"))
def test_malformed_readiness_is_not_treated_as_busy(family, address, fault, readiness_clock):
    def respond(command, _args):
        if command == 0x90:
            return reply(status=0xA1)
        if fault == "checksum":
            raise ds2.DS2ChecksumError("bad readiness reply checksum")
        return reply() if fault == "length" else reply(status=0xB0)

    link = Link(respond)
    ecu = protocol.MS42MS43ECU(link, family)
    with pytest.raises(ds2.DS2Error):
        ecu._authorization_frame(b"\xFC\0\0\0", phase="RAM challenge", readiness_address=address)
    assert [command for _module, command, _args in link.calls] == [0x90, 0x06]


def test_initial_busy_read_avoids_a_missing_reply_from_premature_challenge(agent_workflow, monkeypatch):
    ecu, peer = agent_workflow
    initial_ready_at = peer.ready_at = protocol.time.monotonic() + 1.4
    stock = peer.stock
    challenges, premature = [], []

    def respond(command, args):
        if command == 0x90 and args == b"\xFC\0\0\0":
            challenges.append(protocol.time.monotonic())
            if len(challenges) > 1 and protocol.time.monotonic() < peer.ready_at:
                premature.append(bytes(args))
                return b""  # Supplied fault, not a claim about the physical cause.
        return stock(command, args)

    monkeypatch.setattr(peer, "stock", respond)
    with ecu.read_transfer(use_agent=True) as reader:
        result = reader.read_tune()
    assert len(challenges) == 2 and not premature
    assert challenges[0] < initial_ready_at <= challenges[1]
    assert result == peer.original[peer.layout.calibration_offset:
                                   peer.layout.calibration_offset + peer.layout.partial_size]
    assert peer.restored and ecu.rate_confirmed


def test_full_software_change_does_not_poll_original_ram_state_after_reset(agent_workflow, monkeypatch):
    ecu, peer = agent_workflow
    target = bytearray(target_image(peer, "full"))
    software = b"0110FF" if ecu.family == "MS42" else b"430068"
    target[peer.layout.sw_offset:peer.layout.sw_offset + 6] = software
    target[peer.layout.calibration_offset + 8:peer.layout.calibration_offset + 14] = software
    peer.ready_at = protocol.time.monotonic() + 0.4
    peer.authorization_delay = 0.8
    observed, journal = observe(ecu, peer, monkeypatch), Journal()
    with ecu.transfer():
        state = ecu.write_full(target, use_ram=True, use_agent=True, journal=journal,
                               progress_cb=observed.progress)
    address = {"MS42": 0xF736, "MS43": 0xF6B0}[ecu.family]
    readiness_args = address.to_bytes(4, "big") + b"\x01"
    assert any(mode == "ds2" and command == 0x06 and args == readiness_args
               for mode, command, args in peer.requests)
    assert not any(event[0] == "request" and event[1:] == (0x06, readiness_args)
                   for event in observed.events)
    authorization = [event for event in observed.events if event[0] == "request" and event[1] == 0x90]
    assert len(authorization) >= 2 and len({event[2] for event in authorization}) == 1
    assert all(event[2].startswith(b"BMW") for event in authorization)
    assert state.completed and state.finalized and journal.events[-1][1]["outcome"] == "success"


def observe(ecu, peer, monkeypatch):
    result = SimpleNamespace(events=[], sleeps=[], logs=[])
    sleep = protocol.time.sleep
    stock = peer.stock
    read_memory = ecu.read_memory

    def wait(delay):
        result.sleeps.append((protocol.time.monotonic(), delay, peer.restored))
        sleep(delay)

    def request(command, args):
        if peer.restored:
            result.events.append(("request", command, bytes(args)))
        return stock(command, args)

    def read(address, count, *args, **kwargs):
        data = read_memory(address, count, *args, **kwargs)
        if peer.finalizations and count == 4:
            result.events.append(("marker", address, data))
        return data

    def progress(done, total, label):
        state = ecu.last_write
        result.events.append(("progress", label, done, total,
                              state.phase, state.completed, state.finalized))

    result.progress = progress
    monkeypatch.setattr(protocol.time, "sleep", wait)
    monkeypatch.setattr(peer, "stock", request)
    monkeypatch.setattr(ecu, "read_memory", read)
    return result


def write_tune(ecu, peer, observed, journal, *, use_agent=False):
    with ecu.transfer():
        return ecu.write_tune(target_image(peer, "tune"), use_ram=True, use_agent=use_agent,
                              journal=journal, progress_cb=observed.progress,
                              log_fn=lambda message, *_: observed.logs.append(message))


def assert_no_success(ecu, journal, observed):
    assert not ecu.last_write.completed and not ecu.last_write.finalized
    assert journal.events[-1][1]["outcome"] != "success"
    assert not any("write complete" in text.casefold() for text in observed.logs)
    assert not any(event[0] == "progress" and event[5] for event in observed.events)


def check_complete_tail(workflow, monkeypatch, mode, *, use_agent):
    ecu, peer = workflow
    observed, journal = observe(ecu, peer, monkeypatch), Journal()
    original_progress = observed.progress
    completion_observations = []

    def progress(done, total, label):
        if label == f"{ecu.family} write complete":
            completion_observations.append((journal.closed, journal.events[-1],
                                            list(observed.events)))
        original_progress(done, total, label)

    target = target_image(peer, mode)
    if use_agent:
        # Unknown target software retains the existing stock finalization path.
        assert mode == "full"
        target = bytearray(target)
        software = b"0110FF" if ecu.family == "MS42" else b"430068"
        target[peer.layout.sw_offset:peer.layout.sw_offset + 6] = software
        target[peer.layout.calibration_offset + 8:peer.layout.calibration_offset + 14] = software
    with ecu.transfer():
        state = getattr(ecu, "write_" + mode)(
            target, use_ram=True, use_agent=use_agent,
            journal=journal, progress_cb=progress)

    expected_labels = [f"{ecu.family} {label}" for label, _phase in TAIL_PHASES]
    tail = [event for event in observed.events if event[0] == "progress"
            and event[1] in expected_labels]
    assert [event[1] for event in tail] == expected_labels
    assert [event[4] for event in tail] == [phase for _label, phase in TAIL_PHASES]
    assert all(event[2:4] == (0, 0) and not event[5] and not event[6] for event in tail)
    assert [fields["phase"] for event, fields in journal.events if event == "phase_started"] == [
        phase for _label, phase in TAIL_PHASES]

    assert len(completion_observations) == 1
    closed, finish, events_before_complete = completion_observations[0]
    assert closed and finish == journal.events[-1] and finish[1]["outcome"] == "success"
    markers = [(event[1], event[2]) for event in events_before_complete if event[0] == "marker"]
    program = peer.contract["program_marker"] + (0x80000 if ecu.family == "MS43" else 0)
    assert markers == [(program, bytes.fromhex("90 48 24 42")),
                       (peer.contract["calibration_marker"], bytes.fromhex("42 24 48 90"))]
    total = ecu.profile.calibration_write_length + (
        sum(length for _address, _offset, length in ecu.profile.program_write_regions) if mode == "full" else 0)
    assert observed.events[-1] == ("progress", f"{ecu.family} write complete", total, total,
                                   "complete", True, True)
    assert state.completed and state.finalized


def test_other_software_agent_tail_keeps_stock_completion_feedback(agent_workflow, monkeypatch):
    check_complete_tail(agent_workflow, monkeypatch, "full", use_agent=True)


@pytest.mark.parametrize("mode", ("full", "tune"))
def test_existing_resident_write_keeps_finalization_with_phase_feedback(write_workflow, monkeypatch, mode):
    check_complete_tail(write_workflow, monkeypatch, mode, use_agent=False)


@pytest.mark.parametrize("fault", ("checksum", "finalization", "program_marker", "calibration_marker"))
def test_failed_resident_write_tail_never_reports_completion(write_workflow, monkeypatch, fault):
    ecu, peer = write_workflow
    if fault == "checksum":
        peer.validation_mask = 1
    elif fault == "finalization":
        peer.finalize_result = 0
    else:
        peer.missing_marker = fault.removesuffix("_marker")
    observed, journal = observe(ecu, peer, monkeypatch), Journal()
    with pytest.raises(protocol.FlashInterrupted):
        write_tune(ecu, peer, observed, journal)
    assert_no_success(ecu, journal, observed)


def test_lost_postwrite_authorization_reply_is_not_resent_or_reported_complete(write_workflow, monkeypatch):
    ecu, peer = write_workflow
    stock = peer.stock
    authorization = []

    def lose_authorization(command, args):
        if peer.restored and command == 0x90:
            authorization.append(bytes(args))
            return b""
        return stock(command, args)

    monkeypatch.setattr(peer, "stock", lose_authorization)
    observed, journal = observe(ecu, peer, monkeypatch), Journal()
    with pytest.raises(protocol.FlashInterrupted):
        write_tune(ecu, peer, observed, journal)
    assert len(authorization) == 1
    assert not peer.finalizations
    assert_no_success(ecu, journal, observed)


def test_ready_write_tail_has_no_fixed_startup_delay(agent_workflow, monkeypatch):
    ecu, peer = agent_workflow
    observed, journal = observe(ecu, peer, monkeypatch), Journal()
    state = write_tune(ecu, peer, observed, journal, use_agent=True)
    assert state.completed and state.finalized
    tail_sleeps = [delay for _at, delay, restored in observed.sleeps if restored]
    assert tail_sleeps and max(tail_sleeps) <= 0.2
    assert sum(tail_sleeps) < 1.0


def test_checksum_busy_wait_is_bounded_polling_and_never_skips_validation(write_workflow, monkeypatch):
    ecu, peer = write_workflow
    peer.validation_busy = 3
    observed, journal = observe(ecu, peer, monkeypatch), Journal()
    state = write_tune(ecu, peer, observed, journal)
    requests = [event for event in observed.events if event[0] == "request"]
    validations = [event for event in requests if event[1] == 0x0A]
    assert len(validations) == 4
    assert max(i for i, event in enumerate(requests) if event[1] == 0x0A) < next(
        i for i, event in enumerate(requests) if event[1] == 0x90)
    assert state.completed and peer.validation_busy == 0


def test_postwrite_checksum_timeout_does_not_reach_authorization_or_success(write_workflow, monkeypatch):
    ecu, peer = write_workflow
    stock = peer.stock

    def always_busy(command, args):
        return response(status=0xA1) if command == 0x0A else stock(command, args)

    monkeypatch.setattr(peer, "stock", always_busy)
    observed, journal = observe(ecu, peer, monkeypatch), Journal()
    with pytest.raises(protocol.FlashInterrupted) as raised:
        write_tune(ecu, peer, observed, journal)
    assert isinstance(raised.value.__cause__, ds2.DS2Timeout)
    assert not any(event[0] == "request" and event[1] == 0x90 for event in observed.events)
    assert not peer.finalizations
    assert_no_success(ecu, journal, observed)
