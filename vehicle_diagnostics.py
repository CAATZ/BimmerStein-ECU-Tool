"""Self-contained BMW DS2 module discovery and fault-memory support.

The profiles, telegrams, record layouts, and text tables are built into the
application. Runtime operation uses only the existing DS2 connection; no
external diagnostic runtime or data file is loaded.
"""

from dataclasses import dataclass, field
import time

from ds2 import DS2Error, DS2Timeout


@dataclass(frozen=True)
class ModuleProfile:
    key: str
    name: str
    address: int

    @property
    def identify_frame(self) -> bytes:
        frame = bytes((self.address, 0x04, 0x00))
        return frame + bytes((frame[0] ^ frame[1] ^ frame[2],))


@dataclass(frozen=True)
class ModuleScanResult:
    profile: ModuleProfile
    response: bytes = b""
    error: str = ""

    @property
    def responded(self) -> bool:
        return len(self.response) >= 4

    @property
    def status(self):
        return self.response[2] if self.responded else None

    @property
    def status_text(self) -> str:
        if not self.responded:
            return self.error or "No response"
        return {
            0xA0: "Responded",
            0xA1: "Responded — busy",
            0xA2: "Responded — request rejected",
            0xB0: "Responded — invalid parameter",
            0xB1: "Responded — unsupported function",
            0xB2: "Responded — unsupported number",
            0xFF: "Responded — negative acknowledgement",
        }.get(self.status, f"Responded — status 0x{self.status:02X}")


# Exact concept-6 modules supported by the current single K-line transport.
MODULE_PROFILES = (
    ModuleProfile("dme", "Engine ECU", 0x12),
    ModuleProfile("egs", "Automatic Transmission", 0x32),
    ModuleProfile("ews", "Immobilizer", 0x44),
    ModuleProfile("abs", "ABS / Traction Control", 0x56),
    ModuleProfile("ihka", "Climate Control", 0x5B),
    ModuleProfile("fgr", "Cruise Control", 0xA6),
)
PROFILE_BY_KEY = {profile.key: profile for profile in MODULE_PROFILES}


def scan_modules(ds2, *, pause: float = 0.1) -> list[ModuleScanResult]:
    """Identify each curated module, preserving no-response vs rejection."""
    results = []
    for index, profile in enumerate(MODULE_PROFILES):
        try:
            response = bytes(ds2.send_frame(
                profile.identify_frame, resp_addr=profile.address, timeout=2.0))
            if len(response) < 4:
                raise DS2Error(
                    f"invalid {profile.name} response length {len(response)}")
            results.append(ModuleScanResult(profile, response=response))
        except DS2Timeout:
            results.append(ModuleScanResult(profile, error="No response"))
        except DS2Error as error:
            results.append(ModuleScanResult(
                profile, error=f"Communication error: {error}"))
        if pause and index + 1 < len(MODULE_PROFILES):
            time.sleep(pause)
    return results


@dataclass(frozen=True)
class ModuleFault:
    """One module fault, shaped for the existing diagnostics table/export."""

    profile_key: str
    module_name: str
    code: int
    description: str
    raw_record: bytes
    status: int = 0
    frequency: int | None = None
    speed_kmh: float | None = None
    conditions: tuple[str, ...] = ()
    unknown_status_bits: int = 0
    environment_raw: bytes = b""
    current: bool = False
    state_text: str = ""
    reported_total: int | None = None

    @property
    def code_hex(self) -> str:
        return f"0x{self.code:02X}"

    @property
    def sae_code(self) -> str:
        return "-"

    @property
    def system(self) -> str:
        return self.module_name

    @property
    def status_text(self) -> str:
        text = self.state_text or ("Current" if self.current else "Stored")
        if self.frequency is not None:
            text += f" · frequency {self.frequency}"
        return text

    @property
    def is_active(self) -> bool:
        return self.current

    @property
    def is_confirmed(self) -> bool:
        return False

    @property
    def is_pending(self) -> bool:
        return False

    @property
    def self_test_reason(self):
        return None


@dataclass(frozen=True)
class FaultProfile:
    key: str
    name: str
    module_key: str
    address: int
    parser: str
    read_frame: bytes
    clear_frame: bytes
    fault_names: dict[int, str] = field(default_factory=dict, repr=False)
    wake_frame: bytes = b""


