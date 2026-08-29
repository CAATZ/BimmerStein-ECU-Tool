import os, shutil, sys
from pathlib import Path
from types import SimpleNamespace

import pytest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import softbsl_install


def test_d2xx_available_returns_bool_and_never_raises():
    assert softbsl_install.d2xx_available() in (True, False)


def test_alphan_compose_is_available_only_for_ms413():
    from engines.patcher import patch_ms41
    from tests.conftest import ref

    image, patch_ids, _log = softbsl_install._sb.compose_persistent_image(
        ref("MS41.3"), "29f400", with_alphan=True)
    assert "alphan_failsafe" in patch_ids
    assert patch_ms41.is_applied(
        image, patch_ms41.load_patches()["alphan_failsafe"])
    with pytest.raises(softbsl_install._sb.SoftBSLError, match="only applicable"):
        softbsl_install._sb.compose_persistent_image(
            ref("MS41.2"), "29f400", with_alphan=True)


def test_alphan_v2_upgrades_during_softbsl_install_compose():
    from engines.patcher import patch_ms41
    from tests.conftest import ref

    patches = patch_ms41.load_patches()
    stock = ref("MS41.3")
    v1, _ = patch_ms41.build(
        stock, ["alphan_failsafe_v1"], allow_deprecated=True)
    v2, _ = patch_ms41.build(
        stock, ["alphan_failsafe_v2"], allow_deprecated=True)
    v3, _ = patch_ms41.build(stock, ["alphan_failsafe"])
    current = patches["alphan_failsafe"]
    assert softbsl_install._sb._patch_state(stock, current) == "absent"
    assert softbsl_install._sb._patch_state(v1, current) == "legacy"
    assert softbsl_install._sb._patch_state(
        v2, current) == "legacy"
    assert softbsl_install._sb._patch_state(v3, current) == "applied"
    assert softbsl_install._sb._confirm_reinstall(
        SimpleNamespace(confirm_reinstall=lambda _message: True),
        stock, patches, ["alphan_failsafe"],
    ) == {"alphan_failsafe": "absent"}

    args = softbsl_install._sb.InstallRequest(
        port="COM_TEST",
        prompt=lambda _message: None,
        base=v2,
        chip="29f400",
        with_alphan=True,
        confirm_reinstall=lambda _message: True,
    )
    try:
        softbsl_install._sb._install_resolve_images(args)
        target = Path(args.target).read_bytes()
        assert patch_ms41.is_applied(target, patches["alphan_failsafe"])
        assert not patch_ms41.is_applied(target, patches["alphan_failsafe_v2"])
    finally:
        if args.target:
            shutil.rmtree(Path(args.target).parent, ignore_errors=True)


def test_install_log_keeps_detail_as_debug_and_one_terminal_success():
    events = []
    log = softbsl_install._install_log(
        lambda message, level="info": events.append((message, level))
    )

    log("bootstrap door : temporary.bin")
    log("staged 43 entry (stage=464 B, tier=high, agent=1464 B) ...")
    log(">>> INSTALL DONE: detailed engine completion")
    log("== FAST BOOTSTRAP COMPLETE == stock full-program finalization left the ECU high")

    assert events[0] == ("bootstrap door : temporary.bin", "debug")
    assert events[1][1] == "debug"
    assert events[2][1] == "debug"
    assert events[3] == (
        "Phase 1/3 complete; the required ignition cycle may begin.",
        "ok",
    )


def test_install_compose_reads_ecu_when_no_base(monkeypatch):
    captured = {}
    def fake_run(args, log):
        captured["args"] = args
        return 0
    monkeypatch.setattr(softbsl_install, "_run_install", fake_run)

    rc = softbsl_install.install_compose("COM1", None, with_calguard=True, allow_convert=False,
                                         prompt=lambda m: None, log=lambda *a: None)
    assert rc == 0
    args = captured["args"]
    assert args.cmd == "install" and args.yes is True
    assert args.with_calguard is True
    assert args.base is None                         # None base -> engine reads the ECU over stock DS2
    assert args.port == "COM1"
    assert args.baud == "low"                       # conservative brick-class default
    assert args.confirm_convert() is False
    assert args.preserve_cal is True


def test_install_compose_passes_base_and_convert(monkeypatch):
    captured = {}
    def fake_run(args, log):
        captured["args"] = args
        return 0
    monkeypatch.setattr(softbsl_install, "_run_install", fake_run)

    softbsl_install.install_compose(
        "COM3", "/tmp/ms413.bin", with_calguard=False, allow_convert=True,
        prompt=lambda m: None, log=lambda *a: None, preserve_cal=False)
    args = captured["args"]
    assert args.base == "/tmp/ms413.bin"
    assert args.with_calguard is False               # opt-out honored
    assert args.port == "COM3"
    assert args.confirm_convert() is True            # authorizes the factory-ECU conversion gate
    assert args.preserve_cal is False


def test_cached_full_read_is_passed_as_bytes_not_a_file_path(monkeypatch):
    captured = {}
    monkeypatch.setattr(softbsl_install, "_run_install",
                        lambda args, log: captured.setdefault("args", args) and 0)
    cached = b"\x5A" * 0x40000
    serial_factory = object()

    softbsl_install.install_compose(
        "COM4", cached, with_calguard=True, allow_convert=False,
        prompt=lambda _m: None, log=lambda _m: None,
        serial_factory=serial_factory)

    args = captured["args"]
    assert args.base is None
    assert args.base_bytes == cached
    assert args.ds2_factory is softbsl_install.AppDS2Interface
    assert args.serial_factory is serial_factory


def test_install_request_uses_dedicated_phase1_reentry_callback():
    class Prompt:
        def __call__(self, _message):
            return None

        def phase1_reentry_retry_cancel(self, port, message):
            return bool(port and message)

    prompt = Prompt()
    args = softbsl_install._install_args(port="COM4", prompt=prompt)

    assert args.phase1_reentry_prompt == prompt.phase1_reentry_retry_cancel


def test_run_install_calls_engine_directly_and_normalizes_success(monkeypatch):
    seen = []
    monkeypatch.setattr(softbsl_install._sb, "cmd_install",
                        lambda args: (softbsl_install._sb._emit("engine started"),
                                      seen.append(args.port)))
    logs = []
    args = softbsl_install._install_args(
        port="COM9", prompt=lambda _m: None, progress_cb=None)

    assert softbsl_install._run_install(args, logs.append) == 0
    assert seen == ["COM9"]
    assert logs == ["engine started"]


def test_run_install_normalizes_domain_error_for_the_gui(monkeypatch):
    def abort(_args):
        raise softbsl_install._sb.SoftBSLError("safety gate refused")
    monkeypatch.setattr(softbsl_install._sb, "cmd_install", abort)
    args = softbsl_install._install_args(port="COM9", prompt=lambda _m: None)

    try:
        softbsl_install._run_install(args, lambda _line: None)
        assert False, "expected an in-process service error"
    except softbsl_install.SoftBSLInstallError as e:
        assert "safety gate refused" in str(e)


def test_run_install_preserves_pre_phase1_typed_cancellation(monkeypatch):
    def cancel(_request, _log):
        raise softbsl_install._sb.InstallCancelled(
            "operator cancelled before Phase 1", phase="pre_phase1"
        )

    monkeypatch.setattr(softbsl_install._sb, "install", cancel)
    args = softbsl_install._install_args(port="COM9", prompt=lambda _m: None)

    with pytest.raises(softbsl_install.SoftBSLInstallCancelled) as caught:
        softbsl_install._run_install(args, lambda _line: None)

    assert caught.value.phase == "pre_phase1"
    assert "before Phase 1" in str(caught.value)


def test_run_install_preserves_live_installer_recovery(monkeypatch):
    class LiveRetainedSession:
        is_open = True

    engine_recovery = softbsl_install._sb.InstallRecovery(
        request=SimpleNamespace(port="COM9"),
        target=b"target",
        flash_over={"scope": "softbsl"},
        phase="target",
        retained=LiveRetainedSession(),
    )

    def fail_with_recovery(_request, _log):
        raise softbsl_install._sb.InstallRecoveryRequired(engine_recovery)

    monkeypatch.setattr(softbsl_install._sb, "install", fail_with_recovery)
    args = softbsl_install._install_args(port="COM9", prompt=lambda _m: None)

    with pytest.raises(softbsl_install.SoftBSLInstallRecoveryRequired) as caught:
        softbsl_install._run_install(args, lambda _line: None)

    assert caught.value.recovery.engine_recovery is engine_recovery
    assert caught.value.recovery.is_open is True
    assert caught.value.recovery.port == "COM9"
    assert "DO NOT TURN IGNITION OFF" in str(caught.value)


def test_phase2_failure_retains_and_reuses_the_same_ram_agent(monkeypatch, tmp_path):
    image_path = tmp_path / "target.bin"
    image_path.write_bytes(b"\x00" * softbsl_install._sb.IMAGE_SIZE)
    events = []

    class FakeDS2:
        is_open = True

        def close(self):
            self.is_open = False
            events.append("close")

    class FakeAgent:
        def __init__(self):
            self.flash_attempts = 0

        def flash_image(self, _image, **kwargs):
            self.flash_attempts += 1
            events.append(("flash", self.flash_attempts, kwargs["baud"]))
            if self.flash_attempts == 1:
                kwargs["progress_cb"](0, 1, "erase")
                raise RuntimeError("injected post-erase failure")

        def crc_read(self, address, length):
            events.append(("crc", address, length))
            return b"\x00" * length

        def finalize_marker0(self, already_sent=False):
            events.append(("finalize", already_sent))
            return True

    ds2 = FakeDS2()
    agent = FakeAgent()
    monkeypatch.setattr(
        softbsl_install._sb,
        "_check_image_checksums",
        lambda _image: (True, []),
    )
    monkeypatch.setattr(
        softbsl_install._sb,
        "_session_with_baud_fallback",
        lambda _args: (ds2, agent, "high"),
    )
    args = SimpleNamespace(
        image=str(image_path),
        scope="softbsl",
        chip="auto",
        dry_run=False,
        force=False,
        yes=True,
        trigger="43",
        baud="high",
        baud_fallback=True,
        progress_cb=None,
        retain_on_failure=True,
        no_verify=False,
        write_bootloader=True,
        assume_half="B",
        cross_bank=False,
        reset_recover=True,
        stay_flash=False,
        port="COM9",
    )

    with pytest.raises(softbsl_install._sb._RetainedInstallFlashRequired) as caught:
        softbsl_install._sb.cmd_flash(args)

    retained = caught.value.recovery
    assert retained.ds2 is ds2
    assert retained.agent is agent
    assert retained.is_open is True
    assert "close" not in events

    assert softbsl_install._sb._resume_retained_install_flash(retained) is True
    assert agent.flash_attempts == 2
    assert [
        event[1]
        for event in events
        if isinstance(event, tuple) and event[0] == "crc"
    ] == [0x20000, 0x20080, 0x20100]
    assert ("finalize", True) in events
    assert ds2.is_open is False


