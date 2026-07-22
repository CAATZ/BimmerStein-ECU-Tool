# BimmerStein ECU Tool Release Notes

## 0.1.0 Beta 6

This release completes the MS41 variant-conversion, recovery, patch-composition,
and Windows packaging work qualified since Beta 5.

**OFF-ROAD, COMPETITION, RESEARCH, AND BENCH USE ONLY.** Do not use this software
to modify a vehicle operated on public roads. The user is responsible for
compliance with applicable emissions, safety, registration, and other laws.

### Highlights

- Full-ROM conversion now applies the target coding-family identity graft on
  every MS41.0, MS41.1, MS41.2, and MS41.3 conversion route: normal DS2,
  native-fast DS2, and Soft-BSL. Existing target-family boot bytes are preserved
  when valid, and mixed-family boot sectors are corrected before programming.
- MS41.3 now receives the same program-checksum correction as the other
  variants. Obsolete MS41.0 operation gates were removed after hardware
  qualification.
- **Force Slow DS2 (ECU Recovery)** lets the operator select the stock 9,600-baud
  path immediately when a damaged calibration still answers DS2 but cannot
  enter an accelerated route. Automatic pre-erase fallbacks remain available.
- The application window now fits deterministically within the usable screen
  while preserving its established aspect ratio and native layout proportions.
- Removing a patch now produces a build that can be archived or flashed. When a
  selected patch change modifies boot-sector bytes, the Patches tab offers the
  same guarded boot-region handling used by the Flash tab. The Soft-BSL-only
  loader patch is no longer offered as a general file patch.
- `BimmerStein MS41 Patch Definitions.xml` is bundled beside the executable in
  every Windows package for RomRaider and BimmerStein Tuning Suite. It contains
  the calibration items introduced by the supported patches.
- PyInstaller and Nuitka are both supported release backends. Each is supplied
  as a per-user installer and portable ZIP with a distinct product identity so
  the two installed builds can coexist.

### Operator behavior to know

- Backup and host read-back Verify remain operator choices. ECU-side write
  finalization remains mandatory and separate from the optional host Verify.
- Rate or route fallback is allowed only before erase. If a write fails after
  erase starts, keep ignition ON, keep the adapter connected, keep the
  application open, and use **Retry Flash Recovery**.
- After a successful Flash-tab write, turn ignition OFF, wait at least 10
  seconds, and turn ignition ON.
- Use the bundled patch definition only after the matching firmware patch is
  installed. Verify the ECU variant, calibration ID, and installed patch
  revision before editing any added table.
- Ignition Cut V7 and Launch Control V4 ignition mode remain marked Untested.
  Do not rely on either patch for engine protection or any safety-critical
  function.

### Distribution status

Version `0.1.0b6` is distributed under GNU GPL version 3 (`GPL-3.0-only`) using
the GPLv3 PyQt5 distribution path. Both Windows x64 backends include unchanged
application-local Visual C++ runtime files, the user manual, third-party license
inventory, release metadata, and the standalone patch definition. SHA-256
manifests cover every portable ZIP and installer; no separate runtime
installation or administrator access is required.
