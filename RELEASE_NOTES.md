# BimmerStein ECU Tool Release Notes

## 0.1.0 Beta 14

Beta 14 adds human-readable vehicle diagnostics and coding, improves ECU
identity reporting, and hardens recovery and flashing workflows. The packaged
Ignition Cut and Launch Control payloads remain frozen to the exact Beta 13
versions while newer development revisions are investigated.

**OFF-ROAD, COMPETITION, RESEARCH, AND BENCH USE ONLY.** Do not use this software
to modify a vehicle operated on public roads. The user is responsible for
compliance with applicable emissions, safety, registration, and other laws.

### Changes

- Added a Diagnostics tab that discovers the supported engine, transmission,
  immobilizer, ABS/traction-control, climate-control, and cruise-control
  modules over K-line, with exact-profile fault reading and clearing where
  supported.
- Added a human-readable Coding tab for reviewed GM3 window, lock, and memory
  settings plus exact E46 driver-seat memory settings. Everyday controls are
  shown first; technical references remain behind the Advanced switch.
- Added guided one-click manual/automatic transmission conversion for exact
  reviewed E39 MS41-family, E46 MS42 AM51, and late E46 MS43 EV51 profiles.
  It verifies the complete fitted-car state before writing, archives every
  changed owner, supports finish-or-restore recovery after restart, requires
  the displayed ignition cycle, and independently verifies the final state.
  E36 and unknown or inconsistent profiles are identified and refused before
  coding.
- Expanded ECU Info with programming history, program lineage, firmware-aware
  transmission state, and clearer identity fields.
- Added advisory battery-voltage checks before writes. Low or unavailable
  voltage warns the user but does not enforce a programming block.
- Relocated Soft-BSL V11 and CalGuard V5 outside the complete programming-history
  area, with exact migration from V10/V4. Both require renewed bench testing.
- Replaced AlphaN MAF-failsafe V2 with V3, correcting its fallback load path and
  preserving diagnostic reason information. Physical validation is still
  required.
- Kept the exact packaged Beta 13 Ignition Cut V9 and Launch Control V7
  descriptors in both Windows builds; newer development bytes are excluded.

### Validation status

- The frozen Ignition Cut V9 and Launch Control V7 payloads remain experimental,
  retain their Beta 13 offline verification status, and still require on-car testing.
- Soft-BSL V11, CalGuard V5, and AlphaN MAF-failsafe V3 passed exact firmware
  execution checks; renewed physical testing is required.
- Vehicle diagnostics and coding are restricted to reviewed exact profiles;
  unknown module revisions remain read-only.
- Transmission conversion and restart recovery passed the complete offline
  automated test suite. Physical vehicle validation is still required.

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
manifests.
