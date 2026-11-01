"""Offline coverage of shared CSV parsing and the desktop live/log controls."""

import os
from pathlib import Path
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from live_log import parse_log, sample_value


def test_csv_preserves_gaps_states_and_known_units():
    result = parse_log(b"Time,Datetime,Engine RPM,Closed Throttle,Custom\n"
                       b"0,now,850,Active,ERR\n1,later,,Inactive,NaN\n")
    assert result["times"] == [0., 1.]
    rpm, state, custom = result["series"]
    assert rpm["unit"] == "RPM"
    assert rpm["values"] == [850., None]
    assert state["values"] == [1., 0.]
    assert state["labels"] == ["Active", "Inactive"]
    assert custom["unit"] == "" and custom["values"] == [None, None]
    assert custom["labels"] == ["ERR", "NaN"]
    assert result["display_specs"]["Engine RPM"]["kind"] == "dial"
    assert "Custom" not in result["display_specs"]
    assert sample_value(None) == (None, None)


def test_csv_roughness_preserves_signed_values_zero_and_missing_samples(qt_app):
    from live_data_view import LiveDataView

    result = parse_log(
        b"Time,Datetime,Cylinder 1 Roughness,Cylinder 2 Roughness\n"
        b"0,now,-1,12\n1,later,0,-20\n2,last,,3\n")
    assert result["series"][0]["values"] == [-1., 0., None]
    assert result["series"][1]["values"] == [12., -20., 3.]
    view = LiveDataView()
    try:
        rows = [("Cylinder 1 Roughness", ""), ("Cylinder 2 Roughness", "")]
        view.clear(rows)
        view.append_samples(rows, [(1, 0., ("-1", "12")),
                                  (2, 1., ("0", "-20")),
                                  (3, 2., (None, "3"))])
        view.slider.setValue(0)
        assert view.sample_text("Cylinder 1 Roughness") == "-1"
        view.slider.setValue(1)
        assert view.sample_text("Cylinder 1 Roughness") == "0"
        assert view.sample_text("Cylinder 2 Roughness") == "-20"
        view.slider.setValue(2)
        assert view.sample_text("Cylinder 1 Roughness") == "—"
    finally:
        view.close()


@pytest.mark.parametrize("payload, error", [
    (b"Time,A,A\n0,1,2\n", "duplicate"),
    (b"Time,A\n1,1\n0,2\n", "backward"),
    (b"Time,A\nnan,1\n", "invalid Time"),
    (b"Time,A\n0,1,2\n", "column count"),
    (b"Time,A\n", "no samples"),
    (b"Time,A\n0,\xff\n", "UTF-8"),
    pytest.param(b"Time," + b"A" * 131_073 + b"\n0,1\n", "Invalid Live Data CSV",
                 id="oversized-header"),
])
def test_csv_rejects_malformed_inputs(payload, error):
    with pytest.raises(ValueError, match=error):
        parse_log(payload)


@pytest.fixture
def qt_app(monkeypatch, tmp_path):
    from PyQt5.QtCore import QSettings
    from PyQt5.QtWidgets import QApplication
    from gui import MS41FlashGUI, configure_application
    settings = QSettings(str(tmp_path / "live.ini"), QSettings.IniFormat)
    monkeypatch.setattr(MS41FlashGUI, "_live_settings", staticmethod(lambda: settings))
    app = QApplication.instance() or QApplication([])
    configure_application(app)
    return app


def test_plot_reduction_keeps_spikes_and_missing_sample_breaks():
    from live_data_view import plot_segments, nearest_sample
    values = [0.] * 100
    values[17] = 900.
    values[50] = None
    segments = list(plot_segments(list(range(100)), values, 0, 100, 8))
    assert len(segments) == 2
    assert (17, 900.) in segments[0]
    assert segments[0][-1][0] == 49 and segments[1][0][0] == 51
    assert nearest_sample([0, 1, 3], 2.7) == 2


