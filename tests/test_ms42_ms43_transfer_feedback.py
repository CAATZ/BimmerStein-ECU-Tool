"""Real Qt worker feedback with supplied ECU callbacks; no physical transport."""
from contextlib import contextmanager
import threading
from types import SimpleNamespace

import pytest
from PyQt5.QtWidgets import QMessageBox

import gui
import ms42_ms43_image as images
import transfer_rates
from tests.test_ms42_ms43 import Journal
from tests.test_ms42_ms43_agent_workflow import agent_workflow as agent_workflow
from tests.test_ms42_ms43_desktop import connect, window as window
from tests.test_ms42_ms43_image import synthetic_image
from tests.test_ms42_ms43_ram_write import target_image
from tests.test_ms42_ms43_snapshot_baud import bulk_reads, snapshot_peer as snapshot_peer


@pytest.fixture
def feedback(window, monkeypatch):
    monkeypatch.setattr(transfer_rates, "_high_baud", transfer_rates.STANDARD_HIGH_BAUD)
    result = SimpleNamespace(errors=[], completed=[], slot_threads=[], worker_threads=[])
    slot = window._ms42_ms43_show_transfer_rate
    def observed(baud, source):
        result.slot_threads.append(threading.get_ident())
        slot(baud, source)
    monkeypatch.setattr(window, "_ms42_ms43_show_transfer_rate", observed)
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.Yes)
    monkeypatch.setattr(QMessageBox, "critical", lambda *args: result.errors.append(args))
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: result.errors.append(args))
    monkeypatch.setattr(window, "_prewrite_battery_notice", lambda: "12.8 V")
    monkeypatch.setattr(window, "_offer_additional_read_copy", lambda *args: None)
    monkeypatch.setattr(window, "_show_flash_complete", lambda *args: result.completed.append(args))
    window.chk_backup_before_write.setChecked(False)
    return result


@contextmanager
def worker_lifetime(window, qtbot, *releases):
    worker = window._worker
    assert worker is not None and window._task_busy
    try:
        yield
    finally:
        for release in releases:
            release.set()
        assert worker.wait(10000)
        qtbot.waitUntil(lambda: not window._task_busy, timeout=5000)


@pytest.mark.parametrize("family,preferred", [("MS42", 166667), ("MS43", 187500)])
def test_identity_history_worker_keeps_stock_rate_without_bulk_storage_reads(
        window, feedback, qtbot, snapshot_peer, family, preferred):
    connect(window, family)
    ecu, peer = snapshot_peer(family)
    window._ds2 = ecu
    window.chk_force_slow_ds2.setChecked(False)
    window._update_transfer_mode()
    idle = window.lbl_high_speed_mode.text()
    assert idle == f"High-speed: Standard · {preferred:,} bps"
    release = threading.Event()
    entered = threading.Event()
    read = ecu.read_memory

    def paused_identity_read(address, length, **kwargs):
        feedback.worker_threads.append(threading.get_ident())
        if not entered.is_set():
            entered.set()
            assert release.wait(10)
        return read(address, length, **kwargs)

    ecu.read_memory = paused_identity_read
    window._ms42_ms43_data_read.click()
    with worker_lifetime(window, qtbot, release):
        qtbot.waitUntil(entered.is_set)
        assert window._task_busy and not window._ms42_ms43_data_snapshot
        assert "9,600 baud" in window.lbl_observed_rate.text()
        assert window.lbl_high_speed_mode.text() == idle
        assert ecu.baud == peer.baudrate == 9600
    assert window.lbl_high_speed_mode.text() == idle
    assert window._ms42_ms43_data_snapshot["aif_raw"]
    assert "retained_raw" not in window._ms42_ms43_data_snapshot
    assert not bulk_reads(peer)
    assert peer.requests and all(baud == 9600 and command != 0x91
                                 for baud, command, _ in peer.requests)
    assert ecu.baud == 9600 and not feedback.errors
    assert feedback.worker_threads and threading.get_ident() not in feedback.worker_threads
    assert set(feedback.slot_threads) <= {threading.get_ident()}


