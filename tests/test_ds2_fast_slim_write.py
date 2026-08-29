"""Production-policy tests for the slim stock-ECU native DS2 writers."""

import ecu_info
import pytest

from ds2_fast_contracts import LinkRate, SessionState
from ds2_fast_full_write import FullWriteError, NativeFastFullWriteTransport
from ds2_fast_partial_write import (
    NativeFastPartialWriteTransport,
    PartialWriteCancelled,
    PartialWriteStateError,
    TOKEN_ADDRESS,
    TOKEN_LENGTH,
)
from ds2_fast_plans import (
    FULL_IMAGE_SIZE,
    PROGRAM_HIGH_START,
    TUNE_END,
    TUNE_START,
    file_image_to_ds2_layout,
)
from ds2_fast_slim_write import (
    SlimNativeFastFullWriteSession,
    SlimNativeFastPartialWriteSession,
)
from ms41 import (
    CODING_FAMILY_FILE_ADDR,
    MS41ECU,
    SS1V2_PROG_SIG_ADDR,
)
from tests.test_ds2_fast_full_write import (
    FullMemoryJournal,
    FullWriteStockSerial,
    ZERO_TIMING as ZERO_FULL_TIMING,
)
from tests.test_ds2_fast_partial_write import (
    MemoryJournal,
    PartialWriteStockSerial,
    ZERO_TIMING as ZERO_PARTIAL_TIMING,
    _image_fixture,
)


def _assert_no_hash_fields(journal):
    for _event, fields in journal.events:
        assert not any("sha" in str(name).lower() or "hash" in str(name).lower()
                       for name in fields)


def _read_requests_at(serial, address):
    return sum(
        1
        for _baud, command, args in serial.requests
        if command == 0x06 and int.from_bytes(args[:4], "big") == address
    )


def _partial_session(
    tmp_path,
    *,
    verify_write,
    progress_cb=None,
    expected_ecu_id=None,
    expected_program_compatibility_id=None,
    expected_coding_family=None,
    expected_coding_digit=None,
    expected_program_signature_hex=None,
    expected_driver_signature_hex=None,
    cancel_cb=None,
):
    source = _image_fixture()
    token = source.ds2_image[TOKEN_ADDRESS:TOKEN_ADDRESS + TOKEN_LENGTH]
    serial = PartialWriteStockSerial(source.ds2_image, token=token)
    journal = MemoryJournal(tmp_path / f"slim-partial-{verify_write}.jsonl")
    transport = NativeFastPartialWriteTransport(serial, event_cb=journal.event_callback)
    target = bytes(MS41ECU.tune_from_full(source.file_image))
    session = SlimNativeFastPartialWriteSession(
        transport,
        target,
        journal,
        verify_write=verify_write,
        expected_ecu_id=expected_ecu_id,
        expected_program_compatibility_id=expected_program_compatibility_id,
        expected_coding_family=expected_coding_family,
        expected_coding_digit=expected_coding_digit,
        expected_program_signature_hex=expected_program_signature_hex,
        expected_driver_signature_hex=expected_driver_signature_hex,
        cancel_cb=cancel_cb,
        timing=ZERO_PARTIAL_TIMING,
        progress_cb=progress_cb,
        sleeper=lambda _seconds: None,
    )
    return session, serial, journal, target


def _sparse_write_expectations(source):
    return {
        "expected_program_compatibility_id":
            MS41ECU.read_program_compatibility_id(source.file_image),
        "expected_coding_family": bytes(
            source.file_image[CODING_FAMILY_FILE_ADDR:CODING_FAMILY_FILE_ADDR + 3]
        ).decode("ascii"),
        "expected_program_signature_hex": bytes(
            source.file_image[SS1V2_PROG_SIG_ADDR:SS1V2_PROG_SIG_ADDR + 4]
        ).hex(),
        "expected_driver_signature_hex": bytes(
            source.file_image[
                ecu_info.DRV_SIG_FILE_OFFSET:
                ecu_info.DRV_SIG_FILE_OFFSET + ecu_info.DRV_SIG_LEN
            ]
        ).hex(),
    }


def _qualification_session(tmp_path, *, expected_ecu_id):
    token = bytes(range(TOKEN_LENGTH))
    image = bytearray(b"\xFF" * (256 * 1024))
    image[TOKEN_ADDRESS:TOKEN_ADDRESS + TOKEN_LENGTH] = token
    serial = PartialWriteStockSerial(bytes(image), token=token)
    journal = MemoryJournal(tmp_path / "write-entry-qualification.jsonl")
    transport = NativeFastPartialWriteTransport(
        serial,
        flash_enabled=False,
        event_cb=journal.event_callback,
    )
    session = SlimNativeFastPartialWriteSession(
        transport,
        b"\xFF" * (TUNE_END - TUNE_START),
        journal,
        verify_write=False,
        expected_ecu_id=expected_ecu_id,
        cancel_cb=lambda: True,
        timing=ZERO_PARTIAL_TIMING,
        sleeper=lambda _seconds: None,
    )
    return session, serial, journal


