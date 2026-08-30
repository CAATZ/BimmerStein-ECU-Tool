import hashlib

import pytest

import checksum
import patch_service
from tests.conftest import ref


CASES = (
    (
        "MS41.0",
        ["ignition_cut_v7_ms410", "launch_control_v4_ms410", "vanos_minrpm_v2_ms410"],
        {"ignition_cut_v7_ms410": 2, "launch_control_v4_ms410": 8,
         "vanos_minrpm_v2_ms410": 1},
    ),
    (
        "MS41.1",
        ["ignition_cut_v7_ms411", "launch_control_v4_ms411"],
        {"ignition_cut_v7_ms411": 2, "launch_control_v4_ms411": 8},
    ),
    (
        "MS41.2",
        ["ignition_cut_v7", "launch_control_v4_ms412"],
        {"ignition_cut_v7": 2, "launch_control_v4_ms412": 8},
    ),
    (
        "MS41.3",
        ["ignition_cut_v7", "launch_control_v5"],
        {"ignition_cut_v7": 2, "launch_control_v5": 8},
    ),
)

EXPECTED_PARAMETER_IDS = {
    "ignition_cut_v7": {"CUTSW", "CUTRPM"},
    "launch_control_v5": {
        "LC_SW", "LC_CUTTYPE", "LC_CLUTCHPOL", "LC_MAXRPM", "LC_ARMSPEED",
        "LC_MAXSPEED", "LC_MINTPS", "LC_HARDRPM",
    },
    "launch_control_v4": {
        "LC_SW", "LC_CUTTYPE", "LC_CLUTCHPOL", "LC_MAXRPM", "LC_ARMSPEED",
        "LC_MAXSPEED", "LC_MINTPS", "LC_HARDRPM",
    },
    "vanos_minrpm": {"VANOSRPM"},
}


def _built(variant, patch_ids):
    return patch_service.build_image(ref(variant), patch_ids)[0]


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
        ("MS41.0", "ignition_cut_v7_ms410"),
        ("MS41.1", "ignition_cut_v7_ms411"),
        ("MS41.2", "ignition_cut_v7"),
        ("MS41.3", "ignition_cut_v7"),
    ),
)
def test_ignition_parameters_round_trip_without_touching_identity(variant, patch_id):
    source = _built(variant, [patch_id])
    group = patch_service.editable_parameters(source)[0]
    source_identity = source[0x5CD5:0x5F8B]

    result, report = patch_service.apply_parameter_changes(
        source,
        patch_id,
        {"CUTSW": "pin80", "CUTRPM": "4000"},
        expected_sha256=hashlib.sha256(source).hexdigest(),
        expected_descriptor_token=group["descriptor_token"],
    )

    patch = patch_service.definitions()[patch_id]
    cals = patch["cave"]["cals"]
    assert result[cals["CUTSW"]] == 0x01
    assert result[cals["CUTRPM"]] == 125
    assert result[0x5CD5:0x5F8B] == source_identity
    assert checksum.verify_checksum(bytearray(result))[0]
    assert report["source_sha256"] == hashlib.sha256(source).hexdigest()
    assert report["result_sha256"] == hashlib.sha256(result).hexdigest()
    assert len(report["changes"]) == 2


def test_launch_parameters_validate_and_keep_named_sentinels():
    source = _built("MS41.3", ["ignition_cut_v7", "launch_control_v5"])
    groups = {group["patch_id"]: group for group in patch_service.editable_parameters(source)}
    launch = groups["launch_control_v5"]
    hard = next(parameter for parameter in launch["parameters"]
                if parameter["id"] == "LC_HARDRPM")
    assert hard["current"] == "@auto"
    assert hard["current_display"] == "Automatic: soft cut + 96 RPM"

    result, _report = patch_service.apply_parameter_changes(
        source,
        "launch_control_v5",
        {
            "LC_SW": "pin80", "LC_CUTTYPE": "fuel", "LC_CLUTCHPOL": "active_low",
            "LC_MAXRPM": "4000", "LC_ARMSPEED": "5", "LC_MAXSPEED": "10",
            "LC_MINTPS": "50", "LC_HARDRPM": "4192",
        },
        expected_descriptor_token=launch["descriptor_token"],
    )
    values = {
        parameter["id"]: parameter
        for group in patch_service.editable_parameters(result)
        if group["patch_id"] == "launch_control_v5"
        for parameter in group["parameters"]
    }
    assert values["LC_MAXRPM"]["current"] == "4000"
    assert values["LC_HARDRPM"]["current"] == "4192"
    assert values["LC_MINTPS"]["current"] == "49.82"


def test_launch_relationships_fail_closed_when_enabled():
    source = _built("MS41.3", ["ignition_cut_v7", "launch_control_v5"])
    with pytest.raises(patch_service.PatchError, match="LC_MAXSPEED"):
        patch_service.apply_parameter_changes(
            source,
            "launch_control_v5",
            {"LC_SW": "pin80", "LC_ARMSPEED": "10", "LC_MAXSPEED": "5"},
        )
    with pytest.raises(patch_service.PatchError, match="LC_HARDRPM"):
        patch_service.apply_parameter_changes(
            source,
            "launch_control_v5",
            {
                "LC_SW": "pin80", "LC_CUTTYPE": "fuel", "LC_ARMSPEED": "5",
                "LC_MAXSPEED": "10", "LC_MAXRPM": "4000", "LC_HARDRPM": "3968",
            },
        )


def test_unknown_stale_partial_and_uninstalled_requests_are_rejected():
    source = _built("MS41.3", ["ignition_cut_v7"])
    with pytest.raises(patch_service.PatchError, match="unknown patch parameter"):
        patch_service.apply_parameter_changes(source, "ignition_cut_v7", {"OFFSET": "1"})
    with pytest.raises(patch_service.PatchError, match="string ids and values"):
        patch_service.apply_parameter_changes(source, "ignition_cut_v7", {"CUTRPM": 4000})
    with pytest.raises(patch_service.PatchError, match="source ROM changed"):
        patch_service.apply_parameter_changes(
            source, "ignition_cut_v7", {"CUTRPM": "4000"}, expected_sha256="0" * 64)
    with pytest.raises(patch_service.PatchError, match="256 KiB"):
        patch_service.editable_parameters(source[:0x6000])
    with pytest.raises(patch_service.PatchError, match="not an editable current installation"):
        patch_service.apply_parameter_changes(
            ref("MS41.3"), "ignition_cut_v7", {"CUTRPM": "4000"})


def test_parameter_edit_does_not_mutate_the_source_buffer():
    source = bytearray(_built("MS41.0", ["vanos_minrpm_v2_ms410"]))
    original = bytes(source)
    result, _report = patch_service.apply_parameter_changes(
        source, "vanos_minrpm_v2_ms410", {"VANOSRPM": "4000"})
    assert bytes(source) == original
    assert result != original
