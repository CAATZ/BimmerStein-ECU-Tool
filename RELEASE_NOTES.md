# BimmerStein ECU Tool Release Notes

## 0.1.0 Beta 4

This beta refresh keeps the BMW MS41 programming, diagnostic, patching, and
recovery workflows from Beta 3 while adding a safe operator-controlled recovery
for a Soft-BSL Phase 1 marker timeout. It also ships an explicitly experimental
Nuitka installer as a second Windows packaging option.

**OFF-ROAD, COMPETITION, RESEARCH, AND BENCH USE ONLY.** Do not use this software
to modify a vehicle operated on public roads. The user is responsible for
compliance with applicable emissions, safety, registration, and other laws.

### Highlights

- When the temporary Soft-BSL Phase 1 write cannot observe the ECU-owned
  `E659=0xCC` readiness marker, the application now closes and releases the
  adapter before asking the operator to cycle ignition and explicitly retry or
  cancel. Cancellation is pre-erase: no challenge, selector, erase, or flash
  command is sent.
- Every repeated marker timeout requires a new operator decision. A retry uses
  the same validated prepared images and detected flash family, opens a fresh
  native-fast transport, and never silently changes to the legacy 9,600-baud
  writer. Unrelated pre-erase failures still fail closed, and post-erase
  failures retain the existing live recovery path.
- The regular PyInstaller installer and portable ZIP remain the recommended
  Windows packages. A separate installer and ZIP whose filenames and product
  identity contain **Nuitka Experimental** are supplied only as a second
  compatibility-testing option; both backends use the same application source.

- A native-fast read followed immediately by a stock-DS2 write now uses one
  10-second zero-traffic recovery interval, qualified twice on hardware, and
  one bounded retry. Both qualification runs required the retry and completed the
  full seed plus single-key `A0 00` exchange without issuing a flash command.
  The previous rapid challenge loop could keep the ECU from making its write
  seed available even though normal DS2 at 9,600 baud had been restored.
- Only an exact empty `0x90/A1` with unchanged `E658/E74B` can enter that retry.
  Timeouts and malformed replies fail closed, active live-data polling is
  stopped before ECU operations, and the recovery wait is shown in the UI.

- A missed or incomplete Soft-BSL ignition cycle is now detected before the
  destructive hook-write phase. The operator can retry after restoring normal
  DS2 communication or cancel safely, and the serial handle is released for an
  immediate reconnect.
- Hook-write authorization and pre-program activity are surfaced in the UI so
  delayed ECU seed readiness no longer looks like an application freeze.

- Automatic Flash-tab route selection: Soft-BSL with its own baud-tier fallback,
  stock native-fast DS2 with confirmed pre-erase normal-DS2 fallback, or normal DS2.
- Direct native-fast DS2 requests at the ECU-exact 187,500 baud host rate.
- Slim read/write policy: single-pass dumps, optional backups, and optional
  host read-back verification.
- Retained native-fast and Soft-BSL recovery sessions after a post-erase
  programming failure.
- Intel and AMD/JEDEC Soft-BSL and hardware-BSL workflows, with D2XX preferred
  and supported serial fallbacks.
- Mandatory ECU-side DS2 write finalization kept separate from the optional
  host Verify checkbox.
- Current Ignition Cut V7 and Launch Control V4 descriptors, with explicit
  Untested status for the new ignition final-stage route and safe
  detection/removal of deprecated revisions including field-failed V6.
- Exact white-and-black variant of the canonical BimmerStein vector across the
  app, installer, shortcuts, documentation, and all Windows icon resolutions.
- Persistent user-managed calibration definitions in the ROM Analyzer; no
  third-party ECU definition is bundled with the application.
- A modeless, resizable ROM Analyzer parameters window with independent
  filtering, scalar-only view, sorting, and live synchronization.
- Rewritten public README, illustrated user manual, synthetic screenshot
  pipeline, third-party notices, and stronger release-package validation.

### Operator behavior to know

- Backup and Verify remain operator choices.
- If a write fails after erase starts, keep ignition ON, keep the adapter
  connected, keep the application open, and use Retry Flash Recovery.
- After every successful Flash-tab write, turn ignition OFF, wait at least 10
  seconds, and turn ignition ON.
- Ignition Cut V7 and Launch Control V4 ignition mode remain marked Untested;
  Launch Control V4 fuel mode has held its configured 4000 RPM
  setpoint during vehicle testing.
- Ignition Cut and Launch Control tuning definitions are not bundled with the
  Windows release. Source a compatible definition separately and verify that
  it matches the ECU variant, calibration ID, and installed patch revision.

### Distribution status

This beta refresh is version `0.1.0b4` and is distributed under GNU GPL version 3
(`GPL-3.0-only`). The free GPLv3 PyQt5 distribution path is selected. The
recommended Windows x64 PyInstaller build is distributed as both a per-user
installer and a portable ZIP. The separately labeled experimental Nuitka build
is also distributed in both forms and installs under a distinct product identity
so it can coexist for comparison. Every package includes unchanged
application-local Visual C++ runtime files and records their SHA-256 hashes,
backend, and experimental status in the release metadata; no separate runtime
installation or administrator access is required.