def _sparse_driver_session(tmp_path, *, modify_target=False):
    token = bytes(range(TOKEN_LENGTH))
    signature = bytes.fromhex("e00e0d58f04ec084")
    image = bytearray(b"\xA5" * FULL_IMAGE_SIZE)
    image[TOKEN_ADDRESS:TOKEN_ADDRESS + TOKEN_LENGTH] = token
    image[
        ecu_info.DRV_SIG_ADDR:ecu_info.DRV_SIG_ADDR + ecu_info.DRV_SIG_LEN
    ] = signature
    live_tune = bytes(image[TUNE_START:TUNE_END])
    target = bytearray(live_tune)
    if modify_target:
        target[100] ^= 1
    target = bytes(target)
    serial = PartialWriteStockSerial(bytes(image), token=token)
    journal = MemoryJournal(tmp_path / "sparse-live-checks.jsonl")
    session = SlimNativeFastPartialWriteSession(
        NativeFastPartialWriteTransport(serial, event_cb=journal.event_callback),
        target,
        journal,
        verify_write=False,
        expected_ecu_id="SIMULAT",
        expected_driver_signature_hex=signature.hex(),
        timing=ZERO_PARTIAL_TIMING,
        sleeper=lambda _seconds: None,
    )
    return session, serial, target, live_tune


def test_slim_write_entry_qualification_stops_before_every_flash_request(tmp_path):
    session, serial, journal = _qualification_session(
        tmp_path, expected_ecu_id="SIMULAT"
    )

    with pytest.raises(PartialWriteCancelled, match="before_tune_erase"):
        session.execute()

    assert serial.flash_requests == []
    assert serial.cleanup_completed
    assert session.destructive_started is False
    assert session.cleanup_attempted
    assert session.safe_legacy_fallback
    assert session.link is LinkRate.LOW
    assert session.state is SessionState.LOW_READY
    assert journal.outcome == "aborted"


def test_slim_expected_identity_mismatch_stops_before_authorization(tmp_path):
    session, serial, _journal = _qualification_session(
        tmp_path, expected_ecu_id="WRONGID"
    )

    with pytest.raises(PartialWriteStateError, match="identity mismatch"):
        session.execute()

    assert serial.authorized is False
    assert serial.armed is False
    assert serial.flash_requests == []


def test_slim_live_program_mismatch_stops_before_authorization(tmp_path):
    source = _image_fixture()
    evidence = _sparse_write_expectations(source)
    evidence["expected_program_compatibility_id"] = "9999"
    session, serial, _journal, _target = _partial_session(
        tmp_path,
        verify_write=False,
        expected_ecu_id="SIMULAT",
        **evidence,
    )

    with pytest.raises(PartialWriteStateError, match="program compatibility mismatch"):
        session.execute()

    assert serial.authorized is False
    assert serial.armed is False
    assert serial.flash_requests == []


def test_slim_tune_coding_digit_is_checked_without_inventing_a_family(tmp_path):
    source = _image_fixture()
    family = bytes(
        source.file_image[
            CODING_FAMILY_FILE_ADDR:CODING_FAMILY_FILE_ADDR + 3
        ]
    ).decode("ascii")
    wrong = "0" if family[-1] != "0" else "1"
    session, serial, _journal, _target = _partial_session(
        tmp_path,
        verify_write=False,
        expected_coding_digit=wrong,
    )

    with pytest.raises(PartialWriteStateError, match="coding-family digit"):
        session.execute()

    assert serial.authorized is False
    assert serial.flash_requests == []


def test_slim_modified_target_with_sparse_live_checks_proceeds(tmp_path):
    session, serial, target, live_tune = _sparse_driver_session(
        tmp_path, modify_target=True
    )

    result = session.execute()

    assert target != live_tune
    assert serial.authorized
    assert bytes(serial.memory[TUNE_START:TUNE_END]) == target
    assert result.program_payload_bytes > 0


def test_slim_live_tune_is_not_preread(tmp_path):
    session, serial, target, _live_tune = _sparse_driver_session(
        tmp_path, modify_target=True
    )
    serial.memory[TUNE_START + 100] ^= 1

    session.execute()

    assert serial.authorized
    assert bytes(serial.memory[TUNE_START:TUNE_END]) == target
    assert _read_requests_at(serial, TUNE_START) == 0


