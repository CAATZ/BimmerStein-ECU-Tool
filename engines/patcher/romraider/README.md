# RomRaider definitions for current patches

`BimmerStein MS41 Patch Definitions.xml` is a standalone RomRaider ECU
definition containing only calibrations introduced by BimmerStein patches:

- Ignition Cut V7 and Launch Control V4 for MS41.0 ID41, MS41.1 ID60,
  MS41.2 ID12, and MS41.3 SS1v2
- the VANOSRT1/MS41.0 and VANOSRT2/MS41.1 minimum-RPM retrofits

Both 24 KB calibration files and 256 KB full reads are covered. Code-only
patches are intentionally absent because they have no RomRaider calibration to
edit. AlphaN MAF-failsafe continues to use the standard SS1v2 AlphaN tables; it
does not add a calibration of its own.

`ms412_ignition_cut_v7_launch_control_v4.xml` contains the ten calibration
tables used by Ignition Cut V7 + Launch Control V4 at their MS41.2/MS41.3
addresses. The standalone builder remaps those controls to the dedicated blank
MS41.0 and MS41.1 calibration tails. Paste the fragment directly only into an
MS41.2 ID12 or MS41.3 SS1v2 ROM element.

Rebuild the standalone file with:

```powershell
python build_patch_definitions.py --standalone
```

The legacy combined-definition mode remains available as
`python build_patch_definitions.py <source.xml> <output.xml>`; it injects the
Ignition Cut and Launch Control fragment into SS1v2 24 KB, ID12 24 KB, and
ID12 256 KB ROM blocks without changing other definitions.

For a 256 KB definition, do not use the 24 KB addresses verbatim. The builder
maps each storage address with `fo(SA) = (0x10000 + SA) XOR 0x4000`; for
example, `0x2A65 -> 0x16A65`, `0x352C -> 0x1752C`, and `0x3533 -> 0x17533`.

Only use these tables after the matching patches are installed. The VANOS
entries match the patch-specific `VANOSRT1`/`VANOSRT2` markers. Ignition Cut and
Launch Control definitions match the firmware CAL ID because a 24 KB
calibration cannot prove that its program-region patch is installed; verify the
installed revision in BimmerStein first. All feature switch bytes are `0xFF` in
an unconfigured image, which leaves both features disabled.
Launch V4 fuel mode continues to use the firmware's native Engine Speed Limiter
AT/MT, High Load, Resume Delay, and Hysteresis logic. `LC - Soft Cut RPM` starts
the staged injector cut and `LC - Hard Cut RPM` controls the full-cut boundary;
neither permanently rewrites the selected stock limiter tables. A hard value
below soft is clamped to soft, while raw `0xFF` uses soft + 96 RPM with saturation.
The MS41.2/MS41.3 Launch block at `0x352C-0x3533` intentionally repurposes the naturally
aspirated firmware's unused boost knock-compensation cells; do not tune the old
overlapping boost table and these Launch tables at the same time.
