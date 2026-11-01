"""Calibration switches with independently reviewed firmware consumers.

Offsets come from the matching XDFs and native consumers, not MS41 control bits.
Edits retain all other bytes; the existing image/write owner corrects checksums.
Learned transmission/A/C variants are diagnostic state, not these switches.
"""

from dataclasses import replace
from hashlib import sha256

from ecu_config import ConfigFeature
from ms42_ms43_image import FULL_SIZE, PROFILES, extract_calibration, image_identity


_BOOL = [("Disabled", 0), ("Enabled", 1)]
_PRESENT = [("Not fitted", 0), ("Fitted", 1)]
_ACTUATION = [("Off", 0), ("On", 1)]
_LAMBDA = [
    ("Learn single bank / catalyst preparation", 0),
    ("Learn dual bank / catalyst preparation", 1),
    ("Single bank: pre-cat and post-cat sensors", 2),
    ("Dual bank: pre-cat and post-cat sensors", 3),
]
_MIL = [("Disabled", 0), ("BMW fault criteria", 1),
        ("OBD faults after second driving cycle", 2), ("OBD faults immediately", 3)]
_CRUISE = [("Steering-wheel main switch", 0), ("Ignition-enabled (Z3)", 1)]
_BRAKE = [("High signal means pressed", 0), ("Low signal means pressed", 1)]
_HYSTERESIS = [("Conservative", 0), ("Responsive", 1)]
_DIAGNOSIS_BANK = [("Bank 1", 0), ("ECU-selected bank", 1)]
FULL_IMAGE_REQUIRED = "Full ROM file required"

_FEATURE_NOTES = {
    "Electrical cooling fan": "Tells the ECU whether an electric cooling fan is fitted.",
    "Exhaust flap": "Tells the ECU whether an exhaust flap is fitted; this is not an actuation command.",
    "Brake test switch polarity": "Selects whether a high or low brake-test switch signal means the pedal is pressed.",
    "Cruise control main switch": "Selects a steering-wheel main switch or ignition-enabled cruise control for the Z3.",
    "Malfunction indicator lamp": "Selects the fault criteria and driving-cycle timing used to illuminate the check-engine lamp.",
    "MIL without monitoring lambda sensors": "Selects the check-engine lamp criteria used without monitoring lambda sensors.",
    "Venturi pump": "Tells the ECU whether the venturi pump is fitted.",
    "Tank leak diagnosis module (DMTL)": "Tells the ECU whether the fuel-tank leak diagnosis module is fitted.",
    "Thermostat mechanical diagnosis": "Enables or disables thermostat mechanical fault checks; it does not switch off thermostat actuation.",
    "Crank pre-injection": "Allows or aborts pre-injection while cranking the engine.",
    "Secondary air valve lambda diagnosis": "Selects Bank 1 or the bank chosen by the ECU for secondary-air valve lambda diagnosis. The ECU uses Bank 1 for its single-bank configuration and Bank 2 otherwise; this is not a diagnosis on/off switch.",
    "Secondary air valve diagnosis": "Enables or disables secondary-air valve fault checks; it does not switch off the valve.",
    "Secondary air pump": "Selects automatic equipment learning or the secondary-air pump configuration, with or without an airflow meter.",
    "EVAP purge operation": "Selects the coolant threshold for normal tank-vapor purging or disables it. Other operating conditions still apply; actuator tests remain available.",
    "Purge-valve electrical diagnosis": "Controls new purge-valve electrical fault declarations. Disabling leaves stored faults and other EVAP diagnostics unchanged.",
    "Automatic purge-flow diagnosis": "Selects the coolant threshold for automatic purge-flow diagnosis or disables it. Explicit diagnostic test requests remain available.",
    "OBD distance with MIL reporting": "Enables or disables OBD reporting of distance travelled with the check-engine lamp on.",
    "Anti-jerk load reference": "Selects measured or substitute airflow as the load input for anti-jerk control.",
    "Alternator charge balancing": "Selects which transmission types use alternator charge balancing, or disables it.",
    "Fuel-cut hysteresis (manual)": "Selects the conservative or responsive fuel-cut hysteresis mode for a manual transmission.",
    "Fuel-cut hysteresis (automatic)": "Selects the conservative or responsive fuel-cut hysteresis mode for an automatic transmission.",
    "Exhaust temperature sensors": "Selects automatic learning, no exhaust temperature sensors, or the three-sensor configuration.",
    "First-gear torque limitation": "Enables or disables the first-gear torque limiter.",
    "Anti-jerk: torque converter open": "Enables or disables anti-jerk control for the torque-converter open state.",
    "Anti-jerk: torque converter controlled": "Enables or disables anti-jerk control for the torque-converter controlled state.",
    "Anti-jerk: torque converter closed": "Enables or disables anti-jerk control for the torque-converter closed state.",
    "Anti-jerk: automated-manual clutch open": "Enables or disables anti-jerk control when the automated-manual clutch is open.",
    "Anti-jerk: automated-manual clutch creeping": "Enables or disables anti-jerk control for the automated-manual clutch creeping state.",
    "Anti-jerk: automated-manual clutch closed": "Enables or disables anti-jerk control when the automated-manual clutch is closed.",
    "Anti-jerk: automated-manual clutch default state": "Enables or disables anti-jerk control for the automated-manual clutch default state.",
    "Throttle dashpot with clutch pressed": "Selects whether throttle dashpot control is retained or cancelled during overrun with the clutch pressed.",
    "Post-cat lambda readiness diagnosis": "Controls whether post-cat sensor checks are required when OBD readiness is initialized.",
    "Ambient temperature plausibility diagnosis": "Enables or disables the ambient-temperature signal plausibility check.",
    "Post-cat lambda signal-delay diagnosis": "Enables or disables the post-catalyst lambda-signal delay diagnosis; it does not disable the sensors.",
    "Closed-loop lambda feedback": "Controls normal closed-loop mixture feedback using speed in 32 rpm steps. Disabling uses the native admission threshold and lets existing correction return toward neutral; learned adaptations and sensor diagnostics are preserved. Other factory operating conditions still apply when enabled.",
    "Camshaft-assisted back-rotation detection": "Enables or disables use of camshaft phase in back-rotation detection.",
}

