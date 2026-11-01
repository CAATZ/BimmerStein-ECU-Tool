"""Native readers establish each revision's read-only stored diagnostic layout."""

from hashlib import sha256
import os
from pathlib import Path

import pytest

from ms42_ms43_diagnostics import inspect_storage_records
from ms42_ms43_storage import make_snapshot
from ms42_ms43_storage_edit import editable_fields, storage_record_checks
from tests.ms42_ms43_revision_sources import composed_image, full_image
from tests.test_ms42_ms43_config_native import native as native
from tests.test_ms42_ms43_storage_inspection import put_record
from tests.test_ms43_revision_storage_native import OWNERS, BINDINGS
from tests.test_ms43_storage_edit_native import native_storage as native_storage, restore


RECORDS = {
    "430037": (
        (0x3C, 0x3C, 4, 0xA52B0),
        (0x56, 0x54, 126, 0xAED14),
        (0xD6, 0xD4, 190, 0xC3D3C),
        (0x1A0, 0x194, 6, 0xC3C6C),
        (0x37E, 0x358, 2, 0xCBCA2),
        (0x3D0, 0x3A8, 2, 0xA4A52),
    ),
    "430055": (
        (0x3C, 0x3C, 4, 0xA43CC),
        (0x56, 0x56, 126, 0xAE116),
        (0xD6, 0xD6, 190, 0xC3B66),
        (0x1A0, 0x196, 6, 0xC3A96),
        (0x37E, 0x36C, 2, 0xBFEAE),
        (0x3D0, 0x3BC, 2, 0xA3854),
    ),
    "430064": (
        (0x3C, 0x3C, 4, 0xA44FC),
        (0x56, 0x56, 126, 0xAE2F4),
        (0xD6, 0xD6, 190, 0xC11C6),
        (0x1A0, 0x196, 6, 0xC10F6),
        (0x37E, 0x36C, 2, 0xAFE7E),
        (0x3D0, 0x3C0, 2, 0xA38BE),
    ),
    "430065": (
        (0x3C, 0x3C, 4, 0xA4C16),
        (0x56, 0x56, 126, 0xAD73E),
        (0xD6, 0xD6, 200, 0xC00D0),
        (0x1A0, 0x1A0, 6, 0xC0000),
        (0x37E, 0x376, 2, 0xAFF3C),
        (0x3D0, 0x3CA, 2, 0xA4550),
    ),
    "430066": (
        (0x3C, 0x3C, 4, 0xA4C16),
        (0x56, 0x56, 126, 0xAB96C),
        (0xD6, 0xD6, 200, 0xC00D0),
        (0x1A0, 0x1A0, 6, 0xC0000),
        (0x37E, 0x37E, 2, 0xBF5F8),
        (0x3D0, 0x3D0, 2, 0xA4550),
    ),
}

# Identity, monitor bytes, cycle, status, bitmap, extra monitor word, windows,
# prioritized snapshot, six cycle bytes and final cycle byte; all physical RAM.
DESTINATIONS = {
    "430037": (0x40144, 0x41000, 0x4043B, 0x40137, 0x4102E, 0x410DC,
               0x40F78, 0x40F5C, 0x40169, 0x403D0),
    "430055": (0x40168, 0x41058, 0x40471, 0x4015A, 0x41086, 0x41134,
               0x40FD0, 0x40FB4, 0x40190, 0x40404),
    "430064": (0x4016D, 0x410A8, 0x40480, 0x4015F, 0x410D6, 0x41184,
               0x41020, 0x41004, 0x40195, 0x40413),
    "430065": (0x40172, 0x410C8, 0x4048F, 0x40164, 0x410FA, 0x411B0,
               0x41040, 0x4101C, 0x4019A, 0x40419),
    "430066": (0x40172, 0x410DA, 0x40491, 0x40164, 0x4110C, 0x411C2,
               0x41052, 0x4102E, 0x4019A, 0x40419),
}
# Actual selector table, two count bytes, two ID lists, twelve-byte live fault array.
FAULTS = {
    "430037": (0x4B14, 0x40186, 0x417F2, 0x41888, 0x410E8),
    "430055": (0x4DC4, 0x401AE, 0x41884, 0x4191E, 0x4114A),
    "430064": (0x4E38, 0x401B5, 0x418D4, 0x4196E, 0x4119A),
    "430065": (0x4EAA, 0x401BB, 0x41960, 0x41A02, 0x411C6),
    "430066": (0x4EB0, 0x401BB, 0x41972, 0x41A14, 0x411D8),
}
PRODUCERS = {"430037": 0xAEA06, "430055": 0xADE08, "430064": 0xADFE6,
             "430065": 0xAD430, "430066": 0xAB65E, "430069": 0xAB666}


