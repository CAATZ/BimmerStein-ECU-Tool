"""Storage editor actions remain local and preserve the native edit contracts."""

import os
from pathlib import Path
import subprocess
import sys

import pytest
from PyQt5.QtCore import QPoint, QPointF, Qt
from PyQt5.QtGui import QWheelEvent
from PyQt5.QtWidgets import QApplication, QFileDialog

from checksum import _crc
from gui_ms42_ms43_storage import _StorageBackupDialog
from ms42_ms43_storage import make_snapshot
from ms42_ms43_storage_edit import edit_storage
from tests.test_ms42_ms43_desktop import window as window
from tests.test_ms42_ms43_storage_editor_ui import storage_snapshot, forbid_external_actions
from tests.test_ms42_ms43_storage_inspection import put_record


@pytest.mark.parametrize("family", ("MS42", "MS43"))
def test_draft_undo_redo_and_new_branch_preserve_source(window, monkeypatch, family):
    source = storage_snapshot(family)
    forbid_external_actions(window, monkeypatch)
    dialog = _StorageBackupDialog(window, source)
    offset = 0x114 if family == "MS42" else 0x280
    assert not dialog.undo_button.isEnabled() and not dialog.redo_button.isEnabled()
    dialog._editors[offset][1].set_raw_value(21)
    dialog._editors[offset + 2][1].set_raw_value(42)
    dialog.undo_button.click()
    assert dialog._changes() == {offset: 21}
    dialog.undo_button.click()
    assert dialog._changes() == {} and not dialog.apply_button.isEnabled()
    dialog.redo_button.click()
    assert dialog._changes() == {offset: 21}
    dialog._editors[offset + 2][1].set_raw_value(55)
    assert not dialog.redo_button.isEnabled()
    dialog.apply_button.click()
    assert dialog.edited_snapshot == edit_storage(source, {offset: 21, offset + 2: 55})
    assert source == storage_snapshot(family)


def test_knock_grid_and_field_drafts_stay_in_sync_without_clamping(window, monkeypatch):
    source = storage_snapshot(knock=True)
    forbid_external_actions(window, monkeypatch)
    dialog = _StorageBackupDialog(window, source)
    assert dialog.knock_table.item(15, 3).text() == "47.625"
    dialog.knock_table.item(2, 1).setText("0")
    assert dialog._changes() == {0x2AD: 128}
    assert dialog._editors[0x2AD][1].raw_value() == 128
    dialog._editors[0x2AD][1].set_raw_value(33)
    assert dialog.knock_table.item(2, 1).text() == "-35.625"
    dialog.undo_button.click()
    assert dialog.knock_table.item(2, 1).text() == "0.000"
    dialog.knock_table.item(2, 1).setText("0.375")
    assert dialog.knock_table.item(2, 1).text() == "0.000"
    dialog.apply_button.click()
    assert dialog.edited_snapshot["data"][0x2E3] == 255
    assert dialog.edited_snapshot == edit_storage(source, {0x2AD: 128})


def test_editor_comparison_does_not_change_pending_draft(window, monkeypatch, tmp_path):
    source = storage_snapshot()
    other = edit_storage(source, {0x280: 7})
    path = tmp_path / "comparison.bin"
    path.write_bytes(other["data"])
    forbid_external_actions(window, monkeypatch)
    dialog = _StorageBackupDialog(window, source)
    dialog._editors[0x282][1].set_raw_value(12)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(path), ""))
    dialog.compare_button.click()
    assert dialog._changes() == {0x282: 12} and dialog.edited_snapshot is None
    text = dialog.comparison.toPlainText()
    assert "Stored idle correction 1" in text and "0.0107 %" in text
    assert "Stored idle correction 2" in text and "0.0183 %" in text
    dialog.undo_button.click()
    assert not dialog._changes()
    assert "draft changed" in dialog.comparison.toPlainText()
    dialog.compare_button.click()
    dialog._editors[0x280][1].set_raw_value(6)
    assert "draft changed" in dialog.comparison.toPlainText()


@pytest.mark.parametrize("family", ("MS42", "MS43"))
def test_explicit_record_repair_is_undoable_and_does_not_rewrite_payload(window, monkeypatch, family):
    source = storage_snapshot(family)
    raw = bytearray(source["data"])
    offset = 0x114 if family == "MS42" else 0x280
    raw[offset + 8] ^= 1
    if family == "MS42":
        raw[0x3FE:0x400] = _crc(raw[:0x3E0], 15).to_bytes(2, "little")
    source = make_snapshot(family, source["kind"], raw, software_id=source["software_id"])
    forbid_external_actions(window, monkeypatch)
    dialog = _StorageBackupDialog(window, source)
    assert offset not in dialog._editors
    row = next(i for i in range(dialog.checks.rowCount())
               if dialog.checks.item(i, 0).data(Qt.UserRole)["offset"] == offset)
    dialog.checks.selectRow(row)
    assert dialog.repair_button.isEnabled()
    dialog.repair_button.click()
    assert offset in dialog._editors and dialog.apply_button.isEnabled()
    dialog.undo_button.click()
    assert offset not in dialog._editors and not dialog.apply_button.isEnabled()
    dialog.redo_button.click()
    dialog.apply_button.click()
    expected = {offset + 8, offset + 9, *({0x3FE, 0x3FF} if family == "MS42" else set())}
    changed = {i for i, (a, b) in enumerate(zip(raw, dialog.edited_snapshot["data"])) if a != b}
    assert changed and changed <= expected and source["data"] == raw


