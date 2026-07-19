import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import ds2
import live_data
from ds2 import DS2Interface, _xor
import ctypes
import pytest

from ds2_write_authorization import (
    AUTHORIZATION_STATE_ADDRESS,
    FLASH_MODE_MARKER_ADDRESS,
    INITIAL_SEED_RETRY_DELAY,
    MAX_INITIAL_SEED_ATTEMPTS,
    NATIVE_FAST_REENTRY_LATCH_ADDRESS,
    NATIVE_FAST_REENTRY_TIMER_ADDRESS,
    WRONG_KEY_COUNTER_ADDRESS,
)


_LEGACY_SEED = bytes(range(42))


class _LegacyAuthorizationHarness(DS2Interface):
    def __init__(
        self,
        *,
        state=0,
        wrong_keys=0,
        challenge_a1=0,
        challenge_a1_state=0,
        challenge_a1_wrong_key_increment=False,
    ):
        self.state = int(state)
        self.wrong_keys = int(wrong_keys)
        self.challenge_a1 = int(challenge_a1)
        self.challenge_a1_state = int(challenge_a1_state)
        self.challenge_a1_wrong_key_increment = bool(challenge_a1_wrong_key_increment)
        self.events = []

    def read_mem(self, address, count):
        self.events.append(("read", address, count))
        if address == AUTHORIZATION_STATE_ADDRESS:
            return bytes((self.state,))
        if address == WRONG_KEY_COUNTER_ADDRESS:
            return bytes((self.wrong_keys,))
        if address == FLASH_MODE_MARKER_ADDRESS:
            return b"\x00"
        return b"\x00" * count

    def _prepare(self):
        self.events.append(("prepare",))

    def status(self):
        self.events.append(("status",))

    def execute(self, command, args=b"", timeout=None):
        del timeout
        args = bytes(args)
        self.events.append(("execute", command, args))
        assert command == 0x90
        if args == b"BMW\x1e":
            if self.state == 2:
                return b"\x00"
            if self.state == 1:
                self.state = 0
                self.wrong_keys += 1
                raise ds2.DS2NegativeResponse(
                    "challenge consumed as wrong key",
                    command=0x90,
                    status=0xA1,
                    response=b"\x12\x04\xA1\xB7",
                )
            if self.challenge_a1:
                self.challenge_a1 -= 1
                self.state = self.challenge_a1_state
                if self.challenge_a1_wrong_key_increment:
                    self.wrong_keys += 1
                raise ds2.DS2NegativeResponse(
                    "contextual A1",
                    command=0x90,
                    status=0xA1,
                    response=b"\x12\x04\xA1\xB7",
                )
            self.state = 1
            return _LEGACY_SEED
        if args == self._compute_ms41_key(0x1E, _LEGACY_SEED):
            self.state = 2
            return b"\x00"
        raise AssertionError(f"unexpected authorization payload {args.hex(' ')}")


def _legacy_0x90_payloads(harness):
    return [
        event[2]
        for event in harness.events
        if event[0] == "execute" and event[1] == 0x90
    ]


def test_legacy_unlock_defaults_to_proven_challenge_and_sends_one_key():
    d = _LegacyAuthorizationHarness()
    messages = []

    assert d.unlock_write(log_fn=lambda message, *_args: messages.append(message)) == b"\x00"

    assert _legacy_0x90_payloads(d) == [
        b"BMW\x1e",
        d._compute_ms41_key(0x1E, _LEGACY_SEED),
    ]
    assert (d.state, d.wrong_keys) == (2, 0)
    assert messages == [
        "MS41 flash-mode marker (initial authorization): E740=0x00"
    ]


def test_legacy_unlock_does_not_use_native_fast_reentry_latch():
    d = _LegacyAuthorizationHarness()

    assert d.unlock_write() == b"\x00"

    read_addresses = [
        event[1]
        for event in d.events
        if event[0] == "read"
    ]
    assert NATIVE_FAST_REENTRY_TIMER_ADDRESS not in read_addresses
    assert NATIVE_FAST_REENTRY_LATCH_ADDRESS not in read_addresses


