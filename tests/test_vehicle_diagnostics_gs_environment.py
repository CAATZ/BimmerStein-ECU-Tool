"""BMW per-fault freeze-frame matrices, including profile-specific encodings."""

from hashlib import sha256
from pathlib import Path

import pytest

import vehicle_diagnostics as diagnostics


PROFILES = ("egs_gs8600", "egs_gs8604", "egs_gs20")


def decode(profile, code, values, mileage=0x1234, *, frequency=1):
    snapshot = bytes(values) + mileage.to_bytes(2, "big")
    record = bytes((code, 0, frequency, 0)) + snapshot * 3
    return diagnostics._parse_egs19(
        diagnostics.PROFILE_BY_FAULT_KEY[profile], b"\x01" + record)[0]


@pytest.mark.parametrize("profile,engine_temp,oil_temp,voltage", [
    ("egs_gs8600", "52", "45", "16"),
    ("egs_gs8604", "52", "60", "16"),
    ("egs_gs20", "60", "60", "19.8"),
])
def test_profile_specific_temperature_and_voltage(profile, engine_temp, oil_temp, voltage):
    fault = decode(profile, 0x22, (100, 100, 198))
    assert "Engine speed: 3200 rpm" in fault.conditions[2]
    assert f"Engine temperature: {engine_temp} °C" in fault.conditions[2]
    assert f"Battery voltage: {voltage} V" in fault.conditions[2]
    fault = decode(profile, 0x04, (0xA3, 100, 198))
    assert f"Transmission oil temperature: {oil_temp} °C" in fault.conditions[2]
    assert "Shift in progress: yes" in fault.conditions[2]
    assert "Last shift: " in fault.conditions[2]


@pytest.mark.parametrize("profile", PROFILES)
def test_physical_units_have_unsigned_boundary_values(profile):
    fault = decode(profile, 0x22, (255, 0, 255), mileage=65535)
    line = fault.conditions[2]
    assert "mileage 655350 km" in line
    assert "Engine speed: 8160 rpm" in line
    assert f"Engine temperature: {-40 if profile == 'egs_gs20' else -48} °C" in line
    assert f"Battery voltage: {'25.5' if profile == 'egs_gs20' else '20.61'} V" in line
    assert "raw values FF 00 FF" in line


@pytest.mark.parametrize("profile", PROFILES)
@pytest.mark.parametrize("frequency", (0, 1, 255))
def test_all_three_snapshots_keep_their_own_values(profile, frequency):
    raw = bytes((0x22, 0, frequency, 0)) + bytes.fromhex(
        "01 02 03 00 01 04 05 06 00 02 07 08 09 FF FF")
    [fault] = diagnostics._parse_egs19(diagnostics.PROFILE_BY_FAULT_KEY[profile], b"\x01" + raw)
    snapshots = fault.conditions[2:]
    assert len(snapshots) == 3
    for index, (speed, mileage) in enumerate(((32, 10), (128, 20), (224, 655350))):
        assert snapshots[index].startswith(f"Snapshot {index + 1}: mileage {mileage} km;")
        assert f"Engine speed: {speed} rpm" in snapshots[index]
    assert fault.raw_record == raw and fault.environment_raw == raw[4:]


@pytest.mark.parametrize("profile", PROFILES)
def test_reduced_environment_count_does_not_decode_unused_bytes(profile):
    fault = decode(profile, 0x51, (198, 1, 255))
    assert fault.conditions[2].count("Battery voltage") == 1
    assert len(fault.conditions[2].split(";")) == 3
    assert fault.environment_raw[:3] == bytes((198, 1, 255))
    if profile == "egs_gs20":
        fault = decode(profile, 0x97, (0xA0, 100, 198))
        assert "Output speed: 3200 rpm" in fault.conditions[2]
        assert "Battery voltage" not in fault.conditions[2]


