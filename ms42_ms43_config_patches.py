"""Program options composed with stock configuration.

These options change program and calibration together; stock settings stay data-only.
"""

from dataclasses import dataclass
from hashlib import sha256

import ms42_ms43_config as stock
from ms42_ms43_image import FULL_SIZE, extract_calibration, image_identity


@dataclass(frozen=True)
class PatchSetting:
    name: str
    entries: tuple
    note: str
    options = (("Disabled", 0), ("Enabled", 1))
    full_file_only = True

    def state(self, read):
        states = []
        for offset, original, patched in self.entries:
            value = read(offset, len(original))
            if value not in (original, patched):
                return "Custom / unsupported bytes"
            if original != patched:
                states.append(value == patched)
        return "Enabled" if all(states) else "Disabled" if not any(states) else "Mixed — select to repair"


def _entry(offset, original, patched):
    return offset, bytes.fromhex(original), bytes.fromhex(patched)


_IMMOBILIZER = (
    "Bypasses ECU immobilizer authorization, including the matching fault-code correction. "
    "Does not clear VIN, ISN or pairing data. Requires program and calibration writing.")
_CHECKSUM = (
    "Bypasses calibration checksum checks only. Program and boot checksums still apply. "
    "Requires paired program/calibration changes; later tune files must retain this setting.")
_PATCHES = {
    ("MS42", "0110C6"): (
        PatchSetting("Immobilizer bypass", (
            _entry(0x283BA, "889088608A0E02F0EA00", "DA079C5BFE0E2E0FDB00"),
            _entry(0x481A4, "02", "00"),
            _entry(0x29E8A, "C2FEE901", "C2FEA401")), _IMMOBILIZER),
        PatchSetting("Calibration checksum bypass", (
            _entry(0x4FEFE, "0000", "FFFF"),
            _entry(0x5002C, "00", "A8")), _CHECKSUM),
    ),
    ("MS43", "430069"): (
        PatchSetting("Immobilizer bypass", (
            _entry(0x600D8, "DA0A6CDD", "DA0DF83B"),
            _entry(0x53BF8, "0000FFFFFFFFFFFF", "DA0AE4FC0E1ADB00"),
            _entry(0x70A6E, "02", "00"),
            _entry(0x2E2F0, "C2FEB90A", "C2FE6E0A")), _IMMOBILIZER),
        PatchSetting("Calibration checksum bypass", (
            _entry(0x70E07, "01", "A5"),
            _entry(0x6FFB0, "00", "A8"),
            _entry(0x63595, "84", "94"),
            _entry(0x73FFE, "0000", "FFFF")), _CHECKSUM),
    ),
}

_PATCHES["MS42", "0110C7"] = _PATCHES["MS42", "0110C6"]
_PATCHES["MS42", "0110CA"] = (
    PatchSetting("Immobilizer bypass", (
        _entry(0x283BA, "889088608A0F0210EA00", "DA079C5B1E0F4E0FDB00"),
        _entry(0x481A4, "02", "00"),
        _entry(0x29E8A, "C2FEE901", "C2FEA401")), _IMMOBILIZER),
    _PATCHES["MS42", "0110C6"][1],
)

# Each success sequence and its fault operand come from that program's native
# authorization handler. The short replacement needs no additional code cave.
_MS43_REVISIONS = {
    "430037": dict(entry=0x31C5A, success="6E18DA0BFE39", increment=0x99B,
                   maximum=0x9DF, crc_owner=0x58934, monitor=0xCE3),
    "430055": dict(entry=0x32040, success="5E19DA0BB83F", increment=0xA12,
                   maximum=0xA59, crc_owner=0x5B818, monitor=0xD7C),
    "430064": dict(entry=0x321CA, success="EE19DA0B4241", increment=0xA26,
                   maximum=0xA6D, crc_owner=0x5FE4E, monitor=0xDA0),
    "430065": dict(entry=0x30000, success="4E1ADA0B781F", increment=0xA75,
                   maximum=0xABF, crc_owner=0x616D0, monitor=0xE09, permission=0xA8),
    "430066": dict(entry=0x2DD64, success="0E1ADA0ADCFC", increment=0xA77,
                   maximum=0xAC1, crc_owner=0x63530, monitor=0xE0A),
}


