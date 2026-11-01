"""Real desktop worker contracts for family features; all ECU traffic is fake."""

from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from PyQt5.QtWidgets import QApplication, QFileDialog, QMessageBox

import ms42_ms43_config as config
import ms42_ms43_diagnostics as diagnostics
import ms42_ms43_image as images
from tests import test_ms42_ms43_desktop as desktop
from tests.test_ms42_ms43_desktop import connect
from tests.test_ms42_ms43_diagnostics import IDENT, aif_image, aif_record, record
from tests.test_ms42_ms43_image import synthetic_image
from tests.test_ms42_ms43_snapshot_baud import snapshot_peer as snapshot_peer

window = desktop.window
worker_runner = desktop.worker_runner


@pytest.mark.parametrize("family", ["MS42", "MS43"])
@pytest.mark.parametrize("slow", [False, True])
def test_identity_button_uses_existing_worker_without_reading_storage(
        window, worker_runner, snapshot_peer, family, slow):
    from tests.test_ms42_ms43_snapshot_baud import bulk_reads

    connect(window, family)
    ecu, peer = snapshot_peer(family)
    window._ds2 = ecu
    window.chk_force_slow_ds2.setChecked(slow)
    window._ms42_ms43_data_read.click()
    assert worker_runner.results[0][0]
    assert window._ms42_ms43_data_snapshot["vin"] == "WBAAM31010FG12345"
    assert "retained_raw" not in window._ms42_ms43_data_snapshot
    assert window._ms42_ms43_storage_snapshot is None
    assert worker_runner.progress[-1] == (1, 1, "Identity / history")
    assert bulk_reads(peer) == []
    assert {baud for baud, _, _ in peer.requests} == {9600}
    assert all(command in (0, 6, 0x0B, 0x0D) for _, command, _ in peer.requests)
    assert ecu.baud == 9600 and ecu.rate_confirmed


@pytest.mark.parametrize("family,count", [("MS42", 26), ("MS43", 49)])
def test_family_config_read_uses_small_span_and_worker_contract(window, worker_runner, monkeypatch, family, count):
    from ms42_ms43_config_patches import patches_for
    from tests.test_ms42_ms43_config_patches import patch_image

    ecu = connect(window, family)
    calls = []
    full = patch_image(family)
    def read(address, length):
        calls.append((address, length))
        offset = address - 0x80000 if family == "MS43" and address >= 0x90000 else address
        return full[offset:offset + length]
    ecu.read_memory = read
    monkeypatch.setattr(diagnostics, "read_learned_config", lambda _: {"Air conditioning": True})
    window._on_config_read_ecu()
    assert worker_runner.results[0][0]
    expected = ([(0x4816D, 1), (0x481EF, 96), (0x48287, 28), (0x48319, 4), (0x483AA, 37)] if family == "MS42"
                else [(0x70A83, 1), (0x70B16, 21), (0x70BBB, 1), (0x70C4E, 8), (0x70CD7, 38), (0x70DEF, 34)])
    expected += [(offset + 0x80000 if family == "MS43" and 0x10000 <= offset < 0x70000 else offset, len(original))
                 for patch in patches_for(family, window._ecu_cal_id) for offset, original, _ in patch.entries]
    assert calls == expected
    assert len(window._ms42_ms43_config_combos) == count
    assert window.btn_config_write_ecu.isEnabled()
    assert "Air conditioning: Yes" in window._ms42_ms43_config_detected.text()


