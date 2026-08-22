import hashlib
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from engines.softbsl import checksum
from engines.softbsl import st9030_proxy


def _frame(body):
    return body + checksum._crc(body, 0xFFFF).to_bytes(2, "big")


class _FakeDS2:
    def __init__(self, reads=(), marker=b"\x00"):
        self.reads = list(reads)
        self.marker = marker
        self.baud = 187500

    def _read_exact(self, length, _timeout):
        value = self.reads.pop(0)
        assert len(value) == length
        return value

    def read_mem(self, address, length):
        assert (address, length) == (0xE740, 1)
        return self.marker


class _FakeSoftBSL:
    def __init__(self, reads=(), statuses=()):
        self.ds2 = _FakeDS2(reads)
        self.statuses = list(statuses)
        self.sent = []
        self.serial = SimpleNamespace(
            baudrate=187500,
            reset_input_buffer=lambda: None,
        )

    def _tx(self, value):
        self.sent.append(bytes((value,)))

    def _txs(self, value):
        self.sent.append(bytes(value))

    def _rx(self, timeout=2.0):
        return self.statuses.pop(0) if self.statuses else st9030_proxy.ACK

    def _ser(self):
        return self.serial


def _identity(marker=0):
    return _frame(bytes((5, 0x0F, 7, marker)))


def _snapshot():
    words = (0x801C, 1, 0, 0, 0x53)
    body = b"\x00" + b"".join(word.to_bytes(2, "big") for word in words)
    return _frame(body)


def _replay(slot=0, status=0, words=(0x101, 0x1FF)):
    header = bytes((status, slot, len(words)))
    body = header + b"".join(word.to_bytes(2, "big") for word in words)
    framed = _frame(body)
    return framed[:3], framed[3:]


def _gate(
    status=0,
    challenge=b"65772052030",
    challenge_status=0xA0,
    transmitted=b"72052030657",
    acknowledgement=0xA0,
):
    words = (*challenge, challenge_status)
    body = (
        bytes((status,))
        + b"".join(word.to_bytes(2, "big") for word in words)
        + transmitted
        + acknowledgement.to_bytes(2, "big")
    )
    framed = _frame(body)
    assert len(framed) == st9030_proxy.GATE_REPLY_SIZE == 40
    return framed


def _telemetry(
    status=0,
    count=1,
    terminal=0x19,
    words=(0xA0,),
    times=(0x19,),
):
    padded_words = (*words, *((0,) * (15 - len(words))))
    padded_times = (*times, *((0,) * (15 - len(times))))
    body = (
        bytes((status, count))
        + terminal.to_bytes(2, "big")
        + b"".join(word.to_bytes(2, "big") for word in padded_words)
        + b"".join(value.to_bytes(2, "big") for value in padded_times)
    )
    framed = _frame(body)
    assert len(framed) == st9030_proxy.TELEMETRY_REPLY_SIZE == 66
    return framed


def test_target_and_agent_validation_are_fail_closed(monkeypatch):
    image = b"reviewed"
    monkeypatch.setattr(st9030_proxy, "TARGET_IMAGE_SIZE", len(image))
    monkeypatch.setattr(
        st9030_proxy, "TARGET_IMAGE_SHA256", hashlib.sha256(image).hexdigest()
    )
    assert st9030_proxy.validate_target_image(image) == image
    with pytest.raises(st9030_proxy.St9030Error, match="target mismatch"):
        st9030_proxy.validate_target_image(b"changed")
    with pytest.raises(st9030_proxy.St9030Error, match="1..2048"):
        st9030_proxy._validate_agent_payload(b"")
    with pytest.raises(st9030_proxy.St9030Error, match="1..2048"):
        st9030_proxy._validate_agent_payload(b"x" * 2049)


def test_frozen_st9030_agent_matches_manifest():
    payload = st9030_proxy.load_st9030_agent()
    assert len(payload) == 1944
    assert hashlib.sha256(payload).hexdigest() == (
        "cd43358bde39c4e2a5dd00884b7775df1662802d08886df9a209027c32706ee2"
    )


