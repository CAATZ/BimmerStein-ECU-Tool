import os
import sys
import hashlib
import json
from dataclasses import asdict
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import backup_manager
from tests.conftest import ref


def _mgr(tmp_path, monkeypatch):
    backups = tmp_path / "backups"
    monkeypatch.setattr(backup_manager, "BACKUP_DIR", str(backups))
    monkeypatch.setattr(backup_manager, "INDEX_FILE", str(backups / "index.json"))
    return backup_manager.BackupManager()


def test_backup_dir_is_absolute_and_anchored_to_configured_data_root():
    assert os.path.isabs(backup_manager.BACKUP_DIR)
    install_dir = os.path.dirname(os.path.abspath(backup_manager.__file__))
    expected_root = os.environ.get("BIMMERSTEIN_DATA_DIR") or install_dir
    assert os.path.dirname(backup_manager.BACKUP_DIR) == expected_root


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
    digest = hashlib.sha256(b"\xFF" * 262144).hexdigest()
    assert mgr.entries[0].sha256 == digest
    assert mgr.entries[0].folder == ""
    assert mgr.read_data("old.bin", digest) == b"\xFF" * 262144


@pytest.mark.parametrize("broken", [b'{"incomplete"', b'["not an object"]'])
def test_unreadable_index_is_not_replaced_or_treated_as_empty(
        tmp_path, monkeypatch, broken):
    backups = tmp_path / "backups"
    backups.mkdir()
    image = backups / "important.bin"
    image.write_bytes(b"important")
    index_path = backups / "index.json"
    index_path.write_bytes(broken)
    monkeypatch.setattr(backup_manager, "BACKUP_DIR", str(backups))
    monkeypatch.setattr(backup_manager, "INDEX_FILE", str(index_path))

    with pytest.raises(backup_manager.BackupIndexError, match="left unchanged"):
        backup_manager.BackupManager()

    assert index_path.read_bytes() == broken
    assert image.read_bytes() == b"important"


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
    direct = mgr.add_data(
        bytes([2]) * 512, "direct.bin", variant="MS41.2", folder=" Direct imports ",
    )
    original_paths = {first.filename: first.path, second.filename: second.path}

    assert direct.folder == "Direct imports"
    assert "Direct imports" in mgr.folders
    mgr.remove_exact(direct.filename, direct.sha256)
    assert "Direct imports" in backup_manager.BackupManager().folders
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


def test_empty_catalog_folders_persist_until_removed(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path, monkeypatch)
    entry = mgr.add_data(bytes(512), "library-folders.json", variant="MS41.2")

    assert mgr.create_folder(" Track  cars ") == "Track cars"
    assert backup_manager.BackupManager().folders == ["Track cars"]
    assert mgr.read_data(entry.filename, entry.sha256) == bytes(512)

    mgr.update_folder_exact(entry.filename, entry.sha256, "Track cars")
    mgr.remove_exact(entry.filename, entry.sha256)
    assert backup_manager.BackupManager().folders == ["Track cars"]

    assert mgr.rename_folder("Track cars", "Race day") == 1
    assert backup_manager.BackupManager().folders == ["Race day"]
    assert mgr.clear_folder("Race day") == 1
    assert backup_manager.BackupManager().folders == []


def test_first_import_cannot_replace_catalog_index(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path, monkeypatch)
    data = bytes(512)

    entry = mgr.add_data(data, "index.json", variant="MS41.2")

    assert entry.filename != "index.json"
    assert mgr.read_data(entry.filename, entry.sha256) == data
    reloaded = backup_manager.BackupManager()
    assert reloaded.read_data(entry.filename, entry.sha256) == data


def test_failed_index_commit_recovers_exact_image_and_metadata(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path, monkeypatch)
    original = mgr.add_data(b"existing", "existing.bin")
    index = tmp_path / "backups" / "index.json"
    original_index = index.read_bytes()
    replace = os.replace

    def fail_index(source, destination):
        if os.fspath(destination) == str(index):
            raise PermissionError("index locked")
        return replace(source, destination)

    monkeypatch.setattr(os, "replace", fail_index)
    image = tmp_path / "backups" / "capture.bin"
    with pytest.raises(backup_manager.BackupIndexError) as failure:
        mgr.add_data(
            bytes(512), image.name, notes="durable original", source="ECU EEPROM Agent",
            variant="MS41.2", ecu_id="test ECU", vin="test VIN", folder="Recovery",
        )
    expected = asdict(mgr.entries[-1])
    assert str(image) in str(failure.value)
    assert isinstance(failure.value.__cause__, PermissionError)
    assert image.read_bytes() == bytes(512)
    pending, = (image.parent / ".pending").glob("*.json")
    assert json.loads(pending.read_text()) == expected
    assert index.read_bytes() == original_index

    with pytest.raises(backup_manager.BackupIndexError, match="restart to retry"):
        backup_manager.BackupManager()
    assert pending.exists() and image.read_bytes() == bytes(512)
    assert index.read_bytes() == original_index

    monkeypatch.setattr(os, "replace", replace)
    reloaded = backup_manager.BackupManager()
    assert [asdict(entry) for entry in reloaded.entries] == [asdict(original), expected]
    assert reloaded.folders == ["Recovery"]
    assert reloaded.read_data(image.name, expected["sha256"]) == bytes(512)
    assert not pending.exists()
    assert len(backup_manager.BackupManager().entries) == 2


