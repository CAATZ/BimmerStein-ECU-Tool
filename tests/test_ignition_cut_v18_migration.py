"""V18 timing and knock guards retains exact predecessor detection and configured tuning."""
import pytest

import checksum
import patch_service
from engines.patcher import patch_ms41
from tests.conftest import ref


PREDECESSORS = (
    ("MS41.0", "_ms410", "v17", "v17"),
    ("MS41.1", "_ms411", "v17", "v17"),
    ("MS41.2", "_ms412", "v17", "v17"),
    ("MS41.3", "", "v17", "v17"),
    ("MS41.0", "_ms410", "v16", "v16"),
    ("MS41.1", "_ms411", "v16", "v16"),
    ("MS41.2", "_ms412", "v16", "v16"),
    ("MS41.3", "", "v16", "v16"),
    ("MS41.0", "_ms410", "v14", "v14"),
    ("MS41.0", "_ms410", "v15", "v15"),
    ("MS41.1", "_ms411", "v14", "v14"),
    ("MS41.2", "_ms412", "v14", "v14"),
    ("MS41.3", "", "v14", "v14"),
    ("MS41.0", "_ms410", "v11", "v12"),
    ("MS41.1", "_ms411", "v11", "v12"),
    ("MS41.2", "_ms412", "v11", "v12"),
    ("MS41.3", "", "v12", "v12"),
)
VALUES = {
    "CUTSW": 1, "CUTRPM": 125, "CUT_HYST": 3, "CUT_IPW": 500,
    "CUT_RETARD_RANGE": 4, "CUT_IGN_ANGLE": 27,
    "LC_SW": 2, "LC_CUTTYPE": 1, "LC_CLUTCHPOL": 0, "LC_MAXRPM": 100,
    "LC_HARDRPM": 105, "LC_HYST": 3, "LC_IPW": 400, "LC_ARMSPEED": 3,
    "LC_MAXSPEED": 10, "LC_MINTPS": 80, "LC_FUEL_HYST_B": 0,
    "LC_FUEL_HYST_A": 3, "LC_IGN_ANGLE": 20,
    "LC_RETARD_RANGE": 5, "LC_CUT_IGN_ANGLE": 20,
}


@pytest.mark.parametrize("family,suffix,old_cut_revision,old_launch_revision", PREDECESSORS)
@pytest.mark.parametrize("with_launch", (False, True))
def test_v18_upgrade_retains_configured_predecessor_and_removes_cleanly(
        family, suffix, old_cut_revision, old_launch_revision, with_launch):
    patches = patch_ms41.load_patches()
    old_cut = f"ignition_cut_{old_cut_revision}{suffix}"
    old_launch = f"launch_control_{old_launch_revision}{suffix}"
    cut, launch = "ignition_cut_v18" + suffix, "launch_control_v18" + suffix
    old_ids = [old_cut] + ([old_launch] if with_launch else [])
    new_ids = [cut] + ([launch] if with_launch else [])
    previous, _ = patch_ms41.build(ref(family), old_ids, allow_deprecated=True)
    configured, tuned_stock = bytearray(previous), bytearray(ref(family))
    for old_id, new_id in zip(old_ids, new_ids):
        assert old_id in patches[new_id]["supersedes"]
        for name, address in patches[old_id]["cave"]["cals"].items():
            assert patches[new_id]["cave"]["cals"][name] == address
            payload = VALUES[name].to_bytes(2 if name.endswith("IPW") else 1, "little")
            configured[address:address + len(payload)] = payload
            tuned_stock[address:address + len(payload)] = payload
    configured = bytes(checksum.correct_checksums(configured)[0])
    rows = {row["id"]: row for row in patch_service.available_patches(configured)}
    assert rows[old_cut]["deprecated"] and rows[old_cut]["installed"]
    if with_launch:
        assert rows[old_launch]["deprecated"] and rows[old_launch]["installed"]

    # An engine-only selection carries an exact installed Launch forward.
    upgraded, _ = patch_service.build_image(configured, [cut])
    direct, _ = patch_service.build_image(tuned_stock, new_ids)
    assert upgraded == direct
    assert all(checksum.checksum_status(upgraded)[key] for key in ("boot", "program", "cal"))
    rows = {row["id"]: row for row in patch_service.available_patches(upgraded)}
    assert rows[cut]["installed"] and rows[cut]["legacy"] == []
    assert old_cut not in rows
    assert patches[cut]["family_id"] == "ignition_cut_v18"
    if with_launch:
        assert rows[launch]["installed"] and rows[launch]["legacy"] == []
        assert old_launch not in rows
        assert patches[launch]["requires"] == [cut]
        with pytest.raises(patch_service.PatchError, match="dependent patch"):
            patch_service.revert_patch(upgraded, cut)
    for patch_id in reversed(new_ids):
        upgraded = patch_service.revert_patch(upgraded, patch_id)
        assert patch_ms41.is_absent(upgraded, patches[patch_id])
    # Removal preserves introduced defaults and tuning, then reinstalls exactly.
    rebuilt, _ = patch_service.build_image(upgraded, new_ids)
    assert rebuilt == direct


@pytest.mark.parametrize("family,suffix,revision", (
    ("MS41.0", "_ms410", "v17"),
    ("MS41.1", "_ms411", "v17"),
    ("MS41.2", "_ms412", "v17"),
    ("MS41.3", "", "v17"),
    ("MS41.0", "_ms410", "v16"),
    ("MS41.1", "_ms411", "v16"),
    ("MS41.2", "_ms412", "v16"),
    ("MS41.3", "", "v16"),
    ("MS41.0", "_ms410", "v15"), ("MS41.1", "_ms411", "v14"),
    ("MS41.2", "_ms412", "v14"), ("MS41.3", "", "v14"),
))
def test_v18_rejects_partial_current_predecessor_without_modifying_input(family, suffix, revision):
    patches = patch_ms41.load_patches()
    old_cut, old_launch = "ignition_cut_" + revision + suffix, "launch_control_" + revision + suffix
    previous, _ = patch_ms41.build(ref(family), [old_cut, old_launch], allow_deprecated=True)
    damaged = bytearray(previous)
    damaged[patches[old_launch]["cave"]["base"] + 20] ^= 1
    before = bytes(damaged)
    with pytest.raises(patch_service.PatchError, match="expect|PARTIAL"):
        patch_service.build_image(damaged, ["ignition_cut_v18" + suffix])
    assert bytes(damaged) == before
