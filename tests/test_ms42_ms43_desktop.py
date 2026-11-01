"""Offline desktop/storage integration; no serial backend is opened."""
import os
from types import SimpleNamespace
from contextlib import contextmanager, nullcontext

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if os.name == "nt":
    # Offscreen Qt otherwise looks for fonts in its unpopulated bundled directory.
    os.environ.setdefault("QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))

import pytest
from PyQt5.QtCore import QEvent, QSettings
from PyQt5.QtWidgets import QApplication, QInputDialog, QMessageBox, QScrollArea

import backup_manager
import bin_compare
import gui
import ms42_ms43_image as images
import rom_analyzer
from ms42_ms43 import MS42MS43ECU
from tests.test_ms42_ms43 import Link, reply
from tests.test_ms42_ms43_image import synthetic_image


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)
    monkeypatch.setattr(gui.MS41FlashGUI, "_live_settings", staticmethod(lambda: settings))
    monkeypatch.setattr(gui._PanelSplitter, "_settings", lambda _: settings)
    monkeypatch.setattr(gui.MS41FlashGUI, "_refresh_ports", lambda _: None)
    monkeypatch.setattr(backup_manager, "BACKUP_DIR", str(tmp_path / "backups"))
    monkeypatch.setattr(backup_manager, "INDEX_FILE", str(tmp_path / "backups/index.json"))
    monkeypatch.setattr(gui, "LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setattr(gui.DS2Interface, "open", lambda _: pytest.fail("No transport may open"))
    app = qapp
    old_font = app.font()
    gui.configure_application(app)
    window = gui.MS41FlashGUI()
    yield window
    window._ds2 = None
    window.close()
    window.deleteLater()
    QApplication.sendPostedEvents(window, QEvent.DeferredDelete)
    app.processEvents()
    app.setFont(old_font)


def connect(window, family):
    profile = images.PROFILES[family]
    ecu = SimpleNamespace(family=family, profile=profile, baud=9600, uses_d2xx=True)
    ecu.transport = SimpleNamespace(native_fast_capable=True, _ser=None)
    ecu.transfer = lambda **_: nullcontext()
    ecu.read_transfer = lambda **_: nullcontext(ecu)
    window._ds2 = ecu
    window._on_connected({"family": family, "hardware_id": profile.hw_prefix + "53",
                          "software_id": "430069" if family == "MS43" else "0110C6",
                          "vin": ""})
    return ecu


@pytest.fixture
def worker_runner(window, monkeypatch):
    observed = SimpleNamespace(logs=[], progress=[], results=[])

    def run(task, on_success=None, on_failure=None):
        worker = gui.WorkerThread(task)
        worker.log_signal.connect(lambda *args: observed.logs.append(args))
        worker.progress_signal.connect(lambda *args: observed.progress.append(args))
        completed = []
        worker.done_signal.connect(lambda *args: completed.append(args))
        worker.run()  # Exercise the production keyword contract without starting a thread.
        assert len(completed) == 1
        success, result = completed[0]
        observed.results.append((success, result))
        if success:
            if on_success:
                on_success(result)
        elif on_failure:
            on_failure(result)
        else:
            raise result
        return True

    monkeypatch.setattr(window, "_run_task", run)
    return observed


@pytest.mark.parametrize("family", ["MS42", "MS43"])
def test_family_connection_uses_existing_controls_without_ms41_tools(window, family):
    connect(window, family)
    window._set_all_buttons_enabled(True)
    assert window._ecu_variant == family
    assert window.btn_read_full.isEnabled() and window.btn_write_tune.isEnabled()
    assert not window.btn_softbsl_install.isEnabled()
    assert window.btn_read_adaptations.isEnabled()
    assert not window.chk_bootloader_write.isEnabled()
    assert window._auto_transfer_route() == "ms42_ms43"
    assert window.btn_id_read_ecu.text() == "Synchronize EWS…"
    assert window.ews_group.title() == "EWS3.3 Synchronization"
    assert "512 KB" in window.btn_read_full.text()
    assert f"{images.PROFILES[family].partial_size // 1024} KB" in window.btn_write_tune.text()
    assert window._info_field_names["Calibration ID"].text() == "Software ID:"
    assert window._info_labels["BMW Hardware Number"].text() == "—"


@pytest.mark.parametrize("family", ["MS42", "MS43"])
@pytest.mark.parametrize("mode", ["full", "tune"])
@pytest.mark.parametrize("resident", [False, True])
def test_read_archives_family_size_and_provenance(
        window, monkeypatch, worker_runner, family, mode, resident):
    ecu = connect(window, family)
    data, _ = images.correct_checksums(synthetic_image(family), family)
    context = data[0x6FDB8:0x6FDE0]
    if mode == "tune":
        data = images.extract_calibration(data)

    def read(*, log_fn, progress_cb):
        log_fn("Fake read")
        progress_cb(len(data), len(data), "Read")
        return bytearray(data)

    reader = SimpleNamespace(read_source="ECU RAM loader read") if resident else ecu
    setattr(reader, "read_" + mode, read)
    lifecycle = []
    @contextmanager
    def read_transfer(*, force_slow, log_fn, use_agent):
        assert use_agent is not force_slow
        assert force_slow is not resident
        lifecycle.append("enter")
        log_fn("Reader selected")
        try:
            yield reader
        finally:
            lifecycle.append("exit")
    ecu.read_transfer = read_transfer
    ecu.transfer = lambda **_: pytest.fail("Read must use the selected reader")
    memory_calls = []
    def read_memory(address, length):
        memory_calls.append((address, length))
        assert family == "MS43" and mode == "tune"
        assert (address, length) == (0xEFDB8, 40)
        assert lifecycle == ["enter", "exit"]
        lifecycle.append("context")
        return context
    ecu.read_memory = read_memory
    window.chk_force_slow_ds2.setChecked(not resident)
    monkeypatch.setattr(window, "_offer_additional_read_copy", lambda *a: None)
    window._on_read(mode)
    entry = window._backup_mgr.entries[-1]
    assert entry.size == len(data) and entry.variant == family
    assert entry.source == ("ECU RAM loader read" if resident else "ECU diagnostic read")
    assert entry.filename.startswith(family.lower() + "_")
    assert worker_runner.results == [(True, data)]
    assert type(worker_runner.results[0][1]) is bytes
    assert lifecycle == ["enter", "exit"] + (["context"] if family == "MS43" and mode == "tune" else [])
    assert memory_calls == ([(0xEFDB8, 40)] if family == "MS43" and mode == "tune" else [])
    assert ("Reader selected", "info") in worker_runner.logs
    assert ("Fake read", "info") in worker_runner.logs
    assert worker_runner.progress == [(len(data), len(data), "Read")]
    if family == "MS43":
        assert ("Calibration monitor: OK", "info") in worker_runner.logs
    assert entry.cs_ok is True
    assert window.backup_table.item(0, 5).text() == "OK"
    window._backup_mgr = backup_manager.BackupManager()
    window._refresh_backup_table()
    assert window._backup_mgr.entries[0].cs_ok is True
    assert window.backup_table.item(0, 5).text() == "OK"


@pytest.mark.parametrize("context_result", ["mismatch", "timeout", "missing", "short"])
def test_ms43_partial_context_problem_preserves_raw_read(
        window, monkeypatch, worker_runner, context_result):
    ecu = connect(window, "MS43")
    full, _ = images.correct_checksums(synthetic_image("MS43"), "MS43")
    data = images.extract_calibration(full)
    context = bytearray(full[0x6FDB8:0x6FDE0])
    context[0] ^= 1  # A different monitor seed must report MISMATCH, never OK.
    ecu.read_tune = lambda **_: data
    calls = []
    def read_memory(address, length):
        calls.append((address, length))
        if context_result == "timeout":
            raise gui.DS2Error("Context read timed out")
        if context_result == "missing":
            return None
        return bytes(context if context_result == "mismatch" else context[:-1])
    ecu.read_memory = read_memory
    monkeypatch.setattr(window, "_offer_additional_read_copy", lambda *a: None)
    window._on_read("tune")
    assert calls == [(0xEFDB8, 40)]
    assert worker_runner.results == [(True, data)]
    assert len(window._backup_mgr.entries) == 1
    with open(window._backup_mgr.entries[0].path, "rb") as archived:
        assert archived.read() == data
    assert ("Calibration CRC16: OK", "info") in worker_runner.logs
    messages = [message for message, _ in worker_runner.logs]
    if context_result == "mismatch":
        assert "Calibration monitor: MISMATCH" in messages
    else:
        assert any(message.startswith("Calibration monitor: unavailable") for message in messages)
        if context_result == "timeout":
            assert any("Context read timed out" in message and level == "warn"
                       for message, level in worker_runner.logs)
    expected = False if context_result == "mismatch" else None
    label = "INVALID" if context_result == "mismatch" else "UNKNOWN"
    assert window._backup_mgr.entries[0].cs_ok is expected
    assert window.backup_table.item(0, 5).text() == label
    window._backup_mgr = backup_manager.BackupManager()
    window._refresh_backup_table()
    assert window._backup_mgr.entries[0].cs_ok is expected
    assert window.backup_table.item(0, 5).text() == label


@pytest.mark.parametrize("failure", ["read", "restore", "size"])
def test_failed_ms43_partial_read_does_not_request_context_or_archive(window, worker_runner, failure):
    ecu = connect(window, "MS43")
    data = images.extract_calibration(synthetic_image("MS43"))
    def read_tune(**_):
        if failure == "read":
            raise gui.DS2Error("Read cancelled")
        return data[:-1] if failure == "size" else data
    @contextmanager
    def read_transfer(**_):
        yield SimpleNamespace(read_source="ECU RAM loader read", read_tune=read_tune)
        if failure == "restore":
            raise gui.DS2Error("Normal diagnostics did not return")
    ecu.read_transfer = read_transfer
    calls = []
    ecu.read_memory = lambda *args: calls.append(args)
    with pytest.raises((gui.DS2Error, ValueError)):
        window._on_read("tune")
    assert not calls and not window._backup_mgr.entries
    assert worker_runner.results[0][0] is False


@pytest.mark.parametrize("family", ["MS42", "MS43"])
@pytest.mark.parametrize("accepted", [False, True])
def test_ram_read_confirmation_precedes_worker_and_decline_does_no_work(
        window, monkeypatch, worker_runner, family, accepted):
    ecu = connect(window, family)
    ecu.transport = SimpleNamespace(native_fast_capable=True, _ser=SimpleNamespace(parity="E"))
    data = synthetic_image(family)
    ecu.read_full = lambda **_: data
    prompts = []
    def confirm(*args):
        assert not worker_runner.results and not window._backup_mgr.entries
        prompts.append(args)
        return QMessageBox.Yes if accepted else QMessageBox.No
    monkeypatch.setattr(QMessageBox, "question", confirm)
    monkeypatch.setattr(window, "_offer_additional_read_copy", lambda *a: None)
    window._on_read("full")
    assert len(prompts) == 1
    assert "ignition ON" in prompts[0][2] and "engine STOPPED" in prompts[0][2]
    assert "restarts the ECU" in prompts[0][2]
    assert len(worker_runner.results) == int(accepted)
    assert len(window._backup_mgr.entries) == int(accepted)


@pytest.mark.parametrize("reason", ["slow", "software", "parity"])
def test_stock_read_candidates_do_not_show_ram_confirmation(
        window, monkeypatch, worker_runner, reason):
    ecu = connect(window, "MS43")
    ecu.transport = SimpleNamespace(native_fast_capable=True,
                                    _ser=SimpleNamespace(parity="E"))
    window.chk_force_slow_ds2.setChecked(reason == "slow")
    if reason == "software":
        window._ecu_cal_id = "unknown"
    if reason == "parity":
        del ecu.transport._ser.parity
    ecu.read_tune = lambda **_: images.extract_calibration(synthetic_image("MS43"))
    monkeypatch.setattr(QMessageBox, "question", lambda *a: pytest.fail("Stock read must not prompt for RAM entry"))
    monkeypatch.setattr(window, "_offer_additional_read_copy", lambda *a: None)
    window._on_read("tune")
    assert worker_runner.results[0][0]
    assert len(window._backup_mgr.entries) == 1


@pytest.mark.parametrize("family", ["MS42", "MS43"])
@pytest.mark.parametrize("mode", ["full", "tune"])
@pytest.mark.parametrize("backup,verify", [(False, False), (False, True), (True, False)])
@pytest.mark.parametrize("force_slow", [False, True])
def test_write_respects_backup_and_verify_choices(
        window, monkeypatch, worker_runner, family, mode, backup, verify, force_slow):
    ecu = connect(window, family)
    ecu.read_transfer = lambda **_: pytest.fail("Write backup must stay on the stock diagnostic reader")
    full, _ = images.correct_checksums(synthetic_image(family), family)
    data = full if mode == "full" else images.extract_calibration(full)
    calls = []

    def read_full(*, log_fn, progress_cb):
        assert backup, "Backup is off"
        calls.append("backup")
        return full

    def write(image, *, log_fn, progress_cb, verify, use_ram, use_agent, write_boot=False):
        assert use_ram is not force_slow
        assert use_agent is not force_slow
        assert not write_boot
        calls.append((image, verify))
        log_fn("Fake write", "ok")
        progress_cb(len(image), len(image), "Write")

    setattr(ecu, "write_" + mode, write)
    ecu.read_full = read_full
    window.chk_backup_before_write.setChecked(backup)
    window.chk_verify.setChecked(verify)
    window.chk_force_slow_ds2.setChecked(force_slow)
    monkeypatch.setattr(window, "_prewrite_battery_notice", lambda: "12.8 V")
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.Yes)
    completed = []
    monkeypatch.setattr(window, "_show_flash_complete", lambda *a: completed.append(a))
    getattr(window, "_ds2_write_" + mode)(bytearray(data), "test.bin")
    assert calls == (["backup"] if backup else []) + [(data, verify)]
    assert len(window._backup_mgr.entries) == int(backup)
    assert worker_runner.results == [(True, None)]
    assert worker_runner.progress == [(len(data), len(data), "Write")]
    assert ("Fake write", "ok") in worker_runner.logs
    assert len(completed) == 1


