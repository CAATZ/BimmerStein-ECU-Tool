"""Offline edits of fields consumed by verified MS42/MS43 storage readers.

Raw backup/restore owns hardware operations. This module preserves the snapshot
and every byte outside a requested field and its enclosing native CRCs.
"""

from checksum import _crc
from ms42_ms43_diagnostics import (_MS42_STORAGE_RECORDS, _MS43_EEPROM_RECORDS,
                                  _STORAGE_BINDINGS, inspect_storage_records,
                                  select_ms42_slot, storage_operating_counter,
                                  storage_record_definitions)
from ms42_ms43_storage import _validated, make_snapshot
from ms42_ms43_image import FULL_SIZE, extract_calibration, image_identity
from ms42_ms43_storage_units import storage_field_presentation, MS43_STORAGE_RECORD_MAPS


# Limits and edits use stored integers. Presentation units never change native
# acceptance; tune-dependent fields use a separately supplied calibration.
_LIMITS = {
    "MS42": {**{offset: (-32768, 32767, None) for offset in (0x104, 0x106, 0x108)},
             **{offset: (0, 128, None) for offset in range(0x18, 0x58)},
             0x58: (0, 0xFFFF, None), 0x1FC: (0, 0xFF, None),
             **{offset: (0, 0xFF, None) for offset in (0x216, 0x22B, 0x22C, 0x22D, 0x22E)},
             0x232: (0, 0xFFFF, None),
             **{offset: (0, 0xFFFE, None) for offset in (0x24A, 0x24C)},
             **{offset: (0, 0xFFFF, None) for offset in (0x252, 0x254)}},
    "MS43": {**{offset: (-32768, 32767, None) for offset in (0x280, 0x282, 0x284)},
             0x3E2: (1, 2, (1, 2)), 0x3E6: (0, 1, (0, 1)),
             0x2A0: (0, 0xFFFF, None), 0x2A2: (0, 0xFFFF, (0, 0xFFFF)),
             **{offset: (0, 0xFFFE, None) for offset in (0x2F6, 0x2F8)},
             **{offset: (0, 0xFFFF, None) for offset in (0x2FA, 0x2FC)},
             0x27C: (0, 0xFF, None), 0x3D4: (0, 0xFF, None),
             **{offset: (0, 0xFFFF, None) for offset in (0x300, 0x354, 0x3D8)},
             **{offset: (0, 0xFF, None) for offset in (0x32A, 0x32B, 0x3EA)},
             **{offset: (0, 128, None) for offset in range(0x2A4, 0x2E4)}},
}

# These complete fields reload without numeric clipping; raw limits are not operating recommendations.
for _family, _definitions, _records in (
        ("MS42", _MS42_STORAGE_RECORDS, {0x5C, 0x70, 0x1EE, 0x200, 0x21A, 0x240, 0x258}),
        ("MS43", _MS43_EEPROM_RECORDS, {0x28A, 0x304, 0x31A, 0x322, 0x32E, 0x348, 0x358, 0x36C, 0x3DC})):
    for _group, _relative, _size, _fields in _definitions:
        if _relative in _records:
            for _label, _start, _width, _signed in _fields:
                _LIMITS[_family][_relative + _start] = (0, (1 << (_width * 8)) - 1, None)
_LIMITS["MS42"].update({offset: (0, 1, (0, 1)) for offset in (0x212, 0x213)})
_LIMITS["MS43"].update({offset: (0, 1, (0, 1)) for offset in (0x29C, 0x29D, 0x31A, 0x31B)})
_LIMITS["MS43"][0x350] = (0x5555, 0xAAAA, (0x5555, 0xAAAA))
_LIMITS["MS43"][0x33C] = (0, 0xFFFE, None)  # FFFF marks the native counter unavailable.


def storage_calibration(snapshot, data):
    """Validate a local full/partial image; matching IDs do not prove it is the ECU's current tune."""
    snapshot = _validated(snapshot)
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise ValueError("Expected calibration or full-ROM bytes.")
    identity = image_identity(data, snapshot["family"])
    if identity["software_id"].upper() != snapshot["software_id"].upper():
        raise ValueError("Calibration software does not match the storage backup.")
    if not identity["partial"] and snapshot["hardware_id"] and identity["hardware_id"] != snapshot["hardware_id"]:
        raise ValueError("Calibration hardware ID does not match the storage backup.")
    calibration = extract_calibration(data) if len(data) == FULL_SIZE else bytes(data)
    # Partial headers identify a compatibility family (stock ends in 00), not ECU hardware.
    return calibration


