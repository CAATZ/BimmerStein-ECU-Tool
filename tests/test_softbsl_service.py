import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import softbsl_service
import ds2 as app_ds2
import ecu_info
from engines.softbsl import softbsl_host

BLANK = b"\xFF" * 262144


def _image_with_driver(signature):
    image = bytearray(BLANK)
    image[ecu_info.DRV_SIG_FILE_OFFSET:
          ecu_info.DRV_SIG_FILE_OFFSET + ecu_info.DRV_SIG_LEN] = signature
    return bytes(image)


AMD_IMAGE = _image_with_driver(bytes.fromhex("e00e0d58f04ec084"))
INTEL_IMAGE = _image_with_driver(bytes.fromhex("e6f45000b84c6fe0"))


def test_service_uses_the_app_ds2_transport():
    assert softbsl_service._sbds2 is app_ds2


def test_agent_log_downgrades_mechanics_but_preserves_actionable_messages():
    events = []
    log = softbsl_service._agent_log(
        lambda message, level="info": events.append((message, level))
    )

    log("streaming agent (1464 B) into SRAM 0xD800 ...")
    log("marker 0 was not confirmed after reset; trying stock program VERIFY ...", "warn")

    assert events == [
        ("streaming agent (1464 B) into SRAM 0xD800 ...", "debug"),
        ("marker 0 was not confirmed after reset; trying stock program VERIFY ...", "warn"),
    ]


def test_amd_image_is_blocked_on_intel_before_agent_entry(monkeypatch):
    monkeypatch.setattr(
        softbsl_service, "_open_session",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("family gate entered the RAM agent")))

    try:
        softbsl_service.run_flash(
            "COM1", AMD_IMAGE, "full", prompt=lambda _message: "",
            log=lambda *_args: None, chip_family="intel")
        assert False, "AMD image was accepted for an Intel ECU"
    except softbsl_service.FlashFamilyMismatchError as error:
        assert "blocked before agent entry" in str(error)
        assert "built and saved" in str(error)


def test_intel_image_is_blocked_on_amd_before_agent_entry(monkeypatch):
    monkeypatch.setattr(
        softbsl_service, "_open_session",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("family gate entered the RAM agent")))

    try:
        softbsl_service.run_flash(
            "COM1", INTEL_IMAGE, "full", prompt=lambda _message: "",
            log=lambda *_args: None, chip_family="amd")
        assert False, "Intel image was accepted for an AMD ECU"
    except softbsl_service.FlashFamilyMismatchError as error:
        assert "Intel 28F" in str(error)
        assert "AMD/JEDEC 29F" in str(error)


def test_amd_image_is_allowed_for_amd_when_boot_is_preserved():
    assert softbsl_service.validate_flash_image_family(
        AMD_IMAGE, "amd", write_bootloader=False) == "amd"


def test_cross_bank_amd_image_is_blocked_on_intel_before_agent_entry(monkeypatch):
    monkeypatch.setattr(
        softbsl_service, "_open_session",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("cross-bank gate entered the RAM agent")))

    try:
        softbsl_service.run_cross_bank(
            "COM1", AMD_IMAGE, prompt=lambda _message: "", log=lambda *_args: None,
            chip_family="intel")
        assert False, "AMD cross-bank image was accepted for an Intel ECU"
    except softbsl_service.FlashFamilyMismatchError:
        pass


def test_armed_boot_write_requires_known_matching_family():
    for image, connected_family in (
            (AMD_IMAGE, "intel"),
            (INTEL_IMAGE, "amd"),
            (INTEL_IMAGE, None)):
        try:
            softbsl_service.validate_flash_image_family(
                image, connected_family, write_bootloader=True)
            assert False, "mismatched or unknown armed boot write was accepted"
        except softbsl_service.FlashFamilyMismatchError:
            pass

    assert softbsl_service.validate_flash_image_family(
        INTEL_IMAGE, "intel", write_bootloader=True) == "intel"


def test_missing_d2xx_skips_pyserial_fast_tiers():
    attempts = []
    logs = []

    def attempt(tier):
        attempts.append(tier)
        if tier != "low":
            raise softbsl_service.D2XXRequiredError("COM1 opened through pyserial")
        return "low-ok"

    result = softbsl_service._with_baud_fallback(attempt, "high", logs.append, "read")
    assert result == "low-ok"
    assert attempts == ["high", "low"]
    assert any("skipping unsupported pyserial fast tiers" in line.lower() for line in logs)


def test_recovery_agent_commits_marker_zero():
    agent = softbsl_host.RECOVERY_AGENT
    assert len(agent) == 12
    assert agent[:2] == bytes.fromhex("e108")


def test_top_flash_plan_reports_top_coarse_geometry():
    image = bytearray(BLANK)
    image[0x5FFC:0x6000] = bytes([0xA5, 0x5A, 0x54, 0x54 ^ 0xFF])
    logs = []
    softbsl_host.flash_dry_run(
        bytes(image), scope="full", write_bootloader=True,
        chip="29f400", log=logs.append)
    text = "\n".join(logs)
    assert "full 29F400 TOP half" in text
    assert "SA8 cal" in text
    assert "SA7 FUSED boot" in text
    assert "full bottom half" not in text