@pytest.mark.parametrize("family,expected", [("MS42", "13.05"), ("MS43", "13.00")])
def test_info_worker_and_write_advisory_use_decoded_battery_voltage(window, worker_runner, monkeypatch, family, expected):
    connect(window, family)
    data = bytearray(34 if family == "MS42" else 41)
    data[25 - 3] = 17
    data[(23 if family == "MS42" else 43) - 3] = 128
    ecu = MS42MS43ECU(Link(lambda *_: reply(data)), family)
    window._ds2 = ecu
    info = images.image_identity(synthetic_image(family))
    monkeypatch.setattr("ms42_ms43_diagnostics.read_identity", lambda _: info)
    window._ms42_ms43_info()
    assert worker_runner.results == [(True, info)]
    label = "Battery voltage (after main relay)" if family == "MS43" else "Battery voltage"
    assert (f"{label}: {expected} V", "info") in worker_runner.logs
    if family == "MS43":
        assert ("Ignition supply voltage (KL15): 1.73 V", "info") in worker_runner.logs
    assert window._info_labels["Calibration ID"].text() == info["software_id"]
    assert window._prewrite_battery_notice() == f"{label}: {expected} V"
    assert ecu.transport.calls == [(0x12, 0x0B, b"\x03")] * 2


def test_ms43_short_voltage_response_keeps_write_advisory_nonblocking(window):
    connect(window, "MS43")
    window._ds2 = MS42MS43ECU(Link(lambda *_: reply(bytes(40))), "MS43")
    notice = window._prewrite_battery_notice()
    assert "Battery voltage (after main relay): unavailable" in notice
    assert "Programming remains available" in notice
    assert window._ds2.transport.calls == [(0x12, 0x0B, b"\x03")]


