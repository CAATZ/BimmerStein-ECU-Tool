# Calibration XML definitions for current patches

`BimmerStein MS41 Patch Definitions.xml` is a standalone ECU calibration XML
definition containing only calibrations introduced by BimmerStein patches:

- Ignition Cut V7 and Launch Control V4 for MS41.0 ID41, MS41.1 ID60,
  and MS41.2 ID12
- Ignition Cut V7 and Launch Control V5 for MS41.3 SS1v2
- the VANOSRT1/MS41.0 and VANOSRT2/MS41.1 minimum-RPM retrofits

Both 24 KB calibration files and 256 KB full reads are covered. Code-only
patches are intentionally absent because they have no calibration parameter to
edit. AlphaN MAF-failsafe continues to use the standard SS1v2 AlphaN tables; it
does not add a calibration of its own.

`ms412_ignition_cut_v7_launch_control_v4.xml` contains the ten calibration
tables used by Ignition Cut V7 + Launch Control V4 at their MS41.2 addresses.
The builder remaps those controls to dedicated calibration tails on MS41.0,
MS41.1, and MS41.3. Paste the raw fragment directly only into an MS41.2 ID12
ROM element; use the builder output for every other firmware.

Rebuild the standalone file with:

```powershell
python build_patch_definitions.py --standalone
```

The legacy combined-definition mode remains available as
`python build_patch_definitions.py <source.xml> <output.xml>`; it injects the
Ignition Cut and Launch Control fragment into SS1v2 24 KB, ID12 24 KB, and
ID12 256 KB ROM blocks without changing other definitions. Its SS1v2 block is
automatically remapped to the current MS41.3 Launch addresses.

For a 256 KB definition, do not use the 24 KB addresses verbatim. The builder
maps each storage address with `fo(SA) = (0x10000 + SA) XOR 0x4000`; for
example, MS41.2 maps `0x352C -> 0x1752C`, while current MS41.3 maps its
dedicated Launch block `0x47E0 -> 0x107E0`.

Only use these tables after the matching patches are installed. The VANOS
entries match the patch-specific `VANOSRT1`/`VANOSRT2` markers. Ignition Cut and
Launch Control definitions match the firmware CAL ID because a 24 KB
calibration cannot prove that its program-region patch is installed; verify the
installed revision in BimmerStein first. All feature switch bytes are `0xFF` in
an unconfigured image, which leaves both features disabled.
Launch fuel mode continues to use the firmware's native Engine Speed Limiter
AT/MT, High Load, Resume Delay, and Hysteresis logic. `LC - Soft Cut RPM` starts
the staged injector cut and `LC - Hard Cut RPM` controls the full-cut boundary;
neither permanently rewrites the selected stock limiter tables. A hard value
below soft is clamped to soft, while raw `0xFF` uses soft + 96 RPM with
saturation. MS41.2 retains `0x352C-0x3533`, where ID12 has no overlapping
definition. MS41.3 Launch Control V5 uses the erased `0x47E0-0x47E7`
calibration tail and leaves all stock and custom boost-control calibrations
untouched, so current Launch and boost control may be configured together.

The released MS41.3 V4 used `0x352C-0x3533`. BimmerStein detects that revision
as deprecated: remove it before installing V5, then configure Launch again
through the current definition. Removal and installation preserve the old
bytes; if V4 was configured, review or restore the overlapping boost table once
during migration. This restriction does not apply to V5.
