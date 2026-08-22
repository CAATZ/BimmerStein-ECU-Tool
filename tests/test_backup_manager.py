import os
import sys
import hashlib
import pytest

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
    assert mgr.entries[0].folder == ""


def test_catalog_exact_crud_is_content_checked_and_collision_safe(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path, monkeypatch)
    first = mgr.add_data(bytes(512), "ecu.bin", notes="before", variant="MS41.2")
    second = mgr.add_data(bytes([1]) * 512, "ecu.bin", variant="MS41.2")
    third = mgr.add_data(bytes([2]) * 512, "ecu.bin", variant="MS41.2")

    assert len({first.filename, second.filename, third.filename}) == 3
    assert mgr.read_data(first.filename, first.sha256) == bytes(512)
    assert mgr.update_notes_exact(first.filename, first.sha256, "after").notes == "after"
    old_name = first.filename
    assert mgr.rename_exact(first.filename, first.sha256, "Daily baseline.bin").filename == \
        "Daily baseline.bin"
    assert not (tmp_path / "backups" / old_name).exists()
    assert mgr.read_data(first.filename, first.sha256) == bytes(512)
    assert not list((tmp_path / "backups").glob(".*.tmp"))

    with pytest.raises(ValueError, match="identity changed"):
        mgr.read_data(first.filename, "f" * 64)
    with pytest.raises(ValueError, match="filename"):
        mgr.exact_entry("../ecu.bin", first.sha256)
    with pytest.raises(ValueError, match="portable"):
        mgr.rename_exact(first.filename, first.sha256, "bad?.bin")
    with pytest.raises(ValueError, match="already exists"):
        mgr.rename_exact(first.filename, first.sha256, second.filename)

    mgr.remove_exact(first.filename, first.sha256)
    assert not (tmp_path / "backups" / first.filename).exists()
    assert [entry.filename for entry in backup_manager.BackupManager().entries] == [
        second.filename,
        third.filename,
    ]


def test_catalog_folders_change_metadata_without_moving_images(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path, monkeypatch)
    first = mgr.add_data(bytes(512), "first.bin", variant="MS41.2")
    second = mgr.add_data(bytes([1]) * 512, "second.bin", variant="MS41.2")
    original_paths = {first.filename: first.path, second.filename: second.path}

    assert mgr.update_folder_exact(first.filename, first.sha256, " Track  cars ").folder == \
        "Track cars"
    mgr.update_folder_exact(second.filename, second.sha256, "Stock")
    assert mgr.rename_folder("Track cars", "Race day") == 1
    assert mgr.clear_folder("Race day") == 1
    assert mgr.exact_entry(first.filename, first.sha256).folder == ""
    assert mgr.exact_entry(second.filename, second.sha256).folder == "Stock"
    assert {first.filename: first.path, second.filename: second.path} == original_paths
    assert all(os.path.exists(path) for path in original_paths.values())

    with pytest.raises(ValueError, match="reserved"):
        mgr.update_folder_exact(first.filename, first.sha256, "Unfiled")
    mgr.update_folder_exact(first.filename, first.sha256, "Other")
    with pytest.raises(ValueError, match="already exists"):
        mgr.rename_folder("Other", "stock")