def test_retry_worker_uses_retained_backend_and_reports_completion(window, monkeypatch, worker_runner):
    ecu = connect(window, "MS43")
    ecu.write_state = SimpleNamespace(destructive_started=True, completed=False)

    def retry_write(*, progress_cb, log_fn):
        log_fn("Fake retry")
        progress_cb(1, 1, "Retry")
        ecu.write_state.completed = True
        return "retried"

    ecu.retry_write = retry_write
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.Yes)
    completed = []
    monkeypatch.setattr(window, "_show_flash_complete", lambda *a: completed.append(a))
    window._ms42_ms43_retry()
    assert worker_runner.results == [(True, "retried")]
    assert worker_runner.progress == [(1, 1, "Retry")]
    assert completed[0][0] == "Recovery Complete"


def test_adaptation_worker_resets_selected_group(window, monkeypatch, worker_runner):
    ecu = connect(window, "MS42")
    ecu.adaptation_groups = {"Idle": 1, "Lambda": 2}
    selected = []
    ecu.clear_adaptations = lambda groups: selected.extend(groups)
    monkeypatch.setattr(QInputDialog, "getItem", lambda *a: ("Idle", True))
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.Yes)
    window._ms42_ms43_adaptations()
    assert selected == ["Idle"]
    assert worker_runner.results == [(True, "Idle: reset accepted.")]
    assert window.lbl_adapt_status.text() == "Idle: reset accepted."