def test_agent_gate_buffers_are_disjoint_and_within_transient_window():
    source = Path(st9030_proxy.__file__).with_name("st9030_agent_build.asm")
    text = source.read_text(encoding="utf-8")
    addresses = {
        name: int(re.search(rf"^{name}\s+EQU\s+([0-9A-F]+)h$", text, re.M)[1], 16)
        for name in ("BUF0", "GATETX", "GATE10E", "TELTIMES", "TELTERM")
    }
    spans = (
        range(addresses["BUF0"], addresses["BUF0"] + 30),
        range(addresses["GATETX"], addresses["GATETX"] + 11),
        range(addresses["GATE10E"], addresses["GATE10E"] + 2),
        range(addresses["TELTIMES"], addresses["TELTIMES"] + 30),
        range(addresses["TELTERM"], addresses["TELTERM"] + 2),
    )
    assert all(0xD800 <= span.start < span.stop <= 0xE400 for span in spans)
    occupied = [set(span) for span in spans]
    assert not any(
        occupied[left] & occupied[right]
        for left in range(len(occupied))
        for right in range(left + 1, len(occupied))
    )


def test_stock_gate_configures_asc1_once_and_keeps_one_session():
    source = Path(st9030_proxy.__file__).with_name("st9030_agent_build.asm")
    text = source.read_text(encoding="utf-8")
    gate = text.split("c_gate:\n", 1)[1].split("gate_clear:\n", 1)[0]
    assert gate.count("calls s1_init") == 1
    assert gate.count("calls s1_read_fixed_noinit") == 2
    assert gate.count("calls s1_send_gate_noinit") == 1


def test_stock_gate_captures_crc_tail_before_clearing_transcript_buffers():
    source = Path(st9030_proxy.__file__).with_name("st9030_agent_build.asm")
    text = source.read_text(encoding="utf-8")
    gate = text.split("c_gate:\n", 1)[1].split("gate_clear:\n", 1)[0]
    request, _rest = gate.split("calls gate_clear", 1)
    assert "mov   r11,#4" in request
    assert request.count("calls rx") == 3
    assert request.rstrip().endswith("or    r6,r4")


def test_stock_telemetry_is_one_fixed_session_and_captures_request_first():
    source = Path(st9030_proxy.__file__).with_name("st9030_agent_build.asm")
    text = source.read_text(encoding="utf-8")
    telemetry = text.split("c_telemetry:\n", 1)[1].split("tele_reply:\n", 1)[0]
    request, operation = telemetry.split("calls gate_clear", 1)
    assert "mov   r11,#4" in request
    assert request.count("calls rx") == 3
    assert request.rstrip().endswith("or    r6,r4")
    assert operation.count("calls s1_init") == 1
    assert operation.count("mov   r4,#010Eh") == 1
    assert "mov   r4,#010Dh" not in operation
    assert operation.index("mov   r14,0FE52h") < operation.index(
        "calls s1_send_telemetry_noinit"
    )
    assert operation.index("mov   r8,0FE52h") < operation.index(
        "calls s1_read_fixed_noinit"
    )
    assert "mov   r10,#0FFFFh" in operation
    assert "cmp   r9,#019h" in operation
    assert "cmp   r9,#0177h" in operation
    assert "cmp   r4,#15" in operation
    assert "sub   r9,r14" in operation
    assert operation.count("and   r6,#0FF00h") == 1
    assert "srvwdt" in operation
    assert operation.index("mov   WORDCOUNT,r4") < operation.index("tele_crc_loop:")

    reply = text.split("tele_reply:\n", 1)[1].split("c_gate:\n", 1)[0]
    assert reply.count("mov   r11,#15") == 2

    clear = text.split("gate_clear:\n", 1)[1].split("s1_init:\n", 1)[0]
    assert "mov   r11,#96" in clear

    sender = text.split("s1_send_telemetry_noinit:\n", 1)[1].split(
        "s1_rx_next:\n", 1
    )[0]
    assert "mov   r7,#010Bh" in sender
    assert "mov   r7,#2" in sender
    assert "mov   r11,#3" in sender


def test_exact_target_mismatch_stops_before_payload_or_serial(monkeypatch):
    monkeypatch.setattr(
        st9030_proxy,
        "load_st9030_agent",
        lambda: pytest.fail("payload must not load for a mismatched target"),
    )
    with pytest.raises(st9030_proxy.St9030Error, match="target mismatch"):
        st9030_proxy.reconnaissance("COM1", b"wrong")


