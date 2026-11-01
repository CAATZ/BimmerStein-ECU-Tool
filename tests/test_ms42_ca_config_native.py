"""Original CA configuration consumers with explicit operating-state inputs."""

import os
from pathlib import Path

import pytest

import ms42_ms43_config as config
from ms42_ms43_image import extract_calibration
from tests.ms42_ms43_revision_sources import composed_image, full_image


@pytest.fixture
def target(monkeypatch):
    root = Path(os.environ.get("MS41EMU_ROOT", Path.home() / "ECU Emulator"))
    monkeypatch.syspath_prepend(str(root))
    from ms41emu.ms42 import MS42
    return MS42(composed_image("0110CA", "0110C6").image, experimental=True)


def call(emu, entry):
    context = emu.reg.cp, tuple(emu.reg.dpp), emu.reg.sp, emu.reg.get_word(0)
    result = emu.call(entry, max_steps=20000)
    assert result.exit_reason == "stop_at", result
    assert (emu.reg.cp, tuple(emu.reg.dpp), emu.reg.sp, emu.reg.get_word(0)) == context


@pytest.mark.parametrize("entry,size", [(0x19B78, 164), (0x1A70A, 88),
    (0x1B4EE, 230), (0x11452, 44), (0x15106, 8), (0x2490E, 478)])
def test_ca_unchanged_admission_windows_and_fault_helpers(entry, size):
    old = full_image("0110C6")
    new = composed_image("0110CA", "0110C6").image
    assert old[entry:entry + size] == new[entry:entry + size]


@pytest.mark.parametrize("value", (0, 1, 255))
def test_cruise_main_switch_selects_both_original_owners(target, value):
    target.mem.image[0x4828B] = value
    target.write(0xFD64, 0xA550)
    result = target.run_from(0x11452, stop_at=(0x1145C, 0x1147E), max_steps=5)
    assert result.exit_reason == "stop_at", result
    assert target.read(0xFD64) == 0xA550 | (8 if value else 0)
    result = target.run_from(0x15106, stop_at=(0x1510E, 0x15216), max_steps=3)
    assert result.exit_reason == "stop_at", result
    assert target.cpu.pc == (0x1510E if value else 0x15216)


@pytest.mark.parametrize("value", (0, 1, 255))
def test_secondary_air_valve_diagnosis_keeps_or_clears_only_its_bits(target, value):
    target.mem.image[0x48293] = value
    for address in (0xCFEC, 0xD000):
        target.write(address, 0xA55A)
    result = target.run_from(0x3B986, stop_at=0x3B998, max_steps=8)
    assert result.exit_reason == "stop_at", result
    assert [target.read(address) for address in (0xCFEC, 0xD000)] == [
        0xA55A if value else 0x255A] * 2


@pytest.mark.parametrize("value", (0, 1, 255))
@pytest.mark.parametrize("active_bank", (0, 1, 2))
def test_secondary_air_lambda_selector_uses_the_actual_bank_context(target, value, active_bank):
    target.mem.image[0x48292] = value
    target.write_byte(0xE526, active_bank)
    result = target.run_from(0x3D4A8, stop_at=0x3D4C8, max_steps=10)
    assert result.exit_reason == "stop_at", result
    assert target.read(0xFB3E) == (0xE33A if not value or active_bank == 1 else 0xE42C)


@pytest.mark.parametrize("value", (0, 1, 255))
def test_purge_feedback_selects_and_reads_the_actual_lambda_bank(target, value):
    target.mem.image[0x48291] = value
    result = target.run_from(0x168E6, stop_at=0x168FE, max_steps=8)
    assert result.exit_reason == "stop_at", result
    banks = (0xE33A, 0xE42C)
    assert target.read(0xE232) == banks[bool(value)]
    statuses = ((0x1234, 0x5678), (0xABCD, 0xEF01))
    for bank, words in zip(banks, statuses):
        target.write(bank + 2, words[0])
        target.write(bank + 4, words[1])
    result = target.run_from(0x16B96, stop_at=0x16BAA, max_steps=8)
    assert result.exit_reason == "stop_at", result
    assert (target.read(0xFD36), target.read(0xFD38)) == statuses[bool(value)]
    for bank, words in zip(banks, statuses):
        assert (target.read(bank + 2), target.read(bank + 4)) == words


