"""Exercise import, configure, build, reopen and rollback through Windows Patches."""

from dataclasses import replace
from pathlib import Path

import pytest
from PyQt5.QtCore import QPoint, Qt
from PyQt5.QtGui import QFont
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QDialog, QFileDialog, QInputDialog, QLabel, QMessageBox, QPushButton

import ms42_ms43_image as images
from external_patch_library import ExternalPatchLibrary
from gui_xdf_patches import FieldDialog
from tests.test_ms42_ms43_desktop import connect, window, worker_runner  # noqa: F401
from tests.test_ms42_ms43_image import synthetic_image


def test_table_editor_preserves_read_only_rows(window):
    field = dict(id="table", title="Table", kind="table", rows=2, columns=2,
                 values=[[1, 2], [3, 4]], read_only_rows=[1],
                 ownership_note="This setting is saved independently.")
    dialog = FieldDialog([field], {}, window)
    assert any(field["ownership_note"] in label.text() for label in dialog.findChildren(QLabel))
    grid = dialog.grids["table"]
    for row in range(2):
        for col in range(2):
            assert bool(grid.item(row, col).flags() & Qt.ItemIsEditable) == (row == 0)

    field["axes"] = {
        "x": dict(values=[10, 20], read_only=False),
        "y": dict(values=[30, 40], read_only=True),
    }
    dialog = FieldDialog([field], {}, window)
    grid = dialog.grids["table"]
    assert not grid.item(1, 0).flags() & Qt.ItemIsEditable
    assert not grid.item(2, 1).flags() & Qt.ItemIsEditable
    grid.item(0, 2).setText("25")
    grid.item(1, 1).setText("7")
    assert dialog.edits == {("table", "x", 0, 1): "25", ("table", "z", 0, 0): "7"}


def test_compatibility_notes_keep_patch_actions_available(window, tmp_path, monkeypatch):
    import ms42_ms43_xdf_patches as patches
    from tests.test_ms42_ms43_xdf_patches import fixture

    panel = window._external_patches
    panel.library = ExternalPatchLibrary(tmp_path / "catalog")
    source = images.correct_checksums(synthetic_image("MS42"), "MS42")[0]
    address = 0x48111
    path = fixture(tmp_path, [("Updated patch", [(address, source[address:address+1], b"\x42")])])
    digest = panel.library.import_file(path)
    catalog = replace(panel.library.catalog(digest), notices=("Some patch relationships need review.",))
    monkeypatch.setattr(panel.library, "catalog", lambda _: catalog)
    original_inspect = patches.inspect

    def with_note(*args, **kwargs):
        rows = original_inspect(*args, **kwargs)
        rows[0]["compatibility_note"] = "Dependencies for this entry are unknown."
        return rows

    monkeypatch.setattr(patches, "inspect", with_note)
    window._set_patch_base(source, "source.bin")
    details = "\n".join(label.text() for label in panel.details["0x1"].findChildren(QLabel))
    assert catalog.notices[0] in details
    assert panel.rows[0]["compatibility_note"] in details
    assert panel.rows[0]["compatibility_note"] in panel.checkboxes["0x1"].toolTip()
    action = panel.checkboxes["0x1"]
    assert action.isEnabled()
    action.click()
    assert panel.actions["0x1"] == "install"
    assert window.btn_patches_build.isEnabled()


