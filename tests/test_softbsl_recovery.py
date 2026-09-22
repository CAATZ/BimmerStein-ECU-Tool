"""Error-path checks for the shared Soft-BSL owner; healthy writes keep one packet."""
import types
import pytest

from engines.softbsl import softbsl_host as sh
import softbsl_service as service


def host(monkeypatch):
    sb = sh.SoftBSL(types.SimpleNamespace(), log=lambda _message: None)
    monkeypatch.setattr(sh.time, "sleep", lambda _seconds: None)
    return sb


def test_healthy_chunk_sends_only_the_existing_packet(monkeypatch):
    sb = host(monkeypatch)
    sent = []
    monkeypatch.setattr(sb, "_txs", sent.append)
    monkeypatch.setattr(sb, "_rx", lambda timeout: 1)
    monkeypatch.setattr(sb, "crc_read", lambda *_args: pytest.fail("unexpected read"))
    assert sb.program_chunk(0x10000, b"A" * 1024) == 1
    assert len(sent) == 1 and sent[0][:4] == b"C\x01\x00\x00"


@pytest.mark.parametrize("complete", [False, True])
def test_incomplete_send_proves_agent_ready_before_resend(monkeypatch, complete):
    sb = host(monkeypatch)
    events = []
    def failed_send(_wire):
        events.append("send")
        raise sh.SoftBSLError("echo interrupted")
    monkeypatch.setattr(sb, "_txs", failed_send)
    monkeypatch.setattr(sb, "_drain_until_quiet", lambda: events.append("quiet"))
    monkeypatch.setattr(sb, "crc_read", lambda a, n: events.append((a, n)) or (b"A" if complete else b"\xFF") * n)
    assert sb.program_chunk(0x10000, b"A" * 1024) == int(complete)
    assert events == ["send", "quiet", (0x10000, 1024)]


def test_unproven_link_cannot_retry_or_erase(monkeypatch):
    sb = host(monkeypatch)
    monkeypatch.setattr(sb, "_txs", lambda _wire: None)
    monkeypatch.setattr(sb, "_rx", lambda timeout: (_ for _ in ()).throw(sh.SoftBSLError("timeout")))
    monkeypatch.setattr(sb, "_drain_until_quiet", lambda: None)
    monkeypatch.setattr(sb, "crc_read", lambda *_args: (_ for _ in ()).throw(sh.SoftBSLError("CRC read failed")))
    monkeypatch.setattr(sb, "erase", lambda _a: pytest.fail("unqualified erase"))
    with pytest.raises(sh.SoftBSLError, match="CRC read failed"):
        sb._program_chunk_with_retries(0x10000, b"A" * 1024)


def test_transient_partial_program_retries_without_read_or_erase(monkeypatch):
    sb = host(monkeypatch)
    statuses = iter((2, 1))
    monkeypatch.setattr(sb, "program_chunk", lambda a, data: next(statuses))
    monkeypatch.setattr(sb, "crc_read", lambda *_args: pytest.fail("unnecessary read"))
    monkeypatch.setattr(sb, "erase", lambda _a: pytest.fail("unnecessary erase"))
    sb._program_chunk_with_retries(0x10000, b"A" * 1024)


def test_crc_packet_retries_remain_bounded_without_erasing(monkeypatch):
    sb = host(monkeypatch)
    calls = []
    monkeypatch.setattr(sb, "program_chunk", lambda a, data: calls.append(a) or 4)
    monkeypatch.setattr(sb, "crc_read", lambda *_args: pytest.fail("CRC rejected before programming"))
    with pytest.raises(sh.SoftBSLError, match="packet retries"):
        sb._program_chunk_with_retries(0x10000, b"A" * 1024)
    assert len(calls) == 9


@pytest.mark.parametrize("persistent", [False, True])
def test_tune_replays_at_most_once_for_unrepairable_confirmed_bytes(monkeypatch, persistent):
    sb = host(monkeypatch)
    target = b"A" * sh.TUNE_PARTIAL_SIZE
    actual = bytearray(b"\xFF" * len(target))
    erases = []
    def erase(address):
        erases.append(address)
        actual[:] = b"\xFF" * len(actual)
        return 1
    def program(address, data):
        off = address - 0x10000
        if address == 0x10000 and (persistent or len(erases) == 1):
            actual[:2] = b"\x00\x00"
            return 2
        actual[off:off + len(data)] = data
        return 1
    monkeypatch.setattr(sb, "erase", erase)
    monkeypatch.setattr(sb, "program_chunk", program)
    monkeypatch.setattr(sb, "crc_read", lambda a, n: bytes(actual[a - 0x10000:a - 0x10000 + n]))
    if persistent:
        with pytest.raises(sh.ProgramReadbackMismatch):
            sb.write_tune_partial(target, do_verify=False)
    else:
        sb.write_tune_partial(target, do_verify=False)
        assert bytes(actual) == target
    assert erases == [0x10000, 0x10000]


