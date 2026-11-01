"""Installed revision settings, executed with original C167 instructions."""

import os
from pathlib import Path

import pytest

import ms42_ms43_config_patches as config
from tests.ms42_ms43_revision_sources import composed_image, full_image
from tests.test_ms42_ms43_config_expansion import MONITOR


# Program file offsets. Native MS43 execution adds 0x80000.
LAYOUTS = {
    "430037": (0x31C5A, 0x339FE, 0x58934, 0xCE3),
    "430055": (0x32040, 0x33FB8, 0x5B818, 0xD7C),
    "430064": (0x321CA, 0x34142, 0x5FE4E, 0xDA0),
    "430065": (0x30000, 0x31F78, 0x616D0, 0xE09),
    "430066": (0x2DD64, 0x2FCDC, 0x63530, 0xE0A),
    "430066-later": (0x2DD64, 0x2FCDC, 0x63530, 0xE0A),
}


def source(key):
    return full_image(key) if key == "430055" else composed_image(key, "430069").image


def immo_image(key):
    image = bytearray(source(key))
    entry, helper, _, _ = LAYOUTS[key]
    success = image[entry + 0x4E0:entry + 0x4E6]
    assert success[0] & 15 == 14
    assert success[2:] == b"\xDA" + bytes((8 + (helper >> 16),)) + (helper & 65535).to_bytes(2, "little")
    image[entry:entry + 8] = success + b"\xDB\0"
    operands = image[entry + 0x584:entry + 0x58C]
    assert operands[:2] == b"\xC2\xFE" and operands[4:6] == b"\xC2\xFF"
    assert operands[2:4] == operands[6:8]
    threshold = 0x70000 + int.from_bytes(operands[2:4], "little")
    assert image[threshold] == 10
    normal = image[entry + 0x3C:entry + 0x50]
    assert normal[:8] == image[entry + 0x57C:entry + 0x584]
    assert normal[8:10] == b"\xC2\xFE" and normal[12:14] == b"\xC2\xFF"
    increment = int.from_bytes(normal[10:12], "little")
    assert image[0x70000 + increment] == 2
    image[entry + 0x586:entry + 0x588] = increment.to_bytes(2, "little")
    image[0x70000 + increment] = 0
    installed = config.apply_config(source(key), {"Immobilizer bypass": "Enabled"})
    assert installed == image
    return installed


def checksum_image(key):
    image = bytearray(source(key))
    _, _, entry, monitor = LAYOUTS[key]
    assert image[0x73FFE:0x74000] == b"\0\0"
    assert image[entry + 0x65] == (0x94 if key == "430065" else 0x84)
    assert image[0x6FFB0] == (0xA8 if key == "430065" else 0)
    assert image[0x70000 + monitor] == 1
    image[0x73FFE:0x74000] = b"\xFF\xFF"
    image[entry + 0x65] = 0x94
    image[0x6FFB0] = 0xA8
    image[0x70000 + monitor] = 0xA5
    installed = config.apply_config(source(key), {"Calibration checksum bypass": "Enabled"})
    assert installed == image
    return installed


@pytest.fixture
def native(monkeypatch):
    root = Path(os.environ.get("MS41EMU_ROOT", Path.home() / "ECU Emulator"))
    monkeypatch.syspath_prepend(str(root))
    from ms41emu.ms43 import MS43
    return lambda image: MS43(image, experimental=True)


@pytest.mark.parametrize("key", LAYOUTS)
def test_checksum_runtime_owner_is_exact_original_code(key):
    entry = LAYOUTS[key][2]
    actual = source(key)[entry:entry + 0x74]
    expected = bytearray(full_image("430069")[0x63530:0x635A4])
    if key == "430065":
        expected[0x65] = 0x94
    assert actual == expected


