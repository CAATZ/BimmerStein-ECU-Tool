"""Privacy-scoped support bundle shared by desktop and Android."""

from __future__ import annotations

import datetime
import json
import os
from pathlib import Path
import tempfile
import zipfile


def latest_file(directory, patterns):
    paths = []
    root = Path(directory)
    for pattern in patterns:
        paths.extend(root.glob(pattern))
    files = [path for path in paths if path.is_file()]
    try:
        return str(max(files, key=lambda path: path.stat().st_mtime)) if files else ""
    except OSError:
        return ""


def _write_redacted_operation_journal(archive, source):
    with open(source, "r", encoding="utf-8", errors="replace") as stream:
        with archive.open("operation-journal.jsonl", "w") as target:
            for line in stream:
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if isinstance(record, dict):
                    record = {
                        key: record[key]
                        for key in (
                            "sequence", "utc", "operationId", "operation", "phase",
                            "destructiveStarted", "terminal", "transportRoute",
                        )
                        if key in record
                    }
                    target.write(
                        (json.dumps(record, sort_keys=True) + "\n").encode("utf-8")
                    )


def create_support_bundle(
        destination, build_info, *, bin_metadata=None, live_csv="",
        native_journal="", operation_journal="", session_log=""):
    """Write a privacy-scoped support ZIP atomically and return its member names."""
    destination = os.path.abspath(destination)
    os.makedirs(os.path.dirname(destination), exist_ok=True)
    temp = tempfile.NamedTemporaryFile(
        prefix=".bimmerstein-support-", suffix=".tmp",
        dir=os.path.dirname(destination), delete=False)
    temp_path = temp.name
    temp.close()
    members = ["build-info.txt"]
    try:
        with zipfile.ZipFile(
                temp_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("build-info.txt", build_info.rstrip() + "\n")
            if bin_metadata is not None:
                archive.writestr(
                    "selected-bin.json",
                    json.dumps(bin_metadata, indent=2, sort_keys=True) + "\n")
                members.append("selected-bin.json")
            for source, member in (
                    (live_csv, "live-data.csv"),
                    (native_journal, "native-fast-journal.jsonl")):
                if source and os.path.isfile(source):
                    archive.write(source, member)
                    members.append(member)
            if operation_journal and os.path.isfile(operation_journal):
                _write_redacted_operation_journal(archive, operation_journal)
                members.append("operation-journal.jsonl")
            if session_log and os.path.isfile(session_log):
                archive.write(session_log, "session-log.txt")
                members.append("session-log.txt")
            manifest = {
                "schema": 1,
                "created_utc": (
                    datetime.datetime.now(datetime.timezone.utc)
                    .replace(microsecond=0).isoformat().replace("+00:00", "Z")
                ),
                "contents": members + ["manifest.json"],
                "raw_rom_included": False,
                "session_log_included": "session-log.txt" in members,
                "privacy": (
                    "Raw ROMs, Bin filenames, VIN, ISN, notes, and ECU identifiers "
                    "are excluded. Session logs are included only by explicit opt-in."
                ),
            }
            archive.writestr(
                "manifest.json",
                json.dumps(manifest, indent=2, sort_keys=True) + "\n")
            members.append("manifest.json")
        os.replace(temp_path, destination)
    finally:
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass
    return members
