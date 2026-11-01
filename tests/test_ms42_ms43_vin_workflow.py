"""Real host VIN workflow over the strict in-memory agent wire peer."""

import json

import pytest

from ds2 import DS2Error
from ms42_ms43 import FlashInterrupted
from ms42_ms43_vin import AIF_ADDRESSES, AIF_SIZE, plan_vin
from ms42_ms43_vin_write import write_vin
from tests.test_ms42_ms43 import Journal, reply
from tests.test_ms42_ms43_agent import packet
from tests.test_ms42_ms43_agent_workflow import agent_workflow as agent_workflow
from tests.test_ms42_ms43_diagnostics import aif_image, aif_record
from tests.test_ms42_ms43_desktop import connect, window as window, worker_runner as worker_runner
from tests.test_ms42_ms43_compatibility_baud import compatibility as compatibility, assert_handoff


VIN = "WBAAM31010FG54321"


@pytest.fixture
def vin_workflow(agent_workflow, monkeypatch):
    ecu, peer = agent_workflow
    address = AIF_ADDRESSES[ecu.family]
    stock = peer.stock

    def respond(command, args):
        if command == 0x0D:
            directory = bytearray(63)
            directory[60:63] = address.to_bytes(3, "big")
            return reply(directory)
        return stock(command, args)

    monkeypatch.setattr(peer, "stock", respond)
    peer.image[address:address + AIF_SIZE] = aif_image(aif_record())
    return ecu, peer, address


def seed_history(peer, address, mode):
    if mode == "full":
        peer.image[address:address + AIF_SIZE] = aif_image(*(
            aif_record(vin=f"WBAAM31010FG{index:05d}") for index in range(14)))
    elif mode == "dirty":
        unfinished = bytearray(aif_record())
        unfinished[:2] = b"\xFF\xFF"
        peer.image[address:address + AIF_SIZE] = aif_image(aif_record(), unfinished)
    return bytes(peer.image)


def expected_image(original, address):
    plan = plan_vin(original[address:address + AIF_SIZE], VIN)
    expected = bytearray(original)
    expected[address:address + AIF_SIZE] = plan.target_history
    return bytes(expected), plan


@pytest.mark.parametrize("mode", ("append", "full", "dirty"))
def test_vin_write_preserves_whole_array_and_uses_no_finalizer(vin_workflow, mode):
    ecu, peer, address = vin_workflow
    original = seed_history(peer, address, mode)
    expected, plan = expected_image(original, address)
    journal, progress = Journal(), []
    result = write_vin(ecu, VIN, journal=journal, progress_cb=lambda *p: progress.append(p))
    assert result["vin"] == result["native_vin"] == VIN
    assert bytes(peer.image) == expected
    assert peer.restored and ecu.baud == 9600 and not ecu.baud_uncertain
    assert ecu.last_write.completed and ecu.last_write.verified and not ecu.can_retry_write
    assert not peer.agent_finalizations and not peer.finalizations
    assert peer.erases == ([0] if mode != "append" else [])
    record = address + plan.record_index * 46
    assert peer.programs[-1] == (record, plan.record[:2])
    if mode == "append":
        assert peer.programs == [(record + 2, plan.record[2:]), (record, plan.record[:2])]
    backup = next(fields for event, fields in journal.events if event == "vin_backup")
    assert bytes.fromhex(backup["original_hex"]) == (
        original[:0x4000] if mode != "append" else original[address:address + AIF_SIZE])
    assert progress[-1] == (1, 1, "VIN written and verified")


@pytest.mark.parametrize("mode", ("append", "full"))
@pytest.mark.parametrize("fault", ("body_ack", "body_partial", "commit_ack", "commit_partial"))
def test_interrupted_write_keeps_target_and_explicit_retry_verifies_before_programming(
        vin_workflow, monkeypatch, mode, fault):
    ecu, peer, address = vin_workflow
    original = seed_history(peer, address, mode)
    expected, plan = expected_image(original, address)
    commit = address + plan.record_index * 46
    request = peer.agent_request
    fired = []

    def inject(command, offset, count, data):
        matching = offset == commit if fault.startswith("commit") else offset != commit
        if command == ord("P") and matching and not fired:
            fired.append(True)
            if fault.endswith("ack"):
                request(command, offset, count, data)
                return b""
            length = 1 if count == 2 else 2
            peer.image[offset:offset + length] = data[:length]
            return packet(command, offset, count, status=4)
        return request(command, offset, count, data)

    monkeypatch.setattr(peer, "agent_request", inject)
    with pytest.raises(FlashInterrupted):
        write_vin(ecu, VIN, journal=Journal())
    assert fired and ecu.can_retry_write and peer.mode == "agent" and not peer.restored
    before_retry = len(peer.agent_requests)
    for operation in (lambda: ecu.identify(), lambda: ecu.write_tune(b""),
                      lambda: write_vin(ecu, "WBAAM31010FG99999", journal=Journal())):
        with pytest.raises(DS2Error, match="incomplete identity write|incomplete write"):
            operation()
    assert len(peer.agent_requests) == before_retry
    result = ecu.retry_write(journal=Journal())
    assert result["vin"] == VIN and bytes(peer.image) == expected
    assert peer.erases == ([0] if mode == "full" else [])
    assert peer.agent_launches == 1 and not peer.agent_finalizations
    assert not ecu.can_retry_write and ecu.last_write.completed
    retry_requests = peer.agent_requests[before_retry:]
    assert retry_requests[0][0] == ord("I") and retry_requests[1][0] == ord("R")
    if fault == "commit_ack":
        assert not any(cmd in (ord("E"), ord("P")) for cmd, *_ in retry_requests)


