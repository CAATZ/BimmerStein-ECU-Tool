# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path


ROOT = Path(SPECPATH).resolve().parent
APP_NAME = "BimmerStein ECU Tool"

datas = []


def add_file(source, destination):
    source = Path(source)
    if not source.is_file():
        raise FileNotFoundError(source)
    datas.append((str(source), str(destination)))


def add_tree(source, destination):
    source = Path(source)
    if not source.is_dir():
        raise FileNotFoundError(source)
    for item in sorted(path for path in source.rglob("*") if path.is_file()):
        relative_parent = item.relative_to(source).parent
        datas.append((str(item), str(Path(destination) / relative_parent)))


add_tree(ROOT / "assets", "assets")
add_tree(ROOT / "engines" / "patcher" / "patches", "engines/patcher/patches")
add_tree(ROOT / "THIRD_PARTY_LICENSES", "THIRD_PARTY_LICENSES")
add_file(
    ROOT / "engines" / "patcher" / "romraider" / "BimmerStein MS41 Patch Definitions.xml",
    ".",
)

softbsl_root = ROOT / "engines" / "softbsl"
for filename in (
    "agent.hex",
    "agent_28f.hex",
    "stage1_payload.hex",
    "stage1_manifest.json",
    "agent_manifest.json",
    "loader_sa1_relocated_crc.hex",
    "loader_sa1_relocated_main.hex",
    "loader_sa1_relocated_io.hex",
):
    add_file(softbsl_root / filename, "engines/softbsl")

a = Analysis(
    [str(ROOT / "gui.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=["serial.tools.list_ports_windows"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "pytestqt", "tests", "tkinter"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=True,
    version=str(ROOT / "build" / "windows_version_info.txt"),
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    contents_directory="_internal",
    icon=str(ROOT / "assets" / "bimmerstein_ecu_tool.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=APP_NAME,
)
