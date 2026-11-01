"""Desktop acquisition preferences; isolated settings and no ECU traffic."""

import os
from pathlib import Path
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from PyQt5.QtCore import QEvent, QSettings
from PyQt5.QtWidgets import QApplication, QPushButton

import gui
import logger_definition_registry
from logger_definition import bundled_logger_definition_path


@pytest.fixture
def desktop(qapp, tmp_path, monkeypatch):
    app = qapp
    old_font = app.font()
    gui.configure_application(app)
    settings = QSettings(str(tmp_path / "preferences.ini"), QSettings.IniFormat)
    registry = logger_definition_registry.LoggerDefinitionRegistry(tmp_path / "definitions")
    monkeypatch.setattr(gui.MS41FlashGUI, "_live_settings", staticmethod(lambda: settings))
    monkeypatch.setattr(gui._PanelSplitter, "_settings", lambda self: settings)
    monkeypatch.setattr(gui.MS41FlashGUI, "_refresh_ports", lambda self: None)
    monkeypatch.setattr(logger_definition_registry, "LoggerDefinitionRegistry", lambda: registry)
    windows = []

    def create(ecu_id="1429861", variant="MS41.0"):
        window = gui.MS41FlashGUI()
        windows.append(window)
        window._ds2 = object()
        window._ecu_id = ecu_id
        window._ecu_variant = variant
        window._refresh_live_channels()
        window._set_live_buttons_enabled(True)
        return window

    yield create, settings, registry
    for window in windows:
        window._ds2 = None
        window.close()
        window.deleteLater()
        QApplication.sendPostedEvents(window, QEvent.DeferredDelete)
    app.processEvents()
    app.setFont(old_font)


def test_favorites_and_maximum_rate_survive_new_window_by_exact_ecu(desktop):
    create, settings, _registry = desktop
    first = create()
    defaults = first.live_view.selected_channels()
    assert len(defaults) == 3
    assert first.spin_interval.value() == 0
    assert first.spin_interval.text() == "Maximum rate"
    first.live_view.set_selected_channels(["Engine RPM", "Cylinder 1 Roughness"])
    first.spin_interval.setValue(200)
    settings.sync()

    restarted = create()
    assert restarted.live_view.selected_channels() == ["Engine RPM", "Cylinder 1 Roughness"]
    assert restarted.spin_interval.value() == 200
    assert settings.value(first._live_channel_context, type=list) == ["P8", "BS_ROUGHNESS_CYL1"]
    different = create("1437806", "MS41.1")
    assert different.live_view.selected_channels() == defaults
    different.live_view.set_selected_channels(["Coolant Temp"])
    different._ecu_id, different._ecu_variant = "1429861", "MS41.0"
    different._refresh_live_channels()
    assert different.live_view.selected_channels() == ["Engine RPM", "Cylinder 1 Roughness"]


def test_live_divider_persists_and_resets_without_changing_channels(desktop):
    create, settings, _registry = desktop

    def show_live(window):
        window.resize(1000, 800)
        window.tabs.setCurrentIndex(next(i for i in range(window.tabs.count())
                                        if window.tabs.tabText(i).strip() == "Live Data"))
        window.show()
        for _ in range(4):
            QApplication.processEvents()
        return window.live_view.findChild(gui._PanelSplitter, "live_data")

    first = create()
    splitter = show_live(first)
    first.live_view.set_selected_channels(["Engine RPM", "Coolant Temp"])
    selection = first.live_view.selected_channels()
    splitter.moveSplitter(350, 1)
    QApplication.processEvents()
    saved = splitter.sizes()
    assert settings.contains("layout/live_data")
    assert saved[0] >= 340

    second = create()
    restored = show_live(second)
    assert abs(restored.sizes()[0] - saved[0]) <= 2
    assert second.live_view.selected_channels() == selection
    reset = next(button for button in second.findChildren(QPushButton)
                 if button.text() == "Reset Layout")
    reset.click()
    QApplication.processEvents()
    assert not settings.contains("layout/live_data")
    assert restored.sizes()[0] < saved[0]
    assert second.live_view.selected_channels() == selection
    second.resize(980, 740)
    QApplication.processEvents()
    assert all(size > 0 for size in restored.sizes())


