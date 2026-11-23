"""Windows actions for a finished patch or ECU configuration image."""

from pathlib import Path

from PyQt5.QtCore import QEvent, QIODevice, QSaveFile, Qt
from PyQt5.QtWidgets import (
    QBoxLayout, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QInputDialog, QLabel,
    QMessageBox, QPushButton, QScrollArea, QSizePolicy, QStyle, QStyleOptionButton,
    QStylePainter, QVBoxLayout, QWidget,
)

from backup_manager import BackupIndexError
from gui_controls import ElidedLabel, fit_window_to_screen


def write_bin(path, image):
    """Replace the destination only after the complete image has been written."""
    output = QSaveFile(str(path))
    if (not output.open(QIODevice.WriteOnly) or output.write(image) != len(image)
            or not output.commit()):
        raise OSError(output.errorString())


class _SavedBinLink(QPushButton):
    """Show the saved filename as a keyboard-accessible link into Bins."""

    def __init__(self):
        super().__init__()
        self.setFlat(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.setStyleSheet(
            "QPushButton { color:palette(link); text-align:left; text-decoration:underline;"
            " padding:2px; border:1px solid transparent; background:transparent; }"
            "QPushButton:focus { border-color:palette(highlight); }")

    def paintEvent(self, event):
        option = QStyleOptionButton()
        self.initStyleOption(option)
        space = self.style().subElementRect(QStyle.SE_PushButtonContents, option, self)
        name = self.text().replace("&&", "&")
        option.text = self.fontMetrics().elidedText(name, Qt.ElideMiddle, space.width()).replace("&", "&&")
        QStylePainter(self).drawControl(QStyle.CE_PushButton, option)


class SavedImageActions(QWidget):
    """Inline receipt bound to the image that was actually saved."""

    def __init__(self, owner, *, draft_actions=None):
        super().__init__(owner)
        self._owner = owner
        self._draft_actions = draft_actions
        self._row = QBoxLayout(QBoxLayout.LeftToRight, self)
        self._row.setContentsMargins(0, 0, 0, 0)
        info = QHBoxLayout()
        self._saved = QLabel("Saved")
        self._saved.setStyleSheet("color:#9ece6a;")
        info.addWidget(self._saved)
        self.label = ElidedLabel(elide=Qt.ElideMiddle)
        self.reveal = _SavedBinLink()
        info.addWidget(self.reveal, 1, Qt.AlignLeft)
        info.addWidget(self.label, 1)
        self._row.addLayout(info, 1)
        self._buttons = QHBoxLayout()
        self._buttons.setAlignment(Qt.AlignLeft)
        self.review = QPushButton("Flash…")
        self.review.setToolTip("Review this saved image before writing it to the ECU.")
        self.export = QPushButton("Export BIN…")
        self._buttons.addWidget(self.review)
        self._buttons.addWidget(self.export)
        self._row.addLayout(self._buttons)
        self.review.clicked.connect(lambda: self._review(self._image, self._name))
        self.export.clicked.connect(lambda: self._owner._export_edited_bin(self._image, self._name))
        self.reveal.clicked.connect(lambda: self._owner._show_saved_bin(self._entry))
        self.clear()

    def clear(self):
        self._image = self._entry = self._review = None
        self._name = ""
        self.set_pending(False)

    def set_pending(self, pending):
        show_saved = self._image is not None and not pending
        self.setVisible(show_saved)
        if self._draft_actions is not None:
            self._draft_actions.setVisible(not show_saved)

    def show_saved(self, entry, image, review_callback):
        self._set_output(f"Bins/{entry.filename}", entry.filename, image,
                         review_callback, entry)

    def show_file(self, path, image, review_callback):
        self._set_output(str(path), path, image, review_callback, None)

    def _set_output(self, location, path, image, review_callback, entry):
        self._name = Path(path).name
        self._image = bytes(image)
        self._review = review_callback
        self._entry = entry
        self.label.setText(self._name)
        self.label.setToolTip(location)
        self.label.setVisible(entry is None)
        self.reveal.setText(self._name.replace("&", "&&"))
        self.reveal.setToolTip(f"Show in Bins\n{location}")
        self.reveal.setAccessibleName(f"Show {self._name} in Bins")
        self.reveal.setVisible(entry is not None)
        self._fit_row()
        self.set_pending(False)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_row()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in (QEvent.FontChange, QEvent.StyleChange) and hasattr(self, "_buttons"):
            self._fit_row()

    def _fit_row(self):
        metrics = self.fontMetrics()
        readable_name = min(metrics.horizontalAdvance(self._name), 22 * metrics.averageCharWidth())
        needed = self._saved.sizeHint().width() + readable_name + self._buttons.sizeHint().width()
        stacked = self.width() < needed + self._row.spacing() * 3
        self._row.setDirection(QBoxLayout.TopToBottom if stacked else QBoxLayout.LeftToRight)


class EditedImageOutputMixin:
    def _save_edited_bin(self, image, suggested_name, folder, *, source, notes="",
                         before_output=None, **metadata):
        name = suggested_name
        while True:
            name, ok = QInputDialog.getText(
                self, "Save to Bins", f"Filename in Bins/{folder}", text=name)
            if not ok:
                return None
            name = name.strip()
            if not name or Path(name).name != name or name.lower() in (".", "..", ".bin"):
                QMessageBox.warning(self, "Save to Bins", "Enter a filename for this BIN.")
                continue
            if not name.lower().endswith(".bin"):
                name += ".bin"
            try:
                if before_output is not None:
                    before_output()
                entry = self._backup_mgr.add_data(bytes(image), name, notes=notes, source=source,
                                                  folder=folder, **metadata)
            except (OSError, ValueError, BackupIndexError) as exc:
                QMessageBox.warning(self, "Save to Bins", str(exc))
                return None
            self._on_backup_refresh()
            return entry

    def _export_edited_bin(self, image, filename, *, before_output=None, parent=None):
        parent = parent or self
        settings = self._live_settings()
        folder = Path(settings.value("bins/export_directory", "", type=str))
        destination, _ = QFileDialog.getSaveFileName(
            parent, "Export BIN", str(folder / Path(filename).name),
            "Binary files (*.bin);;All files (*)")
        if not destination:
            return False
        try:
            path = Path(destination)
            if path.resolve().is_relative_to(Path(self._backup_mgr.folder_path("")).resolve()):
                raise ValueError("Choose an export location outside the Bins library.")
            if before_output is not None:
                before_output()
            write_bin(path, image)
        except (OSError, ValueError, BackupIndexError) as exc:
            QMessageBox.warning(parent, "Export BIN", str(exc))
            return False
        settings.setValue("bins/export_directory", str(path.parent))
        self._log(f"Exported BIN to {path}", "ok")
        return True

    def _confirm_edited_write(self, title, text, image, filename, *, warning=False):
        """Offer export inside the live writer's existing Yes/No confirmation."""
        image = bytes(image)
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        layout = QVBoxLayout(dialog)
        body = QHBoxLayout()
        icon = QLabel()
        kind = QStyle.SP_MessageBoxWarning if warning else QStyle.SP_MessageBoxQuestion
        size = self.style().pixelMetric(QStyle.PM_MessageBoxIconSize)
        icon.setPixmap(self.style().standardIcon(kind).pixmap(size, size))
        body.addWidget(icon, 0, Qt.AlignTop)
        message = QLabel(text)
        message.setTextFormat(Qt.PlainText)
        message.setWordWrap(True)
        message.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        area = QScrollArea()
        area.setFrameShape(QScrollArea.NoFrame)
        area.setWidgetResizable(True)
        area.setWidget(message)
        body.addWidget(area, 1)
        layout.addLayout(body, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Yes | QDialogButtonBox.No)
        export = buttons.addButton("Export BIN…", QDialogButtonBox.ActionRole)
        export.clicked.connect(lambda: self._export_edited_bin(image, filename, parent=dialog))
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        buttons.button(QDialogButtonBox.No).setDefault(True)
        buttons.button(QDialogButtonBox.No).setFocus()
        height = max(size, message.heightForWidth(440)) + buttons.sizeHint().height() + 42
        fit_window_to_screen(dialog, 540, height)
        accepted = dialog.exec_() == QDialog.Accepted
        dialog.deleteLater()
        return QMessageBox.Yes if accepted else QMessageBox.No

    def _review_edited_bin(self, image, filename, write_callback, *, flash_reason,
                           temporary=True, before_output=None):
        image = bytes(image)
        filename = Path(filename).name
        dialog = QDialog(self)
        dialog.setWindowTitle("Flash without saving" if temporary else "Review flash")
        layout = QVBoxLayout(dialog)
        name = ElidedLabel(filename, elide=Qt.ElideMiddle)
        layout.addWidget(name)
        description = f"{len(image):,} bytes"
        if temporary:
            description += " · Temporary image"
        layout.addWidget(QLabel(description))

        reason = flash_reason()
        hint = QLabel(reason or "Continue to review the ECU write.")
        hint.setTextFormat(Qt.PlainText)
        hint.setWordWrap(True)
        layout.addWidget(hint)
        buttons = QDialogButtonBox()
        export = buttons.addButton("Export BIN…", QDialogButtonBox.ActionRole)
        proceed = buttons.addButton("Continue to flash", QDialogButtonBox.AcceptRole)
        cancel = buttons.addButton(QDialogButtonBox.Cancel)
        proceed.setEnabled(reason is None)
        proceed.setAutoDefault(False)

        def export_copy():
            if self._export_edited_bin(image, filename, before_output=before_output, parent=dialog):
                reason = flash_reason()
                hint.setText("BIN exported. " + (reason or "Continue when ready."))
                proceed.setEnabled(reason is None)

        def accept():
            reason = flash_reason()
            if reason:
                hint.setText(reason)
                proceed.setEnabled(False)
                return
            try:
                if before_output is not None:
                    before_output()
            except (OSError, ValueError, BackupIndexError) as exc:
                QMessageBox.warning(dialog, "Review flash", str(exc))
                return
            dialog.accept()

        export.clicked.connect(export_copy)
        proceed.clicked.connect(accept)
        cancel.clicked.connect(dialog.reject)
        layout.addWidget(buttons)
        cancel.setDefault(True)
        cancel.setFocus()
        fit_window_to_screen(dialog, max(400, dialog.sizeHint().width()), dialog.sizeHint().height())
        accepted = dialog.exec_() == QDialog.Accepted
        dialog.deleteLater()
        if accepted:
            write_callback(image, filename)

    def _show_saved_bin(self, entry):
        self.tabs.setCurrentIndex(self._backup_tab_index)
        self._backup_search.clear()
        self.cb_backup_folder.setCurrentIndex(max(0, self.cb_backup_folder.findData(entry.folder)))
        for row, item in enumerate(self._backup_mgr.entries):
            if item.filename == entry.filename and item.sha256 == entry.sha256:
                self.backup_table.selectRow(row)
                self.backup_table.scrollToItem(self.backup_table.item(row, 1))
                self.backup_table.setFocus()
                return
        QMessageBox.information(self, "Show in Bins", "The saved entry has been moved or removed.")