@pytest.mark.parametrize("reset", [False, True])
def test_ews_worker_dispatches_and_displays_result(window, monkeypatch, worker_runner, reset):
    ecu = connect(window, "MS43")
    result = {"code": 0, "message": "Fake EWS reset" if reset else "Fake EWS synchronization"}
    setattr(ecu, "reset_ews_start_value" if reset else "sync_ews", lambda: result)
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.Yes)
    window._ms42_ms43_ews(reset=reset)
    assert worker_runner.results == [(True, result)]
    assert window.id_ews_frames.toPlainText() == result["message"]


@pytest.mark.parametrize("clear", [False, True])
def test_fault_worker_reads_or_clears_and_displays_decoded_records(
        window, monkeypatch, worker_runner, clear):
    connect(window, "MS42")
    from ms42_ms43_diagnostics import FaultMemory, decode_fault_record
    record = decode_fault_record(bytes((8, 0x70, 1, 2, 3, 4, 5, 6, 7, 8)), "MS42")
    memory = FaultMemory("MS42", (record,), ())
    calls = []
    monkeypatch.setattr("ms42_ms43_diagnostics.clear_fault_memory", lambda _: calls.append("clear"))
    monkeypatch.setattr("ms42_ms43_diagnostics.read_fault_memory", lambda _: calls.append("read") or memory)
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.Yes)
    window._ms42_ms43_read_faults(clear=clear)
    assert calls == (["clear"] if clear else []) + ["read"]
    assert worker_runner.results == [(True, (memory, None))]
    if clear:
        assert window.lbl_dtc_count.text() == "Clear accepted — 1 stored, 0 shadow fault(s) remain."
    assert window.dtc_table.item(0, 0).text() == "008"
    assert window.dtc_table.item(0, 3).text() == record.status_text
    window.dtc_table.setCurrentCell(0, 0)
    window._ms42_ms43_fault_detail()
    assert "Raw DTC Records" in window.dtc_detail.toPlainText()


