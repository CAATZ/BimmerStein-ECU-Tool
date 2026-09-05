#!/usr/bin/env python3
"""Verify the Windows onedir package without opening an ECU connection."""

from __future__ import annotations

import argparse
import ast
import ctypes
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
APP_NAME = "BimmerStein ECU Tool"
PATCH_DEFINITION_NAME = "BimmerStein MS41 Patch Definitions.xml"
LOGGER_DEFINITION_NAME = "BimmerStein MS41 Logger Definitions.xml"
PROHIBITED_PUBLIC_TERMS = tuple("".join(parts) for parts in (
    ("artificial", " intelligence"),
    ("chat", "gpt"),
    ("chip", "ster"),
    ("clau", "de"),
    ("co", "dex"),
    ("co", "pilot"),
    ("edia", "bas"),
    ("ews", "sync"),
    ("gall", "etto"),
    ("gall", "eto"),
    ("in", "pa"),
    ("is", "ta"),
    ("open", "a", "i"),
    ("quick", "flash"),
))
_PROHIBITED_PUBLIC_PATTERN = re.compile(
    rb"(?<![a-z0-9])(?:"
    + b"|".join(re.escape(term.encode()) for term in PROHIBITED_PUBLIC_TERMS)
    + rb")(?![a-z0-9])",
    re.IGNORECASE,
)
_PROHIBITED_PUBLIC_TEXT_PATTERN = re.compile(
    rb"(?<![A-Za-z0-9])" + b"".join((b"A", b"I")) + rb"(?![A-Za-z0-9])"
)
_PRIVATE_PATCH_PATTERN = re.compile(
    rb"(?<![a-z0-9])(?:"
    rb"ignition[ _-]?cut[ _-]?v(?:8|9)|"
    rb"launch[ _-]?(?:control[ _-]?)?v(?:6|7)|"
    rb"(?:cut|lc)[ _-]?(?:hyst|ipw)|"
    rb"ignition[ _-]?hysteresis|fixed[ _-]?ipw"
    rb")(?![a-z0-9])",
    re.IGNORECASE,
)
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


class _VSFixedFileInfo(ctypes.Structure):
    _fields_ = [
        ("signature", ctypes.c_uint32),
        ("structure_version", ctypes.c_uint32),
        ("file_version_ms", ctypes.c_uint32),
        ("file_version_ls", ctypes.c_uint32),
        ("product_version_ms", ctypes.c_uint32),
        ("product_version_ls", ctypes.c_uint32),
        ("file_flags_mask", ctypes.c_uint32),
        ("file_flags", ctypes.c_uint32),
        ("file_os", ctypes.c_uint32),
        ("file_type", ctypes.c_uint32),
        ("file_subtype", ctypes.c_uint32),
        ("file_date_ms", ctypes.c_uint32),
        ("file_date_ls", ctypes.c_uint32),
    ]


def _version_tuple(ms: int, ls: int) -> tuple[int, int, int, int]:
    return ms >> 16, ms & 0xFFFF, ls >> 16, ls & 0xFFFF


def _fixed_file_versions(path: Path) -> tuple[
        tuple[int, int, int, int], tuple[int, int, int, int]]:
    """Read the numeric file and product versions from a Windows PE resource."""
    if os.name != "nt":
        raise RuntimeError("Windows version-resource verification requires Windows")
    library = ctypes.WinDLL("version", use_last_error=True)
    handle = ctypes.c_uint32()
    size = library.GetFileVersionInfoSizeW(str(path), ctypes.byref(handle))
    if not size:
        raise RuntimeError(f"Windows executable has no version resource: {path}")
    buffer = ctypes.create_string_buffer(size)
    if not library.GetFileVersionInfoW(str(path), 0, size, buffer):
        raise RuntimeError(f"Windows executable version resource is unreadable: {path}")
    value = ctypes.c_void_p()
    length = ctypes.c_uint()
    if not library.VerQueryValueW(buffer, "\\", ctypes.byref(value), ctypes.byref(length)):
        raise RuntimeError(f"Windows executable fixed version is missing: {path}")
    info = ctypes.cast(value, ctypes.POINTER(_VSFixedFileInfo)).contents
    if info.signature != 0xFEEF04BD:
        raise RuntimeError(f"Windows executable fixed version is invalid: {path}")
    return (
        _version_tuple(info.file_version_ms, info.file_version_ls),
        _version_tuple(info.product_version_ms, info.product_version_ls),
    )


