"""Non-CRC counter drafts use the existing offline editor, including offset zero."""

import pytest

from checksum import _crc
from gui_ms42_ms43_storage import _StorageBackupDialog
from ms42_ms43_storage import make_snapshot
from tests.test_ms42_ms43_desktop import window as window
from tests.test_ms42_ms43_storage_completion import counter_record
from tests.test_ms42_ms43_storage_editor_ui import storage_snapshot, forbid_external_actions


@pytest.mark.parametrize("family", ("MS42", "MS43"))
def test_offline_counter_dialog_updates_redundant_words_without_ecu_actions(window, monkeypatch, family):
    original = storage_snapshot(family)
    raw = bytearray(original["data"])
    offset = 0x10 if family == "MS42" else 0
    raw[offset:offset + 6] = counter_record(0xBEEF, 2, 3)  # The accepted counter is recovered as one.
    if family == "MS42":
        raw[0x3FE:0x400] = _crc(raw[:0x3E0], 15).to_bytes(2, "little")
    original = make_snapshot(family, original["kind"], raw, software_id=original["software_id"])
    forbid_external_actions(window, monkeypatch)
    dialog = _StorageBackupDialog(window, original)
    editor = dialog._editors[offset][1]
    assert editor.raw_value() == 1 and not dialog.apply_button.isEnabled()
    editor.set_raw_value(0xFFFF)
    assert dialog._changes() == {offset: 0xFFFF}
    editor.set_raw_value(1)
    assert not dialog.apply_button.isEnabled()  # No incidental copy repair on a no-op.
    editor.set_raw_value(0xFFFF)
    dialog.apply_button.click()
    target = dialog.edited_snapshot["data"]
    assert target[offset:offset + 6] == counter_record(0xFFFF, 0, 1)
    allowed = {*range(offset, offset + 6), *({0x3FE, 0x3FF} if family == "MS42" else set())}
    assert all(a == b or index in allowed for index, (a, b) in enumerate(zip(raw, target)))
    assert original["data"] == raw
