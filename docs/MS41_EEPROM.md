# MS41 EEPROM backup and editing

MS41.0, MS41.1, MS41.2 and MS41.3 use a separate **512-byte 24C04 EEPROM**.
It stores learned values, diagnostic history, counters and family-specific
identity/coding records. It is not another calibration file.

## Read and inspect

The **EEPROM** tab has a loaded-image panel, **ECU Agent** controls and
**CH341A Programmer** controls. Both writers use the displayed image.

- **ECU Agent** reads the physical EEPROM through an installed compatible
  Soft-BSL loader. The temporary EEPROM agent does not erase or program flash.
- **CH341A Programmer** reads the chip through the separately connected
  programmer. Follow the required unpowered-device wiring procedure.
- **Open EEPROM File** or an EEPROM entry in **Bins** loads an existing backup
  without communicating with an ECU.

Successful reads are archived in Bins before an optional additional copy is
offered. Keep original backups private: they may contain vehicle identity data.

A 512-byte file is not necessarily a full physical capture. Some RAM mirrors
are padded to that length and omit the boot/identity tail. The inspector warns
about suspected padding and does not interpret that tail as genuine data.
Uniform captures and padded mirrors are not accepted as physical write targets.

## Edit a backup

**EEPROM Manager** provides decoded fields, a Hex view, learned-knock grids,
undo/redo and comparisons. Select the correct ECU family: record locations and
checks differ between families.

Named values use established units where available. Unknown fields remain raw;
stored values need not describe the current live ECU state. Inferred labels
are identified in the editor. Do not edit unfamiliar state merely to clear a
warning or make a check pass.

**Repair selected checks** repairs only the selected supported records.
**Update Checks for Edited Records** updates checks for changed payloads.
Neither operation proves that the payload itself is sensible. Applying changes
updates the loaded image, not the physical EEPROM. Save a separate edited copy
before choosing a write action.

The transmission shortcut changes the family-specific stored selector and its
record check while preserving unrelated bits. Whether the ECU uses that selector
also depends on the matching firmware configuration; it is not a complete
vehicle transmission conversion.

## Write and recover

**Write Loaded Image** requires explicit **WRITE EEPROM** confirmation. The app
reads and saves the original image, validates changed records, writes only the
required bytes and verifies the complete result. Known checked records have
their payload written before their final check values.

If a write or reply is uncertain, keep the ECU powered and the app open. The
active agent session checks the actual EEPROM against the saved original and
prepared target. Resume only through the offered recovery action; do not start
another write or reconnect independently.

**Seed ECU Recovery** and **Restore Pre-Seed State** are separate programmer
actions for established boot-state records. Restore uses the saved original
state rather than guessing it. They do not replace a complete EEPROM backup.

Normal ROM flashing and identity grafting do not write the EEPROM. A successful
EEPROM write also does not establish that a flash image or vehicle coding is
correct. Follow the app's completion and ignition instructions.

Offline validation does not establish electrical behavior or the suitability of
arbitrary edited values on a physical ECU. Keep a verified original backup and
a recovery method available.
