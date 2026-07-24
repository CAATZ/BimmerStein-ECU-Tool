"""
live_data.py — MS41 live data parameter definitions and polling engine.

Standard mode: DS2 command 0x06 range reads from RAM.
Telegram mode: MS41 DS2 command 0x0B/0x01 — one registered-address response
  containing every displayed RAM parameter.

Standard mode note:
  Byte layouts, offsets, and scaling factors are derived from BMW INPA
  MS41.PRG source analysis and community reverse-engineering.

Telegram mode note:
  Addresses are 24-bit C166 logical addresses for the Siemens 80C166
  processor (little-endian).  Source: community-reverse-engineered ECU
  definition XML (ba114/Siemens-MS41, v0.46.0, covers MS41.0–MS41.3).
  Some parameters have different addresses across software revisions. This
  module selects the table for the detected ECU ID when one is available.

  Parameters without a mapped RAM address show "—". Standard and telegram
  acquisition use the same selected address/scaling table; standard mode is a
  transport fallback, not an independent validation source.
"""

import threading
import time
import csv
import os
import datetime
from dataclasses import dataclass, field
from typing import Optional, Dict, Tuple, List


# ──────────────────────────── Standard-mode parameters ────────────────────────

@dataclass
class MS41Parameter:
    """Legacy display-schema entry; acquisition now uses DS2 RAM addresses below."""
    name:   str
    unit:   str
    lid:    int      # SID 0x21 local identifier
    offset: int      # byte index within data payload (after 0x61 + LID echo)
    length: int      # 1 or 2 bytes
    scale:  float    # multiply raw value by this
    bias:   float    # add after scaling
    fmt:    str      # Python format string, e.g. "{:.1f}"
    signed: bool = False

    def parse(self, resp: bytes) -> Optional[float]:
        """Extract and scale this parameter from a full SID 0x21 response."""
        start = 2 + self.offset   # skip positive SID (0x61) + LID echo
        if start + self.length > len(resp):
            return None
        if self.length == 2:
            raw = (resp[start] << 8) | resp[start + 1]
            if self.signed and raw > 0x7FFF:
                raw -= 0x10000
        else:
            raw = resp[start]
            if self.signed and raw > 127:
                raw -= 256
        return raw * self.scale + self.bias

    def display(self, value: Optional[float]) -> str:
        return "—" if value is None else self.fmt.format(value)


# Source: BMW INPA MS41.PRG analysis, NCS Expert traces, community docs.
# Scaling is approximate — verify against your INPA version for critical use.

MS41_PARAMETERS: List[MS41Parameter] = [
    # LID 0x01 — Basic engine values
    MS41Parameter("Engine RPM",         "RPM",  0x01, 0, 2, 0.25,       0.0,   "{:.0f}"),
    MS41Parameter("Coolant Temp",       "°C",   0x01, 2, 1, 1.0,       -48.0,  "{:.0f}"),
    MS41Parameter("Throttle Position",  "%",    0x01, 3, 1, 0.390625,   0.0,   "{:.1f}"),
    MS41Parameter("Intake Air Temp",    "°C",   0x01, 4, 1, 1.0,       -48.0,  "{:.0f}"),
    MS41Parameter("Battery Voltage",    "V",    0x01, 5, 1, 0.1,        0.0,   "{:.1f}"),

    # LID 0x02 — Air / fuel
    MS41Parameter("Mass Air Flow",      "kg/h", 0x02, 0, 2, 0.01,       0.0,   "{:.2f}"),
    MS41Parameter("Lambda Upstream",    "V",    0x02, 2, 2, 0.004883,   0.0,   "{:.3f}"),
    MS41Parameter("Fuel Trim ST",       "%",    0x02, 4, 1, 0.390625, -50.0,   "{:.1f}"),
    MS41Parameter("Fuel Trim LT",       "%",    0x02, 5, 1, 0.390625, -50.0,   "{:.1f}"),

    # LID 0x03 — Ignition / VANOS
    MS41Parameter("Ignition Advance",   "°",    0x03, 0, 1, 0.75,      -48.0,  "{:.1f}"),
    MS41Parameter("Knock Retard",       "°",    0x03, 1, 1, 0.75,        0.0,  "{:.1f}"),
    MS41Parameter("VANOS Advance",      "°",    0x03, 2, 1, 0.75,        0.0,  "{:.1f}"),

    # LID 0x04 — Speed / injectors / idle
    MS41Parameter("Vehicle Speed",      "km/h", 0x04, 0, 1, 1.0,        0.0,  "{:.0f}"),
    MS41Parameter("Injector PW",        "ms",   0x04, 1, 2, 0.01,       0.0,  "{:.2f}"),
    MS41Parameter("Idle Valve Pos",     "%",    0x04, 3, 1, 0.390625,   0.0,  "{:.1f}"),
]


