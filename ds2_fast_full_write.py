"""Native-fast full writable-image state machine for stock MS41 ECUs.

The stock ECU remains unmodified.  Entry is direct from normal 9600-host DS2
to the ECU-exact 187500 tier.  A token read proves the selected rate before
the program-array erase.  Once erase begins there is no automatic retry,
fallback, reset, cleanup, or port close; the caller must retain this object for
controlled recovery.  Successful full writes intentionally remain at high
rate and are followed by an operator ignition-cycle instruction.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

from ds2_fast_contracts import (
    ContractViolation,
    FastOperation,
    FlashOperation,
    FlashRequest,
    LinkRate,
    ResponseStatus,
    SessionState,
    StatusResponseContract,
)
from ds2_fast_partial_write import (
    BITS_PER_CHARACTER_8E2,
    NativeFastPartialWriteSession,
    NativeFastPartialWriteTransport,
    PartialWriteError,
    PREPARE_COMMAND,
    SEED_KEY_COMMAND,
    SELECTOR_HIGH,
    SELECTOR_LOW,
    STATUS_COMMAND,
    TOKEN_ADDRESS,
    TOKEN_LENGTH,
)
from ds2_fast_plans import (
    FINAL_POLL_ADDRESS,
    PROGRAM_CONTROL_ADDRESS,
    PROGRAM_HIGH_END,
    PROGRAM_HIGH_START,
    PROGRAM_LOW_END,
    PROGRAM_LOW_START,
    TUNE_ERASE_ADDRESS,
    TUNE_PRE_ERASE_POLL_ADDRESS,
    TUNE_START,
)
from ds2_write_authorization import (
    INITIAL_SEED_RETRY_DELAY,
    NATIVE_FAST_REENTRY_POLL_INTERVAL,
    NATIVE_FAST_REENTRY_TIMEOUT,
)


class FullWriteError(PartialWriteError):
    """A native full-write contract or state transition failed."""


class FullWriteFamilyError(FullWriteError):
    """The connected ECU and target flash families do not agree."""


@dataclass(frozen=True)
class FullWriteTiming:
    initial_seed_retry_delay: float = INITIAL_SEED_RETRY_DELAY
    native_fast_reentry_poll_interval: float = NATIVE_FAST_REENTRY_POLL_INTERVAL
    native_fast_reentry_timeout: float = NATIVE_FAST_REENTRY_TIMEOUT
    pre_arm_delay: float = 0.40
    post_selector_delay: float = 0.52
    poll_delay: float = 0.50
    post_program_erase_delay: float = 2.60
    between_program_requests: float = 0.015
    post_tune_erase_delay: float = 1.30
    post_tune_poll_delay: float = 0.23
    # Pre-erase recovery reuses the proven selector-0x26/BMW readiness loop.
    # Keep its timing on the full profile so this base never depends on a
    # partial timing object.
    post_cleanup_readiness_timeout: float = 15.0
    post_cleanup_poll_delay: float = 0.25


class NativeFastFullWriteTransport(NativeFastPartialWriteTransport):
    """Full-write command surface with strict capture-derived address gates."""

    _FLASH_MODE = FastOperation.FULL_WRITE

    @staticmethod
    def _validate_control(command: int, args: bytes, state: SessionState) -> None:
        try:
            NativeFastPartialWriteTransport._validate_control(command, args, state)
            return
        except Exception:
            # Full pre-erase recovery is the only extra control transition.
            if not (
                command == SEED_KEY_COMMAND
                and len(args) == TOKEN_LENGTH + 1
                and args[0] == SELECTOR_LOW
                and state == SessionState.HIGH_FULL_PROGRAM
            ):
                raise

    @staticmethod
    def _validate_flash(request: FlashRequest, state: SessionState) -> None:
        operation = int(request.operation)
        address = request.address
        end = address + request.count

        if operation == int(FlashOperation.POLL):
            allowed = {
                (PROGRAM_CONTROL_ADDRESS, SessionState.HIGH_FULL_PROGRAM),
                (TUNE_PRE_ERASE_POLL_ADDRESS, SessionState.HIGH_FULL_TUNE),
                (TUNE_ERASE_ADDRESS, SessionState.HIGH_FULL_TUNE),
                (FINAL_POLL_ADDRESS, SessionState.WRITE_FINALIZE_HIGH),
            }
            if request.count or (address, state) not in allowed:
                raise FullWriteError(
                    f"full poll 0x{address:06X} is invalid in state {state.value}"
                )
            return

        if operation == int(FlashOperation.ERASE):
            allowed = {
                (PROGRAM_CONTROL_ADDRESS, SessionState.HIGH_FULL_PROGRAM),
                (TUNE_ERASE_ADDRESS, SessionState.HIGH_FULL_TUNE),
            }
            if request.count or (address, state) not in allowed:
                raise FullWriteError(
                    f"full erase 0x{address:06X} is invalid in state {state.value}"
                )
            return

        if operation == int(FlashOperation.FULL_PROGRAM):
            if not request.count:
                raise FullWriteError("full program request cannot be empty")
            if state == SessionState.HIGH_FULL_PROGRAM:
                valid = (
                    PROGRAM_LOW_START <= address < end <= PROGRAM_LOW_END
                    or PROGRAM_HIGH_START <= address < end <= PROGRAM_HIGH_END
                )
            elif state == SessionState.HIGH_FULL_TUNE:
                valid = TUNE_START <= address < end <= TUNE_ERASE_ADDRESS + 0x10000
            else:
                valid = False
            if not valid:
                raise FullWriteError(
                    f"full program 0x{address:06X}..0x{end:06X} is invalid in "
                    f"state {state.value}"
                )
            return

        raise FullWriteError(f"operation 0x{operation:02X} is not valid for full write")


class NativeFastFullWriteSession(NativeFastPartialWriteSession):
    """Validated native-fast state transitions used by the slim full writer."""

    def _status(
        self,
        label: str,
        statuses=(ResponseStatus.ACK,),
        *,
        exact_payload_length: Optional[int] = None,
    ):
        return self._request(
            STATUS_COMMAND,
            b"",
            StatusResponseContract(
                label,
                frozenset(statuses),
                exact_payload_length=exact_payload_length,
            ),
            label,
        )

    def _arm_and_enter_high_full(self) -> None:
        self._sleep(self.timing.pre_arm_delay)
        arm = self._request(
            SEED_KEY_COMMAND,
            b"BMW",
            StatusResponseContract(
                "authorized full-write arm",
                frozenset((ResponseStatus.ACK,)),
                exact_payload_length=1,
            ),
            "bare_BMW_authorized_full_write_arm_expected_A0_00",
        )
        if arm.payload != b"\x00":
            raise ContractViolation("authorized full-write arm payload is not 00")
        self.fast_write_armed = True
        self._set_state(state=SessionState.ARMED_LOW, reason="full write armed")
        self._switch_up(SELECTOR_HIGH, LinkRate.HIGH, SessionState.HIGH_FULL_PROGRAM)

        actual = self._read_mem(
            TOKEN_ADDRESS,
            TOKEN_LENGTH,
            label="full_high_rate_pre_erase_token_liveness",
        )
        if actual != self._require_token():
            raise FullWriteError("full high-rate liveness differs from the low-rate token")
        self._record(
            "full_high_rate_pre_erase_liveness_validated",
            baud=self.rates.high,
        )
        self._sleep(self.timing.post_selector_delay)
        self._read_mem(0x1CF4, 3, label="full_high_preamble_0x1CF4")

    def _flash_full(self, request: FlashRequest, label: str, timeout: float = None):
        return self.transport.flash(
            request,
            label=label,
            rate=self.link,
            state=self.state,
            first_byte_timeout=timeout,
        )

    def _program_requests(
        self,
        requests: Tuple[FlashRequest, ...],
        phase: str,
        *,
        progress_label: Optional[str] = None,
        progress_base: int = 0,
        progress_total: Optional[int] = None,
    ) -> int:
        local_total = sum(request.count for request in requests)
        displayed_total = local_total if progress_total is None else progress_total
        done = 0
        for index, request in enumerate(requests, 1):
            self._flash_full(
                request,
                f"full_{phase}_{index:03d}_0x{request.address:06X}_{request.count}",
            )
            done += request.count
            self._progress(
                progress_label or phase,
                progress_base + done,
                displayed_total,
            )
            self._sleep(self.timing.between_program_requests)
        return done

    def _finalize_full(self) -> None:
        self._progress("Finalizing full ROM write", 0, 0)
        self._set_state(
            state=SessionState.WRITE_FINALIZE_HIGH,
            link=LinkRate.HIGH,
            reason="full programming complete; captured finalizer started",
        )
        self._request(
            PREPARE_COMMAND,
            b"",
            StatusResponseContract(
                "full finalize prepare",
                frozenset((ResponseStatus.READY_FF,)),
                exact_payload_length=0,
            ),
            "full_finalize_prepare_expected_FF",
        )
        self._read_mem(0x2001, 12, label="full_finalize_state_0x2001")
        self._status(
            "full_finalize_status_before_BMW_0A",
            statuses=(ResponseStatus.ACK,),
            exact_payload_length=69,
        )
        authorization = self._request(
            SEED_KEY_COMMAND,
            b"BMW\x0A",
            StatusResponseContract(
                "full finalize existing authorization",
                frozenset((ResponseStatus.ACK,)),
                exact_payload_length=1,
            ),
            "full_finalize_existing_authorization_probe_0x0A",
        )
        if authorization.payload != b"\x00":
            raise ContractViolation("full finalizer BMW/0A payload is not 00")
        self._status(
            "full_finalize_status_after_BMW_0A",
            exact_payload_length=69,
        )
        if self.plan is None:
            raise FullWriteError("full plan is unavailable during finalization")
        self._flash_full(self.plan.final_poll, "full_finalize_poll_0x001D07")
        self._record("full_finalize_completed", final_poll_status="0x01")

    def _cleanup_pre_erase_to_low(self) -> bool:
        """Use only the proven selector/BMW exit before any destructive request."""
        if self.destructive_started:
            return False
        if self.authorization_state_requires_cycle or (
            self.authorization_may_be_active and not self.write_authorized
        ):
            self._record(
                "full_pre_erase_cleanup_blocked_by_authorization_state",
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
                contract_name="full pre-authorization low readiness",
                label="full_pre_authorization_low_readiness",
                timeout_event="full_pre_authorization_low_readiness_timeout",
            )
            self._set_state(
                state=SessionState.LOW_READY,
                link=LinkRate.LOW,
                reason="normal low identity confirmed before authorization",
            )
            self.safe_legacy_fallback = True
            self._record(
                "full_pre_authorization_low_fallback_confirmed",
                final_baud=self.rates.low,
            )
            return True
        if self.token is None:
            self.safe_legacy_fallback = self.link is LinkRate.LOW
            return self.safe_legacy_fallback
        found = None
        for candidate in (LinkRate.HIGH, LinkRate.LOW):
            self.transport.set_baud(
                self.rates.for_link(candidate),
                reason=f"pre-erase recovery probe {candidate.name.lower()}",
            )
            self.link = candidate
            try:
                actual = self._read_mem(
                    TOKEN_ADDRESS,
                    TOKEN_LENGTH,
                    label=f"pre_erase_recovery_{candidate.name.lower()}_token",
                )
            except Exception:
                continue
            if actual == self._require_token():
                found = candidate
                break
        if found is None:
            self.link = LinkRate.UNKNOWN
            return False

        self.state = SessionState.HIGH_FULL_PROGRAM
        self.cleanup_attempted = True
        self._request(
            SEED_KEY_COMMAND,
            bytes((SELECTOR_LOW,)) + self._require_token(),
            StatusResponseContract(
                "pre-erase selector 0x26",
                frozenset((ResponseStatus.ACK,)),
                exact_payload_length=0,
            ),
            "full_pre_erase_selector_0x26",
        )
        guard = max(0.001, 2 * BITS_PER_CHARACTER_8E2 / self.rates.for_link(found))
        self._sleep(guard)
        self.transport.set_baud(self.rates.low, reason="pre-erase selector 0x26 ACKed")
        self.link = LinkRate.LOW
        cleanup = self._request(
            SEED_KEY_COMMAND,
            b"BMW",
            StatusResponseContract(
                "pre-erase full-write cleanup",
                frozenset((ResponseStatus.CONTEXT_B0,)),
                exact_payload_length=0,
            ),
            "full_pre_erase_bare_BMW_expected_B0",
        )
        if cleanup.status != ResponseStatus.CONTEXT_B0:
            return False
        self._wait_for_low_identity(
            contract_name="full pre-erase low readiness",
            label="full_pre_erase_low_readiness",
            timeout_event="full_pre_erase_low_readiness_timeout",
        )
        self._set_state(
            state=SessionState.LOW_READY,
            link=LinkRate.LOW,
            reason="full write recovered before erase; normal low identity confirmed",
        )
        self.safe_legacy_fallback = True
        return True
