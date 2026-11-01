"""Original configuration owners with supplied fault and torque-request state.

Curve experiments below are evidence only, not exposed configuration settings.
The existing C167 emulator executes all helpers; no scheduler or ECU is run.
"""

import pytest

import ms42_ms43_config as config
from ms42_ms43_image import extract_calibration
from tests.test_ms42_ms43_config_native import native as native
from tests.test_ms42_ms43_config_revisions import private_sources as private_sources
from tests.test_ms42_ms43_config_vanos_knock_native import byte, write


LDP_THRESHOLD = "Leak-detection pump electrical fault threshold"


@pytest.mark.parametrize("fault", (1, 2))
@pytest.mark.parametrize("increment", (0, 2))
def test_ldp_fault_threshold_disable_reference_restore_and_retained_fault(native, fault, increment):
    emu = native("MS42")
    # Increment remains a separate calibration. The supplied EU image uses zero.
    emu.mem.image[0x481A9] = increment
    original = bytes(emu.mem.image)
    emu.write(0xFD80, 0x2000)
    emu.write(0xFDA8, 0x400 if fault == 1 else 0)
    emu.write(0xFDB4 if fault == 1 else 0xFDB6, 0x400)

    def update():
        emu.write_byte(0xD360, 0)
        result = emu.run_from(0x1B4EE, stop_at=(0x1B5D4,), max_steps=2000)
        assert result.exit_reason == "stop_at", result

    emu.mem.image[:] = config.apply_config(original, {LDP_THRESHOLD: "Disabled"})
    for _ in range(8):
        update()
    assert emu.read_byte(0xCC02) == fault
    assert emu.read_byte(0xCC03) == 0
    assert not emu.read_byte(0xCC0C) & 0x20

    emu.mem.image[:] = config.apply_config(bytes(emu.mem.image), {LDP_THRESHOLD: "10-count threshold"})
    assert bytes(emu.mem.image) == original
    for _ in range(8):
        update()
    assert bool(emu.read_byte(0xCC02) & 0x20) == bool(increment)
    assert bool(emu.read_byte(0xCC0C) & 0x20) == bool(increment)
    assert emu.read_byte(0xCC03) == (10 if increment else 0)

    emu.mem.image[:] = config.apply_config(bytes(emu.mem.image), {LDP_THRESHOLD: "Disabled"})
    for _ in range(8):
        update()
    assert bool(emu.read_byte(0xCC02) & 0x20) == bool(increment)
    assert bool(emu.read_byte(0xCC0C) & 0x20) == bool(increment)
    assert emu.mem.image[0x481A9] == increment
    assert emu.read(0xFDA8) == (0x400 if fault == 1 else 0)
    # Service the existing-reason branches after the physical fault disappears.
    emu.write(0xFDB4 if fault == 1 else 0xFDB6, 0)
    emu.write_byte(0xC62E, 0)
    for _ in range(12):
        update()
    assert emu.read_byte(0xCC03) == 0
    assert bool(emu.read_byte(0xCC02) & 0x20) == bool(increment)
    assert bool(emu.read_byte(0xCC0C) & 0x20) == bool(increment)


@pytest.mark.parametrize("partial", (False, True))
def test_ldp_configuration_edits_only_the_fault_maximum(native, partial):
    original = bytes(native("MS42").mem.image)
    if partial:
        original = extract_calibration(original)
    offset = (0 if partial else 0x48000) + 0x1EF
    edited = config.apply_config(original, {LDP_THRESHOLD: "Disabled"})
    assert [i for i, pair in enumerate(zip(original, edited)) if pair[0] != pair[1]] == [offset]
    assert config.read_config(edited)[LDP_THRESHOLD] == "Disabled"
    assert config.apply_config(edited, {LDP_THRESHOLD: "10-count threshold"}) == original


def test_c7_supplies_the_complete_ldp_fault_owner_helper_and_calibrations(private_sources):
    memory = private_sources["c7_memory"]
    for start, end in ((0x1A50A, 0x1B70C), (0x2490E, 0x24AEC)):
        assert all(address in memory for address in range(start, end))
        assert bytes(memory[address] for address in range(start, end)) == private_sources["c6"][start:end]
    calibration = private_sources["c7_cal_memory"]
    assert [calibration[address] for address in (0x481A9, 0x481EF)] == [0, 10]
    assert LDP_THRESHOLD in {feature.name for feature in config.features_for("MS42", "0110C7")}