# ──────────────────────────── Telegram-mode parameters ────────────────────────
#
# RAM addresses and conversions taken from the RomRaider MS41 Logger
# Definitions (<ecuparam> entries) for ECU ID 1437806 (MS41.1, E36/E39/Z3 — the
# 256 KB DME on M52/M50-swap cars). Read directly from RAM through MS41 DS2
# commands 0x06 or 0x0B. The 80C166 is little-endian — 16-bit values are LE.
#
# storagetype int16 -> signed read; uint16 -> unsigned read.  Some uint16
# parameters (fuel trims) are centred at 32768, handled by the convert function.
#
# Shared addresses apply across MS41.0-.3; the family maps below resolve the
# working-RAM addresses that drift between software versions. Names are aligned
# with the standard-mode table so both transports reuse the same display rows.

@dataclass
class TelegramParameter:
    """A live value read from ECU RAM using a RomRaider logger definition address."""
    name:    str
    unit:    str
    address: int                 # C166 RAM address
    length:  int                 # 1 or 2 bytes
    signed:  bool                # True for int16 storage
    convert: callable            # raw int -> physical value
    fmt:     str

    def parse(self, block: bytes, block_start: int):
        offset = self.address - block_start
        if offset < 0 or offset + self.length > len(block):
            return None
        if self.length == 2:
            raw = block[offset] | (block[offset + 1] << 8)   # little-endian
            if self.signed and raw > 0x7FFF:
                raw -= 0x10000
        else:
            raw = block[offset]
            if self.signed and raw > 127:
                raw -= 256
        return self.convert(raw)

    def display(self, value) -> str:
        return "—" if value is None else self.fmt.format(value)


# Parameter metadata, independent of ECU ID:  name -> (unit, length, signed, convert, fmt)
_FT = lambda x: (x - 32768) * 100 / 65535

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


def _state(active) -> str:
    return "Active" if bool(active) else "Inactive"


def _adc_voltage(raw: int) -> float:
    return (raw & 0x03FF) * 5 / 1023


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
]
PROFILE_DISPLAY_NAMES = frozenset(name for name, _unit in _PROFILE_DISPLAY_ROWS)
_PROFILE_STATUS_NAMES = frozenset({
    "Wideband Mode", "Wideband Input Source", "Narrowband Emulation",
})

_PARAM_META = {
    "Engine RPM":            ("RPM",  2, False, lambda x: x,                "{:.0f}"),
    "Mass Air Flow":         ("kg/h", 2, False, lambda x: x * 0.25,         "{:.1f}"),
    "Idle Valve Pos":        ("%",    2, True,  lambda x: x * 0.00153,      "{:.1f}"),
    "Intake Air Temp":       ("°C",   1, False, lambda x: x * 0.747 - 48,   "{:.0f}"),
    "Coolant Temp":          ("°C",   1, False, lambda x: x * 0.747 - 48,   "{:.0f}"),
    "Vehicle Speed":         ("km/h", 1, False, lambda x: x,                "{:.0f}"),
    "Throttle Position":     ("%",    1, False, lambda x: x * 100 / 255,    "{:.1f}"),
    "Ignition Advance":      ("°",    1, False, lambda x: 0.373 * x - 23.6, "{:.1f}"),
    "Knock Retard":          ("°",    1, False, lambda x: (x - 128) * 0.375,"{:.1f}"),
    "Knock Retard (Global)": ("°",    1, False, lambda x: (x - 128) * 0.375,"{:.1f}"),
    "VANOS Advance":         ("°",    1, False, lambda x: x * 0.3745,       "{:.1f}"),
    "Injector PW":           ("ms",   2, False, lambda x: x * 0.00534,      "{:.2f}"),
    "Fuel Trim ST":          ("%",    2, False, _FT, "{:.1f}"),
    "Fuel Trim ST B2":       ("%",    2, False, _FT, "{:.1f}"),
    "Fuel Trim LT":          ("%",    2, False, _FT, "{:.1f}"),
    "Fuel Trim LT B2":       ("%",    2, False, _FT, "{:.1f}"),
    "Engine Load":           ("mg/st",2, False, lambda x: x * 0.021195,     "{:.1f}"),
    # V_IGK: supply voltage monitor, 1 byte, scale 0.10196 V/count (confirmed live: 122 → 12.44 V)
    "Battery Voltage":       ("V",    1, False, lambda x: x * 0.10196,      "{:.2f}"),
}

# Display order for telegram params (std-aligned names first, then extras).
_TELEGRAM_ORDER = [
    "Engine RPM", "Mass Air Flow", "Idle Valve Pos", "Intake Air Temp", "Coolant Temp",
    "Vehicle Speed", "Throttle Position", "Ignition Advance", "Knock Retard",
    "Knock Retard (Global)", "VANOS Advance", "Injector PW",
    "Fuel Trim ST", "Fuel Trim ST B2", "Fuel Trim LT", "Fuel Trim LT B2", "Engine Load",
    "Battery Voltage",
]

