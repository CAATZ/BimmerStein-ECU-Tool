"""Original C167 branch checks for configuration values that definitions mislabel.

Uses the canonical emulator and exact private stock images when available.
Entry/runtime flags are supplied. These bounded branches do not execute the
engine scheduler, perform a diagnostic write, or prove physical ECU behavior.
"""

import os
from pathlib import Path

import pytest

import ms42_ms43_config as config


@pytest.fixture
def native(monkeypatch):
    root = Path(os.environ.get("MS41EMU_ROOT", Path.home() / "ECU Emulator"))
    if not (root / "ms41emu").is_dir():
        pytest.skip("canonical C167 emulator unavailable; set MS41EMU_ROOT")
    monkeypatch.syspath_prepend(str(root))
    from ms41emu.ms42 import MS42
    from ms41emu.ms43 import MS43

    refs = Path(os.environ.get("MS41_TEST_DATA_ROOT", Path(__file__).resolve().parents[2] / "_shared"))

    def load(family):
        filename = ("Siemens_MS42_0110C6_E46_M52TUB28_EU3_LHD.bin" if family == "MS42"
                    else "Siemens_MS43_MS430069_E46_M54B30_US.bin")
        path = refs / f"REF_{family}" / filename
        if not path.is_file():
            pytest.skip(f"private {family} reference unavailable")
        return (MS42 if family == "MS42" else MS43).load(path)

    return load


@pytest.mark.parametrize("value", (0, 1))
@pytest.mark.parametrize("diagnostic_override", (False, True))
def test_native_ms42_low_speed_exhaust_flap_actuation(native, value, diagnostic_override):
    emu = native("MS42")
    emu.mem.image[:] = config.apply_config(bytes(emu.mem.image), {
        "Exhaust flap actuation: below minimum speed": "On" if value else "Off",
    })
    emu.mem.image[0x483FC] = 10  # Minimum speed; leave executable bytes intact.
    emu.write_byte(0xC442, 0)
    emu.write(0xFD78, 4 if diagnostic_override else 0)
    mask = 1 << 14
    original = 0xA55A & ~mask if value else 0xA55A | mask
    emu.write(0xFDA8, original)
    emu.write(0xFDA6, original)

    result = emu.run_from(0x1D68A, stop_at=(0x1D6A2, 0x1D6AC, 0x1D6DC), max_steps=20)

    assert result.exit_reason == "stop_at"
    requested = (original & ~mask) | (value << 14)
    assert emu.read(0xFDA8) == requested
    assert emu.read(0xFDA6) == (original if diagnostic_override else requested)


@pytest.mark.parametrize("transmission,entry,threshold", [
    ("automatic", 0xA9894, 0x70DE3), ("manual", 0xA998E, 0x70DE4),
])
@pytest.mark.parametrize("heating", (False, True))
@pytest.mark.parametrize("value", (0, 1))
@pytest.mark.parametrize("diagnostic_override", (False, True))
def test_native_ms43_low_speed_exhaust_flap_actuation(
        native, transmission, entry, threshold, heating, value, diagnostic_override):
    emu = native("MS43")
    # Opposing choices prove the active transmission/heating branch owns the request.
    choices = {
        "Exhaust flap actuation: below minimum speed (automatic)": "Off" if value else "On",
        "Exhaust flap actuation: below minimum speed (manual)": "Off" if value else "On",
        "Exhaust flap actuation: catalyst-heating state 2": "Off" if value else "On",
    }
    selected = ("Exhaust flap actuation: catalyst-heating state 2" if heating else
                f"Exhaust flap actuation: below minimum speed ({transmission})")
    choices[selected] = "On" if value else "Off"
    emu.mem.image[:] = config.apply_config(bytes(emu.mem.image), choices)
    emu.mem.image[threshold] = 10
    emu.write_byte(0x847F, 0)
    emu.write(0xFD38, 0x80 if heating else 0)
    emu.write(0xFD8C, 0x80 if diagnostic_override else 0)
    mask = 1 << 6
    original = 0xA55A & ~mask if value else 0xA55A | mask
    emu.write(0xFDCE, original)
    emu.write(0xFDCC, original)

    result = emu.run_from(entry, stop_at=(
        0xA98B4, 0xA98C2, 0xA98D6, 0xA98E4,
        0xA99AA, 0xA99B4, 0xA99C4, 0xA99CE, 0xA99FE,
    ), max_steps=25)

    assert result.exit_reason == "stop_at"
    requested = (original & ~mask) | (value << 6)
    assert emu.read(0xFDCE) == requested
    assert emu.read(0xFDCC) == (original if diagnostic_override else requested)


