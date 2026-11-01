"""Reported desktop regressions, using snapshots/fake connections only."""

from types import SimpleNamespace

import pytest
from PyQt5.QtCore import QPoint
from PyQt5.QtWidgets import QApplication, QScrollArea

from tests.test_ms42_ms43_desktop import connect, window as window
from tests.test_ms42_ms43_diagnostics import identity_ecu
from ms42_ms43_diagnostics import read_identity


def show_tab(window, title, size):
    index = next(i for i in range(window.tabs.count()) if window.tabs.tabText(i).strip() == title)
    window.tabs.setCurrentIndex(index)
    window.resize(*size)
    window.show()
    QApplication.processEvents()
    return window.tabs.currentWidget()


def fully_visible(widget, ancestor):
    top_left = widget.mapTo(ancestor, QPoint())
    rect = widget.rect().translated(top_left)
    return ancestor.rect().contains(rect) and widget.visibleRegion().boundingRect() == widget.rect()


@pytest.mark.parametrize("size", ((980, 740), (1000, 800), (1280, 900)))
@pytest.mark.parametrize("family", ("MS42", "MS43"))
def test_diagnostics_headers_stay_visible_and_stop_stays_outside_scrolling_grid(window, family, size):
    connect(window, family)
    page = show_tab(window, "Diagnostics", size)
    assert not isinstance(page, QScrollArea)
    for subtab in (1, 2, 0, 1):
        window.diag_tabs.setCurrentIndex(subtab)
        QApplication.processEvents()
        assert fully_visible(window.diag_tabs.tabBar(), page)
        if subtab == 1:
            assert fully_visible(window.btn_stop_actuator, page)
            assert window._actuator_scroll.horizontalScrollBar().maximum() == 0
            window._actuator_scroll.verticalScrollBar().setValue(
                window._actuator_scroll.verticalScrollBar().maximum())
            QApplication.processEvents()
            assert fully_visible(window.btn_stop_actuator, page)
        elif subtab == 2:
            assert fully_visible(window.btn_roughness_start, page)
            assert fully_visible(window.roughness_modes, page)
            assert fully_visible(window.roughness_view, page)
    assert window._main_scroll.verticalScrollBar().maximum() == 0


@pytest.mark.parametrize("family", ("MS42", "MS43"))
@pytest.mark.parametrize("size", ((980, 740), (1000, 780)))
def test_info_single_record_is_compact_and_full_history_still_exports(window, family, size):
    connect(window, family)
    ecu, _ = identity_ecu(family)
    info = read_identity(ecu)
    info["aif_records"] = info["aif_records"][:1]
    info["programming_count"] = 1
    info["daten_lineage"] = "Type 7503718; program 7551615 A; calibration 7552624DA"
    info["supply_voltages"] = {"battery": 2.539, **({"ignition": 12.695} if family == "MS43" else {})}
    window._ms42_ms43_show_info(info)
    show_tab(window, "ECU Info", size)
    label = window._info_labels["Current AIF Record"]
    assert label.text() == "1"
    assert "VIN:" in label.toolTip()
    assert window._info_field_names["First AIF Record"].isHidden()
    assert window._info_scroll.verticalScrollBar().maximum() == 0
    assert fully_visible(window.btn_show_technical_info, window._info_scroll.viewport())
    lineage = window._info_labels["BMW DATEN Lineage"]
    authority = window._info_field_names["Recording Authority Number"]
    grid = lineage.parentWidget().layout()
    assert lineage.y() - authority.geometry().bottom() == grid.verticalSpacing() + 1
    assert lineage.height() == window._info_labels["Current AIF Record"].height()
    assert lineage.text() == info["daten_lineage"]
    # All sections retain the same two label/value columns; the long lineage spans both.
    left_values = ("ECU Family", "Current AIF Record", "VIN", "Battery Voltage", "BMW DATEN Lineage")
    right_values = ("Reported ECU Identifier", "Recorded ZB / ZUSB", "DME Production Serial")
    for fields in (left_values, right_values):
        assert len({window._info_labels[field].x() for field in fields}) == 1
    assert window._info_labels["Battery Voltage"].text() == "2.54 V"
    if family == "MS43":
        assert "after main relay" in window._info_field_names["Battery Voltage"].text()
        assert window._info_labels["Ignition Supply Voltage"].text() == "12.70 V"
    else:
        assert window._info_field_names["Ignition Supply Voltage"].isHidden()
    assert "Programming history:" in window._ecu_info_report()
    assert info["aif_records"][0]["vin"] in window._ecu_info_report()


