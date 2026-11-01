"""Engineering-unit controls keep the existing raw-byte draft and restore contract."""

import pytest
from PyQt5.QtWidgets import QFileDialog

from gui_ms42_ms43_storage import _StorageBackupDialog
from ms42_ms43_storage_edit import edit_storage
from tests.test_ms42_ms43_desktop import window as window
from tests.test_ms42_ms43_storage_editor_ui import storage_snapshot, forbid_external_actions


@pytest.mark.parametrize("mouse_click", [False, True])
def test_typed_physical_value_can_apply_without_enter_or_tab(window, monkeypatch, mouse_click):
    from PyQt5.QtCore import Qt
    from PyQt5.QtTest import QTest
    from PyQt5.QtWidgets import QApplication

    source = storage_snapshot(knock=True)
    forbid_external_actions(window, monkeypatch)
    dialog = _StorageBackupDialog(window, source)
    dialog.tabs.setCurrentIndex(1)
    dialog.show()
    editor = dialog._editors[0x2AD][1]
    editor.setFocus()
    editor.selectAll()
    QTest.keyClicks(editor, "-24")
    QApplication.processEvents()
    assert editor.raw_value() == 9  # Deferred normalization keeps typing usable.
    assert dialog.apply_button.isEnabled()
    if mouse_click:
        QTest.mouseClick(dialog.apply_button, Qt.LeftButton)
    else:
        dialog.apply_button.click()
    assert dialog.edited_snapshot == edit_storage(source, {0x2AD: 64})
    assert source["data"][0x2AD] == 9


def test_unchanged_typed_value_does_not_enable_apply(window):
    from PyQt5.QtTest import QTest

    dialog = _StorageBackupDialog(window, storage_snapshot(knock=True))
    editor = dialog._editors[0x2AD][1]
    editor.selectAll()
    QTest.keyClicks(editor, "-44.625")
    assert not dialog.apply_button.isEnabled()
    assert not dialog._changes()


def test_incomplete_typed_value_cannot_be_applied(window):
    from PyQt5.QtTest import QTest

    source = storage_snapshot(knock=True)
    dialog = _StorageBackupDialog(window, source)
    editor = dialog._editors[0x2AD][1]
    editor.selectAll()
    QTest.keyClicks(editor, "-")
    assert not editor.hasAcceptableInput()
    dialog._apply()
    assert dialog.edited_snapshot is None and dialog._snapshot == source
    assert "valid value" in dialog.note.text()


def test_knock_grid_and_editor_use_degrees_but_commit_exact_raw_values(window, monkeypatch):
    source = storage_snapshot(knock=True)
    forbid_external_actions(window, monkeypatch)
    dialog = _StorageBackupDialog(window, source)
    editor = dialog._editors[0x2AD][1]
    assert editor.value() == pytest.approx(-44.625)
    assert "°" in editor.suffix()
    assert float(dialog.knock_table.item(2, 1).text()) == pytest.approx(-44.625)
    dialog.knock_table.item(2, 1).setText("-24")
    assert dialog._changes() == {0x2AD: 64}
    assert editor.value() == -24
    editor.setValue(-12)
    assert dialog._changes() == {0x2AD: 96}
    assert float(dialog.knock_table.item(2, 1).text()) == -12
    dialog.undo_button.click()
    assert dialog._changes() == {0x2AD: 64}
    dialog.redo_button.click()
    assert dialog._changes() == {0x2AD: 96}
    dialog.apply_button.click()
    assert dialog.edited_snapshot == edit_storage(source, {0x2AD: 96})
    assert dialog.edited_snapshot["data"][0x2E3] == 255


@pytest.mark.parametrize("family", ("MS42", "MS43"))
def test_physical_display_does_not_change_unedited_backup(window, monkeypatch, family):
    source = storage_snapshot(family, knock=True)
    forbid_external_actions(window, monkeypatch)
    dialog = _StorageBackupDialog(window, source)
    assert dialog._draft_snapshot() == source
    assert not dialog._changes() and not dialog.apply_button.isEnabled()
    for original, editor in dialog._editors.values():
        if hasattr(editor, "raw_value"):
            assert editor.raw_value() == original
    dialog.reject()
    assert dialog.edited_snapshot is None


