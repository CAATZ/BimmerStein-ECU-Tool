"""Offline storage review tools preserve payloads and honor the native layout boundary."""

import pytest

from checksum import _crc
from ms42_ms43_storage import make_snapshot
from ms42_ms43_storage_edit import (compare_storage_fields, edit_storage, repair_storage_checks,
                                   storage_knock_axes, storage_record_checks)
from tests.test_ms42_ms43_storage_edit import sample
from tests.test_ms42_ms43_storage_calibration import calibrated
from tests.test_ms42_ms43_storage_inspection import put_record


def changed_snapshot(source, changes):
    raw = bytearray(source["data"])
    for offset, value in changes.items():
        raw[offset] = value
    if source["family"] == "MS42":
        for base in (0, 0x400):
            length = int.from_bytes(raw[base + 0x3F6:base + 0x3F8], "little")
            raw[base + 0x3FE:base + 0x400] = _crc(raw[base:base + length], 15).to_bytes(2, "little")
    return make_snapshot(source["family"], source["kind"], raw,
                         software_id=source["software_id"], hardware_id=source["hardware_id"])


@pytest.mark.parametrize("family,offset", (("MS42", 0x514), ("MS43", 0x280)))
def test_explicit_check_repair_changes_only_selected_checks_and_outer_crc(family, offset):
    original = sample(family)
    source = changed_snapshot(original, {offset + 8: original["data"][offset + 8] ^ 1})
    checks = {row["offset"]: row for row in storage_record_checks(source)}
    assert checks[offset]["repairable"] and not checks[offset]["crc_valid"]
    target = repair_storage_checks(source, [offset, offset])
    assert target["data"] == original["data"]
    assert source["data"] != original["data"]
    assert target["hardware_id"] == source["hardware_id"]
    assert not next(row for row in storage_record_checks(target) if row["offset"] == offset)["repairable"]
    assert repair_storage_checks(source, []) == source


def test_repair_does_not_fix_other_invalid_checks_or_crc_covered_padding():
    original = sample("MS43")
    source = changed_snapshot(original, {0x288: original["data"][0x288] ^ 1,
                                         0x2E4: original["data"][0x2E4] ^ 1, 0x787: 0x42})
    target = repair_storage_checks(source, [0x280])
    assert target["data"][0x280:0x288] == source["data"][0x280:0x288]
    assert target["data"][0x28A:] == source["data"][0x28A:]
    assert next(row for row in storage_record_checks(target) if row["offset"] == 0x2A0)["repairable"]


@pytest.mark.parametrize("offset", (True, 0x26, 0x32, 0x1A8, 0x3EE, 0x281, 0x3E2))
def test_unknown_erased_valid_or_nonrecord_checks_are_not_repaired(offset):
    source = sample("MS43")
    with pytest.raises(ValueError):
        repair_storage_checks(source, [offset])


def test_ms42_only_current_admitted_slot_is_repaired():
    original = sample("MS42")
    source = changed_snapshot(original, {0x11C: original["data"][0x11C] ^ 1,
                                         0x51C: original["data"][0x51C] ^ 1})
    checks = {row["offset"]: row for row in storage_record_checks(source)}
    assert not checks[0x114]["repairable"] and checks[0x514]["repairable"]
    with pytest.raises(ValueError):
        repair_storage_checks(source, [0x514, 0x114])
    assert source["data"][0x51C] != original["data"][0x51C]


@pytest.mark.parametrize("family,offset", (("MS42", 0x514), ("MS43", 0x280)))
def test_comparison_reports_fields_checks_and_every_changed_byte(family, offset):
    source = sample(family)
    target = edit_storage(source, {offset: -32768})
    rows = compare_storage_fields(source, target)
    field = next(row for row in rows if row["group"] == "Idle corrections")
    assert field["offset"] == field["before_offset"] == offset
    assert field["after"] == "-50.0000 %" and field["length"] == 2
    assert any(row["group"] == "Record check" for row in rows)
    assert {row["offset"] for row in rows if row["group"] == "Raw byte"} == {
        i for i, (a, b) in enumerate(zip(source["data"], target["data"])) if a != b}
    assert compare_storage_fields(source, source) == []


@pytest.mark.parametrize("invalid", ("binding", "revision", "kind"))
def test_comparison_falls_back_to_raw_for_unadmitted_or_different_layouts(invalid):
    source = sample("MS43")
    target = changed_snapshot(source, {0x280: 42})
    if invalid == "binding":
        target = changed_snapshot(target, {0x32: 0})
    elif invalid == "revision":
        target = make_snapshot("MS43", "eeprom", target["data"], software_id="43X001")
    else:
        source = sample("MS42")
        target = make_snapshot("MS43", "retained", source["data"][:-1] + b"\x42", software_id="430069")
    assert all(row["group"] == "Raw byte" for row in compare_storage_fields(source, target))


def test_binding_failure_prevents_selected_check_repair():
    source = changed_snapshot(sample("MS43"), {0x32: 0, 0x288: 0x12})
    assert not any(row["repairable"] for row in storage_record_checks(source))
    with pytest.raises(ValueError, match="binding"):
        repair_storage_checks(source, [0x280])


@pytest.mark.parametrize("offset,size", ((0xD6, 200), (0x1A8, 28), (0x1C6, 128)))
def test_readonly_monitor_records_remain_unrepairable_even_with_known_crc(offset, size):
    source = sample("MS43")
    raw = bytearray(source["data"])
    put_record(raw, offset, bytes(size))
    raw[offset + size] ^= 1
    source = make_snapshot("MS43", "eeprom", raw, software_id="430069")
    record = next(row for row in storage_record_checks(source) if row["offset"] == offset)
    assert not record["erased"] and not record["crc_valid"] and not record["repairable"]
    assert record["values"] == []
    with pytest.raises(ValueError, match="independent"):
        repair_storage_checks(source, [offset])


@pytest.mark.parametrize("family", ("MS42", "MS43"))
def test_knock_axes_use_matching_calibration_and_stored_table_order(family):
    source, calibration = calibrated(family)
    calibration = bytearray(calibration)
    rpm, load = (0x90D, 0x91E) if family == "MS42" else (0x400C, 0x4095)
    calibration[rpm - 1:rpm + 16] = bytes((16, *range(10, 26)))
    calibration[load - 1:load + 4] = bytes((4, 0, 85, 170, 255))
    assert storage_knock_axes(source, calibration) == {
        "rpm": [value * 32 for value in range(10, 26)], "load": [0, 463, 926, 1389]}
    calibration[load - 1] = 3
    with pytest.raises(ValueError, match="16 by 4"):
        storage_knock_axes(source, calibration)
    calibration[8:14] = b"999999"
    with pytest.raises(ValueError, match="software"):
        storage_knock_axes(source, calibration)
