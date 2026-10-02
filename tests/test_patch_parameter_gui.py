import os
from copy import deepcopy
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PyQt5")
from PyQt5.QtCore import QPoint, Qt
from PyQt5.QtGui import QFont
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QComboBox, QDialog, QMessageBox, QPushButton, QSpinBox, QDoubleSpinBox, QLabel

import checksum
import gui
import patch_service
from tests.conftest import ref


@pytest.fixture
def patch_window(monkeypatch, tmp_path):
    app = QApplication.instance() or QApplication([])
    gui.configure_application(app)
    window = gui.MS41FlashGUI()
    archived = []
    errors = []
    window._ds2 = None
    monkeypatch.setattr(QMessageBox, "warning", lambda *_a, **_k: QMessageBox.Yes)
    monkeypatch.setattr(QMessageBox, "question", lambda *_a, **_k: QMessageBox.Yes)
    monkeypatch.setattr(QMessageBox, "information", lambda *_a, **_k: QMessageBox.Ok)
    monkeypatch.setattr(
        QMessageBox, "critical",
        lambda _parent, title, message, *_a, **_k: errors.append((title, message)),
    )

    def archive(data, name, **metadata):
        archived.append(bytes(data))
        return SimpleNamespace(filename=name, path=str(tmp_path / name))

    monkeypatch.setattr(window._backup_mgr, "add_data", archive)
    monkeypatch.setattr(window, "_refresh_backup_table", lambda: None)
    monkeypatch.setattr(window, "_offer_additional_read_copy", lambda *_a, **_k: None)
    try:
        yield window, archived, errors
    finally:
        window.close()
        app.processEvents()


def _edit_dialog(monkeypatch, changes, *, expected=None, accept=True):
    def edit(dialog):
        for parameter_id, value in (expected or {}).items():
            editor = dialog.findChild(QComboBox, parameter_id)
            if editor is not None:
                assert str(editor.currentData()) == value
            else:
                assert dialog.findChild(gui._PatchNumberEditor, parameter_id + "_editor").input_value() == value
        for parameter_id, value in changes.items():
            editor = dialog.findChild(QComboBox, parameter_id)
            if editor is not None:
                editor.setCurrentIndex(editor.findData(value))
            else:
                _set_number(dialog, parameter_id, value)
        return QDialog.Accepted if accept else QDialog.Rejected

    monkeypatch.setattr(QDialog, "exec_", edit)


def _set_number(dialog, parameter_id, value):
    editor = dialog.findChild(gui._PatchNumberEditor, parameter_id + "_editor")
    assert editor is not None
    for option, button in editor.modes:
        button.setChecked(value == option["value"])
    if not value.startswith("@"):
        editor.spin.lineEdit().setText(value + editor.spin.suffix())
    return editor


def test_installed_parameter_patch_has_configure_button(patch_window, monkeypatch):
    window, archived, errors = patch_window
    image = patch_service.build_image(ref("MS41.3"), ["ignition_cut_v11"])[0]
    window._set_patch_base(image, "parameter-test.bin")

    group = window._patch_parameter_groups["ignition_cut_v11"]
    button = window._patch_rows["ignition_cut_v11"].findChild(
        QPushButton, "patch_configure_ignition_cut_v11")
    assert group["editable"] is True
    assert len(group["parameters"]) == 4
    assert button is not None and button.isEnabled()

    _edit_dialog(monkeypatch, {"CUTRPM": "4000"})
    button.click()
    assert window._patch_base == image
    assert window.btn_patches_build.isEnabled()
    window._on_patches_build()

    expected = patch_service.apply_parameter_changes(
        image, "ignition_cut_v11", {"CUTRPM": "4000"})[0]
    assert archived == [expected]
    assert window._patch_base == expected
    assert not window._patch_parameter_changes
    assert not window.btn_patches_build.isEnabled()
    assert not errors


