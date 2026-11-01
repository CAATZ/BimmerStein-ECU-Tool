"""RAM finalization host/worker contracts over injected transports only.

Native firmware parity and the payload's actual flash validation are tested in
the separate C167 emulator; this peer deliberately supplies finalizer outcomes.
"""
import pytest

import ds2_fast_safety
import ms42_ms43 as protocol
import ms42_ms43_agent as agent
from tests.test_ms42_ms43 import Journal
from tests.test_ms42_ms43_agent import packet, service as service
from tests.test_ms42_ms43_agent_workflow import agent_workflow as agent_workflow
from tests.test_ms42_ms43_desktop import connect as desktop_connect
from tests.test_ms42_ms43_desktop import window as window, worker_runner as worker_runner
from tests.test_ms42_ms43_ram_write import expected_array, target_image


def write(ecu, peer, *, mode="tune", verify=False, journal=None, progress=None):
    with ecu.transfer():
        return getattr(ecu, "write_" + mode)(
            target_image(peer, mode), use_ram=True, use_agent=True, verify=verify,
            journal=journal if journal is not None else Journal(), progress_cb=progress)


def test_finalize_wire_has_no_arguments_or_response_data(service):
    reader, pipe = service
    assert reader._transfer(agent.FINALIZE, 0, 0) == b""
    assert pipe.requests == [packet(ord("F"), 0, 0)]
    assert not pipe.pending


@pytest.mark.parametrize("offset,count,data", ((1, 0, b""), (0, 2, b""), (0, 0, b"xx")))
def test_finalize_rejects_arguments_before_sending(service, offset, count, data):
    reader, pipe = service
    with pytest.raises(ValueError):
        reader._transfer(agent.FINALIZE, offset, count, data)
    assert not pipe.requests


@pytest.mark.parametrize("status", range(7, 17))
def test_finalize_errors_keep_specific_reason_and_do_not_retry(service, status):
    reader, pipe = service
    pipe.fault = status
    with pytest.raises(agent.AgentRejected) as raised:
        reader._transfer(agent.FINALIZE, 0, 0)
    assert raised.value.command == agent.FINALIZE and raised.value.status == status
    assert agent.STATUS[status] in str(raised.value)
    assert len(pipe.requests) == 1


@pytest.mark.parametrize("mode", ("tune", "full"))
@pytest.mark.parametrize("verify", (False, True))
def test_agent_commits_checks_markers_and_resets_without_second_authorization(
        agent_workflow, mode, verify):
    ecu, peer = agent_workflow
    peer.authorization_delay = 30.0  # The post-reset auth timer is irrelevant.
    journal, progress, start = Journal(), [], protocol.time.monotonic()

    def report(done, total, label):
        progress.append((done, total, label, ecu.last_write.completed, journal.closed))

    state = write(ecu, peer, mode=mode, verify=verify, journal=journal, progress=report)
    assert state.completed and state.finalized and state.verified is verify
    assert peer.image == expected_array(peer, target_image(peer, mode), mode)
    commands = [command for command, *_ in peer.agent_requests]
    final = commands.index(ord("F"))
    assert commands.count(ord("F")) == commands.count(ord("X")) == 1
    assert not any(command in (ord("P"), ord("E")) for command in commands[final + 1:])
    assert peer.agent_requests[final + 1:] == [
        (ord("R"), peer.contract["program_marker"], 4, b""),
        (ord("R"), peer.contract["calibration_marker"], 4, b""),
        (ord("X"), 0, 0, b""),
    ]
    reset = next(index for index, (mode_name, command, _args) in enumerate(peer.requests)
                 if mode_name == "agent" and command == ord("X"))
    assert [command for _mode, command, _args in peer.requests[reset + 1:]] == [0, 6, 6]
    assert peer.markers_at_reset == (bytes.fromhex("90482442"), bytes.fromhex("42244890"))
    assert not peer.finalizations and peer.agent_finalizations == 1
    assert peer.restored and ecu.rate_confirmed and not ecu.can_retry_write
    assert ecu._pending_image is ecu._pending_writer is None
    assert protocol.time.monotonic() - start < 1.0
    assert [fields["phase"] for event, fields in journal.events if event == "phase_started"] == [
        "RAM finalization", "completion markers", "restore diagnostics"]
    assert all(completed == (label == f"{ecu.family} restoring diagnostics")
               for _done, total, label, completed, _closed in progress if total == 0)
    assert progress[-1][2:] == (f"{ecu.family} write complete", True, True)
    assert journal.events[-1][1]["outcome"] == "success"


