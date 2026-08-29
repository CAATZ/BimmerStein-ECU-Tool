"""Synthetic offline checks for the shared human-readable EEPROM contract."""

import json
import math

import pytest

from engines.softbsl import eeprom_ram as eeprom


VARIANTS = tuple(eeprom.FIELDS_BY_VARIANT)


def _image(variant):
    image = bytearray((index * 31 + 17) & 0xFF for index in range(512))
    layout = eeprom.DECODE_LAYOUTS[variant]
    image[:6] = b"".join(value.to_bytes(2, "little") for value in (123, 124, 125))
    image[layout["dtc_occurrence"]] = 0
    if variant in eeprom._FAULT_SNAPSHOTS:
        image[eeprom._FAULT_SNAPSHOTS[variant]["state"]] = 0
        image[eeprom._FAULT_SNAPSHOTS[variant]["flags"]] = 0
    image[0x00E:0x00E + layout["knock_stored_bytes"]] = (
        bytes([0 if variant == "MS41.1" else 128]) * layout["knock_stored_bytes"])
    image[0x00E + layout["knock_stored_bytes"]] = 128
    for offset in layout["trims"]:
        image[offset:offset + 2] = b"\x00\x80"
    transmission = layout["transmission"]
    image[transmission:transmission + 2] = (0xA5A2).to_bytes(2, "little")
    image[0x1DD:0x1E0] = b"\x00\x01\x02"
    descriptor, part = {
        "MS41.0": (b"111006064101", b"1429861"),
        "MS41.1": (b"111009096000", b"1437806"),
        "MS41.2": (b"111009091202", b"1406464"),
        "MS41.3": (b"111009091202", b"1406464"),
    }[variant]
    image[0x1E3:0x1EF] = descriptor
    image[0x1EF:0x1FD] = part + part
    for record in eeprom.fields_for_variant(variant):
        if record.checked:
            end = record.offset + record.length
            image[end - 2:end] = eeprom.additive_check(image[record.offset:end - 2]).to_bytes(2, "little")
    return bytes(image)


def _rows(image, variant):
    return {row["id"]: row for row in eeprom.decoded_fields(image, variant)}


def _record_for(variant, offset):
    return next(record for record in eeprom.fields_for_variant(variant)
                if record.offset <= offset < record.offset + record.length)


@pytest.mark.parametrize("variant", VARIANTS)
def test_decoded_contract_has_real_offsets_units_and_family_coverage(variant):
    image = _image(variant)
    report = eeprom.inspect_image(image, variant)
    rows = _rows(image, variant)
    assert len(rows) == len(report["decoded_fields"])
    json.dumps(report, allow_nan=False)
    assert report["warnings"] == []
    required = {
        "id", "label", "category", "description", "unit", "offset", "length",
        "raw", "display", "value", "editable", "kind", "minimum", "maximum",
        "step", "options", "check_ok", "confidence", "requires_advanced",
    }
    for row in report["decoded_fields"]:
        assert required <= set(row) <= required | {"byteorder", "bit_mask"}
        assert row["category"] in {
            "coding", "fuel", "knock", "adaptation", "history", "diagnostic", "identification", "unknown", "faults",
        }
        assert row["confidence"] in {"STATIC", "HOMOLOG", "INFERRED", "UNRESOLVED"}
        assert 0 <= row["offset"] < row["offset"] + row["length"] <= 512
        assert row["raw"] == image[row["offset"]:row["offset"] + row["length"]].hex(" ").upper()
        assert row["check_ok"] in (True, None)
        assert isinstance(row["display"], str)
        assert isinstance(row["requires_advanced"], bool)
        assert not row["requires_advanced"] or row["editable"]
        assert row["kind"] in {"number", "choice", "ascii", "text"}
    assert rows["transmission"]["value"] == "mt"
    assert rows["transmission"]["display"] == "Manual"
    assert rows["transmission"]["offset"] == eeprom.transmission_offset(variant)
    assert rows["idle_trim_1_ms"]["value"] == 0
    assert rows["idle_trim_1_ms"]["unit"] == "ms"
    assert rows["ltft_2_percent"]["unit"] == "%"
    assert rows["knock_global"]["value"] == 0
    assert sum(key.startswith("knock_cell_") for key in rows) == 64
    assert rows["knock_cell_0"]["label"] == "Knock correction R1 C1"
    assert rows["knock_cell_3"]["label"] == "Knock correction R1 C4"
    assert rows["knock_cell_4"]["label"] == "Knock correction R2 C1"
    assert rows["knock_cell_63"]["label"] == "Knock correction R16 C4"
    assert eeprom.KNOCK_LOGICAL_CELLS == 64
    assert "knock_cells" not in eeprom.DECODE_LAYOUTS[variant]
    assert eeprom.DECODE_LAYOUTS[variant]["knock_stored_bytes"] == (
        32 if variant == "MS41.1" else 64)
    assert report["decoded"]["knock_cells_neutral"] is True
    assert ("last_shutdown_ect_celsius" in rows) == (variant != "MS41.0")
    assert ("peak_rpm" in rows) == (variant in ("MS41.2", "MS41.3"))
    assert ("diagnostic_counter_0" in rows) == (variant != "MS41.0")
    assert "Normal operating mode" in rows["tail_progression"]["display"]
    assert rows["tail_descriptor"]["value"] == image[0x1E3:0x1EF].decode("ascii")


@pytest.mark.parametrize("variant", VARIANTS)
def test_repeat_start_coolant_and_warmup_history_use_only_the_active_payload_byte(variant):
    image = bytearray(_image(variant))
    records = {field.key: field for field in eeprom.fields_for_variant(variant)}
    coolant = records["coolant_latch"]
    assert coolant.offset == {"MS41.0": 0x180, "MS41.1": 0x1B6,
                               "MS41.2": 0x1B4, "MS41.3": 0x1B4}[variant]
    image[coolant.offset:coolant.offset + 2] = bytes((160, 0xA5))
    end = coolant.offset + coolant.length
    image[end - 2:end] = eeprom.additive_check(image[coolant.offset:end - 2]).to_bytes(2, "little")
    rows = _rows(image, variant)
    reference = rows["coolant_latch"]
    assert reference["label"] == "Repeat-start coolant reference"
    assert reference["offset"] == coolant.offset and reference["length"] == 1
    assert reference["value"] == 72 and reference["unit"] == "°C"
    assert reference["minimum"] == -48 and reference["maximum"] == 142.5
    assert reference["options"] == [
        {"value": "raw:FF", "label": "Set Not available (0xFF)"},
    ]
    assert rows["coolant_latch_reserved_raw"]["offset"] == coolant.offset + 1
    target = eeprom.set_decoded_field(image, variant, reference["id"], "72.75", allow_advanced=True)
    assert target[coolant.offset:coolant.offset + 2] == bytes((161, 0xA5))
    _assert_field_write_boundary(image, target, variant, reference["id"])

    with pytest.raises(ValueError, match="requires advanced"):
        eeprom.set_decoded_field(image, variant, reference["id"], "raw:FF")
    target = eeprom.set_decoded_field(image, variant, reference["id"], "raw:FF", allow_advanced=True)
    assert target[coolant.offset:coolant.offset + 2] == bytes((0xFF, 0xA5))
    assert _rows(target, variant)[reference["id"]]["check_ok"] is True
    _assert_field_write_boundary(image, target, variant, reference["id"])
    damaged = bytearray(target)
    damaged[end - 2] ^= 0x80
    assert eeprom.set_decoded_field(
        damaged, variant, reference["id"], "raw:FF", allow_advanced=True) == damaged
    restored = eeprom.set_decoded_field(target, variant, reference["id"], "72", allow_advanced=True)
    assert restored[coolant.offset:coolant.offset + 2] == bytes((160, 0xA5))

    unavailable = bytearray(image)
    unavailable[coolant.offset] = 0xFF
    unavailable[end - 2:end] = eeprom.additive_check(
        unavailable[coolant.offset:end - 2]).to_bytes(2, "little")
    missing = _rows(unavailable, variant)["coolant_latch"]
    assert missing["value"] is None and missing["display"] == "Not available (0xFF)"

    if "load_collective" not in records:
        assert "load_collective" not in rows
        return
    warmup = records["load_collective"]
    assert warmup.offset == {"MS41.1": 0x1C8, "MS41.2": 0x1C6, "MS41.3": 0x1C6}[variant]
    image[warmup.offset:warmup.offset + 2] = bytes((90, 0x5A))
    end = warmup.offset + warmup.length
    image[end - 2:end] = eeprom.additive_check(image[warmup.offset:end - 2]).to_bytes(2, "little")
    rows = _rows(image, variant)
    counter = rows["load_collective"]
    assert counter["label"] == "Persistent warm-up history counter"
    assert counter["length"] == 1 and counter["value"] == 90
    assert counter["unit"] == "internal counts"
    assert "only above 90" in counter["description"]
    assert rows["load_collective_reserved_raw"]["offset"] == warmup.offset + 1
    target = eeprom.set_decoded_field(image, variant, counter["id"], "91", allow_advanced=True)
    assert target[warmup.offset:warmup.offset + 2] == bytes((91, 0x5A))
    _assert_field_write_boundary(image, target, variant, counter["id"])


@pytest.mark.parametrize("variant", VARIANTS)
def test_damos_linked_fields_decode_the_storage_domain_and_require_advanced(variant):
    image = bytearray(_image(variant))
    records = {field.key: field for field in eeprom.fields_for_variant(variant)}
    vanos = records["vanos_adaptation"].offset
    idle = records["idle_regulator_adaptation"].offset
    tps = records["tps_adaptation"].offset
    image[vanos:vanos + 3] = b"\x00\x40\x01"
    image[tps:tps + 2] = b"\x00\x10"
    image[idle] = 128
    image[idle + 2:idle + 4] = (-32768).to_bytes(2, "little", signed=True)
    image[idle + 4:idle + 6] = (32767).to_bytes(2, "little", signed=True)
    image[idle + 6:idle + 8] = (-1).to_bytes(2, "little", signed=True)
    rows = _rows(image, variant)
    assert rows["vanos_reference_degrees"]["value"] == 24
    assert rows["vanos_reference_degrees"]["unit"] == "° crank"
    assert rows["vanos_learned_state"]["display"] == "Learned"
    assert rows["idle_air_factor"]["value"] == 1
    assert rows["idle_air_correction_0"]["value"] == -50
    assert rows["idle_air_correction_1"]["value"] == 32767 * 100 / 65536
    assert rows["idle_air_correction_2"]["value"] == -100 / 65536
    assert rows["tps_baseline_degrees"]["value"] == pytest.approx(16 * 0.46862745098039)
    assert rows["tps_baseline_degrees"]["unit"] == "° throttle"
    for field_id in ("vanos_reference_degrees", "vanos_learned_state", "tps_baseline_degrees",
                     "idle_air_factor", "idle_air_correction_0", "idle_air_correction_1", "idle_air_correction_2"):
        assert rows[field_id]["editable"] is True
        assert rows[field_id]["requires_advanced"] is True
        with pytest.raises(ValueError, match="requires advanced"):
            eeprom.set_decoded_field(image, variant, field_id, "0")


