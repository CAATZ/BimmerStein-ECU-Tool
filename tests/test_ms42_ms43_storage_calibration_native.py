"""Edited backups reload through exact stock firmware using the selected local tune.

The warm EEPROM peer and supplied adaptation values are test inputs; these
bounded reader calls do not prove physical storage writes or engine behavior.
"""

import pytest

from checksum import _crc
from ms42_ms43_storage import make_snapshot
from ms42_ms43_storage_edit import editable_fields, edit_storage
from tests.test_ms42_ms43_config_native import native as native
from tests.test_ms42_ms43_storage_edit_native import native_selection
from tests.test_ms42_ms43_storage_edit_native import restore_record
from tests.test_ms42_ms43_storage_inspection import put_record
from tests.test_ms43_storage_edit_native import native_storage as native_storage, read_word, restore


def words(values):
    return b"".join((value & 0xFFFF).to_bytes(2, "little") for value in values)


@pytest.mark.parametrize("boundary", ("minimum", "maximum"))
def test_ms42_calibrated_idle_fuel_and_endpoints_reload_at_native_bounds(native, boundary):
    emu = native("MS42")
    raw = bytearray(emu.mem.image[0x4000:0x8000])
    # Custom thresholds deliberately differ from stock: they must come from this tune.
    for offset, value in {0x487E0: 0x7400, 0x487DE: 0x8C00,
                          0x487E6: 0x7500, 0x487E4: 0x8B00,
                          0x486B2: 200, 0x486B0: 400, 0x48706: 500, 0x48704: 800,
                          0x480E8: (-750 & 0xFFFF), 0x480E6: 650}.items():
        emu.mem.image[offset:offset + 2] = value.to_bytes(2, "little")
    emu.mem.image[0x48300], emu.mem.image[0x482FF] = 105, 145
    tune = bytes(emu.mem.image)
    base = 0x410
    put_record(raw, base + 0x62, words((300, 300, 600, 600)) + b"\x82\x83\x00\xA5")
    put_record(raw, base + 0x78, words((0x8000,) * 5))
    put_record(raw, base + 0x24A, words((123, 456, 0, 0, 789, 900)))
    length = int.from_bytes(raw[0x7F6:0x7F8], "little")
    raw[0x7FE:0x800] = _crc(raw[0x400:0x400 + length], 0xF).to_bytes(2, "little")
    source = make_snapshot("MS42", "retained", raw, software_id="0110C6")
    fields = [field for field in editable_fields(source, calibration=tune) if field["calibration_dependent"]]
    assert len(fields) == 14
    changes = {field["offset"]: field[boundary] for field in fields}
    changes[base + 0x78] = 0x1234
    target = edit_storage(source, changes, calibration=tune)["data"]
    assert native_selection(emu, target) == 1
    emu.write(0xF94A, emu.read(0xF92C))
    emu.write(0xF94C, emu.read(0xF92E))
    for entry in (0x2B324, 0x31E3E, 0x3871A, 0x321C0):
        result = emu.call(entry, max_steps=100_000)
        assert result.exit_reason == "stop_at"
    assert emu.read_byte(0xE193) == changes[base + 0x10A]
    assert emu.read_byte(0xE194) == changes[base + 0x10A]
    for stored, address in ((0x78, 0xE518), (0x7A, 0xE3C8), (0x7C, 0xE3CA),
                            (0x7E, 0xE4B2), (0x80, 0xE4B4), (0x62, 0xE0E2),
                            (0x64, 0xE0E4), (0x66, 0xE0C0), (0x68, 0xE0C2),
                            (0x24E, 0xE3B8), (0x250, 0xE4A2)):
        assert emu.read(address) == changes[base + stored] & 0xFFFF
    assert [emu.read(a) for a in (0xE3DA, 0xE4C4, 0xE3C2, 0xE4AC)] == [123, 456, 789, 900]
    assert emu.read(0xFD5C) & 0x8000
    assert bytes(emu.mem.image[:0x4000]) == tune[:0x4000]
    assert bytes(emu.mem.image[0x8000:]) == tune[0x8000:]
    assert bytes(emu.mem.image[0x4000:0x8000]) == target


