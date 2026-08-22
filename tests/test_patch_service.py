import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import patch_service
import ecu_info
import softbsl_service
from engines.patcher import patch_ms41
from tests.conftest import ref
import pytest


DEPRECATED_PATCH_CASES = tuple(
    (variant, patch_id)
    for patch_id, patch in patch_service.definitions().items()
    if patch.get("deprecated")
    for variant in patch_ms41.patch_targets(patch)
)


def _synthetic_patch_base(ecu_id, cal_family, compatibility_id):
    image = bytearray(b"\xFF" * 262144)
    image[0x6025:0x602C] = ecu_id.encode("ascii")
    image[0x1400E:0x14016] = (cal_family + "000000").encode("ascii")
    for address in (0x6007, 0x6013, 0x601F):
        image[address:address + 4] = compatibility_id.encode("ascii")
    for address in (0x1400C, 0x14016, 0x14026, 0x14036):
        image[address:address + 4] = compatibility_id.encode("ascii")
    return bytes(image)


def _calguard_image(base):
    return patch_service.build_image(
        base, ["softbsl_loader", "cal_guard"])


def _deprecated_fixture(base, patch_ids):
    return patch_ms41.build(
        base, patch_ids, allow_deprecated=True)


def test_base_version_of_ms41_3():
    assert patch_service.base_version(ref("MS41.3")) == "MS41.3"


def test_patch_catalogue_does_not_inherit_unverified_variant_hooks():
    assert patch_service.base_version(
        _synthetic_patch_base("1429861", "41", "0641")) == "MS41.0"
    assert patch_service.base_version(
        _synthetic_patch_base("1429373", "59", "0659")) is None
    assert patch_service.base_version(
        _synthetic_patch_base("1438068", "60", "0960")) is None


def test_available_patches_filters_by_version():
    avail = patch_service.available_patches(ref("MS41.3"))
    ids = {p["id"] for p in avail}
    assert "cal_guard" in ids
    assert "vanos_minrpm_v2_ms410" not in ids        # MS41.0 target, filtered out
    assert "ignition_cut" not in ids                 # V1 deprecated, superseded by V9
    assert "ignition_cut_v2" not in ids              # V2 deprecated, superseded by V9
    assert "ignition_cut_v3" not in ids              # V3 deprecated (gated on speed, not rpm)
    assert "ignition_cut_v5" not in ids              # field-failed V5 is remove-only
    assert "launch_control_v2" not in ids             # field-failed V2 is remove-only
    assert "ignition_cut_v6" not in ids              # field-failed V6 is remove-only
    assert "ignition_cut_v7" not in ids              # V7 is remove-only
    assert "ignition_cut_v9" in ids                  # independent shared-request revision
    assert "launch_control_v3" not in ids            # V3 is retained only for removal
    assert "launch_control_v4" not in ids            # overlapping V4 is remove-only
    assert "launch_control_v5" not in ids            # V5 is remove-only
    assert "launch_control_v7" in ids                # independent ignition requester
    assert "door_0x43" not in ids                    # installer-only Soft-BSL bootstrap
    assert "alphan_failsafe" in ids
    assert len(avail) == 7                            # the 7 user-facing MS41.3 patches
    cg = next(p for p in avail if p["id"] == "cal_guard")
    assert cg["ok"] is True and cg["title"] and cg["target"] == "MS41.3"
    assert cg["user_description"] == (
        "Exact compatibility guard with a short K-Line boot-recovery window, "
        "relocated outside BMW AIF programming history.")
    assert "@0x" not in cg["user_description"]
    amd = next(p for p in avail if p["id"] == "amd_flash")
    assert amd["version"] == "V2"
    assert amd["status"] == "TESTED"
    assert amd["tested"] is True
    alphan = next(p for p in avail if p["id"] == "alphan_failsafe")
    assert alphan["status"] == "EMULATOR VERIFIED - ON-CAR TEST REQUIRED"
    assert alphan["tested"] is False
    assert next(p for p in avail if p["id"] == "softbsl_loader")["tested"] is False
    assert next(p for p in avail if p["id"] == "door_magic")["tested"] is True
    ic = next(p for p in avail if p["id"] == "ignition_cut_v9")
    assert ic["status"] == "EMULATOR VERIFIED - ON-CAR TEST REQUIRED"
    assert ic["tested"] is False
    assert ic["legacy"] == []                          # clean ref base has no predecessor installed


def test_every_active_patch_has_a_release_facing_description():
    definitions = patch_service.definitions()
    active = [patch for patch in definitions.values() if not patch.get("deprecated")]

    assert active
    assert all(patch.get("user_description", "").strip() for patch in active)


