# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Release numbers use a `major.minor.patch` base and the compact `bN` beta suffix
shared by BimmerStein applications.

## [Unreleased]

## [0.1.0b1] - 2026-07-17

First public beta for BMW MS41.1, MS41.2, and MS41.3 programming, diagnostics,
configuration, patching, and recovery workflows.

### Added

- Full-ROM (256 KB) and tune-region (24 KB calibration) read/write over DS2,
  with the bootloader region left intact.
- Checksum verification, correction, and configurable disable options across
  the program, calibration, and boot regions.
- Hybrid-ROM detection and variant-conversion warnings before a full flash.
- DTC read and clear with MS41-specific fault descriptions.
- Live-data monitoring, fast telegram mode, and adaptation reset workflows.
- Offline ROM Analyzer for variant, ECU ID, CAL ID, VIN, checksum state, and
  user-supplied RomRaider calibration definitions.
- Backup cataloguing with automatic VIN and CAL-ID naming.
- Collision-safe firmware patch composition with checksum recomputation and
  deprecated-patch detection/removal.
- Soft-BSL install/read/write workflows and in-circuit hardware-BSL recovery
  for Intel 28F200 and AMD/JEDEC 29F200/29F400 flash chips.
- ECU identity and ISN reading plus EWS3 alignment helpers.
- PyQt5 desktop interface for live ECU operations and offline analysis.
- Stock native-fast DS2 reads and writes with direct 187,500 baud operation,
  pre-erase stability checks, and safe fallback to normal DS2.
- Optimized Intel and AMD/JEDEC Soft-BSL RAM agents with retained-session
  recovery after post-erase failures.
- D2XX-preferred hardware-BSL transport with compatible pyserial fallback.
- BimmerStein ECU Tool product identity, application artwork, illustrated user
  manual, reproducible synthetic screenshots, and release-package verification.
- Ignition Cut V7 at the proven six-channel P1L coil final-stage charge commands,
  plus Launch Control V4 definitions updated to require V7. Field-failed V6 is
  retained as a deprecated remove-only migration target.
- Complete 24-slot DS2 live-data profiles with operating-state decoding, raw
  front-O2/MAF voltages, and automatic MS41.3 wideband AFR/target selection.
- A branded, per-user Windows installer with Start Menu integration, an optional
  desktop shortcut, and matching portable and installer checksums.

### Changed

- Relicensed the public project from MIT to GNU GPL version 3 and designated the
  current release track as beta, using the free GPLv3 PyQt5 distribution path.
- Added a persistent off-road-use-only notice to the application, README,
  release notes, user manual, and release metadata.
- ECU Config live writes now keep calibration-only edits on the 24 KB path while
  program-region edits reuse a matching archived full read, or read and archive
  the current ECU before routing the patched image through the guarded full writer.
- Simplified production read/write policy to single-pass dumps, optional
  backups, and optional host read-back verification.
- Standardized ECU-side DS2 write finalization independently of the host Verify
  choice.
- Improved Flash-tab route reporting, recovery prompts, dependency handling,
  checksum language, and hardware-BSL controls.

### Removed

- Obsolete enforced double-read, mandatory-backup, and research-only release
  policy text.
- Public reverse-engineering notes and deprecated patch sources; valuable
  engineering history remains preserved in the private project repository.
