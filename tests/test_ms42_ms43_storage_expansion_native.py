"""Additional offline fields consumed by original C167 firmware readers.

The canonical SSC peer supplies EEPROM replies; no hardware is opened. Tests
exercise record admission and restoration, not a full engine lifecycle.
"""

import pytest

from checksum import _crc
from ms42_ms43_diagnostics import inspect_storage_records
from ms42_ms43_storage import make_snapshot
from ms42_ms43_storage_edit import editable_fields, edit_storage
from tests.test_ms42_ms43_config_native import native as native
from tests.test_ms42_ms43_storage_edit_native import native_selection
from tests.test_ms42_ms43_storage_inspection import put_record
from tests.test_ms43_storage_edit_native import native_storage as native_storage, read_word, restore


def words(*values):
    return b"".join(value.to_bytes(2, "little") for value in values)


def test_ms42_additional_lambda_words_and_calibrated_high_bytes_reload(native):
    emu = native("MS42")
    firmware = bytes(emu.mem.image)
    cal_word = lambda offset: int.from_bytes(firmware[offset:offset + 2], "little")
    base = 0x410
    raw = bytearray(firmware[0x4000:0x8000])
    put_record(raw, base + 0x62, words(cal_word(0x486B2), cal_word(0x486B0),
                                     cal_word(0x48706), cal_word(0x48704)) + b"\x82\x83\x00\xA5")
    put_record(raw, base + 0x24A, words(123, 456, 0, 0, 789, 900))
    length = int.from_bytes(raw[0x7F6:0x7F8], "little")
    raw[0x7FE:0x800] = _crc(raw[0x400:0x400 + length], 0xF).to_bytes(2, "little")
    source = make_snapshot("MS42", "retained", raw, software_id="0110C6")
    assert base + 0x6A not in {field["offset"] for field in editable_fields(source)}
    for low, high in ((0, 255), (255, 0)):
        changes = {base + 0x24A: 0, base + 0x24C: 0xFFFE,
                   base + 0x252: 0xFFFF, base + 0x254: 0,
                   base + 0x6A: low, base + 0x6B: high}
        target = edit_storage(source, changes, calibration=firmware)["data"]
        allowed = {base + 0x6A, base + 0x6B, base + 0x6E, base + 0x6F,
                   *range(base + 0x24A, base + 0x24E), *range(base + 0x252, base + 0x258), 0x7FE, 0x7FF}
        assert all(old == new or offset in allowed for offset, (old, new) in enumerate(zip(raw, target)))
        assert native_selection(emu, target) == 1
        emu.write(0xF94A, emu.read(0xF92C))
        emu.write(0xF94C, emu.read(0xF92E))
        emu.write(0xE0EC, 0x1234)
        emu.write(0xE0FE, 0x5678)
        for entry in (0x3871A, 0x321C0):
            restore(emu, entry)
        assert emu.read(0xE0EC) == low << 8 | 0x34
        assert emu.read(0xE0FE) == high << 8 | 0x78
        assert [emu.read(a) for a in (0xE3DA, 0xE4C4, 0xE3C2, 0xE4AC)] == [0, 0xFFFE, 0xFFFF, 0]
        assert bytes(emu.mem.image[0x4000:0x8000]) == target
    assert bytes(emu.mem.image[:0x4000]) == firmware[:0x4000]
    assert bytes(emu.mem.image[0x8000:]) == firmware[0x8000:]
    with pytest.raises(ValueError, match="integer"):
        edit_storage(source, {base + 0x24A: 0xFFFF})
    put_record(raw, base + 0x62, b"\x00\x00" + raw[base + 0x64:base + 0x6E])
    raw[0x7FE:0x800] = _crc(raw[0x400:0x400 + length], 0xF).to_bytes(2, "little")
    source = make_snapshot("MS42", "retained", raw, software_id="0110C6")
    with pytest.raises(ValueError, match="same draft"):
        edit_storage(source, {base + 0x6A: 19}, calibration=firmware)


