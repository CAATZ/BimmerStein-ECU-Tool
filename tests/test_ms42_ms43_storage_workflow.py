"""Retained-flash wire workflows and injected EEPROM service contracts.

EEPROM payload/native hardware tests are maintained with its dedicated service;
these cases exercise the shared snapshot, journal and recovery owner.
"""

import json

import pytest

from ds2 import DS2Error
from ms42_ms43 import BaudTransitionError, FlashInterrupted
import ms42_ms43_storage as storage
from tests.test_ms42_ms43 import Journal
from tests.test_ms42_ms43_agent import packet
from tests.test_ms42_ms43_agent_workflow import agent_workflow as agent_workflow


pytestmark = pytest.mark.parametrize("agent_workflow,kind", [
    ("MS42", "retained"), ("MS43", "retained"), ("MS43", "eeprom")], indirect=["agent_workflow"])


class EepromService:
    """Only the agreed low-level device contract; no alternate protocol."""
    def __init__(self, ecu):
        self.identity = ecu._identity_fields()
        self.data = bytearray(b"\xA5" * 2048)
        self.writes = []
        self.resets = self.entries = self.confirmations = 0

    def enter(self, log_fn=None):
        self.entries += 1

    def read_all(self, progress_cb=None):
        return bytes(self.data)

    def write_word(self, offset, data):
        assert offset % 2 == 0 and len(data) == 2
        self.data[offset:offset + 2] = data
        self.writes.append((offset, bytes(data)))

    def _confirm_identity(self):
        self.confirmations += 1

    def _reset(self):
        self.resets += 1


@pytest.fixture
def workflow(agent_workflow, kind, monkeypatch):
    ecu, peer = agent_workflow
    service = EepromService(ecu) if kind == "eeprom" else None
    if service:
        monkeypatch.setattr(storage, "_select", lambda *args, **kwargs: service)
        before = bytes(service.data)
    else:
        peer.image[0x4000] = peer.image[0x6000] = 0
        before = bytes(peer.image[0x4000:0x8000])
    target = bytearray(before)
    target[0:2], target[-2:] = b"\xFF\x12", b"\x34\x56"
    if kind == "retained":
        target[0x2000] = 0xFF
    snapshot = storage.make_snapshot(ecu.family, kind, target, software_id=storage.SOFTWARE[ecu.family])
    return ecu, peer, service, before, snapshot


def contents(peer, service):
    return bytes(service.data if service else peer.image[0x4000:0x8000])


def inject_failure(peer, service, monkeypatch, mode):
    fired = []
    if service:
        original = service.write_word
        def write(offset, data):
            if not fired:
                fired.append(True)
                if mode == "ack":
                    original(offset, data)
                else:
                    service.data[offset] = data[0]
                raise DS2Error("injected EEPROM write failure")
            return original(offset, data)
        monkeypatch.setattr(service, "write_word", write)
    else:
        original = peer.agent_request
        def request(command, offset, count, data):
            if command == ord("P") and not fired:
                fired.append(True)
                if mode == "ack":
                    original(command, offset, count, data)
                    return b""
                peer.image[offset:offset + 2] = data[:2]
                if mode == "zero":
                    peer.image[offset] = 0
                return packet(command, offset, count, status=4)
            return original(command, offset, count, data)
        monkeypatch.setattr(peer, "agent_request", request)
    return fired


def test_read_is_complete_and_sends_no_storage_writes(workflow):
    ecu, peer, service, before, snapshot = workflow
    result = storage.read_storage(ecu, snapshot["kind"])
    assert result["data"] == before and result["sha256"] == storage.sha256(before).hexdigest()
    assert not peer.programs and not peer.erases
    assert not service or not service.writes and service.resets == 1
    assert not ecu.can_retry_write


