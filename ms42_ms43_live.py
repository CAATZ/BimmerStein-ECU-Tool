"""BMW MS42/MS43 measurements, with the shared live capture/CSV lifecycle.

Offsets below include the three DS2 header bytes. Sources: BMW MS420DS0 /
MS430DS0 and exact 0110C6 / 430069 selected-measurement handlers. Conditional
firmware conversions stay on fixed groups; only direct stores get RAM aliases.
"""

from dataclasses import dataclass, replace
import time

from ds2 import DS2Error
from live_data import LiveDataPoller, RoughnessReading
from logger_definition import LoggerParameter, load_logger_definition


@dataclass(frozen=True)
class Channel:
    id: str
    name: str
    unit: str
    selector: int
    offset: int
    length: int = 1
    scale: float = 1.0
    bias: float = 0.0
    signed: bool = False
    address: int | None = None
    adaptation: bool = False
    adc: int | None = None
    parameter: LoggerParameter | None = None
    mask: int = 0
    expected: int = 0

    @property
    def command(self):
        return 6 if self.selector == 6 else 0x0B

    @property
    def request(self):
        if self.selector == 6:
            return self.address.to_bytes(4, "big") + bytes((self.length,))
        return bytes((2, 14, 0, 0, self.adc)) if self.adc is not None else bytes((self.selector,))

    @property
    def descriptor(self):
        if self.adc is not None:
            return bytes((2, 0, 0, 0, self.adc))
        return bytes((self.length - 1,)) + self.address.to_bytes(4, "big")

    def decode(self, raw):
        if len(raw) != self.length:
            raise DS2Error(f"Short {self.name} measurement: {len(raw)}/{self.length}")
        if self.parameter:
            return self.parameter.parse(raw, self.parameter.address,
                                        wire_endian="little" if self.selector == 6 else "big")
        if self.mask:
            return int((raw[0] & self.mask) == self.expected)
        return int.from_bytes(raw, "little" if self.selector == 6 else "big",
                              signed=self.signed) * self.scale + self.bias

    def display(self, value):
        if self.parameter:
            return self.parameter.display(value)
        if value is None:
            return "—"
        if self.mask:
            return "1" if value else "0"
        return f"{value:.0f}" if self.unit in {"rpm", "km/h"} else f"{value:.3f}"


def _c(key, name, unit, selector, offset, length=1, scale=1.0, bias=0.0,
       signed=False, address=None, adaptation=False, adc=None):
    return Channel(key, name, unit, selector, offset, length, scale, bias,
                   signed, address, adaptation, adc)