def test_legacy_clean_a1_waits_once_refreshes_preamble_then_retries(monkeypatch):
    d = _LegacyAuthorizationHarness(challenge_a1=1, challenge_a1_state=0)
    messages = []
    monkeypatch.setattr(
        ds2.time,
        "sleep",
        lambda seconds: d.events.append(("sleep", float(seconds))),
    )

    assert d.unlock_write(log_fn=lambda message, *_args: messages.append(message)) == b"\x00"

    assert _legacy_0x90_payloads(d) == [
        b"BMW\x1e",
        b"BMW\x1e",
        d._compute_ms41_key(0x1E, _LEGACY_SEED),
    ]
    retry_flow = []
    for event in d.events:
        if event[:2] == ("execute", 0x90) and event[2] == b"BMW\x1e":
            retry_flow.append(("challenge",))
        elif event[0] == "sleep":
            retry_flow.append(event)
        elif event == ("prepare",):
            retry_flow.append(event)
        elif event == ("read", 0x2001, 12):
            retry_flow.append(("read_preamble",))
        elif event == ("status",):
            retry_flow.append(event)
    assert retry_flow == [
        ("challenge",),
        ("sleep", 10.0),
        ("prepare",),
        ("read_preamble",),
        ("status",),
        ("challenge",),
    ]
    assert messages == [
        "MS41 flash-mode marker (initial authorization): E740=0x00",
        "MS41 flash-mode marker (after challenge A1 attempt 1): E740=0x00",
        "ECU seed not ready; waiting 10 seconds before one final retry",
    ]
    assert MAX_INITIAL_SEED_ATTEMPTS == 2
    assert INITIAL_SEED_RETRY_DELAY == 10.0
    assert (d.state, d.wrong_keys) == (2, 0)


def test_legacy_clean_a1_stops_after_second_challenge(monkeypatch):
    d = _LegacyAuthorizationHarness(
        challenge_a1=MAX_INITIAL_SEED_ATTEMPTS,
        challenge_a1_state=0,
    )
    sleeps = []
    monkeypatch.setattr(ds2.time, "sleep", lambda seconds: sleeps.append(seconds))

    with pytest.raises(ds2.DS2NegativeResponse, match="after 2 bounded"):
        d.unlock_write()

    assert _legacy_0x90_payloads(d) == [b"BMW\x1e", b"BMW\x1e"]
    assert sleeps == [10.0]


@pytest.mark.parametrize(
    "seed_error",
    (
        ds2.DS2Error("simulated challenge timeout"),
        ds2.DS2NegativeResponse(
            "malformed contextual A1",
            command=0x90,
            status=0xA1,
            response=bytes.fromhex("12 05 A1 00 B6"),
        ),
    ),
    ids=("transport-timeout", "a1-with-payload"),
)
def test_legacy_non_safe_seed_failure_never_retries(monkeypatch, seed_error):
    d = _LegacyAuthorizationHarness()

    def fail_challenge(command, args=b"", timeout=None):
        del timeout
        d.events.append(("execute", command, bytes(args)))
        raise seed_error

    monkeypatch.setattr(d, "execute", fail_challenge)

    with pytest.raises(type(seed_error)):
        d.unlock_write()

    assert _legacy_0x90_payloads(d) == [b"BMW\x1e"]
    assert ("prepare",) not in d.events
    assert ("status",) not in d.events


def test_legacy_a1_in_pending_key_state_never_repeats_challenge():
    d = _LegacyAuthorizationHarness(challenge_a1=1, challenge_a1_state=1)

    with pytest.raises(ds2.DS2Error, match="E658=1"):
        d.unlock_write()

    assert _legacy_0x90_payloads(d) == [b"BMW\x1e"]
    assert d.wrong_keys == 0


