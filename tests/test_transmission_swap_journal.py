import hashlib
import json

import pytest
import transmission_swap_journal as swap_journal

from transmission_swap_journal import (
    JournalIntegrityError,
    JournalStateError,
    SwapOperationJournal,
)


def test_first_journal_directory_creation_persists_parent_entries(
    monkeypatch, tmp_path
):
    synced = []
    monkeypatch.setattr(
        swap_journal, "_fsync_directory", lambda path: synced.append(path.resolve())
    )
    directory = tmp_path / "backups" / "journal"

    SwapOperationJournal(directory)

    assert synced == [tmp_path.resolve(), (tmp_path / "backups").resolve()]
    synced.clear()
    SwapOperationJournal(directory)
    assert synced == []


def test_new_file_is_synced_before_atomic_publication(monkeypatch, tmp_path):
    events = []
    real_replace = swap_journal.os.replace

    monkeypatch.setattr(
        swap_journal.os, "fsync", lambda _descriptor: events.append("file_fsync")
    )

    def replace(source, target):
        events.append(("replace", source.read_bytes()))
        real_replace(source, target)

    monkeypatch.setattr(swap_journal, "_replace_durably", replace)
    target = tmp_path / "archive.json"

    swap_journal.write_new_file_durably(target, b"exact archive")

    assert events == ["file_fsync", ("replace", b"exact archive")]
    assert target.read_bytes() == b"exact archive"
    assert not tuple(tmp_path.glob(".*.tmp"))


def _archive(tmp_path, data=b"exact pre-write owners"):
    path = tmp_path / "archive.json"
    path.write_bytes(data)
    return path


