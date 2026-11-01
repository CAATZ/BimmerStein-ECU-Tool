"""Native lambda admission and speed boundaries, with supplied operating state.

These execute original firmware through its normal disable/re-enable paths;
they do not establish sensor diagnosis deletion or physical engine behavior.
"""

import pytest

import ms42_ms43_config as config
from tests.test_ms42_ms43_config_native import native as native
from tests.test_ms42_ms43_config_daten import packages as packages


SETTING = "Closed-loop lambda feedback"
LAYOUTS = {
    "MS42": dict(offset=0x482A2, threshold=10, speed=0xC42E, rpm=0xFA22,
                 producer=0x75722, producer_end=0x7573C, compare=0x312AA,
                 allowed=0x312B6, rejected=0x3136A, gate=0x30F56,
                 bases=(0xE33A, 0xE424), selector=0xFD4C, status=0xFD36,
                 ready=0x209, control=6, step=10, adaptation=0x16),
    "MS43": dict(offset=0x70BBB, threshold=15, speed=0x807F, rpm=0xE0E4,
                 producer=0x928AC, producer_end=0x928CE, compare=0xDA790,
                 allowed=0xDA79A, rejected=0xDA828, gate=0xDA486,
                 bases=(0x8C6A, 0x8DD8), selector=0xFDA8, status=0xFDAE,
                 ready=0x29, control=8, step=12, adaptation=0x18),
}


def seed_bank(emu, family, bank, correction):
    layout = LAYOUTS[family]
    base = layout["bases"][bank]
    emu.write(layout["selector"], bank << 10)
    emu.write(layout["status"], layout["ready"])
    emu.write(layout["rpm"], 3200)
    emu.write_byte(layout["speed"], 100)
    emu.write(base + layout["control"], correction)
    emu.write(base + layout["step"], 0x144)
    emu.write(base + layout["adaptation"], 0x8100)
    if family == "MS42":
        emu.write(0xFD0E, 0x200)
        emu.write_byte(0xC424, 255)
        emu.write(0xFD38, 1)
    else:
        emu.write(0xFD40, 0x4000)
        emu.write(0xE29E, 65535)
        emu.write_byte(base + 0xA5, 0x80)
        emu.write_byte(0x8433, 255)
        emu.write(0xFDB0, 1)
        emu.write(base + 0x13C, 1000)
    return base


@pytest.mark.parametrize("family", LAYOUTS)
@pytest.mark.parametrize("rpm", [0, 319, 320, 351, 480, 511, 8160, 8191, 8192, 65535])
def test_native_lambda_speed_is_saturated_unsigned_byte(native, family, rpm):
    emu = native(family)
    layout = LAYOUTS[family]
    emu.write(layout["rpm"], rpm)
    result = emu.run_from(layout["producer"], stop_at=layout["producer_end"], max_steps=10)
    assert result.exit_reason == "stop_at"
    assert emu.read_byte(layout["speed"]) == min(255, rpm >> 5)


@pytest.mark.parametrize("family", LAYOUTS)
@pytest.mark.parametrize("disabled", [False, True])
def test_native_lambda_admission_covers_every_speed_byte(native, family, disabled):
    emu = native(family)
    layout = LAYOUTS[family]
    feature = next(f for f in config.features_for(family, "0110C6" if family == "MS42" else "430069")
                   if f.name == SETTING)
    emu.mem.image[:] = config.apply_config(bytes(emu.mem.image), {
        SETTING: "Disabled" if disabled else feature.options[0][0]})
    for speed in range(256):
        emu.write_byte(layout["speed"], speed)
        result = emu.run_from(layout["compare"], stop_at=(layout["allowed"], layout["rejected"]), max_steps=5)
        assert result.exit_reason == "stop_at"
        expected = layout["allowed"] if not disabled and speed > layout["threshold"] else layout["rejected"]
        assert emu.cpu.pc == expected, speed


