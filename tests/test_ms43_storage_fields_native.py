"""Original record readers qualify the expanded MS43 stored-data views.

Peripheral replies and saved field values are supplied. The actual firmware
performs record reads, integrity checks, copies, clamps and flag publication.
"""

from ms42_ms43_diagnostics import inspect_storage_records
from ms42_ms43_storage_edit import edit_storage
from tests.test_ms42_ms43_storage_inspection import put_record
from tests.test_ms43_storage_edit_native import (
    native as native,
    native_storage as native_storage,
    read_word,
    restore,
    snapshot_with_records,
)


def words(*values):
    return b"".join(value.to_bytes(2, "little") for value in values)


def test_edited_averaged_knock_cells_reload_to_all_six_native_banks(native_storage):
    emu, peer, firmware = native_storage
    source = snapshot_with_records(peer, 1, 0)
    raw = bytearray(source["data"])
    cells = bytearray(range(32, 96))
    cells[7] = 0xFE  # Existing out-of-range data must not be normalized by another edit.
    cells[32] = 97  # The middle-cell test must change its previous value, too.
    payload = words(0x4567, 0xFFFF) + cells
    put_record(raw, 0x2A0, payload)
    from ms42_ms43_storage import make_snapshot
    source = make_snapshot("MS43", "eeprom", raw, software_id="430069")
    edited = edit_storage(source, {0x2A4: 0, 0x2C4: 64, 0x2E3: 128})
    target = edited["data"]
    changed = {offset for offset, (old, new) in enumerate(zip(source["data"], target)) if old != new}
    assert changed <= {0x2A4, 0x2C4, 0x2E3, 0x2E4, 0x2E5}
    assert target[0x2A0:0x2A4] == payload[:4]
    expected = bytearray(payload[4:])
    expected[0], expected[32], expected[63] = 0, 64, 128
    assert target[0x2AB] == 0xFE
    expected[7] = 128  # Native restore clamps the unchanged stored byte.
    peer.storage[:] = target
    writes = peer.storage_write_attempts
    restore(emu, 0xBDC2C)
    restore(emu, 0xB6D00)
    for bank in range(6):
        assert bytes(emu.mem._physical_read(0x40221 + bank * 64 + index)
                     for index in range(64)) == bytes(expected)
    assert read_word(emu, 0xE304) == 0x4567
    assert emu.mem.read_word_direct(0xFD3A) & 0x4000
    assert bytes(peer.storage) == target and peer.storage_write_attempts == writes

    # An explicit valid replacement changes only this cell and its native CRC.
    corrected = edit_storage(edited, {0x2AB: 64})["data"]
    assert {i for i, (old, new) in enumerate(zip(target, corrected)) if old != new} <= {0x2AB, 0x2E4, 0x2E5}
    assert corrected[0x2AB] == 64
    peer.storage[:] = corrected
    restore(emu, 0xB6D00)
    for bank in range(6):
        assert emu.mem._physical_read(0x40221 + bank * 64 + 7) == 64
    assert bytes(peer.storage) == corrected and peer.storage_write_attempts == writes
    assert bytes(emu.mem.image) == firmware