def test_crossbank_plan_flags_brick_class():
    txt = softbsl_service.crossbank_plan(BLANK)
    assert "BRICK-CLASS" in txt


def test_marker_detects_top_bottom_none():
    img = bytearray(BLANK)
    img[0x5FFC:0x6000] = bytes([0xA5, 0x5A, 0x54, 0x54 ^ 0xFF])   # 'T'
    assert softbsl_service.marker(bytes(img)) == "T"
    img[0x5FFC:0x6000] = bytes([0xA5, 0x5A, 0x42, 0x42 ^ 0xFF])   # 'B'
    assert softbsl_service.marker(bytes(img)) == "B"
    assert softbsl_service.marker(BLANK) is None


def test_run_flash_forwards_progress_do_verify_write_bootloader(monkeypatch):
    captured = {}

    class FakeSB:
        def __init__(self, *a, **k): pass
        def ensure_flash_mode(self, **kwargs): pass
        def enter_retry(self, agent, trigger="5a"): pass
        def crc_read(self, addr, length): return b"\x00" * length
        def finalize_marker0(self, already_sent=False): return True
        def flash_image(self, image, **kw):
            captured.update(kw)

    class FakeDS2:
        def open(self): pass
        def close(self): pass

    monkeypatch.setattr(softbsl_service, "_sbds2",
                        type("M", (), {"DS2Interface": lambda *a, **k: FakeDS2()}))
    monkeypatch.setattr(softbsl_service, "_sb", type("M", (), {
        "SoftBSL": FakeSB,
        "load_agent": lambda p: b"\x00",
        "_agent_default": lambda: "agent.hex",
        "agent_path_for_family": lambda fam: "agent.hex" if fam != "intel" else "agent_28f.hex",
    }))

    seen_progress = []
    softbsl_service.run_flash("COM1", INTEL_IMAGE, "tune", prompt=lambda m: "", log=lambda *a: None,
                              baud="low", progress_cb=lambda d, t, l="": seen_progress.append(1),
                              do_verify=False, write_bootloader=True, chip_family="intel")

    assert captured["do_verify"] is False
    assert captured["write_bootloader"] is True
    assert captured["progress_cb"] is not None


class _RecordingSB:
    """A fake SoftBSL that records the finalization + transfer calls the service makes."""
    def __init__(self, *a, **k):
        self.calls = []
        self.last_flash_image = None
        self.last_tune = None
    def ensure_flash_mode(self, **kwargs): self.calls.append(("ensure_flash_mode", kwargs))
    def enter_retry(self, agent, trigger="5a"): self.calls.append(("enter_retry", trigger))
    def set_baud(self, tier): self.calls.append(("set_baud", tier))
    def reset(self): self.calls.append("reset")
    def finalize_marker0(self, already_sent=False):
        self.calls.append(("finalize_marker0", already_sent))
        if not already_sent:
            self.reset()
        return True
    def flash_image(self, image, **kw):
        self.last_flash_image = bytes(image)
        self.calls.append(("flash_image", kw))
        if kw.get("do_verify", True):
            self.reset()                 # real flash_image finalizes after a clean verify
    def write_tune_partial(self, partial, *, do_verify=True, progress_cb=None):
        self.last_tune = bytes(partial)
        self.calls.append(("write_tune_partial", len(partial), do_verify))
        if progress_cb:
            progress_cb(len(partial), len(partial), "program")
    def read_range(self, lo, length, *, progress_cb=None, descramble=True, log_fn=None, **kw):
        self.calls.append(("read_range", lo, length, descramble))
        if progress_cb:
            progress_cb(length, length)
        return b"\x11" * length
    def crc_read(self, addr, length):
        self.calls.append(("crc_read", addr, length))
        return b"\xA5\x5A\x42\xBD"


def _install_fakes(monkeypatch, sb, close_rec=None):
    class FakeDS2:
        uses_d2xx = True
        transport_name = "d2xx"
        def __init__(self): self.is_open = False
        def open(self): self.is_open = True
        def close(self):
            if self.is_open and close_rec is not None:
                close_rec.append(True)
            self.is_open = False
    monkeypatch.setattr(softbsl_service, "_sbds2",
                        type("M", (), {"DS2Interface": lambda *a, **k: FakeDS2()}))
    monkeypatch.setattr(softbsl_service, "_sb", type("M", (), {
        "SoftBSL": lambda *a, **k: sb,
        "load_agent": lambda p: b"\x00",
        "_agent_default": lambda: "agent.hex",
        "agent_path_for_family": lambda fam: "agent.hex" if fam != "intel" else "agent_28f.hex",
        "IMAGE_SIZE": 0x40000,
        "PARAM1_FILE": (0x4000, 0x6000),
        "MARKER_OFF": 0x5FFC,
        "DESCR": 0x4000,
        "image_marker": softbsl_host.image_marker,
    }))


