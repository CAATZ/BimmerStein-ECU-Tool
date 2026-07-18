# Building BimmerStein ECU Tool

The supported release build is a Windows x64 PyInstaller one-folder package.
All commands below run from the repository root in PowerShell.

## 1. Create the build environment

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements-build.txt
```

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
and PyInstaller 6.21.0. If any of those versions or the Python-carried OpenSSL
or libffi libraries change, update the source license texts, hashes in
`packaging/verify_dist.py`, and `THIRD_PARTY_NOTICES.md` before building a
release. The package verifier rejects missing or altered tracked texts.

Publication still requires the release owner's final package review.

## 6. Prepare a versioned release archive

After the release owner has selected a version, create the final ZIP with the
GPLv3 licensing gate selected for the public beta:

Public beta versions use the same compact `bN` suffix as BimmerStein Tuning
Suite. The first beta is `0.1.0b1`, with Git tag `v0.1.0b1`.

```powershell
.\packaging\prepare_release.ps1 `
    -Version 0.1.0b1 `
    -PyQtLicenseBasis GPLv3
```

The `Commercial` option is reserved for a future distribution whose application
code and dependencies have been separately cleared for proprietary release. The
script performs a fresh build, verifies the staged x64 package, records the
project license and selected PyQt5 basis in `RELEASE-METADATA.json`, and writes
both the ZIP and a SHA-256 file under `release\`.

This script does not commit, push, tag, or publish anything.
