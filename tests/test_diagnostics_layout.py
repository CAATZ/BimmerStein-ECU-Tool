"""Diagnostics layout and real worker lifecycle; no ECU connection is opened."""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
pytest.importorskip("PyQt5")
from PyQt5.QtCore import QPoint, QRect, QSettings
from PyQt5.QtGui import QCloseEvent, QFontDatabase, QFontInfo
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QMessageBox, QScrollArea

import gui
from ds2 import DS2Commands


def _configure_test_application(app):
    # Windows' offscreen platform can expose no fonts until they are registered.
    # Load installed fonts for the probe; production DPI/theme policy is unchanged.
    if sys.platform == "win32":
        fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
        for name in ("segoeui.ttf", "segoeuib.ttf", "cour.ttf", "courbd.ttf"):
            assert QFontDatabase.addApplicationFont(str(fonts / name)) >= 0
    gui.configure_application(app)
    if sys.platform == "win32":
        assert QFontInfo(app.font()).family() == "Segoe UI"


def _pump_until(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        QTest.qWait(5)
    assert predicate(), "GUI worker did not reach the expected state"


class _FaultMemoryDS2:
    fail_shadow = False

    def read_dtc(self, selector):
        if selector == 0:
            return bytes.fromhex("00 00 00 AC 00")
        assert selector == 1
        return bytes.fromhex("01 08 B4 03 04 17 0B 30 05 AB 57")

    def read_shadow_dtc(self):
        if self.fail_shadow:
            raise OSError("simulated shadow read failure")
        return bytes.fromhex("01 08 78 A5 02 09 10 20 30 40 AA 0C")


def test_fault_memory_selection_shows_freeze_frame_and_clears_stale_detail(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    _configure_test_application(app)
    settings = QSettings(str(tmp_path / "layout.ini"), QSettings.IniFormat)
    monkeypatch.setattr(gui._PanelSplitter, "_settings", lambda self: settings)
    window = gui.MS41FlashGUI()
    try:
        window._ds2 = _FaultMemoryDS2()
        window._ecu_variant = "MS41.0"
        window._on_read_dtc()
        _pump_until(lambda: not window._task_busy)
        for memory, rpm, frequency in (("stored", 736, 3), ("shadow", 512, 2)):
            window.cb_dtc_memory.setCurrentIndex(window.cb_dtc_memory.findData(memory))
            assert window.dtc_table.currentRow() == 0
            assert window.dtc_table.selectedItems()
            record = window._dtcs[0]
            assert record.memory == memory and record.code == 8
            assert record.frequency == frequency
            assert record.freeze_frame[0].value == rpm
            detail = window.dtc_detail.toPlainText()
            assert "Freeze Frame" in detail
            assert f"{rpm} rpm" in detail
            assert memory.title() in detail
            assert record.raw_record.hex(" ").upper() in detail
            assert detail.index("Freeze Frame") < detail.index("Conditions")
        window.dtc_table.clearSelection()
        assert not window.dtc_detail.toPlainText()
        window._fault_memories["shadow"] = []
        window._show_dtc_memory()
        assert window.dtc_table.rowCount() == 0
        assert not window.dtc_detail.toPlainText()
    finally:
        if window._worker is not None:
            window._worker.wait(3000)
        app.processEvents()
        window._ds2 = None
        window.close()


def test_fault_read_failure_replaces_reading_status_and_old_details(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    _configure_test_application(app)
    settings = QSettings(str(tmp_path / "layout.ini"), QSettings.IniFormat)
    monkeypatch.setattr(gui._PanelSplitter, "_settings", lambda self: settings)
    monkeypatch.setattr(QMessageBox, "critical", lambda *_args: None)
    window = gui.MS41FlashGUI()
    try:
        connection = _FaultMemoryDS2()
        connection.fail_shadow = True
        window._ds2 = connection
        window._ecu_variant = "MS41.0"
        window.dtc_detail.setPlainText("Old fault details")
        window._on_read_dtc()
        _pump_until(lambda: not window._task_busy)
        assert "fail" in window.lbl_dtc_count.text().lower()
        assert "Reading" not in window.lbl_dtc_count.text()
        assert not window._fault_memories
        assert window.dtc_table.rowCount() == 0
        assert not window.dtc_detail.toPlainText()
        assert not window.cb_dtc_memory.isEnabled()
    finally:
        if window._worker is not None:
            window._worker.wait(3000)
        app.processEvents()
        window._ds2 = None
        window.close()


def test_actuator_worker_holds_busy_until_stop_cleanup_returns(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    _configure_test_application(app)
    settings = QSettings(str(tmp_path / "layout.ini"), QSettings.IniFormat)
    monkeypatch.setattr(gui._PanelSplitter, "_settings", lambda self: settings)
    dialogs = []
    for name in ("warning", "critical"):
        monkeypatch.setattr(QMessageBox, name, lambda *args: dialogs.append(args))
    accepted, cleaning, release_cleanup = (threading.Event() for _ in range(3))
    calls = []
    gui_thread = threading.get_ident()

    class FakeDS2:
        def execute(self, command, args=b""):
            calls.append((command, args, threading.get_ident()))
            if command == DS2Commands.TELEGRAM:
                return b"\x00\x00"
            if command == DS2Commands.ACTUATOR_TEST:
                accepted.set()
                return args[:1] + b"\x00"
            assert command == DS2Commands.DIAGNOSE_END
            cleaning.set()
            assert release_cleanup.wait(3), "test did not release cleanup"
            return b""

    window = gui.MS41FlashGUI()
    try:
        window._ds2 = FakeDS2()
        window._ecu_variant = "MS41.0"
        window._refresh_actuator_tests()
        window.actuator_buttons["injector_1"].click()
        _pump_until(lambda: accepted.is_set() and window.btn_stop_actuator.isEnabled())
        assert isinstance(window._worker, gui.WorkerThread)
        assert window._task_busy
        assert not any(button.isEnabled() for button in window.actuator_buttons.values())
        window.actuator_buttons["injector_2"].click()
        window.btn_stop_actuator.click()
        _pump_until(cleaning.is_set)
        assert window._task_busy
        assert not window.btn_connect.isEnabled()
        assert not any(button.isEnabled() for button in window.actuator_buttons.values())
        release_cleanup.set()
        _pump_until(lambda: not window._task_busy)
        assert all(button.isEnabled() for button in window.actuator_buttons.values())
        assert not window.btn_stop_actuator.isEnabled()
        assert window._actuator_stop_event is None
        assert not dialogs
        assert [(command, args) for command, args, _ in calls] == [
            (DS2Commands.TELEGRAM, b"\x03"),
            (DS2Commands.TELEGRAM, b"\x03"),
            (DS2Commands.ACTUATOR_TEST, b"\x06\x05\x0A"),
            (DS2Commands.DIAGNOSE_END, b""),
        ]
        assert all(thread != gui_thread for _, _, thread in calls)
    finally:
        release_cleanup.set()
        worker = getattr(window, "_worker", None)
        if worker is not None:
            worker.wait(3000)
        app.processEvents()
        window._ds2 = None
        window.close()


def test_failed_cleanup_requires_retry_and_keeps_close_pending(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    _configure_test_application(app)
    settings = QSettings(str(tmp_path / "layout.ini"), QSettings.IniFormat)
    monkeypatch.setattr(gui._PanelSplitter, "_settings", lambda self: settings)
    accepted, retrying, release_retry, closed = (threading.Event() for _ in range(4))
    cleanups = []

    class FakeDS2:
        def execute(self, command, args=b""):
            if command == DS2Commands.TELEGRAM:
                return b"\x00\x00"
            if command == DS2Commands.ACTUATOR_TEST:
                accepted.set()
                return args[:1] + b"\x00"
            assert command == DS2Commands.DIAGNOSE_END
            cleanups.append(threading.get_ident())
            if len(cleanups) == 1:
                raise OSError("simulated cleanup reply timeout")
            retrying.set()
            assert release_retry.wait(3)
            return b""

        def close(self):
            closed.set()

    window = gui.MS41FlashGUI()
    try:
        window._ds2 = FakeDS2()
        window._ecu_variant = "MS41.0"
        window._refresh_actuator_tests()
        window.show()
        window.actuator_buttons["injector_1"].click()
        _pump_until(accepted.is_set)
        window.btn_stop_actuator.click()
        _pump_until(lambda: not window._task_busy)
        assert window._actuator_cleanup_pending
        assert window.btn_stop_actuator.text() == "Retry Stop"
        assert window.btn_stop_actuator.isEnabled()
        assert window.btn_connect.isEnabled()
        assert not any(button.isEnabled() for button in window.actuator_buttons.values())
        assert window._run_task(lambda *_args: None) is False
        previous_worker = window._worker

        def forbidden(*_args, **_kwargs):
            pytest.fail("pending actuator cleanup must reject another hardware task")

        window._run_eeprom_task(forbidden, forbidden)
        assert not window._eeprom_task_busy
        monkeypatch.setattr(gui, "LiveDataPoller", forbidden)
        monkeypatch.setattr(gui, "RoughnessPoller", forbidden)
        window._on_live_start()
        window._on_roughness_start()
        window._on_run_actuator("injector_2")
        assert window._worker is previous_worker
        assert window._actuator_stop_event is None
        window.btn_stop_actuator.click()
        _pump_until(retrying.is_set)
        assert window._task_busy
        assert window._disconnect() is False
        event = QCloseEvent()
        window.closeEvent(event)
        assert not event.isAccepted()
        assert window._actuator_close_pending
        assert window.isVisible() and not closed.is_set()
        release_retry.set()
        _pump_until(closed.is_set)
        assert not window._task_busy
        assert not window._actuator_cleanup_pending
        assert window._ds2 is None
        assert len(cleanups) == 2
        assert all(thread != threading.get_ident() for thread in cleanups)
    finally:
        release_retry.set()
        worker = getattr(window, "_worker", None)
        if worker is not None:
            worker.wait(3000)
        app.processEvents()
        window._actuator_close_pending = False
        window._actuator_cleanup_pending = False
        window._ds2 = None
        window.close()


@pytest.mark.parametrize("answer", [QMessageBox.No, QMessageBox.Yes])
@pytest.mark.parametrize("action", ["close", "disconnect"])
def test_unconfirmed_actuator_stop_requires_explicit_abandonment(
        tmp_path, monkeypatch, answer, action):
    app = QApplication.instance() or QApplication([])
    _configure_test_application(app)
    settings = QSettings(str(tmp_path / "layout.ini"), QSettings.IniFormat)
    monkeypatch.setattr(gui._PanelSplitter, "_settings", lambda self: settings)
    prompts, messages, closed = [], [], []

    def confirm(_parent, title, text, buttons, default):
        prompts.append((title, text, buttons, default))
        return answer

    monkeypatch.setattr(QMessageBox, "warning", confirm)

    class FakeDS2:
        def execute(self, *_args, **_kwargs):
            pytest.fail("abandonment must not retry or report acknowledged ECU cleanup")

        def close(self):
            closed.append(True)

    connection = FakeDS2()
    window = gui.MS41FlashGUI()
    try:
        monkeypatch.setattr(window, "_log", lambda message, level="info": messages.append((message, level)))
        window._ds2 = connection
        window._ecu_variant = "MS41.0"
        window._refresh_actuator_tests()
        window._actuator_cleanup_pending = True
        window._set_all_buttons_enabled(True)
        window.show()
        assert window.btn_connect.isEnabled()
        assert window.btn_stop_actuator.isEnabled()
        assert not any(button.isEnabled() for button in window.actuator_buttons.values())
        result = window.close() if action == "close" else window._disconnect()
        confirmed = answer == QMessageBox.Yes
        assert result is confirmed
        assert len(prompts) == 1
        title, text, buttons, default = prompts[0]
        assert title == "Actuator Stop Unconfirmed"
        assert "ignition OFF" in text
        assert buttons == QMessageBox.Yes | QMessageBox.No
        assert default == QMessageBox.No
        assert closed == ([True] if confirmed else [])
        assert window._actuator_cleanup_pending is not confirmed
        assert window._ds2 is (None if confirmed else connection)
        if confirmed:
            assert any("stop remains unconfirmed" in message and level == "warn"
                       for message, level in messages)
            assert not any("session ended" in message for message, _ in messages)
        else:
            assert window.isVisible()
            assert window.btn_stop_actuator.isEnabled()
    finally:
        window._actuator_cleanup_pending = False
        window._ds2 = None
        window.close()


def test_pending_actuator_cleanup_disables_computed_ecu_controls(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    _configure_test_application(app)
    settings = QSettings(str(tmp_path / "layout.ini"), QSettings.IniFormat)
    monkeypatch.setattr(gui._PanelSplitter, "_settings", lambda self: settings)
    window = gui.MS41FlashGUI()
    try:
        window._ds2 = object()
        window._connection_port = "test-only"
        window._connection_echo = True
        window._ecu_variant = "MS41.2"
        window._last_ident_raw = bytes(gui.ds2_fast_read.IDENTITY_LENGTH)
        window._ecu_softbsl_marker = "B"
        window._ecu_softbsl_hook_present = True
        window.cb_coding_chassis.setCurrentText("E39")
        window.cb_coding_module.setCurrentIndex(window.cb_coding_module.findData("e39_gm3"))
        controls = (window.btn_eeprom_read, window.btn_config_read_ecu,
                    window.btn_read_module_coding, window.btn_transmission_swap_check)

        def refresh_controls():
            window._update_eeprom_buttons()
            window._update_config_buttons()
            window._update_coding_actions()
            window._update_transmission_swap_actions()

        refresh_controls()
        assert all(control.isEnabled() for control in controls), [
            (control.text(), control.isEnabled()) for control in controls]
        window._actuator_cleanup_pending = True
        refresh_controls()
        assert not any(control.isEnabled() for control in controls)
    finally:
        window._actuator_cleanup_pending = False
        window._ds2 = None
        window.close()


def _assert_symmetric_actuator_groups(window, viewport):
    groups = [[], []]
    for test_id, button in window.actuator_buttons.items():
        rect = QRect(button.mapTo(viewport, QPoint()), button.size())
        assert button.isVisible() and viewport.rect().contains(rect)
        groups[0 if test_id.startswith("injector_") else 1].append(rect)
    assert [len(group) for group in groups] == [6, 6]
    for group in groups:
        for row in (group[:3], group[3:]):
            assert len({rect.top() for rect in row}) == 1
            assert row[0].right() < row[1].left() < row[1].right() < row[2].left()
        assert group[0].bottom() < group[3].top()
    assert max(rect.bottom() for rect in groups[0]) < min(rect.top() for rect in groups[1])
    for column in range(3):
        cells = [group[row * 3 + column] for group in groups for row in range(2)]
        assert max(rect.left() for rect in cells) - min(rect.left() for rect in cells) <= 1
        assert max(rect.right() for rect in cells) - min(rect.right() for rect in cells) <= 1
    cells = groups[0] + groups[1]
    assert max(rect.width() for rect in cells) - min(rect.width() for rect in cells) <= 1
    assert max(rect.height() for rect in cells) - min(rect.height() for rect in cells) <= 1


def _probe_layout(settings_path):
    app = QApplication([])
    _configure_test_application(app)
    settings = QSettings(str(settings_path), QSettings.IniFormat)
    gui._PanelSplitter._settings = lambda self: settings
    window = gui.MS41FlashGUI()
    result = []
    try:
        window._ds2 = object()
        window._ecu_variant = "MS41.2"  # Longest supported actuator names.
        window._refresh_actuator_tests()
        memory = gui.read_ms41_fault_memory(_FaultMemoryDS2(), "MS41.2")
        window._fault_memories = {"stored": list(memory.stored), "shadow": list(memory.shadow)}
        window._show_dtc_memory()
        window.show()
        diag_index = next(i for i in range(window.tabs.count())
                          if window.tabs.tabText(i).strip() == "Diagnostics")
        flash_index = next(i for i in range(window.tabs.count())
                           if window.tabs.tabText(i).strip() == "Flash")
        live_index = next(i for i in range(window.tabs.count())
                          if window.tabs.tabText(i).strip() == "Live Data")
        assert app.style().objectName().lower() == "fusion"
        assert app.font().family() == "Segoe UI"
        assert app.font().pointSizeF() == 9
        assert window.roughness_modes.currentIndex() == 0
        assert window.roughness_modes.tabText(0) == "Box plots"
        assert window.roughness_modes.tabText(1) == "Trend"
        assert window.roughness_view.mode == "box"
        for width, height in ((980, 740), (1280, 800), (1600, 1000)):
            window.resize(width, height)
            window.tabs.setCurrentIndex(flash_index)
            QTest.qWait(20)
            flash_rect = window.tabs.widget(flash_index).geometry()
            log_height = window.log_view.height()
            window.tabs.setCurrentIndex(diag_index)
            for view in range(3):
                window.diag_tabs.setCurrentIndex(view)
                QTest.qWait(20)
                page = window.tabs.widget(diag_index)
                assert not isinstance(page, QScrollArea)
                scrolls = (window._main_scroll, window._actuator_scroll) if view == 1 else (window._main_scroll,)
                for scroll in scrolls:
                    assert scroll.horizontalScrollBar().maximum() == 0, (width, height, view, "horizontal")
                    assert scroll.verticalScrollBar().maximum() == 0, (width, height, view, "vertical")
                controls = ([window.btn_scan_modules, window.cb_diag_module,
                             window.btn_read_dtc, window.btn_clear_dtc,
                             window.btn_export_dtc, window.cb_dtc_memory,
                             window.dtc_table, window.dtc_detail] if view == 0
                            else list(window.actuator_buttons.values()) if view == 1
                            else [window.btn_roughness_start, window.btn_roughness_stop,
                                  window.lbl_roughness_status, window.roughness_modes,
                                  window.roughness_view])
                for control in controls:
                    assert control.isVisible()
                    rect = QRect(control.mapTo(page, QPoint()), control.size())
                    assert page.rect().contains(rect), (width, height, view, control.objectName(), rect)
                if view == 0:
                    assert window.dtc_table.height() >= 120
                    assert window.dtc_table.currentRow() == 0
                    assert "Freeze Frame" in window.dtc_detail.toPlainText()
                    assert "736 rpm" in window.dtc_detail.toPlainText()
                    assert window.dtc_detail.horizontalScrollBar().maximum() == 0
                elif view == 1:
                    assert set(window.actuator_buttons) == set(dict(
                        gui.actuator_tests.available_actuator_tests("MS41.2")))
                    for button in window.actuator_buttons.values():
                        metrics = button.fontMetrics()
                        assert button.height() >= metrics.lineSpacing() + 12
                        assert button.width() >= metrics.horizontalAdvance(button.text()) + 20
                    for state, message in (
                        ("active", "Check-engine lamp (MIL) test active; stopping after 5 seconds or when Stop is pressed."),
                        ("warning", "Check-engine lamp (MIL) test session ended. ECU did not confirm this test (result 0x04). Check whether the output responded."),
                        ("retry", "Stop not confirmed: ECU output stop was not confirmed: simulated reply timeout. Retry Stop before another ECU operation."),
                    ):
                        window._task_busy = state == "active"
                        window._actuator_stop_event = threading.Event() if state == "active" else None
                        window._active_actuator_id = "malfunction_indicator_lamp" if state == "active" else None
                        window._actuator_cleanup_pending = state == "retry"
                        window._set_actuator_buttons_enabled()
                        window.lbl_actuator_result.setText(message)
                        QTest.qWait(20)
                        assert window.btn_stop_actuator.text() == ("Retry Stop" if state == "retry" else "Stop Test")
                        assert window.btn_stop_actuator.isEnabled() == (state != "warning")
                        for scroll in scrolls:
                            assert scroll.horizontalScrollBar().maximum() == 0, (width, height, state, "horizontal")
                            assert scroll.verticalScrollBar().maximum() == 0, (width, height, state, "vertical")
                        rectangles = []
                        for control in [*window.actuator_buttons.values(),
                                        window.lbl_actuator_result, window.btn_stop_actuator]:
                            rect = QRect(control.mapTo(page, QPoint()), control.size())
                            assert page.rect().contains(rect), (width, height, state, rect)
                            assert all(not rect.intersects(other) for other in rectangles), (state, rect)
                            rectangles.append(rect)
                        label = window.lbl_actuator_result
                        assert label.height() >= label.heightForWidth(label.width()), (width, height, state)
                    window._task_busy = False
                    window._actuator_cleanup_pending = False
                    window._set_actuator_buttons_enabled()
                    window._ecu_variant = "MS41.0"
                    window._refresh_actuator_tests()
                    QTest.qWait(20)
                    _assert_symmetric_actuator_groups(window, page)
                    for scroll in scrolls:
                        assert scroll.horizontalScrollBar().maximum() == 0, (width, height, "MS41.0", "horizontal")
                        assert scroll.verticalScrollBar().maximum() == 0, (width, height, "MS41.0", "vertical")
                    result.append({"size": [width, height], "view": "actuators_ms410",
                                   "device_pixel_ratio": window.devicePixelRatioF()})
                    window._ecu_variant = "MS41.2"
                    window._refresh_actuator_tests()
                else:
                    roughness = window.roughness_view
                    roughness.reset()
                    for sample in range(6):
                        roughness.add_sample(sample, tuple(
                            (cylinder + 1) * (sample + 1) * 3 for cylinder in range(6)))
                    roughness.add_sample(6, (32768,) * 6)
                    mode_geometry = []
                    for mode, mode_name in enumerate(("box", "trend")):
                        window.roughness_modes.setCurrentIndex(mode)
                        QTest.qWait(20)
                        assert roughness.mode == mode_name
                        for scroll in scrolls:
                            assert scroll.horizontalScrollBar().maximum() == 0, (width, height, "roughness", mode)
                            assert scroll.verticalScrollBar().maximum() == 0, (width, height, "roughness", mode)
                        chart = roughness.chart
                        chart_rect = QRect(chart.mapTo(page, QPoint()), chart.size())
                        assert chart.isVisible() and page.rect().contains(chart_rect)
                        assert chart.height() >= 144
                        labels = roughness.value_labels
                        assert len(labels) == 6
                        if mode_name == "trend":
                            rects = []
                            for label in labels:
                                rect = QRect(label.mapTo(page, QPoint()), label.size())
                                assert label.isVisible() and page.rect().contains(rect)
                                assert rect.top() > chart_rect.bottom()
                                assert label.width() >= label.fontMetrics().horizontalAdvance(label.text())
                                assert label.height() >= label.fontMetrics().height()
                                rects.append(rect)
                            assert len({rect.top() for rect in rects}) == 1
                            assert len({rect.bottom() for rect in rects}) == 1
                            assert max(rect.width() for rect in rects) - min(rect.width() for rect in rects) <= 1
                            assert all(left.right() < right.left() for left, right in zip(rects, rects[1:]))
                        else:
                            assert all(not label.isVisible() for label in labels)
                            assert len(roughness.box_summaries) == 6
                            assert all(summary.count == 7 and summary.outliers == (32768,)
                                       for summary in roughness.box_summaries)
                            # Six cylinder rows plus the header and horizontal axis.
                            assert chart.height() >= chart.fontMetrics().lineSpacing() * 8 + 24
                            legend = roughness.footer.currentWidget()
                            legend_rect = QRect(legend.mapTo(page, QPoint()), legend.size())
                            assert legend.isVisible() and page.rect().contains(legend_rect)
                            assert legend.width() >= legend.sizeHint().width()
                            assert legend.height() >= legend.sizeHint().height()
                        mode_geometry.append(chart_rect)
                        result.append({"size": [width, height], "view": f"roughness_{mode_name}",
                                       "device_pixel_ratio": window.devicePixelRatioF()})
                    assert mode_geometry[0] == mode_geometry[1]
                    continue
                result.append({"size": [width, height], "view": view,
                               "device_pixel_ratio": window.devicePixelRatioF()})
            window.tabs.setCurrentIndex(live_index)
            QTest.qWait(20)
            page = window.tabs.widget(live_index)
            assert isinstance(page, QScrollArea)
            assert not page.isAncestorOf(window.roughness_view)
            assert not page.isAncestorOf(window.btn_roughness_start)
            for scroll in (window._main_scroll, page):
                assert scroll.horizontalScrollBar().maximum() == 0, (width, height, "live", "horizontal")
                assert scroll.verticalScrollBar().maximum() == 0, (width, height, "live", "vertical")
            for control in (window.btn_logger_definition, window.btn_live_open_log,
                            window.spin_interval, window.cb_live_mode, window.chk_live_log,
                            window.btn_live_start, window.btn_live_stop,
                            window.lbl_live_status, window.live_view):
                rect = QRect(control.mapTo(page.viewport(), QPoint()), control.size())
                assert control.isVisible() and page.viewport().rect().contains(rect), (width, height, "live", rect)
            assert window.lbl_live_status is window.live_view.note
            assert not window.lbl_live_status.wordWrap()
            assert window.btn_logger_definition.geometry().top() == window.btn_live_open_log.geometry().top()
            assert abs(window.btn_logger_definition.geometry().center().y()
                       - window.btn_live_start.geometry().center().y()) <= 1
            toolbar = page.widget().layout().itemAt(0).layout()
            assert window.btn_live_stop.geometry().right() == toolbar.geometry().right()
            group_gap = window.fontMetrics().height()
            assert (window.chk_live_log.geometry().left()
                    - window.cb_live_mode.geometry().right()) >= group_gap
            assert (window.btn_live_start.geometry().left()
                    - window.chk_live_log.geometry().right()) >= group_gap
            result.append({"size": [width, height], "view": "live",
                           "device_pixel_ratio": window.devicePixelRatioF()})
            window.tabs.setCurrentIndex(flash_index)
            QTest.qWait(20)
            assert window.tabs.widget(flash_index).geometry() == flash_rect
            assert window.log_view.height() == log_height
        return result
    finally:
        window._task_busy = False
        window._actuator_stop_event = None
        window._actuator_cleanup_pending = False
        window._ds2 = None
        window.close()


@pytest.mark.parametrize("scale", ["1", "1.25", "1.5", "2"])
def test_diagnostics_fit_each_independent_qt_scale(tmp_path, scale):
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", QT_SCALE_FACTOR=scale,
               QT_AUTO_SCREEN_SCALE_FACTOR="0", QT_FONT_DPI="96")
    child = subprocess.run(
        [sys.executable, str(Path(__file__)), "--probe", str(tmp_path / "layout.ini")],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=45)
    assert child.returncode == 0, child.stdout + child.stderr
    samples = json.loads(child.stdout)
    assert len(samples) == 18
    assert all(sample["device_pixel_ratio"] == pytest.approx(float(scale))
               for sample in samples)


if __name__ == "__main__":
    assert len(sys.argv) == 3 and sys.argv[1] == "--probe"
    print(json.dumps(_probe_layout(Path(sys.argv[2]))))
