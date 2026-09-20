# BimmerStein ECU Tool Release Notes

## 0.1.0 Beta 16

Beta 16 makes everyday work easier: organize your saved files, edit EEPROM settings
with clearer controls, and explore live data and recorded logs. A separate Windows 7
download brings the same desktop features to older computers. Dual-bank 29F400BB
support lets you work with both halves of the flash chip.

### What's new

- **Tested 29F400BB dual-bank support:** write both halves of the flash chip and
  manage each bank's patches and Soft-BSL.
- **EEPROM editor:** find settings by name or category, edit supported values, undo
  and redo changes, and review everything before saving or writing. Compare images
  from the same ECU family and repair supported record checks when needed.
- **A more useful Bins library:** create folders, rename and move files, and open an
  editable copy in BimmerStein Tuning Suite. Frequently used actions are easier to reach.
- **Live data your way:** choose channels and view graphs, gauges, or digital values.
  Select a logger definition and recording rate, and save a CSV while watching the data.
- **Better log browsing:** search, preview, copy, and export logs and recovery records.
  Open saved CSV recordings to compare channels, zoom in, and inspect values with a cursor.
- **Windows 7 downloads:** a portable package and installer for Windows 7 SP1 x64,
  with the simple BimmerStein ECU Tool name in the installation folder and shortcuts.

### Improvements and fixes

- The main window now starts at a more compact size and can be resized. Scrolling
  keeps controls within reach on smaller screens.
- Diagnostics show clearer stored and shadow fault details, including available
  freeze-frame information. Clearing faults refreshes the list and reports any errors.
- Adaptation values can be refreshed without reopening the page.
- Improved handling of TOP and BOTTOM flash banks when reading, detecting installed
  Soft-BSL, and checking whether a boot-region write is needed.
- Bins folder operations preserve file information and handle naming conflicts more reliably.

### Firmware patches

The firmware patches are unchanged from Beta 15: **Ignition Cut V7**, **Launch
Control V4** for MS41.0/MS41.1/MS41.2, and **Launch Control V5** for MS41.3.
Installing this application does not change the firmware in your ECU.

**AlphaN MAF-failsafe V3, Soft-BSL V11, and CalGuard V5 are tested.**
Ignition Cut V7 and Launch Control V4/V5 remain experimental and require vehicle
testing. Vehicle module coding remains highly experimental and has not completed
vehicle testing.

**IGNITION CUT HAZARD:** Ignition Cut may suppress spark while injection continues.
Unburned fuel can damage catalytic converters and exhaust components; never use it
on a car with catalytic converters.

### Downloads

Version `0.1.0b16` is available as Windows x64 portable ZIPs and per-user installers,
with a separate Windows 7 SP1 x64 package. Each includes the user manual, patch
definitions (`BimmerStein MS41 Patch Definitions.xml`, beside the executable), and required application libraries. No separate Python installation
is needed. Install the appropriate interface driver separately.

The Windows 7 package requires KB2533623 or a superseding Windows update.
The Windows 7 package is tested and working. Installers are unsigned.
SHA-256 checksums accompany the downloads.

**OFF-ROAD, COMPETITION, RESEARCH, AND BENCH USE ONLY.** Do not use this software
to modify a vehicle operated on public roads. Use stable power and follow the
application's recovery instructions if a write is interrupted. Distributed under
GPL-3.0-only.