@pytest.mark.parametrize("committed", (False, True))
def test_full_history_erase_failure_or_lost_reply_can_resume(vin_workflow, monkeypatch, committed):
    ecu, peer, address = vin_workflow
    original = seed_history(peer, address, "full")
    expected, _ = expected_image(original, address)
    request, fired = peer.agent_request, []

    def inject(command, offset, count, data):
        if command == ord("E") and not fired:
            fired.append(True)
            if committed:
                request(command, offset, count, data)
                return b""
            return packet(command, offset, count, status=4)
        return request(command, offset, count, data)

    monkeypatch.setattr(peer, "agent_request", inject)
    with pytest.raises(FlashInterrupted):
        write_vin(ecu, VIN, journal=Journal())
    assert ecu.can_retry_write
    ecu.retry_write(journal=Journal())
    assert bytes(peer.image) == expected and peer.erases == [0]


def test_backup_failure_cannot_send_flash_commands(vin_workflow):
    ecu, peer, _ = vin_workflow

    class FailedBackup(Journal):
        def append(self, event, **fields):
            if event == "vin_backup":
                raise OSError("disk full")
            return super().append(event, **fields)

    with pytest.raises(FlashInterrupted, match="disk full"):
        write_vin(ecu, VIN, journal=FailedBackup())
    assert not peer.erases and not peer.programs and peer.restored
    assert not ecu.can_retry_write and ecu._pending_vin is None


def test_recovery_backup_is_durable_and_exact(vin_workflow, tmp_path):
    ecu, peer, address = vin_workflow
    original = seed_history(peer, address, "full")
    write_vin(ecu, VIN, backup_dir=tmp_path)
    records = [json.loads(line) for line in next(tmp_path.glob("vin-*.jsonl")).read_text().splitlines()]
    backup_index = next(i for i, row in enumerate(records) if row["event"] == "vin_backup")
    erase_index = next(i for i, row in enumerate(records) if row["event"] == "erase_requested")
    assert backup_index < erase_index
    assert bytes.fromhex(records[backup_index]["fields"]["original_hex"]) == original[:0x4000]
    assert records[-1]["fields"]["outcome"] == "success"


@pytest.mark.parametrize("fault", ("reset_ack", "journal_finish"))
def test_verified_write_is_never_replayed_after_completion_error(vin_workflow, fault):
    ecu, peer, address = vin_workflow
    original = bytes(peer.image)
    expected, _ = expected_image(original, address)

    class FailedFinish(Journal):
        def finish(self, outcome, **fields):
            if fault == "journal_finish":
                raise OSError("completion journal unavailable")
            return super().finish(outcome, **fields)

    peer.agent_fault = fault
    with pytest.raises(DS2Error) as error:
        write_vin(ecu, VIN, journal=FailedFinish())
    assert error.value.programming_completed
    assert ecu.last_write.verified and ecu.last_write.completed and not ecu.can_retry_write
    assert ecu._pending_vin is None and bytes(peer.image) == expected
    if fault == "journal_finish":
        assert not ecu.baud_uncertain


def test_invalid_and_unchanged_vin_do_not_enter_agent(vin_workflow):
    ecu, peer, _ = vin_workflow
    with pytest.raises(ValueError):
        write_vin(ecu, "not-a-vin", journal=Journal())
    assert not peer.requests
    result = write_vin(ecu, "WBAAM31010FG12345", journal=Journal())
    assert result["vin"] == "WBAAM31010FG12345"
    assert not peer.agent_launches and not peer.erases and not peer.programs


def test_changed_transport_cannot_resume(vin_workflow):
    ecu, peer, _ = vin_workflow
    peer.agent_fault = "boot_program_ack"
    with pytest.raises(FlashInterrupted):
        write_vin(ecu, VIN, journal=Journal())
    ecu.transport._ser = object()
    assert not ecu.can_retry_write
    with pytest.raises(DS2Error, match="original open RAM-agent"):
        ecu.retry_write(journal=Journal())


