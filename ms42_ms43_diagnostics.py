"""BMW MS42/MS43 diagnostic records, identity and retained-data exports.

Wire contracts come from MS420DS0/MS430DS0 and the exact 0110C6/430069
firmware. Nothing here opens a transport or changes the flashing workflow.
"""

from dataclasses import dataclass
from functools import lru_cache
import hashlib
import json

from app_paths import install_root
from checksum import _crc
from ds2 import DS2Error, DS2NegativeResponse
from dtc import DTCEnvironmentValue, format_dtc_table
from ms42_ms43_vin import (AIF_RECORD_COUNT as AIF_RECORD_COUNT,
                          AIF_RECORD_SIZE as AIF_RECORD_SIZE, AIF_SIZE as AIF_SIZE,
                          decode_aif, read_aif)


RETAINED_ADDRESS = 0x4000
RETAINED_SIZE = 0x4000
RETAINED_PROVENANCE = (
    "Stock DS2 command 0x06; unmasked flash addresses 0x004000–0x007FFF")


@lru_cache(maxsize=1)
def _database():
    return json.loads((install_root() / "data/ms42_ms43/diagnostics.json").read_text(
        encoding="utf-8"))


def _profile(family):
    if family not in ("MS42", "MS43"):
        raise ValueError("Expected an MS42 or MS43 ECU")
    return _database()[family]


_QUALIFIER_ENGLISH = {
    "Fehler nach Entprellung gespeichert": "Stored after debounce",
    "Fehler momentan vorhanden": "Currently present",
    "Fehler momentan nicht vorhanden": "Currently not present",
    "statischer Fehler": "Static",
    "sporadischer Fehler": "Sporadic",
    "CARB-Fehler": "Emissions relevant",
    "Fehler ist nicht abgasrelevant": "Not emissions relevant",
    "Fehler nicht abgasrelevant": "Not emissions relevant",
    "Fehler ist abgasrelevant": "Emissions relevant",
    "Fehler nicht entprellt": "Not debounced",
    "Fehler entprellt": "Debounced",
}


@dataclass(frozen=True)
class FaultRecord:
    family: str
    code: int
    status_raw: int
    raw_record: bytes
    memory: str
    description: str
    qualifiers: tuple
    freeze_frame: tuple
    frequency: int
    logistics_counter: int
    occurred_hours_ago: float | None = None
    operating_hours: float | None = None
    debounce_counter: int | None = None
    reported_total: int | None = None
    system: str = "Engine"
    self_test_reason: str | None = None

    @property
    def code_hex(self):
        return f"{self.code:03d}"

    @property
    def sae_code(self):
        return f"DS2-{self.code:03d}"

    @property
    def is_active(self):
        return "Currently present" in self.qualifiers

    @property
    def status_text(self):
        if self.is_active:
            return "Active"
        if "Currently not present" in self.qualifiers:
            return "Stored"
        return f"Raw 0x{self.status_raw:02X}"


@dataclass(frozen=True)
class FaultMemory:
    profile: str
    stored: tuple
    shadow: tuple


def decode_fault_record(raw, family, *, memory="stored", current_counter=None,
                        diagnostic_index=None, reported_total=None):
    """Decode a single BMW record; keep unrecognised codes and source bytes."""
    profile = _profile(family)
    if memory not in ("stored", "shadow"):
        raise ValueError("Unknown fault memory")
    raw = bytes(raw)
    shift = int(memory == "shadow")
    if len(raw) != 10 + shift:
        raise ValueError("Truncated or oversized fault record")
    code, status = raw[:2]
    if family == "MS43" and memory == "stored" and code == 0x30:
        code = {1: 0x30, 2: 0x3A, 3: 0x3F, 4: 0x42, 5: 0x43,
                6: 0x46, 7: 0x47}.get(raw[4], code)
    definition = profile["faults"].get(str(code), {})
    qualifiers = []
    for bit, pair in enumerate(definition.get("bits", ())):
        identifier = pair[bool(status & (1 << bit))]
        text = profile["qualifiers"].get(str(identifier), "") if identifier else ""
        if text and text not in ("--", "---"):
            qualifiers.append(profile.get("qualifiers_en", {}).get(
                str(identifier), _QUALIFIER_ENGLISH.get(text, text)))
    identifiers = list(definition.get("environment", ()))
    if family == "MS43" and memory == "stored" and diagnostic_index == "10":
        if code == 0x8E:
            identifiers[2] = 0x8E
        elif code == 0x08:
            identifiers[2] = 0x14
    environment = []
    for index, identifier in enumerate(identifiers):
        value = raw[4 + shift + index]
        if identifier == 0xFF and index == 1:
            identifier = profile["environment_choices"].get(str(code), {}).get(str(value), 0)
        entry = profile["environment"].get(str(identifier)) if identifier else None
        if entry is None:
            continue
        if memory == "stored" and code in (1, 2, 3, 29, 30, 31) and index >= 2:
            value = int.from_bytes(raw[6:8], "big")
        label, unit, factor, offset = entry
        label = profile.get("environment_labels_en", {}).get(str(identifier), label)
        environment.append(DTCEnvironmentValue(
            identifier, label, unit, value, value * factor + offset))
    timestamp = int.from_bytes(raw[8 + shift:10 + shift], "big")
    return FaultRecord(
        family, code, status, raw, memory,
        definition.get("text_en", definition.get("text", f"Unknown {family} fault code {code}")),
        tuple(dict.fromkeys(qualifiers)), tuple(environment), raw[2 + shift], raw[3 + shift],
        ((current_counter - timestamp) & 0xFFFF) * 0.1
        if current_counter is not None and memory == "stored" else None,
        timestamp * 0.1 if memory == "shadow" else None,
        raw[2] if shift else None, reported_total,
    )


def _busy_read(ecu, command, args=b"", **kwargs):
    minimum = kwargs.pop("minimum", 4)
    for _attempt in range(32):
        try:
            frame = ecu._frame(command, args, **kwargs)
            if len(frame) < minimum:
                raise DS2Error(f"Truncated diagnostic response to 0x{command:02X}")
            return frame
        except DS2NegativeResponse as error:
            if error.command != command or error.status != 0xA1:
                raise
    raise DS2Error("Diagnostic preparation remained busy; try again when the ECU is ready")


class _FaultMemoryChanged(DS2Error):
    pass


def _read_fault_pages(ecu, memory, *, current_counter=None, diagnostic_index=None):
    command, size = (0x04, 10) if memory == "stored" else (0x14, 11)
    records = []
    start, total = 1, None
    while start <= 255:
        frame = ecu._frame(command, bytes((start,)))
        if len(frame) < 5:
            raise DS2Error(f"Invalid {memory} fault page at record {start}")
        count, raw = frame[3], frame[4:-1]
        if total is not None and count != total:
            raise _FaultMemoryChanged("Fault memory changed during reading; read it again")
        total = count
        if count == 0 and raw in (b"", b"\x00"):
            return ()
        if len(raw) % size or not raw or start + len(raw) // size - 1 > count:
            raise DS2Error(f"Invalid {memory} fault page at record {start}")
        for offset in range(0, len(raw), size):
            records.append(decode_fault_record(
                raw[offset:offset + size], ecu.family, memory=memory,
                current_counter=current_counter, diagnostic_index=diagnostic_index,
                reported_total=count))
        start += len(raw) // size
        if start > count:
            return tuple(records)
    raise DS2Error("Fault memory pagination exceeded the protocol range")


def read_fault_memory(ecu):
    """Read both BMW fault lists with real pagination and bounded preparation."""
    _profile(ecu.family)
    ident = bytes(getattr(ecu, "raw_ident", b""))
    if ecu.family == "MS43" and len(ident) != 42:
        ident = _busy_read(ecu, 0x00, length=46)[3:-1]
    diagnostic_index = ident[11:13].decode("ascii", "replace")
    for attempt in range(3):
        try:
            header = _busy_read(ecu, 0x04, b"\x00", length=9)
            counter = int.from_bytes(header[6:8], "big")
            stored = _read_fault_pages(ecu, "stored", current_counter=counter,
                                       diagnostic_index=diagnostic_index)
            shadow = _read_fault_pages(ecu, "shadow")
            return FaultMemory(ecu.family, stored, shadow)
        except _FaultMemoryChanged:
            # A new list can reorder records; discard every page from this attempt.
            if attempt == 2:
                raise


def clear_fault_memory(ecu):
    """The native 0x05 handler clears the engine's stored and shadow state."""
    _profile(ecu.family)
    ecu.clear_dtcs()


def format_fault_detail(record):
    text = format_dtc_table([record])
    if record.debounce_counter is not None:
        text += f"\nDebounce counter: {record.debounce_counter}"
    return text


