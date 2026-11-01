"""Existing boot choices and retained recovery flow, with fake transports only."""

from types import SimpleNamespace

import pytest
from PyQt5.QtWidgets import QInputDialog, QMessageBox

import ms42_ms43 as protocol
import ms42_ms43_image as images
import transfer_rates
from tests import test_ms42_ms43_desktop as desktop
from tests.test_ms42_ms43 import Journal
from tests.test_ms42_ms43_agent_workflow import agent_workflow as agent_workflow
from tests.test_ms42_ms43_compatibility_baud import (
    compatibility as compatibility, expected_rate,
)
from tests.test_ms42_ms43_image import synthetic_image
from tests.test_ms42_ms43_ram_write import expected_array, target_image
from tests.test_ms42_ms43_vin_desktop import connected


window = desktop.window
worker_runner = desktop.worker_runner


@pytest.fixture
def prompts(window, monkeypatch):
    observed = SimpleNamespace(questions=[], boot_prompts=[], completed=[])
    monkeypatch.setattr(QInputDialog, "getText", lambda *args:
                        observed.boot_prompts.append(args) or ("WRITE BOOT", True))
    monkeypatch.setattr(QMessageBox, "question", lambda *args:
                        observed.questions.append(args) or QMessageBox.Yes)
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: pytest.fail(str(args[1:])))
    monkeypatch.setattr(window, "_prewrite_battery_notice", lambda: "Battery voltage: 13.0 V")
    monkeypatch.setattr(window, "_show_flash_complete", lambda *args: observed.completed.append(args))
    return observed


@pytest.mark.parametrize("family", ["MS42", "MS43"])
@pytest.mark.parametrize("preserve", [True, False])
def test_boot_write_forwards_explicit_identity_choice_through_real_worker(
        window, worker_runner, prompts, family, preserve):
    ecu = connected(window, family)
    image = synthetic_image(family)
    window.chk_bootloader_write.setChecked(True)
    window.chk_boot_preserve_identity.setChecked(preserve)
    window.chk_backup_before_write.setChecked(False)
    window.chk_verify.setChecked(False)
    assert window.chk_boot_preserve_identity.isEnabled()
    assert window._ms42_ms43_vin_info is None
    calls = []

    def write(data, *, log_fn, progress_cb, verify, use_ram, use_agent, write_boot, preserve_identity):
        assert data == image and write_boot and use_ram and use_agent and not verify
        calls.append(preserve_identity)
        log_fn("Boot identity choice accepted")
        progress_cb(1, 1, "Boot verified")

    ecu.write_full = write
    window._ms42_ms43_write(image, "donor.bin", "full")
    assert worker_runner.results == [(True, None)]
    assert calls == [preserve]
    assert worker_runner.progress == [(1, 1, "Boot verified")]
    assert len(prompts.questions) == len(prompts.completed) == 1
    assert len(prompts.boot_prompts) == 1
    assert "WRITE BOOT" in prompts.boot_prompts[0][2]
    notice = prompts.questions[0][2]
    assert "Boot code WILL be updated and verified" in notice
    assert "VIN/AIF" in notice and "ISN/pairing" in notice
    assert "engine STOPPED" in notice and "hardware recovery" in notice
    if preserve:
        assert "Live VIN/AIF" in notice and "preserved" in notice
    else:
        assert "will be taken from the selected file" in notice


@pytest.mark.parametrize("family", ["MS42", "MS43"])
@pytest.mark.parametrize("mode,selected_boot", [("full", False), ("tune", False), ("tune", True)])
@pytest.mark.parametrize("preserve", [True, False])
def test_ordinary_full_and_tune_writes_do_not_apply_the_boot_identity_choice(
        window, worker_runner, prompts, family, mode, selected_boot, preserve):
    ecu = connected(window, family)
    full = synthetic_image(family)
    image = full if mode == "full" else images.extract_calibration(full)
    window.chk_bootloader_write.setChecked(selected_boot)
    window.chk_boot_preserve_identity.setChecked(preserve)
    window.chk_backup_before_write.setChecked(False)
    window.chk_verify.setChecked(False)
    calls = []

    def write(data, **options):
        calls.append((bytes(data), options))
        options["progress_cb"](len(data), len(data), "Calibration/program verified")

    setattr(ecu, "write_" + mode, write)
    window._ms42_ms43_write(image, "normal.bin", mode)
    assert worker_runner.results == [(True, None)]
    assert len(calls) == 1 and calls[0][0] == image
    options = calls[0][1]
    assert not options.get("write_boot", False)
    if mode == "tune":
        assert "preserve_identity" not in options and "write_boot" not in options
    assert "Bootloader and protected identity are preserved" in prompts.questions[0][2]
    assert not prompts.boot_prompts


