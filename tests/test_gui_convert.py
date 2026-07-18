import os, sys
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import pytest

try:
    from PyQt5.QtWidgets import QApplication, QMessageBox, QFileDialog, QInputDialog
    import gui
    import identity
    import backup_manager
    _HAS_QT = True
except Exception:
    _HAS_QT = False

pytestmark = pytest.mark.skipif(not _HAS_QT, reason="PyQt5 not available")

from tests.conftest import ref
import patch_service
import ecu_info


def _gui():
    app = QApplication.instance() or QApplication([])
    w = gui.MS41FlashGUI()
    # Full-write tests model a normally connected stock 28F200 ECU unless the
    # individual geometry-mismatch test explicitly overrides this signature.
    w._ecu_chip_sig = bytes.fromhex("e6f45000b84c6fe0")
    w.chk_verify.setChecked(False)
    w.chk_correct_cksum.setChecked(False)
    return app, w


def _warning_router(monkeypatch, rules, default=None):
    """Route QMessageBox.warning by a substring of its title (args[1]) to a canned
    reply, so a test can answer the 'checksum not valid' and 'variant conversion'
    dialogs differently without a real UI."""
    default = QMessageBox.Yes if default is None else default

    def fake_warning(*args, **kwargs):
        title = args[1] if len(args) > 1 else ""
        for needle, reply in rules.items():
            if needle in title:
                return reply
        return default

    monkeypatch.setattr(QMessageBox, "warning", fake_warning)


def _stub_run_task(monkeypatch, w):
    """Run the task function synchronously (no QThread) and capture the image
    actually handed to _ds2_write, without touching real serial I/O."""
    captured = {}

    def fake_ds2_write(kind, image_bytes, progress_fn, log_fn):
        captured["kind"] = kind
        captured["image"] = image_bytes

    monkeypatch.setattr(w, "_ds2_write", fake_ds2_write)

    def fake_run_task(task_fn, on_success=None, on_failure=None):
        result = task_fn(lambda *a, **k: None, lambda *a, **k: None)
        captured["ran"] = True
        if on_success:
            on_success(result)

    monkeypatch.setattr(w, "_run_task", fake_run_task)
    return captured


def test_full_write_blocks_built_amd_patch_on_connected_intel(monkeypatch):
    app, w = _gui()
    try:
        image, _log = patch_service.build_image(ref("MS41.3"), ["amd_flash"])
        w._ecu_variant = "MS41.3"
        w._ecu_chip_sig = bytes.fromhex("e6f45000b84c6fe0")
        blocked = []
        monkeypatch.setattr(
            QMessageBox, "critical",
            lambda *args, **_kwargs: blocked.append((args[1], args[2])) or QMessageBox.Ok)
        monkeypatch.setattr(QMessageBox, "warning", lambda *args, **kwargs: QMessageBox.Yes)
        captured = _stub_run_task(monkeypatch, w)

        w._ds2_write_full(bytearray(image), "ms413-amd.bin")

        assert captured.get("ran") is not True
        assert blocked and blocked[-1][0] == "Flash-Chip Family Mismatch"
        assert "AMD/JEDEC" in blocked[-1][1] and "Intel 28F" in blocked[-1][1]
    finally:
        w.close()


def test_full_write_blocks_intel_image_on_connected_amd(monkeypatch):
    app, w = _gui()
    try:
        image = ref("MS41.3")
        assert ecu_info.image_chip_family(image) == "intel"
        w._ecu_variant = "MS41.3"
        w._ecu_chip_sig = bytes.fromhex("e00e0d58f04ec084")
        blocked = []
        monkeypatch.setattr(
            QMessageBox, "critical",
            lambda *args, **_kwargs: blocked.append((args[1], args[2])) or QMessageBox.Ok)
        monkeypatch.setattr(QMessageBox, "warning", lambda *args, **kwargs: QMessageBox.Yes)
        captured = _stub_run_task(monkeypatch, w)

        w._ds2_write_full(bytearray(image), "ms413-intel.bin")

        assert captured.get("ran") is not True
        assert blocked and blocked[-1][0] == "Flash-Chip Family Mismatch"
        assert "Intel 28F" in blocked[-1][1] and "AMD/JEDEC" in blocked[-1][1]
    finally:
        w.close()


