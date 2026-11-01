"""Original firmware validates, restores and saves the additional storage fields."""

import os
from pathlib import Path

import pytest

from checksum import _crc
from ms42_ms43_diagnostics import (inspect_storage_records, storage_operating_counter,
                                  select_ms42_slot, _ms43_start_triplet, _ms43_sync_copies)
from ms42_ms43_storage import make_snapshot
from ms42_ms43_storage_edit import edit_storage
from tests.test_ms42_ms43_config_native import native as native
from tests.test_ms42_ms43_config_revisions import private_sources as private_sources
from tests.test_ms42_ms43_storage_edit_native import native_selection
from tests.test_ms42_ms43_storage_inspection import put_record
from tests.test_ms43_storage_edit_native import native_storage as native_storage, restore, read_word
from tests.test_ms42_ms43_storage_completion import counter_record


def admit_journal(emu, raw):
    length = int.from_bytes(raw[0x7F6:0x7F8], "little")
    raw[0x7FE:0x800] = _crc(raw[0x400:0x400 + length], 15).to_bytes(2, "little")
    assert native_selection(emu, raw) == 1
    emu.write(0xF94A, emu.read(0xF92C))
    emu.write(0xF94C, emu.read(0xF92E))


def test_ms42_knock_edits_replicate_to_six_banks_and_reject_bad_crc(native):
    emu = native("MS42")
    emu.enable_flash()  # Native writes to unarmed ROM are rejected, not invented writable RAM.
    firmware = bytes(emu.mem.image)
    raw = bytearray(firmware[0x4000:0x8000])
    base = 0x410
    put_record(raw, base + 0x18, bytes(range(64)) + b"\x34\x12")
    put_record(raw, base + 0x1FC, b"\x19\xA5")
    admit_journal(emu, raw)
    source = make_snapshot("MS42", "retained", raw, software_id="0110C6")
    target = edit_storage(source, {base + 0x18: 128, base + 0x38: 0,
                                   base + 0x57: 64, base + 0x58: 0xFFFF, base + 0x1FC: 255})["data"]
    admit_journal(emu, bytearray(target))
    restore(emu, 0x2FBE4)
    expected = bytes((128,)) + bytes(range(1, 32)) + b"\x00" + bytes(range(33, 63)) + b"\x40"
    for bank in range(6):
        assert bytes(emu.mem.read_byte(0xC486 + bank * 64 + i) for i in range(64)) == expected
    assert emu.read(0xFA98) == 0xFFFF
    restore(emu, 0x326A6)
    assert emu.mem.read_byte(0xE517) == 255 and target[base + 0x1FD] == 0xA5
    assert bytes(emu.mem.image[:0x4000]) == firmware[:0x4000]
    assert bytes(emu.mem.image[0x8000:]) == firmware[0x8000:]
    corrupt = bytearray(target)
    corrupt[base + 0x5A] ^= 1
    admit_journal(emu, corrupt)
    restore(emu, 0x2FBE4)
    for bank in range(6):
        assert bytes(emu.mem.read_byte(0xC486 + bank * 64 + i) for i in range(64)) == b"\x80" * 64
    assert emu.read(0xFA98) == 0
    assert bytes(emu.mem.image[0x4000:0x8000]) == corrupt
    with pytest.raises(ValueError, match="integer"):
        edit_storage(source, {base + 0x18: 129})


