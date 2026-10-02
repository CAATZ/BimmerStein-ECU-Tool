# BimmerStein ECU Tool — Beta 18

Version `0.1.0b18`

Beta 18 makes patch configuration easier to use and improves ECU boot recovery.

## What's improved

- Configure selected patches before building an image. Numeric settings now use
  number controls, show their current values, and round entries to the supported
  step when you review changes. Stock and automatic modes have separate controls,
  with the stock value shown where available.
- The Patches tab has aligned columns and headers. Compact configuration dialogs
  and Details popovers keep descriptions accessible without crowding the controls.
  ECU Info buttons also have matching sizes and corrected hover help.
- Fixed an MS41.3 ignition-cut installation defect that could prevent ECU
  communication after flashing. This release includes Ignition Cut V11 for
  MS41.3 and V10 for MS41.0, MS41.1 and MS41.2.
- Launch Control V11 corrects arming and release conditions. **Always** mode
  allows arming without a switch while still respecting speed and throttle
  limits. Soft and hard cut settings remain independent, including a hard cut
  below the soft cut. Fuel hysteresis A and B can follow the stock settings or
  use separate values. The stock limiter remains active.
- CalGuard V6 keeps its initial recovery window and calibration compatibility
  check in the boot area. Its shared 40 ms startup wait supports Intel and AMD
  flash setups, including the AMD upper bank. Removing CalGuard restores the
  original startup timing.
- Normal Soft-BSL operations avoid the unnecessary full CalGuard read before
  connecting. Updates and removal retain checks for patch compatibility,
  dependencies and protected data. Soft-BSL V12 and AMD flash driver V4 are included.

## Downloads

Choose **x64** for 64-bit Windows or **x86** for 32-bit Windows. Installers and
complete portable ZIPs include the user manual and patch definitions. Keep the
complete portable folder together. Separate Windows 7 SP1 packages require
KB2533623 or a superseding update.

The installers are unsigned. The Windows 7 packages passed checks on a newer
Windows PC, but still need testing on Windows 7 itself.

Installing the application does not flash or change the firmware in your ECU.

## Firmware notes

Ignition cut, launch control, CalGuard and Soft-BSL remain experimental.
Bench results cover specific ECU and firmware combinations; the current cut
and launch revisions still need further vehicle testing. Other firmware options
retain the test status shown in the Patches tab.

Spark suppression can send unburned fuel into the exhaust and damage catalytic
converters and other components. Do not use ignition cut with catalytic converters
fitted.

**OFF-ROAD, COMPETITION, RESEARCH, AND BENCH USE ONLY.**
The project is distributed under GNU GPL version 3 (`GPL-3.0-only`). Required
runtime licenses and notices are included.