def decode_identification(payload):
    payload = bytes(payload)
    if len(payload) != 42:
        raise ValueError("Truncated BMW identification response")
    fields = {
        "reported_identifier": (3, 7), "bmw_hardware_number": (10, 2),
        "coding_index": (12, 2), "diagnostic_index": (14, 2), "bus_index": (16, 2),
        "manufacturing_week": (18, 2), "manufacturing_year": (20, 2),
        "supplier_number": (22, 10), "software_index": (32, 2), "change_index": (34, 2),
        "dme_production_serial": (37, 8),
    }
    result = {}
    for name, (offset, size) in fields.items():
        raw = payload[offset - 3:offset - 3 + size].rstrip(b"\x00 ")
        result[name] = raw.decode("ascii") if raw and all(32 <= b < 127 for b in raw) else None
    return result


def decode_aif_history(raw, family):
    """Keep every occupied slot and apply each BMW field's validity marker."""
    _profile(family)
    current = decode_aif(raw)
    row = _profile(family)["daten"].get(current.get("recorded_zb_zusb"))
    current["daten_lineage"] = (
        f"Type {row['type_number']}; program {row['hardware_number']} {row['program_index']}; "
        f"calibration {row['software_dataset']}" if row else "Not found in bundled DATEN")
    return current


ISN_READS = {"MS42": ("0110C6", 0xF906, 8), "MS43": ("430069", 0xE2D0, 28)}
_ISN_LAYOUTS = {(family, software): (address, count)
                for family, (software, address, count) in ISN_READS.items()}
_ISN_LAYOUTS.update({
    ("MS42", "0110C7"): (0xF906, 8), ("MS42", "0110CA"): (0xF906, 8),
    ("MS43", "430037"): (0xE2B0, 28), ("MS43", "430055"): (0xE2CE, 28),
    ("MS43", "430064"): (0xE2D0, 28), ("MS43", "430065"): (0xE2D0, 28),
    ("MS43", "430066"): (0xE2D0, 28),
})
ISN_STATES = ("Stored", "Not stored", "Invalid ISN data", "Unavailable")


def decode_isn(raw, family):
    """Decode the original firmware's restored ISN, not EWS synchronization."""
    _profile(family)
    raw = bytes(raw)
    if len(raw) != ISN_READS[family][2]:
        raise ValueError("Incomplete ISN snapshot")
    if family == "MS42":
        data, validity, empty_descriptor = raw[:6], int.from_bytes(raw[6:8], "little"), True
    else:
        data, validity = raw[22:28], int.from_bytes(raw[:2], "little")
        empty_descriptor = raw[10:12] == b"\xFF\xFF"
    first, second, third = (int.from_bytes(data[i:i + 2], "little") for i in (0, 2, 4))
    if validity == 0x55AA:
        if second == first ^ 0x00FF and third == first ^ 0xFF00:
            return {"isn": f"{first ^ 0xFFFF:04X}", "isn_status": "Stored"}
        state = "Invalid ISN data"
    elif validity == 0xFFFF:
        state = "Not stored" if empty_descriptor and data == b"\xFF" * 6 else "Invalid ISN data"
    else:
        state = "Invalid ISN data" if validity == 0xAA55 else "Unavailable"
    return {"isn": None, "isn_status": state}


_INFO_LAYOUTS = {
    # Native redundant-reference descriptors and the original RAM word-program
    # driver's AA/55 command sequence. This describes software, not chip brand.
    "MS42": ("0110C6", 0x502D6, 0x502FA, 0x2F76,
             bytes.fromhex("e6f2aa00dc0bb82ae6f25500dc04b823")),
    "MS43": ("430069", 0xEED46, 0xEED5E, 0x1C64,
             bytes.fromhex("e6f2aa00dc0db82ce6f25500dc0fb82e")),
}


def _identity_copy(raw, size, family):
    """Native pair order; MS42 intentionally does not compare the last byte."""
    if len(raw) != size * 3:
        raise ValueError("Incomplete redundant identity records")
    copies = [raw[index:index + size] for index in range(0, len(raw), size)]
    compared = size - (family == "MS42")
    for first, second in ((0, 1), (0, 2), (1, 2)):
        if copies[first][:compared] == copies[second][:compared]:
            return copies[first]
    return None


def _read_info_details(ecu, info):
    """Only explicit Info/snapshot reads; never part of flash admission."""
    info.update(transmission_mode="Unavailable", program_calibration_match="Unavailable",
                flash_driver="Unavailable")
    try:
        frame = _busy_read(ecu, 0x0B, b"\x94", minimum=5)
        if frame[3] != 0xFF:
            info["transmission_mode"] = "Automatic" if frame[3] & 0x10 else "Manual"
    except (DS2Error, ValueError) as error:
        info["warnings"].append(f"Transmission mode unavailable: {error}")
    software, program_pointer, calibration_pointer, driver, signature = _INFO_LAYOUTS[ecu.family]
    if ((ecu.family, info.get("software_id")) not in _ISN_LAYOUTS
            and not (ecu.family == "MS43" and info.get("software_id") == "43X001")):
        return
    try:
        records = []
        for label, pointer, size in (("Program", program_pointer, 12),
                                     ("Calibration", calibration_pointer, 16)):
            raw = bytes(ecu.read_memory(pointer, 4))
            if len(raw) != 4:
                raise ValueError("Incomplete identity descriptor")
            address = int.from_bytes(raw, "little")
            maximum = 0x80000 if ecu.family == "MS42" else 0xF0000
            if not 0 <= address <= maximum - size * 3:
                raise ValueError(f"{label} identity descriptor is unavailable")
            record = _identity_copy(bytes(ecu.read_memory(address, size * 3)), size, ecu.family)
            if record is None:
                info["program_calibration_match"] = f"Invalid {label.lower()} reference"
                raise ValueError(f"No agreeing {label.lower()} identity copies")
            records.append(record)
        # Boot validators 0011C2 /002126 compare selected reference bytes using
        # FCC0. Equality of software names alone is not this firmware check.
        matches = all(records[0][index] == records[1][index]
                      for index in range(12) if 0xFCC0 & (0x8000 >> index))
        info["program_calibration_match"] = "References agree" if matches else "Reference mismatch"
    except (DS2Error, ValueError) as error:
        info["warnings"].append(f"Program/calibration references unavailable: {error}")
    try:
        raw = bytes(ecu.read_memory(driver, len(signature)))
        info["flash_driver"] = ("AMD-compatible word-program driver" if raw == signature
                                else "Unknown (driver signature differs)")
    except (DS2Error, ValueError) as error:
        info["warnings"].append(f"Flash command-set driver unavailable: {error}")


def read_identity(ecu):
    """On-demand ECU Info: one directory read also supplies the AIF location."""
    _profile(ecu.family)
    info = {"family": ecu.family, "warnings": []}
    try:
        info.update(ecu._identity_fields())
    except (DS2Error, ValueError) as error:
        info["warnings"].append(f"Hardware/software identity unavailable: {error}")
    try:
        shift = 0x80000 if ecu.family == "MS43" else 0
        if info.get("hardware_id"):
            tail = ecu.read_memory(ecu.profile.hw_offset + shift + 8, 4).decode("ascii")
            info["program_reference"] = info["hardware_id"] + tail
            programs = _profile(ecu.family)["programs"].get(info["program_reference"], [])
            info["program_part_number"] = programs[0] if len(programs) == 1 else None
            info["program_candidates"] = programs
    except (DS2Error, ValueError) as error:
        info["warnings"].append(f"Program reference unavailable: {error}")
    try:
        ident = _busy_read(ecu, 0x00, length=46)[3:-1]
        info.update(decode_identification(ident), raw_ident=ident)
    except (DS2Error, ValueError) as error:
        info["warnings"].append(f"BMW identification unavailable: {error}")
    try:
        directory = _busy_read(ecu, 0x0D, minimum=67)
        address, raw = read_aif(ecu, directory)
        info["aif_address"] = address
        info["aif_raw"] = raw
        info.update(decode_aif_history(raw, ecu.family))
        info["warnings"].extend(info["aif_warnings"])
    except (DS2Error, ValueError) as error:
        info["warnings"].append(f"Programming history unavailable: {error}")
    info.update(ecu_number=None, isn=None, isn_status="Unavailable")
    try:
        raw = bytes(ecu.read_memory(0x3F80, 8))
        number = raw.rstrip(b"\x00 ")
        if len(raw) != 8 or not number or any(not 32 <= byte < 127 for byte in number):
            raise ValueError("ECU Number is not an eight-byte ASCII field")
        info["ecu_number"] = number.decode("ascii")
    except (DS2Error, ValueError) as error:
        info["warnings"].append(f"ECU Number unavailable: {error}")
    layout = _ISN_LAYOUTS.get((ecu.family, info.get("software_id")))
    if layout is not None:
        address, count = layout
        try:
            info.update(decode_isn(ecu.read_memory(address, count), ecu.family))
            if info["isn_status"] in ("Invalid ISN data", "Unavailable"):
                info["warnings"].append(f"ISN: {info['isn_status']}")
        except (DS2Error, ValueError) as error:
            info["warnings"].append(f"ISN unavailable: {error}")
    elif ecu.family == "MS43" and info.get("software_id") == "43X001":
        info["warnings"].append("ISN unavailable: 43X001 removes the original pairing handlers")
    else:
        info["warnings"].append("ISN memory layout is not verified for this software")
    _read_info_details(ecu, info)
    return info


