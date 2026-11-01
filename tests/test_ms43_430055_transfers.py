"""Exact boot/service revisions, including preserved boot during conversion."""
from hashlib import sha256
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from ds2 import DS2Timeout
import ms42_ms43_agent as agent
import ms42_ms43_image as images
import ms42_ms43_resident as resident
from tests.test_ms42_ms43 import Journal
from tests.test_ms42_ms43_agent_workflow import agent_workflow
from tests.test_ms42_ms43_ram_write import expected_array, target_image
from tests.test_ms42_ms43_compatibility_baud import compatibility as compatibility
from tests import test_ms42_ms43_storage_workflow as storage_tests


@pytest.fixture
def revision_images():
    root = Path(os.environ.get("MS41_TEST_DATA_ROOT", Path(__file__).resolve().parents[2] / "_shared"))
    paths = {
        "430055": (root / "REF_MS43/430055.bin",
                   "820bce7aea24933673fce0b6dc4b24b4f672b4e6dbd43476dcecc5826feec1f8"),
        "430069": (root / "REF_MS43/Siemens_MS43_MS430069_E46_M54B30_US.bin",
                   "eb687fdd8eee4a0671dbe1d3294b4c25788b6cfa60c29f15d3b77293981909da"),
    }
    result = {}
    for software, (path, digest) in paths.items():
        if not path.is_file():
            pytest.skip("private complete MS43 reference unavailable")
        data = path.read_bytes()
        assert sha256(data).hexdigest() == digest
        assert images.image_identity(data)["software_id"] == software
        result[software] = data
    return result


@pytest.fixture(params=[("430055", "430055"), ("430055", "430069"), ("430069", "430055")],
                ids=["055", "055-boot-069-program", "069-boot-055-program"])
def workflow(request, revision_images, monkeypatch):
    boot, program = request.param
    ecu, peer = agent_workflow.__wrapped__(SimpleNamespace(param="MS43"), monkeypatch)
    # This is the actual ordinary-full-write boundary, using two complete known
    # source images. No missing boot bytes are filled from another revision.
    peer.original = revision_images[boot][:0x10000] + revision_images[program][0x10000:]
    assert all(images.checksum_status(peer.original)[group] for group in ("boot", "program", "cal"))
    peer.image[:] = peer.original
    peer.profile = next(p for p in resident.BOOT_PROFILES["MS43"] if p["software"] == boot)
    return ecu, peer


def test_only_existing_compact_spans_select_boot_independently_of_program(workflow, monkeypatch):
    ecu, peer = workflow
    identity = ecu._identity_fields()
    before = len(peer.requests)
    reader = agent.select_reader(ecu, identity=identity)
    assert reader.profile["software"] == peer.profile["software"]
    assert reader.identity == identity
    assert ecu._resident_signature_matches is True
    assert [(int.from_bytes(args[:4], "big"), args[4]) for mode, cmd, args in peer.requests[before:]] == list(
        resident.PROFILES["MS43"]["signature_spans"])
    assert sum(n for _, n in reader.profile["signature_spans"]) == 108
    before = len(peer.requests)
    assert agent.select_writer(ecu, identity=identity).profile == reader.profile
    assert len(peer.requests) == before
    closed = []
    monkeypatch.setattr(peer, "close", lambda: closed.append(True), raising=False)
    ecu.close()
    assert closed == [True]
    assert ecu._resident_signature_matches is None and ecu._resident_profile is None


@pytest.mark.parametrize("mode", ["tune", "full"])
def test_native_route_read_keeps_boot_identity_and_selected_rate(workflow, compatibility, mode):
    ecu, peer = workflow
    with ecu.read_transfer(use_agent=True) as reader:
        assert isinstance(reader, agent.AgentReader)
        assert reader.profile["software"] == peer.profile["software"]
        assert reader.profile["baud"] == (125000 if compatibility else 187500)
        result = getattr(reader, "read_" + mode)()
    assert result == (peer.original if mode == "full" else images.extract_calibration(peer.original))
    assert peer.restored and not peer.erases and not peer.programs


