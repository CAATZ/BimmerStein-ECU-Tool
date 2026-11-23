# BimmerStein ECU Tool — Beta 20

Version 0.1.0b20

## What's new

- **Community patches for MS42/MS43.** Import MS42 v1.7.1 or either MS43 430069 v2.9.2 list (standard or Extended Load). Configure cluster LEDs, presets, maps and flags in Patches. You can import a newer list alongside the original and take the original XDF, BIN and rollback record to another PC.
- **Save, export or flash an edit.** Patches and ECU Config let you name a BIN in **Patched** or **Configured**, then open it in Bins or export it immediately. **Flash without saving** opens a review where you can also export the exact image.
- **MS42/MS43 hardware recovery.** BSL-Unbricker can read a full flash or tune through hardware boot mode when normal ECU communication is unavailable. The new recovery workflow backs up the original before a write and verifies the result. Physical recovery is still unverified.

## Fixes and improvements

- **MS41 limiters.** Ignition Cut and Launch Control V20 improve spark and fuel-cut recovery and preserve knock readings during cuts. A diagnosed vehicle-speed fault releases Launch Control. Upgrading a supported V18 or V19 pair retains compatible settings.
- **MS41 load and VANOS.** MS41.3 AlphaN V4 uses measured airflow to detect when the MAF recovers and preserves Speed Density transient fueling. **Original VANOS logic** now follows the stock solenoid command through 8,160 RPM on MS41.0 1429861 and MS41.1 1437806. Numeric RPM settings carry over.
- **Coding and transmission conversion.** Corrected E38/E39 cluster checksums and E46 climate-module identification. After an interrupted cluster write, **Read Settings** can restore the saved original. Guided conversion checks for later coding changes before rollback and can verify restored coding with either transmission-controller layout.
- **Live Data and CSV.** Unavailable channels explain why they have no reading. MS41.3 patch channels are recognized when the ECU reports the stock software number. Missed samples appear as gaps, channel colors stay consistent, and long saved logs retain every sample across pages. New CSVs carry the units needed for playback.
- **Bins and file handling.** Long filenames keep their distinguishing endings visible. Older MS42/MS43 library entries remain accessible, and a completed ECU read can still be saved to another location if Bins cannot archive it.

The installer includes the RomRaider file `BimmerStein MS41 Patch Definitions.xml` beside the executable.

## Before using it

**Coding and guided transmission conversion are HIGHLY EXPERIMENTAL. We do not recommend using them until further vehicle testing is complete.** Keep original coding and a suitable recovery method.

**Ignition Cut and Launch Control are HIGHLY EXPERIMENTAL.** Vehicle validation is incomplete. Do not rely on them for engine protection or use ignition cut with catalytic converters fitted. Unburned fuel can damage the exhaust. Check each patch’s testing status and exact firmware support.

Use stable power and keep original backups. If a write is interrupted, follow the app’s recovery instructions before disconnecting or cycling ignition.

## Windows downloads

Choose the x64 or x86 installer for your Windows architecture. Windows 7 SP1 has separately labelled x64 and x86 installers. Verify your download against the supplied SHA-256 checksums.

**OFF-ROAD, COMPETITION, RESEARCH, AND BENCH USE ONLY.**

Copyright (C) 2026 CAATZ. GNU GPL version 3 (GPL-3.0-only).
