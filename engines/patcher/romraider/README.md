# RomRaider fragments for current patches

`ms412_ignition_cut_v7_launch_control_v4.xml` contains the ten calibration
tables used by Ignition Cut V7 + Launch Control V4. The 24 KB storage addresses
are identical for MS41.2 ID12 (`launch_control_v4_ms412`) and MS41.3 SS1v2
(`launch_control_v4`). Paste the fragment into the ROM element selected for the
patched calibration.

`build_patch_definitions.py` injects that fragment into SS1v2 24 KB, ID12
24 KB, and ID12 256 KB ROM blocks without changing other definitions. The
checked-in combined output is `2023 MS41 ECU Definitions - IgnitionCut V7 +
LaunchControl V4 - MS41.2+MS41.3.xml`.

For a 256 KB MS41.2 definition, do not use the 24 KB addresses verbatim. Map
each storage address with `fo(SA) = (0x10000 + SA) XOR 0x4000`; for example,
`0x2A65 -> 0x16A65`, `0x352C -> 0x1752C`, and `0x3533 -> 0x17533`. The supplied MS41.3 definition
family supports SS1v2 as a 24 KB calibration definition, not a 256 KB full-read
variant.

Only use these tables after the matching patches are installed. All switch
bytes are `0xFF` in an unconfigured image, which leaves both features disabled.
Launch V4 fuel mode continues to use the firmware's native Engine Speed Limiter
AT/MT, High Load, Resume Delay, and Hysteresis logic. `LC - Soft Cut RPM` starts
the staged injector cut and `LC - Hard Cut RPM` controls the full-cut boundary;
neither permanently rewrites the selected stock limiter tables. A hard value
below soft is clamped to soft, while raw `0xFF` uses soft + 96 RPM with saturation.
The Launch block at `0x352C-0x3533` intentionally repurposes the naturally
aspirated firmware's unused boost knock-compensation cells; do not tune the old
overlapping boost table and these Launch tables at the same time.
