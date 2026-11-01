"""Execute revision-specific configuration selectors from complete DATEN owners.

Sparse sources remain bounded function fixtures, not substitute full ROMs.
All calibration addresses, selector states and relocated flags are explicit.
"""

import pytest

import ms42_ms43_config as config
from tests.test_ms42_ms43_config_daten import packages as packages, native as native
from tests.test_ms42_ms43_config_revisions import private_sources as private_sources


# Four converter states: open, controlled, closed, default. Entries include the
# whole call-free state selection, not just the calibration-load instruction.
CONVERTERS = {
    "0110CA": (0x138AC, 0xC376, 0xFD10, 14, (0x319, 0x31B, 0x31C, 0x31A)),
    "430037": (0x9D580, 0x80DB, 0xFD1E, 12, (0xCE8, 0xCEA, 0xCEB, 0xCE9)),
    "430055": (0xD4666, 0x80FC, 0xFD1E, 10, (0xD81, 0xD83, 0xD84, 0xD82)),
    "430064": (0xD4670, 0x80FC, 0xFD1E, 10, (0xDA6, 0xDA8, 0xDA9, 0xDA7)),
    "430065": (0xD4670, 0x80FC, 0xFD1E, 11, (0xE0F, 0xE11, 0xE12, 0xE10)),
    "430066": (0xD4670, 0x80FC, 0xFD1E, 7, (0xE10, 0xE12, 0xE13, 0xE11)),
    "430066-later": (0xD4670, 0x80FC, 0xFD1E, 7, (0xE10, 0xE12, 0xE13, 0xE11)),
}
# The unoffered stationary-clutch setting (state2) is included in the native
# owner comparison and left unchanged by every edit.
CLUTCHES = {
    "430055": (0xD4954, 10, (0xBE6, 0xBE4, 0xBE7, 0xBE3, 0xBE5)),
    "430064": (0xD4968, 10, (0xC03, 0xC01, 0xC04, 0xC00, 0xC02)),
    "430065": (0xD4968, 11, (0xC57, 0xC55, 0xC58, 0xC54, 0xC56)),
    "430066": (0xD4968, 7, (0xC59, 0xC57, 0xC5A, 0xC56, 0xC58)),
    "430066-later": (0xD4968, 7, (0xC59, 0xC57, 0xC5A, 0xC56, 0xC58)),
}
EQUIPMENT = {
    "430037": (0x9D044, (0xA3D, 0xA3E, 0xA47, 0xA38), (7, 6, 9, 10)),
    "430064": (0x9D4F0, (0xAD1, 0xAD2, 0xADB, 0xACC), (5, 4, 7, 8)),
    "430065": (0x9DB6C, (0xB23, 0xB24, 0xB2D, 0xB1E), (6, 5, 8, 9)),
}
BULK = {"430037": (0xA5948, 0xCD0, 0xA5D3C), "430055": (0xA4A64, 0xD68, 0xA4E58),
        "430064": (0xA4B94, 0xD8B, 0xA4F88), "430065": (0xA52AE, 0xDF2, 0xA56A2),
        "430066": (0xA52AE, 0xDF3, 0xA56A2), "430066-later": (0xA52AE, 0xDF3, 0xA56A2)}
PEDAL = {"430037": (0x92972, 0xCE1, 10), "430055": (0x92AF0, 0xD7A, 13),
         "430064": (0x92B00, 0xD9E, 13), "430065": (0x92B00, 0xE07, 13),
         "430066": (0x92B00, 0xE08, 9), "430066-later": (0x92B00, 0xE08, 9)}
MONITOR = {"430037": (0xD6508, 0xCE3, 0xE642, 0xE650, 0xFD76, 11),
           "430055": (0xD93E8, 0xD7C, 0xE668, 0xE676, 0xFD78, 13),
           "430064": (0xDDA1E, 0xDA0, 0xE672, 0xE680, 0xFD7A, 14),
           "430065": (0xDF1C6, 0xE09, 0xE672, 0xE680, 0xFD7E, 10),
           "430066": (0xDFD1A, 0xE0A, 0xE672, 0xE680, 0xFD7E, 6),
           "430066-later": (0xDFD32, 0xE0A, 0xE672, 0xE680, 0xFD7E, 6)}


