import json
from pathlib import Path
import xml.etree.ElementTree as ET

import romraider_defs
from engines.patcher import patch_ms41
from engines.patcher.romraider import build_patch_definitions
from ms41 import MS41ECU
from tests.conftest import ref


ROOT = Path(__file__).resolve().parents[1]
PATCH_DIR = ROOT / "engines" / "patcher" / "patches"
FRAGMENT = (
    ROOT
    / "engines"
    / "patcher"
    / "romraider"
    / "ms412_ignition_cut_v9_launch_control_v7.xml"
)
STANDALONE = FRAGMENT.parent / "BimmerStein MS41 Patch Definitions.xml"

TABLE_TO_CAL = {
    "Ignition Cut - Switch Input": "CUTSW",
    "Ignition Cut - RPM Limit": "CUTRPM",
    "Ignition Cut - RPM Hysteresis": "CUT_HYST",
    "Ignition Cut - Fixed Injector Pulse Width": "CUT_IPW",
    "LC - Switch / Mode": "LC_SW",
    "LC - Cut Type": "LC_CUTTYPE",
    "LC - Clutch Polarity": "LC_CLUTCHPOL",
    "LC - Soft Cut RPM": "LC_MAXRPM",
    "LC - Hard Cut RPM": "LC_HARDRPM",
    "LC - Ignition RPM Hysteresis": "LC_HYST",
    "LC - Ignition Fixed Injector Pulse Width": "LC_IPW",
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


def test_source_fragment_matches_ms412_patch_calibration_addresses():
    ignition = _patch("ignition_cut_v9_ms412")["cave"]["cals"]
    launch_413 = _patch("launch_control_v7")["cave"]["cals"]
    launch_412 = _patch("launch_control_v7_ms412")["cave"]["cals"]
    assert launch_412 != launch_413

    expected = {**ignition, **launch_412}
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
        "Ignition Cut - RPM Limit", "LC - Soft Cut RPM", "LC - Hard Cut RPM",
    ):
        scaling = tables[name].find("scaling")
        assert scaling is not None
        assert scaling.attrib["expression"] == "x*32"
        assert scaling.attrib["to_byte"] == "x/32"
        assert scaling.attrib["fineincrement"] == "32"
        assert scaling.attrib["coarseincrement"] == "320"

    for name in (
        "Ignition Cut - RPM Hysteresis",
        "LC - Ignition RPM Hysteresis",
    ):
        scaling = tables[name].find("scaling")
        assert scaling is not None
        assert scaling.attrib["expression"] == "(x*32)%8160"
        assert scaling.attrib["to_byte"] == "x/32"
        assert scaling.attrib["fineincrement"] == "32"
        assert scaling.attrib["coarseincrement"] == "320"
        assert [
            romraider_defs._eval(scaling.attrib["expression"], raw)
            for raw in (0x00, 0x01, 0xFE, 0xFF)
        ] == [0, 32, 8128, 0]


def test_fixed_ipw_definitions_match_the_c166_word_and_scaling():
    tables = _tables()
    for name in (
        "Ignition Cut - Fixed Injector Pulse Width",
        "LC - Ignition Fixed Injector Pulse Width",
    ):
        table = tables[name]
        scaling = table.find("scaling")
        assert table.attrib["storagetype"] == "uint16"
        assert table.attrib["endian"] == "little"
        assert scaling.attrib["units"] == "ms (-0.00534 = stock)"
        assert scaling.attrib["expression"] == "((x+1)%65536)*.00534-.00534"
        assert scaling.attrib["to_byte"] == "if(x==-0.00534,65535,x/.00534)"
        assert scaling.attrib["format"] == "0.00000"

        displayed = {
            raw: romraider_defs._eval(scaling.attrib["expression"], raw)
            for raw in (0x0000, 0x0001, 0x1234, 0xFFFE, 0xFFFF)
        }
        assert displayed[0x0000] == 0
        assert displayed[0x0001] == 0.00534
        assert displayed[0x1234] == 24.8844
        assert displayed[0xFFFF] == -0.00534

        for raw, value in displayed.items():
            restored = 0xFFFF if value == -0.00534 else round(value / 0.00534)
            assert restored == raw


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