def test_conversion_warning_policy_is_bidirectional_for_ms41_0():
    supported_pairs = (
        ("MS41.1", "MS41.2"), ("MS41.2", "MS41.1"),
        ("MS41.1", "MS41.3"), ("MS41.3", "MS41.2"),
    )
    for source, target in supported_pairs:
        title, risk, ms410 = gui.MS41FlashGUI._conversion_warning_policy(
            source, target, False)
        assert title == "Confirm Variant Conversion"
        assert "supported" in risk
        assert ms410 is False

    for source, target in (("MS41.0", "MS41.1"), ("MS41.3", "MS41.0")):
        title, risk, ms410 = gui.MS41FlashGUI._conversion_warning_policy(
            source, target, False)
        assert "MS41.0" in title and "Brick Risk" in title
        assert "existing boot/parameter region" in risk
        assert ms410 is True

        title, risk, ms410 = gui.MS41FlashGUI._conversion_warning_policy(
            source, target, True)
        assert "MS41.0" in title and "Boot Write" in title
        assert "compatible hardware" in risk
        assert "not yet been validated" in risk
        assert ms410 is True


def test_ms41_0_conversion_is_allowed_after_explicit_untested_warning(monkeypatch):
    app, w = _gui()
    try:
        w._ecu_variant = "MS41.0"
        warnings = []
        monkeypatch.setattr(
            QMessageBox, "warning",
            lambda *a, **k: warnings.append((a[1], a[2])) or QMessageBox.Yes)
        monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
        monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: QMessageBox.Ok)
        captured = _stub_run_task(monkeypatch, w)

        w._ds2_write_full(bytearray(ref("MS41.3")), "target.bin")

        assert captured.get("ran") is True
        conversion = next(item for item in warnings if "MS41.0 Conversion" in item[0])
        assert "not yet been validated" in conversion[1]
        assert "hardware BSL" in conversion[1]
    finally:
        w.close()


def test_conversion_into_ms41_0_is_allowed_after_explicit_untested_warning(monkeypatch):
    app, w = _gui()
    try:
        w._ecu_variant = "MS41.2"
        warnings = []
        monkeypatch.setattr(
            QMessageBox, "warning",
            lambda *a, **k: warnings.append((a[1], a[2])) or QMessageBox.Yes)
        monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
        monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: QMessageBox.Ok)
        captured = _stub_run_task(monkeypatch, w)

        w._ds2_write_full(bytearray(ref("MS41.0")), "target-ms410.bin")

        assert captured.get("ran") is True
        conversion = next(item for item in warnings if "MS41.0 Conversion" in item[0])
        assert "existing boot/parameter region" in conversion[1]
        assert "may not boot" in conversion[1]
    finally:
        w.close()


def test_conversion_without_a_prior_full_read_is_allowed_when_boot_is_preserved(monkeypatch):
    app, w = _gui()
    try:
        w._ecu_variant = "MS41.1"
        w._last_full_read = None
        _warning_router(monkeypatch, {"Variant Conversion": QMessageBox.Yes})
        critical_calls = []
        monkeypatch.setattr(QMessageBox, "critical",
                            lambda *a, **k: critical_calls.append(a) or QMessageBox.Ok)
        monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
        monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: QMessageBox.Ok)
        captured = _stub_run_task(monkeypatch, w)

        w._ds2_write_full(bytearray(ref("MS41.3")), "target.bin")

        assert not critical_calls
        assert captured.get("ran") is True
    finally:
        w.close()