def test_ms43_calibrated_idle_fuel_and_postcat_reload_both_native_bounds(native_storage):
    emu, peer, firmware = native_storage
    for offset, value in {0x7094C: 0x7300, 0x7094A: 0x8D00,
                          0x70954: 0x7400, 0x70952: 0x8C00,
                          0x70E7E: (-750 & 0xFFFF), 0x70E7C: 650}.items():
        emu.mem.image[offset:offset + 2] = value.to_bytes(2, "little")
    emu.mem.image[0x70C21], emu.mem.image[0x70C20] = 109, 149
    tune = bytes(emu.mem.image)
    put_record(peer.storage, 0x280, words((0, 0, 0)) + b"\x80\xA5")
    put_record(peer.storage, 0x2E6, words((0x8000,) * 5))
    put_record(peer.storage, 0x2F2, words((0, 0, 123, 456, 789, 900)))
    writes = peer.storage_write_attempts
    for boundary in ("minimum", "maximum"):
        source = make_snapshot("MS43", "eeprom", peer.storage, software_id="430069")
        fields = [field for field in editable_fields(source, calibration=tune) if field["calibration_dependent"]]
        assert len(fields) == 8
        changes = {field["offset"]: field[boundary] for field in fields}
        changes[0x2E6] = 0x1234
        target = edit_storage(source, changes, calibration=tune)["data"]
        peer.storage[:] = target
        for entry in (0xBDC2C, 0xB1928, 0xDB310, 0xDB4F8):
            restore(emu, entry)
        assert emu.mem._physical_read(0x401ED) == changes[0x286]
        assert emu.mem._physical_read(0x401EE) == changes[0x286]
        for stored, address in ((0x2E6, 0x40C66), (0x2E8, 0x40CCE), (0x2EA, 0x40CD0),
                                (0x2EC, 0x40E3C), (0x2EE, 0x40DF0),
                                (0x2F2, 0x40CBE), (0x2F4, 0x40E2C)):
            assert read_word(emu, address) == changes[stored] & 0xFFFF
        assert [read_word(emu, a) for a in (0x40D60, 0x40ECE, 0x40CC8, 0x40E36)] == [123, 456, 789, 900]
        assert bytes(peer.storage) == target
        assert peer.storage_write_attempts == writes
    assert bytes(emu.mem.image) == tune
    # Deliberately changed fixture bytes belong only to calibration data.
    assert tune[:0x70000] == firmware[:0x70000]
    assert tune[0x80000:] == firmware[0x80000:]


@pytest.mark.parametrize("valid", (False, True))
def test_ms42_new_raw_views_match_native_readers_without_inventing_physical_units(native, valid):
    from ms42_ms43_diagnostics import inspect_storage_records

    emu = native("MS42")
    emu.enable_flash()
    payload = words((0x1234, 0x5678)) + b"\x65\x54" + bytes(range(0x81, 0x8D)) + b"\x01\x02"
    restore_record(emu, 0x200, payload, 0x2DC24, valid)
    assert [emu.read(a) for a in (0xD042, 0xD1B0)] == (
        [0x1234, 0x5678] if valid else [emu.mem.image[0x4830B] << 8, emu.mem.image[0x4830A] << 8])
    assert [emu.read_byte(a) for a in (0xD06D, 0xD1DB)] == ([0x65, 0x54] if valid else [0, 0])
    assert bytes(emu.read_byte(0xD1A3 + i) for i in range(6)) == (payload[6:12] if valid else b"\x80" * 6)
    assert bytes(emu.read_byte(0xD311 + i) for i in range(6)) == (payload[12:18] if valid else b"\x80" * 6)
    assert emu.read(0xFD16) & 6 == (2 if valid else 0)
    restore_record(emu, 0x70, bytes.fromhex("8182838485a5"), 0x27FB4, valid)
    assert bytes(emu.read_byte(a) for a in (0xFA9D, 0xFA9F, 0xFAA1, 0xFAA3, 0xFAA5)) == (
        bytes.fromhex("8182838485") if valid else b"\x80" * 5)
    rows = inspect_storage_records(bytes(emu.mem.image[0x4000:0x8000]), "MS42", "0110C6", "retained")
    vanos = next(row for row in rows if row["address"] == 0x4210)
    adaptation = next(row for row in rows if row["address"] == 0x4080)
    assert len(vanos["values"]) == (18 if valid else 0)
    assert len(adaptation["values"]) == (5 if valid else 0)