def test_configuration_shows_current_modes_and_reviews_rounded_values(patch_window, monkeypatch):
    window, _archived, errors = patch_window
    source = patch_service.build_image(ref("MS41.3"), ["ignition_cut_v11"])[0]
    source = patch_service.apply_parameter_changes(
        source, "ignition_cut_v11", {"CUTRPM": "4000"})[0]
    window._set_patch_base(source, "current-values.bin")
    reviews = []
    monkeypatch.setattr(QMessageBox, "warning",
                        lambda _parent, _title, text, *_a: reviews.append(text) or QMessageBox.Yes)

    def edit(dialog):
        assert dialog.findChild(QSpinBox, "CUTRPM").value() == 4000
        assert dialog.findChild(QPushButton, "CUT_HYST_mode_legacy_zero").isChecked()
        assert dialog.findChild(QPushButton, "CUT_IPW_mode_stock").isChecked()
        assert not dialog.findChild(QDoubleSpinBox, "CUT_IPW").isVisible()
        editor = _set_number(dialog, "CUTRPM", "4200")
        assert editor.context.text() == "Will use 4192 RPM"
        return QDialog.Accepted

    monkeypatch.setattr(QDialog, "exec_", edit)
    window._on_patch_configure("ignition_cut_v11")
    assert "4000 RPM → 4192 RPM" in reviews[0]
    assert window._patch_parameter_changes["ignition_cut_v11"]["changes"] == {"CUTRPM": "4192"}
    _edit_dialog(monkeypatch, {"CUTRPM": "4200"}, expected={"CUTRPM": "4192"})
    window._on_patch_configure("ignition_cut_v11")
    assert len(reviews) == 1  # Rounding to the existing draft is a harmless no-op.
    assert not errors


def test_launch_configuration_rejects_zero_arm_speed(patch_window, monkeypatch):
    window, archived, errors = patch_window
    source = patch_service.build_image(
        ref("MS41.3"), ["ignition_cut_v11", "launch_control_v11"])[0]
    window._set_patch_base(source, "launch-arm-speed.bin")
    group = window._patch_parameter_groups["launch_control_v11"]
    arm_speed = next(parameter for parameter in group["parameters"]
                     if parameter["id"] == "LC_ARMSPEED")
    assert arm_speed["minimum"] == "1"

    _edit_dialog(monkeypatch, {"LC_ARMSPEED": "0"})
    window._on_patch_configure("launch_control_v11")

    assert "LC_ARMSPEED must be between 1" in errors[-1][1]
    assert window._patch_base == source
    assert not window._patch_parameter_changes
    assert not archived


def test_launch_configuration_accepts_hard_below_soft(patch_window, monkeypatch):
    window, archived, errors = patch_window
    launch = "launch_control_v11"
    source = patch_service.build_image(ref("MS41.3"), ["ignition_cut_v11", launch])[0]
    window._set_patch_base(source, "launch-hard-below-soft.bin")
    changes = {
        "LC_SW": "always", "LC_CUTTYPE": "fuel", "LC_ARMSPEED": "1",
        "LC_MAXSPEED": "10", "LC_MAXRPM": "4000", "LC_HARDRPM": "3968",
    }
    _edit_dialog(monkeypatch, changes)
    window._on_patch_configure(launch)
    assert window._patch_base == source
    assert not errors

    window._on_patches_build()
    expected = patch_service.apply_parameter_changes(source, launch, changes)[0]
    assert archived == [expected]
    assert window._patch_base == expected
    assert not errors


