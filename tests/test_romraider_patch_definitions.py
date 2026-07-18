import json
from pathlib import Path
import xml.etree.ElementTree as ET

from engines.patcher.romraider import build_patch_definitions


ROOT = Path(__file__).resolve().parents[1]
PATCH_DIR = ROOT / "engines" / "patcher" / "patches"
FRAGMENT = (
    ROOT
    / "engines"
    / "patcher"
    / "romraider"
    / "ms412_ignition_cut_v7_launch_control_v4.xml"
)

TABLE_TO_CAL = {
    "Ignition Cut - Switch Input": "CUTSW",
    "Ignition Cut - RPM Limit": "CUTRPM",
    "LC - Switch / Mode": "LC_SW",
    "LC - Cut Type": "LC_CUTTYPE",
    "LC - Clutch Polarity": "LC_CLUTCHPOL",
    "LC - Soft Cut RPM": "LC_MAXRPM",
    "LC - Hard Cut RPM": "LC_HARDRPM",
    "LC - Arm Speed": "LC_ARMSPEED",
    "LC - Max Speed": "LC_MAXSPEED",
    "LC - Min TPS": "LC_MINTPS",
}


def _patch(name):
    return json.loads((PATCH_DIR / f"{name}.json").read_text(encoding="utf-8"))


def _storage_address(full_offset):
    """Inverse of fo(SA) = (0x10000 + SA) XOR 0x4000."""
    return (int(full_offset) ^ 0x4000) - 0x10000


def _tables():
    root = ET.fromstring(f"<fragment>{FRAGMENT.read_text(encoding='utf-8')}</fragment>")
    return {table.attrib["name"]: table for table in root.findall("table")}


def test_current_fragment_matches_patch_calibration_addresses():
    ignition = _patch("ignition_cut_v7")["cave"]["cals"]
    launch_413 = _patch("launch_control_v4")["cave"]["cals"]
    launch_412 = _patch("launch_control_v4_ms412")["cave"]["cals"]
    assert launch_412 == launch_413

    expected = {**ignition, **launch_413}
    tables = _tables()
    assert set(tables) == set(TABLE_TO_CAL)
    for table_name, cal_name in TABLE_TO_CAL.items():
        assert int(tables[table_name].attrib["storageaddress"], 16) == _storage_address(
            expected[cal_name]
        )


def test_switch_encodings_match_runtime_pin_bits():
    tables = _tables()
    for name in ("Ignition Cut - Switch Input", "LC - Switch / Mode"):
        states = {state.attrib["data"]: state.attrib["name"] for state in tables[name].findall("state")}
        assert states["FF"] == "Off"
        assert "SIR fd60.9" in states["01"]
        assert "SIR fd60.8" in states["02"]
        assert "SIR fd60.7" in states["04"]


def test_rpm_scalings_use_representable_increments():
    tables = _tables()
    for name in (
        "Ignition Cut - RPM Limit", "LC - Soft Cut RPM", "LC - Hard Cut RPM"
    ):
        scaling = tables[name].find("scaling")
        assert scaling is not None
        assert scaling.attrib["expression"] == "x*32"
        assert scaling.attrib["to_byte"] == "x/32"
        assert scaling.attrib["fineincrement"] == "32"
        assert scaling.attrib["coarseincrement"] == "320"


def _synthetic_definition(marker=""):
    blocks = []
    for xmlid, filesize in (("SS1v2", "24kb"), ("12", "24kb"), ("12", "256kb")):
        blocks.append(
            f"<rom><romid><xmlid>{xmlid}</xmlid><filesize>{filesize}</filesize>"
            f"</romid>{marker}</rom>"
        )
    return "<roms>" + "".join(blocks) + "</roms>"


def test_builder_upgrades_previous_marker_blocks():
    for old_begin, old_end in build_patch_definitions.LEGACY_MARKERS:
        old_payload = (
            f"{old_begin}<table name=\"Legacy Patch Table\" storageaddress=\"0x1\" />"
            f"{old_end}"
        )
        legacy = _synthetic_definition(old_payload)
        rebuilt = build_patch_definitions.inject_definition(
            legacy, FRAGMENT.read_text(encoding="utf-8")
        )

        assert old_begin not in rebuilt and old_end not in rebuilt
        assert rebuilt.count(build_patch_definitions.BEGIN) == 3
        assert rebuilt.count('name="LC - Hard Cut RPM"') == 3