_ASC_COMMON = {
    0x04: "Left-rear wheel-speed sensor",
    0x05: "Right-rear wheel-speed sensor",
    0x06: "Right-front wheel-speed sensor",
    0x07: "Left-front wheel-speed sensor",
    0x0E: "Valve-relay fault",
    0x0F: "ABS return-pump fault",
    0x15: "Control-unit fault",
    0x17: "ASC variant coding",
    0x18: "Incorrect toothed wheel on one of the four wheels",
    0x19: "Brake-light-switch open circuit",
    0x1E: "Left-rear wheel-speed sensor",
    0x1F: "Right-rear wheel-speed sensor",
    0x20: "Right-front wheel-speed sensor",
    0x21: "Left-front wheel-speed sensor",
    0x22: "ASC changeover valve",
    0x2F: "ABS outlet valve, left rear or rear axle",
    0x30: "ABS outlet valve, right rear",
    0x31: "ABS outlet valve, right front",
    0x32: "ABS outlet valve, left front",
    0x33: "ABS inlet valve, left rear or rear axle",
    0x34: "ABS inlet valve, right rear",
    0x35: "ABS inlet valve, right front",
    0x36: "ABS inlet valve, left front",
    0x37: "ASC shutoff valve",
    0x3F: "V comparison",
    0x40: "Continuous control / actuation",
    0x42: "Wheel-speed-sensor supply voltage / control-unit fault",
}
_ASC5 = {
    **_ASC_COMMON,
    0x03: "Ignition-timing adjustment",
    0x14: "Transmission intervention",
    0x16: "TD signal fault",
    0x1B: "Idle-speed-increase feedback",
    0x23: "SN switch (brake-fluid level)",
    0x24: "Ignition suppression",
    0x25: "Control-unit fault (ADS section)",
    0x26: "Position controller (ADS section)",
    0x27: "Actuator motor (ADS section)",
    0x28: "Throttle-valve potentiometer (ADS section)",
    0x38: "General CAN fault",
    0x39: "DKI signal from Motronic (ADS section)",
    0x3A: "CAN EGS1 message fault",
    0x3B: "CAN DMER1 message fault",
    0x43: "Temporary interference",
    0x44: "ASR passive after Uz due to interference",
    0x47: "Motor-relay service-life monitoring",
    0x49: "CAN WDKBL signal fault",
    0x4A: "CAN MD_IND signal fault",
    0x4B: "CAN NMOT fault",
}
_DSC5 = {
    **_ASC_COMMON,
    0x02: "EML fault",
    0x29: "Steering angle",
    0x38: "CAN fault",
    0x3A: "EGS message fault",
    0x3B: "DMER1 message fault",
    0x45: "DMER2 message fault",
    0x46: "Trailer-hitch (AHK) fault",
}

_ABS_MK20 = {
    0x11: "Front-left wheel-speed sensor trigger signal",
    0x12: "Front-left wheel-speed sensor continuity",
    0x14: "Front-left wheel-speed sensor drive-off detection",
    0x15: "Outlet-valve monitoring via front-left wheel-speed sensor",
    0x21: "Front-right wheel-speed sensor trigger signal",
    0x22: "Front-right wheel-speed sensor continuity",
    0x24: "Front-right wheel-speed sensor drive-off detection",
    0x25: "Outlet-valve monitoring via front-right wheel-speed sensor",
    0x31: "Rear-left wheel-speed sensor trigger signal",
    0x32: "Rear-left wheel-speed sensor continuity",
    0x34: "Rear-left wheel-speed sensor drive-off detection",
    0x35: "Outlet-valve monitoring via rear-left wheel-speed sensor",
    0x41: "Rear-right wheel-speed sensor trigger signal",
    0x42: "Rear-right wheel-speed sensor continuity",
    0x44: "Rear-right wheel-speed sensor drive-off detection",
    0x45: "Outlet-valve monitoring via rear-right wheel-speed sensor",
    0x51: "ABS inlet valve, front left",
    0x52: "ABS inlet valve, front right",
    0x53: "ABS inlet valve, rear",
    0x55: "ABS outlet valve, front left",
    0x56: "ABS outlet valve, front right",
    0x57: "ABS outlet valve, rear",
    0x71: "Pump motor",
    0x73: "Internal IC fault",
    0x76: "Wheel-speed-sensor wiring fault",
    0x78: "Voltage above 18 V",
    0x81: "Main relay",
    0x82: "Valve supply voltage",
    0x83: "Valve leakage current",
    0x85: "Valve-coil voltage",
}
_ASC_MK20 = {
    **_ABS_MK20,
    0x15: "ABS outlet-valve monitoring via front-left wheel-speed sensor",
    0x25: "ABS outlet-valve monitoring via front-right wheel-speed sensor",
    0x35: "ABS outlet-valve monitoring via rear-left wheel-speed sensor",
    0x45: "ABS outlet-valve monitoring via rear-right wheel-speed sensor",
    0x53: "ABS inlet valve, rear left",
    0x54: "ABS inlet valve, rear right",
    0x57: "ABS outlet valve, rear left",
    0x58: "ABS outlet valve, rear right",
    0x61: "ASC special valve 1",
    0x67: "Brake-light switch",
    0x71: "Pump motor, valve block, wiring harness",
    0x73: "Control-unit fault",
    0x76: "Control-unit fault; wheel-speed-sensor interference",
    0x78: "Vehicle-system voltage above 18 V",
    0x81: "Main relay in control unit",
    0x82: "Reference-voltage fault",
    0x83: "Valve leakage current",
    0x85: "Valve coil, overvoltage",
    0x91: "Internal CAN-controller fault",
    0x92: "CAN-bus fault",
    0x93: "Implausible CAN DME/DDE data",
    0x94: "CAN DME/DDE: engine torque cannot be set",
    0x95: "CAN timeout, DME/DDE",
    0x96: "CAN timeout, EGS",
    0x97: "Coding fault",
    0x98: "ASC button",
}

