import json

import pytest

import ds2_fast_safety as safety
from ds2_fast_contracts import FastOperation


def test_journal_is_append_only_sequence_numbered_and_redacts_bytes(tmp_path):
    path = tmp_path / "operation.jsonl"
    journal = safety.OperationJournal(
        path,
        operation=FastOperation.FULL_READ,
        metadata={"port": "COM1"},
        operation_id="11111111-1111-1111-1111-111111111111",
    )
    journal.event_callback("request_completed", {"payload": b"secret", "baud": 187500})
    journal.finish("success", written_bytes=123)

    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [record["sequence"] for record in records] == [0, 1, 2]
    assert [record["event"] for record in records] == [
        "journal_started",
        "request_completed",
        "journal_finished",
    ]
    assert records[1]["fields"]["payload"] == {"byte_length": 6}
    assert "secret" not in path.read_text(encoding="utf-8")
    inspection = safety.inspect_operation_journal(path)
    assert inspection.complete
    assert inspection.outcome == "success"
    assert inspection.event_count == 3
    with pytest.raises(safety.JournalError, match="closed"):
        journal.append("too_late")
    with pytest.raises(safety.JournalError, match="overwrite"):
        safety.OperationJournal(path, operation=FastOperation.FULL_READ)


def test_journal_inspection_distinguishes_interruption_and_rejects_truncation(tmp_path):
    path = tmp_path / "interrupted.jsonl"
    journal = safety.OperationJournal(path, operation=FastOperation.PARTIAL_WRITE)
    journal.append("high_rate_stability_validated", probes=3)
    inspection = safety.inspect_operation_journal(path)
    assert not inspection.complete
    assert inspection.outcome is None
    assert inspection.last_event == "high_rate_stability_validated"
    journal.finish("aborted", reason="test cleanup")

    corrupt = tmp_path / "truncated.jsonl"
    corrupt.write_bytes(path.read_bytes() + b'{"schema":')
    with pytest.raises(safety.JournalError, match="truncated or malformed"):
        safety.inspect_operation_journal(corrupt)