# One coordinated metadata setting spans calibration and program regions.
_MS42_CAL_CRC = ConfigFeature(
    "Calibration CRC", 0x7EFE, 0xFFFFFF, [("Enabled", 0), ("Disabled", 0xA8FFFF)],
    "Requires a full ROM file. Save the configured image and use the existing full-ROM write workflow.",
    full_file_only=True,
)


def _feature(name, offset, options=_BOOL, note=""):
    return ConfigFeature(name, offset, 0xFF, options, note or _FEATURE_NOTES.get(name, ""))


# MS42 consumers: 0133FA variant initialization, 02A3E4 brake polarity,
# 011452/015106 cruise, 07289E EOBD, 074AD4 pre-injection, 03D31A/03B7F8 diagnosis.
_MS42 = (
    _feature("Electrical cooling fan", 0x28A, _PRESENT),
    _feature("Exhaust flap", 0x288, _PRESENT),
    _feature("Exhaust flap actuation: below minimum speed", 0x16D, _ACTUATION,
             "Valve actuation below the calibrated minimum vehicle speed; the existing equipment and runtime conditions apply."),
    _feature("Brake test switch polarity", 0x289, _BRAKE),
    _feature("Lambda sensor configuration", 0x287, _LAMBDA,
             "Reset learned variants after changing the sensor configuration."),
    _feature("Closed-loop lambda feedback", 0x2A2, [("320 rpm threshold", 10), ("Disabled", 255)]),
    _feature("Octane adaptation learning", 0x3AA, [("Above 75 °C", 164), ("Disabled", 255)],
             "Coolant-temperature admission for learning the fuel octane factor. Does not reset learned data or disable knock detection, per-cylinder correction or fault handling."),
    _feature("Cruise control main switch", 0x28B, _CRUISE),
    _feature("Malfunction indicator lamp", 0x28C, _MIL),
    _feature("Venturi pump", 0x28E, _PRESENT),
    _feature("Thermostat mechanical diagnosis", 0x28F),
    _feature("Crank pre-injection", 0x290, [("Allowed", 0), ("Aborted", 1)]),
    _feature("Secondary air valve lambda diagnosis", 0x292, _DIAGNOSIS_BANK),
    _feature("Secondary air valve diagnosis", 0x293),
    _feature("Purge-control lambda feedback", 0x291, [("Bank 1", 0), ("Bank 2", 1)],
             "Selects the lambda bank used for purge mixture feedback and transition tracking; it does not disable lambda control."),
    _feature("EVAP purge operation", 0x3CE, [("Above 65.25 °C", 151), ("Disabled", 255)]),
    _feature("Purge-valve electrical diagnosis", 0x210, [("10-count threshold", 10), ("Disabled", 0)]),
    _feature("Leak-detection pump electrical fault threshold", 0x1EF,
             [("10-count threshold", 10), ("Disabled", 0)],
             "Threshold for new pump electrical fault declarations. Other factory diagnosis conditions still apply. Disabling preserves stored faults and does not disable pump operation or automatic leak testing."),
    _feature("Automatic purge-flow diagnosis", 0x3C1, [("Above 141.75 °C", 253), ("Disabled", 255)]),
    _feature("OBD distance with MIL reporting", 0x24E),
    _feature("Anti-jerk: torque converter open", 0x319),
    _feature("Anti-jerk: torque converter controlled", 0x31B),
    _feature("Anti-jerk: torque converter closed", 0x31C),
    _feature("Anti-jerk: torque converter default state", 0x31A,
             note="Also used when the transmission message is unavailable."),
    _MS42_CAL_CRC,
)

