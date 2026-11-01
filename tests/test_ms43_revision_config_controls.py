"""Original native owners for revision-specific MS43 configuration controls."""

import pytest

from tests.test_ms42_ms43_config_daten import packages as packages, native as native
from tests.test_ms42_ms43_config_revisions import private_sources as private_sources


CRUISE = {
    "430037": (0x962F4, 0xA3B, 0x80E0, 0xD1A, 0x807A),
    "430055": (0x964FA, 0xABB, 0x8101, 0xDAC, 0x8098),
    "430064": (0x9650A, 0xACF, 0x8101, 0xDD1, 0x8098),
    "430065": (0x96B82, 0xB21, 0x8101, 0xE3D, 0x8098),
    "430066": (0x96BE6, 0xB23, 0x8101, 0xE41, 0x8098),
    "430066-later": (0x96BE6, 0xB23, 0x8101, 0xE41, 0x8098),
}
LAMBDA = {
    "430037": (0x9CFB6, 0xA39, 0x80D5, 0x8BE0),
    "430055": (0x9D44A, 0xAB9, 0x80F6, 0x8C2A),
    "430064": (0x9D462, 0xACD, 0x80F6, 0x8C3E),
    "430065": (0x9DADE, 0xB1F, 0x80F6, 0x8C56),
    "430066": (0x9DB3E, 0xB21, 0x80F6, 0x8C64),
    "430066-later": (0x9DB3E, 0xB21, 0x80F6, 0x8C64),
}
BANK = {
    "430037": (0xC62D6, 0xA40, 0x8BE0, (0x8BE6, 0x8D44), 0xE336, 0xC6558, 0xC6C10, 0xC6E2A),
    "430055": (0xC617E, 0xAC0, 0x8C2A, (0x8C30, 0x8D8E), 0xE354, 0xC62FA, 0xC69B2, 0xC6BCC),
    "430064": (0xC38A8, 0xAD4, 0x8C3E, (0x8C44, 0x8DB0), 0xE35E, 0xC3A24, 0xC40DC, 0xC42F6),
    "430065": (0xC2888, 0xB26, 0x8C56, (0x8C5C, 0x8DC8), 0xE35E, 0xC2A04, 0xC30BC, 0xC32D6),
    "430066": (0xBE02A, 0xB28, 0x8C64, (0x8C6A, 0x8DD8), 0xE35E, 0xBE1A6, 0xBE85E, 0xBEA78),
    "430069": (0xBE138, 0xB20, 0x8C64, (0x8C6A, 0x8DD8), 0xE35E, 0xBE2B4, 0xBE96C, 0xBEB86),
}
BANK["430066-later"] = BANK["430066"]
DIAGNOSIS = {
    "430037": ((0xC4856, 0xA46, ((0x400, (0x9010, 0x9024)),)),
               (0xC4868, 0xA3C, ((0x20, (0x9018, 0x902C)),))),
    "430055": ((0xC46EE, 0xAC6, ((0x1000, (0x9068, 0x907C)),)),
               (0xC4700, 0xABC, ((0x80, (0x9070, 0x9084)),))),
    "430064": ((0xC1E16, 0xADA, ((0x1000, (0x90B8, 0x90CC)),)),
               (0xC1E28, 0xAD0, ((0x80, (0x90C0, 0x90D4)),))),
    "430065": ((0xC0D20, 0xB2C, ((1, (0x90DA, 0x90F0)),)),
               (0xC0D32, 0xB22, ((0x800, (0x90E0, 0x90F6)), (2, (0x90E2, 0x90F8)),
                                (0x2000, (0x90DC, 0x90F2))))),
    "430066": ((0xC0D20, 0xB2E, ((1, (0x90EC, 0x9102)),)),
               (0xC0D32, 0xB24, ((0x800, (0x90F2, 0x9108)), (2, (0x90F4, 0x910A)),
                                (0x2000, (0x90EE, 0x9104))))),
    "430066-later": ((0xC0D20, 0xB2E, ((1, (0x90EC, 0x9102)),)),
                     (0xC0D32, 0xB24, ((0x800, (0x90F2, 0x9108)),
                                      (0x2000, (0x90EE, 0x9104))))),
}
CRANK = {"430037": (0xC87B4, 0xA49, 0xFD68, 13),
         "430055": (0xC8A92, 0xAC9, 0xFD6A, 15),
         "430064": (0xC5CB4, 0xADD, 0xFD6C, 15),
         "430065": (0xC55BE, 0xB2F, 0xFD70, 11),
         "430066": (0xC3F8A, 0xB31, 0xFD70, 7),
         "430066-later": (0xC3FA8, 0xB31, 0xFD70, 7)}
CHARGE = {"430037": ((0xADDAA, 0xAF0DE), 0xA3A, 0xFD94),
          "430055": ((0xAD0D2, 0xAE4F2), 0xABA, 0xFD96),
          "430064": ((0xAD2B0, 0xAE6D0), 0xACE, 0xFD98),
          "430065": ((0xB21FA, 0xD6D04), 0xB20, 0xFD9C),
          "430066": ((0xB01B2, 0xD8ADA), 0xB22, 0xFD9C),
          "430066-later": ((0xB01B2, 0xD8ADA), 0xB22, 0xFD9C)}