def test_patch_versions_are_badges_not_title_text():
    expected = {
        "alphan_failsafe_v1": "V1",
        "alphan_failsafe_v2": "V2",
        "alphan_failsafe": "V3",
        "amd_flash": "V2",
        "cal_guard_v1": "V1",
        "cal_guard_v2": "V2",
        "cal_guard_v4": "V4",
        "cal_guard": "V5",
        "door_magic": "V2",
        "door_magic_ms410": "V2",
        "door_magic_ms411": "V2",
        "ignition_cut": "V1",
        "ignition_cut_v2": "V2",
        "ignition_cut_v3": "V3",
        "ignition_cut_v4": "V4",
        "ignition_cut_v5": "V5",
        "ignition_cut_v6": "V6",
        "ignition_cut_v7": "V7",
        "ignition_cut_v7_ms410": "V7",
        "ignition_cut_v7_ms411": "V7",
        "ignition_cut_v8": "V8",
        "ignition_cut_v8_ms410": "V8",
        "ignition_cut_v8_ms411": "V8",
        "ignition_cut_v8_ms412": "V8",
        "ignition_cut_v9": "V9",
        "ignition_cut_v9_ms410": "V9",
        "ignition_cut_v9_ms411": "V9",
        "ignition_cut_v9_ms412": "V9",
        "launch_control": "V1",
        "launch_control_v2": "V2",
        "launch_control_v2_ms412": "V2",
        "launch_control_v3": "V3",
        "launch_control_v3_ms412": "V3",
        "launch_control_v4": "V4",
        "launch_control_v4_ms410": "V4",
        "launch_control_v4_ms411": "V4",
        "launch_control_v4_ms412": "V4",
        "launch_control_v5": "V5",
        "launch_control_v6": "V6",
        "launch_control_v6_ms410": "V6",
        "launch_control_v6_ms411": "V6",
        "launch_control_v6_ms412": "V6",
        "launch_control_v7": "V7",
        "launch_control_v7_ms410": "V7",
        "launch_control_v7_ms411": "V7",
        "launch_control_v7_ms412": "V7",
        "softbsl_loader_relocated_v1": "V1",
        "softbsl_loader_v2": "V2",
        "softbsl_loader_v10": "V10",
        "softbsl_loader": "V11",
        "vanos_minrpm_ms410": "V1",
        "vanos_minrpm_v2_ms410": "V2",
    }
    definitions = patch_service.definitions()

    for patch_id, version in expected.items():
        patch = definitions[patch_id]
        assert patch["version"] == version
        assert version.lower() not in patch["title"].lower()


def test_available_patches_exposes_only_latest_ms412_ports():
    avail = patch_service.available_patches(ref("MS41.2"))
    ids = {p["id"] for p in avail}

    assert ids == {
        "amd_flash", "cal_guard", "door_magic",
        "ignition_cut_v9_ms412", "launch_control_v7_ms412", "softbsl_loader",
    }
    assert all(p["target"] == "MS41.2" for p in avail)
    assert not any(p.get("deprecated") for p in avail)


def test_softbsl_bootstrap_definition_is_kept_but_hidden_from_patch_catalogue():
    bootstrap_ids = {
        "MS41.0": "door_0x43_ms410",
        "MS41.1": "door_0x43_ms411",
        "MS41.2": "door_0x43",
        "MS41.3": "door_0x43",
    }
    assert set(bootstrap_ids.values()) <= patch_service.definitions().keys()
    for variant, bootstrap_id in bootstrap_ids.items():
        assert bootstrap_id not in {
            patch["id"] for patch in patch_service.available_patches(ref(variant))
        }


@pytest.mark.parametrize(
    "variant,door_id",
    [
        ("MS41.0", "door_magic_ms410"),
        ("MS41.1", "door_magic_ms411"),
        ("MS41.2", "door_magic"),
        ("MS41.3", "door_magic"),
    ],
)
def test_persistent_door_requires_the_resident_loader(variant, door_id):
    with pytest.raises(patch_ms41.PatchError, match="requires 'softbsl_loader'"):
        patch_service.build_image(ref(variant), [door_id])

    image, _ = patch_service.build_image(
        ref(variant), ["softbsl_loader", door_id])
    definitions = patch_service.definitions()
    assert patch_service.is_applied(image, definitions["softbsl_loader"])
    assert patch_service.is_applied(image, definitions[door_id])
    assert patch_service.installed_dependents(
        image, "softbsl_loader") == [door_id]
    with pytest.raises(patch_ms41.PatchError, match="remove the dependent patch"):
        patch_service.revert_patch(image, "softbsl_loader")


def test_ms410_vanos_v2_is_selectable_and_retains_hardware_tested_logic():
    avail = patch_service.available_patches(ref("MS41.0"))
    assert [patch["id"] for patch in avail] == [
        "amd_flash", "cal_guard", "door_magic_ms410",
        "ignition_cut_v9_ms410", "launch_control_v7_ms410",
        "softbsl_loader", "vanos_minrpm_v2_ms410",
    ]
    patch = next(
        item for item in avail if item["id"] == "vanos_minrpm_v2_ms410"
    )
    definition = patch_service.definitions()["vanos_minrpm_v2_ms410"]
    assert patch["version"] == "V2"
    assert patch["status"] == "TESTED"
    assert patch["tested"] is True
    assert definition["tested"] is True
    assert "vehicle-tested runtime behavior" in patch["user_description"]
    assert "UNTESTED" not in patch["title"]


