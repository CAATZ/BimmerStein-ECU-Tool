import pytest

from ds2_fast_contracts import (
    CommitUnknownError,
    ContractViolation,
    FastOperation,
    FlashOperation,
    LinkRate,
    SessionState,
)
from ds2_fast_full_write import (
    FullWriteFamilyError,
    FullWriteTiming,
    NativeFastFullWriteTransport,
)
from ds2_fast_plans import FULL_IMAGE_SIZE, TUNE_START
from ds2_fast_partial_write import (
    PartialWriteStateError,
    PartialWriteTimeout,
    SEED_KEY_COMMAND,
    TOKEN_ADDRESS,
    TOKEN_LENGTH,
)
from ds2_fast_slim_write import SlimNativeFastFullWriteSession
from ds2_write_authorization import (
    AUTHORIZATION_STATE_ADDRESS,
    FLASH_MODE_MARKER_ADDRESS,
    INITIAL_SEED_RETRY_DELAY,
    NATIVE_FAST_REENTRY_LATCH_ADDRESS,
    NATIVE_FAST_REENTRY_TIMER_ADDRESS,
    WRONG_KEY_COUNTER_ADDRESS,
)
from tests.test_ds2_fast_partial_write import (
    IDENTITY,
    MemoryJournal,
    PartialWriteStockSerial,
    _image_fixture,
)


ZERO_TIMING = FullWriteTiming(
    pre_arm_delay=0,
    post_selector_delay=0,
    poll_delay=0,
    post_program_erase_delay=0,
    between_program_requests=0,
    post_tune_erase_delay=0,
    post_tune_poll_delay=0,
)


def _assert_rom_matches_except_live_authorization_state(serial, source):
    actual = bytes(serial.memory)
    expected = source.ds2_image
    differing_offsets = {
        offset
        for offset, (actual_byte, expected_byte) in enumerate(zip(actual, expected))
        if actual_byte != expected_byte
    }
    assert differing_offsets == {
        AUTHORIZATION_STATE_ADDRESS,
        FLASH_MODE_MARKER_ADDRESS,
        WRONG_KEY_COUNTER_ADDRESS,
    }
    assert actual[AUTHORIZATION_STATE_ADDRESS] == 2
    assert actual[FLASH_MODE_MARKER_ADDRESS] == 0
    assert actual[WRONG_KEY_COUNTER_ADDRESS] == 0

class FullMemoryJournal(MemoryJournal):
    def __init__(self, path):
        super().__init__(path)
        self.operation = FastOperation.FULL_WRITE.value


