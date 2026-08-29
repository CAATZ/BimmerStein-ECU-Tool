"""Validated transport and session primitives for native-fast tune writes.

The transport allowlist is partial-only: command 0x07 can erase only the tune
sector, program only 0x10000..0x15FFF with operation 0x00, and poll only the
    validated final address 0x1D07. Operation 0x02 and the program-sector erase
address are structurally unreachable.
"""

from __future__ import annotations

import time
from collections import Counter
from dataclasses import dataclass
from typing import Callable, Mapping, Optional, Tuple, Union

from ds2_fast_contracts import (
    CommitUnknownError,
    ContractViolation,
    DS2Response,
    FastOperation,
    FlashOperation,
    FlashReply,
    FlashRequest,
    FrameValidationError,
    LinkRate,
    ResponseStatus,
    SessionState,
    StatusResponseContract,
    contextual_recovery_contract,
    decode_ds2_response,
    encode_ds2_frame,
    read_response_contract,
    selector_ack_contract,
    validate_flash_exchange,
)
from ds2_fast_plans import (
    FINAL_POLL_ADDRESS,
    MAX_READ_DATA,
    ROM_BLOCK_SIZE,
    TUNE_END,
    TUNE_START,
)
from ds2_fast_read import (
    BITS_PER_CHARACTER_8E2,
    IDENTITY_LENGTH,
    PRODUCTION_RATE_PROFILE,
)
from ds2_write_authorization import (
    AUTHORIZATION_STATE_ADDRESS,
    CAPTURED_INITIAL_CHALLENGE,
    FLASH_MODE_MARKER_ADDRESS,
    INITIAL_SEED_RETRY_DELAY,
    MAX_INITIAL_SEED_ATTEMPTS,
    NATIVE_FAST_REENTRY_LATCH_ADDRESS,
    NATIVE_FAST_REENTRY_LATCH_READY_VALUE,
    NATIVE_FAST_REENTRY_POLL_INTERVAL,
    NATIVE_FAST_REENTRY_TIMER_ADDRESS,
    NATIVE_FAST_REENTRY_TIMER_INITIAL_VALUE,
    NATIVE_FAST_REENTRY_TIMEOUT,
    WRONG_KEY_COUNTER_ADDRESS,
)


IDENTIFY_COMMAND = 0x00
READ_MEMORY_COMMAND = 0x06
FLASH_COMMAND = 0x07
STATUS_COMMAND = 0x0D
SEED_KEY_COMMAND = 0x90
PREPARE_COMMAND = 0xA2

SELECTOR_MID = 0x12
SELECTOR_HIGH = 0x01
SELECTOR_LOW = 0x26
TOKEN_ADDRESS = 0x205E
TOKEN_LENGTH = 10
TOKEN_READ_A_ADDRESS = 0x2040
TOKEN_READ_A_LENGTH = 32
TOKEN_READ_B_ADDRESS = 0x2060
TOKEN_READ_B_LENGTH = 18

INITIAL_CHALLENGE = CAPTURED_INITIAL_CHALLENGE
FINALIZE_CHALLENGE = 0x1F
MAX_FINALIZE_SEED_ATTEMPTS = 45

EventCallback = Callable[[str, Mapping[str, object]], None]


class PartialWriteError(RuntimeError):
    """Base class for the native-fast partial writer."""


class UnsafePartialWriteCommand(PartialWriteError):
    """A command or address lies outside the reviewed partial-write surface."""


class PartialWriteTimeout(PartialWriteError):
    """A complete echo or ECU reply did not arrive within the bound."""


class InitialWriteIdentityNotReady(PartialWriteTimeout):
    """Normal low-rate ECU identity did not arrive within the startup bound."""


class InitialWriteSeedUnavailable(PartialWriteTimeout):
    """The ECU stayed safely locked but did not offer a seed within the bound."""


class NativeFastWriteReentryNotReady(PartialWriteTimeout):
    """A known prior native-fast session did not reach its shared ready latch."""


class PartialWriteStateError(PartialWriteError):
    """The requested transition is illegal in the current write state."""


class PartialWriteCancelled(PartialWriteError):
    """Cancellation was accepted at a declared pre-destructive checkpoint."""


class PartialWriteReadbackMismatch(PartialWriteError):
    def __init__(self, address: int, expected: int, actual: int):
        self.address = address
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"partial-write readback differs at DS2 0x{address:05X}: "
            f"expected 0x{expected:02X}, got 0x{actual:02X}"
        )


def compute_ms41_write_key(challenge: int, seed_payload: bytes) -> bytes:
    """Reproduce the stock four-byte seed/key derivation used by the live proof."""

    if not 0 <= challenge <= 41:
        raise ValueError("write challenge must be in the stock range 0..41")
    seed = bytes(seed_payload)
    if len(seed) != 42:
        raise ValueError("MS41 seed payload must be exactly 42 bytes")
    challenge_frame = encode_ds2_frame(
        SEED_KEY_COMMAND, b"BMW" + bytes((challenge,))
    )
    response_frame = encode_ds2_frame(ResponseStatus.ACK, seed)
    combined = challenge_frame + response_frame
    start = challenge + 8
    return bytes(
        (
            combined[start + index]
            + combined[49 + index]
            + combined[26 + index]
        )
        & 0xFF
        for index in range(4)
    )


@dataclass(frozen=True)
class PartialWriteTiming:
    initial_seed_retry_delay: float = INITIAL_SEED_RETRY_DELAY
    native_fast_reentry_poll_interval: float = NATIVE_FAST_REENTRY_POLL_INTERVAL
    native_fast_reentry_timeout: float = NATIVE_FAST_REENTRY_TIMEOUT
    post_authorization_delay: float = 0.40
    between_low_preamble_reads: float = 0.10
    pre_arm_delay: float = 1.60
    post_arm_delay: float = 0.005
    post_high_selector_delay: float = 0.52
    post_erase_delay: float = 2.06
    between_program_requests: float = 0.015
    pre_finalize_delay: float = 1.09
    finalize_seed_poll_delay: float = 0.20
    post_cleanup_readiness_timeout: float = 15.0
    post_cleanup_poll_delay: float = 0.25

    def __post_init__(self) -> None:
        if any(value < 0 for value in self.__dict__.values()):
            raise ValueError("partial-write timing values cannot be negative")


CAPTURED_PARTIAL_WRITE_TIMING = PartialWriteTiming()