@pytest.mark.parametrize("variant", VARIANTS)
@pytest.mark.parametrize(("field_id", "value", "stored"), (
    ("idle_trim_1_ms", "0.0534", 32778),
    ("ltft_1_percent", "2.5", 34406),
    ("idle_trim_2_ms", "-0.0534", 32758),
    ("ltft_2_percent", "-2.5", 31130),
    ("knock_cell_0", "-2.25", 122),
    ("knock_global", "-0.75", 126),
))
def test_numeric_edits_quantize_and_preserve_all_other_bytes(variant, field_id, value, stored):
    before = _image(variant)
    row = _rows(before, variant)[field_id]
    if variant == "MS41.1" and field_id == "knock_cell_0":
        stored = 3
    record = _record_for(variant, row["offset"])
    target = eeprom.set_decoded_field(before, variant, field_id, value)
    end = record.offset + record.length
    allowed = set(range(row["offset"], row["offset"] + row["length"])) | {end - 2, end - 1}
    assert set(eeprom.changed_offsets(before, target)) <= allowed
    assert int.from_bytes(target[row["offset"]:row["offset"] + row["length"]], "little") == stored
    assert _rows(target, variant)[field_id]["check_ok"] is True
    assert eeprom.build_write_plan(before, target, variant)
    assert abs(_rows(target, variant)[field_id]["value"] - float(value)) <= row["step"] / 2


@pytest.mark.parametrize("variant", VARIANTS)
def test_transmission_preserves_auxiliary_bits_and_choice_validation(variant):
    before = _image(variant)
    offset = eeprom.transmission_offset(variant)
    target = eeprom.set_decoded_field(before, variant, "transmission", "at")
    assert int.from_bytes(target[offset:offset + 2], "little") == 0xA5A1
    assert eeprom.changed_offsets(before, target) == (offset, offset + 2)
    assert eeprom.set_decoded_field(target, variant, "transmission", "at") == target
    for value in ("manual", "2", "AT", "", None):
        with pytest.raises(ValueError, match="must be 'at' or 'mt'"):
            eeprom.set_decoded_field(before, variant, "transmission", value)


@pytest.mark.parametrize("variant", VARIANTS)
def test_every_editable_numeric_field_bounds_and_formatted_roundtrip(variant):
    before = _image(variant)
    rows = _rows(before, variant)
    for field_id, row in rows.items():
        if not row["editable"] or row["kind"] != "number" or row["requires_advanced"]:
            continue
        for limit, expected in (("minimum", 0), ("maximum", (1 << (8 * row["length"])) - 1)):
            target = eeprom.set_decoded_field(before, variant, field_id, str(row[limit]))
            current = _rows(target, variant)[field_id]
            if variant == "MS41.1" and field_id.startswith("knock_cell_"):
                assert current["value"] == row[limit]
            else:
                assert int.from_bytes(target[row["offset"]:row["offset"] + row["length"]], "little") == expected
            assert eeprom.set_decoded_field(target, variant, field_id, str(current["value"])) == target
            # Saving the displayed value must not nudge its stored representation.
            assert eeprom.set_decoded_field(target, variant, field_id, current["display"]) == target
        with pytest.raises(ValueError, match="must be between"):
            eeprom.set_decoded_field(before, variant, field_id, str(row["maximum"] + row["step"]))
        with pytest.raises(ValueError, match="must be between"):
            eeprom.set_decoded_field(before, variant, field_id, str(row["minimum"] - row["step"]))


def test_ms411_packed_knock_nibbles_are_real_64_cells_and_edits_preserve_the_pair():
    before = bytearray(_image("MS41.1"))
    before[0x00E] = 0xA3
    before[0x02D] = 0xF0
    rows = _rows(before, "MS41.1")
    assert sum(field_id.startswith("knock_cell_") for field_id in rows) == 64
    assert rows["knock_cell_0"]["value"] == -2.25
    assert rows["knock_cell_1"]["value"] == -7.5
    assert rows["knock_cell_0"]["offset"] == rows["knock_cell_1"]["offset"] == 0x00E
    assert rows["knock_cell_62"]["value"] == 0
    assert rows["knock_cell_63"]["value"] == -11.25
    assert rows["knock_cell_62"]["offset"] == rows["knock_cell_63"]["offset"] == 0x02D
    assert rows["knock_global"]["offset"] == 0x02E
    assert eeprom.decoded_values(before, "MS41.1")["knock_cells_neutral"] is False
    target = eeprom.set_decoded_field(before, "MS41.1", "knock_cell_0", "-3.75")
    assert target[0x00E] == 0xA5
    assert set(eeprom.changed_offsets(before, target)) <= {0x00E, 0x030, 0x031}
    target = eeprom.set_decoded_field(before, "MS41.1", "knock_cell_1", "-0.75")
    assert target[0x00E] == 0x13
    assert _rows(target, "MS41.1")["knock_cell_0"]["value"] == -2.25
    with pytest.raises(ValueError, match="must be between"):
        eeprom.set_decoded_field(before, "MS41.1", "knock_cell_1", "0.75")


@pytest.mark.parametrize(("variant", "base"), (
    ("MS41.1", 0x0F4), ("MS41.2", 0x126), ("MS41.3", 0x126),
))
def test_later_family_rough_running_record_is_an_advanced_editable_six_slot_view(variant, base):
    image = bytearray(_image(variant))
    image[base:base + 4] = (0x78563412).to_bytes(4, "little")
    for index in range(6):
        image[base + 4 + index * 2:base + 6 + index * 2] = (index + 1).to_bytes(2, "little")
    for index in range(5):
        image[base + 16 + index * 2:base + 18 + index * 2] = (0x1100 + index).to_bytes(2, "little")
    image[base + 26:base + 28] = b"\xFE\xCA"
    image[base + 28:base + 30] = b"\x01\xA5"
    record = _record_for(variant, base)
    end = record.offset + record.length
    image[end - 2:end] = eeprom.additive_check(image[record.offset:end - 2]).to_bytes(2, "little")
    rows = _rows(image, variant)

    summary = rows["rough_running"]
    assert summary["offset"] == base and summary["length"] == 30
    assert summary["category"] == "adaptation" and not summary["editable"]
    assert "firing order 1-5-3-6-2-4" in summary["display"]
    assert "cylinder 1 is the zero/reference" in summary["description"]
    assert "percent scale" in summary["description"]
    assert rows["rough_running_event_count"]["value"] == 0x78563412
    for index, cylinder in enumerate((1, 5, 3, 6, 2, 4)):
        count = rows[f"rough_running_slot_{index}_count"]
        assert count["value"] == index + 1
        assert count["label"] == f"Cylinder {cylinder} event count"
        if index:
            correction = rows[f"rough_running_slot_{index}_correction_raw"]
            assert correction["value"] == pytest.approx((0x10FF + index) * 100 / (1 << 22))
            assert correction["minimum"] == -32768 * 100 / (1 << 22)
            assert correction["maximum"] == 32767 * 100 / (1 << 22)
            assert correction["unit"] == "% relative"
            assert correction["confidence"] == "STATIC"
    assert rows["rough_running_reference_accumulator_raw"]["value"] == 0xCAFE
    assert rows["rough_running_completion"]["display"] == "Complete"
    assert rows["rough_running_reserved"]["value"] == 0xA5
    children = [row for field_id, row in rows.items()
                if field_id.startswith("rough_running_") and field_id != "rough_running_raw"]
    assert children and all(row["editable"] and row["requires_advanced"] for row in children)
    correction_value = 4660 * 100 / (1 << 22)
    with pytest.raises(ValueError, match="requires advanced"):
        eeprom.set_decoded_field(image, variant, "rough_running_slot_1_correction_raw", str(correction_value))
    target = eeprom.set_decoded_field(
        image, variant, "rough_running_slot_1_correction_raw", str(correction_value), allow_advanced=True)
    assert target[base + 16:base + 18] == b"\x34\x12"
    _assert_field_write_boundary(image, target, variant, "rough_running_slot_1_correction_raw")
    assert _rows(target, variant)["rough_running_slot_1_correction_raw"]["value"] == pytest.approx(correction_value)
    assert not rows["rough_running"]["editable"] and not rows["rough_running_raw"]["editable"]


def test_ms410_does_not_inherit_the_later_family_rough_running_record():
    assert not any(field_id.startswith("rough_running") for field_id in _rows(_image("MS41.0"), "MS41.0"))


@pytest.mark.parametrize("variant", VARIANTS)
def test_remaining_fuel_record_payload_is_named_raw_and_advanced_editable(variant):
    before = _image(variant)
    rows = _rows(before, variant)
    base = _record_for(variant, eeprom.DECODE_LAYOUTS[variant]["trims"][0]).offset
    co = rows["co_alignment_percent"]
    assert co["offset"] == base and co["unit"] == "%"
    assert co["editable"] and co["requires_advanced"]
    with pytest.raises(ValueError, match="requires advanced"):
        eeprom.set_decoded_field(before, variant, co["id"], "0")
    target = eeprom.set_decoded_field(before, variant, co["id"], "0", allow_advanced=True)
    assert target[base:base + 2] == b"\x00\x80"
    _assert_field_write_boundary(before, target, variant, co["id"])

    cached = [row for field_id, row in rows.items() if field_id.startswith("fuel_bank_")]
    if variant == "MS41.0":
        assert cached == []
        return
    assert len(cached) == (18 if variant == "MS41.1" else 16)
    assert all(row["editable"] and row["requires_advanced"] for row in cached)
    assert rows["fuel_bank_1_lambda_switch_average_1_raw"]["offset"] == (
        0x056 if variant == "MS41.1" else 0x072)
    assert rows["fuel_bank_1_lambda_switch_average_1_raw"]["unit"] == "internal count"
    assert "regulation-frequency" in rows["fuel_bank_1_lambda_switch_average_1_raw"]["label"]
    assert "transition-time" in rows["fuel_bank_1_precat_lambda_switch_average_1_raw"]["label"]
    assert rows["fuel_bank_2_lambda_monitor_state_2_raw"]["offset"] == (
        0x065 if variant == "MS41.1" else 0x08D)
    assert rows["fuel_bank_1_lambda_monitor_state_1_raw"]["unit"] == "ADC count"
    assert "upper O2 switching threshold" in rows["fuel_bank_1_lambda_monitor_state_1_raw"]["label"]
    assert "lower O2 switching threshold" in rows["fuel_bank_1_lambda_monitor_state_2_raw"]["label"]
    field_id = "fuel_bank_1_lambda_switch_average_1_raw"
    row = rows[field_id]
    target = eeprom.set_decoded_field(before, variant, field_id, "1", allow_advanced=True)
    assert target[row["offset"]:row["offset"] + row["length"]] == (1).to_bytes(row["length"], "little")
    _assert_field_write_boundary(before, target, variant, field_id)
    if variant == "MS41.1":
        assert rows["fuel_bank_1_window_state_raw"]["offset"] == 0x052
        assert rows["fuel_bank_1_window_state_raw"]["unit"] == "internal index"
        assert "0xFFFF means invalid" in rows["fuel_bank_1_window_state_raw"]["description"]


@pytest.mark.parametrize("value", ("nan", "NaN", "inf", "-inf", "1e9999", "", "1,25", "abc", None, True))
def test_numeric_edits_reject_nonfinite_or_malformed_values(value):
    with pytest.raises(ValueError, match="finite number"):
        eeprom.set_decoded_field(_image("MS41.2"), "MS41.2", "idle_trim_1_ms", value)


