"""
ecu_config.py — BMW MS41 "Control Bits" configuration (enable/disable features).

The MS41 calibration has a set of control bytes (Byte 4..Byte 8) whose individual
bits enable/disable engine features.  Bit meanings are from the RomRaider MS41
"Control Bits" thread (mrf582's reference post) and cross-checked against real
dumps.

Control bytes live in the calibration region:
    full 256 KB ROM : 0x14004 .. 0x14008
    24 KB partial   : 0x00004 .. 0x00008
so this works on either, via a cal-region base offset.

Each feature is a masked field inside one byte.  Editing is read-modify-write of
only that field, preserving the other bits in the byte (several features share a
byte).  After editing, the calibration checksum (#1, which covers offsets 0..0x4E
including these bytes) must be recomputed — callers do that via checksum.correct_checksums.

Transmission selection updates Byte 5 bits 0..6 while preserving the independent
knock-detection setting in bit 7.

A/C type selection updates Byte 4 bits 1, 2, and 4 while preserving the
independent VANOS setting in bit 5 and every unrelated bit in the byte.
"""

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

CAL_BASE_FULL = 0x14000      # control bytes are at CAL_BASE + 4 .. +8 in a full ROM
FULL_ROM_SIZE = 256 * 1024
TUNE_SIZE     = 24 * 1024


@dataclass
class ConfigFeature:
    name:    str
    byte:    int                       # control byte number (4..8) in the cal region
    mask:    int                       # bits this feature occupies within the byte
    options: List[Tuple[str, int]]     # (label, masked value)
    note:    str = ""
    abs_addr: Optional[int] = None     # absolute file offset (full ROM only) instead of cal byte
    profile_options: Optional[Dict[str, List[Tuple[str, int]]]] = None
    full_profile_options: Optional[Dict[str, List[Tuple[str, int]]]] = None
    profile_abs_addrs: Optional[Dict[str, int]] = None
    program_variants: Optional[Dict[str, Tuple[str, ...]]] = None
    full_file_only: bool = False
    full_options_require_program_gate: bool = False

    def addr(self, cal_base: int, size: int, profile: Optional[str] = None,
             program_variant: Optional[str] = None) -> Optional[int]:
        """Resolve this feature's byte offset, or None if not present in this image."""
        if self.profile_abs_addrs is not None:
            if size != FULL_ROM_SIZE:
                return None
            if self.program_variants is not None:
                allowed = self.program_variants.get(profile, ())
                if program_variant not in allowed:
                    return None
            return self.profile_abs_addrs.get(profile)
        if self.abs_addr is not None:
            return self.abs_addr if size == FULL_ROM_SIZE else None
        return cal_base + self.byte

    def options_for(self, profile: Optional[str] = None,
                    target_size: Optional[int] = None,
                    program_gate_present: bool = False) -> List[Tuple[str, int]]:
        """Return this feature's choices for a CAL-ID control-bit profile.

        Most feature fields are shared by every MS41 generation and use ``options``.
        A profile-specific field returns no choices when the target CAL ID cannot be
        established. Some ID12/ID60 choices are full-ROM-only because they require a
        coordinated program-region edit.
        """
        if (target_size == FULL_ROM_SIZE and self.full_profile_options is not None
                and profile in self.full_profile_options
                and (not self.full_options_require_program_gate
                     or program_gate_present)):
            return self.full_profile_options[profile]
        if self.profile_options is None:
            return self.options
        return self.profile_options.get(profile, [])

    @property
    def is_profile_specific(self) -> bool:
        return self.profile_options is not None or self.full_profile_options is not None

    @property
    def is_program_feature(self) -> bool:
        return self.abs_addr is not None or self.profile_abs_addrs is not None

    def current(self, byte_val: int, profile: Optional[str] = None,
                target_size: Optional[int] = None,
                program_gate_present: bool = False) -> str:
        """Return the label matching the current byte value, or a 'Custom' string."""
        cur = byte_val & self.mask
        for label, val in self.options_for(
                profile, target_size, program_gate_present):
            if (val & self.mask) == cur:
                return label
        return f"Custom (0x{cur:02X})"

    def apply(self, byte_val: int, label: str, profile: Optional[str] = None,
              target_size: Optional[int] = None,
              program_gate_present: bool = False) -> int:
        """Return the new byte value with this feature set to the chosen option."""
        for lbl, val in self.options_for(
                profile, target_size, program_gate_present):
            if lbl == label:
                return (byte_val & ~self.mask) | (val & self.mask)
        return byte_val