# MS43 variant owner09DB3E, brake0B051E, cruise096BE6, charge0B01B2,
# anti-jerk09D4CC, fuel-cut hysteresis0BF2C0, SAP0BEEBC and EGT096C24.
_MS43 = (
    _feature("Electrical cooling fan", 0xB1D, _PRESENT),
    _feature("Exhaust flap", 0xB1E, _PRESENT),
    _feature("Exhaust flap actuation: below minimum speed (automatic)", 0xC54, _ACTUATION,
             "Valve actuation below the automatic-transmission minimum vehicle speed, outside catalyst-heating state 2."),
    _feature("Exhaust flap actuation: below minimum speed (manual)", 0xC55, _ACTUATION,
             "Valve actuation below the manual-transmission minimum vehicle speed, outside catalyst-heating state 2."),
    _feature("Exhaust flap actuation: catalyst-heating state 2", 0xC4E, _ACTUATION,
             "Valve actuation during catalyst-heating state 2 below the calibrated minimum vehicle speed."),
    _feature("Brake test switch polarity", 0xB17, _BRAKE),
    _feature("Lambda sensor configuration", 0xB19,
             _LAMBDA + [("Learn dual-bank sensor configuration", 4)],
             "Reset learned variants after changing the sensor configuration."),
    _feature("Closed-loop lambda feedback", 0xBBB, [("480 rpm threshold", 15), ("Disabled", 255)]),
    _feature("Octane adaptation learning", 0xCD7, [("Above 75 °C", 164), ("Disabled", 255)],
             "Coolant-temperature admission for learning the fuel octane factor. Does not reset learned data or disable knock detection, per-cylinder correction or fault handling."),
    _feature("Cruise control main switch", 0xB1B, _CRUISE),
    _feature("Malfunction indicator lamp", 0xB21, _MIL),
    _feature("MIL without monitoring lambda sensors", 0xB22, _MIL),
    _feature("Tank leak diagnosis module (DMTL)", 0xB1C, _PRESENT),
    _feature("Thermostat mechanical diagnosis", 0xB27),
    _feature("Crank pre-injection", 0xB29, [("Allowed", 0), ("Aborted", 1)]),
    _feature("Secondary air valve lambda diagnosis", 0xB20, _DIAGNOSIS_BANK),
    _feature("Secondary air valve diagnosis", 0xB26),
    _feature("Secondary air pump", 0xB25,
             [("Automatic learning", 0), ("Not fitted", 1),
              ("Fitted without airflow meter", 2), ("Fitted with airflow meter", 3)]),
    _feature("EVAP purge operation", 0xCFA, [("Above 65.25 °C", 151), ("Disabled", 255)]),
    _feature("Purge-valve electrical diagnosis", 0xA83, [("10-count threshold", 10), ("Disabled", 0)]),
    _feature("Automatic purge-flow diagnosis", 0xCFC, [("Above 85.5 °C", 178), ("Disabled", 255)]),
    _feature("OBD distance with MIL reporting", 0xB1F),
    _feature("Anti-jerk load reference", 0xB16,
             [("Measured airflow", 0), ("Substitute airflow", 1)]),
    _feature("CAN calibration logic", 0xB18,
             note="Controls the factory calibration logic; this is not a CAN communications on/off switch."),
    _feature("Alternator charge balancing", 0xB1A,
             [("Disabled", 0), ("Manual transmission", 1),
              ("Automatic transmission", 2), ("Both transmissions", 3)]),
    _feature("Fuel-cut hysteresis (manual)", 0xB23, _HYSTERESIS),
    _feature("Fuel-cut hysteresis (automatic)", 0xB24, _HYSTERESIS),
    _feature("Exhaust temperature sensors", 0xB28,
             [("Automatic learning", 0), ("Not fitted", 1), ("Three sensors", 2)]),
    _feature("First-gear torque limitation", 0xB2A),
    _feature("Anti-jerk: torque converter open", 0xE0D),
    _feature("Anti-jerk: torque converter controlled", 0xE0F),
    _feature("Anti-jerk: torque converter closed", 0xE10),
    _feature("Anti-jerk: torque converter default state", 0xE0E,
             note="Also used when the transmission message is unavailable."),
    _feature("Anti-jerk: automated-manual clutch open", 0xC52),
    _feature("Anti-jerk: automated-manual clutch creeping", 0xC50),
    _feature("Anti-jerk: automated-manual clutch closed", 0xC4F),
    _feature("Anti-jerk: automated-manual clutch default state", 0xC51),
    _feature("DS2 bulk-adaptation writes", 0xDEF,
             [("Allowed with native conditions", 0), ("Blocked", 1)],
             "Controls the stock diagnostic write command, not adaptation learning."),
    _feature("Throttle dashpot with clutch pressed", 0xE00,
             [("Apply during overrun", 0), ("Cancel during overrun", 1)]),
    _feature("Post-cat lambda readiness diagnosis", 0xE01,
             [("Required for readiness", 0), ("Excluded from readiness", 1)]),
    _feature("Fuel tank level plausibility diagnosis", 0xE02,
             note="The native check also requires the DMTL module configuration."),
    _feature("Ambient temperature plausibility diagnosis", 0xE03,
             [("Enabled", 0), ("Disabled", 1)]),
    _feature("Post-cat lambda signal-delay diagnosis", 0xE04),
    _feature("Accelerator / brake plausibility", 0xE05,
             [("Disabled", 0), ("Limit pedal demand", 1),
              ("Limit pedal demand and report faults", 2)],
             "Mode 2 is the reviewed stock setting; mode 1 retains intervention without fault reporting."),
    _feature("Individual-cylinder cut during traction intervention", 0xE06,
             note="Selects one intervention method; this does not disable ASC/DSC."),
    _feature("Calibration monitor checksum", 0xE07,
             [("Enabled", 1), ("Disabled", 0xA5)],
             "Controls the runtime calibration monitor check. Does not change program monitoring or flash CRC checks."),
    _feature("Camshaft-assisted back-rotation detection", 0xE08),
)
# C7/CA use the same calibration offsets, with independently traced consumers.
# The coordinated CRC edit also verifies the supplied full image's actual owners.
_MS42_C7 = _MS42
_MS42_CA = tuple(
    replace(feature, options=[("Above 90 °C", 184), ("Disabled", 255)])
    if feature.byte == 0x3C1 else
    replace(feature, options=[("Above 80.25 °C", 171), ("Disabled", 255)])
    if feature.byte == 0x3AA else feature for feature in _MS42
)