def test_same_fault_location_has_different_fields_by_profile():
    gs86 = decode("egs_gs8604", 0x32, (255, 100, 198)).conditions[2]
    gs20 = decode("egs_gs20", 0x32, (255, 100, 198)).conditions[2]
    assert "Throttle: 100 %" in gs86 and "Mean rear-wheel speed: 1600 rpm" in gs86
    assert "Output speed: 6336 rpm" in gs86
    assert "Throttle" not in gs20 and "Shift in progress: yes" in gs20
    assert "Transmission oil temperature: 60 °C" in gs20


def test_gs8604_only_turbine_field_and_unknown_fault_preservation():
    fault = decode("egs_gs8604", 0x41, (255, 100, 90))
    assert "Throttle: 100 %; Turbine speed: 3200 rpm; Output speed: 2880 rpm" in fault.conditions[2]
    fault = decode("egs_gs8600", 0x41, (255, 100, 90))
    assert not any("Snapshot" in item for item in fault.conditions)
    assert fault.environment_raw[:3] == bytes((255, 100, 90))


@pytest.mark.parametrize("profile", PROFILES)
@pytest.mark.parametrize("raw", (0, 1, 2, 3, 7, 0x80, 0xC0, 0xFF))
def test_checksum_region_keeps_unknown_codes_and_ignores_unrelated_bits(profile, raw):
    text = decode(profile, 0x50, (1, raw, 2)).conditions[2]
    expected = {0: "ROM / boot sector", 1: "Program", 2: "Data"}.get(
        raw & 7, f"unknown / implausible (0x{raw & 7:02X})")
    assert f"Checksum fault region: {expected}" in text


@pytest.mark.parametrize("profile", PROFILES)
def test_selector_contacts_and_solenoid_bits_are_profile_specific(profile):
    text = decode(profile, 0x13, (0xA7, 0xA0, 198)).conditions[2]
    assert "Solenoid valve 1: yes; Solenoid valve 2: no; Solenoid valve 3: yes" in text
    assert "Shift-lock solenoid: yes; Brake pressed: yes" in text
    assert "Selector contact L1: yes" in text
    if profile == "egs_gs20":
        assert "Selector contact L2" not in text
        assert "Selector contact L3: no; Selector contact L4: yes; Selector contact L5: no" in text
        assert "Torque-converter clutch closed: yes" in text
    else:
        assert "Selector contact L2: no; Selector contact L3: yes; Selector contact L4: no" in text
        assert "Torque-converter clutch closed" not in text


def test_shift_mask_and_discrete_tables_do_not_cross_profiles():
    gs86 = decode("egs_gs8600", 0x04, (0xD4, 40, 198)).conditions[2]
    gs20 = decode("egs_gs20", 0x04, (0xD4, 40, 198)).conditions[2]
    assert "Gear engagement: yes" in gs86
    assert "Last shift: downshift 4->3" in gs86
    assert "Gear engagement" not in gs20
    assert "Last shift: unknown / implausible (0x54)" in gs20
    assert "Last shift: engagement P/N->D" in decode("egs_gs20", 0x04, (0xC8, 40, 198)).conditions[2]


@pytest.mark.parametrize("profile", PROFILES[:2])
def test_bmw_gear_mask_table_inconsistency_is_not_silently_normalized(profile):
    text = decode(profile, 0x30, (0xA0, 40, 2)).conditions[2]
    assert "Gear ratio: unknown / implausible (0xA0)" in text
    assert "Torque-converter clutch: Closed" in text
    assert "Gear: 5" not in text


def test_gs20_numeric_gear_and_torque_reduction():
    text = decode("egs_gs20", 0x30, (5, 40, 4)).conditions[2]
    assert "Gear: 5; Transmission oil temperature: 0 °C" in text
    assert "Torque-converter clutch closed: yes" in text
    assert "EGS torque reduction: 100 %" in decode("egs_gs20", 0x91, (0, 0, 255)).conditions[2]


