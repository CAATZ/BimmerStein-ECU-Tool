import os, sys
import types
import pytest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def test_softbsl_package_imports_cleanly():
    from engines.softbsl import softbsl_host
    assert hasattr(softbsl_host, "SoftBSL")
    assert hasattr(softbsl_host, "SoftBSLError")


def test_internal_softbsl_ds2_wrapper_reexports_the_canonical_transport():
    import ds2 as flasher_ds2
    from engines.softbsl import ds2 as softbsl_ds2
    # The compatibility module has its own import path, but no duplicate transport.
    assert flasher_ds2 is not softbsl_ds2
    assert softbsl_ds2.DS2Interface is flasher_ds2.DS2Interface


def test_softbsl_runtime_agents_are_packaged():
    from engines.softbsl import softbsl_host
    pkg = os.path.dirname(os.path.abspath(softbsl_host.__file__))
    assert os.path.exists(os.path.join(pkg, "agent.hex"))
    assert os.path.exists(os.path.join(pkg, "agent_28f.hex"))
    assert os.path.exists(os.path.join(pkg, "stage1_payload.hex"))
    assert os.path.exists(os.path.join(pkg, "stage1_manifest.json"))


def test_staged_entry_payload_matches_its_production_manifest():
    from engines.softbsl import softbsl_host
    payload = softbsl_host.load_stage_payload()
    assert len(payload) == 464
    assert payload[:4] == bytes.fromhex("be88e609")


def test_both_agents_finalize_marker0_before_hybrid_srst():
    from engines.softbsl import softbsl_host
    pkg = os.path.dirname(os.path.abspath(softbsl_host.__file__))
    # marker 0 + EEPROM commit; minimum WDT fallback; SRVWDT; protected SRST; fallback spin
    finalize = bytes.fromhex(
        "e108f7f840e7da00621ae6d700ffa758a7a7b748b7b70dff")
    old_watchdog_finalize = bytes.fromhex("e108f7f840e7da00621a0dff")
    for name, expected_size in (("agent.hex", 1498), ("agent_28f.hex", 1464)):
        agent = softbsl_host.load_agent(os.path.join(pkg, name))
        assert len(agent) == expected_size
        assert agent.count(finalize) == 1
        assert old_watchdog_finalize not in agent
        assert len(agent) <= 1500       # remain inside the loader size ceiling


def test_both_agents_allow_the_same_ram_writer_on_top_and_bottom():
    from engines.softbsl import softbsl_host
    pkg = os.path.dirname(os.path.abspath(softbsl_host.__file__))
    # policy_check keeps the marker read/CMP for layout stability, but the branch to
    # pc_bot is unconditional (0D) instead of the old top-denying cc_NE (3D).
    allowed = bytes.fromhex("f3f802e449810d02e118db004890")
    denied = bytes.fromhex("f3f802e449813d02e118db004890")
    for name in ("agent.hex", "agent_28f.hex"):
        agent = softbsl_host.load_agent(os.path.join(pkg, name))
        assert agent.count(allowed) == 1
        assert denied not in agent


def test_agent_build_inputs_are_packaged_with_the_runtime_payloads():
    from engines.softbsl import softbsl_host
    pkg = os.path.dirname(os.path.abspath(softbsl_host.__file__))
    for name in (
        "agent_build.asm", "agent_28f_build.asm", "preprocess_asm.py",
        "agent_manifest.json", "verify_agent_artifacts.py", "BUILDING_AGENTS.md",
        os.path.join("tools", "AssembleC166.java"),
    ):
        assert os.path.exists(os.path.join(pkg, name))


def test_agent_sources_and_runtime_payloads_match_the_reviewed_manifest():
    from engines.softbsl.verify_agent_artifacts import verify_manifest
    assert verify_manifest() == []


def test_broken_relocated_loader_preflight_matches_deprecated_descriptor():
    from engines.patcher.patch_ms41 import load_patches
    from engines.softbsl import softbsl_host as sh

    patch = load_patches()["softbsl_loader_relocated_v1"]
    crc_edit = next(edit for edit in patch["edits"] if edit["off"] == 0x5C32)

    assert sh._RELOCATED_V1_CRC_CPU == (crc_edit["off"] ^ sh.DESCR)
    assert sh._RELOCATED_V1_CRC == bytes.fromhex(crc_edit["data"])


