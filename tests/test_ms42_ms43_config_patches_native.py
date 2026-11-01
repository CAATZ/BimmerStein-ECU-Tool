"""Execute installed configuration patches against exact stock C167 firmware.

Entry state and completed checksum inputs are supplied; these are bounded native
contracts, not engine-start, scheduler, serial or physical ECU validation.
"""

import os
from pathlib import Path

import pytest

import ms42_ms43_config_patches as config


@pytest.fixture
def native(monkeypatch):
    root = Path(os.environ.get("MS41EMU_ROOT", Path.home() / "ECU Emulator"))
    if not (root / "ms41emu").is_dir():
        pytest.skip("canonical C167 emulator unavailable")
    monkeypatch.syspath_prepend(str(root))
    from ms41emu.ms42 import MS42
    from ms41emu.ms43 import MS43

    refs = Path(os.environ.get("MS41_TEST_DATA_ROOT", Path(__file__).resolve().parents[2] / "_shared"))

    def load(family, patch=None):
        name = ("Siemens_MS42_0110C6_E46_M52TUB28_EU3_LHD.bin" if family == "MS42" else
                "Siemens_MS43_MS430069_E46_M54B30_US.bin")
        path = refs / f"REF_{family}" / name
        if not path.is_file():
            pytest.skip(f"private {family} stock reference unavailable")
        emu = (MS42 if family == "MS42" else MS43).load(path)
        if patch:
            emu.mem.image[:] = config.apply_config(bytes(emu.mem.image), {patch: "Enabled"})
        return emu

    return load


@pytest.mark.parametrize("seed", [0, 0xFFFF, 0xA55A, 0x5AA5])
def test_ms42_installed_bypass_matches_native_success_and_returns(native, seed):
    patched = native("MS42", "Immobilizer bypass")
    stock = native("MS42")
    states = {0xFD1C: seed, 0xFD1E: seed ^ 0x1234, 0xF184: seed, 0xFF28: seed ^ 0xF00F}
    for emu in (patched, stock):
        emu.enable_hardware()
        for address, value in states.items():
            emu.write(address, value)
    # Interrupt-control register reserved bits read zero in the C167 hardware model.
    states = {address: patched.read(address) for address in states}
    before = bytes(patched.mem.image)
    stack = patched.reg.sp
    registers = [patched.reg.get_word(i) for i in range(16)]
    result = patched.call(0x283BA, max_steps=30)
    reference = stock.run_from(0x288C6, stop_at=0x288CE, max_steps=30)
    assert result.exit_reason == reference.exit_reason == "stop_at"
    assert patched.reg.sp == stack
    assert [patched.reg.get_word(i) for i in range(16)] == registers
    assert patched.read(0xFD1C) == states[0xFD1C] & ~0x8000
    assert patched.read(0xFD1E) == states[0xFD1E] & ~4
    assert patched.read(0xF184) == states[0xF184] & 0xFF00
    assert patched.read(0xFF28) == states[0xFF28] & ~0xF0
    assert {address: patched.read(address) for address in states} == {
        address: stock.read(address) for address in states}
    assert bytes(patched.mem.image) == before


@pytest.mark.parametrize("individual,common", [(0, 0), (0x3F, 0), (0x15, 0), (0, 0x10), (0, 0x20)])
def test_ms43_installed_call_preserves_other_inhibition_reasons(native, individual, common):
    patched = native("MS43", "Immobilizer bypass")
    stock = native("MS43")
    reasons = {0x81D9: 1, 0x845D: 2, 0x845C: 4, 0x8474: 8,
               0x8477: 0x10, 0x8478: 0x20, 0x8475: 0, 0x8473: 0}
    words = {0xFD30: 0xE05A | (individual << 7), 0xFD32: 0xAA87 | common,
             0xFD34: 0xA55B, 0xFD72: 0x1234, 0xFD74: 0xFED0}
    for emu in (patched, stock):
        for address, value in reasons.items():
            emu.write_byte(address, value)
        for address, value in words.items():
            emu.write(address, value)
        emu.write_byte(0x8479, 0x3F)
        emu.write_byte(0xFB9A, 0)
        emu.write_byte(0xFC4D, 0xFF)
    image = bytes(patched.mem.image)
    stack, user_stack = patched.reg.sp, patched.reg.get_word(0)
    result = patched.run_from(0xE00D8, stop_at=0xE00DC, max_steps=120)
    reference = stock.run_from(0xAE24C, stop_at=0xAE252, max_steps=120)
    assert result.exit_reason == reference.exit_reason == "stop_at"
    assert patched.reg.sp == stack and patched.reg.get_word(0) == user_stack
    assert patched.read(0xFD30) == words[0xFD30] & ~0xE000
    assert patched.read(0xFD32) == words[0xFD32] & ~7
    assert patched.read(0xFD34) == words[0xFD34] & ~9
    assert patched.read_byte(0x8479) == 0
    assert patched.read_byte(0xFB9A) == 0x3F  # Other injected cut reasons remain.
    assert patched.read_byte(0xFC4D) == 0xFD
    assert patched.read(0xFD72) & ~0xC000 == words[0xFD72] & ~0xC000
    assert patched.read(0xFD74) & ~0xF == words[0xFD74] & ~0xF
    for cylinder in range(6):
        expected = bool(individual & (1 << cylinder) or common)
        address, bit = (0xFD72, 14 + cylinder) if cylinder < 2 else (0xFD74, cylinder - 2)
        assert bool(patched.read(address) & (1 << bit)) == expected
    for address in words:
        assert patched.read(address) == stock.read(address)
    for address, reason in reasons.items():
        assert patched.read_byte(address) == reason
    for address in (*reasons, 0x8479, 0xFB9A, 0xFC4D):
        assert patched.read_byte(address) == stock.read_byte(address)
    assert bytes(patched.mem.image) == image


