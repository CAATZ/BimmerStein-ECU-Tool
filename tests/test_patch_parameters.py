import hashlib

import pytest

import checksum
import patch_service
from tests.conftest import ref


CASES = (
    (
        "MS41.0",
        ["ignition_cut_v18_ms410", "launch_control_v18_ms410", "vanos_minrpm_v2_ms410"],
        {"ignition_cut_v18_ms410": 6, "launch_control_v18_ms410": 15,
         "vanos_minrpm_v2_ms410": 1},
    ),
    (
        "MS41.1",
        ["ignition_cut_v18_ms411", "launch_control_v18_ms411"],
        {"ignition_cut_v18_ms411": 6, "launch_control_v18_ms411": 15,
         "vanos_minrpm_ms411": 1},
    ),
    (
        "MS41.2",
        ["ignition_cut_v18_ms412", "launch_control_v18_ms412"],
        {"ignition_cut_v18_ms412": 6, "launch_control_v18_ms412": 15},
    ),
    (
        "MS41.3",
        ["ignition_cut_v18", "launch_control_v18"],
        {"ignition_cut_v18": 6, "launch_control_v18": 15},
    ),
)

EXPECTED_PARAMETER_IDS = {
    "ignition_cut_v10": {"CUTSW", "CUTRPM", "CUT_HYST", "CUT_IPW"},
    "launch_control_v12": {
        "LC_SW", "LC_CUTTYPE", "LC_CLUTCHPOL", "LC_MAXRPM", "LC_ARMSPEED",
        "LC_MAXSPEED", "LC_MINTPS", "LC_HARDRPM", "LC_HYST", "LC_IPW",
        "LC_FUEL_HYST_B", "LC_FUEL_HYST_A", "LC_IGN_ANGLE",
    },
    "vanos_minrpm": {"VANOSRPM"},
}
EXPECTED_PARAMETER_IDS["ignition_cut_v18"] = EXPECTED_PARAMETER_IDS["ignition_cut_v10"] | {
    "CUT_RETARD_RANGE", "CUT_IGN_ANGLE",
}
EXPECTED_PARAMETER_IDS["launch_control_v18"] = EXPECTED_PARAMETER_IDS["launch_control_v12"] | {
    "LC_RETARD_RANGE", "LC_CUT_IGN_ANGLE",
}


@pytest.mark.parametrize("checksum_ok", [True, False])
def test_installable_patch_exposes_schema_but_cannot_be_edited_directly(monkeypatch, checksum_ok):
    image = bytes(256 * 1024)
    monkeypatch.setattr(patch_service, "base_version", lambda _data: "MS41.3")
    monkeypatch.setattr(patch_service, "available_patches", lambda _data: [{
        "id": "ignition_cut_v18", "installed": False, "deprecated": False,
        "ok": True, "badge": "OK",
    }])
    monkeypatch.setattr(
        patch_service.checksum, "verify_checksum", lambda _data: (checksum_ok, []),
    )
    monkeypatch.setattr(
        patch_service, "_public_parameter",
        lambda _image, _patch, spec: {"id": spec["id"]},
    )

    groups = patch_service.editable_parameters(image)

    assert [group["patch_id"] for group in groups] == ["ignition_cut_v18"]
    assert groups[0]["editable"] is True
    with pytest.raises(patch_service.PatchError, match="not an editable current installation"):
        patch_service.apply_parameter_changes(
            image, "ignition_cut_v18", {"CUTRPM": "4000"},
        )


def _built(variant, patch_ids):
    return patch_service.build_image(ref(variant), patch_ids)[0]


def test_installed_parameter_writes_still_require_valid_checksums():
    source = bytearray(_built("MS41.3", ["ignition_cut_v18"]))
    source[0x5C80] ^= 1  # Stored boot checksum, outside the patch's executable bytes.
    group = next(group for group in patch_service.editable_parameters(source)
                 if group["patch_id"] == "ignition_cut_v18")
    assert not group["editable"]
    assert "checksum" in group["blocked_reason"].lower()
    with pytest.raises(patch_service.PatchError, match="checksum"):
        patch_service.apply_parameter_changes(source, "ignition_cut_v18", {"CUTRPM": "4000"})


