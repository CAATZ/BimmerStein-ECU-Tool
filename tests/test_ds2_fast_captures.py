"""Replay the supplied read/write captures against the offline contracts.

The private captures are not shipped with the repository. These tests skip
cleanly when the evidence directory is absent, matching the existing reference
ROM test policy.
"""

from collections import Counter
import os
from pathlib import Path

import pytest

from ds2_fast_contracts import (
    FastOperation,
    FlashOperation,
    FlashRequest,
    decode_ds2_frame,
    decode_ds2_response,
    read_response_contract,
    validate_flash_exchange,
)
from ds2_fast_plans import (
    FULL_IMAGE_SIZE,
    TUNE_END,
    TUNE_SECTOR_END,
    TUNE_SIZE,
    TUNE_START,
    build_fast_full_write_plan,
    build_fast_partial_write_plan,
    ds2_image_to_file_layout,
)
from tests.ds2_capture_support import (
    CapturedFrame,
    decode_raw_like_saleae,
    decode_raw_phase,
    load_saleae_frames,
    saleae_window,
)


def _optional_capture_root() -> Path | None:
    for variable in ("MS41_DS2_CAPTURE_ROOT", "MS41_TEST_DATA_ROOT"):
        value = os.environ.get(variable, "").strip()
        if value:
            root = Path(value).expanduser()
            if root.is_dir():
                return root
    return None


CAPTURE_ROOT = _optional_capture_root()


def _capture(name: str) -> Path:
    if CAPTURE_ROOT is None:
        pytest.skip(
            "private DS2 captures unavailable; set MS41_DS2_CAPTURE_ROOT "
            "or MS41_TEST_DATA_ROOT"
        )
    direct = CAPTURE_ROOT / name
    if direct.is_file():
        return direct
    matches = sorted(CAPTURE_ROOT.rglob(name))
    if not matches:
        pytest.skip(f"private DS2 capture unavailable: {name}")
    return matches[0]


DECODED_CAPTURE_COUNTS = (
    ("Fast Full Read First Step 9600.csv", 20),
    ("Fast Full Read Second Step 19200.csv", 2),
    ("Fast Full Read Third Step 192000.csv", 1746),
    ("Fast Full Write Fourth Step 9600.csv", 2),
    ("Fast Full Write First Step 9600.csv", 28),
    ("Fast Full Write Second Step 19200.csv", 2),
    ("Fast Full Write Third Step 192000.csv", 1396),
    ("Fast Partial Read Fist Step 9600.csv", 20),
    ("Fast Partial Read Second Step 19200.csv", 2),
    ("Fast Partial Read Third Step 192000.csv", 204),
    ("Fast Partial Read Fourth Step 9600.csv", 2),
    ("Fast Partial Write First Step 9600.csv", 32),
    ("Fast Partial Write Second Step 19200.csv", 2),
    ("Fast Partial Write Third Step 192000.csv", 0),
)


@pytest.mark.parametrize("name,expected_frames", DECODED_CAPTURE_COUNTS)
def test_every_decoded_fragment_has_only_valid_ds2_frames(name, expected_frames):
    frames, rejects = load_saleae_frames(_capture(name))
    assert rejects == 0
    assert len(frames) == expected_frames
    for frame in frames:
        decode_ds2_frame(frame.data)


RAW_EXPORT_EQUIVALENCE = (
    (
        "Fast Full Read (Complete - RAW).csv",
        "Fast Full Read First Step 9600.csv",
        9600.0,
        9615.4,
    ),
    (
        "Fast Full Read (Complete - RAW).csv",
        "Fast Full Read Second Step 19200.csv",
        19200.0,
        19736.8,
    ),
    (
        "Fast Full Read (Complete - RAW).csv",
        "Fast Full Read Third Step 192000.csv",
        192000.0,
        187500.0,
    ),
    (
        "Fast Full Read (Complete - RAW).csv",
        "Fast Full Write Fourth Step 9600.csv",
        9600.0,
        9615.4,
    ),
    (
        "Fast Full Write (Complete - RAW).csv",
        "Fast Full Write First Step 9600.csv",
        9600.0,
        9615.4,
    ),
    (
        "Fast Full Write (Complete - RAW).csv",
        "Fast Full Write Second Step 19200.csv",
        19200.0,
        19736.8,
    ),
    (
        "Fast Full Write (Complete - RAW).csv",
        "Fast Full Write Third Step 192000.csv",
        192000.0,
        187500.0,
    ),
    (
        "Fast Partial Read (Complete - RAW).csv",
        "Fast Partial Read Fist Step 9600.csv",
        9600.0,
        9615.4,
    ),
    (
        "Fast Partial Read (Complete - RAW).csv",
        "Fast Partial Read Second Step 19200.csv",
        19200.0,
        19736.8,
    ),
    (
        "Fast Partial Read (Complete - RAW).csv",
        "Fast Partial Read Third Step 192000.csv",
        192000.0,
        187500.0,
    ),
    (
        "Fast Partial Read (Complete - RAW).csv",
        "Fast Partial Read Fourth Step 9600.csv",
        9600.0,
        9615.4,
    ),
    (
        "Fast Partial Write (Complete - RAW).csv",
        "Fast Partial Write First Step 9600.csv",
        9600.0,
        9615.4,
    ),
    (
        "Fast Partial Write (Complete - RAW).csv",
        "Fast Partial Write Second Step 19200.csv",
        19200.0,
        19736.8,
    ),
)