@pytest.mark.parametrize("variant", VARIANTS)
def test_invalid_records_are_visible_and_only_edited_payload_gets_a_new_check(variant):
    before = bytearray(_image(variant))
    records = {field.key: field for field in eeprom.fields_for_variant(variant)}
    for record in (records["fuel_adaptations"], records["transmission"]):
        before[record.offset + record.length - 2] ^= 0x80
    report = eeprom.inspect_image(before, variant)
    assert "2 checked record(s) are invalid" in " ".join(report["warnings"])
    assert _rows(before, variant)["idle_trim_1_ms"]["check_ok"] is False
    assert eeprom.set_decoded_field(before, variant, "idle_trim_1_ms", "0") == before
    assert eeprom.set_decoded_field(before, variant, "idle_trim_1_ms", "0.00001") == before
    assert eeprom.set_decoded_field(before, variant, "transmission", "mt") == before
    target = eeprom.set_decoded_field(before, variant, "idle_trim_1_ms", "0.00534")
    assert _rows(target, variant)["idle_trim_1_ms"]["check_ok"] is True
    assert _rows(target, variant)["transmission"]["check_ok"] is False
    transmission = records["transmission"]
    assert target[transmission.offset:transmission.offset + transmission.length] == before[
        transmission.offset:transmission.offset + transmission.length]
    # Explicitly changing the invalid coding payload updates only that record;
    # the old hardware-oriented shortcut remains stricter and is untouched.
    target = eeprom.set_decoded_field(before, variant, "transmission", "at")
    assert _rows(target, variant)["transmission"]["check_ok"] is True
    assert _rows(target, variant)["idle_trim_1_ms"]["check_ok"] is False


@pytest.mark.parametrize("variant", VARIANTS)
def test_padded_mirrors_warn_and_do_not_invent_tail_identity(variant):
    image = bytearray(_image(variant))
    mirror_size = eeprom.DECODE_LAYOUTS[variant]["mirror_size"]
    image[mirror_size:] = bytes(512 - mirror_size)
    report = eeprom.inspect_image(image, variant)
    assert report["decoded"]["looks_like_zero_padded_ram_mirror"] is True
    assert report["decoded"]["tail_descriptor"] is None
    assert report["decoded"]["tail_dme_part_numbers"] is None
    assert report["decoded"]["tail_progression"] is None
    assert "padded RAM mirror" in " ".join(report["warnings"])
    for field_id in ("tail_descriptor", "tail_dme_part_1", "tail_dme_part_2", "tail_progression"):
        row = _rows(image, variant)[field_id]
        assert row["value"] is None
        assert row["display"] == "Unavailable (possible RAM mirror)"
        assert row["editable"] is False
        assert row["requires_advanced"] is False
        with pytest.raises(ValueError, match="read-only"):
            eeprom.set_decoded_field(image, variant, field_id, "123456789012", allow_advanced=True)
    with pytest.raises(eeprom.EepromError, match="padded RAM mirror"):
        eeprom.validate_write_image(image, variant)
    with pytest.raises(ValueError, match="exactly 512 bytes"):
        eeprom.inspect_image(image[:mirror_size], variant)


def test_unknown_read_only_layout_and_uniform_file_errors_are_explicit():
    image = _image("MS41.0")
    with pytest.raises(ValueError, match="unknown EEPROM field"):
        eeprom.set_decoded_field(image, "MS41.0", "injector_size", "1")
    with pytest.raises(ValueError, match="requires advanced"):
        eeprom.set_decoded_field(image, "MS41.0", "eeprom_save_count", "1")
    with pytest.raises(ValueError, match="unsupported EEPROM layout"):
        eeprom.inspect_image(image, "MS42")
    with pytest.raises(ValueError, match="exactly 512 bytes"):
        eeprom.set_decoded_field(image[:-1], "MS41.0", "transmission", "at")
    assert "differs from the tail identity" in " ".join(eeprom.inspect_image(image, "MS41.1")["warnings"])
    assert "repeated byte" in " ".join(eeprom.inspect_image(bytes(512), "MS41.3")["warnings"])
    assert math.isfinite(_rows(image, "MS41.0")["idle_trim_1_ms"]["maximum"])


@pytest.mark.parametrize("variant", VARIANTS)
def test_advanced_numeric_bounds_roundtrip_and_exact_write_boundary(variant):
    before = _image(variant)
    for field_id, row in _rows(before, variant).items():
        if row["kind"] != "number" or not row["requires_advanced"] or row["value"] is None:
            continue
        with pytest.raises(ValueError, match="requires advanced"):
            eeprom.set_decoded_field(before, variant, field_id, row["display"])
        values = (row["minimum"], row["maximum"])
        if row["minimum"] <= 0 <= row["maximum"]:
            values = (row["minimum"], 0, row["maximum"])
        for value in values:
            target = eeprom.set_decoded_field(before, variant, field_id, str(value), allow_advanced=True)
            current = _rows(target, variant)[field_id]
            assert current["value"] == pytest.approx(value, abs=row["step"] / 2)
            assert eeprom.set_decoded_field(target, variant, field_id, str(current["value"]), allow_advanced=True) == target
            assert eeprom.set_decoded_field(target, variant, field_id, current["display"], allow_advanced=True) == target
            record = _record_for(variant, row["offset"])
            allowed = set(range(row["offset"], row["offset"] + row["length"]))
            packed = eeprom._ms411_management_field(field_id) if variant == "MS41.1" else None
            if packed is not None:
                group, index, suffix = packed
                offsets = eeprom._ms411_management_record(before, group, index)["offsets"]
                allowed = {
                    "min_rpm": {offsets[0]},
                    "min_load": {offsets[0], offsets[1]},
                    "max_rpm": {offsets[1], offsets[2]},
                    "max_load": {offsets[2]},
                    "countdown": {offsets[4]},
                }.get(suffix, allowed)
            if record.checked:
                allowed |= {record.offset + record.length - 2, record.offset + record.length - 1}
                assert current["check_ok"] is True
            assert set(eeprom.changed_offsets(before, target)) <= allowed
        for value in (row["minimum"] - row["step"], row["maximum"] + row["step"]):
            with pytest.raises(ValueError, match="must be between"):
                eeprom.set_decoded_field(before, variant, field_id, str(value), allow_advanced=True)


@pytest.mark.parametrize("variant", VARIANTS)
@pytest.mark.parametrize(("value", "raw"), (("0", b"\x00\x00"), ("-50", b"\x00\x80"),
                                            (str(32767 * 100 / 65536), b"\xff\x7f"),
                                            (str(-100 / 65536), b"\xff\xff")))
def test_advanced_signed_idle_words_are_twos_complement_not_offset_binary(variant, value, raw):
    before = _image(variant)
    for field_id in ("idle_air_correction_0", "idle_air_correction_1", "idle_air_correction_2"):
        row = _rows(before, variant)[field_id]
        target = eeprom.set_decoded_field(before, variant, field_id, value, allow_advanced=True)
        assert target[row["offset"]:row["offset"] + 2] == raw
        assert _rows(target, variant)[field_id]["value"] == float(value)


@pytest.mark.parametrize("variant", VARIANTS)
def test_signed_load_model_has_verified_encoding_and_damos_derived_units(variant):
    before = _image(variant)
    row = _rows(before, variant)["load_model_correction"]
    scale = 5.46850393700787 if variant == "MS41.0" else 5.46850393700787 / 256
    assert row["unit"] == "mg/stroke"
    assert row["step"] == pytest.approx(scale)
    assert row["minimum"] == pytest.approx(-128 * 5.46850393700787)
    assert row["maximum"] == pytest.approx(127 * 5.46850393700787)
    counts = (-128, -1, 0, 127) if variant == "MS41.0" else (-32768, -256, -1, 0, 32512)
    record = _record_for(variant, row["offset"])
    for count in counts:
        value = count * scale
        target = eeprom.set_decoded_field(
            before, variant, "load_model_correction", str(value), allow_advanced=True)
        expected = count.to_bytes(row["length"], "little", signed=True)
        assert target[row["offset"]:row["offset"] + row["length"]] == expected
        assert _rows(target, variant)["load_model_correction"]["value"] == pytest.approx(value)
        assert set(eeprom.changed_offsets(before, target)) <= {
            *range(row["offset"], row["offset"] + row["length"]),
            record.offset + record.length - 2, record.offset + record.length - 1,
        }
    with pytest.raises(ValueError, match="must be between"):
        eeprom.set_decoded_field(
            before, variant, "load_model_correction", str(128 * 5.46850393700787), allow_advanced=True)
    if variant == "MS41.0":
        fraction = _rows(before, variant)["load_model_fraction_not_restored_raw"]
        assert fraction["offset"] == record.offset and fraction["unit"] == "raw"
        target = eeprom.set_decoded_field(before, variant, fraction["id"], "1", allow_advanced=True)
        assert target[record.offset] == 1 and target[row["offset"]] == before[row["offset"]]
    else:
        assert "load_model_fraction_not_restored_raw" not in _rows(before, variant)


@pytest.mark.parametrize("variant", VARIANTS)
@pytest.mark.parametrize("pad", (0x00, 0x80, 0xFF))
def test_programmed_idle_addition_is_one_rpm_byte_and_preserves_padding(variant, pad):
    before = bytearray(_image(variant))
    field_id = "idle_speed_command_raw"  # Retained serialized ID, not raw-unit semantics.
    row = _rows(before, variant)[field_id]
    offset = {"MS41.0": 0x0F2, "MS41.1": 0x0F0, "MS41.2": 0x122, "MS41.3": 0x122}[variant]
    assert row["offset"] == offset
    assert (row["length"], row["unit"], row["minimum"], row["maximum"], row["step"]) == (1, "RPM", 0, 255, 1)
    assert row["label"] == "Programmed idle-speed addition"
    assert row["confidence"] == "STATIC"
    assert row["requires_advanced"] is True
    before[offset:offset + 2] = bytes((37, pad))
    record = _record_for(variant, offset)
    check_start = record.offset + record.length - 2
    before[check_start:check_start + 2] = b"\x00\x00"  # Preserve a rejected record on no-op.
    before = bytes(before)
    assert _rows(before, variant)[field_id]["value"] == 37
    assert _rows(before, variant)[field_id]["raw"] == "25"
    assert eeprom.set_decoded_field(before, variant, field_id, "37", allow_advanced=True) == before
    with pytest.raises(ValueError, match="requires advanced"):
        eeprom.set_decoded_field(before, variant, field_id, "1")
    for value in (0, 1, 99, 255):
        target = eeprom.set_decoded_field(before, variant, field_id, str(value), allow_advanced=True)
        assert target[offset:offset + 2] == bytes((value, pad))
        assert _rows(target, variant)[field_id]["value"] == value
        assert _rows(target, variant)[field_id]["check_ok"] is True
        assert set(eeprom.changed_offsets(before, target)) <= {offset, check_start, check_start + 1}
        assert eeprom.set_decoded_field(target, variant, field_id, str(value), allow_advanced=True) == target
    assert eeprom.set_decoded_field(before, variant, field_id, "50.25", allow_advanced=True)[offset] == 50
    for value in ("-1", "255.01", "256", "513", "NaN", "Infinity"):
        with pytest.raises(ValueError):
            eeprom.set_decoded_field(before, variant, field_id, value, allow_advanced=True)