_FGR_COMMON = {
    0x01: "Watchdog system fault",
    0x02: "RAM initialization fault",
    0x06: "Minimum-voltage hardware/software plausibility fault",
    0x10: "Clutch-state vs KU+ plausibility fault",
    0x11: "Actuator exceeded maximum shutdown time",
    0x12: "Control-loop monitoring fault",
    0x13: "P+ voltage outside valid range",
    0x21: "Toggle-bit fault",
    0xFF: "Unknown fault location",
}
_FGR2 = {
    **_FGR_COMMON,
    0x03: "BMW coding-data checksum invalid",
    0x04: "VDO coding-data checksum invalid",
    0x05: "Implausible main-switch input detection",
    0x07: "Hardware/software shutdown-memory plausibility fault",
}
_GR2 = {
    **_FGR_COMMON,
    0x03: "Coding-data checksum invalid",
    0x05: "Implausible main-switch input detection",
    0x07: "Hardware/software shutdown-memory plausibility fault",
    0x30: "Coding-data pointer out of range",
}
_FGR25 = {
    **_FGR_COMMON,
    0x03: "BMW coding-data checksum invalid",
    0x04: "VDO coding-data checksum invalid",
    0x05: "Main-switch coding fault (ZYL_ZAHL / MAIN_SWITCH)",
}

_EWS_SPECIAL = {
    0xFF: "General reset",
    0x0F: "Power-on reset",
    0x1F: "Clock-monitor reset",
    0x2F: "Watchdog reset",
    0x3F: "Illegal-opcode trap",
    0x7F: "Software interrupt",
    0x8F: "Illegal-opcode trap",
    0x0E: "Engine ECU random-code XOR error",
    0x1E: "Engine ECU random code lost",
}
_EWS_KEY_FAULTS = {
    0x0: "identification failed",
    0x1: "incorrect password",
    0x2: "incorrect random code",
    0x3: "random-code tolerance increased",
    0x4: "communication with transmitter/receiver module failed",
}
_EGS_QUALIFIERS = {
    0x0: "No additional qualifier",
    0x1: "Plausibility fault",
    0x2: "Short circuit to battery positive",
    0x3: "Short circuit to ground",
    0x4: "Open circuit",
    0x5: "Open circuit or short circuit to battery positive",
    0x6: "Open circuit or short circuit to ground",
}