# X001 has relocated owners and fewer reviewed choices for two modified modes.
_MS43_X001 = tuple(
    replace(feature, options=_LAMBDA) if feature.byte == 0xB19 else
    replace(feature, options=feature.options[:3],
            note="Selects automatic equipment learning, no pump, or a pump without an airflow meter.")
    if feature.byte == 0xB25 else feature
    for feature in _MS43 if feature.byte in {
    0xB1D, 0xB1E, 0xB27, 0xB18, 0xBBB, 0xCD7,
    0xC54, 0xC55, 0xC4E, 0xB17, 0xB21, 0xB22, 0xB29, 0xB1F, 0xB16,
    0xE0D, 0xE0F, 0xE10, 0xE0E, 0xDEF, 0xE00, 0xE02, 0xE03, 0xE04,
    0xE05, 0xE07, 0xE08,
    0xB19, 0xB1C, 0xB20, 0xB26, 0xB25, 0xCFA, 0xA83, 0xCFC,
    0xB1A, 0xB23, 0xB24, 0xB2A, 0xC52, 0xC50, 0xC4F, 0xC51, 0xE01, 0xE06,
})
_FEATURES = {
    ("MS42", "0110C6"): _MS42,
    ("MS42", "0110C7"): _MS42_C7,
    ("MS42", "0110CA"): _MS42_CA,
    ("MS43", "430069"): _MS43,
    ("MS43", "43X001"): _MS43_X001,
}

