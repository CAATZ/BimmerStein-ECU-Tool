"""Virginizing uses the existing worker/recovery UI without triggering EWS alignment."""

from contextlib import contextmanager
import sys
from types import SimpleNamespace

import pytest
from PyQt5.QtWidgets import QMessageBox

import ms42_ms43_vin as vin_data
from tests import test_ms42_ms43_desktop as desktop
from tests.test_ms42_ms43 import Journal
from tests.test_ms42_ms43_vin_desktop import OLD_VIN, connected, result
from tests.test_ms42_ms43_virgin_workflow import (
    agent_workflow as agent_workflow, vin_workflow as vin_workflow,
    virgin_workflow as virgin_workflow, target_image,
)


window = desktop.window
worker_runner = desktop.worker_runner


@pytest.fixture
def dialogs(window, monkeypatch):
    observed = SimpleNamespace(prompts=[], information=[], failures=[], warnings=[],
                               checkboxes=[], clear_vin=False, accept=True)

    def execute(box):
        assert box.windowTitle() == "Virginize ECU"
        checkbox = box.checkBox()
        assert checkbox is not None and not checkbox.isChecked()
        assert box.defaultButton() == box.button(QMessageBox.No)
        observed.checkboxes.append(checkbox.text())
        checkbox.setChecked(observed.clear_vin)
        observed.prompts.append((window, box.windowTitle(), box.text()))
        return QMessageBox.Yes if observed.accept else QMessageBox.No

    monkeypatch.setattr(window, "_prewrite_battery_notice", lambda: "Battery voltage: 13.0 V")
    monkeypatch.setattr(QMessageBox, "exec_", execute)
    monkeypatch.setattr(QMessageBox, "question", lambda *args:
                        observed.prompts.append(args) or QMessageBox.Yes)
    monkeypatch.setattr(QMessageBox, "information", lambda *args: observed.information.append(args))
    monkeypatch.setattr(QMessageBox, "critical", lambda *args: observed.failures.append(args))
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: observed.warnings.append(args))
    return observed


def virgin_result(family, *, already=False, clear_vin=False):
    raw = b"\xFF" * vin_data.AIF_SIZE if clear_vin else result(family, OLD_VIN)["aif_raw"]
    return dict(vin_data.decode_aif(raw), family=family, aif_raw=raw,
                aif_address=vin_data.AIF_ADDRESSES[family], virginized=True, already_virginized=already,
                vin_cleared=clear_vin)


def forbid_alignment(ecu):
    ecu.sync_ews = lambda: pytest.fail("Virginizing must not synchronize EWS")
    ecu.reset_ews_start_value = lambda: pytest.fail("Virginizing must not reset EWS through diagnostics")