_MS43 = (
    _c("N", "Engine Speed", "rpm", 3, 3, 2, address=0xE0E4),
    _c("VS", "Vehicle Speed", "km/h", 3, 5, address=0x4047F),
    _c("PVS_AV", "Accelerator Pedal", "°", 3, 6, 2, .0018311, address=0xE0FE),
    _c("TPS_AV_PRJ", "Throttle Angle", "°", 3, 8, 2, .0018311, address=0xE15E),
    _c("MAF_KGH", "Mass Air Flow", "kg/h", 3, 10, 2, .25, address=0xE32E),
    _c("TIA", "Intake Air Temperature", "°C", 3, 12, scale=.75, bias=-48),
    _c("TCO", "Coolant Temperature", "°C", 3, 13, scale=.75, bias=-48),
    _c("TOIL", "Oil Temperature", "°C", 3, 14, scale=.796, bias=-48),
    _c("TCO_EX", "Radiator Outlet Temperature", "°C", 3, 15, scale=.75, bias=-48),
    _c("IGA_1", "Ignition Timing", "°", 3, 16, scale=-.375, bias=72, address=0xFBAB),
    _c("TI_BANK_1", "Injection Time", "ms", 3, 17, 2, .0053333, address=0x40DAA),
    _c("ISAPWM_IS", "Idle Integrator", "%", 3, 19, 2, .0015, signed=True, address=0x40888),
    _c("ISAPWM_ISA", "Idle Valve Duty", "%", 3, 21, 2, .0015, address=0x40862),
    _c("CAM_AV_IN", "Intake Camshaft Actual", "°", 0x90, 3, scale=.375, bias=60),
    _c("CAM_AV_EX", "Exhaust Camshaft Actual", "°", 0x90, 9, scale=-.375, bias=-60),
    _c("VB_IGK", "Ignition Supply Voltage (KL15)", "V", 3, 25, scale=.10156, address=0xFBD6),
    _c("LAM_1", "Lambda Integrator Bank 1", "λ", 3, 26, 2, .000015259, .5, address=0x40C72),
    _c("LAM_2", "Lambda Integrator Bank 2", "λ", 3, 28, 2, .000015259, .5, address=0x40DE0),
    _c("LAM_1V", "Short Term Fuel Trim Bank 1", "%", 3, 26, 2, .0015259, -50, address=0x40C72),
    _c("LAM_2V", "Short Term Fuel Trim Bank 2", "%", 3, 28, 2, .0015259, -50, address=0x40DE0),
    _c("LSHPWM_UP_1", "Front O2 Heater Bank 1", "%", 3, 30, scale=.391, address=0x40F6C),
    _c("LSHPWM_UP_2", "Front O2 Heater Bank 2", "%", 3, 31, scale=.391, address=0x40F84),
    _c("LSHPWM_DOWN_1", "Rear O2 Heater Bank 1", "%", 3, 32, scale=.391, address=0x40FAC),
    _c("LSHPWM_DOWN_2", "Rear O2 Heater Bank 2", "%", 3, 33, scale=.391, address=0x40FC4),
    _c("MAF_MES", "Engine Load", "mg/stroke", 3, 34, 2, .0212, address=0xE330),
    _c("NL_2", "Knock Sensor 1", "V", 3, 36, 2, .0000778, address=0xE300),
    _c("NL_5", "Knock Sensor 2", "V", 3, 38, 2, .0000778, address=0xE2FA),
    _c("ECFPWM_ECF", "Electric Fan Request", "%", 3, 40, scale=.39063, address=0x40121),
    _c("AMP_MES", "Atmospheric Pressure", "hPa", 3, 41, 2, .08292),
    _c("VB", "Battery Voltage (after main relay)", "V", 3, 43, scale=.10156, address=0xFBD5),
    _c("CAM_SP_IN", "Intake Camshaft Target", "°", 0x90, 4, scale=.375, bias=60),
    _c("CAM_REF_IN", "Intake Camshaft Reference", "°", 0x90, 5, 2, .001465, 60),
    _c("CAM_AD_IN", "Intake Camshaft Adaptation", "°", 0x90, 7, scale=.375, signed=True, adaptation=True),
    _c("CAM_AV_IN_RAW", "Intake Camshaft Raw", "°", 0x90, 8, scale=.375, bias=60),
    _c("CAM_SP_EX", "Exhaust Camshaft Target", "°", 0x90, 10, scale=-.375, bias=-60),
    _c("CAM_REF_EX", "Exhaust Camshaft Reference", "°", 0x90, 11, 2, -.001465, -60),
    _c("CAM_AD_EX", "Exhaust Camshaft Adaptation", "°", 0x90, 13, scale=.375, signed=True, adaptation=True),
    _c("CAM_AV_EX_RAW", "Exhaust Camshaft Raw", "°", 0x90, 14, scale=-.375, bias=-60),
    _c("ISAPWM_AD_MMV_IS", "Idle Adaptation", "%", 0x92, 3, 2, .001526, signed=True, adaptation=True),
    _c("ISAPWM_DRI_AD_MMV_IS", "Idle Drive Adaptation", "%", 0x92, 5, 2, .001526, signed=True, adaptation=True),
    _c("ISAPWM_ACCIN_AD_MMV_IS", "Idle A/C Adaptation", "%", 0x92, 7, 2, .001526, signed=True, adaptation=True),
    _c("PVS_AV_1", "Accelerator Sensor 1", "°", 0x92, 9, 2, .0018311),
    _c("PVS_AV_2", "Accelerator Sensor 2", "°", 0x92, 11, 2, .0018311),
    _c("TPS_AV_1", "Throttle Sensor 1", "°", 0x92, 13, 2, .007294),
    _c("TPS_AV_2", "Throttle Sensor 2", "°", 0x92, 15, 2, .007294),
    _c("V_TPS_AD_BOL_1", "Throttle Mechanical Stop 1", "V", 0x92, 17, 2, .0048828, adaptation=True),
    _c("V_TPS_AD_BOL_2", "Throttle Mechanical Stop 2", "V", 0x92, 19, 2, .0048828, adaptation=True),
    _c("V_TPS_AD_EL_BOL_1", "Throttle Electrical Stop 1", "V", 0x92, 21, 2, .0048828, adaptation=True),
    _c("V_TPS_AD_EL_BOL_2", "Throttle Electrical Stop 2", "V", 0x92, 23, 2, .0048828, adaptation=True),
    _c("V_TPS_AD_LIH_1", "Throttle Limp-home Stop 1", "V", 0x92, 25, 2, .0048828, adaptation=True),
    _c("V_TPS_AD_LIH_2", "Throttle Limp-home Stop 2", "V", 0x92, 27, 2, .0048828, adaptation=True),
    _c("FAC_RON_AD", "Octane Adaptation", "", 0x93, 3, 2, .00001526, adaptation=True),
    _c("TI_AD_ADD_MMV_1", "Additive Fuel Adaptation Bank 1", "ms", 0x91, 4, 2, .004, -131.072, adaptation=True),
    _c("TI_AD_FAC_MMV_1", "Multiplicative Fuel Adaptation Bank 1", "%", 0x91, 6, 2, .001526, -50, adaptation=True),
    _c("TI_AD_ADD_MMV_2", "Additive Fuel Adaptation Bank 2", "ms", 0x91, 8, 2, .004, -131.072, adaptation=True),
    _c("TI_AD_FAC_MMV_2", "Multiplicative Fuel Adaptation Bank 2", "%", 0x91, 10, 2, .001526, -50, adaptation=True),
)