# Older releases relocate these reviewed consumers; other settings stay unmapped.
for _software, _offsets in {
    "430037": {0xB1D: 0xA3D, 0xB1E: 0xA3E, 0xB27: 0xA47, 0xB18: 0xA38,
               0xB1F: 0xA3F, 0xB21: 0xA41, 0xB22: 0xA42,
               0xE0D: 0xCE8, 0xE0F: 0xCEA, 0xE10: 0xCEB, 0xE0E: 0xCE9,
               0xDEF: 0xCD0, 0xE05: 0xCE1, 0xE07: 0xCE3, 0xBBB: 0xACE,
               0xB19: 0xA39, 0xB1B: 0xA3B, 0xB20: 0xA40, 0xB26: 0xA46,
               0xB1C: 0xA3C, 0xB29: 0xA49, 0xB1A: 0xA3A, 0xB25: 0xA45,
               0xCFA: 0xBF8, 0xA83: 0x9AE, 0xCFC: 0xBFA, 0xB23: 0xA43, 0xB24: 0xA44,
               0xB2A: 0xA4A, 0xE06: 0xCE2, 0xCD7: 0xBD7,
               0xC54: 0xB62, 0xC55: 0xB63, 0xC4E: 0xB61, 0xB17: 0xA37},
    "430055": {0xB1D: 0xABD, 0xB1E: 0xABE, 0xB21: 0xAC1, 0xB22: 0xAC2,
               0xB27: 0xAC7, 0xB18: 0xAB8, 0xB1F: 0xABF, 0xBBB: 0xB54,
               0xE0D: 0xD81, 0xE0F: 0xD83, 0xE10: 0xD84, 0xE0E: 0xD82,
               0xC52: 0xBE6, 0xC50: 0xBE4, 0xC4F: 0xBE3, 0xC51: 0xBE5,
               0xDEF: 0xD68, 0xE05: 0xD7A, 0xE07: 0xD7C,
               0xB19: 0xAB9, 0xB1B: 0xABB, 0xB20: 0xAC0, 0xB26: 0xAC6,
               0xB1C: 0xABC, 0xB29: 0xAC9, 0xB1A: 0xABA, 0xB16: 0xAB6,
               0xB25: 0xAC5, 0xB28: 0xAC8, 0xCFA: 0xC87, 0xA83: 0xA27, 0xCFC: 0xC89,
               0xB23: 0xAC3, 0xB24: 0xAC4, 0xB2A: 0xACA, 0xE06: 0xD7B, 0xE00: 0xD79, 0xCD7: 0xC66,
               0xC54: 0xBE8, 0xC55: 0xBE9, 0xC4E: 0xBE2, 0xB17: 0xAB7},
    "430064": {0xB1D: 0xAD1, 0xB1E: 0xAD2, 0xB27: 0xADB, 0xB18: 0xACC,
               0xB1F: 0xAD3, 0xB21: 0xAD5, 0xB22: 0xAD6,
               0xE0D: 0xDA6, 0xE0F: 0xDA8, 0xE10: 0xDA9, 0xE0E: 0xDA7,
               0xC52: 0xC03, 0xC50: 0xC01, 0xC4F: 0xC00, 0xC51: 0xC02,
               0xDEF: 0xD8B, 0xE05: 0xD9E, 0xE07: 0xDA0, 0xBBB: 0xB6C,
               0xB19: 0xACD, 0xB1B: 0xACF, 0xB20: 0xAD4, 0xB26: 0xADA,
               0xB1C: 0xAD0, 0xB29: 0xADD, 0xB1A: 0xACE, 0xB16: 0xACA,
               0xB25: 0xAD9, 0xB28: 0xADC, 0xCFA: 0xCA5, 0xA83: 0xA3B, 0xCFC: 0xCA7,
               0xB23: 0xAD7, 0xB24: 0xAD8, 0xB2A: 0xADE, 0xE06: 0xD9F, 0xE08: 0xDA1,
               0xE00: 0xD9C, 0xE04: 0xD9D, 0xC54: 0xC05, 0xC55: 0xC06, 0xC4E: 0xBFF,
               0xB17: 0xACB, 0xCD7: 0xC84},
    "430065": {0xB1D: 0xB23, 0xB1E: 0xB24, 0xB27: 0xB2D, 0xB18: 0xB1E,
               0xB1F: 0xB25, 0xB21: 0xB27, 0xB22: 0xB28,
               0xC54: 0xC59, 0xC55: 0xC5A, 0xC4E: 0xC53,
               0xE0D: 0xE0F, 0xE0F: 0xE11, 0xE10: 0xE12, 0xE0E: 0xE10,
               0xC52: 0xC57, 0xC50: 0xC55, 0xC4F: 0xC54, 0xC51: 0xC56,
               0xDEF: 0xDF2, 0xE05: 0xE07, 0xE07: 0xE09, 0xBBB: 0xBC0,
               0xB19: 0xB1F, 0xB1B: 0xB21, 0xB20: 0xB26, 0xB26: 0xB2C,
               0xB1C: 0xB22, 0xB29: 0xB2F, 0xB1A: 0xB20, 0xB16: 0xB1C,
               0xB25: 0xB2B, 0xB28: 0xB2E, 0xCFA: 0xCFF, 0xA83: 0xA8A, 0xCFC: 0xD01,
               0xB23: 0xB29, 0xB24: 0xB2A, 0xB2A: 0xB30, 0xE06: 0xE08, 0xE08: 0xE0A,
               0xE00: 0xE03, 0xE03: 0xE05, 0xE04: 0xE06, 0xB17: 0xB1D,
               0xE02: 0xE04, 0xCD7: 0xCDD},
    "430066": {0xB17: 0xB1F, 0xB18: 0xB20, 0xB1D: 0xB25, 0xB1E: 0xB26,
               0xB1F: 0xB27, 0xB21: 0xB29, 0xB22: 0xB2A, 0xB27: 0xB2F,
               0xC54: 0xC5B, 0xC55: 0xC5C, 0xC4E: 0xC55,
               0xE0D: 0xE10, 0xE0F: 0xE12, 0xE10: 0xE13, 0xE0E: 0xE11,
               0xC52: 0xC59, 0xC50: 0xC57, 0xC4F: 0xC56, 0xC51: 0xC58,
               0xDEF: 0xDF3, 0xE05: 0xE08, 0xE07: 0xE0A, 0xBBB: 0xBC2,
               0xB19: 0xB21, 0xB1B: 0xB23, 0xB20: 0xB28, 0xB26: 0xB2E,
               0xB1C: 0xB24, 0xB29: 0xB31, 0xB1A: 0xB22, 0xB16: 0xB1E,
               0xB25: 0xB2D, 0xB28: 0xB30, 0xCFA: 0xD00, 0xA83: 0xA8C, 0xCFC: 0xD02,
               0xE02: 0xE05, 0xB23: 0xB2B, 0xB24: 0xB2C, 0xB2A: 0xB32,
               0xE06: 0xE09, 0xE08: 0xE0B, 0xE00: 0xE04, 0xE03: 0xE06, 0xE01: 0xE26,
               0xE04: 0xE07, 0xCD7: 0xCDE},
}.items():
    _FEATURES[("MS43", _software)] = tuple(
        replace(feature, byte=_offsets[feature.byte],
                options=([("Above 141.75 °C", 253), ("Disabled", 255)]
                         if feature.byte == 0xCFC else feature.options))
        for feature in _MS43 if feature.byte in _offsets
    )