# Stock command-0x07/subcommand-0x03 is intentionally exposed only for the
# four-byte checked transmission record.  These are the exact physical 24C04
# offsets used by MS41.0, MS41.1, and MS41.2/MS41.3 respectively.
TRANSMISSION_RECORD_OFFSETS = frozenset((0x196, 0x1CC, 0x1CA))


class NativeFastPartialWriteTransport:
    """One-request-at-a-time native transport with a partial-only allowlist."""

    _FLASH_MODE = FastOperation.PARTIAL_WRITE

    _CONTROL_COMMANDS = frozenset(
        (
            IDENTIFY_COMMAND,
            READ_MEMORY_COMMAND,
            STATUS_COMMAND,
            SEED_KEY_COMMAND,
            PREPARE_COMMAND,
        )
    )

    def __init__(
        self,
        serial_port,
        *,
        baud: int = PRODUCTION_RATE_PROFILE.low,
        echo: bool = True,
        flash_enabled: bool = True,
        first_byte_timeout: float = 1.5,
        inter_byte_timeout: float = 0.25,
        event_cb: Optional[EventCallback] = None,
    ):
        if not bool(getattr(serial_port, "native_fast_capable", False)):
            raise UnsafePartialWriteCommand(
                "native fast partial write requires a supported direct USB transport"
            )
        if not isinstance(getattr(serial_port, "port", None), str) or not serial_port.port:
            raise UnsafePartialWriteCommand("serial-port association is missing")
        if getattr(serial_port, "transport_name", None) == "d2xx":
            adapter_index = getattr(serial_port, "index", None)
            if (
                not isinstance(adapter_index, int)
                or isinstance(adapter_index, bool)
                or adapter_index < 0
            ):
                raise UnsafePartialWriteCommand(
                    "D2XX device index is missing or invalid"
                )
        if not echo:
            raise UnsafePartialWriteCommand(
                "native fast partial write requires exact K-Line echo validation"
            )
        if int(getattr(serial_port, "baudrate", -1)) != int(baud):
            raise UnsafePartialWriteCommand(
                "injected transport is not configured at the declared low baud"
            )
        self.serial = serial_port
        self.baud = int(baud)
        self.echo = True
        self.flash_enabled = bool(flash_enabled)
        self.first_byte_timeout = max(0.01, float(first_byte_timeout))
        self.inter_byte_timeout = max(0.01, float(inter_byte_timeout))
        self.event_cb = event_cb
        self.command_counts = Counter()
        self._request_active = False

    @classmethod
    def open_d2xx(
        cls,
        port: str,
        *,
        baud: int = PRODUCTION_RATE_PROFILE.low,
        event_cb: Optional[EventCallback] = None,
        serial_factory=None,
        flash_enabled: bool = True,
    ):
        """Open the D2XX device mapped to ``port`` with strict 8E2 echo."""
        if serial_factory is None:
            from engines.softbsl.d2xx_serial import D2XXSerial

            serial_factory = D2XXSerial
        serial_port = serial_factory(
            port=port,
            baudrate=int(baud),
            timeout=1.5,
            write_timeout=3.0,
            two_stop=True,
        )
        try:
            serial_port.setDTR(False)
            serial_port.setRTS(False)
            return cls(
                serial_port,
                baud=baud,
                echo=True,
                flash_enabled=flash_enabled,
                event_cb=event_cb,
            )
        except Exception:
            try:
                serial_port.close()
            except Exception:
                pass
            raise

    @property
    def is_open(self) -> bool:
        return bool(getattr(self.serial, "is_open", False))

    def _emit(self, event: str, **fields: object) -> None:
        if self.event_cb is not None:
            self.event_cb(event, fields)

    def close(self) -> None:
        if self.is_open:
            self.serial.close()

    def queue_status(self) -> Tuple[int, int, int]:
        getter = getattr(self.serial, "queue_status", None)
        if getter is None:
            raise PartialWriteError("D2XX transport does not expose queue status")
        rx_bytes, tx_bytes, event_count = getter()
        return int(rx_bytes), int(tx_bytes), int(event_count)

    def set_baud(self, baud: int, *, reason: str) -> None:
        old = self.baud
        self.serial.baudrate = int(baud)
        self.baud = int(baud)
        self._emit("host_baud_changed", old=old, new=self.baud, reason=reason)

    def _read_exact(self, length: int, timeout: float) -> bytes:
        self.serial.timeout = float(timeout)
        output = bytearray()
        while len(output) < length:
            part = self.serial.read(length - len(output))
            if not part:
                break
            output.extend(part)
        return bytes(output)

    @staticmethod
    def _validate_control(command: int, args: bytes, state: SessionState) -> None:
        if command not in NativeFastPartialWriteTransport._CONTROL_COMMANDS:
            raise UnsafePartialWriteCommand(
                f"command 0x{command:02X} is not in the partial-write control allowlist"
            )
        if command in (IDENTIFY_COMMAND, STATUS_COMMAND, PREPARE_COMMAND) and args:
            raise UnsafePartialWriteCommand(
                f"command 0x{command:02X} cannot contain arguments"
            )
        if command == READ_MEMORY_COMMAND:
            if len(args) != 5 or not 1 <= args[4] <= MAX_READ_DATA:
                raise UnsafePartialWriteCommand(
                    "READ_MEM requires a four-byte address and count 1..247"
                )
        if command == SEED_KEY_COMMAND:
            valid = (
                args == b"BMW"
                or (len(args) == 4 and args[:3] == b"BMW" and args[3] <= 41)
                or (len(args) == 4 and args[:3] != b"BMW")
                or (
                    len(args) == TOKEN_LENGTH + 1
                    and args[0] in (SELECTOR_LOW, SELECTOR_MID, SELECTOR_HIGH)
                )
            )
            if not valid:
                raise UnsafePartialWriteCommand(
                    "command 0x90 arguments are not a challenge, key, arm, or up-selector"
                )
            if len(args) == TOKEN_LENGTH + 1:
                selector = args[0]
                allowed_states = {
                    SELECTOR_MID: frozenset((SessionState.ARMED_LOW,)),
                    SELECTOR_HIGH: frozenset((SessionState.ARMED_LOW, SessionState.MID)),
                    SELECTOR_LOW: frozenset(
                        (SessionState.HIGH_PARTIAL_WRITE, SessionState.WRITE_FINALIZE_HIGH)
                    ),
                }
                if state not in allowed_states[selector]:
                    raise UnsafePartialWriteCommand(
                        f"selector 0x{selector:02X} is not allowed in state {state.value}"
                    )

    @staticmethod
    def _validate_flash(request: FlashRequest, state: SessionState) -> None:
        operation = request.operation
        if operation == int(FlashOperation.EEPROM_WRITE):
            if (
                request.address not in TRANSMISSION_RECORD_OFFSETS
                or request.count != 4
            ):
                raise UnsafePartialWriteCommand(
                    "EEPROM writes are restricted to one known four-byte "
                    "transmission record"
                )
            if state is not SessionState.HIGH_PARTIAL_WRITE:
                raise PartialWriteStateError(
                    "EEPROM transmission write requires HIGH_PARTIAL_WRITE"
                )
            return
        if operation == int(FlashOperation.ERASE):
            if request.address != TUNE_START or request.count != 0:
                raise UnsafePartialWriteCommand(
                    "partial erase is restricted to DS2 0x010000"
                )
            if state is not SessionState.HIGH_PARTIAL_WRITE:
                raise PartialWriteStateError("tune erase requires HIGH_PARTIAL_WRITE")
            return
        if operation == int(FlashOperation.PARTIAL_PROGRAM):
            end = request.address + request.count
            if not TUNE_START <= request.address < end <= TUNE_END:
                raise UnsafePartialWriteCommand(
                    "partial program data must stay inside DS2 0x10000..0x15FFF"
                )
            if request.address // ROM_BLOCK_SIZE != (end - 1) // ROM_BLOCK_SIZE:
                raise UnsafePartialWriteCommand(
                    "partial program request crosses a 0x4000 boundary"
                )
            if state is not SessionState.HIGH_PARTIAL_WRITE:
                raise PartialWriteStateError("program data requires HIGH_PARTIAL_WRITE")
            return
        if operation == int(FlashOperation.POLL):
            if request.address != FINAL_POLL_ADDRESS or request.count != 0:
                raise UnsafePartialWriteCommand(
                    "partial final poll is restricted to DS2 0x001D07"
                )
            if state is not SessionState.WRITE_FINALIZE_HIGH:
                raise PartialWriteStateError("final poll requires WRITE_FINALIZE_HIGH")
            return
        raise UnsafePartialWriteCommand(
            f"flash operation 0x{operation:02X} is not available to the partial writer"
        )

    def _exchange(
        self,
        command: int,
        args: bytes,
        *,
        label: str,
        rate: LinkRate,
        state: SessionState,
        contract: Optional[StatusResponseContract] = None,
        flash_request: Optional[FlashRequest] = None,
        flash_allowed_statuses=frozenset((0x01,)),
        first_byte_timeout: Optional[float] = None,
    ) -> Union[DS2Response, FlashReply]:
        args = bytes(args)
        if flash_request is None:
            self._validate_control(command, args, state)
            if contract is None:
                raise ValueError("control request requires a response contract")
        else:
            if not self.flash_enabled:
                raise UnsafePartialWriteCommand(
                    "flash requests are disabled for this transport"
                )
            if command != FLASH_COMMAND or args != flash_request.payload:
                raise UnsafePartialWriteCommand("flash request/frame mismatch")
            self._validate_flash(flash_request, state)
        if self._request_active:
            raise PartialWriteStateError("only one DS2 request may be outstanding")
        if not self.is_open:
            raise PartialWriteStateError("D2XX transport is closed")

        frame = encode_ds2_frame(command, args)
        self._request_active = True
        started = time.monotonic()
        echo = b""
        raw = b""
        echo_complete = False
        response_validated = False
        try:
            self._emit(
                "request_started",
                label=label,
                baud=self.baud,
                command=f"0x{command:02X}",
                frame_length=len(frame),
                destructive=bool(flash_request and flash_request.destructive),
            )
            self.serial.reset_input_buffer()
            written = self.serial.write(frame)
            if written != len(frame):
                raise PartialWriteError(
                    f"short D2XX write for {label}: {written}/{len(frame)} bytes"
                )
            self.serial.flush()

            echo_timeout = max(
                0.05,
                len(frame) * BITS_PER_CHARACTER_8E2 / self.baud + 0.05,
            )
            echo = self._read_exact(len(frame), echo_timeout)
            if echo != frame:
                raise PartialWriteError(
                    f"K-Line echo mismatch for {label}: expected "
                    f"{frame.hex(' ')}, got {echo.hex(' ')}"
                )
            echo_complete = True

            start_timeout = (
                self.first_byte_timeout
                if first_byte_timeout is None
                else max(0.01, float(first_byte_timeout))
            )
            head = self._read_exact(2, start_timeout)
            if len(head) != 2:
                raise PartialWriteTimeout(
                    f"no complete response header for {label} at {self.baud} baud"
                )
            if head[0] != 0x12 or not 4 <= head[1] <= 0xFC:
                raise FrameValidationError(
                    f"invalid DS2 response header for {label}: {head.hex(' ')}"
                )
            rest = self._read_exact(head[1] - 2, self.inter_byte_timeout)
            raw = head + rest
            if len(raw) != head[1]:
                raise PartialWriteTimeout(
                    f"short response for {label}: expected {head[1]}, got {len(raw)}"
                )

            if flash_request is not None:
                reply = validate_flash_exchange(
                    self._FLASH_MODE,
                    flash_request,
                    raw,
                    echo_complete=True,
                    rate=rate,
                    state=state,
                    label=label,
                    allowed_statuses=frozenset(flash_allowed_statuses),
                )
                result: Union[DS2Response, FlashReply] = reply
                response = reply.response
            else:
                response = decode_ds2_response(
                    raw, rate=rate, state=state, label=label
                )
                result = contract.validate(response)  # type: ignore[union-attr]
            response_validated = True
            self.command_counts[command] += 1
            self._emit(
                "request_completed",
                label=label,
                baud=self.baud,
                status=f"0x{response.status:02X}",
                response_length=len(raw),
                duration_s=round(time.monotonic() - started, 6),
            )
            return result
        except Exception as error:
            classified: Exception = error
            if (
                flash_request is not None
                and flash_request.destructive
                and echo_complete
                and not response_validated
                and not isinstance(error, (CommitUnknownError, ContractViolation))
            ):
                classified = CommitUnknownError(flash_request, str(error))
            self._emit(
                "request_failed",
                label=label,
                baud=self.baud,
                echo_complete=echo_complete,
                echo_length=len(echo),
                response_length=len(raw),
                error=f"{type(classified).__name__}: {classified}",
                duration_s=round(time.monotonic() - started, 6),
                retry_allowed=False if flash_request is not None else None,
            )
            if classified is error:
                raise
            raise classified from error
        finally:
            self._request_active = False

    def request(
        self,
        command: int,
        args: bytes,
        *,
        contract: StatusResponseContract,
        label: str,
        rate: LinkRate,
        state: SessionState,
        first_byte_timeout: Optional[float] = None,
    ) -> DS2Response:
        result = self._exchange(
            command,
            args,
            label=label,
            rate=rate,
            state=state,
            contract=contract,
            first_byte_timeout=first_byte_timeout,
        )
        if not isinstance(result, DS2Response):
            raise AssertionError("control exchange returned a flash reply")
        return result

    def flash(
        self,
        request: FlashRequest,
        *,
        label: str,
        rate: LinkRate,
        state: SessionState,
        allowed_statuses=frozenset((0x01,)),
        first_byte_timeout: Optional[float] = None,
    ) -> FlashReply:
        result = self._exchange(
            FLASH_COMMAND,
            request.payload,
            label=label,
            rate=rate,
            state=state,
            flash_request=request,
            flash_allowed_statuses=frozenset(allowed_statuses),
            first_byte_timeout=first_byte_timeout,
        )
        if not isinstance(result, FlashReply):
            raise AssertionError("flash exchange returned a control response")
        return result


