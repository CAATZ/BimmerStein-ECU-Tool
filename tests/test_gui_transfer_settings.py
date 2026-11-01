import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PyQt5.QtWidgets")
from PyQt5.QtCore import QSettings, QTimer
from PyQt5.QtWidgets import QApplication, QDialog, QDialogButtonBox, QRadioButton

import gui
import transfer_rates
from tests.test_ms42_ms43_desktop import connect


@pytest.fixture
def window(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)
    monkeypatch.setattr(gui.MS41FlashGUI, "_live_settings", staticmethod(lambda: settings))
    monkeypatch.setattr(gui.MS41FlashGUI, "_refresh_ports", lambda self: None)
    view = gui.MS41FlashGUI()
    yield app, view, settings
    view.hide()
    view.deleteLater()
    app.processEvents()
    transfer_rates.set_compatibility_baud(False)


def test_saved_rate_restores_and_indicator_is_shared_across_tabs(window):
    app, view, settings = window
    assert transfer_rates.get_high_baud() == 187500
    assert "Standard" in view.lbl_high_speed_mode.text()
    assert view._save_transfer_rate(True)
    assert settings.value("connection/compatibility_baud", type=bool) is True
    assert transfer_rates.get_high_baud() == 125000
    view.show()
    for tab in range(view.tabs.count()):
        view.tabs.setCurrentIndex(tab)
        app.processEvents()
        assert view.lbl_high_speed_mode.isVisible()
        assert "Compatibility · 125,000 bps" in view.lbl_high_speed_mode.text()
        assert view.btn_transfer_settings.isVisible()
    restored = gui.MS41FlashGUI()
    try:
        assert "Compatibility · 125,000 bps" in restored.lbl_high_speed_mode.text()
        assert transfer_rates.get_high_baud() == 125000
        assert restored._save_transfer_rate(False)
        assert settings.value("connection/compatibility_baud", type=bool) is False
    finally:
        restored.deleteLater()


@pytest.mark.parametrize("owner", [
    "_task_busy", "_connect_pending", "_eeprom_task_busy", "_poller",
    "_roughness_poller", "_actuator_cleanup_pending", "_native_write_recovery",
    "_softbsl_write_recovery", "_softbsl_install_recovery",
    "_eeprom_write_recovery", "_softbsl_boot_session",
])
def test_active_operation_or_recovery_cannot_change_rate(window, owner):
    _app, view, settings = window
    setattr(view, owner, object())
    try:
        assert not view._save_transfer_rate(True)
        assert transfer_rates.get_high_baud() == 187500
        assert not settings.contains("connection/compatibility_baud")
    finally:
        setattr(view, owner, None)


def test_settings_save_cancel_and_locked_choices(window):
    app, view, _settings = window
    observations = []

    def choose_compatibility(accept):
        dialog = app.activeModalWidget()
        assert isinstance(dialog, QDialog)
        choices = dialog.findChildren(QRadioButton)
        observations.append([choice.text() for choice in choices])
        choices[1].setChecked(True)
        dialog.accept() if accept else dialog.reject()

    QTimer.singleShot(0, lambda: choose_compatibility(False))
    view._show_transfer_settings()
    assert transfer_rates.get_high_baud() == 187500
    QTimer.singleShot(0, lambda: choose_compatibility(True))
    view._show_transfer_settings()
    assert transfer_rates.get_high_baud() == 125000
    assert observations[0] == observations[1] == [
        "Standard — 187,500 bps (default)", "Compatibility — 125,000 bps"]

    def inspect_locked():
        dialog = app.activeModalWidget()
        observations.append(all(not choice.isEnabled()
                                for choice in dialog.findChildren(QRadioButton)))
        buttons = dialog.findChild(QDialogButtonBox)
        observations.append(not buttons.button(QDialogButtonBox.Save).isEnabled())
        dialog.reject()

    view._task_busy = True
    QTimer.singleShot(0, inspect_locked)
    view._show_transfer_settings()
    view._task_busy = False
    assert observations[-2:] == [True, True]


