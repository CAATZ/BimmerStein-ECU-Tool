# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Release numbers use a `major.minor.patch` base and the compact `bN` beta suffix
shared by BimmerStein applications.

## [Unreleased]

## [0.1.0b11] - 2026-07-25

### Added

- Added automatic and forced Soft-BSL recovery routing, plus a built-in
  Adaptations tab for knock, fuel-trim, idle-fuel-trim, throttle, and related
  MS41 values.

### Fixed

- Tightened exact firmware compatibility and coding-family handling across
  flashing, Soft-BSL installation, CalGuard, live data, and patch migration.
- Added a safe pre-erase ignition-cycle Retry/Cancel path when Soft-BSL
  installation encounters ambiguous stock DS2 write authorization.

### Changed

- Expanded Ignition Cut warnings to state its early-stage status, aggressive
  behavior, expected DTC and fuel-trim effects, and catalytic-converter hazard.

## [0.1.0b10] - 2026-07-23

### Fixed

- Revved MS41.3 Launch Control to V5 and relocated its controls to the dedicated
  `0x47E0-0x47E7` calibration block, allowing Launch and boost control together.
  Released V4 remains detectable and remove-only for migration, and the bundled
  RomRaider definitions now expose the V5 addresses.

## [0.1.0b9] - 2026-07-23

### Added

- Ported persistent Soft-BSL installation and its firmware-specific normal-mode
  entry hooks to MS41.0 and MS41.1.
- Ported Ignition Cut V7 and Launch Control V4 to MS41.0 and MS41.1, with
  firmware-specific splice sites, collision checks, and offline verification gates.
- Added the closed-throttle VANOS minimum-RPM retrofit to MS41.1 and expanded
  the bundled RomRaider patch definitions for MS41.0 and MS41.1 partial and
  full-ROM images.

### Fixed

- Applied the correct program-checksum policy when composing patches for all
  supported MS41.0, MS41.1, MS41.2, and MS41.3 images.
- Preserved each firmware's native boot layout and selected variant-specific
  Soft-BSL hooks instead of reusing MS41.2 code locations.

## [0.1.0b8] - 2026-07-22

### Fixed

- Hardened stock-DS2 write recovery so retry behavior remains phase-aware after
  erasure and finalization, with explicit slow-DS2 and bench recovery guidance.
- Normalized mixed coding-family images before partial, full, conversion, and
  offline-merge writes, including the matching checksum updates.
- Disconnected after a successful patch flash and required an ignition cycle
  before the next ECU operation.

### Changed

- Increased native DS2 program payloads to 243 data bytes for both supported
  flash families while preserving exact final-frame handling.
- Marked AlphaN MAF-failsafe as untested in the patch catalog and documentation.
- Unified the installed application, Start Menu, and desktop shortcut names
  across both Windows packaging backends.

## [0.1.0b7] - 2026-07-21

### Fixed

- Corrected the EWS alignment generation label to EWS2 in the application,
  implementation documentation, and user manual. Protocol behavior is unchanged.

### Changed

- Aligned the README hero and release links with the BimmerStein presentation.

## [0.1.0b6] - 2026-07-21

### Added

- Bundled the standalone BimmerStein MS41 patch definition in both Windows
  packaging backends for RomRaider and BimmerStein Tuning Suite.
- Added explicit slow-DS2 ECU recovery selection and complete cross-variant
  identity grafting for full-ROM conversion.

### Fixed

- Enabled program checksum correction for MS41.3 and removed obsolete MS41.0
  operation gates.
- Preserved a usable patched build when removing patches and offered the
  required boot-sector write path when boot-region patch bytes change.
- Restored deterministic UI fitting without changing the established window
  proportions.

### Changed

- Promoted the Nuitka installer and portable ZIP to a supported packaging
  option while retaining the PyInstaller build.
- Kept the Soft-BSL-only loader patch out of the general Patches tab.

## [0.1.0b5] - 2026-07-21

### Added

- Patched full-ROM builds are still archived automatically to Bins and now also
  offer an optional additional copy in a user-selected location.

### Fixed

- Extracting or merging a Partial / Full image no longer switches to the Flash
  tab after the offline operation completes.
- Flash-tab option checkboxes now use even vertical spacing.

### Changed

- Reused the canonical VIN decoder and shared source-GUI startup path without
  changing their public interfaces or behavior.

## [0.1.0b4] - 2026-07-19

### Added

- Added a Nuitka Windows installer and portable ZIP as a second packaging
  option with a distinct installer identity.

### Fixed

- A Soft-BSL temporary Phase 1 program-only write that times out waiting for
  the ECU-owned `E659=0xCC` readiness marker now closes and releases the serial
  transport before offering an ignition-cycle retry or safe pre-erase cancel.
- Repeated marker timeouts require a new explicit operator decision, reuse the
  same validated prepared images and flash family, and never silently fall back
  to the legacy writer; unrelated pre-erase errors still fail closed and
  post-erase recovery behavior is unchanged.

## [0.1.0b3] - 2026-07-18

### Fixed

- Successful Soft-BSL installation now intentionally leaves the application
  disconnected instead of reopening DS2, so the next manual connection detects
  the installed loader and enables automatic Soft-BSL transfer routing.
- Stock DS2 writes started immediately after a native-fast read now recover from
  the ECU's temporary seed-unavailable state with one 10-second zero-traffic
  interval, qualified twice on hardware, and one refreshed-preamble retry
  instead of rapid challenge polling that kept the ECU from becoming ready.
- Normal 9,600-baud fallback retries only an exact empty `0x90/A1` after
  `E658/E74B` prove the ECU remained safely locked; malformed responses and
  timeouts fail closed, and Soft-BSL bootstrap installation no longer repeats a
  seed-unavailable failure through the legacy writer.
- Active live-data polling is stopped before an ECU task begins, preventing
  background DS2 traffic during authorization, baud transitions, and port
  handoffs.

## [0.1.0b2] - 2026-07-17

### Fixed

- Soft-BSL installation now confirms that normal DS2 communication has returned
  after the required ignition cycle before entering the destructive hook-write
  phase, with an explicit retry or safe cancellation path when the ECU is not
  ready.
- Failed Soft-BSL entry and serial-open paths now release their D2XX/COM handle,
  so a missed ignition cycle no longer requires restarting the application to
  reconnect.
- Soft-BSL hook-write progress now reports its authorization and pre-program
  work instead of appearing frozen on the completed base read.
- Replaced the approximate ECU Tool artwork with an exact white-and-black color
  variant of the canonical BimmerStein vector, including transparent corners
  and matching multi-resolution Windows icon frames.

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
- ECU identity and ISN reading plus EWS2 alignment helpers.
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
