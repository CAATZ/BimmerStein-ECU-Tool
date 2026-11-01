from copy import deepcopy

import pytest

import patch_service


def parameters(prefix="CUT"):
    family = "ignition_cut_v14" if prefix == "CUT" else "launch_control_v14"
    values = {
        "CUTRPM": "4000", "LC_MAXRPM": "4000", "CUT_HYST": "0", "LC_HYST": "0",
        "CUT_RETARD_RANGE": "32", "LC_RETARD_RANGE": "32", "CUT_IGN_ANGLE": "-10.125",
        "LC_CUT_IGN_ANGLE": "-10.125", "CUT_IPW": "@stock", "LC_IPW": "@stock",
        "LC_CUTTYPE": "ignition",
        "LC_SW": "always", "LC_ARMSPEED": "1", "LC_MAXSPEED": "10", "LC_MINTPS": "50",
    }
    return [{**spec, "current": values.get(spec["id"], "0")}
            for spec in patch_service._EDITABLE_PARAMETER_FAMILIES[family]]


@pytest.mark.parametrize("prefix", ("CUT", "LC"))
@pytest.mark.parametrize("hysteresis,resume,overlap,fallback", (
    ("0", 4000, False, False), ("32", 3968, True, False),
    ("@legacy_zero", 4000, False, False), ("4000", 4000, False, True),
    ("4096", 4000, False, True),
))
def test_preview_explains_strict_release_and_range_overlap_without_mutation(
        prefix, hysteresis, resume, overlap, fallback):
    specs = parameters(prefix)
    draft = {prefix + "_HYST": hysteresis}
    before = deepcopy((specs, draft))
    result = patch_service.parameter_feedback(specs, draft)
    assert f"Spark resumes below {resume} RPM." in result[prefix + "_HYST"]
    assert ("uses zero hysteresis" in result[prefix + "_HYST"]) == fallback
    text = result[prefix + "_RETARD_RANGE"]
    assert "3968 RPM to below 4000 RPM" in text
    assert ("spark stays cut through this range" in text) == overlap
    assert (specs, draft) == before


def test_feedback_uses_rounded_drafts_and_keeps_off_distinct():
    result = patch_service.parameter_feedback(parameters(), {
        "CUTRPM": "4200", "CUT_HYST": "32", "CUT_IGN_ANGLE": "@off",
    })
    assert "4160 RPM to below 4192 RPM" in result["CUT_RETARD_RANGE"]
    assert "Fixed angle is off." in result["CUT_RETARD_RANGE"]
    assert "spark stays cut" not in result["CUT_RETARD_RANGE"]
    assert "Spark resumes below 4160 RPM." in result["CUT_HYST"]


@pytest.mark.parametrize("draft", ({"CUT_RETARD_RANGE": "0"}, {"CUT_RETARD_RANGE": "4000"},
                                   {"CUT_RETARD_RANGE": "NaN"}, {"CUTRPM": ""}))
def test_incomplete_or_invalid_draft_has_no_invented_range(draft):
    result = patch_service.parameter_feedback(parameters(), draft)
    assert result["CUT_RETARD_RANGE"].startswith("Choose valid cut RPM")


@pytest.mark.parametrize("hysteresis", ("", "NaN"))
def test_incomplete_hysteresis_does_not_hide_valid_independent_timing_range(hysteresis):
    result = patch_service.parameter_feedback(parameters(), {"CUT_HYST": hysteresis})
    assert "Enter valid cut RPM and hysteresis" in result["CUT_HYST"]
    assert "3968 RPM to below 4000 RPM" in result["CUT_RETARD_RANGE"]
    assert "spark stays cut" not in result["CUT_RETARD_RANGE"]


@pytest.mark.parametrize("width", ("0", "4000"))
def test_disabled_angle_marks_invalid_range_as_unused(width):
    result = patch_service.parameter_feedback(parameters(), {
        "CUT_RETARD_RANGE": width, "CUT_IGN_ANGLE": "@off",
    })
    assert result["CUT_RETARD_RANGE"].startswith("Fixed angle is off. To preview its unused range")


