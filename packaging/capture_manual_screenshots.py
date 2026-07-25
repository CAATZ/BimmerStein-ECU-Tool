"""Capture release-manual screenshots with synthetic, non-ECU data only.

This developer tool renders the real PyQt5 interface in Qt's offscreen mode. It
never opens a serial port, reads a ROM, or uses files from ``backups``/``logs``.
Every identity, port name, status line, and patch record visible in the output
is created below solely for documentation.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _select_tab(window, title: str) -> None:
    for index in range(window.tabs.count()):
        if window.tabs.tabText(index).strip() == title:
            window.tabs.setCurrentIndex(index)
            return
    raise RuntimeError(f"Documentation tab not found: {title}")


def _save_window(window, app, destination: Path) -> None:
    from PyQt5.QtCore import QPoint
    from PyQt5.QtGui import QColor, QImage, QPainter

    app.processEvents()
    window.repaint()
    app.processEvents()
    destination.parent.mkdir(parents=True, exist_ok=True)
    image = QImage(window.size(), QImage.Format_ARGB32_Premultiplied)
    image.fill(QColor("#101215"))
    painter = QPainter(image)
    window.render(painter, QPoint())
    painter.end()
    if not image.save(str(destination), "PNG"):
        raise RuntimeError(f"Could not write documentation screenshot: {destination}")


def _synthetic_patch_records() -> list[dict]:
    return [
        {
            "id": "cal_guard",
            "title": "Calibration Guard",
            "version": "V3",
            "description": "Protects startup from an incompatible calibration version.",
            "user_description": "Protects startup from an incompatible calibration version.",
            "target": "MS41.3",
            "status": "TESTED",
            "tested": True,
            "requires": [],
            "conflicts": [],
            "ok": True,
            "badge": "",
            "installed": True,
            "needs_boot": True,
            "legacy": [],
            "deprecated": False,
            "removable": False,
        },
        {
            "id": "ignition_cut_v7",
            "title": "Ignition Cut",
            "version": "V7",
            "description": "Current ignition-cut limiter implementation.",
            "user_description": "Adds the current configurable ignition-cut limiter.",
            "target": "MS41.3",
            "status": "UNTESTED",
            "tested": False,
            "requires": [],
            "conflicts": [],
            "ok": True,
            "badge": "",
            "installed": False,
            "needs_boot": False,
            "legacy": [],
            "deprecated": False,
            "removable": False,
        },
        {
            "id": "launch_control_v5",
            "title": "Launch Control / 2-step",
            "version": "V5",
            "description": "Current staged launch-control limiter implementation.",
            "user_description": "Adds configurable staged launch control.",
            "target": "MS41.3",
            "status": "UNTESTED",
            "tested": False,
            "requires": ["ignition_cut_v7"],
            "conflicts": [],
            "ok": True,
            "badge": "",
            "installed": False,
            "needs_boot": False,
            "legacy": [],
            "deprecated": False,
            "removable": False,
        },
        {
            "id": "softbsl_loader",
            "title": "Persistent Soft-BSL Loader",
            "version": "",
            "description": "Installs the persistent Soft-BSL entry loader.",
            "user_description": "Installs the persistent Soft-BSL entry loader.",
            "target": "MS41.3",
            "status": "TESTED",
            "tested": True,
            "requires": [],
            "conflicts": [],
            "ok": True,
            "badge": "",
            "installed": False,
            "needs_boot": True,
            "legacy": [],
            "deprecated": False,
            "removable": False,
        },
    ]


def main() -> int:
    scratch_root = ROOT / ".tmp"
    scratch_root.mkdir(parents=True, exist_ok=True)
    temporary_data = tempfile.TemporaryDirectory(
        prefix="manual-appdata-", dir=scratch_root
    )
    os.environ["LOCALAPPDATA"] = temporary_data.name

    from PyQt5.QtGui import QFont, QFontDatabase
    from PyQt5.QtWidgets import QApplication

    import gui
    import patch_service

    app = QApplication.instance() or QApplication([])
    # Qt's Windows offscreen platform does not enumerate system fonts. Register
    # the two families used by the GUI explicitly so documentation text is
    # rendered instead of producing empty control silhouettes.
    for font_path in (
        Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "segoeui.ttf",
        Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "segoeuib.ttf",
        Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "seguiemj.ttf",
        Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "seguisym.ttf",
        Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "cour.ttf",
        Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "courbd.ttf",
    ):
        if font_path.is_file():
            QFontDatabase.addApplicationFont(str(font_path))
    app.setFont(QFont("Segoe UI", 10))
    gui.configure_application(app)
    window = gui.MS41FlashGUI()
    window.resize(1600, 940)

    # Replace any locally enumerated hardware with an unmistakably synthetic
    # documentation entry before the first image is rendered.
    window.cb_port.clear()
    window.cb_port.addItem("COM7 (Documentation Demo)")
    if hasattr(window, "cb_bsl_port"):
        window.cb_bsl_port.clear()
        window.cb_bsl_port.addItem("COM8 (Documentation Demo)")
    window.lbl_status.setText("● Connected (documentation demo)")
    window.lbl_status.setStyleSheet("color:#5f5; font-weight:bold;")
    window.lbl_variant.setText("MS41.3 · synthetic data")
    window.lbl_transfer_mode.setText(
        "Transfer: native-fast DS2, 187,500 baud (documentation demo)"
    )
    window.lbl_transfer_mode.setStyleSheet("color:#9ece6a; padding:4px;")
    for button in (
        window.btn_read_full,
        window.btn_read_tune,
        window.btn_write_full,
        window.btn_write_tune,
        window.btn_reset_adapt,
    ):
        button.setEnabled(True)
    window.log_view.setPlainText(
        "Documentation mode — no serial port is open.\n"
        "Synthetic MS41.3 example loaded for the user manual.\n"
        "Ready."
    )
    window.show()
    app.processEvents()

    images = ROOT / "manual" / "images"
    _select_tab(window, "Flash")
    _save_window(window, app, images / "application-overview.png")

    window.chk_backup_before_write.setChecked(True)
    window.chk_verify.setChecked(True)
    _save_window(window, app, images / "flash-workflow.png")

    synthetic_info = {
        "ECU ID": "1437806 (synthetic)",
        "CAL ID": "98A0 (synthetic)",
        "Detected Variant": "MS41.3",
        "Firmware Version": "Documentation example",
        "VIN": "WBAZZ0000TEST0001",
        "ISN": "0000 (synthetic)",
        "Flash Chip": "Intel 28F200",
        "Transmission": "Manual",
    }
    for key, value in synthetic_info.items():
        window._info_labels[key].setText(value)
    _select_tab(window, "ECU Info")
    _save_window(window, app, images / "diagnostics.png")

    original_available = patch_service.available_patches
    try:
        patch_service.available_patches = lambda _data: _synthetic_patch_records()
        window._patch_base = bytes(256 * 1024)
        window._patch_base_source = "Synthetic_MS41_3_Demo.bin"
        window.lbl_patch_base.setText(
            "Base: Synthetic_MS41_3_Demo.bin — MS41.3 documentation example"
        )
        window._refresh_patch_list()
        window._patch_checkboxes["ignition_cut_v7"].setChecked(True)
        window._patch_checkboxes["launch_control_v5"].setChecked(True)
    finally:
        patch_service.available_patches = original_available
    _select_tab(window, "Patches")
    _save_window(window, app, images / "patches.png")

    demo_definition = Path(temporary_data.name) / "Synthetic_MS41_Documentation.xml"
    demo_definition.write_text(
        """<roms><rom><romid><xmlid>DOC41</xmlid>
