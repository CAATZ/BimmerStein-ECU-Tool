"""Offline storage drafts use the real record editor and never start a worker."""

from copy import deepcopy
import os
from pathlib import Path
import subprocess
import sys

import pytest
from PyQt5.QtCore import QPoint
from PyQt5.QtWidgets import QApplication, QComboBox, QDialog, QDialogButtonBox

from checksum import _crc
from gui_ms42_ms43_storage import _StorageBackupDialog
from ms42_ms43_storage import make_snapshot
from ms42_ms43_storage_edit import editable_fields
from tests.test_ms42_ms43_desktop import connect, window as window
from tests.test_ms42_ms43_storage_inspection import put_record


def storage_snapshot(family="MS43", *, coil=2, heat=1, valid_records=True, knock=False):
    raw = bytearray(b"\xFF" * (0x800 if family == "MS43" else 0x4000))
    idle = b"\xFE\xFF\x03\x00\x00\x80\x82\xAB"
    if family == "MS43":
        put_record(raw, 0x32, b"43290010")
        if valid_records:
            put_record(raw, 0x280, idle)
            if knock:
                put_record(raw, 0x2A0, b"\x34\x12\x00\x00" + bytes(range(63)) + b"\xFF")
            put_record(raw, 0x3E2, bytes((coil, 0xF3)))
            put_record(raw, 0x3E6, bytes((heat, 0)))
    else:
        raw[:4] = (1).to_bytes(4, "little")
        raw[4:14] = b"6577225107"
        if valid_records:
            put_record(raw, 0x114, idle)
        raw[0x3F6:0x3F8] = (0x3E0).to_bytes(2, "little")
        raw[0x3F8:0x3FC] = (0xFFFFFFFE).to_bytes(4, "little")
        raw[0x3FE:0x400] = _crc(raw[:0x3E0], 0xF).to_bytes(2, "little")
    return make_snapshot(family, "eeprom" if family == "MS43" else "retained", raw,
                         software_id="430069" if family == "MS43" else "0110C6",
                         hardware_id="offline-test")


def forbid_external_actions(window, monkeypatch):
    for name in ("_run_task", "_run_state_changing_task", "_ms42_ms43_catalog_storage",
                 "_ms42_ms43_save_storage", "_ms42_ms43_restore_storage"):
        monkeypatch.setattr(window, name, lambda *a, **k: pytest.fail("Editor must remain offline"))


def test_interrupted_restore_keeps_inspection_without_changing_its_target(window, monkeypatch):
    from ms42_ms43 import WriteState

    ecu = connect(window, "MS43")
    original = storage_snapshot()
    window._ms42_ms43_show_storage(original, "Restore target")
    ecu.write_state = WriteState("MS43", "eeprom_restore", destructive_started=True)
    forbid_external_actions(window, monkeypatch)
    window._ms42_ms43_update_storage()
    assert not window._ms42_ms43_storage_load.isEnabled()
    assert window._ms42_ms43_storage_view.isEnabled()
    dialog = _StorageBackupDialog(window, original)
    assert dialog.fields.rowCount() == 0 and not dialog.apply_button.isEnabled()
    assert "finish recovery" in dialog.note.text()
    assert "MS43 430069" in dialog.inspection.toPlainText()
    dialog.reject()
    assert window._ms42_ms43_storage_snapshot == original
    ecu.write_state.completed = True
    finished = _StorageBackupDialog(window, original)
    assert finished.fields.rowCount() == 5


@pytest.mark.parametrize("family", ("MS42", "MS43"))
def test_cancel_discards_draft_and_preserves_inspection(window, monkeypatch, family):
    original = storage_snapshot(family)
    before = deepcopy(original)
    window._ms42_ms43_show_storage(original, "Loaded backup")
    forbid_external_actions(window, monkeypatch)

    def cancel(dialog):
        assert dialog.tabs.currentIndex() == 0
        text = dialog.inspection
        assert text.isReadOnly() and "Raw bytes (file offsets)" in text.toPlainText()
        first = next(iter(dialog._editors.values()))[1]
        first.set_raw_value(123)
        assert dialog.apply_button.isEnabled()
        dialog.reject()
        assert dialog.edited_snapshot is None
        return QDialog.Rejected

    monkeypatch.setattr(QDialog, "exec_", cancel)
    window._ms42_ms43_view_storage()
    assert original == before == window._ms42_ms43_storage_snapshot
    assert window._ms42_ms43_storage_source == "Loaded backup"