def test_identify_and_snapshot_have_exact_crc_protected_formats():
    fake = _FakeSoftBSL([_identity(), _snapshot()])
    protocol = st9030_proxy.St9030Protocol(fake)
    assert protocol.identify()["slot_count"] == 7
    snapshot = protocol.snapshot()
    assert snapshot["registers"] == {
        "S1CON": 0x801C,
        "S1BG": 1,
        "S1TIC": 0,
        "S1RIC": 0,
        "S1EIC": 0x53,
    }
    assert fake.sent == [b"i", b"s"]


def test_identify_rejects_crc_and_contract_drift():
    with pytest.raises(st9030_proxy.St9030Error, match="CRC mismatch"):
        st9030_proxy.St9030Protocol(
            _FakeSoftBSL([_identity()[:-1] + b"\x00"])
        ).identify()
    changed = _frame(bytes((4, 0x0F, 8, 0)))
    with pytest.raises(st9030_proxy.St9030Error, match="unsupported"):
        st9030_proxy.St9030Protocol(_FakeSoftBSL([changed])).identify()


def test_replay_sends_one_crc_protected_allowlisted_request_and_keeps_ninth_bit():
    header, tail = _replay()
    fake = _FakeSoftBSL([header, tail])
    result = st9030_proxy.St9030Protocol(fake).replay(0)
    body = b"r\x00"
    assert fake.sent == [_frame(body)]
    assert result["words"] == [0x101, 0x1FF]


@pytest.mark.parametrize("slot", (-1, 7, 255, True, "0"))
def test_replay_denies_every_non_allowlisted_slot_before_transmit(slot):
    fake = _FakeSoftBSL()
    with pytest.raises(st9030_proxy.St9030Error, match="slot denied"):
        st9030_proxy.St9030Protocol(fake).replay(slot)
    assert fake.sent == []


def test_replay_accepts_bounded_timeout_status_but_not_oversize():
    header, tail = _replay(slot=2, status=4, words=())
    result = st9030_proxy.St9030Protocol(_FakeSoftBSL([header, tail])).replay(2)
    assert result["status_name"] == "asc1_rx_timeout"
    assert result["actual_len"] == 0

    header = bytes((0, 5, 13))
    fake = _FakeSoftBSL([header])
    with pytest.raises(st9030_proxy.St9030Error, match="exceeds 12"):
        st9030_proxy.St9030Protocol(fake).replay(5)
    assert len(fake.sent) == 1


def test_largest_replay_reply_is_exactly_29_bytes():
    words = tuple(0x100 + value for value in range(12))
    header, tail = _replay(slot=5, words=words)
    assert len(header) + len(tail) == st9030_proxy.MAX_REPLAY_REPLY == 29
    result = st9030_proxy.St9030Protocol(_FakeSoftBSL([header, tail])).replay(5)
    assert result["words"] == list(words)


def test_stock_gate_sends_only_fixed_crc_magic_request_and_parses_success():
    fake = _FakeSoftBSL([_gate()])
    result = st9030_proxy.St9030Protocol(fake).stock_gate()
    assert fake.sent == [_frame(b"gST90")]
    assert result["completed"] is True
    assert result["pending"] is False
    assert bytes(result["challenge"]) == b"65772052030"
    assert bytes(result["derived_response"]) == b"72052030657"
    assert result["response_transmit_complete"] is True
    assert result["acknowledgement_word"] == 0xA0


def test_stock_gate_reports_one_shot_a1_as_pending_not_completion():
    result = st9030_proxy.St9030Protocol(
        _FakeSoftBSL([_gate(status=0x0F, acknowledgement=0xA1)])
    ).stock_gate()
    assert result["status_name"] == "command_10e_pending_a1"
    assert result["completed"] is False
    assert result["pending"] is True
    assert result["response_transmit_complete"] is True

    partial = st9030_proxy.St9030Protocol(
        _FakeSoftBSL([_gate(status=0x09, acknowledgement=0)])
    ).stock_gate()
    assert partial["status_name"] == "command_10c_payload_tx_timeout"
    assert partial["response_transmit_complete"] is False


def test_stock_gate_rejects_crc_and_transcript_drift():
    bad_crc = _gate()[:-1] + b"\x00"
    with pytest.raises(st9030_proxy.St9030Error, match="CRC mismatch"):
        st9030_proxy.St9030Protocol(_FakeSoftBSL([bad_crc])).stock_gate()
    with pytest.raises(st9030_proxy.St9030Error, match="fixed rotation"):
        st9030_proxy.St9030Protocol(
            _FakeSoftBSL([_gate(transmitted=b"wrongrotate")])
        ).stock_gate()


