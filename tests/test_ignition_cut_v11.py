"""MS41.3 V11 restores the native WBO instructions damaged by V10."""
import pytest

import patch_service
from engines.patcher import patch_ms41
from tests.conftest import ref


BAD_SPLICE = 0x2F5A6
ORIGINAL = bytes.fromhex("d633c2fd")
BROKEN_V10 = bytes.fromhex("fa03c2e2")


def test_v11_changes_only_the_ms413_invalid_splice_and_keeps_v10_detectable():
    patches = patch_ms41.load_patches()
    old, new = patches["ignition_cut_v10"], patches["ignition_cut_v11"]
    assert old["deprecated"] and old["superseded_by"] == new["id"]
    assert new["family_id"] == "ignition_cut_v10" and new["target"] == "MS41.3"
    assert new["supersedes"] == old["supersedes"] + [old["id"]]
    assert new["conflicts"] == old["conflicts"] + [old["id"]]
    assert new["cave"]["splices"] == [
        splice for splice in old["cave"]["splices"] if splice["off"] != BAD_SPLICE]
    assert not patch_ms41.validate_splices(new)
    assert len(new["edits"]) == len(old["edits"])
    for previous, current in zip(old["edits"], new["edits"]):
        if previous["off"] == BAD_SPLICE:
            assert bytes.fromhex(previous["expect"]) == ORIGINAL
            assert bytes.fromhex(previous["data"]) == BROKEN_V10
            assert current == dict(previous, data=ORIGINAL.hex(),
                                   upgrade_expect=[BROKEN_V10.hex()])
        else:
            assert current == previous
    assert patches["launch_control_v7"]["requires"] == [new["id"]]
    assert patches["launch_control_v11"]["requires"] == [new["id"]]
    for suffix in ("_ms410", "_ms411", "_ms412"):
        assert not patches["ignition_cut_v10" + suffix].get("deprecated")
        assert patches["launch_control_v7" + suffix]["requires"] == ["ignition_cut_v10" + suffix]
        assert patches["launch_control_v11" + suffix]["requires"] == ["ignition_cut_v10" + suffix]


@pytest.mark.parametrize("with_launch", [False, True])
def test_v11_fresh_install_and_removal_preserve_native_wbo(with_launch):
    patches = patch_ms41.load_patches()
    ids = ["ignition_cut_v11"] + (["launch_control_v11"] if with_launch else [])
    image, _ = patch_service.build_image(ref("MS41.3"), ids)
    assert image[BAD_SPLICE:BAD_SPLICE + 4] == ORIGINAL
    assert patch_ms41.is_applied(image, patches["ignition_cut_v11"])
    assert not patch_ms41.is_applied(image, patches["ignition_cut_v10"])
    assert patch_ms41.checksum.verify_checksum(bytearray(image))[0]
    for patch_id in reversed(ids):
        image = patch_service.revert_patch(image, patch_id)
    expected, _ = patch_ms41.checksum.correct_checksums(bytearray(ref("MS41.3")))
    assert image == bytes(expected)


@pytest.mark.parametrize("with_launch", [False, True])
def test_configured_exact_v10_upgrades_to_v11_without_losing_launch(with_launch):
    patches = patch_ms41.load_patches()
    historical = dict(patches)
    historical["launch_control_v7"] = dict(patches["launch_control_v7"],
                                           requires=["ignition_cut_v10"])
    old_ids = ["ignition_cut_v10"] + (["launch_control_v7"] if with_launch else [])
    prior, _ = patch_ms41.build(ref("MS41.3"), old_ids, historical, allow_deprecated=True)
    assert prior[BAD_SPLICE:BAD_SPLICE + 4] == BROKEN_V10
    configured, configured_stock = bytearray(prior), bytearray(ref("MS41.3"))
    values = {"CUTSW": 0, "CUTRPM": 125, "CUT_HYST": 3, "CUT_IPW": 500,
              "LC_SW": 255, "LC_CUTTYPE": 1, "LC_CLUTCHPOL": 0,
              "LC_MAXRPM": 100, "LC_HARDRPM": 103, "LC_HYST": 2,
              "LC_IPW": 400, "LC_ARMSPEED": 3, "LC_MAXSPEED": 10,
              "LC_MINTPS": 80}
    for patch_id in old_ids:
        for name, address in historical[patch_id]["cave"]["cals"].items():
            payload = values[name].to_bytes(2 if name.endswith("IPW") else 1, "little")
            configured[address:address + len(payload)] = payload
            configured_stock[address:address + len(payload)] = payload
    configured, _ = patch_ms41.checksum.correct_checksums(configured)
    assert patch_ms41.is_applied(configured, patches["ignition_cut_v10"])
    upgraded, _ = patch_service.build_image(configured, ["ignition_cut_v11"])
    ids = ["ignition_cut_v11"] + (["launch_control_v7"] if with_launch else [])
    direct, _ = patch_ms41.build(configured_stock, ids, allow_deprecated=True)
    assert upgraded == direct
    assert not patch_ms41.is_applied(upgraded, patches["ignition_cut_v10"])
    for patch_id in reversed(ids):
        assert patch_ms41.is_applied(upgraded, patches[patch_id])
        upgraded = patch_service.revert_patch(upgraded, patch_id)
    expected, _ = patch_ms41.checksum.correct_checksums(configured_stock)
    assert upgraded == bytes(expected)


def test_deprecated_ms413_v10_install_is_rejected():
    with pytest.raises(patch_ms41.PatchError, match="deprecated.*ignition_cut_v10"):
        patch_service.build_image(ref("MS41.3"), ["ignition_cut_v10"])