def test_launch_fuel_hysteresis_dialog_keeps_stock_and_custom_choices(patch_window, monkeypatch):
    window, archived, errors = patch_window
    launch = "launch_control_v11"
    source = patch_service.build_image(ref("MS41.3"), ["ignition_cut_v11", launch])[0]
    source = patch_service.apply_parameter_changes(
        source, launch, {"LC_FUEL_HYST_B": "64"})[0]
    window._set_patch_base(source, "launch-fuel-hysteresis.bin")

    def edit(dialog):
        b = dialog.findChild(gui._PatchNumberEditor, "LC_FUEL_HYST_B_editor")
        a = dialog.findChild(gui._PatchNumberEditor, "LC_FUEL_HYST_A_editor")
        assert b.input_value() == "64"
        assert a.input_value() == "@stock"
        assert a.context.text().startswith("Stock: ")
        _set_number(dialog, "LC_FUEL_HYST_B", "@stock")
        _set_number(dialog, "LC_FUEL_HYST_A", "16")
        return QDialog.Accepted

    monkeypatch.setattr(QDialog, "exec_", edit)
    window._on_patch_configure(launch)
    window._on_patches_build()
    expected = patch_service.apply_parameter_changes(
        source, launch, {"LC_FUEL_HYST_B": "@stock", "LC_FUEL_HYST_A": "32"})[0]
    assert archived == [expected]
    assert not errors


@pytest.mark.parametrize("alter_checksum_store", (False, True))
def test_selected_patches_configure_before_first_build(
        patch_window, monkeypatch, alter_checksum_store):
    window, archived, errors = patch_window
    image = bytearray(ref("MS41.3"))
    if alter_checksum_store:
        image[0x5C80] ^= 1
    image = bytes(image)
    ignition = "ignition_cut_v11"
    launch = "launch_control_v11"
    window._set_patch_base(image, "unpatched-base.bin")
    ignition_button = window._patch_rows[ignition].findChild(
        QPushButton, "patch_configure_" + ignition)
    launch_button = window._patch_rows[launch].findChild(
        QPushButton, "patch_configure_" + launch)
    assert ignition_button is not None and not ignition_button.isEnabled()
    assert launch_button is not None and not launch_button.isEnabled()

    window._patch_checkboxes[launch].setChecked(True)
    assert window._patch_checkboxes[ignition].isChecked()
    assert ignition_button.isEnabled() and launch_button.isEnabled()

    _edit_dialog(monkeypatch, {"CUTSW": "pin80", "CUTRPM": "4000"})
    ignition_button.click()
    launch_changes = {"LC_MAXRPM": "4000", "LC_FUEL_HYST_B": "0", "LC_FUEL_HYST_A": "160"}
    _edit_dialog(monkeypatch, launch_changes,
                 expected={"LC_FUEL_HYST_B": "@stock", "LC_FUEL_HYST_A": "@stock"})
    launch_button.click()
    _edit_dialog(
        monkeypatch, {"CUT_HYST": "64"},
        expected={"CUTSW": "pin80", "CUTRPM": "4000"},
    )
    ignition_button.click()
    assert window._patch_base == image
    assert archived == []
    assert window._patch_checkboxes[ignition].isChecked()
    assert window._patch_checkboxes[launch].isChecked()
    assert set(window._patch_parameter_changes) == {ignition, launch}

    window._on_patches_build()

    expected = patch_service.build_image(image, [ignition, launch])[0]
    expected = patch_service.apply_parameter_changes(
        expected, ignition, {"CUTSW": "pin80", "CUTRPM": "4000", "CUT_HYST": "64"})[0]
    expected = patch_service.apply_parameter_changes(
        expected, launch, launch_changes)[0]
    assert archived == [expected]
    assert checksum.verify_checksum(bytearray(archived[0]))[0]
    assert window._patch_base == expected
    assert {ignition, launch} <= window._patch_installed_ids
    assert not window._patch_parameter_changes
    assert not window.btn_patches_build.isEnabled()
    assert not errors


