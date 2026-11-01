import os
import sys
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import actuator_tests as outputs
from ds2 import DS2Commands, DS2Interface, DS2NegativeResponse


class FakeDS2:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.calls = []

    def execute(self, command, args=b""):
        self.calls.append((command, args))
        reply = next(self.replies)
        if callable(reply):
            reply = reply()
        if isinstance(reply, Exception):
            raise reply
        return reply


@pytest.fixture
def clock(monkeypatch):
    class Clock:
        tick = 0.0

        def monotonic(self):
            return self.tick

        def sleep(self, seconds):
            self.tick += seconds

    clock = Clock()
    monkeypatch.setattr(outputs, "time", clock)
    return clock


def test_catalog_is_fixed_by_identified_family():
    assert len(outputs.available_actuator_tests("MS41.0")) == 12
    assert len(outputs.available_actuator_tests("MS41.1")) == 17
    assert len(outputs.available_actuator_tests("MS41.2")) == 17
    assert len(outputs.available_actuator_tests("MS41.3")) == 17
    assert outputs.available_actuator_tests("unknown") == ()
    assert "secondary_air_pump_relay" not in dict(outputs.available_actuator_tests("MS41.0"))


def test_running_engine_and_unknown_test_send_no_actuator_request(monkeypatch):
    monkeypatch.setattr(outputs.time, "sleep", lambda _: None)
    ecu = FakeDS2([b"\x03\xE8"])
    with pytest.raises(outputs.ActuatorTestError, match="Engine must be stopped"):
        outputs.run_actuator_test(ecu, "MS41.2", "injector_1")
    assert ecu.calls == [(DS2Commands.TELEGRAM, b"\x03")]

    ecu = FakeDS2([])
    with pytest.raises(outputs.ActuatorTestError, match="unavailable"):
        outputs.run_actuator_test(ecu, "MS41.0", "secondary_air_pump_relay")
    assert ecu.calls == []


@pytest.mark.parametrize("variant,test_id,payload,label", [
    ("MS41.0", "purge_valve", b"\x44\x80\x80", "Purge valve"),
    ("MS41.1", "injector_4", b"\x18\x05\x0A", "Injector 4"),
    ("MS41.2", "secondary_air_pump_relay", b"\x23\x15\x30", "Secondary air pump relay"),
    ("MS41.3", "secondary_air_valve", b"\x3E\x15\x30", "Secondary air valve"),
])
def test_factory_request_and_busy_poll(clock, variant, test_id, payload, label):
    busy = DS2NegativeResponse("busy", command=DS2Commands.ACTUATOR_TEST, status=0xA1)
    ecu = FakeDS2([b"\x00\x00", b"\x00\x00", busy, payload[:1] + b"\x00", b""])
    assert outputs.run_actuator_test(ecu, variant, test_id) == outputs.ActuatorTestResult(label)
    assert ecu.calls == [
        (DS2Commands.TELEGRAM, b"\x03"),
        (DS2Commands.TELEGRAM, b"\x03"),
        (DS2Commands.ACTUATOR_TEST, payload),
        (DS2Commands.ACTUATOR_TEST, payload),
        (DS2Commands.DIAGNOSE_END, b""),
    ]
    assert clock.tick == pytest.approx(5.3)


@pytest.mark.parametrize("variant", ["MS41.0", "MS41.1", "MS41.2", "MS41.3"])
@pytest.mark.parametrize("test_id,payload", [
    ("vanos", b"\x15\x20\x10"), ("exhaust_flap", b"\x34\x15\x30"),
])
def test_running_only_tests_verify_engine_state_and_native_request(clock, variant, test_id, payload):
    assert outputs.actuator_test_requirement(variant, test_id) == "Engine running"
    stopped_ecu = FakeDS2([b"\x00\x00"])
    with pytest.raises(outputs.ActuatorTestError, match="Engine must be running"):
        outputs.run_actuator_test(stopped_ecu, variant, test_id)
    assert stopped_ecu.calls == [(DS2Commands.TELEGRAM, b"\x03")]
    running_ecu = FakeDS2([b"\x03\xE8", b"\x03\xE8", payload[:1] + b"\x00", b""])
    outputs.run_actuator_test(running_ecu, variant, test_id)
    assert running_ecu.calls[-2:] == [
        (DS2Commands.ACTUATOR_TEST, payload), (DS2Commands.DIAGNOSE_END, b""),
    ]


