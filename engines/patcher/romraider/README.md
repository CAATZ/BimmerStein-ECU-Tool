# RomRaider definitions for current patches

`BimmerStein MS41 Patch Definitions.xml` is a standalone RomRaider ECU
definition containing only calibrations introduced by BimmerStein patches:

- Ignition Cut V11 and Launch Control V11 for MS41.3 SS1v2
- Ignition Cut V10 and Launch Control V11 for MS41.0 ID41, MS41.1 ID60,
  and MS41.2 ID12
- the checksum-correct MS41.0 V2 (`VANOSRT3`) and MS41.1 (`VANOSRT2`)
  minimum-RPM retrofits

Both 24 KB calibration files and 256 KB full reads are covered. Code-only
patches are intentionally absent because they have no RomRaider calibration to
edit. AlphaN MAF-failsafe continues to use the standard SS1v2 AlphaN tables; it
does not add a calibration of its own.

`ms412_ignition_cut_v10_launch_control_v11.xml` contains the sixteen calibration
tables used by Ignition Cut V10 + Launch Control V11 at their MS41.2 addresses.
The builder remaps those controls to dedicated calibration tails on MS41.0,
MS41.1, and MS41.3. Paste the raw fragment directly only into an MS41.2 ID12
ROM element; use the builder output for every other firmware.
MS41.3 Ignition Cut V11 uses the same calibration controls as Ignition Cut V10;
the builder labels its blocks V11 without duplicating the fragment or changing
their addresses.
Launch V11 keeps V7/V8/V9/V10 calibration addresses and encodings and appends two
fuel hysteresis bytes. Its **Always (no switch)**
mode applies the same speed and TPS gates as clutch modes: arm only below Arm
Speed with TPS at or above Min TPS, retain an armed latch during rollout, and
release at or above Max Speed or below Min TPS. After release it can rearm only
below Arm Speed. Set Arm Speed to at least 1 km/h; 1 km/h arms only at a
reported integer speed of 0 km/h. Always ignores Clutch Polarity. Releasing a
clutch input retains the latch until the speed or throttle release condition.
Historical V7/V8/V9/V10 fragments remain available for those revisions; use the V11
fragment for the current patch.

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
entries match the patch-specific `VANOSRT3`/`VANOSRT2` markers. The deprecated
MS41.0 V1 marker `VANOSRT1` is intentionally not exposed for tuning; remove or
upgrade that revision first. Ignition Cut and
Launch Control definitions match the firmware CAL ID because a 24 KB
calibration cannot prove that its program-region patch is installed; verify the
installed revision in BimmerStein first. Installation preserves calibration and
does not apply a tuning preset. All feature switch bytes are `0xFF` in an
unconfigured image, which leaves both features disabled. Standalone Ignition
Cut and Launch ignition mode each have their own adjustable RPM hysteresis and
optional fixed injector pulse width. Raw `0xFF`/`0xFFFF` select zero hysteresis
and stock injection. If both spark requests are active, the Launch fixed-IPW
setting wins.
Launch fuel mode continues to use the firmware's native Engine Speed Limiter
AT/MT, High Load, Resume Delay, and Hysteresis logic. Effective soft RPM is the
lower of the native soft limit and `LC - Soft Cut RPM`, independently of
`LC - Hard Cut RPM`. A hard value below soft is allowed; fuel-return thresholds
still derive from effective soft RPM. The effective hard threshold is the
lower of the native hard limit and the requested Launch hard value; raw `0xFF`
requests soft + 96 RPM with saturation and the same native ceiling. These
settings retain native cut staging and do not permanently rewrite the stock
limiter tables. Launch fuel mode ignores `LC_HYST` and `LC_IPW`.
`LC - Fuel Hysteresis B (Gradual Recovery)` and `LC - Fuel Hysteresis A
(Immediate Clear)` independently override the corresponding native fuel
hysteresis while Launch fuel mode is armed. `0xFF` means **Follow stock**, using
the current tune's native setting, and leaves existing tuning unchanged. The
shared editor names this choice; RomRaider displays it as 8160 RPM. Custom
values are 0–8128 RPM in 32-RPM steps; zero is a literal zero, not Follow stock.
Gradual recovery starts below effective soft RPM minus B, while A clears the
limiter below effective soft RPM minus A; both thresholds floor at zero.
The immediate-clear check takes priority. A and B are independent, including
equal or reversed values, and retain the native stage/countdown behavior.
Native limiter protection takes priority over the Launch fuel-return settings.
Off and ignition modes ignore both fuel controls. Installation, removal, and
reinstallation preserve their stored values; no tuning preset is applied.
In ignition mode, spark cut starts at Soft Cut RPM and releases below soft minus
`LC_HYST`; raw `0xFF` or a hysteresis value at or above soft uses zero
hysteresis. Hard Cut RPM is ignored in ignition mode. MS41.2 retains
`0x352C-0x3538`, where ID12 has no overlapping definition. MS41.3 Launch
Control V11 uses the calibration tail `0x47E0-0x47EC`
and leaves all stock and custom boost-control calibrations
untouched, so current Launch and boost control may be configured together.

The released MS41.3 V4 used `0x352C-0x3533`. BimmerStein detects older Launch
revisions as deprecated. Exact V7/V8/V9/V10 installations can be upgraded directly
to V11 while retaining calibration addresses and settings. Correct any
enabled zero Arm Speed to at least 1 km/h; existing Always-mode speed/TPS
gates and speed-based latch release remain unchanged from V8. For V4, configure
Launch again through the current definition and review or restore the old
overlapping boost table.
This boost-table restriction does not apply to V7, V8, V9, V10, or V11.