@pytest.mark.parametrize("variant", VARIANTS)
def test_legacy_segment_ids_describe_relative_ignition_gains_not_wheel_corrections(variant):
    before = _image(variant)
    for index in range(5):
        field_id = f"roughness_segment_{index}"
        row = _rows(before, variant)[field_id]
        assert row["label"] == f"Relative ignition/dwell gain {index + 1}"
        assert (row["unit"], row["minimum"], row["maximum"], row["step"]) == ("×", 0, 255 / 128, 1 / 128)
        target = eeprom.set_decoded_field(before, variant, field_id, "1", allow_advanced=True)
        assert target[row["offset"]] == 128
        target = eeprom.set_decoded_field(before, variant, field_id, "1.5", allow_advanced=True)
        assert target[row["offset"]] == 192
        record = _record_for(variant, row["offset"])
        assert set(eeprom.changed_offsets(before, target)) <= {
            row["offset"], record.offset + record.length - 2, record.offset + record.length - 1,
        }


@pytest.mark.parametrize("allow_advanced", ("true", "false", 1, 0, None, [], {}))
def test_advanced_opt_in_must_be_a_real_boolean(allow_advanced):
    with pytest.raises(ValueError, match="must be a boolean"):
        eeprom.set_decoded_field(_image("MS41.2"), "MS41.2", "eeprom_save_count", "1",
                                 allow_advanced=allow_advanced)


@pytest.mark.parametrize("variant", VARIANTS)
def test_advanced_ascii_is_exact_printable_and_preserves_other_identity_copy(variant):
    before = _image(variant)
    for field_id in ("tail_descriptor", "tail_dme_part_1", "tail_dme_part_2"):
        row = _rows(before, variant)[field_id]
        assert row["kind"] == "ascii" and row["requires_advanced"]
        replacement = "A " + "1" * (row["length"] - 2)
        with pytest.raises(ValueError, match="requires advanced"):
            eeprom.set_decoded_field(before, variant, field_id, replacement)
        target = eeprom.set_decoded_field(before, variant, field_id, replacement, allow_advanced=True)
        assert target[row["offset"]:row["offset"] + row["length"]] == replacement.encode("ascii")
        assert set(eeprom.changed_offsets(before, target)) <= set(range(row["offset"], row["offset"] + row["length"]))
        assert _rows(target, variant)[field_id]["value"] == replacement
        assert eeprom.set_decoded_field(target, variant, field_id, replacement, allow_advanced=True) == target
        blank = " " * row["length"]
        blank_target = eeprom.set_decoded_field(before, variant, field_id, blank, allow_advanced=True)
        assert _rows(blank_target, variant)[field_id]["value"] == blank
        assert eeprom.set_decoded_field(blank_target, variant, field_id, blank, allow_advanced=True) == blank_target
        for value in (replacement[:-1], replacement + "X", "\n" + replacement[1:],
                      "é" + replacement[1:], "\x7f" + replacement[1:], "\x00" + replacement[1:], None, 1234567):
            with pytest.raises(ValueError, match="exactly .* printable ASCII"):
                eeprom.set_decoded_field(before, variant, field_id, value, allow_advanced=True)
    target = eeprom.set_decoded_field(before, variant, "tail_dme_part_1", "7654321", allow_advanced=True)
    assert target[0x1F6:0x1FD] == before[0x1F6:0x1FD]
    assert "HW-NR copies differ" in " ".join(eeprom.inspect_image(target, variant)["warnings"])


@pytest.mark.parametrize("variant", VARIANTS)
def test_advanced_named_state_choices_preserve_reserved_bytes(variant):
    before = _image(variant)
    for field_id, values in (("tail_progression", ("00 01 02", "03 04 05", "01 02 03")),
                             ("vanos_learned_state", ("0", "1"))):
        row = _rows(before, variant)[field_id]
        assert [option["value"] for option in row["options"]] == list(values)
        with pytest.raises(ValueError, match="requires advanced"):
            eeprom.set_decoded_field(before, variant, field_id, values[0])
        for value in values:
            target = eeprom.set_decoded_field(before, variant, field_id, value, allow_advanced=True)
            raw = bytes.fromhex(value) if field_id == "tail_progression" else bytes([int(value)])
            assert target[row["offset"]:row["offset"] + row["length"]] == raw
            allowed = set(range(row["offset"], row["offset"] + row["length"]))
            if field_id == "vanos_learned_state":
                record = _record_for(variant, row["offset"])
                allowed |= {record.offset + record.length - 2, record.offset + record.length - 1}
            assert set(eeprom.changed_offsets(before, target)) <= allowed
            assert eeprom.set_decoded_field(target, variant, field_id, value, allow_advanced=True) == target
        for value in ("2", "0x01", "02 03 04", None, 1, True):
            with pytest.raises(ValueError, match="named choices"):
                eeprom.set_decoded_field(before, variant, field_id, value, allow_advanced=True)


@pytest.mark.parametrize("variant", VARIANTS)
def test_advanced_edits_do_not_make_opaque_records_editable_or_repair_noop_checks(variant):
    before = bytearray(_image(variant))
    row = _rows(before, variant)["vanos_reference_degrees"]
    record = _record_for(variant, row["offset"])
    before[record.offset + record.length - 2] ^= 0x80
    assert eeprom.set_decoded_field(before, variant, "vanos_reference_degrees", row["display"], allow_advanced=True) == before
    for field in _rows(before, variant).values():
        if not field["editable"]:
            assert field["requires_advanced"] is False
            with pytest.raises(ValueError, match="read-only"):
                eeprom.set_decoded_field(before, variant, field["id"], "1", allow_advanced=True)


@pytest.mark.parametrize("variant", VARIANTS)
def test_selected_check_repair_changes_only_selected_checks(variant):
    before = bytearray(_image(variant))
    checked = [field for field in eeprom.fields_for_variant(variant) if field.checked]
    for record in checked:
        before[record.offset + record.length - 2] ^= 0x80
    selected = checked[1:3]
    target = eeprom.repair_record_checks(before, variant, [field.offset for field in selected])
    allowed = {field.offset + field.length - check_byte for field in selected for check_byte in (1, 2)}
    assert set(eeprom.changed_offsets(before, target)) <= allowed
    assert len(eeprom.changed_offsets(before, target)) == 2
    for row in eeprom.field_report(target, variant):
        if row["checked"]:
            assert row["check_ok"] is (row["offset"] in {field.offset for field in selected})
    assert target[0x1DD:] == before[0x1DD:]
    assert eeprom.repair_record_checks(target, variant, tuple(field.offset for field in selected)) == target
    plan = eeprom.build_write_plan(before, target, variant)
    assert all(item.reason == "check-last" for item in plan)


@pytest.mark.parametrize("variant", VARIANTS)
@pytest.mark.parametrize("offsets", (None, [], (), "all", b"\x0a", 10, [True], [False], [10.0], ["10"],
                                      [0x00A, 0x00A], [-1], [512], [0], [0x1DD], [0x00B], [0x00C]))
def test_selected_check_repair_requires_explicit_distinct_checked_record_starts(variant, offsets):
    with pytest.raises(ValueError):
        eeprom.repair_record_checks(_image(variant), variant, offsets)


@pytest.mark.parametrize("variant", VARIANTS)
def test_selected_repair_keeps_padded_tail_and_requires_supported_image(variant):
    before = bytearray(_image(variant))
    mirror_size = eeprom.DECODE_LAYOUTS[variant]["mirror_size"]
    before[mirror_size:] = bytes(512 - mirror_size)
    before[0x00C] ^= 0x80
    target = eeprom.repair_record_checks(before, variant, [0x00A])
    assert target[mirror_size:] == before[mirror_size:]
    assert eeprom.decoded_values(target, variant)["looks_like_zero_padded_ram_mirror"] is True
    with pytest.raises(ValueError, match="exactly 512"):
        eeprom.repair_record_checks(before[:-1], variant, [0x00A])
    with pytest.raises(ValueError, match="unsupported EEPROM layout"):
        eeprom.repair_record_checks(before, "MS42", [0x00A])


def _fault_image(variant, identifiers=(1,), *, count=None):
    image = bytearray(_image(variant))
    start = eeprom.DECODE_LAYOUTS[variant]["dtc_occurrence"]
    stride = {"MS41.0": 10, "MS41.1": 11, "MS41.2": 12, "MS41.3": 12}[variant]
    image[start] = len(identifiers) if count is None else count
    image[start + 1:start + 1 + len(identifiers)] = bytes(identifiers)
    for index in range(len(identifiers)):
        offset = start + 11 + stride * index
        image[offset:offset + stride] = bytes((0xEF, 0xBD, 3, 40, 100, 123, 80, 120, 1, 44, 0xA7, 0xE9))[:stride]
    if variant in eeprom._FAULT_SNAPSHOTS:
        offsets = eeprom._FAULT_SNAPSHOTS[variant]
        image[offsets["internal_id"]] = identifiers[0] if identifiers else 0xFF
        image[offsets["flags"]] = 0xB9  # Availability and unknown sibling bits.
        for key, value in (("rpm", 2750), ("stft_1_percent", 0x7000), ("stft_2_percent", 0x9000),
                           ("ltft_1_percent", 0x8000), ("ltft_2_percent", 0xFFFF)):
            image[offsets[key]:offsets[key] + 2] = value.to_bytes(2, "little")
        image[offsets["load_mg_stroke"]] = 100
        image[offsets["coolant_celsius"]] = 160
        image[offsets["speed_kmh"]] = 87
        for key, value in (("pp1_1_raw", 0x1234), ("pp1_2_raw", 0x5678),
                           ("pt2_1_raw", 0x9ABC), ("pt2_2_raw", 0xDEF0),
                           ("lambda_state_1", 0xA508), ("lambda_state_2", 0x5A00)):
            image[offsets[key]:offsets[key] + 2] = value.to_bytes(2, "little")
    for record in eeprom.fields_for_variant(variant):
        if record.checked:
            end = record.offset + record.length
            image[end - 2:end] = eeprom.additive_check(image[record.offset:end - 2]).to_bytes(2, "little")
    return bytes(image)


def _assert_field_write_boundary(before, target, variant, field_id):
    row = _rows(before, variant)[field_id]
    record = _record_for(variant, row["offset"])
    allowed = set(range(row["offset"], row["offset"] + row["length"]))
    if record.checked:
        allowed.update((record.offset + record.length - 2, record.offset + record.length - 1))
    assert set(eeprom.changed_offsets(before, target)) <= allowed


