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
            "title": "CalGuard compatibility + recovery guard",
            "version": "V5",
            "description": "Adds a compatibility gate and boot-recovery window.",
            "user_description": "Adds a compatibility gate and boot-recovery window.",
            "target": "MS41.3",
            "status": "OFFLINE VERIFIED - BENCH TEST REQUIRED",
            "tested": False,
            "requires": ["softbsl_loader"],
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
            "id": "door_magic",
            "title": "DS2 0x2A Soft-BSL entry",
            "version": "V2",
            "description": "Adds normal-mode DS2 entry into the persistent Soft-BSL loader.",
            "user_description": "Adds normal-mode DS2 entry into the persistent Soft-BSL loader.",
            "target": "MS41.3",
            "status": "TESTED",
            "tested": True,
            "requires": ["softbsl_loader"],
            "conflicts": [],
            "ok": True,
            "badge": "",
            "installed": True,
            "needs_boot": False,
            "legacy": [],
            "deprecated": False,
            "removable": True,
        },
        {
            "id": "ignition_cut_v7",
            "title": "Ignition Cut",
            "version": "V7",
            "description": "Independent ignition-cut limiter using the six-channel coil output gate.",
            "user_description": "Adds an independent ignition-cut limiter.",
            "target": "MS41.3",
            "status": "OFFLINE EXACT-BYTE VERIFIED - ON-CAR TEST REQUIRED",
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
            "description": "Independent launch requester using fuel cut or the shared V7 ignition engine.",
            "user_description": "Adds independently armed staged launch control.",
            "target": "MS41.3",
            "status": "OFFLINE EXACT-BYTE VERIFIED - ON-CAR TEST REQUIRED",
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
            "title": "Soft-BSL 0x5A loader",
            "version": "V11",
            "description": "Installs the persistent Soft-BSL entry loader.",
            "user_description": "Installs the persistent Soft-BSL entry loader.",
            "target": "MS41.3",
            "status": "OFFLINE VERIFIED - BENCH TEST REQUIRED",
            "tested": False,
            "requires": [],
            "conflicts": [],
            "ok": True,
            "badge": "",
            "installed": True,
            "needs_boot": True,
            "legacy": [],
            "deprecated": False,
            "removable": False,
            "required_by": ["cal_guard", "door_magic"],
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
    font = QFont("Segoe UI")
    font.setPointSizeF(8.25)  # Match Qt's normal Windows application font.
    app.setFont(font)
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
        window.btn_read_adaptations,
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
        "ECU Family": "MS41.3",
        "Reported ECU Identifier": "1437806 (synthetic)",
        "BMW Program Part Number": "1406464 (synthetic)",
        "Calibration ID": "98A0 (synthetic)",
        "Program / Calibration Match": "Match (synthetic)",
        "Recorded ZB / ZUSB": "Documentation example",
        "Programming Date": "2026-08-22 (synthetic)",
        "Recorded Software Number": "0000000 (synthetic)",
        "Programming Count": "1 (synthetic)",
        "BMW DATEN Lineage": "MS41.3 documentation example",
        "VIN": "WBAZZ0000TEST0001",
        "DME Production Serial": "900000001 (synthetic)",
        "EWS2 ISN": "0000 (synthetic)",
        "Transmission Mode": "Manual",
    }
    for key, value in synthetic_info.items():
        window._info_labels[key].setText(value)
    _select_tab(window, "ECU Info")
    _save_window(window, app, images / "diagnostics.png")

    synthetic_adaptations = {
        "ecu_id": "1437806 (synthetic)",
        "additive": (0.18, -0.11),
        "ltft": (1.42, -0.76),
        "throttle": 2.35,
        "load": (180.0, 440.0, 700.0, 960.0),
        "rpm": tuple(800.0 + 400.0 * index for index in range(16)),
        "knock": tuple(
            tuple(
                tuple(-0.75 + 0.25 * ((table + row + column) % 7)
                      for column in range(4))
                for row in range(16)
            )
            for table in range(6)
        ),
    }
    window._show_adaptations(synthetic_adaptations)
    window.lbl_adapt_status.setText(
        "Synthetic documentation data — no ECU read"
    )
    _select_tab(window, "Adaptations")
    _save_window(window, app, images / "adaptations.png")

    synthetic_eeprom = bytearray(
        (index * 37 + 11) & 0xFF for index in range(gui.eeprom_ram.EEPROM_SIZE)
    )
    for field in gui.eeprom_ram.fields_for_variant("MS41.3"):
        if not field.checked:
            continue
        payload_end = field.offset + field.length - 2
        synthetic_eeprom[payload_end:payload_end + 2] = (
            gui.eeprom_ram.additive_check(
                synthetic_eeprom[field.offset:payload_end]
            ).to_bytes(2, "little")
        )
    synthetic_eeprom = gui.eeprom_ram.set_transmission_mode(
        synthetic_eeprom, "mt", "MS41.3"
    )
    window._show_eeprom_image(
        synthetic_eeprom,
        "Synthetic_MS41_3_EEPROM_Documentation.bin",
        variant="MS41.3",
    )
    _select_tab(window, "EEPROM")
    _save_window(window, app, images / "eeprom.png")

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