def test_ms410_vanos_v1_is_detected_upgraded_and_removed_checksum_safely():
    definitions = patch_service.definitions()
    v1 = definitions["vanos_minrpm_ms410"]
    v2 = definitions["vanos_minrpm_v2_ms410"]
    assert [edit["data"] for edit in v2["edits"][:2]] == [
        edit["data"] for edit in v1["edits"][:2]
    ]
    legacy = bytearray(ref("MS41.0"))
    for edit in v1["edits"]:
        payload = bytes.fromhex(edit["data"])
        offset = edit["off"]
        legacy[offset:offset + len(payload)] = payload
    legacy = bytes(legacy)

    assert patch_service.is_applied(legacy, v1)
    assert not patch_service.is_applied(legacy, v2)
    assert patch_ms41.checksum.checksum_status(legacy)["program"] is False
    available = {
        patch["id"]: patch
        for patch in patch_service.available_patches(legacy)
    }
    assert available["vanos_minrpm_ms410"]["deprecated"] is True
    assert available["vanos_minrpm_v2_ms410"]["legacy"] == [{
        "id": "vanos_minrpm_ms410",
        "label": "V1 invalid-checksum revision",
    }]

    upgraded, log = patch_service.build_image(
        legacy, ["vanos_minrpm_v2_ms410"]
    )
    assert patch_service.is_applied(upgraded, v2)
    assert not patch_service.is_applied(upgraded, v1)
    assert upgraded[0x17008:0x17010] == b"VANOSRT3"
    assert any("exact prior revision" in line for line in log)
    assert all(
        patch_ms41.checksum.checksum_status(upgraded)[name]
        for name in ("boot", "program", "cal")
    )

    cleaned = patch_service.revert_patch(legacy, "vanos_minrpm_ms410")
    assert cleaned == ref("MS41.0")


def test_ms410_vanos_v2_isolated_build_and_revert_have_valid_checksums():
    stock = ref("MS41.0")
    definition = patch_service.definitions()["vanos_minrpm_v2_ms410"]
    installed, _log = patch_service.build_image(
        stock, ["vanos_minrpm_v2_ms410"]
    )

    assert installed[0x17008:0x17010] == b"VANOSRT3"
    assert patch_service.is_applied(installed, definition)
    assert all(
        patch_ms41.checksum.checksum_status(installed)[name]
        for name in ("boot", "program", "cal")
    )
    assert patch_service.revert_patch(
        installed, "vanos_minrpm_v2_ms410"
    ) == stock


def test_ms411_exposes_current_feature_ports_and_softbsl():
    assert [patch["id"] for patch in patch_service.available_patches(ref("MS41.1"))] == [
        "amd_flash", "cal_guard", "door_magic_ms411",
        "ignition_cut_v9_ms411", "launch_control_v7_ms411",
        "softbsl_loader", "vanos_minrpm_ms411",
    ]


@pytest.mark.parametrize(
    "variant,ignition_id,launch_id",
    [
        ("MS41.0", "ignition_cut_v9_ms410", "launch_control_v7_ms410"),
        ("MS41.1", "ignition_cut_v9_ms411", "launch_control_v7_ms411"),
    ],
)
def test_older_launch_ports_require_and_compose_with_matching_ignition_port(
        variant, ignition_id, launch_id):
    with pytest.raises(patch_ms41.PatchError, match="requires"):
        patch_service.build_image(ref(variant), [launch_id])

    image, _log = patch_service.build_image(
        ref(variant), [ignition_id, launch_id])
    available = {
        patch["id"]: patch
        for patch in patch_service.available_patches(image)
    }
    assert available[ignition_id]["installed"] is True
    assert available[launch_id]["installed"] is True


@pytest.mark.parametrize("variant", ["MS41.0", "MS41.1", "MS41.2", "MS41.3"])
def test_amd_patch_can_be_built_and_saved_but_not_sent_to_intel(
        variant, tmp_path):
    image, _log = patch_service.build_image(ref(variant), ["amd_flash"])
    assert ecu_info.image_chip_family(image) == "amd"

    saved = tmp_path / f"{variant}-amd.bin"
    saved.write_bytes(image)
    assert saved.read_bytes() == image

    with pytest.raises(softbsl_service.FlashFamilyMismatchError):
        softbsl_service.validate_flash_image_family(
            image, "intel", write_bootloader=False)


@pytest.mark.parametrize("variant", ["MS41.0", "MS41.1", "MS41.2", "MS41.3"])
def test_stock_intel_image_is_not_sent_to_amd_geometry(variant):
    image = ref(variant)
    assert ecu_info.image_chip_family(image) == "intel"
    with pytest.raises(softbsl_service.FlashFamilyMismatchError):
        softbsl_service.validate_flash_image_family(
            image, "amd", write_bootloader=False)


def test_available_patches_flags_legacy_v1_installed():
    base, _ = _deprecated_fixture(ref("MS41.3"), ["ignition_cut"])
    ic = next(p for p in patch_service.available_patches(base) if p["id"] == "ignition_cut_v9")
    assert [(l["id"], l["label"]) for l in ic["legacy"]] == [("ignition_cut", "V1")]
    assert ic["installed"] is False                    # V9's own edits aren't present


def test_available_patches_flags_legacy_v2_installed():
    base, _ = _deprecated_fixture(ref("MS41.3"), ["ignition_cut_v2"])
    ic = next(p for p in patch_service.available_patches(base) if p["id"] == "ignition_cut_v9")
    assert [(l["id"], l["label"]) for l in ic["legacy"]] == [("ignition_cut_v2", "V2")]
    assert ic["installed"] is False


@pytest.mark.parametrize(
    "variant,ignition_id",
    [("MS41.2", "ignition_cut_v9_ms412"), ("MS41.3", "ignition_cut_v9")],
)
def test_field_failed_v6_is_remove_only_and_v9_replaces_it(
        variant, ignition_id):
    failed_image, _ = _deprecated_fixture(ref(variant), ["ignition_cut_v6"])
    available = {
        patch["id"]: patch for patch in patch_service.available_patches(failed_image)
    }

    failed = available["ignition_cut_v6"]
    assert failed["installed"] is True
    assert failed["deprecated"] is True
    assert failed["removable"] is True
    assert failed["ok"] is False
    assert available[ignition_id]["legacy"] == [
        {"id": "ignition_cut_v6", "label": "V6"}
    ]

    cleaned = patch_service.revert_patch(failed_image, "ignition_cut_v6")
    upgraded, _ = patch_service.build_image(cleaned, [ignition_id])
    definitions = patch_service.definitions()
    assert not patch_service.is_applied(upgraded, definitions["ignition_cut_v6"])
    assert patch_service.is_applied(upgraded, definitions[ignition_id])


