# BimmerStein ECU Tool — Beta 18

Version `0.1.0b18`

- CalGuard V6 owns the existing nominal 40 ms startup wait on Intel and AMD.
  The AMD flash routines remain independent. The watchdog service, initial recovery window
  and calibration compatibility check keep the same machine instructions.
  Reinstallation applies the wait once; no separate startup-wait patch is offered.
  Removing CalGuard restores the original startup timing while retaining
  Soft-BSL and the flash driver. Standalone Soft-BSL or AMD driver installations
  do not add the wait. Exact leftover waits from earlier local builds are cleaned
  during standalone updates or removal; unknown startup bytes remain rejected.
- Exact AMD V3 installations can be upgraded or removed. Soft-BSL updates,
  TOP protection, dependency checks and boot-write gating recognize the
  historical driver while rejecting unknown or partly installed startup bytes.
- Includes Launch Control V11 on all four firmware families, Ignition Cut V11
  on MS41.3 and V10 on MS41.0/.1/.2, CalGuard V6 and Soft-BSL V12.
- Patch configuration uses numeric inputs with explicit stock/automatic modes,
  loaded stock hysteresis values, clearer fuel hysteresis A/B labels and zero-IPW
  help. Compact dialogs, patch-table headers and Details popovers
  reduce clutter without changing firmware behavior
  or installation/removal checks.
- Includes migration from CalGuard V5 / Soft-BSL V11, patch removal,
  dependency checks and recovery routing.
- Soft-BSL updates preserve an installed CalGuard and recognize both valid
  loader bank markers. Ambiguous historical patch bytes remain rejected.
- Normal Soft-BSL entry checks small firmware markers before recovery detection,
  avoiding a complete CalGuard read at 9600 baud. Direct recovery still requires
  exact guard and loader evidence for its existing entry path.
- Firmware compositions preserve identifiers and calibration. Local bench
  validation compares protected bytes against the actual ECU before erase.
- Before this ownership change, the Intel MS41.3 / 28F200 image with these wait bytes passed three cold
  recovery trials with full matching readbacks. The exact marker-free AMD MS41.0 / 29F400BB
  lower-bank image passed three cold recovery trials with full matching
  readbacks. Earlier firmware/bank combinations have emulator evidence.
  These results remain historical evidence for the unchanged firmware bytes.
  Removal and standalone-update corrections have offline application-test coverage.
- The current AMD MS41.0 / 29F400BB TOP image passed exact-image emulator checks,
  a verified full TOP-bank installation, three cold initial-token recovery trials
  with matching complete CRC readbacks, and normal cold boots. Identifiers and
  calibration were preserved. This qualifies that exact ECU/image; destructive
  calibration-mismatch, program-rewrite and power-loss tests were not performed.

CalGuard and Soft-BSL remain experimental. Installing the application does not
modify ECU firmware.

---

# BimmerStein ECU Tool — Beta 17

Version `0.1.0b17`

This update focuses on more reliable flashing and fewer delays when preparing a
write. It also fixes a desktop freeze when a serial adapter stops responding.

## What's improved

- Interrupted writes now have better recovery and a more reliable return to
  slow communication. When a write acknowledgement is missing, the app checks
  what reached the ECU before deciding how to continue.
- Full writes now follow the switches you selected. With **Write boot** off,
  the ECU's existing 8 KB boot area is preserved. With it on, the boot area
  comes from your file. **Graft identity** copies the ECU's 670 identity bytes
  when using the file's boot area.
- TOP-bank full writes use Soft-BSL. Forced DS2 mode reports this restriction
  earlier, avoiding the lengthy preparation that previously ended in rejection.
  Partial DS2 writes remain available.
- Verification uses one continuous progress indicator for the whole write.
- A stalled serial adapter no longer holds the desktop open indefinitely.
  Connection attempts can time out, and closing the app stays responsive.
- Incomplete operation records no longer block a deliberately started new
  write. Saved recovery information remains available for review.
- Fixed the brief blank squares that appeared while the desktop was starting.
- Updated the AMD flash driver and its TOP-bank full-write protection.

## Downloads

Choose **x64** for a 64-bit PC or **x86** for 32-bit Windows. Each has an installer
and a complete portable ZIP. Separate Windows 7 SP1 downloads are provided
for both architectures. Windows 7 requires KB2533623 or a superseding update. Installation folders and shortcuts use the name
**BimmerStein ECU Tool**. Updates keep the existing installation location.

The packages include the user manual and `BimmerStein MS41 Patch Definitions.xml`
beside the executable. Keep the complete portable folder together.

Installing this update does not change the firmware already in your ECU.

## Firmware notes

This release keeps **Ignition Cut V7** and **Launch Control V4/V5** from Beta 16.
Newer revisions remain outside public releases until vehicle testing confirms them.
AlphaN MAF-failsafe V3, Soft-BSL V11 and CalGuard V5 retain their tested status.

Ignition cut and launch control remain experimental. Spark suppression can
send unburned fuel into the exhaust and damage catalytic converters and other
components. Do not use ignition cut with catalytic converters fitted.
Other firmware options retain the test status shown in the Patches tab.

**OFF-ROAD, COMPETITION, RESEARCH, AND BENCH USE ONLY.**
The project is distributed under GNU GPL version 3 (`GPL-3.0-only`). Required
runtime licenses and notices are included.