@pytest.mark.parametrize("family,entry,threshold,speed,normal_path", [
    ("MS42", 0x1D68A, 0x483FC, 0xC442, 0x1D6AE),
    ("MS43", 0xA9894, 0x70DE3, 0x847F, 0xA98E6),
    ("MS43", 0xA998E, 0x70DE4, 0x847F, 0xA99D0),
])
def test_exhaust_flap_low_speed_settings_leave_normal_speed_path(native, family, entry,
                                                               threshold, speed, normal_path):
    emu = native(family)
    emu.mem.image[threshold] = 10
    emu.write_byte(speed, 10)
    result = emu.run_from(entry, stop_at=(normal_path,), max_steps=5)
    assert result.exit_reason == "stop_at" and emu.cpu.pc == normal_path


@pytest.mark.parametrize("minimum,drive,offset", [
    (False, False, 0x482C8), (False, True, 0x482C9),
    (True, False, 0x482CA), (True, True, 0x482CB),
])
def test_ms42_fuel_cut_hysteresis_uses_numeric_thresholds(native, minimum, drive, offset):
    emu = native("MS42")
    emu.mem.image[0x482C8:0x482CC] = bytes((11, 22, 33, 44))
    emu.write(0xFD06, 0x400 if minimum else 0)
    emu.write(0xFD42, 0x100 if drive else 0)
    emu.write_byte(0xC434, 99)
    result = emu.run_from(0x2374E, stop_at=(0x23784,), max_steps=15)
    assert result.exit_reason == "stop_at"
    assert emu.read_byte(0xC434) == emu.mem.image[offset]


def test_ms42_fuel_cut_hysteresis_holds_selected_value(native):
    emu = native("MS42")
    emu.write(0xFD06, 0x200)
    emu.write_byte(0xC434, 99)
    result = emu.run_from(0x2374E, stop_at=(0x23784,), max_steps=2)
    assert result.exit_reason == "stop_at"
    assert emu.read_byte(0xC434) == 99


@pytest.mark.parametrize("command,target", [(0x41, 0x1CC0C), (0x43, 0x1CD70), (0x45, 0x1D236)])
def test_ms42_adjustment_dispatch_has_no_ms43_bulk_write_command(native, command, target):
    emu = native("MS42")
    original = bytes(emu.mem.image)
    emu.write_byte(0xF89E, command)
    result = emu.run_from(0x1C57C, stop_at=(0x1CC0C, 0x1CD70, 0x1D236), max_steps=100)
    assert result.exit_reason == "stop_at" and emu.cpu.pc == target
    assert bytes(emu.mem.image) == original


@pytest.mark.parametrize("value,ready,expected", [
    (0, True, 0xA52C6), (1, True, 0xA56A2), (0, False, 0xA56A2),
])
def test_native_ms43_bulk_adaptation_write_admission(native, value, ready, expected):
    emu = native("MS43")
    emu.mem.image[0x70DEF] = value  # Calibration input; executable bytes unchanged.
    emu.write(0xFD78, 0x40 if ready else 0)
    emu.write(0xFD2C, 0x80)
    result = emu.run_from(0xA52AE, stop_at=(0xA52C6, 0xA56A2), max_steps=20)
    assert result.exit_reason == "stop_at"
    assert emu.cpu.pc == expected


@pytest.mark.parametrize("value,expected,intervention", [
    (0, 0x92B68, False), (1, 0x92B4A, True), (2, 0x92B18, True),
])
def test_native_ms43_pedal_brake_modes(native, value, expected, intervention):
    emu = native("MS43")
    assert emu.mem.image[0x70E05] == 2  # Factory reference uses the third mode.
    emu.mem.image[0x70E05] = value
    emu.write_byte(0xFB46, 255)  # Supplied completed debounce interval.
    result = emu.run_from(0x92B00, stop_at=(0x92B68, 0x92B4A, 0x92B18), max_steps=20)
    assert result.exit_reason == "stop_at"
    assert emu.cpu.pc == expected
    assert bool(emu.read(0xFD0E) & 0x200) is intervention


