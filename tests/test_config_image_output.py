"""Windows ECU Config output uses the prepared image for both save and flash."""

import pytest
from PyQt5.QtWidgets import QFileDialog, QInputDialog, QMessageBox

import ms42_ms43_config_patches as config
import ms42_ms43_image as images
from tests.test_ms42_ms43_config_patches import patch_image
from tests.test_ms42_ms43_desktop import connect, window as window, worker_runner as worker_runner


@pytest.mark.parametrize("family", ("MS41", "MS42", "MS43"))
def test_file_review_and_immediate_export_use_the_saved_bytes(window, monkeypatch, tmp_path, family):
    base = tmp_path / "original.bin"
    source = bytes(24 * 1024) if family == "MS41" else patch_image(family)
    base.write_bytes(source)
    window._load_config_from_path(str(base))
    assert window._config_live_actions.isHidden()
    if family == "MS41":
        window._config_combos["Transmission"].setCurrentText("AT Only")
    else:
        assert window._ms42_ms43_config_detected.isHidden()
        window._ms42_ms43_config_combos["Electrical cooling fan"].setCurrentText("Fitted")

    reviewed = []
    writes = []
    monkeypatch.setattr(window, "_review_edited_bin", lambda image, name, write, *, temporary, flash_reason:
                        reviewed.append((bytes(image), name, temporary, write)))
    monkeypatch.setattr(window, "_write_edited_image",
                        lambda *args, original: writes.append((*args, original)))
    window.btn_config_flash.click()
    image, name, temporary, write = reviewed.pop()
    assert temporary and name == "original_config.bin" and source != image
    assert not window._backup_mgr.entries and list(tmp_path.glob("*.bin")) == [base]
    write(image, name)
    assert writes == [(image, name, family, source)]

    saved, exported = tmp_path / "Track setup.bin", tmp_path / "For laptop.bin"
    paths = iter([saved, exported])
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_args: (str(next(paths)), ""))
    window.btn_config_save.click()
    receipt = window._config_saved_actions
    assert str(saved) in receipt.label.toolTip() and not receipt.isHidden()
    # Editing again must not change what the saved result exports.
    if family == "MS41":
        window._config_combos["Transmission"].setCurrentText("MT Only")
    else:
        window._ms42_ms43_config_combos["Electrical cooling fan"].setCurrentText("Not fitted")
    receipt.export.click()
    receipt.review.click()
    assert reviewed[0][:3] == (image, saved.name, False)
    assert exported.read_bytes() == saved.read_bytes() == image
    assert base.read_bytes() == source and not window._backup_mgr.entries
    window.btn_config_close.click()
    assert receipt.isHidden() and not window._config_live_actions.isHidden()


@pytest.mark.parametrize("keep", (False, True))
def test_live_ms43_tune_copy_and_write_share_current_checksum_context(
        window, worker_runner, monkeypatch, keep):
    ecu = connect(window, "MS43")
    full = patch_image("MS43")
    source = images.extract_calibration(full)
    window._ms42_ms43_config_values(config.read_config(full))
    name = "Electrical cooling fan"
    window._ms42_ms43_config_combos[name].setCurrentText("Fitted")
    assert not window.chk_config_keep.isChecked()
    window.chk_config_keep.setChecked(keep)
    window.chk_correct_cksum.setChecked(True)
    reads, writes = [], []
    ecu.read_tune = lambda **_kw: reads.append("tune") or source
    context = full[0x6FDB8:0x6FDB8 + images.MS43_MONITOR_CONTEXT_SIZE]
    ecu.read_memory = lambda address, size: reads.append((address, size)) or context
    monkeypatch.setattr(QInputDialog, "getText", lambda *_args, **_kw: ("Fan setup", True))
    monkeypatch.setattr(window, "_ms42_ms43_write",
                        lambda image, _name, mode, **kw: writes.append((image, mode, kw)))
    window.btn_config_write_ecu.click()
    target, mode, options = writes[0]
    assert mode == "tune" and config.read_config(target)[name] == "Fitted"
    assert reads == ["tune", (images.MS43_MONITOR_CONTEXT_ADDRESS, len(context))]
    assert options == {"checksum_context": context, "live_config_checksum": True}
    assert images.checksum_status(target, context=context)["cal"]
    if keep:
        entry, = window._backup_mgr.entries
        assert entry.folder == "Configured" and entry.filename.endswith("Fan setup.bin")
        assert window._backup_mgr.read_data(entry.filename, entry.sha256) == target
        assert entry.read_checksum_context(target) == context
        assert not window._config_saved_actions.isHidden()
    else:
        assert not window._backup_mgr.entries


@pytest.mark.parametrize("accept_name", (False, True))
def test_named_ms41_live_copy_precedes_write_and_preserves_original_backup(window, monkeypatch, accept_name):
    from tests.test_gui_smoke import _prepare_live_id12_config, _synthetic_id12_full

    original = _synthetic_id12_full()
    _prepare_live_id12_config(window, original, cache=True)
    window._config_combos["Program CRC Check"].setCurrentText("Enabled")
    window.chk_config_fix.setChecked(False)
    window.chk_config_keep.setChecked(True)
    monkeypatch.setattr(QMessageBox, "warning", lambda *_: QMessageBox.Yes)
    monkeypatch.setattr(QInputDialog, "getText", lambda *_, **_kw: ("Test setup", accept_name))
    writes = []
    monkeypatch.setattr(window, "_ds2_write_full", lambda image, _name, **kw: writes.append((image, kw)))
    window._on_config_write_ecu()
    if not accept_name:
        assert not writes and not window._backup_mgr.entries
        return
    entry, = window._backup_mgr.entries
    image, options = writes[0]
    assert window._backup_mgr.read_data(entry.filename, entry.sha256) == image != original
    assert options["archived_prewrite_image"] == original
    assert window._config_live_baseline["Program CRC Check"] == "Disabled"
    options["on_write_success"]()
    assert window._config_live_baseline["Program CRC Check"] == "Enabled"


@pytest.mark.parametrize("load_from_bins", (False, True))
def test_config_save_in_bins_preserves_existing_entries_and_partial_context(
        window, monkeypatch, tmp_path, load_from_bins):
    full = patch_image("MS43")
    source = images.extract_calibration(full)
    context = full[0x6FDB8:0x6FDB8 + images.MS43_MONITOR_CONTEXT_SIZE]
    library = window._backup_mgr
    existing = library.add_data(source, "Fan setup.bin", folder="Configured", checksum_context=context)
    if load_from_bins:
        window._load_config_from_path(existing.path)
        assert window.btn_config_save.text() == "Save to Bins…"
        monkeypatch.setattr(QInputDialog, "getText", lambda *_args, **_kw: ("Fan setup", True))
        monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_: pytest.fail("Already in Bins"))
    else:
        base = tmp_path / "source.bin"
        base.write_bytes(source)
        window._load_config_from_path(str(base))
        monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_: (existing.path, ""))
        monkeypatch.setattr(QInputDialog, "getText", lambda *_, **_kw: pytest.fail("Name already chosen"))
    window._ms42_ms43_config_combos["Electrical cooling fan"].setCurrentText("Fitted")
    window.btn_config_save.click()
    added, = [entry for entry in library.entries if entry.filename != existing.filename]
    assert added.folder == "Configured" and added.filename in window._config_saved_actions.reveal.toolTip()
    target = library.read_data(added.filename, added.sha256)
    assert added.read_checksum_context(target) == context and config.read_config(target)["Electrical cooling fan"] == "Fitted"
    assert library.read_data(existing.filename, existing.sha256) == source
    assert not window._config_saved_actions.reveal.isHidden()
