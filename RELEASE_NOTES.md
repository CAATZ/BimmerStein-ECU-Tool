# BimmerStein ECU Tool Release Notes

## 0.1.0 Beta 8

This release hardens stock-DS2 programming and mixed-family image handling,
and completes the patch-tab ignition-cycle workflow.

**OFF-ROAD, COMPETITION, RESEARCH, AND BENCH USE ONLY.** Do not use this software
to modify a vehicle operated on public roads. The user is responsible for
compliance with applicable emissions, safety, registration, and other laws.

### Changes

- Increased native DS2 program payloads to 243 data bytes on both supported
  flash families, including exact final-frame handling.
- Normalized mixed coding-family images before partial, full, conversion, and
  offline-merge writes, with the required checksum updates.
- Made retained-session recovery phase-aware so unsafe retry paths are not
  offered after finalization; slow DS2 and bench recovery remain explicit.
- Added the same disconnect and ignition-cycle handoff after a successful patch
  flash that is used by other disruptive write operations.
- Marked AlphaN MAF-failsafe as untested in the patch catalog and documentation.
- Unified the installed application, Start Menu, and desktop shortcut names
  across both Windows packages.

### Distribution status

Version `0.1.0b8` is distributed under GNU GPL version 3 (`GPL-3.0-only`) using
the GPLv3 PyQt5 distribution path. PyInstaller and Nuitka Windows x64 builds are
provided as per-user installers and portable ZIPs. Every package includes the
user manual, third-party license inventory, release metadata, unchanged
application-local Visual C++ runtime files, and
`BimmerStein MS41 Patch Definitions.xml` beside the executable. That definition
includes the supported Ignition Cut and Launch Control tuning entries. SHA-256
manifests cover every portable ZIP and installer.