def test_phase1_recovery_reuses_native_session_before_continuing_install(monkeypatch):
    import ds2_native_fast_service

    native_recovery = SimpleNamespace(is_open=True)
    request = SimpleNamespace(port="COM9")
    recovery = softbsl_install._sb.InstallRecovery(
        request=request,
        target=b"target",
        flash_over={"scope": "softbsl"},
        phase="bootstrap",
        retained=native_recovery,
    )
    progress = object()
    events = []

    monkeypatch.setattr(
        ds2_native_fast_service,
        "resume_recovery",
        lambda retained, progress_cb=None: events.append(
            ("resume", retained, progress_cb)
        ),
    )
    monkeypatch.setattr(
        softbsl_install._sb,
        "_continue_install_after_bootstrap",
        lambda args, target, flash_over: events.append(
            ("continue", args, target, flash_over)
        ),
    )

    assert softbsl_install._sb.resume_install_recovery(
        recovery, progress_cb=progress
    ) is True
    assert events == [
        ("resume", native_recovery, progress),
        ("continue", request, b"target", {"scope": "softbsl"}),
    ]


def test_install_keycycle_retries_until_stock_ds2_reboot_is_confirmed(monkeypatch):
    events = []
    monkeypatch.setattr(
        softbsl_install._sb,
        "_verify_post_keycycle_bootstrap",
        lambda *_args: None,
    )

    class Probe:
        def __init__(self, label, answers):
            self.label = label
            self.answers = answers

        def identify(self):
            events.append(("identify", self.label))
            if not self.answers:
                raise RuntimeError("ignition is still off")
            return b"SHINDE1"

        def close(self):
            events.append(("close", self.label))

    probes = iter((Probe("off", False), Probe("on", True)))
    monkeypatch.setattr(
        softbsl_install._sb,
        "_open",
        lambda _args: next(probes),
    )
    args = SimpleNamespace(
        keycycle_prompt=lambda message: events.append(("keycycle", message)),
        keycycle_retry_prompt=lambda message: events.append(("retry", message)) or True,
    )

    softbsl_install._sb._install_keycycle(args)

    assert [event[0] for event in events] == [
        "keycycle", "identify", "close", "retry", "identify", "close"
    ]
    assert "OFF" in events[0][1] and "ignition ON" in events[0][1]
    assert "Phase 2 erase" in events[3][1]


def test_install_keycycle_cancel_is_typed_and_pre_phase2(monkeypatch):
    events = []
    monkeypatch.setattr(
        softbsl_install._sb,
        "_verify_post_keycycle_bootstrap",
        lambda *_args: None,
    )

    class SilentProbe:
        def identify(self):
            raise RuntimeError("no response")

        def close(self):
            events.append("close")

    monkeypatch.setattr(
        softbsl_install._sb,
        "_open",
        lambda _args: SilentProbe(),
    )
    args = SimpleNamespace(
        keycycle_prompt=lambda _message: None,
        keycycle_retry_prompt=lambda _message: False,
    )

    with pytest.raises(
        softbsl_install._sb.InstallCancelled,
        match="Phase 2 erase was not started",
    ):
        softbsl_install._sb._install_keycycle(args)

    assert events == ["close"]


def test_post_keycycle_gate_reads_every_prepared_phase1_range(tmp_path, monkeypatch):
    bootstrap = bytes(
        (index * 17 + 3) & 0xFF
        for index in range(softbsl_install._sb.IMAGE_SIZE)
    )
    bootstrap_path = tmp_path / "bootstrap.bin"
    bootstrap_path.write_bytes(bootstrap)
    door_ranges = softbsl_install._sb._bootstrap_verify_ranges(("door_0x43",))
    ranges = [(0x02000, 0x04000, "program-low safety range"), *door_ranges]
    requested = []
    identity = b"SHINDE1"
    monkeypatch.setattr(
        softbsl_install._sb,
        "_live_preflight",
        lambda _args: {"identity": identity},
    )

    class Probe:
        def read_mem(self, address, length):
            assert (address, length) == (0xE740, 1)
            return b"\x00"

        def read_memory_range(self, address, length):
            requested.append((address, length))
            return softbsl_install._sb._bootstrap_cpu_bytes(
                bootstrap, address, length)

    softbsl_install._sb._verify_post_keycycle_bootstrap(
        SimpleNamespace(
            bootstrap=str(bootstrap_path), target_version="MS41.3",
            bootstrap_verify_ranges=tuple(ranges),
        ),
        Probe(),
        identity,
    )

    assert requested == [(address, length) for address, length, _label in ranges]
    assert len(requested) == 3
    assert max(length for _address, length in requested) > 4


def test_live_preflight_captures_the_exact_ds2_identify_value(monkeypatch):
    class Probe:
        def identify(self):
            return b"SHINDE1\x00extra"

    monkeypatch.setattr(
        softbsl_install._sb,
        "_detect_ecu_variant",
        lambda _probe, **_kwargs: ("MS41.3", "MS41.3", True),
    )
    monkeypatch.setattr(
        softbsl_install._sb,
        "_detect_firmware_compatibility",
        lambda _probe: ("0912", "0912", b"909", True),
    )

    evidence = softbsl_install._sb._store_live_preflight(
        SimpleNamespace(port="COM1"),
        Probe(),
        "amd",
        softbsl_install._sb._DRV_SIG_AMD,
    )

    assert evidence["identity"] == b"SHINDE1"


def test_post_keycycle_verification_failure_is_a_typed_safe_stop(monkeypatch):
    events = []

    class Probe:
        def identify(self): return b"1406464"
        def close(self): events.append("close")

    monkeypatch.setattr(
        softbsl_install._sb, "_open", lambda _args: Probe())
    monkeypatch.setattr(
        softbsl_install._sb,
        "_verify_post_keycycle_bootstrap",
        lambda *_args: (_ for _ in ()).throw(
            softbsl_install._sb.BootstrapVerificationError(
                "door cave mismatch")
        ),
    )

    with pytest.raises(softbsl_install._sb.InstallCancelled) as caught:
        softbsl_install._sb._install_keycycle(SimpleNamespace(
            keycycle_prompt=lambda _message: None,
            keycycle_retry_prompt=lambda _message: True,
        ))

    assert caught.value.phase == "pre_phase2"
    assert "Phase 2 erase was not started" in str(caught.value)
    assert events == ["close"]


def test_session_closes_transport_when_agent_entry_fails_before_return(monkeypatch):
    events = []

    class Transport:
        def close(self):
            events.append("close")

    class FailingSoftBSL:
        def __init__(self, _transport):
            pass

        def enter(self, _agent, trigger=None):
            events.append(("enter", trigger))
            raise softbsl_install._sb.SoftBSLError("ignition was not cycled")

    monkeypatch.setattr(softbsl_install._sb, "load_agent", lambda _path: b"agent")
    monkeypatch.setattr(softbsl_install._sb, "_open", lambda *_args, **_kwargs: Transport())
    monkeypatch.setattr(softbsl_install._sb, "SoftBSL", FailingSoftBSL)
    args = SimpleNamespace(
        agent="agent.hex",
        trigger="43",
        auto_flash=False,
        hammer_entry=False,
    )

    with pytest.raises(softbsl_install._sb.SoftBSLError, match="not cycled"):
        softbsl_install._sb._session(args)

    assert events == [("enter", "43"), "close"]


def test_engine_composes_with_the_vendored_patcher_library(monkeypatch):
    from engines.patcher import patch_ms41

    seen = {}
    expected = b"patched image"
    def fake_build(base, patch_ids, patches=None):
        seen["base"] = base
        seen["patch_ids"] = patch_ids
        return expected, ["composed"]
    monkeypatch.setattr(patch_ms41, "build", fake_build)

    result = softbsl_install._sb._compose_image(b"base image", ["softbsl_loader", "door_magic"])

    assert result == expected
    assert seen == {"base": b"base image", "patch_ids": ["softbsl_loader", "door_magic"]}


def test_compose_reuses_an_already_applied_patch(monkeypatch):
    from engines.patcher import patch_ms41

    patch = {"id": "installed", "edits": [
        {"off": 0, "expect": "00", "data": "5a"}
    ]}
    monkeypatch.setattr(patch_ms41, "load_patches",
                        lambda: {"installed": patch})
    monkeypatch.setattr(
        patch_ms41, "build",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("build must not run for an already-applied patch")))

    assert softbsl_install._sb._compose_image(b"\x5A", ["installed"]) == b"\x5A"


def test_patch_match_normalizes_only_an_exact_requested_top_marker():
    patch = {"edits": [{
        "off": softbsl_install._sb.MARKER_OFF - 2,
        "data": "1122a55a42bd",
    }]}
    top = bytearray(b"\xFF" * (softbsl_install._sb.MARKER_OFF + 4))
    top[-6:] = bytes.fromhex("1122a55a54ab")

    normalized = softbsl_install._sb._normalize_patch_marker_for_match(
        top, patch, "T")
    assert normalized[-6:] == bytes.fromhex("1122a55a42bd")

    top[-1] ^= 1
    assert softbsl_install._sb._normalize_patch_marker_for_match(
        top, patch, "T") == bytes(top)