_MS42 = (
    _c("N", "Engine Speed", "rpm", 3, 3, 2, address=0xFA22),
    _c("VS", "Vehicle Speed", "km/h", 3, 5, address=0xC442),
    _c("PVS_AV", "Accelerator Pedal", "°", 3, 6, scale=.46862, address=0xE0AB),
    _c("TPS_AV_PRJ", "Throttle Angle", "°", 3, 7, scale=.46862, address=0xFAB7),
    _c("MAF_KGH", "Mass Air Flow", "kg/h", 3, 8, 2, .25, address=0xFA4A),
    _c("TIA", "Intake Air Temperature", "°C", 3, 10, scale=.75, bias=-48, address=0xC425),
    _c("TCO", "Coolant Temperature", "°C", 3, 11, scale=.75, bias=-48, address=0xC424),
    _c("TOIL", "Oil Temperature", "°C", 3, 12, scale=.7961, bias=-48, address=0xC426),
    _c("TCO_EX", "Radiator Outlet Temperature", "°C", 3, 13, scale=.75, bias=-48, address=0xC326),
    _c("IGA_1", "Ignition Timing", "°", 3, 14, scale=-.375, bias=72, address=0xE53D),
    _c("TI_BANK_1", "Injection Time", "ms", 3, 15, 2, .004, address=0xE39E),
    _c("ISAPWM_IS", "Idle Integrator", "%", 3, 17, 2, .001526, signed=True, address=0xE144),
    _c("ISAPWM_ISA", "Idle Valve Duty", "%", 3, 19, 2, .001526, address=0xE114),
    _c("VB", "Battery Voltage", "V", 3, 23, scale=.10196, address=0xC422),
    _c("LAM_1", "Lambda Integrator Bank 1", "λ", 3, 24, 2, .000015259, .5, address=0xE340),
    _c("LAM_2", "Lambda Integrator Bank 2", "λ", 3, 26, 2, .000015259, .5, address=0xE42A),
    _c("LSHPWM_UP_1", "Front O2 Heater Bank 1", "%", 3, 28, scale=.3906, address=0xCF40),
    _c("LSHPWM_UP_2", "Front O2 Heater Bank 2", "%", 3, 29, scale=.3906, address=0xCF4A),
    _c("LSHPWM_DOWN_1", "Rear O2 Heater Bank 1", "%", 3, 30, scale=.3906, address=0xC1EE),
    _c("LSHPWM_DOWN_2", "Rear O2 Heater Bank 2", "%", 3, 31, scale=.3906, address=0xC1F8),
    _c("MAF_MES", "Engine Load", "mg/stroke", 3, 32, 2, .02119, address=0xFA26),
    _c("TPS_AD", "Throttle Adaptation", "%", 3, 35, scale=.3922, address=0xFA81, adaptation=True),
    _c("ECFPWM_ECF", "Electric Fan Request", "", 3, 36, address=0xCE3E),
    _c("CAM_AV_IN", "Intake Camshaft Actual", "°", 0x90, 3, scale=.375, bias=60),
    _c("CAM_SP_IN", "Intake Camshaft Target", "°", 0x90, 4, scale=.375, bias=60),
    _c("CAM_REF_IN", "Intake Camshaft Reference", "°", 0x90, 5, scale=.375, bias=60),
    _c("CAM_AD_IN", "Intake Camshaft Adaptation", "°", 0x90, 6, scale=.3779, signed=True, adaptation=True),
    _c("CAM_TIME_AD_IN", "Intake VANOS Energization Adaptation", "", 0x90, 7, scale=.007812, adaptation=True),
    _c("CAM_DUTY_AD_IN", "Intake VANOS Holding Adaptation", "", 0x90, 8, scale=.007812, adaptation=True),
    _c("CAM_AV_EX", "Exhaust Camshaft Actual", "°", 0x90, 9, scale=-.375, bias=-60),
    _c("CAM_SP_EX", "Exhaust Camshaft Target", "°", 0x90, 10, scale=-.375, bias=-60),
    _c("CAM_REF_EX", "Exhaust Camshaft Reference", "°", 0x90, 11, scale=-.375, bias=-60),
    _c("CAM_AD_EX", "Exhaust Camshaft Adaptation", "°", 0x90, 12, scale=.3779, signed=True, adaptation=True),
    _c("CAM_TIME_AD_EX", "Exhaust VANOS Energization Adaptation", "", 0x90, 13, scale=.007812, adaptation=True),
    _c("CAM_DUTY_AD_EX", "Exhaust VANOS Holding Adaptation", "", 0x90, 14, scale=.007812, adaptation=True),
    _c("CAM_TEMP", "VANOS Solenoid Temperature Model", "°C", 0x90, 15, scale=.7961, bias=-48),
    _c("TI_AD_ADD_MMV_1", "Additive Fuel Adaptation Bank 1", "ms", 0x91, 3, 2, .004, -131.072, adaptation=True),
    _c("TI_AD_FAC_MMV_1", "Multiplicative Fuel Adaptation Bank 1", "%", 0x91, 5, 2, .001526, -50, adaptation=True),
    _c("TI_AD_ADD_MMV_2", "Additive Fuel Adaptation Bank 2", "ms", 0x91, 7, 2, .004, -131.072, adaptation=True),
    _c("TI_AD_FAC_MMV_2", "Multiplicative Fuel Adaptation Bank 2", "%", 0x91, 9, 2, .001526, -50, adaptation=True),
    _c("ISAPWM_AD_MMV_IS", "Idle Adaptation", "%", 0x92, 3, 2, .001526, signed=True, adaptation=True),
    _c("ISAPWM_DRI_AD_MMV_IS", "Idle Drive Adaptation", "%", 0x92, 5, 2, .001526, signed=True, adaptation=True),
    _c("ISAPWM_ACCIN_AD_MMV_IS", "Idle A/C Adaptation", "%", 0x92, 7, 2, .001526, signed=True, adaptation=True),
    _c("PVS_AD_1", "Accelerator Sensor Adaptation 1", "°", 0x92, 9, 2, .003662, adaptation=True),
    _c("PVS_AD_2", "Accelerator Sensor Adaptation 2", "°", 0x92, 11, 2, .003662, adaptation=True),
    _c("TPS_AD_1", "Throttle Sensor Adaptation 1", "°", 0x92, 13, 2, .003662, adaptation=True),
    _c("TPS_AD_2", "Throttle Sensor Adaptation 2", "°", 0x92, 15, 2, .003662, adaptation=True),
    _c("PVS_AV_1", "Accelerator Sensor 1", "°", 0x92, 17, 2, .001825),
    _c("PVS_AV_2", "Accelerator Sensor 2", "°", 0x92, 19, 2, .001825),
    _c("TPS_AV_1", "Throttle Sensor 1", "°", 0x92, 21, 2, .001825),
    _c("TPS_AV_2", "Throttle Sensor 2", "°", 0x92, 23, 2, .001825),
    _c("COUPLING_AD", "Throttle Coupling Spring Adaptation", "", 0x92, 34, scale=.00778, adaptation=True),
    _c("FAC_RON_AD", "Octane Adaptation", "", 0x93, 3, scale=.003906, adaptation=True),
)