@pytest.mark.parametrize("family,ram_baud", [("MS42", 166667), ("MS43", 187500)])
@pytest.mark.parametrize("route", ["ram", "fallback", "slow"])
@pytest.mark.parametrize("compatibility", [False, True])
def test_read_reports_active_confirmed_rate_then_restored_ds2_on_gui_thread(
        window, feedback, qtbot, family, ram_baud, route, compatibility):
    ecu = connect(window, family)
    ecu.transport = SimpleNamespace(native_fast_capable=True, _ser=SimpleNamespace(parity="E"))
    assert window._save_transfer_rate(compatibility)
    ram_baud = 125000 if compatibility else ram_baud
    def original_callback(*args):
        pytest.fail("Previous observer must not own this task")
    ecu.transfer_rate_cb = original_callback
    data, _ = images.correct_checksums(synthetic_image(family), family)
    active_baud = ram_baud if route == "ram" else 9600 if route == "slow" else 125000
    active_source = "RAM transfer" if route == "ram" else "DS2 connection"
    release = threading.Event()
    window.chk_force_slow_ds2.setChecked(route == "slow")

    def read(*, progress_cb, log_fn):
        feedback.worker_threads.append(threading.get_ident())
        progress_cb(1024, len(data), "Reading")
        assert release.wait(10)
        return data

    @contextmanager
    def read_transfer(*, force_slow, log_fn, use_agent):
        assert force_slow == (route == "slow")
        assert use_agent is not force_slow
        ecu.transfer_rate_cb(0, "transition")
        ecu.transfer_rate_cb(active_baud, active_source)
        yield SimpleNamespace(read_source="ECU test read", read_full=read)
        ecu.transfer_rate_cb(0, "transition")
        ecu.transfer_rate_cb(9600, "DS2 connection")
    ecu.read_transfer = read_transfer

    window._on_read("full")
    with worker_lifetime(window, qtbot, release):
        qtbot.waitUntil(lambda: window.lbl_observed_rate.text() ==
                       f"Current link: {active_baud:,} baud ({active_source})")
        assert window.lbl_observed_rate.text().startswith("Current link:")
        assert window._task_busy and not window._backup_mgr.entries
        assert not window._save_transfer_rate(not compatibility)
        assert transfer_rates.get_high_baud() == (125000 if compatibility else 187500)
    assert window.lbl_observed_rate.text() == "Current link: 9,600 baud (DS2 connection)"
    assert feedback.worker_threads and set(feedback.worker_threads) != {threading.get_ident()}
    assert set(feedback.slot_threads) == {threading.get_ident()}
    assert ecu.transfer_rate_cb is original_callback
    assert len(window._backup_mgr.entries) == 1 and not feedback.errors


