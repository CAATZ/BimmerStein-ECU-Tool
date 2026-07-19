# Building BimmerStein ECU Tool

The supported release build is a Windows x64 PyInstaller one-folder package
distributed as both a portable ZIP and a per-user installer. A separately
labeled experimental Nuitka standalone package can be built as a second option;
PyInstaller remains the recommended release backend.
All commands below run from the repository root in PowerShell.

## 1. Create the build environment

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements-build.txt
```

Install Inno Setup 6 when building the installer EXE. The release scripts find
`ISCC.exe` from `INNO_ISCC`, the standard Program Files locations, or an
explicit `-IsccPath` argument.

`requirements-build.txt` also pins the experimental Nuitka compiler and its
build helpers. The Nuitka path uses MSVC because Python 3.14 is not supported by
Nuitka's MinGW mode.

The FTDI D2XX driver is a system dependency used at runtime when available. It
is not installed or redistributed by this repository.

## 2. Run validation

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
.venv\Scripts\python.exe -m ruff check . --select F,E9
.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp .pytest-release
.venv\Scripts\python.exe -m engines.softbsl.verify_agent_artifacts
```

The RAM-agent verification is mandatory. It confirms that the checked-in HEX
payloads match their manifests and reproducible source artifacts.

## 3. Rebuild the documentation

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
.venv\Scripts\python.exe packaging\capture_manual_screenshots.py
.venv\Scripts\python.exe packaging\build_user_manual.py
```

The screenshot tool uses synthetic in-memory labels only. It does not open a
serial port or read a private ROM. The PDF is written to:

`output\pdf\BimmerStein-ECU-Tool-User-Manual.pdf`

Render and visually inspect every PDF page before publishing a release.

## 4. Build the portable package

```powershell
.\build_windows.ps1
```

The script regenerates metadata-clean icons, rebuilds the manual, runs the
static/test/artifact gates, creates the PyInstaller package, adds the public
release documents and tracked third-party license texts, and validates the
frozen output.

Output:

`dist\BimmerStein ECU Tool\`

Keep the complete folder together. `BimmerStein ECU Tool.exe` depends on the
adjacent `_internal` directory.

To compile the separately labeled experimental Nuitka portable package:

```powershell
.\build_windows_nuitka.ps1 -Version 0.1.0b4
```

Its output is `dist\BimmerStein ECU Tool Nuitka Experimental\`. It is a flat
Nuitka standalone directory and does not use PyInstaller's `_internal` layout.
The package includes `EXPERIMENTAL-NOTICE.txt`; do not rename or present it as
the recommended installer without completing a new release qualification.

## 5. Licensing and publication gate

The public project is licensed under GNU GPL version 3 (`GPL-3.0-only`), and a
frozen public binary also carries the licenses and redistribution conditions of
its dependencies.

The public package is marked **OFF-ROAD USE ONLY** in its application UI,
documentation, and release metadata.
Review `THIRD_PARTY_NOTICES.md` before public distribution. In particular:

- Record the GPLv3 PyQt5 license path selected for the public beta.
- Keep the application-local Microsoft Visual C++ runtime files unchanged. The
  package verifier compares them byte-for-byte with the CPython and PyQt5-Qt5
  build dependencies and the release metadata records their SHA-256 hashes.
- Do not bundle FTDI's D2XX DLL unless its redistribution terms have been
  reviewed and accepted separately.

The tracked `THIRD_PARTY_LICENSES/` inventory matches the current Windows
release toolchain: CPython 3.14.6, Qt 5.15.2, PyQt5-sip 12.18.0, pyserial 3.5,
PyInstaller 6.21.0, and the Nuitka 4.1.3 runtime exception. If any of those
versions or the Python-carried OpenSSL or libffi libraries change, update the
source license texts, hashes in `packaging/verify_dist.py`, and
`THIRD_PARTY_NOTICES.md` before building a release. The package verifier rejects
missing or altered tracked texts.

Publication still requires the release owner's final package review.

## 6. Prepare the versioned release artifacts

After the release owner has selected a version, create the final ZIP with the
GPLv3 licensing gate selected for the public beta:

Beta versions use the same compact `bN` suffix as BimmerStein Tuning Suite.
The current beta is `0.1.0b4`, with Git tag `v0.1.0b4`.

```powershell
.\packaging\prepare_release.ps1 `
    -Version 0.1.0b4 `
    -PyQtLicenseBasis GPLv3 `
    -IncludeExperimentalNuitka `
    -IsccPath "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
```

The `Commercial` option is reserved for a future distribution whose application
code and dependencies have been separately cleared for proprietary release. The
script performs a fresh build, verifies every staged x64 package, records the
backend, experimental status, project license, and selected PyQt5 basis in
`RELEASE-METADATA.json`, and writes the portable ZIPs, per-user installer EXEs,
individual checksum files, and one complete `SHA256SUMS.txt` under `release\`.

The regular and experimental installers use distinct product identities and
installation directories so they can coexist. Both use the BimmerStein icon,
install under the current user's local application-data folder without
requiring administrator access, create a Start Menu shortcut, and offer an
optional desktop shortcut. The Nuitka filenames, installer UI, install folder,
and package notice all say **Nuitka Experimental**. Use `-SkipInstaller` only
when intentionally preparing a portable-only build.

This script does not commit, push, tag, or publish anything.
