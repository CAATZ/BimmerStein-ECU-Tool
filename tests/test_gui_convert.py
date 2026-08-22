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
from ms41 import (
    CODING_FAMILY_CAL_ADDRS,
    CODING_FAMILY_FILE_ADDR,
    CODING_FAMILY_PARTIAL_ADDRS,
    CODING_FAMILY_PROGRAM_ADDRS,
)


class _CodingFamilyDS2:
    def __init__(self, family):
        self.family = bytes(family)
        self.reads = []

    def read_mem(self, address, length):
        self.reads.append((address, length))
        assert (address, length) == (gui.MS41ECU.CODING_FAMILY_DS2_ADDR, 3)
        return self.family

    def close(self):
        pass


def _gui():
    app = QApplication.instance() or QApplication([])
    w = gui.MS41FlashGUI()
    # Full-write tests model a normally connected stock 28F200 ECU unless the
    # individual geometry-mismatch test explicitly overrides this signature.
    w._ecu_chip_sig = bytes.fromhex("e6f45000b84c6fe0")
    w.chk_verify.setChecked(False)
    w.chk_correct_cksum.setChecked(False)
    return app, w


def test_same_broad_variant_conversion_uses_program_version():
    assert gui._is_firmware_conversion(
        "MS41.0", "MS41.0", "0659", "0641")
    assert not gui._is_firmware_conversion(
        "MS41.0", "MS41.0", "0641", "0641")


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


def test_conversion_warning_policy_supports_all_variants():
    title, risk = gui.MS41FlashGUI._conversion_warning_policy()
    assert title == "Confirm Variant Conversion"
    assert "MS41.0/MS41.1/MS41.2/MS41.3" in risk
    assert "supported" in risk


def test_ms41_0_conversion_uses_common_confirmation(monkeypatch):
    app, w = _gui()
    try:
        w._ecu_variant = "MS41.0"
        w._last_full_read = ref("MS41.0")
        warnings = []
        monkeypatch.setattr(
            QMessageBox, "warning",
            lambda *a, **k: warnings.append((a[1], a[2])) or QMessageBox.Yes)
        monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
        monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: QMessageBox.Ok)
        captured = _stub_run_task(monkeypatch, w)

        w._ds2_write_full(bytearray(ref("MS41.3")), "target.bin")

        assert captured.get("ran") is True
        conversion = next(item for item in warnings if item[0] == "Confirm Variant Conversion")
        assert "MS41.0/MS41.1/MS41.2/MS41.3" in conversion[1]
    finally:
        w.close()


def test_conversion_into_ms41_0_uses_common_confirmation(monkeypatch):
    app, w = _gui()
    try:
        w._ecu_variant = "MS41.2"
        w._last_full_read = ref("MS41.2")
        warnings = []
        monkeypatch.setattr(
            QMessageBox, "warning",
            lambda *a, **k: warnings.append((a[1], a[2])) or QMessageBox.Yes)
        monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
        monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: QMessageBox.Ok)
        captured = _stub_run_task(monkeypatch, w)

        w._ds2_write_full(bytearray(ref("MS41.0")), "target-ms410.bin")

        assert captured.get("ran") is True
        conversion = next(item for item in warnings if item[0] == "Confirm Variant Conversion")
        assert "MS41.0/MS41.1/MS41.2/MS41.3" in conversion[1]
    finally:
        w.close()


def test_conversion_without_a_prior_full_read_is_allowed_when_boot_is_preserved(monkeypatch):
    app, w = _gui()
    try:
        w._ecu_variant = "MS41.1"
        w._last_full_read = None
        source = ref("MS41.1")
        w._ds2 = _CodingFamilyDS2(
            source[CODING_FAMILY_FILE_ADDR:CODING_FAMILY_FILE_ADDR + 3]
        )
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
        assert w._ds2.reads == [(gui.MS41ECU.CODING_FAMILY_DS2_ADDR, 3)]
    finally:
        w.close()


