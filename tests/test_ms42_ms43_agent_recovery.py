"""Bounded recovery through the real host and an independent fake byte pipe."""
import pytest

import ds2
import ms42_ms43 as protocol
import ms42_ms43_agent as agent
from tests.test_ms42_ms43 import Journal
from tests.test_ms42_ms43_agent import packet
from tests.test_ms42_ms43_agent_workflow import agent_workflow as agent_workflow, boot_target
from tests.test_ms42_ms43_ram_write import target_image, expected_array


def corrupt(reply):
    return reply[:-1] + bytes((reply[-1] ^ 1,))


@pytest.mark.parametrize("phase", ("entry", "retained"))
def test_identity_reply_noise_retries_same_permission_without_reupload(agent_workflow, monkeypatch, phase):
    ecu, peer = agent_workflow
    target = target_image(peer, "tune")
    if phase == "retained":
        peer.reject_next_program = True
        with pytest.raises(protocol.FlashInterrupted):
            with ecu.transfer():
                ecu.write_tune(target, use_ram=True, use_agent=True, journal=Journal())
    original = peer.agent_request
    failed = []

    def request(command, offset, count, data):
        reply = original(command, offset, count, data)
        if command == agent.IDENTITY and not failed:
            failed.append(offset)
            return corrupt(reply)
        return reply

    monkeypatch.setattr(peer, "agent_request", request)
    with ecu.transfer():
        state = (ecu.retry_write(journal=Journal()) if phase == "retained" else
                 ecu.write_tune(target, use_ram=True, use_agent=True, journal=Journal()))
    assert state.completed and failed == [0] and peer.agent_launches == 1
    assert peer.image == expected_array(peer, target, "tune")
    assert len([item for item in peer.agent_requests if item[0] == agent.IDENTITY]) == (3 if phase == "retained" else 2)


@pytest.mark.parametrize("fault", ("missing", "crc", "header", "short", 1, 3, 6))
def test_read_retries_same_range_without_duplicate_progress(agent_workflow, monkeypatch, fault):
    ecu, peer = agent_workflow
    original = peer.agent_request
    failed = []

    def request(command, offset, count, data):
        reply = original(command, offset, count, data)
        if command == agent.READ and not failed:
            failed.append(offset)
            if fault == "header":
                return bytes((reply[0] ^ 1,)) + reply[1:]
            return (packet(command, offset, count, status=fault) if isinstance(fault, int)
                    else b"" if fault == "missing" else corrupt(reply) if fault == "crc" else reply[:-1])
        return reply

    monkeypatch.setattr(peer, "agent_request", request)
    progress = []
    with ecu.read_transfer(use_agent=True) as reader:
        assert reader._read(0x80000, 2048, lambda *args: progress.append(args)) == peer.original[:2048]
    assert [item[1] for item in peer.agent_requests if item[0] == agent.READ] == [0, 0, 1024]
    assert [item[0] for item in progress] == [1024, 2048]
    assert not peer.erases and not peer.programs


@pytest.mark.parametrize("status", (2, 4, 5, 0xE0))
def test_valid_permanent_read_error_is_not_retried(agent_workflow, monkeypatch, status):
    ecu, peer = agent_workflow
    original = peer.agent_request
    monkeypatch.setattr(peer, "agent_request", lambda command, offset, count, data:
                        packet(command, offset, count, status=status) if command == agent.READ
                        else original(command, offset, count, data))
    with pytest.raises(ds2.DS2Error) as failed:
        with ecu.read_transfer(use_agent=True) as reader:
            reader._read(0x80000, 2)
    assert isinstance(failed.value.__cause__, agent.AgentRejected)
    assert len([item for item in peer.agent_requests if item[0] == agent.READ]) == 1


def test_read_retry_limit_is_three_and_leaves_protocol_owned(agent_workflow):
    ecu, peer = agent_workflow
    peer.agent_fault = "read_crc"
    with pytest.raises(ds2.DS2Error) as failed:
        with ecu.read_transfer(use_agent=True) as reader:
            reader._read(0x80000, 2)
    assert isinstance(failed.value.__cause__, ds2.DS2ChecksumError)
    assert len([item for item in peer.agent_requests if item[0] == agent.READ]) == 3
    assert not peer.restored and not peer.erases and not peer.programs