@pytest.mark.parametrize("variant", ("MS41.2", "MS41.3"))
def test_dedicated_fault_management_envelopes_follow_internal_ids_without_duplicate_edits(variant):
    before = bytearray(_fault_image(variant, (0x44, 0x44)))
    before[0x152:0x158] = bytes((100, 50, 200, 100, 0xB3, 0x50))
    record = _record_for(variant, 0x152)
    end = record.offset + record.length
    before[end - 2:end] = eeprom.additive_check(before[record.offset:end - 2]).to_bytes(2, "little")
    rows = _rows(before, variant)

    assert "238 — Misfire Cyl 1" in rows["fault_slot_0_management"]["label"]
    assert rows["fault_slot_0_management_min_rpm"]["value"] == 3200
    assert rows["fault_slot_0_management_min_load"]["value"] == pytest.approx(50 * 5.4470588235)
    assert rows["fault_slot_0_management_max_rpm"]["value"] == 6400
    assert rows["fault_slot_0_management_max_load"]["value"] == pytest.approx(100 * 5.4470588235)
    assert rows["fault_slot_0_management_progression"]["value"] == "3"
    assert rows["fault_slot_0_management_progression"]["bit_mask"] == 0x03
    assert rows["fault_slot_0_management_countdown"]["value"] == 0x50
    assert rows["fault_slot_1_management_reference"]["display"] == "Same record as saved fault 1"
    assert "fault_slot_1_management_min_rpm" not in rows
    assert sum(key.startswith("fault_management_") and key.count("_") == 2 for key in rows) == 9

    target = eeprom.set_decoded_field(
        before, variant, "fault_slot_0_management_min_rpm", "3232", allow_advanced=True)
    assert target[0x152] == 101
    _assert_field_write_boundary(before, target, variant, "fault_slot_0_management_min_rpm")
    target = eeprom.set_decoded_field(
        before, variant, "fault_slot_0_management_progression", "0", allow_advanced=True)
    assert target[0x156] == 0xB0
    _assert_field_write_boundary(before, target, variant, "fault_slot_0_management_progression")


def test_fault_management_layout_is_not_projected_onto_ms410():
    rows = _rows(_fault_image("MS41.0", (0x44,)), "MS41.0")
    assert not any("_management" in field_id or field_id.startswith("fault_management_")
                   for field_id in rows)


def test_ms411_packed_fault_management_decode_and_sparse_repack_preserve_neighbors():
    before = bytearray(_fault_image("MS41.1", (0x44, 0x44)))
    q0, q1, q2, q3 = 10, 20, 30, 40
    before[0x128] = (q0 << 2) | (q1 >> 4)
    before[0x13E] = ((q1 & 0x0F) << 4) | (q2 >> 2)
    before[0x154] = ((q2 & 0x03) << 6) | q3
    before[0x16A] = 0xCD
    before[0x180] = 0x50
    record = _record_for("MS41.1", 0x128)
    end = record.offset + record.length
    before[end - 2:end] = eeprom.additive_check(
        before[record.offset:end - 2]).to_bytes(2, "little")
    rows = _rows(before, "MS41.1")

    assert all(f"fault_slot_0_management_{group}0" in rows for group in "ABC")
    assert "short-window severe-misfire" in rows["fault_slot_0_management_A0"]["label"]
    assert "long-window pre-switch misfire" in rows["fault_slot_0_management_B0"]["label"]
    assert "long-window post-switch qualified misfire" in rows[
        "fault_slot_0_management_C0"]["label"]
    assert "600-count short-window" in rows["fault_slot_0_management_A0"]["description"]
    assert "same 3000-count long-window" in rows[
        "fault_slot_0_management_C0"]["description"]
    assert rows["fault_slot_1_management_reference"]["display"] == "Same records as saved fault 1"
    assert rows["fault_slot_0_management_A0_min_rpm"]["value"] == 42 * 32
    assert rows["fault_slot_0_management_A0_min_load"]["value"] == pytest.approx(
        82 * 5.4470588235)
    assert rows["fault_slot_0_management_A0_max_rpm"]["value"] == 122 * 32
    assert rows["fault_slot_0_management_A0_max_load"]["value"] == pytest.approx(
        162 * 5.4470588235)
    assert rows["fault_slot_0_management_A0_progression"]["display"] == "Stage 1"
    assert rows["fault_slot_0_management_A0_terminal_latch"]["display"] == "Latched"
    assert rows["fault_slot_0_management_A0_valid"]["display"] == "Valid"
    assert rows["fault_slot_0_management_A0_countdown"]["value"] == 0x50
    assert "0x128/0x13E" in rows["fault_slot_0_management_A0_min_load"]["description"]
    assert sum(key.startswith("fault_management_") and key.count("_") == 2
               for key in rows) == 19

    # q1 spans sparse p0/p1 bytes. Both change, while q0/q2 and every other
    # packed record remain byte-for-byte stable; only this record check follows.
    target = eeprom.set_decoded_field(
        before, "MS41.1", "fault_slot_0_management_A0_min_load",
        str(194 * 5.4470588235), allow_advanced=True)
    assert set(eeprom.changed_offsets(before, target)) <= {0x128, 0x13E, end - 2, end - 1}
    assert target[0x128] == (q0 << 2) | 3
    assert target[0x13E] == q2 >> 2
    changed = _rows(target, "MS41.1")
    assert changed["fault_slot_0_management_A0_min_rpm"]["value"] == 42 * 32
    assert changed["fault_slot_0_management_A0_max_rpm"]["value"] == 122 * 32
    assert changed["fault_slot_0_management_A0_min_load"]["value"] == pytest.approx(
        194 * 5.4470588235)
    assert changed["fault_slot_0_management_A0_min_load"]["check_ok"] is True

    # q2 likewise spans sparse p1/p2 bytes and preserves q1/q3.
    target = eeprom.set_decoded_field(
        before, "MS41.1", "fault_slot_0_management_A0_max_rpm",
        str(22 * 32), allow_advanced=True)
    assert set(eeprom.changed_offsets(before, target)) <= {0x13E, 0x154, end - 2, end - 1}
    changed = _rows(target, "MS41.1")
    assert changed["fault_slot_0_management_A0_min_load"]["value"] == pytest.approx(
        82 * 5.4470588235)
    assert changed["fault_slot_0_management_A0_max_rpm"]["value"] == 22 * 32
    assert changed["fault_slot_0_management_A0_max_load"]["value"] == pytest.approx(
        162 * 5.4470588235)


def test_ms411_packed_fault_management_flags_countdown_and_check_policy():
    before = bytearray(_fault_image("MS41.1", (0x57,)))
    record = _record_for("MS41.1", 0x13A)
    end = record.offset + record.length
    before[0x17C] = 0xED
    before[0x192] = 0x50
    before[end - 2:end] = eeprom.additive_check(
        before[record.offset:end - 2]).to_bytes(2, "little")
    rows = _rows(before, "MS41.1")
    assert "mixture/post-catalyst lambda" in rows["fault_slot_0_management_D0"]["label"]
    assert "227 — Mixture Deviation Bank 1" in rows["fault_slot_0_management_D0"]["label"]
    assert rows["fault_slot_0_management_D0_progression"]["bit_mask"] == 0x03
    assert rows["fault_slot_0_management_D0_terminal_latch"]["bit_mask"] == 0x0C
    assert rows["fault_slot_0_management_D0_valid"]["bit_mask"] == 0x40
    assert rows["fault_slot_0_management_D0_flags_raw"]["confidence"] == "UNRESOLVED"

    with pytest.raises(ValueError, match="requires advanced"):
        eeprom.set_decoded_field(
            before, "MS41.1", "fault_slot_0_management_D0_progression", "2")
    target = eeprom.set_decoded_field(
        before, "MS41.1", "fault_slot_0_management_D0_progression", "2",
        allow_advanced=True)
    assert target[0x17C] == 0xEE
    assert set(eeprom.changed_offsets(before, target)) <= {0x17C, end - 2, end - 1}
    target = eeprom.set_decoded_field(
        before, "MS41.1", "fault_slot_0_management_D0_countdown", "79",
        allow_advanced=True)
    assert target[0x192] == 79
    assert set(eeprom.changed_offsets(before, target)) <= {0x192, end - 2, end - 1}

    damaged = bytearray(before)
    damaged[end - 2] ^= 0x80
    current = rows["fault_slot_0_management_D0_countdown"]["display"]
    assert eeprom.set_decoded_field(
        damaged, "MS41.1", "fault_slot_0_management_D0_countdown", current,
        allow_advanced=True) == damaged
    repaired = eeprom.set_decoded_field(
        damaged, "MS41.1", "fault_slot_0_management_D0_countdown", "79",
        allow_advanced=True)
    assert _rows(repaired, "MS41.1")["fault_slot_0_management_D0_countdown"]["check_ok"] is True


@pytest.mark.parametrize("variant", ("MS41.1", "MS41.2", "MS41.3"))
def test_fault_qualification_counter_and_relation_matrix_keep_family_offsets(variant):
    before = bytearray(_image(variant))
    matrix = {"MS41.1": 0x122, "MS41.2": 0x1AC, "MS41.3": 0x1AC}[variant]
    before[matrix:matrix + 6] = bytes((0x21, 0x02, 0x04, 0x08, 0x10, 0xE0))
    rows = _rows(before, variant)
    qualification, matrix = {
        "MS41.1": (0x1B2, 0x122),
        "MS41.2": (0x1AB, 0x1AC),
        "MS41.3": (0x1AB, 0x1AC),
    }[variant]
    assert rows["fault_history_qualification_counter"]["offset"] == qualification
    assert rows["fault_history_qualification_counter"]["unit"] == "internal counts"
    assert rows["fault_relation_matrix"]["offset"] == matrix
    assert rows["fault_relation_matrix"]["length"] == 6
    assert "1-5-3-6-2-4" in rows["fault_relation_matrix"]["display"]
    assert rows["fault_relation_matrix"]["label"] == "Cylinder fault-retention matrix (raw)"
    assert rows["fault_relation_matrix_row_1"]["display"] == (
        "1:1 · 5:0 · 3:0 · 6:0 · 2:0 · 4:1 · reserved 0x00")
    assert rows["fault_relation_matrix_row_4"]["display"].endswith("reserved 0xC0")
    assert all(not rows[f"fault_relation_matrix_row_{cylinder}"]["editable"]
               for cylinder in (1, 5, 3, 6, 2, 4))
    target = eeprom.set_decoded_field(
        before, variant, "fault_history_qualification_counter", "1", allow_advanced=True)
    _assert_field_write_boundary(before, target, variant, "fault_history_qualification_counter")


def test_ms410_has_no_later_fault_qualification_or_relation_matrix():
    rows = _rows(_image("MS41.0"), "MS41.0")
    assert "fault_history_qualification_counter" not in rows
    assert "fault_relation_matrix" not in rows


def test_diagnostic_counter_names_follow_proven_family_writers():
    later = _rows(_image("MS41.2"), "MS41.2")
    assert [later[f"diagnostic_counter_{index}"]["label"] for index in range(7)] == [
        "Catalyst-efficiency monitor completions — bank 1",
        "Catalyst-efficiency monitor completions — bank 2",
        "Secondary-air monitor completions — bank 1",
        "Secondary-air monitor completions — bank 2",
        "Secondary-air valve sticking evaluations",
        "Tank-vent/leak diagnostic finalizations",
        "Misfire-monitor evaluation windows",
    ]
    assert all("wraps from 255 to 0" in later[f"diagnostic_counter_{index}"]["description"]
               for index in range(7))
    older = _rows(_image("MS41.1"), "MS41.1")
    assert [older[f"diagnostic_counter_{index}"]["label"] for index in range(7)] == [
        later[f"diagnostic_counter_{index}"]["label"] for index in range(7)
    ]
    assert all(older[f"diagnostic_counter_{index}"]["confidence"] == "STATIC"
               for index in range(7))