@pytest.mark.parametrize("disabled", (False, True))
def test_lambda_threshold_covers_every_speed_byte(target, disabled):
    target.mem.image[0x482A2] = 255 if disabled else 10
    for speed in range(256):
        target.write_byte(0xC42E, speed)
        result = target.run_from(0x312E0, stop_at=(0x312EC, 0x313A0), max_steps=5)
        assert result.exit_reason == "stop_at", result
        assert target.cpu.pc == (0x312EC if not disabled and speed > 10 else 0x313A0)


@pytest.mark.parametrize("bank", (0, 1))
@pytest.mark.parametrize("correction", (0x7000, 0x8000, 0x9000))
def test_lambda_feedback_settles_then_reenables_without_erasing_adaptations(target, bank, correction):
    base = (0xE33A, 0xE42C)[bank]
    target.write(0xFD4C, bank << 10)
    target.write(0xFD36, 0x209)
    target.write(0xFA22, 3200)
    target.write_byte(0xC42E, 100)
    target.write(base + 6, correction)
    target.write(base + 10, 0x144)
    target.write(base + 0x16, 0x8100)
    target.write(0xFD0E, 0x200)
    target.write_byte(0xC424, 255)
    target.write(0xFD38, 1)
    for threshold in (10, 255, 10):
        target.mem.image[0x482A2] = threshold
        before = target.read(base + 6)
        target.reg.set_word(12, base)
        call(target, 0x30F8C)
        assert bool(target.read(0xFD36) & 1) == (threshold == 10)
        assert target.read(base + 6) == (before if threshold == 10 else
            before + max(-0x144, min(0x144, 0x8000 - before)))
        assert target.read(base + 0x16) == 0x8100


@pytest.mark.parametrize("disabled", (False, True))
def test_purge_flow_threshold_covers_coolant_and_retains_explicit_test(target, disabled):
    assert target.mem.image[0x483C1] == 184
    threshold = 255 if disabled else 184
    target.mem.image[0x483C1] = threshold
    target.write(0xFD3C, 4)
    target.write(0xFD62, 0x80)
    for coolant in range(256):
        target.write_byte(0xC424, coolant)
        call(target, 0x19B78)
        assert bool(target.read(0xFD54) & 0x800) == (coolant > threshold)
    target.write_byte(0xC1DB, 1)
    call(target, 0x19B78)
    assert target.read(0xFD54) & 0x800


@pytest.mark.parametrize("fault", (0, 1, 2))
def test_purge_electrical_limit_disables_new_faults_and_retains_existing(target, fault):
    target.mem.image[0x481C8] = 2
    def update():
        target.write(0xFDAC + 2 * fault, 0x40)
        target.write_byte(0xD34B, 0)
        result = target.run_from(0x1A70A, stop_at=0x1A762, max_steps=10000)
        assert result.exit_reason == "stop_at", result
    for limit in (0, 10, 0):
        target.mem.image[0x48210] = limit
        for _ in range(8):
            update()
        if limit:
            assert target.read_byte(0xC84E) & 0x20
            assert target.read_byte(0xC84F) == 10
    assert target.read_byte(0xC84E) & 0x20


@pytest.mark.parametrize("fault", (1, 2))
@pytest.mark.parametrize("increment", (0, 2))
def test_ldp_limit_fault_progression_and_retention(target, fault, increment):
    target.mem.image[0x481A9] = increment
    target.write(0xFD80, 0x2000)
    target.write(0xFDA8, 0x400 if fault == 1 else 0)
    target.write(0xFDB4 if fault == 1 else 0xFDB6, 0x400)
    for threshold in (0, 10, 0):
        target.mem.image[0x481EF] = threshold
        for _ in range(8):
            target.write_byte(0xD360, 0)
            result = target.run_from(0x1B4EE, stop_at=0x1B5D4, max_steps=2000)
            assert result.exit_reason == "stop_at", result
        if threshold:
            assert bool(target.read_byte(0xCC02) & 0x20) == bool(increment)
            assert target.read_byte(0xCC03) == (10 if increment else 0)
    assert bool(target.read_byte(0xCC02) & 0x20) == bool(increment)


@pytest.mark.parametrize("disabled", (False, True))
def test_normal_purge_threshold_covers_every_coolant_byte(target, disabled):
    call(target, 0x168B6)
    threshold = 255 if disabled else 151
    target.mem.image[0x483CE] = threshold
    visited = set()
    target.cpu.set_trace(lambda pc, op: visited.add(pc))
    for coolant in range(256):
        target.write(0xFD6C, 0x1000)
        target.write(0xFD26, 1)
        target.write(0xFD50, 8)
        target.write_byte(0xC424, coolant)
        target.write(0xE252, 4000)
        visited.clear()
        call(target, 0x16A04)
        assert (0x16A52 in visited) == (coolant > threshold)


