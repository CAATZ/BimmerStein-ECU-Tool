"""Pure BMW AIF decoding and VIN update plans; no flash operations.

The native MS43 OBD reader selects the newest record whose first byte is not
FF. BMW's AIF reader additionally honors each field's trailing validity byte.
Programming the first word last therefore leaves an unfinished body unselected.
"""

from dataclasses import dataclass


AIF_RECORD_SIZE = 46
AIF_RECORD_COUNT = 14
AIF_SIZE = AIF_RECORD_SIZE * AIF_RECORD_COUNT
AIF_ADDRESSES = {"MS42": 0x3C4A, "MS43": 0x3C40}
VIN_WRITE_SOFTWARE = {"MS42": ("0110C6", "0110C7", "0110CA"),
                      "MS43": ("430037", "430055", "430064", "430065", "430066", "430069", "43X001")}
# These application-owned status words are independent of the selected boot.
PAIRING_STATUS = {
    "MS42": {"0110C6": 0xF90C, "0110C7": 0xF90C, "0110CA": 0xF90C},
    "MS43": {"430037": 0xE2B0, "430055": 0xE2CE, "430064": 0xE2D0,
             "430065": 0xE2D0, "430066": 0xE2D0, "430069": 0xE2D0},
}
_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_VIN_CHARACTERS = frozenset(_ALPHABET) - set("IOQ")
_ERASED = b"\xFF" * AIF_RECORD_SIZE
# BMW-valid groups already decoded by the shared AIF reader. Other bytes stay
# exactly as in the latest record; earlier records are retained on an append.
_FIELDS = ((14, 17), (17, 23), (23, 27), (27, 31), (35, 41), (41, 44), (44, 46))


def encode_vin(vin):
    """Encode an entered 17-character VIN using BMW's six-bit alphabet."""
    if not isinstance(vin, str):
        raise ValueError("VIN must contain 17 letters and digits")
    vin = vin.strip()
    if (not vin.isascii() or len(vin) != 17
            or any(char not in _VIN_CHARACTERS for char in vin.upper())):
        raise ValueError("VIN must contain 17 ASCII letters and digits, excluding I, O and Q")
    vin = vin.upper()
    value = 0
    for char in vin:
        value = value * 64 + _ALPHABET.index(char)
    return value.to_bytes(13, "big")


def decode_vin(packed):
    """Keep the legacy BMW decoder permissive for existing recorded identities."""
    if len(packed) != 13:
        raise ValueError("Packed VIN must contain 13 bytes")
    value = int.from_bytes(packed, "big")
    result = ""
    while value:
        value, digit = divmod(value, 64)
        if digit >= len(_ALPHABET):
            raise ValueError("Packed VIN contains a digit outside the documented alphabet")
        result = _ALPHABET[digit] + result
    if len(result) > 17:
        raise ValueError("Packed VIN exceeds 17 characters")
    return result or None


def _slots(history):
    if not isinstance(history, (bytes, bytearray, memoryview)):
        raise ValueError("AIF history must be bytes")
    history = bytes(history)
    if len(history) != AIF_SIZE:
        raise ValueError(f"AIF history must contain {AIF_SIZE} bytes")
    return tuple(history[offset:offset + AIF_RECORD_SIZE]
                 for offset in range(0, AIF_SIZE, AIF_RECORD_SIZE))


def _slot_state(slot):
    if slot == _ERASED:
        return "erased"
    if slot[:2] == b"\xFF\xFF":
        return "uncommitted"
    if slot[0] == 0xFF:
        return "interrupted"
    return "occupied"


