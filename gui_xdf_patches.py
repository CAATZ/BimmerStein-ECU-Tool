"""Windows file workflow for an imported community patch list."""

from html import escape
from pathlib import Path

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QGridLayout, QHBoxLayout,
    QLabel, QLineEdit, QMenu, QMessageBox, QPushButton, QScrollArea, QSizePolicy, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from gui_controls import ConfigComboBox, fit_window_to_screen


class FieldDialog(QDialog):
    """Keep scalar settings in a form and multidimensional values in grids."""

    def __init__(self, fields, drafts, parent):
        super().__init__(parent)
        self.setWindowTitle("Configure patch settings")
        self.fields, self.edits = fields, dict(drafts)
        self.editors, self.grids = {}, {}
        layout = QVBoxLayout(self)
        intro = QLabel("Settings apply to the next patched image. Apply Settings keeps these edits until you build.")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        body = QWidget()
        form = QFormLayout(body)
        form.setAlignment(Qt.AlignTop)
        form.setLabelAlignment(Qt.AlignLeft | Qt.AlignTop)
        form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(16)
        for field in fields:
            label = QLabel(field["title"])
            label.setTextFormat(Qt.PlainText)
            label.setWordWrap(True)
            label.setMaximumWidth(240)
            label.setToolTip(field.get("category", ""))
            container = QWidget()
            controls = QVBoxLayout(container)
            controls.setContentsMargins(0, 0, 0, 0)
            controls.setSpacing(5)
            values = field.get("values", [])
            if (field["kind"] in ("scalar", "flag") and values and
                    field["rows"] == field["columns"] == 1 and not field.get("axes")):
                key, original = (field["id"], "z", 0, 0), values[0][0]
                value = self.edits.get(key, original)
                read_only = field.get("read_only") or 0 in field.get("read_only_rows", ())
                if field["kind"] == "flag":
                    editor = QCheckBox()
                    editor.setChecked(value in (True, 1, "1", "True"))
                    editor.toggled.connect(lambda value, k=key, old=original: self._set_edit(k, value, old))
                    editor.setEnabled(not read_only)
                else:
                    editor = QLineEdit(str(value))
                    editor.setMinimumWidth(100)
                    editor.setReadOnly(bool(read_only))
                    editor.textChanged.connect(lambda value, k=key, old=original: self._set_edit(k, value.strip(), old))
                editor.setAccessibleName(field["title"])
                editor.setObjectName(field["id"])
                label.setBuddy(editor)
                controls.addWidget(editor)
                self.editors[field["id"]] = editor
            elif values:
                grid = self._grid(field)
                controls.addWidget(grid)
                self.grids[field["id"]] = grid
            info = [field.get("description"), field.get("reason"), field.get("ownership_note")]
            bounds = []
            if field.get("minimum") is not None:
                bounds.append(f"Minimum: {field['minimum']:g}")
            if field.get("maximum") is not None:
                bounds.append(f"Maximum: {field['maximum']:g}")
            if field.get("units"):
                bounds.append(field["units"])
            info.insert(0, " · ".join(bounds))
            help_text = QLabel("\n".join(filter(None, info)))
            help_text.setTextFormat(Qt.PlainText)
            help_text.setWordWrap(True)
            help_text.setStyleSheet("color:#aaa;")
            help_text.setTextInteractionFlags(Qt.TextSelectableByMouse)
            controls.addWidget(help_text)
            form.addRow(label, container)
        scroll.setWidget(body)
        layout.addWidget(scroll)
        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        buttons.addButton("Apply Settings", QDialogButtonBox.AcceptRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        width = 700
        body_height = form.heightForWidth(width - 40)
        fit_window_to_screen(self, width, min(680, max(220, body_height + 120)))

    def _set_edit(self, key, value, original):
        if str(value) == str(original):
            self.edits.pop(key, None)
        else:
            self.edits[key] = value

    def _grid(self, field):
        axes = field.get("axes", {})
        x_axis, y_axis = axes.get("x"), axes.get("y")
        top, left = int(bool(x_axis)), int(bool(y_axis))
        grid = QTableWidget(field["rows"] + top, field["columns"] + left)
        grid.setAccessibleName(field["title"])
        grid.setAlternatingRowColors(True)

        def add(row, col, axis, r, c, value, read_only):
            key = (field["id"], axis, r, c)
            display = self.edits.get(key, value)
            item = QTableWidgetItem(str(display))
            is_switch = field["kind"] == "flag" and axis == "z"
            item.setData(Qt.UserRole, (key, value, is_switch))
            if is_switch:
                item.setText("")
                item.setCheckState(Qt.Checked if display in (True, 1, "1", "True") else Qt.Unchecked)
                item.setFlags((item.flags() | Qt.ItemIsUserCheckable) & ~Qt.ItemIsEditable)
            if field.get("read_only") or read_only:
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                if is_switch:
                    item.setFlags(item.flags() & ~Qt.ItemIsUserCheckable)
            grid.setItem(row, col, item)

        if x_axis:
            for col, value in enumerate(x_axis["values"]):
                add(0, col + left, "x", 0, col, value, x_axis.get("read_only", True))
        if y_axis:
            for row, value in enumerate(y_axis["values"]):
                add(row + top, 0, "y", row, 0, value, y_axis.get("read_only", True))
        for row, values in enumerate(field.get("values", ())):
            for col, value in enumerate(values):
                add(row + top, col + left, "z", row, col, value, row in field.get("read_only_rows", ()))
        grid.setHorizontalHeaderLabels((["Y axis"] if left else []) + [str(c + 1) for c in range(field["columns"])])
        grid.setVerticalHeaderLabels((["X axis"] if top else []) + [str(r + 1) for r in range(field["rows"])])
        grid.resizeColumnsToContents()
        grid.setMinimumHeight(120)
        grid.setMaximumHeight(300)
        grid.itemChanged.connect(self._grid_edit)
        return grid

    def _grid_edit(self, item):
        data = item.data(Qt.UserRole)
        if data is not None:
            value = item.checkState() == Qt.Checked if data[2] else item.text().strip()
            self._set_edit(data[0], value, data[1])


class ExternalPatchesPanel(QWidget):
    changed = pyqtSignal()

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.image = self.catalog = self.history = None
        self.rows = []
        self.actions = {}
        self.drafts = {}
        self.field_owners = {}
        self.reference = None
        self.library = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        bar = QHBoxLayout()
        self.sources = ConfigComboBox()
        self.sources.setAccessibleName("Community patch list")
        self.sources.setMinimumContentsLength(20)
        self.sources.setSizeAdjustPolicy(ConfigComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.sources.currentIndexChanged.connect(lambda _index: self._select(remember=True))
        bar.addWidget(self.sources, 1)
        self.settings = QPushButton("All XDF settings…")
        self.settings.clicked.connect(lambda: self.configure())
        bar.addWidget(self.settings)
        actions = QPushButton("Rollback / files…")
        menu = QMenu(actions)
        self.add_reference = menu.addAction("Choose reference BIN…", self.choose_reference)
        self.undo_build = menu.addAction("Undo last XDF build", self.undo)
        self.import_record = menu.addAction("Import rollback record…", lambda: self.record(False))
        self.export_record = menu.addAction("Export rollback record…", lambda: self.record(True))
        actions.setMenu(menu)
        bar.addWidget(actions)
        layout.addLayout(bar)
        self.notice = QLabel()
        self.notice.setTextFormat(Qt.PlainText)
        self.notice.setWordWrap(True)
        self.notice.setStyleSheet("color:#aaa;")
        layout.addWidget(self.notice)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.NoFrame)
        layout.addWidget(self.scroll, 1)
        self.checkboxes, self.configure_buttons, self.remove_buttons = {}, {}, {}
        self.detail_buttons, self.statuses, self.details, self.row_widgets = {}, {}, {}, {}

    def _library(self):
        if self.library is None:
            from external_patch_library import ExternalPatchLibrary
            self.library = ExternalPatchLibrary()
        return self.library

    def import_catalog(self, path):
        lib = self._library()
        digest = lib.import_file(path)
        from ms42_ms43_xdf_patches import catalog_matches
        catalog = lib.catalog(digest)
        if (self.window.patches_splitter.widget(0) is self and digest != self.sources.currentData()
                and catalog_matches(catalog, self.image)):
            if self.has_changes():
                if self.sources.findData(digest) < 0:
                    self.sources.addItem(Path(path).name, digest)
            else:
                self.open_image(self.image, preferred=digest)
                lib.select(self.image, digest)
        return catalog.title

    def open_image(self, data, preferred=None):
        lib = self._library()
        history = lib.receipt(data)
        choices = lib.catalogs(data)
        ids = [entry["id"] for entry in choices]
        if preferred not in ids:
            preferred = lib.preferred(data, choices)
        self.export_record.setEnabled(bool(history))
        self.image, self.reference = data, None
        self.history = history if history and history["operations"] else None
        self.sources.blockSignals(True)
        self.sources.clear()
        self.sources.addItem("Select a patch list…" if ids else "Import a matching community patch list…", None)
        for entry in choices:
            self.sources.addItem(entry["label"], entry["id"])
        index = ids.index(preferred) + 1 if preferred in ids else 1 if len(ids) == 1 else 0
        self.sources.setCurrentIndex(index)
        self.sources.blockSignals(False)
        self._select()

    def _select(self, *, remember=False):
        from ms42_ms43_xdf_patches import inspect
        digest = self.sources.currentData()
        if remember and self.catalog and self.has_changes():
            if QMessageBox.question(self, "Change patch list", "Discard the unsaved changes for this list?",
                                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                self.sources.blockSignals(True)
                self.sources.setCurrentIndex(self.sources.findData(self.catalog.digest))
                self.sources.blockSignals(False)
                return
        self.sources.setToolTip(self.sources.currentText())
        self.catalog = None
        self.rows, self.actions, self.drafts = [], {}, {}
        self.field_owners = {}
        self.settings.setText("All XDF settings…")
        self.reference = None
        try:
            if digest:
                self.catalog = self._library().catalog(digest)
                self.rows = inspect(self.catalog, self.image, self.history)
                if remember:
                    self._library().select(self.image, digest)
        except (ValueError, OSError) as error:
            self.catalog = None
            self.rows = []
            QMessageBox.warning(self, "Patch list unavailable", str(error))
        self._render_rows()
        self.settings.setEnabled(self.catalog is not None)
        self.add_reference.setEnabled(self.catalog is not None)
        self.import_record.setEnabled(self.catalog is not None)
        try:
            self.undo_build.setEnabled(self._library().previous(self.image) is not None)
        except (ValueError, OSError) as error:
            self.undo_build.setEnabled(False)
            self.window.patches_log.append("Undo unavailable: " + escape(str(error)))
        self.changed.emit()

    def _render_rows(self):
        self.checkboxes, self.configure_buttons, self.remove_buttons = {}, {}, {}
        self.detail_buttons, self.statuses, self.details, self.row_widgets = {}, {}, {}, {}
        body = QWidget()
        body.setFont(self.font())
        rows = QVBoxLayout(body)
        rows.setContentsMargins(0, 0, 0, 0)
        rows.setAlignment(Qt.AlignTop)
        self.notice.setText("\n".join(self.catalog.notices) if self.catalog else "")
        self.notice.setVisible(bool(self.notice.text()))
        if not self.rows:
            rows.addWidget(QLabel("Select a matching patch list to see its patches."))
            self.scroll.setWidget(body)
            return
        header = QWidget()
        header.setObjectName("xdf_patch_header")
        header.setAttribute(Qt.WA_StyledBackground, True)
        header.setStyleSheet("QWidget#xdf_patch_header{background:#303030;border-bottom:1px solid #444;}")
        font = body.font()
        font.setBold(True)
        header.setFont(font)
        metrics = header.fontMetrics()
        status_width = max(metrics.horizontalAdvance(text) for text in
                           ("Not installed", "Matching bytes", "Install pending", "Removal pending",
                            "Unsupported", "Requires patch", "Conflicting bytes")) + 8
        probe = QPushButton()
        probe.setFont(body.font())
        action_width = 0
        for text in ("Configure *", "Cancel removal", "Restore baseline", "Details"):
            probe.setText(text)
            action_width = max(action_width, probe.sizeHint().width())
        probe.deleteLater()
        header_layout = QGridLayout(header)
        header_layout.setContentsMargins(0, 5, 0, 5)
        header_layout.setHorizontalSpacing(8)
        header_layout.setColumnStretch(0, 1)
        for column, text, width in ((0, "Patch", None), (1, "State", status_width),
                                    (2, "Actions", action_width * 3 + 16)):
            label = QLabel(text)
            label.setStyleSheet("color:#aaa;")
            if width:
                label.setFixedWidth(width)
                label.setAlignment(Qt.AlignCenter)
            else:
                label.setIndent(18)
            header_layout.addWidget(label, 0, column, 1, 3 if column == 2 else 1)
        rows.addWidget(header)
        self.header = header
        for entry in self.rows:
            key = entry["id"]
            row = QWidget()
            grid = QGridLayout(row)
            grid.setContentsMargins(0, 4, 0, 4)
            grid.setHorizontalSpacing(8)
            grid.setColumnStretch(0, 1)
            checkbox = QCheckBox(entry["title"])
            checkbox.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
            checkbox.setStyleSheet("QCheckBox{font-weight:normal;color:#d4d4d4;}")
            checkbox.setToolTip("\n".join(filter(None, (entry["title"], entry["reason"], entry.get("compatibility_note"), entry["description"]))))
            checkbox.setChecked(entry["state"] in ("applied", "configured", "matching"))
            checkbox.setEnabled(entry["installable"] or entry["state"] == "requires")
            checkbox.toggled.connect(lambda selected, k=key: self._set_action(k, "install" if selected else ""))
            grid.addWidget(checkbox, 0, 0)
            status = QLabel()
            status.setFixedWidth(status_width)
            status.setAlignment(Qt.AlignCenter)
            grid.addWidget(status, 0, 1)
            configure = QPushButton("Configure")
            configure.setFixedWidth(action_width)
            configure.setEnabled(entry["has_settings"])
            configure.setToolTip("Configure this patch's settings, including values saved before installation."
                                 if entry["has_settings"] else "This patch has no associated configuration settings.")
            configure.clicked.connect(lambda _=False, k=key: self.configure(k))
            grid.addWidget(configure, 0, 2)
            remove = QPushButton()
            remove.setFixedWidth(action_width)
            remove.setEnabled(entry["removable"])
            remove.setStyleSheet("QPushButton:enabled{color:#f0a0a0;}")
            remove.clicked.connect(lambda _=False, k=key: self._set_action(k,
                                   "" if self.actions.get(k) == "remove" else "remove"))
            grid.addWidget(remove, 0, 3)
            details = QDialog(row, Qt.Popup)
            details.setWindowTitle(entry["title"])
            details.setStyleSheet("QDialog{border:1px solid #555;}")
            popup_layout = QVBoxLayout(details)
            popup_layout.setContentsMargins(1, 1, 1, 1)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QScrollArea.NoFrame)
            content = QWidget()
            content_layout = QVBoxLayout(content)
            content_layout.setContentsMargins(12, 12, 12, 12)
            content_layout.setSpacing(8)
            content_layout.setAlignment(Qt.AlignTop)
            rollback = ("Restore XDF baseline uses the original bytes declared by this XDF; "
                        "no saved installation record is available." if entry["rollback_kind"] == "xdf_baseline"
                        else "Removal restores the original bytes saved in this image's rollback record."
                        if entry["rollback_kind"] == "recorded" else "")
            for index, text in enumerate((entry["title"], entry["description"], entry["reason"],
                                           entry.get("compatibility_note"), rollback, *self.catalog.notices)):
                if not text:
                    continue
                label = QLabel(text)
                label.setTextFormat(Qt.PlainText)
                label.setWordWrap(True)
                label.setTextInteractionFlags(Qt.TextSelectableByMouse)
                label.setStyleSheet("font-weight:bold;" if index == 0 else "color:#aaa;")
                content_layout.addWidget(label)
            scroll.setWidget(content)
            popup_layout.addWidget(scroll)
            detail = QPushButton("Details")
            detail.setFixedWidth(action_width)
            detail.setToolTip("Show description and requirements. Close with Escape or click outside.")
            detail.clicked.connect(lambda _=False, popup=details, button=detail:
                                   self.window._show_patch_details(popup, button))
            grid.addWidget(detail, 0, 4)
            self.checkboxes[key], self.statuses[key] = checkbox, status
            self.configure_buttons[key], self.remove_buttons[key] = configure, remove
            self.detail_buttons[key], self.details[key], self.row_widgets[key] = detail, details, row
            if checkbox.isEnabled() or entry["removable"]:
                self.actions[key] = ""
            rows.addWidget(row)
            self._update_row(entry)
        self.scroll.setWidget(body)

    def _update_row(self, row):
        key = row["id"]
        action = self.actions.get(key, "")
        status = {"ready": "Not installed", "needs_original": "Not installed", "requires": "Requires patch",
                  "applied": "Installed", "configured": "Configured", "matching": "Matching bytes",
                  "unavailable": "Unsupported", "conflict": "Conflicting bytes"}.get(row["state"], row["state"].replace("_", " "))
        if row["read_only"]:
            status = "Read-only"
        if action:
            status = "Install pending" if action == "install" else "Removal pending"
        self.statuses[key].setText(status)
        self.statuses[key].setStyleSheet("color:#e8c46a;" if action else
                                         "color:#9ece6a;" if row["state"] in ("applied", "configured", "matching") else "color:#aaa;")
        self.statuses[key].setToolTip(row["reason"])
        removal = self.remove_buttons[key]
        removal.setText("Cancel removal" if action == "remove" else
                        "Restore baseline" if row["rollback_kind"] == "xdf_baseline" else "✕ Remove")
        removal.setToolTip("Cancel the pending removal." if action == "remove" else
                          "Restore XDF baseline: use the original bytes declared in the XDF, without an installation record."
                          if row["rollback_kind"] == "xdf_baseline" else
                          "Remove using the original bytes saved in this image's rollback record."
                          if row["removable"] else row["reason"] or "No recoverable installation was found in this image.")

    def _set_action(self, key, action):
        self.actions[key] = action
        self._update_row(next(row for row in self.rows if row["id"] == key))
        self.changed.emit()

    def has_changes(self):
        return bool(self.drafts or any(self.actions.values()))

    def choose_reference(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose a matching reference BIN", "", "BIN (*.bin)")
        if path:
            try:
                from ms42_ms43_xdf_patches import catalog_matches
                data = Path(path).read_bytes()
                if not catalog_matches(self.catalog, data):
                    raise ValueError("The reference must match this catalog's family, software and edition.")
                self.reference = data
                self.window.patches_log.append("Reference BIN: " + escape(Path(path).name))
            except (OSError, ValueError) as error:
                QMessageBox.warning(self, "Reference BIN unavailable", str(error))

    def record(self, export):
        from external_patch_library import EditedPatchImage
        chooser = QFileDialog.getSaveFileName if export else QFileDialog.getOpenFileName
        path, _ = chooser(self, "Export rollback record" if export else "Import rollback record",
                          "patch-rollback.json" if export else "", "Rollback record (*.json)")
        if path:
            try:
                if export:
                    self._library().export_receipt(self.image, path)
                else:
                    try:
                        self._library().import_receipt(self.image, path)
                    except EditedPatchImage:
                        if QMessageBox.question(
                                self, "Attach rollback record",
                                "Attach this record to the edited BIN after checking every owned range?",
                                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                            return
                        self._library().import_receipt(self.image, path, allow_edited=True)
                    self.open_image(self.image)
                self.window.patches_log.append(("Exported" if export else "Imported") + " rollback record.")
            except (ValueError, OSError) as error:
                QMessageBox.warning(self, "Rollback record unavailable", str(error))

    def _requested(self, action):
        return [key for key, selected in self.actions.items() if selected == action]

    def undo(self):
        from ms42_ms43_image import image_identity
        try:
            previous = self._library().previous(self.image)
            if previous is None:
                raise ValueError("The previous BIN for this build is unavailable.")
            summary = "Undid last XDF build"
            self.window._patch_output(previous, image_identity(previous)["family"], "Rollback", summary,
                                      source="Patched: " + summary, notes=self.catalog.title if self.catalog else summary,
                                      original=self.image)
        except (ValueError, OSError, RuntimeError) as error:
            QMessageBox.warning(self, "Undo unavailable", str(error))

    def _compose(self, *, preview=False, drafts=None):
        from ms42_ms43_xdf_patches import build
        install, remove = self._requested("install"), self._requested("remove")
        needs = []
        for row in self.rows:
            if row["id"] in install and row["needs_original"]:
                missing = []
                for span in row["unguarded"]:
                    start, size = span["start"], span["size"]
                    if self.reference is None or self.reference[start:start+size] != self.image[start:start+size]:
                        missing.append(f"0x{start:05X} ({size} bytes)")
                if missing:
                    needs.append(f"{row['title']} — {', '.join(missing)}")
        if needs and not preview:
            answer = QMessageBox.question(
                self, "Capture original bytes",
                "The XDF does not provide original bytes for these ranges. Apply the selected patches "
                "and save the current BIN bytes for rollback?\n\n" + "\n".join(needs),
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if answer != QMessageBox.Yes:
                return None
        drafts = self.drafts if drafts is None else drafts
        changes = [dict(field_id=field, axis=axis, row=row, column=col, value=value)
                   for (field, axis, row, col), value in drafts.items()]
        return build(self.catalog, self.image, install=install, remove=remove, changes=changes,
                     history=self.history, reference=self.reference, allow_unbased=bool(needs))

    def configure(self, group_id=None):
        from ms42_ms43_xdf_patches import configuration
        try:
            if self._requested("install") or self._requested("remove"):
                data, history = self._compose(preview=True, drafts={})
            else:
                data, history = self.image, self.history
            fields = configuration(self.catalog, data, history)
            self.field_owners = {field["id"]: field.get("owners", ()) for field in fields}
            if group_id is not None:
                fields = [field for field in fields if group_id in field.get("owners", ())]
            if not fields:
                QMessageBox.information(self, "Patch settings", "This catalog has no configuration fields.")
                return
            dialog = FieldDialog(fields, self.drafts, self)
            dialog.setWindowTitle("Configure " + next(row["title"] for row in self.rows if row["id"] == group_id)
                                  if group_id is not None else "All XDF settings")
            while dialog.exec_() == QDialog.Accepted:
                try:
                    if dialog.edits:
                        self._compose(preview=True, drafts=dialog.edits)
                except ValueError as error:
                    QMessageBox.warning(self, "Check patch settings", str(error))
                    continue
                self.drafts = dialog.edits
                pending_fields = {key[0] for key in self.drafts}
                for key, button in self.configure_buttons.items():
                    pending = any(key in self.field_owners.get(field, ()) for field in pending_fields)
                    button.setText("Configure *" if pending else "Configure")
                self.settings.setText("All XDF settings *" if self.drafts else "All XDF settings…")
                self.changed.emit()
                break
        except (ValueError, OSError) as error:
            QMessageBox.warning(self, "Patch settings unavailable", str(error))

    def build_output(self, *, temporary=False):
        if not self.has_changes() or not self.catalog:
            return
        try:
            result = self._compose()
            if result is None:
                return
            output, history = result
            changed = [(row["title"], action) for row in self.rows if (action := self.actions.get(row["id"]))]
            edits = [f"{action.capitalize()} {title}" for title, action in changed]
            if self.drafts:
                edits.append(f"Configured {len(self.drafts)} cells")
            summary = "; ".join(edits)
            title = changed[0][0] if len(changed) == 1 else "Patched"
            if changed and not self.drafts and all(action == "remove" for _, action in changed):
                title = "Rollback"
            lib, parent = self._library(), self.image
            self.window._patch_output(
                output, self.catalog.family, title, summary, source="Patched: " + summary,
                notes=self.catalog.title, temporary=temporary, original=parent,
                before_output=lambda: lib.save_receipt(output, history, parent=parent))
        except (ValueError, OSError, RuntimeError) as error:
            QMessageBox.warning(self, "Patch build failed", str(error))