@pytest.mark.parametrize(
    "raw_name,decoded_name,host_baud,ecu_baud",
    RAW_EXPORT_EQUIVALENCE,
)
def test_raw_waveform_reconstruction_matches_each_decoded_export(
    raw_name, decoded_name, host_baud, ecu_baud
):
    decoded, decoded_rejects = load_saleae_frames(_capture(decoded_name))
    raw, raw_rejects = decode_raw_like_saleae(
        _capture(raw_name),
        _capture(decoded_name),
        host_baud=host_baud,
        ecu_baud=ecu_baud,
    )
    assert decoded_rejects == raw_rejects == 0
    assert [(frame.direction, frame.data) for frame in raw] == [
        (frame.direction, frame.data) for frame in decoded
    ]


def _flash_request(frame: CapturedFrame) -> FlashRequest:
    assert frame.direction == "HOST"
    assert frame.data[2] == 0x07
    args = frame.data[3:-1]
    assert len(args) >= 5
    count = args[4]
    data = args[5:]
    assert len(data) == count
    return FlashRequest(args[0], int.from_bytes(args[1:4], "big"), data)


def _flash_exchanges(frames):
    exchanges = []
    pending = None
    for frame in frames:
        if frame.direction == "HOST" and frame.data[2] == 0x07:
            assert pending is None, "capture issued another flash request before its reply"
            pending = _flash_request(frame)
        elif frame.direction == "ECU" and pending is not None:
            exchanges.append((pending, frame))
            pending = None
    assert pending is None, "capture ended with an unanswered flash request"
    return exchanges


def _read_exchanges(frames):
    exchanges = []
    pending = None
    for frame in frames:
        if frame.direction == "HOST" and frame.data[2] == 0x06:
            assert pending is None
            args = frame.data[3:-1]
            assert len(args) == 5
            pending = (int.from_bytes(args[:4], "big"), args[4])
        elif frame.direction == "ECU" and pending is not None:
            exchanges.append((pending, frame))
            pending = None
    assert pending is None
    return exchanges


@pytest.mark.parametrize(
    "name,expected_reads",
    (
        ("Fast Full Read Third Step 192000.csv", 872),
        ("Fast Partial Read Third Step 192000.csv", 101),
    ),
)
def test_high_read_captures_satisfy_every_read_response_contract(
    name, expected_reads
):
    frames, rejects = load_saleae_frames(_capture(name))
    assert rejects == 0
    exchanges = _read_exchanges(frames)
    assert len(exchanges) == expected_reads
    for (_address, count), captured_response in exchanges:
        response = decode_ds2_response(captured_response.data)
        read_response_contract(count).validate(response)


def test_full_write_capture_satisfies_all_688_flash_contracts():
    frames, rejects = load_saleae_frames(
        _capture("Fast Full Write Third Step 192000.csv")
    )
    assert rejects == 0
    exchanges = _flash_exchanges(frames)
    assert len(exchanges) == 688
    assert Counter(request.operation for request, _ in exchanges) == {
        0x02: 679,
        0x06: 2,
        0x0F: 7,
    }
    for request, captured_response in exchanges:
        validate_flash_exchange(
            FastOperation.FULL_WRITE,
            request,
            captured_response.data,
            echo_complete=True,
        )


