"""Original firmware binds separate ADC/DS2 rails and roughness rejection causes.

RAM/ADC inputs are supplied; these tests do not model the bench wiring or an engine.
"""

import pytest

from ms42_ms43 import MS42MS43ECU
from tests.test_ms42_ms43 import Link, reply
from tests.test_ms42_ms43_config_native import native as native


@pytest.mark.parametrize("main,ignition", ((100, 484), (484, 100), (0, 1023)))
def test_original_ms43_adc_producer_and_fixed_group_keep_distinct_rails(native, main, ignition):
    emu = native("MS43")
    before = bytes(emu.mem.image)
    emu.write(0xE572, main)
    emu.write(0xE570, ignition)
    result = emu.run_from(0xE184E, stop_at=0xE1862, max_steps=8)
    assert result.exit_reason == "stop_at"
    assert emu.read_byte(0xFBD5) == main >> 2
    assert emu.read_byte(0xFBD6) == ignition >> 2
    emu.write_byte(0xF6D0, 3)
    emu.write(0xFD1C, 4)  # Select the stored radiator temperature branch.
    emu.write(0xFD4A, 0)
    emu.write(0xFDB2, 0)
    emu.write(0xFDC2, 0)
    result = emu.call(0xA5C1E, max_steps=500)
    assert result.exit_reason == "stop_at"
    assert emu.read_byte(0xF74E) == 45
    payload = bytes(emu.read_byte(0xF750 + index) for index in range(41))
    ecu = MS42MS43ECU(Link(lambda *_: reply(payload)), "MS43")
    assert ecu.read_supply_voltages() == pytest.approx({"battery": (main >> 2) * .10156,
                                                      "ignition": (ignition >> 2) * .10156})
    assert bytes(emu.mem.image) == before


@pytest.mark.parametrize("family", ("MS42", "MS43"))
@pytest.mark.parametrize("condition", ("ready", "engine_stopped", "active_check", "override0", "override1", "override2"))
def test_original_roughness_start_result_one_has_multiple_causes(native, family, condition):
    emu = native(family)
    before = bytes(emu.mem.image)
    if family == "MS43":
        entry, selector, master, flag, mask, overrides, response = (
            0xA7006, 0xF6D0, 0x4017C, 0xFD2C, 0x200, (0xFD8C, 0xFD8E, 0xFD90), 0xF750)
        emu.mem._physical_write(0x4017A, 0)
    else:
        entry, selector, master, flag, mask, overrides, response = (
            0x214BC, 0xF7A1, 0xC37B, 0xFD3C, 4, (0xFD76, 0xFD78, 0xFD7A), 0xF821)
    emu.write_byte(selector, 5)
    write_byte = getattr(emu.mem, "_physical_write", emu.mem.write_byte_direct)
    write_byte(master, int(condition == "active_check"))
    emu.write(flag, 0 if condition == "engine_stopped" else mask)
    for index, address in enumerate(overrides):
        emu.write(address, int(condition == f"override{index}"))
    result = emu.call(entry, max_steps=500)
    assert result.exit_reason == "stop_at"
    assert emu.read_byte(response) == (0 if condition == "ready" else 1)
    assert bytes(emu.mem.image) == before
    if family == "MS43":
        assert emu.mem._physical_read(0x4017A) == int(condition == "ready")