@pytest.mark.parametrize("route", ("ds2", "native_ds2", "softbsl"))
def test_boot_preserving_family_graft_reaches_every_full_write_route(
    monkeypatch, route
):
    app, w = _gui()
    try:
        target = bytearray(b"\xFF" * gui.MS41ECU.FULL_ROM_SIZE)
        target[CODING_FAMILY_FILE_ADDR:CODING_FAMILY_FILE_ADDR + 3] = b"909"
        for address in CODING_FAMILY_PROGRAM_ADDRS:
            target[address:address + 3] = b"909"
        for address in CODING_FAMILY_CAL_ADDRS:
            target[address] = ord("9")

        w._ecu_variant = "MS41.1"
        w._ds2 = _CodingFamilyDS2(b"606")
        monkeypatch.setattr(w, "_auto_transfer_route", lambda: route)
        monkeypatch.setattr(
            gui.MS41ECU, "detect_variant", staticmethod(lambda _data: "MS41.1")
        )
        monkeypatch.setattr(
            gui.MS41ECU, "check_hybrid", staticmethod(lambda _data: None)
        )
        monkeypatch.setattr(gui, "verify_checksum", lambda _data: (True, []))
        monkeypatch.setattr(
            gui.softbsl_service, "validate_flash_image_family", lambda *a, **k: None
        )
        monkeypatch.setattr(gui.patch_service, "boot_write_patches_in", lambda _data: [])
        monkeypatch.setattr(w, "_softbsl_missing_after_full_write", lambda *a, **k: ())
        monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: QMessageBox.Yes)
        monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
        monkeypatch.setattr(w, "_finish_flash_success", lambda *a, **k: None)
        monkeypatch.setattr(w, "_disconnect", lambda: None)
        captured = {}

        monkeypatch.setattr(
            w,
            "_ds2_write",
            lambda kind, image, *a, **k: captured.update(kind=kind, image=image),
        )
        monkeypatch.setattr(
            w,
            "_native_fast_write_with_fallback",
            lambda kind, image, family, *a, **k: captured.update(
                kind=kind, image=image, native_kwargs=k
            ),
        )
        monkeypatch.setattr(
            gui.softbsl_service,
            "run_flash",
            lambda port, image, *a, **k: captured.update(image=image, soft_kwargs=k),
        )
        monkeypatch.setattr(
            w,
            "_run_via_softbsl",
            lambda operation, log_fn, progress_fn, **kwargs:
                operation("COM1", progress_fn, log_fn),
        )

        def run_task(task_fn, on_success=None, on_failure=None):
            result = task_fn(lambda *a, **k: None, lambda *a, **k: None)
            if on_success:
                on_success(result)

        monkeypatch.setattr(w, "_run_task", run_task)

        w._ds2_write_full(target, "conversion.bin")

        written = captured["image"]
        for address in CODING_FAMILY_PROGRAM_ADDRS:
            assert written[address:address + 3] == b"606"
        for address in CODING_FAMILY_CAL_ADDRS:
            assert written[address] == ord("6")
        assert w._ds2.reads == [(gui.MS41ECU.CODING_FAMILY_DS2_ADDR, 3)]
        if route == "native_ds2":
            assert captured["native_kwargs"]["variant_conversion"] is False
    finally:
        w._ds2 = None
        w.close()


@pytest.mark.parametrize("route", ("ds2", "native_ds2", "softbsl"))
def test_partial_family_graft_reaches_every_write_route(monkeypatch, route):
    app, w = _gui()
    try:
        target = bytearray(b"\xFF" * gui.MS41ECU.TUNE_SIZE)
        for address in CODING_FAMILY_PARTIAL_ADDRS:
            target[address] = ord("9")

        w._ds2 = _CodingFamilyDS2(b"606")
        monkeypatch.setattr(w, "_auto_transfer_route", lambda: route)
        monkeypatch.setattr(
            gui,
            "correct_checksums",
            lambda data, **_kwargs: (bytearray(data), ["calibration checksum corrected"]),
        )
        captured = {}
        logs = []
        monkeypatch.setattr(
            w,
            "_ds2_write",
            lambda kind, image, *a, **k: captured.update(kind=kind, image=image),
        )
        monkeypatch.setattr(
            w,
            "_native_fast_write_with_fallback",
            lambda kind, image, family, *a, **k: captured.update(
                kind=kind, image=image
            ),
        )
        monkeypatch.setattr(
            gui.softbsl_service,
            "write_tune",
            lambda port, image, *a, **k: captured.update(kind="tune", image=image),
        )
        monkeypatch.setattr(
            w,
            "_run_via_softbsl",
            lambda operation, log_fn, progress_fn, **kwargs:
                operation("COM1", progress_fn, log_fn),
        )

        w._write_tune_auto(
            target,
            lambda *args: logs.append(args),
            lambda *_args: None,
            verify_write=False,
        )

        assert captured["kind"] == "tune"
        assert all(
            captured["image"][address] == ord("6")
            for address in CODING_FAMILY_PARTIAL_ADDRS
        )
        assert w._ds2.reads == [(gui.MS41ECU.CODING_FAMILY_DS2_ADDR, 3)]
        assert any("normalized to live boot family 606" in entry[0] for entry in logs)
    finally:
        w._ds2 = None
        w.close()


