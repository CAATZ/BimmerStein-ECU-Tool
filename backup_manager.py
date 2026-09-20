"""
backup_manager.py — ROM backup catalogue for BimmerStein ECU Tool.

Copies .bin files into a 'backups/' directory and maintains a JSON index
storing metadata: date, variant, checksum status, file type, and user notes.
"""

import os
import json
import datetime
import hashlib
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, asdict, replace
from pathlib import Path
from typing import List

from app_paths import mutable_path


# Anchored to the Flasher install dir, not the process CWD — a relative "backups" path
# scatters the catalogue across whatever directory the tool happens to be launched from.
BACKUP_DIR = str(mutable_path("backups"))
INDEX_FILE = os.path.join(BACKUP_DIR, "index.json")
_INTERNAL_NAMES = {"index.json", ".pending", ".folder-migration.json",
                   "native_fast", "transmission", "bsl"}


def _catalog_path(relative: str) -> str:
    """Resolve a catalogue-relative path without traversing links or leaving its root."""
    parts = relative.split("/")
    if any(not part or part in (".", "..") or "\\" in part or ":" in part
           or "\x00" in part for part in parts):
        raise ValueError("invalid backup filename or folder")
    root = os.path.realpath(BACKUP_DIR)
    path = os.path.abspath(os.path.join(root, *parts))
    if (os.path.commonpath((root, path)) != root
            or os.path.normcase(os.path.realpath(path)) != os.path.normcase(path)):
        raise ValueError("backup path escapes the catalogue or traverses a link")
    return path


def _portable_segment(name: str):
    if (not name or name in (".", "..") or len(name.encode("utf-8")) > 255
            or name.endswith((".", " "))
            or any(ord(c) < 32 or c in '<>:"/\\|?*' for c in name)
            or name.split(".", 1)[0].casefold() in {
                "con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)),
                *(f"lpt{i}" for i in range(1, 10))}):
        raise ValueError("filename or folder is not portable")


def _move_file(source: str, destination: str):
    """Publish on the same filesystem without replacing a concurrently created file."""
    if os.name == "nt":
        os.rename(source, destination)  # Windows rename refuses existing destinations.
    else:
        os.link(source, destination)
        os.unlink(source)


def _folder_index_file():
    # Keep metadata outside BACKUP_DIR so it can never replace an imported image.
    return os.path.join(os.path.dirname(BACKUP_DIR), "library-folders.json")


@dataclass
class BackupEntry:
    filename:  str    # path relative to backups/, using forward slashes
    file_type: str    # "Full ROM" | "Tune" | "EEPROM" | "Unknown"
    variant:   str    # "MS41.1" | "MS41.2" | "Unknown" | "N/A"  (cal-side; kept for display)
    cs_ok:     bool
    size:      int
    date:      str    # ISO-8601
    notes:     str = ""
    ecu_id:    str = ""          # DME part number (e.g. "1437806"), if known
    vin:       str = ""          # 17-char VIN; in a full ROM at 0x5D07 (6-bit packed)
    cal_id:    str = ""          # ASCII CAL ID (e.g. "60011110")
    source:    str = "imported"  # "imported" (file) | "ECU read" | ...
    program_variant: str = ""    # program-side variant (Full ROM only; "" for a tune/unknown)
    cal_variant:     str = ""    # cal-side variant (same detector as `variant`, kept separately
                                  # so a hybrid ROM's two sides are both on record)
    hybrid:          str = ""    # human-readable program/cal mismatch description, or ""
    sha256:          str = ""    # immutable catalogue identity; migrated on load for legacy entries
    folder:          str = ""    # relative parent directory; empty means backups/ itself

    @property
    def path(self) -> str:
        return _catalog_path(self.filename)

    @property
    def display_date(self) -> str:
        try:
            return datetime.datetime.fromisoformat(self.date).strftime("%Y-%m-%d  %H:%M")
        except Exception:
            return self.date


class BackupIndexError(RuntimeError):
    """The catalogue could not be loaded or committed without metadata loss."""


class BackupManager:
    """Manages the ROM backup catalogue on disk."""

    def __init__(self):
        os.makedirs(BACKUP_DIR, exist_ok=True)
        self._entries: List[BackupEntry] = []
        self._folders: List[str] = []
        self._pending_records: dict[str, BackupEntry] = {}
        self._missing: List[BackupEntry] = []
        self._legacy = False
        self._index_dirty = False
        self._load()
        self._recover_pending()
        self._migrate_folders()
        self.refresh()

    # ── Public API ─────────────────────────────────────────────────────────

    @property
    def entries(self) -> List[BackupEntry]:
        return list(self._entries)

    @property
    def folders(self) -> List[str]:
        return list(self._folders)

    def create_folder(self, folder: str) -> str:
        folder = self.normalize_folder(folder)
        if not folder:
            raise ValueError("folder name is required")
        if any(candidate.casefold() == folder.casefold() for candidate in self._folders):
            raise ValueError("folder already exists")
        os.makedirs(self.folder_path(folder), exist_ok=False)
        self.refresh()
        return folder

    def folder_path(self, folder: str) -> str:
        return _catalog_path(folder) if folder else os.path.realpath(BACKUP_DIR)

    def add(self, src_path: str, notes: str = "", folder: str = "") -> BackupEntry:
        """Copy a .bin file into backups/ and register it (source='imported')."""
        with open(src_path, "rb") as f:
            data = f.read()
        return self.add_data(data, os.path.basename(src_path),
                             notes=notes, source="imported", folder=folder)

    def _describe(self, data, filename: str, notes: str = "",
                  source: str = "imported", ecu_id: str = "",
                  vin: str = "", variant: str = "", folder: str = "") -> BackupEntry:
        """Derive image metadata without copying or registering the file."""
        from ms41 import MS41ECU
        from checksum import verify_checksum

        data = bytearray(data)
        size = len(data)
        file_type, detected_variant = self._classify(data, size)
        variant = variant or detected_variant
        cal_id = ""
        if file_type == "EEPROM":
            if variant in ("MS41.0", "MS41.1", "MS41.2", "MS41.3"):
                from engines.softbsl import eeprom_ram
                rows = eeprom_ram.field_report(data, variant)
                cs_ok = all(not row["checked"] or row["check_ok"] for row in rows)
            else:
                cs_ok = False
        else:
            cal_id = MS41ECU.read_calid(data) or ""
            if not ecu_id:
                ecu_id = MS41ECU.read_ecu_id(data) or ""
            if not vin:
                vin = MS41ECU.vin_from_image(data) or ""   # full ROMs carry the VIN at 0x5D07
            cs_ok, _ = verify_checksum(data)
        date     = datetime.datetime.now().isoformat(timespec="seconds")

        program_variant = cal_variant = hybrid = ""
        if size == MS41ECU.FULL_ROM_SIZE:
            resolved = MS41ECU.resolve_version(bytes(data))
            program_variant = resolved["program"] or ""
            cal_variant     = resolved["cal"] or ""
            hybrid          = resolved["hybrid"] or ""

        return BackupEntry(
            filename=filename, file_type=file_type, variant=variant, cs_ok=cs_ok,
            size=size, date=date, notes=notes, ecu_id=ecu_id, vin=vin,
            cal_id=cal_id, source=source,
            program_variant=program_variant, cal_variant=cal_variant, hybrid=hybrid,
            sha256=hashlib.sha256(data).hexdigest(),
            folder=folder,
        )

    def add_data(self, data, filename: str, notes: str = "",
                 source: str = "imported", ecu_id: str = "",
                 vin: str = "", variant: str = "", folder: str = "") -> BackupEntry:
        """Archive bytes under a unique name in a real catalogue folder."""
        folder = folder if folder in self._folders else self.normalize_folder(folder)
        os.makedirs(self.folder_path(folder), exist_ok=True)
        base = self._unique_name(filename, folder)
        destination = self._entry_path(base)
        entry = self._describe(data, base, notes, source, ecu_id, vin, variant, folder)
        # Persist provenance before publishing an image that may outlive its index save.
        pending_dir = os.path.join(BACKUP_DIR, ".pending")
        os.makedirs(pending_dir, exist_ok=True)
        pending = os.path.join(pending_dir, f"{uuid.uuid4().hex}.json")
        self._write_json(pending, asdict(entry))
        self._pending_records[pending] = entry
        temporary = os.path.join(BACKUP_DIR, f".{uuid.uuid4().hex}.tmp")
        try:
            with open(temporary, "xb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            _move_file(temporary, destination)
        except FileExistsError:
            # Another writer owns this path; its bytes are not our pending capture.
            os.remove(pending)
            del self._pending_records[pending]
            raise
        finally:
            try:
                os.remove(temporary)
            except FileNotFoundError:
                pass

        self._entries.append(entry)
        if folder and not any(
                candidate.casefold() == folder.casefold() for candidate in self._folders):
            self._folders.append(folder)
            self._folders.sort(key=str.casefold)
        try:
            self._save()
        except OSError as error:
            raise BackupIndexError(
                f"Backup image saved at {destination}, but its catalogue entry "
                f"could not be committed. Recovery metadata is saved at {pending}. "
                "Resolve the storage error and restart to retry catalogue recovery."
            ) from error
        return entry

    @staticmethod
    def _classify(data, size):
        from ms41 import MS41ECU
        if size == 256 * 1024:
            return "Full ROM", (MS41ECU.detect_variant(data) or "Unknown")
        if size == 24 * 1024:
            return "Tune", (MS41ECU.detect_variant(data) or "N/A")
        if size == 512:
            return "EEPROM", "Unknown"
        return "Unknown", "Unknown"

    def _entry_path(self, filename: str) -> str:
        return _catalog_path(filename)

    def _unique_name(self, filename: str, folder: str = "") -> str:
        name = os.path.basename(filename)
        _portable_segment(name)
        base = f"{folder}/{name}" if folder else name
        path = self._entry_path(base)
        pending_names = {entry.filename.casefold() for entry in self._pending_records.values()}
        if (os.path.normcase(path) != os.path.normcase(os.path.realpath(INDEX_FILE))
                and base.split("/", 1)[0].casefold() not in _INTERNAL_NAMES
                and base.casefold() not in pending_names
                and not os.path.exists(path)):
            return base
        stem, ext = os.path.splitext(base)
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        suffix = 1
        while True:
            candidate = f"{stem}_{ts}_{suffix}{ext}"
            if (candidate.casefold() not in pending_names
                    and not os.path.exists(self._entry_path(candidate))):
                return candidate
            suffix += 1

    def exact_entry(self, filename: str, sha256: str = "") -> BackupEntry:
        self._entry_path(filename)
        matches = [entry for entry in self._entries if entry.filename == filename]
        if len(matches) != 1:
            raise ValueError("catalogue entry was not found")
        entry = matches[0]
        if sha256 and entry.sha256 != sha256:
            raise ValueError("catalogue entry identity changed")
        return entry

    def read_data(self, filename: str, sha256: str) -> bytes:
        entry = self.exact_entry(filename, sha256)
        path = self._entry_path(entry.filename)
        with open(path, "rb") as stream:
            data = stream.read()
        if (len(data) != entry.size or
                hashlib.sha256(data).hexdigest() != entry.sha256):
            raise ValueError("catalogue file failed its content check")
        return data

    def remove_exact(self, filename: str, sha256: str):
        entry = self.exact_entry(filename, sha256)
        path = self._entry_path(entry.filename)
        os.remove(path)
        self._entries = [e for e in self._entries if e.filename != entry.filename]
        self._save()

    def update_notes_exact(self, filename: str, sha256: str, notes: str):
        entry = self.exact_entry(filename, sha256)
        entry.notes = str(notes)
        self._save()
        return entry

    def rename_exact(self, filename: str, sha256: str, replacement: str):
        entry = self.exact_entry(filename, sha256)
        replacement = str(replacement).strip()
        if replacement == entry.filename:
            return entry
        _portable_segment(replacement)
        replacement = f"{entry.folder}/{replacement}" if entry.folder else replacement
        if replacement.split("/", 1)[0].casefold() in _INTERNAL_NAMES:
            raise ValueError("filename is not portable")
        if replacement == entry.filename:
            return entry
        if any(candidate.filename.casefold() == replacement.casefold()
               for candidate in [*self._entries, *self._pending_records.values()]):
            raise ValueError("filename already exists")
        self.read_data(entry.filename, entry.sha256)
        source = self._entry_path(entry.filename)
        destination = self._entry_path(replacement)
        if os.path.exists(destination):
            raise ValueError("filename already exists")
        original = entry.filename
        _move_file(source, destination)
        entry.filename = replacement
        try:
            self._save()
        except Exception:
            entry.filename = original
            _move_file(destination, source)
            raise
        return entry

    @staticmethod
    def normalize_folder(folder: str) -> str:
        folder = "/".join(" ".join(part.split()) for part in str(folder).split("/"))
        if not folder:
            return ""
        for part in folder.split("/"):
            _portable_segment(part)
        if folder.split("/", 1)[0].casefold() in _INTERNAL_NAMES | {"all", "unfiled"}:
            raise ValueError("folder name is reserved")
        return folder

    def update_folder_exact(self, filename: str, sha256: str, folder: str):
        entry = self.exact_entry(filename, sha256)
        folder = folder if folder in self._folders else self.normalize_folder(folder)
        if folder == entry.folder:
            return entry
        self.read_data(filename, sha256)
        os.makedirs(self.folder_path(folder), exist_ok=True)
        destination = self._unique_name(os.path.basename(filename), folder)
        original = entry.filename, entry.folder
        _move_file(entry.path, self._entry_path(destination))
        entry.filename, entry.folder = destination, folder
        try:
            self._save()
        except Exception:
            _move_file(entry.path, self._entry_path(original[0]))
            entry.filename, entry.folder = original
            raise
        self.refresh()
        return entry

    def rename_folder(self, current: str, replacement: str) -> int:
        current = current if current in self._folders else self.normalize_folder(current)
        replacement = self.normalize_folder(replacement)
        if not current or not replacement:
            raise ValueError("folder name is required")
        if current not in self._folders:
            raise ValueError("folder was not found")
        if current == replacement:
            return 0
        if replacement.casefold().startswith(current.casefold() + "/"):
            raise ValueError("cannot move a folder inside itself")
        source, destination = self.folder_path(current), self.folder_path(replacement)
        if replacement.casefold() != current.casefold() and os.path.exists(destination):
            raise ValueError("folder already exists")
        matches = [(entry, entry.filename, entry.folder) for entry in self._entries
                   if entry.folder == current or entry.folder.startswith(current + "/")]
        os.makedirs(os.path.dirname(destination), exist_ok=True)
        os.rename(source, destination)
        for entry, filename, folder in matches:
            entry.filename = replacement + filename[len(current):]
            entry.folder = replacement + folder[len(current):]
        try:
            self._save()
        except Exception:
            os.rename(destination, source)
            for entry, filename, folder in matches:
                entry.filename, entry.folder = filename, folder
            raise
        self.refresh()
        return len(matches) or 1

    def clear_folder(self, folder: str) -> int:
        """Move images to Unfiled, removing only directories left empty."""
        folder = folder if folder in self._folders else self.normalize_folder(folder)
        if not folder or folder not in self._folders:
            raise ValueError("folder was not found")
        matches = [entry for entry in self._entries
                   if entry.folder == folder or entry.folder.startswith(folder + "/")]
        for entry in matches:
            self.update_folder_exact(entry.filename, entry.sha256, "")
        for directory, _dirs, _files in os.walk(self.folder_path(folder), topdown=False):
            os.rmdir(directory)  # Leave unrelated files intact; report a nonempty folder.
        self.refresh()
        return len(matches) or 1

    def remove(self, entry: BackupEntry):
        self.remove_exact(entry.filename, entry.sha256)

    def update_notes(self, entry: BackupEntry, notes: str):
        self.update_notes_exact(entry.filename, entry.sha256, notes)

    def refresh(self):
        """Reconcile disk paths with metadata, matching external moves only unambiguously."""
        old = [*self._entries, *self._missing]
        before = [asdict(entry) for entry in old]
        by_path = {entry.filename: entry for entry in old}
        by_content_path = {(entry.filename, entry.sha256, entry.size): entry for entry in old}
        files, folders = {}, []

        def scan_error(error):
            raise error  # A failed scan must never look like files were deleted.

        for directory, dirs, names in os.walk(BACKUP_DIR, onerror=scan_error):
            relative = Path(directory).relative_to(BACKUP_DIR).as_posix()
            dirs[:] = sorted(name for name in dirs
                             if not (relative == "." and name.casefold() in _INTERNAL_NAMES))
            for name in list(dirs):
                child = name if relative == "." else f"{relative}/{name}"
                try:
                    self._entry_path(child)
                except ValueError:
                    dirs.remove(name)
                else:
                    folders.append(child)
            for name in sorted(names):
                filename = name if relative == "." else f"{relative}/{name}"
                if (filename.casefold() in _INTERNAL_NAMES
                        or (not name.lower().endswith(".bin") and filename not in by_path)):
                    continue
                try:
                    path = self._entry_path(filename)
                except ValueError:
                    continue
                with open(path, "rb") as stream:
                    data = stream.read()
                    modified = os.fstat(stream.fileno()).st_mtime
                files[filename] = (data, hashlib.sha256(data).hexdigest(), modified)

        matched, remaining = {}, {}
        for filename, record in files.items():
            entry = by_content_path.get((filename, record[1], len(record[0])))
            if entry:
                matched[filename] = entry
            else:
                remaining[filename] = record
        retained_ids = {id(entry) for entry in matched.values()}
        missing = [entry for entry in old if id(entry) not in retained_ids]
        missing_by_digest = defaultdict(list)
        for entry in missing:
            missing_by_digest[(entry.sha256, entry.size)].append(entry)
        destination_counts = Counter(record[1] for record in remaining.values())
        for filename, (data, digest, modified) in remaining.items():
            candidates = missing_by_digest[(digest, len(data))]
            folder = filename.rpartition("/")[0]
            if len(candidates) == destination_counts[digest] == 1:
                entry = candidates[0]
                missing.remove(entry)
                entry.filename, entry.folder = filename, folder
            else:
                entry = self._describe(data, filename, folder=folder)
                entry.date = datetime.datetime.fromtimestamp(modified).isoformat(timespec="seconds")
            matched[filename] = entry
        # Keep old notes/provenance for missing or ambiguously moved files in the index.
        # They are not shown as available images and can be recovered on a later scan.
        present_ids = {id(entry) for entry in matched.values()}
        old_ids = {id(entry) for entry in old}
        entries = [entry for entry in old if id(entry) in present_ids]
        entries.extend(entry for entry in matched.values() if id(entry) not in old_ids)
        self._entries, self._missing = entries, missing
        self._folders = sorted(folders, key=str.casefold)
        if self._index_dirty or before != [asdict(entry) for entry in [*entries, *missing]]:
            self._index_dirty = True
            self._save()

    # ── Persistence ────────────────────────────────────────────────────────

    @staticmethod
    def _write_json(destination, payload):
        temporary = os.path.join(
            os.path.dirname(destination), f".{uuid.uuid4().hex}.tmp")
        try:
            with open(temporary, "x", encoding="utf-8") as stream:
                json.dump(payload, stream, indent=2, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, destination)
        finally:
            try:
                os.remove(temporary)
            except FileNotFoundError:
                pass

    def _recover_pending(self):
        pending_dir = os.path.join(BACKUP_DIR, ".pending")
        if not os.path.exists(pending_dir):
            return
        try:
            for name in sorted(os.listdir(pending_dir)):
                if not name.endswith(".json"):
                    continue
                pending = os.path.join(pending_dir, name)
                with open(pending, "r", encoding="utf-8") as stream:
                    entry = BackupEntry(**json.load(stream))
                path = self._entry_path(entry.filename)
                if (os.path.normcase(path) == os.path.normcase(os.path.realpath(INDEX_FILE))
                        or entry.filename.casefold() == ".pending"):
                    raise ValueError("pending backup filename is reserved")
                if os.path.exists(path):
                    with open(path, "rb") as stream:
                        data = stream.read()
                    if len(data) != entry.size or hashlib.sha256(data).hexdigest() != entry.sha256:
                        raise ValueError(f"pending backup failed its content check: {path}")
                    indexed = [e for e in self._entries if e.filename == entry.filename]
                    if indexed:
                        if len(indexed) != 1 or indexed[0].sha256 != entry.sha256:
                            raise ValueError(f"pending backup conflicts with the index: {path}")
                    else:
                        self._entries.append(entry)
                self._pending_records[pending] = entry
            if self._pending_records:
                self._save()
        except (OSError, TypeError, ValueError) as error:
            raise BackupIndexError(
                f"Backup catalogue recovery could not finish: {error}. "
                f"Images remain at {os.path.abspath(BACKUP_DIR)} and recovery metadata "
                f"at {pending_dir}. Resolve the storage or metadata error and restart to retry."
            ) from error

    def _load(self):
        if not os.path.exists(INDEX_FILE):
            self._legacy = os.path.exists(_folder_index_file())
            return
        try:
            with open(INDEX_FILE, "r", encoding="utf-8") as f:
                raw = json.load(f)
            self._legacy = isinstance(raw, list)
            if not self._legacy:
                if not isinstance(raw, dict) or raw.get("version") != 2:
                    raise ValueError("unsupported catalogue index version")
                raw = raw["entries"]
            if not isinstance(raw, list) or not all(isinstance(row, dict) for row in raw):
                raise ValueError("catalogue entries must be a list of objects")
            fields = set(BackupEntry.__dataclass_fields__)
            loaded = [BackupEntry(**{k: v for k, v in row.items() if k in fields}) for row in raw]
            for entry in loaded:
                path = self._entry_path(entry.filename)
                if not self._legacy:
                    entry.folder = entry.filename.rpartition("/")[0]
                if not entry.sha256 and os.path.exists(path):
                    with open(path, "rb") as stream:
                        data = stream.read()
                    if len(data) != entry.size:
                        raise ValueError(f"legacy catalogue file has changed size: {entry.filename}")
                    entry.sha256 = hashlib.sha256(data).hexdigest()
                    self._index_dirty = True
            self._entries = loaded
        except (OSError, KeyError, TypeError, ValueError) as error:
            raise BackupIndexError(
                f"Backup catalogue index is unreadable: {INDEX_FILE}. "
                "The index and backup files were left unchanged."
            ) from error

    def _migrate_folders(self):
        """Replay a durable migration plan before scanning, without overwriting any BIN."""
        journal = os.path.join(BACKUP_DIR, ".folder-migration.json")
        if not self._legacy and not os.path.exists(journal):
            return
        try:
            if os.path.exists(journal):
                with open(journal, encoding="utf-8") as stream:
                    plan = json.load(stream)
            else:
                stored = []
                if os.path.exists(_folder_index_file()):
                    with open(_folder_index_file(), encoding="utf-8") as stream:
                        stored = json.load(stream)
                    if not isinstance(stored, list):
                        raise ValueError("legacy folders must be a list")
                mapping, used = {}, set()
                for old in [*stored, *(entry.folder for entry in self._entries if entry.folder)]:
                    if not old or old in mapping:
                        continue
                    # Old labels allowed Windows-invalid characters; map them once, visibly.
                    parts = []
                    for part in str(old).split("/"):
                        part = " ".join(part.split()).rstrip(". ")
                        part = "".join("_" if ord(c) < 32 or c in '<>:"\\|?*' else c
                                       for c in part) or "Folder"
                        try:
                            _portable_segment(part)
                        except ValueError:
                            part = "_" + part[:48]
                        parts.append(part)
                    folder = "/".join(parts)
                    if folder.split("/", 1)[0].casefold() in _INTERNAL_NAMES | {"all", "unfiled"}:
                        folder = "_" + folder
                    base, suffix = folder, 1
                    while folder.casefold() in used or os.path.isfile(self.folder_path(folder)):
                        suffix += 1
                        folder = f"{base}_{suffix}"
                    mapping[old] = folder
                    used.add(folder.casefold())
                entries, moves, destinations = [], [], set()
                for entry in self._entries:
                    folder = mapping.get(entry.folder, "")
                    destination = entry.filename
                    if folder and "/" not in entry.filename and os.path.exists(entry.path):
                        self.read_data(entry.filename, entry.sha256)
                        destination = self._unique_name(entry.filename, folder)
                        if destination.casefold() in destinations:
                            raise ValueError("legacy folder destinations conflict")
                        destinations.add(destination.casefold())
                        moves.append([entry.filename, destination, entry.sha256])
                    entries.append(asdict(replace(entry, filename=destination,
                                                  folder=destination.rpartition("/")[0])))
                plan = {"entries": entries, "moves": moves, "folders": list(mapping.values())}
                self._write_json(journal, plan)
            for folder in plan["folders"]:
                os.makedirs(self.folder_path(folder), exist_ok=True)
            for source, destination, digest in plan["moves"]:
                old_path, new_path = self._entry_path(source), self._entry_path(destination)
                if os.path.exists(old_path):
                    if hashlib.sha256(Path(old_path).read_bytes()).hexdigest() != digest:
                        raise ValueError("a migration source changed")
                    if os.path.exists(new_path):
                        raise ValueError("a migration destination already exists")
                    _move_file(old_path, new_path)
                elif not os.path.isfile(new_path) or hashlib.sha256(
                        Path(new_path).read_bytes()).hexdigest() != digest:
                    raise ValueError("a migrated image is missing or changed")
            self._entries = [BackupEntry(**row) for row in plan["entries"]]
            self._legacy = False
            self._save()
            os.remove(journal)
        except (OSError, KeyError, TypeError, ValueError) as error:
            raise BackupIndexError(f"Folder migration could not finish: {error}. "
                                   "Files and migration metadata are retained; restart to retry.") from error

    def _save(self):
        entries = [asdict(e) for e in [*self._entries, *self._missing]]
        self._write_json(INDEX_FILE, entries if self._legacy else {"version": 2, "entries": entries})
        self._index_dirty = False
        for pending in list(self._pending_records):
            try:
                os.remove(pending)
            except FileNotFoundError:
                pass
            except OSError:
                continue  # The committed index is authoritative on the next replay.
            del self._pending_records[pending]
