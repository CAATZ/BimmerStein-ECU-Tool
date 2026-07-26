import ecu_config
import pytest


def _partial_with_byte4(value):
    data = bytearray(b"\xFF" * ecu_config.TUNE_SIZE)
    data[4] = value
    return data


def _partial_with_byte6(value, cal_id=None):
    data = bytearray(b"\xFF" * ecu_config.TUNE_SIZE)
    data[6] = value
    if cal_id is not None:
        data[0x0E:0x16] = cal_id
    return data


def _oxygen_feature():
    return next(f for f in ecu_config.FEATURES if f.name == "Oxygen Sensors")


def _o2_program_feature():
    return next(
        f for f in ecu_config.FEATURES
        if f.name == "O2 Feedback Program Gate (Experimental)"
    )


def _full_with_identity(cal_id, program_id, byte6=0x14):
    data = bytearray(b"\xFF" * ecu_config.FULL_ROM_SIZE)
    data[0x1400E:0x14016] = cal_id
    data[0x6025:0x602C] = program_id
    data[ecu_config.CAL_BASE_FULL + 6] = byte6
    return data


@pytest.mark.parametrize(
    "profile, expected",
    [
        ("ID41", [
            ("Dual (2-channel)", 0x1C),
            ("Single (1-channel)", 0x18),
            ("Disabled", 0x04),
        ]),
        ("ID42", [
            ("Dual (2-channel)", 0x1C),
            ("Single (1-channel)", 0x18),
            ("Disabled", 0x04),
        ]),
        ("ID59", [
            ("Dual (2-channel)", 0x08),
            ("Single (1-channel)", 0x18),
            ("Disabled", 0x04),
        ]),
        ("ID85", [("Dual (2-channel)", 0x14), ("Single (1-channel)", 0x0C)]),
        ("ID60", [("Dual (2-channel)", 0x14), ("Single (1-channel)", 0x0C)]),
        ("ID12", [("Dual (2-channel)", 0x14), ("Single (1-channel)", 0x0C)]),
    ],
)
def test_oxygen_sensor_choices_follow_cal_id_profile(profile, expected):
    assert _oxygen_feature().options_for(profile, ecu_config.TUNE_SIZE) == expected


@pytest.mark.parametrize("profile", ("ID12", "ID60"))
def test_id12_and_id60_add_experimental_disable_only_when_program_gate_is_present(profile):
    feature = _oxygen_feature()
    assert feature.options_for(profile, ecu_config.FULL_ROM_SIZE) == [
        ("Dual (2-channel)", 0x14),
        ("Single (1-channel)", 0x0C),
    ]
    assert feature.options_for(
        profile, ecu_config.FULL_ROM_SIZE, program_gate_present=True) == [
        ("Dual (2-channel)", 0x14),
        ("Single (1-channel)", 0x0C),
        ("Disabled (Experimental)", 0x04),
    ]


@pytest.mark.parametrize(
    "profile, raw, label",
    [
        ("ID41", 0x1C, "Dual (2-channel)"),
        ("ID41", 0x18, "Single (1-channel)"),
        ("ID41", 0x04, "Disabled"),
        ("ID42", 0x1C, "Dual (2-channel)"),
        ("ID59", 0x08, "Dual (2-channel)"),
        ("ID59", 0x18, "Single (1-channel)"),
        ("ID59", 0x04, "Disabled"),
        ("ID85", 0x14, "Dual (2-channel)"),
        ("ID85", 0x0C, "Single (1-channel)"),
        ("ID60", 0x14, "Dual (2-channel)"),
        ("ID60", 0x0C, "Single (1-channel)"),
        ("ID12", 0x14, "Dual (2-channel)"),
        ("ID12", 0x0C, "Single (1-channel)"),
    ],
)
def test_oxygen_sensor_states_decode_exact_portal_values(profile, raw, label):
    cfg = ecu_config.read_config(_partial_with_byte6(raw), profile=profile)

    assert cfg["Oxygen Sensors"] == label


