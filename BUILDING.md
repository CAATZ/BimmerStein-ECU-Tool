# Building BimmerStein ECU Tool

Beta 19 uses Nuitka for Windows x64 and x86 installers and portable packages.
The public project is `GPL-3.0-only` and is marked **OFF-ROAD USE ONLY**.

## Build environments

Beta 19 release profiles use CPython 3.10.11 for modern Windows and CPython
3.8.10 for Windows 7 SP1, with MinGW and LTO disabled. Use a separate environment
for each operating-system profile and architecture. The default source script
also supports its documented MSVC environment. Install dependencies matching the
selected profile; Python 3.8 requires compatible versions rather than modern pins.
The Python interpreter must match the intended package architecture. The x86 build needs the x86 compiler
and dependencies; running an x64 interpreter cannot produce an x86 application.

```powershell
.venv\Scripts\python.exe -m pip install -r requirements-build.txt
```

Each release profile must carry notices and verified runtime hashes matching
its actual Python, Qt, binding and compiler versions. Update the inventory when
changing a profile. Required dependency licenses must remain in packages.
Do not bundle the FTDI D2XX DLL. Application-local Microsoft runtime files must
remain unchanged; verification compares them with their build dependencies.

## Validation and documentation

Routine development builds run a fixed regression set covering MS41 and
MS42/MS43 transfers, recovery, write finalization, GUI dispatch and packaging.
They do not run every firmware/emulator test. Add tests for the code being
changed, or explicitly request the full qualification:

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
.venv\Scripts\python.exe packaging\check_offline.py
.venv\Scripts\python.exe packaging\check_offline.py tests/test_ms42_ms43_storage_edit.py
.venv\Scripts\python.exe packaging\check_offline.py --full
```

The development check reports whether the saved private MS41 qualification
matches the current firmware, emulator, test and source inputs. A missing or
stale proof is reported as qualification needed; it never starts the long
native gate implicitly. Development checks are not full release qualification.
The fixed selection is in `packaging/check_offline.py`; results replace the
previous `.tmp/offline-checks/summary.json` and `results.xml`. Run one check
command at a time. Test fixtures isolate application data and temporary files.

Agent artifact checks remain mandatory during every build. Regenerate the
manual when its contents or screenshots change:

```powershell
.venv\Scripts\python.exe -m engines.softbsl.verify_agent_artifacts
.venv\Scripts\python.exe packaging\capture_manual_screenshots.py
.venv\Scripts\python.exe packaging\build_user_manual.py
```

The public release script runs the owner's exact-byte execution admission
against private reference images once, then runs the remaining full suite once
before compiling any architecture. It does not rerun the same native admission
through pytest or repeat the full suite for x86. Native admission still runs
with `-SkipTests`.
Those inputs are not shipped. Offline checks do not establish physical ECU or
on-car behavior. Render and inspect every manual PDF page before publication.

## Public firmware policy

Beta 19 includes Ignition Cut V18 and Launch Control V18 on MS41.0/.1/.2/.3,
AMD 29F Flash Driver V5, CalGuard V6, Soft-BSL entry V2 and loader V12.
The four recovery items have **Tested** validation status following owner confirmation;
Ignition Cut and Launch Control V18 remain HIGHLY EXPERIMENTAL and require
vehicle testing. Coding and transmission conversion are HIGHLY EXPERIMENTAL
and not recommended until further testing. Keep all untested patches untested.
Preserve historical descriptors needed for installed-patch detection, migration
and removal. Keep validation
labels tied to the available evidence; bench and offline checks do not establish
vehicle testing for every firmware and flash-chip combination.

## Prepare Beta 19

Build from a clean, reviewed source checkout. Release metadata records the
source commit and must show `source_dirty: false`. Keep private projects,
reference images and user data outside the source snapshot.

The commands below use the default CPython 3.14/MSVC configuration. The
CPython 3.10 and Windows 7 release profiles also require their matching compiler,
compatibility and package-verification adapters; selecting another interpreter
with `-PythonPath` alone does not apply those profiles.

```powershell
.\packaging\prepare_release.ps1 -Version 0.1.0b19 -PyQtLicenseBasis GPLv3
```

For both architectures, pass the path to a prepared 32-bit environment:

```powershell
.\packaging\prepare_release.ps1 -Version 0.1.0b19 -PyQtLicenseBasis GPLv3 `
    -Architectures x64,x86 -PythonX86Path '<x86-venv>\Scripts\python.exe'
```

Use `-PythonPath` to select a different x64 environment, and `-IsccPath` if
Inno Setup is not in its usual installation location. `-SkipInstaller` produces
portable packages only. To build a single application folder, use
`build_windows_nuitka.ps1 -Version 0.1.0b19 -Architecture x64`; the old
`build_windows.ps1` command forwards to the same build.

For a routine build, `-TestPaths tests/test_changed_area.py` extends the fixed
regression set, `-FullTests` explicitly requests all offline tests, and
`-SkipTests` reuses checks already completed for the same source. Agent and
package verification still run with `-SkipTests`. Use `-SkipDocumentation` when
the current manual and icon are already available and unchanged.

Application folders are written to `dist\x64\BimmerStein ECU Tool` and
`dist\x86\BimmerStein ECU Tool`. The complete folders are required at runtime.
Versioned ZIPs, installers, metadata and SHA-256 manifests are written under
`release\`. Downloads include the architecture; folders and shortcuts use
**BimmerStein ECU Tool**. Updates retain an existing installation location.

Verify each portable package and installer by starting the app, then installing,
starting and uninstalling in an isolated test directory. Check archive CRCs,
manifest hashes, native runtime architecture, public content and required notices.

The intended tag is `v0.1.0b19`. Preparation does not create a tag, commit, push
or publish. Publication requires the release owner's final review.

Windows 7 compatibility must be checked on Windows 7 SP1 itself; a successful
launch on newer Windows is not target-operating-system qualification.
