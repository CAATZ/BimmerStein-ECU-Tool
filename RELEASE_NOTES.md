# BimmerStein ECU Tool Release Notes

## 0.1.0 Beta 13

Beta 13 improves DS2 fallback, EEPROM service, diagnostics, recovery, and
offline file handling. It intentionally retains the Ignition Cut and Launch
Control revisions distributed in Beta 12; newer experimental revisions are not
included.

**OFF-ROAD, COMPETITION, RESEARCH, AND BENCH USE ONLY.** Do not use this software
to modify a vehicle operated on public roads. The user is responsible for
compliance with applicable emissions, safety, registration, and other laws.

### Changes

- Corrected regular-DS2 write fallback so an eligible native-fast startup
  failure retries at 9600 baud instead of ending the operation immediately.
- Accepted the two-byte empty DTC response (`00 00`) returned by some ECUs.
- Added MS41 EEPROM layout detection with an optional manual override. CH341A
  reads no longer require a layout selection, and unreadable ECU identifiers
  are sanitized before automatic Windows filenames are created.
- Added CH341A full-image read, guarded write, seed/recovery, exact readback,
  and restore workflows with saved-before-image requirements.
- Updated the persistent Soft-BSL loader to V10 with stricter dispatcher-hook
  and RAM-agent length validation.
- Hardened Soft-BSL Phase 1 startup, serial connection ownership, cleanup, and
  recovery handoff. A connection-matched full ECU read from Patches can be
  reused instead of repeating the read.
- Enforced installed patch dependencies and blocked removal of loaders still
  required by installed patches. A selected full-ROM Bin can now be opened
  directly in Patches.
- Added read-only comparison of two Bins, including SHA-256 replacement
  detection, program/calibration and checksum details, installed patch
  inventory, and changed-byte ranges.
- Restored AlphaN MAF-failsafe V2 with its historical A14-XOR transfer error
  corrected, and added the checksum-correct MS41.0 VANOS minimum-RPM V2
  migration.
- Added packaged build information and privacy-scoped support export. Raw ROMs
  are excluded, and session logs require explicit privacy consent.
- Retained the Beta 12 patch set and matching definitions exactly: Ignition Cut
  V7 for MS41.0 through MS41.3; Launch Control V4 for MS41.0 through MS41.2;
  and Launch Control V5 for MS41.3.

### Validation status

- Soft-BSL loader V10 and the MS41.2/MS41.3 DS2 `0x2A` entry patch:
  **TESTED**.
- CalGuard V4: **BENCH PROVEN**.
- Ignition Cut V7: **VEHICLE TEST REQUIRED**.
- Launch Control V4 on MS41.0/MS41.1: **VEHICLE TEST REQUIRED**.
- Launch Control V4 on MS41.2 and Launch Control V5 on MS41.3:
  **VEHICLE RETEST REQUIRED**.
- AlphaN MAF-failsafe V2: **UNTESTED** for physical/on-car behavior.

**IGNITION CUT HAZARD:** Ignition Cut V7 is highly experimental and extremely
aggressive. It can cause fuel-related, misfire, and coil-related DTCs and
fuel-trim issues. Never use it on a vehicle with catalytic converters;
unburned fuel can destroy them.

### Distribution status

Version `0.1.0b13` is distributed under GNU GPL version 3 (`GPL-3.0-only`) using
the GPLv3 PyQt5 distribution path. PyInstaller and Nuitka Windows x64 builds are
supplied as per-user installers and portable ZIPs, with
`BimmerStein MS41 Patch Definitions.xml` beside the executable, release
metadata, third-party notices and licenses, the user manual, and SHA-256
manifests.