@pytest.mark.parametrize("variant", VARIANTS)
@pytest.mark.parametrize(("words", "counter", "valid", "consistent"), (
    ((100, 101, 102), 100, True, True), ((100, 101, 999), 100, True, False),
    ((100, 999, 102), 100, True, False), ((999, 101, 102), 100, True, False),
    ((65535, 0, 1), 65535, True, True), ((77, 0, 1), 65535, True, False),
    ((0, 0, 0), 0, False, False), ((123, 456, 789), 0, False, False),
))
def test_operating_time_vote_matches_firmware_and_noop_preserves_redundancy(variant, words, counter, valid, consistent):
    before = bytearray(_image(variant))
    before[:6] = b"".join(word.to_bytes(2, "little") for word in words)
    before[0x00C] ^= 0x80  # An unrelated invalid check is never repaired.
    report = eeprom.inspect_image(before, variant)
    decoded = report["decoded"]
    assert decoded["cycle_sequence_base"] == words[0]
    assert decoded["operating_time_counter"] == counter
    assert decoded["operating_time_hours"] == counter * 0.1
    assert decoded["operating_time_vote_valid"] is valid
    assert decoded["operating_time_sequence_consistent"] is consistent
    row = _rows(before, variant)["operating_time_hours"]
    assert row["value"] == counter * 0.1 and row["length"] == 6
    if not valid:
        assert "fallback" in row["display"]
        assert "falls back to zero" in " ".join(report["warnings"])
    elif not consistent:
        assert "redundancy is inconsistent" in " ".join(report["warnings"])
    for value in (str(counter * 0.1),) + ((row["display"],) if valid else ()):
        assert eeprom.set_decoded_field(before, variant, row["id"], value, allow_advanced=True) == before
    if not valid:
        with pytest.raises(ValueError, match="finite number"):
            eeprom.set_decoded_field(before, variant, row["id"], row["display"], allow_advanced=True)
    for index in range(3):
        legacy = _rows(before, variant)[f"cycle_sequence_{index}"]
        assert legacy["value"] == words[index] and not legacy["editable"]
        with pytest.raises(ValueError, match="read-only"):
            eeprom.set_decoded_field(before, variant, legacy["id"], "0", allow_advanced=True)


@pytest.mark.parametrize("variant", VARIANTS)
def test_operating_time_edit_is_atomic_quantized_and_wraps_only_its_six_bytes(variant):
    before = _image(variant)
    for counter in (0, 1, 410, 65534, 65535):
        target = eeprom.set_decoded_field(before, variant, "operating_time_hours", str(counter * 0.1), allow_advanced=True)
        expected = b"".join(((counter + index) & 65535).to_bytes(2, "little") for index in range(3))
        assert target[:6] == expected
        assert target[6:] == before[6:]
        assert _rows(target, variant)["operating_time_hours"]["value"] == counter * 0.1
    assert eeprom.set_decoded_field(before, variant, "operating_time_hours", "12.31", allow_advanced=True) == before
    for value in ("-0.1", "6553.6", "NaN", "Infinity", True):
        with pytest.raises(ValueError):
            eeprom.set_decoded_field(before, variant, "operating_time_hours", value, allow_advanced=True)


@pytest.mark.parametrize("variant", VARIANTS)
def test_saved_fault_slots_keep_order_duplicate_ids_id_zero_and_family_stride(variant):
    last_id, last_code, stride = {"MS41.0": (51, "016", 10), "MS41.1": (96, "221", 11),
                                  "MS41.2": (94, "204", 12), "MS41.3": (94, "204", 12)}[variant]
    image = _fault_image(variant, (1, 0, 1, last_id))
    rows = _rows(image, variant)
    start = eeprom.DECODE_LAYOUTS[variant]["dtc_occurrence"]
    for index, code in enumerate(("008", "012", "008", last_code)):
        summary = rows[f"fault_slot_{index}"]
        assert code in summary["label"]
        assert summary["offset"] == start + 11 + stride * index
        assert summary["length"] == stride and not summary["editable"]
        assert "Present at save" in summary["display"] and "Sporadic" in summary["display"]
        assert "frequency 3" in summary["display"]
        assert rows[f"fault_slot_{index}_operating_time_hours"]["value"] == 30
        assert rows[f"fault_slot_{index}_operating_time_hours"]["byteorder"] == "big"
        assert (f"fault_slot_{index}_raw_extra" in rows) == (stride > 10)
    assert rows["fault_slot_0_env_0"]["value"] == 3200
    assert "fault_slot_4" not in rows
    order = list(rows)
    assert order.index("fault_slot_0_frequency") < order.index("fault_slot_0_env_0")
    assert order.index("fault_slot_0_env_0") < order.index("fault_slot_0_internal_id")
    assert order.index("fault_slot_0_operating_time_hours") < order.index("fault_slot_0_status")
    assert "IDs repeat" in " ".join(eeprom.inspect_image(image, variant)["warnings"])


@pytest.mark.parametrize("variant", VARIANTS)
@pytest.mark.parametrize("count", (0, 1, 10, 255))
def test_saved_fault_count_limits_visible_slots_without_mutation(variant, count):
    image = _fault_image(variant, tuple(range(10)), count=count)
    report = eeprom.inspect_image(image, variant)
    rows = {row["id"]: row for row in report["decoded_fields"]}
    assert all((f"fault_slot_{index}" in rows) is (index < min(count, 10)) for index in range(11))
    assert len(rows) == len(report["decoded_fields"]) <= 768
    assert rows["dtc_occurrence_count"]["value"] == count
    assert ("exceeds ten" in " ".join(report["warnings"])) is (count > 10)
    json.dumps(report, allow_nan=False)


@pytest.mark.parametrize("variant", VARIANTS)
def test_unknown_fault_ids_and_stale_record_checks_do_not_acquire_sensor_units(variant):
    unknown_id = {"MS41.0": 52, "MS41.1": 97, "MS41.2": 95, "MS41.3": 95}[variant]
    image = bytearray(_fault_image(variant, (unknown_id,)))
    record = _record_for(variant, eeprom.DECODE_LAYOUTS[variant]["dtc_occurrence"])
    image[record.offset + record.length - 2] ^= 0x80
    report = eeprom.inspect_image(image, variant)
    rows = _rows(image, variant)
    assert "Unknown internal ID" in rows["fault_slot_0"]["label"]
    assert "Stale / invalid" in rows["fault_slot_0"]["display"]
    assert rows["fault_slot_0"]["check_ok"] is False
    assert rows["fault_slot_0_internal_id"]["value"] is None
    assert "outside the" in " ".join(report["warnings"])
    for index in range(4):
        row = rows[f"fault_slot_0_env_{index}"]
        assert row["value"] is None and row["unit"] == "" and not row["editable"]
    assert "fault_slot_0_status_battery" not in rows


@pytest.mark.parametrize("variant", VARIANTS)
def test_saved_fault_source_whitelist_does_not_inherit_wrong_bmw_environment_rows(variant):
    common = _rows(_fault_image(variant, (2, 44)), variant)
    assert common["fault_slot_0_env_0"]["value"] == 3200
    assert common["fault_slot_0_env_2"]["value"] == pytest.approx(80 * 0.7471 - 48)
    assert common["fault_slot_0_env_3"]["value"] == pytest.approx(120 * 0.1020)
    assert common["fault_slot_1_self_test_reason"]["raw"] == "50 78"
    assert common["fault_slot_1_self_test_reason"]["display"] == "0x7850"
    assert "fault_slot_1_env_2" not in common and "fault_slot_1_env_3" not in common
    load = _rows(_fault_image(variant, (0,)), variant)
    assert load["fault_slot_0_env_1"]["value"] == pytest.approx(123 * 5.4471)
    assert load["fault_slot_0_env_1"]["unit"] == "mg/stroke"
    assert "Engine load" in load["fault_slot_0_env_1"]["label"]
    if variant != "MS41.0":
        rows = _rows(_fault_image(variant, (92, 68)), variant)  # Codes 46 and 238.
        assert "046" in rows["fault_slot_0"]["label"]
        assert rows["fault_slot_0_env_2"]["unit"] == "deg C"
        assert rows["fault_slot_0_env_3"]["unit"] == "V"
        assert "238" in rows["fault_slot_1"]["label"]
        assert rows["fault_slot_1_env_0"]["unit"] == "deg C"
        assert all(not rows[f"fault_slot_1_env_{index}"]["editable"] for index in (1, 2, 3))

    intake = _rows(_fault_image(variant, (6,)), variant)
    assert intake["fault_slot_0_env_2"]["value"] == pytest.approx(80 * 0.7471 - 48)
    assert intake["fault_slot_0_env_2"]["unit"] == "deg C"
    assert "Intake-air temperature" in intake["fault_slot_0_env_2"]["label"]


@pytest.mark.parametrize(("variant", "descriptor_count"), (
    ("MS41.0", 52), ("MS41.1", 97), ("MS41.2", 95), ("MS41.3", 95),
))
def test_saved_fault_environment_whitelist_contains_only_traced_sources(variant, descriptor_count):
    _, codes, environments, _ = eeprom._FAULT_LAYOUTS[variant]
    assert len(codes) == len(environments) == descriptor_count
    identifiers = {identifier for row in environments for identifier in row}
    expected = {
        0, 1, 2, 5, 6, 9, 10, 11, 12, 13, 14, 16, 19, 21, 22, 24, 25, 26, 27,
        *range(eeprom._ENV_THROTTLE_SIGNAL,
               (eeprom._ENV_FRONT_HEATER_2 if variant == "MS41.0"
                else eeprom._ENV_REAR_HEATER_2) + 1),
        eeprom._ENV_FRONT_O2_ENVELOPE_1, eeprom._ENV_FRONT_O2_ENVELOPE_2,
    }
    if variant == "MS41.0":
        expected.add(eeprom._ENV_VANOS_POSITION_MS410)
    else:
        expected.update((eeprom._ENV_TANK_PRESSURE_SIGNAL, eeprom._ENV_STARTUP_ECT,
                         eeprom._ENV_STARTUP_IAT, eeprom._ENV_VANOS_POSITION_LATER))
        if variant == "MS41.1":
            expected.update((eeprom._ENV_REAR_O2_ERROR_1, eeprom._ENV_REAR_O2_ERROR_2))
    assert identifiers == expected
    assert eeprom.dtc.environment_definition(0) is None
    assert all(eeprom._fault_environment_definition(identifier) is not None
               for identifier in identifiers - {0})


