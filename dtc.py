"""BMW MS41 DS2 diagnostic-trouble-code database and parser.

Command 0x04 returns a count byte followed by that many ten-byte records.
Record byte 0 contains the single-byte DTC and record byte 1 contains its
state flags. The layout was verified against an MS41 serial capture and the
MS41.3 response builder.
"""

from dataclasses import dataclass


def format_dtc_table(dtcs: list) -> str:
    """Return a human-readable plain-text table of DTCs."""
    if not dtcs:
        return "No DTCs stored."
    lines = [
        f"{'Fault Code':<10} {'Reference':<10} {'System':<22} {'Status':<30} Description",
        "-" * 115,
    ]
    for d in dtcs:
        lines.append(
            f"{d.code_hex:<10} {d.sae_code:<10} {d.system:<22} {d.status_text:<30} {d.description}"
        )

    lines.extend((
        "",
        "Raw DTC Records",
        "-" * 115,
        f"{'Fault Code':<10} {'Reference':<10} Raw Record",
        "-" * 115,
    ))
    for d in dtcs:
        lines.append(
            f'{d.code_hex:<10} {d.sae_code:<10} {d.raw_record.hex(" ").upper()}'
        )
        conditions = getattr(d, "conditions", ())
        if conditions:
            lines.append(f"{'':<21}Conditions: {'; '.join(conditions)}")
        reported_total = getattr(d, "reported_total", None)
        if reported_total is not None:
            lines.append(f"{'':<21}Module Fault Count: {reported_total}")
        if d.self_test_reason is not None:
            lines.append(f"{'':<21}Self-Test Reason: {d.self_test_reason}")
    return "\n".join(lines)

# ---------------------------------------------------------------------------
# DS2 single-byte DTC database (BMW MS41 / MS42 / MS43)
# Primary source: openms41.sites.google.com DTC reference table (verified).
# Code 46 is supplemented from hash-bound BMW MS411DS2.PRG FORTTEXTE.
# Code = single byte integer (1–255), matches byte[0] of each 10-byte DS2 record
# ---------------------------------------------------------------------------

