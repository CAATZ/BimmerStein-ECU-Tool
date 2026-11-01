# MS41 patch calibration definitions

`BimmerStein MS41 Patch Definitions.xml` contains the calibration controls added
by the current BimmerStein patches:

- Ignition Cut V18 and Launch Control V18 for MS41.0, MS41.1, MS41.2 and MS41.3.
- Minimum-RPM VANOS controls for the supported MS41.0 and MS41.1 retrofits.

Both 24 KB calibration files and 256 KB full images are covered. Install the
matching firmware patch before editing its controls. Calibration bytes alone do
not identify an installed patch revision. Historical fragments are retained for
their matching older revisions.

**Ignition Cut and Launch Control are HIGHLY EXPERIMENTAL and remain untested on
a vehicle.** Software verification does not establish safe combustion, exhaust
or drivetrain behavior. Do not use ignition cut with catalytic converters.

The definition format retains the existing XML elements, scaling and inheritance
rules. Rebuild the standalone file from this directory:

```powershell
python build_patch_definitions.py --standalone
```

The builder remaps each family's calibration addresses. Use its complete output
for the target firmware; an MS41.2 fragment is not interchangeable with another
family's addresses. Code-only patches have no added calibration tables.

On MS41.3, current Launch and boost control may be configured together because
their calibration storage is separate. The old V4 launch revision reused part
of the boost table: review or restore that table when removing V4 and configure
Launch again with the matching current definition.
This boost-table restriction does not apply to V7, V8, V9, V10, or V11.
