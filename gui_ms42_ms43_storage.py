"""Explicit MS42/MS43 storage backups, separate from ROM identity grafting."""

from datetime import datetime
from hashlib import sha256
from pathlib import Path

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFontDatabase, QValidator
from PyQt5.QtWidgets import (
    QAbstractItemView, QApplication, QComboBox, QDialog, QDialogButtonBox,
    QFileDialog, QGridLayout, QGroupBox, QHeaderView, QHBoxLayout, QInputDialog, QLabel, QMessageBox, QPushButton,
    QPlainTextEdit, QDoubleSpinBox, QTableWidget, QTableWidgetItem, QTabWidget,
    QVBoxLayout, QWidget,
)

from ms42_ms43_resident import PROFILES
from backup_manager import BackupIndexError
from gui_controls import ConfigComboBox
from ms42_ms43_storage_units import storage_display_value, storage_raw_value, storage_format_value


LAYOUTS = (("MS43", "eeprom"), ("MS42", "retained"), ("MS43", "retained"))


class _StorageSpinBox(QDoubleSpinBox):
    def __init__(self, field):
        super().__init__()
        self.field = field
        self._special = {**field.get("sentinel_values", {}),
                         **{raw: storage_format_value(field, raw) for raw in field.get("excluded_values", ())}}
        self.setDecimals(field.get("decimals", 0))
        self.setSuffix(f" {field['unit']}" if field.get("unit") else "")
        self.setSingleStep(abs(field.get("scale", 1)))
        self.setKeyboardTracking(False)
        self.valueChanged.connect(self._normalize)

    def textFromValue(self, value):
        raw = storage_raw_value(getattr(self, "field", {}), value)
        return getattr(self, "_special", {}).get(raw, super().textFromValue(value))

    def valueFromText(self, text):
        for raw, label in self._special.items():
            if text.strip() == label:
                return storage_display_value(self.field, raw)
        return super().valueFromText(text)

    def validate(self, text, position):
        if text.strip() in getattr(self, "_special", {}).values():
            return QValidator.Acceptable, text, position
        return super().validate(text, position)

    def raw_value(self):
        return storage_raw_value(self.field, self.value())

    def set_raw_value(self, raw):
        self.setValue(storage_display_value(self.field, raw))

    def _normalize(self):
        # Show the representable stored value, including after repeated arrow steps.
        blocked = self.blockSignals(True)
        try:
            raw = self.raw_value()
            self.setSuffix(f" {self.field['unit']}" if self.field.get("unit") and raw not in self._special else "")
            self.set_raw_value(raw)
        finally:
            self.blockSignals(blocked)

    def wheelEvent(self, event):
        event.ignore()


