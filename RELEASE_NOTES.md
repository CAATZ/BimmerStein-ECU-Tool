# BimmerStein ECU Tool Release Notes

## 0.1.0 Beta 7

This maintenance release corrects the EWS generation named by the alignment
workflow and refreshes the project presentation.

**OFF-ROAD, COMPETITION, RESEARCH, AND BENCH USE ONLY.** Do not use this software
to modify a vehicle operated on public roads. The user is responsible for
compliance with applicable emissions, safety, registration, and other laws.

### Changes

- Corrected the EWS alignment generation label to `EWS2` throughout the UI,
  implementation documentation, and user manual. This is a terminology correction only; the
  validated DS2 frames, module address, command, and safety checks are unchanged.
- Aligned the README hero, product subtitle, platform badges, and release links
  with the BimmerStein presentation.

### Distribution status

Version `0.1.0b7` is distributed under GNU GPL version 3 (`GPL-3.0-only`) using
the GPLv3 PyQt5 distribution path. PyInstaller and Nuitka Windows x64 builds are
provided as per-user installers and portable ZIPs. Every package includes the
user manual, third-party license inventory, release metadata, unchanged
application-local Visual C++ runtime files, and
`BimmerStein MS41 Patch Definitions.xml` beside the executable. That definition
includes the supported Ignition Cut and Launch Control tuning entries. SHA-256
manifests cover every portable ZIP and installer.