def format_aif_record(record):
    labels = (("vin", "VIN"), ("programming_date", "Date"),
              ("recorded_zb_zusb", "ZUSB"), ("recorded_software_number", "Software"),
              ("recorded_authority_number", "Authority"), ("recorded_change_index", "Index"),
              ("programmer", "Programmer"), ("workshop_code", "Workshop"),
              ("recorded_distance_km", "Recorded km"))
    return f"Record {record['aif_record_number']}: " + "; ".join(
        f"{label}: {record[key] if record[key] is not None else 'Unavailable'}"
        for key, label in labels if key in record)


def format_identity(info):
    fields = (
        ("family", "ECU"), ("hardware_id", "Hardware ID"), ("software_id", "Software ID"),
        ("ecu_number", "ECU Number"), ("isn", "ISN"), ("isn_status", "ISN data"),
        ("program_part_number", "DATEN program number"), ("program_reference", "Program reference"),
        ("transmission_mode", "Transmission mode"),
        ("program_calibration_match", "Program/calibration references"),
        ("flash_driver", "Flash command-set driver"),
        ("vin", "VIN"), ("reported_identifier", "BMW part number"),
        ("bmw_hardware_number", "BMW hardware number"), ("coding_index", "Coding index"),
        ("diagnostic_index", "Diagnostic index"), ("bus_index", "Bus index"),
        ("manufacturing_week", "Manufacturing week"), ("manufacturing_year", "Manufacturing year"),
        ("supplier_number", "Supplier number"), ("software_index", "Software index"),
        ("change_index", "Change index"), ("dme_production_serial", "Production number"),
        ("programming_count", "Programming records"), ("recorded_zb_zusb", "Recorded ZUSB"),
        ("programming_date", "Programming date"), ("recorded_software_number", "Recorded software"),
        ("recorded_authority_number", "Recorded authority number"), ("daten_lineage", "BMW DATEN"),
    )
    lines = [f"{label}: {info.get(key) if info.get(key) is not None else 'Unavailable'}"
             for key, label in fields]
    if len(info.get("program_candidates", [])) > 1:
        lines.append("DATEN programs sharing this reference: " + ", ".join(info["program_candidates"]))
    for record in info.get("aif_records", ()):
        lines.append("\nAIF " + format_aif_record(record))
    lines.extend(dict.fromkeys((*info.get("warnings", ()), *info.get("aif_warnings", ()))))
    return "\n".join(lines)


_CONFIG_LABELS = {
    "CONFIG_EGS": "Automatic transmission", "CONFIG_GE": "Gearbox configured",
    "CONFIG_KAT": "Catalytic converter", "CONFIG_2FL": "Dual exhaust path",
    "CONFIG_1FL": "Single exhaust path", "CONFIG_KOMBI": "Instrument cluster",
    "CONFIG_SSP": "SSP configuration", "CONFIG_ELUEFTER": "Electric fan",
    "CONFIG_MIL": "Malfunction indicator lamp", "CONFIG_AKLAPPE": "Exhaust flap",
    "CONFIG_AC": "Air conditioning", "CONFIG_MFL": "Multifunction steering wheel",
    "CONFIG_ASC": "ASC", "CONFIG_SLP": "Secondary air pump", "CONFIG_LDP": "Leak detection pump",
    "CONFIG_REGEL": "Lambda control", "CONFIG_MONITOR": "Lambda monitoring",
    "CONFIG_P_COIL": "Pencil ignition coils", "CONFIG_DMTL_H": "DMTL heater",
}


def read_learned_config(ecu):
    """Read ECU-reported configuration flags, not physical equipment detection."""
    profile = _profile(ecu.family)
    frame = _busy_read(ecu, 0x0B, b"\x94", minimum=7)
    labels = _CONFIG_LABELS
    if ecu.family == "MS42":
        labels = dict(labels, CONFIG_SLP="Secondary-air diagnostic counter",
                      CONFIG_LDP="Leak-pump diagnostic counter")
    result = {labels[name]: (frame[index] & mask) == expected
              for name, (index, mask, expected) in profile["config"].items()
              if index < len(frame) - 1}
    if ecu.family == "MS43":
        ident = bytes(getattr(ecu, "raw_ident", b""))
        if len(ident) != 42:
            ident = _busy_read(ecu, 0x00, length=46)[3:-1]
        if ident[11:13] == b"10":
            slp = ecu.read_memory(0x70947, 1)[0]
            if slp in (0, 2):
                result["Secondary air pump"] = slp == 2
    return result


def read_snapshot(ecu, *, force_slow=False, log_fn=None, progress_fn=None):
    """Legacy combined read; the desktop now reads identity and storage separately."""
    if log_fn:
        log_fn("Reading BMW identification, AIF records and retained flash over stock diagnostics.")
    if progress_fn:
        progress_fn(0, RETAINED_SIZE + 1)
    info = read_identity(ecu)
    with ecu.transfer(force_slow=force_slow):
        try:
            callback = ((lambda done, total, label: progress_fn(done + 1, total + 1))
                        if progress_fn else None)
            raw = ecu.read_memory(RETAINED_ADDRESS, RETAINED_SIZE, progress_cb=callback)
            if len(raw) != RETAINED_SIZE:
                raise ValueError("Incomplete retained flash response")
            info.update(retained_address=RETAINED_ADDRESS, retained_raw=raw,
                        retained_provenance=RETAINED_PROVENANCE)
        except (DS2Error, ValueError) as error:
            ecu._check_operation()
            info["warnings"].append(f"Raw retained flash unavailable: {error}")
    if progress_fn:
        progress_fn(RETAINED_SIZE + 1, RETAINED_SIZE + 1)
    return info


_STORAGE_BINDINGS = {
    ("MS42", "0110C6"): b"6577225107", ("MS42", "0110C7"): b"6577225108",
    ("MS42", "0110CA"): b"6577225109",
    ("MS43", "430069"): b"43290010", ("MS43", "43X001"): b"43290010",
    ("MS43", "430055"): b"43241010",
    ("MS43", "430037"): b"43153010", ("MS43", "430064"): b"43270010",
    ("MS43", "430065"): b"43271T10", ("MS43", "430066"): (b"43281010", b"43283010"),
}


def decode_retained_records(raw, family, software):
    """Read-only native journal structure, not adaptation meanings or a restore plan.

    MS42 scanner0764CA and MS43 scanner0CA452 use different length limits.
    Their CRC helpers075DB2/0C9CF8 use reflected A001, seed000F, no complement.
    Sequence agreement does not establish the selected bank or ECU ownership.
    """
    _profile(family)
    if len(raw) != RETAINED_SIZE:
        raise ValueError("Retained flash must be exactly 16 KiB")
    if ((family == "MS42" and (family, software) not in _STORAGE_BINDINGS)
            or (family == "MS43" and software != _INFO_LAYOUTS[family][0])):
        return None
    maximum = 0x3E6 if family == "MS42" else 0x3F2
    records = []
    for offset in range(0, RETAINED_SIZE, 0x400):
        block = raw[offset:offset + 0x400]
        sequence = int.from_bytes(block[:4], "little")
        complement = int.from_bytes(block[0x3F8:0x3FC], "little")
        length = int.from_bytes(block[0x3F6:0x3F8], "little")
        stored = int.from_bytes(block[0x3FE:], "little")
        crc_valid = _crc(block[:length], 0x000F) == stored if 0x10 < length <= maximum else None
        records.append({"address": RETAINED_ADDRESS + offset, "erased": block == b"\xFF" * 0x400,
                        "sequence": sequence, "length": length, "crc_valid": crc_valid,
                        "sequence_valid": (sequence != 0xFFFFFFFF and complement != 0xFFFFFFFF
                                           and sequence ^ complement == 0xFFFFFFFF)})
    return records


