"""
backup_manager.py — ROM backup catalogue for BimmerStein ECU Tool.

Copies .bin files into a 'backups/' directory and maintains a JSON index
storing metadata: date, variant, checksum status, file type, and user notes.
"""

import os
import json
import datetime
import hashlib
from dataclasses import dataclass, asdict
from typing import List

from app_paths import mutable_path


# Anchored to the Flasher install dir, not the process CWD — a relative "backups" path
# scatters the catalogue across whatever directory the tool happens to be launched from.
BACKUP_DIR = str(mutable_path("backups"))
INDEX_FILE = os.path.join(BACKUP_DIR, "index.json")


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
        self._load()

    # ── Public API ─────────────────────────────────────────────────────────

    @property
    def entries(self) -> List[BackupEntry]:
        return list(self._entries)

    def add(self, src_path: str, notes: str = "") -> BackupEntry:
        """Copy a .bin file into backups/ and register it (source='imported')."""
        with open(src_path, "rb") as f:
            data = f.read()
        return self.add_data(data, os.path.basename(src_path),
                             notes=notes, source="imported")

    def add_data(self, data, filename: str, notes: str = "",
                 source: str = "imported", ecu_id: str = "",
                 vin: str = "", variant: str = "") -> BackupEntry:
        """Register an in-memory image (e.g. a live ECU read) as a backup.

        Writes `data` into backups/ under a unique `filename` and indexes it with
        derived metadata (type, variant, CAL ID, checksum) plus the supplied
        ecu_id/vin/source.  ecu_id falls back to one read from the image.
        """
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

        base = self._unique_name(filename)
        with open(os.path.join(BACKUP_DIR, base), "wb") as f:
            f.write(data)

        entry = BackupEntry(
            filename=base, file_type=file_type, variant=variant, cs_ok=cs_ok,
            size=size, date=date, notes=notes, ecu_id=ecu_id, vin=vin,
            cal_id=cal_id, source=source,
            program_variant=program_variant, cal_variant=cal_variant, hybrid=hybrid,
            sha256=hashlib.sha256(data).hexdigest(),
        )
        self._entries.append(entry)
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

    @staticmethod
    def _unique_name(filename: str) -> str:
        base = os.path.basename(filename)
        if os.path.exists(os.path.join(BACKUP_DIR, base)):
            stem, ext = os.path.splitext(base)
            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            base = f"{stem}_{ts}{ext}"
        return base

    def remove(self, entry: BackupEntry):
        try:
            os.remove(entry.path)
        except OSError:
            pass
        self._entries = [e for e in self._entries if e.filename != entry.filename]
        self._save()

    def update_notes(self, entry: BackupEntry, notes: str):
        entry.notes = notes
        self._save()

    def refresh(self):
        """Prune entries whose files have been deleted externally."""
        self._entries = [e for e in self._entries if os.path.exists(e.path)]
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

    def _save(self):
        with open(INDEX_FILE, "w", encoding="utf-8") as f:
            json.dump([asdict(e) for e in self._entries], f, indent=2, ensure_ascii=False)