def test_desktop_external_patch_roundtrip_from_bins(window, tmp_path, monkeypatch):
    panel = window._external_patches
    panel.library = ExternalPatchLibrary(tmp_path / "catalog")
    source = images.correct_checksums(synthetic_image("MS43"), "MS43")[0]
    base = tmp_path / "original.bin"
    base.write_bytes(source)
    setting = 0x70123
    xdf = tmp_path / "cluster.xdf"
    xdf.write_text(f"""<XDFFORMAT version="1.70"><XDFHEADER>
      <deftitle>MS43 430069 cluster lighting</deftitle>
      <REGION type="0xffffffff" startaddress="0" size="0x80000"/>
      </XDFHEADER><XDFPATCH uniqueid="1"><title>Immobilizer Bypass</title>
      <XDFPATCHENTRY address="{setting}" datasize="1" basedata="{source[setting]:02x}" patchdata="42"/>
      </XDFPATCH><XDFPATCH uniqueid="2"><title>Immobilizer</title><description>Publisher notes.</description></XDFPATCH>
      <XDFPATCH uniqueid="3"><title>Unknown layout</title><NEWFORMAT/></XDFPATCH>
      <XDFCONSTANT><title>LED threshold</title>
      <EMBEDDEDDATA mmedaddress="{setting}" mmedelementsizebits="8"/>
      </XDFCONSTANT></XDFFORMAT>""", encoding="utf-8")
    messages = []
    monkeypatch.setattr(QMessageBox, "warning", lambda _, title, msg, *args: messages.append((title, msg)))
    monkeypatch.setattr(QInputDialog, "getText", lambda *args, text: (text, True))
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_: (str(xdf), ""))
    window.btn_import_patch_list.click()
    assert window._load_patch_base_path(base)
    assert window.patches_splitter.widget(0) is panel
    assert panel.checkboxes["0x1"].text() == "Immobilizer Bypass"
    assert not any(panel.actions.values())
    for row, state in ((1, "Unsupported"), (2, "Unsupported")):
        key = panel.rows[row]["id"]
        assert panel.statuses[key].text() == state
        assert not panel.checkboxes[key].isEnabled()
        assert not panel.remove_buttons[key].isEnabled()
        reason = panel.rows[row]["reason"]
        assert reason and reason in panel.statuses[key].toolTip()
        assert any(reason in label.text() for label in panel.details[key].findChildren(QLabel))
    assert not window.btn_patches_build.isEnabled()
    panel.checkboxes["0x1"].click()
    assert window.btn_patches_build.isEnabled()

    def configure(dialog):
        editor = next(iter(dialog.editors.values()))
        assert editor.text() in ("66", "66.0")
        editor.setText("56")
        return QDialog.Accepted

    monkeypatch.setattr(FieldDialog, "exec_", configure)
    panel.settings.click()
    assert len(panel.drafts) == 1, messages
    window.btn_patches_build.click()
    assert not messages
    assert base.read_bytes() == source
    built_path = Path(window._patch_base_source)
    built = built_path.read_bytes()
    assert built[setting] == 56 and panel.rows[0]["state"] == "configured"
    draft_actions = window.btn_patches_build.parentWidget()
    saved_actions = window._patch_saved_actions
    assert draft_actions.isHidden() and not saved_actions.isHidden()
    panel.remove_buttons["0x1"].click()
    assert not draft_actions.isHidden() and saved_actions.isHidden()
    panel.remove_buttons["0x1"].click()  # Cancelling the edit restores the saved result.
    assert draft_actions.isHidden() and not saved_actions.isHidden()
    assert saved_actions.reveal.text() == built_path.name
    assert all(images.checksum_status(built)[k] for k in ("boot", "program", "cal"))
    assert panel.undo_build.isEnabled()
    panel.undo_build.trigger()
    assert Path(window._patch_base_source).read_bytes() == source
    assert window._load_patch_base_path(built_path)
    record = tmp_path / "rollback.json"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_: (str(record), ""))
    panel.export_record.trigger()
    assert record.is_file()

    # Reopen via the Library with only the XDF and the portable rollback record.
    panel.library = ExternalPatchLibrary(tmp_path / "second-library")
    panel.library.import_file(xdf)
    foreign = bytearray(source)
    foreign[setting] = 0x42
    window._set_patch_base(images.correct_checksums(foreign, "MS43")[0], "other-tool.bin")
    assert "XDF baseline" in (panel.remove_buttons["0x1"].text() + panel.remove_buttons["0x1"].toolTip())
    entry = next(e for e in window._backup_mgr.entries if Path(e.path) == built_path)
    monkeypatch.setattr(window, "_selected_backup", lambda: entry)
    window._on_backup_open_patches()
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_: (str(record), ""))
    panel.import_record.trigger()
    assert panel.rows[0]["removable"], messages
    assert "Remove" in panel.remove_buttons["0x1"].text()
    panel.remove_buttons["0x1"].click()
    window.btn_patches_build.click()
    assert not messages and Path(window._patch_base_source).read_bytes() == source

    def with_other_tune(data):
        changed = bytearray(data)
        changed[0x70234] ^= 1
        return images.correct_checksums(changed, "MS43")[0]

    edited = tmp_path / "edited.bin"
    edited.write_bytes(with_other_tune(built))
    assert window._load_patch_base_path(edited)
    questions = []
    monkeypatch.setattr(QMessageBox, "question",
                        lambda _, title, *args: questions.append(title) or QMessageBox.Yes)
    panel.import_record.trigger()
    assert questions == ["Attach rollback record"] and not panel.undo_build.isEnabled()
    panel.remove_buttons["0x1"].click()
    window.btn_patches_build.click()
    assert not messages and Path(window._patch_base_source).read_bytes() == with_other_tune(source)
    assert window._load_patch_base_path(base)
    assert panel.undo_build.isEnabled() and not panel.history
    other = tmp_path / "other.xdf"
    other.write_text(xdf.read_text().replace('patchdata="42"', 'patchdata="43"'), encoding="utf-8")
    panel.import_catalog(other)
    assert window._load_patch_base_path(base) and panel.sources.currentText() == other.name
    panel.checkboxes["0x1"].click()
    monkeypatch.setattr(FieldDialog, "exec_", lambda _: QDialog.Rejected)
    panel.settings.click()
    window.btn_patches_build.click()
    assert not messages and window._patch_base[setting] == 0x43
    window._set_patch_base(bytes(256 * 1024), "ms41.bin")
    assert window.patches_splitter.widget(0) is window._native_patch_scroll