DS2_DTC_DB: dict = {
    1:   ("Ignition coil Cyl 2",                        "Ignition"),
    2:   ("Ignition coil Cyl 4",                        "Ignition"),
    3:   ("Ignition coil Cyl 6",                        "Ignition"),
    5:   ("Fuel injector Cyl 2",                        "Fuel Injectors"),
    6:   ("Fuel injector Cyl 1",                        "Fuel Injectors"),
    8:   ("Air Flow Meter (HFM)",                       "Air Intake"),
    10:  ("Coolant Temperature Sensor",                 "Temperature"),
    11:  ("Tank Pressure Sensor / Radiator Outlet Temp","Emissions"),
    12:  ("TPS or Plausibility - Max Coolant Temp",     "Throttle"),
    13:  ("Plausibility - Radiator Outlet Temp",        "Temperature"),
    14:  ("Intake Air Temperature Sensor",              "Temperature"),
    15:  ("Plausibility - Cut Out Time",                "ECU Internal"),
    16:  ("AirCon Compressor PWM / Intake Air Temp",    "A/C"),
    17:  ("Plausibility - Engine Coolant Temp",         "Temperature"),
    18:  ("EWS Signal or Camshaft Sensor",              "Immobiliser"),
    19:  ("VANOS Inlet/Exhaust Valve Activation",       "VANOS"),
    20:  ("CHECK ENGINE Light Failure",                 "ECU Internal"),
    21:  ("VANOS Electrical Fault or Inlet Valve",      "VANOS"),
    22:  ("Fuel Injector Cyl 3",                        "Fuel Injectors"),
    23:  ("Fuel Injector Cyl 6",                        "Fuel Injectors"),
    24:  ("Fuel Injector Cyl 4",                        "Fuel Injectors"),
    25:  ("Lambda Sensor Heater Bank 1",                "Lambda / Fueling"),
    27:  ("Idle Control Valve Malfunction",             "Idle Control"),
    29:  ("Ignition Coil Cyl 1",                        "Ignition"),
    30:  ("Ignition Coil Cyl 3",                        "Ignition"),
    31:  ("Ignition Coil Cyl 5",                        "Ignition"),
    33:  ("Fuel Injector Cyl 5",                        "Fuel Injectors"),
    35:  ("Aux. Air Injection System Relay",            "Emissions"),
    36:  ("DME Main Relay",                             "Power Supply"),
    37:  ("DME Main Relay Delay",                       "Power Supply"),
    38:  ("Clutch Switch - Plausibility",               "Input Signals"),
    39:  ("Brake Light Switch / Test Switch",           "Input Signals"),
    40:  ("Brake Light Switch / Pedal Signal",          "Input Signals"),
    42:  ("Multi Function Steering Wheel - Plausibility","Communication"),
    43:  ("Multi Function Steering Wheel Button",       "Communication"),
    45:  ("Multi Function Steering Wheel Port",         "Communication"),
    46:  ("Fuel-Level Reserve-Lamp Signal",             "Fuel System"),
    47:  ("Temp Sensor Downstream Pre-Cat",             "Temperature"),
    48:  ("DME Control Unit - Self Test 1",             "ECU Internal"),
    49:  ("DME Control Unit",                           "ECU Internal"),
    50:  ("EVAP Control Valve",                         "Emissions"),
    51:  ("Shut-off Valve Charcoal Filter",             "Emissions"),
    52:  ("Solenoid Valve - Exhaust Flap",              "Emissions"),
    53:  ("Idle Speed Actuator",                        "Idle Control"),
    55:  ("Lambda Sensor Heater Bank 2",                "Lambda / Fueling"),
    56:  ("Ignition Current Feedback Resistor Open",    "Ignition"),
    57:  ("Knock Sensor Bank 1",                        "Knock Control"),
    58:  ("DME Control Unit - Self Test 2",             "ECU Internal"),
    59:  ("Knock Sensor Bank 2",                        "Knock Control"),
    61:  ("Lambda Sensor Heater Bank 2 Post Cat",       "Lambda / Fueling"),
    62:  ("Aux. Air Injection Switching Valve",         "Emissions"),
    63:  ("DME Control Unit / Ambient Temp via CAN",    "ECU Internal"),
    64:  ("Plausibility - Ambient Temperature",         "Temperature"),
    65:  ("Camshaft Position Sensor",                   "Crank/Cam"),
    66:  ("DME Control Unit",                           "ECU Internal"),
    67:  ("DME Control Unit",                           "ECU Internal"),
    68:  ("Tank Venting Valve",                         "Emissions"),
    69:  ("Fuel Pump Relay",                            "Fuel System"),
    70:  ("DME Control Unit",                           "ECU Internal"),
    71:  ("DME Control Unit",                           "ECU Internal"),
    72:  ("DME Control Unit",                           "ECU Internal"),
    74:  ("AirCon Compressor Relay",                    "A/C"),
    75:  ("Lambda Sensor Voltage Bank 1",               "Lambda / Fueling"),
    76:  ("Lambda Sensor Voltage Bank 2",               "Lambda / Fueling"),
    77:  ("Lambda Sensor Voltage Bank 1 Post Cat",      "Lambda / Fueling"),
    78:  ("Lambda Sensor Voltage Bank 2 Post Cat",      "Lambda / Fueling"),
    79:  ("Lambda Sensor Heater Bank 1 Post Cat",       "Lambda / Fueling"),
    80:  ("ABS/ASC Interface",                          "Communication"),
    81:  ("MSR Signal - Active Too Long",               "Communication"),
    82:  ("ABS/ASC Interface - Advance Adjustment",     "Communication"),
    83:  ("Crankshaft Sensor",                          "Crank/Cam"),
    90:  ("Exhaust Temp Pre Cat Bank 1",                "Temperature"),
    91:  ("Exhaust Temp Pre Cat Bank 2",                "Temperature"),
    92:  ("Exhaust Temp Post Cat Bank 1",               "Temperature"),
    93:  ("Exhaust Temp Post Cat Bank 2",               "Temperature"),
    94:  ("Auxiliary Air - Mass Flow Sensor",           "Emissions"),
    95:  ("Auxiliary Air Valve or Hose Blocked",        "Emissions"),
    96:  ("Auxiliary Air Pump Function",                "Emissions"),
    97:  ("Auxiliary Air - Flow Rate Too Low",          "Emissions"),
    98:  ("Auxiliary Air - Flow Rate Too High",         "Emissions"),
    99:  ("Auxiliary Air Valve Jammed Open",            "Emissions"),
    100: ("DME Control Unit - Self-Test Failed",        "ECU Internal"),
    103: ("VANOS Error - Inlet Camshaft",               "VANOS"),
    104: ("VANOS Error - Exhaust Camshaft",             "VANOS"),
    105: ("VANOS Error - Position Inlet Camshaft",      "VANOS"),
    106: ("VANOS Error - Position Exhaust Camshaft",    "VANOS"),
    109: ("Throttle Valve Plausibility",                "Throttle"),
    110: ("Pedal Sensor Value Potentiometer 1",         "Throttle"),
    111: ("Pedal Sensor Value Potentiometer 2",         "Throttle"),
    112: ("TPS Potentiometer 1",                        "Throttle"),
    113: ("TPS Potentiometer 2",                        "Throttle"),
    114: ("Throttle Valve Final Stage",                 "Throttle"),
    115: ("Reference Voltage Regulator 1",              "ECU Internal"),
    116: ("Reference Voltage Regulator 2",              "ECU Internal"),
    117: ("Plausibility - Pedal Position Sensor 1/2",  "Throttle"),
    118: ("Plausibility - TPS 1/2",                    "Throttle"),
    119: ("Throttle Valve Sensor Mechanical Error",     "Throttle"),
    120: ("Plausibility Pedal Sensor or TPS",          "Throttle"),
    122: ("Engine Oil Temperature",                     "Temperature"),
    123: ("Map Cooling Thermostat Control",             "Cooling"),
    124: ("Activation DISA Solenoid",                   "Air Intake"),
    125: ("Activation Electric Fan",                    "Cooling"),
    126: ("Activation Tank Leak Pump Solenoid",         "Emissions"),
    127: ("Activation Pump Solenoid",                   "Emissions"),
    128: ("DME/EWS Communication",                      "Immobiliser"),
    129: ("CAN Signal SMG 1",                           "Communication"),
    130: ("CAN Signal ASC - Timeout",                   "Communication"),
    131: ("CAN Signal Instrument Cluster - Timeout",    "Communication"),
    132: ("CAN Signal Instrument Cluster - Timeout",    "Communication"),
    133: ("CAN Signal ASC - Timeout",                   "Communication"),
    134: ("SMG Intervention - Plausibility",            "Communication"),
    135: ("Throttle Valve Re-Adaptation Required",      "Throttle"),
    136: ("Throttle Valve Spring Test Failed",          "Throttle"),
    137: ("CAN Signal - Steering Angle Sensor",         "Communication"),
    139: ("CAN Signal - Tank Level Sensor",             "Communication"),
    140: ("Tank Leak Pump Solenoid Reed Switch",        "Emissions"),
    141: ("Tank Leak Pump Reed Switch Stuck",           "Emissions"),
    142: ("Tank Leak Pump Reed Switch Open / DMTL",    "Emissions"),
    143: ("Tank Ventilation / Leakage",                 "Emissions"),
    144: ("Fuel System Large Leak",                     "Emissions"),
    145: ("Fuel System Small Leak",                     "Emissions"),
    146: ("EVAP Small Leak",                            "Emissions"),
    147: ("Pedal Position Sensor Supply Channel 1",     "Throttle"),
    149: ("Air Flow Sensor or Pedal Sensor Mismatch",   "Air Intake"),
    150: ("Lambda Post Cat Bank 1 Max Limit",           "Lambda / Fueling"),
    151: ("Lambda Post Cat Bank 2 Max Limit",           "Lambda / Fueling"),
    152: ("Lambda Post Cat Bank 1 Min Limit",           "Lambda / Fueling"),
    153: ("Lambda Pre Cat Bank 2 Max Limit",            "Lambda / Fueling"),
    154: ("Lambda Pre Cat Bank 2 Min Limit",            "Lambda / Fueling"),
    155: ("Lambda Pre Cat Bank 2 No Signal",            "Lambda / Fueling"),
    156: ("Lambda Pre Cat Bank 1 No Signal",            "Lambda / Fueling"),
    157: ("Lambda Post Cat Bank 1 Min Limit",           "Lambda / Fueling"),
    159: ("Lambda Post Cat Bank 2 Max Limit",           "Lambda / Fueling"),
    160: ("Lambda Post Cat Bank 2 / Throttle Stuck",    "Lambda / Fueling"),
    161: ("Throttle Valve Stuck",                       "Throttle"),
    162: ("Throttle Valve Control Deviation",           "Throttle"),
    168: ("Pedal Position Sensor Pot Supply 1",         "Throttle"),
    169: ("Throttle Valve Output Stage Cutoff",         "Throttle"),
    170: ("DME Control Unit - Self Test Failed",        "ECU Internal"),
    171: ("Plausibility - Throttle Valve",              "Throttle"),
    172: ("Pedal Sensor Potentiometer 1/2 Short",       "Throttle"),
    173: ("TPS Potentiometer 1/2 Short Circuit",        "Throttle"),
    174: ("Throttle Valve Potentiometer 1/2 Adaptation","Throttle"),
    175: ("Pedal Sensor 1 Adaptation",                  "Throttle"),
    176: ("Pedal Sensor 2 Adaptation",                  "Throttle"),
    186: ("Voltage Post Cat Bank 1",                    "Lambda / Fueling"),
    187: ("Voltage Post Cat Bank 2",                    "Lambda / Fueling"),
    188: ("Voltage Pre Cat Bank 1",                     "Lambda / Fueling"),
    189: ("Voltage Pre Cat Bank 2",                     "Lambda / Fueling"),
    190: ("EVAP Reed Switch Open",                      "Emissions"),
    191: ("EVAP Reed Switch Closed",                    "Emissions"),
    192: ("EVAP Reed Switch Open",                      "Emissions"),
    193: ("EVAP Check Hoses",                           "Emissions"),
    194: ("EVAP Large Leak",                            "Emissions"),
    195: ("EVAP Small Leak",                            "Emissions"),
    196: ("EVAP Electrical Valve / Barometric Pressure","Emissions"),
    197: ("EVAP Barometric Pressure Sensor",            "Emissions"),
    198: ("Cat Efficiency during Start Bank 1",         "Catalyst"),
    199: ("Cat Efficiency during Start Bank 2",         "Catalyst"),
    200: ("Lambda Regulation Bank 1 Pre Cat",           "Lambda / Fueling"),
    201: ("Lambda Regulation Bank 2 Pre Cat",           "Lambda / Fueling"),
    202: ("Lambda Regulation Bank 1 Post Cat",          "Lambda / Fueling"),
    203: ("Lambda Regulation Bank 2 Post Cat",          "Lambda / Fueling"),
    204: ("Idle Control System - Idle Speed Not Plausible", "Idle Control"),
    208: ("EWS RPM Signal Error",                       "Immobiliser"),
    209: ("EWS Message Error",                          "Immobiliser"),
    210: ("Ignition Feedback Resistor (ZSR)",           "Ignition"),
    211: ("Idle Speed Actuator - Mechanical",           "Idle Control"),
    212: ("VANOS Bank 1 - Mechanical",                  "VANOS"),
    214: ("Vehicle Speed Signal (VSS)",                 "Speed"),
    215: ("Lambda Sensor Bank 1",                       "Lambda / Fueling"),
    216: ("Lambda Sensor Bank 2",                       "Lambda / Fueling"),
    217: ("CAN Bus Error - EGS Signal Not Present",     "Communication"),
    218: ("CAN Module Warning",                         "Communication"),
    219: ("CAN Module Offline",                         "Communication"),
    220: ("Lambda Voltage Range Bank 1 Sensor 1",       "Lambda / Fueling"),
    221: ("Lambda Voltage Range Bank 2 Sensor 1",       "Lambda / Fueling"),
    222: ("Low Coolant Temp / Lambda Sensor Control",   "Temperature"),
    223: ("Lambda Sensor Switching Bank 1 Sensor 2",    "Lambda / Fueling"),
    224: ("Lambda Sensor Switching Bank 2 Sensor 2",    "Lambda / Fueling"),
    225: ("Cat Efficiency Bank 1",                      "Catalyst"),
    226: ("Cat Efficiency Bank 2",                      "Catalyst"),
    227: ("Mixture Deviation Bank 1",                   "Lambda / Fueling"),
    228: ("Mixture Deviation Bank 2",                   "Lambda / Fueling"),
    229: ("Lambda Sensor Switching Bank 1",             "Lambda / Fueling"),
    230: ("Lambda Sensor Switching Bank 2",             "Lambda / Fueling"),
    231: ("Lambda Sensor Switching Bank 1 Pre Cat",     "Lambda / Fueling"),
    232: ("Lambda Sensor Switching Bank 2 Pre Cat",     "Lambda / Fueling"),
    233: ("Catalytic Converter Overall Efficiency Bank 1", "Catalyst"),
    234: ("Catalytic Converter Overall Efficiency Bank 2", "Catalyst"),
    235: ("Lambda Heater Bank 1 Post Cat",              "Lambda / Fueling"),
    236: ("Lambda Heater Bank 2 Post Cat",              "Lambda / Fueling"),
    238: ("Misfire Cyl 1",                              "Ignition"),
    239: ("Misfire Cyl 2",                              "Ignition"),
    240: ("Misfire Cyl 3",                              "Ignition"),
    241: ("Misfire Cyl 4",                              "Ignition"),
    242: ("Misfire Cyl 5",                              "Ignition"),
    243: ("Misfire Cyl 6",                              "Ignition"),
    244: ("Crankshaft Interval Timing",                 "Crank/Cam"),
    245: ("Aux Air Injection System Bank 1",            "Emissions"),
    246: ("Aux Air Injection System Bank 2",            "Emissions"),
    247: ("Aux Air Injection System - Incorrect Flow",  "Emissions"),
    248: ("Pre Cat Converter Efficiency Bank 1",        "Catalyst"),
    249: ("Pre Cat Converter Efficiency Bank 2",        "Catalyst"),
    250: ("Tank Venting Valve Function",                "Emissions"),
    251: ("Tank Ventilation Diagnosis Error",           "Emissions"),
    252: ("Tank Ventilation System Vacuum",             "Emissions"),
    253: ("Charcoal Filter Shut-off Valve Stuck Shut",  "Emissions"),
    254: ("Tank Ventilation System Large Air Leak",     "Emissions"),
    255: ("Tank Ventilation System Valve Stuck Open",   "Emissions"),
}