def test_ms42_operating_counter_native_recovery_and_editor_crc(native):
    emu = native("MS42")
    emu.enable_flash()
    firmware = bytes(emu.mem.image)
    raw = bytearray(firmware[0x4000:0x8000])
    base = 0x410
    for copies in ((0, 1, 2), (0xFFFE, 0xFFFF, 0), (0xFFFF, 0, 1),
                   (0xBEEF, 0x1235, 0x1236), (0x1234, 0xBEEF, 0x1236),
                   (0x1234, 0x1235, 0xBEEF), (0xFFFF,) * 3, (0, 10, 20)):
        raw[base:base + 6] = counter_record(*copies)
        admit_journal(emu, raw)
        restore(emu, 0x1FD2E)
        value = storage_operating_counter(raw[base:base + 6])
        assert emu.read(0xCF04) == (0 if value is None else value)
    raw[base:base + 6] = counter_record(1, 2, 3)
    admit_journal(emu, raw)
    source = make_snapshot("MS42", "retained", raw, software_id="0110C6")
    for value in (0, 0xFFFE, 0xFFFF):
        target = edit_storage(source, {base: value})["data"]
        assert select_ms42_slot(target) == 1  # Non-CRC field edits still update the outer journal CRC.
        allowed = {*range(base, base + 6), 0x7FE, 0x7FF}
        assert all(a == b or i in allowed for i, (a, b) in enumerate(zip(raw, target)))
        assert target[base:base + 6] == counter_record(value, (value + 1) & 0xFFFF, (value + 2) & 0xFFFF)
        admit_journal(emu, bytearray(target))
        restore(emu, 0x1FD2E)
        assert emu.read(0xCF04) == value
    assert bytes(emu.mem.image[:0x4000]) == firmware[:0x4000]
    assert bytes(emu.mem.image[0x8000:]) == firmware[0x8000:]


def test_ms42_monitor_records_restore_distinct_fields_and_keep_native_rejection_behavior(native):
    emu = native("MS42")
    firmware = bytes(emu.mem.image)
    raw = bytearray(firmware[0x4000:0x8000])
    base = 0x410
    records = ((0x10E, 30, 0x1F04A), (0x1F2, 8, 0x3AD1A),
               (0x22A, 12, 0x3775A), (0x23C, 2, 0x198B0),
               (0x260, 28, 0x7329C), (0x27E, 80, 0x73430),
               (0x2DC, 2, 0x19FC6), (0x2E0, 26, 0x1BEB0), (0x2FC, 4, 0x1D108))
    for offset, length, _ in records:
        put_record(raw, base + offset, bytes(range(1, length + 1)))
    put_record(raw, base + 0x23C, b"\x07\xA5")
    admit_journal(emu, raw)
    emu.write(0xDFEA, 0)
    emu.write_byte(0xE516, 2)
    emu.write(0xE2B8, 0x9876)
    emu.write_byte(0xC2D3, 0xA5)
    for _, _, entry in records:
        restore(emu, entry)
    assert emu.read(0xE2B8) == 0x9876  # Saved by the writer, but not reloaded by this consumer.
    assert [emu.read(a) for a in (0xE2F8, 0xE2FA, 0xE2FC, 0xE2FE, 0xE300, 0xE2F2)] == [
        int.from_bytes(raw[base + 0x11E + i * 2:base + 0x120 + i * 2], "little") for i in range(6)]
    assert emu.read(0xFD7E) & 2
    assert bytes(emu.mem.read_byte(0xC2CC + i) for i in range(8)) == bytes(range(1, 8)) + b"\xA5"
    assert [emu.mem.read_byte(a) for a in (0xDFEC, 0xDFED, 0xDFEE, 0xDFEF, 0xDFF0)] == list(range(1, 6))
    assert [emu.read(a) for a in (0xDFE4, 0xDFE6, 0xDFE8)] == [0x807, 0xA09, 0xC0B]
    assert emu.mem.read_byte(0xC200) == 7
    assert bytes(emu.mem.read_byte(0xC406 + i) for i in range(28)) == bytes(range(1, 29))
    assert bytes(emu.mem.read_byte(0xCEB2 + i) for i in range(80)) == bytes(range(1, 81))
    assert (emu.mem.read_byte(0xC1DD), emu.mem.read_byte(0xC1DF), emu.mem.read_byte(0xC1DE)) == (1, 2, 255)
    addresses = (0xE7DE, 0xE7E0, 0xE7E2, 0xE7E4, 0xE7E6, 0xE7E8, 0xE7EA,
                 0xE7F0, 0xE7F2, 0xE7F4, 0xE7F6, 0xE7FC, 0xE7FE)
    assert [emu.read(a) for a in addresses] == [i * 514 + 0x201 for i in range(13)]
    assert bytes(emu.mem.read_byte(0xC3F8 + i) for i in range(3)) == b"\x01\x02\x03"
    decoded = {r["address"]: r for r in inspect_storage_records(raw, "MS42", "0110C6", "retained")}
    assert len(decoded[0x4000 + base + 0x1F2]["values"]) == 7
    assert len(decoded[0x4000 + base + 0x22A]["values"]) == 8
    assert len(decoded[0x4000 + base + 0x2FC]["values"]) == 3
    for offset, length, _ in records:
        raw[base + offset + length] ^= 1
    admit_journal(emu, raw)
    for entry in (0x3AD1A, 0x3775A, 0x198B0, 0x19FC6, 0x1BEB0, 0x1D108):
        restore(emu, entry)
    assert bytes(emu.mem.read_byte(0xC2CC + i) for i in range(8)) == b"\x00" * 8
    assert [emu.read(a) for a in (0xDFE4, 0xDFE6, 0xDFE8, *addresses)] == [0] * 16
    assert emu.mem.read_byte(0xC200) == 0
    assert (emu.mem.read_byte(0xC1DD), emu.mem.read_byte(0xC1DF)) == (1, 2)  # This reader retains RAM on rejection.
    assert bytes(emu.mem.read_byte(0xC3F8 + i) for i in range(3)) == b"\xFF" * 3
    assert bytes(emu.mem.image[:0x4000]) == firmware[:0x4000]
    assert bytes(emu.mem.image[0x8000:]) == firmware[0x8000:]