def check_original_fault_producer(emu, software, image, descriptor_id=0):
    entry = PRODUCERS[software]
    code = image[entry & 0x7FFFF:(entry & 0x7FFFF) + 504]
    # These are operands of the original producer, not guessed cross-revision RAM aliases.
    assert code[0x8E:0x90] == b"\xF3\xF8"
    assert code[0xCC:0xCE] == b"\xE6\xF2"
    assert code[0x142:0x144] == b"\xF3\xF8"
    reason = int.from_bytes(code[0x90:0x92], "little")
    descriptor = int.from_bytes(code[0xCE:0xD0], "little") + descriptor_id * 22
    operating_counter = int.from_bytes(code[0x144:0x146], "little")
    age_entry = entry + 504
    age_code = image[age_entry & 0x7FFFF:(age_entry & 0x7FFFF) + 246]
    assert age_code[8] == 0x8A and age_code[42:44] == b"\xE6\xF9"
    record = int.from_bytes(age_code[44:46], "little") + descriptor_id * 12
    counts = (int.from_bytes(code[start:start + 2], "little") for start in (0xE2, 0x188))
    for address in counts:
        emu.write_byte(address, 0)
    emu.write(0xFD00 + code[0x31] * 2, 0)
    emu.write(operating_counter, 0x1234)
    for i in range(12):
        emu.write_byte(record + i, 0)
    environment = bytes(emu.mem.read_byte(emu.read(descriptor + 10 + i * 2)) for i in range(4))
    for _ in range(5):
        emu.write_byte(reason, 1)
        for register, value in ((12, record), (13, descriptor), (14, 2), (15, 10)):
            emu.reg.set_word(register, value)
        restore(emu, entry)
    captured = bytes(emu.mem.read_byte(record + i) for i in range(11))
    assert captured[0] & 0x40
    assert captured[1:4] == b"\x0A\x01\x28"
    assert captured[4:8] == environment
    assert captured[8:10] == b"\x12\x34"
    for _ in range(10):
        emu.write_byte(reason, 0)
        for register, value in ((12, record), (13, descriptor), (14, 2), (15, 10)):
            emu.reg.set_word(register, value)
        restore(emu, entry)
    assert emu.mem.read_byte(record + 1) == 0
    assert not emu.mem.read_byte(record) & 0x40
    assert emu.mem.read_byte(record + 2) == 1
    # Supply the explicit inactive-record aging condition and execute its original owner.
    emu.write(0xFD00 + age_code[9] * 2, 1 << (age_code[11] >> 4))
    emu.write_byte(record + 10, 0x20)
    restore(emu, age_entry)
    assert emu.mem.read_byte(record + 3) == 39
    assert bytes(emu.mem.image) == image


def test_baseline_fault_counter_labels_follow_original_producer(native_storage):
    emu, peer, image = native_storage
    before, writes = bytes(peer.storage), peer.storage_write_attempts
    check_original_fault_producer(emu, "430069", image)
    assert bytes(peer.storage) == before and peer.storage_write_attempts == writes