def test_full_write_low_capture_uses_one_initial_challenge_and_no_e659_gate():
    frames, rejects = load_saleae_frames(
        _capture("Fast Full Write First Step 9600.csv")
    )
    assert rejects == 0
    host_frames = [frame.data for frame in frames if frame.direction == "HOST"]
    authorization_payloads = [
        frame[3:-1]
        for frame in host_frames
        if frame[2] == 0x90
    ]
    read_addresses = [
        int.from_bytes(frame[3:7], "big")
        for frame in host_frames
        if frame[2] == 0x06
    ]

    assert authorization_payloads[0] == b"BMW\x1e"
    assert authorization_payloads.count(b"BMW\x1e") == 1
    assert len(authorization_payloads[1]) == 4
    assert authorization_payloads[2] == b"BMW"
    assert authorization_payloads[3][0] == 0x12
    assert 0xE659 not in read_addresses


def _partial_write_high_frames():
    raw = _capture("Fast Partial Write (Complete - RAW).csv")
    mid = _capture("Fast Partial Write Second Step 19200.csv")
    _start, mid_end = saleae_window(mid)
    frames, rejects = decode_raw_phase(
        raw,
        start=mid_end,
        end=40.0,
        host_baud=192000.0,
        ecu_baud=187500.0,
    )
    assert rejects == 0
    return frames


def test_partial_write_raw_capture_satisfies_its_distinct_flash_contract():
    frames = _partial_write_high_frames()
    assert len(frames) == 250
    exchanges = _flash_exchanges(frames)
    assert len(exchanges) == 81
    assert Counter(request.operation for request, _ in exchanges) == {
        0x00: 79,
        0x06: 1,
        0x0F: 1,
    }
    for request, captured_response in exchanges:
        validate_flash_exchange(
            FastOperation.PARTIAL_WRITE,
            request,
            captured_response.data,
            echo_complete=True,
        )


def test_partial_write_planner_reconstructs_the_capture_with_safer_boundaries():
    frames = _partial_write_high_frames()
    captured = [
        _flash_request(frame)
        for frame in frames
        if frame.direction == "HOST"
        and frame.data[2] == 0x07
        and frame.data[3] == FlashOperation.PARTIAL_PROGRAM
    ]
    tune = bytearray(b"\xFF" * TUNE_SIZE)
    for request in captured:
        offset = request.address - TUNE_START
        assert 0 <= offset < TUNE_SIZE
        tune[offset : offset + request.count] = request.data
    plan = build_fast_partial_write_plan(
        tune,
        b"\xFF" * (TUNE_SECTOR_END - TUNE_END),
    )

    planned_tune = bytearray(b"\xFF" * TUNE_SIZE)
    for request in plan.program:
        offset = request.address - TUNE_START
        planned_tune[offset : offset + request.count] = request.data

    captured_crossings = [
        request
        for request in captured
        if request.address // 0x4000
        != (request.address + request.count - 1) // 0x4000
    ]
    planned_crossings = [
        request
        for request in plan.program
        if request.address // 0x4000
        != (request.address + request.count - 1) // 0x4000
    ]
    assert len(captured) == len(plan.program) == 79
    assert sum(request.count for request in captured) == 18_211
    assert sum(request.count for request in plan.program) == 18_232
    assert len(captured_crossings) == 1
    assert captured_crossings[0].address == 0x13F2A
    assert planned_crossings == []
    assert bytes(planned_tune) == bytes(tune)


def test_full_write_planner_replays_all_688_captured_flash_requests():
    frames, rejects = load_saleae_frames(
        _capture("Fast Full Write Third Step 192000.csv")
    )
    assert rejects == 0
    captured = [
        _flash_request(frame)
        for frame in frames
        if frame.direction == "HOST" and frame.data[2] == 0x07
    ]

    capture_target_ds2 = bytearray(b"\xFF" * FULL_IMAGE_SIZE)
    for request in captured:
        if not request.is_program:
            continue
        end = request.address + request.count
        assert end <= FULL_IMAGE_SIZE
        capture_target_ds2[request.address:end] = request.data
    capture_target_file = ds2_image_to_file_layout(capture_target_ds2)
    plan = build_fast_full_write_plan(capture_target_file, capture_target_file)

    assert list(plan.high_flash_requests) == captured
    assert len(plan.data_requests) == 679
    assert sum(request.count for request in plan.data_requests) == 156_465
    assert Counter(request.count for request in plan.data_requests) == {
        107: 1,
        128: 1,
        210: 1,
        214: 8,
        231: 668,
    }
