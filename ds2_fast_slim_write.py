"""Slim production native-fast DS2 writers for stock MS41 ECUs.

The capture-qualified wire protocol stays intact.  Production policy is kept
small: no mandatory ECU backup or pre-read, sparse live target checks, and
readback only when the caller selected Verify.  A failure after erase still
retains the live high-rate session for a complete re-erase/write.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Tuple

import ecu_info
from ds2_fast_contracts import (
    CommitUnknownError,
    FastOperation,
    LinkRate,
    ResponseStatus,
    SessionState,
)
from ds2_fast_full_write import (
    FullWriteError,
    FullWriteFamilyError,
    FullWriteTiming,
    NativeFastFullWriteSession,
    NativeFastFullWriteTransport,
)
from ds2_fast_partial_write import (
    CAPTURED_PARTIAL_WRITE_TIMING,
    INITIAL_CHALLENGE,
    FINALIZE_CHALLENGE,
    NativeFastPartialWriteSession,
    NativeFastPartialWriteTransport,
    PartialWriteCancelled,
    PartialWriteError,
    PartialWriteReadbackMismatch,
    PartialWriteStateError,
    PartialWriteTiming,
    PRODUCTION_RATE_PROFILE,
    TOKEN_ADDRESS,
    TOKEN_LENGTH,
    TOKEN_READ_A_ADDRESS,
    TOKEN_READ_A_LENGTH,
    TOKEN_READ_B_ADDRESS,
    TOKEN_READ_B_LENGTH,
)
from ds2_fast_plans import (
    FULL_IMAGE_SIZE,
    PROGRAM_ERASE_TAIL_END,
    PROGRAM_ERASE_TAIL_START,
    PROGRAM_HIGH_END,
    PROGRAM_HIGH_START,
    PROGRAM_LOW_END,
    PROGRAM_LOW_START,
    TUNE_END,
    TUNE_SECTOR_END,
    TUNE_SIZE,
    TUNE_START,
    FullWritePlan,
    PartialWritePlan,
    build_fast_full_write_plan,
    build_fast_partial_write_plan,
)
from ds2_fast_read import RateProfile
from ds2_fast_safety import OperationJournal
from ms41 import (
    CODING_FAMILY_DS2_ADDR,
    FIRMWARE_COMPAT_PROGRAM_ADDRS,
    SS1V2_PROG_SIG_ADDR,
)


STABILITY_PROBES = 3


@dataclass(frozen=True)
class SlimPartialWriteResult:
    operation_id: str
    journal_path: Path
    program_blocks: int
    program_payload_bytes: int
    finalize_seed_attempts: int
    verified: bool
    verified_bytes: int
    final_link: LinkRate
    final_state: SessionState
    cleanup_attempted: bool
    power_cycle_recommended: bool = True


@dataclass(frozen=True)
class SlimFullWriteResult:
    operation_id: str
    journal_path: Path
    chip_family: str
    program_blocks: int
    tune_blocks: int
    payload_bytes: int
    verified: bool
    verified_bytes: int
    final_link: LinkRate
    final_state: SessionState
    cleanup_attempted: bool = False
    power_cycle_required: bool = True


class _SlimTokenMixin:
    """Token discovery/stability without backup binding or hashing."""

    def _discover_token(self) -> bytes:
        first = self._read_mem(
            TOKEN_READ_A_ADDRESS,
            TOKEN_READ_A_LENGTH,
            label="write_token_window_0x2040",
        )
        second = self._read_mem(
            TOKEN_READ_B_ADDRESS,
            TOKEN_READ_B_LENGTH,
            label="write_token_window_0x2060",
        )
        offset = TOKEN_ADDRESS - TOKEN_READ_A_ADDRESS
        token = first[offset:] + second[: TOKEN_LENGTH - (len(first) - offset)]
        if len(token) != TOKEN_LENGTH:
            raise PartialWriteError("token extraction did not produce ten bytes")
        self.token = token
        self._record("write_token_bound", address="0x0205E", length=len(token))
        return token

    def _high_rate_stability_check(self) -> None:
        # The arm/entry method already performs probe one.  Repeat the exact,
        # qualified token read twice before any erase.
        for probe in range(2, STABILITY_PROBES + 1):
            actual = self._read_mem(
                TOKEN_ADDRESS,
                TOKEN_LENGTH,
                label=f"high_rate_stability_probe_{probe}",
            )
            if actual != self._require_token():
                raise PartialWriteError(
                    f"high-rate stability probe {probe} returned a different token"
                )
        self._record(
            "high_rate_stability_validated",
            baud=self.rates.high,
            probes=STABILITY_PROBES,
        )


class SlimNativeFastPartialWriteSession(
    _SlimTokenMixin, NativeFastPartialWriteSession
):
    """One-pass, optional-verify tune writer used by the flasher GUI."""

    def __init__(
        self,
        transport: NativeFastPartialWriteTransport,
        target_tune: bytes,
        journal: OperationJournal,
        *,
        verify_write: bool,
        rates: RateProfile = PRODUCTION_RATE_PROFILE,
        timing: PartialWriteTiming = CAPTURED_PARTIAL_WRITE_TIMING,
        challenge: int = INITIAL_CHALLENGE,
        finalize_challenge: int = FINALIZE_CHALLENGE,
        reentry_required: bool = False,
        reentry_ready_cb: Optional[Callable[[], None]] = None,
        expected_ecu_id: Optional[str] = None,
        expected_program_compatibility_id: Optional[str] = None,
        expected_coding_family: Optional[str] = None,
        expected_coding_digit: Optional[str] = None,
        expected_program_signature_hex: Optional[str] = None,
        expected_driver_signature_hex: Optional[str] = None,
        cancel_cb: Optional[Callable[[], bool]] = None,
        progress_cb: Optional[Callable[[str, int, int], None]] = None,
        sleeper: Callable[[float], None] = time.sleep,
    ):
        if journal.operation != FastOperation.PARTIAL_WRITE.value:
            raise PartialWriteStateError("journal operation must be partial_write")
        if transport.baud != rates.low or not rates.direct_low_to_high:
            raise PartialWriteStateError(
                "slim partial writes require the direct rate profile"
            )
        target = bytes(target_tune)
        if len(target) != TUNE_SIZE:
            raise ValueError(
                f"partial target must be {TUNE_SIZE} bytes, got {len(target)}"
            )
        if expected_ecu_id is not None and (
            not isinstance(expected_ecu_id, str)
            or len(expected_ecu_id) != 7
            or any(ord(character) < 0x20 or ord(character) > 0x7E
                   for character in expected_ecu_id)
        ):
            raise ValueError("expected ECU ID must be exactly seven printable ASCII characters")
        if expected_program_compatibility_id is not None and (
            len(expected_program_compatibility_id) != 4
            or not expected_program_compatibility_id.isdigit()
        ):
            raise ValueError("expected program compatibility ID must be four ASCII digits")
        if expected_coding_family is not None and (
            len(expected_coding_family) != 3 or not expected_coding_family.isdigit()
        ):
            raise ValueError("expected coding family must be three ASCII digits")
        if expected_coding_digit is not None and (
            len(expected_coding_digit) != 1 or not expected_coding_digit.isdigit()
        ):
            raise ValueError("expected coding digit must be one ASCII digit")
        if expected_program_signature_hex is not None:
            try:
                expected_program_signature = bytes.fromhex(expected_program_signature_hex)
            except ValueError as error:
                raise ValueError("expected program signature must be eight hexadecimal digits") from error
            if len(expected_program_signature) != 4:
                raise ValueError("expected program signature must be eight hexadecimal digits")
        else:
            expected_program_signature = None
        if expected_driver_signature_hex is not None:
            if (
                not isinstance(expected_driver_signature_hex, str)
            ):
                raise ValueError("expected driver signature must be hexadecimal")
            try:
                expected_driver_signature = bytes.fromhex(
                    expected_driver_signature_hex
                )
            except ValueError as error:
                raise ValueError("expected driver signature must be hexadecimal") from error
            if (
                len(expected_driver_signature) != ecu_info.DRV_SIG_LEN
                or ecu_info.chip_family(expected_driver_signature) not in ("amd", "intel")
            ):
                raise ValueError("expected driver signature is unknown")
        else:
            expected_driver_signature = None
        self.transport = transport
        self.target_tune = target
        self.journal = journal
        self.rates = rates
        self.timing = timing
        self.challenge = challenge
        self.finalize_challenge = finalize_challenge
        self.reentry_required = bool(reentry_required)
        self.reentry_ready_cb = reentry_ready_cb
        self.expected_ecu_id = expected_ecu_id
        self.expected_program_compatibility_id = expected_program_compatibility_id
        self.expected_coding_family = expected_coding_family
        self.expected_coding_digit = expected_coding_digit
        self.expected_program_signature = expected_program_signature
        self.expected_driver_signature = expected_driver_signature
        self.progress_cb = progress_cb
        self.cancel_cb = cancel_cb
        self._sleep = sleeper
        self.link = LinkRate.LOW
        self.state = SessionState.LOW_READY
        self.identity = None
        self.token = None
        self.plan: Optional[PartialWritePlan] = None
        self.write_authorized = False
        self.authorization_may_be_active = False
        self.authorization_state_requires_cycle = False
        self.fast_write_armed = False
        self.destructive_started = False
        self.restore_verified = False
        self.flash_completed = False
        self.failure_state: Optional[SessionState] = None
        self.recovery_replay_attempted = False
        self.cleanup_attempted = False
        self.safe_legacy_fallback = False
        self.verify_write = bool(verify_write)

    @property
    def can_recover_in_place(self) -> bool:
        """Whether the failed ECU handler is still qualified for tune replay."""
        return bool(
            self.destructive_started
            and not self.flash_completed
            and self.failure_state is SessionState.HIGH_PARTIAL_WRITE
            and not self.recovery_replay_attempted
        )

    def _verify_requested_tune(self) -> int:
        actual = self._read_range(
            TUNE_START,
            TUNE_SIZE,
            phase="Verifying calibration region",
        )
        if actual != self.target_tune:
            index = next(
                i
                for i, (left, right) in enumerate(zip(actual, self.target_tune))
                if left != right
            )
            raise PartialWriteReadbackMismatch(
                TUNE_START + index,
                self.target_tune[index],
                actual[index],
            )
        self._record("partial_optional_verify_passed", bytes=len(actual))
        self.restore_verified = True
        return len(actual)

    def _result(
        self,
        blocks: int,
        payload_bytes: int,
        finalize_attempts: int,
        verified_bytes: int,
    ) -> SlimPartialWriteResult:
        return SlimPartialWriteResult(
            operation_id=self.journal.operation_id,
            journal_path=self.journal.path,
            program_blocks=blocks,
            program_payload_bytes=payload_bytes,
            finalize_seed_attempts=finalize_attempts,
            verified=self.verify_write,
            verified_bytes=verified_bytes,
            final_link=self.link,
            final_state=self.state,
            cleanup_attempted=self.cleanup_attempted,
        )

    def execute(self) -> SlimPartialWriteResult:
        try:
            self.identity = self._identify()
            if self.expected_ecu_id is not None:
                try:
                    actual_ecu_id = bytes(self.identity[:7]).decode("ascii")
                except UnicodeDecodeError as error:
                    raise PartialWriteStateError(
                        "live ECU identity is not printable ASCII"
                    ) from error
                if actual_ecu_id != self.expected_ecu_id:
                    raise PartialWriteStateError(
                        "live ECU identity mismatch: expected "
                        f"{self.expected_ecu_id}, received {actual_ecu_id}"
                    )
            if self.expected_program_compatibility_id is not None:
                expected = self.expected_program_compatibility_id.encode("ascii")
                for file_address in FIRMWARE_COMPAT_PROGRAM_ADDRS:
                    address = file_address ^ 0x4000
                    actual = self._read_mem(
                        address,
                        4,
                        label=f"live_program_compatibility_0x{address:05X}",
                    )
                    if actual != expected:
                        raise PartialWriteStateError(
                            "live program compatibility mismatch at "
                            f"0x{address:05X}: expected {expected.decode('ascii')}, "
                            f"received {actual.hex()}"
                        )
            if self.expected_coding_family is not None:
                actual = self._read_mem(
                    CODING_FAMILY_DS2_ADDR,
                    3,
                    label="live_coding_family_0x01CF4",
                )
                if actual != self.expected_coding_family.encode("ascii"):
                    raise PartialWriteStateError(
                        "live coding family mismatch: expected "
                        f"{self.expected_coding_family}, received {actual.hex()}"
                    )
            elif self.expected_coding_digit is not None:
                actual = self._read_mem(
                    CODING_FAMILY_DS2_ADDR,
                    3,
                    label="live_coding_family_0x01CF4",
                )
                if len(actual) != 3 or not actual.isdigit() or actual[2:3] != (
                    self.expected_coding_digit.encode("ascii")
                ):
                    raise PartialWriteStateError(
                        "live coding-family digit does not match target: expected "
                        f"{self.expected_coding_digit}, received {actual.hex()}"
                    )
            if self.expected_program_signature is not None:
                address = SS1V2_PROG_SIG_ADDR ^ 0x4000
                actual = self._read_mem(
                    address,
                    len(self.expected_program_signature),
                    label=f"live_program_signature_0x{address:05X}",
                )
                if actual != self.expected_program_signature:
                    raise PartialWriteStateError("live program signature does not match target")
            if self.expected_driver_signature is not None:
                actual = self._read_mem(
                    ecu_info.DRV_SIG_ADDR,
                    ecu_info.DRV_SIG_LEN,
                    label="live_driver_signature_0x0023C",
                )
                if actual != self.expected_driver_signature:
                    raise PartialWriteStateError(
                        "live flash-driver signature does not match target"
                    )
            self.plan = build_fast_partial_write_plan(
                self.target_tune,
                b"\xFF" * (TUNE_SECTOR_END - TUNE_END),
            )
            self._discover_token()
            self._set_state(
                state=SessionState.TOKEN_KNOWN,
                link=LinkRate.LOW,
                reason="identity, target size, and token validated",
            )
            self._authorize_once()
            self._arm_and_enter_high()
            self._high_rate_stability_check()
            self._cancel_checkpoint("before_tune_erase")
            blocks, payload_bytes = self._erase_and_program()
            finalize_attempts = self._finalize()
            self.flash_completed = True
            verified_bytes = (
                self._verify_requested_tune() if self.verify_write else 0
            )
            cleanup_confirmed = self._cleanup_to_low()
            result = self._result(
                blocks, payload_bytes, finalize_attempts, verified_bytes
            )
            self.journal.finish(
                "success" if cleanup_confirmed else "power_cycle_required",
                program_blocks=blocks,
                program_payload_bytes=payload_bytes,
                verify_requested=self.verify_write,
                verified_bytes=verified_bytes,
                final_link="low",
                cleanup_confirmed=cleanup_confirmed,
            )
            return result
        except Exception as error:
            self.failure_state = self.state
            if (
                self.destructive_started
                and getattr(error, "response_status", None)
                == int(ResponseStatus.READINESS_A2)
            ):
                self.recovery_replay_attempted = True
            commit_unknown = isinstance(error, CommitUnknownError)
            cancelled = isinstance(error, PartialWriteCancelled)
            recovered = False
            if not self.destructive_started:
                try:
                    recovered = self._recover_pre_erase_to_low()
                except Exception as cleanup_error:
                    self._record(
                        "partial_pre_erase_recovery_failed",
                        error=f"{type(cleanup_error).__name__}: {cleanup_error}",
                    )
            power_cycle_required = not recovered and (
                self.destructive_started
                or self.write_authorized
                or self.authorization_state_requires_cycle
                or self.authorization_may_be_active
            )
            if commit_unknown:
                self.state = SessionState.COMMIT_UNKNOWN
            elif recovered:
                self.state = SessionState.LOW_READY
            elif power_cycle_required:
                self.state = SessionState.POWER_CYCLE_REQUIRED
            else:
                self.state = SessionState.FAILED
            if not self.journal.closed:
                self.journal.finish(
                    "commit_unknown" if commit_unknown else "aborted" if cancelled else "failed",
                    error=f"{type(error).__name__}: {error}",
                    state=self.state.value,
                    link=self.link.name.lower(),
                    destructive_started=self.destructive_started,
                    safe_legacy_fallback=self.safe_legacy_fallback,
                    verify_requested=self.verify_write,
                    power_cycle_required=power_cycle_required,
                )
            raise

    def recover_in_place(self) -> SlimPartialWriteResult:
        if not self.destructive_started or self.plan is None:
            raise PartialWriteStateError("no destructive partial-write state is available")
        if not self.can_recover_in_place:
            raise PartialWriteStateError(
                "same-session partial-write replay is no longer qualified; the "
                "retained handler is not safe for another destructive retry"
            )
        if not self.transport.is_open:
            raise PartialWriteStateError("retained partial-write transport is closed")
        if self.journal.closed:
            raise PartialWriteStateError("recovery requires a new open recovery journal")
        self.transport.event_cb = self.journal.event_callback
        self.transport.set_baud(
            self.rates.high,
            reason="retained partial-write recovery resumes at high rate",
        )
        self.link = LinkRate.HIGH
        self.state = SessionState.HIGH_PARTIAL_WRITE
        self.cleanup_attempted = False
        self._record(
            "partial_write_recovery_started",
            strategy="same-session complete re-erase and rewrite",
        )
        try:
            self.recovery_replay_attempted = True
            blocks, payload_bytes = self._erase_and_program()
            finalize_attempts = self._finalize()
            self.flash_completed = True
            verified_bytes = (
                self._verify_requested_tune() if self.verify_write else 0
            )
            cleanup_confirmed = self._cleanup_to_low()
            result = self._result(
                blocks, payload_bytes, finalize_attempts, verified_bytes
            )
            self.journal.finish(
                "success" if cleanup_confirmed else "power_cycle_required",
                recovery=True,
                verify_requested=self.verify_write,
                verified_bytes=verified_bytes,
                final_link="low",
                cleanup_confirmed=cleanup_confirmed,
            )
            return result
        except Exception as error:
            self.failure_state = self.state
            self.fast_write_armed = False
            self.state = (
                SessionState.COMMIT_UNKNOWN
                if isinstance(error, CommitUnknownError)
                else SessionState.POWER_CYCLE_REQUIRED
            )
            if not self.journal.closed:
                self.journal.finish(
                    "commit_unknown"
                    if isinstance(error, CommitUnknownError)
                    else "power_cycle_required",
                    recovery=True,
                    error=f"{type(error).__name__}: {error}",
                    state=self.state.value,
                    link=self.link.name.lower(),
                    destructive_started=True,
                    retry_supported=False,
                    transport_retained=self.transport.is_open,
                    power_cycle_required=not isinstance(error, CommitUnknownError),
                )
            raise


class SlimNativeFastFullWriteSession(
    _SlimTokenMixin, NativeFastFullWriteSession
):
    """Full program+tune writer with optional single-pass affected-range verify."""

    def __init__(
        self,
        transport: NativeFastFullWriteTransport,
        target_file_image: bytes,
        journal: OperationJournal,
        *,
        connected_family: str,
        verify_write: bool,
        variant_conversion: bool = False,
        rates: RateProfile = PRODUCTION_RATE_PROFILE,
        timing: FullWriteTiming = FullWriteTiming(),
        challenge: int = INITIAL_CHALLENGE,
        reentry_required: bool = False,
        reentry_ready_cb: Optional[Callable[[], None]] = None,
        initial_identity_attempts: int = 1,
        expected_ecu_id: Optional[str] = None,
        expected_program_compatibility_id: Optional[str] = None,
        expected_coding_family: Optional[str] = None,
        expected_program_signature_hex: Optional[str] = None,
        expected_driver_signature_hex: Optional[str] = None,
        progress_cb: Optional[Callable[[str, int, int], None]] = None,
        sleeper: Callable[[float], None] = time.sleep,
    ):
        if journal.operation != FastOperation.FULL_WRITE.value:
            raise PartialWriteStateError("journal operation must be full_write")
        if transport.baud != rates.low or not rates.direct_low_to_high:
            raise PartialWriteStateError(
                "slim full writes require the direct rate profile"
            )
        target = bytes(target_file_image)
        if len(target) != FULL_IMAGE_SIZE:
            raise ValueError(
                f"full target must be {FULL_IMAGE_SIZE} bytes, got {len(target)}"
            )
        self.transport = transport
        self.target_file_image = target
        self.target_tune = b""
        self.journal = journal
        self.connected_family = str(connected_family).lower()
        self.rates = rates
        self.timing = timing
        self.challenge = challenge
        self.reentry_required = bool(reentry_required)
        self.reentry_ready_cb = reentry_ready_cb
        if initial_identity_attempts < 1:
            raise ValueError("initial identity attempts must be positive")
        if expected_ecu_id is not None and (
            len(expected_ecu_id) != 7
            or any(ord(character) < 0x20 or ord(character) > 0x7E
                   for character in expected_ecu_id)
        ):
            raise ValueError("expected ECU ID must be exactly seven printable ASCII characters")
        if expected_program_compatibility_id is not None and (
            len(expected_program_compatibility_id) != 4
            or not expected_program_compatibility_id.isdigit()
        ):
            raise ValueError("expected program compatibility ID must be four ASCII digits")
        if expected_coding_family is not None and (
            len(expected_coding_family) != 3 or not expected_coding_family.isdigit()
        ):
            raise ValueError("expected coding family must be three ASCII digits")
        try:
            expected_program_signature = (
                bytes.fromhex(expected_program_signature_hex)
                if expected_program_signature_hex is not None else None
            )
            expected_driver_signature = (
                bytes.fromhex(expected_driver_signature_hex)
                if expected_driver_signature_hex is not None else None
            )
        except ValueError as error:
            raise ValueError("invalid full-write target expectation") from error
        if expected_program_signature is not None and len(expected_program_signature) != 4:
            raise ValueError("expected program signature must be eight hexadecimal digits")
        if expected_driver_signature is not None and (
            len(expected_driver_signature) != ecu_info.DRV_SIG_LEN
            or ecu_info.chip_family(expected_driver_signature) not in ("amd", "intel")
        ):
            raise ValueError("expected driver signature is unknown")
        self.initial_identity_attempts = int(initial_identity_attempts)
        self.expected_ecu_id = expected_ecu_id
        self.expected_program_compatibility_id = expected_program_compatibility_id
        self.expected_coding_family = expected_coding_family
        self.expected_program_signature = expected_program_signature
        self.expected_driver_signature = expected_driver_signature
        self.progress_cb = progress_cb
        self.cancel_cb = None
        self._sleep = sleeper
        self.link = LinkRate.LOW
        self.state = SessionState.LOW_READY
        self.identity = None
        self.token = None
        self.plan: Optional[FullWritePlan] = None
        self.write_authorized = False
        self.authorization_may_be_active = False
        self.authorization_state_requires_cycle = False
        self.fast_write_armed = False
        self.destructive_started = False
        self.cleanup_attempted = False
        self.restore_verified = False
        self.flash_completed = False
        self.failure_state: Optional[SessionState] = None
        self.recovery_replay_attempted = False
        self.safe_legacy_fallback = False
        self.verify_write = bool(verify_write)
        self.variant_conversion = bool(variant_conversion)
        # Phase-1 bootstrap deployment uses the same native-fast full-write
        # control surface, but deliberately omits the tune-sector phase.  Keep
        # this explicit so retained-session recovery can never silently switch
        # a program-only operation into a full program+tune erase.
        self.program_only = False

    def _validate_live_identity(self) -> None:
        if self.expected_ecu_id is not None:
            try:
                actual_ecu_id = bytes(self.identity[:7]).decode("ascii")
            except UnicodeDecodeError as error:
                raise FullWriteError("live ECU identity is not printable ASCII") from error
            if actual_ecu_id != self.expected_ecu_id:
                raise FullWriteError(
                    "live ECU identity mismatch: expected "
                    f"{self.expected_ecu_id}, received {actual_ecu_id}"
                )
        if self.expected_program_compatibility_id is not None:
            expected = self.expected_program_compatibility_id.encode("ascii")
            for file_address in FIRMWARE_COMPAT_PROGRAM_ADDRS:
                address = file_address ^ 0x4000
                if self._read_mem(address, 4, label=f"live_program_compatibility_0x{address:05X}") != expected:
                    raise FullWriteError("live program compatibility does not match target")
        if self.expected_coding_family is not None and self._read_mem(
            CODING_FAMILY_DS2_ADDR, 3, label="live_coding_family_0x01CF4"
        ) != self.expected_coding_family.encode("ascii"):
            raise FullWriteError("live coding family does not match target")
        if self.expected_program_signature is not None:
            address = SS1V2_PROG_SIG_ADDR ^ 0x4000
            if self._read_mem(
                address,
                len(self.expected_program_signature),
                label=f"live_program_signature_0x{address:05X}",
            ) != self.expected_program_signature:
                raise FullWriteError("live program signature does not match target")
        if self.expected_driver_signature is not None and self._read_mem(
            ecu_info.DRV_SIG_ADDR,
            ecu_info.DRV_SIG_LEN,
            label="live_driver_signature_0x0023C",
        ) != self.expected_driver_signature:
            raise FullWriteError("live flash-driver signature does not match target")

    @property
    def can_recover_in_place(self) -> bool:
        """Whether the failed ECU handler is still qualified for full replay."""
        return bool(
            self.destructive_started
            and not self.flash_completed
            and self.failure_state in (
                SessionState.HIGH_FULL_PROGRAM,
                SessionState.HIGH_FULL_TUNE,
            )
            and not self.recovery_replay_attempted
        )

    def _validate_family(self) -> str:
        if self.connected_family not in ("amd", "intel"):
            raise FullWriteFamilyError("connected ECU flash-driver family is unknown")
        target_family = ecu_info.image_chip_family(self.target_file_image)
        if target_family != self.connected_family:
            raise FullWriteFamilyError(
                "live and target flash-driver families do not match: "
                f"live={self.connected_family}, target={target_family}"
            )
        self._record(
            "full_write_family_validated",
            chip_family=self.connected_family,
        )
        return self.connected_family

    def _verify_affected_ranges(self) -> int:
        if self.plan is None:
            raise FullWriteError("full plan is unavailable during verification")
        target = self.plan.effective_target_ds2
        ranges: Tuple[Tuple[int, int, bytes], ...] = (
            (
                PROGRAM_LOW_START,
                PROGRAM_ERASE_TAIL_END,
                target[PROGRAM_LOW_START:PROGRAM_LOW_END]
                + b"\xFF" * (PROGRAM_ERASE_TAIL_END - PROGRAM_ERASE_TAIL_START),
            ),
            (
                TUNE_START,
                TUNE_SECTOR_END,
                target[TUNE_START:TUNE_END]
                + b"\xFF" * (TUNE_SECTOR_END - TUNE_END),
            ),
            (
                PROGRAM_HIGH_START,
                PROGRAM_HIGH_END,
                target[PROGRAM_HIGH_START:PROGRAM_HIGH_END],
            ),
        )
        checked = 0
        for start, end, expected in ranges:
            actual = self._read_range(
                start,
                end - start,
                phase=f"full_optional_verify_{start:05X}",
            )
            if actual != expected:
                offset = next(
                    i
                    for i, (left, right) in enumerate(zip(actual, expected))
                    if left != right
                )
                raise FullWriteError(
                    f"full optional verify differs at DS2 0x{start + offset:06X}"
                )
            checked += len(actual)
        self.restore_verified = True
        self._record("full_optional_verify_passed", bytes=checked)
        return checked

    def _result(
        self,
        program_payload: int,
        tune_payload: int,
        verified_bytes: int,
        *,
        power_cycle_required: bool = True,
    ) -> SlimFullWriteResult:
        assert self.plan is not None
        return SlimFullWriteResult(
            operation_id=self.journal.operation_id,
            journal_path=self.journal.path,
            chip_family=self.connected_family,
            program_blocks=1 + len(self.plan.program),
            tune_blocks=0 if self.program_only else len(self.plan.tune),
            payload_bytes=program_payload + tune_payload,
            verified=self.verify_write,
            verified_bytes=verified_bytes,
            final_link=self.link,
            final_state=self.state,
            power_cycle_required=power_cycle_required,
        )

    def _program_program_only_plan(self) -> int:
        """Erase/program only the program array; never enter the tune phase."""
        assert self.plan is not None
        plan = self.plan
        self._progress("Preparing program erase", 0, 1)
        for index, request in enumerate(plan.program_polls, 1):
            self._flash_full(request, f"program_only_control_poll_{index}")
            self._sleep(self.timing.poll_delay)
        self._progress("Erasing program region", 0, 1)
        self.destructive_started = True
        self._record(
            "destructive_boundary_crossed",
            operation="program_only_array_erase",
            retry_policy="no automatic retry or baud fallback",
        )
        self._flash_full(plan.program_erase, "program_only_array_erase", 6.0)
        self._progress("Waiting for program erase to settle", 0, 1)
        self._sleep(self.timing.post_program_erase_delay)
        self._progress("Starting temporary hook flash", 0, 1)
        self._flash_full(plan.primer, "program_only_primer")
        return plan.primer.count + self._program_requests(
            plan.program, "program_only_array"
        )

    def _verify_program_only_ranges(self) -> int:
        """Optional high-rate readback for the two written program windows."""
        if self.plan is None:
            raise FullWriteError("program-only plan is unavailable during verification")
        target = self.plan.effective_target_ds2
        checked = 0
        for start, end in (
            (PROGRAM_LOW_START, PROGRAM_LOW_END),
            (PROGRAM_HIGH_START, PROGRAM_HIGH_END),
        ):
            actual = self._read_range(
                start,
                end - start,
                phase=f"program_only_optional_verify_{start:05X}",
            )
            expected = target[start:end]
            if actual != expected:
                offset = next(
                    i
                    for i, (left, right) in enumerate(zip(actual, expected))
                    if left != right
                )
                raise FullWriteError(
                    f"program-only optional verify differs at DS2 0x{start + offset:06X}"
                )
            checked += len(actual)
        self.restore_verified = True
        self._record("program_only_optional_verify_passed", bytes=checked)
        return checked

    def _program_full_plan(self) -> Tuple[int, int]:
        assert self.plan is not None
        plan = self.plan
        program_payload_total = plan.primer.count + sum(
            request.count for request in plan.program
        )
        tune_payload_total = sum(request.count for request in plan.tune)
        full_payload_total = program_payload_total + tune_payload_total
        self._progress("Erasing program region (phase 1 of 2)", 0, 1)
        for index, request in enumerate(plan.program_polls, 1):
            self._flash_full(request, f"full_program_control_poll_{index}")
            self._sleep(self.timing.poll_delay)
        self.destructive_started = True
        self._record(
            "destructive_boundary_crossed",
            operation="full_program_array_erase",
            retry_policy="no automatic retry or baud fallback",
        )
        self._flash_full(plan.program_erase, "full_program_array_erase", 6.0)
        self._sleep(self.timing.post_program_erase_delay)
        self._flash_full(plan.primer, "full_program_primer")
        self._progress(
            "Writing program region (phase 1 of 2)",
            plan.primer.count,
            full_payload_total,
        )
        program_payload = plan.primer.count + self._program_requests(
            plan.program,
            "program_array",
            progress_label="Writing program region (phase 1 of 2)",
            progress_base=plan.primer.count,
            progress_total=full_payload_total,
        )
        self._set_state(
            state=SessionState.HIGH_FULL_TUNE,
            link=LinkRate.HIGH,
            reason="program array complete; tune phase started",
        )
        midpoint_statuses = (
            frozenset((0x01, 0x0E))
            if self.variant_conversion
            else frozenset((0x01,))
        )
        for index, request in enumerate(plan.tune_polls_before, 1):
            reply = self._flash_full(
                request,
                f"full_tune_control_poll_{index}",
                allowed_statuses=midpoint_statuses,
            )
            if reply.status == 0x0E:
                self._record(
                    "conversion_midpoint_status_accepted",
                    status="0x0E",
                    address=f"0x{request.address:06X}",
                    scope="pre_tune_poll_only",
                )
            self._sleep(self.timing.poll_delay)
        self._progress("Erasing calibration region (phase 2 of 2)", 0, 0)
        self._flash_full(plan.tune_erase, "full_tune_sector_erase", 5.0)
        self._sleep(self.timing.post_tune_erase_delay)
        tune_payload = self._program_requests(
            plan.tune,
            "tune",
            progress_label="Writing calibration region (phase 2 of 2)",
            progress_base=program_payload,
            progress_total=full_payload_total,
        )
        for index, request in enumerate(plan.tune_polls_after, 1):
            self._flash_full(request, f"full_tune_post_poll_{index}")
            self._sleep(self.timing.post_tune_poll_delay)
        return program_payload, tune_payload

    def execute(self) -> SlimFullWriteResult:
        try:
            self._validate_family()
            self.identity = self._identify(
                attempts=self.initial_identity_attempts
            )
            self._validate_live_identity()
            self.plan = build_fast_full_write_plan(
                self.target_file_image,
                self.target_file_image,
            )
            self._discover_token()
            self._set_state(
                state=SessionState.TOKEN_KNOWN,
                link=LinkRate.LOW,
                reason="identity, family, target size, and token validated",
            )
            self._read_mem(0x1000C, 4, label="full_low_preamble_0x1000C")
            self._status("full_low_status_before_authorization")
            self._read_mem(0x1D07, 13, label="full_low_preamble_0x1D07")
            self._authorize_once()
            self._arm_and_enter_high_full()
            self._high_rate_stability_check()
            program_payload, tune_payload = self._program_full_plan()
            self._finalize_full()
            self.flash_completed = True
            verified_bytes = (
                self._verify_affected_ranges() if self.verify_write else 0
            )
            self._set_state(
                state=SessionState.POWER_CYCLE_REQUIRED,
                link=LinkRate.HIGH,
                reason="full write finalized; operator ignition cycle required",
            )
            result = self._result(program_payload, tune_payload, verified_bytes)
            self.journal.finish(
                "success",
                chip_family=self.connected_family,
                program_blocks=result.program_blocks,
                tune_blocks=result.tune_blocks,
                payload_bytes=result.payload_bytes,
                verify_requested=self.verify_write,
                verified_bytes=verified_bytes,
                final_link="high",
                cleanup_attempted=False,
                power_cycle_required=True,
            )
            return result
        except Exception as error:
            self.failure_state = self.state
            if (
                self.destructive_started
                and getattr(error, "response_status", None)
                == int(ResponseStatus.READINESS_A2)
            ):
                self.recovery_replay_attempted = True
            commit_unknown = isinstance(error, CommitUnknownError)
            recovered = False
            if not self.destructive_started:
                try:
                    recovered = self._cleanup_pre_erase_to_low()
                except Exception as cleanup_error:
                    self._record(
                        "pre_erase_cleanup_failed",
                        error=f"{type(cleanup_error).__name__}: {cleanup_error}",
                    )
            power_cycle_required = not recovered and (
                self.destructive_started
                or self.write_authorized
                or self.authorization_state_requires_cycle
                or self.authorization_may_be_active
            )
            if commit_unknown:
                self.state = SessionState.COMMIT_UNKNOWN
            elif power_cycle_required:
                self.state = SessionState.POWER_CYCLE_REQUIRED
            elif recovered:
                self.state = SessionState.LOW_READY
            else:
                self.state = SessionState.FAILED
            if not self.journal.closed:
                self.journal.finish(
                    "commit_unknown" if commit_unknown else "failed",
                    error=f"{type(error).__name__}: {error}",
                    state=self.state.value,
                    link=self.link.name.lower(),
                    destructive_started=self.destructive_started,
                    safe_legacy_fallback=self.safe_legacy_fallback,
                    verify_requested=self.verify_write,
                    power_cycle_required=power_cycle_required,
                )
            raise

    def execute_program_only(self) -> SlimFullWriteResult:
        """Deploy a 0x43/bootstrap program image without erasing the tune.

        This follows the same stock-ECU fast-write contract as ``execute``
        through the program finalizer. Stock full-program finalization leaves
        the ECU at the high-rate flash listener, so the result explicitly
        requires the operator ignition cycle; this method does not force or
        emulate that reboot.
        """
        self.program_only = True
        try:
            self._validate_family()
            self.identity = self._identify(
                attempts=self.initial_identity_attempts
            )
            self.plan = build_fast_full_write_plan(
                self.target_file_image,
                self.target_file_image,
                program_only=True,
            )
            self._discover_token()
            self._set_state(
                state=SessionState.TOKEN_KNOWN,
                link=LinkRate.LOW,
                reason="identity, family, target size, and token validated",
            )
            self._read_mem(0x1000C, 4, label="program_only_low_preamble_0x1000C")
            self._status("program_only_low_status_before_authorization")
            self._read_mem(0x1D07, 13, label="program_only_low_preamble_0x1D07")
            self._progress("Authorizing program write", 0, 1)
            self._authorize_once()
            self._progress("Entering high-rate write mode", 0, 1)
            self._arm_and_enter_high_full()
            self._progress("Checking high-rate write link", 0, 1)
            self._high_rate_stability_check()
            program_payload = self._program_program_only_plan()
            self._finalize_full()
            self.flash_completed = True
            verified_bytes = (
                self._verify_program_only_ranges() if self.verify_write else 0
            )
            # Stock full-program finalization leaves the ECU in its high-rate
            # flash listener.  Unlike the proven partial/read path, selector
            # 0x26 is not acknowledged with A0 here (the live ECU returns the
            # transitional A1/A2 states), so completion requires the operator
            # ignition cycle rather than an unqualified downshift attempt.
            self._set_state(
                state=SessionState.POWER_CYCLE_REQUIRED,
                link=LinkRate.HIGH,
                reason="program-only bootstrap finalized; operator ignition cycle required",
            )
            result = self._result(
                program_payload,
                0,
                verified_bytes,
                power_cycle_required=True,
            )
            self.journal.finish(
                "success",
                chip_family=self.connected_family,
                operation_scope="program_only",
                program_blocks=result.program_blocks,
                tune_blocks=0,
                payload_bytes=result.payload_bytes,
                verify_requested=self.verify_write,
                verified_bytes=verified_bytes,
                final_link=self.link.name.lower(),
                cleanup_attempted=False,
                cleanup_confirmed=False,
                power_cycle_required=True,
            )
            return result
        except Exception as error:
            self.failure_state = self.state
            if (
                self.destructive_started
                and getattr(error, "response_status", None)
                == int(ResponseStatus.READINESS_A2)
            ):
                self.recovery_replay_attempted = True
            commit_unknown = isinstance(error, CommitUnknownError)
            recovered = False
            if not self.destructive_started:
                try:
                    recovered = self._cleanup_pre_erase_to_low()
                except Exception as cleanup_error:
                    self._record(
                        "program_only_pre_erase_cleanup_failed",
                        error=f"{type(cleanup_error).__name__}: {cleanup_error}",
                    )
            power_cycle_required = not recovered and (
                self.destructive_started
                or self.write_authorized
                or self.authorization_state_requires_cycle
                or self.authorization_may_be_active
            )
            if commit_unknown:
                self.state = SessionState.COMMIT_UNKNOWN
            elif power_cycle_required:
                self.state = SessionState.POWER_CYCLE_REQUIRED
            elif recovered:
                self.state = SessionState.LOW_READY
            else:
                self.state = SessionState.FAILED
            if not self.journal.closed:
                self.journal.finish(
                    "commit_unknown" if commit_unknown else "failed",
                    operation_scope="program_only",
                    error=f"{type(error).__name__}: {error}",
                    state=self.state.value,
                    link=self.link.name.lower(),
                    destructive_started=self.destructive_started,
                    safe_legacy_fallback=self.safe_legacy_fallback,
                    verify_requested=self.verify_write,
                    power_cycle_required=power_cycle_required,
                )
            raise

    def recover_in_place(self) -> SlimFullWriteResult:
        if not self.destructive_started or self.plan is None:
            raise FullWriteError("no destructive full-write state is available")
        if not self.can_recover_in_place:
            raise FullWriteError(
                "same-session full-write replay is no longer qualified; the retained "
                "handler is not safe for another destructive retry"
            )
        if not self.transport.is_open:
            raise FullWriteError("retained full-write transport is closed")
        if self.journal.closed:
            raise FullWriteError("recovery requires a new open recovery journal")
        self.transport.event_cb = self.journal.event_callback
        self.transport.set_baud(
            self.rates.high,
            reason="retained full-write recovery resumes at high rate",
        )
        self.link = LinkRate.HIGH
        self.state = SessionState.HIGH_FULL_PROGRAM
        self.cleanup_attempted = False
        self._record(
            "full_write_recovery_started",
            strategy="same-session complete re-erase and rewrite",
        )
        try:
            self.recovery_replay_attempted = True
            if self.program_only:
                program_payload = self._program_program_only_plan()
                tune_payload = 0
            else:
                program_payload, tune_payload = self._program_full_plan()
            self._finalize_full()
            self.flash_completed = True
            if self.program_only:
                verified_bytes = (
                    self._verify_program_only_ranges() if self.verify_write else 0
                )
                self._set_state(
                    state=SessionState.POWER_CYCLE_REQUIRED,
                    link=LinkRate.HIGH,
                    reason="program-only recovery finalized; operator ignition cycle required",
                )
                result = self._result(
                    program_payload,
                    tune_payload,
                    verified_bytes,
                    power_cycle_required=True,
                )
                self.journal.finish(
                    "success",
                    recovery=True,
                    operation_scope="program_only",
                    verify_requested=self.verify_write,
                    verified_bytes=verified_bytes,
                    final_link=self.link.name.lower(),
                    cleanup_attempted=False,
                    cleanup_confirmed=False,
                    power_cycle_required=True,
                )
                return result
            verified_bytes = (
                self._verify_affected_ranges() if self.verify_write else 0
            )
            self._set_state(
                state=SessionState.POWER_CYCLE_REQUIRED,
                link=LinkRate.HIGH,
                reason="full recovery finalized; ignition cycle required",
            )
            result = self._result(program_payload, tune_payload, verified_bytes)
            self.journal.finish(
                "success",
                recovery=True,
                verify_requested=self.verify_write,
                verified_bytes=verified_bytes,
                final_link="high",
                power_cycle_required=True,
            )
            return result
        except Exception as error:
            self.failure_state = self.state
            self.fast_write_armed = False
            self.state = (
                SessionState.COMMIT_UNKNOWN
                if isinstance(error, CommitUnknownError)
                else SessionState.POWER_CYCLE_REQUIRED
            )
            if not self.journal.closed:
                self.journal.finish(
                    "commit_unknown"
                    if isinstance(error, CommitUnknownError)
                    else "power_cycle_required",
                    recovery=True,
                    error=f"{type(error).__name__}: {error}",
                    state=self.state.value,
                    link=self.link.name.lower(),
                    destructive_started=True,
                    retry_supported=False,
                    transport_retained=self.transport.is_open,
                    power_cycle_required=not isinstance(error, CommitUnknownError),
                )
            raise