# ---------------------------------------------------------------------------
# DS2 DTC record and parser
# ---------------------------------------------------------------------------

_DS2_FLAG_RECORDED = 0x20
_DS2_FLAG_ACTIVE = 0x40
_DS2_FLAG_HISTORY = 0x80

# Named bits for archived views; bit 0x08 has no admitted BMW text.
SAVED_STATUS_FLAGS = (
    (0x20, "Stored after debounce"), (0x40, "Present at save"),
    (0x80, "Sporadic"), (0x10, "Emissions relevant"),
)
FAULT_QUALIFIER_FLAGS = (
    (0x01, "Short circuit to battery positive"),
    (0x02, "Short circuit to ground"), (0x04, "Open circuit"),
)

# Hash-bound BMW FUMWELTTEXTE values.  The BEST jobs apply raw * A + B.
_ENVIRONMENT = {
    0x01: ("Engine speed", "rpm", 32.0, 0.0),
    0x02: ("Air mass", "kg/h", 4.0, 0.0),
    0x03: ("Spark duration (low range)", "ms", 0.0196, 0.0),
    0x04: ("Spark duration (high range)", "ms", 2.56, 0.0),
    0x05: ("Lambda controller bank 1", "%", 0.3906, -50.0),
    0x06: ("Lambda controller bank 2", "%", 0.3906, -50.0),
    0x07: ("Oxygen-sensor voltage 1", "V", 0.0196, 0.0),
    0x08: ("Oxygen-sensor voltage 2", "V", 0.0196, 0.0),
    0x09: ("Battery voltage", "V", 0.1020, 0.0),
    0x0A: ("Throttle angle", "degrees", 0.4686, 0.0),
    0x0B: ("Idle actuator", "%", 0.3906, 0.0),
    0x0C: ("Engine load", "mg/stroke", 5.4471, 0.0),
    0x0D: ("Vehicle speed", "km/h", 1.0, 0.0),
    0x0E: ("Gear information", "raw", 1.0, 0.0),
    0x0F: ("Camshaft angle", "degrees", 32.0, 0.0),
    0x10: ("Engine operating state", "raw", 1.0, 0.0),
    0x11: ("Digital fault code 1", "raw", 1.0, 0.0),
    0x12: ("Digital fault code 2", "raw", 1.0, 0.0),
    0x13: ("Coolant-sensor voltage", "V", 0.0196, 0.0),
    0x14: ("Mean rich oxygen-sensor value", "V", 32.0, 0.0),
    0x15: ("Knock-signal gain", "ratio", 0.00392, 0.0),
    0x16: ("Noise value", "V", 0.02, 0.0),
    0x17: ("Oxygen-sensor heater", "%", 0.391, 0.0),
    0x18: ("Coolant temperature", "deg C", 0.7471, -48.0),
    0x19: ("Intake-air temperature", "deg C", 0.7471, -48.0),
    0x1A: ("Air-flow-meter voltage", "V", 0.0196, 0.0),
    0x1B: ("Tank-vent valve command", "%", 0.391, 0.0),
}