def test_broken_relocated_loader_is_refused_before_the_0x2a_reset():
    from engines.softbsl import softbsl_host as sh

    events = []

    class FakeDS2:
        def read_mem(self, address, length):
            events.append(("read", address, length))
            assert address == sh._RELOCATED_V1_CRC_CPU
            return sh._RELOCATED_V1_CRC

        def send_no_response(self, command):
            events.append(("send", command))

    sb = sh.SoftBSL(FakeDS2(), log=lambda _line: None)
    with pytest.raises(sh.SoftBSLError, match="non-triggering relocated v1"):
        sb.ensure_flash_mode(wait=0)

    assert events == [
        ("read", sh._RELOCATED_V1_CRC_CPU, len(sh._RELOCATED_V1_CRC))]


def test_polled_flash_mode_entry_uses_quiet_guard_then_waits_for_e740(monkeypatch):
    from engines.softbsl import softbsl_host as sh

    events = []

    class FakeSerial:
        def reset_input_buffer(self):
            events.append(("flush",))

    class FakeDS2:
        _ser = FakeSerial()

        def __init__(self):
            self.polls = 0

        def read_mem(self, address, length):
            events.append(("read", address, length))
            if address == sh._RELOCATED_V1_CRC_CPU:
                return b"\x00" * length
            assert address == 0xE740 and length == 1
            return b"\x00"

        def send_no_response(self, command):
            events.append(("send", command))

        def execute(self, command, args, timeout):
            self.polls += 1
            events.append(("poll", command, args, timeout))
            if self.polls < 3:
                raise RuntimeError("rebooting")
            return b"\x01"

    sleeps = []
    monkeypatch.setattr(sh.time, "sleep", sleeps.append)
    sb = sh.SoftBSL(FakeDS2(), log=lambda _line: None)

    assert sb.ensure_flash_mode(poll_ready=True) is True
    assert sleeps == [0.6, 0.02, 0.02]
    assert events == [
        ("read", sh._RELOCATED_V1_CRC_CPU, len(sh._RELOCATED_V1_CRC)),
        ("read", 0xE740, 1),
        ("send", 0x2A),
        ("poll", sh.ds2.DS2Commands.READ_MEM,
         (0xE740).to_bytes(4, "big") + b"\x01", 0.05),
        ("poll", sh.ds2.DS2Commands.READ_MEM,
         (0xE740).to_bytes(4, "big") + b"\x01", 0.05),
        ("poll", sh.ds2.DS2Commands.READ_MEM,
         (0xE740).to_bytes(4, "big") + b"\x01", 0.05),
        ("flush",),
    ]


def test_runtime_calguard_detector_does_not_use_the_abhishek_credit():
    from engines.softbsl import softbsl_host as sh

    memory = {
        0x133BB: b"\xFF" * 5,
        0x15F60: b"ABHISHEK",
        0x1000E: b"12000000",
        0x3DA9A: sh._PROG_SIG_3,
        0x2025: b"1406464",
    }

    class FakeDS2:
        def read_mem(self, address, length):
            return memory.get(address, b"\xFF" * length)[:length]

    assert sh._detect_ecu_variant(FakeDS2()) == (
        "MS41.3", "MS41.3", True)
    assert sh._detect_ecu_variant(FakeDS2(), accept_credit=False) == (
        "MS41.3", "MS41.3", True)


def test_calguard_mismatch_selects_direct_entry_without_marker_one(monkeypatch):
    from engines.softbsl import softbsl_host as sh

    class FakeDS2:
        def read_mem(self, address, length):
            assert (address, length) == (0xE740, 1)
            return b"\x03"

    logs = []
    sb = sh.SoftBSL(FakeDS2(), log=logs.append)
    monkeypatch.setattr(sh, "_live_patch_applied", lambda _ds2, patch_id: patch_id == "cal_guard")
    monkeypatch.setattr(
        sh, "_detect_ecu_variant",
        lambda _ds2, **_kwargs: ("MS41.2", "MS41.1", False))
    monkeypatch.setattr(
        sh, "_detect_firmware_compatibility",
        lambda _ds2: ("0960", "0960", b"909", True))

    assert sb.calguard_direct_entry_ready() is True
    assert any("without 0x2A" in line for line in logs)


def test_live_calguard_check_reads_every_exact_descriptor_byte():
    from engines.patcher.patch_ms41 import load_patches
    from engines.softbsl import softbsl_host as sh

    patch = load_patches()["cal_guard"]
    expected = {
        int(edit["off"]) ^ sh.DESCR: bytes.fromhex(edit["data"])
        for edit in patch["edits"]
    }
    reads = []

    class FakeDS2:
        def read_memory_range(self, address, length):
            reads.append((address, length))
            return expected[address]

    assert sh._live_patch_applied(FakeDS2(), "cal_guard") is True
    assert reads == [
        (address, len(data)) for address, data in expected.items()
    ]


