"""
live_data.py — MS41 live data parameter definitions and polling engine.

Standard mode: DS2 command 0x06 range reads from RAM.
Telegram mode: MS41 DS2 command 0x0B/0x01 — one registered-address response
  for the selected channels and validity fields, with grouped ordinary RAM
  reads only for descriptor overflow or unsupported descriptor addresses.

Standard and telegram modes use the same selected XML logger definition.
Addresses are absolute 24-bit C166 logical addresses for the Siemens 80C166
processor; the definition selects the applicable address for the detected ECU
ID and owns storage, scaling, units, and display formatting.

  Parameters without a mapped RAM address show "—". Standard and telegram
  acquisition use the same selected address/scaling table; standard mode is a
  transport fallback, not an independent validation source.
"""

import threading
import time
import csv
import os
import datetime
from collections import deque
from dataclasses import dataclass, field, replace
from typing import Optional, Dict, Tuple, List

from logger_definition import (
    LoggerDefinition,
    LoggerParameter,
    bundled_logger_definition_path,
    load_logger_definition,
)
from cut_runtime import (cut_state_address, detect_cut_runtime, detect_launch_runtime,
                         detect_alphan_failsafe_runtime)
from ds2 import (DS2Commands, DS2Error, DS2NegativeResponse,
                 MAX_TELEGRAM_ENTRIES, MAX_TELEGRAM_ADDRESS)


# ──────────────────────────── Definition-driven parameters ────────────────────

PROFILE_STANDARD = "standard"
PROFILE_WIDEBAND = "wideband"

_WBO2_ENABLE_ADDR = 0xFD22
_WBO2_ENABLE_MASK = 0x0100
_NARROWBAND_EMULATION_ADDR = 0xFD5A
_NARROWBAND_EMULATION_MASK = 0x0004
# Calibration SA 0x33C0 is visible to a DS2 CPU-memory read at 0x133C0.
# Its little-endian word is the live 10-bit ADC source pointer used by FUN_024570.
_WBO2_INPUT_SELECT_DS2_ADDR = 0x133C0
_DEFAULT_WBO2_INPUT_ADDR = 0xFA9A

_WBO2_INPUT_SOURCES = {
    0xE8DA: "TPS Voltage",
    0xFAA2: "TPS Base Voltage",
    0xFA96: "Rear O2 Bank 1",
    0xFA94: "Rear O2 Bank 2",
    0xFA98: "Front O2 Bank 2",
    0xFA9A: "Front O2 Bank 1",
    0xF5A8: "EVAP Pressure",
}


_PROFILE_DISPLAY_ROWS = [
    ("EVAP Purge Duty", "%"),
    ("Closed Throttle", ""),
    ("Part Load", ""),
    ("Full Load", ""),
    ("Decel Fuel Cut", ""),
    ("Engine Start", ""),
    ("Front O2 B1 Voltage", "V"),
    ("Front O2 B2 Voltage", "V"),
    ("MAF Sensor Voltage", "V"),
    ("Wideband Mode", ""),
    ("Wideband Input Source", ""),
    ("Narrowband Emulation", ""),
    ("Wideband Input Voltage", "V"),
    ("Wideband AFR", "AFR"),
    ("AFR Target", "AFR"),
    ("Cut Input 80", ""),
    ("Cut Input 81", ""),
    ("Cut Input 82", ""),
    ("Ignition Cut Switch", ""),
    ("Ignition Cut RPM", "RPM"),
    ("Cut Hysteresis", "RPM"),
    ("Cut Fixed IPW", "ms"),
    ("Launch Control Switch", ""),
    ("Launch Cut Type", ""),
    ("Launch Clutch Polarity", ""),
    ("Launch Control RPM", "RPM"),
    ("Launch Arm Speed", "km/h"),
    ("Launch Max Speed", "km/h"),
    ("Launch Min TPS", "%"),
    ("Launch Hard Cut RPM", "RPM"),
    ("Launch Ignition Hysteresis", "RPM"),
    ("Launch Ignition Fixed IPW", "ms"),
    ("Ignition Cut Request", ""),
    ("Launch Ignition Request", ""),
    ("Launch Fuel Cut Active", ""),
    ("Intentional Combustion Cut", ""),
    ("Cut Patch Runtime", ""),
    ("Launch Armed", ""),
    ("Launch Legacy FD5A.7", ""),
    ("Native Fuel Limiter Active", ""),
    ("Native Fuel Recovery Priority", ""),
    ("Effective Fuel Limiter RPM", "RPM"),
    ("Fuel Cut Stage Count", ""),
    ("Cut RPM", "RPM"),
    ("Launch TPS (threshold scale)", "%"),
    ("Launch Speed", "km/h"),
    ("Fuel Trim Additive", "ms"),
    ("Fuel Trim Additive B2", "ms"),
    ("Lambda Regulation B1", ""),
    ("Lambda Regulation B2", ""),
    ("O2 Heater Front B1", "%"),
    ("O2 Heater Front B2", "%"),
    ("O2 Heater Rear B1", "%"),
    ("O2 Heater Rear B2", "%"),
    ("Lambda Monitor Counter", ""),
    ("Lambda Functions FD0E", ""),
    ("Lambda Functions FD10", ""),
]
PROFILE_DISPLAY_NAMES = frozenset(name for name, _unit in _PROFILE_DISPLAY_ROWS)
_PROFILE_STATUS_NAMES = frozenset({
    "Wideband Mode", "Wideband Input Source", "Narrowband Emulation",
})
_PROFILE_STATUS_IDS = {
    "BS_PROFILE_WIDEBAND_MODE": "Wideband Mode",
    "BS_PROFILE_WIDEBAND_INPUT_SOURCE": "Wideband Input Source",
    "BS_PROFILE_NARROWBAND_EMULATION": "Narrowband Emulation",
}

# Non-gauge presentation kinds remain code-owned for runtime probes and raw
# diagnostic values; every dial range comes from the selected definition.
_LIVE_STATUS_CHANNELS = frozenset({
    ("Closed Throttle", ""), ("Part Load", ""), ("Full Load", ""),
    ("Decel Fuel Cut", ""), ("Engine Start", ""),
    ("Wideband Mode", ""), ("Narrowband Emulation", ""),
    ("Cut Input 80", ""), ("Cut Input 81", ""), ("Cut Input 82", ""),
    ("Ignition Cut Request", ""), ("Launch Ignition Request", ""),
    ("Launch Fuel Cut Active", ""), ("Intentional Combustion Cut", ""),
    ("Cut Patch Runtime", ""), ("Launch Armed", ""),
    ("Launch Legacy FD5A.7", ""), ("Native Fuel Limiter Active", ""),
    ("Native Fuel Recovery Priority", ""),
    ("Lambda Regulation B1", ""), ("Lambda Regulation B2", ""),
    ("Cut Timing State", ""), ("Cut Timing Source", ""),
    ("Active MAF Fault", ""), ("Manual AlphaN Mode", ""), ("Speed Density Mode", ""),
})