# FORTTEXTE selects the four environment bytes for each fault.  MS410DS1 and
# MS410DS3 are byte-identical here; MS411DS2 is also the inherited MS41.3 base.
_ENVIRONMENT_BY_CODE = {
    0x00: (0x00, 0x00, 0x00, 0x00),
    0x01: (0x01, 0x0C, 0x03, 0x04),
    0x02: (0x01, 0x0C, 0x03, 0x04),
    0x03: (0x01, 0x0C, 0x03, 0x04),
    0x05: (0x01, 0x0C, 0x09, 0x05),
    0x06: (0x01, 0x0C, 0x09, 0x05),
    0x08: (0x01, 0x0A, 0x0B, 0x1A),
    0x0A: (0x01, 0x0C, 0x19, 0x18),
    0x0C: (0x01, 0x0C, 0x18, 0x0A),
    0x0E: (0x01, 0x0C, 0x18, 0x19),
    0x10: (0x01, 0x0C, 0x18, 0x19),
    0x12: (0x01, 0x0C, 0x18, 0x0A),
    0x14: (0x01, 0x0C, 0x18, 0x19),
    0x15: (0x01, 0x0C, 0x18, 0x19),
    0x16: (0x01, 0x0C, 0x09, 0x05),
    0x17: (0x01, 0x0C, 0x09, 0x06),
    0x18: (0x01, 0x0C, 0x09, 0x06),
    0x19: (0x01, 0x0C, 0x07, 0x18),
    0x1B: (0x01, 0x02, 0x0A, 0x0B),
    0x1D: (0x01, 0x0C, 0x03, 0x04),
    0x1E: (0x07, 0x0C, 0x03, 0x04),
    0x1F: (0x01, 0x0C, 0x03, 0x04),
    0x21: (0x01, 0x0C, 0x09, 0x06),
    0x23: (0x01, 0x0C, 0x09, 0x06),
    0x2E: (0x01, 0x0C, 0x09, 0x06),
    0x2F: (0x01, 0x0C, 0x09, 0x06),
    0x32: (0x01, 0x0C, 0x18, 0x09),
    0x33: (0x01, 0x0C, 0x18, 0x09),
    0x34: (0x01, 0x0C, 0x18, 0x09),
    0x35: (0x01, 0x02, 0x0A, 0x0B),
    0x37: (0x01, 0x0C, 0x08, 0x18),
    0x38: (0x01, 0x0C, 0x18, 0x09),
    0x39: (0x01, 0x0C, 0x15, 0x16),
    0x3B: (0x01, 0x0C, 0x15, 0x16),
    0x3D: (0x01, 0x0C, 0x15, 0x16),
    0x3E: (0x01, 0x0C, 0x15, 0x16),
    0x41: (0x01, 0x10, 0x18, 0x09),
    0x44: (0x01, 0x0C, 0x18, 0x1B),
    0x45: (0x01, 0x0D, 0x18, 0x09),
    0x4A: (0x01, 0x0D, 0x18, 0x09),
    0x4B: (0x01, 0x0C, 0x17, 0x07),
    0x4C: (0x01, 0x0C, 0x17, 0x08),
    0x4D: (0x01, 0x0C, 0x17, 0x08),
    0x4E: (0x01, 0x0C, 0x17, 0x08),
    0x4F: (0x01, 0x0C, 0x17, 0x08),
    0x50: (0x01, 0x0C, 0x0D, 0x18),
    0x51: (0x01, 0x0C, 0x0D, 0x18),
    0x52: (0x01, 0x0C, 0x0D, 0x18),
    0x53: (0x01, 0x10, 0x18, 0x09),
    0x64: (0x13, 0x09, 0x11, 0x12),
    0xC8: (0x01, 0x14, 0x17, 0x07),
    0xC9: (0x01, 0x14, 0x17, 0x08),
    0xCA: (0x01, 0x18, 0x17, 0x07),
    0xCB: (0x01, 0x18, 0x17, 0x08),
    0xD1: (0x01, 0x0C, 0x18, 0x09),
    0xD2: (0x01, 0x0C, 0x18, 0x09),
    0xD3: (0x01, 0x0C, 0x18, 0x09),
    0xD4: (0x01, 0x0C, 0x0E, 0x09),
    0xD6: (0x01, 0x0C, 0x0E, 0x09),
    0xD7: (0x01, 0x0C, 0x0D, 0x18),
    0xD8: (0x01, 0x0C, 0x09, 0x0E),
    0xD9: (0x01, 0x0C, 0x18, 0x09),
    0xDA: (0x01, 0x0C, 0x18, 0x09),
    0xDB: (0x01, 0x0C, 0x18, 0x09),
    0xDE: (0x01, 0x0C, 0x18, 0x09),
    0xE1: (0x01, 0x0C, 0x18, 0x09),
    0xE2: (0x01, 0x0C, 0x18, 0x09),
    0xE3: (0x01, 0x0C, 0x18, 0x09),
    0xE4: (0x01, 0x0C, 0x18, 0x09),
    0xE5: (0x01, 0x0C, 0x18, 0x09),
    0xE6: (0x01, 0x0C, 0x18, 0x09),
    0xE7: (0x01, 0x0C, 0x18, 0x09),
    0xE8: (0x01, 0x0C, 0x18, 0x09),
    0xE9: (0x01, 0x0C, 0x18, 0x09),
    0xEA: (0x01, 0x0C, 0x18, 0x09),
    0xEB: (0x01, 0x0C, 0x18, 0x09),
    0xEC: (0x01, 0x0C, 0x18, 0x09),
    0xEE: (0x01, 0x0C, 0x18, 0x09),
    0xEF: (0x01, 0x0C, 0x18, 0x09),
    0xF0: (0x01, 0x0C, 0x18, 0x09),
    0xF1: (0x01, 0x0C, 0x18, 0x09),
    0xF2: (0x01, 0x0C, 0x18, 0x09),
    0xF3: (0x01, 0x0C, 0x18, 0x09),
    0xF4: (0x01, 0x0C, 0x18, 0x09),
    0xF5: (0x01, 0x0C, 0x18, 0x09),
    0xF6: (0x01, 0x0C, 0x18, 0x09),
    0xF7: (0x01, 0x0C, 0x18, 0x09),
    0xF8: (0x01, 0x0C, 0x18, 0x09),
    0xF9: (0x01, 0x0C, 0x18, 0x09),
    0xFA: (0x01, 0x0C, 0x18, 0x09),
    0xFB: (0x01, 0x0C, 0x18, 0x09),
    0xFC: (0x01, 0x0C, 0x18, 0x09),
    0xFD: (0x01, 0x0C, 0x18, 0x09),
    0xFE: (0x01, 0x0C, 0x18, 0x09),
    0xFF: (0x01, 0x0C, 0x18, 0x09),
}

