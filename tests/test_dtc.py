"""Regression tests for the captured MS41 DS2 command-0x04 record layout."""
import csv
import os
from pathlib import Path

import pytest

from dtc import parse_ds2_dtc_response

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
    active = bytes.fromhex("64 61 00 00 00 00 00 40 24 00")

    dtc8, dtc100 = parse_ds2_dtc_response(b"\x02" + stored + active)

    assert (dtc8.code, dtc8.status_raw, dtc8.status_text) == (8, 0xB4, "Stored")
    assert not dtc8.is_active
    assert dtc8.raw_record == stored
    assert (dtc100.code, dtc100.status_raw, dtc100.status_text) == (
        100, 0x61, "Active")
    assert dtc100.is_active
    assert dtc100.raw_record == active


def test_parse_ds2_dtc_response_validates_count_framing():
    assert parse_ds2_dtc_response(b"\x00") == []
    assert parse_ds2_dtc_response(b"\x00\x00") == []
    with pytest.raises(ValueError, match="empty DTC payload"):
        parse_ds2_dtc_response(b"")
    with pytest.raises(ValueError, match="unexpected DTC payload length"):
        parse_ds2_dtc_response(b"\x01")
    with pytest.raises(ValueError, match="unexpected DTC payload length"):
        parse_ds2_dtc_response(b"\x00\x01")


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
