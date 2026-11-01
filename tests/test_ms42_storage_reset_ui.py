"""Reset-history engineering units retain their exact offline stored values."""

import pytest

from checksum import _crc
from gui_ms42_ms43_storage import _StorageBackupDialog
from ms42_ms43_storage import make_snapshot
from ms42_ms43_storage_edit import edit_storage
from tests.test_ms42_ms43_desktop import window as window
from tests.test_ms42_ms43_storage_editor_ui import storage_snapshot, forbid_external_actions
from tests.test_ms42_ms43_storage_inspection import put_record


@pytest.mark.parametrize("software,binding", [("0110C6", b"6577225107"),
                                              ("0110C7", b"6577225108"),
                                              ("0110CA", b"6577225109")])
def test_reset_history_units_and_saturated_rpm_roundtrip(window, monkeypatch, software, binding):
    raw = bytearray(storage_snapshot("MS42")["data"])
    raw[4:14] = binding
    put_record(raw, 0x23A, bytes.fromhex("04 12 FF 79 78 A5 34 12 78 56 BC 9A"))
    raw[0x3FE:0x400] = _crc(raw[:0x3E0], 15).to_bytes(2, "little")
    source = make_snapshot("MS42", "retained", raw, software_id=software)
    forbid_external_actions(window, monkeypatch)
    dialog = _StorageBackupDialog(window, source)
    rpm = dialog._editors[0x23C][1]
    voltage = dialog._editors[0x23D][1]
    coolant = dialog._editors[0x23E][1]
    assert "At least 8160 rpm" in rpm.text()
    assert rpm.raw_value() == 255
    assert voltage.value() == pytest.approx(12.337)
    assert coolant.value() == 42
    assert not dialog._changes()
    coolant.setValue(45)
    assert dialog._changes() == {0x23E: 124}
    dialog.apply_button.click()
    assert dialog.edited_snapshot == edit_storage(source, {0x23E: 124})
    assert dialog.edited_snapshot["data"][0x23C] == 255