@pytest.mark.parametrize("family", ["MS42", "MS43"])
@pytest.mark.parametrize("entry", ["flash", "bins"])
@pytest.mark.parametrize("response", [("WRITE BOOT", False), ("write boot", True)])
def test_boot_write_requires_typed_acceptance_before_ecu_work(
        window, monkeypatch, family, entry, response):
    ecu = connected(window, family)
    image = synthetic_image(family)
    window.chk_bootloader_write.setChecked(True)
    window.chk_backup_before_write.setChecked(True)
    ecu.transfer = lambda **_: pytest.fail("Cancelled boot write entered a transfer")
    monkeypatch.setattr(window, "_prewrite_battery_notice", lambda: pytest.fail("No ECU work before acceptance"))
    monkeypatch.setattr(window, "_run_task", lambda *_, **__: pytest.fail("Cancelled boot write started a worker"))
    monkeypatch.setattr(QInputDialog, "getText", lambda *_: response)
    monkeypatch.setattr(QMessageBox, "question", lambda *_: pytest.fail("Typed boot confirmation was not accepted"))

    if entry == "flash":
        window._ds2_write_full(image, "donor.bin")
    else:
        backup = SimpleNamespace(file_type="Full ROM", filename="donor.bin", path="donor.bin", sha256="unused")
        monkeypatch.setattr(window, "_selected_backup", lambda: backup)
        monkeypatch.setattr(window._backup_mgr, "read_data", lambda *_: image)
        window._on_backup_flash()


@pytest.mark.parametrize("family", ["MS42", "MS43"])
def test_boot_write_still_requires_final_confirmation(window, worker_runner, prompts, monkeypatch, family):
    connected(window, family)
    window.chk_bootloader_write.setChecked(True)
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.No)
    window._ms42_ms43_write(synthetic_image(family), "donor.bin", "full")
    assert len(prompts.boot_prompts) == 1
    assert not worker_runner.results


@pytest.mark.parametrize("family", ["MS42", "MS43"])
def test_preserve_identity_defaults_on_and_retains_choice_through_control_refreshes(window, family):
    window.chk_boot_preserve_identity.setChecked(False)  # The independent MS41 preference.
    connected(window, family)
    assert window.chk_boot_preserve_identity.isChecked()
    assert not window.chk_boot_preserve_identity.isEnabled()
    window.chk_bootloader_write.setChecked(True)
    assert window.chk_boot_preserve_identity.isEnabled()
    assert window.chk_boot_preserve_identity.text() == "Preserve ECU identity during boot writes"
    window.chk_boot_preserve_identity.setChecked(False)
    window._ms42_ms43_boot_controls()
    window._set_all_buttons_enabled(True)
    assert not window.chk_boot_preserve_identity.isChecked()
    window.chk_bootloader_write.setChecked(False)
    assert not window.chk_boot_preserve_identity.isEnabled()
    window.chk_bootloader_write.setChecked(True)
    assert window.chk_boot_preserve_identity.isEnabled() and not window.chk_boot_preserve_identity.isChecked()
    window.chk_force_slow_ds2.setChecked(True)
    assert not window.chk_boot_preserve_identity.isEnabled()
    window.rb_recovery_auto.setChecked(True)
    window.chk_bootloader_write.setChecked(True)
    assert window.chk_boot_preserve_identity.isEnabled() and not window.chk_boot_preserve_identity.isChecked()
    window._task_busy = True
    window._ms42_ms43_boot_controls()
    assert not window.chk_boot_preserve_identity.isEnabled()
    window._task_busy = False
    window._ms42_ms43_boot_controls()
    assert window.chk_boot_preserve_identity.isEnabled() and not window.chk_boot_preserve_identity.isChecked()
    window._ds2 = None
    window._restore_ms41_labels()
    assert not window.chk_boot_preserve_identity.isChecked()
    assert not hasattr(window, "_ms41_boot_identity_choice")


@pytest.mark.parametrize("reason", ["parity", "software", "retained", "baud"])
def test_identity_choice_is_only_enabled_for_supported_boot_operations(window, reason):
    ecu = connected(window)
    window.chk_bootloader_write.setChecked(True)
    if reason == "parity":
        del ecu.transport._ser.parity
    elif reason == "software":
        window._ecu_cal_id = "430056"
    elif reason == "retained":
        ecu.write_state = SimpleNamespace(destructive_started=True, completed=False)
    else:
        ecu.baud_uncertain = True
    window._ms42_ms43_boot_controls()
    assert not window.chk_bootloader_write.isEnabled()
    assert not window.chk_boot_preserve_identity.isEnabled()