def test_bad_community_ms42_target_is_rejected_and_negative_execution_fails(native):
    from ms41emu.errors import MemoryFault

    emu = native("MS42", "Immobilizer bypass")
    emu.mem.image[0x283BA:0x283BE] = bytes.fromhex("DA07C859")
    before = bytes(emu.mem.image)
    assert config.read_config(before)["Immobilizer bypass"] == "Custom / unsupported bytes"
    with pytest.raises(ValueError, match="unrecognized bytes"):
        config.apply_config(before, {"Immobilizer bypass": "Enabled"})
    # Source patchlist 1.7.1 enters the middle of PWM code and falls into journal writes.
    with pytest.raises(MemoryFault, match="write to flash ROM"):
        emu.call(0x283BA, max_steps=1000)
    assert emu.cpu.pc == 0x75D3A
    assert bytes(emu.mem.image) == before


@pytest.mark.parametrize("family,entry,stop,maximum", [
    ("MS42", 0x29E8A, 0x29E92, 0x481E9),
    ("MS43", 0xAE2F0, 0xAE2F8, 0x70AB9),
])
@pytest.mark.parametrize("patched", [False, True])
def test_installed_dtc_operand_uses_increment_without_changing_limit(native, family, entry, stop, maximum, patched):
    emu = native(family, "Immobilizer bypass" if patched else None)
    image = bytes(emu.mem.image)
    result = emu.run_from(entry, stop_at=stop, max_steps=3)
    assert result.exit_reason == "stop_at"
    assert image[maximum] > 0
    assert emu.reg.get_word(14) == (0 if patched else image[maximum])
    assert emu.reg.get_word(15) == image[maximum]
    assert bytes(emu.mem.image) == image


@pytest.mark.parametrize("boot", [False, True])
@pytest.mark.parametrize("state,expected", [("stock", 8), ("metadata_only", 0x18), ("installed", 9)])
def test_ms42_installed_crc_exception_uses_paired_metadata(native, boot, state, expected):
    emu = native("MS42", "Calibration checksum bypass" if state == "installed" else None)
    if state == "metadata_only":
        emu.mem.image[0x4FEFE:0x4FF00] = b"\xff\xff"
    image = bytes(emu.mem.image)
    store = 0x4FEE0
    count = int.from_bytes(image[store + 2:store + 4], "little")
    end = int.from_bytes(image[store + 8 + (count - 1) * 8:store + 12 + (count - 1) * 8], "little")
    emu.write(0xF756, int.from_bytes(image[store:store + 2], "little") ^ 1)
    if boot:
        emu.write(0xF72E, 2)
        emu.write_byte(0xF765, count - 1)
        emu.write(0xF760, (end + 1) & 0xFFFF)
        emu.write(0xF762, (end + 1) >> 16)
        result = emu.call(0xAC2, max_steps=500)
    else:
        emu.write(0xF72E, 8)
        result = emu.run_from(0x1C3C8, stop_at=0x1C410, max_steps=40)
    assert result.exit_reason == "stop_at"
    assert emu.read(0xF72E) == expected
    assert bytes(emu.mem.image) == image


@pytest.mark.parametrize("boot", [False, True])
@pytest.mark.parametrize("state", ["stock", "metadata_only", "without_runtime_instruction", "installed"])
def test_ms43_boot_and_runtime_checks_need_their_own_bypass_changes(native, boot, state):
    emu = native("MS43", "Calibration checksum bypass" if state in ("installed", "without_runtime_instruction") else None)
    if state == "metadata_only":
        emu.mem.image[0x73FFE:0x74000] = b"\xff\xff"
    if state == "without_runtime_instruction":
        emu.mem.image[0x63595] = 0x84
    image = bytes(emu.mem.image)
    emu.write(0xF8F4, 4)
    result = (emu.call(0x1460, max_steps=200) if boot else
              emu.run_from(0xE3530, stop_at=0xE35A4, max_steps=100))
    assert result.exit_reason == "stop_at"
    expected = (2 if state == "stock" else 9 if state == "installed" or
                (boot and state == "without_runtime_instruction") else 8)
    assert emu.read(0xF8F4) == expected
    assert bytes(emu.mem.image) == image


@pytest.mark.parametrize("patched", [False, True])
@pytest.mark.parametrize("program_bad,calibration_bad", [(False, True), (True, False), (True, True)])
def test_ms43_installed_monitor_bypass_does_not_bypass_program_monitor(native, patched, program_bad, calibration_bad):
    emu = native("MS43", "Calibration checksum bypass" if patched else None)
    image = bytes(emu.mem.image)
    sums = [(0xE672, int.from_bytes(image[0x6FDAE:0x6FDB2], "little") ^ program_bad),
            (0xE680, int.from_bytes(image[0x72FFC:0x73000], "little") ^ calibration_bad)]
    emu.write_byte(0xE000, 2)
    emu.write_byte(0xE002, 2)
    for address, total in sums:
        emu.write(address, total & 0xFFFF)
        emu.write(address + 2, total >> 16)
    result = emu.run_from(0xDFCB8, stop_at=(0xDFD0A, 0xDFD0E), max_steps=40)
    fault = program_bad or (calibration_bad and not patched)
    assert result.exit_reason == "stop_at"
    assert emu.cpu.pc == (0xDFD0A if fault else 0xDFD0E)
    assert bool(emu.read(0xFD7E) & 0x100) == fault
    assert bytes(emu.mem.image) == image