# Offsets are payload-relative for MS42, EEPROM-byte-relative for MS43.
# Field tuples are label, offset, width, signed; unlisted bytes stay opaque.
def select_ms42_slot(raw, software="0110C6"):
    """Return the native-selected payload's slot index, or no admitted slot.

    Mirrors 0764CA -> 07684E -> 076354 for the 16 KiB retained area.
    Sequence recovery and fallback are native behavior, not host repair.
    """
    if len(raw) != 0x4000:
        raise ValueError("MS42 retained storage must be 16 KiB")
    binding = _STORAGE_BINDINGS.get(("MS42", software))
    if binding is None:
        return None
    raw = bytes(raw)
    mask = 0xFFFFFFFF
    u32 = lambda offset: int.from_bytes(raw[offset:offset + 4], "little")
    previous_header, previous_footer = u32(0x3C00), u32(0x3FF8)
    selected, maximum = None, 0
    valid_crc = []
    for index in range(16):
        base = index * 0x400
        slot = raw[base:base + 0x400]
        if slot == b"\xFF" * 0x400:
            continue
        length = int.from_bytes(slot[0x3F6:0x3F8], "little")
        good = (0x10 < length < 999 and _crc(slot[:length], 0xF)
                == int.from_bytes(slot[0x3FE:0x400], "little"))
        if good:
            valid_crc.append(index)
        header, footer = u32(base), u32(base + 0x3F8)
        if header == mask or footer == mask:
            continue
        complement = footer ^ mask
        next_header = (previous_header + 1) & mask
        next_footer = ((previous_footer - 1) & mask) ^ mask
        sequence = None
        if header == complement or header in (next_header, next_footer):
            sequence = header
        elif complement in (next_header, next_footer):
            sequence = complement
        if good and sequence is not None and sequence > maximum:
            maximum, selected = sequence, index
        previous_header, previous_footer = header, footer
    if selected is None and valid_crc:
        first_bank = [index for index in valid_crc if index < 8]
        selected = (first_bank or valid_crc)[-1]
    if selected is None:
        return None
    start = selected * 0x400 + 4
    if raw[start:start + 10] != binding:
        return None
    return selected


_OBD_OXYGEN_FIELDS = tuple(
    (f"Sensor {sensor:02X}, test {test:02X} {meaning} (raw)", bank * 14 + offset, width, False)
    for bank, sensor in enumerate((1, 0x10))
    for test, meaning, offset, width in (
        (3, "result", 13, 1), (4, "result", 12, 1),
        (5, "result", 6, 2), (5, "limit", 10, 2),
        (6, "result", 4, 2), (6, "limit", 8, 2),
        (7, "result", 0, 2), (7, "limit", 2, 2)))


def _obd_result_fields(family, *, legacy_ms43=False):
    """Stored OBD06 result/limit locations; IDs name native responses, not guessed units."""
    fields = []

    def pair(test, component, result, limit, width=2):
        for meaning, offset in (("result", result), ("limit", limit)):
            fields.append((f"Test {test:02X}, component {component} {meaning} (raw)", offset, width, False))

    stride = 32 if family == "MS42" else 36 if legacy_ms43 else 40
    for bank in range(2):
        base = bank * stride
        pair(1, f"{bank + 1:02X}", base, base + 2)
        for item in range(4):
            pair(2, f"{bank * 4 + item + 1:02X}", base + 4 + item * 2, base + 12 + item * 2)
        pair(3, f"{0x85 + bank:02X}", base + 20, base + 22)
        for item in range(2):
            component = bank + 1 + item * 2
            name = f"{component:02X}" if family == "MS42" else f"{component:02X}/{component | 0x80:02X}"
            pair(6, name, base + 24 + item * 2, base + 28 + item * 2)
        if family == "MS43":
            if legacy_ms43:
                pair(2, f"{0x89 + bank:02X}", base + 32, base + 34)
            else:
                pair(2, f"{0x30 + bank * 2:02X}", base + 32, base + 36)
                pair(2, f"{0xB1 + bank * 2:02X}", base + 34, base + 38)
    tail = ((0x82, 0), (0x87, 4), (0x88, 8), (9, 12)) if family == "MS42" else (
        (0x87, 28), (0x88, 32), (9, 36), (0x92, 0), (0x13, 4), (0x14, 8),
        (0x95, 12), (0x96, 16), (0x17, 20), (0x98, 24))
    for component, offset in tail:
        pair(5, f"{component:02X}", stride * 2 + offset, stride * 2 + offset + 2,
             1 if family == "MS43" and component == 0x17 else 2)
    return tuple(fields)


def _monitor_history_fields(family, *, short_ms43=False):
    count = 10 if family == "MS42" or short_ms43 else 11
    fields = [(f"Retained monitor bitmap word {index + 1} (raw)", index * 2, 2, False)
              for index in range(count)]
    if family == "MS42":
        fields.extend((("Snapshot owner (internal fault ID; FF = unassigned)", 0xB7, 1, False),
                       ("Snapshot flags (raw)", 0xB9, 1, False),
                       ("Diagnostic duration counter (raw)", 0xBA, 2, False),
                       ("Diagnostic-cycle count", 0xBC, 1, False)))
    else:
        owner, flags, cycles = (0xB3, 0xB5, 0xB6) if short_ms43 else (0xBD, 0xBF, 0xC0)
        fields.extend((("Retained monitor word (raw)", count * 2, 2, False),
                       ("Snapshot owner (internal fault ID; FF = unassigned)", owner, 1, False),
                       ("Snapshot flags (raw)", flags, 1, False)))
        fields.extend((f"Retained cycle byte {index + 1} (raw)", cycles + index, 1, False)
                      for index in range(7))
    return tuple(fields)


