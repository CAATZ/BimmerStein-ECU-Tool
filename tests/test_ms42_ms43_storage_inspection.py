"""Read-only record inspection follows the independently executed native maps."""

import pytest

from checksum import _crc
from ms42_ms43_diagnostics import format_storage_inspection, inspect_storage_records


def put_record(raw, offset, payload):
    raw[offset:offset + len(payload)] = payload
    raw[offset + len(payload):offset + len(payload) + 2] = _crc(payload, 0xF).to_bytes(2, "little")


def find(records, name):
    return next(record for record in records if record["name"] == name)


def test_ms42_fields_use_payload_offset_and_do_not_infer_active_slot():
    raw = bytearray(b"\xFF" * 0x4000)
    # The last slot exercises the upper address boundary, with no valid outer header.
    base = 15 * 0x400 + 0x10
    put_record(raw, base + 0x104, b"\xFE\xFF\x03\x00\x00\x80\x82\xAB")
    put_record(raw, base + 0x21E, b"\x10\xF1")
    put_record(raw, base + 0x2D4, b"\x02\xF2")
    before = bytes(raw)
    records = inspect_storage_records(raw, "MS42", "0110C6", "retained")
    idle = find(records, "Idle corrections")
    assert idle["address"] == 0x7D14
    assert [value for _, value in idle["values"]] == ["-0.0031 %", "0.0046 %", "-50.0000 %", "0x82 (130)"]
    assert idle["crc_valid"] and idle["slot"]["crc_valid"] is None
    assert find(records, "ETCU CAN marker")["values"][0][1] == "0x10 (16)"
    assert find(records, "ICL3 retained mode")["values"][0][1] == "0x02 (2)"
    text = format_storage_inspection(raw, "MS42", "0110C6", "retained")
    assert "active record is not inferred" in text
    assert "whole-slot CRC invalid length" in text
    assert "firmware admission" in text
    assert bytes(raw) == before


@pytest.mark.parametrize("count", (0, 10, 11, 255))
def test_ms42_fault_count_is_reported_and_bounded_without_mimicking_stock_overrun(count):
    raw = bytearray(b"\xFF" * 0x4000)
    payload = bytes((count,)) + bytes(range(10)) + b"\x93" * (0x7E - 11)
    put_record(raw, 0x10 + 0x84, payload)
    record = find(inspect_storage_records(raw, "MS42", "0110C6", "retained"), "Saved fault records")
    values = dict(record["values"])
    assert values["Stored count"] == count
    assert values["Internal fault IDs"] == tuple(range(min(count, 10)))
    assert sum(label.startswith("Fault slot") for label in values) == min(count, 10)
    if count:
        assert values["Fault slot 1, internal ID 0"] == "active-bit clear; " + " ".join(["93"] * 11)
    assert values["Diagnostic state word 1 (raw)"] == values["Diagnostic state word 2 (raw)"] == 0x9393
    assert bool(record["warnings"]) == (count > 10)
    if count > 10:
        assert "native restore can overrun" in record["warnings"][0]


def test_ms42_fault_id_outside_native_table_is_flagged():
    raw = bytearray(b"\xFF" * 0x4000)
    put_record(raw, 0x94, b"\x01\x93" + bytes(0x7C))
    record = find(inspect_storage_records(raw, "MS42", "0110C6", "retained"), "Saved fault records")
    assert record["warnings"] == ["Fault ID outside this firmware's 147-entry table."]


def test_ms43_eeprom_offsets_crc_signed_words_and_unset_fields():
    raw = bytearray(b"\xFF" * 0x800)
    put_record(raw, 0x2F2, b"\xFE\xFF\x03\x00\xFF\xFF\x34\x12\xFF\xFF\x67\x45")
    put_record(raw, 0x3E2, b"\x02\xF8")
    before = bytes(raw)
    records = inspect_storage_records(raw, "MS43", "430069", "eeprom")
    postcat = find(records, "Post-catalyst correction and catalyst state")
    assert postcat["address"] == 0x2F2 and postcat["size"] == 12
    assert postcat["crc_valid"]
    assert [v for _, v in postcat["values"]] == [-2, 3, "unset (FFFF)", 0x1234, "0.99998 ×", "0.27110 ×"]
    coil = find(records, "Ignition-coil type")
    assert coil["values"] == [("Type code (1 or 2)", "Type 2")]
    assert not coil["warnings"]
    assert all(record["address"] not in (0xA, 0x18) for record in records)
    assert "not the whole image" in format_storage_inspection(raw, "MS43", "430069", "eeprom")
    assert bytes(raw) == before


