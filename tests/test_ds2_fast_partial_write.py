"""Offline transport and fault-injection tests for native-fast tune writes."""

from __future__ import annotations

import uuid
from collections import Counter
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from ds2_fast_contracts import (
    CommitUnknownError,
    ContractViolation,
    FastOperation,
    FlashOperation,
    FlashRequest,
    LinkRate,
    ResponseStatus,
    SessionState,
    StatusResponseContract,
    decode_ds2_frame,
    encode_ds2_frame,
)
from ds2_fast_partial_write import (
    FLASH_COMMAND,
    FINALIZE_CHALLENGE,
    MAX_FINALIZE_SEED_ATTEMPTS,
    NativeFastPartialWriteTransport,
    PartialWriteCancelled,
    PartialWriteReadbackMismatch,
    PartialWriteStateError,
    PartialWriteTimeout,
    PartialWriteTiming,
    SEED_KEY_COMMAND,
    TOKEN_ADDRESS,
    TOKEN_LENGTH,
    UnsafePartialWriteCommand,
    compute_ms41_write_key,
)
from ds2_fast_read import CAPTURED_RATE_PROFILE
from ds2_fast_plans import (
    FULL_IMAGE_SIZE,
    TUNE_END,
    TUNE_SECTOR_END,
    TUNE_START,
    file_image_to_ds2_layout,
)
from ds2_fast_safety import OperationJournal, inspect_operation_journal
from ds2_fast_slim_write import SlimNativeFastPartialWriteSession
from ds2_write_authorization import (
    AUTHORIZATION_STATE_ADDRESS,
    MAX_INITIAL_SEED_ATTEMPTS,
    WRONG_KEY_COUNTER_ADDRESS,
)
from ms41 import MS41ECU
from tests.conftest import ref


IDENTITY = b"SIMULATED-STOCK-MS41-WRITE-IDENTITY".ljust(42, b" ")[:42]
SEED = bytes.fromhex(
    "31 34 30 36 34 36 34 31 36 30 31 ff ff ff ff "
    "31 34 39 38 30 30 30 30 31 31 35 38 35 32 31 "
    "32 30 32 39 38 31 33 34 34 32 36 32"
)

class MemoryJournal:
    def __init__(self, path: Path):
        self.path = path
        self.operation = FastOperation.PARTIAL_WRITE.value
        self.operation_id = str(uuid.uuid4())
        self.events = []
        self.closed = False
        self.outcome = None

    def append(self, event, **fields):
        assert not self.closed
        self.events.append((event, fields))

    def event_callback(self, event, fields):
        self.append(event, **dict(fields))

    def finish(self, outcome, **fields):
        assert not self.closed
        self.outcome = outcome
        self.events.append(("journal_finished", {"outcome": outcome, **fields}))
        self.closed = True