_LIVE_VALUE_CHANNELS = frozenset({
    ("Injector PW", "ms"), ("Wideband Input Source", ""),
    ("Ignition Cut Switch", ""), ("Ignition Cut RPM", "RPM"),
    ("Cut Hysteresis", "RPM"), ("Cut Fixed IPW", "ms"),
    ("Launch Control Switch", ""), ("Launch Cut Type", ""),
    ("Launch Clutch Polarity", ""), ("Launch Control RPM", "RPM"),
    ("Launch Arm Speed", "km/h"), ("Launch Max Speed", "km/h"),
    ("Launch Min TPS", "%"), ("Launch Hard Cut RPM", "RPM"),
    ("Launch Min TPS", "° throttle"),
    ("Launch TPS (threshold scale)", "° throttle"),
    ("Effective Fuel Limiter RPM", "RPM"),
    ("Launch Ignition Hysteresis", "RPM"),
    ("Launch Ignition Fixed IPW", "ms"), ("Cut RPM", "RPM"),
    ("Launch TPS (threshold scale)", "%"), ("Launch Speed", "km/h"),
    ("Ignition Retard Range", "RPM"), ("Ignition Cut Timing", "°BTDC"),
    ("Launch Fuel Hysteresis B", "RPM"), ("Launch Fuel Hysteresis A", "RPM"),
    ("Launch Ignition Timing", "°BTDC"), ("Launch Retard Range", "RPM"),
    ("Launch Cut Timing", "°BTDC"),
    ("Extended Engine Load", "mg/st"), ("Extended MAF / Model", "kg/h"),
})

_LIVE_RAW_CHANNELS = frozenset({
    ("Fuel Cut Stage Count", ""), ("Lambda Monitor Counter", ""),
    ("Lambda Functions FD0E", ""), ("Lambda Functions FD10", ""),
    ("Cut Recovery Pending", "raw"),
}) | frozenset((f"Cylinder {index + 1} Roughness", "raw") for index in range(6))


def live_display_spec(name: str, unit: str, definition_path=None) -> dict:
    """Return evidence-bounded viewer metadata for an exact live-data channel."""
    key = (name, unit)
    for parameter in _load_definition(definition_path).parameters:
        if (parameter.name, parameter.unit) != key:
            continue
        minimum, maximum, step = (
            parameter.gauge_min, parameter.gauge_max, parameter.gauge_step)
        if (minimum is not None and maximum is not None and step is not None
                and minimum < maximum and 0 < step <= maximum - minimum):
            return {
                "kind": "dial", "minimum": minimum, "maximum": maximum,
                "step": step, "source": parameter.id,
                "evidence": "definition",
            }
        break
    if key in _LIVE_STATUS_CHANNELS:
        return {"kind": "status", "evidence": "core"}
    if key in _LIVE_RAW_CHANNELS:
        return {"kind": "raw", "evidence": "core"}
    return {
        "kind": "value",
        "evidence": "core" if key in _LIVE_VALUE_CHANNELS else "unknown",
    }

_CUT_PATCH_IDS = frozenset({"1406464", "SHINDE1", "1429861", "1437806"})
_RAM_ROUGHNESS_IDS = {f"BS_ROUGHNESS_CYL{index + 1}": index for index in range(6)}
_RAM_ROUGHNESS_RANGES = ((0xE854, 14), (0xFD30, 2), (0xDA2A, 2), (0xFD14, 1))
_RAM_ROUGHNESS_GATES = ((0xE861, 1), (0xFD30, 2), (0xDA2A, 2), (0xFD14, 1))
_NATIVE_LIVE_IDS = frozenset({
    "BS_CUT_FUEL_ADD_B1", "BS_CUT_FUEL_ADD_B2",
    "BS_CUT_LAMBDA_REG_B1", "BS_CUT_LAMBDA_REG_B2",
    "BS_CUT_O2_FRONT_B1", "BS_CUT_O2_FRONT_B2",
    "BS_CUT_O2_REAR_B1", "BS_CUT_O2_REAR_B2",
    "BS_CUT_STOCK_LIMITER", "BS_CUT_STAGE_COUNT", "BS_CUT_EFFECTIVE_SOFT_RPM",
    "BS_CUT_LAMBDA_COUNTER", "BS_CUT_LAMBDA_FD0E", "BS_CUT_LAMBDA_FD10",
    "BS_MODE_MAF_FAULT", "BS_MODE_MANUAL_ALPHA", "BS_MODE_SPEED_DENSITY",
})
_ALPHAN_EXTENDED_IDS = frozenset({"BS_ALPHA_EXTENDED_LOAD", "BS_ALPHA_EXTENDED_MAF"})
_V16_LIVE_IDS = frozenset({
    "BS_CUT_RETARD_RANGE", "BS_CUT_IGN_ANGLE",
    "BS_CUT_LAUNCH_FUEL_HYST_B", "BS_CUT_LAUNCH_FUEL_HYST_A",
    "BS_CUT_LAUNCH_IGN_ANGLE", "BS_CUT_LAUNCH_RETARD_RANGE",
    "BS_CUT_LAUNCH_CUT_IGN_ANGLE", "BS_CUT_RECOVERY_PENDING",
    "BS_CUT_TIMING_STATE", "BS_CUT_TIMING_SOURCE",
    "BS_CUT_LAUNCH_RECOVERY_PRIORITY",
})


def _is_ram_roughness_parameter(parameter):
    index = _RAM_ROUGHNESS_IDS.get(parameter.id)
    return (index is not None and parameter.address == 0xE854 + index * 2
            and parameter.length == 2 and parameter.signed
            and parameter.endian == "little")


# Definition-derived axis locations converted to live DS2 CPU addresses.
# Only variants with proven Knock Tables X/Y definitions are listed.
_ADAPTATION_AXES = {
    "1437806": (0x12606, 0x125D9),
    "1438068": (0x12606, 0x125D9),
    "1429861": (0x1239A, 0x1236D),
    "1432401": (0x1239A, 0x1236D),
    "1429373": (0x12144, 0x12117),
    "1406464": (0x12388, 0x1235B),
    "SHINDE1": (0x12388, 0x1235B),
}
# Fuel-adaptation RAM addresses are separate from the selectable live logger.
_ADAPTATION_FUEL_ADDRS = {
    "1437806": ((0xF040, 0xF048), (0xF0FC, 0xF104)),
    "1429861": ((0xED66, 0xED6E), (0xEDA0, 0xEDA8)),
    "1406464": ((0xF028, 0xF030), (0xF0D4, 0xF0DC)),
    "SHINDE1": ((0xF028, 0xF030), (0xF0D4, 0xF0DC)),
}
_KNOCK_ADAPTATION_ADDR = 0xD840


def _load_definition(definition_path=None) -> LoggerDefinition:
    return load_logger_definition(
        definition_path or bundled_logger_definition_path())


