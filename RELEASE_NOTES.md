# BimmerStein ECU Tool Release Notes

## 0.1.0 Beta 12

This release adds CalGuard boot recovery, corrects MS41 live-data and
transmission handling, and polishes the recovery and patch interfaces.

**OFF-ROAD, COMPETITION, RESEARCH, AND BENCH USE ONLY.** Do not use this software
to modify a vehicle operated on public roads. The user is responsible for
compliance with applicable emissions, safety, registration, and other laws.

### Changes

- Added the bench-test-required CalGuard V4 compatibility and boot-recovery
  guard. Its bounded key-on listener can establish and retain a Soft-BSL
  recovery session for subsequent reads or writes.
- Added automatic and explicit CalGuard recovery routing. Once the recovery
  agent owns the port, operations reuse that session without reopening the
  adapter or falling back to DS2.
- Corrected firmware-specific live-data mappings: throttle position now uses
  the normalized `0xE8D0` value, MS41.0 battery voltage uses `0xFB47`, the
  unsupported lambda display was removed, and `0xE9E6` is identified as the
  measured VANOS angle.
- Stock MS41.0 Byte 5 value `0xAC` now decodes as `AT/MT (auto)` instead of
  `Custom (0x2C)`.
- Transmission changes now modify only Byte 5 bits 0-5. Unrelated bit 6 and
  knock-detection bit 7 are preserved exactly.
- Simplified transfer and recovery labels, patch test-status wording, and the
  matching manual while retaining the existing safety warnings and tooltips.
- Expanded regression coverage for boot-recovery ownership, route selection,
  live-data address families, and every Byte 5 transmission value.

CalGuard V4 remains marked **BENCH TEST REQUIRED**. Offline tests do not prove
its behavior on physical ECU hardware.

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
