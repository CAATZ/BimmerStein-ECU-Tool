"""Fixed BMW MS41 output tests with one owner for request, hold and cleanup.

Requests combine the BMW STEUERN_* jobs with the exact native selector and
parameter checks in MS41.0/.1/.2/.3. The ECU, not this host,
controls the output pulses; the host ends the session with DIAGNOSE_ENDE.
MS41.3 uses the MS411DS2 diagnostic lineage. No caller-supplied selector or
duty is sent.
"""

import math
import time
from dataclasses import dataclass

from ds2 import DS2Commands, DS2NegativeResponse
from ms42_ms43_actuators import TESTS as _MS42_MS43_TESTS, stop_output_override


class ActuatorTestError(Exception):
    """The fixed output test was unavailable, unsafe to start, or not accepted."""


class ActuatorTestCleanupError(ActuatorTestError):
    """The ECU has not acknowledged output-session cleanup; retain Retry Stop."""

    def __init__(self, message, *, original_error=None):
        super().__init__(message)
        self.original_error = original_error


@dataclass(frozen=True)
class ActuatorTestResult:
    label: str
    warning: str = ""


# ID -> (display label, 0x0C request payload). Native checks correct stale
# factory jobs: MS41.0 purge is 44/80/80, not 46/05/0A; exhaust is 34/15/30,
# not 34/00/FF. The timing bytes are pulse parameters, not host run durations.
_INJECTORS = {
    "injector_1": ("Injector 1", b"\x06\x05\x0A"),
    "injector_2": ("Injector 2", b"\x05\x05\x0A"),
    "injector_3": ("Injector 3", b"\x16\x05\x0A"),
    "injector_4": ("Injector 4", b"\x18\x05\x0A"),
    "injector_5": ("Injector 5", b"\x21\x05\x0A"),
    "injector_6": ("Injector 6", b"\x17\x05\x0A"),
}
_COMMON_TESTS = {
    **_INJECTORS,
    "purge_valve": ("Purge valve", b"\x44\x80\x80"),
    "idle_control_valve": ("Idle control valve", b"\x35\x80\x80"),
    "fuel_pump_relay": ("Fuel pump relay", b"\x45\xFF\xFF"),
    "ac_compressor_relay": ("A/C compressor relay", b"\x4A\x80\x80"),
    "vanos": ("VANOS", b"\x15\x20\x10"),
    "exhaust_flap": ("Exhaust flap", b"\x34\x15\x30"),
}
_LATER_TESTS = {
    **_COMMON_TESTS,
    "secondary_air_pump_relay": ("Secondary air pump relay", b"\x23\x15\x30"),
    "secondary_air_valve": ("Secondary air valve", b"\x3E\x15\x30"),
    "running_losses_valve": ("Running losses valve", b"\x32\x15\x30"),
    "shutoff_valve": ("Shutoff valve", b"\x33\x80\x80"),
    "malfunction_indicator_lamp": ("Check-engine lamp (MIL)", b"\x14\x80\x80"),
}
_TESTS = {
    "MS41.0": _COMMON_TESTS,
    "MS41.1": _LATER_TESTS,
    "MS41.2": _LATER_TESTS,
    "MS41.3": _LATER_TESTS,
    **_MS42_MS43_TESTS,
}
_ENGINE_RUNNING_TESTS = {"vanos", "exhaust_flap", "vanos_intake", "vanos_exhaust"}
# Exact stock MS41.1/.2 and inherited MS41.3 handlers can retain these output
# requests while returning stale result 04. Configuration can also block the
# output, so keep that result visibly unconfirmed through session cleanup.
_UNCONFIRMED_RESULT_TESTS = {
    "secondary_air_pump_relay", "secondary_air_valve", "running_losses_valve",
    "shutoff_valve", "malfunction_indicator_lamp",
}


def available_actuator_tests(variant: str) -> tuple[tuple[str, str], ...]:
    """Return (test ID, label) choices for an explicitly identified family."""
    tests = _TESTS.get(variant)
    return tuple((test_id, label) for test_id, (label, _) in tests.items()) if tests else ()


def actuator_test_requirement(variant: str, test_id: str) -> str:
    """Return the engine prerequisite for a listed test, or no hint if unavailable."""
    if test_id not in _TESTS.get(variant, {}):
        return ""
    return "Engine running" if test_id in _ENGINE_RUNNING_TESTS else "Engine stopped"


def stop_actuator_test(ds2) -> None:
    """End the diagnostic output session and require its empty A0 reply."""
    try:
        stop_output_override(ds2)
        result = ds2.execute(DS2Commands.DIAGNOSE_END)
        if result != b"":
            raise ActuatorTestError("invalid DIAGNOSE_ENDE response")
        if hasattr(ds2, "_ms42_ms43_actuator_selector"):
            ds2._ms42_ms43_actuator_selector = None
    except Exception as error:
        raise ActuatorTestCleanupError(
            f"ECU output stop was not confirmed: {error}. Retry Stop before another ECU operation."
        ) from error


def run_actuator_test(ds2, variant: str, test_id: str, *, max_wait_s: float = 10.0,
                      duration_s: float = 5.0, stop_event=None, status_fn=None) -> ActuatorTestResult:
    """Request, hold, then end one output session from the calling worker.

    A0/result zero acknowledges the request; it does not mean the output ended.
    Stop requests wake the worker, which alone sends DIAGNOSE_ENDE. Return the
    result only after cleanup, or if cancelled before sending an output request.
    """
    tests = _TESTS.get(variant)
    if tests is None or test_id not in tests:
        raise ActuatorTestError("Actuator test unavailable for this identified ECU family")
    later_family = variant in _MS42_MS43_TESTS
    command = 0x22 if later_family else DS2Commands.ACTUATOR_TEST
    if later_family and getattr(ds2, "_ms42_ms43_actuator_selector", None) is not None:
        raise ActuatorTestCleanupError("Previous output stop was not confirmed. Retry Stop first.")
    for name, value in (("max_wait_s", max_wait_s), ("duration_s", duration_s)):
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value) or not 0 < value <= 10):
            raise ValueError(f"{name} must be greater than zero and at most 10 seconds")
    if stop_event is not None and not all(
            callable(getattr(stop_event, method, None)) for method in ("is_set", "wait")):
        raise TypeError("stop_event must provide is_set() and wait()")
    if status_fn is not None and not callable(status_fn):
        raise TypeError("status_fn must be callable")

    def stopped():
        return stop_event is not None and stop_event.is_set()

    def wait(seconds):
        if stop_event is None:
            time.sleep(seconds)
        else:
            stop_event.wait(seconds)

    def status(message):
        if status_fn is not None:
            status_fn(message)

    label, request = tests[test_id]
    result_info = ActuatorTestResult(label)
    engine_running = test_id in _ENGINE_RUNNING_TESTS
    if stopped():
        status("Test cancelled before output request.")
        return result_info
    status("Checking engine RPM…")
    # BMW STATUS_MOTORDREHZAHL: 0B 03 -> raw response bytes 3:5,
    # big-endian RPM in 1/min. Require two samples matching this test's mode.
    for sample in range(2):
        if stopped():
            status("Test cancelled before output request.")
            return result_info
        rpm_payload = ds2.execute(DS2Commands.TELEGRAM, b"\x03")
        if len(rpm_payload) < 2:
            raise ActuatorTestError("Cannot verify engine RPM: short ECU response")
        rpm = int.from_bytes(rpm_payload[:2], "big")
        if bool(rpm) != engine_running:
            condition = "running" if engine_running else "stopped"
            raise ActuatorTestError(f"Engine must be {condition} (ECU reports {rpm} RPM)")
        if sample == 0:
            wait(0.15)

    requested = False
    original_error = None
    try:
        deadline = time.monotonic() + max_wait_s
        status("Requesting output test…")
        while not stopped():
            if time.monotonic() >= deadline:
                raise ActuatorTestError("ECU still BUSY; output-test acceptance timed out")
            requested = True  # A timeout or malformed reply can follow an accepted request.
            if later_family:
                ds2._ms42_ms43_actuator_selector = request[0]
            try:
                result = ds2.execute(command, request)
            except DS2NegativeResponse as exc:
                if exc.command == command and exc.status == 0xA1:
                    wait(min(0.15, max(0.0, deadline - time.monotonic())))
                    continue
                if later_family and exc.command == command and exc.status == 0xB0:
                    ds2._ms42_ms43_actuator_selector = None
                raise
            if later_family:
                if result in (b"\x01", b"\x02"):
                    ds2._ms42_ms43_actuator_selector = None
                    reason = "operating conditions not met" if result == b"\x01" else "parameters rejected"
                    raise ActuatorTestError(f"ECU did not start this output test: {reason}")
                if result != b"\x00":
                    raise ActuatorTestError("ECU returned an invalid actuator-test result")
                result = request[:1] + b"\x00"
            unconfirmed = (len(result) == 2 and result[1] == 4
                           and variant != "MS41.0" and test_id in _UNCONFIRMED_RESULT_TESTS)
            if len(result) != 2 or result[0] != request[0] or (result[1] != 0 and not unconfirmed):
                raise ActuatorTestError("ECU returned an invalid actuator-test result")
            if unconfirmed:
                result_info = ActuatorTestResult(
                    label, "ECU did not confirm this test (result 0x04). Check whether the output responded.")
                status(f"Testing {label}; ECU confirmation unavailable.")
            else:
                status(f"{label} test active; stopping after {duration_s:g} seconds or when Stop is pressed.")
            wait(duration_s)
            break
    except Exception as error:
        original_error = error
        raise
    finally:
        if requested:
            try:
                status("Stopping output test…")
            finally:
                try:
                    stop_actuator_test(ds2)
                except ActuatorTestCleanupError as error:
                    error.original_error = original_error
                    if original_error is not None:
                        error.args = (f"{error} Original test failure: {original_error}",)
                    raise
    status("ECU output session ended." if requested else "Test cancelled before output request.")
    return result_info