def test_view_cursor_zoom_states_gauges_and_bounded_history(qt_app, monkeypatch):
    from PyQt5.QtCore import Qt
    import live_data_view
    view = live_data_view.LiveDataView()
    rows = [("Engine RPM", "RPM"), ("Closed Throttle", ""), ("Custom", "")]
    view.clear(rows)
    view.append_samples(rows, [(1, 0., ("800", "Active", "Unknown")),
                               (2, 1., ("900", "Inactive", None)),
                               (3, 2., ("1000", "Active", "3"))])
    assert view.cursor == 2 and view.sample_text("Engine RPM") == "1000"
    assert view.time_range() == (0., 30.)
    view.slider.setValue(0)
    assert not view.follow.isChecked() and view.sample_text("Custom") == "Unknown"
    view.fit()
    view.zoom(.5, 1.)
    assert view.time_range() == (.5, 1.5)
    view.set_time_range(-10, -9)
    assert view.time_range() == (0., 1.)
    view.channels.item(0).setCheckState(Qt.Unchecked)
    assert view.selected_channels() == ["Closed Throttle", "Custom"]
    view.channels.item(0).setCheckState(Qt.Checked)
    for width in (700, 1100):
        view.resize(width, 580)
        view.show()
        for index in (0, 1, 2):
            view.tabs.setCurrentIndex(index)
            qt_app.processEvents()
            assert not view.grab().isNull()
    assert view.font().family() == "Segoe UI"
    assert view.table.item(0, 1).font().family() == "Consolas"
    monkeypatch.setattr(live_data_view, "MAX_LIVE_SAMPLES", 3)
    view.follow.setChecked(True)
    assert view.time_range() == (0., 30.)
    view.append_samples(rows, [(4, 3., ("1100", "Active", "4")),
                               (5, 4., ("1200", "Active", "5"))], dropped=2)
    assert view.times == [2., 3., 4.]
    assert view.series["Engine RPM"]["values"] == [1000., 1100., 1200.]
    assert view.cursor == 2 and view.dropped == 2
    view.append_samples(rows, [(6, 31., ("1200", "Active", "5"))])
    assert view.time_range() == (1., 31.)
    view.close()


def test_channel_selection_filters_values_and_preserves_subset_history(qt_app, monkeypatch):
    from PyQt5.QtTest import QSignalSpy
    from live_data_view import LiveDataView

    view = LiveDataView()
    catalog = [("A", ""), ("B", ""), ("C", "")]
    try:
        view.clear(catalog)
        changed = QSignalSpy(view.selection_changed)
        view.set_selected_channels(["B"])
        assert len(changed) == 1 and view.selected_channels() == ["B"]
        assert [view.table.isRowHidden(index) for index in range(3)] == [True, False, True]
        view.append_samples([("B", "")], [(1, 0., ("12",)), (2, 1., ("15",))])
        assert list(view.series) == ["A", "B", "C"]
        assert view.channels.count() == 3 and view.selected_channels() == ["B"]
        assert view.series["B"]["values"] == [12., 15.]
        assert view.series["A"]["values"] == [None, None]

        # A profile/catalog refresh retains captured values; new channels get gaps.
        view.set_channels(catalog + [("D", "")])
        assert view.times == [0., 1.] and view.series["B"]["values"] == [12., 15.]
        assert view.series["D"]["values"] == [None, None]
        view.append_samples([("A", "")], [(3, 2., ("9",))])
        assert view.series["B"]["values"] == [12., 15., None]
        assert view.series["A"]["values"] == [None, None, 9.]
        assert view.selected_channels() == ["B"]

        displayed = []
        original = view.sample_text
        monkeypatch.setattr(view, "sample_text", lambda name: displayed.append(name) or original(name))
        view._refresh()
        assert displayed == ["B"]  # Hidden Values rows do no formatting work.
        view.clear(catalog)
        assert not view.times and all(not item["values"] for item in view.series.values())
        assert view.selected_channels() == ["B"]
    finally:
        view.close()


