"""Regression tests for captured MS41 stored and shadow fault-record layouts."""
import csv
import os
from pathlib import Path

import pytest

from dtc import (
    format_dtc_table,
    parse_ds2_dtc_response,
    parse_ds2_shadow_response,
    read_ms41_fault_memory,
)
from ds2 import DS2NegativeResponse

def _optional_capture() -> Path | None:
    direct_value = os.environ.get("MS41_DTC_CAPTURE", "").strip()
    if direct_value:
        direct = Path(direct_value).expanduser()
        if direct.is_file():
            return direct

    for variable in ("MS41_DTC_CAPTURE_ROOT", "MS41_TEST_DATA_ROOT"):
        value = os.environ.get(variable, "").strip()
        if not value:
            continue
        root = Path(value).expanduser()
        if not root.is_dir():
            continue
        matches = sorted(
            path for path in root.rglob("*.csv") if "dtc" in path.name.lower()
        )
        if matches:
            # A read capture contains the complete response and is therefore
            # substantially larger than a clear-DTC request/ack capture.
            return max(matches, key=lambda path: path.stat().st_size)
    return None


CAPTURE = _optional_capture()


def test_parse_ds2_dtc_response_uses_count_aligned_flags_and_active_bit():
    stored = bytes.fromhex("08 B4 03 04 17 0B 30 05 AB 57")
    active = bytes.fromhex("64 78 01 28 00 00 03 00 00 00")

    dtc8, dtc100 = parse_ds2_dtc_response(b"\x02" + stored + active)

    assert (dtc8.code, dtc8.status_raw, dtc8.status_text) == (8, 0xB4, "Stored")
    assert not dtc8.is_active
    assert dtc8.raw_record == stored
    assert (dtc100.code, dtc100.status_raw, dtc100.status_text) == (
        100, 0x78, "Active")
    assert dtc100.is_active
    assert dtc100.raw_record == active
    assert dtc8.self_test_reason is None
    assert dtc100.self_test_reason == "0x0003"

    report = format_dtc_table([dtc100])
    summary, raw_records = report.split("\n\nRaw DTC Records\n", 1)
    assert "Raw:" not in summary
    assert "64 78 01 28 00 00 03 00 00 00" not in summary
    assert "Fault Code Reference  Raw Record" in raw_records
    assert "64 78 01 28 00 00 03 00 00 00" in report
    assert "Self-Test Reason: 0x0003" in report


def test_parse_ds2_dtc_response_validates_count_framing():
    assert parse_ds2_dtc_response(b"\x00") == []
    assert parse_ds2_dtc_response(b"\x00\x00") == []
    with pytest.raises(ValueError, match="empty DTC payload"):
        parse_ds2_dtc_response(b"")
    with pytest.raises(ValueError, match="unexpected DTC payload length"):
        parse_ds2_dtc_response(b"\x01")
    with pytest.raises(ValueError, match="unexpected DTC payload length"):
        parse_ds2_dtc_response(b"\x00\x01")

    code255 = bytes.fromhex("FF 20 00 00 00 00 00 00 00 00")
    record = parse_ds2_dtc_response(b"\x01" + code255)[0]
    assert (record.code, record.description) == (
        255,
        "Tank Ventilation System Valve Stuck Open",
    )


def test_rich_fault_decode_uses_exact_bmw_status_and_environment_scaling():
    raw = bytes.fromhex("08 B4 03 04 17 0B 30 05 AB 57")
    record = parse_ds2_dtc_response(
        b"\x01" + raw, variant="MS41.2", current_counter=0xAC00)[0]

    assert record.frequency == 3
    assert record.logistics_counter == 4
    assert record.qualifiers == (
        "Stored after debounce",
        "Currently not present",
        "Sporadic",
        "Emissions relevant",
        "Open circuit",
    )
    assert [(value.label, value.value_text, value.unit) for value in record.freeze_frame] == [
        ("Engine speed", "736", "rpm"),
        ("Throttle angle", "5.1546", "degrees"),
        ("Idle actuator", "18.7488", "%"),
        ("Air-flow-meter voltage", "0.098", "V"),
    ]
    assert record.occurred_hours_ago == pytest.approx(16.9)


