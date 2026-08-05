import os
import sys
import hashlib

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import backup_manager
from tests.conftest import ref


def _mgr(tmp_path, monkeypatch):
    backups = tmp_path / "backups"
    monkeypatch.setattr(backup_manager, "BACKUP_DIR", str(backups))
    monkeypatch.setattr(backup_manager, "INDEX_FILE", str(backups / "index.json"))
    return backup_manager.BackupManager()


def test_backup_dir_is_absolute_and_anchored_to_install_dir():
    assert os.path.isabs(backup_manager.BACKUP_DIR)
    install_dir = os.path.dirname(os.path.abspath(backup_manager.__file__))
    assert os.path.dirname(backup_manager.BACKUP_DIR) == install_dir


def test_add_data_full_rom_records_program_and_cal_variant(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path, monkeypatch)
    data = ref("MS41.3")
    entry = mgr.add_data(data, "ms41_3_test.bin", source="ECU read")
    assert entry.program_variant == "MS41.3"
    assert entry.cal_variant == "MS41.3"
    assert entry.hybrid == ""
    assert entry.sha256 == hashlib.sha256(data).hexdigest()
    assert backup_manager.BackupManager().entries[0].sha256 == entry.sha256


def test_add_data_full_rom_ms41_1_records_program_and_cal_variant(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path, monkeypatch)
    data = ref("MS41.1")
    entry = mgr.add_data(data, "ms41_1_test.bin", source="ECU read")
    assert entry.program_variant == "MS41.1"
    assert entry.cal_variant == "MS41.1"
    assert entry.hybrid == ""


def test_add_data_tune_leaves_program_fields_blank(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path, monkeypatch)
    tune = ref("MS41.3")[0x14000:0x1A000]        # a 24KB cal partial, CPU/DS2 order
    entry = mgr.add_data(tune, "tune_test.bin", source="ECU read")
    assert entry.file_type == "Tune"
    assert entry.program_variant == ""
    assert entry.hybrid == ""


def test_add_data_eeprom_is_catalogued_separately(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path, monkeypatch)
    entry = mgr.add_data(
        bytes(512),
        "ecu-eeprom.bin",
        source="ECU EEPROM Agent",
        variant="MS41.2",
    )

    assert entry.file_type == "EEPROM"
    assert entry.variant == "MS41.2"
    assert entry.source == "ECU EEPROM Agent"
    assert entry.program_variant == entry.cal_variant == entry.cal_id == ""


def test_old_index_entries_without_new_fields_load_cleanly(tmp_path, monkeypatch):
    """An index.json written before this schema change lacks program_variant/cal_variant/
    hybrid — loading it must not crash, and the new fields should default to ''."""
    backups = tmp_path / "backups"
    backups.mkdir()
    monkeypatch.setattr(backup_manager, "BACKUP_DIR", str(backups))
    index_path = backups / "index.json"
    monkeypatch.setattr(backup_manager, "INDEX_FILE", str(index_path))
    old_entry = {
        "filename": "old.bin", "file_type": "Full ROM", "variant": "MS41.3",
        "cs_ok": True, "size": 262144, "date": "2026-01-01T00:00:00", "notes": "",
        "ecu_id": "1406464", "vin": "", "cal_id": "12011110", "source": "imported",
    }
    (backups / "old.bin").write_bytes(b"\xFF" * 262144)
    import json
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump([old_entry], f)

    mgr = backup_manager.BackupManager()
    assert len(mgr.entries) == 1
    assert mgr.entries[0].program_variant == ""
    assert mgr.entries[0].cal_variant == ""
    assert mgr.entries[0].sha256 == ""