LOAD = {"430055": ((0x9CDD8, 0x9D0A4), 0xAB6, 0xE334, 0xE339, 0xE338),
        "430064": ((0x9CDF0, 0x9D0BC), 0xACA, 0xE336, 0xE33B, 0xE33A),
        "430065": ((0x9D46C, 0x9D738), 0xB1C, 0xE336, 0xE33B, 0xE33A),
        "430066": ((0x9D4CC, 0x9D798), 0xB1E, 0xE336, 0xE33B, 0xE33A),
        "430066-later": ((0x9D4CC, 0x9D798), 0xB1E, 0xE336, 0xE33B, 0xE33A)}
SAP = {"430037": (0xC7160, 0xA45, 0x80D4, (8, 12, 13)),
       "430055": (0xC6F02, 0xAC5, 0x80F5, (6, 10, 11)),
       "430064": (0xC462C, 0xAD9, 0x80F5, (6, 10, 11)),
       "430065": (0xC360C, 0xB2B, 0x80F5, (7, 11, 12)),
       "430066": (0xBEDAE, 0xB2D, 0x80F5, (3, 7, 8)),
       "430066-later": (0xBEDAE, 0xB2D, 0x80F5, (3, 7, 8))}
EGT = {"430055": (0x96538, 0xAC8, 0x8578, 0x857E, 0x8580),
       "430064": (0x96548, 0xADC, 0x8586, 0x858C, 0x858E),
       "430065": (0x96BC0, 0xB2E, 0x859E, 0x85A4, 0x85A6),
       "430066": (0x96C24, 0xB30, 0x85A8, 0x85AE, 0x85B0),
       "430066-later": (0x96C24, 0xB30, 0x85A8, 0x85AE, 0x85B0)}
HYSTERESIS = {
    "430037": (0xAFF00, (0xA43, 0xA44), 0xFD94, 0xFD2A, 11, 0x8177),
    "430055": (0xC7408, (0xAC3, 0xAC4), 0xFD96, 0xFD2C, 7, 0x819E),
    "430064": (0xBFC52, (0xAD7, 0xAD8), 0xFD98, 0xFD2C, 13, 0x81A3),
    "430065": (0xC3B16, (0xB29, 0xB2A), 0xFD9C, 0xFD2E, 2, 0x81A8),
    "430066": (0xBF2B8, (0xB2B, 0xB2C), 0xFD9C, 0xFD2C, 14, 0x81A8),
    "430066-later": (0xBF2B8, (0xB2B, 0xB2C), 0xFD9C, 0xFD2C, 14, 0x81A8),
}
FIRST_GEAR = {
    "430037": (0x9057A, 0xA4A, 0x8EC, 0xFD94, 0x817B, 0x801E),
    "430055": (0x90584, 0xACA, 0x954, 0xFD96, 0x81A2, 0x8023),
    "430064": (0x90584, 0xADE, 0x968, 0xFD98, 0x81A7, 0x8023),
    "430065": (0x90584, 0xB30, 0x9AC, 0xFD9C, 0x81AC, 0x8023),
    "430066": (0x90584, 0xB32, 0x9AE, 0xFD9C, 0x81AC, 0x8023),
    "430066-later": (0x90584, 0xB32, 0x9AE, 0xFD9C, 0x81AC, 0x8023),
}
TRACTION = {
    "430037": (0x90D32, 0xCE2, 0x801E, 0x8024, 0x83EA, 0xC06),
    "430055": (0x90E28, 0xD7B, 0x8023, 0x8029, 0x841E, 0xC96),
    "430064": (0x90D68, 0xD9F, 0x8023, 0x8029, 0x842D, 0xCB4),
    "430065": (0x90D68, 0xE08, 0x8023, 0x8029, 0x8433, 0xD0F),
    "430066": (0x90D68, 0xE09, 0x8023, 0x8029, 0x8433, 0xD10),
    "430066-later": (0x90D68, 0xE09, 0x8023, 0x8029, 0x8433, 0xD10),
}
CAM = {"430064": (0xA39BE, 0xDA1, 4, 0xFD76, 13, 0x8151),
       "430065": (0xA4650, 0xE0A, 9, 0xFD7A, 9, 0x8156),
       "430066": (0xA4650, 0xE0B, 5, 0xFD7A, 5, 0x8156),
       "430066-later": (0xA4650, 0xE0B, 5, 0xFD7A, 5, 0x8156)}
DASHPOT = {"430055": (0xC2CA2, 0xD79), "430064": (0xC09D4, 0xD9C),
           "430065": (0xBEF72, 0xE03), "430066": (0xBCFAE, 0xE04),
           "430066-later": (0xBCFAE, 0xE04)}
AMBIENT = {"430065": (0xBFE52, 0xE05, 13), "430066": (0xBF796, 0xE06, 9),
           "430066-later": (0xBF796, 0xE06, 9)}
SIGNAL_DELAY = {"430064": ((0xD7F1E, 0xD7F96), 0xD9D),
                "430065": ((0xD939E, 0xD9416), 0xE06),
                "430066": ((0xDB174, 0xDB1EC), 0xE07),
                "430066-later": ((0xDB17C, 0xDB1FC), 0xE07)}