def test_combined_builder_remaps_only_ms413_launch_controls():
    rebuilt = build_patch_definitions.inject_definition(
        _synthetic_definition(),
        FRAGMENT.read_text(encoding="utf-8"),
    )
    root = ET.fromstring(rebuilt)
    addresses = {}
    for rom in root.findall("rom"):
        key = (
            rom.findtext("romid/xmlid"),
            rom.findtext("romid/filesize"),
        )
        launch = next(
            table for table in rom.findall("table")
            if table.attrib.get("name") == "LC - Switch / Mode"
        )
        addresses[key] = int(launch.attrib["storageaddress"], 16)

    assert addresses[("SS1v2", "24kb")] == 0x47E0
    assert addresses[("12", "24kb")] == 0x352C
    assert addresses[("12", "256kb")] == 0x1752C


def test_ms413_v7_documentation_has_no_current_boost_conflict_warning():
    text = (FRAGMENT.parent / "README.md").read_text(encoding="utf-8")
    assert "current Launch and boost control may be configured together" in text
    assert "This restriction does not apply to V7" in text


def test_standalone_artifact_is_reproducible_and_patch_only():
    fragment = FRAGMENT.read_text(encoding="utf-8")
    expected = build_patch_definitions.build_standalone_definition(fragment)
    assert STANDALONE.read_text(encoding="utf-8") == expected

    root = ET.fromstring(expected)
    roms = root.findall("rom")
    assert len(roms) == 12
    patch_tables = set(TABLE_TO_CAL)
    for rom in roms:
        tables = {table.attrib["name"] for table in rom.findall("table")}
        xmlid = rom.findtext("romid/xmlid")
        if "VANOSRT" in xmlid:
            assert tables == patch_tables | {
                "VANOS Retrofit - Minimum RPM (Closed Throttle)"
            }
        else:
            assert tables == patch_tables


def test_standalone_addresses_match_patch_descriptors_for_each_framing():
    root = ET.parse(STANDALONE).getroot()
    for rom in root.findall("rom"):
        rid = rom.find("romid")
        full_read = rid.findtext("filesize") == "256kb"
        xmlid = rid.findtext("xmlid")
        tables = {table.attrib["name"]: table for table in rom.findall("table")}
        if "MS410" in xmlid:
            ignition_id = "ignition_cut_v9_ms410"
            launch_id = "launch_control_v7_ms410"
        elif "MS411" in xmlid:
            ignition_id = "ignition_cut_v9_ms411"
            launch_id = "launch_control_v7_ms411"
        elif "MS412" in xmlid:
            ignition_id = "ignition_cut_v9_ms412"
            launch_id = "launch_control_v7_ms412"
        else:
            ignition_id = "ignition_cut_v9"
            launch_id = "launch_control_v7"
        expected_full = {
            **_patch(ignition_id)["cave"]["cals"],
            **_patch(launch_id)["cave"]["cals"],
        }
        for table_name, cal_name in TABLE_TO_CAL.items():
            full_address = expected_full[cal_name]
            expected_address = (
                full_address if full_read else _storage_address(full_address)
            )
            assert int(
                tables[table_name].attrib["storageaddress"], 16
            ) == expected_address

        if "VANOSRT" in xmlid:
            vanos_id = (
                "vanos_minrpm_v2_ms410"
                if "MS410_VANOSRT3" in xmlid
                else "vanos_minrpm_ms411"
            )
            vanos_full = _patch(vanos_id)["cave"]["cals"]["VANOSRPM"]
            address = int(
                tables["VANOS Retrofit - Minimum RPM (Closed Throttle)"].attrib[
                    "storageaddress"
                ],
                16,
            )
            assert address == (vanos_full if full_read else _storage_address(vanos_full))