@pytest.mark.parametrize("variant,patch_ids,expected", CASES)
def test_parameter_inventory_is_exact_and_address_free(variant, patch_ids, expected):
    groups = patch_service.editable_parameters(_built(variant, patch_ids))

    assert {group["patch_id"]: len(group["parameters"]) for group in groups} == expected
    assert all(group["editable"] for group in groups)
    assert all(len(group["descriptor_token"]) == 64 for group in groups)
    assert all("offset" not in parameter for group in groups for parameter in group["parameters"])
    assert all(parameter["id"] != "VERSION_MARKER"
               for group in groups for parameter in group["parameters"])
    for group in groups:
        family = patch_service.definitions()[group["patch_id"]].get(
            "family_id", group["patch_id"])
        assert {parameter["id"] for parameter in group["parameters"]} == (
            EXPECTED_PARAMETER_IDS[family]
        )


@pytest.mark.parametrize(
    "variant,patch_id",
    (
        ("MS41.0", "ignition_cut_v18_ms410"),
        ("MS41.1", "ignition_cut_v18_ms411"),
        ("MS41.2", "ignition_cut_v18_ms412"),
        ("MS41.3", "ignition_cut_v18"),
    ),
)
def test_ignition_parameters_round_trip_without_touching_identity(variant, patch_id):
    source = _built(variant, [patch_id])
    group = patch_service.editable_parameters(source)[0]
    source_identity = source[0x5CD5:0x5F8B]

    result, report = patch_service.apply_parameter_changes(
        source,
        patch_id,
        {"CUTSW": "pin80", "CUTRPM": "4001", "CUT_HYST": "48", "CUT_IPW": "0"},
        expected_sha256=hashlib.sha256(source).hexdigest(),
        expected_descriptor_token=group["descriptor_token"],
    )

    patch = patch_service.definitions()[patch_id]
    cals = patch["cave"]["cals"]
    assert result[cals["CUTSW"]] == 0x01
    assert result[cals["CUTRPM"]] == 125
    assert result[cals["CUT_HYST"]] == 2
    assert result[cals["CUT_IPW"]:cals["CUT_IPW"] + 2] == b"\x00\x00"
    assert result[0x5CD5:0x5F8B] == source_identity
    assert checksum.verify_checksum(bytearray(result))[0]
    assert report["source_sha256"] == hashlib.sha256(source).hexdigest()
    assert report["result_sha256"] == hashlib.sha256(result).hexdigest()
    assert len(report["changes"]) == 4
    applied = {change["parameter_id"]: change["after"] for change in report["changes"]}
    assert applied["CUTRPM"] == "4000 RPM"
    assert applied["CUT_HYST"] == "64 RPM"


def test_launch_parameters_validate_and_keep_named_sentinels():
    source = _built("MS41.3", ["ignition_cut_v18", "launch_control_v18"])
    groups = {group["patch_id"]: group for group in patch_service.editable_parameters(source)}
    launch = groups["launch_control_v18"]
    hard = next(parameter for parameter in launch["parameters"]
                if parameter["id"] == "LC_HARDRPM")
    assert hard["current"] == "@auto"
    assert hard["current_display"] == "Automatic"

    result, _report = patch_service.apply_parameter_changes(
        source,
        "launch_control_v18",
        {
            "LC_SW": "pin80", "LC_CUTTYPE": "fuel", "LC_CLUTCHPOL": "active_low",
            "LC_MAXRPM": "4000", "LC_ARMSPEED": "5", "LC_MAXSPEED": "10",
            "LC_MINTPS": "50", "LC_HARDRPM": "4192", "LC_HYST": "64",
            "LC_IPW": "@stock",
        },
        expected_descriptor_token=launch["descriptor_token"],
    )
    values = {
        parameter["id"]: parameter
        for group in patch_service.editable_parameters(result)
        if group["patch_id"] == "launch_control_v18"
        for parameter in group["parameters"]
    }
    assert values["LC_MAXRPM"]["current"] == "4000"
    assert values["LC_HARDRPM"]["current"] == "4192"
    assert values["LC_IPW"]["current"] == "@stock"
    assert values["LC_MINTPS"]["current"] == "49.82"


def test_launch_relationships_fail_closed_when_enabled():
    source = _built("MS41.3", ["ignition_cut_v18", "launch_control_v18"])
    with pytest.raises(patch_service.PatchError, match="LC_MAXSPEED"):
        patch_service.apply_parameter_changes(
            source,
            "launch_control_v18",
            {"LC_SW": "pin80", "LC_ARMSPEED": "10", "LC_MAXSPEED": "5"},
        )


