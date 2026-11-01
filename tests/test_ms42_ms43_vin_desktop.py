"""VIN tab and retained recovery regressions use the real worker with fake ECUs."""

from contextlib import contextmanager
import sys
from types import SimpleNamespace

import pytest
from PyQt5.QtWidgets import QMessageBox

import ms42_ms43_vin as vin_data
from tests import test_ms42_ms43_desktop as desktop
from tests.test_ms42_ms43_diagnostics import aif_image, aif_record, identity_ecu


window = desktop.window
worker_runner = desktop.worker_runner
OLD_VIN = "WBAAM31010FG12345"
NEW_VIN = "WBAAM31010FG54321"


def connected(window, family="MS43", *, fast=True):
    ecu = desktop.connect(window, family)
    ecu.transport = SimpleNamespace(native_fast_capable=fast, _ser=SimpleNamespace(parity="E"))
    window._ms42_ms43_controls()
    return ecu


def result(family, vin=NEW_VIN):
    raw = aif_image(aif_record(vin=vin))
    return dict(vin_data.decode_aif(raw), family=family, aif_raw=raw,
                aif_address=vin_data.AIF_ADDRESSES[family])


@pytest.mark.parametrize("family", ["MS42", "MS43"])
def test_read_vin_refreshes_identity_once_after_reconnect_and_preserves_ews_state(
        window, worker_runner, family):
    connected(window, family, fast=False)
    window._ms42_ms43_show_info(dict(family=family, reported_identifier="7654321",
                                    dme_production_serial="87654321", isn="9876", isn_status="Stored"))
    connected(window, family, fast=False)
    ecu, link = identity_ecu(family)
    window._ds2 = ecu
    window._identity_isn = "1234"
    window._identity_isn_key = "existing-authorization"
    window._on_identity_read_flash_ecu()
    window._set_all_buttons_enabled(True)
    assert sum(command == 0x0D for _, command, _ in link.calls) == 1
    assert all(command in (0, 6, 0x0D) or (command, args) == (0x0B, b"\x94")
               for _, command, args in link.calls)
    assert not any(0x4000 <= int.from_bytes(args[:4], "big") < 0x8000
                   for _, command, args in link.calls if command == 6)
    assert worker_runner.results[0][0]
    assert window.id_vin_current.text() == OLD_VIN
    assert OLD_VIN in window.id_boot_strings.toPlainText()
    assert "Record 1" in window.id_boot_strings.toPlainText()
    assert window._identity_isn == "1234"
    assert window._identity_isn_key == "existing-authorization"
    assert window._id_labels["part"].text() == "7500255"
    assert window._id_labels["serial"].text() == "12345678"
    assert window._id_labels["isn"].text() == "1234"
    assert window._info_labels["Reported BMW Part Number"].text() == "7500255"
    assert window._info_labels["DME Production Serial"].text() == "12345678"
    assert window._identity_boot_data is None
    assert window.btn_id_read_flash_ecu.isEnabled()
    assert window.btn_id_read_flash_ecu.text() == "Read VIN / AIF"
    assert not window.btn_softbsl_install.isEnabled()


@pytest.mark.parametrize("family", ["MS42", "MS43"])
def test_vin_read_clears_unavailable_identifiers_instead_of_reusing_old_values(
        window, worker_runner, monkeypatch, family):
    from ds2 import DS2Error
    from ms42_ms43_diagnostics import ISN_READS

    connected(window, family)
    window._ms42_ms43_show_info(dict(family=family, reported_identifier="7654321",
                                    dme_production_serial="87654321", isn="9876", isn_status="Stored",
                                    raw_ident=b"OLD IDENT"))
    window._on_show_raw_ident()
    ecu, _ = identity_ecu(family)
    window._ds2 = ecu
    frame, read_memory = ecu._frame, ecu.read_memory

    def without_ident(command, *args, **kwargs):
        if command == 0:
            raise DS2Error("IDENT unavailable")
        return frame(command, *args, **kwargs)

    def without_isn(address, size):
        if address == ISN_READS[family][1]:
            raise DS2Error("ISN unavailable")
        return read_memory(address, size)

    monkeypatch.setattr(ecu, "_frame", without_ident)
    monkeypatch.setattr(ecu, "read_memory", without_isn)
    window._on_identity_read_flash_ecu()
    window._set_all_buttons_enabled(True)
    assert window.id_vin_current.text() == OLD_VIN
    assert window._id_labels["part"].text() == "—"
    assert window._id_labels["serial"].text() == "—"
    assert window._id_labels["isn"].text() == "Unavailable"
    assert window._info_labels["Reported BMW Part Number"].text() == "—"
    assert window._info_labels["DME Production Serial"].text() == "—"
    assert window._info_labels["EWS2 ISN"].text() == "—"
    assert any("IDENT unavailable" in message for message, _ in worker_runner.logs)
    assert any("ISN unavailable" in message for message, _ in worker_runner.logs)
    assert window._identity_isn is None and window._identity_isn_key is None
    assert window._last_ident_raw == b""
    assert not window.raw_ident_view.toPlainText()
    assert "4F 4C 44 20 49 44 45 4E 54" not in window._ecu_info_report()