# ── Feature table (Byte / mask / options) ─────────────────────────────────────
# Polarity note: most features are "delete when the bit is 0" → Enabled = bit set.
# EWS and Calibration CRC are inverted ("active when the bit is 1").
OXYGEN_SENSOR_PROFILE_OPTIONS = {
    "ID41": [
        ("Dual (2-channel)", 0x1C),
        ("Single (1-channel)", 0x18),
        ("Disabled", 0x04),
    ],
    "ID42": [
        ("Dual (2-channel)", 0x1C),
        ("Single (1-channel)", 0x18),
        ("Disabled", 0x04),
    ],
    "ID59": [
        ("Dual (2-channel)", 0x08),
        ("Single (1-channel)", 0x18),
        ("Disabled", 0x04),
    ],
    "ID85": [("Dual (2-channel)", 0x14), ("Single (1-channel)", 0x0C)],
    "ID60": [("Dual (2-channel)", 0x14), ("Single (1-channel)", 0x0C)],
    "ID12": [("Dual (2-channel)", 0x14), ("Single (1-channel)", 0x0C)],
}

OXYGEN_SENSOR_FULL_PROFILE_OPTIONS = {
    profile: options + [("Disabled (Experimental)", 0x04)]
    for profile, options in OXYGEN_SENSOR_PROFILE_OPTIONS.items()
    if profile in ("ID12", "ID60")
}

O2_FEEDBACK_PROGRAM_OPTIONS = [
    ("Feedback Enabled", 0x0C),
    ("Feedback Disabled", 0x11),
]

