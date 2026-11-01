"""Identity reports and selected-memory backups stay separate; all ECU peers are fake."""

import json
from types import SimpleNamespace

import pytest
from PyQt5.QtWidgets import QApplication, QDialog, QFileDialog

import ms42_ms43_diagnostics as diagnostics
import ms42_ms43_storage as storage
from gui_controls import ConfigComboBox
from tests.test_config_hover_wheel import wheel
from tests.test_ms42_ms43_desktop import connect, window as window, worker_runner as worker_runner
from tests.test_ms42_ms43_diagnostics import identity_ecu
from tests.test_ms42_ms43_storage_desktop import snapshot


@pytest.mark.parametrize("family,kind", [
    ("MS42", "retained"), ("MS43", "eeprom"), ("MS43", "retained"),
])
def test_identity_refresh_keeps_loaded_backup_and_selected_memory(
        window, worker_runner, monkeypatch, family, kind):
    connect(window, family)
    ecu, link = identity_ecu(family)
    window._ds2 = ecu
    backup = snapshot(family, kind)
    window._ms42_ms43_show_storage(backup, "Edited backup")
    combo = window._ms42_ms43_storage_layout
    if family == "MS43":
        other = "retained" if kind == "eeprom" else "eeprom"
        combo.setCurrentIndex(next(i for i in range(combo.count()) if combo.itemData(i) == (family, other)))
    selection = combo.currentData()
    status = window._ms42_ms43_storage_status.text()
    monkeypatch.setattr(diagnostics, "read_snapshot", lambda *a, **k: pytest.fail("No combined read"))
    monkeypatch.setattr(storage, "read_storage", lambda *a, **k: pytest.fail("No memory backup read"))

    window._ms42_ms43_data_read.click()

    assert worker_runner.results[0][0]
    assert worker_runner.progress[-1] == (1, 1, "Identity / history")
    assert "retained_raw" not in window._ms42_ms43_data_snapshot
    assert window._ms42_ms43_storage_snapshot == backup
    assert window._ms42_ms43_storage_source == "Edited backup"
    assert combo.currentData() == selection
    assert window._ms42_ms43_storage_status.text() == status
    assert window._ms42_ms43_data_save.isEnabled()
    assert window._ms42_ms43_storage_save.isEnabled()
    assert window._ms42_ms43_storage_view.isEnabled()
    assert not any(command == 0x91 or command == 6 and 0x4000 <= int.from_bytes(args[:4], "big") < 0x8000
                   for _, command, args in link.calls)


@pytest.mark.parametrize("family,kind,label", [
    ("MS42", "retained", "Read Flash Journal"),
    ("MS43", "retained", "Read Flash Banks"),
    ("MS43", "eeprom", "Read EEPROM"),
])
@pytest.mark.parametrize("slow", [False, True])
def test_selected_memory_and_force_ds2_reach_storage_worker(
        window, worker_runner, monkeypatch, family, kind, label, slow):
    ecu = connect(window, family)
    combo = window._ms42_ms43_storage_layout
    combo.setCurrentIndex(next(i for i in range(combo.count()) if combo.itemData(i) == (family, kind)))
    window.chk_force_slow_ds2.setChecked(slow)
    expected = snapshot(family, kind)
    calls = []

    def read(current, selected, *, force_slow, log_fn, progress_cb):
        assert current is ecu
        calls.append((selected, force_slow))
        progress_cb(len(expected["data"]), len(expected["data"]), label)
        return expected

    monkeypatch.setattr(storage, "read_storage", read)
    monkeypatch.setattr(diagnostics, "read_identity", lambda *a, **k: pytest.fail("No report read"))
    assert window._ms42_ms43_storage_read.text() == label
    window._ms42_ms43_storage_read.click()
    assert calls == [(kind, slow and kind == "retained")]
    assert worker_runner.results[0][0]
    assert window._ms42_ms43_data_snapshot is None
    assert window._ms42_ms43_storage_snapshot == expected
    assert window._ms42_ms43_storage_status.text().startswith("Loaded backup:")
    assert not window._ms42_ms43_data_save.isEnabled()