_ABS_WAKE = bytes.fromhex("56 04 00 52")
_ABS_READ = bytes.fromhex("56 05 04 01 56")
_ABS_CLEAR = bytes.fromhex("56 04 05 57")
_FGR_READ = bytes.fromhex("A6 05 04 01 A6")
_FGR_CLEAR = bytes.fromhex("A6 04 05 A7")
_IHKA_READ_1 = bytes.fromhex("5B 05 04 01 5B")
_IHKA_READ_3 = bytes.fromhex("5B 05 04 03 59")
_IHKA_READ_5 = bytes.fromhex("5B 05 04 05 5F")
_IHKA_CLEAR = bytes.fromhex("5B 04 05 5A")
_EWS_READ_COUNT = bytes.fromhex("44 05 04 00 45")
_EWS_READ_RECORDS = bytes.fromhex("44 05 04 01 44")
_EWS_CLEAR = bytes.fromhex("44 04 05 45")
_EGS_READ = bytes.fromhex("32 05 04 01 32")
_EGS_CLEAR = bytes.fromhex("32 04 05 33")

FAULT_PROFILES = (
    FaultProfile("egs_gs832", "Transmission — GS 8.32", "egs", 0x32, "egs",
                 _EGS_READ, _EGS_CLEAR),
    FaultProfile("egs_gs855", "Transmission — GS 8.55", "egs", 0x32, "egs",
                 _EGS_READ, _EGS_CLEAR),
    FaultProfile("ews", "Immobilizer — EWS II / III", "ews", 0x44, "ews",
                 _EWS_READ_COUNT, _EWS_CLEAR),
    FaultProfile("ihka", "Climate Control (generic)", "ihka", 0x5B, "ihka",
                 _IHKA_READ_1, _IHKA_CLEAR),
    FaultProfile("asc5", "ASC 5", "abs", 0x56, "asc5",
                 _ABS_READ, _ABS_CLEAR, _ASC5, _ABS_WAKE),
    FaultProfile("dsc5", "DSC 5", "abs", 0x56, "asc5",
                 _ABS_READ, _ABS_CLEAR, _DSC5, _ABS_WAKE),
    FaultProfile("abs_mk20", "ABS MK20", "abs", 0x56, "abs_mk20",
                 _ABS_READ, _ABS_CLEAR, _ABS_MK20, _ABS_WAKE),
    FaultProfile("asc_mk20", "ASC MK20", "abs", 0x56, "asc_mk20",
                 _ABS_READ, _ABS_CLEAR, _ASC_MK20, _ABS_WAKE),
    FaultProfile("fgr", "Cruise Control (generic)", "fgr", 0xA6, "fgr",
                 _FGR_READ, _FGR_CLEAR, _FGR_COMMON),
    FaultProfile("fgr2", "Cruise Control FGR2", "fgr", 0xA6, "fgr",
                 _FGR_READ, _FGR_CLEAR, _FGR2),
    FaultProfile("gr2", "Cruise Control GR2", "fgr", 0xA6, "fgr",
                 _FGR_READ, _FGR_CLEAR, _GR2),
    FaultProfile("fgr2_5", "Cruise Control FGR2.5", "fgr", 0xA6, "fgr",
                 _FGR_READ, _FGR_CLEAR, _FGR25),
)
PROFILE_BY_FAULT_KEY = {profile.key: profile for profile in FAULT_PROFILES}


def _profile(profile_key: str) -> FaultProfile:
    try:
        return PROFILE_BY_FAULT_KEY[profile_key]
    except KeyError:
        raise KeyError(f"unknown fault profile {profile_key!r}") from None


def _send_a0(ds2, profile: FaultProfile, frame: bytes) -> tuple[bytes, bytes]:
    response = bytes(ds2.send_frame(
        frame, resp_addr=profile.address, timeout=2.0))
    if len(response) < 4:
        raise DS2Error(
            f"invalid {profile.name} response length {len(response)}")
    if response[2] != 0xA0:
        raise DS2Error(
            f"{profile.name} rejected request with status 0x{response[2]:02X}")
    return response, response[3:-1]


def _fault(profile, record, **values) -> ModuleFault:
    code = record[0]
    return ModuleFault(
        profile.key,
        profile.name,
        code,
        profile.fault_names.get(code, f"Unknown fault location 0x{code:02X}"),
        bytes(record),
        **values,
    )


def _length(profile, payload, expected) -> None:
    if len(payload) != expected:
        raise DS2Error(
            f"invalid {profile.name} fault payload length {len(payload)}; "
            f"expected {expected}")