@pytest.mark.parametrize("action", (
    "cancel-dialog", "cancel-review", "invalid-value", "deselect", "reload",
    "restore-original", "stale-descriptor", "archive-failure",
))
def test_parameter_draft_lifecycle(patch_window, monkeypatch, action):
    window, archived, errors = patch_window
    image = ref("MS41.3")
    patch_id = "ignition_cut_v11"
    window._set_patch_base(image, "unpatched-base.bin")
    window._patch_checkboxes[patch_id].setChecked(True)
    _edit_dialog(monkeypatch, {"CUTRPM": "4000"})
    window._on_patch_configure(patch_id)
    assert window._patch_parameter_changes[patch_id]["changes"]["CUTRPM"] == "4000"

    if action == "stale-descriptor":
        window._patch_parameter_changes[patch_id]["descriptor_token"] = "0" * 64
    pending = deepcopy(window._patch_parameter_changes)
    if action == "deselect":
        window._patch_checkboxes[patch_id].setChecked(False)
    elif action == "reload":
        window._set_patch_base(image, "another-base.bin")
    elif action == "restore-original":
        original = patch_service.build_image(image, [patch_id])[0]
        original_rpm = next(
            parameter["current"]
            for group in patch_service.editable_parameters(original)
            if group["patch_id"] == patch_id
            for parameter in group["parameters"] if parameter["id"] == "CUTRPM"
        )
        _edit_dialog(monkeypatch, {"CUTRPM": original_rpm})
        window._on_patch_configure(patch_id)
    elif action == "archive-failure":
        def fail_archive(*_a, **_k):
            raise OSError("test archive failure")

        monkeypatch.setattr(window._backup_mgr, "add_data", fail_archive)
        window._on_patches_build()
    elif action == "stale-descriptor":
        window._on_patches_build()
    else:
        _edit_dialog(
            monkeypatch, {"CUTRPM": "invalid" if action == "invalid-value" else "4800"},
            expected={"CUTRPM": "4000"}, accept=action != "cancel-dialog",
        )
        if action == "cancel-review":
            monkeypatch.setattr(QMessageBox, "warning", lambda *_a, **_k: QMessageBox.No)
        window._on_patch_configure(patch_id)

    assert window._patch_base == image
    assert archived == []
    if action in {"deselect", "reload", "restore-original"}:
        assert not window._patch_parameter_changes
        assert window.btn_patches_build.isEnabled() == (action == "restore-original")
        button = window._patch_rows[patch_id].findChild(
            QPushButton, "patch_configure_" + patch_id)
        assert button is not None
        assert button.isEnabled() == (action == "restore-original")
    else:
        assert window._patch_parameter_changes == pending
        assert window.btn_patches_build.isEnabled()
    assert bool(errors) == (action in {"invalid-value", "stale-descriptor", "archive-failure"})


def test_selected_patch_rejects_incompatible_bytes_before_configure(patch_window, monkeypatch):
    window, archived, errors = patch_window
    patch_id = "ignition_cut_v11"
    image = bytearray(ref("MS41.3"))
    image[patch_service.definitions()[patch_id]["edits"][0]["off"]] ^= 1
    window._set_patch_base(image, "incompatible-base.bin")
    window._patch_checkboxes[patch_id].setChecked(True)
    button = window._patch_rows[patch_id].findChild(
        QPushButton, "patch_configure_" + patch_id)
    warnings = []
    monkeypatch.setattr(
        QMessageBox, "warning",
        lambda _parent, title, message, *_a, **_k: warnings.append((title, message)),
    )
    monkeypatch.setattr(QDialog, "exec_", lambda _dialog: pytest.fail(
        "The Configure dialog must not open when composing the selected patch fails."))

    assert button.isEnabled()
    button.click()

    assert len(warnings) == 1
    assert warnings[0][0] == "Parameters Unavailable"
    assert "expect" in warnings[0][1]
    assert window._patch_base == bytes(image)
    assert not window._patch_parameter_changes
    assert not archived and not errors


