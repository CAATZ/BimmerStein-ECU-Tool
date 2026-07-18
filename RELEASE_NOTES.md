# BimmerStein ECU Tool Release Notes

## 0.1.0 Beta 1

This release consolidates the BMW MS41 programming, diagnostic, patching, and
recovery workflows under the BimmerStein ECU Tool identity.

**OFF-ROAD, COMPETITION, RESEARCH, AND BENCH USE ONLY.** Do not use this software
to modify a vehicle operated on public roads. The user is responsible for
compliance with applicable emissions, safety, registration, and other laws.

### Highlights

- Automatic Flash-tab route selection: Soft-BSL with its own baud-tier fallback,
  stock native-fast DS2 with confirmed pre-erase normal-DS2 fallback, or normal DS2.
- Direct native-fast DS2 requests at the ECU-exact 187,500 baud host rate.
- Slim read/write policy: single-pass dumps, optional backups, and optional
  host read-back verification.
- Retained native-fast and Soft-BSL recovery sessions after a post-erase
  programming failure.
- Intel and AMD/JEDEC Soft-BSL and hardware-BSL workflows, with D2XX preferred
  and supported serial fallbacks.
- Mandatory ECU-side DS2 write finalization kept separate from the optional
  host Verify checkbox.
- Current Ignition Cut V7 and Launch Control V4 descriptors, with explicit
  Untested status for the new ignition final-stage route and safe
  detection/removal of deprecated revisions including field-failed V6.
- White-and-black BimmerStein application artwork across the app and Windows
  package.
- Persistent user-managed calibration definitions in the ROM Analyzer; no
  third-party ECU definition is bundled with the application.
- A modeless, resizable ROM Analyzer parameters window with independent
  filtering, scalar-only view, sorting, and live synchronization.
- Rewritten public README, illustrated user manual, synthetic screenshot
  pipeline, third-party notices, and stronger release-package validation.

### Operator behavior to know

- Backup and Verify remain operator choices.
- If a write fails after erase starts, keep ignition ON, keep the adapter
  connected, keep the application open, and use Retry Flash Recovery.
- After every successful Flash-tab write, turn ignition OFF, wait at least 10
  seconds, and turn ignition ON.
- Ignition Cut V7 and Launch Control V4 ignition mode remain marked Untested;
  Launch Control V4 fuel mode has held its configured 4000 RPM
  setpoint during vehicle testing.
- Ignition Cut and Launch Control tuning definitions are not bundled with the
  Windows release. Source a compatible definition separately and verify that
  it matches the ECU variant, calibration ID, and installed patch revision.

### Distribution status

This first public beta is version `0.1.0b1` and is distributed under GNU GPL version 3
(`GPL-3.0-only`). The free GPLv3 PyQt5 distribution path is selected. The
Windows x64 build is distributed as both a per-user installer and a portable
ZIP. Both include unchanged application-local Visual C++ runtime files and
record their SHA-256 hashes in the release metadata; no separate runtime
installation or administrator access is required.
