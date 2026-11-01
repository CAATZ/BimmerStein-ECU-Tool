"""ECU Data panel resizing with fake connections and isolated settings."""

import pytest
from PyQt5.QtCore import QEvent
from PyQt5.QtWidgets import QApplication, QPushButton, QScrollArea

import gui
from ms42_ms43_diagnostics import RETAINED_PROVENANCE, read_identity
from tests.test_ms42_ms43_desktop import connect, window as window
from tests.test_ms42_ms43_diagnostics import identity_ecu
from tests.test_ms42_ms43_reported_layout import fully_visible, show_tab


def show_snapshot(window, family="MS43", size=(1000, 780)):
    connect(window, family)
    ecu, _ = identity_ecu(family)
    info = read_identity(ecu)
    info.update(retained_address=0x4000, retained_raw=b"\xff" * 0x4000,
                retained_provenance=RETAINED_PROVENANCE)
    window._ms42_ms43_show_data(info)
    return show_tab(window, "ECU Data", size)


@pytest.mark.parametrize("family", ("MS42", "MS43"))
@pytest.mark.parametrize("size", ((980, 740), (1000, 780), (1280, 900)))
def test_data_report_fits_remaining_space_without_scrolling_whole_page(window, family, size):
    page = show_snapshot(window, family, size)
    assert isinstance(page, QScrollArea)
    assert page.verticalScrollBar().maximum() == 0
    assert page.horizontalScrollBar().maximum() == 0
    controls = window._ms42_ms43_data_controls
    assert controls.verticalScrollBar().maximum() == 0
    assert controls.horizontalScrollBar().maximum() == 0
    for control in (window._ms42_ms43_data_read, window._ms42_ms43_storage_read,
                    window._ms42_ms43_storage_write, window._ms42_ms43_storage_note):
        assert fully_visible(control, controls.viewport())
    report = window._ms42_ms43_data_text
    assert fully_visible(report, page.viewport())
    assert report.height() >= 3 * report.fontMetrics().lineSpacing()
    assert "ECU" in report.toPlainText()
    assert window._main_scroll.verticalScrollBar().maximum() == 0


def test_data_divider_persists_and_reset_layout_restores_defaults(window):
    show_snapshot(window, size=(1000, 900))
    panel = window._ms42_ms43_data_splitter
    default_sizes = panel.sizes()
    report_height = window._ms42_ms43_data_text.height()
    panel.moveSplitter(175, 1)
    QApplication.processEvents()
    saved = panel.sizes()
    assert saved != default_sizes
    assert window._ms42_ms43_data_text.height() > report_height
    assert panel._settings().contains("layout/ecu_data")
    controls = window._ms42_ms43_data_controls
    assert controls.verticalScrollBar().maximum() > 0
    for control in (window._ms42_ms43_data_read, window._ms42_ms43_storage_write):
        controls.ensureWidgetVisible(control, 0, 0)
        QApplication.processEvents()
        assert fully_visible(control, controls.viewport())

    other = gui.MS41FlashGUI()
    try:
        show_snapshot(other, size=(1000, 900))
        restored = other._ms42_ms43_data_splitter
        assert restored.sizes() == saved
        reset = next(button for button in other.findChildren(QPushButton)
                     if button.text() == "Reset Layout")
        reset.click()
        QApplication.processEvents()
        assert not restored._settings().contains("layout/ecu_data")
        assert restored.sizes() != saved
        other.resize(980, 740)
        QApplication.processEvents()
        assert all(size > 0 for size in restored.sizes())
        assert other.tabs.currentWidget().verticalScrollBar().maximum() == 0
    finally:
        other._ds2 = None
        other.close()
        other.deleteLater()
        QApplication.sendPostedEvents(other, QEvent.DeferredDelete)


def test_expanding_data_page_gives_space_to_report(window):
    show_snapshot(window, size=(980, 740))
    report = window._ms42_ms43_data_text
    controls = window._ms42_ms43_data_controls
    start_report, start_controls = report.height(), controls.height()
    window.resize(1280, 1000)
    QApplication.processEvents()
    assert report.height() > start_report + 150
    assert controls.height() == start_controls