@pytest.mark.parametrize("rpm", (0, 319, 320, 351, 480, 511, 8160, 8191, 8192, 65535))
def test_lambda_speed_producer_is_the_original_saturating_conversion(target, rpm):
    target.write(0xFA22, rpm)
    result = target.run_from(0x75722, stop_at=0x7573C, max_steps=10)
    assert result.exit_reason == "stop_at", result
    assert target.read_byte(0xC42E) == min(255, rpm >> 5)


@pytest.mark.parametrize("software", ("0110C6", "0110CA"))
@pytest.mark.parametrize("bank", (0, 1))
@pytest.mark.parametrize("condition", ("sensor_not_ready", "sensor_fault"))
def test_lambda_feedback_retains_original_sensor_prerequisites(target, software, bank, condition):
    if software == "0110C6":
        target.mem.image[:] = full_image(software)
    base = (0xE33A, 0xE42C if software == "0110CA" else 0xE424)[bank]
    target.write(0xFD4C, bank << 10)
    target.write(0xFD36, 0 if condition == "sensor_not_ready" else 0x209)
    target.write(0xFA22, 3200)
    target.write_byte(0xC42E, 100)
    target.write(base + 6, 0x9000)
    target.write(base + 10, 0x144)
    target.write(base + 0x16, 0x8100)
    target.write(0xFD0E, 0x200)
    target.write_byte(0xC424, 255)
    target.write(0xFD38, 1)
    if condition == "sensor_fault":
        target.write(0xFD84 if bank == 0 else 0xFD86, 0x400 if bank == 0 else 2)
    target.reg.set_word(12, base)
    call(target, 0x30F8C if software == "0110CA" else 0x30F56)
    assert not target.read(0xFD36) & 1
    # Sensor faults have their own original neutral reset, independent of the
    # calibration threshold that only settles normal feedback toward neutral.
    assert target.read(base + 0x16) == (0x8000 if condition == "sensor_fault" else 0x8100)


@pytest.mark.parametrize("software", ("0110C6", "0110C7", "0110CA"))
@pytest.mark.parametrize("value", (0, 1, 255))
@pytest.mark.parametrize("active_bank", (0, 1, 2))
@pytest.mark.parametrize("consumer_bank", (0, 1))
def test_secondary_air_bank_setting_reaches_the_actual_diagnostic_consumer(
        target, software, value, active_bank, consumer_bank):
    if software != "0110CA":
        target.mem.image[:] = (full_image(software) if software == "0110C6" else
                               composed_image(software, "0110C6").image)
    ca = software == "0110CA"
    banks = (0xE33A, 0xE42C if ca else 0xE424)
    target.mem.image[0x48292] = value
    target.write_byte(0xE526 if ca else 0xE516, active_bank)
    entry, end = (0x3D4A8, 0x3D4C8) if ca else (0x3D31A, 0x3D33A)
    result = target.run_from(entry, stop_at=end, max_steps=10)
    assert result.exit_reason == "stop_at", result
    selected = 0 if not value or active_bank == 1 else 1
    assert target.read(0xFB3E) == banks[selected]
    target.reg.set_word(9, banks[consumer_bank])
    target.write(0xFD6C, 0)
    target.write(0xFD36, 0)
    entry, admitted, skipped = ((0x3DC12, 0x3DC32, 0x3DE48) if ca else
                               (0x3DA84, 0x3DAA4, 0x3DCBA))
    result = target.run_from(entry, stop_at=(admitted, skipped), max_steps=10)
    assert result.exit_reason == "stop_at", result
    assert target.cpu.pc == (admitted if consumer_bank == selected else skipped)


@pytest.mark.parametrize("offset", (0x2A2, 0x28B, 0x292, 0x293, 0x291, 0x3CE, 0x210, 0x1EF, 0x3C1))
@pytest.mark.parametrize("partial", (False, True))
def test_ca_verified_choices_edit_only_their_native_calibration_byte(offset, partial):
    original = composed_image("0110CA", "0110C6").image
    if partial:
        original = extract_calibration(original)
    feature = next(item for item in config.features_for("MS42", "0110CA") if item.byte == offset)
    address = offset if partial else 0x48000 + offset
    for label, value in feature.options:
        edited = config.apply_config(original, {feature.name: label})
        expected = bytearray(original)
        expected[address] = value
        assert edited == expected
        assert config.read_config(edited)[feature.name] == label
        assert config.apply_config(edited, config.read_config(original)) == original