def test_slim_changed_driver_signature_stops_before_authorization_and_erase(tmp_path):
    session, serial, _target, _live_tune = _sparse_driver_session(tmp_path)
    serial.memory[ecu_info.DRV_SIG_ADDR] ^= 1

    with pytest.raises(PartialWriteStateError, match="driver signature does not match"):
        session.execute()

    assert serial.authorized is False
    assert serial.armed is False
    assert serial.flash_requests == []


def _full_session(tmp_path, *, verify_write, progress_cb=None):
    source = _image_fixture()
    token = source.ds2_image[TOKEN_ADDRESS:TOKEN_ADDRESS + TOKEN_LENGTH]
    serial = FullWriteStockSerial(source.ds2_image, token=token)
    journal = FullMemoryJournal(tmp_path / f"slim-full-{verify_write}.jsonl")
    transport = NativeFastFullWriteTransport(serial, event_cb=journal.event_callback)
    session = SlimNativeFastFullWriteSession(
        transport,
        source.file_image,
        journal,
        connected_family="intel",
        verify_write=verify_write,
        timing=ZERO_FULL_TIMING,
        progress_cb=progress_cb,
        sleeper=lambda _seconds: None,
    )
    return session, serial, journal


def test_slim_partial_without_verify_has_no_backup_readback_or_hashes(tmp_path):
    session, serial, journal, target = _partial_session(tmp_path, verify_write=False)

    result = session.execute()

    assert bytes(serial.memory[TUNE_START:TUNE_END]) == target
    assert result.verified is False
    assert result.verified_bytes == 0
    assert result.final_link is LinkRate.LOW
    assert result.final_state is SessionState.COMPLETE
    assert _read_requests_at(serial, TUNE_START) == 0
    _assert_no_hash_fields(journal)


def test_slim_partial_verify_reads_requested_tune_once(tmp_path):
    session, serial, _journal, _target = _partial_session(tmp_path, verify_write=True)

    result = session.execute()

    assert result.verified is True
    assert result.verified_bytes == 24 * 1024
    assert _read_requests_at(serial, TUNE_START) == 1


def test_slim_partial_progress_reports_cumulative_payload_bytes(tmp_path):
    progress = []
    session, _serial, _journal, _target = _partial_session(
        tmp_path,
        verify_write=False,
        progress_cb=lambda phase, done, total: progress.append(
            (phase, done, total)
        ),
    )

    result = session.execute()

    program = [
        event
        for event in progress
        if event[0] == "Writing calibration region"
    ]
    assert program
    assert {total for _phase, _done, total in program} == {
        result.program_payload_bytes
    }
    assert [done for _phase, done, _total in program] == sorted(
        done for _phase, done, _total in program
    )
    assert program[-1] == (
        "Writing calibration region",
        result.program_payload_bytes,
        result.program_payload_bytes,
    )

    status_labels = [
        phase.lower()
        for phase, done, total in progress
        if (done, total) == (0, 0)
    ]
    assert any("finaliz" in label for label in status_labels)
    assert any("9600" in label or "low" in label for label in status_labels)


def test_slim_partial_finalizer_failure_disables_destructive_replay(
    monkeypatch, tmp_path
):
    session, _serial, _journal, _target = _partial_session(
        tmp_path, verify_write=False
    )

    def fail_after_finalizer_entry():
        session._set_state(
            state=SessionState.WRITE_FINALIZE_HIGH,
            link=LinkRate.HIGH,
            reason="injected finalizer failure",
        )
        raise RuntimeError("injected finalizer failure")

    monkeypatch.setattr(session, "_finalize", fail_after_finalizer_entry)

    with pytest.raises(RuntimeError, match="injected finalizer failure"):
        session.execute()

    assert session.failure_state is SessionState.WRITE_FINALIZE_HIGH
    assert session.can_recover_in_place is False
    with pytest.raises(PartialWriteStateError, match="replay is no longer qualified"):
        session.recover_in_place()


def test_slim_full_without_verify_stays_high_and_does_not_read_back(tmp_path):
    session, serial, journal = _full_session(tmp_path, verify_write=False)

    result = session.execute()

    assert result.verified is False
    assert result.verified_bytes == 0
    assert result.final_link is LinkRate.HIGH
    assert result.final_state is SessionState.POWER_CYCLE_REQUIRED
    assert _read_requests_at(serial, 0x02000) == 0
    assert _read_requests_at(serial, 0x10000) == 0
    assert _read_requests_at(serial, 0x20000) == 0
    _assert_no_hash_fields(journal)


def test_slim_full_verify_reads_only_the_affected_ranges_once(tmp_path):
    session, serial, _journal = _full_session(tmp_path, verify_write=True)

    result = session.execute()

    assert result.verified is True
    assert result.verified_bytes == 0x6000 + 0x10000 + 0x20000
    assert _read_requests_at(serial, 0x02000) == 1
    assert _read_requests_at(serial, 0x10000) == 1
    assert _read_requests_at(serial, 0x20000) == 1