def _numeric_release_version(version: str) -> tuple[int, int, int, int]:
    match = re.fullmatch(
        r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
        r"(?:b([1-9]\d*)|-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?",
        version,
    )
    if match is None:
        raise RuntimeError(f"invalid expected release version: {version}")
    return tuple(int(part or 0) for part in match.groups()[:4])


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
        expected_version: str | None = None,
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
    if expected_version is not None and metadata.get("version") != expected_version:
        raise RuntimeError("release metadata has the wrong version")
    if metadata.get("calibration_definitions_bundled") is not True:
        raise RuntimeError("release metadata omits the bundled patch definition")


def _verify_document_version(path: Path, expected_version: str) -> None:
    if expected_version not in path.read_text(encoding="utf-8"):
        raise RuntimeError(
            f"packaged document does not identify {expected_version}: {path.name}")


def _verify_patch_tree(packaged: Path) -> int:
    source = ROOT / "engines" / "patcher" / "patches"
    source_files = {
        path.relative_to(source): path for path in source.rglob("*") if path.is_file()
    }
    packaged_files = {
        path.relative_to(packaged): path for path in packaged.rglob("*") if path.is_file()
    }
    private_names = sorted(
        path.as_posix() for path in source_files
        if _PRIVATE_PATCH_PATTERN.search(path.as_posix().encode())
    )
    if private_names:
        raise RuntimeError(
            "private firmware revision entered the release patch tree: "
            + ", ".join(private_names)
        )
    if source_files.keys() != packaged_files.keys():
        missing = sorted(path.as_posix() for path in source_files.keys() - packaged_files.keys())
        unexpected = sorted(path.as_posix() for path in packaged_files.keys() - source_files.keys())
        raise RuntimeError(
            f"packaged patch inventory differs from tracked source; "
            f"missing={missing}, unexpected={unexpected}")
    for relative, source_path in source_files.items():
        if packaged_files[relative].read_bytes() != source_path.read_bytes():
            raise RuntimeError(
                f"packaged patch does not match tracked source: {relative.as_posix()}")
    return len(source_files)


def _verify_public_terms(paths) -> None:
    for path in paths:
        payload = path.read_bytes()
        if path.suffix.lower() in {".py", ".pyw", ".spec"}:
            payload += b"\n" + b"\n".join(
                node.value.encode("utf-8", errors="backslashreplace")
                for node in ast.walk(ast.parse(payload, filename=str(path)))
                if isinstance(node, ast.Constant) and isinstance(node.value, str)
            )
        hits = sorted({
            match.group().decode("ascii", errors="replace").lower()
            for match in _PROHIBITED_PUBLIC_PATTERN.finditer(payload)
        })
        if path.suffix.lower() in {".json", ".md", ".txt", ".xml"}:
            hits.extend(
                match.group().decode("ascii", errors="replace").lower()
                for match in _PROHIBITED_PUBLIC_TEXT_PATTERN.finditer(payload)
            )
        hits.extend(
            match.group().decode("ascii", errors="replace").lower()
            for match in _PRIVATE_PATCH_PATTERN.finditer(payload)
        )
        if hits:
            raise RuntimeError(
                f"prohibited public reference in {path.name}: {', '.join(hits)}")


def verify_public_source() -> dict:
    """Reject private inputs before either frozen backend is built."""
    for forbidden in ("android", "_private"):
        if (ROOT / forbidden).exists():
            raise RuntimeError(f"private source directory entered release source: {forbidden}")

    patcher = ROOT / "engines" / "patcher"
    private_paths = sorted(
        path.relative_to(ROOT).as_posix()
        for path in patcher.rglob("*")
        if path.is_file()
        and _PRIVATE_PATCH_PATTERN.search(path.name.encode())
    )
    if private_paths:
        raise RuntimeError(
            "private firmware revision entered release source: "
            + ", ".join(private_paths)
        )

    romraider = patcher / "romraider"
    inputs = [
        ROOT / "gui.py",
        ROOT / "patch_service.py",
        ROOT / "live_data.py",
        ROOT / "README.md",
        ROOT / "CHANGELOG.md",
        ROOT / "RELEASE_NOTES.md",
        ROOT / "manual" / "USER_MANUAL.md",
        ROOT / "THIRD_PARTY_NOTICES.md",
        ROOT / "logger_definitions" / LOGGER_DEFINITION_NAME,
        ROOT / "packaging" / "capture_manual_screenshots.py",
        romraider / "README.md",
        romraider / "build_patch_definitions.py",
        *romraider.glob("*.xml"),
        *(patcher / "patches").glob("*.json"),
    ]
    missing = [str(path) for path in inputs if not path.is_file()]
    if missing:
        raise RuntimeError("public release input is missing:\n" + "\n".join(missing))
    # Scan production sources and documentation, keeping dependency licenses intact.
    excluded = {
        ".git", ".venv", ".tmp", "__pycache__", "build", "dist", "release",
        "output", "backups", "logs", "tests", "THIRD_PARTY_LICENSES",
    }
    text_extensions = {
        ".py", ".pyw", ".spec", ".ps1", ".bat", ".cmd", ".md", ".txt",
        ".xml", ".json", ".asm", ".c", ".h", ".toml", ".ini", ".yml",
        ".yaml", ".html", ".svg",
    }
    for directory, names, filenames in os.walk(ROOT, followlinks=False):
        names[:] = [name for name in names if name not in excluded]
        for filename in filenames:
            path = Path(directory) / filename
            if path.suffix.lower() in text_extensions:
                inputs.append(path)
    inputs = sorted(set(inputs))
    _verify_public_terms(inputs)
    for path in inputs:
        if path.suffix.lower() != ".md" or path.name == "THIRD_PARTY_NOTICES.md":
            continue
        if re.search(rb"\bandroid\b", path.read_bytes(), re.IGNORECASE):
            raise RuntimeError(
                "private platform reference in public documentation: "
                + path.relative_to(ROOT).as_posix())
    return {"source_inputs": len(inputs), "private_paths": 0}


