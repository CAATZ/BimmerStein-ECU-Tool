"""Actual desktop worker and passive identity reads; no hardware connections."""

import pytest
from PyQt5.QtWidgets import QApplication

from tests.test_ms42_ms43_desktop import connect, window as window, worker_runner as worker_runner
from tests.test_ms42_ms43_diagnostics import identity_ecu
from tests.test_ms42_ms43_virgin_desktop import virgin_result


@pytest.mark.parametrize("family", ("MS42", "MS43"))
def test_worker_displays_identifiers_and_export_without_arming_ews(
        window, worker_runner, monkeypatch, family):
    connect(window, family)
    ecu, link = identity_ecu(family)
    window._ds2 = ecu
    monkeypatch.setattr(ecu, "read_supply_voltages", lambda: {"battery": 12.9})
    window._ms42_ms43_info()
    assert worker_runner.results[-1][0]
    info = worker_runner.results[-1][1]
    assert info["isn"] == "1234"
    assert window._info_labels["ECU Number"].text() == info["ecu_number"]
    assert window._info_field_names["EWS2 ISN"].text() == "ISN:"
    assert window._info_labels["EWS2 ISN"].text() == "1234"
    assert window._info_labels["ISN Data"].text() == "Stored"
    assert window._id_labels["isn"].text() == "1234"
    assert window._info_labels["Reported BMW Part Number"].text() == "7500255"
    assert window._id_labels["part"].text() == "7500255"
    assert window._id_labels["serial"].text() == "12345678"
    assert window._info_field_names["BMW Program Part Number"].text() == "DATEN Program Number:"
    if family == "MS43":
        assert info["program_part_number"] is None
        assert window._info_labels["BMW Program Part Number"].text() == "—"
        assert "7551615, 7571017" in window._info_labels["BMW Program Part Number"].toolTip()
    assert window._identity_isn is None and window._identity_isn_key is None
    # ECU Info also reads the existing BMW configuration group for transmission
    # mode. Permit that exact passive request, not arbitrary command-0B setup.
    assert all(command in (0, 6, 0x0D) or (command, args) == (0x0B, b"\x94")
               for _, command, args in link.calls)
    assert (0x12, 0x0B, b"\x94") in link.calls
    assert window._info_labels["Transmission Mode"].text() == "Automatic"
    report = window._ecu_info_report()
    assert f"ECU Number: {info['ecu_number']}" in report and "ISN: 1234" in report
    assert "AIF Record Date: 20.10.23" in report
    assert "Reported BMW Part Number: 7500255" in report


def test_missing_identifiers_clear_stale_values_and_ms41_restores_labels(window):
    connect(window, "MS43")
    window._ms42_ms43_show_info(dict(family="MS43", ecu_number="5WK90027", isn="1234", isn_status="Stored"))
    window._ms42_ms43_show_info(dict(family="MS43", isn=None, isn_status="Unavailable"))
    assert window._info_labels["ECU Number"].text() == "—"
    assert window._info_labels["EWS2 ISN"].text() == "—"
    assert window._id_labels["isn"].text() == "Unavailable"
    window._restore_ms41_labels()
    assert window._info_field_names["EWS2 ISN"].text() == "EWS2 ISN:"
    assert window._info_field_names["ECU Number"].isHidden()
    assert window._info_labels["ISN Data"].isHidden()
    assert window._info_labels["Reported BMW Part Number"].isHidden()
    assert window._info_field_names["Reported BMW Part Number"].isHidden()
    assert window._info_field_names["BMW Program Part Number"].text() == "BMW Program Part Number:"
    assert not window._info_labels["BMW Program Part Number"].toolTip()
    assert not window._info_labels["EWS2 ISN"].toolTip()
    report = window._ecu_info_report()
    assert "ECU Number:" not in report and "ISN Data:" not in report
    assert "Reported BMW Part Number:" not in report


def test_virginize_invalidates_previously_displayed_isn(window, monkeypatch):
    from PyQt5.QtWidgets import QMessageBox

    connect(window, "MS43")
    monkeypatch.setattr(QMessageBox, "information", lambda *_: None)
    window._ms42_ms43_show_info(dict(family="MS43", ecu_number="5WK90027", isn="1234", isn_status="Stored",
                                    reported_identifier="7551615"))
    window._ms42_ms43_virginized(virgin_result("MS43"))
    assert window._info_labels["EWS2 ISN"].text() == "—"
    assert window._info_labels["ISN Data"].text() == "Not stored"
    assert window._id_labels["isn"].text() == "Not stored"
    assert window._info_labels["ECU Number"].text() == "5WK90027"
    assert window._id_labels["part"].text() == "7551615"


@pytest.mark.parametrize("size", ((980, 740), (1600, 900), (1920, 1080)))
def test_identifier_fields_use_existing_aligned_grid(window, size):
    connect(window, "MS43")
    window._ms42_ms43_show_info(dict(family="MS43", ecu_number="5WK90027", isn="1234", isn_status="Stored",
                                    reported_identifier="7551615"))
    tab = window._info_labels["ECU Number"].parentWidget()
    while window.tabs.indexOf(tab) < 0:
        tab = tab.parentWidget()
    window.tabs.setCurrentWidget(tab)
    window.resize(*size)
    window.show()
    QApplication.processEvents()
    first, second = (window._info_field_names[name] for name in ("ECU Number", "ISN Data"))
    assert first.geometry().top() == second.geometry().top()
    assert first.geometry().right() < second.geometry().left()
    assert (window._info_labels["Reported BMW Part Number"].y()
            == window._info_labels["Program / Calibration Match"].y())
    for name in ("ECU Number", "EWS2 ISN", "ISN Data", "Reported BMW Part Number"):
        value = window._info_labels[name]
        assert value.geometry().right() <= value.parentWidget().width()
        assert value.fontMetrics().horizontalAdvance(value.text()) <= value.contentsRect().width()