FLAP = {"430037": ((0xA9E0A, 0xA9F04), (0xB62, 0xB63, 0xB61), 0x8428, 0xCC8, 0xFD34, 13, 0xFDC4, 0xFD84),
        "430055": ((0xA90B0, 0xA91AA), (0xBE8, 0xBE9, 0xBE2), 0x845E, 0xD60, 0xFD36, 12, 0xFDC6, 0xFD86),
        "430064": ((0xA91E0, 0xA92DA), (0xC05, 0xC06, 0xBFF), 0x846D, 0xD82, 0xFD38, 5, 0xFDC8, 0xFD88)}
BRAKE = {"430037": (0xAF42E, 0xA37), "430055": (0xAE84A, 0xAB7),
         "430064": (0xAEA2C, 0xACB), "430065": (0xB2562, 0xB1D)}
TANK = {"430065": (0xD61C8, 0xB22, 0xE04),
        "430066": (0xD61C8, 0xB24, 0xE05), "430066-later": (0xD61C8, 0xB24, 0xE05)}


def checked_native(source, native):
    emu = native["MS43"](source.image, experimental=True)
    def check(pc, opcode):
        assert all(at in source.memory for at in range(pc, pc + 4)), hex(pc)
    emu.cpu.set_trace(check)
    return emu


def cruise(emu, layout, mode, button, ignition):
    entry, offset, button_address, ignition_offset, output = layout
    emu.mem.image[0x70000 + offset] = mode
    emu.mem.image[0x70000 + ignition_offset] = ignition
    emu.write_byte(button_address, button)
    # The enclosing native task supplies this filter descriptor before the
    # configuration branch; use its actual descriptor for the complete call.
    at = entry & 0x7FFFF
    assert bytes(emu.mem.image[at - 10:at - 8]) == b"\xe6\xfc"
    emu.reg.set_word(12, int.from_bytes(emu.mem.image[at - 8:at - 6], "little"))
    assert emu.run_from(entry, stop_at=entry + 26, max_steps=15).exit_reason == "stop_at"
    selected = ignition if mode else button
    assert emu.reg.get_word(13) == (selected + 128) & 255
    emu.reg.set_word(12, int.from_bytes(emu.mem.image[at - 8:at - 6], "little"))
    assert emu.call(entry, max_steps=300).exit_reason == "stop_at"
    return emu.read_byte(output)


@pytest.mark.parametrize("key", CRUISE)
@pytest.mark.parametrize("mode", (0, 1))
@pytest.mark.parametrize("button", (0, 128))
@pytest.mark.parametrize("ignition", (0, 128))
def test_complete_cruise_owner_chooses_the_same_actual_source(packages, native, private_sources,
                                                           key, mode, button, ignition):
    base = native["MS43"](private_sources["stock"], experimental=True)
    expected = cruise(base, (0x96BE6, 0xB1B, 0x8101, 0xE3C, 0x8098), mode, button, ignition)
    actual = cruise(checked_native(packages[key], native), CRUISE[key], mode, button, ignition)
    assert actual == expected


def lambda_init(emu, layout, mode, learned):
    entry, offset, state, count = layout
    emu.mem.image[0x70000 + offset] = mode
    code = bytes(emu.mem.image[(entry & 0x7FFFF) + 0x14:(entry & 0x7FFFF) + 0x18])
    assert code[0] == 0x8A
    emu.write(0xFD00 + code[1] * 2, (1 << (code[3] >> 4)) if learned else 0)
    for address in (state, state + 1, count):
        emu.write_byte(address, 0xA5)
    owner = bytes(emu.mem.image[(entry & 0x7FFFF):(entry & 0x7FFFF) + 0x8E])
    call = next(i for i in range(0, 0x86, 2) if owner[i:i + 2] == b"\xda\x09")
    learning = 0x90000 + int.from_bytes(owner[call + 2:call + 4], "little")
    assert emu.run_from(entry, stop_at=(entry + 0x8A, learning), max_steps=100).exit_reason == "stop_at"
    # This bounded selector dispatches the real persistent-variant owner. Its
    # SSC transaction is outside this selector check, without a test callback.
    return (emu.cpu.pc == learning,
            tuple(emu.read_byte(address) for address in (state, state + 1, count)))


@pytest.mark.parametrize("key", LAMBDA)
@pytest.mark.parametrize("mode", (0, 1, 2, 3, 4, 255))
@pytest.mark.parametrize("learned", (False, True))
def test_lambda_selector_preserves_modes_and_learning_dispatch(packages, native, private_sources,
                                                              key, mode, learned):
    base = native["MS43"](private_sources["stock"], experimental=True)
    expected = lambda_init(base, (0x9DB3E, 0xB19, 0x80F6, 0x8C64), mode, learned)
    actual = lambda_init(checked_native(packages[key], native), LAMBDA[key], mode, learned)
    assert actual == expected


@pytest.mark.parametrize("key", BANK)
@pytest.mark.parametrize("mode", (0, 1, 255))
@pytest.mark.parametrize("active_bank", (0, 1, 2))
@pytest.mark.parametrize("consumer_bank", (0, 1))
def test_secondary_air_lambda_option_selects_a_bank_instead_of_disabling_diagnosis(
        packages, native, private_sources, key, mode, active_bank, consumer_bank):
    emu = (native["MS43"](private_sources["stock"], experimental=True) if key == "430069" else
           checked_native(packages[key], native))
    entry, offset, count, banks, pointer, end, consumer, skip = BANK[key]
    emu.mem.image[0x70000 + offset] = mode
    emu.write_byte(count, active_bank)
    assert emu.run_from(entry, stop_at=end, max_steps=15).exit_reason == "stop_at"
    selected = 0 if mode == 0 or active_bank == 1 else 1
    assert emu.read(pointer) == banks[selected]
    emu.reg.set_word(9, banks[consumer_bank])
    assert emu.run_from(consumer, stop_at=(consumer + 8, skip), max_steps=4).exit_reason == "stop_at"
    assert emu.cpu.pc == (consumer + 8 if consumer_bank == selected else skip)


