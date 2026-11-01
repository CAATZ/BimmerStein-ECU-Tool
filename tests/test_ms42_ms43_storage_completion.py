"""Remaining storage formats, with no transport or physical ECU access."""

import pytest

from checksum import _crc
from ms42_ms43_diagnostics import storage_operating_counter, format_storage_inspection
from ms42_ms43_storage import make_snapshot
from ms42_ms43_storage_edit import edit_storage, editable_fields
from tests.test_ms42_ms43_storage_inspection import put_record
from tests.test_ms42_ms43_storage_edit import sample


def counter_record(*words):
    return b"".join(value.to_bytes(2, "little") for value in words)


@pytest.mark.parametrize("copies,expected", (
    ((0, 1, 2), 0), ((0xFFFE, 0xFFFF, 0), 0xFFFE), ((0xFFFF, 0, 1), 0xFFFF),
    ((0x1234, 0x1235, 0xBEEF), 0x1234),
    ((0x1234, 0xBEEF, 0x1236), 0x1234),
    ((0xBEEF, 0x1235, 0x1236), 0x1234),
    ((0xBEEF, 0, 1), 0xFFFF),
    ((0, 10, 20), None), ((0xFFFF, 0xFFFF, 0xFFFF), None),
))
def test_counter_copy_recovery_and_edit_admission(copies, expected):
    raw = bytearray(b"\xFF" * 0x800)
    raw[:6] = counter_record(*copies)
    put_record(raw, 0x32, b"43290010")
    source = make_snapshot("MS43", "eeprom", raw, software_id="430069")
    assert storage_operating_counter(raw) == expected
    fields = {field["offset"]: field for field in editable_fields(source)}
    assert (0 in fields) == (expected is not None)
    assert 0 not in {field["offset"] for field in editable_fields(
        make_snapshot("MS43", "eeprom", raw, software_id="43X001"))}
    if expected is None:
        with pytest.raises(ValueError, match="not editable"):
            edit_storage(source, {0: 123})
        assert "copies rejected" in format_storage_inspection(raw, "MS43", "430069", "eeprom")
    else:
        assert fields[0]["value"] == expected
        assert edit_storage(source, {0: expected}) == source
        for value in (0, 0x1234, 0xFFFE, 0xFFFF):
            if value == expected:
                continue
            target = edit_storage(source, {0: value})["data"]
            assert target[:6] == counter_record(value, (value + 1) & 0xFFFF, (value + 2) & 0xFFFF)
            assert target[6:] == raw[6:]
    assert source["data"] == raw


def test_new_ms43_fields_preserve_upper_eeprom_and_local_padding():
    raw = bytearray(bytes(range(256)) * 8)
    put_record(raw, 0x32, b"43290010")
    for offset, payload in ((0x300, b"\x00\x80"), (0x32A, b"\x80\x81"),
                            (0x354, b"\x34\x12"), (0x3D8, b"\x00\x01"), (0x3EA, b"\x23\xA5")):
        put_record(raw, offset, payload)
    source = make_snapshot("MS43", "eeprom", raw, software_id="430069")
    target = edit_storage(source, {0x300: 0xFFFF, 0x32A: 0xFF, 0x32B: 0,
                                   0x354: 0xFFFF, 0x3D8: 0, 0x3EA: 0xFF})["data"]
    assert target[0x3EE:] == raw[0x3EE:]
    assert target[0x3EB] == 0xA5
    allowed = {i for offset in (0x300, 0x32A, 0x354, 0x3D8, 0x3EA) for i in range(offset, offset + 4)}
    assert all(a == b or index in allowed for index, (a, b) in enumerate(zip(raw, target)))
    for offset in (0x300, 0x32A, 0x354, 0x3D8, 0x3EA):
        corrupt = bytearray(raw)
        corrupt[offset + 2] ^= 1
        fields = editable_fields(make_snapshot("MS43", "eeprom", corrupt, software_id="430069"))
        assert offset not in {field["offset"] for field in fields}


@pytest.mark.parametrize("copies,expected", (
    ((0xFFFE, 0xFFFF, 0), 0xFFFE), ((0xFFFF, 0, 1), 0xFFFF),
    ((0xBEEF, 0x1235, 0x1236), 0x1234), ((0xFFFF,) * 3, None),
))
def test_ms42_non_crc_counter_edit_preserves_records_and_updates_outer_crc(copies, expected):
    raw = bytearray(sample("MS42")["data"])
    base = 0x410
    raw[base:base + 6] = counter_record(*copies)
    raw[0x7FE:0x800] = _crc(raw[0x400:0x712], 15).to_bytes(2, "little")
    for software, binding in (("0110C6", b"6577225107"), ("0110C7", b"6577225108")):
        raw[0x404:0x40E] = binding
        raw[0x7FE:0x800] = _crc(raw[0x400:0x712], 15).to_bytes(2, "little")
        source = make_snapshot("MS42", "retained", raw, software_id=software)
        fields = {field["offset"]: field for field in editable_fields(source)}
        assert (base in fields) == (expected is not None)
        if expected is None:
            with pytest.raises(ValueError, match="not editable"):
                edit_storage(source, {base: 7})
            continue
        assert fields[base]["value"] == expected
        assert edit_storage(source, {base: expected}) == source
        target = edit_storage(source, {base: 7})["data"]
        assert target[base:base + 6] == counter_record(7, 8, 9)
        allowed = {*range(base, base + 6), 0x7FE, 0x7FF}
        assert all(a == b or i in allowed for i, (a, b) in enumerate(zip(raw, target)))
        assert next(field for field in editable_fields(
            make_snapshot("MS42", "retained", target, software_id=software)) if field["offset"] == base)["value"] == 7