def test_ms42_native_knock_and_fault_producers_save_and_reset_restore(native, monkeypatch):
    import tests

    root = Path(os.environ.get("MS41EMU_ROOT", Path.home() / "ECU Emulator"))
    monkeypatch.setattr(tests, "__path__", [*tests.__path__, str(root / "tests")])
    from tests.test_c167_lifecycle import prepare_lifecycle

    original = native("MS42")
    firmware = bytes(original.mem.image)
    emu, _, _ = prepare_lifecycle(type(original), firmware)
    result = emu.cpu.run(emu.cpu.pc, stop_at=0x76CA0, max_steps=600_000)
    assert result.exit_reason == "stop_at", result
    for bank in range(6):
        for cell in range(64):
            emu.mem.write_byte(0xC486 + bank * 64 + cell, 120 if cell == 63 else cell + bank)
    emu.write(0xFA98, 0x7B5A)
    emu.write(0xFD58, emu.read(0xFD58) | 0x2000)
    record, descriptor = 0xC84E, 0x6468
    for index in range(12):
        emu.write_byte(record + index, 0)
    emu.write_byte(0xC62F, 0)
    emu.write_byte(0xC630, 0)
    emu.write(0xFD80, 0)
    emu.write(0xCF04, 0x1234)
    sources = [emu.read(descriptor + offset) for offset in (4, 6, 8, 10)]
    for source, value in zip(sources, (17, 34, 51, 68)):
        emu.write_byte(source, value)
    for _ in range(5):
        emu.write_byte(0xC62E, 1)
        for register, value in ((12, record), (13, descriptor), (14, 2), (15, 10)):
            emu.reg.set_word(register, value)
        restore(emu, 0x2490E)
    captured = bytes(emu.mem.read_byte(record + index) for index in range(11))
    assert captured[1:4] == b"\x0A\x01\x28"
    assert captured[4:8] == b"\x11\x22\x33\x44"
    assert captured[8:10] == b"\x12\x34"
    restore(emu, 0x75BFC)
    for register, value in ((12, 0x3C), (13, 0x14), (14, 0x53AC)):
        emu.reg.set_word(register, value)
    result = emu.call(0x75E30, max_steps=3_000_000)
    assert result.exit_reason == "stop_at", result
    raw = bytes(emu.mem.image[0x4000:0x8000])
    selected = select_ms42_slot(raw)
    assert selected == 2
    base = selected * 0x400 + 0x10
    expected = bytes(min(128, cell + 2 + firmware[0x4843C]) for cell in range(63))
    expected += bytes((min(128, 120 + firmware[0x4843C]),))
    assert raw[base + 0x18:base + 0x58] == expected
    assert raw[base + 0x58:base + 0x5A] == b"\x5A\x7B"
    assert raw[base:base + 6] == counter_record(0x1234, 0x1235, 0x1236)
    assert raw[base + 0x84:base + 0x86] == bytes((1, 45))
    assert raw[base + 0x8F:base + 0x9A] == captured
    rows = {row["address"]: row for row in inspect_storage_records(raw, "MS42", "0110C6", "retained")}
    counters = dict(rows[0x4000 + base + 0x84]["values"])["Saved fault 1 counters"]
    assert "debounce 10; occurrences 1; aging 40; operating-counter snapshot 4660" in counters
    saved_flash = bytes(emu.mem.image)
    emu.hardware.reset()
    result = emu.cpu.run(emu.cpu.pc, stop_at=0x76CA0, max_steps=600_000)
    assert result.exit_reason == "stop_at", result
    for bank in range(6):
        assert bytes(emu.mem.read_byte(0xC486 + bank * 64 + i) for i in range(64)) == expected
    assert emu.read(0xFA98) == 0x7B5A
    assert emu.read(0xCF04) == 0x1234
    assert bytes(emu.mem.image) == saved_flash
    assert saved_flash[:0x4000] == firmware[:0x4000]
    assert saved_flash[0x8000:] == firmware[0x8000:]