def test_write_identity_sector_is_strict_sa1_verified_scope(monkeypatch):
    sb = _RecordingSB()
    _install_fakes(monkeypatch, sb)
    sector = bytearray(b"\x33" * 0x2000)
    sector[0x023C:0x0244] = bytes.fromhex("e00e0d58f04ec084")
    sector[0x1FFC:0x2000] = bytes.fromhex("a55a42bd")

    softbsl_service.write_identity_sector(
        "COM1", bytes(sector), prompt=lambda _m: "", log=lambda *_a: None,
        baud="low", chip_family="amd")

    assert sb.last_flash_image[0x4000:0x6000] == bytes(sector)
    assert sb.last_flash_image[:0x4000] == b"\xFF" * 0x4000
    assert sb.last_flash_image[0x6000:] == b"\xFF" * (0x40000 - 0x6000)
    call = next(call for call in sb.calls if isinstance(call, tuple) and call[0] == "flash_image")
    assert call[1]["scope"] == "sa1"
    assert call[1]["write_bootloader"] is True
    assert call[1]["do_verify"] is True


def test_write_top_identity_sector_builds_a_t_marked_64k_sa7_scope(monkeypatch):
    sb = _RecordingSB()
    _install_fakes(monkeypatch, sb)
    sector = bytearray(b"\x33" * 0x10000)
    sector[ecu_info.DRV_SIG_FILE_OFFSET:
           ecu_info.DRV_SIG_FILE_OFFSET + ecu_info.DRV_SIG_LEN] = bytes.fromhex(
               "e00e0d58f04ec084")
    sector[0x5FFC:0x6000] = bytes.fromhex("a55a54ab")

    softbsl_service.write_identity_sector(
        "COM1", bytes(sector), prompt=lambda _m: "", log=lambda *_a: None,
        baud="low", chip_family="amd", half="T")

    assert sb.last_flash_image[:0x10000] == bytes(sector)
    assert sb.last_flash_image[0x10000:] == b"\xFF" * (0x40000 - 0x10000)
    call = next(call for call in sb.calls if isinstance(call, tuple) and call[0] == "flash_image")
    assert call[1]["scope"] == "sa1"
    assert call[1]["write_bootloader"] is True
    assert call[1]["do_verify"] is True


def test_write_top_identity_sector_rejects_non_amd_or_wrong_marker(monkeypatch):
    sb = _RecordingSB()
    _install_fakes(monkeypatch, sb)
    sector = bytearray(b"\x33" * 0x10000)
    sector[0x5FFC:0x6000] = bytes.fromhex("a55a54ab")

    for family in (None, "intel"):
        try:
            softbsl_service.write_identity_sector(
                "COM1", bytes(sector), prompt=lambda _m: "", log=lambda *_a: None,
                baud="low", chip_family=family, half="T")
            assert False, "expected TOP family rejection"
        except ValueError as error:
            assert "AMD/29F400" in str(error)

    sector[0x5FFC:0x6000] = bytes.fromhex("a55a42bd")
    try:
        softbsl_service.write_identity_sector(
            "COM1", bytes(sector), prompt=lambda _m: "", log=lambda *_a: None,
            baud="low", chip_family="amd", half="T")
        assert False, "expected marker mismatch"
    except ValueError as error:
        assert "bank marker" in str(error)


def test_read_image_tune_reads_the_raw_24k_partition_at_0x10000(monkeypatch):
    sb = _RecordingSB()
    _install_fakes(monkeypatch, sb)

    progress_seen = []
    logs = []
    data = softbsl_service.read_image("COM1", "tune", "low",
                                      lambda d, t: progress_seen.append((d, t)), log=logs.append)

    # 24 KB (not 64 KB), raw CPU 0x10000, descramble=False -- byte-identical to ds2.read_partial.
    assert len(data) == 24 * 1024
    assert ("read_range", 0x10000, 24 * 1024, False) in sb.calls
    assert progress_seen and progress_seen[-1] == (24 * 1024, 24 * 1024)


def test_read_identity_data_uses_file_order_window_and_recovers(monkeypatch):
    sb = _RecordingSB()
    _install_fakes(monkeypatch, sb)
    progress_seen = []

    data = softbsl_service.read_identity_data(
        "COM1", "high", lambda d, t: progress_seen.append((d, t)),
        log=lambda *_a: None, chip_family="amd")

    assert len(data) == 16 * 1024
    assert ("read_range", 0x4000, 16 * 1024, True) in sb.calls
    assert ("set_baud", "high") in sb.calls
    assert ("finalize_marker0", False) in sb.calls
    assert progress_seen[-1] == (16 * 1024, 16 * 1024)


def test_read_top_identity_data_caches_complete_64k_sa7(monkeypatch):
    sb = _RecordingSB()
    _install_fakes(monkeypatch, sb)

    data = softbsl_service.read_identity_data(
        "COM1", "high", None, log=lambda *_a: None,
        chip_family="amd", half="T")

    assert len(data) == 64 * 1024
    assert ("read_range", 0, 64 * 1024, True) in sb.calls


