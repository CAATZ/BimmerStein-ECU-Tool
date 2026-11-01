"""Real BMW strings and English display text, without altering stored bytes."""

import json
from pathlib import Path

import pytest

import ms42_ms43_diagnostics as diagnostics
from ms42_ms43_vin import decode_aif, plan_vin
from tests.test_ms42_ms43_diagnostics import aif_image, aif_record, record


@pytest.mark.parametrize("programmer,expected", (
    (bytes.fromhex("31 00 00 F0 00"), "1"),  # Exact MS430069 reference at raw 3C63.
    (bytes.fromhex("34 00 30 21 00"), "4"),  # Exact MS42 0110C6 reference at raw 3C6D.
    (b"TEST1", "TEST1"),
    (b"ABC  ", "ABC"),
    (b"A\xF0B\x00\x00", "Raw 41 F0 42"),
    (b"A\nB\x00\x00", "Raw 41 0A 42"),
))
def test_programmer_serial_ends_at_first_nul_and_nontext_is_not_replaced(programmer, expected):
    raw = bytearray(aif_record())
    raw[35:41] = programmer + b"\x00"
    history = aif_image(raw)
    info = decode_aif(history)
    assert info["programmer"] == expected
    assert bytes.fromhex(info["aif_records"][0]["raw"]) == bytes(raw)
    text = diagnostics.format_aif_record(info["aif_records"][0])
    assert "Programmer: " + expected in text
    assert "\x00" not in text and "\ufffd" not in text
    planned = plan_vin(history, "WBAAM31010FG54321")
    assert planned.record[35:41] == raw[35:41]
    assert planned.target_history[:46] == history[:46]


@pytest.mark.parametrize("family,filename,start,expected", (
    ("MS42", "Siemens_MS42_0110C6_E46_M52TUB28_EU3_LHD.bin", 0x3C4A, "4"),
    ("MS43", "Siemens_MS43_MS430069_E46_M54B30_US.bin", 0x3C40, "1"),
))
def test_exact_stock_programmer_fields_reproduce_screenshot_without_garbage(family, filename, start, expected):
    path = Path(__file__).resolve().parents[2] / "_shared" / f"REF_{family}" / filename
    if not path.exists():
        pytest.skip("Private exact reference unavailable")
    image = path.read_bytes()
    info = decode_aif(image[start:start + 644])
    assert info["programmer"] == expected
    assert "\ufffd" not in diagnostics.format_identity(info)


@pytest.mark.parametrize("code,expected", (
    (3, "Ignition, cylinder 6"),
    (125, "Electric cooling fan"),
    (131, "CAN message timeout: instrument cluster (INSTR2)"),
    (124, "Variable intake manifold (DISA)"),
    (123, "Map-controlled thermostat heater"),
    (63, "Ambient-temperature CAN signal"),
    (132, "CAN message timeout: instrument cluster (INSTR3)"),
))
def test_screenshot_faults_are_english_in_shared_table_and_detail(code, expected):
    raw = record(code)
    decoded = diagnostics.decode_fault_record(raw, "MS43")
    assert decoded.description == expected
    assert decoded.raw_record == raw and decoded.code == code
    assert decoded.sae_code == f"DS2-{code:03d}"
    assert expected in diagnostics.format_fault_detail(decoded)


def test_all_published_faults_qualifiers_and_environment_labels_have_english_display_text():
    for family in ("MS42", "MS43"):
        profile = diagnostics._profile(family)
        assert set(profile["qualifiers_en"]) == set(profile["qualifiers"])
        assert set(profile["environment_labels_en"]) == set(profile["environment"])
        for definition in profile["faults"].values():
            assert definition["text"] and definition["text_en"]
            assert definition["text_en"].isascii()
        for source in (profile["qualifiers_en"], profile["environment_labels_en"]):
            assert all(text and text.isascii() for text in source.values())


def test_translation_keeps_original_bmw_tables_available():
    data = json.loads((Path(__file__).resolve().parents[1] / "data/ms42_ms43/diagnostics.json").read_text())
    assert data["MS43"]["faults"]["125"]["text"] == "E-Luefter"
    assert data["MS43"]["faults"]["125"]["text_en"] == "Electric cooling fan"
    profile = data["MS43"]
    active_id = next(key for key, value in profile["qualifiers"].items() if value == "Fehler momentan vorhanden")
    assert profile["qualifiers_en"][active_id] == "Currently present"
    profile = data["MS42"]
    battery_id = next(key for key, value in profile["environment"].items() if value[0] == "Batteriespannung")
    assert profile["environment_labels_en"][battery_id] == "Battery voltage"


def test_unknown_code_does_not_borrow_another_familys_description():
    for family in ("MS42", "MS43"):
        decoded = diagnostics.decode_fault_record(record(255), family)
        assert decoded.description == f"Unknown {family} fault code 255"


@pytest.mark.parametrize("family", ("MS42", "MS43"))
@pytest.mark.parametrize("code,bank", ((248, 1), (249, 2)))
def test_post_fuel_cut_oxygen_monitor_is_not_mistranslated_as_secondary_air(family, code, bank):
    decoded = diagnostics.decode_fault_record(record(code, status=0x7C), family)
    assert decoded.description == (
        f"Downstream oxygen-sensor signal after overrun fuel cut is implausible, bank {bank}")
    assert "secondary air" not in diagnostics.format_fault_detail(decoded).lower()
    assert "Oxygen-sensor signal after overrun fuel cut is implausible" in decoded.qualifiers