def test_ms42_raw_learned_field_edits_reload_without_changing_padding_or_other_slots(native):
    emu = native("MS42")
    emu.enable_flash()
    raw = bytearray(emu.mem.image[0x4000:0x8000])
    base = 0x410
    for offset, size in ((0x5C, 4), (0x70, 6), (0x1EE, 2), (0x200, 20),
                         (0x21A, 2), (0x240, 8), (0x258, 6)):
        put_record(raw, base + offset, b"\x00" * size)
    length = int.from_bytes(raw[0x7F6:0x7F8], "little")
    raw[0x7FE:0x800] = _crc(raw[0x400:0x400 + length], 0xF).to_bytes(2, "little")
    source = make_snapshot("MS42", "retained", raw, software_id="0110C6")
    changes = {base + offset: value for offset, value in {
        0x5C: 0xFEDC, 0x70: 0x91, 0x74: 0x95, 0x1EE: 0x83, 0x1EF: 0x84,
        0x200: 0x1234, 0x202: 0x5678, 0x204: 0x65, 0x205: 0x54,
        0x206: 0x81, 0x211: 0x8C, 0x212: 1, 0x213: 1, 0x21A: 0x67,
        0x240: 0xA1, 0x245: 0xA6, 0x246: 0x89AB,
        0x258: 0x2345, 0x25A: 0x6789, 0x25C: 0xBC}.items()}
    target = edit_storage(source, changes)["data"]
    assert target[:0x400] == raw[:0x400] and target[0x800:] == raw[0x800:]
    assert native_selection(emu, target) == 1
    emu.write(0xF94A, emu.read(0xF92C))
    emu.write(0xF94C, emu.read(0xF92E))
    for entry in (0x36C4C, 0x27FB4, 0x3F3C0, 0x2DC24, 0x2633C, 0x3F4E8, 0x348BA):
        result = emu.call(entry, max_steps=100_000)
        assert result.exit_reason == "stop_at"
    for address, expected in ((0xFA36, 0xFEDC), (0xD042, 0x1234), (0xD1B0, 0x5678),
                              (0xCE92, 0x89AB), (0xE1C6, 0x2345), (0xE1C8, 0x6789)):
        assert emu.read(address) == expected
    for address, expected in ((0xFA9D, 0x91), (0xFAA5, 0x95), (0xC1A6, 0x83), (0xC188, 0x84),
                              (0xD06D, 0x65), (0xD1DB, 0x54), (0xD1A3, 0x81), (0xD316, 0x8C),
                              (0xFAF8, 0x67), (0xC17F, 0xA1), (0xC33E, 0xA6), (0xC1E3, 0xBC)):
        assert emu.read_byte(address) == expected
    assert emu.read(0xFD16) & 6 == 6
    assert bytes(emu.mem.image[0x4000:0x8000]) == target