def test_ms43_additional_edits_restore_at_native_boundaries_and_preserve_padding(native_storage):
    emu, peer, firmware = native_storage
    put_record(peer.storage, 0x27C, b"\x19\xA6")
    put_record(peer.storage, 0x2A0, words(0x1234, 0) + b"\x80" * 64)
    put_record(peer.storage, 0x2F2, words(0, 0, 123, 456, 789, 900))
    put_record(peer.storage, 0x3D4, b"\x39\xB7")
    source = make_snapshot("MS43", "eeprom", peer.storage, software_id="430069")
    for byte, word, flag in ((0, 0, 0), (255, 0xFFFF, 0xFFFF)):
        changes = {0x27C: byte, 0x2A0: word, 0x2A2: flag,
                   0x2F6: 0, 0x2F8: 0xFFFE, 0x2FA: word, 0x2FC: 0xFFFF - word, 0x3D4: byte}
        target = edit_storage(source, changes)["data"]
        allowed = {0x27C, 0x27E, 0x27F, *range(0x2A0, 0x2A4), 0x2E4, 0x2E5,
                   *range(0x2F6, 0x300), 0x3D4, 0x3D6, 0x3D7}
        assert all(old == new or offset in allowed
                   for offset, (old, new) in enumerate(zip(source["data"], target)))
        assert target[0x27D] == 0xA6 and target[0x3D5] == 0xB7
        peer.storage[:] = target
        writes = peer.storage_write_attempts
        for entry in (0xBDC2C, 0xDB79C, 0xB6D00, 0xDB4F8, 0xB2A94):
            restore(emu, entry)
        assert emu.mem._physical_read(0x40C65) == byte
        assert read_word(emu, 0xE304) == word
        assert bool(emu.mem.read_word_direct(0xFD3A) & 0x4000) == bool(flag)
        assert [read_word(emu, a) for a in (0x40D60, 0x40ECE, 0x40CC8, 0x40E36)] == [0, 0xFFFE, word, 0xFFFF - word]
        assert emu.mem._physical_read(0x401F3) == byte
        assert read_word(emu, 0x4090C) == byte << 8
        assert read_word(emu, 0x408FC) | read_word(emu, 0x408FE) << 16 == byte * 1800
        assert bytes(peer.storage) == target and peer.storage_write_attempts == writes
    for offset, size in ((0x27C, 2), (0x2A0, 68), (0x2F2, 12), (0x3D4, 2)):
        peer.storage[offset + size] ^= 1
    rejected = bytes(peer.storage)
    for entry in (0xDB79C, 0xB6D00, 0xDB4F8, 0xB2A94):
        restore(emu, entry)
    assert emu.mem._physical_read(0x40C65) == read_word(emu, 0xE304) == 0
    assert not emu.mem.read_word_direct(0xFD3A) & 0x4000
    assert [read_word(emu, a) for a in (0x40D60, 0x40ECE, 0x40CC8, 0x40E36)] == [0, 0, 0xFFFF, 0xFFFF]
    assert emu.mem._physical_read(0x401F3) == firmware[0x70B10]
    assert bytes(peer.storage) == rejected and peer.storage_write_attempts == writes
    assert bytes(emu.mem.image) == firmware
    with pytest.raises(ValueError, match="one of"):
        edit_storage(source, {0x2A2: 1})
    with pytest.raises(ValueError, match="integer"):
        edit_storage(source, {0x2F8: 0xFFFF})