_VARIANT_ENVIRONMENT_ADDITIONS = {
    "MS41.0": {0x0B: (0x01, 0x0C, 0x19, 0x18)},
    "MS41.1": {0x0B: (0x01, 0x0C, 0x19, 0x18)},
    "MS41.2": {
        code: (0x13, 0x09, 0x11, 0x12)
        for code in range(0xBE, 0xC6)
    },
    "MS41.3": {
        code: (0x13, 0x09, 0x11, 0x12)
        for code in range(0xBE, 0xC6)
    },
}


def environment_definition(identifier: int) -> tuple | None:
    """Return the canonical (label, unit, factor, offset) for an explicit source ID.

    The caller must establish which physical source was captured. This does not
    choose a per-code BMW environment row or validate an ECU's source mapping.
    """
    return _ENVIRONMENT.get(identifier)


@dataclass(frozen=True)
class DTCEnvironmentValue:
    identifier: int
    label: str
    unit: str
    raw: int
    value: float

    @property
    def value_text(self) -> str:
        return f"{self.value:.4f}".rstrip("0").rstrip(".")


@dataclass(frozen=True)
class MS41FaultMemory:
    profile: str
    stored: tuple
    shadow: tuple


@dataclass
class DS2DTCRecord:
    """One BMW MS41 stored or shadow-memory record."""
    code:       int    # 1-byte DTC number from record byte[0]
    status_raw: int    # state flags from record byte[1]
    raw_record: bytes  # full 10-byte stored or 11-byte shadow record
    memory: str = "stored"
    frequency: int | None = None
    logistics_counter: int | None = None
    qualifiers: tuple = ()
    freeze_frame: tuple = ()
    occurred_hours_ago: float | None = None
    operating_hours: float | None = None

    # --- GUI-facing properties ------------------------------------------------

    @property
    def code_hex(self) -> str:
        return f"{self.code:03d}"

    @property
    def sae_code(self) -> str:
        return f"DS2-{self.code:03d}"

    @property
    def system(self) -> str:
        entry = DS2_DTC_DB.get(self.code)
        return entry[1] if entry else "Unknown"

    @property
    def is_active(self) -> bool:
        return bool(self.status_raw & _DS2_FLAG_ACTIVE)

    @property
    def status_text(self) -> str:
        if self.is_active:
            return "Active"
        if self.status_raw & (_DS2_FLAG_RECORDED | _DS2_FLAG_HISTORY):
            return "Stored"
        return f"0x{self.status_raw:02X}"

    @property
    def description(self) -> str:
        entry = DS2_DTC_DB.get(self.code)
        return entry[0] if entry else f"Unknown fault code {self.code}"

    @property
    def self_test_reason(self) -> str | None:
        if self.code != 100 or self.memory != "stored":
            return None
        # Native command 0x04 omits internal record byte 1, placing the reason here.
        return f"0x{int.from_bytes(self.raw_record[6:8], 'little'):04X}"

    def __repr__(self):
        return f"DS2 DTC {self.code:03d}  [{self.status_text}]  {self.description}"