def test_empty_selection_stays_empty_and_cannot_start(desktop, monkeypatch):
    create, settings, _registry = desktop
    window = create()
    window.live_view.set_selected_channels([])
    assert settings.contains(window._live_channel_context)
    assert not window.btn_live_start.isEnabled()
    restarted = create()
    assert restarted.live_view.selected_channels() == []
    errors = []
    monkeypatch.setattr(gui.QMessageBox, "warning", lambda *args: errors.append(args[-1]))
    monkeypatch.setattr(gui, "LiveDataPoller", lambda **kwargs: pytest.fail("No poller for no channels"))
    restarted._on_live_start()
    assert errors == ["Select at least one measurement channel."]


def test_unavailable_favorites_survive_profile_refresh_and_user_changes(desktop):
    create, settings, _registry = desktop
    window = create()
    full_catalog = window._live_catalog
    window.live_view.set_selected_channels(["Engine RPM", "Cylinder 1 Roughness"])
    saved = settings.value(window._live_channel_context, type=list)
    reduced = tuple(row for row in full_catalog if row[0] != "BS_ROUGHNESS_CYL1")
    window._set_live_catalog(reduced)
    assert window.live_view.selected_channels() == ["Engine RPM"]
    assert settings.value(window._live_channel_context, type=list) == saved
    window.live_view.set_selected_channels(["Coolant Temp"])
    assert "BS_ROUGHNESS_CYL1" in settings.value(window._live_channel_context, type=list)
    window._refresh_live_channels(force=True)
    assert set(window.live_view.selected_channels()) == {"Coolant Temp", "Cylinder 1 Roughness"}


def test_next_capture_retries_retained_favorites_and_none_clears_them(desktop, monkeypatch):
    create, settings, _registry = desktop
    window = create()
    window.live_view.set_selected_channels(["Engine RPM", "Cylinder 1 Roughness"])
    window._set_live_catalog(tuple(row for row in window._live_catalog
                                   if row[0] != "BS_ROUGHNESS_CYL1"))
    captured = {}

    class Poller:
        def __init__(self, **kwargs):
            captured.update(kwargs)
        def start(self, log_path):
            pass

    monkeypatch.setattr(gui, "LiveDataPoller", Poller)
    window.chk_live_log.setChecked(False)
    window._on_live_start()
    window._live_timer.stop()
    assert captured["selected_ids"] == ("P8", "BS_ROUGHNESS_CYL1")
    window._poller = None
    window._update_telegram_checkbox_state()
    window.live_view.selection_buttons[1].click()
    assert settings.value(window._live_channel_context, type=list) == []


def test_disconnect_context_preserves_completed_capture(desktop):
    create, settings, _registry = desktop
    window = create()
    window.live_view.set_selected_channels(["Engine RPM"])
    context = window._live_channel_context
    window.live_view.append_samples([("Engine RPM", "RPM")], [(1, 0., ("900",))])
    window._ds2 = window._ecu_id = window._ecu_variant = None
    window._refresh_live_channels()
    assert window._live_channel_context is None
    assert not window.live_view.channels.isEnabled()
    assert window.live_view.sample_text("Engine RPM") == "900"
    assert settings.value(context, type=list) == ["P8"]


def test_imported_content_and_program_variant_have_distinct_favorites(desktop, tmp_path):
    create, settings, registry = desktop
    window = create("1406464", "MS41.2")
    bundled_context = window._live_channel_context
    window.live_view.set_selected_channels(["Engine RPM"])
    window._ecu_program_variant = "MS41.3"
    window._refresh_live_channels()
    assert window._live_channel_context != bundled_context
    assert len(window.live_view.selected_channels()) == 3

    imported = tmp_path / "custom.xml"
    contents = bundled_logger_definition_path().read_bytes()
    imported.write_bytes(contents)
    registry.import_file(imported)
    window._refresh_logger_definition()
    imported_context = window._live_channel_context
    window.live_view.set_selected_channels(["Coolant Temp"])
    # The registry replaces imports with the same filename; content is the identity.
    imported.write_bytes(contents + b"\n<!-- Updated definition -->\n")
    registry.import_file(imported)
    window._refresh_logger_definition()
    assert window._live_channel_context != imported_context
    assert len(window.live_view.selected_channels()) == 3
    assert settings.value(imported_context, type=list)  # Old favorites remain recoverable.
    registry.reset_to_bundled()
    window._ecu_program_variant = None
    window._refresh_logger_definition()
    assert window._live_channel_context == bundled_context
    assert window.live_view.selected_channels() == ["Engine RPM"]


