# Building BimmerStein ECU Tool

Beta 17 uses Nuitka for Windows x64 and x86 installers and portable packages.
The public project is `GPL-3.0-only` and is marked **OFF-ROAD USE ONLY**.

## Build environments

Use CPython 3.14.6 and the MSVC build tools. Install `requirements-build.txt`
in a virtual environment for each architecture. The Python interpreter must
match the intended package architecture. The x86 build needs the x86 compiler
and dependencies; running an x64 interpreter cannot produce an x86 application.

```powershell
.venv\Scripts\python.exe -m pip install -r requirements-build.txt
```

The tracked runtime license inventory matches CPython 3.14.6, Qt 5.15.2,
PyQt5-sip 12.18.0 and Nuitka 4.1.3. Update notices and verifier hashes before
changing those versions. Required dependency licenses must remain in packages.
Do not bundle the FTDI D2XX DLL. Application-local Microsoft runtime files must
remain unchanged; verification compares them with their build dependencies.

## Validation and documentation

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
.venv\Scripts\python.exe -m ruff check . --select F,E9
.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp .tmp\pytest-release
.venv\Scripts\python.exe -m engines.softbsl.verify_agent_artifacts
.venv\Scripts\python.exe packaging\capture_manual_screenshots.py
.venv\Scripts\python.exe packaging\build_user_manual.py
```

The release script also requires the owner's exact-byte execution admission
against the private reference images. This gate runs even with `-SkipTests`.
Those inputs are not shipped. Offline checks do not establish physical ECU or
on-car behavior. Render and inspect every manual PDF page before publication.

## Public firmware policy

Keep Ignition Cut V7 and Launch Control V4/V5 in public releases until the
release owner confirms the replacements work on a vehicle. Development revisions
remain in the development checkout. Prepare the public snapshot from the last
public release, carrying over the reviewed application and AMD flashing fixes.
Compare the retained patch descriptors and tuning definitions byte-for-byte
with that public baseline before building.

## Prepare Beta 17

Build from a clean, reviewed source checkout. Release metadata records the
source commit and must show `source_dirty: false`. Keep private projects,
reference images and user data outside the source snapshot.

```powershell
.\packaging\prepare_release.ps1 -Version 0.1.0b17 -PyQtLicenseBasis GPLv3
```

For both architectures, pass the path to a prepared 32-bit environment:

```powershell
.\packaging\prepare_release.ps1 -Version 0.1.0b17 -PyQtLicenseBasis GPLv3 `
    -Architectures x64,x86 -PythonX86Path '<x86-venv>\Scripts\python.exe'
```

Use `-PythonPath` to select a different x64 environment, and `-IsccPath` if
Inno Setup is not in its usual installation location. `-SkipInstaller` produces
portable packages only. To build a single application folder, use
`build_windows_nuitka.ps1 -Version 0.1.0b17 -Architecture x64`; the old
`build_windows.ps1` command forwards to the same build.

Application folders are written to `dist\x64\BimmerStein ECU Tool` and
`dist\x86\BimmerStein ECU Tool`. The complete folders are required at runtime.
Versioned ZIPs, installers, metadata and SHA-256 manifests are written under
`release\`. Downloads include the architecture; folders and shortcuts use
**BimmerStein ECU Tool**. Updates retain an existing installation location.

Verify each portable package and installer by starting the app, then installing,
starting and uninstalling in an isolated test directory. Check archive CRCs,
manifest hashes, native runtime architecture, public content and required notices.

The intended tag is `v0.1.0b17`. Preparation does not create a tag, commit, push
or publish. Publication requires the release owner's final review.
