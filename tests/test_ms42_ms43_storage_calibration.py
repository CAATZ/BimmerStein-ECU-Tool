"""Local calibration context admits only values retained by the native readers."""

import pytest

from checksum import _crc
from ms42_ms43_image import PROFILES
from ms42_ms43_storage import make_snapshot
from ms42_ms43_storage_edit import editable_fields, edit_storage, storage_calibration
from tests.test_ms42_ms43_storage_edit import sample
from tests.test_ms42_ms43_storage_inspection import put_record


def calibrated(family):
    profile = PROFILES[family]
    cal = bytearray(profile.partial_size)
    cal[8:14] = ("0110C6" if family == "MS42" else "430069").encode()
    cal[0x20:0x28] = (profile.hw_prefix + "53").encode()
    if family == "MS42":
        cal[0x300], cal[0x2FF] = 100, 150
        words = {0x6B2: 200, 0x6B0: 400, 0x706: 500, 0x704: 800,
                 0x7E0: 0x7000, 0x7DE: 0x9000, 0x7E6: 0x7100, 0x7E4: 0x9100,
                 0xE8: 0xFC18, 0xE6: 1000}
    else:
        cal[0xC21], cal[0xC20] = 105, 155
        words = {0x94C: 0x7200, 0x94A: 0x9200, 0x954: 0x7300, 0x952: 0x9300,
                 0xE7E: 0xFC18, 0xE7C: 1000}
    for offset, value in words.items():
        cal[offset:offset + 2] = value.to_bytes(2, "little")
    source = sample(family)
    raw = bytearray(source["data"])
    if family == "MS42":
        put_record(raw, 0x472, b"".join(value.to_bytes(2, "little") for value in (300, 300, 600, 600)) + b"\x80\x80\x00\xA5")
        put_record(raw, 0x488, b"\x00\x80" * 5)
        put_record(raw, 0x65A, b"\x00\x00" * 6)
        raw[0x7FE:0x800] = _crc(raw[0x400:0x712], 0xF).to_bytes(2, "little")
    else:
        put_record(raw, 0x2E6, b"\x00\x80" * 5)
        put_record(raw, 0x2F2, b"\x00\x00" * 6)
    source = make_snapshot(family, source["kind"], raw, software_id=source["software_id"])
    return source, bytes(cal)


@pytest.mark.parametrize("family", ("MS42", "MS43"))
def test_calibrated_fields_use_local_tune_bounds_and_preserve_every_other_byte(family):
    source, cal = calibrated(family)
    fields = {f["offset"]: f for f in editable_fields(source, calibration=cal)}
    tuned = [f for f in fields.values() if f["calibration_dependent"]]
    assert len(tuned) == (14 if family == "MS42" else 8)
    changes = {f["offset"]: f["maximum"] for f in tuned}
    # Change the common correction to a distinct, nonsentinel value.
    changes[0x488 if family == "MS42" else 0x2E6] = 0x1234
    result = edit_storage(source, changes, calibration=cal)
    allowed = set()
    for field in tuned:
        allowed.update(range(field["offset"], field["offset"] + field["width"]))
        crc = field["record_offset"] + field["record_size"]
        allowed.update((crc, crc + 1))
    if family == "MS42":
        allowed.update((0x7FE, 0x7FF))
    assert all(a == b or i in allowed for i, (a, b) in enumerate(zip(source["data"], result["data"])))
    assert result["sha256"] != source["sha256"]
    assert {f["offset"]: f["value"] for f in editable_fields(result, calibration=cal)
            if f["calibration_dependent"]} == changes
    assert not any(f["calibration_dependent"] for f in editable_fields(source))