def test_shadow_decode_and_owner_use_native_layout_and_shared_operating_counter():
    stored = bytes.fromhex("08 B4 03 04 17 0B 30 05 AB 57")
    shadow = bytes.fromhex("08 78 02 09 10 20 30 40 AA AB 67")

    class FakeDs2:
        def __init__(self):
            self.calls = []
            self.busy = True

        def read_dtc(self, selector):
            self.calls.append((0x04, selector))
            if selector == 0 and self.busy:
                self.busy = False
                raise DS2NegativeResponse(
                    "busy", command=0x04, status=0xA1,
                    response=bytes.fromhex("12 04 A1 B7"))
            if selector == 0:
                return bytes.fromhex("00 00 00 AC 00")
            return b"\x01" + stored

        def read_shadow_dtc(self):
            self.calls.append((0x14, 1))
            return b"\x01" + shadow

    ds2 = FakeDs2()
    result = read_ms41_fault_memory(ds2, "MS41.2")

    assert ds2.calls == [(0x04, 0), (0x04, 0), (0x04, 1), (0x14, 1)]
    assert result.stored[0].occurred_hours_ago == pytest.approx(16.9)
    assert result.shadow[0].memory == "shadow"
    assert result.shadow[0].frequency == 9
    assert result.shadow[0].logistics_counter == 0x10
    assert [value.raw for value in result.shadow[0].freeze_frame] == [0x20, 0x30, 0x40, 0xAA]
    assert result.shadow[0].occurred_hours_ago == pytest.approx(15.3)
    assert result.shadow[0].operating_hours is None
    assert len(result.shadow[0].raw_record) == 11

    with pytest.raises(ValueError, match="shadow DTC payload length"):
        parse_ds2_shadow_response(b"\x01", variant="MS41.2")


def test_four_family_replay_keeps_common_and_family_specific_environment_scopes():
    common = bytes.fromhex("0C 71 01 28 10 20 30 40 AA 00")
    ms410_only = bytes.fromhex("0B 71 01 28 10 20 30 40 AA 00")
    ms412_only = bytes.fromhex("BE 71 01 28 10 20 30 40 AA 00")
    shadow = common[:2] + b"\x0A" + common[2:]

    for variant in ("common", "MS41.0", "MS41.1", "MS41.2", "MS41.3"):
        stored = parse_ds2_dtc_response(b"\x01" + common, variant=variant)[0]
        replayed_shadow = parse_ds2_shadow_response(
            b"\x01" + shadow, variant=variant)[0]
        assert [value.identifier for value in stored.freeze_frame] == [
            0x01, 0x0C, 0x18, 0x0A]
        assert replayed_shadow.memory == "shadow"
        assert replayed_shadow.freeze_frame == stored.freeze_frame
        assert replayed_shadow.frequency == stored.frequency
        assert replayed_shadow.logistics_counter == stored.logistics_counter
        assert replayed_shadow.occurred_hours_ago is None
        assert replayed_shadow.operating_hours is None

    assert [
        bool(parse_ds2_dtc_response(
            b"\x01" + ms410_only, variant=variant)[0].freeze_frame)
        for variant in ("common", "MS41.0", "MS41.1", "MS41.2", "MS41.3")
    ] == [False, True, True, False, False]
    assert [
        bool(parse_ds2_dtc_response(
            b"\x01" + ms412_only, variant=variant)[0].freeze_frame)
        for variant in ("common", "MS41.0", "MS41.1", "MS41.2", "MS41.3")
    ] == [False, False, False, True, True]


