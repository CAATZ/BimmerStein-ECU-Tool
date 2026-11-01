"""Reported desktop states, using real controls and entirely offline workers."""

import hashlib

import pytest
from PyQt5.QtWidgets import QApplication, QDialog

import ms42_ms43_diagnostics as diagnostics
from tests.test_ms42_ms43_desktop import connect, window as window, worker_runner as worker_runner
from tests.test_ms42_ms43_image import synthetic_image
from tests.test_ms42_ms43_storage_desktop import snapshot


def choices(window):
    combo = window._ms42_ms43_storage_layout
    return [combo.itemData(i) for i in range(combo.count())]


def test_storage_choices_follow_connection_but_preserve_offline_imports(window):
    assert len(choices(window)) == 3
    connect(window, "MS43")
    assert choices(window) == [("MS43", "eeprom"), ("MS43", "retained")]
    assert window._ms42_ms43_storage_layout.currentData() == ("MS43", "eeprom")
    connect(window, "MS42")
    assert choices(window) == [("MS42", "retained")]
    assert window._ms42_ms43_storage_layout.currentData() == ("MS42", "retained")
    connect(window, "MS42")
    assert window._ms42_ms43_storage_read.isEnabled()
    window._ds2 = None
    window._ms42_ms43_update_data()
    assert len(choices(window)) == 3


def test_other_family_backup_remains_inspectable_without_becoming_live_target(window, monkeypatch):
    connect(window, "MS43")
    original = snapshot("MS42", "retained")
    window._ms42_ms43_show_storage(original, "Offline MS42 file")
    assert choices(window) == [("MS43", "eeprom"), ("MS43", "retained")]
    assert window._ms42_ms43_storage_snapshot == original
    assert window._ms42_ms43_storage_view.isEnabled()
    assert not window._ms42_ms43_storage_write.isEnabled()
    monkeypatch.setattr(window, "_run_task", lambda *a, **k: pytest.fail("No ECU access"))
    seen = []
    monkeypatch.setattr(QDialog, "exec_", lambda dialog: seen.append(dialog.inspection.toPlainText()))
    window._ms42_ms43_view_storage()
    assert "MS42 0110C6" in seen[0]


def test_reported_ff_backup_is_identified_as_flash_not_eeprom(window, monkeypatch):
    from ms42_ms43_storage import make_snapshot

    connect(window, "MS43")
    raw = b"\xFF" * 16384
    # Exact digest visible in the user's report: this is truly all-FF data.
    assert hashlib.sha256(raw).hexdigest() == "0fbba07a833d4dcfc7024eaf313661a0ba8f80a05c6d29b8801c612e10e60dee"
    original = make_snapshot("MS43", "retained", raw, software_id="430069")
    window._ms42_ms43_show_storage(original, "ECU Data snapshot")
    assert "only FF bytes" in window._ms42_ms43_storage_status.text()
    assert "Select EEPROM, then Read EEPROM" in window._ms42_ms43_storage_status.text()
    assert window._ms42_ms43_storage_status.toolTip().endswith(hashlib.sha256(raw).hexdigest())
    seen = []
    monkeypatch.setattr(QDialog, "exec_", lambda dialog: seen.append(dialog.inspection.toPlainText()))
    window._ms42_ms43_view_storage()
    assert "All sixteen slots are erased" in seen[0]
    assert "0000  FF FF FF FF" in seen[0]
    assert window._ms42_ms43_storage_snapshot["data"] == raw
    window._ms42_ms43_storage_layout.setCurrentIndex(0)
    assert window._ms42_ms43_storage_read.isEnabled()
    assert not window._ms42_ms43_storage_write.isEnabled()  # Raw flash cannot become an EEPROM target.


@pytest.mark.parametrize("family", ("MS42", "MS43"))
def test_config_read_button_reaches_worker_after_fresh_connection(window, worker_runner, monkeypatch, family):
    from tests.test_ms42_ms43_config_patches import patch_image

    ecu = connect(window, family)
    image = patch_image(family)
    calls = []
    def read(address, length):
        calls.append((address, length))
        offset = address - 0x80000 if family == "MS43" and address >= 0x90000 else address
        return image[offset:offset + length]
    ecu.read_memory = read
    monkeypatch.setattr(diagnostics, "read_learned_config", lambda _: {})
    window._set_all_buttons_enabled(True)
    assert window.btn_config_read_ecu.isEnabled()
    window.btn_config_read_ecu.click()
    assert sum(length for _, length in calls) == (184 if family == "MS42" else 125)
    assert worker_runner.results[0][0]


@pytest.mark.parametrize("family", ("MS42", "MS43"))
def test_file_mode_explains_disabled_read_and_close_reenables_it(window, tmp_path, family):
    connect(window, family)
    path = tmp_path / "loaded.bin"
    original = synthetic_image(family)
    path.write_bytes(original)
    window._load_config_from_path(str(path))
    assert not window.btn_config_read_ecu.isEnabled()
    assert window.btn_config_copy_ecu.isEnabled()
    assert "File mode" in window._ms42_ms43_config_group.title()
    assert "Close the loaded file" in window.btn_config_read_ecu.toolTip()
    assert window._config_data == original
    window.btn_config_close.click()
    assert window.btn_config_read_ecu.isEnabled()
    assert "ECU settings" in window._ms42_ms43_config_group.title()
    assert path.read_bytes() == original


def test_config_table_uses_remaining_height_and_restores_ms41_spacing(window):
    connect(window, "MS43")
    window.tabs.setCurrentIndex(window._config_tab_index)
    window.resize(1000, 800)
    window.show()
    QApplication.processEvents()
    group = window._ms42_ms43_config_group
    layout = group.parentWidget().layout()
    assert layout.stretch(layout.indexOf(group)) == 1
    assert layout.stretch(layout.count() - 1) == 0
    assert group.height() > 250
    window._ds2 = None
    window._update_config_buttons()
    assert layout.stretch(layout.indexOf(group)) == 0
    assert layout.stretch(layout.count() - 1) == 1