def test_legacy_a1_with_counter_change_never_repeats_challenge():
    d = _LegacyAuthorizationHarness(
        challenge_a1=1,
        challenge_a1_state=0,
        challenge_a1_wrong_key_increment=True,
    )

    with pytest.raises(ds2.DS2Error, match="E658=0, E74B=1"):
        d.unlock_write()

    assert _legacy_0x90_payloads(d) == [b"BMW\x1e"]


@pytest.mark.parametrize(("state", "wrong_keys"), ((1, 0), (0, 2)))
def test_legacy_unsafe_auth_state_sends_no_0x90(state, wrong_keys):
    d = _LegacyAuthorizationHarness(state=state, wrong_keys=wrong_keys)

    with pytest.raises(ds2.DS2Error, match="turn ignition off"):
        d.unlock_write()

    assert _legacy_0x90_payloads(d) == []


def test_legacy_existing_authorization_is_confirmed_without_key():
    d = _LegacyAuthorizationHarness(state=2)

    assert d.unlock_write() == b"\x00"
    assert _legacy_0x90_payloads(d) == [b"BMW\x1e"]


def test_wire_decoded_a1_preserves_command_status_and_payload_context():
    class A1Serial:
        is_open = True

        def __init__(self):
            self.timeout = 1.5
            self.pending = bytearray()

        def reset_input_buffer(self):
            self.pending.clear()

        def write(self, _frame):
            response = bytearray((0x12, 0x05, 0xA1, 0x7E))
            response.append(_xor(response))
            self.pending.extend(response)
            return len(_frame)

        def flush(self):
            pass

        def read(self, count):
            data = bytes(self.pending[:count])
            del self.pending[:count]
            return data

    d = DS2Interface("COM1", echo=False)
    d._ser = A1Serial()

    with pytest.raises(ds2.DS2NegativeResponse) as caught:
        d.execute(0x90, b"BMW\x1e")

    assert caught.value.command == 0x90
    assert caught.value.status == 0xA1
    assert caught.value.payload == b"\x7e"
    assert caught.value.response == bytes.fromhex("12 05 A1 7E C8")


class _FakeReadSerial:
    """Answers every READ_MEM (0x06) execute() with a valid zero-filled response.
    Enough to drive read_full/read_memory_range end-to-end without real I/O."""
    is_open = True

    def __init__(self):
        self.timeout = 1.5
        self._pending = b""

    def reset_input_buffer(self): self._pending = b""
    def reset_output_buffer(self): pass
    def flush(self): pass
    def setDTR(self, v): pass
    def setRTS(self, v): pass

    def write(self, frame):
        args = frame[3:-1]                      # READ_MEM args = addr(4) + len(1)
        n = args[4] if len(args) >= 5 else 1
        resp = bytes([0x12, 0, 0xA0]) + bytes(n)
        resp = bytes([0x12, len(resp) + 1, 0xA0]) + bytes(n)
        self._pending = resp + bytes([_xor(resp)])
        return len(frame)

    def read(self, n):
        chunk, self._pending = self._pending[:n], self._pending[n:]
        return chunk


def test_read_full_forwards_numeric_progress():
    """Regression: read_full's per-block progress callback must forward numeric
    (done, total, label) — a prior version captured the block base in a slot that
    the 3rd positional label argument clobbered, raising
    'can only concatenate str (not "int") to str' on every full read."""
    d = DS2Interface(port="COM1", baud=9600, echo=False)
    d._ser = _FakeReadSerial()

    seen = []
    d.read_full(progress_cb=lambda done, total, label="": seen.append((done, total, label)))

    assert seen, "progress callback never fired"
    for done, total, label in seen:
        assert isinstance(done, int) and isinstance(total, int)
        assert isinstance(label, str)
    # progress must be monotonic within [0, FULL_SIZE]
    assert max(done for done, _, _ in seen) <= DS2Interface.FULL_SIZE