def test_stock_telemetry_sends_only_fixed_request_and_reports_ready_observed():
    fake = _FakeSoftBSL([
        _telemetry(
            count=3,
            terminal=0x4B,
            words=(0xA1, 0xA1, 0xA0),
            times=(0x19, 0x32, 0x4B),
        )
    ])
    result = st9030_proxy.St9030Protocol(fake).stock_telemetry()
    assert fake.sent == [_frame(b"tST0B")]
    assert result == {
        "status": 0,
        "status_name": "ready_a0_observed",
        "ready_observed": True,
        "attempt_count": 3,
        "received_count": 3,
        "terminal_delta_ticks": 0x4B,
        "status_words": [0xA1, 0xA1, 0xA0],
        "observation_delta_ticks": [0x19, 0x32, 0x4B],
    }


@pytest.mark.parametrize("status", (1, 2, 3, 4, 5))
def test_stock_telemetry_preserves_pre_poll_failures(status):
    terminal = 0 if status < 3 else 7
    result = st9030_proxy.St9030Protocol(
        _FakeSoftBSL([_telemetry(status=status, count=0, terminal=terminal, words=(), times=())])
    ).stock_telemetry()
    assert result["status_name"] == st9030_proxy.TELEMETRY_STATUS[status]
    assert result["attempt_count"] == result["received_count"] == 0
    assert result["ready_observed"] is False


@pytest.mark.parametrize("status", (6, 7, 8))
def test_stock_telemetry_transport_failure_preserves_only_prior_replies(status):
    result = st9030_proxy.St9030Protocol(
        _FakeSoftBSL([
            _telemetry(
                status=status,
                count=2,
                terminal=0x20,
                words=(0xA1,),
                times=(0x19,),
            )
        ])
    ).stock_telemetry()
    assert result["attempt_count"] == 2
    assert result["received_count"] == 1
    assert result["status_words"] == [0xA1]


@pytest.mark.parametrize(
    ("status", "word", "name"),
    (
        (0x09, 0x100, "poll_10e_high_bits_set"),
        (0x0A, 0xFF, "poll_10e_explicit_fail_ff"),
        (0x0B, 0x55, "poll_10e_unexpected_status"),
    ),
)
def test_stock_telemetry_preserves_bounded_terminal_status(status, word, name):
    result = st9030_proxy.St9030Protocol(
        _FakeSoftBSL([_telemetry(status=status, words=(word,))])
    ).stock_telemetry()
    assert result["status_name"] == name
    assert result["status_words"] == [word]
    assert result["ready_observed"] is False


def test_stock_telemetry_preserves_stall_and_late_reply_as_non_ready():
    stalled = st9030_proxy.St9030Protocol(
        _FakeSoftBSL([
            _telemetry(status=0x0C, count=1, terminal=0x18, words=(0xA0,), times=(0x18,))
        ])
    ).stock_telemetry()
    assert stalled["status_name"] == "fe52_stall_guard"
    assert stalled["ready_observed"] is False

    expired = st9030_proxy.St9030Protocol(
        _FakeSoftBSL([
            _telemetry(
                status=0x0D,
                count=2,
                terminal=0x177,
                words=(0xA1, 0xA0),
                times=(0x19, 0x177),
            )
        ])
    ).stock_telemetry()
    assert expired["status_name"] == "fe52_overall_expired"
    assert expired["status_words"][-1] == 0xA0
    assert expired["ready_observed"] is False


def test_stock_telemetry_accepts_zero_attempt_overall_expiry():
    result = st9030_proxy.St9030Protocol(
        _FakeSoftBSL([
            _telemetry(status=0x0D, count=0, terminal=0x177, words=(), times=())
        ])
    ).stock_telemetry()
    assert result["attempt_count"] == 0
    assert result["status_words"] == []


def test_stock_telemetry_accepts_expiry_before_retry_after_prior_a1():
    result = st9030_proxy.St9030Protocol(
        _FakeSoftBSL([
            _telemetry(
                status=0x0D,
                count=1,
                terminal=0x177,
                words=(0xA1,),
                times=(0x176,),
            )
        ])
    ).stock_telemetry()
    assert result["terminal_delta_ticks"] == 0x177
    assert result["observation_delta_ticks"] == [0x176]


