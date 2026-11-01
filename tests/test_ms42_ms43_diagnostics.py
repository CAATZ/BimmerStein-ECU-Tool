"""MS42/MS43 diagnostic workflows use in-memory transports only."""

import hashlib
import json

import pytest

from ds2 import DS2Error, DS2NegativeResponse
from ms42_ms43 import MS42MS43ECU
import ms42_ms43_diagnostics as diagnostics
from tests.test_ms42_ms43 import Link, reply


IDENT = (b"7500255" + b"01" + b"02" + b"10" + b"04" + b"40" + b"01"
         + b"SIEMENS123" + b"12" + b"34" + b"X12345678")
RETAINED = bytes(range(256)) * 64
ECU_NUMBER = b"87654321"
ISN_SNAPSHOTS = {
    "MS42": bytes.fromhex("CB ED 34 ED CB 12 AA 55"),
    "MS43": bytes.fromhex(
        "AA 55 00 00 00 00 00 00 00 00 01 00 00 00 00 00 00 00 00 00 00 00 CB ED 34 ED CB 12"),
}


def record(code=8, status=0x70, *, shadow=False):
    result = bytes((code, status, 3, 4, 100, 128, 1, 2, 0x12, 0x34))
    return result[:2] + b"\x55" + result[2:] if shadow else result


def aif_record(vin="WBAAM31010FG12345", *, zb=7560406, marker=0):
    alphabet = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    packed = 0
    for char in vin:
        packed = packed * 64 + alphabet.index(char)
    raw = bytearray(b"\xFF" * 46)
    raw[:13] = packed.to_bytes(13, "big")
    raw[13] = marker
    raw[14:17] = b"\xA5\x17\x00"  # BMW recorded date 20.10.23, with no century field.
    raw[17:20] = (7551615).to_bytes(3, "big")
    raw[20:23] = b"\x12\xFF\x00"
    raw[23:27] = (1234567).to_bytes(3, "big") + b"\x00"
    raw[27:31] = zb.to_bytes(3, "big") + b"\x00"
    raw[35:41] = b"TEST1\x00"
    raw[41:44] = b"\x12\x34\x00"
    raw[44:46] = b"\x14\x00"
    return bytes(raw)


def aif_image(*records):
    return b"".join(records) + b"\xFF" * (diagnostics.AIF_SIZE - len(records) * 46)


@pytest.mark.parametrize("family", ["MS42", "MS43"])
def test_stored_and_shadow_use_actual_page_lengths_and_existing_gui_contract(family):
    stored = [record(n) for n in range(1, 14)]
    shadow = [record(n, shadow=True) for n in range(1, 13)]
    def respond(command, args):
        if command == 4 and args == b"\x00":
            return reply(b"\x0D\x00\x00\x12\x44")
        rows = stored if command == 4 else shadow
        size = 12 if command == 4 else 11
        start = args[0] - 1
        return reply(bytes((len(rows),)) + b"".join(rows[start:start + size]))
    link = Link(respond)
    ecu = MS42MS43ECU(link, family, raw_ident=IDENT)
    result = diagnostics.read_fault_memory(ecu)
    assert result.profile == family
    assert len(result.stored) == 13 and len(result.shadow) == 12
    assert [row.raw_record for row in result.stored] == stored
    assert [row.raw_record for row in result.shadow] == shadow
    assert [call[1:] for call in link.calls] == [
        (4, b"\x00"), (4, b"\x01"), (4, b"\x0D"), (0x14, b"\x01"), (0x14, b"\x0C")]
    row = result.stored[0]
    assert row.is_active and row.status_text == "Active"
    assert row.frequency == 3 and row.logistics_counter == 4
    assert row.occurred_hours_ago == pytest.approx(1.6)
    assert row.code_hex == "001" and row.sae_code == "DS2-001"
    assert row.system == "Engine" and row.description
    assert "Raw DTC Records" in diagnostics.format_fault_detail(row)
    assert result.shadow[0].debounce_counter == 0x55
    assert result.shadow[0].operating_hours == pytest.approx(466)


