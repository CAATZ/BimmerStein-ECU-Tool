"""Presentation of storage values; editing and persistence always use raw integers.

Scales are limited to fields traced from their native storage owner to a
fixed-point consumer or a matching diagnostic measurement.
"""

from fractions import Fraction
import math


def _quantity(unit, scale, bias=0, *, decimals=0, label=None):
    scale, bias = Fraction(scale), Fraction(bias)
    result = {"unit": unit, "scale": float(scale), "bias": float(bias),
              "decimals": decimals, "_scale_ratio": (scale.numerator, scale.denominator),
              "_bias_ratio": (bias.numerator, bias.denominator)}
    if label is not None:
        result["label"] = label
    return result


_PERCENT = _quantity("%", Fraction(100, 65536), decimals=4)
_FUEL_ADD = _quantity("ms", Fraction(1, 250), Fraction(-16384, 125), decimals=3)
_FUEL_FACTOR = _quantity("%", Fraction(100, 65536), -50, decimals=4)
_Q15_FACTOR = _quantity("×", Fraction(1, 32768), decimals=5)
_Q16_FACTOR = _quantity("×", Fraction(1, 65536), decimals=5)
_Q8_FACTOR = _quantity("×", Fraction(1, 256), decimals=4)
_COOLANT = _quantity("°C", Fraction(3, 4), -48, decimals=2)
_BOOLEAN = {"choice_labels": {0: "No", 1: "Yes"}}

_FIELDS = {
    "MS42": {
        **{offset: _PERCENT for offset in (0x104, 0x106, 0x108)},
        **{offset: _FUEL_ADD for offset in (0x7A, 0x7E)},
        **{offset: _FUEL_FACTOR for offset in (0x7C, 0x80)},
        **{offset: _quantity("°", Fraction(3, 8), -48, decimals=3)
           for offset in range(0x18, 0x58)},
        0x200: _quantity("°", Fraction(3, 2048), 60, decimals=4,
                         label="Intake camshaft reference"),
        0x202: _quantity("°", Fraction(-3, 2048), -60, decimals=4,
                         label="Exhaust camshaft reference"),
        0x1EE: {**_COOLANT, "label": "Retained coolant temperature 1",
                "sentinel_values": {255: "Unset (FF)"}},
        0x1EF: {**_COOLANT, "label": "Retained coolant temperature 2"},
        **{offset: _BOOLEAN for offset in (0x212, 0x213)},
        0x216: _quantity("starts", 1),
        0x22B: _quantity("events", 1),
        0x22C: {**_quantity("rpm", 32), "sentinel_values": {255: "At least 8160 rpm"}},
        0x22D: _quantity("V", Fraction(2549, 25000), decimals=3),
        0x22E: _COOLANT,
    },
    "MS43": {
        **{offset: _PERCENT for offset in (0x280, 0x282, 0x284)},
        **{offset: _FUEL_ADD for offset in (0x2E8, 0x2EC)},
        **{offset: _FUEL_FACTOR for offset in (0x2EA, 0x2EE)},
        0x28A: _quantity("°", Fraction(3, 2048), 60, decimals=4,
                         label="Intake camshaft reference"),
        0x28C: _quantity("°", Fraction(-3, 2048), -60, decimals=4,
                         label="Exhaust camshaft reference"),
        **{offset: _quantity("°", Fraction(3, 8), -48, decimals=3)
           for offset in range(0x2A4, 0x2E4)},
        0x300: {**_Q15_FACTOR, "label": "Ambient pressure correction factor"},
        0x3D8: {**_Q8_FACTOR, "label": "Gear-estimation correction factor"},
        **{offset: _Q16_FACTOR for offset in (0x2FA, 0x2FC)},
        0x32A: {**_COOLANT, "label": "Retained coolant temperature 1",
                "sentinel_values": {255: "Unset (FF)"}},
        0x32B: {**_COOLANT, "label": "Retained coolant temperature 2"},
        **{offset: _quantity("V", Fraction(5, 1024), decimals=4, label=label)
           for offset, label in ((0x348, "Throttle mechanical stop 1"),
                                  (0x34A, "Throttle mechanical stop 2"),
                                  (0x34C, "Throttle limp-home stop 1"),
                                  (0x34E, "Throttle limp-home stop 2"))},
        **{offset: _BOOLEAN for offset in (0x29C, 0x29D, 0x31A, 0x31B, 0x3E6)},
        0x2A2: {"choice_labels": {0: "Clear (0000)", 0xFFFF: "Set (FFFF)"}},
        0x350: {"choice_labels": {0x5555: "Learned", 0xAAAA: "Fallback"}},
        0x3E2: {"choice_labels": {1: "Type 1", 2: "Type 2"}},
    },
}