FEATURES: List[ConfigFeature] = [
    ConfigFeature("Oxygen Sensors", 6, 0x1C,
                  [],
                  "Byte 6 bits 2-4, interpreted by exact CAL ID. ID41/42: "
                  "Dual 0x1C, Single 0x18, Disabled 0x04. ID59: Dual 0x08, "
                  "Single 0x18, Disabled 0x04. ID12/60/85: Dual 0x14, "
                  "Single 0x0C. ID12/60 experimental disable is full-ROM-only "
                  "and also requires the program gate below.",
                  profile_options=OXYGEN_SENSOR_PROFILE_OPTIONS,
                  full_profile_options=OXYGEN_SENSOR_FULL_PROFILE_OPTIONS,
                  full_options_require_program_gate=True),
    ConfigFeature("VANOS", 4, 0x20,
                  [("Enabled", 0x20), ("Disabled", 0x00)],
                  "Byte 4 bit 5 (0 = VANOS delete)."),
    ConfigFeature("A/C Type", 4, 0x16,
                  [("E39", 0x10), ("E36", 0x06)],
                  "Byte 4 bits 1, 2, and 4 select the known-working chassis A/C "
                  "configuration. VANOS bit 5 is preserved."),
    ConfigFeature("Idle Air Control (IACV)", 8, 0x04,
                  [("Enabled", 0x04), ("Disabled", 0x00)],
                  "Byte 8 bit 2 (0 = IACV delete)."),
    ConfigFeature("Knock Detection", 5, 0x80,
                  [("Enabled", 0x80), ("Disabled", 0x00)],
                  "Byte 5 bit 7 (0 = knock sensor delete)."),
    ConfigFeature("ASC Ignition Retard", 7, 0x01,
                  [("Enabled", 0x01), ("Disabled", 0x00)],
                  "Byte 7 bit 0 (0 = ASC ignition retard delete)."),
    ConfigFeature("Muffler Solenoid Fault", 8, 0x08,
                  [("Enabled", 0x08), ("Disabled", 0x00)],
                  "Byte 8 bit 3 (0 = muffler flap solenoid delete)."),
    ConfigFeature("EVAP (Vapor) Faults", 8, 0x30,
                  [("Allowed", 0x10), ("Not Allowed", 0x20), ("Disabled", 0x00)],
                  "Byte 8 bits 4/5 (0x10 = allowed, 0x20 = not allowed, 0x00 = disabled)."),
    ConfigFeature("EVAP (Vapor) Purging", 7, 0x08,
                  [("Enabled", 0x08), ("Disabled", 0x00)],
                  "Byte 7 bit 3 / AKF (0 = purge/charcoal-canister delete)."),
    ConfigFeature("ORVR", 7, 0x80,
                  [("Enabled", 0x80), ("Disabled", 0x00)],
                  "Byte 7 bit 7 (0 = ORVR delete)."),
    ConfigFeature("EWS (Immobilizer)", 8, 0x80,
                  [("Enabled", 0x00), ("Disabled", 0x80)],
                  "Byte 8 bit 7 (1 = EWS delete) — inverted polarity."),
    ConfigFeature("Transmission", 5, 0x7F,
                  [("AT/MT (auto)", 0x6C), ("MT Only", 0x40), ("AT Only", 0x6B)],
                  "Byte 5 bits 0-6 select the transmission mode. Bit 7 (Knock) is preserved."),
    ConfigFeature("Calibration CRC Check", 7, 0x10,
                  [("Enabled", 0x00), ("Disabled", 0x10)],
                  "Byte 7 bit 4 (1 = disable CRC check for the 24 KB calibration "
                  "section)."),
    ConfigFeature("Program CRC Check", 0, 0xFF,
                  [("Enabled", 0x30), ("Disabled", 0xFF)],
                  "Switch at 0x605C (program/full-ROM checksum). Full ROM only.",
                  abs_addr=0x605C),
    ConfigFeature("O2 Feedback Program Gate (Experimental)", 0, 0xFF,
                  [],
                  "Full-ROM-only companion to Oxygen Sensors = Disabled "
                  "(Experimental). Changes the unknown-mode branch displacement "
                  "from 0x0C to 0x11 so Byte 6 value 0x04 skips O2 setup instead "
                  "of falling back to two channels. ID12/MS41.2-.3: 0x2DF95; "
                  "ID60/MS41.1: 0x2E311.",
                  profile_options={
                      "ID12": O2_FEEDBACK_PROGRAM_OPTIONS,
                      "ID60": O2_FEEDBACK_PROGRAM_OPTIONS,
                  },
                  profile_abs_addrs={"ID12": 0x2DF95, "ID60": 0x2E311},
                  program_variants={
                      "ID12": ("MS41.2", "MS41.3"),
                      "ID60": ("MS41.1",),
                  },
                  full_file_only=True),
]


def _cal_base(data) -> Optional[int]:
    n = len(data)
    if n == FULL_ROM_SIZE:
        return CAL_BASE_FULL
    if n == TUNE_SIZE:
        return 0
    return None


_AUTO_PROFILE = object()

CONTROL_BIT_PROFILES = frozenset(OXYGEN_SENSOR_PROFILE_OPTIONS)


def control_bit_profile_from_calid(cal_id: Optional[str]) -> Optional[str]:
    """Return the exact CAL-ID family used by the Byte 6 switch definition."""
    if not cal_id:
        return None
    prefix = str(cal_id).strip()[:2]
    profile = f"ID{prefix}"
    return profile if profile in CONTROL_BIT_PROFILES else None


def detect_control_bit_profile(data) -> Optional[str]:
    """Return the CAL-ID family that defines the image's control-bit switches.

    Broad MS41.0/.1/.2/.3 detection is intentionally not used here: ID41, ID42,
    ID59, and ID85 are all MS41.0 but have different Byte 6 state values.
    """
    from ms41 import MS41ECU

    if len(data) not in (FULL_ROM_SIZE, TUNE_SIZE):
        return None
    return control_bit_profile_from_calid(MS41ECU.read_calid(data))