def test_ms43_raw_fields_produced_by_editor_reload_in_original_consumers(native_storage):
    emu, peer, firmware = native_storage
    records = (
        (0x304, 20, 0xBA4C0, (0x40F5C, 0x40F5E, 0x40F60, 0x40F62, 0x40F64, 0x40F58, 0x40F5A),
         (0x403F8, 0x403F9, 0x403FA, 0x403FB, 0x403F6)),
        (0x32E, 24, 0xC3DF6, (0x40706, 0x405C4, 0x405C2, 0x405B2, 0x405B4, 0x4068A, 0x40688, 0x41162, 0x411D0, 0x411D2),
         (0x40454, 0x4043C, 0x40122, 0x4043F)),
        (0x348, 10, 0x9B2F2, (0xE176, 0xE178, 0xE172, 0xE174), ()),
        (0x358, 18, 0x914CC, (0xE092, 0xE094, 0xE0A2, 0x404E6, 0x404E8, 0x404EA, 0x404EC, 0x404EE), (0x40027, 0x40028)),
        (0x36C, 16, 0xA20AC, (0xE1D4, 0xE1D6, 0x4075A, 0x4075C, 0x4075E, 0x40760, 0x40762, 0x40764), ()),
        (0x3DC, 4, 0x956BC, (0x405A4, 0x405A6), ()),
    )
    for offset, size, *_ in records:
        put_record(peer.storage, offset, bytes(size))
    for offset, payload in ((0x28A, bytes(20)), (0x31A, bytes(2)), (0x322, bytes(6))):
        put_record(peer.storage, offset, payload)
    source = make_snapshot("MS43", "eeprom", peer.storage, software_id="430069")
    selected = {offset for offset, *_ in records} | {0x28A, 0x31A, 0x322}
    fields = [field for field in editable_fields(source) if field["record_offset"] in selected]
    changes = {field["offset"]: (field["choices"][-1] if field["choices"] else
                                 0x1234 if field["width"] == 2 else 0x91) for field in fields}
    target = edit_storage(source, changes)["data"]
    peer.storage[:] = target
    writes = peer.storage_write_attempts
    for offset, size, entry, word_addresses, byte_addresses in records:
        restore(emu, entry)
        assert all(read_word(emu, address) == 0x1234 for address in word_addresses)
        assert all(emu.mem._physical_read(address) == 0x91 for address in byte_addresses)
    assert emu.mem.read_word_direct(0xFD14) & 1  # Canonical AAAA invalid-learning marker.
    restore(emu, 0xB496E)
    assert [read_word(emu, a) for a in (0x40C0C, 0x40C0A)] == [0x1234, 0x1234]
    assert all(emu.mem._physical_read(a) == 0x91 for a in (0x40951, 0x40ABF, 0x40A85, 0x40A8A, 0x40BF3, 0x40BF8))
    assert emu.mem.read_word_direct(0xFD3A) & 0x30 == 0x30
    restore(emu, 0x93748)
    assert emu.mem.read_word_direct(0xFD10) & 0xC == 0xC
    restore(emu, 0xAD556)
    assert all(emu.mem._physical_read(a) == 0x91 for a in (0xE2BB, 0xE2BD, 0xE2BF, 0xE2C1, 0xE2C3))
    assert bytes(emu.mem.image) == firmware and bytes(peer.storage) == target
    assert peer.storage_write_attempts == writes


def test_ms43_counter_ffff_sentinel_marks_unavailable_and_edited_value_reloads(native_storage):
    emu, peer, firmware = native_storage
    put_record(peer.storage, 0x32E, bytes(14) + b"\xFF\xFF" + bytes(8))
    source = make_snapshot("MS43", "eeprom", peer.storage, software_id="430069")
    for snapshot, expected in ((source, 0xFFFF), (edit_storage(source, {0x33C: 0xFFFE}), 0xFFFE)):
        peer.storage[:] = snapshot["data"]
        emu.mem.write_word_direct(0xFD4A, emu.mem.read_word_direct(0xFD4A) & ~0x20)
        writes = peer.storage_write_attempts
        restore(emu, 0xC3DF6)
        assert read_word(emu, 0x406FE) == read_word(emu, 0x41162) == expected
        assert bool(emu.mem.read_word_direct(0xFD4A) & 0x20) == (expected == 0xFFFF)
        assert peer.storage_write_attempts == writes and bytes(peer.storage) == snapshot["data"]
    assert bytes(emu.mem.image) == firmware
