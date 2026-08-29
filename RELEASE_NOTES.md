# BimmerStein ECU Tool Release Notes

## 0.1.0 Beta 14

Beta 14 is a Windows x64 update focused on safer EEPROM work, easier diagnostics
and coding, and more reliable recovery when an operation is interrupted.

**OFF-ROAD, COMPETITION, RESEARCH, AND BENCH USE ONLY.** Do not use this software
to modify a vehicle operated on public roads. The user is responsible for
compliance with applicable emissions, safety, registration, and other laws.

### Highlights

- A new PC EEPROM workspace reads, reviews, writes, seeds, and restores complete
  images with guarded backups and full verification.
- **Restore Pre-Seed State** now works after reconnecting the programmer or
  restarting the app. It restores only the exact saved original; if that backup
  is missing or conflicting, restore stays unavailable.
- Diagnostics and self-contained vehicle coding now cover more reviewed E36,
  E38, E39, and E46 modules, with common settings shown in plain language.
- Guided transmission conversion now includes backup, ignition-cycle, recovery,
  and final verification steps for reviewed MS41-, MS42-, and MS43-family cars.
- ECU Info is more complete, and writes now show a clear battery-voltage warning.

### Reliability

- Older Bins catalogue entries are upgraded only when their stored files still
  match, and an unreadable catalogue no longer risks replacing good records.
- Tune, full-image, and conversion checks now reject mismatched or malformed
  files before a save or write is prepared.
- Interrupted reads, writes, coding, and conversions have clearer cleanup and
  recovery paths.
- Experimental firmware options were refreshed, with their test status kept
  visible so offline checks are not mistaken for physical validation.

### Validation status

- Automated offline checks cover EEPROM seed/restore recovery, diagnostics,
  coding, transmission conversion, catalogue upgrades, and release packaging.
  Unknown or mismatched revisions remain non-writable.
- Soft-BSL V11 and CalGuard V5 are **OFFLINE EXACT-BYTE VERIFIED - BENCH TEST
  REQUIRED**.
- Ignition Cut V9, Launch Control V7, and AlphaN MAF-failsafe V3 remain
  experimental: **OFFLINE EXACT-BYTE VERIFIED - ON-CAR TESTING REQUIRED**.

**IGNITION CUT HAZARD:** Ignition Cut V9 may suppress spark while injection
continues at the stock or configured fixed pulse width. Unburned fuel can
damage catalytic converters and exhaust components; never use it on a car with
catalytic converters. Offline validation does not establish safe behavior on
an engine.

### Distribution status

Version `0.1.0b14` is prepared under GNU GPL version 3 (`GPL-3.0-only`) using
the GPLv3 PyQt5 distribution path. PyInstaller and Nuitka Windows x64 builds are
supplied as per-user installers and portable ZIPs, with
`BimmerStein MS41 Patch Definitions.xml` beside the executable, release
metadata, third-party notices and licenses, the user manual, and SHA-256
manifests. Private reference ROMs and development-only execution inputs are not
included.
