"""Bounded native DS2 probe for the MS41.2/.3 ST9030 telemetry path.

This is deliberately separate from the production fast-read API.  It admits
one exact local target image, enters only the captured mid-rate selector state,
sends one fixed command-0x0B request, reads three mid-rate RAM windows plus the
retained E62E completion byte once after low-rate cleanup, and returns to normal
9,600-baud DS2. It has no flash, EEPROM, seed/key, arbitrary command, or
automatic command-0x0B retry surface.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Optional

from ds2_fast_contracts import (
    LinkRate,
    ResponseStatus,
    SessionState,
    StatusResponseContract,
    contextual_recovery_contract,
    encode_ds2_frame,
    read_response_contract,
    selector_ack_contract,
)
from ds2_fast_plans import ReadRequest
from ds2_fast_read import (
    BITS_PER_CHARACTER_8E2,
    IDENTIFY_COMMAND,
    NativeFastReadSession,
    NativeFastReadTransport,
    PRODUCTION_RATE_PROFILE,
    READ_MEMORY_COMMAND,
    SELECTOR_COMMAND,
    SELECTOR_LOW,
    SELECTOR_MID,
    TOKEN_LENGTH,
    UnsafeReadOnlyCommand,
)


TARGET_SIZE = 0x40000
TARGET_SHA256 = "b0b3e9b4b2bb72bb507908c078ce542f42a90999f55c3a1c8e967d314b4e1ae3"
TARGET_TOKEN_OFFSET = 0x605E
EXPECTED_TOKEN = b"6577205163"

TELEGRAM_COMMAND = 0x0B
TELEGRAM_PAYLOAD = b"\x02\x00\x00\x00\x10"
TELEGRAM_TIMEOUT = 3.0

F732_ADDRESS = 0xF732
F732_LENGTH = 3
E620_ADDRESS = 0xE620
E620_LENGTH = 0x0E
E646_ADDRESS = 0xE646
E646_LENGTH = 0x14
E62E_ADDRESS = 0xE62E
E62E_LENGTH = 1


class NativeSt9030ProbeError(RuntimeError):
    """The bounded native ST9030 probe could not complete safely."""


@dataclass(frozen=True)
class NativeSt9030ProbeResult:
    timestamp_utc: str
    port: str
    target_sha256: str
    identity_hex: str
    token_ascii: str
    value_hex: str
    value_u16_be: int
    f732_f734_hex: str
    e620_e62d_hex: str
    e62e_followup_hex: str
    e646_e659_hex: str
    request_count: int
    telegram_attempts: int
    final_link: str
    cleanup_confirmed: bool
    frames: Mapping[str, str]


def _admit_target(path: Path) -> str:
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if len(data) != TARGET_SIZE:
        raise NativeSt9030ProbeError(
            f"target image is {len(data)} bytes, expected {TARGET_SIZE}"
        )
    if digest != TARGET_SHA256:
        raise NativeSt9030ProbeError(
            f"target SHA-256 {digest} is not admitted; expected {TARGET_SHA256}"
        )
    token = data[TARGET_TOKEN_OFFSET : TARGET_TOKEN_OFFSET + TOKEN_LENGTH]
    if token != EXPECTED_TOKEN:
        raise NativeSt9030ProbeError(
            "admitted image does not contain the expected MS41.2/.3 native token"
        )
    return digest


class NativeSt9030ProbeTransport(NativeFastReadTransport):
    """Native-fast transport with one additional exact read-only request."""

    @staticmethod
    def _validate_allowlist(command: int, args: bytes) -> None:
        if command == TELEGRAM_COMMAND:
            if bytes(args) != TELEGRAM_PAYLOAD:
                raise UnsafeReadOnlyCommand(
                    "command 0x0B permits only payload 02 00 00 00 10"
                )
            return
        NativeFastReadTransport._validate_allowlist(command, args)


class NativeSt9030ProbeSession(NativeFastReadSession):
    """One selector, one native 0x0B request, fixed observations, cleanup."""

    def __init__(self, transport: NativeSt9030ProbeTransport, **kwargs):
        super().__init__(transport, rates=PRODUCTION_RATE_PROFILE, **kwargs)
        self.telegram_attempted = False

    def _enter_mid(self) -> None:
        if self.state is not SessionState.TOKEN_KNOWN or self.link is not LinkRate.LOW:
            raise NativeSt9030ProbeError("mid-rate entry requires a known low-rate token")
        self.cleanup_b0_seen = False
        self.fast_selector_attempted = True
        self._request(
            SELECTOR_COMMAND,
            bytes((SELECTOR_MID,)) + self._require_token(),
            selector_ack_contract(),
            "st9030_selector_0x12_low_to_mid",
        )
        self.fast_selector_acknowledged = True
        self._set_state(
            state=SessionState.MID,
            link=LinkRate.UNKNOWN,
            reason="selector 0x12 ACK completed at low rate",
        )
        guard = max(
            0.001,
            2 * BITS_PER_CHARACTER_8E2 / PRODUCTION_RATE_PROFILE.low,
        )
        self._sleep(guard)
        self.transport.set_baud(
            PRODUCTION_RATE_PROFILE.mid,
            reason="selector 0x12 acknowledged at low rate",
        )
        self._set_state(
            state=SessionState.MID,
            link=LinkRate.MID,
            reason="host switched to exact native mid rate",
        )

    def _read_fixed(self, address: int, count: int, label: str) -> bytes:
        response = self._request(
            READ_MEMORY_COMMAND,
            ReadRequest(address, count).payload,
            read_response_contract(count),
            label,
        )
        return response.payload

    def _request_telegram_once(self) -> bytes:
        if self.telegram_attempted:
            raise NativeSt9030ProbeError("command 0x0B may be attempted only once")
        self.telegram_attempted = True
        response = self._request(
            TELEGRAM_COMMAND,
            TELEGRAM_PAYLOAD,
            StatusResponseContract(
                "native ST9030 telemetry value",
                frozenset((ResponseStatus.ACK,)),
                exact_payload_length=2,
            ),
            "st9030_native_0x0B_value_0x10",
            first_byte_timeout=TELEGRAM_TIMEOUT,
        )
        return response.payload

    def _recover_probe_to_low(self) -> bool:
        """Recover only from the two rates this probe can occupy."""

        if self.token is None or not self.fast_selector_attempted:
            return self.link is LinkRate.LOW
        self.recovery_used = True
        self._set_state(
            state=SessionState.FAILED,
            link=LinkRate.UNKNOWN,
            reason="bounded native ST9030 probe recovery started",
        )
        for candidate in (LinkRate.MID, LinkRate.LOW):
            self.transport.set_baud(
                PRODUCTION_RATE_PROFILE.for_link(candidate),
                reason=f"ST9030 probe recovery probe {candidate.name.lower()}",
            )
            self.link = candidate
            try:
                self._liveness(candidate)
            except Exception as error:
                self._emit(
                    "st9030_probe_recovery_probe_failed",
                    candidate=candidate.name.lower(),
                    error=f"{type(error).__name__}: {error}",
                )
                continue

            try:
                if candidate is LinkRate.MID:
                    self.state = SessionState.MID
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
                            name="ST9030 probe recovery bare BMW cleanup",
                        ),
                        "st9030_probe_recovery_bare_BMW_expected_B0",
                    )
                    self.cleanup_b0_seen = True
                self._sleep(self.post_cleanup_delay)
                self._wait_for_low_ready()
                self._set_state(
                    state=SessionState.LOW_READY,
                    link=LinkRate.LOW,
                    reason="bounded ST9030 probe recovery validated low",
                )
                return True
            except Exception as error:
                self._emit(
                    "st9030_probe_recovery_cleanup_failed",
                    candidate=candidate.name.lower(),
                    error=f"{type(error).__name__}: {error}",
                )
        self.link = LinkRate.UNKNOWN
        return False

    def execute(self, *, port: str, target_sha256: str) -> NativeSt9030ProbeResult:
        try:
            self._begin()
            if self.token != EXPECTED_TOKEN:
                raise NativeSt9030ProbeError(
                    "live ECU token differs from admitted MS41.2/.3 token"
                )
            self._wait_for_native_fast_reentry()
            self._enter_mid()
            self._liveness(LinkRate.MID)
            value = self._request_telegram_once()
            f732 = self._read_fixed(F732_ADDRESS, F732_LENGTH, "observe_F732_F734")
            e620 = self._read_fixed(E620_ADDRESS, E620_LENGTH, "observe_E620_E62D")
            e646 = self._read_fixed(E646_ADDRESS, E646_LENGTH, "observe_E646_E659")
            self._cleanup_to_low()
            # E62E is the ninth byte of the native 0x10D receive.  The main
            # observation window intentionally stops at E62D; read this
            # retained completion byte once at ordinary low-rate DS2 after
            # cleanup.  Do not re-enter fast mode or replay 0x0B.
            e62e = self._read_fixed(
                E62E_ADDRESS,
                E62E_LENGTH,
                "observe_E62E_post_cleanup",
            )
            self._finish_success()
        except Exception as error:
            recovered = self._recover_probe_to_low()
            if self.fast_selector_attempted and not recovered:
                raise NativeSt9030ProbeError(
                    f"{error}; normal low-rate cleanup was not confirmed, "
                    "so an ignition power-cycle is required"
                ) from error
            raise

        token = self._require_token()
        frames = {
            "selector_0x12": encode_ds2_frame(
                SELECTOR_COMMAND, bytes((SELECTOR_MID,)) + token
            ).hex(" "),
            "telegram_0x0B": encode_ds2_frame(
                TELEGRAM_COMMAND, TELEGRAM_PAYLOAD
            ).hex(" "),
            "read_F732_F734": encode_ds2_frame(
                READ_MEMORY_COMMAND, ReadRequest(F732_ADDRESS, F732_LENGTH).payload
            ).hex(" "),
            "read_E620_E62D": encode_ds2_frame(
                READ_MEMORY_COMMAND, ReadRequest(E620_ADDRESS, E620_LENGTH).payload
            ).hex(" "),
            "read_E646_E659": encode_ds2_frame(
                READ_MEMORY_COMMAND, ReadRequest(E646_ADDRESS, E646_LENGTH).payload
            ).hex(" "),
            "read_E62E_post_cleanup": encode_ds2_frame(
                READ_MEMORY_COMMAND, ReadRequest(E62E_ADDRESS, E62E_LENGTH).payload
            ).hex(" "),
            "selector_0x26": encode_ds2_frame(
                SELECTOR_COMMAND, bytes((SELECTOR_LOW,)) + token
            ).hex(" "),
            "bare_BMW": encode_ds2_frame(SELECTOR_COMMAND, b"BMW").hex(" "),
            "identify": encode_ds2_frame(IDENTIFY_COMMAND).hex(" "),
        }
        return NativeSt9030ProbeResult(
            timestamp_utc=datetime.now(timezone.utc).isoformat(),
            port=str(port),
            target_sha256=target_sha256,
            identity_hex=bytes(self.identity or b"").hex(),
            token_ascii=token.decode("ascii"),
            value_hex=value.hex(),
            value_u16_be=int.from_bytes(value, "big"),
            f732_f734_hex=f732.hex(),
            e620_e62d_hex=e620.hex(),
            e62e_followup_hex=e62e.hex(),
            e646_e659_hex=e646.hex(),
            request_count=sum(self.transport.command_counts.values()),
            telegram_attempts=int(self.telegram_attempted),
            final_link=self.link.name.lower(),
            cleanup_confirmed=(
                self.state is SessionState.COMPLETE and self.link is LinkRate.LOW
            ),
            frames=frames,
        )


def run_native_st9030_probe(
    port: str,
    target_image: Path,
    *,
    echo: bool = True,
    event_cb=None,
    serial_factory=None,
) -> NativeSt9030ProbeResult:
    """Run the exact one-shot probe after local-image admission."""

    digest = _admit_target(Path(target_image))
    transport = NativeSt9030ProbeTransport.open_d2xx(
        port,
        echo=echo,
        event_cb=event_cb,
        serial_factory=serial_factory,
    )
    try:
        session = NativeSt9030ProbeSession(
            transport,
            event_cb=event_cb,
            reentry_required=True,
        )
        return session.execute(port=port, target_sha256=digest)
    finally:
        transport.close()


def _event_logger(event: str, fields: Mapping[str, object]) -> None:
    print(json.dumps({"event": event, **fields}, sort_keys=True), file=sys.stderr)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True, help="D2XX-associated COM port")
    parser.add_argument("--target-image", required=True, type=Path)
    parser.add_argument("--no-echo", action="store_true")
    args = parser.parse_args(argv)
    result = run_native_st9030_probe(
        args.port,
        args.target_image,
        echo=not args.no_echo,
        event_cb=_event_logger,
    )
    print(json.dumps(asdict(result), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
