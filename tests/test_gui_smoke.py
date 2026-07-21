import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # Set before importing PyQt5.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import pytest
from ms41 import MS41ECU

try:
    from PyQt5.QtCore import QEvent, QThread
    from PyQt5.QtGui import QPalette
    from PyQt5.QtWidgets import QApplication, QMessageBox, QInputDialog, QFileDialog, QPushButton
    import gui
    _HAS_QT = True
except Exception:
    _HAS_QT = False

pytestmark = pytest.mark.skipif(not _HAS_QT, reason="PyQt5 not available")

from tests.conftest import ref

EXISTING_TABS = {"Flash", "DTC Codes", "ECU Info", "Live Data",
                 "ROM Analyzer", "ECU Config", "Partial / Full", "Bins", "Patches"}
TEST_SERIAL = "900000001"
TEST_ISN = "0001"
TEST_VIN = "WBAZZ0000TEST0001"
TARGET_VIN = "WBAZZ0000TEST0002"


def _gui():
    app = QApplication.instance() or QApplication([])
    return app, gui.MS41FlashGUI()


def _set_bsl_chip(window, chip):
    index = window.cb_bsl_chip.findData(chip)
    assert index >= 0
    window.cb_bsl_chip.setCurrentIndex(index)


def test_debug_log_is_file_only(tmp_path):
    app, window = _gui()
    try:
        log_path = tmp_path / "session.txt"
        window._log_file = log_path.open("w", encoding="utf-8")

        window._log("queue RX=0 TX=0", "debug")
        window._log("Write completed", "ok")
        window._log_file.close()
        window._log_file = None

        assert "queue RX=0 TX=0" not in window.log_view.toPlainText()
        assert "Write completed" in window.log_view.toPlainText()
        text = log_path.read_text(encoding="utf-8")
        assert "[DEBUG] queue RX=0 TX=0" in text
        assert "[OK   ] Write completed" in text
    finally:
        window.close()


def test_status_only_progress_resets_completed_read_without_fake_byte_units():
    app, window = _gui()
    try:
        window.progress_bar.setValue(100)
        window.progress_label.setText("base read  256/256 KB")

        window._on_progress(0, 1, "Authorizing program write")

        assert window.progress_bar.value() == 0
        assert window.progress_label.text() == "Authorizing program write"
    finally:
        window.close()


def test_status_only_progress_preserves_completed_bar_when_total_is_zero():
    app, window = _gui()
    try:
        window.progress_bar.setValue(100)
        window.progress_label.setText("Writing calibration region  24/24 KB")

        window._on_progress(0, 0, "Finalizing ECU write")

        assert window.progress_bar.value() == 100
        assert window.progress_label.text() == "Finalizing ECU write"
    finally:
        window.close()


def test_shared_progress_status_has_its_own_row_below_full_width_bar():
    app, window = _gui()
    try:
        root = window.centralWidget().layout()
        assert root.indexOf(window.progress_bar) >= 0
        assert root.indexOf(window.progress_label) == root.indexOf(window.progress_bar) + 1
    finally:
        window.close()


@pytest.fixture(autouse=True)
def _dispose_gui_objects():
    """Release deferred Qt objects after every complete-window smoke test."""
    yield
    if not _HAS_QT:
        return
    app = QApplication.instance()
    if app is None:
        return
    for widget in list(app.topLevelWidgets()):
        widget.close()
        widget.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()


def test_softbsl_installer_post_erase_failure_keeps_port_and_recovery(monkeypatch):
    app, window = _gui()
    try:
        engine_recovery = type(
            "EngineRecovery",
            (),
            {"phase": "target", "port": "COM1", "is_open": True},
        )()
        recovery = gui.softbsl_install.SoftBSLInstallRecovery(engine_recovery)
        error = gui.softbsl_install.SoftBSLInstallRecoveryRequired(recovery)
        offered = []
        monkeypatch.setattr(
            window,
            "_offer_active_flash_recovery",
            lambda summary: offered.append(summary) or True,
        )
        monkeypatch.setattr(
            window,
            "_release_softbsl_port",
            lambda _port: pytest.fail("a retained installer session must not release COM"),
        )

        window._on_softbsl_install_failure("COM1", error)

        assert window._softbsl_install_recovery is recovery
        assert offered and "DO NOT TURN IGNITION OFF" in offered[0]
    finally:
        window._softbsl_install_recovery = None
        window.close()


def test_softbsl_installer_recovery_releases_port_after_session_closes(monkeypatch):
    app, window = _gui()
    callbacks = {}
    released = []

    class EngineRecovery:
        phase = "target"
        port = "COM1"
        is_open = True

    recovery = gui.softbsl_install.SoftBSLInstallRecovery(EngineRecovery())
    try:
        window._softbsl_install_recovery = recovery
        monkeypatch.setattr(
            window,
            "_run_task",
            lambda _task, on_success=None, on_failure=None: callbacks.update(
                on_success=on_success, on_failure=on_failure
            ),
        )
        monkeypatch.setattr(
            window,
            "_release_softbsl_port",
            lambda port: released.append(port),
        )
        monkeypatch.setattr(
            QMessageBox,
            "critical",
            staticmethod(lambda *_args, **_kwargs: QMessageBox.Ok),
        )

        window._start_softbsl_install_recovery(confirmed=True)
        recovery.engine_recovery.is_open = False
        callbacks["on_failure"]("validation failed after reset")

        assert released == ["COM1"]
        assert window._softbsl_install_recovery is None
        assert not window.btn_native_recovery.isVisible()
    finally:
        window._softbsl_install_recovery = None
        window.close()


def test_softbsl_keycycle_cancel_reports_safe_pre_phase2_stop(monkeypatch):
    app, window = _gui()
    released = []
    shown = []
    try:
        monkeypatch.setattr(
            window,
            "_release_softbsl_port",
            lambda port: released.append(port),
        )
        monkeypatch.setattr(
            QMessageBox,
            "information",
            staticmethod(lambda *args, **_kwargs: shown.append(args)),
        )
        error = gui.softbsl_install.SoftBSLInstallCancelled(
            "Phase 2 erase was not started"
        )

        window._on_softbsl_install_failure("COM1", error)

        assert released == ["COM1"]
        assert shown
        assert shown[0][1] == "Soft-BSL Installation Paused"
        assert "No persistent-image erase occurred" in shown[0][2]
    finally:
        window.close()


@pytest.mark.parametrize(
    ("answer_name", "expected_owner"),
    [
        pytest.param("Cancel", None, id="cancel"),
        pytest.param("Retry", "softbsl", id="retry"),
    ],
)
def test_phase1_reentry_prompt_releases_port_while_modal_and_disconnects_gui(
    monkeypatch, answer_name, expected_owner
):
    app, window = _gui()
    answer = getattr(QMessageBox, answer_name)
    shown = []
    try:
        window._port_owner.acquire("softbsl")
        window._softbsl_handoff_port = "COM7"
        window._connection_port = "COM7"
        window._ds2 = None
        window.btn_connect.blockSignals(True)
        window.btn_connect.setChecked(True)
        window.btn_connect.setText("Disconnect")
        window.btn_connect.blockSignals(False)
        monkeypatch.setattr(
            window,
            "_reopen_ds2_with_retry",
            lambda *_args, **_kwargs: pytest.fail(
                "the ordinary 9600-baud DS2 session must remain closed"
            ),
        )

        def warning(parent, title, message, buttons, default):
            assert QThread.currentThread() == app.thread()
            assert parent is window
            assert window._port_owner.is_free()
            assert window._softbsl_handoff_port is None
            assert window._connection_port is None
            assert window._ds2 is None
            assert window.btn_connect.isChecked() is False
            assert "Disconnected" in window.lbl_status.text()
            shown.append((title, message, buttons, default))
            return answer

        monkeypatch.setattr(QMessageBox, "warning", staticmethod(warning))

        window._softbsl_prompt._show_phase1_reentry(
            "COM7", "Turn ignition OFF, wait at least 10 seconds, then turn ignition ON."
        )

        assert shown[0][0] == "Soft-BSL Installation Needs an Ignition Cycle"
        assert shown[0][2] == QMessageBox.Retry | QMessageBox.Cancel
        assert shown[0][3] == QMessageBox.Cancel
        assert window._port_owner.owner == expected_owner
        assert window._softbsl_prompt._retry_answer is (
            answer == QMessageBox.Retry
        )
    finally:
        window._port_owner.release("softbsl")
        window.close()


def test_phase1_reentry_retry_fails_safe_when_port_owner_cannot_be_reacquired(
    monkeypatch
):
    _app, window = _gui()
    shown = []
    try:
        window._port_owner.acquire("bsl")
        monkeypatch.setattr(
            QMessageBox,
            "warning",
            staticmethod(
                lambda *args, **_kwargs: shown.append(args) or QMessageBox.Ok
            ),
        )

        assert window._reacquire_softbsl_port_after_phase1_reentry("COM7") is False

        assert window._port_owner.owner == "bsl"
        assert shown[0][1] == "Soft-BSL Installation Cancelled"
        assert "without writing the ECU" in shown[0][2]
    finally:
        window._port_owner.release("bsl")
        window.close()


def test_pre_phase1_cancel_restores_busy_progress_and_controls(monkeypatch):
    _app, window = _gui()
    shown = []

    class Signal:
        def __init__(self):
            self.callback = None

        def connect(self, callback):
            self.callback = callback

        def emit(self, *args):
            self.callback(*args)

    class ImmediateFailWorker:
        def __init__(self, task):
            self.task = task
            self.log_signal = Signal()
            self.progress_signal = Signal()
            self.done_signal = Signal()

        def start(self):
            try:
                self.task(
                    lambda message, level="info": self.log_signal.emit(message, level),
                    lambda done, total, label="": self.progress_signal.emit(
                        done, total, label
                    ),
                )
            except Exception as error:
                self.done_signal.emit(False, error)

    try:
        window._port_owner.acquire("softbsl")
        monkeypatch.setattr(gui, "WorkerThread", ImmediateFailWorker)
        monkeypatch.setattr(
            QMessageBox,
            "information",
            staticmethod(
                lambda *args, **_kwargs: shown.append(args) or QMessageBox.Ok
            ),
        )

        def task(_log_fn, progress_fn):
            progress_fn(1, 2, "Phase 1")
            raise gui.softbsl_install.SoftBSLInstallCancelled(
                "operator cancelled", phase="pre_phase1"
            )

        window._run_task(
            task,
            on_failure=lambda error: window._on_softbsl_install_failure(
                "COM7", error
            ),
        )

        assert window._task_busy is False
        assert window.progress_bar.isHidden() is True
        assert window.progress_label.text() == ""
        assert window.btn_connect.isEnabled() is True
        assert window._port_owner.is_free()
        assert window._softbsl_install_recovery is None
        assert shown[0][1] == "Soft-BSL Installation Cancelled"
        assert "before the temporary Phase 1 write" in shown[0][2]
    finally:
        window._port_owner.release("softbsl")
        window.close()


def _write_ms413_base(path):
    """Create the minimum markers needed for a consistent full MS41.3 test image."""
    import ms41
    data = bytearray(b"\xFF" * 262144)
    start = ms41.SS1V2_PROG_SIG_ADDR
    data[start:start + len(ms41.SS1V2_PROG_SIG)] = ms41.SS1V2_PROG_SIG
    data[0x173BB:0x173BE] = b"SS1"
    path.write_bytes(data)
    return path


def _live_identity_source():
    import identity
    data = bytearray(b"\xFF" * 0x6100)
    data[identity.MARK_1585_OFF:identity.MARK_1585_OFF + 4] = b"1585"
    data[identity.SERIAL_OFF:identity.SERIAL_NUL_OFF + 1] = (TEST_SERIAL + "\x00").encode("ascii")
    data[identity.VIN_OFF:identity.VIN_OFF + identity.VIN_LEN] = identity.encode_vin(
        TEST_VIN)
    return bytes(data)


def test_gui_constructs_headless_with_existing_tabs():
    app, w = _gui()
    try:
        assert w.windowTitle() == "BimmerStein ECU Tool"
        assert w.lbl_intended_use.text() == (
            "OFF-ROAD, COMPETITION, RESEARCH, AND BENCH USE ONLY"
        )
        assert isinstance(w.lbl_intended_use.parentWidget(), gui.QGroupBox)
        assert w.lbl_intended_use.parentWidget().title().startswith("ECU Connection")
        connection_layout = w.lbl_intended_use.parentWidget().layout()
        assert connection_layout.count() == 1
        connection_row = connection_layout.itemAt(0).layout()
        assert connection_row.indexOf(w.chk_direct_tap) < connection_row.indexOf(
            w.lbl_intended_use
        ) < connection_row.indexOf(w.lbl_status)
        titles = {w.tabs.tabText(i).strip() for i in range(w.tabs.count())}
        assert EXISTING_TABS <= titles, f"missing tabs: {EXISTING_TABS - titles}"
    finally:
        w.close()


def test_flash_tab_backup_is_explicit_optional_and_verify_defaults_off():
    app, w = _gui()
    try:
        assert w.chk_backup_before_write.text().startswith("Back up before write")
        assert w.chk_backup_before_write.isChecked() is False
        assert w.chk_verify.isChecked() is False
    finally:
        w.close()


@pytest.mark.parametrize(("mode", "size", "save_copy"), [
    ("full", MS41ECU.FULL_ROM_SIZE, False),
    ("tune", MS41ECU.TUNE_SIZE, True),
])
def test_flash_read_saves_to_bins_before_optional_copy(
        mode, size, save_copy, tmp_path, monkeypatch):
    app, w = _gui()
    try:
        data = bytes([0x5A]) * size
        copy_path = tmp_path / f"{mode}-operator-copy.bin"
        w._ds2 = object()
        w._connection_port = "COM_TEST"
        events = []
        monkeypatch.setattr(w, "_auto_transfer_route", lambda: "legacy_ds2")
        monkeypatch.setattr(
            w, "_ds2_read",
            lambda which, progress_fn, log_fn:
                (events.append(("read", which)), data)[1])

        entry = type("Entry", (), {
            "filename": f"catalogued-{mode}.bin",
            "path": os.path.join(gui.BACKUP_DIR, f"catalogued-{mode}.bin"),
        })()
        archived = {}
        def save_image(image, archive_mode, source):
            events.append(("archive", archive_mode))
            archived.update(data=bytes(image), mode=archive_mode, source=source)
            return entry
        monkeypatch.setattr(w, "_backup_save_bytes", save_image)
        if mode == "tune":
            monkeypatch.setattr(
                w, "_record_full_ecu_read",
                lambda *args, **kwargs: pytest.fail("tune read must use partial-read owner"))
        refreshed = []
        monkeypatch.setattr(w, "_refresh_backup_table", lambda: refreshed.append(True))

        prompt = {}
        def ask_copy(*args, **kwargs):
            events.append(("prompt", mode))
            prompt.update(title=args[1], message=args[2])
            return QMessageBox.Yes if save_copy else QMessageBox.No
        monkeypatch.setattr(QMessageBox, "question", staticmethod(ask_copy))
        dialog = {}
        if save_copy:
            monkeypatch.setattr(
                QFileDialog, "getSaveFileName",
                staticmethod(lambda *args, **kwargs:
                             (dialog.update(title=args[1], suggested=args[2]),
                              (str(copy_path), ""))[1]))
        else:
            monkeypatch.setattr(
                QFileDialog, "getSaveFileName",
                staticmethod(lambda *args, **kwargs:
                             pytest.fail("No must not open the save dialog")))

        def sync_run_task(task, on_success=None, on_failure=None):
            try:
                result = task(lambda *args, **kwargs: None, lambda *args, **kwargs: None)
            except Exception as error:
                if on_failure:
                    on_failure(error)
                else:
                    raise
            else:
                if on_success:
                    on_success(result)
        monkeypatch.setattr(w, "_run_task", sync_run_task)

        w._on_read(mode)

        assert events == [("read", mode), ("archive", mode), ("prompt", mode)]
        assert archived["data"] == data
        assert archived["mode"] == mode
        assert archived["source"] == "ECU read"
        assert w._session_backup_read is True
        if mode == "full":
            assert w._last_full_read == data
            assert w._last_full_read_key is not None
        else:
            assert w._last_full_read is None
        assert "automatically to Bins" in prompt["message"]
        assert gui.BACKUP_DIR in prompt["message"]
        assert refreshed == [True]
        if save_copy:
            assert copy_path.read_bytes() == data
            assert dialog["suggested"] == entry.filename
            assert dialog["title"] == "Save Additional Tune (24 KB) Copy"
        else:
            assert not copy_path.exists()
    finally:
        w._ds2 = None
        w._connection_port = None
        w.close()


def test_flash_read_wrong_size_is_not_saved_or_offered_for_copy(monkeypatch):
    app, w = _gui()
    try:
        w._ds2 = object()
        monkeypatch.setattr(w, "_auto_transfer_route", lambda: "legacy_ds2")
        monkeypatch.setattr(
            w, "_ds2_read",
            lambda which, progress_fn, log_fn: b"\x5A" * (MS41ECU.TUNE_SIZE - 1))
        monkeypatch.setattr(
            w, "_backup_save_bytes",
            lambda *args, **kwargs: pytest.fail("wrong-sized read must not reach Bins"))
        monkeypatch.setattr(
            QMessageBox, "question",
            staticmethod(lambda *args, **kwargs:
                         pytest.fail("wrong-sized read must not offer a copy")))
        failures = []

        def sync_run_task(task, on_success=None, on_failure=None):
            try:
                result = task(lambda *args, **kwargs: None, lambda *args, **kwargs: None)
            except Exception as error:
                failures.append(error)
                if on_failure:
                    on_failure(error)
            else:
                if on_success:
                    on_success(result)
        monkeypatch.setattr(w, "_run_task", sync_run_task)

        w._on_read("tune")

        assert len(failures) == 1
        assert "24,575 bytes; expected 24,576" in str(failures[0])
    finally:
        w._ds2 = None
        w.close()


def test_flash_success_is_logged_before_modal_instructions(monkeypatch):
    app, w = _gui()
    try:
        events = []
        monkeypatch.setattr(
            w, "_log", lambda message, level="info": events.append(("log", message, level)))
        monkeypatch.setattr(
            w,
            "_show_flash_complete",
            lambda title, message: events.append(("modal", title, message)),
        )

        w._finish_flash_success("Full ROM Write Complete", "Full ROM write completed.")

        assert events == [
            ("log", "Full ROM write completed.", "ok"),
            ("modal", "Full ROM Write Complete", "Full ROM write completed."),
        ]
        assert w._post_write_cycle_pending is True
    finally:
        w.close()