def test_stock_telemetry_attempt_cap_is_an_unreachable_invariant_guard():
    words = (0xA1,) * 15
    times = tuple(0x19 * (index + 1) for index in range(15))
    with pytest.raises(st9030_proxy.St9030Error, match="beyond the overall deadline"):
        st9030_proxy.St9030Protocol(
            _FakeSoftBSL([
                _telemetry(
                    status=0x0E,
                    count=15,
                    terminal=times[-1],
                    words=words,
                    times=times,
                )
            ])
        ).stock_telemetry()


def test_stock_telemetry_rejects_unknown_status_short_reply_and_crc():
    with pytest.raises(st9030_proxy.St9030Error, match="unknown.*status"):
        st9030_proxy.St9030Protocol(
            _FakeSoftBSL([_telemetry(status=0x0F)])
        ).stock_telemetry()

    fake = _FakeSoftBSL()
    fake.ds2._read_exact = lambda length, _timeout: b"\x00" * (length - 1)
    with pytest.raises(st9030_proxy.St9030Error, match="short.*reply"):
        st9030_proxy.St9030Protocol(fake).stock_telemetry()

    bad_crc = _telemetry()[:-1] + b"\x00"
    with pytest.raises(st9030_proxy.St9030Error, match="CRC mismatch"):
        st9030_proxy.St9030Protocol(_FakeSoftBSL([bad_crc])).stock_telemetry()


@pytest.mark.parametrize(
    ("frame", "message"),
    (
        (_telemetry(status=0, count=2, terminal=0x32, words=(0xA0, 0xA0), times=(0x19, 0x32)), "continued after"),
        (_telemetry(status=0, count=2, terminal=0x31, words=(0xA1, 0xA0), times=(0x19, 0x31)), "not FE52-paced"),
        (_telemetry(status=0, count=1, words=(0xA0, 0x55)), "unused transcript"),
        (_telemetry(status=0x09, words=(0xA0,)), "lacks a ninth-bit"),
        (_telemetry(status=0x09, words=(0x200,)), "exceeds nine bits"),
        (_telemetry(status=0x0D, count=1, terminal=0x178, words=(0xA0,), times=(0x177,)), "advanced after"),
    ),
)
def test_stock_telemetry_rejects_transcript_drift(frame, message):
    with pytest.raises(st9030_proxy.St9030Error, match=message):
        st9030_proxy.St9030Protocol(_FakeSoftBSL([frame])).stock_telemetry()


def test_stock_telemetry_rejects_attempt_count_over_fixed_bound():
    with pytest.raises(st9030_proxy.St9030Error, match="exceeds 15"):
        st9030_proxy.St9030Protocol(
            _FakeSoftBSL([_telemetry(status=0x0D, count=16, terminal=0x177, words=(), times=())])
        ).stock_telemetry()


@pytest.mark.parametrize("status", (1, 2))
def test_stock_telemetry_request_failure_rejects_stale_attempt_transcript(status):
    with pytest.raises(st9030_proxy.St9030Error, match="pre-poll failure has attempts"):
        st9030_proxy.St9030Protocol(
            _FakeSoftBSL([
                _telemetry(
                    status=status,
                    count=1,
                    terminal=0x19,
                    words=(0xA1,),
                    times=(0x19,),
                )
            ])
        ).stock_telemetry()


def test_stock_gate_cannot_be_combined_with_replay_before_serial(monkeypatch):
    monkeypatch.setattr(st9030_proxy, "validate_target_image", lambda _i: b"image")
    monkeypatch.setattr(
        st9030_proxy,
        "load_st9030_agent",
        lambda: pytest.fail("conflicting operations must stop before agent load"),
    )
    with pytest.raises(st9030_proxy.St9030Error, match="cannot be combined"):
        st9030_proxy.reconnaissance(
            "COM1", b"ignored", slots=(5,), stock_gate=True
        )


@pytest.mark.parametrize(
    "kwargs",
    (
        {"slots": (0,), "stock_telemetry": True},
        {"stock_gate": True, "stock_telemetry": True},
    ),
)
def test_stock_telemetry_cannot_be_combined_before_agent_load(monkeypatch, kwargs):
    monkeypatch.setattr(st9030_proxy, "validate_target_image", lambda _i: b"image")
    monkeypatch.setattr(
        st9030_proxy,
        "load_st9030_agent",
        lambda: pytest.fail("conflicting operations must stop before agent load"),
    )
    with pytest.raises(st9030_proxy.St9030Error, match="cannot be combined"):
        st9030_proxy.reconnaissance("COM1", b"ignored", **kwargs)


