"""Config options use the existing full writer only when program bytes change."""

import pytest
from PyQt5.QtWidgets import QFileDialog

import ms42_ms43_config_patches as config
from ms42_ms43_image import extract_calibration
from tests.test_ms42_ms43_config_patches import patch_image
from tests.test_ms42_ms43_desktop import connect, window as window, worker_runner as worker_runner


@pytest.mark.parametrize("family", ("MS42", "MS43"))
@pytest.mark.parametrize("disabled", (False, True))
@pytest.mark.parametrize("name", ("Closed-loop lambda feedback", "Octane adaptation learning"))
def test_calibration_only_live_edit_stays_on_existing_tune_writer(
        window, worker_runner, monkeypatch, family, disabled, name):
    ecu = connect(window, family)
    normal = ("Above 75 °C" if name == "Octane adaptation learning" else
              "320 rpm threshold" if family == "MS42" else "480 rpm threshold")
    before, after = (normal, "Disabled") if disabled else ("Disabled", normal)
    source = config.apply_config(patch_image(family), {name: before})
    window._ms42_ms43_config_values(config.read_config(source))
    window._ms42_ms43_config_combos[name].setCurrentText(after)
    ecu.read_full = lambda **kw: pytest.fail("A calibration threshold change must not read the program")
    ecu.read_tune = lambda **kw: extract_calibration(source)
    writes = []
    monkeypatch.setattr(window, "_ms42_ms43_write", lambda *args: writes.append(args))
    window._on_config_write_ecu()
    assert len(writes) == 1 and writes[0][2] == "tune"
    assert config.read_config(writes[0][0])[name] == after


@pytest.mark.parametrize("family", ("MS42", "MS43"))
@pytest.mark.parametrize("changed", (False, True))
def test_live_patch_edit_chooses_full_writer_only_for_changed_program_option(
        window, worker_runner, monkeypatch, family, changed):
    ecu = connect(window, family)
    source = patch_image(family)
    window._ms42_ms43_config_values(config.read_config(source))
    name = "Immobilizer bypass" if changed else "Electrical cooling fan"
    window._ms42_ms43_config_combos[name].setCurrentText("Enabled" if changed else "Fitted")
    reads, writes = [], []
    ecu.read_full = lambda **kw: reads.append("full") or source
    ecu.read_tune = lambda **kw: reads.append("tune") or extract_calibration(source)
    monkeypatch.setattr(window, "_ms42_ms43_write", lambda *args: writes.append(args))
    window._on_config_write_ecu()
    expected = "full" if changed else "tune"
    assert reads == [expected] and len(writes) == 1 and writes[0][2] == expected
    assert config.read_config(writes[0][0])[name] == ("Enabled" if changed else "Fitted")


@pytest.mark.parametrize("family", ("MS42", "MS43"))
def test_imported_patch_selection_does_not_replace_live_baseline(
        window, worker_runner, monkeypatch, tmp_path, family):
    ecu = connect(window, family)
    source = patch_image(family)
    window._ms42_ms43_config_values(config.read_config(source))
    target = config.apply_config(source, {"Calibration checksum bypass": "Enabled"})
    path = tmp_path / "desired.bin"
    path.write_bytes(target)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: (str(path), ""))
    window._ms42_ms43_config_get_file()
    assert window._ms42_ms43_config_original_values["Calibration checksum bypass"] == "Disabled"
    ecu.read_full = lambda **kw: source
    ecu.read_tune = lambda **kw: pytest.fail("Program option must use full read")
    writes = []
    monkeypatch.setattr(window, "_ms42_ms43_write", lambda *args: writes.append(args))
    window._on_config_write_ecu()
    assert writes[0][2] == "full" and writes[0][0] == target


@pytest.mark.parametrize("family", ("MS42", "MS43"))
def test_full_file_patch_save_uses_existing_checksum_option_and_preserves_input(
        window, monkeypatch, tmp_path, family):
    from ms42_ms43_image import checksum_status

    source = patch_image(family)
    path, output = tmp_path / "original.bin", tmp_path / "edited.bin"
    path.write_bytes(source)
    window._load_config_from_path(str(path))
    window._ms42_ms43_config_combos["Immobilizer bypass"].setCurrentText("Enabled")
    window._ms42_ms43_config_combos["Calibration checksum bypass"].setCurrentText("Enabled")
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: (str(output), ""))
    window._on_config_save()
    data = output.read_bytes()
    values = config.read_config(data)
    assert values["Immobilizer bypass"] == values["Calibration checksum bypass"] == "Enabled"
    status = checksum_status(data, family)
    assert status["cal"] and status["program"] and status["boot"]
    assert path.read_bytes() == source


@pytest.mark.parametrize("family", ("MS42", "MS43"))
def test_partial_file_and_unrecognized_program_do_not_enable_patch_dropdown(window, tmp_path, family):
    source = bytearray(patch_image(family))
    offset = config.patches_for(family, "0110C6" if family == "MS42" else "430069")[0].entries[0][0]
    source[offset] ^= 0x55
    for name, data in (("custom.bin", source), ("tune.bin", extract_calibration(source))):
        path = tmp_path / name
        path.write_bytes(data)
        window._load_config_from_path(str(path))
        assert not window._ms42_ms43_config_combos["Immobilizer bypass"].isEnabled()
