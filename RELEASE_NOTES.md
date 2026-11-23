# BimmerStein ECU Tool — Beta 20

Version 0.1.0b20

## What's new

- **Community patch lists.** Import a compatible MS42/MS43 XDF in Patches, configure its settings and keep a rollback record. The built-in MS41 catalog remains available.
- **Edited BINs.** Give a patched or configured image a name, export it directly, or review it for flashing without saving a library copy.
- **Live Data and CSV.** Missed readings leave gaps. Long recordings open in pages, and new logs preserve the units needed to interpret their values. MS41.3 patch channels now appear when the ECU reports a stock firmware number and its program signature confirms MS41.3.
- **MS41 patches.** Ignition Cut and Launch Control advance to V20. Original VANOS logic is corrected for MS41.0 1429861 and MS41.1 1437806. MS41.3 gains AlphaN MAF-failsafe V4.
- **Coding recovery.** Supported clusters can restore the saved original settings after an interrupted write. Guided transmission conversion preserves independently changed coding when it detects a mismatch.

The installer includes the RomRaider file `BimmerStein MS41 Patch Definitions.xml` beside the executable.

## Before using it

**Coding and guided transmission conversion are HIGHLY EXPERIMENTAL. We do not recommend using them until further vehicle testing is complete.** Keep original coding and a suitable recovery method.

**Ignition Cut and Launch Control are HIGHLY EXPERIMENTAL.** Vehicle validation is incomplete. Do not rely on them for engine protection or use ignition cut with catalytic converters fitted. Unburned fuel can damage the exhaust. Check each patch’s testing status and exact firmware support.

The integrated MS42/MS43 hardware recovery route still awaits physical qualification. Use stable power and keep original backups. If a write is interrupted, follow the app’s recovery instructions before disconnecting or cycling ignition.

## Windows downloads

Choose the x64 or x86 installer for your Windows architecture. Windows 7 SP1 has separately labelled x64 and x86 installers. Verify your download against the supplied SHA-256 checksums.

**OFF-ROAD, COMPETITION, RESEARCH, AND BENCH USE ONLY.**

Copyright (C) 2026 CAATZ. GNU GPL version 3 (GPL-3.0-only).