def test_worker_preserves_backend_error_and_does_not_archive_failed_read(window, worker_runner):
    ecu = connect(window, "MS43")
    error = ValueError("Fake backend read failed")

    def fail(**kwargs):
        raise error

    ecu.read_full = fail
    with pytest.raises(ValueError, match="Fake backend read failed") as raised:
        window._on_read("full")
    assert raised.value is error
    assert worker_runner.results == [(False, error)]
    assert not window._backup_mgr.entries


def test_resident_read_cancellation_releases_context_without_archiving(window, worker_runner):
    ecu = connect(window, "MS43")
    error = gui.DS2Error("Read cancelled")
    lifecycle = []
    def cancel(*, progress_cb, log_fn):
        progress_cb(64, images.FULL_SIZE, "RAM read")
        raise error
    @contextmanager
    def read_transfer(*, force_slow, log_fn, use_agent):
        assert use_agent is not force_slow
        lifecycle.append("enter")
        try:
            yield SimpleNamespace(read_source="ECU RAM loader read", read_full=cancel)
        finally:
            lifecycle.append("exit")
    ecu.read_transfer = read_transfer
    ecu.read_full = lambda **_: pytest.fail("Cancellation must not start a diagnostic read")
    with pytest.raises(gui.DS2Error, match="Read cancelled") as raised:
        window._on_read("full")
    assert raised.value is error
    assert worker_runner.results == [(False, error)]
    assert lifecycle == ["enter", "exit"]
    assert not window._backup_mgr.entries