def _rewrite_with_valid_checksum(path, change):
    envelope = json.loads(path.read_text("ascii"))
    change(envelope["record"])
    canonical = json.dumps(
        envelope["record"],
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")
    envelope["digest"] = hashlib.sha256(canonical).hexdigest()
    path.write_text(
        json.dumps(envelope, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="ascii",
    )


def test_full_durable_lifecycle_and_restart_recovery(tmp_path):
    archive = _archive(tmp_path)
    plan = {"token": "plan-123", "target": "manual", "owners": ["EWS", "KMB"]}
    journal = SwapOperationJournal(tmp_path / "journal")
    record = journal.create("swap-123", plan=plan, archive_path=archive)

    plan["target"] = "automatic"
    assert record.plan["target"] == "manual"
    assert record.archive_path == archive.resolve()
    assert record.archive_sha256 == hashlib.sha256(archive.read_bytes()).hexdigest()
    with pytest.raises(TypeError):
        record.plan["target"] = "automatic"

    journal.mark_write_intent(
        "swap-123", "ews_zcs", {"before_sha256": "11" * 32, "target_sha256": "22" * 32}
    )
    restarted = SwapOperationJournal(tmp_path / "journal")
    pending = restarted.load_incomplete()
    assert len(pending) == 1
    assert pending[0].writes[0].owner == "ews_zcs"
    assert pending[0].writes[0].complete is False

    restarted.mark_write_complete("swap-123", "ews_zcs")
    restarted.mark_write_intent("swap-123", "cluster")
    restarted.mark_write_complete("swap-123", "cluster")
    awaiting = restarted.mark_awaiting_cycle("swap-123")
    assert awaiting.phase == "awaiting_cycle"
    assert restarted.load_incomplete()[0].phase == "awaiting_cycle"

    verified = restarted.mark_final_verified("swap-123")
    assert verified.phase == "verified"
    assert restarted.load_incomplete() == ()
    assert not tuple((tmp_path / "journal").glob("*.tmp"))


def test_phase_rules_and_failed_records_remain_recoverable(tmp_path):
    journal = SwapOperationJournal(tmp_path / "journal")
    journal.create(
        "failed-swap", plan={"token": "abc"}, archive_path=_archive(tmp_path)
    )
    journal.mark_write_intent("failed-swap", "stability", {"address": 16})

    with pytest.raises(JournalStateError, match="all intended writes"):
        journal.mark_awaiting_cycle("failed-swap")
    with pytest.raises(JournalStateError, match="no write intent"):
        journal.mark_write_complete("failed-swap", "cluster")

    failed = journal.mark_failed("failed-swap", "connection lost after write intent")
    assert failed.phase == "failed"
    assert failed.failure == "connection lost after write intent"
    assert failed.failed_from == "writing"
    assert journal.load_incomplete() == (failed,)
    with pytest.raises(JournalStateError, match="writing phase"):
        journal.mark_write_complete("failed-swap", "stability")

    resumed = journal.resume("failed-swap")
    assert resumed.phase == "writing"
    assert resumed.failure is None
    assert resumed.failed_from is None
    assert resumed.writes == failed.writes

    journal.mark_failed("failed-swap", "rollback requested")
    restored = journal.mark_restored("failed-swap")
    assert restored.phase == "restored"
    assert restored.failure == "rollback requested"
    assert restored.failed_from == "writing"
    assert journal.load_incomplete() == ()


def test_awaiting_cycle_failure_resumes_at_final_verification(tmp_path):
    journal = SwapOperationJournal(tmp_path / "journal")
    journal.create("cycle-swap", plan={"token": "abc"}, archive_path=_archive(tmp_path))
    journal.mark_write_intent("cycle-swap", "cluster")
    journal.mark_write_complete("cycle-swap", "cluster")
    journal.mark_awaiting_cycle("cycle-swap")

    failed = journal.mark_failed("cycle-swap", "reconnect timed out")
    assert failed.failed_from == "awaiting_cycle"
    resumed = SwapOperationJournal(tmp_path / "journal").resume("cycle-swap")
    assert resumed.phase == "awaiting_cycle"
    assert journal.mark_final_verified("cycle-swap").phase == "verified"


def test_rejects_record_archive_tampering_and_unknown_phases(tmp_path):
    archive = _archive(tmp_path)
    journal = SwapOperationJournal(tmp_path / "journal")
    record = journal.create("swap-tamper", plan={"token": "abc"}, archive_path=archive)

    envelope = json.loads(record.path.read_text("ascii"))
    envelope["record"]["plan"]["token"] = "changed"
    record.path.write_text(json.dumps(envelope), encoding="ascii")
    with pytest.raises(JournalIntegrityError, match="checksum"):
        journal.load("swap-tamper")

    record.path.unlink()
    record = journal.create("swap-unknown", plan={"token": "abc"}, archive_path=archive)
    _rewrite_with_valid_checksum(
        record.path, lambda value: value.__setitem__("phase", "mystery")
    )
    with pytest.raises(JournalIntegrityError, match="unknown journal phase"):
        journal.load("swap-unknown")

    record.path.unlink()
    record = journal.create("swap-failure", plan={"token": "abc"}, archive_path=archive)

    def invalid_failure(value):
        value["phase"] = "failed"
        value["failure"] = "lost connection"
        value["failed_from"] = "verified"

    _rewrite_with_valid_checksum(record.path, invalid_failure)
    with pytest.raises(JournalIntegrityError, match="pre-failure phase"):
        journal.load("swap-failure")

    record.path.unlink()
    record = journal.create("swap-schema", plan={"token": "abc"}, archive_path=archive)
    _rewrite_with_valid_checksum(
        record.path, lambda value: value.__setitem__("unexpected", True)
    )
    with pytest.raises(JournalIntegrityError, match="invalid journal record fields"):
        journal.load("swap-schema")

    record.path.unlink()
    record = journal.create("swap-archive", plan={"token": "abc"}, archive_path=archive)
    archive.write_bytes(b"changed archive")
    with pytest.raises(JournalIntegrityError, match="archive checksum"):
        journal.load("swap-archive")