class PartialWriteStockSerial:
    ECU_RATES = {0x26: 9615.4, 0x12: 19736.8, 0x01: 187500.0}

    def __init__(
        self,
        ds2_image: bytes,
        *,
        token: bytes,
        finalize_busy: int = 2,
        missing_flash_response_at: int | None = None,
        corrupt_flash_response_at: int | None = None,
        wrong_cursor_at: int | None = None,
        readback_corrupt_address: int | None = None,
        missing_initial_key_ack: bool = False,
        post_cleanup_identity_timeouts: int = 0,
        post_cleanup_identity_a2: int = 0,
        initial_seed_busy: int = 0,
        initial_seed_busy_state: int = 0,
        initial_seed_busy_payload: bytes = b"",
        initial_seed_busy_wrong_key_increment: bool = False,
        missing_existing_authorization_confirmation: bool = False,
        authorization_state: int = 0,
        wrong_key_count: int = 0,
    ):
        assert len(ds2_image) == FULL_IMAGE_SIZE
        assert len(token) == TOKEN_LENGTH
        self.memory = bytearray(ds2_image)
        self.memory[AUTHORIZATION_STATE_ADDRESS] = int(authorization_state)
        self.memory[WRONG_KEY_COUNTER_ADDRESS] = int(wrong_key_count)
        self.token = bytes(token)
        self.identity = IDENTITY
        self.finalize_busy = int(finalize_busy)
        self.finalize_attempts = 0
        self.missing_flash_response_at = missing_flash_response_at
        self.corrupt_flash_response_at = corrupt_flash_response_at
        self.wrong_cursor_at = wrong_cursor_at
        self.readback_corrupt_address = readback_corrupt_address
        self.missing_initial_key_ack = bool(missing_initial_key_ack)
        self.post_cleanup_identity_timeouts = int(post_cleanup_identity_timeouts)
        self.post_cleanup_identity_a2 = int(post_cleanup_identity_a2)
        self.initial_seed_busy = int(initial_seed_busy)
        self.initial_seed_busy_state = int(initial_seed_busy_state)
        self.initial_seed_busy_payload = bytes(initial_seed_busy_payload)
        self.initial_seed_busy_wrong_key_increment = bool(
            initial_seed_busy_wrong_key_increment
        )
        self.missing_existing_authorization_confirmation = bool(
            missing_existing_authorization_confirmation
        )
        self.cleanup_completed = False
        self._readback_corruption_used = False
        self._baud = CAPTURED_RATE_PROFILE.low
        self.ecu_baud = 9615.4
        self.selector = 0x26
        self.timeout = 1.5
        self._open = True
        self._pending = bytearray()
        self.requests = []
        self.flash_requests = []
        self.command_counts = Counter()
        self.transport_name = "d2xx"
        self.port = "COM1"
        self.index = 0
        self.prepared_high = False
        self.final_key_accepted = False
        self.authorized = False
        self.armed = False

    @property
    def baudrate(self):
        return self._baud

    @baudrate.setter
    def baudrate(self, value):
        self._baud = int(value)

    @property
    def is_open(self):
        return self._open

    def close(self):
        self._open = False

    def reset_input_buffer(self):
        self._pending.clear()

    def flush(self):
        pass

    def read(self, count):
        data = bytes(self._pending[:count])
        del self._pending[:count]
        return data

    def _rate_matches(self):
        return abs(self._baud - self.ecu_baud) / self.ecu_baud <= 0.04

    def _response(self, status, payload=b"", *, corrupt=False):
        raw = bytearray(encode_ds2_frame(status, payload))
        if corrupt:
            raw[-1] ^= 1
        self._pending.extend(raw)

    def write(self, raw):
        raw = bytes(raw)
        frame = decode_ds2_frame(raw)
        args = frame.payload
        self.requests.append((self._baud, frame.command, args))
        self.command_counts[frame.command] += 1
        self._pending.extend(raw)  # exact K-Line echo
        if not self._rate_matches():
            return len(raw)

        if frame.command == 0x00:
            if self.cleanup_completed and self.post_cleanup_identity_timeouts > 0:
                self.post_cleanup_identity_timeouts -= 1
                return len(raw)
            if self.cleanup_completed and self.post_cleanup_identity_a2 > 0:
                self.post_cleanup_identity_a2 -= 1
                self._response(ResponseStatus.READINESS_A2)
                return len(raw)
            self._response(ResponseStatus.ACK, self.identity)
        elif frame.command == 0x06:
            address = int.from_bytes(args[:4], "big")
            count = args[4]
            data = bytearray(self.memory[address : address + count])
            if (
                self.readback_corrupt_address is not None
                and self.selector == 0x01
                and address <= self.readback_corrupt_address < address + count
                and not self._readback_corruption_used
            ):
                data[self.readback_corrupt_address - address] ^= 1
                self._readback_corruption_used = True
            self._response(ResponseStatus.ACK, data)
        elif frame.command == 0xA2:
            self.prepared_high = self.selector == 0x01
            self._response(ResponseStatus.READY_FF)
        elif frame.command == 0x0D:
            if getattr(self, "full_status_payload", False):
                self._response(ResponseStatus.ACK, b"S" * 69)
            elif self.prepared_high and not self.final_key_accepted:
                self._response(ResponseStatus.READY_FF)
            else:
                self._response(ResponseStatus.ACK, b"S" * 69)
        elif frame.command == SEED_KEY_COMMAND:
            self._handle_seed_key(args)
        elif frame.command == FLASH_COMMAND:
            self._handle_flash(args)
        else:
            raise AssertionError(f"unexpected command 0x{frame.command:02X}")
        return len(raw)

    def _handle_seed_key(self, args):
        if len(args) == TOKEN_LENGTH + 1 and args[1:] == self.token:
            selector = args[0]
            assert selector in self.ECU_RATES
            self._response(ResponseStatus.ACK)
            self.selector = selector
            self.ecu_baud = self.ECU_RATES[selector]
            return
        if args == b"BMW":
            if self.armed and self.selector == 0x26:
                self.armed = False
                self.cleanup_completed = True
                self._response(ResponseStatus.CONTEXT_B0)
                return
            self.armed = True
            self._response(ResponseStatus.ACK, b"\x00")
            return
        if args == b"BMW\x1e":
            state = self.memory[AUTHORIZATION_STATE_ADDRESS]
            if state == 2:
                if self.missing_existing_authorization_confirmation:
                    return
                self._response(ResponseStatus.ACK, b"\x00")
                return
            if state == 1:
                self.memory[AUTHORIZATION_STATE_ADDRESS] = 0
                self.memory[WRONG_KEY_COUNTER_ADDRESS] += 1
                self._response(ResponseStatus.CONTEXT_A1)
                return
            if self.initial_seed_busy > 0:
                self.initial_seed_busy -= 1
                self.memory[AUTHORIZATION_STATE_ADDRESS] = self.initial_seed_busy_state
                if self.initial_seed_busy_wrong_key_increment:
                    self.memory[WRONG_KEY_COUNTER_ADDRESS] += 1
                self._response(ResponseStatus.CONTEXT_A1, self.initial_seed_busy_payload)
                return
            self.memory[AUTHORIZATION_STATE_ADDRESS] = 1
            self._response(ResponseStatus.ACK, SEED)
            return
        if args == b"BMW" + bytes((FINALIZE_CHALLENGE,)):
            self.finalize_attempts += 1
            if self.finalize_attempts <= self.finalize_busy:
                self._response(ResponseStatus.CONTEXT_A1)
            else:
                self._response(ResponseStatus.ACK, SEED)
            return
        if args == compute_ms41_write_key(0x1E, SEED):
            self.authorized = True
            self.memory[AUTHORIZATION_STATE_ADDRESS] = 2
            if self.missing_initial_key_ack:
                return
            self._response(ResponseStatus.ACK, b"\x00")
            return
        if args == compute_ms41_write_key(FINALIZE_CHALLENGE, SEED):
            self.final_key_accepted = True
            self._response(ResponseStatus.ACK, b"\x00")
            return
        raise AssertionError(f"unexpected seed/key arguments {args.hex(' ')}")

    def _handle_flash(self, args):
        operation = args[0]
        address = int.from_bytes(args[1:4], "big")
        count = args[4]
        data = bytes(args[5:])
        assert count == len(data)
        self.flash_requests.append((operation, address, data))
        flash_index = len(self.flash_requests)

        if operation == FlashOperation.ERASE:
            assert address == TUNE_START and not data
            self.memory[TUNE_START:TUNE_SECTOR_END] = b"\xFF" * (
                TUNE_SECTOR_END - TUNE_START
            )
            response_operation = FlashOperation.PARTIAL_PROGRAM
            cursor = address
        elif operation == FlashOperation.PARTIAL_PROGRAM:
            self.memory[address : address + count] = data
            response_operation = FlashOperation.PARTIAL_PROGRAM
            cursor = address + count
        elif operation == FlashOperation.POLL:
            response_operation = FlashOperation.POLL
            cursor = address
        else:
            raise AssertionError(f"forbidden flash operation 0x{operation:02X}")

        if flash_index == self.missing_flash_response_at:
            return
        if flash_index == self.wrong_cursor_at:
            cursor += 1
        payload = (
            bytes((int(response_operation),))
            + int(cursor).to_bytes(3, "big")
            + bytes((count, 1))
        )
        self._response(
            ResponseStatus.ACK,
            payload,
            corrupt=flash_index == self.corrupt_flash_response_at,
        )

