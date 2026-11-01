"""Storage UI reaches real workers and never opens a hardware transport."""

from types import SimpleNamespace

import pytest
from PyQt5.QtWidgets import QApplication, QDialog, QFileDialog, QInputDialog, QMessageBox, QScrollArea

import ms42_ms43_storage as storage
from tests.test_ms42_ms43_desktop import connect, window as window, worker_runner as worker_runner


def snapshot(family="MS43", kind="eeprom"):
    return storage.make_snapshot(family, kind, bytes(range(256)) * (storage.SIZES[kind] // 256),
                                 software_id=storage.SOFTWARE[family], hardware_id="test")


@pytest.mark.parametrize("family,kind", (("MS43", "eeprom"), ("MS42", "retained"), ("MS43", "retained")))
def test_read_worker_catalogues_correct_storage_without_writing(window, worker_runner, monkeypatch, family, kind):
    ecu = connect(window, family)
    expected = snapshot(family, kind)
    window._ms42_ms43_show_storage(expected, "Previously loaded")
    calls = []

    def read(current, selected, *, force_slow, log_fn, progress_cb):
        assert current is ecu and selected == kind
        assert not force_slow
        calls.append(selected)
        log_fn("Reading storage")
        progress_cb(1, 1, "Read complete")
        return expected

    monkeypatch.setattr(storage, "read_storage", read)
    monkeypatch.setattr(storage, "restore_storage", lambda *a, **k: pytest.fail("Read must not write"))
    window._ms42_ms43_read_storage()
    assert calls == [kind] and worker_runner.results[0][0]
    assert window._ms42_ms43_storage_snapshot == expected
    entry = window._backup_mgr.entries[0]
    assert entry.variant == family and entry.cs_ok is None
    assert entry.file_type == ("EEPROM" if kind == "eeprom" else "Retained Data")
    assert window._backup_mgr.read_data(entry.filename, entry.sha256) == expected["data"]
    assert window.backup_table.item(0, 5).text() == "UNKNOWN"
    if (family, kind) == ("MS43", "retained"):
        assert window.backup_table.item(0, 2).text() == "Raw flash"
        assert "raw flash banks" in window._ms42_ms43_storage_layout.currentText()
        assert "unverified" in window._ms42_ms43_storage_note.text()
    elif kind == "retained":
        assert "retained-data journal" in window._ms42_ms43_storage_layout.currentText()
        assert "learned values and diagnostic records" in window._ms42_ms43_storage_note.text()


@pytest.mark.parametrize("kind", ("eeprom", "retained"))
def test_restore_uses_loaded_target_and_catalogues_before_and_after(window, worker_runner, monkeypatch, kind):
    ecu = connect(window, "MS43")
    target = snapshot(kind=kind)
    before = storage.make_snapshot("MS43", kind, b"\xFF" * storage.SIZES[kind], software_id="430069")
    window._ms42_ms43_show_storage(target, "Selected backup")
    questions, calls, completions = [], [], []
    monkeypatch.setattr(QMessageBox, "question", lambda *a: questions.append(a) or QMessageBox.Yes)
    monkeypatch.setattr(QMessageBox, "information", lambda *a: completions.append(a))
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a: pytest.fail("Restore uses the displayed target"))

    def restore(current, selected, *, log_fn, progress_cb, backup_cb):
        assert current is ecu and selected == target
        backup_cb(before, "durable-before.jsonl")
        assert window._backup_mgr.entries[0].source.endswith("before restore")
        calls.append(selected)
        progress_cb(1, 1, "Storage restored and verified")
        log_fn("Verified")
        return {"snapshot": selected, "backup": before, "backup_journal": "durable-before.jsonl"}

    monkeypatch.setattr(storage, "restore_storage", restore)
    window._ms42_ms43_restore_storage()
    assert calls == [target] and len(questions) == len(completions) == 1
    assert len(window._backup_mgr.entries) == 2
    assert {entry.source for entry in window._backup_mgr.entries} == {
        f"ECU {kind} before restore", f"ECU {kind} verified restore"}


def test_cancelled_restore_and_different_family_never_start_worker(window, monkeypatch):
    connect(window, "MS43")
    window._ms42_ms43_show_storage(snapshot(), "Target")
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.No)
    monkeypatch.setattr(window, "_run_task", lambda *a, **k: pytest.fail("No work authorized"))
    window._ms42_ms43_restore_storage()
    connect(window, "MS42")
    assert not window._ms42_ms43_storage_write.isEnabled()
    assert window._ms42_ms43_storage_read.isEnabled()
    assert window._ms42_ms43_storage_layout.currentData() == ("MS42", "retained")
    window._ms42_ms43_restore_storage()