_ADC = (("VLS_UP_1", "Front O2 Sensor Bank 1", 1),
        ("VLS_UP_2", "Front O2 Sensor Bank 2", 2),
        ("VLS_DOWN_1", "Rear O2 Sensor Bank 1", 0x19),
        ("VLS_DOWN_2", "Rear O2 Sensor Bank 2", 0x1A),
        ("V_MAF", "Mass Air Flow Sensor Voltage", 6),
        ("V_KNOCK_1", "Knock Sensor 1 Voltage", 0x17),
        ("V_KNOCK_2", "Knock Sensor 2 Voltage", 0x18))
_STATES = (
    ("STAT_S_KO", "A/C Compressor Request", 0, 0x80, 0x80),
    ("STAT_S_AC", "A/C Switch", 0, 0x40, 0x40),
    ("STAT_S_FGR", "Cruise Control Switch", 0, 0x20, 0x20),
    ("STAT_S_KUP", "Clutch Switch", 0, 0x10, 0x10),
    ("STAT_S_BLS", "Brake Light Switch", 0, 0x08, 0x08),
    ("STAT_S_BLTS", "Brake Test Switch", 0, 0x04, 0x04),
    ("STAT_LL", "Idle State", 0, 3, 1),
    ("STAT_TL", "Part Load State", 0, 3, 2),
    ("STAT_VL", "Full Load State", 0, 3, 3),
    ("STAT_LAMBDAREG2", "Lambda Control Bank 2 Active", 1, 0x80, 0x80),
    ("STAT_LAMBDAREG1", "Lambda Control Bank 1 Active", 1, 0x40, 0x40),
    ("STAT_SCHUB_AB", "Overrun Fuel Cut", 1, 0x20, 0x20),
    ("STAT_SCHUB", "Trailing Throttle", 1, 0x10, 0x10),
    ("STAT_START", "Engine Start State", 1, 0x08, 0x08),
    ("STAT_FS", "Transmission Drive Range", 1, 0x04, 0x04),
    ("STAT_CAN", "CAN Communication", 1, 0x01, 0x01),
    ("STAT_SLV", "Secondary Air Valve", 2, 0x80, 0x80),
    ("STAT_SLP", "Secondary Air Pump", 2, 0x40, 0x40),
    ("STAT_KLAPPE", "Exhaust Flap", 2, 0x08, 0x08),
    ("STAT_R_KO", "A/C Compressor Relay", 2, 0x02, 0x02),
    ("STAT_R_EKP", "Fuel Pump Relay", 2, 0x01, 0x01),
    ("STAT_KAT_H", "Catalyst Heating", 3, 0x80, 0x80),
    ("STAT_EML_IL", "EML Warning Lamp", 3, 0x20, 0x20),
    ("STAT_MIL", "Check Engine Lamp", 3, 0x10, 0x10),
    ("STAT_TEV", "Purge Valve", 3, 0x08, 0x08),
    ("STAT_DISA", "DISA Valve", 3, 0x04, 0x04),
    ("STAT_ASC", "ASC/DSC Torque Intervention", 3, 0x02, 0x02),
    ("STAT_TMOT", "Overtemperature Warning", 3, 0x01, 0x01),
    ("STAT_VAN_AK", "VANOS Active", 5, 0x80, 0x80),
    ("STAT_VAN_BR", "VANOS Ready", 5, 0x40, 0x40),
    ("STAT_VAN_PA", "VANOS Passive", 5, 0x20, 0x20),
    ("STAT_GEB_OK", "Crankshaft Wheel Adapted", 5, 0x10, 0x10),
)
_DIGITAL = tuple(Channel(key, name, "", 4, byte + 3, mask=mask, expected=value,
                         adaptation=key == "STAT_GEB_OK")
                 for key, name, byte, mask, value in _STATES)
_CATALOG = {family: channels + _DIGITAL + tuple(_c(key, name, "V", 2, 3, 2, scale, adc=index)
                                   for key, name, index in _ADC)
            for family, channels, scale in (("MS42", _MS42, .004887), ("MS43", _MS43, .0048828))}
_EXACT_IDS = {"MS42": {"0110C6", "7500255", "0110C7", "7505859", "0110CA"},
              "MS43": {"430037", "MS430037", "430055", "MS430055", "430064", "MS430064",
                       "430065", "MS430065", "430066", "MS430066",
                       "430069", "MS430069", "7551615", "43X001", "MS43X001"}}
