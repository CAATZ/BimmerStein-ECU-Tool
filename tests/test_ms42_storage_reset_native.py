"""Exact MS42 journal owners: restart aging and software-reset snapshots."""

import pytest

from checksum import _crc
from ms42_ms43_diagnostics import inspect_storage_records, select_ms42_slot
from ms42_ms43_storage import make_snapshot
from ms42_ms43_storage_edit import editable_fields, edit_storage
from ms42_ms43_storage_units import storage_format_value
from tests.ms42_ms43_revision_sources import composed_image
from tests.test_ms42_ca_storage_native import select as ca_select
from tests.test_ms42_ms43_config_native import native as native
from tests.test_ms42_ms43_config_revisions import private_sources as private_sources
from tests.test_ms42_ms43_storage_completion_native import admit_journal
from tests.test_ms42_ms43_storage_inspection import put_record
from tests.test_ms43_storage_edit_native import restore


BASE = 0x410


@pytest.fixture(params=["0110C6", "0110C7", "0110CA"])
def revision(request, native):
    emu = native("MS42")
    software = request.param
    if software != "0110C6":
        emu = type(emu)(composed_image(software, "0110C6").image, experimental=True)
    return software, emu, 0x18E if software == "0110CA" else 0


def admit(emu, raw, software):
    if software == "0110CA":
        length = int.from_bytes(raw[0x7F6:0x7F8], "little")
        raw[0x7FE:0x800] = _crc(raw[0x400:0x400 + length], 15).to_bytes(2, "little")
        assert ca_select(emu, raw) == 1
    else:
        admit_journal(emu, raw)


def snapshot(emu, software):
    raw = bytearray(emu.mem.image[0x4000:0x8000])
    raw[0x404:0x40E] = {"0110C6": b"6577225107", "0110C7": b"6577225108",
                       "0110CA": b"6577225109"}[software]
    put_record(raw, BASE + 0x216, b"\x12\xA5")
    put_record(raw, BASE + 0x22A, bytes.fromhex("E4 FE 20 7A 80 A5 34 12 78 56 BC 9A"))
    admit(emu, raw, software)
    return make_snapshot("MS42", "retained", raw, software_id=software)


def test_editor_only_changes_verified_reset_fields_and_record_checks(revision):
    software, emu, _ = revision
    source = snapshot(emu, software)
    changes = {BASE + 0x216: 254, BASE + 0x22B: 0, BASE + 0x22C: 255,
               BASE + 0x22D: 121, BASE + 0x22E: 120, BASE + 0x232: 0xABCD}
    result = edit_storage(source, changes)
    raw = result["data"]
    assert select_ms42_slot(raw, software) == 1
    allowed = {*changes, BASE + 0x233, BASE + 0x218, BASE + 0x219,
               BASE + 0x236, BASE + 0x237, 0x7FE, 0x7FF}
    assert all(a == b or i in allowed for i, (a, b) in enumerate(zip(source["data"], raw)))
    fields = {field["offset"]: field for field in editable_fields(result)}
    assert storage_format_value(fields[BASE + 0x216], 254) == "254 starts"
    assert storage_format_value(fields[BASE + 0x22B], 0) == "0 events"
    assert storage_format_value(fields[BASE + 0x22C], 255) == "At least 8160 rpm"
    assert storage_format_value(fields[BASE + 0x22D], 121) == "12.337 V"
    assert storage_format_value(fields[BASE + 0x22E], 120) == "42.00 °C"
    for protected in (0x217, 0x22A, 0x22F, 0x230, 0x234):
        with pytest.raises(ValueError):
            edit_storage(source, {BASE + protected: 0})
    for offset in changes:
        with pytest.raises(ValueError):
            edit_storage(source, {offset: 0x10000 if offset == BASE + 0x232 else 256})
    rows = inspect_storage_records(raw, "MS42", software, "retained")
    assert any(row["name"] == "Secondary-air warm-restart counter" for row in rows)
    assert any(row["name"] == "Reset-event snapshot" for row in rows)
    assert not any(row["name"] == "Secondary-air retained configuration" for row in rows)