def _revision_ms43_patches(entry, success, increment, maximum, crc_owner, monitor, permission=0):
    success = bytes.fromhex(success)
    original = bytes.fromhex("887088608A") + bytes((success[1], 2, success[0] & 0xF0))
    return (
        PatchSetting("Immobilizer bypass", (
            (entry, original, success + bytes.fromhex("DB00")),
            _entry(0x70000 + increment, "02", "00"),
            (entry + 0x584, b"\xC2\xFE" + maximum.to_bytes(2, "little"),
             b"\xC2\xFE" + increment.to_bytes(2, "little"))), _IMMOBILIZER),
        PatchSetting("Calibration checksum bypass", (
            _entry(0x70000 + monitor, "01", "A5"),
            _entry(0x6FFB0, f"{permission:02X}", "A8"),
            _entry(crc_owner + 0x65, "94" if permission else "84", "94"),
            _entry(0x73FFE, "0000", "FFFF")), _CHECKSUM),
    )


for _software, _sites in _MS43_REVISIONS.items():
    _PATCHES["MS43", _software] = _revision_ms43_patches(**_sites)


def _checksum_owner_matches(data, family, software):
    """Check the already-loaded full image; no additional ECU reads."""
    if family == "MS42" and software in {"0110C7", "0110CA"}:
        return stock.ms42_crc_owner_matches(data)
    if family != "MS43" or software not in _MS43_REVISIONS:
        return True
    start = _MS43_REVISIONS[software]["crc_owner"]
    owner = bytearray(data[start:start + 0x74])
    if len(owner) != 0x74 or owner[0x65] not in (0x84, 0x94):
        return False
    owner[0x65] = 0x84
    return (sha256(data[0x1628:0x16B4]).hexdigest() ==
            "aa0fbb700ff81fd7d03fca168cdfe69eec95818dbad2d39352b58322445f9759"
            and sha256(owner).hexdigest() ==
            "eaee77f5f2055c5ca1cb222c12a5bbbcd5166763651d3d72b8622561a678de4f")


def patches_for(family, software_id):
    return _PATCHES.get((str(family).upper(), str(software_id).upper()), ())


def features_for(family, software_id):
    patches = patches_for(family, software_id)
    # MS42's existing paired CRC option is the inverse of this same bypass.
    settings = tuple(feature for feature in stock.features_for(family, software_id)
                     if not (patches and str(family).upper() == "MS42" and feature.name == "Calibration CRC"))
    return (*settings, *patches)


def read_config(data, family=None, software_id=None):
    values = stock.read_config(data, family, software_id)
    identity = image_identity(data, family)
    for patch in patches_for(identity["family"], identity["software_id"]):
        if len(data) != FULL_SIZE:
            value = stock.FULL_IMAGE_REQUIRED
        elif patch.name == "Calibration checksum bypass" and not _checksum_owner_matches(
                data, identity["family"], identity["software_id"]):
            value = "Custom / unsupported bytes"
        else:
            value = patch.state(lambda offset, size: data[offset:offset + size])
        values[patch.name] = value
    return values


def read_live_config(ecu, software_id):
    values = stock.read_live_config(ecu, software_id)

    def read(offset, size):
        address = offset + 0x80000 if ecu.family == "MS43" and 0x10000 <= offset < 0x70000 else offset
        value = ecu.read_memory(address, size)
        if len(value) != size:
            raise ValueError("Short program-configuration response")
        return value

    values.update({patch.name: patch.state(read) for patch in patches_for(ecu.family, software_id)})
    return values


def requires_full_write(selections, current, family, software_id):
    return any(patch.name in selections and selections[patch.name] != current.get(patch.name)
               for patch in patches_for(family, software_id))


def apply_config(data, selections, family=None, software_id=None):
    current = read_config(data, family, software_id)
    identity = image_identity(data, family)
    patches = {patch.name: patch for patch in patches_for(identity["family"], identity["software_id"])}
    stock_changes = {name: value for name, value in selections.items()
                     if name not in patches and value != current.get(name)}
    result = bytearray(stock.apply_config(data, stock_changes, family, software_id))
    for name, patch in patches.items():
        if name not in selections or selections[name] == current[name]:
            continue
        if len(data) != FULL_SIZE:
            raise ValueError(f"{name} requires a full ROM file")
        if name == "Calibration checksum bypass" and not _checksum_owner_matches(
                data, identity["family"], identity["software_id"]):
            raise ValueError(f"{name}: unrecognized checksum handler; no changes applied")
        extract_calibration(data)
        choice = selections[name]
        if choice not in dict(patch.options):
            raise ValueError(f"Invalid choice for {name}: {choice}")
        for offset, original, patched in patch.entries:
            before = data[offset:offset + len(original)]
            if before not in (original, patched):
                raise ValueError(f"{name}: unrecognized bytes at 0x{offset:05X}; no changes applied")
            target = patched if choice == "Enabled" else original
            after = result[offset:offset + len(original)]
            if after != before and after != target:
                raise ValueError(f"{name} conflicts with the selected stock checksum setting")
            result[offset:offset + len(original)] = target
    return bytes(result)