_MS42_STORAGE_RECORDS = (
    ("Averaged knock adaptation", 0x18, 0x42,
     tuple((f"Averaged knock cell {i + 1} (raw)", i, 1, False) for i in range(64))
     + (("Retained knock word (raw)", 64, 2, False),)),
    ("Start airflow history", 0x5C, 4,
     (("Saved airflow history (raw)", 0, 2, False),)),
    ("Pedal/throttle endpoints", 0x62, 12,
     (("Throttle endpoint 1 (raw)", 0, 2, False), ("Throttle endpoint 2 (raw)", 2, 2, False),
      ("Pedal endpoint 1 (raw)", 4, 2, False), ("Pedal endpoint 2 (raw)", 6, 2, False),
      ("Learned high byte 1 (raw)", 8, 1, False), ("Learned high byte 2 (raw)", 9, 1, False),
      ("Retained latch (nonzero sets; zero does not clear)", 10, 1, False))),
    ("Adaptation-byte bundle", 0x70, 6,
     tuple((f"Adaptation high byte {i + 1} (raw)", i, 1, False) for i in range(5))),
    ("Fuel adaptation", 0x78, 10,
     (("Common correction (raw)", 0, 2, False),
      ("Bank 1 additive adaptation (raw)", 2, 2, False),
      ("Bank 1 multiplicative adaptation (raw)", 4, 2, False),
      ("Bank 2 additive adaptation (raw)", 6, 2, False),
      ("Bank 2 multiplicative adaptation (raw)", 8, 2, False))),
    ("Saved fault records", 0x84, 0x7E,
     (("Diagnostic state word 1 (raw)", 0x7A, 2, False),
      ("Diagnostic state word 2 (raw)", 0x7C, 2, False))),
    ("Idle corrections", 0x104, 8,
     tuple((f"Stored idle correction {i + 1} (raw)", i * 2, 2, True) for i in range(3))
     + (("Idle-learning byte (raw)", 6, 1, False),)),
    ("Crank/channel retained state", 0x10E, 0x1E,
     tuple((f"Retained channel word {i + 1} (raw)", 16 + i * 2, 2, False) for i in range(6))
     + (("Retained channel latch (nonzero sets)", 28, 1, False),)),
    ("Diagnostic monitor history", 0x12E, 0xBE, _monitor_history_fields("MS42")),
    ("Thermal history", 0x1EE, 2,
     (("History byte 1 (raw)", 0, 1, False), ("History byte 2 (raw)", 1, 1, False))),
    ("Diagnostic monitor bytes", 0x1F2, 8,
     tuple((f"Retained monitor byte {i + 1} (raw)", i, 1, False) for i in range(7))),
    ("Stop-weight accumulator", 0x1FC, 2, (("Accumulated stop weight (raw)", 0, 1, False),)),
    ("VANOS record", 0x200, 20,
     (("VANOS reference 1 (raw)", 0, 2, False), ("VANOS reference 2 (raw)", 2, 2, False),
      ("VANOS adaptation high byte 1 (raw)", 4, 1, False),
      ("VANOS adaptation high byte 2 (raw)", 5, 1, False))
     + tuple((f"VANOS bank {bank + 1} cell {cell + 1} (raw)", 6 + bank * 6 + cell, 1, False)
             for bank in range(2) for cell in range(6))
     + (("VANOS validity marker 1 (1 means valid)", 18, 1, False),
        ("VANOS validity marker 2 (1 means valid)", 19, 1, False))),
    ("Secondary-air warm-restart counter", 0x216, 2,
     (("Warm restarts since sufficient cooling", 0, 1, False),)),
    ("Throttle-servo learned state", 0x21A, 2,
     (("Learned byte (raw)", 0, 1, False),)),
    ("ETCU CAN marker", 0x21E, 2, (("Marker (raw; presence marker is 0x10)", 0, 1, False),)),
    ("ICL CAN marker", 0x222, 2, (("Marker (raw; presence marker is 0x20)", 0, 1, False),)),
    ("ASR CAN marker", 0x226, 2, (("Marker (raw; presence marker is 0x40)", 0, 1, False),)),
    ("Reset-event snapshot", 0x22A, 12,
     (("Reset reason and mode flags (raw)", 0, 1, False),
      ("Reset event count", 1, 1, False),
      ("Engine speed at reset", 2, 1, False),
      ("Supply voltage at reset", 3, 1, False),
      ("Coolant temperature at reset", 4, 1, False),
      ("Retained status word at reset (raw)", 6, 2, False),
      ("Operating-counter snapshot (raw ticks)", 8, 2, False),
      ("Retained state word at reset (raw)", 10, 2, False))),
    ("Purge-flow diagnostic mode", 0x23C, 2, (("Mode (7 retained; other values restore as zero)", 0, 1, False),)),
    ("Thermal/start history", 0x240, 8,
     tuple((f"History byte {i + 1} (raw)", i, 1, False) for i in range(6))
     + (("History word (raw)", 6, 2, False),)),
    ("Paired lambda retained state", 0x24A, 12,
     (("Bank 1 retained lambda state (raw)", 0, 2, False),
      ("Bank 2 retained lambda state (raw)", 2, 2, False),
      ("Bank 1 retained lambda correction (raw)", 4, 2, True),
      ("Bank 2 retained lambda correction (raw)", 6, 2, True),
      ("Bank 1 retained lambda state 2 (raw)", 8, 2, False),
      ("Bank 2 retained lambda state 2 (raw)", 10, 2, False))),
    ("Overrev history", 0x258, 6,
     (("Peak speed code (raw)", 0, 2, False),
      ("Operating-counter snapshot (raw)", 2, 2, False),
      ("Occurrence count", 4, 1, False))),
    ("OBD monitor banks", 0x260, 28, _OBD_OXYGEN_FIELDS),
    ("OBD result blocks", 0x27E, 80, _obd_result_fields("MS42")),
    ("Retained configuration marker 1", 0x2D0, 2, (("Marker (raw)", 0, 1, False),)),
    ("ICL3 retained mode", 0x2D4, 2, (("Mode marker (raw)", 0, 1, False),)),
    ("Retained configuration marker 2", 0x2D8, 2, (("Marker (raw)", 0, 1, False),)),
    ("Purge-flow diagnostic state", 0x2DC, 2,
     (("Diagnostic state byte 1 (raw)", 0, 1, False), ("Diagnostic state byte 2 (raw)", 1, 1, False))),
    ("Monitor-state bundle", 0x2E0, 26,
     tuple((f"Retained monitor word {i + 1} (raw)", i * 2, 2, False) for i in range(13))),
    ("Retained diagnostic bytes", 0x2FC, 4,
     tuple((f"Diagnostic byte {i + 1} (raw)", i, 1, False) for i in range(3))),
)
_MS43_EEPROM_RECORDS = (
    ("Undecoded record", 0x26, 10, ()),
    ("Firmware compatibility binding", 0x32, 8, ()),
    ("Diagnostic identity bytes", 0x3C, 4,
     tuple((f"Diagnostic identity byte {i + 1} (raw)", i, 1, False) for i in range(4))),
    ("Configuration markers", 0x42, 6, ()),
    ("Configuration selector", 0x4A, 2, ()),
    ("Configuration state", 0x4E, 6, ()),
    ("Fault-list record", 0x56, 126,
     (("Diagnostic state word (raw)", 0, 2, False), ("Diagnostic state byte (raw)", 2, 1, False))),
    ("Diagnostic monitor state", 0xD6, 200, _monitor_history_fields("MS43")),
    ("Retained monitor bytes", 0x1A0, 6,
     tuple((f"Monitor byte {i + 1} (raw)", i, 1, False) for i in range(6))),
    ("OBD monitor banks", 0x1A8, 28, _OBD_OXYGEN_FIELDS),
    ("OBD result blocks", 0x1C6, 128, _obd_result_fields("MS43")),
    ("Counter/state bundle", 0x248, 6, ()),
    ("Channel/crank state", 0x250, 42, ()),
    ("Stop-weight accumulator", 0x27C, 2, (("Accumulated stop weight (raw)", 0, 1, False),)),
    ("Idle corrections", 0x280, 8,
     tuple((f"Stored idle correction {i + 1} (raw)", i * 2, 2, True) for i in range(3))
     + (("Idle speed-offset byte (raw)", 6, 1, False),)),
    ("VANOS record", 0x28A, 20, (
        ("VANOS reference 1 (raw)", 0, 2, False),
        ("VANOS reference 2 (raw)", 2, 2, False),
        ("VANOS adaptation high byte 1 (raw)", 4, 1, False),
        ("VANOS adaptation high byte 2 (raw)", 5, 1, False),
    ) + tuple((f"VANOS bank {bank + 1} cell {cell + 1} (raw)",
               6 + bank * 6 + cell, 1, False) for bank in range(2) for cell in range(6)) + (
        ("VANOS validity marker 1 (1 means valid)", 18, 1, False),
        ("VANOS validity marker 2 (1 means valid)", 19, 1, False),
    )),
    ("Knock record", 0x2A0, 68, (
        ("Retained knock word (raw)", 0, 2, False),
        ("Knock validity word (zero means false)", 2, 2, False),
    ) + tuple((f"Averaged knock RPM row {i // 4 + 1}, load column {i % 4 + 1} (raw)",
               4 + i, 1, False) for i in range(64))),
    ("Paired-bank adaptation", 0x2E6, 10,
     (("Common correction (raw)", 0, 2, False),
      ("Bank 1 additive adaptation (raw)", 2, 2, False),
      ("Bank 1 multiplicative adaptation (raw)", 4, 2, False),
      ("Bank 2 additive adaptation (raw)", 6, 2, False),
      ("Bank 2 multiplicative adaptation (raw)", 8, 2, False))),
    ("Post-catalyst correction and catalyst state", 0x2F2, 12,
     (("Bank 1 post-catalyst correction (raw)", 0, 2, True),
      ("Bank 2 post-catalyst correction (raw)", 2, 2, True),
      ("Bank 1 catalyst score (raw)", 4, 2, False),
      ("Bank 2 catalyst score (raw)", 6, 2, False),
      ("Bank 1 score-derived gain (raw)", 8, 2, False),
      ("Bank 2 score-derived gain (raw)", 10, 2, False))),
    ("Ambient-pressure correction", 0x300, 2, (("Pressure correction factor (raw; 32768 = 1)", 0, 2, False),)),
    ("Overrev history", 0x304, 20, tuple((f"Over-rev history word {i + 1} (raw)", i * 2, 2, False)
                 for i in range(5)) + tuple(
        (f"Over-rev history byte {i + 1} (raw)", 10 + i, 1, False) for i in range(4)
    ) + (("Over-rev history word 6 (raw)", 14, 2, False),
         ("Over-rev history word 7 (raw)", 16, 2, False),
         ("Over-rev history byte 5 (raw)", 18, 1, False))),
    ("Stored boolean states", 0x31A, 2, (("Stored state 1 (raw boolean)", 0, 1, False),
            ("Stored state 2 (raw boolean)", 1, 1, False))),
    ("Learned secondary-air configuration", 0x31E, 2, ()),
    ("Adaptation-byte bundle", 0x322, 6, tuple((f"Adaptation high byte {i + 1} (raw)", i, 1, False) for i in range(5))),
    ("Saved coolant temperatures", 0x32A, 2,
     (("Restart coolant snapshot (raw; FF = unavailable)", 0, 1, False),
      ("Last-stop coolant snapshot (raw)", 1, 1, False))),
    ("Distance/operating counters", 0x32E, 24, tuple((f"Operating counter word {i + 1} (raw)", i * 2, 2, False)
                 for i in range(8)) + tuple(
        (f"Operating counter byte {i + 1} (raw)", 16 + i, 1, False) for i in range(4)
    ) + (("Operating counter word 9 (raw)", 20, 2, False),
         ("Operating counter word 10 (raw)", 22, 2, False))),
    ("Throttle record", 0x348, 10, tuple((f"Throttle learned word {i + 1} (raw)", i * 2, 2, False)
                 for i in range(4)) + (("Throttle marker (0x5555 / 0xAAAA)", 8, 2, False),)),
    ("Previous run-duration snapshot", 0x354, 2, (("Previous run-duration counter (raw)", 0, 2, False),)),
    ("Torque record", 0x358, 18, tuple((f"Torque learned word {i + 1} (raw)", i * 2, 2, False)
                 for i in range(8)) + (("Torque learned byte 1 (raw)", 16, 1, False),
                                      ("Torque learned byte 2 (raw)", 17, 1, False))),
    ("Cylinder-factor bundle", 0x36C, 16, (("Retained cylinder-factor word 1 (raw)", 0, 2, False),
            ("Retained cylinder-factor word 2 (raw)", 2, 2, False)) + tuple(
        (f"Cylinder factor {i + 1} (raw)", 4 + i * 2, 2, False) for i in range(6))),
    ("Cycle state", 0x37E, 2,
     (("Cycle state byte 1 (raw)", 0, 1, False), ("Cycle state byte 2 (raw)", 1, 1, False))),
    ("Tank-leak diagnostic state", 0x382, 44, ()),
    ("Monitor-state bundle", 0x3B0, 30, ()),
    ("Retained status bytes", 0x3D0, 2,
     (("Status byte 1 (raw)", 0, 1, False), ("Status byte 2 (raw)", 1, 1, False))),
    ("Battery-charge estimate", 0x3D4, 2, (("Charge estimate (raw, not voltage)", 0, 1, False),)),
    ("Gear-estimation correction", 0x3D8, 2, (("Correction factor (raw; 256 = 1)", 0, 2, False),)),
    ("OBD verification words", 0x3DC, 4, (("OBD verification word 1 (raw)", 0, 2, False),
            ("OBD verification word 2 (raw)", 2, 2, False))),
    ("Ignition-coil type", 0x3E2, 2, (("Type code (1 or 2)", 0, 1, False),)),
    ("Heat-demand latch", 0x3E6, 2, (("Stored latch (raw)", 0, 1, False),)),
    ("Ambient-temperature snapshot", 0x3EA, 2, (("Saved ambient-temperature code (raw)", 0, 1, False),)),
)


