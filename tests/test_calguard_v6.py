"""Public catalog lifecycle checks for the boot-resident guard migration."""
import hashlib
import json

import pytest

import checksum
import patch_service
import softbsl_install
from engines.patcher import patch_ms41
from tests.conftest import ref


@pytest.mark.parametrize("version", ["MS41.0", "MS41.1", "MS41.2", "MS41.3"])
@pytest.mark.parametrize("chip,bank", [("28f200", "B"), ("29f400", "B"), ("29f400", "T")])
def test_current_guard_lifecycle(version, chip, bank):
    patches = patch_ms41.load_patches()
    stock = bytes(checksum.correct_checksums(ref(version))[0])
    extra = ["amd_flash"] if chip == "29f400" else []
    if bank == "T":
        extra += ["top_ds2_guard"]
    current_ids = ["softbsl_loader", "cal_guard", *extra]
    expected, _ = patch_ms41.build(stock, current_ids, marker=bank)
    delay = patch_ms41.startup_wait_definition(patches)
    assert patch_ms41.is_applied(expected, delay)
    for prior_ids in (["softbsl_loader_v11"], ["softbsl_loader_v11", "cal_guard_v5"]):
        prior, _ = patch_ms41.build(stock, [*prior_ids, *extra], marker=bank, allow_deprecated=True)
        upgraded, _ = patch_service.build_image(prior, current_ids)
        assert upgraded == expected
        assert patch_service.build_image(upgraded, current_ids)[0] == upgraded
        assert upgraded[0x14000:0x1A000] == stock[0x14000:0x1A000]
        assert upgraded[0x5D07:0x5F8B] == stock[0x5D07:0x5F8B]
        assert upgraded[0x605C] == stock[0x605C]
        if "cal_guard_v5" in prior_ids:
            assert patch_service.build_image(prior, ["softbsl_loader"])[0] == expected
            # Reinstalling with the unchecked option must preserve installed intent.
            for selected in (False, True):
                composed, ids, _ = softbsl_install.compose_persistent_target(
                    prior, chip=chip, marker=None, with_calguard=selected)
                assert "cal_guard" in ids
                assert patch_ms41.is_applied(composed, patches["cal_guard"])
                assert composed[0x5FFC:0x6000] == prior[0x5FFC:0x6000]
                assert softbsl_install.compose_persistent_target(
                    composed, chip=chip, marker=None, with_calguard=selected)[0] == composed
            absent = bytearray(prior)
            body = next(e for e in patches["cal_guard_v5"]["edits"] if e["off"] >= 0x6000)
            lo = body["off"]
            absent[lo:lo + len(bytes.fromhex(body["expect"]))] = bytes.fromhex(body["expect"])
            assert patch_service.build_image(absent, current_ids)[0] == expected
            assert patch_ms41.is_applied(softbsl_install.compose_persistent_target(
                absent, chip=chip, marker=None, with_calguard=False)[0], patches["cal_guard"])
            for off in (lo, 0x4942):
                corrupt = bytearray(absent)
                corrupt[off] ^= 1
                with pytest.raises(patch_ms41.PatchError):
                    patch_service.build_image(corrupt, current_ids)
                with pytest.raises(softbsl_install.SoftBSLInstallError):
                    softbsl_install.compose_persistent_target(corrupt, chip=chip, marker=None, with_calguard=True)
    with pytest.raises(patch_ms41.PatchError, match="cal_guard"):
        patch_service.revert_patch(expected, "softbsl_loader")
    removed = patch_service.revert_patch(expected, "cal_guard")
    loader_only, _ = patch_ms41.build(stock, ["softbsl_loader", *extra], marker=bank)
    assert removed == loader_only
    assert patch_ms41.is_absent(removed, delay)
    assert patch_service.build_image(removed, ["cal_guard"])[0] == expected
    if chip == "29f400":
        assert patch_ms41.is_applied(removed, patches["amd_flash"])
        assert patch_ms41.is_applied(removed, patches["top_ds2_guard"]) is (bank == "T")
    removed = patch_service.revert_patch(removed, "softbsl_loader")
    assert not patch_ms41.is_applied(removed, patches["softbsl_loader"])
    assert checksum.verify_checksum(removed)[0]
    assert patch_service.missing_boot_patches(expected, stock[0x4000:0x6000])
    assert patch_service.missing_boot_patches(expected, expected[0x4000:0x6000]) == []


