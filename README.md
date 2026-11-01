<p align="center"><img src="assets/bimmerstein_ecu_tool.png" alt="BimmerStein ECU Tool" width="160"></p>

<h1 align="center">BimmerStein ECU Tool</h1>

<p align="center"><strong>BMW MS41, MS42 and MS43 programming and diagnostics</strong></p>

<p align="center">
<a href="https://github.com/CAATZ/BimmerStein-ECU-Tool/releases">Windows releases</a> ·
<a href="manual/USER_MANUAL.md">User manual</a> ·
<a href="https://github.com/CAATZ/BimmerStein-ECU-Tool/issues">Issues and feedback</a>
</p>

## Beta 19

Version 0.1.0b19

BimmerStein brings flashing, diagnostics, ECU configuration and file management
together in one Windows application. Beta 19 adds MS42/MS43 support alongside the
existing MS41 workflows, with faster transfers, clearer diagnostics and more
control over stored ECU data.

- **MS42/MS43 reads and writes:** full ROMs and calibration files, temporary
  high-speed RAM agents, checksum handling and recovery for interrupted writes.
- **Better diagnostics:** plain-English fault descriptions, freeze-frame details,
  actuator tests and cylinder roughness views for supported ECUs and modules.
- **Live Data:** selectable channels, graphs, gauges and CSV recording, with saved
  display preferences and adjustable panels.
- **ECU identity and storage:** VIN and programming history, verified identity
  fields, backup inspection and editing of supported stored values.
- **ECU configuration:** family-specific controls and supported transmission
  conversion workflows, with changes reviewed before writing.
- **MS41 patches and recovery:** updated Ignition Cut and Launch Control V18,
  existing Soft-BSL and hardware recovery, and clear per-patch testing status.

See the [release notes](RELEASE_NOTES.md) and [MS42/MS43 guide](docs/MS42_MS43.md)
for revision coverage and limits.

## Important use limits

> [!IMPORTANT]
> **OFF-ROAD, COMPETITION, RESEARCH, AND BENCH USE ONLY.** You are responsible for
> compliance with applicable emissions, safety, registration and other laws.

> [!WARNING]
> **CODING IS HIGHLY EXPERIMENTAL AND NOT RECOMMENDED UNTIL FURTHER TESTING.**
> This includes guided transmission conversion. Keep the original coding and a
> suitable recovery method before changing a module.

> [!WARNING]
> **IGNITION CUT AND LAUNCH CONTROL ARE HIGHLY EXPERIMENTAL.** They have not
> completed vehicle validation. Do not rely on them for engine protection.
> Ignition cut can send unburned fuel into the exhaust; do not use it with
> catalytic converters fitted. Patches marked **Untested** remain untested.

Use a stable regulated supply and keep a verified original backup. If a write
fails after erase begins, **keep ignition ON, leave the adapter connected and
keep the application open**. Follow the displayed recovery instructions. Do not
cycle power while a retained recovery session is available.

After a successful flash, follow the app's ignition-cycle instructions before
reconnecting. Installing or updating the Windows application does not flash an ECU.

## Supported workflows

| Area | Support |
| --- | --- |
| ECU families | MS41.0, MS41.1, MS41.2, MS41.3, MS42 and MS43 |
| Full ROM files | MS41: 256 KiB; MS42/MS43: 512 KiB |
| Calibration files | MS41: 24 KiB; MS42: 32 KiB; MS43: 64 KiB |
| Diagnostics | ECU information, supported fault memories, live values, actuator tests and adaptations |
| Vehicle modules | Faults and exact-profile coding on supported chassis and modules |
| Storage | MS41 512-byte EEPROM; MS42 16 KiB flash journal; MS43 2 KiB EEPROM and separate 16 KiB raw flash banks |
| Offline work | Image analysis, checksums, comparisons, Bins, calibration extraction and supported configuration edits |
| MS41 recovery | Soft-BSL and hardware BSL, including supported Intel and AMD/JEDEC flash devices |

Available functions depend on the detected firmware and fitted hardware. The app
does not treat an unknown firmware revision or module layout as a supported one.
MS41 Soft-BSL, hardware-BSL controls and the Patches catalog remain MS41-specific.

### Transfer speed and recovery

Automatic transfer uses the supported fast route and can fall back to stock DS2
when the ECU is confirmed ready for it. MS41 and MS43 use up to **187,500 baud**;
MS42 uses up to **166,667 baud**. **Settings → Compatibility — 125,000 bps** applies
to supported high-speed reads and writes. Normal diagnostics use 9,600 baud.

