"""Exercise the existing VIN transaction against the exact X001 image."""

import pytest
from PyQt5.QtWidgets import QMessageBox

import ms42_ms43_vin_write as writer
from tests import test_ms42_ms43_vin_workflow as workflow
from tests.test_ms42_ms43_ram_revisions import (
    revision_images as revision_images, x001_workflow as x001_workflow,
)
from tests.test_ms42_ms43_compatibility_baud import (
    compatibility as compatibility, assert_handoff,
)
from tests.test_ms42_ms43_vin_desktop import connected, NEW_VIN
from tests.test_ms42_ms43_desktop import window as window, worker_runner as worker_runner


@pytest.fixture
def x001_vin(x001_workflow, monkeypatch):
    return workflow.vin_workflow.__wrapped__(x001_workflow, monkeypatch)


@pytest.mark.parametrize("mode", ("append", "full", "dirty"))
def test_x001_vin_preserves_flash_and_uses_selected_rate(x001_vin, mode, compatibility):
    workflow.test_vin_write_preserves_whole_array_and_uses_no_finalizer(x001_vin, mode)
    assert_handoff(x001_vin[1], compatibility)


@pytest.mark.parametrize("mode", ("append", "full"))
@pytest.mark.parametrize("fault", ("body_ack", "body_partial", "commit_ack", "commit_partial"))
def test_x001_interrupted_vin_keeps_existing_recovery(x001_vin, monkeypatch, mode, fault):
    workflow.test_interrupted_write_keeps_target_and_explicit_retry_verifies_before_programming(
        x001_vin, monkeypatch, mode, fault)


@pytest.mark.parametrize("fault", ("reset_ack", "journal_finish"))
def test_x001_completed_vin_is_not_replayed(x001_vin, fault):
    workflow.test_verified_write_is_never_replayed_after_completion_error(x001_vin, fault)


def test_x001_desktop_worker_writes_full_history(x001_vin, window, worker_runner, monkeypatch):
    ecu, peer, address = x001_vin
    original = workflow.seed_history(peer, address, "full")
    expected, _ = workflow.expected_image(original, address)
    connected(window)
    window._ds2, window._ecu_cal_id = ecu, "43X001"
    monkeypatch.setattr(writer, "_journal", lambda *_, **__: workflow.Journal())
    monkeypatch.setattr(window, "_prewrite_battery_notice", lambda: "Battery voltage: 13.0 V")
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.Yes)
    monkeypatch.setattr(QMessageBox, "information", lambda *_: None)
    window.id_vin_custom.setText(NEW_VIN)
    assert window.btn_id_vin_apply.isEnabled()
    window._on_identity_vin_apply()
    assert len(worker_runner.results) == 1 and worker_runner.results[0][0]
    assert bytes(peer.image) == expected and peer.restored and not peer.agent_finalizations
    assert window._ecu_cal_id == "43X001"
    assert window.id_vin_current.text() == window._info_labels["VIN"].text() == NEW_VIN
    assert not window.btn_virginize.isEnabled()


def test_x001_vin_controls_do_not_enable_unverified_pairing(window):
    connected(window)
    window._ecu_cal_id = "43X001"
    window.id_vin_custom.setText(NEW_VIN)
    window._ms42_ms43_update_vin()
    assert window.btn_id_vin_apply.isEnabled()
    assert not window.btn_virginize.isEnabled()
    assert "pairing restore/status" in window.btn_virginize.toolTip()
    window.chk_force_slow_ds2.setChecked(True)
    assert not window.btn_id_vin_apply.isEnabled()
    window.rb_recovery_auto.setChecked(True)
    assert window.btn_id_vin_apply.isEnabled()
    window._ecu_cal_id = "430069"
    window._ms42_ms43_update_vin()
    assert window.btn_virginize.isEnabled()
    assert "unavailable" not in window.btn_virginize.toolTip()
    window._ds2 = None
    window._task_busy = True
    try:
        window._ms42_ms43_update_vin()
        assert not window.btn_id_vin_apply.isEnabled() and not window.btn_virginize.isEnabled()
    finally:
        window._task_busy = False
