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
