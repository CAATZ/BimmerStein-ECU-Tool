"""Offline UI contracts for diagnostics, Library and installed-editor handoff."""

import json
import os
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt5.QtCore import QProcess
from PyQt5.QtWidgets import QApplication, QFileDialog, QInputDialog, QMessageBox

import backup_manager
import gui
import tuning_suite
from dtc import DS2DTCRecord, MS41FaultMemory


@pytest.fixture
def window(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    gui.configure_application(app)
    root = tmp_path / "backups"
    monkeypatch.setattr(backup_manager, "BACKUP_DIR", str(root))
    monkeypatch.setattr(backup_manager, "INDEX_FILE", str(root / "index.json"))
    monkeypatch.setattr(gui, "BACKUP_DIR", str(root))
    monkeypatch.setattr(gui, "mutable_path", lambda name: tmp_path / name)
    monkeypatch.setattr(gui.MS41FlashGUI, "_refresh_ports", lambda self: None)
    monkeypatch.setattr(tuning_suite, "installation_candidates", lambda: iter(()))
    instance = gui.MS41FlashGUI()

    def run(task, on_success=None, **kwargs):
        result = task(lambda *_args: None, lambda *_args: None)
        if on_success:
            on_success(result)

    monkeypatch.setattr(instance, "_run_task", run)
    monkeypatch.setattr(instance, "_run_state_changing_task", run)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    yield instance
    instance._ds2 = None
    instance.close()
    app.processEvents()


def test_stored_shadow_details_export_and_stale_reset(window, monkeypatch, tmp_path):
    stored = DS2DTCRecord(8, 0x40, bytes(10), frequency=3, qualifiers=("Currently present",))
    shadow = DS2DTCRecord(100, 0, bytes(11), memory="shadow", frequency=2)
    connection = object()
    window._ds2 = connection
    window._ecu_variant = window._ecu_program_variant = "MS41.2"
    calls = []
    monkeypatch.setattr(gui, "read_ms41_fault_memory", lambda ds2, variant:
                        calls.append((ds2, variant)) or MS41FaultMemory(variant, (stored,), (shadow,)))
    window._on_read_dtc()
    assert calls == [(connection, "MS41.2")]
    assert window._dtcs == [stored]
    window.cb_dtc_memory.setCurrentIndex(1)
    assert window._dtcs == [shadow]
    window.dtc_table.selectRow(0)
    window._on_dtc_selected()
    assert "Shadow" in window.dtc_detail.toPlainText()
    assert "Frequency" in window.dtc_detail.toPlainText()
    destination = tmp_path / "faults.txt"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(destination), ""))
    window._on_export_dtc()
    report = destination.read_text(encoding="utf-8")
    assert "STORED FAULT MEMORY" in report and "SHADOW FAULT MEMORY" in report
    assert "Currently present" in report and "Frequency: 3" in report
    window._on_diag_profile_changed()
    assert window._fault_memories == {} and window._dtcs == []


@pytest.mark.parametrize("fail,remaining", [(False, 0), (False, 1), (True, 0)])
def test_clear_requires_readback_to_claim_result(window, monkeypatch, fail, remaining):
    calls = []

    def read():
        calls.append("read")
        if fail:
            raise OSError("disconnected")
        return bytes([remaining]) + (bytes([8]) + bytes(9)) * remaining

    window._ds2 = SimpleNamespace(clear_dtc=lambda: calls.append("clear"), read_dtc=read)
    window._ecu_variant = "MS41.2"
    window._on_clear_dtc()
    assert calls == ["clear", "read"]
    assert ("confirmation read failed" if fail else f"{remaining} stored fault(s) remain") in \
        window.lbl_dtc_count.text()
    if fail:
        assert window._fault_memories == {}


@pytest.mark.parametrize("fail", [False, True])
def test_adaptation_reset_refreshes_or_invalidates_old_values(window, monkeypatch, fail):
    calls = []
    window._ecu_variant = "MS41.2"
    window._ecu_id = "1406464"
    window._ds2 = SimpleNamespace(ADAPT_ALL=(0, 0), ADAPT_IDLE=(1, 0), ADAPT_KNOCK=(2, 0),
                                 ADAPT_LAMBDA=(3, 0), ADAPT_THROTTLE=(4, 0),
                                 clear_adaptations=lambda *a: calls.append("reset"))
    monkeypatch.setattr(QInputDialog, "getItem", lambda *a, **k: ("All adaptations", True))

    def read(ds2, ecu_id):
        calls.append((ds2, ecu_id))
        if fail:
            raise OSError("refresh failed")
        return {"refreshed": True}

    monkeypatch.setattr(gui, "read_adaptations", read)
    shown = []
    monkeypatch.setattr(window, "_show_adaptations", shown.append)
    window._on_reset_adaptations()
    assert calls[0] == "reset" and calls[1][1] == "1406464"
    assert shown == ([] if fail else [{"refreshed": True}])
    assert ("refresh unavailable" if fail else "values refreshed") in window.lbl_adapt_status.text()