<internalidaddress>0xE</internalidaddress><internalidstring>41</internalidstring>
<filesize>24kb</filesize><submodel>MS41 Documentation Example</submodel>
<ecuid>SYNTHETIC</ecuid></romid>
<table name="Example Scalar" category="Documentation" storageaddress="0x20"
sizex="1" sizey="1" storagetype="uint8">
<scaling units="raw" expression="x" format="0" /></table></rom></roms>""",
        encoding="utf-8",
    )
    registered = window._definition_registry.import_file(demo_definition)
    window._definition_registry.set_active(registered.path.name)
    window._refresh_analyzer_definitions(registered.path.name)
    demo_tune = bytearray(b"\xFF" * (24 * 1024))
    demo_tune[0xE:0x10] = b"41"
    demo_tune[0x20] = 42
    window._show_analysis(demo_tune, "Synthetic_MS41_Documentation.bin")
    _select_tab(window, "ROM Analyzer")
    _save_window(window, app, images / "rom-analyzer.png")
    window._open_analyzer_parameters_window()
    parameters_window = window._analyzer_parameters_window
    if parameters_window is None:
        raise RuntimeError("ROM Analyzer parameters window did not open")
    parameters_window.resize(1200, 720)
    _save_window(parameters_window, app, images / "rom-analyzer-parameters.png")
    parameters_window.close()

    window._softbsl_marker_lbl.setText("B   (synthetic installed-loader example)")
    window._softbsl_preview.setPlainText(
        "Prepared image: synthetic MS41.3 documentation base\n"
        "Target: inactive TOP half\n"
        "Identity: preserve VIN / ISN\n"
        "Status: review required before any write"
    )
    _select_tab(window, "Soft-BSL")
    _save_window(window, app, images / "softbsl.png")

    window.cb_bsl_baud.setCurrentIndex(2)
    chip_index = window.cb_bsl_chip.findData("28f200")
    if chip_index < 0:
        raise RuntimeError("Intel 28F200 documentation option is unavailable")
    window.cb_bsl_chip.setCurrentIndex(chip_index)
    window._bsl_ref_lbl.setText("Synthetic_Reference.bin")
    window._bsl_preview.setPlainText(
        "DOCUMENTATION PREVIEW — no hardware operation\n"
        "Chip: Intel 28F200\n"
        "Region: tune sector\n"
        "Reference: synthetic 256 KB image\n"
        "Action: review erase/program ranges before confirmation"
    )
    _select_tab(window, "BSL-Unbricker")
    _save_window(window, app, images / "bsl-unbricker.png")

    window.close()
    temporary_data.cleanup()
    print(f"Manual screenshots written to {images}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
