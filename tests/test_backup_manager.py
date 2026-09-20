import os
import sys
import hashlib
import json
from pathlib import Path
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


def test_catalog_folders_move_images_on_disk(tmp_path, monkeypatch):
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
    assert first.path == original_paths["first.bin"]
    assert Path(second.path) == tmp_path / "backups" / "Stock" / "second.bin"
    assert not Path(original_paths["second.bin"]).exists()
    assert Path(first.path).exists() and Path(second.path).exists()

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
    image = tmp_path / "backups" / "Recovery" / "capture.bin"
    with pytest.raises(backup_manager.BackupIndexError) as failure:
        mgr.add_data(
            bytes(512), image.name, notes="durable original", source="ECU EEPROM Agent",
            variant="MS41.2", ecu_id="test ECU", vin="test VIN", folder="Recovery",
        )
    expected = asdict(mgr.entries[-1])
    assert str(image) in str(failure.value)
    assert isinstance(failure.value.__cause__, PermissionError)
    assert image.read_bytes() == bytes(512)
    pending, = (index.parent / ".pending").glob("*.json")
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
    assert reloaded.read_data(expected["filename"], expected["sha256"]) == bytes(512)
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

    monkeypatch.setattr(backup_manager, "_move_file", fail_publication_or_index)
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

    monkeypatch.setattr(backup_manager, "_move_file", fail_image_publication)
    with pytest.raises(PermissionError):
        mgr.add_data(b"different capture", failed_image.name)
    assert mgr.rename_exact(entry.filename, entry.sha256, entry.filename) is entry
    with pytest.raises(ValueError, match="already exists"):
        mgr.rename_exact(entry.filename, entry.sha256, failed_image.name)
    reloaded = backup_manager.BackupManager()
    assert reloaded.read_data(entry.filename, entry.sha256) == b"stable"


@pytest.mark.parametrize("interruption", ["none", "move", "index"])
def test_legacy_folder_migration_retries_and_preserves_metadata(tmp_path, monkeypatch, interruption):
    mgr = _mgr(tmp_path, monkeypatch)
    first = mgr.add_data(bytes(512), "first.bin", notes="original", source="ECU read", variant="MS41.2")
    second = mgr.add_data(bytes([1]) * 512, "second.bin", notes="second")
    rows = [asdict(first), asdict(second)]
    for row in rows:
        row["folder"] = "Road / Baselines"
    index = tmp_path / "backups" / "index.json"
    index.write_text(json.dumps(rows))
    (tmp_path / "library-folders.json").write_text(json.dumps(["Road / Baselines", "Empty?", "CON"]))
    rename, replace = os.rename, os.replace

    def fail_move(source, destination):
        if str(source).endswith("second.bin"):
            raise PermissionError("interrupted move")
        return rename(source, destination)

    def fail_index(source, destination):
        if str(destination) == str(index):
            raise PermissionError("interrupted index")
        return replace(source, destination)

    if interruption != "none":
        monkeypatch.setattr(os, "rename" if interruption == "move" else "replace",
                            fail_move if interruption == "move" else fail_index)
        with pytest.raises(backup_manager.BackupIndexError, match="migration"):
            backup_manager.BackupManager()
        assert (index.parent / ".folder-migration.json").exists()
        monkeypatch.setattr(os, "rename", rename)
        monkeypatch.setattr(os, "replace", replace)
    migrated = backup_manager.BackupManager()
    assert (index.parent / "Empty_").is_dir()
    assert (index.parent / "_CON").is_dir()
    for old, entry in zip(rows, migrated.entries):
        assert entry.filename == "Road/Baselines/" + old["filename"]
        assert entry.notes == old["notes"] and entry.source == old["source"]
        assert entry.sha256 == old["sha256"] and entry.variant == old["variant"]
        assert not (index.parent / old["filename"]).exists()
        assert len(migrated.read_data(entry.filename, entry.sha256)) == 512
    assert json.loads(index.read_text())["version"] == 2
    assert not (index.parent / ".folder-migration.json").exists()
    assert [asdict(entry) for entry in backup_manager.BackupManager().entries] == [
        asdict(entry) for entry in migrated.entries]


def test_external_move_discovery_edits_and_empty_folders(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path, monkeypatch)
    entry = mgr.add_data(bytes(512), "original.bin", notes="keep", source="ECU read", variant="MS41.2")
    directory = tmp_path / "backups" / "External  folder" / "Nested"
    directory.mkdir(parents=True)
    Path(entry.path).rename(directory / "renamed.BIN")
    (directory / "new.bin").write_bytes(bytes([2]) * 512)
    (directory / "Empty").mkdir()
    (directory / "readme.txt").write_text("not a bin")
    mgr = backup_manager.BackupManager()
    moved = next(e for e in mgr.entries if e.notes == "keep")
    assert moved.filename == "External  folder/Nested/renamed.BIN"
    assert moved.folder == "External  folder/Nested" and moved.source == "ECU read"
    assert moved.variant == "MS41.2"
    assert "External  folder/Nested/Empty" in mgr.folders
    assert len(mgr.entries) == 2
    old_hash = moved.sha256
    Path(moved.path).write_bytes(bytes([3]) * 512)
    mgr.refresh()
    edited = mgr.exact_entry(moved.filename)
    assert edited.sha256 != old_hash and edited.source == "imported"
    assert edited.variant == "Unknown"
    with pytest.raises(ValueError, match="identity changed"):
        mgr.read_data(moved.filename, old_hash)
    assert backup_manager.BackupManager().exact_entry(edited.filename).sha256 == edited.sha256
    Path(edited.path).unlink()
    mgr.refresh()
    assert len(mgr.entries) == 1
    Path(edited.path).write_bytes(bytes(512))
    restored = backup_manager.BackupManager().exact_entry(edited.filename)
    assert restored.notes == "keep" and restored.source == "ECU read"