def test_standalone_ms410_uses_grounded_input_latch_labels():
    root = ET.parse(STANDALONE).getroot()
    for rom in root.findall("rom"):
        xmlid = rom.findtext("romid/xmlid")
        if "MS410" not in xmlid:
            continue
        for name in ("Ignition Cut - Switch Input", "LC - Switch / Mode"):
            table = next(
                table for table in rom.findall("table")
                if table.attrib.get("name") == name
            )
            states = {
                state.attrib["data"]: state.attrib["name"]
                for state in table.findall("state")
            }
            assert "FD51.1" in states["01"]
            assert "FD51.0" in states["02"]
            assert "FD50.7" in states["04"]


def test_standalone_matches_every_declared_rom_variant():
    definitions = romraider_defs.load_definitions(STANDALONE)
    root = ET.parse(STANDALONE).getroot()
    vanos_markers = {
        rom.findtext("romid/internalidstring")
        for rom in root.findall("rom")
        if "VANOSRT" in rom.findtext("romid/xmlid")
    }
    assert vanos_markers == {"VANOSRT2", "VANOSRT3"}
    for rom in root.findall("rom"):
        rid = rom.find("romid")
        size = 262144 if rid.findtext("filesize") == "256kb" else 24576
        image = bytearray(b"\xFF" * size)
        address = int(rid.findtext("internalidaddress"), 16)
        marker = rid.findtext("internalidstring").encode("ascii")
        image[address:address + len(marker)] = marker
        assert definitions.match(image).xmlid == rid.findtext("xmlid")


def test_standalone_matches_real_images_after_each_tunable_patch_set():
    definitions = romraider_defs.load_definitions(STANDALONE)
    cases = (
        (
            "MS41.3", ["ignition_cut_v9", "launch_control_v7"],
            "BIMMERSTEIN_MS413_SS1V2_24K", "BIMMERSTEIN_MS413_SS1V2_256K",
        ),
        (
            "MS41.2", ["ignition_cut_v9_ms412", "launch_control_v7_ms412"],
            "BIMMERSTEIN_MS412_ID12_24K", "BIMMERSTEIN_MS412_ID12_256K",
        ),
        (
            "MS41.0",
            ["ignition_cut_v9_ms410", "launch_control_v7_ms410"],
            "BIMMERSTEIN_MS410_ID41_24K", "BIMMERSTEIN_MS410_ID41_256K",
        ),
        (
            "MS41.0",
            [
                "ignition_cut_v9_ms410",
                "launch_control_v7_ms410",
                "vanos_minrpm_v2_ms410",
            ],
            "BIMMERSTEIN_MS410_VANOSRT3_24K", "BIMMERSTEIN_MS410_VANOSRT3_256K",
        ),
        (
            "MS41.1",
            ["ignition_cut_v9_ms411", "launch_control_v7_ms411"],
            "BIMMERSTEIN_MS411_ID60_24K", "BIMMERSTEIN_MS411_ID60_256K",
        ),
        (
            "MS41.1",
            [
                "ignition_cut_v9_ms411",
                "launch_control_v7_ms411",
                "vanos_minrpm_ms411",
            ],
            "BIMMERSTEIN_MS411_VANOSRT2_24K", "BIMMERSTEIN_MS411_VANOSRT2_256K",
        ),
    )
    for version, patch_ids, partial_xmlid, full_xmlid in cases:
        patched, _log = patch_ms41.build(ref(version), patch_ids)
        partial = MS41ECU.tune_from_full(patched)
        for image, xmlid in ((partial, partial_xmlid), (patched, full_xmlid)):
            matched = definitions.match(image)
            assert matched.xmlid == xmlid
            tables = definitions.resolve(matched)
            if any(patch_id.startswith("vanos_minrpm") for patch_id in patch_ids):
                value, units, _format = romraider_defs.read_scalar(
                    image, tables["VANOS Retrofit - Minimum RPM (Closed Throttle)"]
                )
                assert (value, units) == (8160, "RPM")
            assert romraider_defs.switch_state(
                image, tables["Ignition Cut - Switch Input"]
            ) == "Off"
            assert romraider_defs.switch_state(
                image, tables["LC - Switch / Mode"]
            ) == "Off"