@pytest.mark.parametrize("family", ["MS42", "MS43"])
@pytest.mark.parametrize("padding", [b"", b"\x00"])
def test_empty_memories(family, padding):
    link = Link(lambda command, args: reply(b"\x00" * 5) if args == b"\x00"
                else reply(b"\x00" + padding))
    result = diagnostics.read_fault_memory(MS42MS43ECU(link, family, raw_ident=IDENT))
    assert result.stored == result.shadow == ()


@pytest.mark.parametrize("payload", [b"\x01", b"\x01" + record()[:-1],
                                     b"\x01" + record() * 2, b"\x00\x01"])
def test_malformed_pages_do_not_return_partial_success(payload):
    link = Link(lambda command, args: reply(b"\x00" * 5) if args == b"\x00" else reply(payload))
    with pytest.raises(DS2Error, match="Invalid stored fault page"):
        diagnostics.read_fault_memory(MS42MS43ECU(link, "MS43", raw_ident=IDENT))
    assert [call[1:] for call in link.calls] == [(4, b"\x00"), (4, b"\x01")]


@pytest.mark.parametrize("family", ["MS42", "MS43"])
def test_changing_fault_memory_stops_after_three_complete_read_attempts(family):
    def respond(command, args):
        assert command == 4
        if args == b"\x00":
            return reply(b"\x02" + b"\x00" * 4)
        return reply((b"\x02" if args == b"\x01" else b"\x01") + record())

    link = Link(respond)
    with pytest.raises(DS2Error, match="changed"):
        diagnostics.read_fault_memory(MS42MS43ECU(link, family, raw_ident=IDENT))
    assert [call[1:] for call in link.calls] == [(4, bytes((index,)))
                                              for _ in range(3) for index in (0, 1, 2)]


@pytest.mark.parametrize("family", ["MS42", "MS43"])
@pytest.mark.parametrize("memory", ["stored", "shadow"])
def test_clear_then_changing_count_restarts_read_and_discards_old_pages(family, memory):
    attempts = 0
    changing_command = 4 if memory == "stored" else 0x14

    def respond(command, args):
        nonlocal attempts
        if command == 5:
            assert attempts == 0
            return reply()
        if command == 4 and args == b"\x00":
            attempts += 1
            return reply(b"\x02\x00\x00" + (0x1240 + attempts).to_bytes(2, "big"))
        if attempts == 1 and command == changing_command:
            # Stored count shrinks; shadow count grows. Both require a fresh read.
            count = 2 if args == b"\x01" else (1 if memory == "stored" else 3)
            return reply(bytes((count,)) + record(99, shadow=command == 0x14))
        code = 9 if attempts == 1 else (21 if command == 4 else 22)
        return reply(b"\x01" + record(code, shadow=command == 0x14))

    link = Link(respond)
    ecu = MS42MS43ECU(link, family, raw_ident=IDENT)
    diagnostics.clear_fault_memory(ecu)
    result = diagnostics.read_fault_memory(ecu)
    assert attempts == 2
    assert [row.code for row in result.stored] == [21]
    assert [row.code for row in result.shadow] == [22]
    assert result.stored[0].occurred_hours_ago == pytest.approx(1.4)
    assert sum(command == 5 for _, command, _ in link.calls) == 1
    assert sum(command == 4 and args == b"\x01" for _, command, args in link.calls) == 2


def test_cancelled_fault_read_does_not_restart():
    cancelled = False

    def respond(command, args):
        nonlocal cancelled
        if args == b"\x00":
            return reply(b"\x02" + b"\x00" * 4)
        if args == b"\x02":
            cancelled = True
            return reply(b"\x01" + record())
        return reply(b"\x02" + record())

    def check():
        if cancelled:
            raise RuntimeError("cancelled")

    link = Link(respond)
    link.operation_check = check
    with pytest.raises(RuntimeError, match="cancelled"):
        diagnostics.read_fault_memory(MS42MS43ECU(link, "MS42"))
    assert [call[1:] for call in link.calls] == [(4, b"\x00"), (4, b"\x01"), (4, b"\x02")]


