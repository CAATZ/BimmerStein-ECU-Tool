"""Production read-only D2XX session for native-fast DS2 on stock MS41 ECUs.

Its command allowlist contains only IDENTIFY, READ_MEM, and the captured
command-0x90 selector/cleanup exchanges. It cannot prepare, authorize, erase,
or program an ECU.
"""

from __future__ import annotations

import time
from collections import Counter
from dataclasses import dataclass
from typing import Callable, Mapping, Optional, Sequence, Tuple

from ds2_fast_contracts import (
    ContractViolation,
    DS2Response,
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
)
from ds2_fast_plans import (
    FULL_IMAGE_SIZE,
    TUNE_SIZE,
    ReadPlan,
    ReadRequest,
    assemble_read_pass,
    build_fast_full_read_plan,
    build_fast_partial_read_plan,
    ds2_image_to_file_layout,
)


IDENTIFY_COMMAND = 0x00
READ_MEMORY_COMMAND = 0x06
SELECTOR_COMMAND = 0x90

SELECTOR_LOW = 0x26
SELECTOR_MID = 0x12
SELECTOR_HIGH = 0x01
SELECTORS = frozenset((SELECTOR_LOW, SELECTOR_MID, SELECTOR_HIGH))

TOKEN_ADDRESS = 0x205E
TOKEN_LENGTH = 10
TOKEN_READ_A_ADDRESS = 0x2040
TOKEN_READ_A_LENGTH = 32
TOKEN_READ_B_ADDRESS = 0x2060
TOKEN_READ_B_LENGTH = 18

IDENTITY_LENGTH = 42
BITS_PER_CHARACTER_8E2 = 12

EventCallback = Callable[[str, Mapping[str, object]], None]
ProgressCallback = Callable[[int, int, str], None]


class FastReadError(RuntimeError):
    """Base class for the standalone read-only service."""


class UnsafeReadOnlyCommand(FastReadError):
    """A caller attempted a command outside the hard read-only allowlist."""


class FastReadTimeout(FastReadError):
    """A complete echo or ECU response did not arrive in time."""


class FastReadStateError(FastReadError):
    """The requested action is illegal in the current rate/session state."""


@dataclass(frozen=True)
class RateProfile:
    name: str
    low: int
    mid: int
    high: int
    direct_low_to_high: bool = False

    def for_link(self, link: LinkRate) -> int:
        if link is LinkRate.LOW:
            return self.low
        if link is LinkRate.MID:
            return self.mid
        if link is LinkRate.HIGH:
            return self.high
        raise ValueError("UNKNOWN does not have a host baud setting")


CAPTURED_RATE_PROFILE = RateProfile(
    "captured",
    low=9_600,
    mid=19_200,
    high=192_000,
    direct_low_to_high=False,
)

# Production uses the standard 9,600 host setting for low-rate DS2 and the
# C166 divisor-derived 187,500 bit/s value for direct high-rate transfer.
PRODUCTION_RATE_PROFILE = RateProfile(
    "direct_exact_high",
    low=9_600,
    mid=19_737,
    high=187_500,
    direct_low_to_high=True,
)


@dataclass(frozen=True)
class FastPartialReadResult:
    data: bytes
    identity: bytes
    final_link: LinkRate
    rate_profile: RateProfile
    request_count: int
    recovery_used: bool


@dataclass(frozen=True)
class FastFullReadResult:
    """Result of one production full-ROM read pass."""

    file_image: bytes
    ds2_image: bytes
    identity: bytes
    readable_bytes: int
    final_link: LinkRate
    rate_profile: RateProfile
    request_count: int
    recovery_used: bool


