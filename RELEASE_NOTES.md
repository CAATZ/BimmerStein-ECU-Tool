# BimmerStein ECU Tool Release Notes

## 0.1.0 Beta 10

This release relocates the MS41.3 Launch Control configuration so it can be
used alongside boost control.

**OFF-ROAD, COMPETITION, RESEARCH, AND BENCH USE ONLY.** Do not use this software
to modify a vehicle operated on public roads. The user is responsible for
compliance with applicable emissions, safety, registration, and other laws.

### Changes

- Revved MS41.3 Launch Control to V5 and moved its configuration from the live
  boost knock-compensation table to the dedicated `0x47E0-0x47E7` block.
- Preserved released MS41.3 Launch Control V4 byte-for-byte as a deprecated,
  detect-and-remove-only migration entry.
- Regenerated the bundled RomRaider definitions with the MS41.3 V5 addresses
  for both 24 KB calibration files and 256 KB full reads.
- Kept program-checksum correction enabled when composing the relocated patch.
- Extended regression and emulator gates across V4 migration, V5 installation,
  checksum handling, configuration addressing, and boost-table preservation.

MS41.3 Launch Control V5 remains emulator-verified and requires controlled
on-car validation. Existing V4 installations should be removed before V5 is
installed; review the formerly overlapping boost table once during migration.

### Distribution status

Version `0.1.0b10` is distributed under GNU GPL version 3 (`GPL-3.0-only`) using
the GPLv3 PyQt5 distribution path. PyInstaller and Nuitka Windows x64 builds are
provided as per-user installers and portable ZIPs. Every package includes the
user manual, third-party license inventory, release metadata, unchanged
application-local Visual C++ runtime files, and
`BimmerStein MS41 Patch Definitions.xml` beside the executable. That definition
includes the supported Ignition Cut and Launch Control tuning entries. SHA-256
manifests cover every portable ZIP and installer.