def test_slim_full_finalizer_failure_disables_destructive_replay(
    monkeypatch, tmp_path
):
    session, _serial, _journal = _full_session(tmp_path, verify_write=False)

    def fail_after_finalizer_entry():
        session._set_state(
            state=SessionState.WRITE_FINALIZE_HIGH,
            link=LinkRate.HIGH,
            reason="injected finalizer failure",
        )
        raise RuntimeError("injected finalizer failure")

    monkeypatch.setattr(session, "_finalize_full", fail_after_finalizer_entry)

    with pytest.raises(RuntimeError, match="injected finalizer failure"):
        session.execute()

    assert session.failure_state is SessionState.WRITE_FINALIZE_HIGH
    assert session.can_recover_in_place is False
    with pytest.raises(FullWriteError, match="replay is no longer qualified"):
        session.recover_in_place()


def test_slim_full_progress_is_one_monotonic_program_and_calibration_total(
    tmp_path,
):
    progress = []
    session, _serial, _journal = _full_session(
        tmp_path,
        verify_write=False,
        progress_cb=lambda phase, done, total: progress.append(
            (phase, done, total)
        ),
    )

    result = session.execute()

    byte_progress = [
        event
        for event in progress
        if event[2] > 0
        and event[1:] != (0, 1)
        and (
            "program" in event[0].lower()
            or "calibration" in event[0].lower()
            or "tune" in event[0].lower()
        )
    ]
    assert byte_progress
    assert any("program" in phase.lower() for phase, _done, _total in byte_progress)
    assert any(
        "calibration" in phase.lower() or "tune" in phase.lower()
        for phase, _done, _total in byte_progress
    )
    assert {total for _phase, _done, total in byte_progress} == {
        result.payload_bytes
    }
    assert [done for _phase, done, _total in byte_progress] == sorted(
        done for _phase, done, _total in byte_progress
    )
    assert byte_progress[-1][1:] == (result.payload_bytes, result.payload_bytes)

    status_labels = [
        phase.lower()
        for phase, done, total in progress
        if (done, total) == (0, 0)
    ]
    assert any("finaliz" in label for label in status_labels)


def test_slim_program_only_never_erases_or_programs_tune_and_cleans_to_low(tmp_path):
    source = _image_fixture()
    token = source.ds2_image[0x205E:0x205E + 10]
    target = bytearray(source.file_image)
    target[PROGRAM_HIGH_START] ^= 0x01
    target = bytes(target)
    serial = FullWriteStockSerial(source.ds2_image, token=token)
    journal = FullMemoryJournal(tmp_path / "slim-program-only.jsonl")
    transport = NativeFastFullWriteTransport(serial, event_cb=journal.event_callback)
    progress = []
    session = SlimNativeFastFullWriteSession(
        transport,
        target,
        journal,
        connected_family="intel",
        verify_write=False,
        timing=ZERO_FULL_TIMING,
        progress_cb=lambda phase, done, total: progress.append(
            (phase, done, total)
        ),
        sleeper=lambda _seconds: None,
    )

    result = session.execute_program_only()

    target_ds2 = file_image_to_ds2_layout(target)
    assert bytes(serial.memory[PROGRAM_HIGH_START:PROGRAM_HIGH_START + 1]) == target_ds2[
        PROGRAM_HIGH_START:PROGRAM_HIGH_START + 1
    ]
    assert bytes(serial.memory[TUNE_START:TUNE_END]) == source.ds2_image[TUNE_START:TUNE_END]
    assert result.tune_blocks == 0
    assert result.final_link is LinkRate.HIGH
    assert result.final_state is SessionState.POWER_CYCLE_REQUIRED
    assert result.power_cycle_required is True
    assert any(
        operation == 0x02 and address == PROGRAM_HIGH_START
        for operation, address, _data in serial.flash_requests
    )
    assert all(address != TUNE_START for _operation, address, _data in serial.flash_requests)
    selectors = [
        args[0]
        for _baud, command, args in serial.requests
        if command == 0x90 and len(args) == 11
    ]
    assert selectors == [0x01]
    status_updates = [event for event in progress if event[1:] == (0, 1)]
    assert status_updates == [
        ("Authorizing program write", 0, 1),
        ("Entering high-rate write mode", 0, 1),
        ("Checking high-rate write link", 0, 1),
        ("Preparing program erase", 0, 1),
        ("Erasing program region", 0, 1),
        ("Waiting for program erase to settle", 0, 1),
        ("Starting temporary hook flash", 0, 1),
    ]