@pytest.mark.parametrize("key", DIAGNOSIS)
@pytest.mark.parametrize("setting", (0, 1))
@pytest.mark.parametrize("value", (0, 1, 255))
def test_secondary_air_and_dmtl_options_keep_the_native_diagnosis_masks(packages, native, key, setting, value):
    source = packages[key]
    entry, offset, groups = DIAGNOSIS[key][setting]
    expected = bytearray(b"\xf3\xf8" + offset.to_bytes(2, "little") + bytes((0x3D, len(groups) * 6)))
    for mask, addresses in groups:
        expected += b"\xe6\xf4" + (65535 ^ mask).to_bytes(2, "little")
        for address in addresses:
            expected += b"\x64\xf4" + address.to_bytes(2, "little")
    assert bytes(source.memory[at] for at in range(entry, entry + len(expected))) == expected
    emu = checked_native(source, native)
    emu.mem.image[0x70000 + offset] = value
    # A distinct neighboring mask remains untouched, including066-later's
    # independently controlled tank-level diagnosis.
    for address in range(0x9000, 0x9120, 2):
        emu.write(address, 65535)
    assert emu.run_from(entry, stop_at=entry + len(expected), max_steps=20).exit_reason == "stop_at"
    changed = {address: 65535 ^ mask for mask, addresses in groups for address in addresses} if not value else {}
    for address in range(0x9000, 0x9120, 2):
        assert emu.read(address) == changed.get(address, 65535)