def _environment_ids(variant: str | None, code: int):
    if variant in (None, "common"):
        return _ENVIRONMENT_BY_CODE.get(code)
    if variant not in _VARIANT_ENVIRONMENT_ADDITIONS:
        raise ValueError(f"unsupported MS41 fault profile {variant!r}")
    return _VARIANT_ENVIRONMENT_ADDITIONS[variant].get(
        code, _ENVIRONMENT_BY_CODE.get(code))


def _qualifiers(variant: str | None, code: int, status: int) -> tuple:
    if _environment_ids(variant, code) is None:
        return ()
    values = []
    if status & 0x20:
        values.append("Stored after debounce")
    values.append("Currently present" if status & 0x40 else "Currently not present")
    values.append("Sporadic" if status & 0x80 else "Static")
    if status & 0x10:
        values.append("Emissions relevant")
    values.extend(label for mask, label in FAULT_QUALIFIER_FLAGS if status & mask)
    return tuple(values)


def _freeze_frame(variant: str | None, code: int, raw_values: bytes) -> tuple:
    identifiers = _environment_ids(variant, code)
    if identifiers is None:
        return ()
    decoded = []
    for identifier, raw in zip(identifiers, raw_values):
        definition = environment_definition(identifier)
        if definition is None:
            continue
        label, unit, factor, offset = definition
        decoded.append(DTCEnvironmentValue(
            identifier, label, unit, raw, raw * factor + offset))
    return tuple(decoded)