@pytest.mark.parametrize(
    "profile, label, masked_value",
    [
        ("ID41", "Dual (2-channel)", 0x1C),
        ("ID41", "Single (1-channel)", 0x18),
        ("ID41", "Disabled", 0x04),
        ("ID42", "Dual (2-channel)", 0x1C),
        ("ID59", "Dual (2-channel)", 0x08),
        ("ID59", "Single (1-channel)", 0x18),
        ("ID59", "Disabled", 0x04),
        ("ID85", "Dual (2-channel)", 0x14),
        ("ID85", "Single (1-channel)", 0x0C),
        ("ID60", "Dual (2-channel)", 0x14),
        ("ID60", "Single (1-channel)", 0x0C),
        ("ID12", "Dual (2-channel)", 0x14),
        ("ID12", "Single (1-channel)", 0x0C),
    ],
)
def test_oxygen_sensor_apply_preserves_unrelated_byte6_bits(profile, label, masked_value):
    original = _partial_with_byte6(0xE3)
    changed, _ = ecu_config.apply_config(
        original, {"Oxygen Sensors": label}, profile=profile)

    assert changed[6] & 0x1C == masked_value
    assert changed[6] & ~0x1C == original[6] & ~0x1C


def test_id12_partial_experimental_disable_is_not_available_or_written():
    original = _partial_with_byte6(0x14)
    changed, log = ecu_config.apply_config(
        original, {"Oxygen Sensors": "Disabled (Experimental)"}, profile="ID12")

    assert changed == original
    assert log == ["No changes."]
    assert "Disabled (Experimental)" not in [
        label for label, _
        in _oxygen_feature().options_for("ID12", ecu_config.TUNE_SIZE)
    ]


def test_unknown_profile_omits_and_cannot_edit_oxygen_sensors():
    original = _partial_with_byte6(0x1C)

    assert "Oxygen Sensors" not in ecu_config.read_config(original, profile=None)
    changed, _ = ecu_config.apply_config(
        original, {"Oxygen Sensors": "Single (1-channel)"}, profile=None)
    assert changed == original


def test_partial_profile_auto_detection_uses_calibration_identity():
    id41 = _partial_with_byte6(0x18, b"41000000")
    id59 = _partial_with_byte6(0x08, b"59000000")
    id12 = _partial_with_byte6(0x0C, b"12000000")

    assert ecu_config.detect_control_bit_profile(id41) == "ID41"
    assert ecu_config.read_config(id41)["Oxygen Sensors"] == "Single (1-channel)"
    assert ecu_config.detect_control_bit_profile(id59) == "ID59"
    assert ecu_config.read_config(id59)["Oxygen Sensors"] == "Dual (2-channel)"
    assert ecu_config.detect_control_bit_profile(id12) == "ID12"
    assert ecu_config.read_config(id12)["Oxygen Sensors"] == "Single (1-channel)"


def test_full_rom_profile_uses_cal_id_even_when_program_identity_differs():
    full = bytearray(b"\xFF" * ecu_config.FULL_ROM_SIZE)
    full[0x6025:0x602C] = b"1406464"       # MS41.2 program identity
    full[0x1400E:0x14016] = b"59000000"    # deliberately conflicting ID59 CAL
    full[ecu_config.CAL_BASE_FULL + 6] = 0x08

    assert ecu_config.detect_control_bit_profile(full) == "ID59"
    assert ecu_config.read_config(full)["Oxygen Sensors"] == "Dual (2-channel)"


def test_oxygen_sensor_uses_calibration_byte6_in_a_full_rom():
    full = bytearray(b"\xFF" * ecu_config.FULL_ROM_SIZE)
    byte6_addr = ecu_config.CAL_BASE_FULL + 6
    full[byte6_addr] = 0x14

    changed, _ = ecu_config.apply_config(
        full, {"Oxygen Sensors": "Single (1-channel)"}, profile="ID12")

    assert changed[byte6_addr] == 0x0C
    assert full[byte6_addr] == 0x14