def test_handoff_requires_installed_compatible_executable(tmp_path, monkeypatch):
    executable = tmp_path / tuning_suite.EXECUTABLE
    monkeypatch.setattr(tuning_suite, "installation_candidates", lambda: iter((executable,)))
    assert tuning_suite.handoff_editor()[0] is None
    executable.write_bytes(b"test executable")
    assert "Update" in tuning_suite.handoff_editor()[1]
    (tmp_path / tuning_suite.CAPABILITIES).write_text(json.dumps({"open_bin_argument": "--open"}))
    assert tuning_suite.handoff_editor()[0] == executable
    executable.unlink()
    assert tuning_suite.handoff_editor()[0] is None


def test_handoff_preserves_catalogue_and_handles_uninstallation(window, monkeypatch, tmp_path):
    payload = bytes(24 * 1024)
    entry = window._backup_mgr.add_data(payload, "tune.bin")
    window._refresh_backup_table()
    window.backup_table.selectRow(0)
    assert not window.act_backup_tuning.isEnabled()
    executable = tmp_path / "suite" / tuning_suite.EXECUTABLE
    executable.parent.mkdir()
    executable.write_bytes(b"test")
    (executable.parent / tuning_suite.CAPABILITIES).write_text('{"open_bin_argument":"--open"}')
    monkeypatch.setattr(tuning_suite, "installation_candidates", lambda: iter((executable,)))
    window._set_backup_buttons_enabled()
    assert window.act_backup_tuning.isEnabled()
    launches = []
    monkeypatch.setattr(QProcess, "startDetached", lambda *args: launches.append(args) or (True, 123))
    window.act_backup_tuning.trigger()
    assert len(launches) == 1 and launches[0][1][0] == "--open"
    working = Path(launches[0][1][1])
    assert working.read_bytes() == payload and working != Path(entry.path)
    assert window._backup_mgr.read_data(entry.filename, entry.sha256) == payload
    executable.unlink()
    messages = []
    monkeypatch.setattr(QMessageBox, "information", lambda *args: messages.append(args[2]))
    window._on_backup_open_tuning()
    assert len(launches) == 1 and "Install" in messages[0]
    window.backup_open_menu.aboutToShow.emit()
    assert not window.act_backup_tuning.isEnabled()


def test_library_folder_filter_move_and_portable_rename(window, monkeypatch):
    entry = window._backup_mgr.add_data(bytes(512), "before.bin", variant="MS41.2", notes="daily")
    window._backup_mgr.create_folder("Road/Baselines")
    window._refresh_backup_table()
    window.backup_table.selectRow(0)
    monkeypatch.setattr(QInputDialog, "getItem", lambda *a, **k: ("Road/Baselines", True))
    window.act_backup_move.trigger()
    assert entry.folder == "Road/Baselines"
    window.cb_backup_folder.setCurrentIndex(1)  # Unfiled
    assert window.backup_table.isRowHidden(0)
    window.cb_backup_folder.setCurrentIndex(0)
    window._backup_search.setText("daily")
    assert not window.backup_table.isRowHidden(0)
    window.backup_table.selectRow(0)
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("baseline.bin", True))
    window.act_backup_rename.trigger()
    assert entry.filename == "Road/Baselines/baseline.bin"
    assert window._backup_mgr.read_data(entry.filename, entry.sha256) == bytes(512)


def test_ecu_info_export_uses_displayed_fields_without_markup(window, monkeypatch):
    window._last_ident_raw = b"\x12\x34"
    window._info_labels["Calibration ID"].setText("<b>12011110 (12)</b>")
    report = window._ecu_info_report()
    assert "Calibration ID: 12011110 (12)" in report
    assert "<b>" not in report and "Raw IDENT: 12 34" in report


@pytest.mark.parametrize("same_variant", [False, True])
def test_library_eeprom_compare_decodes_only_matching_layouts(window, monkeypatch, same_variant):
    import eeprom_editor

    before = bytes(512)
    after = bytes([1]) + bytes(511)
    first = window._backup_mgr.add_data(before, "first.bin", variant="MS41.2")
    second = window._backup_mgr.add_data(
        after, "second.bin", variant="MS41.2" if same_variant else "MS41.0")
    monkeypatch.setattr(window, "_selected_backups", lambda: [first, second])
    comparisons = []

    def compare(parent, old, new, variant, **kwargs):
        comparisons.append((old, new, variant, kwargs))
        return SimpleNamespace(exec_=lambda: None)

    monkeypatch.setattr(eeprom_editor, "EepromComparisonDialog", compare)
    window._on_backup_compare()
    assert len(comparisons) == 1
    assert comparisons[0][:3] == (before, after, "MS41.2" if same_variant else None)
    assert ("Archived" if same_variant else "only raw bytes") in comparisons[0][3]["warning"]


@pytest.mark.parametrize("corrupt", [False, True])
def test_handoff_rejects_changed_original_and_cleans_failed_launch(window, monkeypatch, tmp_path, corrupt):
    entry = window._backup_mgr.add_data(bytes(24 * 1024), "original.bin")
    window._refresh_backup_table()
    window.backup_table.selectRow(0)
    monkeypatch.setattr(tuning_suite, "handoff_editor", lambda: (tmp_path / "suite.exe", ""))
    if corrupt:
        Path(entry.path).write_bytes(bytes([1]) + bytes(24 * 1024 - 1))
    launches, messages = [], []
    monkeypatch.setattr(QProcess, "startDetached", lambda *args: launches.append(args) or (False, 0))
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: messages.append(args[2]))
    window._on_backup_open_tuning()
    assert len(messages) == 1
    assert len(launches) == (0 if corrupt else 1)
    if launches:
        assert not Path(launches[0][1][1]).exists()
        assert window._backup_mgr.read_data(entry.filename, entry.sha256) == bytes(24 * 1024)


def test_log_filename_filter_preserves_library_root_and_folder_navigation(window, tmp_path):
    from library_browser import LibraryBrowser

    root = tmp_path / "logs"
    (root / "older").mkdir(parents=True)
    (root / "session.csv").write_text("Time (s),Engine RPM\n0,800\n")
    dialog = LibraryBrowser(window, "Logs", [root])
    for query in ("no-such-file", "session", ""):
        dialog.search.setText(query)
        QApplication.processEvents()
        assert dialog.tree.rootIndex().isValid()
        assert Path(dialog.model.filePath(dialog.tree.rootIndex())) == root
        assert dialog.model.index(str(root / "older")).isValid()
    dialog.close()


def test_bins_refresh_on_open_and_open_selected_real_folder(window, tmp_path, monkeypatch):
    folder = tmp_path / "backups" / "Explorer" / "Nested"
    folder.mkdir(parents=True)
    (folder / "external.bin").write_bytes(bytes(512))
    window.tabs.setCurrentIndex(window._backup_tab_index)
    assert window.backup_table.rowCount() == 1
    assert window.backup_table.item(0, 1).text() == "external.bin"
    window.cb_backup_folder.setCurrentIndex(window.cb_backup_folder.findData("Explorer/Nested"))
    opened = []
    monkeypatch.setattr(gui.QDesktopServices, "openUrl", lambda url: opened.append(url.toLocalFile()) or True)
    window._on_backup_open_folder()
    assert Path(opened[-1]) == folder
    window.cb_backup_folder.setCurrentIndex(0)
    window.backup_table.selectRow(0)
    window._on_backup_open_folder()
    assert Path(opened[-1]) == folder
    (folder / "external.bin").rename(folder / "renamed.bin")
    window.act_backup_refresh.trigger()
    assert window.backup_table.item(0, 1).text() == "renamed.bin"
    source = tmp_path / "import.bin"
    source.write_bytes(bytes([1]) * 512)
    window.cb_backup_folder.setCurrentIndex(window.cb_backup_folder.findData("Explorer/Nested"))
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(source), ""))
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("imported note", True))
    window._on_backup_add()
    assert (folder / source.name).read_bytes() == source.read_bytes()
    entry = next(e for e in window._backup_mgr.entries if e.notes == "imported note")
    assert entry.folder == "Explorer/Nested"