def _record(raw: bytes, variant: str | None, memory: str,
            current_counter: int | None = None) -> DS2DTCRecord:
    occurred = None
    operating = None
    if memory == "stored" and current_counter is not None:
        occurred = (current_counter - int.from_bytes(raw[8:10], "big")) * 0.1
    elif memory == "shadow":
        operating = float(raw[9])
    return DS2DTCRecord(
        code=raw[0],
        status_raw=raw[1],
        raw_record=raw,
        memory=memory,
        frequency=raw[2],
        logistics_counter=raw[3],
        qualifiers=_qualifiers(variant, raw[0], raw[1]),
        freeze_frame=_freeze_frame(variant, raw[0], raw[4:8]),
        occurred_hours_ago=occurred,
        operating_hours=operating,
    )


def parse_ds2_dtc_response(payload: bytes, *, variant: str | None = None,
                           current_counter: int | None = None) -> list:
    """Parse a DS2 command-0x04 response payload into a list of DS2DTCRecord.

    Format verified against captured DS2 traffic:
      payload = count byte + count × 10-byte records
      Per record: byte[0] = DTC code, byte[1] = state flags
      Records with code == 0 are silently skipped (empty slots).

    Returns unique DTC codes sorted by code number.
    """
    data = bytes(payload)
    if data == b"\x00\x00":
        return []  # Stock MS41 empty response includes a zero pad byte.
    if not data:
        raise ValueError("empty DTC payload")
    count = data[0]
    expected = 1 + count * 10
    if len(data) != expected:
        raise ValueError(
            f"unexpected DTC payload length: {len(data)}, expected {expected}")

    records = []
    seen = set()
    for i in range(1, expected, 10):
        rec = data[i:i + 10]
        code = rec[0]
        status_raw = rec[1]
        if code != 0x00 and code not in seen:
            seen.add(code)
            records.append(
                _record(rec, variant, "stored", current_counter)
                if variant else DS2DTCRecord(code, status_raw, rec)
            )
    records.sort(key=lambda r: r.code)
    return records


