"""Offline EEPROM presentation. Byte meaning and mutations belong to eeprom_ram."""

from pathlib import Path
import hashlib
import math
import re

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QBrush, QColor, QFont
from PyQt5.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog,
    QDialogButtonBox, QFileDialog, QGridLayout, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QMessageBox, QPushButton, QTableWidget,
    QTableWidgetItem, QTabWidget, QTextBrowser, QVBoxLayout, QWidget,
)

from engines.softbsl import eeprom_ram


def _display(field):
    return f"{field['display']} {field['unit']}".strip()


def comparison_rows(before, after, variant=None):
    """Present semantic changes plus every changed physical byte, without decoding twice."""
    before = eeprom_ram.validate_image(before)
    after = eeprom_ram.validate_image(after)
    rows = []
    checks = set()
    if variant is not None:
        left = {row['id']: row for row in eeprom_ram.decoded_fields(before, variant)}
        for field in eeprom_ram.decoded_fields(after, variant):
            original = left[field['id']]
            # Packed fields can share a byte. Compare their decoded values, not
            # their entire storage byte, to avoid naming unchanged neighbours.
            if (field['value'], field['display']) != (original['value'], original['display']):
                rows.append((field['category'].replace('_', ' ').title(), field['label'],
                             _display(original), _display(field),
                             f"0x{field['offset']:03X}", field['confidence']))
        for record in eeprom_ram.fields_for_variant(variant):
            if record.checked:
                offset = record.offset + record.length - 2
                checks.update((offset, offset + 1))
                if before[offset:offset + 2] != after[offset:offset + 2]:
                    rows.append(('Record check', record.label,
                                 before[offset:offset + 2].hex(' ').upper(),
                                 after[offset:offset + 2].hex(' ').upper(), f"0x{offset:03X}", 'Stored bytes'))
    for offset in eeprom_ram.changed_offsets(before, after):
        if offset not in checks:
            rows.append(('Raw byte', f"Byte 0x{offset:03X}", f"{before[offset]:02X}",
                         f"{after[offset]:02X}", f"0x{offset:03X}", 'Exact bytes'))
    return rows


def load_knock_axes(image, variant, definition_paths):
    """Read exact definition-backed axes using the existing desktop definition parser."""
    from ms41 import MS41ECU
    from romraider_defs import load_definitions, read_scalar

    if len(image) not in (MS41ECU.TUNE_SIZE, MS41ECU.FULL_ROM_SIZE):
        raise ValueError('Choose a 24 KiB partial or 256 KiB full BIN.')
    family = MS41ECU.detect_variant(image) or MS41ECU.detect_program_variant(image)
    if family is not None and family != variant:
        raise ValueError(f'The BIN identifies {family}; the EEPROM layout is {variant}.')
    if len(image) == MS41ECU.FULL_ROM_SIZE and family is None:
        raise ValueError('The full BIN family could not be verified.')
    tune = MS41ECU.tune_from_full(image) if len(image) == MS41ECU.FULL_ROM_SIZE else image
    for path in definition_paths:
        definitions = load_definitions(path)
        matched = definitions.match(tune)
        if matched is not None:
            break
    else:
        raise ValueError('The BIN needs an exact active calibration definition match.')
    declared = re.search(r'MS41\.[0-3]', matched.submodel)
    if declared and declared.group() != variant:
        raise ValueError(f'The exact definition identifies {declared.group()}, not {variant}.')
    if family is None and (not declared or declared.group() != variant):
        raise ValueError('The partial BIN family is unresolved by both its bytes and exact definition.')
    tables = definitions.resolve(matched)
    result = {}
    for key, title, count, unit, maximum in (
        ('rpm', 'Knock Tables Y Axis (Engine Speed)', 16, 'rpm', 8000),
        ('load', 'Knock Tables X Axis (Load)', 4, 'mg/stroke', 1389),
    ):
        attrs = tables.get(title)
        if attrs is None or int(attrs.get('sizex', '1')) * int(attrs.get('sizey', '1')) != count:
            raise ValueError(f'The exact definition must expose {count} values for {title}.')
        storage = attrs.get('storagetype', 'uint8')
        if storage not in ('uint8', 'uint16', 'int16'):
            raise ValueError(f'Unsupported axis storage: {storage}.')
        width = 1 if storage == 'uint8' else 2
        start = int(attrs['storageaddress'], 16)
        if start < 0 or start + width * count > len(tune):
            raise ValueError(f'{title} lies outside the calibration image.')
        values = []
        for index in range(count):
            value, units, _ = read_scalar(tune, dict(attrs, storageaddress=f'{start + width * index:X}'))
            if value is None or unit not in units.lower() or not math.isfinite(value) or not 0 <= value <= maximum:
                raise ValueError(f'{title} needs finite, plausible {unit} values.')
            values.append(value)
        if any(a >= b for a, b in zip(values, values[1:])):
            raise ValueError(f'{title} must be strictly increasing.')
        if key == 'rpm' and any(abs(value - round(value)) > 0.000001 for value in values):
            raise ValueError('Engine-speed breakpoints must be integral RPM.')
        result[key] = values
    result.update(xmlid=matched.xmlid, sha256=hashlib.sha256(image).hexdigest(),
                  warning='' if family else 'Family resolved from the exact definition; not independently identified from BIN bytes.')
    return result