def test_selected_flash_does_not_change_loaded_eeprom_save_or_inspect(
        window, monkeypatch, tmp_path):
    connect(window, "MS43")
    loaded = snapshot()
    window._ms42_ms43_show_storage(loaded, "EEPROM backup")
    combo = window._ms42_ms43_storage_layout
    combo.setCurrentIndex(next(i for i in range(combo.count()) if combo.itemData(i) == ("MS43", "retained")))
    assert window._ms42_ms43_storage_read.text() == "Read Flash Banks"
    assert "EEPROM" in window._ms42_ms43_storage_status.text()
    assert not window._ms42_ms43_storage_write.isEnabled()
    assert window._ms42_ms43_storage_save.isEnabled()
    assert window._ms42_ms43_storage_view.isEnabled()

    saved = tmp_path / "loaded-eeprom.bin"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a: (str(saved), ""))
    window._ms42_ms43_storage_save.click()
    assert saved.read_bytes() == loaded["data"]
    inspected = []
    monkeypatch.setattr(QDialog, "exec_", lambda dialog: inspected.append(dialog._snapshot) or QDialog.Rejected)
    window._ms42_ms43_storage_view.click()
    assert inspected == [loaded]
    assert combo.currentData() == ("MS43", "retained")


@pytest.mark.parametrize("family", ["MS42", "MS43"])
def test_legacy_combined_report_roundtrip_preserves_embedded_flash(
        window, monkeypatch, tmp_path, family):
    ecu, _ = identity_ecu(family)
    info = diagnostics.read_identity(ecu)
    raw = snapshot(family, "retained")["data"]
    info.update(retained_raw=raw, retained_address=diagnostics.RETAINED_ADDRESS,
                retained_provenance=diagnostics.RETAINED_PROVENANCE)
    original = diagnostics.encode_snapshot(info)
    source, target = tmp_path / "legacy.json", tmp_path / "saved.json"
    source.write_text(original)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a: (str(source), ""))
    window._ms42_ms43_data_load.click()
    assert window._ds2 is None
    assert window._ms42_ms43_storage_snapshot["data"] == raw
    assert window._ms42_ms43_storage_layout.currentData() == (family, "retained")
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a: (str(target), ""))
    window._ms42_ms43_data_save.click()
    assert json.loads(target.read_text()) == json.loads(original)
    assert window._ms42_ms43_storage_snapshot["data"] == raw


def test_clear_loaded_data_clears_both_sections_and_returns_to_ms41(window):
    window._ds2 = SimpleNamespace(family="MS41.2")
    window._ms42_ms43_show_data({"family": "MS43"})
    window._ms42_ms43_show_storage(snapshot(), "Offline EEPROM")
    assert window._ms42_ms43_data_close.text() == "Clear Loaded Data"
    assert window._ms41_eeprom_body.isHidden()
    window._ms42_ms43_data_close.click()
    assert window._ms42_ms43_data_snapshot is None
    assert window._ms42_ms43_storage_snapshot is None
    assert window._ms42_ms43_storage_source == ""
    assert window._ms42_ms43_data_text.toPlainText() == ""
    assert not window._ms41_eeprom_body.isHidden()
    assert window._ms42_ms43_data_body.isHidden()
    assert window.tabs.tabText(window._eeprom_tab_index).strip() == "EEPROM"


@pytest.mark.parametrize("focused", [False, True])
def test_memory_selector_wheel_cannot_change_read_target(window, focused):
    connect(window, "MS43")
    window.tabs.setCurrentIndex(window._eeprom_tab_index)
    window.show()
    QApplication.processEvents()
    combo = window._ms42_ms43_storage_layout
    assert isinstance(combo, ConfigComboBox)
    combo.setFocus() if focused else combo.clearFocus()
    before = combo.currentData()
    for delta in (-120, 120, -360):
        wheel(combo, delta=delta)
        assert combo.currentData() == before
        assert window._ms42_ms43_storage_read.text() == "Read EEPROM"
