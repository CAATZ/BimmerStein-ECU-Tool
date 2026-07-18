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

# Live AT/MT flag (working RAM, not subject to the flash-descramble XOR law
# -- read directly).
TRANS_FLAG_ADDR = 0xFD5C


# Soft-BSL bank-ID marker (DS2 0x1FFC = file 0x5FFC, byte pattern A5 5A <half> <~half>),
# resident-flash and readable over plain DS2 (no agent needed) — used to decide whether
# the Fast checkbox can be offered without entering the agent first.
BANK_MARKER_ADDR = 0x1FFC
BANK_MARKER_LEN  = 4


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
    digits = "".join(chr(b) for b in raw if 0x30 <= b <= 0x39)
    if len(digits) != FW_VERSION_LEN:
        return "Unavailable"
    return digits


def decode_transmission(raw: bytes) -> str:
    """Bit 7 of the live 0xFD5C RAM flag: 1 = Automatic, 0 = Manual."""
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


def decode_full_isn(block: bytes, isn4_live: str) -> str:
    """Full 9-digit serial with the last 4 digits bold, decoded from a live
    14-byte DS2 read. Only trusted when the '1585' marker is intact -- see
    format_full_isn_html() for the shared last-4 cross-check + fallback."""
    serial = ""
    if len(block) >= ISN_BLOCK_LEN and block[:4] == _ISN_MARKER:
        raw_serial = block[_SERIAL_OFFSET_IN_BLOCK:_SERIAL_OFFSET_IN_BLOCK + _SERIAL_LEN]
        serial = "".join(chr(b) for b in raw_serial if 0x30 <= b <= 0x39)
    return format_full_isn_html(serial, isn4_live)


def format_new_fields(fw_raw: bytes, isn_block: bytes, isn4_live: str,
                       chip_sig: bytes, trans_raw: bytes) -> dict:
    """Assemble the 4 new/fixed ECU Info label values from raw
    DS2 read_mem() bytes: Firmware Version, ISN, Flash Chip, Transmission."""
    return {
        "Firmware Version": decode_firmware_version(fw_raw),
        "ISN":               decode_full_isn(isn_block, isn4_live),
        "Flash Chip":        decode_flash_chip(chip_sig),
        "Transmission":      decode_transmission(trans_raw),
    }