def _table(headers):
    table = QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.verticalHeader().hide()
    table.setEditTriggers(QAbstractItemView.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectRows)
    table.setSelectionMode(QAbstractItemView.SingleSelection)
    table.setWordWrap(False)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
    table.horizontalHeader().setStretchLastSection(True)
    return table


def _fill(table, rows, mono_columns=()):
    table.setRowCount(len(rows))
    for row, values in enumerate(rows):
        for column, value in enumerate(values):
            item = QTableWidgetItem(str(value))
            item.setToolTip(str(value))
            if column in mono_columns:
                item.setFont(QFont('Courier New', 10))
            table.setItem(row, column, item)


class EepromComparisonDialog(QDialog):
    def __init__(self, parent, before, after, variant=None, *, title='EEPROM comparison',
                 before_label='Original', after_label='Draft', warning='', apply=False):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
        self.resize(940, 620)
        layout = QVBoxLayout(self)
        note = QLabel(f'{before_label} → {after_label}\n'
                      f'{len(eeprom_ram.changed_offsets(before, after))} changed bytes. '
                      + (f'Decoded layout: {variant}.' if variant else 'Raw comparison only; the layouts are not proven compatible.'))
        note.setWordWrap(True)
        layout.addWidget(note)
        hashes = QLabel(f'A SHA-256: {hashlib.sha256(before).hexdigest()}\n'
                        f'B SHA-256: {hashlib.sha256(after).hexdigest()}')
        hashes.setTextInteractionFlags(Qt.TextSelectableByMouse)
        hashes.setWordWrap(True)
        layout.addWidget(hashes)
        if warning:
            warning_label = QLabel(warning)
            warning_label.setWordWrap(True)
            warning_label.setStyleSheet('color:#e8c46a;')
            layout.addWidget(warning_label)
        self.table = _table(('Category', 'Field', 'Before', 'After', 'Offset', 'Evidence'))
        _fill(self.table, comparison_rows(before, after, variant), (2, 3, 4))
        self.table.setColumnWidth(0, 130)
        self.table.setColumnWidth(1, 270)
        self.table.setColumnWidth(2, 130)
        self.table.setColumnWidth(3, 130)
        layout.addWidget(self.table)
        buttons = QDialogButtonBox(QDialogButtonBox.Cancel | QDialogButtonBox.Apply if apply else QDialogButtonBox.Close)
        if apply:
            buttons.button(QDialogButtonBox.Apply).setText('Apply to loaded image')
            buttons.button(QDialogButtonBox.Apply).clicked.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class EepromEditorDialog(QDialog):
    """One reversible draft shared by decoded controls, raw bytes and record checks."""

    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self._baseline = self._working = None
        self._variant = None
        self._updating = False
        self._undo = []
        self._redo = []
        self._decoded = []
        self._inspection = None
        self._selected_id = None
        self._rom_context = None
        self.setModal(True)
        self.setWindowTitle('EEPROM Manager')
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
        self.setFont(QFont('Segoe UI', 10))
        area = QApplication.primaryScreen().availableGeometry()
        self.resize(min(1120, area.width() - 60), min(800, area.height() - 60))
        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        self.layout_label = QLabel()
        top.addWidget(self.layout_label)
        top.addStretch()
        self.undo_button = QPushButton('Undo')
        self.undo_button.setShortcut('Ctrl+Z')
        self.undo_button.clicked.connect(self._undo_edit)
        self.redo_button = QPushButton('Redo')
        self.redo_button.setShortcut('Ctrl+Y')
        self.redo_button.clicked.connect(self._redo_edit)
        self.compare_button = QPushButton('Compare file…')
        self.compare_button.clicked.connect(self._compare_file)
        self.info_button = QPushButton('Image information…')
        self.info_button.clicked.connect(self._image_information)
        for button in (self.undo_button, self.redo_button, self.compare_button, self.info_button):
            top.addWidget(button)
        layout.addLayout(top)
        self.source = QLabel()
        self.source.setWordWrap(True)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.summary.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.source)
        layout.addWidget(self.summary)

        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        decoded_page = QWidget()
        decoded_layout = QVBoxLayout(decoded_page)
        filters = QGridLayout()
        self.category = QComboBox()
        self.category.currentIndexChanged.connect(self._filter_fields)
        self.search = QLineEdit()
        self.search.setPlaceholderText('Search names, values, offsets or descriptions')
        self.search.textChanged.connect(self._filter_fields)
        self.edited_only = QCheckBox('Edited only')
        self.invalid_only = QCheckBox('Invalid record only')
        self.advanced = QCheckBox('Allow advanced edits')
        self.advanced.toggled.connect(self._toggle_advanced)
        for check in (self.edited_only, self.invalid_only):
            check.toggled.connect(self._filter_fields)
        filters.addWidget(self.category, 0, 0)
        filters.addWidget(self.search, 0, 1, 1, 3)
        filters.addWidget(self.edited_only, 1, 0)
        filters.addWidget(self.invalid_only, 1, 1)
        filters.addWidget(self.advanced, 1, 2, 1, 2)
        decoded_layout.addLayout(filters)
        self.decoded_table = _table(('Field', 'Stored value', 'Check', 'Offset', 'Access'))
        self.decoded_table.setColumnWidth(0, 340)
        self.decoded_table.setColumnWidth(1, 180)
        self.decoded_table.setColumnWidth(2, 85)
        self.decoded_table.setColumnWidth(3, 80)
        self.decoded_table.itemSelectionChanged.connect(self._select_field)
        decoded_layout.addWidget(self.decoded_table)
        self.tabs.addTab(decoded_page, 'Decoded')

        knock_page = QWidget()
        knock_layout = QVBoxLayout(knock_page)
        self.axes_note = QLabel('Rows 1–16 / columns 1–4. Attach the matching BIN to display exact definition-backed axes.')
        self.axes_note.setWordWrap(True)
        knock_layout.addWidget(self.axes_note)
        self.knock_table = QTableWidget(16, 4)
        self.knock_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.knock_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.knock_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.knock_table.itemSelectionChanged.connect(self._select_knock)
        knock_layout.addWidget(self.knock_table)
        axis_buttons = QHBoxLayout()
        attach = QPushButton('Attach matching BIN…')
        attach.clicked.connect(self._attach_rom)
        clear = QPushButton('Use row / column indices')
        clear.clicked.connect(self._clear_rom)
        axis_buttons.addWidget(attach)
        axis_buttons.addWidget(clear)
        axis_buttons.addStretch()
        knock_layout.addLayout(axis_buttons)
        self.tabs.addTab(knock_page, 'Learned knock')

        raw_page = QWidget()
        raw_layout = QVBoxLayout(raw_page)
        self.expert = QCheckBox('Enable raw byte editing')
        self.expert.toggled.connect(self._set_editable)
        raw_layout.addWidget(self.expert)
        self.hex_table = _table(['Offset'] + [f'{index:X}' for index in range(16)] + ['ASCII'])
        self.hex_table.setSelectionBehavior(QAbstractItemView.SelectItems)
        self.hex_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.hex_table.setRowCount(32)
        self.hex_table.setColumnWidth(0, 70)
        for column in range(1, 17):
            self.hex_table.setColumnWidth(column, 43)
        self.hex_table.itemChanged.connect(self._byte_changed)
        raw_layout.addWidget(self.hex_table)
        self.tabs.addTab(raw_page, 'Hex')

        records_page = QWidget()
        records_layout = QVBoxLayout(records_page)
        self.fields = _table(('Offset', 'Length', 'Check', 'Category', 'Meaning', 'Raw bytes'))
        self.fields.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.fields.setColumnWidth(0, 80)
        self.fields.setColumnWidth(1, 65)
        self.fields.setColumnWidth(2, 80)
        self.fields.setColumnWidth(3, 130)
        self.fields.setColumnWidth(4, 330)
        records_layout.addWidget(self.fields)
        record_buttons = QHBoxLayout()
        self.update_checks_button = QPushButton('Update Checks for Edited Records')
        self.update_checks_button.setToolTip('Update checks only for changed payloads. Unrelated invalid records stay untouched.')
        self.update_checks_button.clicked.connect(self._update_changed_checks)
        self.repair_checks_button = QPushButton('Repair selected invalid checks…')
        self.repair_checks_button.clicked.connect(self._repair_selected_checks)
        self.fields.itemSelectionChanged.connect(self._update_repair_button)
        self.fields.itemSelectionChanged.connect(self._show_record)
        record_buttons.addWidget(self.update_checks_button)
        record_buttons.addWidget(self.repair_checks_button)
        record_buttons.addStretch()
        records_layout.addLayout(record_buttons)
        self.tabs.addTab(records_page, 'Record checks')

        self.field_details = QTextBrowser()
        self.field_details.setOpenExternalLinks(False)
        self.field_details.setMinimumHeight(65)
        self.field_details.setMaximumHeight(105)
        layout.addWidget(self.field_details)
        self.edit_controls = QWidget()
        edit_row = QHBoxLayout(self.edit_controls)
        edit_row.setContentsMargins(0, 0, 0, 0)
        self.value_label = QLabel('Select a decoded field')
        self.value_label.setMinimumWidth(160)
        self.value_editor = QLineEdit()
        self.value_editor.setFont(QFont('Courier New', 10))
        self.value_editor.returnPressed.connect(self._apply_field)
        self.choice_editor = QComboBox()
        self.edit_button = QPushButton('Apply field edit')
        self.edit_button.clicked.connect(self._apply_field)
        edit_row.addWidget(self.value_label)
        edit_row.addWidget(self.value_editor, 1)
        edit_row.addWidget(self.choice_editor, 1)
        edit_row.addWidget(self.edit_button)
        layout.addWidget(self.edit_controls)
        self.tabs.currentChanged.connect(self._tab_changed)

        shortcut = QHBoxLayout()
        shortcut.addWidget(QLabel('Transmission:'))
        self.transmission = QComboBox()
        self.transmission.addItem('Automatic transmission', 'at')
        self.transmission.addItem('Manual transmission', 'mt')
        shortcut.addWidget(self.transmission)
        transmission_button = QPushButton('Apply')
        transmission_button.clicked.connect(self._apply_transmission)
        shortcut.addWidget(transmission_button)
        shortcut.addStretch()
        self.review_button = QPushButton('Review & apply…')
        self.review_button.clicked.connect(self._review_changes)
        close = QPushButton('Close')
        close.clicked.connect(self.reject)
        shortcut.addWidget(self.review_button)
        shortcut.addWidget(close)
        layout.addLayout(shortcut)
        self._update_history()

    def set_image(self, image, source, variant):
        image = eeprom_ram.validate_image(image)
        eeprom_ram.fields_for_variant(variant)
        self._baseline = self._working = image
        self._variant = variant
        self._undo.clear()
        self._redo.clear()
        self.advanced.blockSignals(True)
        self.advanced.setChecked(False)
        self.advanced.blockSignals(False)
        self._rom_context = None
        self._selected_id = None
        self.source.setText(f'Source: {source}')
        self.layout_label.setText(f'EEPROM layout: {variant}')
        self._render_image()

    def image(self):
        result = bytearray()
        for row in range(32):
            for column in range(1, 17):
                text = self.hex_table.item(row, column).text().strip()
                if len(text) != 2 or not re.fullmatch('[0-9a-fA-F]{2}', text):
                    raise ValueError(f'Invalid byte at 0x{row * 16 + column - 1:03X}.')
                result.append(int(text, 16))
        return bytes(result)

    def _render_image(self):
        self._updating = True
        try:
            rows = []
            for offset in range(0, 512, 16):
                part = self._working[offset:offset + 16]
                rows.append([f'0x{offset:03X}'] + [f'{value:02X}' for value in part]
                            + [''.join(chr(value) if 32 <= value < 127 else '.' for value in part)])
            _fill(self.hex_table, rows, range(18))
            for row in range(32):
                for column in (0, 17):
                    item = self.hex_table.item(row, column)
                    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
        finally:
            self._updating = False
        self._set_editable(self.expert.isChecked())
        self._refresh_details()

    def _set_editable(self, enabled):
        self.hex_table.setEditTriggers(
            QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed | QAbstractItemView.AnyKeyPressed
            if enabled else QAbstractItemView.NoEditTriggers)

    def _byte_changed(self, item):
        if self._updating or not 1 <= item.column() <= 16:
            return
        try:
            text = item.text().strip()
            if not re.fullmatch('[0-9a-fA-F]{1,2}', text):
                raise ValueError('Enter one hexadecimal byte (00–FF).')
            self._updating = True
            item.setText(f'{int(text, 16):02X}')
            self._updating = False
            target = self.image()
        except ValueError as error:
            self._updating = False
            item.setBackground(QColor('#7a2f2f'))
            self.summary.setText(str(error))
            self.review_button.setEnabled(False)
            self.undo_button.setEnabled(True)
            self.redo_button.setEnabled(False)
            return
        self._set_edited_image(target)

    def _set_edited_image(self, image):
        image = eeprom_ram.validate_image(image)
        if image != self._working:
            self._undo = (self._undo + [self._working])[-64:]
            self._redo.clear()
            self._working = image
        self._render_image()

    def _undo_edit(self):
        try:
            self.image()
        except ValueError:
            self._render_image()
            return
        if self._undo:
            self._redo.append(self._working)
            self._working = self._undo.pop()
            self._render_image()

    def _redo_edit(self):
        if self._redo:
            self._undo.append(self._working)
            self._working = self._redo.pop()
            self._render_image()

    def _update_history(self):
        self.undo_button.setEnabled(bool(self._undo))
        self.redo_button.setEnabled(bool(self._redo))
        self.review_button.setEnabled(self._working is not None and self._working != self._baseline)

    def _refresh_details(self):
        if self._baseline is None:
            return
        try:
            image = self.image()
            self._inspection = inspection = eeprom_ram.inspect_image(image, self._variant)
        except (ValueError, eeprom_ram.EepromError) as error:
            self.summary.setText(f'Invalid edited image: {error}')
            return
        changed = set(eeprom_ram.changed_offsets(self._baseline, image))
        self._updating = True
        for row in range(32):
            for column in range(1, 17):
                self.hex_table.item(row, column).setBackground(
                    QBrush(QColor('#4a3a00')) if row * 16 + column - 1 in changed else QBrush())
        rows = inspection['fields']
        checked = sum(row['checked'] for row in rows)
        valid = sum(row.get('check_ok') is True for row in rows if row['checked'])
        self.summary.setText(f"SHA-256: {inspection['sha256']}\nChecked records: {valid}/{checked} valid"
                             f"  |  Changed bytes: {len(changed)}  |  {len(inspection['warnings'])} warnings")
        self.summary.setToolTip('\n'.join(inspection['warnings']))
        _fill(self.fields, [(f"0x{row['offset']:03X}", row['length'],
                            'Valid' if row.get('check_ok') else 'Invalid' if row['checked'] else 'None',
                            row['category'].replace('_', ' ').title(), row['label'], row['raw'])
                           for row in rows], (0, 1, 5))
        for index, row in enumerate(rows):
            if row.get('check_ok') is False:
                self.fields.item(index, 2).setForeground(QColor('#f47171'))
        mode = {'automatic': 0, 'manual': 1}.get(inspection['decoded']['transmission']['mode'])
        if mode is not None:
            self.transmission.setCurrentIndex(mode)
        self._decoded = inspection['decoded_fields']
        category = self.category.currentData()
        self.category.clear()
        self.category.addItem('All categories', None)
        for key in dict.fromkeys(field['category'] for field in self._decoded):
            self.category.addItem(key.replace('_', ' ').title(), key)
        self.category.setCurrentIndex(max(0, self.category.findData(category)))
        self._updating = False
        self._filter_fields()
        self._render_knock()
        self._update_repair_button()
        self._update_history()

    def _field_changed(self, field):
        baseline = next(row for row in self._baseline_fields if row['id'] == field['id'])
        return (field['value'], field['display']) != (baseline['value'], baseline['display'])

    def _filter_fields(self, *_):
        if self._updating or not self._decoded:
            return
        self._baseline_fields = eeprom_ram.decoded_fields(self._baseline, self._variant)
        query = self.search.text().strip().casefold()
        category = self.category.currentData()
        self._visible = [field for field in self._decoded
                         if (category is None or field['category'] == category)
                         and (not self.edited_only.isChecked() or self._field_changed(field))
                         and (not self.invalid_only.isChecked() or field['check_ok'] is False)
                         and (not query or query in ' '.join((field['label'], field['description'], _display(field),
                                                              field['raw'], f"0x{field['offset']:03X}")).casefold())]
        self.decoded_table.blockSignals(True)
        _fill(self.decoded_table, [(field['label'], _display(field),
                                   'Invalid' if field['check_ok'] is False else 'Valid' if field['check_ok'] else 'None',
                                   f"0x{field['offset']:03X}",
                                   'Read-only' if not field['editable'] else 'Advanced' if field['requires_advanced'] else 'Editable')
                                  for field in self._visible], (1, 3))
        for index, field in enumerate(self._visible):
            if self._field_changed(field):
                self.decoded_table.item(index, 1).setBackground(QColor('#4a3a00'))
            if field['check_ok'] is False:
                self.decoded_table.item(index, 2).setForeground(QColor('#f47171'))
        selected = next((index for index, field in enumerate(self._visible) if field['id'] == self._selected_id), None)
        if selected is not None:
            self.decoded_table.selectRow(selected)
        self.decoded_table.blockSignals(False)
        self._show_field()

    def _select_field(self):
        row = self.decoded_table.currentRow()
        if 0 <= row < len(self._visible):
            self._selected_id = self._visible[row]['id']
        self._show_field()

    def _show_field(self):
        field = next((field for field in self._decoded if field['id'] == self._selected_id), None)
        self._selected_field = field
        editable = bool(field and field['editable'] and (not field['requires_advanced'] or self.advanced.isChecked()))
        self.edit_button.setEnabled(editable)
        self.value_editor.setEnabled(editable)
        self.choice_editor.setEnabled(editable)
        choice = bool(field and field['options'])
        self.value_editor.setVisible(not choice or field['kind'] == 'number')
        self.choice_editor.setVisible(choice)
        self.choice_editor.clear()
        self.value_editor.clear()
        if field is None:
            self.value_label.setText('Select a decoded field')
            self.field_details.setPlainText('\n'.join(self._inspection['warnings']) if self._inspection else '')
            return
        self.value_label.setText(field['label'])
        self.value_label.setMaximumWidth(300)
        self.value_label.setWordWrap(True)
        self.value_editor.setMaxLength(field['length'] if field['kind'] == 'ascii' else 80)
        self.value_editor.setText(field['display'])
        if choice:
            if field['kind'] == 'number':
                self.choice_editor.addItem('Use numeric value', None)
            for option in field['options']:
                self.choice_editor.addItem(option['label'], option['value'])
            selected = self.choice_editor.findData(field['value'])
            self.choice_editor.setCurrentIndex(selected if selected >= 0 else (0 if field['kind'] == 'number' else -1))
        detail = [field['description'],
                  f"Physical: 0x{field['offset']:03X} · {field['length']} bytes · {field['raw']}",
                  f"Evidence: {field['confidence']} · {'Advanced edit' if field['requires_advanced'] else 'Named edit' if field['editable'] else 'Read-only'}"]
        if field['minimum'] is not None:
            detail.append(f"Range: {field['minimum']:g} to {field['maximum']:g} {field['unit']} · step {field['step']:g}")
        if field['check_ok'] is False:
            detail.append('Invalid stored record: the ECU may use defaults. A changed field updates only its record check.')
        if field['requires_advanced'] and not self.advanced.isChecked():
            detail.append('Enable Allow advanced edits to edit this understood field.')
        self.field_details.setPlainText('\n'.join(detail))

    def _toggle_advanced(self, enabled):
        if enabled and QMessageBox.warning(
                self, 'Allow Advanced EEPROM Edits?',
                'Unlock understood learned values, counters, program references and boot state. '
                'Incorrect values can affect ECU operation. This changes only the draft file.',
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            self.advanced.blockSignals(True)
            self.advanced.setChecked(False)
            self.advanced.blockSignals(False)
        self._show_field()

    def _apply_field(self):
        if not self._selected_field or not self.edit_button.isEnabled():
            return
        field = self._selected_field
        value = self.choice_editor.currentData() if field['options'] else None
        if value is None:
            value = self.value_editor.text()
        try:
            image = eeprom_ram.set_decoded_field(self.image(), self._variant, field['id'], value,
                                               allow_advanced=self.advanced.isChecked())
            self._set_edited_image(image)
        except (ValueError, eeprom_ram.EepromError) as error:
            QMessageBox.critical(self, 'EEPROM Edit Rejected', str(error))

    def _apply_transmission(self):
        try:
            self._set_edited_image(eeprom_ram.set_transmission_mode(self.image(), self.transmission.currentData(), self._variant))
        except (ValueError, eeprom_ram.EepromError) as error:
            QMessageBox.critical(self, 'Transmission Edit Failed', str(error))

    def _update_changed_checks(self):
        try:
            self._set_edited_image(eeprom_ram.update_checks_for_changed_records(self._baseline, self.image(), self._variant))
        except (ValueError, eeprom_ram.EepromError) as error:
            QMessageBox.critical(self, 'Check Update Failed', str(error))

    def _selected_invalid_records(self):
        if self._inspection is None:
            return []
        return [self._inspection['fields'][row.row()] for row in self.fields.selectionModel().selectedRows()
                if self._inspection['fields'][row.row()].get('check_ok') is False]

    def _update_repair_button(self):
        self.repair_checks_button.setEnabled(bool(self._selected_invalid_records()))

    def _show_record(self):
        if self._updating or self._inspection is None or self.tabs.currentIndex() != 3:
            return
        row = self.fields.currentRow()
        if 0 <= row < len(self._inspection['fields']):
            record = self._inspection['fields'][row]
            detail = f"{record['label']}\n0x{record['offset']:03X} · {record['length']} bytes\n{record['raw']}"
            if record['checked']:
                detail += (f"\nStored check: 0x{record['stored_check']:04X} · "
                           f"Expected check: 0x{record['computed_check']:04X}\n"
                           'Repair changes only selected invalid check words; it does not validate stored payloads.')
            self.field_details.setPlainText(detail)

    def _repair_selected_checks(self):
        records = self._selected_invalid_records()
        if not records:
            return
        names = '\n'.join(f"0x{row['offset']:03X} — {row['label']}" for row in records)
        if QMessageBox.warning(self, 'Repair Selected Record Checks?',
                               f'{names}\n\nOnly these check words change. Repair does not validate their payloads; '
                               'it can make stale data active in the ECU. Undo restores the previous draft.',
                               QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        try:
            self._set_edited_image(eeprom_ram.repair_record_checks(self.image(), self._variant, [row['offset'] for row in records]))
        except (ValueError, eeprom_ram.EepromError) as error:
            QMessageBox.critical(self, 'Check Repair Rejected', str(error))

    def _render_knock(self):
        fields = {field['id']: field for field in self._decoded}
        self.knock_table.blockSignals(True)
        for index in range(64):
            field = fields[f'knock_cell_{index}']
            item = QTableWidgetItem(_display(field))
            item.setFont(QFont('Courier New', 10))
            item.setToolTip(field['description'])
            self.knock_table.setItem(index // 4, index % 4, item)
        context = self._rom_context
        self.knock_table.setHorizontalHeaderLabels(
            [f'{value:g} mg/stroke' for value in context['load']] if context else [f'C{index + 1}' for index in range(4)])
        self.knock_table.setVerticalHeaderLabels(
            [f'{value:g} RPM' for value in context['rpm']] if context else [f'R{index + 1}' for index in range(16)])
        self.knock_table.blockSignals(False)
        self.axes_note.setText(
            f"Exact ROM axes: {context['filename']} · XMLID {context['xmlid']}\nSHA-256: {context['sha256']}\n{context['warning']}"
            if context else 'Rows 1–16 / columns 1–4. Attach the matching BIN to display exact definition-backed axes.')

    def _select_knock(self):
        row, column = self.knock_table.currentRow(), self.knock_table.currentColumn()
        if row >= 0 and column >= 0:
            self._selected_id = f'knock_cell_{row * 4 + column}'
            self._show_field()

    def _tab_changed(self, index):
        self.edit_controls.setVisible(index in (0, 1))
        if index in (0, 1):
            self._show_field()
        else:
            self._selected_id = None
            self._show_field()

    def _attach_rom(self):
        from definition_registry import DefinitionRegistry
        path, _ = QFileDialog.getOpenFileName(self, 'Attach the BIN belonging to this EEPROM', '', 'BIN images (*.bin);;All files (*)')
        if not path:
            return
        try:
            with open(path, 'rb') as stream:
                image = stream.read(256 * 1024 + 1)
            context = load_knock_axes(image, self._variant, DefinitionRegistry().active_paths())
        except (OSError, ValueError, RuntimeError) as error:
            QMessageBox.warning(self, 'ROM Context Unavailable', str(error))
            return
        self._rom_context = dict(context, filename=Path(path).name)
        self._render_knock()

    def _clear_rom(self):
        self._rom_context = None
        self._render_knock()

    def _compare_file(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Compare EEPROM file with draft', '', 'EEPROM images (*.bin);;All files (*)')
        if not path:
            return
        try:
            with open(path, 'rb') as stream:
                before = eeprom_ram.validate_image(stream.read(513))
            after = self.image()
            detected = eeprom_ram.detect_layouts(before)
            compatible = self._variant in detected or (self._variant in ('MS41.2', 'MS41.3') and any(v in detected for v in ('MS41.2', 'MS41.3')))
            warning = '\n'.join(eeprom_ram.inspect_image(before, self._variant)['warnings']) if compatible else 'The comparison file has an unknown or different layout. Named fields are unavailable; no layout is inferred from its size.'
            dialog = EepromComparisonDialog(self, before, after, self._variant if compatible else None,
                                            before_label=Path(path).name, after_label='Current draft', warning=warning)
            dialog.exec_()
        except (OSError, ValueError, eeprom_ram.EepromError) as error:
            QMessageBox.critical(self, 'EEPROM Comparison Failed', str(error))

    def _image_information(self):
        if self._inspection:
            QMessageBox.information(self, 'EEPROM Image Information',
                                    self.summary.text() + '\n\n' + '\n\n'.join(self._inspection['warnings'])
                                    + '\n\nDecoded values describe stored EEPROM bytes. They are not live ECU readings. '
                                    'Edits change only this draft; save and hardware writes are separate actions.')

    def _review_changes(self):
        try:
            image = self.image()
            eeprom_ram.build_write_plan(self._baseline, image, self._variant)
        except (ValueError, eeprom_ram.EepromError) as error:
            QMessageBox.critical(self, 'Invalid EEPROM Edit', str(error))
            return
        review = EepromComparisonDialog(self, self._baseline, image, self._variant,
                                        title='Review EEPROM Edits', apply=True,
                                        warning='\n'.join(self._inspection['warnings'])
                                        + '\nApply updates the loaded image only. Save Copy and ECU writing remain separate actions.')
        if review.exec_() == QDialog.Accepted:
            self._apply_to_owner()

    def _apply_to_owner(self):
        try:
            image = self.image()
            eeprom_ram.build_write_plan(self._baseline, image, self._variant)
        except (ValueError, eeprom_ram.EepromError) as error:
            QMessageBox.critical(self, 'Invalid EEPROM Edit', str(error))
            return
        modified = self.owner._eeprom_modified or image != self._baseline
        self.owner._show_eeprom_image(image, (self.source.text()[8:] if self.source.text().startswith('Source: ') else self.source.text()),
                                     variant=self._variant, modified=modified)
        self._baseline = image
        self.accept()

    def reject(self):
        invalid = False
        try:
            dirty = self._baseline is not None and self.image() != self._baseline
        except ValueError:
            dirty = invalid = True
        if dirty:
            buttons = QMessageBox.Discard | QMessageBox.Cancel
            if not invalid:
                buttons |= QMessageBox.Save
            choice = QMessageBox.question(self, 'Unapplied EEPROM Edits',
                                          'Review and apply the draft to the loaded image, discard it, or keep editing.',
                                          buttons, QMessageBox.Cancel)
            if choice == QMessageBox.Save:
                self._review_changes()
                return
            if choice != QMessageBox.Discard:
                return
        super().reject()