def test_channel_selection_locks_user_controls_but_allows_gui_restore(qt_app):
    from PyQt5.QtCore import Qt
    from PyQt5.QtTest import QTest
    from live_data_view import LiveDataView

    view = LiveDataView()
    try:
        view.clear([("A", ""), ("B", "")])
        view.set_selected_channels(["A"])
        view.show()
        view.channels.setCurrentRow(0)
        view.set_selection_enabled(False)
        assert not view.channels.isEnabled()
        assert all(not button.isEnabled() for button in view.selection_buttons)
        assert view.search.isEnabled()
        QTest.keyClick(view.channels, Qt.Key_Space)
        for button in view.selection_buttons:
            button.click()
        assert view.selected_channels() == ["A"]
        view.set_selected_channels(["B"])
        assert view.selected_channels() == ["B"]
        view.set_selection_enabled(True)
        view.selection_buttons[0].click()
        assert view.selected_channels() == ["A", "B"]
        view.selection_buttons[1].click()
        assert not view.selected_channels()
        assert all(view.table.isRowHidden(index) for index in range(2))
        view.clear()
        assert not view.selected_channels()  # Explicit None survives a new capture.
    finally:
        view.close()


def test_saved_log_selection_is_display_only_and_independent(qt_app):
    from live_data_view import LiveDataView

    live = LiveDataView()
    saved = LiveDataView(live=False)
    try:
        live.clear([("A", ""), ("B", "")])
        live.set_selected_channels(["B"])
        saved.load(parse_log(b"Time,A,B\n0,1,2\n1,3,4\n"))
        saved.set_selected_channels(["A"])
        assert live.selected_channels() == ["B"]
        assert saved.series["B"]["values"] == [2., 4.]
        assert saved.table.isRowHidden(saved.table_rows["B"])
        assert saved.time_range() == (0., 1.)
        assert saved.channels.accessibleName() == "Channels to display"
        assert live.channels.accessibleName() == "Channels to read, log and display"
    finally:
        live.close()
        saved.close()


def test_instrument_modes_are_individual_and_do_not_change_samples(qt_app):
    from PyQt5.QtTest import QSignalSpy
    from live_data_view import LiveDataView

    view = LiveDataView()
    try:
        rows = [("Engine RPM", "RPM"), ("Cylinder 1 Roughness", "raw"),
                ("Fuel Trim ST", "%"), ("Closed Throttle", "")]
        view.clear(rows)
        view.set_selected_channels([name for name, _unit in rows])
        assert view.presentation_for("Engine RPM") == "gauge"
        assert view.presentation_for("Cylinder 1 Roughness") == "box"
        assert view.presentation_options("Closed Throttle") == ("digital",)
        changed = QSignalSpy(view.presentation_changed)
        view.set_presentation("Engine RPM", "digital")
        view.set_presentation("Fuel Trim ST", "box")
        view.set_presentation("Closed Throttle", "gauge")
        assert len(changed) == 2
        view.append_samples(rows, [(1, 0., ("800", "-1", "2.5", "Active"))])
        view.set_presentation("Engine RPM", "box")
        assert view.times == [0.]
        assert view.series["Engine RPM"]["values"] == [800.]
        assert view.selected_channels() == [name for name, _unit in rows]
        view.clear(rows)
        assert view.presentation_for("Engine RPM") == "box"
        view.set_presentations({"Engine RPM": "digital"})
        assert len(changed) == 3  # Restoring does not save or trigger acquisition.
    finally:
        view.close()