def _resolved_profile(data, profile):
    if profile is _AUTO_PROFILE:
        return detect_control_bit_profile(data)
    return profile if profile in CONTROL_BIT_PROFILES else None


def _program_variant(data) -> Optional[str]:
    if len(data) != FULL_ROM_SIZE:
        return None
    from ms41 import MS41ECU
    return MS41ECU.detect_program_variant(data)


def experimental_o2_program_gate_present(
        data, profile=_AUTO_PROFILE,
        program_variant: Optional[str] = None) -> bool:
    """Return whether this full ROM contains the compatible O2 disable gate."""
    if len(data) != FULL_ROM_SIZE:
        return False
    profile = _resolved_profile(data, profile)
    if program_variant is None:
        program_variant = _program_variant(data)
    feature = next(
        f for f in FEATURES
        if f.name == "O2 Feedback Program Gate (Experimental)"
    )
    addr = feature.addr(0, len(data), profile, program_variant)
    return addr is not None and addr < len(data) and data[addr] == 0x11


def read_config(data, profile=_AUTO_PROFILE) -> Optional[Dict[str, str]]:
    """
    Return {feature_name: current_state_label} for a full ROM or 24 KB partial.
    Features that don't exist in this image (e.g. Program CRC on a partial) are
    omitted from the dict. Profile-specific features are also omitted when the
    running/target firmware profile is unknown.
    """
    base = _cal_base(data)
    if base is None:
        return None
    size = len(data)
    profile = _resolved_profile(data, profile)
    program_variant = _program_variant(data)
    program_gate_present = experimental_o2_program_gate_present(
        data, profile, program_variant)
    out = {}
    for f in FEATURES:
        a = f.addr(base, size, profile, program_variant)
        if a is None or a >= len(data):
            continue
        if f.is_profile_specific and not f.options_for(
                profile, size, program_gate_present):
            continue
        out[f.name] = f.current(
            data[a], profile, size, program_gate_present)
    return out


def apply_config(data, changes: Dict[str, str], profile=_AUTO_PROFILE) -> Tuple[bytearray, List[str]]:
    """
    Apply {feature_name: chosen_label} to a copy of the image.
    Returns (new_image, change_log).  Only the targeted bits are modified. A
    profile-specific feature is left untouched when its target firmware is unknown
    or the selected semantic state has no equivalent in that profile.
    """
    out = bytearray(data)
    base = _cal_base(out)
    log: List[str] = []
    if base is None:
        return out, ["Not a 256 KB ROM or 24 KB partial — cannot edit control bits."]
    size = len(out)
    profile = _resolved_profile(out, profile)
    program_variant = _program_variant(out)
    gate_choice = changes.get("O2 Feedback Program Gate (Experimental)")
    gate_feature = next(
        f for f in FEATURES
        if f.name == "O2 Feedback Program Gate (Experimental)"
    )
    gate_supported = gate_feature.addr(
        base, size, profile, program_variant) is not None
    if gate_choice == "Feedback Disabled":
        program_gate_present = gate_supported
    elif gate_choice == "Feedback Enabled":
        program_gate_present = False
    else:
        program_gate_present = experimental_o2_program_gate_present(
            out, profile, program_variant)
    by_name = {f.name: f for f in FEATURES}
    for name, label in changes.items():
        f = by_name.get(name)
        if f is None:
            continue
        addr = f.addr(base, size, profile, program_variant)
        if addr is None or addr >= len(out):
            continue
        old = out[addr]
        new = f.apply(old, label, profile, size, program_gate_present)
        if new != old:
            out[addr] = new
            where = f"0x{addr:05X}" if f.is_program_feature else f"Byte {f.byte}"
            log.append(f"{name}: {where} 0x{old:02X} → 0x{new:02X}  ({label})")
    if not log:
        log.append("No changes.")
    return out, log