MS42/MS43 RAM agents are temporary: they load into RAM for the operation and
restart the ECU on completion. They do not require installing a persistent
loader. Reads using this route require the engine to be stopped.

Fallback is limited to a recoverable state before erase. After programming may
have started, recovery keeps the active session and prepared image. It does not
silently reconnect or switch protocols.

**Back up before write** and **Verify after write** follow your selections.
Required ECU-side finalization is separate from optional host readback. Including
boot code requires explicit **WRITE BOOT** confirmation. MS42/MS43 boot writes
require the RAM agent; stock DS2 writes preserve boot code. The identity-preserve
option keeps the live VIN/history, pairing records and ECU identifiers when boot
code is replaced. It does not clone or overwrite separate EEPROM storage.

### ECU data and identity

For MS42/MS43, **ECU Data → Read Identity / History** reads identification and
programming records. **Read Flash Journal**, **Read EEPROM** and **Read Flash
Banks** back up the selected storage. The selected memory and the loaded backup
are labelled separately.

The storage editor shows established units and named fields where available.
Unknown values remain raw or read-only. Applying an edit updates the loaded
backup; restoring it to the ECU is a separate confirmed action. Calibration-
dependent fields require the matching calibration.

VIN editing and optional virginizing are separate from ordinary flashing.
Virginizing clears supported pairing data and optionally VIN/programming history;
it does not perform EWS alignment automatically. See the
[MS42/MS43 guide](docs/MS42_MS43.md) and [MS41 EEPROM guide](docs/MS41_EEPROM.md).

### Live data and files

**Values**, **Graphs** and **Instruments** share your selected channels. View live
data or open a saved CSV, adjust the panel dividers, and save your preferred
instruments. CSV logging retains the complete session; the live viewer keeps a
bounded recent history. Stop polling before changing logger definitions.

**Bins** organizes reads, imports and prepared files with notes, checksums and
comparisons. Folders correspond to real directories in **backups/**; moving or
renaming files preserves their recorded metadata when the match is unambiguous.
Supported files open in the app's analysis, configuration or patch views.

## Installation

1. Choose x64 for 64-bit Windows or x86 for 32-bit Windows. Use the separately
   labelled **Windows 7** package on Windows 7 SP1.
2. Run the installer, or extract the entire portable ZIP to a writable folder.
3. Install the driver for your FTDI adapter, then start **BimmerStein ECU Tool**.

Keep the complete application folder together. Required runtimes, resources,
manual and license notices are included. Installers use a per-user location;
updates retain an existing installation location. Compare downloads with the
release SHA-256 checksums. Packages may be unsigned.

The application stores reads and recovery files in **backups/** and session logs
in **logs/** beside the executable. These files can contain VIN and ECU identity
information; keep them private and back them up before replacing a portable folder.

## Documentation and support

`BimmerStein MS41 Patch Definitions.xml` is included beside the executable for
configuring supported MS41 patches with a compatible calibration editor.

- [User manual](manual/USER_MANUAL.md) and [illustrated PDF](output/pdf/BimmerStein-ECU-Tool-User-Manual.pdf)
- [MS42/MS43 support](docs/MS42_MS43.md) and [MS41 EEPROM](docs/MS41_EEPROM.md)
- [Build instructions](BUILDING.md)
- [Hardware-BSL recovery companion](https://github.com/CAATZ/MS41-BSL-Unbricker)
- [Report a problem](https://github.com/CAATZ/BimmerStein-ECU-Tool/issues)

The build label opens **About**, including the support-bundle export. Reports
should include ECU software, Windows version, adapter, operation, exact message
and last completed step. Remove personal information before sharing logs or
screenshots. Raw ROMs are not included in support bundles.

## License and disclaimer

Copyright (C) 2026 CAATZ. Distributed under [GNU GPL version 3](LICENSE)
(GPL-3.0-only). Required dependency [notices](THIRD_PARTY_NOTICES.md) and
[license texts](THIRD_PARTY_LICENSES/) remain included.

The software is provided "as is", without warranty. ECU programming, coding,
patches and recovery can cause data loss, an unbootable ECU, engine or vehicle
damage, or unsafe behavior. You assume the risks of use and are responsible for
backups, stable power and recovery. Offline verification does not establish
physical behavior on every ECU or vehicle.

BimmerStein ECU Tool is independent software and is not affiliated with or
endorsed by BMW AG or interface manufacturers. Nothing in this notice limits
rights granted under the GNU GPL version 3.

## Acknowledgements

Thanks to NXT-Tronic, grantUser, Diolum, Alpine, roimaomanik and Alphamk4 for
ideas, feedback and testing. Required source and dependency credits remain
with the relevant code and license notices.
