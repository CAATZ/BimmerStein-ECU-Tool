"""ecu_info.py — pure decode helpers for the ECU Info tab.

All addresses are DS2/CPU addresses (already XOR-0x4000-translated from the
documented FILE offsets used elsewhere in this project), safe to read_mem()
on a normally-running ECU — no BSL/flash-listen mode required. Stdlib only,
no Qt / hardware dependency, so these are unit-testable with plain bytes.
"""

# Flash-driver signature @ SA1 driver entry (DS2 0x023C = file 0x423C).
# Byte-identical location across MS41.0/.1/.2/.3; identifies the flash DRIVER
# family compiled into the firmware (AMD vs Intel command set), not the
# exact silicon part number. Same read-only check already used by
# engines/softbsl/softbsl_host.py's auto-detect.
DRV_SIG_ADDR  = 0x023C
DRV_SIG_LEN   = 8
DRV_SIG_FILE_OFFSET = DRV_SIG_ADDR ^ 0x4000
_DRV_SIG_AMD   = bytes.fromhex("e00e0d58f04ec084")   # AMD driver -> 29F200 / 29F400 (bottom half)
_DRV_SIG_INTEL = bytes.fromhex("e6f45000b84c6fe0")   # Intel driver -> 28F200

# Program-side BMW part number (DS2 0x2025 = file 0x6025), 7 ASCII digits.
# Firmware-common (not per-unit) -- present even under a community ECU-ID
# string like "SHINDE1", since SS1v2 doesn't touch this region.
FW_VERSION_ADDR = 0x2025
FW_VERSION_LEN  = 7

# Per-unit serial block (DS2 0x1CE0 = file 0x5CE0). Layout: "1585" marker
# (4B) followed by a 1-byte gap into the 9-digit serial (file 0x5CE5 = DS2
# 0x1CE5, offset 5 within this 14-byte block) -- one read covers both.
ISN_BLOCK_ADDR = 0x1CE0
ISN_BLOCK_LEN  = 14
_ISN_MARKER    = b"1585"
_SERIAL_OFFSET_IN_BLOCK = 5   # 0x1CE5 - 0x1CE0
_SERIAL_LEN    = 9

# Live AT/MT flag (working RAM, not subject to the flash-descramble XOR law).
# MS41.0 owns the flag at FD4C; later program families own it at FD5C.
TRANS_FLAG_ADDR = 0xFD5C
TRANS_FLAG_ADDRS = {
    "MS41.0": 0xFD4C,
    "MS41.1": TRANS_FLAG_ADDR,
    "MS41.2": TRANS_FLAG_ADDR,
    "MS41.3": TRANS_FLAG_ADDR,
}


def transmission_flag_address(program_family: str | None) -> int | None:
    return TRANS_FLAG_ADDRS.get(str(program_family or "").strip().upper())


# Read-only live identity evidence. The compatibility IDs are the same
# program/calibration pairing values used by the desktop transfer owner.
PROGRAM_COMPAT_ADDR = 0x2007
CALIBRATION_COMPAT_ADDR = 0x1000C
CAL_ID_ADDR = 0x1000E
CAL_ID_LEN = 8
MS413_CAL_MARKER_ADDR = 0x133BB
MS413_CAL_MARKER = b"SS1v2"
MS413_CREDIT_ADDR = 0x15F60
MS413_CREDIT_MARKER = b"ABHISHEK"

# Exact MS41.2 AIF geometry recovered from the application status builder and
# 10MDS412.prg. MS41.3 inherits this region from 1406464; it is deliberately
# not projected onto MS41.0/.1.
AIF_FILE_ADDR = 0x5D07
AIF_DS2_ADDR = AIF_FILE_ADDR ^ 0x4000
AIF_RECORD_SIZE = 0x2E
AIF_RECORD_COUNT = 14
AIF_LEN = AIF_RECORD_SIZE * AIF_RECORD_COUNT

# Compact, packaged subset of the normalized BMW DATEN assembly table. These
# are the exact MDS412 rows needed to explain an AIF ZB without parsing DATEN at
# runtime. Values are (type/hardware, program, program index, calibration data).
_MDS412_ZB = {
    "7831585": ("1405968", "1406464", "C", "7831586DA"),
    "7830455": ("1405548", "1406464", "C", "7830456DA"),
    "7830636": ("1405548", "1406464", "C", "7830637DA"),
    "1407151": ("1406680", "1406464", "C", "1407152DA"),
    "7830457": ("1406680", "1406464", "C", "7830458DA"),
    "7830638": ("1406680", "1406464", "C", "7830639DA"),
    "7830640": ("1406680", "1406464", "C", "7830641DA"),
    "1407135": ("1405968", "1406464", "C", "1407137DA"),
    "1407136": ("1405548", "1406464", "C", "1407138DA"),
    "7830287": ("1406680", "1406464", "C", "7830286DA"),
}