def test_read_identity_sector_uses_bottom_sa1_or_top_sa7_geometry(monkeypatch):
    sb = _RecordingSB()
    _install_fakes(monkeypatch, sb)

    bottom = softbsl_service.read_identity_sector(
        "COM1", "low", None, log=lambda *_a: None,
        chip_family="amd", half="B")
    top = softbsl_service.read_identity_sector(
        "COM1", "low", None, log=lambda *_a: None,
        chip_family="amd", half="T")

    assert len(bottom) == 8 * 1024
    assert len(top) == 64 * 1024
    assert ("read_range", 0x4000, 8 * 1024, True) in sb.calls
    assert ("read_range", 0, 64 * 1024, True) in sb.calls


def test_read_identity_data_falls_back_to_next_baud(monkeypatch):
    sb = _RecordingSB()
    _install_fakes(monkeypatch, sb)
    attempts = []

    def flaky_read(lo, length, **_kwargs):
        attempts.append((lo, length))
        if len(attempts) == 1:
            raise softbsl_service.SoftBSLError("CRC retries exhausted")
        return b"\x22" * length

    sb.read_range = flaky_read
    data = softbsl_service.read_identity_data(
        "COM1", "high", None, log=lambda *_a: None, chip_family="amd")

    assert data == b"\x22" * (16 * 1024)
    assert ("set_baud", "high") in sb.calls
    assert ("set_baud", "mid") in sb.calls
    assert len(attempts) == 2


def test_crossbank_base_read_confirms_a17_then_returns_to_lower(monkeypatch):
    sb = _RecordingSB()
    guards = iter((b"\xA5\x5A\x42\xBD", b"\xA5\x5A\x54\xAB"))
    sb.crc_read = lambda addr, length: next(guards)
    _install_fakes(monkeypatch, sb)
    prompts = []

    image = softbsl_service.read_cross_bank_image(
        "COM1", lambda message: prompts.append(message), lambda *args: None,
        baud="low", progress_cb=None)

    assert len(image) == 0x40000
    assert len(prompts) == 2
    assert "UPPER" in prompts[0] and "LOWER" in prompts[1]
    assert ("read_range", 0, 0x40000, True) in sb.calls
    assert ("finalize_marker0", False) in sb.calls


def test_crossbank_base_read_same_guard_is_rejected_and_prompts_lower(monkeypatch):
    sb = _RecordingSB()
    sb.crc_read = lambda addr, length: b"\xA5\x5A\x42\xBD"
    _install_fakes(monkeypatch, sb)
    prompts = []

    try:
        softbsl_service.read_cross_bank_image(
            "COM1", lambda message: prompts.append(message), lambda *args: None,
            baud="low", progress_cb=None)
        assert False, "unchanged A17 guard must reject the TOP base"
    except softbsl_service.CrossBankSafetyError as error:
        assert "could not be confirmed" in str(error)

    assert len(prompts) == 2
    assert "UPPER" in prompts[0] and "LOWER" in prompts[1]
    assert not any(call[0] == "read_range" for call in sb.calls if isinstance(call, tuple))
    assert ("finalize_marker0", False) in sb.calls


def test_read_image_full_reads_the_whole_image_in_file_order(monkeypatch):
    sb = _RecordingSB()
    _install_fakes(monkeypatch, sb)

    data = softbsl_service.read_image("COM1", "full", "high",
                                      lambda d, t: None, log=lambda *a: None)

    assert len(data) == 0x40000
    assert ("read_range", 0, 0x40000, True) in sb.calls        # descramble=True, file order


def test_ms412_payloads_use_all_softbsl_read_and_write_paths(monkeypatch):
    from tests.conftest import ref

    image = ref("MS41.2")
    tune = image[0x14000:0x1A000]
    sb = _RecordingSB()

    def read_range(lo, length, *, progress_cb=None, descramble=True, log_fn=None, **_kw):
        sb.calls.append(("read_range", lo, length, descramble))
        if progress_cb:
            progress_cb(length, length)
        return image if length == 0x40000 else tune

    sb.read_range = read_range
    _install_fakes(monkeypatch, sb)

    assert softbsl_service.read_image(
        "COM1", "full", "low", lambda *_args: None, log=lambda *_args: None) == image
    assert softbsl_service.read_image(
        "COM1", "tune", "low", lambda *_args: None, log=lambda *_args: None) == tune

    softbsl_service.run_flash(
        "COM1", image, "full", prompt=lambda _message: "", log=lambda *_args: None,
        baud="low", do_verify=True, chip_family="intel")
    softbsl_service.write_tune(
        "COM1", tune, log=lambda *_args: None, baud="low", do_verify=True,
        chip_family="intel")
    assert sb.last_flash_image == image
    assert sb.last_tune == tune
    assert ("read_range", 0, 0x40000, True) in sb.calls
    assert ("read_range", 0x10000, 24 * 1024, False) in sb.calls