def test_unrepresentable_and_invalid_grid_values_preserve_exact_drafts(window):
    dialog = _StorageBackupDialog(window, storage_snapshot(knock=True))
    editor = dialog._editors[0x2AD][1]
    editor.setValue(-24.1)
    assert editor.value() == -24 and dialog._changes() == {0x2AD: 64}
    for text in ("-24.1", "nan", "inf", "not a number", "0.375"):
        dialog.knock_table.item(2, 1).setText(text)
        assert dialog.knock_table.item(2, 1).text() == "-24.000"
        assert dialog._changes() == {0x2AD: 64}
    dialog.undo_button.click()
    assert not dialog._changes()


@pytest.mark.parametrize("family, additive, multiplier", (("MS42", 0x48A, 0x48C), ("MS43", 0x2E8, 0x2EA)))
def test_calibrated_fuel_values_edit_in_ms_and_percent(window, monkeypatch, tmp_path, family, additive, multiplier):
    from tests.test_ms42_ms43_storage_calibration import calibrated

    source, calibration = calibrated(family)
    path = tmp_path / "tune.bin"
    path.write_bytes(calibration)
    forbid_external_actions(window, monkeypatch)
    dialog = _StorageBackupDialog(window, source)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(path), ""))
    dialog.calibration_button.click()
    add, factor = dialog._editors[additive][1], dialog._editors[multiplier][1]
    assert add.value() == factor.value() == 0
    assert add.suffix() == " ms" and factor.suffix() == " %"
    add.setValue(0.4)
    factor.setValue(1.5625)
    assert dialog._changes() == {additive: 32868, multiplier: 33792}
    dialog.apply_button.click()
    assert dialog.edited_snapshot == edit_storage(source, {additive: 32868, multiplier: 33792}, calibration=calibration)


def test_negative_exhaust_angle_scale_and_repeated_small_steps(window):
    from ms42_ms43_storage import make_snapshot
    from tests.test_ms42_ms43_storage_inspection import put_record

    source = storage_snapshot()
    data = bytearray(source["data"])
    put_record(data, 0x28A, bytes(20))
    source = make_snapshot("MS43", "eeprom", data, software_id="430069")
    dialog = _StorageBackupDialog(window, source)
    exhaust = dialog._editors[0x28C][1]
    assert exhaust.value() == -60
    assert exhaust.minimum() == pytest.approx(-155.9985) and exhaust.maximum() == -60
    exhaust.setValue(-63)
    assert exhaust.raw_value() == 2048
    for _ in range(100):
        exhaust.stepUp()
    assert exhaust.raw_value() == 1948
    for _ in range(100):
        exhaust.stepDown()
    assert exhaust.raw_value() == 2048
    idle = dialog._editors[0x280][1]
    for _ in range(100):
        idle.stepUp()
    assert idle.raw_value() == 98
    dialog.apply_button.click()
    assert dialog.edited_snapshot == edit_storage(source, {0x28C: 2048, 0x280: 98})


def test_unset_temperature_is_not_presented_as_a_measurement(window):
    from ms42_ms43_storage import make_snapshot
    from tests.test_ms42_ms43_storage_inspection import put_record

    data = bytearray(storage_snapshot()["data"])
    put_record(data, 0x32A, bytes((255, 100)))
    source = make_snapshot("MS43", "eeprom", data, software_id="430069")
    dialog = _StorageBackupDialog(window, source)
    editor = dialog._editors[0x32A][1]
    assert editor.text() == "Unset (FF)" and editor.raw_value() == 255
    editor.interpretText()
    assert not dialog._changes()
    editor.setValue(27)
    assert editor.text() == "27.00 °C" and editor.raw_value() == 100
    editor.set_raw_value(255)
    assert editor.text() == "Unset (FF)" and not dialog._changes()
