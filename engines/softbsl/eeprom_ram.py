"""MS41 24C04 inspection plus guarded full-image RAM-agent operations."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
import uuid
from dataclasses import asdict, dataclass, field as dataclass_field
from pathlib import Path

import ds2
import ecu_info
import softbsl_service
from ds2_fast_safety import OperationJournal
from engines.softbsl import checksum
from engines.softbsl import softbsl_host as _sb


EEPROM_SIZE = 0x200
TAIL_START = 0x1DD
TRANSMISSION_OFFSET = 0x1CA
SOFTBSL_DOOR_PATCHES = {
    "MS41.0": "door_magic_ms410",
    "MS41.1": "door_magic_ms411",
    "MS41.2": "door_magic",
    "MS41.3": "door_magic",
}
EEPROM_AGENT_VERSION = 3
CAP_FULL_READ = 0x01
CAP_GENERIC_WRITE = 0x02
# Compatibility name retained for older callers; v3's bit is generic byte write.
CAP_TRANSMISSION_WRITE = CAP_GENERIC_WRITE
CAP_SELF_CONTAINED_I2C = 0x04
CAP_SAFE_FINALIZER = 0x08
REQUIRED_READ_CAPS = (
    CAP_FULL_READ | CAP_SELF_CONTAINED_I2C | CAP_SAFE_FINALIZER
)


class EepromError(RuntimeError):
    """EEPROM operation failed without widening the write boundary."""


class EepromCommitUnknown(EepromError):
    """A byte-write reply was lost; the same live RAM session is retained."""

    def __init__(self, recovery: "EepromWriteRecovery", cause: Exception):
        self.recovery = recovery
        super().__init__(
            f"{cause}. EEPROM WRITE STATE IS UNKNOWN; keep ignition on and "
            "query this retained session. Do not reopen the port."
        )


class EepromWriteRecoveryRequired(EepromError):
    """A byte-write sequence began but terminal verification did not complete."""

    def __init__(self, recovery: "EepromWriteRecovery", message: str):
        self.recovery = recovery
        super().__init__(
            f"{message}. Keep ignition on; the EEPROM RAM-agent session remains open."
        )


class EepromCancelled(EepromError):
    """The prepared operation was cancelled before any byte write."""


class EepromResetRequired(EepromError):
    """EEPROM is verified, but the ECU did not confirm its normal-mode reset."""


class EepromAuditError(EepromError):
    """The ECU is safe and disconnected, but local audit finalization failed."""


@dataclass(frozen=True)
class ByteWrite:
    offset: int
    expected: int
    replacement: int
    reason: str = "data"


@dataclass(frozen=True)
class EepromField:
    offset: int
    length: int
    checked: bool
    key: str
    label: str
    category: str


FIELDS_MS412 = (
    EepromField(0x000, 6, False, "cycle_sequence",
                "Operating-cycle counter (three-copy sequence)", "history"),
    EepromField(0x006, 4, False, "save_count",
                "EEPROM save counter", "history"),
    EepromField(0x00A, 4, True, "identity_gate",
                "Adaptation compatibility key", "system"),
    EepromField(0x00E, 68, True, "knock_adaptation",
                "Learned knock corrections (64 cells and overall correction)",
                "adaptation"),
    EepromField(0x052, 4, True, "load_model_correction",
                "Learned load-model correction (units unknown)", "adaptation"),
    EepromField(0x056, 6, True, "vanos_adaptation",
                "VANOS learned reference and controller state", "adaptation"),
    EepromField(0x05C, 4, True, "tps_adaptation",
                "Throttle-position adaptation", "adaptation"),
    EepromField(0x060, 8, True, "engine_roughness_segment_adaptation",
                "Learned crankshaft segment corrections (misfire detection)",
                "adaptation"),
    EepromField(0x068, 40, True, "fuel_adaptations",
                "Idle and long-term fuel trims plus internal saved values",
                "adaptation"),
    EepromField(0x090, 138, True, "dtc_occurrence",
                "Fault occurrence history and condition snapshots", "diagnostic"),
    EepromField(0x11A, 12, True, "idle_regulator_adaptation",
                "Idle-control learned factor and integrator values",
                "adaptation"),
    EepromField(0x126, 32, True, "rough_running",
                "Learned per-cylinder rough-running corrections", "adaptation"),
    EepromField(0x146, 110, True, "fault_memory",
                "Stored faults and freeze-frame data", "diagnostic"),
    EepromField(0x1B4, 4, True, "coolant_latch",
                "Coolant-temperature related saved state", "service"),
    EepromField(0x1B8, 10, True, "diagnostic_counters",
                "Diagnostic event counters", "diagnostic"),
    EepromField(0x1C2, 4, False, "output_test_nonce",
                "Actuator-test sequence", "service"),
    EepromField(0x1C6, 4, True, "load_collective",
                "Saved engine-load model value", "adaptation"),
    EepromField(0x1CA, 4, True, "transmission",
                "Transmission selection and other coding bits", "coding"),
    EepromField(0x1CE, 4, True, "shutdown_coolant",
                "Coolant temperature at shutdown and warm-restart count", "history"),
    EepromField(0x1D2, 4, False, "peak_rpm",
                "Peak engine speed and cycle marker", "history"),
    EepromField(0x1D6, 4, False, "overrev_history",
                "Over-rev history", "history"),
)

FIELDS_MS411 = (
    EepromField(0x000, 6, False, "cycle_sequence",
                "Operating-cycle counter (three-copy sequence)", "history"),
    EepromField(0x006, 4, False, "save_count",
                "EEPROM save counter", "history"),
    EepromField(0x00A, 4, True, "identity_gate",
                "Adaptation compatibility key", "system"),
    EepromField(0x00E, 36, True, "knock_adaptation",
                "Learned knock corrections (32 cells and overall correction)",
                "adaptation"),
    EepromField(0x032, 4, True, "load_model_correction",
                "Learned load-model correction (units unknown)", "adaptation"),
    EepromField(0x036, 6, True, "vanos_adaptation",
                "VANOS learned reference and controller state", "adaptation"),
    EepromField(0x03C, 4, True, "tps_adaptation",
                "Throttle-position adaptation", "adaptation"),
    EepromField(0x040, 8, True, "engine_roughness_segment_adaptation",
                "Learned crankshaft segment corrections (misfire detection)",
                "adaptation"),
    EepromField(0x048, 32, True, "fuel_adaptations",
                "Idle and long-term fuel trims plus internal saved values",
                "adaptation"),
    EepromField(0x068, 128, True, "dtc_occurrence",
                "Fault occurrence history and condition snapshots", "diagnostic"),
    EepromField(0x0E8, 12, True, "idle_regulator_adaptation",
                "Idle-control learned factor and integrator values",
                "adaptation"),
    EepromField(0x0F4, 32, True, "rough_running",
                "Learned per-cylinder rough-running corrections", "adaptation"),
    EepromField(0x114, 162, True, "fault_memory",
                "Stored faults and freeze-frame data", "diagnostic"),
    EepromField(0x1B6, 4, True, "coolant_latch",
                "Coolant-temperature related saved state", "service"),
    EepromField(0x1BA, 10, True, "diagnostic_counters",
                "Diagnostic event counters", "diagnostic"),
    EepromField(0x1C4, 4, False, "output_test_nonce",
                "Actuator-test sequence", "service"),
    EepromField(0x1C8, 4, True, "load_collective",
                "Saved engine-load model value", "adaptation"),
    EepromField(0x1CC, 4, True, "transmission",
                "Transmission selection and other coding bits", "coding"),
    EepromField(0x1D0, 4, True, "shutdown_coolant",
                "Coolant temperature at shutdown and warm-restart count", "history"),
    EepromField(0x1D4, 8, False, "unresolved_tail_mirror",
                "Unidentified EEPROM data", "unknown"),
)

FIELDS_MS410 = (
    EepromField(0x000, 6, False, "cycle_sequence",
                "Operating-cycle counter (three-copy sequence)", "history"),
    EepromField(0x006, 4, False, "save_count",
                "EEPROM save counter", "history"),
    EepromField(0x00A, 4, True, "identity_gate",
                "Adaptation compatibility key", "system"),
    EepromField(0x00E, 68, True, "knock_adaptation",
                "Learned knock corrections (64 cells and overall correction)",
                "adaptation"),
    EepromField(0x052, 4, True, "load_model_correction",
                "Learned load-model correction (units unknown)", "adaptation"),
    EepromField(0x056, 6, True, "vanos_adaptation",
                "VANOS learned reference and controller state", "adaptation"),
    EepromField(0x05C, 4, True, "tps_adaptation",
                "Throttle-position adaptation", "adaptation"),
    EepromField(0x060, 8, True, "engine_roughness_segment_adaptation",
                "Learned crankshaft segment corrections (misfire detection)",
                "adaptation"),
    EepromField(0x068, 12, True, "fuel_adaptations",
                "Idle and long-term fuel trims", "adaptation"),
    EepromField(0x074, 118, True, "dtc_occurrence",
                "Fault occurrence history and condition snapshots", "diagnostic"),
    EepromField(0x0EA, 12, True, "idle_regulator_adaptation",
                "Idle-control learned factor and integrator values",
                "adaptation"),
    EepromField(0x0F6, 138, False, "unresolved_bulk_0f6",
                "Unidentified EEPROM data", "unknown"),
    EepromField(0x180, 4, True, "coolant_latch",
                "Coolant-temperature related saved state", "service"),
    EepromField(0x184, 10, False, "unresolved_bulk_184",
                "Unidentified EEPROM data", "unknown"),
    EepromField(0x18E, 4, False, "output_test_nonce",
                "Actuator-test sequence", "service"),
    EepromField(0x192, 4, False, "unresolved_bulk_192",
                "Unidentified EEPROM data", "unknown"),
    EepromField(0x196, 4, True, "transmission",
                "Transmission selection and other coding bits", "coding"),
    EepromField(0x19A, 12, False, "unresolved_bulk_19a",
                "Unidentified EEPROM data", "unknown"),
)

FIELDS_BY_VARIANT = {
    "MS41.0": FIELDS_MS410,
    "MS41.1": FIELDS_MS411,
    "MS41.2": FIELDS_MS412,
    "MS41.3": FIELDS_MS412,
}

DECODE_LAYOUTS = {
    "MS41.0": {
        "mirror_size": 0x1A6,
        "knock_cells": 64,
        "tps": 0x05C,
        "trims": (0x06A, 0x06C, 0x06E, 0x070),
        "dtc_occurrence": 0x074,
        "diagnostic_counters": None,
        "transmission": 0x196,
        "shutdown_coolant": None,
        "peak_rpm": None,
        "overrev": None,
    },
    "MS41.1": {
        "mirror_size": 0x1DC,
        "knock_cells": 32,
        "tps": 0x03C,
        "trims": (0x04A, 0x04C, 0x04E, 0x050),
        "dtc_occurrence": 0x068,
        "diagnostic_counters": 0x1BA,
        "transmission": 0x1CC,
        "shutdown_coolant": 0x1D0,
        "peak_rpm": None,
        "overrev": None,
    },
    "MS41.2": {
        "mirror_size": 0x1DA,
        "knock_cells": 64,
        "tps": 0x05C,
        "trims": (0x06A, 0x06C, 0x06E, 0x070),
        "dtc_occurrence": 0x090,
        "diagnostic_counters": 0x1B8,
        "transmission": 0x1CA,
        "shutdown_coolant": 0x1CE,
        "peak_rpm": 0x1D2,
        "overrev": 0x1D6,
    },
}
DECODE_LAYOUTS["MS41.3"] = DECODE_LAYOUTS["MS41.2"]

_LAYOUT_CANDIDATES = {
    "MS41.0": ("MS41.0",),
    "MS41.1": ("MS41.1",),
    "MS41.2": ("MS41.2", "MS41.3"),
}
_LAYOUT_BY_DESCRIPTOR = {
    b"111006064101": "MS41.0",
    b"111009096000": "MS41.1",
    b"111009091202": "MS41.2",
}
_LAYOUT_BY_DME_PART = {
    b"1429861": "MS41.0",
    b"1437806": "MS41.1",
    b"1406464": "MS41.2",
}

# Backward-compatible default for the original MS41.3 inspector and writer.
FIELDS = FIELDS_MS412


@dataclass(frozen=True)
class Preflight:
    port: str
    entry_marker: int
    calibration_selector: int | None
    program_variant: str
    softbsl_bank: str
    door_patch: str

    @property
    def eeprom_transmission_active(self) -> bool:
        return (
            self.calibration_selector is not None
            and (self.calibration_selector & 0x3F) == 0x2C
        )


@dataclass(frozen=True)
class Capture:
    image: bytes
    preflight: Preflight
    write_performed: bool = False


@dataclass
class EepromWriteRecovery:
    ds2: object
    protocol: "EepromProtocol"
    before: bytes
    target: bytes
    variant: str
    plan: tuple[ByteWrite, ...]
    after_path: Path
    journal: OperationJournal
    admission: Preflight | None = None
    audit_errors: list[str] = dataclass_field(default_factory=list)

    @property
    def is_open(self) -> bool:
        return bool(getattr(self.ds2, "is_open", False))

    def query(self) -> bytes:
        return self.protocol.stable_dump()

    def close_after_confirmed_power_cycle(self) -> None:
        """Release the dead session and durably terminate its audit journal."""
        current_errors = []
        if getattr(self.ds2, "is_open", True):
            try:
                self.ds2.close()
            except (Exception, KeyboardInterrupt) as error:
                current_errors.append(
                    f"interface close: {type(error).__name__}: {error}"
                )
        try:
            if not self.journal.closed:
                self.journal.finish(
                    "power_cycle_required",
                    resolution="confirmed_power_cycle",
                    audit_errors=self.audit_errors + current_errors,
                )
        except (Exception, KeyboardInterrupt) as error:
            current_errors.append(
                f"journal finalization: {type(error).__name__}: {error}"
            )
        self.audit_errors.extend(current_errors)
        if current_errors:
            raise EepromAuditError(
                "Power cycle was confirmed, but EEPROM recovery cleanup/audit "
                f"failed: {'; '.join(current_errors)}"
            )


def additive_check(payload: bytes) -> int:
    """Firmware routine CPU 0x0259B2: byte sum plus one, modulo 16 bits."""
    return (sum(payload) + 1) & 0xFFFF


def _u16(data: bytes, offset: int = 0) -> int:
    return int.from_bytes(data[offset:offset + 2], "little")


def validate_image(image: bytes) -> bytes:
    image = bytes(image)
    if len(image) != EEPROM_SIZE:
        raise ValueError(
            f"physical EEPROM image must be exactly 512 bytes, got {len(image)}")
    return image


def validate_physical_capture(image: bytes) -> bytes:
    """Reject stable stuck-bus/fill reads without blocking offline inspection."""
    image = validate_image(image)
    if len(set(image)) == 1:
        raise EepromError(
            f"physical EEPROM read returned 512 bytes of 0x{image[0]:02X}; "
            "recheck chip power, ground, clip contact/orientation, SDA/SCL, "
            "and in-circuit isolation before retrying"
        )
    unrotated = image[32:] + image[:32]
    if not detect_layouts(image) and detect_layouts(unrotated):
        raise EepromError(
            "physical EEPROM read is rotated by one 32-byte CH341A packet; "
            "discard it and reopen the programmer before retrying"
        )
    return image


def validate_write_image(image: bytes, variant: str) -> bytes:
    """Require a physical-looking full image, never a zero-padded RAM mirror."""
    image = validate_physical_capture(image)
    fields_for_variant(variant)
    mirror_size = DECODE_LAYOUTS[variant]["mirror_size"]
    if image[mirror_size:] == bytes(EEPROM_SIZE - mirror_size):
        raise EepromError(
            f"{variant} image is zero-filled from 0x{mirror_size:03X}; "
            "it looks like a padded RAM mirror, not a restorable 24C04 image")
    return image


def fields_for_variant(variant: str) -> tuple[EepromField, ...]:
    try:
        return FIELDS_BY_VARIANT[variant]
    except KeyError as error:
        raise ValueError(f"unsupported EEPROM layout {variant!r}") from error


def detect_layouts(image: bytes) -> tuple[str, ...]:
    """Return exact tail-supported layouts; MS41.2 and MS41.3 are identical."""
    image = validate_image(image)
    first_part = image[0x1EF:0x1F6]
    second_part = image[0x1F6:0x1FD]
    if first_part != second_part:
        return ()
    votes = tuple(
        layout
        for layout in (
            _LAYOUT_BY_DESCRIPTOR.get(image[0x1E3:0x1EF]),
            _LAYOUT_BY_DME_PART.get(first_part),
        )
        if layout is not None
    )
    if not votes or len(set(votes)) != 1:
        return ()
    return _LAYOUT_CANDIDATES[votes[0]]


def field_report(image: bytes, variant: str = "MS41.3") -> list[dict]:
    image = validate_image(image)
    report = []
    for field in fields_for_variant(variant):
        raw = image[field.offset:field.offset + field.length]
        row = asdict(field) | {"raw": raw.hex(" ")}
        if field.checked:
            row["stored_check"] = _u16(raw, field.length - 2)
            row["computed_check"] = additive_check(raw[:-2])
            row["check_ok"] = row["stored_check"] == row["computed_check"]
        report.append(row)
    return report


def _transmission_record_at(image: bytes, offset: int) -> dict:
    raw = image[offset:offset + 4]
    value = _u16(raw)
    stored = _u16(raw, 2)
    low2 = value & 3
    return {
        "raw": raw.hex(" "),
        "value": value,
        "stored_check": stored,
        "computed_check": additive_check(raw[:2]),
        "check_ok": stored == additive_check(raw[:2]),
        "mode_bits": low2,
        "mode": {1: "automatic", 2: "manual"}.get(low2, "invalid/unchanged"),
        "preserved_bits": value & 0xFFFC,
    }


def transmission_offset(variant: str = "MS41.3") -> int:
    fields_for_variant(variant)
    return DECODE_LAYOUTS[variant]["transmission"]


def transmission_record(image: bytes, variant: str = "MS41.3") -> dict:
    return _transmission_record_at(
        validate_image(image), transmission_offset(variant))


def make_transmission_record(
    image: bytes, mode: str, variant: str = "MS41.3"
) -> bytes:
    """Masked RMW of only bits 0..1; every unrelated bit is preserved."""
    mode_bits = {"at": 1, "mt": 2}.get(mode.lower())
    if mode_bits is None:
        raise ValueError("transmission mode must be 'at' or 'mt'")
    current = transmission_record(image, variant)
    if not current["check_ok"]:
        raise EepromError(
            f"0x{transmission_offset(variant):03X} currently has an invalid check word")
    value = (current["value"] & 0xFFFC) | mode_bits
    payload = value.to_bytes(2, "little")
    return payload + additive_check(payload).to_bytes(2, "little")


def set_transmission_mode(
    image: bytes, mode: str, variant: str = "MS41.3"
) -> bytes:
    """Return a 512-byte image changing only the version-specific mode bits/check."""
    image = bytearray(validate_image(image))
    offset = transmission_offset(variant)
    image[offset:offset + 4] = make_transmission_record(image, mode, variant)
    return bytes(image)


def changed_offsets(before: bytes, target: bytes) -> tuple[int, ...]:
    before, target = validate_image(before), validate_image(target)
    return tuple(
        offset
        for offset, (old, new) in enumerate(zip(before, target))
        if old != new
    )


def update_checks_for_changed_records(
    before: bytes, target: bytes, variant: str
) -> bytes:
    """Update checks only where a known checked record's payload was edited."""
    before, target = validate_image(before), bytearray(validate_image(target))
    for field in fields_for_variant(variant):
        if not field.checked:
            continue
        start = field.offset
        payload_end = start + field.length - 2
        end = start + field.length
        if before[start:payload_end] != target[start:payload_end]:
            target[payload_end:end] = additive_check(
                target[start:payload_end]).to_bytes(2, "little")
    return bytes(target)