@pytest.mark.parametrize("chip,end", [("29f400", 0x30000), ("28f200", 0x40000)])
def test_image_repairs_only_the_complete_affected_hardware_sector(monkeypatch, chip, end):
    sb = host(monkeypatch)
    image = bytearray(b"\xFF" * sh.IMAGE_SIZE)
    image[0x20000:0x20400] = b"A" * 1024
    image[0x30000:0x30400] = b"B" * 1024
    target = bytes(image)
    actual = bytearray(b"\xFF" * len(image))
    erases = []
    programs = []
    monkeypatch.setattr(sb, "select_half", lambda *_args, **_kw: None)
    monkeypatch.setattr(sb, "reset", lambda: None)
    def erase(address):
        erases.append(address)
        if address == 0x20000:
            for a in range(0x20000, end): actual[a ^ sh.DESCR] = 255
        return 1
    def program(address, data):
        programs.append(address)
        off = address ^ sh.DESCR
        if address == 0x24000 and erases.count(0x20000) == 1:
            actual[off:off + 2] = b"\x00\x00"
            return 2
        actual[off:off + len(data)] = data
        return 1
    monkeypatch.setattr(sb, "erase", erase)
    monkeypatch.setattr(sb, "program_chunk", program)
    monkeypatch.setattr(sb, "crc_read", lambda a, n: bytes(actual[a ^ sh.DESCR:(a ^ sh.DESCR) + n]))
    sb.flash_image(target, scope="full", chip=chip, baud="low", do_verify=False,
                   progress_cb=lambda *_args: None)
    assert bytes(actual) == target
    assert erases.count(0x20000) == 2
    assert all(erases.count(a) == 1 for a in set(erases) - {0x20000})
    assert programs.count(0x34000) == 1


def test_sector_replay_budget_is_one_for_the_entire_write(monkeypatch):
    sb = host(monkeypatch)
    image = b"\xFF" * sh.IMAGE_SIZE
    erases = []
    monkeypatch.setattr(sb, "erase", lambda a: erases.append(a) or 1)
    monkeypatch.setattr(sb, "crc_read", lambda a, n: b"\xFF" * n)
    sectors, _lo, _hi = sh._flash_scope("full", chip="29f400")
    replayed = set()
    args = dict(scope="full", half="lower", chip="29f400", sectors=sectors,
                write_bootloader=False, replayed=replayed)
    sb._replay_image_sector(0x24000, image, **args)
    with pytest.raises(sh.SoftBSLError, match="one automatic"):
        sb._replay_image_sector(0x34000, image, **args)
    assert erases == [0x20000]


def test_manual_recovery_has_one_attempt_and_retains_failed_session(monkeypatch):
    d = types.SimpleNamespace(is_open=True)
    calls = []
    def fail(*_args, **_kw):
        calls.append("write")
        raise sh.SoftBSLError("persistent write fault")
    sb = types.SimpleNamespace(write_tune_partial=fail)
    recovery = service.SoftBSLWriteRecovery("COM0", d, sb, "tune", b"A" * sh.TUNE_PARTIAL_SIZE,
                                          "tune", "high", None, False, False, "amd", RuntimeError())
    monkeypatch.setattr(service, "_prove_write_link", lambda *_args: None)
    for _ in range(2):
        with pytest.raises(service.SoftBSLWriteRecoveryRequired):
            service.resume_write_recovery(recovery)
    assert calls == ["write"] and not recovery.retry_supported and recovery.is_open


def test_missing_ack_impossible_readback_skips_useless_packet_retries(monkeypatch):
    sb = host(monkeypatch)
    sent = []
    monkeypatch.setattr(sb, "_txs", sent.append)
    monkeypatch.setattr(sb, "_rx", lambda timeout: (_ for _ in ()).throw(sh.SoftBSLError("timeout")))
    monkeypatch.setattr(sb, "_drain_until_quiet", lambda: None)
    monkeypatch.setattr(sb, "crc_read", lambda a, n: b"\x00" * n)
    with pytest.raises(sh.ProgramReadbackMismatch):
        sb._program_chunk_with_retries(0x10000, b"A" * 1024)
    assert len(sent) == 1


@pytest.mark.parametrize("operation", ["tune", "image"])
def test_explicit_recovery_cannot_renew_automatic_erase_budget(monkeypatch, operation):
    seen = []
    def fail(*_args, **kwargs):
        seen.append(kwargs["_sector_replay"])
        raise sh.SoftBSLError("persistent fault")
    sb = types.SimpleNamespace(write_tune_partial=fail, flash_image=fail)
    recovery = service.SoftBSLWriteRecovery(
        "COM0", types.SimpleNamespace(is_open=True), sb, operation, b"A" * sh.TUNE_PARTIAL_SIZE,
        "tune", "high", None, False, False, "amd", RuntimeError())
    monkeypatch.setattr(service, "_prove_write_link", lambda *_args: None)
    with pytest.raises(service.SoftBSLWriteRecoveryRequired):
        service.resume_write_recovery(recovery)
    assert seen == [True]