def test_retry_cancellation_keeps_retained_image_and_sends_no_worker(window, worker_runner, monkeypatch):
    ecu = connected(window)
    ecu.write_state = SimpleNamespace(destructive_started=True, completed=False)
    ecu.retry_write = lambda **_: pytest.fail("Cancelled recovery reached backend")
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.No)
    window._ms42_ms43_retry()
    assert not worker_runner.results and not ecu.write_state.completed


def test_retained_retry_worker_reports_original_target_progress(window, worker_runner, prompts):
    ecu = connected(window)
    ecu.write_state = SimpleNamespace(destructive_started=True, completed=False)
    ecu.can_retry_write = True
    window._set_all_buttons_enabled(True)
    calls = []

    def retry(*, progress_cb, log_fn):
        calls.append("original target")
        log_fn("Continuing retained image")
        progress_cb(64, 128, "Recovering retained write")
        progress_cb(128, 128, "Recovery verified")
        ecu.write_state.completed = True
        return ecu.write_state

    ecu.retry_write = retry
    window._start_native_flash_recovery()
    assert calls == ["original target"]
    assert prompts.questions[0][1] == "Retry Same Image"
    assert "same ECU powered and connected" in prompts.questions[0][2]
    assert worker_runner.progress == [(64, 128, "Recovering retained write"), (128, 128, "Recovery verified")]
    assert worker_runner.results[0][0] and prompts.completed[0][0] == "Recovery Complete"


def test_desktop_retained_retry_keeps_original_baud_after_setting_changes(
        agent_workflow, window, worker_runner, compatibility, prompts, monkeypatch):
    ecu, peer = agent_workflow
    image = target_image(peer, "tune")
    original_request = peer.agent_request
    loss = {"started": False, "active": True}

    def missing_program_reply(command, offset, count, data):
        response = original_request(command, offset, count, data)
        if loss["active"]:
            if command == ord("P"):
                loss["started"] = True
                return b""
            if loss["started"] and command == ord("R"):
                return b""  # Persistent loss prevents confirming whether programming completed.
        return response

    monkeypatch.setattr(peer, "agent_request", missing_program_reply)
    with pytest.raises(protocol.FlashInterrupted):
        with ecu.transfer():
            ecu.write_tune(image, use_ram=True, use_agent=True, journal=Journal())
    writer = ecu._pending_writer
    rate = expected_rate(ecu.family, compatibility)
    assert loss["started"] and ecu.can_retry_write and writer.active
    assert ecu.baud == writer.profile["baud"] == rate
    desktop.connect(window, ecu.family)
    window._ds2 = ecu
    window._set_all_buttons_enabled(True)
    assert window.btn_native_recovery.isEnabled()
    assert not window.btn_write_full.isEnabled()
    transfer_rates.set_compatibility_baud(not compatibility)
    loss["active"] = False
    monkeypatch.setattr("ds2_fast_safety.OperationJournal", lambda *_, **__: Journal())
    window._start_native_flash_recovery()
    window._set_all_buttons_enabled(True)
    assert worker_runner.results[-1][0]
    assert ecu.last_write.completed and not ecu.can_retry_write
    assert writer.profile["baud"] == rate and peer.agent_launches == 1
    assert peer.image == expected_array(peer, image, "tune")
    assert peer.restored and ecu.baud == 9600
    assert worker_runner.progress and worker_runner.progress[-1][0] == worker_runner.progress[-1][1]
    assert prompts.questions[-1][1] == "Retry Same Image"
    assert prompts.completed[-1][0] == "Recovery Complete"


@pytest.mark.parametrize("size", [(980, 740), (1600, 900), (1920, 1080)])
def test_existing_boot_controls_keep_alignment_and_scaling(window, size):
    connected(window)
    window.chk_bootloader_write.setChecked(True)
    window.resize(*size)
    tab = window.chk_bootloader_write
    while window.tabs.indexOf(tab) < 0:
        tab = tab.parentWidget()
    window.tabs.setCurrentWidget(tab)
    window.show()
    desktop.QApplication.processEvents()
    boot, preserve = window.chk_bootloader_write, window.chk_boot_preserve_identity
    assert boot.height() == preserve.height()
    assert boot.geometry().top() == preserve.geometry().top()
    assert boot.geometry().right() < preserve.geometry().left()
    for checkbox in (boot, preserve):
        assert checkbox.geometry().right() <= checkbox.parentWidget().width()
        assert checkbox.fontMetrics().horizontalAdvance(checkbox.text()) < checkbox.contentsRect().width()