def test_golden_top_composer_shares_persistent_install_patch_set():
    from tests.conftest import ref

    image, patch_ids, log = softbsl_install.compose_persistent_target(
        ref("MS41.3"), with_calguard=True, marker="T", chip="29f400")

    assert patch_ids == ["softbsl_loader", "door_magic", "cal_guard", "amd_flash"]
    assert image[0x5FFC:0x6000] == bytes([0xA5, 0x5A, 0x54, 0xAB])
    assert image[0x423C:0x4244] == softbsl_install._sb._DRV_SIG_AMD
    assert any("set bank marker" in line for line in log)

    # Recomposition of an already-patched target is a marker/checksum-safe no-op, not an
    # expect-anchor failure.
    rebuilt, rebuilt_ids, _ = softbsl_install.compose_persistent_target(
        image, with_calguard=True, marker="T", chip="29f400")
    assert rebuilt_ids == ["softbsl_loader", "door_magic", "cal_guard"]
    assert rebuilt == image


@pytest.mark.parametrize("legacy_id", ["cal_guard_v1", "cal_guard_v2"])
def test_ms411_deprecated_calguard_directly_upgrades_during_softbsl_compose(
        legacy_id):
    from engines.patcher.patch_ms41 import build, is_applied, load_patches
    from tests.conftest import ref

    patches = load_patches()
    legacy_image, _ = build(
        ref("MS41.1"), [legacy_id], allow_deprecated=True)

    composed, log = softbsl_install._sb._compose_image(
        legacy_image,
        ["softbsl_loader", "door_magic_ms411", "cal_guard"],
        return_log=True,
    )

    assert is_applied(composed, patches["softbsl_loader"])
    assert is_applied(composed, patches["door_magic_ms411"])
    assert is_applied(composed, patches["cal_guard"])
    assert not is_applied(composed, patches[legacy_id])
    assert any("removed exact predecessor" in line for line in log)


def test_complete_existing_loader_asks_and_allows_reinstall():
    patch = {"id": "softbsl_loader", "edits": [
        {"off": 0, "expect": "00", "data": "5a"}
    ]}
    questions = []
    args = type("Args", (), {
        "confirm_reinstall": lambda self, message: questions.append(message) or True
    })()

    states = softbsl_install._sb._confirm_reinstall(
        args, b"\x5A", {"softbsl_loader": patch}, ["softbsl_loader"])

    assert states == {"softbsl_loader": "applied"}
    assert questions and "already has" in questions[0]


def test_partial_existing_loader_remains_blocked():
    patch = {"id": "softbsl_loader", "edits": [
        {"off": 0, "expect": "00", "data": "5a"},
        {"off": 1, "expect": "00", "data": "a5"},
    ]}
    args = type("Args", (), {"confirm_reinstall": lambda *_args: True})()

    try:
        softbsl_install._sb._confirm_reinstall(
            args, b"\x5A\x00", {"softbsl_loader": patch}, ["softbsl_loader"])
        assert False, "partial loader state must be rejected"
    except softbsl_install._sb.SoftBSLError as error:
        assert "partial/inconsistent" in str(error)


def test_28f_install_scope_erases_main_e_once_and_uses_intel_names():
    sectors, lo, hi = softbsl_install._sb._flash_scope("softbsl", chip="28f200")

    assert [addr for addr, _name, _protected in sectors] == [0x20000, 0x00000]
    assert all("SA" not in name for _addr, name, _protected in sectors)
    assert (lo, hi) == (0, 0x40000)


def test_29f_install_scope_keeps_two_program_high_sectors():
    sectors, _lo, _hi = softbsl_install._sb._flash_scope("softbsl", chip="29f400")
    assert [addr for addr, _name, _protected in sectors] == [0x20000, 0x30000, 0x00000]


def test_checksum_aware_install_scope_rewrites_program_checksum_block_without_cal():
    intel, lo, hi = softbsl_install._sb._flash_scope(
        "softbsl_ms412", chip="28f200")
    amd, amd_lo, amd_hi = softbsl_install._sb._flash_scope(
        "softbsl_ms412", chip="29f400")

    assert [addr for addr, _name, _protected in intel] == [0x20000, 0x02000, 0x00000]
    assert [addr for addr, _name, _protected in amd] == [0x02000, 0x20000, 0x30000, 0x00000]
    assert (lo, hi) == (amd_lo, amd_hi) == (0, 0x40000)
    assert all(addr != 0x10000 for addr, _name, _protected in intel + amd)

    # File 0x6050 maps to CPU 0x2050 and holds the program CRC.
    assert softbsl_install._sb._softbsl_prog_ok(0x2050, include_program_low=True)
    assert not softbsl_install._sb._softbsl_prog_ok(0x2050)


def test_program_only_write_is_checksum_aware_for_ms412_and_ms413():
    from tests.conftest import ref

    assert softbsl_install._sb._effective_flash_scope(
        "program", ref("MS41.2")) == "program_checked"
    assert softbsl_install._sb._effective_flash_scope(
        "program", ref("MS41.3")) == "program_checked"

    intel, lo, hi = softbsl_install._sb._flash_scope(
        "program_checked", chip="28f200")
    amd, amd_lo, amd_hi = softbsl_install._sb._flash_scope(
        "program_checked", chip="29f400")
    assert [address for address, _name, _protected in intel] == [0x20000, 0x02000]
    assert [address for address, _name, _protected in amd] == [0x02000, 0x20000, 0x30000]
    assert (lo, hi) == (amd_lo, amd_hi) == (0, 0x40000)
    assert softbsl_install._sb._scope_prog_ok("program_checked", 0x2050)
    assert softbsl_install._sb._scope_prog_ok("program_checked", 0x24000)
    assert not softbsl_install._sb._scope_prog_ok("program_checked", 0x10000)
    assert not softbsl_install._sb._scope_prog_ok("program_checked", 0x01000)

    log = []
    softbsl_install._sb.flash_dry_run(
        ref("MS41.2"), scope="program", chip="29f400", log=log.append)
    assert any("extending program-only write to param2/SA2" in line for line in log)


def test_softbsl_checksum_gate_requires_ms412_program_crc_when_enabled():
    from tests.conftest import ref

    ms412 = bytearray(ref("MS41.2"))
    assert softbsl_install._sb._check_image_checksums(ms412)[0] is True
    ms412[0x20000] ^= 0x01
    assert softbsl_install._sb._check_image_checksums(ms412)[0] is False

    # MS41.3 deliberately disables the program gate; boot + calibration remain decisive.
    ms413, _details = softbsl_install._sb.checksum.correct_checksums(
        bytearray(ref("MS41.3")), correct_program=False)
    ms413[0x20000] ^= 0x01
    assert softbsl_install._sb._check_image_checksums(ms413)[0] is True


def test_ms413_cal_preservation_includes_the_program_checksum_block():
    assert softbsl_install._sb._ms413_install_scope(True) == "softbsl_ms412"
    assert softbsl_install._sb._ms413_install_scope(False) == "full"


@pytest.mark.parametrize("version", ["MS41.0", "MS41.1", "MS41.2", "MS41.3"])
def test_cal_preservation_selects_the_checksum_aware_scope(version):
    assert softbsl_install._sb._ms41_install_scope(version, True) == "softbsl_ms412"
    assert softbsl_install._sb._ms41_install_scope(version, False) == "full"


def test_persistent_composer_builds_ms412_and_migrates_deprecated_loaders():
    import checksum
    from engines.patcher import patch_ms41
    from tests.conftest import ref

    image, patch_ids, _log = softbsl_install.compose_persistent_target(
        ref("MS41.2"), with_calguard=True, marker="B", chip="29f400")
    assert patch_ids == ["softbsl_loader", "door_magic", "cal_guard", "amd_flash"]
    assert image[0x55A0:0x55A4] == bytes.fromhex("da008c1f")
    assert image[0x5D07:0x5F8B] == ref("MS41.2")[0x5D07:0x5F8B]
    assert checksum.checksum_status(image) == {
        "boot": True, "program": True, "cal": True,
        "prog_disabled": False, "cal_disabled": False,
    }

    patches = patch_ms41.load_patches()
    for old_id in (
            "softbsl_loader_legacy",
            "softbsl_loader_relocated_v1",
            "softbsl_loader_v2",
            "softbsl_loader_v10",
    ):
        for marker in ("B", "T"):
            old, _ = patch_ms41.build(
                ref("MS41.3"), [old_id], allow_deprecated=True, marker=marker)
            migrated, _ids, _ = softbsl_install.compose_persistent_target(
                old, with_calguard=False, marker=marker, chip="29f400")
            assert not patch_ms41.is_applied(migrated, patches[old_id])
            assert patch_ms41.is_applied(
                softbsl_install._sb._normalize_patch_marker_for_match(
                    migrated, patches["softbsl_loader"], marker),
                patches["softbsl_loader"],
            )
            assert migrated[0x5FFE:0x6000] == bytes(
                [ord(marker), ord(marker) ^ 0xFF])
            assert migrated[0x5D07:0x5F8B] == ref("MS41.3")[0x5D07:0x5F8B]


@pytest.mark.parametrize(
    "version,door_id",
    [("MS41.0", "door_magic_ms410"), ("MS41.1", "door_magic_ms411")],
)
def test_persistent_composer_builds_older_softbsl_ports(version, door_id):
    import checksum
    from engines.patcher import patch_ms41
    from tests.conftest import ref

    image, patch_ids, _log = softbsl_install.compose_persistent_target(
        ref(version), with_calguard=True, marker="B", chip="29f400")

    assert patch_ids == ["softbsl_loader", door_id, "cal_guard", "amd_flash"]
    assert patch_ms41.is_applied(image, patch_ms41.load_patches()[door_id])
    assert checksum.checksum_status(image) == {
        "boot": True, "program": True, "cal": True,
        "prog_disabled": False, "cal_disabled": False,
    }


