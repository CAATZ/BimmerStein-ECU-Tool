"""Production-policy tests for the slim stock-ECU native DS2 writers."""

from ds2_fast_contracts import LinkRate, SessionState
from ds2_fast_full_write import NativeFastFullWriteTransport
from ds2_fast_partial_write import NativeFastPartialWriteTransport, TOKEN_ADDRESS, TOKEN_LENGTH
from ds2_fast_plans import (
    PROGRAM_HIGH_START,
    TUNE_END,
    TUNE_START,
    file_image_to_ds2_layout,
)
from ds2_fast_slim_write import (
    SlimNativeFastFullWriteSession,
    SlimNativeFastPartialWriteSession,
)
from ms41 import MS41ECU
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


def _partial_session(tmp_path, *, verify_write, progress_cb=None):
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
        timing=ZERO_PARTIAL_TIMING,
        progress_cb=progress_cb,
        sleeper=lambda _seconds: None,
    )
    return session, serial, journal, target


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
