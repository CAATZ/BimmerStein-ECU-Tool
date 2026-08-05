"""Deterministic protocol stress tests.

Set ``MS41_SOAK_ROUNDS`` to run a longer soak (for example, 10000).
"""

import os
import random
from types import SimpleNamespace

import ds2
import softbsl_service
from engines.softbsl.softbsl_host import SoftBSLError


_ROUNDS = int(os.environ.get("MS41_SOAK_ROUNDS", "512"))
if _ROUNDS <= 0:
    raise ValueError("MS41_SOAK_ROUNDS must be a positive integer")
_SEED = 0x41D52


class _FragmentedSerial:
    is_open = True

    def __init__(self, rng, *, echo, status, payload):
        self.rng = rng
        self.echo = echo
        self.status = status
        self.payload = payload
        self.pending = bytearray()
        self.writes = []
        self.timeout = 0

    def reset_input_buffer(self):
        self.pending.clear()

    def write(self, frame):
        frame = bytes(frame)
        assert frame[1] == len(frame) and ds2._xor(frame[:-1]) == frame[-1]
        response = bytes((frame[0], len(self.payload) + 4, self.status)) + self.payload
        response += bytes((ds2._xor(response),))
        self.pending.extend(frame if self.echo else b"")
        self.pending.extend(response)
        self.writes.append(frame)
        return len(frame)

    def flush(self):
        pass

    def read(self, size):
        if not self.pending:
            return b""
        size = self.rng.randint(1, min(size, len(self.pending)))
        chunk = bytes(self.pending[:size])
        del self.pending[:size]
        return chunk


def test_randomized_fragmented_ds2_roundtrip_soak(monkeypatch):
    rng = random.Random(_SEED)
    monkeypatch.setattr(ds2.time, "sleep", lambda _seconds: None)

    for case in range(_ROUNDS):
        args = rng.randbytes(rng.randrange(252))
        payload = rng.randbytes(rng.randrange(252))
        status = rng.choice((0xA0, 0xFF))
        command = 0xA2 if status == 0xFF else rng.randrange(256)
        echo = bool(rng.getrandbits(1))
        serial = _FragmentedSerial(
            rng, echo=echo, status=status, payload=payload
        )
        interface = ds2.DS2Interface("SOAK", echo=echo)
        interface._ser = serial

        result = interface.execute(command, args)

        assert result == payload, f"seed={_SEED} case={case}"
        assert serial.writes == [interface._command_frame(command, args)]
        assert not serial.pending, f"seed={_SEED} case={case} left unread bytes"


def test_randomized_softbsl_fallback_state_soak():
    scenarios = (
        ("high", ("ok",), ("high",), None),
        ("high", ("soft", "ok"), ("high", "mid"), None),
        ("high", ("soft", "soft", "ok"), ("high", "mid", "low"), None),
        ("high", ("soft", "soft", "soft"), ("high", "mid", "low"),
         softbsl_service.SoftBSLFallbackExhausted),
        ("mid", ("soft", "ok"), ("mid", "low"), None),
        ("low", ("soft",), ("low",), SoftBSLError),
        ("weird", ("soft",), ("weird",), SoftBSLError),
        ("high", ("d2xx", "ok"), ("high", "low"), None),
        ("mid", ("d2xx", "soft"), ("mid", "low"),
         softbsl_service.SoftBSLFallbackExhausted),
        ("high", ("recovery",), ("high",),
         softbsl_service.SoftBSLWriteRecoveryRequired),
        ("high", ("soft", "recovery"), ("high", "mid"),
         softbsl_service.SoftBSLWriteRecoveryRequired),
    )
    rng = random.Random(_SEED ^ 0xBAD)

    for case in range(_ROUNDS):
        start, outcomes, expected_attempts, expected_error = rng.choice(scenarios)
        attempts = []

        def attempt(tier):
            outcome = outcomes[len(attempts)]
            attempts.append(tier)
            if outcome == "soft":
                raise SoftBSLError(f"noise at {tier}")
            if outcome == "d2xx":
                raise softbsl_service.D2XXRequiredError(f"no D2XX at {tier}")
            if outcome == "recovery":
                recovery = SimpleNamespace(error=RuntimeError(f"erased at {tier}"))
                raise softbsl_service.SoftBSLWriteRecoveryRequired(recovery)
            return (case, tier)

        try:
            result = softbsl_service._with_baud_fallback(
                attempt, start, lambda _message: None, "soak"
            )
            caught = None
        except Exception as error:
            result, caught = None, error

        assert tuple(attempts) == expected_attempts, f"seed={_SEED} case={case}"
        if expected_error is None:
            assert result == (case, expected_attempts[-1]) and caught is None
        else:
            assert isinstance(caught, expected_error), (
                f"seed={_SEED} case={case}: expected {expected_error}, got {caught!r}"
            )