@dataclass(frozen=True)
class ImageFixture:
    file_image: bytes
    ds2_image: bytes


def _image_fixture(*, file_image=None):
    image = bytes(file_image if file_image is not None else ref("MS41.1"))
    return ImageFixture(image, file_image_to_ds2_layout(image))


ZERO_TIMING = PartialWriteTiming(
    post_authorization_delay=0,
    between_low_preamble_reads=0,
    pre_arm_delay=0,
    post_arm_delay=0,
    post_high_selector_delay=0,
    post_erase_delay=0,
    between_program_requests=0,
    pre_finalize_delay=0,
    finalize_seed_poll_delay=0,
)


def _session(
    tmp_path,
    *,
    serial_kwargs=None,
    journal=None,
    cancel_cb=None,
    transport_events=True,
    target_tune=None,
    verify_write=False,
):
    source = _image_fixture()
    token = source.ds2_image[TOKEN_ADDRESS : TOKEN_ADDRESS + TOKEN_LENGTH]
    serial = PartialWriteStockSerial(
        source.ds2_image,
        token=token,
        **(serial_kwargs or {}),
    )
    journal = journal or MemoryJournal(tmp_path / "operation.jsonl")
    transport = NativeFastPartialWriteTransport(
        serial,
        event_cb=journal.event_callback if transport_events else None,
    )
    target = bytes(
        target_tune
        if target_tune is not None
        else MS41ECU.tune_from_full(source.file_image)
    )
    session = SlimNativeFastPartialWriteSession(
        transport,
        target,
        journal,
        verify_write=verify_write,
        timing=ZERO_TIMING,
        sleeper=lambda _seconds: None,
    )
    session.cancel_cb = cancel_cb
    return session, serial, journal, source


