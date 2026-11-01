"""MS42/MS43 image layouts and offline checksums.

CRC descriptors have inclusive ends; MS43 monitor sums have exclusive ends.
MS43 partial context is a matching full image or the 40 bytes at ECU 0xEFDB8.
The latter contain no identity: the caller must match ECU HW/SW separately.
"""

from dataclasses import dataclass
from struct import iter_unpack, unpack_from

from checksum import _crc


FULL_SIZE = 0x80000
MS43_MONITOR_CONTEXT_ADDRESS = 0xEFDB8
MS43_MONITOR_CONTEXT_SIZE = 40


@dataclass(frozen=True)
class ImageProfile:
    name: str
    partial_size: int
    calibration_offset: int
    read_regions: tuple
    program_write_regions: tuple
    calibration_write_length: int
    hw_offset: int
    sw_offset: int
    hw_prefix: str


PROFILES = {
    "MS42": ImageProfile(
        "MS42", 0x8000, 0x48000,
        ((0, 0, 0xC000), (0x11000, 0x11000, 0x30000),
         (0x50000, 0x50000, 0x30000), (0x48000, 0x48000, 0x8000)),
        ((0x11000, 0x11000, 0x2F000), (0x5002C, 0x5002C, 0x2FFD4)),
        0x7FF0, 0x50048, 0x50036, "111011"),
    "MS43": ImageProfile(
        "MS43", 0x10000, 0x70000,
        ((0, 0, 0xE000), (0x90000, 0x10000, 0x60000),
         (0x70000, 0x70000, 0x10000)),
        ((0x90000, 0x10000, 0x5FFF0),),
        0xFFEF, 0x6FFCC, 0x6FFBA, "111430"),
}


def image_profile(data):
    """Detect an image using its size and family hardware identifier."""
    matches = []
    for profile in PROFILES.values():
        if len(data) not in (FULL_SIZE, profile.partial_size):
            continue
        offset = profile.hw_offset if len(data) == FULL_SIZE else 0x20
        if data[offset:offset + 6] == profile.hw_prefix.encode("ascii"):
            matches.append(profile)
    return matches[0] if len(matches) == 1 else None


def _profile(data, family=None):
    profile = (family if isinstance(family, ImageProfile) else
               PROFILES.get(str(family).upper()) if family is not None else
               image_profile(data))
    if profile is None or profile not in PROFILES.values():
        raise ValueError("Unrecognized MS42/MS43 image family")
    if len(data) not in (FULL_SIZE, profile.partial_size):
        raise ValueError(f"{profile.name} requires a 512 KiB full image or "
                         f"{profile.partial_size // 1024} KiB calibration")
    if image_profile(data) != profile:
        raise ValueError(f"Image hardware identifier does not identify {profile.name}")
    return profile


def image_identity(data, family=None):
    profile = _profile(data, family)
    partial = len(data) != FULL_SIZE
    hw = 0x20 if partial else profile.hw_offset
    sw = 8 if partial else profile.sw_offset
    try:
        hardware = bytes(data[hw:hw + 8]).decode("ascii")
        software = bytes(data[sw:sw + 6]).decode("ascii")
    except UnicodeDecodeError as exc:
        raise ValueError("Invalid ASCII image identity") from exc
    if not all(0x20 <= ord(c) < 0x7F for c in hardware + software):
        raise ValueError("Invalid ASCII image identity")
    return {"family": profile.name, "hardware_id": hardware,
            "software_id": software, "partial": partial}


def extract_calibration(data):
    """Extract calibration from a full image with matching family/software IDs."""
    if len(data) != FULL_SIZE:
        raise ValueError("Calibration extraction requires a 512 KiB full image")
    profile = _profile(data)
    identity = image_identity(data, profile)
    start = profile.calibration_offset
    partial = bytes(data[start:start + profile.partial_size])
    calibration = image_identity(partial, profile)
    if calibration["software_id"] != identity["software_id"]:
        raise ValueError("Full-image program and calibration software do not match")
    return partial