@pytest.mark.parametrize("qualified", (False, True))
def test_target_completion_route_is_selected_before_erase(agent_workflow, monkeypatch, qualified):
    ecu, peer = agent_workflow
    target = bytearray(target_image(peer, "full"))
    if not qualified:
        software = b"0110FF" if ecu.family == "MS42" else b"430068"
        target[peer.layout.sw_offset:peer.layout.sw_offset + 6] = software
        target[peer.layout.calibration_offset + 8:peer.layout.calibration_offset + 14] = software
    erase, selected = agent.AgentWriter._erase, []

    def check_selection(writer, *args):
        selected.append(writer.ram_finalize)
        assert writer.ram_finalize is qualified
        return erase(writer, *args)

    monkeypatch.setattr(agent.AgentWriter, "_erase", check_selection)
    with ecu.transfer():
        state = ecu.write_full(target, use_ram=True, use_agent=True, journal=Journal())
    assert state.completed and selected
    assert peer.agent_finalizations == int(qualified)
    assert bool(peer.finalizations) is not qualified


def test_partial_hardware_suffix_keeps_existing_family_compatibility(agent_workflow):
    ecu, peer = agent_workflow
    target = bytearray(target_image(peer, "tune"))
    target[0x26:0x28] = b"99"
    with ecu.transfer():
        state = ecu.write_tune(target, use_ram=True, use_agent=True, journal=Journal())
    assert state.completed and peer.agent_finalizations == 1
    assert ecu._identity_fields()["hardware_id"] == peer.original[
        peer.layout.hw_offset:peer.layout.hw_offset + 8].decode("ascii")


@pytest.mark.parametrize("status", (7, 8, 9, 10, 11, 12, 13, 14, 15, 16))
def test_rejected_finalization_keeps_agent_and_target_without_reset_or_fallback(agent_workflow, status):
    ecu, peer = agent_workflow
    peer.agent_finalize_status = status
    journal = Journal()
    with pytest.raises(protocol.FlashInterrupted) as raised:
        write(ecu, peer, journal=journal)
    assert isinstance(raised.value.__cause__, agent.AgentRejected)
    assert raised.value.__cause__.status == status
    assert peer.agent_finalizations == 1 and not peer.finalizations
    assert peer.mode == "agent" and not peer.restored and ecu.can_retry_write
    assert ecu._pending_writer.active and ecu._pending_image == target_image(peer, "tune")
    assert not ecu.last_write.completed and not ecu.last_write.finalized
    assert not any(command == ord("X") for command, *_ in peer.agent_requests)
    assert journal.events[-1][1]["outcome"] == "commit_unknown"


@pytest.mark.parametrize("phase,finalizations", (("RAM finalization", 0), ("completion markers", 1)))
def test_cancellation_around_finalization_stops_io_and_preserves_explicit_retry(
        agent_workflow, phase, finalizations):
    ecu, peer = agent_workflow
    journal, stopped_at, cancelled = Journal(), [], False

    def check_operation():
        if cancelled:
            raise RuntimeError("cancelled")

    def report(_done, total, _label):
        nonlocal cancelled
        if total == 0 and ecu.last_write.phase == phase:
            stopped_at.append(len(peer.agent_requests))
            cancelled = True

    ecu.transport.operation_check = check_operation
    with pytest.raises(protocol.FlashInterrupted, match="cancelled"):
        write(ecu, peer, journal=journal, progress=report)
    assert stopped_at == [len(peer.agent_requests)]
    assert peer.agent_finalizations == finalizations and not peer.finalizations
    assert not any(command == ord("X") for command, *_ in peer.agent_requests)
    assert peer.mode == "agent" and not peer.restored
    assert ecu.can_retry_write and ecu._pending_writer.active
    assert ecu._pending_image == target_image(peer, "tune")
    assert not ecu.last_write.completed and not ecu.last_write.finalized
    assert journal.events[-1][1]["outcome"] == "commit_unknown"

    cancelled = False
    with ecu.transfer():
        state = ecu.retry_write(journal=Journal())
    assert state.completed and state.finalized and not ecu.can_retry_write
    assert peer.agent_launches == 1 and peer.agent_finalizations == finalizations + 1
    assert peer.image == expected_array(peer, target_image(peer, "tune"), "tune")
    assert not peer.finalizations


