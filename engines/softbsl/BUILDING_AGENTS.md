# Building the Soft-BSL RAM agents

The application ships three C166 RAM agents:

- `agent.hex`: AMD/JEDEC-command flash devices.
- `agent_28f.hex`: Intel 28F200-command flash devices.
- `eeprom_agent.hex`: chip-independent MS41 24C04 read/replay-safe byte service.

Their reviewed assembly sources, preprocessor, Ghidra assembly script, and exact
SHA-256 values are kept together in this directory. Text hashes normalize line
endings so Windows and Unix checkouts verify identically. `agent_manifest.json` binds
each runtime payload to its source. This avoids relying on an unpublished build
folder or accepting a payload merely because it has the expected size.

## Required tools

- Python 3.10 or newer.
- Ghidra 12 with the C166 processor extension, language
  `C166:LE:16:default`, and compiler specification `tasking`.

## Rebuild

For each source, first produce the syntax-normalized assembly:

```powershell
python engines/softbsl/preprocess_asm.py engines/softbsl/agent_build.asm build/agent_amd_ready.asm
python engines/softbsl/preprocess_asm.py engines/softbsl/agent_28f_build.asm build/agent_intel_ready.asm
python engines/softbsl/preprocess_asm.py engines/softbsl/eeprom_agent_build.asm build/agent_eeprom_ready.asm
```

Create a small dummy binary for Ghidra to load, then invoke `analyzeHeadless` with
the C166 language and the included script. The essential arguments are:

```text
<project-dir> <project-name> -import <dummy.bin>
-processor C166:LE:16:default -cspec tasking
-scriptPath engines/softbsl/tools
-postScript AssembleC166.java <ready.asm> <raw.hex>
-deleteProject
```

`raw.hex` is continuous hexadecimal text. Convert it to bytes and compare it
byte-for-byte with the decoded Intel HEX runtime file. A rebuild is accepted only
when all three comparisons are exact. The current reviewed results are:

| Family | Load address | Size | Runtime SHA-256 |
| --- | ---: | ---: | --- |
| AMD/JEDEC | `0xD800` | 1498 | `00eea04eae248f35f77140913bd27a0ffc0003251acd361db2ee80c4b336cb72` |
| Intel 28F200 | `0xD800` | 1464 | `5c35c219cf350f9dfd936be92907b2a44d9c52e0cb40d0831f805f49f8a418c2` |
| MS41 EEPROM | `0xD800` | 1442 | `e1c17e3a4e3684ab99f8d3ca98506a1829d37315028a00ff86a04c6f4ca3949f` |

The EEPROM agent contains no flash erase/program commands. It owns the complete
`0x000..0x1FF` 24C04 transaction directly and has no variant-specific EEPROM
routine call. Version 3 accepts one CRC-protected compare-before-write byte at a
time; an exact replay after a lost reply is idempotent. Field checks, ordering,
backups, and exact full-image verification remain host-owned. On exit it invokes
the common marker finalizer only when its saved entry state was `E740=1`; normal
states `0` and `3` are preserved.

The host uploads `stage1_payload.hex` at 9,600 baud, then lets that small loader
retune ASC0 and receive the EEPROM agent at the requested tier. `auto` tries
187,500 baud first and repeats the complete pre-write entry at 9,600 if the fast
path fails; no EEPROM byte is written during either entry attempt.

The current payloads retain their reviewed sizes and layout. Relative to the
previous payloads, the bank policy changes exactly one opcode in each agent:
`JMPR cc_NE,pc_bot` (`0x3D`) becomes `JMPR cc_UC,pc_bot` (`0x0D`). This lets the
same RAM-resident writer operate on either visible half while retaining the
existing boot-sector arm, CRC, erase/program, and verification paths. The
successful finalize path commits marker zero, arms `WDTCON=0xFF00` as a hardware
fallback, services it once, and executes protected `SRST`; all operational
watchdog servicing remains intact. The previous spin-until-watchdog payloads
(`7542ca...` AMD and `d335c9...` Intel) are deprecated after isolated emulator
validation plus 5/5 high-baud hardware trials on each flash family.

Run the repository-side integrity check after any source or payload change:

```powershell
python -m engines.softbsl.verify_agent_artifacts
```

Changing an agent requires rebuilding it, proving byte identity, reviewing the
hardware-facing change, and then deliberately updating the manifest. Do not edit
a manifest hash just to silence the verifier.