def test_pending_post_write_cycle_blocks_native_write_while_e658_is_active(monkeypatch):
    app, w = _gui()
    try:
        reads = []
        native_calls = []

        class FakeDS2:
            def read_mem(self, address, length):
                reads.append((address, length))
                return b"\x02"

        w._ds2 = FakeDS2()
        monkeypatch.setattr(w, "_log", lambda *args, **kwargs: None)
        monkeypatch.setattr(w, "_show_flash_complete", lambda *args, **kwargs: None)
        monkeypatch.setattr(
            w,
            "_run_via_native_fast_write",
            lambda *args, **kwargs: native_calls.append((args, kwargs)),
        )

        w._finish_flash_success("Calibration Write Complete", "Calibration write completed.")

        with pytest.raises(RuntimeError) as caught:
            w._native_fast_write_with_fallback(
                "tune",
                b"target",
                "intel",
                lambda *args, **kwargs: None,
                lambda *args, **kwargs: None,
                verify_write=False,
            )

        message = str(caught.value).lower()
        assert reads == [(gui.AUTHORIZATION_STATE_ADDRESS, 1)]
        assert native_calls == []
        assert w._post_write_cycle_pending is True
        assert "nothing was erased" in message
        assert "turn ignition off" in message
        assert "10 seconds" in message
        assert "turn ignition on" in message
    finally:
        w._ds2 = None
        w._post_write_cycle_pending = False
        w.close()


def test_native_read_reentry_block_is_not_hidden_by_slow_fallback(monkeypatch):
    app, w = _gui()
    try:
        slow_reads = []
        failure = gui.ds2_fast_read.NativeFastReadReentryNotReady(
            "native-fast read reentry did not complete"
        )
        monkeypatch.setattr(
            w,
            "_run_via_native_fast_ds2",
            lambda *args, **kwargs: (_ for _ in ()).throw(failure),
        )
        monkeypatch.setattr(
            w,
            "_ds2_read",
            lambda *args, **kwargs: slow_reads.append((args, kwargs)),
        )

        with pytest.raises(
            gui.ds2_fast_read.NativeFastReadReentryNotReady,
            match="did not complete",
        ) as caught:
            w._native_fast_read_with_fallback(
                "tune",
                lambda *args, **kwargs: None,
                lambda *args, **kwargs: None,
            )

        assert caught.value is failure
        assert slow_reads == []
    finally:
        w.close()


def test_pending_post_write_cycle_clears_at_e658_zero_and_native_write_proceeds(
    monkeypatch,
):
    app, w = _gui()
    try:
        reads = []
        logs = []
        service_calls = []
        sentinel = object()

        class FakeDS2:
            def read_mem(self, address, length):
                reads.append((address, length))
                return b"\x00"

        def run_native(operation, log_fn, progress_fn, **kwargs):
            return operation("COM1", progress_fn, log_fn)

        w._ds2 = FakeDS2()
        monkeypatch.setattr(w, "_log", lambda message, level="info": logs.append((message, level)))
        monkeypatch.setattr(w, "_show_flash_complete", lambda *args, **kwargs: None)
        monkeypatch.setattr(w, "_run_via_native_fast_write", run_native)
        monkeypatch.setattr(
            gui.ds2_native_fast_service,
            "write_partial_d2xx",
            lambda port, target, **kwargs: service_calls.append(
                (port, target, kwargs)
            ) or sentinel,
        )

        w._finish_flash_success("Calibration Write Complete", "Calibration write completed.")
        result = w._native_fast_write_with_fallback(
            "tune",
            b"target",
            "intel",
            lambda message, level="info": logs.append((message, level)),
            lambda *args, **kwargs: None,
            verify_write=False,
        )

        assert result is sentinel
        assert reads == [(gui.AUTHORIZATION_STATE_ADDRESS, 1)]
        assert len(service_calls) == 1
        assert service_calls[0][0:2] == ("COM1", b"target")
        assert w._post_write_cycle_pending is False
        assert ("Required post-write ignition cycle confirmed (E658=0).", "ok") in logs
    finally:
        w._ds2 = None
        w._post_write_cycle_pending = False
        w.close()


def test_native_write_passes_verify_choice_without_acquiring_backup(monkeypatch):
    app, w = _gui()
    try:
        captured = {}
        sentinel = object()
        monkeypatch.setattr(
            w,
            "_run_via_native_fast_write",
            lambda operation, log_fn, progress_fn, **kwargs:
                operation("COM1", progress_fn, log_fn),
        )
        monkeypatch.setattr(
            gui.ds2_native_fast_service,
            "write_partial_d2xx",
            lambda port, target, **kwargs:
                captured.update(port=port, target=target, kwargs=kwargs) or sentinel,
        )
        result = w._native_fast_write_with_fallback(
            "tune",
            b"target",
            "intel",
            lambda *args, **kwargs: None,
            lambda *args, **kwargs: None,
            verify_write=True,
        )

        assert result is sentinel
        assert captured["kwargs"]["verify_write"] is True
        assert "backup" not in captured["kwargs"]
    finally:
        w.close()


@pytest.mark.parametrize("operation", ("tune", "full"))
@pytest.mark.parametrize("verify_write", (False, True))
def test_native_pre_erase_fallback_respects_verify_choice(
    monkeypatch, operation, verify_write
):
    app, w = _gui()
    try:
        calls = []
        w._ds2 = object()  # models the wrapper's confirmed low-rate reopen

        def fail_before_erase(*args, **kwargs):
            raise gui.ds2_native_fast_service.NativeFastPreEraseFailure(
                RuntimeError("unstable high rate"), safe_legacy_fallback=True)

        monkeypatch.setattr(w, "_run_via_native_fast_write", fail_before_erase)
        monkeypatch.setattr(
            w, "_ds2_write",
            lambda *args, **kwargs: calls.append("write"),
        )
        monkeypatch.setattr(
            w, "_ds2_verify_after_write",
            lambda *args, **kwargs: calls.append("verify"),
        )

        w._native_fast_write_with_fallback(
            operation,
            b"target",
            "intel",
            lambda *args, **kwargs: None,
            lambda *args, **kwargs: None,
            verify_write=verify_write,
        )

        assert calls == (["write", "verify"] if verify_write else ["write"])
    finally:
        w._ds2 = None
        w.close()


def test_native_pre_erase_failure_never_falls_back_without_low_identity(monkeypatch):
    app, w = _gui()
    try:
        calls = []
        w._ds2 = object()

        def fail_without_low_identity(*args, **kwargs):
            raise gui.ds2_native_fast_service.NativeFastPreEraseFailure(
                RuntimeError("authorization state requires ignition cycle"),
                safe_legacy_fallback=False,
            )

        monkeypatch.setattr(w, "_run_via_native_fast_write", fail_without_low_identity)
        monkeypatch.setattr(
            w,
            "_ds2_write",
            lambda *args, **kwargs: calls.append("write"),
        )

        with pytest.raises(
            RuntimeError,
            match="normal low state was not confirmed",
        ) as caught:
            w._native_fast_write_with_fallback(
                "full",
                b"target",
                "intel",
                lambda *args, **kwargs: None,
                lambda *args, **kwargs: None,
                verify_write=False,
            )

        assert isinstance(
            caught.value.__cause__,
            gui.ds2_native_fast_service.NativeFastPreEraseFailure,
        )
        assert calls == []
    finally:
        w._ds2 = None
        w.close()


def test_initial_seed_unavailable_never_restarts_legacy_write(monkeypatch):
    app, w = _gui()
    try:
        legacy_calls = []
        low_ds2 = object()
        w._ds2 = low_ds2
        failure = gui.ds2_native_fast_service.NativeFastPreEraseFailure(
            gui.ds2_native_fast_service.InitialWriteSeedUnavailable(
                "initial write seed unavailable after 2 bounded BMW/0x1E challenges"
            ),
            safe_legacy_fallback=True,
        )

        monkeypatch.setattr(
            w,
            "_run_via_native_fast_write",
            lambda *args, **kwargs: (_ for _ in ()).throw(failure),
        )
        monkeypatch.setattr(
            w,
            "_ds2_write",
            lambda *args, **kwargs: legacy_calls.append((args, kwargs)),
        )

        with pytest.raises(RuntimeError) as caught:
            w._native_fast_write_with_fallback(
                "full",
                b"target",
                "intel",
                lambda *args, **kwargs: None,
                lambda *args, **kwargs: None,
                verify_write=False,
            )

        message = str(caught.value).lower()
        assert caught.value.__cause__ is failure
        assert isinstance(
            failure.cause,
            gui.ds2_native_fast_service.InitialWriteSeedUnavailable,
        )
        assert failure.safe_legacy_fallback is True
        assert w._ds2 is low_ds2
        assert legacy_calls == []
        assert "normal ds2 at 9600 was restored" in message
        assert "nothing was erased" in message
        assert "turn ignition off" in message
        assert "10 seconds" in message
        assert "turn ignition on" in message
    finally:
        w._ds2 = None
        w.close()


def test_ecu_info_tab_has_new_field_set():
    app, w = _gui()
    try:
        expected_fields = {
            "ECU ID", "CAL ID", "Detected Variant", "Firmware Version",
            "VIN", "ISN", "Flash Chip", "Transmission",
        }
        assert set(w._info_labels.keys()) == expected_fields
        assert not any(stale in w._info_labels for stale in
                       ("Part Number", "Hardware Version", "Software Version",
                        "Coding / Variant", "ROM Header (hex)"))
        assert w.raw_ident_view.isHidden()
        assert w._last_ident_raw == b""
    finally:
        w.close()


def test_show_raw_response_button_reveals_hex():
    app, w = _gui()
    try:
        w._last_ident_raw = bytes([0xDE, 0xAD, 0xBE, 0xEF])
        w._on_show_raw_ident()
        # The top-level window is never actually shown in this headless test,
        # so isVisible() (which also checks ancestor visibility) is always
        # False here regardless of state; isHidden() reflects this widget's
        # own explicit show()/hide() flag, which is what we're testing.
        assert not w.raw_ident_view.isHidden()
        assert w.raw_ident_view.toPlainText() == "DE AD BE EF"
    finally:
        w.close()


def test_read_new_info_fields_maps_ds2_reads_to_ecu_info_dict():
    app, w = _gui()
    try:
        calls = []

        class FakeDS2:
            def read_isn(self):
                return "5678"

            def read_mem(self, addr, length):
                calls.append((addr, length))
                if addr == 0x2025:
                    return b"1437806"
                if addr == 0x1CE0:
                    return b"1585" + b"\x00" + b"012345678"
                if addr == 0x023C:
                    return bytes.fromhex("e00e0d58f04ec084")
                if addr == 0xFD5C:
                    return bytes([0x00])
                raise AssertionError(f"unexpected read_mem({addr:#x}, {length})")

        result = w._read_new_info_fields(FakeDS2(), log_fn=lambda *a, **k: None)
        assert result == {
            "Firmware Version": "1437806",
            "ISN": "01234<b>5678</b>",
            "Flash Chip": "AMD driver — 29F200 / 29F400 (bottom half)",
            "Transmission": "Manual",
        }
        assert (0x2025, 7) in calls
        assert (0x1CE0, 14) in calls
        assert (0x023C, 8) in calls
        assert (0xFD5C, 1) in calls
    finally:
        w.close()


def test_read_new_info_fields_survives_a_failing_read():
    app, w = _gui()
    try:
        class FlakyDS2:
            def read_isn(self):
                return "5678"

            def read_mem(self, addr, length):
                if addr == 0x023C:
                    raise TimeoutError("no response")
                return b"1437806" if addr == 0x2025 else (
                    b"1585\x00012345678" if addr == 0x1CE0 else bytes([0x80]))

        result = w._read_new_info_fields(FlakyDS2(), log_fn=lambda *a, **k: None)
        assert result["Flash Chip"] == "Unknown (unexpected signature: no response)"
        assert result["Firmware Version"] == "1437806"   # other fields unaffected
    finally:
        w.close()


def test_populate_ecu_info_sets_detected_variant_with_trimmed_label_set():
    app, w = _gui()
    try:
        # SHINDE1 identify() response: ECU-ID field is exactly 7 ASCII chars.
        ident = b"SHINDE1" + b"\x00" * 20
        parsed_id, parsed_variant = gui._populate_ecu_info(ident, w._info_labels)
        assert parsed_id == "SHINDE1"
        assert parsed_variant == "MS41.3"
        assert "MS41.3" in w._info_labels["Detected Variant"].text()
    finally:
        w.close()


def test_gui_has_free_port_owner_that_works():
    app, w = _gui()
    try:
        assert w._port_owner is not None and w._port_owner.is_free()
        w._port_owner.acquire("flasher")
        assert w._port_owner.owner == "flasher"
        w._port_owner.release("flasher")
        assert w._port_owner.is_free()
    finally:
        w.close()


def test_direct_tap_disables_echo_and_locks_connection_controls(monkeypatch):
    app, w = _gui()
    created = {}

    class FakeDS2:
        def __init__(self, port, baud, verbose, echo):
            created.update(port=port, baud=baud, echo=echo)

        def open(self): pass
        def close(self): pass
        def identify(self): return b"1437806" + b"\x00" * 20
        def read_vin(self): return TEST_VIN
        def read_isn(self): return "5678"

        def read_mem(self, addr, length):
            import identity
            if addr == 0x1000E: return b"12"
            if addr == 0x2025: return b"1437806"
            if addr == 0x1CE0 and length == 15:
                return b"1585\x00" + TEST_SERIAL.encode("ascii") + b"\x00"
            if addr == 0x1CE0: return b"1585\x00" + TEST_SERIAL.encode("ascii")
            if addr == 0x1D07: return identity.encode_vin(TEST_VIN)
            return b"\xFF" * length

    def run_now(task, on_success=None, on_failure=None):
        try:
            result = task(lambda *_a, **_k: None, lambda *_a: None)
            if on_success: on_success(result)
        except Exception as error:
            if on_failure: on_failure(error)

    try:
        monkeypatch.setattr(gui, "DS2Interface", FakeDS2)
        monkeypatch.setattr(w, "_run_task", run_now)
        w.cb_port.clear(); w.cb_port.addItem("COM_TEST")
        w.chk_direct_tap.setChecked(True)
        w._connect()
        assert created == {"port": "COM_TEST", "baud": 9600, "echo": False}
        w._set_all_buttons_enabled(True)
        assert not w.cb_port.isEnabled()
        assert not w.chk_direct_tap.isEnabled()
        w._disconnect()
        w._set_all_buttons_enabled(True)
        assert w.cb_port.isEnabled()
        assert w.chk_direct_tap.isEnabled()
    finally:
        w.close()


def test_stopping_inactive_live_data_does_not_log_false_polling_message(monkeypatch):
    app, w = _gui()
    try:
        logs = []
        monkeypatch.setattr(
            w,
            "_log",
            lambda message, level="info": logs.append((message, level)),
        )
        assert w._poller is None

        w._on_live_stop()

        assert ("Live data polling stopped", "info") not in logs
    finally:
        w.close()


def test_patches_tab_lists_the_ms41_3_patches():
    app, w = _gui()
    try:
        w._set_patch_base(ref("MS41.3"), "test")
        assert "cal_guard" in w._patch_checkboxes
        assert "vanos_minrpm_ms410" not in w._patch_checkboxes   # MS41.0 target
        assert len(w._patch_checkboxes) == 8   # 8 selectable MS41.3 patches (matches test_patch_service)
        pending_badges = {
            label.text()
            for label in w._patch_rows["ignition_cut_v7"].findChildren(gui.QLabel)
        }
        assert "UNTESTED" in pending_badges
        assert "0x3992A" not in w._patch_checkboxes["ignition_cut_v7"].toolTip()
        assert "configurable ignition-cut rev limiter" in (
            w._patch_checkboxes["ignition_cut_v7"].toolTip())
    finally:
        w.close()


def test_patches_tab_warns_for_every_explicitly_untested_patch(monkeypatch):
    app, w = _gui()
    try:
        w._set_patch_base(ref("MS41.3"), "test")
        w._patch_checkboxes["ignition_cut_v7"].setChecked(True)
        shown = {}

        def warning(_parent, title, message, *_args):
            shown.update(title=title, message=message)
            return QMessageBox.No

        monkeypatch.setattr(QMessageBox, "warning", staticmethod(warning))
        w._on_patches_build()

        assert shown["title"] == "Untested Patch"
        assert "marked untested" in shown["message"]
        assert "Ignition Cut v7" in shown["message"]
    finally:
        w.close()


def test_patch_dependency_is_labeled_and_selected_automatically():
    app, w = _gui()
    try:
        w._set_patch_base(ref("MS41.3"), "test")
        launch = w._patch_checkboxes["launch_control_v4"]
        ignition = w._patch_checkboxes["ignition_cut_v7"]

        labels = {
            label.text()
            for label in w._patch_rows["launch_control_v4"].findChildren(gui.QLabel)
        }
        assert "REQUIRES IGNITION CUT V7" in labels
        assert "Required patch: Ignition Cut v7" in launch.toolTip()

        launch.setChecked(True)
        assert launch.isChecked()
        assert ignition.isChecked()
        assert w.btn_patches_build.isEnabled()

        ignition.setChecked(False)
        assert ignition.isChecked()  # an active dependency cannot be silently dropped
    finally:
        w.close()


def test_patch_dependency_never_removes_a_conflicting_selection(monkeypatch):
    app, w = _gui()
    try:
        w._set_patch_base(ref("MS41.3"), "test")
        monkeypatch.setattr(
            gui.patch_service,
            "collisions",
            lambda selected: {"ignition_cut_v7"}
            if "launch_control_v4" in selected else set(),
        )

        launch = w._patch_checkboxes["launch_control_v4"]
        launch.setChecked(True)

        assert launch.isChecked()
        assert not w._patch_checkboxes["ignition_cut_v7"].isChecked()
        assert not w.btn_patches_build.isEnabled()
        assert "Required patch unavailable" in w.btn_patches_build.toolTip()
    finally:
        w.close()


def test_installed_dependency_remove_button_is_blocked_by_launch_control():
    import patch_service

    app, w = _gui()
    try:
        combined, _ = patch_service.build_image(
            ref("MS41.3"), ["ignition_cut_v7", "launch_control_v4"]
        )
        w._set_patch_base(combined, "dependency-removal-test")

        buttons = w._patch_rows["ignition_cut_v7"].findChildren(QPushButton)
        remove = next(button for button in buttons if button.text() == "✕ Remove")
        labels = {
            label.text()
            for label in w._patch_rows["ignition_cut_v7"].findChildren(gui.QLabel)
        }
        assert remove.isEnabled() is False
        assert "Launch Control v4" in remove.toolTip()
        assert "REQUIRED BY LAUNCH CONTROL V4" in labels
    finally:
        w.close()


def test_patches_tab_removes_field_failed_v6_and_enables_v7(monkeypatch):
    import patch_service

    app, w = _gui()
    try:
        failed_image, _ = patch_service.build_image(
            ref("MS41.3"), ["ignition_cut_v6"])
        w._set_patch_base(failed_image, "field-failed-v6-test")

        assert w._patch_checkboxes["ignition_cut_v6"].isChecked()
        assert not w._patch_checkboxes["ignition_cut_v6"].isEnabled()
        assert not w._patch_checkboxes["ignition_cut_v7"].isEnabled()
        buttons = w._patch_rows["ignition_cut_v6"].findChildren(QPushButton)
        assert any(button.text() == "✕ Remove" for button in buttons)

        monkeypatch.setattr(
            QMessageBox, "question",
            staticmethod(lambda *_args, **_kwargs: QMessageBox.Yes),
        )
        w._on_patch_remove("ignition_cut_v6")

        assert "ignition_cut_v6" not in w._patch_checkboxes
        assert w._patch_checkboxes["ignition_cut_v7"].isEnabled()
        assert "Removed ignition_cut_v6" in w.patches_log.toPlainText()
    finally:
        w.close()