_ROUGHNESS_IDS = {f"BS_ROUGHNESS_CYL{cylinder}": cylinder - 1 for cylinder in range(1, 7)}
_FIRING_ORDER = (1, 5, 3, 6, 2, 4)
_ROUGHNESS_LAYOUTS = {
    # Native producers 01F33A /0A9A00; bitmap precedes six filtered LE16 words.
    "MS42": (0xCFB0, 0xFA22, 0xE27A, ((0xFD84, 0x06), (0xFD06, 0x1000))),
    "MS43": (0x407F2, 0xE0E4, 0xE1F2, ((0xFDB2, 0x06), (0xFD42, 0x40))),
}

# Actual diagnostic stores and native consumers, not revision-number aliases.
_REVISION_LAYOUTS = {
    ("MS42", "0110CA"): {
        "addresses": {"IGA_1": 0xE54D, "LAM_2": 0xE432},
        "roughness": _ROUGHNESS_LAYOUTS["MS42"],
        "knock": (0xC486, 0x48911, 0x48922),
    },
    ("MS43", "430037"): {
        "addresses": {
            "N": 0xE0DC, "VS": 0x40428, "PVS_AV": 0xE0F6, "TPS_AV_PRJ": 0xE156,
            "MAF_KGH": 0xE30E, "IGA_1": 0xFB94, "TI_BANK_1": 0x40D20,
            "ISAPWM_IS": 0x40810, "ISAPWM_ISA": 0x407EA, "VB_IGK": 0xFBBF,
            "LAM_1": 0x40BEE, "LAM_1V": 0x40BEE, "LAM_2": 0x40D4C, "LAM_2V": 0x40D4C,
            "LSHPWM_UP_1": 0x40EBA, "LSHPWM_UP_2": 0x40ECA,
            "LSHPWM_DOWN_1": 0x40EEA, "LSHPWM_DOWN_2": 0x40EFA,
            "MAF_MES": 0xE310, "NL_2": 0xE2E0, "NL_5": 0xE2DA,
            "ECFPWM_ECF": 0x400F9, "VB": 0xFBBE,
        },
        "roughness": (0x40780, 0xE0DC, 0xE1D6, ((0xFDAA, 0x06), (0xFD3E, 0x80))),
        "knock": (0x401E4, 0x7400C, 0x7408E),
    },
    ("MS43", "430055"): {
        "addresses": {
            "VS": 0x4045E, "MAF_KGH": 0xE32C, "TI_BANK_1": 0x40D6A,
            "ISAPWM_IS": 0x4084E, "ISAPWM_ISA": 0x40828,
            "LAM_1": 0x40C38, "LAM_1V": 0x40C38,
            "LAM_2": 0x40D96, "LAM_2V": 0x40D96,
            "LSHPWM_UP_1": 0x40F12, "LSHPWM_UP_2": 0x40F22,
            "LSHPWM_DOWN_1": 0x40F42, "LSHPWM_DOWN_2": 0x40F52,
            "MAF_MES": 0xE32E, "NL_2": 0xE2FE, "NL_5": 0xE2F8,
            "ECFPWM_ECF": 0x4011C,
        },
        "roughness": (0x407BC, 0xE0E4, 0xE1F2, ((0xFDAC, 0x06), (0xFD40, 0x80))),
        "knock": (0x40213, 0x7400C, 0x74095),
    },
    ("MS43", "430064"): {
        "addresses": {
            "VS": 0x4046D, "TI_BANK_1": 0x40D82,
            "ISAPWM_IS": 0x40862, "ISAPWM_ISA": 0x4083C,
            "LAM_1": 0x40C4C, "LAM_1V": 0x40C4C, "LAM_2": 0x40DB8, "LAM_2V": 0x40DB8,
            "LSHPWM_UP_1": 0x40F42, "LSHPWM_UP_2": 0x40F5A,
            "LSHPWM_DOWN_1": 0x40F82, "LSHPWM_DOWN_2": 0x40F9A, "ECFPWM_ECF": 0x4011C,
        },
        "roughness": (0x407CC, 0xE0E4, 0xE1F2, ((0xFDAE, 0x06), (0xFD42, 0x10))),
        "knock": (0x4021B, 0x7400C, 0x74095),
    },
    ("MS43", "430065"): {
        "addresses": {
            "VS": 0x4047C, "TI_BANK_1": 0x40D9A,
            "ISAPWM_IS": 0x4087A, "ISAPWM_ISA": 0x40854,
            "LAM_1": 0x40C64, "LAM_1V": 0x40C64, "LAM_2": 0x40DD0, "LAM_2V": 0x40DD0,
            "LSHPWM_UP_1": 0x40F5A, "LSHPWM_UP_2": 0x40F72,
            "LSHPWM_DOWN_1": 0x40F9A, "LSHPWM_DOWN_2": 0x40FB2,
        },
        "roughness": (0x407E4, 0xE0E4, 0xE1F2, ((0xFDB2, 0x06), (0xFD42, 0x400))),
        "knock": (0x40221, 0x7400C, 0x74095),
    },
    ("MS43", "430066"): {
        "addresses": {"VS": 0x4047E},
        "roughness": _ROUGHNESS_LAYOUTS["MS43"],
        "knock": (0x40221, 0x7400C, 0x74095),
    },
}