def test_boot_write_grafts_live_identity_onto_conversion_target(monkeypatch):
    app, w = _gui()
    try:
        import softbsl_service

        source = ref("MS41.1")
        target = bytearray(ref("MS41.3"))

        w._ecu_variant = "MS41.1"
        w._last_full_read = None
        w._ecu_identity_source = source
        monkeypatch.setattr(w, "_fast_read_available", lambda: True)
        w.chk_bootloader_write.setEnabled(True)
        w.chk_bootloader_write.setChecked(True)
        assert w.chk_boot_preserve_identity.isChecked() is True
        _warning_router(monkeypatch, {"Checksum": QMessageBox.Yes,
                                      "Variant Conversion": QMessageBox.Yes})
        monkeypatch.setattr(QInputDialog, "getText",
                            lambda *a, **k: ("WRITE BOOT", True))
        monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
        monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: QMessageBox.Ok)
        captured = {}
        monkeypatch.setattr(
            softbsl_service, "run_flash",
            lambda port, image, *a, **k: captured.update(image=image, kwargs=k))
        monkeypatch.setattr(
            w, "_run_via_softbsl",
            lambda operation, log_fn, progress_fn: operation("COM1", progress_fn, log_fn))

        def fake_run_task(task_fn, on_success=None, on_failure=None):
            result = task_fn(lambda *a, **k: None, lambda *a, **k: None)
            captured["ran"] = True
            if on_success:
                on_success(result)

        monkeypatch.setattr(w, "_run_task", fake_run_task)

        w._ds2_write_full(target, "target.bin")

        assert captured.get("ran") is True
        written = captured["image"]
        assert captured["kwargs"]["write_bootloader"] is True
        src_info = identity.decode_identity(source)
        out_info = identity.decode_identity(written)
        assert out_info.serial == src_info.serial
        assert out_info.isn4 == src_info.isn4
        assert out_info.vin == src_info.vin
        # everything outside the graft still comes from the target file
        assert written[:0x5CE5] == bytes(ref("MS41.3"))[:0x5CE5]
    finally:
        w.close()


def test_boot_write_preservation_blocks_when_live_identity_is_unavailable(monkeypatch):
    app, w = _gui()
    try:
        w._ecu_variant = "MS41.1"
        w._last_full_read = None
        w._ecu_identity_source = None
        monkeypatch.setattr(w, "_fast_read_available", lambda: True)
        w.chk_bootloader_write.setEnabled(True)
        w.chk_bootloader_write.setChecked(True)
        _warning_router(monkeypatch, {"Checksum": QMessageBox.Yes})
        critical_calls = []
        monkeypatch.setattr(QMessageBox, "critical",
                            lambda *a, **k: critical_calls.append(a) or QMessageBox.Ok)
        captured = _stub_run_task(monkeypatch, w)

        w._ds2_write_full(bytearray(ref("MS41.3")), "target.bin")

        assert critical_calls
        assert "VIN / ISN Preservation Unavailable" in critical_calls[0][1]
        assert "ran" not in captured
    finally:
        w.close()


def test_required_identity_boot_write_has_no_ds2_fallback(monkeypatch):
    app, w = _gui()
    try:
        w._ecu_variant = "MS41.1"
        monkeypatch.setattr(w, "_fast_read_available", lambda: False)
        _warning_router(monkeypatch, {"Checksum": QMessageBox.Yes})
        critical_calls = []
        monkeypatch.setattr(QMessageBox, "critical",
                            lambda *a, **k: critical_calls.append(a) or QMessageBox.Ok)
        captured = _stub_run_task(monkeypatch, w)

        w._ds2_write_full(
            bytearray(ref("MS41.1")), "identity.bin",
            require_boot_write=True, preserve_boot_identity=False)

        assert critical_calls
        assert "Soft-BSL Boot Write Required" in critical_calls[0][1]
        assert "DS2 cannot write" in critical_calls[0][2]
        assert "ran" not in captured
    finally:
        w.close()


def test_ms41_0_boot_conversion_warning_explains_untested_but_compatible_path(monkeypatch):
    app, w = _gui()
    try:
        w._ecu_variant = "MS41.0"
        w._ecu_identity_source = ref("MS41.0")
        monkeypatch.setattr(w, "_fast_read_available", lambda: True)
        w.chk_bootloader_write.setEnabled(True)
        w.chk_bootloader_write.setChecked(True)
        warnings = []

        def warning(*args, **kwargs):
            warnings.append((args[1], args[2]))
            return (QMessageBox.No if "MS41.0 Conversion" in args[1]
                    else QMessageBox.Yes)

        monkeypatch.setattr(QMessageBox, "warning", warning)
        captured = _stub_run_task(monkeypatch, w)

        w._ds2_write_full(bytearray(ref("MS41.3")), "target.bin")

        title, message = next(item for item in warnings if "MS41.0 Conversion" in item[0])
        assert "compatible hardware" in message
        assert "not yet been validated" in message
        assert "boot/parameter region will also be overwritten" in message
        assert "ran" not in captured
    finally:
        w.close()