@pytest.mark.parametrize("family,kind", (("MS43", "eeprom"), ("MS42", "retained"), ("MS43", "retained")))
def test_bins_load_storage_offline_without_ms41_editor(window, monkeypatch, family, kind):
    expected = snapshot(family, kind)
    monkeypatch.setattr(QInputDialog, "getItem", lambda *a: (storage.SOFTWARE[family], True))
    entry = window._backup_mgr.add_data(expected["data"], "storage.bin", variant=family)
    window._refresh_backup_table()
    window.backup_table.selectRow(0)
    window._set_backup_buttons_enabled()
    monkeypatch.setattr(window, "_show_eeprom_image", lambda *a, **k: pytest.fail("Not an MS41 image"))
    window._on_backup_open_eeprom()
    assert window._ds2 is None
    assert window.tabs.currentIndex() == window._eeprom_tab_index
    assert window._ms42_ms43_storage_snapshot["data"] == expected["data"]
    assert window._ms42_ms43_storage_snapshot["family"] == family
    assert not window._ms42_ms43_storage_write.isEnabled()
    assert window._backup_mgr.read_data(entry.filename, entry.sha256) == expected["data"]
    window._ms42_ms43_close_data()
    assert window._ms42_ms43_storage_snapshot is None
    assert not window._ms41_eeprom_body.isHidden()


@pytest.mark.parametrize("suffix", ("json", "bin"))
def test_loaded_backup_save_and_load_preserve_bytes_and_layout(window, monkeypatch, tmp_path, suffix):
    original = snapshot("MS42", "retained")
    path = tmp_path / f"saved.{suffix}"
    window._ms42_ms43_show_storage(original, "Backup")
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a: (str(path), ""))
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a: (str(path), ""))
    def choose_revision(*args):
        assert suffix == "bin", "JSON already identifies its firmware"
        return "0110C6", True
    monkeypatch.setattr(QInputDialog, "getItem", choose_revision)
    window._ms42_ms43_save_storage()
    window._ms42_ms43_close_data()
    window._ms42_ms43_load_storage()
    assert window._ms42_ms43_storage_snapshot["data"] == original["data"]
    assert window._ms42_ms43_storage_snapshot["family"] == "MS42"
    assert window._ms42_ms43_storage_snapshot["kind"] == "retained"


def test_bad_backup_does_not_replace_current_target(window, monkeypatch, tmp_path):
    original = snapshot()
    window._ms42_ms43_show_storage(original, "Backup")
    path = tmp_path / "bad.bin"
    path.write_bytes(b"invalid")
    warnings = []
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a: (str(path), ""))
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: warnings.append(a))
    window._ms42_ms43_load_storage()
    assert warnings and window._ms42_ms43_storage_snapshot == original


@pytest.mark.parametrize("family,kind", (("MS42", "retained"), ("MS43", "retained"), ("MS43", "eeprom")))
def test_inspect_backup_is_read_only_and_never_starts_ecu_work(window, monkeypatch, family, kind):
    original = snapshot(family, kind)
    window._ms42_ms43_show_storage(original, "Offline backup")
    monkeypatch.setattr(window, "_run_task", lambda *a, **k: pytest.fail("Inspection must stay offline"))
    seen = []

    def inspect(dialog):
        text = dialog.inspection
        assert text.isReadOnly()
        assert "stored-data inspection" in text.toPlainText()
        assert "Raw bytes (file offsets)" in text.toPlainText()
        assert "0000  00 01 02 03" in text.toPlainText()
        seen.append(text.toPlainText())

    monkeypatch.setattr(QDialog, "exec_", inspect)
    window._ms42_ms43_view_storage()
    assert len(seen) == 1 and window._ds2 is None
    assert window._ms42_ms43_storage_snapshot == original


