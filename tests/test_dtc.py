"""Regression test for parse_ds2_dtc_response() against a real capture.

Bug: the parser read the DTC code from byte[3] of each 10-byte record
instead of byte[1]. byte[3] is a constant flag/counter field (0x01) across
most self-test slots, so on a healthy ECU (where the earlier "catalog"
records are placeholder 0x00/0xFF and get filtered) every read collapsed
to a single bogus "DTC 001" regardless of the ECU's actual fault state.
"""
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


def _load_payload() -> bytes:
    assert CAPTURE is not None
    rows = list(csv.reader(CAPTURE.open(encoding="utf-8")))
    rx = [int(r[4], 16) for r in rows[1:] if r[0] == "RX"]
    # First 5 RX bytes are the K-line echo of the TX request frame
    # (addr, len, cmd, arg, checksum); the real response frame follows.
    resp = bytes(rx[5:])
    return resp[3:-1]  # strip addr, len, status / trailing checksum


@pytest.mark.skipif(CAPTURE is None, reason="private DTC capture not configured")
def test_parse_ds2_dtc_response_reads_code_from_byte1_not_byte3():
    payload = _load_payload()
    dtcs = parse_ds2_dtc_response(payload)

    codes = sorted(d.code for d in dtcs)
    expected = sorted([
        8, 55, 25, 21, 212, 238, 242, 240, 243, 239,
        12, 79, 74, 24, 23, 27, 53, 22, 62, 20, 50, 69, 51, 18,
    ])
    assert codes == expected
    # The old (buggy) byte[3] read would have collapsed 14 of these
    # records into a single duplicate "code 1" entry.
    assert 1 not in codes