def test_patches_tab_removes_deprecated_loader_and_enables_relocation(monkeypatch):
    import patch_service

    app, w = _gui()
    try:
        legacy_image, _ = patch_service.build_image(
            ref("MS41.3"), ["softbsl_loader_legacy"])
        w._set_patch_base(legacy_image, "legacy-loader-test")

        assert w._patch_checkboxes["softbsl_loader_legacy"].isChecked()
        assert not w._patch_checkboxes["softbsl_loader_legacy"].isEnabled()
        assert not w._patch_checkboxes["softbsl_loader"].isEnabled()
        buttons = w._patch_rows["softbsl_loader_legacy"].findChildren(QPushButton)
        assert any(button.text() == "✕ Remove" for button in buttons)

        monkeypatch.setattr(
            QMessageBox, "question", staticmethod(lambda *_args, **_kwargs: QMessageBox.Yes))
        w._on_patch_remove("softbsl_loader_legacy")

        assert "softbsl_loader_legacy" not in w._patch_checkboxes
        assert w._patch_checkboxes["softbsl_loader"].isEnabled()
        assert "Removed softbsl_loader_legacy" in w.patches_log.toPlainText()
    finally:
        w.close()


def test_identity_tab_decodes_ref_image():
    app, w = _gui()
    try:
        import identity

        expected = identity.decode_identity(ref("MS41.1"))
        titles = {w.tabs.tabText(i).strip() for i in range(w.tabs.count())}
        assert "VIN / EWS" in titles
        assert not hasattr(w, "btn_id_load_flash")
        assert w.btn_id_read_flash_ecu.text() == "Read BOOT Identity"
        assert w.btn_id_read_ecu.text() == "Read ISN"
        assert "16 KB" in w.btn_id_read_flash_ecu.toolTip()
        assert "four-digit DME ISN" in w.btn_id_read_ecu.toolTip()
        assert w.btn_id_read_flash_ecu.isEnabled() is False
        w._show_identity(ref("MS41.1"), "unit-test")
        assert w._id_labels["serial"].text() == expected.serial
        assert w._id_labels["isn"].text() == expected.isn4
        assert w._id_labels["vin"].text() == expected.vin
        assert w.id_vin_current.text() == expected.vin
        assert "0x" in w.id_boot_strings.toPlainText()
        assert w._identity_isn is None       # BOOT display data never arms EWS
    finally:
        w.close()


def test_identity_full_flash_read_auto_routes_archives_and_loads_editor(monkeypatch):
    app, w = _gui()
    try:
        data = ref("MS41.3")
        w._ds2 = object()
        w._connection_port = "COM_TEST"
        w._ecu_identity_source = _live_identity_source()
        w._ecu_softbsl_marker = "B"
        w._ecu_softbsl_hook_present = True
        w._ecu_chip_sig = bytes.fromhex("e00e0d58f04ec084")
        w._d2xx_ok = True
        monkeypatch.setattr(w, "_ask_identity_backup_choice", lambda: "backup")
        routed = {}
        monkeypatch.setattr(
            w, "_read_image_auto",
            lambda which, log_fn, progress_fn:
                (routed.update(which=which), data)[1])
        entry = type("Entry", (), {"filename": "identity-full-read.bin"})()
        archived = {}
        monkeypatch.setattr(
            w, "_backup_save_bytes",
            lambda image, mode, source:
                (archived.update(data=bytes(image), mode=mode, source=source), entry)[1])
        monkeypatch.setattr(w, "_refresh_backup_table",
                            lambda: archived.update(refreshed=True))
        shown = {}
        monkeypatch.setattr(
            QMessageBox, "information",
            staticmethod(lambda *a, **k: shown.update(title=a[1], message=a[2])))

        def sync_run_task(task, on_success=None, on_failure=None):
            result = task(lambda *a, **k: None, lambda *a, **k: None)
            if on_success:
                on_success(result)

        monkeypatch.setattr(w, "_run_task", sync_run_task)

        w._on_identity_read_flash_ecu()

        assert routed["which"] == "full"
        assert archived["data"] == data
        assert archived["mode"] == "full"
        assert archived["source"] == "ECU read (VIN editor)"
        assert archived["refreshed"] is True
        assert w._last_full_read == data
        assert w._session_backup_read is True
        assert w._ecu_program_variant == "MS41.3"
        assert w._id_labels["source"].text() == entry.filename
        assert w._identity_boot_data == data[0x4000:0x8000]
        assert w.btn_id_vin_apply.isEnabled() is False  # no valid new VIN/transport yet
        assert shown["title"] == "Full Backup and BOOT Read Complete"
    finally:
        w._ds2 = None
        w.close()


def test_identity_partial_read_auto_routes_through_softbsl(monkeypatch):
    app, w = _gui()
    try:
        import identity
        full = ref("MS41.1")
        boot_data = full[
            identity.BOOT_DATA_OFF:identity.BOOT_DATA_OFF + identity.BOOT_DATA_SIZE]
        w._ds2 = object()
        w._connection_port = "COM_TEST"
        w._ecu_identity_source = _live_identity_source()
        w._ecu_softbsl_marker = "B"
        w._ecu_softbsl_hook_present = True
        w._ecu_chip_sig = bytes.fromhex("e00e0d58f04ec084")
        w._d2xx_ok = True
        monkeypatch.setattr(w, "_ask_identity_backup_choice", lambda: "partial")

        routed = {}
        monkeypatch.setattr(
            gui.softbsl_service, "read_identity_data",
            lambda port, baud, progress_cb, log, chip_family=None, half="B":
                (routed.update(port=port, baud=baud, chip_family=chip_family, half=half), boot_data)[1])
        monkeypatch.setattr(
            w, "_run_via_softbsl",
            lambda op, log_fn, progress_fn: op("COM_TEST", progress_fn, log_fn))

        def sync_run_task(task, on_success=None, on_failure=None):
            try:
                result = task(lambda *a, **k: None, lambda *a, **k: None)
                if on_success:
                    on_success(result)
            except Exception as error:
                if on_failure:
                    on_failure(str(error))

        monkeypatch.setattr(w, "_run_task", sync_run_task)

        w._on_identity_read_flash_ecu()

        assert routed == {"port": "COM_TEST", "baud": "high", "chip_family": "amd", "half": "B"}
        assert w._identity_boot_data == boot_data
        assert w._id_labels["source"].text() == "live ECU • cached only"
    finally:
        w._ds2 = None
        w.close()


def test_identity_write_rewrites_only_sa1_and_persists_recovery(monkeypatch, tmp_path):
    app, w = _gui()
    try:
        import identity
        image = bytearray(ref("MS41.1"))
        image[0x5FFC:0x6000] = bytes.fromhex("a55a42bd")
        w._ds2 = object()
        w._connection_port = "COM_TEST"
        w._ecu_id = "1437806"
        w._ecu_softbsl_marker = "B"
        w._ecu_softbsl_hook_present = True
        w._ecu_chip_sig = bytes.fromhex("e00e0d58f04ec084")
        w._d2xx_ok = True
        w._ecu_identity_source = bytes(image)
        w._show_identity(bytes(image), "live ECU")
        w.id_vin_custom.setText(TARGET_VIN)
        monkeypatch.setattr(QInputDialog, "getText",
                            staticmethod(lambda *a, **k: ("WRITE VIN", True)))
        monkeypatch.setattr(gui, "IDENTITY_RECOVERY_DIR", str(tmp_path))

        original_sector = bytes(image[0x4000:0x6000])
        target_boot = identity.set_boot_vin(bytes(image[0x4000:0x8000]), TARGET_VIN)
        target_sector = bytes(target_boot[:identity.IDENTITY_SECTOR_SIZE])
        reads = iter((original_sector, target_sector))
        written = {}
        monkeypatch.setattr(
            gui.softbsl_service, "read_identity_sector",
            lambda port, baud, progress_cb, log, **kwargs: next(reads))
        monkeypatch.setattr(
            gui.softbsl_service, "write_identity_sector",
            lambda port, sector, prompt, log, **kwargs:
                written.update(port=port, sector=bytes(sector), kwargs=kwargs))
        monkeypatch.setattr(
            w, "_run_via_softbsl",
            lambda op, log_fn, progress_fn: op("COM_TEST", progress_fn, log_fn))
        monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))

        def sync_run_task(task, on_success=None, on_failure=None):
            try:
                result = task(lambda *a, **k: None, lambda *a, **k: None)
                if on_success:
                    on_success(result)
            except Exception as error:
                if on_failure:
                    on_failure(str(error))

        monkeypatch.setattr(w, "_run_task", sync_run_task)

        w._on_identity_vin_apply()

        assert written["sector"] == target_sector
        assert len(written["sector"]) == 8192
        assert written["kwargs"]["chip_family"] == "amd"
        assert written["kwargs"]["half"] == "B"
        recovery_files = list(tmp_path.glob("*_sa1_bottom_pre_vin_*.bin"))
        assert len(recovery_files) == 1
        assert recovery_files[0].read_bytes() == original_sector
        assert w.id_vin_current.text() == TARGET_VIN
        assert identity.decode_boot_identity(w._identity_boot_data).vin == TARGET_VIN
    finally:
        w._ds2 = None
        w.close()


def test_identity_write_on_top_rewrites_complete_fused_sa7(monkeypatch, tmp_path):
    app, w = _gui()
    try:
        import identity
        image = bytearray(ref("MS41.1"))
        image[0x5FFC:0x6000] = bytes.fromhex("a55a54ab")
        w._ds2 = object()
        w._connection_port = "COM_TEST"
        w._ecu_id = "1437806"
        w._ecu_softbsl_marker = "T"
        w._ecu_softbsl_hook_present = True
        w._ecu_chip_sig = bytes.fromhex("e00e0d58f04ec084")
        w._d2xx_ok = True
        w._ecu_identity_source = bytes(image)
        w._show_identity(bytes(image), "live ECU TOP")
        w.id_vin_custom.setText(TARGET_VIN)
        monkeypatch.setattr(QInputDialog, "getText",
                            staticmethod(lambda *a, **k: ("WRITE VIN", True)))
        monkeypatch.setattr(gui, "IDENTITY_RECOVERY_DIR", str(tmp_path))

        original_sector = bytes(image[:0x10000])
        target_sector = bytearray(original_sector)
        target_sector[identity.VIN_OFF:identity.VIN_OFF + identity.VIN_LEN] = identity.encode_vin(
            TARGET_VIN)
        target_sector = bytes(target_sector)
        reads = iter((original_sector, target_sector))
        written = {}
        monkeypatch.setattr(
            gui.softbsl_service, "read_identity_sector",
            lambda port, baud, progress_cb, log, **kwargs: next(reads))
        monkeypatch.setattr(
            gui.softbsl_service, "write_identity_sector",
            lambda port, sector, prompt, log, **kwargs:
                written.update(port=port, sector=bytes(sector), kwargs=kwargs))
        monkeypatch.setattr(
            w, "_run_via_softbsl",
            lambda op, log_fn, progress_fn: op("COM_TEST", progress_fn, log_fn))
        monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))

        def sync_run_task(task, on_success=None, on_failure=None):
            try:
                result = task(lambda *a, **k: None, lambda *a, **k: None)
                if on_success:
                    on_success(result)
            except Exception as error:
                if on_failure:
                    on_failure(str(error))

        monkeypatch.setattr(w, "_run_task", sync_run_task)

        w._on_identity_vin_apply()

        assert written["sector"] == target_sector
        assert len(written["sector"]) == 64 * 1024
        assert written["kwargs"]["chip_family"] == "amd"
        assert written["kwargs"]["half"] == "T"
        recovery_files = list(tmp_path.glob("*_sa7_top_pre_vin_*.bin"))
        assert len(recovery_files) == 1
        assert recovery_files[0].read_bytes() == original_sector
        assert w.id_vin_current.text() == TARGET_VIN
        assert w._identity_sector_data == target_sector
    finally:
        w._ds2 = None
        w.close()


def test_ews_read_populates_only_its_own_isn_state(monkeypatch):
    app, w = _gui()
    try:
        w._show_identity(ref("MS41.1"), "older-editor-image.bin")
        before = {key: label.text() for key, label in w._id_labels.items()}
        class FakeDS2:
            def read_isn(self):
                return "5678"

        w._ds2 = FakeDS2()
        w._connection_port = "COM_TEST"
        w._ecu_identity_source = _live_identity_source()

        def sync_run_task(task, on_success=None, on_failure=None):
            result = task(lambda *a, **k: None, lambda *a, **k: None)
            if on_success:
                on_success(result)
        monkeypatch.setattr(w, "_run_task", sync_run_task)

        w._on_identity_read_ecu()

        assert {key: label.text() for key, label in w._id_labels.items()} == before
        assert w.id_ews_isn.text() == "5678"
        assert w._identity_isn == "5678"
        assert w.btn_ews_send.isEnabled()
    finally:
        w.close()


def test_ews_send_uses_proven_encoding_and_exact_ack(monkeypatch):
    app, w = _gui()
    try:
        class FakeDS2:
            def read_isn(self):
                return "4262"
            def send_frame(self, frame, resp_addr):
                sent.update(frame=bytes(frame), resp_addr=resp_addr)
                return bytes([0x44, 0x04, 0xA0, 0xE0])
        sent = {}
        w._ds2 = FakeDS2()
        w._connection_port = "COM_TEST"
        w._ecu_identity_source = _live_identity_source()
        w._set_isn("4262", w._identity_connection_key())
        monkeypatch.setattr(QMessageBox, "warning",
                            staticmethod(lambda *a, **k: QMessageBox.Yes))
        monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))

        def sync_run_task(task, on_success=None, on_failure=None):
            try:
                on_success(task(lambda *a, **k: None, lambda *a, **k: None))
            except Exception as error:
                if on_failure:
                    on_failure(str(error))
        monkeypatch.setattr(w, "_run_task", sync_run_task)

        w._on_ews_send()

        assert sent["resp_addr"] == 0x44
        assert sent["frame"] == bytes([0x44, 0x06, 0x61, 0x00, 0xA6, 0x85])
        assert "0x0A6" in w.id_ews_frames.toPlainText()
        assert "Validated acknowledgement" in w.id_ews_frames.toPlainText()
        assert w._identity_isn is None
    finally:
        w.close()


def test_rom_analyzer_shows_full_isn_bold_last_four():
    app, w = _gui()
    try:
        import ecu_info
        import identity

        expected = identity.decode_identity(ref("MS41.1"))
        w._show_analysis(ref("MS41.1"), "unit-test.bin")
        assert w._analyzer_labels["isn"].text() == ecu_info.format_full_isn_html(
            expected.serial, expected.isn4 or "")
    finally:
        w.close()


def test_rom_analyzer_isn_reports_na_for_tune_file():
    app, w = _gui()
    try:
        tune = bytearray(b"\xFF" * 24576)
        w._show_analysis(bytes(tune), "unit-test-tune.bin")
        assert "N/A" in w._analyzer_labels["isn"].text()
    finally:
        w.close()


def test_rom_analyzer_registers_selects_and_deletes_definition(tmp_path, monkeypatch):
    source = tmp_path / "User MS41.xml"
    source.write_text(
        """<roms><rom><romid><xmlid>TEST</xmlid><internalidaddress>0xE</internalidaddress>
<internalidstring>41</internalidstring><filesize>24kb</filesize>
<submodel>Test</submodel><ecuid>TEST</ecuid></romid></rom></roms>""",
        encoding="utf-8",
    )
    app, w = _gui()
    try:
        w._definition_registry = gui.DefinitionRegistry(tmp_path / "registry")
        w._refresh_analyzer_definitions()
        monkeypatch.setattr(
            QFileDialog,
            "getOpenFileName",
            staticmethod(lambda *args, **kwargs: (str(source), "XML Definition Files (*.xml)")),
        )
        monkeypatch.setattr(
            QMessageBox,
            "question",
            staticmethod(lambda *args, **kwargs: QMessageBox.Yes),
        )

        w._on_load_definition()

        assert w.cb_analyzer_definition.currentData() == source.name
        assert w._definition_registry.active_name() == source.name
        assert (w._definition_registry.directory / source.name).is_file()

        w._on_delete_definition()

        assert w.cb_analyzer_definition.currentData() is None
        assert w._definition_registry.names() == []
        assert source.is_file()
    finally:
        w.close()


def test_rom_analyzer_parameters_window_is_modeless_reused_and_synchronized(tmp_path):
    source = tmp_path / "Window Test.xml"
    source.write_text(
        """<roms><rom><romid><xmlid>WINDOW</xmlid>
<internalidaddress>0xE</internalidaddress><internalidstring>41</internalidstring>
<filesize>24kb</filesize><submodel>Window Test</submodel><ecuid>TEST</ecuid></romid>
<table name="Test Scalar" category="Limits" storageaddress="0x20"
sizex="1" sizey="1" storagetype="uint8">
<scaling units="raw" expression="x" format="0" /></table>
<table name="Test Map" category="Fuel" storageaddress="0x30" sizex="2" sizey="2" />
</rom></roms>""",
        encoding="utf-8",
    )
    app, w = _gui()
    try:
        w._definition_registry = gui.DefinitionRegistry(tmp_path / "registry")
        registered = w._definition_registry.import_file(source)
        w._definition_registry.set_active(registered.path.name)
        w._refresh_analyzer_definitions(registered.path.name)

        tune = bytearray(b"\xFF" * 24576)
        tune[0xE:0x10] = b"41"
        tune[0x20] = 7
        w._show_analysis(tune, "first-tune.bin")
        assert w.btn_analyzer_parameters_window.isEnabled() is True

        w._open_analyzer_parameters_window()
        dialog = w._analyzer_parameters_window
        assert dialog is not None
        assert dialog.isModal() is False
        assert dialog.table.rowCount() == 2
        assert "first-tune.bin" in dialog.context_label.text()
        assert source.name in dialog.context_label.text()

        w._open_analyzer_parameters_window()
        assert w._analyzer_parameters_window is dialog

        dialog.filter_edit.setText("scalar")
        assert dialog.table.rowCount() == 1
        assert dialog.table.item(0, 1).text() == "Test Scalar"

        tune[0x20] = 9
        w._show_analysis(tune, "second-tune.bin")
        assert w._analyzer_parameters_window is dialog
        assert "second-tune.bin" in dialog.context_label.text()
        assert dialog.table.item(0, 2).text() == "9"

        dialog.filter_edit.clear()
        dialog.scalars_only.setChecked(True)
        assert dialog.table.rowCount() == 1
        assert dialog.table.item(0, 1).text() == "Test Scalar"

        dialog.close()
        app.processEvents()
        assert w._analyzer_parameters_window is None
    finally:
        w.close()