@pytest.mark.parametrize("family", ["MS42", "MS43"])
@pytest.mark.parametrize("slow", [False, True])
@pytest.mark.parametrize("feature_name", [
    "Electrical cooling fan", "EVAP purge operation",
    "Purge-valve electrical diagnosis", "Automatic purge-flow diagnosis",
])
def test_config_write_uses_selected_reader_then_existing_tune_writer(
        window, worker_runner, monkeypatch, family, slow, feature_name):
    ecu = connect(window, family)
    partial = images.extract_calibration(synthetic_image(family))
    window._ms42_ms43_config_values(config.read_config(partial))
    feature = next(item for item in config.features_for(family, window._ecu_cal_id) if item.name == feature_name)
    label, value = feature.options[0]
    window._ms42_ms43_config_combos[feature.name].setCurrentText(label)
    window.chk_force_slow_ds2.setChecked(slow)
    lifecycle = []

    @contextmanager
    def transfer(*, force_slow, log_fn, use_agent):
        assert force_slow is slow
        lifecycle.append("enter")
        yield SimpleNamespace(read_tune=lambda **_: partial)
        lifecycle.append("exit")

    ecu.read_transfer = transfer
    ecu.read_tune = lambda **_: pytest.fail("The context reader owns the RAM link")
    writes = []
    monkeypatch.setattr(window, "_ms42_ms43_write", lambda *args: writes.append(args))
    window._on_config_write_ecu()
    expected = bytearray(partial)
    expected[feature.byte] = value
    assert lifecycle == ["enter", "exit"]
    assert writes == [(bytes(expected), "ECU configuration", "tune")]


def test_ms42_paired_crc_has_one_control_and_partial_files_cannot_change_it(window, monkeypatch, tmp_path):
    from ms42_ms43_config_patches import read_config
    from tests.test_ms42_ms43_config_patches import patch_image

    connect(window, "MS42")
    full = images.correct_checksums(patch_image("MS42"), "MS42")[0]
    window._ms42_ms43_config_values(read_config(full))
    assert "Calibration CRC" not in window._ms42_ms43_config_combos
    combo = window._ms42_ms43_config_combos["Calibration checksum bypass"]
    assert combo.isEnabled() and combo.currentText() == "Disabled"
    source = tmp_path / "partial.bin"
    source.write_bytes(images.extract_calibration(full))
    window._load_config_from_path(str(source))
    combo = window._ms42_ms43_config_combos["Calibration checksum bypass"]
    assert not combo.isEnabled() and combo.currentText() == config.FULL_IMAGE_REQUIRED
    source = tmp_path / "full.bin"
    source.write_bytes(full)
    window._load_config_from_path(str(source))
    combo = window._ms42_ms43_config_combos["Calibration checksum bypass"]
    assert combo.isEnabled()
    combo.setCurrentText("Enabled")
    # Copying tune/live values cannot clear the separately selected full-image choice.
    window._ms42_ms43_config_values(read_config(images.extract_calibration(full)))
    assert combo.currentText() == "Enabled"
    assert not window.btn_config_write_ecu.isEnabled()
    output = tmp_path / "configured.bin"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: (str(output), ""))
    window._on_config_save()
    expected = config.apply_config(full, {"Calibration CRC": "Disabled"})
    expected = images.correct_checksums(expected, "MS42")[0]
    assert output.read_bytes() == expected and source.read_bytes() == full


@pytest.mark.parametrize("family", ["MS42", "MS43"])
def test_config_file_save_preserves_other_bytes_and_returns_to_ecu(window, monkeypatch, tmp_path, family):
    connect(window, family)
    full, _ = images.correct_checksums(synthetic_image(family), family)
    source = tmp_path / "source.bin"
    source.write_bytes(full)
    output = tmp_path / "configured.bin"
    window._load_config_from_path(str(source))
    feature = config.features_for(family, window._ecu_cal_id)[0]
    label, value = feature.options[0]
    window._ms42_ms43_config_combos[feature.name].setCurrentText(label)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: (str(output), ""))
    window._on_config_save()
    expected = config.apply_config(full, {feature.name: label})
    expected, _ = images.correct_checksums(expected, family)
    assert output.read_bytes() == expected
    assert source.read_bytes() == full
    window._on_config_close()
    assert window._config_data is None and window.btn_config_read_ecu.isEnabled()