_MS43_CALIBRATION_LIMITS = {
    # minimum/maximum offsets for the idle byte, additive, factor and signed postcat words.
    "430037": ((0xB3B, 0xB3A), (0x890, 0x88E), (0x898, 0x896), (0xD5C, 0xD5A)),
    "430055": ((0xBBA, 0xBB9), (0x8F4, 0x8F2), (0x8FC, 0x8FA), (0xDEE, 0xDEC)),
    "430064": ((0xBD2, 0xBD1), (0x908, 0x906), (0x910, 0x90E), (0xE14, 0xE12)),
    "430065": ((0xC26, 0xC25), (0x94C, 0x94A), (0x954, 0x952), (0xE80, 0xE7E)),
    "430066": ((0xC28, 0xC27), (0x94E, 0x94C), (0x956, 0x954), (0xE84, 0xE82)),
}


def _calibrated_limits(family, calibration, software=None):
    word = lambda offset: int.from_bytes(calibration[offset:offset + 2], "little")
    signed = lambda offset: int.from_bytes(calibration[offset:offset + 2], "little", signed=True)
    if family == "MS42":
        fuel_shift = 4 if software == "0110CA" else 0
        limits = {
            0x10A: (calibration[0x300] + 1, calibration[0x2FF], None),
            0x62: (word(0x6B2), word(0x6B0), None),
            0x64: (word(0x6B2), word(0x6B0), None),
            0x66: (word(0x706), word(0x704), None),
            0x68: (word(0x706), word(0x704), None),
            0x6A: (0, 0xFF, None),
            0x6B: (0, 0xFF, None),
            0x78: (0, 0xFFFE, None),
            **{offset: (word(0x7E0 + fuel_shift), word(0x7DE + fuel_shift), None) for offset in (0x7A, 0x7E)},
            **{offset: (word(0x7E6 + fuel_shift), word(0x7E4 + fuel_shift), None) for offset in (0x7C, 0x80)},
            **{offset: (signed(0xE8), signed(0xE6), None) for offset in (0x24E, 0x250)},
        }
    elif software in _MS43_CALIBRATION_LIMITS:
        idle_bounds, add_bounds, factor_bounds, post_bounds = _MS43_CALIBRATION_LIMITS[software]
        records = {counterpart: actual for actual, (counterpart, _) in MS43_STORAGE_RECORD_MAPS[software].items()}
        idle, fuel, postcat = records[0x280], records[0x2E6], records[0x2F2]
        limits = {
            idle + 6: (calibration[idle_bounds[0]] + 1, calibration[idle_bounds[1]], None),
            fuel: (0, 0xFFFE, None),
            **{fuel + delta: (word(add_bounds[0]), word(add_bounds[1]), None) for delta in (2, 6)},
            **{fuel + delta: (word(factor_bounds[0]), word(factor_bounds[1]), None) for delta in (4, 8)},
            **{postcat + delta: (signed(post_bounds[0]), signed(post_bounds[1]), None) for delta in (0, 2)},
        }
    else:
        limits = {
            0x286: (calibration[0xC21] + 1, calibration[0xC20], None),
            0x2E6: (0, 0xFFFE, None),
            **{offset: (word(0x94C), word(0x94A), None) for offset in (0x2E8, 0x2EC)},
            **{offset: (word(0x954), word(0x952), None) for offset in (0x2EA, 0x2EE)},
            **{offset: (signed(0xE7E), signed(0xE7C), None) for offset in (0x2F2, 0x2F4)},
        }
    # A reversed tune range admits no value for that field, not a failure of other records.
    return {offset: bounds for offset, bounds in limits.items() if bounds[0] <= bounds[1]}


def _valid_record(raw, offset, size):
    record = raw[offset:offset + size + 2]
    return (record != b"\xFF" * (size + 2)
            and _crc(record[:size], 0xF) == int.from_bytes(record[size:], "little"))


def _field_limits(family, software):
    if family == "MS43" and software in MS43_STORAGE_RECORD_MAPS:
        limits = {actual + delta: _LIMITS[family][counterpart + delta]
                  for actual, (counterpart, size) in MS43_STORAGE_RECORD_MAPS[software].items()
                  for delta in range(size) if counterpart + delta in _LIMITS[family]}
        if software == "430037":
            for _, offset, _, fields in storage_record_definitions(family, software):
                if offset in (0x2F0, 0x30C):
                    for _, start, width, _ in fields:
                        limits[offset + start] = (0, (1 << (width * 8)) - 1, None)
            limits[0x31E] = (0, 0xFFFE, None)  # The final retained counter has the FFFF sentinel.
        return limits
    return _LIMITS[family]