@pytest.mark.parametrize("variant", VARIANTS)
def test_pointer_owned_saved_environments_decode_without_per_code_label_guessing(variant):
    throttle = _rows(_fault_image(variant, (1,)), variant)
    assert throttle["fault_slot_0_env_1"]["value"] == pytest.approx(123 * 0.4686)
    assert throttle["fault_slot_0_env_1"]["unit"] == "degrees"
    assert throttle["fault_slot_0_env_2"]["value"] == pytest.approx(80 * 0.3906)
    assert throttle["fault_slot_0_env_2"]["unit"] == "%"
    assert throttle["fault_slot_0_env_3"]["value"] == pytest.approx(120 * 0.0196)
    assert "Air-flow-meter voltage" in throttle["fault_slot_0_env_3"]["label"]

    signal = _rows(_fault_image(variant, (0, 5)), variant)
    assert signal["fault_slot_0_env_3"]["value"] == pytest.approx(120 * 0.01952)
    assert "Throttle-sensor signal" in signal["fault_slot_0_env_3"]["label"]
    assert signal["fault_slot_1_env_3"]["value"] == pytest.approx(120 * 0.0196)
    assert "Intake-air sensor signal" in signal["fault_slot_1_env_3"]["label"]

    air = _rows(_fault_image(variant, (0x20, 0x10, 0x11, 0x23, 0x07)), variant)
    assert air["fault_slot_0_env_1"]["value"] == 123 * 4
    assert air["fault_slot_0_env_1"]["unit"] == "kg/h"
    assert air["fault_slot_0_env_2"]["value"] == pytest.approx(80 * 0.4686)
    assert air["fault_slot_0_env_3"]["value"] == pytest.approx(120 * 0.3906)
    assert air["fault_slot_1_env_3"]["value"] == pytest.approx(120 * 0.3906 - 50)
    assert air["fault_slot_2_env_3"]["value"] == pytest.approx(120 * 0.3906 - 50)
    assert air["fault_slot_3_env_1"]["value"] == 123
    assert air["fault_slot_3_env_1"]["unit"] == "km/h"
    assert air["fault_slot_4_env_2"]["value"] == pytest.approx(80 * 0.391)
    assert "Front oxygen-sensor heater bank 1" in air["fault_slot_4_env_2"]["label"]
    assert air["fault_slot_4_env_3"]["value"] == pytest.approx(120 * 0.0196)

    state = _rows(_fault_image(variant, (2,)), variant)["fault_slot_0_env_1"]
    assert state["value"] == 123 and state["unit"] == "raw"
    assert "Engine operating state" in state["label"]
    if variant != "MS41.0":
        rear = _rows(_fault_image(variant, (0x50,)), variant)
        assert rear["fault_slot_0_env_2"]["value"] == pytest.approx(80 * 0.391)
        assert "Rear oxygen-sensor heater bank 1" in rear["fault_slot_0_env_2"]["label"]
        assert rear["fault_slot_0_env_3"]["value"] == pytest.approx(120 * 0.0196)
        assert "Rear oxygen-sensor voltage bank 1" in rear["fault_slot_0_env_3"]["label"]


@pytest.mark.parametrize("variant", VARIANTS)
def test_deeper_saved_fault_sources_use_exact_family_producers(variant):
    gear = _rows(_fault_image(variant, (0x04,)), variant)["fault_slot_0_env_2"]
    assert gear["value"] == 80 and gear["unit"] == "raw"
    assert "Gear information" in gear["label"]

    coolant = _rows(_fault_image(variant, (0x06,)), variant)["fault_slot_0_env_3"]
    assert coolant["value"] == pytest.approx(120 * 0.0196)
    assert "Coolant-sensor voltage" in coolant["label"]

    envelope = _rows(_fault_image(variant, (0x09,)), variant)["fault_slot_0_env_1"]
    assert envelope["value"] == pytest.approx(123 * 5 / 256)
    assert "Tracked front oxygen-sensor envelope bank 1" in envelope["label"]

    purge = _rows(_fault_image(variant, (0x0F,)), variant)["fault_slot_0_env_3"]
    assert purge["value"] == pytest.approx(120 * 0.391)
    assert "Tank-vent valve command" in purge["label"]

    knock = _rows(_fault_image(variant, (0x1E,)), variant)
    assert knock["fault_slot_0_env_2"]["value"] == pytest.approx(80 * 0.00392)
    assert knock["fault_slot_0_env_2"]["unit"] == "ratio"
    assert knock["fault_slot_0_env_3"]["value"] == pytest.approx(120 * 0.02)
    assert knock["fault_slot_0_env_3"]["unit"] == "V"

    vanos = _rows(_fault_image(variant, (0x25,)), variant)["fault_slot_0_env_2"]
    assert vanos["value"] == pytest.approx(80 * (0.375 if variant == "MS41.0" else 0.3745))
    assert vanos["unit"] == "degrees crank"


@pytest.mark.parametrize("variant", VARIANTS)
def test_ignition_fault_timer_bytes_are_one_big_endian_family_value(variant):
    before = _fault_image(variant, (0x16,))
    rows = _rows(before, variant)
    assert "fault_slot_0_env_2" not in rows and "fault_slot_0_env_3" not in rows
    field_id = ("fault_slot_0_spark_burn_duration_ms" if variant in ("MS41.0", "MS41.1")
                else "fault_slot_0_rough_running_metric")
    row = rows[field_id]
    assert row["raw"] == "50 78" and row["byteorder"] == "big"
    expected = 0x5078 * (0.00534004716564 if variant in ("MS41.0", "MS41.1") else 1)
    assert row["value"] == pytest.approx(expected)
    target_raw = 0x1234
    target = eeprom.set_decoded_field(
        before, variant, field_id, str(target_raw * row["step"]), allow_advanced=True)
    assert target[row["offset"]:row["offset"] + 2] == b"\x12\x34"
    _assert_field_write_boundary(before, target, variant, field_id)


@pytest.mark.parametrize("variant", ("MS41.1", "MS41.2", "MS41.3"))
def test_later_saved_fault_sources_keep_family_specific_evidence_boundaries(variant):
    tank = _rows(_fault_image(variant, (0x34,)), variant)["fault_slot_0_env_3"]
    assert tank["value"] == pytest.approx(120 * 5 / 256)
    assert "Tank-pressure sensor signal" in tank["label"]

    envelope = _rows(_fault_image(variant, (0x3D,)), variant)["fault_slot_0_env_2"]
    assert envelope["value"] == pytest.approx(80 * 5 / 256)
    assert "Tracked front oxygen-sensor envelope bank 1" in envelope["label"]

    rear_error = _rows(_fault_image(variant, (0x52,)), variant)["fault_slot_0_env_3"]
    if variant == "MS41.1":
        assert rear_error["value"] == pytest.approx((120 - 128) * 0.0390625)
        assert "setpoint error bank 1" in rear_error["label"]
    else:
        assert rear_error["value"] is None and rear_error["confidence"] == "UNRESOLVED"


@pytest.mark.parametrize("variant", VARIANTS)
def test_qualifier_bits_require_supported_rows_and_plausibility_is_family_wide(variant):
    missing_ids = {"MS41.0": (49, 50, 51), "MS41.1": (51, 94, 95, 96),
                   "MS41.2": (51, 94), "MS41.3": (51, 94)}[variant]
    for identifier in missing_ids:
        rows = _rows(_fault_image(variant, (identifier,)), variant)
        assert all(f"fault_slot_0_status_{key}" not in rows for key in ("battery", "ground", "open"))
    before = bytearray(_fault_image(variant))
    before[0x00C] ^= 0x80
    for key, mask in (("stored", 0x20), ("present", 0x40), ("sporadic", 0x80), ("emissions", 0x10),
                      ("plausibility", 0x08), ("battery", 1), ("ground", 2), ("open", 4)):
        field_id = f"fault_slot_0_status_{key}"
        row = _rows(before, variant)[field_id]
        assert row["bit_mask"] == mask
        for value in ("0", "1"):
            target = eeprom.set_decoded_field(before, variant, field_id, value, allow_advanced=True)
            assert target[row["offset"]] == (before[row["offset"]] & ~mask) | (mask if value == "1" else 0)
            if mask != 0x08:
                assert target[row["offset"]] & 0x08
            _assert_field_write_boundary(before, target, variant, field_id)
            assert target[0x00C] == before[0x00C]
        with pytest.raises(ValueError, match="requires advanced"):
            eeprom.set_decoded_field(before, variant, field_id, "1")


@pytest.mark.parametrize("variant", VARIANTS)
def test_saved_fault_numeric_edits_preserve_siblings_and_only_recheck_changed_record(variant):
    before = bytearray(_fault_image(variant, (0, 1)))
    before[0x00C] ^= 0x80
    record = _record_for(variant, eeprom.DECODE_LAYOUTS[variant]["dtc_occurrence"])
    before[record.offset + record.length - 2] ^= 0x80
    for field_id, value, raw in (
        ("fault_slot_0_frequency", "4", b"\x04"), ("fault_slot_0_logistics", "255", b"\xff"),
        ("fault_slot_0_raw_debounce", "42", b"\x2a"),
        ("fault_slot_0_env_0", "3232", b"\x65"), ("fault_slot_0_env_2", "-48", b"\x00"),
        ("fault_slot_0_env_1", "544.71", b"\x64"),
        ("fault_slot_0_env_3", "1.952", b"\x64"),
        ("fault_slot_0_operating_time_hours", "1.2", b"\x00\x0c"),
    ):
        row = _rows(before, variant)[field_id]
        assert eeprom.set_decoded_field(before, variant, field_id, row["display"], allow_advanced=True) == before
        target = eeprom.set_decoded_field(before, variant, field_id, value, allow_advanced=True)
        assert target[row["offset"]:row["offset"] + row["length"]] == raw
        assert _rows(target, variant)[field_id]["check_ok"] is True
        assert _rows(target, variant)["identity_gate_value"]["check_ok"] is False
        _assert_field_write_boundary(before, target, variant, field_id)
    with pytest.raises(ValueError, match="read-only"):
        eeprom.set_decoded_field(before, variant, "fault_slot_0_status", "1", allow_advanced=True)


@pytest.mark.parametrize("variant", ("MS41.1", "MS41.2", "MS41.3"))
def test_secondary_fault_state_masked_edits_preserve_unknown_sibling_bits(variant):
    before = bytearray(_fault_image(variant))
    start = eeprom.DECODE_LAYOUTS[variant]["dtc_occurrence"]
    stride = eeprom._FAULT_LAYOUTS[variant][0]
    state_offset = start + 11 + 10
    before[state_offset] = 0xFF
    if stride == 12:
        before[state_offset + 1] = 9
    record = _record_for(variant, start)
    end = record.offset + record.length
    before[end - 2:end] = eeprom.additive_check(before[record.offset:end - 2]).to_bytes(2, "little")
    rows = _rows(before, variant)
    assert rows["fault_slot_0_secondary_stage"]["display"] == "Stage 3"
    assert rows["fault_slot_0_secondary_terminal_latch"]["display"] == "Latched"
    assert rows["fault_slot_0_secondary_transition_handled"]["display"] == "Handled"
    assert rows["fault_slot_0_secondary_delay_elapsed"]["display"] == "Elapsed"
    assert rows["fault_slot_0_secondary_delay_initialized"]["display"] == "Initialized"
    target = eeprom.set_decoded_field(
        before, variant, "fault_slot_0_secondary_stage", "1", allow_advanced=True)
    assert target[state_offset] == 0xFD
    _assert_field_write_boundary(before, target, variant, "fault_slot_0_secondary_stage")
    target = eeprom.set_decoded_field(
        before, variant, "fault_slot_0_secondary_terminal_latch", "0", allow_advanced=True)
    assert target[state_offset] == 0xF3
    _assert_field_write_boundary(before, target, variant, "fault_slot_0_secondary_terminal_latch")
    if stride == 12:
        countdown = rows["fault_slot_0_secondary_delay_countdown"]
        assert countdown["value"] == 9 and countdown["unit"] == "internal counts"
        target = eeprom.set_decoded_field(before, variant, countdown["id"], "8", allow_advanced=True)
        assert target[state_offset:state_offset + 2] == bytes((0xFF, 8))
        _assert_field_write_boundary(before, target, variant, countdown["id"])
    else:
        assert "fault_slot_0_secondary_delay_countdown" not in rows