def test_boot_overwrite_normalizes_mixed_target_to_its_own_boot_family(monkeypatch):
    app, w = _gui()
    try:
        target = bytearray(b"\xFF" * gui.MS41ECU.FULL_ROM_SIZE)
        target[CODING_FAMILY_FILE_ADDR:CODING_FAMILY_FILE_ADDR + 3] = b"606"
        for address in CODING_FAMILY_PROGRAM_ADDRS:
            target[address:address + 3] = b"909"
        for address in CODING_FAMILY_CAL_ADDRS:
            target[address] = ord("9")

        w._ecu_variant = "MS41.2"
        w._ds2 = _CodingFamilyDS2(b"909")
        w.chk_bootloader_write.setEnabled(True)
        w.chk_bootloader_write.setChecked(True)
        w.chk_boot_preserve_identity.setChecked(False)
        monkeypatch.setattr(w, "_auto_transfer_route", lambda: "softbsl")
        monkeypatch.setattr(
            gui.MS41ECU, "detect_variant", staticmethod(lambda _data: "MS41.2")
        )
        monkeypatch.setattr(
            gui.MS41ECU, "check_hybrid", staticmethod(lambda _data: None)
        )
        monkeypatch.setattr(gui, "verify_checksum", lambda _data: (True, []))
        monkeypatch.setattr(
            gui.softbsl_service, "validate_flash_image_family", lambda *a, **k: None
        )
        monkeypatch.setattr(gui.patch_service, "boot_write_patches_in", lambda _data: [])
        monkeypatch.setattr(w, "_bootloader_write_file_warning", lambda _data: None)
        monkeypatch.setattr(w, "_softbsl_missing_after_full_write", lambda *a, **k: ())
        monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: QMessageBox.Yes)
        monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
        monkeypatch.setattr(
            QInputDialog, "getText", lambda *a, **k: ("WRITE BOOT", True)
        )
        monkeypatch.setattr(w, "_finish_flash_success", lambda *a, **k: None)
        captured = {}
        monkeypatch.setattr(
            gui.softbsl_service,
            "run_flash",
            lambda port, image, *a, **k: captured.update(image=image, kwargs=k),
        )
        monkeypatch.setattr(
            w,
            "_run_via_softbsl",
            lambda operation, log_fn, progress_fn, **kwargs:
                operation("COM1", progress_fn, log_fn),
        )

        def run_task(task_fn, on_success=None, on_failure=None):
            result = task_fn(lambda *a, **k: None, lambda *a, **k: None)
            if on_success:
                on_success(result)

        monkeypatch.setattr(w, "_run_task", run_task)

        w._ds2_write_full(target, "mixed-boot.bin")

        written = captured["image"]
        assert written[CODING_FAMILY_FILE_ADDR:CODING_FAMILY_FILE_ADDR + 3] == b"606"
        for address in CODING_FAMILY_PROGRAM_ADDRS:
            assert written[address:address + 3] == b"606"
        for address in CODING_FAMILY_CAL_ADDRS:
            assert written[address] == ord("6")
        assert captured["kwargs"]["write_bootloader"] is True
        assert w._ds2.reads == []
    finally:
        w._ds2 = None
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
            lambda operation, log_fn, progress_fn, **kwargs:
                operation("COM1", progress_fn, log_fn))

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
        target_bytes = bytes(ref("MS41.3"))
        for start, end in identity.IDENTITY_GRAFT_RANGES:
            assert written[start:end] == source[start:end]
        assert written[:identity.PRODUCTION_OFF] == target_bytes[:identity.PRODUCTION_OFF]
        assert written[identity.PRODUCTION_END:identity.AIF_OFF] == target_bytes[
            identity.PRODUCTION_END:identity.AIF_OFF
        ]
        assert written[0x6001:0x6072] == target_bytes[0x6001:0x6072]
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


