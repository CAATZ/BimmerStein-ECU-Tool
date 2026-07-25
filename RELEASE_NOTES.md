# BimmerStein ECU Tool Release Notes

## 0.1.0 Beta 11

This release expands recovery, adaptations, and firmware-compatibility handling
across the supported MS41 variants.

**OFF-ROAD, COMPETITION, RESEARCH, AND BENCH USE ONLY.** Do not use this software
to modify a vehicle operated on public roads. The user is responsible for
compliance with applicable emissions, safety, registration, and other laws.

### Changes

- Added automatic and operator-forced Soft-BSL recovery routing alongside the
  existing forced slow-DS2 recovery option.
- Added an Adaptations tab for knock, fuel-trim, idle-fuel-trim, throttle, and
  related built-in MS41 adaptation values, and moved adaptation reset there.
- Tightened exact program/calibration compatibility checks while preserving
  supported full-ROM conversions, coding-family grafting, boot writes, and
  recovery paths.
- Updated CalGuard installation, detection, migration, and version reporting.
- Added version badges and safer migration handling for installed patch
  revisions.
- Added a pre-erase ignition-cycle Retry/Cancel prompt when stock DS2 write
  authorization is ambiguous during Soft-BSL installation. No erase or flash
  command is sent before this prompt.

**IGNITION CUT HAZARD:** Ignition Cut V7 is in a very early stage. It will cause
fuel-related, misfire, and coil-related DTCs and fuel-trim issues, and the cut is
extremely aggressive. Never use it on a car with catalytic converters; unburned
fuel can destroy them.

### Distribution status

Version `0.1.0b11` is distributed under GNU GPL version 3 (`GPL-3.0-only`) using
the GPLv3 PyQt5 distribution path. PyInstaller and Nuitka Windows x64 builds are
provided as per-user installers and portable ZIPs. Every package includes the
user manual, third-party license inventory, release metadata, unchanged
application-local Visual C++ runtime files, and
`BimmerStein MS41 Patch Definitions.xml` beside the executable. That definition
includes the supported Ignition Cut and Launch Control tuning entries. SHA-256
manifests cover every portable ZIP and installer.
