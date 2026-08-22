"""Regression tests for the captured MS41 DS2 command-0x04 record layout."""
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


def test_shadow_decode_and_owner_follow_the_factory_jobs():
    stored = bytes.fromhex("08 B4 03 04 17 0B 30 05 AB 57")
    shadow = bytes.fromhex("08 78 02 09 10 20 30 40 AA 0C BB")

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
    assert result.shadow[0].operating_hours == 12.0
    assert len(result.shadow[0].raw_record) == 11

    with pytest.raises(ValueError, match="shadow DTC payload length"):
        parse_ds2_shadow_response(b"\x01", variant="MS41.2")


def test_four_family_replay_keeps_common_and_family_specific_environment_scopes():
    common = bytes.fromhex("0C 71 01 28 10 20 30 40 AA 00")
    ms410_only = bytes.fromhex("0B 71 01 28 10 20 30 40 AA 00")
    ms412_only = bytes.fromhex("BE 71 01 28 10 20 30 40 AA 00")
    shadow = bytes.fromhex("0C 71 01 28 10 20 30 40 AA 0C BB")

    for variant in ("common", "MS41.0", "MS41.1", "MS41.2", "MS41.3"):
        stored = parse_ds2_dtc_response(b"\x01" + common, variant=variant)[0]
        replayed_shadow = parse_ds2_shadow_response(
            b"\x01" + shadow, variant=variant)[0]
        assert [value.identifier for value in stored.freeze_frame] == [
            0x01, 0x0C, 0x18, 0x0A]
        assert replayed_shadow.memory == "shadow"
        assert replayed_shadow.operating_hours == 12.0

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