@pytest.mark.parametrize("key", CRANK)
@pytest.mark.parametrize("abort", (False, True))
def test_native_crank_preinjection_abort_flag_is_owned_by_its_actual_setting(packages, native, key, abort):
    entry, offset, address, bit = CRANK[key]
    expected = (b"\xf3\xf8" + offset.to_bytes(2, "little") + b"\x2d\x02" +
                bytes(((bit << 4) | 15, (address - 0xFD00) // 2, 0x0D, 1,
                       (bit << 4) | 14, (address - 0xFD00) // 2)))
    assert bytes(packages[key].memory[at] for at in range(entry, entry + 12)) == expected
    emu = checked_native(packages[key], native)
    emu.mem.image[0x70000 + offset] = int(abort)
    emu.write(address, 0xA55A)
    assert emu.run_from(entry, stop_at=entry + 12, max_steps=5).exit_reason == "stop_at"
    assert emu.read(address) == (0xA55A & ~(1 << bit)) | (int(abort) << bit)


@pytest.mark.parametrize("key", CHARGE)
@pytest.mark.parametrize("mode", (0, 1, 2, 3, 255))
@pytest.mark.parametrize("automatic", (False, True))
def test_both_charge_balancing_owners_obey_transmission_selection(packages, native, key, mode, automatic):
    entries, offset, flag = CHARGE[key]
    for entry in entries:
        emu = checked_native(packages[key], native)
        code = bytes(packages[key].memory[at] for at in range(entry, entry + 28))
        assert code[:6] == b"\xf3\xf8" + offset.to_bytes(2, "little") + b"\x49\x83"
        assert code[12:16] == bytes((0x8A, (flag - 0xFD00) // 2, 6, 0))
        assert code[22] == 0x3D and code[24] == 0x8A
        rejected = entry + 24 + code[23] * 2
        assert rejected == entry + 28 + code[26] * 2
        emu.mem.image[0x70000 + offset] = mode
        emu.write(flag, int(automatic))
        assert emu.run_from(entry, stop_at=(entry + 28, rejected), max_steps=12).exit_reason == "stop_at"
        admitted = mode == 3 or mode == (2 if automatic else 1)
        assert emu.cpu.pc == (entry + 28 if admitted else rejected)


@pytest.mark.parametrize("key", LOAD)
@pytest.mark.parametrize("substitute", (False, True))
def test_both_antijerk_table_inputs_keep_the_actual_load_reference(packages, native, key, substitute):
    entries, offset, alternate, measured_byte, measured_word = LOAD[key]
    for index, entry in enumerate(entries):
        emu = checked_native(packages[key], native)
        emu.mem.image[0x70000 + offset] = int(substitute)
        emu.write(alternate, 0x1234)
        emu.write(measured_word, 0x5678)
        emu.write_byte(measured_byte, 0x9A)
        stop = (entry + (22 if substitute else 40) if index == 0 else
                entry + (18 if substitute else 36))
        assert emu.run_from(entry, stop_at=stop, max_steps=12).exit_reason == "stop_at"
        assert bytes(emu.mem.image[(stop & 0x7FFFF):(stop & 0x7FFFF) + 2]) == b"\xda\x0d"
        expected = ((0x12 if substitute else 0x9A) if index == 0 else
                    (0x1234 if substitute else 0x9A78))
        assert emu.reg.get_word(14) == expected


def secondary_air(emu, layout, mode, previous):
    entry, offset, state, bits = layout
    emu.mem.image[0x70000 + offset] = mode
    emu.write_byte(state, previous)
    emu.write(0xFD1C, 3)
    assert emu.run_from(entry, stop_at=entry + 64, max_steps=20).exit_reason == "stop_at"
    flags = emu.read(0xFD1C)
    assert flags & ~sum(1 << bit for bit in bits) == 3
    return emu.read_byte(state), tuple(bool(flags & (1 << bit)) for bit in bits)


@pytest.mark.parametrize("key", SAP)
@pytest.mark.parametrize("mode", (0, 1, 2, 3, 255))
@pytest.mark.parametrize("previous", (0, 1, 2, 3))
def test_secondary_air_modes_preserve_learning_and_relocated_flags(
        packages, native, private_sources, key, mode, previous):
    base = native["MS43"](private_sources["stock"], experimental=True)
    expected = secondary_air(base, (0xBEEBC, 0xB25, 0x80F5, (3, 7, 8)), mode, previous)
    assert secondary_air(checked_native(packages[key], native), SAP[key], mode, previous) == expected


def exhaust_temperature(emu, layout, mode, valid):
    entry, offset, model, sensor1, sensor2 = layout
    emu.mem.image[0x70000 + offset] = mode
    for address, value in ((model, 0x1111), (sensor1, 0x2222), (sensor2, 0x3333)):
        emu.write(address, value)
    at = entry & 0x7FFFF
    branch = emu.mem.image[at + 8:at + 12]
    assert branch[0] == 0x9A
    emu.write(0xFD00 + branch[1] * 2, (1 << (branch[3] >> 4)) if valid else 0)
    assert emu.run_from(entry, stop_at=entry + 88, max_steps=100).exit_reason == "stop_at"
    return emu.read(0xE148)


@pytest.mark.parametrize("key", EGT)
@pytest.mark.parametrize("mode", (0, 1, 2, 255))
@pytest.mark.parametrize("valid", (False, True))
def test_exhaust_temperature_modes_keep_sensor_and_model_selection(
        packages, native, private_sources, key, mode, valid):
    base = native["MS43"](private_sources["stock"], experimental=True)
    expected = exhaust_temperature(base, (0x96C24, 0xB28, 0x85A8, 0x85AE, 0x85B0), mode, valid)
    assert exhaust_temperature(checked_native(packages[key], native), EGT[key], mode, valid) == expected


@pytest.mark.parametrize("key", HYSTERESIS)
@pytest.mark.parametrize("automatic", (False, True))
@pytest.mark.parametrize("responsive", (False, True))
@pytest.mark.parametrize("rising", (False, True))
def test_both_fuel_cut_hysteresis_edges_select_the_actual_transmission_option(
        packages, native, key, automatic, responsive, rising):
    entry, offsets, transmission, flags, bit, tracking = HYSTERESIS[key]
    emu = checked_native(packages[key], native)
    emu.mem.image[0x70000 + offsets[int(automatic)]] = int(responsive)
    emu.mem.image[0x70000 + offsets[int(not automatic)]] = int(not responsive)
    emu.write(transmission, int(automatic) << 8)
    emu.write(flags, 3)
    emu.write_byte(tracking, 41)
    emu.write_byte(tracking + 1, 93)
    entry += 48 if rising else 0
    assert emu.run_from(entry, stop_at=entry + 38, max_steps=12).exit_reason == "stop_at"
    changes = responsive if rising else not responsive
    assert emu.read(flags) == 3 | (int(changes) << bit)
    assert emu.read_byte(tracking + 1) == (41 if changes else 93)


def first_gear(emu, layout, enabled, automatic, demand, gear):
    entry, offset, limit, transmission, gear_address, origin = layout
    emu.mem.image[0x70000 + offset] = int(enabled)
    emu.mem.image[0x70000 + limit:0x70000 + limit + 2] = (250).to_bytes(2, "little")
    emu.write(transmission, int(automatic))
    emu.write(0xE040, demand)
    emu.write(0xE05C, 0xFFFF)
    assert emu.run_from(entry, stop_at=entry + 88, max_steps=100).exit_reason == "stop_at"
    computed = (emu.read(0xE048), emu.read(0xE04A))
    assert computed[0] == (min(1023, demand + 250) if enabled and not automatic else 0)
    emu.reg.set_word(8, 1100)
    emu.reg.set_word(9, 1100)
    emu.write_byte(gear_address, gear)
    emu.write_byte(origin, 7)
    assert emu.run_from(entry + 132, stop_at=entry + 170, max_steps=20).exit_reason == "stop_at"
    actual = (emu.reg.get_word(8), emu.reg.get_word(9), emu.read_byte(origin))
    assert actual == ((computed[1], computed[1], 4) if enabled and not automatic and gear == 1 else (1100, 1100, 7))
    return computed, actual


@pytest.mark.parametrize("key", FIRST_GEAR)
@pytest.mark.parametrize("enabled,automatic", ((False, False), (False, True), (True, False), (True, True)))
@pytest.mark.parametrize("demand", (0, 500, 1200))
@pytest.mark.parametrize("gear", (1, 2))
def test_first_gear_limit_and_consumer_keep_native_clamps_and_transmission_conditions(
        packages, native, private_sources, key, enabled, automatic, demand, gear):
    base = native["MS43"](private_sources["stock"], experimental=True)
    expected = first_gear(base, (0x90584, 0xB2A, 0x9AC, 0xFD9C, 0x81AC, 0x8023),
                          enabled, automatic, demand, gear)
    actual = first_gear(checked_native(packages[key], native), FIRST_GEAR[key], enabled, automatic, demand, gear)
    if key == "430037":
        # Its original unsigned divider truncates; later owners round using
        # the remainder. Keep that actual revision difference, including clamps.
        computed, publication = actual
        assert computed == (expected[0][0], expected[0][0] * 8192 // 65535)
        assert publication == ((computed[1], computed[1], 4)
                               if enabled and not automatic and gear == 1 else (1100, 1100, 7))
    else:
        assert actual == expected


@pytest.mark.parametrize("key", TRACTION)
@pytest.mark.parametrize("inhibit", (None, "setting", "source", "flag", "request", "limit", "coolant", "existing_cut"))
def test_cylinder_cut_selector_keeps_each_original_intervention_condition(packages, native, key, inhibit):
    source = packages[key]
    entry, offset, origin, previous, coolant, threshold = TRACTION[key]
    emu = checked_native(source, native)
    emu.mem.image[0x70000 + offset] = int(inhibit != "setting")
    emu.write_byte(origin, 4 if inhibit == "source" else 1)
    emu.write(0xFD08, 0x2000 if inhibit == "flag" else 0)
    emu.reg.set_word(6, 2 if inhibit == "request" else 1)
    emu.write(0xE0B4, 0xFFFF if inhibit == "limit" else 1000)
    emu.mem.image[0x70000 + threshold] = 100
    emu.write_byte(coolant, 0 if inhibit == "coolant" else 255)
    emu.write_byte(previous, int(inhibit == "existing_cut"))
    emu.write(0xE0B8, 0x4000)
    emu.write_byte(origin + 1, 5)
    seen = set()
    def trace(pc, opcode):
        assert all(at in source.memory for at in range(pc, pc + 4)), hex(pc)
        seen.add(pc)
    emu.cpu.set_trace(trace)
    assert emu.run_from(entry, stop_at=entry + 70, max_steps=500).exit_reason == "stop_at"
    assert (entry + 48 in seen) == (inhibit is None), ",".join(hex(pc-entry) for pc in sorted(seen))
    assert emu.read_byte(origin) == (4 if inhibit == "source" else 1)
    if inhibit:
        assert emu.read_byte(origin + 1) == 0


def cam_counter(emu, layout, enabled, ready, phase, angle, count):
    entry, offset, ready_bit, phase_word, phase_bit, counter = layout
    emu.mem.image[0x70000 + offset] = int(enabled)
    emu.write(0xFD28, int(ready) << ready_bit)
    emu.write(phase_word, int(phase) << phase_bit)
    emu.write_byte(0xFBDC, angle)
    emu.write_byte(counter, count)
    emu.write_byte(counter - 1, 0)
    assert emu.call(entry, max_steps=100).exit_reason == "stop_at"
    return emu.read_byte(counter), emu.read_byte(counter - 1), bool(emu.read(0xFD28) & (1 << ready_bit))


@pytest.mark.parametrize("key", CAM)
@pytest.mark.parametrize("enabled,ready", ((False, False), (False, True), (True, False), (True, True)))
@pytest.mark.parametrize("phase", (False, True))
@pytest.mark.parametrize("angle", (0, 0x1F, 0x2E, 0x40, 0x5B, 0x6A, 0xFF))
@pytest.mark.parametrize("count", (0, 3))
def test_cam_assisted_rotation_counter_keeps_native_phase_windows_and_ready_state(
        packages, native, private_sources, key, enabled, ready, phase, angle, count):
    base = native["MS43"](private_sources["stock"], experimental=True)
    expected = cam_counter(base, (0xA4650, 0xE08, 5, 0xFD7A, 7, 0x8156),
                           enabled, ready, phase, angle, count)
    actual = cam_counter(checked_native(packages[key], native), CAM[key], enabled, ready, phase, angle, count)
    assert actual == expected


@pytest.mark.parametrize("key", DASHPOT)
@pytest.mark.parametrize("cancel,clutch", ((False, False), (False, True), (True, False), (True, True)))
@pytest.mark.parametrize("overrun", (0, 1, 2))
@pytest.mark.parametrize("ramp_active", (False, True))
def test_dashpot_choice_keeps_clutch_and_both_overrun_conditions(packages, native, key, cancel, clutch, overrun, ramp_active):
    entry, offset = DASHPOT[key]
    emu = checked_native(packages[key], native)
    code = bytes(packages[key].memory[at] for at in range(entry, entry + 48))
    assert code[:6] == b"\xf3\xf8" + offset.to_bytes(2, "little") + b"\x2d\x06"
    assert code[6] == 0x9A and code[10] == code[14] == 0x8A
    emu.mem.image[0x70000 + offset] = int(cancel)
    for at, enabled in ((6, clutch), (10, overrun == 1), (14, overrun == 2)):
        word = 0xFD00 + code[at + 1] * 2
        mask = 1 << (code[at + 3] >> 4)
        emu.write(word, emu.read(word) | (mask if enabled else 0))
    ramp, target, saved, timer = (int.from_bytes(code[at:at + 2], "little") for at in (20, 28, 32, 46))
    emu.write(ramp, 0xFFFF if ramp_active else 0)
    emu.write(target, 512)
    emu.write(saved, 256)
    emu.write(timer, 1234)
    assert emu.run_from(entry, stop_at=entry + 48, max_steps=20).exit_reason == "stop_at"
    settles = not ramp_active or (cancel and clutch and overrun)
    assert (emu.read(saved), emu.read(timer)) == ((512, 0) if settles else (256, 1234))


def ambient_inhibit(emu, entry, offset, output_bit, disabled, condition):
    code = bytes(emu.mem.image[(entry & 0x7FFFF):(entry & 0x7FFFF) + 44])
    assert code[:6] == b"\xf3\xf8" + offset.to_bytes(2, "little") + b"\x3d\x12"
    emu.mem.image[0x70000 + offset] = int(disabled)
    for index, at in enumerate(range(6, 42, 4)):
        assert code[at] == (0x9A if at == 38 else 0x8A)
        if condition == index:
            word, mask = 0xFD00 + code[at + 1] * 2, 1 << (code[at + 3] >> 4)
            emu.write(word, emu.read(word) | mask)
    emu.write(0xFD6A, 3)
    assert emu.run_from(entry, stop_at=entry + 44, max_steps=15).exit_reason == "stop_at"
    assert emu.read(0xFD6A) & ~(1 << output_bit) == 3
    return bool(emu.read(0xFD6A) & (1 << output_bit))


@pytest.mark.parametrize("key", AMBIENT)
@pytest.mark.parametrize("disabled", (False, True))
@pytest.mark.parametrize("condition", range(-1, 9))
def test_ambient_diagnosis_selector_preserves_native_inhibits(packages, native, private_sources, key, disabled, condition):
    base = native["MS43"](private_sources["stock"], experimental=True)
    expected = ambient_inhibit(base, 0xBF78E, 0xE03, 10, disabled, condition)
    actual = ambient_inhibit(checked_native(packages[key], native), *AMBIENT[key], disabled, condition)
    assert actual == expected
    if disabled:
        assert actual


@pytest.mark.parametrize("key,entry", (("430066", 0xC0F28), ("430066-later", 0xC0F3A)))
@pytest.mark.parametrize("bank", (0, 1))
@pytest.mark.parametrize("enabled", (False, True))
def test_066_postcat_readiness_selector_has_its_own_nonadjacent_calibration_byte(packages, native, key, entry, bank, enabled):
    entry += 42 * bank
    mask = 1 << (4 + bank)
    expected = (bytes.fromhex("f3f8260e2d04e6f4") + (0xFFFF ^ mask).to_bytes(2, "little") +
                bytes.fromhex("64f42691"))
    assert bytes(packages[key].memory[at] for at in range(entry, entry + 14)) == expected
    emu = checked_native(packages[key], native)
    emu.mem.image[0x70E26] = int(enabled)
    emu.write(0x9126, 0xFFFF)
    assert emu.run_from(entry, stop_at=entry + 14, max_steps=6).exit_reason == "stop_at"
    assert emu.read(0x9126) == (0xFFFF ^ mask if enabled else 0xFFFF)


@pytest.mark.parametrize("key", SIGNAL_DELAY)
@pytest.mark.parametrize("bank", (0, 1))
@pytest.mark.parametrize("enabled,ready", ((False, False), (False, True), (True, False), (True, True)))
def test_postcat_delay_setting_reaches_both_native_diagnostic_owners_only_when_ready(
        packages, native, key, bank, enabled, ready):
    source = packages[key]
    entries, offset = SIGNAL_DELAY[key]
    entry = entries[bank]
    code = bytes(source.memory[at] for at in range(entry, entry + 26))
    assert code[:4] == b"\xf3\xf8" + offset.to_bytes(2, "little")
    if code[4:6] == b"\xea\x20":
        disabled_target = 0xD0000 + int.from_bytes(code[6:8], "little")
        condition_at = 8
    else:
        assert code[4] == 0x2D
        disabled_target = entry + 6 + code[5] * 2
        condition_at = 6
    assert code[condition_at] == 0x9A
    assert code[condition_at + 4:condition_at + 6] == b"\xe6\xfc"
    assert code[condition_at + 8:condition_at + 10] == b"\xe6\xfd"
    assert code[condition_at + 12:condition_at + 14] == b"\xda\x0a"
    not_ready = entry + condition_at + 4 + code[condition_at + 2] * 2
    owner = 0xA0000 + int.from_bytes(code[condition_at + 14:condition_at + 16], "little")
    returned = entry + condition_at + 16
    emu = checked_native(source, native)
    emu.mem.image[0x70000 + offset] = int(enabled)
    emu.write(0xFD00 + code[condition_at + 1] * 2,
              (1 << (code[condition_at + 3] >> 4)) if ready else 0)
    seen = set()
    def trace(pc, opcode):
        assert all(at in source.memory for at in range(pc, pc + 4)), hex(pc)
        seen.add(pc)
    emu.cpu.set_trace(trace)
    assert emu.run_from(entry, stop_at=(disabled_target, not_ready, returned), max_steps=2000).exit_reason == "stop_at"
    assert (owner in seen) == (enabled and ready)
    assert emu.cpu.pc == (disabled_target if not enabled else returned if ready else not_ready)


@pytest.mark.parametrize("key", FLAP)
@pytest.mark.parametrize("transmission", (0, 1))
@pytest.mark.parametrize("heating,value,override", [(h, v, o) for h in (False, True) for v in (0, 1) for o in (False, True)])
def test_earlier_flap_request_and_output_preserve_heating_and_diagnostic_overrides(
        packages, native, key, transmission, heating, value, override):
    entries, offsets, speed, threshold, heat_word, heat_bit, request, override_word = FLAP[key]
    emu = checked_native(packages[key], native)
    for offset in offsets:
        emu.mem.image[0x70000 + offset] = 1 - value
    emu.mem.image[0x70000 + offsets[2 if heating else transmission]] = value
    emu.mem.image[0x70000 + threshold + transmission] = 10
    emu.write_byte(speed, 0)
    emu.write(heat_word, (1 << heat_bit) if heating else 0)
    emu.write(override_word, 0x80 if override else 0)
    original = (0xA55A & ~0x40) | ((1 - value) << 6)
    emu.write(request, original)
    emu.write(request - 2, original)
    assert emu.call(entries[transmission], max_steps=25).exit_reason == "stop_at"
    expected = (original & ~0x40) | (value << 6)
    assert emu.read(request) == expected
    assert emu.read(request - 2) == (original if override else expected)


@pytest.mark.parametrize("key", FLAP)
@pytest.mark.parametrize("transmission", (0, 1))
def test_earlier_flap_low_speed_choices_leave_the_normal_speed_path(packages, native, key, transmission):
    entries, offsets, speed, threshold, heat_word, heat_bit, request, override_word = FLAP[key]
    emu = checked_native(packages[key], native)
    emu.mem.image[0x70000 + threshold + transmission] = 10
    emu.write_byte(speed, 11)
    emu.write(request, 0xA55A)
    emu.write(request - 2, 0xA55A)
    end = entries[transmission] + (82 if transmission == 0 else 66)
    assert emu.run_from(entries[transmission], stop_at=end, max_steps=4).exit_reason == "stop_at"
    assert emu.read(request) == emu.read(request - 2) == 0xA55A


@pytest.mark.parametrize("key", BRAKE)
@pytest.mark.parametrize("polarity,test_pin,brake_pin", [(p, t, b) for p in (0, 1) for t in (0, 1) for b in (0, 1)])
def test_brake_test_polarity_only_inverts_the_test_input_in_the_full_native_handler(
        packages, native, key, polarity, test_pin, brake_pin):
    entry, offset = BRAKE[key]
    source = packages[key]
    code = bytes(source.memory[at] for at in range(entry, entry + 24))
    assert code[:4] == b"\xc2\xf4" + offset.to_bytes(2, "little")
    assert code[4:6] == b"\xf6\xf4" and code[8] == 0x4A
    assert code[12] == 0x2A and code[15] & 15 == 5
    assert code[16:18] == b"\x4a\x88" and code[19] >> 4 == 0
    assert code[20] == 0x4A and code[21] == code[14] and code[23] >> 4 == 1
    emu = checked_native(source, native)
    emu.mem.image[0x70000 + offset] = polarity
    emu.write(0xFD00 + code[14] * 2, (test_pin << 5) | (brake_pin << 1))
    assert emu.call(entry, max_steps=25).exit_reason == "stop_at"
    assert emu.read(int.from_bytes(code[6:8], "little")) == polarity
    test_status = emu.read(0xFD00 + code[18] * 2) >> (code[19] & 15) & 1
    brake_status = emu.read(0xFD00 + code[22] * 2) >> (code[23] & 15) & 1
    assert test_status == (test_pin ^ polarity)
    assert brake_status == brake_pin


@pytest.mark.parametrize("key", TANK)
@pytest.mark.parametrize("enabled,dmtl", [(e, d) for e in (0, 1) for d in (0, 1)])
@pytest.mark.parametrize("inhibited", (None, 0, 1, 2, 3))
def test_tank_level_actual_runtime_selector_and_native_admission_conditions(packages, native, key, enabled, dmtl, inhibited):
    entry, dmtl_offset, offset = TANK[key]
    source = packages[key]
    code = bytes(source.memory[at] for at in range(entry, entry + 84))
    assert code[:8] == b"\xf3\xf8" + dmtl_offset.to_bytes(2, "little") + b"\x49\x81\x3d\x2b"
    assert code[8:14] == b"\xf3\xfa" + offset.to_bytes(2, "little") + b"\x2d\x28"
    assert code[62:64] == b"\xda\x0a"
    owner = 0xA0000 + int.from_bytes(code[64:66], "little")
    emu = checked_native(source, native)
    emu.mem.image[0x70000 + dmtl_offset] = dmtl
    emu.mem.image[0x70000 + offset] = enabled
    for index, at in enumerate((14, 18, 22, 26)):
        assert code[at] == (0x9A if index in (0, 3) else 0x8A)
        address, bit = 0xFD00 + code[at + 1] * 2, code[at + 3] >> 4
        set_bit = (index in (0, 3)) ^ (inhibited == index)
        emu.write(address, (emu.read(address) & ~(1 << bit)) | (int(set_bit) << bit))
    seen = set()
    def trace(pc, opcode):
        assert all(at in source.memory for at in range(pc, pc + 4)), hex(pc)
        seen.add(pc)
    emu.cpu.set_trace(trace)
    assert emu.call(entry, max_steps=2000).exit_reason == "stop_at"
    assert (owner in seen) == bool(enabled and dmtl and inhibited is None)
