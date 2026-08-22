import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PyQt5")
from PyQt5.QtWidgets import QApplication, QPushButton

import gui
import patch_service
from tests.conftest import ref


def test_installed_parameter_patch_has_configure_button():
    app = QApplication.instance() or QApplication([])
    window = gui.MS41FlashGUI()
    try:
        image = patch_service.build_image(ref("MS41.3"), ["ignition_cut_v9"])[0]
        window._set_patch_base(image, "parameter-test.bin")

        group = window._patch_parameter_groups["ignition_cut_v9"]
        row = window._patch_rows["ignition_cut_v9"]
        labels = [button.text() for button in row.findChildren(QPushButton)]
        assert group["editable"] is True
        assert "Configure" in labels
        assert len(group["parameters"]) == 4
    finally:
        window.close()
        app.processEvents()