@pytest.mark.parametrize("result", [b"", b"\x06", b"\x05\x00", b"\x06\x02"])
def test_reject_malformed_or_failed_ecu_result(clock, result):
    ecu = FakeDS2([b"\x00\x00", b"\x00\x00", result, b""])
    with pytest.raises(outputs.ActuatorTestError, match="invalid actuator-test result"):
        outputs.run_actuator_test(ecu, "MS41.2", "injector_1")
    assert ecu.calls[-1] == (DS2Commands.DIAGNOSE_END, b"")


def test_busy_poll_has_host_deadline(monkeypatch):
    class Clock:
        tick = 0

        def monotonic(self):
            self.tick += 5
            return self.tick

        def sleep(self, _):
            pass

    monkeypatch.setattr(outputs, "time", Clock())
    busy = DS2NegativeResponse("busy", command=DS2Commands.ACTUATOR_TEST, status=0xA1)
    ecu = FakeDS2([b"\x00\x00", b"\x00\x00", busy, b""])
    with pytest.raises(outputs.ActuatorTestError, match="still BUSY"):
        outputs.run_actuator_test(ecu, "MS41.2", "injector_1", max_wait_s=10)
    assert ecu.calls.count((DS2Commands.ACTUATOR_TEST, b"\x06\x05\x0A")) == 1
    assert ecu.calls[-1] == (DS2Commands.DIAGNOSE_END, b"")


@pytest.mark.parametrize("kwargs", [
    {"duration_s": 0}, {"duration_s": 11}, {"duration_s": float("nan")},
    {"max_wait_s": False}, {"max_wait_s": float("inf")},
    {"stop_event": object()}, {"status_fn": 1},
])
def test_invalid_options_do_not_contact_ecu(kwargs):
    ecu = FakeDS2([])
    with pytest.raises((TypeError, ValueError)):
        outputs.run_actuator_test(ecu, "MS41.2", "injector_1", **kwargs)
    assert ecu.calls == []


def test_stop_before_start_sends_nothing():
    stop = threading.Event()
    stop.set()
    ecu = FakeDS2([])
    statuses = []
    assert outputs.run_actuator_test(
        ecu, "MS41.2", "injector_1", stop_event=stop, status_fn=statuses.append).label == "Injector 1"
    assert ecu.calls == []
    assert statuses == ["Test cancelled before output request."]


@pytest.mark.parametrize("busy", [False, True])
def test_stop_after_request_ends_session_without_another_start(clock, busy):
    stop = threading.Event()

    def requested():
        stop.set()
        if busy:
            return DS2NegativeResponse("busy", command=DS2Commands.ACTUATOR_TEST, status=0xA1)
        return b"\x06\x00"

    ecu = FakeDS2([b"\x00\x00", b"\x00\x00", requested, b""])
    assert outputs.run_actuator_test(ecu, "MS41.2", "injector_1", stop_event=stop).label == "Injector 1"
    assert ecu.calls == [
        (DS2Commands.TELEGRAM, b"\x03"), (DS2Commands.TELEGRAM, b"\x03"),
        (DS2Commands.ACTUATOR_TEST, b"\x06\x05\x0A"), (DS2Commands.DIAGNOSE_END, b""),
    ]


def test_lost_acceptance_reply_still_cleans_up(clock):
    error = TimeoutError("lost reply")
    ecu = FakeDS2([b"\x00\x00", b"\x00\x00", error, b""])
    with pytest.raises(TimeoutError, match="lost reply"):
        outputs.run_actuator_test(ecu, "MS41.2", "injector_1")
    assert ecu.calls[-1] == (DS2Commands.DIAGNOSE_END, b"")


