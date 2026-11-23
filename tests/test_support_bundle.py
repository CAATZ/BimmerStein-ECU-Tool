import json
import zipfile

import pytest

from support_bundle import create_support_bundle


@pytest.mark.parametrize("kind", ["diagnostic", "native-fast"])
def test_journal_export_keeps_framing_and_timing_without_payloads(tmp_path, kind):
    metadata = {
        "source": "send_frame",
        "protocol": "ds2",
        "command": 0xA2,
        "baud": 9600,
        "parity": "E",
        "stopbits": 1,
        "request_length": 6,
        "phase": "response_body",
        "write_length": 6,
        "echo_length": 6,
        "echo_complete": True,
        "echo_mismatch_offset": None,
        "response_length": 4,
        "declared_response_length": 12,
        "expected_response_length": 12,
        "write_duration_s": 0.002,
        "echo_duration_s": 0.015,
        "response_duration_s": 1.0,
        "duration_s": 1.017,
        "elapsed_s": 2.5,
        "error_type": "TimeoutError",
        "usb_packets": 8,
        "usb_raw_bytes": 116,
        "usb_payload_bytes": 100,
        "usb_status_only_packets": 1,
        "usb_overrun_packets": 2,
        "usb_parity_error_packets": 3,
        "usb_framing_error_packets": 4,
        "usb_break_packets": 5,
        "usb_read_gap_max_us": 2345,
    }
    private = {
        key: "PRIVATE_DATA" for key in (
            "request", "request_hex", "echo", "echo_hex", "response", "response_hex",
            "raw_request", "raw_echo", "raw_response", "flash_response_hex", "vin", "isn", "serial",
            "filename", "error", "traceback", "message",
        )
    }
    records = [
        {"event": "owner_log", "fields": {"message": "PRIVATE_DATA"}},
        {"event": "request_failed", "fields": {**metadata, **private}, **private},
        {"event": "journal_finished", "fields": {
            "outcome": "failed", "dropped_events": 3, **private,
        }},
    ]
    source = tmp_path / "PRIVATE_DATA.jsonl"
    source.write_text("\n".join(json.dumps(record) for record in records), encoding="utf-8")
    destination = tmp_path / "support.zip"

    journals = {"diagnostic_journals" if kind == "diagnostic" else "native_journals": [source]}
    create_support_bundle(destination, "test build", **journals)

    with zipfile.ZipFile(destination) as archive:
        exported = [json.loads(line) for line in archive.read(
            f"{kind}-journal.jsonl").decode().splitlines()]
        assert exported == [
            {"event": "request_failed", "fields": metadata},
            {"event": "journal_finished", "fields": {
                "outcome": "failed", "dropped_events": 3,
            }},
        ]
        assert "PRIVATE_DATA" not in "\n".join(archive.namelist())
        assert all(b"PRIVATE_DATA" not in archive.read(name) for name in archive.namelist())
    assert "PRIVATE_DATA" in source.read_text(encoding="utf-8")