@pytest.mark.parametrize("family,baud", [("MS42", 166667), ("MS43", 187500)])
@pytest.mark.parametrize("compatibility", [False, True])
def test_write_keeps_completed_bytes_visible_during_restore_and_finalization(
        window, feedback, qtbot, family, baud, compatibility):
    ecu = connect(window, family)
    assert window._save_transfer_rate(compatibility)
    baud = 125000 if compatibility else baud
    ecu.transfer_rate_cb = None
    data = images.extract_calibration(synthetic_image(family))
    restore, finalize, finish = (threading.Event() for _ in range(3))

    @contextmanager
    def transfer(**kwargs):
        ecu.transfer_rate_cb(125000, "DS2 connection")
        yield ecu
        ecu.transfer_rate_cb(9600, "DS2 connection")

    def write(data, *, progress_cb, **kwargs):
        ecu.transfer_rate_cb(baud, "RAM transfer")
        progress_cb(len(data), len(data), "Write")
        assert restore.wait(10)
        ecu.transfer_rate_cb(0, "transition")
        progress_cb(0, 0, family + " restoring diagnostics")
        assert finalize.wait(10)
        ecu.transfer_rate_cb(9600, "DS2 connection")
        progress_cb(0, 0, family + " checking firmware")
        assert finish.wait(10)
    ecu.transfer, ecu.write_tune = transfer, write

    window._ms42_ms43_write(data, "test.bin", "tune")
    with worker_lifetime(window, qtbot, restore, finalize, finish):
        qtbot.waitUntil(lambda: f"{baud:,} baud" in window.lbl_observed_rate.text())
        qtbot.waitUntil(lambda: window.progress_bar.value() == 100)
        assert not window._save_transfer_rate(not compatibility)
        assert transfer_rates.get_high_baud() == (125000 if compatibility else 187500)
        restore.set()
        qtbot.waitUntil(lambda: window.progress_label.text() == family + " restoring diagnostics")
        assert window.lbl_observed_rate.text() == "Current link: changing rate…"
        assert window.progress_bar.value() == 100 and window.progress_bar.maximum() == 100
        assert window._task_busy and not feedback.completed
        finalize.set()
        qtbot.waitUntil(lambda: window.progress_label.text() == family + " checking firmware")
        assert "9,600 baud" in window.lbl_observed_rate.text()
        assert window.progress_bar.value() == 100
        assert window._task_busy and not feedback.completed
    assert not feedback.errors and len(feedback.completed) == 1
    assert ecu.transfer_rate_cb is None
    assert window.lbl_observed_rate.text() == "Current link: 9,600 baud (DS2 connection)"


@pytest.mark.parametrize("unconfirmed", [False, True], ids=["retained-rate", "uncertain-rate"])
def test_failed_write_does_not_claim_restored_link_or_leak_observer(
        window, feedback, qtbot, unconfirmed):
    ecu = connect(window, "MS43")
    def original_callback(*args):
        pass
    ecu.transfer_rate_cb = original_callback
    data = images.extract_calibration(synthetic_image("MS43"))
    release = threading.Event()
    restores = []

    @contextmanager
    def transfer(**kwargs):
        yield ecu
        restores.append(True)
        ecu.transfer_rate_cb(9600, "DS2 connection")

    def write(data, **kwargs):
        ecu.transfer_rate_cb(187500, "RAM transfer")
        assert release.wait(10)
        ecu.write_state = SimpleNamespace(destructive_started=True, completed=False)
        if unconfirmed:
            ecu.transfer_rate_cb(0, "unconfirmed")
        raise gui.DS2Error("Supplied interrupted write")
    ecu.transfer, ecu.write_tune = transfer, write

    window._ms42_ms43_write(data, "test.bin", "tune")
    with worker_lifetime(window, qtbot, release):
        qtbot.waitUntil(lambda: "187,500 baud" in window.lbl_observed_rate.text())
    assert feedback.errors and not feedback.completed and not restores
    assert ecu.transfer_rate_cb is original_callback
    assert not window.btn_read_full.isEnabled()
    assert window.lbl_observed_rate.text() == (
        "Current link: unconfirmed" if unconfirmed else "Current link: 187,500 baud (RAM transfer)")


def test_queued_rate_from_previous_ecu_does_not_replace_current_connection(window, feedback, qtbot):
    old = connect(window, "MS43")
    old.transfer_rate_cb = None
    release = threading.Event()
    entered = threading.Event()

    def task(*, log_fn, progress_fn):
        entered.set()
        assert release.wait(10)
        old.transfer_rate_cb(187500, "RAM transfer")
    window._run_task(task)
    with worker_lifetime(window, qtbot, release):
        qtbot.waitUntil(entered.is_set)
        connect(window, "MS43")  # Same family, different connection owner.
    assert window.lbl_observed_rate.text() == "Connected link: 9,600 baud (DS2 connection)"
    assert not feedback.slot_threads
    assert old.transfer_rate_cb is None