def test_only_preparation_busy_is_retried_and_stays_bounded():
    responses = iter((reply(status=0xA1), reply(b"\x00" * 5), reply(b"\x00"), reply(b"\x00")))
    link = Link(lambda *_: next(responses))
    assert diagnostics.read_fault_memory(MS42MS43ECU(link, "MS42")).stored == ()
    assert [call[2] for call in link.calls[:2]] == [b"\x00", b"\x00"]
    busy = Link(lambda *_: reply(status=0xA1))
    with pytest.raises(DS2Error, match="remained busy"):
        diagnostics.read_fault_memory(MS42MS43ECU(busy, "MS42"))
    assert len(busy.calls) == 32
    rejected = Link(lambda *_: reply(status=0xA2))
    with pytest.raises(DS2NegativeResponse):
        diagnostics.read_fault_memory(MS42MS43ECU(rejected, "MS42"))
    assert len(rejected.calls) == 1


def test_shadow_rejection_is_not_silently_hidden():
    link = Link(lambda command, args: reply(status=0xB0) if command == 0x14
                else reply(b"\x00" * 5 if args == b"\x00" else b"\x00"))
    with pytest.raises(DS2NegativeResponse):
        diagnostics.read_fault_memory(MS42MS43ECU(link, "MS42"))
    assert [call[1:] for call in link.calls] == [(4, b"\x00"), (4, b"\x01"), (0x14, b"\x01")]


def test_clear_uses_only_existing_native_clear():
    link = Link()
    diagnostics.clear_fault_memory(MS42MS43ECU(link, "MS43"))
    assert link.calls == [(0x12, 5, b"")]


def test_family_specific_names_status_and_environment_do_not_reuse_ms41():
    ms42 = diagnostics.decode_fault_record(record(18), "MS42")
    ms43 = diagnostics.decode_fault_record(record(18), "MS43")
    assert ms42.description != ms43.description
    assert ms42.freeze_frame[0].identifier == 1
    assert ms43.freeze_frame[0].identifier == 36
    assert ms42.freeze_frame[0].value == 3200
    assert "Stored after debounce" in ms42.qualifiers
    assert "Debounced" in ms43.qualifiers
    absent = diagnostics.decode_fault_record(record(8, status=0), "MS43")
    assert not absent.is_active and absent.status_text == "Stored"
    unknown = diagnostics.decode_fault_record(record(255), "MS43")
    assert unknown.description == "Unknown MS43 fault code 255"
    assert unknown.freeze_frame == () and unknown.qualifiers == ()


def test_ms43_special_fault_and_diagnostic_index_environment_contracts():
    raw = bytearray(record(48))
    raw[4] = 2
    assert diagnostics.decode_fault_record(raw, "MS43").code == 58
    normal = diagnostics.decode_fault_record(record(8), "MS43", diagnostic_index="11")
    legacy = diagnostics.decode_fault_record(record(8), "MS43", diagnostic_index="10")
    assert normal.freeze_frame[2].identifier == 141
    assert legacy.freeze_frame[2].identifier == 20
    coil = diagnostics.decode_fault_record(record(1), "MS43")
    assert coil.freeze_frame[2].raw == coil.freeze_frame[3].raw == 0x102


@pytest.mark.parametrize("family", ["MS42", "MS43"])
def test_aif_history_honors_per_field_markers_and_keeps_each_record(family):
    first = aif_record(zb=7560406)
    last = bytearray(aif_record(vin="WBAAM31010FG54321", zb=7560408))
    last[13] = 1
    last[16] = 1
    result = diagnostics.decode_aif_history(aif_image(first, last), family)
    assert result["programming_count"] == 2
    assert result["vin"] == "WBAAM31010FG12345"
    assert result["recorded_zb_zusb"] == "7560408"
    assert result["programming_date"] == "20.10.23"
    assert result["recorded_distance_km"] == 12000
    assert result["aif_records"][0]["raw"] == first.hex()
    assert "VIN: WBA" in diagnostics.format_aif_record(result["aif_records"][0])