def test_read_image_finalizes_marker0_with_the_running_agent(monkeypatch):
    sb = _RecordingSB()
    _install_fakes(monkeypatch, sb)

    softbsl_service.read_image("COM1", "tune", "low", lambda d, t: None, log=lambda *a: None)

    # R 9C 9C commits marker 0 inside the already-running agent; no second upload/re-entry.
    assert "reset" in sb.calls
    assert ("finalize_marker0", False) in sb.calls
    assert ("recover_to_normal", 0) not in sb.calls
    assert ("recover_normal", 0) not in sb.calls


def test_read_image_recovers_even_when_the_read_itself_fails(monkeypatch):
    closed = []
    sb = _RecordingSB()
    def boom(*a, **k): raise RuntimeError("crc_read link too noisy")
    sb.read_range = boom
    _install_fakes(monkeypatch, sb, close_rec=closed)

    try:
        softbsl_service.read_image("COM1", "tune", "low", lambda d, t: None, log=lambda *a: None)
        assert False, "expected the read failure to propagate"
    except RuntimeError:
        pass

    assert "reset" in sb.calls                    # finalization still ran on the failure path
    assert closed == [True]                        # and the port was closed


def test_run_flash_recovers_to_marker0_in_finally(monkeypatch):
    sb = _RecordingSB()
    _install_fakes(monkeypatch, sb)

    softbsl_service.run_flash("COM1", BLANK, "full", prompt=lambda m: "", log=lambda *a: None,
                              do_verify=True)

    assert any(isinstance(c, tuple) and c[0] == "flash_image" for c in sb.calls)
    # a write must ALWAYS end at marker 0, reboots into the app, no key-cycle
    assert "reset" in sb.calls
    assert ("finalize_marker0", True) in sb.calls


def test_run_flash_recovers_to_marker0_even_with_verify_off(monkeypatch):
    sb = _RecordingSB()
    _install_fakes(monkeypatch, sb)

    softbsl_service.run_flash("COM1", BLANK, "full", prompt=lambda m: "", log=lambda *a: None,
                              do_verify=False)

    # the old behavior left verify-off writes stuck in flash mode (E740=1); now they still recover.
    assert "reset" in sb.calls


TUNE_24K = b"\x5A" * (24 * 1024)


def test_write_tune_uses_the_partial_writer_not_flash_image(monkeypatch):
    sb = _RecordingSB()
    _install_fakes(monkeypatch, sb)

    seen = []
    softbsl_service.write_tune("COM1", TUNE_24K, log=lambda *a: None,
                               baud="high", progress_cb=lambda d, t, l="": seen.append((d, t)),
                               do_verify=True)

    # a 24 KB partial goes through write_tune_partial (no marker), NEVER flash_image (which needs a
    # full 256 KB image + a bank marker -- the cause of the "no valid bank-ID marker @0x5FFC" error).
    assert ("write_tune_partial", 24 * 1024, True) in sb.calls
    assert not any(isinstance(c, tuple) and c[0] == "flash_image" for c in sb.calls)
    probe_calls = [c for c in sb.calls if isinstance(c, tuple) and c[0] == "crc_read"]
    assert probe_calls == [
        ("crc_read", 0x20000, 128),
        ("crc_read", 0x20080, 128),
        ("crc_read", 0x20100, 128),
    ]
    # and it still finalizes to marker 0 like every other Fast op
    assert "reset" in sb.calls
    assert seen


def test_write_tune_recovers_even_when_the_write_fails(monkeypatch):
    closed = []
    sb = _RecordingSB()
    def boom(*a, **k): raise RuntimeError("tune chunk failed")
    sb.write_tune_partial = boom
    _install_fakes(monkeypatch, sb, close_rec=closed)

    try:
        softbsl_service.write_tune("COM1", TUNE_24K, log=lambda *a: None)
        assert False, "expected the write failure to propagate"
    except RuntimeError:
        pass

    assert "reset" in sb.calls
    assert closed == [True]


def test_open_session_uses_steady_state_door_not_the_disposable_43_door(monkeypatch):
    sb = _RecordingSB()
    _install_fakes(monkeypatch, sb)

    softbsl_service.run_flash("COM1", BLANK, "tune", prompt=lambda m: "", log=lambda *a: None)

    assert sb.calls[0] == ("ensure_flash_mode", {"poll_ready": True})
    assert sb.calls[1] == ("enter_retry", "5a")
    assert "reset" in sb.calls


def test_open_session_uses_direct_5a_for_calguard_mismatch(monkeypatch):
    class CalGuardSB(_RecordingSB):
        def calguard_direct_entry_ready(self):
            self.calls.append("calguard_direct_entry_ready")
            return True
        def enter_staged(self, _agent, tier, trigger):
            self.calls.append(("enter_staged", tier, trigger))

    sb = CalGuardSB()
    _install_fakes(monkeypatch, sb)

    d, _session = softbsl_service._open_session(
        "COM1", lambda *_args: None, chip_family="intel",
        baud_tier="low", entry_mode="auto")
    d.close()

    assert "calguard_direct_entry_ready" in sb.calls
    assert not any(call[0] == "ensure_flash_mode" for call in sb.calls if isinstance(call, tuple))
    assert ("enter_staged", "low", "5a") in sb.calls