def test_complete_restore_preserves_other_storage_and_backups_first(workflow):
    ecu, peer, service, before, snapshot = workflow
    flash = bytes(peer.image)
    journal, saved = Journal(), []
    def backup(value, path):
        assert any(event == "storage_backup" for event, _ in journal.events)
        assert not peer.programs and not peer.erases and (not service or not service.writes)
        saved.append(value)
    result = storage.restore_storage(ecu, snapshot, journal=journal, backup_cb=backup)
    assert result["snapshot"] == snapshot and result["backup"]["data"] == before
    assert saved == [result["backup"]] and result["verified"]
    assert contents(peer, service) == snapshot["data"]
    assert bytes(peer.image[:0x4000]) == flash[:0x4000] and bytes(peer.image[0x8000:]) == flash[0x8000:]
    assert not peer.agent_finalizations and not peer.finalizations
    assert ecu.last_write.completed and ecu.last_write.verified and not ecu.can_retry_write
    if service:
        assert bytes(peer.image) == flash and len(service.writes) == 2
    else:
        assert peer.erases == [1, 2]
        assert all(0x4000 <= offset < offset + len(data) <= 0x8000 for offset, data in peer.programs)


@pytest.mark.parametrize("mode", ["ack", "partial", "zero"])
def test_interrupted_restore_retains_target_and_reconciles_before_explicit_retry(workflow, monkeypatch, mode):
    ecu, peer, service, before, snapshot = workflow
    inject_failure(peer, service, monkeypatch, mode)
    with pytest.raises(FlashInterrupted):
        storage.restore_storage(ecu, snapshot, journal=Journal())
    assert ecu.can_retry_write and not ecu.last_write.completed
    assert not service or service.resets == 0
    for operation in (lambda: ecu.read_dtcs(), lambda: ecu.write_full(bytes(0x80000)),
                      lambda: storage.read_storage(ecu, snapshot["kind"]),
                      lambda: storage.restore_storage(ecu, snapshot)):
        with pytest.raises(DS2Error, match="incomplete"):
            operation()
    retained = ecu._pending_storage
    assert retained.target == snapshot and retained.before["data"] == before
    snapshot["data"] = bytes(len(snapshot["data"]))  # Caller edits cannot replace the retained target.
    result = ecu.retry_write(journal=Journal())
    assert contents(peer, service) == retained.target["data"] and result["verified"]
    assert ecu.last_write.completed and not ecu.can_retry_write
    assert not peer.agent_finalizations
    assert not service or service.entries == 1 and service.confirmations == 1


@pytest.mark.parametrize("failure", ["journal", "callback"])
def test_backup_failure_aborts_before_mutation(workflow, failure):
    ecu, peer, service, before, snapshot = workflow
    class BrokenJournal(Journal):
        def append(self, event, **fields):
            if event == "storage_backup" and failure == "journal":
                raise OSError("backup unavailable")
            super().append(event, **fields)
    def callback(*args):
        if failure == "callback":
            raise OSError("catalog unavailable")
    with pytest.raises(FlashInterrupted):
        storage.restore_storage(ecu, snapshot, journal=BrokenJournal(), backup_cb=callback)
    assert contents(peer, service) == before
    assert not peer.erases and not peer.programs and (not service or not service.writes)
    assert not ecu.can_retry_write and not ecu.last_write.destructive_started


def test_backup_journal_contains_complete_original_and_target_before_mutation(workflow, tmp_path):
    ecu, peer, service, before, snapshot = workflow
    def callback(value, path):
        with open(path, encoding="utf-8") as stream:
            events = [json.loads(line) for line in stream]
        entry = next(item["fields"] for item in events if item["event"] == "storage_backup")
        assert bytes.fromhex(entry["original_hex"]) == before
        assert bytes.fromhex(entry["target_hex"]) == snapshot["data"]
        assert not peer.erases and not peer.programs and (not service or not service.writes)
    result = storage.restore_storage(ecu, snapshot, backup_dir=tmp_path, backup_cb=callback)
    assert result["backup_journal"] and result["verified"]