def _parse_asc5(profile, payload) -> list[ModuleFault]:
    if not payload:
        raise DS2Error(f"empty {profile.name} fault payload")
    _length(profile, payload, 1 + payload[0] * 5)
    faults = []
    for offset in range(1, len(payload), 5):
        record = payload[offset:offset + 5]
        flags = record[1]
        faults.append(_fault(
            profile, record, status=flags, frequency=record[2],
            speed_kmh=((record[3] << 8) | record[4]) / 16.0,
            conditions=(
                "ASC not passive" if flags & 0x10 else "ASC passive",
                "ABS regulation active" if flags & 0x20
                else "ABS regulation inactive",
                "Brake-light switch pressed" if flags & 0x40
                else "Brake-light switch not pressed",
                "ASC regulation active" if flags & 0x80
                else "ASC regulation inactive",
            ),
            unknown_status_bits=flags & 0x0F,
            environment_raw=record[3:5],
        ))
    return faults


def _parse_abs_mk20(profile, payload) -> list[ModuleFault]:
    _length(profile, payload, 9)
    faults = []
    for offset in range(0, 9, 3):
        record = payload[offset:offset + 3]
        if record[0] == 0:
            continue
        env = record[2]
        faults.append(_fault(
            profile, record, status=env, frequency=record[1],
            speed_kmh=(env & 0x1F) * 10.0,
            conditions=(
                "ABS regulation active" if env & 0x20
                else "ABS regulation inactive",
                "Brake-light switch pressed" if env & 0x40
                else "Brake-light switch not pressed",
                "Undervoltage detected" if env & 0x80
                else "No undervoltage detected",
            ),
            environment_raw=record[2:3],
        ))
    return faults


def _parse_asc_mk20(profile, payload) -> list[ModuleFault]:
    _length(profile, payload, 9)
    faults = []
    for offset in range(0, 9, 3):
        record = payload[offset:offset + 3]
        if record[0] == 0 or record[1] == 0:
            continue
        env = record[2]
        faults.append(_fault(
            profile, record, status=env, frequency=0xFF - record[1],
            speed_kmh=(env & 0x1F) * 10.0,
            conditions=(
                "Regulation active" if env & 0x20 else "Regulation inactive",
                "Brake-light switch pressed" if env & 0x40
                else "Brake-light switch not pressed",
            ),
            unknown_status_bits=env & 0x80,
            environment_raw=record[2:3],
        ))
    return faults


def _parse_fgr(profile, payload) -> list[ModuleFault]:
    if not payload:
        raise DS2Error(f"empty {profile.name} fault payload")
    _length(profile, payload, 1 + payload[0] * 2)
    return [
        _fault(profile, payload[i:i + 2], frequency=payload[i + 1])
        for i in range(1, len(payload), 2)
    ]


def _ews_description(code: int) -> str:
    if code in _EWS_SPECIAL:
        return _EWS_SPECIAL[code]
    fault = _EWS_KEY_FAULTS.get(code & 0x0F)
    if fault is None or code >> 4 > 9:
        return f"Immobilizer fault 0x{code:02X}"
    return f"Key {(code >> 4) + 1}: {fault}"


def _parse_ews(profile, count_payload, record_payload) -> list[ModuleFault]:
    if not count_payload:
        raise DS2Error("empty Immobilizer fault-count payload")
    count = count_payload[0]
    if count == 0:
        return []
    expected = 1 + count * 2
    if len(record_payload) < expected:
        raise DS2Error(
            f"invalid Immobilizer fault payload length {len(record_payload)}; "
            f"expected at least {expected}")
    faults = []
    for offset in range(1, expected, 2):
        record = record_payload[offset:offset + 2]
        meta = record[1]
        state = "Static" if meta & 0x20 else "Intermittent / sporadic"
        faults.append(ModuleFault(
            profile.key, profile.name, record[0], _ews_description(record[0]),
            bytes(record), status=meta, frequency=meta & 0x1F,
            conditions=(state,), unknown_status_bits=meta & 0xC0,
            current=bool(meta & 0x20), state_text=state, reported_total=count,
        ))
    return faults