@pytest.mark.parametrize("variant,patch_ids,_expected", CASES)
@pytest.mark.parametrize("mode", ("always", "pin80", "pin81", "pin82"))
def test_enabled_launch_requires_explicit_strategy_and_physical_input_polarity(
        variant, patch_ids, _expected, mode):
    source = _built(variant, patch_ids)
    launch = patch_ids[1]
    cals = patch_service.definitions()[launch]["cave"]["cals"]
    assert source[cals["LC_CUTTYPE"]] == source[cals["LC_CLUTCHPOL"]] == 255
    changes = {"LC_SW": mode, "LC_ARMSPEED": "1", "LC_MAXSPEED": "10"}
    with pytest.raises(patch_service.PatchError, match="LC_CUTTYPE"):
        patch_service.apply_parameter_changes(source, launch, changes)
    changes["LC_CUTTYPE"] = "ignition"
    if mode == "always":
        result = patch_service.apply_parameter_changes(source, launch, changes)[0]
        assert result[cals["LC_CLUTCHPOL"]] == 255  # Ignored in Always mode.
    else:
        with pytest.raises(patch_service.PatchError, match="LC_CLUTCHPOL"):
            patch_service.apply_parameter_changes(source, launch, changes)
        for polarity, raw in (("active_high", 0), ("active_low", 1)):
            result = patch_service.apply_parameter_changes(
                source, launch, dict(changes, LC_CLUTCHPOL=polarity))[0]
            assert result[cals["LC_CLUTCHPOL"]] == raw
    assert result[cals["LC_CUTTYPE"]] == 1
    assert checksum.verify_checksum(bytearray(result))[0]


@pytest.mark.parametrize("variant,patch_ids,_expected", CASES)
def test_disabled_launch_preserves_unknown_choices_and_can_disable_invalid_existing_setup(
        variant, patch_ids, _expected):
    source = bytearray(_built(variant, patch_ids))
    launch = patch_ids[1]
    cals = patch_service.definitions()[launch]["cave"]["cals"]
    source[cals["LC_CUTTYPE"]] = 254
    source[cals["LC_CLUTCHPOL"]] = 253
    source[cals["LC_ARMSPEED"]] = 1
    source[cals["LC_MAXSPEED"]] = 10
    source = bytes(checksum.correct_checksums(source, correct_program=False)[0])
    edited = patch_service.apply_parameter_changes(source, launch, {"LC_MAXRPM": "4000"})[0]
    assert edited[cals["LC_CUTTYPE"]] == 254 and edited[cals["LC_CLUTCHPOL"]] == 253
    existing = bytearray(edited)
    existing[cals["LC_SW"]] = 1
    existing = bytes(checksum.correct_checksums(existing, correct_program=False)[0])
    with pytest.raises(patch_service.PatchError, match="LC_CUTTYPE"):
        patch_service.apply_parameter_changes(existing, launch, {"LC_MAXRPM": "5000"})
    disabled = patch_service.apply_parameter_changes(existing, launch, {"LC_SW": "off"})[0]
    assert disabled == edited
    unknown_switch = bytearray(existing)
    unknown_switch[cals["LC_SW"]] = 3
    unknown_switch = bytes(checksum.correct_checksums(unknown_switch, correct_program=False)[0])
    with pytest.raises(patch_service.PatchError, match="LC_SW"):
        patch_service.apply_parameter_changes(unknown_switch, launch, {"LC_CUTTYPE": "ignition"})


def test_ms410_throttle_angle_schema_preserves_every_raw_value():
    patch = patch_service.definitions()["launch_control_v18_ms410"]
    spec = next(spec for spec in patch_service._parameter_specs(patch) if spec["id"] == "LC_MINTPS")
    assert spec["units"] == "° throttle" and spec["maximum"] == "119.5"
    for raw in range(256):
        value, _ = patch_service._decode_parameter(spec, raw)
        assert patch_service._encode_parameter(spec, value) == raw
        assert patch_service.normalize_parameter_value(spec, value) == value
    assert patch_service._encode_parameter(spec, "50") == 107
    assert patch_service._encode_parameter(spec, "119.5") == 255
    for suffix in ("_ms411", "_ms412", ""):
        other = next(spec for spec in patch_service._parameter_specs(
            patch_service.definitions()["launch_control_v18" + suffix]) if spec["id"] == "LC_MINTPS")
        assert (other["units"], other["step"], other["maximum"]) == ("%", "0.47", "119.85")