def parse_ds2_shadow_response(payload: bytes, *, variant: str | None = None) -> list:
    """Decode the 11-byte records returned by BMW FS_SHADOW_LESEN."""
    data = bytes(payload)
    if data in (b"\x00", b"\x00\x00"):
        return []
    if not data:
        raise ValueError("empty shadow DTC payload")
    count = data[0]
    expected = 1 + count * 11
    if len(data) != expected:
        raise ValueError(
            f"unexpected shadow DTC payload length: {len(data)}, expected {expected}")
    records = []
    seen = set()
    for index in range(count):
        raw = data[1 + index * 11:12 + index * 11]
        if raw[0] and raw[0] not in seen:
            seen.add(raw[0])
            records.append(_record(raw, variant, "shadow"))
    return sorted(records, key=lambda record: record.code)


def read_ms41_fault_memory(ds2, variant: str | None = None) -> MS41FaultMemory:
    """Run BMW FS_LESEN and FS_SHADOW_LESEN through an open DS2 owner."""
    from ds2 import DS2Error, DS2NegativeResponse

    header = None
    for _attempt in range(32):
        try:
            header = ds2.read_dtc(0)
            break
        except DS2NegativeResponse as error:
            # Exact FS_LESEN behavior: only 0x04/0x00 A1 means preparation busy.
            if error.command != 0x04 or error.status != 0xA1:
                raise
    if header is None:
        raise DS2Error("DTC preparation remained busy")
    if len(header) < 5:
        raise ValueError("DTC preparation response is too short")
    current_counter = int.from_bytes(header[3:5], "big")
    profile = variant or "common"
    stored = parse_ds2_dtc_response(
        ds2.read_dtc(1), variant=profile, current_counter=current_counter)
    shadow = parse_ds2_shadow_response(ds2.read_shadow_dtc(), variant=profile)
    return MS41FaultMemory(profile, tuple(stored), tuple(shadow))