class FullWriteStockSerial(PartialWriteStockSerial):
    full_status_payload = True

    def __init__(self, *args, flash_status_by_request=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.flash_status_by_request = dict(flash_status_by_request or {})

    def _handle_seed_key(self, args):
        if args == b"BMW\x0A":
            self.final_key_accepted = True
            self._response(0xA0, b"\x00")
            return
        super()._handle_seed_key(args)

    def _handle_flash(self, args):
        operation = args[0]
        address = int.from_bytes(args[1:4], "big")
        count = args[4]
        data = bytes(args[5:])
        assert count == len(data)
        self.flash_requests.append((operation, address, data))
        flash_index = len(self.flash_requests)

        if operation == FlashOperation.ERASE:
            if address == 0x002000:
                self.memory[0x002000:0x008000] = b"\xFF" * 0x6000
                self.memory[0x020000:0x040000] = b"\xFF" * 0x20000
            elif address == TUNE_START:
                self.memory[TUNE_START:0x020000] = b"\xFF" * 0x10000
            else:
                raise AssertionError(f"unexpected full erase 0x{address:06X}")
            cursor = address
        elif operation == FlashOperation.FULL_PROGRAM:
            self.memory[address:address + count] = data
            cursor = address + count
        elif operation == FlashOperation.POLL:
            cursor = address
        else:
            raise AssertionError(f"unexpected full operation 0x{operation:02X}")

        if flash_index == self.missing_flash_response_at:
            return
        status = self.flash_status_by_request.get((int(operation), address), 0x01)
        payload = (
            bytes((int(operation),))
            + int(cursor).to_bytes(3, "big")
            + bytes((count, status))
        )
        self._response(0xA0, payload)


def _session(
    tmp_path,
    *,
    connected_family="intel",
    serial_kwargs=None,
    verify_write=True,
    variant_conversion=False,
):
    source = _image_fixture()
    token = source.ds2_image[TOKEN_ADDRESS : TOKEN_ADDRESS + TOKEN_LENGTH]
    serial = FullWriteStockSerial(
        source.ds2_image,
        token=token,
        **(serial_kwargs or {}),
    )
    journal = FullMemoryJournal(tmp_path / "full.jsonl")
    transport = NativeFastFullWriteTransport(
        serial,
        event_cb=journal.event_callback,
    )
    session = SlimNativeFastFullWriteSession(
        transport,
        source.file_image,
        journal,
        connected_family=connected_family,
        verify_write=verify_write,
        variant_conversion=variant_conversion,
        timing=ZERO_TIMING,
        sleeper=lambda _seconds: None,
    )
    return session, serial, journal, source


def _pre_erase_cleanup_session(tmp_path, **serial_kwargs):
    token = bytes(range(TOKEN_LENGTH))
    image = bytearray(b"\xFF" * FULL_IMAGE_SIZE)
    image[TOKEN_ADDRESS:TOKEN_ADDRESS + TOKEN_LENGTH] = token
    serial = FullWriteStockSerial(bytes(image), token=token, **serial_kwargs)
    serial.selector = 0x01
    serial.ecu_baud = serial.ECU_RATES[0x01]
    serial.armed = True
    journal = FullMemoryJournal(tmp_path / "full-cleanup.jsonl")
    transport = NativeFastFullWriteTransport(
        serial,
        event_cb=journal.event_callback,
    )
    session = SlimNativeFastFullWriteSession(
        transport,
        bytes(image),
        journal,
        connected_family="intel",
        verify_write=False,
        timing=ZERO_TIMING,
        sleeper=lambda _seconds: None,
    )
    transport.set_baud(187500, reason="test enters proven high state")
    session.identity = IDENTITY
    session.token = token
    session.link = LinkRate.HIGH
    session.state = SessionState.HIGH_FULL_PROGRAM
    return session, serial, journal


def _authorization_full_session(tmp_path, **serial_kwargs):
    token = bytes(range(TOKEN_LENGTH))
    file_image = b"\xFF" * FULL_IMAGE_SIZE
    ds2_memory = bytearray(file_image)
    ds2_memory[TOKEN_ADDRESS:TOKEN_ADDRESS + TOKEN_LENGTH] = token
    serial = FullWriteStockSerial(bytes(ds2_memory), token=token, **serial_kwargs)
    journal = FullMemoryJournal(tmp_path / "full-authorization.jsonl")
    transport = NativeFastFullWriteTransport(
        serial,
        event_cb=journal.event_callback,
    )
    session = SlimNativeFastFullWriteSession(
        transport,
        file_image,
        journal,
        connected_family="intel",
        verify_write=False,
        timing=ZERO_TIMING,
        sleeper=lambda _seconds: None,
    )
    session._validate_family = lambda: "intel"
    return session, serial, journal


def test_full_pre_erase_cleanup_waits_through_silence_and_a2(tmp_path):
    session, serial, journal = _pre_erase_cleanup_session(
        tmp_path,
        post_cleanup_identity_timeouts=1,
        post_cleanup_identity_a2=1,
    )

    assert session._cleanup_pre_erase_to_low() is True

    assert session.safe_legacy_fallback
    assert session.state is SessionState.LOW_READY
    assert session.link is LinkRate.LOW
    assert sum(
        event == "full_pre_erase_low_readiness_timeout"
        for event, _fields in journal.events
    ) == 1
    readiness_statuses = [
        fields.get("status")
        for event, fields in journal.events
        if event == "request_completed"
        and fields.get("label") == "full_pre_erase_low_readiness"
    ]
    assert readiness_statuses == ["0xA2", "0xA0"]
    assert serial.flash_requests == []


def test_full_pre_erase_cleanup_timeout_never_enables_legacy_fallback(tmp_path):
    session, _serial, _journal = _pre_erase_cleanup_session(tmp_path)
    session.timing = FullWriteTiming(
        initial_seed_retry_delay=0,
        pre_arm_delay=0,
        post_selector_delay=0,
        poll_delay=0,
        post_program_erase_delay=0,
        between_program_requests=0,
        post_tune_erase_delay=0,
        post_tune_poll_delay=0,
        post_cleanup_readiness_timeout=0,
        post_cleanup_poll_delay=0,
    )

    with pytest.raises(PartialWriteTimeout, match="normal low DS2"):
        session._cleanup_pre_erase_to_low()

    assert not session.safe_legacy_fallback


@pytest.mark.parametrize("program_only", (False, True))
def test_full_and_program_only_pre_authorization_failure_confirm_low_without_0x90(
    tmp_path, program_only
):
    session, serial, _journal = _pre_erase_cleanup_session(tmp_path)
    session.program_only = program_only
    session.transport.set_baud(9600, reason="test remains in normal low state")
    session.link = LinkRate.LOW
    session.state = SessionState.TOKEN_KNOWN
    serial.ecu_baud = serial.ECU_RATES[0x26]
    serial.selector = 0x26
    serial.armed = False
    before = len(serial.requests)

    assert session._cleanup_pre_erase_to_low() is True

    assert session.safe_legacy_fallback
    assert session.state is SessionState.LOW_READY
    assert not any(
        command == SEED_KEY_COMMAND
        for _baud, command, _args in serial.requests[before:]
    )


@pytest.mark.parametrize("program_only", (False, True))
def test_full_authorization_failure_is_reported_as_power_cycle_required(
    tmp_path, program_only
):
    session, _serial, journal = _authorization_full_session(
        tmp_path,
        authorization_state=1,
    )
    operation = session.execute_program_only if program_only else session.execute

    with pytest.raises(PartialWriteStateError, match="E658=1"):
        operation()

    assert session.state is SessionState.POWER_CYCLE_REQUIRED
    assert journal.outcome == "failed"
    finish = next(fields for event, fields in journal.events if event == "journal_finished")
    assert finish["power_cycle_required"] is True
    assert finish["safe_legacy_fallback"] is False


def test_full_program_only_uses_single_native_challenge_and_never_reads_e659(
    tmp_path,
):
    session, serial, _journal = _authorization_full_session(tmp_path)
    session.timing = FullWriteTiming(
        initial_seed_retry_delay=0,
        pre_arm_delay=0,
        post_selector_delay=0,
        poll_delay=0,
        post_program_erase_delay=0,
        between_program_requests=0,
        post_tune_erase_delay=0,
        post_tune_poll_delay=0,
    )

    assert session._authorize_once() == "new_authorization"

    seed_key_payloads = [
        args
        for _baud, command, args in serial.requests
        if command == SEED_KEY_COMMAND
    ]
    assert seed_key_payloads[0] == b"BMW\x1e"
    assert seed_key_payloads.count(b"BMW\x1e") == 1
    assert len(seed_key_payloads) == 2
    read_addresses = [
        int.from_bytes(args[:4], "big")
        for _baud, command, args in serial.requests
        if command == 0x06
    ]
    assert 0xE659 not in read_addresses
    assert session.write_authorized


@pytest.mark.parametrize("program_only", (False, True))
def test_full_and_program_only_inherit_shared_pending_reentry_gate(
    tmp_path, program_only
):
    session, serial, _journal = _authorization_full_session(
        tmp_path,
        native_reentry_states=(
            {"e72e": 2, "e659": 0},
            {"e72e": 1, "e659": 0},
            {"e72e": 0, "e659": 0xCC},
        ),
    )
    session.program_only = program_only
    session.reentry_required = True
    session.timing = FullWriteTiming(
        initial_seed_retry_delay=0,
        native_fast_reentry_poll_interval=0,
        native_fast_reentry_timeout=15,
        pre_arm_delay=0,
        post_selector_delay=0,
        poll_delay=0,
        post_program_erase_delay=0,
        between_program_requests=0,
        post_tune_erase_delay=0,
        post_tune_poll_delay=0,
    )

    assert session._authorize_once() == "new_authorization"

    timer_reads = [
        index
        for index, (_baud, command, args) in enumerate(serial.requests)
        if command == 0x06
        and int.from_bytes(args[:4], "big")
        == NATIVE_FAST_REENTRY_TIMER_ADDRESS
    ]
    latch_reads = [
        index
        for index, (_baud, command, args) in enumerate(serial.requests)
        if command == 0x06
        and int.from_bytes(args[:4], "big")
        == NATIVE_FAST_REENTRY_LATCH_ADDRESS
    ]
    challenge_indices = [
        index
        for index, (_baud, command, args) in enumerate(serial.requests)
        if command == SEED_KEY_COMMAND and args == b"BMW\x1e"
    ]
    assert len(timer_reads) == 3
    assert len(latch_reads) == 3
    assert len(challenge_indices) == 1
    assert max(timer_reads + latch_reads) < challenge_indices[0]
    assert serial.flash_requests == []


@pytest.mark.parametrize("program_only", (False, True))
def test_full_and_program_only_inherit_state_qualified_10_second_retry(
    tmp_path, program_only
):
    session, serial, _journal = _authorization_full_session(
        tmp_path,
        initial_seed_busy=1,
        initial_seed_busy_state=0,
    )
    session.program_only = program_only
    sleeps = []
    session._sleep = lambda seconds: sleeps.append(seconds)

    assert session._authorize_once() == "new_authorization"

    assert [
        args
        for _baud, command, args in serial.requests
        if command == SEED_KEY_COMMAND and args == b"BMW\x1e"
    ] == [b"BMW\x1e", b"BMW\x1e"]
    assert sleeps == [10.0]
    assert INITIAL_SEED_RETRY_DELAY == 10.0
    assert serial.flash_requests == []
    assert session.write_authorized
    assert session.state is SessionState.AUTHORIZED_LOW


def test_full_write_direct_high_optional_verify_and_stays_high(tmp_path):
    session, serial, journal, source = _session(tmp_path)
    result = session.execute()

    assert result.final_link is LinkRate.HIGH
    assert result.final_state is SessionState.POWER_CYCLE_REQUIRED
    assert result.power_cycle_required
    assert not result.cleanup_attempted
    assert result.verified
    assert result.verified_bytes == 0x36000
    assert journal.outcome == "success"
    _assert_rom_matches_except_live_authorization_state(serial, source)
    selectors = [
        args[0]
        for _baud, command, args in serial.requests
        if command == SEED_KEY_COMMAND and len(args) == TOKEN_LENGTH + 1
    ]
    assert selectors == [0x01]


def test_confirmed_conversion_accepts_0e_only_at_the_two_pre_tune_polls(tmp_path):
    session, _serial, journal, _source = _session(
        tmp_path,
        serial_kwargs={
            "flash_status_by_request": {
                (int(FlashOperation.POLL), 0x000100): 0x0E,
            }
        },
        variant_conversion=True,
    )

    result = session.execute()

    assert result.final_state is SessionState.POWER_CYCLE_REQUIRED
    accepted = [
        fields
        for event, fields in journal.events
        if event == "conversion_midpoint_status_accepted"
    ]
    assert len(accepted) == 2
    assert {fields["scope"] for fields in accepted} == {"pre_tune_poll_only"}


def test_same_variant_write_keeps_pre_tune_0e_strict(tmp_path):
    session, _serial, _journal, _source = _session(
        tmp_path,
        serial_kwargs={
            "flash_status_by_request": {
                (int(FlashOperation.POLL), 0x000100): 0x0E,
            }
        },
    )

    with pytest.raises(ContractViolation, match="flash status 0x0E"):
        session.execute()


def test_conversion_does_not_accept_0e_after_the_tune_write(tmp_path):
    session, _serial, _journal, _source = _session(
        tmp_path,
        serial_kwargs={
            "flash_status_by_request": {
                (int(FlashOperation.POLL), TUNE_START): 0x0E,
            }
        },
        variant_conversion=True,
    )

    with pytest.raises(ContractViolation, match="flash status 0x0E"):
        session.execute()


def test_full_write_ambiguous_program_erase_retains_high_recovery_session(tmp_path):
    session, serial, journal, _source = _session(
        tmp_path,
        serial_kwargs={"missing_flash_response_at": 3},
    )
    with pytest.raises(CommitUnknownError):
        session.execute()

    assert session.destructive_started
    assert session.state is SessionState.COMMIT_UNKNOWN
    assert session.link is LinkRate.HIGH
    assert session.transport.is_open
    assert journal.outcome == "commit_unknown"
    selectors = [
        args[0]
        for _baud, command, args in serial.requests
        if command == SEED_KEY_COMMAND and len(args) == TOKEN_LENGTH + 1
    ]
    assert 0x26 not in selectors


def test_retained_full_session_can_reerase_restore_and_optionally_verify(tmp_path):
    session, serial, first_journal, source = _session(
        tmp_path,
        serial_kwargs={"missing_flash_response_at": 3},
    )
    with pytest.raises(CommitUnknownError):
        session.execute()
    assert session.transport.is_open
    assert first_journal.outcome == "commit_unknown"

    serial.missing_flash_response_at = None
    session.journal = FullMemoryJournal(tmp_path / "recovery.jsonl")
    result = session.recover_in_place()

    _assert_rom_matches_except_live_authorization_state(serial, source)
    assert result.final_link is LinkRate.HIGH
    assert result.verified_bytes == 0x36000
    assert session.journal.outcome == "success"


def test_full_write_family_mismatch_blocks_before_authorization_or_flash(tmp_path):
    session, serial, journal, _source = _session(
        tmp_path,
        connected_family="amd",
    )
    with pytest.raises(FullWriteFamilyError, match="do not match"):
        session.execute()
    assert serial.flash_requests == []
    assert not session.destructive_started
    assert journal.outcome == "failed"


def test_amd_family_uses_same_stock_wire_contract_when_family_evidence_matches(
    tmp_path,
    monkeypatch,
):
    session, serial, journal, _source = _session(
        tmp_path,
        connected_family="amd",
    )
    monkeypatch.setattr(
        "ds2_fast_slim_write.ecu_info.image_chip_family",
        lambda _image: "amd",
    )
    result = session.execute()
    assert result.chip_family == "amd"
    assert result.verified_bytes == 0x36000
    assert journal.outcome == "success"
    assert serial.flash_requests