def test_open_prefers_d2xx_when_available(monkeypatch):
    calls = []

    class FakeD2XX:
        def __init__(self, **kw):
            calls.append(("d2xx", kw))
        def setDTR(self, v): pass
        def setRTS(self, v): pass
        @property
        def is_open(self): return True
        def close(self): pass

    monkeypatch.setattr(ds2, "_import_d2xx_serial", lambda: FakeD2XX)
    d = ds2.DS2Interface(port="COM7", baud=9600)
    d.open()
    assert calls and calls[0][0] == "d2xx"
    assert d._ser.__class__ is FakeD2XX
    assert d.uses_d2xx is True


def test_d2xx_does_not_require_pyserial_to_be_installed(monkeypatch):
    class FakeD2XX:
        def __init__(self, **_kw): pass
        def setDTR(self, _value): pass
        def setRTS(self, _value): pass
        @property
        def is_open(self): return True
        def close(self): pass

    monkeypatch.setattr(ds2, "serial", None)
    monkeypatch.setattr(ds2, "_import_d2xx_serial", lambda: FakeD2XX)
    d = ds2.DS2Interface(port="COM7", baud=9600)
    d.open()
    assert d.uses_d2xx is True


def test_open_falls_back_to_pyserial_when_d2xx_unavailable(monkeypatch):
    def _boom():
        raise ImportError("no ftd2xx")
    monkeypatch.setattr(ds2, "_import_d2xx_serial", _boom)

    calls = []
    class FakeSerial:
        def __init__(self, **kw):
            calls.append(kw)
        def setDTR(self, v): pass
        def setRTS(self, v): pass
        @property
        def is_open(self): return True
        def close(self): pass

    monkeypatch.setattr(ds2, "serial", type("S", (), {
        "Serial": FakeSerial, "EIGHTBITS": 8, "PARITY_EVEN": "E", "STOPBITS_TWO": 2,
    }))
    d = ds2.DS2Interface(port="COM7", baud=9600)
    d.open()
    assert calls and d._ser.__class__ is FakeSerial
    assert d.transport_name == "pyserial"


def test_open_falls_back_when_d2xx_construction_raises(monkeypatch):
    def _ok_import():
        class Boom:
            def __init__(self, **kw):
                raise OSError("device not found")
        return Boom
    monkeypatch.setattr(ds2, "_import_d2xx_serial", _ok_import)

    calls = []
    class FakeSerial:
        def __init__(self, **kw):
            calls.append(kw)
        def setDTR(self, v): pass
        def setRTS(self, v): pass
        @property
        def is_open(self): return True
        def close(self): pass

    monkeypatch.setattr(ds2, "serial", type("S", (), {
        "Serial": FakeSerial, "EIGHTBITS": 8, "PARITY_EVEN": "E", "STOPBITS_TWO": 2,
    }))
    d = ds2.DS2Interface(port="COM7", baud=9600)
    d.open()
    assert calls and d._ser.__class__ is FakeSerial


