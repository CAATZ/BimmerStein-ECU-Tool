"""Durable operation journaling for production native-fast DS2 sessions."""

from __future__ import annotations

import datetime as _datetime
import json
import math
import os
import threading
import uuid
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Mapping, Optional, Union

from ds2_fast_contracts import FastOperation


JOURNAL_SCHEMA = "ms41-native-fast-operation-journal/v1"


class JournalError(RuntimeError):
    """The durable operation journal was used inconsistently."""


def _utc_now() -> str:
    return _datetime.datetime.now(_datetime.timezone.utc).isoformat(
        timespec="milliseconds"
    ).replace("+00:00", "Z")


def _validate_utc(value: object, label: str) -> None:
    if not isinstance(value, str):
        raise JournalError(f"{label} must be an ISO-8601 string")
    try:
        parsed = _datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise JournalError(f"{label} is not valid ISO-8601") from error
    if parsed.tzinfo is None:
        raise JournalError(f"{label} must include a timezone")


def _journal_value(value: object) -> object:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise JournalError("journal floats must be finite")
        return value
    if isinstance(value, bytes):
        return {"byte_length": len(value)}
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Enum):
        return _journal_value(value.value)
    if isinstance(value, Mapping):
        return {str(key): _journal_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_journal_value(item) for item in value]
    raise JournalError(f"unsupported journal value type: {type(value).__name__}")


class OperationJournal:
    """Exclusive-create, sequence-numbered JSONL with flush+fsync per event."""

    _OUTCOMES = frozenset(
        ("success", "failed", "aborted", "commit_unknown", "power_cycle_required")
    )

    def __init__(
        self,
        path: Union[str, os.PathLike],
        *,
        operation: Union[FastOperation, str],
        metadata: Optional[Mapping[str, object]] = None,
        operation_id: Optional[str] = None,
    ):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.operation_id = operation_id or str(uuid.uuid4())
        try:
            uuid.UUID(self.operation_id)
        except ValueError as error:
            raise JournalError("operation_id must be a UUID") from error
        self.operation = (
            operation.value if isinstance(operation, FastOperation) else str(operation)
        )
        self._lock = threading.Lock()
        self._sequence = 0
        self._closed = False
        try:
            self._stream = self.path.open("x", encoding="utf-8", newline="\n")
        except FileExistsError as error:
            raise JournalError(f"refusing to overwrite journal {self.path}") from error
        try:
            self.append("journal_started", metadata=dict(metadata or {}))
        except Exception:
            self._stream.close()
            self.path.unlink(missing_ok=True)
            raise

    def _append_locked(self, event: str, fields: Mapping[str, object]) -> None:
        if self._closed:
            raise JournalError("journal is closed")
        if not event or not isinstance(event, str):
            raise JournalError("journal event must be a non-empty string")
        record = {
            "schema": JOURNAL_SCHEMA,
            "sequence": self._sequence,
            "utc": _utc_now(),
            "operation_id": self.operation_id,
            "operation": self.operation,
            "event": event,
            "fields": _journal_value(fields),
        }
        self._stream.write(
            json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n"
        )
        self._stream.flush()
        os.fsync(self._stream.fileno())
        self._sequence += 1

    def append(self, event: str, **fields: object) -> None:
        with self._lock:
            self._append_locked(event, fields)

    def event_callback(self, event: str, fields: Mapping[str, object]) -> None:
        self.append(event, **dict(fields))

    def finish(self, outcome: str, **fields: object) -> None:
        if outcome not in self._OUTCOMES:
            raise JournalError(f"unsupported terminal outcome {outcome!r}")
        with self._lock:
            self._append_locked("journal_finished", {"outcome": outcome, **fields})
            self._stream.close()
            self._closed = True

    @property
    def closed(self) -> bool:
        return self._closed

    def __enter__(self) -> "OperationJournal":
        return self

    def __exit__(self, exc_type, exc, traceback) -> bool:
        if not self._closed:
            if exc is None:
                self.finish("aborted", reason="context exited without explicit outcome")
            else:
                self.finish("failed", error=f"{exc_type.__name__}: {exc}")
        return False


@dataclass(frozen=True)
class JournalInspection:
    path: Path
    operation_id: str
    operation: str
    event_count: int
    last_event: str
    complete: bool
    outcome: Optional[str]


def inspect_operation_journal(
    path: Union[str, os.PathLike],
) -> JournalInspection:
    """Validate ordering and report whether the journal has a terminal record."""

    journal_path = Path(path)
    try:
        lines = journal_path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise JournalError(f"cannot read journal {journal_path}: {error}") from error
    if not lines:
        raise JournalError("journal is empty")

    operation_id = None
    operation = None
    last_event = ""
    outcome = None
    terminal_seen = False
    for expected_sequence, line in enumerate(lines):
        try:
            record = json.loads(line)
        except json.JSONDecodeError as error:
            raise JournalError(
                f"journal record {expected_sequence} is truncated or malformed"
            ) from error
        if not isinstance(record, dict):
            raise JournalError(f"journal record {expected_sequence} is not an object")
        if record.get("schema") != JOURNAL_SCHEMA:
            raise JournalError(f"journal record {expected_sequence} has the wrong schema")
        if record.get("sequence") != expected_sequence:
            raise JournalError(
                f"journal sequence is not contiguous at record {expected_sequence}"
            )
        _validate_utc(record.get("utc"), f"journal record {expected_sequence} UTC")
        record_id = record.get("operation_id")
        try:
            uuid.UUID(record_id)
        except (AttributeError, TypeError, ValueError) as error:
            raise JournalError(
                f"journal record {expected_sequence} has an invalid operation UUID"
            ) from error
        record_operation = record.get("operation")
        if not isinstance(record_operation, str) or not record_operation:
            raise JournalError(f"journal record {expected_sequence} has no operation")
        if expected_sequence == 0:
            operation_id = record_id
            operation = record_operation
            if record.get("event") != "journal_started":
                raise JournalError("journal does not begin with journal_started")
        elif record_id != operation_id or record_operation != operation:
            raise JournalError("journal identity changed between records")
        if terminal_seen:
            raise JournalError("journal contains records after its terminal event")
        event = record.get("event")
        fields = record.get("fields")
        if not isinstance(event, str) or not event or not isinstance(fields, dict):
            raise JournalError(
                f"journal record {expected_sequence} has invalid event fields"
            )
        last_event = event
        if event == "journal_finished":
            terminal_seen = True
            outcome = fields.get("outcome")
            if outcome not in OperationJournal._OUTCOMES:
                raise JournalError("journal terminal outcome is invalid")

    return JournalInspection(
        path=journal_path,
        operation_id=str(operation_id),
        operation=str(operation),
        event_count=len(lines),
        last_event=last_event,
        complete=terminal_seen,
        outcome=outcome,
    )