@pytest.mark.parametrize("variant", ("MS41.1", "MS41.2", "MS41.3"))
def test_auxiliary_air_faults_store_temperatures_rpm_and_battery_not_t_nb_dte(variant):
    identifier = eeprom._FAULT_LAYOUTS[variant][1].index(245)
    before = _fault_image(variant, (identifier,))
    rows = _rows(before, variant)
    assert "fault_slot_0_t_nb_dte_seconds" not in rows
    assert rows["fault_slot_0_env_0"]["value"] == pytest.approx(100 * 0.7471 - 48)
    assert "Startup/reference coolant" in rows["fault_slot_0_env_0"]["label"]
    assert rows["fault_slot_0_env_1"]["value"] == pytest.approx(123 * 0.7471 - 48)
    assert "Startup/reference intake-air" in rows["fault_slot_0_env_1"]["label"]
    assert rows["fault_slot_0_env_2"]["value"] == 80 * 32
    assert rows["fault_slot_0_env_2"]["unit"] == "rpm"
    assert rows["fault_slot_0_env_3"]["value"] == pytest.approx(120 * 0.102)
    assert rows["fault_slot_0_env_3"]["unit"] == "V"


@pytest.mark.parametrize("variant", VARIANTS)
def test_saved_fault_internal_id_edit_changes_only_the_id_and_occurrence_check(variant):
    before = _fault_image(variant, (1,))
    row = _rows(before, variant)["fault_slot_0_internal_id"]
    target = eeprom.set_decoded_field(before, variant, row["id"], "2", allow_advanced=True)
    assert target[row["offset"]] == 2
    assert "083" in _rows(target, variant)["fault_slot_0"]["label"]
    assert _rows(target, variant)["fault_slot_0_env_2"]["unit"] == "deg C"
    _assert_field_write_boundary(before, target, variant, row["id"])
    slot = _rows(before, variant)["fault_slot_0"]
    assert target[slot["offset"]:slot["offset"] + slot["length"]] == before[slot["offset"]:slot["offset"] + slot["length"]]


@pytest.mark.parametrize("variant", ("MS41.1", "MS41.2", "MS41.3"))
@pytest.mark.parametrize("condition", ("valid", "absent", "empty", "bad_occurrence", "bad_snapshot", "mismatch", "unknown", "special"))
def test_saved_freeze_snapshot_availability_is_not_a_live_zero_reading(variant, condition):
    before = bytearray(_fault_image(variant, (0, 1)))
    offsets = eeprom._FAULT_SNAPSHOTS[variant]
    start = eeprom.DECODE_LAYOUTS[variant]["dtc_occurrence"]
    if condition == "absent":
        before[offsets["flags"]] &= ~0x10
    elif condition == "empty":
        before[start] = 0
        before[offsets["internal_id"]] = 0xFF
    elif condition in ("mismatch", "unknown", "special"):
        before[offsets["internal_id"]] = {"mismatch": 2, "unknown": 254, "special": 255}[condition]
    for record in eeprom.fields_for_variant(variant):
        if record.checked:
            end = record.offset + record.length
            before[end - 2:end] = eeprom.additive_check(before[record.offset:end - 2]).to_bytes(2, "little")
    if condition in ("bad_occurrence", "bad_snapshot"):
        record = _record_for(variant, start if condition == "bad_occurrence" else offsets["rpm"])
        before[record.offset + record.length - 2] ^= 0x80
    rows = _rows(before, variant)
    available = condition in ("valid", "special")
    assert rows["fault_snapshot_rpm"]["value"] == (2750 if available else None)
    assert rows["fault_snapshot_load_mg_stroke"]["value"] == (pytest.approx(544.71) if available else None)
    assert rows["fault_snapshot_coolant_celsius"]["value"] == (pytest.approx(160 * 0.747 - 48) if available else None)
    assert rows["fault_snapshot_speed_kmh"]["value"] == (87 if available else None)
    assert ("Unavailable" in rows["fault_snapshot"]["display"]) is not available
    assert rows["fault_snapshot_rpm"]["editable"] and rows["fault_snapshot_rpm"]["requires_advanced"]
    if available:
        assert rows["fault_snapshot_stft_1_percent"]["value"] == pytest.approx(-4096 * 100 / 65535)
        assert rows["fault_snapshot_stft_2_percent"]["value"] == pytest.approx(4096 * 100 / 65535)
        assert rows["fault_snapshot_ltft_1_percent"]["value"] == 0
        assert rows["fault_snapshot_pp1_1_raw"]["value"] == 0x1234
        assert rows["fault_snapshot_pp1_1_raw"]["unit"] == "STFT count"
        assert "Last stored lambda-integrator step" in rows["fault_snapshot_pp1_1_raw"]["label"]
        assert "neither current STFT" in rows["fault_snapshot_pp1_1_raw"]["description"]
        assert rows["fault_snapshot_pt2_2_raw"]["value"] == 0xDEF0
        assert "Bit 1 participates" in rows["fault_snapshot_pt2_2_raw"]["description"]
        assert rows["fault_snapshot_lambda_regulation_1_active"]["display"] == "Active"
        assert rows["fault_snapshot_lambda_regulation_2_active"]["display"] == "Inactive"
    else:
        assert rows["fault_snapshot_rpm"]["display"].startswith("Unavailable")
        assert rows["fault_snapshot_pp1_1_raw"]["value"] is None
        assert rows["fault_snapshot_lambda_regulation_1_active"]["value"] is None
        with pytest.raises(ValueError, match="finite number"):
            eeprom.set_decoded_field(before, variant, "fault_snapshot_rpm", rows["fault_snapshot_rpm"]["display"],
                                     allow_advanced=True)
    assert rows["fault_snapshot_raw"]["raw"]
    target = eeprom.set_decoded_field(before, variant, "fault_snapshot_rpm", "4321", allow_advanced=True)
    assert target[offsets["rpm"]:offsets["rpm"] + 2] == (4321).to_bytes(2, "little")
    _assert_field_write_boundary(before, target, variant, "fault_snapshot_rpm")
    assert target[offsets["flags"]] == before[offsets["flags"]]
    assert target[offsets["internal_id"]] == before[offsets["internal_id"]]
    occurrence = _record_for(variant, start)
    assert target[start:start + occurrence.length] == before[start:start + occurrence.length]


@pytest.mark.parametrize("variant", ("MS41.1", "MS41.2", "MS41.3"))
def test_snapshot_numeric_roundtrip_flags_and_id_preserve_unrelated_bytes(variant):
    before = _fault_image(variant)
    rows = _rows(before, variant)
    for key in ("rpm", "load_mg_stroke", "coolant_celsius", "speed_kmh", "stft_1_percent", "stft_2_percent",
                "ltft_1_percent", "ltft_2_percent", "pp1_1_raw", "pp1_2_raw", "pt2_1_raw", "pt2_2_raw",
                "lambda_state_1_raw", "lambda_state_2_raw"):
        field_id = f"fault_snapshot_{key}"
        row = rows[field_id]
        values = ((row["minimum"], row["minimum"] + 64 * row["step"], row["maximum"])
                  if key == "coolant_celsius" else (row["minimum"], 0, row["maximum"]))
        for value in values:
            target = eeprom.set_decoded_field(before, variant, field_id, str(value), allow_advanced=True)
            current = _rows(target, variant)[field_id]
            assert current["value"] == pytest.approx(value)
            assert eeprom.set_decoded_field(target, variant, field_id, current["display"], allow_advanced=True) == target
            _assert_field_write_boundary(before, target, variant, field_id)
    expected_pt2 = {"MS41.1": (0x1A6, 0x1A8), "MS41.2": (0x1A4, 0x1A6),
                    "MS41.3": (0x1A4, 0x1A6)}[variant]
    assert tuple(rows[f"fault_snapshot_pt2_{bank}_raw"]["offset"] for bank in (1, 2)) == expected_pt2
    for bank, expected in ((1, 0x08), (2, 0x00)):
        field_id = f"fault_snapshot_lambda_regulation_{bank}_active"
        row = rows[field_id]
        assert row["bit_mask"] == 0x08
        for value in ("0", "1"):
            target = eeprom.set_decoded_field(before, variant, field_id, value, allow_advanced=True)
            assert target[row["offset"]] == (before[row["offset"]] & ~0x08) | (0x08 if value == "1" else 0)
            _assert_field_write_boundary(before, target, variant, field_id)
        assert before[row["offset"]] & 0x08 == expected
    flag = rows["fault_snapshot_available"]
    target = eeprom.set_decoded_field(before, variant, flag["id"], "0", allow_advanced=True)
    assert target[flag["offset"]] == before[flag["offset"]] & ~16
    assert _rows(target, variant)["fault_snapshot_rpm"]["value"] is None
    _assert_field_write_boundary(before, target, variant, flag["id"])
    target = eeprom.set_decoded_field(before, variant, "fault_snapshot_internal_id", "255", allow_advanced=True)
    assert _rows(target, variant)["fault_snapshot_rpm"]["value"] == 2750
    _assert_field_write_boundary(before, target, variant, "fault_snapshot_internal_id")


@pytest.mark.parametrize("variant", ("MS41.1", "MS41.2", "MS41.3"))
def test_snapshot_capture_state_and_replacement_latches_are_masked_cross_family_fields(variant):
    before = bytearray(_fault_image(variant))
    offsets = eeprom._FAULT_SNAPSHOTS[variant]
    before[offsets["state"]] = 0xF9  # Upper bits plus normal DTC-associated state 1.
    before[offsets["flags"]] = 0xFF
    record = _record_for(variant, offsets["state"])
    end = record.offset + record.length
    before[end - 2:end] = eeprom.additive_check(
        before[record.offset:end - 2]).to_bytes(2, "little")
    rows = _rows(before, variant)

    state = rows["fault_snapshot_capture_state"]
    assert state["value"] == "1" and state["display"] == "Local DME fault-associated snapshot"
    assert state["bit_mask"] == 0x07
    assert [option["value"] for option in state["options"]] == ["0", "1", "2"]
    target = eeprom.set_decoded_field(
        before, variant, state["id"], "2", allow_advanced=True)
    assert target[offsets["state"]] == 0xFA
    _assert_field_write_boundary(before, target, variant, state["id"])

    retention = rows["fault_snapshot_retention_tier"]
    assert retention["bit_mask"] == 0x60
    assert retention["value"] == "3"
    assert "effectively locked" in retention["display"]
    for value, expected in (("0", 0x9F), ("1", 0xBF), ("2", 0xDF), ("3", 0xFF)):
        target = eeprom.set_decoded_field(
            before, variant, retention["id"], value, allow_advanced=True)
        assert target[offsets["flags"]] == expected
        _assert_field_write_boundary(before, target, variant, retention["id"])

    unknown = bytearray(before)
    unknown[offsets["state"]] = 0xFF
    unknown[end - 2:end] = eeprom.additive_check(
        unknown[record.offset:end - 2]).to_bytes(2, "little")
    unknown_state = _rows(unknown, variant)["fault_snapshot_capture_state"]
    assert unknown_state["value"] is None
    assert "non-stock state 7" in unknown_state["display"]
    warnings = " ".join(eeprom.inspect_image(unknown, variant)["warnings"])
    assert "unknown/non-stock capture state 7" in warnings
    assert "non-stock persisted bits" in warnings
    assert "mutually exclusive" in warnings


def test_ms410_does_not_inherit_the_later_family_snapshot():
    rows = _rows(_fault_image("MS41.0"), "MS41.0")
    assert not any(field_id.startswith("fault_snapshot") for field_id in rows)