def _revision_layout(family, software):
    key = str(software or "").upper()
    if family == "MS43" and key.startswith("MS43"):
        key = key[2:]
    return _REVISION_LAYOUTS.get((family, key), {})


def read_passive_roughness(ecu, software):
    """Observe normal producers. The native bitmap is history eligibility, not age.

    No system-check START/READ/STOP is sent. BMW's dimensionless 0.00122
    quotient and firing-order labels apply to these same six source words.
    """
    if str(software or "").upper() not in _EXACT_IDS.get(ecu.family, ()):
        raise ValueError("Passive roughness RAM is not mapped for this software")
    address, rpm_address, input_address, faults = _revision_layout(ecu.family, software).get(
        "roughness", _ROUGHNESS_LAYOUTS[ecu.family])

    def read(address, length=2):
        raw = bytes(ecu.read_memory(address, length))
        if len(raw) != length:
            raise DS2Error(f"Short roughness RAM read at 0x{address:X}: {len(raw)}/{length}")
        return raw

    raw = read(address, 14)
    mask = int.from_bytes(raw[:2], "little") & 0x3F
    rpm = int.from_bytes(read(rpm_address), "little")
    inhibited = int.from_bytes(read(input_address), "little") == 0xFFFF
    for location, bits in faults:
        inhibited |= bool(int.from_bytes(read(location), "little") & bits)
    values = [None] * 6
    for index, cylinder in enumerate(_FIRING_ORDER):
        if rpm > 0 and not inhibited and mask & (1 << index):
            values[cylinder - 1] = int.from_bytes(raw[2 + index * 2:4 + index * 2],
                                                "little", signed=True) * .00122
    state = ("engine_stopped" if rpm == 0 else
             "unavailable" if all(value is None for value in values) else
             "partial" if any(value is None for value in values) else "available")
    return RoughnessReading(mask, tuple(values), time.monotonic(), rpm, mask, state)


def catalog_for(family, definition_path=None, ecu_id=None):
    if family not in _CATALOG:
        raise ValueError(f"Unsupported measurement family: {family}")
    if definition_path:
        definition = load_logger_definition(definition_path)
        if definition.module_address != 0x12:
            raise ValueError("The selected logger definition does not target the engine ECU")
        key = str(ecu_id or "").upper()
        if any(key in ids for variant, ids in _EXACT_IDS.items() if variant != family):
            raise ValueError(f"Software {key} does not belong to {family}")
        key = {"0110C6": "7500255", "0110C7": "7500255", "7505859": "7500255",
               "430069": "7551615", "MS430069": "7551615",
               "MS43X001": "43X001"}.get(key, key)
        parameters = definition.parameters_for(key)
        if not parameters:
            raise ValueError(f"The selected logger definition has no channels for {family} software {key or 'unknown'}")
        channels = []
        for p in parameters:
            command = int(p.group or "0x0B", 0)
            subgroup = int(p.subgroup or "0", 0)
            if command != 0x0B:
                raise ValueError(f"Unsupported logger command for {p.name}")
            if p.groupsize:
                if subgroup not in {3, 4, 0x90, 0x91, 0x92, 0x93} or p.address + p.length > p.groupsize:
                    raise ValueError(f"Unsupported logger measurement group for {p.name}")
                channels.append(Channel(p.id, p.name, p.unit, subgroup, p.address + 3,
                                        p.length, parameter=p))
            elif p.address < 0x20:
                limit = 28 if family == "MS42" else 27
                if subgroup not in {1, 0x020E} or p.length != 2 or p.address >= limit:
                    raise ValueError(f"Unsupported logger ADC selector for {p.name}")
                channels.append(Channel(p.id, p.name, p.unit, 2, 3, 2, adc=p.address, parameter=p))
            else:
                maximum = 0x7FFFF if family == "MS42" else 0xEFFFF
                if subgroup not in {0, 1} or p.address + p.length - 1 > maximum:
                    raise ValueError(f"Unsupported logger memory address for {p.name}")
                channels.append(Channel(p.id, p.name, p.unit, 6, 3, p.length,
                                        address=p.address, parameter=p))
        return tuple(channels)
    layout = _revision_layout(family, ecu_id)
    addresses = layout.get("addresses", {})
    channels = tuple(replace(p, address=addresses[p.id]) if p.id in addresses else p
                     for p in _CATALOG[family])
    if str(ecu_id or "").upper() in _EXACT_IDS[family]:
        address = layout.get("roughness", _ROUGHNESS_LAYOUTS[family])[0] + 2
        channels += tuple(_c(key, f"Cylinder {index + 1} Roughness Quotient", "", 6, 3, 2,
                             .00122, signed=True, address=address + 2 * _FIRING_ORDER.index(index + 1))
                          for key, index in _ROUGHNESS_IDS.items())
    return channels


def selection_catalog(family, definition_path=None, ecu_id=None):
    return tuple((p.id, p.name, p.unit) for p in catalog_for(family, definition_path, ecu_id))


def display_rows(family, definition_path=None, ecu_id=None):
    # CSV/viewer labels are usable offline. The acquisition/selection catalog
    # still requires a matching connected software ID for passive RAM channels.
    if definition_path is None and ecu_id is None:
        ecu_id = {"MS42": "0110C6", "MS43": "430069"}.get(family)
    return [(p.name, p.unit) for p in catalog_for(family, definition_path, ecu_id)]


