"""Offline field edits preserve other records and reuse existing restore owners."""

import pytest

from checksum import _crc
import ms42_ms43_storage as storage
from ms42_ms43 import FlashInterrupted
from ms42_ms43_storage_edit import editable_fields, edit_storage, select_ms42_slot
from tests.test_ms42_ms43_storage_inspection import put_record
from tests.test_ms42_ms43_storage_edit_native import slot, journal
from tests.test_ms42_ms43_agent_workflow import agent_workflow as agent_workflow
from tests.test_ms42_ms43_storage_workflow import EepromService, inject_failure, contents
from tests.test_ms42_ms43 import Journal


def sample(family):
    if family == "MS42":
        raw = journal(((0, slot(10)), (1, slot(11))))
        kind = "retained"
    else:
        raw = bytearray(b"\xFF" * 0x800)
        put_record(raw, 0x32, b"43290010")
        put_record(raw, 0x280, bytes.fromhex("01000200030080a7"))
        put_record(raw, 0x2A0, b"\x12\x34\x00\x00" + bytes(range(64)))
        put_record(raw, 0x3E2, b"\x01\xF8")
        put_record(raw, 0x3E6, b"\x00\xC3")
        kind = "eeprom"
    return storage.make_snapshot(family, kind, raw, software_id=storage.SOFTWARE[family],
                                 hardware_id="preserved reference")


@pytest.mark.parametrize("family", ("MS42", "MS43"))
def test_atomic_edits_preserve_metadata_padding_other_records_and_roundtrip(family):
    original = sample(family)
    raw = original["data"]
    fields = editable_fields(original)
    assert len(fields) == (3 if family == "MS42" else 71)
    offset = fields[0]["offset"]
    changed = edit_storage(original, {offset: -32768, offset + 2: 32767})
    allowed = {offset, offset + 1, offset + 2, offset + 3, offset + 8, offset + 9}
    if family == "MS42":
        allowed.update((0x7FE, 0x7FF))
        assert select_ms42_slot(changed["data"]) == 1
    assert original["data"] == raw
    assert all(a == b or i in allowed for i, (a, b) in enumerate(zip(raw, changed["data"])))
    assert changed["hardware_id"] == original["hardware_id"]
    assert changed["software_id"] == original["software_id"]
    assert changed["sha256"] != original["sha256"]
    assert storage.decode_snapshot(storage.encode_snapshot(changed)) == changed
    assert [f["value"] for f in editable_fields(changed)[:2]] == [-32768, 32767]
    assert edit_storage(original, {}) == original
    assert edit_storage(original, {offset: fields[0]["value"]}) == original


@pytest.mark.parametrize("value", (-32769, 32768, True, 1.5, "1", None))
def test_wrong_field_values_are_rejected_atomically(value):
    original = sample("MS43")
    before = dict(original)
    with pytest.raises(ValueError, match="integer"):
        edit_storage(original, {0x3E2: 2, 0x280: value})
    assert original == before


@pytest.mark.parametrize("offset", (True, "640", 0, 0x32, 0x281, 0x286, 0x287, 0x288,
                                  0x2A1, 0x2A3, 0x3E3, 0x3E4, 0x3EE, 0x800))
def test_only_declared_field_starts_are_editable(offset):
    with pytest.raises(ValueError, match="not editable"):
        edit_storage(sample("MS43"), {offset: 1})


@pytest.mark.parametrize("offset,value", ((0x3E2, 0), (0x3E2, 3), (0x3E6, 2),
                                         (0x2A4, -1), (0x2E3, 129)))
def test_native_enum_and_knock_bounds_are_enforced(offset, value):
    with pytest.raises(ValueError, match="integer"):
        edit_storage(sample("MS43"), {offset: value})


@pytest.mark.parametrize("family", ("MS42", "MS43"))
def test_bad_record_is_readonly_without_gating_other_records(family):
    original = sample(family)
    raw = bytearray(original["data"])
    offset = 0x514 if family == "MS42" else 0x280
    raw[offset + 8] ^= 1
    if family == "MS42":
        raw[0x7FE:0x800] = _crc(raw[0x400:0x712], 0xF).to_bytes(2, "little")
    original = storage.make_snapshot(family, original["kind"], raw,
                                     software_id=storage.SOFTWARE[family])
    assert offset not in {f["offset"] for f in editable_fields(original)}
    with pytest.raises(ValueError, match="not editable"):
        edit_storage(original, {offset: 1})
    if family == "MS43":
        changed = edit_storage(original, {0x3E2: 2})
        assert changed["data"][0x280:0x28A] == original["data"][0x280:0x28A]


@pytest.mark.parametrize("change", ("binding", "binding_crc", "unknown", "flash", "digest"))
def test_unsupported_or_corrupt_metadata_never_admits_semantic_edits(change):
    original = sample("MS43")
    if change in ("binding", "binding_crc"):
        raw = bytearray(original["data"])
        if change == "binding":
            put_record(raw, 0x32, b"OTHER001")
        else:
            raw[0x3A] ^= 1
        original = storage.make_snapshot("MS43", "eeprom", raw, software_id="430069")
    elif change == "unknown":
        original["software_id"] = "430056"
    elif change == "flash":
        original = storage.make_snapshot("MS43", "retained", bytes(0x4000), software_id="430069")
    else:
        original["sha256"] = "0" * 64
    with pytest.raises(ValueError):
        edit_storage(original, {0x3E2: 2})
    if change != "digest":
        assert storage.decode_snapshot(storage.encode_snapshot(original)) == original


def test_ms42_prior_slot_and_calibration_dependent_byte_remain_readonly():
    original = sample("MS42")
    for offset in (0x114, 0x51A, 0x51B, 0x51C, 0x7FE):
        with pytest.raises(ValueError, match="not editable"):
            edit_storage(original, {offset: 1})


@pytest.mark.parametrize("agent_workflow", ("MS42", "MS43"), indirect=True)
@pytest.mark.parametrize("interrupted", (False, True))
def test_edited_snapshot_uses_existing_backup_restore_and_retry(
        agent_workflow, monkeypatch, interrupted):
    ecu, peer = agent_workflow
    original = sample(ecu.family)
    service = None
    if ecu.family == "MS43":
        service = EepromService(ecu)
        service.data[:] = original["data"]
        monkeypatch.setattr(storage, "_select", lambda *a, **k: service)
    else:
        peer.image[0x4000:0x8000] = original["data"]
    flash = bytes(peer.image)
    offset = editable_fields(original)[0]["offset"]
    target = edit_storage(original, {offset: -123})
    backups = []
    if interrupted:
        inject_failure(peer, service, monkeypatch, "ack")
        with pytest.raises(FlashInterrupted):
            storage.restore_storage(ecu, target, journal=Journal(),
                                    backup_cb=lambda value, _: backups.append(value))
        assert ecu._pending_storage.target == target
        result = ecu.retry_write(journal=Journal())
    else:
        result = storage.restore_storage(ecu, target, journal=Journal(),
                                         backup_cb=lambda value, _: backups.append(value))
    assert backups[0]["data"] == original["data"]
    assert result["verified"] and contents(peer, service) == target["data"]
    assert bytes(peer.image[:0x4000]) == flash[:0x4000]
    assert bytes(peer.image[0x8000:]) == flash[0x8000:]
    assert not ecu.can_retry_write and ecu.last_write.verified
    assert not peer.agent_finalizations and not peer.finalizations