def test_consistent_calguard_image_keeps_normal_entry(monkeypatch):
    from engines.softbsl import softbsl_host as sh

    class FakeDS2:
        def read_mem(self, address, length):
            assert (address, length) == (0xE740, 1)
            return b"\x03"

    sb = sh.SoftBSL(FakeDS2(), log=lambda _line: None)
    monkeypatch.setattr(sh, "_live_patch_applied", lambda _ds2, _patch_id: True)
    monkeypatch.setattr(
        sh, "_detect_ecu_variant",
        lambda _ds2, **_kwargs: ("MS41.1", "MS41.1", True))
    monkeypatch.setattr(
        sh, "_detect_firmware_compatibility",
        lambda _ds2: ("0960", "0960", b"909", True))

    assert sb.calguard_direct_entry_ready() is False


def test_calguard_exact_id_mismatch_selects_direct_entry(monkeypatch):
    from engines.softbsl import softbsl_host as sh

    class FakeDS2:
        def read_mem(self, address, length):
            assert (address, length) == (0xE740, 1)
            return b"\x03"

    sb = sh.SoftBSL(FakeDS2(), log=lambda _line: None)
    monkeypatch.setattr(sh, "_live_patch_applied", lambda _ds2, _patch_id: True)
    monkeypatch.setattr(
        sh, "_detect_ecu_variant",
        lambda _ds2, **_kwargs: ("MS41.0", "MS41.0", True))
    monkeypatch.setattr(
        sh, "_detect_firmware_compatibility",
        lambda _ds2: ("0641", "0659", b"909", False))

    assert sb.calguard_direct_entry_ready() is True


def test_enter_retry_forwards_the_bounded_ack_timeout(monkeypatch):
    from engines.softbsl import softbsl_host as sh

    serial = types.SimpleNamespace(reset_input_buffer=lambda: None)
    sb = sh.SoftBSL(types.SimpleNamespace(_ser=serial), log=lambda _line: None)
    calls = []

    def fake_enter(agent, trigger="5a", ack_timeout=2.0):
        calls.append((agent, trigger, ack_timeout))
        if len(calls) == 1:
            raise sh.SoftBSLError("not ready")

    monkeypatch.setattr(sb, "enter", fake_enter)
    monkeypatch.setattr(sh.time, "sleep", lambda _seconds: None)

    sb.enter_retry(b"agent", tries=2, gap=0.01, ack_timeout=0.075)
    assert calls == [
        (b"agent", "5a", 0.075),
        (b"agent", "5a", 0.075),
    ]


def test_staged_entry_switches_after_stage_header_and_reaches_normal_agent(monkeypatch):
    from engines.softbsl import softbsl_host as sh

    serial = types.SimpleNamespace(baudrate=9600, reset_input_buffer=lambda: None)
    ds2 = types.SimpleNamespace(_ser=serial, baud=9600, ecu_addr=0x12)
    sb = sh.SoftBSL(ds2, log=lambda _line: None)
    frames = []
    monkeypatch.setattr(sb, "_txs", lambda data: frames.append(bytes(data)))
    responses = iter((sh.ACK, sh.ACK, sh.STAGE_READY, sh.ACK, sh.ACK))
    monkeypatch.setattr(sb, "_rx", lambda _timeout=2.0: next(responses))
    monkeypatch.setattr(sb, "_retune_staged_host", lambda tier: setattr(ds2, "baud", sh.BG[tier][1]))
    handed_off = []
    monkeypatch.setattr(sb, "_stream_and_confirm", lambda agent: handed_off.append(agent))

    sb.enter_staged(b"agent", "high", stage_payload=b"stage")

    assert frames[0]  # DS2 loader trigger
    assert frames[1] == b"stage"
    assert frames[2].startswith(b"S2\x01")
    assert frames[3] == bytes([sh.STAGE_HANDSHAKE])
    assert handed_off == [b"agent"]
    assert ds2.baud == 187500
    assert sb.staged_entry is True


@pytest.mark.parametrize(
    ("echoed", "detail"),
    ((b"\x10", "echo length mismatch"),
     (b"\x10\x21", "echo content mismatch")),
)
def test_raw_send_requires_the_exact_kline_echo(echoed, detail, monkeypatch):
    from engines.softbsl import softbsl_host as sh

    class Serial:
        timeout = 0
        echo_drain_chunk_size = 128
        chunks = [echoed]
        def reset_input_buffer(self): pass
        def write(self, data): return len(data)
        def flush(self): pass
        def read(self, _size):
            return self.chunks.pop(0) if self.chunks else b""

    ds2 = types.SimpleNamespace(_ser=Serial(), baud=9600, echo=True)
    times = iter((0.0, 0.0, 0.0, 3.0))
    monkeypatch.setattr(sh.time, "perf_counter", lambda: next(times, 3.0))

    with pytest.raises(sh.SoftBSLError, match=detail):
        sh.SoftBSL(ds2)._txs(b"\x10\x20")


