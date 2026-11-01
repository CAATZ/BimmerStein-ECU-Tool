"""Raw imports select a proven storage layout without contacting an ECU."""

import pytest
from PyQt5.QtWidgets import QInputDialog

from tests.test_ms42_ms43_desktop import connect, window as window
from tests.test_ms42_ms43_storage_edit_native import journal, slot
from tests.test_ms42_ms43_storage_inspection import put_record


def eeprom(binding):
    data = bytearray(b"\xFF" * 2048)
    put_record(data, 0x32, binding)
    return bytes(data)


@pytest.mark.parametrize("software,binding", [
    ("430037", b"43153010"), ("430055", b"43241010"),
    ("430064", b"43270010"), ("430065", b"43271T10"),
    ("430066", b"43281010"), ("430066", b"43283010"),
    ("0110C6", b"6577225107"), ("0110C7", b"6577225108"),
    ("0110CA", b"6577225109"),
])
def test_unambiguous_native_binding_identifies_offline_raw_backup(window, monkeypatch, software, binding):
    family, kind = ("MS42", "retained") if software.startswith("01") else ("MS43", "eeprom")
    data = journal(((0, slot(10, descriptor=binding)),)) if family == "MS42" else eeprom(binding)
    monkeypatch.setattr(QInputDialog, "getItem", lambda *a: pytest.fail("Binding is unambiguous"))
    monkeypatch.setattr(window, "_run_task", lambda *a, **k: pytest.fail("Import must stay offline"))
    window._ms42_ms43_load_storage_bytes(data, "Raw backup", family=family, kind=kind)
    loaded = window._ms42_ms43_storage_snapshot
    assert loaded["software_id"] == software and loaded["data"] == data
    assert window._ms42_ms43_storage_layout.currentText().endswith(software)
    assert not window._ms42_ms43_storage_write.isEnabled()


@pytest.mark.parametrize("chosen", ["430069", "43X001"])
def test_shared_binding_requires_a_revision_choice(window, monkeypatch, chosen):
    data = eeprom(b"43290010")
    def choose(_owner, _title, _prompt, choices, _index, editable):
        assert choices == ["430069", "43X001"] and not editable
        return chosen, True
    monkeypatch.setattr(QInputDialog, "getItem", choose)
    window._ms42_ms43_load_storage_bytes(data, "Raw backup", family="MS43", kind="eeprom")
    assert window._ms42_ms43_storage_snapshot["software_id"] == chosen
    assert window._ms42_ms43_storage_snapshot["data"] == data


def test_cancelled_revision_choice_preserves_loaded_backup(window, monkeypatch):
    window._ms42_ms43_load_storage_bytes(eeprom(b"43241010"), "Original", family="MS43", kind="eeprom")
    original = dict(window._ms42_ms43_storage_snapshot)
    monkeypatch.setattr(QInputDialog, "getItem", lambda *a: ("", False))
    window._ms42_ms43_load_storage_bytes(eeprom(b"43290010"), "Other", family="MS43", kind="eeprom")
    assert window._ms42_ms43_storage_snapshot == original


def test_invalid_binding_check_is_not_used_to_guess_firmware(window, monkeypatch):
    data = bytearray(eeprom(b"43241010"))
    data[0x3A] ^= 1
    def choose(_owner, _title, _prompt, choices, _index, _editable):
        assert len(choices) == 7
        return "430055", True
    monkeypatch.setattr(QInputDialog, "getItem", choose)
    window._ms42_ms43_load_storage_bytes(data, "Damaged backup", family="MS43", kind="eeprom")
    assert window._ms42_ms43_storage_snapshot["data"] == bytes(data)


def test_connected_import_uses_existing_ecu_context_without_another_query(window, monkeypatch):
    ecu = connect(window, "MS43")
    window._ecu_cal_id = "43X001"
    ecu.read_memory = lambda *a, **k: pytest.fail("No extra ECU query")
    monkeypatch.setattr(QInputDialog, "getItem", lambda *a: pytest.fail("Connected context is available"))
    window._ms42_ms43_load_storage_bytes(eeprom(b"43290010"), "Raw backup", family="MS43", kind="eeprom")
    assert window._ms42_ms43_storage_snapshot["software_id"] == "43X001"


def test_connected_ecu_does_not_override_a_unique_foreign_backup_binding(window, monkeypatch):
    ecu = connect(window, "MS43")
    ecu.read_memory = lambda *a, **k: pytest.fail("No ECU query for a file import")
    monkeypatch.setattr(QInputDialog, "getItem", lambda *a: pytest.fail("Backup binding is unique"))
    data = eeprom(b"43241010")
    window._ms42_ms43_load_storage_bytes(data, "Older backup", family="MS43", kind="eeprom")
    assert window._ms42_ms43_storage_snapshot["software_id"] == "430055"
    assert window._ms42_ms43_storage_snapshot["data"] == data