def test_forced_direct_5a_bypasses_detection_and_0x2a(monkeypatch):
    class ForcedSB(_RecordingSB):
        def calguard_direct_entry_ready(self):
            raise AssertionError("forced mode must bypass CalGuard detection")
        def enter_staged(self, _agent, tier, trigger):
            self.calls.append(("enter_staged", tier, trigger))

    sb = ForcedSB()
    _install_fakes(monkeypatch, sb)

    d, _session = softbsl_service._open_session(
        "COM1", lambda *_args: None, chip_family="amd",
        baud_tier="high", entry_mode="direct")
    d.close()

    assert not any(call[0] == "ensure_flash_mode" for call in sb.calls if isinstance(call, tuple))
    assert ("enter_staged", "high", "5a") in sb.calls


def test_forced_direct_requires_a_known_flash_family(monkeypatch):
    class ForcedSB(_RecordingSB):
        def enter_staged(self, _agent, tier, trigger):
            self.calls.append(("enter_staged", tier, trigger))

    sb = ForcedSB()
    closed = []
    _install_fakes(monkeypatch, sb, close_rec=closed)

    try:
        softbsl_service._open_session(
            "COM1", lambda *_args: None, chip_family=None,
            baud_tier="low", entry_mode="direct")
        assert False, "forced direct entry guessed a flash agent"
    except softbsl_service.SoftBSLError as error:
        assert "known Intel/AMD" in str(error)

    assert closed == []
    assert not any(call[0] == "ensure_flash_mode" for call in sb.calls if isinstance(call, tuple))
    assert not any(call[0] == "enter_staged" for call in sb.calls if isinstance(call, tuple))


def test_open_session_recovers_and_closes_when_entry_fails_after_the_door(monkeypatch):
    # ensure_flash_mode fires the 0x2A door (commits E740=1) BEFORE enter_retry; if enter_retry then
    # misses the 5a window, the ECU is stranded in flash-listen. _open_session must walk it back to
    # marker 0 (via _recover_marker0) AND close the port, then re-raise.
    events = []
    sb = _RecordingSB()
    def boom(agent, trigger="5a"):
        sb.calls.append(("enter_retry", trigger))
        raise RuntimeError("trigger rejected (phase/magic/auth) - got 0x12, want ACK 0x06")
    sb.enter_retry = boom
    _install_fakes(monkeypatch, sb, close_rec=events)

    try:
        softbsl_service.run_flash("COM1", BLANK, "tune", prompt=lambda m: "", log=lambda *a: None)
        assert False, "expected the entry failure to propagate"
    except RuntimeError:
        pass

    assert "reset" in sb.calls                    # running agent finalizes marker 0 when available
    assert events == [True]                        # port closed, not leaked


def test_open_session_selects_the_intel_agent_for_a_28f200(monkeypatch):
    loaded = []
    sb = _RecordingSB()
    class FakeDS2:
        def open(self): pass
        def close(self): pass
    monkeypatch.setattr(softbsl_service, "_sbds2",
                        type("M", (), {"DS2Interface": lambda *a, **k: FakeDS2()}))
    monkeypatch.setattr(softbsl_service, "_sb", type("M", (), {
        "SoftBSL": lambda *a, **k: sb,
        "load_agent": lambda p: loaded.append(p) or b"\x00",
        "agent_path_for_family": lambda fam: "agent_28f.hex" if fam == "intel" else "agent.hex",
        "IMAGE_SIZE": 0x40000,
    }))

    # a 28F200 (Intel) tune write must load the Intel agent, not the AMD default
    softbsl_service.write_tune("COM1", TUNE_24K, log=lambda *a: None, chip_family="intel")
    assert loaded == ["agent_28f.hex"]

    loaded.clear()
    softbsl_service.read_image("COM1", "tune", "low", lambda d, t: None, log=lambda *a: None,
                               chip_family="amd")
    assert loaded == ["agent.hex"]


# ── baud fall-back ────────────────────────────────────────────────────────────


def test_baud_tiers_from_builds_the_descending_ladder():
    assert softbsl_service._baud_tiers_from("high") == ["high", "mid", "low"]
    assert softbsl_service._baud_tiers_from("mid") == ["mid", "low"]
    assert softbsl_service._baud_tiers_from("low") == ["low"]        # no fall-back from the safe rate
    assert softbsl_service._baud_tiers_from("weird") == ["weird"]    # unknown tier used alone