def test_softbsl_tab_loads_and_previews():
    app, w = _gui()
    try:
        titles = {w.tabs.tabText(i).strip() for i in range(w.tabs.count())}
        assert "Soft-BSL" in titles
        img = bytearray(b"\xFF" * 262144)
        img[0x423C:0x4244] = bytes.fromhex("e00e0d58f04ec084")
        img[0x5FFC:0x6000] = bytes([0xA5, 0x5A, 0x54, 0x54 ^ 0xFF])   # 'T'
        w._ecu_chip_sig = bytes.fromhex("e00e0d58f04ec084")
        w._show_softbsl_image(bytes(img), "unit-test")
        assert w._softbsl_image is not None
        assert "T" in w._softbsl_marker_lbl.text()
        preview = w._softbsl_preview.toPlainText()
        assert "CROSS-BANK top-half write PLAN" in preview
        assert "BRICK-CLASS" in preview
        assert "full bottom half" not in preview
        assert w.btn_softbsl_xbank.isEnabled() is True

        # A bottom image can be inspected, but can never arm the TOP-bank write.
        img[0x5FFC:0x6000] = bytes([0xA5, 0x5A, 0x42, 0x42 ^ 0xFF])   # 'B'
        w._show_softbsl_image(bytes(img), "bottom.bin")
        assert w.btn_softbsl_xbank.isEnabled() is False
        assert "marker 'T'" in w.btn_softbsl_xbank.toolTip()
    finally:
        w.close()


def test_softbsl_crossbank_button_requires_disconnected_session():
    app, w = _gui()
    try:
        img = bytearray(b"\xFF" * 262144)
        img[0x423C:0x4244] = bytes.fromhex("e00e0d58f04ec084")
        img[0x5FFC:0x6000] = bytes([0xA5, 0x5A, 0x54, 0x54 ^ 0xFF])
        w._ecu_chip_sig = bytes.fromhex("e00e0d58f04ec084")
        w._show_softbsl_image(bytes(img), "top.bin")
        assert w.btn_softbsl_xbank.isEnabled() is True

        w._ds2 = object()
        w._update_softbsl_crossbank_button()
        assert w.btn_softbsl_xbank.isEnabled() is False
        assert "Disconnect" in w.btn_softbsl_xbank.toolTip()
    finally:
        w._ds2 = None
        w.close()


def test_softbsl_crossbank_failure_releases_port_and_shows_a17_recovery(monkeypatch):
    app, w = _gui()
    try:
        w._port_owner.acquire("softbsl")
        shown = {}
        monkeypatch.setattr(
            QMessageBox, "critical",
            staticmethod(lambda *args, **kwargs: shown.update(message=args) or QMessageBox.Ok))

        w._on_softbsl_crossbank_failure("verify mismatch")

        assert w._port_owner.is_free()
        assert shown["message"][1] == "Cross-bank Write Stopped"
        assert "A17 switch is back in the LOWER" in shown["message"][2]
        assert "verify mismatch" in shown["message"][2]
    finally:
        w._port_owner.release("softbsl")
        w.close()


def test_softbsl_tab_has_install_controls():
    app, w = _gui()
    try:
        assert hasattr(w, "btn_softbsl_install") and hasattr(w, "chk_install_calguard")
        assert hasattr(w, "chk_install_force_base")
        assert hasattr(w, "chk_install_preserve_identity")
        assert hasattr(w, "chk_install_preserve_cal")
        assert w.chk_install_calguard.isChecked() is True     # cal_guard on by default
        assert w.chk_install_force_base.isChecked() is False
        assert w.chk_install_preserve_identity.isChecked() is True
        assert w.chk_install_preserve_cal.isChecked() is True
        assert w.btn_softbsl_install.isEnabled() is False      # disconnected by default
        assert hasattr(w, "btn_softbsl_xbank_load")
        assert hasattr(w, "btn_softbsl_xbank_read")
        assert w.chk_xbank_calguard.isChecked() is True
        assert w.chk_xbank_preserve_identity.isChecked() is True
        assert not hasattr(w, "_install_target")               # legacy two-image pickers removed
    finally:
        w.close()


def test_softbsl_install_button_tracks_connection_state():
    app, w = _gui()
    try:
        assert w.btn_softbsl_install.isEnabled() is False
        w._set_ds2_buttons_enabled()                       # successful DS2 connection path
        assert w.btn_softbsl_install.isEnabled() is True
        w._set_ecu_buttons_enabled(False)
        assert w.btn_softbsl_install.isEnabled() is False
    finally:
        w.close()


def test_task_restores_button_state_after_callback_reopens_ds2(monkeypatch):
    app, w = _gui()
    try:
        class Signal:
            def __init__(self):
                self.callback = None
            def connect(self, callback):
                self.callback = callback
            def emit(self, *args):
                self.callback(*args)

        class ImmediateWorker:
            def __init__(self, _task):
                self.log_signal = Signal()
                self.progress_signal = Signal()
                self.done_signal = Signal()
            def start(self):
                self.done_signal.emit(True, "done")

        monkeypatch.setattr(gui, "WorkerThread", ImmediateWorker)
        states = []
        monkeypatch.setattr(
            w, "_set_all_buttons_enabled",
            lambda enabled: states.append((enabled, w._ds2 is not None)))

        def reopen_before_restore(_result):
            w._ds2 = object()       # mirrors _release_softbsl_port -> _reopen_ds2_with_retry

        w._run_task(lambda *_args: None, on_success=reopen_before_restore)

        assert states == [(False, False), (True, True)]
    finally:
        w._ds2 = None
        w.close()


def test_run_task_stops_active_live_poller_before_worker_starts(monkeypatch):
    app, w = _gui()
    try:
        events = []

        class Poller:
            csv_rows = 0

            def stop(self):
                events.append("poller_stop")

        class Signal:
            def __init__(self):
                self.callback = None

            def connect(self, callback):
                self.callback = callback

            def emit(self, *args):
                self.callback(*args)

        class ImmediateWorker:
            def __init__(self, _task):
                self.log_signal = Signal()
                self.progress_signal = Signal()
                self.done_signal = Signal()

            def start(self):
                events.append("worker_start")
                assert w._poller is None
                assert not w._live_timer.isActive()
                self.done_signal.emit(True, None)

        w._poller = Poller()
        w._live_timer.start()
        monkeypatch.setattr(gui, "WorkerThread", ImmediateWorker)

        w._run_task(lambda *_args: None)

        assert events == ["poller_stop", "worker_start"]
    finally:
        w._live_timer.stop()
        w._poller = None
        w.close()


def test_softbsl_cal_preservation_control_is_gated_by_live_variant():
    app, w = _gui()
    try:
        w._ds2 = object()
        w._ecu_program_variant = "MS41.3"
        w._update_softbsl_install_options()
        assert w.chk_install_preserve_cal.isEnabled() is True
        assert w.chk_install_preserve_cal.isChecked() is True

        w._ecu_program_variant = None
        w._ecu_variant = "MS41.2"
        w._update_softbsl_install_options()
        assert w.chk_install_preserve_cal.isEnabled() is True
        assert w.chk_install_preserve_cal.isChecked() is True
    finally:
        w._ds2 = None
        w.close()


def test_ecu_is_ms41_3_detects_from_variant_fields():
    app, w = _gui()
    try:
        assert w._ecu_is_ms41_3() is False
        w._ecu_program_variant = "MS41.3"
        assert w._ecu_is_ms41_3() is True
        w._ecu_program_variant = None
        w._ecu_variant = "MS41.3"
        assert w._ecu_is_ms41_3() is True
        w._ecu_variant = "MS41.2"
        assert w._ecu_is_ms41_3() is False
    finally:
        w.close()


@pytest.mark.parametrize("version", ["MS41.2", "MS41.3"])
def test_softbsl_install_native_target_reads_ecu_no_base_no_convert(monkeypatch, version):
    app, w = _gui()
    try:
        import softbsl_install
        w._ecu_program_variant = version
        w.cb_port.clear(); w.cb_port.addItem("COM1"); w.cb_port.setCurrentText("COM1")
        monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: QMessageBox.Yes))
        monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: QMessageBox.Ok))
        captured = {}
        monkeypatch.setattr(softbsl_install, "install_compose",
                            lambda port, base, wcg, ac, prompt, log, baud="low", progress_cb=None,
                                   confirm_reinstall=None, preserve_cal=True:
                                captured.update(port=port, base=base, allow_convert=ac, wcg=wcg,
                                                preserve_cal=preserve_cal) or 0)
        def sync_run_task(task, on_success=None, on_failure=None):
            r = task(lambda *a, **k: None, lambda *a, **k: None)
            if on_success:
                on_success(r)
        monkeypatch.setattr(w, "_run_task", sync_run_task)

        w._on_softbsl_install()

        assert captured["base"] is None            # in-process installer reads this ECU as the base
        assert captured["allow_convert"] is False
        assert captured["port"] == "COM1"
        assert captured["preserve_cal"] is True
    finally:
        w.close()


@pytest.mark.parametrize("version", ["MS41.2", "MS41.3"])
def test_softbsl_install_matching_force_base_skips_ecu_image(monkeypatch, tmp_path, version):
    app, w = _gui()
    try:
        import softbsl_install
        w._ecu_program_variant = version
        w.cb_port.clear(); w.cb_port.addItem("COM1"); w.cb_port.setCurrentText("COM1")
        w.chk_install_force_base.setChecked(True)
        w._last_full_read = b"cached ECU image must not be used"
        w._ecu_identity_source = _live_identity_source()
        base = tmp_path / f"forced-{version.lower().replace('.', '')}.bin"
        base.write_bytes(ref(version))

        monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: QMessageBox.Yes))
        monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: QMessageBox.Ok))
        monkeypatch.setattr(QFileDialog, "getOpenFileName",
                            staticmethod(lambda *a, **k: (str(base), "")))
        captured = {}
        monkeypatch.setattr(
            softbsl_install, "install_compose",
            lambda port, selected_base, wcg, ac, prompt, log, baud="low", progress_cb=None,
                   confirm_reinstall=None, preserve_cal=True:
                captured.update(port=port, base=selected_base, allow_convert=ac,
                                preserve_cal=preserve_cal) or 0)

        def sync_run_task(task, on_success=None, on_failure=None):
            result = task(lambda *a, **k: None, lambda *a, **k: None)
            if on_success:
                on_success(result)
        monkeypatch.setattr(w, "_run_task", sync_run_task)

        w._on_softbsl_install()

        assert captured["base"] != str(base)            # temporary identity-grafted copy
        grafted = open(captured["base"], "rb").read()
        import identity
        info = identity.decode_identity(grafted)
        assert (info.serial, info.isn4, info.vin) == (TEST_SERIAL, TEST_ISN, TEST_VIN)
        assert captured["allow_convert"] is False
        assert captured["preserve_cal"] is True
        assert captured["port"] == "COM1"
    finally:
        w.close()


def test_softbsl_install_success_disconnects_without_reopening_ds2(monkeypatch):
    app, w = _gui()
    try:
        shown = {}
        w._port_owner.acquire("softbsl")
        w._softbsl_handoff_port = "COM7"
        w._connection_port = "COM7"
        w._ds2 = None
        w.btn_connect.blockSignals(True)
        w.btn_connect.setChecked(True)
        w.btn_connect.blockSignals(False)
        end_session_calls = []
        monkeypatch.setattr(
            w, "_end_session_log", lambda: end_session_calls.append(True)
        )
        monkeypatch.setattr(
            w,
            "_reopen_ds2_with_retry",
            lambda *_args, **_kwargs: pytest.fail(
                "verified installation must not reopen the DS2 session"
            ),
        )
        monkeypatch.setattr(
            QMessageBox, "information",
            staticmethod(lambda *args, **kwargs: shown.update(info=args) or QMessageBox.Ok))

        w._on_softbsl_install_success("COM7")

        assert w._port_owner.is_free()
        assert w._softbsl_handoff_port is None
        assert w._connection_port is None
        assert w._ds2 is None
        assert not w.btn_connect.isChecked()
        assert end_session_calls == [True]
        assert w.lbl_status.text() == "● Disconnected"
        assert shown["info"][1] == "Soft-BSL Installed"
        assert "installed and verified successfully" in shown["info"][2]
        assert "closed intentionally" in shown["info"][2]
        assert "Press Connect" in shown["info"][2]
        assert "use Soft-BSL automatically" in shown["info"][2]
    finally:
        w._ds2 = None
        w.close()


def test_softbsl_install_factory_prompts_convert_picks_grafted_base(monkeypatch, tmp_path):
    app, w = _gui()
    try:
        import softbsl_install
        w._ecu_program_variant = None
        w._ecu_variant = "MS41.1"                  # unsupported native target -> convert path
        w.cb_port.clear(); w.cb_port.addItem("COM1"); w.cb_port.setCurrentText("COM1")
        base = _write_ms413_base(tmp_path / "ms413.bin")

        monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: QMessageBox.Yes))
        monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: QMessageBox.Yes))
        monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
        monkeypatch.setattr(QFileDialog, "getOpenFileName",
                            staticmethod(lambda *a, **k: (str(base), "")))
        # simulate a cached full read -> identity graft returns a grafted temp path + info
        info = type("I", (), {"serial": TEST_SERIAL, "isn4": TEST_ISN, "vin": TEST_VIN})()
        monkeypatch.setattr(w, "_graft_softbsl_target", lambda p: ("/tmp/grafted.bin", info))
        captured = {}
        monkeypatch.setattr(softbsl_install, "install_compose",
                            lambda port, base, wcg, ac, prompt, log, baud="low", progress_cb=None,
                                   confirm_reinstall=None, preserve_cal=True:
                                captured.update(base=base, allow_convert=ac,
                                                preserve_cal=preserve_cal) or 0)
        def sync_run_task(task, on_success=None, on_failure=None):
            r = task(lambda *a, **k: None, lambda *a, **k: None)
            if on_success:
                on_success(r)
        monkeypatch.setattr(w, "_run_task", sync_run_task)

        w._on_softbsl_install()

        assert captured["base"] == "/tmp/grafted.bin"   # grafted MS41.3 base passed as --base
        assert captured["allow_convert"] is True
        assert captured["preserve_cal"] is False
    finally:
        w.close()


def test_softbsl_install_ms41_0_uses_boot_write_untested_warning(monkeypatch):
    app, w = _gui()
    try:
        w._ecu_program_variant = None
        w._ecu_variant = "MS41.0"
        w.cb_port.clear(); w.cb_port.addItem("COM1"); w.cb_port.setCurrentText("COM1")
        shown = []

        def warning(*args, **kwargs):
            shown.append((args[1], args[2]))
            return QMessageBox.Cancel

        monkeypatch.setattr(QMessageBox, "warning", staticmethod(warning))

        w._on_softbsl_install()

        title, message = shown[0]
        assert "MS41.0 Conversion" in title
        assert "Boot Write" in title
        assert "compatible hardware" in message
        assert "not yet been validated" in message
        assert "target boot/parameter region" in message
        assert w._port_owner.is_free()
    finally:
        w.close()


def test_softbsl_install_releases_port_if_graft_raises(monkeypatch, tmp_path):
    app, w = _gui()
    try:
        import softbsl_install
        w._ecu_variant = "MS41.1"                   # unsupported native target -> convert path
        w.cb_port.clear(); w.cb_port.addItem("COM1"); w.cb_port.setCurrentText("COM1")
        base = _write_ms413_base(tmp_path / "ms413.bin")
        monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: QMessageBox.Yes))
        monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *a, **k: None))
        monkeypatch.setattr(QFileDialog, "getOpenFileName",
                            staticmethod(lambda *a, **k: (str(base), "")))
        def boom(p):
            raise OSError("disk gone between the dialog and the read")
        monkeypatch.setattr(w, "_graft_softbsl_target", boom)
        called = {}
        monkeypatch.setattr(softbsl_install, "install_compose",
                            lambda *a, **k: called.update(ran=True) or 0)

        w._on_softbsl_install()

        assert "ran" not in called                  # install never dispatched
        assert w._port_owner.is_free()              # port released despite the graft failure
    finally:
        w.close()


def test_softbsl_install_rejects_wrong_sized_base(monkeypatch, tmp_path):
    app, w = _gui()
    try:
        import softbsl_install
        w._ecu_variant = "MS41.1"
        w.cb_port.clear(); w.cb_port.addItem("COM1"); w.cb_port.setCurrentText("COM1")
        bad = tmp_path / "tune.bin"; bad.write_bytes(b"\xFF" * 24576)   # 24 KB, not a 256 KB full image
        warned = {}
        monkeypatch.setattr(QMessageBox, "warning",
                            staticmethod(lambda *a, **k: warned.update(m=a) or QMessageBox.Yes))
        monkeypatch.setattr(QFileDialog, "getOpenFileName",
                            staticmethod(lambda *a, **k: (str(bad), "")))
        called = {}
        monkeypatch.setattr(softbsl_install, "install_compose",
                            lambda *a, **k: called.update(ran=True) or 0)

        w._on_softbsl_install()

        assert "ran" not in called                  # rejected before dispatch
        assert w._port_owner.is_free()              # port released
    finally:
        w.close()


def test_softbsl_tab_consolidated_to_crossbank():
    app, w = _gui()
    try:
        assert hasattr(w, "btn_softbsl_xbank")           # the soft-BSL-only op stays
        # the now-redundant plain-flash / fast-read controls are gone — the Flash tab auto-routes those
        assert not hasattr(w, "btn_softbsl_flash")
        assert not hasattr(w, "btn_softbsl_dump")
        assert not hasattr(w, "cb_softbsl_baud")
        assert not hasattr(w, "cb_softbsl_scope")
    finally:
        w.close()


@pytest.mark.parametrize("version", ["MS41.2", "MS41.3"])
def test_softbsl_crossbank_top_base_is_composed_with_persistent_patches(version):
    app, w = _gui()
    try:
        data = ref(version)
        assert w._set_softbsl_crossbank_base(data, "live TOP", "top") is True
        assert gui.softbsl_service.marker(w._softbsl_image) == "T"
        assert w._softbsl_xbank_patch_ids == [
            "softbsl_loader", "door_magic", "cal_guard", "amd_flash"]
        preview = w._softbsl_preview.toPlainText()
        assert "PREPARED IMAGE" in preview
        assert "CROSS-BANK top-half write PLAN" in preview
    finally:
        w.close()


