# MS41 RAM agent artifacts

The source tree supplies these C166 payloads:

| Payload | Purpose |
| --- | --- |
| `agent.hex` | AMD/JEDEC flash reads and writes |
| `agent_28f.hex` | Intel 28F200 flash reads and writes |
| `eeprom_agent.hex` | MS41 24C04 EEPROM reads and replay-safe byte writes |
| `st9030_agent.hex` | Fixed-slot co-processor diagnostics |

`agent_manifest.json` binds each payload to its assembly source and helper.
Source text hashes normalize line endings. The separate `stage1_manifest.json`
records the initial baud-switch loader.

Verify the supplied artifacts without communicating with an ECU:

```powershell
python -m engines.softbsl.verify_agent_artifacts
```

A rebuild uses the supplied assembly sources, `preprocess_asm.py` and
`tools/AssembleC166.java` with the `C166:LE:16:default` language and `tasking`
compiler specification. Compare assembled output byte-for-byte with the decoded
runtime files. Keep payload hashes and source hashes together; changing a
manifest alone does not validate a modified agent.

Source and offline checks do not replace powered testing. Previous hardware
results apply to the exact payload tested, not automatically to later revisions.
