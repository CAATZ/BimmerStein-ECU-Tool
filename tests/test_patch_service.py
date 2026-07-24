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
    assert "vanos_minrpm_ms410" not in ids           # MS41.0 target, filtered out
    assert "ignition_cut" not in ids                 # V1 deprecated, superseded by V7
    assert "ignition_cut_v2" not in ids              # V2 deprecated, superseded by V7
    assert "ignition_cut_v3" not in ids              # V3 deprecated (gated on speed, not rpm)
    assert "ignition_cut_v5" not in ids              # field-failed V5 is remove-only
    assert "launch_control_v2" not in ids             # field-failed V2 is remove-only
    assert "ignition_cut_v6" not in ids              # field-failed V6 is remove-only
    assert "ignition_cut_v7" in ids                  # final-stage P1L revision
    assert "launch_control_v3" not in ids            # V3 is retained only for removal
    assert "launch_control_v4" not in ids            # overlapping V4 is remove-only
    assert "launch_control_v5" in ids                # relocated independent soft + hard limiter
    assert "door_0x43" not in ids                    # installer-only Soft-BSL bootstrap
    assert len(avail) == 7                            # the 7 user-facing MS41.3 patches
    cg = next(p for p in avail if p["id"] == "cal_guard")
    assert cg["ok"] is True and cg["title"] and cg["target"] == "MS41.3"
    assert "recoverable flash-listen mode" in cg["user_description"]
    assert "@0x" not in cg["user_description"]
    assert next(p for p in avail if p["id"] == "alphan_failsafe")["tested"] is False
    ic = next(p for p in avail if p["id"] == "ignition_cut_v7")
    assert ic["status"] == "EMULATOR VERIFIED - ON-CAR TEST REQUIRED"
    assert ic["tested"] is False
    assert ic["legacy"] == []                          # clean ref base has no predecessor installed


def test_every_active_patch_has_a_release_facing_description():
    definitions = patch_service.definitions()
    active = [patch for patch in definitions.values() if not patch.get("deprecated")]

    assert active
    assert all(patch.get("user_description", "").strip() for patch in active)