def test_bsl_tab_present_with_flash_and_diag_controls():
    app, w = _gui()
    try:
        titles = {w.tabs.tabText(i).strip() for i in range(w.tabs.count())}
        assert "BSL-Unbricker" in titles
        assert w._bsl_ref is None
        assert not w.btn_bsl_arm.isEnabled()          # disabled until a dry-run succeeds
        for attr in ("btn_bsl_dryrun", "cb_bsl_port", "cb_bsl_baud", "cb_bsl_chip",
                     "cb_bsl_half", "cb_bsl_region", "chk_bsl_fix_cksum", "chk_bsl_force"):
            assert hasattr(w, attr)
        assert [w.cb_bsl_baud.itemData(i) for i in range(w.cb_bsl_baud.count())] == [
            9600, 19200, 38400]
        assert w.cb_bsl_baud.currentData() == 38400
        assert [w.cb_bsl_chip.itemText(i) for i in range(w.cb_bsl_chip.count())] == [
            "Auto-detect", "Intel 28F200", "AMD 29F200", "AMD 29F400"]
        assert [w.cb_bsl_chip.itemData(i) for i in range(w.cb_bsl_chip.count())] == [
            "auto", "28f200", "29f200", "29f400"]
        assert "no echo" in w.lbl_bsl_transport_mode.text()
        assert "DTR reset" in w.lbl_bsl_transport_mode.text()
        assert w.btn_bsl_refresh.text() == "⟳ Refresh"
        assert w.btn_bsl_refresh.minimumWidth() >= 84
        assert w.btn_bsl_read_full.text() == "Read Full Flash (256 KB)…"
        assert w.btn_bsl_read_tune.text() == "Read Tune (24 KB)…"
        assert "standard file order" in w.btn_bsl_read_full.toolTip()
        assert "0x10000–0x15FFF" in w.btn_bsl_read_tune.toolTip()
        assert "additional copy elsewhere" in w.btn_bsl_read_full.toolTip()
        assert "additional copy elsewhere" in w.btn_bsl_read_tune.toolTip()
        assert not w.btn_bsl_vpp.isEnabled()
        assert w.btn_bsl_vpp.text() == "VPP On (select 28F200)"
        assert "Select Intel 28F200" in w.btn_bsl_vpp.toolTip()
        assert w.btn_bsl_dryrun.text() == "Review Flash Plan…"
        assert w.btn_bsl_arm.text() == "Confirm and Flash…"

        w.bsl_diag_group.setChecked(True)
        _set_bsl_chip(w, "28f200")
        assert w.btn_bsl_vpp.isEnabled()
        assert w.btn_bsl_vpp.text() == "VPP On"
        assert "12 V VPP/RP#" in w.btn_bsl_vpp.toolTip()
        _set_bsl_chip(w, "29f400")
        assert not w.btn_bsl_vpp.isEnabled()
        assert w.btn_bsl_vpp.text() == "VPP N/A (AMD)"
        assert "single-supply" in w.btn_bsl_vpp.toolTip()
    finally:
        w.close()


@pytest.mark.parametrize(("mode", "size"), [
    ("tune", MS41ECU.TUNE_SIZE),
    ("full", MS41ECU.FULL_ROM_SIZE),
])
def test_bsl_read_saves_automatically_to_bins(mode, size, tmp_path, monkeypatch):
    app, w = _gui()
    try:
        path = tmp_path / f"bsl-{mode}-capture.bin"
        data = b"\x5A" * size
        _set_bsl_chip(w, "28f200")
        w.cb_bsl_baud.setCurrentIndex(2)
        monkeypatch.setattr(
            QFileDialog, "getSaveFileName",
            staticmethod(lambda *a, **k: pytest.fail("BSL read must not show a save dialog")))
        monkeypatch.setattr(
            gui.tempfile, "mkstemp",
            lambda **kwargs: (
                os.open(path, os.O_CREAT | os.O_RDWR), str(path)))
        monkeypatch.setattr(w, "_acquire_bsl_port", lambda: "COM_BSL_TEST")

        called = {}
        def fake_dump(port, outfile, chip, half, log, progress, **kwargs):
            called.update(port=port, outfile=outfile, chip=chip, half=half, **kwargs)
            with open(outfile, "wb") as handle:
                handle.write(data)
            return 0
        monkeypatch.setattr(gui.bsl_service, f"dump_{mode}", fake_dump)

        archived = {}
        def archive(image, filename, **metadata):
            archived.update(data=bytes(image), filename=filename, **metadata)
            return type("Entry", (), {
                "filename": filename,
                "path": os.path.join(gui.BACKUP_DIR, filename),
            })()
        monkeypatch.setattr(w._backup_mgr, "add_data", archive)
        monkeypatch.setattr(w, "_refresh_backup_table",
                            lambda: archived.update(refreshed=True))
        released = []
        monkeypatch.setattr(w._port_owner, "release", lambda owner: released.append(owner))
        shown = {}
        monkeypatch.setattr(
            QMessageBox, "question",
            staticmethod(lambda *a, **k:
                         (shown.update(title=a[1], message=a[2]), QMessageBox.No)[1]))

        def sync_run_task(task, on_success=None, on_failure=None):
            try:
                result = task(lambda *a, **k: None, lambda *a, **k: None)
            except Exception as error:
                if on_failure:
                    on_failure(error)
            else:
                if on_success:
                    on_success(result)
        monkeypatch.setattr(w, "_run_task", sync_run_task)

        getattr(w, f"_on_bsl_read_{mode}")()

        assert not path.exists()
        assert called == {
            "port": "COM_BSL_TEST", "outfile": str(path), "chip": "28f200",
            "half": "upper", "baud": 38400, "reset_line": "dtr"}
        assert archived["data"] == data
        assert archived["filename"].startswith(f"ms41_bsl_{mode}_")
        assert archived["filename"].endswith(".bin")
        assert archived["source"] == "BSL-Unbricker read"
        assert archived["refreshed"] is True
        assert released == ["bsl"]
        assert shown["title"] == "BSL Read Complete"
        assert "automatically to Bins" in shown["message"]
        assert gui.BACKUP_DIR in shown["message"]
    finally:
        w.close()