def test_launch_control_requires_and_composes_with_ignition_cut_v9():
    # V7 requires the shared V9 cut engine to be installed, but its runtime
    # ignition request is independent of V9's standalone CUTSW state.
    with pytest.raises(patch_ms41.PatchError):
        patch_service.build_image(ref("MS41.3"), ["launch_control_v7"])
    out, _ = patch_service.build_image(
        ref("MS41.3"), ["ignition_cut_v9", "launch_control_v7"])
    assert len(out) == patch_ms41.FULL
    v9_base, _ = patch_service.build_image(ref("MS41.3"), ["ignition_cut_v9"])
    out2, _ = patch_service.build_image(v9_base, ["launch_control_v7"])
    assert len(out2) == patch_ms41.FULL
    assert "launch_control_v7" not in patch_service.collisions(["ignition_cut_v9"])
    assert "ignition_cut_v9" not in patch_service.collisions(["launch_control_v7"])


@pytest.mark.parametrize(
    "variant,ignition_id,launch_id",
    [
        ("MS41.0", "ignition_cut_v9_ms410", "launch_control_v7_ms410"),
        ("MS41.1", "ignition_cut_v9_ms411", "launch_control_v7_ms411"),
        ("MS41.2", "ignition_cut_v9_ms412", "launch_control_v7_ms412"),
        ("MS41.3", "ignition_cut_v9", "launch_control_v7"),
    ],
)
def test_installed_launch_blocks_removing_its_ignition_dependency(
        variant, ignition_id, launch_id):
    combined, _ = patch_service.build_image(
        ref(variant), [ignition_id, launch_id]
    )
    available = {patch["id"]: patch for patch in patch_service.available_patches(combined)}

    assert patch_service.installed_dependents(combined, ignition_id) == [launch_id]
    assert available[ignition_id]["required_by"] == [launch_id]
    assert available[ignition_id]["removable"] is False
    with pytest.raises(patch_ms41.PatchError, match="remove the dependent patch"):
        patch_service.revert_patch(combined, ignition_id)

    without_launch = patch_service.revert_patch(combined, launch_id)
    without_ignition = patch_service.revert_patch(without_launch, ignition_id)
    definitions = patch_service.definitions()
    assert not patch_service.is_applied(without_ignition, definitions[launch_id])
    assert not patch_service.is_applied(without_ignition, definitions[ignition_id])
    removed_status = patch_ms41.checksum.checksum_status(without_ignition)
    assert all(removed_status[name] for name in ("boot", "program", "cal"))

    rebuilt, _ = patch_service.build_image(
        without_ignition, [ignition_id, launch_id]
    )
    assert patch_service.is_applied(rebuilt, definitions[ignition_id])
    assert patch_service.is_applied(rebuilt, definitions[launch_id])
    rebuilt_status = patch_ms41.checksum.checksum_status(rebuilt)
    assert all(rebuilt_status[name] for name in ("boot", "program", "cal"))


@pytest.mark.parametrize(
    "variant,ignition_id,old_id,new_id",
    [
        ("MS41.3", "ignition_cut_v9",
         "launch_control_v3", "launch_control_v7"),
        ("MS41.2", "ignition_cut_v9_ms412",
         "launch_control_v3_ms412", "launch_control_v7_ms412"),
    ],
)
def test_launch_v3_is_remove_only_and_v4_replaces_it(
        variant, ignition_id, old_id, new_id):
    old_image, _ = _deprecated_fixture(
        ref(variant), ["ignition_cut_v6", old_id])
    available = {patch["id"]: patch for patch in patch_service.available_patches(old_image)}

    assert available[old_id]["installed"] is True
    assert available[old_id]["deprecated"] is True
    assert available[old_id]["removable"] is True
    assert available[new_id]["legacy"] == [{"id": old_id, "label": "V3"}]

    cleaned = patch_service.revert_patch(old_image, old_id)
    cleaned = patch_service.revert_patch(cleaned, "ignition_cut_v6")
    upgraded, _ = patch_service.build_image(cleaned, [ignition_id, new_id])
    assert patch_service.is_applied(upgraded, patch_service.definitions()[new_id])


