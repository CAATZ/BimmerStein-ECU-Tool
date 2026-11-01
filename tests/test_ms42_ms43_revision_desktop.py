"""Revision-changing full writes refresh desktop state without extra ECU reads."""

from types import SimpleNamespace

import pytest
from PyQt5.QtWidgets import QMessageBox

import ms42_ms43_image as images
from ms42_ms43 import ProgrammingCompletedError
from tests import test_ms42_ms43_desktop as desktop
from tests.test_ms42_ms43_image import synthetic_image
from tests.test_ms42_ms43_recovery_desktop import prompts as prompts
from tests.test_ms42_ms43_vin_desktop import connected


window = desktop.window
worker_runner = desktop.worker_runner


def revision_image(family, software):
    data = bytearray(synthetic_image(family))
    profile = images.PROFILES[family]
    for offset in (profile.sw_offset, profile.calibration_offset + 8):
        data[offset:offset + 6] = software.encode("ascii")
    return bytes(data)


def prepare(window, software="430069", family="MS43"):
    ecu = connected(window, family)
    window._ms42_ms43_connected(images.image_identity(revision_image(family, software), family))
    window.chk_backup_before_write.setChecked(False)
    window.chk_verify.setChecked(False)
    window._last_full_read = b"old full image"
    window._last_full_read_key = ("old connection", software)
    window._identity_isn = b"\x12\x34"
    window._identity_isn_key = window._last_full_read_key
    window._session_backup_read = True
    window._config_live_values_read = True
    window._config_live_baseline = {"old revision": 1}
    window._ms42_ms43_vin_info = {"vin": "WBAAM31010FG12345"}
    window.id_vin_current.setText("WBAAM31010FG12345")
    window.id_vin_custom.setText("WBAAM31010FG54321")
    for method in ("identify", "_identity_fields", "read_memory", "send_command"):
        setattr(ecu, method, lambda *a, **kw: pytest.fail("Completion must not query the ECU"))
    return ecu


def assert_refreshed(window, ecu, software):
    assert window._ds2 is ecu and window._ecu_cal_id == software
    assert window._info_labels["Calibration ID"].text() == software
    assert window._ms42_ms43_identity_info["software_id"] == software
    assert window._last_full_read is window._last_full_read_key is None
    assert window._identity_isn is window._identity_isn_key is None
    assert not window._config_live_values_read and window._config_live_baseline == {}
    assert not window._session_backup_read
    assert window._ms42_ms43_vin_info is None and window.id_vin_current.text() == ""
    assert window.id_vin_custom.text() == ""
    assert window.chk_bootloader_write.isEnabled()
    assert window.btn_virginize.isEnabled() == (software == "430069")
    window.id_vin_custom.setText("WBAAM31010FG54321")
    assert window.btn_id_vin_apply.isEnabled()


@pytest.mark.parametrize("family,software,vin,pairing", [
    ("MS42", "0110C6", True, True), ("MS42", "0110C7", True, True),
    ("MS42", "0110CA", True, True), ("MS42", "0110CB", False, False),
    ("MS43", "430037", True, True), ("MS43", "430055", True, True),
    ("MS43", "430064", True, True), ("MS43", "430065", True, True),
    ("MS43", "430066", True, True), ("MS43", "430069", True, True),
    ("MS43", "43X001", True, False), ("MS43", "430056", False, False),
])
def test_identity_actions_follow_verified_application_without_querying_ecu(
        window, family, software, vin, pairing):
    prepare(window, software, family)
    window.id_vin_custom.setText("WBAAM31010FG54321")
    window._ms42_ms43_update_vin()
    assert window.btn_id_vin_apply.isEnabled() is vin
    assert window.btn_virginize.isEnabled() is pairing


@pytest.mark.parametrize("current,target", (("430069", "43X001"), ("43X001", "430069")))
@pytest.mark.parametrize("verify", (False, True))
def test_full_write_refreshes_revision_controls_only_after_success(
        window, worker_runner, prompts, current, target, verify):
    ecu = prepare(window, current)
    image = revision_image("MS43", target)
    window.chk_verify.setChecked(verify)
    calls = []

    def write(data, **options):
        assert window._ecu_cal_id == current
        assert window._config_live_values_read
        assert data == image and options["verify"] is verify
        calls.append(data)
        return SimpleNamespace(completed=True)

    ecu.write_full = write
    window._ms42_ms43_write(image, "target.bin", "full")
    assert calls == [image] and len(prompts.completed) == 1
    assert worker_runner.results == [(True, None)]
    assert_refreshed(window, ecu, target)


@pytest.mark.parametrize("current,target", (("430069", "43X001"), ("43X001", "430069")))
def test_successful_full_retry_uses_original_target_before_backend_clears_it(
        window, worker_runner, prompts, current, target):
    ecu = prepare(window, current)
    ecu.write_state = SimpleNamespace(operation="full", destructive_started=True, completed=False)
    ecu._pending_image = revision_image("MS43", target)
    target_bytes = ecu._pending_image

    def retry(**options):
        assert ecu._pending_image == target_bytes and window._ecu_cal_id == current
        ecu._pending_image = None
        ecu.write_state.completed = True
        return ecu.write_state

    ecu.retry_write = retry
    window._ms42_ms43_retry()
    assert worker_runner.results == [(True, ecu.write_state)]
    assert prompts.completed[0][0] == "Recovery Complete"
    assert_refreshed(window, ecu, target)