def _parse_egs(profile, payload) -> list[ModuleFault]:
    if not payload:
        raise DS2Error("empty Automatic Transmission fault payload")
    reported_count = payload[0]
    count = min(reported_count, 5)
    expected = 1 + count * 19
    if len(payload) < expected:
        raise DS2Error(
            f"invalid Automatic Transmission fault payload length "
            f"{len(payload)}; expected at least {expected}")
    faults = []
    for offset in range(1, expected, 19):
        record = payload[offset:offset + 19]
        fault_type = record[1]
        qualifier = fault_type & 0x0F
        conditions = [_EGS_QUALIFIERS.get(
            qualifier, f"Unknown qualifier 0x{qualifier:X}")]
        conditions.extend(text for bit, text in (
            (0x10, "Fault present after start"),
            (0x20, "Intermittent"),
            (0x40, "Substitute / failsafe function active"),
            (0x80, "Currently present"),
        ) if fault_type & bit)
        conditions.append(f"CARB count: {record[3]}")
        for index in range(min(record[2], 3)):
            start = 4 + index * 5
            values = record[start:start + 3].hex(" ").upper()
            hours = int.from_bytes(record[start + 3:start + 5], "big")
            conditions.append(
                f"Snapshot {index + 1}: values {values}; operating hours {hours}")
        faults.append(ModuleFault(
            profile.key, profile.name, record[0],
            f"Transmission fault 0x{record[0]:02X}", bytes(record),
            status=fault_type, frequency=record[2],
            conditions=tuple(conditions),
            unknown_status_bits=(qualifier if qualifier not in _EGS_QUALIFIERS else 0),
            environment_raw=record[4:], current=bool(fault_type & 0x80),
            state_text=("Current" if fault_type & 0x80 else
                        "Intermittent" if fault_type & 0x20 else "Stored"),
            reported_total=reported_count,
        ))
    return faults


def _parse_ihka_page(profile, payload, first_index, expected_count=None):
    if not payload:
        raise DS2Error("empty Climate Control fault payload")
    count = payload[0]
    if count > 6:
        raise DS2Error(f"invalid Climate Control fault count {count}; maximum is 6")
    if expected_count is not None and count != expected_count:
        raise DS2Error(
            "Climate Control fault count changed between pages "
            f"({expected_count} to {count})")
    page_count = min(2, max(0, count - first_index + 1))
    _length(profile, payload, 1 + page_count * 11)
    faults = []
    for offset in range(1, len(payload), 11):
        record = payload[offset:offset + 11]
        flags = record[1]
        conditions = tuple(text for bit, text in (
            (0x01, "Short circuit to battery positive"),
            (0x02, "Short circuit to ground"),
            (0x04, "Open circuit"),
            (0x08, "Implausible / out of range"),
            (0x40, "Current"),
            (0x80, "Intermittent"),
        ) if flags & bit)
        faults.append(_fault(
            profile, record, status=flags, frequency=record[2],
            conditions=conditions, unknown_status_bits=flags & 0x30,
            environment_raw=record[3:11], current=bool(flags & 0x40),
        ))
    return count, faults


_PARSERS = {
    "asc5": _parse_asc5,
    "abs_mk20": _parse_abs_mk20,
    "asc_mk20": _parse_asc_mk20,
    "egs": _parse_egs,
    "fgr": _parse_fgr,
}


def read_module_faults(ds2, profile_key: str) -> list[ModuleFault]:
    """Read and decode one exact non-DME fault profile."""
    profile = _profile(profile_key)
    if profile.parser == "ews":
        _, count_payload = _send_a0(ds2, profile, _EWS_READ_COUNT)
        if not count_payload:
            raise DS2Error("empty Immobilizer fault-count payload")
        if count_payload[0] == 0:
            return []
        _, record_payload = _send_a0(ds2, profile, _EWS_READ_RECORDS)
        return _parse_ews(profile, count_payload, record_payload)

    if profile.parser == "ihka":
        _, payload = _send_a0(ds2, profile, _IHKA_READ_1)
        count, faults = _parse_ihka_page(profile, payload, 1)
        if count > 2:
            _, payload = _send_a0(ds2, profile, _IHKA_READ_3)
            _, page = _parse_ihka_page(profile, payload, 3, count)
            faults.extend(page)
        if count > 4:
            _, payload = _send_a0(ds2, profile, _IHKA_READ_5)
            _, page = _parse_ihka_page(profile, payload, 5, count)
            faults.extend(page)
        return faults

    if profile.wake_frame:
        _send_a0(ds2, profile, profile.wake_frame)
    _, payload = _send_a0(ds2, profile, profile.read_frame)
    return _PARSERS[profile.parser](profile, payload)


def clear_module_faults(ds2, profile_key: str) -> bytes:
    """Clear one exact non-DME profile, without retrying a busy ECU."""
    profile = _profile(profile_key)
    if profile.wake_frame:
        _send_a0(ds2, profile, profile.wake_frame)
    response, _ = _send_a0(ds2, profile, profile.clear_frame)
    return response
