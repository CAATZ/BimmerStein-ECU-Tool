import hashlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import backup_manager
from backup_manager import BackupIndexError
from gui import MS41FlashGUI, QMessageBox
from tests.conftest import ref


def _delete_target():
    entry = SimpleNamespace(filename="image.bin")
    return SimpleNamespace(
        _selected_backup=lambda: entry,
        _backup_mgr=SimpleNamespace(remove=Mock()),
        _refresh_backup_table=Mock(),
        _log=Mock(),
    )


@pytest.mark.parametrize("error", [
    FileNotFoundError("Image no longer exists"),
    PermissionError("Image is read-only"),
    ValueError("Image content changed"),
    BackupIndexError("Index could not be saved"),
])
def test_backup_delete_reports_failure_without_success(monkeypatch, error):
    target = _delete_target()
    target._backup_mgr.remove.side_effect = error
    warning = Mock()
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.Yes)
    monkeypatch.setattr(QMessageBox, "warning", warning)

    MS41FlashGUI._on_backup_delete(target)

    warning.assert_called_once_with(target, "Backup Delete Failed", str(error))
    target._refresh_backup_table.assert_not_called()
    target._log.assert_not_called()


@pytest.mark.parametrize("answer", [QMessageBox.No, QMessageBox.Yes])
def test_backup_delete_honors_confirmation(monkeypatch, answer):
    target = _delete_target()
    monkeypatch.setattr(QMessageBox, "question", lambda *_: answer)
    monkeypatch.setattr(QMessageBox, "warning", lambda *_: pytest.fail("Unexpected warning"))

    MS41FlashGUI._on_backup_delete(target)

    if answer == QMessageBox.No:
        target._backup_mgr.remove.assert_not_called()
        target._refresh_backup_table.assert_not_called()
        target._log.assert_not_called()
    else:
        target._backup_mgr.remove.assert_called_once_with(target._selected_backup())
        target._refresh_backup_table.assert_called_once_with()
        target._log.assert_called_once_with("Backup deleted: image.bin", "warn")


@pytest.mark.parametrize("state", ["unchanged", "replaced", "missing"])
def test_backup_flash_uses_the_selected_catalog_bytes(tmp_path, monkeypatch, state):
    backups = tmp_path / "backups"
    monkeypatch.setattr(backup_manager, "BACKUP_DIR", str(backups))
    monkeypatch.setattr(backup_manager, "INDEX_FILE", str(backups / "index.json"))
    manager = backup_manager.BackupManager()
    data = ref("MS41.1")
    entry = manager.add_data(data, "image.bin")
    if state == "replaced":
        Path(entry.path).write_bytes(ref("MS41.2"))
    elif state == "missing":
        Path(entry.path).unlink()
    calls = []

    def dispatch(image, filename, **options):
        calls.append((hashlib.sha256(image).hexdigest(), filename, options))
    target = SimpleNamespace(
        _selected_backup=lambda: entry,
        _ds2=object(),
        _backup_mgr=manager,
        _is_ms42_ms43=lambda: False,
        _ds2_write_full=dispatch,
    )
    warning = Mock()
    monkeypatch.setattr(QMessageBox, "warning", warning)

    MS41FlashGUI._on_backup_flash(target)

    if state == "unchanged":
        assert calls == [(hashlib.sha256(data).hexdigest(), "image.bin", {
            "require_boot_write": False, "preserve_boot_identity": None})]
        warning.assert_not_called()
    else:
        assert calls == []
        warning.assert_called_once()
        assert warning.call_args.args[:2] == (target, "Backup Unavailable")