@pytest.mark.parametrize("saved_context", [True, False])
@pytest.mark.parametrize("name,label", [
    ("Electrical cooling fan", "Not fitted"), ("Calibration monitor checksum", "Disabled"),
])
def test_partial_config_save_reuses_original_checksum_context(window, monkeypatch, tmp_path, saved_context, name, label):
    full = images.correct_checksums(synthetic_image("MS43"), "MS43")[0]
    partial = images.extract_calibration(full)
    context = full[0x6FDB8:0x6FDE0]
    if saved_context:
        window._backup_mgr.add_data(partial, "read.bin", checksum_context=context)
    source = tmp_path / "tune.bin"
    source.write_bytes(partial)
    full_path = tmp_path / "full.bin"
    full_path.write_bytes(full)
    output = tmp_path / "configured.bin"
    window._load_config_from_path(str(source))
    window.chk_config_fix.setChecked(True)
    window._ms42_ms43_config_combos[name].setCurrentText(label)
    calls, prompts = [], []
    lookup = window._backup_mgr.checksum_context
    monkeypatch.setattr(window._backup_mgr, "checksum_context",
                        lambda data: calls.append(bytes(data)) or lookup(data))
    monkeypatch.setattr(QFileDialog, "getOpenFileName",
                        lambda *args: prompts.append(args) or (str(full_path), ""))
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: (str(output), ""))
    window._on_config_save()
    expected = config.apply_config(partial, {name: label})
    assert expected != partial  # Lookup must happen against the original, not edited bytes.
    expected = images.correct_checksums(expected, "MS43", context)[0]
    assert output.read_bytes() == expected
    assert images.checksum_status(expected, "MS43", context)["cal"] is True
    assert calls == [partial] and len(prompts) == (0 if saved_context else 1)
    assert source.read_bytes() == window._config_data == partial


def test_identity_failure_is_partial_and_snapshot_roundtrip_uses_existing_worker(window, worker_runner, monkeypatch, tmp_path):
    ecu = connect(window, "MS43")
    ecu.read_supply_voltages = lambda: {"battery": 12.9}
    partial = {"family": "MS43", "warnings": ["History unavailable"]}
    monkeypatch.setattr(diagnostics, "read_identity", lambda _: partial)
    window._ms42_ms43_info()
    assert window._info_labels["Calibration ID"].text() == "430069"
    assert ("History unavailable", "warn") in worker_runner.logs
    raw = aif_image(aif_record())
    info = dict(family="MS43", hardware_id="11143053", software_id="430069",
                aif_raw=raw, raw_ident=IDENT, aif_address=0xF800)
    info.update(diagnostics.decode_aif_history(raw, "MS43"))
    window._ms42_ms43_show_data(info)
    path = tmp_path / "identity.json"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a: (str(path), ""))
    window._ms42_ms43_save_data()
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a: (str(path), ""))
    window._ms42_ms43_load_data()
    assert "WBAAM31010FG12345" in window._ms42_ms43_data_text.toPlainText()
    assert window._ms42_ms43_data_snapshot["aif_raw"] == raw


def test_adaptation_values_and_partial_failure_remain_visible(window, worker_runner, monkeypatch):
    connect(window, "MS42")
    logs = []
    monkeypatch.setattr(window, "_log", lambda *args: logs.append(args))
    result = {"ecu_id": "MS42", "values": [("Fuel", 1.234, "%"), ("Idle", None, "rpm")],
              "errors": ["Idle response unavailable"]}
    monkeypatch.setattr("ms42_ms43_live.read_adaptations", lambda _, **kwargs: result)
    window._on_read_adaptations()
    assert window._ms42_ms43_adapt_values.item(0, 1).text() == "1.23"
    assert window._ms42_ms43_adapt_values.item(1, 1).text() == "—"
    assert "some values unavailable" in window.lbl_adapt_status.text()
    assert ("Idle response unavailable", "warn") in logs


@pytest.mark.parametrize("family", ["MS42", "MS43"])
def test_family_features_do_not_compete_with_interrupted_flash(window, family):
    ecu = connect(window, family)
    assert window.btn_live_start.isEnabled()
    assert window.btn_roughness_start.isEnabled()
    assert len(window.actuator_buttons) == 22
    ecu.write_state = SimpleNamespace(destructive_started=True, completed=False)
    window._set_all_buttons_enabled(True)
    assert not window.btn_live_start.isEnabled()
    assert not window.btn_roughness_start.isEnabled()
    assert not window.btn_read_adaptations.isEnabled()
    assert not window.btn_config_read_ecu.isEnabled()
    assert not window._ms42_ms43_data_read.isEnabled()
    assert all(not b.isEnabled() for b in window.actuator_buttons.values())