@pytest.mark.parametrize("raw", (0, 1, 106, 128, 254, 255))
def test_ms410_throttle_display_and_save_leave_unchanged_calibration_byte(raw):
    launch = "launch_control_v18_ms410"
    source = bytearray(_built("MS41.0", ["ignition_cut_v18_ms410", launch]))
    offset = patch_service.definitions()[launch]["cave"]["cals"]["LC_MINTPS"]
    source[offset] = raw
    source = bytes(checksum.correct_checksums(source, correct_program=False)[0])
    group = next(group for group in patch_service.editable_parameters(source) if group["patch_id"] == launch)
    parameter = next(parameter for parameter in group["parameters"] if parameter["id"] == "LC_MINTPS")
    assert parameter["units"] == "° throttle" and parameter["raw_hex"] == f"{raw:02X}"
    expected = patch_service.apply_parameter_changes(source, launch, {"LC_HYST": "64"})[0]
    actual = patch_service.apply_parameter_changes(source, launch, {
        "LC_HYST": "64", "LC_MINTPS": parameter["current"],
    })[0]
    assert actual == expected and actual[offset] == raw


@pytest.mark.parametrize("variant,patch_ids,_expected", CASES)
@pytest.mark.parametrize("hard_rpm", ("0", "3968", "4000", "4192", "@auto"))
def test_launch_fuel_hard_rpm_accepts_below_equal_above_soft_and_auto(
        variant, patch_ids, _expected, hard_rpm):
    source = _built(variant, patch_ids)
    patch_id = next(name for name in patch_ids if name.startswith("launch_control_"))
    result, _report = patch_service.apply_parameter_changes(source, patch_id, {
        "LC_SW": "always", "LC_CUTTYPE": "fuel", "LC_ARMSPEED": "1",
        "LC_MAXSPEED": "10", "LC_MAXRPM": "4000", "LC_HARDRPM": hard_rpm,
    })
    cals = patch_service.definitions()[patch_id]["cave"]["cals"]
    assert result[cals["LC_HARDRPM"]] == (0xFF if hard_rpm == "@auto" else int(hard_rpm) // 32)
    assert result[cals["LC_MAXRPM"]] == 125
    assert result[cals["LC_HYST"]] == source[cals["LC_HYST"]] == 0xFF
    assert checksum.verify_checksum(bytearray(result))[0]


@pytest.mark.parametrize("variant,patch_ids,_expected", CASES)
@pytest.mark.parametrize("mode", ("off", "fuel", "ignition"))
def test_launch_fuel_hysteresis_settings_are_independent_and_preserved_in_each_mode(
        variant, patch_ids, _expected, mode):
    source = _built(variant, patch_ids)
    patch_id = next(name for name in patch_ids if name.startswith("launch_control_"))
    group = next(group for group in patch_service.editable_parameters(source)
                 if group["patch_id"] == patch_id)
    parameters = {parameter["id"]: parameter for parameter in group["parameters"]}
    for name in ("LC_FUEL_HYST_B", "LC_FUEL_HYST_A"):
        assert parameters[name]["current"] == "@stock"
        assert parameters[name]["current_display"] == "Follow stock"
        assert parameters[name]["minimum"] == "0"
        assert parameters[name]["maximum"] == "8128"
    source = patch_service.apply_parameter_changes(source, patch_id, {"LC_HYST": "96"})[0]
    changed = patch_service.apply_parameter_changes(source, patch_id, {
        "LC_SW": "off" if mode == "off" else "always",
        "LC_CUTTYPE": "ignition" if mode == "ignition" else "fuel",
        "LC_ARMSPEED": "1", "LC_MAXSPEED": "10",
        "LC_FUEL_HYST_B": "64", "LC_FUEL_HYST_A": "32",
    })[0]
    cals = patch_service.definitions()[patch_id]["cave"]["cals"]
    assert changed[cals["LC_FUEL_HYST_B"]] == 2
    assert changed[cals["LC_FUEL_HYST_A"]] == 1  # A need not be greater than B.
    assert changed[cals["LC_HYST"]] == source[cals["LC_HYST"]] == 3
    mixed = patch_service.apply_parameter_changes(
        changed, patch_id, {"LC_FUEL_HYST_B": "@stock"})[0]
    assert mixed[cals["LC_FUEL_HYST_B"]] == 0xFF
    assert mixed[cals["LC_FUEL_HYST_A"]] == 1
    assert checksum.verify_checksum(bytearray(mixed))[0]


@pytest.mark.parametrize("variant,patch_ids,_expected", CASES)
def test_launch_fuel_hysteresis_custom_bounds_do_not_encode_stock(
        variant, patch_ids, _expected):
    source = _built(variant, patch_ids)
    patch_id = next(name for name in patch_ids if name.startswith("launch_control_"))
    changed = patch_service.apply_parameter_changes(source, patch_id, {
        "LC_FUEL_HYST_B": "0", "LC_FUEL_HYST_A": "8128",
    })[0]
    cals = patch_service.definitions()[patch_id]["cave"]["cals"]
    assert changed[cals["LC_FUEL_HYST_B"]] == 0
    assert changed[cals["LC_FUEL_HYST_A"]] == 0xFE
    for name in ("LC_FUEL_HYST_B", "LC_FUEL_HYST_A"):
        for invalid in ("-1", "8129", "8160"):
            with pytest.raises(patch_service.PatchError, match=name):
                patch_service.apply_parameter_changes(changed, patch_id, {name: invalid})


@pytest.mark.parametrize("variant,patch_ids,_expected", CASES)
@pytest.mark.parametrize("mode", ("always", "pin80", "off"))
def test_launch_arm_speed_zero_rejected_and_one_round_trips(
        variant, patch_ids, _expected, mode):
    source = _built(variant, patch_ids)
    patch_id = next(name for name in patch_ids if name.startswith("launch_control_"))
    group = next(group for group in patch_service.editable_parameters(source)
                 if group["patch_id"] == patch_id)
    arm_speed = next(parameter for parameter in group["parameters"]
                     if parameter["id"] == "LC_ARMSPEED")
    assert arm_speed["minimum"] == "1"
    assert "reported speed of 0 km/h" in arm_speed["description"]
    changes = {"LC_SW": mode, "LC_CUTTYPE": "ignition", "LC_CLUTCHPOL": "active_low", "LC_ARMSPEED": "0",
               "LC_MAXSPEED": "10", "LC_MAXRPM": "4000", "LC_MINTPS": "50"}
    with pytest.raises(patch_service.PatchError, match="LC_ARMSPEED must be between 1"):
        patch_service.apply_parameter_changes(source, patch_id, changes)
    changes["LC_ARMSPEED"] = "1"
    if mode != "off":
        with pytest.raises(patch_service.PatchError, match="LC_MAXSPEED"):
            patch_service.apply_parameter_changes(
                source, patch_id, dict(changes, LC_MAXSPEED="1"))
    result, _report = patch_service.apply_parameter_changes(source, patch_id, changes)
    offset = patch_service.definitions()[patch_id]["cave"]["cals"]["LC_ARMSPEED"]
    assert result[offset] == 1
    assert checksum.verify_checksum(bytearray(result))[0]


@pytest.mark.parametrize("variant,patch_ids,_expected", CASES)
@pytest.mark.parametrize("mode", ("always", "pin80"))
def test_launch_existing_zero_arm_speed_requires_correction_or_disable(
        variant, patch_ids, _expected, mode):
    source = _built(variant, patch_ids)
    patch_id = next(name for name in patch_ids if name.startswith("launch_control_"))
    source, _report = patch_service.apply_parameter_changes(source, patch_id, {
        "LC_SW": mode, "LC_CUTTYPE": "ignition", "LC_CLUTCHPOL": "active_low", "LC_ARMSPEED": "1",
        "LC_MAXSPEED": "10", "LC_MAXRPM": "4000", "LC_MINTPS": "50",
    })
    cals = patch_service.definitions()[patch_id]["cave"]["cals"]
    source = bytearray(source)
    source[cals["LC_ARMSPEED"]] = 0  # Existing configuration from an older editor.
    source, _details = checksum.correct_checksums(source, correct_program=False)
    source = bytes(source)

    with pytest.raises(patch_service.PatchError, match="LC_ARMSPEED must be at least 1"):
        patch_service.apply_parameter_changes(source, patch_id, {"LC_HYST": "64"})
    corrected, _report = patch_service.apply_parameter_changes(
        source, patch_id, {"LC_ARMSPEED": "1"})
    assert corrected[cals["LC_ARMSPEED"]] == 1
    disabled, _report = patch_service.apply_parameter_changes(
        source, patch_id, {"LC_SW": "off"})
    assert disabled[cals["LC_SW"]] == 0xFF
    assert disabled[cals["LC_ARMSPEED"]] == 0
    edited_disabled, _report = patch_service.apply_parameter_changes(
        disabled, patch_id, {"LC_HYST": "64"})
    assert edited_disabled[cals["LC_ARMSPEED"]] == 0
    with pytest.raises(patch_service.PatchError, match="LC_ARMSPEED must be at least 1"):
        patch_service.apply_parameter_changes(edited_disabled, patch_id, {"LC_SW": mode})


def test_unknown_stale_partial_and_uninstalled_requests_are_rejected():
    source = _built("MS41.3", ["ignition_cut_v18"])
    with pytest.raises(patch_service.PatchError, match="unknown patch parameter"):
        patch_service.apply_parameter_changes(source, "ignition_cut_v18", {"OFFSET": "1"})
    with pytest.raises(patch_service.PatchError, match="string ids and values"):
        patch_service.apply_parameter_changes(source, "ignition_cut_v18", {"CUTRPM": 4000})
    with pytest.raises(patch_service.PatchError, match="source ROM changed"):
        patch_service.apply_parameter_changes(
            source, "ignition_cut_v18", {"CUTRPM": "4000"}, expected_sha256="0" * 64)
    with pytest.raises(patch_service.PatchError, match="256 KiB"):
        patch_service.editable_parameters(source[:0x6000])
    with pytest.raises(patch_service.PatchError, match="not an editable current installation"):
        patch_service.apply_parameter_changes(
            ref("MS41.3"), "ignition_cut_v18", {"CUTRPM": "4000"})


def test_parameter_edit_does_not_mutate_the_source_buffer():
    source = bytearray(_built("MS41.0", ["vanos_minrpm_v2_ms410"]))
    original = bytes(source)
    result, _report = patch_service.apply_parameter_changes(
        source, "vanos_minrpm_v2_ms410", {"VANOSRPM": "4000"})
    assert bytes(source) == original
    assert result != original


@pytest.mark.parametrize("family,parameter_id,requested,applied", [
    ("ignition_cut_v10", "CUTRPM", "4200", "4192"),
    ("ignition_cut_v10", "CUTRPM", "4208", "4224"),
    ("ignition_cut_v10", "CUTRPM", "8159", "8160"),
    ("ignition_cut_v10", "CUT_HYST", "15", "0"),
    ("ignition_cut_v10", "CUT_HYST", "16", "32"),
    ("ignition_cut_v10", "CUT_IPW", "12", "11.99898"),
    ("launch_control_v12", "LC_MINTPS", "50", "49.82"),
    ("launch_control_v12", "LC_ARMSPEED", "5.5", "6"),
    ("launch_control_v12", "LC_HARDRPM", "@auto", "@auto"),
    ("launch_control_v12", "LC_FUEL_HYST_B", "15", "0"),
    ("launch_control_v12", "LC_FUEL_HYST_A", "16", "32"),
    ("launch_control_v12", "LC_FUEL_HYST_B", "@stock", "@stock"),
    ("launch_control_v12", "LC_IGN_ANGLE", "-10", "-10.125"),
    ("launch_control_v12", "LC_IGN_ANGLE", "0", "0.000"),
    ("launch_control_v12", "LC_IGN_ANGLE", "@off", "@off"),
    ("ignition_cut_v10", "CUT_IPW", "@stock", "@stock"),
    ("ignition_cut_v10", "CUT_HYST", "@legacy_zero", "@legacy_zero"),
])
def test_schema_quantization_matches_stored_value(family, parameter_id, requested, applied):
    spec = next(spec for spec in patch_service._EDITABLE_PARAMETER_FAMILIES[family]
                if spec["id"] == parameter_id)
    assert patch_service.normalize_parameter_value(spec, requested) == applied
    raw = patch_service._encode_parameter(spec, requested)
    assert patch_service._decode_parameter(spec, raw)[0] == applied


@pytest.mark.parametrize("variant,patch_ids,_expected", CASES)
def test_launch_absolute_angle_encodings_keep_off_distinct_and_identity_intact(
        variant, patch_ids, _expected):
    source = _built(variant, patch_ids)
    patch_id = next(name for name in patch_ids if name.startswith("launch_control_"))
    spec = next(spec for spec in patch_service._EDITABLE_PARAMETER_FAMILIES["launch_control_v12"]
                if spec["id"] == "LC_IGN_ANGLE")
    address = patch_service.definitions()[patch_id]["cave"]["cals"]["LC_IGN_ANGLE"]
    assert source[address] == 255
    assert patch_service._decode_parameter(spec, 255)[0] == "@off"
    for raw in range(64):
        value, _display = patch_service._decode_parameter(spec, raw)
        assert patch_service._encode_parameter(spec, value) == raw
    for raw in range(64, 255):
        assert patch_service._decode_parameter(spec, raw)[0].startswith("@raw:")
    for value in ("-24", "0.001", "NaN", "Infinity"):
        with pytest.raises(patch_service.PatchError):
            patch_service._encode_parameter(spec, value)
    configured, _ = patch_service.apply_parameter_changes(source, patch_id, {"LC_IGN_ANGLE": "0"})
    assert configured[address] == 63
    restored, _ = patch_service.apply_parameter_changes(configured, patch_id, {"LC_IGN_ANGLE": "@off"})
    assert restored == source
    assert configured[0x5CD5:0x5F8B] == source[0x5CD5:0x5F8B]
    assert checksum.verify_checksum(bytearray(configured))[0]


@pytest.mark.parametrize("requested", ["", "NaN", "Infinity", "-1", "8161"])
def test_quantization_keeps_invalid_numeric_inputs_rejected(requested):
    spec = patch_service._EDITABLE_PARAMETER_FAMILIES["ignition_cut_v10"][1]
    with pytest.raises(patch_service.PatchError):
        patch_service._encode_parameter(spec, requested)


def test_numeric_normalization_uses_declared_step():
    parameter = dict(patch_service._EDITABLE_PARAMETER_FAMILIES["ignition_cut_v10"][1],
                     step="16")
    assert patch_service.normalize_parameter_value(parameter, "4200") == "4208"


@pytest.mark.parametrize("variant,patch_ids,_expected", CASES)
def test_ignition_retard_controls_are_independent_and_validate_quantized_band(
        variant, patch_ids, _expected):
    source = _built(variant, patch_ids)
    original = source
    for patch_id, angle, width, limit in (
            (patch_ids[0], "CUT_IGN_ANGLE", "CUT_RETARD_RANGE", "CUTRPM"),
            (patch_ids[1], "LC_CUT_IGN_ANGLE", "LC_RETARD_RANGE", "LC_MAXRPM")):
        cals = patch_service.definitions()[patch_id]["cave"]["cals"]
        assert source[cals[width]] == 6 and source[cals[angle]] == 255
        configured, _ = patch_service.apply_parameter_changes(source, patch_id, {
            limit: "4000", width: "200", angle: "-10",
        })
        assert configured[cals[width]] == 6  # 200 RPM rounds to 192.
        assert configured[cals[angle]] == 36  # -10.125 degrees BTDC.
        assert checksum.verify_checksum(bytearray(configured))[0]
        assert configured[0x5CD5:0x5F8B] == original[0x5CD5:0x5F8B]
        if "LC_IGN_ANGLE" in cals:
            assert configured[cals["LC_IGN_ANGLE"]] == source[cals["LC_IGN_ANGLE"]] == 255
        for bad_range in ("0", "15", "3984", "4000", "4032"):
            with pytest.raises(patch_service.PatchError, match=width):
                patch_service.apply_parameter_changes(source, patch_id, {
                    limit: "4000", width: bad_range, angle: "0",
                })
        with pytest.raises(patch_service.PatchError, match=width):
            patch_service.apply_parameter_changes(configured, patch_id, {limit: "192"})
        disabled, _ = patch_service.apply_parameter_changes(configured, patch_id, {
            angle: "@off", width: "0",
        })
        assert disabled[cals[angle]] == 255 and disabled[cals[width]] == 0
        source = configured


def test_fresh_patch_parameters_expose_current_numeric_values_and_named_modes():
    source = _built("MS41.3", ["ignition_cut_v18", "launch_control_v18"])
    values = {parameter["id"]: parameter for group in patch_service.editable_parameters(source)
              for parameter in group["parameters"]}
    assert all(value["current"] and value["current_display"] for value in values.values())
    assert values["CUT_HYST"]["current"] == "@legacy_zero"
    assert values["CUT_HYST"]["current_display"] == "Legacy zero hysteresis"
    assert values["CUT_IPW"]["current"] == "@stock"
    assert values["LC_HARDRPM"]["current"] == "@auto"
    for name in ("LC_FUEL_HYST_B", "LC_FUEL_HYST_A"):
        assert values[name]["current"] == "@stock"
        assert values[name]["current_display"] == "Follow stock"


@pytest.mark.parametrize("variant,patch_ids,_expected", CASES)
@pytest.mark.parametrize("native_a,native_b", [(3, 7), (0, 255)])
def test_stock_hysteresis_display_uses_loaded_native_scalars_and_keeps_sentinels(
        variant, patch_ids, _expected, native_a, native_b):
    native_addresses = {
        "MS41.0": (0x01D2, 0x01D1),
        "MS41.1": (0x02DA, 0x02D9),
        "MS41.2": (0x02BA, 0x02B9),
        "MS41.3": (0x02BA, 0x02B9),
    }
    # Exact family-native MOVBZ consumers and definition xrefs resolve these
    # low tune SAs at file 14000+SA, not CPU 10000+SA.
    native_offsets = [0x14000 + address for address in native_addresses[variant]]
    source = bytearray(ref(variant))
    for offset, value in zip(native_offsets, (native_a, native_b)):
        source[offset] = value
    source = bytes(checksum.correct_checksums(source, correct_program=False)[0])
    patch_id = next(name for name in patch_ids if name.startswith("launch_control_"))

    for image in (source, patch_service.build_image(source, patch_ids)[0]):
        original = bytes(image)
        group = next(group for group in patch_service.editable_parameters(image)
                     if group["patch_id"] == patch_id)
        values = {parameter["id"]: parameter for parameter in group["parameters"]}
        for name, native in zip(("LC_FUEL_HYST_A", "LC_FUEL_HYST_B"), (native_a, native_b)):
            parameter = values[name]
            assert parameter["label"] == "Fuel hysteresis " + name[-1]
            assert parameter["current"] == "@stock"
            assert parameter["specials"] == [{
                "value": "@stock", "label": "Follow stock",
                "display_value": str(native * 32),
                "display_text": f"Stock: {native * 32} RPM",
            }]
        assert bytes(image) == original

    built = patch_service.build_image(source, patch_ids)[0]
    custom = patch_service.apply_parameter_changes(built, patch_id, {
        "LC_FUEL_HYST_A": "64", "LC_FUEL_HYST_B": "96",
    })[0]
    restored = patch_service.apply_parameter_changes(custom, patch_id, {
        "LC_FUEL_HYST_A": "@stock", "LC_FUEL_HYST_B": "@stock",
    })[0]
    assert restored == built  # Following stock stores FF, not the displayed number.
    assert [restored[offset] for offset in native_offsets] == [native_a, native_b]


@pytest.mark.parametrize("variant,patch_ids,_expected", CASES)
def test_special_display_distinguishes_dynamic_logic_from_numeric_values(
        variant, patch_ids, _expected):
    values = {parameter["id"]: parameter
              for group in patch_service.editable_parameters(_built(variant, patch_ids))
              for parameter in group["parameters"]}
    for name in ("CUT_IPW", "LC_IPW"):
        assert values[name]["specials"] == [{
            "value": "@stock", "label": "Preserve stock injection",
            "display_text": "Calculated by ECU",
        }]
        assert values[name]["minimum"] == "0"
        assert "Zero is a valid zero base pulse" in values[name]["description"]
    for name in ("CUT_HYST", "LC_HYST"):
        assert values[name]["specials"][0]["display_value"] == "0"
        assert values[name]["specials"][0]["display_text"] == "Zero hysteresis"
    assert values["LC_HARDRPM"]["specials"] == [{
        "value": "@auto", "label": "Automatic",
        "display_text": "Soft cut + 96 RPM, capped by native hard cut",
    }]
    if "VANOSRPM" in values:
        assert values["VANOSRPM"]["specials"] == [{
            "value": "@stock", "label": "Stock behavior",
            "display_text": "Original VANOS logic",
        }]