def test_q_and_R_exit_frames_are_fixed():
    fake = _FakeSoftBSL()
    protocol = st9030_proxy.St9030Protocol(fake)
    assert protocol.quit_to_normal() is True
    assert protocol.recover_to_normal() is True
    assert fake.sent == [b"q\xC3\x3C", b"R\x9C\x9C"]


def _admission(variant="MS41.3"):
    return SimpleNamespace(program_variant=variant, port="COM1")


def test_high_entry_may_fallback_to_low_only_before_identify(monkeypatch):
    calls = []
    interface = SimpleNamespace(close=lambda: None)

    def open_session(_port, _log, **kwargs):
        calls.append(kwargs["baud_tier"])
        if kwargs["baud_tier"] == "high":
            raise RuntimeError("fast entry unavailable")
        return interface, object()

    class Protocol:
        def __init__(self, _softbsl):
            pass

        def identify(self):
            return {"version": 1}

    monkeypatch.setattr(st9030_proxy.eeprom_ram, "preflight", lambda _p: _admission())
    monkeypatch.setattr(st9030_proxy.softbsl_service, "_open_session", open_session)
    monkeypatch.setattr(st9030_proxy, "St9030Protocol", Protocol)
    _, _, protocol = st9030_proxy._open_agent(
        "COM1", "auto", lambda *_args: None, b"agent"
    )
    assert calls == ["high", "low"]
    assert protocol.baud_tier == "low"


def test_identify_failure_never_retries_another_baud(monkeypatch):
    calls = []
    events = []
    interface = SimpleNamespace(close=lambda: events.append("close"))

    def open_session(_port, _log, **kwargs):
        calls.append(kwargs["baud_tier"])
        return interface, object()

    class Protocol:
        def __init__(self, _softbsl):
            pass

        def identify(self):
            events.append("identify")
            raise st9030_proxy.St9030Error("bad identify")

        def quit_to_normal(self):
            events.append("quit")
            return True

    monkeypatch.setattr(st9030_proxy.eeprom_ram, "preflight", lambda _p: _admission())
    monkeypatch.setattr(st9030_proxy.softbsl_service, "_open_session", open_session)
    monkeypatch.setattr(st9030_proxy, "St9030Protocol", Protocol)
    with pytest.raises(st9030_proxy.St9030Error, match="bad identify"):
        st9030_proxy._open_agent("COM1", "auto", lambda *_a: None, b"agent")
    assert calls == ["high"]
    assert events == ["identify", "quit", "close"]


def test_identify_reset_failure_survives_interface_close_failure(monkeypatch):
    def close():
        raise RuntimeError("close failed")

    interface = SimpleNamespace(close=close)

    class Protocol:
        def __init__(self, _softbsl):
            pass

        def identify(self):
            raise st9030_proxy.St9030Error("bad identify")

        def quit_to_normal(self):
            return False

    monkeypatch.setattr(st9030_proxy.eeprom_ram, "preflight", lambda _p: _admission())
    monkeypatch.setattr(
        st9030_proxy.softbsl_service,
        "_open_session",
        lambda *_a, **_k: (interface, object()),
    )
    monkeypatch.setattr(st9030_proxy, "St9030Protocol", Protocol)
    with pytest.raises(st9030_proxy.St9030ResetRequired):
        st9030_proxy._open_agent("COM1", "high", lambda *_a: None, b"agent")


def test_reconnaissance_defaults_to_snapshot_and_always_exits(monkeypatch):
    events = []
    protocol = SimpleNamespace(
        agent_sha256="agent-sha",
        baud_tier="high",
        identity={"version": 1},
        snapshot=lambda: events.append("snapshot") or {"status": 1},
        replay=lambda _slot: pytest.fail("default must not replay"),
        quit_to_normal=lambda: events.append("quit") or True,
    )
    interface = SimpleNamespace(close=lambda: events.append("close"))
    monkeypatch.setattr(st9030_proxy, "validate_target_image", lambda _i: b"image")
    monkeypatch.setattr(st9030_proxy, "load_st9030_agent", lambda: b"agent")
    monkeypatch.setattr(
        st9030_proxy,
        "_open_agent",
        lambda *_a, **_k: (_admission(), interface, protocol),
    )
    result = st9030_proxy.reconnaissance("COM1", b"ignored")
    assert result["replies"] == []
    assert result["stock_telemetry"] is None
    assert events == ["snapshot", "quit", "close"]


