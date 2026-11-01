"""Storage file binding and integrity; no transport construction."""

import json

import pytest

import ms42_ms43_storage as storage
from ms42_ms43_diagnostics import RETAINED_ADDRESS, RETAINED_PROVENANCE


@pytest.mark.parametrize("family,kind,size", [("MS42", "retained", 0x4000),
                                           ("MS43", "retained", 0x4000), ("MS43", "eeprom", 0x800)])
def test_storage_snapshot_roundtrip_and_explicit_layout(family, kind, size):
    data = bytearray(range(256)) * (size // 256)
    snapshot = storage.make_snapshot(family, kind, data, software_id=storage.SOFTWARE[family])
    data[0] ^= 1
    assert snapshot["data"][0] == 0
    assert storage.decode_snapshot(storage.encode_snapshot(snapshot)) == snapshot
    broken = json.loads(storage.encode_snapshot(snapshot))
    broken["software_id"] = "999999"
    with pytest.raises(ValueError, match="checksum"):
        storage.decode_snapshot(json.dumps(broken))
    snapshot["data"] = b"\0" * size
    with pytest.raises(ValueError, match="checksum"):
        storage.encode_snapshot(snapshot)


@pytest.mark.parametrize("family,kind,size", [("MS41", "eeprom", 512), ("MS42", "eeprom", 2048),
                                           ("MS43", "retained", 2048), ("MS42", "unknown", 0)])
def test_unsupported_or_wrong_size_layout_is_rejected(family, kind, size):
    with pytest.raises(ValueError):
        storage.make_snapshot(family, kind, bytes(size), software_id="0110C6")


def test_legacy_snapshot_conversion_is_only_the_retained_flash():
    info = dict(family="MS42", software_id="0110C6", hardware_id="11101102",
                retained_raw=bytes(0x4000), retained_address=RETAINED_ADDRESS,
                retained_provenance=RETAINED_PROVENANCE, aif_raw=b"unrelated identity")
    result = storage.from_identity_snapshot(info)
    assert result["kind"] == "retained" and result["data"] == info["retained_raw"]
    assert "aif_raw" not in result
    info["retained_address"] = 0
    with pytest.raises(ValueError, match="Invalid retained"):
        storage.from_identity_snapshot(info)


def test_unknown_software_can_be_archived_without_claiming_restore_support():
    value = storage.make_snapshot("MS42", "retained", bytes(0x4000), software_id="999999")
    assert storage.decode_snapshot(storage.encode_snapshot(value)) == value
    with pytest.raises(ValueError, match="size"):
        storage.decode_snapshot(" " * (256 * 1024 + 1))
