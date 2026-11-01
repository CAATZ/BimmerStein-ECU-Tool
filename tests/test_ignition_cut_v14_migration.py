"""Current shared timing ownership, tune preservation and dependent migration."""
import pytest

import checksum
import patch_service
from engines.patcher import patch_ms41
from tests.conftest import ref


VARIANTS = [
    ("MS41.0", "_ms410", "ignition_cut_v11_ms410", "launch_control_v12_ms410"),
    ("MS41.1", "_ms411", "ignition_cut_v11_ms411", "launch_control_v12_ms411"),
    ("MS41.2", "_ms412", "ignition_cut_v11_ms412", "launch_control_v12_ms412"),
    ("MS41.3", "", "ignition_cut_v13", "launch_control_v13"),
]
VALUES = {
    "CUTSW": 0, "CUTRPM": 125, "CUT_HYST": 3, "CUT_IPW": 500,
    "LC_SW": 2, "LC_CUTTYPE": 1, "LC_CLUTCHPOL": 0, "LC_MAXRPM": 100,
    "LC_HARDRPM": 105, "LC_HYST": 3, "LC_IPW": 400, "LC_ARMSPEED": 3,
    "LC_MAXSPEED": 10, "LC_MINTPS": 80, "LC_FUEL_HYST_B": 2,
    "LC_FUEL_HYST_A": 3, "LC_IGN_ANGLE": 20,
}


def corrected(image):
    return bytes(checksum.correct_checksums(image)[0])


def current_ids(suffix):
    revision = "v18"
    return ["ignition_cut_" + revision + suffix, "launch_control_" + revision + suffix]


@pytest.mark.parametrize("family,suffix,old_ig,old_lc", VARIANTS)
@pytest.mark.parametrize("with_launch", [False, True])
def test_v14_upgrade_defaults_shared_ownership_and_removal(family, suffix, old_ig, old_lc, with_launch):
    patches = patch_ms41.load_patches()
    new_ig, new_lc = current_ids(suffix)
    previous = [old_ig] + ([old_lc] if with_launch else [])
    selected = [new_ig] + ([new_lc] if with_launch else [])
    prior, _ = patch_ms41.build(ref(family), previous, allow_deprecated=True)
    configured, stock = bytearray(prior), bytearray(ref(family))
    for patch_id in previous:
        for name, address in patches[patch_id]["cave"]["cals"].items():
            payload = VALUES[name].to_bytes(2 if name.endswith("IPW") else 1, "little")
            configured[address:address + len(payload)] = payload
            stock[address:address + len(payload)] = payload
    configured = corrected(configured)
    for patch_id in selected:
        assert not patch_ms41.is_applied(configured, patches[patch_id])

    # Requesting only the engine still carries an installed Launch feature forward.
    upgraded, _ = patch_service.build_image(configured, [new_ig])
    direct, _ = patch_service.build_image(stock, selected)
    assert upgraded == direct
    assert all(checksum.checksum_status(upgraded)[key] for key in ("boot", "program", "cal"))
    for old_id, new_id in zip(previous, selected):
        assert patches[old_id]["deprecated"]
        assert old_id in patches[new_id]["supersedes"]
        assert not patch_ms41.is_applied(upgraded, patches[old_id])
        assert patch_ms41.is_applied(upgraded, patches[new_id])
        for name, address in patches[old_id]["cave"]["cals"].items():
            size = 2 if name.endswith("IPW") else 1
            assert upgraded[address:address + size] == configured[address:address + size]
        for name, default in patches[new_id]["cal_defaults"].items():
            address = patches[new_id]["cave"]["cals"][name]
            assert upgraded[address] == default == 6
            stock[address] = default
    ig = patches[new_ig]
    entries = ig["cave"]["retard_entries"]
    assert len([s for s in ig["cave"]["splices"] if s["base"] == entries["timing"]]) == 2
    assert not any(s["base"] == entries["timing"] for s in patches[new_lc]["cave"]["splices"])
    assert upgraded[ig["cave"]["cals"]["CUT_IGN_ANGLE"]] == 0xFF
    if with_launch:
        assert upgraded[patches[new_lc]["cave"]["cals"]["LC_CUT_IGN_ANGLE"]] == 0xFF
        with pytest.raises(patch_service.PatchError, match="dependent patch"):
            patch_service.revert_patch(upgraded, new_ig)
    for patch_id in reversed(selected):
        upgraded = patch_service.revert_patch(upgraded, patch_id)
        if patch_id == new_lc:
            assert patch_ms41.is_applied(upgraded, ig), "Launch removal must preserve shared timing"
    assert upgraded == corrected(stock)


@pytest.mark.parametrize("family,suffix,old_ig,old_lc", VARIANTS)
@pytest.mark.parametrize("raw_range", [0, 4, 254, 255])
def test_v14_range_defaults_never_reset_existing_configuration(family, suffix, old_ig, old_lc, raw_range):
    patches = patch_ms41.load_patches()
    ids = current_ids(suffix)
    image, _ = patch_ms41.build(ref(family), ids)
    configured = bytearray(image)
    for patch_id in ids:
        for name in patches[patch_id]["cal_defaults"]:
            configured[patches[patch_id]["cave"]["cals"][name]] = raw_range
    configured = corrected(configured)
    rebuilt, _ = patch_ms41.build(configured, ids)
    assert rebuilt == configured

    # Preset non-erased values also survive first installation.
    if raw_range != 255:
        preset = bytearray(ref(family))
        for patch_id in ids:
            for name in patches[patch_id]["cal_defaults"]:
                preset[patches[patch_id]["cave"]["cals"][name]] = raw_range
        installed, _ = patch_ms41.build(preset, ids)
        for patch_id in ids:
            for name in patches[patch_id]["cal_defaults"]:
                assert installed[patches[patch_id]["cave"]["cals"][name]] == raw_range