class _StorageBackupDialog(QDialog):
    """Inspect original bytes and prepare a local draft; saving/restoring stays outside."""

    def __init__(self, owner, snapshot):
        super().__init__(owner)
        from ms42_ms43_diagnostics import format_storage_inspection

        self._snapshot = dict(snapshot, data=bytes(snapshot["data"]))
        self._calibration = None
        self.edited_snapshot = None
        self._editors = {}
        self._limits = {}
        self._repairs = set()
        self._history = []
        self._history_index = -1
        self._populating = False
        self._restoring = False
        self._comparison_active = False
        self.setWindowTitle(f"{snapshot['family']} Storage Backup")
        area = QApplication.primaryScreen().availableGeometry()
        self.resize(min(840, area.width() - 60), min(620, area.height() - 60))
        layout = QVBoxLayout(self)
        toolbar = QHBoxLayout()
        self.undo_button = QPushButton("Undo")
        self.redo_button = QPushButton("Redo")
        self.compare_button = QPushButton("Compare file…")
        self.undo_button.clicked.connect(lambda: self._restore_history(-1))
        self.redo_button.clicked.connect(lambda: self._restore_history(1))
        self.compare_button.clicked.connect(self._compare_file)
        for button in (self.undo_button, self.redo_button, self.compare_button):
            toolbar.addWidget(button)
        layout.addLayout(toolbar)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        text = QPlainTextEdit()
        self.inspection = text
        text.setReadOnly(True)
        text.setLineWrapMode(QPlainTextEdit.NoWrap)
        text.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        data = snapshot["data"]
        inspection = format_storage_inspection(
            data, snapshot["family"], snapshot["software_id"], snapshot["kind"])
        text.setPlainText(inspection + "\n\nRaw bytes (file offsets)\n" + "\n".join(
            f"{offset:04X}  {data[offset:offset + 16].hex(' ').upper()}"
            for offset in range(0, len(data), 16)))
        self.tabs.addTab(text, "Original / raw bytes")

        page = QWidget()
        fields_layout = QVBoxLayout(page)
        self._draft_note = "Apply updates the loaded backup only. Saving and ECU restoration are separate actions."
        self.note = QLabel(self._draft_note)
        self.note.setWordWrap(True)
        fields_layout.addWidget(self.note)
        self.calibration_button = QPushButton("Load matching calibration…")
        self.calibration_button.setToolTip(
            "Use the calibration saved from this ECU to supply limits for additional stored fields. No ECU read is performed.")
        self.calibration_button.clicked.connect(self._load_calibration)
        fields_layout.addWidget(self.calibration_button)
        self.calibration_note = QLabel("Optional calibration context: no file loaded.")
        self.calibration_note.setWordWrap(True)
        fields_layout.addWidget(self.calibration_note)
        self.fields = QTableWidget(0, 4)
        self.fields.setHorizontalHeaderLabels(("Stored field", "Original", "Draft", "File offset"))
        self.fields.verticalHeader().hide()
        self.fields.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.fields.setSelectionMode(QAbstractItemView.NoSelection)
        self.fields.setWordWrap(True)
        self.fields.setTextElideMode(Qt.ElideNone)
        self.fields.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        for column in (1, 2, 3):
            self.fields.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeToContents)
        self.fields.horizontalHeader().sectionResized.connect(lambda *_: self.fields.resizeRowsToContents())
        fields_layout.addWidget(self.fields, 1)
        self.tabs.addTab(page, "Verified fields")
        knock_page = QWidget()
        knock_layout = QVBoxLayout(knock_page)
        self.knock_note = QLabel("Stored averaged knock values (raw). Load matching calibration under Verified fields for axes.")
        self.knock_note.setWordWrap(True)
        knock_layout.addWidget(self.knock_note)
        self.knock_table = QTableWidget(16, 4)
        self.knock_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.knock_table.itemChanged.connect(self._knock_changed)
        knock_layout.addWidget(self.knock_table, 1)
        self.tabs.addTab(knock_page, "Learned knock")
        checks_page = QWidget()
        checks_layout = QVBoxLayout(checks_page)
        self.checks = QTableWidget(0, 4)
        self.checks.setHorizontalHeaderLabels(("Record", "File offset", "Check", "Details"))
        self.checks.verticalHeader().hide()
        self.checks.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.checks.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.checks.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.checks.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        for column in (1, 2):
            self.checks.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeToContents)
        self.checks.itemSelectionChanged.connect(self._update_repair_button)
        checks_layout.addWidget(self.checks, 1)
        self.repair_button = QPushButton("Repair selected invalid checks")
        self.repair_button.setToolTip("Preserve the selected records' stored values and repair their checks in this draft. Undo is available.")
        self.repair_button.clicked.connect(self._repair_checks)
        checks_layout.addWidget(self.repair_button)
        self.checks_note = QLabel("Checks update automatically for edited fields. Repair accepts the selected record's existing payload.")
        self.checks_note.setWordWrap(True)
        checks_layout.addWidget(self.checks_note)
        self.tabs.addTab(checks_page, "Record checks")
        self.comparison = QPlainTextEdit()
        self.comparison.setReadOnly(True)
        self.comparison.setPlainText("Choose Compare file to compare the current draft with a saved backup.")
        self.tabs.addTab(self.comparison, "Comparison")
        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        self.apply_button = buttons.addButton("Apply to loaded backup", QDialogButtonBox.AcceptRole)
        self.apply_button.setEnabled(False)
        buttons.accepted.connect(self._apply)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._populate_fields()
        self._remember_state()

    def _draft_values(self):
        drafts = {}
        for offset, (original, editor) in self._editors.items():
            value = editor.currentData() if isinstance(editor, QComboBox) else editor.raw_value()
            if value != original:
                drafts[offset] = value
        return drafts

    def _base_snapshot(self):
        if not self._repairs:
            return self._snapshot
        from ms42_ms43_storage_edit import repair_storage_checks
        return repair_storage_checks(self._snapshot, self._repairs)

    def _remember_state(self):
        if not self._restoring:
            state = (self._calibration, self.calibration_note.text(), frozenset(self._repairs), self._draft_values())
            if self._history_index < 0 or state != self._history[self._history_index]:
                self._invalidate_comparison()
                self._history[self._history_index + 1:] = [state]
                self._history_index += 1
        idle = not self.parent()._ms42_ms43_interrupted()
        self.undo_button.setEnabled(idle and self._history_index > 0)
        self.redo_button.setEnabled(idle and self._history_index + 1 < len(self._history))

    def _restore_history(self, direction):
        index = self._history_index + direction
        if self.parent()._ms42_ms43_interrupted() or not 0 <= index < len(self._history):
            return
        self._history_index = index
        self._invalidate_comparison()
        self._calibration, note, repairs, drafts = self._history[index]
        self.calibration_note.setText(note)
        self._repairs = set(repairs)
        self._restoring = True
        try:
            self._populate_fields(drafts=drafts)
        finally:
            self._restoring = False
        self._remember_state()

    def _populate_fields(self, *, drafts=None):
        from ms42_ms43_storage_edit import editable_fields

        if drafts is None:
            drafts = self._draft_values()
        self._populating = True
        self._editors = {}
        self._limits = {}
        self.apply_button.setEnabled(False)
        try:
            interrupted = self.parent()._ms42_ms43_interrupted()
            self.calibration_button.setEnabled(not interrupted)
            if interrupted:
                raise ValueError("Programming is incomplete. Inspection is available; finish recovery before editing the loaded backup.")
            definitions = editable_fields(self._base_snapshot(), calibration=self._calibration)
        except ValueError as error:
            definitions = []
            self.note.setText(str(error))
        else:
            if not definitions:
                self.note.setText("No verified editable records are available in this backup. Inspection remains available.")
        self.fields.setRowCount(len(definitions))
        for row, field in enumerate(definitions):
            label = QTableWidgetItem(f"{field['group']}\n{field['label']}")
            label.setToolTip(label.text())
            self.fields.setItem(row, 0, label)
            self.fields.setItem(row, 1, QTableWidgetItem(storage_format_value(field, field["value"])))
            self.fields.item(row, 1).setToolTip(f"Stored value: {field['value']}")
            self.fields.item(row, 1).setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.fields.setItem(row, 3, QTableWidgetItem(f"0x{field['offset']:04X}"))
            if field["choices"] is not None:
                editor = ConfigComboBox()
                if field["value"] not in field["choices"]:
                    editor.addItem(f"Current: {field['value']} (unrecognized)", field["value"])
                    editor.model().item(0).setEnabled(False)
                for value in field["choices"]:
                    editor.addItem(storage_format_value(field, value), value)
                editor.setCurrentIndex(editor.findData(field["value"]))
                editor.currentIndexChanged.connect(self._update_changes)
            else:
                editor = _StorageSpinBox(field)
                draft = drafts.get(field["offset"], field["value"])
                bounds = [storage_display_value(field, raw) for raw in
                          (field["minimum"], field["maximum"], field["value"], draft)]
                editor.setRange(min(bounds), max(bounds))
                editor.set_raw_value(draft)
                minimum, maximum = sorted((field["minimum"], field["maximum"]),
                                          key=lambda raw: storage_display_value(field, raw))
                editor.setToolTip(f"Allowed: {storage_format_value(field, minimum)} to "
                                  f"{storage_format_value(field, maximum)}. Original stored value: {field['value']}.")
                editor.valueChanged.connect(self._update_changes)
                editor.lineEdit().textEdited.connect(self._update_changes)
            if field["choices"] is not None and field["offset"] in drafts:
                editor.setCurrentIndex(editor.findData(drafts[field["offset"]]))
            self._editors[field["offset"]] = (field["value"], editor)
            self._limits[field["offset"]] = (field["minimum"], field["maximum"], field["label"], field.get("excluded_values", ()))
            self.fields.setCellWidget(row, 2, editor)
        self.fields.resizeRowsToContents()
        self._knock_fields = [field for field in definitions if field["width"] == 1 and
                              field["group"] in ("Averaged knock adaptation", "Knock record")]
        self._populating = False
        self._refresh_checks()
        self._sync_knock()
        if definitions or self._repairs and not interrupted:
            self._update_changes()
        else:
            self._remember_state()

    def _load_calibration(self):
        from ms42_ms43_storage_edit import editable_fields

        if self.parent()._ms42_ms43_interrupted():
            self._populate_fields()
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Calibration saved from this ECU", "", "Binary Files (*.bin)")
        if not path:
            return
        try:
            data = Path(path).read_bytes()
            editable_fields(self._snapshot, calibration=data)
        except (OSError, ValueError) as error:
            self.note.setText(str(error))
            return
        self._calibration = data
        self.calibration_note.setText(
            f"Limits from {Path(path).name}. Use the ECU's current calibration; matching software alone cannot identify tuning changes.")
        self._populate_fields()
        self._remember_state()

    def _sync_knock(self):
        self.knock_table.blockSignals(True)
        try:
            self.knock_table.clearContents()
            self.knock_table.setVerticalHeaderLabels([f"RPM row {i + 1}" for i in range(16)])
            self.knock_table.setHorizontalHeaderLabels([f"Load {i + 1}" for i in range(4)])
            available = len(self._knock_fields) == 64
            self.knock_table.setEnabled(available)
            unit = self._knock_fields[0].get("unit", "raw units") if available else ""
            self.knock_note.setText(f"Stored averaged knock values ({unit}). Load matching calibration under Verified fields for axes."
                                    if available else "No verified averaged knock record is available in this backup.")
            if not available:
                return
            for index, field in enumerate(self._knock_fields):
                value = self._editors[field["offset"]][1].raw_value()
                display = storage_display_value(field, value)
                item = QTableWidgetItem(f"{display:.{field.get('decimals', 0)}f}")
                item.setTextAlignment(Qt.AlignCenter)
                item.setToolTip(f"0x{field['offset']:04X} · Stored value: {value} · Allowed: "
                                f"{storage_format_value(field, field['minimum'])} to {storage_format_value(field, field['maximum'])}")
                self.knock_table.setItem(index // 4, index % 4, item)
            if self._calibration is not None:
                from ms42_ms43_storage_edit import storage_knock_axes
                axes = storage_knock_axes(self._base_snapshot(), self._calibration)
                self.knock_table.setVerticalHeaderLabels([f"{value:g} rpm" for value in axes["rpm"]])
                self.knock_table.setHorizontalHeaderLabels([f"{value:.1f} mg/stroke" for value in axes["load"]])
                self.knock_note.setText(f"Stored averaged knock values ({unit}), with axes from the loaded calibration.")
        except ValueError as error:
            self.knock_note.setText(str(error))
        finally:
            self.knock_table.blockSignals(False)

    def _knock_changed(self, item):
        if self._populating or not self.knock_table.isEnabled():
            return
        field = self._knock_fields[item.row() * 4 + item.column()]
        try:
            value = storage_raw_value(field, item.text())
            if not field["minimum"] <= value <= field["maximum"]:
                raise ValueError()
        except ValueError:
            self._sync_knock()
            self.knock_note.setText(f"Enter a value from {storage_format_value(field, field['minimum'])} "
                                   f"to {storage_format_value(field, field['maximum'])}.")
            return
        self._editors[field["offset"]][1].set_raw_value(value)
        self._sync_knock()

    def _draft_snapshot(self):
        from ms42_ms43_storage_edit import edit_storage
        changes = self._changes()
        if self._calibration is None:
            return edit_storage(self._base_snapshot(), changes)
        return edit_storage(self._base_snapshot(), changes, calibration=self._calibration)

    def _refresh_checks(self):
        from ms42_ms43_storage_edit import storage_record_checks
        try:
            rows = storage_record_checks(self._base_snapshot())
        except ValueError as error:
            rows = []
            self.checks_note.setText(str(error))
        self.checks.setRowCount(len(rows))
        for index, row in enumerate(rows):
            status = "Erased" if row["erased"] else "Valid" if row["crc_valid"] else "Invalid"
            if row["offset"] in self._repairs:
                status = "Repaired draft"
            values = (row["name"], f"0x{row['offset']:04X}", status, row["reason"])
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                if column == 0:
                    item.setData(Qt.UserRole, row)
                self.checks.setItem(index, column, item)
        self.checks.resizeRowsToContents()
        self._update_repair_button()

    def _selected_repairs(self):
        rows = [self.checks.item(index.row(), 0).data(Qt.UserRole)
                for index in self.checks.selectionModel().selectedRows()]
        return {row["offset"] for row in rows if row["repairable"]}

    def _update_repair_button(self):
        self.repair_button.setEnabled(bool(self._selected_repairs()) and not self.parent()._ms42_ms43_interrupted())

    def _repair_checks(self):
        from ms42_ms43_storage_edit import repair_storage_checks
        if self.parent()._ms42_ms43_interrupted():
            self._populate_fields()
            return
        proposed = self._repairs | self._selected_repairs()
        if proposed == self._repairs:
            return
        try:
            repair_storage_checks(self._snapshot, proposed)
        except ValueError as error:
            self.checks_note.setText(str(error))
            return
        self._repairs = proposed
        self._populate_fields()

    def _compare_file(self):
        from ms42_ms43_storage import decode_snapshot, make_snapshot
        from ms42_ms43_storage_edit import compare_storage_fields

        path, _ = QFileDialog.getOpenFileName(
            self, "Compare with saved backup — raw BIN uses the loaded layout", "",
            "Storage Backups (*.json *.bin);;All Files (*)")
        if not path:
            return
        try:
            draft = self._draft_snapshot()
            if Path(path).suffix.lower() == ".json":
                other = decode_snapshot(Path(path).read_text(encoding="utf-8"))
            else:
                other = make_snapshot(self._snapshot["family"], self._snapshot["kind"], Path(path).read_bytes(),
                                      software_id=self._snapshot["software_id"])
            rows = compare_storage_fields(draft, other)
            lines = [f"Current draft → {Path(path).name}",
                     f"Compared as {other['family']} {other['software_id']} · {other['kind']}", ""]
            lines += [f"{row['group']} · {row['label']} · 0x{row['offset']:04X}: {row['before']} → {row['after']}"
                      for row in rows]
            self.comparison.setPlainText("\n".join(lines) if rows else "\n".join(lines + ["No differences."]))
        except (OSError, ValueError) as error:
            self.comparison.setPlainText(str(error))
        self._comparison_active = True
        self.tabs.setCurrentWidget(self.comparison)

    def _invalidate_comparison(self):
        if self._comparison_active:
            self.comparison.setPlainText("The draft changed. Choose Compare file again to refresh these differences.")
            self._comparison_active = False

    def _changes(self):
        changes = {}
        for offset, (original, editor) in self._editors.items():
            value = editor.currentData() if isinstance(editor, QComboBox) else editor.raw_value()
            if value != original:
                minimum, maximum, label, excluded = self._limits[offset]
                if not minimum <= value <= maximum:
                    field = editor.field if isinstance(editor, _StorageSpinBox) else {}
                    low, high = sorted((minimum, maximum), key=lambda raw: storage_display_value(field, raw))
                    raise ValueError(f"{label}: enter a value from {storage_format_value(field, low)} "
                                     f"to {storage_format_value(field, high)}.")
                if value in excluded:
                    raise ValueError(f"{label}: {value} is reserved for an uninitialized record.")
                changes[offset] = value
        return changes

    def _update_changes(self, *_):
        if self._populating:
            return
        try:
            pending = any(isinstance(editor, _StorageSpinBox) and editor.hasAcceptableInput()
                          and storage_raw_value(editor.field, editor.valueFromText(editor.text())) != editor.raw_value()
                          for _, editor in self._editors.values())
            changed = bool(self._changes() or self._repairs or pending)
        except ValueError as error:
            self.note.setText(str(error))
            changed = False
        else:
            self.note.setText(self._draft_note)
        self.apply_button.setEnabled(changed)
        self._remember_state()
        self._sync_knock()

    def _apply(self):
        if self.parent()._ms42_ms43_interrupted():
            self._populate_fields()
            return
        try:
            for _, editor in self._editors.values():
                if isinstance(editor, _StorageSpinBox):
                    if not editor.hasAcceptableInput():
                        raise ValueError("Enter a valid value before applying changes.")
                    editor.interpretText()
            changes = self._changes()
            if not changes and not self._repairs:
                return
            self.edited_snapshot = self._draft_snapshot()
        except ValueError as error:
            self.note.setText(str(error))
            self.tabs.setCurrentIndex(1)
            return
        self.accept()


class MS42MS43StorageDesktop:
    def _ms42_ms43_build_storage(self, layout):
        self._ms42_ms43_storage_snapshot = None
        self._ms42_ms43_storage_source = ""
        group = QGroupBox("Memory backup and restore")
        grid = QGridLayout(group)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        self._ms42_ms43_storage_layout = ConfigComboBox()
        self._ms42_ms43_storage_layout.setToolTip(
            "Select the memory to read, or the layout for loading a raw BIN. "
            "Changing this selection does not change the loaded backup.")
        self._ms42_ms43_select_storage_layout()
        grid.addWidget(self._ms42_ms43_storage_layout, 0, 0, 1, 2)
        self._ms42_ms43_storage_status = QLabel("No storage backup loaded.")
        self._ms42_ms43_storage_status.setWordWrap(True)
        grid.addWidget(self._ms42_ms43_storage_status, 1, 0, 1, 2)
        self._ms42_ms43_storage_read = self._op_btn(
            "Read Storage", "#1e5080", self._ms42_ms43_read_storage)
        self._ms42_ms43_storage_load = self._op_btn(
            "Load Backup…", "#3d3d3d", self._ms42_ms43_load_storage)
        self._ms42_ms43_storage_save = self._op_btn(
            "Save Loaded Backup…", "#3d3d3d", self._ms42_ms43_save_storage)
        self._ms42_ms43_storage_view = self._op_btn(
            "Inspect / Edit Backup…", "#3d3d3d", self._ms42_ms43_view_storage)
        self._ms42_ms43_storage_write = self._op_btn(
            "Restore Loaded Backup…", "#7a2f2f", self._ms42_ms43_restore_storage)
        self._ms42_ms43_storage_retry = self._op_btn(
            "Retry Storage Restore…", "#7a2f2f", self._ms42_ms43_retry)
        for index, button in enumerate((self._ms42_ms43_storage_read, self._ms42_ms43_storage_load,
                                       self._ms42_ms43_storage_save, self._ms42_ms43_storage_view,
                                       self._ms42_ms43_storage_write, self._ms42_ms43_storage_retry)):
            grid.addWidget(button, 2 + index // 2, index % 2)
        self._ms42_ms43_storage_note = QLabel()
        self._ms42_ms43_storage_note.setWordWrap(True)
        grid.addWidget(self._ms42_ms43_storage_note, 5, 0, 1, 2)
        self._ms42_ms43_storage_layout.currentIndexChanged.connect(self._ms42_ms43_update_storage)
        layout.addWidget(group)

    def _ms42_ms43_select_storage_layout(self, selected=None):
        combo = self._ms42_ms43_storage_layout
        choices = [item for item in LAYOUTS
                   if not self._is_ms42_ms43() or item[0] == self._ds2.family]
        current = selected or combo.currentData()
        previous_block = combo.blockSignals(True)
        try:
            if [combo.itemData(i) for i in range(combo.count())] != choices:
                combo.clear()
                for family, kind in choices:
                    combo.addItem("", (family, kind))
            for index, (family, kind) in enumerate(choices):
                name = ("EEPROM (2 KiB, low byte first)" if kind == "eeprom" else
                        "retained-data journal (16 KiB)" if family == "MS42" else
                        "raw flash banks (16 KiB)")
                software = (self._ecu_cal_id if self._is_ms42_ms43() and self._ds2.family == family
                            else PROFILES[family]["software"])
                snapshot = getattr(self, "_ms42_ms43_storage_snapshot", None)
                if (not self._is_ms42_ms43() and snapshot
                        and (snapshot["family"], snapshot["kind"]) == (family, kind)):
                    software = snapshot["software_id"]
                combo.setItemText(index, f"{family} {name} · {software}")
            if current in choices:
                combo.setCurrentIndex(choices.index(current))
            elif combo.currentData() not in choices:
                combo.setCurrentIndex(0)
        finally:
            combo.blockSignals(previous_block)

    def _ms42_ms43_update_storage(self):
        if not hasattr(self, "_ms42_ms43_storage_read"):
            return
        connected = self._is_ms42_ms43()
        idle = not self._task_busy and not self._actuator_cleanup_pending
        snapshot = self._ms42_ms43_storage_snapshot
        self._ms42_ms43_select_storage_layout()
        family, kind = self._ms42_ms43_storage_layout.currentData()
        memory = "EEPROM" if kind == "eeprom" else "Flash Journal" if family == "MS42" else "Flash Banks"
        self._ms42_ms43_storage_read.setText(f"Read {memory}")
        self._ms42_ms43_storage_read.setToolTip(
            "Read the selected memory using its RAM agent. " +
            ("This EEPROM has no stock DS2 read fallback." if kind == "eeprom" else
             "Uses stock DS2 if the RAM reader is unavailable; Force DS2 selects a slow read."))
        purpose = ("These flash banks are separate from the MS43 EEPROM; their use remains unverified. "
                   if (family, kind) == ("MS43", "retained") else
                   "MS42 stores learned values and diagnostic records in this flash journal. "
                   if kind == "retained" else "This is the separate MS43 serial EEPROM. ")
        self._ms42_ms43_storage_note.setText(
            purpose + "Restore Loaded Backup writes the loaded backup to the ECU. "
            "ROM flashing and identity grafting do not run this operation.")
        available = connected and not self._ms42_ms43_link_unavailable()
        same_family = available and self._ds2.family == family
        self._ms42_ms43_storage_layout.setEnabled(idle and not self._ms42_ms43_interrupted())
        self._ms42_ms43_storage_read.setEnabled(idle and same_family)
        self._ms42_ms43_storage_load.setEnabled(idle and not self._ms42_ms43_interrupted())
        self._ms42_ms43_storage_save.setEnabled(idle and snapshot is not None)
        self._ms42_ms43_storage_view.setEnabled(idle and snapshot is not None)
        self._ms42_ms43_storage_write.setEnabled(bool(
            idle and same_family and snapshot is not None
            and (snapshot["family"], snapshot["kind"]) == (family, kind)))
        state = getattr(getattr(self, "_ds2", None), "write_state", None)
        self._ms42_ms43_storage_retry.setEnabled(bool(
            idle and state and getattr(state, "operation", None) in ("eeprom_restore", "retained_restore")
            and self._ms42_ms43_interrupted() and getattr(self._ds2, "can_retry_write", False)))

    def _ms42_ms43_show_storage(self, snapshot, source):
        self._ms42_ms43_storage_snapshot = dict(snapshot, data=bytes(snapshot["data"]))
        self._ms42_ms43_storage_source = source
        self._ms42_ms43_data_view = True
        self._ms42_ms43_select_storage_layout((snapshot["family"], snapshot["kind"]))
        label = ("EEPROM" if snapshot["kind"] == "eeprom" else
                 "retained-data journal" if snapshot["family"] == "MS42" else "raw flash banks")
        empty = snapshot["data"] == b"\xFF" * len(snapshot["data"])
        status = f"Loaded backup: {source} · {snapshot['family']} {snapshot['software_id']} · {label} · {len(snapshot['data']):,} bytes"
        if empty:
            status += "\nThis backup contains only FF bytes (empty / erased data)."
            if (snapshot["family"], snapshot["kind"]) == ("MS43", "retained"):
                status += " Select EEPROM, then Read EEPROM to back up the separate EEPROM."
        self._ms42_ms43_storage_status.setText(status)
        self._ms42_ms43_storage_status.setToolTip(f"SHA-256: {sha256(snapshot['data']).hexdigest()}")
        self._ms42_ms43_update_data()

    def _ms42_ms43_catalog_storage(self, snapshot, source):
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        name = f"{snapshot['family'].lower()}_{snapshot['software_id']}_{snapshot['kind']}_{stamp}.bin"
        return self._backup_mgr.add_data(
            snapshot["data"], name, variant=snapshot["family"], source=source)

    def _ms42_ms43_read_storage(self):
        from ms42_ms43_storage import read_storage

        if not self._ms42_ms43_storage_read.isEnabled():
            return
        _, kind = self._ms42_ms43_storage_layout.currentData()
        slow = kind == "retained" and self.chk_force_slow_ds2.isChecked()

        def task(log_fn, progress_fn):
            snapshot = read_storage(self._ds2, kind, force_slow=slow, log_fn=log_fn, progress_cb=progress_fn)
            entry = self._ms42_ms43_catalog_storage(snapshot, f"ECU {kind} read")
            log_fn(f"Storage backup saved in Bins: {entry.filename}")
            return snapshot, entry.filename

        def done(result):
            self._ms42_ms43_show_storage(*result)
            self._refresh_backup_table()

        self._run_task(task, on_success=done)

    def _ms42_ms43_load_storage_bytes(self, data, source, *, family=None, kind=None):
        from ms42_ms43_storage import make_snapshot
        from ms42_ms43_storage_edit import _storage_layout
        from ms42_ms43_diagnostics import _STORAGE_BINDINGS

        selected_family, selected_kind = self._ms42_ms43_storage_layout.currentData()
        family, kind = family or selected_family, kind or selected_kind
        connected = self._is_ms42_ms43() and self._ds2.family == family
        software = self._ecu_cal_id if connected else PROFILES[family]["software"]
        snapshot = make_snapshot(family, kind, bytes(data), software_id=software)
        revisions = sorted(revision for owner, revision in _STORAGE_BINDINGS if owner == family)
        matching = []
        for revision in revisions:
            try:
                _storage_layout(dict(snapshot, software_id=revision))
            except ValueError:
                continue
            matching.append(revision)
        if len(matching) == 1:
            software = matching[0]
        elif not connected or (matching and software not in matching):
            choices = matching or revisions
            software, accepted = QInputDialog.getItem(
                self, f"{family} Storage Firmware",
                "Select the firmware used by this raw backup.\n"
                "JSON snapshots retain this information automatically.", choices,
                choices.index(software) if software in choices else 0, False)
            if not accepted:
                return
        snapshot["software_id"] = software
        self._ms42_ms43_show_storage(snapshot, source + " (selected layout)")

    def _ms42_ms43_load_storage(self):
        from ms42_ms43_storage import decode_snapshot

        path, _ = QFileDialog.getOpenFileName(
            self, "Load Storage Backup — raw BIN uses the selected layout", "",
            "Storage Backups (*.json *.bin);;All Files (*)")
        if path:
            try:
                if Path(path).suffix.lower() == ".json":
                    self._ms42_ms43_show_storage(decode_snapshot(Path(path).read_text(encoding="utf-8")),
                                                Path(path).name)
                else:
                    self._ms42_ms43_load_storage_bytes(Path(path).read_bytes(), Path(path).name)
            except (OSError, ValueError) as error:
                QMessageBox.warning(self, "Storage Backup Unavailable", str(error))

    def _ms42_ms43_save_storage(self):
        from ms42_ms43_storage import encode_snapshot

        snapshot = self._ms42_ms43_storage_snapshot
        if snapshot is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Storage Backup", f"{snapshot['family'].lower()}_{snapshot['kind']}.json",
            "Storage Snapshot (*.json);;Raw Binary (*.bin)")
        if path:
            try:
                if Path(path).suffix.lower() == ".bin":
                    Path(path).write_bytes(snapshot["data"])
                else:
                    Path(path).write_text(encode_snapshot(snapshot), encoding="utf-8")
            except (OSError, ValueError) as error:
                QMessageBox.warning(self, "Storage Backup Not Saved", str(error))

    def _ms42_ms43_view_storage(self):
        snapshot = self._ms42_ms43_storage_snapshot
        if snapshot is None:
            return
        source = self._ms42_ms43_storage_source
        dialog = _StorageBackupDialog(self, snapshot)
        if dialog.exec_() == QDialog.Accepted and dialog.edited_snapshot is not None:
            self._ms42_ms43_show_storage(dialog.edited_snapshot, source + " (edited)")

    def _ms42_ms43_restore_storage(self):
        from ms42_ms43_storage import restore_storage

        if not self._ms42_ms43_storage_write.isEnabled():
            return
        snapshot = dict(self._ms42_ms43_storage_snapshot)
        label = ("serial EEPROM" if snapshot["kind"] == "eeprom" else
                 "retained-data journal" if snapshot["family"] == "MS42" else "raw flash banks")
        if QMessageBox.question(
                self, "Restore Storage Backup",
                f"Restore {self._ms42_ms43_storage_source} to the {snapshot['family']} {label}?\n\n"
                "This replaces the selected storage bytes with the loaded backup. "
                "A complete before-image will be saved before writing; readback is required.\n\n"
                "Keep ignition ON, the engine STOPPED and the connection stable.",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return

        def task(log_fn, progress_fn):
            def save_before(before, journal_path):
                entry = self._ms42_ms43_catalog_storage(before, f"ECU {before['kind']} before restore")
                log_fn(f"Before-image saved in Bins: {entry.filename}; journal: {journal_path}")

            return restore_storage(self._ds2, snapshot, log_fn=log_fn, progress_cb=progress_fn,
                                   backup_cb=save_before)

        self._run_state_changing_task(task, on_success=self._ms42_ms43_storage_restored,
                                      on_failure=self._ms42_ms43_storage_failed)

    def _ms42_ms43_storage_restored(self, result):
        snapshot = result["snapshot"]
        source = "Verified restore"
        try:
            entry = self._ms42_ms43_catalog_storage(snapshot, f"ECU {snapshot['kind']} verified restore")
            source = entry.filename
            self._refresh_backup_table()
        except (OSError, ValueError, BackupIndexError) as error:
            self._log(f"Storage restore verified; Bins save failed: {error}. "
                      f"Recovery journal: {result.get('backup_journal', '')}", "warn")
        self._ms42_ms43_show_storage(snapshot, source)
        QMessageBox.information(self, "Storage Restored", "The loaded storage backup was written and read back successfully.")

    def _ms42_ms43_storage_failed(self, error):
        try:
            self._refresh_backup_table()
        except (OSError, ValueError, BackupIndexError) as refresh_error:
            self._log(f"Bins refresh failed: {refresh_error}", "warn")
        self._ms42_ms43_update_data()
        title = "Storage Verified — Reconnect Required" if getattr(error, "programming_completed", False) else "Storage Restore Stopped"
        QMessageBox.warning(self, title, str(error))