def verify_distribution(
        app_dir: Path,
        *,
        expected_backend: str | None = None,
        expected_version: str | None = None,
) -> dict:
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
    if expected_version is not None:
        expected_fixed = _numeric_release_version(expected_version)
        file_version, product_version = _fixed_file_versions(executable)
        if file_version != expected_fixed or product_version != expected_fixed:
            raise RuntimeError(
                "Windows executable fixed version is wrong: "
                f"file={file_version}, product={product_version}, expected={expected_fixed}"
            )

    required = (
        app_dir / "README.md",
        app_dir / "LICENSE.txt",
        app_dir / "RELEASE_NOTES.md",
        app_dir / "THIRD_PARTY_NOTICES.md",
        app_dir / "BimmerStein-ECU-Tool-User-Manual.pdf",
        app_dir / PATCH_DEFINITION_NAME,
        content / "logger_definitions" / LOGGER_DEFINITION_NAME,
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
    _verify_release_metadata(
        app_dir,
        verified_msvc_runtime,
        backend=backend,
        expected_version=expected_version,
    )

    for forbidden in ("_private", "android", "tests", "docs", "defs", "definitions"):
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
    if expected_version is not None:
        _verify_document_version(app_dir / "README.md", expected_version)
        _verify_document_version(app_dir / "RELEASE_NOTES.md", expected_version)
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

    logger_definition = content / "logger_definitions" / LOGGER_DEFINITION_NAME
    tracked_logger_definition = ROOT / "logger_definitions" / LOGGER_DEFINITION_NAME
    if logger_definition.read_bytes() != tracked_logger_definition.read_bytes():
        raise RuntimeError("bundled logger definition does not match tracked source")
    try:
        ET.parse(logger_definition)
    except ET.ParseError as error:
        raise RuntimeError("bundled logger definition is invalid XML") from error

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
    patch_file_count = _verify_patch_tree(patch_dir)
    _verify_public_terms((
        executable,
        app_dir / "README.md",
        app_dir / "RELEASE_NOTES.md",
        app_dir / "THIRD_PARTY_NOTICES.md",
        patch_definition,
        logger_definition,
        *patch_dir.glob("*.json"),
    ))

    return {
        "application": str(app_dir),
        "build_backend": backend,
        "patch_definition": PATCH_DEFINITION_NAME,
        "logger_definition": LOGGER_DEFINITION_NAME,
        "pe_machine": f"0x{machine:04X}",
        "executable_bytes": executable.stat().st_size,
        "patch_count": patch_file_count,
        "version": expected_version,
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
        "--expected-version",
        help="require packaged documents and metadata to identify this version",
    )
    parser.add_argument(
        "--source-only",
        action="store_true",
        help="verify public release inputs without requiring a built application",
    )
    parser.add_argument(
        "app_dir",
        nargs="?",
        type=Path,
        default=ROOT / "dist" / APP_NAME,
    )
    args = parser.parse_args()
    if args.source_only:
        print(json.dumps(verify_public_source(), indent=2))
        return 0
    result = verify_distribution(
        args.app_dir,
        expected_backend=args.backend,
        expected_version=args.expected_version,
    )
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