def test_id12_full_rom_experimental_disable_updates_both_definition_tables():
    full = _full_with_identity(b"12011110", b"1406464")
    full[0x2DF95] = 0x0C

    changed, log = ecu_config.apply_config(full, {
        "Oxygen Sensors": "Disabled (Experimental)",
        "O2 Feedback Program Gate (Experimental)": "Feedback Disabled",
    })

    assert changed[ecu_config.CAL_BASE_FULL + 6] == 0x04
    assert changed[0x2DF95] == 0x11
    assert any("Byte 6" in line for line in log)
    assert any("0x2DF95" in line for line in log)


def test_id12_full_rom_does_not_label_or_apply_disable_without_program_gate():
    full = _full_with_identity(b"12011110", b"1406464", byte6=0x04)
    full[0x2DF95] = 0x0C

    cfg = ecu_config.read_config(full)
    assert cfg["Oxygen Sensors"] == "Custom (0x04)"
    assert not ecu_config.experimental_o2_program_gate_present(full)

    changed, _ = ecu_config.apply_config(full, {
        "Oxygen Sensors": "Disabled (Experimental)",
    })
    assert changed == full


def test_id12_existing_program_gate_unlocks_calibration_disable():
    full = _full_with_identity(b"12011110", b"1406464", byte6=0x14)
    full[0x2DF95] = 0x11

    assert ecu_config.experimental_o2_program_gate_present(full)
    changed, _ = ecu_config.apply_config(full, {
        "Oxygen Sensors": "Disabled (Experimental)",
    })
    assert changed[ecu_config.CAL_BASE_FULL + 6] == 0x04


def test_incompatible_program_cannot_unlock_experimental_calibration_disable():
    hybrid = _full_with_identity(b"12011110", b"1437806", byte6=0x14)
    hybrid[0x2DF95] = 0x0C

    changed, _ = ecu_config.apply_config(hybrid, {
        "Oxygen Sensors": "Disabled (Experimental)",
        "O2 Feedback Program Gate (Experimental)": "Feedback Disabled",
    })
    assert changed[ecu_config.CAL_BASE_FULL + 6] == 0x14
    assert changed[0x2DF95] == 0x0C


def test_id60_full_rom_program_gate_uses_its_own_definition_address():
    full = _full_with_identity(b"60011110", b"1437806")
    full[0x2E311] = 0x0C

    cfg = ecu_config.read_config(full)
    assert cfg["O2 Feedback Program Gate (Experimental)"] == "Feedback Enabled"

    changed, _ = ecu_config.apply_config(full, {
        "O2 Feedback Program Gate (Experimental)": "Feedback Disabled",
    })
    assert changed[0x2E311] == 0x11
    assert changed[0x2DF95] == full[0x2DF95]


def test_experimental_program_gate_is_omitted_from_partial_and_unknown_program_pair():
    partial = _partial_with_byte6(0x04, b"12011110")
    assert "O2 Feedback Program Gate (Experimental)" not in ecu_config.read_config(partial)

    hybrid = _full_with_identity(b"12011110", b"1437806")
    hybrid[0x2DF95] = 0x0C
    assert "O2 Feedback Program Gate (Experimental)" not in ecu_config.read_config(hybrid)
    changed, _ = ecu_config.apply_config(hybrid, {
        "O2 Feedback Program Gate (Experimental)": "Feedback Disabled",
    })
    assert changed[0x2DF95] == 0x0C


def test_experimental_program_feature_matches_definition_states_and_addresses():
    feature = _o2_program_feature()
    assert feature.options_for("ID12", ecu_config.FULL_ROM_SIZE) == [
        ("Feedback Enabled", 0x0C),
        ("Feedback Disabled", 0x11),
    ]
    assert feature.profile_abs_addrs == {"ID12": 0x2DF95, "ID60": 0x2E311}
    assert feature.full_file_only is True


