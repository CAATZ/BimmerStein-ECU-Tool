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


@pytest.mark.parametrize("marker", [b"\xa5\x5a\x42\xbd", b"\xa5\x5a\x54\xab"])
def test_loader_bank_marker_does_not_break_dependency_detection(marker):
    patches = patch_service.definitions()
    image = bytearray(_synthetic_patch_base("1406464", "12", "0912"))
    for pid in ("softbsl_loader", "cal_guard", "door_magic"):
        for edit in patches[pid]["edits"]:
            data = bytes.fromhex(edit["data"])
            image[edit["off"]:edit["off"] + len(data)] = data
    image[0x5FFC:0x6000] = marker
    original = bytes(image)
    rows = {row["id"]: row for row in patch_service.available_patches(image)}
    assert rows["softbsl_loader"]["installed"]
    for pid in ("cal_guard", "door_magic"):
        assert rows[pid]["installed"]
        assert "MISSING REQUIRED PATCH" not in rows[pid]["badge"]
    assert bytes(image) == original
    reads = [(lo, bytes(image[lo:hi]))
             for lo, hi in patch_service.boot_patch_read_ranges(image)]
    assert patch_service.missing_boot_patches(image, image) == []
    assert patch_service.missing_boot_patches_sparse(image, reads) == []
    other_bank = bytearray(image)
    other_bank[0x5FFC:0x6000] = (b"\xa5\x5a\x54\xab" if marker[2] == 0x42
                               else b"\xa5\x5a\x42\xbd")
    assert patch_service.missing_boot_patches(image, other_bank) == ["softbsl_loader"]
    other_reads = [(lo, bytes(other_bank[lo:hi]))
                   for lo, hi in patch_service.boot_patch_read_ranges(image)]
    assert patch_service.missing_boot_patches_sparse(image, other_reads) == ["softbsl_loader"]

    loader = patches["softbsl_loader"]
    for invalid in (b"\xff" * 4, b"\xa5\x5a\x54\x00", b"\xa5\x5a\x58\xa7"):
        image[0x5FFC:0x6000] = invalid
        assert not patch_ms41.is_applied(image, loader)
    image[0x5FFC:0x6000] = marker
    for edit in loader["edits"]:
        if edit["off"] == 0x5FFC:
            continue
        image[edit["off"]] ^= 1
        assert not patch_ms41.is_applied(image, loader)
        image[edit["off"]] ^= 1


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
    assert "ignition_cut" not in ids                 # V1 deprecated, superseded by V11
    assert "ignition_cut_v2" not in ids              # V2 deprecated, superseded by V11
    assert "ignition_cut_v3" not in ids              # V3 deprecated (gated on speed, not rpm)
    assert "ignition_cut_v5" not in ids              # field-failed V5 is remove-only
    assert "launch_control_v2" not in ids             # field-failed V2 is remove-only
    assert "ignition_cut_v6" not in ids              # field-failed V6 is remove-only
    assert "ignition_cut_v7" not in ids              # V7 is remove-only
    assert "ignition_cut_v11" in ids                  # independent shared-request revision
    assert "launch_control_v3" not in ids            # V3 is retained only for removal
    assert "launch_control_v4" not in ids            # overlapping V4 is remove-only
    assert "launch_control_v5" not in ids            # V5 is remove-only
    assert "launch_control_v11" in ids                # independent ignition requester
    assert "door_0x43" not in ids                    # installer-only Soft-BSL bootstrap
    assert "top_ds2_guard" not in ids               # automatic TOP-image protection
    assert "alphan_failsafe" in ids
    assert len(avail) == 7                            # the 7 user-facing MS41.3 patches
    cg = next(p for p in avail if p["id"] == "cal_guard")
    assert cg["ok"] is True and cg["title"] and cg["target"] == "MS41.3"
    assert cg["user_description"] == patch_service.definitions()["cal_guard"]["user_description"]
    assert "@0x" not in cg["user_description"]
    amd = next(p for p in avail if p["id"] == "amd_flash")
    assert amd["version"] == "V4"
    assert amd["status"] == "IMPLEMENTED"
    assert amd["tested"] is False  # Production V4 still needs exact-image bench qualification.
    alphan = next(p for p in avail if p["id"] == "alphan_failsafe")
    assert alphan["status"] == "TESTED"
    assert alphan["tested"] is True
    assert next(p for p in avail if p["id"] == "softbsl_loader")["tested"] is False
    assert next(p for p in avail if p["id"] == "door_magic")["tested"] is True
    ic = next(p for p in avail if p["id"] == "ignition_cut_v11")
    assert ic["status"] == "OFFLINE EXACT-BYTE VERIFIED - ON-CAR TEST REQUIRED"
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
        "amd_flash": "V4",
        "amd_flash_v3": "V3",
        "cal_guard_v1": "V1",
        "cal_guard_v2": "V2",
        "cal_guard_v4": "V4",
        "cal_guard": "V6",
        "cal_guard_v5": "V5",
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
        "ignition_cut_v10": "V10",
        "ignition_cut_v11": "V11",
        "ignition_cut_v9_ms410": "V9",
        "ignition_cut_v10_ms410": "V10",
        "ignition_cut_v9_ms411": "V9",
        "ignition_cut_v10_ms411": "V10",
        "ignition_cut_v9_ms412": "V9",
        "ignition_cut_v10_ms412": "V10",
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
        "launch_control_v8": "V8",
        "launch_control_v9": "V9",
        "launch_control_v10": "V10",
        "launch_control_v11": "V11",
        "launch_control_v7_ms410": "V7",
        "launch_control_v8_ms410": "V8",
        "launch_control_v9_ms410": "V9",
        "launch_control_v10_ms410": "V10",
        "launch_control_v11_ms410": "V11",
        "launch_control_v7_ms411": "V7",
        "launch_control_v8_ms411": "V8",
        "launch_control_v9_ms411": "V9",
        "launch_control_v10_ms411": "V10",
        "launch_control_v11_ms411": "V11",
        "launch_control_v7_ms412": "V7",
        "launch_control_v8_ms412": "V8",
        "launch_control_v9_ms412": "V9",
        "launch_control_v10_ms412": "V10",
        "launch_control_v11_ms412": "V11",
        "softbsl_loader_relocated_v1": "V1",
        "softbsl_loader_v2": "V2",
        "softbsl_loader_v3_bench_failed": "V3",
        "softbsl_loader_v9": "V9",
        "softbsl_loader_v10": "V10",
        "softbsl_loader": "V12",
        "softbsl_loader_v11": "V11",
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
        "ignition_cut_v10_ms412", "launch_control_v11_ms412", "softbsl_loader",
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
        "ignition_cut_v10_ms410", "launch_control_v11_ms410",
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
        "ignition_cut_v10_ms411", "launch_control_v11_ms411",
        "softbsl_loader", "vanos_minrpm_ms411",
    ]


