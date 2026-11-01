"""Original revision-specific lambda owners, including both bank state paths."""

import pytest

import ms42_ms43_config as config
from tests.test_ms42_ms43_config_daten import packages as packages, native as native


# Gate, calibration threshold, banks, selector, speed/rpm, operating bit,
# temperature accumulator, bank ready byte, coolant, reference-age word.
LAYOUTS = {
    "430037": (0xC105C, 0xACE, (0x8BE6, 0x8D44), 0xFDA0, 0x8061, 0xE0DC,
               (0xFD3C, 15), 0xE280, 0xA1, 0x83EA, 0x12C, 0x9271E),
    "430064": (0xD722C, 0xB6C, (0x8C44, 0x8DB0), 0xFDA4, 0x807F, 0xE0E4,
               (0xFD40, 12), 0xE29E, 0xA5, 0x842D, 0x13C, 0x928AC),
    "430065": (0xD86AC, 0xBC0, (0x8C5C, 0x8DC8), 0xFDA8, 0x807F, 0xE0E4,
               (0xFD42, 2), 0xE29E, 0xA5, 0x8433, 0x13C, 0x928AC),
    "430066": (0xDA482, 0xBC2, (0x8C6A, 0x8DD8), 0xFDA8, 0x807F, 0xE0E4,
               (0xFD40, 14), 0xE29E, 0xA5, 0x8433, 0x13C, 0x928AC),
}
LAYOUTS["430066-later"] = LAYOUTS["430066"]


@pytest.fixture(params=LAYOUTS)
def revision(request, packages, native):
    source = packages[request.param]
    emu = native["MS43"](source.image, experimental=True)
    emu.cpu.set_trace(lambda pc, opcode: all_known(source.memory, pc))
    return emu, LAYOUTS[request.param]


def all_known(memory, pc):
    assert all(at in memory for at in range(pc, pc + 4)), hex(pc)


def set_threshold(emu, offset, disabled):
    original = bytes(emu.mem.image)
    edited = config.apply_config(original, {"Closed-loop lambda feedback":
                                           "Disabled" if disabled else "480 rpm threshold"})
    expected = bytearray(original)
    expected[0x70000 + offset] = 255 if disabled else 15
    assert edited == expected
    emu.mem.image[:] = edited


def seed(emu, layout, bank, correction):
    gate, offset, bases, selector, speed, rpm, operating, accumulator, ready, coolant, age, _ = layout
    base = bases[bank]
    emu.write(selector, bank << 10)
    emu.write(selector + 6, 0x29)
    emu.write(rpm, 3200)
    emu.write_byte(speed, 100)
    emu.write(base + 8, correction)
    emu.write(base + 12, 0x144)
    emu.write(base + 24, 0x8100)
    emu.write(operating[0], 1 << operating[1])
    emu.write(accumulator, 65535)
    emu.write_byte(base + ready, 0x80)
    emu.write_byte(coolant, 255)
    emu.write(selector + 8, 1)
    emu.write(base + age, 1000)
    return base


@pytest.mark.parametrize("bank", (0, 1))
@pytest.mark.parametrize("correction", (0x7000, 0x8000, 0x9000))
def test_whole_native_gate_disables_neutrally_then_restores(revision, bank, correction):
    emu, layout = revision
    gate, offset, _, selector, *_ = layout
    base = seed(emu, layout, bank, correction)
    assert emu.mem.image[0x70000 + offset] == 15
    context = emu.reg.sp, emu.reg.get_word(0), emu.reg.cp, tuple(emu.reg.dpp)
    for threshold in (15, 255, 15):
        set_threshold(emu, offset, threshold == 255)
        before = emu.read(base + 8)
        emu.reg.set_word(12, base)
        assert emu.call(gate, max_steps=2000).exit_reason == "stop_at"
        assert (emu.reg.sp, emu.reg.get_word(0), emu.reg.cp, tuple(emu.reg.dpp)) == context
        assert bool(emu.read(selector + 6) & 1) == (threshold != 255)
        expected = before if threshold != 255 else before + max(-0x144, min(0x144, 0x8000 - before))
        assert emu.read(base + 8) == expected
        assert emu.read(base + 24) == 0x8100


@pytest.mark.parametrize("disabled", (False, True))
def test_speed_saturation_and_admission_cover_every_byte(revision, disabled):
    emu, layout = revision
    gate, offset, _, _, speed, rpm, *_, producer = layout
    set_threshold(emu, offset, disabled)
    for value in (*range(0, 8192, 32), 8191, 8192, 65535):
        emu.write(rpm, value)
        assert emu.run_from(producer, stop_at=producer + 34, max_steps=10).exit_reason == "stop_at"
        actual = min(255, value >> 5)
        assert emu.read_byte(speed) == actual
        assert emu.run_from(gate + 0x30A, stop_at=(gate + 0x314, gate + 0x3A2), max_steps=5).exit_reason == "stop_at"
        assert emu.cpu.pc == (gate + 0x314 if not disabled and actual > 15 else gate + 0x3A2)


@pytest.mark.parametrize("bank", (0, 1))
@pytest.mark.parametrize("condition", ("not_ready", "sensor_fault"))
def test_reenable_still_requires_native_sensor_state(revision, bank, condition):
    emu, layout = revision
    gate, _, _, selector, *_ = layout
    base = seed(emu, layout, bank, 0x9000)
    if condition == "not_ready":
        emu.write(selector + 6, 0)
    else:
        # Each complete owner supplies the bank-specific fault test at60/84.
        instruction = bytes(emu.mem.image[(gate & 0x7FFFF) + (0x60 if bank else 0x84):
                                          (gate & 0x7FFFF) + (0x64 if bank else 0x88)])
        assert instruction[0] == 0x8A
        emu.write(0xFD00 + instruction[1] * 2, 1 << (instruction[3] >> 4))
    emu.reg.set_word(12, base)
    assert emu.call(gate, max_steps=2000).exit_reason == "stop_at"
    assert not emu.read(selector + 6) & 1