@pytest.mark.parametrize("software", PRODUCERS)
def test_fault_summary_uses_qualified_byte_layout(software):
    raw = bytearray(b"\xFF" * 2048)
    payload = bytearray(126)
    payload[4], payload[124] = 0, 1
    payload[14:25] = bytes.fromhex("61 0A 01 28 11 22 33 44 12 34 20")
    offset = 0x54 if software == "430037" else 0x56
    put_record(raw, offset, payload)
    row = next(row for row in inspect_storage_records(raw, "MS43", software, "eeprom")
               if row["address"] == offset)
    values = dict(row["values"])
    assert "debounce 10; occurrences 1; aging 40; operating-counter snapshot 4660" in values[
        "Saved fault 1 counters"]
    assert values["Saved fault 1 environment codes (raw)"] == "11 22 33 44"


@pytest.mark.parametrize("family,software,kind,offset,size,total", (
    ("MS42", "0110C6", "retained", 0x10 + 0x12E, 190, 0x4000),
    ("MS43", "430069", "eeprom", 0xD6, 200, 0x800),
))
def test_snapshot_preview_does_not_replace_whole_monitor_record_length(
        family, software, kind, offset, size, total):
    raw = bytearray(b"\xFF" * total)
    put_record(raw, offset, bytes(size))
    rows = inspect_storage_records(raw, family, software, kind)
    address = offset + (0x4000 if family == "MS42" else 0)
    record = next(row for row in rows if row["address"] == address)
    assert record["crc_valid"] and record["values"]
    assert any(label.startswith("Prioritized snapshot bytes") for label, _ in record["values"])
    assert record["size"] == size