@pytest.mark.parametrize("family", ("MS42", "MS43"))
def test_calibration_matches_family_software_hardware_and_full_image_references(family):
    source, cal = calibrated(family)
    profile = PROFILES[family]
    source["hardware_id"] = cal[0x20:0x28].decode()
    assert storage_calibration(source, cal) == cal
    full = bytearray(0x80000)
    full[profile.hw_offset:profile.hw_offset + 8] = cal[0x20:0x28]
    full[profile.sw_offset:profile.sw_offset + 6] = cal[8:14]
    full[profile.calibration_offset:profile.calibration_offset + len(cal)] = cal
    assert storage_calibration(source, full) == cal
    full[profile.calibration_offset + 8] ^= 1
    with pytest.raises(ValueError, match="software"):
        storage_calibration(source, full)
    for offset, message in ((8, "software"), (0x20, "identifier")):
        wrong = bytearray(cal)
        wrong[offset] ^= 1
        with pytest.raises(ValueError, match=message):
            storage_calibration(source, wrong)
    full[profile.calibration_offset + 8] ^= 1
    full[profile.hw_offset + 7] ^= 1
    with pytest.raises(ValueError, match="hardware"):
        storage_calibration(source, full)
    # Actual stock tune headers use the generic xx00 family compatibility ID.
    generic = bytearray(cal)
    generic[0x26:0x28] = b"00"
    assert storage_calibration(source, generic) == generic
    with pytest.raises(ValueError):
        storage_calibration(source, cal[:-1])
    with pytest.raises(ValueError):
        storage_calibration(source, "not an image")


@pytest.mark.parametrize("family", ("MS42", "MS43"))
def test_tune_changes_bounds_and_invalid_ranges_do_not_fall_back_to_stock(family):
    source, cal = calibrated(family)
    offset = 0x51A if family == "MS42" else 0x286
    minimum, maximum = (0x300, 0x2FF) if family == "MS42" else (0xC21, 0xC20)
    changed = bytearray(cal)
    changed[minimum], changed[maximum] = 135, 140
    field = next(f for f in editable_fields(source, calibration=changed) if f["offset"] == offset)
    assert (field["minimum"], field["maximum"]) == (136, 140)
    for value in (135, 141):
        with pytest.raises(ValueError, match="integer"):
            edit_storage(source, {offset: value}, calibration=changed)
    changed[maximum] = 135
    remaining = editable_fields(source, calibration=changed)
    assert offset not in {field["offset"] for field in remaining}
    assert any(field["calibration_dependent"] for field in remaining)
    with pytest.raises(ValueError, match="not editable"):
        edit_storage(source, {offset: 136}, calibration=changed)


def test_ms42_endpoint_group_must_reload_together_and_unrelated_data_stays_unchanged():
    source, cal = calibrated("MS42")
    raw = bytearray(source["data"])
    put_record(raw, 0x472, b"\xFF\xFF" + raw[0x474:0x47E])
    raw[0x7FE:0x800] = _crc(raw[0x400:0x712], 0xF).to_bytes(2, "little")
    source = make_snapshot("MS42", "retained", raw, software_id="0110C6")
    with pytest.raises(ValueError, match="same draft"):
        edit_storage(source, {0x474: 400}, calibration=cal)
    result = edit_storage(source, {0x472: 200, 0x474: 400}, calibration=cal)
    assert result["data"][0x472:0x476] == b"\xC8\x00\x90\x01"
    # A different record is editable without repairing the unrelated endpoints.
    result = edit_storage(source, {0x51A: 125}, calibration=cal)
    assert result["data"][0x472:0x480] == source["data"][0x472:0x480]


@pytest.mark.parametrize("family", ("MS42", "MS43"))
def test_common_correction_cannot_be_changed_while_bank1_would_reset_it(family):
    source, cal = calibrated(family)
    common = 0x488 if family == "MS42" else 0x2E6
    raw = bytearray(source["data"])
    put_record(raw, common, raw[common:common + 2] + b"\x00\x00" + raw[common + 4:common + 10])
    if family == "MS42":
        raw[0x7FE:0x800] = _crc(raw[0x400:0x712], 0xF).to_bytes(2, "little")
    source = make_snapshot(family, source["kind"], raw, software_id=source["software_id"])
    with pytest.raises(ValueError, match="same draft"):
        edit_storage(source, {common: 0x1234}, calibration=cal)
    result = edit_storage(source, {common: 0x1234, common + 2: 0x8000}, calibration=cal)
    assert result["data"][common:common + 4] == b"\x34\x12\x00\x80"


