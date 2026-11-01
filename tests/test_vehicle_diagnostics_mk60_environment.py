"""BMW DSC_MK60 FS_LESEN_DETAIL service 17 and default environment table."""

from hashlib import sha256
from pathlib import Path

import pytest

from ds2 import DS2Error, DS2Timeout
import vehicle_diagnostics as diagnostics
from vehicle_diagnostics import read_module_faults


class ScriptedMK60:
    def __init__(self, *payloads):
        self.payloads = iter(payloads)
        self.calls = []

    def send_bmw_fast(self, body, target, timeout):
        assert target == 0x29 and timeout == 2.0
        self.calls.append(bytes(body))
        payload = next(self.payloads)
        if isinstance(payload, Exception):
            raise payload
        reply = b"\xB8\xF1\x29" + bytes((len(payload),)) + payload
        checksum = 0
        for byte in reply:
            checksum ^= byte
        return reply + bytes((checksum,))


def detail(*snapshots, code=0x5D90, status=0xE1, frequency=1, counter=9):
    return (b"\x57\x01" + code.to_bytes(2, "big") + bytes((status, frequency, counter))
            + b"".join(snapshots))


def read_detail(payload):
    link = ScriptedMK60(bytes.fromhex("58 01 5D 90 E1"), payload)
    [fault] = read_module_faults(link, "dsc_mk60")
    return fault, link


def test_mk60_reads_details_once_per_fault_without_a_session_or_clear():
    payload = detail(bytes.fromhex("12 34 7B 2D 81 09"))
    other = detail(bytes.fromhex("00 02 28 00 00 00"), code=0x5E15, status=0x24)
    link = ScriptedMK60(bytes.fromhex("58 02 5D 90 E1 5E 15 24"), payload, other)
    first, second = read_module_faults(link, "dsc_mk60")
    assert link.calls == [bytes.fromhex(frame) for frame in (
        "B8 29 F1 04 18 02 FF FF", "B8 29 F1 03 17 5D 90", "B8 29 F1 03 17 5E 15")]
    assert first.raw_record == bytes.fromhex("5D 90 E1")
    assert first.environment_raw == payload[1:]
    assert first.frequency == 1 and first.current
    assert "Logistics counter: 9" in first.conditions
    assert "Snapshot 1: mileage 46600 km; vehicle speed 123 km/h" in first.conditions
    assert "Snapshot 1: mileage 20 km; vehicle speed 40 km/h" in second.conditions
    assert second.raw_record == bytes.fromhex("5E 15 24")


@pytest.mark.parametrize("frequency", (0, 1, 2, 3, 255))
@pytest.mark.parametrize("count", (1, 2, 3))
def test_mk60_complete_snapshots_are_not_hidden_by_frequency(frequency, count):
    snapshots = [bytes((0, index, index * 30, 0, 0, 0)) for index in range(1, count + 1)]
    payload = detail(*snapshots, frequency=frequency)
    fault, _ = read_detail(payload)
    assert fault.frequency == frequency
    assert fault.environment_raw == payload[1:]
    assert not any("unavailable" in row for row in fault.conditions)
    assert [row for row in fault.conditions if ": mileage " in row] == [
        f"Snapshot {index}: mileage {index * 10} km; vehicle speed {index * 30} km/h"
        for index in range(1, count + 1)]


@pytest.mark.parametrize("snapshot,expected", (
    (bytes(6), "Snapshot 1: mileage 0 km; vehicle speed 0 km/h"),
    (bytes.fromhex("FF FF FF 00 00 00"),
     "Snapshot 1: mileage 655350 km; vehicle speed 255 km/h"),
))
def test_mk60_mileage_uses_executed_times_ten_not_stale_times_eight_comment(snapshot, expected):
    fault, _ = read_detail(detail(snapshot, frequency=255, counter=255))
    assert expected in fault.conditions
    assert "Logistics counter: 255" in fault.conditions


@pytest.mark.parametrize("mask,label", (
    (0x01, "Brake-light switch"), (0x04, "Brake-fluid switch"),
    (0x08, "ASC/DSC active (button function)"), (0x20, "Brake pressure detected"),
))
def test_mk60_input_bits_use_bmw_masks(mask, label):
    fault, _ = read_detail(detail(bytes((0, 0, 0, mask, 0, 0))))
    row = next(row for row in fault.conditions if " inputs (0/1):" in row)
    assert f"{label}: 1" in row
    assert row.count(": 1") == 1
    assert row.count(": 0") == 3


@pytest.mark.parametrize("high,low,label", (
    (0x01, 0, "ABS control"), (0x02, 0, "ASC control (BMR)"),
    (0x04, 0, "ASC control (AMR)"), (0x08, 0, "GMR control"),
    (0x10, 0, "MMR control"), (0x20, 0, "Cornering brake control (CBC)"),
    (0x40, 0, "Engine drag-torque control (MSR)"), (0x80, 0, "TDR control"),
    (0, 0x01, "SDR control"), (0, 0x02, "Dynamic brake control (DBC)"),
    (0, 0x04, "RTA active"), (0, 0x08, "Run-up mode"),
))
def test_mk60_control_word_is_high_byte_first_and_does_not_drop_low_bits(high, low, label):
    fault, _ = read_detail(detail(bytes((0, 0, 0, 0, high, low))))
    row = next(row for row in fault.conditions if " control states (0/1):" in row)
    assert f"{label}: 1" in row
    assert row.count(": 1") == 1
    assert row.count(": 0") == 11