@pytest.mark.parametrize("family", ["MS42", "MS43"])
def test_vin_read_distinguishes_unreadable_history_from_empty_history(window, worker_runner, family):
    connected(window, family)
    window._ms42_ms43_show_vin(result(family, OLD_VIN))
    ecu, _ = identity_ecu(family, broken_aif=True)
    window._ds2 = ecu
    window._on_identity_read_flash_ecu()
    window._set_all_buttons_enabled(True)
    assert worker_runner.results[-1][0]
    assert "aif_raw" not in window._ms42_ms43_vin_info
    assert not window.id_vin_current.text() and not window._ecu_vin
    assert window._id_labels["vin"].text() == "Unavailable"
    assert "unavailable" in window.id_boot_strings.toPlainText().lower()
    assert "No recorded" not in window.id_boot_strings.toPlainText()
    assert "unavailable" in window.lbl_identity_cache.text().lower()
    assert window._id_labels["part"].text() == "7500255"
    assert window._id_labels["serial"].text() == "12345678"
    assert window._id_labels["isn"].text() == "1234"

    empty = aif_image()
    window._ms42_ms43_show_vin(dict(vin_data.decode_aif(empty), family=family, aif_raw=empty))
    assert window._ms42_ms43_vin_info is not None
    assert window._id_labels["vin"].text() == "Not recorded"
    assert window.id_boot_strings.toPlainText() == "No recorded AIF entries."


@pytest.mark.parametrize("family", ["MS42", "MS43"])
def test_vin_write_does_not_require_cached_identity_and_rate_toggle_is_live(window, family):
    connected(window, family)
    window.id_vin_custom.setText(NEW_VIN)
    assert window.btn_id_vin_apply.isEnabled()
    window.chk_force_slow_ds2.setChecked(True)
    assert not window.btn_id_vin_apply.isEnabled()
    assert window.btn_id_read_flash_ecu.isEnabled()
    window.rb_recovery_auto.setChecked(True)
    assert window.btn_id_vin_apply.isEnabled()
    window.id_vin_custom.setText("WBAAM31010FQ54321")
    assert not window.btn_id_vin_apply.isEnabled()
    assert "excluding I, O and Q" in window.lbl_vin_validation.text()


@pytest.mark.parametrize("family", ["MS42", "MS43"])
@pytest.mark.parametrize("fast", [False, True])
def test_vin_and_virgin_controls_require_runtime_parity_not_native_speed(window, family, fast):
    ecu = connected(window, family, fast=fast)
    window.id_vin_custom.setText(NEW_VIN)
    assert window.btn_id_vin_apply.isEnabled()
    assert window.btn_virginize.isEnabled()
    del ecu.transport._ser.parity
    window._ms42_ms43_controls()
    assert not window.btn_id_vin_apply.isEnabled()
    assert not window.btn_virginize.isEnabled()


@pytest.mark.parametrize("family", ["MS42", "MS43"])
def test_non_ascii_vin_cannot_become_valid_after_uppercase(window, family):
    connected(window, family)
    candidate = NEW_VIN[:14] + "\u00df" + NEW_VIN[-1]
    assert len(candidate) == 16 and len(candidate.upper()) == 17
    window.id_vin_custom.setText(candidate)
    assert not window.btn_id_vin_apply.isEnabled()
    assert "ASCII" in window.lbl_vin_validation.text()


@pytest.mark.parametrize("family", ["MS42", "MS43"])
def test_vin_write_worker_uses_shared_writer_and_updates_existing_views(
        window, worker_runner, monkeypatch, family):
    ecu = connected(window, family)
    lifecycle = []
    written = result(family)

    @contextmanager
    def transfer():
        lifecycle.append("enter")
        yield
        lifecycle.append("exit")

    ecu.transfer = transfer

    def write(owner, vin, *, log_fn, progress_cb):
        assert owner is ecu and vin == NEW_VIN
        lifecycle.append("write")
        log_fn("VIN fake write")
        progress_cb(1, 1, "VIN verified")
        return written

    monkeypatch.setitem(sys.modules, "ms42_ms43_vin_write", SimpleNamespace(write_vin=write))
    monkeypatch.setattr(window, "_prewrite_battery_notice", lambda: "Battery voltage: 13.0 V")
    prompts, messages = [], []
    monkeypatch.setattr(QMessageBox, "question", lambda *args: prompts.append(args) or QMessageBox.Yes)
    monkeypatch.setattr(QMessageBox, "information", lambda *args: messages.append(args))
    window._last_full_read, window._last_full_read_key = b"old", "old"
    window.id_vin_custom.setText(NEW_VIN.lower())
    window._on_identity_vin_apply()
    assert lifecycle == ["enter", "write", "exit"]
    assert len(prompts) == 1 and "16 KB" in prompts[0][2]
    assert worker_runner.progress == [(1, 1, "VIN verified")]
    assert window._ecu_vin == window._info_labels["VIN"].text() == NEW_VIN
    assert window.id_vin_current.text() == NEW_VIN and not window.id_vin_custom.text()
    assert window._last_full_read is None and window._last_full_read_key is None
    assert messages[0][1] == "VIN Write Complete"
    assert "ignition OFF" not in messages[0][2]