def build_write_plan(
    before: bytes, target: bytes, variant: str
) -> tuple[ByteWrite, ...]:
    """Build replay-safe byte writes with checked-record check bytes written last.

    Existing invalid records may remain untouched. A changed checked record must
    itself be valid in the target; unrelated invalid records are never "fixed".
    """
    before, target = validate_image(before), validate_image(target)
    fields = fields_for_variant(variant)
    plan: list[ByteWrite] = []
    covered: set[int] = set()

    for field in fields:
        start, end = field.offset, field.offset + field.length
        if before[start:end] == target[start:end]:
            continue
        covered.update(range(start, end))
        if not field.checked:
            for offset in range(start, end):
                if before[offset] != target[offset]:
                    plan.append(ByteWrite(
                        offset, before[offset], target[offset], "data"))
            continue

        payload_end = end - 2
        target_raw = target[start:end]
        stored = int.from_bytes(target_raw[-2:], "little")
        computed = additive_check(target_raw[:-2])
        if stored != computed:
            raise EepromError(
                f"changed checked record 0x{start:03X} has an invalid target check")

        state = bytearray(before[start:end])
        if before[start:payload_end] != target[start:payload_end]:
            check_low = field.length - 2
            invalid = (target_raw[check_low] + 1) & 0xFF
            if invalid == state[check_low]:
                invalid = (invalid + 1) & 0xFF
            plan.append(ByteWrite(
                start + check_low, state[check_low], invalid, "invalidate-check"))
            state[check_low] = invalid

        for relative in range(field.length - 2):
            if state[relative] != target_raw[relative]:
                plan.append(ByteWrite(
                    start + relative,
                    state[relative],
                    target_raw[relative],
                    "payload",
                ))
                state[relative] = target_raw[relative]

        # High byte first; low byte is the final validity transition.
        for relative in (field.length - 1, field.length - 2):
            if state[relative] != target_raw[relative]:
                plan.append(ByteWrite(
                    start + relative,
                    state[relative],
                    target_raw[relative],
                    "check-last",
                ))
                state[relative] = target_raw[relative]

    for offset in range(EEPROM_SIZE):
        if offset not in covered and before[offset] != target[offset]:
            plan.append(ByteWrite(offset, before[offset], target[offset], "raw"))
    return tuple(plan)