def test_error_drain_waits_after_late_bytes_and_preserves_timeout(agent_workflow, monkeypatch):
    ecu, peer = agent_workflow
    reader = agent.AgentReader(ecu)
    began = protocol.time.monotonic()
    arrivals = [began + 0.4, began + 0.9]

    def read(_count):
        if arrivals and protocol.time.monotonic() >= arrivals[0]:
            arrivals.pop(0)
            return b"late packet tail"
        return b""

    monkeypatch.setattr(peer, "read", read)
    peer.timeout = 7.25
    reader._drain_until_quiet()
    assert not arrivals and protocol.time.monotonic() >= began + 2.0
    assert peer.timeout == 7.25 and not peer.requests
    monkeypatch.setattr(peer, "read", lambda _count: b"noise")
    with pytest.raises(ds2.DS2Timeout, match="did not become quiet"):
        reader._drain_until_quiet()
    assert peer.timeout == 7.25 and not peer.requests


@pytest.mark.parametrize("fault", ("missing", "crc", "header", "partial", "not_received"))
def test_program_reconciles_actual_flash_before_any_replay(agent_workflow, monkeypatch, fault):
    ecu, peer = agent_workflow
    target, journal = target_image(peer, "tune"), Journal()
    original = peer.agent_request
    failed = []

    def request(command, offset, count, data):
        if command == agent.PROGRAM and peer.erases and not failed:
            failed.append((offset, data))
            if fault in ("partial", "not_received"):
                if fault == "partial":
                    peer.image[offset:offset + 2] = data[:2]
                return b""
            reply = original(command, offset, count, data)
            if fault == "header":
                return bytes((reply[0] ^ 1,)) + reply[1:]
            return b"" if fault == "missing" else corrupt(reply)
        return original(command, offset, count, data)

    monkeypatch.setattr(peer, "agent_request", request)
    with ecu.transfer():
        state = ecu.write_tune(target, use_ram=True, use_agent=True, verify=True, journal=journal)
    assert state.completed and peer.image == expected_array(peer, target, "tune")
    offset, data = failed[0]
    requests = peer.agent_requests
    first = next(i for i, item in enumerate(requests) if item == (agent.PROGRAM, offset, len(data), data))
    assert requests[first + 1][:3] == (agent.READ, offset, len(data))
    attempts = sum(item == requests[first] for item in requests)
    assert attempts == (1 if fault in ("missing", "crc", "header") else 2)
    assert len(peer.erases) == 1 and peer.agent_launches == 1


@pytest.mark.parametrize("committed", (False, True))
def test_erase_reconciles_entire_sector_before_retry(agent_workflow, monkeypatch, committed):
    ecu, peer = agent_workflow
    original = peer.agent_request
    failed = []

    def request(command, offset, count, data):
        if command == agent.ERASE and not failed:
            failed.append(offset)
            if committed:
                original(command, offset, count, data)
            return b""
        return original(command, offset, count, data)

    monkeypatch.setattr(peer, "agent_request", request)
    with ecu.transfer():
        state = ecu.write_tune(target_image(peer, "tune"), use_ram=True, use_agent=True, journal=Journal())
    assert state.completed
    requests = peer.agent_requests
    first = next(i for i, item in enumerate(requests) if item[0] == agent.ERASE)
    assert [item[1] for item in requests[first + 1:first + 65]] == list(range(failed[0], failed[0] + 0x10000, 1024))
    assert all(item[0] == agent.READ for item in requests[first + 1:first + 65])
    assert len([item for item in requests if item[0] == agent.ERASE]) == (1 if committed else 2)