def test_postcat_negative_one_is_a_sentinel_not_a_retained_correction():
    source, cal = calibrated("MS43")
    with pytest.raises(ValueError, match="erased sentinel"):
        edit_storage(source, {0x2F2: -1}, calibration=cal)
    assert edit_storage(source, {0x2F2: -2}, calibration=cal)["data"][0x2F2:0x2F4] == b"\xFE\xFF"


def test_related_reversed_calibration_range_rejects_only_affected_edit():
    source, cal = calibrated("MS42")
    cal = bytearray(cal)
    cal[0x7E0:0x7E2] = b"\xFF\xFF"
    with pytest.raises(ValueError, match="related field"):
        edit_storage(source, {0x488: 0x1234}, calibration=cal)
    assert edit_storage(source, {0x514: 321}, calibration=cal)["data"][0x514:0x516] == b"\x41\x01"


def test_unavailable_counter_sentinel_is_preserved_but_not_offered_as_numeric_edit():
    source = sample("MS43")
    raw = bytearray(source["data"])
    put_record(raw, 0x32E, bytes(14) + b"\xFF\xFF" + bytes(8))
    source = make_snapshot("MS43", "eeprom", raw, software_id="430069")
    field = next(field for field in editable_fields(source) if field["offset"] == 0x33C)
    assert field["value"] == 0xFFFF and field["maximum"] == 0xFFFE
    assert edit_storage(source, {0x33C: 0xFFFF})["data"] == source["data"]
    edited = edit_storage(source, {0x33C: 0xFFFE})
    with pytest.raises(ValueError, match="integer"):
        edit_storage(edited, {0x33C: 0xFFFF})


def test_non_crc_copies_and_paired_bank_fields_are_inspected_without_repair():
    from ms42_ms43_diagnostics import format_storage_inspection, inspect_storage_records

    source, _ = calibrated("MS43")
    raw = bytearray(source["data"])
    raw[:6] = b"\x34\x12\x35\x12\x36\x12"
    raw[6:10] = b"\x7F\x80\x81\x00"
    for offset in (0xA, 0x18):
        raw[offset:offset + 14] = b"".join(value.to_bytes(4, "little") for value in
                                          (0x12345678, 0x12CBA978, 0xED345687)) + b"\x42\xA5"
    before = bytes(raw)
    text = format_storage_inspection(raw, "MS43", "430069", "eeprom")
    assert "Incremented word copies (raw): 0x1234, 0x1235, 0x1236" in text
    assert "Incremented byte copies (raw): 7F 80 81" in text
    assert text.count("All decoded copies agree: yes") == 2
    assert "not the flash ISN" in text and "Bytes 0x03EE" in text
    assert bytes(raw) == before
    records = inspect_storage_records(raw, "MS43", "430069", "eeprom")
    fuel = next(record for record in records if record["address"] == 0x2E6)
    assert len(fuel["values"]) == 5
    assert dict(fuel["values"])["Bank 1 additive adaptation"] == "0.000 ms"
    raw[0x18 + 4] ^= 1
    assert format_storage_inspection(raw, "MS43", "430069", "eeprom").count("All decoded copies agree: no") == 1


def test_raw_throttle_marker_accepts_only_canonical_values_and_keeps_padding():
    source = sample("MS43")
    raw = bytearray(source["data"])
    put_record(raw, 0x348, b"\x34\x12" * 4 + b"\x55\x55")
    source = make_snapshot("MS43", "eeprom", raw, software_id="430069")
    with pytest.raises(ValueError, match="one of"):
        edit_storage(source, {0x350: 0x6000})
    result = edit_storage(source, {0x350: 0xAAAA})
    assert result["data"][0x348:0x350] == source["data"][0x348:0x350]
    assert result["data"][0x350:0x352] == b"\xAA\xAA"