_MS43_REVISION_INVENTORIES = {
    # Payload sizes exclude the following CRC16.
    "430037": (
        (0x26, 10), (0x32, 8), (0x3C, 4), (0x42, 4), (0x48, 2), (0x4C, 6),
        (0x54, 126), (0xD4, 190), (0x194, 6), (0x19C, 28), (0x1BA, 120), (0x234, 6),
        (0x23C, 42), (0x268, 2), (0x26C, 8), (0x276, 20), (0x28C, 68), (0x2D2, 10),
        (0x2DE, 12), (0x2EC, 2), (0x2F0, 6), (0x2F8, 2), (0x2FC, 2), (0x300, 6),
        (0x308, 2), (0x30C, 20), (0x322, 10), (0x32E, 2), (0x332, 18), (0x346, 16),
        (0x358, 2), (0x35C, 46), (0x38C, 26), (0x3A8, 2), (0x3AC, 2), (0x3B0, 2),
    ),
    "430064": (
        (0x26, 10), (0x32, 8), (0x3C, 4), (0x42, 6), (0x4A, 2), (0x4E, 6),
        (0x56, 126), (0xD6, 190), (0x196, 6), (0x19E, 28), (0x1BC, 120), (0x236, 6),
        (0x23E, 42), (0x26A, 2), (0x26E, 8), (0x278, 20), (0x28E, 68), (0x2D4, 10),
        (0x2E0, 12), (0x2EE, 2), (0x2F2, 20), (0x308, 2), (0x30C, 2), (0x310, 6),
        (0x318, 2), (0x31C, 24), (0x336, 10), (0x342, 2), (0x346, 18), (0x35A, 16),
        (0x36C, 2), (0x370, 46), (0x3A0, 30), (0x3C0, 2), (0x3C4, 2), (0x3C8, 2),
        (0x3CC, 2), (0x3D0, 2), (0x3D4, 4),
    ),
    "430055": (
        (0x26, 10), (0x32, 8), (0x3C, 4), (0x42, 6), (0x4A, 2), (0x4E, 6),
        (0x56, 126), (0xD6, 190), (0x196, 6), (0x19E, 28), (0x1BC, 120), (0x236, 6),
        (0x23E, 42), (0x26A, 2), (0x26E, 8), (0x278, 20), (0x28E, 68), (0x2D4, 10),
        (0x2E0, 12), (0x2EE, 2), (0x2F2, 20), (0x308, 2), (0x30C, 2), (0x310, 6),
        (0x318, 2), (0x31C, 24), (0x336, 10), (0x342, 2), (0x346, 18), (0x35A, 16),
        (0x36C, 2), (0x370, 46), (0x3A0, 26), (0x3BC, 2), (0x3C0, 2), (0x3C4, 2),
        (0x3C8, 2), (0x3CC, 2), (0x3D0, 4),
    ),
    "430065": (
        (0x26, 10), (0x32, 8), (0x3C, 4), (0x42, 6), (0x4A, 2), (0x4E, 6),
        (0x56, 126), (0xD6, 200), (0x1A0, 6), (0x1A8, 28), (0x1C6, 120), (0x240, 6),
        (0x248, 42), (0x274, 2), (0x278, 8), (0x282, 20), (0x298, 68), (0x2DE, 10),
        (0x2EA, 12), (0x2F8, 2), (0x2FC, 20), (0x312, 2), (0x316, 2), (0x31A, 6),
        (0x322, 2), (0x326, 24), (0x340, 10), (0x34C, 2), (0x350, 18), (0x364, 16),
        (0x376, 2), (0x37A, 46), (0x3AA, 30), (0x3CA, 2), (0x3CE, 2), (0x3D2, 2),
        (0x3D6, 2), (0x3DA, 2), (0x3DE, 2), (0x3E2, 4),
    ),
    "430066": (
        (0x26, 10), (0x32, 8), (0x3C, 4), (0x42, 6), (0x4A, 2), (0x4E, 6),
        (0x56, 126), (0xD6, 200), (0x1A0, 6), (0x1A8, 28), (0x1C6, 128), (0x248, 6),
        (0x250, 42), (0x27C, 2), (0x280, 8), (0x28A, 20), (0x2A0, 68), (0x2E6, 10),
        (0x2F2, 12), (0x300, 2), (0x304, 20), (0x31A, 2), (0x31E, 2), (0x322, 6),
        (0x32A, 2), (0x32E, 24), (0x348, 10), (0x354, 2), (0x358, 18), (0x36C, 16),
        (0x37E, 2), (0x382, 44), (0x3B0, 30), (0x3D0, 2), (0x3D4, 2), (0x3D8, 2),
        (0x3DC, 4), (0x3E2, 2), (0x3E6, 2), (0x3EA, 2),
    ),
}


def storage_record_definitions(family, software):
    from ms42_ms43_storage_units import MS43_STORAGE_RECORD_MAPS

    if family == "MS42":
        if software == "0110CA":
            verified = (0x18, 0x5C, 0x62, 0x70, 0x78, 0x104, 0x1EE, 0x1FC,
                        0x200, 0x216, 0x21A, 0x22A, 0x240, 0x24A, 0x258)
            return tuple(record for record in _MS42_STORAGE_RECORDS if record[1] in verified)
        return _MS42_STORAGE_RECORDS
    if software not in MS43_STORAGE_RECORD_MAPS:
        return _MS43_EEPROM_RECORDS

    inventory = _MS43_REVISION_INVENTORIES[software]
    established = {offset: (name, fields) for name, offset, _, fields in _MS43_EEPROM_RECORDS}
    result = []
    for offset, size in inventory:
        if offset in MS43_STORAGE_RECORD_MAPS[software]:
            counterpart, expected = MS43_STORAGE_RECORD_MAPS[software][offset]
            assert size == expected
            name, fields = established[counterpart]
            if counterpart == 0xD6 and size == 190:
                fields = _monitor_history_fields("MS43", short_ms43=True)
            elif counterpart == 0x1C6 and size == 120:
                fields = _obd_result_fields("MS43", legacy_ms43=True)
            elif software == "430037" and counterpart == 0x304:
                fields = (("Over-rev history word 1 (raw)", 0, 2, False),
                          ("Over-rev history word 2 (raw)", 2, 2, False),
                          ("Over-rev history byte 1 (raw)", 4, 1, False))
            elif software == "430037" and counterpart == 0x32E:
                fields = tuple((f"Operating counter word {i + 1} (raw)", i * 2, 2, False)
                               for i in range(7)) + tuple(
                    (f"Operating counter byte {i + 1} (raw)", 14 + i, 1, False) for i in range(4)
                ) + (("Operating counter word 8 (raw)", 18, 2, False),)
        else:
            name = "Firmware compatibility binding" if offset == 0x32 else f"Undecoded record 0x{offset:04X}"
            fields = ()
        result.append((name, offset, size, fields))
    return tuple(result)