def _authorization_session(tmp_path, **serial_kwargs):
    token = bytes(range(TOKEN_LENGTH))
    image = bytearray(b"\xFF" * FULL_IMAGE_SIZE)
    image[TOKEN_ADDRESS:TOKEN_ADDRESS + TOKEN_LENGTH] = token
    serial = PartialWriteStockSerial(bytes(image), token=token, **serial_kwargs)
    journal = MemoryJournal(tmp_path / "authorization.jsonl")
    transport = NativeFastPartialWriteTransport(
        serial,
        event_cb=journal.event_callback,
    )
    session = SlimNativeFastPartialWriteSession(
        transport,
        b"\xFF" * (TUNE_END - TUNE_START),
        journal,
        verify_write=False,
        timing=ZERO_TIMING,
        sleeper=lambda _seconds: None,
    )
    session.identity = IDENTITY
    session.token = token
    session.state = SessionState.TOKEN_KNOWN
    return session, serial, journal


def test_seed_key_matches_both_live_initial_and_finalize_keys():
    assert compute_ms41_write_key(0x1E, SEED) == bytes.fromhex("9a98a09c")
    assert compute_ms41_write_key(0x1F, SEED) == bytes.fromhex("9797a19a")


def test_initial_a1_retries_only_after_ram_confirms_clean_state(tmp_path):
    session, serial, _journal = _authorization_session(
        tmp_path,
        initial_seed_busy=2,
        initial_seed_busy_state=0,
    )

    assert session._authorize_once() == "new_authorization"

    challenges = [
        args
        for _baud, command, args in serial.requests
        if command == SEED_KEY_COMMAND and args == b"BMW\x1e"
    ]
    keys = [
        args
        for _baud, command, args in serial.requests
        if command == SEED_KEY_COMMAND
        and args == compute_ms41_write_key(0x1E, SEED)
    ]
    assert len(challenges) == 3
    assert len(keys) == 1
    assert serial.memory[AUTHORIZATION_STATE_ADDRESS] == 2
    assert serial.memory[WRONG_KEY_COUNTER_ADDRESS] == 0


def test_initial_a1_that_enters_key_state_never_sends_another_0x90(tmp_path):
    session, serial, _journal = _authorization_session(
        tmp_path,
        initial_seed_busy=1,
        initial_seed_busy_state=1,
    )

    with pytest.raises(PartialWriteStateError, match="E658=1"):
        session._authorize_once()

    assert [
        args for _baud, command, args in serial.requests if command == SEED_KEY_COMMAND
    ] == [b"BMW\x1e"]
    assert session.authorization_state_requires_cycle
    assert session._recover_pre_erase_to_low() is False
    assert not session.safe_legacy_fallback