@pytest.mark.parametrize("family,packed,expected", [
    ("MS42", b"\x79\x00\x00", "15.02.00"),
    ("MS43", b"\x7B\x00\x00", "15.06.00"),
])
def test_oem_aif_dates_match_bmw_display_without_an_invented_century(family, packed, expected):
    # Literal date fields from the exact 0110C6 / 430069 reference images at 3C58 / 3C4E.
    raw = bytearray(aif_record())
    raw[14:17] = packed
    decoded = diagnostics.decode_aif_history(aif_image(raw), family)
    assert decoded["programming_date"] == expected
    assert "Date: " + expected in diagnostics.format_aif_record(decoded["aif_records"][0])
    assert "Programming date: " + expected in diagnostics.format_identity(decoded)
    assert bytes.fromhex(decoded["aif_records"][0]["raw"])[14:17] == packed


@pytest.mark.parametrize("raw", [b"", b"\xFF" * 643,
                                  b"\xFF" * 46 + aif_image(aif_record())[:-46]])
def test_invalid_aif_layouts_are_rejected(raw):
    with pytest.raises(ValueError):
        diagnostics.decode_aif_history(raw, "MS43")


def test_erased_aif_is_empty_and_invalid_date_is_unavailable():
    assert diagnostics.decode_aif_history(aif_image(), "MS43")["programming_count"] == 0
    raw = bytearray(aif_record())
    raw[14:16] = b"\x00\x00"
    assert diagnostics.decode_aif_history(aif_image(raw), "MS43")["programming_date"] is None


def identity_ecu(family, *, broken_aif=False, broken_retained=False):
    hardware = b"11143053" if family == "MS43" else b"11101102"
    hw_address, sw_address = (0xEFFCC, 0xEFFBA) if family == "MS43" else (0x50048, 0x50036)
    aif_address = 0x3C40 if family == "MS43" else 0x3C4A
    image = aif_image(aif_record())
    software, program_pointer, calibration_pointer, driver, signature = diagnostics._INFO_LAYOUTS[family]
    calibration = 0x70010 if family == "MS43" else 0x48010
    program_record = hardware + (b"6901" if family == "MS43" else b"C600")
    calibration_record = b"1114300069010000" if family == "MS43" else b"11101100C6010000"
    def respond(command, args):
        if command == 0:
            return reply(IDENT)
        if command == 0x0D:
            directory = bytearray(63)
            directory[60:63] = aif_address.to_bytes(3, "big")
            return reply(directory)
        if command == 0x0B:
            assert args == b"\x94"
            return reply(b"\x10\x00\x00")
        assert command == 6
        address, count = int.from_bytes(args[:4], "big"), args[4]
        if address == program_pointer:
            return reply(hw_address.to_bytes(4, "little"))
        if address == calibration_pointer:
            return reply(calibration.to_bytes(4, "little"))
        if address == calibration:
            return reply(calibration_record * 3)
        if address == driver:
            return reply(signature)
        if address == hw_address:
            return reply(hardware if count == 8 else program_record * 3)
        if address == sw_address:
            return reply(b"430069" if family == "MS43" else b"0110C6")
        if address == hw_address + 8:
            return reply(b"6901" if family == "MS43" else b"C600")
        if address == 0x3F80:
            assert count == 8
            return reply(ECU_NUMBER)
        if address == (0xE2D0 if family == "MS43" else 0xF906):
            assert count == len(ISN_SNAPSHOTS[family])
            return reply(ISN_SNAPSHOTS[family])
        if 0x4000 <= address < 0x8000:
            if broken_retained:
                return reply(status=0xA2)
            assert address + count <= 0x8000
            return reply(RETAINED[address - 0x4000:address - 0x4000 + count])
        if broken_aif:
            return reply(status=0xA2)
        return reply(image[address - aif_address:address - aif_address + count])
    link = Link(respond)
    return MS42MS43ECU(link, family, raw_ident=IDENT), link