def test_persistent_composer_rejects_a_partial_deprecated_calguard():
    from engines.patcher import patch_ms41
    from tests.conftest import ref

    old, _ = patch_ms41.build(
        ref("MS41.2"), ["softbsl_loader_v10", "cal_guard_v4"],
        allow_deprecated=True)
    old = bytearray(old)
    old[0x5E20] ^= 0x01

    with pytest.raises(
            softbsl_install.SoftBSLInstallError,
            match="deprecated CalGuard is partial/corrupt"):
        softbsl_install.compose_persistent_target(
            old, with_calguard=True, marker="B", chip="29f400")


def test_fixed_relocated_loader_restores_the_hardware_proven_crc_bytes():
    from engines.patcher import patch_ms41
    from tests.conftest import ref

    patches = patch_ms41.load_patches()
    current, _ = patch_ms41.build(ref("MS41.3"), ["softbsl_loader"])
    broken, _ = patch_ms41.build(
        ref("MS41.3"), ["softbsl_loader_relocated_v1"],
        allow_deprecated=True)
    proven = bytes.fromhex(
        "f075e6f500d8e6f6ffff40572d0fa985c0845064e08df04668417c1648402d02"
        "56f601a028d13df708510defc2f427e45c84c2f528e4704540643d02e108db00"
        "e118db00")

    assert current[0x5C32:0x5C32 + len(proven)] == proven
    assert patch_ms41.is_applied(current, patches["softbsl_loader"])
    assert patch_ms41.is_applied(broken, patches["softbsl_loader_relocated_v1"])
    assert broken[0x5C32:0x5C32 + len(proven)] != proven


@pytest.mark.parametrize("version", ["MS41.0", "MS41.1", "MS41.2", "MS41.3"])
@pytest.mark.parametrize(
    "chip, wants_amd",
    [("28f200", False), ("29f200", True), ("29f400", True)],
)
def test_installer_composes_relocated_loader_for_both_flash_families(
        version, chip, wants_amd):
    from engines.patcher import patch_ms41
    from tests.conftest import ref

    stock = ref(version)
    args = softbsl_install._sb.InstallRequest(
        port="COM_TEST", prompt=lambda _message: None, base=stock, chip=chip)
    try:
        softbsl_install._sb._install_resolve_images(args)
        bootstrap = Path(args.bootstrap).read_bytes()
        target = Path(args.target).read_bytes()
        patches = patch_ms41.load_patches()
        bootstrap_door_id, persistent_door_id = softbsl_install._sb._door_patch_ids(
            version)

        for image in (bootstrap, target):
            assert patch_ms41.is_applied(image, patches["softbsl_loader"])
            assert image[0x55A0:0x55A4] == bytes.fromhex("da008c1f")
            assert image[0x5D07:0x5F8B] == stock[0x5D07:0x5F8B]
            assert image[0x4412:0x4416] == bytes.fromhex("4fd87eb7")
            assert image[0x5C32:0x5C36] == bytes.fromhex("f075e6f5")
            assert image[0x5CA0:0x5CA4] == bytes.fromhex("4ed8f7f8")
            assert image[0x5F8C:0x5F90] == bytes.fromhex("f3f853e6")

        assert patch_ms41.is_applied(bootstrap, patches[bootstrap_door_id])
        assert patch_ms41.is_applied(target, patches[persistent_door_id])
        assert patch_ms41.checksum.checksum_status(bootstrap)["program"] is True
        assert patch_ms41.checksum.checksum_status(target)["program"] is True

        assert ("amd_flash" in patches and
                patch_ms41.is_applied(target, patches["amd_flash"])) is wants_amd
        assert ("amd_flash" in patches and
                patch_ms41.is_applied(bootstrap, patches["amd_flash"])) is wants_amd

        amd_tail = patches["amd_flash"]["edits"][-1]
        amd_end = amd_tail["off"] + len(bytes.fromhex(amd_tail["data"]))
        loader_crc = next(edit for edit in patches["softbsl_loader"]["edits"]
                          if edit["off"] == 0x5C32)
        from engines.patcher.cal_guard_exact import CAVE_FILE
        guard = patches["cal_guard"]
        guard_body = next(edit for edit in guard["edits"]
                          if edit["off"] == CAVE_FILE)
        assert amd_end == loader_crc["off"] == 0x5C32
        assert guard_body["off"] + len(bytes.fromhex(guard_body["data"])) == 0x3BF80
    finally:
        if args.target:
            shutil.rmtree(Path(args.target).parent, ignore_errors=True)


def test_reinstall_displaces_shared_alpha_n_cave_only_in_bootstrap():
    """A stock 0x43 slot plus an existing AlphaN/legacy install is recoverable."""
    from engines.patcher import patch_ms41
    from tests.conftest import ref

    stock = ref("MS41.3")
    fixture_patches = patch_ms41.load_patches()
    fixture_patches["door_magic"] = {
        **fixture_patches["door_magic"],
        "requires": [],
    }
    current, _ = patch_ms41.build(
        stock, [
            "softbsl_loader_legacy",
            "door_magic",
            "cal_guard_v1",
            "alphan_failsafe",
        ], patches=fixture_patches, allow_deprecated=True)
    args = softbsl_install._sb.InstallRequest(
        port="COM_TEST", prompt=lambda _message: None, base=current, chip="29f400",
        with_calguard=True,
        confirm_reinstall=lambda _message: True)
    try:
        softbsl_install._sb._install_resolve_images(args)
        bootstrap = Path(args.bootstrap).read_bytes()
        target = Path(args.target).read_bytes()
        patches = patch_ms41.load_patches()

        assert bootstrap[0x27354:0x27358] == bytes.fromhex("da036adb")
        assert target[0x27354:0x27358] == bytes.fromhex("da02cc51")
        assert not patch_ms41.is_applied(bootstrap, patches["alphan_failsafe"])
        assert patch_ms41.is_applied(target, patches["alphan_failsafe"])
        assert patch_ms41.is_applied(target, patches["cal_guard"])
        assert patch_ms41.is_applied(target, patches["softbsl_loader"])
        assert patch_ms41.is_applied(target, patches["door_magic"])
    finally:
        if args.target:
            shutil.rmtree(Path(args.target).parent, ignore_errors=True)


@pytest.mark.parametrize("version", ["MS41.0", "MS41.1", "MS41.2", "MS41.3"])
def test_final_install_verifier_reads_every_relocated_loader_component(
        monkeypatch, version):
    from engines.patcher import patch_ms41
    from tests.conftest import ref

    target, _ids, _log = softbsl_install.compose_persistent_target(
        ref(version), with_calguard=True, marker="B", chip="29f400")
    reads = []

    class FakeDS2:
        def read_mem(self, address, length):
            reads.append((address, length))
            file_offset = address ^ softbsl_install._sb.DESCR
            return target[file_offset:file_offset + length]

        def close(self):
            pass

    monkeypatch.setattr(softbsl_install._sb, "_open", lambda _args: FakeDS2())
    assert softbsl_install._sb._verify_install(object(), target) is True

    assert {(address ^ softbsl_install._sb.DESCR, length)
            for address, length in reads} >= {
        (0x4412, 4), (0x55A0, 4), (0x5C32, 4), (0x5CA0, 4),
        (0x5F8C, 4),
    }
    bootstrap_door_id, persistent_door_id = softbsl_install._sb._door_patch_ids(
        version)
    patches = patch_ms41.load_patches()
    assert {
        (patches[bootstrap_door_id]["cave"]["splice_off"], 4),
        (patches[persistent_door_id]["cave"]["splice_off"], 4),
    } <= {(address ^ softbsl_install._sb.DESCR, length)
          for address, length in reads}


def test_live_variant_gate_uses_program_signature_and_either_cal_marker(monkeypatch):
    monkeypatch.setattr(softbsl_install._sb.time, "sleep", lambda _seconds: None)

    class FakeDS2:
        def __init__(self, memory):
            self.memory = memory
        def read_mem(self, address, length):
            return self.memory.get(address, b"\xFF" * length)[:length]

    common = {
        0x3DA9A: softbsl_install._sb._PROG_SIG_3,
        0x2025: b"1406464",
        0x1000E: b"12000000",
    }
    ss1 = dict(common, **{})
    ss1[0x133BB] = b"SS1v2"
    credit = dict(common, **{})
    credit[0x15F60] = b"ABHISHEK"

    assert softbsl_install._sb._detect_ecu_variant(FakeDS2(ss1)) == (
        "MS41.3", "MS41.3", True)
    assert softbsl_install._sb._detect_ecu_variant(FakeDS2(credit)) == (
        "MS41.3", "MS41.3", True)

    hybrid = dict(credit)
    hybrid[0x3DA9A] = b"\xFF" * 4
    assert softbsl_install._sb._detect_ecu_variant(FakeDS2(hybrid)) == (
        "MS41.3", "MS41.2", False)

    for cal_id, ecu_id, version in (
        (b"41000000", b"1429861", "MS41.0"),
        (b"60000000", b"1437806", "MS41.1"),
        (b"12000000", b"1406464", "MS41.2"),
    ):
        memory = {0x1000E: cal_id, 0x2025: ecu_id}
        assert softbsl_install._sb._detect_ecu_variant(FakeDS2(memory)) == (
            version, version, True)


def test_live_compatibility_gate_reads_the_same_program_and_calibration_ids():
    class FakeDS2:
        def read_mem(self, address, length):
            values = {
                0x1000C: b"0660",
                0x2007: b"0660",
                0x1CF4: b"606",
            }
            return values.get(address, b"\xFF" * length)[:length]

    assert softbsl_install._sb._detect_firmware_compatibility(FakeDS2()) == (
        "0660", "0660", b"606", True)