def test_completed_restore_cannot_be_replayed_after_reporting_error(workflow):
    ecu, peer, service, before, snapshot = workflow
    class BrokenJournal(Journal):
        def finish(self, outcome, **fields):
            raise OSError("completion journal unavailable")
    with pytest.raises(BaudTransitionError) as caught:
        storage.restore_storage(ecu, snapshot, journal=BrokenJournal())
    assert caught.value.programming_completed and contents(peer, service) == snapshot["data"]
    assert ecu.last_write.completed and not ecu.can_retry_write
    with pytest.raises(DS2Error, match="No incomplete"):
        ecu.retry_write(journal=Journal())


def test_layout_mismatch_does_not_enter_agent(workflow):
    ecu, peer, service, before, snapshot = workflow
    snapshot["software_id"] = "999999"
    with pytest.raises(ValueError, match="layout"):
        storage.restore_storage(ecu, snapshot, journal=Journal())
    assert not peer.agent_requests and (not service or not service.entries)


def test_changed_transport_cannot_resume_storage_write(workflow, monkeypatch):
    ecu, peer, service, before, snapshot = workflow
    inject_failure(peer, service, monkeypatch, "partial")
    with pytest.raises(FlashInterrupted):
        storage.restore_storage(ecu, snapshot, journal=Journal())
    ecu.transport._ser = object()
    assert not ecu.can_retry_write
    with pytest.raises(DS2Error, match="original open"):
        ecu.retry_write(journal=Journal())


def test_identical_restore_does_not_erase_or_program(workflow):
    ecu, peer, service, before, snapshot = workflow
    same = storage.make_snapshot(ecu.family, snapshot["kind"], before, software_id=snapshot["software_id"])
    result = storage.restore_storage(ecu, same, journal=Journal())
    assert result["verified"] and not result["bytes_written"]
    assert not peer.erases and not peer.programs and (not service or not service.writes)


def test_failed_final_readback_retains_recovery_and_does_not_reset(workflow):
    ecu, peer, service, before, snapshot = workflow
    def progress(done, total, label):
        if label == "Verifying restored storage":
            if service:
                service.data[0] ^= 1
            else:
                peer.image[0x4000] ^= 1
    with pytest.raises(FlashInterrupted, match="readback differs"):
        storage.restore_storage(ecu, snapshot, journal=Journal(), progress_cb=progress)
    assert ecu.can_retry_write and not ecu.last_write.completed
    assert not service or not service.resets
    result = ecu.retry_write(journal=Journal())
    assert result["verified"] and contents(peer, service) == snapshot["data"]


@pytest.mark.parametrize("moment", ["during_write", "after_verified"])
def test_progress_exception_cannot_lose_recovery_or_replay_verified_storage(workflow, moment):
    ecu, peer, service, before, snapshot = workflow
    def progress(done, total, label):
        if ((moment == "during_write" and done > 0)
                or (moment == "after_verified" and label == "Restoring diagnostics")):
            raise RuntimeError("progress receiver failed")
    if moment == "during_write":
        with pytest.raises(FlashInterrupted):
            storage.restore_storage(ecu, snapshot, journal=Journal(), progress_cb=progress)
        assert ecu.can_retry_write
        ecu.retry_write(journal=Journal())
    else:
        with pytest.raises(BaudTransitionError) as caught:
            storage.restore_storage(ecu, snapshot, journal=Journal(), progress_cb=progress)
        assert caught.value.programming_completed and not ecu.can_retry_write and ecu.baud_uncertain
    assert ecu.last_write.completed and contents(peer, service) == snapshot["data"]


def test_lost_reset_acknowledgement_does_not_offer_restore_replay(workflow, monkeypatch):
    ecu, peer, service, before, snapshot = workflow
    if service:
        def reset():
            raise DS2Error("lost reset acknowledgement")
        monkeypatch.setattr(service, "_reset", reset)
    else:
        peer.agent_fault = "reset_ack"
    with pytest.raises(BaudTransitionError) as caught:
        storage.restore_storage(ecu, snapshot, journal=Journal())
    assert caught.value.programming_completed and not ecu.can_retry_write and ecu.baud_uncertain
    assert ecu.last_write.completed and contents(peer, service) == snapshot["data"]
