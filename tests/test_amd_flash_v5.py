"""AMD V5 identification, exact predecessor migration and protected-boot lifecycle."""
from copy import deepcopy
from pathlib import Path

import pytest

import ecu_info
import patch_service
from engines.patcher import patch_ms41
from engines.softbsl import softbsl_host as host
from tests.conftest import ref


VERSIONS = ("MS41.0", "MS41.1", "MS41.2", "MS41.3")
OLD_SIGNATURE = bytes.fromhex("e00e0d58f04ec084")
NEW_SIGNATURE = bytes.fromhex("e00e0d5ff04ec084")


def prior_image(version, marker="B", guard=False):
    patches = patch_ms41.load_patches()
    historical = dict(patches)
    historical["amd_flash"] = {
        **deepcopy(patches["amd_flash_v4"]),
        "id": "amd_flash", "deprecated": False, "conflicts": [],
    }
    base = bytearray(ref(version))
    base[0x5CD5:0x5F8C] = bytes((i * 37 + 11) & 255 for i in range(0x5F8C - 0x5CD5))
    base, _ = patch_ms41.checksum.correct_checksums(base)
    ids = ["amd_flash"]
    if guard:
        ids += ["softbsl_loader", "cal_guard"]
    if marker == "T":
        ids += ["top_ds2_guard"]
    image, _ = patch_ms41.build(base, ids, patches=historical, marker=marker)
    return image, bytes(base)


@pytest.mark.parametrize("signature", (OLD_SIGNATURE, NEW_SIGNATURE))
def test_current_and_prior_signatures_select_same_command_set(signature):
    assert ecu_info.chip_family(signature) == "amd"
    assert ecu_info.decode_flash_chip(signature) == "AMD driver — 29F200 / 29F400"
    assert ecu_info.bank_identification("T", signature) == "Detected bank: TOP"

    class Reader:
        def read_mem(self, address, size):
            assert (address, size) == (0x23C, 8)
            return signature

    assert host._detect_flash_chip(Reader()) == ("amd", signature)
    assert ecu_info.chip_family(signature[:-1] + b"\x00") is None


def test_v5_artifact_windows_and_exact_legacy_metadata():
    patches = patch_ms41.load_patches()
    current = patches["amd_flash"]
    prior = patches["amd_flash_v4"]
    assert current["version"] == "V5"
    assert current["supersedes"] == ["amd_flash_v4", "amd_flash_v3"]
    assert prior["deprecated"] and prior["version"] == "V4"
    assert prior["edits"] == patches["amd_flash_v3"]["edits"][:4]
    root = Path(patch_ms41.__file__).parent
    program = bytes.fromhex((root / "program_amd_v7.hex").read_text())
    erase = bytes.fromhex((root / "erase_amd_v2.hex").read_text())
    assert len(program) == 230 and 0x4230 + len(program) <= 0x432E
    assert len(erase) == 164 and 0x432E + len(erase) <= 0x4412
    edits = {e["off"]: e for e in current["edits"]}
    assert bytes.fromhex(edits[0x423C]["data"]) == program[12:]
    assert bytes.fromhex(edits[0x4334]["data"]) == erase[6:]
    assert program[12:20] == NEW_SIGNATURE
    for edit in current["edits"]:
        assert len(bytes.fromhex(edit["expect"])) == len(bytes.fromhex(edit["data"]))