@pytest.mark.parametrize("fault", ("finalize_ack", "finalize_crc"))
def test_ambiguous_commit_never_replays_but_explicit_retry_preserves_target(agent_workflow, fault):
    ecu, peer = agent_workflow
    peer.agent_fault = fault
    with pytest.raises(protocol.FlashInterrupted):
        write(ecu, peer)
    assert peer.agent_finalizations == 1 and not peer.restored
    assert ecu.can_retry_write and ecu._pending_writer.active
    assert not ecu.last_write.completed and not ecu.last_write.finalized
    assert not any(command == ord("X") for command, *_ in peer.agent_requests)
    peer.agent_fault = None
    with ecu.transfer():
        state = ecu.retry_write(journal=Journal())
    assert state.completed and state.finalized
    assert peer.agent_finalizations == 2 and peer.agent_launches == 1
    assert peer.image == expected_array(peer, target_image(peer, "tune"), "tune")
    assert not peer.finalizations


@pytest.mark.parametrize("marker", ("program", "calibration"))
def test_accepted_finalizer_with_missing_marker_does_not_reset_or_succeed(agent_workflow, marker):
    ecu, peer = agent_workflow
    peer.missing_marker = marker
    with pytest.raises(protocol.FlashInterrupted, match="did not finalize the marker"):
        write(ecu, peer)
    assert ecu.can_retry_write and ecu._pending_writer.active
    assert not ecu.last_write.completed and not ecu.last_write.finalized
    assert peer.agent_finalizations == 1 and not peer.restored
    assert not any(command == ord("X") for command, *_ in peer.agent_requests)


@pytest.mark.parametrize("fault", ("reset_ack", "reconnect", "restored_identity", "restored_identity_read"))
def test_post_commit_restore_failure_reports_programmed_without_replay(agent_workflow, fault):
    ecu, peer = agent_workflow
    rates = []
    ecu.transfer_rate_cb = lambda baud, source: rates.append((baud, source))
    if fault == "reset_ack":
        peer.agent_fault = fault
    else:
        peer.fault = fault
    journal = Journal()
    with pytest.raises(protocol.ProgrammingCompletedError) as raised:
        write(ecu, peer, journal=journal)
    assert peer.agent_finalizations == 1 and peer.restored
    assert raised.value.programming_completed
    assert ecu.last_write.completed and ecu.last_write.finalized
    assert ecu._pending_image is ecu._pending_writer is None
    assert not ecu.can_retry_write
    assert rates[-1] == ((9600, "DS2 connection") if fault == "restored_identity_read"
                         else (0, "unconfirmed"))
    assert journal.closed and journal.events[-1][1]["outcome"] == "failed"
    assert journal.events[-1][1]["programming_completed"] is True
    assert [event for event, _fields in journal.events].count("finalize_accepted") == 1
    assert sum(command == ord("X") for command, *_ in peer.agent_requests) == 1
    assert not peer.finalizations
    count = len(peer.requests)
    with pytest.raises(protocol.DS2Error, match="No incomplete write"):
        ecu.retry_write(journal=Journal())
    assert len(peer.requests) == count


def test_post_commit_journal_failure_preserves_the_reset_error(agent_workflow):
    ecu, peer = agent_workflow
    peer.agent_fault = "reset_ack"

    class FailedJournal(Journal):
        def finish(self, outcome, **fields):
            raise OSError("journal disk full")

    with pytest.raises(protocol.ProgrammingCompletedError) as raised:
        write(ecu, peer, journal=FailedJournal())
    assert isinstance(raised.value.original_error, protocol.DS2Timeout)
    assert raised.value.__cause__ is raised.value.original_error
    assert "Incomplete RAM agent response header" in str(raised.value)
    assert "Completion journal also failed: journal disk full" in str(raised.value)
    assert ecu.last_write.completed and not ecu.can_retry_write
    assert ecu._pending_image is ecu._pending_writer is None
    assert peer.agent_finalizations == 1 and peer.restored