@pytest.mark.parametrize("family", ("MS42", "MS43"))
def test_apply_updates_only_loaded_snapshot_and_native_checks(window, monkeypatch, tmp_path, family):
    original = storage_snapshot(family)
    before = deepcopy(original)
    source = tmp_path / "backup.bin"
    source.write_bytes(original["data"])
    window._ms42_ms43_show_storage(original, str(source))
    forbid_external_actions(window, monkeypatch)
    offset = 0x280 if family == "MS43" else 0x114

    def apply(dialog):
        assert not dialog.apply_button.isEnabled()
        editor = dialog._editors[offset][1]
        editor.set_raw_value(123)
        assert dialog._changes() == {offset: 123}
        assert dialog.fields.item(0, 1).text() == "-0.0031 %"
        editor.set_raw_value(-2)
        assert not dialog.apply_button.isEnabled()
        editor.set_raw_value(123)
        dialog.apply_button.click()
        return dialog.result()

    monkeypatch.setattr(QDialog, "exec_", apply)
    window._ms42_ms43_view_storage()
    result = window._ms42_ms43_storage_snapshot
    assert result["hardware_id"] == original["hardware_id"]
    assert result["sha256"] != original["sha256"]
    assert int.from_bytes(result["data"][offset:offset + 2], "little", signed=True) == 123
    changed = {index for index, (left, right) in enumerate(zip(original["data"], result["data"])) if left != right}
    assert changed <= {offset, offset + 1, offset + 8, offset + 9, *({0x3FE, 0x3FF} if family == "MS42" else set())}
    assert len(editable_fields(result)) == (5 if family == "MS43" else 3)
    assert original == before and source.read_bytes() == original["data"]
    assert window._ms42_ms43_storage_source == str(source) + " (edited)"


def test_unknown_enum_is_never_clamped_or_submitted_without_a_choice(window):
    original = storage_snapshot(coil=99, heat=3)
    dialog = _StorageBackupDialog(window, original)
    for offset, value in ((0x3E2, 99), (0x3E6, 3)):
        editor = dialog._editors[offset][1]
        assert isinstance(editor, QComboBox)
        assert editor.currentData() == value
        assert editor.currentText() == f"Current: {value} (unrecognized)"
        assert not editor.model().item(editor.currentIndex()).isEnabled()
    assert not dialog._changes() and not dialog.apply_button.isEnabled()
    dialog._editors[0x280][1].set_raw_value(7)
    dialog.apply_button.click()
    assert dialog.edited_snapshot["data"][0x3E2] == 99
    assert dialog.edited_snapshot["data"][0x3E6] == 3
    assert original["data"][0x280:0x282] == b"\xFE\xFF"


def test_enum_choices_are_explicit_and_only_changed_values_are_submitted(window, monkeypatch):
    import ms42_ms43_storage_edit as core

    original = storage_snapshot()
    edit = core.edit_storage
    calls = []

    def record(snapshot, changes):
        calls.append(dict(changes))
        return edit(snapshot, changes)

    monkeypatch.setattr(core, "edit_storage", record)
    dialog = _StorageBackupDialog(window, original)
    for offset, value in ((0x3E2, 1), (0x3E6, 0)):
        editor = dialog._editors[offset][1]
        editor.setCurrentIndex(editor.findData(value))
    dialog.apply_button.click()
    assert calls == [{0x3E2: 1, 0x3E6: 0}]
    assert dialog.edited_snapshot["data"][0x3E2:0x3E4] == b"\x01\xF3"


def test_out_of_range_knock_original_is_preserved_until_explicit_valid_change(window, monkeypatch):
    import ms42_ms43_storage_edit as core

    original = storage_snapshot(knock=True)
    edit = core.edit_storage
    calls = []

    def record(snapshot, changes):
        calls.append(dict(changes))
        return edit(snapshot, changes)

    monkeypatch.setattr(core, "edit_storage", record)
    dialog = _StorageBackupDialog(window, original)
    assert dialog.fields.rowCount() == 71
    editor = dialog._editors[0x2E3][1]
    assert editor.raw_value() == 255 and not dialog._changes()
    dialog._editors[0x280][1].set_raw_value(6)
    dialog.apply_button.click()
    assert calls == [{0x280: 6}]
    assert dialog.edited_snapshot["data"][0x2E3] == 255

    dialog = _StorageBackupDialog(window, original)
    editor = dialog._editors[0x2E3][1]
    editor.set_raw_value(129)
    assert not dialog.apply_button.isEnabled()
    assert "-48.000 ° to 0.000 °" in dialog.note.text()
    dialog._apply()
    assert len(calls) == 1 and dialog.edited_snapshot is None
    editor.set_raw_value(128)
    assert dialog.apply_button.isEnabled()
    dialog.apply_button.click()
    assert calls[-1] == {0x2E3: 128}
    assert dialog.edited_snapshot["data"][0x2E3] == 128
    assert original["data"][0x2E3] == 255


@pytest.mark.parametrize("family", ("MS42", "MS43"))
def test_valid_backup_without_editable_records_stays_inspectable(window, family):
    original = storage_snapshot(family, valid_records=False)
    dialog = _StorageBackupDialog(window, original)
    assert dialog.fields.rowCount() == 0
    assert "No verified editable records" in dialog.note.text()
    assert not dialog.apply_button.isEnabled()
    assert original["data"].hex(" ").upper()[:20] in dialog.inspection.toPlainText()