def test_vin_cancel_never_starts_writer(window, worker_runner, monkeypatch):
    connected(window)
    monkeypatch.setitem(sys.modules, "ms42_ms43_vin_write", SimpleNamespace(
        write_vin=lambda *a, **k: pytest.fail("Cancelled write reached backend")))
    monkeypatch.setattr(window, "_prewrite_battery_notice", lambda: "Battery voltage unavailable")
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.No)
    window.id_vin_custom.setText(NEW_VIN)
    window._on_identity_vin_apply()
    assert not worker_runner.results


def test_matching_bmw_vin_can_repair_differing_native_vin(window):
    connected(window)
    raw = aif_image(aif_record(vin=OLD_VIN), aif_record(vin=NEW_VIN, marker=255))
    info = dict(vin_data.decode_aif(raw), family="MS43", aif_raw=raw, aif_address=0x3C40)
    window._ms42_ms43_show_vin(info)
    window.id_vin_custom.setText(OLD_VIN)
    assert window.btn_id_vin_apply.isEnabled()
    assert "Latest native VIN differs" in window.id_boot_strings.toPlainText()
    window._ms42_ms43_show_vin(result("MS43", OLD_VIN))
    window.id_vin_custom.setText(OLD_VIN)
    assert not window.btn_id_vin_apply.isEnabled()


def test_failed_vin_worker_invalidates_current_display_and_offers_retained_retry(
        window, worker_runner, monkeypatch):
    ecu = connected(window)
    window._ms42_ms43_show_vin(result("MS43", OLD_VIN))

    def fail(*args, **kwargs):
        ecu.write_state = SimpleNamespace(destructive_started=True, completed=False, operation="vin")
        ecu.can_retry_write = True
        raise RuntimeError("Readback differs; keep ignition on for retry")

    monkeypatch.setitem(sys.modules, "ms42_ms43_vin_write", SimpleNamespace(write_vin=fail))
    monkeypatch.setattr(window, "_prewrite_battery_notice", lambda: "Battery voltage: 13.0 V")
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.Yes)
    monkeypatch.setattr(QMessageBox, "information", lambda *a: pytest.fail("Failed write reported success"))
    monkeypatch.setattr(QMessageBox, "critical", lambda *a: None)
    window._last_full_read, window._last_full_read_key = b"old", "old"
    window.id_vin_custom.setText(NEW_VIN)
    window._on_identity_vin_apply()
    window._set_all_buttons_enabled(True)
    assert not window.id_vin_current.text() and window._ecu_vin is None
    assert window._last_full_read is None and window._last_full_read_key is None
    assert "unverified" in window.lbl_identity_cache.text()
    assert window.id_vin_custom.text() == NEW_VIN
    assert window.btn_native_recovery.isEnabled()
    assert window.btn_native_recovery.text() == "Retry VIN Write"
    assert not window.btn_id_vin_apply.isEnabled()


def test_completed_vin_followup_failure_allows_read_without_replaying_write(window, monkeypatch):
    ecu = connected(window)
    window._ms42_ms43_show_vin(result("MS43", OLD_VIN))
    window.id_vin_custom.setText(NEW_VIN)
    ecu.write_state = SimpleNamespace(destructive_started=True, completed=True, operation="vin")
    ecu.can_retry_write = False
    error = RuntimeError("Completion journal unavailable")
    error.programming_completed = True
    messages = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: messages.append(args))
    window._ms42_ms43_vin_failed(error)
    window._set_all_buttons_enabled(True)
    assert window.btn_id_read_flash_ecu.isEnabled()
    assert not window.btn_native_recovery.isEnabled()
    assert window.id_vin_custom.text() == NEW_VIN and not window.id_vin_current.text()
    assert window._ms42_ms43_identity_info == {} and window._ms42_ms43_vin_info is None
    assert "Follow-up Failed" in messages[0][1]
    assert "do not repeat the write" in window.log_view.toPlainText()


