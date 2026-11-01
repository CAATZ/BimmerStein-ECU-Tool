"""Host workflows for exact DATEN apps on independently identified boot code.

The fixture supplies a completed-flash marker state, not a claimed factory ROM.
Actual application entry/finalization are executed by the canonical emulator.
"""
from types import SimpleNamespace

import pytest

import ms42_ms43_agent as agent
import ms42_ms43_image as images
import ms42_ms43_resident as resident
from ds2 import DS2Timeout
from tests.ms42_ms43_revision_sources import composed_image
from tests.test_ms42_ms43 import Journal
from tests.test_ms42_ms43_agent_workflow import agent_workflow
from tests.test_ms42_ms43_compatibility_baud import compatibility as compatibility
from tests.test_ms42_ms43_ram_write import target_image, expected_array
from tests import test_ms42_ms43_storage_workflow as storage_tests
from tests import test_ms42_ms43_agent_workflow as recovery_tests


COMBINATIONS = [("0110C7", "0110C6"), ("0110CA", "0110C6"),
                *((software, boot) for software in
                  ("430037", "430064", "430065", "430066", "430066-later", "430069-later")
                  for boot in ("430055", "430069"))]


@pytest.fixture(params=COMBINATIONS, ids=lambda item: f"program-{item[0]}-boot-{item[1]}")
def workflow(request, monkeypatch):
    try:
        source = composed_image(*request.param)
    except FileNotFoundError:
        pytest.skip("private exact boot/DATEN sources unavailable")
    ecu, peer = agent_workflow.__wrapped__(SimpleNamespace(param=source.family), monkeypatch)
    image = bytearray(source.image)
    program, calibration = ((0x50000, 0x4FFF0) if source.family == "MS42" else (0x6FFF0, 0x7FFF0))
    for offset, marker in ((program, bytes.fromhex("90482442")), (calibration, bytes.fromhex("42244890"))):
        assert image[offset:offset + 4] in (b"\xff" * 4, marker)
        image[offset:offset + 4] = marker
    peer.original = bytes(image)
    assert all(images.checksum_status(peer.original)[group] for group in ("boot", "program", "cal"))
    peer.image[:] = peer.original
    peer.profile = next(profile for profile in resident.BOOT_PROFILES[source.family]
                        if profile["software"] == source.boot_software)
    return ecu, peer


def assert_preflight_precedes_authorization(requests):
    commands = [(command, args) for _, command, args in requests]
    setup = b"\x01\x08" + b"".join(
        b"\x00" + address.to_bytes(4, "big") for address in range(0x3F5C, 0x3F64))
    measurement = ((0x0B, setup), (0x0B, b"\x00"), (0x0B, b"\xFF"))
    assert all(commands.count(request) == 1 for request in measurement)
    setup_at, read_at, cleanup_at = (commands.index(request) for request in measurement)
    authorization_at = next(index for index, (command, _) in enumerate(commands) if command == 0x90)
    assert setup_at < read_at < cleanup_at < authorization_at


@pytest.mark.parametrize("mode", ["tune", "full"])
def test_exact_revision_read_and_write(workflow, compatibility, mode):
    ecu, peer = workflow
    read_start = len(peer.requests)
    with ecu.read_transfer(use_agent=True) as reader:
        assert isinstance(reader, agent.AgentReader)
        assert reader.profile["software"] == peer.profile["software"]
        assert reader.profile["baud"] == (125000 if compatibility else peer.profile["baud"])
        data = getattr(reader, "read_" + mode)()
    assert data == (peer.original if mode == "full" else images.extract_calibration(peer.original))
    assert_preflight_precedes_authorization(peer.requests[read_start:])
    target = target_image(peer, mode)
    write_start = len(peer.requests)
    with ecu.transfer():
        state = getattr(ecu, "write_" + mode)(target, use_ram=True, use_agent=True,
                                             verify=True, journal=Journal())
    assert state.completed and state.verified and state.finalized
    assert_preflight_precedes_authorization(peer.requests[write_start:])
    assert peer.image == expected_array(peer, target, mode)
    assert peer.image[:0x10000] == peer.original[:0x10000]
    assert peer.agent_finalizations == 1 and not peer.finalizations


def test_same_small_fingerprint_and_interrupted_probe(workflow, monkeypatch):
    ecu, peer = workflow
    read = ecu.read_memory
    spans = peer.profile["signature_spans"]
    def interrupted(address, count):
        if address == spans[1][0]:
            raise DS2Timeout("supplied missing fingerprint reply")
        return read(address, count)
    monkeypatch.setattr(ecu, "read_memory", interrupted)
    with pytest.raises(DS2Timeout):
        agent.select_reader(ecu)
    assert ecu._resident_signature_matches is None and ecu._resident_profile is None
    assert not any(command in (0x0B, 0x90, 0x07, 0x53) for _, command, _ in peer.requests)
    monkeypatch.setattr(ecu, "read_memory", read)
    identity = ecu._identity_fields()
    before = len(peer.requests)
    reader = agent.select_reader(ecu, identity=identity)
    assert reader.profile["software"] == peer.profile["software"]
    assert [(int.from_bytes(args[:4], "big"), args[4]) for _, _, args in peer.requests[before:]] == list(spans)
    assert sum(count for _, count in spans) == (120 if ecu.family == "MS42" else 108)
    before = len(peer.requests)
    assert agent.select_writer(ecu, identity=identity).profile == reader.profile
    assert len(peer.requests) == before


def test_known_software_id_never_admits_unrecognized_boot_code(workflow):
    ecu, peer = workflow
    peer.image[peer.profile["signature_spans"][1][0]] ^= 1
    assert agent.select_reader(ecu) is None
    assert agent.select_writer(ecu) is None
    assert ecu._resident_signature_matches is False and ecu._resident_profile is None
    assert not any(command in (0x0B, 0x90, 0x07, 0x53) for _, command, _ in peer.requests)


@pytest.mark.parametrize("check", [
    recovery_tests.test_lost_program_ack_is_confirmed_by_readback_without_replaying,
    recovery_tests.test_verify_failure_does_not_finalize_or_reset,
    recovery_tests.test_lost_reset_ack_prevents_retry_and_further_commands,
], ids=lambda check: check.__name__.removeprefix("test_"))
def test_revision_recovery_retains_existing_write_rules(workflow, check):
    check(workflow)


@pytest.mark.parametrize("kind", ["retained", "eeprom"])
def test_storage_restore_keeps_known_revision_and_backup_contract(workflow, monkeypatch, kind):
    import ms42_ms43_storage as storage
    if workflow[0].family == "MS42" and kind == "eeprom":
        pytest.skip("MS42 uses retained flash, not this serial EEPROM service")
    ecu, peer, service, before, snapshot = storage_tests.workflow.__wrapped__(workflow, kind, monkeypatch)
    snapshot = storage.make_snapshot(ecu.family, kind, snapshot["data"],
                                     software_id=images.image_identity(peer.original)["software_id"])
    storage_tests.test_complete_restore_preserves_other_storage_and_backups_first(
        (ecu, peer, service, before, snapshot))