def test_install_normalization_uses_the_boot_image_family():
    from ms41 import (
        CODING_FAMILY_CAL_ADDRS, CODING_FAMILY_PROGRAM_ADDRS,
        FIRMWARE_COMPAT_CAL_ADDRS, FIRMWARE_COMPAT_PROGRAM_ADDRS, MS41ECU,
    )

    image = bytearray(b"\xFF" * MS41ECU.FULL_ROM_SIZE)
    for address in CODING_FAMILY_PROGRAM_ADDRS:
        image[address:address + 3] = b"909"
    for address in CODING_FAMILY_CAL_ADDRS:
        image[address] = ord("9")
    for address in FIRMWARE_COMPAT_PROGRAM_ADDRS:
        image[address:address + 4] = b"0960"
    for address in FIRMWARE_COMPAT_CAL_ADDRS:
        image[address:address + 4] = b"0960"

    image[MS41ECU.CODING_FAMILY_FILE_ADDR:
          MS41ECU.CODING_FAMILY_FILE_ADDR + 3] = b"606"
    normalized = softbsl_install._sb._normalize_install_image(image)
    assert MS41ECU.read_program_compatibility_id(normalized) == "0660"
    assert MS41ECU.read_calibration_compatibility_id(normalized) == "0660"


def test_same_variant_different_boot_family_aborts_before_phase1(
    tmp_path, monkeypatch
):
    from ms41 import (
        CODING_FAMILY_CAL_ADDRS, CODING_FAMILY_PROGRAM_ADDRS,
        FIRMWARE_COMPAT_CAL_ADDRS, FIRMWARE_COMPAT_PROGRAM_ADDRS, MS41ECU,
    )

    image = bytearray(b"\xFF" * MS41ECU.FULL_ROM_SIZE)
    image[MS41ECU.CODING_FAMILY_FILE_ADDR:
          MS41ECU.CODING_FAMILY_FILE_ADDR + 3] = b"909"
    for address in CODING_FAMILY_PROGRAM_ADDRS:
        image[address:address + 3] = b"909"
    for address in CODING_FAMILY_CAL_ADDRS:
        image[address] = ord("9")
    for address in FIRMWARE_COMPAT_PROGRAM_ADDRS:
        image[address:address + 4] = b"0960"
    for address in FIRMWARE_COMPAT_CAL_ADDRS:
        image[address:address + 4] = b"0960"

    target = tmp_path / "target.bin"
    bootstrap = tmp_path / "bootstrap.bin"
    target.write_bytes(image)
    bootstrap.write_bytes(image)
    preflight = {
        "port": "COM1",
        "uses_d2xx": True,
        "native_fast_capable": True,
        "flash_family": "intel",
        "flash_signature": softbsl_install._sb._DRV_SIG_INTEL,
        "cal_variant": "MS41.1",
        "program_variant": "MS41.1",
        "cal_compatibility_id": "0660",
        "program_compatibility_id": "0660",
        "coding_family": b"606",
        "broad_consistent": True,
        "exact_consistent": True,
        "consistent": True,
    }
    args = SimpleNamespace(
        bootstrap=str(bootstrap),
        target=str(target),
        port="COM1",
        dry_run=False,
        yes=True,
        preserve_cal=True,
        chip="28f200",
        baud="low",
        force=False,
        progress_cb=None,
        bootstrap_verify_ranges=(),
        _live_preflight=preflight,
        confirm_convert=lambda: False,
    )

    monkeypatch.setattr(
        softbsl_install._sb, "_patch_base_version", lambda _image: "MS41.1"
    )
    monkeypatch.setattr(
        softbsl_install._sb,
        "cmd_deploy_splice",
        lambda _args: pytest.fail("Phase 1 must not start without conversion"),
    )

    with pytest.raises(
        softbsl_install._sb.SoftBSLError, match="aborted \\(no conversion\\)"
    ):
        softbsl_install._sb.cmd_install(args)


def test_final_install_patches_really_span_both_29f_program_sectors_and_boot():
    from engines.patcher.patch_ms41 import load_patches
    patches = load_patches()

    door_cpus = [edit["off"] ^ softbsl_install._sb.DESCR
                 for edit in patches["door_magic"]["edits"]]
    loader_cpus = [edit["off"] ^ softbsl_install._sb.DESCR
                   for edit in patches["softbsl_loader"]["edits"]]

    assert {cpu & 0x30000 for cpu in door_cpus} == {0x20000, 0x30000}
    assert all(cpu < 0x2000 for cpu in loader_cpus)


@pytest.mark.parametrize(
    "door_id", ["door_0x43_ms410", "door_0x43_ms411", "door_0x43"])
def test_bootstrap_targeted_verify_only_includes_deployed_0x43_edits(door_id):
    ranges = softbsl_install._sb._bootstrap_verify_ranges(
        ["softbsl_loader", door_id])

    assert len(ranges) == 2
    assert {label for _addr, _size, label in ranges} == {door_id}
    assert all(0x20000 <= addr < 0x40000 for addr, _size, _label in ranges)


def test_install_baud_preflight_falls_back_before_erase(monkeypatch):
    events = []

    class FakeD:
        def close(self):
            events.append("close")

    class FakeSB:
        def __init__(self, fail_tier=None):
            self.fail_tier = fail_tier
        def set_baud(self, tier):
            events.append(("set_baud", tier))
            if tier == self.fail_tier:
                raise softbsl_install._sb.SoftBSLError("noisy")
        def crc_read(self, addr, size):
            events.append(("crc_read", addr, size))
            return b"\x00" * size
        def reset(self):
            events.append("reset")

    sessions = iter([(FakeD(), FakeSB("high")), (FakeD(), FakeSB())])
    monkeypatch.setattr(
        softbsl_install._sb, "_session",
        lambda _args, require_d2xx=False: next(sessions))
    monkeypatch.setattr(softbsl_install._sb.time, "sleep", lambda _seconds: None)
    args = type("Args", (), {"baud": "high"})()

    d, sb, tier = softbsl_install._sb._session_with_baud_fallback(args)

    assert tier == "mid"
    assert ("set_baud", "high") in events and ("set_baud", "mid") in events
    assert "reset" in events and "close" in events
    assert len([event for event in events if isinstance(event, tuple)
                and event[0] == "crc_read"]) == 3


def test_install_43_fast_tier_uses_staged_entry_without_second_baud_command(monkeypatch):
    events = []

    class FakeD:
        def close(self):
            events.append("close")

    class FakeSB:
        def __init__(self, _d):
            self.staged_entry = False

        def enter_staged(self, agent, tier, trigger):
            events.append(("enter_staged", agent, tier, trigger))
            self.staged_entry = True

        def set_baud(self, tier):
            events.append(("set_baud", tier))

        def crc_read(self, addr, size):
            events.append(("crc_read", addr, size))
            return b"\x00" * size

    monkeypatch.setattr(softbsl_install._sb, "load_agent", lambda _path: b"agent")
    monkeypatch.setattr(softbsl_install._sb, "SoftBSL", FakeSB)
    monkeypatch.setattr(softbsl_install._sb, "_open", lambda _args, require_d2xx=False: FakeD())
    args = type("Args", (), {
        "baud": "high", "trigger": "43", "agent": "agent.hex",
    })()

    d, sb, tier = softbsl_install._sb._session_with_baud_fallback(args)

    assert tier == "high"
    assert sb.staged_entry is True
    assert ("enter_staged", b"agent", "high", "43") in events
    assert not any(event[0] == "set_baud" for event in events if isinstance(event, tuple))
    assert len([event for event in events if isinstance(event, tuple)
                and event[0] == "crc_read"]) == 3
    d.close()


def test_install_43_staged_failure_requires_cycle_before_another_trigger(monkeypatch):
    events = []

    class FakeD:
        def __init__(self, label):
            self.label = label

        def close(self):
            events.append(("close", self.label))

    class FakeSB:
        def __init__(self, label):
            self.label = label
            self.staged_entry = False

        def enter_staged(self, _agent, tier, trigger):
            events.append(("enter_staged", self.label, tier, trigger))
            if self.label == "high":
                raise softbsl_install._sb.SoftBSLError("stage rejected")
            self.staged_entry = True

        def crc_read(self, addr, size):
            events.append(("crc_read", self.label, addr, size))
            return b"\x00" * size

        def reset(self):
            events.append(("reset", self.label))

    sessions = iter((FakeD("high"), FakeD("mid")))
    agents = iter((FakeSB("high"), FakeSB("mid")))
    monkeypatch.setattr(softbsl_install._sb, "load_agent", lambda _path: b"agent")
    monkeypatch.setattr(softbsl_install._sb, "SoftBSL", lambda _d: next(agents))
    monkeypatch.setattr(
        softbsl_install._sb, "_open",
        lambda _args, require_d2xx=False: next(sessions),
    )
    monkeypatch.setattr(softbsl_install._sb.time, "sleep", lambda _seconds: None)
    args = type("Args", (), {
        "baud": "high", "trigger": "43", "agent": "agent.hex",
    })()

    with pytest.raises(
        softbsl_install._sb.AgentLinkPreEraseFailure,
        match="ignition cycle is required",
    ):
        softbsl_install._sb._session_with_baud_fallback(args)

    assert ("enter_staged", "high", "high", "43") in events
    assert ("enter_staged", "mid", "mid", "43") not in events
    assert ("reset", "high") not in events
    assert ("close", "high") in events


def test_install_baud_preflight_preserves_each_tier_error(monkeypatch):
    errors = iter(("high stage rejected", "mid stage timeout", "low CRC failed"))
    monkeypatch.setattr(
        softbsl_install._sb,
        "_session",
        lambda _args, require_d2xx=False: (_ for _ in ()).throw(
            softbsl_install._sb.SoftBSLError(next(errors))
        ),
    )
    monkeypatch.setattr(softbsl_install._sb.time, "sleep", lambda _seconds: None)

    with pytest.raises(
        softbsl_install._sb.AgentLinkPreEraseFailure,
    ) as caught:
        softbsl_install._sb._session_with_baud_fallback(
            SimpleNamespace(baud="high")
        )

    detail = str(caught.value)
    assert "high (high stage rejected)" in detail
    assert "mid (mid stage timeout)" in detail
    assert "low (low CRC failed)" in detail