def test_instrument_distributions_use_rolling_history_through_cursor(qt_app, monkeypatch):
    import live_data_view
    view = live_data_view.LiveDataView()
    rows = [("Cylinder 1 Roughness", "raw"), ("Cylinder 2 Roughness", "raw")]
    try:
        view.clear(rows)
        view.append_samples(rows, [(i + 1, float(i), (str(i - 20), str(2 * i)))
                                   for i in range(41)])
        calls = []
        original = live_data_view.box_summary
        monkeypatch.setattr(live_data_view, "box_summary",
                            lambda values: calls.append(1) or original(values))
        a, b = (view.card_snapshot(name) for name, _unit in rows)
        assert a["summary"].count == 31 and a["summary"].median == 5
        assert a["bounds"] == b["bounds"] == (-88., 88.)
        assert a["current"] == 20
        view.card_snapshot(rows[0][0])
        assert len(calls) == 2  # Repaints do not sort the same data again.
        view.slider.setValue(5)
        past = view.card_snapshot(rows[0][0])
        assert past["summary"].count == 6 and past["summary"].median == -17.5
        assert past["max"] == -15 and past["current"] == -15
        assert past["bounds"] == (-22., 22.)  # No future extremes leak into history.
        assert "History" in view.position.text()
    finally:
        view.close()


def test_instrument_minmax_survives_trimming_and_resets_without_erasing_history(qt_app, monkeypatch):
    import live_data_view
    view = live_data_view.LiveDataView()
    rows = [("Engine RPM", "RPM")]
    try:
        monkeypatch.setattr(live_data_view, "MAX_LIVE_SAMPLES", 3)
        view.clear(rows)
        view.append_samples(rows, [(1, 0., ("500",)), (2, 1., ("1000",)),
                                   (3, 2., ("800",)), (4, 3., ("900",))])
        card = view.card_snapshot("Engine RPM")
        assert (card["min"], card["max"]) == (500., 1000.)
        assert not card["gauge_auto"]
        assert card["gauge_bounds"] == (view.specs["Engine RPM"]["minimum"],
                                        view.specs["Engine RPM"]["maximum"])
        view.reset_extrema()
        assert view.card_snapshot("Engine RPM")["min"] == 900.
        assert view.times == [1., 2., 3.]
        view.append_samples(rows, [(5, 4., ("950",))])
        assert view.card_snapshot("Engine RPM")["max"] == 950.
        view.slider.setValue(0)
        assert view.card_snapshot("Engine RPM")["min"] is None  # Before reset.
        assert not view.card_buttons[-1].isEnabled()
    finally:
        view.close()


def test_numeric_csv_card_options_and_contextual_size_controls(qt_app):
    from live_data_view import LiveDataView
    view = LiveDataView(live=False)
    try:
        view.load(parse_log(b"Time,Custom,State\n0,-2,Active\n1,3,Inactive\n"))
        assert view.presentation_options("Custom") == ("gauge", "digital", "box")
        assert view.presentation_options("State") == ("digital",)
        view.show()
        view.tabs.setCurrentIndex(1)
        assert all(not button.isHidden() for button in view.time_buttons)
        assert all(button.isHidden() for button in view.card_buttons)
        view.tabs.setCurrentIndex(2)
        assert all(button.isHidden() for button in view.time_buttons)
        assert all(not button.isHidden() for button in view.card_buttons)
        before = view.time_range()
        view.gauges.set_size(1)
        view.card_buttons[0].click()
        assert view.time_range() == before and view.gauges.size_level == 0
        assert view.card_buttons[0].isEnabled()
        view.card_buttons[0].click()
        assert view.gauges.size_level == -1 and not view.card_buttons[0].isEnabled()
        view.card_buttons[1].click()
        assert view.gauges.size_level == 0 and view.time_range() == before
    finally:
        view.close()