def test_ms43_counter_native_recovery_edit_wrap_and_save(native_storage):
    emu, peer, firmware = native_storage
    for copies in ((0x1234, 0x1235, 0x1236), (0xBEEF, 0x1235, 0x1236),
                   (0x1234, 0xBEEF, 0x1236), (0x1234, 0x1235, 0xBEEF),
                   (0xFFFE, 0xFFFF, 0), (0xFFFF, 0, 1),
                   (0xBEEF, 0, 1), (0, 10, 20), (0xFFFF, 0xFFFF, 0xFFFF)):
        peer.storage[:6] = counter_record(*copies)
        before = bytes(peer.storage)
        writes = peer.storage_write_attempts
        restore(emu, 0xD8FB2)
        expected = storage_operating_counter(peer.storage)
        assert read_word(emu, 0x40824) == (expected if expected is not None else 0)
        assert bytes(peer.storage) == before and peer.storage_write_attempts == writes
    peer.storage[:6] = counter_record(1, 2, 3)
    source = make_snapshot("MS43", "eeprom", peer.storage, software_id="430069")
    target = edit_storage(source, {0: 0xFFFF})["data"]
    peer.storage[:] = target
    restore(emu, 0xD8FB2)
    assert read_word(emu, 0x40824) == 0xFFFF
    restore(emu, 0xD84D8)
    assert read_word(emu, 0x40824) == 0
    restore(emu, 0xD8FE0)
    assert bytes(peer.storage[:6]) == counter_record(0, 1, 2)
    assert bytes(peer.storage[6:]) == target[6:]
    assert bytes(emu.mem.image) == firmware