def merge_calibration(full, partial, *, correct_checksum=True):
    """Merge matching calibration; optionally correct all full-image checksums."""
    extract_calibration(full)
    profile = _profile(full)
    if len(partial) != profile.partial_size:
        raise ValueError(f"{profile.name} merge requires a "
                         f"{profile.partial_size // 1024} KiB calibration")
    identity = image_identity(partial, profile)
    if identity["software_id"] != image_identity(full, profile)["software_id"]:
        raise ValueError("Calibration software does not match full-image program")
    result = bytearray(full)
    start = profile.calibration_offset
    result[start:start + profile.partial_size] = partial
    if correct_checksum:
        return correct_checksums(result, profile)
    return bytes(result), []


def _span(data, offset, length):
    if offset < 0 or length < 0 or offset + length > len(data):
        raise ValueError(f"Image descriptor is out of bounds: 0x{offset:X}+0x{length:X}")
    return memoryview(data)[offset:offset + length]


def _u16(data, offset):
    return unpack_from("<H", _span(data, offset, 2))[0]


def _u32(data, offset):
    return unpack_from("<I", _span(data, offset, 4))[0]


def _address(address, profile, partial):
    if profile.name == "MS43" and 0x90000 <= address < 0xF0000:
        address -= 0x80000
    if partial:
        if not profile.calibration_offset <= address < profile.calibration_offset + profile.partial_size:
            raise ValueError("Calibration checksum descriptor leaves the calibration image")
        address -= profile.calibration_offset
    return address


def _crc_ranges(data, store, seed, ranges):
    _span(data, store, 2)
    value = seed
    for start, end in ranges:
        if end < start:
            raise ValueError("Checksum descriptor has a reversed range")
        if start <= store + 1 and end >= store:
            raise ValueError("Checksum descriptor includes its own stored checksum")
        value = _crc(_span(data, start, end - start + 1), value)
    return store, value, 2


def _boot(data):
    return _crc_ranges(data, 0x3C24, _u16(data, 0x3FE6),
                       [(_u32(data, 0x3C28), _u32(data, 0x3C2C))])


def _descriptor_crc(data, profile, group):
    partial = len(data) != FULL_SIZE
    if profile.name == "MS42":
        if group == "program":
            store = _u32(data, 0x502CE) & 0xFFFFFF
            seed_at = _u32(data, 0x502D2)
        elif partial:
            store, seed_at = 0x7EE0, 0xC
        else:
            store = _u32(data, 0x502F2) & 0xFFFFFF
            seed_at = _u32(data, 0x502F6)
    else:
        if group == "program":
            store = 0x6FDE0
            seed_at = _address(_u32(data, 0x6ED42), profile, False)
        elif partial:
            store, seed_at = 0x3FE0, 0xC
        else:
            store = 0x73FE0
            seed_at = _address(_u32(data, 0x6ED5A), profile, False)
    if group == "cal":
        base = 0 if partial else profile.calibration_offset
        if not base <= store <= base + profile.partial_size - 2:
            raise ValueError("Calibration checksum storage is outside calibration")
    elif not any(start <= store <= start + length - 2
                 for _, start, length in profile.program_write_regions):
        raise ValueError("Program checksum storage is outside the program")
    count = _u16(data, store + 2)
    if count == 0:
        raise ValueError("Checksum descriptor has no ranges")
    _span(data, store + 4, count * 8)
    ranges = [tuple(_address(_u32(data, at + delta), profile, partial)
                    for delta in (0, 4))
              for at in range(store + 4, store + 4 + count * 8, 8)]
    return _crc_ranges(data, store, _u16(data, seed_at), ranges)