# Addresses shared by ALL ECU IDs (RomRaider MS41 logger defs).
_SHARED_ADDR = {
    "Engine RPM": 0xDA2A, "Mass Air Flow": 0xDA34, "Idle Valve Pos": 0xDA36,
    "Intake Air Temp": 0xDA50, "Coolant Temp": 0xDA5A, "Vehicle Speed": 0xDA63,
    "Ignition Advance": 0xE989, "Knock Retard": 0xE98D, "Knock Retard (Global)": 0xE9D9,
    "VANOS Advance": 0xE9E6,
    "Battery Voltage": 0xFC9D,   # V_IGK, shared across all MS41 variants
}

# Per-ECU-ID address families for the parameters that differ (fuel + TPS).
_FAMILY_ADDR = {
    "1437806": {"Throttle Position": 0xE8D7, "Engine Load": 0xFC52, "Injector PW": 0xEF96,
                "Fuel Trim ST": 0xF036, "Fuel Trim ST B2": 0xF0F2,
                "Fuel Trim Additive": 0xF040, "Fuel Trim Additive B2": 0xF0FC,
                "Fuel Trim LT": 0xF048, "Fuel Trim LT B2": 0xF104},
    "1429861": {"Throttle Position": 0xE8D7, "Engine Load": 0xFAFC, "Injector PW": 0xECBC,
                "Fuel Trim ST": 0xED5C, "Fuel Trim ST B2": 0xED96,
                "Fuel Trim Additive": 0xED66, "Fuel Trim Additive B2": 0xEDA0,
                "Fuel Trim LT": 0xED6E, "Fuel Trim LT B2": 0xEDA8},
    "1406464": {"Throttle Position": 0xE8D0, "Engine Load": 0xFC52, "Injector PW": 0xEF7E,
                "Fuel Trim ST": 0xF01E, "Fuel Trim ST B2": 0xF0CA,
                "Fuel Trim Additive": 0xF028, "Fuel Trim Additive B2": 0xF0D4,
                "Fuel Trim LT": 0xF030, "Fuel Trim LT B2": 0xF0DC},
}

# Map a connected ECU ID -> address family (for the varying params).
_ECU_FAMILY = {
    "1437806": "1437806",   # MS41.1 E36/E39/Z3 M52
    "1438068": "1437806",   # MS41.1 (alternate part number, same addresses)
    "1429861": "1429861",   # MS41.0
    "1432401": "1429861",   # MS41.0 (alternate part number)
    "1429373": "1429861",   # MS41.0 (alternate part number)
    "1438137": "1429861",   # MS41.0 (alternate part number)
    "1406464": "1406464",   # MS41.2 E36 M3 S52
    "SHINDE1": "1406464",   # MS41.3 bench build (shares MS41.2 RAM layout)
}
# ECU IDs with only the shared parameter set mapped (use default TPS, no fuel addrs).
_DEFAULT_TPS = 0xE8D7

# Definition-derived axis locations converted to live DS2 CPU addresses.
# Only variants with proven Knock Tables X/Y definitions are listed.
_ADAPTATION_AXES = {
    "1437806": (0x12606, 0x125D9),
    "1438068": (0x12606, 0x125D9),
    "1429861": (0x1239A, 0x1236D),
    "1432401": (0x1239A, 0x1236D),
    "1406464": (0x12388, 0x1235B),
    "SHINDE1": (0x12388, 0x1235B),
}
_KNOCK_ADAPTATION_ADDR = 0xD840


def _profile_telegram_params(profile: str, wideband_input_addr: int):
    common = [
        TelegramParameter("Closed Throttle", "", 0xFD24, 1, False,
                          lambda x: _state((x & 0x01) == 0), "{}"),
        TelegramParameter("Full Load", "", 0xFD24, 1, False,
                          lambda x: _state(x & 0x02), "{}"),
        TelegramParameter("Part Load", "", 0xFD14, 1, False,
                          lambda x: _state(x & 0x08), "{}"),
        TelegramParameter("Decel Fuel Cut", "", 0xFD14, 1, False,
                          lambda x: _state(x & 0x20), "{}"),
        TelegramParameter("Engine Start", "", 0xFD14, 1, False,
                          lambda x: _state(x & 0x02), "{}"),
    ]
    if profile == PROFILE_WIDEBAND:
        return common + [
            TelegramParameter("Wideband Input Voltage", "V", wideband_input_addr,
                              2, False, _adc_voltage, "{:.3f}"),
            TelegramParameter("MAF Sensor Voltage", "V", 0xFA9E,
                              2, False, _adc_voltage, "{:.3f}"),
            TelegramParameter("Wideband AFR", "AFR", 0xE800, 1, False,
                              lambda x: x * 0.05 + 8.25, "{:.2f}"),
            TelegramParameter("AFR Target", "AFR", 0xE811, 1, False,
                              lambda x: x * 0.05 + 8.25, "{:.2f}"),
        ]
    return common + [
        TelegramParameter("EVAP Purge Duty", "%", 0xDA56, 1, False,
                          lambda x: x * 100 / 255, "{:.1f}"),
        TelegramParameter("Front O2 B1 Voltage", "V", 0xFA9A,
                          2, False, _adc_voltage, "{:.3f}"),
        TelegramParameter("Front O2 B2 Voltage", "V", 0xFA98,
                          2, False, _adc_voltage, "{:.3f}"),
        TelegramParameter("MAF Sensor Voltage", "V", 0xFA9E,
                          2, False, _adc_voltage, "{:.3f}"),
    ]


