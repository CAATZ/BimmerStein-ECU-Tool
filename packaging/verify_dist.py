#!/usr/bin/env python3
"""Verify the Windows onedir package without opening an ECU connection."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
APP_NAME = "BimmerStein ECU Tool"
PATCH_DEFINITION_NAME = "BimmerStein MS41 Patch Definitions.xml"
REQUIRED_PATCH_IDS = {
    "amd_flash",
    "cal_guard",
    "door_magic",
    "softbsl_loader",
    "ignition_cut_v7",
    "launch_control_v4",
    "launch_control_v5",
    "launch_control_v4_ms412",
}
PE_MACHINE_AMD64 = 0x8664
REQUIRED_LICENSE_FILES = {
    "Nuitka-4.1.3-LICENSE-RUNTIME.txt": (
        "20ff0ae581adf436a7b06e50e67a6c8913aec1ea4e60dba138d0a0bee7ee520c"
    ),
    "PyInstaller-6.21.0-COPYING.txt": (
        "dcf75fdb959db1e3b41c0f8505069d2ece781b5ec6b3d0a4d30975cfc6580245"
    ),
    "PyQt5-sip-12.18.0-BSD-2-Clause.txt": (
        "3e6f5b427c36f94ecf86bc01698af7030a1ed6eb3748110d5dbb8d142d804611"
    ),
    "PyUSB-1.3.1-BSD-3-Clause.txt": (
        "03e39fdcee9c18f2f9d0c3500a993ddeac050695eb81070ea41347587c76a7fe"
    ),
    "Python-3.14.6-LICENSE.txt": (
        "935cf13e19f8c31b497d20b05d73623431a226b230c3599bc30fa3348979bc68"
    ),
    "Python-3.14.6-INCORPORATED-SOFTWARE-NOTICES.rst.txt": (
        "c695d550b135e53e38807e76496d1db17d22c40e461d1f3f354c86188d3305dd"
    ),
    "Qt-5.15.2-LICENSE.txt": (
        "2004ee3ef8282a85f7dbd035dfacf63cf03d569537bc08655ffeb140ea3671c5"
    ),
    "pyserial-3.5-BSD-3-Clause.txt": (
        "ddba22532a6f362880d849b5e2ed4b0a288b8bec4315364d6640d8dad3feea27"
    ),
    "libusb1-3.4.0-COPYING.txt": (
        "ab15fd526bd8dd18a9e77ebc139656bf4d33e97fc7238cd11bf60e2b9b8666c6"
    ),
    "libusb1-3.4.0-COPYING.LESSER.txt": (
        "dc626520dcd53a22f727af3ee42c770e56c97a64fe3adb063799d8ab032fe551"
    ),
}

MSVC_RUNTIME_FILES = (
    Path("VCRUNTIME140.dll"),
    Path("VCRUNTIME140_1.dll"),
    Path("PyQt5/Qt5/bin/MSVCP140.dll"),
    Path("PyQt5/Qt5/bin/MSVCP140_1.dll"),
    Path("PyQt5/Qt5/bin/VCRUNTIME140.dll"),
    Path("PyQt5/Qt5/bin/VCRUNTIME140_1.dll"),
)
NUITKA_MSVC_RUNTIME_FILES = (
    Path("VCRUNTIME140.dll"),
    Path("VCRUNTIME140_1.dll"),
    Path("MSVCP140.dll"),
    Path("MSVCP140_1.dll"),
)


def _pe_machine(path: Path) -> int | None:
    """Return the PE machine type, or ``None`` for a malformed executable."""
    size = path.stat().st_size
    if size < 70:
        return None
    with path.open("rb") as stream:
        dos_header = stream.read(64)
        if dos_header[:2] != b"MZ":
            return None
        pe_offset = int.from_bytes(dos_header[0x3C:0x40], "little")
        if pe_offset < 64 or pe_offset > size - 6:
            return None
        stream.seek(pe_offset)
        if stream.read(4) != b"PE\x00\x00":
            return None
        return int.from_bytes(stream.read(2), "little")


def _payload(path: Path) -> bytes:
    return bytes.fromhex("".join(path.read_text(encoding="ascii").split()))


def _libusb_runtime_source() -> Path:
    spec = importlib.util.find_spec("usb1")
    if spec is None or spec.origin is None:
        raise RuntimeError("libusb1 build dependency is unavailable")
    path = Path(spec.origin).with_name("libusb-1.0.dll")
    if not path.is_file():
        raise RuntimeError("libusb1 build dependency has no Windows runtime DLL")
    return path


def _verify_license_inventory(app_dir: Path) -> dict[str, str]:
    """Verify the exact public license texts shipped with the frozen runtime."""
    license_dir = Path(app_dir) / "THIRD_PARTY_LICENSES"
    if not license_dir.is_dir():
        raise RuntimeError("third-party license directory is missing from package root")

    verified: dict[str, str] = {}
    for filename, expected_digest in REQUIRED_LICENSE_FILES.items():
        path = license_dir / filename
        if not path.is_file():
            raise RuntimeError(f"required third-party license text is missing: {filename}")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != expected_digest:
            raise RuntimeError(f"third-party license text does not match tracked source: {filename}")
        verified[filename] = digest
    return verified


def _pyinstaller_msvc_runtime_sources() -> dict[Path, tuple[Path, str]]:
    """The exact dependency files PyInstaller is expected to copy unchanged."""
    pyqt_spec = importlib.util.find_spec("PyQt5")
    if pyqt_spec is None or not pyqt_spec.submodule_search_locations:
        raise RuntimeError("PyQt5 build dependency is unavailable for runtime verification")
    pyqt_root = Path(next(iter(pyqt_spec.submodule_search_locations)))
    python_root = Path(sys.base_prefix)
    return {
        MSVC_RUNTIME_FILES[0]: (
            python_root / MSVC_RUNTIME_FILES[0], "CPython 3.14.6"),
        MSVC_RUNTIME_FILES[1]: (
            python_root / MSVC_RUNTIME_FILES[1], "CPython 3.14.6"),
        **{
            relative: (pyqt_root / relative.relative_to("PyQt5"), "PyQt5-Qt5 5.15.2")
            for relative in MSVC_RUNTIME_FILES[2:]
        },
    }


def _nuitka_msvc_runtime_sources(
        content: Path) -> dict[Path, tuple[Path, str]]:
    """Resolve the unchanged CPython/MSVC redistributables selected by Nuitka."""
    python_root = Path(sys.base_prefix)
    sources: dict[Path, tuple[Path, str]] = {
        NUITKA_MSVC_RUNTIME_FILES[0]: (
            python_root / NUITKA_MSVC_RUNTIME_FILES[0], "CPython 3.14.6"),
        NUITKA_MSVC_RUNTIME_FILES[1]: (
            python_root / NUITKA_MSVC_RUNTIME_FILES[1], "CPython 3.14.6"),
    }
    program_files_x86 = os.environ.get("ProgramFiles(x86)")
    if not program_files_x86:
        raise RuntimeError("ProgramFiles(x86) is unavailable for Nuitka runtime verification")
    redist_root = (
        Path(program_files_x86)
        / "Microsoft Visual Studio" / "2022" / "BuildTools" / "VC" / "Redist" / "MSVC"
    )
    for relative in NUITKA_MSVC_RUNTIME_FILES[2:]:
        packaged = content / relative
        if not packaged.is_file():
            raise RuntimeError(f"packaged VC++ runtime file is missing: {relative.as_posix()}")
        packaged_digest = hashlib.sha256(packaged.read_bytes()).hexdigest()
        candidates = sorted(
            redist_root.glob(f"*/x64/Microsoft.VC143.CRT/{relative.name}"),
            reverse=True,
        )
        source = next(
            (
                candidate for candidate in candidates
                if hashlib.sha256(candidate.read_bytes()).hexdigest() == packaged_digest
            ),
            None,
        )
        if source is None:
            raise RuntimeError(
                "packaged VC++ runtime file does not match an installed MSVC "
                f"redistributable: {relative.as_posix()}"
            )
        sources[relative] = (source, f"MSVC {source.parents[2].name} redistributable")
    return sources


def _msvc_runtime_sources(
        backend: str, content: Path) -> dict[Path, tuple[Path, str]]:
    if backend == "nuitka":
        return _nuitka_msvc_runtime_sources(content)
    return _pyinstaller_msvc_runtime_sources()


def _verify_msvc_runtime(
        content: Path, *, backend: str = "pyinstaller") -> dict[str, dict[str, str | int]]:
    """Prove every packaged VC++ runtime file is an unchanged dependency copy."""
    verified: dict[str, dict[str, str | int]] = {}
    for relative, (source, origin) in _msvc_runtime_sources(backend, content).items():
        packaged = content / relative
        if not source.is_file():
            raise RuntimeError(f"VC++ runtime source dependency is missing: {source}")
        if not packaged.is_file():
            raise RuntimeError(f"packaged VC++ runtime file is missing: {relative.as_posix()}")
        source_digest = hashlib.sha256(source.read_bytes()).hexdigest()
        packaged_digest = hashlib.sha256(packaged.read_bytes()).hexdigest()
        if packaged_digest != source_digest:
            raise RuntimeError(
                "packaged VC++ runtime file does not match its unmodified build dependency: "
                + relative.as_posix()
            )
        verified[relative.as_posix()] = {
            "source": origin,
            "bytes": packaged.stat().st_size,
            "sha256": packaged_digest,
        }
    return verified


def _verify_release_metadata(
        app_dir: Path,
        msvc_runtime: dict[str, dict[str, str | int]],
        *,
        backend: str,
) -> None:
    metadata_path = app_dir / "RELEASE-METADATA.json"
    if not metadata_path.exists():
        return
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError("release metadata is unreadable") from error
    if metadata.get("vc_runtime_deployment") != "application-local":
        raise RuntimeError("release metadata has the wrong VC++ runtime deployment mode")
    if metadata.get("vc_runtime_files_unmodified") is not True:
        raise RuntimeError("release metadata does not affirm unmodified VC++ runtime files")
    if metadata.get("vc_runtime_files") != msvc_runtime:
        raise RuntimeError("release metadata VC++ runtime hashes do not match the package")
    if metadata.get("build_backend") != backend:
        raise RuntimeError("release metadata has the wrong build backend")
    if metadata.get("calibration_definitions_bundled") is not True:
        raise RuntimeError("release metadata omits the bundled patch definition")


def verify_distribution(app_dir: Path, *, expected_backend: str | None = None) -> dict:
    app_dir = Path(app_dir).resolve()
    executable = app_dir / f"{APP_NAME}.exe"
    if not executable.is_file():
        raise RuntimeError(f"missing or invalid Windows executable: {executable}")
    machine = _pe_machine(executable)
    if machine is None:
        raise RuntimeError(f"missing or invalid Windows executable: {executable}")
    if machine != PE_MACHINE_AMD64:
        raise RuntimeError(
            f"Windows executable is not x64 (PE machine 0x{machine:04X}): {executable}")

    content = app_dir / "_internal"
    backend = "pyinstaller"
    if not content.is_dir():
        content = app_dir
        backend = "nuitka"
    if expected_backend is not None and backend != expected_backend:
        raise RuntimeError(
            f"package backend is {backend}, expected {expected_backend}"
        )

    required = (
        app_dir / "README.md",
        app_dir / "LICENSE.txt",
        app_dir / "RELEASE_NOTES.md",
        app_dir / "THIRD_PARTY_NOTICES.md",
        app_dir / "BimmerStein-ECU-Tool-User-Manual.pdf",
        app_dir / PATCH_DEFINITION_NAME,
        content / "assets" / "bimmerstein_ecu_tool.png",
        content / "assets" / "bimmerstein_ecu_tool.ico",
        content / "python314.dll",
        content / "libcrypto-3.dll",
        content / "libssl-3.dll",
        content / "libffi-8.dll",
        content / "VCRUNTIME140.dll",
        content / "VCRUNTIME140_1.dll",
        content / "usb1" / "libusb-1.0.dll",
        content / "engines" / "softbsl" / "agent.hex",
        content / "engines" / "softbsl" / "agent_28f.hex",
        content / "engines" / "softbsl" / "eeprom_agent.hex",
        content / "engines" / "softbsl" / "stage1_payload.hex",
        content / "engines" / "softbsl" / "stage1_manifest.json",
        content / "engines" / "softbsl" / "agent_manifest.json",
        content / "engines" / "patcher" / "patches" / "softbsl_loader.json",
    )
    if backend == "pyinstaller":
        required += (
            content / "PyQt5" / "Qt5" / "bin" / "Qt5Core.dll",
        )
    else:
        required += (
            content / "qt5core.dll",
            content / "PyQt5" / "qt-plugins" / "platforms" / "qwindows.dll",
        )
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise RuntimeError("packaged runtime files are missing:\n" + "\n".join(missing))

    libusb_runtime = content / "usb1" / "libusb-1.0.dll"
    if _pe_machine(libusb_runtime) != PE_MACHINE_AMD64:
        raise RuntimeError(f"bundled libusb runtime is not x64: {libusb_runtime}")
    if libusb_runtime.read_bytes() != _libusb_runtime_source().read_bytes():
        raise RuntimeError(
            "bundled libusb runtime does not match its unmodified build dependency")

    verified_msvc_runtime = _verify_msvc_runtime(content, backend=backend)
    _verify_release_metadata(app_dir, verified_msvc_runtime, backend=backend)

    for forbidden in ("_private", "tests", "docs", "defs", "definitions"):
        if (content / forbidden).exists() or (app_dir / forbidden).exists():
            raise RuntimeError(f"private/generated directory leaked into package: {forbidden}")

    # ``backups`` and ``logs`` are created beside the portable executable at
    # runtime. A distributable package must not contain either user-data tree.
    for forbidden in ("backups", "logs"):
        if (content / forbidden).exists() or (app_dir / forbidden).exists():
            raise RuntimeError(f"generated directory leaked into package: {forbidden}")

    leaked_bins = [
        str(path) for path in app_dir.rglob("*.bin")
        if path.is_file()
    ]
    if leaked_bins:
        raise RuntimeError("private ECU image leaked into package:\n" + "\n".join(leaked_bins))

    release_readme = (app_dir / "README.md").read_text(encoding="utf-8")
    source_only_references = (
        "manual/USER_MANUAL.md",
        "BUILDING.md",
        "output/pdf/BimmerStein-ECU-Tool-User-Manual.pdf",
        "](LICENSE)",
        "## Run from source",
        "## Verify and build",
        "## Project layout",
    )
    leaked_references = [
        reference for reference in source_only_references if reference in release_readme
    ]
    if leaked_references:
        raise RuntimeError(
            "source-only README links leaked into the portable package: "
            + ", ".join(leaked_references)
        )
    expected_icon_reference = (
        "_internal/assets/bimmerstein_ecu_tool.png"
        if backend == "pyinstaller"
        else "assets/bimmerstein_ecu_tool.png"
    )
    if expected_icon_reference not in release_readme:
        raise RuntimeError("portable README does not reference its packaged application icon")

    patch_definition = app_dir / PATCH_DEFINITION_NAME
    tracked_definition = (
        ROOT / "engines" / "patcher" / "romraider" / PATCH_DEFINITION_NAME
    )
    if patch_definition.read_bytes() != tracked_definition.read_bytes():
        raise RuntimeError("bundled patch definition does not match tracked source")
    try:
        ET.parse(patch_definition)
    except ET.ParseError as error:
        raise RuntimeError("bundled patch definition is invalid XML") from error

    verified_licenses = _verify_license_inventory(app_dir)
    if content != app_dir and (content / "THIRD_PARTY_LICENSES").exists():
        raise RuntimeError("third-party license inventory must be public at package root")
    notices = (app_dir / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    missing_license_references = [
        filename for filename in REQUIRED_LICENSE_FILES
        if f"THIRD_PARTY_LICENSES/{filename}" not in notices
    ]
    if missing_license_references:
        raise RuntimeError(
            "third-party notices omit bundled license files: "
            + ", ".join(missing_license_references)
        )

    manifest_path = content / "engines" / "softbsl" / "agent_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    verified_agents = {}
    for family, entry in manifest["agents"].items():
        payload = _payload(manifest_path.parent / entry["payload"])
        digest = hashlib.sha256(payload).hexdigest()
        if len(payload) != entry["payload_size"] or digest != entry["payload_sha256"]:
            raise RuntimeError(f"packaged {family} RAM agent does not match its manifest")
        verified_agents[family] = {"bytes": len(payload), "sha256": digest}

    patch_dir = content / "engines" / "patcher" / "patches"
    patch_ids = {
        json.loads(path.read_text(encoding="utf-8"))["id"]
        for path in patch_dir.glob("*.json")
    }
    missing_patches = sorted(REQUIRED_PATCH_IDS - patch_ids)
    if missing_patches:
        raise RuntimeError(f"required packaged patches are missing: {missing_patches}")

    return {
        "application": str(app_dir),
        "build_backend": backend,
        "patch_definition": PATCH_DEFINITION_NAME,
        "pe_machine": f"0x{machine:04X}",
        "executable_bytes": executable.stat().st_size,
        "patch_count": len(patch_ids),
        "agents": verified_agents,
        "licenses": verified_licenses,
        "msvc_runtime": verified_msvc_runtime,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--backend",
        choices=("pyinstaller", "nuitka"),
        help="require the package to match this frozen backend",
    )
    parser.add_argument(
        "app_dir",
        nargs="?",
        type=Path,
        default=ROOT / "dist" / APP_NAME,
    )
    args = parser.parse_args()
    result = verify_distribution(args.app_dir, expected_backend=args.backend)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
