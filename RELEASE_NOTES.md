# BimmerStein ECU Tool Release Notes

## 0.1.0 Beta 9

This release brings the current Soft-BSL and firmware-patch suite to MS41.0
and MS41.1.

**OFF-ROAD, COMPETITION, RESEARCH, AND BENCH USE ONLY.** Do not use this software
to modify a vehicle operated on public roads. The user is responsible for
compliance with applicable emissions, safety, registration, and other laws.

### Changes

- Added persistent Soft-BSL installation for MS41.0 and MS41.1 using
  firmware-specific command-door hooks and native boot layouts.
- Added MS41.0 and MS41.1 ports of Ignition Cut V7 and Launch Control V4.
- Added the closed-throttle VANOS minimum-RPM retrofit to MS41.1.
- Expanded the bundled RomRaider patch definitions to cover the new MS41.0 and
  MS41.1 controls in both 24 KB calibration files and 256 KB full reads.
- Recomputed the applicable program checksum for composed MS41.0, MS41.1,
  MS41.2, and MS41.3 patch images.
- Extended the emulator gate across the variant-specific hooks, patch caves,
  switch and threshold paths, dependencies, and register/DPP hygiene.

Soft-BSL installation on MS41.0 and MS41.1 has been confirmed with the
application. Ignition Cut V7, Launch Control V4, and the MS41.1 VANOS retrofit
remain emulator-verified and require controlled on-car validation.

### Distribution status

Version `0.1.0b9` is distributed under GNU GPL version 3 (`GPL-3.0-only`) using
the GPLv3 PyQt5 distribution path. PyInstaller and Nuitka Windows x64 builds are
provided as per-user installers and portable ZIPs. Every package includes the
user manual, third-party license inventory, release metadata, unchanged
application-local Visual C++ runtime files, and
`BimmerStein MS41 Patch Definitions.xml` beside the executable. That definition
includes the supported Ignition Cut and Launch Control tuning entries. SHA-256
manifests cover every portable ZIP and installer.