def features_for(family, software_id):
    """Return only the stock switch map reviewed for this software identity."""
    return _FEATURES.get((str(family).upper(), str(software_id).upper()), ())


def ms42_crc_owner_matches(data):
    """Qualify the coordinated CRC edit from a full image, with no ECU reads."""
    return len(data) == FULL_SIZE and all(
        sha256(data[first:last]).hexdigest() == expected
        for first, last, expected in (
            (0xE88, 0xF08, "e71d6f3a1aab8ec32a352306acfc475ff9a2a7a1f99c1ddbaaaf2b122c40d562"),
            (0x1C3C8, 0x1C410, "f830ae88b187e2880cdd3f45215f678daad67531219ec00c86a9763ea2fde5d3"),
        )
    )


def _image_features(data, family, software_id):
    identity = image_identity(data, family)
    if software_id is not None and str(software_id).upper() != identity["software_id"].upper():
        raise ValueError("Configuration software does not match the image")
    features = features_for(identity["family"], identity["software_id"])
    if not features:
        raise ValueError("No verified configuration map for this software version")
    if len(data) == FULL_SIZE:
        extract_calibration(data)
        base = PROFILES[identity["family"]].calibration_offset
    else:
        base = 0
    return features, base


def read_config(data, family=None, software_id=None):
    """Decode editable settings from a full image or matching calibration."""
    features, base = _image_features(data, family, software_id)
    return {feature.name: _current(data, base, feature) for feature in features}