# Soft-BSL bank-ID marker (DS2 0x1FFC = file 0x5FFC, byte pattern A5 5A <half> <~half>),
# resident-flash and readable over plain DS2 (no agent needed) — used to decide whether
# the Fast checkbox can be offered without entering the agent first.
BANK_MARKER_ADDR = 0x1FFC
BANK_MARKER_LEN  = 4


def resolve_live_variants(ecu_id, cal_id=b"", program_part=b"",
                          program_compat=b"", calibration_compat=b"",
                          program_signature=b"", cal_marker=b"", credit=b""):
    """Return ``(cal_variant, program_variant, consistent)`` from live evidence.

    A corrupt/unlisted ECU-ID does not erase the independent CAL and repeated
    program/calibration compatibility evidence. MS41.2 versus community
    MS41.3 remains separated only by the exact admitted markers.
    """
    from ms41 import SS1V2_PROG_SIG, variant_from_cal_id, variant_from_program_id

    cal_variant = (
        "MS41.3"
        if cal_marker == MS413_CAL_MARKER or credit == MS413_CREDIT_MARKER
        else variant_from_cal_id(calibration_compat)
        or variant_from_cal_id(cal_id)
    )
    base_program = (
        variant_from_cal_id(program_compat)
        or variant_from_program_id(program_part)
        or variant_from_program_id(ecu_id)
    )
    if base_program == "MS41.2" or str(ecu_id or "") == "SHINDE1":
        if program_signature == SS1V2_PROG_SIG:
            program_variant = "MS41.3"
        elif program_signature == b"\xFF" * len(SS1V2_PROG_SIG):
            program_variant = "MS41.2"
        else:
            program_variant = None
    else:
        program_variant = base_program

    # SS1v2 keeps BMW's ID12 calibration-family prefix. The exact program
    # signature is what distinguishes the derived MS41.3 firmware from stock
    # MS41.2 when optional calibration-side markers have been replaced.
    if program_variant == "MS41.3" and cal_variant == "MS41.2":
        cal_variant = "MS41.3"

    consistent = bool(
        cal_variant and program_variant and cal_variant == program_variant
    )
    return cal_variant, program_variant, consistent


def decode_bank_marker(raw: bytes) -> str:
    """'T'/'B' from a live 4-byte read at BANK_MARKER_ADDR, or None if absent/invalid.
    Mirrors engines/softbsl/softbsl_host.image_marker's byte pattern, applied to a bare
    live read instead of a file offset."""
    if len(raw) < 4 or raw[0] != 0xA5 or raw[1] != 0x5A:
        return None
    half = raw[2]
    if (half ^ 0xFF) != raw[3]:
        return None
    return {0x54: "T", 0x42: "B"}.get(half)


def decode_flash_chip(sig: bytes) -> str:
    """Map an 8-byte SA1 driver signature to a chip family label. Never
    guesses: an unrecognized signature (or no response) reports itself as
    unknown, including the raw bytes so a human can investigate."""
    if sig == _DRV_SIG_AMD:
        return "AMD driver — 29F200 / 29F400 (bottom half)"
    if sig == _DRV_SIG_INTEL:
        return "Intel driver — 28F200"
    shown = sig.hex() if sig else "no response"
    return f"Unknown (unexpected signature: {shown})"


def chip_family(sig: bytes) -> str:
    """The flash DRIVER FAMILY from the SA1 driver signature: 'amd' (29F200/29F400), 'intel'
    (28F200), or None if unrecognized. Selects which soft-BSL agent command set to use. Never
    guesses — an unknown signature returns None so the caller can fail safe."""
    if sig == _DRV_SIG_AMD:
        return "amd"
    if sig == _DRV_SIG_INTEL:
        return "intel"
    return None


def image_chip_family(image: bytes) -> str:
    """Flash-driver family carried by a full file-order image, or ``None``.

    This is deliberately separate from physical-silicon identification: it
    answers which command-set driver the image would install at file 0x423C.
    Live flashing compares it with the connected ECU's read-only driver-family
    probe before any erase; offline patch composition remains unrestricted.
    """
    image = bytes(image)
    end = DRV_SIG_FILE_OFFSET + DRV_SIG_LEN
    if len(image) < end:
        return None
    return chip_family(image[DRV_SIG_FILE_OFFSET:end])