def test_pending_authorization_failure_is_reported_as_power_cycle_required(tmp_path):
    session, _serial, journal = _authorization_session(
        tmp_path,
        authorization_state=1,
    )

    with pytest.raises(PartialWriteStateError, match="E658=1"):
        session.execute()

    assert session.state is SessionState.POWER_CYCLE_REQUIRED
    assert journal.outcome == "failed"
    finish = next(fields for event, fields in journal.events if event == "journal_finished")
    assert finish["power_cycle_required"] is True
    assert finish["safe_legacy_fallback"] is False


def test_malformed_initial_a1_never_allows_cleanup_or_another_0x90(tmp_path):
    session, serial, _journal = _authorization_session(
        tmp_path,
        initial_seed_busy=1,
        initial_seed_busy_payload=b"\x00",
    )

    with pytest.raises(ContractViolation, match="unexpectedly carried a payload"):
        session._authorize_once()

    assert [
        args for _baud, command, args in serial.requests if command == SEED_KEY_COMMAND
    ] == [b"BMW\x1e"]
    assert session.authorization_state_requires_cycle
    assert session.authorization_may_be_active
    assert session._recover_pre_erase_to_low() is False
    assert not session.safe_legacy_fallback


def test_initial_a1_with_counter_change_never_sends_another_0x90(tmp_path):
    session, serial, _journal = _authorization_session(
        tmp_path,
        initial_seed_busy=1,
        initial_seed_busy_state=0,
        initial_seed_busy_wrong_key_increment=True,
    )

    with pytest.raises(PartialWriteStateError, match="E658=0, E74B=1"):
        session._authorize_once()

    assert [
        args for _baud, command, args in serial.requests if command == SEED_KEY_COMMAND
    ] == [b"BMW\x1e"]
    assert session.authorization_state_requires_cycle
    assert session._recover_pre_erase_to_low() is False
    assert not session.safe_legacy_fallback


def test_exhausted_clean_a1_state_confirms_low_fallback_without_0x90_cleanup(
    tmp_path,
):
    session, serial, _journal = _authorization_session(
        tmp_path,
        initial_seed_busy=MAX_INITIAL_SEED_ATTEMPTS,
        initial_seed_busy_state=0,
    )

    with pytest.raises(PartialWriteTimeout, match="initial write seed unavailable"):
        session._authorize_once()

    requests_before_recovery = list(serial.requests)
    assert session._recover_pre_erase_to_low() is True
    assert session.safe_legacy_fallback
    assert session.state is SessionState.LOW_READY
    assert [
        args for _baud, command, args in serial.requests if command == SEED_KEY_COMMAND
    ] == [b"BMW\x1e"] * MAX_INITIAL_SEED_ATTEMPTS
    assert not any(
        command == SEED_KEY_COMMAND
        for _baud, command, _args in serial.requests[len(requests_before_recovery):]
    )


@pytest.mark.parametrize(
    ("serial_kwargs", "message"),
    (
        ({"authorization_state": 1}, "E658=1"),
        ({"wrong_key_count": 2}, "E74B >= 2"),
    ),
)
def test_unsafe_authorization_state_blocks_before_any_0x90(
    tmp_path, serial_kwargs, message
):
    session, serial, _journal = _authorization_session(tmp_path, **serial_kwargs)

    with pytest.raises(PartialWriteStateError, match=message):
        session._authorize_once()

    assert not any(command == SEED_KEY_COMMAND for _baud, command, _args in serial.requests)
    assert serial.flash_requests == []
    assert session.authorization_state_requires_cycle


def test_existing_authorization_is_confirmed_once_without_sending_a_key(tmp_path):
    session, serial, _journal = _authorization_session(
        tmp_path,
        authorization_state=2,
    )

    assert session._authorize_once() == "already_authorized"

    requests_90 = [
        args for _baud, command, args in serial.requests if command == SEED_KEY_COMMAND
    ]
    assert requests_90 == [b"BMW\x1e"]
    assert session.write_authorized


def test_ambiguous_existing_authorization_confirmation_blocks_all_fallback(tmp_path):
    session, serial, _journal = _authorization_session(
        tmp_path,
        authorization_state=2,
        missing_existing_authorization_confirmation=True,
    )

    with pytest.raises(PartialWriteTimeout):
        session._authorize_once()

    assert [
        args for _baud, command, args in serial.requests if command == SEED_KEY_COMMAND
    ] == [b"BMW\x1e"]
    assert session.authorization_state_requires_cycle
    assert session.authorization_may_be_active
    assert session._recover_pre_erase_to_low() is False
    assert not session.safe_legacy_fallback