def test_raw_send_drains_fifo_sized_chunks_and_collects_fragmented_echo(monkeypatch):
    from engines.softbsl import softbsl_host as sh

    payload = bytes(index & 0xFF for index in range(464))

    class Serial:
        timeout = 0
        echo_drain_chunk_size = 128

        def __init__(self):
            self.chunks = []
            self.writes = []
            self.resets = 0

        def reset_input_buffer(self): self.resets += 1
        def write(self, data):
            data = bytes(data)
            self.writes.append(data)
            split = min(80, max(1, len(data) // 2))
            self.chunks.extend((data[:split], b"", data[split:]))
            return len(data)
        def flush(self): pass
        def read(self, size):
            chunk = self.chunks.pop(0)
            assert len(chunk) <= size
            return chunk

    serial = Serial()
    monkeypatch.setattr(sh.time, "sleep", lambda _seconds: None)
    sh.SoftBSL(types.SimpleNamespace(
        _ser=serial, baud=9600, echo=True,
    ))._txs(payload)

    assert serial.writes == [
        payload[:128], payload[128:256], payload[256:384], payload[384:]
    ]
    assert b"".join(serial.writes) == payload
    assert serial.resets == 1
    assert serial.chunks == []


def test_raw_send_stops_before_next_chunk_when_echo_misses_post_wire_deadline(monkeypatch):
    from engines.softbsl import softbsl_host as sh

    now = [0.0]

    class Serial:
        timeout = 0.0
        echo_drain_chunk_size = 128

        def __init__(self):
            self.writes = []
            self.read_timeouts = []

        def reset_input_buffer(self): pass
        def write(self, data):
            data = bytes(data)
            self.writes.append(data)
            now[0] += len(data) * 12 / 9600
            return len(data)
        def flush(self): pass
        def read(self, _size):
            self.read_timeouts.append(self.timeout)
            now[0] += self.timeout
            return b""

    payload = bytes(range(128)) * 2
    serial = Serial()
    monkeypatch.setattr(sh.time, "perf_counter", lambda: now[0])
    monkeypatch.setattr(sh.time, "sleep", lambda _seconds: None)

    with pytest.raises(sh.SoftBSLError, match="echo length mismatch at byte 0"):
        sh.SoftBSL(types.SimpleNamespace(
            _ser=serial, baud=9600, echo=True,
        ))._txs(payload)

    assert serial.writes == [payload[:128]]
    assert serial.read_timeouts
    assert max(serial.read_timeouts) <= 0.01
    assert sum(serial.read_timeouts) == pytest.approx(0.032, abs=0.001)


def test_raw_send_stops_if_descheduled_after_exact_chunk_echo(monkeypatch):
    from engines.softbsl import softbsl_host as sh

    class Serial:
        timeout = 0.0
        echo_drain_chunk_size = 128

        def __init__(self):
            self.writes = []
            self.echo = b""

        def reset_input_buffer(self): pass
        def write(self, data):
            data = bytes(data)
            self.writes.append(data)
            self.echo = data
            return len(data)
        def flush(self): pass
        def read(self, _size):
            echo, self.echo = self.echo, b""
            return echo

    payload = bytes(range(128)) * 2
    serial = Serial()
    times = iter((0.0, 0.0, 0.0, 0.193))
    monkeypatch.setattr(sh.time, "perf_counter", lambda: next(times, 0.193))

    with pytest.raises(
        sh.SoftBSLError,
        match="inter-chunk deadline exceeded before byte 128",
    ):
        sh.SoftBSL(types.SimpleNamespace(
            _ser=serial, baud=9600, echo=True,
        ))._txs(payload)

    assert serial.writes == [payload[:128]]


def test_raw_send_keeps_unflagged_desktop_transport_as_one_write():
    from engines.softbsl import softbsl_host as sh

    payload = bytes(range(256)) * 4 + b"frame"

    class Serial:
        def __init__(self):
            self.writes = []
            self.resets = 0

        def reset_input_buffer(self): self.resets += 1
        def write(self, data): self.writes.append(bytes(data)); return len(data)
        def flush(self): pass

    serial = Serial()
    echo_reads = []

    def read_exact(size, timeout):
        echo_reads.append((size, timeout))
        return payload

    sh.SoftBSL(types.SimpleNamespace(
        _ser=serial, baud=187500, echo=True, _read_exact=read_exact,
    ))._txs(payload)

    assert serial.writes == [payload]
    assert serial.resets == 1
    assert echo_reads == [(
        len(payload), 2.0 + len(payload) * 12 / 187500,
    )]


def test_staged_trigger_timeout_is_labelled_and_never_retransmitted(monkeypatch):
    from engines.softbsl import softbsl_host as sh

    serial = types.SimpleNamespace(baudrate=9600, reset_input_buffer=lambda: None)
    ds2 = types.SimpleNamespace(_ser=serial, baud=9600, ecu_addr=0x12)
    sb = sh.SoftBSL(ds2, log=lambda _line: None)
    frames = []
    monkeypatch.setattr(sb, "_txs", lambda data: frames.append(bytes(data)))
    monkeypatch.setattr(
        sb, "_rx",
        lambda _timeout=2.0: (_ for _ in ()).throw(
            sh.SoftBSLError("timeout waiting for agent byte")
        ),
    )

    with pytest.raises(
        sh.SoftBSLError,
        match="staged 0x5A trigger ACK: timeout waiting for agent byte",
    ):
        sb.enter_staged(b"agent", "high", stage_payload=b"stage")

    assert len(frames) == 1


def test_intel_agent_reuses_the_automatic_program_status_sample():
    """The 28F200 exposes SR automatically; do not add redundant 0x70 bus cycles."""
    from engines.softbsl import softbsl_host
    pkg = os.path.dirname(os.path.abspath(softbsl_host.__file__))
    agent = softbsl_host.load_agent(os.path.join(pkg, "agent_28f.hex"))

    redundant_status_transaction = bytes.fromhex("e6f57000b85ca98c")
    poll_once_and_preserve = bytes.fromhex("a9acf18acc00cc00")
    reuse_ready_sample = bytes.fromhex("f18acc00cc00cc00")

    assert redundant_status_transaction not in agent
    assert agent.count(poll_once_and_preserve) == 1
    assert agent.count(reuse_ready_sample) == 1


def test_finalize_marker0_uses_running_agent_then_confirms_stock_ds2(monkeypatch):
    from engines.softbsl import softbsl_host as sh
    serial = types.SimpleNamespace(baudrate=187500, reset_input_buffer=lambda: None)
    ds2 = types.SimpleNamespace(baud=187500, _ser=serial,
                                execute=lambda command, args, timeout: b"\x00")
    logs = []
    sb = sh.SoftBSL(ds2, log=logs.append)
    sent = []
    sleeps = []
    monkeypatch.setattr(sb, "reset", lambda: sent.append("R 9C 9C"))
    monkeypatch.setattr(sh.time, "sleep", sleeps.append)

    assert sb.finalize_marker0() is True
    assert sent == ["R 9C 9C"]
    assert sleeps == []
    assert ds2.baud == serial.baudrate == 9600
    assert any("confirmed" in line for line in logs)


def test_reset_sends_redundant_magic_as_one_contiguous_burst(monkeypatch):
    from engines.softbsl import softbsl_host as sh
    sb = sh.SoftBSL(types.SimpleNamespace(), log=lambda _line: None)
    sent = []
    monkeypatch.setattr(sb, "_txs", sent.append)

    sb.reset()

    assert sent == [bytes([sh.R, 0x9C, 0x9C]) * 5]


def _fake_softbsl(monkeypatch):
    from engines.softbsl import softbsl_host as sh
    sb = sh.SoftBSL(ds2=types.SimpleNamespace(), log=lambda *a, **k: None)
    monkeypatch.setattr(sb, "select_half", lambda target, prompt, chip=None: None)
    monkeypatch.setattr(sb, "set_baud", lambda tier: None)
    monkeypatch.setattr(sb, "arm_bootloader", lambda: None)
    monkeypatch.setattr(sb, "erase", lambda addr: 1)                 # 1 = OK status
    monkeypatch.setattr(sb, "program_chunk", lambda addr, data: 1)   # 1 = OK status
    monkeypatch.setattr(sb, "crc_read", lambda addr, n: b"\x00" * n) # dummy read-back
    monkeypatch.setattr(sb, "reset", lambda: None)
    return sh, sb


def test_select_half_uses_single_device_label_for_28f200(monkeypatch):
    from engines.softbsl import softbsl_host as sh
    logs = []
    sb = sh.SoftBSL(ds2=types.SimpleNamespace(), log=logs.append)
    monkeypatch.setattr(sb, "identify", lambda: "B")

    sb.select_half("B", lambda _message: None, chip="28f200")
    assert logs == ["image marker: 'B' (working image; Intel 28F200)"]

    logs.clear()
    sb.select_half("B", lambda _message: None, chip="29f400")
    assert logs == ["visible half: 'B' (bottom/working)"]


def test_flash_image_calls_progress_cb_program_and_verify(monkeypatch):
    sh, sb = _fake_softbsl(monkeypatch)
    # image marked 'B' (bottom) so select_half's target check passes; tune scope keeps
    # the run small/fast (SA4 cal only, no param1 involved).
    image = bytearray(b"\xFF" * sh.IMAGE_SIZE)
    image[0x10000:0x10010] = b"\x01" * 16   # a few non-FF bytes inside the tune sector so program runs
    image[sh.MARKER_OFF:sh.MARKER_OFF + 4] = bytes([0xA5, 0x5A, 0x42, 0x42 ^ 0xFF])  # 'B'
    # crc_read (verify's read-back) must echo the just-"programmed" bytes back, or the
    # verify loop legitimately raises a mismatch — the fake reads from `image` itself.
    monkeypatch.setattr(sb, "crc_read", lambda cpu, n: bytes(image[cpu ^ sh.DESCR: (cpu ^ sh.DESCR) + n]))

    calls = []
    sb.flash_image(bytes(image), scope="tune", baud="low",
                   do_verify=True, progress_cb=lambda d, t, l="": calls.append((d, t, l)))

    assert calls, "progress_cb was never called"
    assert calls[0][2] == "erase"          # exact destructive boundary, before the first erase opcode
    assert calls[-1][0] == calls[-1][1]   # final call reaches 100%


def test_flash_image_verifies_with_1k_crc_reads(monkeypatch):
    sh, sb = _fake_softbsl(monkeypatch)
    image = bytearray(b"\xFF" * sh.IMAGE_SIZE)
    image[0x10000:0x10800] = b"\x33" * 0x800
    image[sh.MARKER_OFF:sh.MARKER_OFF + 4] = bytes([0xA5, 0x5A, 0x42, 0xBD])
    reads = []
    def read_back(cpu, size):
        reads.append((cpu, size))
        file_off = cpu ^ sh.DESCR
        return bytes(image[file_off:file_off + size])
    monkeypatch.setattr(sb, "crc_read", read_back)

    sb.flash_image(bytes(image), scope="tune", baud="low", do_verify=True)

    _sectors, lo, hi = sh._flash_scope("tune")
    assert reads == [
        (file_offset ^ sh.DESCR, sh.CHUNK_SIZE)
        for file_offset in range(lo, hi, sh.CHUNK_SIZE)
        if not sh._in_hole(file_offset ^ sh.DESCR)
    ]


def test_read_range_crc_reads_and_reports_progress(monkeypatch):
    sh, sb = _fake_softbsl(monkeypatch)
    seen = []
    reads = []
    def fake_crc_read(cpu, n):
        reads.append((cpu, n))
        return bytes([0xAB]) * n
    monkeypatch.setattr(sb, "crc_read", fake_crc_read)

    out = sb.read_range(0x10000, 0x800, progress_cb=lambda d, t: seen.append((d, t)))

    assert len(out) == 0x800
    assert out == bytes([0xAB]) * 0x800
    assert seen[0] == (0, 0x800)
    assert seen[-1] == (0x800, 0x800)
    assert reads  # crc_read was actually called


def test_read_range_synthesizes_ff_over_the_unmapped_hole(monkeypatch):
    sh, sb = _fake_softbsl(monkeypatch)
    monkeypatch.setattr(sb, "crc_read", lambda cpu, n: (_ for _ in ()).throw(
        AssertionError("crc_read must not be called inside the unmapped hole")))
    # CPU 0xC000-0xFFFF is the unmapped bus hole; file 0x8000-0xBFFF maps there via ^DESCR.
    out = sb.read_range(0x8000, 0x100)
    assert out == b"\xFF" * 0x100


def test_read_range_descramble_true_xors_file_offset_to_cpu(monkeypatch):
    sh, sb = _fake_softbsl(monkeypatch)
    reads = []
    monkeypatch.setattr(sb, "crc_read", lambda cpu, n: reads.append(cpu) or (b"\x00" * n))
    # file 0x10000 with descramble -> cpu 0x10000 ^ 0x4000 = 0x14000
    sb.read_range(0x10000, 0x400, descramble=True)
    assert reads[0] == 0x14000


def test_read_range_descramble_false_reads_raw_cpu_addresses(monkeypatch):
    sh, sb = _fake_softbsl(monkeypatch)
    reads = []
    monkeypatch.setattr(sb, "crc_read", lambda cpu, n: reads.append((cpu, n)) or (b"\x00" * n))
    # raw read of the 24 KB tune @ CPU 0x10000 -- NO ^DESCR; spans the 0x14000 page boundary
    out = sb.read_range(0x10000, 24 * 1024, descramble=False)
    assert len(out) == 24 * 1024
    assert reads[0][0] == 0x10000                     # first read is raw 0x10000, not 0x14000
    assert all(cpu < 0x16000 for cpu, _n in reads)    # never reads past the 24 KB partition
    # each call stays within one 16 KB page (no call crosses 0x14000)
    for cpu, n in reads:
        assert (cpu & ~0x3FFF) == ((cpu + n - 1) & ~0x3FFF)


def test_write_tune_partial_erases_cal_then_writes_24k_at_cpu_0x10000(monkeypatch):
    sh, sb = _fake_softbsl(monkeypatch)
    erased, progs, reads = [], [], []
    monkeypatch.setattr(sb, "erase", lambda addr: erased.append(addr) or 1)
    monkeypatch.setattr(sb, "program_chunk", lambda cpu, data: progs.append((cpu, len(data))) or 1)
    # verify reads back exactly what was "written": echo the partial
    partial = bytes((i * 7) & 0xFF for i in range(sh.TUNE_PARTIAL_SIZE))
    monkeypatch.setattr(
        sb, "crc_read",
        lambda cpu, n: reads.append((cpu, n))
        or partial[cpu - 0x10000: cpu - 0x10000 + n])

    seen = []
    sb.write_tune_partial(partial, do_verify=True, progress_cb=lambda d, t, l="": seen.append((d, t, l)))

    # erased the cal block at CPU 0x10000 (the HW block base), exactly once (no retries needed)
    assert erased == [0x10000]
    # programmed 24 KB starting at CPU 0x10000; partial[i] -> CPU 0x10000+i; each chunk in one page
    assert progs[0][0] == 0x10000
    assert all(0x10000 <= cpu < 0x16000 for cpu, _ln in progs)
    assert sum(ln for _c, ln in progs) == sh.TUNE_PARTIAL_SIZE     # all 24 KB written (no FF to skip here)
    for cpu, ln in progs:
        assert (cpu & ~0x3FFF) == ((cpu + ln - 1) & ~0x3FFF)       # never crosses a 16 KB page
    assert reads == [
        (0x10000 + off, sh.CHUNK_SIZE)
        for off in range(0, sh.TUNE_PARTIAL_SIZE, sh.CHUNK_SIZE)
    ]                                                               # 24 x 1 KB, every byte covered
    assert seen[0][2] == "erase"                                   # emitted before erase is sent
    assert any(l == "program" for _d, _t, l in seen) and any(l == "verify" for _d, _t, l in seen)


def test_crossbank_top_verify_uses_1k_crc_reads_for_every_non_ff_byte(monkeypatch):
    sh, sb = _fake_softbsl(monkeypatch)
    image = bytearray(b"\x33" * sh.IMAGE_SIZE)
    image[0x8000:0xC000] = b"\xFF" * 0x4000       # unmapped ghost region is never transferred
    image[sh.MARKER_OFF:sh.MARKER_OFF + 4] = bytes([0xA5, 0x5A, 0x54, 0xAB])

    monkeypatch.setattr(sh, "_progress_bar", lambda _label: None)
    monkeypatch.setattr(sb, "arm_bootloader", lambda: None)
    monkeypatch.setattr(sb, "erase", lambda _addr: 1)
    monkeypatch.setattr(sb, "program_chunk", lambda _cpu, _data: 1)
    monkeypatch.setattr(sb, "reset", lambda: None)

    guard_count = 0
    verify_reads = []
    progress = []
    def read_back(cpu, size):
        nonlocal guard_count
        if cpu == 0x1FFC and size == 4:
            guard_count += 1
            return b"BOT!" if guard_count == 1 else b"TOP!"
        verify_reads.append((cpu, size))
        file_off = cpu ^ sh.DESCR
        return bytes(image[file_off:file_off + size])
    monkeypatch.setattr(sb, "crc_read", read_back)

    sb.flash_cross_bank(
        bytes(image), baud="low", do_verify=True, prompt=lambda _msg: None,
        progress_cb=lambda done, total, phase: progress.append((done, total, phase)),
    )

    assert guard_count == 2
    assert len(verify_reads) == (sh.IMAGE_SIZE - 0x4000) // sh.CHUNK_SIZE
    assert all(size == sh.CHUNK_SIZE for _cpu, size in verify_reads)
    assert sum(size for _cpu, size in verify_reads) == sh.IMAGE_SIZE - 0x4000
    assert progress[0][2] == "erase"
    assert progress[-1] == (sh.IMAGE_SIZE, sh.IMAGE_SIZE, "verify")


def test_write_tune_partial_skips_all_ff_chunks(monkeypatch):
    sh, sb = _fake_softbsl(monkeypatch)
    progs = []
    monkeypatch.setattr(sb, "erase", lambda addr: 1)
    monkeypatch.setattr(sb, "program_chunk", lambda cpu, data: progs.append(cpu) or 1)
    # a partition that is FF except the first 1 KB chunk -> only that chunk is programmed
    partial = bytearray(b"\xFF" * sh.TUNE_PARTIAL_SIZE)
    partial[:0x400] = b"\x5A" * 0x400
    reads = []
    monkeypatch.setattr(
        sb, "crc_read",
        lambda cpu, n: reads.append(cpu)
        or bytes(partial[cpu - 0x10000: cpu - 0x10000 + n]),
    )

    sb.write_tune_partial(bytes(partial), do_verify=True)
    assert progs == [0x10000]     # only the single non-FF chunk was written; the FF tail was skipped
    assert len(reads) == sh.TUNE_PARTIAL_SIZE // sh.CHUNK_SIZE


def test_write_tune_partial_verify_rejects_residue_in_erased_ff_chunk(monkeypatch):
    sh, sb = _fake_softbsl(monkeypatch)
    partial = b"\xFF" * sh.TUNE_PARTIAL_SIZE
    monkeypatch.setattr(
        sb, "crc_read",
        lambda cpu, n: (b"\x00" + b"\xFF" * (n - 1))
        if cpu == 0x10000 else b"\xFF" * n,
    )

    with pytest.raises(sh.SoftBSLError, match="verify failed"):
        sb.write_tune_partial(partial, do_verify=True)


def test_write_tune_partial_rejects_wrong_size(monkeypatch):
    sh, sb = _fake_softbsl(monkeypatch)
    try:
        sb.write_tune_partial(b"\x00" * 1000)
        assert False, "expected a size rejection"
    except sh.SoftBSLError:
        pass


def test_write_tune_partial_raises_on_erase_failure(monkeypatch):
    sh, sb = _fake_softbsl(monkeypatch)
    monkeypatch.setattr(sb, "erase", lambda addr: 3)      # persistent policy-deny -> hard fail
    try:
        sb.write_tune_partial(b"\x00" * sh.TUNE_PARTIAL_SIZE)
        assert False, "expected an erase failure to raise"
    except sh.SoftBSLError as e:
        assert "erase" in str(e)
        assert "unexpected policy-deny" in str(e)


def test_direct_top_full_write_uses_coarse_geometry_and_sa7_last(monkeypatch):
    sh, sb = _fake_softbsl(monkeypatch)
    image = bytearray(b"\x33" * sh.IMAGE_SIZE)
    image[0x8000:0xC000] = b"\xFF" * 0x4000
    image[sh.MARKER_OFF:sh.MARKER_OFF + 4] = bytes.fromhex("a55a54ab")
    erased = []
    monkeypatch.setattr(sb, "erase", lambda addr: erased.append(addr) or 1)
    monkeypatch.setattr(
        sb, "crc_read",
        lambda cpu, n: bytes(image[cpu ^ sh.DESCR:(cpu ^ sh.DESCR) + n]))

    sb.flash_image(
        bytes(image), scope="full", write_bootloader=True,
        baud="low", do_verify=True, chip="29f400")

    assert erased == [0x10000, 0x20000, 0x30000, 0x00000]


def test_unarmed_direct_top_full_write_preserves_entire_fused_sa7(monkeypatch):
    sh, sb = _fake_softbsl(monkeypatch)
    image = bytearray(b"\x33" * sh.IMAGE_SIZE)
    image[0x8000:0xC000] = b"\xFF" * 0x4000
    image[sh.MARKER_OFF:sh.MARKER_OFF + 4] = bytes.fromhex("a55a54ab")
    erased, programmed = [], []
    monkeypatch.setattr(sb, "erase", lambda addr: erased.append(addr) or 1)
    monkeypatch.setattr(
        sb, "program_chunk", lambda cpu, data: programmed.append(cpu) or 1)
    monkeypatch.setattr(
        sb, "crc_read",
        lambda cpu, n: bytes(image[cpu ^ sh.DESCR:(cpu ^ sh.DESCR) + n]))

    sb.flash_image(
        bytes(image), scope="full", write_bootloader=False,
        baud="low", do_verify=True, chip="29f400")

    assert erased == [0x10000, 0x20000, 0x30000]
    assert programmed
    assert all((cpu ^ sh.DESCR) >= 0x10000 for cpu in programmed)


def test_agent_path_for_family_picks_intel_vs_amd():
    from engines.softbsl import softbsl_host as sh
    assert sh.agent_path_for_family("intel").endswith("agent_28f.hex")
    assert sh.agent_path_for_family("28f200").endswith("agent_28f.hex")
    assert sh.agent_path_for_family("amd").endswith("agent.hex")
    assert sh.agent_path_for_family(None).endswith("agent.hex")     # default = AMD