def decode_firmware_version(raw: bytes) -> str:
    """7-ASCII-digit program-side BMW part number, or 'Unavailable' if the
    read came back short or non-numeric."""
    if len(raw) != FW_VERSION_LEN or any(not 0x30 <= b <= 0x39 for b in raw):
        return "Unavailable"
    return bytes(raw).decode("ascii")


def _ascii_field(raw: bytes, *, digits=False) -> str | None:
    """Decode one fixed-width BMW string field without manufacturing text."""
    value = bytes(raw).rstrip(b"\x00 ")
    if not value or any(byte < 0x20 or byte > 0x7E for byte in value):
        return None
    text = value.decode("ascii")
    return text if not digits or text.isdigit() else None


def decode_identification(raw: bytes) -> dict:
    """Decode the 42-byte normal-runtime IDENT payload described by BMW PRGs."""
    raw = bytes(raw)
    result = {
        "reported_identifier": _ascii_field(raw[:7]) if len(raw) >= 7 else None,
        "bmw_hardware_number": None,
        "coding_index": None,
        "diagnostic_index": None,
        "bus_index": None,
        "manufacturing_week": None,
        "manufacturing_year": None,
        "supplier_number": None,
        "software_index": None,
        "change_index": None,
        "dme_production_serial": None,
    }
    if len(raw) != 42:
        return result
    result.update({
        "bmw_hardware_number": _ascii_field(raw[7:9]),
        "coding_index": _ascii_field(raw[9:11]),
        "diagnostic_index": _ascii_field(raw[11:13]),
        "bus_index": _ascii_field(raw[13:15]),
        "manufacturing_week": _ascii_field(raw[15:17], digits=True),
        "manufacturing_year": _ascii_field(raw[17:19], digits=True),
        "supplier_number": _ascii_field(raw[19:29]),
        "software_index": _ascii_field(raw[29:31]),
        "change_index": _ascii_field(raw[31:33]),
        "dme_production_serial": _ascii_field(raw[33:42], digits=True),
    })
    if result["dme_production_serial"] and len(result["dme_production_serial"]) != 9:
        result["dme_production_serial"] = None
    return result


def format_calibration_id(calibration_id: str) -> str:
    return f"{calibration_id} ({calibration_id[:2]})"


def format_program_calibration_match(program_id, calibration_id) -> str:
    """Render the independent four-character firmware compatibility IDs."""
    def value(raw):
        if isinstance(raw, str):
            raw = raw.encode("ascii", "ignore")
        text = _ascii_field(bytes(raw or b""), digits=True)
        return text if text and len(text) == 4 else None

    program = value(program_id)
    calibration = value(calibration_id)
    if not program or not calibration:
        return "Unavailable"
    verdict = "Matched" if program == calibration else "Mismatch"
    return f"{program} / {calibration} — {verdict}"


def format_family(cal_variant, program_variant, consistent=False) -> str:
    """Make program/calibration family evidence explicit when it conflicts."""
    if cal_variant and program_variant:
        if consistent:
            return program_variant
        return f"Program {program_variant} / calibration {cal_variant} — Mismatch"
    if program_variant:
        return f"{program_variant} (program evidence)"
    if cal_variant:
        return f"{cal_variant} (calibration evidence)"
    return "Unresolved"


def _aif_number(raw: bytes) -> str | None:
    value = int.from_bytes(raw, "big")
    text = str(value)
    return text if 0 < value < 0xFFFFFF and len(text) == 7 else None


def decode_aif_history(raw: bytes, family: str | None) -> dict:
    """Decode the current append-only MS41.2/.3 AIF programming record.

    ``raw`` may be the bare 14-record range or a file-offset-addressable image.
    A non-erased slot after an erased slot is rejected because it cannot satisfy
    BMW's "current record followed by a free record" selection rule.
    """
    if family not in ("MS41.2", "MS41.3"):
        return {}
    raw = bytes(raw)
    if len(raw) >= AIF_FILE_ADDR + AIF_LEN:
        raw = raw[AIF_FILE_ADDR:AIF_FILE_ADDR + AIF_LEN]
    if len(raw) != AIF_LEN:
        return {}

    occupied = []
    free_seen = False
    for offset in range(0, AIF_LEN, AIF_RECORD_SIZE):
        record = raw[offset:offset + AIF_RECORD_SIZE]
        if record == b"\xFF" * AIF_RECORD_SIZE:
            free_seen = True
        elif free_seen:
            return {}
        else:
            occupied.append(record)
    if not occupied:
        return {}

    record = occupied[-1]
    day = record[0x0E] >> 3
    month = ((record[0x0E] & 0x07) << 1) | (record[0x0F] >> 7)
    year = 1900 + (record[0x0F] & 0x7F)
    date = f"{day:02d}.{month:02d}.{year:04d}" if (
        1 <= day <= 31 and 1 <= month <= 12 and 1980 <= year <= 2027
    ) else None
    zb = _aif_number(record[0x1B:0x1E])
    software = _aif_number(record[0x11:0x14])
    if not (zb and software and date):
        return {}
    return {
        "recorded_zb_zusb": zb,
        "programming_date": date,
        "recorded_software_number": software,
        "programming_count": len(occupied),
    }