# Actual revision record locations; only layouts with verified readers are mapped.
# Each value is (430069 layout counterpart, payload length).
MS43_STORAGE_RECORD_MAPS = {
    "430037": {
        0x3C: (0x3C, 4), 0x54: (0x56, 126), 0xD4: (0xD6, 190),
        0x194: (0x1A0, 6), 0x19C: (0x1A8, 28), 0x1BA: (0x1C6, 120),
        0x268: (0x27C, 2), 0x26C: (0x280, 8), 0x276: (0x28A, 20),
        0x28C: (0x2A0, 68), 0x2D2: (0x2E6, 10), 0x2DE: (0x2F2, 12),
        0x2EC: (0x300, 2), 0x2F0: (0x304, 6), 0x2F8: (0x31A, 2),
        0x300: (0x322, 6), 0x308: (0x32A, 2), 0x30C: (0x32E, 20),
        0x322: (0x348, 10), 0x32E: (0x354, 2), 0x332: (0x358, 18),
        0x346: (0x36C, 16), 0x358: (0x37E, 2), 0x3A8: (0x3D0, 2),
        0x3AC: (0x3D4, 2), 0x3B0: (0x3D8, 2),
    },
    "430055": {
        0x3C: (0x3C, 4), 0x56: (0x56, 126), 0xD6: (0xD6, 190),
        0x196: (0x1A0, 6), 0x19E: (0x1A8, 28), 0x1BC: (0x1C6, 120),
        0x26A: (0x27C, 2), 0x26E: (0x280, 8), 0x278: (0x28A, 20),
        0x28E: (0x2A0, 68), 0x2D4: (0x2E6, 10), 0x2E0: (0x2F2, 12),
        0x2EE: (0x300, 2), 0x2F2: (0x304, 20), 0x308: (0x31A, 2),
        0x310: (0x322, 6), 0x318: (0x32A, 2), 0x31C: (0x32E, 24),
        0x336: (0x348, 10), 0x342: (0x354, 2), 0x346: (0x358, 18),
        0x35A: (0x36C, 16), 0x36C: (0x37E, 2), 0x3BC: (0x3D0, 2),
        0x3C0: (0x3D4, 2), 0x3C4: (0x3D8, 2), 0x3C8: (0x3E2, 2),
        0x3CC: (0x3E6, 2), 0x3D0: (0x3DC, 4),
    },
    "430064": {
        0x3C: (0x3C, 4), 0x56: (0x56, 126), 0xD6: (0xD6, 190),
        0x196: (0x1A0, 6), 0x19E: (0x1A8, 28), 0x1BC: (0x1C6, 120),
        0x26A: (0x27C, 2), 0x26E: (0x280, 8), 0x278: (0x28A, 20),
        0x28E: (0x2A0, 68), 0x2D4: (0x2E6, 10), 0x2E0: (0x2F2, 12),
        0x2EE: (0x300, 2), 0x2F2: (0x304, 20), 0x308: (0x31A, 2),
        0x310: (0x322, 6), 0x318: (0x32A, 2), 0x31C: (0x32E, 24),
        0x336: (0x348, 10), 0x342: (0x354, 2), 0x346: (0x358, 18),
        0x35A: (0x36C, 16), 0x36C: (0x37E, 2), 0x3C0: (0x3D0, 2),
        0x3C4: (0x3D4, 2), 0x3C8: (0x3D8, 2), 0x3CC: (0x3E2, 2),
        0x3D0: (0x3E6, 2), 0x3D4: (0x3DC, 4),
    },
    "430065": {
        0x3C: (0x3C, 4), 0x56: (0x56, 126), 0xD6: (0xD6, 200),
        0x1A0: (0x1A0, 6), 0x1A8: (0x1A8, 28), 0x1C6: (0x1C6, 120),
        0x274: (0x27C, 2), 0x278: (0x280, 8), 0x282: (0x28A, 20),
        0x298: (0x2A0, 68), 0x2DE: (0x2E6, 10), 0x2EA: (0x2F2, 12),
        0x2F8: (0x300, 2), 0x2FC: (0x304, 20), 0x312: (0x31A, 2),
        0x31A: (0x322, 6), 0x322: (0x32A, 2), 0x326: (0x32E, 24),
        0x340: (0x348, 10), 0x34C: (0x354, 2), 0x350: (0x358, 18),
        0x364: (0x36C, 16), 0x376: (0x37E, 2), 0x3CA: (0x3D0, 2),
        0x3CE: (0x3D4, 2), 0x3D2: (0x3D8, 2), 0x3D6: (0x3E2, 2),
        0x3DA: (0x3E6, 2), 0x3DE: (0x3EA, 2), 0x3E2: (0x3DC, 4),
    },
    "430066": {
        0x3C: (0x3C, 4), 0x56: (0x56, 126), 0xD6: (0xD6, 200),
        0x1A0: (0x1A0, 6), 0x1A8: (0x1A8, 28), 0x1C6: (0x1C6, 128),
        0x27C: (0x27C, 2), 0x280: (0x280, 8), 0x28A: (0x28A, 20),
        0x2A0: (0x2A0, 68), 0x2E6: (0x2E6, 10), 0x2F2: (0x2F2, 12),
        0x300: (0x300, 2), 0x304: (0x304, 20), 0x31A: (0x31A, 2),
        0x322: (0x322, 6), 0x32A: (0x32A, 2), 0x32E: (0x32E, 24),
        0x348: (0x348, 10), 0x354: (0x354, 2), 0x358: (0x358, 18),
        0x36C: (0x36C, 16), 0x37E: (0x37E, 2), 0x3D0: (0x3D0, 2),
        0x3D4: (0x3D4, 2), 0x3D8: (0x3D8, 2), 0x3DC: (0x3DC, 4),
        0x3E2: (0x3E2, 2), 0x3E6: (0x3E6, 2), 0x3EA: (0x3EA, 2),
    },
}