@pytest.mark.parametrize("damage", ["malformed", "image_changed", "path_escape", "index_corrupt"])
def test_pending_recovery_does_not_guess_or_replace_corrupt_data(tmp_path, monkeypatch, damage):
    mgr = _mgr(tmp_path, monkeypatch)
    entry = mgr.add_data(b"original", "capture.bin", notes="original notes")
    index = tmp_path / "backups" / "index.json"
    pending = index.parent / ".pending" / "interrupted.json"
    metadata = asdict(entry)
    index.write_text("[]")
    if damage == "path_escape":
        metadata["filename"] = "../capture.bin"
    pending.write_text("{" if damage == "malformed" else json.dumps(metadata))
    if damage == "image_changed":
        (index.parent / entry.filename).write_bytes(b"modified")
    if damage == "index_corrupt":
        index.write_text("{")
    before = index.read_bytes(), pending.read_bytes()

    with pytest.raises(backup_manager.BackupIndexError):
        backup_manager.BackupManager()

    assert (index.read_bytes(), pending.read_bytes()) == before


@pytest.mark.parametrize("deleted", [False, True])
def test_stale_pending_record_preserves_newer_index_or_deleted_image(tmp_path, monkeypatch, deleted):
    mgr = _mgr(tmp_path, monkeypatch)
    entry = mgr.add_data(b"original", "capture.bin", notes="old notes")
    pending = tmp_path / "backups" / ".pending" / "stale.json"
    pending.write_text(json.dumps(asdict(entry)))
    if deleted:
        mgr.remove_exact(entry.filename, entry.sha256)
    else:
        mgr.update_notes(entry, "new notes")

    reloaded = backup_manager.BackupManager()

    assert not pending.exists()
    assert [entry.notes for entry in reloaded.entries] == ([] if deleted else ["new notes"])


def test_pending_metadata_must_be_saved_before_publishing_image(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path, monkeypatch)

    def fail_metadata(*args):
        raise PermissionError("storage unavailable")

    monkeypatch.setattr(mgr, "_write_json", fail_metadata)
    with pytest.raises(PermissionError):
        mgr.add_data(b"original", "capture.bin")
    assert not (tmp_path / "backups" / "capture.bin").exists()
    assert mgr.entries == []


def test_pending_directory_name_is_reserved_for_import_and_rename(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path, monkeypatch)
    entry = mgr.add_data(b"original", ".pending")
    assert entry.filename != ".pending"
    assert mgr.read_data(entry.filename, entry.sha256) == b"original"
    with pytest.raises(ValueError, match="portable"):
        mgr.rename_exact(entry.filename, entry.sha256, ".pending")


def test_retry_after_failed_image_publication_keeps_pending_identities_separate(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path, monkeypatch)
    replace = os.replace
    first_image = tmp_path / "backups" / "capture.bin"
    index = first_image.parent / "index.json"

    def fail_publication_or_index(source, destination):
        if os.fspath(destination) in (str(first_image), str(index)):
            raise PermissionError("storage unavailable")
        return replace(source, destination)

    monkeypatch.setattr(os, "replace", fail_publication_or_index)
    with pytest.raises(PermissionError):
        mgr.add_data(b"first capture", first_image.name)
    with pytest.raises(backup_manager.BackupIndexError):
        mgr.add_data(b"second capture", first_image.name)

    monkeypatch.setattr(os, "replace", replace)
    reloaded = backup_manager.BackupManager()
    entry, = reloaded.entries
    assert reloaded.read_data(entry.filename, entry.sha256) == b"second capture"
    assert not first_image.exists()
    assert not list((first_image.parent / ".pending").glob("*.json"))


def test_rename_cannot_reuse_an_unresolved_pending_filename(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path, monkeypatch)
    entry = mgr.add_data(b"stable", "stable.bin")
    replace = os.replace
    failed_image = tmp_path / "backups" / "capture.bin"

    def fail_image_publication(source, destination):
        if os.fspath(destination) == str(failed_image):
            raise PermissionError("image unavailable")
        return replace(source, destination)

    monkeypatch.setattr(os, "replace", fail_image_publication)
    with pytest.raises(PermissionError):
        mgr.add_data(b"different capture", failed_image.name)
    assert mgr.rename_exact(entry.filename, entry.sha256, entry.filename) is entry
    with pytest.raises(ValueError, match="already exists"):
        mgr.rename_exact(entry.filename, entry.sha256, failed_image.name)
    reloaded = backup_manager.BackupManager()
    assert reloaded.read_data(entry.filename, entry.sha256) == b"stable"
