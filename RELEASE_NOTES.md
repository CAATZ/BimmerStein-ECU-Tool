# BimmerStein ECU Tool Release Notes

## 0.1.0 Beta 14

Beta 14 makes recovery, EEPROM work, diagnostics, coding, and full-file
flashing easier and more predictable.

**OFF-ROAD, COMPETITION, RESEARCH, AND BENCH USE ONLY.** Do not use this
software to modify a vehicle operated on public roads. The user is responsible
for compliance with applicable emissions, safety, registration, and other laws.

### Highlights

- A complete PC EEPROM workspace can read, review, write, seed, compare, and
  restore 512-byte images with guided backups and verification.
- **Restore Pre-Seed State** can recover the exact saved original after the
  programmer is reconnected or the application is restarted.
- ECU Info is clearer, and diagnostics cover more reviewed vehicle modules.
- Guided coding and transmission-conversion workflows use plain-language
  choices, backups, read-back checks, and recovery steps.
- Soft-BSL and CalGuard can be reinstalled or updated from recognized older
  versions when the ECU boot region is healthy. A damaged boot region still
  requires recovery from a known-good backup.
- Boot-preserving full-file writes no longer stop because the file and ECU use
  different flash-chip families; that distinction matters only when the boot
  region will actually be written.
- Interrupted operations now provide clearer recovery instructions and retain
  the prepared recovery path whenever it is still safe to continue.

### Important warnings

> **HIGHLY EXPERIMENTAL — NOT VEHICLE TESTED.** The Coding tab can change
> configuration in multiple vehicle modules. Built-in profiles and read-back
> checks reduce mistakes, but they do not prove a change is safe for a
> particular vehicle. Back up first, use stable power, keep the engine off,
> change only settings you understand, and be prepared to restore the original
> coding.

Ignition Cut and Launch Control are unchanged from Beta 13: Ignition Cut V7;
Launch Control V4 on MS41.0, MS41.1, and MS41.2; and Launch Control V5 on
MS41.3. They remain highly experimental and require controlled vehicle testing.
AlphaN MAF-failsafe V3 also remains experimental and requires vehicle testing.

**IGNITION CUT HAZARD:** Ignition Cut may suppress spark while injection
continues. Unburned fuel can damage catalytic converters and exhaust components;
never use it on a car with catalytic converters. Offline validation does not
establish safe behavior on an engine.

### Validation

- Automated offline checks cover EEPROM recovery, diagnostics, coding,
  transmission conversion, file validation, Soft-BSL and CalGuard migration,
  packaging, and interrupted-operation recovery.
- Soft-BSL V11 and CalGuard V5 still require bench confirmation, including the
  newly broadened reinstall and update path.
- The Coding tab has not completed vehicle testing and must be treated as
  highly experimental.

### Distribution

Version `0.1.0b14` contains the Windows x64 PC application only. PyInstaller
and Nuitka builds are supplied as per-user installers and portable ZIPs. Each
package includes the user manual, `BimmerStein MS41 Patch Definitions.xml`,
placed beside the executable, plus release metadata, third-party notices and
licenses, and SHA-256 checksums. The application is distributed under
`GPL-3.0-only`. Development-only material and private reference ROMs are not
included.
