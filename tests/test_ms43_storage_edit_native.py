"""Production offline edits consumed by original MS430069 EEPROM readers.

The canonical lifecycle fixture supplies the existing SSC EEPROM peer and warm
startup inputs. Original firmware owns binding, CRC and restore decisions. These
bounded reader calls do not prove physical writes or a complete engine lifecycle.
"""

import os
from pathlib import Path

import pytest

from ms42_ms43_storage import make_snapshot
from ms42_ms43_storage_edit import edit_storage
from tests.test_ms42_ms43_config_native import native as native
from tests.test_ms42_ms43_storage_inspection import put_record


@pytest.fixture
def native_storage(native, monkeypatch):
    import tests

    root = Path(os.environ.get("MS41EMU_ROOT", Path.home() / "ECU Emulator"))
    # Reuse the canonical SSC peer rather than copying its device behavior.
    monkeypatch.setattr(tests, "__path__", [*tests.__path__, str(root / "tests")])
    from tests.test_c167_lifecycle import prepare_lifecycle

    original = native("MS43")
    firmware = bytes(original.mem.image)
    emu, peer, _ = prepare_lifecycle(type(original), firmware)
    result = emu.cpu.run(emu.cpu.pc, stop_at=0xE0094, max_steps=1_000_000)
    assert result.exit_reason == "stop_at", result
    assert not emu.mem.read_word_direct(0xFD46) & 0x20
    emu.cpu.set_trace(None)
    return emu, peer, firmware


def snapshot_with_records(peer, coil, heat):
    raw = bytearray(peer.storage)
    put_record(raw, 0x280, bytes.fromhex("01000200030080a7"))
    put_record(raw, 0x3E2, bytes((coil, 0xF8)))
    put_record(raw, 0x3E6, bytes((heat, 0xC3)))
    return make_snapshot("MS43", "eeprom", raw, software_id="430069")


def read_word(emu, address):
    return emu.mem._physical_read(address) | emu.mem._physical_read(address + 1) << 8


def restore(emu, entry):
    result = emu.call(entry, max_steps=500_000)
    assert result.exit_reason == "stop_at", result


@pytest.mark.parametrize("coil,heat,words", (
    (1, 0, (-32768, -1, 32767)),
    (2, 1, (32767, 1, -32768)),
))
def test_offline_edited_records_reload_native_signed_words_and_boolean_fields(
        native_storage, coil, heat, words):
    emu, peer, firmware = native_storage
    source = snapshot_with_records(peer, 3 - coil, 1 - heat)
    before = source["data"]
    changes = {0x280: words[0], 0x282: words[1], 0x284: words[2],
               0x3E2: coil, 0x3E6: heat}
    edited = edit_storage(source, changes)
    raw = edited["data"]
    assert source["data"] == before
    allowed = {*range(0x280, 0x286), 0x288, 0x289, 0x3E2, 0x3E4, 0x3E5,
               0x3E6, 0x3E8, 0x3E9}
    assert all(old == new or offset in allowed
               for offset, (old, new) in enumerate(zip(before, raw)))
    assert raw[0x286:0x288] == b"\x80\xA7"
    assert raw[0x3E3] == 0xF8 and raw[0x3E7] == 0xC3
    peer.storage[:] = raw
    writes = peer.storage_write_attempts

    restore(emu, 0xBDC2C)
    assert not emu.mem.read_word_direct(0xFD46) & 0x20
    restore(emu, 0xB1928)
    assert tuple(read_word(emu, address) for address in (0x40882, 0x4088C, 0x40894)) == tuple(
        value & 0xFFFF for value in words)
    assert emu.mem._physical_read(0x401EE) == 0x80
    restore(emu, 0xB2B98)
    assert bool(emu.mem.read_word_direct(0xFD1C) & 0x1000) == bool(heat)
    restore(emu, 0xADA30)
    assert emu.mem._physical_read(0x401D8) == coil
    assert bool(emu.mem.read_word_direct(0xFD32) & 8) == (coil == 2)
    assert bytes(peer.storage) == raw and peer.storage_write_attempts == writes
    assert bytes(emu.mem.image) == firmware


@pytest.mark.parametrize("failure", ("binding", "record_crc"))
def test_native_binding_or_record_crc_rejects_changed_payload_without_writes(native_storage, failure):
    emu, peer, firmware = native_storage
    source = snapshot_with_records(peer, 1, 0)
    edited = edit_storage(source, {0x280: -123, 0x282: 456, 0x284: -789, 0x3E2: 2, 0x3E6: 1})
    raw = bytearray(edited["data"])
    if failure == "binding":
        binding = bytearray(raw[0x32:0x3A])
        binding[0] ^= 1
        put_record(raw, 0x32, binding)  # Local CRC still passes; program binding does not.
    else:
        for offset in (0x288, 0x3E4, 0x3E8):
            raw[offset] ^= 1
    peer.storage[:] = raw
    writes = peer.storage_write_attempts

    restore(emu, 0xBDC2C)
    assert bool(emu.mem.read_word_direct(0xFD46) & 0x20) == (failure == "binding")
    restore(emu, 0xB1928)
    assert tuple(read_word(emu, address) for address in (0x40882, 0x4088C, 0x40894)) == (0, 0, 0)
    assert emu.mem._physical_read(0x401EE) == 0x80
    restore(emu, 0xB2B98)
    assert not emu.mem.read_word_direct(0xFD1C) & 0x1000
    # Stop before detection instead of using the returning-function helper.
    result = emu.run_from(0xADA30, stop_at=0xADA8C, max_steps=500_000)
    assert result.exit_reason == "stop_at", result
    assert emu.cpu.pc == 0xADA8C and emu.mem._physical_read(0x401D8) == 0
    assert bytes(peer.storage) == bytes(raw) and peer.storage_write_attempts == writes
    assert bytes(emu.mem.image) == firmware