def decoded_values(image: bytes, variant: str = "MS41.3") -> dict:
    """Small, evidence-backed view; unresolved bytes stay in the raw field report."""
    image = validate_image(image)
    fields_for_variant(variant)
    layout = DECODE_LAYOUTS[variant]
    knock = image[0x00E:0x00E + layout["knock_cells"] + 1]
    trims = [_u16(image, off) for off in layout["trims"]]
    tail = image[TAIL_START:]
    diagnostic_counters = layout["diagnostic_counters"]
    shutdown_coolant = layout["shutdown_coolant"]
    peak_rpm = layout["peak_rpm"]
    overrev = layout["overrev"]
    return {
        "looks_like_zero_padded_ram_mirror": image[layout["mirror_size"]:] == bytes(
            EEPROM_SIZE - layout["mirror_size"]),
        "cycle_sequence_base": _u16(image, 0x000),
        "eeprom_save_count": int.from_bytes(image[0x006:0x00A], "little"),
        "knock_cells_neutral": all(
            value == 0x80 for value in knock[:layout["knock_cells"]]
        ),
        "knock_global_raw": knock[layout["knock_cells"]],
        "tps_adaptation_raw": _u16(image, layout["tps"]),
        "tps_adaptation_percent_if_logger_scale": (
            _u16(image, layout["tps"]) * 0.001526
        ),
        "idle_trim_1_us": (trims[0] - 32768) * 5.34,
        "ltft_1_percent": (trims[1] - 32768) * 100 / 65535,
        "idle_trim_2_us": (trims[2] - 32768) * 5.34,
        "ltft_2_percent": (trims[3] - 32768) * 100 / 65535,
        "dtc_occurrence_count": image[layout["dtc_occurrence"]],
        "diagnostic_event_counters": (
            list(image[diagnostic_counters:diagnostic_counters + 7])
            if diagnostic_counters is not None else None
        ),
        "transmission": _transmission_record_at(image, layout["transmission"]),
        "last_shutdown_ect_raw": (
            image[shutdown_coolant] if shutdown_coolant is not None else None
        ),
        "last_shutdown_ect_celsius": (
            image[shutdown_coolant] * 0.747 - 48
            if shutdown_coolant is not None else None
        ),
        "warm_restart_count": (
            image[shutdown_coolant + 1] if shutdown_coolant is not None else None
        ),
        "peak_rpm": image[peak_rpm] * 32 if peak_rpm is not None else None,
        "overrev_event_count": image[overrev] if overrev is not None else None,
        "tail_progression": list(tail[:3]),
        "tail_descriptor": tail[6:18].decode("ascii", "replace"),
        "tail_dme_part_numbers": [
            tail[18:25].decode("ascii", "replace"),
            tail[25:32].decode("ascii", "replace"),
        ],
    }