def telegram_params_for(ecu_id, profile: str = PROFILE_STANDARD,
                        wideband_input_addr: int = _DEFAULT_WBO2_INPUT_ADDR
                        ) -> List["TelegramParameter"]:
    """
    Build the telegram parameter list for a given ECU ID.  Shared params apply to
    all IDs; fuel/TPS params use the ID's address family.  Parameters whose
    address is unknown for the ID are omitted (never shown with a wrong address).
    """
    fam = _ECU_FAMILY.get(str(ecu_id) if ecu_id is not None else None)
    fam_map = _FAMILY_ADDR.get(fam, {})
    params = []
    for name in _TELEGRAM_ORDER:
        unit, length, signed, convert, fmt = _PARAM_META[name]
        if name in _SHARED_ADDR:
            addr = _SHARED_ADDR[name]
        elif name == "Throttle Position":
            addr = fam_map.get(name, _DEFAULT_TPS)   # TPS known for all (default 0xE8D7)
        elif name in fam_map:
            addr = fam_map[name]
        else:
            continue                                  # varying param, unknown for this ID
        params.append(TelegramParameter(name, unit, addr, length, signed, convert, fmt))
    params.extend(_profile_telegram_params(profile, wideband_input_addr))
    return params


def read_adaptations(ds2, ecu_id):
    """Read stored fuel, throttle, and six 16x4 knock-adaptation tables."""
    ecu_id = str(ecu_id or "")
    family = _ECU_FAMILY.get(ecu_id)
    axes = _ADAPTATION_AXES.get(ecu_id)
    if family is None or axes is None:
        raise ValueError(
            f"adaptation-table addresses are not mapped for ECU ID {ecu_id or 'unknown'}")

    def read_exact(address, length):
        data = bytes(ds2.read_mem(address, length))
        if len(data) != length:
            raise ValueError(
                f"short adaptation read at 0x{address:05X}: {len(data)}/{length} bytes")
        return data

    addresses = _FAMILY_ADDR[family]
    additive, ltft = [], []
    for suffix in ("", " B2"):
        add_address = addresses[f"Fuel Trim Additive{suffix}"]
        lt_address = addresses[f"Fuel Trim LT{suffix}"]
        block = read_exact(add_address, lt_address - add_address + 2)
        add_raw = int.from_bytes(block[:2], "little")
        lt_raw = int.from_bytes(block[lt_address - add_address:lt_address - add_address + 2],
                                "little")
        additive.append((add_raw - 32768) * 0.00534)
        ltft.append((lt_raw - 32768) * 100 / 65535)

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


# Default param set (ECU ID 1437806 / MS41.1) for display layout, names, and tests.
_TELEGRAM_PARAMS: List[TelegramParameter] = telegram_params_for("1437806")

# All parameter names supplied by either DS2 live-data profile.
TELEGRAM_PARAM_NAMES: frozenset = (
    frozenset(_TELEGRAM_ORDER) | PROFILE_DISPLAY_NAMES
)

# Telegram-only parameters (not in the standard SID 0x21 table) — appended as
# extra rows in the live display so they are visible in telegram mode.
TELEGRAM_EXTRA_PARAMS = [
    (p.name, p.unit) for p in _TELEGRAM_PARAMS
    if (p.name not in {sp.name for sp in MS41_PARAMETERS}
        and p.name not in PROFILE_DISPLAY_NAMES)
]


def display_rows() -> List[Tuple[str, str]]:
    """Ordered (name, unit) rows for the live table: standard params then telegram extras."""
    rows = [(p.name, p.unit) for p in MS41_PARAMETERS]
    rows += TELEGRAM_EXTRA_PARAMS
    existing = {name for name, _unit in rows}
    rows += [row for row in _PROFILE_DISPLAY_ROWS if row[0] not in existing]
    return rows