def test_ms41_0_boot_conversion_uses_common_supported_confirmation(monkeypatch):
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
            return QMessageBox.Yes

        monkeypatch.setattr(QMessageBox, "warning", warning)
        monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
        monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: QMessageBox.Ok)
        monkeypatch.setattr(
            QInputDialog, "getText",
            lambda *a, **k: ("WRITE BOOT", True),
        )
        monkeypatch.setattr(w, "_run_via_softbsl", lambda *a, **k: None)
        captured = _stub_run_task(monkeypatch, w)

        w._ds2_write_full(bytearray(ref("MS41.3")), "target.bin")

        title, message = next(item for item in warnings if item[0] == "Confirm Variant Conversion")
        assert "MS41.0/MS41.1/MS41.2/MS41.3" in message
        assert "supported" in message
        assert "not yet been validated" not in message
        assert captured.get("ran") is True
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
            lambda operation, log_fn, progress_fn, **kwargs:
                operation("COM1", progress_fn, log_fn))

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
            lambda operation, log_fn, progress_fn, **kwargs:
                operation("COM1", progress_fn, log_fn))

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
        image[CODING_FAMILY_FILE_ADDR:CODING_FAMILY_FILE_ADDR + 3] = b"606"
        for address in CODING_FAMILY_PROGRAM_ADDRS:
            image[address:address + 3] = b"606"
        for address in CODING_FAMILY_CAL_ADDRS:
            image[address] = ord("6")
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
        events = []
        monkeypatch.setattr(
            w, "_show_flash_complete",
            lambda *_args: events.append("ignition-cycle prompt"))
        monkeypatch.setattr(w, "_disconnect", lambda: events.append("disconnect"))

        w._ds2_write_full(
            image, "config-full.bin",
            archived_prewrite_image=bytes(image),
            on_write_success=lambda: events.append("callback"),
            disconnect_after_success=True)

        assert captured["ran"] is True
        assert captured["kind"] == "full"
        assert captured["image"] == bytes(image)
        assert events == ["ignition-cycle prompt", "callback", "disconnect"]
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
        monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.No)

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
        source = bytearray(b"\x5A" * identity.FULL_ROM_SIZE)
        source[identity.MARK_1585_OFF:identity.MARK_1585_OFF + 4] = b"1585"
        source[identity.SERIAL_OFF:identity.SERIAL_NUL_OFF + 1] = b"123456789\x00"
        source[identity.VIN_OFF:identity.VIN_OFF + identity.VIN_LEN] = identity.encode_vin(
            "WBAAA1300H8250001"
        )
        source = bytes(source)
        target = bytes([0xA5]) * identity.FULL_ROM_SIZE
        target_path = tmp_path / "target.bin"
        target_path.write_bytes(target)
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
        for start, end in identity.IDENTITY_GRAFT_RANGES:
            assert grafted[start:end] == source[start:end]
        assert grafted[identity.PRODUCTION_END:identity.AIF_OFF] == target[
            identity.PRODUCTION_END:identity.AIF_OFF
        ]
        assert grafted[0x6001:0x6072] == target[0x6001:0x6072]
        os.remove(out_path)
    finally:
        w.close()


def test_live_identity_source_reads_complete_provenance_ranges():
    source = bytearray(b"\xFF" * 0x6100)
    source[identity.PRODUCTION_OFF:identity.PRODUCTION_END] = bytes(range(26))
    source[identity.MARK_1585_OFF:identity.MARK_1585_OFF + 4] = b"1585"
    source[identity.SERIAL_OFF:identity.SERIAL_NUL_OFF + 1] = b"123456789\x00"
    source[identity.AIF_OFF:identity.AIF_END] = bytes(
        (index * 17 + 3) & 0xFF for index in range(identity.AIF_END - identity.AIF_OFF)
    )
    source[identity.VIN_OFF:identity.VIN_OFF + identity.VIN_LEN] = identity.encode_vin(
        "WBAAA1300H8250001"
    )
    source = bytes(source)
    reads = []

    class Ds2:
        def read_mem(self, address, length):
            reads.append(("read_mem", address, length))
            file_offset = address ^ 0x4000
            return source[file_offset:file_offset + length]

        def read_memory_range(self, address, length):
            reads.append(("read_memory_range", address, length))
            file_offset = address ^ 0x4000
            return source[file_offset:file_offset + length]

    captured = gui.MS41FlashGUI._read_live_identity_source(Ds2(), lambda *_args: None)

    assert captured is not None
    for start, end in identity.IDENTITY_GRAFT_RANGES:
        assert captured[start:end] == source[start:end]
    assert reads == [
        (
            "read_mem",
            identity.PRODUCTION_OFF ^ 0x4000,
            identity.PRODUCTION_END - identity.PRODUCTION_OFF,
        ),
        (
            "read_memory_range",
            identity.AIF_OFF ^ 0x4000,
            identity.AIF_END - identity.AIF_OFF,
        ),
    ]
