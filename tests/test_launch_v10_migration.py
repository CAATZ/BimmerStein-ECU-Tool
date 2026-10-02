"""Launch V11 delivery preserves V7/V8/V9/V10 tuning and independent fuel hysteresis."""
import pytest

import checksum
import patch_service
from engines.patcher import patch_ms41
from tests.conftest import ref


VARIANTS = (
    ("MS41.0", "_ms410", "ignition_cut_v10_ms410"),
    ("MS41.1", "_ms411", "ignition_cut_v10_ms411"),
    ("MS41.2", "_ms412", "ignition_cut_v10_ms412"),
    ("MS41.3", "", "ignition_cut_v11"),
)


def _configured(image, patch, switch, ipw):
    values = {
        "LC_SW": switch, "LC_CUTTYPE": 1, "LC_CLUTCHPOL": 1,
        "LC_MAXRPM": 100, "LC_ARMSPEED": 5, "LC_MAXSPEED": 10,
        "LC_MINTPS": 106, "LC_HARDRPM": 105, "LC_HYST": 7, "LC_IPW": ipw,
        "LC_FUEL_HYST_B": 0xFF, "LC_FUEL_HYST_A": 0xFF,
    }
    configured = bytearray(image)
    for name, address in patch["cave"]["cals"].items():
        width = 2 if name == "LC_IPW" else 1
        configured[address:address + width] = values[name].to_bytes(width, "little")
    return bytes(checksum.correct_checksums(configured)[0])


def _valid_checksums(image):
    status = checksum.checksum_status(image)
    assert all(status[name] for name in ("boot", "program", "cal")), status


@pytest.mark.parametrize("variant,suffix,ignition_id", VARIANTS)
def test_launch_v11_clean_install_and_dependency_contract(variant, suffix, ignition_id):
    patches = patch_ms41.load_patches()
    old_id, new_id = "launch_control_v7" + suffix, "launch_control_v11" + suffix
    old, new = patches[old_id], patches[new_id]
    assert old["deprecated"]
    assert patches["launch_control_v10" + suffix]["superseded_by"] == new_id
    assert not new.get("deprecated") and new["version"] == "V11"
    assert old_id in new["supersedes"]
    assert new["requires"] == old["requires"] == [ignition_id]
    assert {name: new["cave"]["cals"][name] for name in old["cave"]["cals"]} == old["cave"]["cals"]
    assert set(new["cave"]["cals"]) - set(old["cave"]["cals"]) == {
        "LC_FUEL_HYST_B", "LC_FUEL_HYST_A",
    }
    assert patch_ms41.validate_splices(new) == []

    stock = ref(variant)
    catalogue = {row["id"]: row for row in patch_service.available_patches(stock)}
    assert new_id in catalogue and old_id not in catalogue
    with pytest.raises(patch_service.PatchError, match="requires"):
        patch_service.build_image(stock, [new_id])
    with pytest.raises(patch_service.PatchError, match="deprecated"):
        patch_service.build_image(stock, [ignition_id, old_id])

    installed, _ = patch_service.build_image(stock, [ignition_id, new_id])
    assert patch_ms41.is_applied(installed, new)
    assert not patch_ms41.is_applied(installed, old)
    assert installed[0x4000:0x6000] == stock[0x4000:0x6000]
    for name, address in new["cave"]["cals"].items():
        width = 2 if name == "LC_IPW" else 1
        assert installed[address:address + width] == stock[address:address + width]
    _valid_checksums(installed)
    with pytest.raises(patch_service.PatchError, match="remove the dependent patch"):
        patch_service.revert_patch(installed, ignition_id)
    ignition_only = patch_service.revert_patch(installed, new_id)
    assert ignition_only == patch_service.build_image(stock, [ignition_id])[0]