@pytest.mark.parametrize("value", [3, 4, 255])
def test_native_ms42_lambda_unknown_values_take_fixed_dual_bank_default(native, value):
    emu = native("MS42")
    emu.mem.image[0x48287] = value
    visited = []
    emu.cpu.set_trace(lambda pc, op: visited.append(pc))
    result = emu.run_from(0x133FA, stop_at=0x13440, max_steps=20)
    assert result.exit_reason == "stop_at"
    assert emu.read_byte(0xE516) == 2
    assert emu.read(0xFD42) & 8 and emu.read(0xFD04) & 0x1000
    assert 0x13414 not in visited and 0x13424 not in visited  # Variant-learning calls.


@pytest.mark.parametrize("value,program_bad,calibration_bad,fault", [
    (1, False, False, False), (1, False, True, True), (0, False, True, True),
    (0xA5, False, True, False), (0xA5, True, False, True), (0xA5, True, True, True),
])
def test_native_ms43_monitor_switch_only_bypasses_calibration_mismatch(
        native, value, program_bad, calibration_bad, fault):
    emu = native("MS43")
    assert emu.mem.image[0x70E07] == 1
    program_sum = int.from_bytes(emu.mem.image[0x6FDAE:0x6FDB2], "little") ^ int(program_bad)
    calibration_sum = int.from_bytes(emu.mem.image[0x72FFC:0x73000], "little") ^ int(calibration_bad)
    emu.mem.image[0x70E07] = value
    # Supply completed accumulators; execute the unchanged comparison/fault branch.
    emu.write_byte(0xE000, 2)
    emu.write_byte(0xE002, 2)
    for address, total in ((0xE672, program_sum), (0xE680, calibration_sum)):
        emu.write(address, total & 0xFFFF)
        emu.write(address + 2, total >> 16)
    result = emu.run_from(0xDFCB8, stop_at=(0xDFD0A, 0xDFD0E), max_steps=40)
    assert result.exit_reason == "stop_at"
    assert emu.cpu.pc == (0xDFD0A if fault else 0xDFD0E)
    assert bool(emu.read(0xFD7E) & 0x100) is fault
    if fault:
        assert emu.reg.get_word(12) == 8


@pytest.mark.parametrize("boot", [False, True])
@pytest.mark.parametrize("metadata,marker,expected", [
    (0, 0, 8), (0, 0xA8, 8), (0xFFFF, 0, 0x18), (0xFFFF, 0xA8, 9),
])
def test_native_ms42_crc_bypass_requires_both_metadata_fields(native, boot, metadata, marker, expected):
    emu = native("MS42")
    assert emu.mem.image[0x4FEFE:0x4FF00] == bytes(2) and emu.mem.image[0x5002C] == 0
    store = 0x4FEE0
    count = int.from_bytes(emu.mem.image[store + 2:store + 4], "little")
    end = int.from_bytes(emu.mem.image[store + 8 + (count - 1) * 8:store + 12 + (count - 1) * 8], "little")
    crc = int.from_bytes(emu.mem.image[store:store + 2], "little")
    emu.mem.image[0x4FEFE:0x4FF00] = metadata.to_bytes(2, "little")
    emu.mem.image[0x5002C] = marker
    emu.write(0xF756, crc ^ 1)  # Supply a completed, mismatched calibration CRC.
    if boot:
        emu.write(0xF72E, 2)
        emu.write_byte(0xF765, count - 1)
        emu.write(0xF760, (end + 1) & 0xFFFF)
        emu.write(0xF762, (end + 1) >> 16)
        result = emu.call(0xAC2, max_steps=500)
    else:
        emu.write(0xF72E, 8)
        result = emu.run_from(0x1C3C8, stop_at=0x1C410, max_steps=40)
    assert result.exit_reason == "stop_at"
    assert emu.read(0xF72E) == expected