@pytest.mark.parametrize("prefix", ("CUT", "LC"))
@pytest.mark.parametrize("hysteresis", ("0", "@legacy_zero", "32"))
def test_zero_cut_rpm_cannot_release_by_rpm(prefix, hysteresis):
    result = patch_service.parameter_feedback(parameters(prefix), {
        "CUTRPM" if prefix == "CUT" else "LC_MAXRPM": "0", prefix + "_HYST": hysteresis,
    })
    assert "RPM alone cannot release spark cut at a zero cut RPM." in result[prefix + "_HYST"]
    assert "Choose valid cut RPM" in result[prefix + "_RETARD_RANGE"]


def test_fuel_mode_marks_preserved_ignition_preview_inactive():
    result = patch_service.parameter_feedback(parameters("LC"), {"LC_CUTTYPE": "fuel", "LC_HYST": "32"})
    assert result["LC_HYST"].startswith("Ignition mode only.")
    assert "Inactive for the selected cut type." in result["LC_RETARD_RANGE"]
    assert "spark stays cut" not in result["LC_RETARD_RANGE"]


@pytest.mark.parametrize("mode", ("always", "pin80", "pin81", "pin82"))
def test_launch_feedback_explains_strict_rearming_and_continuous_release_gates(mode):
    specs = parameters("LC")
    before = deepcopy(specs)
    result = patch_service.parameter_feedback(specs, {
        "LC_SW": mode, "LC_CLUTCHPOL": "active_low", "LC_ARMSPEED": "1.2", "LC_MAXSPEED": "10.2",
    })["LC_ARMSPEED"]
    assert "Arms below 1 km/h" in result
    assert "throttle at least 49.82 %" in result
    assert "Releases at 10 km/h or below that throttle" in result
    assert "rearming requires speed below 1 km/h" in result
    assert ("Always skips the switch only" in result) == (mode == "always")
    assert ("ends optional fixed timing" in result) == (mode != "always")
    assert specs == before


@pytest.mark.parametrize("draft,expected", (
    ({"LC_SW": "off"}, "Launch is off"),
    ({"LC_SW": "@raw:03"}, "Choose Always"),
    ({"LC_CUTTYPE": "@raw:FF"}, "Choose Fuel cut or Ignition cut"),
    ({"LC_SW": "pin80", "LC_CLUTCHPOL": "@raw:FF"}, "Choose Active-high or Active-low"),
    ({"LC_ARMSPEED": "10"}, "Choose valid throttle and speeds"),
    ({"LC_MINTPS": ""}, "Choose valid throttle and speeds"),
))
def test_launch_feedback_handles_disabled_unknown_and_incomplete_drafts(draft, expected):
    result = patch_service.parameter_feedback(parameters("LC"), draft)["LC_ARMSPEED"]
    assert result.startswith(expected)
    assert "Arms below" not in result


def test_launch_feedback_uses_family_specific_units_from_mobile_schema():
    specs = parameters("LC")
    for spec in specs:
        if spec["id"] == "LC_MINTPS":
            spec.update(units="° throttle", maximum="119.5", step="0.46862745098039")
    # Android transmits these fields, not private codec/choice definitions.
    mobile_specs = [{key: spec.get(key) for key in (
        "id", "kind", "current", "minimum", "maximum", "step", "decimals", "units",
    )} for spec in specs]
    text = patch_service.parameter_feedback(mobile_specs)["LC_ARMSPEED"]
    assert "throttle at least 50.14 ° throttle" in text


def test_fixed_ipw_feedback_distinguishes_base_pulse_and_native_output_gates():
    assert "CUT_IPW" not in patch_service.parameter_feedback(parameters())
    result = patch_service.parameter_feedback(parameters(), {"CUT_IPW": "0"})
    assert "not throughout the timing range" in result["CUT_IPW"]
    assert "native fuel cuts can still stop injection" in result["CUT_IPW"]
    assert "not a command to switch injection off" in result["CUT_IPW"]


@pytest.mark.parametrize("revision,expected", ((None, False), ("V17", False), ("V18", True)))
def test_current_fuel_fallback_is_not_claimed_for_older_patches(revision, expected):
    result = patch_service.parameter_feedback(parameters(), {"CUT_IPW": "0"}, revision=revision)
    assert ("cannot fit the validated timing interval" in result["CUT_IPW"]) == expected