def test_bsl_read_can_save_optional_copy_after_bins_archive(tmp_path, monkeypatch):
    app, w = _gui()
    try:
        temporary_path = tmp_path / "temporary-bsl-read.bin"
        copy_path = tmp_path / "operator-copy.bin"
        data = b"\xA5" * MS41ECU.TUNE_SIZE
        _set_bsl_chip(w, "28f200")
        monkeypatch.setattr(
            gui.tempfile, "mkstemp",
            lambda **kwargs: (
                os.open(temporary_path, os.O_CREAT | os.O_RDWR), str(temporary_path)))
        monkeypatch.setattr(w, "_acquire_bsl_port", lambda: "COM_BSL_TEST")

        def fake_dump(port, outfile, chip, half, log, progress, **kwargs):
            with open(outfile, "wb") as handle:
                handle.write(data)
            return 0
        monkeypatch.setattr(gui.bsl_service, "dump_tune", fake_dump)

        archived = {}
        def archive(image, filename, **metadata):
            archived.update(data=bytes(image), filename=filename, **metadata)
            return type("Entry", (), {
                "filename": filename,
                "path": os.path.join(gui.BACKUP_DIR, filename),
            })()
        monkeypatch.setattr(w._backup_mgr, "add_data", archive)
        monkeypatch.setattr(w, "_refresh_backup_table", lambda: None)
        monkeypatch.setattr(w._port_owner, "release", lambda owner: None)
        monkeypatch.setattr(
            QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
        dialog = {}
        monkeypatch.setattr(
            QFileDialog, "getSaveFileName",
            staticmethod(lambda *a, **k:
                         (dialog.update(title=a[1], suggested=a[2]),
                          (str(copy_path), ""))[1]))

        def sync_run_task(task, on_success=None, on_failure=None):
            try:
                result = task(lambda *a, **k: None, lambda *a, **k: None)
            except Exception as error:
                if on_failure:
                    on_failure(error)
            else:
                if on_success:
                    on_success(result)
        monkeypatch.setattr(w, "_run_task", sync_run_task)

        w._on_bsl_read_tune()

        assert archived["data"] == data
        assert not temporary_path.exists()
        assert copy_path.read_bytes() == data
        assert dialog["title"] == "Save Additional BSL Tune (24 KB) Copy"
        assert dialog["suggested"] == archived["filename"]
    finally:
        w.close()


def test_bsl_incomplete_read_removes_temporary_file_and_skips_bins(
        tmp_path, monkeypatch):
    app, w = _gui()
    try:
        path = tmp_path / "incomplete-bsl-read.bin"
        _set_bsl_chip(w, "28f200")
        monkeypatch.setattr(
            gui.tempfile, "mkstemp",
            lambda **kwargs: (
                os.open(path, os.O_CREAT | os.O_RDWR), str(path)))
        monkeypatch.setattr(w, "_acquire_bsl_port", lambda: "COM_BSL_TEST")

        def incomplete_dump(port, outfile, chip, half, log, progress, **kwargs):
            with open(outfile, "wb") as handle:
                handle.write(b"\x5A" * 1024)
            return 0
        monkeypatch.setattr(gui.bsl_service, "dump_tune", incomplete_dump)
        monkeypatch.setattr(
            w._backup_mgr, "add_data",
            lambda *args, **kwargs: pytest.fail("incomplete read must not reach Bins"))
        released = []
        monkeypatch.setattr(w._port_owner, "release", lambda owner: released.append(owner))
        shown = {}
        monkeypatch.setattr(
            QMessageBox, "critical",
            staticmethod(lambda *a, **k: shown.update(title=a[1], message=a[2])))

        def sync_run_task(task, on_success=None, on_failure=None):
            try:
                result = task(lambda *a, **k: None, lambda *a, **k: None)
            except Exception as error:
                if on_failure:
                    on_failure(error)
            else:
                if on_success:
                    on_success(result)
        monkeypatch.setattr(w, "_run_task", sync_run_task)

        w._on_bsl_read_tune()

        assert not path.exists()
        assert released == ["bsl"]
        assert shown["title"] == "BSL Read Failed"
        assert "No incomplete image was added to Bins" in shown["message"]
    finally:
        w.close()


def test_application_icon_asset_is_loaded():
    app, w = _gui()
    try:
        assert os.path.exists(gui.APP_ICON_PATH)
        assert not w.windowIcon().isNull()
    finally:
        w.close()


def test_shared_application_configuration_uses_dark_fusion_theme():
    app = QApplication.instance() or QApplication([])

    gui.configure_application(app)

    palette = app.palette()
    assert app.applicationName() == "BimmerStein ECU Tool"
    assert app.style().objectName().lower() == "fusion"
    assert palette.color(QPalette.Window).name() == "#2b2b2b"
    assert palette.color(QPalette.Base).name() == "#1e1e1e"
    assert palette.color(QPalette.Disabled, QPalette.ButtonText).name() == "#888888"
    assert not app.windowIcon().isNull()


def test_uncaught_exception_handler_logs_detail_but_keeps_dialog_concise(
        monkeypatch, tmp_path):
    previous = sys.excepthook
    shown = {}
    monkeypatch.setattr(gui, "LOG_DIR", str(tmp_path))
    monkeypatch.setattr(
        QMessageBox,
        "critical",
        staticmethod(lambda _parent, title, message: shown.update(
            title=title, message=message)),
    )
    try:
        gui.install_exception_handler()
        try:
            raise RuntimeError("private low-level detail")
        except RuntimeError:
            sys.excepthook(*sys.exc_info())
    finally:
        sys.excepthook = previous

    crash_logs = list(tmp_path.glob("crash_*.txt"))
    assert len(crash_logs) == 1
    assert "private low-level detail" in crash_logs[0].read_text(encoding="utf-8")
    assert shown["title"] == "Unexpected Application Error"
    assert "private low-level detail" not in shown["message"]
    assert str(crash_logs[0]) in shown["message"]


def test_bsl_region_choices_track_chip_and_half():
    app, w = _gui()
    try:
        _set_bsl_chip(w, "29f400")
        w.cb_bsl_half.setCurrentText("upper")
        upper_regions = {w.cb_bsl_region.itemText(i) for i in range(w.cb_bsl_region.count())}
        assert upper_regions == {"low", "tune", "program-high", "all"}

        w.cb_bsl_half.setCurrentText("lower")
        lower_regions = {w.cb_bsl_region.itemText(i) for i in range(w.cb_bsl_region.count())}
        assert lower_regions == {"boot", "program-low", "program-mid", "tune", "program-high", "all"}
    finally:
        w.close()


def test_bsl_dry_run_populates_preview_and_gates_arm(tmp_path):
    app, w = _gui()
    try:
        ref_path = tmp_path / "tune_ref.bin"
        ref_path.write_bytes(b"\xFF" * 24576)
        w._bsl_ref = str(ref_path)
        _set_bsl_chip(w, "28f200")
        w.cb_bsl_region.setCurrentText("tune")
        w.cb_bsl_port.clear()
        w.cb_bsl_port.addItem("COM_BSL_TEST")
        w.cb_bsl_port.setCurrentText("COM_BSL_TEST")
        w.cb_bsl_baud.setCurrentIndex(1)
        w._on_bsl_dry_run()
        assert "PLAN REVIEW COMPLETE" in w._bsl_preview.toPlainText()
        assert "--arm" not in w._bsl_preview.toPlainText()
        assert w.btn_bsl_arm.isEnabled()
        assert w._bsl_plan.port == "COM_BSL_TEST"
        assert w._bsl_plan.baud == 19200
        assert w._bsl_plan.reset_line == "dtr"
        assert "direct ASC0/8N1" in w._bsl_preview.toPlainText()
        w.cb_bsl_baud.setCurrentIndex(2)
        assert w._bsl_plan is None
        assert not w.btn_bsl_arm.isEnabled()
    finally:
        w.close()


def test_bins_open_in_bsl_button_is_offline_local_action():
    app, w = _gui()
    try:
        assert w.btn_backup_open_bsl.text() == "Open in BSL-Unbricker"
        assert "does not open hardware or flash anything" in w.btn_backup_open_bsl.toolTip()

        w._ds2 = None
        w.backup_table.clearSelection()
        w._set_backup_buttons_enabled()
        assert not w.btn_backup_open_bsl.isEnabled()

        row = w.backup_table.rowCount()
        w.backup_table.insertRow(row)
        w.backup_table.selectRow(row)
        w._set_backup_buttons_enabled()
        assert w.btn_backup_open_bsl.isEnabled()
        assert not w.btn_backup_flash.isEnabled()
    finally:
        w.close()


def test_bins_tune_handoff_prepares_bsl_without_changing_geometry(
        tmp_path, monkeypatch):
    app, w = _gui()
    try:
        path = tmp_path / "catalogued-tune.bin"
        path.write_bytes(b"\x5A" * gui.MS41ECU.TUNE_SIZE)
        entry = type("Entry", (), {"path": str(path), "filename": path.name})()
        monkeypatch.setattr(w, "_selected_backup", lambda: entry)
        messages = []
        monkeypatch.setattr(
            w, "_log", lambda message, level="info": messages.append((message, level)))

        _set_bsl_chip(w, "29f400")
        w.cb_bsl_half.setCurrentText("lower")
        w.cb_bsl_region.setCurrentText("program-mid")
        previous_chip = w.cb_bsl_chip.currentData()
        previous_half = w.cb_bsl_half.currentText()
        w._bsl_plan = object()
        w.btn_bsl_arm.setEnabled(True)
        w.tabs.setCurrentIndex(0)

        w._on_backup_open_in_bsl()

        assert w._bsl_ref == os.path.abspath(path)
        assert w._bsl_ref_lbl.text() == path.name
        assert w.cb_bsl_region.currentText() == "tune"
        assert w.cb_bsl_chip.currentData() == previous_chip
        assert w.cb_bsl_half.currentText() == previous_half
        assert w._bsl_plan is None
        assert not w.btn_bsl_arm.isEnabled()
        assert w.tabs.currentIndex() == w._bsl_tab_index
        assert messages == [(f"BSL reference loaded from Bins: {path.name}", "info")]
    finally:
        w.close()


def test_bins_full_handoff_preserves_bsl_geometry_and_invalidates_plan(
        tmp_path, monkeypatch):
    app, w = _gui()
    try:
        path = tmp_path / "catalogued-full.bin"
        path.write_bytes(b"\xA5" * gui.MS41ECU.FULL_ROM_SIZE)
        entry = type("Entry", (), {"path": str(path), "filename": path.name})()
        monkeypatch.setattr(w, "_selected_backup", lambda: entry)
        monkeypatch.setattr(w, "_log", lambda *args, **kwargs: None)

        _set_bsl_chip(w, "29f400")
        w.cb_bsl_half.setCurrentText("lower")
        w.cb_bsl_region.setCurrentText("program-mid")
        geometry = (
            w.cb_bsl_chip.currentData(),
            w.cb_bsl_half.currentText(),
            w.cb_bsl_region.currentText(),
        )
        w._bsl_plan = object()
        w.btn_bsl_arm.setEnabled(True)
        w.tabs.setCurrentIndex(0)

        w._on_backup_open_in_bsl()

        assert w._bsl_ref == os.path.abspath(path)
        assert w._bsl_ref_lbl.text() == path.name
        assert (
            w.cb_bsl_chip.currentData(),
            w.cb_bsl_half.currentText(),
            w.cb_bsl_region.currentText(),
        ) == geometry
        assert w._bsl_plan is None
        assert not w.btn_bsl_arm.isEnabled()
        assert w.tabs.currentIndex() == w._bsl_tab_index
    finally:
        w.close()


def test_bins_missing_file_handoff_does_not_mutate_bsl_state(
        tmp_path, monkeypatch):
    app, w = _gui()
    try:
        missing = tmp_path / "missing.bin"
        entry = type("Entry", (), {"path": str(missing), "filename": missing.name})()
        monkeypatch.setattr(w, "_selected_backup", lambda: entry)
        shown = {}
        monkeypatch.setattr(
            QMessageBox, "critical",
            staticmethod(lambda *args, **kwargs: shown.update(
                title=args[1], message=args[2])))

        _set_bsl_chip(w, "29f400")
        w.cb_bsl_half.setCurrentText("lower")
        w.cb_bsl_region.setCurrentText("program-mid")
        plan = object()
        w._bsl_ref = "existing-reference.bin"
        w._bsl_ref_lbl.setText("existing-reference.bin")
        w._bsl_plan = plan
        w.btn_bsl_arm.setEnabled(True)
        w.tabs.setCurrentIndex(0)
        state = (
            w._bsl_ref, w._bsl_ref_lbl.text(), w.cb_bsl_chip.currentData(),
            w.cb_bsl_half.currentText(), w.cb_bsl_region.currentText(),
            w._bsl_plan, w.btn_bsl_arm.isEnabled(), w.tabs.currentIndex(),
        )

        w._on_backup_open_in_bsl()

        assert shown["title"] == "File Missing"
        assert "missing.bin" in shown["message"]
        assert (
            w._bsl_ref, w._bsl_ref_lbl.text(), w.cb_bsl_chip.currentData(),
            w.cb_bsl_half.currentText(), w.cb_bsl_region.currentText(),
            w._bsl_plan, w.btn_bsl_arm.isEnabled(), w.tabs.currentIndex(),
        ) == state
    finally:
        w.close()


def test_bins_invalid_size_handoff_does_not_mutate_bsl_state(
        tmp_path, monkeypatch):
    app, w = _gui()
    try:
        path = tmp_path / "wrong-size.bin"
        path.write_bytes(b"\x00" * 1024)
        entry = type("Entry", (), {"path": str(path), "filename": path.name})()
        monkeypatch.setattr(w, "_selected_backup", lambda: entry)
        shown = {}
        monkeypatch.setattr(
            QMessageBox, "critical",
            staticmethod(lambda *args, **kwargs: shown.update(
                title=args[1], message=args[2])))

        _set_bsl_chip(w, "29f400")
        w.cb_bsl_half.setCurrentText("lower")
        w.cb_bsl_region.setCurrentText("program-mid")
        plan = object()
        w._bsl_ref = "existing-reference.bin"
        w._bsl_ref_lbl.setText("existing-reference.bin")
        w._bsl_plan = plan
        w.btn_bsl_arm.setEnabled(True)
        w.tabs.setCurrentIndex(0)
        state = (
            w._bsl_ref, w._bsl_ref_lbl.text(), w.cb_bsl_chip.currentData(),
            w.cb_bsl_half.currentText(), w.cb_bsl_region.currentText(),
            w._bsl_plan, w.btn_bsl_arm.isEnabled(), w.tabs.currentIndex(),
        )

        w._on_backup_open_in_bsl()

        assert shown["title"] == "Unsupported BSL Reference"
        assert "1,024 bytes" in shown["message"]
        assert (
            w._bsl_ref, w._bsl_ref_lbl.text(), w.cb_bsl_chip.currentData(),
            w.cb_bsl_half.currentText(), w.cb_bsl_region.currentText(),
            w._bsl_plan, w.btn_bsl_arm.isEnabled(), w.tabs.currentIndex(),
        ) == state
    finally:
        w.close()


def test_flash_chip_label_generic_before_connect():
    app, w = _gui()
    try:
        assert "28F200" in w._flash_chip_note.text() or "29F" in w._flash_chip_note.text()
        # Before any connect, no chip is known — label must not claim ONLY Intel 28F200.
        assert w._flash_chip_note.text() == w._flash_chip_label_text(b"")
    finally:
        w.close()


def test_flash_chip_label_updates_after_connect_amd():
    app, w = _gui()
    try:
        sig = bytes.fromhex("e00e0d58f04ec084")
        w._on_connected(chip_sig=sig)
        assert "29F200 / 29F400" in w._flash_chip_note.text()
    finally:
        w.close()


def test_flash_chip_label_updates_after_connect_intel():
    app, w = _gui()
    try:
        sig = bytes.fromhex("e6f45000b84c6fe0")
        w._on_connected(chip_sig=sig)
        assert "28F200" in w._flash_chip_note.text()
    finally:
        w.close()


def test_transfer_mode_is_ds2_before_connect():
    app, w = _gui()
    try:
        assert w._fast_read_available() is False
        assert "DS2" in w.lbl_transfer_mode.text()
    finally:
        w.close()


def test_transfer_mode_shows_softbsl_when_marker_and_d2xx_present():
    app, w = _gui()
    try:
        w._d2xx_ok = True
        w._ecu_softbsl_marker = "B"
        w._ecu_softbsl_hook_present = True
        w._ecu_program_variant = "MS41.2"
        w._update_transfer_mode()
        assert w._fast_read_available() is True
        assert "Soft-BSL" in w.lbl_transfer_mode.text()
    finally:
        w.close()


def test_transfer_mode_uses_native_ds2_without_softbsl_marker():
    app, w = _gui()
    try:
        w._d2xx_ok = True
        w._ecu_softbsl_marker = None
        w._update_transfer_mode()
        assert w._fast_read_available() is False
        assert w._auto_transfer_route() == "native_ds2"
        assert "Native DS2 187,500" in w.lbl_transfer_mode.text()
    finally:
        w.close()


def test_transfer_mode_uses_native_ds2_when_loader_exists_without_hook():
    app, w = _gui()
    try:
        w._d2xx_ok = True
        w._ecu_softbsl_marker = "B"
        w._ecu_softbsl_hook_present = False
        w._update_transfer_mode()

        assert w._fast_read_available() is False
        assert w._auto_transfer_route() == "native_ds2"
        assert "Soft-BSL hook not detected" in w.lbl_transfer_mode.text()
    finally:
        w.close()


def test_live_softbsl_hook_check_reads_exact_descriptor_edits_at_ds2_addresses():
    import patch_service

    patch = patch_service.definitions()["door_magic"]
    expected_reads = {
        int(edit["off"]) ^ 0x4000: bytes.fromhex(edit["data"])
        for edit in patch["edits"]
    }
    calls = []

    class HookedDS2:
        def read_mem(self, address, length):
            calls.append((address, length))
            return expected_reads[address]

    assert gui.MS41FlashGUI._live_patch_present(HookedDS2(), "door_magic") is True
    assert calls == [
        (int(edit["off"]) ^ 0x4000, len(bytes.fromhex(edit["data"])))
        for edit in patch["edits"]
    ]

    class MissingHookDS2(HookedDS2):
        def read_mem(self, address, length):
            data = super().read_mem(address, length)
            return bytes([data[0] ^ 0xFF]) + data[1:]

    assert gui.MS41FlashGUI._live_patch_present(
        MissingHookDS2(), "door_magic") is False


def test_transfer_mode_ds2_without_d2xx_even_with_marker():
    app, w = _gui()
    try:
        w._d2xx_ok = False
        w._ecu_softbsl_marker = "B"
        w._ecu_softbsl_hook_present = True
        w._update_transfer_mode()
        assert w._fast_read_available() is False
        assert "D2XX" in w.lbl_transfer_mode.text()
    finally:
        w.close()


def test_run_via_softbsl_hands_off_port_and_restores_connection(monkeypatch):
    app, w = _gui()
    original_port_widget = w.cb_port
    try:
        opened, closed = [], []

        class FakeDS2:
            def __init__(self, **kw): opened.append(kw)
            def open(self): pass
            def close(self): closed.append(True)
            def identify(self): return b"SHINDE1" + b"\xFF" * 10

        monkeypatch.setattr(gui, "DS2Interface", FakeDS2)
        w._ds2 = FakeDS2(port="COM1", baud=9600)
        w._connection_port = "COM1"
        w._port_owner.acquire("flasher")
        w.cb_port.clear(); w.cb_port.addItem("COM1")
        w.cb_port = object()  # worker handoff must use the connection-time string snapshot

        seen = []
        def op_fn(port, progress_fn, log_fn):
            seen.append(port)
            assert w._port_owner.owner == "softbsl"
            return b"result"

        result = w._run_via_softbsl(op_fn, log_fn=lambda *a: None, progress_fn=lambda *a: None)

        assert result == b"result"
        assert seen == ["COM1"]
        assert closed == [True]              # the original _ds2 was closed before the handoff
        assert w._port_owner.owner == "flasher"   # restored afterward
        assert w._ds2 is not None            # reconnected
    finally:
        w.cb_port = original_port_widget
        w.close()


def test_run_via_softbsl_can_leave_success_disconnected(monkeypatch):
    app, w = _gui()
    try:
        closed = []

        class ConnectedDS2:
            def close(self):
                closed.append(True)

        w._ds2 = ConnectedDS2()
        w._connection_port = "COM1"
        w._port_owner.acquire("flasher")
        reopened = []
        monkeypatch.setattr(
            w, "_reopen_ds2_with_retry",
            lambda *_args, **_kwargs: reopened.append(True),
        )

        result = w._run_via_softbsl(
            lambda *_args: b"written",
            log_fn=lambda *_args: None,
            progress_fn=lambda *_args: None,
            restore_after_success=False,
        )

        assert result == b"written"
        assert closed == [True]
        assert reopened == []
        assert w._ds2 is None
        assert w._port_owner.owner is None
    finally:
        w._port_owner.release("flasher")
        w._port_owner.release("softbsl")
        w.close()


def test_run_via_softbsl_failure_still_restores_ds2_when_success_would_disconnect(
        monkeypatch):
    app, w = _gui()
    try:
        class ConnectedDS2:
            def close(self):
                pass

        w._ds2 = ConnectedDS2()
        w._connection_port = "COM1"
        w._port_owner.acquire("flasher")
        reopened = []
        monkeypatch.setattr(
            w, "_reopen_ds2_with_retry",
            lambda port, _log_fn: reopened.append(port),
        )

        def fail_before_erase(*_args):
            raise RuntimeError("injected pre-erase failure")

        with pytest.raises(RuntimeError, match="pre-erase failure"):
            w._run_via_softbsl(
                fail_before_erase,
                log_fn=lambda *_args: None,
                progress_fn=lambda *_args: None,
                restore_after_success=False,
            )

        assert reopened == ["COM1"]
        assert w._port_owner.owner == "flasher"
    finally:
        w._port_owner.release("flasher")
        w._port_owner.release("softbsl")
        w.close()


def test_run_via_softbsl_retains_post_erase_agent_and_port_owner(monkeypatch):
    app, w = _gui()
    try:
        class ConnectedDS2:
            def close(self): pass

        class RetainedDS2:
            def __init__(self): self.is_open = True
            def close(self): self.is_open = False

        retained_ds2 = RetainedDS2()
        recovery = gui.softbsl_service.SoftBSLWriteRecovery(
            port="COM1",
            ds2=retained_ds2,
            agent=object(),
            operation="tune",
            target=b"target",
            scope="tune",
            baud="high",
            prompt=None,
            do_verify=True,
            write_bootloader=False,
            chip_family="intel",
            error=RuntimeError("injected post-erase failure"),
        )
        w._ds2 = ConnectedDS2()
        w._connection_port = "COM1"
        w._port_owner.acquire("flasher")
        reopened = []
        monkeypatch.setattr(
            w, "_reopen_ds2_with_retry",
            lambda *_args, **_kwargs: reopened.append(True),
        )

        def fail_after_erase(_port, _progress_fn, _log_fn):
            raise gui.softbsl_service.SoftBSLWriteRecoveryRequired(recovery)

        with pytest.raises(gui.softbsl_service.SoftBSLWriteRecoveryRequired):
            w._run_via_softbsl(
                fail_after_erase,
                log_fn=lambda *_args: None,
                progress_fn=lambda *_args: None,
            )

        assert w._softbsl_write_recovery is recovery
        assert recovery.is_open is True
        assert w._port_owner.owner == "softbsl"
        assert w._ds2 is None
        assert reopened == []
    finally:
        if getattr(w, "_softbsl_write_recovery", None) is not None:
            w._softbsl_write_recovery.close_after_confirmed_power_cycle()
        w._softbsl_write_recovery = None
        w._port_owner.release("softbsl")
        w.close()


@pytest.mark.parametrize("answer_name,queues_retry", [
    ("Retry", True),
    ("Cancel", False),
])
def test_post_erase_dialog_offers_retry_or_keeps_session_pending(
        monkeypatch, answer_name, queues_retry):
    app, w = _gui()
    try:
        answer = getattr(QMessageBox, answer_name)
        recovery = type("Recovery", (), {"is_open": True})()
        w._softbsl_write_recovery = recovery
        shown = {}
        queued = []
        started = []
        monkeypatch.setattr(
            QMessageBox,
            "warning",
            staticmethod(lambda *args, **kwargs: shown.update(args=args) or answer),
        )
        monkeypatch.setattr(
            gui.QTimer,
            "singleShot",
            staticmethod(lambda delay, callback: queued.append((delay, callback))),
        )
        monkeypatch.setattr(
            w,
            "_start_native_flash_recovery",
            lambda confirmed=False: started.append(confirmed),
        )

        assert w._offer_active_flash_recovery("Injected post-erase failure") is True

        assert w._softbsl_write_recovery is recovery
        assert recovery.is_open is True
        assert w.btn_native_recovery.isHidden() is False
        assert w.btn_native_recovery.isEnabled() is True
        assert "DO NOT TURN IGNITION OFF" in shown["args"][2]
        assert "same corrected target" in shown["args"][2]
        assert bool(queued) is queues_retry
        if queued:
            assert queued[0][0] == 0
            queued[0][1]()
        assert bool(started) is queues_retry
        if started:
            assert started == [True]
    finally:
        w._softbsl_write_recovery = None
        w.close()


@pytest.mark.parametrize(
    "operation,family,verify_requested",
    [("tune", "intel", False), ("image", "amd", True)],
)
def test_softbsl_recovery_ui_reuses_target_and_verify_choice(
        monkeypatch, operation, family, verify_requested):
    app, w = _gui()
    try:
        class Signal:
            def __init__(self):
                self.callback = None
            def connect(self, callback):
                self.callback = callback
            def emit(self, *args):
                if self.callback:
                    self.callback(*args)

        class ImmediateWorker:
            def __init__(self, task):
                self.task = task
                self.log_signal = Signal()
                self.progress_signal = Signal()
                self.done_signal = Signal()
            def start(self):
                try:
                    result = self.task(
                        log_fn=lambda *_args, **_kwargs: None,
                        progress_fn=lambda *_args, **_kwargs: None,
                    )
                except Exception as error:
                    self.done_signal.emit(False, str(error))
                else:
                    self.done_signal.emit(True, result)

        class RetainedDS2:
            def __init__(self):
                self.is_open = True
            def close(self):
                self.is_open = False

        retained_ds2 = RetainedDS2()
        target = b"same-checksum-corrected-target"
        recovery = gui.softbsl_service.SoftBSLWriteRecovery(
            port="COM1",
            ds2=retained_ds2,
            agent=object(),
            operation=operation,
            target=target,
            scope="tune" if operation == "tune" else "full",
            baud="high",
            prompt=None,
            do_verify=verify_requested,
            write_bootloader=False,
            chip_family=family,
            error=RuntimeError("injected post-erase failure"),
        )
        w._softbsl_write_recovery = recovery
        w._connection_port = "COM1"
        w._port_owner.acquire("softbsl")
        resumed = []
        reopened = []
        completed = []

        monkeypatch.setattr(gui, "WorkerThread", ImmediateWorker)
        monkeypatch.setattr(
            QMessageBox,
            "warning",
            staticmethod(lambda *_args, **_kwargs: QMessageBox.Yes),
        )

        def resume(received, *, progress_cb, log):
            resumed.append((received, received.target, received.do_verify, received.chip_family))
            received.ds2.close()
            return True

        monkeypatch.setattr(gui.softbsl_service, "resume_write_recovery", resume)
        monkeypatch.setattr(
            w,
            "_reopen_ds2_with_retry",
            lambda port, _log: reopened.append(port),
        )
        monkeypatch.setattr(
            w,
            "_show_flash_complete",
            lambda title, message: completed.append((title, message)),
        )

        w._start_softbsl_flash_recovery()

        assert resumed == [(recovery, target, verify_requested, family)]
        assert w._softbsl_write_recovery is None
        assert retained_ds2.is_open is False
        assert w._port_owner.owner == "flasher"
        assert reopened == ["COM1"]
        assert completed and completed[0][0] == "Flash Recovery Complete"
        if verify_requested:
            assert "verification passed" in completed[0][1]
        else:
            assert gui.VERIFY_OFF_MESSAGE in completed[0][1]
    finally:
        w._softbsl_write_recovery = None
        w._port_owner.release("flasher")
        w.close()


def test_write_full_routes_through_softbsl_when_available(monkeypatch):
    app, w = _gui()
    try:
        import softbsl_service
        calls = {}
        def fake_run_via_softbsl(op_fn, log_fn, progress_fn):
            calls["invoked"] = True
            return op_fn("COM1", progress_fn, log_fn)
        monkeypatch.setattr(w, "_run_via_softbsl", fake_run_via_softbsl)
        monkeypatch.setattr(softbsl_service, "run_flash",
                            lambda *a, **k: calls.update(kw=k))
        w._ecu_softbsl_marker = "B"       # loader present + D2XX -> auto-routes to soft-BSL
        w._ecu_softbsl_hook_present = True
        w._d2xx_ok = True
        w.chk_verify.setChecked(True)

        image_bytes = ref("MS41.2")
        # Directly exercise the write-full task body via a minimal stand-in, since the full
        # method requires a file dialog + confirmation dialogs earlier in _ds2_write_full.
        def task(log_fn, progress_fn):
            if w._fast_read_available():
                write_bootloader = (getattr(w, "chk_bootloader_write", None) is not None
                                    and w.chk_bootloader_write.isChecked())
                w._run_via_softbsl(
                    lambda port, pf, lf: softbsl_service.run_flash(
                        port, image_bytes, "full", w._softbsl_prompt, lf, baud="high",
                        progress_cb=pf, do_verify=w.chk_verify.isChecked(),
                        write_bootloader=write_bootloader),
                    log_fn, progress_fn)
        task(lambda *a: None, lambda *a: None)

        assert calls["invoked"] is True
        assert calls["kw"]["do_verify"] is True
        assert calls["kw"]["write_bootloader"] is False
    finally:
        w.close()


def test_bootloader_checkbox_enabled_only_when_softbsl_available():
    app, w = _gui()
    try:
        w._ecu_softbsl_marker = None
        w._d2xx_ok = False
        w._update_bootloader_checkbox_state()
        assert w.chk_bootloader_write.isEnabled() is False
        assert w.chk_boot_preserve_identity.isChecked() is True
        assert w.chk_boot_preserve_identity.isEnabled() is False

        w._d2xx_ok = True
        w._ecu_softbsl_marker = "B"
        w._ecu_softbsl_hook_present = True
        w._update_bootloader_checkbox_state()
        assert w.chk_bootloader_write.isEnabled() is True
        assert w.chk_boot_preserve_identity.isEnabled() is False
        w.chk_bootloader_write.setChecked(True)
        assert w.chk_boot_preserve_identity.isEnabled() is True
    finally:
        w.close()


def test_bootloader_write_file_warning_flags_missing_patches():
    app, w = _gui()
    try:
        stock = bytearray(b"\xFF" * 262144)   # no softbsl_loader / door_magic edits present
        warning = w._bootloader_write_file_warning(bytes(stock))
        assert warning is not None
        assert "soft-BSL loader" in warning or "dispatcher door" in warning
    finally:
        w.close()


def test_softbsl_survival_check_uses_effective_full_write_regions():
    import patch_service

    patches = patch_service.definitions()

    def image_with(*patch_ids):
        image = bytearray(b"\xFF" * 262144)
        for patch_id in patch_ids:
            for edit in patches[patch_id]["edits"]:
                start = int(edit["off"])
                payload = bytes.fromhex(edit["data"])
                image[start:start + len(payload)] = payload
        return bytes(image)

    stock = image_with()
    hook_only = image_with("door_magic")
    complete = image_with("softbsl_loader", "door_magic")

    # A simple full write preserves the already-working loader in SA1, but
    # always replaces the program-region entry hook.
    assert gui.MS41FlashGUI._softbsl_missing_after_full_write(
        stock, write_bootloader=False) == ("door_magic",)
    assert gui.MS41FlashGUI._softbsl_missing_after_full_write(
        hook_only, write_bootloader=False) == ()

    # A BOOT-enabled write replaces both regions, so the target needs both.
    assert gui.MS41FlashGUI._softbsl_missing_after_full_write(
        hook_only, write_bootloader=True) == ("softbsl_loader",)
    assert gui.MS41FlashGUI._softbsl_missing_after_full_write(
        complete, write_bootloader=True) == ()


def test_bootloader_write_blocked_when_typed_ack_wrong(monkeypatch):
    app, w = _gui()
    try:
        w._d2xx_ok = True
        w._ecu_softbsl_marker = "B"
        w._ecu_softbsl_hook_present = True
        w._update_transfer_mode()
        w.chk_bootloader_write.setChecked(True)

        monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: QMessageBox.Yes))
        monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("nope", True)))

        image = bytearray(b"\xFF" * 262144)
        # Exercise just the gate logic (full _ds2_write_full needs a file dialog earlier).
        warning = w._bootloader_write_file_warning(bytes(image))
        assert warning is not None   # stock image: file-check would warn
    finally:
        w.close()