def test_overlapping_ms413_launch_v4_is_detected_removed_and_replaced():
    definitions = patch_service.definitions()
    stock = ref("MS41.3")
    old_image, _ = _deprecated_fixture(
        stock, ["ignition_cut_v7", "launch_control_v4"])

    # A configured legacy image has real Launch values in the boost table.
    # Migration must preserve them for an explicit boost-table review rather
    # than copying them to, or silently erasing them from, the new controls.
    old_values = bytes((0x01, 0x00, 0x00, 0x7D, 0x05, 0x28, 0x80, 0x80))
    old_image = bytearray(old_image)
    old_image[0x1752C:0x17534] = old_values
    old_image, _details = patch_ms41.checksum.correct_checksums(old_image)

    available = {
        patch["id"]: patch
        for patch in patch_service.available_patches(old_image)
    }
    legacy = available["launch_control_v4"]
    assert legacy["installed"] is True
    assert legacy["deprecated"] is True
    assert legacy["removable"] is True
    assert available["launch_control_v7"]["legacy"] == [
        {"id": "launch_control_v4", "label": "V4"}
    ]

    cleaned = patch_service.revert_patch(
        old_image, "launch_control_v4")
    cleaned_status = patch_ms41.checksum.checksum_status(cleaned)
    assert cleaned_status["boot"]
    assert cleaned_status["program"]
    assert cleaned_status["cal"]
    upgraded, _log = patch_service.build_image(
        cleaned, ["ignition_cut_v9", "launch_control_v7"])
    assert not patch_service.is_applied(
        upgraded, definitions["launch_control_v4"])
    assert patch_service.is_applied(upgraded, definitions["launch_control_v7"])
    assert upgraded[0x1752C:0x17534] == old_values
    assert upgraded[0x107E0:0x107EB] == b"\xFF" * 11


@pytest.mark.parametrize(
    "variant,patch_id",
    [("MS41.3", "ignition_cut_v5"),
     ("MS41.3", "launch_control_v2"),
     ("MS41.2", "launch_control_v2_ms412")],
)
def test_field_failed_patch_is_surfaced_remove_only(variant, patch_id):
    dependencies = ["ignition_cut_v5"] if patch_id.startswith("launch_control") else []
    failed_image, _ = _deprecated_fixture(ref(variant), dependencies + [patch_id])
    available = {patch["id"]: patch for patch in patch_service.available_patches(failed_image)}
    failed = available[patch_id]
    assert failed["installed"] is True
    assert failed["deprecated"] is True
    assert failed["removable"] is True
    assert failed["ok"] is False


def test_installed_deprecated_patch_is_surfaced_for_removal():
    # a deprecated patch (v4) that is INSTALLED is surfaced as a removable row (not hidden), so it can be
    # reverted straight from the tab without first selecting its successor:
    base, _ = _deprecated_fixture(ref("MS41.3"), ["ignition_cut_v4"])
    avail = {p["id"]: p for p in patch_service.available_patches(base)}
    assert "ignition_cut_v4" in avail
    v4 = avail["ignition_cut_v4"]
    assert v4["installed"] is True and v4.get("deprecated") is True and v4.get("removable") is True
    # a deprecated patch that is NOT installed stays hidden:
    assert "ignition_cut_v4" not in {p["id"] for p in patch_service.available_patches(ref("MS41.3"))}


@pytest.mark.parametrize(
    ("legacy_id", "legacy_label"),
    [
        ("cal_guard_v1", "V1 broad-version guard"),
        ("cal_guard_v2", "V2 unsafe odd-word guard"),
    ],
)
def test_deprecated_calguard_is_detected_removed_and_replaced_by_v5(
        legacy_id, legacy_label):
    stock = ref("MS41.2")
    legacy_image, _ = _deprecated_fixture(stock, [legacy_id])
    available = {
        patch["id"]: patch for patch in patch_service.available_patches(legacy_image)
    }

    assert available[legacy_id]["installed"] is True
    assert available[legacy_id]["removable"] is True
    assert available["cal_guard"]["installed"] is False
    assert available["cal_guard"]["version"] == "V5"
    assert available["cal_guard"]["status"] == "EMULATOR VERIFIED - BENCH TEST REQUIRED"
    assert available["cal_guard"]["tested"] is False
    assert available["cal_guard"]["legacy"] == [{
        "id": legacy_id,
        "label": legacy_label,
    }]

    directly_upgraded, direct_log = _calguard_image(legacy_image)
    definitions = patch_service.definitions()
    assert patch_service.is_applied(directly_upgraded, definitions["cal_guard"])
    assert not patch_service.is_applied(
        directly_upgraded, definitions[legacy_id])
    assert any("removed exact predecessor" in line for line in direct_log)

    cleaned = patch_service.revert_patch(legacy_image, legacy_id)
    upgraded, _ = _calguard_image(cleaned)
    assert not patch_service.is_applied(upgraded, definitions[legacy_id])
    assert patch_service.is_applied(upgraded, definitions["cal_guard"])


@pytest.mark.parametrize(
    "variant,patch_id",
    DEPRECATED_PATCH_CASES,
    ids=lambda value: str(value).replace(".", ""),
)
def test_every_deprecated_patch_remains_detectable_and_uninstallable(
        variant, patch_id):
    definitions = patch_service.definitions()
    definition = definitions[patch_id]
    selected = [*definition.get("requires", []), patch_id]
    installed_image, _ = _deprecated_fixture(ref(variant), selected)
    available = {
        patch["id"]: patch
        for patch in patch_service.available_patches(installed_image)
    }

    installed = available[patch_id]
    assert installed["installed"] is True
    assert installed["deprecated"] is True
    assert installed["removable"] is True

    cleaned = patch_service.revert_patch(installed_image, patch_id)
    assert not patch_service.is_applied(cleaned, definition)
    for edit in definition["edits"]:
        offset = edit["off"]
        expected = bytes.fromhex(edit["expect"])
        assert cleaned[offset:offset + len(expected)] == expected

    checksum_status = patch_ms41.checksum.checksum_status(cleaned)
    baseline_status = patch_ms41.checksum.checksum_status(ref(variant))
    assert all(
        checksum_status[name] or not was_valid
        for name, was_valid in baseline_status.items()
    )