@pytest.mark.parametrize("family", LAYOUTS)
@pytest.mark.parametrize("bank", [0, 1])
@pytest.mark.parametrize("correction", [0x7000, 0x8000, 0x9000])
def test_native_lambda_disable_settles_then_reenables_both_banks(native, family, bank, correction):
    emu = native(family)
    layout = LAYOUTS[family]
    base = seed_bank(emu, family, bank, correction)
    original = bytes(emu.mem.image)
    assert original[layout["offset"]] == layout["threshold"]
    normal = config.read_config(original)[SETTING]
    before_context = emu.reg.sp, emu.reg.get_word(0), emu.reg.cp, tuple(emu.reg.dpp)
    for choice in (normal, "Disabled", normal):
        emu.mem.image[:] = config.apply_config(bytes(emu.mem.image), {SETTING: choice})
        before = emu.read(base + layout["control"])
        emu.reg.set_word(12, base)
        result = emu.call(layout["gate"], max_steps=2000)
        assert result.exit_reason == "stop_at"
        assert (emu.reg.sp, emu.reg.get_word(0), emu.reg.cp, tuple(emu.reg.dpp)) == before_context
        assert bool(emu.read(layout["status"]) & 1) == (choice == normal)
        expected = before if choice == normal else before + max(-0x144, min(0x144, 0x8000 - before))
        assert emu.read(base + layout["control"]) == expected
        assert emu.read(base + layout["adaptation"]) == 0x8100
    assert bytes(emu.mem.image) == original


@pytest.mark.parametrize("family", LAYOUTS)
@pytest.mark.parametrize("bank", [0, 1])
@pytest.mark.parametrize("condition", ["sensor_not_ready", "sensor_fault"])
def test_native_lambda_reenable_preserves_sensor_prerequisites(native, family, bank, condition):
    emu = native(family)
    layout = LAYOUTS[family]
    base = seed_bank(emu, family, bank, 0x9000)
    if condition == "sensor_not_ready":
        emu.write(layout["status"], 0)
    else:
        address, mask = ((0xFD84, 0x400) if bank == 0 else (0xFD86, 2)) if family == "MS42" else (
            (0xFDB2, 0x4000) if bank == 0 else (0xFDB4, 4))
        emu.write(address, mask)
    emu.reg.set_word(12, base)
    result = emu.call(layout["gate"], max_steps=2000)
    assert result.exit_reason == "stop_at"
    assert not emu.read(layout["status"]) & 1


@pytest.mark.parametrize("family", LAYOUTS)
def test_lambda_custom_threshold_is_preserved_until_explicit_selection(native, family):
    emu = native(family)
    layout = LAYOUTS[family]
    emu.mem.image[layout["offset"]] = 42
    data = bytes(emu.mem.image)
    values = config.read_config(data)
    assert values[SETTING].startswith("Custom")
    assert config.apply_config(data, values) == data
    updated = config.apply_config(data, {SETTING: "Disabled"})
    assert [i for i, (old, new) in enumerate(zip(data, updated)) if old != new] == [layout["offset"]]


@pytest.mark.parametrize("family", LAYOUTS)
@pytest.mark.parametrize("high_sensor", [False, True])
def test_native_lambda_bank_tasks_stop_new_sensor_feedback(native, family, high_sensor):
    emu = native(family)
    layout = LAYOUTS[family]
    if family == "MS42":
        update, publish, status, sensor = 0x303F6, 0x30200, 2, 0xB7
        enable, count, purge = 0xFD42, 0xE516, 0xE514
        emu.write(0xFD04, 0x400)
    else:
        update, publish, status, sensor = 0xD9842, 0xD9630, 4, 0xA8
        enable, count, purge = 0xFD9C, 0x8C64, 0x8C62
        emu.write(0xFD1A, 0x60)
    for bank, base in enumerate(layout["bases"]):
        seed_bank(emu, family, bank, 0x9000)
        emu.write(base + status, layout["ready"])
        emu.write(base + status + 2, 1)
    emu.write(enable, emu.read(enable) | 8)
    emu.write_byte(count, 2)
    emu.write(purge, 0x8000)
    emu.mem.image[:] = config.apply_config(bytes(emu.mem.image), {SETTING: "Disabled"})
    # These tasks alternate banks and copy prior admission before refreshing it.
    # Two visits per bank retire prior eligibility; they are not a wall-time claim.
    for _ in range(4):
        result = emu.call(update, max_steps=20000)
        assert result.exit_reason == "stop_at"
    for base in layout["bases"]:
        assert emu.read(base + status) & 9 == 0
        emu.write_byte(base + sensor, 255 if high_sensor else 0)
    before = [emu.read(base + layout["control"]) for base in layout["bases"]]
    result = emu.call(publish, max_steps=20000)
    assert result.exit_reason == "stop_at"
    assert [emu.read(base + layout["control"]) for base in layout["bases"]] == before