@pytest.mark.parametrize("fault", ("authorization_timeout_seed", "authorization_timeout_key",
                                  "authorization_lost_reply", "key_ack", "upload_ack",
                                  "upload_readback", "startup_silent"))
def test_vin_entry_failure_uses_existing_write_reconnection_policy(vin_workflow, fault):
    ecu, peer, _ = vin_workflow
    peer.fault = fault
    with pytest.raises(FlashInterrupted):
        write_vin(ecu, VIN, journal=Journal())
    assert not peer.erases and not peer.programs and not ecu.can_retry_write
    assert ecu.baud_uncertain is (fault != "authorization_timeout_seed")
    if ecu.baud_uncertain:
        before = len(peer.requests)
        with pytest.raises(DS2Error, match="unconfirmed"):
            ecu.identify()
        assert len(peer.requests) == before


def test_vin_uses_selected_compatibility_rate(vin_workflow, compatibility):
    ecu, peer, _ = vin_workflow
    write_vin(ecu, VIN, journal=Journal())
    assert_handoff(peer, compatibility)


@pytest.mark.parametrize("mode", ("append", "full"))
def test_desktop_worker_reaches_actual_vin_wire_workflow(
        vin_workflow, window, worker_runner, monkeypatch, mode):
    from PyQt5.QtWidgets import QMessageBox
    import ms42_ms43_vin_write as writer

    ecu, peer, address = vin_workflow
    original = seed_history(peer, address, mode)
    expected, _ = expected_image(original, address)
    connect(window, ecu.family)
    window._ds2 = ecu
    window.rb_recovery_auto.setChecked(True)
    monkeypatch.setattr(writer, "_journal", lambda *_, **__: Journal())
    monkeypatch.setattr(window, "_prewrite_battery_notice", lambda: "Battery voltage: 13.0 V")
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.Yes)
    monkeypatch.setattr(QMessageBox, "information", lambda *_: None)
    window.id_vin_custom.setText(VIN)
    window._on_identity_vin_apply()
    assert len(worker_runner.results) == 1 and worker_runner.results[0][0]
    assert bytes(peer.image) == expected and peer.restored and not peer.agent_finalizations
    assert window.id_vin_current.text() == window._info_labels["VIN"].text() == VIN


def test_desktop_retains_failed_wire_write_then_explicitly_recovers(
        vin_workflow, window, worker_runner, monkeypatch):
    from PyQt5.QtWidgets import QMessageBox
    import ms42_ms43_vin_write as writer

    ecu, peer, address = vin_workflow
    expected, _ = expected_image(bytes(peer.image), address)
    connect(window, ecu.family)
    window._ds2 = ecu
    window.rb_recovery_auto.setChecked(True)
    monkeypatch.setattr(writer, "_journal", lambda *_, **__: Journal())
    monkeypatch.setattr(window, "_prewrite_battery_notice", lambda: "Battery voltage: 13.0 V")
    for name in ("critical", "warning", "information"):
        monkeypatch.setattr(QMessageBox, name, lambda *_: None)
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.Yes)
    peer.agent_fault = "boot_program_ack"
    window.id_vin_custom.setText(VIN)
    window._on_identity_vin_apply()
    # The synchronous worker fixture omits _run_task's final control refresh.
    window._set_all_buttons_enabled(True)
    assert not worker_runner.results[-1][0]
    assert ecu.can_retry_write and window.btn_native_recovery.isEnabled()
    assert window.btn_native_recovery.text() == "Retry VIN Write"
    assert window.id_vin_current.text() != "WBAAM31010FG12345"
    assert not window.btn_id_read_flash_ecu.isEnabled()
    window._start_native_flash_recovery()
    window._set_all_buttons_enabled(True)
    assert worker_runner.results[-1][0] and not ecu.can_retry_write
    assert bytes(peer.image) == expected and peer.restored
    assert window.id_vin_current.text() == VIN and not window.btn_native_recovery.isVisible()


@pytest.mark.parametrize("moment", ("body", "commit", "complete"))
def test_callback_failure_cannot_lose_recovery_or_replay_completed_write(vin_workflow, moment):
    ecu, peer, address = vin_workflow
    expected, _ = expected_image(bytes(peer.image), address)
    failed = []

    def progress(done, total, label):
        fail = (moment == "body" and label == "Committing VIN"
                or moment == "commit" and label == "Restoring diagnostics"
                or moment == "complete" and label == "VIN written and verified")
        if fail and not failed:
            failed.append(True)
            raise RuntimeError("callback failed")

    with pytest.raises(DS2Error) as error:
        write_vin(ecu, VIN, journal=Journal(), progress_cb=progress)
    assert failed
    if moment == "body":
        assert ecu.can_retry_write and peer.mode == "agent"
        ecu.retry_write(journal=Journal())
    else:
        assert error.value.programming_completed and not ecu.can_retry_write
        assert ecu.baud_uncertain is (moment == "commit")
    assert bytes(peer.image) == expected
