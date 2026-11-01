"""43X001 admission and application workflows over injected byte pipes only."""
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest

from ds2 import DS2Error
import ms42_ms43_agent as agent
import ms42_ms43_image as images
import ms42_ms43_resident as resident
import ms42_ms43_storage as storage
import ms42_ms43_vin_write as identity_write
from tests.test_ms42_ms43 import Journal
from tests.test_ms42_ms43_agent_workflow import agent_workflow
from tests.test_ms42_ms43_ram_write import expected_array, target_image
from tests.test_ms42_ms43_compatibility_baud import compatibility as compatibility
from tests import test_ms42_ms43_compatibility_baud as rates
from tests import test_ms42_ms43_storage_workflow as storage_tests


@pytest.fixture
def revision_images():
    root = Path(__file__).resolve().parents[2]
    paths = {
        "430069": (root / "_shared/REF_MS43/Siemens_MS43_MS430069_E46_M54B30_US.bin",
                   "eb687fdd8eee4a0671dbe1d3294b4c25788b6cfa60c29f15d3b77293981909da"),
        "43X001": (Path.home() / "ECU-reference-data/Siemens_MS43_MS43X001_E46_M54B30_US.bin",
                   "997975e61fb237094216052c300964dffd97e2ab57a94ddea5d710216389e21d"),
    }
    if not all(path.is_file() for path, _digest in paths.values()):
        pytest.skip("private exact MS43 revision images unavailable")
    result = {}
    for software, (path, digest) in paths.items():
        data = path.read_bytes()
        assert sha256(data).hexdigest() == digest
        assert images.image_identity(data)["software_id"] == software
        result[software] = data
    return result


@pytest.fixture
def x001_workflow(revision_images, monkeypatch):
    ecu, peer = agent_workflow.__wrapped__(SimpleNamespace(param="MS43"), monkeypatch)
    peer.original = revision_images["43X001"]
    peer.image[:] = peer.original
    return ecu, peer


def test_x001_exact_boot_and_resident_owners_share_the_compact_fingerprint(revision_images):
    stock, x001 = revision_images["430069"], revision_images["43X001"]
    for start, end in ((0, 0x4000), (0x8000, 0x10000), (0x3FA98, 0x3FAD8),
                       (0x24B6C, 0x24BB2), (0x6ED30, 0x6ED80)):
        assert stock[start:end] == x001[start:end]
    profile = resident.PROFILES["MS43"]
    assert sum(length for _address, length in profile["signature_spans"]) == 108
    signature = b"".join(x001[address:address + length] for address, length in profile["signature_spans"])
    assert sha256(signature).hexdigest() == profile["fingerprint"]


@pytest.mark.parametrize("mode", ["tune", "full"])
def test_x001_read_uses_agent_and_selected_baud(x001_workflow, compatibility, mode):
    ecu, peer = x001_workflow
    with ecu.read_transfer(use_agent=True) as reader:
        assert isinstance(reader, agent.AgentReader)
        assert reader.profile["baud"] == (125000 if compatibility else 187500)
        result = getattr(reader, "read_" + mode)()
    expected = peer.original if mode == "full" else images.extract_calibration(peer.original)
    assert result == expected and peer.restored
    assert bytes(peer.image) == peer.original and not peer.erases and not peer.programs


@pytest.mark.parametrize("mode", ["tune", "full"])
def test_x001_write_reuses_finalization_and_preserves_other_regions(x001_workflow, compatibility, mode):
    ecu, peer = x001_workflow
    target = target_image(peer, mode)
    with ecu.transfer():
        state = getattr(ecu, "write_" + mode)(target, use_ram=True, use_agent=True,
                                             verify=True, journal=Journal())
    assert state.completed and state.verified and peer.restored
    assert peer.image == expected_array(peer, target, mode)
    assert peer.agent_finalizations == 1 and not peer.finalizations
    assert peer.image[:0x10000] == peer.original[:0x10000]


@pytest.mark.parametrize("target_software", ["430069", "43X001"])
def test_full_revision_conversion_checks_target_identity(x001_workflow, revision_images, target_software):
    ecu, peer = x001_workflow
    source = "430069" if target_software == "43X001" else "43X001"
    peer.original = revision_images[source]
    peer.image[:] = peer.original
    target = revision_images[target_software]
    with ecu.transfer():
        state = ecu.write_full(target, use_ram=True, use_agent=True, verify=True, journal=Journal())
    assert state.completed and state.finalized and state.verified
    assert ecu._identity_fields()["software_id"] == target_software
    assert peer.agent_finalizations == 1 and not peer.finalizations and peer.restored


def test_x001_boot_write_preserves_live_identity(x001_workflow, compatibility):
    rates.test_boot_write_retains_permission_and_protected_data_at_selected_rate(x001_workflow, compatibility)


@pytest.mark.parametrize("kind", ["retained", "eeprom"])
def test_x001_storage_restore_keeps_existing_backup_and_recovery_owner(x001_workflow, monkeypatch, kind):
    ecu, peer = x001_workflow
    if kind == "eeprom":
        from ms42_ms43_eeprom import EepromAgent, select_eeprom
        assert isinstance(select_eeprom(ecu), EepromAgent)
    workflow = storage_tests.workflow.__wrapped__(x001_workflow, kind, monkeypatch)
    _ecu, _peer, service, before, snapshot = workflow
    snapshot = storage.make_snapshot("MS43", kind, snapshot["data"], software_id="43X001")
    storage_tests.test_complete_restore_preserves_other_storage_and_backups_first(
        (ecu, peer, service, before, snapshot))


def test_x001_fast_transfer_does_not_admit_unverified_virginizing(x001_workflow):
    ecu, peer = x001_workflow
    with pytest.raises(DS2Error, match="not mapped for this software version"):
        identity_write.virginize(ecu, clear_vin=True, journal=Journal())
    assert bytes(peer.image) == peer.original and not peer.erases and not peer.programs
    assert not peer.agent_launches and not peer.agent_requests


def test_unknown_revision_still_uses_stock_route(x001_workflow):
    ecu, peer = x001_workflow
    peer.image[peer.layout.sw_offset:peer.layout.sw_offset + 6] = b"430068"
    assert agent.select_reader(ecu) is None
    assert not peer.agent_launches and not peer.erases and not peer.programs