@pytest.mark.parametrize("family", ["MS42", "MS43"])
@pytest.mark.parametrize("clear_vin", [False, True])
def test_virginize_worker_obeys_identity_choice_and_leaves_alignment_optional(
        window, worker_runner, monkeypatch, dialogs, family, clear_vin):
    ecu = connected(window, family)
    dialogs.clear_vin = clear_vin
    forbid_alignment(ecu)
    window._ms42_ms43_show_info(dict(result(family, OLD_VIN), program_part_number="1430355"))
    assert window.btn_virginize.isEnabled()
    assert not window.id_vin_current.text() and not window.id_vin_custom.text()
    lifecycle = []

    @contextmanager
    def transfer():
        lifecycle.append("enter")
        yield
        lifecycle.append("exit")

    ecu.transfer = transfer

    def virginize(owner, *, log_fn, progress_cb, clear_vin):
        assert owner is ecu and clear_vin is dialogs.clear_vin
        lifecycle.append("virginize")
        log_fn("Original sector saved")
        progress_cb(1, 1, "Virginizing verified")
        return virgin_result(family, clear_vin=clear_vin)

    monkeypatch.setitem(sys.modules, "ms42_ms43_vin_write", SimpleNamespace(virginize=virginize))
    window._identity_isn, window._identity_isn_key = "1234", "old"
    window._last_full_read, window._last_full_read_key = b"old", "old"
    window.btn_virginize.click()
    assert lifecycle == ["enter", "virginize", "exit"]
    assert worker_runner.results[0][0]
    assert worker_runner.progress == [(1, 1, "Virginizing verified")]
    assert len(dialogs.prompts) == 1
    prompt = dialogs.prompts[0][2]
    assert "ISN and pairing" in prompt and "Original data is saved" in prompt
    assert "VIN and AIF programming history are preserved unless" in prompt
    assert "recorded dates, software and workshop entries" in prompt
    assert dialogs.checkboxes == ["Also clear VIN and programming history"]
    assert "16 KB" in prompt and "STOPPED" in prompt and "power stable" in prompt
    assert "EWS alignment will not run" in prompt and "preferred tool" in prompt
    assert window._identity_isn is window._identity_isn_key is None
    assert window._last_full_read is window._last_full_read_key is None
    if clear_vin:
        assert not window._ecu_vin and not window.id_vin_current.text()
        assert window._info_labels["VIN"].text() == "Unavailable"
        assert window.id_boot_strings.toPlainText() == "No recorded AIF entries."
        assert window._info_labels["AIF Record Date"].text() == "—"
        assert window._info_labels["Recorded Software Number"].text() == "—"
        assert window._info_labels["Programming Count"].text() == "0"
    else:
        assert window._ecu_vin == window.id_vin_current.text() == OLD_VIN
        assert "Record 1" in window.id_boot_strings.toPlainText()
        assert window._info_labels["AIF Record Date"].text() != "—"
        assert window._info_labels["Recorded Software Number"].text() != "—"
        assert window._info_labels["Programming Count"].text() == "1"
    assert window._info_labels["BMW Program Part Number"].text() == "1430355"
    assert len(dialogs.information) == 1 and not dialogs.failures and not dialogs.warnings
    assert dialogs.information[0][1] == "ECU Virginized"
    assert ("history are cleared" if clear_vin else "history are preserved") in dialogs.information[0][2]
    assert "EWS alignment was not performed" in window.id_ews_frames.toPlainText()
    assert "ignition OFF" not in dialogs.information[0][2]
    window._set_all_buttons_enabled(True)
    assert window.btn_id_read_ecu.isEnabled() and window.btn_ews_send.isEnabled()


@pytest.mark.parametrize("clear_vin", [False, True])
def test_virginize_cancel_never_starts_worker(window, worker_runner, monkeypatch, dialogs, clear_vin):
    connected(window)
    dialogs.clear_vin = clear_vin
    monkeypatch.setitem(sys.modules, "ms42_ms43_vin_write", SimpleNamespace(
        virginize=lambda *a, **k: pytest.fail("Cancelled operation reached backend")))
    dialogs.accept = False
    window.btn_virginize.click()
    assert not worker_runner.results and not dialogs.information


def test_vin_erasure_is_not_remembered_for_the_next_virginize(window, worker_runner, monkeypatch, dialogs):
    connected(window)
    choices = []

    def virginize(owner, *, log_fn, progress_cb, clear_vin):
        choices.append(clear_vin)
        return virgin_result(owner.family, clear_vin=clear_vin)

    monkeypatch.setitem(sys.modules, "ms42_ms43_vin_write", SimpleNamespace(virginize=virginize))
    dialogs.clear_vin = True
    window.btn_virginize.click()
    dialogs.clear_vin = False
    window.btn_virginize.click()
    assert choices == [True, False]
    assert len(dialogs.checkboxes) == 2 and all(success for success, _ in worker_runner.results)


@pytest.mark.parametrize("reason", ["busy", "baud", "actuator", "software", "interrupted",
                                    "slow", "transport", "transmission"])
def test_virginize_obeys_existing_connection_and_recovery_states(window, reason):
    ecu = connected(window)
    if reason == "busy":
        window._task_busy = True
    elif reason == "baud":
        ecu.baud_uncertain = True
    elif reason == "actuator":
        window._actuator_cleanup_pending = True
    elif reason == "software":
        window._ecu_cal_id = "430056"
    elif reason == "interrupted":
        ecu.write_state = SimpleNamespace(destructive_started=True, completed=False, operation="virginize")
    elif reason == "slow":
        window.chk_force_slow_ds2.setChecked(True)
    elif reason == "transport":
        ecu.transport._ser = None
    else:
        window._transmission_recovery_quarantine_id = "unfinished"
    window._ms42_ms43_update_vin()
    assert not window.btn_virginize.isEnabled()
    window._task_busy = window._actuator_cleanup_pending = False
    window._transmission_recovery_quarantine_id = None