def test_removing_unrelated_patch_keeps_selected_parameter_draft(patch_window, monkeypatch):
    window, archived, errors = patch_window
    image = patch_service.build_image(ref("MS41.3"), ["softbsl_loader", "cal_guard"])[0]
    window._set_patch_base(image, "installed-calguard.bin")
    patch_id = "ignition_cut_v11"
    window._patch_checkboxes[patch_id].setChecked(True)
    _edit_dialog(monkeypatch, {"CUTRPM": "4000"})
    window._on_patch_configure(patch_id)
    pending = deepcopy(window._patch_parameter_changes)

    window._on_patch_remove("cal_guard")

    assert window._patch_checkboxes[patch_id].isChecked()
    assert window._patch_parameter_changes == pending
    assert window._patch_removed_ids == {"cal_guard"}
    assert not archived and not errors
    window._on_patches_build()
    expected = patch_service.revert_patch(image, "cal_guard")
    expected = patch_service.build_image(expected, [patch_id])[0]
    expected = patch_service.apply_parameter_changes(expected, patch_id, {"CUTRPM": "4000"})[0]
    assert archived == [expected]
    assert not errors


def test_numeric_modes_preserve_custom_value_and_show_native_range(patch_window):
    window, _archived, _errors = patch_window
    group = next(group for group in patch_service.editable_parameters(ref("MS41.3"))
                 if group["patch_id"] == "launch_control_v11")
    parameter = deepcopy(next(item for item in group["parameters"]
                              if item["id"] == "LC_FUEL_HYST_A"))
    parameter["current"] = "160"
    parameter["specials"][0].update(display_value="8160", display_text="Stock: 8160 RPM")
    editor = gui._PatchNumberEditor(parameter)
    try:
        button = editor.modes[0][1]
        button.click()
        assert editor.input_value() == "@stock"
        assert not editor.spin.isEnabled()
        assert editor.spin.value() == 8160  # Native FF is valid; custom FF is reserved.
        assert editor.context.text() == "Stock: 8160 RPM"
        button.click()
        assert editor.input_value() == "160"
        assert editor.spin.value() == 160
        assert editor.spin.maximum() == 8128
        assert editor.spin.isEnabled()
        assert editor.spin.singleStep() == 32
        editor.spin.stepUp()
        assert editor.input_value() == "192"
    finally:
        editor.close()


def test_short_configuration_stays_compact_when_resized(patch_window, monkeypatch):
    window, _archived, errors = patch_window
    patch_id = "vanos_minrpm_v2_ms410"
    window._set_patch_base(ref("MS41.0"), "compact-form.bin")
    window._patch_checkboxes[patch_id].setChecked(True)

    def inspect(dialog):
        dialog.show()
        QApplication.processEvents()
        assert dialog.height() < 350
        editor = dialog.findChild(gui._PatchNumberEditor, "VANOSRPM_editor")
        context = dialog.findChild(QLabel, "VANOSRPM_context")
        assert editor.input_value() == "@stock"
        assert context.text() == "Original VANOS logic"
        assert not editor.spin.isVisible()
        assert context.isHidden()  # Dynamic modes show their text beside the mode button.
        assert editor.mode_value.isVisible()
        dialog.resize(dialog.width(), 650)
        QApplication.processEvents()
        assert editor.height() < 100  # Extra space stays below the controls.
        assert editor.mode_value.height() < 40
        return QDialog.Rejected

    monkeypatch.setattr(QDialog, "exec_", inspect)
    window._on_patch_configure(patch_id)
    assert not errors