def test_ms43_expanded_raw_fields_follow_native_copies_with_separate_edit_support(native_storage):
    emu, peer, firmware = native_storage
    byte_records = (
        (0x3C, bytes.fromhex("12345678"), 0xA4C16, (0x40172, 0x40173, 0x40174, 0x40175)),
        (0x1A0, bytes.fromhex("213243546576"), 0xC0000, tuple(range(0x410DA, 0x410E0))),
        (0x32A, b"\x67\x89", 0xC3D5A, (0x4046D, 0x40453)),
        (0x3D0, b"\xA3\xB4", 0xA4550, (0x40164, 0x40165)),
        (0x3EA, b"\xC5\xD6", 0xBF8E6, (0x404C1,)),
    )
    word_records = ((0x300, 0x1234, 0xBBEDE, 0x404CA, "0.14221 ×"),
                    (0x354, 0x3456, 0xAB112, 0xE2A0, 0x3456),
                    (0x3D8, 0x5678, 0xD90F2, 0x40830, "86.4688 ×"))
    for offset, payload, _, _ in byte_records:
        put_record(peer.storage, offset, payload)
    for offset, value, _, _, _ in word_records:
        put_record(peer.storage, offset, words(value))
    put_record(peer.storage, 0x37E, b"\x01\x03")
    before = bytes(peer.storage)
    writes = peer.storage_write_attempts
    source = make_snapshot("MS43", "eeprom", before, software_id="430069")
    editable = {field["offset"] for field in editable_fields(source)}
    rows = {row["address"]: row for row in inspect_storage_records(before, "MS43", "430069", "eeprom")}
    restore(emu, 0xBDC2C)
    for offset, payload, entry, addresses in byte_records:
        restore(emu, entry)
        assert bytes(emu.mem._physical_read(a) for a in addresses) == payload[:len(addresses)]
        assert len(rows[offset]["values"]) == len(addresses)
        assert (offset in editable) == (offset in (0x32A, 0x3EA))
    for offset, value, entry, address, display in word_records:
        restore(emu, entry)
        assert read_word(emu, address) == value
        assert rows[offset]["values"][0][1] == display
        assert offset in editable
    restore(emu, 0xBF5F0)
    assert rows[0x37E]["values"] == [("Cycle state byte 1 (raw)", "0x01 (1)"),
                                     ("Cycle state byte 2 (raw)", "0x03 (3)")]
    assert emu.mem._physical_read(0x40492) == emu.mem._physical_read(0x40493) == 0
    assert emu.mem.read_word_direct(0xFD52) & 0x10
    assert 0x37E not in editable
    assert bytes(peer.storage) == before and peer.storage_write_attempts == writes
    assert bytes(emu.mem.image) == firmware


def test_ms43_fault_layout_and_capped_native_count_differ_from_ms42(native_storage):
    emu, peer, firmware = native_storage
    payload = bytearray(126)
    payload[:4] = b"\x34\x12\x56\xA7"
    payload[4:14] = bytes(range(10))
    snapshots = [bytes((0x40 if i % 2 else 0,)) + bytes((i + 1,)) * 10 for i in range(10)]
    payload[14:124] = b"".join(snapshots)
    payload[124:126] = b"\xFF\xB8"
    put_record(peer.storage, 0x56, payload)
    before = bytes(peer.storage)
    writes = peer.storage_write_attempts
    restore(emu, 0xBDC2C)
    emu.mem.write_word_direct(0xFDB2, 0)
    restore(emu, 0xAB974)
    assert read_word(emu, 0x41972) == 0x1234 and emu.mem._physical_read(0x4083C) == 0x56
    assert emu.mem._physical_read(0x401BB) == emu.mem._physical_read(0x401BC) == 10
    for index, snapshot in enumerate(snapshots):
        assert emu.mem._physical_read(0x41974 + index) == index
        assert bytes(emu.mem._physical_read(0x411DA + 12 * index + n) for n in range(11)) == snapshot
    assert emu.mem.read_word_direct(0xFDB2) == 0x2AA
    row = next(row for row in inspect_storage_records(before, "MS43", "430069", "eeprom")
               if row["address"] == 0x56)
    values = dict(row["values"])
    assert values["Stored count"] == 255
    assert values["Internal fault IDs"] == tuple(range(10))
    assert sum(label.startswith("Fault slot") for label in values) == 10
    assert values["Fault slot 2, internal ID 1"] == "active-bit set; " + snapshots[1].hex(" ").upper()
    assert row["warnings"] == ["Stored count exceeds ten; native restore uses only the first ten slots."]
    assert bytes(peer.storage) == before and peer.storage_write_attempts == writes
    assert bytes(emu.mem.image) == firmware