# ─────────────────── DS2 0x0B/0x01 batch parameter layout ───────────────────
#
# Ordered list matching the setup frame entries (see ds2.py _BATCH_SETUP_ARGS).
# Each tuple: (display_name_or_None, addr, n_response_bytes, signed, convert, unit, fmt)
#   display_name=None marks a shared state byte decoded through _BATCH_STATE_VALUES.
#
# Response parsing:
#   data[0:2]   = group 1 status (skipped)
#   data[2:28]  = 20 param values (group 1), sizes per n_response_bytes
#   data[28:30] = group 2 status (skipped)
#   data[30:38] = 4 param values (group 2)
#
# Conversions use the same formulas as the direct RAM parameters (_PARAM_META)
# so readings are directly comparable between modes.

_FT_BATCH = lambda x: (x - 32768) * 100 / 65535   # same as _FT above

# Addresses shown here are for ECU 1437806 (MS41.1) — the default.
# batch_layout_for() substitutes ECU-family and profile-specific addresses before
# the same positional plan is passed to both the transport and response parser.
#
# ST entries (1b) read the high byte of the standard-mode LE 16b value
# (addr = standard_ST_addr + 1).  Formula (x-128)*100/256 gives ±50% range,
# matching the 2b _FT formula used by standard/telegram mode.
_FT_ST_BATCH = lambda x: (x - 128) * 100 / 256   # 1b high-byte of LE 16b ST trim

DS2_BATCH_LAYOUT = [
    # name,                     addr,   bytes, signed, convert,                         unit,   fmt
    # ── group 1 (20 entries, data[2:28]) ─────────────────────────────────────
    ("Idle Valve Pos",       0xDA36, 2, True,  lambda x: x*0.00153,            "%",     "{:.1f}"),
    ("Injector PW",          0xEF96, 2, False, lambda x: x*0.00534,            "ms",    "{:.2f}"),
    ("Ignition Advance",     0xE989, 1, False, lambda x: 0.373*x - 23.6,      "°",     "{:.1f}"),
    ("Knock Retard",         0xE98D, 1, False, lambda x: (x-128)*0.375,        "°",     "{:.1f}"),
    ("Vehicle Speed",        0xDA63, 1, False, lambda x: x,                    "km/h",  "{:.0f}"),
    ("Throttle Position",    0xE8D7, 1, False, lambda x: x*100/255,            "%",     "{:.1f}"),
    ("Engine RPM",           0xDA2A, 2, False, lambda x: x,                    "RPM",   "{:.0f}"),
    ("Mass Air Flow",        0xDA34, 2, False, lambda x: x*0.25,               "kg/h",  "{:.1f}"),
    ("Coolant Temp",         0xDA5A, 1, False, lambda x: x*0.747 - 48,         "°C",    "{:.0f}"),
    ("Intake Air Temp",      0xDA50, 1, False, lambda x: x*0.747 - 48,         "°C",    "{:.0f}"),
    ("Battery Voltage",      0xFC9D, 1, False, lambda x: x * 0.10196,          "V",     "{:.2f}"),
    ("Knock Retard (Global)",0xE9D9, 1, False, lambda x: (x-128)*0.375,        "°",     "{:.1f}"),
    ("VANOS Advance",        0xE9E6, 1, False, lambda x: x*0.3745,             "°",     "{:.1f}"),
    ("EVAP Purge Duty",     0xDA56, 1, False, lambda x: x*100/255,             "%",     "{:.1f}"),
    ("Fuel Trim LT",         0xF048, 2, False, _FT_BATCH,                      "%",     "{:.1f}"),
    ("Fuel Trim LT B2",      0xF104, 2, False, _FT_BATCH,                      "%",     "{:.1f}"),
    ("Fuel Trim ST",         0xF037, 1, False, _FT_ST_BATCH,                   "%",     "{:.1f}"),
    ("Fuel Trim ST B2",      0xF0F3, 1, False, _FT_ST_BATCH,                   "%",     "{:.1f}"),
    (None,           0xFD24, 1, False, None,                                   "",      ""),
    (None,           0xFD14, 1, False, None,                                   "",      ""),
    # ── group 2 (4 entries, data[30:38]) ─────────────────────────────────────
    ("Engine Load",          0xFC52, 2, False, lambda x: x*0.021195,           "mg/st", "{:.1f}"),
    ("Front O2 B1 Voltage", 0xFA9A, 2, False, _adc_voltage,                   "V",     "{:.3f}"),
    ("Front O2 B2 Voltage", 0xFA98, 2, False, _adc_voltage,                   "V",     "{:.3f}"),
    ("MAF Sensor Voltage",  0xFA9E, 2, False, _adc_voltage,                   "V",     "{:.3f}"),
]

