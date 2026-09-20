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
def qt_app():
    from PyQt5.QtWidgets import QApplication
    from gui import configure_application
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
    view.append_samples(rows, [(4, 3., ("1100", "Active", "4")),
                               (5, 4., ("1200", "Active", "5"))], dropped=2)
    assert view.times == [2., 3., 4.]
    assert view.series["Engine RPM"]["values"] == [1000., 1100., 1200.]
    assert view.cursor == 2 and view.dropped == 2
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
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self._use_telegram = kwargs["use_telegram"]
        def resolved_rows(self):
            return [("RPM", "RPM")]
        def start(self, log_path):
            captured["log_path"] = log_path
        def stop(self):
            captured["stopped"] = True
        def pop_errors(self):
            return []
        def completed_samples_since(self, after):
            return 1, 0, 0, (("RPM", "RPM"),), (() if after else ((1, .05, ("1234",)),))
    monkeypatch.setattr(gui, "LiveDataPoller", Poller)
    window = gui.MS41FlashGUI()
    window._ds2 = object()
    window._ecu_id = "1437806"
    window._ecu_variant = "MS41.1"
    window.cb_live_mode.setCurrentIndex(window.cb_live_mode.findData(mode))
    window.spin_interval.setValue(0)
    window.chk_live_log.setChecked(False)
    window._on_live_start()
    assert captured["interval"] == 0
    assert captured["use_telegram"] == telegram
    assert captured["telegram_fallback"] == fallback
    assert captured["definition_path"] == window._logger_registry.active_path()
    assert captured["log_path"] is None
    assert not window.btn_logger_import.isEnabled()
    window._refresh_live_display()
    assert window.live_view.sample_text("RPM") == "1234"
    assert "20.0 samples/s" in window.lbl_live_status.text()
    window._on_live_stop()
    assert captured["stopped"] and window.btn_logger_import.isEnabled()
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
    window._on_import_logger_definition()
    assert registry.status().name == "Selected.xml"
    assert window.live_view.definition_path == registry.active_path()
    assert "(imported)" in window.lbl_logger_definition.text()
    window._on_reset_logger_definition()
    assert registry.status().bundled
    assert "(bundled)" in window.lbl_logger_definition.text()
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