@pytest.mark.parametrize("fault, error_type, message", (
    ("reset_ack", protocol.DS2Timeout, "Incomplete RAM agent response header"),
    ("restored_identity", protocol.DS2Error, "ECU identity changed after the RAM write"),
))
def test_post_commit_feedback_failure_preserves_the_operation_error(
        agent_workflow, fault, error_type, message):
    ecu, peer = agent_workflow
    if fault == "reset_ack":
        peer.agent_fault = fault
    else:
        peer.fault = fault
    reports = []

    def feedback(baud, source):
        reports.append((baud, source))
        if (baud, source) == (0, "unconfirmed"):
            raise RuntimeError("feedback receiver unavailable")

    ecu.transfer_rate_cb = feedback
    journal = Journal()
    with pytest.raises(protocol.ProgrammingCompletedError, match=message) as raised:
        write(ecu, peer, journal=journal)
    assert type(raised.value.original_error) is error_type
    assert raised.value.__cause__ is raised.value.original_error
    assert reports.count((0, "unconfirmed")) == 1
    assert ecu.last_write.completed and not ecu.can_retry_write
    assert ecu._pending_image is ecu._pending_writer is None
    assert journal.closed and journal.events[-1][1]["programming_completed"] is True
    assert peer.agent_finalizations == 1 and peer.restored


@pytest.mark.parametrize("stage", ("finalize_accepted", "finish"))
def test_completed_agent_write_is_not_retained_after_completion_journal_failure(agent_workflow, stage):
    ecu, peer = agent_workflow

    class FailedJournal(Journal):
        def append(self, event, **fields):
            if event == stage:
                raise OSError("completion journal unavailable")
            return super().append(event, **fields)

        def finish(self, outcome, **fields):
            if stage == "finish" and outcome == "success":
                raise OSError("completion journal unavailable")
            return super().finish(outcome, **fields)

    with pytest.raises(protocol.ProgrammingCompletedError):
        write(ecu, peer, journal=FailedJournal())
    assert ecu.last_write.completed and ecu.last_write.finalized
    assert not ecu.can_retry_write and ecu._pending_writer is ecu._pending_image is None
    assert peer.agent_finalizations == 1 and peer.restored is (stage == "finish")


@pytest.mark.parametrize("fault", ("finalize", "reset", "identity"))
def test_worker_reports_failure_without_success_or_completed_write_replay(
        agent_workflow, window, worker_runner, monkeypatch, fault):
    from PyQt5.QtWidgets import QMessageBox

    ecu, peer = agent_workflow
    if fault == "finalize":
        peer.agent_finalize_status = 9
    elif fault == "reset":
        peer.agent_fault = "reset_ack"
    else:
        peer.fault = "restored_identity"
    desktop_connect(window, ecu.family)
    window._ds2 = ecu
    window.chk_force_slow_ds2.setChecked(False)
    window.chk_backup_before_write.setChecked(False)
    window.chk_verify.setChecked(False)
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.Yes)
    monkeypatch.setattr(window, "_prewrite_battery_notice", lambda: "12.8 V")
    monkeypatch.setattr(ds2_fast_safety, "OperationJournal", lambda *_args, **_kwargs: Journal())
    completed = []
    monkeypatch.setattr(window, "_show_flash_complete", lambda *args: completed.append(args))
    programmed = fault != "finalize"
    error_type = protocol.ProgrammingCompletedError if programmed else protocol.FlashInterrupted
    with pytest.raises(error_type):
        window._ms42_ms43_write(target_image(peer, "tune"), "offline.bin", "tune")
    assert len(worker_runner.results) == 1 and worker_runner.results[0][0] is False
    assert not completed and ecu.last_write.completed is programmed
    assert ecu.can_retry_write is not programmed
    assert peer.agent_finalizations == 1 and peer.restored is programmed
    window._set_all_buttons_enabled(True)
    assert window.btn_native_recovery.isEnabled() is not programmed