def test_vin_failure_before_programming_keeps_valid_read_cache(window, monkeypatch):
    ecu = connected(window)
    window._ms42_ms43_show_vin(result("MS43", OLD_VIN))
    ecu.write_state = SimpleNamespace(destructive_started=False, completed=False, operation="vin")
    window._last_full_read, window._last_full_read_key = b"old", "old"
    monkeypatch.setattr(QMessageBox, "critical", lambda *args: None)
    window._ms42_ms43_vin_failed(RuntimeError("Backup disk full"))
    assert window.id_vin_current.text() == window._ecu_vin == OLD_VIN
    assert window._last_full_read == b"old" and window._last_full_read_key == "old"


@pytest.mark.parametrize("reason", ["busy", "baud", "actuator", "software", "interrupted"])
def test_vin_write_obeys_existing_connection_and_recovery_states(window, reason):
    ecu = connected(window)
    if reason == "busy":
        window._task_busy = True
    elif reason == "baud":
        ecu.baud_uncertain = True
    elif reason == "actuator":
        window._actuator_cleanup_pending = True
    elif reason == "software":
        window._ecu_cal_id = "430056"
    else:
        ecu.write_state = SimpleNamespace(destructive_started=True, completed=False, operation="vin")
    window.id_vin_custom.setText(NEW_VIN)
    assert not window.btn_id_vin_apply.isEnabled()
    window._task_busy = False
    window._actuator_cleanup_pending = False


def test_vin_failure_keeps_specific_retry_and_retry_updates_vin(window, worker_runner, monkeypatch):
    ecu = connected(window)
    ecu.write_state = SimpleNamespace(destructive_started=True, completed=False, operation="vin")
    ecu.can_retry_write = True
    window._set_all_buttons_enabled(True)
    assert window.btn_native_recovery.isEnabled()
    assert window.btn_native_recovery.text() == "Retry VIN Write"
    assert not window.btn_id_read_flash_ecu.isEnabled()
    assert not window.btn_write_tune.isEnabled()
    calls, messages = [], []

    def retry(*, log_fn, progress_cb):
        calls.append("retry")
        ecu.write_state.completed = True
        return result("MS43")

    ecu.retry_write = retry
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.Yes)
    monkeypatch.setattr(QMessageBox, "information", lambda *a: messages.append(a))
    window._start_native_flash_recovery()
    assert calls == ["retry"]
    assert window.id_vin_current.text() == NEW_VIN
    assert messages[0][1] == "VIN Write Complete"


def test_ms41_identity_labels_restore_and_family_vin_cache_is_discarded(window):
    connected(window)
    window._ms42_ms43_show_vin(result("MS43", OLD_VIN))
    window._restore_ms41_labels()
    assert window._ms42_ms43_vin_info is None
    assert not window.id_vin_current.text()
    assert window.identity_data_group.title() == "BOOT Identity Data"
    assert window.identity_strings_label.text() == "Boot strings:"
    assert window.btn_id_read_flash_ecu.text() == "Read BOOT Identity"
    assert "Requires Soft-BSL" in window.btn_id_read_flash_ecu.toolTip()
    assert not window._id_field_names["isn"].isHidden()
    assert not window._id_labels["isn"].isHidden()


@pytest.mark.parametrize("size", [(980, 740), (1600, 900), (1920, 1080)])
def test_vin_controls_keep_existing_alignment_and_scaling(window, size):
    connected(window)
    window.resize(*size)
    window._ms42_ms43_show_vin(result("MS43", OLD_VIN))
    window.id_vin_custom.setText(NEW_VIN)
    tab = window.id_vin_current
    while window.tabs.indexOf(tab) < 0:
        tab = tab.parentWidget()
    window.tabs.setCurrentWidget(tab)
    window.show()
    desktop.QApplication.processEvents()
    assert window.id_vin_current.height() == window.id_vin_custom.height()
    assert window.id_vin_current.geometry().top() == window.id_vin_custom.geometry().top()
    assert window.btn_id_vin_apply.isEnabled()
    assert window.id_vin_custom.geometry().right() < window.btn_id_vin_apply.geometry().left()
    assert (window.lbl_identity_cache.fontMetrics().horizontalAdvance(window.lbl_identity_cache.text())
            <= window.lbl_identity_cache.contentsRect().width())
    window.chk_force_slow_ds2.setChecked(True)
    desktop.QApplication.processEvents()
    assert (window.lbl_vin_validation.fontMetrics().horizontalAdvance(window.lbl_vin_validation.text())
            <= window.lbl_vin_validation.contentsRect().width())
    assert window.btn_id_vin_apply.geometry().right() <= window.btn_id_vin_apply.parentWidget().width()