def decode_aif(history):
    """Decode committed fields while retaining interrupted records and warnings."""
    records, current, states, warnings = [], {}, [], []
    erased_seen = False
    native_vin = None
    occupied = 0
    for index, slot in enumerate(_slots(history)):
        state = _slot_state(slot)
        states.append(state)
        if state == "erased":
            erased_seen = True
            continue
        record = {"aif_record_number": index + 1, "raw": slot.hex(), "aif_state": state}
        records.append(record)
        if state != "occupied":
            warnings.append(f"AIF record {index + 1} contains an unfinished write")
            continue
        if erased_seen:
            raise ValueError("AIF history contains an occupied record after a free record")
        occupied += 1
        try:
            native_vin = (decode_vin(slot[:13]) or "").zfill(17)
        except ValueError:
            native_vin = None
            warnings.append(f"AIF record {index + 1} contains an invalid packed VIN")
        if slot[13] == 0:
            record["vin"] = native_vin
        if slot[16] == 0:
            day, month, year = slot[14] >> 3, ((slot[14] & 7) << 1) | (slot[15] >> 7), slot[15] & 127
            # BMW emits DD.MM.YY; 100..127 produce non-digit year characters, not a century.
            # Year 00 can be a leap year; the record does not establish its century.
            month_days = (31, 29 if year % 4 == 0 else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)
            record["programming_date"] = (
                f"{day:02d}.{month:02d}.{year:02d}"
                if year <= 99 and 1 <= month <= 12 and 1 <= day <= month_days[month - 1] else None)
        for name, offset, marker in (("recorded_software_number", 17, 22),
                                    ("recorded_authority_number", 23, 26),
                                    ("recorded_zb_zusb", 27, 30)):
            if slot[marker] == 0:
                record[name] = str(int.from_bytes(slot[offset:offset + 3], "big"))
        if slot[22] == 0:
            record["recorded_change_index"] = f"{slot[20]:02X}"
        if slot[40] == 0:
            # The first NUL ends the BMW serial-number string.
            serial = slot[35:40].split(b"\x00", 1)[0].rstrip(b" ")
            record["programmer"] = (serial.decode("ascii") if all(32 <= byte < 127 for byte in serial)
                                    else "Raw " + serial.hex(" ").upper())
        if slot[43] == 0:
            record["workshop_code"] = int.from_bytes(slot[41:43], "big")
        if slot[45] == 0:
            record["recorded_distance_km"] = slot[44] * 600
        current.update({key: value for key, value in record.items()
                        if key not in ("raw", "aif_state")})
    current.setdefault("vin", None)
    if native_vin != current["vin"]:
        warnings.append("Latest native VIN differs from the BMW-valid AIF VIN")
    current.update(aif_records=records, programming_count=occupied,
                   native_vin=native_vin, aif_slot_states=states, aif_warnings=warnings)
    return current


def current_vin(history):
    return decode_aif(history)["vin"]


def read_aif(ecu, directory=None):
    """Read one published AIF history; callers may reuse their directory reply."""
    if ecu.family not in AIF_ADDRESSES:
        raise ValueError("Expected an MS42 or MS43 ECU")
    if directory is None:
        directory = ecu._frame(0x0D, minimum=67)
    if len(directory) < 67:
        raise ValueError("AIF address directory is incomplete")
    address = int.from_bytes(directory[63:66], "big")
    maximum = 0xF0000 if ecu.family == "MS43" else 0x1000000
    if address + AIF_SIZE > maximum:
        raise ValueError("AIF history address is outside the family memory range")
    raw = ecu.read_memory(address, AIF_SIZE)
    _slots(raw)
    return address, bytes(raw)


@dataclass(frozen=True)
class VinPlan:
    vin: str
    original_history: bytes
    target_history: bytes
    record_index: int
    record: bytes
    needs_sector_rewrite: bool
    mode: str


def plan_vin(history, vin):
    """Plan an append, or a byte-preserving current-record replacement when full.

    An unfinished slot is never assumed erased or silently resumed. Recovery
    must use the writer's saved immutable transaction; a fresh plan rewrites.
    """
    packed = encode_vin(vin)
    vin = vin.strip().upper()
    slots = _slots(history)
    history = bytes(history)
    info = decode_aif(history)
    occupied = [index for index, state in enumerate(info["aif_slot_states"])
                if state == "occupied"]
    latest = occupied[-1] if occupied else -1
    if info["vin"] == vin and info["native_vin"] == vin:
        return VinPlan(vin, history, history, latest, slots[latest], False, "unchanged")
    rewrite = any(state in ("uncommitted", "interrupted") for state in info["aif_slot_states"])
    if latest == AIF_RECORD_COUNT - 1:
        index, mode, rewrite = latest, "replace", True
        record = bytearray(slots[index])
    else:
        index, mode = latest + 1, "append"
        record = bytearray(slots[latest] if latest >= 0 else _ERASED)
        for previous in (slots[i] for i in occupied):
            for start, end in _FIELDS:
                if previous[end - 1] == 0:
                    record[start:end] = previous[start:end]
        rewrite = rewrite or slots[index] != _ERASED
    record[:13] = packed
    record[13] = 0
    target = bytearray(history)
    offset = index * AIF_RECORD_SIZE
    target[offset:offset + AIF_RECORD_SIZE] = record
    return VinPlan(vin, history, bytes(target), index, bytes(record), rewrite, mode)