@pytest.mark.parametrize("tab_index", [1, 2])
def test_plot_and_gauges_process_only_visible_rows_when_scrolled(qt_app, monkeypatch, tab_index):
    from live_data_view import LiveDataView

    view = LiveDataView()
    rows = [(f"Channel {index + 1}", "") for index in range(77)]
    try:
        view.resize(1100, 700)
        view.clear(rows)
        view.set_selected_channels([name for name, _unit in rows])
        view.append_samples(rows, [(1, 0., tuple(str(index - 1) for index in range(77)))])
        painted = set()
        original = view.sample_text
        monkeypatch.setattr(view, "sample_text", lambda name: painted.add(name) or original(name))
        view.tabs.setCurrentIndex(tab_index)
        view.show()
        qt_app.processEvents()
        assert "Channel 1" in painted and "Channel 77" not in painted
        assert 0 < len(painted) < 20
        painted.clear()
        scroll = view.tabs.widget(tab_index).verticalScrollBar()
        scroll.setValue(scroll.maximum())
        qt_app.processEvents()
        assert "Channel 77" in painted and "Channel 1" not in painted
        assert 0 < len(painted) < 20
        assert len(view.series) == 77  # Culling never discards captured channels.
        painted.clear()
        assert not view.tabs.widget(tab_index).widget().grab().isNull()
        assert painted == {name for name, _unit in rows}  # Full-widget exports still paint all.
    finally:
        view.close()


@pytest.mark.parametrize("mode, telegram, fallback", [
    ("auto", True, True), ("telegram", True, False), ("ds2", False, False),
])
def test_gui_routes_maximum_rate_modes_and_graphs_without_csv(
        qt_app, monkeypatch, tmp_path, mode, telegram, fallback):
    import gui
    import logger_definition_registry
    registry = logger_definition_registry.LoggerDefinitionRegistry(tmp_path / "definitions")
    monkeypatch.setattr(logger_definition_registry, "LoggerDefinitionRegistry", lambda: registry)
    monkeypatch.setattr(gui.MS41FlashGUI, "_refresh_ports", lambda self: None)
    monkeypatch.setattr(gui, "LOG_DIR", str(tmp_path))
    captured = {}
    class Poller:
        csv_rows = 0
        sample_rate = 20.
        terminal_error = None
        profile_ready = True
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.acquisition_mode = "telegram" if kwargs["use_telegram"] else "standard"
        def available_channels(self):
            return (("P8", "Engine RPM", "RPM"), ("P12", "Mass Air Flow", "kg/h"))
        def start(self, log_path):
            captured["log_path"] = log_path
        def stop(self):
            captured["stopped"] = True
        def pop_errors(self):
            return []
        def completed_samples_since(self, after):
            return 1, 0, 0, (("Engine RPM", "RPM"),), (() if after else ((1, .05, ("1234",)),))
    monkeypatch.setattr(gui, "LiveDataPoller", Poller)
    window = gui.MS41FlashGUI()
    window._ds2 = object()
    window._ecu_id = "1437806"
    window._ecu_variant = "MS41.1"
    window._refresh_live_channels()
    window.live_view.set_selected_channels(["Engine RPM"])
    window.cb_live_mode.setCurrentIndex(window.cb_live_mode.findData(mode))
    window.spin_interval.setValue(0)
    window.chk_live_log.setChecked(False)
    window._on_live_start()
    assert captured["interval"] == 0
    assert captured["use_telegram"] == telegram
    assert captured["telegram_fallback"] == fallback
    assert captured["selected_ids"] == ("P8",)
    assert captured["definition_path"] == window._logger_registry.active_path()
    assert captured["log_path"] is None
    assert window.lbl_live_status is window.live_view.note
    assert not window.btn_logger_import.isEnabled()
    assert not window.btn_logger_reset.isEnabled()
    assert window.btn_logger_definition.isEnabled()  # Current definition remains inspectable.
    assert not window.live_view.channels.isEnabled()
    window._refresh_live_display()
    assert window.live_view.sample_text("Engine RPM") == "1234"
    assert window.live_view.channels.count() == 2
    assert window.live_view.selected_channels() == ["Engine RPM"]
    assert "20.0 samples/s" in window.lbl_live_status.text()
    window._on_live_stop()
    assert captured["stopped"] and window.btn_logger_import.isEnabled()
    assert window.live_view.channels.isEnabled()
    assert window.live_view.times == [.05]
    window._ds2 = None
    window.close()


