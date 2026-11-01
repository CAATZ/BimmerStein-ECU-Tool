"""Native boundaries that prevent treating calibration limits as system switches.

Only scalar bytes in in-memory copies are changed. Runtime inputs select bounded
original paths; these tests neither patch executable bytes nor access an ECU.
"""

import pytest

from tests.test_ms42_ms43_config_native import native as native


def write(emu, address, value, size=1):
    writer = getattr(emu.mem, "_physical_write", emu.mem.write_byte_direct)
    for index in range(size):
        writer(address + index, (value >> (8 * index)) & 255)


def byte(emu, address):
    return getattr(emu.mem, "_physical_read", emu.mem.read_byte_direct)(address)


VANOS = {
    ("MS42", "intake"): (0x48311, 0x4830B, 0xD08F, 0x2D2CC, 0x2D2E6),
    ("MS42", "exhaust"): (0x48310, 0x4830A, 0xD1FD, 0x2D3E2, 0x2D3FC),
    ("MS43", "intake"): (0x70B00, 0x70B09, 0x40215, 0xB3F78, 0xB3F90),
    ("MS43", "exhaust"): (0x70AFF, 0x70B08, 0x40213, 0xB40BE, 0xB40D6),
}


@pytest.mark.parametrize("family,channel", VANOS)
@pytest.mark.parametrize("raw_target", (0, 128, 255))
@pytest.mark.parametrize("zero_range", (False, True))
def test_vanos_travel_is_a_position_clamp_not_an_enable_switch(native, family, channel, raw_target, zero_range):
    emu = native(family)
    original = bytes(emu.mem.image)
    travel, initial, output, entry, stop = VANOS[family, channel]
    if zero_range:
        emu.mem.image[travel] = 0
    reference, limit = emu.mem.image[initial], emu.mem.image[travel]
    write(emu, output, raw_target)
    emu.reg.set_word(4, raw_target)
    result = emu.run_from(entry, stop_at=(stop,), max_steps=80)
    assert result.exit_reason == "stop_at", result
    assert byte(emu, output) == max(reference - limit, min(reference, raw_target))
    assert all(a == b or offset == travel for offset, (a, b) in enumerate(zip(original, emu.mem.image)))


@pytest.mark.parametrize("family", ("MS42", "MS43"))
def test_zero_vanos_travel_keeps_both_native_holding_pwm_outputs(native, family):
    emu = native(family)
    emu.enable_hardware()
    for channel in ("intake", "exhaust"):
        emu.mem.image[VANOS[family, channel][0]] = 0
    modified = bytes(emu.mem.image)
    if family == "MS42":
        write(emu, 0xD084, 4)
        write(emu, 0xD1F2, 4)
        for entry, stop in ((0x2D5BE, 0x2D674), (0x2D842, 0x2D8F8)):
            result = emu.run_from(entry, stop_at=(stop,), max_steps=300)
            assert result.exit_reason == "stop_at", result
        duties = (modified[0x48373], modified[0x48374])
        period = 1251
    else:
        write(emu, 0x4020F, 128)  # Unity Q7 battery factor for the default mode.
        result = emu.run_from(0xD867E, stop_at=(0xD86FA,), max_steps=200)
        assert result.exit_reason == "stop_at", result
        duties = (modified[0x70BB3], modified[0x70BB0])
        period = 1875
    expected = tuple(duty * period // 256 for duty in duties)
    assert all(value > 0 for value in expected)
    assert tuple(emu.mem.read_word_direct(address) for address in (0xFE30, 0xFE32)) == expected
    assert bytes(emu.mem.image) == modified


@pytest.mark.parametrize("low_ron", (False, True))
def test_ms43_normal_knock_cap_does_not_replace_the_independent_low_ron_cap(native, low_ron):
    emu = native("MS43")
    emu.mem.image[0x70B9C] = 128  # Zero ordinary retard limit.
    modified = bytes(emu.mem.image)
    write(emu, 0xFD3A, 0x4000 if low_ron else 0, 2)
    result = emu.run_from(0xB601E, stop_at=(0xB6034,), max_steps=12)
    assert result.exit_reason == "stop_at", result
    assert byte(emu, 0x403D2) == (modified[0x70B9D] if low_ron else 128)
    assert modified[0x70B9D] == 88  # The supplied image's separate -15 degree floor.
    assert bytes(emu.mem.image) == modified


@pytest.mark.parametrize("family", ("MS42", "MS43"))
@pytest.mark.parametrize("retained_correction", (116, 128))
def test_neutral_knock_caps_do_not_bypass_retained_correction_publication(native, family, retained_correction):
    emu = native(family)
    if family == "MS42":
        caps, correction, base, entry, stop, output = (0x4843E,), 0xC464, 0xFA61, 0x2F5FA, 0x2F624, 0xE53D
    else:
        caps, correction, base, entry, stop, output = (0x70B9C, 0x70B9D), 0x403A2, 0xE2B5, 0xB65B0, 0xB65DA, 0xFBAB
    for offset in caps:
        emu.mem.image[offset] = 128
    modified = bytes(emu.mem.image)
    write(emu, correction, retained_correction)
    write(emu, base, 100)
    result = emu.run_from(entry, stop_at=(stop,), max_steps=100)
    assert result.exit_reason == "stop_at", result
    assert byte(emu, output) == 255 - (100 + retained_correction - 128)
    assert bytes(emu.mem.image) == modified


def test_ms43_stopped_state_clears_the_transient_knock_correction_bypass_bits(native):
    emu = native("MS43")
    original = bytes(emu.mem.image)
    write(emu, 0xFD2C, 0x1800, 2)
    result = emu.run_from(0xA9B6A, stop_at=(0xA9B78,), max_steps=12)
    assert result.exit_reason == "stop_at", result
    assert not emu.mem.read_word_direct(0xFD2C) & 0x1800
    assert bytes(emu.mem.image) == original


@pytest.mark.parametrize("family,entry,held,admitted,rejected", [
    ("MS42", 0x2EDE4, 0xC47B, 0x2EDF8, 0x2EEBC),
    ("MS43", 0xB5D44, 0x403B7, 0xB5D5E, 0xB5E66),
])
@pytest.mark.parametrize("load", (254, 255))
def test_knock_minimum_load_ff_still_admits_ff(native, family, entry, held, admitted, rejected, load):
    emu = native(family)
    original = bytes(emu.mem.image)
    emu.reg.set_word(6, 255)  # Maximum byte result from the minimum-load map.
    write(emu, held, load)
    result = emu.run_from(entry, stop_at=(admitted, rejected), max_steps=20)
    assert result.exit_reason == "stop_at", result
    assert emu.cpu.pc == (admitted if load == 255 else rejected)
    assert bytes(emu.mem.image) == original


@pytest.mark.parametrize("family,entry,sample,detected,recovery", [
    ("MS42", 0x2F0DC, 0xE561, 0x2F0E6, 0x2F12E),
    ("MS43", 0xB60B6, 0xFC39, 0xB60C0, 0xB6108),
])
@pytest.mark.parametrize("value", (254, 255))
def test_knock_sample_threshold_ff_still_detects_ff(native, family, entry, sample, detected, recovery, value):
    emu = native(family)
    original = bytes(emu.mem.image)
    emu.reg.set_word(6, 255)  # Maximum result after the threshold's final SHR 8.
    emu.reg.set_word(8, 0)
    write(emu, sample, value)
    result = emu.run_from(entry, stop_at=(detected, recovery), max_steps=20)
    assert result.exit_reason == "stop_at", result
    assert emu.cpu.pc == (detected if value == 255 else recovery)
    assert bytes(emu.mem.image) == original
