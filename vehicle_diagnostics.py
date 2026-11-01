"""Self-contained BMW DS2 module discovery and fault-memory support.

The profiles, telegrams, record layouts, and text tables are built into the
application. Runtime operation uses only the existing DS2 connection; no
external diagnostic runtime or data file is loaded.
"""

from dataclasses import dataclass, field, replace
import time

from ds2 import DS2Error, DS2Timeout


@dataclass(frozen=True)
class ModuleProfile:
    key: str
    name: str
    address: int
    bmw_fast: bool = False

    @property
    def identify_frame(self) -> bytes:
        if self.bmw_fast:
            return bytes.fromhex("B8 29 F1 02 1A 80")
        frame = bytes((self.address, 0x04, 0x00))
        return frame + bytes((frame[0] ^ frame[1] ^ frame[2],))


@dataclass(frozen=True)
class ModuleScanResult:
    profile: ModuleProfile
    response: bytes = b""
    error: str = ""

    @property
    def responded(self) -> bool:
        return len(self.response) >= (6 if self.profile.bmw_fast else 4)

    @property
    def status(self):
        return self.response[4 if self.profile.bmw_fast else 2] if self.responded else None

    @property
    def status_text(self) -> str:
        if not self.responded:
            return self.error or "No response"
        if self.profile.bmw_fast:
            if self.response[4:6] == b"\x5A\x80":
                return "Responded"
            if self.status == 0x7F and len(self.response) >= 8:
                return f"Responded — request rejected (0x{self.response[6]:02X})"
            return f"Responded — service 0x{self.status:02X}"
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
    ModuleProfile("dsc_mk60", "DSC MK60", 0x29, bmw_fast=True),
)
PROFILE_BY_KEY = {profile.key: profile for profile in MODULE_PROFILES}


def scan_modules(ds2, *, pause: float = 0.1) -> list[ModuleScanResult]:
    """Identify each curated module, preserving no-response vs rejection."""
    results = []
    for index, profile in enumerate(MODULE_PROFILES):
        try:
            if profile.bmw_fast:
                response = bytes(ds2.send_bmw_fast(
                    profile.identify_frame, target=profile.address, timeout=2.0))
            else:
                response = bytes(ds2.send_frame(
                    profile.identify_frame, resp_addr=profile.address, timeout=2.0))
            if len(response) < (6 if profile.bmw_fast else 4):
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

_DSC57 = {
    **_ASC_COMMON,
    0x02: "CAN engine-power control",
    0x04: "Left-rear wheel-speed sensor plausibility",
    0x05: "Right-rear wheel-speed sensor plausibility",
    0x06: "Right-front wheel-speed sensor plausibility",
    0x07: "Left-front wheel-speed sensor plausibility",
    0x17: "Coding fault",
    0x1E: "Left-rear wheel-speed sensor open circuit",
    0x1F: "Right-rear wheel-speed sensor open circuit",
    0x20: "Right-front wheel-speed sensor open circuit",
    0x21: "Left-front wheel-speed sensor open circuit",
    0x22: "Front-axle ASC changeover valve",
    0x29: "Steering-angle plausibility",
    0x2F: "ABS outlet valve, left rear",
    0x33: "ABS inlet valve, left rear",
    0x37: "Front-axle precharge valve",
    0x38: "CAN communication fault",
    0x3A: "Transmission CAN message fault",
    0x3B: "Engine CAN DMER1 message fault",
    0x3F: "Vehicle-speed comparison",
    0x42: "Active wheel-speed-sensor supply voltage",
    0x44: "DSC passive after Uz due to interference",
    0x45: "Engine CAN DMER2 message fault",
    0x51: "Brake-pressure sensor open circuit",
    0x52: "Yaw-rate sensor open circuit",
    0x53: "Lateral-acceleration sensor open circuit",
    0x54: "Yaw-rate sensor plausibility",
    0x56: "Rear-axle ASC changeover valve",
    0x57: "Rear-axle precharge valve",
    0x58: "Precharge pump",
    0x59: "Undervoltage",
    0x5A: "Temporary deactivation",
    0x5B: "Steering-angle message fault",
    0x5C: "Brake-pressure sensor / precharge-pump plausibility",
    0x5E: "Yaw-rate sensor",
    0x5F: "Signal interference",
    0x60: "Actuation fault",
    0x61: "Steering-angle calibration required",
    0x62: "Emergency braking without pressure",
    0x63: "Continuous control / actuation",
    0x64: "Lateral-acceleration sensor",
    0x67: "Steering-angle sensor initialization algorithm",
    0x68: "Steering-angle control-unit fault",
    0x69: "Brake-light-switch plausibility",
    0x6A: "Lateral-acceleration sensor plausibility",
    0x6B: "Yaw-rate sensor",
    0x6C: "SN monitoring",
    0x6D: "End-of-line test",
    0x6E: "Overvoltage",
    0x6F: "ASC acknowledgement",
    0x72: "Brake-pressure sensor offset",
    0x73: "Brake-pressure sensor interference",
    0x74: "Brake-switch fault",
    0x75: "Brake-light switch permanently active",
    0x76: "Engine control unit reports internal fault",
    0x77: "Hill-descent control unavailable",
    0x78: "Yaw-rate sensor mounting position suspect",
    0x79: "Incorrect yaw-rate sensor mounting position",
    0x7A: "Adaptive cruise control CAN message fault",
    0x7B: "Adaptive cruise control deceleration request plausibility",
    0x7C: "Adaptive cruise control switched off",
    0x7D: "Adaptive cruise control status unchanged with DSC passive",
    0x7E: "Adaptive cruise control brake-light actuation fault",
    0x80: "Brake overheating",
    0x8A: "Air-suspension CAN message fault",
    0x8B: "Transfer-case low-range CAN message fault",
}