def inspect_image(image: bytes, variant: str = "MS41.3") -> dict:
    return {
        "variant": variant,
        "size": len(image),
        "lower_mirror_size": DECODE_LAYOUTS[variant]["mirror_size"],
        "sha256": hashlib.sha256(image).hexdigest(),
        "decoded": decoded_values(image, variant),
        "fields": field_report(image, variant),
        "unmapped": {
            "0x1DA-0x1DC": image[0x1DA:0x1DD].hex(" "),
            "0x1DD-0x1FF": image[0x1DD:0x200].hex(" "),
        },
    }


def save_capture(path: str | os.PathLike, image: bytes) -> Path:
    """Durably save one exact capture and refuse accidental overwrite."""
    image = validate_image(image)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("xb") as stream:
        stream.write(image)
        stream.flush()
        os.fsync(stream.fileno())
    return target


def _save_capture_atomic(path: str | os.PathLike, image: bytes) -> Path:
    """Publish a fully synced capture atomically without replacing a file."""
    target = Path(path)
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    try:
        save_capture(temporary, image)
        try:
            os.link(temporary, target)
        except PermissionError:
            # ponytail: Android app storage forbids hard links; operation IDs make
            # same-path writers unique, so a checked same-directory rename is enough.
            if target.exists():
                raise FileExistsError(
                    f"refusing to overwrite EEPROM capture {target}"
                )
            os.rename(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def _after_capture_path(backup_path: str | os.PathLike) -> Path:
    backup = Path(backup_path)
    suffix = backup.suffix or ".bin"
    return backup.with_name(f"{backup.stem}.after{suffix}")


def _finish_closed_journal(
    journal: OperationJournal,
    returned_to_normal: bool,
    *,
    outcome: str,
    reset_message: str,
    **fields,
) -> None:
    if not returned_to_normal:
        try:
            if not journal.closed:
                journal.finish(
                    "power_cycle_required",
                    intended_outcome=outcome,
                    **fields,
                )
        except (Exception, KeyboardInterrupt) as error:
            raise EepromResetRequired(
                f"{reset_message}; journal finalization also failed: {error}"
            ) from error
        raise EepromResetRequired(reset_message)
    try:
        if not journal.closed:
            journal.finish(outcome, **fields)
    except (Exception, KeyboardInterrupt) as error:
        raise EepromAuditError(
            f"ECU returned to normal mode and the port is closed, but "
            f"journal finalization failed: {error}"
        ) from error


def _quit_and_close(protocol: EepromProtocol, interface) -> bool:
    try:
        returned_to_normal = protocol.quit_to_normal()
    except (Exception, KeyboardInterrupt):
        returned_to_normal = False
    try:
        interface.close()
    except (Exception, KeyboardInterrupt):
        returned_to_normal = False
    return returned_to_normal


def _recovery_journal_append(
    recovery: EepromWriteRecovery, event: str, **fields
) -> None:
    try:
        recovery.journal.append(event, **fields)
    except (Exception, KeyboardInterrupt) as error:
        recovery.audit_errors.append(
            f"{event}: {type(error).__name__}: {error}")


def _allowed_recovery_values(
    recovery: EepromWriteRecovery,
) -> dict[int, set[int]]:
    allowed: dict[int, set[int]] = {}
    for operation in recovery.plan:
        allowed.setdefault(operation.offset, set()).update(
            (operation.expected, operation.replacement))
    return allowed


def _validate_recovery_image(
    recovery: EepromWriteRecovery, image: bytes
) -> bytes:
    image = validate_physical_capture(image)
    allowed = _allowed_recovery_values(recovery)
    unexpected = [
        offset
        for offset, value in enumerate(image)
        if value != recovery.before[offset]
        and value not in allowed.get(offset, ())
    ]
    if unexpected:
        rendered = ", ".join(f"0x{offset:03X}" for offset in unexpected[:12])
        raise EepromError(
            f"recovery readback contains unexpected changes ({rendered})")
    return image


def load_eeprom_agent() -> bytes:
    root = Path(__file__).resolve().parent
    payload = _sb.load_agent(str(root / "eeprom_agent.hex"))
    record = json.loads(
        (root / "agent_manifest.json").read_text(encoding="utf-8")
    )["agents"]["eeprom_ms41"]
    if (
        len(payload) != record["payload_size"]
        or hashlib.sha256(payload).hexdigest() != record["payload_sha256"]
    ):
        raise EepromError("EEPROM agent failed its integrity check")
    return payload


class EepromProtocol:
    """Wire owner for the lowercase EEPROM-only RAM-agent commands."""

    def __init__(self, softbsl):
        self.sb = softbsl

    def identify(self) -> dict:
        self.sb._tx(ord("i"))
        reply = self.sb.ds2._read_exact(3, 2.0)
        if len(reply) != 3:
            raise EepromError("short EEPROM-agent identify reply")
        version, capabilities, entry_marker = reply
        if (
            version != EEPROM_AGENT_VERSION
            or capabilities & REQUIRED_READ_CAPS != REQUIRED_READ_CAPS
        ):
            raise EepromError(
                f"unsupported EEPROM agent v{version}, caps=0x{capabilities:02X}")
        if entry_marker not in (0, 1, 3):
            raise EepromError(
                f"unsafe EEPROM-agent entry marker E740=0x{entry_marker:02X}")
        return {
            "version": version,
            "capabilities": capabilities,
            "entry_marker": entry_marker,
        }

    def dump_once(self) -> bytes:
        self.sb._tx(ord("d"))
        status = self.sb._rx(timeout=8.0)
        if status != 1:
            raise EepromError(
                "agent's two physical reads disagreed"
                if status == 2 else f"EEPROM dump failed with status {status}")
        payload = self.sb.ds2._read_exact(
            EEPROM_SIZE + 2,
            3.0 + (EEPROM_SIZE + 2) * 12 / max(self.sb.ds2.baud, 1),
        )
        if len(payload) != EEPROM_SIZE + 2:
            raise EepromError(
                f"short EEPROM dump reply ({len(payload)}/{EEPROM_SIZE + 2})")
        image, received = payload[:-2], int.from_bytes(payload[-2:], "big")
        computed = checksum._crc(bytes((status,)) + image, 0xFFFF)
        if received != computed:
            raise EepromError(
                f"EEPROM dump CRC mismatch ({received:04X}!={computed:04X})")
        return image

    def stable_dump(self, attempts: int = 3) -> bytes:
        previous = None
        for _ in range(attempts):
            current = self.dump_once()
            if current == previous:
                return validate_physical_capture(current)
            previous = current
        raise EepromError(
            f"full physical EEPROM reads did not stabilize after {attempts} attempts")

    def write_byte(self, operation: ByteWrite) -> None:
        """Send one CRC-protected, compare-before-write, replay-safe byte update."""
        if not 0 <= operation.offset < EEPROM_SIZE:
            raise ValueError(f"EEPROM offset out of range: 0x{operation.offset:X}")
        body = (
            b"w"
            + operation.offset.to_bytes(2, "big")
            + bytes((operation.expected, operation.replacement))
        )
        self.sb._txs(body + checksum._crc(body, 0xFFFF).to_bytes(2, "big"))
        status = self.sb._rx(timeout=8.0)
        if status != 1:
            meanings = {
                2: "two physical reads disagreed",
                3: "address or request denied",
                4: "frame CRC failed",
                5: "compare-before-write found stale data",
                6: "physical write/readback failed",
            }
            raise EepromError(
                f"EEPROM byte write 0x{operation.offset:03X} failed: "
                f"{meanings.get(status, f'status {status}')}")

    def quit_to_normal(self) -> bool:
        try:
            self.sb._txs(b"q\xC3\x3C")
            self.sb._rx(timeout=1.0)
        except Exception:
            pass
        try:
            self.sb._ser().baudrate = 9600
            self.sb.ds2.baud = 9600
            self.sb._ser().reset_input_buffer()
        except Exception:
            pass
        for _ in range(25):
            try:
                marker = self.sb.ds2.read_mem(0xE740, 1)
                if marker in (b"\x00", b"\x03"):
                    return True
            except Exception:
                pass
            time.sleep(0.02)
        return False


def _finish_verified_write(
    recovery: EepromWriteRecovery,
    after: bytes,
    *,
    resolution: str,
) -> bytes:
    try:
        after = validate_physical_capture(after)
        if after != recovery.target:
            raise EepromError("full EEPROM readback does not match the prepared image")
        changed = changed_offsets(recovery.before, after)
        if recovery.after_path.exists():
            if recovery.after_path.read_bytes() != after:
                raise EepromError(
                    f"refusing to replace different after-image "
                    f"{recovery.after_path}")
            saved = recovery.after_path
        else:
            saved = _save_capture_atomic(recovery.after_path, after)
    except (Exception, KeyboardInterrupt) as error:
        raise EepromWriteRecoveryRequired(
            recovery, f"terminal-state readback failed: {error}") from error
    returned_to_normal = _quit_and_close(
        recovery.protocol, recovery.ds2)
    _finish_closed_journal(
        recovery.journal,
        returned_to_normal,
        outcome="success",
        reset_message=(
            "EEPROM readback is verified and archived, but normal DS2 reset "
            "was not confirmed. A power cycle does not clear E740=1; reconnect "
            "through installed Soft-BSL and retry finalization before normal use"
        ),
        resolution=resolution,
        after_path=saved,
        after_sha256=hashlib.sha256(after).hexdigest(),
        changed_offsets=[f"0x{index:03X}" for index in changed],
        audit_errors=recovery.audit_errors,
    )
    return after


def _resolve_write_recovery(
    recovery: EepromWriteRecovery, *, resolution: str, confirm=None
) -> bytes:
    if not isinstance(recovery, EepromWriteRecovery) or not recovery.is_open:
        raise ValueError("an open EepromWriteRecovery is required")
    try:
        current = _validate_recovery_image(
            recovery, recovery.protocol.stable_dump())
    except (Exception, KeyboardInterrupt) as error:
        raise EepromCommitUnknown(recovery, error) from error
    if current != recovery.target:
        if confirm is None:
            remaining = len(changed_offsets(current, recovery.target))
            raise EepromWriteRecoveryRequired(
                recovery,
                f"verified partial EEPROM image has {remaining} byte(s) remaining",
            )
        try:
            accepted = bool(confirm(
                "The retained session produced a safe partial image. "
                f"Continue the remaining {len(changed_offsets(current, recovery.target))} "
                "byte update(s) to the already prepared target?"
            ))
        except (EOFError, KeyboardInterrupt) as error:
            raise EepromWriteRecoveryRequired(
                recovery, "operator interrupted the recovery prompt") from error
        if not accepted:
            raise EepromWriteRecoveryRequired(
                recovery, "operator declined the remaining EEPROM writes")
        remaining_plan = build_write_plan(
            current, recovery.target, recovery.variant)
        _recovery_journal_append(
            recovery, "recovery_resume", remaining=len(remaining_plan))
        try:
            for operation in remaining_plan:
                recovery.protocol.write_byte(operation)
            current = recovery.protocol.stable_dump()
        except (Exception, KeyboardInterrupt) as error:
            raise EepromCommitUnknown(recovery, error) from error
    return _finish_verified_write(
        recovery,
        current,
        resolution=resolution,
    )


def resolve_write_recovery(recovery: EepromWriteRecovery) -> bytes:
    """Resolve a pending write using a read-only full-device comparison."""
    return _resolve_write_recovery(
        recovery, resolution="read_only_full_readback")


def repair_write_recovery(recovery: EepromWriteRecovery, *, confirm) -> bytes:
    """Explicitly resume only the already prepared target after full readback."""
    return _resolve_write_recovery(
        recovery, resolution="explicit_safe_resume", confirm=confirm)


def preflight(port: str, *, serial_factory=None) -> Preflight:
    """Admit only a recognized ECU with the installed Soft-BSL loader door."""
    ds2_kwargs = {"baud": 9600, "verbose": False, "echo": True}
    if serial_factory is not None:
        ds2_kwargs["serial_factory"] = serial_factory
    interface = ds2.DS2Interface(port, **ds2_kwargs)
    interface.open()
    try:
        interface.identify()
        marker_raw = interface.read_mem(0xE740, 1)
        if len(marker_raw) != 1 or marker_raw[0] not in (0, 1, 3):
            marker = marker_raw[0] if marker_raw else -1
            raise EepromError(
                f"Soft-BSL entry-state admission failed: "
                f"E740=0x{marker & 0xFF:02X}")
        _cal_variant, program_variant, _consistent = _sb._detect_ecu_variant(
            interface, accept_credit=False)
        door_patch = SOFTBSL_DOOR_PATCHES.get(program_variant)
        if door_patch is None:
            raise EepromError(
                "RAM EEPROM access requires a recognized MS41.0-MS41.3 "
                f"program (detected {program_variant or 'unknown'})")
        bank_raw = interface.read_mem(
            ecu_info.BANK_MARKER_ADDR, ecu_info.BANK_MARKER_LEN)
        bank = ecu_info.decode_bank_marker(bank_raw)
        if bank is None:
            raise EepromError(
                "installed Soft-BSL bank marker was not detected; stock ECUs "
                "must use CH341A for EEPROM access")
        if not _sb._live_patch_applied(interface, door_patch):
            raise EepromError(
                f"the matching {program_variant} Soft-BSL door hook "
                f"({door_patch}) is missing or changed")
        cal_mode = interface.read_mem(0xF1A0, 1)
        return Preflight(
            str(port),
            marker_raw[0],
            cal_mode[0] if len(cal_mode) == 1 else None,
            program_variant,
            bank,
            door_patch,
        )
    finally:
        interface.close()


def _agent_tiers(baud: str) -> tuple[str, ...]:
    if baud in ("auto", "high"):
        return ("high", "low")
    if baud == "mid":
        return ("mid", "low")
    if baud == "low":
        return ("low",)
    raise ValueError(f"unknown EEPROM agent baud tier {baud!r}")


def _open_agent(port, baud, log, serial_factory=None):
    admission = (
        preflight(port)
        if serial_factory is None
        else preflight(port, serial_factory=serial_factory)
    )
    agent_payload = load_eeprom_agent()
    last_error = None
    for tier in _agent_tiers(baud):
        interface = None
        try:
            session_kwargs = {
                "require_d2xx": tier != "low",
                "baud_tier": tier,
                "entry_mode": "auto",
                "agent_payload": agent_payload,
            }
            if serial_factory is not None:
                session_kwargs["serial_factory"] = serial_factory
            interface, softbsl = softbsl_service._open_session(
                port, log, **session_kwargs)
            protocol = EepromProtocol(softbsl)
            protocol.identity = protocol.identify()
            protocol.baud_tier = tier
            return admission, interface, protocol
        except (Exception, KeyboardInterrupt) as error:
            last_error = error
            if interface is not None:
                try:
                    returned = protocol.quit_to_normal()
                except Exception:
                    returned = False
                try:
                    interface.close()
                except Exception:
                    returned = False
                if not returned:
                    raise EepromResetRequired(
                        "EEPROM agent entry failed after upload and normal DS2 reset "
                        "could not be confirmed") from error
            if isinstance(error, KeyboardInterrupt) or tier == "low":
                raise
            log(
                f"EEPROM agent '{tier}' entry failed before any EEPROM write "
                f"({error}); retrying the complete entry at 9600 baud."
            )
    raise EepromError(f"EEPROM agent entry failed: {last_error}")


def read_eeprom(port: str, *, baud="auto", log=print,
                serial_factory=None) -> Capture:
    """Return two matching, CRC-protected, direct physical 512-byte reads."""
    admission, interface, protocol = _open_agent(
        port, baud, log, serial_factory=serial_factory)
    returned_to_normal = False
    try:
        image = protocol.stable_dump()
        if not protocol.quit_to_normal():
            raise EepromError("EEPROM read completed, but normal DS2 reset was not confirmed")
        returned_to_normal = True
        return Capture(image, admission)
    finally:
        if not returned_to_normal:
            protocol.quit_to_normal()
        interface.close()


def _write_eeprom(
    port: str,
    target_builder,
    *,
    variant: str | None,
    backup_path: str | os.PathLike,
    confirm,
    expected_before: bytes | None = None,
    baud="auto",
    log=print,
    operation="eeprom_image",
    serial_factory=None,
) -> Capture:
    if expected_before is not None:
        expected_before = validate_physical_capture(expected_before)
    backup = Path(backup_path)
    if backup.exists():
        raise FileExistsError(f"refusing to overwrite EEPROM backup {backup}")
    after_path = _after_capture_path(backup)
    if after_path.exists():
        raise FileExistsError(
            f"refusing to overwrite EEPROM after-image {after_path}")
    journal_path = Path(backup_path).with_suffix(".journal.jsonl")
    journal = OperationJournal(
        journal_path,
        operation=operation,
        metadata={
            "port": str(port),
            "requested_variant": variant,
            "requested_baud": baud,
        },
    )
    try:
        admission, interface, protocol = _open_agent(
            port, baud, log, serial_factory=serial_factory)
    except (Exception, KeyboardInterrupt) as error:
        try:
            journal.finish(
                "failed", error=f"{type(error).__name__}: {error}")
        except Exception as audit_error:
            raise EepromAuditError(
                f"RAM-agent entry did not complete, and journal finalization "
                f"also failed: {audit_error}"
            ) from audit_error
        if isinstance(error, KeyboardInterrupt):
            raise EepromCancelled(
                "EEPROM writer interrupted before RAM-agent entry") from error
        raise
    write_entered = False
    try:
        identity = protocol.identity
        if not (identity["capabilities"] & CAP_GENERIC_WRITE):
            raise EepromError("loaded EEPROM agent does not advertise generic write capability")
        if variant is not None and variant != admission.program_variant:
            raise EepromError(
                f"prepared image uses {variant}, but the connected ECU is "
                f"{admission.program_variant}")
        active_variant = admission.program_variant
        before = protocol.stable_dump()
        if expected_before is not None and before != expected_before:
            raise EepromError(
                "EEPROM changed since compatibility checking; no byte write was sent")
        target = validate_physical_capture(
            target_builder(before, admission))
        plan = build_write_plan(before, target, active_variant)
        changed = changed_offsets(before, target)
        saved = save_capture(backup_path, before)
        journal.append(
            "before_capture_saved",
            path=saved,
            sha256=hashlib.sha256(before).hexdigest(),
            target_sha256=hashlib.sha256(target).hexdigest(),
            program_variant=active_variant,
            softbsl_bank=admission.softbsl_bank,
            door_patch=admission.door_patch,
            baud_tier=protocol.baud_tier,
            changed_offsets=[f"0x{offset:03X}" for offset in changed],
        )
        if not plan:
            returned_to_normal = _quit_and_close(protocol, interface)
            _finish_closed_journal(
                journal,
                returned_to_normal,
                outcome="success",
                reset_message=(
                    "EEPROM already matched the prepared image, but normal DS2 "
                    "reset was not confirmed. A power cycle does not clear "
                    "E740=1; reconnect through installed Soft-BSL and retry "
                    "finalization before normal use"
                ),
                resolution="target_already_matches_live_image",
                writes_sent=0,
                before_path=saved,
                before_sha256=hashlib.sha256(before).hexdigest(),
                target_sha256=hashlib.sha256(target).hexdigest(),
                changed_offsets=[],
            )
            return Capture(before, admission, write_performed=False)
        recovery = EepromWriteRecovery(
            interface,
            protocol,
            before,
            target,
            active_variant,
            plan,
            after_path,
            journal,
            admission,
        )
        interrupted = False
        try:
            confirmed = confirm(
                f"Prepared {len(changed)} changed EEPROM "
                f"byte(s) for {active_variant}.\n\n"
                f"Immutable before-image: {saved}\n"
                f"Before SHA-256: {hashlib.sha256(before).hexdigest()}\n"
                f"Target SHA-256: {hashlib.sha256(target).hexdigest()}\n\n"
                "Write the prepared 512-byte image?"
            )
        except (EOFError, KeyboardInterrupt):
            confirmed = False
            interrupted = True
        if not confirmed:
            returned_to_normal = _quit_and_close(protocol, interface)
            _finish_closed_journal(
                journal,
                returned_to_normal,
                outcome="aborted",
                reset_message=(
                    "write was cancelled before any byte update, but normal DS2 reset "
                    "was not confirmed. A power cycle does not clear E740=1; "
                    "reconnect through installed Soft-BSL and retry finalization"
                ),
                reason=(
                    "confirmation input interrupted"
                    if interrupted else "operator cancelled before write"
                ),
            )
            raise EepromCancelled("EEPROM write cancelled before any byte write")

        journal.append("write_started", operations=len(plan))
        for index, byte_write in enumerate(plan):
            write_entered = True
            try:
                protocol.write_byte(byte_write)
            except EepromError as error:
                _recovery_journal_append(
                    recovery, "byte_write_failed",
                    operation_index=index,
                    offset=f"0x{byte_write.offset:03X}",
                    error=str(error),
                )
                raise EepromWriteRecoveryRequired(recovery, str(error)) from error
            except (Exception, KeyboardInterrupt) as error:
                raise EepromCommitUnknown(recovery, error) from error

        try:
            after = protocol.stable_dump()
        except Exception as error:
            raise EepromWriteRecoveryRequired(
                recovery,
                f"post-write physical readback failed: {error}",
            ) from error
        after = _finish_verified_write(
            recovery,
            after,
            resolution="all_byte_replies_and_full_readback",
        )
        return Capture(after, admission, write_performed=True)
    except (
        EepromCancelled,
        EepromAuditError,
        EepromCommitUnknown,
        EepromResetRequired,
        EepromWriteRecoveryRequired,
    ):
        raise
    except (Exception, KeyboardInterrupt) as error:
        if write_entered:
            raise EepromWriteRecoveryRequired(
                recovery,
                f"{type(error).__name__}: {error}",
            ) from error
        returned_to_normal = _quit_and_close(protocol, interface)
        try:
            _finish_closed_journal(
                journal,
                returned_to_normal,
                outcome="failed",
                reset_message=(
                    "No EEPROM byte write was sent, but normal DS2 reset was not "
                    "confirmed. A power cycle does not clear E740=1; reconnect "
                    "through installed Soft-BSL and retry finalization"
                ),
                error=f"{type(error).__name__}: {error}",
            )
        except (EepromAuditError, EepromResetRequired) as cleanup_error:
            raise cleanup_error from error
        if isinstance(error, KeyboardInterrupt):
            raise EepromCancelled(
                "EEPROM writer interrupted before the first byte write") from error
        raise


def write_image(
    port: str,
    target: bytes,
    *,
    variant: str,
    backup_path: str | os.PathLike,
    confirm,
    expected_before: bytes | None = None,
    baud="auto",
    log=print,
    serial_factory=None,
) -> Capture:
    """Write a prepared exact image through replay-safe byte transactions."""
    target = validate_write_image(target, variant)
    return _write_eeprom(
        port,
        lambda _before, _admission: target,
        variant=variant,
        backup_path=backup_path,
        confirm=confirm,
        expected_before=expected_before,
        baud=baud,
        log=log,
        operation="eeprom_image",
        serial_factory=serial_factory,
    )


def write_transmission(
    port: str,
    mode: str,
    *,
    backup_path: str | os.PathLike,
    confirm,
    expected_before: bytes | None = None,
    baud="auto",
    log=print,
    serial_factory=None,
) -> Capture:
    """Compatibility wrapper for the version-specific transmission shortcut."""
    return _write_eeprom(
        port,
        lambda before, admission: set_transmission_mode(
            before, mode, admission.program_variant),
        variant=None,
        backup_path=backup_path,
        confirm=confirm,
        expected_before=expected_before,
        baud=baud,
        log=log,
        operation="eeprom_transmission",
        serial_factory=serial_factory,
    )


def _print_inspection(result: dict) -> None:
    print(
        f"variant={result['variant']} size={result['size']} "
        f"sha256={result['sha256']}"
    )
    decoded = result["decoded"]
    if decoded["looks_like_zero_padded_ram_mirror"]:
        mirror_size = result["lower_mirror_size"]
        print(
            f"WARNING: bytes 0x{mirror_size:03X}-0x1FF are all zero; this "
            "looks like a padded RAM mirror, not a physical full-device dump."
        )
    for row in result["fields"]:
        check = (
            "OK" if row.get("check_ok") else "BAD"
            if row["checked"] else "unchecked"
        )
        print(
            f"0x{row['offset']:03X} len={row['length']:3d} "
            f"{check:9s} {row['label']}"
        )
    print(json.dumps(decoded, indent=2))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="MS41 direct physical 24C04 reader and guarded field editor")
    commands = parser.add_subparsers(dest="command", required=True)

    inspect_cmd = commands.add_parser("inspect", help="annotate a 512-byte capture")
    inspect_cmd.add_argument("image")
    inspect_cmd.add_argument(
        "--variant", choices=tuple(FIELDS_BY_VARIANT), default="MS41.3"
    )
    inspect_cmd.add_argument("--json", action="store_true")

    dump_cmd = commands.add_parser("dump", help="read all 512 physical bytes")
    dump_cmd.add_argument("port")
    dump_cmd.add_argument("-o", "--output", required=True)
    dump_cmd.add_argument(
        "--baud", choices=("auto", "high", "mid", "low"), default="auto")

    write_cmd = commands.add_parser(
        "set-transmission", help="guarded masked write of the version-specific record")
    write_cmd.add_argument("port")
    write_cmd.add_argument("mode", choices=("at", "mt"))
    write_cmd.add_argument("--backup", required=True)
    write_cmd.add_argument(
        "--baud", choices=("auto", "high", "mid", "low"), default="auto")
    write_cmd.add_argument(
        "--yes-i-understand", action="store_true",
        help="still requires typed confirmation after the agent prepares the write")

    args = parser.parse_args(argv)
    if args.command == "inspect":
        result = inspect_image(Path(args.image).read_bytes(), args.variant)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            _print_inspection(result)
        return 0
    if args.command == "dump":
        capture = read_eeprom(args.port, baud=args.baud)
        save_capture(args.output, capture.image)
        print(
            f"saved 512 bytes to {args.output}; "
            f"sha256={hashlib.sha256(capture.image).hexdigest()}")
        return 0
    if not args.yes_i_understand:
        parser.error("set-transmission requires --yes-i-understand")

    def typed_confirmation(message):
        print(message)
        return input("Type WRITE EEPROM to continue: ") == "WRITE EEPROM"

    def retained_input(message):
        while True:
            try:
                return input(message)
            except (EOFError, KeyboardInterrupt):
                print(
                    "\nThe live recovery session may own an invalid record; "
                    "EOF/Ctrl+C is ignored."
                )

    def typed_repair_confirmation(message):
        print(message)
        return retained_input(
            "Type RESUME EEPROM to continue: ") == "RESUME EEPROM"

    try:
        capture = write_transmission(
            args.port,
            args.mode,
            backup_path=args.backup,
            confirm=typed_confirmation,
            baud=args.baud,
        )
        image = capture.image
    except EepromCancelled as error:
        print(error)
        return 2
    except EepromAuditError as error:
        print(error)
        return 4
    except EepromResetRequired as error:
        print(error)
        return 3
    except (EepromCommitUnknown, EepromWriteRecoveryRequired) as initial_error:
        recovery = initial_error.recovery
        current_error = initial_error
        while True:
            print(current_error)
            try:
                image = resolve_write_recovery(recovery)
                break
            except EepromCommitUnknown as query_error:
                current_error = query_error
                retained_input(
                    "Press Enter to retry the read-only state query...")
            except EepromAuditError as audit_error:
                print(audit_error)
                return 4
            except EepromResetRequired as reset_error:
                print(reset_error)
                return 3
            except (EOFError, KeyboardInterrupt):
                current_error = EepromWriteRecoveryRequired(
                    recovery,
                    "operator interrupt ignored during read-only recovery",
                )
            except EepromWriteRecoveryRequired as query_error:
                current_error = query_error
                try:
                    image = repair_write_recovery(
                        recovery,
                        confirm=typed_repair_confirmation,
                    )
                    break
                except EepromCommitUnknown as repair_error:
                    current_error = repair_error
                    retained_input(
                        "Press Enter to retry the read-only state query...")
                except EepromAuditError as audit_error:
                    print(audit_error)
                    return 4
                except EepromWriteRecoveryRequired as repair_error:
                    current_error = repair_error
                    retained_input(
                        "No unsafe replay was sent. Press Enter to retry "
                        "the retained session..."
                    )
                except EepromResetRequired as reset_error:
                    print(reset_error)
                    return 3
                except (EOFError, KeyboardInterrupt):
                    current_error = EepromWriteRecoveryRequired(
                        recovery,
                        "operator interrupt ignored during explicit repair",
                    )
    variant = (
        capture.preflight.program_variant
        if "capture" in locals() else recovery.variant)
    offset = transmission_offset(variant)
    print("verified record " + image[offset:offset + 4].hex(" "))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