@pytest.mark.parametrize("kind", ("binding", "revision", "raw_flash"))
def test_unverified_layout_shows_exact_core_reason_and_preserves_raw_view(window, kind):
    original = storage_snapshot()
    if kind == "binding":
        raw = bytearray(original["data"])
        put_record(raw, 0x32, b"XXXXXXXX")
        original = make_snapshot("MS43", "eeprom", raw, software_id="430069")
    elif kind == "revision":
        original = make_snapshot("MS43", "eeprom", original["data"], software_id="430056")
    else:
        original = make_snapshot("MS43", "retained", b"\xFF" * 0x4000, software_id="430069")
    with pytest.raises(ValueError) as error:
        editable_fields(original)
    dialog = _StorageBackupDialog(window, original)
    assert dialog.note.text() == str(error.value)
    assert dialog.fields.rowCount() == 0 and not dialog.apply_button.isEnabled()
    assert "Raw bytes (file offsets)" in dialog.inspection.toPlainText()


def test_other_family_import_can_be_edited_offline_without_becoming_restore_target(window, monkeypatch):
    connect(window, "MS42")
    original = storage_snapshot("MS43")
    window._ms42_ms43_show_storage(original, "Imported MS43")
    forbid_external_actions(window, monkeypatch)

    def apply(dialog):
        dialog._editors[0x280][1].set_raw_value(6)
        dialog.apply_button.click()
        return dialog.result()

    monkeypatch.setattr(QDialog, "exec_", apply)
    window._ms42_ms43_view_storage()
    assert window._ms42_ms43_storage_snapshot["family"] == "MS43"
    assert window._ms42_ms43_storage_layout.currentData() == ("MS42", "retained")
    assert not window._ms42_ms43_storage_write.isEnabled()
    assert window._ms42_ms43_storage_save.isEnabled()


def test_core_edit_error_keeps_draft_open_and_loaded_backup_untouched(window, monkeypatch):
    import ms42_ms43_storage_edit as core

    original = storage_snapshot()
    window._ms42_ms43_show_storage(original, "Original")
    dialog = _StorageBackupDialog(window, original)
    dialog._editors[0x280][1].set_raw_value(6)
    monkeypatch.setattr(core, "edit_storage", lambda *a: (_ for _ in ()).throw(ValueError("Record changed")))
    dialog.apply_button.click()
    assert dialog.result() == QDialog.Rejected and dialog.edited_snapshot is None
    assert dialog.note.text() == "Record changed" and dialog.tabs.currentIndex() == 1
    assert window._ms42_ms43_storage_snapshot == original


@pytest.mark.parametrize("knock", (False, True))
def test_editor_controls_fit_logical_sizes(window, tmp_path, knock):
    dialog = _StorageBackupDialog(window, storage_snapshot(coil=99, heat=3, knock=knock))
    dialog.tabs.setCurrentIndex(1)
    dialog.show()
    ratio = dialog.devicePixelRatioF()
    directory = Path(os.environ.get("BIMMERSTEIN_STORAGE_EDITOR_SCREENSHOTS", str(tmp_path)))
    directory.mkdir(parents=True, exist_ok=True)
    for width, height in ((840, 620), (600, 420), (round(1000 / ratio), round(780 / ratio))):
        dialog.resize(width, height)
        QApplication.processEvents()
        assert dialog.fields.horizontalScrollBar().maximum() == 0
        assert dialog.fields.rowHeight(0) >= dialog.fields.fontMetrics().lineSpacing() * 2
        for control in (dialog.tabs.tabBar(), dialog.apply_button, dialog.findChild(QDialogButtonBox)):
            bounds = control.rect().translated(control.mapTo(dialog, QPoint()))
            assert dialog.rect().contains(bounds)
            assert control.visibleRegion().boundingRect() == control.rect()
        if knock:
            assert dialog.fields.rowCount() == 71
            dialog.fields.scrollToBottom()
            QApplication.processEvents()
            last = dialog._editors[0x3E6][1]
            assert last.visibleRegion().boundingRect() == last.rect()
    suffix = "-knock" if knock else ""
    assert dialog.grab().save(str(directory / f"storage-editor-compact-{ratio:g}{suffix}.png"))
    dialog.resize(840, 620)
    QApplication.processEvents()
    assert dialog.grab().save(str(directory / f"storage-editor-{ratio:g}{suffix}.png"))


@pytest.mark.parametrize("scale", ("1", "1.25", "1.5", "2"))
def test_editor_at_independent_qt_scales(tmp_path, scale):
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", QT_SCALE_FACTOR=scale,
               QT_AUTO_SCREEN_SCALE_FACTOR="0", QT_FONT_DPI="96")
    result = subprocess.run(
        [sys.executable, "-m", "pytest", f"{__file__}::test_editor_controls_fit_logical_sizes",
         "-q", f"--basetemp={tmp_path / 'child'}"],
        cwd=Path(__file__).resolve().parents[1], env=env, capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr
