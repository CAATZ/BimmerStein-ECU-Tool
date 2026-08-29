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
from dataclasses import dataclass, asdict
from typing import List

from app_paths import mutable_path


# Anchored to the Flasher install dir, not the process CWD — a relative "backups" path
# scatters the catalogue across whatever directory the tool happens to be launched from.
BACKUP_DIR = str(mutable_path("backups"))
INDEX_FILE = os.path.join(BACKUP_DIR, "index.json")


def _folder_index_file():
    # Keep metadata outside BACKUP_DIR so it can never replace an imported image.
    return os.path.join(os.path.dirname(BACKUP_DIR), "library-folders.json")


@dataclass
class BackupEntry:
    filename:  str
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
    sha256:          str = ""    # immutable catalogue identity; blank for legacy entries
    folder:          str = ""    # logical user folder; files remain flat on disk

    @property
    def path(self) -> str:
        return os.path.join(BACKUP_DIR, self.filename)

    @property
    def display_date(self) -> str:
        try:
            return datetime.datetime.fromisoformat(self.date).strftime("%Y-%m-%d  %H:%M")
        except Exception:
            return self.date

class BackupManager:
    """Manages the ROM backup catalogue on disk."""

    def __init__(self):
        os.makedirs(BACKUP_DIR, exist_ok=True)
        self._entries: List[BackupEntry] = []
        self._folders: List[str] = []
        self._load()
        self._load_folders()

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
        self._folders.append(folder)
        self._folders.sort(key=str.casefold)
        self._save_folders()
        return folder

    def add(self, src_path: str, notes: str = "") -> BackupEntry:
        """Copy a .bin file into backups/ and register it (source='imported')."""
        with open(src_path, "rb") as f:
            data = f.read()
        return self.add_data(data, os.path.basename(src_path),
                             notes=notes, source="imported")

    def add_data(self, data, filename: str, notes: str = "",
                 source: str = "imported", ecu_id: str = "",
                 vin: str = "", variant: str = "", folder: str = "") -> BackupEntry:
        """Register an in-memory image (e.g. a live ECU read) as a backup.

        Writes `data` into backups/ under a unique `filename` and indexes it with
        derived metadata (type, variant, CAL ID, checksum) plus the supplied
        ecu_id/vin/source.  ecu_id falls back to one read from the image.
        """
        from ms41 import MS41ECU
        from checksum import verify_checksum

        folder = self.normalize_folder(folder)
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

        base = self._unique_name(filename)
        destination = self._entry_path(base)
        temporary = os.path.join(BACKUP_DIR, f".{uuid.uuid4().hex}.tmp")
        try:
            with open(temporary, "xb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temporary, destination)
        finally:
            try:
                os.remove(temporary)
            except FileNotFoundError:
                pass

        entry = BackupEntry(
            filename=base, file_type=file_type, variant=variant, cs_ok=cs_ok,
            size=size, date=date, notes=notes, ecu_id=ecu_id, vin=vin,
            cal_id=cal_id, source=source,
            program_variant=program_variant, cal_variant=cal_variant, hybrid=hybrid,
            sha256=hashlib.sha256(data).hexdigest(),
            folder=folder,
        )
        self._entries.append(entry)
        if folder and not any(
                candidate.casefold() == folder.casefold() for candidate in self._folders):
            self._folders.append(folder)
            self._folders.sort(key=str.casefold)
        self._save()
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
        if (not filename or filename in (".", "..") or "\x00" in filename
                or "/" in filename or "\\" in filename):
            raise ValueError("invalid backup filename")
        root = os.path.realpath(BACKUP_DIR)
        path = os.path.realpath(os.path.join(root, filename))
        if os.path.dirname(path) != root:
            raise ValueError("backup path escapes the catalogue")
        return path

    def _unique_name(self, filename: str) -> str:
        base = os.path.basename(filename)
        self._entry_path(base)
        if not os.path.exists(self._entry_path(base)):
            return base
        stem, ext = os.path.splitext(base)
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        suffix = 1
        while True:
            candidate = f"{stem}_{ts}_{suffix}{ext}"
            if not os.path.exists(self._entry_path(candidate)):
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
        if entry.folder:
            self._save_folders()
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
        self._entry_path(replacement)
        if (len(replacement.encode("utf-8")) > 255
                or replacement.endswith((".", " "))
                or any(ord(character) < 32 or character in '<>:"/\\|?*'
                       for character in replacement)
                or replacement.split(".", 1)[0].casefold() in {
                    "con", "prn", "aux", "nul",
                    *(f"com{index}" for index in range(1, 10)),
                    *(f"lpt{index}" for index in range(1, 10)),
                }):
            raise ValueError("filename is not portable")
        if replacement == entry.filename:
            return entry
        if any(candidate.filename.casefold() == replacement.casefold()
               for candidate in self._entries):
            raise ValueError("filename already exists")
        self.read_data(entry.filename, entry.sha256)
        source = self._entry_path(entry.filename)
        destination = self._entry_path(replacement)
        if os.path.exists(destination):
            raise ValueError("filename already exists")
        original = entry.filename
        os.rename(source, destination)
        entry.filename = replacement
        try:
            self._save()
        except Exception:
            entry.filename = original
            os.rename(destination, source)
            raise
        return entry

    @staticmethod
    def normalize_folder(folder: str) -> str:
        folder = " ".join(str(folder).split())
        if not folder:
            return ""
        if len(folder) > 48:
            raise ValueError("folder name is too long")
        if folder.casefold() in {"all", "unfiled"}:
            raise ValueError("folder name is reserved")
        if any(ord(character) < 32 for character in folder):
            raise ValueError("folder name contains control characters")
        return folder

    def update_folder_exact(self, filename: str, sha256: str, folder: str):
        entry = self.exact_entry(filename, sha256)
        entry.folder = self.normalize_folder(folder)
        if entry.folder and not any(
                candidate.casefold() == entry.folder.casefold()
                for candidate in self._folders):
            self._folders.append(entry.folder)
            self._folders.sort(key=str.casefold)
        self._save()
        self._save_folders()
        return entry

    def rename_folder(self, current: str, replacement: str) -> int:
        current = self.normalize_folder(current)
        replacement = self.normalize_folder(replacement)
        if not current or not replacement:
            raise ValueError("folder name is required")
        current_key = current.casefold()
        matches = [entry for entry in self._entries if entry.folder.casefold() == current_key]
        registered = next(
            (folder for folder in self._folders if folder.casefold() == current_key), None,
        )
        if not matches and registered is None:
            raise ValueError("folder was not found")
        if replacement.casefold() != current_key and any(
                folder.casefold() == replacement.casefold() for folder in self._folders):
            raise ValueError("folder already exists")
        for entry in matches:
            entry.folder = replacement
        if registered is not None:
            self._folders[self._folders.index(registered)] = replacement
        else:
            self._folders.append(replacement)
        self._folders.sort(key=str.casefold)
        self._save()
        self._save_folders()
        return len(matches) or 1

    def clear_folder(self, folder: str) -> int:
        folder = self.normalize_folder(folder)
        if not folder:
            raise ValueError("folder name is required")
        folder_key = folder.casefold()
        matches = [entry for entry in self._entries if entry.folder.casefold() == folder_key]
        registered = [
            candidate for candidate in self._folders
            if candidate.casefold() == folder_key
        ]
        if not matches and not registered:
            raise ValueError("folder was not found")
        for entry in matches:
            entry.folder = ""
        self._folders = [
            candidate for candidate in self._folders
            if candidate.casefold() != folder_key
        ]
        self._save()
        self._save_folders()
        return len(matches) or 1

    def remove(self, entry: BackupEntry):
        self.remove_exact(entry.filename, entry.sha256)

    def update_notes(self, entry: BackupEntry, notes: str):
        self.update_notes_exact(entry.filename, entry.sha256, notes)

    def refresh(self):
        """Prune entries whose files have been deleted externally."""
        self._entries = [
            e for e in self._entries
            if os.path.exists(self._entry_path(e.filename))
        ]
        self._save()

    # ── Persistence ────────────────────────────────────────────────────────

    def _load(self):
        if not os.path.exists(INDEX_FILE):
            return
        try:
            with open(INDEX_FILE, "r", encoding="utf-8") as f:
                raw = json.load(f)
            fields = set(BackupEntry.__dataclass_fields__)
            self._entries = [
                BackupEntry(**{k: v for k, v in r.items() if k in fields})
                for r in raw
            ]
            self.refresh()
        except Exception:
            self._entries = []

    def _load_folders(self):
        stored = []
        try:
            with open(_folder_index_file(), "r", encoding="utf-8") as f:
                stored = json.load(f)
            if not isinstance(stored, list):
                stored = []
        except (FileNotFoundError, OSError, ValueError):
            stored = []
        folders = [*stored, *(entry.folder for entry in self._entries if entry.folder)]
        self._folders = []
        for candidate in folders:
            try:
                folder = self.normalize_folder(candidate)
            except ValueError:
                continue
            if folder and not any(
                    existing.casefold() == folder.casefold() for existing in self._folders):
                self._folders.append(folder)
        self._folders.sort(key=str.casefold)

    def _save(self):
        temporary = os.path.join(BACKUP_DIR, f".index-{uuid.uuid4().hex}.tmp")
        try:
            with open(temporary, "x", encoding="utf-8") as f:
                json.dump([asdict(e) for e in self._entries], f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temporary, INDEX_FILE)
        finally:
            try:
                os.remove(temporary)
            except FileNotFoundError:
                pass

    def _save_folders(self):
        destination = _folder_index_file()
        temporary = os.path.join(
            os.path.dirname(destination), f".library-folders-{uuid.uuid4().hex}.tmp",
        )
        try:
            with open(temporary, "x", encoding="utf-8") as f:
                json.dump(self._folders, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temporary, destination)
        finally:
            try:
                os.remove(temporary)
            except FileNotFoundError:
                pass