def test_legacy_loader_is_removable_and_new_loader_replaces_it():
    stock = ref("MS41.3")
    legacy_image, _ = _deprecated_fixture(stock, ["softbsl_loader_legacy"])
    avail = {p["id"]: p for p in patch_service.available_patches(legacy_image)}

    legacy = avail["softbsl_loader_legacy"]
    assert legacy["installed"] is True
    assert legacy["deprecated"] is True
    assert legacy["removable"] is True

    replacement = avail["softbsl_loader"]
    assert replacement["installed"] is False
    assert replacement["legacy"] == [
        {"id": "softbsl_loader_legacy", "label": "descriptor-overlapping loader"}
    ]

    cleaned = patch_service.revert_patch(legacy_image, "softbsl_loader_legacy")
    legacy_definition = patch_service.definitions()["softbsl_loader_legacy"]
    for edit in legacy_definition["edits"]:
        off = edit["off"]
        expected = bytes.fromhex(edit["expect"])
        assert cleaned[off:off + len(expected)] == expected
    checksum_status = patch_ms41.checksum.checksum_status(cleaned)
    assert checksum_status["boot"] and checksum_status["program"] and checksum_status["cal"]
    assert checksum_status["prog_disabled"]
    relocated_image, _ = patch_service.build_image(cleaned, ["softbsl_loader"])
    relocated = {p["id"]: p for p in patch_service.available_patches(relocated_image)}
    assert "softbsl_loader_legacy" not in relocated
    assert relocated["softbsl_loader"]["installed"] is True


def test_non_triggering_relocated_v1_is_removable_and_superseded():
    stock = ref("MS41.3")
    broken_image, _ = _deprecated_fixture(
        stock, ["softbsl_loader_relocated_v1"])
    avail = {p["id"]: p for p in patch_service.available_patches(broken_image)}

    broken = avail["softbsl_loader_relocated_v1"]
    assert broken["installed"] is True
    assert broken["deprecated"] is True
    assert broken["removable"] is True
    assert "BROKEN" in broken["status"]

    replacement = avail["softbsl_loader"]
    assert replacement["installed"] is False
    assert replacement["legacy"] == [{
        "id": "softbsl_loader_relocated_v1",
        "label": "non-triggering relocated loader v1",
    }]

    cleaned = patch_service.revert_patch(
        broken_image, "softbsl_loader_relocated_v1")
    current, _ = patch_service.build_image(cleaned, ["softbsl_loader"])
    patches = patch_service.definitions()
    assert not patch_service.is_applied(
        current, patches["softbsl_loader_relocated_v1"])
    assert patch_service.is_applied(current, patches["softbsl_loader"])


def test_broken_alphan_v2_is_detected_and_directly_upgraded_to_v3():
    stock = ref("MS41.3")
    v2_image, _ = _deprecated_fixture(stock, ["alphan_failsafe_v2"])
    available = {
        patch["id"]: patch for patch in patch_service.available_patches(v2_image)
    }

    assert available["alphan_failsafe_v2"]["installed"] is True
    assert available["alphan_failsafe_v2"]["deprecated"] is True
    replacement = available["alphan_failsafe"]
    assert replacement["installed"] is False
    assert replacement["version"] == "V3"
    assert replacement["legacy"] == [{
        "id": "alphan_failsafe_v2",
        "label": "V2 broken SS1v2 load-domain integration",
    }]

    upgraded, log = patch_service.build_image(v2_image, ["alphan_failsafe"])
    definitions = patch_service.definitions()
    assert patch_service.is_applied(upgraded, definitions["alphan_failsafe"])
    assert not patch_service.is_applied(
        upgraded, definitions["alphan_failsafe_v2"])
    assert sum("exact prior revision" in line for line in log) == 2


def test_softbsl_v2_is_detected_and_directly_upgraded_to_v11():
    stock = ref("MS41.3")
    v2_image, _ = _deprecated_fixture(stock, ["softbsl_loader_v2"])
    available = {
        patch["id"]: patch for patch in patch_service.available_patches(v2_image)
    }

    v2 = available["softbsl_loader_v2"]
    assert v2["installed"] is True
    assert v2["deprecated"] is True
    assert v2["removable"] is True

    replacement = available["softbsl_loader"]
    assert replacement["installed"] is False
    assert replacement["version"] == "V11"
    assert replacement["legacy"] == [{
        "id": "softbsl_loader_v2",
        "label": "V2 prior loader",
    }]

    current, log = patch_service.build_image(v2_image, ["softbsl_loader"])
    definitions = patch_service.definitions()
    assert not patch_service.is_applied(current, definitions["softbsl_loader_v2"])
    assert patch_service.is_applied(current, definitions["softbsl_loader"])
    assert "removed exact predecessor softbsl_loader_v2" in log


def _historical_door_fixture(base, loader_id=None):
    definitions = patch_service.definitions()
    definitions["door_magic"] = {
        **definitions["door_magic"],
        "requires": [],
    }
    selected = [loader_id, "door_magic"] if loader_id else ["door_magic"]
    return patch_ms41.build(
        base, selected, patches=definitions,
        allow_deprecated=loader_id is not None,
    )[0]


