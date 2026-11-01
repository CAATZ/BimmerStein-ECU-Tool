"""Selected rates across real host workflows with strict injected peers only."""
import pytest

import ds2_fast_safety
import ms42_ms43 as protocol
import ms42_ms43_agent as agent
import ms42_ms43_image as images
import ms42_ms43_resident as resident
import transfer_rates
from tests.test_ms42_ms43 import Journal
from tests.test_ms42_ms43_agent_workflow import agent_workflow as agent_workflow, boot_target
from tests.test_ms42_ms43_desktop import connect, window as window, worker_runner as worker_runner
from tests.test_ms42_ms43_ram_write import expected_array, target_image, write_workflow as write_workflow


@pytest.fixture(params=(False, True), ids=("standard", "compatibility"))
def compatibility(request, monkeypatch):
    monkeypatch.setattr(transfer_rates, "_high_baud", 125000 if request.param else 187500)
    return request.param


def expected_rate(family, compatibility):
    return 125000 if compatibility else 166667 if family == "MS42" else 187500


def assert_handoff(peer, compatibility):
    expected = ({"MS42": "00 30 D4 00 00", "MS43": "00 06 29 00 00"} if compatibility else
                {"MS42": "00 41 1B 00 00", "MS43": "00 09 3E 00 00"})[peer.family]
    assert [args for wire, command, args in peer.requests if (wire, command) == ("resident", 1)] == [
        bytes.fromhex(expected)]
    assert peer.restored and (peer.baudrate, peer.parity, peer.stopbits) == (9600, "E", 1)


@pytest.mark.parametrize("mode", ("full", "tune"))
def test_agent_read_uses_selected_rate_and_restores_diagnostics(agent_workflow, compatibility, mode):
    ecu, peer = agent_workflow
    rates, logs = [], []
    ecu.transfer_rate_cb = lambda *args: rates.append(args)
    with ecu.read_transfer(use_agent=True, log_fn=logs.append) as reader:
        assert reader.profile["baud"] == expected_rate(ecu.family, compatibility)
        data = getattr(reader, "read_" + mode)()
    expected = peer.original if mode == "full" else images.extract_calibration(peer.original)
    assert data == expected and not peer.erases and not peer.programs
    assert (expected_rate(ecu.family, compatibility), "RAM transfer") in rates
    assert any(f"1 KiB transfers at {expected_rate(ecu.family, compatibility):,} baud" in log for log in logs)
    assert peer.upload_readbacks == [(0, 150)]
    assert_handoff(peer, compatibility)


@pytest.mark.parametrize("mode", ("full", "tune"))
def test_agent_write_verifies_and_finalizes_at_selected_rate(agent_workflow, compatibility, mode):
    ecu, peer = agent_workflow
    target = target_image(peer, mode)
    with ecu.transfer():
        state = getattr(ecu, "write_" + mode)(target, use_ram=True, use_agent=True,
                                             verify=True, journal=Journal())
    assert state.completed and state.verified and state.finalized
    assert peer.image == expected_array(peer, target, mode)
    assert peer.image[:0x10000] == peer.original[:0x10000]
    assert peer.agent_finalizations == 1 and peer.upload_readbacks == [(0, 150)]
    assert_handoff(peer, compatibility)


def test_boot_write_retains_permission_and_protected_data_at_selected_rate(agent_workflow, compatibility):
    ecu, peer = agent_workflow
    target = boot_target(peer)
    with ecu.transfer():
        state = ecu.write_full(target, use_ram=True, use_agent=True, write_boot=True,
                               verify=True, journal=Journal())
    expected = expected_array(peer, target, "full")
    expected[:0x4000], expected[0x8000:0x10000] = target[:0x4000], target[0x8000:0x10000]
    assert state.completed and state.verified and state.finalized
    assert peer.image == expected and peer.image[0x4000:0x8000] == peer.original[0x4000:0x8000]
    assert [sector for sector in peer.erases if sector < 4] == [3, 0]
    assert peer.agent_requests[0][:3] == (ord("I"), 1, 4)
    assert_handoff(peer, compatibility)


@pytest.mark.parametrize("mode", ("full", "tune"))
def test_resident_write_uses_same_setting_and_preserves_existing_plan(write_workflow, compatibility, mode):
    ecu, peer = write_workflow
    target = target_image(peer, mode)
    with ecu.transfer():
        state = getattr(ecu, "write_" + mode)(target, use_ram=True, verify=True, journal=Journal())
    assert state.completed and state.verified and state.finalized
    assert peer.image == expected_array(peer, target, mode)
    assert_handoff(peer, compatibility)