def test_install_phase2_entry_retry_cycles_again_without_repeating_phase1(monkeypatch):
    events = []
    attempts = iter((False, True))
    args = SimpleNamespace(
        keycycle_retry_prompt=lambda message: events.append(("retry", message)) or True,
    )

    monkeypatch.setattr(
        softbsl_install._sb,
        "_install_keycycle",
        lambda _args: events.append("keycycle"),
    )

    def phase2(_args, target, flash_over):
        events.append(("phase2", target, flash_over))
        if not next(attempts):
            raise softbsl_install._sb.AgentLinkPreEraseFailure("high (timeout)")

    monkeypatch.setattr(softbsl_install._sb, "_run_install_target_phase", phase2)
    monkeypatch.setattr(
        softbsl_install._sb,
        "_finish_install",
        lambda _args, target: events.append(("finish", target)),
    )

    softbsl_install._sb._continue_install_after_bootstrap(
        args, b"target", {"scope": "softbsl"}
    )

    assert events[0] == "keycycle"
    assert events.count("keycycle") == 2
    assert len([event for event in events if isinstance(event, tuple)
                and event[0] == "phase2"]) == 2
    assert len([event for event in events if isinstance(event, tuple)
                and event[0] == "retry"]) == 1
    retry_message = next(event[1] for event in events if isinstance(event, tuple)
                         and event[0] == "retry")
    assert "requires an ignition cycle" in retry_message
    assert events[-1] == ("finish", b"target")


def test_install_phase2_entry_cancel_is_typed_before_target_erase(monkeypatch):
    args = SimpleNamespace(keycycle_retry_prompt=lambda _message: False)
    monkeypatch.setattr(softbsl_install._sb, "_install_keycycle", lambda _args: None)
    monkeypatch.setattr(
        softbsl_install._sb,
        "_run_install_target_phase",
        lambda *_args: (_ for _ in ()).throw(
            softbsl_install._sb.AgentLinkPreEraseFailure("low (timeout)")
        ),
    )
    monkeypatch.setattr(
        softbsl_install._sb,
        "_finish_install",
        lambda *_args: pytest.fail("cancelled Phase 2 must not finish"),
    )

    with pytest.raises(softbsl_install._sb.InstallCancelled) as caught:
        softbsl_install._sb._continue_install_after_bootstrap(
            args, b"target", {"scope": "softbsl"}
        )

    assert caught.value.phase == "pre_phase2"
    assert "Phase 2 erase was not started" in str(caught.value)


def test_install_skips_fast_tiers_when_selected_port_is_not_d2xx(monkeypatch):
    events = []

    class FakeD:
        def close(self): events.append("close")

    class FakeSB:
        def crc_read(self, addr, size):
            events.append(("crc_read", addr, size)); return b"\x00" * size

    def session(_args, require_d2xx=False):
        events.append(("session", require_d2xx))
        if require_d2xx:
            raise softbsl_install._sb.D2XXRequiredError("COM1 opened through pyserial")
        return FakeD(), FakeSB()

    monkeypatch.setattr(softbsl_install._sb, "_session", session)
    monkeypatch.setattr(softbsl_install._sb.time, "sleep", lambda _seconds: None)
    args = type("Args", (), {"baud": "high"})()

    _d, _sb, tier = softbsl_install._sb._session_with_baud_fallback(args)
    assert tier == "low"
    assert [event for event in events if event[0] == "session"] == [
        ("session", True), ("session", False)]


def test_install_progress_adapter_accepts_app_ds2_three_argument_callbacks():
    seen = []
    callback = softbsl_install._sb._progress_adapter(
        lambda done, total, label: seen.append((done, total, label)),
        "base read")

    callback(247, 0x40000, "DS2 full read")

    assert seen == [(247, 0x40000, "base read")]


def test_shared_app_ds2_supports_install_finalize(monkeypatch):
    from ds2 import DS2Interface

    d = object.__new__(DS2Interface)
    events = []
    d._prepare = lambda: events.append("prepare")
    d.status = lambda: events.append("status")
    d.unlock_write = lambda **_kwargs: events.append("unlock")
    d.read_mem = lambda addr, size: (b"\xCC" if addr == 0xE659 else b"\x00" * size)
    d._flash_sub = lambda sub, addr: events.append((sub, addr)) or b"\x01"

    ok, status = d.verify_program_region()

    assert (ok, status) == (True, 0x01)
    assert "prepare" in events and "unlock" in events
    assert (0x0F, d._ds2_addr3(d.PROGRAM_VERIFY_DS2_ADDR)) in events


def test_softbsl_entry_uses_shared_state_aware_unlock_without_e659_gate():
    events = []

    class FakeDS2:
        def _prepare(self):
            events.append("prepare")

        def read_mem(self, address, count):
            assert address != 0xE659
            events.append(("read", address, count))
            return b"\x00" * count

        def status(self):
            events.append("status")

        def unlock_write(self):
            events.append("unlock")

    host = softbsl_install._sb.SoftBSL(FakeDS2(), log=lambda _message: None)

    host._ds2_unlock()

    assert events == [
        "prepare",
        ("read", 0x2001, 12),
        "status",
        "unlock",
    ]


def test_bootstrap_unsafe_native_failure_never_starts_legacy_writer(
    tmp_path, monkeypatch
):
    import ds2_native_fast_service

    image = tmp_path / "bootstrap.bin"
    image.write_bytes(b"\xFF" * 0x40000)
    events = []

    class Probe:
        uses_d2xx = True
        native_fast_capable = True

        def close(self):
            events.append("probe_closed")

    def open_probe(_args):
        events.append("open")
        if events.count("open") > 1:
            raise AssertionError("legacy 9600 writer must not be opened")
        return Probe()

    def unsafe_native_failure(*_args, **_kwargs):
        events.append("native_program")
        raise ds2_native_fast_service.NativeFastPreEraseFailure(
            RuntimeError("ambiguous write authorization"),
            safe_legacy_fallback=False,
        )

    monkeypatch.setattr(softbsl_install._sb, "_open", open_probe)
    monkeypatch.setattr(
        softbsl_install._sb,
        "_detect_flash_chip",
        lambda _probe: ("intel", b"INTEL"),
    )
    monkeypatch.setattr(
        softbsl_install._sb.ecu_info,
        "image_chip_family",
        lambda _image: "intel",
    )
    monkeypatch.setattr(
        ds2_native_fast_service,
        "write_program_d2xx",
        unsafe_native_failure,
    )
    args = SimpleNamespace(
        image=str(image),
        dry_run=False,
        yes=True,
        no_finalize=False,
        no_readback=True,
        verify_ranges=(),
        port="COM1",
        progress_cb=None,
    )

    with pytest.raises(
        softbsl_install._sb.SoftBSLError,
        match="without a confirmed low-rate fallback",
    ):
        softbsl_install._sb.cmd_deploy_splice(args)

    assert events == ["open", "probe_closed", "native_program"]


def test_bootstrap_seed_unavailable_never_starts_legacy_writer_even_when_low_is_confirmed(
    tmp_path, monkeypatch
):
    import ds2_native_fast_service

    image = tmp_path / "bootstrap.bin"
    image.write_bytes(b"\xFF" * 0x40000)
    events = []

    class Probe:
        uses_d2xx = True

        native_fast_capable = True

        def close(self):
            events.append("probe_closed")

    def open_probe(_args):
        events.append("open")
        if events.count("open") > 1:
            raise AssertionError("legacy 9600 writer must not be opened")
        return Probe()

    def seed_unavailable(*_args, **_kwargs):
        events.append("native_program")
        raise ds2_native_fast_service.NativeFastPreEraseFailure(
            ds2_native_fast_service.InitialWriteSeedUnavailable(
                "initial write seed unavailable after 2 bounded BMW/0x1E challenges"
            ),
            safe_legacy_fallback=True,
        )

    monkeypatch.setattr(softbsl_install._sb, "_open", open_probe)
    monkeypatch.setattr(
        softbsl_install._sb,
        "_detect_flash_chip",
        lambda _probe: ("intel", b"INTEL"),
    )
    monkeypatch.setattr(
        softbsl_install._sb.ecu_info,
        "image_chip_family",
        lambda _image: "intel",
    )
    monkeypatch.setattr(
        ds2_native_fast_service,
        "write_program_d2xx",
        seed_unavailable,
    )
    args = SimpleNamespace(
        image=str(image),
        dry_run=False,
        yes=True,
        no_finalize=False,
        no_readback=True,
        verify_ranges=(),
        port="COM1",
        progress_cb=None,
    )

    with pytest.raises(
        softbsl_install._sb.SoftBSLError,
        match="not started",
    ) as caught:
        softbsl_install._sb.cmd_deploy_splice(args)

    assert events == ["open", "probe_closed", "native_program"]
    message = str(caught.value).lower()
    assert "nothing was erased" in message
    assert "9600 fallback was not attempted" in message
    assert "turn ignition off" in message
    assert "10 seconds" in message
    assert "turn ignition on" in message