def _inspect_storage_record(raw, base, definition, address_base=0, family=None, software=None):
    from ms42_ms43_storage_units import storage_field_presentation, storage_format_value

    name, relative, size, fields = definition
    offset = base + relative
    payload = raw[offset:offset + size]
    stored = int.from_bytes(raw[offset + size:offset + size + 2], "little")
    erased = raw[offset:offset + size + 2] == b"\xFF" * (size + 2)
    valid = not erased and _crc(payload, 0x000F) == stored
    values = []
    warnings = []
    if valid:
        for label, start, width, signed in fields:
            value = int.from_bytes(payload[start:start + width], "little", signed=signed)
            presentation = storage_field_presentation(family, relative + start, label, software)
            label = presentation.get("label", label)
            if (name in ("Paired lambda retained state", "Post-catalyst correction and catalyst state")
                    and start in (0, 2, 4, 6)
                    and payload[start:start + width] == b"\xFF\xFF"):
                value = "unset (FFFF)"
            elif "scale" in presentation or value in presentation.get("choice_labels", {}):
                value = storage_format_value(presentation, value)
            elif width == 1:
                value = f"0x{value:02X} ({value})"
            values.append((label, value))
        if name in ("Saved fault records", "Fault-list record"):
            ms42 = name == "Saved fault records"
            count = payload[0 if ms42 else 0x7C]
            ids_start, records_start = (1, 11) if ms42 else (4, 14)
            ids = tuple(payload[ids_start:ids_start + min(count, 10)])
            values.extend((("Stored count", count), ("Internal fault IDs", ids)))
            if count > 10:
                warnings.append("Malformed count above 10; native restore can overrun its saved-record slots."
                                if ms42 else "Stored count exceeds ten; native restore uses only the first ten slots.")
            if ms42 and any(identifier >= 147 for identifier in ids):
                warnings.append("Fault ID outside this firmware's 147-entry table.")
            for index, identifier in enumerate(ids):
                saved = payload[records_start + 11 * index:records_start + 11 * (index + 1)]
                values.append((f"Fault slot {index + 1}, internal ID {identifier}",
                               ("active-bit set; " if saved[0] & 0x40 else "active-bit clear; ")
                               + saved.hex(" ").upper()))
                values.append((f"Saved fault {index + 1} counters",
                               f"debounce {saved[1]}; occurrences {saved[2]}; aging {saved[3]}; "
                               f"operating-counter snapshot {int.from_bytes(saved[8:10], 'big')}; "
                               f"diagnostic flags 0x{saved[10]:02X}"))
                values.append((f"Saved fault {index + 1} environment codes (raw)",
                               saved[4:8].hex(" ").upper()))
        if name in ("Knock record", "Averaged knock adaptation") and any(
                value > 128 for value in (payload[4:68] if name == "Knock record" else payload[:64])):
            warnings.append("Cells above 128 are restored as 128 by the firmware; saved bytes are unchanged.")
        if name in ("Diagnostic monitor history", "Diagnostic monitor state"):
            ms42 = name == "Diagnostic monitor history"
            short_ms43 = not ms42 and size == 190
            start = 0x1A if ms42 else 0x16 if short_ms43 else 0x18
            for group, count in enumerate((6, 6, 6, 4), 1):
                for index in range(count):
                    saved = payload[start:start + 6]
                    values.append((f"Diagnostic window group {group}, slot {index + 1}",
                                   ("capture bit set; " if saved[4] & 0x40 else "capture bit clear; ")
                                   + saved.hex(" ").upper()))
                    start += 6
            snapshot_size = 27 if ms42 or short_ms43 else 35
            values.append(("Prioritized snapshot bytes (raw)",
                           payload[start:start + snapshot_size].hex(" ").upper()))
            warnings.append("Window reload depends on current fault IDs and descriptor flags; "
                            "snapshot reload checks its owner and flags. Saved capture bits alone "
                            "do not establish live admission.")
        if name == "Ignition-coil type" and payload[0] not in (1, 2):
            warnings.append("Type code is outside the reviewed 1/2 values.")
    return {"name": name, "address": address_base + offset, "size": size,
            "crc_valid": valid, "erased": erased, "values": values, "warnings": warnings}


def inspect_storage_records(raw, family, software, kind):
    """Inspect known records without selecting an active slot or changing bytes."""
    _profile(family)
    if kind not in ("retained", "eeprom") or (kind == "eeprom" and family != "MS43"):
        raise ValueError("Unsupported storage kind for this ECU family")
    if len(raw) != (0x800 if kind == "eeprom" else RETAINED_SIZE):
        raise ValueError("Storage size does not match the selected layout")
    if ((family, software) not in _STORAGE_BINDINGS
            or family == "MS43" and kind == "retained" and software != "430069"):
        return None
    if kind == "eeprom":
        # X001 replaces the stock synchronization owners, including record 0026.
        return [_inspect_storage_record(raw, 0, definition, family=family, software=software)
                for definition in storage_record_definitions(family, software)
                if software != "43X001" or definition[1] != 0x26]
    if family == "MS43":
        return []  # The flash journal's operational payload is not established.
    records = []
    for slot in decode_retained_records(raw, family, software):
        if slot["erased"]:
            continue
        for definition in storage_record_definitions(family, software):
            record = _inspect_storage_record(raw, slot["address"] - RETAINED_ADDRESS + 0x10,
                                             definition, RETAINED_ADDRESS, family, software)
            record["slot"] = slot
            records.append(record)
    return records


def format_storage_inspection(raw, family, software, kind):
    records = inspect_storage_records(raw, family, software, kind)
    lines = [f"{family} {software} — stored-data inspection",
             "Read only. Saved bytes are not current ECU readings."]
    if records is None:
        return "\n".join(lines + ["Field mapping is not verified for this software; raw bytes remain available."])
    lines.append("CRC checks cover only the listed records, not the whole image or firmware admission.")
    if kind == "retained":
        slots = decode_retained_records(raw, family, software)
        lines.append("Slots are shown individually; the active record is not inferred from its sequence.")
        if family == "MS42":
            selected = select_ms42_slot(raw, software)
            lines.append("No journal slot is admitted by the verified firmware." if selected is None else
                         f"Firmware-admitted slot: 0x{RETAINED_ADDRESS + selected * 0x400:06X}.")
        if family == "MS43":
            lines.extend(("MS43 flash-journal payload and activation remain unverified.",
                          "The separate serial EEPROM is not part of this 16 KiB image."))
        for slot in slots:
            if slot["erased"]:
                continue
            crc = {True: "OK", False: "mismatch", None: "invalid length"}[slot["crc_valid"]]
            lines.append(f"\nSlot 0x{slot['address']:06X}: sequence {slot['sequence']}; whole-slot CRC {crc}; "
                         + ("sequence copies agree" if slot["sequence_valid"] else "sequence copies inconsistent"))
            if family == "MS42":
                offset = slot["address"] - RETAINED_ADDRESS + 0x10
                counter = storage_operating_counter(raw[offset:offset + 6])
                lines.append("Operating-tick counter: " + ("copies rejected; native reload uses zero"
                             if counter is None else f"{counter} (raw; stored copy recovery)"))
            for record in (r for r in records if r.get("slot", {}).get("address") == slot["address"]):
                lines.extend(_format_storage_record(record))
        if all(slot["erased"] for slot in slots):
            lines.append("All sixteen slots are erased.")
    else:
        lines.append("Unlisted bytes and undecoded payloads remain available in the raw view.")
        if family == "MS43" and software in (
                "430037", "430055", "430064", "430065", "430066", "430069"):
            lines.extend(_format_ms43_redundant_storage(raw))
        elif software == "43X001":
            lines.append("X001 bytes 0x000000–0x000031 remain raw: stock synchronization interpretation is not verified.")
            lines.append("The secondary-air record has different restore flags on X001; its payload remains undecoded.")
        elif (family, software) in _STORAGE_BINDINGS:
            counter = storage_operating_counter(raw[:6])
            lines.append("Operating-tick counter: " + ("copies rejected; native reload uses zero"
                         if counter is None else f"{counter} (raw; stored copy recovery)"))
            lines.append("Synchronization bytes 0x000006–0x000031 remain raw for this revision.")
        else:
            lines.append("Counter and synchronization bytes 0x000000–0x000031 remain raw for this revision.")
        end = max(offset + size + 2 for _, offset, size, _ in storage_record_definitions(family, software))
        lines.append(f"Bytes 0x{end:04X}–0x07FF remain unmapped and are not validated here.")
        for record in records:
            lines.extend(_format_storage_record(record))
    return "\n".join(lines)


def storage_operating_counter(raw):
    """MS42 01FD2E / MS43 BDD3C word-copy recovery; None means no accepted pair."""
    first, second, third = (int.from_bytes(raw[offset:offset + 2], "little") for offset in (0, 2, 4))
    if first in ((second - 1) & 0xFFFF, (third - 2) & 0xFFFF):
        return first
    if second == (third - 1) & 0xFFFF:
        return (second - 1) & 0xFFFF
    return None


def _ms43_start_triplet(raw):
    # BDCE4 compares zero-extended bytes; byte-wise modulo comparisons are not equivalent.
    first, second, third = raw[6:9]
    if first in (second - 1, third - 2):
        return first
    if second == third - 1:
        return (second - 1) & 0xFF
    return None


def _ms43_sync_copies(raw, offset):
    copies = [int.from_bytes(raw[offset + start:offset + start + 4], "little") for start in (0, 4, 8)]
    normalized = (copies[0], copies[1] ^ 0x00FFFF00, copies[2] ^ 0xFF0000FF)
    first, second, third = normalized
    value = first if first in (second, third) else second if second == third else None
    return copies, normalized, value