@pytest.mark.parametrize("family", ["MS42", "MS43"])
def test_complete_info_snapshot_and_gui_fields_through_real_backend(family):
    ecu, link = identity_ecu(family)
    progress, logs = [], []
    info = diagnostics.read_snapshot(ecu, progress_fn=lambda *p: progress.append(p), log_fn=logs.append)
    assert info["vin"] == "WBAAM31010FG12345"
    assert info["reported_identifier"] == "7500255"
    assert info["dme_production_serial"] == "12345678"
    assert info["coding_index"] == "02" and info["diagnostic_index"] == "10"
    assert info["aif_address"] == (0x3C40 if family == "MS43" else 0x3C4A)
    assert len(info["aif_raw"]) == 644
    assert info["retained_address"] == 0x4000 and info["retained_raw"] == RETAINED
    assert info["warnings"] == []
    assert [c[1] for c in link.calls].count(0x0D) == 1
    assert all(c[1] in (0, 6, 0x0D, 0x0B) for c in link.calls)
    assert progress[0] == (0, 16385) and progress[-1] == (16385, 16385)
    assert all(first[0] <= second[0] for first, second in zip(progress, progress[1:]))
    assert len(logs) == 1
    text = diagnostics.encode_snapshot(info)
    loaded = diagnostics.decode_snapshot(text, family=family)
    assert loaded["aif_raw"] == info["aif_raw"]
    assert loaded["retained_raw"] == RETAINED
    assert loaded["vin"] == info["vin"]
    assert "not an EEPROM image or complete ECU clone" in diagnostics.format_snapshot(loaded)
    assert "Read source: Stock DS2 command 0x06" in diagnostics.format_snapshot(loaded)
    assert "BMW part number: 7500255" in diagnostics.format_identity(loaded)
    if family == "MS43":
        assert info["program_part_number"] is None
        assert info["program_candidates"] == ["7551615", "7571017"]
    else:
        assert info["program_part_number"] == "7500255"


def test_unreadable_aif_preserves_identification_and_explains_export_limit():
    ecu, _ = identity_ecu("MS43", broken_aif=True)
    info = diagnostics.read_identity(ecu)
    assert info["hardware_id"] == "11143053"
    assert info["reported_identifier"] == "7500255"
    assert "Programming history unavailable" in info["warnings"][0]
    with pytest.raises(ValueError, match="complete identity"):
        diagnostics.encode_snapshot(info)


def test_ecu_info_does_not_read_retained_sector_and_identity_only_snapshot_still_loads():
    ecu, link = identity_ecu("MS43")
    info = diagnostics.read_identity(ecu)
    assert "retained_raw" not in info
    assert not any(0x4000 <= int.from_bytes(args[:4], "big") < 0x8000
                   for _, command, args in link.calls if command == 6)
    loaded = diagnostics.decode_snapshot(diagnostics.encode_snapshot(info))
    assert "Identity/history report; memory backups are read separately" in diagnostics.format_snapshot(loaded)


@pytest.mark.parametrize("family", ["MS42", "MS43"])
def test_offline_snapshot_shows_unfinished_aif_warning_once(family):
    ecu, _ = identity_ecu(family)
    info = diagnostics.read_identity(ecu)
    unfinished = bytearray(aif_record(vin="WBAAM31010FG54321"))
    unfinished[:2] = b"\xFF\xFF"
    info["aif_raw"] = aif_image(aif_record(), unfinished)
    info.update(diagnostics.decode_aif_history(info["aif_raw"], family))
    warning = "AIF record 2 contains an unfinished write"
    info["warnings"].extend(("Identification warning", warning, warning))
    connected_text = diagnostics.format_identity(info)
    assert connected_text.count(warning) == 1
    assert connected_text.count("Identification warning") == 1
    loaded = diagnostics.decode_snapshot(diagnostics.encode_snapshot(info))
    assert loaded["aif_raw"] == info["aif_raw"]
    assert loaded["vin"] == "WBAAM31010FG12345"
    assert diagnostics.format_snapshot(loaded).count(warning) == 1


