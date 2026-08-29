"""MS41 24C04 inspection plus guarded full-image RAM-agent operations."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import time
import uuid
from dataclasses import asdict, dataclass, field as dataclass_field
from pathlib import Path

import ds2
import dtc
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
                "Operating-time counter (three-copy sequence)", "history"),
    EepromField(0x006, 4, False, "save_count",
                "EEPROM save counter", "history"),
    EepromField(0x00A, 4, True, "identity_gate",
                "Adaptation compatibility key", "system"),
    EepromField(0x00E, 68, True, "knock_adaptation",
                "Learned knock corrections (64 cells and overall correction)",
                "adaptation"),
    EepromField(0x052, 4, True, "load_model_correction",
                "Learned load-model correction (signed Q8.8)", "adaptation"),
    EepromField(0x056, 6, True, "vanos_adaptation",
                "VANOS learned reference and controller state", "adaptation"),
    EepromField(0x05C, 4, True, "tps_adaptation",
                "Throttle-position adaptation", "adaptation"),
    EepromField(0x060, 8, True, "engine_roughness_segment_adaptation",
                "Learned relative ignition/dwell gains",
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
                "Repeat-start coolant reference", "service"),
    EepromField(0x1B8, 10, True, "diagnostic_counters",
                "Diagnostic event counters", "diagnostic"),
    EepromField(0x1C2, 4, False, "output_test_nonce",
                "Actuator-test sequence", "service"),
    EepromField(0x1C6, 4, True, "load_collective",
                "Persistent warm-up history counter", "history"),
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
                "Operating-time counter (three-copy sequence)", "history"),
    EepromField(0x006, 4, False, "save_count",
                "EEPROM save counter", "history"),
    EepromField(0x00A, 4, True, "identity_gate",
                "Adaptation compatibility key", "system"),
    EepromField(0x00E, 36, True, "knock_adaptation",
                "Learned knock corrections (64 packed cells and overall correction)",
                "adaptation"),
    EepromField(0x032, 4, True, "load_model_correction",
                "Learned load-model correction (signed Q8.8)", "adaptation"),
    EepromField(0x036, 6, True, "vanos_adaptation",
                "VANOS learned reference and controller state", "adaptation"),
    EepromField(0x03C, 4, True, "tps_adaptation",
                "Throttle-position adaptation", "adaptation"),
    EepromField(0x040, 8, True, "engine_roughness_segment_adaptation",
                "Learned relative ignition/dwell gains",
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
                "Repeat-start coolant reference", "service"),
    EepromField(0x1BA, 10, True, "diagnostic_counters",
                "Diagnostic event counters", "diagnostic"),
    EepromField(0x1C4, 4, False, "output_test_nonce",
                "Actuator-test sequence", "service"),
    EepromField(0x1C8, 4, True, "load_collective",
                "Persistent warm-up history counter", "history"),
    EepromField(0x1CC, 4, True, "transmission",
                "Transmission selection and other coding bits", "coding"),
    EepromField(0x1D0, 4, True, "shutdown_coolant",
                "Coolant temperature at shutdown and warm-restart count", "history"),
    EepromField(0x1D4, 8, False, "unresolved_tail_mirror",
                "Unidentified EEPROM data", "unknown"),
)

FIELDS_MS410 = (
    EepromField(0x000, 6, False, "cycle_sequence",
                "Operating-time counter (three-copy sequence)", "history"),
    EepromField(0x006, 4, False, "save_count",
                "EEPROM save counter", "history"),
    EepromField(0x00A, 4, True, "identity_gate",
                "Adaptation compatibility key", "system"),
    EepromField(0x00E, 68, True, "knock_adaptation",
                "Learned knock corrections (64 cells and overall correction)",
                "adaptation"),
    EepromField(0x052, 4, True, "load_model_correction",
                "Learned load-model correction (signed Q8.8)", "adaptation"),
    EepromField(0x056, 6, True, "vanos_adaptation",
                "VANOS learned reference and controller state", "adaptation"),
    EepromField(0x05C, 4, True, "tps_adaptation",
                "Throttle-position adaptation", "adaptation"),
    EepromField(0x060, 8, True, "engine_roughness_segment_adaptation",
                "Learned relative ignition/dwell gains",
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
                "Repeat-start coolant reference", "service"),
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

KNOCK_LOGICAL_CELLS = 64

DECODE_LAYOUTS = {
    "MS41.0": {
        "mirror_size": 0x1A6,
        "knock_stored_bytes": 64,
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
        # Each stored byte contains two logical knock cells as nibbles.
        "knock_stored_bytes": 32,
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
        "knock_stored_bytes": 64,
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

# Exact canonical descriptor order, not DS2 database insertion order. The .1
# suffix includes two IDs absent in .2/.3; .1's persisted slot stride is also 11.
_FAULT_CODES_MS410 = (
    12, 8, 83, 65, 214, 14, 10, 75, 76, 200, 201, 25, 55, 202, 203, 68,
    6, 33, 22, 23, 5, 24, 29, 31, 30, 3, 1, 2, 56, 210, 57, 59, 53, 27,
    211, 74, 69, 21, 212, 52, 80, 81, 82, 215, 100, 216, 218, 219, 217,
    209, 18, 16,
)
_FAULT_CODES_MS411 = _FAULT_CODES_MS410 + (
    11, 253, 254, 255, 252, 251, 250, 233, 234, 231, 232, 248, 249, 79, 61,
    244, 238, 242, 240, 243, 239, 241, 62, 35, 245, 246, 51, 50, 77, 78,
    235, 236, 20, 229, 230, 227, 228, 225, 226, 222, 46, 247, 204, 220, 221,
)
# Exact pointer-owned sources use the canonical BMW environment IDs where the
# label agrees. Values above 0xFF distinguish equally scaled signals whose BMW
# generic label would otherwise identify the wrong bank or sensor.
_ENV_THROTTLE_SIGNAL = 0x100
_ENV_IAT_SIGNAL = 0x101
_ENV_FRONT_O2_1 = 0x102
_ENV_FRONT_O2_2 = 0x103
_ENV_FRONT_HEATER_1 = 0x104
_ENV_FRONT_HEATER_2 = 0x105
_ENV_REAR_O2_1 = 0x106
_ENV_REAR_O2_2 = 0x107
_ENV_REAR_HEATER_1 = 0x108
_ENV_REAR_HEATER_2 = 0x109
_ENV_FRONT_O2_ENVELOPE_1 = 0x10A
_ENV_FRONT_O2_ENVELOPE_2 = 0x10B
_ENV_VANOS_POSITION_MS410 = 0x10C
_ENV_TANK_PRESSURE_SIGNAL = 0x10D
_ENV_STARTUP_ECT = 0x10E
_ENV_STARTUP_IAT = 0x10F
_ENV_REAR_O2_ERROR_1 = 0x110
_ENV_REAR_O2_ERROR_2 = 0x111
_ENV_VANOS_POSITION_LATER = 0x112
_FAULT_ENV_DEFINITIONS = {
    _ENV_THROTTLE_SIGNAL: ("Throttle-sensor signal", "V", 0.01952, 0.0),
    _ENV_IAT_SIGNAL: ("Intake-air sensor signal", "V", 0.0196, 0.0),
    _ENV_FRONT_O2_1: ("Front oxygen-sensor voltage bank 1", "V", 0.0196, 0.0),
    _ENV_FRONT_O2_2: ("Front oxygen-sensor voltage bank 2", "V", 0.0196, 0.0),
    _ENV_FRONT_HEATER_1: ("Front oxygen-sensor heater bank 1", "%", 0.391, 0.0),
    _ENV_FRONT_HEATER_2: ("Front oxygen-sensor heater bank 2", "%", 0.391, 0.0),
    _ENV_REAR_O2_1: ("Rear oxygen-sensor voltage bank 1", "V", 0.0196, 0.0),
    _ENV_REAR_O2_2: ("Rear oxygen-sensor voltage bank 2", "V", 0.0196, 0.0),
    _ENV_REAR_HEATER_1: ("Rear oxygen-sensor heater bank 1", "%", 0.391, 0.0),
    _ENV_REAR_HEATER_2: ("Rear oxygen-sensor heater bank 2", "%", 0.391, 0.0),
    _ENV_FRONT_O2_ENVELOPE_1: ("Tracked front oxygen-sensor envelope bank 1", "V", 5 / 256, 0.0),
    _ENV_FRONT_O2_ENVELOPE_2: ("Tracked front oxygen-sensor envelope bank 2", "V", 5 / 256, 0.0),
    _ENV_VANOS_POSITION_MS410: ("Actual VANOS position", "degrees crank", 0.375, 0.0),
    _ENV_TANK_PRESSURE_SIGNAL: ("Tank-pressure sensor signal", "V", 5 / 256, 0.0),
    _ENV_STARTUP_ECT: ("Startup/reference coolant temperature", "deg C", 0.7471, -48.0),
    _ENV_STARTUP_IAT: ("Startup/reference intake-air temperature", "deg C", 0.7471, -48.0),
    _ENV_REAR_O2_ERROR_1: ("Rear oxygen-sensor setpoint error bank 1", "V", 0.0390625, -5.0),
    _ENV_REAR_O2_ERROR_2: ("Rear oxygen-sensor setpoint error bank 2", "V", 0.0390625, -5.0),
    _ENV_VANOS_POSITION_LATER: ("Actual VANOS position", "degrees crank", 0.3745, 0.0),
}
_IGNITION_COIL_CODES = frozenset((1, 2, 3, 29, 30, 31))


def _fault_environment_rows(rows, replacements):
    result = [list(row) for row in rows]
    for identifier, position, source in replacements:
        result[identifier][position] = source
    return tuple(tuple(row) for row in result)


def _fault_environment_definition(identifier):
    return _FAULT_ENV_DEFINITIONS.get(identifier) or dtc.environment_definition(identifier)


# Zero means the pointer or its standalone conversion remains unresolved/raw.
_FAULT_ENV_MS410 = (
    (1, 12, 24, 0), (1, 0, 0, 0), (1, 0, 24, 9), (1, 0, 24, 9),
    (1, 12, 0, 9), (1, 12, 24, 0), (1, 12, 25, 0), (1, 12, 0, 0),
    (1, 12, 0, 0), (1, 0, 0, 0), (1, 0, 0, 0), (1, 12, 0, 24),
    (1, 12, 0, 24), (1, 24, 0, 0), (1, 24, 0, 0), (1, 12, 24, 0),
    (1, 12, 9, 0), (1, 12, 9, 0), (1, 12, 9, 0), (1, 12, 9, 0),
    (1, 12, 9, 0), (1, 12, 9, 0), (1, 12, 0, 0), (1, 12, 0, 0),
    (1, 12, 0, 0), (1, 12, 0, 0), (1, 12, 0, 0), (1, 12, 0, 0),
    (1, 12, 24, 9), (1, 12, 24, 9), (1, 12, 0, 0), (1, 12, 0, 0),
    (1, 0, 0, 0), (1, 0, 0, 0), (1, 0, 0, 0), (1, 0, 24, 9),
    (1, 0, 24, 9), (1, 12, 0, 0), (1, 12, 0, 0), (1, 12, 24, 9),
    (1, 12, 0, 24), (1, 12, 0, 24), (1, 12, 0, 24), (1, 12, 0, 24),
    (0, 9, 0, 0), (1, 12, 0, 9), (1, 12, 24, 9), (1, 12, 24, 9),
    (1, 12, 24, 9), (24, 25, 0, 9), (24, 25, 0, 9), (1, 9, 24, 25),
)
_FAULT_ENV_MS410 = _fault_environment_rows(_FAULT_ENV_MS410, (
    (0x00, 3, _ENV_THROTTLE_SIGNAL),
    (0x01, 1, 0x0A), (0x01, 2, 0x0B), (0x01, 3, 0x1A),
    (0x02, 1, 0x10), (0x03, 1, 0x10), (0x05, 3, _ENV_IAT_SIGNAL),
    (0x07, 2, _ENV_FRONT_HEATER_1), (0x07, 3, _ENV_FRONT_O2_1),
    (0x08, 2, _ENV_FRONT_HEATER_2), (0x08, 3, _ENV_FRONT_O2_2),
    (0x09, 2, _ENV_FRONT_HEATER_1), (0x09, 3, _ENV_FRONT_O2_1),
    (0x0A, 2, _ENV_FRONT_HEATER_2), (0x0A, 3, _ENV_FRONT_O2_2),
    (0x0B, 2, _ENV_FRONT_O2_1), (0x0C, 2, _ENV_FRONT_O2_2),
    (0x0D, 2, _ENV_FRONT_HEATER_1), (0x0D, 3, _ENV_FRONT_O2_1),
    (0x0E, 2, _ENV_FRONT_HEATER_2), (0x0E, 3, _ENV_FRONT_O2_2),
    (0x10, 3, 0x05), (0x11, 3, 0x06), (0x12, 3, 0x05),
    (0x13, 3, 0x06), (0x14, 3, 0x05), (0x15, 3, 0x06),
    (0x20, 1, 0x02), (0x20, 2, 0x0A), (0x20, 3, 0x0B),
    (0x21, 1, 0x02), (0x21, 2, 0x0A), (0x21, 3, 0x0B),
    (0x22, 1, 0x02), (0x22, 2, 0x0A), (0x22, 3, 0x0B),
    (0x23, 1, 0x0D), (0x24, 1, 0x0D), (0x25, 3, 0x0D),
    (0x26, 3, 0x0D), (0x28, 2, 0x0D), (0x29, 2, 0x0D),
    (0x2A, 2, 0x0D), (0x2B, 2, 0x0D), (0x31, 2, 0x0D), (0x32, 2, 0x0D),
    (0x04, 2, 0x0E), (0x2D, 2, 0x0E),
    (0x06, 3, 0x13), (0x2C, 0, 0x13),
    (0x09, 1, _ENV_FRONT_O2_ENVELOPE_1),
    (0x0A, 1, _ENV_FRONT_O2_ENVELOPE_2),
    (0x0F, 3, 0x1B),
    (0x1E, 2, 0x15), (0x1E, 3, 0x16),
    (0x1F, 2, 0x15), (0x1F, 3, 0x16),
    (0x25, 2, _ENV_VANOS_POSITION_MS410),
    (0x26, 2, _ENV_VANOS_POSITION_MS410),
))
_FAULT_ENV_MS411 = _FAULT_ENV_MS410 + (
    (1, 12, 0, 0), (1, 12, 0, 0), (1, 12, 0, 9), (1, 12, 0, 9),
    (1, 12, 0, 9), (1, 12, 0, 9), (1, 12, 24, 0), (1, 12, 24, 25),
    (1, 12, 24, 25), (1, 12, 0, 0), (1, 12, 0, 0), (1, 12, 24, 25),
    (1, 12, 24, 25), (1, 12, 0, 24), (1, 12, 0, 24), (1, 12, 24, 9),
    (24, 0, 0, 0), (24, 0, 0, 0), (24, 0, 0, 0), (24, 0, 0, 0),
    (24, 0, 0, 0), (24, 0, 0, 0), (1, 24, 25, 9), (1, 24, 25, 9),
    (0, 0, 0, 0), (0, 0, 0, 0), (1, 12, 24, 9), (1, 12, 24, 9),
    (1, 12, 0, 0), (1, 12, 0, 0), (1, 0, 0, 0), (1, 0, 0, 0),
    (1, 0, 24, 9), (1, 12, 0, 0), (1, 12, 0, 0), (1, 12, 24, 0),
    (1, 12, 24, 0), (1, 12, 24, 25), (1, 12, 24, 25), (1, 12, 0, 24),
    (1, 12, 24, 9), (1, 12, 0, 0), (1, 0, 0, 12), (1, 12, 0, 0),
    (1, 12, 0, 0),
)
_FAULT_ENV_MS411 = _fault_environment_rows(_FAULT_ENV_MS411, (
    (0x3D, 3, _ENV_FRONT_HEATER_1), (0x3E, 3, _ENV_FRONT_HEATER_2),
    (0x41, 2, _ENV_REAR_O2_1), (0x42, 2, _ENV_REAR_O2_2),
    (0x50, 2, _ENV_REAR_HEATER_1), (0x50, 3, _ENV_REAR_O2_1),
    (0x51, 2, _ENV_REAR_HEATER_2), (0x51, 3, _ENV_REAR_O2_2),
    (0x52, 1, _ENV_REAR_O2_1), (0x52, 2, _ENV_REAR_HEATER_1),
    (0x53, 1, _ENV_REAR_O2_2), (0x53, 2, _ENV_REAR_HEATER_2),
    (0x54, 1, 0x0D), (0x55, 3, _ENV_FRONT_HEATER_1),
    (0x56, 3, _ENV_FRONT_HEATER_2),
    (0x5E, 1, 0x0A), (0x5E, 2, 0x0B),
    (0x5F, 3, _ENV_REAR_HEATER_1), (0x60, 3, _ENV_REAR_HEATER_2),
    (0x25, 2, _ENV_VANOS_POSITION_LATER),
    (0x26, 2, _ENV_VANOS_POSITION_LATER),
    (0x34, 2, 0x1B), (0x34, 3, _ENV_TANK_PRESSURE_SIGNAL),
    (0x35, 2, 0x1B), (0x35, 3, _ENV_STARTUP_IAT),
    (0x36, 2, 0x1B), (0x37, 2, 0x1B), (0x38, 2, 0x1B),
    (0x39, 2, 0x1B), (0x3A, 3, 0x1B),
    (0x4C, 0, _ENV_STARTUP_ECT), (0x4C, 1, _ENV_STARTUP_IAT),
    (0x4C, 2, 0x01), (0x4C, 3, 0x09),
    (0x4D, 0, _ENV_STARTUP_ECT), (0x4D, 1, _ENV_STARTUP_IAT),
    (0x4D, 2, 0x01), (0x4D, 3, 0x09),
    (0x5B, 2, _ENV_STARTUP_ECT),
    (0x5D, 2, _ENV_STARTUP_ECT), (0x5D, 3, _ENV_STARTUP_IAT),
    (0x3D, 2, _ENV_FRONT_O2_ENVELOPE_1),
    (0x3E, 2, _ENV_FRONT_O2_ENVELOPE_2),
    (0x52, 3, _ENV_REAR_O2_ERROR_1), (0x53, 3, _ENV_REAR_O2_ERROR_2),
    (0x55, 2, _ENV_FRONT_O2_ENVELOPE_1),
    (0x56, 2, _ENV_FRONT_O2_ENVELOPE_2),
    (0x5F, 2, _ENV_REAR_O2_1), (0x60, 2, _ENV_REAR_O2_2),
))
_FAULT_ENV_MS412 = _fault_environment_rows(_FAULT_ENV_MS411[:-2], (
    (0x52, 3, 0), (0x53, 3, 0),
))
_FAULT_LAYOUTS = {
    "MS41.0": (10, _FAULT_CODES_MS410, _FAULT_ENV_MS410, (209, 18, 16)),
    "MS41.1": (11, _FAULT_CODES_MS411, _FAULT_ENV_MS411, (16, 204, 220, 221)),
    "MS41.2": (12, _FAULT_CODES_MS411[:-2], _FAULT_ENV_MS412, (16, 204)),
}
_FAULT_LAYOUTS["MS41.3"] = _FAULT_LAYOUTS["MS41.2"]
_FAULT_SNAPSHOTS = {
    "MS41.1": {"rpm": 0x1AA, "load_mg_stroke": 0x1AC, "speed_kmh": 0x1AD,
               "coolant_celsius": 0x1AE,
               "stft_1_percent": 0x196, "stft_2_percent": 0x198,
               "ltft_1_percent": 0x1A2, "ltft_2_percent": 0x1A4,
               "pp1_1_raw": 0x19A, "pp1_2_raw": 0x19C,
               "lambda_state_1": 0x19E, "lambda_state_2": 0x1A0,
               "pt2_1_raw": 0x1A6, "pt2_2_raw": 0x1A8,
               "internal_id": 0x1AF, "state": 0x1B0, "flags": 0x1B1},
    "MS41.2": {"rpm": 0x18E, "load_mg_stroke": 0x190, "coolant_celsius": 0x191,
               "speed_kmh": 0x196,
               "stft_1_percent": 0x192, "stft_2_percent": 0x194,
               "ltft_1_percent": 0x1A0, "ltft_2_percent": 0x1A2,
               "pp1_1_raw": 0x198, "pp1_2_raw": 0x19A,
               "lambda_state_1": 0x19C, "lambda_state_2": 0x19E,
               "pt2_1_raw": 0x1A4, "pt2_2_raw": 0x1A6,
               "internal_id": 0x1A8, "state": 0x1A9, "flags": 0x1AA},
}
_FAULT_SNAPSHOTS["MS41.3"] = _FAULT_SNAPSHOTS["MS41.2"]

# Dedicated MS41.2 fault-management envelope records. These are keyed by
# internal fault ID, not by saved-occurrence slot number. MS41.1 packs a larger
# homologous runtime structure into parallel arrays and must not use this map.
_FAULT_MANAGEMENT_IDS_MS412 = (0x44, 0x45, 0x46, 0x47, 0x48, 0x49, 0x57, 0x58, 0x0D, 0x0E)

# MS41.1 compresses four reconstructed envelope bytes into three parallel
# six-bit arrays. Groups A/B/C cover three proven misfire-monitor phases; group
# D covers mixture and post-catalyst lambda IDs.
_FAULT_MANAGEMENT_GROUPS_MS411 = {
    "A": ((0x44, 0x45, 0x46, 0x47, 0x48, 0x49), 0x128, 0x13E, 0x154, 0x16A, 0x180),
    "B": ((0x44, 0x45, 0x46, 0x47, 0x48, 0x49), 0x12E, 0x144, 0x15A, 0x170, 0x186),
    "C": ((0x44, 0x45, 0x46, 0x47, 0x48, 0x49), 0x134, 0x14A, 0x160, 0x176, 0x18C),
    "D": ((0x57, 0x58, 0x0D, 0x0E), 0x13A, 0x150, 0x166, 0x17C, 0x192),
}
_FAULT_MANAGEMENT_GROUP_ROLES_MS411 = {
    "A": ("short-window severe-misfire",
          "Produced from the 600-count short-window source and consumed by the "
          "severe-misfire output-mask path."),
    "B": ("long-window pre-switch misfire",
          "Produced from the 3000-count long-window source before the monitor's "
          "internal phase switch."),
    "C": ("long-window post-switch qualified misfire",
          "Produced from the same 3000-count long-window source after the phase "
          "switch and an additional qualification threshold."),
    "D": ("mixture/post-catalyst lambda",
          "Owned by the mixture-deviation and post-catalyst lambda-regulation paths."),
}


def _ms411_management_record(image: bytes, group: str, index: int) -> dict:
    identifiers, p0_base, p1_base, p2_base, flags_base, countdown_base = (
        _FAULT_MANAGEMENT_GROUPS_MS411[group]
    )
    if not 0 <= index < len(identifiers):
        raise ValueError("MS41.1 fault-management record index is out of range")
    p0_offset, p1_offset, p2_offset = p0_base + index, p1_base + index, p2_base + index
    p0, p1, p2 = image[p0_offset], image[p1_offset], image[p2_offset]
    packed = (p0 >> 2, ((p0 & 0x03) << 4) | (p1 >> 4),
              ((p1 & 0x0F) << 2) | (p2 >> 6), p2 & 0x3F)
    return {
        "identifier": identifiers[index],
        "values": tuple((value << 2) | 0x02 for value in packed),
        "offsets": (p0_offset, p1_offset, p2_offset, flags_base + index,
                    countdown_base + index),
        "flags": image[flags_base + index],
        "countdown": image[countdown_base + index],
    }


def _ms411_management_field(field_id: str):
    marker = "_management_"
    if marker not in field_id:
        return None
    parts = field_id.split(marker, 1)[1].split("_")
    token_index = 0 if parts and parts[0][:1] in _FAULT_MANAGEMENT_GROUPS_MS411 else 1
    if len(parts) <= token_index + 1:
        return None
    token = parts[token_index]
    try:
        index = int(token[1:])
    except ValueError:
        return None
    if (len(token) < 2 or token[0] not in _FAULT_MANAGEMENT_GROUPS_MS411
            or not 0 <= index < len(_FAULT_MANAGEMENT_GROUPS_MS411[token[0]][0])):
        return None
    return token[0], index, "_".join(parts[token_index + 1:])

_DIAGNOSTIC_COUNTERS_MS412 = (
    ("Catalyst-efficiency monitor completions — bank 1",
     "Incremented when the bank 1 catalyst overall-efficiency evaluation window completes."),
    ("Catalyst-efficiency monitor completions — bank 2",
     "Incremented when the bank 2 catalyst overall-efficiency evaluation window completes."),
    ("Secondary-air monitor completions — bank 1",
     "Incremented when the bank 1 secondary-air-system evaluation completes."),
    ("Secondary-air monitor completions — bank 2",
     "Incremented when the bank 2 secondary-air-system evaluation completes."),
    ("Secondary-air valve sticking evaluations",
     "Incremented when the secondary-air-valve mechanical-sticking evaluation completes."),
    ("Tank-vent/leak diagnostic finalizations",
     "Incremented by the tank-ventilation/leak diagnostic finalization paths."),
    ("Misfire-monitor evaluation windows",
     "Incremented when the aggregate six-cylinder misfire evaluation window completes."),
)
# Raw-code writers omitted by the linear MS41.1 export map all seven bytes
# one-for-one to the later-family monitor counter roles.
_DIAGNOSTIC_COUNTERS_MS411 = _DIAGNOSTIC_COUNTERS_MS412

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
    return decode_transmission_record(image[offset:offset + 4])


def decode_transmission_record(raw: bytes) -> dict:
    """Decode one exact four-byte value/check record."""
    raw = bytes(raw)
    if len(raw) != 4:
        raise ValueError("transmission record must be exactly four bytes")
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
    image = validate_image(image)
    offset = transmission_offset(variant)
    return make_transmission_record_from_record(
        image[offset:offset + 4],
        mode,
    )


def make_transmission_record_from_record(raw: bytes, mode: str) -> bytes:
    """Return one checked record changing only transmission bits 0..1."""
    mode_bits = {"at": 1, "mt": 2}.get(mode.lower())
    if mode_bits is None:
        raise ValueError("transmission mode must be 'at' or 'mt'")
    current = decode_transmission_record(raw)
    if not current["check_ok"]:
        raise EepromError("transmission record currently has an invalid check word")
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


def repair_record_checks(image: bytes, variant: str, record_offsets) -> bytes:
    """Repair only explicitly selected checked records; never alter their payloads.

    This is an offline expert action: accepting stale physical payloads can
    replace the ECU's rejected-record defaults. Callers must review the exact
    selected records, not infer a blanket repair from an invalid-record report.
    """
    image = validate_image(image)
    checked = {field.offset: field for field in fields_for_variant(variant) if field.checked}
    if isinstance(record_offsets, (str, bytes, bytearray)):
        raise ValueError("select checked-record start offsets as integers")
    try:
        offsets = tuple(record_offsets)
    except TypeError as error:
        raise ValueError("select checked-record start offsets as integers") from error
    if not offsets:
        raise ValueError("select at least one checked record to repair")
    if any(type(offset) is not int for offset in offsets):
        raise ValueError("checked-record offsets must be integers, not booleans or converted values")
    if len(set(offsets)) != len(offsets):
        raise ValueError("selected checked-record offsets must not contain duplicates")
    if any(offset not in checked for offset in offsets):
        raise ValueError("each selected offset must be the start of a known checked record")
    target = bytearray(image)
    for offset in offsets:
        end = offset + checked[offset].length
        target[end - 2:end] = additive_check(image[offset:end - 2]).to_bytes(2, "little")
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


def _operating_time_state(image: bytes) -> tuple[int, bool, bool]:
    """The stock 16-bit three-copy vote, including wrap and zero fallback."""
    a, b, c = (_u16(image, offset) for offset in (0, 2, 4))
    if b == (a + 1) & 0xFFFF or c == (a + 2) & 0xFFFF:
        counter, valid = a, True
    elif c == (b + 1) & 0xFFFF:
        counter, valid = (b - 1) & 0xFFFF, True
    else:
        counter, valid = 0, False
    consistent = (a, b, c) == tuple((counter + index) & 0xFFFF for index in range(3))
    return counter, valid, consistent


def _fault_history_state(image: bytes, variant: str, records: dict) -> dict:
    occurrence = records["dtc_occurrence"]
    start = occurrence["offset"]
    count = image[start]
    identifiers = tuple(image[start + 1:start + 1 + min(count, 10)])
    codes = _FAULT_LAYOUTS[variant][1]
    warnings = []
    if count > 10:
        warnings.append(f"Saved fault-slot count {count} exceeds ten; only the first ten stored slots are shown.")
    if count and not occurrence["check_ok"]:
        warnings.append("The saved fault-occurrence record has an invalid check. Its slots are stale stored bytes, not restored ECU fault state.")
    if any(identifier >= len(codes) for identifier in identifiers):
        warnings.append(f"Some saved fault IDs are outside the {variant} descriptor table; their code and environment interpretation remain unknown.")
    if len(set(identifiers)) != len(identifiers):
        warnings.append("Saved fault IDs repeat. Each physical slot is shown separately; later copies can replace earlier ones on ECU load.")
    snapshot_reasons = []
    if variant in _FAULT_SNAPSHOTS:
        offsets = _FAULT_SNAPSHOTS[variant]
        associated = image[offsets["internal_id"]]
        if not records["fault_memory"]["check_ok"]:
            snapshot_reasons.append("invalid snapshot-record check")
        if not occurrence["check_ok"]:
            snapshot_reasons.append("invalid occurrence-record check")
        if not identifiers:
            snapshot_reasons.append("no saved fault slots")
        if not image[offsets["flags"]] & 0x10:
            snapshot_reasons.append("availability flag is clear")
        if associated != 0xFF and (associated >= len(codes) or associated not in identifiers):
            snapshot_reasons.append("associated fault ID is not a recognized saved slot")
        if image[offsets["flags"]] & 0x10 and snapshot_reasons:
            warnings.append("Saved freeze snapshot is unavailable: " + "; ".join(snapshot_reasons) + ". Raw bytes remain visible.")
    return {"count": count, "ids": identifiers, "warnings": warnings,
            "snapshot_available": variant in _FAULT_SNAPSHOTS and not snapshot_reasons,
            "snapshot_reasons": snapshot_reasons}


def decoded_values(image: bytes, variant: str = "MS41.3") -> dict:
    """Small, evidence-backed view; unresolved bytes stay in the raw field report."""
    image = validate_image(image)
    fields_for_variant(variant)
    layout = DECODE_LAYOUTS[variant]
    knock_stored_bytes = layout["knock_stored_bytes"]
    knock = image[0x00E:0x00E + knock_stored_bytes + 1]
    trims = [_u16(image, off) for off in layout["trims"]]
    tail = image[TAIL_START:]
    diagnostic_counters = layout["diagnostic_counters"]
    shutdown_coolant = layout["shutdown_coolant"]
    peak_rpm = layout["peak_rpm"]
    overrev = layout["overrev"]
    padded = image[layout["mirror_size"]:] == bytes(
        EEPROM_SIZE - layout["mirror_size"])
    counter, vote_valid, consistent = _operating_time_state(image)
    return {
        "looks_like_zero_padded_ram_mirror": padded,
        "cycle_sequence_base": _u16(image, 0x000),
        "operating_time_counter": counter,
        "operating_time_hours": counter * 0.1,
        "operating_time_vote_valid": vote_valid,
        "operating_time_sequence_consistent": consistent,
        "eeprom_save_count": int.from_bytes(image[0x006:0x00A], "little"),
        "knock_cells_neutral": all(
            value == (0 if variant == "MS41.1" else 0x80)
            for value in knock[:knock_stored_bytes]
        ),
        "knock_global_raw": knock[knock_stored_bytes],
        "tps_adaptation_raw": _u16(image, layout["tps"]),
        "tps_adaptation_percent_if_logger_scale": (
            _u16(image, layout["tps"]) * 0.001526
        ),
        "idle_trim_1_ms": (trims[0] - 32768) * 0.00534,
        "ltft_1_percent": (trims[1] - 32768) * 100 / 65535,
        "idle_trim_2_ms": (trims[2] - 32768) * 0.00534,
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
        "tail_progression": None if padded else list(tail[:3]),
        "tail_descriptor": None if padded else tail[6:18].decode("ascii", "replace"),
        "tail_dme_part_numbers": None if padded else [
            tail[18:25].decode("ascii", "replace"),
            tail[25:32].decode("ascii", "replace"),
        ],
    }


def decoded_fields(image: bytes, variant: str = "MS41.3") -> list[dict]:
    """Family-aware offline values, with explicit advanced gates on stored state.

    Offsets and units come from the native EEPROM producer/consumer map in
    docs/MS41_EEPROM.md. A field's check describes the stored record, not the
    live ECU value: rejected records can be replaced with defaults at startup.
    """
    image = validate_image(image)
    records = {row["key"]: row for row in field_report(image, variant)}
    layout = DECODE_LAYOUTS[variant]
    decoded = decoded_values(image, variant)
    homolog = "STATIC" if variant in ("MS41.2", "MS41.3") else "HOMOLOG"
    rows = []

    def add(field_id, label, category, offset, length, description, *,
            value=None, display=None, unit="", editable=False, kind="text",
            minimum=None, maximum=None, step=None, options=(), confidence="STATIC",
            requires_advanced=False, byteorder="little", bit_mask=None):
        record = next((record for record in records.values()
                       if record["offset"] <= offset
                       and offset + length <= record["offset"] + record["length"]), None)
        if display is None:
            display = (format(value, ".6f").rstrip("0").rstrip(".")
                       if isinstance(value, float) else str(value))
            if display == "-0":
                display = "0"
        rows.append({
            "id": field_id, "label": label, "category": category,
            "description": description, "unit": unit, "offset": offset,
            "length": length, "raw": image[offset:offset + length].hex(" ").upper(),
            "display": display, "value": value, "editable": editable,
            "kind": kind, "minimum": minimum, "maximum": maximum,
            "step": step, "options": list(options),
            "check_ok": record.get("check_ok") if record is not None else None,
            "confidence": confidence, "requires_advanced": requires_advanced,
        })
        if byteorder != "little":
            rows[-1]["byteorder"] = byteorder
        if bit_mask is not None:
            rows[-1]["bit_mask"] = bit_mask

    def number(field_id, label, category, offset, length, description, *,
               scale=1, zero=0, unit="", confidence="STATIC", signed=False,
               requires_advanced=True, editable=True, available=True, byteorder="little",
               stored_minimum=None, stored_maximum=None):
        raw = int.from_bytes(image[offset:offset + length], byteorder, signed=signed)
        raw_minimum = (-(1 << (length * 8 - 1)) if signed else 0) if stored_minimum is None else stored_minimum
        raw_maximum = ((1 << (length * 8 - int(signed))) - 1) if stored_maximum is None else stored_maximum
        add(field_id, label, category, offset, length, description,
            value=(raw - zero) * scale if available else None,
            display=None if available else "Unavailable (raw bytes retained)",
            unit=unit, editable=editable, kind="number",
            minimum=(raw_minimum - zero) * scale, maximum=(raw_maximum - zero) * scale,
            step=scale, confidence=confidence,
            requires_advanced=requires_advanced and editable, byteorder=byteorder)

    transmission = decoded["transmission"]
    mode = {1: "at", 2: "mt"}.get(transmission["mode_bits"])
    add("transmission", "Transmission", "coding", layout["transmission"], 2,
        "Persisted transmission mode (bits 0 and 1). All other coding bits are "
        "preserved. Used at startup only when the calibration selector's low six "
        "bits are 0x2C; changing this is not a complete transmission conversion.",
        value=mode, display={"at": "Automatic", "mt": "Manual"}.get(
            mode, f"Unknown (bits {transmission['mode_bits']})"),
        editable=True, kind="choice", options=(
            {"value": "at", "label": "Automatic"},
            {"value": "mt", "label": "Manual"},
        ))
    for field_id, label, offset, scale, unit in zip(
        ("idle_trim_1_ms", "ltft_1_percent", "idle_trim_2_ms", "ltft_2_percent"),
        ("Idle fuel trim 1", "Long-term fuel trim 1", "Idle fuel trim 2", "Long-term fuel trim 2"),
        layout["trims"], (0.00534, 100 / 65535, 0.00534, 100 / 65535),
        ("ms", "%", "ms", "%"),
    ):
        number(field_id, label, "fuel", offset, 2,
               "Stored learned fuel adaptation, not a calibration target. "
               "0x8000 is neutral; input is rounded to the nearest stored increment.",
               scale=scale, zero=32768, unit=unit, requires_advanced=False, confidence=homolog)
    fuel = records["fuel_adaptations"]
    fuel_base = fuel["offset"]
    number("co_alignment_percent", "Stored CO alignment", "fuel", fuel_base, 2,
           "Centered stored CO-alignment state; 0x8000 is neutral. The OEM diagnostic reads "
           "the high byte as signed steps and writes integral 0x100-count steps, while this "
           "offline field retains the complete fractional state.",
           scale=100 / 65536, zero=32768, unit="%",
           confidence="STATIC" if variant == "MS41.0" else "HOMOLOG")
    if variant != "MS41.0":
        if variant == "MS41.1":
            for bank, offset in ((1, fuel_base + 10), (2, fuel_base + 12)):
                number(f"fuel_bank_{bank}_window_state_raw",
                       f"Bank {bank} upstream O2 monitor retained index", "fuel", offset, 2,
                       "Retained upstream-O2 monitor index/state, valid from 0 through the stock "
                       "calibration limit 30. 0xFFFF means invalid or uninitialized. The exact BMW "
                       "short name is unresolved; this is not a fuel trim, voltage, or time value.",
                       unit="internal index", confidence="STATIC")
            cursor, width = fuel_base + 14, 1
        else:
            cursor, width = fuel_base + 10, 2
        for key, label, count, faults in (
            ("lambda_switch", "Upstream O2 regulation-frequency metric", 2, "E5/E6"),
            ("precat_lambda_switch", "Upstream O2 transition-time metric", 4, "E7/E8"),
        ):
            for average in range(1, count + 1):
                for bank in (1, 2):
                    number(f"fuel_bank_{bank}_{key}_average_{average}_raw",
                           f"Bank {bank} {label.lower()} average {average}", "fuel", cursor, width,
                           f"Unsigned cached {faults} diagnostic metric produced by dividing an "
                           "accumulator by its calibration window. No Hz or ms conversion is proven; "
                           "this is upstream-O2 monitor history, not a tuning parameter.",
                           unit="internal count", confidence="STATIC")
                    cursor += width
        for state, threshold in ((1, "upper"), (2, "lower")):
            for bank in (1, 2):
                number(f"fuel_bank_{bank}_lambda_monitor_state_{state}_raw",
                       f"Bank {bank} learned {threshold} O2 switching threshold", "fuel", cursor, 1,
                       f"Learned {threshold} switching-voltage threshold for the upstream O2 monitor. "
                       "Firmware closes the voltage-domain role, but not an exact volts-per-count "
                       "conversion for this archived byte.",
                       unit="ADC count", confidence="STATIC")
                cursor += 1
    for index in range(KNOCK_LOGICAL_CELLS):
        rpm_row, load_column = divmod(index, 4)
        label = f"Knock correction R{rpm_row + 1} C{load_column + 1}"
        if variant == "MS41.1":
            # The .1 record holds two averaged logical cells in each stored byte.
            # Load expands nibble n to raw 128 - 2*n; degrees are therefore -0.75*n.
            offset = 0x00E + index // 2
            nibble = (image[offset] >> ((index % 2) * 4)) & 0x0F
            add(f"knock_cell_{index}", label, "knock", offset, 1,
                "Averaged persisted knock correction at this RPM-row/load-column position, "
                "packed as one nibble of this byte. "
                "0 is neutral; each increment is −0.75°. Editing preserves the paired cell. "
                "The EEPROM stores the learned cells, not the calibration axis breakpoints.",
                value=-0.75 * nibble, unit="°", editable=True, kind="number",
                minimum=-11.25, maximum=0.0, step=0.75)
        else:
            number(f"knock_cell_{index}", label, "knock", 0x00E + index, 1,
                   "Averaged persisted ignition correction, not an individual cylinder map. "
                   "0x80 is neutral; stored values above neutral are clamped to zero correction on ECU load. "
                   "Cells are row-major across 16 RPM rows and four load columns; the EEPROM does not "
                   "store the calibration axis breakpoints.",
                   scale=0.375, zero=128, unit="°", requires_advanced=False, confidence=homolog)
    number("knock_global", "Overall knock correction", "knock", 0x00E + layout["knock_stored_bytes"], 1,
           "Stored overall learned ignition correction. 0x80 is neutral. "
           "The loader clamps positive stored corrections to neutral on every supported family.",
           scale=0.375, zero=128, unit="°", requires_advanced=False, confidence=homolog)

    add("operating_time_hours", "Operating time (counter)", "history", 0, 6,
        "Voted operating-time counter, 0.1 hour per tick, wrapping after 65535 ticks. "
        "This is nominal modulo time, not an odometer or lifetime total. One edit writes "
        "all three little-endian words as n, n+1, n+2 with 16-bit wrap. A quantized no-op "
        "preserves even an inconsistent sequence; no implicit redundancy repair occurs.",
        value=decoded["operating_time_hours"],
        display=None if decoded["operating_time_vote_valid"] else "0 (fallback: invalid sequence)",
        unit="h", editable=True, requires_advanced=True, kind="number",
        minimum=0, maximum=65535 * 0.1, step=0.1)
    for index in range(3):
        number(f"cycle_sequence_{index}", f"Operating-time storage word {index + 1}",
               "history", index * 2, 2,
               "Physical redundant counter word, normally n, n+1 or n+2 modulo 65536. "
               "Edit the logical operating-time field to update the three-copy representation together.",
               unit="raw", editable=False)
    number("eeprom_save_count", "EEPROM save count", "history", 0x006, 4,
           "Little-endian count incremented before EEPROM saves.", unit="saves")
    number("identity_gate_value", "Adaptation compatibility key", "identification", 0x00A, 2,
           "Raw firmware compatibility key, not VIN or ISN. Changing it can cause the "
           "ECU to discard other saved adaptations at startup.", unit="raw")
    nonce = records["output_test_nonce"]
    for index in range(3):
        number(f"output_test_sequence_{index}", f"Actuator-test progression byte {index + 1}",
               "history", nonce["offset"] + index, 1,
               "One byte of the three-byte actuator-test progression/anti-replay state. "
               "The other progression bytes and reserved pad are not rewritten automatically.", unit="raw")
    number("dtc_occurrence_count", "Saved fault-slot count", "faults",
           layout["dtc_occurrence"], 1,
           "Stored slot count, not the number of currently active faults. The loader caps "
           "this at ten. Increasing it exposes retained slot bytes; it does not initialize them.",
           unit="count")
    if layout["diagnostic_counters"] is not None:
        definitions = (_DIAGNOSTIC_COUNTERS_MS411 if variant == "MS41.1"
                       else _DIAGNOSTIC_COUNTERS_MS412)
        for index in range(7):
            definition = definitions[index]
            label, description = (definition if definition is not None else (
                f"Diagnostic counter {index + 1} (no canonical increment writer)",
                "This byte is loaded, saved, and cleared with the diagnostic-counter record, "
                "but the canonical MS41.1 program has no direct increment writer. A later "
                "family's meaning is not projected onto it."))
            number(f"diagnostic_counter_{index}", label,
                   "diagnostic", layout["diagnostic_counters"] + index, 1,
                   description + " The byte wraps from 255 to 0; it is not saturating, "
                   "elapsed time, or an operating-hours value. DS2 service 0x89/1 clears "
                   "all seven counters.", unit="count",
                   confidence=("UNRESOLVED" if definition is None else
                               "HOMOLOG" if variant == "MS41.3" else "STATIC"))
    if layout["shutdown_coolant"] is not None:
        offset = layout["shutdown_coolant"]
        raw = image[offset]
        add("last_shutdown_ect_celsius", "Coolant at last shutdown", "history", offset, 1,
            "Stored coolant-temperature snapshot (raw × 0.747 − 48), not live coolant temperature.",
            value=raw * 0.747 - 48, unit="°C", kind="number", confidence=homolog,
            minimum=-48, maximum=255 * 0.747 - 48, step=0.747, editable=True, requires_advanced=True)
        number("warm_restart_count", "Consecutive warm restarts", "history", offset + 1, 1,
               "Stored warm-restart/no-cooldown count.", unit="count", confidence=homolog)
    if layout["peak_rpm"] is not None:
        number("peak_rpm", "Peak engine speed", "history", layout["peak_rpm"], 1,
               "Persisted peak engine-speed sample, stored in 32 RPM increments.", scale=32, unit="RPM")
    if layout["overrev"] is not None:
        number("overrev_event_count", "Qualified over-rev events", "history", layout["overrev"], 1,
               "Persistent event count. The over-rev label is inferred from the traced "
               "qualification logic, not a recovered BMW factory name.", unit="count", confidence="INFERRED")

    load = records["load_model_correction"]
    load_scale = 5.46850393700787
    load_description = (
        "Signed filtered difference between throttle-model load and measured/filtered load. "
        "Positive values raise corrected load; negative values lower it. The engineering "
        "projection comes from the ID41 DAMOS load-count scale; the exact stored representation "
        "is signed Q8.8 state, not a calibration or altitude value."
    )
    if variant == "MS41.0":
        number("load_model_correction", "Learned load-model correction", "adaptation",
               load["offset"] + 1, 1,
               load_description + " Only this signed high byte is restored at startup.",
               scale=load_scale, unit="mg/stroke", signed=True, confidence="STATIC")
        number("load_model_fraction_not_restored_raw", "Saved load-model fractional byte", "adaptation",
               load["offset"], 1,
               "The saver and record check retain this low byte, but MS41.0 startup does not restore it. "
               "It therefore has no startup-effective engineering edit and remains explicit raw state.",
               unit="raw", confidence="STATIC")
    else:
        number("load_model_correction", "Learned load-model correction", "adaptation",
               load["offset"], 2, load_description,
               scale=load_scale / 256, unit="mg/stroke", signed=True,
               stored_maximum=0x7F00, confidence="HOMOLOG")

    # Retain the legacy serialized IDs even though exact traces corrected their old labels.
    coolant = records["coolant_latch"]
    coolant_raw = image[coolant["offset"]]
    add("coolant_latch", coolant["label"], "history", coolant["offset"], 1,
        "Saved ECT reference captured while the repeat-start timer is active. At the next start, "
        "firmware subtracts a calibrated allowed temperature drop and compares current ECT with "
        "that threshold. 0xFF means unavailable, expired, or rejected; this is neither live coolant "
        "nor a status word.",
        value=(coolant_raw - 64) * 0.75 if coolant_raw != 0xFF else None,
        display=None if coolant_raw != 0xFF else "Not available (0xFF)",
        unit="°C", editable=True, kind="number", minimum=-48, maximum=142.5,
        step=0.75, requires_advanced=True, options=(
            {"value": "raw:FF", "label": "Set Not available (0xFF)"},
        ))
    number("coolant_latch_reserved_raw", "Coolant-reference unresolved checked byte", "history",
           coolant["offset"] + 1, 1,
           "Second checked payload byte. Exact canonical paths preserve it, but no independent "
           "producer or consumer meaning is established.", unit="raw", confidence="UNRESOLVED")
    if "load_collective" in records:
        warmup = records["load_collective"]
        number("load_collective", warmup["label"], "history", warmup["offset"], 1,
               "Persistent saturating byte: cold starts add coolant-indexed counts and qualified "
               "warm-up/shutdown events subtract counts. Stock firmware sets a diagnostic/monitor "
               "gate only above 90 counts. It is not engine load, elapsed time, or a physical quantity.",
               unit="internal counts", confidence="STATIC")
        number("load_collective_reserved_raw", "Warm-up-history unresolved checked byte", "history",
               warmup["offset"] + 1, 1,
               "Second checked payload byte. Exact canonical paths preserve it, but no independent "
               "producer or consumer meaning is established.", unit="raw", confidence="UNRESOLVED")
    record = records["engine_roughness_segment_adaptation"]
    for index in range(5):
        # Retain legacy serialized IDs; exact consumers disprove the old roughness label.
        number(f"roughness_segment_{index}", f"Relative ignition/dwell gain {index + 1}",
               "adaptation", record["offset"] + index, 1,
               "Stored relative ignition/dwell-control gain, raw / 128; 0x80 is neutral (1×). "
               "These are learned array indices 1–5 relative to index 0, not verified physical "
               "cylinder numbers or crankshaft-wheel corrections. No mA or ms unit is implied.",
               scale=1 / 128, unit="×", confidence="STATIC")
    record = records["vanos_adaptation"]
    number("vanos_reference_degrees", "Learned VANOS reference", "adaptation", record["offset"], 2,
           "Stored Q8.8 learned reference: raw × 0.375 / 256 crankshaft degrees. "
           "MS41.0 DAMOS nw_ini/nw_hys_ini and corresponding family firmware "
           "load/filter/save paths establish this scale; this is not live VANOS position.",
           scale=0.375 / 256, unit="° crank", confidence="STATIC")
    learned = image[record["offset"] + 2]
    add("vanos_learned_state", "VANOS reference state", "adaptation", record["offset"] + 2, 1,
        "State 1 selects learned-reference hysteresis; 0 selects default. Other values "
        "have no verified friendly interpretation.", value=str(learned) if learned in (0, 1) else None,
        display={0: "Default", 1: "Learned"}.get(learned, f"Unknown ({learned})"),
        editable=True, requires_advanced=True, kind="choice", options=(
            {"value": "0", "label": "Default"}, {"value": "1", "label": "Learned"},
        ))
    number("tps_baseline_degrees", "Learned closed-throttle reference", "adaptation", layout["tps"], 2,
           "Stored Q8.8 closed-throttle baseline, not live throttle opening. "
           "The DAMOS dk_max_ll/dk_add scale and the corresponding startup clamps "
           "and filters give raw × 0.46862745098039 / 256 throttle degrees.",
           scale=0.46862745098039 / 256, unit="° throttle", confidence="STATIC")
    record = records["idle_regulator_adaptation"]
    number("idle_air_factor", "Learned idle-air multiplier", "adaptation", record["offset"], 1,
           "Stored multiplicative idle-controller factor: raw / 128. "
           "0x80 is neutral (1×); this is not an actuator duty percentage.",
           scale=1 / 128, unit="×", confidence="STATIC")
    for index, relative in enumerate((2, 4, 6)):
        number(f"idle_air_correction_{index}",
               {2: "Idle-air correction (drive disengaged)",
                4: "Idle-air correction (drive engaged)",
                6: "Stored A/C idle-air correction"}[relative],
               "adaptation", record["offset"] + relative, 2,
               "Signed 16-bit learned idle-air correction, neutral 0, scaled by 100/65536. "
               + ("The saved A/C value already includes a calibration-dependent factor; "
                  "it is not the current live correction." if relative == 6 else
                  "Selected by the ECU drive-state signal, not by cylinder bank. "
                  "DAMOS bounds and equivalent family filter/save paths establish this percent domain."),
               scale=100 / 65536, unit="%", confidence="STATIC", signed=True)
    # Keep the legacy ID; the firmware copies only this byte, not the pad at +9.
    number("idle_speed_command_raw", "Programmed idle-speed addition", "adaptation", record["offset"] + 8, 1,
           "Stored service/diagnostic idle-speed addition, one RPM per count. "
           "The ECU applies coolant-dependent weighting before adding it to the idle target; "
           "this is not the final idle speed. The diagnostic acceptance ceiling depends "
           "on the calibration. The following padding byte is not part of this command.", unit="RPM")

    if "rough_running" in records:
        record = records["rough_running"]
        base = record["offset"]
        complete = image[base + 28] != 0
        add("rough_running", "Rough-running / load-correction learning", "adaptation", base, 30,
            "Checked learned state: one 32-bit event counter, six logical-slot "
            "counters, five relative correction words, one common reference/accumulator "
            "word, a completion byte and one reserved byte. Firmware tables establish firing "
            "order 1-5-3-6-2-4; cylinder 1 is the zero/reference, so only the other five signed "
            "relative corrections are stored. Their multiplier path establishes a percent scale. "
            "Stored values remain available through Allow advanced edits.",
            display=("Complete" if complete else "Incomplete") + " · firing order 1-5-3-6-2-4")
        number("rough_running_event_count", "Learning event counter", "adaptation", base, 4,
               "Unsigned 32-bit counter saved from the rough-running learner. Its exact "
               "event cadence is not a time unit.", unit="count")
        for index, cylinder in enumerate((1, 5, 3, 6, 2, 4)):
            number(f"rough_running_slot_{index}_count", f"Cylinder {cylinder} event count",
                   "adaptation", base + 4 + index * 2, 2,
                   f"Unsigned learner counter for cylinder {cylinder}, stored at firing-order "
                   f"position {index + 1} in the firmware's 1-5-3-6-2-4 sequence.",
                   unit="count")
            if index:
                offset = base + 14 + index * 2
                number(f"rough_running_slot_{index}_correction_raw",
                       f"Cylinder {cylinder} correction relative to cylinder 1", "adaptation", offset, 2,
                       "Signed relative correction. Cylinder 1 is the zero/reference and has no stored word. "
                       "The runtime multiplier path establishes raw × 100 / 2^22 percent; positive values reduce "
                       "the corrected base and negative values increase it. Diagnostic raw >> 8 is only a coarser "
                       "export of the same stored state.",
                       scale=100 / (1 << 22), unit="% relative", signed=True, confidence="STATIC")
        offset = base + 26
        number("rough_running_reference_accumulator_raw", "Learning convergence countdown",
               "adaptation", offset, 2,
               "Unsigned common learning counter initialized from a calibration times 24 and driven toward zero. "
               "It is not a sixth cylinder correction or a time unit.",
               unit="count", confidence="STATIC")
        state = image[base + 28]
        add("rough_running_completion", "Learning completion state", "adaptation", base + 28, 1,
            "Saved FD52.1 learned/valid flag. The stock producer writes only 0 or 1.",
            value=str(state) if state in (0, 1) else None,
            display={0: "Incomplete", 1: "Complete"}.get(state, f"Unknown ({state})"),
            editable=True, requires_advanced=True, kind="choice", options=(
                {"value": "0", "label": "Incomplete"}, {"value": "1", "label": "Complete"},
            ))
        number("rough_running_reserved", "Rough-running reserved byte", "adaptation", base + 29, 1,
               "No individual producer or physical meaning is established; retained as raw data.",
               unit="raw", confidence="UNRESOLVED")

    # Archived slots retain physical order and duplicates; the live parser is
    # called one slot at a time only for its canonical code/name/record contract.
    fault_state = _fault_history_state(image, variant, records)
    stride, codes, environments, missing_qualifiers = _FAULT_LAYOUTS[variant]
    code_options = tuple({"value": str(index), "label": f"{code:03d} — {dtc.DS2DTCRecord(code, 0, b'').description}"}
                         for index, code in enumerate(codes))

    def flag(field_id, label, offset, mask, description, *, off="No", on="Yes", confidence="STATIC"):
        enabled = bool(image[offset] & mask)
        add(field_id, label, "faults", offset, 1, description,
            value="1" if enabled else "0", display=on if enabled else off,
            editable=True, requires_advanced=True, kind="choice", bit_mask=mask,
            options=({"value": "0", "label": off}, {"value": "1", "label": on}),
            confidence=confidence)

    occurrence_start = records["dtc_occurrence"]["offset"]
    management_first_slots = {}

    def add_ms412_management_record(prefix, record_index, identifier, *, shared_with=None):
        offset = 0x152 + record_index * 6
        raw = image[offset:offset + 6]
        code = codes[identifier]
        name = f"{code:03d} — {dtc.DS2DTCRecord(code, 0, b'').description}"
        confidence = "STATIC" if variant == "MS41.2" else "HOMOLOG"
        if shared_with is not None:
            add(f"{prefix}_management_reference", "Shared persistent fault envelope",
                "faults", offset, 6,
                "This internal fault ID uses one fixed management record, not one record per "
                "saved occurrence. Editing the first matching card updates the shared bytes.",
                display=f"Same record as saved fault {shared_with + 1}", confidence=confidence)
            return
        summary_id = prefix if prefix.startswith("fault_management_") else f"{prefix}_management"
        add(summary_id, f"Persistent fault envelope: {name}", "faults", offset, 6,
            "Dedicated MS41.2 fault-management record selected by internal fault ID, not by "
            "occurrence-slot number. It retains the observed RPM/load envelope and delayed "
            "recovery state. Flags 0x10/0x20 select mutually exclusive internal source/mode "
            "paths, but their human meaning and all other sibling bits remain unresolved. "
            "MS41.3 uses the homologous MS41.2 layout.",
            display=(f"{raw[0] * 32}–{raw[2] * 32} RPM · "
                     f"{raw[1] * 5.4470588235:.1f}–{raw[3] * 5.4470588235:.1f} mg/stroke · "
                     f"flags 0x{raw[4]:02X} · countdown {raw[5]}"), confidence=confidence)
        for suffix, label, byte_offset, scale, unit in (
            ("min_rpm", "Minimum engine speed", 0, 32, "RPM"),
            ("min_load", "Minimum filtered load", 1, 5.4470588235, "mg/stroke"),
            ("max_rpm", "Maximum engine speed", 2, 32, "RPM"),
            ("max_load", "Maximum filtered load", 3, 5.4470588235, "mg/stroke"),
        ):
            number(f"{prefix}_management_{suffix}", label, "faults", offset + byte_offset, 1,
                   "Observed envelope boundary retained by the dedicated fault-management path. "
                   "This is archived diagnostic state, not a live reading or tune breakpoint.",
                   scale=scale, unit=unit, confidence=confidence)
        progression = raw[4] & 0x03
        progression_options = (
            {"value": "0", "label": "Clear"},
            {"value": "1", "label": "Progression bit 0 only"},
            {"value": "2", "label": "Delayed selector only (partial)"},
            {"value": "3", "label": "Progression active · delayed selected"},
        )
        add(f"{prefix}_management_progression", "Management progression state", "faults",
            offset + 4, 1,
            "Masked low two behavior bits: bit 0 is progression/validity state and bit 1 selects "
            "delayed secondary progression. These neutral labels do not claim a recovered BMW "
            "enum; every upper sibling bit is preserved.",
            value=str(progression), display=progression_options[progression]["label"],
            editable=True, requires_advanced=True, kind="choice", bit_mask=0x03,
            options=progression_options, confidence=confidence)
        number(f"{prefix}_management_countdown", "Recovery/delay countdown", "faults",
               offset + 5, 1,
               "Firmware seeds this byte with 0x50 and decrements it toward zero. No seconds, "
               "cycles, or distance conversion is established.",
               unit="internal counts", confidence=confidence)

    def add_ms411_management_record(prefix, group, record_index):
        record = _ms411_management_record(image, group, record_index)
        identifier = record["identifier"]
        code = codes[identifier]
        name = f"{code:03d} — {dtc.DS2DTCRecord(code, 0, b'').description}"
        token = f"{group}{record_index}"
        ordinal = sum(len(definition[0]) for key, definition in
                      _FAULT_MANAGEMENT_GROUPS_MS411.items() if key < group) + record_index
        base_id = (f"{prefix}_management_{token}" if prefix is not None
                   else f"fault_management_{ordinal}")
        child_id = lambda suffix: (f"{base_id}_{suffix}" if prefix is not None
                                   else f"{base_id}_{token}_{suffix}")
        values = record["values"]
        offsets = record["offsets"]
        role, role_description = _FAULT_MANAGEMENT_GROUP_ROLES_MS411[group]
        add(base_id, f"Packed persistent {role} envelope: {name}", "faults",
            offsets[0], 1,
            "One of 22 MS41.1 virtual six-byte records reconstructed from parallel packed "
            f"arrays. This is group {group}: {role_description} The four envelope values "
            "retain only six bits each, so load reconstructs the canonical midpoint raw "
            "value 2 modulo 4. Catalyst-damage/protection and emissions-relevance wording "
            "remain inferred rather than recovered supplier names.",
            display=(f"{values[0] * 32}–{values[2] * 32} RPM · "
                     f"{values[1] * 5.4470588235:.1f}–"
                     f"{values[3] * 5.4470588235:.1f} mg/stroke · "
                     f"flags 0x{record['flags']:02X} · countdown {record['countdown']}"))
        component_sources = (
            (offsets[0],), (offsets[0], offsets[1]),
            (offsets[1], offsets[2]), (offsets[2],),
        )
        for component, (suffix, label, scale, unit) in enumerate((
            ("min_rpm", "Minimum engine speed", 32, "RPM"),
            ("min_load", "Minimum filtered load", 5.4470588235, "mg/stroke"),
            ("max_rpm", "Maximum engine speed", 32, "RPM"),
            ("max_load", "Maximum filtered load", 5.4470588235, "mg/stroke"),
        )):
            sources = component_sources[component]
            source_text = "/".join(f"0x{offset:03X}" for offset in sources)
            add(child_id(suffix), label, "faults", sources[0], 1,
                "Lossy six-bit archived envelope boundary, not a live reading or tune "
                f"breakpoint. Packed source byte(s): {source_text}. Editing performs one "
                "masked repack and preserves every neighboring packed value. The original "
                "two low bits were discarded by stock save and cannot be recovered.",
                value=values[component] * scale, unit=unit, editable=True, kind="number",
                minimum=2 * scale, maximum=254 * scale, step=4 * scale,
                requires_advanced=True)
        flags = record["flags"]
        stage = flags & 0x03
        add(child_id("progression"), "Management progression stage", "faults",
            offsets[3], 1,
            "MS41.1 low-two-bit progression stage. Values 0 through 3 are behavior stages, "
            "not recovered BMW enum names. Every sibling flag is preserved.",
            value=str(stage), display=f"Stage {stage}", editable=True,
            requires_advanced=True, kind="choice", bit_mask=0x03,
            options=tuple({"value": str(value), "label": f"Stage {value}"}
                          for value in range(4)))
        terminal = (flags & 0x0C) >> 2
        add(child_id("terminal_latch"), "Management terminal-stage latch", "faults",
            offsets[3], 1,
            "MS41.1 0x0C terminal-stage pair. The stock path sets the pair at terminal "
            "progression; partial 0x04/0x08 patterns remain visible without invented names.",
            value=str(terminal) if terminal in (0, 3) else None,
            display={0: "Not latched", 3: "Latched"}.get(
                terminal, f"Partial raw pattern {terminal}"),
            editable=True, requires_advanced=True, kind="choice", bit_mask=0x0C,
            options=({"value": "0", "label": "Not latched"},
                     {"value": "3", "label": "Latched (0x0C)"}))
        flag(child_id("valid"), "Management record valid", offsets[3], 0x40,
             "MS41.1 record-valid behavior bit. This neutral validity label is not a "
             "projection of later-family source/mode flags.", off="Invalid", on="Valid")
        add(child_id("flags_raw"), "Management flags (raw)", "faults", offsets[3], 1,
            "Exact MS41.1 flag byte. Only progression bits 0x03, terminal pair 0x0C, and "
            "validity bit 0x40 have named controls; every other bit remains unresolved.",
            display=f"0x{flags:02X}", confidence="UNRESOLVED")
        number(child_id("countdown"), "Management countdown", "faults", offsets[4], 1,
               "Direct MS41.1 runtime countdown byte retained by this packed record. No "
               "seconds, cycle, or distance conversion is established.",
               unit="internal counts", confidence="STATIC")

    for index, identifier in enumerate(fault_state["ids"]):
        prefix = f"fault_slot_{index}"
        offset = occurrence_start + 11 + stride * index
        raw = image[offset:offset + stride]
        code = codes[identifier] if identifier < len(codes) else None
        native = (dtc.parse_ds2_dtc_response(
            b"\x01" + bytes((code, raw[0])) + raw[2:10], variant=variant)[0]
            if code is not None else None)
        name = (f"{native.code_hex} — {native.description}" if native else
                f"Unknown internal ID 0x{identifier:02X}")
        flags = dtc.SAVED_STATUS_FLAGS + ((0x08, "Plausibility / out-of-range"),) + (
            dtc.FAULT_QUALIFIER_FLAGS if code is not None and code not in missing_qualifiers else ())
        status = [label for mask, label in flags if raw[0] & mask]
        if not raw[0] & 0x40:
            status.append("Not present at save")
        if not raw[0] & 0x80:
            status.append("Static")
        stale = "Stale / invalid record; " if not records["dtc_occurrence"]["check_ok"] else ""
        add(prefix, f"Saved fault {index + 1}: {name}", "faults", offset, stride,
            "Archived occurrence slot, not a current live fault. The selected family determines "
            "its physical stride and internal-ID lookup. Repeated IDs remain separate. "
            "A failed record check means the stock loader does not restore these bytes.",
            display=stale + "; ".join(status) + f"; frequency {raw[2]}")
        number(f"{prefix}_frequency", "Saved occurrence frequency", "faults", offset + 2, 1,
               "Stored occurrence count; firmware saturates this byte at 255.", unit="count")
        unresolved_environments = []
        for environment_index, environment_id in enumerate(
                environments[identifier] if code is not None else (0, 0, 0, 0)):
            if ((code == 100 or code in _IGNITION_COIL_CODES)
                    and environment_index in (2, 3)):
                continue  # A family/code-specific word occupies these two bytes.
            field_id = f"{prefix}_env_{environment_index}"
            definition = _fault_environment_definition(environment_id)
            if definition is None:
                unresolved_environments.append(environment_index)
            else:
                label, unit, scale, bias = definition
                number(field_id, f"{label} at save", "faults", offset + 4 + environment_index, 1,
                       "Archived environment value. The exact family descriptor's source pointer "
                       "and its diagnostic scaling establish this conversion; it is not a live reading.",
                       scale=scale, zero=-bias / scale, unit=unit)
        if code == 100:
            add(f"{prefix}_self_test_reason", "Saved self-test reason", "faults", offset + 6, 2,
                "Little-endian reason word shared with the native self-test diagnostic record. "
                "Individual reason bits are not decoded here; this is not two sensor readings.",
                display=native.self_test_reason, confidence="STATIC")
        elif variant in ("MS41.0", "MS41.1") and code in _IGNITION_COIL_CODES:
            number(f"{prefix}_spark_burn_duration_ms", "Spark-burn duration at save", "faults",
                   offset + 6, 2,
                   "Big-endian per-cylinder gated ignition-feedback timer. The stock diagnostic "
                   "path compares it with the minimum spark-burn map; MS41.1 uses the exact MS41.0 "
                   "timer contract and homologous engineering scale.",
                   scale=0.00534004716564, unit="ms", byteorder="big",
                   confidence="STATIC" if variant == "MS41.0" else "HOMOLOG")
        elif code in _IGNITION_COIL_CODES:
            number(f"{prefix}_rough_running_metric", "Per-cylinder rough-running metric at save",
                   "faults", offset + 6, 2,
                   "Big-endian 16-bit gated-timer value used by the exact later-family per-cylinder "
                   "rough-running diagnostic. Its physical time scale remains unresolved.",
                   unit="internal counts", byteorder="big")
        number(f"{prefix}_operating_time_hours", "Operating-time counter at save", "faults", offset + 8, 2,
               "Big-endian archived operating-time counter, 0.1 hour per tick with 16-bit wrap. "
               "This is the counter at save, not hours ago or a lifetime total.",
               scale=0.1, unit="h", byteorder="big")
        number(f"{prefix}_logistics", "Saved logistics counter", "faults", offset + 3, 1,
               "Fault-management aging/logistics count, not elapsed hours. "
               "The generic fault producer resets this byte to 40.", unit="count")
        add(f"{prefix}_internal_id", "Associated fault", "faults", occurrence_start + 1 + index, 1,
            "Family-specific internal ID, not the public fault number. Changing the ID preserves "
            "all slot bytes and can change their environment interpretation; it does not create a new snapshot.",
            value=str(identifier) if code is not None else None, display=name,
            editable=True, requires_advanced=True, kind="choice", options=code_options)
        add(f"{prefix}_status", "Status at save", "faults", offset, 1,
            "Saved fault-management flags, not live status. Unsupported lower qualifiers remain "
            "raw. Editing a named flag preserves every other bit.",
            display=f"0x{raw[0]:02X} — " + "; ".join(status))
        flag_names = {0x20: "stored", 0x40: "present", 0x80: "sporadic", 0x10: "emissions",
                      0x08: "plausibility", 0x01: "battery", 0x02: "ground", 0x04: "open"}
        for mask, label in flags:
            off, on = {0x40: ("Not present at save", "Present at save"),
                       0x80: ("Static", "Sporadic")}.get(mask, ("No", "Yes"))
            flag(f"{prefix}_status_{flag_names[mask]}", label, offset, mask,
                 "One named saved-status bit. This describes the archived state, not a live "
                 "fault assertion. All other bits and unrelated slot bytes are preserved.", off=off, on=on)
        number(f"{prefix}_raw_debounce", "Fault debounce accumulator", "faults", offset + 1, 1,
               "Saturating fault-manager accumulator: qualifying events increment it and healthy "
               "events decrement it toward zero. Thresholds are caller-specific, so it has no "
               "time conversion.", unit="internal counts", confidence="STATIC")
        for environment_index in unresolved_environments:
            add(f"{prefix}_env_{environment_index}", f"Environment byte {environment_index + 1} (raw)", "faults",
                offset + 4 + environment_index, 1,
                "Saved environment byte. This exact firmware source has no admitted physical "
                "conversion; per-code diagnostic labels are not assumed to match it.",
                display=f"0x{raw[4 + environment_index]:02X}", confidence="UNRESOLVED")
        if stride > 10:
            add(f"{prefix}_raw_extra", "Additional fault-management state", "faults", offset + 10, stride - 10,
                "Family-specific retained state omitted from command 04. Named child fields decode "
                "the proven behavior; unsupported sibling bits remain raw.",
                display=raw[10:].hex(" ").upper(), confidence="UNRESOLVED")
            state = raw[10]
            stage = state & 0x03
            add(f"{prefix}_secondary_stage", "Secondary progression stage", "faults", offset + 10, 1,
                "Low two behavior bits of the secondary fault manager. Values 0 through 3 are "
                "progression stages, not recovered BMW state names.",
                value=str(stage), display=f"Stage {stage}", editable=True, requires_advanced=True,
                kind="choice", bit_mask=0x03,
                options=tuple({"value": str(value), "label": f"Stage {value}"} for value in range(4)))
            terminal = (state & 0x0C) >> 2
            add(f"{prefix}_secondary_terminal_latch", "Secondary terminal-stage latch", "faults",
                offset + 10, 1,
                "The 0x0C pair is latched at terminal progression. Partial 0x04/0x08 patterns have "
                "no admitted friendly meaning and remain visible as such.",
                value=str(terminal) if terminal in (0, 3) else None,
                display={0: "Not latched", 3: "Latched"}.get(terminal, f"Partial raw pattern {terminal}"),
                editable=True, requires_advanced=True, kind="choice", bit_mask=0x0C,
                options=({"value": "0", "label": "Not latched"},
                         {"value": "3", "label": "Latched (0x0C)"}))
            for mask, key, label, off, on in (
                (0x10, "transition_handled", "Secondary transition handled", "Pending", "Handled"),
                (0x20, "delay_elapsed", "Secondary delay elapsed", "Not elapsed", "Elapsed"),
                (0x40, "delay_initialized", "Secondary delay initialized", "Not initialized", "Initialized"),
            ):
                flag(f"{prefix}_secondary_{key}", label, offset + 10, mask,
                     "Firmware behavior bit in the secondary fault-management state; this is not a "
                     "recovered BMW enum label.", off=off, on=on)
            if stride == 12:
                number(f"{prefix}_secondary_delay_countdown", "Secondary delay countdown", "faults",
                       offset + 11, 1,
                       "Caller-seeded secondary fault-management countdown. Qualifying events decrement "
                       "it to zero; no seconds or cycles conversion is established.",
                       unit="internal counts", confidence="STATIC")

        if variant in ("MS41.2", "MS41.3") and identifier in _FAULT_MANAGEMENT_IDS_MS412:
            record_index = _FAULT_MANAGEMENT_IDS_MS412.index(identifier)
            first_slot = management_first_slots.get(identifier)
            add_ms412_management_record(prefix, record_index, identifier, shared_with=first_slot)
            if first_slot is None:
                management_first_slots[identifier] = index
        elif variant == "MS41.1":
            matching = tuple(
                (group, record_index)
                for group, (identifiers, *_offsets) in _FAULT_MANAGEMENT_GROUPS_MS411.items()
                for record_index, managed_identifier in enumerate(identifiers)
                if managed_identifier == identifier
            )
            if matching:
                first_slot = management_first_slots.get(identifier)
                if first_slot is None:
                    for group, record_index in matching:
                        add_ms411_management_record(prefix, group, record_index)
                    management_first_slots[identifier] = index
                else:
                    shared = _ms411_management_record(image, *matching[0])
                    add(f"{prefix}_management_reference", "Shared packed fault envelopes",
                        "faults", shared["offsets"][0], 1,
                        "This internal fault ID uses fixed MS41.1 packed management records, "
                        "not one copy per saved occurrence. Editing the first matching card "
                        "updates their shared packed bytes.",
                        display=f"Same records as saved fault {first_slot + 1}")

    if variant in ("MS41.2", "MS41.3"):
        for record_index, identifier in enumerate(_FAULT_MANAGEMENT_IDS_MS412):
            if identifier not in management_first_slots:
                add_ms412_management_record(f"fault_management_{record_index}", record_index, identifier)
    elif variant == "MS41.1":
        for group, (identifiers, *_offsets) in _FAULT_MANAGEMENT_GROUPS_MS411.items():
            for record_index, identifier in enumerate(identifiers):
                if identifier not in management_first_slots:
                    add_ms411_management_record(None, group, record_index)

    if variant != "MS41.0":
        confidence = "HOMOLOG" if variant == "MS41.3" else "STATIC"
        qualification = 0x1B2 if variant == "MS41.1" else 0x1AB
        matrix = 0x122 if variant == "MS41.1" else 0x1AC
        number("fault_history_qualification_counter",
               "Fault-history/DTC-mask qualification counter", "faults", qualification, 1,
               "Saturating internal counter used before copying retained DTC-mask state. It is "
               "not a drive-cycle, time, distance, or fault-occurrence count.",
               unit="internal counts", confidence=confidence)
        add("fault_relation_matrix", "Cylinder fault-retention matrix (raw)", "faults",
            matrix, 6,
            "Six independent raw rows in firing order 1-5-3-6-2-4; the low six bit positions "
            "use the same order. Stock code restores and saves them, clears selected rows and "
            "columns, globally clears them, and consumes only their OR aggregate. No nonzero "
            "writer or per-cell consumer was found: 0 means clear/unset, while 1 is only a "
            "retained raw bit with unresolved provenance. Symmetry and diagonal meaning are "
            "not assumed; bits 6-7 remain reserved raw data.",
            display=("6×6 raw bit matrix · order 1-5-3-6-2-4 · "
                     + image[matrix:matrix + 6].hex(" ").upper()), confidence="STATIC")
        cylinder_order = (1, 5, 3, 6, 2, 4)
        for row_index, cylinder in enumerate(cylinder_order):
            raw = image[matrix + row_index]
            cells = " · ".join(
                f"{target}:{(raw >> bit) & 1}"
                for bit, target in enumerate(cylinder_order)
            )
            add(f"fault_relation_matrix_row_{cylinder}",
                f"Raw retention row — cylinder {cylinder}", "faults",
                matrix + row_index, 1,
                "Columns are cylinders 1-5-3-6-2-4. Each low-bit value is displayed exactly "
                "as stored: 0 is clear/unset and 1 is retained with unresolved meaning. "
                "The two high bits are shown separately and are not discarded.",
                display=f"{cells} · reserved 0x{raw & 0xC0:02X}", confidence="UNRESOLVED")

    if variant in _FAULT_SNAPSHOTS:
        offsets = _FAULT_SNAPSHOTS[variant]
        start = min(offsets.values())
        length = offsets["flags"] + 1 - start
        available = fault_state["snapshot_available"]
        reason = "; ".join(fault_state["snapshot_reasons"])
        add("fault_snapshot", "Saved freeze snapshot", "faults", start, length,
            "Archived snapshot, not live data. Availability requires valid occurrence and "
            "snapshot records, at least one saved slot, flag 0x10, and a matching associated "
            "fault ID or the special 0xFF marker. Missing snapshots are not zero readings.",
            display="Available archived snapshot" if available else "Unavailable — " + reason)
        for key, label, width, scale, zero, unit in (
            ("rpm", "Engine speed", 2, 1, 0, "RPM"),
            ("load_mg_stroke", "Engine load", 1, 5.4471, 0, "mg/stroke"),
            ("coolant_celsius", "Coolant temperature", 1, 0.747, 48 / 0.747, "°C"),
            ("speed_kmh", "Vehicle speed", 1, 1, 0, "km/h"),
            ("stft_1_percent", "Short-term fuel trim 1", 2, 100 / 65535, 32768, "%"),
            ("stft_2_percent", "Short-term fuel trim 2", 2, 100 / 65535, 32768, "%"),
            ("ltft_1_percent", "Long-term fuel trim 1", 2, 100 / 65535, 32768, "%"),
            ("ltft_2_percent", "Long-term fuel trim 2", 2, 100 / 65535, 32768, "%"),
        ):
            number(f"fault_snapshot_{key}", label + " at snapshot", "faults", offsets[key], width,
                   "Direct saved copy of the identified runtime value, little-endian in EEPROM. "
                   "Not a live reading or a calibration target. " + ("" if available else "Unavailable: " + reason + "."),
                   scale=scale, zero=zero, unit=unit, available=available)
        for family_key, label, unit, description in (
            ("pp1", "Last stored lambda-integrator step", "STFT count",
             "Direct little-endian saved copy of the bank {bank} PP1 slot: the last step "
             "magnitude stored by a lambda-integrator slew/ramp branch. Some branches apply a "
             "new step without refreshing this slot, so it is neither current STFT nor "
             "necessarily the last applied step."),
            ("pt2", "PT2 lambda-controller state", "raw",
             "Direct little-endian saved copy of the bank {bank} PT2 controller-state word. "
             "Bit 1 participates in the derived diagnostic code: it is 2 when paired lambda-state bit 3 is set, otherwise "
             "8 when this word's bit 1 is set, otherwise 1; unavailable bank 2 gives 0. The human "
             "meanings of those codes remain unresolved."),
        ):
            for bank in (1, 2):
                key = f"{family_key}_{bank}_raw"
                number(f"fault_snapshot_{key}", f"{label} bank {bank}", "faults",
                       offsets[key], 2,
                       description.format(bank=bank) + " "
                       + ("" if available else "Unavailable: " + reason + "."),
                       unit=unit, available=available)
        for bank in (1, 2):
            key = f"lambda_state_{bank}"
            offset = offsets[key]
            enabled = bool(image[offset] & 0x08)
            add(f"fault_snapshot_lambda_regulation_{bank}_active",
                f"Lambda regulation bank {bank} active", "faults", offset, 1,
                "Bit 3 of the saved lambda-controller state word. It reports whether closed-loop "
                "lambda regulation was active in the archived snapshot, not whether it is active now. "
                + ("" if available else "Unavailable: " + reason + "."),
                value=("1" if enabled else "0") if available else None,
                display=("Active" if enabled else "Inactive") if available else "Unavailable (raw bytes retained)",
                editable=True, requires_advanced=True, kind="choice", bit_mask=0x08,
                options=({"value": "0", "label": "Inactive"}, {"value": "1", "label": "Active"}))
            number(f"fault_snapshot_lambda_state_{bank}_raw",
                   f"Lambda-controller state bank {bank} (raw)", "faults", offset, 2,
                   "Exact saved controller-state word. Only the separately named closed-loop-active bit "
                   "has an admitted friendly meaning; all sibling bits remain raw.",
                   unit="raw", available=available, confidence="UNRESOLVED")
        identifier = image[offsets["internal_id"]]
        options = code_options + ({"value": "255", "label": "No particular fault (0xFF)"},)
        choice = next((option for option in options if option["value"] == str(identifier)), None)
        add("fault_snapshot_internal_id", "Snapshot associated fault", "faults", offsets["internal_id"], 1,
            "Internal ID matched against the saved occurrence list; 0xFF is the no-particular-fault "
            "marker. Changing this byte preserves the existing snapshot, not a newly captured value.",
            value=choice["value"] if choice else None,
            display=choice["label"] if choice else f"Unknown internal ID 0x{identifier:02X}",
            editable=True, requires_advanced=True, kind="choice", options=options)
        capture_state = image[offsets["state"]] & 0x07
        capture_state_options = (
            {"value": "0", "label": "No snapshot / reset state"},
            {"value": "1", "label": "Local DME fault-associated snapshot"},
            {"value": "2", "label": "External drivetrain-CAN transition snapshot"},
        )
        capture_state_choice = next(
            (option for option in capture_state_options
             if option["value"] == str(capture_state)), None)
        add("fault_snapshot_capture_state", "Snapshot capture state", "faults",
            offsets["state"], 1,
            "Masked low-three-bit stock capture state. Reset writes 0; local DME fault "
            "captures write 1; an ID 0xFF capture of an external drivetrain-CAN transition "
            "writes 2, whose restart path also restores companion runtime state 0x20. The "
            "exact CAN transition meaning is unresolved. Stock producers traced here do not "
            "emit values 3 through 7. Upper bits and all sibling bytes are preserved.",
            value=capture_state_choice["value"] if capture_state_choice else None,
            display=("Unknown / non-stock available state 0"
                     if capture_state == 0 and image[offsets["flags"]] & 0x10 else
                     capture_state_choice["label"] if capture_state_choice else
                     f"Unknown / non-stock state {capture_state}"),
            editable=True, requires_advanced=True, kind="choice", bit_mask=0x07,
            options=capture_state_options)
        flag("fault_snapshot_available", "Snapshot availability flag", offsets["flags"], 0x10,
             "Stored availability bit only. Setting it does not capture or initialize snapshot "
             "values. All unknown sibling flag bits are preserved.", off="Absent", on="Stored")
        retention = (image[offsets["flags"]] & 0x60) >> 5
        retention_options = (
            {"value": "0", "label": "Ordinary · replaceable by protected or locked"},
            {"value": "1", "label": "Protected · only locked may replace"},
            {"value": "2", "label": "Locked · no later capture may replace"},
            {"value": "3", "label": "Inconsistent / non-stock · effectively locked"},
        )
        add("fault_snapshot_retention_tier", "Snapshot retention tier", "faults",
            offsets["flags"], 1,
            "Behavioral replacement tier encoded by persisted flag bits 0x20/0x40. Ordinary "
            "snapshots can be replaced by protected or locked captures; protected snapshots "
            "can be replaced only by locked captures; locked snapshots block later captures. "
            "Stock producers make the two bits mutually exclusive, so 0x60 is inconsistent "
            "but effectively locked. These are behavioral names, not BMW severity or priority.",
            value=str(retention), display=retention_options[retention]["label"],
            editable=True, requires_advanced=True, kind="choice", bit_mask=0x60,
            options=retention_options)
        add("fault_snapshot_state", "Snapshot state (raw)", "faults", offsets["state"], 1,
            "The low three bits have the separate masked capture-state control. Upper sibling "
            "bits have no admitted meaning; all eight bits remain visible and preserved.",
            display=f"0x{image[offsets['state']]:02X}", confidence="UNRESOLVED")
        add("fault_snapshot_flags", "Snapshot flags (raw)", "faults", offsets["flags"], 1,
            "Stock persistence masks this byte with 0x70: 0x10 is availability and 0x20/0x40 "
            "form the separately named retention tier. Bits 0x01–0x08 and "
            "0x80 have no proven stock persistence or friendly meaning and remain raw.",
            display=f"0x{image[offsets['flags']]:02X}", confidence="UNRESOLVED")
        add("fault_snapshot_raw", "Snapshot payload (raw)", "faults", start, length,
            "Exact archived payload, including unresolved load/fuel-control state. "
            "Raw bytes remain available even when the snapshot cannot be restored.",
            display=f"{length} bytes (raw)", confidence="UNRESOLVED")

    for key in ("identity_gate", "fuel_adaptations", "dtc_occurrence", "idle_regulator_adaptation",
                "rough_running", "fault_memory", "output_test_nonce"):
        if key not in records:
            continue
        record = records[key]
        category = {"identity_gate": "identification", "fuel_adaptations": "fuel",
                    "output_test_nonce": "history", "dtc_occurrence": "faults",
                    "fault_memory": "faults"}.get(key, record["category"])
        length = record["length"] - (2 if record["checked"] else 0)
        add(f"{key}_raw", record["label"], category, record["offset"], length,
            "Stored record payload. Individual bytes not decoded above retain their raw "
            "representation; this is not a calibration table. Record check bytes are excluded.",
            display=f"{length} bytes (raw)", confidence="UNRESOLVED")
    for record in records.values():
        if record["category"] == "unknown":
            add(record["key"], record["label"], "unknown", record["offset"], record["length"],
                "No fixed writer/check pair or direct absolute consumer was found in this "
                "family's canonical program. Other families' meanings must not be assumed.",
                display=f"{record['length']} bytes (raw)", confidence="UNRESOLVED")

    padded = decoded["looks_like_zero_padded_ram_mirror"]
    progression = image[TAIL_START:TAIL_START + 3]
    progression_options = {
        "00 01 02": "Normal operating mode (0)",
        "03 04 05": "Normal operating mode (3)",
        "01 02 03": "Recovery / flash-listener mode (1)",
    }
    progression_value = progression.hex(" ").upper()
    progression_label = progression_options.get(progression_value, "Unrecognized progression")
    add("tail_progression", "Boot / recovery progression", "identification", TAIL_START, 3,
        "Boot-owned progression voted into E740. This is status, not a normal coding option. "
        "A live agent read can capture a temporary recovery state. Advanced changes "
        "alter boot behavior; this offline edit is not a live Seed/Restore operation.",
        value=None if padded or progression_value not in progression_options else progression_value,
        display="Unavailable (possible RAM mirror)" if padded else progression_label,
        confidence="UNRESOLVED" if padded else "STATIC", kind="choice",
        editable=not padded, requires_advanced=not padded,
        options=tuple({"value": value, "label": label} for value, label in progression_options.items()))
    for field_id, label, offset, length, description in (
        ("tail_descriptor", "Program reference (ZL_Referenz)", 0x1E3, 12,
         "ASCII BMW DATEN program-reference mirror. Not VIN, ISN, ZUSB or editable feature bits."),
        ("tail_dme_part_1", "BMW DATEN HW-NR — copy 1", 0x1EF, 7,
         "Program hardware/part-reference mirror. Not the vehicle's ZB/ZUSB assembly number."),
        ("tail_dme_part_2", "BMW DATEN HW-NR — copy 2", 0x1F6, 7,
         "Second program hardware/part-reference mirror; normally agrees with the first copy."),
    ):
        raw = image[offset:offset + length]
        value = raw.decode("ascii") if not padded and all(32 <= byte < 127 for byte in raw) else None
        add(field_id, label, "identification", offset, length, description,
            value=value, display=("Unavailable (possible RAM mirror)" if padded
                                 else (value if value.strip() else "Blank ASCII reference")
                                 if value is not None else "Not valid printable ASCII"),
            confidence="UNRESOLVED" if padded else "STATIC", kind="ascii",
            editable=not padded, requires_advanced=not padded)
    for field_id, label, offset, length in (
        ("unmapped_upper", "Unmapped upper bytes", layout["mirror_size"], TAIL_START - layout["mirror_size"]),
        ("tail_finalizer", "Boot finalizer byte", 0x1E0, 1),
        ("tail_reserved_1", "Reserved tail bytes", 0x1E1, 2),
        ("tail_reserved_2", "Reserved final bytes", 0x1FD, 3),
    ):
        if length > 0:
            add(field_id, label, "unknown", offset, length,
                "No verified user parameter or physical-unit conversion. Preserve these bytes.",
                display="Unavailable (possible RAM mirror)" if padded else f"{length} bytes (raw)",
                confidence="UNRESOLVED")
    return rows


def _set_ms411_management_field(before: bytes, field_id: str, value, field: dict) -> bytes:
    parsed = _ms411_management_field(field_id)
    if parsed is None:
        raise ValueError(f"unknown packed MS41.1 fault-management field {field_id!r}")
    group, index, suffix = parsed
    record = _ms411_management_record(before, group, index)
    target = bytearray(before)
    if field["kind"] == "choice":
        if not isinstance(value, str) or value not in {
                option["value"] for option in field["options"]}:
            raise ValueError(f"select one of the named choices for {field['label']}")
        mask = {"progression": 0x03, "terminal_latch": 0x0C, "valid": 0x40}.get(suffix)
        if mask is None:
            raise ValueError(f"unsupported packed MS41.1 choice {field_id!r}")
        shift = (mask & -mask).bit_length() - 1
        offset = record["offsets"][3]
        target[offset] = (before[offset] & ~mask) | ((int(value) << shift) & mask)
    elif field["kind"] == "number":
        try:
            numeric = float(value)
        except (TypeError, ValueError, OverflowError) as error:
            raise ValueError("EEPROM field value must be a finite number") from error
        if isinstance(value, bool) or not math.isfinite(numeric):
            raise ValueError("EEPROM field value must be a finite number")
        if str(value).strip() == field["display"]:
            return before
        if not field["minimum"] <= numeric <= field["maximum"]:
            raise ValueError(
                f"{field['label']} must be between {field['minimum']} and "
                f"{field['maximum']} {field['unit']}")
        stored = round((numeric - field["minimum"]) / field["step"])
        if suffix == "countdown":
            target[record["offsets"][4]] = stored
        else:
            component = {"min_rpm": 0, "min_load": 1,
                         "max_rpm": 2, "max_load": 3}.get(suffix)
            if component is None or not 0 <= stored <= 0x3F:
                raise ValueError(f"unsupported packed MS41.1 number {field_id!r}")
            p0, p1, p2 = record["offsets"][:3]
            if component == 0:
                target[p0] = (before[p0] & 0x03) | (stored << 2)
            elif component == 1:
                target[p0] = (before[p0] & 0xFC) | (stored >> 4)
                target[p1] = (before[p1] & 0x0F) | ((stored & 0x0F) << 4)
            elif component == 2:
                target[p1] = (before[p1] & 0xF0) | (stored >> 2)
                target[p2] = (before[p2] & 0x3F) | ((stored & 0x03) << 6)
            else:
                target[p2] = (before[p2] & 0xC0) | stored
    else:
        raise ValueError(f"unsupported packed MS41.1 field {field_id!r}")
    return update_checks_for_changed_records(before, target, "MS41.1")


def set_decoded_field(image: bytes, variant: str, field_id: str, value: str, *,
                      allow_advanced: bool = False) -> bytes:
    """Edit one admitted offline value; quantize once and preserve other bytes/bits."""
    if type(allow_advanced) is not bool:
        raise ValueError("allow_advanced must be a boolean")
    before = validate_image(image)
    field = next((row for row in decoded_fields(before, variant) if row["id"] == field_id), None)
    if field is None:
        raise ValueError(f"unknown EEPROM field {field_id!r}")
    if not field["editable"]:
        raise ValueError(f"{field['label']} is read-only in the decoded view")
    if field["requires_advanced"] and not allow_advanced:
        raise ValueError(f"{field['label']} requires advanced editing")
    if variant == "MS41.1" and _ms411_management_field(field_id) is not None:
        return _set_ms411_management_field(before, field_id, value, field)
    raw_replacement = None
    if field["kind"] == "number":
        raw_option = (value if isinstance(value, str)
                      and value.startswith("raw:")
                      and value in {option["value"] for option in field["options"]}
                      else None)
        if raw_option is not None:
            try:
                raw_replacement = bytes.fromhex(raw_option.removeprefix("raw:"))
            except ValueError as error:
                raise ValueError("invalid raw EEPROM field option") from error
            if len(raw_replacement) != field["length"]:
                raise ValueError("raw EEPROM field option has the wrong length")
        else:
            try:
                numeric = float(value)
            except (TypeError, ValueError, OverflowError) as error:
                raise ValueError("EEPROM field value must be a finite number") from error
            if isinstance(value, bool) or not math.isfinite(numeric):
                raise ValueError("EEPROM field value must be a finite number")
            if str(value).strip() == field["display"]:
                # Numeric rounded displays at a storage limit must remain exact no-ops.
                return before
    target = bytearray(before)
    offset, length = field["offset"], field["length"]
    signed = field_id in (
        "load_model_correction", "idle_air_correction_0", "idle_air_correction_1", "idle_air_correction_2",
    ) or (field_id.startswith("rough_running_slot_") and field_id.endswith("_correction_raw"))
    if raw_replacement is not None:
        replacement = raw_replacement
    elif field["kind"] == "ascii":
        if (not isinstance(value, str) or len(value) != length
                or any(not 32 <= ord(character) < 127 for character in value)):
            raise ValueError(f"{field['label']} requires exactly {length} printable ASCII characters")
        replacement = value.encode("ascii")
    elif field["kind"] == "choice":
        if not isinstance(value, str) or value not in {option["value"] for option in field["options"]}:
            if field_id == "transmission":
                raise ValueError("transmission mode must be 'at' or 'mt'")
            raise ValueError(f"select one of the named choices for {field['label']}")
        if field_id == "tail_progression":
            replacement = bytes.fromhex(value)
        else:
            stored = ((_u16(before, offset) & 0xFFFC) | {"at": 1, "mt": 2}[value]
                      if field_id == "transmission" else int(value))
            if "bit_mask" in field:
                mask = field["bit_mask"]
                shift = (mask & -mask).bit_length() - 1
                stored = (before[offset] & ~mask) | ((int(value) << shift) & mask)
            replacement = stored.to_bytes(length, "little")
    else:
        if not field["minimum"] <= numeric <= field["maximum"]:
            raise ValueError(
                f"{field['label']} must be between {field['minimum']} and "
                f"{field['maximum']} {field['unit']}")
        if variant == "MS41.1" and field_id.startswith("knock_cell_"):
            shift = (int(field_id.removeprefix("knock_cell_")) % 2) * 4
            nibble = round(-numeric / field["step"])
            stored = (before[offset] & ~(0x0F << shift)) | (nibble << shift)
        elif signed:
            stored = round(numeric / field["step"])
        else:
            stored = round((numeric - field["minimum"]) / field["step"])
        raw_minimum = -(1 << (8 * length - 1)) if signed else 0
        raw_maximum = 65535 if field_id == "operating_time_hours" else (1 << (8 * length - int(signed))) - 1
        if not raw_minimum <= stored <= raw_maximum:
            raise ValueError("EEPROM value cannot be represented by this field")
        if field_id == "operating_time_hours":
            if stored == _operating_time_state(before)[0]:
                return before
            replacement = b"".join(((stored + index) & 0xFFFF).to_bytes(2, "little") for index in range(3))
        else:
            replacement = stored.to_bytes(length, field.get("byteorder", "little"), signed=signed)
    target[offset:offset + length] = replacement
    return update_checks_for_changed_records(before, target, variant)


def inspect_image(image: bytes, variant: str = "MS41.3") -> dict:
    image = validate_image(image)
    fields = field_report(image, variant)
    decoded = decoded_values(image, variant)
    warnings = []
    if decoded["looks_like_zero_padded_ram_mirror"]:
        warnings.append(
            f"Bytes 0x{DECODE_LAYOUTS[variant]['mirror_size']:03X}–0x1FF are zero. "
            "This may be a padded RAM mirror, not a full physical EEPROM capture. "
            "Confirm provenance; tail identity and status are not decoded.")
    invalid = sum(row.get("check_ok") is False for row in fields)
    if invalid:
        warnings.append(
            f"{invalid} checked record(s) are invalid. Stored values may be rejected or "
            "replaced by defaults in the running ECU. Editing a payload updates only that "
            "record's check and can make the rest of that record active.")
    if len(set(image)) == 1:
        warnings.append("The image contains one repeated byte value; it is not a verified physical capture.")
    knock_stored_bytes = DECODE_LAYOUTS[variant]["knock_stored_bytes"]
    knock_raw = image[0x00E:0x00F + knock_stored_bytes]
    if any(value > 128 for value in (knock_raw[-1:] if variant == "MS41.1" else knock_raw)):
        warnings.append("Some stored knock corrections are above neutral; the ECU loader clamps those to zero correction.")
    detected = detect_layouts(image)
    if detected and variant not in detected:
        warnings.append(f"Selected {variant} layout differs from the tail identity ({' / '.join(detected)}).")
    if (not decoded["looks_like_zero_padded_ram_mirror"]
            and image[0x1EF:0x1F6] != image[0x1F6:0x1FD]):
        warnings.append("The two program HW-NR copies differ; editing one does not rewrite the other automatically.")
    if not decoded["operating_time_vote_valid"]:
        warnings.append("The three operating-time words have no valid progression. The stock vote falls back to zero; the raw words are preserved.")
    elif not decoded["operating_time_sequence_consistent"]:
        warnings.append("Operating-time redundancy is inconsistent. A surviving pair supplies the displayed counter; no storage words were repaired.")
    warnings.extend(_fault_history_state(image, variant, {row["key"]: row for row in fields})["warnings"])
    if variant in _FAULT_SNAPSHOTS:
        offsets = _FAULT_SNAPSHOTS[variant]
        state = image[offsets["state"]] & 0x07
        flags = image[offsets["flags"]]
        if flags & 0x10 and state not in (1, 2):
            warnings.append(
                f"Snapshot availability is set with unknown/non-stock capture state {state}.")
        if flags & 0x8F:
            warnings.append(
                f"Snapshot flags contain non-stock persisted bits 0x{flags & 0x8F:02X}; "
                "stock EEPROM save retains only mask 0x70.")
        if flags & 0x60 == 0x60:
            warnings.append(
                "Snapshot retention bits are both set (0x60); stock producers make them "
                "mutually exclusive, and this image behaves as locked.")
        if not flags & 0x10 and flags & 0x60:
            warnings.append(
                "Snapshot retention tier is set while availability is clear; retained state "
                "may be stale or externally edited.")
    return {
        "variant": variant,
        "size": len(image),
        "lower_mirror_size": DECODE_LAYOUTS[variant]["mirror_size"],
        "sha256": hashlib.sha256(image).hexdigest(),
        "decoded": decoded,
        "decoded_fields": decoded_fields(image, variant),
        "fields": fields,
        "warnings": warnings,
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
            # Some application storage forbids hard links. Operation IDs make same-path
            # writers unique, so a checked same-directory rename is enough.
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
