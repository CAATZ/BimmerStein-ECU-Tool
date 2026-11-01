"""Both066 programs use the existing config routes without a new context probe."""

import pytest

import ms42_ms43_config_patches as config
from ms42_ms43_image import extract_calibration
from tests.test_ms42_ms43_desktop import window as window, worker_runner as worker_runner
from tests.test_ms42_ms43_revision_desktop import prepare
from tests.test_ms42_ms43_revision_patches import reference

NAME = "Fuel tank level plausibility diagnosis"
REVISIONS = ("430065", "430066", "430066-later")


@pytest.mark.parametrize("revision", REVISIONS)
def test_live_read_needs_no_extra_program_probe(window, worker_runner, monkeypatch, revision):
    ecu = prepare(window, revision[:6])
    source, reads = reference(revision), []
    def read(address, size):
        reads.append((address, size))
        offset = address - 0x80000 if address >= 0x80000 else address
        return source[offset:offset + size]
    ecu.read_memory = read
    monkeypatch.setattr("ms42_ms43_diagnostics.read_learned_config", lambda *_: {})
    window._ms42_ms43_config_read()
    combo = window._ms42_ms43_config_combos[NAME]
    assert combo.isEnabled() and combo.currentText() == config.read_config(source)[NAME]
    assert (0xC0D50, 18) not in reads


@pytest.mark.parametrize("revision", REVISIONS)
@pytest.mark.parametrize("mode", ["tune", "full"])
def test_tank_edit_reuses_existing_tune_and_program_routes(window, worker_runner, monkeypatch, revision, mode):
    ecu = prepare(window, revision[:6])
    source = reference(revision)
    original = config.read_config(source)[NAME]
    window._ms42_ms43_config_values({NAME: original, "Immobilizer bypass": "Disabled"})
    selected = "Disabled" if original == "Enabled" else "Enabled"
    window._ms42_ms43_config_combos[NAME].setCurrentText(selected)
    if mode == "full":
        window._ms42_ms43_config_combos["Immobilizer bypass"].setCurrentText("Enabled")
    ecu.read_memory = lambda *a: pytest.fail("No added program-context query")
    def read_full(**kwargs):
        assert mode == "full"
        return source
    def read_tune(**kwargs):
        assert mode == "tune"
        return extract_calibration(source)
    ecu.read_full, ecu.read_tune = read_full, read_tune
    writes = []
    monkeypatch.setattr(window, "_ms42_ms43_write", lambda *args: writes.append(args))
    window._ms42_ms43_config_write()
    assert len(writes) == 1 and writes[0][2] == mode
    assert config.read_config(writes[0][0])[NAME] == selected
    if mode == "tune":
        changed = [i for i, (a, b) in enumerate(zip(extract_calibration(source), writes[0][0])) if a != b]
        assert changed == [0xE04 if revision == "430065" else 0xE05]