def test_boot_write_can_deliberately_use_the_rom_file_identity(monkeypatch):
    app, w = _gui()
    try:
        import softbsl_service

        target = bytes(ref("MS41.3"))
        w._ecu_variant = "MS41.3"
        w._ecu_identity_source = None
        monkeypatch.setattr(w, "_fast_read_available", lambda: True)
        w.chk_bootloader_write.setEnabled(True)
        w.chk_bootloader_write.setChecked(True)
        w.chk_boot_preserve_identity.setChecked(False)
        _warning_router(monkeypatch, {}, default=QMessageBox.Yes)
        monkeypatch.setattr(QInputDialog, "getText",
                            lambda *a, **k: ("WRITE BOOT", True))
        monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
        monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: QMessageBox.Ok)
        captured = {}
        monkeypatch.setattr(
            softbsl_service, "run_flash",
            lambda port, image, *a, **k: captured.update(image=image, kwargs=k))
        monkeypatch.setattr(
            w, "_run_via_softbsl",
            lambda operation, log_fn, progress_fn: operation("COM1", progress_fn, log_fn))

        def fake_run_task(task_fn, on_success=None, on_failure=None):
            result = task_fn(lambda *a, **k: None, lambda *a, **k: None)
            captured["ran"] = True
            if on_success:
                on_success(result)

        monkeypatch.setattr(w, "_run_task", fake_run_task)

        w._ds2_write_full(bytearray(target), "target.bin")

        assert captured.get("ran") is True
        assert captured["kwargs"]["write_bootloader"] is True
        assert captured["image"] == target
    finally:
        w.close()


def test_required_identity_write_forces_boot_without_toggling_flash_tab_option(monkeypatch):
    app, w = _gui()
    try:
        import softbsl_service

        target = bytes(ref("MS41.3"))
        w._ecu_variant = "MS41.3"
        w._ecu_identity_source = None
        monkeypatch.setattr(w, "_fast_read_available", lambda: True)
        assert w.chk_bootloader_write.isChecked() is False
        assert w.chk_boot_preserve_identity.isChecked() is True
        _warning_router(monkeypatch, {}, default=QMessageBox.Yes)
        monkeypatch.setattr(QInputDialog, "getText",
                            lambda *a, **k: ("WRITE BOOT", True))
        monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
        monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: QMessageBox.Ok)
        captured = {}
        monkeypatch.setattr(
            softbsl_service, "run_flash",
            lambda port, image, *a, **k: captured.update(image=image, kwargs=k))
        monkeypatch.setattr(
            w, "_run_via_softbsl",
            lambda operation, log_fn, progress_fn: operation("COM1", progress_fn, log_fn))

        def fake_run_task(task_fn, on_success=None, on_failure=None):
            result = task_fn(lambda *a, **k: None, lambda *a, **k: None)
            captured["ran"] = True
            if on_success:
                on_success(result)

        monkeypatch.setattr(w, "_run_task", fake_run_task)

        w._ds2_write_full(
            bytearray(target), "identity.bin",
            require_boot_write=True, preserve_boot_identity=False)

        assert captured.get("ran") is True
        assert captured["kwargs"]["write_bootloader"] is True
        assert captured["image"] == target
        assert w.chk_bootloader_write.isChecked() is False
    finally:
        w.close()


def test_hybrid_target_remains_hard_blocked(monkeypatch):
    app, w = _gui()
    try:
        from ms41 import MS41ECU, SS1V2_PROG_SIG_ADDR, SS1V2_PROG_SIG

        hybrid = bytearray(ref("MS41.3"))
        hybrid[SS1V2_PROG_SIG_ADDR:SS1V2_PROG_SIG_ADDR + len(SS1V2_PROG_SIG)] = \
            b"\xFF" * len(SS1V2_PROG_SIG)
        assert MS41ECU.check_hybrid(hybrid) is not None
        _warning_router(monkeypatch, {"Checksum": QMessageBox.Yes})
        critical_calls = []
        monkeypatch.setattr(QMessageBox, "critical",
                            lambda *a, **k: critical_calls.append(a) or QMessageBox.Ok)
        captured = _stub_run_task(monkeypatch, w)

        w._ds2_write_full(hybrid, "hybrid.bin")

        assert critical_calls
        assert "Hybrid ROM" in critical_calls[0][1]
        assert "ran" not in captured
    finally:
        w.close()