def _monitor_context(data, context):
    if len(data) == FULL_SIZE:
        return _span(data, 0x6FDB8, MS43_MONITOR_CONTEXT_SIZE)
    if context is None:
        raise ValueError("MS43 calibration monitor checksum needs matching program context "
                         "(40 bytes from ECU 0xEFDB8 or a full image)")
    if len(context) == FULL_SIZE:
        identity = image_identity(context, "MS43")
        if identity["software_id"] != image_identity(data, "MS43")["software_id"]:
            raise ValueError("MS43 checksum context software does not match calibration")
        return _span(context, 0x6FDB8, MS43_MONITOR_CONTEXT_SIZE)
    if len(context) != MS43_MONITOR_CONTEXT_SIZE:
        raise ValueError("MS43 monitor context must be 40 bytes or a 512 KiB full image")
    return memoryview(context)


def _monitor(data, profile, group, context):
    partial = len(data) != FULL_SIZE
    if group == "program":
        seed = _u32(data, 0x6FDB2)
        descriptors = _span(data, 0x6FDBE, 16)
        store = 0x6FDAE
    else:
        saved = _monitor_context(data, context)
        seed = _u32(saved, 0)
        descriptors = _span(saved, 22, 16)
        store = 0x2FFC if partial else 0x72FFC
    value = seed
    for at in (0, 8):
        start_address = _u32(descriptors, at)
        end_address = _u32(descriptors, at + 4)
        if start_address % 2 or end_address % 2 or end_address < start_address:
            raise ValueError("Monitor checksum descriptor has an invalid word range")
        start = _address(start_address, profile, partial)
        if start_address == end_address:
            _span(data, start, 0)
            continue
        end = _address(end_address - 1, profile, partial) + 1
        if end < start:
            raise ValueError("Monitor checksum descriptor has an invalid word range")
        if start < store + 4 and end > store:
            raise ValueError("Monitor descriptor includes its own stored checksum")
        words = _span(data, start, end - start)
        value += sum(word[0] for word in iter_unpack("<H", words))
    return store, value & 0xFFFFFFFF, 4


def _calculators(data, profile, context):
    if len(data) == FULL_SIZE:
        yield "boot", "Boot CRC16", lambda: _boot(data)
        if profile.name == "MS43":
            yield "program", "Program monitor", lambda: _monitor(data, profile, "program", context)
        yield "program", "Program CRC16", lambda: _descriptor_crc(data, profile, "program")
    if profile.name == "MS43":
        yield "cal", "Calibration monitor", lambda: _monitor(data, profile, "cal", context)
    yield "cal", "Calibration CRC16", lambda: _descriptor_crc(data, profile, "cal")


def correct_checksums(data, family, context=None):
    """Return a corrected copy; refuse malformed descriptors or missing context."""
    profile = _profile(data, family)
    result = bytearray(data)
    details = []
    for _, label, calculate in _calculators(result, profile, context):
        store, value, size = calculate()
        before = int.from_bytes(_span(result, store, size), "little")
        result[store:store + size] = value.to_bytes(size, "little")
        details.append(f"{label}: {before:0{size * 2}X} -> {value:0{size * 2}X}")
    return bytes(result), details


def checksum_status(data, family=None, context=None):
    """Return boot/program/cal validity; None means unavailable, never success."""
    status = {"boot": None, "program": None, "cal": None, "details": []}
    try:
        profile = _profile(data, family)
    except ValueError as exc:
        status["details"].append(str(exc))
        return status
    status["family"] = profile.name
    results = {group: [] for group in ("boot", "program", "cal")}
    for group, label, calculate in _calculators(data, profile, context):
        try:
            store, value, size = calculate()
            valid = int.from_bytes(_span(data, store, size), "little") == value
            status["details"].append(f"{label}: {'OK' if valid else 'MISMATCH'}")
        except ValueError as exc:
            valid = None
            status["details"].append(f"{label}: unavailable ({exc})")
        results[group].append(valid)
    for group, values in results.items():
        if values:
            status[group] = False if False in values else None if None in values else True
    return status