def test_temporary_xdf_export_and_flash_share_rollback_and_leave_draft_intact(window, tmp_path, monkeypatch):
    from tests.test_ms42_ms43_xdf_patches import fixture

    panel = window._external_patches
    lib = panel.library = ExternalPatchLibrary(tmp_path / "catalog")
    base = images.correct_checksums(synthetic_image("MS42"), "MS42")[0]
    offset = 0x48111
    lib.import_file(fixture(tmp_path, [("Cluster LEDs", [(offset, base[offset:offset+1], b"\x42")])]))
    connect(window, "MS42")
    window._set_patch_base(base, "source.bin")
    panel.checkboxes["0x1"].click()

    monkeypatch.setattr(QInputDialog, "getText", lambda *args, **kwargs: ("", False))
    window.btn_patches_build.click()
    monkeypatch.setattr(QDialog, "exec_", lambda dialog: QDialog.Rejected)
    window.btn_patches_flash.click()
    assert not (lib.directory / "rollback").exists()
    assert panel.actions["0x1"] == "install" and window._patch_base == base

    target = tmp_path / "LEDs.bin"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: (str(target), ""))
    flashed = []
    monkeypatch.setattr(window, "_ms42_ms43_write",
                        lambda data, name, mode: flashed.append((bytes(data), name, mode)))

    def review(dialog):
        buttons = {b.text(): b for b in dialog.findChildren(QPushButton)}
        buttons["Export BIN…"].click()
        assert lib.previous(target.read_bytes()) == base
        buttons["Continue to flash"].click()
        return dialog.result()

    monkeypatch.setattr(QDialog, "exec_", review)
    window.btn_patches_flash.click()
    output = target.read_bytes()
    assert output[offset] == 0x42
    assert flashed == [(output, "MS42 Cluster LEDs.bin", "full")]
    assert not window._backup_mgr.entries and window._patch_saved_actions.isHidden()
    assert panel.actions["0x1"] == "install" and window._patch_base == base