def test_historical_v2_loader_cannot_be_removed_while_the_door_uses_it():
    image = _historical_door_fixture(
        ref("MS41.3"), "softbsl_loader_v2")
    available = {
        patch["id"]: patch for patch in patch_service.available_patches(image)
    }

    assert patch_service.installed_dependents(
        image, "softbsl_loader_v2") == ["door_magic"]
    assert available["softbsl_loader_v2"]["removable"] is False
    assert available["softbsl_loader_v2"]["required_by"] == ["door_magic"]
    assert available["softbsl_loader"]["legacy"] == [{
        "id": "softbsl_loader_v2",
        "label": "V2 prior loader",
        "required_by": ["door_magic"],
    }]
    assert available["door_magic"]["ok"] is False
    assert "MISSING REQUIRED PATCH" in available["door_magic"]["badge"]
    with pytest.raises(patch_ms41.PatchError, match="dependent patch"):
        patch_service.revert_patch(image, "softbsl_loader_v2")

    repaired, _ = patch_service.build_image(image, ["softbsl_loader"])
    repaired_available = {
        patch["id"]: patch
        for patch in patch_service.available_patches(repaired)
    }
    assert repaired_available["softbsl_loader"]["installed"] is True
    assert repaired_available["door_magic"]["ok"] is True


def test_orphan_door_is_flagged_and_blocks_unrelated_builds_until_repaired():
    image = _historical_door_fixture(ref("MS41.3"))
    door = next(
        patch for patch in patch_service.available_patches(image)
        if patch["id"] == "door_magic"
    )

    assert door["installed"] is True
    assert door["ok"] is False
    assert door["badge"] == "MISSING REQUIRED PATCH: softbsl_loader"
    with pytest.raises(
            patch_ms41.PatchError, match="installed patch dependency is incomplete"):
        patch_service.build_image(image, ["amd_flash"])

    repaired, _ = patch_service.build_image(image, ["softbsl_loader"])
    repaired_door = next(
        patch for patch in patch_service.available_patches(repaired)
        if patch["id"] == "door_magic"
    )
    assert repaired_door["ok"] is True


def test_collisions_flags_shared_cave():
    assert "alphan_failsafe" in patch_service.collisions(["door_0x43"])
    assert "cal_guard" not in patch_service.collisions(["door_0x43"])   # no overlap


def test_build_image_delegates_and_composes():
    out, log = _calguard_image(ref("MS41.3"))
    assert len(out) == patch_ms41.FULL
    assert isinstance(log, list)


def test_build_image_raises_on_collision():
    with pytest.raises(patch_ms41.PatchError):
        patch_service.build_image(ref("MS41.3"), ["door_0x43", "alphan_failsafe"])


def test_build_image_can_stack_a_new_patch_onto_an_already_patched_base():
    # Regression: building used to be handed the ALREADY-installed patch id too (the GUI
    # checkbox for it is checked, for status display), which made build_image try to
    # re-apply it and fail the expect-byte check. The GUI now excludes installed ids from
    # the selection it sends; this pins the underlying expectation at the service layer:
    # a non-overlapping NEW patch must build cleanly onto a base that already has one applied.
    base, _ = patch_service.build_image(
        ref("MS41.3"), ["softbsl_loader"])
    out, log = patch_service.build_image(base, ["cal_guard"])
    assert len(out) == patch_ms41.FULL
    avail = patch_service.available_patches(out)
    cg = next(p for p in avail if p["id"] == "cal_guard")
    sb = next(p for p in avail if p["id"] == "softbsl_loader")
    assert cg["installed"] and sb["installed"]


def test_revert_legacy_v1_then_apply_v9():
    v1_base, _ = _deprecated_fixture(ref("MS41.3"), ["ignition_cut"])
    cleaned = patch_service.revert_patch(v1_base, "ignition_cut")
    corrected_stock, _ = patch_ms41.checksum.correct_checksums(ref("MS41.3"))
    assert cleaned == bytes(corrected_stock)             # stock bytes plus corrected program CRC

    ic = next(p for p in patch_service.available_patches(cleaned) if p["id"] == "ignition_cut_v9")
    assert ic["legacy"] == []                            # V1 gone, no longer flagged

    out, log = patch_service.build_image(cleaned, ["ignition_cut_v9"])
    assert len(out) == patch_ms41.FULL


def test_revert_legacy_v2_then_apply_v9():
    v2_base, _ = _deprecated_fixture(ref("MS41.3"), ["ignition_cut_v2"])
    cleaned = patch_service.revert_patch(v2_base, "ignition_cut_v2")
    corrected_stock, _ = patch_ms41.checksum.correct_checksums(ref("MS41.3"))
    assert cleaned == bytes(corrected_stock)             # stock bytes plus corrected program CRC
    out, _ = patch_service.build_image(cleaned, ["ignition_cut_v9"])
    assert len(out) == patch_ms41.FULL


def test_revert_patch_raises_if_not_applied():
    with pytest.raises(patch_ms41.PatchError):
        patch_service.revert_patch(ref("MS41.3"), "ignition_cut")


def test_available_patches_flags_needs_boot():
    avail = {p["id"]: p for p in patch_service.available_patches(ref("MS41.3"))}
    assert avail["cal_guard"]["needs_boot"] is True          # writes SA1
    assert avail["ignition_cut_v9"]["needs_boot"] is False   # program region


def test_boot_write_patches_detected_in_built_image():
    stock = ref("MS41.3")
    v9_img, _ = patch_service.build_image(stock, ["ignition_cut_v9"])
    assert patch_service.boot_write_patches_in(v9_img) == []     # program patch, nothing in boot
    cg_img, _ = _calguard_image(stock)
    assert patch_service.boot_write_patches_in(cg_img) == [
        "cal_guard", "softbsl_loader"]