def _asc_angle(native, family, neutral, independent=False):
    emu = native(family)
    curve = 0x49B26 if family == "MS42" else 0x7A2AC
    original_curve = bytes(emu.mem.image[curve:curve + 8])
    if neutral:
        emu.mem.image[curve:curve + 8] = b"\0" * 8
    if family == "MS42":
        words = ((0xFB30, 256), (0xFB34, 256), (0xFB28, 65535), (0xFB2C, 65535),
                 (0xFB2E, 8000 if independent else 65535), (0xFD3C, 8), (0xFD5A, 512), (0xFD5E, 0))
        values = ((0xC60E, 50), (0xC453, 160), (0xC44B, 160), (0xC42E, 64), (0xFA45, 64))
        entry, stop, ratio, angle = 0x71EE8, 0x7213A, 0xFB32, 0xC44D
    else:
        words = ((0xE050, 256), (0xE054, 256), (0xE02A, 65535), (0xE030, 65535),
                 (0xE02E, 8000 if independent else 65535), (0xFD2C, 1024), (0xFD16, 0))
        values = ((0x40119, 50), (0x40023, 1), (0x40025, 160), (0x401C1, 160),
                  (0x4007F, 64), (0xE33B, 64))
        entry, stop, ratio, angle = 0x909F4, 0x90C7A, 0xE052, 0x40020
    for address, value in words:
        emu.write(address, value)
    for address, value in values:
        write(emu, address, value)
    result = emu.run_from(entry, stop_at=(stop,), max_steps=2000)
    assert result.exit_reason == "stop_at", result
    return emu, (emu.read(ratio), byte(emu, angle)), curve, original_curve


@pytest.mark.parametrize("family,stock_ratio,stock_angle", (("MS42", 700, 94), ("MS43", 313, 62)))
@pytest.mark.parametrize("independent", (False, True))
def test_asc_correction_curve_reaches_angle_but_preserves_separate_intervention(
        native, family, stock_ratio, stock_angle, independent):
    _, stock, _, _ = _asc_angle(native, family, False, independent)
    emu, neutral, curve, original_curve = _asc_angle(native, family, True, independent)
    assert stock == (stock_ratio, 48 if independent else stock_angle)
    assert neutral == (1023, 48 if independent else 160)
    emu.mem.image[curve:curve + 8] = original_curve
    assert bytes(emu.mem.image) == bytes(native(family).mem.image)


def test_ms43_asc_correction_curve_also_changes_cylinder_cut_selection(native):
    outputs = []
    for neutral in (False, True):
        emu, _, _, _ = _asc_angle(native, "MS43", neutral)
        # Supply the separate normalization and admission owners' outputs.
        emu.write(0xE04E, 0xFFC0)
        for address, value in ((0x40118, 1), (0x40433, 255)):
            write(emu, address, value)
        write(emu, 0x404DC, 65535, 2)
        write(emu, 0x404DE, 65535, 2)
        result = emu.run_from(0x90CD8, stop_at=(0x90DAE,), max_steps=2000)
        assert result.exit_reason == "stop_at", result
        outputs.append((emu.read(0xE058), emu.read(0xE0B4), byte(emu, 0x40024)))
    assert outputs == [(312, 20027, 10), (1022, 65535, 0)]


@pytest.mark.parametrize("neutral,angle", ((False, 65), (True, 77)))
def test_ms42_second_asr_ratio_keeps_retarding_with_a_neutral_correction_curve(native, neutral, angle):
    emu, _, _, _ = _asc_angle(native, "MS42", neutral)
    # FB06/FB0C are torque requests derived from ASR1 C60E/C60F.
    emu.write(0xFB06, 128)
    emu.write(0xFB0C, 256)
    result = emu.run_from(0x71B8E, stop_at=(0x71BA8,), max_steps=100)
    assert result.exit_reason == "stop_at", result
    assert emu.read(0xFB2C) == 32768
    result = emu.run_from(0x71EE8, stop_at=(0x7213A,), max_steps=2000)
    assert result.exit_reason == "stop_at", result
    assert emu.read_byte(0xC44D) == angle < emu.read_byte(0xC44B)


@pytest.mark.parametrize("maximum", (190, 255))
def test_ldp_ff_minimum_depends_on_the_separate_maximum_not_a_boolean_switch(native, maximum):
    emu = native("MS42")
    emu.mem.image[0x483C6] = 255
    emu.mem.image[0x483BA] = maximum
    for temperature in range(256):
        emu.write_byte(0xC424, temperature)
        emu.write_byte(0xC1FD, 0)
        result = emu.run_from(0x17F6C, stop_at=(0x17F84,), max_steps=10)
        assert result.exit_reason == "stop_at", result
        assert emu.read_byte(0xC1FD) == (0 if temperature == maximum == 255 else 0xE0)