@pytest.mark.parametrize("family,suffix,old_ig,old_lc", VARIANTS)
def test_v14_partial_predecessor_launch_fails_closed(family, suffix, old_ig, old_lc):
    patches = patch_ms41.load_patches()
    image, _ = patch_ms41.build(ref(family), [old_ig, old_lc], allow_deprecated=True)
    damaged = bytearray(image)
    timing = patches[old_lc]["cave"]["retard_entries"]["timing"]
    damaged[timing] ^= 1
    with pytest.raises(patch_ms41.PatchError, match="PARTIAL predecessor dependency"):
        patch_ms41.build(corrected(damaged), [current_ids(suffix)[0]])


@pytest.mark.parametrize("with_launch", [False, True])
@pytest.mark.parametrize("transitional_fix", [False, True])
def test_ms410_v14_to_v15_preserves_configuration_and_removal(with_launch, transitional_fix, monkeypatch):
    patches = patch_ms41.load_patches()
    ignition, launch = "ignition_cut_v15_ms410", "launch_control_v15_ms410"
    # Retain this exact historical transition while V18 is the active catalog.
    for patch_id in (ignition, launch):
        patches[patch_id] = dict(patches[patch_id], deprecated=False)
    monkeypatch.setattr(patch_ms41, "load_patches", lambda: patches)
    selected = [ignition] + ([launch] if with_launch else [])
    control = next(edit for edit in patches[ignition]["edits"] if edit["off"] == 0x36A00)
    old_ignition, old_launch = "ignition_cut_v14_ms410", "launch_control_v14_ms410"
    old_selected = [old_ignition] + ([old_launch] if with_launch else [])
    previous = bytes.fromhex(next(edit for edit in patches[old_ignition]["edits"]
                                  if edit["off"] == control["off"])["data"])
    assert len(previous) == len(bytes.fromhex(control["data"])) == 252
    assert patches[old_ignition]["deprecated"] and patches[old_ignition]["superseded_by"] == ignition
    assert patches[old_launch]["deprecated"] and patches[old_launch]["superseded_by"] == launch
    assert patches[launch]["requires"] == [ignition]
    assert previous != bytes.fromhex(control["data"])
    current, _ = patch_ms41.build(ref("MS41.0"), old_selected, allow_deprecated=True)
    source, restored = bytearray(current), bytearray(ref("MS41.0"))
    values = VALUES | {"CUT_RETARD_RANGE": 4, "CUT_IGN_ANGLE": 27,
                       "LC_RETARD_RANGE": 5, "LC_CUT_IGN_ANGLE": 20}
    for patch_id in selected:
        for name, address in patches[patch_id]["cave"]["cals"].items():
            payload = values[name].to_bytes(2 if name.endswith("IPW") else 1, "little")
            source[address:address + len(payload)] = payload
            restored[address:address + len(payload)] = payload
    if transitional_fix:
        # Before the version bump, this exact fixed control was briefly labeled V14.
        source[control["off"]:control["off"] + len(previous)] = bytes.fromhex(control["data"])
    source = corrected(source)
    available = {entry["id"]: entry for entry in patch_service.available_patches(source)}
    assert available[ignition]["ok"] and available[ignition]["installed"] == transitional_fix
    assert not available[launch]["installed"]
    assert patch_ms41.is_applied(source, patches[old_ignition]) == (not transitional_fix)
    assert patch_ms41.is_applied(source, patches[old_launch]) == with_launch

    repaired, _ = patch_ms41.build(source, [ignition], allow_deprecated=True)
    direct, _ = patch_ms41.build(corrected(restored), selected, allow_deprecated=True)
    assert repaired == direct
    assert all(checksum.checksum_status(repaired)[key] for key in ("boot", "program", "cal"))
    assert patch_ms41.is_applied(repaired, patches[ignition])
    assert patch_ms41.is_applied(repaired, patches[launch]) == with_launch
    assert not patch_ms41.is_applied(repaired, patches[old_ignition])
    assert not patch_ms41.is_applied(repaired, patches[old_launch])
    assert repaired[control["off"] + 248:control["off"] + 252] == b"\xff" * 4
    if with_launch:
        with pytest.raises(patch_service.PatchError, match="dependent patch"):
            patch_service.revert_patch(repaired, ignition)
        repaired = patch_service.revert_patch(repaired, launch)
    repaired = patch_service.revert_patch(repaired, ignition)
    assert repaired == corrected(restored)

    damaged = bytearray(source)
    damaged[control["off"] + len(previous) - 1] ^= 1
    with pytest.raises(patch_service.PatchError, match="expect|PARTIAL"):
        patch_service.build_image(corrected(damaged), [ignition])