@pytest.mark.parametrize("family", ["MS42", "MS43"])
def test_read_runs_real_thread_and_restores_gui_state(window, monkeypatch, qtbot, family):
    ecu = connect(window, family)
    data, _ = images.correct_checksums(synthetic_image(family), family)
    reader = SimpleNamespace(read_source="ECU RAM loader read", read_full=lambda **kwargs: data)
    ecu.read_transfer = lambda **_: nullcontext(reader)
    ecu.read_full = lambda **_: pytest.fail("Must read from the selected RAM reader")
    errors = []
    monkeypatch.setattr(QMessageBox, "critical", lambda *args: errors.append(args))
    monkeypatch.setattr(window, "_offer_additional_read_copy", lambda *args: None)
    window._on_read("full")
    worker = window._worker
    assert worker is not None and window._task_busy
    try:
        qtbot.waitUntil(lambda: not window._task_busy, timeout=5000)
    finally:
        assert worker.wait(5000)
    assert not errors
    assert len(window._backup_mgr.entries) == 1
    assert window._backup_mgr.entries[0].size == len(data)
    assert window._backup_mgr.entries[0].source == "ECU RAM loader read"
    assert window.btn_read_full.isEnabled()
    assert window.progress_bar.isHidden()


def test_interrupted_flash_keeps_link_when_disconnect_declined(window, monkeypatch):
    ecu = connect(window, "MS42")
    ecu.write_state = SimpleNamespace(destructive_started=True, completed=False)
    ecu.close = lambda: pytest.fail("Pending session must remain open")
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.No)
    window._disconnect()
    assert window._ds2 is ecu
    window._set_all_buttons_enabled(True)
    assert not window.btn_read_full.isEnabled()
    assert window.btn_native_recovery.isEnabled()
    window._update_diag_actions()
    window._update_coding_actions()
    window._set_backup_buttons_enabled()
    assert not window.btn_read_dtc.isEnabled()
    assert not window.btn_read_module_coding.isEnabled()
    assert not window.btn_backup_ecu.isEnabled()


def test_declined_close_preserves_interrupted_session(window, monkeypatch):
    from PyQt5.QtGui import QCloseEvent
    ecu = connect(window, "MS43")
    ecu.write_state = SimpleNamespace(destructive_started=True, completed=False)
    ecu.close = lambda: pytest.fail("Pending session must remain open")
    prompts = []
    monkeypatch.setattr(QMessageBox, "question", lambda *args: prompts.append(args) or QMessageBox.No)
    event = QCloseEvent()
    window.closeEvent(event)
    assert not event.isAccepted() and window._ds2 is ecu
    assert len(prompts) == 1


def test_common_read_and_diagnostics_use_family_backend(window):
    ecu = connect(window, "MS42")
    ecu.read_tune = lambda **_: b"calibration"
    ecu.transport = object()
    assert window._read_image_auto("tune", lambda *a: None, lambda *a: None) == b"calibration"
    assert window._diagnostic_link() is ecu.transport


def test_ms41_labels_restore_after_other_family(window):
    connect(window, "MS43")
    window._restore_ms41_labels()
    assert "256 KB" in window.btn_read_full.text()
    assert "24 KB" in window.btn_write_tune.text()
    assert window._info_field_names["Calibration ID"].text() == "Calibration ID:"
    assert window.btn_id_read_ecu.text() == "Read ISN"


@pytest.mark.parametrize("family", ["MS42", "MS43"])
@pytest.mark.parametrize("size", [(980, 740), (1600, 900), (1920, 1080)])
def test_family_pages_fit_and_flash_buttons_stay_aligned(window, family, size):
    connect(window, family)
    window.resize(*size)
    window.show()
    app = QApplication.instance()
    for index in range(window.tabs.count()):
        if window.tabs.tabText(index).strip() not in {"Flash", "ECU Info", "VIN / EWS", "Partial / Full"}:
            continue
        window.tabs.setCurrentIndex(index)
        for _ in range(8):
            app.processEvents()
        page = window.tabs.widget(index)
        scroll = page if isinstance(page, QScrollArea) else page.findChild(QScrollArea)
        assert scroll.horizontalScrollBar().maximum() == 0, window.tabs.tabText(index)
        assert scroll.verticalScrollBar().maximum() == 0, window.tabs.tabText(index)
    assert abs(window.btn_read_full.width() - window.btn_read_tune.width()) <= 1
    assert window.btn_read_full.x() == window.btn_write_full.x()