def _definition_parameters_for(definition, ecu_id):
    # Logger conversions belong to a parameter, not an ECU mapping. Separate
    # exact-ID XML parameters keep its units correct; retain app selection IDs.
    aliases = {"BS_CUT_LAUNCH_MIN_TPS_MS410": "BS_CUT_LAUNCH_MIN_TPS",
               "BS_CUT_LAUNCH_TPS_MS410": "BS_CUT_LAUNCH_TPS"} if ecu_id == "1429861" else {}
    return tuple(replace(param, id=aliases.get(param.id, param.id))
                 for param in definition.parameters_for(ecu_id))


def telegram_params_for(ecu_id, profile: str = PROFILE_STANDARD,
                        wideband_input_addr: int = _DEFAULT_WBO2_INPUT_ADDR,
                        include_cut: bool = False, definition_path=None,
                        definition: LoggerDefinition | None = None,
                        cut_revision: str = "V16", include_launch: bool = False,
                        include_alphan: bool = False
                        ) -> List[LoggerParameter]:
    """Resolve the selected logger definition for one exact ECU ID/profile."""
    if profile not in {PROFILE_STANDARD, PROFILE_WIDEBAND}:
        raise ValueError(f"unknown live-data profile {profile!r}")
    definition = definition or _load_definition(definition_path)
    params = []
    for param in _definition_parameters_for(definition, str(ecu_id or "")):
        # The format also permits ADC selectors and predefined group offsets;
        # this owner only issues absolute DS2 memory reads for those channels.
        if param.address < 0x20 or param.groupsize:
            continue
        if param.id.startswith("BS_STD_") and profile != PROFILE_STANDARD:
            continue
        if param.id.startswith("BS_WB_") and profile != PROFILE_WIDEBAND:
            continue
        if param.id in _ALPHAN_EXTENDED_IDS and not include_alphan:
            continue
        if param.id.startswith("BS_CUT_") and param.id not in _NATIVE_LIVE_IDS:
            if not include_cut:
                continue
            if param.id in _V16_LIVE_IDS and cut_revision != "V16":
                continue
            if (param.id.startswith("BS_CUT_LAUNCH_")
                    or param.id == "BS_CUT_CLUTCH_POLARITY") and not include_launch:
                continue
            if param.id == "BS_CUT_LEGACY_FD5A7" and (not include_launch or cut_revision == "V16"):
                continue
        if param.id == "BS_WB_INPUT":
            param = param.with_address(wideband_input_addr)
        if (param.id in {"BS_CUT_IGNITION_REQUEST", "BS_CUT_LAUNCH_IGN_REQUEST",
                         "BS_CUT_LAUNCH_FUEL_ACTIVE", "BS_CUT_INTENTIONAL", "BS_CUT_RUNTIME"}
                and param.address in (0xE847, 0xD801)):
            param = param.with_address(cut_state_address(cut_revision))
        params.append(param)
    return params


def live_data_supported(ecu_id, definition_path=None) -> bool:
    """Whether the selected definition has at least one exact-ID live channel."""
    return bool(telegram_params_for(ecu_id, definition_path=definition_path))


def selection_catalog(ecu_id, definition_path=None):
    """Exact-ID choices before runtime admission, as (ID, name, unit) triples.

    Patch/profile choices may be unavailable after detection. Profile metadata
    can accompany selected measurements; metadata alone does not start polling.
    """
    rows = [(p.id, p.name, p.unit)
            for p in _definition_parameters_for(_load_definition(definition_path), str(ecu_id or ""))
            if p.address >= 0x20 and not p.groupsize]
    if str(ecu_id) == "SHINDE1":
        rows.extend((key, name, "") for key, name in _PROFILE_STATUS_IDS.items())
    return tuple(rows)


_ROUGHNESS_VARIANTS = frozenset({"MS41.0", "MS41.1", "MS41.2", "MS41.3", "MS42", "MS43"})
_ROUGHNESS_ECU_IDS = {
    "MS41.0": "1429861", "MS41.1": "1437806",
    "MS41.2": "1406464", "MS41.3": "SHINDE1",
}


@dataclass(frozen=True)
class RoughnessReading:
    """Signed snapshot; timestamp marks acquisition, not new ECU production.

    ``status`` is the BMW result byte for command reads, or the eligibility mask
    for RAM reads. Unavailable cylinders remain None rather than a false zero.
    """

    status: int
    cylinders: tuple[float | int | None, ...]
    timestamp: float = 0.0
    rpm: float | None = None
    validity_mask: int = 0
    state: str = "unavailable"


def roughness_supported(ecu_variant) -> bool:
    return ecu_variant in _ROUGHNESS_VARIANTS


def _roughness_variant(ecu_variant) -> str:
    if not roughness_supported(ecu_variant):
        raise ValueError(f"roughness monitoring is not mapped for {ecu_variant or 'unknown'}")
    return ecu_variant


class _RoughnessStartRejected(DS2Error):
    """The ECU explicitly declined to start this system check."""


def _roughness_command(ds2, command):
    # BMW's system-check jobs resend on A1/BUSY. Bound that loop, and never
    # reinterpret another negative reply or a transport failure as success.
    for attempt in range(3):
        try:
            return bytes(ds2.execute(command, b"\x05"))
        except DS2NegativeResponse as error:
            if error.command != command or error.status != 0xA1 or attempt == 2:
                raise
            time.sleep(0.05)


def start_roughness_check(ds2, ecu_variant) -> None:
    """Start BMW system-check selector 5; MS41.0 acknowledges a no-op."""
    _roughness_variant(ecu_variant)
    payload = _roughness_command(ds2, DS2Commands.START_SYSTEMCHECK)
    if payload == b"\x01":
        if ecu_variant in {"MS42", "MS43"}:
            raise _RoughnessStartRejected(
                "ECU declined roughness start: run the engine and stop any active actuator/system tests.")
        raise _RoughnessStartRejected("ECU declined roughness start: another system check is active")
    if payload != b"\x00":
        raise DS2Error(f"Unexpected roughness START payload: {payload.hex(' ')}")


def stop_roughness_check(ds2, ecu_variant) -> None:
    """Stop BMW system-check selector 5 and validate its empty acknowledgement."""
    _roughness_variant(ecu_variant)
    payload = _roughness_command(ds2, DS2Commands.STOP_SYSTEMCHECK)
    if payload:
        raise DS2Error(f"Unexpected roughness STOP payload: {payload.hex(' ')}")


def read_roughness(ds2, ecu_variant) -> RoughnessReading:
    """Read BMW selector 5, retaining signed deviations instead of magnitudes."""
    _roughness_variant(ecu_variant)
    if ecu_variant in {"MS42", "MS43"}:
        from ms42_ms43_live import read_roughness as read_family_roughness
        return read_family_roughness(ds2)
    payload = _roughness_command(ds2, DS2Commands.READ_SYSTEMCHECK)
    if len(payload) != 13:
        raise DS2Error(f"Unexpected roughness READ payload length: {len(payload)} (expected 13)")
    status = payload[0]
    # All four native serializers report status 0 and copy six words verbatim.
    # In particular FFFF can be a real signed -1; it is not a wire sentinel.
    values = tuple(int.from_bytes(payload[index:index + 2], "big", signed=True)
                   for index in range(1, 13, 2)) if status == 0 else (None,) * 6
    return RoughnessReading(status, values, time.monotonic(),
                            state="available" if status == 0 else "unavailable")


