"""Exact AMD monitor artifact and optional canonical-emulator polling regression."""
import hashlib
import os
from pathlib import Path

import pytest

from engines.bsl import bsl_unbrick as bsl


def test_amd_monitor_binds_reviewed_source_and_fits_existing_preloader():
    source = Path(bsl.__file__).with_name("monitor_flash_amd.asm").read_text(encoding="utf-8")
    assert hashlib.sha256(source.encode()).hexdigest() == (
        "891ce84ea449d85c23a98b15a673a08acdfc7087d5df880dbdfba1f1a2e3b4d3")
    assert hashlib.sha256(bsl.MONITOR_FLASH_AMD).hexdigest() == (
        "97b7d8076d1aa0a237b6aac650e9dafc155cdf11762a8765ad5306cd2b38970b")
    assert len(bsl.MONITOR_FLASH_AMD) == 484 <= bsl.MONITOR_LOAD_LEN == 512


@pytest.fixture
def canonical_monitor(monkeypatch):
    root = os.environ.get("MS41EMU_ROOT")
    data_root = os.environ.get("MS41_TEST_DATA_ROOT")
    if not root or not data_root:
        pytest.skip("set MS41EMU_ROOT and MS41_TEST_DATA_ROOT for monitor execution")
    monkeypatch.syspath_prepend(root)
    from ms41emu import Emulator
    from ms41emu.peripherals import AmdFlashModel
    from ms41emu.references import resolve_reference
    from tools.opcode_coverage import require_trusted
    reference = resolve_reference(".2", root=data_root, required=False)
    if reference is None:
        pytest.skip("canonical MS41.2 reference unavailable")
    return Emulator, AmdFlashModel, reference.read_bytes(), require_trusted


def _exchange(emu, wire, length):
    start = len(emu.asc0.tx)
    emu.asc0.rx_inject(wire)

    class Replied(Exception):
        pass

    def trace(_pc, _opcode):
        if len(emu.asc0.tx) >= start + length:
            raise Replied

    emu.cpu.set_trace(trace)
    try:
        with pytest.raises(Replied):
            emu.run_from(emu.cpu.pc, max_steps=200_000)
    finally:
        emu.cpu.set_trace(None)
    reply = bytes(emu.asc0.tx[start:])
    assert len(reply) == length
    return reply


@pytest.mark.parametrize("device,half", (
    ("am29f200bb", "lower"), ("am29f400bb", "lower"), ("am29f400bb", "upper")))
@pytest.mark.parametrize("kind", ("erase", "program"))
@pytest.mark.parametrize("busy_reads,genuine_error", ((1, False), (2, False), (3, False), (4, False), (1, True)))
def test_amd_monitor_completion_and_real_failure(canonical_monitor, device, half, kind, busy_reads, genuine_error):
    Emulator, AmdFlashModel, reference, require_trusted = canonical_monitor
    address = 0x10000
    offset = address ^ 0x4000
    image = bytearray(reference)
    before = b"\xff\xff\x00\x00" if kind == "erase" else b"\xff" * 4
    image[offset:offset + 4] = before
    emu = Emulator.load(bytes(image), force_variant="1406464")
    # Monitor-leaf fixture: free GPR bank, separate from the 512-byte preload.
    # This exercises its wire commands, not the hardware BSL ROM handoff.
    emu.reg.cp = 0xFA00
    emu.reg.psw.IEN = False
    for index, byte in enumerate(bsl.MONITOR_FLASH_AMD.ljust(bsl.MONITOR_LOAD_LEN, b"\x00")):
        emu.write_byte(0xFA60 + index, byte)
    emu.cpu.ip = 0xFA60
    flash = AmdFlashModel(device=device, active_half=half, busy_reads=busy_reads,
                          error=kind if genuine_error else None, inactive_image=image)
    emu.mem.flash_model = flash
    assert _exchange(emu, b"P", 1) == b"\xa5"
    encoded = (address >> 14).to_bytes(2, "little") + (address & 0x3fff).to_bytes(2, "little")
    data = b"\xef\xbe\xde\xc0"
    wire = b"E" + encoded if kind == "erase" else b"F" + encoded + b"\x04\x00" + data
    reply = _exchange(emu, wire, 1)
    actual = bytes(flash.array()[offset:offset + 4])
    readback = _exchange(emu, b"R" + encoded + b"\x04\x00", 4)
    assert _exchange(emu, b"P", 1) == b"\xa5"
    assert not emu.reset_count
    if device == "am29f400bb":
        other = "upper" if half == "lower" else "lower"
        assert bytes(flash.array(other)) == bytes(image)
    if genuine_error:
        assert actual == before
        assert reply == (b"\x20" if kind == "erase" else b"K")
        if kind == "program":
            assert readback != data  # F retains its existing readback-verification contract.
    else:
        assert actual == (b"\xff" * 4 if kind == "erase" else data)
        assert readback == actual
        assert reply == (b"\x80" if kind == "erase" else b"K")
    require_trusted(emu.cpu.executed_opcodes)