_GS8600 = {
    0x01: "Pressure regulator EDS 1",
    0x02: "Pressure regulator EDS 2",
    0x03: "Pressure regulator EDS 3",
    0x04: "Pressure regulator EDS 4",
    0x0F: "Combined EDS pressure-regulator current",
    0x10: "Solenoid valve 1",
    0x11: "Solenoid valve 2",
    0x12: "Solenoid valve 3",
    0x13: "Shift-lock solenoid",
    0x20: "Transmission output-speed signal",
    0x21: "Turbine-speed sensor",
    0x22: "Transmission oil-temperature sensor",
    0x30: "Torque-converter clutch excessive slip",
    0x31: "Gear-monitoring symptom",
    0x32: "Gear 1 monitoring",
    0x33: "Gear 1M monitoring",
    0x34: "Gear 2 monitoring",
    0x35: "Gear 3 monitoring",
    0x36: "Gear 4 monitoring",
    0x37: "Gear 5 monitoring",
    0x38: "GLUE monitoring symptom",
    0x39: "GLUE monitoring, gears 2/3",
    0x3A: "GLUE monitoring, gears 3/4",
    0x3B: "Stall speed",
    0x3C: "Transmission selector switch",
    0x3D: "Transmission temperature monitoring",
    0x3E: "Output-speed and wheel-speed signals both faulty",
    0x50: "Internal control-unit fault 1 (EPROM)",
    0x51: "Internal control-unit fault 2 (EEPROM)",
    0x52: "Internal control-unit fault 3 (watchdog)",
    0x53: "Internal control-unit fault 4 (FET)",
    0x60: "Battery supply, terminal 87",
    0x61: "Battery supply, terminal 30",
    0x70: "Transmission program button",
    0x71: "Kickdown switch",
    0x72: "Steptronic switch",
    0x80: "CAN-bus monitoring",
    0x81: "CAN timeout, engine control unit",
    0x82: "CAN timeout, traction control",
    0x90: "CAN version fault",
    0x91: "CAN torque reduction",
    0x93: "CAN throttle signal",
    0x95: "CAN wheel-speed signals",
    0x97: "CAN brake signal",
    0xFF: "Unknown fault location",
}
_GS8604 = {**_GS8600, 0x41: "Gear 4 monitoring (ENP)"}