def test_transport_requires_d2xx_echo_and_declared_low_rate():
    source = _image_fixture()
    token = source.ds2_image[TOKEN_ADDRESS : TOKEN_ADDRESS + TOKEN_LENGTH]
    serial = PartialWriteStockSerial(source.ds2_image, token=token)
    serial.transport_name = "pyserial"
    with pytest.raises(UnsafePartialWriteCommand, match="D2XX"):
        NativeFastPartialWriteTransport(serial)
    serial.transport_name = "d2xx"
    with pytest.raises(UnsafePartialWriteCommand, match="echo"):
        NativeFastPartialWriteTransport(serial, echo=False)
    serial._baud = 19200
    with pytest.raises(UnsafePartialWriteCommand, match="low baud"):
        NativeFastPartialWriteTransport(serial)


def test_writer_has_d2xx_opener_and_is_reached_only_through_reviewed_service():
    assert hasattr(NativeFastPartialWriteTransport, "open_d2xx")
    root = Path(__file__).resolve().parents[1]
    service = (root / "ds2_native_fast_service.py").read_text(encoding="utf-8")
    gui = (root / "gui.py").read_text(encoding="utf-8")
    assert "NativeFastPartialWriteTransport" in service
    assert "ds2_native_fast_service" in gui
    assert "ds2_fast_partial_write" not in gui


def test_transport_structurally_rejects_full_write_and_unproven_commands(tmp_path):
    session, serial, _journal, _source = _session(tmp_path)
    transport = session.transport
    before = len(serial.requests)
    contract = StatusResponseContract("unused", frozenset((ResponseStatus.ACK,)))
    for command, args in (
        (FLASH_COMMAND, b""),
        (SEED_KEY_COMMAND, b"\x26" + serial.token),
        (SEED_KEY_COMMAND, b"BMW\xFF"),
    ):
        with pytest.raises(UnsafePartialWriteCommand):
            transport.request(
                command,
                args,
                contract=contract,
                label="forbidden",
                rate=LinkRate.LOW,
                state=SessionState.LOW_READY,
            )
    for request in (
        FlashRequest(FlashOperation.FULL_PROGRAM, 0x2000, b"x"),
        FlashRequest(FlashOperation.ERASE, 0x2000),
        FlashRequest(FlashOperation.POLL, 0x10000),
    ):
        with pytest.raises((UnsafePartialWriteCommand, ValueError)):
            transport.flash(
                request,
                label="forbidden_flash",
                rate=LinkRate.HIGH,
                state=SessionState.HIGH_PARTIAL_WRITE,
            )
    assert len(serial.requests) == before


def test_successful_partial_write_uses_capture_order_and_cleans_low(tmp_path):
    session, serial, journal, source = _session(tmp_path)
    result = session.execute()

    expected_sector = MS41ECU.tune_from_full(source.file_image) + b"\xFF" * 0xA000
    assert bytes(serial.memory[TUNE_START:TUNE_SECTOR_END]) == expected_sector
    assert result.final_link is LinkRate.LOW
    assert result.final_state is SessionState.COMPLETE
    assert result.power_cycle_recommended
    assert result.verified is False
    assert result.cleanup_attempted
    assert journal.outcome == "success"
    assert result.finalize_seed_attempts == 3
    operations = [operation for operation, _address, _data in serial.flash_requests]
    assert operations[0] == FlashOperation.ERASE
    assert operations[-1] == FlashOperation.POLL
    assert set(operations[1:-1]) == {FlashOperation.PARTIAL_PROGRAM}
    assert FlashOperation.FULL_PROGRAM not in operations
    assert [args[0] for _baud, command, args in serial.requests
            if command == SEED_KEY_COMMAND and len(args) == TOKEN_LENGTH + 1] == [0x01, 0x26]
    journal_text = repr(journal.events).lower()
    assert SEED.hex() not in journal_text
    assert "9a98a09c" not in journal_text

    prepare_index = next(
        index
        for index, (_baud, command, _args) in enumerate(serial.requests)
        if command == 0xA2
    )
    preflash = serial.requests[prepare_index:]
    expected_prefix = [
        (0xA2, b""),
        (0x06, (0x2001).to_bytes(4, "big") + b"\x0c"),
        (0x0D, b""),
        (0x90, b"BMW\x1e"),
        (0x90, bytes.fromhex("9a98a09c")),
        (0x06, (0x1CF4).to_bytes(4, "big") + b"\x03"),
        (0x06, (0x1000E).to_bytes(4, "big") + b"\x02"),
        (0x90, b"BMW"),
        (0x90, b"\x01" + serial.token),
        (0x06, TOKEN_ADDRESS.to_bytes(4, "big") + bytes((TOKEN_LENGTH,))),
    ]
    assert [(command, args) for _baud, command, args in preflash[:10]] == expected_prefix
    assert [
        (command, args)
        for _baud, command, args in preflash[10:12]
    ] == [
        (0x06, TOKEN_ADDRESS.to_bytes(4, "big") + bytes((TOKEN_LENGTH,))),
        (0x06, TOKEN_ADDRESS.to_bytes(4, "big") + bytes((TOKEN_LENGTH,))),
    ]
    assert preflash[12][1] == FLASH_COMMAND
    assert preflash[12][2][0] == FlashOperation.ERASE