def test_info_multiple_records_remain_distinct_without_repeating_full_summary(window):
    connect(window, "MS43")
    ecu, _ = identity_ecu("MS43")
    info = read_identity(ecu)
    first = info["aif_records"][0]
    second = dict(first, aif_record_number=2, programming_date="25.10.26")
    info["aif_records"] = [first, second]
    window._ms42_ms43_show_info(info)
    assert window._info_labels["Current AIF Record"].text() == "2"
    assert not window._info_field_names["First AIF Record"].isHidden()
    assert "VIN:" not in window._info_labels["First AIF Record"].text()
    assert "VIN:" in window._info_labels["First AIF Record"].toolTip()
    assert "Record 1:" in window._ecu_info_report() and "Record 2:" in window._ecu_info_report()
    show_tab(window, "ECU Info", (1000, 780))
    lineage = window._info_labels["BMW DATEN Lineage"]
    first_label = window._info_labels["First AIF Record"]
    previous_y, previous_height = lineage.y(), first_label.height()
    info["aif_records"] = [first]
    window._ms42_ms43_show_info(info)
    QApplication.processEvents()
    QApplication.processEvents()
    assert previous_y - lineage.y() == previous_height + lineage.parentWidget().layout().verticalSpacing()


def test_ms41_eeprom_controls_return_after_ms43_snapshot_connection(window, monkeypatch):
    from ms42_ms43_storage import make_snapshot

    connect(window, "MS43")
    snapshot = make_snapshot("MS43", "eeprom", b"\xFF" * 2048, software_id="430069")
    window._ms42_ms43_show_storage(snapshot, "Offline backup")
    assert window._ms41_eeprom_body.isHidden()
    window._ds2 = SimpleNamespace(uses_d2xx=True, baud=9600)
    monkeypatch.setattr(window, "_start_session_log", lambda: None)
    window._on_connected(ident=b"1429861", cal_id="1429861", live_variants=("MS41.0", "MS41.0", True))
    assert window.tabs.tabText(window._eeprom_tab_index).strip() == "EEPROM"
    assert not window._ms41_eeprom_body.isHidden()
    assert window._ms42_ms43_data_body.isHidden()
    assert not window._info_field_names["First AIF Record"].isHidden()


@pytest.mark.parametrize("family", ("MS42", "MS43"))
def test_physical_1000_by_800_window_keeps_diagnostic_controls_reachable(window, family):
    connect(window, family)
    ratio = window.devicePixelRatioF()
    size = (round(1000 / ratio), round(800 / ratio))
    show_tab(window, "Diagnostics", size)
    assert abs(window.width() * ratio - 1000) <= ratio
    assert abs(window.height() * ratio - 800) <= ratio
    for index, control in ((1, window.btn_stop_actuator), (2, window.btn_roughness_start)):
        window.diag_tabs.setCurrentIndex(index)
        QApplication.processEvents()
        # Existing main-canvas panning is necessary below its 980px design width.
        window._main_scroll.ensureWidgetVisible(control, 0, 0)
        QApplication.processEvents()
        assert fully_visible(control, window._main_scroll.viewport())
        window._main_scroll.ensureWidgetVisible(window.diag_tabs.tabBar(), 0, 0)
        QApplication.processEvents()
        assert fully_visible(window.diag_tabs.tabBar(), window._main_scroll.viewport())