@pytest.mark.parametrize("boot", (False, True))
def test_overcleared_bit_repairs_one_complete_frozen_sector(agent_workflow, monkeypatch, boot):
    ecu, peer = agent_workflow
    target = boot_target(peer) if boot else target_image(peer, "tune")
    original = peer.agent_request
    failed = []

    def request(command, offset, count, data):
        reply = original(command, offset, count, data)
        selected = offset < 0x10000 if boot else offset >= peer.layout.calibration_offset
        if command == agent.PROGRAM and peer.erases and selected and not failed:
            index = next(i for i, value in enumerate(data) if value)
            failed.append(offset + index)
            peer.image[offset + index] &= ~(data[index] & -data[index])
            return b""
        return reply

    monkeypatch.setattr(peer, "agent_request", request)
    journal = Journal()
    with ecu.transfer():
        state = (ecu.write_full(target, use_ram=True, use_agent=True, write_boot=True, verify=True, journal=journal)
                 if boot else ecu.write_tune(target, use_ram=True, use_agent=True, verify=True, journal=journal))
    assert state.completed and failed
    assert len([item for item in journal.events if item[0] == "sector_repair_requested"]) == 1
    assert len([item for item in journal.events if item[0] == "sector_repair_verified"]) == 1
    expected = expected_array(peer, target, "full" if boot else "tune")
    if boot:
        expected[:0x4000], expected[0x8000:0x10000] = target[:0x4000], target[0x8000:0x10000]
    assert peer.image == expected
    assert peer.image[0x4000:0x8000] == peer.original[0x4000:0x8000]


def test_definitive_flash_failure_retains_same_target_without_replay(agent_workflow, monkeypatch):
    ecu, peer = agent_workflow
    original = peer.agent_request
    failed = []

    def request(command, offset, count, data):
        if command == agent.PROGRAM and peer.erases:
            failed.append(offset)
            return packet(command, offset, count, status=4)
        return original(command, offset, count, data)

    monkeypatch.setattr(peer, "agent_request", request)
    target = target_image(peer, "tune")
    with pytest.raises(protocol.FlashInterrupted):
        with ecu.transfer():
            ecu.write_tune(target, use_ram=True, use_agent=True, journal=Journal())
    assert len(failed) == 1 and ecu._pending_image == target and ecu.can_retry_write
    assert not peer.restored and not peer.agent_finalizations


@pytest.mark.parametrize("command", (agent.PROGRAM, agent.ERASE))
def test_uncommitted_mutation_retries_are_bounded(agent_workflow, monkeypatch, command):
    ecu, peer = agent_workflow
    original = peer.agent_request
    failures = []

    def request(actual, offset, count, data):
        if actual == command and (actual == agent.ERASE or peer.erases):
            failures.append(offset)
            return b""
        return original(actual, offset, count, data)

    monkeypatch.setattr(peer, "agent_request", request)
    with pytest.raises(protocol.FlashInterrupted):
        with ecu.transfer():
            ecu.write_tune(target_image(peer, "tune"), use_ram=True, use_agent=True, journal=Journal())
    assert len(failures) == 3 and ecu.can_retry_write and not peer.restored
    assert not peer.agent_finalizations


def test_second_overcleared_bit_stops_after_one_sector_repair(agent_workflow, monkeypatch):
    ecu, peer = agent_workflow
    original = peer.agent_request
    failed = []

    def request(command, offset, count, data):
        reply = original(command, offset, count, data)
        if command == agent.PROGRAM and peer.erases and len(failed) < 2:
            index = next(i for i, value in enumerate(data) if value)
            failed.append(offset + index)
            peer.image[offset + index] &= ~(data[index] & -data[index])
            return b""
        return reply

    monkeypatch.setattr(peer, "agent_request", request)
    target, journal = target_image(peer, "tune"), Journal()
    with pytest.raises(protocol.FlashInterrupted, match="no complete sector repair remains"):
        with ecu.transfer():
            ecu.write_tune(target, use_ram=True, use_agent=True, journal=journal)
    assert len(failed) == 2 and len(peer.erases) == 2
    assert ecu._pending_image == target and ecu.can_retry_write
    assert not peer.restored and not peer.agent_finalizations


def test_standalone_identity_transaction_still_owns_lost_write_ack(agent_workflow, monkeypatch):
    ecu, peer = agent_workflow
    original = peer.agent_request

    def request(command, offset, count, data):
        reply = original(command, offset, count, data)
        return b"" if command == agent.PROGRAM else reply

    monkeypatch.setattr(peer, "agent_request", request)
    with pytest.raises(ds2.DS2Error) as failed:
        with ecu.read_transfer(use_agent=True) as reader:
            writer = agent.AgentWriter(ecu, reader.identity)
            state = protocol.WriteState(ecu.family, "identity")
            writer._program(0x10000, b"\0\0", Journal(), state)
    assert isinstance(failed.value.__cause__, ds2.DS2Timeout)
    assert [item[0] for item in peer.agent_requests] == [agent.IDENTITY, agent.PROGRAM]
