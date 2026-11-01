"""Resident write rejection reporting over fake ports only."""
import pytest

from ds2 import DS2Error, DS2Timeout, _xor
import ms42_ms43 as protocol
from ms42_ms43_resident import ResidentRejected, ResidentWriter
from tests.test_ms42_ms43 import Journal
from tests.test_ms42_ms43_ram_write import (
    COMPATIBLE_MS43_FLASH_IDS, target_image, write_workflow as write_workflow,
)
from tests.test_ms42_ms43_ram_workflow import response
from tests.test_ms42_ms43_resident import reader as reader


@pytest.mark.parametrize("mode", ("tune", "full"))
@pytest.mark.parametrize("failure", ("manufacturer", "lost_reply"))
def test_first_marker_failure_reports_exact_phase_and_retains_without_replay(
        write_workflow, monkeypatch, mode, failure):
    ecu, peer = write_workflow
    # Already-populated backups make the marker the first program request.
    for key in ("primary", "secondary"):
        start = peer.contract[key]
        peer.image[start:start + 26] = bytes(26)
    before = bytes(peer.image)
    region = "calibration" if mode == "tune" else "program"
    marker = peer.contract[region + "_marker"]
    command = 0x74 if peer.family == "MS42" else 0x7A
    arguments = (b"" if peer.family == "MS42" else b"\x02") + marker.to_bytes(3, "big") + bytes(4)
    original_loader = peer.loader
    attempts = []

    def fail_marker(operation, args):
        if operation == command:
            attempts.append((operation, args))
            assert args == arguments
            assert not peer.erases and not peer.programs
            if failure == "manufacturer":
                return response(stock=False, status=0xD0)
            original_loader(operation, args)  # Commit, then lose its acknowledgement.
            return b""
        return original_loader(operation, args)

    monkeypatch.setattr(peer, "loader", fail_marker)
    target, journal = target_image(peer, mode), Journal()
    with pytest.raises(protocol.FlashInterrupted) as caught:
        with ecu.transfer():
            getattr(ecu, "write_" + mode)(target, use_ram=True, journal=journal)

    error, state = caught.value.original_error, caught.value.state
    phase = "invalidate " + region + " marker"
    assert state.phase == phase and state.address == marker
    assert phase in str(caught.value)
    assert state.bytes_written == 0 and not state.completed and not state.finalized
    assert caught.value.completion_unknown
    assert attempts == [(command, arguments)]
    assert not peer.erases and not peer.finalizations and not peer.restored
    assert peer.mode == "resident" and ecu.transport._ser is peer
    assert ecu._pending_image is not None and ecu._pending_writer.active
    assert ecu.can_retry_write
    assert [(event, fields) for event, fields in journal.events
            if event not in ("finished", "resident_flash_identified")] == [
        ("program_requested", {"address": marker, "length": 4})]
    identifications = [fields for event, fields in journal.events if event == "resident_flash_identified"]
    assert identifications == ([{"command": command, "manufacturer": 1, "device": 0x22AB}]
                               if peer.family == "MS43" else [])
    event, final = journal.events[-1]
    assert journal.closed and event == "finished" and final["outcome"] == "commit_unknown"
    assert final["phase"] == phase and final["address"] == marker
    assert final["error"] == str(error) and final["error_type"] == type(error).__name__
    if failure == "manufacturer":
        assert isinstance(error, ResidentRejected)
        assert (error.command, error.status) == (command, 0xD0)
        assert "manufacturer check failed" in str(error)
        assert (final["command"], final["status"]) == (command, 0xD0)
        assert not peer.programs and bytes(peer.image) == before
    else:
        assert isinstance(error, DS2Timeout)
        assert (final["command"], final["status"]) == (None, None)
        expected = bytearray(before)
        expected[marker:marker + 4] = bytes(4)
        assert bytes(peer.image) == expected
        assert peer.programs == [(marker, bytes(4))]

    count = len(peer.requests)
    with pytest.raises(DS2Error, match="incomplete.*retained"):
        getattr(ecu, "write_" + mode)(target, use_ram=True, journal=Journal())
    assert len(peer.requests) == count and attempts == [(command, arguments)]