def test_unknown_ecu_has_no_guessed_channels(desktop):
    create, _settings, _registry = desktop
    window = create("UNKNOWN", "MS41.0")
    assert window.live_view.channels.count() == 0
    assert not window.btn_live_start.isEnabled()


def _choose_presentation(view, name, mode):
    view.set_presentation(name, mode)


def test_instrument_modes_restore_by_id_without_crossing_ecu_or_definition(desktop, tmp_path):
    create, settings, registry = desktop
    window = create()
    _choose_presentation(window.live_view, "Engine RPM", "digital")
    _choose_presentation(window.live_view, "Cylinder 1 Roughness", "box")
    preference_key = window._live_channel_context.replace("live/channels/", "live/presentations/", 1)
    settings.sync()
    assert settings.value(preference_key) == {"P8": "digital", "BS_ROUGHNESS_CYL1": "box"}
    restarted = create()
    assert restarted.live_view.presentation_for("Engine RPM") == "digital"
    assert restarted.live_view.presentation_for("Cylinder 1 Roughness") == "box"
    assert create("1437806", "MS41.1").live_view.presentations == {}

    window._ecu_program_variant = "MS41.1"
    window._refresh_live_channels()
    assert window.live_view.presentations == {}
    window._ecu_program_variant = None
    window._refresh_live_channels()
    assert window.live_view.presentation_for("Engine RPM") == "digital"

    imported = tmp_path / "instruments.xml"
    contents = bundled_logger_definition_path().read_bytes()
    imported.write_bytes(contents)
    registry.import_file(imported)
    window._refresh_logger_definition()
    assert window.live_view.presentations == {}
    _choose_presentation(window.live_view, "Engine RPM", "box")
    imported.write_bytes(contents + b"\n<!-- Updated instruments definition -->\n")
    registry.import_file(imported)
    window._refresh_logger_definition()
    assert window.live_view.presentations == {}
    registry.reset_to_bundled()
    window._refresh_logger_definition()
    assert window.live_view.presentation_for("Engine RPM") == "digital"


def test_instrument_modes_survive_filtered_catalog_and_follow_stable_ids(desktop):
    create, settings, _registry = desktop
    window = create()
    full_catalog = window._live_catalog
    _choose_presentation(window.live_view, "Engine RPM", "digital")
    _choose_presentation(window.live_view, "Cylinder 1 Roughness", "box")
    preference_key = window._live_channel_context.replace("live/channels/", "live/presentations/", 1)
    saved = settings.value(preference_key)
    window._set_live_catalog(tuple(row for row in full_catalog if row[0] != "BS_ROUGHNESS_CYL1"))
    assert window.live_view.presentations == {"Engine RPM": "digital"}
    assert settings.value(preference_key) == saved
    _choose_presentation(window.live_view, "Engine RPM", "gauge")
    assert settings.value(preference_key)["BS_ROUGHNESS_CYL1"] == "box"
    renamed = tuple((key, "Renamed RPM" if key == "P8" else name, unit)
                    for key, name, unit in full_catalog)
    window._set_live_catalog(renamed)
    assert window.live_view.presentations == {"Renamed RPM": "gauge", "Cylinder 1 Roughness": "box"}


def test_instrument_mode_can_be_saved_during_capture(desktop):
    create, settings, _registry = desktop
    window = create()
    selection = window.live_view.selected_channels()
    window._poller = object()
    try:
        window._update_telegram_checkbox_state()
        assert not window.live_view.channels.isEnabled()
        _choose_presentation(window.live_view, "Engine RPM", "digital")
        preference_key = window._live_channel_context.replace("live/channels/", "live/presentations/", 1)
        assert settings.value(preference_key) == {"P8": "digital"}
        assert window.live_view.selected_channels() == selection
    finally:
        window._poller = None
    assert create().live_view.presentation_for("Engine RPM") == "digital"


@pytest.mark.parametrize("stored, expected", [(-9, -1), (-1, -1), (0, 0), (1, 1), (2, 2), (8, 2)])
def test_instrument_size_is_global_persistent_and_clamped(desktop, stored, expected):
    create, settings, _registry = desktop
    settings.setValue("live/instrument_size", stored)
    window = create()
    assert window.live_view.gauges.size_level == expected
    window.live_view.gauges.set_size(1)
    assert settings.value("live/instrument_size", type=int) == 1
    assert create("1437806", "MS41.1").live_view.gauges.size_level == 1
