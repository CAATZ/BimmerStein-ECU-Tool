import copy

import pytest

from external_patch_library import ExternalPatchLibrary
from ms42_ms43_image import correct_checksums
import ms42_ms43_xdf_patches as patches
from tests.test_ms42_ms43_desktop import window  # noqa: F401
from tests.test_ms42_ms43_image import synthetic_image
from tests.test_ms42_ms43_xdf_patches import fixture


def test_preconfigure_flag_then_install_and_remove_preset(tmp_path):
    addr = 0x48111
    base = bytearray(synthetic_image("MS42"))
    base[addr - 1:addr + 1] = b"\xff\xff"
    source, _ = correct_checksums(base, "MS42")
    xdf = fixture(tmp_path, [
        ("Cluster defaults", [(addr - 1, b"\xff\xff", b"\x71\xfc")]),
        ("Alternate defaults", [(addr, None, b"\xfd")]),
    ], fields=True)
    library = ExternalPatchLibrary(tmp_path / "library")
    digest = library.import_file(xdf)
    catalog = library.catalog(digest)
    flag = patches.configuration(catalog, source)[0]
    change = dict(field_id=flag["id"], group_id="0x1", value=False)
    assert patches.inspect(catalog, source)[0]["has_settings"]
    tuned = library.build(source, digest, changes=[change])
    assert tuned[addr - 1:addr + 1] == b"\xff\xfe"
    assert library.previous(tuned) == source
    history = library.receipt(tuned)
    assert [op["group_id"] for op in history["operations"]] == [None]
    first, alternate = patches.inspect(catalog, tuned, history)
    assert first["installable"] and alternate["state"] == "conflict"
    with pytest.raises(ValueError, match="separately saved setting"):
        library.build(tuned, digest, install=["0x2"], allow_unbased=True)

    installed = library.build(tuned, digest, install=["0x1"])
    assert installed[addr - 1:addr + 1] == b"\x71\xfc"
    receipt = library.receipt(installed)
    assert patches.validate_history(catalog, installed, receipt)
    assert library.previous(installed) == tuned
    assert library.build(installed, digest, remove=["0x1"]) == tuned

    # An independently altered bit still fails the original-byte guard.
    edited = bytearray(tuned)
    edited[addr] ^= 0x80
    with pytest.raises(ValueError, match="Original bytes differ"):
        patches.build(catalog, edited, history=history, install=["0x1"])
    forged = copy.deepcopy(receipt)
    forged["operations"][1]["spans"][0]["before"] = "01fe"
    with pytest.raises(ValueError, match="Original bytes differ"):
        patches.validate_history(catalog, installed, forged)


def test_windows_configure_and_undo_without_staging_install(window, tmp_path, monkeypatch):
    from PyQt5.QtWidgets import QDialog, QInputDialog
    from gui_xdf_patches import FieldDialog

    base, _ = correct_checksums(synthetic_image("MS42"), "MS42")
    address = 0x48111
    source = fixture(tmp_path, [("LED preset", [(address, base[address:address + 1], b"\x08")])], fields=True)
    panel = window._external_patches
    panel.library = ExternalPatchLibrary(tmp_path / "patches")
    panel.library.import_file(source)
    monkeypatch.setattr(QInputDialog, "getText", lambda *args, text: (text, True))

    def toggle(dialog):
        next(iter(dialog.editors.values())).click()
        return QDialog.Accepted

    monkeypatch.setattr(FieldDialog, "exec_", toggle)
    window._set_patch_base(base, "source.bin")
    assert not any(panel.actions.values())
    panel.configure_buttons["0x1"].click()
    assert window.btn_patches_build.isEnabled()
    window.btn_patches_build.click()
    assert panel.image[address] == base[address] ^ 1
    assert panel.history["operations"][0]["group_id"] is None
    panel.undo_build.trigger()
    assert panel.image == base