class _FakeD2XXDriver:
    def __init__(self, com_ports=(3, 7), fail_baud=False, latency_statuses=None,
                 queue_status=(0, 0, 0)):
        self.com_ports = tuple(com_ports)
        self.fail_baud = fail_baud
        self.latency_statuses = list(latency_statuses or ())
        self.latency_calls = []
        self.handles = {}
        self.closed = []
        self.purge_calls = []
        self.queue_status = tuple(queue_status)

    @staticmethod
    def _set(pointer, ctype, value):
        ctypes.cast(pointer, ctypes.POINTER(ctype))[0] = value

    def FT_CreateDeviceInfoList(self, pointer):
        self._set(pointer, ctypes.c_ulong, len(self.com_ports)); return 0
    def FT_Open(self, index, pointer):
        handle = 100 + int(index)
        self.handles[handle] = int(index)
        self._set(pointer, ctypes.c_void_p, handle); return 0
    def FT_GetComPortNumber(self, handle, pointer):
        self._set(pointer, ctypes.c_long, self.com_ports[self.handles[handle.value]]); return 0
    def FT_Close(self, handle):
        self.closed.append(handle.value); return 0
    def FT_SetDataCharacteristics(self, *_args): return 0
    def FT_SetBaudRate(self, *_args): return 5 if self.fail_baud else 0
    def FT_SetLatencyTimer(self, _handle, value):
        self.latency_calls.append(int(value.value))
        return self.latency_statuses.pop(0) if self.latency_statuses else 0
    def FT_SetTimeouts(self, *_args): return 0
    def FT_Purge(self, _handle, mask):
        self.purge_calls.append(int(mask)); return 0
    def FT_GetStatus(self, _handle, rx, tx, events):
        self._set(rx, ctypes.c_ulong, self.queue_status[0])
        self._set(tx, ctypes.c_ulong, self.queue_status[1])
        self._set(events, ctypes.c_ulong, self.queue_status[2])
        return 0


def test_d2xx_binds_to_the_selected_com_port(monkeypatch):
    from engines.softbsl import d2xx_serial
    driver = _FakeD2XXDriver((3, 7))
    monkeypatch.setattr(d2xx_serial, "_ft", driver)

    serial = d2xx_serial.D2XXSerial(port="COM7")
    assert serial.index == 1
    assert 100 in driver.closed                 # non-matching COM3 probe was closed
    assert 101 not in driver.closed
    serial.close()
    assert 101 in driver.closed


def test_d2xx_open_purges_both_queues_and_exposes_full_status(monkeypatch):
    from engines.softbsl import d2xx_serial
    driver = _FakeD2XXDriver((7,), queue_status=(3, 2, 1))
    monkeypatch.setattr(d2xx_serial, "_ft", driver)

    serial = d2xx_serial.D2XXSerial(port="COM7")

    assert driver.purge_calls == [d2xx_serial._PURGE_RX | d2xx_serial._PURGE_TX]
    assert serial.queue_status() == (3, 2, 1)
    assert serial.in_waiting == 3
    serial.close()


def test_d2xx_prefers_one_ms_latency_and_falls_back_to_two(monkeypatch):
    from engines.softbsl import d2xx_serial
    driver = _FakeD2XXDriver((7,), latency_statuses=[5, 0])
    monkeypatch.setattr(d2xx_serial, "_ft", driver)

    serial = d2xx_serial.D2XXSerial(port="COM7")
    assert driver.latency_calls == [1, 2]
    assert serial.latency_timer_ms == 2
    serial.close()


def test_d2xx_configuration_failure_closes_the_open_handle(monkeypatch):
    from engines.softbsl import d2xx_serial
    driver = _FakeD2XXDriver((7,), fail_baud=True)
    monkeypatch.setattr(d2xx_serial, "_ft", driver)

    try:
        d2xx_serial.D2XXSerial(port="COM7")
        assert False, "expected D2XX configuration failure"
    except d2xx_serial.D2XXError:
        pass
    assert driver.closed == [100]


def test_d2xx_maps_none_parity_for_raw_bsl_framing():
    from engines.softbsl.d2xx_serial import D2XXSerial

    assert D2XXSerial._parity_code(0) == 0
    assert D2XXSerial._parity_code("N") == 0
    assert D2XXSerial._parity_code("E") == 2
def test_direct_tap_echo_false_skips_echo_read(monkeypatch):
    d = DS2Interface(port="COM1", baud=9600, echo=False)
    monkeypatch.setattr(d, "_read_exact",
                        lambda *_args: (_ for _ in ()).throw(AssertionError("echo read attempted")))
    d._discard_echo(b"\x12\x04\x00\x16")