def _current(data, base, feature):
    if feature is _MS42_CAL_CRC:
        if len(data) != FULL_SIZE:
            return FULL_IMAGE_REQUIRED
        value = int.from_bytes(data[0x4FEFE:0x4FF00], "little") | data[0x5002C] << 16
    else:
        value = data[base + feature.byte]
    return feature.current(value)


def apply_config(data, selections, family=None, software_id=None):
    """Apply named choices, preserving every unselected byte and unknown value."""
    features, base = _image_features(data, family, software_id)
    by_name = {feature.name: feature for feature in features}
    result = bytearray(data)
    for name, label in selections.items():
        if name not in by_name:
            raise ValueError(f"Unknown configuration setting: {name}")
        feature = by_name[name]
        offset = base + feature.byte
        if label == _current(result, base, feature):
            continue
        if feature.full_file_only and len(result) != FULL_SIZE:
            raise ValueError(f"{name} requires a full ROM file")
        if label not in dict(feature.options):
            raise ValueError(f"Invalid choice for {name}: {label}")
        if feature is _MS42_CAL_CRC:
            if (image_identity(result)["software_id"] in {"0110C7", "0110CA"}
                    and not ms42_crc_owner_matches(result)):
                raise ValueError("Calibration CRC owners differ from the verified MS42 implementation")
            value = dict(feature.options)[label]
            result[0x4FEFE:0x4FF00] = (value & 0xFFFF).to_bytes(2, "little")
            result[0x5002C] = value >> 16
        else:
            result[offset] = feature.apply(result[offset], label)
    return bytes(result)


def read_live_config(ecu, software_id):
    """Read compact spans that fit the stock 123-byte response payload."""
    features = features_for(ecu.family, software_id)
    if not features:
        raise ValueError("No verified configuration map for this software version")
    groups = [[]]
    for feature in sorted((item for item in features if not item.full_file_only), key=lambda item: item.byte):
        if groups[-1] and feature.byte - groups[-1][0].byte >= 123:
            groups.append([])
        groups[-1].append(feature)
    values = {}
    for group in groups:
        first = group[0].byte
        length = group[-1].byte - first + 1
        data = ecu.read_memory(PROFILES[ecu.family].calibration_offset + first, length)
        if len(data) != length:
            raise ValueError("Short configuration response")
        values.update({feature.name: feature.current(data[feature.byte - first]) for feature in group})
    return {feature.name: values.get(feature.name, FULL_IMAGE_REQUIRED) for feature in features}