@pytest.mark.parametrize("saved,drop,expected", [(0, 0, 1), (254, 0, 255), (255, 0, 255),
                                                (200, 19, 201), (200, 20, 0)])
def test_original_restart_reader_increments_saturates_or_clears(revision, saved, drop, expected):
    software, emu, shift = revision
    source = snapshot(emu, software)
    raw = bytearray(edit_storage(source, {BASE + 0x216: saved})["data"])
    emu.mem.image[0x483B2] = 20  # Declared calibration threshold; executable bytes unchanged.
    emu.write_byte(0xC17F, 120)
    emu.write_byte(0xC424, 120 - drop)
    admit(emu, raw, software)
    restore(emu, 0x3E05A + shift)
    assert emu.read_byte(0xC346) == expected
    assert bytes(emu.mem.image[0x4000:0x8000]) == raw
    raw[BASE + 0x218] ^= 1
    admit(emu, raw, software)
    restore(emu, 0x3E05A + shift)
    assert emu.read_byte(0xC346) == 0


@pytest.mark.parametrize("reason", [1, 2, 4, 8, 16])
def test_original_reset_capture_wraps_count_and_preserves_reason_one_status(revision, reason):
    software, emu, shift = revision
    original = bytes(emu.mem.image)
    emu.write(0xFD24, 0xA000 if software == "0110CA" else 0x2800)
    emu.write(0xFD98, 0x1234)
    emu.write(0xDFE4, 0x5678)
    emu.write(0xCF04, 0x9ABC)
    emu.write(0xE1DA, 0xDEF0)
    for at, value in ((0xDFED, 255), (0xC42E, 250), (0xC422, 121), (0xC424, 120)):
        emu.write_byte(at, value)
    emu.reg.set_word(12, reason)
    restore(emu, 0x379F4 + shift)
    assert bytes(emu.read_byte(0xDFEC + i) for i in range(5)) == bytes((0xA0 | reason, 0, 250, 121, 120))
    assert emu.read(0xDFE4) == (0x5678 if reason == 1 else 0x1234)
    assert (emu.read(0xDFE6), emu.read(0xDFE8), emu.read(0xDFEA)) == (0x9ABC, 0xDEF0, 0xA5C7)
    assert bytes(emu.mem.image) == original


def test_original_reset_restore_cache_rejection(revision):
    software, emu, shift = revision
    source = snapshot(emu, software)
    changes = {BASE + 0x22B: 19, BASE + 0x22C: 250, BASE + 0x22D: 121,
               BASE + 0x22E: 120, BASE + 0x232: 0xABCD}
    raw = bytearray(edit_storage(source, changes)["data"])
    admit(emu, raw, software)
    original = bytes(emu.mem.image)
    emu.write(0xDFEA, 0)
    restore(emu, 0x3775A + shift)
    assert bytes(emu.read_byte(0xDFEC + i) for i in range(5)) == raw[BASE + 0x22A:BASE + 0x22F]
    assert (emu.read(0xDFE4), emu.read(0xDFE6), emu.read(0xDFE8)) == (0x1234, 0xABCD, 0x9ABC)
    # A newer reset capture survives the startup reader, even if the old record is bad.
    emu.write(0xDFEA, 0xA5C7)
    emu.write_byte(0xDFED, 77)
    raw[BASE + 0x236] ^= 1
    admit(emu, raw, software)
    restore(emu, 0x3775A + shift)
    assert emu.read_byte(0xDFED) == 77
    emu.write(0xDFEA, 0)
    restore(emu, 0x3775A + shift)
    assert bytes(emu.read_byte(0xDFEC + i) for i in range(5)) == b"\0" * 5
    assert (emu.read(0xDFE4), emu.read(0xDFE6), emu.read(0xDFE8)) == (0, 0, 0)
    assert bytes(emu.mem.image[:0x4000]) == original[:0x4000]
    assert bytes(emu.mem.image[0x8000:]) == original[0x8000:]