def _storage_layout(snapshot):
    family, kind, raw = snapshot["family"], snapshot["kind"], snapshot["data"]
    if (family, snapshot["software_id"]) not in _STORAGE_BINDINGS:
        raise ValueError("Field editing is not verified for this software version.")
    if family == "MS42":
        slot = select_ms42_slot(raw, snapshot["software_id"])
        if slot is None:
            raise ValueError("No MS42 journal record is admitted by the verified firmware.")
        base = slot * 0x400 + 0x10
        definitions = storage_record_definitions(family, snapshot["software_id"])
    else:
        if kind != "eeprom":
            raise ValueError("The MS43 flash-bank payload is unverified; inspect its separate EEPROM instead.")
        bindings = _STORAGE_BINDINGS[family, snapshot["software_id"]]
        bindings = (bindings,) if isinstance(bindings, bytes) else bindings
        if not _valid_record(raw, 0x32, 8) or raw[0x32:0x3A] not in bindings:
            raise ValueError(f"The EEPROM firmware binding fails the verified MS43 {snapshot['software_id']} restore check.")
        base, definitions = 0, storage_record_definitions(family, snapshot["software_id"])
    return base, definitions


def editable_fields(snapshot, *, calibration=None):
    """Return verified fields with valid native record checks, using loaded bytes only."""
    snapshot = _validated(snapshot)
    family, raw = snapshot["family"], snapshot["data"]
    base, definitions = _storage_layout(snapshot)
    tune = storage_calibration(snapshot, calibration) if calibration is not None else None
    tuned_limits = (_calibrated_limits(family, tune, snapshot["software_id"])
                    if tune is not None else {})
    limits_by_offset = {**_field_limits(family, snapshot["software_id"]), **tuned_limits}
    result = []
    if (family == "MS42" or snapshot["software_id"] == "430069"
            or snapshot["software_id"] in MS43_STORAGE_RECORD_MAPS):
        counter = storage_operating_counter(raw[base:base + 6])
        if counter is not None:
            result.append({"offset": base, "label": "Operating-tick counter (raw)",
                           "group": "Retained operating counter", "value": counter,
                           "minimum": 0, "maximum": 0xFFFF, "choices": None,
                           "width": 2, "signed": False, "record_offset": base,
                           "record_size": 6, "calibration_dependent": False,
                           "excluded_values": (), "encoding": "incremented_words"})
    for group, relative, size, fields in definitions:
        record_offset = base + relative
        if not _valid_record(raw, record_offset, size):
            continue
        for label, start, width, signed in fields:
            limits = limits_by_offset.get(relative + start)
            if limits is None:
                continue
            offset = record_offset + start
            minimum, maximum, choices = limits
            result.append({"offset": offset, "label": label, "group": group,
                           "value": int.from_bytes(raw[offset:offset + width], "little", signed=signed),
                           "minimum": minimum, "maximum": maximum, "choices": choices,
                           "width": width, "signed": signed,
                           "record_offset": record_offset, "record_size": size,
                           "calibration_dependent": relative + start in tuned_limits,
                           "excluded_values": (-1,) if signed and group in (
                               "Paired lambda retained state", "Post-catalyst correction and catalyst state") else ()})
            result[-1].update(storage_field_presentation(family, relative + start, label, snapshot["software_id"]))
    return result


def storage_knock_axes(snapshot, calibration):
    """The saved average restores to the runtime table's same RPM-row/load-column order."""
    snapshot = _validated(snapshot)
    _storage_layout(snapshot)
    calibration = storage_calibration(snapshot, calibration)
    rpm, load = (0x90D, 0x91E) if snapshot["family"] == "MS42" else (0x400C, 0x4095)
    if snapshot["family"] == "MS42" and snapshot["software_id"] == "0110CA":
        rpm, load = 0x911, 0x922
    if snapshot["family"] == "MS43" and snapshot["software_id"] == "430037":
        load = 0x408E
    if calibration[rpm - 1] != 16 or calibration[load - 1] != 4:
        raise ValueError("The matching calibration does not contain the verified 16 by 4 knock axes.")
    return {"rpm": [value * 32 for value in calibration[rpm:rpm + 16]],
            "load": [value * 1389 / 255 for value in calibration[load:load + 4]]}