def display_spec(name, unit):
    """Viewer ranges are presentation choices, not ECU operating limits."""
    if any(p.name == name for p in _DIGITAL):
        return {"kind": "status", "evidence": "BMW"}
    ranges = {"Engine Speed": (0, 10000, 1000), "Vehicle Speed": (0, 300, 30),
              "Battery Voltage": (0, 20, 2), "Ignition Supply Voltage": (0, 20, 2),
              "Battery Voltage (after main relay)": (0, 20, 2),
              "Ignition Supply Voltage (KL15)": (0, 20, 2),
              "Intake Air Temperature": (-40, 160, 20), "Coolant Temperature": (-40, 160, 20),
              "Oil Temperature": (-40, 160, 20), "Throttle Angle": (0, 120, 20),
              "Accelerator Pedal": (0, 120, 20)}
    if name in ranges:
        low, high, step = ranges[name]
        return {"kind": "dial", "minimum": low, "maximum": high, "step": step,
                "source": "MS42/MS43", "evidence": "viewer"}
    return {"kind": "value", "evidence": "BMW"}


def _group_read(ecu, parameters):
    values = {}
    for command, request in dict.fromkeys((p.command, p.request) for p in parameters):
        selected = [p for p in parameters if (p.command, p.request) == (command, request)]
        payload = bytes(ecu.execute(command, request))
        required = max(p.offset + p.length - 3 for p in selected)
        if len(payload) < required:
            raise DS2Error(f"Short {ecu.family} measurement {request.hex(' ')}: {len(payload)}/{required}")
        for parameter in selected:
            start = parameter.offset - 3
            values[parameter.id] = parameter.decode(payload[start:start + parameter.length])
    return values


def read_adaptations(ecu, ecu_id=None):
    """Read BMW learned values; unavailable groups do not hide other groups."""
    parameters = [p for p in catalog_for(ecu.family, ecu_id=ecu_id) if p.adaptation]
    values, errors = {}, []
    for selector in dict.fromkeys(p.selector for p in parameters):
        group = [p for p in parameters if p.selector == selector]
        try:
            values.update(_group_read(ecu, group))
        except DS2Error as error:
            errors.append(str(error))
    result = {"ecu_id": ecu.family,
              "values": [(p.name, values.get(p.id), p.unit) for p in parameters],
              "errors": errors}
    if str(ecu_id or "").upper() in _EXACT_IDS[ecu.family]:
        # Exact firmware consumers index these as slot*64 + rpm*4 + load.
        # Their physical cylinder assignment is not established by this trace.
        default = ((0xC486, 0x4890D, 0x4891E) if ecu.family == "MS42"
                   else (0x40221, 0x7400C, 0x74095))
        table, rpm, load = _revision_layout(ecu.family, ecu_id).get("knock", default)

        def read_exact(address, count):
            raw = bytes(ecu.read_memory(address, count))
            if len(raw) != count:
                raise DS2Error(f"Short adaptation read at 0x{address:05X}: {len(raw)}/{count}")
            return raw

        try:
            rpm_values = [value * 32 for value in read_exact(rpm, 16)]
            load_values = [value * 1389 / 255 for value in read_exact(load, 4)]
            tables = []
            for slot in range(6):
                cells = [(value - 128) * .375 for value in read_exact(table + slot * 64, 64)]
                tables.append([cells[index:index + 4] for index in range(0, 64, 4)])
            result.update(rpm=rpm_values, load=load_values, knock=tables,
                          knock_labels=[f"Ignition slot {index + 1}" for index in range(6)])
        except DS2Error as error:
            errors.append(str(error))
    return result


def build_telegram_setup(family, entries):
    maximum = {"MS42": 0x7FFFF, "MS43": 0xEFFFF}[family]
    if not 1 <= len(entries) <= 24:
        raise ValueError("MS42/MS43 telegrams require 1–24 descriptors")
    payload = bytearray((1, len(entries)))
    for descriptor in entries:
        if len(descriptor) != 5:
            raise ValueError("Invalid MS42/MS43 telegram descriptor length")
        kind = descriptor[0]
        address = int.from_bytes(descriptor[1:], "big")
        if kind == 2 and address < (28 if family == "MS42" else 27):
            payload.extend(descriptor)
            continue
        if kind not in (0, 1) or not 0 <= address <= maximum or (kind == 1 and address % 2):
            raise ValueError("Invalid MS42/MS43 telegram descriptor")
        payload.extend(descriptor)
    return bytes(payload)