def test_expanded_readonly_layouts_match_native_restoration_and_preserve_padding(native_storage):
    emu, peer, firmware = native_storage
    records = {
        0x28A: words(0x1234, 0x5678) + bytes((0x65, 0x54))
               + bytes(range(0x81, 0x87)) + bytes(range(0x91, 0x97)) + b"\x01\x02",
        0x2A0: words(0x2345, 0xFFFF) + b"\x81" + bytes(range(1, 64)),
        0x304: words(0x8123, 0x2345, 0x3456, 0x4567, 0x5678)
               + bytes.fromhex("61626364") + words(0x7182, 0x8394) + b"\x75\xD7",
        0x31A: b"\x02\x00",
        0x322: bytes.fromhex("8182838485d9"),
        0x32E: words(0x1122, 0x2233, 0x3344, 0x4455, 0x5566, 0x6677, 0x7788, 0x8899)
               + bytes.fromhex("a1a2a3a4") + words(0xAABB, 0xBBCC),
        0x348: words(0x1122, 0x3344, 0x5566, 0x7788, 0x5555),
        0x358: words(0x1122, 0x2233, 0x3344, 0x4455, 0x5566, 0x6677, 0x7788, 0x8899)
               + b"\xA5\xB6",
        0x36C: words(0x1122, 0x3344, 0x8123, 0x8234, 0x8345, 0x8456, 0x8567, 0x8678),
        0x3DC: words(0xA123, 0xB456),
    }
    for offset, payload in records.items():
        put_record(peer.storage, offset, payload)
    before = bytes(peer.storage)
    writes = peer.storage_write_attempts
    restore(emu, 0xBDC2C)
    assert not emu.mem.read_word_direct(0xFD46) & 0x20

    # Table layout, high-byte copies, exact-one flags and six-bank clamp.
    restore(emu, 0xB496E)
    assert (read_word(emu, 0x40C0C), read_word(emu, 0x40C0A)) == (0x1234, 0x5678)
    assert (emu.mem._physical_read(0x40951), emu.mem._physical_read(0x40ABF)) == (0x65, 0x54)
    for start, expected in ((0x40A85, range(0x81, 0x87)), (0x40BF3, range(0x91, 0x97))):
        assert bytes(emu.mem._physical_read(start + index) for index in range(6)) == bytes(expected)
    assert emu.mem.read_word_direct(0xFD3A) & 0x30 == 0x10
    restore(emu, 0xB6D00)
    for bank in range(6):
        assert bytes(emu.mem._physical_read(0x40221 + bank * 64 + index)
                     for index in range(64)) == b"\x80" + bytes(range(1, 64))

    # Noncontiguous words/bytes are traced to actual consumers, not a schema echo.
    for offset, entry, word_addresses, byte_addresses in (
        (0x304, 0xBA4C0, (0x40F5C, 0x40F5E, 0x40F60, 0x40F62, 0x40F64,
                         0x40F58, 0x40F5A), (0x403F8, 0x403F9, 0x403FA, 0x403FB, 0x403F6)),
        (0x32E, 0xC3DF6, (0x40706, 0x405C4, 0x405C2, 0x405B2, 0x405B4,
                         0x4068A, 0x40688, 0x41162, 0x411D0, 0x411D2),
                         (0x40454, 0x4043C, 0x40122, 0x4043F)),
        (0x348, 0x9B2F2, (0xE176, 0xE178, 0xE172, 0xE174), ()),
        (0x358, 0x914CC, (0xE092, 0xE094, 0xE0A2, 0x404E6, 0x404E8,
                         0x404EA, 0x404EC, 0x404EE), (0x40027, 0x40028)),
        (0x36C, 0xA20AC, (0xE1D4, 0xE1D6, 0x4075A, 0x4075C, 0x4075E,
                         0x40760, 0x40762, 0x40764), ()),
        (0x3DC, 0x956BC, (0x405A4, 0x405A6), ()),
    ):
        restore(emu, entry)
        payload = records[offset]
        if offset == 0x304:
            expected_words = payload[:10] + payload[14:18]
            expected_bytes = payload[10:14] + payload[18:19]
        elif offset == 0x32E:
            expected_words = payload[:16] + payload[20:24]
            expected_bytes = payload[16:20]
        else:
            expected_words = payload[:len(word_addresses) * 2]
            expected_bytes = payload[len(word_addresses) * 2:]
        assert b"".join(read_word(emu, address).to_bytes(2, "little")
                        for address in word_addresses) == expected_words
        assert bytes(emu.mem._physical_read(address) for address in byte_addresses) == (
            expected_bytes if byte_addresses else b"")
    assert not emu.mem.read_word_direct(0xFD14) & 1  # 5555 throttle marker.
    restore(emu, 0x93748)
    assert emu.mem.read_word_direct(0xFD10) & 0xC == 4
    restore(emu, 0xAD556)
    assert bytes(emu.mem._physical_read(address) for address in (0xE2BB, 0xE2BD, 0xE2BF, 0xE2C1, 0xE2C3)) == records[0x322][:5]

    decoded = {record["address"]: record for record in
               inspect_storage_records(before, "MS43", "430069", "eeprom")}
    assert sum(len(decoded[offset]["values"]) for offset in records) == 142
    assert len(decoded[0x304]["values"]) == 12  # 0x317 is padding, not another history byte.
    assert len(decoded[0x322]["values"]) == 5  # 0x327 is padding, not an adaptation.
    assert all(decoded[offset]["crc_valid"] for offset in records)
    assert bytes(peer.storage) == before and peer.storage_write_attempts == writes
    assert bytes(emu.mem.image) == firmware