def test_available_patches_exposes_only_latest_ms412_ports():
    avail = patch_service.available_patches(ref("MS41.2"))
    ids = {p["id"] for p in avail}

    assert ids == {
        "amd_flash", "cal_guard", "door_magic",
        "ignition_cut_v7", "launch_control_v4_ms412", "softbsl_loader",
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


def test_ms410_vanos_patch_is_selectable_and_hardware_tested():
    avail = patch_service.available_patches(ref("MS41.0"))
    assert [patch["id"] for patch in avail] == [
        "amd_flash", "cal_guard", "door_magic_ms410",
        "ignition_cut_v7_ms410", "launch_control_v4_ms410",
        "softbsl_loader", "vanos_minrpm_ms410",
    ]
    patch = next(item for item in avail if item["id"] == "vanos_minrpm_ms410")
    definition = patch_service.definitions()["vanos_minrpm_ms410"]
    assert patch["status"] == "TESTED"
    assert patch["tested"] is True
    assert definition["tested"] is True
    assert "proven working on-car" in definition["verification"]
    assert "UNTESTED" not in patch["title"]


def test_ms411_exposes_current_feature_ports_and_softbsl():
    assert [patch["id"] for patch in patch_service.available_patches(ref("MS41.1"))] == [
        "amd_flash", "cal_guard", "door_magic_ms411",
        "ignition_cut_v7_ms411", "launch_control_v4_ms411",
        "softbsl_loader", "vanos_minrpm_ms411",
    ]


@pytest.mark.parametrize(
    "variant,ignition_id,launch_id",
    [
        ("MS41.0", "ignition_cut_v7_ms410", "launch_control_v4_ms410"),
        ("MS41.1", "ignition_cut_v7_ms411", "launch_control_v4_ms411"),
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
    base, _ = patch_service.build_image(ref("MS41.3"), ["ignition_cut"])
    ic = next(p for p in patch_service.available_patches(base) if p["id"] == "ignition_cut_v7")
    assert [(l["id"], l["label"]) for l in ic["legacy"]] == [("ignition_cut", "V1")]
    assert ic["installed"] is False                    # V7's own edits aren't present


def test_available_patches_flags_legacy_v2_installed():
    base, _ = patch_service.build_image(ref("MS41.3"), ["ignition_cut_v2"])
    ic = next(p for p in patch_service.available_patches(base) if p["id"] == "ignition_cut_v7")
    assert [(l["id"], l["label"]) for l in ic["legacy"]] == [("ignition_cut_v2", "V2")]
    assert ic["installed"] is False


@pytest.mark.parametrize("variant", ["MS41.2", "MS41.3"])
def test_field_failed_v6_is_remove_only_and_v7_replaces_it(variant):
    failed_image, _ = patch_service.build_image(ref(variant), ["ignition_cut_v6"])
    available = {
        patch["id"]: patch for patch in patch_service.available_patches(failed_image)
    }

    failed = available["ignition_cut_v6"]
    assert failed["installed"] is True
    assert failed["deprecated"] is True
    assert failed["removable"] is True
    assert failed["ok"] is False
    assert available["ignition_cut_v7"]["legacy"] == [
        {"id": "ignition_cut_v6", "label": "V6"}
    ]

    cleaned = patch_service.revert_patch(failed_image, "ignition_cut_v6")
    upgraded, _ = patch_service.build_image(cleaned, ["ignition_cut_v7"])
    definitions = patch_service.definitions()
    assert not patch_service.is_applied(upgraded, definitions["ignition_cut_v6"])
    assert patch_service.is_applied(upgraded, definitions["ignition_cut_v7"])


def test_launch_control_requires_and_composes_with_ignition_cut_v7():
    # launch_control_v5's ignition mode feeds V7's final-stage hook (fd5a.7), so it REQUIRES V7 and
    # COMPOSES with it (no byte collision) instead of conflicting.
    # 1) building launch_control_v5 alone (no V7) fails the requires check:
    with pytest.raises(patch_ms41.PatchError):
        patch_service.build_image(ref("MS41.3"), ["launch_control_v5"])
    # 2) V7 + launch_control_v5 build together cleanly (no overlapping bytes):
    out, _ = patch_service.build_image(ref("MS41.3"), ["ignition_cut_v7", "launch_control_v5"])
    assert len(out) == patch_ms41.FULL
    # 3) onto a base that ALREADY has V7 installed, launch_control_v5 alone builds:
    v7_base, _ = patch_service.build_image(ref("MS41.3"), ["ignition_cut_v7"])
    out2, _ = patch_service.build_image(v7_base, ["launch_control_v5"])
    assert len(out2) == patch_ms41.FULL
    # 4) they do NOT byte-collide (composition, not mutual exclusion):
    assert "launch_control_v5" not in patch_service.collisions(["ignition_cut_v7"])
    assert "ignition_cut_v7" not in patch_service.collisions(["launch_control_v5"])


@pytest.mark.parametrize(
    "variant,ignition_id,launch_id",
    [
        ("MS41.0", "ignition_cut_v7_ms410", "launch_control_v4_ms410"),
        ("MS41.1", "ignition_cut_v7_ms411", "launch_control_v4_ms411"),
        ("MS41.2", "ignition_cut_v7", "launch_control_v4_ms412"),
        ("MS41.3", "ignition_cut_v7", "launch_control_v5"),
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


@pytest.mark.parametrize(
    "variant,old_id,new_id",
    [
        ("MS41.3", "launch_control_v3", "launch_control_v5"),
        ("MS41.2", "launch_control_v3_ms412", "launch_control_v4_ms412"),
    ],
)
def test_launch_v3_is_remove_only_and_v4_replaces_it(variant, old_id, new_id):
    old_image, _ = patch_service.build_image(
        ref(variant), ["ignition_cut_v6", old_id])
    available = {patch["id"]: patch for patch in patch_service.available_patches(old_image)}

    assert available[old_id]["installed"] is True
    assert available[old_id]["deprecated"] is True
    assert available[old_id]["removable"] is True
    assert available[new_id]["legacy"] == [{"id": old_id, "label": "V3"}]

    cleaned = patch_service.revert_patch(old_image, old_id)
    cleaned = patch_service.revert_patch(cleaned, "ignition_cut_v6")
    upgraded, _ = patch_service.build_image(cleaned, ["ignition_cut_v7", new_id])
    assert patch_service.is_applied(upgraded, patch_service.definitions()[new_id])


def test_overlapping_ms413_launch_v4_is_detected_removed_and_replaced():
    definitions = patch_service.definitions()
    stock = ref("MS41.3")
    old_image, _ = patch_service.build_image(
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
    assert available["launch_control_v5"]["legacy"] == [
        {"id": "launch_control_v4", "label": "V4"}
    ]

    cleaned = patch_service.revert_patch(
        old_image, "launch_control_v4")
    cleaned_status = patch_ms41.checksum.checksum_status(cleaned)
    assert cleaned_status["boot"]
    assert cleaned_status["program"]
    assert cleaned_status["cal"]
    upgraded, _log = patch_service.build_image(cleaned, ["launch_control_v5"])
    assert not patch_service.is_applied(
        upgraded, definitions["launch_control_v4"])
    assert patch_service.is_applied(upgraded, definitions["launch_control_v5"])
    assert upgraded[0x1752C:0x17534] == old_values
    assert upgraded[0x107E0:0x107E8] == b"\xFF" * 8


@pytest.mark.parametrize(
    "variant,patch_id",
    [("MS41.3", "ignition_cut_v5"),
     ("MS41.3", "launch_control_v2"),
     ("MS41.2", "launch_control_v2_ms412")],
)
def test_field_failed_patch_is_surfaced_remove_only(variant, patch_id):
    dependencies = ["ignition_cut_v5"] if patch_id.startswith("launch_control") else []
    failed_image, _ = patch_service.build_image(ref(variant), dependencies + [patch_id])
    available = {patch["id"]: patch for patch in patch_service.available_patches(failed_image)}
    failed = available[patch_id]
    assert failed["installed"] is True
    assert failed["deprecated"] is True
    assert failed["removable"] is True
    assert failed["ok"] is False


def test_installed_deprecated_patch_is_surfaced_for_removal():
    # a deprecated patch (v4) that is INSTALLED is surfaced as a removable row (not hidden), so it can be
    # reverted straight from the tab without first selecting its successor:
    base, _ = patch_service.build_image(ref("MS41.3"), ["ignition_cut_v4"])
    avail = {p["id"]: p for p in patch_service.available_patches(base)}
    assert "ignition_cut_v4" in avail
    v4 = avail["ignition_cut_v4"]
    assert v4["installed"] is True and v4.get("deprecated") is True and v4.get("removable") is True
    # a deprecated patch that is NOT installed stays hidden:
    assert "ignition_cut_v4" not in {p["id"] for p in patch_service.available_patches(ref("MS41.3"))}


def test_calguard_v1_is_detected_removed_and_replaced_by_v2():
    stock = ref("MS41.2")
    v1_image, _ = patch_service.build_image(stock, ["cal_guard_v1"])
    available = {
        patch["id"]: patch for patch in patch_service.available_patches(v1_image)
    }

    assert available["cal_guard_v1"]["installed"] is True
    assert available["cal_guard_v1"]["removable"] is True
    assert available["cal_guard"]["installed"] is False
    assert available["cal_guard"]["status"] == "V2"
    assert available["cal_guard"]["legacy"] == [{
        "id": "cal_guard_v1",
        "label": "V1 broad-version guard",
    }]

    directly_upgraded, direct_log = patch_service.build_image(
        v1_image, ["cal_guard"])
    definitions = patch_service.definitions()
    assert patch_service.is_applied(directly_upgraded, definitions["cal_guard"])
    assert not patch_service.is_applied(
        directly_upgraded, definitions["cal_guard_v1"])
    assert any("exact prior revision" in line for line in direct_log)

    cleaned = patch_service.revert_patch(v1_image, "cal_guard_v1")
    upgraded, _ = patch_service.build_image(cleaned, ["cal_guard"])
    assert not patch_service.is_applied(upgraded, definitions["cal_guard_v1"])
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
    installed_image, _ = patch_service.build_image(ref(variant), selected)
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
    legacy_image, _ = patch_service.build_image(stock, ["softbsl_loader_legacy"])
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
    broken_image, _ = patch_service.build_image(
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


def test_collisions_flags_shared_cave():
    assert "alphan_failsafe" in patch_service.collisions(["door_0x43"])
    assert "cal_guard" not in patch_service.collisions(["door_0x43"])   # no overlap


def test_build_image_delegates_and_composes():
    out, log = patch_service.build_image(ref("MS41.3"), ["cal_guard"])
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
    base, _ = patch_service.build_image(ref("MS41.3"), ["cal_guard"])
    out, log = patch_service.build_image(base, ["softbsl_loader"])
    assert len(out) == patch_ms41.FULL
    avail = patch_service.available_patches(out)
    cg = next(p for p in avail if p["id"] == "cal_guard")
    sb = next(p for p in avail if p["id"] == "softbsl_loader")
    assert cg["installed"] and sb["installed"]


def test_revert_legacy_v1_then_apply_v7():
    v1_base, _ = patch_service.build_image(ref("MS41.3"), ["ignition_cut"])
    cleaned = patch_service.revert_patch(v1_base, "ignition_cut")
    corrected_stock, _ = patch_ms41.checksum.correct_checksums(ref("MS41.3"))
    assert cleaned == bytes(corrected_stock)             # stock bytes plus corrected program CRC

    ic = next(p for p in patch_service.available_patches(cleaned) if p["id"] == "ignition_cut_v7")
    assert ic["legacy"] == []                            # V1 gone, no longer flagged

    # V7 now applies cleanly after the legacy cave/splice is restored.
    out, log = patch_service.build_image(cleaned, ["ignition_cut_v7"])
    assert len(out) == patch_ms41.FULL


def test_revert_legacy_v2_then_apply_v7():
    v2_base, _ = patch_service.build_image(ref("MS41.3"), ["ignition_cut_v2"])
    cleaned = patch_service.revert_patch(v2_base, "ignition_cut_v2")
    corrected_stock, _ = patch_ms41.checksum.correct_checksums(ref("MS41.3"))
    assert cleaned == bytes(corrected_stock)             # stock bytes plus corrected program CRC
    out, _ = patch_service.build_image(cleaned, ["ignition_cut_v7"])
    assert len(out) == patch_ms41.FULL


def test_revert_patch_raises_if_not_applied():
    with pytest.raises(patch_ms41.PatchError):
        patch_service.revert_patch(ref("MS41.3"), "ignition_cut")


def test_available_patches_flags_needs_boot():
    avail = {p["id"]: p for p in patch_service.available_patches(ref("MS41.3"))}
    assert avail["cal_guard"]["needs_boot"] is True          # writes SA1
    assert avail["ignition_cut_v7"]["needs_boot"] is False   # program region


def test_boot_write_patches_detected_in_built_image():
    stock = ref("MS41.3")
    v7_img, _ = patch_service.build_image(stock, ["ignition_cut_v7"])
    assert patch_service.boot_write_patches_in(v7_img) == []     # program patch, nothing in boot
    cg_img, _ = patch_service.build_image(stock, ["cal_guard"])
    assert patch_service.boot_write_patches_in(cg_img) == ["cal_guard"]


def test_missing_boot_patches_gate():
    stock = ref("MS41.3")
    cg_img, _ = patch_service.build_image(stock, ["cal_guard"])
    # ECU is stock -> cal_guard's SA1 bytes would be dropped by a DS2 flash
    assert patch_service.missing_boot_patches(cg_img, stock) == ["cal_guard"]
    # ECU already has cal_guard -> nothing would be lost
    assert patch_service.missing_boot_patches(cg_img, cg_img) == []
    # no cached full read -> can't confirm, treat as missing
    assert patch_service.missing_boot_patches(cg_img, None) == ["cal_guard"]
    # a pure program patch is never gated
    v7_img, _ = patch_service.build_image(stock, ["ignition_cut_v7"])
    assert patch_service.missing_boot_patches(v7_img, None) == []


SA1_LO, SA1_HI = 0x4000, 0x6000


def _sa1(img):
    """The 8 KB SA1 window a live read_memory_range(0x0000, 0x2000) returns."""
    return bytes(img[SA1_LO:SA1_HI])


def test_missing_boot_patches_accepts_sa1_slice_evidence():
    # The corrected gate confirms against the ECU's live 8 KB SA1 window, not a 256 KB read.
    stock = ref("MS41.3")
    cg_img, _ = patch_service.build_image(stock, ["cal_guard"])
    assert patch_service.missing_boot_patches(cg_img, _sa1(stock))  == ["cal_guard"]
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
    cg_img, _ = patch_service.build_image(stock, ["cal_guard"])
    sa1 = bytearray(_sa1(cg_img))
    sa1[0x5D00 - SA1_LO] ^= 0xFF        # descriptor byte, outside cal_guard's 0x493A / 0x5E10-0x5FC3
    assert patch_service.missing_boot_patches(cg_img, bytes(sa1)) == []          # still present
    sa1[0x5E10 - SA1_LO] ^= 0xFF        # now corrupt a byte INSIDE cal_guard's cave
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
    cg_img, _ = patch_service.build_image(stock, ["cal_guard"])
    ranges = patch_service.boot_patch_read_ranges(cg_img)

    assert ranges == [(0x493A, 0x4942), (0x5E10, 0x5FC4)]
    assert sum(hi - lo for lo, hi in ranges) == 444       # not the legacy 8192-byte SA1 read

    live_patched = [(lo, cg_img[lo:hi]) for lo, hi in ranges]
    live_stock = [(lo, stock[lo:hi]) for lo, hi in ranges]
    assert patch_service.missing_boot_patches_sparse(cg_img, live_patched) == []
    assert patch_service.missing_boot_patches_sparse(cg_img, live_stock) == ["cal_guard"]


def test_sparse_boot_gate_fails_safe_on_incomplete_evidence():
    stock = ref("MS41.3")
    cg_img, _ = patch_service.build_image(stock, ["cal_guard"])
    ranges = patch_service.boot_patch_read_ranges(cg_img)
    only_first_range = [(ranges[0][0], cg_img[ranges[0][0]:ranges[0][1]])]

    assert patch_service.missing_boot_patches_sparse(cg_img, only_first_range) == ["cal_guard"]