def test_read_image_falls_back_to_a_lower_baud_on_a_noisy_link(monkeypatch):
    sb = _RecordingSB()
    real_read = sb.read_range

    def flaky_read(lo, length, **kw):
        # fail the fast tiers (high, mid), succeed at low — as a marginal cable would behave
        tiers = [c for c in sb.calls if isinstance(c, tuple) and c[0] == "set_baud"]
        if len(tiers) < 2:                       # high attempt (1 set_baud) or mid attempt (2)
            raise softbsl_host.SoftBSLError("crc_read exhausted retries at this rate")
        return real_read(lo, length, **kw)
    sb.read_range = flaky_read
    _install_fakes(monkeypatch, sb)

    data = softbsl_service.read_image("COM1", "tune", "high", lambda d, t: None, log=lambda *a: None)

    assert len(data) == 24 * 1024                                   # eventually succeeded
    assert ("set_baud", "high") in sb.calls and ("set_baud", "mid") in sb.calls
    assert "reset" in sb.calls                                      # every attempt still finalized


def test_read_image_reraises_when_even_low_baud_fails(monkeypatch):
    sb = _RecordingSB()
    def always_fail(*a, **k): raise softbsl_host.SoftBSLError("link dead at every rate")
    sb.read_range = always_fail
    _install_fakes(monkeypatch, sb)

    try:
        softbsl_service.read_image("COM1", "full", "high", lambda d, t: None, log=lambda *a: None)
        assert False, "expected SoftBSLError to propagate after the low-baud attempt"
    except softbsl_service.SoftBSLFallbackExhausted as error:
        assert "Force Slow DS2 (ECU Recovery)" in str(error)
    # tried all three tiers: two set_baud (high, mid); low skips set_baud
    assert ("set_baud", "high") in sb.calls and ("set_baud", "mid") in sb.calls


def test_low_baud_read_makes_no_fallback_attempts(monkeypatch):
    sb = _RecordingSB()
    calls = {"n": 0}
    def once(lo, length, **kw):
        calls["n"] += 1
        raise softbsl_host.SoftBSLError("fail")
    sb.read_range = once
    _install_fakes(monkeypatch, sb)

    try:
        softbsl_service.read_image("COM1", "tune", "low", lambda d, t: None, log=lambda *a: None)
        assert False
    except softbsl_host.SoftBSLError:
        pass
    assert calls["n"] == 1                        # no ladder below 'low' — a single attempt


def test_run_flash_bootloader_write_can_fall_back_while_erase_is_untouched(monkeypatch):
    sb = _RecordingSB()
    attempts = {"n": 0}
    def boom_flash(image, **kw):
        attempts["n"] += 1
        sb.calls.append(("flash_image", kw))
        raise softbsl_host.SoftBSLError("boot write failed at high")
    sb.flash_image = boom_flash
    _install_fakes(monkeypatch, sb)

    try:
        softbsl_service.run_flash(
            "COM1", INTEL_IMAGE, "full", prompt=lambda m: "", log=lambda *a: None,
            baud="high", write_bootloader=True, chip_family="intel")
        assert False, "expected SoftBSLError after all pre-erase baud tiers failed"
    except softbsl_host.SoftBSLError:
        pass
    assert attempts["n"] == 3
    assert sb.calls.count("reset") >= 3


def test_run_flash_full_program_write_falls_back_when_not_bootloader(monkeypatch):
    sb = _RecordingSB()
    attempts = {"n": 0}
    def flaky_flash(image, **kw):
        attempts["n"] += 1
        sb.calls.append(("flash_image", kw))
        if attempts["n"] < 2:                        # fail the high attempt, succeed at mid
            raise softbsl_host.SoftBSLError("noisy at high")
    sb.flash_image = flaky_flash
    _install_fakes(monkeypatch, sb)

    softbsl_service.run_flash("COM1", BLANK, "full", prompt=lambda m: "", log=lambda *a: None,
                              baud="high", write_bootloader=False)
    assert attempts["n"] == 2                        # program write (no boot) DID fall back


def test_write_tune_falls_back_on_a_noisy_link(monkeypatch):
    sb = _RecordingSB()
    failed = {"once": False}
    real_crc_read = sb.crc_read

    def flaky_preflight(addr, length):
        if not failed["once"]:
            failed["once"] = True
            raise softbsl_host.SoftBSLError("high-rate CRC probe failed")
        return real_crc_read(addr, length)

    sb.crc_read = flaky_preflight
    _install_fakes(monkeypatch, sb)

    softbsl_service.write_tune("COM1", TUNE_24K, log=lambda *a: None, baud="high")

    assert ("set_baud", "high") in sb.calls
    assert ("set_baud", "mid") in sb.calls
    assert ("write_tune_partial", 24 * 1024, True) in sb.calls
    assert "reset" in sb.calls