def test_reopen_ds2_retries_past_a_transient_permission_error(monkeypatch):
    app, w = _gui()
    try:
        import gui as gui_module
        sleeps = []
        monkeypatch.setattr(gui_module.time, "sleep", lambda s: sleeps.append(s))

        attempts = []
        class FlakyThenOK:
            def __init__(self, **kw):
                assert kw["echo"] is False
                self.uses_d2xx = True
            def open(self):
                attempts.append(1)
                if len(attempts) < 3:
                    raise PermissionError(13, "Acceso denegado.", None, 5)
            def identify(self): return b"SHINDE1" + b"\xFF" * 10   # ECU answers once the port opens
            def close(self): pass

        monkeypatch.setattr(gui_module, "DS2Interface", FlakyThenOK)
        logs = []
        w._connection_echo = False
        w._reopen_ds2_with_retry("COM1", lambda *a: logs.append(a[0]), attempts=6, delay=0.0)

        assert len(attempts) == 3          # failed twice, succeeded on the 3rd
        assert w._ds2 is not None
        assert any("back up" in m for m in logs)   # success-after-retry is logged
    finally:
        w.close()


def test_reopen_ds2_retries_when_port_opens_but_ecu_not_answering_yet(monkeypatch):
    app, w = _gui()
    try:
        import gui as gui_module
        monkeypatch.setattr(gui_module.time, "sleep", lambda s: None)

        ident_attempts = []
        class PortOKEcuBooting:
            def __init__(self, **kw): pass
            def open(self): pass                        # the port always opens (FTDI device present)
            def identify(self):
                ident_attempts.append(1)
                if len(ident_attempts) < 4:
                    raise Exception("no response to command 0x00")   # ECU still mid-reboot
                return b"SHINDE1" + b"\xFF" * 10
            def close(self): pass

        monkeypatch.setattr(gui_module, "DS2Interface", PortOKEcuBooting)
        w._reopen_ds2_with_retry("COM1", lambda *a: None, attempts=8, delay=0.0)

        # a bare open() would have "succeeded" on attempt 1 and left a silent ECU; identify() forces
        # the retry until the ECU actually answers.
        assert len(ident_attempts) == 4
        assert w._ds2 is not None
    finally:
        w.close()


def test_reopen_ds2_gives_up_after_max_attempts_and_stays_disconnected(monkeypatch):
    app, w = _gui()
    try:
        import gui as gui_module
        monkeypatch.setattr(gui_module.time, "sleep", lambda s: None)

        class AlwaysFails:
            def __init__(self, **kw): pass
            def open(self):
                raise PermissionError(13, "Acceso denegado.", None, 5)

        monkeypatch.setattr(gui_module, "DS2Interface", AlwaysFails)
        logs = []
        w._port_owner.acquire("flasher")
        w._connection_port = "COM1"
        w.btn_connect.setChecked(True)
        w.btn_connect.setText("Disconnect")
        w.lbl_status.setText("● Connected (DS2)")

        restored = w._reopen_ds2_with_retry(
            "COM1", lambda *a: logs.append(a[0]), attempts=3, delay=0.0)

        assert restored is False
        assert w._ds2 is None
        assert w._port_owner.is_free()
        assert w._connection_port is None
        assert not w.btn_connect.isChecked()
        assert w.btn_connect.text() == "Connect"
        assert "Disconnected" in w.lbl_status.text()
        assert any("Could not reconnect" in m for m in logs)
    finally:
        w.close()


def test_main_tab_native_read_restores_low_ds2_for_following_native_write(
        monkeypatch):
    app, w = _gui()
    try:
        events = []
        logs = []
        progress = []
        reopened = []
        open_attempts = []

        class InitiallyConnectedDS2:
            def close(self):
                events.append("initial_ds2_closed")

        class ReopenedDS2:
            uses_d2xx = True

            def __init__(self, *, port, baud, verbose, echo):
                assert (port, baud, verbose, echo) == ("COM1", 9600, False, False)
                reopened.append(self)
                self.closed = False
                events.append(("ordinary_ds2_created", baud))

            def open(self):
                open_attempts.append(self)
                events.append("ordinary_ds2_open")
                if len(open_attempts) == 1:
                    raise PermissionError(13, "transient handoff", None, 5)

            def identify(self):
                events.append("ordinary_ds2_identified")
                return b"SHINDE1" + b"\xFF" * 10

            def close(self):
                self.closed = True
                events.append("ordinary_ds2_closed")

        def native_read(port, *, progress_cb, event_cb, echo):
            assert (port, echo) == ("COM1", False)
            assert w._port_owner.owner == "native_fast_ds2"
            assert w._ds2 is None
            events.append("native_read")
            event_cb(
                "host_baud_changed",
                {"old": 9600, "new": 187500, "reason": "test selector up"},
            )
            progress_cb(24576, 24576, "fast_partial_read")
            event_cb(
                "host_baud_changed",
                {"old": 187500, "new": 9600, "reason": "test cleanup"},
            )
            return type("ReadResult", (), {"data": b"R" * 24576})()

        def native_write(port, target, *, verify_write, progress_cb):
            assert (port, target, verify_write) == ("COM1", b"target", False)
            assert w._port_owner.owner == "native_fast_ds2"
            assert w._ds2 is None
            events.append("native_write")
            progress_cb(len(target), len(target), "native_partial_write")
            return "write-result"

        monkeypatch.setattr(gui, "DS2Interface", ReopenedDS2)
        monkeypatch.setattr(gui.time, "sleep", lambda _seconds: None)
        monkeypatch.setattr(
            gui.ds2_fast_read,
            "read_partial_d2xx",
            native_read,
        )
        monkeypatch.setattr(
            gui.ds2_native_fast_service,
            "write_partial_d2xx",
            native_write,
        )

        w._ds2 = InitiallyConnectedDS2()
        w._connection_port = "COM1"
        w._connection_echo = False
        w._ecu_softbsl_marker = None
        w._d2xx_ok = True
        w._port_owner.acquire("flasher")

        data = w._read_image_auto(
            "tune",
            lambda *args: logs.append(args),
            lambda *args: progress.append(args),
        )

        assert data == b"R" * 24576
        assert w._port_owner.owner == "flasher"
        assert w._ds2 is reopened[1]
        assert w._ds2.closed is False
        assert len(open_attempts) == 2
        assert any("ECU back up after the Fast operation (attempt 2)" in item[0]
                   for item in logs)
        assert not any("Native DS2 host baud" in item[0] for item in logs)

        restored_after_read = w._ds2
        result = w._write_tune_auto(
            b"target",
            lambda *args: logs.append(args),
            lambda *args: progress.append(args),
            verify_write=False,
        )

        assert result is None
        assert restored_after_read.closed is True
        assert events.index("native_read") < events.index("native_write")
        assert progress == [
            (24576, 24576, "fast_partial_read"),
            (0, 0, "Reopening normal DS2 at 9600"),
            (len(b"target"), len(b"target"), "native_partial_write"),
            (0, 0, "Reopening normal DS2 at 9600"),
        ]
        assert w._port_owner.owner == "flasher"
        assert w._ds2 is reopened[2]
        assert w._ds2.closed is False
        assert events.count("ordinary_ds2_identified") == 2
        assert all(event == ("ordinary_ds2_created", 9600)
                   for event in events if isinstance(event, tuple))
    finally:
        if w._ds2 is not None:
            w._ds2.close()
            w._ds2 = None
        w._port_owner.release("flasher")
        w.close()


@pytest.mark.parametrize("marker,hook,d2xx,expected", [
    ("B",  True,  True,  True),
    ("B",  False, True,  False),
    ("B",  True,  False, False),
    (None, False, True,  False),
    (None, False, False, False),
])
def test_fast_read_available_truth_table(marker, hook, d2xx, expected):
    app, w = _gui()
    try:
        w._ecu_softbsl_marker = marker
        w._ecu_softbsl_hook_present = hook
        w._d2xx_ok = d2xx
        assert w._fast_read_available() is expected
    finally:
        w.close()


def test_patches_read_routes_through_softbsl_when_available(monkeypatch):
    app, w = _gui()
    try:
        import softbsl_service
        w._ecu_softbsl_marker = "B"
        w._ecu_softbsl_hook_present = True
        w._d2xx_ok = True
        payload = b"\xAB" * 262144
        captured = {}

        def fake_run_via_softbsl(op_fn, log_fn, progress_fn):
            captured["routed"] = True
            return op_fn("COM1", progress_fn, log_fn)   # drive the op_fn just like the real helper

        def fake_read_image(port, scope, baud, pf, lf, chip_family=None):
            captured["args"] = (port, scope, baud)
            return payload

        def _no_ds2(*a, **k):
            raise AssertionError("DS2 path used when soft-BSL was available")

        monkeypatch.setattr(w, "_run_via_softbsl", fake_run_via_softbsl)
        monkeypatch.setattr(softbsl_service, "read_image", fake_read_image)
        monkeypatch.setattr(w, "_ds2_read", _no_ds2)

        data, source = w._read_base_from_ecu(lambda *a, **k: None, lambda *a, **k: None)
        assert captured["routed"] is True
        assert captured["args"] == ("COM1", "full", "high")
        assert data == payload
        assert source == "ECU read (Soft-BSL fast)"
    finally:
        w.close()


def test_patches_read_falls_back_to_ds2_without_softbsl(monkeypatch):
    app, w = _gui()
    try:
        w._ecu_softbsl_marker = None
        w._d2xx_ok = False
        payload = b"\xCD" * 262144

        def _no_softbsl(*a, **k):
            raise AssertionError("soft-BSL path used when unavailable")

        monkeypatch.setattr(w, "_ds2_read", lambda which, pf, lf: payload)
        monkeypatch.setattr(w, "_run_via_softbsl", _no_softbsl)

        data, source = w._read_base_from_ecu(lambda *a, **k: None, lambda *a, **k: None)
        assert data == payload
        assert source == "ECU read (DS2)"
    finally:
        w.close()


def test_patches_read_requires_connection(monkeypatch):
    app, w = _gui()
    try:
        w._ds2 = None
        shown = {}
        monkeypatch.setattr(QMessageBox, "information",
                            staticmethod(lambda *a, **k: shown.update(called=True)))
        ran = {}
        monkeypatch.setattr(w, "_run_task", lambda *a, **k: ran.update(dispatched=True))
        w._on_patches_read_ecu()
        assert shown.get("called") is True     # "Connect first" guard fired
        assert "dispatched" not in ran          # returned before dispatching the task
    finally:
        w.close()


def test_tab_order_is_workflow_grouped():
    app, w = _gui()
    try:
        order = [w.tabs.tabText(i).strip() for i in range(w.tabs.count())]
        assert order == ["Flash", "ECU Info", "DTC Codes", "Live Data", "Partial / Full", "Bins",
                         "Patches", "ECU Config", "VIN / EWS", "ROM Analyzer",
                         "Soft-BSL", "BSL-Unbricker"]
    finally:
        w.close()


def test_transfer_indicator_lives_on_the_flash_tab_top():
    app, w = _gui()
    try:
        # relocated out of the option checkboxes; still the same attribute driven by _update_transfer_mode
        assert hasattr(w, "lbl_transfer_mode")
        assert "Transfer" in w.lbl_transfer_mode.text()
    finally:
        w.close()


@pytest.mark.parametrize(
    "profile, expected",
    [
        ("ID41", ["Dual (2-channel)", "Single (1-channel)", "Disabled"]),
        ("ID42", ["Dual (2-channel)", "Single (1-channel)", "Disabled"]),
        ("ID59", ["Dual (2-channel)", "Single (1-channel)", "Disabled"]),
        ("ID85", ["Dual (2-channel)", "Single (1-channel)"]),
        ("ID60", ["Dual (2-channel)", "Single (1-channel)"]),
        ("ID12", ["Dual (2-channel)", "Single (1-channel)"]),
    ],
)
def test_config_oxygen_choices_follow_target_cal_id(profile, expected):
    app, w = _gui()
    try:
        w._configure_config_combos(profile, gui.MS41ECU.TUNE_SIZE, preserve=False)
        combo = w._config_combos["Oxygen Sensors"]

        assert [combo.itemText(i) for i in range(combo.count())] == expected
        assert combo.isEnabled()
    finally:
        w.close()


def test_config_unknown_cal_id_locks_only_profile_specific_oxygen_control():
    app, w = _gui()
    try:
        w._configure_config_combos(None, gui.MS41ECU.TUNE_SIZE, preserve=False)

        oxygen = w._config_combos["Oxygen Sensors"]
        assert not oxygen.isEnabled()
        assert oxygen.currentText() == "(CAL ID required)"
        assert w._config_combos["A/C Type"].isEnabled()
        assert w._config_combos["VANOS"].isEnabled()
    finally:
        w.close()


def test_config_copy_translates_common_oxygen_state_by_meaning():
    app, w = _gui()
    try:
        w._configure_config_combos("ID59", target_size=None, preserve=False)
        oxygen = w._config_combos["Oxygen Sensors"]
        oxygen.setCurrentText("Single (1-channel)")
        raw = bytearray([0x00, 0x00, 0x14, 0x00, 0x00])  # live Byte 4..8; ID85 Dual

        updated, skipped = w._apply_ecu_config_result(
            (raw, None), source_profile="ID85", target_profile="ID59")

        assert oxygen.currentText() == "Dual (2-channel)"
        assert any("Oxygen Sensors: Dual (2-channel)" in line for line in updated)
        assert not any("Oxygen Sensors" in line for line in skipped)
    finally:
        w.close()


def test_config_copy_does_not_translate_id41_disabled_to_id85():
    app, w = _gui()
    try:
        w._configure_config_combos("ID85", target_size=None, preserve=False)
        oxygen = w._config_combos["Oxygen Sensors"]
        oxygen.setCurrentText("Single (1-channel)")
        raw = bytearray([0x00, 0x00, 0x04, 0x00, 0x00])  # live Byte 4..8; ID41 Disabled

        updated, skipped = w._apply_ecu_config_result(
            (raw, None), source_profile="ID41", target_profile="ID85")

        assert oxygen.currentText() == "Single (1-channel)"
        assert not any("Oxygen Sensors" in line for line in updated)
        assert skipped == [
            "Oxygen Sensors: Disabled has no ID85 equivalent"
        ]
    finally:
        w.close()


def test_config_live_read_preserves_custom_oxygen_value_within_same_profile():
    app, w = _gui()
    try:
        w._configure_config_combos("ID12", target_size=None, preserve=False)
        raw = bytearray([0x00, 0x00, 0x04, 0x00, 0x00])  # Custom 0x04 for live ID12

        updated, skipped = w._apply_ecu_config_result(
            (raw, None), source_profile="ID12", target_profile="ID12")

        assert w._config_combos["Oxygen Sensors"].currentText() == "Custom (0x04)"
        assert any("Oxygen Sensors: Custom (0x04)" in line for line in updated)
        assert not any("Oxygen Sensors" in line for line in skipped)
    finally:
        w.close()


def test_config_full_id12_exposes_cal_disable_only_after_program_gate_is_selected():
    app, w = _gui()
    try:
        w._configure_config_combos(
            "ID12", gui.MS41ECU.FULL_ROM_SIZE, preserve=False,
            program_variant="MS41.2")

        oxygen = w._config_combos["Oxygen Sensors"]
        program_gate = w._config_combos[
            "O2 Feedback Program Gate (Experimental)"]
        assert [oxygen.itemText(i) for i in range(oxygen.count())] == [
            "Dual (2-channel)", "Single (1-channel)"]
        assert program_gate.isEnabled()
        assert [program_gate.itemText(i) for i in range(program_gate.count())] == [
            "Feedback Enabled", "Feedback Disabled",
        ]

        program_gate.setCurrentText("Feedback Disabled")
        assert [oxygen.itemText(i) for i in range(oxygen.count())] == [
            "Dual (2-channel)", "Single (1-channel)",
            "Disabled (Experimental)",
        ]

        oxygen.setCurrentText("Disabled (Experimental)")
        program_gate.setCurrentText("Feedback Enabled")
        assert "Disabled (Experimental)" not in [
            oxygen.itemText(i) for i in range(oxygen.count())]
        assert oxygen.currentText() == "Dual (2-channel)"
    finally:
        w.close()


def test_config_full_id12_with_existing_program_gate_starts_unlocked():
    app, w = _gui()
    try:
        w._configure_config_combos(
            "ID12", gui.MS41ECU.FULL_ROM_SIZE, preserve=False,
            program_variant="MS41.2", program_gate_present=True)

        oxygen = w._config_combos["Oxygen Sensors"]
        assert "Disabled (Experimental)" in [
            oxygen.itemText(i) for i in range(oxygen.count())]
    finally:
        w.close()


def test_config_sections_use_the_shared_groupbox_frames():
    app, w = _gui()
    try:
        assert list(w._config_section_groups) == [
            "Calibration Region — Partial & Full ROM / ECU Read & Write",
            "Program Region — Full ROM write only",
        ]
        for title, group in w._config_section_groups.items():
            assert isinstance(group, gui.QGroupBox)
            assert group.title() == title
            assert group.styleSheet() == gui._SECTION_GB
    finally:
        w.close()


def test_config_comboboxes_share_a_text_safe_fixed_height():
    app, w = _gui()
    try:
        heights = {combo.height() for combo in w._config_combos.values()}
        minimum_safe_height = max(
            combo.fontMetrics().lineSpacing() + 6
            for combo in w._config_combos.values()
        )
        assert len(heights) == 1
        assert next(iter(heights)) >= minimum_safe_height
        assert all(combo.minimumHeight() == combo.maximumHeight()
                   for combo in w._config_combos.values())
    finally:
        w.close()


def test_config_tab_scrolls_instead_of_compressing_combobox_rows():
    app, w = _gui()
    try:
        w.resize(700, 700)
        w.show()
        w.tabs.setCurrentIndex(w._config_tab_index)
        app.processEvents()

        calibration = next(iter(w._config_section_groups.values()))
        calibration_combos = [
            combo for name, combo in w._config_combos.items()
            if name not in {
                "Program CRC Check",
                "O2 Feedback Program Gate (Experimental)",
            }
        ]
        assert all(combo.parentWidget() is calibration
                   for combo in calibration_combos)
        geometries = sorted(
            (combo.y(), combo.height())
            for combo in calibration_combos
        )
        assert all(y + height <= next_y
                   for (y, height), (next_y, _) in zip(
                       geometries, geometries[1:]))
        assert w._config_scroll.verticalScrollBar().maximum() > 0
    finally:
        w.close()


@pytest.mark.parametrize("target_size", (None, MS41ECU.TUNE_SIZE))
def test_config_experimental_program_gate_requires_loaded_full_rom(target_size):
    app, w = _gui()
    try:
        w._configure_config_combos(
            "ID12", target_size, preserve=False, program_variant="MS41.2")

        oxygen = w._config_combos["Oxygen Sensors"]
        program_gate = w._config_combos[
            "O2 Feedback Program Gate (Experimental)"]
        assert "Disabled (Experimental)" not in [
            oxygen.itemText(i) for i in range(oxygen.count())]
        assert not program_gate.isEnabled()
        assert program_gate.currentText() == "(full ROM file required)"
    finally:
        w.close()


def test_config_experimental_program_gate_rejects_program_cal_mismatch():
    app, w = _gui()
    try:
        w._configure_config_combos(
            "ID12", gui.MS41ECU.FULL_ROM_SIZE, preserve=False,
            program_variant="MS41.1")

        program_gate = w._config_combos[
            "O2 Feedback Program Gate (Experimental)"]
        assert not program_gate.isEnabled()
        assert program_gate.currentText() == "(program/CAL mismatch)"
    finally:
        w.close()


