import os, shutil, sys
from pathlib import Path
from types import SimpleNamespace

import pytest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import softbsl_install


def test_d2xx_available_returns_bool_and_never_raises():
    assert softbsl_install.d2xx_available() in (True, False)


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

    softbsl_install.install_compose(
        "COM4", cached, with_calguard=True, allow_convert=False,
        prompt=lambda _m: None, log=lambda _m: None)

    args = captured["args"]
    assert args.base is None
    assert args.base_bytes == cached
    assert args.ds2_factory is softbsl_install.AppDS2Interface


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


def test_ms412_install_scope_rewrites_program_checksum_block_without_cal():
    intel, lo, hi = softbsl_install._sb._flash_scope(
        "softbsl_ms412", chip="28f200")
    amd, amd_lo, amd_hi = softbsl_install._sb._flash_scope(
        "softbsl_ms412", chip="29f400")

    assert [addr for addr, _name, _protected in intel] == [0x20000, 0x02000, 0x00000]
    assert [addr for addr, _name, _protected in amd] == [0x02000, 0x20000, 0x30000, 0x00000]
    assert (lo, hi) == (amd_lo, amd_hi) == (0, 0x40000)
    assert all(addr != 0x10000 for addr, _name, _protected in intel + amd)

    # File 0x6050 maps to CPU 0x2050 and holds MS41.2's enabled program CRC.
    assert softbsl_install._sb._softbsl_prog_ok(0x2050, include_program_low=True)
    assert not softbsl_install._sb._softbsl_prog_ok(0x2050)


def test_program_only_write_becomes_checksum_aware_for_ms412():
    from tests.conftest import ref

    assert softbsl_install._sb._effective_flash_scope(
        "program", ref("MS41.2")) == "program_checked"
    assert softbsl_install._sb._effective_flash_scope(
        "program", ref("MS41.3")) == "program"

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


def test_ms413_cal_preservation_is_an_explicit_install_scope_gate():
    assert softbsl_install._sb._ms413_install_scope(True) == "softbsl"
    assert softbsl_install._sb._ms413_install_scope(False) == "full"


def test_ms412_cal_preservation_selects_the_checksum_aware_scope():
    assert softbsl_install._sb._ms41_install_scope("MS41.2", True) == "softbsl_ms412"
    assert softbsl_install._sb._ms41_install_scope("MS41.2", False) == "full"


def test_persistent_composer_builds_ms412_and_migrates_deprecated_loaders():
    import checksum
    from engines.patcher import patch_ms41
    from tests.conftest import ref

    image, patch_ids, _log = softbsl_install.compose_persistent_target(
        ref("MS41.2"), with_calguard=True, marker="B", chip="29f400")
    assert patch_ids == ["softbsl_loader", "door_magic", "cal_guard", "amd_flash"]
    assert image[0x55A2:0x55A4] == bytes.fromhex("921d")
    assert checksum.checksum_status(image) == {
        "boot": True, "program": True, "cal": True,
        "prog_disabled": False, "cal_disabled": False,
    }

    patches = patch_ms41.load_patches()
    for old_id in ("softbsl_loader_legacy", "softbsl_loader_relocated_v1"):
        old, _ = patch_ms41.build(ref("MS41.3"), [old_id])
        migrated, _ids, _ = softbsl_install.compose_persistent_target(
            old, with_calguard=False, marker="B", chip="29f400")
        assert not patch_ms41.is_applied(migrated, patches[old_id])
        assert patch_ms41.is_applied(migrated, patches["softbsl_loader"])
        assert migrated[0x5D36:0x5D92] == ref("MS41.3")[0x5D36:0x5D92]


def test_fixed_relocated_loader_restores_the_hardware_proven_crc_bytes():
    from engines.patcher import patch_ms41
    from tests.conftest import ref

    patches = patch_ms41.load_patches()
    current, _ = patch_ms41.build(ref("MS41.3"), ["softbsl_loader"])
    broken, _ = patch_ms41.build(ref("MS41.3"), ["softbsl_loader_relocated_v1"])
    proven = bytes.fromhex(
        "f075e6f500d8e6f6ffff40572d0fa985c0845064e08df04668417c1648402d02"
        "56f601a028d13df708510defc2f427e45c84c2f528e4704540643d02e108db00"
        "e118db00")

    assert current[0x5C32:0x5C32 + len(proven)] == proven
    assert patch_ms41.is_applied(current, patches["softbsl_loader"])
    assert patch_ms41.is_applied(broken, patches["softbsl_loader_relocated_v1"])
    assert broken[0x5C32:0x5C32 + len(proven)] != proven