_BATCH_STATE_VALUES = {
    18: (
        ("Closed Throttle", lambda x: _state((x & 0x01) == 0), "", "{}"),
        ("Full Load", lambda x: _state(x & 0x02), "", "{}"),
    ),
    19: (
        ("Part Load", lambda x: _state(x & 0x08), "", "{}"),
        ("Decel Fuel Cut", lambda x: _state(x & 0x20), "", "{}"),
        ("Engine Start", lambda x: _state(x & 0x02), "", "{}"),
    ),
}


def batch_layout_for(ecu_id, profile: str = PROFILE_STANDARD,
                     wideband_input_addr: int = _DEFAULT_WBO2_INPUT_ADDR):
    """Build the positional decoder for the ECU family and selected profile."""
    fam = _ECU_FAMILY.get(str(ecu_id) if ecu_id is not None else None, "1437806")
    addrs = _FAMILY_ADDR[fam]
    layout = list(DS2_BATCH_LAYOUT)
    layout[1] = (layout[1][0], addrs["Injector PW"], *layout[1][2:])
    layout[5] = (layout[5][0], addrs["Throttle Position"], *layout[5][2:])
    layout[14] = (layout[14][0], addrs["Fuel Trim LT"], *layout[14][2:])
    layout[15] = (layout[15][0], addrs["Fuel Trim LT B2"], *layout[15][2:])
    layout[16] = (layout[16][0], addrs["Fuel Trim ST"] + 1, *layout[16][2:])
    layout[17] = (layout[17][0], addrs["Fuel Trim ST B2"] + 1, *layout[17][2:])
    layout[20] = (layout[20][0], addrs["Engine Load"], *layout[20][2:])

    if profile == PROFILE_WIDEBAND:
        layout[13] = ("Wideband AFR", 0xE800, 1, False,
                      lambda x: x * 0.05 + 8.25, "AFR", "{:.2f}")
        layout[21] = ("Wideband Input Voltage", wideband_input_addr, 2, False,
                      _adc_voltage, "V", "{:.3f}")
        layout[22] = ("MAF Sensor Voltage", 0xFA9E, 2, False,
                      _adc_voltage, "V", "{:.3f}")
        # E810/E811 is one native little-endian word. Batch mode presents it
        # MSB-first, so the high byte of the parsed word is E811 (AFR target).
        layout[23] = ("AFR Target", 0xE810, 2, False,
                      lambda x: (x >> 8) * 0.05 + 8.25, "AFR", "{:.2f}")
    return tuple(layout)


def batch_wire_entries(layout):
    """Return the transport-only ``(address, byte_length)`` batch plan."""
    return tuple((entry[1], entry[2]) for entry in layout)

# Byte offsets in the 38-byte payload where each param value starts.
# Groups separated by 2-byte status headers at data[0:2] and data[28:30].
_G1_START = 2     # group 1 data begins after 2-byte group 1 status
_G2_START = 30    # group 2 data begins after group 1 data + 2-byte group 2 status


def _parse_ds2_batch(raw: bytes, latest: dict, lock, csv_row: dict,
                     layout=None) -> None:
    """Parse the 38-byte DS2 batch poll response into latest values."""
    if len(raw) < 38:
        return
    layout = layout or DS2_BATCH_LAYOUT

    # Build offset table: skip 2-byte group headers at boundaries
    offsets = []
    off = _G1_START
    for i, (name, addr, nbytes, signed, convert, unit, fmt) in enumerate(layout):
        if i == 20:           # group 2 starts at data[30]
            off = _G2_START
        offsets.append(off)
        off += nbytes

    with lock:
        for i, (name, addr, nbytes, signed, convert, unit, fmt) in enumerate(layout):
            start = offsets[i]
            end   = start + nbytes
            if end > len(raw):
                continue
            chunk = raw[start:end]
            # The 0x0B/0x01 telegram response serializes multi-byte values
            # BIG-endian (MSB first) — unlike a raw read_mem(), which returns
            # native little-endian RAM bytes. Parsing big-endian here makes the
            # batch (telegram) values match standard (cmd 0x06) mode. (Confirmed
            # on hardware: little-endian here gives wrong Fuel Trim LT/LT B2.)
            val   = int.from_bytes(chunk, "big")
            if signed and val >= (1 << (8 * nbytes - 1)):
                val -= 1 << (8 * nbytes)
            if name is not None and convert is not None:
                physical = convert(val)
                disp = fmt.format(physical)
                latest[name] = (disp, unit)
                csv_row[name] = disp
            for state_name, state_convert, state_unit, state_fmt in _BATCH_STATE_VALUES.get(i, ()):
                state_value = state_convert(val)
                state_disp = state_fmt.format(state_value)
                latest[state_name] = (state_disp, state_unit)
                csv_row[state_name] = state_disp