class NativeFastReadTransport:
    """One-request-at-a-time DS2 transport over an injected serial-like object."""

    _ALLOWED_COMMANDS = frozenset(
        (IDENTIFY_COMMAND, READ_MEMORY_COMMAND, SELECTOR_COMMAND)
    )

    def __init__(
        self,
        serial_port,
        *,
        baud: int = PRODUCTION_RATE_PROFILE.low,
        echo: bool = True,
        first_byte_timeout: float = 1.5,
        inter_byte_timeout: float = 0.6,
        event_cb: Optional[EventCallback] = None,
    ):
        self.serial = serial_port
        self.baud = int(baud)
        self.echo = bool(echo)
        self.first_byte_timeout = float(first_byte_timeout)
        self.inter_byte_timeout = float(inter_byte_timeout)
        self.event_cb = event_cb
        self._request_active = False
        self.command_counts: Counter[int] = Counter()
        self.serial.baudrate = self.baud

    @classmethod
    def open_d2xx(
        cls,
        port: str,
        *,
        baud: int = PRODUCTION_RATE_PROFILE.low,
        echo: bool = True,
        first_byte_timeout: float = 1.5,
        inter_byte_timeout: float = 0.6,
        event_cb: Optional[EventCallback] = None,
        serial_factory=None,
    ) -> "NativeFastReadTransport":
        """Open only the FTDI D2XX adapter associated with the requested COM name."""

        if serial_factory is None:
            from engines.softbsl.d2xx_serial import D2XXSerial

            serial_factory = D2XXSerial
        serial_port = serial_factory(
            port=port,
            baudrate=baud,
            timeout=first_byte_timeout,
            write_timeout=3.0,
            two_stop=True,
        )
        try:
            serial_port.setDTR(False)
            serial_port.setRTS(False)
            return cls(
                serial_port,
                baud=baud,
                echo=echo,
                first_byte_timeout=first_byte_timeout,
                inter_byte_timeout=inter_byte_timeout,
                event_cb=event_cb,
            )
        except Exception:
            serial_port.close()
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

    def set_baud(self, baud: int, *, reason: str) -> None:
        old = self.baud
        self.serial.baudrate = int(baud)
        self.baud = int(baud)
        self._emit("host_baud_changed", old=old, new=self.baud, reason=reason)

    def _read_exact(self, length: int, timeout: float) -> bytes:
        self.serial.timeout = float(timeout)
        data = bytearray()
        while len(data) < length:
            part = self.serial.read(length - len(data))
            if not part:
                break
            data.extend(part)
        return bytes(data)

    @staticmethod
    def _validate_allowlist(command: int, args: bytes) -> None:
        if command not in NativeFastReadTransport._ALLOWED_COMMANDS:
            raise UnsafeReadOnlyCommand(
                f"command 0x{command:02X} is not allowed by the read-only transport"
            )
        if command == IDENTIFY_COMMAND and args:
            raise UnsafeReadOnlyCommand("IDENTIFY cannot contain arguments")
        if command == READ_MEMORY_COMMAND:
            if len(args) != 5 or not 1 <= args[4] <= 247:
                raise UnsafeReadOnlyCommand(
                    "READ_MEM requires a four-byte address and count 1..247"
                )
        if command == SELECTOR_COMMAND:
            valid_selector = (
                len(args) == TOKEN_LENGTH + 1 and args[0] in SELECTORS
            )
            valid_cleanup = args == b"BMW"
            if not (valid_selector or valid_cleanup):
                raise UnsafeReadOnlyCommand(
                    "read-only command 0x90 permits only selector+token or bare BMW"
                )

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
        """Send one allowlisted request, consume exact echo, and validate its reply."""

        args = bytes(args)
        self._validate_allowlist(command, args)
        if self._request_active:
            raise FastReadStateError("only one DS2 request may be outstanding")
        if not self.is_open:
            raise FastReadStateError("D2XX transport is closed")

        frame = encode_ds2_frame(command, args)
        self._request_active = True
        started = time.monotonic()
        echo = b""
        raw = b""
        self._emit(
            "request_started",
            label=label,
            baud=self.baud,
            command=f"0x{command:02X}",
            length=len(frame),
        )
        try:
            self.serial.reset_input_buffer()
            written = self.serial.write(frame)
            if written != len(frame):
                raise FastReadError(
                    f"short D2XX write for {label}: {written}/{len(frame)} bytes"
                )
            self.serial.flush()

            if self.echo:
                echo_timeout = max(
                    0.05,
                    len(frame) * BITS_PER_CHARACTER_8E2 / self.baud + 0.05,
                )
                echo = self._read_exact(len(frame), echo_timeout)
                if echo != frame:
                    raise FastReadError(
                        f"K-Line echo mismatch for {label}: expected "
                        f"{frame.hex(' ')}, got {echo.hex(' ')}"
                    )

            start_timeout = (
                self.first_byte_timeout
                if first_byte_timeout is None
                else float(first_byte_timeout)
            )
            head = self._read_exact(2, start_timeout)
            if len(head) != 2:
                raise FastReadTimeout(
                    f"no complete response header for {label} at {self.baud} baud"
                )
            if head[1] < 4 or head[1] > 0xFC:
                raise FrameValidationError(
                    f"implausible DS2 response length {head[1]} for {label}"
                )
            rest = self._read_exact(head[1] - 2, self.inter_byte_timeout)
            raw = head + rest
            if len(raw) != head[1]:
                raise FastReadTimeout(
                    f"short response for {label}: expected {head[1]}, got {len(raw)}"
                )
            response = decode_ds2_response(
                raw,
                rate=rate,
                state=state,
                label=label,
            )
            contract.validate(response)
            self.command_counts[command] += 1
            self._emit(
                "request_completed",
                label=label,
                baud=self.baud,
                status=f"0x{response.status:02X}",
                response_length=len(raw),
                duration_s=round(time.monotonic() - started, 6),
            )
            return response
        except Exception as error:
            self._emit(
                "request_failed",
                label=label,
                baud=self.baud,
                echo_length=len(echo),
                response_length=len(raw),
                error=f"{type(error).__name__}: {error}",
                duration_s=round(time.monotonic() - started, 6),
            )
            raise
        finally:
            self._request_active = False


