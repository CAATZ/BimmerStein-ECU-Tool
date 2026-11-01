"""Original readers and BMW measurement owners agree on the stored quantities.

The canonical emulator supplies warm RAM/EEPROM inputs. No engine cycle,
hardware operation, interpreter change or substituted firmware response is used.
"""

from ms42_ms43_storage_units import storage_field_presentation, storage_display_value
from tests.test_ms42_ms43_config_native import native as native
from tests.test_ms42_ms43_storage_edit_native import restore_record
from tests.test_ms42_ms43_storage_inspection import put_record
from tests.test_ms42_live_revisions_native import native_ecu as ms42_ecu
from tests.test_ms43_x001_live_native import native_ecu as ms43_ecu
from tests.test_ms43_storage_edit_native import native_storage as native_storage, restore
from tests.test_ms42_ms43_config_vanos_knock_native import write, byte


def words(*values):
    return b"".join((value & 0xFFFF).to_bytes(2, "little") for value in values)


def verify_groups(emu, family, ecu):
    payload = ecu.execute(0x0B, b"\x92")
    assert tuple(int.from_bytes(payload[i:i + 2], "big", signed=True)
                 for i in (0, 2, 4)) == (1234, -4321, 2500)
    idle = 0x104 if family == "MS42" else 0x280
    assert storage_display_value(storage_field_presentation(family, idle), 1234) == 1234 * 100 / 65536
    if family == "MS42":
        # The native fuel-adaptation measurement group requires its active mode.
        emu.write(0xFD42, emu.read(0xFD42) | 8)
    payload = ecu.execute(0x0B, b"\x91")
    start = 0 if family == "MS42" else 1
    assert tuple(int.from_bytes(payload[i:i + 2], "big")
                 for i in range(start, start + 8, 2)) == (0x8001, 0x7FFF, 0x7FFE, 0x8002)
    additive = 0x7A if family == "MS42" else 0x2E8
    assert storage_display_value(storage_field_presentation(family, additive), 0x8001) == .004


def verify_references(emu, family, ecu):
    payload = ecu.execute(0x0B, b"\x90")
    if family == "MS42":
        assert (payload[2], payload[8]) == (0xA0, 0x80)
        relative = 0x200
    else:
        assert (int.from_bytes(payload[2:4], "big"), int.from_bytes(payload[8:10], "big")) == (0xA000, 0x8000)
        relative = 0x28A
    assert storage_display_value(storage_field_presentation(family, relative), 0xA000) == 120
    assert storage_display_value(storage_field_presentation(family, relative + 2), 0x8000) == -108


def verify_knock_angle(emu, family):
    if family == "MS42":
        cells, correction, base, entry, stop, output = 0xC486, 0xC464, 0xFA61, 0x2F5FA, 0x2F624, 0xE53D
        relative = 0x18
    else:
        cells, correction, base, entry, stop, output = 0x40221, 0x403A2, 0xE2B5, 0xB65B0, 0xB65DA, 0xFBAB
        relative = 0x2A4
    assert all(byte(emu, cells + index) == 116 for index in range(6 * 64))
    # Selected correction is a supplied runtime input; this is not the whole learner.
    write(emu, correction, byte(emu, cells))
    write(emu, base, 100)
    result = emu.run_from(entry, stop_at=(stop,), max_steps=100)
    assert result.exit_reason == "stop_at", result
    assert byte(emu, output) == 167
    # BMW ignition display uses 72 - 0.375*raw; neutral publication is raw155.
    difference = (72 - 167 * .375) - (72 - 155 * .375)
    assert storage_display_value(storage_field_presentation(family, relative), 116) == difference == -4.5