@pytest.mark.parametrize("version", ["MS41.2", "MS41.3"])
@pytest.mark.parametrize("chip, wants_amd", [("28f200", False), ("29f400", True)])
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

        for image in (bootstrap, target):
            assert patch_ms41.is_applied(image, patches["softbsl_loader"])
            assert image[0x55A0:0x55A4] == bytes.fromhex("da00921d")
            assert image[0x5D36:0x5D92] == stock[0x5D36:0x5D92]
            assert image[0x5C32:0x5C36] == bytes.fromhex("f075e6f5")
            assert image[0x5D92:0x5D96] == bytes.fromhex("f3f853e6")
            assert image[0x5FC4:0x5FC8] == bytes.fromhex("4fd87eb7")

        assert ("amd_flash" in patches and
                patch_ms41.is_applied(target, patches["amd_flash"])) is wants_amd
        assert ("amd_flash" in patches and
                patch_ms41.is_applied(bootstrap, patches["amd_flash"])) is wants_amd

        amd_tail = patches["amd_flash"]["edits"][-1]
        amd_end = amd_tail["off"] + len(bytes.fromhex(amd_tail["data"]))
        loader_crc = next(edit for edit in patches["softbsl_loader"]["edits"]
                          if edit["off"] == 0x5C32)
        guard = patches["cal_guard"]
        guard_body = next(edit for edit in guard["edits"]
                          if edit["off"] == guard["cave"]["base"])
        assert amd_end == loader_crc["off"] == 0x5C32
        assert guard_body["off"] + len(bytes.fromhex(guard_body["data"])) == 0x5FC4
    finally:
        if args.target:
            shutil.rmtree(Path(args.target).parent, ignore_errors=True)


def test_reinstall_displaces_shared_alpha_n_cave_only_in_bootstrap():
    """A stock 0x43 slot plus an existing AlphaN/legacy install is recoverable."""
    from engines.patcher import patch_ms41
    from tests.conftest import ref

    stock = ref("MS41.3")
    current, _ = patch_ms41.build(
        stock, ["softbsl_loader_legacy", "door_magic", "cal_guard", "alphan_failsafe"])
    args = softbsl_install._sb.InstallRequest(
        port="COM_TEST", prompt=lambda _message: None, base=current, chip="29f400",
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


def test_final_install_verifier_reads_every_relocated_loader_component(monkeypatch):
    from tests.conftest import ref

    target, _ids, _log = softbsl_install.compose_persistent_target(
        ref("MS41.2"), with_calguard=True, marker="B", chip="29f400")
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
        (0x55A0, 4), (0x5C32, 4), (0x5D92, 4), (0x5FC4, 4),
    }


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


def test_final_install_patches_really_span_both_29f_program_sectors_and_boot():
    from engines.patcher.patch_ms41 import load_patches
    patches = load_patches()

    door_cpus = [edit["off"] ^ softbsl_install._sb.DESCR
                 for edit in patches["door_magic"]["edits"]]
    loader_cpus = [edit["off"] ^ softbsl_install._sb.DESCR
                   for edit in patches["softbsl_loader"]["edits"]]

    assert {cpu & 0x30000 for cpu in door_cpus} == {0x20000, 0x30000}
    assert all(cpu < 0x2000 for cpu in loader_cpus)


def test_bootstrap_targeted_verify_only_includes_deployed_0x43_edits():
    ranges = softbsl_install._sb._bootstrap_verify_ranges(
        ["softbsl_loader", "door_0x43"])

    assert len(ranges) == 2
    assert {label for _addr, _size, label in ranges} == {"door_0x43"}
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


def test_install_43_staged_failure_falls_back_without_resetting_pre_erase_agent(monkeypatch):
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

    d, _sb, tier = softbsl_install._sb._session_with_baud_fallback(args)

    assert tier == "mid"
    assert ("enter_staged", "high", "high", "43") in events
    assert ("enter_staged", "mid", "mid", "43") in events
    assert ("reset", "high") not in events
    assert ("close", "high") in events
    d.close()


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
    d.unlock_write = lambda: events.append("unlock")
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

    assert events == ["prepare", ("read", 0x2001, 12), "status", "unlock"]


def test_bootstrap_unsafe_native_failure_never_starts_legacy_writer(
    tmp_path, monkeypatch
):
    import ds2_native_fast_service

    image = tmp_path / "bootstrap.bin"
    image.write_bytes(b"\xFF" * 0x40000)
    events = []

    class Probe:
        uses_d2xx = True

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
