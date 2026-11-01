"""Storage presentation must never quantize or re-encode unchanged bytes."""

import math

import pytest

from ms42_ms43_diagnostics import format_storage_inspection
from ms42_ms43_storage_edit import compare_storage_fields, editable_fields, edit_storage
from ms42_ms43_storage_units import (
    _FIELDS, storage_display_value, storage_field_presentation,
    storage_format_value, storage_raw_value,
)
from tests.test_ms42_ms43_storage_calibration import calibrated


def quantities():
    unique = {}
    for fields in _FIELDS.values():
        for field in fields.values():
            if "scale" in field:
                unique.setdefault((field["_scale_ratio"], field["_bias_ratio"], field["decimals"]), field)
    return list(unique.values())


@pytest.mark.parametrize("field", quantities())
def test_display_precision_roundtrips_every_signed_and_unsigned_word(field):
    # Includes out-of-admission existing data: opening an editor cannot normalize it.
    for raw in range(-32768, 65536):
        shown = f"{storage_display_value(field, raw):.{field['decimals']}f}"
        assert storage_raw_value(field, shown) == raw


@pytest.mark.parametrize("value", (math.inf, -math.inf, math.nan))
def test_nonfinite_values_rejected(value):
    with pytest.raises(ValueError, match="finite"):
        storage_raw_value(storage_field_presentation("MS43", 0x348), value)


@pytest.mark.parametrize("family", ("MS42", "MS43"))
def test_display_roundtrip_noop_preserves_full_storage_and_native_checks(family):
    snapshot, calibration = calibrated(family)
    fields = editable_fields(snapshot, calibration=calibration)
    converted = {field["offset"]: storage_raw_value(field, round(
        storage_display_value(field, field["value"]), field.get("decimals", 0))) for field in fields}
    result = edit_storage(snapshot, converted, calibration=calibration)
    assert result == snapshot
    base = 0x410 if family == "MS42" else 0
    offset = base + (0x7A if family == "MS42" else 0x2E8)
    additive = next(field for field in fields if field["offset"] == offset)
    assert additive["value"] == 32768 and additive["unit"] == "ms"
    assert "raw" not in additive["label"]
    assert storage_format_value(additive, 32768) == "0.000 ms"
    changed = edit_storage(snapshot, {offset: storage_raw_value(additive, "0.012")}, calibration=calibration)
    assert next(field for field in editable_fields(changed, calibration=calibration)
                if field["offset"] == offset)["value"] == 32771
    difference = next(row for row in compare_storage_fields(snapshot, changed)
                      if row["label"] == additive["label"])
    assert (difference["offset"], difference["length"]) == (offset, 2)
    assert (difference["before"], difference["after"]) == ("0.000 ms", "0.012 ms")
    text = format_storage_inspection(changed["data"], family, changed["software_id"], changed["kind"])
    assert "0.012 ms" in text


def test_independent_family_units_and_state_labels_do_not_invent_meanings():
    for family, knock in (("MS42", 0x18), ("MS43", 0x2A4)):
        field = storage_field_presentation(family, knock)
        assert storage_format_value(field, 128) == "0.000 °"
        assert storage_format_value(field, 116) == "-4.500 °"
    assert storage_format_value(storage_field_presentation("MS43", 0x300), 32768) == "1.00000 ×"
    assert storage_format_value(storage_field_presentation("MS43", 0x3D8), 256) == "1.0000 ×"
    assert storage_format_value(storage_field_presentation("MS43", 0x32A), 255) == "Unset (FF)"
    assert storage_format_value(storage_field_presentation("MS43", 0x32B), 120) == "42.00 °C"
    assert storage_format_value(storage_field_presentation("MS43", 0x3E2), 1) == "Type 1"
    assert storage_field_presentation("MS43", 0x354) == {}  # No invented seconds for ticks.
    assert storage_field_presentation("MS43", 0x3D4) == {}  # No invented percentage for charge state.
    assert storage_field_presentation("MS42", 0x58) == {}  # No borrowing another family's factor.


def test_exact_rounding_does_not_clamp_or_alter_erased_sentinels():
    field = storage_field_presentation("MS43", 0x348)
    assert storage_raw_value(field, "0.00244140625") == 1
    assert storage_raw_value(field, "-0.00244140625") == -1
    assert storage_raw_value(field, storage_display_value(field, 0xFFFF)) == 0xFFFF
    assert storage_format_value({"excluded_values": (-1,)}, -1) == "Unset (FFFF)"