def test_ms43_non_crc_sync_inspection_matches_native_partial_copy_recovery(native_storage):
    emu, peer, firmware = native_storage
    # This helper operates on a supplied RAM buffer; it does not authorize the full pairing state.
    for triplet in ((1, 2, 3), (255, 0, 1), (255, 0, 19), (90, 2, 3), (1, 90, 3), (1, 2, 90), (1, 8, 19)):
        raw = bytes(6) + bytes(triplet)
        for index, value in enumerate(triplet):
            emu.write_byte(0xF9C0 + index, value)
        emu.reg.set_word(12, 0xF9C0)
        emu.reg.set_word(13, 1)
        restore(emu, 0xBDCE4)
        expected = _ms43_start_triplet(raw)
        assert (emu.reg.get_word(4) & 0xFF) == (2 if expected is None else 1)
        if expected is not None:
            assert emu.mem.read_byte(0xF9C0) == expected
    value = 0x12345678
    encoded = (value, value ^ 0x00FFFF00, value ^ 0xFF0000FF)
    valid = b"".join(word.to_bytes(4, "little") for word in encoded) + b"\x35\xA5"
    peer.storage[0xA:0x18] = valid
    for broken in (None, 0, 1, 2, "all"):
        primary = bytearray(valid)
        if broken == "all":
            primary[:12] = b"\x00" * 12
        elif broken is not None:
            primary[broken * 4] ^= 0x40
        peer.storage[0x18:0x26] = primary
        before = bytes(peer.storage)
        writes = peer.storage_write_attempts
        emu.write(0xE2DA, 0xFEFE)  # Explicitly supplied flash admission, not inferred from EEPROM.
        restore(emu, 0xAF328)
        candidate = _ms43_sync_copies(peer.storage, 0x18)[2]
        assert candidate == (None if broken == "all" else value)
        assert read_word(emu, 0xE2CA) | read_word(emu, 0xE2CC) << 16 == (~value & 0xFFFFFFFF)
        assert bool(emu.read(0xFD34) & 0x10) == (broken == "all")
        assert not emu.read(0xFD34) & 0x20  # The valid backup is admitted after rejected primary.
        assert bytes(peer.storage) == before and peer.storage_write_attempts == writes
    emu.write(0xE2DA, 0xFFFF)
    restore(emu, 0xAF328)
    assert read_word(emu, 0xE2CA) | read_word(emu, 0xE2CC) << 16 == 0
    assert bytes(emu.mem.image) == firmware


def test_ms43_new_fields_reload_boundaries_save_and_crc_fallback(native_storage):
    emu, peer, firmware = native_storage
    records = ((0x300, 0xBBEDE, 0xBBF2E, (0x404CA,), 2),
               (0x32A, 0xC3D5A, 0xC3DB2, (0x4046D, 0x40453), 1),
               (0x354, 0xAB112, 0xAB15E, (0xE2A0,), 2),
               (0x3D8, 0xD90F2, 0xD9156, (0x40830,), 2),
               (0x3EA, 0xBF8E6, 0xBF94C, (0x404C1,), 1))
    for offset, *_ in records:
        put_record(peer.storage, offset, b"\x23\xA5")
    source = make_snapshot("MS43", "eeprom", peer.storage, software_id="430069")
    for high in (False, True):
        changes = {0x300: 0xFFFF if high else 0, 0x32A: 255 if high else 0,
                   0x32B: 0 if high else 255, 0x354: 0xFFFF if high else 0,
                   0x3D8: 0xFFFF if high else 0, 0x3EA: 255 if high else 0}
        target = edit_storage(source, changes)["data"]
        peer.storage[:] = target
        writes = peer.storage_write_attempts
        for offset, load, _, addresses, width in records:
            restore(emu, load)
            actual = [read_word(emu, a) if width == 2 else emu.mem._physical_read(a) for a in addresses]
            assert actual == [changes[offset + i * width] for i in range(len(addresses))]
        assert read_word(emu, 0x40826) == changes[0x3D8] and read_word(emu, 0x4082E) == 0
        assert bytes(peer.storage) == target and peer.storage_write_attempts == writes
        for offset, _, save, _, _ in records:
            peer.storage[offset:offset + 4] = b"\xFF" * 4
            restore(emu, save)
            expected = bytearray(target[offset:offset + 4])
            if offset == 0x3EA:  # Native save owns its zero padding; the editor preserves existing padding.
                expected[1] = 0
                expected[2:] = _crc(expected[:2], 15).to_bytes(2, "little")
            assert peer.storage[offset:offset + 4] == expected
    for offset, load, _, _, _ in records:
        peer.storage[offset + 2] ^= 1
        restore(emu, load)
    assert read_word(emu, 0x404CA) == 0x7FFF and read_word(emu, 0xE2A0) == 0
    assert read_word(emu, 0x40830) == read_word(emu, 0x40826) == 0x100
    assert (emu.mem._physical_read(0x4046D), emu.mem._physical_read(0x40453),
            emu.mem._physical_read(0x404C1)) == (255, 0, 0)
    assert bytes(emu.mem.image) == firmware


