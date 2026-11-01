"""MS41.3 event-time cut upgrades retain exact historical detection and tuning."""
import pytest

import checksum
import patch_service
from engines.patcher import patch_ms41
from tests.conftest import ref


def test_launch_v13_changes_only_retired_cave_marker_and_dependency():
    patches = patch_ms41.load_patches()
    old, new = patches["launch_control_v12"], patches["launch_control_v13"]
    assert old["deprecated"] and old["superseded_by"] == new["id"]
    assert new["requires"] == ["ignition_cut_v13"]
    assert new["family_id"] == old["family_id"] == "launch_control_v12"
    assert new["cave"] == old["cave"]
    assert new["supersedes"] == old["supersedes"] + [old["id"]]
    assert new["conflicts"] == old["conflicts"] + [old["id"]]
    changed = [(previous, current) for previous, current in zip(old["edits"], new["edits"])
               if previous != current]
    assert len(new["edits"]) == len(old["edits"]) and len(changed) == 1
    previous, current = changed[0]
    assert current["off"] == 0x3A100
    assert set(bytes.fromhex(previous["data"])) == {0xFF}
    assert current == dict(previous, data="0df2" + previous["data"][4:])
    assert all(splice["base"] != current["off"] for splice in new["cave"]["splices"])


@pytest.mark.parametrize("with_launch", [False, True])
def test_configured_v12_upgrade_selects_paired_successor_and_removes_cleanly(with_launch):
    patches = patch_ms41.load_patches()
    old_ids = ["ignition_cut_v12"] + (["launch_control_v12"] if with_launch else [])
    new_ids = ["ignition_cut_v18"] + (["launch_control_v18"] if with_launch else [])
    prior, _ = patch_ms41.build(ref("MS41.3"), old_ids, allow_deprecated=True)
    configured, stock = bytearray(prior), bytearray(ref("MS41.3"))
    values = {
        "CUTSW": 0, "CUTRPM": 125, "CUT_HYST": 0, "CUT_IPW": 500,
        "LC_SW": 2, "LC_CUTTYPE": 1, "LC_CLUTCHPOL": 0, "LC_MAXRPM": 100,
        "LC_HARDRPM": 105, "LC_HYST": 3, "LC_IPW": 400, "LC_ARMSPEED": 3,
        "LC_MAXSPEED": 10, "LC_MINTPS": 80, "LC_FUEL_HYST_B": 2,
        "LC_FUEL_HYST_A": 3, "LC_IGN_ANGLE": 20,
    }
    for patch_id in old_ids:
        for name, address in patches[patch_id]["cave"]["cals"].items():
            payload = values[name].to_bytes(2 if name.endswith("IPW") else 1, "little")
            configured[address:address + len(payload)] = payload
            stock[address:address + len(payload)] = payload
    configured = bytes(checksum.correct_checksums(configured)[0])
    rows = {row["id"]: row for row in patch_service.available_patches(configured)}
    for old_id, new_id in zip(old_ids, new_ids):
        assert rows[old_id]["installed"] and rows[old_id]["deprecated"]
        assert not rows[new_id]["installed"]
        assert [row["id"] for row in rows[new_id]["legacy"]] == [old_id]
    upgraded, _ = patch_service.build_image(configured, ["ignition_cut_v18"])
    direct, _ = patch_service.build_image(stock, new_ids)
    assert upgraded == direct
    rows = {row["id"]: row for row in patch_service.available_patches(upgraded)}
    for old_id, new_id in zip(old_ids, new_ids):
        assert old_id not in rows and rows[new_id]["installed"]
        assert not patch_ms41.is_applied(upgraded, patches[old_id])
    if with_launch:
        with pytest.raises(patch_service.PatchError, match="dependent patch"):
            patch_service.revert_patch(upgraded, "ignition_cut_v18")
    for patch_id in reversed(new_ids):
        upgraded = patch_service.revert_patch(upgraded, patch_id)
    for patch_id in new_ids:
        for name, value in patches[patch_id]["cal_defaults"].items():
            stock[patches[patch_id]["cave"]["cals"][name]] = value
    assert upgraded == bytes(checksum.correct_checksums(stock)[0])