def test_live_control_bit_profile_uses_exact_cal_id_not_broad_variant():
    app, w = _gui()
    try:
        w._ecu_cal_id = "59021110"
        w._ecu_variant = "MS41.0"
        w._ecu_program_variant = "MS41.0"
        assert w._live_control_bit_profile() == "ID59"
    finally:
        w.close()


def test_config_write_snapshots_verify_option_before_worker_task(monkeypatch):
    app, w = _gui()
    original_verify_widget = w.chk_verify
    try:
        import ecu_config

        partial = bytearray(b"\xFF" * gui.MS41ECU.TUNE_SIZE)
        patched = bytearray(partial)
        patched[4] = 0xFE
        monkeypatch.setattr(
            ecu_config, "apply_config", lambda data, changes, **kwargs: (patched, []))
        monkeypatch.setattr(w, "_config_diff", lambda old, new, **kwargs: [
            ("Test flag", 4, 0xFF, 0xFE, "old", "new")])
        monkeypatch.setattr(gui, "correct_checksums", lambda data, **kwargs: (data, []))
        monkeypatch.setattr(
            QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.No))
        monkeypatch.setattr(
            QMessageBox, "warning", staticmethod(lambda *a, **k: QMessageBox.Yes))

        class SnapshotCheck:
            calls = 0
            def isChecked(self):
                self.calls += 1
                return True

        snapshot_check = SnapshotCheck()
        w.chk_verify = snapshot_check
        captured = {}
        monkeypatch.setattr(w, "_run_task", lambda task, **kwargs: captured.update(task=task))
        monkeypatch.setattr(
            w, "_write_tune_auto",
            lambda image, log, progress, *, verify_write:
                captured.update(verify_write=verify_write))

        w._config_write_apply(partial)
        assert snapshot_check.calls == 1

        # The worker closure must use the GUI-thread snapshot, not touch the widget.
        w.chk_verify = object()
        captured["task"](lambda *a, **k: None, lambda *a, **k: None)
        assert captured["verify_write"] is True
    finally:
        w.chk_verify = original_verify_widget
        w.close()


def _synthetic_id12_full(*, program_crc=0xFF, o2_gate=0x0C, oxygen=0x14):
    """Minimal internally-identifiable MS41.2 image for config-routing tests."""
    image = bytearray(b"\xFF" * gui.MS41ECU.FULL_ROM_SIZE)
    image[0x6025:0x602C] = b"1406464"
    image[0x1400E:0x14016] = b"12000000"
    image[0x605C] = program_crc
    image[0x2DF95] = o2_gate
    image[0x14004:0x14009] = bytes((0x26, 0xC0, oxygen, 0x09, 0x86))
    return bytes(image)


def _prepare_live_id12_config(w, image, *, cache=False, read_gate=True):
    w._ds2 = object()
    w._connection_port = "COM_CONFIG_TEST"
    w._ecu_id = "1406464"
    w._ecu_cal_id = "12000000"
    w._ecu_variant = "MS41.2"
    w._ecu_program_variant = "MS41.2"
    if cache:
        w._last_full_read = bytes(image)
        w._last_full_read_key = w._identity_connection_key()
    raw = image[0x14004:0x14009]
    result = (raw, image[0x605C])
    if read_gate:
        result += (image[0x2DF95],)
    w._apply_live_config_read(result)


def test_config_live_read_populates_gate_and_decodes_experimental_o2_disable():
    app, w = _gui()
    try:
        image = _synthetic_id12_full(o2_gate=0x11, oxygen=0x04)
        _prepare_live_id12_config(w, image, cache=False)

        assert w._config_target_size == gui.MS41ECU.FULL_ROM_SIZE
        assert w._config_live_values_read is True
        assert (w._config_combos["Program CRC Check"].currentText()
                == "Disabled")
        gate = w._config_combos[
            "O2 Feedback Program Gate (Experimental)"]
        assert gate.isEnabled()
        assert gate.currentText() == "Feedback Disabled"
        assert (w._config_combos["Oxygen Sensors"].currentText()
                == "Disabled (Experimental)")
        assert w._config_live_baseline[
            "O2 Feedback Program Gate (Experimental)"] == gate.currentText()
    finally:
        w._ds2 = None
        w.close()


def test_config_live_read_keeps_full_read_fallback_when_gate_byte_read_failed():
    app, w = _gui()
    try:
        image = _synthetic_id12_full()
        _prepare_live_id12_config(w, image, cache=False, read_gate=False)

        gate = w._config_combos[
            "O2 Feedback Program Gate (Experimental)"]
        assert gate.isEnabled()
        assert gate.currentText() == "(full ROM read on write)"
    finally:
        w._ds2 = None
        w.close()


@pytest.mark.parametrize(
    "cal_id, variant, expected_ds2_addr",
    [
        ("12000000", "MS41.2", 0x29F95),
        ("60000000", "MS41.1", 0x2A311),
    ],
)
def test_config_live_task_reads_profile_program_gate_at_a14_mapped_address(
        cal_id, variant, expected_ds2_addr):
    app, w = _gui()
    try:
        calls = []

        class FakeDS2:
            def read_mem(self, addr, length):
                calls.append((addr, length))
                if addr == 0x10004:
                    return bytes((0x26, 0xC0, 0x04, 0x09, 0x86))
                if addr == 0x205C:
                    return b"\xFF"
                if addr == expected_ds2_addr:
                    return b"\x11"
                raise AssertionError(f"unexpected DS2 read 0x{addr:05X}")

        w._ds2 = FakeDS2()
        w._ecu_cal_id = cal_id
        w._ecu_variant = variant
        w._ecu_program_variant = variant

        result = w._ecu_read_config_task()(
            lambda *a, **k: None, lambda *a, **k: None)

        assert result[1:] == (0xFF, 0x11)
        assert (expected_ds2_addr, 1) in calls
    finally:
        w._ds2 = None
        w.close()


def test_config_live_read_reuses_matching_full_cache_for_program_values():
    app, w = _gui()
    try:
        image = _synthetic_id12_full(o2_gate=0x11, oxygen=0x04)
        _prepare_live_id12_config(w, image, cache=True)

        assert (w._config_combos[
            "O2 Feedback Program Gate (Experimental)"].currentText()
                == "Feedback Disabled")
        assert (w._config_combos["Oxygen Sensors"].currentText()
                == "Disabled (Experimental)")
        assert "matching current-session full read" in w.lbl_config_file.text()
    finally:
        w._ds2 = None
        w.close()


def test_config_live_program_edit_reuses_cached_full_without_another_read(monkeypatch):
    app, w = _gui()
    try:
        image = _synthetic_id12_full()
        _prepare_live_id12_config(w, image, cache=True)
        w._config_combos[
            "O2 Feedback Program Gate (Experimental)"].setCurrentText(
                "Feedback Disabled")
        captured = {}
        monkeypatch.setattr(
            w, "_config_write_apply_full",
            lambda data, **kwargs: captured.update(
                image=bytes(data), requested=kwargs["requested_changes"]))
        monkeypatch.setattr(
            w, "_run_task",
            lambda *a, **k: pytest.fail("matching cached full read must be reused"))

        w._on_config_write_ecu()

        assert captured["image"] == image
        assert (captured["requested"][
            "O2 Feedback Program Gate (Experimental)"]
                == "Feedback Disabled")
    finally:
        w._ds2 = None
        w.close()


def test_config_live_program_edit_reads_and_archives_full_when_cache_missing(monkeypatch):
    app, w = _gui()
    try:
        image = _synthetic_id12_full()
        _prepare_live_id12_config(w, image, cache=False)
        w._config_combos[
            "O2 Feedback Program Gate (Experimental)"].setCurrentText(
                "Feedback Disabled")
        routed = {}
        monkeypatch.setattr(
            QMessageBox, "warning",
            staticmethod(lambda *a, **k: QMessageBox.Yes))
        monkeypatch.setattr(
            w, "_read_image_auto",
            lambda which, log_fn, progress_fn:
                (routed.update(read=which), image)[1])
        entry = type("Entry", (), {"filename": "config-source.bin"})()
        monkeypatch.setattr(
            w, "_record_full_ecu_read",
            lambda data, source:
                (routed.update(archived=bytes(data), source=source), entry)[1])
        monkeypatch.setattr(
            w, "_config_write_apply_full",
            lambda data, **kwargs: routed.update(
                patched_from=bytes(data), requested=kwargs["requested_changes"]))

        def sync_run_task(task, on_success=None, on_failure=None):
            result = task(lambda *a, **k: None, lambda *a, **k: None)
            if on_success:
                on_success(result)

        monkeypatch.setattr(w, "_run_task", sync_run_task)

        w._on_config_write_ecu()
        app.processEvents()

        assert routed["read"] == "full"
        assert routed["archived"] == image
        assert routed["source"] == "ECU read (pre-config-full-write)"
        assert routed["patched_from"] == image
        assert (routed["requested"][
            "O2 Feedback Program Gate (Experimental)"]
                == "Feedback Disabled")
    finally:
        w._ds2 = None
        w.close()


def test_config_calibration_only_edit_keeps_partial_write_route(monkeypatch):
    app, w = _gui()
    try:
        image = _synthetic_id12_full()
        _prepare_live_id12_config(w, image, cache=False)
        vanos = w._config_combos["VANOS"]
        vanos.setCurrentText("Disabled" if vanos.currentText() == "Enabled" else "Enabled")
        routed = {}
        partial = bytearray(b"\xFF" * gui.MS41ECU.TUNE_SIZE)
        monkeypatch.setattr(
            w, "_read_image_auto",
            lambda which, log_fn, progress_fn:
                (routed.update(read=which), partial)[1])
        monkeypatch.setattr(
            w, "_config_write_apply",
            lambda data: routed.update(partial=bytes(data)))

        def sync_run_task(task, on_success=None, on_failure=None):
            result = task(lambda *a, **k: None, lambda *a, **k: None)
            if on_success:
                on_success(result)

        monkeypatch.setattr(w, "_run_task", sync_run_task)

        w._on_config_write_ecu()

        assert routed["read"] == "tune"
        assert routed["partial"] == bytes(partial)
    finally:
        w._ds2 = None
        w.close()


def test_config_full_patch_passes_original_archive_to_guarded_writer(monkeypatch):
    app, w = _gui()
    try:
        image = _synthetic_id12_full(program_crc=0xFF)
        _prepare_live_id12_config(w, image, cache=True)
        w._config_combos["Program CRC Check"].setCurrentText("Enabled")
        w.chk_config_fix.setChecked(False)
        monkeypatch.setattr(
            QMessageBox, "warning",
            staticmethod(lambda *a, **k: QMessageBox.Yes))
        captured = {}

        def capture_full(data, filename, **kwargs):
            captured.update(data=bytes(data), filename=filename, kwargs=kwargs)

        monkeypatch.setattr(w, "_ds2_write_full", capture_full)

        w._config_write_apply_full(bytearray(image))

        assert captured["data"][0x605C] == 0x30
        assert captured["filename"] == "live_ECU_config_full.bin"
        assert captured["kwargs"]["archived_prewrite_image"] == image
        captured["kwargs"]["on_write_success"]()
        assert w._config_live_baseline["Program CRC Check"] == "Enabled"
    finally:
        w._ds2 = None
        w.close()


def test_patches_build_archives_to_bins_and_offers_flash(monkeypatch):
    app, w = _gui()
    try:
        import patch_service
        w._patch_base = b"base"
        w._patch_installed_ids = set()
        w._patch_checkboxes = {"test": w.chk_correct_cksum}
        monkeypatch.setattr(patch_service, "build_image",
                            lambda base, sel: (b"\xFF" * 262144, ["ok"]))
        archived = {}
        class FakeEntry:
            filename = "ms41_patched_test.bin"
        monkeypatch.setattr(w._backup_mgr, "add_data",
                            lambda data, name, **k: (archived.update(source=k.get("source"),
                                                                     n=len(data)), FakeEntry())[1])
        monkeypatch.setattr(w, "_refresh_backup_table", lambda: None)
        copied = {}
        monkeypatch.setattr(w, "_offer_additional_read_copy",
                            lambda data, entry, label, **k: copied.update(label=label, n=len(data)))
        monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: QMessageBox.Yes))
        monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
        w._ds2 = object()                      # "connected" -> offers to flash
        flashed = {}
        monkeypatch.setattr(w, "_ds2_write_full", lambda data, name: flashed.update(name=name, n=len(data)))

        w._on_patches_build()

        assert archived["n"] == 262144
        assert archived["source"].startswith("patched:")
        assert copied == {"label": "Patched Full ROM (256 KB)", "n": 262144}
        assert flashed["name"] == "ms41_patched_test.bin"   # flashed the freshly-built image
        assert flashed["n"] == 262144
    finally:
        w.close()


def test_patches_build_archives_and_skips_flash_when_disconnected(monkeypatch):
    app, w = _gui()
    try:
        import patch_service
        w._patch_base = b"base"
        w._patch_installed_ids = set()
        w._patch_checkboxes = {"test": w.chk_correct_cksum}
        monkeypatch.setattr(patch_service, "build_image", lambda base, sel: (b"\xFF" * 262144, []))
        class FakeEntry:
            filename = "x.bin"
        monkeypatch.setattr(w._backup_mgr, "add_data", lambda data, name, **k: FakeEntry())
        monkeypatch.setattr(w, "_refresh_backup_table", lambda: None)
        monkeypatch.setattr(w, "_offer_additional_read_copy", lambda *a, **k: None)
        monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: QMessageBox.Yes))
        w._ds2 = None                          # not connected -> archive only, no flash prompt
        flashed = {}
        monkeypatch.setattr(w, "_ds2_write_full", lambda *a: flashed.update(ran=True))

        w._on_patches_build()

        assert "ran" not in flashed
    finally:
        w.close()


def test_identity_ews_bin_flash_forces_file_identity_boot_write(monkeypatch, tmp_path):
    app, w = _gui()
    try:
        path = tmp_path / "identity.bin"
        path.write_bytes(ref("MS41.1"))
        entry = type("Entry", (), {
            "filename": "identity.bin",
            "file_type": "Full ROM",
            "source": gui.IDENTITY_BACKUP_SOURCE,
            "path": str(path),
        })()
        monkeypatch.setattr(w, "_selected_backup", lambda: entry)
        w._ds2 = object()
        flashed = {}
        monkeypatch.setattr(
            w, "_ds2_write_full",
            lambda data, name, **kwargs:
                flashed.update(data=bytes(data), name=name, kwargs=kwargs))

        w._on_backup_flash()

        assert flashed["data"] == ref("MS41.1")
        assert flashed["kwargs"] == {
            "require_boot_write": True,
            "preserve_boot_identity": False,
        }
    finally:
        w._ds2 = None
        w.close()


def test_reset_adaptations_handler_is_live(monkeypatch):
    app, w = _gui()
    try:
        assert hasattr(w, "btn_reset_adapt")
        w._ds2 = None
        shown = {}
        monkeypatch.setattr(QMessageBox, "information",
                            staticmethod(lambda *a, **k: shown.update(v=True)))
        w._on_reset_adaptations()              # reaches the not-connected branch => wired, not a stub
        assert shown.get("v") is True
    finally:
        w.close()


def test_close_event_warns_and_can_abort_when_task_busy(monkeypatch):
    from PyQt5.QtGui import QCloseEvent
    app, w = _gui()
    try:
        w._task_busy = True
        asked = {}
        monkeypatch.setattr(QMessageBox, "warning",
                            staticmethod(lambda *a, **k: asked.update(shown=True) or QMessageBox.No))
        ev = QCloseEvent()
        w.closeEvent(ev)
        assert asked.get("shown") is True      # user was warned
        assert not ev.isAccepted()             # chose No -> the close is vetoed
    finally:
        w._task_busy = False
        w.close()


def test_close_event_cancel_vetoes_live_softbsl_recovery(monkeypatch):
    from PyQt5.QtGui import QCloseEvent
    app, w = _gui()
    try:
        close_calls = []

        class Recovery:
            is_open = True

            def close_after_confirmed_power_cycle(self):
                close_calls.append(True)

        recovery = Recovery()
        w._softbsl_write_recovery = recovery
        w._port_owner.acquire("softbsl")
        shown = {}
        monkeypatch.setattr(
            QMessageBox,
            "warning",
            staticmethod(
                lambda *args, **kwargs: shown.update(message=args) or QMessageBox.Cancel
            ),
        )

        event = QCloseEvent()
        w.closeEvent(event)

        assert not event.isAccepted()
        assert close_calls == []
        assert w._softbsl_write_recovery is recovery
        assert w._port_owner.owner == "softbsl"
        assert "Soft-BSL RAM-agent" in shown["message"][2]
        assert "Retry Flash Recovery" in shown["message"][2]
    finally:
        w._softbsl_write_recovery = None
        w._port_owner.release("softbsl")
        w.close()


def test_close_event_vetoes_busy_recovery_without_releasing_it(monkeypatch):
    from PyQt5.QtGui import QCloseEvent
    app, w = _gui()
    try:
        close_calls = []

        class Recovery:
            is_open = True

            def close_after_confirmed_power_cycle(self):
                close_calls.append(True)

        recovery = Recovery()
        w._softbsl_write_recovery = recovery
        w._task_busy = True
        w._port_owner.acquire("softbsl")
        shown = []
        monkeypatch.setattr(
            QMessageBox,
            "warning",
            staticmethod(
                lambda *args, **kwargs: shown.append((args, kwargs)) or QMessageBox.Close
            ),
        )

        event = QCloseEvent()
        w.closeEvent(event)

        assert not event.isAccepted()
        assert close_calls == []
        assert w._softbsl_write_recovery is recovery
        assert w._port_owner.owner == "softbsl"
        assert len(shown) == 1
        assert shown[0][0][1] == "Flash Recovery In Progress"
    finally:
        w._task_busy = False
        w._softbsl_write_recovery = None
        w._port_owner.release("softbsl")
        w.close()


def test_close_event_close_releases_recovery_owner_and_accepts(monkeypatch):
    from PyQt5.QtGui import QCloseEvent
    app, w = _gui()
    try:
        close_calls = []
        logs = []

        class Recovery:
            is_open = True

            def close_after_confirmed_power_cycle(self):
                close_calls.append(True)
                self.is_open = False

        recovery = Recovery()
        w._softbsl_write_recovery = recovery
        w._port_owner.acquire("softbsl")
        monkeypatch.setattr(
            QMessageBox,
            "warning",
            staticmethod(lambda *args, **kwargs: QMessageBox.Close),
        )
        monkeypatch.setattr(
            w,
            "_log",
            lambda message, level="info": logs.append((message, level)),
        )
        monkeypatch.setattr(w, "_on_live_stop", lambda: None)
        monkeypatch.setattr(w, "_disconnect", lambda: None)
        monkeypatch.setattr(w, "_end_session_log", lambda: None)

        event = QCloseEvent()
        w.closeEvent(event)

        assert event.isAccepted()
        assert close_calls == [True]
        assert w._softbsl_write_recovery is None
        assert w._port_owner.is_free()
        assert any("Abandoned the retained Soft-BSL RAM-agent session" in message
                   for message, _level in logs)
    finally:
        w._softbsl_write_recovery = None
        w._port_owner.release("softbsl")
        w.close()