@pytest.mark.parametrize("family", ["MS42", "MS43"])
@pytest.mark.parametrize("size", [(1000, 740), (1366, 768), (1920, 1080)])
def test_new_family_pages_keep_width_and_aligned_config_cells(window, family, size):
    connect(window, family)
    window.resize(*size)
    window.show()
    app = QApplication.instance()
    for index in range(window.tabs.count()):
        if window.tabs.tabText(index).strip() not in {"ECU Config", "Adaptations", "ECU Data"}:
            continue
        window.tabs.setCurrentIndex(index)
        for _ in range(4):
            app.processEvents()
        page = window.tabs.widget(index)
        assert page.width() <= window.width()
    table = window._ms42_ms43_config_table
    assert abs(table.columnWidth(0) - table.columnWidth(1)) <= 1


def test_family_config_tooltips_restore_for_ms41(window):
    before = window.btn_config_write_ecu.toolTip()
    connect(window, "MS43")
    assert window.btn_config_write_ecu.toolTip() != before
    window._ds2 = None
    window._update_config_buttons()
    assert window.btn_config_write_ecu.toolTip() == before


def test_unknown_software_does_not_disable_existing_flash_or_known_diagnostics(window):
    connect(window, "MS43")
    window._ecu_cal_id = "430056"
    window._ms42_ms43_controls()
    assert not window.btn_config_read_ecu.isEnabled()
    assert window.btn_read_full.isEnabled() and window.btn_write_full.isEnabled()
    assert window.btn_read_adaptations.isEnabled()
    assert "not mapped" in window._ms42_ms43_config_detected.text()


def test_loaded_configuration_keeps_unsaved_choices_when_connection_changes(window, tmp_path):
    connect(window, "MS43")
    path = tmp_path / "config.bin"
    path.write_bytes(synthetic_image("MS43"))
    window._load_config_from_path(str(path))
    combo = window._ms42_ms43_config_combos["Electrical cooling fan"]
    combo.setCurrentText("Fitted")
    window._ds2 = None
    window._update_config_buttons()
    assert window._ms42_ms43_config_combos["Electrical cooling fan"] is combo
    assert combo.currentText() == "Fitted" and combo.isEnabled()
    connect(window, "MS42")
    assert window._ms42_ms43_config_combos["Electrical cooling fan"] is combo
    assert combo.currentText() == "Fitted" and window.btn_config_save.isEnabled()


def test_invalid_replacement_file_keeps_family_configuration_and_unsaved_choices(window, monkeypatch, tmp_path):
    source = tmp_path / "ms43.bin"
    source.write_bytes(synthetic_image("MS43"))
    window._load_config_from_path(str(source))
    before = window._ms42_ms43_config_context()
    combo = window._ms42_ms43_config_combos["Electrical cooling fan"]
    combo.setCurrentText("Fitted")
    invalid = tmp_path / "invalid.bin"
    invalid.write_bytes(b"invalid")
    warnings = []
    monkeypatch.setattr(QMessageBox, "critical", lambda *args: warnings.append(args))
    window._load_config_from_path(str(invalid))
    window._update_config_buttons()
    assert warnings
    assert window._ms42_ms43_config_context() == before
    assert window._config_data == source.read_bytes()
    assert combo.currentText() == "Fitted" and combo.isEnabled()
    assert window._ms42_ms43_config_combos["Electrical cooling fan"] is combo


def test_loaded_snapshot_can_be_inspected_and_closed_with_ms41_connected(window):
    window._ds2 = SimpleNamespace(family="MS41.2")
    window._ms42_ms43_show_data({"family": "MS43"})
    assert not window._ms42_ms43_data_body.isHidden()
    assert window._ms41_eeprom_body.isHidden()
    assert not window._ms42_ms43_data_read.isEnabled()
    window._ms42_ms43_close_data()
    assert window._ms42_ms43_data_body.isHidden()
    assert not window._ms41_eeprom_body.isHidden()