def test_ms41_existing_observed_rate_signal_keeps_completed_transfer_semantics(window, qtbot):
    idle_footer = window.lbl_high_speed_mode.text()
    release = threading.Event()
    def task(log_fn, progress_fn):
        window._transfer_rate_observed.emit(187500, "native DS2 read")
        assert release.wait(10)
    window._run_task(task)
    with worker_lifetime(window, qtbot, release):
        qtbot.waitUntil(lambda: "187,500 baud" in window.lbl_observed_rate.text())
        assert window.lbl_observed_rate.text() == "Last completed transfer: 187,500 baud (native DS2 read)"
        assert window.lbl_high_speed_mode.text() == idle_footer
    window._show_transfer_rate(9600, "DS2 connection")
    assert window.lbl_observed_rate.text() == "Connected link: 9,600 baud (DS2 connection)"
    assert window.lbl_high_speed_mode.text() == idle_footer


def test_read_startup_failure_does_not_show_unconfirmed_selected_ram_rate(window, feedback, qtbot):
    ecu = connect(window, "MS43")
    ecu.transfer_rate_cb = None
    release = threading.Event()
    finish = threading.Event()
    idle_footer = window.lbl_high_speed_mode.text()

    @contextmanager
    def read_transfer(**kwargs):
        ecu.transfer_rate_cb(0, "transition")
        assert release.wait(10)
        ecu.baud_uncertain = True
        ecu.transfer_rate_cb(0, "unconfirmed")
        assert finish.wait(10)
        raise gui.DS2Error("RAM identity did not confirm the requested rate")
        yield  # This deliberately fails before the context manager yields.
    ecu.read_transfer = read_transfer
    window._on_read("full")
    with worker_lifetime(window, qtbot, release, finish):
        qtbot.waitUntil(lambda: window.lbl_observed_rate.text() == "Current link: changing rate…")
        assert window.lbl_high_speed_mode.text() == "Link: changing rate…"
        assert window._task_busy
        release.set()
        qtbot.waitUntil(lambda: window.lbl_high_speed_mode.text() == "Link: unconfirmed")
    assert window.lbl_observed_rate.text() == "Current link: unconfirmed"
    assert window.lbl_high_speed_mode.text() == idle_footer
    assert not window._backup_mgr.entries and feedback.errors
    assert not window.btn_read_full.isEnabled() and ecu.transfer_rate_cb is None


@pytest.mark.parametrize("operation", ["read", "write"])
def test_real_agent_backend_reports_rates_after_wire_confirmation(agent_workflow, operation):
    ecu, peer = agent_workflow
    events = []
    ecu.transfer_rate_cb = lambda baud, source: events.append(
        (baud, source, peer.requests[-1] if peer.requests else None,
         peer.identities, peer.restored, bool(peer.pending)))
    if operation == "read":
        with ecu.read_transfer(use_agent=True) as reader:
            reader.read_tune()
    else:
        with ecu.transfer():
            state = ecu.write_tune(target_image(peer, "tune"), use_ram=True, use_agent=True,
                                   verify=True, journal=Journal())
        assert state.completed and state.finalized and state.verified
    expected_baud = 166667 if ecu.family == "MS42" else 187500
    fast = [event for event in events if event[1] == "RAM transfer"]
    assert fast == [(expected_baud, "RAM transfer", ("resident", 2, b""), 2, False, False)]
    assert any(event[0:3] == (9600, "DS2 connection", ("ds2", 0, b""))
               and event[4] and not event[5] for event in events)
    assert events[-1][:2] == (9600, "DS2 connection")
    assert peer.restored and ecu.baud == 9600


def test_real_agent_failed_resident_identity_never_reports_fast_rate(agent_workflow):
    ecu, peer = agent_workflow
    peer.fault = "baud_identity"
    events = []
    ecu.transfer_rate_cb = lambda *event: events.append(event)
    with pytest.raises(gui.DS2Error, match="baud change was not confirmed"):
        with ecu.read_transfer(use_agent=True):
            pytest.fail("Failed identity cannot expose a reader")
    assert peer.resident_baud_selected and not peer.agent_launches
    assert not any(source == "RAM transfer" for _baud, source in events)
    assert events[-1] == (0, "unconfirmed")
    assert ecu.baud_uncertain and not peer.restored
