import hashlib
import json
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import bin_compare
from ms41 import MS41ECU


def _entry(path, data, *, stored_hash=None, filename=None):
    path.write_bytes(data)
    return SimpleNamespace(
        path=str(path),
        filename=filename or path.name,
        sha256=(
            hashlib.sha256(data).hexdigest()
            if stored_hash is None else stored_hash
        ),
        source="imported",
        vin="SECRET-VIN",
        ecu_id="SECRET-ECU",
        cal_id="SECRET-CAL",
        notes="SECRET-NOTES",
    )


def test_compare_reports_catalog_replacement_without_rewriting_baseline(tmp_path):
    original = b"\x00" * MS41ECU.TUNE_SIZE
    stored = hashlib.sha256(original).hexdigest()
    changed = bytearray(original)
    changed[2:4] = b"\x01\x02"
    first = _entry(
        tmp_path / "first.bin", bytes(changed),
        stored_hash=stored, filename="first.bin")
    second = _entry(tmp_path / "second.bin", original)

    report = bin_compare.compare_entries(first, second)

    assert "A identity status: REPLACED OR MODIFIED" in report
    assert "Changed bytes: 2 of 24,576" in report
    assert "0x10002-0x10003" in report
    assert first.sha256 == stored


def test_compare_full_to_tune_uses_ds2_calibration_addresses(tmp_path):
    full = bytes([0xFF]) * MS41ECU.FULL_ROM_SIZE
    tune = bytearray(MS41ECU.tune_from_full(full))
    tune[0x123] = 0
    first = _entry(tmp_path / "full.bin", full)
    second = _entry(tmp_path / "tune.bin", bytes(tune))

    report = bin_compare.compare_entries(first, second)

    assert "full ROM reduced to its tune region" in report
    assert "Changed bytes: 1 of 24,576" in report
    assert "0x10123" in report


def test_wrong_size_replacement_still_reports_identity_mismatch(tmp_path):
    stored = hashlib.sha256(
        b"\x00" * MS41ECU.TUNE_SIZE).hexdigest()
    first = _entry(
        tmp_path / "truncated.bin", b"\x01" * 1024,
        stored_hash=stored)
    second = _entry(
        tmp_path / "valid.bin", b"\x00" * MS41ECU.TUNE_SIZE)

    report = bin_compare.compare_entries(first, second)

    assert "A identity status: REPLACED OR MODIFIED" in report
    assert "A analysis: unsupported Bin size 1,024" in report
    assert "BYTE DIFFERENCES\nUnavailable:" in report


def test_patch_inventory_uses_current_patch_catalog(monkeypatch):
    monkeypatch.setattr(bin_compare.patch_service, "base_version", lambda _data: "MS41.3")
    monkeypatch.setattr(
        bin_compare.patch_service, "available_patches",
        lambda _data: [
            {"id": "door_magic", "title": "Door Magic", "version": "V2",
             "installed": True},
            {"id": "cal_guard", "title": "CalGuard", "version": "V4",
             "installed": False},
        ])

    info = bin_compare.analyze_image(
        bytes([0xFF]) * MS41ECU.FULL_ROM_SIZE)

    assert info["installed_patches"] == [
        {"id": "door_magic", "title": "Door Magic", "version": "V2"}
    ]


def test_support_metadata_excludes_private_catalog_fields(tmp_path):
    secret_name = "SECRET-VIN-name.bin"
    entry = _entry(
        tmp_path / secret_name, b"\x00" * MS41ECU.TUNE_SIZE,
        filename=secret_name)

    encoded = json.dumps(bin_compare.support_metadata(entry))

    assert "SECRET-" not in encoded
    assert "filename" not in encoded
    assert "notes" not in encoded