def test_read_patch_base_uses_connected_family_and_archived_bytes(window, worker_runner, tmp_path, monkeypatch):
    ecu = connect(window, "MS42")
    source = images.correct_checksums(synthetic_image("MS42"), "MS42")[0]
    window._external_patches.library = ExternalPatchLibrary(tmp_path / "catalog")
    ecu.read_full = lambda **kwargs: source
    monkeypatch.setattr(window, "_offer_additional_read_copy", lambda *_: None)
    window._on_patches_read_ecu()
    assert window.patches_splitter.widget(0) is window._external_patches
    assert window._patch_base == Path(window._patch_base_source).read_bytes() == source


def test_patch_configuration_keeps_other_and_independent_drafts(window, tmp_path, monkeypatch):
    from xml.etree import ElementTree as ET
    from ms42_ms43_xdf_patches import configuration
    from tests.test_ms42_ms43_xdf_patches import fixture

    source = images.correct_checksums(synthetic_image("MS42"), "MS42")[0]
    address = 0x48111
    path = fixture(tmp_path, [("Cluster defaults", [(address, source[address:address+1], b"\x08")])], fields=True)
    tree = ET.parse(path)
    independent = ET.SubElement(tree.getroot(), "XDFCONSTANT")
    ET.SubElement(independent, "title").text = "Independent threshold"
    ET.SubElement(independent, "EMBEDDEDDATA", mmedaddress="0x48150", mmedelementsizebits="8")
    tree.write(path)
    panel = window._external_patches
    panel.library = ExternalPatchLibrary(tmp_path / "library")
    panel.library.import_file(path)
    window._set_patch_base(source, "source.bin")
    ids = {field["title"]: field["id"] for field in configuration(panel.catalog, source)}
    independent_id = ids["Independent threshold"]
    flag_id = ids["Flag 0"]
    independent_key = (independent_id, "z", 0, 0)
    panel.drafts[independent_key] = "42"
    seen = []

    def edit(dialog):
        seen.append(set(dialog.editors))
        if len(seen) == 1:
            assert seen[-1] == {ids["Flag 0"], ids["Flag 1"]}
            dialog.editors[flag_id].click()
            return QDialog.Accepted
        assert seen[-1] == set(ids.values())
        assert dialog.editors[independent_id].text() == "42"
        dialog.editors[independent_id].setText("99")
        return QDialog.Rejected

    monkeypatch.setattr(FieldDialog, "exec_", edit)
    panel.configure_buttons["0x1"].click()
    assert panel.drafts[independent_key] == "42"
    assert (flag_id, "z", 0, 0) in panel.drafts
    before = dict(panel.drafts)
    panel.settings.click()
    assert panel.drafts == before
    assert not any(panel.actions.values())


def test_simple_fields_keep_precision_flags_and_read_only_values(window):
    value = "123.456789012345"
    fields = [
        dict(id="number", title="Threshold", kind="scalar", rows=1, columns=1,
             values=[[1.0]], units="RPM", minimum=0, maximum=1000, description="Threshold help"),
        dict(id="flag", title="Enable feature", kind="flag", rows=1, columns=1, values=[[False]]),
        dict(id="locked", title="Source information", kind="scalar", rows=1, columns=1,
             values=[[42]], read_only=True, reason="Information only"),
    ]
    other = ("elsewhere", "z", 0, 0)
    dialog = FieldDialog(fields, {other: "17"}, window)
    dialog.editors["number"].setText(value)
    assert dialog.edits[("number", "z", 0, 0)] == value
    dialog.editors["flag"].click()
    assert dialog.edits[("flag", "z", 0, 0)] is True
    assert dialog.editors["locked"].isReadOnly() or not dialog.editors["locked"].isEnabled()
    assert dialog.edits[other] == "17"