def storage_record_checks(snapshot):
    """Inspect every recognized CRC; repair eligibility belongs only to the admitted layout."""
    snapshot = _validated(snapshot)
    family, raw = snapshot["family"], snapshot["data"]
    records = inspect_storage_records(raw, family, snapshot["software_id"], snapshot["kind"]) or []
    try:
        base, definitions = _storage_layout(snapshot)
    except ValueError as error:
        base, definitions, unavailable = None, (), str(error)
    else:
        unavailable = "This record has no independent verified field-reload contract."
    calibrated_records = {0x62, 0x78} if family == "MS42" else {0x2E6}
    if family == "MS43" and snapshot["software_id"] in MS43_STORAGE_RECORD_MAPS:
        calibrated_records = {actual for actual, (counterpart, _) in
                              MS43_STORAGE_RECORD_MAPS[snapshot["software_id"]].items() if counterpart == 0x2E6}
    limits = _field_limits(family, snapshot["software_id"])
    eligible = {base + relative for _, relative, _, fields in definitions
                if relative in calibrated_records
                or any(relative + start in limits for _, start, _, _ in fields)}
    result = []
    for record in records:
        offset = record["address"] - (0x4000 if snapshot["kind"] == "retained" else 0)
        check = offset + record["size"]
        reason = ("The record is erased." if record["erased"] else
                  "The check already matches." if record["crc_valid"] else
                  "Only the firmware-admitted journal slot can be repaired."
                  if family == "MS42" and base is not None and offset // 0x400 != base // 0x400 else
                  unavailable if offset not in eligible else "")
        result.append(dict(record, offset=offset, check_offset=check,
                           stored_check=int.from_bytes(raw[check:check + 2], "little"),
                           expected_check=_crc(raw[offset:check], 0xF),
                           repairable=not reason, reason=reason))
    return result


def repair_storage_checks(snapshot, record_offsets):
    """Explicitly repair selected known checks, never their payloads or other records."""
    snapshot = _validated(snapshot)
    records = {record["offset"]: record for record in storage_record_checks(snapshot)}
    offsets = tuple(record_offsets)
    if any(type(offset) is not int or offset not in records for offset in offsets):
        raise ValueError("Select a recognized storage record check.")
    selected = [records[offset] for offset in dict.fromkeys(offsets)]
    for record in selected:
        if not record["repairable"]:
            raise ValueError(f"{record['name']}: {record['reason']}")
    if not selected:
        return snapshot
    raw = bytearray(snapshot["data"])
    for record in selected:
        check = record["check_offset"]
        raw[check:check + 2] = record["expected_check"].to_bytes(2, "little")
    if snapshot["family"] == "MS42":
        base = select_ms42_slot(snapshot["data"], snapshot["software_id"]) * 0x400
        length = int.from_bytes(raw[base + 0x3F6:base + 0x3F8], "little")
        raw[base + 0x3FE:base + 0x400] = _crc(raw[base:base + length], 0xF).to_bytes(2, "little")
    return make_snapshot(snapshot["family"], snapshot["kind"], raw,
                         software_id=snapshot["software_id"], hardware_id=snapshot["hardware_id"])


def compare_storage_fields(before, after):
    """Compare admitted decoded fields/checks, retaining every raw byte difference."""
    before, after = _validated(before), _validated(after)
    if len(before["data"]) != len(after["data"]):
        raise ValueError("Storage comparisons require images of the same size.")
    rows = []
    matched = all(before[key] == after[key] for key in ("family", "kind", "software_id"))
    if matched:
        try:
            left_base, definitions = _storage_layout(before)
            right_base, _ = _storage_layout(after)
        except ValueError:
            matched = False
    if matched:
        left = {record["offset"]: record for record in storage_record_checks(before)}
        right = {record["offset"]: record for record in storage_record_checks(after)}
        for _, relative, size, fields in definitions:
            old, new = left.get(left_base + relative), right.get(right_base + relative)
            if old is None or new is None:
                continue
            original, changed = dict(old["values"]), dict(new["values"])
            locations = {storage_field_presentation(after["family"], relative + start, label,
                                                   after["software_id"]).get("label", label):
                         (start, width) for label, start, width, _ in fields}
            for label in dict.fromkeys((*original, *changed)):
                if original.get(label) != changed.get(label):
                    start, width = locations.get(label, (0, size))
                    rows.append(dict(group=new["name"], label=label,
                                     before=str(original.get(label, "Unavailable (record check)")),
                                     after=str(changed.get(label, "Unavailable (record check)")),
                                     offset=new["offset"] + start, before_offset=old["offset"] + start, length=width))
            if (old["stored_check"], old["crc_valid"]) != (new["stored_check"], new["crc_valid"]):
                def display(record):
                    return f"{record['stored_check']:04X} ({'OK' if record['crc_valid'] else 'mismatch'})"
                rows.append(dict(group="Record check", label=new["name"], before=display(old), after=display(new),
                                 offset=new["check_offset"], before_offset=old["check_offset"], length=2))
        old_fields = {field["offset"] - left_base: field for field in editable_fields(before)
                      if field.get("encoding") == "incremented_words"}
        for field in editable_fields(after):
            old = old_fields.get(field["offset"] - right_base)
            if old and old["value"] != field["value"]:
                rows.append(dict(group=field["group"], label=field["label"], before=str(old["value"]),
                                 after=str(field["value"]), offset=field["offset"],
                                 before_offset=old["offset"], length=6))
    for offset, (old, new) in enumerate(zip(before["data"], after["data"])):
        if old != new:
            rows.append(dict(group="Raw byte", label=f"Byte 0x{offset:04X}", before=f"{old:02X}", after=f"{new:02X}",
                             offset=offset, before_offset=offset, length=1))
    return rows