def test_tune_failure_after_erase_retains_intel_agent_and_stops_fallback(monkeypatch):
    sb = _RecordingSB()
    closed = []
    attempts = {"count": 0}

    def fail_after_erase(partial, *, do_verify=True, progress_cb=None):
        attempts["count"] += 1
        progress_cb(0, len(partial), "erase")
        progress_cb(0, len(partial), "program")
        raise softbsl_host.SoftBSLError("program chunk failed after erase")

    sb.write_tune_partial = fail_after_erase
    _install_fakes(monkeypatch, sb, close_rec=closed)

    caught = None
    try:
        softbsl_service.write_tune(
            "COM1", TUNE_24K, log=lambda *_args: None, baud="high",
            do_verify=True, chip_family="intel")
    except softbsl_service.SoftBSLWriteRecoveryRequired as error:
        caught = error

    assert caught is not None
    assert attempts["count"] == 1
    assert caught.recovery.operation == "tune"
    assert caught.recovery.chip_family == "intel"
    assert caught.recovery.do_verify is True
    assert caught.recovery.is_open is True
    assert closed == []
    assert "reset" not in sb.calls
    assert ("set_baud", "mid") not in sb.calls


def test_full_verify_failure_after_erase_retains_amd_agent_and_stops_fallback(monkeypatch):
    sb = _RecordingSB()
    closed = []
    attempts = {"count": 0}

    def fail_during_verify(image, **kw):
        attempts["count"] += 1
        kw["progress_cb"](0, len(image), "erase")
        kw["progress_cb"](len(image), len(image), "program")
        kw["progress_cb"](0, len(image), "verify")
        raise softbsl_host.SoftBSLError("read-back verify failed")

    sb.flash_image = fail_during_verify
    _install_fakes(monkeypatch, sb, close_rec=closed)

    caught = None
    try:
        softbsl_service.run_flash(
            "COM1", AMD_IMAGE, "full", prompt=lambda _message: "",
            log=lambda *_args: None, baud="high", do_verify=True,
            write_bootloader=False, chip_family="amd")
    except softbsl_service.SoftBSLWriteRecoveryRequired as error:
        caught = error

    assert caught is not None
    assert attempts["count"] == 1
    assert caught.recovery.operation == "image"
    assert caught.recovery.scope == "full"
    assert caught.recovery.chip_family == "amd"
    assert caught.recovery.do_verify is True
    assert caught.recovery.is_open is True
    assert closed == []
    assert "reset" not in sb.calls
    assert ("set_baud", "mid") not in sb.calls


def test_retained_tune_recovery_reuses_same_session_and_respects_verify_off(monkeypatch):
    sb = _RecordingSB()
    closed = []

    def first_failure(partial, *, do_verify=True, progress_cb=None):
        progress_cb(0, len(partial), "erase")
        raise softbsl_host.SoftBSLError("injected program failure")

    sb.write_tune_partial = first_failure
    _install_fakes(monkeypatch, sb, close_rec=closed)

    try:
        softbsl_service.write_tune(
            "COM1", TUNE_24K, log=lambda *_args: None, baud="high",
            do_verify=False, chip_family="intel")
        assert False, "expected a retained recovery"
    except softbsl_service.SoftBSLWriteRecoveryRequired as error:
        recovery = error.recovery

    resumed = []

    def successful_retry(partial, *, do_verify=True, progress_cb=None):
        resumed.append((bytes(partial), do_verify))
        progress_cb(0, len(partial), "erase")
        progress_cb(len(partial), len(partial), "program")

    sb.write_tune_partial = successful_retry
    assert softbsl_service.resume_write_recovery(
        recovery, progress_cb=lambda *_args: None, log=lambda *_args: None) is True

    assert resumed == [(TUNE_24K, False)]
    assert recovery.is_open is False
    assert closed == [True]
    assert sb.calls.count(("set_baud", "high")) == 1
    assert ("finalize_marker0", False) in sb.calls


def test_retained_full_recovery_reuses_same_session_and_respects_verify_on(monkeypatch):
    sb = _RecordingSB()
    closed = []

    def first_failure(image, **kw):
        kw["progress_cb"](0, len(image), "erase")
        raise softbsl_host.SoftBSLError("injected full-program failure")

    sb.flash_image = first_failure
    _install_fakes(monkeypatch, sb, close_rec=closed)

    try:
        softbsl_service.run_flash(
            "COM1", AMD_IMAGE, "full", prompt=lambda _message: "",
            log=lambda *_args: None, baud="high", do_verify=True,
            chip_family="amd")
        assert False, "expected a retained recovery"
    except softbsl_service.SoftBSLWriteRecoveryRequired as error:
        recovery = error.recovery

    resumed = []

    def successful_retry(image, **kw):
        resumed.append((bytes(image), kw["do_verify"], kw["baud_is_set"]))
        kw["progress_cb"](0, len(image), "erase")
        kw["progress_cb"](len(image), len(image), "program")
        kw["progress_cb"](len(image), len(image), "verify")
        sb.reset()  # production flash_image resets internally after a clean verify

    sb.flash_image = successful_retry
    assert softbsl_service.resume_write_recovery(
        recovery, progress_cb=lambda *_args: None, log=lambda *_args: None) is True

    assert resumed == [(AMD_IMAGE, True, True)]
    assert recovery.is_open is False
    assert closed == [True]
    assert sb.calls.count(("set_baud", "high")) == 1
    assert ("finalize_marker0", True) in sb.calls
