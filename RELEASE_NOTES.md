# BimmerStein ECU Tool Release Notes

## 0.1.0 Beta 12

This release corrects MS41 transmission control-bit handling.

**OFF-ROAD, COMPETITION, RESEARCH, AND BENCH USE ONLY.** Do not use this software
to modify a vehicle operated on public roads. The user is responsible for
compliance with applicable emissions, safety, registration, and other laws.

### Changes

- Stock MS41.0 Byte 5 value `0xAC` now decodes as `AT/MT (auto)` instead of
  `Custom (0x2C)`.
- Transmission changes now modify only Byte 5 bits 0-5. Unrelated bit 6 and
  knock-detection bit 7 are preserved exactly.
- Added exhaustive regression coverage across every Byte 5 value and every
  supported transmission selection.

**IGNITION CUT HAZARD:** Ignition Cut V7 is in a very early stage. It will cause
fuel-related, misfire, and coil-related DTCs and fuel-trim issues, and the cut is
extremely aggressive. Never use it on a car with catalytic converters; unburned
fuel can destroy them.

### Distribution status

Version `0.1.0b12` is distributed under GNU GPL version 3 (`GPL-3.0-only`) using
the GPLv3 PyQt5 distribution path. PyInstaller and Nuitka Windows x64 builds are
provided as per-user installers and portable ZIPs. Every package includes the
user manual, third-party license inventory, release metadata, unchanged
application-local Visual C++ runtime files, and
`BimmerStein MS41 Patch Definitions.xml` beside the executable. That definition
includes the supported Ignition Cut and Launch Control tuning entries. SHA-256
manifests cover every portable ZIP and installer.