def test_cleanup_treats_initial_silence_as_bounded_readiness_transition(tmp_path):
    session, _serial, journal, _source = _session(
        tmp_path,
        serial_kwargs={"post_cleanup_identity_timeouts": 2},
    )
    result = session.execute()
    assert result.final_link is LinkRate.LOW
    assert result.final_state is SessionState.COMPLETE
    assert journal.outcome == "success"
    assert sum(
        event == "post_partial_write_low_readiness_timeout"
        for event, _fields in journal.events
    ) == 2


def test_completed_write_uses_power_cycle_exit_when_low_identity_never_arrives(tmp_path):
    session, _serial, journal, _source = _session(tmp_path)
    session.timing = replace(
        ZERO_TIMING,
        post_cleanup_readiness_timeout=0,
        post_cleanup_poll_delay=0,
    )
    result = session.execute()
    assert result.final_link is LinkRate.LOW
    assert result.final_state is SessionState.POWER_CYCLE_REQUIRED
    assert journal.outcome == "power_cycle_required"


def test_target_tune_is_written_without_backup_comparison(tmp_path):
    target = bytearray(MS41ECU.tune_from_full(ref("MS41.1")))
    target[0x500] ^= 1
    session, serial, journal, _source = _session(
        tmp_path,
        target_tune=bytes(target),
    )
    result = session.execute()
    assert journal.outcome == "success"
    assert result.verified is False
    assert bytes(serial.memory[TUNE_START:TUNE_END]) == bytes(target)


def test_ambiguous_initial_key_ack_sends_no_flash_or_legacy_fallback(tmp_path):
    session, serial, journal, _source = _session(
        tmp_path,
        serial_kwargs={"missing_initial_key_ack": True},
    )
    with pytest.raises(Exception, match="response header"):
        session.execute()
    assert serial.authorized
    assert serial.flash_requests == []
    assert session.authorization_may_be_active
    assert session.state is SessionState.POWER_CYCLE_REQUIRED
    assert session.safe_legacy_fallback is False
    assert journal.outcome == "failed"
    finish = next(fields for event, fields in journal.events if event == "journal_finished")
    assert finish["power_cycle_required"] is True


@pytest.mark.parametrize("fault", ("missing", "corrupt"))
def test_ambiguous_erase_ack_is_commit_unknown_and_never_retried(tmp_path, fault):
    kwargs = {
        "missing_flash_response_at": 1 if fault == "missing" else None,
        "corrupt_flash_response_at": 1 if fault == "corrupt" else None,
    }
    session, serial, journal, _source = _session(tmp_path, serial_kwargs=kwargs)
    with pytest.raises(CommitUnknownError):
        session.execute()
    assert len(serial.flash_requests) == 1
    assert journal.outcome == "commit_unknown"


