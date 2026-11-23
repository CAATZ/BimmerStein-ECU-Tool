"""The Windows editor result owns one immutable BIN across save, export and review."""

from pathlib import Path

import pytest
from PyQt5.QtCore import Qt
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import (
    QApplication, QDialog, QDialogButtonBox, QFileDialog, QInputDialog, QMessageBox,
    QPushButton, QStyleOptionViewItem,
)

from tests.test_ms42_ms43_desktop import window  # noqa: F401


def test_bins_distinguishes_long_revision_names_and_searches_the_full_name(window):
    from tests.test_config_hover_wheel import tooltip

    prefix = "Siemens MS41 1429861 M52B28 VANOS minimum RPM track development calibration revision "
    names = [prefix + suffix for suffix in ("B.bin", "C.bin")]
    for name in names:
        window._backup_mgr.add_data(b"sample", name, folder="Patched")
    window.resize(980, 740)
    window.tabs.setCurrentIndex(window._backup_tab_index)
    window.show()
    QApplication.processEvents()
    table = window.backup_table
    assert table.columnWidth(1) > table.columnWidth(6)
    for row in range(table.rowCount()):
        item = table.item(row, 1)
        assert item.text() in names
        option = QStyleOptionViewItem()
        table.itemDelegateForColumn(1).initStyleOption(option, table.indexFromItem(item))
        assert option.textElideMode == Qt.ElideMiddle
        assert not option.features & QStyleOptionViewItem.WrapText
        assert tooltip(table.viewport(), table.visualItemRect(item).center()) == item.text()
    window._backup_search.setText("revision C.bin")
    visible = [r for r in range(table.rowCount()) if not table.isRowHidden(r)]
    assert [table.item(r, 1).text() for r in visible] == [names[1]]


def test_saved_result_exports_and_reveals_exact_collision_entry(window, tmp_path, monkeypatch):
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **kw: ("Cluster LEDs", True))
    window._save_edited_bin(b"previous revision", "LEDs.bin", "Patched", source="patched")
    target = bytearray(b"configured image")
    entry = window._save_edited_bin(target, "LEDs.bin", "Patched", source="patched")
    name = Path(entry.filename).name
    assert name != "Cluster LEDs.bin" and name.startswith("Cluster LEDs_")
    selected = []
    receipt = window._patch_saved_actions
    receipt.show_saved(entry, target, lambda *args: selected.append(args))
    target[:] = b"later changes"
    assert receipt.reveal.text() == name
    assert entry.filename in receipt.reveal.toolTip()
    assert name in receipt.reveal.accessibleName()
    assert window.btn_patches_build.parentWidget().isHidden()
    destination = tmp_path / "copied.bin"
    suggestions = []
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda _, __, path, ___:
                        (suggestions.append(Path(path)) or str(destination), ""))
    receipt.export.click()
    receipt.review.click()
    assert destination.read_bytes() == Path(entry.path).read_bytes() == b"configured image"
    assert selected == [(destination.read_bytes(), name)]
    assert suggestions[0].name == name
    assert window._live_settings().value("bins/export_directory") == str(tmp_path)
    window._backup_search.setText("hidden by search")
    window.cb_backup_folder.setCurrentIndex(window.cb_backup_folder.findData(""))
    QTest.keyClick(receipt.reveal, Qt.Key_Space)
    assert window.tabs.currentIndex() == window._backup_tab_index
    assert window._selected_backup().sha256 == entry.sha256
    assert window._selected_backup().filename == entry.filename


@pytest.mark.parametrize("proceed", [False, True])
def test_temporary_review_exports_without_closing_or_archiving(window, tmp_path, monkeypatch, proceed):
    image = bytearray(b"temporary target")
    exact = bytes(image)
    destination = tmp_path / "export.bin"
    selections = iter(["", str(destination)])
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: (next(selections), ""))
    receipts, writes = [], []

    def interact(dialog):
        dialog.show()
        buttons = dialog.findChild(QDialogButtonBox)
        export = next(b for b in buttons.buttons() if b.text().startswith("Export BIN"))
        flash = next(b for b in buttons.buttons() if b.text() == "Continue to flash")
        image[:] = b"later draft"
        export.click()  # Cancel the file chooser first.
        assert dialog.isVisible() and not receipts and not writes
        export.click()
        assert dialog.isVisible() and receipts == ["record"] and not writes
        assert destination.read_bytes() == exact
        assert flash.isEnabled() == proceed
        (flash if proceed else buttons.button(QDialogButtonBox.Cancel)).click()
        return dialog.result()

    monkeypatch.setattr(QDialog, "exec_", interact)
    window._review_edited_bin(image, "target.bin", lambda *args: writes.append(args),
                              flash_reason=lambda: None if proceed else "Connect to the ECU.",
                              before_output=lambda: receipts.append("record"))
    assert writes == ([(exact, "target.bin")] if proceed else [])
    assert window._backup_mgr.entries == []


def test_live_confirmation_can_export_then_decline_write(window, tmp_path, monkeypatch):
    destination = tmp_path / "reviewed.bin"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: (str(destination), ""))

    def export_and_decline(box):
        box.show()
        export = next(b for b in box.findChildren(QPushButton) if b.text() == "Export BIN…")
        buttons = box.findChild(QDialogButtonBox)
        assert export.isVisible() and buttons.button(QDialogButtonBox.No).isDefault()
        export.click()
        assert box.isVisible() and box.result() == 0
        buttons.button(QDialogButtonBox.No).click()
        return box.result()

    monkeypatch.setattr(QDialog, "exec_", export_and_decline)
    answer = window._confirm_edited_write("Confirm Config Write", "Apply the selected changes?",
                                          b"exact live target", "Live config.bin", warning=True)
    assert answer == QMessageBox.No
    assert destination.read_bytes() == b"exact live target" and not window._backup_mgr.entries