def test_bootstrap_reuses_install_preflight_without_reopening_ds2(
    tmp_path, monkeypatch
):
    import ds2_native_fast_service

    image = tmp_path / "bootstrap.bin"
    image.write_bytes(b"\xFF" * 0x40000)
    calls = []
    preflight = {
        "port": "COM1",
        "uses_d2xx": True,
        "native_fast_capable": True,
        "flash_family": "intel",
        "flash_signature": softbsl_install._sb._DRV_SIG_INTEL,
        "cal_variant": "MS41.3",
        "program_variant": "MS41.3",
        "cal_compatibility_id": "0912",
        "program_compatibility_id": "0912",
        "coding_family": b"909",
        "broad_consistent": True,
        "exact_consistent": True,
        "consistent": True,
    }

    monkeypatch.setattr(
        softbsl_install._sb,
        "_open",
        lambda _args: pytest.fail(
            "Phase-1 must not reopen DS2 after the native-fast base read"
        ),
    )
    monkeypatch.setattr(
        softbsl_install._sb.ecu_info,
        "image_chip_family",
        lambda _image: "intel",
    )
    monkeypatch.setattr(
        ds2_native_fast_service,
        "write_program_d2xx",
        lambda *args, **kwargs: calls.append((args, kwargs))
        or SimpleNamespace(power_cycle_required=True, verified=False),
    )
    args = SimpleNamespace(
        image=str(image),
        dry_run=False,
        yes=True,
        no_finalize=False,
        no_readback=True,
        verify_ranges=(),
        port="COM1",
        progress_cb=None,
        _live_preflight=preflight,
    )

    softbsl_install._sb.cmd_deploy_splice(args)

    assert len(calls) == 1
    assert calls[0][0][0] == "COM1"
    assert calls[0][1]["connected_family"] == "intel"


def test_install_reuses_cached_variant_and_family_for_phase1(monkeypatch, tmp_path):
    bootstrap = tmp_path / "bootstrap.bin"
    target = tmp_path / "target.bin"
    bootstrap.write_bytes(b"\xFF" * 0x40000)
    target.write_bytes(b"\xFF" * 0x40000)
    calls = []
    preflight = {
        "port": "COM1",
        "uses_d2xx": True,
        "native_fast_capable": True,
        "flash_family": "intel",
        "flash_signature": softbsl_install._sb._DRV_SIG_INTEL,
        "cal_variant": "MS41.3",
        "program_variant": "MS41.3",
        "cal_compatibility_id": "0912",
        "program_compatibility_id": "0912",
        "coding_family": b"909",
        "broad_consistent": True,
        "exact_consistent": True,
        "consistent": True,
    }
    args = SimpleNamespace(
        bootstrap=str(bootstrap),
        target=str(target),
        port="COM1",
        dry_run=False,
        yes=True,
        preserve_cal=True,
        chip="28f200",
        baud="low",
        force=False,
        progress_cb=None,
        bootstrap_verify_ranges=(),
        _live_preflight=preflight,
    )

    monkeypatch.setattr(
        softbsl_install._sb,
        "_open",
        lambda _args: pytest.fail(
            "cached preflight must avoid a post-base-read DS2 reopen"
        ),
    )
    monkeypatch.setattr(
        softbsl_install._sb,
        "_patch_base_version",
        lambda _image: "MS41.3",
    )
    monkeypatch.setattr(
        softbsl_install._sb,
        "cmd_deploy_splice",
        lambda phase_args: calls.append(("phase1", phase_args._live_preflight)),
    )
    monkeypatch.setattr(
        softbsl_install._sb,
        "_continue_install_after_bootstrap",
        lambda *_args: calls.append(("continue",)),
    )

    softbsl_install._sb.cmd_install(args)

    assert calls == [("phase1", preflight), ("continue",)]


@pytest.mark.parametrize("failure_kind", ["reentry", "authorization"])
def test_install_phase1_cycle_recovery_reuses_prepared_images_and_live_family(
    failure_kind, monkeypatch, tmp_path
):
    import ds2_native_fast_service

    bootstrap = tmp_path / "bootstrap-retry.bin"
    target = tmp_path / "target-retry.bin"
    bootstrap_bytes = b"\xA5" * 0x40000
    target_bytes = b"\x5A" * 0x40000
    bootstrap.write_bytes(bootstrap_bytes)
    target.write_bytes(target_bytes)
    events = []

    class Probe:
        uses_d2xx = True
        native_fast_capable = True
        closed = False

        def identify(self):
            return b"SHINDE1"

        def close(self):
            self.closed = True
            events.append("probe_closed")

    probe = Probe()
    open_calls = []

    def open_probe(_args):
        open_calls.append(True)
        return probe

    native_calls = []

    def write_program(port, image, **kwargs):
        native_calls.append((port, bytes(image), dict(kwargs)))
        if len(native_calls) == 1:
            if failure_kind == "authorization":
                raise ds2_native_fast_service.NativeFastPreEraseFailure(
                    RuntimeError(
                        "write key acknowledgement: status 0xA2, expected 0xA0"
                    ),
                    safe_legacy_fallback=False,
                    power_cycle_required=True,
                )
            raise ds2_native_fast_service.NativeFastPreEraseFailure(
                ds2_native_fast_service.NativeFastWriteReentryNotReady(
                    "E659 did not reach 0xCC; no challenge, selector, or flash command was sent"
                ),
                safe_legacy_fallback=False,
            )
        return SimpleNamespace(power_cycle_required=True, verified=False)

    prompt_calls = []

    def retry_prompt(port, message):
        assert probe.closed is True
        prompt_calls.append((port, message))
        return True

    args = SimpleNamespace(
        bootstrap=str(bootstrap),
        target=str(target),
        port="COM1",
        dry_run=False,
        yes=True,
        preserve_cal=True,
        chip="auto",
        baud="high",
        force=False,
        progress_cb=None,
        bootstrap_verify_ranges=(),
        phase1_reentry_prompt=retry_prompt,
    )
    resolve_calls = []
    continued = []
    monkeypatch.setattr(
        softbsl_install._sb,
        "_install_resolve_images",
        lambda request: resolve_calls.append(request),
    )
    monkeypatch.setattr(softbsl_install._sb, "_open", open_probe)
    monkeypatch.setattr(
        softbsl_install._sb,
        "_detect_flash_chip",
        lambda _probe: ("intel", softbsl_install._sb._DRV_SIG_INTEL),
    )
    monkeypatch.setattr(
        softbsl_install._sb,
        "_detect_ecu_variant",
        lambda _probe, **_kwargs: ("MS41.3", "MS41.3", True),
    )
    monkeypatch.setattr(
        softbsl_install._sb,
        "_detect_firmware_compatibility",
        lambda _probe: ("0912", "0912", b"909", True),
    )
    monkeypatch.setattr(
        softbsl_install._sb.ecu_info,
        "image_chip_family",
        lambda _image: "intel",
    )
    monkeypatch.setattr(
        softbsl_install._sb, "_patch_base_version", lambda _image: "MS41.3"
    )
    monkeypatch.setattr(
        ds2_native_fast_service, "write_program_d2xx", write_program
    )
    monkeypatch.setattr(
        softbsl_install._sb,
        "_install_keycycle",
        lambda request: continued.append(("keycycle", request)),
    )
    monkeypatch.setattr(
        softbsl_install._sb,
        "_run_install_target_phase",
        lambda request, prepared_target, flash_over: continued.append(
            ("phase2", request, prepared_target, flash_over)
        ),
    )
    monkeypatch.setattr(
        softbsl_install._sb,
        "_finish_install",
        lambda request, prepared_target: continued.append(
            ("finish", request, prepared_target)
        ),
    )

    softbsl_install._sb.cmd_install(args)

    assert len(resolve_calls) == 1
    assert len(open_calls) == 1
    assert len(native_calls) == 2
    assert [call[0] for call in native_calls] == ["COM1", "COM1"]
    assert [call[1] for call in native_calls] == [bootstrap_bytes, bootstrap_bytes]
    assert [call[2]["connected_family"] for call in native_calls] == [
        "intel", "intel"
    ]
    assert len(prompt_calls) == 1
    assert "nothing was erased" in prompt_calls[0][1]
    assert "The serial port has been disconnected and released" in prompt_calls[0][1]
    if failure_kind == "authorization":
        assert "will not retry the key or fall back to slow DS2" in prompt_calls[0][1]
    else:
        assert "No challenge, selector, erase, or flash command" in prompt_calls[0][1]
    assert [step[0] for step in continued] == ["keycycle", "phase2", "finish"]
    assert continued[1][2] == target_bytes
    assert continued[2][2] == target_bytes


@pytest.mark.parametrize(
    ("failure_kind", "cancel_phase"),
    [
        ("reentry", "pre_phase1"),
        ("authorization", "pre_phase1_authorization"),
        ("identity-timeout", "pre_phase1"),
    ],
)
def test_repeated_phase1_cycle_failure_requires_each_decision_and_cancel_is_typed(
    failure_kind, cancel_phase, monkeypatch, tmp_path
):
    import ds2_native_fast_service

    bootstrap = tmp_path / "bootstrap-repeat.bin"
    target = tmp_path / "target-repeat.bin"
    bootstrap.write_bytes(b"\xA5" * 0x40000)
    target.write_bytes(b"\x5A" * 0x40000)
    preflight = {
        "port": "COM1",
        "uses_d2xx": True,
        "native_fast_capable": True,
        "flash_family": "intel",
        "flash_signature": softbsl_install._sb._DRV_SIG_INTEL,
        "cal_variant": "MS41.3",
        "program_variant": "MS41.3",
        "cal_compatibility_id": "0912",
        "program_compatibility_id": "0912",
        "coding_family": b"909",
        "broad_consistent": True,
        "exact_consistent": True,
        "consistent": True,
    }
    decisions = iter((True, False))
    prompt_calls = []
    attempts = []
    reentry_error = ds2_native_fast_service.NativeFastPreEraseFailure(
        ds2_native_fast_service.NativeFastWriteReentryNotReady(
            "E659 did not reach 0xCC; no challenge, selector, or flash command was sent"
        ),
        safe_legacy_fallback=False,
    )
    if failure_kind == "authorization":
        phase1_errors = (ds2_native_fast_service.NativeFastPreEraseFailure(
            RuntimeError("write key acknowledgement: status 0xA2, expected 0xA0"),
            safe_legacy_fallback=False,
            power_cycle_required=True,
        ),) * 2
    elif failure_kind == "identity-timeout":
        phase1_errors = (
            reentry_error,
            ds2_native_fast_service.NativeFastPreEraseFailure(
                ds2_native_fast_service.InitialWriteIdentityNotReady(
                    "normal low DS2 identity did not become ready after 3 bounded attempts"
                ),
                safe_legacy_fallback=False,
            ),
        )
    else:
        phase1_errors = (reentry_error,) * 2
    args = SimpleNamespace(
        bootstrap=str(bootstrap),
        target=str(target),
        port="COM1",
        dry_run=False,
        yes=True,
        preserve_cal=True,
        chip="28f200",
        baud="high",
        force=False,
        progress_cb=None,
        bootstrap_verify_ranges=(),
        _live_preflight=preflight,
        phase1_reentry_prompt=lambda port, message: prompt_calls.append(
            (port, message)
        ) or next(decisions),
    )
    monkeypatch.setattr(
        softbsl_install._sb, "_install_resolve_images", lambda _args: None
    )
    monkeypatch.setattr(
        softbsl_install._sb, "_patch_base_version", lambda _image: "MS41.3"
    )

    def fail_before_erase(phase_args):
        attempts.append(
            (phase_args.image, phase_args.native_fast_retry_only)
        )
        raise phase1_errors[len(attempts) - 1]

    monkeypatch.setattr(
        softbsl_install._sb, "cmd_deploy_splice", fail_before_erase
    )
    monkeypatch.setattr(
        softbsl_install._sb,
        "_continue_install_after_bootstrap",
        lambda *_args: pytest.fail("Phase 2 must not start after cancellation"),
    )

    with pytest.raises(softbsl_install._sb.InstallCancelled) as caught:
        softbsl_install._sb.cmd_install(args)

    assert caught.value.phase == cancel_phase
    assert attempts == [(str(bootstrap), False), (str(bootstrap), True)]
    assert len(prompt_calls) == 2
    if failure_kind == "identity-timeout":
        assert "lost normal DS2 communication" in prompt_calls[1][1]


