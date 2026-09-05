# BimmerStein ECU Tool Release Notes

## 0.1.0 Beta 15

Beta 15 improves display scaling, backup reliability, and recovery reporting
while retaining the published Beta 14 firmware patches.

**OFF-ROAD, COMPETITION, RESEARCH, AND BENCH USE ONLY.** Do not use this
software to modify a vehicle operated on public roads. The user is responsible
for compliance with applicable emissions, safety, registration, and other laws.

### Highlights

- Improved readability on scaled displays and smaller screens while preserving
  the system font size and keeping controls accessible by scrolling.
- Soft-BSL stops with a clear recovery error when the ECU's return to normal
  operation cannot be confirmed. It does not report success or automatically
  reconnect and retry in that state.
- Completed reads are preserved in Bins when possible even if subsequent
  recovery cannot be confirmed. The error shows the saved capture's location,
  any storage problem, and the required next steps.
- Interrupted Bins catalogue saves can be recovered after the storage problem
  is resolved and the application is restarted. Saved images and their original
  metadata are preserved, and failures identify the retained file.
- Fixed filename collisions with Bins metadata and corrected checksum-copy
  filenames for uppercase `.BIN` and extensionless files.
- Invalid saved definition settings use the normal fallback. VIN and firmware-ID
  validation now rejects malformed or incomplete data more consistently.

### Important warnings

> **HIGHLY EXPERIMENTAL — NOT VEHICLE TESTED.** The Coding tab can change
> configuration in multiple vehicle modules. Built-in profiles and read-back
> checks reduce mistakes, but they do not prove a change is safe for a
> particular vehicle. Back up first, use stable power, keep the engine off,
> change only settings you understand, and be prepared to restore the original
> coding.

Ignition Cut and Launch Control are unchanged from Beta 14: Ignition Cut V7;
Launch Control V4 on MS41.0, MS41.1, and MS41.2; and Launch Control V5 on
MS41.3. They remain highly experimental and require controlled vehicle testing.
AlphaN MAF-failsafe V3 also remains experimental and requires vehicle testing.

**IGNITION CUT HAZARD:** Ignition Cut may suppress spark while injection
continues. Unburned fuel can damage catalytic converters and exhaust components;
never use it on a car with catalytic converters. Offline validation does not
establish safe behavior on an engine.

### Validation

- Automated offline checks cover the recovery, catalogue, filename, settings,
  and validation fixes alongside the existing programming and diagnostic workflows.
- Soft-BSL V11 and CalGuard V5 still require bench confirmation. The application
  recovery fixes do not change their physical validation status.
- The Coding tab has not completed vehicle testing and must be treated as
  highly experimental.

### Distribution

Version `0.1.0b15` contains the Windows x64 PC application only. PyInstaller
and Nuitka builds are supplied as per-user installers and portable ZIPs. Each
package includes the user manual, `BimmerStein MS41 Patch Definitions.xml`,
placed beside the executable, plus release metadata, third-party notices and
licenses, and SHA-256 checksums. The application is distributed under
`GPL-3.0-only`. Development-only material and private reference ROMs are not
included.