def test_ms42_actual_storage_words_reach_native_idle_and_fuel_measurements(native):
    emu = native("MS42")
    # Stock VANOS restore also issues uncommanded writes to a ROM-mapped window.
    # Use the same flash device as the existing storage fixtures, checking no ROM changes.
    emu.enable_flash()
    restore_record(emu, 0x104, words(1234, -4321, 2500) + b"\x80\x00", 0x2B324, True)
    restore_record(emu, 0x78, words(0x8000, 0x8001, 0x7FFF, 0x7FFE, 0x8002), 0x31E3E, True)
    original = bytes(emu.mem.image)
    verify_groups(emu, "MS42", ms42_ecu(emu))
    assert bytes(emu.mem.image) == original
    restore_record(emu, 0x200, words(0xA000, 0x8000) + b"\x00" * 16, 0x2DC24, True)
    verify_references(emu, "MS42", ms42_ecu(emu))
    restore_record(emu, 0x18, bytes((116,)) * 64 + words(0), 0x2FBE4, True)
    original = bytes(emu.mem.image)
    verify_knock_angle(emu, "MS42")
    assert bytes(emu.mem.image) == original


def test_ms43_actual_storage_words_reach_native_idle_fuel_and_throttle_measurements(native_storage):
    emu, peer, original = native_storage
    put_record(peer.storage, 0x280, words(1234, -4321, 2500) + b"\x80\x00")
    put_record(peer.storage, 0x2E6, words(0x8000, 0x8001, 0x7FFF, 0x7FFE, 0x8002))
    put_record(peer.storage, 0x348, words(123, 456, 234, 567, 0x5555))
    put_record(peer.storage, 0x28A, words(0xA000, 0x8000) + b"\x00" * 16)
    put_record(peer.storage, 0x2A0, words(0, 0) + bytes((116,)) * 64)
    writes = peer.storage_write_attempts
    for entry in (0xB1928, 0xDB310, 0x9B2F2, 0xB496E, 0xB6D00):
        restore(emu, entry)
    ecu = ms43_ecu(emu)
    verify_groups(emu, "MS43", ecu)
    payload = ecu.execute(0x0B, b"\x92")
    assert [int.from_bytes(payload[i:i + 2], "big") for i in (14, 16, 22, 24)] == [123, 456, 234, 567]
    assert storage_display_value(storage_field_presentation("MS43", 0x348), 512) == 2.5
    verify_references(emu, "MS43", ecu)
    verify_knock_angle(emu, "MS43")
    assert bytes(emu.mem.image) == original
    assert peer.storage_write_attempts == writes


def test_ms42_original_startup_supplies_the_observed_vanos_restore_context(native, monkeypatch):
    import os
    from pathlib import Path
    import tests
    from checksum import _crc

    root = Path(os.environ.get("MS41EMU_ROOT", Path.home() / "ECU Emulator"))
    monkeypatch.setattr(tests, "__path__", [*tests.__path__, str(root / "tests")])
    from tests.test_c167_lifecycle import prepare_lifecycle

    original = native("MS42")
    image = bytearray(original.mem.image)
    put_record(image, 0x4610, words(0xA000, 0x8000) + bytes(16))
    length = int.from_bytes(image[0x47F6:0x47F8], "little")
    image[0x47FE:0x4800] = _crc(image[0x4400:0x4400 + length], 15).to_bytes(2, "little")
    emu, _, _ = prepare_lifecycle(type(original), bytes(original.mem.image))
    emu.mem.image[:] = image
    seen = []

    def trace(pc, opcode):
        if pc in (0x2A31A, 0x2DC24, 0x2DCAC):
            seen.append((pc, tuple(emu.mem.read_word_direct(a) for a in range(0xFE00, 0xFE08, 2))))

    emu.cpu.set_trace(trace)
    result = emu.cpu.run(emu.cpu.pc, stop_at=0x2DC24, max_steps=1_000_000)
    assert result.exit_reason == "stop_at", result
    result = emu.cpu.run(emu.cpu.pc, stop_at=0x2A31E, max_steps=100_000)
    assert result.exit_reason == "stop_at", result
    assert {pc for pc, _ in seen} == {0x2A31A, 0x2DC24, 0x2DCAC}
    assert all(dpp == (0x12, 0x14, 0x13, 3) for _, dpp in seen)
    assert (emu.read(0xD042), emu.read(0xD1B0)) == (0xA000, 0x8000)
    assert bytes(emu.mem.image) == bytes(image)
