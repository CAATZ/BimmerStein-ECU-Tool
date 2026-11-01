"""Original older-MS43 purge tasks, fault counters and explicit-test admission."""

import pytest

from tests.test_ms42_ms43_config_daten import packages as packages, native as native
from tests.test_ms43_revision_config_controls import checked_native


# Normal threshold/coolant/demand/operating flags; automatic diagnosis owner and
# threshold/request/learned-ready/operating-ready/result; electrical producer.
LAYOUTS = {
    "430037": ((0xBF8, 0x83EA, 0x86B0, 0xFDA2, 0xFD70, 10, 0xFD8C),
               (0xA174C, 0xBFA, 0x8148, 0xFD2A, 6, 0xFD32, 10, 0xFD8E),
               (0xA300E, 0x9AE, 0x970, 0x92D4, 0xFBF3, 0xFDD6)),
    "430055": ((0xC87, 0x841E, 0x86EC, 0xFDA4, 0xFD72, 12, 0xFD8E),
               (0x9EEE0, 0xC89, 0x816C, 0xFD2C, 2, 0xFD34, 9, 0xFD90),
               (0xA1DDA, 0xA27, 0x9E6, 0x9336, 0xFC0A, 0xFDD8)),
    "430064": ((0xCA5, 0x842D, 0x86FA, 0xFDA6, 0xFD74, 12, 0xFD90),
               (0x9EF02, 0xCA7, 0x8171, 0xFD2C, 8, 0xFD36, 2, 0xFD92),
               (0xA1E16, 0xA3B, 0x9FA, 0x9386, 0xFC0C, 0xFDDA)),
    "430065": ((0xCFF, 0x8433, 0x8712, 0xFDAA, 0xFD78, 8, 0xFD94),
               (0x9F57E, 0xD01, 0x8176, 0xFD2C, 13, 0xFD36, 8, 0xFD96),
               (0xA2AA8, 0xA8A, 0xA46, 0x93E2, 0xFC0C, 0xFDE0)),
    "430066": ((0xD00, 0x8433, 0x8720, 0xFDAA, 0xFD78, 4, 0xFD94),
               (0x9F5DE, 0xD02, 0x8176, 0xFD2C, 9, 0xFD36, 4, 0xFD96),
               (0xA2AA8, 0xA8C, 0xA48, 0x93F4, 0xFC0C, 0xFDE0)),
}
LAYOUTS["430066-later"] = LAYOUTS["430066"]


@pytest.mark.parametrize("key", LAYOUTS)
@pytest.mark.parametrize("disabled", (False, True))
def test_whole_normal_purge_owner_covers_all_coolant_values(packages, native, key, disabled):
    source = packages[key]
    offset, coolant, demand, ready, running, bit, state = LAYOUTS[key][0]
    assert source.image[0x70000 + offset] == 151
    emu = checked_native(source, native)
    assert emu.call(0xA0070, max_steps=10000).exit_reason == "stop_at"
    threshold = 255 if disabled else 151
    emu.mem.image[0x70000 + offset] = threshold
    seen = set()
    def trace(pc, opcode):
        assert all(at in source.memory for at in range(pc, pc + 4)), hex(pc)
        seen.add(pc)
    emu.cpu.set_trace(trace)
    for temperature in range(256):
        emu.write(ready, 0x1000)
        emu.write(running, 1 << bit)
        emu.write(state, 8)
        emu.write_byte(coolant, temperature)
        emu.write(demand, 4000)
        seen.clear()
        assert emu.call(0xA01AC, max_steps=10000).exit_reason == "stop_at"
        assert (0xA0200 in seen) == (temperature > threshold)
        if disabled:
            assert emu.read(demand) == 0
            assert emu.read(state) & 0x7F == 1


@pytest.mark.parametrize("key", LAYOUTS)
@pytest.mark.parametrize("disabled", (False, True))
def test_flow_diagnosis_preserves_explicit_request_and_other_conditions(packages, native, key, disabled):
    emu = checked_native(packages[key], native)
    entry, offset, request, learned, learned_bit, running, run_bit, result = LAYOUTS[key][1]
    assert emu.mem.image[0x70000 + offset] == 253
    threshold = 255 if disabled else 253
    emu.mem.image[0x70000 + offset] = threshold
    emu.write(learned, 1 << learned_bit)
    emu.write(running, 1 << run_bit)
    for temperature in range(256):
        emu.write_byte(LAYOUTS[key][0][1], temperature)
        assert emu.call(entry, max_steps=100).exit_reason == "stop_at"
        assert bool(emu.read(result) & 0x800) == (temperature > threshold)
    emu.write_byte(request, 1)
    assert emu.call(entry, max_steps=100).exit_reason == "stop_at"
    assert emu.read(result) & 0x800
    emu.write(running, 0)
    assert emu.call(entry, max_steps=100).exit_reason == "stop_at"
    assert not emu.read(result) & 0x800


@pytest.mark.parametrize("key", LAYOUTS)
@pytest.mark.parametrize("fault", (0, 1, 2))
def test_electrical_diagnosis_controls_new_faults_without_erasing_history(packages, native, key, fault):
    emu = checked_native(packages[key], native)
    entry, offset, increment, record, delay, driver = LAYOUTS[key][2]
    assert (emu.mem.image[0x70000 + offset], emu.mem.image[0x70000 + increment]) == (10, 2)
    recorded = False
    for limit in (0, 10, 0):
        emu.mem.image[0x70000 + offset] = limit
        for _ in range(8):
            emu.write(driver + 2 * fault, 0x40)
            emu.write_byte(delay, 0)
            assert emu.run_from(entry, stop_at=entry + 88, max_steps=10000).exit_reason == "stop_at"
        if limit:
            assert emu.read_byte(record) & 0x20
            assert emu.read_byte(record + 1) == limit
            recorded = True
        else:
            assert bool(emu.read_byte(record) & 0x20) == recorded
            if not recorded:
                assert emu.read_byte(record) == 1 << fault
                assert emu.read_byte(record + 1) == 0
    assert emu.read_byte(record) & 0x20
