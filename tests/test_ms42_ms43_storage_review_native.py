"""Original record readers and OBD consumers verify offline storage review semantics."""

from ms42_ms43_diagnostics import inspect_storage_records
from ms42_ms43_storage import make_snapshot
from ms42_ms43_storage_edit import repair_storage_checks, storage_knock_axes
from tests.test_ms42_ms43_config_native import native as native
from tests.test_ms42_ms43_config_revisions import private_sources as private_sources
from tests.test_ms42_ms43_storage_completion_native import admit_journal
from tests.test_ms42_ms43_storage_inspection import put_record
from tests.test_ms43_storage_edit_native import native_storage as native_storage, restore


def read_bytes(emu, address, count):
    read = emu.mem.read_byte if address <= 0xFFFF else emu.mem._physical_read
    return bytes(read(address + i) for i in range(count))


def exercise_obd_consumers(emu, family, rows):
    ms42 = family == "MS42"
    entry_points = ((0x7392A, 0x739BA, 0x73CB4, 0x73D48, 0x73B9E) if ms42 else
                    (0x95948, 0x959D8, 0x95E2C, 0x95EC0, 0x95CA8))
    flag, output = (0xE57E, 0xE597) if ms42 else (0xFBF3, 0xE605)
    record = next(row for row in rows if row["name"] == "OBD result blocks")
    fields = dict(record["values"])
    for test, entry, count in zip((1, 2, 3, 5, 6), entry_points,
                                   (2, 8 if ms42 else 12, 2, 4 if ms42 else 10, 4)):
        emu.mem.write_byte(flag, 0)
        for _ in range(count):
            restore(emu, entry)
            component = emu.mem.read_byte(output)
            name = (f"{component:02X}" if ms42 or test != 6 else
                    f"{component & 0x7F:02X}/{component | 0x80:02X}")
            result = int.from_bytes(read_bytes(emu, output + 1, 2), "big")
            limit = int.from_bytes(read_bytes(emu, output + 3, 2), "big")
            for meaning, value in (("result", result), ("limit", limit)):
                expected = fields[f"Test {test:02X}, component {name} {meaning} (raw)"]
                if isinstance(expected, str):
                    expected = int(expected.split()[0], 16)
                assert value == expected
        assert emu.mem.read_byte(flag) == 0

    fields = dict(next(row for row in rows if row["name"] == "OBD monitor banks")["values"])
    entry_points = ((0x72C04, 0x72C14, 0x72C24, 0x72C34, 0x72C44) if ms42 else
                    (0x94AA4, 0x94AB4, 0x94AC4, 0x94AD4, 0x94AE4))
    for bank, sensor in enumerate((1, 0x10)):
        for test, entry in zip((3, 4, 5, 6, 7), entry_points):
            emu.reg.set_word(9, (0xC406 if ms42 else 0x8588) + bank * 14)
            result = emu.run_from(entry, stop_at=(0x72C60 if ms42 else 0x94B00,), max_steps=2000)
            assert result.exit_reason == "stop_at", result
            for meaning, address in (("result", output + 1), ("limit", output + 3)):
                key = f"Sensor {sensor:02X}, test {test:02X} {meaning} (raw)"
                if key not in fields:
                    continue
                expected = fields[key]
                if isinstance(expected, str):
                    expected = int(expected.split()[0], 16)
                if test in (5, 6):
                    expected = min(255, expected * 5)
                elif test == 7:
                    expected = min(255, expected // 2)
                assert emu.mem.read_byte(address) == expected


def oxygen_payload():
    return b"".join(b"".join((value + bank).to_bytes(2, "little") for value in (10, 20, 30, 40, 50, 60))
                    + bytes((70 + bank, 80 + bank)) for bank in range(2))


def exercise_monitor_history(emu, family, storage):
    ms42 = family == "MS42"
    base, size = (0x410 + 0x12E, 190) if ms42 else (0xD6, 200)
    entry, count_address, list_address, table = ((0x3AE68, 0xC62F, 0xCD18, 0x6154) if ms42 else
                                                (0xC00D0, 0x401BB, 0x41974, 0x4EB0))
    windows = (0xC248, 0xC26C, 0xC290) if ms42 else (0x41052, 0x41076, 0x4109A)
    bitmap, snapshot_address = (0xD00A, 0xC22C) if ms42 else (0x4110C, 0x4102E)
    start, owner, flags, snapshot_start, snapshot_size = ((26, 183, 185, 158, 27) if ms42 else
                                                         (24, 189, 191, 156, 35))

    def write(address, value):
        if address <= 0xFFFF:
            emu.mem.write_byte(address, value)
        else:
            emu.mem._physical_write(address, value)

    payload = bytearray((index + 1) & 0xFF for index in range(size))
    for index in range(22):
        payload[start + index * 6 + 4] |= 0x40
    payload[owner], payload[flags] = 0xFF, 0x10
    put_record(storage, base, payload)
    if ms42:
        admit_journal(emu, storage)
    write(count_address, 1)
    write(list_address, emu.mem.read_byte(table))
    restore(emu, entry)
    assert read_bytes(emu, bitmap, 20 if ms42 else 22) == payload[:20 if ms42 else 22]
    for group, address in enumerate(windows):
        offset = start + group * 36
        assert read_bytes(emu, address, 6) == payload[offset:offset + 6]
    assert read_bytes(emu, snapshot_address, snapshot_size) == payload[snapshot_start:snapshot_start + snapshot_size]
    rows = inspect_storage_records(storage, family, "0110C6" if ms42 else "430069",
                                   "retained" if ms42 else "eeprom")
    record = next(row for row in rows if row["address"] == base + (0x4000 if ms42 else 0))
    values = dict(record["values"])
    assert values["Retained monitor bitmap word 1 (raw)"] == 0x201
    assert values["Snapshot owner (internal fault ID; FF = unassigned)"] == "0xFF (255)"
    assert values["Prioritized snapshot bytes (raw)"] == payload[snapshot_start:snapshot_start + snapshot_size].hex(" ").upper()
    assert sum(label.startswith("Diagnostic window group") for label in values) == 22
    # A stored capture marker does not bypass the live fault-list condition.
    write(count_address, 0)
    for address in windows:
        for offset in range(6):
            write(address + offset, 0xA5)
    restore(emu, entry)
    assert all(read_bytes(emu, address, 6) == b"\xA5" * 6 for address in windows)


def test_ms42_records_roundtrip_through_original_obd_consumers_and_explicit_check_repair(native):
    emu = native("MS42")
    firmware = bytes(emu.mem.image)
    raw = bytearray(firmware[0x4000:0x8000])
    base = 0x410
    put_record(raw, base + 0x260, oxygen_payload())
    put_record(raw, base + 0x27E, b"".join((200 + i).to_bytes(2, "little") for i in range(40)))
    put_record(raw, base + 0x104, bytes.fromhex("123456789abcdef0"))
    raw[base + 0x10C] ^= 1
    admit_journal(emu, raw)
    emu.mem.write_byte(0xE516, 2)
    restore(emu, 0x7329C)
    restore(emu, 0x73430)
    rows = [row for row in inspect_storage_records(raw, "MS42", "0110C6", "retained")
            if row["slot"]["address"] == 0x4400]
    exercise_obd_consumers(emu, "MS42", rows)
    source = make_snapshot("MS42", "retained", raw, software_id="0110C6")
    target = repair_storage_checks(source, [base + 0x104])["data"]
    admit_journal(emu, bytearray(target))
    restore(emu, 0x2B324)
    assert [emu.read(address) for address in (0xE148, 0xE14A, 0xE14C)] == [0x3412, 0x7856, 0xBC9A]
    axes = storage_knock_axes(source, firmware)
    assert axes["rpm"] == [value * 32 for value in firmware[0x4890D:0x4891D]]
    assert len(axes["load"]) == 4
    exercise_monitor_history(emu, "MS42", bytearray(target))
    assert bytes(emu.mem.image[:0x4000]) == firmware[:0x4000]
    assert bytes(emu.mem.image[0x8000:]) == firmware[0x8000:]


def test_ms43_records_roundtrip_through_original_obd_consumers_and_explicit_check_repair(native_storage):
    emu, peer, firmware = native_storage
    put_record(peer.storage, 0x1A8, oxygen_payload())
    put_record(peer.storage, 0x1C6, b"".join((200 + i).to_bytes(2, "little") for i in range(64)))
    put_record(peer.storage, 0x280, bytes.fromhex("123456789abcdef0"))
    peer.storage[0x288] ^= 1
    emu.mem._physical_write(0x40C64, 2)
    restore(emu, 0x9525A)
    restore(emu, 0x95388)
    rows = inspect_storage_records(peer.storage, "MS43", "430069", "eeprom")
    exercise_obd_consumers(emu, "MS43", rows)
    source = make_snapshot("MS43", "eeprom", peer.storage, software_id="430069")
    peer.storage[:] = repair_storage_checks(source, [0x280])["data"]
    writes = peer.storage_write_attempts
    restore(emu, 0xBDC2C)
    restore(emu, 0xB1928)
    assert [int.from_bytes(read_bytes(emu, address, 2), "little")
            for address in (0x40882, 0x4088C, 0x40894)] == [0x3412, 0x7856, 0xBC9A]
    axes = storage_knock_axes(source, firmware)
    assert axes["rpm"] == [value * 32 for value in firmware[0x7400C:0x7401C]]
    assert len(axes["load"]) == 4
    exercise_monitor_history(emu, "MS43", peer.storage)
    assert peer.storage_write_attempts == writes
    assert bytes(emu.mem.image) == firmware


def test_additional_revisions_match_complete_obd_owners_and_knock_axes_consumers(private_sources):
    sources = private_sources
    memory = sources["c7_memory"]
    for start, end in ((0x7329C, 0x736F0), (0x73812, 0x73E44), (0x72C04, 0x72C60),
                       (0x3AE68, 0x3B6DC), (0x3BB70, 0x3BBA8),
                       (0x2EF4E, 0x2EF6C), (0x2F0B8, 0x2F0D0)):
        assert all(address in memory for address in range(start, end))
        assert bytes(memory[address] for address in range(start, end)) == sources["c6"][start:end]
    for start, end in ((0x9525A, 0x956BC), (0x95830, 0x9610E), (0x94AA4, 0x94B00),
                       (0xC00D0, 0xC07AA), (0xC11CA, 0xC120A),
                       (0xB5EC8, 0xB5F04)):
        assert sources["x001"][start - 0x80000:end - 0x80000] == sources["stock"][start - 0x80000:end - 0x80000]