def test_partial_identity_failure_keeps_retained_inspection_available(window, monkeypatch):
    import ms42_ms43_diagnostics as diagnostics

    logs = []
    monkeypatch.setattr(window, "_log", lambda *args: logs.append(args))
    info = {"family": "MS43", "warnings": ["Identity unavailable"],
            "retained_address": diagnostics.RETAINED_ADDRESS,
            "retained_provenance": diagnostics.RETAINED_PROVENANCE,
            "retained_raw": b"\xFF" * 0x4000}
    window._ms42_ms43_show_data(info)
    assert window._ms42_ms43_data_snapshot == info
    assert window._ms42_ms43_storage_snapshot is None
    assert "Identity unavailable" in window._ms42_ms43_data_text.toPlainText()
    assert logs and "restore metadata is unavailable" in logs[0][0]


@pytest.mark.parametrize("kind", ("eeprom", "retained"))
def test_bins_replaced_bytes_cannot_inherit_stale_storage_layout(window, monkeypatch, kind):
    from pathlib import Path

    original = snapshot(kind=kind)
    window._ms42_ms43_show_storage(original, "Original target")
    entry = window._backup_mgr.add_data(original["data"], "storage.bin", variant="MS43")
    window._refresh_backup_table()
    window.backup_table.selectRow(0)
    Path(entry.path).write_bytes(b"\x01" * len(original["data"]))
    errors = []
    monkeypatch.setattr(QMessageBox, "critical", lambda *a: errors.append(a))
    window._on_backup_open_eeprom()
    assert errors and window._ms42_ms43_storage_snapshot == original


def test_verified_restore_still_reports_success_when_catalog_index_fails(window, monkeypatch):
    from backup_manager import BackupIndexError

    target, messages, logs = snapshot(), [], []
    monkeypatch.setattr(window, "_ms42_ms43_catalog_storage",
                        lambda *a: (_ for _ in ()).throw(BackupIndexError("index unavailable")))
    monkeypatch.setattr(window, "_refresh_backup_table", lambda: pytest.fail("Skip refresh after failed index commit"))
    monkeypatch.setattr(window, "_log", lambda *a: logs.append(a))
    monkeypatch.setattr(QMessageBox, "information", lambda *a: messages.append(a))
    window._ms42_ms43_storage_restored({"snapshot": target, "backup_journal": "durable-before.jsonl"})
    assert window._ms42_ms43_storage_snapshot == target
    assert messages[0][1] == "Storage Restored"
    assert "verified" in logs[0][0] and "durable-before.jsonl" in logs[0][0]


@pytest.mark.parametrize("kind", ("eeprom", "retained"))
def test_storage_retry_uses_original_transaction_and_distinct_message(window, worker_runner, monkeypatch, kind):
    ecu = connect(window, "MS43")
    original = snapshot(kind=kind)
    window._ms42_ms43_show_storage(original, "Backup")
    ecu.write_state = SimpleNamespace(operation=f"{kind}_restore", destructive_started=True, completed=False)
    ecu.can_retry_write = True
    questions = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a: questions.append(a) or QMessageBox.Yes)
    monkeypatch.setattr(QMessageBox, "information", lambda *a: None)
    ecu.retry_write = lambda **_: {"snapshot": original, "backup_journal": "saved.jsonl"}
    window._ms42_ms43_update_data()
    assert window._ms42_ms43_storage_retry.isEnabled()
    assert not window._ms42_ms43_storage_load.isEnabled()
    assert not window._ms42_ms43_storage_write.isEnabled()
    window._ms42_ms43_retry()
    assert "Actual storage will be read" in questions[0][2]
    assert "erase and rewrite" not in questions[0][2]
    assert worker_runner.results[0][0]


@pytest.mark.parametrize("size", ((900, 700), (1280, 800), (1920, 1080)))
@pytest.mark.parametrize("family,kind", (("MS42", "retained"), ("MS43", "retained"), ("MS43", "eeprom")))
def test_storage_buttons_align_and_page_scrolls_without_horizontal_overflow(window, size, family, kind):
    connect(window, family)
    window._ms42_ms43_show_storage(snapshot(family, kind), "Backup")
    window.tabs.setCurrentIndex(window._eeprom_tab_index)
    window.resize(*size)
    window.show()
    QApplication.processEvents()
    left, right = window._ms42_ms43_storage_read, window._ms42_ms43_storage_load
    assert left.geometry().top() == right.geometry().top()
    assert abs(left.width() - right.width()) <= 1
    page = window.tabs.widget(window._eeprom_tab_index)
    assert isinstance(page, QScrollArea)
    assert page.horizontalScrollBar().maximum() == 0