@pytest.mark.parametrize("family", ["MS42", "MS43"])
def test_catalog_analyzer_and_comparison_use_family_layout(tmp_path, monkeypatch, family):
    data, _ = images.correct_checksums(synthetic_image(family), family)
    profile = images.PROFILES[family]
    partial = data[profile.calibration_offset:profile.calibration_offset + profile.partial_size]
    monkeypatch.setattr(backup_manager, "BACKUP_DIR", str(tmp_path / "backups"))
    monkeypatch.setattr(backup_manager, "INDEX_FILE", str(tmp_path / "backups/index.json"))
    manager = backup_manager.BackupManager()
    entry = manager.add_data(data, "full.bin")
    assert entry.file_type == "Full ROM" and entry.variant == family and entry.cs_ok
    result = rom_analyzer.analyze(data)
    assert result.variant == family and result.cs_ok and not result.params
    first, second, base, _ = bin_compare._comparison_bytes(data, partial)
    assert first == second == partial and base == profile.calibration_offset
    assert bin_compare.analyze_image(partial)["calibration_variant"] == family
    if family == "MS43":
        assert bin_compare.analyze_image(partial)["checksum"]["cal"] is None
        entry = manager.add_data(partial, "partial.bin")
        assert entry.cs_ok is None and entry.checksum_label == "UNKNOWN"


@pytest.mark.parametrize("family", ["MS42", "MS43"])
def test_analyzer_uses_family_identity_and_validity_then_restores_ms41(window, monkeypatch, family):
    data, _ = images.correct_checksums(synthetic_image(family), family)
    with monkeypatch.context() as patch:
        patch.setattr(gui.identity, "decode_identity", lambda _: pytest.fail("MS41 identity decoder used"))
        patch.setattr(gui.MS41ECU, "vin_from_image", lambda _: pytest.fail("MS41 VIN decoder used"))
        window._show_analysis(data)
        assert window._analyzer_field_names["ecu_id"].text() == "Hardware ID:"
        assert window._analyzer_field_names["cal_id"].text() == "Software ID:"
        assert window._analyzer_labels["cal_id"].text() == images.image_identity(data)["software_id"]
        assert window._analyzer_labels["vin"].text() == "Not programmed / not found"
        assert "Unavailable" in window._analyzer_labels["isn"].text()
        assert window._analyzer_labels["checksum"].text() == "VALID"
        partial = images.extract_calibration(data)
        window._show_analysis(partial)
        assert window._analyzer_labels["checksum"].text() == ("UNKNOWN" if family == "MS43" else "VALID")

    window._show_analysis(bytes(gui.MS41ECU.TUNE_SIZE))
    assert window._analyzer_field_names["ecu_id"].text() == "ECU ID:"
    assert window._analyzer_field_names["cal_id"].text() == "CAL ID:"
    assert "N/A" in window._analyzer_labels["isn"].text()


def test_analyzer_reports_invalid_family_identity_without_uncaught_error(window, monkeypatch):
    data = synthetic_image("MS43")
    data[images.PROFILES["MS43"].sw_offset] = 0xFF
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args[2]))
    window._show_analysis(data)
    assert warnings and "ASCII" in warnings[0]


def test_analyzer_known_checksum_failure_wins_over_unknown_and_warns_of_mixed_ids():
    data, _ = images.correct_checksums(synthetic_image("MS43"), "MS43")
    data = bytearray(data)
    data[0x100] ^= 1  # Known boot CRC mismatch.
    data[0x6FDE2:0x6FDE4] = b"\x00\x00"  # Invalid program descriptor count.
    data[0x70008:0x7000E] = b"430099"
    result = rom_analyzer.analyze(data)
    assert result.checksum == "mismatch"
    assert result.bootloader_ok is False
    assert any("unavailable" in detail for detail in result.cs_details)
    assert any("identity mismatch" in warning for warning in result.warnings)
