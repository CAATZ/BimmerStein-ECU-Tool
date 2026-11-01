"""Family file tools remain local and use the existing desktop workflow."""

from types import SimpleNamespace

import pytest
from PyQt5.QtWidgets import QFileDialog, QMessageBox

import ms42_ms43_image as images
from tests.test_ms42_ms43_desktop import connect, window as window
from tests.test_ms42_ms43_image import synthetic_image


@pytest.mark.parametrize("family", ("MS42", "MS43"))
@pytest.mark.parametrize("partial", (False, True))
def test_bins_config_opens_selected_family_file_without_connection(window, family, partial):
    image = images.correct_checksums(synthetic_image(family), family)[0]
    if partial:
        image = images.extract_calibration(image)
    entry = window._backup_mgr.add_data(image, "configuration.bin")
    window._refresh_backup_table()
    window.backup_table.selectRow(0)
    window._set_backup_buttons_enabled()
    assert window._ds2 is None and window.act_backup_config.isEnabled()
    window.act_backup_config.trigger()
    assert window.tabs.currentIndex() == window._config_tab_index
    assert window._config_data == image
    assert window._backup_mgr.read_data(entry.filename, entry.sha256) == image
    assert not window.btn_config_write_ecu.isEnabled()


def test_analyzer_definition_picker_accepts_xml_and_xdf(window, monkeypatch):
    dialogs = []
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args:
                        dialogs.append(args) or ("", ""))
    window._on_load_definition()
    assert len(dialogs) == 1
    assert "*.xml" in dialogs[0][3] and "*.xdf" in dialogs[0][3]


@pytest.mark.parametrize("family", ("MS42", "MS43"))
def test_standard_serial_rate_display_matches_agent_choice(window, family):
    ecu = connect(window, family)
    ecu.transport = SimpleNamespace(native_fast_capable=False, _ser=SimpleNamespace(parity="E"))
    window._update_high_speed_mode()
    assert window.lbl_high_speed_mode.text() == "RAM transfer: Serial · 9,600 bps"
    ecu.transport.native_fast_capable = True
    window._update_high_speed_mode()
    assert "High-speed:" in window.lbl_high_speed_mode.text()


@pytest.mark.parametrize("family", ("MS42", "MS43"))
def test_standard_serial_read_keeps_ram_confirmation_and_boot_choice(window, monkeypatch, family):
    ecu = connect(window, family)
    ecu.transport = SimpleNamespace(native_fast_capable=False, _ser=SimpleNamespace(parity="E"))
    ecu.read_transfer = lambda **_: pytest.fail("Cancelled read must not enter the ECU")
    prompts = []
    monkeypatch.setattr(QMessageBox, "question", lambda *args:
                        prompts.append(args) or QMessageBox.No)
    window._on_read("tune")
    assert len(prompts) == 1 and "restarts the ECU" in prompts[0][2]
    window._ms42_ms43_boot_controls()
    assert window.chk_bootloader_write.isEnabled()


@pytest.mark.parametrize("family", ("MS42", "MS43"))
def test_analyzer_displays_recorded_identity_and_saved_partial_context(window, family):
    from tests.test_ms42_ms43_offline import VIN, identity_image

    full = images.correct_checksums(identity_image(family), family)[0]
    window._show_analysis(full)
    assert window._analyzer_labels["vin"].text() == VIN
    assert window._analyzer_labels["isn"].text() == "1234"
    partial = images.extract_calibration(full)
    context = full[0x6FDB8:0x6FDE0] if family == "MS43" else None
    window._backup_mgr.add_data(partial, "read.bin", checksum_context=context)
    window._show_analysis(partial)
    assert window._analyzer_labels["checksum"].text() == "VALID"
    assert window._analyzer_labels["vin"].text() == "N/A (tune file)"
    assert window._analyzer_labels["isn"].text() == "N/A (tune file)"


def test_flash_checksum_file_actions_reuse_saved_context(window, monkeypatch, tmp_path):
    full = images.correct_checksums(synthetic_image("MS43"), "MS43")[0]
    partial = images.extract_calibration(full)
    context = full[0x6FDB8:0x6FDE0]
    entry = window._backup_mgr.add_data(partial, "read.bin", checksum_context=context)
    calls, logs = [], []

    def choose(*args):
        calls.append(args[1])
        assert "Context" not in args[1]
        return entry.path, ""

    monkeypatch.setattr(QFileDialog, "getOpenFileName", choose)
    monkeypatch.setattr(window, "_log", lambda message, *_: logs.append(message))
    window._on_check_file()
    assert not any("unavailable" in line.lower() for line in logs)
    assert any("monitor" in line.lower() and "OK" in line for line in logs)
    output = tmp_path / "corrected.bin"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_: (str(output), ""))
    window._on_fix_file()
    assert len(calls) == 2
    assert images.checksum_status(output.read_bytes(), context=context)["cal"] is True
    assert window._backup_mgr.read_data(entry.filename, entry.sha256) == partial