def test_profile_is_fixed_before_entry_and_new_operation_uses_new_setting(agent_workflow, compatibility):
    ecu, peer = agent_workflow
    reader = agent.select_reader(ecu)
    transfer_rates.set_compatibility_baud(not compatibility)
    reader.enter()
    assert ecu.baud == expected_rate(ecu.family, compatibility)
    assert reader.read_tune() == images.extract_calibration(peer.original)
    reader.finish()
    assert_handoff(peer, compatibility)
    peer.requests.clear()
    with ecu.read_transfer(use_agent=True) as next_reader:
        assert next_reader.profile["baud"] == expected_rate(ecu.family, not compatibility)
        assert next_reader.read_tune() == images.extract_calibration(peer.original)
    assert_handoff(peer, not compatibility)
    assert resident.PROFILES[ecu.family]["baud"] == expected_rate(ecu.family, False)


def retained_retry(workflow, compatibility, use_agent, monkeypatch):
    ecu, peer = workflow
    target = target_image(peer, "tune")
    peer.drop_next_program = True
    if use_agent:
        original_request = peer.agent_request

        def lose_readback_after_program(command, offset, count, data):
            response = original_request(command, offset, count, data)
            return b"" if peer.lost_program and command == ord("R") else response

        # The custom agent reconciles transient loss; this case must remain unconfirmed.
        monkeypatch.setattr(peer, "agent_request", lose_readback_after_program)
    with pytest.raises(protocol.FlashInterrupted):
        with ecu.transfer():
            ecu.write_tune(target, use_ram=True, use_agent=use_agent, journal=Journal())
    writer = ecu._pending_writer
    assert writer.active and ecu.can_retry_write
    assert ecu.baud == writer.profile["baud"] == expected_rate(ecu.family, compatibility)
    transfer_rates.set_compatibility_baud(not compatibility)
    if use_agent:
        monkeypatch.setattr(peer, "agent_request", original_request)
    with ecu.transfer():
        state = ecu.retry_write(verify=True, journal=Journal())
    assert state.completed and state.verified and not writer.active
    assert writer.profile["baud"] == expected_rate(ecu.family, compatibility)
    assert peer.image == expected_array(peer, target, "tune")
    assert_handoff(peer, compatibility)


def test_agent_retry_keeps_original_rate(agent_workflow, compatibility, monkeypatch):
    retained_retry(agent_workflow, compatibility, True, monkeypatch)


def test_resident_retry_keeps_original_rate(write_workflow, compatibility, monkeypatch):
    retained_retry(write_workflow, compatibility, False, monkeypatch)


@pytest.mark.parametrize("force_slow", (False, True))
def test_stock_read_and_write_fallback_ignore_ram_rate(agent_workflow, compatibility, force_slow):
    ecu, peer = agent_workflow
    peer.unsupported = "boot"
    baud = 9600 if force_slow else 125000
    with ecu.read_transfer(force_slow=force_slow, use_agent=True) as reader:
        assert reader is ecu and ecu.baud == baud
        assert reader.read_tune() == images.extract_calibration(peer.original)
    with ecu.transfer(force_slow=force_slow):
        assert ecu.baud == baud
        state = ecu.write_tune(target_image(peer, "tune"), use_ram=not force_slow,
                               use_agent=True, journal=Journal())
    assert state.completed and not peer.agent_launches
    assert not any(wire == "resident" for wire, *_ in peer.requests)
    assert ecu.baud == 9600


@pytest.mark.parametrize("fault", ("host_fast_baud", "postbaud_silent", "baud_identity"))
def test_failed_rate_change_never_uploads_agent_or_writes_flash(agent_workflow, compatibility, fault):
    ecu, peer = agent_workflow
    peer.fault = fault
    with pytest.raises(protocol.FlashInterrupted):
        with ecu.transfer():
            ecu.write_tune(target_image(peer, "tune"), use_ram=True, use_agent=True, journal=Journal())
    assert not peer.uploaded and not peer.agent_launches and not peer.erases and not peer.programs
    assert not ecu.can_retry_write


@pytest.mark.parametrize("mode", ("full", "tune"))
def test_gui_backup_write_verify_uses_saved_rate(
        agent_workflow, window, worker_runner, compatibility, monkeypatch, mode):
    from PyQt5.QtWidgets import QMessageBox

    ecu, peer = agent_workflow
    target = target_image(peer, mode)
    connect(window, ecu.family)
    window._ds2 = ecu
    assert window._save_transfer_rate(compatibility)
    window.chk_force_slow_ds2.setChecked(False)
    window.chk_bootloader_write.setChecked(False)
    window.chk_backup_before_write.setChecked(True)
    window.chk_verify.setChecked(True)
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.Yes)
    monkeypatch.setattr(window, "_prewrite_battery_notice", lambda: "12.8 V")
    monkeypatch.setattr(window, "_show_flash_complete", lambda *_: None)
    monkeypatch.setattr(ds2_fast_safety, "OperationJournal", lambda *_args, **_kwargs: Journal())
    window._ms42_ms43_write(target, "offline.bin", mode)
    assert worker_runner.results == [(True, None)]
    assert ecu.last_write.completed and ecu.last_write.verified and ecu.last_write.finalized
    assert peer.image == expected_array(peer, target, mode)
    assert len(window._backup_mgr.entries) == 1
    assert_handoff(peer, compatibility)