@pytest.mark.parametrize("family,software", (("MS42", "0110C6"), ("MS43", "430069")))
@pytest.mark.parametrize("mode", ("full", "tune"))
def test_same_revision_full_and_tune_keep_existing_completion_behavior(
        window, worker_runner, prompts, family, software, mode):
    ecu = prepare(window, software, family)
    image = revision_image(family, software)
    if mode == "tune":
        image = images.extract_calibration(image)
    setattr(ecu, "write_" + mode, lambda *a, **kw: SimpleNamespace(completed=True))
    window._ms42_ms43_write(image, "same.bin", mode)
    assert worker_runner.results == [(True, None)] and len(prompts.completed) == 1
    assert window._ecu_cal_id == software and window._last_full_read == b"old full image"
    assert window._config_live_values_read and window._config_live_baseline == {"old revision": 1}
    assert window._ms42_ms43_vin_info == {"vin": "WBAAM31010FG12345"}


@pytest.mark.parametrize("outcome", ("cancelled", "failed", "retry_failed"))
def test_cancelled_or_failed_conversion_does_not_claim_new_revision(
        window, worker_runner, prompts, monkeypatch, outcome):
    ecu = prepare(window)
    image = revision_image("MS43", "43X001")
    failures = []
    monkeypatch.setattr(QMessageBox, "critical", lambda *args: failures.append(args[1:]))

    def failed(*args, **options):
        raise RuntimeError("incomplete flash")

    if outcome == "cancelled":
        monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.No)
        ecu.write_full = lambda *a, **kw: pytest.fail("Cancelled write reached backend")
        window._ms42_ms43_write(image, "target.bin", "full")
    else:
        ecu.write_full = ecu.retry_write = failed
        ecu._pending_image = image
        ecu.write_state = SimpleNamespace(operation="full", destructive_started=True, completed=False)
        if outcome == "retry_failed":
            window._ms42_ms43_retry()
        else:
            window._ms42_ms43_write(image, "target.bin", "full")
        assert failures == [("Operation Failed", "incomplete flash")]
        assert ecu.write_state.completed is False and ecu._pending_image == image
    assert window._ecu_cal_id == "430069"
    assert window._config_live_values_read and window._last_full_read == b"old full image"
    assert not prompts.completed


@pytest.mark.parametrize("current,target", (("430069", "43X001"), ("43X001", "430069")))
@pytest.mark.parametrize("retry", (False, True))
@pytest.mark.parametrize("uncertain", (False, True))
def test_completed_flash_followup_failure_refreshes_revision_without_claiming_a_connection(
        window, worker_runner, prompts, monkeypatch, current, target, retry, uncertain):
    ecu = prepare(window, current)
    image = revision_image("MS43", target)
    state = SimpleNamespace(operation="full", destructive_started=True, completed=False)
    ecu.write_state = state
    ecu._pending_image = image
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args[1:]))
    monkeypatch.setattr(QMessageBox, "critical", lambda *args: pytest.fail("Completed flash needs a warning"))
    window.lbl_status.setText("Previous connection status")
    window._ms42_ms43_show_transfer_rate(0 if uncertain else 9600, "unconfirmed" if uncertain else "DS2")
    previous_rate = window.lbl_observed_rate.text()
    error = ProgrammingCompletedError(RuntimeError("post-reset identity unavailable"), state)

    def committed(*args, **kwargs):
        state.completed = True
        ecu._pending_image = None
        ecu.baud_uncertain = uncertain
        ecu.baud = 9600
        raise error

    ecu.write_full = ecu.retry_write = committed
    if retry:
        window._ms42_ms43_retry()
    else:
        window._ms42_ms43_write(image, "target.bin", "full")
    assert worker_runner.results == [(False, error)]
    assert warnings == [("Programming Completed — Follow-up Failed", str(error))]
    assert not prompts.completed
    assert window._ecu_cal_id == target
    assert window._info_labels["Calibration ID"].text() == target
    assert window._last_full_read is window._last_full_read_key is None
    assert window._identity_isn is window._identity_isn_key is None
    assert not window._config_live_values_read and window._config_live_baseline == {}
    assert window._ms42_ms43_vin_info is None and not window.id_vin_current.text()
    assert window.lbl_status.text() == "Previous connection status"
    assert window.lbl_observed_rate.text() == previous_rate and ecu.baud_uncertain is uncertain
    assert ecu.write_state is state and state.completed and ecu._pending_image is None
    assert window.btn_virginize.isEnabled() == (target == "430069" and not uncertain)


def test_followup_error_without_committed_state_does_not_infer_a_revision(window, monkeypatch):
    ecu = prepare(window)
    info = images.image_identity(revision_image("MS43", "43X001"))
    error = ProgrammingCompletedError(RuntimeError("unknown completion"), SimpleNamespace(completed=False))
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args[1:]))
    window._ms42_ms43_full_write_failed(error, ecu, info)
    assert window._ecu_cal_id == "430069" and window._last_full_read == b"old full image"
    assert warnings == [("Programming Completed — Follow-up Failed", str(error))]


def test_completion_cannot_replace_a_different_current_connection(window):
    original = prepare(window)
    replacement = connected(window, "MS42")
    window._ms42_ms43_written_revision(original, images.image_identity(revision_image("MS43", "43X001")))
    assert window._ds2 is replacement and window._ecu_cal_id == "0110C6"