@pytest.mark.parametrize("key", (*RECORDS, "430066-later"))
def test_original_diagnostic_history_consumers_match_public_decode(key, native, monkeypatch):
    import tests
    root = Path(os.environ.get("MS41EMU_ROOT", Path.home() / "ECU Emulator"))
    monkeypatch.setattr(tests, "__path__", [*tests.__path__, str(root / "tests")])
    from tests.test_c167_lifecycle import prepare_lifecycle

    software = key.split("-")[0]
    image = bytearray(full_image(key) if key == "430055" else composed_image(key, "430069").image)
    image[0x6FFF0:0x6FFF4] = bytes.fromhex("90482442")
    image[0x7FFF0:0x7FFF4] = bytes.fromhex("42244890")
    image = bytes(image)
    cls = type(native("MS43"))
    monkeypatch.setattr(cls, "IMAGE_SHA256", sha256(image).hexdigest())
    emu, peer, _ = prepare_lifecycle(cls, image)
    peer.storage[:] = b"\xFF" * 2048
    put_record(peer.storage, 0x32, b"43241010" if key == "430055" else BINDINGS[key])
    binding = 0xC393A if key == "430055" else OWNERS[key][0][0]
    result = emu.cpu.run(emu.cpu.pc, stop_at=binding, max_steps=2_000_000)
    assert result.exit_reason == "stop_at", result
    emu.cpu.set_trace(None)
    restore(emu, binding)
    read = lambda address, count: bytes(emu.mem._physical_read(address + i) for i in range(count))
    write = emu.mem._physical_write
    table, counts, ids1, ids2, live = FAULTS[software]
    identifiers = bytes(emu.mem.read_byte(table + i) for i in range(10))
    (identity, monitor_bytes, cycle, status, bitmap, extra, windows,
     snapshot, cycles, last_cycle) = DESTINATIONS[software]
    short = software in ("430037", "430055", "430064")
    window_start, snapshot_start, snapshot_size = (22, 154, 27) if short else (24, 156, 35)
    owner, flags, cycle_start = (179, 181, 182) if short else (189, 191, 192)
    payloads = {}
    for group, offset, size, entry in RECORDS[software]:
        payload = bytearray(range(1, size + 1))
        if group == 0x56:
            payload[4:14], payload[124] = identifiers, 10
        if group == 0xD6:
            payload[owner], payload[flags] = 0xFF, 0x10
            for i in range(22):
                payload[window_start + i * 6 + 4] |= 0x40
        put_record(peer.storage, offset, payload)
        payloads[group] = payload
        before, attempts = bytes(peer.storage), peer.storage_write_attempts
        restore(emu, entry)
        assert bytes(peer.storage) == before and peer.storage_write_attempts == attempts
        if group == 0x56:
            assert read(counts, 2) == b"\x0A\x0A"
            assert read(ids1, 10) == read(ids2, 10) == identifiers
            for i, identifier in enumerate(identifiers):
                assert read(live + identifier * 12, 11) == payload[14 + 11 * i:25 + 11 * i]
    assert read(identity, 4) == bytes((1, 2, 3, 4))
    assert read(monitor_bytes, 6) == bytes(range(1, 7))
    assert read(cycle, 2) == b"\x01\x03"  # Native restore advances the second saved cycle byte.
    assert read(status, 2) == b"\x01\x02"
    payload = payloads[0xD6]
    bitmap_size = 20 if short else 22
    assert read(bitmap, bitmap_size) == payload[:bitmap_size]
    assert read(extra, 2) == payload[bitmap_size:bitmap_size + 2]
    assert read(windows, 132) == payload[window_start:window_start + 132]
    assert read(snapshot, snapshot_size) == payload[snapshot_start:snapshot_start + snapshot_size]
    assert read(cycles, 6) == payload[cycle_start:cycle_start + 6]
    assert read(last_cycle, 1) == payload[cycle_start + 6:cycle_start + 7]
    rows = {row["address"]: row for row in inspect_storage_records(peer.storage, "MS43", software, "eeprom")}
    for group, offset, size, _ in RECORDS[software]:
        row = rows[offset]
        assert row["crc_valid"] and row["size"] == size
        assert row["values"]
        if group == 0x56:
            assert dict(row["values"])["Internal fault IDs"] == tuple(identifiers)
        if group == 0xD6:
            values = dict(row["values"])
            assert sum(name.startswith("Diagnostic window group") for name in values) == 22
            assert values["Snapshot owner (internal fault ID; FF = unassigned)"] == "0xFF (255)"
            assert values["Prioritized snapshot bytes (raw)"] == payload[snapshot_start:snapshot_start + snapshot_size].hex(" ").upper()
    snapshot_data = make_snapshot("MS43", "eeprom", peer.storage, software_id=software)
    reviewed = {offset for _, offset, _, _ in RECORDS[software]}
    assert not any(field["record_offset"] in reviewed for field in editable_fields(snapshot_data))
    monitor_offset, monitor_entry = next((o, e) for g, o, _, e in RECORDS[software] if g == 0xD6)
    # A capture bit cannot replace live descriptor membership; an unknown owner cannot replace FF.
    write(counts, 0)
    write(counts + 1, 0)
    for i in range(132):
        write(windows + i, 0xA5)
    payload[owner] = 0xFE
    put_record(peer.storage, monitor_offset, payload)
    for i in range(snapshot_size):
        write(snapshot + i, 0x5A)
    restore(emu, monitor_entry)
    assert read(windows, 132) == b"\xA5" * 132
    assert read(snapshot, snapshot_size) == b"\x5A" * snapshot_size
    for _, offset, size, _ in RECORDS[software]:
        peer.storage[offset + size] ^= 1
    rows = {row["address"]: row for row in inspect_storage_records(peer.storage, "MS43", software, "eeprom")}
    assert all(not rows[offset]["crc_valid"] and not rows[offset]["values"] for offset in reviewed)
    damaged = make_snapshot("MS43", "eeprom", peer.storage, software_id=software)
    assert not any(row["repairable"] for row in storage_record_checks(damaged) if row["offset"] in reviewed)
    check_original_fault_producer(emu, software, image, identifiers[0])
    assert bytes(emu.mem.image) == image
