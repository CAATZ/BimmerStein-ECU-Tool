"""Exact 430055 EEPROM locations, native restoration and presentation units."""

from hashlib import sha256
import os
from pathlib import Path

import pytest

from ms42_ms43_storage import make_snapshot
from ms42_ms43_storage_edit import editable_fields, edit_storage, storage_record_checks
from ms42_ms43_diagnostics import inspect_storage_records, format_storage_inspection
from ms42_ms43_storage_units import storage_field_presentation, storage_display_value, storage_raw_value
from tests.test_ms42_ms43_storage_inspection import put_record
from tests.test_ms42_ms43_config_native import native as native
from tests.test_ms43_storage_edit_native import restore


def words(*values):
    return b"".join((value & 0xFFFF).to_bytes(2, "little") for value in values)


def sample():
    raw = bytearray(b"\xFF" * 0x800)
    put_record(raw, 0x32, b"43241010")
    put_record(raw, 0x26E, words(1234, -4321, 2500) + b"\x80\xA5")
    put_record(raw, 0x278, words(0xA000, 0x8000) + bytes(range(14)) + b"\x00\x01")
    put_record(raw, 0x28E, words(0, 0) + bytes((116,)) * 64)
    put_record(raw, 0x2D4, words(0x8000, 0x8001, 0x7FFF, 0x7FFE, 0x8002))
    put_record(raw, 0x2E0, words(0, 0, 123, 456, 789, 900))
    put_record(raw, 0x336, words(123, 456, 234, 567, 0x5555))
    put_record(raw, 0x3C8, b"\x01\xA5")
    put_record(raw, 0x3CC, b"\x00\x5A")
    return make_snapshot("MS43", "eeprom", raw, software_id="430055")


def test_relocated_fields_have_055_units_and_preserve_unreviewed_bytes():
    source = sample()
    fields = {field["offset"]: field for field in editable_fields(source)}
    assert len(fields) == 98
    assert fields[0x26E]["unit"] == "%"
    assert fields[0x278]["unit"] == "°"
    assert fields[0x292]["unit"] == "°"
    assert fields[0x336]["unit"] == "V"
    assert 0x274 not in fields and 0x280 in fields and 0x33E in fields
    assert storage_field_presentation("MS43", 0x300, software_id="430055") == {}
    assert storage_field_presentation("MS43", 0x280, software_id="unknown") == {}
    assert storage_field_presentation("MS43", 0x2E8, software_id="430055")["unit"] == "×"
    assert storage_field_presentation("MS43", 0x2E8, software_id="430069")["unit"] == "ms"
    assert storage_display_value(fields[0x292], 116) == -4.5
    changes = {0x26E: -32768, 0x270: 32767, 0x272: -1,
               0x278: 0xA100, 0x27A: 0x8100, 0x292: 0, 0x2D1: 128,
               0x336: storage_raw_value(fields[0x336], 2.5), 0x3C8: 2, 0x3CC: 1}
    target = edit_storage(source, changes)
    allowed = set()
    for offset in changes:
        field = fields[offset]
        allowed.update(range(offset, offset + field["width"]))
        check = field["record_offset"] + field["record_size"]
        allowed.update((check, check + 1))
    assert all(old == new or offset in allowed for offset, (old, new)
               in enumerate(zip(source["data"], target["data"])))
    assert target["data"][0x274:0x276] == b"\x80\xA5"
    assert target["data"][0x33E:0x340] == words(0x5555)
    assert len(inspect_storage_records(target["data"], "MS43", "430055", "eeprom")) == 39
    text = format_storage_inspection(target["data"], "MS43", "430055", "eeprom")
    assert "2.5000 V" in text and "Bytes 0x03D6" in text and "X001" not in text
    assert not any(record["repairable"] for record in storage_record_checks(target))


@pytest.fixture
def firmware055(native, monkeypatch):
    import tests
    root = Path(os.environ.get("MS41EMU_ROOT", Path.home() / "ECU Emulator"))
    monkeypatch.setattr(tests, "__path__", [*tests.__path__, str(root / "tests")])
    from tests.test_c167_lifecycle import prepare_lifecycle

    base = native("MS43")
    shared = Path(os.environ.get("MS41_TEST_DATA_ROOT", Path(__file__).resolve().parents[2] / "_shared"))
    path = shared / ("REF_MS43/430055.bin")
    if not path.is_file():
        pytest.skip("private exact 430055 firmware unavailable")
    image = path.read_bytes()
    digest = "820bce7aea24933673fce0b6dc4b24b4f672b4e6dbd43476dcecc5826feec1f8"
    assert sha256(image).hexdigest() == digest
    assert image[0x6FFB2:0x6FFBA] == b"43241010"
    monkeypatch.setattr(type(base), "IMAGE_SHA256", digest)
    emu, peer, _ = prepare_lifecycle(type(base), image)
    # Original 055 binding record, rather than the fixture's 069 CRC-table offset.
    peer.storage[:] = sample()["data"]
    result = emu.cpu.run(emu.cpu.pc, stop_at=0xA3C76, max_steps=2_000_000)
    assert result.exit_reason == "stop_at", result
    emu.cpu.set_trace(None)
    return emu, peer, image


def group(emu, selector):
    emu.mem._physical_write(0xF6D0, selector)
    result = emu.call(0xA53D4, max_steps=10000)
    assert result.exit_reason == "stop_at", result
    assert emu.mem._physical_read(0xF74F) == 0xA0
    return bytes(emu.mem._physical_read(0xF750 + index)
                 for index in range(emu.mem._physical_read(0xF74E) - 4))