def test_retained_partial_session_can_reerase_restore_and_cleanup(tmp_path):
    session, serial, first_journal, source = _session(
        tmp_path,
        serial_kwargs={"missing_flash_response_at": 1},
    )
    with pytest.raises(CommitUnknownError):
        session.execute()
    assert session.transport.is_open
    assert first_journal.outcome == "commit_unknown"

    serial.missing_flash_response_at = None
    session.journal = MemoryJournal(tmp_path / "recovery.jsonl")
    result = session.recover_in_place()

    expected_sector = MS41ECU.tune_from_full(source.file_image) + b"\xFF" * 0xA000
    assert bytes(serial.memory[TUNE_START:TUNE_SECTOR_END]) == expected_sector
    assert result.final_link is LinkRate.LOW
    assert result.cleanup_attempted
    assert session.journal.outcome == "success"
    assert session.state is SessionState.COMPLETE


def test_ambiguous_program_ack_stops_without_resending_or_advancing(tmp_path):
    session, serial, journal, _source = _session(
        tmp_path,
        serial_kwargs={"missing_flash_response_at": 3},
    )
    with pytest.raises(CommitUnknownError):
        session.execute()
    assert len(serial.flash_requests) == 3
    assert serial.flash_requests[1] != serial.flash_requests[2]
    assert session.state is SessionState.COMMIT_UNKNOWN
    assert journal.outcome == "commit_unknown"


def test_wrong_program_cursor_is_contract_failure_without_retry(tmp_path):
    session, serial, journal, _source = _session(
        tmp_path,
        serial_kwargs={"wrong_cursor_at": 3},
    )
    with pytest.raises(ContractViolation, match="address/cursor"):
        session.execute()
    assert len(serial.flash_requests) == 3
    assert session.state is SessionState.POWER_CYCLE_REQUIRED
    assert journal.outcome == "failed"


def test_optional_readback_mismatch_does_not_attempt_cleanup(tmp_path):
    session, serial, journal, _source = _session(
        tmp_path,
        serial_kwargs={"readback_corrupt_address": TUNE_START + 0x4321},
        verify_write=True,
    )
    with pytest.raises(PartialWriteReadbackMismatch, match="0x14321"):
        session.execute()
    assert session.state is SessionState.POWER_CYCLE_REQUIRED
    assert not session.cleanup_attempted
    assert journal.outcome == "failed"
    selectors = [
        args[0]
        for _baud, command, args in serial.requests
        if command == SEED_KEY_COMMAND and len(args) == TOKEN_LENGTH + 1
    ]
    assert 0x26 not in selectors


def test_finalize_seed_poll_is_bounded_and_key_is_not_guessed(tmp_path):
    session, serial, journal, _source = _session(
        tmp_path,
        serial_kwargs={"finalize_busy": MAX_FINALIZE_SEED_ATTEMPTS},
    )
    with pytest.raises(Exception, match="finalize seed unavailable"):
        session.execute()
    assert serial.finalize_attempts == MAX_FINALIZE_SEED_ATTEMPTS
    assert not serial.final_key_accepted
    assert session.state is SessionState.POWER_CYCLE_REQUIRED
    assert journal.outcome == "failed"


def test_cancellation_before_erase_recovers_low_and_allows_fallback(tmp_path):
    session, serial, journal, _source = _session(
        tmp_path,
        cancel_cb=lambda: True,
    )
    with pytest.raises(PartialWriteCancelled, match="before_tune_erase"):
        session.execute()
    assert serial.authorized
    assert serial.flash_requests == []
    assert session.state is SessionState.LOW_READY
    assert session.safe_legacy_fallback
    assert journal.outcome == "aborted"


def test_durable_journal_closes_successfully_and_contains_no_seed_or_key(tmp_path):
    journal = OperationJournal(
        tmp_path / "durable.jsonl",
        operation=FastOperation.PARTIAL_WRITE,
        metadata={"port": "COM1"},
    )
    session, _serial, _journal, _source = _session(
        tmp_path / "case",
        journal=journal,
        transport_events=False,
    )
    result = session.execute()
    inspection = inspect_operation_journal(journal.path)
    assert inspection.complete
    assert inspection.outcome == "success"
    text = journal.path.read_text(encoding="utf-8").lower()
    assert SEED.hex() not in text
    assert "9a98a09c" not in text
    assert result.journal_path == journal.path
