#!/usr/bin/env python3
"""Generate PyInstaller Windows version metadata for BimmerStein ECU Tool."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "build" / "windows_version_info.txt"
VERSION_PATTERN = re.compile(
    r"^(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)"
    r"(?:(?P<beta>b[1-9]\d*)|-(?P<prerelease>[0-9A-Za-z.-]+))?"
    r"(?:\+[0-9A-Za-z.-]+)?$"
)


def _numeric_version(version: str) -> tuple[int, int, int, int]:
    match = VERSION_PATTERN.fullmatch(version)
    if match is None:
        raise ValueError(
            "version must use BimmerStein release form "
            f"(for example 1.2.0 or 1.2.0b1): {version}"
        )
    return (
        int(match.group("major")),
        int(match.group("minor")),
        int(match.group("patch")),
        0,
    )


def render_version_info(version: str) -> str:
    numeric = _numeric_version(version)
    numeric_text = ", ".join(str(part) for part in numeric)
    return f"""# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({numeric_text}),
    prodvers=({numeric_text}),
    mask=0x3F,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        '040904B0',
        [
          StringStruct('CompanyName', 'CAATZ'),
          StringStruct('FileDescription', 'BimmerStein ECU Tool'),
          StringStruct('FileVersion', '{version}'),
          StringStruct('InternalName', 'BimmerSteinECUTool'),
          StringStruct('LegalCopyright', 'Copyright (c) 2026 CAATZ'),
          StringStruct('OriginalFilename', 'BimmerStein ECU Tool.exe'),
          StringStruct('ProductName', 'BimmerStein ECU Tool'),
          StringStruct('ProductVersion', '{version}')
        ]
      )
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--version",
        default=os.environ.get("BIMMERSTEIN_VERSION", "0.0.0-dev"),
        help="BimmerStein release version written into the executable",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_version_info(args.version), encoding="utf-8", newline="\n")
    print(f"Windows version metadata written: {output} ({args.version})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
