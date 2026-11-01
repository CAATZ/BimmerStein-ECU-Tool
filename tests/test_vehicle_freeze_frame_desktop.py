"""Fault details and exports use the same decoded module records, offline."""

from types import SimpleNamespace

import pytest

import gui
from dtc import format_dtc_table, parse_ds2_dtc_response
from tests.test_ms42_ms43_desktop import window as window
from tests.test_vehicle_diagnostics_gs20 import ReplyDS2
from tests.test_vehicle_diagnostics_mk60 import ScriptedMK60
from vehicle_diagnostics import ModuleFault, PROFILE_BY_FAULT_KEY


@pytest.mark.parametrize("key", ("egs_gs8600", "egs_gs8604", "egs_gs20", "dsc_mk60"))
def test_module_snapshot_read_detail_and_export(window, tmp_path, monkeypatch, key):
    profile = PROFILE_BY_FAULT_KEY[key]
    link = object()
    window._ds2 = SimpleNamespace(family="MS43", transport=link)
    window._connection_echo = True
    window.cb_diag_module.addItem(profile.name, profile.module_key)
    window.cb_diag_module.setCurrentIndex(window.cb_diag_module.findData(profile.module_key))
    window.cb_diag_profile.setCurrentIndex(window.cb_diag_profile.findData(key))
    conditions = (
        "Snapshot 1: Battery voltage: 12.4 V; oil temperature: 80 °C",
        "Snapshot 2: input speed: 3200 rpm; mileage: 120000 km",
        "Snapshot 3: state <unmapped>; A & B",
    )
    fault = ModuleFault(key, profile.name, 1, "Synthetic UI fixture", b"\x01\x80\x02",
                        conditions=conditions, environment_raw=b"\x00\xA5\xFF")

    def read(connection, selected_key):
        assert connection is link and selected_key == key
        return [fault]

    monkeypatch.setattr(gui, "read_module_faults", read)
    monkeypatch.setattr(window, "_run_task", lambda task, on_success, **_:
                        on_success(task(lambda *_: None, lambda *_: None)))
    window._on_read_dtc()
    window.dtc_table.selectRow(0)
    window._on_dtc_selected()
    detail = window.dtc_detail.toPlainText()
    for condition in conditions:
        assert condition in detail
    assert "00 A5 FF" in detail
    assert "1 stored fault(s)" in window.lbl_dtc_count.text()

    destination = tmp_path / "faults.txt"
    monkeypatch.setattr(gui.QFileDialog, "getSaveFileName", lambda *_:
                        (str(destination), "Text Files (*.txt)"))
    window._on_export_dtc()
    exported = destination.read_text(encoding="utf-8")
    for condition in conditions:
        assert condition in exported
    assert "Raw environment / detail: 00 A5 FF" in exported
    assert "01 80 02" in exported


def test_ms41_export_does_not_acquire_a_module_environment_row():
    [fault] = parse_ds2_dtc_response(bytes.fromhex("01 64 78 01 28 00 00 03 00 00 00"))
    report = format_dtc_table([fault])
    assert "Raw environment / detail" not in report
    assert "Self-Test Reason: 0x0003" in report
    assert "64 78 01 28 00 00 03 00 00 00" in report


@pytest.mark.parametrize("key,temperature,voltage", (
    ("egs_gs8600", "52 °C", "16 V"),
    ("egs_gs8604", "52 °C", "16 V"),
    ("egs_gs20", "60 °C", "19.8 V"),
))
def test_bmw_transmission_snapshots_reach_desktop_and_export(
        window, tmp_path, monkeypatch, key, temperature, voltage):
    record = bytes((0x22, 0, 1, 0)) + bytes((100, 100, 198, 0x12, 0x34)) * 3
    link = ReplyDS2(b"\x01" + record)
    window._ds2 = SimpleNamespace(family="MS43", transport=link)
    window._connection_echo = True
    window.cb_diag_module.addItem("Automatic Transmission", "egs")
    window.cb_diag_module.setCurrentIndex(window.cb_diag_module.findData("egs"))
    window.cb_diag_profile.setCurrentIndex(window.cb_diag_profile.findData(key))
    monkeypatch.setattr(window, "_run_task", lambda task, on_success, **_:
                        on_success(task(lambda *_: None, lambda *_: None)))
    window._on_read_dtc()
    window._on_dtc_selected()
    destination = tmp_path / "transmission.txt"
    monkeypatch.setattr(gui.QFileDialog, "getSaveFileName", lambda *_:
                        (str(destination), "Text Files (*.txt)"))
    window._on_export_dtc()
    for text in (window.dtc_detail.toPlainText(), destination.read_text(encoding="utf-8")):
        for number in range(1, 4):
            assert f"Snapshot {number}: mileage 46600 km" in text
        assert f"Engine temperature: {temperature}" in text
        assert f"Battery voltage: {voltage}" in text
        assert "Engine speed: 3200 rpm" in text
        assert record.hex(" ").upper() in text
    assert link.calls == [bytes.fromhex("32 05 04 01 32")]


@pytest.mark.parametrize("detail_available", (True, False))
def test_mk60_detail_or_rejection_reaches_desktop_and_export(
        window, tmp_path, monkeypatch, detail_available):
    detail = bytes.fromhex("57 01 5D 90 E1 01 09 12 34 7B 2D 81 09")
    link = ScriptedMK60(bytes.fromhex("58 01 5D 90 E1"),
                        detail if detail_available else bytes.fromhex("7F 17 22"))
    window._ds2 = SimpleNamespace(family="MS43", transport=link)
    window._connection_echo = True
    window.cb_diag_module.addItem("DSC MK60", "dsc_mk60")
    window.cb_diag_module.setCurrentIndex(window.cb_diag_module.findData("dsc_mk60"))
    monkeypatch.setattr(window, "_run_task", lambda task, on_success, **_:
                        on_success(task(lambda *_: None, lambda *_: None)))
    window._on_read_dtc()
    window._on_dtc_selected()
    destination = tmp_path / "mk60.txt"
    monkeypatch.setattr(gui.QFileDialog, "getSaveFileName", lambda *_:
                        (str(destination), "Text Files (*.txt)"))
    window._on_export_dtc()
    for text in (window.dtc_detail.toPlainText(), destination.read_text(encoding="utf-8")):
        assert "0x5D90" in text
        assert "Left-front wheel-speed sensor" in text
        if detail_available:
            assert "Snapshot 1: mileage 46600 km; vehicle speed 123 km/h" in text
            assert "Logistics counter: 9" in text
            assert detail[1:].hex(" ").upper() in text
        else:
            assert "unavailable" in text.lower()
            assert "0x22" in text
    assert link.calls == [bytes.fromhex("B8 29 F1 04 18 02 FF FF"),
                          bytes.fromhex("B8 29 F1 03 17 5D 90")]
    assert window.dtc_table.rowCount() == 1
