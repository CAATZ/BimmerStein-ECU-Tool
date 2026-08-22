"""Durable operation journal for transmission-conversion writes."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import re
import tempfile
from types import MappingProxyType
from typing import Any


_SCHEMA = 1
_PHASES = frozenset({"writing", "awaiting_cycle", "verified", "failed", "restored"})
_SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_SUFFIX = ".swap-journal.json"
_MAX_RECORD_BYTES = 2 * 1024 * 1024


class JournalError(RuntimeError):
    """Base class for journal failures."""


class JournalIntegrityError(JournalError):
    """The journal or referenced archive is invalid or has changed."""


class JournalStateError(JournalError):
    """A requested operation is invalid for the current journal phase."""


@dataclass(frozen=True)
class OwnerWrite:
    owner: str
    details: Mapping[str, Any]
    complete: bool


@dataclass(frozen=True)
class SwapJournalRecord:
    path: Path
    operation_id: str
    plan: Mapping[str, Any]
    archive_path: Path
    archive_sha256: str
    phase: str
    writes: tuple[OwnerWrite, ...]
    failure: str | None
    failed_from: str | None

    @property
    def incomplete(self) -> bool:
        return self.phase not in {"verified", "restored"}


def _json_value(value: Any, label: str = "value") -> Any:
    if value is None or type(value) in {str, bool, int}:
        return value
    if type(value) is float:
        if math.isfinite(value):
            return value
        raise ValueError(f"{label} contains a non-finite number")
    if isinstance(value, Mapping):
        result = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{label} keys must be strings")
            if key in result:
                raise ValueError(f"{label} contains duplicate key {key!r}")
            result[key] = _json_value(item, label)
        return result
    if isinstance(value, (list, tuple)):
        return [_json_value(item, label) for item in value]
    raise ValueError(f"{label} must contain JSON values only")


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")


def _checksum(record: Mapping[str, Any]) -> str:
    return sha256(_canonical(record)).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as error:
        raise JournalIntegrityError(
            f"cannot read transmission-swap archive {path}: {error}"
        ) from error
    return digest.hexdigest()


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise JournalIntegrityError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _fsync_directory(path: Path) -> None:
    """Persist directory-entry changes where the platform exposes directory fsync."""
    if os.name == "nt":
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    directory_fd = os.open(path, flags)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def ensure_directory_durable(path: str | os.PathLike[str]) -> Path:
    """Create a directory tree and persist each newly published parent entry."""
    directory = Path(path).expanduser().resolve()
    missing = []
    cursor = directory
    while not cursor.exists():
        missing.append(cursor)
        parent = cursor.parent
        if parent == cursor:
            break
        cursor = parent
    directory.mkdir(parents=True, exist_ok=True)
    for created in reversed(missing):
        _fsync_directory(created.parent)
    return directory


def _replace_durably(source: Path, target: Path) -> None:
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        move = ctypes.WinDLL("kernel32", use_last_error=True).MoveFileExW
        move.argtypes = (wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD)
        move.restype = wintypes.BOOL
        if not move(str(source), str(target), 0x1 | 0x8):
            raise ctypes.WinError(ctypes.get_last_error())
        return

    os.replace(source, target)
    _fsync_directory(target.parent)


def write_new_file_durably(path: str | os.PathLike[str], data: bytes) -> Path:
    """Publish a new file only after its bytes and final directory entry are durable."""
    target = Path(path).expanduser().resolve()
    ensure_directory_durable(target.parent)
    if target.exists():
        raise FileExistsError(target)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=target.parent, prefix=f".{target.name}-", suffix=".tmp"
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(bytes(data))
            stream.flush()
            os.fsync(stream.fileno())
        if target.exists():
            raise FileExistsError(target)
        _replace_durably(temporary, target)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    return target


def _atomic_write(path: Path, record: dict[str, Any]) -> None:
    envelope = {"digest": _checksum(record), "record": record}
    data = _canonical(envelope) + b"\n"
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=".swap-journal-", suffix=".tmp"
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        _replace_durably(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _expect_keys(value: object, expected: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise JournalIntegrityError(f"{label} must be an object")
    keys = set(value)
    if keys != expected:
        unknown = sorted(keys - expected)
        missing = sorted(expected - keys)
        detail = []
        if unknown:
            detail.append(f"unknown {unknown}")
        if missing:
            detail.append(f"missing {missing}")
        raise JournalIntegrityError(f"invalid {label} fields: {', '.join(detail)}")
    return value


class SwapOperationJournal:
    """One checksummed, atomically replaced JSON record per conversion."""

    def __init__(self, directory: str | os.PathLike[str]):
        self.directory = ensure_directory_durable(directory)

    def record_path(self, operation_id: str) -> Path:
        if not isinstance(operation_id, str) or not _SAFE_NAME.fullmatch(operation_id):
            raise ValueError("operation_id must be a safe 1-128 character name")
        return self.directory / f"{operation_id}{_SUFFIX}"

    def create(
        self,
        operation_id: str,
        *,
        plan: Mapping[str, Any],
        archive_path: str | os.PathLike[str],
    ) -> SwapJournalRecord:
        path = self.record_path(operation_id)
        if path.exists():
            raise FileExistsError(path)
        if not isinstance(plan, Mapping) or not plan:
            raise ValueError("plan must be a non-empty JSON object")
        plan_copy = _json_value(plan, "plan")
        archive = Path(archive_path).expanduser().resolve(strict=True)
        if not archive.is_file():
            raise ValueError("archive_path must name an existing file")
        record = {
            "schema": _SCHEMA,
            "operation_id": operation_id,
            "plan": plan_copy,
            "archive": {
                "path": str(archive),
                "sha256": _file_sha256(archive),
            },
            "phase": "writing",
            "writes": [],
            "failure": None,
            "failed_from": None,
        }
        _atomic_write(path, record)
        return self.load(operation_id)

    def load(self, operation_id: str) -> SwapJournalRecord:
        path = self.record_path(operation_id)
        record = self._load_record(path)
        if record["operation_id"] != operation_id:
            raise JournalIntegrityError("journal operation id does not match its path")
        return self._public(path, record)

    def load_incomplete(self) -> tuple[SwapJournalRecord, ...]:
        return tuple(record for record in self.load_all() if record.incomplete)

    def load_all(self) -> tuple[SwapJournalRecord, ...]:
        records = []
        for path in sorted(self.directory.glob(f"*{_SUFFIX}")):
            record = self._load_record(path)
            if path != self.record_path(record["operation_id"]):
                raise JournalIntegrityError(
                    "journal operation id does not match its path"
                )
            records.append(self._public(path, record))
        return tuple(records)

    def mark_write_intent(
        self,
        operation_id: str,
        owner: str,
        details: Mapping[str, Any] | None = None,
    ) -> SwapJournalRecord:
        if not isinstance(owner, str) or not _SAFE_NAME.fullmatch(owner):
            raise ValueError("owner must be a safe 1-128 character name")
        details_copy = _json_value(details or {}, "write details")
        path, record = self._editable(operation_id)
        if record["phase"] != "writing":
            raise JournalStateError("write intent requires the writing phase")
        existing = next(
            (write for write in record["writes"] if write["owner"] == owner), None
        )
        if existing is not None:
            if existing["details"] == details_copy and not existing["complete"]:
                return self._public(path, record)
            raise JournalStateError(f"write intent already exists for {owner}")
        record["writes"].append(
            {
                "owner": owner,
                "details": details_copy,
                "complete": False,
            }
        )
        return self._save(path, record)

    def mark_write_complete(self, operation_id: str, owner: str) -> SwapJournalRecord:
        path, record = self._editable(operation_id)
        if record["phase"] != "writing":
            raise JournalStateError("write completion requires the writing phase")
        write = next(
            (item for item in record["writes"] if item["owner"] == owner), None
        )
        if write is None:
            raise JournalStateError(f"no write intent exists for {owner}")
        if write["complete"]:
            return self._public(path, record)
        write["complete"] = True
        return self._save(path, record)

    def mark_awaiting_cycle(self, operation_id: str) -> SwapJournalRecord:
        path, record = self._editable(operation_id)
        if record["phase"] == "awaiting_cycle":
            return self._public(path, record)
        if record["phase"] != "writing":
            raise JournalStateError("ignition-cycle handoff requires the writing phase")
        if not record["writes"] or not all(
            write["complete"] for write in record["writes"]
        ):
            raise JournalStateError(
                "all intended writes must complete before the ignition cycle"
            )
        record["phase"] = "awaiting_cycle"
        return self._save(path, record)

    def mark_final_verified(self, operation_id: str) -> SwapJournalRecord:
        path, record = self._editable(operation_id)
        if record["phase"] == "verified":
            return self._public(path, record)
        if record["phase"] != "awaiting_cycle":
            raise JournalStateError(
                "final verification requires the awaiting-cycle phase"
            )
        record["phase"] = "verified"
        return self._save(path, record)

    def mark_failed(self, operation_id: str, message: str) -> SwapJournalRecord:
        if not isinstance(message, str) or not message.strip():
            raise ValueError("failure message must not be empty")
        message = message.strip()
        if len(message) > 4096:
            raise ValueError("failure message is too long")
        path, record = self._editable(operation_id)
        if record["phase"] == "failed" and record["failure"] == message:
            return self._public(path, record)
        if record["phase"] not in {"writing", "awaiting_cycle"}:
            raise JournalStateError("a terminal operation cannot be marked failed")
        record["failed_from"] = record["phase"]
        record["phase"] = "failed"
        record["failure"] = message
        return self._save(path, record)

    def resume(self, operation_id: str) -> SwapJournalRecord:
        path, record = self._editable(operation_id)
        if record["phase"] != "failed":
            raise JournalStateError("only a failed operation can be resumed")
        record["phase"] = record["failed_from"]
        record["failure"] = None
        record["failed_from"] = None
        return self._save(path, record)

    def mark_restored(self, operation_id: str) -> SwapJournalRecord:
        """Record that every intended owner was exactly restored and verified."""
        path, record = self._editable(operation_id)
        if record["phase"] == "restored":
            return self._public(path, record)
        if record["phase"] != "failed":
            raise JournalStateError("verified rollback requires a failed operation")
        record["phase"] = "restored"
        return self._save(path, record)

    def _editable(self, operation_id: str) -> tuple[Path, dict[str, Any]]:
        path = self.record_path(operation_id)
        record = self._load_record(path)
        if record["operation_id"] != operation_id:
            raise JournalIntegrityError("journal operation id does not match its path")
        return path, record

    def _save(self, path: Path, record: dict[str, Any]) -> SwapJournalRecord:
        self._validate(path, record)
        _atomic_write(path, record)
        return self._public(path, record)

    def _load_record(self, path: Path) -> dict[str, Any]:
        try:
            data = path.read_bytes()
        except OSError as error:
            raise JournalError(f"cannot read journal {path}: {error}") from error
        if not data or len(data) > _MAX_RECORD_BYTES:
            raise JournalIntegrityError("journal size is invalid")
        try:
            envelope = json.loads(
                data.decode("ascii"),
                object_pairs_hook=_object,
                parse_constant=lambda value: (_ for _ in ()).throw(
                    JournalIntegrityError(f"invalid JSON constant {value}")
                ),
            )
        except JournalIntegrityError:
            raise
        except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
            raise JournalIntegrityError(f"journal JSON is invalid: {error}") from error
        envelope = _expect_keys(envelope, {"digest", "record"}, "journal envelope")
        record = envelope["record"]
        if (
            not isinstance(envelope["digest"], str)
            or not _SHA256.fullmatch(envelope["digest"])
            or not isinstance(record, dict)
            or envelope["digest"] != _checksum(record)
        ):
            raise JournalIntegrityError("journal checksum does not match")
        self._validate(path, record)
        return record

    def _validate(self, path: Path, value: object) -> None:
        record = _expect_keys(
            value,
            {
                "schema",
                "operation_id",
                "plan",
                "archive",
                "phase",
                "writes",
                "failure",
                "failed_from",
            },
            "journal record",
        )
        if type(record["schema"]) is not int or record["schema"] != _SCHEMA:
            raise JournalIntegrityError("unsupported journal schema")
        operation_id = record["operation_id"]
        if not isinstance(operation_id, str) or not _SAFE_NAME.fullmatch(operation_id):
            raise JournalIntegrityError("journal operation id is invalid")
        if path != self.record_path(operation_id):
            raise JournalIntegrityError("journal operation id does not match its path")
        if not isinstance(record["plan"], dict) or not record["plan"]:
            raise JournalIntegrityError("journal plan must be a non-empty object")
        try:
            _json_value(record["plan"], "plan")
        except ValueError as error:
            raise JournalIntegrityError(str(error)) from error

        archive = _expect_keys(
            record["archive"], {"path", "sha256"}, "archive reference"
        )
        if not isinstance(archive["path"], str) or not archive["path"]:
            raise JournalIntegrityError("archive path is invalid")
        archive_path = Path(archive["path"])
        if (
            not archive_path.is_absolute()
            or str(archive_path.resolve()) != archive["path"]
        ):
            raise JournalIntegrityError("archive path must be canonical and absolute")
        if not isinstance(archive["sha256"], str) or not _SHA256.fullmatch(
            archive["sha256"]
        ):
            raise JournalIntegrityError("archive SHA-256 is invalid")
        if _file_sha256(archive_path) != archive["sha256"]:
            raise JournalIntegrityError(
                "transmission-swap archive checksum does not match"
            )

        phase = record["phase"]
        if not isinstance(phase, str) or phase not in _PHASES:
            raise JournalIntegrityError(f"unknown journal phase {phase!r}")
        if not isinstance(record["writes"], list):
            raise JournalIntegrityError("journal writes must be an array")
        owners = set()
        for value in record["writes"]:
            write = _expect_keys(
                value, {"owner", "details", "complete"}, "write intent"
            )
            owner = write["owner"]
            if (
                not isinstance(owner, str)
                or not _SAFE_NAME.fullmatch(owner)
                or owner in owners
            ):
                raise JournalIntegrityError("write owner is invalid or duplicated")
            owners.add(owner)
            if not isinstance(write["details"], dict):
                raise JournalIntegrityError("write details must be an object")
            try:
                _json_value(write["details"], "write details")
            except ValueError as error:
                raise JournalIntegrityError(str(error)) from error
            if type(write["complete"]) is not bool:
                raise JournalIntegrityError("write completion must be boolean")

        failure = record["failure"]
        failed_from = record["failed_from"]
        if phase in {"failed", "restored"}:
            if (
                not isinstance(failure, str)
                or not failure.strip()
                or len(failure) > 4096
            ):
                raise JournalIntegrityError(
                    f"{phase} journal has no valid failure message"
                )
            if failed_from not in {"writing", "awaiting_cycle"}:
                raise JournalIntegrityError(
                    f"{phase} journal has no valid pre-failure phase"
                )
        elif failure is not None or failed_from is not None:
            raise JournalIntegrityError(
                "failure details are only valid in failed or restored phases"
            )
        if phase in {"awaiting_cycle", "verified"} and (
            not record["writes"]
            or not all(write["complete"] for write in record["writes"])
        ):
            raise JournalIntegrityError(f"{phase} journal contains incomplete writes")

    @staticmethod
    def _public(path: Path, record: dict[str, Any]) -> SwapJournalRecord:
        archive = record["archive"]
        return SwapJournalRecord(
            path=path,
            operation_id=record["operation_id"],
            plan=_freeze(record["plan"]),
            archive_path=Path(archive["path"]),
            archive_sha256=archive["sha256"],
            phase=record["phase"],
            writes=tuple(
                OwnerWrite(write["owner"], _freeze(write["details"]), write["complete"])
                for write in record["writes"]
            ),
            failure=record["failure"],
            failed_from=record["failed_from"],
        )
