"""Windows editor handoffs use the normal writers, with no device or selected file."""
from unittest.mock import Mock

import pytest
from PyQt5.QtWidgets import QMessageBox

import ms42_ms43_image as images
from gui import MS41ECU
from tests.test_ms42_ms43_desktop import connect, window, worker_runner
from tests.test_ms42_ms43_image import synthetic_image
from tests.test_ms42_ms43_vin_desktop import connected


def test_ms41_patch_and_tune_deliver_the_edits_and_write_options(window, monkeypatch):
    window._ds2 = object()
    window.btn_write_full.setEnabled(True)
    window.btn_write_tune.setEnabled(True)
    full, tune = Mock(), Mock()
    monkeypatch.setattr(window, "_ds2_write_full", full)
    monkeypatch.setattr(window, "_ds2_write_tune", tune)
    image = b"P" * MS41ECU.FULL_ROM_SIZE
    window._write_edited_image(image, "patched.bin", "MS41", require_boot_write=True,
                               disconnect_after_success=True, write_notes="Patch changes")
    full.assert_called_once_with(image, "patched.bin", require_boot_write=True,
                                 disconnect_after_success=True, write_notes="Patch changes")
    partial = b"T" * MS41ECU.TUNE_SIZE
    window._write_edited_image(partial, "configured.bin", "MS41")
    tune.assert_called_once_with(partial, "configured.bin")


def test_edited_image_checks_current_family_and_flash_availability(window, monkeypatch):
    flash = Mock()
    monkeypatch.setattr(window, "_ms42_ms43_write", flash)
    info = Mock()
    monkeypatch.setattr(QMessageBox, "information", info)
    image = synthetic_image("MS43")
    window._write_edited_image(image, "first.bin", "MS43")
    assert "Connect to the MS43 ECU" in info.call_args.args[2]
    connect(window, "MS42")
    window._write_edited_image(image, "first.bin", "MS43")
    assert "cannot be flashed to a MS42 ECU" in info.call_args.args[2]
    connect(window, "MS43")
    window.btn_write_full.setEnabled(False)
    window._write_edited_image(image, "first.bin", "MS43")
    assert info.call_args.args[1] == "Flash Unavailable"
    flash.assert_not_called()
    window.btn_write_full.setEnabled(True)
    window._write_edited_image(image, "retry.bin", "MS43")
    flash.assert_called_once_with(image, "retry.bin", "full")


@pytest.mark.parametrize("family", ["MS42", "MS43"])
def test_flash_delivery_uses_the_edited_bytes_and_current_write_regions(window, family):
    from ms42_ms43_identity import IDENTITY_GRAFT_RANGES

    connected(window, family)
    profile = images.PROFILES[family]
    original = bytes(synthetic_image(family))

    def reason(offset, source=original):
        image = bytearray(source)
        image[offset] ^= 1
        return window._edited_image_flash_reason(image, family, original=source)

    partial = images.extract_calibration(original)
    assert reason(profile.program_write_regions[0][1]) is None
    assert reason(profile.calibration_offset) is None
    assert reason(0, partial) is None
    assert "writable calibration" in reason(profile.calibration_write_length, partial)
    assert "boot region" in reason(0x100)
    window.chk_bootloader_write.setChecked(True)
    assert reason(0x100) is None
    assert "retained or reserved" in reason(0x6000)
    assert "retained or reserved" in reason(profile.calibration_offset + profile.calibration_write_length)
    identity_offset = IDENTITY_GRAFT_RANGES[family][0][0]
    window.chk_boot_preserve_identity.setChecked(True)
    assert "ECU identity" in reason(identity_offset)
    window.chk_boot_preserve_identity.setChecked(False)
    assert reason(identity_offset) is None


def test_ms43_prepared_tune_context_follows_only_its_confirmed_write(
        window, worker_runner, monkeypatch):
    ecu = connect(window, "MS43")
    ecu.write_tune = Mock()
    image = images.extract_calibration(synthetic_image("MS43"))
    context = bytes(range(images.MS43_MONITOR_CONTEXT_SIZE))
    window.chk_backup_before_write.setChecked(False)
    monkeypatch.setattr(window, "_prewrite_battery_notice", lambda: "Test voltage")
    monkeypatch.setattr(window, "_show_flash_complete", lambda *_: None)
    review = Mock(side_effect=[QMessageBox.No, QMessageBox.Yes])
    monkeypatch.setattr(window, "_confirm_edited_write", review)
    window._ms42_ms43_write(image, "cancelled.bin", "tune", checksum_context=context[::-1],
                            live_config_checksum=True)
    ecu.write_tune.assert_not_called()
    window._ms42_ms43_write(image, "configured.bin", "tune", checksum_context=context,
                            live_config_checksum=True)
    title, text, export, name = review.call_args.args
    assert title == "Write MS43" and "configured.bin" in text and name == "MS43_config_tune.bin"
    assert export == image
    assert ecu.write_tune.call_count == 1
    assert ecu.write_tune.call_args.args == (image,)
    assert ecu.write_tune.call_args.kwargs["checksum_context"] == context
