"""Read identity from complete images using the reviewed native restore rules."""

from ms42_ms43_image import FULL_SIZE, image_identity
from ms42_ms43_identity import PAIRING_POINTERS
from ms42_ms43_vin import AIF_ADDRESSES, AIF_SIZE, decode_aif


_SOFTWARE = {"MS42": ("0110C6", "0110C7", "0110CA"),
             "MS43": ("430037", "430055", "430064", "430065", "430066", "430069", "43X001")}


def _word(data, offset):
    return int.from_bytes(data[offset:offset + 2], "little")


def _isn_words(raw):
    first, second, third = (_word(raw, offset) for offset in (0, 2, 4))
    if second == first ^ 0x00FF or third == first ^ 0xFF00:
        recovered = second != first ^ 0x00FF or third != first ^ 0xFF00
    elif second == third ^ 0xFFFF:
        first, recovered = second ^ 0x00FF, True
    else:
        return {"isn": None, "isn_status": "Invalid ISN data"}, False
    return {"isn": f"{first ^ 0xFFFF:04X}", "isn_status": "Stored"}, recovered


def decode_identity(data):
    """Decode recorded identity, never treating a file as proof of live pairing."""
    identity = image_identity(data)
    family = identity["family"]
    info = {**identity, "vin": None, "isn": None, "isn_status": "Unavailable",
            "ecu_number": None, "warnings": []}
    if len(data) != FULL_SIZE:
        return info
    if identity["software_id"].upper() not in _SOFTWARE[family]:
        info["warnings"].append("Offline identity layout is not mapped for this software.")
        return info
    number = bytes(data[0x3F80:0x3F88]).strip(b"\x00\xFF ")
    if number and all(0x20 <= char < 0x7F for char in number):
        info["ecu_number"] = number.decode("ascii")
    try:
        start = AIF_ADDRESSES[family]
        info.update(decode_aif(data[start:start + AIF_SIZE]))
        info["warnings"].extend(info["aif_warnings"])
    except ValueError as error:
        info["warnings"].append(f"AIF: {error}")
    if identity["software_id"].upper() == "43X001":
        info["warnings"].append("ISN not decoded: 43X001 removes the original pairing handlers.")
        return info
    pointer_at, start = PAIRING_POINTERS[family]
    if int.from_bytes(data[pointer_at:pointer_at + 3], "little") != start:
        info["warnings"].append("ISN data pointer differs from the reviewed firmware layout.")
        return info
    if family == "MS42":
        raw = data[start:start + 6]
        if raw == b"\xFF" * 6:
            info["isn_status"] = "Not stored"
            return info
    else:
        # Native 0AECA0 majority-selects the descriptor before choosing an ISN slot.
        a, b, c = (_word(data, start + offset) for offset in (0x12, 0x14, 0x16))
        descriptor = a if a in (b, c) else b if b == c else 0xAA55
        offset = {0xFFFF: 0, 0xFEFE: 0, 0xECEC: 6, 0xC8C8: 12}.get(descriptor)
        if offset is None or _word(data, start + offset) == 0xFFFF:
            info["isn_status"] = "Not stored" if descriptor == 0xFFFF else "Invalid ISN data"
            return info
        raw = data[start + offset:start + offset + 6]
    restored, recovered = _isn_words(raw)
    info.update(restored)
    if recovered:
        info["warnings"].append("ISN recovered from consistent redundant words in the image.")
    return info