def edit_storage(snapshot, changes, *, calibration=None):
    """Apply an atomic set of field edits to a new snapshot; never repair other records."""
    snapshot = _validated(snapshot)
    if not isinstance(changes, dict):
        raise ValueError("Expected storage field offsets and integer values.")
    if not changes:
        return snapshot
    fields = {field["offset"]: field for field in editable_fields(snapshot, calibration=calibration)}
    raw = bytearray(snapshot["data"])
    affected = {}
    changed_offsets = set()
    for offset, value in changes.items():
        if type(offset) is not int or offset not in fields:
            raise ValueError("This field is not editable in the loaded storage backup.")
        field = fields[offset]
        if type(value) is int and value == field["value"]:
            continue
        if type(value) is not int or not field["minimum"] <= value <= field["maximum"]:
            raise ValueError(f"{field['label']} must be an integer from {field['minimum']} to {field['maximum']}.")
        if field["choices"] is not None and value not in field["choices"]:
            raise ValueError(f"{field['label']} must be one of {field['choices']}.")
        if value in field["excluded_values"]:
            raise ValueError(f"{field['label']}: -1 is the erased sentinel and reloads as zero.")
        if field.get("encoding") == "incremented_words":
            raw[offset:offset + 6] = b"".join(((value + index) & 0xFFFF).to_bytes(2, "little") for index in range(3))
        else:
            raw[offset:offset + field["width"]] = value.to_bytes(field["width"], "little", signed=field["signed"])
            affected[field["record_offset"]] = field["record_size"]
        changed_offsets.add(offset)
    if calibration is not None and changed_offsets:
        _validate_reload(snapshot, raw, changed_offsets, fields)
    for offset, size in affected.items():
        raw[offset + size:offset + size + 2] = _crc(raw[offset:offset + size], 0xF).to_bytes(2, "little")
    if changed_offsets and snapshot["family"] == "MS42":
        base = next(iter(changed_offsets)) // 0x400 * 0x400
        length = int.from_bytes(raw[base + 0x3F6:base + 0x3F8], "little")
        raw[base + 0x3FE:base + 0x400] = _crc(raw[base:base + length], 0xF).to_bytes(2, "little")
    return make_snapshot(snapshot["family"], snapshot["kind"], raw,
                         software_id=snapshot["software_id"], hardware_id=snapshot["hardware_id"])


def _validate_reload(snapshot, raw, changes, fields):
    """Native endpoint admission is collective; bank-1 rejection also resets common fuel correction."""
    base = (select_ms42_slot(snapshot["data"], snapshot["software_id"]) * 0x400 + 0x10
            if snapshot["family"] == "MS42" else 0)
    fuel = 0x78 if snapshot["family"] == "MS42" else 0x2E6
    for actual, (counterpart, _) in MS43_STORAGE_RECORD_MAPS.get(snapshot["software_id"], {}).items():
        if counterpart == 0x2E6:
            fuel = actual
    required = []
    if snapshot["family"] == "MS42" and any(base + offset in changes for offset in (0x62, 0x64, 0x66, 0x68, 0x6A, 0x6B)):
        required.extend(base + offset for offset in (0x62, 0x64, 0x66, 0x68))
    if base + fuel in changes:
        required.append(base + fuel + 2)
    for offset in required:
        field = fields.get(offset)
        if field is None:
            raise ValueError("The loaded calibration has no admissible range for a related field; "
                             "the firmware would discard this edit on reload.")
        value = int.from_bytes(raw[offset:offset + field["width"]], "little", signed=field["signed"])
        if not field["minimum"] <= value <= field["maximum"]:
            raise ValueError(f"{field['label']} is outside the loaded calibration limits; "
                             "correct it in the same draft so the firmware retains the edited values.")