@pytest.mark.parametrize("family,standard_baud", [("MS42", 166667), ("MS43", 187500)])
def test_ms42_ms43_settings_save_cancel_and_connection_persistence(
        window, family, standard_baud):
    app, view, settings = window
    ecu = connect(view, family)
    ecu.transport = SimpleNamespace(native_fast_capable=True)
    closes = []
    ecu.close = lambda: closes.append(True)
    view._update_transfer_mode()
    assert view.btn_transfer_settings.isEnabled()
    assert f"Standard · {standard_baud:,} bps" in view.lbl_high_speed_mode.text()
    observed_rate = view.lbl_observed_rate.text()

    def choose(accept):
        dialog = app.activeModalWidget()
        choices = dialog.findChildren(QRadioButton)
        assert choices[0].text() == f"Standard — {standard_baud:,} bps (default)"
        assert choices[1].text() == "Compatibility — 125,000 bps"
        assert all(choice.isEnabled() for choice in choices)
        choices[1].setChecked(True)
        dialog.accept() if accept else dialog.reject()

    QTimer.singleShot(0, lambda: choose(False))
    view._show_transfer_settings()
    assert transfer_rates.get_high_baud() == 187500
    assert not settings.contains("connection/compatibility_baud")
    QTimer.singleShot(0, lambda: choose(True))
    view._show_transfer_settings()
    assert settings.value("connection/compatibility_baud", type=bool) is True
    assert "Compatibility · 125,000 bps" in view.lbl_high_speed_mode.text()
    assert "125,000 baud" in view.lbl_transfer_mode.toolTip()
    assert view.lbl_observed_rate.text() == observed_rate

    assert view._disconnect()
    assert closes == [True]
    reconnect = connect(view, "MS43" if family == "MS42" else "MS42")
    reconnect.transport = SimpleNamespace(native_fast_capable=True)
    view._update_transfer_mode()
    assert "Compatibility · 125,000 bps" in view.lbl_high_speed_mode.text()
    assert settings.value("connection/compatibility_baud", type=bool) is True
    assert view._save_transfer_rate(False)
    other_standard = 187500 if family == "MS42" else 166667
    assert f"Standard · {other_standard:,} bps" in view.lbl_high_speed_mode.text()
    view._ds2 = None
    view._update_transfer_mode()
    assert "Standard · 187,500 bps" in view.lbl_high_speed_mode.text()


@pytest.mark.parametrize("family", ["MS42", "MS43"])
def test_ms42_ms43_retained_write_locks_settings_until_completed(window, family):
    app, view, settings = window
    ecu = connect(view, family)
    ecu.write_state = SimpleNamespace(destructive_started=True, completed=False)
    assert not view._save_transfer_rate(True)
    assert not settings.contains("connection/compatibility_baud")

    def inspect_locked():
        dialog = app.activeModalWidget()
        assert all(not choice.isEnabled() for choice in dialog.findChildren(QRadioButton))
        buttons = dialog.findChild(QDialogButtonBox)
        assert not buttons.button(QDialogButtonBox.Save).isEnabled()
        dialog.reject()

    QTimer.singleShot(0, inspect_locked)
    view._show_transfer_settings()
    ecu.write_state.completed = True
    assert view._save_transfer_rate(True)
    view._ds2 = None


@pytest.mark.parametrize("family", ["MS42", "MS43"])
def test_ms42_ms43_preferences_do_not_change_observed_link_or_force_slow_route(window, family):
    _app, view, _settings = window
    ecu = connect(view, family)
    ecu.transport = SimpleNamespace(native_fast_capable=True)
    view.chk_force_slow_ds2.setChecked(True)
    assert view._save_transfer_rate(True)
    assert view.lbl_high_speed_mode.text() == f"{family} · 9,600 bps"
    assert view.lbl_transfer_mode.text() == f"Transfer: {family} · DS2 read / write"
    view._ms42_ms43_show_transfer_rate(125000, "RAM transfer")
    ecu.baud_uncertain = True
    assert view._save_transfer_rate(False)  # Changes only the next session's preference.
    assert view.lbl_observed_rate.text() == "Current link: 125,000 baud (RAM transfer)"
    assert view.lbl_high_speed_mode.text() == f"{family} · 9,600 bps"
    view._ds2 = None