@pytest.mark.parametrize("family,software,kind,size,base,relative,name", [
    ("MS42", "0110C6", "retained", 0x4000, 0x10, 0x24A, "Paired lambda retained state"),
    ("MS43", "430069", "eeprom", 0x800, 0, 0x2F2, "Post-catalyst correction and catalyst state"),
])
def test_lambda_record_sentinels_do_not_hide_unconditionally_restored_words(
        family, software, kind, size, base, relative, name):
    raw = bytearray(b"\xFF" * size)
    put_record(raw, base + relative, b"\xFF" * 12)
    before = bytes(raw)
    record = find(inspect_storage_records(raw, family, software, kind), name)
    assert record["crc_valid"]
    gains = [65535, 65535] if family == "MS42" else ["0.99998 ×", "0.99998 ×"]
    assert [value for _, value in record["values"]] == ["unset (FFFF)"] * 4 + gains
    assert bytes(raw) == before


def test_bad_local_crc_does_not_present_decoded_values_or_change_bytes():
    raw = bytearray(b"\xFF" * 0x800)
    put_record(raw, 0x3E2, b"\x01\x00")
    raw[0x3E3] ^= 1  # Unnamed second byte is still covered by the native checksum.
    before = bytes(raw)
    coil = find(inspect_storage_records(raw, "MS43", "430069", "eeprom"), "Ignition-coil type")
    assert not coil["crc_valid"] and not coil["erased"] and not coil["values"]
    assert bytes(raw) == before


def test_ms43_crc_inventory_has_exact_bounds_and_never_validates_opaque_regions():
    raw = bytearray(b"\xFF" * 0x800)
    records = inspect_storage_records(raw, "MS43", "430069", "eeprom")
    assert len(records) == 40 and records[0]["address"] == 0x26
    end = 0x26
    for record in records:
        assert record["address"] == end
        put_record(raw, end, bytes((end & 0xFF,)) * record["size"])
        end += record["size"] + 2
    assert end == 0x3EE
    assert all(r["crc_valid"] for r in inspect_storage_records(raw, "MS43", "430069", "eeprom"))
    for record in records:
        changed = bytearray(raw)
        changed[record["address"] + record["size"] - 1] ^= 1
        failed = [r for r in inspect_storage_records(changed, "MS43", "430069", "eeprom") if not r["crc_valid"]]
        assert [r["address"] for r in failed] == [record["address"]]
    raw[:0x26] = b"\x11" * 0x26
    raw[0x3EE:] = b"\x22" * (0x800 - 0x3EE)
    assert all(r["crc_valid"] for r in inspect_storage_records(raw, "MS43", "430069", "eeprom"))
    assert "not validated here" in format_storage_inspection(raw, "MS43", "430069", "eeprom")


def test_unknown_coil_code_is_not_given_a_manufacturer_or_silently_corrected():
    raw = bytearray(b"\xFF" * 0x800)
    put_record(raw, 0x3E2, b"\x03\x00")
    record = find(inspect_storage_records(raw, "MS43", "430069", "eeprom"), "Ignition-coil type")
    assert record["crc_valid"] and record["warnings"]
    assert record["values"][0][1] == "0x03 (3)"


@pytest.mark.parametrize("family,software,kind,size", (
    ("MS42", "0110C6", "retained", 0x4000),
    ("MS43", "430069", "retained", 0x4000),
    ("MS43", "430069", "eeprom", 0x800),
))
def test_erased_unknown_revision_and_wrong_size_are_distinct(family, software, kind, size):
    raw = b"\xFF" * size
    records = inspect_storage_records(raw, family, software, kind)
    assert all(record["erased"] and not record["crc_valid"] and not record["values"] for record in records)
    text = format_storage_inspection(raw, family, software, kind)
    assert ("All sixteen slots are erased" if kind == "retained" else "Ignition-coil type: erased") in text
    assert inspect_storage_records(raw, family, "000000", kind) is None
    assert "Field mapping is not verified" in format_storage_inspection(raw, family, "000000", kind)
    for invalid in (raw[:-1], raw + b"\xFF"):
        with pytest.raises(ValueError, match="size"):
            inspect_storage_records(invalid, family, software, kind)


def test_ms43_flash_journal_never_inherits_ms42_field_mapping():
    raw = bytearray(b"\xFF" * 0x4000)
    put_record(raw, 0x114, b"\x01\x00" * 4)
    assert inspect_storage_records(raw, "MS43", "430069", "retained") == []
    text = format_storage_inspection(raw, "MS43", "430069", "retained")
    assert "payload and activation remain unverified" in text
    assert "Idle corrections" not in text


@pytest.mark.parametrize("family,kind", (("MS41", "retained"), ("MS42", "eeprom"), ("MS43", "guess")))
def test_invalid_family_kind_is_not_silently_reinterpreted(family, kind):
    with pytest.raises(ValueError):
        inspect_storage_records(b"\xFF" * 0x800, family, "430069", kind)