@pytest.mark.parametrize("mode", ["tune", "full"])
def test_writes_keep_existing_regions_and_ram_finalization(workflow, compatibility, mode):
    ecu, peer = workflow
    target = target_image(peer, mode)
    with ecu.transfer():
        state = getattr(ecu, "write_" + mode)(target, use_ram=True, use_agent=True,
                                             verify=True, journal=Journal())
    assert state.completed and state.verified and state.finalized
    assert peer.image == expected_array(peer, target, mode)
    assert peer.image[:0x10000] == peer.original[:0x10000]
    assert peer.agent_finalizations == 1 and not peer.finalizations


@pytest.mark.parametrize("kind", ["retained", "eeprom"])
def test_storage_restore_uses_existing_backup_and_recovery(workflow, monkeypatch, kind):
    import ms42_ms43_storage as storage
    ecu, peer, service, before, snapshot = storage_tests.workflow.__wrapped__(workflow, kind, monkeypatch)
    snapshot = storage.make_snapshot("MS43", kind, snapshot["data"],
                                     software_id=images.image_identity(peer.original)["software_id"])
    storage_tests.test_complete_restore_preserves_other_storage_and_backups_first(
        (ecu, peer, service, before, snapshot))


@pytest.mark.parametrize("fallbacks", [1, 2], ids=["125000", "9600"])
def test_rate_fallback_retains_selected_boot_service(workflow, fallbacks):
    ecu, peer = workflow
    reader = agent.select_reader(ecu)
    reader.enter()
    for index in range(fallbacks):
        assert reader._lower_baud()
        assert reader.profile["baud"] == (125000 if index == 0 else 9600)
        assert reader.profile["software"] == peer.profile["software"]
        assert reader.profile["challenge"] == peer.profile["challenge"]
        assert reader.profile["key"] == peer.profile["key"]
        reader.enter()
    assert reader.read_tune() == images.extract_calibration(peer.original)
    reader.finish()


def test_interrupted_fingerprint_never_starts_authorization(workflow, monkeypatch):
    ecu, peer = workflow
    read = ecu.read_memory
    def interrupted(address, count):
        if address == 0x8004:
            raise DS2Timeout("supplied lost fingerprint reply")
        return read(address, count)
    monkeypatch.setattr(ecu, "read_memory", interrupted)
    with pytest.raises(DS2Timeout):
        agent.select_reader(ecu)
    assert ecu._resident_signature_matches is None and ecu._resident_profile is None
    assert not any(command in (0x90, 0x07, 0x53) for _, command, _ in peer.requests)
    monkeypatch.setattr(ecu, "read_memory", read)
    assert agent.select_reader(ecu).profile["software"] == peer.profile["software"]


def test_exact_boot_images_and_identical_relocated_write_overlays(revision_images):
    old, current = revision_images["430055"], revision_images["430069"]
    for image in revision_images.values():
        agent.validate_boot_image(image, "MS43")
    for profile in resident.BOOT_PROFILES["MS43"]:
        image = revision_images[profile["software"]]
        assert profile["challenge"] + profile["key"] == image[0x3F5C:0x3F64]
    assert old[:0x3C36] == current[:0x3C36]
    for start, length in ((0xAA94, 0x24C), (0xB586, 0x322)):
        assert old[start - 0xCE:start - 0xCE + length] == current[start:start + length]


def test_explicit_boot_conversion_reidentifies_service_after_reset(workflow, revision_images):
    ecu, peer = workflow
    incoming = "430069" if peer.profile["software"] == "430055" else "430055"
    target = revision_images[incoming][:0x10000] + peer.original[0x10000:]
    with ecu.transfer():
        state = ecu.write_full(target, use_ram=True, use_agent=True, write_boot=True,
                               verify=True, journal=Journal())
    assert state.completed and state.finalized and state.verified
    assert peer.image[0x4000:0x8000] == peer.original[0x4000:0x8000]
    assert ecu._resident_signature_matches is None and ecu._resident_profile is None
    # The behavioral peer now represents the newly committed boot code.
    peer.profile = next(profile for profile in resident.BOOT_PROFILES["MS43"]
                        if profile["software"] == incoming)
    reader = agent.select_reader(ecu)
    assert reader.profile["software"] == incoming
    reader.enter()
    assert reader.read_tune() == images.extract_calibration(peer.image)
    reader.finish()