@pytest.mark.parametrize("version", VERSIONS)
@pytest.mark.parametrize("marker", ("B", "T"))
@pytest.mark.parametrize("guard", (False, True))
def test_prior_driver_upgrade_removal_preserves_recovery_identity_and_top(version, marker, guard):
    patches = patch_ms41.load_patches()
    source, base = prior_image(version, marker, guard)
    assert patch_ms41.is_applied(source, patches["amd_flash_v4"])
    rows = {p["id"]: p for p in patch_service.available_patches(source)}
    assert not rows["amd_flash"]["installed"]
    assert rows["amd_flash"]["legacy"][0]["id"] == "amd_flash_v4"
    assert rows["amd_flash_v4"]["removable"] == (marker == "B")
    upgraded, _ = patch_service.build_image(source, ["amd_flash"])
    assert patch_ms41.is_applied(upgraded, patches["amd_flash"])
    assert not patch_ms41.is_applied(upgraded, patches["amd_flash_v4"])
    assert patch_service.build_image(upgraded, ["amd_flash"])[0] == upgraded
    assert patch_service.missing_boot_patches(upgraded, source) == ["amd_flash"]
    reads = [(lo, source[lo:hi]) for lo, hi in patch_service.boot_patch_read_ranges(upgraded)]
    assert patch_service.missing_boot_patches_sparse(upgraded, reads) == ["amd_flash"]
    assert patch_service.missing_boot_patches(upgraded, upgraded) == []
    assert patch_ms41.is_applied(upgraded, patches["cal_guard"]) == guard
    delay = patch_ms41.startup_wait_definition(patches)
    assert patch_ms41.is_applied(upgraded, delay) == guard
    for lo, hi in ((0x5CD5, 0x5F8C), (0x6000, 0x6100), (0x1400C, 0x14016), (0x5FFC, 0x6000)):
        assert upgraded[lo:hi] == source[lo:hi]
    if marker == "T":
        assert patch_ms41.is_applied(upgraded, patches["top_ds2_guard"])
        for data, patch_id in ((source, "amd_flash_v4"), (upgraded, "amd_flash")):
            with pytest.raises(patch_service.PatchError, match="top_ds2_guard"):
                patch_service.revert_patch(data, patch_id)
        upgraded = patch_service.revert_patch(upgraded, "top_ds2_guard")
    restored = patch_service.revert_patch(upgraded, "amd_flash")
    assert patch_ms41.is_absent(restored, patches["amd_flash"])
    assert patch_ms41.is_applied(restored, patches["cal_guard"]) == guard
    assert patch_ms41.is_applied(restored, delay) == guard
    assert restored[0x4230:0x4412] == base[0x4230:0x4412]
    assert all(patch_ms41.checksum.checksum_status(restored)[key] for key in ("boot", "program", "cal"))


@pytest.mark.parametrize("version", VERSIONS)
@pytest.mark.parametrize("marker", ("B", "T"))
def test_shared_persistent_installer_upgrades_prior_driver(version, marker):
    patches = patch_ms41.load_patches()
    source, _ = prior_image(version, marker, guard=True)
    assert host._patch_state(source, patches["amd_flash"]) == "legacy"
    built, ids, _ = host.compose_persistent_image(source, "29f400", marker=marker)
    assert "amd_flash" in ids
    assert patch_ms41.is_applied(built, patches["amd_flash"])
    assert patch_ms41.is_applied(built, patches["softbsl_loader"])
    assert patch_ms41.is_applied(built, patches["cal_guard"])
    assert patch_ms41.is_applied(built, patches["top_ds2_guard"]) == (marker == "T")
    assert built[0x5CD5:0x5F8C] == source[0x5CD5:0x5F8C]
    assert built[0x5FFC:0x6000] == source[0x5FFC:0x6000]
    assert host.compose_persistent_image(built, "29f400", marker=marker)[0] == built


@pytest.mark.parametrize("version", VERSIONS)
def test_old_first_word_failure_revision_remains_upgradeable(version):
    patches = patch_ms41.load_patches()
    source, _ = prior_image(version)
    old = bytearray(source)
    program = patches["amd_flash_v4"]["edits"][0]
    payload = bytes.fromhex(program["upgrade_expect"])
    old[program["off"]:program["off"] + len(payload)] = payload
    built, _ = patch_service.build_image(old, ["amd_flash"])
    assert patch_ms41.is_applied(built, patches["amd_flash"])
    assert host._patch_state(old, patches["amd_flash"]) == "legacy"


@pytest.mark.parametrize("offset", (0x4308, 0x43C4))
def test_unknown_driver_extension_bytes_reject_upgrade(offset):
    source, _ = prior_image("MS41.2")
    corrupt = bytearray(source)
    corrupt[offset] ^= 1
    with pytest.raises(patch_service.PatchError, match="expect"):
        patch_service.build_image(corrupt, ["amd_flash"])
    with pytest.raises(host.SoftBSLError, match="partial/inconsistent"):
        host.compose_persistent_image(corrupt, "29f400")


@pytest.mark.parametrize("guard", (False, True))
def test_prior_v4_removal_cleans_only_orphaned_startup_wait(guard):
    patches = patch_ms41.load_patches()
    source, _ = prior_image("MS41.0", guard=guard)
    delay = patch_ms41.startup_wait_definition(patches)
    source = bytearray(source)
    for edit in delay["edits"]:
        data = bytes.fromhex(edit["data"])
        source[edit["off"]:edit["off"] + len(data)] = data
    source, _ = patch_ms41.checksum.correct_checksums(source)
    owners = patch_service.boot_write_patches_in(source)
    assert "amd_flash_v4" in owners and "amd_flash_v3" not in owners
    reads = patch_service.boot_patch_read_ranges(source)
    assert all(any(lo <= e["off"] < hi for lo, hi in reads) for e in delay["edits"])
    removed = patch_service.revert_patch(source, "amd_flash_v4")
    assert patch_ms41.is_applied(removed, delay) == guard
    assert patch_ms41.is_applied(removed, patches["cal_guard"]) == guard