@pytest.mark.parametrize("kind", ["scalar", "flag"])
def test_array_constants_and_flags_keep_every_cell_accessible(window, kind):
    field = dict(id="array", title="Array setting", kind=kind, rows=1, columns=2,
                 values=[[False, True]] if kind == "flag" else [[1, 2]],
                 axes={"x": dict(values=[10, 20], read_only=False)})
    dialog = FieldDialog([field], {}, window)
    assert "array" not in dialog.editors
    grid = dialog.grids["array"]
    assert grid.rowCount() == 2 and grid.columnCount() == 2
    grid.item(0, 1).setText("25")
    if kind == "flag":
        assert grid.item(1, 1).flags() & Qt.ItemIsUserCheckable
        grid.item(1, 1).setCheckState(Qt.Unchecked)
        expected = False
    else:
        grid.item(1, 1).setText("8")
        expected = "8"
    assert dialog.edits == {("array", "x", 0, 1): "25", ("array", "z", 0, 1): expected}


@pytest.mark.parametrize("point_size", [9, 12])
def test_catalog_actions_align_and_details_preserve_layout(window, tmp_path, point_size):
    from tests.test_ms42_ms43_xdf_patches import fixture

    QApplication.instance().setFont(QFont("Segoe UI", point_size))
    window.setFont(QFont("Segoe UI", point_size))
    source = images.correct_checksums(synthetic_image("MS42"), "MS42")[0]
    path = fixture(tmp_path, [
        ("Cluster defaults", [(0x48111, source[0x48111:0x48112], b"\x08")]),
        ("Second patch", [(0x48121, source[0x48121:0x48122], b"\x08")]),
        ("Unsupported entry", [(0x80000, b"\x00", b"\x08")]),
    ], fields=True)
    panel = window._external_patches
    panel.setFont(QFont("Segoe UI", point_size))
    panel.library = ExternalPatchLibrary(tmp_path / "library")
    panel.library.import_file(path)
    window._set_patch_base(source, "source.bin")
    window.tabs.setCurrentIndex(window._patch_tab_index)
    window.resize(980, 740)
    window.show()
    QApplication.processEvents()
    assert panel.header.isVisible()
    assert all(row.isVisible() for row in panel.row_widgets.values())
    assert panel.checkboxes["0x1"].font().pointSize() == point_size
    for buttons in (panel.configure_buttons, panel.remove_buttons, panel.detail_buttons):
        geometry = [(button.mapTo(panel, QPoint()).x(), button.width(), button.height())
                    for button in buttons.values()]
        assert len(set(geometry)) == 1
    assert len({buttons["0x1"].width() for buttons in
                (panel.configure_buttons, panel.remove_buttons, panel.detail_buttons)}) == 1
    assert panel.configure_buttons["0x1"].isEnabled()
    assert not panel.configure_buttons["0x2"].isEnabled()
    assert panel.checkboxes["0x1"].isEnabled()
    assert not panel.checkboxes["0x3"].isEnabled()
    panel.checkboxes["0x1"].click()
    assert panel.actions["0x1"] == "install" and window.btn_patches_build.isEnabled()
    assert any(word in panel.statuses["0x1"].text().lower() for word in ("selected", "pending"))
    panel.checkboxes["0x1"].click()
    assert not window.btn_patches_build.isEnabled()
    before = {key: (row.pos(), row.size()) for key, row in panel.row_widgets.items()}
    panel.detail_buttons["0x1"].click()
    QApplication.processEvents()
    popup = panel.details["0x1"]
    assert popup.isVisible() and popup.windowType() == Qt.Popup
    assert before == {key: (row.pos(), row.size()) for key, row in panel.row_widgets.items()}
    QTest.keyClick(popup, Qt.Key_Escape)
    assert not popup.isVisible()
    window._set_patch_base(source, "reopened.bin")
    QApplication.processEvents()
    assert panel.header.isVisible()
    assert all(row.isVisible() for row in panel.row_widgets.values())
