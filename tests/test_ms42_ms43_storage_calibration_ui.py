"""Offline calibration-backed storage editing through the real Qt dialog."""

from copy import deepcopy
import os
from pathlib import Path
import subprocess
import sys

import pytest
from PyQt5.QtCore import QPoint
from PyQt5.QtWidgets import QApplication, QDialog, QFileDialog

from gui_ms42_ms43_storage import _StorageBackupDialog
from ms42_ms43_storage_edit import editable_fields, edit_storage
from tests.test_ms42_ms43_desktop import connect, window as window
from tests.test_ms42_ms43_storage_calibration import calibrated
from tests.test_ms42_ms43_storage_editor_ui import forbid_external_actions


def load_context(dialog, path, monkeypatch):
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(path), ""))
    dialog.calibration_button.click()


@pytest.mark.parametrize("family,extra", (("MS42", 14), ("MS43", 8)))
def test_loading_context_reveals_only_backend_fields_without_mutation(
        window, monkeypatch, tmp_path, family, extra):
    source, calibration = calibrated(family)
    before = deepcopy(source)
    path = tmp_path / "matching-calibration.bin"
    path.write_bytes(calibration)
    forbid_external_actions(window, monkeypatch)
    dialog = _StorageBackupDialog(window, source)
    original_rows = dialog.fields.rowCount()
    load_context(dialog, path, monkeypatch)
    expected = editable_fields(source, calibration=calibration)
    assert dialog.fields.rowCount() == original_rows + extra == len(expected)
    assert set(dialog._editors) == {field["offset"] for field in expected}
    assert dialog._calibration == calibration
    assert not dialog.apply_button.isEnabled() and not dialog._changes()
    assert "matching-calibration.bin" in dialog.calibration_note.text()
    assert source == before and path.read_bytes() == calibration


@pytest.mark.parametrize("family", ("MS42", "MS43"))
def test_apply_uses_context_and_updates_only_loaded_snapshot(
        window, monkeypatch, tmp_path, family):
    import ms42_ms43_storage_edit as core

    source, calibration = calibrated(family)
    before = deepcopy(source)
    backup_path = tmp_path / "original-backup.bin"
    backup_path.write_bytes(source["data"])
    cal_path = tmp_path / "matching.bin"
    cal_path.write_bytes(calibration)
    offset = 0x51A if family == "MS42" else 0x286
    expected = edit_storage(source, {offset: 130}, calibration=calibration)
    window._ms42_ms43_show_storage(source, str(backup_path))
    forbid_external_actions(window, monkeypatch)
    calls = []

    def record(snapshot, changes, *, calibration=None):
        calls.append((snapshot, dict(changes), calibration))
        return edit_storage(snapshot, changes, calibration=calibration)

    def apply(dialog):
        load_context(dialog, cal_path, monkeypatch)
        dialog._editors[offset][1].set_raw_value(130)
        assert dialog.apply_button.isEnabled()
        dialog.apply_button.click()
        return dialog.result()

    monkeypatch.setattr(core, "edit_storage", record)
    monkeypatch.setattr(QDialog, "exec_", apply)
    window._ms42_ms43_view_storage()
    assert calls == [(source, {offset: 130}, calibration)]
    assert window._ms42_ms43_storage_snapshot == expected
    assert source == before and backup_path.read_bytes() == source["data"]
    assert cal_path.read_bytes() == calibration
    assert window._ms42_ms43_storage_source == str(backup_path) + " (edited)"


@pytest.mark.parametrize("family", ("MS42", "MS43"))
@pytest.mark.parametrize("invalid", ("size", "software", "unreadable"))
def test_invalid_context_preserves_loaded_context_and_draft(
        window, monkeypatch, tmp_path, family, invalid):
    source, calibration = calibrated(family)
    path = tmp_path / "valid.bin"
    path.write_bytes(calibration)
    forbid_external_actions(window, monkeypatch)
    dialog = _StorageBackupDialog(window, source)
    load_context(dialog, path, monkeypatch)
    offset = 0x51A if family == "MS42" else 0x286
    dialog._editors[offset][1].set_raw_value(130)
    editors = dict(dialog._editors)
    note = dialog.calibration_note.text()
    wrong = bytearray(calibration)
    if invalid == "size":
        wrong = wrong[:-1]
    elif invalid == "software":
        wrong[8] ^= 1
    bad_path = tmp_path / "invalid.bin"
    if invalid != "unreadable":
        bad_path.write_bytes(wrong)
    load_context(dialog, bad_path, monkeypatch)
    assert dialog._calibration == calibration
    assert dialog._editors == editors and dialog._changes() == {offset: 130}
    assert dialog.calibration_note.text() == note
    assert dialog.apply_button.isEnabled()
    assert dialog.edited_snapshot is None and dialog._snapshot == source