def test_guard_executable_payloads_match_reviewed_package():
    patches = patch_ms41.load_patches()
    # Frozen executable edit lists, independent of UI/status metadata.
    expected = {
        "cal_guard": "9ab7d29c7abb498507c882b7d6964d597e7bb6e0bdd777c99788bf9bed785627",
        "softbsl_loader": "fb90954435cd76977fb5eb397c2f6c27b812aa89d565621bfd5783bbe5c8f117",
    }
    for name, digest in expected.items():
        raw = json.dumps(patches[name]["edits"], sort_keys=True).encode()
        assert hashlib.sha256(raw).hexdigest() == digest
    assert all(0x4000 <= e["off"] < e["off"] + len(bytes.fromhex(e["data"])) <= 0x6000
               for e in patches["cal_guard"]["edits"])
    wait = patch_ms41.startup_wait_definition(patches)
    assert [(edit["off"], edit["expect"], edit["data"]) for edit in wait["edits"]] == [
        (0x4460, "cc00cc00", "e6f060ea"),
        (0x4484, "e0802801ea308604", "a758a7a728013dfc")]
    assert wait["conflicts"] == []


@pytest.mark.parametrize("version", ["MS41.0", "MS41.1", "MS41.2", "MS41.3"])
@pytest.mark.parametrize("bank", ["B", "T"])
def test_legacy_guard_and_historical_amd_wait_keep_exact_lifecycle(version, bank):
    patches = patch_ms41.load_patches()
    stock = bytes(checksum.correct_checksums(ref(version))[0])
    wait = patch_ms41.startup_wait_definition(patches)
    historical = {**patches, "amd_flash": {
        **patches["amd_flash"],
        "edits": [*patches["amd_flash"]["edits"], *wait["edits"]]}}
    extra = ["amd_flash"] + (["top_ds2_guard"] if bank == "T" else [])
    old, _ = patch_ms41.build(
        stock, ["softbsl_loader_v11", "cal_guard_v5", *extra],
        patches=historical, marker=bank, allow_deprecated=True)
    assert patch_ms41.is_applied(old, patches["cal_guard_v5"])
    assert patch_ms41.is_applied(old, patches["amd_flash"])
    assert patch_ms41.is_applied(old, wait)
    assert patch_service.build_image(old, ["amd_flash"])[0] == old
    native = patch_ms41.revert(old, wait)
    assert patch_service.missing_boot_patches(old, native) == ["cal_guard_v5"]
    reads = [(lo, native[lo:hi]) for lo, hi in patch_service.boot_patch_read_ranges(old)]
    assert patch_service.missing_boot_patches_sparse(old, reads) == ["cal_guard_v5"]
    removed = patch_service.revert_patch(old, "cal_guard_v5")
    assert patch_ms41.is_absent(removed, wait)
    assert patch_ms41.is_applied(removed, patches["amd_flash"])
    assert patch_ms41.is_applied(removed, patches["softbsl_loader_v11"])
    assert patch_ms41.is_applied(removed, patches["top_ds2_guard"]) is (bank == "T")
    current, _ = patch_service.build_image(removed, ["softbsl_loader", "cal_guard"])
    expected, _ = patch_ms41.build(
        stock, ["softbsl_loader", "cal_guard", *extra], marker=bank)
    assert current == expected
    for image in (old, native, removed, current):
        assert image[0x5CD5:0x5F8C] == stock[0x5CD5:0x5F8C]
        assert image[0x14000:0x1A000] == stock[0x14000:0x1A000]
        assert image[0x5FFC:0x6000] == old[0x5FFC:0x6000]
        assert checksum.verify_checksum(image)[0]


@pytest.mark.parametrize("marker,mode,corrupt,want", [
    (1, 2, None, True), (1, 1, None, False), (0, 2, None, False),
    (1, 2, 0x44B0, False), (1, 2, 0x5C32, False),
])
@pytest.mark.parametrize("bank", ["B", "T"])
def test_boot_recovery_route_requires_exact_guard_loader_and_live_mode(marker, mode, corrupt, want, bank):
    from engines.softbsl.softbsl_host import SoftBSL
    image, _ = patch_ms41.build(ref("MS41.2"), ["softbsl_loader", "cal_guard"], marker=bank)
    image = bytearray(image)
    if corrupt:
        image[corrupt] ^= 1
    class ReadOnlyDS2:
        def read_mem(self, address, length):
            if address == 0xE740:
                return bytes((marker,))
            if address == 0xE743:
                return bytes((mode,))
            return bytes(image[address ^ 0x4000:(address ^ 0x4000) + length])

        read_memory_range = read_mem

    assert SoftBSL(ReadOnlyDS2(), log=lambda *_: None).calguard_direct_entry_ready() is want