@pytest.mark.parametrize("family", ["MS42", "MS43"])
def test_dtc_memory_selection_clear_export_and_actual_worker_contract(
        window, worker_runner, monkeypatch, tmp_path, family):
    connect(window, family)
    stored = diagnostics.decode_fault_record(record(8), family)
    shadow = diagnostics.decode_fault_record(record(18, shadow=True), family, memory="shadow")
    result = diagnostics.FaultMemory(family, (stored,), (shadow,))
    calls = []
    monkeypatch.setattr(diagnostics, "read_fault_memory", lambda _: calls.append("read") or result)
    monkeypatch.setattr(diagnostics, "clear_fault_memory", lambda _: calls.append("clear"))
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    window._on_read_dtc()
    assert window.dtc_table.item(0, 0).text() == stored.code_hex
    assert stored.description in window.dtc_detail.toPlainText()
    window.cb_dtc_memory.setCurrentIndex(1)
    assert window.dtc_table.item(0, 0).text() == shadow.code_hex
    assert "Debounce" in window.dtc_detail.toPlainText()
    path = tmp_path / "faults.txt"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a: (str(path), ""))
    window._on_export_dtc()
    assert "Stored (1)" in path.read_text(encoding="utf-8")
    assert "Shadow (1)" in path.read_text(encoding="utf-8")
    window._on_clear_dtc()
    assert calls == ["read", "clear", "read"]
    assert all(ok for ok, _ in worker_runner.results)


@pytest.mark.parametrize("family", ["MS42", "MS43"])
@pytest.mark.parametrize("clear", [False, True])
def test_changing_dtc_read_clears_stale_results_and_reports_clear_separately(
        window, worker_runner, monkeypatch, family, clear):
    from ms42_ms43 import MS42MS43ECU
    from tests.test_ms42_ms43 import Link, reply

    connect(window, family)
    window._fault_memories = {
        "stored": [diagnostics.decode_fault_record(record(8), family)],
        "shadow": [diagnostics.decode_fault_record(record(18, shadow=True), family, memory="shadow")],
    }
    window.cb_dtc_memory.setCurrentIndex(1)
    window._show_dtc_memory()
    assert window.dtc_detail.toPlainText()

    def respond(command, args):
        assert window._fault_memories == {} and window._dtcs == []
        assert window.dtc_table.rowCount() == 0 and not window.dtc_detail.toPlainText()
        if command == 5:
            return reply()
        assert command == 4
        if args == b"\x00":
            return reply(b"\x02" + b"\x00" * 4)
        return reply((b"\x02" if args == b"\x01" else b"\x01") + record())

    link = Link(respond)
    window._ds2 = MS42MS43ECU(link, family, raw_ident=IDENT)
    errors, notices = [], []
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.Yes)
    monkeypatch.setattr(QMessageBox, "critical", lambda *a: errors.append(a))
    monkeypatch.setattr(QMessageBox, "information", lambda *a: notices.append(a))
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a: pytest.fail("No stale report to export"))
    window._ms42_ms43_read_faults(clear=clear)
    expected = ([(5, b"")] if clear else []) + [(4, bytes((index,)))
                                               for _ in range(3) for index in (0, 1, 2)]
    assert [call[1:] for call in link.calls] == expected
    assert worker_runner.results[0][0] is clear
    assert not window.cb_dtc_memory.isEnabled()
    assert window._fault_memories == {} and window._dtcs == []
    assert window.dtc_table.rowCount() == 0 and not window.dtc_detail.toPlainText()
    if clear:
        assert not errors
        assert "Clear accepted — confirmation read failed:" in window.lbl_dtc_count.text()
    else:
        assert len(errors) == 1
        assert window.lbl_dtc_count.text() == "Read failed — no current results"
    window._on_export_dtc()
    assert notices[0][2] == "Read DTCs first."