def test_original055_restores_edited_fields_and_native_measurements(firmware055, monkeypatch, tmp_path):
    emu, peer, image = firmware055
    source = sample()
    target = edit_storage(source, {0x26E: -32768, 0x270: 32767, 0x272: -1,
                                   0x278: 0xA100, 0x27A: 0x8100,
                                   0x292: 0, 0x2D1: 128, 0x336: 512, 0x3C8: 2, 0x3CC: 1})
    peer.storage[:] = target["data"]
    writes = peer.storage_write_attempts
    for entry in (0xC393A, 0xAFC54, 0xB6D6C, 0xB90FC, 0x9ABFA, 0xB1D04, 0xB4F96):
        restore(emu, entry)
    payload = group(emu, 0x92)
    assert tuple(int.from_bytes(payload[i:i + 2], "big", signed=True)
                 for i in (0, 2, 4)) == (-32768, 32767, -1)
    assert [int.from_bytes(payload[i:i + 2], "big") for i in (14, 16, 22, 24)] == [512, 456, 234, 567]
    payload = group(emu, 0x90)
    assert (payload[2], payload[8]) == (0xA1, 0x81)
    expected = target["data"][0x292:0x2D2]
    for bank in range(6):
        assert bytes(emu.mem._physical_read(0x40213 + bank * 64 + index)
                     for index in range(64)) == expected
    assert bytes(emu.mem.image) == image
    assert bytes(peer.storage) == target["data"] and peer.storage_write_attempts == writes
    assert emu.mem._physical_read(0x401CB) == 2
    assert emu.mem.read_word_direct(0xFD1C) & 0x8000
    # Trace candidate calibration fields without changing their edit admission.
    import json
    put_record(peer.storage, 0x2D4, words(0x8000, 0x8001, 0x7FFF, 0x7FFE, 0x8002))
    put_record(peer.storage, 0x2E0, words(0, 0, 123, 456, 789, 900))
    traces = {}
    read_byte, write_byte = emu.mem._physical_read, emu.mem._physical_write
    read_word = emu.mem.read_word
    for entry in (0xAFC54, 0xBDD02, 0xBDEEA, 0xB1D04, 0xB4F96):
        reads, stored = [], []
        def traced_read(address):
            value = read_byte(address)
            if 0x70000 <= (address & 0x7FFFF) < 0x80000:
                reads.append((f"{emu.cpu.pc:06X}", f"{address & 0x7FFFF:06X}", value))
            return value
        def traced_word(address):
            physical = emu.mem.translate(address) & 0x7FFFF
            value = read_word(address)
            if 0x70000 <= physical < 0x80000:
                reads.append((f"{emu.cpu.pc:06X}", f"{physical:06X}", value))
            return value
        def traced_write(address, value):
            if 0x40000 <= address < 0x42000:
                stored.append((f"{emu.cpu.pc:06X}", f"{address:06X}", value))
            return write_byte(address, value)
        with monkeypatch.context() as tracing:
            tracing.setattr(emu.mem, "_physical_read", traced_read)
            tracing.setattr(emu.mem, "read_word", traced_word)
            tracing.setattr(emu.mem, "_physical_write", traced_write)
            restore(emu, entry)
        traces[f"{entry:06X}"] = {"calibration_reads": reads, "ram_writes": stored}
    (tmp_path / "055-storage-owners.json").write_text(json.dumps(traces, indent=2))
    assert {address for _, address, _ in traces["0AFC54"]["calibration_reads"]} >= {"070BB9", "070BBA"}
    assert {address for _, address, _ in traces["0BDD02"]["calibration_reads"]} >= {
        "0708F2", "0708F4", "0708FA", "0708FC"}
    assert {address for _, address, _ in traces["0BDEEA"]["calibration_reads"]} >= {"070DEC", "070DEE"}

    # Deliberately distinct local calibration limits establish the actual owners.
    tune = bytearray(image)
    for offset, value in {0x708F4: 0x7400, 0x708F2: 0x8C00,
                          0x708FC: 0x7500, 0x708FA: 0x8B00,
                          0x70DEE: -750, 0x70DEC: 650}.items():
        tune[offset:offset + 2] = words(value)
    tune[0x70BBA], tune[0x70BB9] = 109, 149
    emu.mem.image[:] = tune
    for boundary in ("minimum", "maximum"):
        fields = [field for field in editable_fields(source, calibration=tune) if field["calibration_dependent"]]
        assert len(fields) == 8
        changes = {field["offset"]: field[boundary] for field in fields}
        changes[0x2D4] = 0x1234
        target = edit_storage(source, changes, calibration=tune)
        peer.storage[:] = target["data"]
        for entry in (0xAFC54, 0xBDD02, 0xBDEEA):
            restore(emu, entry)
        assert emu.mem._physical_read(0x401E0) == changes[0x274]
        for offset, address in ((0x2D4, 0x40C2C), (0x2D6, 0x40C90), (0x2D8, 0x40C92),
                                (0x2DA, 0x40DEE), (0x2DC, 0x40DF0),
                                (0x2E0, 0x40C80), (0x2E2, 0x40DDE)):
            actual = emu.mem._physical_read(address) | emu.mem._physical_read(address + 1) << 8
            assert actual == changes[offset] & 0xFFFF
    assert bytes(emu.mem.image) == bytes(tune)
    assert tune[:0x70000] == image[:0x70000]
    assert peer.storage_write_attempts == writes