def code(source, start, size):
    assert all(at in source.memory for at in range(start, start + size))
    return bytes(source.memory[at] for at in range(start, start + size))


def edited_native(packages, native, key, offset, value):
    source = packages[key]
    feature = next(f for f in config.features_for(source.family, key.split("-")[0])
                   if f.byte == offset)
    label = next(label for label, number in feature.options if number == value)
    image = config.apply_config(source.image, {feature.name: label})
    base = 0x48000 if source.family == "MS42" else 0x70000
    expected = bytearray(source.image)
    expected[base + offset] = value
    assert image == expected
    return native[source.family](image, experimental=True)


def expected_selector(state_address, word, bit, offsets):
    count = len(offsets)
    prefix = bytearray.fromhex("f3f800002d0549812d0a49822d0f0d15" if count == 4 else
                               "f3f800002d0749812d0c49822d1149832d160d1c")
    prefix[2:4] = state_address.to_bytes(2, "little")
    body = bytearray()
    for index, offset in enumerate(offsets):
        remaining = count - index - 1
        body.extend(b"\xf3\xf8" + offset.to_bytes(2, "little") + b"\x2d\x02")
        body.extend(((bit << 4) | 15, (word - 0xFD00) // 2, 0x0D, remaining * 7 + 1))
        body.extend(((bit << 4) | 14, (word - 0xFD00) // 2))
        if remaining:
            body.extend((0x0D, remaining * 7 - 1))
    return bytes(prefix + body)


@pytest.mark.parametrize("key", CONVERTERS)
@pytest.mark.parametrize("state", (0, 1, 2, 3, 255))
@pytest.mark.parametrize("value", (0, 1))
def test_converter_settings_execute_whole_native_selector(packages, native, key, state, value):
    entry, state_address, word, bit, offsets = CONVERTERS[key]
    expected = expected_selector(state_address, word, bit, offsets)
    assert code(packages[key], entry, len(expected)) == expected
    selected = offsets[min(state, 3)]
    emu = edited_native(packages, native, key, selected, value)
    emu.write_byte(state_address, state)
    emu.write(word, 0xA55A)
    result = emu.run_from(entry, stop_at=entry + len(expected), max_steps=20)
    assert result.exit_reason == "stop_at"
    assert emu.read(word) == (0xA55A & ~(1 << bit)) | (value << bit)


@pytest.mark.parametrize("key", CLUTCHES)
@pytest.mark.parametrize("state", (0, 1, 3, 4, 255))
@pytest.mark.parametrize("value", (0, 1))
def test_automated_manual_settings_execute_whole_native_selector(packages, native, key, state, value):
    entry, bit, offsets = CLUTCHES[key]
    expected = expected_selector(0xFB57, 0xFD1E, bit, offsets)
    assert code(packages[key], entry, len(expected)) == expected
    emu = edited_native(packages, native, key, offsets[min(state, 4)], value)
    emu.write_byte(0xFB57, state)
    emu.write(0xFD1E, 0xA55A)
    result = emu.run_from(entry, stop_at=entry + len(expected), max_steps=25)
    assert result.exit_reason == "stop_at"
    assert emu.read(0xFD1E) == (0xA55A & ~(1 << bit)) | (value << bit)


@pytest.mark.parametrize("key", EQUIPMENT)
@pytest.mark.parametrize("index", range(4))
@pytest.mark.parametrize("value", (0, 1))
def test_earlier_equipment_fields_have_the_reviewed_native_flag_effect(packages, native, key, index, value):
    start, offsets, bits = EQUIPMENT[key]
    entry, offset, bit = start + index * 12, offsets[index], bits[index]
    expected = b"\xf3\xf8" + offset.to_bytes(2, "little") + bytes((
        0x3D, 2, (bit << 4) | 14, 0x0E, 0x0D, 1, (bit << 4) | 15, 0x0E))
    assert code(packages[key], entry, len(expected)) == expected
    emu = edited_native(packages, native, key, offset, value)
    emu.write(0xFD1C, 0xA55A)
    result = emu.run_from(entry, stop_at=entry + 12, max_steps=5)
    assert result.exit_reason == "stop_at"
    assert emu.read(0xFD1C) == (0xA55A & ~(1 << bit)) | (value << bit)


@pytest.mark.parametrize("key,entry,offset,output", [
    ("0110CA", 0x731F6, 0x24E, 0xE5A7),
    ("430037", 0x94FBC, 0xA3F, 0xE5D5),
    ("430055", 0x9513A, 0xABF, 0xE5FB),
])
@pytest.mark.parametrize("value", (0, 1))
def test_relocated_obd_initializer_executes_complete_native_routine(packages, native, key, entry, offset, output, value):
    expected = bytearray.fromhex(
        "f3f81f0b49813d05e7fa8000f7fa05e60d02f78e05e6f78e06e6f78e07e6"
        "e118f7f808e6e1a8f7f80ce6db00")
    expected[2:4] = offset.to_bytes(2, "little")
    for at, delta in ((14, 0), (20, 0), (24, 1), (28, 2), (34, 3), (40, 7)):
        expected[at:at + 2] = (output + delta).to_bytes(2, "little")
    assert code(packages[key], entry, len(expected)) == expected
    emu = edited_native(packages, native, key, offset, value)
    result = emu.call(entry, max_steps=20)
    assert result.exit_reason == "stop_at"
    assert emu.read_byte(output) == (0x80 if value else 0)
    assert bytes(emu.read_byte(output + at) for at in (1, 2, 3, 7)) == b"\x00\x00\x01\x0a"


def test_ca_lambda_owners_only_relocate_the_published_state_and_flags(packages, private_sources):
    for start, end, changes in (
        (0x133FA, 0x134AC, {0x1342E: 0x26, 0x1343A: 0x26}),
        (0x1324C, 0x133FA, {0x1324F: 0xA0, 0x13253: 0xC0, 0x132BE: 0x26,
                           0x132CA: 0x26, 0x132E4: 0x26, 0x132F2: 0x26}),
        (0x1D5B0, 0x1D6DE, {0x1D5B2: 0x50}),
    ):
        expected = bytearray(private_sources["c6"][start:end])
        for address, value in changes.items():
            expected[address - start] = value
        assert code(packages["0110CA"], start, end - start) == expected


@pytest.mark.parametrize("mode", range(4))
@pytest.mark.parametrize("elapsed", (0, 65535))
def test_ca_lambda_selection_matches_the_original_semantics(packages, private_sources, native, mode, elapsed):
    actual = edited_native(packages, native, "0110CA", 0x287, mode)
    reference = native["MS42"](private_sources["c6"], experimental=True)
    reference.mem.image[0x48287] = mode
    results = []
    for emu, state in ((reference, 0xE516), (actual, 0xE526)):
        emu.write(0xF94A, elapsed)
        emu.write(0xF94C, 0)
        result = emu.run_from(0x133FA, stop_at=0x13440, max_steps=500)
        assert result.exit_reason == "stop_at"
        results.append((emu.read_byte(state), emu.read(0xFD04), emu.read(0xFD42)))
    assert results[0] == results[1]


@pytest.mark.parametrize("value", (0, 1))
def test_ca_preinjection_flag_uses_its_relocated_bit(packages, native, value):
    assert code(packages["0110CA"], 0x74AD4, 12) == bytes.fromhex("f3f890022d024f180d014e18")
    emu = edited_native(packages, native, "0110CA", 0x290, value)
    emu.write(0xFD30, 0xA55A)
    result = emu.run_from(0x74AD4, stop_at=0x74AE0, max_steps=5)
    assert result.exit_reason == "stop_at"
    assert emu.read(0xFD30) == (0xA55A & ~0x10) | (value << 4)


@pytest.mark.parametrize("value", (0, 1))
@pytest.mark.parametrize("override", (False, True))
def test_ca_flap_preserves_native_diagnostic_override(packages, native, value, override):
    emu = edited_native(packages, native, "0110CA", 0x16D, value)
    emu.mem.image[0x483FC] = 10
    emu.write_byte(0xC442, 0)
    emu.write(0xFD78, 4 if override else 0)
    emu.write(0xFDA8, 0xA55A)
    emu.write(0xFDA6, 0xA55A)
    result = emu.run_from(0x1D68A, stop_at=(0x1D6A2, 0x1D6AC, 0x1D6DC), max_steps=20)
    assert result.exit_reason == "stop_at"
    expected = (0xA55A & ~0x4000) | (value << 14)
    assert emu.read(0xFDA8) == expected
    assert emu.read(0xFDA6) == (0xA55A if override else expected)


@pytest.mark.parametrize("key", ("430065", "430066", "430066-later"))
def test_later_flap_owners_change_only_explicit_inputs(packages, private_sources, key):
    if key == "430065":
        changes = {0xA9896: 0x7C, 0xA989A: 0xE6, 0xA98A1: 0xB0,
                   0xA98A4: 0x53, 0xA98C6: 0x59,
                   0xA9990: 0x7C, 0xA9994: 0xE7, 0xA999B: 0xB0,
                   0xA999E: 0x53, 0xA99B8: 0x5A, 0xA99EA: 0x4E}
    else:
        changes = {0xA9896: 0x7E, 0xA989A: 0xE7, 0xA98A4: 0x55, 0xA98C6: 0x5B,
                   0xA9990: 0x7E, 0xA9994: 0xE8, 0xA999E: 0x55,
                   0xA99B8: 0x5C, 0xA99EA: 0x50}
    for first, end in ((0xA9894, 0xA98E6), (0xA998E, 0xA9A00)):
        expected = bytearray(private_sources["stock"][first - 0x80000:end - 0x80000])
        for address, value in changes.items():
            if first <= address < end:
                expected[address - first] = value
        assert code(packages[key], first, end - first) == expected


@pytest.mark.parametrize("key", ("430065", "430066", "430066-later"))
@pytest.mark.parametrize("automatic", (False, True))
@pytest.mark.parametrize("heating", (False, True))
@pytest.mark.parametrize("override", (False, True))
@pytest.mark.parametrize("value", (0, 1))
def test_later_flap_modes_use_actual_speed_heating_and_override_inputs(
        packages, native, key, automatic, heating, override, value):
    if key == "430065":
        manual, auto, hot, speed, heat_bit, minimum = 0xC5A, 0xC59, 0xC53, 0x847C, 11, 0xDE6
    else:
        manual, auto, hot, speed, heat_bit, minimum = 0xC5C, 0xC5B, 0xC55, 0x847E, 7, 0xDE7
    selected = hot if heating else auto if automatic else manual
    emu = edited_native(packages, native, key, selected, value)
    for other in (manual, auto, hot):
        if other != selected:
            emu.mem.image[0x70000 + other] = 1 - value
    emu.mem.image[0x70000 + minimum + (not automatic)] = 10
    emu.write_byte(speed, 0)
    emu.write(0xFD38, (1 << heat_bit) if heating else 0)
    emu.write(0xFD8C, 0x80 if override else 0)
    emu.write(0xFDCE, 0xA55A)
    emu.write(0xFDCC, 0xA55A)
    result = emu.run_from(0xA9894 if automatic else 0xA998E, stop_at=(
        0xA98B4, 0xA98C2, 0xA98D6, 0xA98E4,
        0xA99AA, 0xA99B4, 0xA99C4, 0xA99CE, 0xA99FE), max_steps=25)
    assert result.exit_reason == "stop_at"
    expected = (0xA55A & ~0x40) | (value << 6)
    assert emu.read(0xFDCE) == expected
    assert emu.read(0xFDCC) == (0xA55A if override else expected)


@pytest.mark.parametrize("key", ("430065", "430066", "430066-later"))
@pytest.mark.parametrize("automatic", (False, True))
def test_later_flap_settings_leave_the_normal_speed_path(packages, native, key, automatic):
    emu = native["MS43"](packages[key].image, experimental=True)
    speed, minimum = (0x847C, 0xDE6) if key == "430065" else (0x847E, 0xDE7)
    emu.mem.image[0x70000 + minimum + (not automatic)] = 10
    emu.write_byte(speed, 10)
    normal = 0xA98E6 if automatic else 0xA99D0
    result = emu.run_from(0xA9894 if automatic else 0xA998E, stop_at=normal, max_steps=5)
    assert result.exit_reason == "stop_at"


@pytest.mark.parametrize("key", BULK)
@pytest.mark.parametrize("value", (0, 1))
@pytest.mark.parametrize("readiness", ((False, False), (False, True), (True, False), (True, True)))
def test_native_bulk_adaptation_setting_preserves_both_firmware_prerequisites(
        packages, native, key, value, readiness):
    entry, offset, rejected = BULK[key]
    body = code(packages[key], entry, 24)
    assert body[:8] == b"\xf3\xf8" + offset.to_bytes(2, "little") + b"\xea\x30" + (rejected & 0xFFFF).to_bytes(2, "little")
    assert body[8] == body[16] == 0x8A and body[10] == body[18] == 2
    emu = edited_native(packages, native, key, offset, value)
    for at, ready in zip((8, 16), readiness):
        emu.write(0xFD00 + body[at + 1] * 2, int(ready) << (body[at + 3] >> 4))
    result = emu.run_from(entry, stop_at=(entry + 24, rejected), max_steps=12)
    assert result.exit_reason == "stop_at"
    assert emu.cpu.pc == (entry + 24 if value == 0 and all(readiness) else rejected)


@pytest.mark.parametrize("key", PEDAL)
@pytest.mark.parametrize("value,delta", ((0, 0x68), (1, 0x4A), (2, 0x18)))
def test_each_original_pedal_brake_mode_uses_its_actual_intervention_flag(packages, native, key, value, delta):
    entry, offset, bit = PEDAL[key]
    body = code(packages[key], entry, 0x68)
    assert body[:4] == b"\xf3\xf6" + offset.to_bytes(2, "little")
    assert body[18:20] == bytes(((bit << 4) | 15, 7))
    emu = edited_native(packages, native, key, offset, value)
    emu.write_byte(0xFB46, 255)
    result = emu.run_from(entry, stop_at=tuple(entry + end for end in (0x68, 0x4A, 0x18)), max_steps=20)
    assert result.exit_reason == "stop_at" and emu.cpu.pc == entry + delta
    assert bool(emu.read(0xFD0E) & (1 << bit)) == (value != 0)


@pytest.mark.parametrize("key", MONITOR)
@pytest.mark.parametrize("value,program_bad,calibration_bad,fault", (
    (1, False, False, False), (1, False, True, True), (0, False, True, True),
    (0xA5, False, True, False), (0xA5, True, False, True), (0xA5, True, True, True)))
def test_revision_monitor_bypass_never_hides_program_mismatch(
        packages, native, key, value, program_bad, calibration_bad, fault):
    entry, offset, program_acc, cal_acc, flag_word, flag_bit = MONITOR[key]
    body = code(packages[key], entry, 88)
    assert body[16:20] == b"\xf2\xf4" + cal_acc.to_bytes(2, "little")
    assert body[32:36] == b"\xf2\xf4" + program_acc.to_bytes(2, "little")
    assert body[68:72] == b"\xf3\xf8" + offset.to_bytes(2, "little")
    assert body[78:80] == bytes(((flag_bit << 4) | 15, (flag_word - 0xFD00) // 2))
    source = packages[key].image
    if value == 0:
        emu = native["MS43"](source, experimental=True)
        emu.mem.image[0x70000 + offset] = value  # Unoffered value must still report a mismatch.
    else:
        emu = edited_native(packages, native, key, offset, value)
    program_sum = int.from_bytes(source[0x6FDAE:0x6FDB2], "little") ^ int(program_bad)
    cal_sum = int.from_bytes(source[0x72FFC:0x73000], "little") ^ int(calibration_bad)
    emu.write_byte(0xE000, 2)
    emu.write_byte(0xE002, 2)
    for address, total in ((program_acc, program_sum), (cal_acc, cal_sum)):
        emu.write(address, total & 0xFFFF)
        emu.write(address + 2, total >> 16)
    result = emu.run_from(entry, stop_at=(entry + 82, entry + 86), max_steps=40)
    assert result.exit_reason == "stop_at" and emu.cpu.pc == entry + (82 if fault else 86)
    assert bool(emu.read(flag_word) & (1 << flag_bit)) is fault
    if fault:
        assert emu.reg.get_word(12) == 8