def test_reconnaissance_runs_only_the_selected_fixed_telemetry(monkeypatch):
    events = []
    protocol = SimpleNamespace(
        agent_sha256="agent-sha",
        baud_tier="high",
        identity={"version": 3},
        snapshot=lambda: events.append("snapshot") or {"status": 0},
        replay=lambda _slot: pytest.fail("telemetry must not replay a slot"),
        stock_telemetry=lambda: events.append("telemetry") or {"status": 0},
        quit_to_normal=lambda: events.append("quit") or True,
    )
    interface = SimpleNamespace(close=lambda: events.append("close"))
    monkeypatch.setattr(st9030_proxy, "validate_target_image", lambda _i: b"image")
    monkeypatch.setattr(st9030_proxy, "load_st9030_agent", lambda: b"agent")
    monkeypatch.setattr(
        st9030_proxy,
        "_open_agent",
        lambda *_a, **_k: (_admission(), interface, protocol),
    )
    result = st9030_proxy.reconnaissance(
        "COM1", b"ignored", stock_telemetry=True
    )
    assert result["replies"] == []
    assert result["stock_gate"] is None
    assert result["stock_telemetry"] == {"status": 0}
    assert events == ["snapshot", "telemetry", "quit", "close"]


def test_reconnaissance_failure_still_quits_and_closes(monkeypatch):
    events = []
    protocol = SimpleNamespace(
        agent_sha256="agent-sha",
        baud_tier="high",
        identity={"version": 1},
        snapshot=lambda: (_ for _ in ()).throw(st9030_proxy.St9030Error("failed")),
        quit_to_normal=lambda: events.append("quit") or True,
    )
    interface = SimpleNamespace(close=lambda: events.append("close"))
    monkeypatch.setattr(st9030_proxy, "validate_target_image", lambda _i: b"image")
    monkeypatch.setattr(st9030_proxy, "load_st9030_agent", lambda: b"agent")
    monkeypatch.setattr(
        st9030_proxy,
        "_open_agent",
        lambda *_a, **_k: (_admission(), interface, protocol),
    )
    with pytest.raises(st9030_proxy.St9030Error, match="failed"):
        st9030_proxy.reconnaissance("COM1", b"ignored")
    assert events == ["quit", "close"]


def test_reconnaissance_closes_interface_when_quit_raises(monkeypatch):
    events = []

    def quit_to_normal():
        events.append("quit")
        raise st9030_proxy.St9030Error("quit failed")

    protocol = SimpleNamespace(
        agent_sha256="agent-sha",
        baud_tier="high",
        identity={"version": 1},
        snapshot=lambda: {"status": 0},
        replay=lambda _slot: pytest.fail("default must not replay"),
        quit_to_normal=quit_to_normal,
    )
    interface = SimpleNamespace(close=lambda: events.append("close"))
    monkeypatch.setattr(st9030_proxy, "validate_target_image", lambda _i: b"image")
    monkeypatch.setattr(st9030_proxy, "load_st9030_agent", lambda: b"agent")
    monkeypatch.setattr(
        st9030_proxy,
        "_open_agent",
        lambda *_a, **_k: (_admission(), interface, protocol),
    )
    with pytest.raises(
        st9030_proxy.St9030ResetRequired,
        match="normal DS2 reset was not confirmed",
    ):
        st9030_proxy.reconnaissance("COM1", b"ignored")
    assert events == ["quit", "close"]


def test_unconfirmed_reset_is_not_masked_when_interface_close_also_fails(monkeypatch):
    protocol = SimpleNamespace(
        agent_sha256="agent-sha",
        baud_tier="high",
        identity={"version": 3},
        snapshot=lambda: {"status": 0},
        quit_to_normal=lambda: False,
    )

    def close():
        raise RuntimeError("close failed")

    monkeypatch.setattr(st9030_proxy, "validate_target_image", lambda _i: b"image")
    monkeypatch.setattr(st9030_proxy, "load_st9030_agent", lambda: b"agent")
    monkeypatch.setattr(
        st9030_proxy,
        "_open_agent",
        lambda *_a, **_k: (_admission(), SimpleNamespace(close=close), protocol),
    )
    with pytest.raises(st9030_proxy.St9030ResetRequired):
        st9030_proxy.reconnaissance("COM1", b"ignored")