def format_daten_lineage(recorded_zb, program_part) -> str:
    """Explain a recorded MS41.2 ZB using the packaged normalized DATEN row."""
    row = _MDS412_ZB.get(str(recorded_zb or ""))
    if row is None:
        return "Not found in bundled MS41.2 DATEN" if recorded_zb else "Unavailable"
    type_number, program_number, program_index, calibration = row
    live_program = str(program_part or "")
    verdict = (
        "matches live program" if live_program == program_number
        else f"live program is {live_program}" if live_program and live_program != "Unavailable"
        else "live program unavailable"
    )
    return (
        f"Type {type_number}; program {program_number} {program_index}; "
        f"calibration {calibration} — {verdict}"
    )


def decode_transmission(raw: bytes) -> str:
    """Bit 7 of the family-specific live RAM flag: 1 = Automatic, 0 = Manual."""
    if not raw:
        return "Unavailable"
    return "Automatic" if (raw[0] & 0x80) else "Manual"


def format_full_isn_html(serial: str, isn4_hint: str = "") -> str:
    """Full 9-digit serial with the last 4 digits bold, HTML-formatted for a
    QLabel. Only trusted when it's exactly 9 digits AND (if a hint is given)
    the derived last-4 matches it -- otherwise falls back to the bare hint
    with a visible '(unverified)' note, rather than risking a wrong/garbled
    serial showing plausible-looking digits as fact."""
    if serial and len(serial) == _SERIAL_LEN and (not isn4_hint or serial[-4:] == isn4_hint):
        return f"{serial[:5]}<b>{serial[5:]}</b>"
    if isn4_hint:
        return f"{isn4_hint} <span style='color:#888;'>(full serial unverified)</span>"
    return "Unavailable"


def _serial_from_isn_block(block: bytes) -> str:
    if len(block) < ISN_BLOCK_LEN or block[:4] != _ISN_MARKER:
        return ""
    raw = block[_SERIAL_OFFSET_IN_BLOCK:_SERIAL_OFFSET_IN_BLOCK + _SERIAL_LEN]
    return raw.decode("ascii") if raw.isdigit() else ""


def decode_full_isn(block: bytes, isn4_live: str) -> str:
    """Full 9-digit serial with the last 4 digits bold, decoded from a live
    14-byte DS2 read. Only trusted when the '1585' marker is intact -- see
    format_full_isn_html() for the shared last-4 cross-check + fallback."""
    return format_full_isn_html(_serial_from_isn_block(block), isn4_live)


def decode_full_isn_text(block: bytes, isn4_live: str) -> str:
    """Plain-text form for non-HTML clients, preserving the same verification gate."""
    serial = _serial_from_isn_block(block)
    if serial and (not isn4_live or serial[-4:] == isn4_live):
        return f"{serial} (ISN {serial[-4:]})"
    if isn4_live:
        return f"{isn4_live} (full serial unverified)"
    return "Unavailable"


def format_new_fields(fw_raw: bytes, isn_block: bytes, isn4_live: str,
                       chip_sig: bytes, trans_raw: bytes, ident_raw=b"") -> dict:
    """Assemble shared human-readable ECU Info values from live DS2 bytes."""
    serial = _serial_from_isn_block(isn_block)
    if not serial:
        serial = decode_identification(ident_raw)["dme_production_serial"] or ""
    isn4 = isn4_live if len(isn4_live) == 4 and isn4_live.isdigit() else (
        serial[-4:] if len(serial) == 9 else ""
    )
    return {
        "BMW Program Part Number": decode_firmware_version(fw_raw),
        "DME Production Serial": serial or "Unavailable",
        "EWS2 ISN": isn4 or "Unavailable",
        "Flash Command-Set Driver": decode_flash_chip(chip_sig),
        "Transmission Mode": decode_transmission(trans_raw),
    }