@pytest.mark.parametrize("bank", [0, 1])
@pytest.mark.parametrize("correction", [0x7000, 0x8000, 0x9000])
def test_430055_native_complete_lambda_admission_disables_and_restores(native, packages, bank, correction):
    if "430055" not in packages:
        pytest.skip("private MS43 430055 image unavailable")
    image = packages["430055"].image
    emu = type(native("MS43"))(image, experimental=True)
    base = (0x8C30, 0x8D8E)[bank]
    emu.write(0xFDA2, bank << 10)
    emu.write(0xFDA8, 0x29)
    emu.write(0xE0E4, 3200)
    emu.write_byte(0x807F, 100)
    emu.write(base + 8, correction)
    emu.write(base + 12, 0x144)
    emu.write(base + 24, 0x8100)
    emu.write(0xFD3E, 0x8000)
    emu.write(0xE29E, 65535)
    emu.write_byte(base + 0xA1, 0x80)
    emu.write_byte(0x841E, 255)
    emu.write(0xFDAA, 1)
    emu.write(base + 0x12C, 1000)
    assert image[0x70B54] == 15
    context = emu.reg.sp, emu.reg.get_word(0), emu.reg.cp, tuple(emu.reg.dpp)
    for choice in ("480 rpm threshold", "Disabled", "480 rpm threshold"):
        emu.mem.image[:] = config.apply_config(bytes(emu.mem.image), {SETTING: choice})
        before = emu.read(base + 8)
        emu.reg.set_word(12, base)
        result = emu.call(0xBCE92, max_steps=2000)
        assert result.exit_reason == "stop_at"
        assert (emu.reg.sp, emu.reg.get_word(0), emu.reg.cp, tuple(emu.reg.dpp)) == context
        assert bool(emu.read(0xFDA8) & 1) == (choice != "Disabled")
        assert emu.read(base + 8) == (before if choice != "Disabled" else
                                    before + max(-0x144, min(0x144, 0x8000 - before)))
        assert emu.read(base + 24) == 0x8100
    assert bytes(emu.mem.image) == image


@pytest.mark.parametrize("disabled", [False, True])
def test_430055_native_lambda_saturated_speed_boundary(native, packages, disabled):
    if "430055" not in packages:
        pytest.skip("private MS43 430055 image unavailable")
    image = packages["430055"].image
    emu = type(native("MS43"))(image, experimental=True)
    emu.mem.image[:] = config.apply_config(image, {
        SETTING: "Disabled" if disabled else "480 rpm threshold"})
    for rpm in (*range(0, 8192, 32), 8191, 8192, 65535):
        emu.write(0xE0E4, rpm)
        result = emu.run_from(0x9289C, stop_at=0x928BE, max_steps=10)
        assert result.exit_reason == "stop_at"
        speed = min(255, rpm >> 5)
        assert emu.read_byte(0x807F) == speed
        result = emu.run_from(0xBD19C, stop_at=(0xBD1A6, 0xBD234), max_steps=5)
        assert result.exit_reason == "stop_at"
        assert emu.cpu.pc == (0xBD1A6 if not disabled and speed > 15 else 0xBD234)