def storage_field_presentation(family, offset, label=None, software_id=None):
    """Metadata for an admitted layout; MS42 offsets are within its payload."""
    family, offset = str(family).upper(), int(offset)
    if family == "MS43" and software_id in MS43_STORAGE_RECORD_MAPS:
        for actual, (counterpart, size) in MS43_STORAGE_RECORD_MAPS[software_id].items():
            if actual <= offset < actual + size:
                offset = counterpart + offset - actual
                break
        else:
            return {}
    elif software_id is not None and (family, software_id) not in {
            ("MS42", "0110C6"), ("MS42", "0110C7"), ("MS42", "0110CA"),
            ("MS43", "430069"), ("MS43", "43X001")}:
        return {}
    result = dict(_FIELDS.get(family, {}).get(offset, {}))
    if result and label is not None:
        result.setdefault("label", label.replace(" (raw)", ""))
    return result


def storage_display_value(field, raw):
    """Convert a raw integer without discarding its stored precision."""
    scale = Fraction(*field.get("_scale_ratio", (1, 1)))
    bias = Fraction(*field.get("_bias_ratio", (0, 1)))
    return float(int(raw) * scale + bias)


def storage_raw_value(field, display):
    """Return the nearest representable raw integer; ties round away from zero.

    Validation belongs to edit_storage. In particular this conversion does not
    clamp values, change sentinels, or silently admit values outside native bounds.
    """
    if not math.isfinite(float(display)):
        raise ValueError("Storage value must be finite.")
    scale = Fraction(*field.get("_scale_ratio", (1, 1)))
    bias = Fraction(*field.get("_bias_ratio", (0, 1)))
    value = (Fraction(str(display)) - bias) / scale
    rounded = (abs(value.numerator) * 2 + value.denominator) // (2 * value.denominator)
    return -rounded if value < 0 else rounded


def storage_format_value(field, raw):
    """Human-readable units, verified state labels, or explicit raw values."""
    raw = int(raw)
    if raw in field.get("sentinel_values", {}):
        return field["sentinel_values"][raw]
    if raw in field.get("excluded_values", ()):
        return "Unset (FFFF)"
    choices = field.get("choice_labels", {})
    if raw in choices:
        return choices[raw]
    if "scale" in field:
        value = storage_display_value(field, raw)
        return f"{value:.{int(field.get('decimals', 0))}f} {field.get('unit', '')}".rstrip()
    return str(raw)