def read_ram_roughness(ds2, ecu_variant) -> RoughnessReading:
    """Read normal roughness RAM without starting or acknowledging a system check.

    Canonical MS41.0/.1/.2/.3 producers share E854..E85F (six signed LE16
    filtered values) and E861 bits 0..5 (cylinder eligibility, not freshness).
    Values retain their native sign, including valid -1. FD30 bits 1..3 inhibit
    reference production. The ordinary logger acquires these same RAM channels.
    """
    _roughness_variant(ecu_variant)
    if ecu_variant in {"MS42", "MS43"}:
        raise ValueError("Use the BMW roughness check for MS42/MS43")
    rpm_param = next(param for param in _BUNDLED_DEFINITION.parameters_for(
        _ROUGHNESS_ECU_IDS[ecu_variant]) if param.id == "P8")

    def read_exact(address, length):
        data = bytes(ds2.read_mem(address, length))
        if len(data) != length:
            raise ValueError(
                f"short roughness RAM read at 0x{address:04X}: {len(data)}/{length} bytes")
        return data

    payload = read_exact(0xE854, 14)
    rpm = rpm_param.parse(read_exact(rpm_param.address, rpm_param.length), rpm_param.address)
    running = bool(read_exact(0xFD14, 1)[0] & 0x04)
    inhibited = bool(int.from_bytes(read_exact(0xFD30, 2), "little") & 0x000E)
    mask = payload[13] & 0x3F
    available = rpm > 0 and running and not inhibited
    values = []
    for index in range(6):
        value = int.from_bytes(payload[2 * index:2 * index + 2], "little", signed=True)
        values.append(value if available and mask & (1 << index) else None)
    state = ("engine_stopped" if rpm == 0 else
             "unavailable" if all(value is None for value in values) else
             "partial" if any(value is None for value in values) else "available")
    return RoughnessReading(mask, tuple(values), time.monotonic(), rpm, mask, state)


def adaptation_read_supported(ecu_id) -> bool:
    """Whether the packaged definition owner has exact knock-table axes."""
    return str(ecu_id or "") in _ADAPTATION_AXES


def read_adaptations(ds2, ecu_id):
    """Read stored fuel, throttle, and six 16x4 knock-adaptation tables."""
    ecu_id = str(ecu_id or "")
    axes = _ADAPTATION_AXES.get(ecu_id)
    if axes is None:
        raise ValueError(
            f"adaptation-table addresses are not mapped for ECU ID {ecu_id or 'unknown'}")

    def read_exact(address, length):
        data = bytes(ds2.read_mem(address, length))
        if len(data) != length:
            raise ValueError(
                f"short adaptation read at 0x{address:05X}: {len(data)}/{length} bytes")
        return data

    additive, ltft = [None, None], [None, None]
    if ecu_id in _ADAPTATION_FUEL_ADDRS:
        for index, (add_address, lt_address) in enumerate(
                _ADAPTATION_FUEL_ADDRS[ecu_id]):
            block = read_exact(add_address, lt_address - add_address + 2)
            add_raw = int.from_bytes(block[:2], "little")
            lt_raw = int.from_bytes(
                block[lt_address - add_address:lt_address - add_address + 2],
                "little")
            additive[index] = (add_raw - 32768) * 0.00534
            ltft[index] = (lt_raw - 32768) * 100 / 65535

    throttle_raw = int.from_bytes(read_exact(0xE8DE, 2), "little", signed=True)
    load_address, rpm_address = axes
    load = [value * 1389 / 255 for value in read_exact(load_address, 4)]
    rpm = [value * 32 for value in read_exact(rpm_address, 16)]

    tables = []
    for index in range(6):
        raw = read_exact(_KNOCK_ADAPTATION_ADDR + index * 0x40, 0x40)
        values = [(value - 128) * 0.375 for value in raw]
        tables.append([values[row:row + 4] for row in range(0, 64, 4)])

    return {
        "ecu_id": ecu_id,
        "additive": additive,
        "ltft": ltft,
        "throttle": throttle_raw * 0.001526,
        "load": load,
        "rpm": rpm,
        "knock": tables,
    }


# Callers may select their own metadata path; the desktop uses the bundled default.
_BUNDLED_DEFINITION = _load_definition()
_TELEGRAM_PARAMS: List[LoggerParameter] = telegram_params_for(
    "1437806", definition=_BUNDLED_DEFINITION)
TELEGRAM_PARAM_NAMES = frozenset(
    param.name for param in _BUNDLED_DEFINITION.parameters
) | _PROFILE_STATUS_NAMES


def display_rows(definition_path=None) -> List[Tuple[str, str]]:
    """Ordered rows supplied by the selected definition plus runtime probes."""
    definition = _load_definition(definition_path)
    rows = list(dict.fromkeys(
        (param.name, param.unit) for param in definition.parameters))
    existing = {name for name, _unit in rows}
    rows.extend(
        row for row in _PROFILE_DISPLAY_ROWS
        if row[0] in _PROFILE_STATUS_NAMES and row[0] not in existing
    )
    return rows


# Qualified native variable-descriptor handlers; other IDs retain READ_MEM.
_TELEGRAM_ECU_IDS = frozenset({"1429861", "1437806", "1406464", "SHINDE1"})


def _batch_entries_for(parameters, extra_ranges=()):
    """Deduplicate required bytes and pack adjacent bytes into aligned words."""
    required = {p.address + offset for p in parameters for offset in range(p.length)}
    required.update(address + offset for address, length in extra_ranges
                    for offset in range(length))
    entries = []
    covered = set()
    for address in sorted(required):
        if address in covered or not 0 <= address <= MAX_TELEGRAM_ADDRESS:
            continue
        if len(entries) == MAX_TELEGRAM_ENTRIES:
            break
        length = 2 if address % 2 == 0 and address + 1 in required else 1
        entries.append((address, length))
        covered.update(range(address, address + length))
    # Keep only missing byte runs; grouped READ_MEM handles overflow and addresses
    # outside the native descriptor address range without re-registering a list.
    ranges = []
    for address in sorted(required - covered):
        if (ranges and ranges[-1][1] < 120
                and address == ranges[-1][0] + ranges[-1][1]):
            ranges[-1] = (ranges[-1][0], ranges[-1][1] + 1)
        else:
            ranges.append((address, 1))
    return tuple(entries), _build_telegram_blocks([], extra_ranges=ranges)


def _decode_batch_snapshot(raw, entries):
    """Restore native memory bytes from ordered, big-endian Telegram values."""
    expected = sum(length for _address, length in entries)
    if len(raw) != expected:
        raise ValueError(f"Telegram payload length {len(raw)}; expected {expected}")
    snapshot = {}
    offset = 0
    for address, length in entries:
        snapshot.update((address + index, value)
                        for index, value in enumerate(raw[offset:offset + length][::-1]))
        offset += length
    return snapshot


