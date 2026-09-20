"""Offline desktop parity checks: exact drafts, shared edits and explicit review."""

import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest

pytest.importorskip('PyQt5')
from PyQt5.QtCore import QItemSelectionModel
from PyQt5.QtWidgets import QApplication, QDialog, QFileDialog, QMessageBox, QWidget

import eeprom_editor as ui
from engines.softbsl import eeprom_ram as eeprom
from ms41 import MS41ECU
from tests.test_eeprom_decoded import _image


@pytest.fixture
def editor():
    app = QApplication.instance() or QApplication([])

    class Owner(QWidget):
        _eeprom_modified = False

        def _show_eeprom_image(self, image, source, **kwargs):
            self.applied = (image, source, kwargs)

    owner = Owner()
    dialog = ui.EepromEditorDialog(owner)
    dialog.set_image(_image('MS41.3'), 'synthetic.bin', 'MS41.3')
    yield dialog
    dialog.accept()
    owner.deleteLater()
    app.processEvents()


def _select(editor, field_id):
    editor.category.setCurrentIndex(0)
    editor.search.clear()
    editor.edited_only.setChecked(False)
    row = next(index for index, field in enumerate(editor._visible) if field['id'] == field_id)
    editor.decoded_table.selectRow(row)


@pytest.mark.parametrize('variant', tuple(eeprom.FIELDS_BY_VARIANT))
def test_decoded_edit_shares_exact_draft_history_and_filters(editor, variant):
    before = _image(variant)
    editor.set_image(before, 'synthetic.bin', variant)
    _select(editor, 'idle_trim_1_ms')
    editor.value_editor.setText('0.2')
    editor.edit_button.click()
    expected = eeprom.set_decoded_field(before, variant, 'idle_trim_1_ms', '0.2')
    assert editor.image() == expected
    assert editor._baseline == before
    editor.edit_button.click()  # Display rounding is an exact no-op.
    assert len(editor._undo) == 1
    editor.edited_only.setChecked(True)
    assert [field['id'] for field in editor._visible] == ['idle_trim_1_ms']
    editor.undo_button.click()
    assert editor.image() == before
    editor.redo_button.click()
    assert editor.image() == expected
    assert editor.font().family() == 'Segoe UI'
    assert editor.font().pointSize() == 10


def test_advanced_choice_ascii_validation_and_reset(editor, monkeypatch):
    monkeypatch.setattr(QMessageBox, 'warning', lambda *_: QMessageBox.No)
    _select(editor, 'operating_time_hours')
    assert not editor.edit_button.isEnabled()
    editor.advanced.setChecked(True)
    assert not editor.advanced.isChecked()
    monkeypatch.setattr(QMessageBox, 'warning', lambda *_: QMessageBox.Yes)
    editor.advanced.setChecked(True)
    assert editor.edit_button.isEnabled()
    editor.value_editor.setText('1.1')
    editor.edit_button.click()
    assert editor.image() == eeprom.set_decoded_field(editor._baseline, 'MS41.3', 'operating_time_hours', '1.1', allow_advanced=True)
    before = editor.image()
    errors = []
    monkeypatch.setattr(QMessageBox, 'critical', lambda *args: errors.append(args[2]))
    editor.value_editor.setText('nan')
    editor.edit_button.click()
    assert editor.image() == before and errors
    ascii_field = next(field for field in editor._decoded if field['kind'] == 'ascii')
    _select(editor, ascii_field['id'])
    editor.value_editor.setText('SHORT')
    editor.edit_button.click()
    assert editor.image() == before and 'exactly' in errors[-1]
    _select(editor, 'transmission')
    editor.choice_editor.setCurrentIndex(editor.choice_editor.findData('at'))
    editor.edit_button.click()
    assert eeprom.transmission_record(editor.image(), 'MS41.3')['mode'] == 'automatic'
    editor.set_image(editor.image(), 'new.bin', 'MS41.3')
    assert not editor.advanced.isChecked() and not editor._undo


def test_raw_invalid_input_and_check_repair_are_reversible(editor, monkeypatch):
    before = _image('MS41.3')
    editor.hex_table.item(0, 1).setText('NO')
    assert not editor.review_button.isEnabled()
    editor.undo_button.click()
    assert editor.image() == before
    image = bytearray(before)
    records = [field for field in eeprom.fields_for_variant('MS41.3') if field.checked][:2]
    for record in records:
        image[record.offset + record.length - 1] ^= 1
    editor.set_image(image, 'invalid.bin', 'MS41.3')
    editor.tabs.setCurrentIndex(3)
    assert not editor.repair_checks_button.isEnabled()
    row = next(index for index, field in enumerate(editor._inspection['fields']) if field['offset'] == records[0].offset)
    editor.fields.selectionModel().select(editor.fields.model().index(row, 0), QItemSelectionModel.Select | QItemSelectionModel.Rows)
    assert editor.repair_checks_button.isEnabled()
    monkeypatch.setattr(QMessageBox, 'warning', lambda *_: QMessageBox.No)
    editor.repair_checks_button.click()
    assert editor.image() == image
    monkeypatch.setattr(QMessageBox, 'warning', lambda *_: QMessageBox.Yes)
    editor.repair_checks_button.click()
    assert editor.image() == eeprom.repair_record_checks(image, 'MS41.3', [records[0].offset])
    assert editor._inspection['fields'][next(index for index, field in enumerate(editor._inspection['fields']) if field['offset'] == records[1].offset)]['check_ok'] is False
    editor.undo_button.click()
    assert editor.image() == image