@pytest.mark.parametrize("variant", ["common", "MS41.0", "MS41.1", "MS41.2", "MS41.3"])
def test_user_report_stored_and_shadow_freeze_frames_match(variant):
    stored_raw = [bytes.fromhex(raw) for raw in (
        "0C 71 01 28 00 00 0B FF 00 00",
        "16 72 01 28 00 00 7A 80 00 00",
        "64 78 01 28 FF 7A 00 01 00 00",
    )]
    shadow_raw = [bytes.fromhex(raw) for raw in (
        "0C 71 0A 01 28 00 00 0B FF 00 00",
        "16 72 0A 01 28 00 00 7A 80 00 00",
        "64 78 00 01 28 FF 7A 00 01 00 00",
    )]
    stored = parse_ds2_dtc_response(b"\x03" + b"".join(stored_raw), variant=variant)
    shadow = parse_ds2_shadow_response(b"\x03" + b"".join(shadow_raw), variant=variant)
    for index, (normal, extra) in enumerate(zip(stored, shadow)):
        assert normal.raw_record == stored_raw[index]
        assert extra.raw_record == shadow_raw[index]
        assert normal.frequency == extra.frequency == 1
        assert normal.logistics_counter == extra.logistics_counter == 0x28
        assert normal.freeze_frame == extra.freeze_frame
        assert normal.occurred_hours_ago is extra.occurred_hours_ago is None
        assert extra.operating_hours is None
    for record in (stored[0], shadow[0], stored[1], shadow[1]):
        assert next(value.value for value in record.freeze_frame if value.label == "Engine speed") == 0
        assert record.self_test_reason is None
    for record in (stored[1], shadow[1], stored[2], shadow[2]):
        assert next(value.value for value in record.freeze_frame if value.label == "Battery voltage") == pytest.approx(12.444)
    assert stored[2].self_test_reason == shadow[2].self_test_reason == "0x0100"
    report = format_dtc_table(shadow)
    assert report.count("Freeze Frame:") == 3
    assert "Engine speed: 0 rpm" in report
    assert "Battery voltage: 12.444 V" in report
    assert "Self-Test Reason: 0x0100" in report
    assert "Operating hours:" not in report


def test_shadow_counter_is_optional_and_uses_both_big_endian_timestamp_bytes():
    raw = bytes.fromhex("64 78 0A 01 28 FF 7A 34 12 12 34")
    without_counter = parse_ds2_shadow_response(b"\x01" + raw, variant="MS41.0")[0]
    with_counter = parse_ds2_shadow_response(
        b"\x01" + raw, variant="MS41.0", current_counter=0x1357)[0]
    assert without_counter.occurred_hours_ago is None
    assert without_counter.operating_hours is None
    assert with_counter.occurred_hours_ago == pytest.approx(29.1)
    assert with_counter.operating_hours is None
    assert with_counter.self_test_reason == "0x1234"


@pytest.mark.parametrize("parser,raw", [
    (parse_ds2_dtc_response, "0C 71 01 28 00 00 0B FF FF FF"),
    (parse_ds2_shadow_response, "0C 71 0A 01 28 00 00 0B FF FF FF"),
])
def test_fault_age_wraps_the_native_16_bit_operating_counter(parser, raw):
    record = parser(
        b"\x01" + bytes.fromhex(raw), variant="MS41.0", current_counter=0x0001)[0]
    assert record.occurred_hours_ago == pytest.approx(0.2)


def _load_payload() -> bytes:
    assert CAPTURE is not None
    rows = list(csv.reader(CAPTURE.open(encoding="utf-8")))
    rx = [int(r[4], 16) for r in rows[1:] if r[0] == "RX"]
    # First 5 RX bytes are the K-line echo of the TX request frame
    # (addr, len, cmd, arg, checksum); the real response frame follows.
    resp = bytes(rx[5:])
    return resp[3:-1]  # strip addr, len, status / trailing checksum


@pytest.mark.skipif(CAPTURE is None, reason="private DTC capture not configured")
def test_parse_ds2_dtc_response_matches_real_capture():
    payload = _load_payload()
    dtcs = parse_ds2_dtc_response(payload)

    codes = sorted(d.code for d in dtcs)
    expected = sorted([
        8, 55, 25, 21, 212, 238, 242, 240, 243, 239,
        12, 79, 74, 24, 23, 27, 53, 22, 62, 20, 50, 69, 51, 18,
    ])
    assert codes == expected
    assert 1 not in codes