def test_configuration_aligns_fields_modes_and_help(patch_window, monkeypatch):
    window, _archived, errors = patch_window
    image = patch_service.build_image(ref("MS41.3"), ["ignition_cut_v11"])[0]
    window._set_patch_base(image, "aligned-fields.bin")

    def inspect(dialog):
        dialog.show()
        QApplication.processEvents()
        positions, numeric_widths, mode_positions = [], [], []
        for parameter_id in ("CUTSW", "CUTRPM", "CUT_HYST", "CUT_IPW"):
            editor = dialog.findChild(gui._PatchNumberEditor, parameter_id + "_editor")
            field = ((editor.mode_value if editor.mode_value.isVisible() else editor.spin)
                     if editor else dialog.findChild(QComboBox, parameter_id))
            point = field.mapTo(dialog, QPoint())
            positions.append((point.x(), field.width(), field.height()))
            label = dialog.findChild(QLabel, parameter_id + "_label")
            label_y = label.mapTo(dialog, QPoint()).y()
            assert label_y == point.y() or label_y + label.height() <= point.y()
            help_text = dialog.findChild(QLabel, parameter_id + "_help")
            if help_text:
                assert help_text.mapTo(dialog, QPoint()).x() == point.x()
            if editor:
                numeric_widths.append(field.width())
                assert editor.context.isHidden()  # No duplicate preview for unchanged values.
                for _option, button in editor.modes:
                    mode_positions.append((button.mapTo(dialog, QPoint()).x(), button.width()))
                    assert button.mapTo(dialog, QPoint()).y() == point.y()
                    assert button.height() == field.height()
        assert len(set(positions)) == 1
        assert len(set(numeric_widths)) == 1
        dialog.resize(352, dialog.height())
        QApplication.processEvents()
        scroll = dialog.findChild(gui.QScrollArea)
        assert scroll.horizontalScrollBar().maximum() == 0
        assert dialog.findChild(QLabel, "CUT_IPW_label").mapTo(dialog, QPoint()).y() < (
            dialog.findChild(gui._PatchNumberEditor, "CUT_IPW_editor").mode_value.mapTo(dialog, QPoint()).y())
        for parameter_id in ("CUTRPM", "CUT_HYST", "CUT_IPW"):
            editor = dialog.findChild(gui._PatchNumberEditor, parameter_id + "_editor")
            field = editor.mode_value if editor.mode_value.isVisible() else editor.spin
            assert dialog.findChild(QLabel, parameter_id + "_help").mapTo(dialog, QPoint()).x() == (
                field.mapTo(dialog, QPoint()).x())
        assert len(set(mode_positions)) == 1
        # Dynamic values are styled fields, not an unaligned free-floating label.
        assert dialog.findChild(gui._PatchNumberEditor, "CUT_IPW_editor").mode_value.isReadOnly()
        return QDialog.Rejected

    monkeypatch.setattr(QDialog, "exec_", inspect)
    window._on_patch_configure("ignition_cut_v11")
    assert not errors


def test_patch_details_popup_preserves_rows_and_visible_warnings(patch_window):
    window, _archived, _errors = patch_window
    window._set_patch_base(ref("MS41.3"), "patch-rows.bin")
    window.tabs.setCurrentIndex(window._patch_tab_index)
    window.show()
    QApplication.processEvents()
    row = window._patch_rows["cal_guard"]
    toggle = row.findChild(QPushButton, "patch_details_toggle_cal_guard")
    warning = next(label for label in row.findChildren(QLabel) if label.text() == "UNTESTED")
    assert warning.parent() is row
    assert warning.isVisible()
    assert window._patch_checkboxes["cal_guard"].text() == "CalGuard compatibility + recovery guard"

    def row_geometry():
        return {patch_id: (patch_row.pos(), patch_row.size())
                for patch_id, patch_row in window._patch_rows.items()}

    original_geometry = row_geometry()
    toggle.click()
    QApplication.processEvents()
    details = window.findChild(QDialog, "patch_details_cal_guard")
    assert details is not None and details.isVisible()
    assert details.windowType() == Qt.Popup
    assert row_geometry() == original_geometry
    assert warning.isVisible()
    QTest.keyClick(details, Qt.Key_Escape)
    QApplication.processEvents()
    assert details.isHidden()
    assert row_geometry() == original_geometry
    assert warning.isVisible()