def test_kline_echo_true_consumes_exact_transmitted_frame(monkeypatch):
    d = DS2Interface(port="COM1", baud=9600, echo=True)
    seen = []
    monkeypatch.setattr(ds2.time, "sleep", lambda seconds: seen.append(("sleep", seconds)))
    monkeypatch.setattr(d, "_read_exact", lambda n, timeout: seen.append(("read", n, timeout)) or bytes(n))
    frame = b"\x12\x04\x00\x16"
    d._discard_echo(frame)
    assert seen[-1] == ("read", len(frame), d._ECHO_READ_TMO)


class _TxOnlySerial:
    is_open = True

    def __init__(self, *, echo=True, short_write=False):
        self.echo = echo
        self.short_write = short_write
        self.pending = b""
        self.writes = []
        self.read_calls = 0

    def reset_input_buffer(self):
        self.pending = b""

    def write(self, frame):
        frame = bytes(frame)
        self.writes.append(frame)
        if self.echo:
            self.pending = frame
        return len(frame) - 1 if self.short_write else len(frame)

    def flush(self):
        pass

    def read(self, size):
        self.read_calls += 1
        data, self.pending = self.pending[:size], self.pending[size:]
        return data


def test_send_no_response_transmits_and_validates_the_complete_kline_echo(monkeypatch):
    serial = _TxOnlySerial(echo=True)
    d = DS2Interface(port="COM1", baud=9600, echo=True)
    d._ser = serial
    monkeypatch.setattr(ds2.time, "sleep", lambda _seconds: None)

    d.send_no_response(0x2A)

    expected = bytes([0x12, 0x04, 0x2A, 0x12 ^ 0x04 ^ 0x2A])
    assert serial.writes == [expected]
    assert serial.read_calls == 1                 # echo only; no ECU-response read


def test_send_no_response_direct_tap_does_not_attempt_a_read():
    serial = _TxOnlySerial(echo=False)
    d = DS2Interface(port="COM1", baud=9600, echo=False)
    d._ser = serial

    d.send_no_response(0x2A)

    assert len(serial.writes) == 1
    assert serial.read_calls == 0


def test_send_no_response_rejects_a_short_adapter_write():
    serial = _TxOnlySerial(echo=False, short_write=True)
    d = DS2Interface(port="COM1", baud=9600, echo=False)
    d._ser = serial

    try:
        d.send_no_response(0x2A)
        assert False, "expected a short-write failure"
    except ds2.DS2Error as error:
        assert "short write" in str(error)


def test_ms412_batch_setup_includes_all_fast_logger_parameters():
    payload = DS2Interface._build_batch_setup("1406464")
    # Dynamic MS41.2/MS41.3 addresses: injector PW, load, TPS, and both trim banks.
    for entry in ("010000EF7E", "010000FC52", "000000E8D0",
                  "010000F030", "010000F0DC", "000000F01F", "000000F0CB"):
        assert bytes.fromhex(entry) in payload
    # Shared parameters that previously required six extra cmd-0x06 transactions.
    for entry in ("010000DA36", "000000DA63", "000000E9D9", "000000E9E6"):
        assert bytes.fromhex(entry) in payload
    # The formerly discarded slots now carry state bytes and universal ADC inputs.
    for entry in ("000000DA56", "000000FD24", "000000FD14",
                  "010000FA9A", "010000FA98", "010000FA9E"):
        assert bytes.fromhex(entry) in payload
    assert bytes.fromhex("000000F189") not in payload
    assert bytes.fromhex("000000F191") not in payload


def test_custom_batch_plan_preserves_group_lengths_and_wire_size():
    layout = live_data.batch_layout_for(
        "1406464", live_data.PROFILE_WIDEBAND, 0xFA98)
    entries = live_data.batch_wire_entries(layout)

    payload = DS2Interface._build_batch_setup("1406464", entries)

    assert len(payload) == 132
    for entry in ("000000E800", "010000FA98", "010000FA9E", "010000E810"):
        assert bytes.fromhex(entry) in payload