@pytest.mark.parametrize("family", ("MS42", "MS43"))
def test_reversed_range_excludes_only_affected_field_and_preserves_other_drafts(
        window, monkeypatch, tmp_path, family):
    source, calibration = calibrated(family)
    before = deepcopy(source)
    path = tmp_path / "valid.bin"
    path.write_bytes(calibration)
    forbid_external_actions(window, monkeypatch)
    dialog = _StorageBackupDialog(window, source)
    load_context(dialog, path, monkeypatch)
    affected, idle = (0x51A, 0x514) if family == "MS42" else (0x286, 0x280)
    dialog._editors[affected][1].set_raw_value(130)
    dialog._editors[idle][1].set_raw_value(9)
    original_fields = set(dialog._editors)
    changed = bytearray(calibration)
    minimum, maximum = (0x300, 0x2FF) if family == "MS42" else (0xC21, 0xC20)
    changed[minimum], changed[maximum] = 140, 100
    revised_path = tmp_path / "reversed-range.bin"
    revised_path.write_bytes(changed)
    load_context(dialog, revised_path, monkeypatch)

    assert dialog._calibration == bytes(changed)
    assert set(dialog._editors) == original_fields - {affected}
    assert dialog._changes() == {idle: 9} and dialog.apply_button.isEnabled()
    assert "reversed-range.bin" in dialog.calibration_note.text()
    assert dialog.edited_snapshot is None and dialog._snapshot == source == before
    with pytest.raises(ValueError, match="not editable"):
        edit_storage(source, {affected: 130}, calibration=changed)
    dialog._apply()
    assert dialog.edited_snapshot == edit_storage(source, {idle: 9}, calibration=changed)
    assert source == before and path.read_bytes() == calibration
    assert revised_path.read_bytes() == bytes(changed)


@pytest.mark.parametrize("family", ("MS42", "MS43"))
def test_reloading_context_retains_drafts_and_rechecks_new_limits(
        window, monkeypatch, tmp_path, family):
    source, calibration = calibrated(family)
    offset, idle = (0x51A, 0x514) if family == "MS42" else (0x286, 0x280)
    path = tmp_path / "first.bin"
    path.write_bytes(calibration)
    dialog = _StorageBackupDialog(window, source)
    dialog._editors[idle][1].set_raw_value(9)
    if family == "MS43":
        editor = dialog._editors[0x3E2][1]
        editor.setCurrentIndex(editor.findData(2))
    load_context(dialog, path, monkeypatch)
    assert dialog._editors[idle][1].raw_value() == 9
    if family == "MS43":
        assert dialog._editors[0x3E2][1].currentData() == 2
    dialog._editors[offset][1].set_raw_value(129)
    changed = bytearray(calibration)
    minimum, maximum = (0x300, 0x2FF) if family == "MS42" else (0xC21, 0xC20)
    changed[minimum], changed[maximum] = 130, 140
    new_path = tmp_path / "narrower.bin"
    new_path.write_bytes(changed)
    load_context(dialog, new_path, monkeypatch)
    assert dialog._calibration == bytes(changed)
    assert dialog._editors[idle][1].raw_value() == 9
    assert dialog._editors[offset][1].raw_value() == 129  # Invalid drafts must not be silently clamped.
    assert not dialog.apply_button.isEnabled() and "131 to 140" in dialog.note.text()
    dialog._editors[offset][1].set_raw_value(135)
    assert dialog.apply_button.isEnabled()
    assert dialog._changes()[offset] == 135
    if family == "MS43":
        assert dialog._editors[0x3E2][1].currentData() == 2