@pytest.mark.parametrize("point_size", (9, 13))
@pytest.mark.parametrize("viewport_width", (640, 1120))
def test_patch_header_columns_align_with_optional_fields_and_pending_changes(
        monkeypatch, request, point_size, viewport_width):
    app = QApplication.instance() or QApplication([])
    original_font = app.font()
    app.setFont(QFont("Segoe UI", point_size))
    request.addfinalizer(lambda: app.setFont(original_font))
    window = gui.MS41FlashGUI()
    request.addfinalizer(window.close)
    cases = (
        ("minimal", "", False, True, False),
        ("configure_only", "V2", False, False, True),
        ("installed", "Historical V1234", True, False, False),
        ("configured_installed", "V11", True, False, True),
    )
    patches = [
        {"id": patch_id, "title": "Patch " + patch_id, "description": "Details",
         "version": version, "installed": installed, "tested": tested,
         "status": "", "ok": True, "badge": ""}
        for patch_id, version, installed, tested, _configure in cases
    ]
    groups = [
        {"patch_id": patch_id, "editable": True, "blocked_reason": "", "parameters": []}
        for patch_id, _version, _installed, _tested, configure in cases if configure
    ]
    monkeypatch.setattr(patch_service, "available_patches", lambda _base: patches)
    monkeypatch.setattr(patch_service, "editable_parameters", lambda _base: groups)
    window._patch_base = b"layout-test"
    window._refresh_patch_list()
    window._patch_parameter_changes["configured_installed"] = {"changes": {"TEST": "1"}}
    window._on_patch_selection_changed()

    scroll = window.patches_splitter.widget(0)
    scroll.setFont(window.font())
    scroll.setParent(None)
    try:
        scroll.resize(viewport_width, 420)
        scroll.show()
        QApplication.processEvents()
        columns = {name: [] for name in ("version", "state", "validation", "configure", "remove", "details")}
        for patch_id, version, installed, tested, configure in cases:
            row = window._patch_rows[patch_id]
            for name, expected in (
                    ("version", version), ("state", "Installed" if installed else ""),
                    ("validation", "UNTESTED" if tested is False else "")):
                label = row.findChild(QLabel, f"patch_{name}_{patch_id}")
                assert label is not None
                assert label.font().pointSize() == point_size
                assert label.text() == expected
                columns[name].append((label.mapTo(window._patch_group, QPoint()).x(), label.width()))
                assert label.width() >= label.sizeHint().width()
            buttons = {"details": row.findChild(QPushButton, "patch_details_toggle_" + patch_id)}
            if configure:
                buttons["configure"] = row.findChild(QPushButton, "patch_configure_" + patch_id)
            if installed:
                buttons["remove"] = next(button for button in row.findChildren(QPushButton)
                                         if button.text() == "✕ Remove")
            for name, button in buttons.items():
                assert button is not None
                columns[name].append((button.mapTo(window._patch_group, QPoint()).x(), button.width()))
                assert button.width() >= button.sizeHint().width()
        assert all(len(set(positions)) == 1 for positions in columns.values()), columns
        for name, expected in (("patch", "Patch"), ("version", "Version"),
                               ("state", "Status"), ("validation", "Validation"),
                               ("actions", "Actions")):
            header = window._patch_group.findChild(QLabel, "patch_header_" + name)
            assert header is not None and header.text() == expected
            if name == "patch":
                checkbox = window._patch_checkboxes["minimal"]
                expected_position = (checkbox.mapTo(window._patch_group, QPoint()).x(), checkbox.width())
            elif name == "actions":
                x = columns["configure"][0][0]
                right = sum(columns["details"][0])
                expected_position = (x, right - x)
            else:
                expected_position = columns[name][0]
            assert (header.mapTo(window._patch_group, QPoint()).x(), header.width()) == expected_position
            assert header.width() >= header.sizeHint().width()
    finally:
        scroll.hide()
        window.patches_splitter.insertWidget(0, scroll)