@pytest.mark.parametrize("status", (0xA0, 0xD0))
def test_wrong_resident_reply_address_is_not_a_device_rejection(reader, monkeypatch, status):
    port = reader.transport._ser
    original_write = port.write

    def wrong_address(message):
        written = original_write(message)
        reply = bytes((3, 2, status))
        port.pending = message + reply + bytes((_xor(reply),))
        return written

    monkeypatch.setattr(port, "write", wrong_address)
    with pytest.raises(DS2Error, match="response address") as caught:
        reader._exchange(0x02)
    assert not isinstance(caught.value, ResidentRejected)
    assert len(port.requests) == 1


@pytest.mark.parametrize("write_workflow", ["MS43"], indirect=True)
@pytest.mark.parametrize("mode", ("tune", "full"))
@pytest.mark.parametrize("fault", ("readback", "checksum"))
def test_ms43_setup_failure_before_flash_restores_diagnostics_without_fallback(
        write_workflow, mode, fault):
    ecu, peer = write_workflow
    peer.writer_fault = fault
    before, journal = bytes(peer.image), Journal()
    with pytest.raises(protocol.FlashInterrupted) as caught:
        with ecu.transfer():
            getattr(ecu, "write_" + mode)(target_image(peer, mode), use_ram=True, journal=journal)

    error, state = caught.value.original_error, caught.value.state
    assert state.phase == "prepare RAM flash access"
    assert not state.destructive_started and not caught.value.completion_unknown
    assert not state.completed and state.bytes_written == 0
    assert bytes(peer.image) == before and not peer.erases and not peer.programs
    assert not any(wire == "resident" and command in (0x73, 0x74, 0x7A)
                   for wire, command, _args in peer.requests)
    assert not any(wire == "ds2" and command == 7 and args[0] != 0
                   for wire, command, args in peer.requests)
    assert peer.restored and peer.mode == "ds2" and ecu.rate_confirmed
    assert (peer.baudrate, peer.parity, peer.stopbits) == (9600, "E", 1)
    assert ecu.transport._ser is peer and not peer.finalizations
    assert ecu._pending_image is None and ecu._pending_writer is None and not ecu.can_retry_write
    final = journal.events[-1][1]
    assert journal.closed and final["outcome"] == "aborted"
    assert final["phase"] == state.phase and final["error"] == str(error)
    assert not any(event in ("program_requested", "program_accepted", "erase_requested", "erase_accepted")
                   for event, _fields in journal.events)
    if fault == "checksum":
        assert isinstance(error, ResidentRejected)
        assert (error.command, error.status) == (0x79, 0xB6)
        assert (final["command"], final["status"]) == (0x79, 0xB6)
    else:
        assert "readback verification" in str(error) and not peer.writer_installs


@pytest.mark.parametrize("write_workflow", ["MS43"], indirect=True)
@pytest.mark.parametrize("fault", ("readback", "checksum"))
def test_ms43_reinstallation_failure_after_marker_commit_keeps_resident_session(
        write_workflow, monkeypatch, fault):
    ecu, peer = write_workflow
    peer.flash_manufacturer, peer.flash_device = 0x20, 0xD6
    for key in ("primary", "secondary"):
        start = peer.contract[key]
        peer.image[start:start + 26] = bytes(26)
    before, journal = bytes(peer.image), Journal()
    original_loader = peer.loader

    def fail_after_marker(command, args):
        reply = original_loader(command, args)
        if command == 0x7A:
            assert len(peer.programs) == 1
            peer.writer_fault = fault
        return reply

    monkeypatch.setattr(peer, "loader", fail_after_marker)
    target = target_image(peer, "tune")
    with pytest.raises(protocol.FlashInterrupted) as caught:
        with ecu.transfer():
            ecu.write_tune(target, use_ram=True, journal=journal)
    marker = peer.contract["calibration_marker"]
    expected = bytearray(before)
    expected[marker:marker + 4] = bytes(4)
    assert bytes(peer.image) == expected and peer.programs == [(marker, bytes(4))]
    assert not peer.erases and not peer.finalizations and not peer.restored
    assert not any(wire == "resident" and command == 0x73 for wire, command, _args in peer.requests)
    assert ("resident", 0x78, bytes.fromhex("0000B748B7B7")) not in peer.requests
    assert peer.mode == "resident" and ecu.transport._ser is peer
    assert ecu._pending_writer.active and ecu._pending_image is not None and ecu.can_retry_write
    assert caught.value.completion_unknown and caught.value.state.bytes_written == 4
    assert caught.value.state.phase == "prepare RAM flash access"
    assert journal.closed and journal.events[-1][1]["outcome"] == "commit_unknown"
    count = len(peer.requests)
    with pytest.raises(DS2Error, match="incomplete.*retained"):
        ecu.write_tune(target, use_ram=True, journal=Journal())
    assert len(peer.requests) == count