@pytest.mark.parametrize("profile", PROFILES)
@pytest.mark.parametrize("code", (0, 0xFE, 0xFF))
def test_unknown_location_never_acquires_inferred_fields(profile, code):
    fault = decode(profile, code, (255, 128, 99))
    assert len(fault.conditions) == 2
    assert fault.environment_raw[:3] == bytes((255, 128, 99))


@pytest.mark.parametrize("name,profile,expected_hash", [
    ("GS8600", "egs_gs8600", "9480d8c42b06d0b24359902b3101d2d82b5588996cc28a6562749cdcbee964d5"),
    ("gs8604", "egs_gs8604", "a5e87ff75b65030818e09cb076f099d8cac0f91024634c91300fe4f280ddc59f"),
    ("gs20", "egs_gs20", "d1d0baa26e043fcb2a4f13a06c4df840f655380c7e1519157355cd3bcada1e50"),
])
def test_every_matrix_row_and_used_field_matches_exact_bmw_source(monkeypatch, name, profile, expected_hash):
    source = Path.home() / "ECU-reference-data/diagnostics" / (name + ".prg")
    decoder = Path(__file__).resolve().parents[1] / "tests/reference_tools/diagnostic_decoder"
    if not source.is_file() or not (decoder / "decoderPrg.py").is_file():
        pytest.skip("private exact BMW diagnostic source unavailable")
    assert sha256(source.read_bytes()).hexdigest() == expected_hash
    monkeypatch.syspath_prepend(str(decoder))
    from decoderPrg import parse_prg

    tables = {table.name: table for table in parse_prg(str(source)).tables}
    matrix = {int(row.values[0], 16): tuple(
        int(value, 16) if value.startswith("0x") else value
        for value in row.values[4:3 + int(row.values[1])])
        for row in tables["FUMWELTMATRIX"].rows if row.values[0] != "0xXY"}
    actual_matrix = {"egs_gs8600": diagnostics._EGS86_ENVIRONMENT,
                     "egs_gs8604": diagnostics._GS8604_ENVIRONMENT,
                     "egs_gs20": diagnostics._GS20_ENVIRONMENT}[profile]
    assert matrix == actual_matrix
    source_fields = {int(row.values[0], 16): row.values
                     for row in tables["FUMWELTTEXTE"].rows if row.values[0] != "0xXY"}
    for group in {field for fields in matrix.values() for field in fields if isinstance(field, str)}:
        group_row = tables[group].rows[0].values
        assert diagnostics._EGS_ENVIRONMENT_GROUPS[group] == tuple(int(value, 16) for value in group_row[1:1 + int(group_row[0])])
    used_ids = {field_id for fields in matrix.values() for field in fields
                for field_id in diagnostics._EGS_ENVIRONMENT_GROUPS.get(field, (field,))}
    for field_id in used_ids:
        row = source_fields[field_id]
        for raw in range(256):
            text = diagnostics._egs_environment_value(profile, field_id, raw)
            if row[2] == "0/1":
                assert text.endswith(": yes" if raw & int(row[3], 16) else ": no")
            elif row[2] == "0-n":
                masked = raw & int(row[3], 16)
                source_choices = {int(item.values[0], 16) for item in tables[row[4].upper()].rows
                                  if item.values[0] != "0xXY"}
                assert ("unknown / implausible" in text) == (masked not in source_choices)
                if masked not in source_choices:
                    assert f"0x{masked:02X}" in text
            else:
                value = raw * float(row[5]) / float(row[6]) + float(row[7])
                number = text.split(": ")[1].split()[0]
                assert float(number) == pytest.approx(value, abs=0.005)
    for code, fields in matrix.items():
        line = decode(profile, code, (0xA5, 0x80, 198)).conditions[2]
        expected = sum(len(diagnostics._EGS_ENVIRONMENT_GROUPS.get(field, (field,))) for field in fields)
        assert len(line.split(";")) == expected + 2