def test_unreadable_retained_sector_preserves_identity_with_explicit_warning():
    ecu, _ = identity_ecu("MS43", broken_retained=True)
    info = diagnostics.read_snapshot(ecu)
    assert info["vin"] == "WBAAM31010FG12345" and "retained_raw" not in info
    assert "Raw retained flash unavailable" in info["warnings"][0]
    assert "Identity/history report; memory backups are read separately" in diagnostics.format_snapshot(info)


@pytest.mark.parametrize("key,value", [("family", "MS42"), ("aif_address", 123),
                                       ("hardware_id", "11101102"), ("aif_hex", "FF")])
def test_snapshot_integrity_binds_metadata_and_binary(key, value):
    ecu, _ = identity_ecu("MS43")
    data = json.loads(diagnostics.encode_snapshot(diagnostics.read_identity(ecu)))
    data[key] = value
    with pytest.raises(ValueError, match="checksum"):
        diagnostics.decode_snapshot(json.dumps(data))


@pytest.mark.parametrize("text", ['[]', '{}', '{"format":"other","version":1}'])
def test_snapshot_schema_rejects_unknown_formats(text):
    with pytest.raises(ValueError):
        diagnostics.decode_snapshot(text)


def test_snapshot_family_mismatch():
    ecu, _ = identity_ecu("MS43")
    text = diagnostics.encode_snapshot(diagnostics.read_identity(ecu))
    with pytest.raises(ValueError, match="different ECU family"):
        diagnostics.decode_snapshot(text, family="MS42")


@pytest.mark.parametrize("key,value", [("retained_hex", "FF"), ("retained_address", 0x8000),
                                       ("retained_provenance", "RAM mirror"), ("raw_ident", None),
                                       ("hardware_id", {}), ("aif_address", "3C40"),
                                       ("program_reference", [])])
def test_malformed_snapshot_fields_are_rejected_even_with_recalculated_digest(key, value):
    ecu, _ = identity_ecu("MS43")
    data = json.loads(diagnostics.encode_snapshot(diagnostics.read_snapshot(ecu)))
    data.pop("sha256")
    data[key] = value
    data["sha256"] = hashlib.sha256(json.dumps(
        data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    with pytest.raises(ValueError):
        diagnostics.decode_snapshot(json.dumps(data))


@pytest.mark.parametrize("family", ["MS42", "MS43"])
def test_learned_config_uses_payload_not_checksum_or_length(family):
    ident = IDENT[:11] + b"11" + IDENT[13:]
    payload = b"\x14\x12\x22" + (b"\x21" if family == "MS43" else b"")
    link = Link(lambda command, args: reply(payload))
    result = diagnostics.read_learned_config(MS42MS43ECU(link, family, raw_ident=ident))
    assert result["Electric fan"] and result["Air conditioning"] and result["ASC"]
    assert not result["Exhaust flap"]
    assert result["Catalytic converter"]
    assert link.calls == [(0x12, 0x0B, b"\x94")]
    if family == "MS43":
        assert "Automatic transmission" not in result
        assert result["Pencil ignition coils"] and result["DMTL heater"]
    else:
        assert "Leak detection pump" not in result and "Secondary air pump" not in result
        assert not result["Leak-pump diagnostic counter"]
        assert result["Secondary-air diagnostic counter"]


def test_ms43_legacy_secondary_air_override_is_one_verified_byte():
    link = Link(lambda command, args: reply(b"\x00\x00\x20\x00") if command == 0x0B
                else reply(b"\x00"))
    result = diagnostics.read_learned_config(MS42MS43ECU(link, "MS43", raw_ident=IDENT))
    assert result["Secondary air pump"] is False
    assert link.calls[-1] == (0x12, 6, bytes.fromhex("00 07 09 47 01"))


def test_ms41_is_never_admitted():
    with pytest.raises(ValueError, match="MS42 or MS43"):
        diagnostics.decode_fault_record(record(), "MS41.2")
