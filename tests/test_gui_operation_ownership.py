"""Real Qt-thread regressions for desktop operation and presentation ownership."""

import os
import threading
import time
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PyQt5")
from PyQt5.QtCore import QThread
from PyQt5.QtWidgets import QApplication

import gui


def wait_until(app, condition):
    deadline = time.monotonic() + 5
    while not condition() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.001)
    assert condition()


@pytest.fixture
def window(monkeypatch):
    app = QApplication.instance() or QApplication([])
    window = gui.MS41FlashGUI()
    monkeypatch.setattr(window, "_refresh_transmission_recovery_quarantine", lambda **kw: None)
    yield app, window
    window._ds2 = None
    window._task_busy = False
    window.close()
    window.deleteLater()
    app.processEvents()


def test_patch_actions_cannot_replace_a_running_worker_or_start_flash_preflight(window, monkeypatch):
    app, w = window
    entered, release = threading.Event(), threading.Event()
    completed = []
    w._ds2 = SimpleNamespace(close=lambda: None)
    w._patch_base = bytes(gui.MS41ECU.FULL_ROM_SIZE)
    w._patch_removed_ids = {"removed_patch"}
    w._update_patch_buttons()
    assert w.btn_patches_read.isEnabled() and w.btn_patches_build.isEnabled()

    def blocked_write(log_fn, progress_fn):
        entered.set()
        assert release.wait(5)
        return "original write"

    def forbidden(*args, **kwargs):
        pytest.fail("Another operation reached ECU preflight or task dispatch")

    monkeypatch.setattr(w, "_read_base_from_ecu", forbidden)
    monkeypatch.setattr(w, "_prewrite_battery_notice", forbidden)
    monkeypatch.setattr(w, "_live_coding_family", forbidden)
    monkeypatch.setattr(w, "_build_patch_image", forbidden)
    assert w._run_task(blocked_write, on_success=completed.append)
    original = w._worker
    try:
        assert entered.wait(2)
        assert not w.btn_patches_read.isEnabled()
        assert not w.btn_patches_build.isEnabled()
        w._on_patch_selection_changed()
        assert not w.btn_patches_build.isEnabled()
        w.btn_patches_read.click()
        w._on_patches_read_ecu()
        w._on_patches_build()
        w._ds2_write_tune(bytearray(), "must-not-start.bin")
        w._ds2_write_full(bytearray(), "must-not-start.bin")
        assert w._run_task(forbidden) is False
        assert w._run_state_changing_task(forbidden) is False
        w._run_eeprom_task(forbidden, forbidden)
        assert not w._eeprom_task_busy
        assert w._worker is original and w._task_busy
        assert not w.btn_write_tune.isEnabled()
    finally:
        release.set()
        assert original.wait(5000)
    wait_until(app, lambda: completed == ["original write"])
    assert not w._task_busy
    assert w.btn_patches_read.isEnabled() and w.btn_patches_build.isEnabled()


def test_completion_can_start_the_next_operation(window):
    app, w = window
    workers, results = [], []

    def continue_after_read(result):
        results.append(result)
        assert w._run_task(lambda **kw: "write", on_success=results.append)
        workers.append(w._worker)

    assert w._run_task(lambda **kw: "read", on_success=continue_after_read)
    workers.append(w._worker)
    try:
        wait_until(app, lambda: results == ["read", "write"])
        assert not w._task_busy
    finally:
        for worker in workers:
            assert worker.wait(5000)


def test_reconnect_failure_cleans_transport_immediately_and_updates_widgets_on_gui_thread(
        window, monkeypatch):
    app, w = window
    cleaned, release = threading.Event(), threading.Event()
    widget_threads, completed = [], []
    original_refresh = w._update_d2xx_warning

    def refresh():
        widget_threads.append(QThread.currentThread() == app.thread())
        original_refresh()

    def unavailable(*args, **kwargs):
        raise OSError("simulated unavailable adapter")

    monkeypatch.setattr(w, "_update_d2xx_warning", refresh)
    monkeypatch.setattr(gui, "open_identified", unavailable)
    w._connection_port = "SIMULATED"
    w._ds2 = SimpleNamespace(close=lambda: None)
    w._port_owner.acquire("flasher")
    w.lbl_status.setText("Connected before failed handoff")

    def reconnect(log_fn, progress_fn):
        assert not w._reopen_ds2_with_retry("SIMULATED", log_fn, attempts=1)
        cleaned.set()
        assert release.wait(5)

    w._run_task(reconnect, on_success=completed.append)
    worker = w._worker
    try:
        assert cleaned.wait(2)
        assert w._ds2 is None and w._connection_port is None
        assert w._port_owner.is_free()
        assert widget_threads == []
        assert w.lbl_status.text() == "Connected before failed handoff"
        wait_until(app, lambda: bool(widget_threads))
        assert all(widget_threads)
        assert "Disconnected" in w.lbl_status.text()
        assert w._task_busy and not w.btn_connect.isEnabled()
    finally:
        release.set()
        assert worker.wait(5000)
    wait_until(app, lambda: bool(completed))
    assert not w._task_busy and w.btn_connect.isEnabled()