@pytest.mark.parametrize("variant,suffix,ignition_id", VARIANTS)
@pytest.mark.parametrize("predecessor", ["v7", "v8", "v9", "v10"])
@pytest.mark.parametrize("switch,ipw", [(0, 2247), (2, 0xFFFF)])
def test_configured_launch_upgrade_and_both_removals_preserve_tuning(
        variant, suffix, ignition_id, predecessor, switch, ipw):
    patches = patch_ms41.load_patches()
    old_id, new_id = "launch_control_" + predecessor + suffix, "launch_control_v11" + suffix
    old, new = patches[old_id], patches[new_id]
    stock = ref(variant)
    historical, _ = patch_ms41.build(stock, [ignition_id, old_id], allow_deprecated=True)
    source = _configured(historical, old, switch, ipw)
    assert patch_ms41.is_applied(source, old)

    catalogue = {row["id"]: row for row in patch_service.available_patches(source)}
    assert catalogue[old_id]["deprecated"] and catalogue[old_id]["removable"]
    assert [item["id"] for item in catalogue[new_id]["legacy"]] == [old_id]
    ignition_only = _configured(patch_service.build_image(stock, [ignition_id])[0], old, switch, ipw)
    assert patch_service.revert_patch(source, old_id) == ignition_only

    upgraded, _ = patch_service.build_image(source, [new_id])
    clean, _ = patch_service.build_image(stock, [ignition_id, new_id])
    assert upgraded == _configured(clean, new, switch, ipw)
    assert patch_ms41.is_applied(upgraded, new)
    assert not patch_ms41.is_applied(upgraded, old)
    assert patch_ms41.is_applied(upgraded, patches[ignition_id])
    catalogue = {row["id"]: row for row in patch_service.available_patches(upgraded)}
    assert old_id not in catalogue
    assert catalogue[new_id]["installed"] and catalogue[new_id]["legacy"] == []
    groups = {group["patch_id"]: group for group in patch_service.editable_parameters(upgraded)}
    assert {parameter["id"] for parameter in groups[new_id]["parameters"]} == set(new["cave"]["cals"])
    _valid_checksums(upgraded)
    restored = patch_service.revert_patch(upgraded, new_id)
    assert restored == ignition_only
    _valid_checksums(restored)


@pytest.mark.parametrize("variant,suffix,ignition_id", VARIANTS)
@pytest.mark.parametrize("predecessor", ["v7", "v8", "v9", "v10"])
def test_launch_v11_rejects_corrupted_predecessor_cave(variant, suffix, ignition_id, predecessor):
    patches = patch_ms41.load_patches()
    old_id, new_id = "launch_control_" + predecessor + suffix, "launch_control_v11" + suffix
    historical, _ = patch_ms41.build(ref(variant), [ignition_id, old_id], allow_deprecated=True)
    source = bytearray(historical)
    source[patches[old_id]["cave"]["base"] + 20] ^= 1
    before = bytes(source)
    with pytest.raises(patch_service.PatchError):
        patch_service.build_image(source, [new_id])
    assert bytes(source) == before


@pytest.mark.parametrize("variant,suffix,ignition_id", VARIANTS)
@pytest.mark.parametrize("fuel_b,fuel_a", [(0xFF, 0xFF), (0, 0xFE), (2, 1)])
def test_launch_fuel_hysteresis_survives_initial_install_remove_and_reinstall(
        variant, suffix, ignition_id, fuel_b, fuel_a):
    patch_id = "launch_control_v11" + suffix
    patch = patch_ms41.load_patches()[patch_id]
    cals = patch["cave"]["cals"]
    source = bytearray(ref(variant))
    source[cals["LC_FUEL_HYST_B"]] = fuel_b
    source[cals["LC_FUEL_HYST_A"]] = fuel_a
    source = bytes(checksum.correct_checksums(source)[0])
    installed = patch_service.build_image(source, [ignition_id, patch_id])[0]
    assert installed[cals["LC_FUEL_HYST_B"]] == fuel_b
    assert installed[cals["LC_FUEL_HYST_A"]] == fuel_a
    assert patch_ms41.is_applied(installed, patch)

    configured = patch_service.apply_parameter_changes(
        installed, patch_id, {"LC_FUEL_HYST_A": "64", "LC_HYST": "96"})[0]
    removed = patch_service.revert_patch(configured, patch_id)
    assert removed[cals["LC_FUEL_HYST_B"]] == fuel_b
    assert removed[cals["LC_FUEL_HYST_A"]] == 2
    assert removed[cals["LC_HYST"]] == 3
    reinstalled = patch_service.build_image(removed, [patch_id])[0]
    assert reinstalled == configured
    _valid_checksums(reinstalled)