@pytest.mark.parametrize(
    "variant,ignition_id,launch_id",
    [
        ("MS41.0", "ignition_cut_v10_ms410", "launch_control_v11_ms410"),
        ("MS41.1", "ignition_cut_v10_ms411", "launch_control_v11_ms411"),
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
def test_amd_patch_image_is_allowed_on_intel_when_boot_is_preserved(
        variant, tmp_path):
    image, _log = patch_service.build_image(ref(variant), ["amd_flash"])
    assert ecu_info.image_chip_family(image) == "amd"

    saved = tmp_path / f"{variant}-amd.bin"
    saved.write_bytes(image)
    assert saved.read_bytes() == image

    assert softbsl_service.validate_flash_image_family(
        image, "intel", write_bootloader=False) == "amd"


@pytest.mark.parametrize("variant", ["MS41.0", "MS41.1", "MS41.2", "MS41.3"])
def test_stock_intel_image_is_allowed_on_amd_when_boot_is_preserved(variant):
    image = ref(variant)
    assert ecu_info.image_chip_family(image) == "intel"
    assert softbsl_service.validate_flash_image_family(
        image, "amd", write_bootloader=False) == "intel"


def test_available_patches_flags_legacy_v1_installed():
    base, _ = _deprecated_fixture(ref("MS41.3"), ["ignition_cut"])
    ic = next(p for p in patch_service.available_patches(base) if p["id"] == "ignition_cut_v11")
    assert [(l["id"], l["label"]) for l in ic["legacy"]] == [("ignition_cut", "V1")]
    assert ic["installed"] is False                    # V11's own edits aren't present


def test_available_patches_flags_legacy_v2_installed():
    base, _ = _deprecated_fixture(ref("MS41.3"), ["ignition_cut_v2"])
    ic = next(p for p in patch_service.available_patches(base) if p["id"] == "ignition_cut_v11")
    assert [(l["id"], l["label"]) for l in ic["legacy"]] == [("ignition_cut_v2", "V2")]
    assert ic["installed"] is False


@pytest.mark.parametrize(
    "variant,ignition_id",
    [("MS41.2", "ignition_cut_v10_ms412"), ("MS41.3", "ignition_cut_v11")],
)
def test_field_failed_v6_is_remove_only_and_v10_replaces_it(
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


def test_launch_control_requires_and_composes_with_ignition_cut_v10():
    # Launch V10 requires the shared V11 cut engine, but its runtime
    # ignition request is independent of V11's standalone CUTSW state.
    with pytest.raises(patch_ms41.PatchError):
        patch_service.build_image(ref("MS41.3"), ["launch_control_v11"])
    out, _ = patch_service.build_image(
        ref("MS41.3"), ["ignition_cut_v11", "launch_control_v11"])
    assert len(out) == patch_ms41.FULL
    ignition_base, _ = patch_service.build_image(ref("MS41.3"), ["ignition_cut_v11"])
    out2, _ = patch_service.build_image(ignition_base, ["launch_control_v11"])
    assert len(out2) == patch_ms41.FULL
    assert "launch_control_v11" not in patch_service.collisions(["ignition_cut_v11"])
    assert "ignition_cut_v11" not in patch_service.collisions(["launch_control_v11"])


@pytest.mark.parametrize(
    "variant,ignition_id,launch_id",
    [
        ("MS41.0", "ignition_cut_v10_ms410", "launch_control_v11_ms410"),
        ("MS41.1", "ignition_cut_v10_ms411", "launch_control_v11_ms411"),
        ("MS41.2", "ignition_cut_v10_ms412", "launch_control_v11_ms412"),
        ("MS41.3", "ignition_cut_v11", "launch_control_v11"),
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
        ("MS41.3", "ignition_cut_v11",
         "launch_control_v3", "launch_control_v11"),
        ("MS41.2", "ignition_cut_v10_ms412",
         "launch_control_v3_ms412", "launch_control_v11_ms412"),
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
    assert available["launch_control_v11"]["legacy"] == [
        {"id": "launch_control_v4", "label": "V4"}
    ]

    cleaned = patch_service.revert_patch(
        old_image, "launch_control_v4")
    cleaned_status = patch_ms41.checksum.checksum_status(cleaned)
    assert cleaned_status["boot"]
    assert cleaned_status["program"]
    assert cleaned_status["cal"]
    upgraded, _log = patch_service.build_image(
        cleaned, ["ignition_cut_v11", "launch_control_v11"])
    assert not patch_service.is_applied(
        upgraded, definitions["launch_control_v4"])
    assert patch_service.is_applied(upgraded, definitions["launch_control_v11"])
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
    assert available["cal_guard"]["version"] == "V6"
    assert available["cal_guard"]["status"] == "EXPERIMENTAL"
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

    assert patch_service.is_applied(installed_image, definition)
    if patch_id == "amd_flash_v3":
        # The current driver is the same four exact edits; native startup
        # anchors distinguish the retained fixture, not a separate driver.
        assert patch_id not in available
        installed = available["amd_flash"]
        assert installed["deprecated"] is False
    else:
        installed = available[patch_id]
        assert installed["deprecated"] is True
    assert installed["installed"] is True
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
    assert replacement["version"] == "V12"
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


def test_upgrade_checks_dependencies_on_resulting_bytes(monkeypatch):
    # Historical V9 upgrade retained Launch V4 but removed its required V7.
    definitions = patch_ms41.load_patches()
    definitions["ignition_cut_v9"] = dict(
        definitions["ignition_cut_v9"], deprecated=False)
    monkeypatch.setattr(patch_ms41, "load_patches", lambda: definitions)
    image, _ = _deprecated_fixture(
        ref("MS41.3"), ["ignition_cut_v7", "launch_control_v4"])
    with pytest.raises(
            patch_ms41.PatchError,
            match="launch_control_v4 requires ignition_cut_v7"):
        patch_service.build_image(image, ["ignition_cut_v9"])


@pytest.mark.parametrize("variant,suffix", [
    ("MS41.0", "_ms410"), ("MS41.1", "_ms411"),
    ("MS41.2", "_ms412"), ("MS41.3", ""),
])
@pytest.mark.parametrize("with_launch", [False, True])
def test_configured_v9_upgrade_and_v10_removal_preserve_calibration(
        variant, suffix, with_launch):
    patches = patch_ms41.load_patches()
    old_id = "ignition_cut_v9" + suffix
    new_id = "ignition_cut_v10" + suffix if suffix else "ignition_cut_v11"
    launch_id = "launch_control_v7" + suffix
    historical = dict(patches)
    historical[launch_id] = dict(patches[launch_id], requires=[old_id])
    old_ids = [old_id] + ([launch_id] if with_launch else [])
    image, _ = patch_ms41.build(
        ref(variant), old_ids, patches=historical, allow_deprecated=True)
    configured = bytearray(image)
    configured_stock = bytearray(ref(variant))
    values = {"CUTSW": 0, "CUTRPM": 125, "CUT_HYST": 3, "CUT_IPW": 500,
              "LC_SW": 255, "LC_CUTTYPE": 1, "LC_CLUTCHPOL": 0,
              "LC_MAXRPM": 100, "LC_HARDRPM": 103, "LC_HYST": 2,
              "LC_IPW": 400, "LC_ARMSPEED": 3, "LC_MAXSPEED": 10,
              "LC_MINTPS": 80}
    for patch_id in old_ids:
        for name, address in historical[patch_id]["cave"]["cals"].items():
            width = 2 if name in {"CUT_IPW", "LC_IPW"} else 1
            payload = values[name].to_bytes(width, "little")
            configured[address:address + width] = payload
            configured_stock[address:address + width] = payload
    configured, _ = patch_ms41.checksum.correct_checksums(configured)
    upgraded, _ = patch_service.build_image(configured, [new_id])
    assert patch_ms41.is_applied(upgraded, patches[new_id])
    assert not patch_ms41.is_applied(upgraded, patches[old_id])
    assert patch_ms41.checksum.verify_checksum(bytearray(upgraded))[0]
    if with_launch:
        assert patch_ms41.is_applied(upgraded, patches[launch_id])
        upgraded = patch_service.revert_patch(upgraded, launch_id)
    restored = patch_service.revert_patch(upgraded, new_id)
    expected, _ = patch_ms41.checksum.correct_checksums(configured_stock)
    assert restored == expected


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

    ic = next(p for p in patch_service.available_patches(cleaned) if p["id"] == "ignition_cut_v11")
    assert ic["legacy"] == []                            # V1 gone, no longer flagged

    out, log = patch_service.build_image(cleaned, ["ignition_cut_v11"])
    assert len(out) == patch_ms41.FULL


def test_revert_legacy_v2_then_apply_v9():
    v2_base, _ = _deprecated_fixture(ref("MS41.3"), ["ignition_cut_v2"])
    cleaned = patch_service.revert_patch(v2_base, "ignition_cut_v2")
    corrected_stock, _ = patch_ms41.checksum.correct_checksums(ref("MS41.3"))
    assert cleaned == bytes(corrected_stock)             # stock bytes plus corrected program CRC
    out, _ = patch_service.build_image(cleaned, ["ignition_cut_v11"])
    assert len(out) == patch_ms41.FULL


def test_revert_patch_raises_if_not_applied():
    with pytest.raises(patch_ms41.PatchError):
        patch_service.revert_patch(ref("MS41.3"), "ignition_cut")



@pytest.mark.parametrize("version", ["MS41.0", "MS41.1", "MS41.2", "MS41.3"])
def test_protected_top_loader_removal_and_reinstall_preserve_bank(version):
    import checksum
    import softbsl_install

    patches = patch_service.definitions()
    top, _ids, _log = softbsl_install.compose_persistent_target(
        ref(version), with_calguard=False, marker="T", chip="29f400")
    _bootstrap, door_id = softbsl_install._sb._door_patch_ids(version)
    without_door = patch_service.revert_patch(top, door_id)
    without_loader = patch_service.revert_patch(without_door, "softbsl_loader")

    assert without_loader[0x5FFC:0x6000] == b"\xA5\x5A\x54\xAB"
    assert not patch_ms41.is_applied(without_loader, patches["softbsl_loader"])
    assert patch_ms41.is_applied(without_loader, patches["top_ds2_guard"])
    assert all(checksum.checksum_status(without_loader)[key]
               for key in ("boot", "program", "cal"))
    rows = {row["id"]: row for row in patch_service.available_patches(without_loader)}
    assert "top_ds2_guard" not in rows
    assert rows["amd_flash"]["removable"] is False
    assert rows["amd_flash"]["required_by"] == ["top_ds2_guard"]
    with pytest.raises(patch_service.PatchError, match="top_ds2_guard"):
        patch_service.revert_patch(without_loader, "amd_flash")

    rebuilt, _log = patch_service.build_image(without_loader, ["softbsl_loader"])
    assert rebuilt == without_door
    with pytest.raises(patch_service.PatchError, match="requires a TOP bank"):
        patch_service.build_image(without_loader, ["softbsl_loader"], marker="B")


@pytest.mark.parametrize("variant", ["MS41.0", "MS41.1", "MS41.2", "MS41.3"])
@pytest.mark.parametrize("marker", ["B", "T"])
def test_prior_amd_upgrade_and_removal_preserve_identifiers_and_boot_gate(
        variant, marker, monkeypatch):
    patches = patch_service.definitions()
    historical = dict(patches)
    historical["top_ds2_guard"] = dict(
        patches["top_ds2_guard"], requires=["amd_flash_v3"])
    stock = bytearray(ref(variant))
    # Distinct per-unit bytes prove migration uses this image's identity.
    stock[0x5CD5:0x5F8C] = bytes(
        (offset * 37 + 11) & 0xFF for offset in range(0x5F8C - 0x5CD5))
    stock, _ = patch_ms41.checksum.correct_checksums(stock)
    protected_ranges = ((0x5CD5, 0x5F8C), (0x6000, 0x6100), (0x1400C, 0x14016))
    selected = ["amd_flash_v3"] + (["top_ds2_guard"] if marker == "T" else [])
    source, _ = patch_ms41.build(
        stock, selected, patches=historical, marker=marker, allow_deprecated=True)
    # Expose the installer-only guard to exercise its catalogue health check.
    monkeypatch.setattr(
        patch_service, "PATCH_TAB_HIDDEN_IDS",
        patch_service.PATCH_TAB_HIDDEN_IDS - {"top_ds2_guard"})
    rows = {row["id"]: row for row in patch_service.available_patches(source)}
    assert patch_ms41.is_applied(source, patches["amd_flash_v3"])
    assert "amd_flash_v3" not in rows
    assert rows["amd_flash"]["installed"] is True
    assert rows["amd_flash"]["removable"] is (marker == "B")
    if marker == "T":
        assert rows["top_ds2_guard"]["ok"] is True
        assert rows["amd_flash"]["required_by"] == ["top_ds2_guard"]
        with pytest.raises(patch_service.PatchError, match="top_ds2_guard"):
            patch_service.revert_patch(source, "amd_flash_v3")

    # Current driver recognition does not add a CalGuard wait or rewrite V3.
    with_loader, _ = patch_service.build_image(source, ["softbsl_loader"])
    assert patch_ms41.is_applied(with_loader, patches["amd_flash"])
    assert patch_ms41.is_applied(with_loader, patches["amd_flash_v3"])
    assert patch_ms41.is_absent(with_loader, patch_ms41.startup_wait_definition(patches))
    upgraded, _ = patch_service.build_image(source, ["amd_flash"])
    assert patch_ms41.is_applied(upgraded, patches["amd_flash"])
    assert patch_ms41.is_applied(upgraded, patches["amd_flash_v3"])
    assert upgraded == source
    assert upgraded[0x5FFC:0x6000] == source[0x5FFC:0x6000]
    assert patch_service.missing_boot_patches(upgraded, source) == []
    reads = [(lo, source[lo:hi])
             for lo, hi in patch_service.boot_patch_read_ranges(upgraded)]
    assert patch_service.missing_boot_patches_sparse(upgraded, reads) == []
    assert patch_service.missing_boot_patches(upgraded, upgraded) == []
    removable = upgraded
    if marker == "T":
        assert patch_ms41.is_applied(upgraded, patches["top_ds2_guard"])
        with pytest.raises(patch_service.PatchError, match="top_ds2_guard"):
            patch_service.revert_patch(upgraded, "amd_flash")
        removable = patch_service.revert_patch(upgraded, "top_ds2_guard")
    restored = patch_service.revert_patch(removable, "amd_flash")
    expected, _ = patch_ms41.build(stock, [], marker=marker)
    assert restored == expected
    for image in (source, with_loader, upgraded, restored):
        assert image[0x5FFC:0x6000] == source[0x5FFC:0x6000]
        assert all(image[lo:hi] == stock[lo:hi] for lo, hi in protected_ranges)
        assert all(patch_ms41.checksum.checksum_status(image)[key]
                   for key in ("boot", "program", "cal"))


@pytest.mark.parametrize("corrupt_offset", [0x4460, 0x4484, 0x423C])
def test_unknown_calguard_startup_is_separate_from_amd_top_dependency(
        corrupt_offset, monkeypatch):
    patches = patch_service.definitions()
    historical = dict(patches)
    historical["top_ds2_guard"] = dict(
        patches["top_ds2_guard"], requires=["amd_flash_v3"])
    image, _ = patch_ms41.build(
        ref("MS41.0"), ["amd_flash_v3", "top_ds2_guard"],
        patches=historical, marker="T", allow_deprecated=True)
    image = bytearray(image)
    image[corrupt_offset] ^= 1
    monkeypatch.setattr(
        patch_service, "PATCH_TAB_HIDDEN_IDS",
        patch_service.PATCH_TAB_HIDDEN_IDS - {"top_ds2_guard"})
    rows = {row["id"]: row for row in patch_service.available_patches(image)}
    assert rows["top_ds2_guard"]["ok"] is (corrupt_offset != 0x423C)
    assert rows["amd_flash"]["installed"] is (corrupt_offset != 0x423C)
    if corrupt_offset == 0x423C:
        assert rows["top_ds2_guard"]["badge"] == "MISSING REQUIRED PATCH: amd_flash"
    with pytest.raises(
            patch_service.PatchError,
            match=("unrecognized flash driver" if corrupt_offset == 0x423C
                   else "PARTIAL/unknown CalGuard startup wait")):
        patch_service.build_image(image, ["softbsl_loader"])


@pytest.mark.parametrize("marker", ["B", "T"])
def test_loader_build_preserves_valid_existing_bank_metadata(marker):
    base, _log = patch_ms41.build(ref("MS41.2"), [], marker=marker)
    image, _log = patch_service.build_image(base, ["softbsl_loader"])
    assert image[0x5FFC:0x6000] == base[0x5FFC:0x6000]
    assert patch_ms41.is_applied(image, patch_service.definitions()["softbsl_loader"])

    override = "T" if marker == "B" else "B"
    explicit, _log = patch_service.build_image(base, ["softbsl_loader"], marker=override)
    assert explicit[0x5FFE] == ord(override)


def test_current_amd_driver_boot_gate_ignores_a_shared_wait_on_the_live_image():
    patches = patch_service.definitions()
    driver, _ = patch_service.build_image(ref("MS41.0"), ["amd_flash"])
    assert patch_service.boot_write_patches_in(driver) == ["amd_flash"]
    assert patch_ms41.is_absent(driver, patch_ms41.startup_wait_definition(patches))
    live, _ = patch_service.build_image(driver, ["softbsl_loader", "cal_guard"])
    assert patch_ms41.is_applied(live, patch_ms41.startup_wait_definition(patches))
    assert patch_service.missing_boot_patches(driver, live) == []
    ranges = patch_service.boot_patch_read_ranges(driver)
    assert all(not lo <= offset < hi for lo, hi in ranges for offset in (0x4460, 0x4484))
    assert patch_service.missing_boot_patches_sparse(
        driver, [(lo, live[lo:hi]) for lo, hi in ranges]) == []


def test_loader_build_still_rejects_a_corrupt_bank_marker():
    base = bytearray(ref("MS41.2"))
    base[0x5FFC:0x6000] = b"\xA5\x5A\x54\xAA"
    with pytest.raises(patch_service.PatchError, match="softbsl_loader @0x05FFC"):
        patch_service.build_image(base, ["softbsl_loader"])


@pytest.mark.parametrize("variant", ["MS41.0", "MS41.1", "MS41.2", "MS41.3"])
@pytest.mark.parametrize("amd_driver", [False, True])
def test_calguard_startup_upgrade_gate_conversion_and_removal(variant, amd_driver):
    patches = patch_service.definitions()
    stock = bytearray(ref(variant))
    stock[0x5CD5:0x5F8C] = bytes(
        (offset * 37 + 11) & 0xFF for offset in range(0x5F8C - 0x5CD5))
    stock, _ = patch_ms41.checksum.correct_checksums(stock)
    protected = ((0x5CD5, 0x5F8C), (0x6000, 0x6050), (0x6052, 0x6100),
                 (0x14000, 0x1A000))
    driver_ids = ["amd_flash"] if amd_driver else []
    image, _ = patch_service.build_image(
        stock, [*driver_ids, "softbsl_loader", "cal_guard"])
    delay = patch_ms41.startup_wait_definition(patches)
    assert patch_ms41.is_applied(image, delay)
    assert "intel_startup_delay" not in patches
    assert delay["id"] not in {
        row["id"] for row in patch_service.available_patches(image)}
    assert all(row["ok"] for row in patch_service.available_patches(image)
               if row["installed"])
    assert patch_service.build_image(image, ["softbsl_loader"])[0] == image

    # Simulate an exact previous guard image without its internal wait.
    previous = patch_ms41.revert(image, delay)
    assert patch_ms41.is_applied(previous, patches["softbsl_loader"])
    assert patch_ms41.is_applied(previous, patches["cal_guard"])
    assert patch_ms41.is_absent(previous, delay)
    assert patch_service.missing_boot_patches(image, previous) == ["cal_guard"]
    reads = [(lo, previous[lo:hi])
             for lo, hi in patch_service.boot_patch_read_ranges(image)]
    assert patch_service.missing_boot_patches_sparse(image, reads) == ["cal_guard"]
    assert patch_service.build_image(previous, ["softbsl_loader"])[0] == image

    with pytest.raises(patch_service.PatchError, match="cal_guard"):
        patch_service.revert_patch(image, "softbsl_loader")
    without_guard = patch_service.revert_patch(image, "cal_guard")
    assert patch_ms41.is_absent(without_guard, delay)
    assert patch_ms41.is_applied(without_guard, patches["softbsl_loader"])
    assert patch_service.build_image(without_guard, ["cal_guard"])[0] == image
    restored = patch_service.revert_patch(without_guard, "softbsl_loader")
    assert patch_ms41.is_absent(restored, delay)
    assert not patch_ms41.is_applied(restored, patches["softbsl_loader"])
    # Legacy guard removal also accepts both intact native anchors.
    legacy_without_guard = patch_service.revert_patch(previous, "cal_guard")
    assert legacy_without_guard == without_guard
    # Earlier local builds left this exact wait behind without a guard. Keep
    # their boot-loss gate until a loader update or removal cleans the residue.
    leftover_wait = patch_ms41.revert(image, patches["cal_guard"])
    assert patch_service.missing_boot_patches(
        leftover_wait, without_guard) == ["softbsl_loader"]
    reads = [(lo, without_guard[lo:hi])
             for lo, hi in patch_service.boot_patch_read_ranges(leftover_wait)]
    assert patch_service.missing_boot_patches_sparse(
        leftover_wait, reads) == ["softbsl_loader"]
    assert patch_service.build_image(leftover_wait, ["softbsl_loader"])[0] == without_guard
    assert patch_service.revert_patch(leftover_wait, "softbsl_loader") == restored
    assert patch_service.revert_patch(legacy_without_guard, "softbsl_loader") == restored

    # Driver conversion retains CalGuard's shared wait. Guard removal restores
    # native startup while leaving the AMD driver and TOP protection intact.
    amd, _ = patch_service.build_image(
        image, ["amd_flash", "top_ds2_guard"], marker="T")
    assert patch_ms41.is_applied(amd, patches["amd_flash"])
    assert patch_ms41.is_applied(amd, patches["top_ds2_guard"])
    assert patch_ms41.is_applied(amd, delay)
    amd_without_guard = patch_service.revert_patch(amd, "cal_guard")
    amd_without_loader = patch_service.revert_patch(amd_without_guard, "softbsl_loader")
    assert patch_ms41.is_absent(amd_without_guard, delay)
    assert patch_ms41.is_absent(amd_without_loader, delay)
    assert patch_ms41.is_applied(amd_without_loader, patches["amd_flash"])
    assert patch_ms41.is_applied(amd_without_loader, patches["top_ds2_guard"])
    assert amd_without_loader[0x5FFC:0x6000] == b"\xa5\x5a\x54\xab"
    assert patch_service.available_patches(amd_without_loader)
    assert patch_service.build_image(amd_without_guard, ["cal_guard"])[0] == amd
    assert patch_service.missing_boot_patches(amd, amd_without_guard) == ["cal_guard"]
    reads = [(lo, amd_without_guard[lo:hi])
             for lo, hi in patch_service.boot_patch_read_ranges(amd)]
    assert patch_service.missing_boot_patches_sparse(amd, reads) == ["cal_guard"]
    # Historical V4 left the same wait behind with only an AMD driver/TOP gate.
    orphan = bytearray(patch_ms41.revert(
        patch_ms41.revert(amd, patches["cal_guard"]), patches["softbsl_loader"]))
    # Raw loader removal restores its FF preimage; the historical TOP fixture
    # must retain the physical bank marker required by the remaining TOP gate.
    orphan[0x5FFC:0x6000] = amd[0x5FFC:0x6000]
    orphan, _ = patch_ms41.checksum.correct_checksums(orphan)
    assert patch_ms41.is_applied(orphan, delay)
    assert patch_service.missing_boot_patches(orphan, amd_without_loader) == ["amd_flash"]
    reads = [(lo, amd_without_loader[lo:hi])
             for lo, hi in patch_service.boot_patch_read_ranges(orphan)]
    assert patch_service.missing_boot_patches_sparse(orphan, reads) == ["amd_flash"]
    assert patch_service.build_image(orphan, ["amd_flash"])[0] == amd_without_loader
    assert patch_service.build_image(orphan, ["softbsl_loader"])[0] == amd_without_guard
    for candidate in (image, previous, restored, amd, amd_without_guard,
                      amd_without_loader, orphan):
        assert all(candidate[lo:hi] == stock[lo:hi] for lo, hi in protected)
        assert all(patch_ms41.checksum.checksum_status(candidate)[key]
                   for key in ("boot", "program", "cal"))


@pytest.mark.parametrize("offset,unknown", [
    (0x4460, False), (0x4460, True), (0x4484, False), (0x4484, True),
    (0x423C, True),
])
@pytest.mark.parametrize("amd_driver", [False, True])
def test_calguard_removal_rejects_unknown_or_partial_startup(
        offset, unknown, amd_driver):
    patches = patch_service.definitions()
    driver_ids = ["amd_flash"] if amd_driver else []
    image, _ = patch_service.build_image(
        ref("MS41.0"), [*driver_ids, "softbsl_loader", "cal_guard"])
    damaged = bytearray(image)
    if unknown:
        damaged[offset] ^= 1
    else:
        edit = next(edit for edit in patch_ms41.startup_wait_definition(patches)["edits"]
                    if edit["off"] == offset)
        payload = bytes.fromhex(edit["expect"])
        damaged[offset:offset + len(payload)] = payload
    before = bytes(damaged)
    with pytest.raises(patch_service.PatchError, match="unknown"):
        patch_service.revert_patch(damaged, "cal_guard")
    assert bytes(damaged) == before


def test_available_patches_flags_needs_boot():
    avail = {p["id"]: p for p in patch_service.available_patches(ref("MS41.3"))}
    assert avail["cal_guard"]["needs_boot"] is True          # writes SA1
    assert avail["ignition_cut_v11"]["needs_boot"] is False   # program region


def test_boot_write_patches_detected_in_built_image():
    stock = ref("MS41.3")
    v9_img, _ = patch_service.build_image(stock, ["ignition_cut_v11"])
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
    v9_img, _ = patch_service.build_image(stock, ["ignition_cut_v11"])
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

    assert ranges == [(17426, 17456), (17504, 17508), (17540, 17548), (17584, 18136), (18208, 18224), (18830, 18880), (18980, 19012), (19024, 19056), (19506, 19752), (19888, 20096), (20112, 20140), (21806, 21872), (21920, 21924), (22548, 22576), (22688, 22716), (22922, 22950), (23372, 23572), (23602, 23680), (23692, 23706), (23712, 23734), (24460, 24576)]
    assert sum(hi - lo for lo, hi in ranges) == 1790

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