def test_cancelled_file_picker_preserves_context_and_draft(window, monkeypatch, tmp_path):
    source, calibration = calibrated("MS43")
    path = tmp_path / "valid.bin"
    path.write_bytes(calibration)
    dialog = _StorageBackupDialog(window, source)
    load_context(dialog, path, monkeypatch)
    dialog._editors[0x286][1].set_raw_value(130)
    note = dialog.calibration_note.text()
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: ("", ""))
    dialog.calibration_button.click()
    assert dialog._calibration == calibration and dialog._changes() == {0x286: 130}
    assert dialog.calibration_note.text() == note


def test_reserved_postcat_value_is_rejected_before_apply(window, monkeypatch, tmp_path):
    source, calibration = calibrated("MS43")
    path = tmp_path / "valid.bin"
    path.write_bytes(calibration)
    dialog = _StorageBackupDialog(window, source)
    load_context(dialog, path, monkeypatch)
    dialog._editors[0x2F2][1].set_raw_value(-1)
    assert not dialog.apply_button.isEnabled()
    assert "reserved" in dialog.note.text()
    dialog._apply()
    assert dialog.edited_snapshot is None
    dialog._editors[0x2F2][1].set_raw_value(-2)
    assert dialog.apply_button.isEnabled()


@pytest.mark.parametrize("timing", ("before_open", "before_apply", "before_load"))
def test_interrupted_restore_disables_context_loading_and_draft_application(
        window, monkeypatch, tmp_path, timing):
    from ms42_ms43 import WriteState

    ecu = connect(window, "MS43")
    source, calibration = calibrated("MS43")
    window._ms42_ms43_show_storage(source, "Restore target")
    state = WriteState("MS43", "eeprom_restore", destructive_started=True)
    if timing == "before_open":
        ecu.write_state = state
    dialog = _StorageBackupDialog(window, source)
    path = tmp_path / "valid.bin"
    path.write_bytes(calibration)
    if timing != "before_open":
        load_context(dialog, path, monkeypatch)
        dialog._editors[0x286][1].set_raw_value(130)
        assert dialog.apply_button.isEnabled()
        ecu.write_state = state
        if timing == "before_apply":
            dialog._apply()
        else:
            monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: pytest.fail("No file picker while restore owns the backup"))
            dialog._load_calibration()
    assert not dialog.apply_button.isEnabled() and not dialog.calibration_button.isEnabled()
    assert dialog.fields.rowCount() == 0 and "finish recovery" in dialog.note.text()
    assert dialog.edited_snapshot is None and window._ms42_ms43_storage_snapshot == source


@pytest.mark.parametrize("family", ("MS42", "MS43"))
def test_context_and_draft_controls_fit_resized_dialog(window, monkeypatch, tmp_path, family):
    source, calibration = calibrated(family)
    path = tmp_path / f"{family}_matching_calibration_saved_from_this_ECU.bin"
    path.write_bytes(calibration)
    dialog = _StorageBackupDialog(window, source)
    load_context(dialog, path, monkeypatch)
    dialog.tabs.setCurrentIndex(1)
    dialog.show()
    ratio = dialog.devicePixelRatioF()
    for width, height in ((840, 620), (600, 420), (round(1000 / ratio), round(780 / ratio))):
        dialog.resize(width, height)
        QApplication.processEvents()
        assert dialog.fields.horizontalScrollBar().maximum() == 0
        for control in (dialog.calibration_button, dialog.calibration_note, dialog.apply_button):
            bounds = control.rect().translated(control.mapTo(dialog, QPoint()))
            assert dialog.rect().contains(bounds)
            assert control.visibleRegion().boundingRect() == control.rect()


@pytest.mark.parametrize("scale", ("1", "1.25", "1.5", "2"))
def test_loaded_context_at_independent_qt_scales(tmp_path, scale):
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", QT_SCALE_FACTOR=scale,
               QT_AUTO_SCREEN_SCALE_FACTOR="0", QT_FONT_DPI="96")
    result = subprocess.run(
        [sys.executable, "-m", "pytest", f"{__file__}::test_context_and_draft_controls_fit_resized_dialog",
         "-q", f"--basetemp={tmp_path / 'child'}"],
        cwd=Path(__file__).resolve().parents[1], env=env, capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr
