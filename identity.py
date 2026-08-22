"""MS41 per-unit identity and provenance decode, encode, graft, EWS frames.

The DME's per-unit serial is stored in flash as 9 ASCII digits at file 0x5CE5
(NUL-terminated at 0x5CEE); its last 4 digits at 0x5CEA are the ISN the external
EWS2 immobilizer is aligned to. The VIN is 6-bit packed at 0x5D07. The offsets
are identical across MS41.0/.1/.2/.3 (verified across 8 ECUs). Stdlib only.
"""
import re
from dataclasses import dataclass, field

_VIN_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_VIN_RE = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")

PRODUCTION_OFF = 0x5CD5   # manufacturing / production identity block
PRODUCTION_END = 0x5CEF
SERIAL_OFF     = 0x5CE5   # per-unit serial: 9 ASCII digits
SERIAL_NUL_OFF = 0x5CEE   # serial terminator
PARTNUM_OFF    = 0x6025   # firmware-common BMW part number (NOT per-unit)
MARK_1585_OFF  = 0x5CE0   # constant "1585" marker right before the serial
AIF_OFF        = 0x5D07   # 14 append-only programming-history records
AIF_RECORD_SIZE = 0x2E
AIF_RECORD_COUNT = 14
AIF_END        = AIF_OFF + AIF_RECORD_SIZE * AIF_RECORD_COUNT
VIN_OFF        = AIF_OFF  # packed VIN begins the first AIF record
VIN_LEN        = 13
FULL_ROM_SIZE  = 262144
IDENTITY_GRAFT_RANGES = (
    (PRODUCTION_OFF, PRODUCTION_END),
    (AIF_OFF, AIF_END),
)

# The identity editor reads a 16 KB file-order window so it can show both the
# per-unit data in param1/SA1 and the adjacent firmware descriptors in param2.
# Only the first 8 KB is ever erased/written for a VIN change.
BOOT_DATA_OFF       = 0x4000
BOOT_DATA_SIZE      = 0x4000
IDENTITY_SECTOR_OFF = 0x4000
IDENTITY_SECTOR_SIZE = 0x2000
TOP_IDENTITY_SECTOR_OFF = 0x0000
TOP_IDENTITY_SECTOR_SIZE = 0x10000


@dataclass
class IdentityInfo:
    part: "str | None" = None
    serial: "str | None" = None
    isn4: "str | None" = None
    vin: "str | None" = None
    layout_ok: bool = False
    notes: list = field(default_factory=list)


def _ascii_digits(b, off, maxlen):
    out = []
    for i in range(off, min(off + maxlen, len(b))):
        if 0x30 <= b[i] <= 0x39:
            out.append(chr(b[i]))
        else:
            break
    return "".join(out)


def _layout_ok(b):
    notes = []
    if len(b) < 0x6100:
        return False, ["image too small / not a 256 KB full read"]
    if _ascii_digits(b, PARTNUM_OFF, 7) == "" or len(_ascii_digits(b, PARTNUM_OFF, 7)) != 7:
        notes.append("no 7-digit part number at 0x6025")
    if bytes(b[MARK_1585_OFF:MARK_1585_OFF + 4]) != b"1585":
        notes.append("missing '1585' marker at 0x5CE0")
    if len(_ascii_digits(b, SERIAL_OFF, 12)) < 8:
        notes.append("serial at 0x5CE5 too short")
    return (len(notes) == 0), notes


def decode_identity(data: bytes) -> IdentityInfo:
    """Decode part# / serial / ISN / VIN from a 256 KB full ROM."""
    if len(data) < 0x6100:
        return IdentityInfo(layout_ok=False, notes=["not a full ROM (identity needs a 256 KB read)"])
    ok, notes = _layout_ok(data)
    part = _ascii_digits(data, PARTNUM_OFF, 7) or None
    serial = _ascii_digits(data, SERIAL_OFF, 12) or None
    # The ISN is the last 4 digits of the serial (0x5CEA = 0x5CE5 + 5). Derive it from
    # the validated serial so it is None whenever the serial is absent/invalid, rather
    # than returning raw chr() bytes from an unprogrammed region.
    isn4 = serial[-4:] if serial and len(serial) >= 4 else None
    info = IdentityInfo(part=part, serial=serial, isn4=isn4, layout_ok=ok, notes=notes)
    info.vin = decode_vin(data)
    return info


def decode_vin(data: bytes) -> "str | None":
    """Decode the 17-char VIN 6-bit-packed at 0x5D07, or None if unprogrammed."""
    if len(data) < VIN_OFF + VIN_LEN:
        return None
    raw = bytes(data[VIN_OFF:VIN_OFF + VIN_LEN]).rjust(15, b"\x00")
    chars = []
    for i in range(0, 15, 3):
        x = (raw[i] << 16) | (raw[i + 1] << 8) | raw[i + 2]
        for s in (18, 12, 6, 0):
            idx = (x >> s) & 0x3F
            if idx >= len(_VIN_CHARS):
                return None
            chars.append(_VIN_CHARS[idx])
    vin = "".join(chars)[3:]
    return vin if _VIN_RE.match(vin) else None


def encode_vin(vin: str) -> bytes:
    """Pack a 17-char VIN into the 13-byte 6-bit field (inverse of decode_vin)."""
    vin = vin.upper()
    if not _VIN_RE.match(vin):
        raise ValueError(f"invalid VIN: {vin!r}")
    padded = "000" + vin  # 3 pad chars -> 20 chars -> 5 groups of 4 -> 15 bytes
    out = bytearray()
    for i in range(0, 20, 4):
        x = 0
        for j in range(4):
            x = (x << 6) | _VIN_CHARS.index(padded[i + j])
        out += bytes([(x >> 16) & 0xFF, (x >> 8) & 0xFF, x & 0xFF])
    return bytes(out[2:])  # drop the 2 leading pad bytes -> 13 bytes


def set_vin(data: bytes, vin: str) -> bytearray:
    """Return a copy of `data` with the VIN field replaced. Checksum-neutral:
    0x5D07 is in the un-checksummed gap 0x5C14-0x6100, so no recompute is needed."""
    out = bytearray(data)
    out[VIN_OFF:VIN_OFF + VIN_LEN] = encode_vin(vin)
    return out


def boot_data_image(data: bytes) -> bytearray:
    """Expand the 16 KB BOOT identity window into a file-offset-addressable image."""
    data = bytes(data)
    if len(data) != BOOT_DATA_SIZE:
        raise ValueError(
            f"BOOT identity data must be {BOOT_DATA_SIZE} bytes, got {len(data)}")
    out = bytearray(b"\xFF" * (BOOT_DATA_OFF + BOOT_DATA_SIZE))
    out[BOOT_DATA_OFF:BOOT_DATA_OFF + BOOT_DATA_SIZE] = data
    return out


def decode_boot_identity(data: bytes) -> IdentityInfo:
    """Decode identity fields from the cached 16 KB BOOT identity window."""
    return decode_identity(bytes(boot_data_image(data)))


def set_boot_vin(data: bytes, vin: str) -> bytearray:
    """Return a BOOT identity window with only its packed VIN field changed."""
    out = bytearray(data)
    if len(out) != BOOT_DATA_SIZE:
        raise ValueError(
            f"BOOT identity data must be {BOOT_DATA_SIZE} bytes, got {len(out)}")
    rel = VIN_OFF - BOOT_DATA_OFF
    out[rel:rel + VIN_LEN] = encode_vin(vin)
    return out


def boot_strings(data: bytes, *, min_length: int = 4, max_items: int = 32) -> list:
    """Return conservative printable runs as ``(file_offset, text)`` pairs.

    These are deliberately not assigned meanings. Offsets make the raw evidence
    useful without presenting incidental firmware text as an identified field.
    """
    data = bytes(data)
    if len(data) != BOOT_DATA_SIZE:
        raise ValueError(
            f"BOOT identity data must be {BOOT_DATA_SIZE} bytes, got {len(data)}")
    found = []
    start = None
    for index in range(len(data) + 1):
        printable = index < len(data) and 0x20 <= data[index] <= 0x7E
        if printable and start is None:
            start = index
        if not printable and start is not None:
            raw = data[start:index]
            text = raw.decode("ascii", errors="ignore").strip()
            # Require useful-looking content and reject padding-like repeated runs.
            readable = sum(ch.isalnum() or ch in " ._-/():" for ch in text)
            if (len(text) >= min_length and any(ch.isalnum() for ch in text)
                    and len(set(text)) > 1 and readable / len(text) >= 0.85):
                found.append((BOOT_DATA_OFF + start, text))
                if len(found) >= max_items:
                    break
            start = None
    return found


def graft_identity(target: bytes, source: bytes) -> bytearray:
    """Carry the ECU's manufacturing identity and AIF history onto a target.

    Copies the complete production block (including the serial/ISN) and all 14
    AIF programming-history records (including the packed VIN). The coding-family
    gap and firmware-owned ZIF/program descriptors are intentionally left to their
    existing owners. Checksum-neutral: both ranges are inside 0x5C14-0x6100.
    """
    if len(target) < 0x6100 or len(source) < 0x6100:
        raise ValueError("target and source must contain the complete identity ranges")
    out = bytearray(target)
    for start, end in IDENTITY_GRAFT_RANGES:
        out[start:end] = source[start:end]
    return out


def _ds2_frame(dst: int, data: list) -> bytes:
    """Build a DS2 frame [addr][len][data...][xor]; len = total frame length."""
    length = 1 + len(data) + 2
    f = [dst, length] + list(data)
    x = 0
    for b in f:
        x ^= b
    f.append(x)
    return bytes(f)


def ews_frames(isn4: str) -> dict:
    """Build the two DS2 frames for EWS alignment against a 4-digit ISN.

    read : ask the DME to report its identity (module 0x12, cmd 0x00).
    write: store the ISN into the EWS2 (module 0x44, cmd 0x61) as a 12-bit value,
           the same number the DME reports in decimal, expressed in hex.

    The proven legacy implementation used a 10,000-entry
    lookup table. Every table entry is exactly ``decimal_isn & 0xFFF``; this is
    protocol encoding, not an error condition for decimal values above 4095.
    """
    isn4 = isn4.strip()
    if not (isn4.isdigit() and len(isn4) == 4):
        raise ValueError(f"ISN must be 4 decimal digits, got {isn4!r}")
    dec = int(isn4, 10)
    v = dec & 0xFFF                     # EWS carries 3 hex nibbles (12 bits)
    hh = (v >> 8) & 0xF
    ll = v & 0xFF
    return {
        "read": _ds2_frame(0x12, [0x00]),
        "write": _ds2_frame(0x44, [0x61, hh, ll]),
        "hex_value": v,
        "wrapped": dec > 0xFFF,
        "truncated": dec > 0xFFF,  # compatibility for older callers; do not present as a warning
    }