def test_virginize_is_independent_of_vin_input_and_existing_rate_toggle(window):
    connected(window)
    window.id_vin_custom.setText("invalid")
    assert not window.btn_id_vin_apply.isEnabled() and window.btn_virginize.isEnabled()
    window.chk_force_slow_ds2.setChecked(True)
    assert not window.btn_virginize.isEnabled()
    window.rb_recovery_auto.setChecked(True)
    assert window.btn_virginize.isEnabled()
    window._set_all_buttons_enabled(False)
    assert not window.btn_virginize.isEnabled()


@pytest.mark.parametrize("family", ["MS42", "MS43"])
@pytest.mark.parametrize("clear_vin", [False, True])
def test_interrupted_virginize_retries_retained_data_without_alignment(
        window, worker_runner, monkeypatch, dialogs, family, clear_vin):
    ecu = connected(window, family)
    dialogs.clear_vin = clear_vin
    forbid_alignment(ecu)
    window._ms42_ms43_show_vin(result(family, OLD_VIN))
    window._last_full_read, window._last_full_read_key = b"old", "old"
    window._identity_isn, window._identity_isn_key = "1234", "old"

    def fail(*args, **kwargs):
        ecu.write_state = SimpleNamespace(destructive_started=True, completed=False, operation="virginize")
        ecu.can_retry_write = True
        raise RuntimeError("Readback differs; keep ignition on for retry")

    monkeypatch.setitem(sys.modules, "ms42_ms43_vin_write", SimpleNamespace(virginize=fail))
    window.btn_virginize.click()
    window._set_all_buttons_enabled(True)
    assert not worker_runner.results[0][0]
    assert dialogs.failures[0][1] == "Virginize Failed"
    assert not dialogs.information
    assert not window.id_vin_current.text() and window._ecu_vin is None
    assert window._last_full_read is window._last_full_read_key is None
    assert window._identity_isn is window._identity_isn_key is None
    assert "unverified" in window.lbl_identity_cache.text()
    assert not window.btn_virginize.isEnabled()
    assert not window.btn_write_tune.isEnabled() and not window.btn_id_read_flash_ecu.isEnabled()
    assert window.btn_native_recovery.isEnabled()
    assert window.btn_native_recovery.text() == "Retry Virginize"
    calls = []

    def retry(*, log_fn, progress_cb):
        calls.append("retry")
        ecu.write_state.completed = True
        return virgin_result(family, clear_vin=clear_vin)

    ecu.retry_write = retry
    window._start_native_flash_recovery()
    assert calls == ["retry"]
    assert "retained virginizing operation" in dialogs.prompts[-1][2]
    assert worker_runner.results[-1][0]
    assert dialogs.information[-1][1] == "ECU Virginized"
    assert window.id_vin_current.text() == ("" if clear_vin else OLD_VIN)


def test_virginize_failure_before_programming_keeps_valid_read_cache(
        window, worker_runner, monkeypatch, dialogs):
    ecu = connected(window)
    window._ms42_ms43_show_vin(result("MS43", OLD_VIN))
    window._last_full_read, window._last_full_read_key = b"old", "old"
    window._identity_isn = "1234"

    def fail(*args, **kwargs):
        ecu.write_state = SimpleNamespace(destructive_started=False, completed=False, operation="virginize")
        raise RuntimeError("Backup disk full")

    monkeypatch.setitem(sys.modules, "ms42_ms43_vin_write", SimpleNamespace(virginize=fail))
    window.btn_virginize.click()
    assert window.id_vin_current.text() == window._ecu_vin == OLD_VIN
    assert window._last_full_read == b"old" and window._last_full_read_key == "old"
    assert window._identity_isn == "1234"
    assert dialogs.failures[0][1] == "Virginize Failed" and not dialogs.information


def test_completed_virginize_followup_failure_does_not_offer_replay(
        window, worker_runner, monkeypatch, dialogs):
    ecu = connected(window)

    def fail(*args, **kwargs):
        ecu.write_state = SimpleNamespace(destructive_started=True, completed=True, operation="virginize")
        ecu.can_retry_write = False
        error = RuntimeError("Completion journal unavailable")
        error.programming_completed = True
        raise error

    monkeypatch.setitem(sys.modules, "ms42_ms43_vin_write", SimpleNamespace(virginize=fail))
    window.btn_virginize.click()
    window._set_all_buttons_enabled(True)
    assert not dialogs.failures and not dialogs.information
    assert dialogs.warnings[0][1] == "Virginize — Follow-up Failed"
    assert "Do not repeat the write" in window.log_view.toPlainText()
    assert not window.btn_native_recovery.isEnabled()
    assert window.btn_id_read_flash_ecu.isEnabled()