@pytest.mark.parametrize("reply", [b"unexpected", TimeoutError("lost stop reply")])
def test_cleanup_failure_is_not_success_and_retry_does_not_restart_output(clock, reply):
    ecu = FakeDS2([b"\x00\x00", b"\x00\x00", b"\x06\x00", reply, b""])
    with pytest.raises(outputs.ActuatorTestCleanupError, match="stop was not confirmed") as caught:
        outputs.run_actuator_test(ecu, "MS41.2", "injector_1")
    assert caught.value.original_error is None
    outputs.stop_actuator_test(ecu)
    assert ecu.calls[-2:] == [(DS2Commands.DIAGNOSE_END, b"")] * 2


def test_cleanup_failure_preserves_original_failure(clock):
    original = TimeoutError("lost acceptance")
    ecu = FakeDS2([b"\x00\x00", b"\x00\x00", original, TimeoutError("lost stop")])
    with pytest.raises(outputs.ActuatorTestCleanupError, match="Original test failure: lost acceptance") as caught:
        outputs.run_actuator_test(ecu, "MS41.2", "injector_1")
    assert caught.value.original_error is original


@pytest.mark.parametrize("variant", ["MS41.1", "MS41.2", "MS41.3"])
@pytest.mark.parametrize("test_id,payload", [
    ("secondary_air_pump_relay", b"\x23\x15\x30"),
    ("secondary_air_valve", b"\x3E\x15\x30"),
    ("running_losses_valve", b"\x32\x15\x30"),
    ("shutoff_valve", b"\x33\x80\x80"),
    ("malfunction_indicator_lamp", b"\x14\x80\x80"),
])
def test_later_result_four_preserves_warning_and_cleans_up(clock, variant, test_id, payload):
    ecu = FakeDS2([b"\x00\x00", b"\x00\x00", payload[:1] + b"\x04", b""])
    statuses = []
    result = outputs.run_actuator_test(ecu, variant, test_id, status_fn=statuses.append)
    assert result.warning == "ECU did not confirm this test (result 0x04). Check whether the output responded."
    assert any("ECU confirmation unavailable" in status for status in statuses)
    assert clock.tick == pytest.approx(5.15)
    assert ecu.calls[-2:] == [
        (DS2Commands.ACTUATOR_TEST, payload), (DS2Commands.DIAGNOSE_END, b""),
    ]


def test_result_four_remains_a_failure_for_core_tests(clock):
    ecu = FakeDS2([b"\x00\x00", b"\x00\x00", b"\x06\x04", b""])
    with pytest.raises(outputs.ActuatorTestError, match="invalid actuator-test result"):
        outputs.run_actuator_test(ecu, "MS41.2", "injector_1")
    assert clock.tick == pytest.approx(0.15)
    assert ecu.calls[-1] == (DS2Commands.DIAGNOSE_END, b"")


@pytest.mark.parametrize("command,payload", [
    (DS2Commands.ACTUATOR_TEST, b"\x06\x05\x0A"),
    (DS2Commands.DIAGNOSE_END, b""),
])
def test_transport_rejects_prepare_status_on_output_command(command, payload):
    class Serial:
        is_open = True

        def __init__(self):
            self.pending = bytearray()

        def reset_input_buffer(self):
            self.pending.clear()

        def write(self, frame):
            response = bytearray((0x12, 0x06, 0xFF, 0x06, 0x00))
            response.append(response[0] ^ response[1] ^ response[2] ^ response[3] ^ response[4])
            self.pending.extend(response)
            return len(frame)

        def flush(self):
            pass

        def read(self, count):
            data = bytes(self.pending[:count])
            del self.pending[:count]
            return data

    ds2 = DS2Interface("COM1", echo=False)
    ds2._ser = Serial()
    with pytest.raises(DS2NegativeResponse) as caught:
        ds2.execute(command, payload)
    assert caught.value.status == 0xFF