@pytest.mark.parametrize("key", LAYOUTS)
@pytest.mark.parametrize("seed", (0, 0xFFFF, 0xA55A, 0x5AA5))
def test_in_place_immobilizer_stub_equals_original_success_path(native, key, seed):
    patched, stock = native(immo_image(key)), native(source(key))
    entry = LAYOUTS[key][0] + 0x80000
    for emu in (patched, stock):
        emu.mem.external_ram[:] = bytes((seed & 255,)) * len(emu.mem.external_ram)
        for address in range(0xFD00, 0xFE00, 2):
            emu.write(address, seed)
        emu.reg.set_word(6, 0xA123)
        emu.reg.set_word(7, 0xB456)
    before = bytes(patched.mem.image)
    context = patched.reg.sp, patched.reg.get_word(0), patched.reg.cp, tuple(patched.reg.dpp)
    result = patched.call(entry, max_steps=200)
    reference = stock.run_from(entry + 0x4E0, stop_at=entry + 0x4E6, max_steps=200)
    assert result.exit_reason == reference.exit_reason == "stop_at"
    assert (patched.reg.sp, patched.reg.get_word(0), patched.reg.cp, tuple(patched.reg.dpp)) == context
    assert [patched.reg.get_word(i) for i in (6, 7)] == [0xA123, 0xB456]
    assert patched.mem.external_ram == stock.mem.external_ram
    for start, end in ((0xE000, 0xE800), (0xF900, 0xFE00)):
        assert bytes(patched.read_byte(at) for at in range(start, end)
                     if not context[0] - 8 <= at < context[0]) == bytes(
            stock.read_byte(at) for at in range(start, end)
            if not context[0] - 8 <= at < context[0])
    assert bytes(patched.mem.image) == before


@pytest.mark.parametrize("key", LAYOUTS)
def test_immobilizer_fault_operand_uses_the_existing_increment_and_keeps_limit(native, key):
    original = source(key)
    installed = immo_image(key)
    entry = LAYOUTS[key][0]
    offset = entry + 0x584
    maximum = 0x70000 + int.from_bytes(original[offset + 2:offset + 4], "little")
    increment = int.from_bytes(original[entry + 0x46:entry + 0x48], "little")
    assert installed[maximum] == original[maximum] == 10
    assert installed[0x70000 + increment] == 0 and original[0x70000 + increment] == 2
    for image, expected_increment in ((original, 10), (installed, 0)):
        emu = native(image)
        result = emu.run_from(offset + 0x80000, stop_at=offset + 8 + 0x80000, max_steps=3)
        assert result.exit_reason == "stop_at", result
        assert (emu.reg.get_word(14), emu.reg.get_word(15)) == (expected_increment, 10)


@pytest.mark.parametrize("key", LAYOUTS)
@pytest.mark.parametrize("boot", (False, True))
@pytest.mark.parametrize("state", ("stock", "metadata_only", "installed"))
def test_checksum_toggle_preserves_native_boot_and_runtime_scopes(native, key, boot, state):
    emu = native(checksum_image(key) if state == "installed" else source(key))
    if state == "metadata_only":
        emu.mem.image[0x73FFE:0x74000] = b"\xFF\xFF"
    before = bytes(emu.mem.image)
    entry = LAYOUTS[key][2] + 0x80000
    emu.write(0xF8F4, 4)
    result = emu.call(0x1460, max_steps=200) if boot else emu.run_from(
        entry, stop_at=entry + 0x74, max_steps=100)
    assert result.exit_reason == "stop_at", result
    expected = 2 if state == "stock" else 9 if state == "installed" or key == "430065" else 8
    assert emu.read(0xF8F4) == expected
    assert bytes(emu.mem.image) == before


@pytest.mark.parametrize("key", LAYOUTS)
@pytest.mark.parametrize("program_bad,cal_bad", ((False, True), (True, False), (True, True)))
def test_checksum_bypass_keeps_program_monitor_failures(native, key, program_bad, cal_bad):
    image = checksum_image(key)
    emu = native(image)
    entry, _, program_acc, cal_acc, flag_word, flag_bit = MONITOR[key]
    program_sum = int.from_bytes(image[0x6FDAE:0x6FDB2], "little") ^ int(program_bad)
    calibration_sum = int.from_bytes(image[0x72FFC:0x73000], "little") ^ int(cal_bad)
    emu.write_byte(0xE000, 2)
    emu.write_byte(0xE002, 2)
    for address, value in ((program_acc, program_sum), (cal_acc, calibration_sum)):
        emu.write(address, value & 0xFFFF)
        emu.write(address + 2, value >> 16)
    result = emu.run_from(entry, stop_at=(entry + 82, entry + 86), max_steps=40)
    assert result.exit_reason == "stop_at", result
    assert emu.cpu.pc == entry + (82 if program_bad else 86)
    assert bool(emu.read(flag_word) & (1 << flag_bit)) == program_bad