def test_ac_type_feature_exposes_requested_combobox_choices():
    feature = next(f for f in ecu_config.FEATURES if f.name == "A/C Type")

    assert feature.byte == 4
    assert feature.mask == 0x16
    assert feature.options == [("E39", 0x10), ("E36", 0x06)]


def test_transmission_uses_only_low_six_bits_and_preserves_other_bits():
    feature = next(f for f in ecu_config.FEATURES if f.name == "Transmission")

    assert feature.current(0xAC) == "AT/MT (auto)"  # MS41.0
    assert feature.current(0xEC) == "AT/MT (auto)"  # MS41.1
    assert feature.current(0xC0) == "MT Only"       # MS41.2/3
    for original in range(0x100):
        for label, value in feature.options:
            changed = feature.apply(original, label)
            assert changed & 0xC0 == original & 0xC0
            assert changed & 0x3F == value


def test_ac_type_decodes_known_working_e39_and_e36_values():
    assert ecu_config.read_config(_partial_with_byte4(0x30))["A/C Type"] == "E39"
    assert ecu_config.read_config(_partial_with_byte4(0x26))["A/C Type"] == "E36"


def test_ac_type_switches_known_values_without_touching_vanos():
    e39 = _partial_with_byte4(0x30)
    e36, log = ecu_config.apply_config(e39, {"A/C Type": "E36"})
    assert e36[4] == 0x26
    assert e36[4] & 0x20 == e39[4] & 0x20
    assert log == ["A/C Type: Byte 4 0x30 → 0x26  (E36)"]

    restored, log = ecu_config.apply_config(e36, {"A/C Type": "E39"})
    assert restored[4] == 0x30
    assert restored[4] & 0x20 == e36[4] & 0x20
    assert log == ["A/C Type: Byte 4 0x26 → 0x30  (E39)"]


def test_ac_type_preserves_vanos_when_vanos_is_disabled():
    e39_vanos_disabled = _partial_with_byte4(0x10)
    e36_vanos_disabled, _ = ecu_config.apply_config(
        e39_vanos_disabled, {"A/C Type": "E36"})

    assert e36_vanos_disabled[4] == 0x06
    assert ecu_config.read_config(e36_vanos_disabled)["VANOS"] == "Disabled"
    assert ecu_config.read_config(e36_vanos_disabled)["A/C Type"] == "E36"


def test_vanos_changes_do_not_touch_ac_type():
    e36 = _partial_with_byte4(0x26)
    e36_vanos_disabled, _ = ecu_config.apply_config(e36, {"VANOS": "Disabled"})
    assert e36_vanos_disabled[4] == 0x06
    assert ecu_config.read_config(e36_vanos_disabled)["A/C Type"] == "E36"

    e39_vanos_disabled = _partial_with_byte4(0x10)
    e39_vanos_enabled, _ = ecu_config.apply_config(
        e39_vanos_disabled, {"VANOS": "Enabled"})
    assert e39_vanos_enabled[4] == 0x30
    assert ecu_config.read_config(e39_vanos_enabled)["A/C Type"] == "E39"


def test_ac_type_preserves_every_unrelated_byte4_bit():
    original = _partial_with_byte4(0xF9)
    changed, _ = ecu_config.apply_config(original, {"A/C Type": "E36"})

    ac_mask = 0x16
    assert changed[4] & ~ac_mask == original[4] & ~ac_mask
    assert changed[4] & ac_mask == 0x06


def test_ac_type_uses_calibration_byte4_in_a_full_rom():
    full = bytearray(b"\xFF" * ecu_config.FULL_ROM_SIZE)
    byte4_addr = ecu_config.CAL_BASE_FULL + 4
    full[byte4_addr] = 0x30

    changed, _ = ecu_config.apply_config(full, {"A/C Type": "E36"})

    assert changed[byte4_addr] == 0x26
    assert full[byte4_addr] == 0x30