def test_conversion_declined_at_the_conversion_warning_does_not_flash(monkeypatch):
    app, w = _gui()
    try:
        w._ecu_variant = "MS41.1"
        w._last_full_read = ref("MS41.1")
        _warning_router(monkeypatch, {"Checksum": QMessageBox.Yes,
                                      "Variant Conversion": QMessageBox.No})
        captured = _stub_run_task(monkeypatch, w)

        w._ds2_write_full(bytearray(ref("MS41.3")), "target.bin")

        assert "ran" not in captured
    finally:
        w.close()


def test_full_writer_reuses_archived_prewrite_image_without_duplicate_read(monkeypatch):
    app, w = _gui()
    try:
        image = bytearray(b"\xFF" * gui.MS41ECU.FULL_ROM_SIZE)
        w.chk_backup_before_write.setChecked(True)
        monkeypatch.setattr(gui, "verify_checksum", lambda data: (True, []))
        monkeypatch.setattr(
            gui.MS41ECU, "check_hybrid", staticmethod(lambda data: None))
        monkeypatch.setattr(
            gui.softbsl_service, "validate_flash_image_family",
            lambda *a, **k: None)
        monkeypatch.setattr(
            gui.patch_service, "boot_write_patches_in", lambda data: [])
        monkeypatch.setattr(w, "_auto_transfer_route", lambda: "ds2")
        monkeypatch.setattr(
            w, "_read_image_auto",
            lambda *a, **k: pytest.fail(
                "an archived pre-write full image must suppress a duplicate ECU read"))
        monkeypatch.setattr(
            QMessageBox, "warning", staticmethod(lambda *a, **k: QMessageBox.Yes))
        monkeypatch.setattr(
            QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
        monkeypatch.setattr(
            QMessageBox, "information", staticmethod(lambda *a, **k: QMessageBox.Ok))
        captured = _stub_run_task(monkeypatch, w)
        callback = []

        w._ds2_write_full(
            image, "config-full.bin",
            archived_prewrite_image=bytes(image),
            on_write_success=lambda: callback.append(True))

        assert captured["ran"] is True
        assert captured["kind"] == "full"
        assert captured["image"] == bytes(image)
        assert callback == [True]
    finally:
        w.close()


class _FakeDS2:
    def __init__(self, data):
        self._data = data

    def read_full(self, progress_cb=None, log_fn=None):
        return self._data


def test_full_read_caches_identity_source_and_auto_archives(tmp_path, monkeypatch):
    app, w = _gui()
    try:
        backups = tmp_path / "backups"
        monkeypatch.setattr(backup_manager, "BACKUP_DIR", str(backups))
        monkeypatch.setattr(backup_manager, "INDEX_FILE", str(backups / "index.json"))
        w._backup_mgr = backup_manager.BackupManager()

        source = ref("MS41.1")
        w._ds2 = _FakeDS2(source)
        out_path = tmp_path / "out.bin"
        monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(out_path), ""))
        monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: QMessageBox.Ok)

        def fake_run_task(task_fn, on_success=None, on_failure=None):
            result = task_fn(lambda *a, **k: None, lambda *a, **k: None)
            if on_success:
                on_success(result)

        monkeypatch.setattr(w, "_run_task", fake_run_task)

        w._on_read("full")

        assert w._last_full_read == source
        assert len(w._backup_mgr.entries) == 1
        assert w._backup_mgr.entries[0].source == "ECU read"
        assert w._backup_mgr.entries[0].program_variant == "MS41.1"
    finally:
        w.close()


def test_graft_softbsl_target_passes_through_with_no_cached_read(tmp_path):
    app, w = _gui()
    try:
        target_path = tmp_path / "target.bin"
        target_path.write_bytes(ref("MS41.3"))
        w._last_full_read = None

        out_path, info = w._graft_softbsl_target(str(target_path))

        assert out_path == str(target_path)
        assert info is None
    finally:
        w.close()


def test_graft_softbsl_target_grafts_cached_identity(tmp_path):
    app, w = _gui()
    try:
        source = ref("MS41.1")
        target_path = tmp_path / "target.bin"
        target_path.write_bytes(ref("MS41.3"))
        w._last_full_read = source

        out_path, info = w._graft_softbsl_target(str(target_path))

        assert out_path != str(target_path)
        src_info = identity.decode_identity(source)
        assert info.serial == src_info.serial
        with open(out_path, "rb") as f:
            grafted = f.read()
        out_info = identity.decode_identity(grafted)
        assert out_info.serial == src_info.serial
        assert out_info.vin == src_info.vin
        os.remove(out_path)
    finally:
        w.close()