def test_gui_imports_and_resets_persistent_logger_definition(qt_app, tmp_path, monkeypatch):
    import gui
    import logger_definition_registry
    from PyQt5.QtWidgets import QFileDialog
    from logger_definition import bundled_logger_definition_path
    registry = logger_definition_registry.LoggerDefinitionRegistry(tmp_path / "definitions")
    monkeypatch.setattr(logger_definition_registry, "LoggerDefinitionRegistry", lambda: registry)
    monkeypatch.setattr(gui.MS41FlashGUI, "_refresh_ports", lambda self: None)
    selected = tmp_path / "Selected.xml"
    selected.write_bytes(bundled_logger_definition_path().read_bytes())
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: (str(selected), ""))
    window = gui.MS41FlashGUI()
    window.btn_logger_import.trigger()
    assert registry.status().name == "Selected.xml"
    assert window.live_view.definition_path == registry.active_path()
    assert window.btn_logger_definition.text() == "Logger: Imported"
    assert "Selected.xml (imported)" in window.act_logger_definition.text()
    assert str(registry.active_path()) in window.btn_logger_definition.toolTip()
    window.btn_logger_reset.trigger()
    assert registry.status().bundled
    assert window.btn_logger_definition.text() == "Logger: Bundled"
    assert "(bundled)" in window.act_logger_definition.toolTip()
    window.close()


def test_live_footer_elides_without_losing_status_or_history_help(qt_app):
    from PyQt5.QtCore import Qt
    from live_data_view import LiveDataView
    view = LiveDataView()
    view.resize(700, 500)
    view.show()
    qt_app.processEvents()
    height = view.note.height()
    text = "Stopped — Error: could not write\n" + "long_capture_filename_" * 20 + ".csv"
    view.note.setText(text)
    qt_app.processEvents()
    assert view.note.text() == text
    assert view.note.height() == height
    assert not view.note.wordWrap()
    assert view.note.fontMetrics().elidedText(text, Qt.ElideRight, view.note.width()).startswith(
        "Stopped — Error:")
    assert text in view.note.toolTip()
    assert "5,000 samples" in view.note.toolTip()
    assert view.note.accessibleDescription() == text
    assert view.note.width() < view.note.fontMetrics().horizontalAdvance(text)
    view.close()


def test_gui_logger_menu_preserves_unavailable_prefix(qt_app, tmp_path, monkeypatch):
    import gui
    import logger_definition_registry
    registry = logger_definition_registry.LoggerDefinitionRegistry(
        tmp_path / "definitions", tmp_path / "missing.xml")
    monkeypatch.setattr(logger_definition_registry, "LoggerDefinitionRegistry", lambda: registry)
    monkeypatch.setattr(gui.MS41FlashGUI, "_refresh_ports", lambda self: None)
    window = gui.MS41FlashGUI()
    assert window.btn_logger_definition.text() == "Logger: Unavailable"
    assert window.act_logger_definition.text().startswith("Unavailable:")
    assert "missing" in window.btn_logger_definition.toolTip()
    assert not window.btn_live_start.isEnabled()
    assert window.btn_logger_import.isEnabled()
    window.close()


def test_saved_viewer_opens_with_cursor_and_rejects_bad_file(qt_app, tmp_path, monkeypatch):
    from PyQt5.QtWidgets import QMessageBox
    from live_data_view import LiveDataView, show_log
    path = tmp_path / "sample.csv"
    path.write_text("Time,RPM\n0,800\n1,900\n", encoding="utf-8")
    dialog = show_log(None, path)
    assert dialog is not None
    viewer = dialog.findChild(LiveDataView)
    assert not viewer.live and viewer.sample_text("RPM") == "800"
    viewer.slider.setValue(1)
    assert viewer.sample_text("RPM") == "900"
    dialog.close()
    errors = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: errors.append(args[-1]))
    path.write_text("bad file", encoding="utf-8")
    assert show_log(None, path) is None
    assert errors and "Time column" in errors[0]