@pytest.mark.parametrize("clear_vin", [False, True])
def test_already_virginized_result_reports_no_write(window, dialogs, clear_vin):
    connected(window)
    window._ms42_ms43_virginized(virgin_result("MS43", already=True, clear_vin=clear_vin))
    assert "already virginized; no write was needed" in dialogs.information[0][2]
    assert "alignment was not performed" in dialogs.information[0][2]
    assert ("history are cleared" if clear_vin else "history are preserved") in dialogs.information[0][2]


def test_preserved_malformed_aif_shows_warning_without_stale_history_fields(window, dialogs):
    connected(window)
    window._ms42_ms43_show_info(dict(result("MS43", OLD_VIN), program_part_number="1430355"))
    warning = "VIN/programming history was preserved, but its existing records could not be decoded."
    info = dict(virginized=True, vin_cleared=False, already_virginized=False, family="MS43",
                vin=None, native_vin=None, aif_records=[], programming_count=None, aif_warnings=[warning],
                aif_address=vin_data.AIF_ADDRESSES["MS43"], aif_raw=b"\xAA" * vin_data.AIF_SIZE)
    window._ms42_ms43_virginized(info)
    assert warning in window.id_boot_strings.toPlainText()
    assert not window._ecu_vin and not window.id_vin_current.text()
    assert window._info_labels["AIF Record Date"].text() == "—"
    assert window._info_labels["Recorded Software Number"].text() == "—"
    assert window._info_labels["Programming Count"].text() == "—"
    assert window._info_labels["Current AIF Record"].text() == "—"
    assert window._info_labels["BMW Program Part Number"].text() == "1430355"
    assert "history are preserved" in dialogs.information[0][2]
    assert "EWS alignment was not performed" in dialogs.information[0][2]


def test_ms41_restores_original_ews_controls_and_hides_virginize(window, worker_runner):
    assert window.btn_virginize.isHidden() and not window.btn_virginize.isEnabled()
    connected(window)
    assert not window.btn_virginize.isHidden() and window.btn_virginize.isEnabled()
    window._restore_ms41_labels()
    assert window.btn_virginize.isHidden() and not window.btn_virginize.isEnabled()
    assert window.btn_id_read_ecu.text() == "Read ISN"
    assert window.btn_ews_send.text() == "Send to EWS (0x44)…"
    assert not window.id_ews_isn.isHidden() and not window.ews_isn_label.isHidden()
    window._ds2 = SimpleNamespace(family="MS41")
    window._ms42_ms43_virginize()
    window._ds2 = None
    window._ms42_ms43_virginize()
    assert not worker_runner.results


@pytest.mark.parametrize("size", [(980, 740), (1600, 900), (1920, 1080)])
def test_virginize_and_manual_ews_controls_remain_aligned(window, size):
    connected(window)
    window.resize(*size)
    tab = window.ews_group
    while window.tabs.indexOf(tab) < 0:
        tab = tab.parentWidget()
    window.tabs.setCurrentWidget(tab)
    window.show()
    desktop.QApplication.processEvents()
    buttons = [window.btn_id_read_ecu, window.btn_ews_send, window.btn_virginize]
    assert len({button.height() for button in buttons}) == 1
    assert len({button.geometry().top() for button in buttons}) == 1
    for left, right in zip(buttons, buttons[1:]):
        assert left.geometry().right() < right.geometry().left()
    for button in buttons:
        assert button.geometry().right() <= button.parentWidget().width()
        assert button.fontMetrics().horizontalAdvance(button.text()) <= button.contentsRect().width()