def test_c7_reset_and_restart_owners_match_exact_c6_bytes(private_sources):
    memory = private_sources["c7_memory"]
    for start, end in ((0x3775A, 0x37A52), (0x3E05A, 0x3E170)):
        assert all(at in memory for at in range(start, end))
        assert bytes(memory[at] for at in range(start, end)) == private_sources["c6"][start:end]


def test_ca_owners_only_change_verified_admission_and_mode_bit_locations(private_sources):
    c6, ca = private_sources["c6"], composed_image("0110CA", "0110C6").image
    expected = {0x37767: 0xA0, 0x3776B: 0xC0,
                0x379FB: 0xD5, 0x379FF: 0xE6, 0x37A03: 0xF7,
                0x3E05D: 0xA0, 0x3E061: 0xC0}
    differences = {at: ca[at + 0x18E]
                   for start, end in ((0x3775A, 0x37A52), (0x3E05A, 0x3E170))
                   for at in range(start, end) if c6[at] != ca[at + 0x18E]}
    assert differences == expected


@pytest.mark.parametrize("rpm", [0, 31, 32, 8159, 8160, 8161, 65535])
def test_reset_rpm_snapshot_native_quantization_and_saturation(revision, rpm):
    _, emu, _ = revision
    emu.write(0xFA22, rpm)
    result = emu.run_from(0x75722, stop_at=0x7573C, max_steps=10)
    assert result.exit_reason == "stop_at", result
    assert emu.read_byte(0xC42E) == min(rpm >> 5, 255)


def test_original_journal_writer_commits_reset_and_restart_records(native, monkeypatch):
    import os
    from pathlib import Path
    import tests

    root = Path(os.environ.get("MS41EMU_ROOT", Path.home() / "ECU Emulator"))
    monkeypatch.setattr(tests, "__path__", [*tests.__path__, str(root / "tests")])
    from tests.test_c167_lifecycle import prepare_lifecycle

    original = native("MS42")
    firmware = bytes(original.mem.image)
    emu, _, _ = prepare_lifecycle(type(original), firmware)
    result = emu.cpu.run(emu.cpu.pc, stop_at=0x76CA0, max_steps=600_000)
    assert result.exit_reason == "stop_at", result
    for at, value in ((0xC346, 255), (0xDFEC, 0xA4), (0xDFED, 17), (0xDFEE, 250),
                      (0xDFEF, 121), (0xDFF0, 120)):
        emu.write_byte(at, value)
    for at, value in ((0xDFE4, 0x1234), (0xDFE6, 0x5678), (0xDFE8, 0x9ABC), (0xDFEA, 0xA5C7)):
        emu.write(at, value)
    restore(emu, 0x75BFC)
    for register, value in ((12, 0x3C), (13, 0x14), (14, 0x53AC)):
        emu.reg.set_word(register, value)
    result = emu.call(0x75E30, max_steps=3_000_000)
    assert result.exit_reason == "stop_at", result
    raw = bytes(emu.mem.image[0x4000:0x8000])
    selected = select_ms42_slot(raw)
    assert selected == 2
    base = selected * 0x400 + 0x10
    assert raw[base + 0x216] == 255
    payload = raw[base + 0x22A:base + 0x236]
    assert payload[:5] == bytes((0xA4, 17, 250, 121, 120))
    assert payload[6:] == bytes.fromhex("34 12 78 56 BC 9A")
    for offset, size in ((0x216, 2), (0x22A, 12)):
        assert _crc(raw[base + offset:base + offset + size], 15) == int.from_bytes(
            raw[base + offset + size:base + offset + size + 2], "little")
    assert emu.read(0xDFEA) == 0
    saved = bytes(emu.mem.image)
    emu.hardware.reset()
    result = emu.cpu.run(emu.cpu.pc, stop_at=0x76CA0, max_steps=600_000)
    assert result.exit_reason == "stop_at", result
    assert bytes(emu.read_byte(0xDFEC + i) for i in range(5)) == payload[:5]
    assert (emu.read(0xDFE4), emu.read(0xDFE6), emu.read(0xDFE8)) == (0x1234, 0x5678, 0x9ABC)
    assert bytes(emu.mem.image) == saved