@pytest.mark.parametrize("write_workflow", ["MS43"], indirect=True)
@pytest.mark.parametrize("manufacturer,device", COMPATIBLE_MS43_FLASH_IDS)
def test_ms43_program_batch_reuses_overlay_until_read_probe_or_command_change(
        write_workflow, manufacturer, device):
    ecu, peer = write_workflow
    peer.flash_manufacturer, peer.flash_device = manufacturer, device
    peer.image[0x70000:0x71000] = b"\xff" * 0x1000
    writer, journal = ResidentWriter(ecu), Journal()
    state = protocol.WriteState("MS43", "tune", phase="program calibration")
    writer.enter()
    for offset in (0x70000, 0x700F8, 0x701F0):
        writer._program(offset, b"\x12\x34" * 124, journal, state)
    assert peer.writer_installs == [0x7A] and len(peer.programs) == 3

    assert writer._bytes(0x70000, 2) == b"\x12\x34"
    assert writer._write_overlay is None and peer.write_overlay is None
    writer._program(0x70400, b"\x56\x78", journal, state)
    assert peer.writer_installs == [0x7A, 0x7A]
    assert writer._exchange(2, count=12) == writer.profile["identity"]
    assert writer._write_overlay is None and peer.write_overlay is None
    writer._program(0x70402, b"\x9A\xBC", journal, state)
    assert peer.writer_installs == [0x7A, 0x7A, 0x7A]

    writer._erase(10, journal, state)
    writer._program(0x70000, b"\xDE\xF0", journal, state)
    assert peer.writer_installs == [0x7A, 0x7A, 0x7A, 0x73, 0x7A]
    assert peer.image[0x70000:0x70002] == b"\xDE\xF0"
    identified = [fields for event, fields in journal.events if event == "resident_flash_identified"]
    assert identified == [{"command": command, "manufacturer": manufacturer, "device": device}
                          for command in peer.writer_installs]


@pytest.mark.parametrize("write_workflow", ["MS43"], indirect=True)
@pytest.mark.parametrize("manufacturer,device", COMPATIBLE_MS43_FLASH_IDS)
def test_ms43_explicit_retry_invalidates_cached_writer_at_identity_probe(
        write_workflow, monkeypatch, manufacturer, device):
    ecu, peer = write_workflow
    peer.flash_manufacturer, peer.flash_device = manufacturer, device
    peer.drop_next_program = True
    with pytest.raises(protocol.FlashInterrupted):
        with ecu.transfer():
            ecu.write_tune(target_image(peer, "tune"), use_ram=True, journal=Journal())
    writer = ecu._pending_writer
    assert writer.active and writer._write_overlay == 0x7A
    count, installs = len(peer.requests), len(peer.writer_installs)
    original_exchange, cache_after_probe = writer._exchange, []

    def observe_probe(command, *args, **kwargs):
        result = original_exchange(command, *args, **kwargs)
        if command == 2:
            cache_after_probe.append(writer._write_overlay)
        return result

    monkeypatch.setattr(writer, "_exchange", observe_probe)
    state = ecu.retry_write(journal=Journal())
    assert peer.requests[count] == ("resident", 2, b"")
    assert cache_after_probe == [None]
    assert len(peer.writer_installs) > installs
    assert state.completed and state.finalized and peer.restored
    assert ecu._pending_writer is None and ecu._pending_image is None
