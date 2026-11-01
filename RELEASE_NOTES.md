# BimmerStein ECU Tool — Beta 19

Version 0.1.0b19

Beta 19 brings MS42 and MS43 into the same application as MS41, with faster
reads and writes, clearer diagnostics and better tools for ECU data.

## What's new

- **MS42/MS43 flashing:** full and calibration reads/writes through temporary
  RAM agents, with stock DS2 fallback where available. The 125,000-baud
  compatibility setting works for these transfers too.
- **Safer boot writes:** explicit confirmation, optional preservation of the
  ECU's identity and pairing data, and recovery for interrupted operations.
- **More useful diagnostics:** English fault descriptions, supported engine,
  transmission and ABS freeze frames, direct actuator controls and roughness
  plots. Clearing faults reports acceptance separately from the follow-up read.
- **Live Data improvements:** selectable channels, CSV recording, graphs and
  customizable instruments, with saved preferences and adjustable panels.
- **ECU identity and storage:** VIN/history editing, optional virginizing,
  storage backup/restore and editing of verified fields with meaningful units.
  Virginizing leaves EWS alignment to the user.
- **Expanded configuration:** more firmware revisions and supported vehicle
  profiles, plus clearer controls that cannot change accidentally with the
  mouse wheel.
- **MS41 updates:** Ignition Cut and Launch Control V18, improved patch status
  information and the existing Soft-BSL and hardware-recovery workflows.

## Before using it

**Coding and guided transmission conversion are HIGHLY EXPERIMENTAL and are
not recommended until further testing is completed.**

**Ignition Cut and Launch Control are HIGHLY EXPERIMENTAL.** They still require
vehicle testing. Do not rely on them for engine protection, and do not use
ignition cut with catalytic converters fitted: unburned fuel can damage the
exhaust. Other patches retain their
individual status; **Untested** means untested.

MS42/MS43 features depend on the detected firmware and memory layout. Some
storage values and configuration equivalents remain unavailable. See the
[support guide](docs/MS42_MS43.md) for the current boundaries.

Use stable power and keep original backups. If a write is interrupted, follow
the app's recovery instructions before disconnecting or cycling ignition.
Installing this application does not flash or update ECU firmware automatically.

## Windows downloads

Installer and complete portable packages are provided for **x64**, **x86**,
**Windows 7 x64** and **Windows 7 x86**. Choose the package matching your Windows
version and architecture. Keep the complete portable folder together.

Packages include the user manual, required runtime licenses and
`BimmerStein MS41 Patch Definitions.xml` beside the executable.
Verify downloads against the supplied SHA-256 checksums.

**OFF-ROAD, COMPETITION, RESEARCH, AND BENCH USE ONLY.**

Copyright (C) 2026 CAATZ. GNU GPL version 3 (GPL-3.0-only).