def test_mk60_unassigned_environment_bits_remain_visible_without_new_meanings():
    fault, _ = read_detail(detail(bytes.fromhex("00 00 00 D2 00 F0")))
    assert "Snapshot 1 unassigned bits: inputs 0xD2; control 0x00F0" in fault.conditions
    assert not any(": 1" in row for row in fault.conditions if "(0/1)" in row)


def test_mk60_detail_status_change_does_not_rewrite_the_fault_list():
    fault, _ = read_detail(detail(bytes(6), status=0x24))
    assert fault.status == 0xE1 and fault.current and fault.state_text == "Current"
    assert fault.raw_record == bytes.fromhex("5D 90 E1")
    assert "Detail status: 0x24 (changed since fault list)" in fault.conditions
    assert fault.environment_raw[3] == 0x24


def test_mk60_default_environment_layout_applies_to_unknown_fault_locations():
    payload = detail(bytes.fromhex("00 01 32 00 00 00"), code=0x1234)
    link = ScriptedMK60(bytes.fromhex("58 01 12 34 E1"), payload)
    [fault] = read_module_faults(link, "dsc_mk60")
    assert fault.description == "Unknown fault location 0x1234"
    assert "Snapshot 1: mileage 10 km; vehicle speed 50 km/h" in fault.conditions


@pytest.mark.parametrize("payload,reason", (
    (b"\x57", "header"), (b"\x57\x00", "no longer in memory"),
    (b"\x57\x01", "header"),
    (bytes.fromhex("57 02 5D 90 E1 01 09") + bytes(6), "header"),
    (detail(bytes(6), code=0x5E15), "does not match"),
    (detail(), "snapshot length"), (detail(bytes(1)), "snapshot length"),
    (detail(bytes(5)), "snapshot length"), (detail(bytes(7)), "snapshot length"),
    (detail(bytes(17)), "snapshot length"), (detail(bytes(19)), "snapshot length"),
    (detail(bytes(24)), "snapshot length"),
    (b"\x7F\x17\x21", "status 0x21"),
    (b"\x58\x00", "requested service"),
    (DS2Timeout("no detail response"), "no detail response"),
    (DS2Error("invalid response checksum"), "invalid response checksum"),
))
def test_mk60_detail_failure_preserves_list_and_does_not_retry(payload, reason):
    fault, link = read_detail(payload)
    assert len(link.calls) == 2
    assert fault.code == 0x5D90 and fault.current
    assert fault.raw_record == bytes.fromhex("5D 90 E1")
    assert fault.frequency is None
    assert not any(row.startswith("Snapshot ") for row in fault.conditions)
    assert reason in fault.conditions[-1]
    assert fault.conditions[-1].startswith("Freeze-frame unavailable:")
    if isinstance(payload, bytes) and payload[:1] == b"\x57":
        assert fault.environment_raw == payload[1:]


def test_mk60_detail_failure_does_not_hide_the_next_fault_details():
    link = ScriptedMK60(bytes.fromhex("58 02 5D 90 E1 5E 15 24"),
                       b"\x7F\x17\x11", detail(bytes(6), code=0x5E15))
    failed, success = read_module_faults(link, "dsc_mk60")
    assert "unavailable" in failed.conditions[-1]
    assert any(row.startswith("Snapshot 1:") for row in success.conditions)
    assert len(link.calls) == 3


def test_mk60_empty_memory_sends_no_detail_request():
    link = ScriptedMK60(b"\x58\x00")
    assert read_module_faults(link, "dsc_mk60") == []
    assert len(link.calls) == 1


def test_mk60_programming_errors_are_not_swallowed_as_missing_details():
    with pytest.raises(RuntimeError, match="fixture error"):
        read_detail(RuntimeError("fixture error"))


def test_mk60_fields_and_layout_match_exact_bmw_source(monkeypatch):
    source = (Path.home() / "ECU-reference-data/diagnostics/dsc_mk60.prg")
    decoder = (Path(__file__).resolve().parents[1]
               / "tests/reference_tools/diagnostic_decoder")
    if not source.is_file() or not (decoder / "decoderPrg.py").is_file():
        pytest.skip("private exact BMW diagnostic source unavailable")
    assert sha256(source.read_bytes()).hexdigest() == (
        "31114223634211cc8a241b343916fae9d235ac6fd42c2f319095ada411aa97ea")
    monkeypatch.syspath_prepend(str(decoder))
    from decoderPrg import parse_prg

    tables = {table.name: table for table in parse_prg(str(source)).tables}
    assert dict(row.values for row in tables["FDETAILSTRUKTUR"].rows) == {
        "F_ART_ERW": "nein", "F_HFK": "ja", "F_LZ": "ja", "F_UWB_ERW": "ja"}
    assert [row.values for row in tables["FUMWELTMATRIX"].rows] == [
        ["default", "0x01", "DIGITAL1", "DIGITAL2", "-"]]
    fields = {int(row.values[0], 16): row.values
              for row in tables["FUMWELTTEXTE"].rows}
    assert fields[1][2:] == ["km/h", "-", "unsigned char", "-", "1", "1", "0"]
    for group, expected in (("DIGITAL1", diagnostics._MK60_SNAPSHOT_INPUTS),
                            ("DIGITAL2", diagnostics._MK60_SNAPSHOT_CONTROL)):
        group_row = tables[group].rows[0].values
        ids = [int(value, 16) for value in group_row[1:]]
        assert int(group_row[0]) == len(ids) == len(expected)
        assert [int(fields[field_id][4], 16) for field_id in ids] == [
            mask for mask, _ in expected]
        assert all(fields[field_id][2] == "0/1" for field_id in ids)
        if group == "DIGITAL2":
            assert all(fields[field_id][3] == "high" for field_id in ids)
        assert all(fields[field_id][6:] == ["1", "1", "0"] for field_id in ids)