def test_review_cancel_does_not_apply_and_accept_updates_owner(editor, monkeypatch):
    editor.transmission.setCurrentIndex(0)
    editor._apply_transmission()
    expected = editor.image()
    shown = []
    monkeypatch.setattr(ui.EepromComparisonDialog, 'exec_', lambda self: shown.append(self) or QDialog.Rejected)
    editor.review_button.click()
    assert shown and not hasattr(editor.owner, 'applied')
    monkeypatch.setattr(ui.EepromComparisonDialog, 'exec_', lambda self: QDialog.Accepted)
    editor.review_button.click()
    assert editor.owner.applied[0] == expected
    assert editor.owner.applied[2] == {'variant': 'MS41.3', 'modified': True}


def test_named_comparison_preserves_packed_neighbour_and_raw_coverage():
    before = _image('MS41.1')
    after = eeprom.set_decoded_field(before, 'MS41.1', 'knock_cell_0', '-1.5')
    rows = ui.comparison_rows(before, after, 'MS41.1')
    assert any(row[1] == 'Knock correction R1 C1' for row in rows)
    assert not any(row[1] == 'Knock correction R1 C2' for row in rows)
    covered = {int(row[4], 16) for row in rows if row[0] in ('Record check', 'Raw byte')}
    covered |= {offset + 1 for offset in covered if any(row[0] == 'Record check' and int(row[4], 16) == offset for row in rows)}
    assert set(eeprom.changed_offsets(before, after)) <= covered
    assert all(row[0] == 'Raw byte' for row in ui.comparison_rows(before, after))


def test_unknown_comparison_does_not_inherit_current_layout(editor, monkeypatch, tmp_path):
    other = bytearray(_image('MS41.0'))
    other[0x1E3:0x1FD] = b'?' * 26
    path = tmp_path / 'unknown.bin'
    path.write_bytes(other)
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *_: (str(path), ''))
    shown = []
    monkeypatch.setattr(ui.EepromComparisonDialog, 'exec_', lambda self: shown.append(self) or QDialog.Rejected)
    editor.compare_button.click()
    assert shown
    assert all(shown[0].table.item(row, 0).text() == 'Raw byte' for row in range(shown[0].table.rowCount()))


@pytest.mark.parametrize('full', (False, True))
def test_exact_rom_context_uses_active_definition_and_framing(tmp_path, monkeypatch, full):
    path = tmp_path / 'axes.xml'
    path.write_text('''<roms><rom><romid><xmlid>AXES</xmlid><internalidaddress>E</internalidaddress>
      <internalidstring>AXES</internalidstring><filesize>24kb</filesize><submodel>MS41.3</submodel></romid>
      <table name="Knock Tables Y Axis (Engine Speed)" storageaddress="100" sizex="16" sizey="1" storagetype="uint16" endian="big">
        <scaling units="RPM" expression="x"/></table>
      <table name="Knock Tables X Axis (Load)" storageaddress="140" sizex="4" sizey="1" storagetype="uint16">
        <scaling units="mg/stroke" expression="x*0.5"/></table>
      </rom></roms>''', encoding='utf-8')
    tune = bytearray(24576)
    tune[0xE:0x12] = b'AXES'
    for index in range(16):
        tune[0x100 + index * 2:0x102 + index * 2] = (500 + index * 400).to_bytes(2, 'big')
    for index in range(4):
        tune[0x140 + index * 2:0x142 + index * 2] = (100 + index * 200).to_bytes(2, 'little')
    image = MS41ECU.tune_into_full(bytes(262144), tune) if full else tune
    monkeypatch.setattr(MS41ECU, 'detect_variant', staticmethod(lambda _: 'MS41.3'))
    result = ui.load_knock_axes(image, 'MS41.3', [path])
    assert result['rpm'] == list(range(500, 6501, 400))
    assert result['load'] == [50, 150, 250, 350]
    assert result['xmlid'] == 'AXES'
    with pytest.raises(ValueError, match='layout'):
        ui.load_knock_axes(image, 'MS41.1', [path])
    with pytest.raises(ValueError, match='exact active'):
        ui.load_knock_axes(image, 'MS41.3', [])
    broken = bytearray(image)
    offset = 0x100 + (MS41ECU.TUNE_OFFSET if full else 0)
    broken[offset + 2:offset + 4] = broken[offset:offset + 2]
    with pytest.raises(ValueError, match='increasing'):
        ui.load_knock_axes(broken, 'MS41.3', [path])