@dataclass
class _TelBlock:
    """A contiguous memory range to be read in one DS2 command-0x06 call."""
    start:  int
    size:   int
    params: List[LoggerParameter] = field(default_factory=list)


def _build_telegram_blocks(params, max_span=120, extra_ranges=()) -> List[_TelBlock]:
    """Greedily group params into block reads whose span stays within max_span bytes."""
    blocks = []
    ranges = [(p.address, p.length, [p]) for p in params]
    ranges.extend((start, size, []) for start, size in extra_ranges)
    for start, size, parameters in sorted(ranges, key=lambda item: item[0]):
        end = start + size
        if blocks and end - blocks[-1].start <= max_span:
            b = blocks[-1]
            b.size = max(b.size, end - b.start)
            b.params.extend(parameters)
        else:
            blocks.append(_TelBlock(start=start, size=size, params=list(parameters)))
    return blocks

_TELEGRAM_BLOCKS: List[_TelBlock] = _build_telegram_blocks(_TELEGRAM_PARAMS)


# ──────────────────────────── Poller ─────────────────────────────────────────

class LiveDataPoller:
    """
    Polls MS41 live data on a background thread.

    Standard mode (use_telegram=False):
      Reads the selected definition's memory bytes via DS2 command 0x06, grouped into
      contiguous ranges (multiple round trips per sample).

    Telegram mode (use_telegram=True):
      Registers the selected definition's memory bytes via DS2 0x0B/0x01 and then
      reads one 0x0B/0x00 response plus grouped additional channels per sample.

    Stores latest values in a thread-safe dict; the GUI reads via a QTimer.
    Optionally writes every poll cycle to a CSV file.

    selected_ids=None preserves the full legacy catalog. An explicit immutable
    selection limits acquisition and output; profile metadata requires at least
    one admitted measurement. Auto chooses the smaller recurring request budget.
    """

    def __init__(self, interval: float = 0.5, use_telegram: bool = False,
                 ecu_id=None, ecu_variant=None, ds2=None, log_columns=None,
                 telegram_fallback: bool = True, definition_path=None,
                 selected_ids=None):
        self._ds2          = ds2          # DS2Interface — the live ECU connection
        self._interval     = interval
        self._use_telegram = use_telegram
        self._telegram_fallback = telegram_fallback
        self._ecu_id       = ecu_id
        self._ecu_variant  = ecu_variant
        self._profile      = PROFILE_STANDARD
        self._profile_ready = False
        self._cut_patch_active = False
        self._alphan_extended_active = False
        self._wideband_input_addr = _DEFAULT_WBO2_INPUT_ADDR
        self._logger_definition = _load_definition(definition_path)
        if isinstance(selected_ids, str):
            raise ValueError("selected_ids must contain parameter IDs, not a string")
        self._selected_ids = None if selected_ids is None else frozenset(selected_ids)
        self._available_status = {}
        self._acquisition_mode = None
        # Resolve only parameters explicitly mapped for this ECU ID.
        self._configure_parameters(telegram_params_for(
            ecu_id, definition=self._logger_definition))
        self._log_columns = tuple(log_columns) if log_columns is not None else None
        self._active_profile_names = set()
        self._stop         = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock         = threading.Lock()
        self._latest:  Dict[str, Tuple[str, str]] = {}  # name → (value_str, unit)
        self._errors:  List[str] = []
        self._csv_file   = None
        self._csv_writer = None
        self._csv_rows   = 0     # rows written so far (GUI reads this for status display)
        self._csv_last_flush = 0.0
        self._samples = 0
        self._sample_started = 0.0
        self._sample_origin = None  # first completed acquisition, independent of setup time
        self._sample_sequence = 0
        self._sample_channels = ()
        self._completed_samples = deque(maxlen=256)
        self._sample_times = deque(maxlen=256)
        self._pending_log_path = None
        self._terminal_error = None

    # ── Public API ─────────────────────────────────────────────────────────

    def start(self, log_path: Optional[str] = None):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        with self._lock:
            self._latest.clear()
            self._errors.clear()
            self._terminal_error = None
            self._csv_rows = 0
            self._sample_sequence = 0
            self._sample_channels = ()
            self._completed_samples.clear()
            self._sample_times.clear()
        self._samples = 0
        self._sample_started = time.monotonic()
        self._sample_origin = None
        self._acquisition_mode = None
        self._pending_log_path = log_path
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            # Callers reuse or close the connection as soon as this returns.
            self._thread.join()
            self._thread = None
        self._close_csv()

    def latest_values(self) -> Dict[str, Tuple[str, str]]:
        """Return a snapshot of the latest parameter values (thread-safe)."""
        with self._lock:
            return dict(self._latest)

    def resolved_rows(self) -> List[Tuple[str, str]]:
        """Ordered channels selected by the exact ECU/profile owner."""
        rows = [(param.name, param.unit) for param in self._tel_params]
        parameter_names = {name for name, _unit in rows}
        rows.extend(
            row for row in _PROFILE_DISPLAY_ROWS
            if row[0] in self._active_profile_names and row[0] not in parameter_names
        )
        return list(dict.fromkeys(rows))

    def available_parameters(self):
        """Full admitted parameter catalog, independent of capture selection."""
        return self._available_params

    def available_channels(self):
        """Full admitted (ID, name, unit) catalog, including profile metadata."""
        rows = [(p.id, p.name, p.unit) for p in self._available_params]
        rows.extend((key, name, self._available_status[name][1])
                    for key, name in _PROFILE_STATUS_IDS.items()
                    if name in self._available_status)
        return tuple(rows)

    def available_rows(self):
        return [(name, unit) for _key, name, unit in self.available_channels()]

    @property
    def acquisition_mode(self):
        """Actual recurring transport, available once acquisition starts."""
        return self._acquisition_mode

    @property
    def profile_ready(self):
        """Whether available_channels contains the completed runtime admission."""
        return self._profile_ready

    def display_values(self) -> List[Tuple[str, str, str]]:
        """Ordered display-ready values for the currently resolved channels."""
        latest = self.latest_values()
        return [
            (name, latest[name][0], latest[name][1])
            for name, _unit in self.resolved_rows()
            if name in latest and latest[name][0] != "—"
        ]

    def pop_errors(self) -> List[str]:
        with self._lock:
            errs, self._errors = list(self._errors), []
        return errs

    def completed_samples_since(self, after_sequence: int):
        """Return exact completed poll cycles newer than a bounded sequence cursor."""
        if type(after_sequence) is not int or after_sequence < 0:
            raise ValueError("Live Data sample cursor must be a non-negative integer")
        with self._lock:
            if after_sequence > self._sample_sequence:
                raise ValueError("Live Data sample cursor is ahead of the poller")
            oldest = (
                self._completed_samples[0][0]
                if self._completed_samples else self._sample_sequence + 1
            )
            dropped = max(0, oldest - after_sequence - 1)
            samples = tuple(
                sample for sample in self._completed_samples
                if sample[0] > after_sequence
            )
            return (
                self._sample_sequence,
                dropped,
                self._csv_rows,
                self._sample_channels,
                samples,
            )

    @property
    def terminal_error(self):
        with self._lock:
            return self._terminal_error

    @property
    def active_profile_names(self):
        """Optional live-table rows used by the detected profile."""
        return frozenset(self._active_profile_names)

    # ── Internal ────────────────────────────────────────────────────────────

    def _configure_parameters(self, parameters):
        self._available_params = tuple(parameters)
        self._tel_params = [p for p in self._available_params
                            if self._selected_ids is None or p.id in self._selected_ids]
        self._roughness_params = tuple(p for p in self._tel_params
                                       if _is_ram_roughness_parameter(p))
        self._dependency_ranges = _RAM_ROUGHNESS_GATES if self._roughness_params else ()
        if any(p.id in _ALPHAN_EXTENDED_IDS for p in self._tel_params):
            self._dependency_ranges += ((0xFD22, 2),)
        self._tel_blocks = _build_telegram_blocks(
            self._tel_params, extra_ranges=self._dependency_ranges)
        self._batch_entries, self._batch_supplements = _batch_entries_for(
            self._tel_params, self._dependency_ranges)
        if str(self._ecu_id) not in _TELEGRAM_ECU_IDS:
            self._batch_entries = ()

    def _require_measurement_selection(self, parameters):
        if self._selected_ids is not None and not any(
                p.id in self._selected_ids for p in parameters):
            raise ValueError("Select at least one measurement channel")

    def _selection_prefers_standard(self):
        if not self._telegram_fallback:
            return False
        direct = (len(self._tel_blocks), sum(block.size + 13 for block in self._tel_blocks))
        # Poll request is five bytes; reply adds four framing bytes to its data.
        batch = (1 + len(self._batch_supplements),
                 9 + sum(length for _address, length in self._batch_entries)
                 + sum(block.size + 13 for block in self._batch_supplements))
        return direct <= batch

    def _is_ms413(self) -> bool:
        return self._ecu_variant == "MS41.3" or str(self._ecu_id) == "SHINDE1"

    def _read_word_le(self, address: int) -> int:
        raw = bytes(self._ds2.read_mem(address, 2))
        if len(raw) != 2:
            raise ValueError(f"short read at 0x{address:05X}: {len(raw)}/2 bytes")
        return int.from_bytes(raw, "little")

    def _prepare_live_profile(self) -> None:
        """Select the universal or MS41.3 WBO2 profile once per polling session."""
        if self._profile_ready:
            return
        self._require_measurement_selection(
            _definition_parameters_for(self._logger_definition, str(self._ecu_id or "")))

        profile = PROFILE_STANDARD
        input_addr = _DEFAULT_WBO2_INPUT_ADDR
        status_values = {}
        if self._is_ms413():
            try:
                flags = self._read_word_le(_WBO2_ENABLE_ADDR)
                wideband_enabled = bool(flags & _WBO2_ENABLE_MASK)
                status_values["Wideband Mode"] = (
                    "Enabled" if wideband_enabled else "Disabled", "")
            except Exception as error:
                wideband_enabled = False
                status_values["Wideband Mode"] = ("Unavailable", "")
                with self._lock:
                    self._errors.append(
                        f"MS41.3 wideband status unavailable ({error}); using standard profile")

            try:
                nb_flags = self._read_word_le(_NARROWBAND_EMULATION_ADDR)
                status_values["Narrowband Emulation"] = (
                    "Enabled" if nb_flags & _NARROWBAND_EMULATION_MASK else "Disabled", "")
            except Exception as error:
                status_values["Narrowband Emulation"] = ("Unavailable", "")
                with self._lock:
                    self._errors.append(f"Narrowband-emulation status unavailable ({error})")

            if wideband_enabled:
                profile = PROFILE_WIDEBAND
                try:
                    selected = self._read_word_le(_WBO2_INPUT_SELECT_DS2_ADDR)
                    if selected == 0xFFFF or not 0xE000 <= selected <= 0xFFFF:
                        raise ValueError(f"invalid input pointer 0x{selected:04X}")
                    input_addr = selected
                    source = _WBO2_INPUT_SOURCES.get(selected, f"RAM 0x{selected:04X}")
                    status_values["Wideband Input Source"] = (
                        f"{source} (0x{selected:04X})", "")
                except Exception as error:
                    input_addr = _DEFAULT_WBO2_INPUT_ADDR
                    status_values["Wideband Input Source"] = (
                        "Front O2 Bank 1 fallback (0xFA9A)", "")
                    with self._lock:
                        self._errors.append(
                            f"Wideband input selection unavailable ({error}); "
                            "showing the Front O2 Bank 1 input")

        key = self._ecu_id
        cut_revision = None
        launch_revision = None
        self._alphan_extended_active = False
        if str(key) == "SHINDE1" and self._ds2 is not None:
            try:
                if detect_alphan_failsafe_runtime(self._ds2, key):
                    scale = bytes(self._ds2.read_mem(0x133BF, 1))
                    if len(scale) != 1:
                        raise ValueError("short load-scaling configuration read")
                    self._alphan_extended_active = scale[0] & 3 == 2
            except Exception as error:
                with self._lock:
                    self._errors.append(f"Extended AlphaN channels unavailable ({error})")
        self._cut_patch_active = False
        if self._ds2 is not None and str(key) in _CUT_PATCH_IDS:
            try:
                cut_revision = detect_cut_runtime(self._ds2, key)
                self._cut_patch_active = cut_revision is not None
            except Exception as error:
                with self._lock:
                    self._errors.append(
                        f"Cut-patch runtime status unavailable ({error})")
            if self._cut_patch_active:
                try:
                    launch_revision = detect_launch_runtime(self._ds2, key)
                except Exception as error:
                    with self._lock:
                        self._errors.append(f"Launch-patch runtime status unavailable ({error})")
        self._profile = profile
        self._wideband_input_addr = input_addr
        self._configure_parameters(telegram_params_for(
            key, profile, input_addr, include_cut=self._cut_patch_active,
            definition=self._logger_definition, cut_revision=cut_revision,
            include_launch=launch_revision is not None and launch_revision == cut_revision,
            include_alphan=self._alphan_extended_active))
        self._available_status = dict(status_values)
        active_names = {
            p.name for p in self._tel_params if p.name in PROFILE_DISPLAY_NAMES
        }
        selected_status = {name: value for name, value in status_values.items()
                           if self._selected_ids is None or any(
                               key in self._selected_ids and name == status_name
                               for key, status_name in _PROFILE_STATUS_IDS.items())}
        active_names.update(selected_status)
        self._active_profile_names = active_names
        with self._lock:
            self._latest.update(selected_status)
        self._profile_ready = True

    def _poll_loop(self):
        # Live data is read over DS2: batch telegram (0x0B/0x01) or individual
        # block reads (0x06).  (MS41 has no working KWP2000 path.)
        failure = None
        try:
            if self._use_telegram:
                self._poll_loop_ds2_batch()
            else:
                self._poll_loop_ds2_reads()
        except Exception as error:
            failure = error
        finally:
            try:
                self._close_csv()
            except Exception as error:
                failure = failure or error
            if failure is not None:
                message = f"Live Data stopped: {failure}"
                with self._lock:
                    self._terminal_error = self._terminal_error or message
                    self._errors.append(message)
                self._stop.set()

    def _poll_loop_ds2_reads(self):
        """DS2 standard mode: individual block reads via DS2 cmd 0x06 (READ_MEM).

        Groups nearby RAM addresses into contiguous blocks and issues one read_mem
        per block.  More round-trips than the batch telegram, but simpler and more
        reliable.  It uses the same addresses and scaling as batch mode.
        """
        self._prepare_live_profile()
        self._require_measurement_selection(self._tel_params)
        self._acquisition_mode = "standard"
        self._ensure_csv()

        while not self._stop.is_set():
            cycle_started = time.monotonic()
            csv_row = self._csv_row_base()
            ram_snapshot = {}
            interrupted = False
            for block in self._tel_blocks:
                if self._stop.is_set():
                    interrupted = True
                    break
                try:
                    data = self._ds2.read_mem(block.start, block.size)
                    ram_snapshot.update((block.start + offset, value)
                                        for offset, value in enumerate(data))
                    with self._lock:
                        for tp in block.params:
                            if _is_ram_roughness_parameter(tp) or tp.id in _ALPHAN_EXTENDED_IDS:
                                continue  # Publish only after this cycle's validity reads.
                            val  = tp.parse(data, block.start)
                            disp = tp.display(val)
                            self._latest[tp.name] = (disp, tp.unit)
                            csv_row[tp.name] = disp
                except Exception as e:
                    with self._lock:
                        for tp in block.params:
                            self._latest[tp.name] = ("ERR", tp.unit)
                        self._errors.append(
                            f"DS2 read block 0x{block.start:05X}: {e}"
                        )

            if interrupted:
                break            # Stop pressed mid-cycle — don't log a partial row
            self._publish_ram_roughness(ram_snapshot, csv_row)
            self._publish_alphan_extended(ram_snapshot, csv_row)
            self._write_csv_row(csv_row)
            self._samples += 1
            self._stop.wait(max(0.0, self._interval - (time.monotonic() - cycle_started)))

    def _poll_loop_ds2_batch(self):
        """Register the selection once, then poll its exact variable-length reply."""
        self._prepare_live_profile()
        self._require_measurement_selection(self._tel_params)
        if not self._batch_entries:
            self._telegram_unavailable("Telegram logging is not mapped for this selection")
            return
        if self._selection_prefers_standard():
            self._poll_loop_ds2_reads()
            return
        self._acquisition_mode = "telegram"
        self._ensure_csv()
        try:
            self._ds2.setup_telegram_batch(
                ecu_id=self._ecu_id or "", entries=self._batch_entries)
        except Exception as error:
            self._telegram_unavailable(f"Telegram setup failed ({error})")
            return

        while not self._stop.is_set():
            cycle_started = time.monotonic()
            csv_row = self._csv_row_base()
            try:
                snapshot = _decode_batch_snapshot(
                    self._ds2.poll_telegram_batch(), self._batch_entries)
                interrupted = False
                for block in self._batch_supplements:
                    if self._stop.is_set():
                        interrupted = True
                        break
                    data = self._ds2.read_mem(block.start, block.size)
                    if len(data) != block.size:
                        raise ValueError(f"short supplemental RAM read at 0x{block.start:04X}")
                    snapshot.update((block.start + offset, value)
                                    for offset, value in enumerate(data))
                if interrupted:
                    break
                # Apply the same definition parser as READ_MEM after restoring
                # native byte order. Publish only a complete acquisition cycle.
                with self._lock:
                    for parameter in self._tel_params:
                        if (_is_ram_roughness_parameter(parameter)
                                or parameter.id in _ALPHAN_EXTENDED_IDS):
                            continue
                        data = bytes(snapshot[parameter.address + offset]
                                     for offset in range(parameter.length))
                        display = parameter.display(parameter.parse(data, parameter.address))
                        self._latest[parameter.name] = (display, parameter.unit)
                        csv_row[parameter.name] = display
                self._publish_ram_roughness(snapshot, csv_row)
                self._publish_alphan_extended(snapshot, csv_row)
            except Exception as error:
                with self._lock:
                    self._errors.append(f"DS2 batch poll: {error}")
                self._stop.wait(max(0.0, self._interval - (time.monotonic() - cycle_started)))
                continue
            self._write_csv_row(csv_row)
            self._samples += 1
            self._stop.wait(max(0.0, self._interval - (time.monotonic() - cycle_started)))

    def _publish_ram_roughness(self, ram_snapshot, csv_row):
        """Apply native producer eligibility to signed values from this cycle."""
        if not self._roughness_params:
            return
        required = _RAM_ROUGHNESS_GATES + tuple((p.address, p.length) for p in self._roughness_params)
        complete = all(address + offset in ram_snapshot
                       for address, length in required
                       for offset in range(length))
        available = (complete and
                     (ram_snapshot[0xDA2A] | ram_snapshot[0xDA2B] << 8) > 0 and
                     ram_snapshot[0xFD14] & 0x04 and
                     not ram_snapshot[0xFD30] & 0x0E)
        mask = ram_snapshot.get(0xE861, 0)
        with self._lock:
            for parameter in self._roughness_params:
                index = _RAM_ROUGHNESS_IDS[parameter.id]
                value = None
                if available and mask & (1 << index):
                    data = bytes(ram_snapshot[parameter.address + offset]
                                 for offset in range(parameter.length))
                    # FFFF is native signed -1 here, not a command-reply sentinel.
                    value = parameter.parse(data, parameter.address)
                display = parameter.display(value)
                self._latest[parameter.name] = (display, parameter.unit)
                csv_row[parameter.name] = "" if value is None else display

    def _publish_alphan_extended(self, ram_snapshot, csv_row):
        # FD22.9 SET selects Speed Density, whose path leaves these mirrors stale.
        available = 0xFD23 in ram_snapshot and not ram_snapshot[0xFD23] & 0x02
        with self._lock:
            for parameter in self._tel_params:
                if parameter.id not in _ALPHAN_EXTENDED_IDS:
                    continue
                value = None
                if available and all(parameter.address + offset in ram_snapshot
                                     for offset in range(parameter.length)):
                    raw = bytes(ram_snapshot[parameter.address + offset]
                                for offset in range(parameter.length))
                    value = parameter.parse(raw, parameter.address)
                display = parameter.display(value)
                self._latest[parameter.name] = (display, parameter.unit)
                csv_row[parameter.name] = "" if value is None else display

    def _telegram_unavailable(self, message):
        with self._lock:
            if self._telegram_fallback:
                self._errors.append(f"{message} — using standard DS2 reads")
            else:
                self._terminal_error = message
                self._errors.append(message)
        if self._telegram_fallback:
            self._poll_loop_ds2_reads()
        else:
            self._stop.set()

    @property
    def csv_rows(self) -> int:
        return self._csv_rows

    @property
    def sample_rate(self) -> float:
        """Completed acquisitions per second over the latest three seconds."""
        with self._lock:
            now = time.monotonic()
            recent = [stamp for stamp in self._sample_times if stamp >= now - 3.0]
        if len(recent) < 2:
            return 0.0
        elapsed = now - recent[0]
        return (len(recent) - 1) / elapsed if elapsed > 0 else 0.0

    def _open_csv(self, path: str):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self._csv_file  = open(path, "w", newline="", encoding="utf-8")
        self._csv_rows  = 0
        self._csv_last_flush = time.monotonic()
        if not self._sample_started:
            self._sample_started = time.monotonic()
        # Fixed schema regardless of polling mode so any CSV can be opened in MLV:
        #   Time     — elapsed seconds (float); MLV uses this as the primary time axis
        #   Datetime — human-readable full timestamp for post-processing reference
        #   then all standard params + telegram-only extras in display order
        param_cols = ([name for name, _unit in self.resolved_rows()] if self._selected_ids is not None
                      else self._log_columns or [name for name, _unit in self.resolved_rows()])
        headers = ["Time", "Datetime"] + list(param_cols)
        self._csv_writer = csv.DictWriter(
            self._csv_file, fieldnames=headers, extrasaction="ignore"
        )
        self._csv_writer.writeheader()

    def _ensure_csv(self):
        if self._pending_log_path and self._csv_file is None:
            path, self._pending_log_path = self._pending_log_path, None
            self._open_csv(path)

    def _csv_row_base(self) -> dict:
        """Seed profile status; timestamp the row only when acquisition completes."""
        row = {}
        with self._lock:
            for name in _PROFILE_STATUS_NAMES:
                if name in self._latest:
                    row[name] = self._latest[name][0]
        return row

    def _write_csv_row(self, row: dict):
        completed_at = time.monotonic()
        if not self._sample_started:
            self._sample_started = completed_at
        if self._sample_origin is None:
            self._sample_origin = completed_at
        # Share the CSV's millisecond time axis with the completed-sample history.
        elapsed = round(max(0.0, completed_at - self._sample_origin), 3)
        row["Time"] = f"{elapsed:.3f}"
        row["Datetime"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        wrote_csv = self._csv_writer is not None
        if self._csv_writer:
            self._csv_writer.writerow(row)
            # Buffer logger output to avoid a synchronous disk flush
            # in the serial acquisition loop for every sample, while still making an active
            # log visible on disk at least once per second.
            now = time.monotonic()
            if now - self._csv_last_flush >= 1.0:
                self._csv_file.flush()
                self._csv_last_flush = now
        channels = tuple(self.resolved_rows())
        with self._lock:
            if wrote_csv:
                self._csv_rows += 1
            self._sample_sequence += 1
            self._sample_channels = channels
            values = tuple(
                row.get(name) if row.get(name) not in (None, "") else None
                for name, _unit in channels
            )
            self._completed_samples.append(
                (self._sample_sequence, elapsed, values)
            )
            self._sample_times.append(completed_at)

    def _close_csv(self):
        self._pending_log_path = None
        stream, self._csv_file = self._csv_file, None
        self._csv_writer = None
        if stream:
            stream.close()


class RoughnessPoller:
    """Own the BMW START/read/STOP session on a single acquisition worker."""

    def __init__(self, ds2, ecu_variant, interval: float = 0.5):
        self._variant = _roughness_variant(ecu_variant)
        if interval <= 0:
            raise ValueError("roughness interval must be positive")
        self._ds2 = ds2
        self._interval = interval
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread = None
        self._latest = None
        self._errors = []
        self._readings = deque(maxlen=256)
        self._stop_confirmed = True
        self.cleanup_error = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        if not self._stop_confirmed:
            raise DS2Error("Roughness STOP is unconfirmed; retry Stop before starting again")
        self._stop.clear()
        self._stop_confirmed = False
        self.cleanup_error = None
        with self._lock:
            self._latest = None
            self._errors.clear()
            self._readings.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> bool:
        """Request worker cleanup and join; never perform caller-thread ECU I/O."""
        self._stop.set()
        thread = self._thread
        if thread and thread is not threading.current_thread():
            thread.join(timeout)
        return self.stop_confirmed

    @property
    def stop_confirmed(self) -> bool:
        """The worker exited and the ECU STOP was confirmed (or START declined)."""
        return not self.is_running and self._stop_confirmed

    def retry_stop(self) -> None:
        """Explicitly retry uncertain cleanup on a worker, never on the caller."""
        if self.is_running or self._stop_confirmed:
            return
        self._thread = threading.Thread(target=self._cleanup, daemon=True)
        self._thread.start()

    @property
    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def latest_reading(self) -> RoughnessReading | None:
        with self._lock:
            return self._latest

    def drain_readings(self) -> list[RoughnessReading | None]:
        """Consume each completed acquisition once; None marks a failed sample."""
        with self._lock:
            readings = list(self._readings)
            self._readings.clear()
            return readings

    def pop_errors(self) -> list[str]:
        with self._lock:
            errors, self._errors = self._errors, []
            return errors

    def _run(self) -> None:
        needs_stop = False
        try:
            if self._stop.is_set():
                self._stop_confirmed = True
                return
            needs_stop = True
            try:
                start_roughness_check(self._ds2, self._variant)
            except (DS2NegativeResponse, _RoughnessStartRejected):
                needs_stop = False
                self._stop_confirmed = True
                raise
            failures = 0
            while not self._stop.is_set():
                cycle_started = time.monotonic()
                try:
                    reading = read_roughness(self._ds2, self._variant)
                    with self._lock:
                        self._latest = reading
                        self._readings.append(reading)
                    failures = 0
                except Exception as error:
                    failures += 1
                    with self._lock:
                        self._latest = None
                        self._readings.append(None)
                        self._errors.append(f"Roughness read failed: {error}")
                    if failures >= 3:
                        break
                self._stop.wait(max(
                    0.0, self._interval - (time.monotonic() - cycle_started)))
        except Exception as error:
            with self._lock:
                self._latest = None
                self._readings.append(None)
                self._errors.append(f"Roughness start failed: {error}")
        finally:
            if needs_stop:
                self._cleanup()
            self._stop.set()

    def _cleanup(self) -> None:
        # Deliberately ignore the stop Event: it requests this very cleanup.
        try:
            stop_roughness_check(self._ds2, self._variant)
        except Exception as error:
            self._stop_confirmed = False
            self.cleanup_error = error
            with self._lock:
                self._errors.append(f"Roughness STOP unconfirmed: {error}")
        else:
            self.cleanup_error = None
            self._stop_confirmed = True