def test_duplicates_are_addressed_by_relative_path_and_do_not_steal_notes(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path, monkeypatch)
    first = mgr.add_data(bytes(512), "same.bin", folder="A", notes="first")
    second = mgr.add_data(bytes(512), "same.bin", folder="B", notes="second")
    assert first.filename == "A/same.bin" and second.filename == "B/same.bin"
    original_second = Path(second.path)
    mgr.update_folder_exact(first.filename, first.sha256, "B")
    assert first.filename != second.filename and original_second.read_bytes() == bytes(512)
    Path(first.path).rename(tmp_path / "backups" / "external1.bin")
    Path(second.path).rename(tmp_path / "backups" / "external2.bin")
    mgr.refresh()
    assert len(mgr.entries) == 2 and all(not entry.notes for entry in mgr.entries)
    rows = json.loads(Path(backup_manager.INDEX_FILE).read_text())["entries"]
    assert {row["notes"] for row in rows} >= {"first", "second"}
    mgr.remove_exact("external1.bin", first.sha256)
    assert (tmp_path / "backups" / "external2.bin").exists()


def test_migration_collision_does_not_overwrite_existing_file(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path, monkeypatch)
    entry = mgr.add_data(bytes(512), "same.bin", notes="legacy")
    row = asdict(entry)
    row["folder"] = "Stock"
    Path(backup_manager.INDEX_FILE).write_text(json.dumps([row]))
    folder = tmp_path / "backups" / "Stock"
    folder.mkdir()
    (folder / "same.bin").write_bytes(bytes([1]) * 512)
    migrated = backup_manager.BackupManager()
    assert len(migrated.entries) == 2
    legacy = next(e for e in migrated.entries if e.notes == "legacy")
    assert legacy.filename != "Stock/same.bin"
    assert migrated.read_data(legacy.filename, legacy.sha256) == bytes(512)
    assert (folder / "same.bin").read_bytes() == bytes([1]) * 512


@pytest.mark.parametrize("operation", ["move", "rename_folder"])
def test_folder_operations_roll_back_when_index_write_fails(tmp_path, monkeypatch, operation):
    mgr = _mgr(tmp_path, monkeypatch)
    entry = mgr.add_data(bytes(512), "original.bin", folder="A/Child", notes="keep")
    mgr.refresh()
    before = asdict(entry)
    original_save = mgr._save

    def fail():
        raise PermissionError("index locked")

    monkeypatch.setattr(mgr, "_save", fail)
    with pytest.raises(PermissionError):
        if operation == "move":
            mgr.update_folder_exact(entry.filename, entry.sha256, "B")
        else:
            mgr.rename_folder("A", "B")
    assert asdict(entry) == before and Path(entry.path).read_bytes() == bytes(512)
    monkeypatch.setattr(mgr, "_save", original_save)
    assert mgr.rename_folder("A", "C") == 1
    assert entry.filename == "C/Child/original.bin"
    assert mgr.read_data(entry.filename, entry.sha256) == bytes(512)


@pytest.mark.parametrize("folder", ["../escape", "/absolute", "bad?", "CON", ".pending", "bsl", "a/../b"])
def test_real_folder_names_cannot_escape_or_use_reserved_storage(tmp_path, monkeypatch, folder):
    mgr = _mgr(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        mgr.create_folder(folder)


def test_scan_ignores_recovery_storage_and_does_not_prune_on_scan_error(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path, monkeypatch)
    entry = mgr.add_data(bytes(512), "original.bin")
    root = tmp_path / "backups"
    for name in (".pending", "bsl", "native_fast", "transmission"):
        (root / name).mkdir(exist_ok=True)
        (root / name / "private.bin").write_bytes(bytes(512))
    mgr.refresh()
    assert mgr.entries == [entry] and mgr.folders == []
    before = Path(backup_manager.INDEX_FILE).read_bytes()

    def fail_walk(*args, **kwargs):
        kwargs["onerror"](PermissionError("scan unavailable"))
        yield

    monkeypatch.setattr(os, "walk", fail_walk)
    with pytest.raises(PermissionError):
        mgr.refresh()
    assert Path(backup_manager.INDEX_FILE).read_bytes() == before and mgr.entries == [entry]


def test_refresh_retries_failed_metadata_commit(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path, monkeypatch)
    entry = mgr.add_data(bytes(512), "original.bin", notes="keep")
    Path(entry.path).rename(tmp_path / "backups" / "moved.bin")
    write = mgr._write_json
    monkeypatch.setattr(mgr, "_write_json", lambda *args: (_ for _ in ()).throw(PermissionError("locked")))
    with pytest.raises(PermissionError):
        mgr.refresh()
    monkeypatch.setattr(mgr, "_write_json", write)
    mgr.refresh()
    rows = json.loads(Path(backup_manager.INDEX_FILE).read_text())["entries"]
    assert rows[0]["filename"] == "moved.bin" and rows[0]["notes"] == "keep"


def test_concurrent_file_creation_is_never_overwritten(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path, monkeypatch)
    move = backup_manager._move_file

    def another_writer(source, destination):
        Path(destination).write_bytes(b"external")
        move(source, destination)

    monkeypatch.setattr(backup_manager, "_move_file", another_writer)
    with pytest.raises(FileExistsError):
        mgr.add_data(b"capture", "race.bin")
    assert (tmp_path / "backups" / "race.bin").read_bytes() == b"external"
    assert not list((tmp_path / "backups" / ".pending").glob("*.json"))
    entry, = backup_manager.BackupManager().entries
    assert entry.source == "imported"
    assert entry.sha256 == hashlib.sha256(b"external").hexdigest()