@pytest.mark.parametrize("key,caller", (
    ("430037", 0x54E8C), ("430055", 0x57D74), ("430064", 0x5C3AA),
    ("430065", 0x5DB52), ("430066", 0x600D8), ("430066-later", 0x600D8)))
def test_original_ms43_caller_reaches_installed_stub_without_changing_its_call(native, key, caller):
    image = immo_image(key)
    entry = LAYOUTS[key][0]
    expected = b"\xDA" + bytes((8 + (entry >> 16),)) + (entry & 65535).to_bytes(2, "little")
    assert image[caller:caller + 4] == source(key)[caller:caller + 4] == expected
    assert source(key).count(expected, 0x10000, 0x6F000) == 1
    emu = native(image)
    emu.write(0xFD00 + image[entry + 1] * 2, 0xFFFF)
    context = emu.reg.sp, emu.reg.get_word(0), emu.reg.cp, tuple(emu.reg.dpp)
    result = emu.run_from(caller + 0x80000, stop_at=caller + 0x80004, max_steps=200)
    assert result.exit_reason == "stop_at", result
    assert (emu.reg.sp, emu.reg.get_word(0), emu.reg.cp, tuple(emu.reg.dpp)) == context
    assert not emu.read(0xFD00 + image[entry + 1] * 2) & (1 << (image[entry] >> 4))
    assert bytes(emu.mem.image) == image


@pytest.mark.parametrize("key", ("0110C7", "0110CA"))
@pytest.mark.parametrize("seed", (0, 0xFFFF, 0xA55A, 0x5AA5))
def test_ms42_port_executes_actual_success_flags_and_keeps_the_fault_limit(native, key, seed):
    from ms41emu.ms42 import MS42
    original = composed_image(key, "0110C6").image
    patched = bytearray(original)
    assert original[0x75B9C:0x75BA8] == full_image("0110C6")[0x75B9C:0x75BA8]
    assert original[0x29E8A:0x29E92] == bytes.fromhex("C2FEE901C2FFE901")
    patched[0x283BA:0x283C4] = original[0x288C6:0x288CE] + b"\xDB\0"
    patched[0x29E8A:0x29E8E] = bytes.fromhex("C2FEA401")
    assert patched[0x481A4] == 2
    patched[0x481A4] = 0
    assert config.apply_config(original, {"Immobilizer bypass": "Enabled"}) == patched
    emu, stock = MS42(bytes(patched), experimental=True), MS42(original, experimental=True)
    for machine in (emu, stock):
        machine.enable_hardware()
        for address, value in ((0xFD1C, seed), (0xFD1E, seed ^ 0x1234),
                               (0xF184, seed), (0xFF28, seed ^ 0xF00F)):
            machine.write(address, value)
    registers = [emu.reg.get_word(i) for i in range(16)]
    stack = emu.reg.sp
    result = emu.call(0x283BA, max_steps=30)
    reference = stock.run_from(0x288C6, stop_at=0x288CE, max_steps=30)
    assert result.exit_reason == reference.exit_reason == "stop_at"
    assert emu.reg.sp == stack and [emu.reg.get_word(i) for i in range(16)] == registers
    assert [emu.read(address) for address in (0xFD1C, 0xFD1E, 0xF184, 0xFF28)] == [
        stock.read(address) for address in (0xFD1C, 0xFD1E, 0xF184, 0xFF28)]
    result = emu.run_from(0x29E8A, stop_at=0x29E92, max_steps=3)
    assert result.exit_reason == "stop_at", result
    assert (emu.reg.get_word(14), emu.reg.get_word(15)) == (0, original[0x481E9])
    assert bytes(emu.mem.image) == bytes(patched)
