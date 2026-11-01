"""Both native rate profiles use paired selectors and retain their session rate."""

import pytest

import transfer_rates
from ds2_fast_contracts import (
    CommitUnknownError, FrameValidationError, LinkRate, SessionState,
)
from ds2_fast_partial_write import (
    NativeFastPartialWriteTransport, PartialWriteCancelled, PartialWriteStateError,
    UnsafePartialWriteCommand,
)
from ds2_fast_read import FastReadStateError
from tests.test_ds2_fast_full_write import FullMemoryJournal, _authorization_full_session
from tests.test_ds2_fast_partial_write import IDENTITY, _authorization_session
from tests.test_ds2_fast_read import ReadOnlyStockSerial, _session as _read_session
from tests.test_ds2_fast_slim_write import _qualification_session, _sparse_driver_session


@pytest.fixture(params=[187500, 125000])
def high_baud(request, monkeypatch):
    monkeypatch.setattr(transfer_rates, "_high_baud", request.param)
    return request.param


def _change_preference_after_session_started(high_baud):
    transfer_rates.set_compatibility_baud(high_baud != 125000)


def _assert_selected_wire_rate(serial, high_baud, *, cleanup):
    assert {baud for baud, _command, _args in serial.requests} == {9600, high_baud}
    selectors = [args[0] for _baud, command, args in serial.requests
                 if command == 0x90 and len(args) == 11]
    expected = [0x02 if high_baud == 125000 else 0x01]
    assert selectors == expected + ([0x26] if cleanup else [])


@pytest.mark.parametrize("operation", ["partial", "full", "eeprom"])
def test_reads_snapshot_selected_rate_and_return_to_low(high_baud, operation):
    serial = ReadOnlyStockSerial()
    session = _read_session(serial)
    _change_preference_after_session_started(high_baud)

    if operation == "eeprom":
        result = session.read_eeprom_record("MS41.2", 0x1CA)
    else:
        result = getattr(session, "read_" + operation)()

    assert result.rate_profile.high == high_baud
    assert result.final_link is LinkRate.LOW
    _assert_selected_wire_rate(serial, high_baud, cleanup=True)


def test_partial_write_and_verify_snapshot_selected_rate(high_baud, tmp_path):
    session, serial, target, _original = _sparse_driver_session(tmp_path, modify_target=True)
    session.verify_write = True
    _change_preference_after_session_started(high_baud)

    result = session.execute()

    assert result.verified and result.verified_bytes == len(target)
    assert result.final_link is LinkRate.LOW
    _assert_selected_wire_rate(serial, high_baud, cleanup=True)


@pytest.mark.parametrize("program_only", [False, True])
def test_full_and_program_write_verify_at_selected_rate(high_baud, tmp_path, program_only):
    session, serial, _journal = _authorization_full_session(tmp_path)
    session.verify_write = True
    target = bytearray(session.target_file_image)
    target[0x6000:0x6100] = b"\x55" * 0x100
    session.target_file_image = bytes(target)
    _change_preference_after_session_started(high_baud)

    result = session.execute_program_only() if program_only else session.execute()

    assert result.verified and result.verified_bytes > 0
    assert result.final_state is SessionState.POWER_CYCLE_REQUIRED
    _assert_selected_wire_rate(serial, high_baud, cleanup=False)


def test_eeprom_compare_write_and_readback_use_selected_rate(high_baud, tmp_path):
    session, serial, _journal = _authorization_session(tmp_path)
    before, target = b"\x01\x00\x02\x00", b"\x02\x00\x03\x00"
    serial.eeprom[0x1CA:0x1CE] = before
    _change_preference_after_session_started(high_baud)

    result = session.execute_eeprom_record(
        "MS41.2", before, target, expected_identity=IDENTITY
    )

    assert result.record == target and result.written
    assert result.final_link is LinkRate.LOW
    _assert_selected_wire_rate(serial, high_baud, cleanup=True)


def test_entry_qualification_uses_selected_rate_without_flashing(high_baud, tmp_path):
    session, serial, _journal = _qualification_session(tmp_path, expected_ecu_id="SIMULAT")
    _change_preference_after_session_started(high_baud)

    with pytest.raises(PartialWriteCancelled, match="before_tune_erase"):
        session.execute()

    assert not serial.flash_requests and session.safe_legacy_fallback
    _assert_selected_wire_rate(serial, high_baud, cleanup=True)


def test_partial_failure_retains_rate_without_unsafe_replay(high_baud, tmp_path):
    session, serial, _journal = _authorization_session(
        tmp_path, missing_flash_response_at=1
    )
    with pytest.raises(CommitUnknownError):
        session.execute()
    _change_preference_after_session_started(high_baud)

    assert session.transport.is_open and session.transport.baud == high_baud
    assert not session.can_recover_in_place and not session.safe_legacy_fallback
    before = list(serial.requests)
    with pytest.raises(PartialWriteStateError, match="no longer qualified"):
        session.recover_in_place()
    assert serial.requests == before
    _assert_selected_wire_rate(serial, high_baud, cleanup=False)


@pytest.mark.parametrize("program_only", [False, True])
def test_retained_recovery_preserves_original_rate(high_baud, tmp_path, program_only):
    session, serial, _journal = _authorization_full_session(
        tmp_path, missing_flash_response_at=3
    )
    operation = session.execute_program_only if program_only else session.execute
    with pytest.raises(CommitUnknownError):
        operation()
    assert session.can_recover_in_place and session.transport.is_open
    _change_preference_after_session_started(high_baud)
    serial.missing_flash_response_at = None
    session.journal = FullMemoryJournal(tmp_path / "compatibility-recovery.jsonl")
    request_count = len(serial.requests)

    result = session.recover_in_place()

    assert result.final_link is LinkRate.HIGH
    assert {baud for baud, _command, _args in serial.requests[request_count:]} == {high_baud}
    _assert_selected_wire_rate(serial, high_baud, cleanup=False)


def test_read_recovery_probes_the_sessions_selected_rate(high_baud):
    serial = ReadOnlyStockSerial(
        corrupt_selector=0x02 if high_baud == 125000 else 0x01
    )
    session = _read_session(serial)
    _change_preference_after_session_started(high_baud)

    with pytest.raises(FrameValidationError):
        session.read_partial()

    assert session.recovery_used and session.link is LinkRate.LOW
    _assert_selected_wire_rate(serial, high_baud, cleanup=True)


def test_selector_must_match_the_sessions_rate_profile(high_baud):
    session = _read_session(ReadOnlyStockSerial())
    with pytest.raises(FastReadStateError, match="disallowed escalation"):
        session._switch_up(
            selector=0x01 if high_baud == 125000 else 0x02,
            target=LinkRate.HIGH, target_state=SessionState.HIGH_READ,
        )


@pytest.mark.parametrize("state", [
    SessionState.LOW_READY, SessionState.AUTHORIZED_LOW,
    SessionState.HIGH_PARTIAL_WRITE, SessionState.COMPLETE,
])
def test_compatibility_selector_preserves_write_state_allowlist(state):
    with pytest.raises(UnsafePartialWriteCommand):
        NativeFastPartialWriteTransport._validate_control(0x90, b"\x02" + b"0" * 10, state)