@pytest.mark.parametrize("clear_vin", [False, True])
def test_desktop_worker_reaches_actual_virginize_wire_workflow(
        virgin_workflow, window, worker_runner, monkeypatch, dialogs, clear_vin):
    import ms42_ms43_vin_write as writer

    ecu, peer, address = virgin_workflow
    dialogs.clear_vin = clear_vin
    original_aif = bytes(peer.image[address:address + vin_data.AIF_SIZE])
    expected = target_image(ecu, bytes(peer.image), address, clear_vin=clear_vin)
    desktop.connect(window, ecu.family)
    window._ds2 = ecu
    window._set_all_buttons_enabled(True)
    window._ms42_ms43_show_vin(result(ecu.family, OLD_VIN))
    monkeypatch.setattr(writer, "_journal", lambda *_, **__: Journal())
    window.btn_virginize.click()
    window._set_all_buttons_enabled(True)
    assert len(worker_runner.results) == 1 and worker_runner.results[0][0]
    assert bytes(peer.image) == expected and peer.restored
    assert ecu.last_write.operation == "virginize" and ecu.last_write.completed and ecu.last_write.verified
    assert not ecu.can_retry_write and not peer.agent_finalizations and not peer.finalizations
    if clear_vin:
        assert not window._ecu_vin and not window.id_vin_current.text()
        assert window._ms42_ms43_vin_info["programming_count"] == 0
        assert window.id_boot_strings.toPlainText() == "No recorded AIF entries."
    else:
        assert window._ms42_ms43_vin_info["aif_raw"] == original_aif
        assert window.id_vin_current.text() == vin_data.decode_aif(original_aif)["vin"]
        assert window._ms42_ms43_vin_info["programming_count"] == 14
    assert dialogs.information[0][1] == "ECU Virginized" and not dialogs.failures and not dialogs.warnings
    assert "EWS alignment was not performed" in dialogs.information[0][2]


@pytest.mark.parametrize("clear_vin", [False, True])
def test_desktop_retains_failed_virginize_wire_write_then_explicitly_recovers(
        virgin_workflow, window, worker_runner, monkeypatch, dialogs, clear_vin):
    import ms42_ms43_vin_write as writer

    ecu, peer, address = virgin_workflow
    dialogs.clear_vin = clear_vin
    original_aif = bytes(peer.image[address:address + vin_data.AIF_SIZE])
    expected = target_image(ecu, bytes(peer.image), address, clear_vin=clear_vin)
    desktop.connect(window, ecu.family)
    window._ds2 = ecu
    window._set_all_buttons_enabled(True)
    window._ms42_ms43_show_vin(result(ecu.family, OLD_VIN))
    monkeypatch.setattr(writer, "_journal", lambda *_, **__: Journal())
    peer.agent_fault = "boot_program_ack"
    window.btn_virginize.click()
    window._set_all_buttons_enabled(True)
    assert not worker_runner.results[-1][0] and len(dialogs.failures) == 1
    assert dialogs.failures[0][1] == "Virginize Failed" and not dialogs.information
    assert peer.lost_program and peer.programs.count(peer.lost_program) == 1
    assert ecu.can_retry_write and not ecu.last_write.completed
    assert window.btn_native_recovery.isEnabled()
    assert window.btn_native_recovery.text() == "Retry Virginize"
    assert not window.btn_virginize.isEnabled() and not window.btn_id_read_flash_ecu.isEnabled()
    assert window._ms42_ms43_vin_info is None and not window.id_vin_current.text()
    assert peer.mode == "agent" and not peer.restored
    dialogs.clear_vin = not clear_vin  # The retained operation owns the original selection.
    window._start_native_flash_recovery()
    window._set_all_buttons_enabled(True)
    assert len(worker_runner.results) == 2 and worker_runner.results[-1][0]
    assert dialogs.prompts[-1][1] == "Retry Virginize"
    assert bytes(peer.image) == expected and peer.restored
    assert ecu.last_write.completed and ecu.last_write.verified and not ecu.can_retry_write
    assert peer.erases == [0] and peer.programs.count(peer.lost_program) == 1
    assert not peer.agent_finalizations and not peer.finalizations
    if clear_vin:
        assert not window._ecu_vin and not window.id_vin_current.text()
        assert window._ms42_ms43_vin_info["programming_count"] == 0
        assert window.id_boot_strings.toPlainText() == "No recorded AIF entries."
    else:
        assert window._ms42_ms43_vin_info["aif_raw"] == original_aif
        assert window.id_vin_current.text() == vin_data.decode_aif(original_aif)["vin"]
        assert window._ms42_ms43_vin_info["programming_count"] == 14
    assert len(dialogs.checkboxes) == 1
    assert not window.btn_native_recovery.isVisible()
    assert len(dialogs.information) == 1 and dialogs.information[0][1] == "ECU Virginized"
    assert "EWS alignment was not performed" in dialogs.information[0][2]