@dataclass
class _TelBlock:
    """A contiguous memory range to be read in one DS2 command-0x06 call."""
    start:  int
    size:   int
    params: List[TelegramParameter] = field(default_factory=list)


def _build_telegram_blocks(params, max_span=120) -> List[_TelBlock]:
    """Greedily group params into block reads whose span stays within max_span bytes."""
    blocks = []
    for p in sorted(params, key=lambda q: q.address):
        end = p.address + p.length
        if blocks and end - blocks[-1].start <= max_span:
            b = blocks[-1]
            b.size = max(b.size, end - b.start)
            b.params.append(p)
        else:
            blocks.append(_TelBlock(start=p.address, size=p.length, params=[p]))
    return blocks

_TELEGRAM_BLOCKS: List[_TelBlock] = _build_telegram_blocks(_TELEGRAM_PARAMS)


# ──────────────────────────── Poller ─────────────────────────────────────────

class LiveDataPoller:
    """
    Polls MS41 live data on a background thread.

    Standard mode (use_telegram=False):
      Reads the RomRaider RAM-address set via DS2 command 0x06, grouped into
      contiguous ranges (multiple round trips per sample).

    Telegram mode (use_telegram=True):
      Registers the RomRaider MS41 RAM-address set via DS2 0x0B/0x01 and then
      retrieves the complete sample with one 0x0B/0x00 response.

    Stores latest values in a thread-safe dict; the GUI reads via a QTimer.
    Optionally writes every poll cycle to a CSV file.
    """

    def __init__(self, interval: float = 0.5, use_telegram: bool = False,
                 ecu_id=None, ecu_variant=None, ds2=None):
        self._ds2          = ds2          # DS2Interface — the live ECU connection
        self._interval     = interval
        self._use_telegram = use_telegram
        self._ecu_id       = ecu_id
        self._ecu_variant  = ecu_variant
        self._profile      = PROFILE_STANDARD
        self._profile_ready = False
        self._wideband_input_addr = _DEFAULT_WBO2_INPUT_ADDR
        # Resolve telegram params/blocks for this ECU ID (default 1437806).
        self._tel_params   = telegram_params_for(ecu_id if ecu_id else "1437806")
        self._tel_blocks   = _build_telegram_blocks(self._tel_params)
        self._batch_layout = batch_layout_for(ecu_id if ecu_id else "1437806")
        self._active_profile_names = set()
        self._stop         = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock         = threading.Lock()
        self._latest:  Dict[str, Tuple[str, str]] = {}  # name → (value_str, unit)
        self._errors:  List[str] = []
        self._csv_file   = None
        self._csv_writer = None
        self._log_start  = 0.0   # time.time() at CSV open — used for elapsed-seconds column
        self._csv_rows   = 0     # rows written so far (GUI reads this for status display)
        self._csv_last_flush = 0.0
        self._samples = 0
        self._sample_started = 0.0

    # ── Public API ─────────────────────────────────────────────────────────

    def start(self, log_path: Optional[str] = None):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        with self._lock:
            self._latest.clear()
            self._errors.clear()
        self._samples = 0
        self._sample_started = time.monotonic()
        if log_path:
            self._open_csv(log_path)
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=4)
            self._thread = None
        self._close_csv()

    def latest_values(self) -> Dict[str, Tuple[str, str]]:
        """Return a snapshot of the latest parameter values (thread-safe)."""
        with self._lock:
            return dict(self._latest)

    def pop_errors(self) -> List[str]:
        with self._lock:
            errs, self._errors = list(self._errors), []
        return errs

    @property
    def active_profile_names(self):
        """Optional live-table rows used by the detected profile."""
        return frozenset(self._active_profile_names)

    # ── Internal ────────────────────────────────────────────────────────────

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

        key = self._ecu_id if self._ecu_id else "1437806"
        self._profile = profile
        self._wideband_input_addr = input_addr
        self._tel_params = telegram_params_for(key, profile, input_addr)
        self._tel_blocks = _build_telegram_blocks(self._tel_params)
        self._batch_layout = batch_layout_for(key, profile, input_addr)
        active_names = {
            p.name for p in self._tel_params if p.name in PROFILE_DISPLAY_NAMES
        }
        active_names.update(status_values)
        self._active_profile_names = active_names
        with self._lock:
            self._latest.update(status_values)
        self._profile_ready = True

    def _poll_loop(self):
        # Live data is read over DS2: batch telegram (0x0B/0x01) or individual
        # block reads (0x06).  (MS41 has no working KWP2000 path.)
        if self._use_telegram:
            self._poll_loop_ds2_batch()
        else:
            self._poll_loop_ds2_reads()

    def _poll_loop_ds2_reads(self):
        """DS2 standard mode: individual block reads via DS2 cmd 0x06 (READ_MEM).

        Groups nearby RAM addresses into contiguous blocks and issues one read_mem
        per block.  More round-trips than the batch telegram, but simpler and more
        reliable.  It uses the same addresses and scaling as batch mode.
        """
        self._prepare_live_profile()
        produced = {p.name for p in self._tel_params}
        with self._lock:
            for p in MS41_PARAMETERS:
                if p.name not in produced:
                    self._latest[p.name] = ("—", p.unit)

        while not self._stop.is_set():
            cycle_started = time.monotonic()
            csv_row = self._csv_row_base()
            interrupted = False
            for block in self._tel_blocks:
                if self._stop.is_set():
                    interrupted = True
                    break
                try:
                    data = self._ds2.read_mem(block.start, block.size)
                    with self._lock:
                        for tp in block.params:
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
            self._write_csv_row(csv_row)
            self._samples += 1
            self._stop.wait(max(0.0, self._interval - (time.monotonic() - cycle_started)))

    def _poll_loop_ds2_batch(self):
        """DS2 telegram mode: batch read via DS2 cmd 0x0B/0x01 (MS41-specific).

        Sends the one-time setup frame, then polls ~112 ms apart.
        Response layout (38 bytes):
          [2b grp1_status][26b grp1_data][2b grp2_status][8b grp2_data]
        Parameter order and byte-widths match DS2_BATCH_LAYOUT.
        Every recurring sample is one poll response. Confirmed MS41.3 firmware uses
        small one-time reads before setup to select the correct live-data profile.
        Falls back to individual cmd 0x06 reads if setup is unsupported; transient
        poll errors are reported and retried without writing incomplete CSV rows.
        """
        self._prepare_live_profile()
        # Try to run the setup frame; if it fails, fall back to individual reads.
        try:
            self._ds2.setup_telegram_batch(
                ecu_id=self._ecu_id or "",
                entries=batch_wire_entries(self._batch_layout),
            )
        except Exception as e:
            with self._lock:
                self._errors.append(f"DS2 batch setup failed ({e}) — using cmd 0x06 reads")
            self._poll_loop_ds2_reads()
            return

        while not self._stop.is_set():
            cycle_started = time.monotonic()
            csv_row = self._csv_row_base()
            try:
                raw = self._ds2.poll_telegram_batch()
                if len(raw) != 38:
                    raise ValueError(f"short batch payload {len(raw)}/38 bytes")
                _parse_ds2_batch(
                    raw, self._latest, self._lock, csv_row, self._batch_layout)
            except Exception as e:
                with self._lock:
                    self._errors.append(f"DS2 batch poll: {e}")
                self._stop.wait(max(0.0, self._interval - (time.monotonic() - cycle_started)))
                continue
            self._write_csv_row(csv_row)
            self._samples += 1
            self._stop.wait(max(0.0, self._interval - (time.monotonic() - cycle_started)))

    @property
    def csv_rows(self) -> int:
        return self._csv_rows

    @property
    def sample_rate(self) -> float:
        elapsed = time.monotonic() - self._sample_started
        return self._samples / elapsed if self._sample_started and elapsed > 0 else 0.0

    def _open_csv(self, path: str):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self._csv_file  = open(path, "w", newline="", encoding="utf-8")
        self._log_start = time.time()
        self._csv_rows  = 0
        self._csv_last_flush = time.monotonic()
        # Fixed schema regardless of polling mode so any CSV can be opened in MLV:
        #   Time     — elapsed seconds (float); MLV uses this as the primary time axis
        #   Datetime — human-readable full timestamp for post-processing reference
        #   then all standard params + telegram-only extras in display order
        param_cols = [name for name, _unit in display_rows()]
        headers = ["Time", "Datetime"] + param_cols
        self._csv_writer = csv.DictWriter(
            self._csv_file, fieldnames=headers, extrasaction="ignore"
        )
        self._csv_writer.writeheader()

    def _csv_row_base(self) -> dict:
        """Return the per-row dict pre-populated with Time and Datetime fields."""
        row = {
            "Time":     f"{time.time() - self._log_start:.3f}",
            "Datetime": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
        }
        with self._lock:
            for name in _PROFILE_STATUS_NAMES:
                if name in self._latest:
                    row[name] = self._latest[name][0]
        return row

    def _write_csv_row(self, row: dict):
        if self._csv_writer:
            self._csv_writer.writerow(row)
            self._csv_rows += 1
            # Match RomRaider's buffered logger behavior: avoid a synchronous disk flush
            # in the serial acquisition loop for every sample, while still making an active
            # log visible on disk at least once per second.
            now = time.monotonic()
            if now - self._csv_last_flush >= 1.0:
                self._csv_file.flush()
                self._csv_last_flush = now

    def _close_csv(self):
        if self._csv_file:
            self._csv_file.close()
            self._csv_file   = None
            self._csv_writer = None