def test_new_ms42_record_owner_spans_are_complete_and_match_c7(private_sources):
    memory = private_sources["c7_memory"]
    for start, end in ((0x1FD2E, 0x1FE26), (0x2FBE4, 0x2FE32), (0x326A6, 0x327C2),
                       (0x1F04A, 0x1F272), (0x3AE68, 0x3B6DC),
                       (0x3AD1A, 0x3AE68), (0x3E05A, 0x3E170),
                       (0x3775A, 0x379F4), (0x198B0, 0x199A6),
                       (0x7329C, 0x73402), (0x73430, 0x735F8),
                       (0x1324C, 0x13390), (0x11A6C, 0x11B72),
                       (0x19FC6, 0x1A0EE), (0x1BBF2, 0x1BFAE), (0x1D108, 0x1D216)):
        assert all(address in memory for address in range(start, end))
        assert bytes(memory[a] for a in range(start, end)) == private_sources["c6"][start:end]


def test_x001_new_field_semantics_follow_unchanged_or_relocated_native_producers(native, private_sources):
    stock, modified = private_sources["stock"], private_sources["x001"]
    for start, end in ((0xD3D00, 0xD3F84), (0xBF716, 0xBF8E6), (0xBF942, 0xBF94C),
                       (0xC403C, 0xC41B4), (0xAB666, 0xAB85E)):
        assert stock[start - 0x80000:end - 0x80000] == modified[start - 0x80000:end - 0x80000]
    # The enclosing stopped/gear tasks changed; these complete relevant blocks moved intact.
    for start, end, target in ((0xAA042, 0xAA0B4, 0xAA03A), (0xD8D1E, 0xD8DD0, 0xD8D42)):
        assert stock[start - 0x80000:end - 0x80000] == modified[target - 0x80000:target - 0x80000 + end - start]
    for image, run_start, run_yes, run_no, temp_start, temp_stop, gear_start, gear_stop in (
        (stock, 0xAA042, 0xAA066, 0xAA076, 0xAA098, 0xAA0B4, 0xD8D2C, 0xD8D40),
        (modified, 0xAA03A, 0xAA05E, 0xAA06E, 0xAA090, 0xAA0AC, 0xD8D50, 0xD8D64),
    ):
        emu = native("MS43")
        emu.mem.image[:] = image
        for admitted in (False, True):
            emu.write(0xFD48, 8 if admitted else 0)
            emu.write(0xE29E, 0x1234)
            emu.write(0xE2A0, 0x4567)
            result = emu.run_from(run_start, stop_at=(run_yes, run_no), max_steps=100)
            assert result.exit_reason == "stop_at", result
            assert emu.read(0xE2A0) == (0x1234 if admitted else 0x4567)
            emu.write_byte(0x8433, 77)
            emu.write_byte(0x846D, 91)
            emu.write(0xE416, int(admitted))
            result = emu.run_from(temp_start, stop_at=temp_stop, max_steps=100)
            assert result.exit_reason == "stop_at", result
            assert emu.mem.read_byte(0x846D) == (77 if admitted else 91)
            assert emu.mem.read_byte(0x8453) == 77
        for factor in (128, 256, 512):
            emu.write(0x86B0, 3200)
            emu.write(0x8830, factor)
            result = emu.run_from(gear_start, stop_at=gear_stop, max_steps=1000)
            assert result.exit_reason == "stop_at", result
            assert emu.read(0x882A) == 3200 * 256 // factor
        assert bytes(emu.mem.image) == image