def _format_ms43_redundant_storage(raw):
    """Show stored copies without claiming CRC validity or flash-dependent restore admission."""
    words = [int.from_bytes(raw[offset:offset + 2], "little") for offset in (0, 2, 4)]
    lines = ["0x000000 — Incremented word copies (raw): " + ", ".join(f"0x{value:04X}" for value in words),
             "0x000006 — Incremented byte copies (raw): " + raw[6:9].hex(" ").upper()]
    counter = storage_operating_counter(raw)
    lines.append("Operating-tick counter: " + ("copies rejected; native reload uses zero" if counter is None else
                 f"{counter} (raw; native copy recovery applied)" if counter != words[0] else f"{counter} (raw)"))
    start = _ms43_start_triplet(raw)
    lines.append("Start-state byte triplet: " + ("no recoverable pair" if start is None else
                 f"recoverable value {start} (raw; RAM-copy admission is separate)"))
    for offset, label in ((0xA, "backup"), (0x18, "primary")):
        copies, normalized, value = _ms43_sync_copies(raw, offset)
        lines.append(f"0x{offset:06X} — Immobilizer synchronization {label} (not the flash ISN):")
        lines.append("  Encoded 32-bit copies: " + ", ".join(f"0x{value:08X}" for value in copies))
        lines.append("  All decoded copies agree: " + ("yes" if len(set(normalized)) == 1 else "no"))
        lines.append("  Recoverable stored value: " + ("none" if value is None else f"0x{value:08X}"))
        lines.append(f"  Retained status byte (raw): 0x{raw[offset + 12]:02X}")
    lines.append("The operating counter uses incremented word copies, without a CRC. "
                 "Immobilizer synchronization remains read-only and depends on flash pairing state.")
    return lines


def _format_storage_record(record):
    status = "erased" if record["erased"] else "CRC OK" if record["crc_valid"] else "CRC mismatch"
    lines = [f"0x{record['address']:06X} — {record['name']}: {status}"]
    if record["name"] == "Knock record" and record["crc_valid"]:
        lines.append("  16 RPM rows x 4 load columns; each stored average restores to all six ignition banks.")
        lines.append("  128 = 0 degrees; each raw count is 0.375 degrees. Axes and learned limits require calibration data.")
    lines.extend(f"  {label}: {value}" for label, value in record["values"])
    lines.extend(f"  {warning}" for warning in record["warnings"])
    if record["crc_valid"] and not record["values"]:
        lines.append("  Payload fields are not decoded.")
    return lines


def format_snapshot(info):
    lines = ["BMW identification and AIF programming records"]
    if "retained_raw" in info:
        lines.append("This snapshot is not an EEPROM image or complete ECU clone.")
        label = "MS42 retained-data journal" if info["family"] == "MS42" else "MS43 raw flash banks"
        lines.extend((f"{label}: 16 KiB backup; restoration is a separate explicit storage operation.",
                      "Read source: " + info["retained_provenance"],
                      "Retained flash SHA-256: " + hashlib.sha256(info["retained_raw"]).hexdigest()))
        records = decode_retained_records(info["retained_raw"], info["family"], info.get("software_id"))
        if records is None:
            lines.append("Record layout is not verified for this software.")
        else:
            lines.append("Read-only journal structure (does not identify the active record or decode adaptations):")
            lines.append(f"Erased slots: {sum(record['erased'] for record in records)}/16")
            for record in records:
                if record["erased"]:
                    continue
                crc = {True: "OK", False: "mismatch", None: "invalid stored length"}[record["crc_valid"]]
                marker = "agree" if record["sequence_valid"] else "incomplete / inconsistent"
                lines.append(f"0x{record['address']:06X}: sequence {record['sequence']}; "
                             f"CRC {crc}; sequence markers {marker}")
            if info["family"] == "MS43":
                lines.append("MS43 flash-journal payload and activation are unverified; journal structure does not establish active adaptations.")
                lines.append("MS43's separate serial EEPROM is not included in this flash snapshot.")
    else:
        lines.append("Identity/history report; memory backups are read separately.")
    return "\n".join(lines) + "\n\n" + format_identity(info)


def _validate_retained(address, raw, provenance):
    if (address != RETAINED_ADDRESS or len(raw) != RETAINED_SIZE
            or provenance != RETAINED_PROVENANCE):
        raise ValueError("Invalid retained flash address, length or read provenance")


def encode_snapshot(info):
    _profile(info["family"])
    required = ("aif_raw", "raw_ident", "hardware_id", "software_id", "aif_address")
    if any(key not in info for key in required):
        raise ValueError("A complete identity and AIF read is required to export a snapshot")
    raw = bytes(info["aif_raw"])
    decode_aif_history(raw, info["family"])
    ident = bytes(info["raw_ident"])
    decode_identification(ident)
    data = {
        "format": "BimmerStein MS42/MS43 identity snapshot", "version": 1,
        "family": info["family"], "hardware_id": info["hardware_id"],
        "software_id": info["software_id"], "aif_address": info["aif_address"],
        "program_reference": info.get("program_reference"),
        "ecu_number": info.get("ecu_number"), "isn": info.get("isn"),
        "isn_status": info.get("isn_status"),
        **{key: info.get(key) for key in ("transmission_mode", "program_calibration_match", "flash_driver")},
        "raw_ident": ident.hex(), "aif_hex": raw.hex(),
        "scope": "BMW identification and 14 AIF records only; not a complete ECU clone",
    }
    if "retained_raw" in info:
        retained = bytes(info["retained_raw"])
        _validate_retained(info.get("retained_address"), retained, info.get("retained_provenance"))
        data.update(retained_address=RETAINED_ADDRESS, retained_hex=retained.hex(),
                    retained_provenance=RETAINED_PROVENANCE,
                    scope="BMW identification, 14 AIF records and raw retained flash; not a complete ECU clone")
    data["sha256"] = hashlib.sha256(json.dumps(
        data, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    return json.dumps(data, indent=2) + "\n"


def decode_snapshot(text, *, family=None):
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("Invalid ECU identity snapshot")
    if (data.get("format") != "BimmerStein MS42/MS43 identity snapshot"
            or data.get("version") != 1):
        raise ValueError("Unsupported ECU identity snapshot")
    _profile(data.get("family"))
    if family is not None and data["family"] != family:
        raise ValueError("Identity snapshot belongs to a different ECU family")
    expected = data.pop("sha256", None)
    actual = hashlib.sha256(json.dumps(
        data, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    if actual != expected:
        raise ValueError("Identity snapshot checksum does not match its contents")
    try:
        ident, raw = bytes.fromhex(data["raw_ident"]), bytes.fromhex(data["aif_hex"])
        info = {key: data[key] for key in ("family", "hardware_id", "software_id", "aif_address")}
        if (not all(isinstance(info[key], str) and info[key] for key in ("hardware_id", "software_id"))
                or type(info["aif_address"]) is not int or not 0 <= info["aif_address"] < 0x1000000
                or not isinstance(data.get("program_reference"), (str, type(None)))):
            raise ValueError("Invalid ECU identity metadata")
        number, isn, state = (data.get(key) for key in ("ecu_number", "isn", "isn_status"))
        if (number is not None and (not isinstance(number, str) or not 1 <= len(number) <= 8
                                   or any(not 32 <= ord(char) < 127 for char in number))
                or isn is not None and (not isinstance(isn, str) or len(isn) != 4
                                        or any(char not in "0123456789ABCDEF" for char in isn))
                or state not in (None, *ISN_STATES)):
            raise ValueError("Invalid stored ECU identifiers")
        info.update(ecu_number=number, isn=isn, isn_status=state)
        for key in ("transmission_mode", "program_calibration_match", "flash_driver"):
            value = data.get(key)
            if value is not None and (not isinstance(value, str) or len(value) > 100):
                raise ValueError("Invalid stored ECU Info details")
            info[key] = value
        if any(key in data for key in ("retained_hex", "retained_address", "retained_provenance")):
            retained = bytes.fromhex(data["retained_hex"])
            _validate_retained(data["retained_address"], retained, data["retained_provenance"])
            info.update(retained_raw=retained, retained_address=RETAINED_ADDRESS,
                        retained_provenance=RETAINED_PROVENANCE)
    except (KeyError, TypeError) as error:
        raise ValueError("Incomplete or malformed ECU identity snapshot") from error
    info.update(decode_identification(ident), raw_ident=ident, aif_raw=raw)
    info.update(decode_aif_history(raw, info["family"]))
    info["program_reference"] = data.get("program_reference")
    programs = _profile(info["family"])["programs"].get(info["program_reference"], [])
    info["program_candidates"] = programs
    info["program_part_number"] = programs[0] if len(programs) == 1 else None
    return info