def test_missing_boot_patches_gate():
    stock = ref("MS41.3")
    cg_img, _ = _calguard_image(stock)
    # ECU is stock -> the required boot patches would be dropped by a DS2 flash
    assert patch_service.missing_boot_patches(cg_img, stock) == [
        "cal_guard", "softbsl_loader"]
    # ECU already has cal_guard -> nothing would be lost
    assert patch_service.missing_boot_patches(cg_img, cg_img) == []
    # no cached full read -> can't confirm, treat as missing
    assert patch_service.missing_boot_patches(cg_img, None) == [
        "cal_guard", "softbsl_loader"]
    # a pure program patch is never gated
    v9_img, _ = patch_service.build_image(stock, ["ignition_cut_v9"])
    assert patch_service.missing_boot_patches(v9_img, None) == []


SA1_LO, SA1_HI = 0x4000, 0x6000


def _sa1(img):
    """The 8 KB SA1 window a live read_memory_range(0x0000, 0x2000) returns."""
    return bytes(img[SA1_LO:SA1_HI])


def test_missing_boot_patches_accepts_sa1_slice_evidence():
    # The corrected gate confirms against the ECU's live 8 KB SA1 window, not a 256 KB read.
    stock = ref("MS41.3")
    cg_img, _ = _calguard_image(stock)
    assert patch_service.missing_boot_patches(cg_img, _sa1(stock)) == [
        "cal_guard", "softbsl_loader"]
    assert patch_service.missing_boot_patches(cg_img, _sa1(cg_img)) == []
    assert len(_sa1(stock)) == patch_service.SA1_LEN == 0x2000


def test_sa1_window_normalizes_full_slice_and_none():
    full = ref("MS41.3")
    assert patch_service.sa1_window(full)             == full[SA1_LO:SA1_HI]  # full read auto-sliced
    assert patch_service.sa1_window(_sa1(full))       == full[SA1_LO:SA1_HI]  # already a window
    assert patch_service.sa1_window(None)             is None
    assert patch_service.sa1_window(b"\x00" * 0x1800) is None                 # too short → fail-safe


def test_gate_ignores_per_unit_sa1_drift_outside_patch_edits():
    # Per-unit descriptor/coding/boot-CRC bytes differ between ECUs (62-110 B across the REF
    # bins) but lie OUTSIDE every boot patch's edits — they must not read as "patch missing".
    stock = ref("MS41.3")
    cg_img, _ = _calguard_image(stock)
    sa1 = bytearray(_sa1(cg_img))
    sa1[0x5D00 - SA1_LO] ^= 0xFF        # per-unit byte outside every current patch edit
    assert patch_service.missing_boot_patches(cg_img, bytes(sa1)) == []          # still present
    sa1[0x5C76 - SA1_LO] ^= 0xFF        # now corrupt a byte inside CalGuard's trampoline
    assert patch_service.missing_boot_patches(cg_img, bytes(sa1)) == ["cal_guard"]


def test_gate_does_not_block_variant_conversion_images():
    # A factory/community full read carries none of the 3 boot patches, so the boot gate is a
    # no-op regardless of evidence — the gate must add no full-read demand to conversions.
    for key in ("MS41.1", "MS41.2or3", "MS41.3clean"):
        img = ref(key)
        assert patch_service.boot_write_patches_in(img) == []
        assert patch_service.missing_boot_patches(img, None)      == []
        assert patch_service.missing_boot_patches(img, _sa1(img)) == []


def test_softbsl_loader_present_via_live_sa1():
    stock = ref("MS41.3")
    sb_img, _ = patch_service.build_image(stock, ["softbsl_loader"])
    assert patch_service.boot_write_patches_in(sb_img) == ["softbsl_loader"]
    assert patch_service.missing_boot_patches(sb_img, _sa1(sb_img)) == []             # ECU has it
    assert patch_service.missing_boot_patches(sb_img, _sa1(stock))  == ["softbsl_loader"]


def test_sparse_boot_gate_reads_only_applied_patch_edit_bytes():
    stock = ref("MS41.3")
    cg_img, _ = _calguard_image(stock)
    ranges = patch_service.boot_patch_read_ranges(cg_img)

    assert ranges == [
        (0x4412, 0x442E),
        (0x4942, 0x4948),
        (0x55A0, 0x55A4),
        (0x5C32, 0x5C80),
        (0x5C8C, 0x5C9A),
        (0x5CA0, 0x5CB2),
        (0x5F8C, 0x6000),
    ]
    assert sum(hi - lo for lo, hi in ranges) == 264

    live_patched = [(lo, cg_img[lo:hi]) for lo, hi in ranges]
    live_stock = [(lo, stock[lo:hi]) for lo, hi in ranges]
    assert patch_service.missing_boot_patches_sparse(cg_img, live_patched) == []
    assert patch_service.missing_boot_patches_sparse(cg_img, live_stock) == [
        "cal_guard", "softbsl_loader"]


def test_sparse_boot_gate_fails_safe_on_incomplete_evidence():
    stock = ref("MS41.3")
    cg_img, _ = _calguard_image(stock)
    ranges = patch_service.boot_patch_read_ranges(cg_img)
    only_first_range = [(ranges[0][0], cg_img[ranges[0][0]:ranges[0][1]])]

    assert patch_service.missing_boot_patches_sparse(cg_img, only_first_range) == [
        "cal_guard", "softbsl_loader"]