_MK60 = {
    base + detail: f"{position} wheel-speed sensor: {symptom}"
    for base, position in (
        (0x5D90, "Left-front"), (0x5DA0, "Right-front"),
        (0x5DB0, "Left-rear"), (0x5DC0, "Right-rear"))
    for detail, symptom in enumerate((
        "electrical fault", "extrapolation", "periodic pulse-wheel monitoring",
        "drive-off speed comparison", "long-term monitoring", "double pulse-wheel frequency"))
}
_MK60.update({
    0x5D8C: "Sensor cluster CAN timeout",
    0x5D8E: "Incorrect sensor cluster",
    0x5D8F: "Sensor cluster internal fault",
    0x5D96: "Sensor cluster CAN bus off",
    0x5D97: "Sensor cluster supply voltage outside range",
    0x5D99: "Sensor cluster CAN fault",
    0x5DF0: "Pump motor",
    0x5DF2: "Valve/control-unit hardware or ROM/RAM check fault",
    0x5DF4: "System voltage below 9 V",
    0x5DF5: "Internal control-unit fault",
    0x5DF7: "System voltage above 18 V",
    0x5E00: "End-of-line test active",
    0x5E01: "End-of-line test timeout",
    0x5E02: "End-of-line test: yaw-rate sensor adjustment fault",
    0x5E03: "End-of-line test: yaw-rate sensor fault",
    0x5E04: "End-of-line test: lateral-acceleration sensor fault",
    0x5E05: "End-of-line test: lateral-acceleration and yaw-rate fault",
    0x5E06: "End-of-line test: yaw-rate sensor mounted incorrectly",
    0x5E07: "End-of-line test: lateral-acceleration and yaw-rate sensors mounted incorrectly",
    0x5E08: "End-of-line test: steering-angle sensor fault",
    0x5E11: "CAN bus off / internal CAN-controller fault",
    0x5E14: "CAN timeout, engine control unit",
    0x5E15: "CAN timeout, transmission control unit",
    0x5E16: "CAN timeout, instrument cluster",
    0x5E18: "Implausible engine CAN message",
    0x5E19: "Engine torque cannot be set via CAN",
    0x5E1A: "Engine CAN signal fault",
    0x5E1E: "CAN timeout, steering-angle sensor",
    0x5E1F: "VIN not initialized; steering-angle calibration required",
    0x5E20: "Brake-pressure sensor 1 electrical fault",
    0x5E21: "Brake-pressure sensor 2 electrical fault",
    0x5E24: "Brake-pressure sensors 1/2 implausible",
    0x5E26: "Sensor voltage supply",
    0x5E2F: "Temperature sensor",
    0x5E30: "Lateral-acceleration sensor electrical fault",
    0x5E32: "Lateral-acceleration sensor implausible",
    0x5E38: "Yaw-rate sensor electrical fault",
    0x5E3C: "Yaw-rate sensor implausible",
    0x5E40: "Steering-angle sensor signal implausible / offset",
    0x5E43: "Steering-angle sensor internal fault",
    0x5E4E: "DSC sensor offset check",
    0x5E4F: "Continuous DSC regulation",
    0x5E58: "ASC control unit receives steering-angle CAN message",
    0x5E59: "Coding fault",
    0x5E5B: "DSC button held for over 10 seconds or faulty",
    0x5E5C: "Tire-pressure warning button fault",
    0x5E5D: "Brake-fluid-level switch fault",
    0x5E5E: "Brake-light-switch fault",
    0x5E5F: "Steering-wheel DSC Sport button fault",
})

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
_EGS86_QUALIFIERS = {
    **_EGS_QUALIFIERS,
    0x0: "General fault",
    0x7: "Value too high",
    0x8: "Value too low",
    0x9: "No change",
    0xA: "No matching fault code",
}
_GS20_QUALIFIERS = {
    **_EGS_QUALIFIERS,
    0x0: "General fault",
    0xA: "Stuck closed",
    0xB: "Stuck open",
    0xC: "Value too high",
    0xD: "Value too low",
}
_GS20 = {
    0x01: "Pressure regulator EDS 1",
    0x04: "Pressure regulator EDS 4",
    0x10: "Solenoid valve 1",
    0x11: "Solenoid valve 2",
    0x12: "Solenoid valve 3",
    0x13: "Shift-lock solenoid",
    0x20: "Transmission output-speed signal",
    0x21: "Turbine-speed sensor",
    0x22: "Transmission oil-temperature sensor",
    0x30: "Torque-converter clutch excessive slip",
    0x32: "Gear 1 monitoring",
    0x34: "Gear 2 monitoring",
    0x35: "Gear 3 monitoring",
    0x36: "Gear 4 monitoring",
    0x37: "Gear 5 monitoring",
    0x3B: "Stall speed",
    0x3C: "Transmission selector switch",
    0x3D: "Transmission oil aging",
    0x3F: "Wired brake signal",
    0x50: "Control-unit internal fault 1 (EPROM)",
    0x51: "Control-unit internal fault 2 (EEPROM)",
    0x52: "Control-unit internal fault 3 (watchdog)",
    0x53: "Control-unit internal fault 4 (FET1)",
    0x54: "Control-unit internal fault 5 (FET2)",
    0x60: "Terminal 87 supply voltage",
    0x70: "Transmission program switch",
    0x71: "Kick-down switch",
    0x72: "Steptronic switch",
    0x78: "Cruise control",
    0x80: "CAN bus monitoring",
    0x81: "CAN timeout from engine ECU",
    0x82: "CAN timeout from stability control",
    0x83: "CAN timeout from instrument cluster",
    0x90: "CAN version mismatch",
    0x91: "CAN torque reduction",
    0x92: "CAN torque interface",
    0x93: "CAN throttle signal",
    0x94: "CAN engine temperature",
    0x95: "CAN wheel speeds",
    0x96: "CAN engine speed",
    0x97: "CAN brake signal",
    0xFF: "Unknown fault location",
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
    FaultProfile("egs_gs8600", "Transmission — GS 8.60.0", "egs", 0x32, "egs86",
                 _EGS_READ, _EGS_CLEAR, _GS8600),
    FaultProfile("egs_gs8604", "Transmission — GS 8.60.4", "egs", 0x32, "egs86",
                 _EGS_READ, _EGS_CLEAR, _GS8604),
    FaultProfile("egs_gs20", "Transmission — GS20", "egs", 0x32, "gs20",
                 _EGS_READ, _EGS_CLEAR, _GS20),
    FaultProfile("ews", "Immobilizer — EWS II / III", "ews", 0x44, "ews",
                 _EWS_READ_COUNT, _EWS_CLEAR),
    FaultProfile("ihka", "Climate Control (generic)", "ihka", 0x5B, "ihka",
                 _IHKA_READ_1, _IHKA_CLEAR),
    FaultProfile("asc5", "ASC 5", "abs", 0x56, "asc5",
                 _ABS_READ, _ABS_CLEAR, _ASC5, _ABS_WAKE),
    FaultProfile("dsc5", "DSC 5", "abs", 0x56, "asc5",
                 _ABS_READ, _ABS_CLEAR, _DSC5, _ABS_WAKE),
    FaultProfile("dsc57", "DSC 5.7", "abs", 0x56, "dsc57",
                 _ABS_READ, _ABS_CLEAR, _DSC57, _ABS_WAKE),
    FaultProfile("dsc_mk60", "DSC MK60", "dsc_mk60", 0x29, "mk60",
                 bytes.fromhex("B8 29 F1 04 18 02 FF FF"),
                 bytes.fromhex("B8 29 F1 03 14 FF FF"), _MK60),
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


def _parse_dsc57(profile, payload) -> list[ModuleFault]:
    if not payload:
        raise DS2Error(f"empty {profile.name} fault payload")
    faults = []
    offset, slot = 1, 0
    while slot < payload[0]:
        record = payload[offset:offset + 5]
        if len(record) != 5:
            raise DS2Error(f"truncated {profile.name} fault record")
        code, flags, frequency = record[:3]
        has_states = code in profile.fault_names and code not in {
            0x02, 0x15, 0x44, 0x60, 0x77, 0x80}
        has_speed = code in profile.fault_names and code not in {
            0x02, 0x19, 0x29, 0x38, 0x44, 0x60, 0x77, 0x80}
        states = (
            "ASC passive" if flags & 0x10 else "ASC not passive",
            "ABS regulation active" if flags & 0x20 else "ABS regulation inactive",
            "Brake-light switch pressed" if flags & 0x40 else "Brake-light switch not pressed",
            "ASC regulation active" if flags & 0x80 else "ASC regulation inactive",
            *(f"{name} {'active' if flags & bit else 'inactive'}" for bit, name in (
                (0x01, "ACB"), (0x02, "HBA"), (0x04, "ECD"), (0x08, "HDC"))),
        ) if has_states else ()
        faults.append(_fault(
            profile, record, status=flags, frequency=frequency,
            speed_kmh=int.from_bytes(record[3:5], "big") / 16.0 if has_speed else None,
            conditions=states, unknown_status_bits=0 if has_states else flags,
            environment_raw=record[3:5], reported_total=payload[0],
        ))
        # The BMW job counts a low-frequency brake-switch record as two slots.
        slot += 2 if code == 0x19 and frequency < 50 else 1
        offset += 5
    _length(profile, payload, offset)
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


_EGS86_ENVIRONMENT = {
    0x01: ("SSA_ART", 0x08, 0x09), 0x02: ("SSA_ART", 0x08, 0x09),
    0x03: ("SSA_ART", 0x08, 0x09), 0x04: ("SSA_ART", 0x08, 0x09),
    0x0F: ("SSA_ART", 0x08, 0x09), 0x10: ("SSA_ART", 0x08, 0x09),
    0x11: ("SSA_ART", 0x08, 0x09), 0x12: ("SSA_ART", 0x08, 0x09),
    0x13: ("RGT0_INFO", "RGT1_INFO", 0x09),
    0x20: ("SSA_ART", 0x08, 0x04), 0x21: ("SSA_ART", 0x09, 0x05),
    0x22: (0x06, 0x07, 0x09), 0x30: (0x18, 0x08, 0x23),
    0x31: ("SSA_ART", 0x08, 0x05), 0x32: (0x02, 0x04, 0x05),
    0x33: (0x02, 0x04, 0x05), 0x34: (0x02, 0x04, 0x05),
    0x35: (0x02, 0x04, 0x05), 0x36: (0x02, 0x04, 0x05),
    0x37: (0x02, 0x04, 0x05), 0x38: ("SSA_ART", 0x08, 0x05),
    0x39: ("SSA_ART", 0x08, 0x05), 0x3A: ("SSA_ART", 0x08, 0x05),
    0x3B: ("SSA_ART", 0x08, 0x02), 0x3C: ("RGT1_INFO", 0x05, 0x09),
    0x3D: ("SSA_ART", 0x08, 0x05), 0x3E: ("SSA_ART", 0x08, 0x04),
    0x50: (0x06, 0x22, 0x09), 0x51: (0x09,), 0x52: (0x09,),
    0x53: (0x09,), 0x60: (0x06, 0x07, 0x09), 0x61: (0x09,),
    0x70: ("RGT1_INFO", 0x05, 0x09), 0x71: ("SSA_ART", 0x02, 0x09),
    0x72: ("RGT1_INFO", "RGT3_INFO", 0x09),
    0x80: (0x05, 0x08, 0x09), 0x81: (0x06, 0x05, 0x09),
    0x82: ("SSA_ART", 0x06, "RGT1_INFO"), 0x90: (0x01, 0x08, 0x09),
    0x91: (0x06, "SSA_ART", 0x09), 0x93: ("SSA_ART", 0x06, 0x05),
    0x95: ("SSA_ART", 0x09, 0x05), 0x97: (0x09, 0x05, "RGT1_INFO"),
}
_GS8604_ENVIRONMENT = {**_EGS86_ENVIRONMENT, 0x41: (0x02, 0x24, 0x05)}
_GS20_ENVIRONMENT = {
    0x01: ("BYTE_21", "BYTE_18", 0x09), 0x04: ("BYTE_21", 0x08, 0x09),
    0x10: ("BYTE_21", 0x08, 0x09), 0x11: ("BYTE_21", 0x08, 0x09),
    0x12: ("BYTE_21", 0x08, 0x09), 0x13: ("BYTE_17", "BYTE_18", 0x09),
    0x20: ("BYTE_21", 0x08, 0x24), 0x21: ("BYTE_21", 0x08, 0x05),
    0x22: (0x06, 0x07, 0x09), 0x30: (0x25, 0x08, "BYTE_17"),
    0x32: ("BYTE_21", 0x08, 0x09), 0x34: ("BYTE_21", 0x08, 0x09),
    0x35: ("BYTE_21", 0x08, 0x09), 0x36: ("BYTE_21", 0x08, 0x09),
    0x37: ("BYTE_21", 0x08, 0x09), 0x3B: ("BYTE_21", 0x08, 0x02),
    0x3C: ("BYTE_18", 0x05, 0x09), 0x3D: (0x06, 0x07, 0x09),
    0x3F: ("BYTE_18", 0x05, 0x09), 0x50: (0x06, 0x22, 0x09),
    0x51: (0x09,), 0x52: (0x09,), 0x53: (0x09,), 0x54: (0x09,),
    0x60: (0x06, 0x07, 0x09), 0x70: ("BYTE_18", 0x06, 0x09),
    0x71: ("BYTE_21", 0x02, 0x09), 0x72: ("BYTE_18", 0x25, 0x09),
    0x78: ("BYTE_18", 0x06, 0x09), 0x80: ("BYTE_21", 0x08, 0x09),
    0x81: (0x06, 0x05, 0x09), 0x82: (0x06, 0x05, 0x09),
    0x83: (0x06, 0x05, 0x09), 0x90: (0x01, 0x08, 0x09),
    0x91: ("BYTE_21", 0x05, 0x03), 0x92: ("BYTE_21", 0x06, 0x02),
    0x93: ("BYTE_21", 0x06, 0x05), 0x94: (0x06, 0x08, 0x09),
    0x95: ("BYTE_21", 0x05, 0x09), 0x96: ("BYTE_21", 0x05, 0x09),
    0x97: ("BYTE_18", 0x05),
}
_EGS_ENVIRONMENT_GROUPS = {
    "SSA_ART": (0x1D, 0x1E, 0x21),
    "RGT0_INFO": (0x0B, 0x0C, 0x0D, 0x0E, 0x0F),
    "RGT1_INFO": tuple(range(0x10, 0x18)),
    "RGT3_INFO": tuple(range(0x18, 0x1D)),
    "BYTE_21": (0x1D, 0x21),
    "BYTE_17": (0x0B, 0x0C, 0x0D, 0x23, 0x0E, 0x0F),
    "BYTE_18": tuple(range(0x10, 0x18)),
}
_EGS_ENVIRONMENT_ANALOG = {
    0x01: ("DME CAN version", 1, 1, 0, ""),
    0x02: ("Throttle", 100, 255, 0, "%"),
    0x03: ("EGS torque reduction", 100, 255, 0, "%"),
    0x04: ("Mean rear-wheel speed", 16, 1, 0, "rpm"),
    0x05: ("Output speed", 32, 1, 0, "rpm"),
    0x06: ("Engine speed", 32, 1, 0, "rpm"),
    0x07: ("Engine temperature", 1, 1, -48, "°C"),
    0x08: ("Transmission oil temperature", 1, 1, -55, "°C"),
    0x09: ("Battery voltage", 16, 198, 0, "V"),
    0x24: ("Turbine speed", 32, 1, 0, "rpm"),
    0x25: ("Gear", 1, 1, 0, ""),
}
_EGS_ENVIRONMENT_BITS = {
    0x0B: ("Solenoid valve 1", 0x80), 0x0C: ("Solenoid valve 2", 0x40),
    0x0D: ("Solenoid valve 3", 0x20), 0x0E: ("Shift-lock solenoid", 0x02),
    0x0F: ("Brake pressed", 0x01), 0x10: ("Selector contact L1", 0x80),
    0x11: ("Selector contact L2", 0x40), 0x12: ("Selector contact L3", 0x20),
    0x13: ("Selector contact L4", 0x10), 0x14: ("Program button A", 0x08),
    0x15: ("Program button S/M", 0x04), 0x16: ("Steptronic gate", 0x02),
    0x17: ("Fault lamp", 0x01), 0x19: ("Kickdown", 0x10),
    0x1A: ("Cornering", 0x08), 0x1B: ("Tip down", 0x02),
    0x1C: ("Tip up", 0x01), 0x1D: ("Shift in progress", 0x80),
    0x1E: ("Gear engagement", 0x40),
}
_EGS86_LAST_SHIFT = {
    0x10: "downshift 1->1m", 0x11: "downshift 2->1m", 0x12: "downshift 2->1",
    0x13: "downshift 3->2", 0x14: "downshift 4->3", 0x15: "downshift 5->4",
    0x16: "downshift 3->1", 0x17: "downshift 4->2", 0x18: "downshift 5->3",
    0x20: "upshift 1m->1", 0x21: "upshift 1m->2", 0x22: "upshift 1->2",
    0x23: "upshift 2->3", 0x24: "upshift 3->4", 0x25: "upshift 4->5",
}
_GS20_LAST_SHIFT = {
    0x14: "downshift 5->4", 0x15: "downshift 4->3", 0x16: "downshift 3->2",
    0x17: "downshift 2->1", 0x20: "upshift 1->2", 0x21: "upshift 2->3",
    0x22: "upshift 3->4", 0x23: "upshift 4->5", 0x48: "engagement P/N->D",
    0x49: "engagement R->D", 0x4A: "engagement P/N->R", 0x4B: "engagement D->R",
}


def _egs_environment_value(profile_key: str, field_id: int, raw: int) -> str:
    gs20 = profile_key == "egs_gs20"
    if field_id in _EGS_ENVIRONMENT_ANALOG:
        name, mult, divisor, offset, unit = _EGS_ENVIRONMENT_ANALOG[field_id]
        if field_id == 0x07 and gs20:
            offset = -40
        elif field_id == 0x08 and profile_key != "egs_gs8600":
            offset = -40
        elif field_id == 0x09 and gs20:
            mult, divisor = 1, 10
        number = f"{raw * mult / divisor + offset:.2f}".rstrip("0").rstrip(".")
        return f"{name}: {number}{' ' + unit if unit else ''}"
    if field_id in _EGS_ENVIRONMENT_BITS or field_id == 0x23 and gs20:
        if field_id == 0x23:
            name, mask = "Torque-converter clutch closed", 0x04
        else:
            name, mask = _EGS_ENVIRONMENT_BITS[field_id]
            if gs20 and field_id in (0x11, 0x12, 0x13):
                name = f"Selector contact L{field_id - 0x0E}"
        return f"{name}: {'yes' if raw & mask else 'no'}"
    if field_id == 0x18:
        # BMW masks E0 but supplies unshifted 01..07 keys; no gear is established.
        return f"Gear ratio: unknown / implausible (0x{raw & 0xE0:02X})"
    if field_id == 0x21:
        name, mask = "Last shift", 0x7F if gs20 else 0x3F
        choices = _GS20_LAST_SHIFT if gs20 else _EGS86_LAST_SHIFT
    elif field_id == 0x22:
        name, mask = "Checksum fault region", 0x07
        choices = {0: "ROM / boot sector", 1: "Program", 2: "Data"}
    else:
        name, mask = "Torque-converter clutch", 0x07
        choices = {0: "Open", 1: "Regulated", 2: "Closed"}
    value = raw & mask
    return f"{name}: {choices.get(value, f'unknown / implausible (0x{value:02X})')}"


def _egs_environment(profile_key: str, code: int, record: bytes) -> tuple[str, ...]:
    matrix = (_GS20_ENVIRONMENT if profile_key == "egs_gs20" else
              _GS8604_ENVIRONMENT if profile_key == "egs_gs8604" else _EGS86_ENVIRONMENT)
    fields = matrix.get(code)
    if fields is None:
        return ()
    snapshots = []
    for index in range(3):
        start = 4 + index * 5
        raw = record[start:start + 5]
        values = [f"mileage {int.from_bytes(raw[3:5], 'big') * 10} km"]
        for selector, value in zip(fields, raw):
            for field_id in _EGS_ENVIRONMENT_GROUPS.get(selector, (selector,)):
                values.append(_egs_environment_value(profile_key, field_id, value))
        values.append(f"raw values {raw[:3].hex(' ').upper()}")
        snapshots.append(f"Snapshot {index + 1}: " + "; ".join(values))
    return tuple(snapshots)


def _parse_egs19(profile, payload) -> list[ModuleFault]:
    if not payload:
        raise DS2Error(f"empty {profile.name} fault payload")
    expected = 1 + min(payload[0], 5) * 19
    if len(payload) < expected:
        raise DS2Error(f"truncated {profile.name} fault payload")
    gs20 = profile.parser == "gs20"
    qualifiers = _GS20_QUALIFIERS if gs20 else _EGS86_QUALIFIERS
    faults = []
    for offset in range(1, expected, 19):
        record = payload[offset:offset + 19]
        code, flags, frequency, counter = record[:4]
        qualifier = flags & 0x0F
        conditions = [qualifiers.get(
            qualifier, f"Unknown qualifier 0x{qualifier:X}")]
        conditions.extend(text for bit, text in (
            (0x10, "Fault present after start" if gs20 else "Fault lamp requested"),
            (0x20, "Intermittent"),
            (0x40, "Substitute / failsafe function active"),
            (0x80, "Currently present"),
        ) if flags & bit)
        conditions.append(f"Logistics counter: {counter}")
        conditions.extend(_egs_environment(profile.key, code, record))
        faults.append(_fault(
            profile, record, status=flags, frequency=frequency,
            conditions=tuple(conditions),
            unknown_status_bits=qualifier if qualifier not in qualifiers else 0,
            environment_raw=record[4:], current=bool(flags & 0x80),
            state_text=("Current" if flags & 0x80 else
                        "Intermittent" if flags & 0x20 else "Stored"),
            reported_total=payload[0],
        ))
    return faults


def _send_mk60(ds2, body: bytes) -> tuple[bytes, bytes]:
    response = bytes(ds2.send_bmw_fast(body, target=0x29, timeout=2.0))
    if len(response) < 6 or response[3] + 5 != len(response):
        raise DS2Error("invalid DSC MK60 response length")
    payload = response[4:-1]
    if payload[0] == 0x7F:
        status = f"0x{payload[2]:02X}" if len(payload) >= 3 else "unknown"
        raise DS2Error(f"DSC MK60 rejected service 0x{body[4]:02X} (status {status})")
    if payload[0] != body[4] + 0x40:
        raise DS2Error("DSC MK60 response does not match the requested service")
    return response, payload[1:]


def _parse_mk60(profile, payload) -> list[ModuleFault]:
    if not payload:
        raise DS2Error("empty DSC MK60 fault payload")
    _length(profile, payload, 1 + payload[0] * 3)
    symptoms = {
        0: "No matching fault symptom", 1: "Signal or value above threshold",
        2: "Signal or value below threshold", 4: "No signal or value",
        8: "Implausible signal or value",
    }
    states = {0: "Not observed", 1: "Stored", 2: "Presence uncertain", 3: "Current"}
    faults = []
    for offset in range(1, len(payload), 3):
        record = payload[offset:offset + 3]
        code, flags = int.from_bytes(record[:2], "big"), record[2]
        presence = (flags >> 5) & 3
        conditions = [
            symptoms.get(flags & 15, f"Unknown symptom 0x{flags & 15:X}"),
            "Test conditions not yet met" if flags & 0x10 else "Test conditions met",
            "Warning lamp requested" if flags & 0x80 else "No warning lamp requested",
        ]
        if presence == 2:
            # The source table has contradictory entries for status 0x22.
            conditions.append("BMW status 2 has conflicting stored/pending descriptions")
        faults.append(ModuleFault(
            profile.key, profile.name, code,
            profile.fault_names.get(code, f"Unknown fault location 0x{code:04X}"),
            bytes(record), status=flags, conditions=tuple(conditions),
            current=presence == 3, state_text=states[presence], reported_total=payload[0],
            unknown_status_bits=flags & 15 if flags & 15 not in symptoms else 0,
        ))
    return faults


_MK60_SNAPSHOT_INPUTS = (
    (0x01, "Brake-light switch"),
    (0x04, "Brake-fluid switch"),
    (0x08, "ASC/DSC active (button function)"),
    (0x20, "Brake pressure detected"),
)
_MK60_SNAPSHOT_CONTROL = (
    (0x0100, "ABS control"),
    (0x0200, "ASC control (BMR)"),
    (0x0400, "ASC control (AMR)"),
    (0x0800, "GMR control"),
    (0x1000, "MMR control"),
    (0x2000, "Cornering brake control (CBC)"),
    (0x4000, "Engine drag-torque control (MSR)"),
    (0x8000, "TDR control"),
    (0x0001, "SDR control"),
    (0x0002, "Dynamic brake control (DBC)"),
    (0x0004, "RTA active"),
    (0x0008, "Run-up mode"),
)


def _read_mk60_detail(ds2, fault: ModuleFault) -> ModuleFault:
    payload = b""
    try:
        _, payload = _send_mk60(
            ds2, bytes.fromhex("B8 29 F1 03 17") + fault.code.to_bytes(2, "big"))
        if payload == b"\x00":
            raise DS2Error("fault is no longer in memory")
        if len(payload) < 6 or payload[0] != 1:
            raise DS2Error("invalid DSC MK60 detail header")
        if int.from_bytes(payload[1:3], "big") != fault.code:
            raise DS2Error("DSC MK60 detail code does not match the requested fault")
        snapshots = payload[6:]
        if not snapshots or len(snapshots) % 6 or len(snapshots) > 18:
            raise DS2Error("invalid DSC MK60 snapshot length")
    except DS2Error as error:
        return replace(fault, environment_raw=payload, conditions=(
            *fault.conditions, f"Freeze-frame unavailable: {error}"))

    conditions = [*fault.conditions, f"Logistics counter: {payload[5]}"]
    if payload[3] != fault.status:
        conditions.append(f"Detail status: 0x{payload[3]:02X} (changed since fault list)")
    # BMW accepts up to three complete snapshots even when frequency is lower.
    for index, offset in enumerate(range(0, len(snapshots), 6), 1):
        record = snapshots[offset:offset + 6]
        distance = int.from_bytes(record[:2], "big") * 10
        inputs, control = record[3], int.from_bytes(record[4:6], "big")
        conditions.append(
            f"Snapshot {index}: mileage {distance} km; vehicle speed {record[2]} km/h")
        for name, flags, fields in (
                ("inputs", inputs, _MK60_SNAPSHOT_INPUTS),
                ("control states", control, _MK60_SNAPSHOT_CONTROL)):
            values = "; ".join(f"{label}: {int(bool(flags & bit))}" for bit, label in fields)
            conditions.append(f"Snapshot {index} {name} (0/1): {values}")
        if inputs & 0xD2 or control & 0x00F0:
            conditions.append(
                f"Snapshot {index} unassigned bits: inputs 0x{inputs & 0xD2:02X}; "
                f"control 0x{control & 0x00F0:04X}")
    return replace(fault, frequency=payload[4], environment_raw=payload,
                   conditions=tuple(conditions))


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
    "dsc57": _parse_dsc57,
    "abs_mk20": _parse_abs_mk20,
    "asc_mk20": _parse_asc_mk20,
    "egs": _parse_egs,
    "egs86": _parse_egs19,
    "gs20": _parse_egs19,
    "fgr": _parse_fgr,
}


def read_module_faults(ds2, profile_key: str) -> list[ModuleFault]:
    """Read and decode one exact non-DME fault profile."""
    profile = _profile(profile_key)
    if profile.parser == "mk60":
        _, payload = _send_mk60(ds2, profile.read_frame)
        return [_read_mk60_detail(ds2, fault) for fault in _parse_mk60(profile, payload)]
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
    if profile.parser == "mk60":
        _, session = _send_mk60(ds2, bytes.fromhex("B8 29 F1 02 10 87"))
        if session != b"\x87":
            raise DS2Error("DSC MK60 did not confirm the requested diagnostic session")
        response, _ = _send_mk60(ds2, profile.clear_frame)
        return response
    if profile.wake_frame:
        _send_a0(ds2, profile, profile.wake_frame)
    response, _ = _send_a0(ds2, profile, profile.clear_frame)
    return response