class NativeFastPartialWriteSession:
    """Validated native-fast state transitions used by the slim writer."""

    def _record(self, event: str, **fields: object) -> None:
        self.journal.append(event, **fields)

    def _set_state(
        self,
        *,
        state: Optional[SessionState] = None,
        link: Optional[LinkRate] = None,
        reason: str,
    ) -> None:
        old_state = self.state
        old_link = self.link
        if state is not None:
            self.state = state
        if link is not None:
            self.link = link
        self._record(
            "session_state_changed",
            old_state=old_state.value,
            new_state=self.state.value,
            old_link=old_link.name.lower(),
            new_link=self.link.name.lower(),
            reason=reason,
        )

    def _progress(self, phase: str, current: int, total: int) -> None:
        if self.progress_cb is not None:
            self.progress_cb(phase, current, total)

    def _record_queue_status(self, *, phase: str) -> None:
        """Journal transport queues without changing the authorization path."""
        try:
            rx_bytes, tx_bytes, event_count = self.transport.queue_status()
        except Exception as error:
            self.transport._emit(
                "d2xx_queue_status",
                phase=phase,
                available=False,
                error=f"{type(error).__name__}: {error}",
            )
            return
        self.transport._emit(
            "d2xx_queue_status",
            phase=phase,
            available=True,
            rx_bytes=rx_bytes,
            tx_bytes=tx_bytes,
            event_count=event_count,
        )

    def _cancel_checkpoint(self, label: str) -> None:
        if self.cancel_cb is not None and self.cancel_cb():
            self._record(
                "cancellation_accepted",
                checkpoint=label,
                destructive_started=self.destructive_started,
            )
            raise PartialWriteCancelled(f"cancelled at {label}")

    def _request(
        self,
        command: int,
        args: bytes,
        contract: StatusResponseContract,
        label: str,
    ) -> DS2Response:
        return self.transport.request(
            command,
            args,
            contract=contract,
            label=label,
            rate=self.link,
            state=self.state,
        )

    def _identify(self, *, attempts: int = 1) -> bytes:
        if attempts < 1:
            raise ValueError("initial identity attempts must be positive")
        if attempts > 1:
            self._progress("Waiting for ECU normal DS2 readiness", 0, 1)
        for attempt in range(1, attempts + 1):
            try:
                return self._request(
                    IDENTIFY_COMMAND,
                    b"",
                    StatusResponseContract(
                        "initial write identity",
                        frozenset((ResponseStatus.ACK,)),
                        exact_payload_length=IDENTITY_LENGTH,
                    ),
                    (
                        "initial_write_identify"
                        if attempt == 1
                        else f"initial_write_identify_retry_{attempt}"
                    ),
                ).payload
            except PartialWriteTimeout as error:
                self._record(
                    "initial_write_identity_timeout",
                    attempt=attempt,
                    attempts=attempts,
                    error=str(error),
                )
                if attempt == attempts:
                    raise InitialWriteIdentityNotReady(
                        "normal low DS2 identity did not become ready after "
                        f"{attempts} bounded attempt(s): {error}"
                    ) from error
                self._sleep(self.timing.post_cleanup_poll_delay)
        raise AssertionError("unreachable initial identity loop")

    def _read_mem(self, address: int, count: int, *, label: str) -> bytes:
        response = self._request(
            READ_MEMORY_COMMAND,
            int(address).to_bytes(4, "big") + bytes((count,)),
            read_response_contract(count),
            label,
        )
        return response.payload

    def _read_range(self, address: int, length: int, *, phase: str) -> bytes:
        output = bytearray()
        while len(output) < length:
            count = min(MAX_READ_DATA, length - len(output))
            current = address + len(output)
            output.extend(
                self._read_mem(
                    current,
                    count,
                    label=f"{phase}_0x{current:05X}_{count}",
                )
            )
            self._progress(phase, len(output), length)
        return bytes(output)

    def _require_token(self) -> bytes:
        if self.token is None:
            raise PartialWriteStateError("ECU token is unavailable")
        return self.token

    def _read_authorization_state(self, *, label_prefix: str) -> Tuple[int, int]:
        state = self._read_mem(
            AUTHORIZATION_STATE_ADDRESS,
            1,
            label=f"{label_prefix}_state_0xE658",
        )[0]
        wrong_keys = self._read_mem(
            WRONG_KEY_COUNTER_ADDRESS,
            1,
            label=f"{label_prefix}_wrong_keys_0xE74B",
        )[0]
        self._record(
            "write_authorization_state_observed",
            label=label_prefix,
            e658=state,
            e74b=wrong_keys,
        )
        return state, wrong_keys

    def _observe_flash_mode_marker(self, *, label_prefix: str) -> int:
        """Record E740 for diagnosis without using it as an authorization gate."""
        marker = self._read_mem(
            FLASH_MODE_MARKER_ADDRESS,
            1,
            label=f"{label_prefix}_flash_mode_marker_0xE740",
        )[0]
        self.transport._emit(
            "write_flash_mode_marker_observed",
            label=label_prefix,
            e740=marker,
        )
        return marker

    def _wait_for_native_fast_reentry(
        self,
        *,
        expected_state: int,
        initial_wrong_keys: int,
    ) -> None:
        """Observe the shared completion latch only after a known fast session.

        The caller-provided per-port state scopes this away from fresh initial
        authorization. E72E/E659 are shared by MS41.0 through MS41.3, unlike
        the variant-specific F1FA/F7E0/F7E8 selector countdowns.
        """

        if not bool(getattr(self, "reentry_required", False)):
            self._record("write_native_fast_reentry_not_required")
            return

        timeout_s = float(self.timing.native_fast_reentry_timeout)
        poll_s = float(self.timing.native_fast_reentry_poll_interval)
        started = time.monotonic()
        sample = 1
        wait_announced = False
        while True:
            timer = int.from_bytes(
                self._read_mem(
                    NATIVE_FAST_REENTRY_TIMER_ADDRESS,
                    2,
                    label=f"write_native_fast_reentry_{sample:02d}_0xE72E",
                ),
                "little",
            )
            latch = self._read_mem(
                NATIVE_FAST_REENTRY_LATCH_ADDRESS,
                1,
                label=f"write_native_fast_reentry_{sample:02d}_0xE659",
            )[0]
            elapsed_s = time.monotonic() - started
            state, wrong_keys = self._read_authorization_state(
                label_prefix=f"write_reentry_sample_{sample:02d}"
            )
            marker_fields = {
                "sample": sample,
                "e72e": timer,
                "e659": latch,
                "e658": state,
                "e74b": wrong_keys,
                "elapsed_s": round(elapsed_s, 6),
            }
            self._record("write_native_fast_reentry_observed", **marker_fields)

            if state != expected_state or wrong_keys != initial_wrong_keys:
                self.authorization_may_be_active = state in (1, 2)
                self.authorization_state_requires_cycle = state not in (0, 2)
                raise PartialWriteStateError(
                    "write authorization changed while waiting for native-fast "
                    f"reentry (E658={state}, E74B={wrong_keys}); no challenge "
                    "or flash command was sent"
                )
            if latch == NATIVE_FAST_REENTRY_LATCH_READY_VALUE:
                self.reentry_required = False
                ready_cb = getattr(self, "reentry_ready_cb", None)
                if ready_cb is not None:
                    ready_cb()
                self._record("write_native_fast_reentry_ready", **marker_fields)
                return
            if timer > NATIVE_FAST_REENTRY_TIMER_INITIAL_VALUE:
                raise NativeFastWriteReentryNotReady(
                    f"ECU native-fast reentry timer E72E is implausible ({timer}); "
                    "no challenge, selector, or flash command was sent"
                )
            if elapsed_s >= timeout_s:
                raise NativeFastWriteReentryNotReady(
                    "ECU native-fast completion latch E659 did not reach 0xCC "
                    f"within {timeout_s:g} seconds (E72E={timer}); no challenge, "
                    "selector, or flash command was sent"
                )

            if not wait_announced:
                self._record(
                    "write_native_fast_reentry_wait_started",
                    timeout_s=timeout_s,
                    **marker_fields,
                )
                wait_announced = True
            wait_label = (
                "Waiting for ECU native-fast readiness "
                f"(E72E={timer}, E659=0x{latch:02X})"
            )
            self._progress(wait_label, sample, 0)
            self._sleep(min(poll_s, max(0.0, timeout_s - elapsed_s)))
            sample += 1

    def _authorize_once(self) -> str:
        state, initial_wrong_keys = self._read_authorization_state(
            label_prefix="write_authorization_initial"
        )
        self._observe_flash_mode_marker(
            label_prefix="write_authorization_initial"
        )
        if initial_wrong_keys >= 2:
            self.authorization_state_requires_cycle = True
            raise PartialWriteStateError(
                "write authorization is locked (E74B >= 2); turn ignition off, "
                "wait 10 seconds, then turn ignition on before flashing"
            )
        if state == 1:
            self.authorization_may_be_active = True
            self.authorization_state_requires_cycle = True
            raise PartialWriteStateError(
                "ECU is already waiting for an authorization key (E658=1); "
                "turn ignition off, wait 10 seconds, then turn ignition on "
                "before flashing"
            )
        if state not in (0, 2):
            self.authorization_state_requires_cycle = True
            raise PartialWriteStateError(
                f"unexpected write-authorization state E658={state}"
            )

        self._wait_for_native_fast_reentry(
            expected_state=state,
            initial_wrong_keys=initial_wrong_keys,
        )

        if state == 2:
            self.authorization_may_be_active = True
            self.authorization_state_requires_cycle = True
            self._record_queue_status(
                phase="before_existing_authorization_confirmation"
            )
            confirmation = self._request(
                SEED_KEY_COMMAND,
                b"BMW" + bytes((self.challenge,)),
                StatusResponseContract(
                    "existing write authorization confirmation",
                    frozenset((ResponseStatus.ACK,)),
                    exact_payload_length=1,
                ),
                "write_existing_authorization_confirmation",
            )
            if confirmation.payload != b"\x00":
                raise ContractViolation(
                    "existing write authorization confirmation payload is not 00"
                )
            seed_response = confirmation
        else:
            seed_response = None
        seed_attempts = range(1, MAX_INITIAL_SEED_ATTEMPTS + 1) if state == 0 else ()
        for attempt in seed_attempts:
            self._request(
                PREPARE_COMMAND,
                b"",
                StatusResponseContract(
                    "write prepare",
                    frozenset((ResponseStatus.READY_FF,)),
                    exact_payload_length=0,
                ),
                "write_prepare_expected_FF",
            )
            self._read_mem(0x2001, 12, label="write_preamble_state_0x2001")
            self._request(
                STATUS_COMMAND,
                b"",
                StatusResponseContract(
                    "pre-authorization status",
                    frozenset((ResponseStatus.ACK,)),
                ),
                "write_status_before_seed",
            )
            self._record_queue_status(
                phase=f"before_initial_write_seed_attempt_{attempt}"
            )
            try:
                response = self._request(
                    SEED_KEY_COMMAND,
                    b"BMW" + bytes((self.challenge,)),
                    StatusResponseContract(
                        "write seed challenge",
                        frozenset((ResponseStatus.ACK, ResponseStatus.CONTEXT_A1)),
                    ),
                    f"write_seed_challenge_{self.challenge}",
                )
            except Exception:
                # The complete challenge frame was already transmitted. Unless
                # a contextual A1 is decoded below and RAM proves E658 stayed
                # zero, no further 0x90 command is safe in this power cycle.
                self.authorization_may_be_active = True
                self.authorization_state_requires_cycle = True
                raise
            if response.status == ResponseStatus.ACK:
                seed_response = response
                break
            # A contextual A1 is retryable only after the checks below prove
            # that the ECU did not enter (or pass through) its key state.  Mark
            # the result ambiguous before inspecting its payload so even a
            # malformed A1 cannot authorize cleanup or a legacy 0x90 retry.
            self.authorization_may_be_active = True
            self.authorization_state_requires_cycle = True
            if response.payload:
                raise ContractViolation(
                    "pre-key write-seed A1 unexpectedly carried a payload"
                )
            self._record(
                "initial_write_seed_not_ready",
                attempt=attempt,
                max_attempts=MAX_INITIAL_SEED_ATTEMPTS,
                status="0xA1",
            )
            try:
                state_after, wrong_keys_after = self._read_authorization_state(
                    label_prefix=f"write_seed_a1_attempt_{attempt:02d}"
                )
                self._observe_flash_mode_marker(
                    label_prefix=f"write_seed_a1_attempt_{attempt:02d}"
                )
            except Exception:
                self.authorization_may_be_active = True
                raise
            if state_after != 0 or wrong_keys_after != initial_wrong_keys:
                self.authorization_may_be_active = state_after in (1, 2)
                raise PartialWriteStateError(
                    "write-seed response was ambiguous and ECU authorization "
                    f"state changed (E658={state_after}, E74B={wrong_keys_after}); "
                    "turn ignition off, wait 10 seconds, then turn ignition on"
                )
            self.authorization_may_be_active = False
            self.authorization_state_requires_cycle = False
            if attempt < MAX_INITIAL_SEED_ATTEMPTS:
                quiet_s = float(self.timing.initial_seed_retry_delay)
                wait_label = (
                    f"ECU seed not ready; waiting {quiet_s:g} seconds "
                    "before one final retry"
                )
                self._record(
                    "initial_write_seed_retry_wait_started",
                    attempt=attempt,
                    next_attempt=attempt + 1,
                    seconds=quiet_s,
                )
                self._progress(wait_label, 0, 0)
                quiet_started = time.monotonic()
                self._sleep(quiet_s)
                self._record(
                    "initial_write_seed_retry_wait_completed",
                    attempt=attempt,
                    next_attempt=attempt + 1,
                    seconds=quiet_s,
                    actual_s=round(time.monotonic() - quiet_started, 6),
                )
        if seed_response is None:
            raise InitialWriteSeedUnavailable(
                "initial write seed unavailable after "
                f"{MAX_INITIAL_SEED_ATTEMPTS} bounded BMW/0x{self.challenge:02X} challenges"
            )
        if seed_response.payload == b"\x00":
            self.authorization_may_be_active = True
            result = "already_authorized"
        else:
            if len(seed_response.payload) != 42:
                self.authorization_may_be_active = True
                self.authorization_state_requires_cycle = True
                raise ContractViolation(
                    f"write seed response has {len(seed_response.payload)} bytes; expected 42"
                )
            key = compute_ms41_write_key(self.challenge, seed_response.payload)
            # Once the complete key frame is attempted, a missing/malformed ACK
            # cannot prove that the ECU remained locked.  Any failure from here
            # requires a physical power cycle even though flash is untouched.
            self.authorization_may_be_active = True
            key_response = self._request(
                SEED_KEY_COMMAND,
                key,
                StatusResponseContract(
                    "write key acknowledgement",
                    frozenset((ResponseStatus.ACK,)),
                    exact_payload_length=1,
                ),
                "write_key_single_attempt",
            )
            if key_response.payload != b"\x00":
                raise ContractViolation("write key acknowledgement payload is not 00")
            result = "new_authorization"
        self.write_authorized = True
        self.authorization_state_requires_cycle = False
        self._set_state(
            state=SessionState.AUTHORIZED_LOW,
            link=LinkRate.LOW,
            reason="single-attempt write authorization accepted",
        )
        self._record(
            "write_authorized",
            result=result,
            key_attempts=0 if result == "already_authorized" else 1,
        )
        return result

    def _arm_and_enter_high(self) -> None:
        self._sleep(self.timing.post_authorization_delay)
        self._read_mem(0x1CF4, 3, label="fast_partial_low_preamble_0x1CF4")
        self._sleep(self.timing.between_low_preamble_reads)
        self._read_mem(0x1000E, 2, label="fast_partial_low_preamble_0x1000E")
        self._sleep(self.timing.pre_arm_delay)
        arm = self._request(
            SEED_KEY_COMMAND,
            b"BMW",
            StatusResponseContract(
                "authorized bare BMW arm",
                frozenset((ResponseStatus.ACK,)),
                exact_payload_length=1,
            ),
            "bare_BMW_authorized_fast_write_arm_expected_A0_00",
        )
        if arm.payload != b"\x00":
            raise ContractViolation("authorized bare BMW arm payload is not 00")
        self.fast_write_armed = True
        self._set_state(
            state=SessionState.ARMED_LOW,
            reason="captured bare-BMW write arm accepted",
        )
        self._sleep(self.timing.post_arm_delay)

        if not self.rates.direct_low_to_high:
            self._switch_up(SELECTOR_MID, LinkRate.MID, SessionState.MID)
        self._switch_up(
            SELECTOR_HIGH,
            LinkRate.HIGH,
            SessionState.HIGH_PARTIAL_WRITE,
        )
        # The host must prove request/echo/response integrity at the selected
        # high rate before the destructive boundary.  This exact token read was
        # qualified on the stock write-armed path and is safe to repeat.
        actual = self._read_mem(
            TOKEN_ADDRESS,
            TOKEN_LENGTH,
            label="high_rate_pre_erase_token_liveness",
        )
        if actual != self._require_token():
            raise PartialWriteError(
                "high-rate pre-erase liveness differs from the low-rate token"
            )
        self._record(
            "high_rate_pre_erase_liveness_validated",
            baud=self.rates.high,
        )
        self._sleep(self.timing.post_high_selector_delay)

    def _switch_up(
        self,
        selector: int,
        target: LinkRate,
        target_state: SessionState,
    ) -> None:
        allowed = {
            (LinkRate.LOW, SELECTOR_MID, LinkRate.MID),
            (LinkRate.LOW, SELECTOR_HIGH, LinkRate.HIGH),
            (LinkRate.MID, SELECTOR_HIGH, LinkRate.HIGH),
        }
        old = self.link
        if (old, selector, target) not in allowed:
            raise PartialWriteStateError(
                f"disallowed write escalation {old.name} --0x{selector:02X}--> {target.name}"
            )
        self._request(
            SEED_KEY_COMMAND,
            bytes((selector,)) + self._require_token(),
            selector_ack_contract(),
            f"selector_0x{selector:02X}_{old.name.lower()}_to_{target.name.lower()}",
        )
        self._set_state(
            state=target_state,
            link=LinkRate.UNKNOWN,
            reason="selector ACK completed at old rate",
        )
        guard = max(
            0.001,
            2 * BITS_PER_CHARACTER_8E2 / self.rates.for_link(old),
        )
        self._sleep(guard)
        self.transport.set_baud(
            self.rates.for_link(target),
            reason=f"selector 0x{selector:02X} acknowledged at {old.name.lower()}",
        )
        # The proven write trace sends no extra liveness frame between selector
        # ACK and the next captured write-state transition.
        self._set_state(
            state=target_state,
            link=target,
            reason="captured write selector transition completed without added traffic",
        )

    def _flash(
        self,
        request: FlashRequest,
        *,
        label: str,
        timeout: Optional[float] = None,
    ) -> FlashReply:
        return self.transport.flash(
            request,
            label=label,
            rate=self.link,
            state=self.state,
            first_byte_timeout=timeout,
        )

    def _erase_and_program(self) -> Tuple[int, int]:
        if self.plan is None:
            raise PartialWriteStateError("write plan is unavailable")
        self._progress("Erasing calibration region", 0, 1)
        self.destructive_started = True
        self._record(
            "destructive_boundary_crossed",
            request_operation="0x06",
            response_operation="0x00",
            address=f"0x{self.plan.erase.address:06X}",
            retry_policy="never",
        )
        self._flash(
            self.plan.erase,
            label="fast_partial_tune_erase_06_to_00",
            timeout=5.0,
        )
        self._record("tune_erase_acknowledged", address="0x010000")
        self._sleep(self.timing.post_erase_delay)

        total = len(self.plan.program)
        payload_bytes = sum(request.count for request in self.plan.program)
        self._record(
            "partial_program_started",
            blocks=total,
            payload_bytes=payload_bytes,
            retry_policy="none",
        )
        done = 0
        self._progress("Writing calibration region", done, payload_bytes)
        for index, request in enumerate(self.plan.program, 1):
            self._flash(
                request,
                label=(
                    f"fast_partial_program_{index:03d}_"
                    f"0x{request.address:06X}_{request.count}"
                ),
            )
            done += request.count
            self._progress("Writing calibration region", done, payload_bytes)
            self._sleep(self.timing.between_program_requests)
        self._record(
            "partial_program_acknowledged",
            blocks=total,
            payload_bytes=payload_bytes,
        )
        return total, payload_bytes

    def _finalize(self) -> int:
        # A zero total is a status-only update.  Keep the completed byte bar in
        # place while the capture-qualified finalizer performs its seed polls.
        self._progress("Finalizing calibration write", 0, 0)
        self._set_state(
            state=SessionState.WRITE_FINALIZE_HIGH,
            link=LinkRate.HIGH,
            reason="all planned program replies validated",
        )
        self._sleep(self.timing.pre_finalize_delay)
        self._request(
            PREPARE_COMMAND,
            b"",
            StatusResponseContract(
                "fast partial finalize prepare",
                frozenset((ResponseStatus.READY_FF,)),
                exact_payload_length=0,
            ),
            "fast_partial_finalize_prepare_expected_FF",
        )
        self._read_mem(0x2001, 12, label="fast_partial_finalize_state_0x2001")
        self._request(
            STATUS_COMMAND,
            b"",
            StatusResponseContract(
                "fast partial initial finalize status",
                frozenset((ResponseStatus.READY_FF,)),
                exact_payload_length=0,
            ),
            "fast_partial_finalize_initial_status_expected_FF",
        )

        seed = None
        attempts = 0
        for attempts in range(1, MAX_FINALIZE_SEED_ATTEMPTS + 1):
            response = self._request(
                SEED_KEY_COMMAND,
                b"BMW" + bytes((self.finalize_challenge,)),
                StatusResponseContract(
                    "fast partial finalize seed polling",
                    frozenset(
                        (
                            ResponseStatus.ACK,
                            ResponseStatus.CONTEXT_A1,
                            ResponseStatus.CONTEXT_B0,
                        )
                    ),
                ),
                f"fast_partial_finalize_seed_attempt_{attempts:02d}",
            )
            if response.status == ResponseStatus.ACK:
                if len(response.payload) != 42:
                    raise ContractViolation(
                        "fast-partial finalize seed ACK did not contain 42 bytes"
                    )
                seed = response.payload
                break
            if response.payload:
                raise ContractViolation(
                    "fast-partial busy seed response unexpectedly carried payload"
                )
            self._sleep(self.timing.finalize_seed_poll_delay)
        if seed is None:
            raise PartialWriteTimeout(
                f"finalize seed unavailable after {MAX_FINALIZE_SEED_ATTEMPTS} bounded polls"
            )

        key = compute_ms41_write_key(self.finalize_challenge, seed)
        key_response = self._request(
            SEED_KEY_COMMAND,
            key,
            StatusResponseContract(
                "fast partial finalize key acknowledgement",
                frozenset((ResponseStatus.ACK,)),
                exact_payload_length=1,
            ),
            "fast_partial_finalize_key_single_attempt",
        )
        if key_response.payload != b"\x00":
            raise ContractViolation("fast-partial finalize key ACK payload is not 00")
        self._request(
            STATUS_COMMAND,
            b"",
            StatusResponseContract(
                "post-finalize-key status",
                frozenset((ResponseStatus.ACK,)),
            ),
            "fast_partial_finalize_status_after_key",
        )
        if self.plan is None:
            raise PartialWriteStateError("write plan is unavailable")
        self._flash(
            self.plan.final_poll,
            label="fast_partial_finalize_program_signature_poll_0x1D07",
        )
        self._record(
            "fast_partial_finalize_completed",
            seed_attempts=attempts,
            key_attempts=1,
            final_poll_address="0x001D07",
        )
        return attempts

    def _wait_for_low_identity(
        self,
        *,
        contract_name: str,
        label: str,
        timeout_event: str,
    ) -> bytes:
        """Wait through bounded low-rate silence/A2 and confirm ECU identity."""
        deadline = time.monotonic() + self.timing.post_cleanup_readiness_timeout
        while time.monotonic() < deadline:
            try:
                response = self._request(
                    IDENTIFY_COMMAND,
                    b"",
                    StatusResponseContract(
                        contract_name,
                        frozenset((ResponseStatus.ACK, ResponseStatus.READINESS_A2)),
                    ),
                    label,
                )
            except PartialWriteTimeout as error:
                self._record(timeout_event, error=str(error))
                self._sleep(self.timing.post_cleanup_poll_delay)
                continue
            if response.status == ResponseStatus.ACK:
                if len(response.payload) != IDENTITY_LENGTH:
                    raise ContractViolation(
                        f"{contract_name} identity has an unexpected length"
                    )
                if self.identity is not None and response.payload != self.identity:
                    raise ContractViolation(
                        f"{contract_name} identity differs from the initial identity"
                    )
                return response.payload
            if response.payload:
                raise ContractViolation(
                    f"transitional {contract_name} A2 contained a payload"
                )
            self._sleep(self.timing.post_cleanup_poll_delay)
        raise PartialWriteTimeout(
            f"normal low DS2 did not become ready during {contract_name}"
        )

    def _cleanup_to_low(self) -> bool:
        """Apply the live-proven partial-write selector/BMW cleanup."""
        if self.link not in (LinkRate.LOW, LinkRate.HIGH, LinkRate.MID):
            raise PartialWriteStateError(
                f"partial cleanup requires a known link, got {self.link.name}"
            )
        self._progress("Returning ECU to DS2 9600", 0, 0)
        old = self.link
        self.cleanup_attempted = True
        self._request(
            SEED_KEY_COMMAND,
            bytes((SELECTOR_LOW,)) + self._require_token(),
            selector_ack_contract(),
            f"selector_0x26_{old.name.lower()}_to_low",
        )
        self._set_state(
            state=SessionState.LOW_RECOVERY,
            link=LinkRate.UNKNOWN,
            reason="selector 0x26 ACK completed at the old rate",
        )
        guard = max(
            0.001,
            2 * BITS_PER_CHARACTER_8E2 / self.rates.for_link(old),
        )
        self._sleep(guard)
        self.transport.set_baud(
            self.rates.low,
            reason=f"selector 0x26 acknowledged at {old.name.lower()}",
        )
        self.link = LinkRate.LOW
        self._request(
            SEED_KEY_COMMAND,
            b"BMW",
            contextual_recovery_contract(
                ResponseStatus.CONTEXT_B0,
                exact_payload_length=0,
                name="bare BMW partial-write fast-state cleanup",
            ),
            "bare_BMW_partial_write_cleanup_expected_B0",
        )

        try:
            self._wait_for_low_identity(
                contract_name="post-partial-write low readiness",
                label="post_partial_write_low_readiness",
                timeout_event="post_partial_write_low_readiness_timeout",
            )
        except PartialWriteTimeout:
            pass
        else:
            self._set_state(
                state=SessionState.COMPLETE,
                link=LinkRate.LOW,
                reason="partial write cleaned up to normal low DS2",
            )
            self._record("partial_write_cleanup_confirmed", final_baud=self.rates.low)
            return True
        if self.destructive_started and (
            self.restore_verified or getattr(self, "flash_completed", False)
        ):
            # The flash finalizer completed and both the selector and B0 cleanup
            # completed at their expected rates.  A silent ECU beyond the
            # readiness window requires the documented physical cycle, not a
            # second destructive erase.  Readback proof is reported separately.
            self._set_state(
                state=SessionState.POWER_CYCLE_REQUIRED,
                link=LinkRate.LOW,
                reason=(
                    "completed partial write cleaned to low but normal identity "
                    "remains pending the operator ignition cycle"
                ),
            )
            self._record(
                "partial_write_cleanup_identity_pending_power_cycle",
                final_baud=self.rates.low,
                readback_verified=self.restore_verified,
            )
            return False
        raise PartialWriteTimeout(
            "normal low DS2 did not become ready after partial-write cleanup"
        )

    def _recover_pre_erase_to_low(self) -> bool:
        """Bounded high/low discovery used only before the tune erase."""
        if self.destructive_started:
            return False
        if self.authorization_state_requires_cycle or (
            self.authorization_may_be_active and not self.write_authorized
        ):
            self._record(
                "partial_pre_erase_recovery_blocked_by_authorization_state",
                authorization_may_be_active=self.authorization_may_be_active,
            )
            return False
        if (
            self.link is LinkRate.LOW
            and self.identity is not None
            and not self.write_authorized
            and not self.fast_write_armed
        ):
            self._wait_for_low_identity(
                contract_name="partial pre-authorization low readiness",
                label="partial_pre_authorization_low_readiness",
                timeout_event="partial_pre_authorization_low_readiness_timeout",
            )
            self._set_state(
                state=SessionState.LOW_READY,
                link=LinkRate.LOW,
                reason="normal low identity confirmed before authorization",
            )
            self.safe_legacy_fallback = True
            self._record(
                "partial_pre_authorization_low_fallback_confirmed",
                final_baud=self.rates.low,
            )
            return True
        if self.token is None:
            self.safe_legacy_fallback = False
            return False

        found = None
        for candidate in (LinkRate.HIGH, LinkRate.LOW):
            self.transport.set_baud(
                self.rates.for_link(candidate),
                reason=f"partial pre-erase recovery probe {candidate.name.lower()}",
            )
            self.link = candidate
            try:
                actual = self._read_mem(
                    TOKEN_ADDRESS,
                    TOKEN_LENGTH,
                    label=f"partial_pre_erase_recovery_{candidate.name.lower()}_token",
                )
            except Exception:
                continue
            if actual == self._require_token():
                found = candidate
                break
        if found is None:
            self.link = LinkRate.UNKNOWN
            return False

        if found is LinkRate.LOW:
            self._wait_for_low_identity(
                contract_name="partial pre-erase low readiness",
                label="partial_pre_erase_low_readiness",
                timeout_event="partial_pre_erase_low_readiness_timeout",
            )
            if self.state is SessionState.ARMED_LOW:
                self.fast_write_armed = False
            self._set_state(
                state=SessionState.LOW_READY,
                link=LinkRate.LOW,
                reason="normal low identity confirmed before tune erase",
            )
            self.safe_legacy_fallback = True
            self._record(
                "partial_pre_erase_low_fallback_confirmed",
                final_baud=self.rates.low,
            )
            return True

        # The high-rate write state requires the proven selector/BMW cleanup.
        # No destructive command has been sent.
        self.state = SessionState.HIGH_PARTIAL_WRITE
        cleanup_confirmed = self._cleanup_to_low()
        if not cleanup_confirmed:
            raise PartialWriteTimeout(
                "pre-erase cleanup did not confirm normal low DS2 identity"
            )
        self.state = SessionState.LOW_READY
        self.safe_legacy_fallback = True
        self._record("partial_pre_erase_recovery_confirmed", discovered_link=found.name.lower())
        return True