def test_rejected_dtc_clear_is_not_reported_as_accepted_or_retried(window, worker_runner, monkeypatch):
    from ms42_ms43 import MS42MS43ECU
    from tests.test_ms42_ms43 import Link, reply

    connect(window, "MS42")
    link = Link(lambda *_: reply(status=0xA2))
    window._ds2 = MS42MS43ECU(link, "MS42")
    errors = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.Yes)
    monkeypatch.setattr(QMessageBox, "critical", lambda *a: errors.append(a))
    window._on_clear_dtc()
    assert link.calls == [(0x12, 5, b"")]
    assert not worker_runner.results[0][0]
    assert errors[0][1] == "Fault Clear Failed"
    assert window.lbl_dtc_count.text() == "Clear not confirmed"
    assert not any("Clear accepted" in text for text, _ in worker_runner.logs)


def test_cancelled_dtc_clear_keeps_previous_results(window, worker_runner, monkeypatch):
    connect(window, "MS43")
    stored = diagnostics.decode_fault_record(record(), "MS43")
    window._fault_memories = {"stored": [stored], "shadow": []}
    window._show_dtc_memory()
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.No)
    window._on_clear_dtc()
    assert worker_runner.results == []
    assert window._dtcs == [stored] and window.dtc_table.rowCount() == 1
    assert stored.description in window.dtc_detail.toPlainText()


def test_dtc_clear_acceptance_survives_read_error_without_message(window, worker_runner, monkeypatch):
    connect(window, "MS43")
    calls = []
    monkeypatch.setattr(diagnostics, "clear_fault_memory", lambda _: calls.append("clear"))
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.Yes)

    def fail_read(_ecu):
        calls.append("read")
        raise TimeoutError()

    monkeypatch.setattr(diagnostics, "read_fault_memory", fail_read)
    window._on_clear_dtc()
    assert calls == ["clear", "read"]
    assert worker_runner.results == [(True, (None, "TimeoutError"))]
    assert window.lbl_dtc_count.text() == "Clear accepted — confirmation read failed: TimeoutError"


@pytest.mark.parametrize("family", ["MS42", "MS43"])
def test_live_start_capture_and_stop_uses_real_family_poller(window, qtbot, monkeypatch, family):
    from ms42_ms43 import MS42MS43ECU
    from tests.test_ms42_ms43 import Link, reply

    connect(window, family)
    payload = bytearray(41)
    payload[0:2] = (800).to_bytes(2, "big")
    payload[(23 if family == "MS42" else 43) - 3] = 128
    window._ds2 = MS42MS43ECU(Link(lambda *_: reply(payload)), family)
    window._live_selected_ids = ["N", "VB"]
    window.cb_live_mode.setCurrentIndex(window.cb_live_mode.findData("ds2"))
    window.spin_interval.setValue(0)
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: warnings.append(a))
    window._on_live_start()
    try:
        assert not warnings and window._poller is not None
        qtbot.waitUntil(lambda: window._poller.csv_rows > 0, timeout=5000)
    finally:
        window._on_live_stop()
    assert window._poller is None and window.btn_live_start.isEnabled()
    assert window.live_view.series["Engine Speed"]["values"][-1] == 800
    voltage_name = "Battery Voltage (after main relay)" if family == "MS43" else "Battery Voltage"
    assert window.live_view.specs[voltage_name]["maximum"] == 20


def test_family_logger_menu_remains_accurate_after_reset(window):
    connect(window, "MS43")
    window._refresh_logger_definition()
    assert window.btn_logger_definition.text() == "Logger: MS43 BMW"
    assert "MS43 BMW" in window.act_logger_definition.text()
    assert "MS41" not in window.btn_logger_definition.toolTip()


def test_new_pages_at_larger_font_render_without_horizontal_overflow(window, tmp_path):
    connect(window, "MS43")
    font = window.font()
    font.setPointSizeF(font.pointSizeF() * 1.5)
    window.setFont(font)
    window.resize(1366, 900)
    window.show()
    app = QApplication.instance()
    for title in ("ECU Config", "ECU Data", "Adaptations"):
        index = next(i for i in range(window.tabs.count()) if window.tabs.tabText(i).strip() == title)
        window.tabs.setCurrentIndex(index)
        for _ in range(5):
            app.processEvents()
        if title == "ECU Config":
            assert window._config_scroll.horizontalScrollBar().maximum() == 0
        assert window.grab().save(str(tmp_path / (title.replace(" ", "-") + ".png")))