def test_phase1_native_retry_never_downshifts_to_legacy_writer(monkeypatch, tmp_path):
    import ds2_native_fast_service

    bootstrap = tmp_path / "bootstrap-no-fallback.bin"
    image = b"\xA5" * 0x40000
    bootstrap.write_bytes(image)
    preflight = {
        "port": "COM1",
        "uses_d2xx": True,
        "native_fast_capable": True,
        "flash_family": "intel",
        "flash_signature": softbsl_install._sb._DRV_SIG_INTEL,
        "cal_variant": "MS41.3",
        "program_variant": "MS41.3",
        "cal_compatibility_id": "0912",
        "program_compatibility_id": "0912",
        "coding_family": b"909",
        "broad_consistent": True,
        "exact_consistent": True,
        "consistent": True,
    }
    native_calls = []

    def native_attempt(*_args, **_kwargs):
        native_calls.append(_kwargs["initial_identity_attempts"])
        if len(native_calls) == 1:
            cause = ds2_native_fast_service.NativeFastWriteReentryNotReady(
                "E659 did not reach 0xCC"
            )
        else:
            cause = ds2_native_fast_service.InitialWriteIdentityNotReady(
                "normal low DS2 identity did not become ready after 3 bounded attempts"
            )
        raise ds2_native_fast_service.NativeFastPreEraseFailure(
            cause, safe_legacy_fallback=False
        )

    monkeypatch.setattr(
        softbsl_install._sb,
        "_open",
        lambda _args: pytest.fail("legacy DS2 must not be opened on the retry"),
    )
    monkeypatch.setattr(
        softbsl_install._sb.ecu_info,
        "image_chip_family",
        lambda _image: "intel",
    )
    monkeypatch.setattr(
        ds2_native_fast_service, "write_program_d2xx", native_attempt
    )
    args = SimpleNamespace(
        image=str(bootstrap),
        dry_run=False,
        yes=True,
        no_finalize=False,
        no_readback=True,
        verify_ranges=(),
        port="COM1",
        progress_cb=None,
        _live_preflight=preflight,
        phase1_reentry_recovery=True,
        native_fast_retry_only=False,
    )

    with pytest.raises(ds2_native_fast_service.NativeFastPreEraseFailure):
        softbsl_install._sb.cmd_deploy_splice(args)

    args.native_fast_retry_only = True
    with pytest.raises(
        ds2_native_fast_service.NativeFastPreEraseFailure
    ) as caught:
        softbsl_install._sb.cmd_deploy_splice(args)

    assert caught.value.initial_identity_not_ready is True
    assert caught.value.safe_legacy_fallback is False
    assert native_calls == [1, 3]


@pytest.mark.parametrize(
    "phase1_error",
    [
        pytest.param(
            __import__("ds2_native_fast_service").NativeFastPreEraseFailure(
                __import__("ds2_native_fast_service").InitialWriteSeedUnavailable(
                    "seed unavailable"
                ),
                safe_legacy_fallback=False,
            ),
            id="seed-unavailable",
        ),
        pytest.param(
            __import__("ds2_native_fast_service").NativeFastPreEraseFailure(
                RuntimeError("unrelated pre-erase failure"),
                safe_legacy_fallback=False,
            ),
            id="unrelated-pre-erase",
        ),
    ],
)
def test_phase1_non_cycle_preerase_failures_do_not_prompt(
    phase1_error, monkeypatch, tmp_path
):
    bootstrap = tmp_path / "bootstrap-exclusion.bin"
    target = tmp_path / "target-exclusion.bin"
    bootstrap.write_bytes(b"\xA5" * 0x40000)
    target.write_bytes(b"\x5A" * 0x40000)
    preflight = {
        "port": "COM1",
        "uses_d2xx": True,
        "native_fast_capable": True,
        "flash_family": "intel",
        "flash_signature": softbsl_install._sb._DRV_SIG_INTEL,
        "cal_variant": "MS41.3",
        "program_variant": "MS41.3",
        "cal_compatibility_id": "0912",
        "program_compatibility_id": "0912",
        "coding_family": b"909",
        "broad_consistent": True,
        "exact_consistent": True,
        "consistent": True,
    }
    args = SimpleNamespace(
        bootstrap=str(bootstrap), target=str(target), port="COM1", dry_run=False,
        yes=True, preserve_cal=True, chip="28f200", baud="high", force=False,
        progress_cb=None, bootstrap_verify_ranges=(), _live_preflight=preflight,
        phase1_reentry_prompt=lambda *_args: pytest.fail(
            "only a structured cycle-required failure may prompt"
        ),
    )
    monkeypatch.setattr(
        softbsl_install._sb, "_install_resolve_images", lambda _args: None
    )
    monkeypatch.setattr(
        softbsl_install._sb, "_patch_base_version", lambda _image: "MS41.3"
    )
    monkeypatch.setattr(
        softbsl_install._sb,
        "cmd_deploy_splice",
        lambda _args: (_ for _ in ()).throw(phase1_error),
    )

    with pytest.raises(type(phase1_error)):
        softbsl_install._sb.cmd_install(args)


def test_phase1_posterase_failure_retains_session_without_marker_prompt(
    monkeypatch, tmp_path
):
    import ds2_native_fast_service

    bootstrap = tmp_path / "bootstrap-posterase.bin"
    target = tmp_path / "target-posterase.bin"
    bootstrap.write_bytes(b"\xA5" * 0x40000)
    target.write_bytes(b"\x5A" * 0x40000)
    preflight = {
        "port": "COM1",
        "uses_d2xx": True,
        "native_fast_capable": True,
        "flash_family": "intel",
        "flash_signature": softbsl_install._sb._DRV_SIG_INTEL,
        "cal_variant": "MS41.3",
        "program_variant": "MS41.3",
        "cal_compatibility_id": "0912",
        "program_compatibility_id": "0912",
        "coding_family": b"909",
        "broad_consistent": True,
        "exact_consistent": True,
        "consistent": True,
    }
    retained = SimpleNamespace(
        port="COM1",
        error=RuntimeError("program interrupted"),
        is_open=True,
        retry_supported=True,
    )
    post_erase = ds2_native_fast_service.NativeWriteRecoveryRequired(retained)
    args = SimpleNamespace(
        bootstrap=str(bootstrap), target=str(target), port="COM1", dry_run=False,
        yes=True, preserve_cal=True, chip="28f200", baud="high", force=False,
        progress_cb=None, bootstrap_verify_ranges=(), _live_preflight=preflight,
        phase1_reentry_prompt=lambda *_args: pytest.fail(
            "post-erase failures must retain recovery without the marker prompt"
        ),
    )
    monkeypatch.setattr(
        softbsl_install._sb, "_install_resolve_images", lambda _args: None
    )
    monkeypatch.setattr(
        softbsl_install._sb, "_patch_base_version", lambda _image: "MS41.3"
    )
    monkeypatch.setattr(
        softbsl_install._sb,
        "cmd_deploy_splice",
        lambda _args: (_ for _ in ()).throw(post_erase),
    )

    with pytest.raises(softbsl_install._sb.InstallRecoveryRequired) as caught:
        softbsl_install._sb.cmd_install(args)

    assert caught.value.recovery.phase == "bootstrap"
    assert caught.value.recovery.retained is retained
    assert caught.value.recovery.is_open is True


def test_bootstrap_readback_reports_typed_cumulative_progress():
    from engines.softbsl.ds2 import DS2Interface

    d = object.__new__(DS2Interface)
    def fake_read(lo, total, chunk, progress_cb):
        progress_cb(total, total, "ignored")
        return b"\x00" * total
    d.read_memory_range = fake_read
    progress = []

    ok, total, mismatches, first_bad = d.verify_deployed_program(
        b"\x00" * 0x40000,
        progress_cb=lambda done, size, label: progress.append((done, size, label)))

    assert ok and mismatches == 0 and first_bad is None
    assert total == 0x24000
    assert progress == [
        (0x4000, 0x24000, "bootstrap read-back"),
        (0x24000, 0x24000, "bootstrap read-back"),
    ]