class MS42MS43LiveDataPoller(LiveDataPoller):
    """Use BMW fixed groups or exact-image RAM telegrams with shared capture UI."""

    def __init__(self, ecu, interval=.5, use_telegram=False, telegram_fallback=True,
                 selected_ids=None, ecu_id=None, definition_path=None, **kwargs):
        if interval < 0:
            raise ValueError("Live Data interval cannot be negative")
        parameters = catalog_for(ecu.family, definition_path, ecu_id)
        super().__init__(interval=interval, use_telegram=use_telegram, ds2=ecu,
                         telegram_fallback=telegram_fallback, selected_ids=selected_ids,
                         definition_path=definition_path, **kwargs)
        self._ecu_id = ecu_id
        self._custom_definition = bool(definition_path)
        self._available_params = parameters
        self._tel_params = [p for p in parameters if self._selected_ids is None or p.id in self._selected_ids]
        self._require_measurement_selection(self._tel_params)
        unknown = (self._selected_ids or set()) - {p.id for p in parameters}
        if unknown:
            raise ValueError(f"Unknown {ecu.family} measurement selection: {', '.join(sorted(unknown))}")

    def _prepare_live_profile(self):
        self._profile_ready = True

    def _poll_loop_ds2_reads(self):
        self._run_measurements(False)

    def _poll_loop_ds2_batch(self):
        self._run_measurements(True)

    def _run_measurements(self, use_telegram):
        self._prepare_live_profile()
        exact = self._custom_definition or str(self._ecu_id or "").upper() in _EXACT_IDS[self._ds2.family]
        roughness = [p for p in self._tel_params if not p.parameter and p.id in _ROUGHNESS_IDS]
        ordinary = [p for p in self._tel_params if p not in roughness]
        candidates = [p for p in ordinary if p.adc is not None or
                      (exact and p.address is not None and p.length in (1, 2)
                       and (p.length == 1 or p.address % 2 == 0))]
        entries = list(dict.fromkeys(p.descriptor for p in candidates))[:24]
        mapped = [p for p in candidates if p.descriptor in entries]
        remaining = [p for p in ordinary if p not in mapped]
        fixed_count = len({(p.command, p.request) for p in ordinary})
        batch_count = 1 + len({(p.command, p.request) for p in remaining})
        if use_telegram and (not entries or (self._telegram_fallback and batch_count > fixed_count)):
            if not entries and not self._telegram_fallback and not roughness:
                raise DS2Error("Configured telegrams require a matching reviewed software ID; select Standard or Auto")
            use_telegram = False
        registered = False
        try:
            if use_telegram:
                try:
                    # A lost acknowledgement can still leave the ECU registered.
                    registered = True
                    response = self._ds2.execute(0x0B, build_telegram_setup(self._ds2.family, entries))
                    if response:
                        raise DS2Error("Unexpected configured-telegram setup response")
                except DS2Error:
                    if not self._telegram_fallback:
                        raise
                    use_telegram = False
            self._acquisition_mode = "telegram" if use_telegram else "standard"
            self._ensure_csv()
            while not self._stop.is_set():
                started = time.monotonic()
                try:
                    values = {}
                    if use_telegram:
                        payload = bytes(self._ds2.execute(0x0B, b"\x00"))
                        expected = sum(1 if entry[0] == 0 else 2 for entry in entries)
                        if len(payload) != expected:
                            raise DS2Error(f"Telegram payload length {len(payload)}; expected {expected}")
                        offset, raw = 0, {}
                        for entry in entries:
                            length = 1 if entry[0] == 0 else 2
                            raw[entry] = payload[offset:offset + length]
                            offset += length
                        values.update((p.id, p.decode(raw[p.descriptor][::-1] if p.selector == 6 else raw[p.descriptor]))
                                      for p in mapped)
                    for command, request in dict.fromkeys((p.command, p.request)
                                                          for p in (remaining if use_telegram else ordinary)):
                        if self._stop.is_set():
                            break
                        group = [p for p in (remaining if use_telegram else ordinary)
                                 if (p.command, p.request) == (command, request)]
                        try:
                            values.update(_group_read(self._ds2, group))
                        except DS2Error as error:
                            with self._lock:
                                self._errors.append(str(error))
                            values.update((p.id, None) for p in group)
                    if roughness and not self._stop.is_set():
                        try:
                            reading = read_passive_roughness(self._ds2, self._ecu_id)
                            values.update((p.id, reading.cylinders[_ROUGHNESS_IDS[p.id]]) for p in roughness)
                        except DS2Error as error:
                            with self._lock:
                                self._errors.append(str(error))
                            values.update((p.id, None) for p in roughness)
                    if self._stop.is_set():
                        break
                    row = {p.name: p.display(values[p.id]) if values[p.id] is not None else "" for p in self._tel_params}
                    with self._lock:
                        self._latest.update((p.name, (row[p.name] or "—", p.unit)) for p in self._tel_params)
                    self._write_csv_row(row)
                    self._samples += 1
                except DS2Error as error:
                    with self._lock:
                        self._errors.append(str(error))
                        self._latest.update((p.name, ("ERR", p.unit)) for p in self._tel_params)
                self._stop.wait(max(0., self._interval - (time.monotonic() - started)))
        finally:
            if registered:
                if self._ds2.execute(0x0B, b"\xFF"):
                    raise DS2Error("Unexpected configured-telegram cleanup response")


def read_roughness(ecu):
    """BMW selector5 acknowledges its snapshot; preserve sign and firing order."""
    catalog_for(ecu.family)
    payload = bytes(ecu.execute(0x2B, b"\x05"))
    # Native MS43 additionally serializes six misfire counters after roughness.
    # 0110C6: 17-byte frame; 430069: 29-byte frame (both include 4 framing bytes).
    if len(payload) != (13 if ecu.family == "MS42" else 25):
        raise DS2Error(f"Unexpected {ecu.family} roughness payload length: {len(payload)}")
    values = [None] * 6
    if payload[0] == 0:
        for index, cylinder in enumerate((1, 5, 3, 6, 2, 4)):
            start = 1 + index * 2
            values[cylinder - 1] = int.from_bytes(payload[start:start + 2], "big", signed=True) * .00122
    return RoughnessReading(payload[0], tuple(values), time.monotonic(),
                            state="available" if payload[0] == 0 else "unavailable")