class NativeFastReadSession:
    """Explicit, one-shot read state machine for an unmodified stock ECU."""

    def __init__(
        self,
        transport: NativeFastReadTransport,
        *,
        rates: RateProfile = PRODUCTION_RATE_PROFILE,
        post_cleanup_delay: float = 0.25,
        post_cleanup_timeout: float = 15.0,
        post_cleanup_poll: float = 1.0,
        stability_probes: int = 3,
        progress_cb: Optional[ProgressCallback] = None,
        event_cb: Optional[EventCallback] = None,
        sleeper: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ):
        if transport.baud != rates.low:
            raise FastReadStateError(
                f"transport starts at {transport.baud}, expected low rate {rates.low}"
            )
        self.transport = transport
        self.rates = rates
        self.post_cleanup_delay = max(0.0, float(post_cleanup_delay))
        self.post_cleanup_timeout = max(0.1, float(post_cleanup_timeout))
        self.post_cleanup_poll = max(0.0, float(post_cleanup_poll))
        self.stability_probes = max(1, int(stability_probes))
        self.progress_cb = progress_cb
        self.event_cb = event_cb
        self._sleep = sleeper
        self._monotonic = monotonic
        self.link = LinkRate.LOW
        self.state = SessionState.LOW_READY
        self.identity: Optional[bytes] = None
        self.token: Optional[bytes] = None
        self.cleanup_b0_seen = False
        self.recovery_used = False

    def _emit(self, event: str, **fields: object) -> None:
        if self.event_cb is not None:
            self.event_cb(event, fields)

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
        self._emit(
            "session_state_changed",
            old_state=old_state.value,
            new_state=self.state.value,
            old_link=old_link.name.lower(),
            new_link=self.link.name.lower(),
            reason=reason,
        )

    def _request(
        self,
        command: int,
        args: bytes,
        contract: StatusResponseContract,
        label: str,
        *,
        first_byte_timeout: Optional[float] = None,
    ) -> DS2Response:
        return self.transport.request(
            command,
            args,
            contract=contract,
            label=label,
            rate=self.link,
            state=self.state,
            first_byte_timeout=first_byte_timeout,
        )

    def _identify(self, *, label: str) -> bytes:
        response = self._request(
            IDENTIFY_COMMAND,
            b"",
            StatusResponseContract(
                label,
                frozenset((ResponseStatus.ACK,)),
                exact_payload_length=IDENTITY_LENGTH,
            ),
            label,
        )
        return response.payload

    def _read_mem(self, request: ReadRequest, *, label: str) -> bytes:
        response = self._request(
            READ_MEMORY_COMMAND,
            request.payload,
            read_response_contract(request.count),
            label,
        )
        return response.payload

    def _discover_token(self) -> bytes:
        first = self._read_mem(
            ReadRequest(TOKEN_READ_A_ADDRESS, TOKEN_READ_A_LENGTH),
            label="token_window_0x2040",
        )
        second = self._read_mem(
            ReadRequest(TOKEN_READ_B_ADDRESS, TOKEN_READ_B_LENGTH),
            label="token_window_0x2060",
        )
        offset = TOKEN_ADDRESS - TOKEN_READ_A_ADDRESS
        token = first[offset:] + second[: TOKEN_LENGTH - (len(first) - offset)]
        if len(token) != TOKEN_LENGTH:
            raise FastReadError(
                f"token extraction produced {len(token)} bytes, expected {TOKEN_LENGTH}"
            )
        self.token = token
        self._emit(
            "token_discovered",
            address=f"0x{TOKEN_ADDRESS:05X}",
            length=len(token),
        )
        return token

    def _require_token(self) -> bytes:
        if self.token is None:
            raise FastReadStateError("ECU token is unavailable")
        return self.token

    def _begin(self) -> None:
        if self.state is not SessionState.LOW_READY or self.link is not LinkRate.LOW:
            raise FastReadStateError("read session must begin in LOW_READY")
        self.identity = self._identify(label="initial_identify")
        self._discover_token()
        self._set_state(
            state=SessionState.TOKEN_KNOWN,
            link=LinkRate.LOW,
            reason="identity and session token validated",
        )

    def _liveness(self, target: LinkRate) -> None:
        token = self._require_token()
        actual = self._read_mem(
            ReadRequest(TOKEN_ADDRESS, TOKEN_LENGTH),
            label=f"{target.name.lower()}_token_liveness",
        )
        if actual != token:
            raise FastReadError(
                f"{target.name.lower()}-rate liveness differs from the low-rate token"
            )

    def _switch_up(
        self,
        *,
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
            raise FastReadStateError(
                f"disallowed escalation {old.name} --0x{selector:02X}--> {target.name}"
            )

        self._request(
            SELECTOR_COMMAND,
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
        try:
            self.link = target
            self._liveness(target)
        except Exception:
            self._set_state(
                state=SessionState.FAILED,
                link=LinkRate.UNKNOWN,
                reason=f"{target.name.lower()} liveness failed",
            )
            raise
        self._set_state(
            state=target_state,
            link=target,
            reason=f"{target.name.lower()} liveness validated",
        )

    def _enter_high(self) -> None:
        if self.state is not SessionState.TOKEN_KNOWN:
            raise FastReadStateError("high-rate entry requires TOKEN_KNOWN")
        self.cleanup_b0_seen = False
        if not self.rates.direct_low_to_high:
            self._switch_up(
                selector=SELECTOR_MID,
                target=LinkRate.MID,
                target_state=SessionState.MID,
            )
        self._switch_up(
            selector=SELECTOR_HIGH,
            target=LinkRate.HIGH,
            target_state=SessionState.HIGH_READ,
        )
        # _switch_up already performs the first exact token liveness read.  Two
        # more probes give the production dump path a sub-second stability gate
        # before committing to a large transfer.
        for _probe in range(2, self.stability_probes + 1):
            self._liveness(LinkRate.HIGH)
        self._emit(
            "high_rate_stability_validated",
            baud=self.rates.high,
            probes=self.stability_probes,
        )

    def _wait_for_low_ready(self) -> bytes:
        deadline = self._monotonic() + self.post_cleanup_timeout
        attempt = 0
        last_timeout: Optional[Exception] = None
        while self._monotonic() < deadline:
            attempt += 1
            try:
                response = self._request(
                    IDENTIFY_COMMAND,
                    b"",
                    StatusResponseContract(
                        "post-cleanup identity/readiness",
                        frozenset(
                            (ResponseStatus.ACK, ResponseStatus.READINESS_A2)
                        ),
                    ),
                    f"post_cleanup_identify_{attempt}",
                )
            except FastReadTimeout as error:
                last_timeout = error
                self._emit(
                    "post_cleanup_not_ready",
                    attempt=attempt,
                    result="timeout",
                )
            else:
                if response.status == ResponseStatus.ACK:
                    if len(response.payload) != IDENTITY_LENGTH:
                        raise ContractViolation(
                            "post-cleanup A0 identity has an unexpected length"
                        )
                    if self.identity is not None and response.payload != self.identity:
                        raise ContractViolation(
                            "post-cleanup identity differs from the initial identity"
                        )
                    return response.payload
                if response.status != ResponseStatus.READINESS_A2 or response.payload:
                    raise ContractViolation(
                        "invalid transitional post-cleanup A2 response"
                    )
                self._emit(
                    "post_cleanup_not_ready",
                    attempt=attempt,
                    result="status_0xA2",
                )
            remaining = deadline - self._monotonic()
            if remaining > 0:
                self._sleep(min(self.post_cleanup_poll, remaining))
        detail = f": {last_timeout}" if last_timeout else ""
        raise FastReadTimeout(
            f"normal low-rate DS2 did not return within "
            f"{self.post_cleanup_timeout:.1f}s{detail}"
        )

    def _cleanup_to_low(self) -> None:
        if self.link not in (LinkRate.HIGH, LinkRate.MID):
            raise FastReadStateError(
                f"cleanup requires a known high or mid link, got {self.link.name}"
            )
        old = self.link
        self._request(
            SELECTOR_COMMAND,
            bytes((SELECTOR_LOW,)) + self._require_token(),
            selector_ack_contract(),
            f"selector_0x26_{old.name.lower()}_to_low",
        )
        self._set_state(
            state=SessionState.LOW_RECOVERY,
            link=LinkRate.UNKNOWN,
            reason="selector 0x26 ACK completed at old rate",
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
            SELECTOR_COMMAND,
            b"BMW",
            contextual_recovery_contract(
                ResponseStatus.CONTEXT_B0,
                exact_payload_length=0,
                name="bare BMW fast-state cleanup",
            ),
            "bare_BMW_fast_state_cleanup_expected_B0",
        )
        self.cleanup_b0_seen = True
        self._sleep(self.post_cleanup_delay)
        self._wait_for_low_ready()
        self._set_state(
            state=SessionState.LOW_READY,
            link=LinkRate.LOW,
            reason="B0 cleanup and normal low identity validated",
        )

    def recover_read_only_to_low(self) -> bool:
        """Probe known rates and apply only the proven read-side cleanup."""

        if self.token is None:
            return False
        self.recovery_used = True
        self._set_state(
            state=SessionState.FAILED,
            link=LinkRate.UNKNOWN,
            reason="bounded read-only recovery started",
        )
        found: Optional[LinkRate] = None
        candidates = (
            (LinkRate.HIGH, LinkRate.LOW)
            if self.rates.direct_low_to_high
            else (LinkRate.HIGH, LinkRate.MID, LinkRate.LOW)
        )
        for candidate in candidates:
            self.transport.set_baud(
                self.rates.for_link(candidate),
                reason=f"recovery probe {candidate.name.lower()}",
            )
            self.link = candidate
            try:
                self._liveness(candidate)
            except Exception as error:
                self._emit(
                    "recovery_probe_failed",
                    candidate=candidate.name.lower(),
                    error=f"{type(error).__name__}: {error}",
                )
                continue
            found = candidate
            break

        if found is None:
            self.link = LinkRate.UNKNOWN
            return False
        try:
            if found in (LinkRate.HIGH, LinkRate.MID):
                self.state = (
                    SessionState.HIGH_READ
                    if found is LinkRate.HIGH
                    else SessionState.MID
                )
                self._cleanup_to_low()
                return True

            self.state = SessionState.LOW_RECOVERY
            if not self.cleanup_b0_seen:
                self._request(
                    SELECTOR_COMMAND,
                    b"BMW",
                    contextual_recovery_contract(
                        ResponseStatus.CONTEXT_B0,
                        exact_payload_length=0,
                        name="recovery bare BMW cleanup",
                    ),
                    "recovery_bare_BMW_expected_B0",
                )
                self.cleanup_b0_seen = True
            self._sleep(self.post_cleanup_delay)
            self._wait_for_low_ready()
            self._set_state(
                state=SessionState.LOW_READY,
                link=LinkRate.LOW,
                reason="bounded read-only recovery validated low",
            )
            return True
        except Exception as error:
            self._emit(
                "recovery_failed",
                error=f"{type(error).__name__}: {error}",
            )
            self.link = LinkRate.UNKNOWN
            return False

    def _execute_read_requests(
        self,
        requests: Sequence[ReadRequest],
        *,
        phase: str,
        progress_base: int,
        progress_total: int,
    ) -> Tuple[bytes, ...]:
        payloads = []
        done = progress_base
        for request in requests:
            payloads.append(
                self._read_mem(
                    request,
                    label=f"{phase}_0x{request.address:05X}_{request.count}",
                )
            )
            done += request.count
            if self.progress_cb is not None:
                self.progress_cb(done, progress_total, phase)
        return tuple(payloads)

    def _finish_success(self) -> None:
        self._set_state(
            state=SessionState.COMPLETE,
            link=LinkRate.LOW,
            reason="read operation completed at confirmed low rate",
        )

    def _recover_after_failure(self) -> None:
        if self.link is LinkRate.LOW and self.state is SessionState.LOW_READY:
            return
        recovered = self.recover_read_only_to_low()
        self._emit("automatic_read_recovery", recovered=recovered)

    def read_partial(self) -> FastPartialReadResult:
        """Read exactly the 24 KiB tune and finish at confirmed low rate."""

        try:
            self._begin()
            self._enter_high()
            plan = build_fast_partial_read_plan()
            payloads = self._execute_read_requests(
                plan.passes[0],
                phase="fast_partial_read",
                progress_base=0,
                progress_total=TUNE_SIZE,
            )
            data = b"".join(payloads)
            if len(data) != TUNE_SIZE:
                raise FastReadError(
                    f"partial read assembled {len(data)} bytes, expected {TUNE_SIZE}"
                )
            self._cleanup_to_low()
            self._finish_success()
        except Exception:
            self._recover_after_failure()
            raise

        return FastPartialReadResult(
            data=data,
            identity=bytes(self.identity or b""),
            final_link=self.link,
            rate_profile=self.rates,
            request_count=sum(self.transport.command_counts.values()),
            recovery_used=self.recovery_used,
        )

    def read_full(self) -> FastFullReadResult:
        """Read the accessible 240 KiB once and return normal 256 KiB layout."""

        try:
            self._begin()
            self._enter_high()
            plan = build_fast_full_read_plan(pass_count=1)
            ds2_image = self._execute_full_pass(plan, 0)
            file_image = ds2_image_to_file_layout(ds2_image)
            if len(file_image) != FULL_IMAGE_SIZE:
                raise FastReadError("full read did not produce a 256 KiB image")
            self._cleanup_to_low()
            self._finish_success()
        except Exception:
            self._recover_after_failure()
            raise

        return FastFullReadResult(
            file_image=file_image,
            ds2_image=ds2_image,
            identity=bytes(self.identity or b""),
            readable_bytes=plan.bytes_per_pass,
            final_link=self.link,
            rate_profile=self.rates,
            request_count=sum(self.transport.command_counts.values()),
            recovery_used=self.recovery_used,
        )

    def _execute_full_pass(
        self,
        plan: ReadPlan,
        pass_index: int,
    ) -> bytes:
        requests = plan.passes[pass_index]
        base = pass_index * plan.bytes_per_pass
        payloads = self._execute_read_requests(
            requests,
            phase=f"fast_full_read_pass_{pass_index + 1}",
            progress_base=base,
            progress_total=plan.bytes_per_pass * len(plan.passes),
        )
        return assemble_read_pass(requests, payloads)

def read_partial_d2xx(
    port: str,
    *,
    progress_cb: Optional[ProgressCallback] = None,
    event_cb: Optional[EventCallback] = None,
    echo: bool = True,
) -> FastPartialReadResult:
    """Run one standalone read-only partial operation through D2XX."""

    transport = NativeFastReadTransport.open_d2xx(
        port,
        echo=echo,
        event_cb=event_cb,
    )
    try:
        session = NativeFastReadSession(
            transport,
            progress_cb=progress_cb,
            event_cb=event_cb,
        )
        return session.read_partial()
    finally:
        transport.close()


def read_full_d2xx(
    port: str,
    *,
    progress_cb: Optional[ProgressCallback] = None,
    event_cb: Optional[EventCallback] = None,
    echo: bool = True,
) -> FastFullReadResult:
    """Run the slim one-pass production full dump through D2XX."""

    transport = NativeFastReadTransport.open_d2xx(
        port,
        echo=echo,
        event_cb=event_cb,
    )
    try:
        session = NativeFastReadSession(
            transport,
            progress_cb=progress_cb,
            event_cb=event_cb,
        )
        return session.read_full()
    finally:
        transport.close()