def test_wheel_scroll_does_not_change_storage_draft(window):
    dialog = _StorageBackupDialog(window, storage_snapshot())
    dialog.tabs.setCurrentIndex(1)
    dialog.show()
    QApplication.processEvents()
    editor = dialog._editors[0x280][1]
    event = QWheelEvent(QPointF(5, 5), QPointF(editor.mapToGlobal(QPoint(5, 5))),
                        QPoint(), QPoint(0, -120), Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
    QApplication.sendEvent(editor, event)
    assert editor.raw_value() == -2 and not dialog._changes()


def test_record_check_repair_can_apply_without_calibration_for_editable_fields(window):
    source = storage_snapshot(valid_records=False)
    raw = bytearray(source["data"])
    put_record(raw, 0x2E6, bytes(10))
    raw[0x2F0] ^= 1
    source = make_snapshot("MS43", "eeprom", raw, software_id="430069")
    dialog = _StorageBackupDialog(window, source)
    assert dialog.fields.rowCount() == 0
    row = next(i for i in range(dialog.checks.rowCount())
               if dialog.checks.item(i, 0).data(Qt.UserRole)["offset"] == 0x2E6)
    dialog.checks.selectRow(row)
    dialog.repair_button.click()
    assert dialog.fields.rowCount() == 0 and dialog.apply_button.isEnabled()
    dialog.apply_button.click()
    assert dialog.edited_snapshot["data"][0x2E6:0x2F0] == bytes(10)
    assert dialog.edited_snapshot["data"][0x2F0:0x2F2] == _crc(bytes(10), 15).to_bytes(2, "little")


def test_undo_restores_calibration_context_and_its_draft_fields(window, monkeypatch, tmp_path):
    from tests.test_ms42_ms43_storage_calibration import calibrated

    source, calibration = calibrated("MS43")
    path = tmp_path / "calibration.bin"
    path.write_bytes(calibration)
    dialog = _StorageBackupDialog(window, source)
    dialog._editors[0x280][1].set_raw_value(12)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(path), ""))
    dialog.calibration_button.click()
    dialog._editors[0x286][1].set_raw_value(130)
    dialog.undo_button.click()
    assert dialog._changes() == {0x280: 12} and dialog._calibration == calibration
    dialog.undo_button.click()
    assert dialog._changes() == {0x280: 12} and dialog._calibration is None
    assert 0x286 not in dialog._editors
    dialog.redo_button.click()
    dialog.redo_button.click()
    assert dialog._changes() == {0x280: 12, 0x286: 130}
    dialog.apply_button.click()
    assert dialog.edited_snapshot == edit_storage(source, {0x280: 12, 0x286: 130}, calibration=calibration)


def test_editor_tool_pages_fit_resized_dialog(window, tmp_path):
    dialog = _StorageBackupDialog(window, storage_snapshot(knock=True))
    dialog.show()
    for width, height in ((840, 620), (600, 420)):
        dialog.resize(width, height)
        for index in range(dialog.tabs.count()):
            dialog.tabs.setCurrentIndex(index)
            QApplication.processEvents()
            for control in (dialog.undo_button, dialog.redo_button, dialog.compare_button, dialog.apply_button):
                bounds = control.rect().translated(control.mapTo(dialog, QPoint()))
                assert dialog.rect().contains(bounds)
                assert control.visibleRegion().boundingRect() == control.rect()
            for table in (dialog.fields, dialog.checks, dialog.knock_table):
                if table.isVisible():
                    assert table.horizontalScrollBar().maximum() == 0
            if index in (2, 3):
                assert dialog.grab().save(str(tmp_path / f"storage-tools-{width}-{index}.png"))


@pytest.mark.parametrize("scale", ("1", "1.25", "1.5", "2"))
def test_editor_tools_at_independent_qt_scales(tmp_path, scale):
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", QT_SCALE_FACTOR=scale,
               QT_AUTO_SCREEN_SCALE_FACTOR="0", QT_FONT_DPI="96")
    result = subprocess.run(
        [sys.executable, "-m", "pytest", f"{__file__}::test_editor_tool_pages_fit_resized_dialog",
         "-q", f"--basetemp={tmp_path / 'child'}"],
        cwd=Path(__file__).resolve().parents[1], env=env, capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr
