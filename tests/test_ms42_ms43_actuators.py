"""Real shared output owner, fake transport: family requests and selective cleanup."""

import threading

import pytest

import actuator_tests as outputs
from ds2 import DS2NegativeResponse
from ms42_ms43_actuators import TESTS
from tests.test_actuator_tests import FakeDS2, clock as clock


@pytest.mark.parametrize("family,test_id", [(family, test_id) for family, tests in TESTS.items()
                                         for test_id in tests])
def test_every_factory_output_has_matching_selective_stop(clock, family, test_id):
    label, payload = TESTS[family][test_id]
    rpm = b"\x03\xE8" if outputs.actuator_test_requirement(family, test_id) == "Engine running" else b"\0\0"
    ecu = FakeDS2([rpm, rpm, b"\0", b"", b""])
    assert outputs.run_actuator_test(ecu, family, test_id).label == label
    assert ecu.calls == [(0x0B, b"\x03"), (0x0B, b"\x03"), (0x22, payload),
                         (0x23, payload[:1]), (0x9F, b"")]
    assert ecu._ms42_ms43_actuator_selector is None


def test_injector_requests_are_family_specific():
    assert TESTS["MS42"]["injector_1"][1] == b"\xB1\x7D\x0A"
    assert TESTS["MS43"]["injector_1"][1] == b"\xB1\x5D\x0A"
    assert len(outputs.available_actuator_tests("MS42")) == 22
    assert len(outputs.available_actuator_tests("MS43")) == 22


@pytest.mark.parametrize("family", ("MS42", "MS43"))
def test_busy_uses_native_command_and_cleans_up(clock, family):
    busy = DS2NegativeResponse("busy", command=0x22, status=0xA1)
    ecu = FakeDS2([b"\0\0", b"\0\0", busy, b"\0", b"", b""])
    outputs.run_actuator_test(ecu, family, "purge_valve")
    assert ecu.calls[2:] == [(0x22, b"\xBB\x80"), (0x22, b"\xBB\x80"),
                             (0x23, b"\xBB"), (0x9F, b"")]


@pytest.mark.parametrize("result", (b"", b"\0\0", b"\x04", TimeoutError("lost start")))
def test_uncertain_acceptance_still_releases_selected_output(clock, result):
    ecu = FakeDS2([b"\0\0", b"\0\0", result, b"", b""])
    with pytest.raises((outputs.ActuatorTestError, TimeoutError)):
        outputs.run_actuator_test(ecu, "MS43", "purge_valve")
    assert ecu.calls[-2:] == [(0x23, b"\xBB"), (0x9F, b"")]


@pytest.mark.parametrize("result", (b"\x01", b"\x02",
                                   DS2NegativeResponse("unsupported", command=0x22, status=0xB0)))
def test_native_rejection_does_not_turn_into_unclear_stop_failure(clock, result):
    ecu = FakeDS2([b"\0\0", b"\0\0", result, b""])
    with pytest.raises((outputs.ActuatorTestError, DS2NegativeResponse)):
        outputs.run_actuator_test(ecu, "MS42", "purge_valve")
    assert ecu.calls[-1] == (0x9F, b"")
    assert all(command != 0x23 for command, _ in ecu.calls)


@pytest.mark.parametrize("fail_selected_stop", (False, True))
def test_retry_stop_remembers_selector_and_never_restarts_output(clock, fail_selected_stop):
    failed = TimeoutError("lost stop")
    cleanup = [failed] if fail_selected_stop else [b"", failed]
    ecu = FakeDS2([b"\0\0", b"\0\0", b"\0", *cleanup, b"", b""])
    with pytest.raises(outputs.ActuatorTestCleanupError, match="stop was not confirmed"):
        outputs.run_actuator_test(ecu, "MS43", "purge_valve")
    assert ecu._ms42_ms43_actuator_selector == 0xBB
    with pytest.raises(outputs.ActuatorTestCleanupError, match="Retry Stop"):
        outputs.run_actuator_test(ecu, "MS43", "injector_1")
    outputs.stop_actuator_test(ecu)
    assert ecu.calls[-2:] == [(0x23, b"\xBB"), (0x9F, b"")]
    assert ecu._ms42_ms43_actuator_selector is None
    assert sum(command == 0x22 for command, _ in ecu.calls) == 1


def test_cancellation_before_request_does_not_touch_transport():
    stop = threading.Event()
    stop.set()
    ecu = FakeDS2([])
    outputs.run_actuator_test(ecu, "MS42", "injector_1", stop_event=stop)
    assert ecu.calls == []


def test_stop_after_acceptance_still_sends_selective_release(clock):
    stop = threading.Event()
    def accepted():
        stop.set()
        return b"\0"
    ecu = FakeDS2([b"\0\0", b"\0\0", accepted, b"", b""])
    outputs.run_actuator_test(ecu, "MS42", "injector_1", stop_event=stop)
    assert ecu.calls[-2:] == [(0x23, b"\xB1"), (0x9F, b"")]


def test_busy_timeout_releases_unknown_pending_output(clock):
    busy = DS2NegativeResponse("busy", command=0x22, status=0xA1)
    ecu = FakeDS2([b"\0\0", b"\0\0", busy, b"", b""])
    with pytest.raises(outputs.ActuatorTestError, match="timed out"):
        outputs.run_actuator_test(ecu, "MS42", "injector_1", max_wait_s=0.1)
    assert ecu.calls[-2:] == [(0x23, b"\xB1"), (0x9F, b"")]
