# Isolated MS41.3 RAM ownership investigation

> This is generated research evidence, not current documentation or a production
> allocator. Its certificate applies only to the exact stock image and post-startup
> lifetime stated below.

## Inputs

| source | bytes | SHA-256 |
|---|---:|---|
| `C:\Users\crist\MS41 Projects\Decompilation\MS41.3\logger_ram_shinde1.json` | 15234 | `8e819576c1ad1e8656a276571399442b5b456e384f8c43eeaad60d3762c26565` |
| `C:\Users\crist\MS41 Projects\Decompilation\MS41.3\d_first32k.asm` | 624222 | `a8a886da1dbe3da762222a6c94aa6db960a131d7473401f7acee4bb8e55202be` |
| `C:\Users\crist\MS41 Projects\Decompilation\MS41.3\d_ph.asm` | 1796849 | `7ec009164d5493e8766fcda17c7194281f59f66b1a449a5e575fb882ca5ecbbe` |
| `C:\Users\crist\MS41 Projects\Decompilation\MS41.3\d_progtop.asm` | 469747 | `061f6cce6a6e9607ad307609b12b72972b46f6b8d632e408aa2fb8f60eb21c2e` |
| `C:\Users\crist\MS41 Projects\Flasher-Dev\engines\softbsl\agent_build.asm` | 40372 | `dba862b81a8f55870ba3509eea56117b013929b0a53db9d7e50408fa05e92737` |
| `C:\Users\crist\MS41 Projects\Flasher-Dev\experiments\ram_ownership\analyze.py` | 336676 | `0bdc67013afad22adad50540cf76c6b2f44df2b2c3931f72a71e031acdcc8b58` |
| `C:\Users\crist\MS41 Projects\Flasher-Dev\experiments\ram_ownership\trace_runtime.py` | 10099 | `7188dc3abd2e395488d8e85da2144bb933730773b0192896f4db8ff433938891` |
| `C:\Users\crist\MS41 Projects\Flasher-Dev\experiments\ram_ownership\ghidra\DumpFunctionBodies.java` | 1365 | `0c297f7a940a957e2eea8c436c600340b1a97faf422b6389db3803c0254086ff` |
| `C:\Users\crist\MS41 Projects\Flasher-Dev\experiments\ram_ownership\ghidra\RecoverDisasm.java` | 3292 | `08befb3a36716f7de33845c1a4c37fe08de1345d4cfa31ae0dd7ea269fdf3731` |
| `C:\Users\crist\MS41 Projects\Flasher-Dev\experiments\ram_ownership\evidence\ghidra_function_bodies.tsv` | 25523 | `b10fd23a18c4168ed27bbe46dab643825fc77667d1c7e02d7a29a156115340ce` |
| `C:\Users\crist\MS41 Projects\Flasher-Dev\experiments\ram_ownership\evidence\ghidra_recovered_code.asm` | 312605 | `b8f76c4b1c028c5a3cbd4c20211f0e0669c0b006e15b6a8a1913902f71cbf4c4` |
| `C:\Users\crist\MS41 Projects\Flasher-Dev\experiments\ram_ownership\evidence\verified_overlap.asm` | 249 | `230e2236f0eec81bc1ebcd8822860ab0ab663794bc07581d23554d046c0a71da` |
| `C:\Users\crist\MS41 Projects\Flasher-Dev\experiments\ram_ownership\evidence\ghidra_conservative_roots.asm` | 224788 | `45f8ca10adb9c059105d96fa1922011038aab83f4477375640a52fd3840c3d27` |
| `C:\Users\crist\MS41 Projects\Flasher-Dev\experiments\ram_ownership\evidence\ghidra_reachability_edges.asm` | 77720 | `6ab8d6a148d2c79ca6365184961b69242c868dd863f92732ba8cde58da2094e1` |
| `C:\Users\crist\MS41 Projects\Flasher-Dev\experiments\ram_ownership\evidence\ghidra_lower_computed_targets.asm` | 10131 | `755b48894889a4f30231c6560e74661b087494a274a853908dbae4dc00c69f61` |
| `C:\Users\crist\MS41 Projects\_shared\Datasheets\CPU - c166 - User Manual.pdf` | 7261477 | `c19afbe93afba4b93ba561d720556780674aca4728ec231a23b97b6210139d4b` |
| `C:\Users\crist\MS41 Projects\_shared\Datasheets\CPU - SAB80C166W-M-T3 - Datasheet.pdf` | 898639 | `672fbe595aa05ced61356b3d8a5f19a5779ed319fdedb33109ba328b7b6f22d3` |
| `C:\Users\crist\MS41 Projects\_shared\Datasheets\CPU - BSL - 80c166BSL - Guide.pdf` | 218050 | `f5f21d5197231f0249a779762ae72666f8b7365a122bcd159f0ab91de3427881` |
| `C:\Users\crist\MS41 Projects\Decompilation\MS41.3\ms41_ram_map.md` | 6263 | `a50126b959e7fc91ebf97fcbda539927a07f7057a4a5d4751b618685d0a1d9e4` |
| `C:\Users\crist\MS41 Projects\_shared\Hardware\GAL16V8_Reference.md` | 5147 | `bcdcb6d5ab199991043961b0e1858689ac7011aa93c1c452b46d033e376ee824` |
| `C:\Users\crist\MS41 Projects\_shared\Hardware\MS41_Hardware_Reference.xlsx` | 24378 | `7859d5829a60918545d830d69abd5ea77cc05a448fb9651589eaae5ae4f1a983` |
| `C:\Users\crist\ECU Emulator\tests\boot\boot_ram_footprint_ms41_3.json` | 24247 | `b74b3407c8b3031ac196da076fabde1c723549421e1cd1d9f3896aecaaa26cfa` |
| `C:\Users\crist\MS41 Projects\Flasher-Dev\experiments\ram_ownership\evidence\runtime_ram_footprint.json` | 47100 | `b78f40891da6eaa413f205cc54960542db146b427f2a8e3dc6fa3d3ba1e28458` |
| `C:\Users\crist\ECU Emulator\ms41emu\memory.py` | 10237 | `b6e16d7a482d0e88c0de2980b0bfdd52c78105d2fdda582aedff985cb9f57bd8` |
| `C:\Users\crist\ECU Emulator\ms41emu\isa\bits.py` | 17959 | `2009b85c3a5dbc42523823175f0074e6d568c1bf0875cb961becc065bfa7c3c8` |
| `C:\Users\crist\ECU Emulator\ms41emu\isa\moves.py` | 21886 | `3e189c429e939a6bd1c5848dd5c54f3f979059a2e800b8ec754391a5c5624d6b` |
| `C:\Users\crist\ECU Emulator\tests\diff.py` | 10495 | `b4d9ad8d80f9661d109d392bb7ec036a8a243a67548259e68957c553292d7326` |
| `C:\Users\crist\ECU Emulator\tests\golden\ram_ownership_bmov_0x4a.json` | 34444 | `5c242365447fd37e3b36e3f7d019123696d70920a0fe17ca54e40f2bbbdfcde6` |
| `C:\Users\crist\ECU Emulator\tests\golden\ram_ownership_movb_indirect_mem_0xa4.json` | 33619 | `14710b69bd1c9006b525e5088881e93bf15136502c41533022ec047b497df959` |
| `C:\Users\crist\MS41 Projects\Flasher-Dev\experiments\ram_ownership\evidence\oracle_bmov_0x4a.json` | 33173 | `0c11248f4e6237e0c9561a16cc74062a22d39aebb5f518a427776e7a267c3e6d` |
| `C:\Users\crist\MS41 Projects\Flasher-Dev\experiments\ram_ownership\evidence\oracle_movb_indirect_mem_0xa4.json` | 33013 | `4b4183b60929c76d811b299b3dd8c18e508ab26bde54e29d037abc9680781ada` |
| `C:\Users\crist\ECU Emulator\tests\boot\test_boot_ram_footprint.py` | 4045 | `54760067d974b13b6a60facb7fc5ebb07b9da109ace1e0589102369152b3938c` |
| `C:\Users\crist\ECU Emulator\tests\test_ds2.py` | 12182 | `5ba29589a3125b6be65eb65b005b96991f59c8bbd8a2f4528c7bea9a3921e0f5` |
| `C:\Users\crist\ECU Emulator\tests\test_flash.py` | 10047 | `378b048f4c0326e1e63ba31e970f2b3ff993bef1734f601a190cd6871605149b` |
| `C:\Users\crist\ECU Emulator\tests\test_fun_024670.py` | 3848 | `c97abc8d28fe52b7e664829e8751ad509cc7b809dd11e0d0d06447172247f000` |
| `C:\Users\crist\MS41 Projects\_shared\REF_MS41.3\MS41.3_s52_stock_fullread.bin` | 262144 | `89f31cbb70466ad39b23571e3c1c533251275182e8e2502c214a208e83f64487` |

## Post-startup certification

- Status: **conditionally certified for post-startup patch planning**
- Exact image SHA-256: `89f31cbb70466ad39b23571e3c1c533251275182e8e2502c214a208e83f64487`
- Proof: The conservative stock-live frontier is zero unresolved reads and zero unresolved writes. The certified gaps have no direct, resolved-indirect, bounded-indirect, logger, active-PEC, stack, context-bank, or post-startup lifetime claim; emulator scenarios touch them only during the independently bounded boot RAM test.
- Scope limit: This is an exact-image, post-startup ownership certificate, not a universal MS41-family allocator or a reset-safe RAM claim.

| certified range | bytes | word-aligned subrange | lifetime boundary |
|---|---:|---|---|
| `0xDB8F-0xDC1F` | 145 | `0xDB90-0xDC1F` | after the startup RAM self-test and main firmware handoff |
| `0xD800-0xD83F` | 64 | `0xD800-0xD83F` | after the startup RAM self-test and main firmware handoff |
| `0xE847-0xE85F` | 25 | `0xE848-0xE85F` | after the startup RAM self-test and main firmware handoff |

Conditions:

- Use only with this exact MS41.3 stock image and unchanged native control flow.
- Initialize and use the RAM only after the startup RAM self-test and main firmware handoff.
- Do not expect contents to survive reset; startup deliberately overwrites every certified byte.
- Do not write DPP3 or reconfigure PEC channels 3-6 or their pointer words.
- Re-run this analyzer after any native patch changes a hook, pointer invariant, scheduler path, stack boundary, or PEC configuration.
- Use the listed even-address subranges for 16-bit objects.

Explicitly excluded transient ranges:

- `0xDC20-0xDFFF`: stock authenticated DS2 memory-download target.
- `0xE000-0xE31F`: stock authenticated DS2 memory-download target and Soft-BSL chunk buffer.

## MS41.3 internal RAM map

- Status: **conditionally certified for exact-image post-startup use**
- Physical IRAM: `0xFA00-0xFDFF` = **1024 bytes**
- Normal stock/logger/implicit ownership: **925 bytes**
- Normal-runtime unclaimed: **99 bytes**
- Certified after startup: **95 bytes** (**94 word-aligned bytes**)
- Transient-only exclusion: **4 bytes**
- Stock static-access bytes: **890**
- Logger claims in IRAM: **22**
- Proof: All 1,024 bytes are classified. Normal stock/logger/implicit ownership accounts for the claimed bytes; the only normal-unclaimed bytes reconstruct as 0xFC3F-0xFC41 and 0xFD7C-0xFDDB. The current Soft-BSL CRC table excludes 0xFD7C-0xFD7F, leaving the two certified ranges with zero stock access, logger claim, stack/context/PEC claim, or post-startup runtime touch.

| certified IRAM range | bytes | word-aligned subrange | lifetime boundary |
|---|---:|---|---|
| `0xFD80-0xFDDB` | 92 | `0xFD80-0xFDDB` | after the startup internal-RAM self-test and main handoff |
| `0xFC3F-0xFC41` | 3 | `0xFC40-0xFC41` | after the startup internal-RAM self-test and main handoff |

- `0xFD7C-0xFD7F` is excluded: No stock-runtime owner, but the current Soft-BSL 16-word CRC table at 0xFD60-0xFD7F occupies these four otherwise-free bytes.

Conditions:

- Use only with the exact MS41.3 reference image and current loader contracts.
- Initialize only after the stock startup internal-RAM self-test and main handoff.
- Do not expect contents to survive reset.
- Do not change CP banks, SP/STKOV/STKUN, PEC configuration, or Soft-BSL scratch addresses.
- Use only the listed even-address subranges for 16-bit objects.
- Hardware-BSL coverage ends at its documented built-in register bank, stack, and 32-byte first stage; an arbitrary downloaded second stage may deliberately use any remaining IRAM.

### Context, stack, and loader closure

- Context banks: **proven for the exact stock image**. Every explicit stock CP write is either one of the enumerated immediate bases or the startup-table-derived 0xFA16/0xFA00 pair. POP CP sites only restore a CP previously saved by SCXT or an interrupt context, so the known-bank set is closed.
- Known CP bases: `0xFA00, 0xFA16, 0xFAE4, 0xFAE6, 0xFAE8, 0xFAF2, 0xFAF8, 0xFB00, 0xFB06, 0xFB20, 0xFB28, 0xFB48, 0xFB4C, 0xFB50, 0xFB52, 0xFB56, 0xFB5C, 0xFC00, 0xFC42, 0xFCAE, 0xFCCE`
- CP writes/restores: **28 explicit**, **36 POP restores**
- System stack: **proven for the exact stock image**, envelope `0xFB64-0xFBFF`. Startup sets STKOV=0xFB64, STKUN=0xFC00, and SP=0xFC00; there are no other explicit stock writes to these registers.
- Soft-BSL: The current agent resets SP to 0xFC00 and inherits the same bounds.
- Hardware BSL: Built-in BSL is separate: CP=0xFA00, SP=0xFA40, with the documented stack shown at 0xFA20-0xFA3F.

### Complete byte-class interval map

Every byte in `0xFA00-0xFDFF` appears exactly once below. Precise
instruction/function access records remain in the JSON evidence.

| range | bytes | status | normal-runtime owners | transient owners |
|---|---:|---|---|---|
| `0xFA00-0xFA15` | 22 | owned/reserved | r0 software-stack arena, stock static access | C166 hardware-BSL general-purpose register bank, Soft-BSL agent general-purpose register bank, startup internal-RAM self-test, stock startup general-purpose register bank at CP=0xFA00 |
| `0xFA16-0xFA1F` | 10 | owned/reserved | r0 software-stack arena, stock static access | C166 hardware-BSL general-purpose register bank, Soft-BSL agent general-purpose register bank, startup internal-RAM self-test, stock startup general-purpose register bank at CP=0xFA00, stock startup general-purpose register bank at CP=0xFA16 |
| `0xFA20-0xFA35` | 22 | owned/reserved | r0 software-stack arena, stock static access | C166 hardware-BSL system stack, startup internal-RAM self-test, stock startup general-purpose register bank at CP=0xFA16 |
| `0xFA36-0xFA3F` | 10 | owned/reserved | r0 software-stack arena, stock static access | C166 hardware-BSL system stack, startup internal-RAM self-test |
| `0xFA40-0xFA45` | 6 | owned/reserved | r0 software-stack arena, stock static access | C166 hardware-BSL first-stage code, startup internal-RAM self-test |
| `0xFA46-0xFA5F` | 26 | owned/reserved | stock static access | C166 hardware-BSL first-stage code, startup internal-RAM self-test |
| `0xFA60-0xFA89` | 42 | owned/reserved | stock static access | startup internal-RAM self-test |
| `0xFA8A-0xFA8B` | 2 | owned/reserved | PEC1 word destination, stock static access | startup internal-RAM self-test |
| `0xFA8C-0xFA93` | 8 | owned/reserved | stock static access | startup internal-RAM self-test |
| `0xFA94-0xFAA9` | 22 | owned/reserved | ADC PEC7 sample buffer, stock static access | startup internal-RAM self-test |
| `0xFAAA-0xFAE3` | 58 | owned/reserved | stock static access | startup internal-RAM self-test |
| `0xFAE4-0xFB63` | 128 | owned/reserved | CPU general-purpose register bank, stock static access | startup internal-RAM self-test |
| `0xFB64-0xFB7B` | 24 | owned/reserved | CPU general-purpose register bank, CPU system stack, stock static access | Soft-BSL agent system-stack envelope, startup internal-RAM self-test |
| `0xFB7C-0xFBFF` | 132 | owned/reserved | CPU system stack, stock static access | Soft-BSL agent system-stack envelope, startup internal-RAM self-test |
| `0xFC00-0xFC1F` | 32 | owned/reserved | CPU general-purpose register bank, stock static access | startup internal-RAM self-test |
| `0xFC20-0xFC3E` | 31 | owned/reserved | stock static access | startup internal-RAM self-test |
| `0xFC3F-0xFC41` | 3 | conditionally available after startup | - | startup internal-RAM self-test |
| `0xFC42-0xFC4F` | 14 | owned/reserved | CPU general-purpose register bank | startup internal-RAM self-test |
| `0xFC50-0xFC51` | 2 | owned/reserved | CPU general-purpose register bank, stock static access | startup internal-RAM self-test |
| `0xFC52-0xFC53` | 2 | owned/reserved | CPU general-purpose register bank, SHINDE1 logger claim, stock static access | startup internal-RAM self-test |
| `0xFC54-0xFC56` | 3 | owned/reserved | CPU general-purpose register bank, stock static access | startup internal-RAM self-test |
| `0xFC57-0xFC57` | 1 | owned/reserved | CPU general-purpose register bank | startup internal-RAM self-test |
| `0xFC58-0xFC61` | 10 | owned/reserved | CPU general-purpose register bank, stock static access | startup internal-RAM self-test |
| `0xFC62-0xFCAD` | 76 | owned/reserved | stock static access | startup internal-RAM self-test |
| `0xFCAE-0xFCED` | 64 | owned/reserved | CPU general-purpose register bank, stock static access | startup internal-RAM self-test |
| `0xFCEE-0xFD07` | 26 | owned/reserved | stock static access | startup internal-RAM self-test |
| `0xFD08-0xFD08` | 1 | owned/reserved | SHINDE1 logger claim, stock static access | startup internal-RAM self-test |
| `0xFD09-0xFD0D` | 5 | owned/reserved | stock static access | startup internal-RAM self-test |
| `0xFD0E-0xFD11` | 4 | owned/reserved | SHINDE1 logger claim, stock static access | startup internal-RAM self-test |
| `0xFD12-0xFD12` | 1 | owned/reserved | stock static access | startup internal-RAM self-test |
| `0xFD13-0xFD14` | 2 | owned/reserved | SHINDE1 logger claim, stock static access | startup internal-RAM self-test |
| `0xFD15-0xFD17` | 3 | owned/reserved | stock static access | startup internal-RAM self-test |
| `0xFD18-0xFD18` | 1 | owned/reserved | SHINDE1 logger claim, stock static access | startup internal-RAM self-test |
| `0xFD19-0xFD23` | 11 | owned/reserved | stock static access | startup internal-RAM self-test |
| `0xFD24-0xFD24` | 1 | owned/reserved | SHINDE1 logger claim, stock static access | startup internal-RAM self-test |
| `0xFD25-0xFD52` | 46 | owned/reserved | stock static access | startup internal-RAM self-test |
| `0xFD53-0xFD54` | 2 | owned/reserved | SHINDE1 logger claim, stock static access | startup internal-RAM self-test |
| `0xFD55-0xFD55` | 1 | owned/reserved | stock static access | startup internal-RAM self-test |
| `0xFD56-0xFD56` | 1 | owned/reserved | SHINDE1 logger claim, stock static access | startup internal-RAM self-test |
| `0xFD57-0xFD5B` | 5 | owned/reserved | stock static access | startup internal-RAM self-test |
| `0xFD5C-0xFD5D` | 2 | owned/reserved | SHINDE1 logger claim, stock static access | startup internal-RAM self-test |
| `0xFD5E-0xFD5F` | 2 | owned/reserved | stock static access | startup internal-RAM self-test |
| `0xFD60-0xFD7B` | 28 | owned/reserved | stock static access | Soft-BSL agent nibble CRC table, startup internal-RAM self-test |
| `0xFD7C-0xFD7F` | 4 | transient-owned; not certified | - | Soft-BSL agent nibble CRC table, startup internal-RAM self-test |
| `0xFD80-0xFDDB` | 92 | conditionally available after startup | - | startup internal-RAM self-test |
| `0xFDDC-0xFDDF` | 4 | owned/reserved | stock static access | startup internal-RAM self-test |
| `0xFDE0-0xFDE7` | 8 | owned/reserved | C166 PEC source/destination pointer workspace, stock static access | startup internal-RAM self-test |
| `0xFDE8-0xFDFB` | 20 | owned/reserved | C166 PEC source/destination pointer workspace | startup internal-RAM self-test |
| `0xFDFC-0xFDFF` | 4 | owned/reserved | C166 PEC source/destination pointer workspace, stock static access | startup internal-RAM self-test |

## Address-map working finding

- `0xF000-0xF1FF`: **external SRAM on the exact SAB80C166W**.
  Evidence: SAB80C166W datasheet PDF pp. 9 and 40: 1 KB on-chip RAM and one 512-byte SFR area at physical 0xFE00-0xFFFF; no ESFR block. Stock firmware uses 0xF018/0xF0C4 as high-traffic object bases.
  Boundary: Existing non-investigation memory-map/emulator region labels call this an ESFR overlay; those files were intentionally not edited.

### Retained BSL evidence audit

- Claim reviewed: 0xF000-0xF1FF is hidden by an ESFR overlay, reportedly observed by a 2026-06-23 BSL write/readback sweep.
- Result: **not independently hardware-verified from retained artifacts**.
- Retained: Narrative conclusions and the BSL monitor setup recipe. GAL pinout/equations and an 8 KB multi-block SRAM /CE decode.
- Missing: Per-address pre-write, write, and post-write readback results. A raw sweep log/capture and an alias/overlay comparison.
- Working classification: The exact SAB80C166W datasheet, GAL SRAM decode, and dense native firmware use support external SRAM at 0xF000-0xF1FF.
- Safety effect: None: 0xF000-0xF1FF remains rejected for patches because native firmware owns it densely.

## Static coverage

- Instructions scanned: **53068**
- Direct mapped-memory accesses: **18793**
  - Stock-reachable: **8607**
- Exact indirect logical-address accesses: **12**
  - Stock-reachable: **5**
- Unresolved indirect accesses: **2799**
  - Stock-reachable: **1187**
  - Bounded read sites: **532**
  - Collision-relevant unbounded sites: **0**
  - Writes or read/writes: **1182**
  - Canonical `[-r0]` stack writes: **424**
  - Other unresolved writes: **758**
    - Bounded by proven pointer value sets: **455**
    - Still unbounded: **303**
      - Proven non-executable low-image sites: **2**
      - Stock-unreachable high-segment sites: **301**
      - Collision-relevant sites: **0**
  - Plain pointers: **1490**
  - Indexed pointers: **1309**
  - Outside named decompiler functions: **1760**
- Logger address claims: **91**

| assembly source | instructions | range |
|---|---:|---|
| `d_first32k.asm` | 9566 | `0x000000-0x0062FC` |
| `d_ph.asm` | 33709 | `0x020000-0x039FFE` |
| `d_progtop.asm` | 8966 | `0x03A000-0x03FFFC` |
| `ghidra_recovered_code.asm` | 5473 | `0x00004E-0x03BE38` |
| `verified_overlap.asm` | 1 | `0x036E9A-0x036E9A` |
| `ghidra_conservative_roots.asm` | 3932 | `0x00736A-0x03BE38` |
| `ghidra_reachability_edges.asm` | 1356 | `0x023A50-0x03D7F4` |
| `ghidra_lower_computed_targets.asm` | 225 | `0x0000DC-0x007848` |

### Proven non-executable low-image bytes

- Range `0x004200-0x00422F`: **proven non-executable in the stock image**
  - Evidence: Literal/table bytes between the terminating vector JMPS at 0x0041FC and the copied flash-driver entry at 0x004230.
  - Limit: Stock control flow and decoded dispatch tables only; the copied flash-driver source begins at the exclusive end.

### Conservative lower-segment roots

- Status: **proven for the stock image**
- Full-image scan: `0x000000-0x03FFFF at every even byte`
- `CALLS`/`JMPS` patterns: **390**; unique decoded lower targets: **186**
- Proof: Every possible even-aligned CALLS/JMPS byte pattern in the exact 256 KiB image is scanned. Every aligned target inside the immutable lower code/thunk extent 0x000000-0x0062FF is decoded.

### Conservative high-segment reachability

- Status: **proven for the stock image**
- Lower-flash scan: `0x000000-0x01FFFF at every even byte`
- `CALLS`/`JMPS` patterns: **92**; unique high targets: **67**
- Decoded JMPI table targets: **518**
- High instructions: **42774** decoded; **15700** reachable; **27074** unreachable
- Proof: Every possible even-aligned CALLS/JMPS byte pattern in the lower 128 KiB is rooted, including false positives in data. Segment-2/3 JMPI tables are decoded completely and those segments contain no CALLI.
- Limit: Stock image and immutable dispatch data only; corrupted stack/dispatch state or modified firmware is outside this reachability gate.

### Stock startup RAM self-test

- Immutable boundary table: `0xA300=0xFDFE, 0xA302=0xD800, 0xA304=0xDBFE, 0xA306=0xE420, 0xA308=0xF7FE, 0xA30A=0xDC00, 0xA30C=0xE41E, 0xA30E=0xD080, 0xA310=0xD0FF, 0xA312=0xF7F2, 0xA314=0xFA00`
- Exact-image variant marker `0xA000`: `0x00`
- Proven destructive-test envelopes: `0xD080-0xD0FF, 0xD800-0xF7F3, 0xFA00-0xFDFF`
- Lifetime: reset/startup before the main firmware handoff.

## Emulator/oracle boot gate

- Source: `ghidra-oracle`
- Execution: `0x430` to `0x924` in **82872** instructions
- Frozen window snapshots: `0xD000-0xFB63, 0xFC00-0xFDFF`
- Non-`0xFF` bytes at the handoff: **9176**
- Excluded transient stack: `0xFB64-0xFBFF`

This proves the emulator reproduces the oracle's reset-to-main RAM snapshot.
It is boot-lifetime evidence, not a steady-state free-space declaration.

### Interpretation limits

- Indirect results are 16-bit logical addresses. The exact-image DPP3
  invariant below proves the stock E/F logical-to-physical mapping.
- Absolute/far CALL/JMP targets are raw CPU addresses and are normalized
  to full-read file addresses with low-word A14 XOR. Relative
  targets and all reported instruction PCs remain file addresses.
- **1187** stock-reachable accesses are not locally constant by straight-line
  propagation; all are nevertheless classified by bounded pointer, stack,
  or startup rules, leaving a zero collision-relevant frontier.
- Static operands do not expose CPU GPR banks, stack writes, or PEC payload
  transfers; the separately reconstructed claims are included in the gap gate.

### Pointer reachability bound

- Domain: 16-bit logical address before DPP translation.
- Result: No collision-relevant unbounded pointer remains. Every stock-live indirect access is classified as a bounded native value set, canonical stack access, or startup RAM-test access.
- Certified exclusion: 2 decoded write sites are rejected inside proven non-executable low-image bytes.
- Certified exclusion: 301 write sites are rejected as unreachable by the conservative stock high-segment gate.
- Certified exclusion: 40 startup RAM self-test accesses end before the main firmware handoff.
- Certified exclusion: Canonical r0 stack traffic is confined to reserved 0xFA00-0xFA45.
- Certified exclusion: Single-owner FUN_02b0cc r9 writes use base 0xF018 or 0xF0C4.
- Certified exclusion: Thirteen FUN_02b0cc r4 writes stay inside 0xF042-0xF16D.
- Certified exclusion: All fourteen FUN_02b0cc r8 writes stay inside 0xF01E-0xF0F9.
- Certified exclusion: Single-owner FUN_020986 r5 writes stay inside 0xE865-0xE964.
- Certified exclusion: Six FUN_0044e6 r5 writes stay inside 0xE523-0xE846.
- Certified exclusion: The FUN_00098a r5 write stays inside 0xE523-0xE621.
- Certified exclusion: FUN_0044e6 r2 RAM-test writes stay inside the stock test ranges.
- Certified exclusion: FUN_0357d2 r2/r4 writes stay inside 0xEA24-0xFC3D.
- Certified exclusion: FUN_001bcc r15 writes stay inside 0xFA46-0xFA5D.
- Certified exclusion: FUN_028100 r9 writes stay inside 0xF018-0xF0F7.
- Certified exclusion: FUN_02218a/FUN_022998 r2 writes stay inside 0xF3FA-0xFC3D.
- Certified exclusion: FUN_027b1c r7 writes stay inside 0xE523-0xE720.
- Certified exclusion: FUN_02ada4/FUN_02aeb8 r8 writes stay inside 0xF182-0xF193.
- Certified exclusion: FUN_0352c0 r14/r4 writes stay inside 0xF63E-0xFC3C.
- Certified exclusion: The paired-object updater r8 writes stay inside 0xF024-0xF0D3.
- Certified exclusion: FUN_02c024 r2 writes stay inside 0xF63F-0xFC3D.
- Certified exclusion: FUN_036128 r5 writes stay inside 0xF748-0xF847.
- Certified exclusion: FUN_0362b4 r4 writes stay inside 0xF748-0xF847.
- Certified exclusion: FUN_020a26 r9/r4 writes stay inside 0xE52E-0xE551.
- Certified exclusion: FUN_020f5a r5 writes stay inside 0xE523-0xE624.
- Certified exclusion: Three FUN_0044e6 r4 writes stay inside 0xE523-0xE643.
- Certified exclusion: Two FUN_0044e6 r0 writes stay inside the stock RAM-test ranges.
- Certified exclusion: Sixty-eight immediate MOVBZ-derived write bases are locally bounded.
- Certified exclusion: FUN_02e52e r5 writes stay inside 0xE9A8-0xEBA7.
- Certified exclusion: FUN_0314a8 r5 writes use only the five even bases 0x0002-0x000A.
- Certified exclusion: FUN_02ff78 r9 writes stay inside the two native object records.
- Certified exclusion: The object metric updater r8 writes stay inside 0xF02A-0xF0D7.
- Certified exclusion: The two-record updater writes stay inside 0xEFB4-0xEFC5.
- Certified exclusion: FUN_024e30 ring/countdown writes stay inside 0xE865-0xE8A4 and 0xF770-0xF7F1.
- Certified exclusion: The stock flash-orchestrator scan write stays inside 0xE428-0xE527.
- Certified exclusion: The low/high serial-buffer writers stay inside their proven native transfer buffers.
- Certified exclusion: Locally bounded state-clear, bitset, record, ISR, and flash-copy loops stay inside their explicit native arrays.

### DPP3 boundary

- Status: **proven for the exact stock image**
- Reset page: `0x0003`
- Post-startup stock-live writes: **0**
- Proof: The exact firmware has no stock-live direct, resolved-indirect, or bounded-indirect write to DPP3, and no collision-relevant unbounded access remains. DPP3 therefore stays on reset page 3.
- Manual evidence: C166 user manual PDF page 263 (printed B-4): DPP3 at 0xFE06 resets to 0x0003; PDF page 123 states SFRs reside in data page 3.
- Limit: A patch that writes DPP3 invalidates this logical-to-physical address proof and must be re-audited.

## Emulator scenario RAM footprints

- Completed cases: **53**
- Unsupported direct cases: **0**
- Scope: existing emulator tests plus sliced post-boot ISR bodies; pytest setup/assertion accesses are included, direct-case preloads are excluded, and unsupported slices contribute only the prefix executed before failure
- Certification: An untouched byte is not certified free.

| scenario | complete | unsupported | read bytes | write bytes | touched bytes | touched ranges |
|---|---:|---:|---:|---:|---:|---|
| adc_timer_isr | 2 | 0 | 29 | 19 | 31 | `0xFA9C-0xFA9F, 0xFAAA-0xFAAE, 0xFAB0-0xFAB4, 0xFAB7-0xFAB7, 0xFBF8-0xFBFB, 0xFC50-0xFC51, 0xFCAC-0xFCAD, 0xFD00-0xFD03, 0xFD22-0xFD23, 0xFDFE-0xFDFF` |
| boot | 8 | 0 | 9204 | 9204 | 9204 | `0xD800-0xF7F3, 0xFA00-0xFDFF` |
| ds2 | 6 | 0 | 91 | 91 | 95 | `0xE420-0xE426, 0xE520-0xE54D, 0xE64C-0xE64E, 0xE650-0xE653, 0xE658-0xE658, 0xE65A-0xE65B, 0xE65E-0xE65E, 0xE72C-0xE72D, 0xE730-0xE731, 0xE746-0xE74B, 0xE75C-0xE766, 0xFBF8-0xFBFF, 0xFDDE-0xFDDF` |
| feature_dispatch | 3 | 0 | 6 | 16 | 16 | `0xE800-0xE809, 0xFD22-0xFD23, 0xFD5A-0xFD5D` |
| flash | 17 | 0 | 190 | 238 | 238 | `0xE320-0xE401, 0xE656-0xE657, 0xE73A-0xE73D, 0xE741-0xE742, 0xE744-0xE745, 0xE800-0xE801` |
| pec0_config_isr | 14 | 0 | 5 | 8 | 9 | `0xE64A-0xE64A, 0xFD02-0xFD03, 0xFDDC-0xFDDD, 0xFDE0-0xFDE3` |
| pin_latch_isr | 2 | 0 | 2 | 2 | 2 | `0xFD60-0xFD61` |
| ssc_rx_isr | 1 | 0 | 3 | 4 | 5 | `0xF6E2-0xF6E2, 0xF6FC-0xF6FC, 0xF70F-0xF70F, 0xFD02-0xFD03` |

Runtime limits:

- Interrupt scheduling is not modeled; body slices are invoked explicitly.
- SCXT prologues and POP/RETI epilogues are excluded and their CP/DPP context is seeded.
- The ADC slice starts at its common post-status path; the PEC eligibility guard is skipped and its pending profile bit is seeded.
- PEC payload transfers are hardware events and are not executed by these body slices.
- The emulator does not model CP-window register-bank aliasing.
- Unsupported opcodes and operands are recorded, never guessed or counted as complete.

## Region evidence

| region | range | static accesses | covered bytes | logger claims |
|---|---|---:|---:|---:|
| unmapped | `0xC000-0xCFFF` | 0 | 0 | 0 |
| CAN controller | `0xD000-0xD7FF` | 91 | 59 | 0 |
| external SRAM | `0xD800-0xF7FF` | 16520 | 6164 | 67 |
| unmapped | `0xF800-0xF9FF` | 476 | 68 | 0 |
| internal RAM | `0xFA00-0xFDFF` | 2847 | 702 | 22 |
| SFR | `0xFE00-0xFFFF` | 2159 | 169 | 2 |

## Lifetime-specific ownership

| range | owner | lifetime | evidence |
|---|---|---|---|
| `0xD080-0xD0FF` | startup byte RAM self-test | reset/startup before the main firmware handoff | `d_first32k.asm 0x004642-0x0046BE; REF table 0xA30E/0xA310` |
| `0xD800-0xF7F3` | startup word RAM self-test | reset/startup before the main firmware handoff | `d_first32k.asm 0x0045BC-0x004640; REF table 0xA302-0xA312` |
| `0xFA00-0xFDFF` | startup internal-RAM self-test | reset/startup before the main firmware handoff | `d_first32k.asm 0x0044C8-0x0045A8; REF table 0xA300/0xA314` |
| `0xFA00-0xFA1F` | stock startup general-purpose register bank at CP=0xFA00 | startup internal-RAM test | `d_first32k.asm 0x004534; exact table word 0xA314=0xFA00` |
| `0xFA16-0xFA35` | stock startup general-purpose register bank at CP=0xFA16 | startup internal-RAM test | `d_first32k.asm 0x0044B0-0x0044C0; 0xA314+0x16` |
| `0xFA00-0xFA1F` | C166 hardware-BSL general-purpose register bank | built-in hardware bootstrap loader | `80C166 BSL guide PDF page 8: CP=0xFA00` |
| `0xFA20-0xFA3F` | C166 hardware-BSL system stack | built-in hardware bootstrap loader | `80C166 BSL guide PDF pages 8 and 17: SP=0xFA40 and map` |
| `0xFA40-0xFA5F` | C166 hardware-BSL first-stage code | 32-byte hardware-bootstrap receive and execution | `SAB80C166W datasheet PDF page 21; 80C166 BSL guide PDF page 5` |
| `0xFA00-0xFA1F` | Soft-BSL agent general-purpose register bank | current repository Soft-BSL agent owns the CPU | `engines/softbsl/agent_build.asm line 60: CP=0xFA00` |
| `0xFB64-0xFBFF` | Soft-BSL agent system-stack envelope | current repository Soft-BSL agent owns the CPU | `agent_build.asm line 59 sets SP=0xFC00 and inherits the stock STKOV=0xFB64/STKUN=0xFC00 bounds` |
| `0xFD60-0xFD7F` | Soft-BSL agent nibble CRC table | current repository Soft-BSL agent owns the CPU | `engines/softbsl/agent_build.asm line 36: NIBTBL=0xFD60` |
| `0xDC20-0xE31F` | stock authenticated DS2 memory-download target | diagnostic command 0x00 write/verify | `d_ph.asm 0x02080E-0x02086E` |
| `0xE000-0xE3FF` | Soft-BSL chunk buffer | Soft-BSL agent owns the CPU; interrupts are disabled | `engines/softbsl/agent_build.asm BUF` |
| `0xE320-0xE41F` | stock RAM-resident flash driver copy | stock erase/program preparation and RAM execution | `d_first32k.asm 0x005082-0x005098 and 0x005202-0x005216` |
| `0xE420-0xE528` | stock diagnostic flash protocol state and scan buffer | stock erase/program request handling | `d_first32k.asm 0x004F16-0x005420; indexed fill at 0x0051AC-0x0051B0` |

## Implicit CPU/hardware RAM ownership

| range | owner | lifetime | evidence |
|---|---|---|---|
| `0xFA00-0xFA45` | r0 software-stack arena | normal/shared-r0 firmware contexts | `r0=0xFA46 at d_first32k.asm 0x0046D8; descending [-r0] traffic; exact-part internal RAM begins at 0xFA00` |
| `0xFB64-0xFBFF` | CPU system stack | normal firmware runtime | `d_first32k.asm 0x004468-0x004470; C166 manual pp. 3-4/B-4` |
| `0xFDE0-0xFDFF` | C166 PEC source/destination pointer workspace | hardware event transfers | `d_ph.asm PEC pointer writes; C166 PEC architecture` |
| `0xF6F7-0xF70E` | SSC receive ring buffer | normal SSC receive interrupts | `d_ph.asm 0x039790-0x0397A6; index is masked to 0x1F and accepted through 0x17` |
| `0xE520-0xE61E` | PEC2 variable-length byte source envelope | PEC2 active; runtime count is 0xE521, source increments from 0xE520 | `d_ph.asm 0x02762C-0x027644` |
| `0xE621-0xE724` | PEC0 transfer-profile envelope | PEC0 diagnostic profile active; includes runtime-count destination at 0xE626 | `d_ph.asm 0x0396DE-0x039764` |
| `0xF6DE-0xF6DF` | PEC0 two-byte incrementing destination | PEC0 control 0x0302 active | `d_ph.asm 0x039568-0x039578` |
| `0xF6E2-0xF6F6` | PEC0 fixed transfer profiles | PEC0 controls 0x0302/0x0304/0x0504/0x0507 active | `d_ph.asm 0x0395B4-0x03965E` |
| `0xF710-0xF725` | PEC0 fixed incrementing destinations | PEC0 controls 0x0305/0x030C active | `d_ph.asm 0x039674-0x0396CC` |
| `0xF727-0xF731` | PEC0 eleven-byte incrementing source | PEC0 control 0x050B active | `d_ph.asm 0x039704-0x039714` |
| `0xFA8A-0xFA8B` | PEC1 word destination | PEC1 control 0x0001 active | `d_ph.asm 0x038E7E-0x038E8E` |
| `0xFA94-0xFAA9` | ADC PEC7 sample buffer | PEC7 active: 11 incrementing word transfers | `d_ph.asm 0x02C6BC-0x02C6CC and 0x038A60-0x038A70` |
| `0xFAE4-0xFB03` | CPU general-purpose register bank | while CP=0xFAE4 | `0x038A42` |
| `0xFAE6-0xFB05` | CPU general-purpose register bank | while CP=0xFAE6 | `0x038A88` |
| `0xFAE8-0xFB07` | CPU general-purpose register bank | while CP=0xFAE8 | `0x038ACE` |
| `0xFAF2-0xFB11` | CPU general-purpose register bank | while CP=0xFAF2 | `0x0399C6` |
| `0xFAF8-0xFB17` | CPU general-purpose register bank | while CP=0xFAF8 | `0x038C54` |
| `0xFB00-0xFB1F` | CPU general-purpose register bank | while CP=0xFB00 | `0x001592` |
| `0xFB06-0xFB25` | CPU general-purpose register bank | while CP=0xFB06 | `0x0000DC, 0x000EFA, 0x02BFDA` |
| `0xFB20-0xFB3F` | CPU general-purpose register bank | while CP=0xFB20 | `0x038F96, 0x0394F0, 0x0397C6, 0x0397E0` |
| `0xFB28-0xFB47` | CPU general-purpose register bank | while CP=0xFB28 | `0x038FC8` |
| `0xFB48-0xFB67` | CPU general-purpose register bank | while CP=0xFB48 | `0x039464` |
| `0xFB4C-0xFB6B` | CPU general-purpose register bank | while CP=0xFB4C | `0x03952E` |
| `0xFB50-0xFB6F` | CPU general-purpose register bank | while CP=0xFB50 | `0x03978A` |
| `0xFB52-0xFB71` | CPU general-purpose register bank | while CP=0xFB52 | `0x039844` |
| `0xFB56-0xFB75` | CPU general-purpose register bank | while CP=0xFB56 | `0x0398B0` |
| `0xFB5C-0xFB7B` | CPU general-purpose register bank | while CP=0xFB5C | `0x039906` |
| `0xFC00-0xFC1F` | CPU general-purpose register bank | while CP=0xFC00 | `0x004430, 0x004474, 0x0046C2` |
| `0xFC42-0xFC61` | CPU general-purpose register bank | while CP=0xFC42 | `0x030D38` |
| `0xFCAE-0xFCCD` | CPU general-purpose register bank | while CP=0xFCAE | `0x03936C` |
| `0xFCCE-0xFCED` | CPU general-purpose register bank | while CP=0xFCCE | `0x03940C` |

### PEC reconstruction

- Complete direct configurations observed for channels: `0, 1, 2, 7`
- Pointer workspace: `0xFDE0-0xFDFF`
- Channels 3-6: **proven for the exact stock image**
- Proof: Channels 3-6 reset inactive and have no post-startup stock-live write to their PECC control or source/destination pointer words.
- Manual evidence: C166 user manual PDF page 265 (printed B-6) gives PECC3-PECC6 reset value 0x0000; PDF page 27 states that when the transfer counter reaches zero, service proceeds through the standard interrupt rather than a PEC transfer.
- Limit: Active-channel RAM envelopes remain reserved. Reprogramming any PEC channel requires a new ownership audit.
  - PEC3: control `0xFEC6`, pointers `0xFDEC`/`0xFDEE`, no post-startup writes.
  - PEC4: control `0xFEC8`, pointers `0xFDF0`/`0xFDF2`, no post-startup writes.
  - PEC5: control `0xFECA`, pointers `0xFDF4`/`0xFDF6`, no post-startup writes.
  - PEC6: control `0xFECC`, pointers `0xFDF8`/`0xFDFA`, no post-startup writes.

## Candidate gate

| range | status | direct bytes | indirect bytes | logger bytes | unresolved indirect | offset hints | note |
|---|---|---:|---:|---:|---:|---:|---|
| `0xE000-0xE31F` | shared transient ownership: not exclusive patch RAM | 0 | 0 | 0 | 0 | 0 | Also occupied by the Soft-BSL 0xE000-0xE3FF buffer while its agent owns the CPU. |
| `0xE74E-0xE7FF` | rejected: native static/logger ownership | 7 | 178 | 0 | 0 | 0 | Native low-firmware routines access 0xE74E-0xE750 and 0xE75C-0xE75F. |
| `0xF000-0xF1FF` | rejected: native static/logger ownership | 242 | 512 | 25 | 0 | 0 | Exact-part external SRAM, but densely owned by native per-bank objects. |
| `0xF200-0xF7FF` | rejected: native static/logger ownership | 656 | 1516 | 4 | 0 | 0 | Broad prior candidate; expected to contain native firmware state. |

## Ranked ordinary static gaps

These are maximal gaps in ordinary direct/resolved/logger/implicit claims.
They are split at transient-owner boundaries. Only rows explicitly marked
**certified free after startup handoff** are patch-planning claims.

| rank | range | bytes | status | word-aligned | offset hints | emulator scenarios | transient owners |
|---:|---|---:|---|---|---:|---|---|
| 1 | `0xDC34-0xDFFF` | 972 | transiently claimed | `-` | 0 | boot | startup word RAM self-test, stock authenticated DS2 memory-download target |
| 2 | `0xE000-0xE31F` | 800 | transiently claimed | `-` | 0 | boot | startup word RAM self-test, stock authenticated DS2 memory-download target, Soft-BSL chunk buffer |
| 3 | `0xDB8F-0xDC1F` | 145 | certified free after startup handoff | `0xDB90-0xDC1F` | 0 | boot | startup word RAM self-test |
| 4 | `0xD800-0xD83F` | 64 | certified free after startup handoff | `0xD800-0xD83F` | 0 | boot | startup word RAM self-test |
| 5 | `0xE847-0xE85F` | 25 | certified free after startup handoff | `0xE848-0xE85F` | 0 | boot | startup word RAM self-test |

### Collision-relevant unresolved pointer hotspots

- Remaining sites: **0**

| function | base register | accesses |
|---|---|---:|

### Named-pointer value-set investigations

| function/base | candidate bases | implied access envelopes | status |
|---|---|---|---|
| `FUN_02cd98 r9` | `0xF018, 0xF0C4` | `0xF053-0xF0BB, 0xF0FF-0xF167` | closed by the guarded 0xF596 pointer-slot fixed point |
| evidence | d_ph.asm 0x02F1D4-0x02F1EA assigns only 0xF0C4 or 0xF018 through the direct 0xF596 pointer slot. After A14 normalization, CALLS 0x028D7E at file 0x02D096/0x02D0A4 enters file 0x02CD7E, whose wrapper loads r9 from 0xF596 at 0x02CD86. CALLS 0x02CD98 at file 0x029796/0x029A66 instead targets file 0x028D98. | | |
| `FUN_02b0cc r9` | `0xF018, 0xF0C4` | `0xF01A-0xF06D, 0xF0C6-0xF119` | stock value set resolved; native ownership envelope |
| evidence | The stock far-pointer table enters file 0x02F190, 0x02B03C, or 0x02B356 (reference file offsets 0x6458, 0x6510, 0x6514). The r9 paths select 0xF018/0xF0C4 at file 0x02B054/0x02B05A or 0x02F20A/0x02F220; file 0x02F252 passes that value through r12 to the normalized file entry at 0x02F0CC. | | |
| `FUN_02b0cc r4` | `r9 plus local constant` | `0xF042-0xF16D` | closed by the current entry gates and proven pointer rules |
| evidence | Thirteen sites derive r4 locally from the already-proven FUN_02b0cc r9 value set; file 0x02F3A8 is additionally gated by a comparison proving 0xEF60 equals r9 before adding 0x35. The r8-derived sites are reached only through calls passing r12=r9. | | |
| `FUN_02b0cc r8` | `0xF018, 0xF0C4` | `0xF01E-0xF0F9` | all fourteen r8 write sites resolved |
| evidence | All three stock entry envelopes are direct-call-only, every call is immediately preceded by mov r12,r9, and each entry copies r12 to r8. | | |
| `FUN_020986 r5` | `0x0000-0x00FF` | `0xE865-0xE964` | full byte-derived value set resolved; native ring-buffer envelope |
| evidence | Each of the ten writes at file 0x024B14-0x024CCE immediately loads r5 with MOVBZ from byte 0xFAC2, proving r5 is 0x0000-0x00FF regardless of the byte's producer. | | |
| `FUN_0044e6 r5` | `0x0000-0x00FF, 0xE650 pointer slot at file 0x005630` | `0xE523-0xE624, 0xE747-0xE846` | closed by the guarded 0xE650 pointer-slot fixed point |
| evidence | Six startup-helper writes at file 0x0049CC-0x004D42 derive r5 through MOVBZ from a byte. The seventh, file 0x005630, loads r5 from the mutable word slot 0xE650. | | |
| `FUN_0044e6 r2` | `0xD080-0xD0FF, 0xD800-0xF7F2, 0xFA00-0xFDFE` | `0xD080-0xD0FF, 0xD800-0xF7F3, 0xFA00-0xFDFF` | 18 startup RAM-test pointer sites resolved |
| evidence | The exact stock image's immutable table at logical 0xA300-0xA314 supplies every startup RAM-test boundary used at file 0x0044C8-0x0046BE. | | |
| `FUN_0357d2 r2` | `6 * zero-extended byte index` | `0xF63E-0xFC3D` | all ten r2 write sites resolved |
| evidence | Every site rebuilds r2 from the low byte of r8 with MOVBZ, then computes r2 = index * 6 before the fixed 0xF63E-0xF643 offset. | | |
| `FUN_0357d2 r4` | `fixed base plus bounded byte index * 6 or * 12` | `0xEA24-0xFC3D` | all seven r4 write sites resolved |
| evidence | Six sites use 0xF642/0xF643 plus a MOVBZ-bounded byte index * 6; file 0x035A82 uses 0xEA24 plus the MOVBZ-bounded RL6 index * 12. | | |
| `FUN_001bcc r15` | `0xFA46, 0xFA52` | `0xFA46-0xFA5D` | all ten r15 write sites resolved |
| evidence | File 0x001D68 loads r15=0xFA52; the only alternate assignment at 0x001D74 changes it to 0xFA46 before all ten writes. | | |
| `FUN_028100 r9` | `0xF018, 0xF0C4` | `0xF018-0xF0F7` | all eight r9 write sites resolved |
| evidence | The only stock entry to file 0x0280B4 is called at 0x02F312 immediately after mov r12,r9; the entry copies r12 into r9. | | |
| `FUN_02218a r2` | `6 * zero-extended byte index` | `0xF63E-0xFC3D` | all six r2 write sites resolved |
| evidence | Each site rebuilds r2 from MOVBZ-bounded RL6 and computes r2 = index * 6 before a fixed 0xF63E-0xF643 offset. | | |
| `FUN_022998 r2` | `6 * zero-extended byte index` | `0xF3FA-0xF9F9` | all six r2 write sites resolved |
| evidence | Each site rebuilds r2 from MOVBZ-bounded RL6 and computes r2 = index * 6 before a fixed 0xF3FA-0xF3FF offset. | | |
| `FUN_027b1c r7` | `0xE523 plus at most 2 bytes per descriptor` | `0xE523-0xE720` | all five r7 write sites resolved |
| evidence | The stock entry gate admits only file 0x027A8E. That path initializes r7=0xE523, and the byte-sized descriptor count permits at most 255 iterations with two output bytes per iteration. | | |
| `FUN_02ada4/FUN_02aeb8 r8` | `0xF182, 0xF18A` | `0xF182-0xF193` | all nine r8 write sites resolved |
| evidence | The stock loop-entry gate admits only the preheader/loop-back path. Each iteration selects r8=0xF182 or 0xF18A before any r8 write. | | |
| `FUN_0352c0 r14/r4` | `0xF63E + 6 * zero-extended byte index` | `0xF63E-0xFC3C` | all eight r14/r4 write sites resolved |
| evidence | The stock entry gate admits only file 0x0352AC. The entry zero-extends the low byte of r12, multiplies it by six, and adds 0xF63E. | | |
| `paired-object updater at file 0x02FD6E r8` | `0xF018, 0xF0C4` | `0xF024-0xF0D3` | all eight r8 write sites resolved |
| evidence | The stock entry gate has two callers, each immediately passing the proven FUN_02b0cc r9 object through r12; the entry copies r12 to r8. | | |
| `FUN_02c024 r2` | `6 * zero-extended byte index` | `0xF63F-0xFC3D` | all four r2 write sites resolved |
| evidence | Each write immediately rebuilds r2 from MOVBZ-bounded RL7 and computes r2 = index * 6. | | |
| `FUN_036128 r5` | `zero-extended low byte of r8` | `0xF748-0xF847` | all four r5 write sites resolved |
| evidence | Each write immediately bounds r5 with MOVBZ from RL5. | | |
| `FUN_0362b4 r4` | `zero-extended low byte of r8` | `0xF748-0xF847` | all four r4 write sites resolved |
| evidence | Each write immediately bounds r4 with MOVBZ from RL4. | | |
| `FUN_020a26 r9/r4` | `0xE52E plus three 6-word serialization loops` | `0xE52E-0xE551` | all six r9/r4 write sites resolved |
| evidence | r9 starts at 0xE52E; each of three six-iteration loops writes two bytes and advances r9 by two. | | |
| `FUN_020f5a r5` | `zero-extended stack byte` | `0xE523-0xE624` | all three r5 write sites resolved |
| evidence | Each write immediately bounds r5 with MOVBZ from RL5. | | |
| `FUN_0044e6 r4` | `zero-extended counted-loop byte` | `0xE523-0xE643` | all three r4 write sites resolved |
| evidence | Each write immediately bounds r4 with MOVBZ from RL4. | | |
| `FUN_0044e6 r0` | `stock startup RAM-test table ranges` | `0xD080-0xD0FF, 0xD800-0xF7F3` | two startup r0 write sites resolved |
| evidence | The exact immutable startup table supplies r0 for the word and byte RAM-test loops at file 0x0045D0 and 0x004658. | | |
| `FUN_00098a r5` | `0xE523-0xE621` | `0xE523-0xE621` | counted-loop value set resolved |
| evidence | At file 0x004E2E r5 copies r9, which starts at 0xE523 and advances once per iteration of an 8-bit count. | | |

### Proven multi-value pointer writes

These sites have more than one possible address, but every address is bounded.

| owner | PC | operand | possible addresses |
|---|---|---|---|
| `FUN_02b0cc` | `0x02B0EA` | `[r9+#0x54]` | `0xF06C, 0xF118` |
| `FUN_02b0cc` | `0x02B104` | `[r9+#0x48]` | `0xF060, 0xF10C` |
| `FUN_02b0cc` | `0x02B118` | `[r9+#0x48]` | `0xF060, 0xF10C` |
| `FUN_02b0cc` | `0x02B14E` | `[r9+#0x4d]` | `0xF065, 0xF111` |
| `FUN_02b0cc` | `0x02B168` | `[r9+#0x4b]` | `0xF063, 0xF10F` |
| `FUN_02b0cc` | `0x02B1CA` | `[r9+#0x4a]` | `0xF062, 0xF10E` |
| `FUN_02b0cc` | `0x02B1DA` | `[r9+#0x4a]` | `0xF062, 0xF10E` |
| `FUN_02b0cc` | `0x02B1EC` | `[r9+#0x4a]` | `0xF062, 0xF10E` |
| `FUN_02b0cc` | `0x02B1F0` | `[r9+#0x4c]` | `0xF064, 0xF110` |
| `FUN_02b0cc` | `0x02B288` | `[r9+#0x54]` | `0xF06C, 0xF118` |
| `FUN_02b0cc` | `0x02B324` | `[r9+#0x4e]` | `0xF066, 0xF112` |
| `FUN_02b0cc` | `0x02F2C0` | `[r9+#0x31]` | `0xF049, 0xF0F5` |
| `FUN_02b0cc` | `0x02F3CC` | `[r9+#0x1c]` | `0xF034, 0xF0E0` |
| `FUN_02b0cc` | `0x02F3F4` | `[r9+#0x1a]` | `0xF032, 0xF0DE` |
| `FUN_02b0cc` | `0x02F41E` | `[r9+#0x1a]` | `0xF032, 0xF0DE` |
| `FUN_02b0cc` | `0x02F430` | `[r9+#0x2]` | `0xF01A, 0xF0C6` |
| `FUN_02b0cc` | `0x02F438` | `[r9+#0x4]` | `0xF01C, 0xF0C8` |
| `object-entry gated r4` | `0x028220` | `[r4]` | `0xF057, 0xF103` |
| `FUN_028100` | `0x028102` | `[r4]` | `0xF04A, 0xF0F6` |
| `FUN_02b0cc` | `0x02B200` | `[r4]` | `0xF06A, 0xF116` |
| `FUN_02b0cc` | `0x02F35E` | `[r4]` | `0xF04F, 0xF0FB` |
| `FUN_02b0cc` | `0x02F374` | `[r4]` | `0xF042, 0xF0EE` |
| `FUN_02b0cc` | `0x02F3A8` | `[r4]` | `0xF04D, 0xF0F9` |
| `object-entry gated r8` | `0x0287AC` | `[r8+#0x14]` | `0xF02C, 0xF0D8` |
| `object-entry gated r8` | `0x0287BA` | `[r8+#0x6]` | `0xF01E, 0xF0CA` |
| `object-entry gated r8` | `0x02FCB2` | `[r8+#0xa]` | `0xF022, 0xF0CE` |
| `object-entry gated r9` | `0x028226` | `[r9+#0x32]` | `0xF04A, 0xF0F6` |
| `object-entry gated r9` | `0x02822A` | `[r9]` | `0xF018, 0xF0C4` |
| `FUN_020986` | `0x024B14` | `[r5+#0xe865]` | `0xE865-0xE964` |
| `FUN_020986` | `0x024B44` | `[r5+#0xe865]` | `0xE865-0xE964` |
| `FUN_020986` | `0x024B78` | `[r5+#0xe865]` | `0xE865-0xE964` |
| `FUN_020986` | `0x024BB6` | `[r5+#0xe865]` | `0xE865-0xE964` |
| `FUN_020986` | `0x024BE6` | `[r5+#0xe865]` | `0xE865-0xE964` |
| `FUN_020986` | `0x024C14` | `[r5+#0xe865]` | `0xE865-0xE964` |
| `FUN_020986` | `0x024C44` | `[r5+#0xe865]` | `0xE865-0xE964` |
| `FUN_020986` | `0x024C72` | `[r5+#0xe865]` | `0xE865-0xE964` |
| `FUN_020986` | `0x024CA0` | `[r5+#0xe865]` | `0xE865-0xE964` |
| `FUN_020986` | `0x024CCE` | `[r5+#0xe865]` | `0xE865-0xE964` |
| `flash_write_orchestrator` | `0x0051B0` | `[r5+#0xe428]` | `0xE428-0xE527` |
| `FUN_024e30 receive-ring byte writers` | `0x007912` | `[r5+#0xe865]` | `0xE865-0xE884` |
| `FUN_024e30 receive-ring byte writers` | `0x007952` | `[r5+#0xe865]` | `0xE865-0xE884` |
| `FUN_024e30 transmit-ring byte writers` | `0x0079F2` | `[r5+#0xe885]` | `0xE885-0xE8A4` |
| `FUN_024e30 transmit-ring byte writers` | `0x007A22` | `[r5+#0xe885]` | `0xE885-0xE8A4` |
| `FUN_024e30 transmit-ring byte writers` | `0x007A78` | `[r5+#0xe885]` | `0xE885-0xE8A4` |
| `FUN_024e30 transmit-ring byte writers` | `0x007ACC` | `[r5+#0xe885]` | `0xE885-0xE8A4` |
| `FUN_024e30 transmit-ring byte writers` | `0x007B1C` | `[r5+#0xe885]` | `0xE885-0xE8A4` |
| `FUN_024e30 transmit-ring byte writers` | `0x007B4A` | `[r5+#0xe885]` | `0xE885-0xE8A4` |
| `FUN_024e30 transmit-ring byte writers` | `0x007BCC` | `[r5+#0xe885]` | `0xE885-0xE8A4` |
| `FUN_024e30 transmit-ring byte writers` | `0x007ED6` | `[r5+#0xe885]` | `0xE885-0xE8A4` |
| `FUN_024e30 transmit-ring byte writers` | `0x0079BE` | `[r4+#0xe885]` | `0xE885-0xE8A4` |
| `FUN_024e30 transmit-ring byte writers` | `0x007B9A` | `[r2+#0xe885]` | `0xE885-0xE8A4` |
| `FUN_024e30 countdown table` | `0x007C46` | `[r7]` | `0xF770-0xF793 (stride 2 starts)` |
| `FUN_024e30 countdown table` | `0x007F0C` | `[r7]` | `0xF794-0xF7D1 (stride 2 starts)` |
| `FUN_024e30 countdown table` | `0x007F92` | `[r7]` | `0xF7D2-0xF7F1 (stride 2 starts)` |
| `stock startup byte-derived writers` | `0x0049CC` | `[r5+#0xe523]` | `0xE523-0xE622` |
| `stock startup byte-derived writers` | `0x0049E6` | `[r5+#0xe524]` | `0xE524-0xE623` |
| `stock startup byte-derived writers` | `0x0049F2` | `[r5+#0xe525]` | `0xE525-0xE624` |
| `stock startup byte-derived writers` | `0x004B26` | `[r5+#0xe747]` | `0xE747-0xE846` |
| `stock startup byte-derived writers` | `0x004C54` | `[r5+#0xe523]` | `0xE523-0xE622` |
| `stock startup byte-derived writers` | `0x004D42` | `[r5+#0xe523]` | `0xE523-0xE622` |
| `FUN_00098a` | `0x004E2E` | `[r5]` | `0xE523-0xE621` |
| `FUN_0044e6` | `0x0044EC` | `[r2]` | `0xFA00-0xFA15 (stride 2 starts)` |
| `FUN_0044e6` | `0x0044FA` | `[r2]` | `0xFA00-0xFA15 (stride 2 starts)` |
| `FUN_0044e6` | `0x004510` | `[r2]` | `0xFA00-0xFA15 (stride 2 starts)` |
| `FUN_0044e6` | `0x00452A` | `[-r2]` | `0xFA00-0xFA15 (stride 2 starts)` |
| `FUN_0044e6` | `0x004564` | `[r2]` | `0xFA16-0xFDFF (stride 2 starts)` |
| `FUN_0044e6` | `0x004572` | `[r2]` | `0xFA16-0xFDFF (stride 2 starts)` |
| `FUN_0044e6` | `0x004588` | `[r2]` | `0xFA16-0xFDFF (stride 2 starts)` |
| `FUN_0044e6` | `0x004558` | `[-r2]` | `0xFA16-0xFDFF (stride 2 starts)` |
| `FUN_0044e6` | `0x0045A2` | `[-r2]` | `0xFA16-0xFDFF (stride 2 starts)` |
| `FUN_0044e6` | `0x0045EC` | `[r2]` | `0xD800-0xF7F3 (stride 2 starts)` |
| `FUN_0044e6` | `0x0045F6` | `[r2]` | `0xD800-0xF7F3 (stride 2 starts)` |
| `FUN_0044e6` | `0x0045E0` | `[-r2]` | `0xD800-0xF7F3 (stride 2 starts)` |
| `FUN_0044e6` | `0x004610` | `[-r2]` | `0xD800-0xF7F3 (stride 2 starts)` |
| `FUN_0044e6` | `0x004678` | `[r2]` | `0xD080-0xD0FF` |
| `FUN_0044e6` | `0x004686` | `[r2]` | `0xD080-0xD0FF` |
| `FUN_0044e6` | `0x00469A` | `[r2]` | `0xD080-0xD0FF` |
| `FUN_0044e6` | `0x00466C` | `[-r2]` | `0xD080-0xD0FF` |
| `FUN_0044e6` | `0x0046B8` | `[-r2]` | `0xD080-0xD0FF` |
| `FUN_0357d2` | `0x035A10` | `[r2+#0xf643]` | `0xF643-0xFC3D (stride 6 starts)` |
| `FUN_0357d2` | `0x035A20` | `[r2+#0xf641]` | `0xF641-0xFC3B (stride 6 starts)` |
| `FUN_0357d2` | `0x035A30` | `[r2+#0xf640]` | `0xF640-0xFC3A (stride 6 starts)` |
| `FUN_0357d2` | `0x035A40` | `[r2+#0xf63f]` | `0xF63F-0xFC39 (stride 6 starts)` |
| `FUN_0357d2` | `0x035A50` | `[r2+#0xf63e]` | `0xF63E-0xFC38 (stride 6 starts)` |
| `FUN_0357d2` | `0x035AF2` | `[r2+#0xf643]` | `0xF643-0xFC3D (stride 6 starts)` |
| `FUN_0357d2` | `0x035B02` | `[r2+#0xf641]` | `0xF641-0xFC3B (stride 6 starts)` |
| `FUN_0357d2` | `0x035B12` | `[r2+#0xf640]` | `0xF640-0xFC3A (stride 6 starts)` |
| `FUN_0357d2` | `0x035B22` | `[r2+#0xf63f]` | `0xF63F-0xFC39 (stride 6 starts)` |
| `FUN_0357d2` | `0x035B32` | `[r2+#0xf63e]` | `0xF63E-0xFC38 (stride 6 starts)` |
| `FUN_0357d2` | `0x0358B8` | `[r4]` | `0xF642-0xFC3C (stride 6 starts)` |
| `FUN_0357d2` | `0x0359D6` | `[r4]` | `0xF642-0xFC3C (stride 6 starts)` |
| `FUN_0357d2` | `0x035A00` | `[r4]` | `0xF642-0xFC3C (stride 6 starts)` |
| `FUN_0357d2` | `0x035ABA` | `[r4]` | `0xF642-0xFC3C (stride 6 starts)` |
| `FUN_0357d2` | `0x035AE2` | `[r4]` | `0xF642-0xFC3C (stride 6 starts)` |
| `FUN_0357d2` | `0x035A70` | `[r4]` | `0xF643-0xFC3D (stride 6 starts)` |
| `FUN_0357d2` | `0x035A82` | `[r4]` | `0xEA24-0xF618 (stride 12 starts)` |
| `FUN_001bcc` | `0x001D80` | `[r15+#0xa]` | `0xFA50, 0xFA5C` |
| `FUN_001bcc` | `0x001D98` | `[r15+#0x8]` | `0xFA4E, 0xFA5A` |
| `FUN_001bcc` | `0x001DEC` | `[r15+#0x2]` | `0xFA48, 0xFA54` |
| `FUN_001bcc` | `0x001DF6` | `[r15+#0x2]` | `0xFA48, 0xFA54` |
| `FUN_001bcc` | `0x001E04` | `[r15]` | `0xFA46, 0xFA52` |
| `FUN_001bcc` | `0x001EC2` | `[r15+#0x4]` | `0xFA4A, 0xFA56` |
| `FUN_001bcc` | `0x001EDE` | `[r15+#0x6]` | `0xFA4C, 0xFA58` |
| `FUN_001bcc` | `0x001EE4` | `[r15+#0x4]` | `0xFA4A, 0xFA56` |
| `FUN_001bcc` | `0x001EEC` | `[r15+#0x2]` | `0xFA48, 0xFA54` |
| `FUN_001bcc` | `0x001EF0` | `[r15+#0x0]` | `0xFA46, 0xFA52` |
| `FUN_028100` | `0x028118` | `[r9]` | `0xF018, 0xF0C4` |
| `FUN_028100` | `0x02814E` | `[r9+#0x28]` | `0xF040, 0xF0EC` |
| `FUN_028100` | `0x028156` | `[r9+#0x26]` | `0xF03E, 0xF0EA` |
| `FUN_028100` | `0x02815A` | `[r9+#0x2e]` | `0xF046, 0xF0F2` |
| `FUN_028100` | `0x02815E` | `[r9+#0x2f]` | `0xF047, 0xF0F3` |
| `FUN_028100` | `0x028162` | `[r9+#0x2d]` | `0xF045, 0xF0F1` |
| `FUN_028100` | `0x028166` | `[r9+#0x2c]` | `0xF044, 0xF0F0` |
| `FUN_028100` | `0x02816E` | `[r9+#0x33]` | `0xF04B, 0xF0F7` |
| `FUN_02218a` | `0x0221C4` | `[r2+#0xf640]` | `0xF640-0xFC3A (stride 6 starts)` |
| `FUN_02218a` | `0x0221E0` | `[r2+#0xf63e]` | `0xF63E-0xFC38 (stride 6 starts)` |
| `FUN_02218a` | `0x0221FC` | `[r2+#0xf641]` | `0xF641-0xFC3B (stride 6 starts)` |
| `FUN_02218a` | `0x022218` | `[r2+#0xf63f]` | `0xF63F-0xFC39 (stride 6 starts)` |
| `FUN_02218a` | `0x022234` | `[r2+#0xf642]` | `0xF642-0xFC3C (stride 6 starts)` |
| `FUN_02218a` | `0x022250` | `[r2+#0xf643]` | `0xF643-0xFC3D (stride 6 starts)` |
| `FUN_027b1c` | `0x027B44` | `[r7]` | `0xE523-0xE71F` |
| `FUN_027b1c` | `0x027B4C` | `[r7]` | `0xE523-0xE71F` |
| `FUN_027b1c` | `0x027B50` | `[r7]` | `0xE523-0xE71F` |
| `FUN_027b1c` | `0x027B6A` | `[r7+#0x1]` | `0xE524-0xE720` |
| `FUN_027b1c` | `0x027B72` | `[r7]` | `0xE523-0xE71F` |
| `FUN_02aeb8` | `0x02AEC2` | `[r8+#0x2]` | `0xF184, 0xF18C` |
| `FUN_02aeb8` | `0x02AED8` | `[r8+#0x2]` | `0xF184, 0xF18C` |
| `FUN_02aeb8` | `0x02AEF0` | `[r8+#0x2]` | `0xF184, 0xF18C` |
| `FUN_02aeb8` | `0x02AEF6` | `[r8+#0x7]` | `0xF189, 0xF191` |
| `FUN_0352c0` | `0x0352C8` | `[r14+#0x2]` | `0xF640-0xFC3A (stride 6 starts)` |
| `FUN_0352c0` | `0x0352CC` | `[r14]` | `0xF63E-0xFC38 (stride 6 starts)` |
| `FUN_0352c0` | `0x0352D2` | `[r14+#0x3]` | `0xF641-0xFC3B (stride 6 starts)` |
| `FUN_0352c0` | `0x0352DA` | `[r14+#0x1]` | `0xF63F-0xFC39 (stride 6 starts)` |
| `FUN_0352c0` | `0x0352E6` | `[r4]` | `0xF642-0xFC3C (stride 6 starts)` |
| `FUN_0352c0` | `0x0352F0` | `[r4]` | `0xF642-0xFC3C (stride 6 starts)` |
| `FUN_0352c0` | `0x035300` | `[r4]` | `0xF642-0xFC3C (stride 6 starts)` |
| `FUN_0352c0` | `0x03530E` | `[r4]` | `0xF642-0xFC3C (stride 6 starts)` |
| `paired-object updater` | `0x02FD90` | `[r8+#0xe]` | `0xF026, 0xF0D2` |
| `paired-object updater` | `0x02FDAC` | `[r8+#0xe]` | `0xF026, 0xF0D2` |
| `paired-object updater` | `0x02FDB2` | `[r8+#0xe]` | `0xF026, 0xF0D2` |
| `paired-object updater` | `0x02FDBA` | `[r8+#0xc]` | `0xF024, 0xF0D0` |
| `paired-object updater` | `0x02FDD4` | `[r8+#0xc]` | `0xF024, 0xF0D0` |
| `paired-object updater` | `0x02FDF0` | `[r8+#0xc]` | `0xF024, 0xF0D0` |
| `paired-object updater` | `0x02FDF6` | `[r8+#0xc]` | `0xF024, 0xF0D0` |
| `paired-object updater` | `0x02FDFE` | `[r8+#0xe]` | `0xF026, 0xF0D2` |
| `FUN_02c024` | `0x02C02E` | `[r2+#0xf643]` | `0xF643-0xFC3D (stride 6 starts)` |
| `FUN_02c024` | `0x02C03C` | `[r2+#0xf641]` | `0xF641-0xFC3B (stride 6 starts)` |
| `FUN_02c024` | `0x02C04A` | `[r2+#0xf640]` | `0xF640-0xFC3A (stride 6 starts)` |
| `FUN_02c024` | `0x02C058` | `[r2+#0xf63f]` | `0xF63F-0xFC39 (stride 6 starts)` |
| `FUN_036128` | `0x036172` | `[r5+#0xf748]` | `0xF748-0xF847` |
| `FUN_036128` | `0x036180` | `[r5+#0xf748]` | `0xF748-0xF847` |
| `FUN_036128` | `0x0361B8` | `[r5+#0xf748]` | `0xF748-0xF847` |
| `FUN_036128` | `0x0361C6` | `[r5+#0xf748]` | `0xF748-0xF847` |
| `FUN_0362b4` | `0x0362DE` | `[r4+#0xf748]` | `0xF748-0xF847` |
| `FUN_0362b4` | `0x0362F8` | `[r4+#0xf748]` | `0xF748-0xF847` |
| `FUN_0362b4` | `0x036330` | `[r4+#0xf748]` | `0xF748-0xF847` |
| `FUN_0362b4` | `0x03634A` | `[r4+#0xf748]` | `0xF748-0xF847` |
| `FUN_03635e` | `0x036368` | `[r4+#0xf748]` | `0xF748-0xF847` |
| `FUN_03635e` | `0x036382` | `[r4+#0xf748]` | `0xF748-0xF847` |
| `FUN_029898` | `0x0298C2` | `[r5+#0xe885]` | `0xE885-0xE8A4` |
| `FUN_029898` | `0x0298F0` | `[r5+#0xe885]` | `0xE885-0xE8A4` |
| `FUN_0314a8` | `0x0314F0` | `[r5+#0xe996]` | `0xE998, 0xE99A, 0xE99C, 0xE99E, 0xE9A0` |
| `FUN_0314a8` | `0x03150C` | `[r5+#0xfc5a]` | `0xFC5C, 0xFC5E, 0xFC60, 0xFC62, 0xFC64` |
| `FUN_02ff78` | `0x02FFA8` | `[r9+#0x22]` | `0xF03A, 0xF0E6` |
| `FUN_02ff78` | `0x02FFB0` | `[r9+#0x24]` | `0xF03C, 0xF0E8` |
| `FUN_02ff78` | `0x02FFB8` | `[r9+#0x1e]` | `0xF036, 0xF0E2` |
| `object metric updater` | `0x02FE52` | `[r8+#0x12]` | `0xF02A, 0xF0D6` |
| `object metric updater` | `0x02FE6E` | `[r8+#0x12]` | `0xF02A, 0xF0D6` |
| `object metric updater` | `0x02FE86` | `[r8+#0x12]` | `0xF02A, 0xF0D6` |
| `object metric updater` | `0x02FEA2` | `[r8+#0x12]` | `0xF02A, 0xF0D6` |
| `object metric updater` | `0x02FEAC` | `[r8+#0x12]` | `0xF02A, 0xF0D6` |
| `object metric updater` | `0x02FED2` | `[r8+#0x12]` | `0xF02A, 0xF0D6` |
| `local MOVBZ r5 writers` | `0x02253A` | `[r5+#0xf2b6]` | `0xF2B6-0xF3B5` |
| `local MOVBZ r5 writers` | `0x022544` | `[r5+#0xf2b6]` | `0xF2B6-0xF3B5` |
| `local MOVBZ r5 writers` | `0x023A2A` | `[r5+#0xeef1]` | `0xEEF1-0xEFF0` |
| `local MOVBZ r5 writers` | `0x023AAA` | `[r5+#0xee92]` | `0xEE92-0xEF91` |
| `local MOVBZ r5 writers` | `0x023B74` | `[r5+#0xee92]` | `0xEE92-0xEF91` |
| `local MOVBZ r5 writers` | `0x023C7E` | `[r5+#0xee92]` | `0xEE92-0xEF91` |
| `local MOVBZ r5 writers` | `0x023DF2` | `[r5+#0xeef1]` | `0xEEF1-0xEFF0` |
| `local MOVBZ r5 writers` | `0x023E72` | `[r5+#0xee92]` | `0xEE92-0xEF91` |
| `local MOVBZ r5 writers` | `0x024A2C` | `[r5+#0xe8a5]` | `0xE8A5-0xE9A4` |
| `local MOVBZ r5 writers` | `0x024A88` | `[r5+#0xe865]` | `0xE865-0xE964` |
| `local MOVBZ r5 writers` | `0x024AB6` | `[r5+#0xe865]` | `0xE865-0xE964` |
| `local MOVBZ r5 writers` | `0x024AE4` | `[r5+#0xe865]` | `0xE865-0xE964` |
| `local MOVBZ r5 writers` | `0x024D44` | `[r5+#0xe885]` | `0xE885-0xE984` |
| `local MOVBZ r5 writers` | `0x024D7A` | `[r5+#0xe885]` | `0xE885-0xE984` |
| `local MOVBZ r5 writers` | `0x024DB0` | `[r5+#0xe885]` | `0xE885-0xE984` |
| `local MOVBZ r5 writers` | `0x024DE2` | `[r5+#0xe865]` | `0xE865-0xE964` |
| `local MOVBZ r5 writers` | `0x024E18` | `[r5+#0xe885]` | `0xE885-0xE984` |
| `local MOVBZ r5 writers` | `0x024E48` | `[r5+#0xe885]` | `0xE885-0xE984` |
| `local MOVBZ r5 writers` | `0x024E78` | `[r5+#0xe885]` | `0xE885-0xE984` |
| `local MOVBZ r5 writers` | `0x029850` | `[r5+#0xe885]` | `0xE885-0xE984` |
| `local MOVBZ r5 writers` | `0x02988E` | `[r5+#0xe885]` | `0xE885-0xE984` |
| `local MOVBZ r5 writers` | `0x02996E` | `[r5+#0xe885]` | `0xE885-0xE984` |
| `local MOVBZ r5 writers` | `0x02C65A` | `[r5+#0xe865]` | `0xE865-0xE964` |
| `local MOVBZ r5 writers` | `0x0361FC` | `[r5+#0xf748]` | `0xF748-0xF847` |
| `local MOVBZ r5 writers` | `0x036208` | `[r5+#0xf748]` | `0xF748-0xF847` |
| `local MOVBZ r5 writers` | `0x037230` | `[r5+#0xda4a]` | `0xDA4A-0xDB49` |
| `local MOVBZ r4 writers` | `0x020FA0` | `[r4+#0xe523]` | `0xE523-0xE622` |
| `local MOVBZ r4 writers` | `0x021088` | `[r4+#0xe552]` | `0xE552-0xE651` |
| `local MOVBZ r4 writers` | `0x0226E0` | `[r4+#0xf339]` | `0xF339-0xF438` |
| `local MOVBZ r4 writers` | `0x022B1C` | `[r4+#0xf454]` | `0xF454-0xF553` |
| `local MOVBZ r4 writers` | `0x023B84` | `[r4+#0xeef1]` | `0xEEF1-0xEFF0` |
| `local MOVBZ r4 writers` | `0x023C8E` | `[r4+#0xeef1]` | `0xEEF1-0xEFF0` |
| `local MOVBZ r4 writers` | `0x029820` | `[r4+#0xe885]` | `0xE885-0xE984` |
| `local MOVBZ r4 writers` | `0x029930` | `[r4+#0xe885]` | `0xE885-0xE984` |
| `local MOVBZ r4 writers` | `0x02C288` | `[r4+#0xe865]` | `0xE865-0xE964` |
| `local MOVBZ r4 writers` | `0x02ED96` | `[r4+#0xfc7c]` | `0xFC7C-0xFD7B` |
| `local MOVBZ r4 writers` | `0x035DDA` | `[r4+#0xf749]` | `0xF749-0xF848` |
| `local MOVBZ r4 writers` | `0x03621E` | `[r4+#0xf748]` | `0xF748-0xF847` |
| `local MOVBZ r4 writers` | `0x036228` | `[r4+#0xf748]` | `0xF748-0xF847` |
| `local MOVBZ r4 writers` | `0x03723E` | `[r4+#0xda64]` | `0xDA64-0xDB63` |
| `local MOVBZ r4 writers` | `0x03724A` | `[r4+#0xda6a]` | `0xDA6A-0xDB69` |
| `local MOVBZ r4 writers` | `0x037262` | `[r4+#0xda5c]` | `0xDA5C-0xDB5B` |
| `local MOVBZ r4 writers` | `0x037296` | `[r4+#0xdb06]` | `0xDB06-0xDC05` |
| `local MOVBZ r0 writer` | `0x039964` | `[r0+#0xfa62]` | `0xFA62-0xFB61` |
| `local doubled-MOVBZ r5 writers` | `0x02358E` | `[r5+#0xfc28]` | `0xFC28-0xFE27 (stride 2 starts)` |
| `local doubled-MOVBZ r5 writers` | `0x0235B6` | `[r5+#0xfc28]` | `0xFC28-0xFE27 (stride 2 starts)` |
| `local doubled-MOVBZ r5 writers` | `0x0236FE` | `[r5+#0xeff6]` | `0xEFF6-0xF1F5 (stride 2 starts)` |
| `local doubled-MOVBZ r5 writers` | `0x02B77A` | `[r5+#0xe848]` | `0xE848-0xEA47 (stride 2 starts)` |
| `local doubled-MOVBZ r5 writers` | `0x02B7E0` | `[r5+#0xe854]` | `0xE854-0xEA53 (stride 2 starts)` |
| `local doubled-MOVBZ r5 writers` | `0x0335A0` | `[r5+#0xf5ac]` | `0xF5AC-0xF7AB (stride 2 starts)` |
| `local doubled-MOVBZ r5 writers` | `0x034442` | `[r5+#0xf4f0]` | `0xF4F0-0xF6EF (stride 2 starts)` |
| `local doubled-MOVBZ r5 writers` | `0x034464` | `[r5+#0xf4e4]` | `0xF4E4-0xF6E3 (stride 2 starts)` |
| `local doubled-MOVBZ r4 writers` | `0x03464E` | `[r4+#0xf50e]` | `0xF50E-0xF70D (stride 2 starts)` |
| `local doubled-MOVBZ r4 writers` | `0x03572E` | `[r4+#0xf616]` | `0xF616-0xF815 (stride 2 starts)` |
| `local doubled-MOVBZ r4 writers` | `0x0371F2` | `[r4+#0xda1e]` | `0xDA1E-0xDC1D (stride 2 starts)` |
| `local doubled-MOVBZ r4 writers` | `0x037208` | `[r4+#0xda1e]` | `0xDA1E-0xDC1D (stride 2 starts)` |
| `local six-word copy` | `0x02292E` | `[r4+#0xf3ee]` | `0xF3EE, 0xF3F0, 0xF3F2, 0xF3F4, 0xF3F6, 0xF3F8` |
| `local 60-byte state clear` | `0x0351C0` | `[r9]` | `0xF63E-0xF679` |
| `local 95-record flag clear` | `0x0351DA` | `[r2+#0xea24]` | `0xEA24-0xEE8C (stride 12 starts)` |
| `local 95-record flag clear` | `0x0351E8` | `[r2+#0xea25]` | `0xEA25-0xEE8D (stride 12 starts)` |
| `local bitset word writers` | `0x0354E0` | `[r2]` | `0xF5F2, 0xF5F4, 0xF5F6, 0xF5F8, 0xF5FA, 0xF5FC` |
| `local bitset word writers` | `0x035500` | `[r2]` | `0xF5FE, 0xF600, 0xF602, 0xF604, 0xF606, 0xF608` |
| `local bitset word writers` | `0x0355E0` | `[r2]` | `0xF60A, 0xF60C, 0xF60E, 0xF610, 0xF612, 0xF614` |
| `local bitset word writers` | `0x035698` | `[r2]` | `0xF616, 0xF618, 0xF61A, 0xF61C, 0xF61E, 0xF620` |
| `local six-byte record status writer` | `0x023FF4` | `[r4]` | `0xF642-0xFC36 (stride 6 starts)` |
| `local six-byte mask writers` | `0x02383E` | `[r5]` | `0xF547, 0xF548, 0xF549, 0xF54A, 0xF54B, 0xF54C` |
| `local six-byte mask writers` | `0x023870` | `[r5]` | `0xF547, 0xF548, 0xF549, 0xF54A, 0xF54B, 0xF54C` |
| `local six-byte mask writers` | `0x0238A0` | `[r5]` | `0xF547, 0xF548, 0xF549, 0xF54A, 0xF54B, 0xF54C` |
| `local six-byte mask writers` | `0x0238D0` | `[r5]` | `0xF547, 0xF548, 0xF549, 0xF54A, 0xF54B, 0xF54C` |
| `local six-byte mask writers` | `0x023900` | `[r5]` | `0xF547, 0xF548, 0xF549, 0xF54A, 0xF54B, 0xF54C` |
| `local six-byte mask writers` | `0x023930` | `[r5]` | `0xF547, 0xF548, 0xF549, 0xF54A, 0xF54B, 0xF54C` |
| `countdown record updater` | `0x023988` | `[r8+#0xb]` | `0xEA31-0xEE5D (stride 48 starts)` |
| `countdown record updater` | `0x0239FC` | `[r8]` | `0xEA26-0xEE52 (stride 48 starts)` |
| `countdown record updater` | `0x023A00` | `[r8+#0x1]` | `0xEA27-0xEE53 (stride 48 starts)` |
| `countdown record updater` | `0x023A3A` | `[r8+#0x4]` | `0xEA2A-0xEE56 (stride 48 starts)` |
| `countdown record updater` | `0x023A44` | `[r8+#0x5]` | `0xEA2B-0xEE57 (stride 48 starts)` |
| `countdown record updater` | `0x023A4E` | `[r8+#0x6]` | `0xEA2C-0xEE58 (stride 48 starts)` |
| `countdown record updater` | `0x023A58` | `[r8+#0x7]` | `0xEA2D-0xEE59 (stride 48 starts)` |
| `countdown record updater` | `0x023A74` | `[r8]` | `0xEA26-0xEE52 (stride 48 starts)` |
| `countdown record updater` | `0x023A7A` | `[r8+#0x9]` | `0xEA2F-0xEE5B (stride 48 starts)` |
| `countdown record updater` | `0x023A82` | `[r8+#0x8]` | `0xEA2E-0xEE5A (stride 48 starts)` |
| `countdown record updater` | `0x023ABC` | `[r8]` | `0xEA26-0xEE52 (stride 48 starts)` |
| `countdown record updater` | `0x023AD4` | `[r8+#0x3]` | `0xEA29-0xEE55 (stride 48 starts)` |
| `countdown record updater` | `0x023B08` | `[r8+#0x1]` | `0xEA27-0xEE53 (stride 48 starts)` |
| `countdown record byte-10 writers` | `0x023998` | `[r4]` | `0xEA30-0xEE5C (stride 48 starts)` |
| `countdown record byte-10 writers` | `0x0239BA` | `[r4]` | `0xEA30-0xEE5C (stride 48 starts)` |
| `countdown record byte-10 writers` | `0x0239DC` | `[r4]` | `0xEA30-0xEE5C (stride 48 starts)` |
| `countdown record byte-11 writer` | `0x0239C8` | `[r4]` | `0xEA31-0xEE5D (stride 48 starts)` |
| `countdown record byte-2 writer` | `0x023AEA` | `[r4]` | `0xEA28-0xEE54 (stride 48 starts)` |
| `state record updater` | `0x023B5A` | `[r9]` | `0xEA32-0xEE82 (stride 324 starts)` |
| `state record updater` | `0x023B98` | `[r9+#0x4]` | `0xEA36-0xEE86 (stride 324 starts)` |
| `state record updater` | `0x023BA2` | `[r9+#0x5]` | `0xEA37-0xEE87 (stride 324 starts)` |
| `state record updater` | `0x023BAC` | `[r9+#0x6]` | `0xEA38-0xEE88 (stride 324 starts)` |
| `state record updater` | `0x023BB6` | `[r9+#0x7]` | `0xEA39-0xEE89 (stride 324 starts)` |
| `state record updater` | `0x023BD6` | `[r9+#0x9]` | `0xEA3B-0xEE8B (stride 324 starts)` |
| `state record updater` | `0x023BDE` | `[r9+#0x8]` | `0xEA3A-0xEE8A (stride 324 starts)` |
| `state record updater` | `0x023BE8` | `[r9]` | `0xEA32-0xEE82 (stride 324 starts)` |
| `state record updater` | `0x023C00` | `[r9+#0x3]` | `0xEA35-0xEE85 (stride 324 starts)` |
| `state record byte-10 writer` | `0x023B46` | `[r4]` | `0xEA3C-0xEE8C (stride 324 starts)` |
| `state record byte-2 writer` | `0x023C16` | `[r4]` | `0xEA34-0xEE84 (stride 324 starts)` |
| `paired countdown record updater` | `0x023D50` | `[r8+#0xb]` | `0xEAA9-0xEE15 (stride 12 starts)` |
| `paired countdown record updater` | `0x023DC4` | `[r8]` | `0xEA9E-0xEE0A (stride 12 starts)` |
| `paired countdown record updater` | `0x023DC8` | `[r8+#0x1]` | `0xEA9F-0xEE0B (stride 12 starts)` |
| `paired countdown record updater` | `0x023E02` | `[r8+#0x4]` | `0xEAA2-0xEE0E (stride 12 starts)` |
| `paired countdown record updater` | `0x023E0C` | `[r8+#0x5]` | `0xEAA3-0xEE0F (stride 12 starts)` |
| `paired countdown record updater` | `0x023E16` | `[r8+#0x6]` | `0xEAA4-0xEE10 (stride 12 starts)` |
| `paired countdown record updater` | `0x023E20` | `[r8+#0x7]` | `0xEAA5-0xEE11 (stride 12 starts)` |
| `paired countdown record updater` | `0x023E3C` | `[r8]` | `0xEA9E-0xEE0A (stride 12 starts)` |
| `paired countdown record updater` | `0x023E42` | `[r8+#0x9]` | `0xEAA7-0xEE13 (stride 12 starts)` |
| `paired countdown record updater` | `0x023E4A` | `[r8+#0x8]` | `0xEAA6-0xEE12 (stride 12 starts)` |
| `paired countdown record updater` | `0x023E94` | `[r8+#0x3]` | `0xEAA1-0xEE0D (stride 12 starts)` |
| `paired countdown record updater` | `0x023ED0` | `[r8]` | `0xEA9E-0xEE0A (stride 12 starts)` |
| `paired countdown record updater` | `0x023ED4` | `[r8+#0x1]` | `0xEA9F-0xEE0B (stride 12 starts)` |
| `paired countdown record byte-10 writers` | `0x023D60` | `[r4]` | `0xEAA8-0xEE14 (stride 12 starts)` |
| `paired countdown record byte-10 writers` | `0x023D82` | `[r4]` | `0xEAA8-0xEE14 (stride 12 starts)` |
| `paired countdown record byte-10 writers` | `0x023DA4` | `[r4]` | `0xEAA8-0xEE14 (stride 12 starts)` |
| `paired countdown record byte-11 writer` | `0x023D90` | `[r4]` | `0xEAA9-0xEE15 (stride 12 starts)` |
| `paired countdown record byte-2 writer` | `0x023EAA` | `[r4]` | `0xEAA0-0xEE0C (stride 12 starts)` |
| `record-status helper` | `0x02378A` | `[r9+#0xa]` | `0xEA30-0xEE8C (stride 12 starts)` |
| `record-status helper` | `0x02379C` | `[r9+#0xa]` | `0xEA30-0xEE8C (stride 12 starts)` |
| `record-status helper byte-10 writers` | `0x02373A` | `[r4]` | `0xEA30-0xEE8C (stride 12 starts)` |
| `record-status helper byte-10 writers` | `0x0237B8` | `[r4]` | `0xEA30-0xEE8C (stride 12 starts)` |
| `local bounded table-copy destination` | `0x022716` | `[r4]` | `0xF343-0xF3BA` |
| `local zero-fill destination` | `0x022942` | `[r9]` | `0xF3FA-0xF435` |
| `record byte-10 mask writer` | `0x023806` | `[r4]` | `0xEA24-0xF618 (stride 12 starts)` |
| `record byte-3 countdown writer` | `0x023F92` | `[r4]` | `0xEA1D-0xEE85 (stride 12 starts)` |
| `record byte-10 mask writer` | `0x023FB2` | `[r4]` | `0xEA24-0xEE8C (stride 12 starts)` |
| `CRC accumulator writer` | `0x03747E` | `[r14]` | `0xF482, 0xF484, 0xF486` |
| `CRC accumulator writer` | `0x037486` | `[r14]` | `0xF482, 0xF484, 0xF486` |
| `fixed native status byte` | `0x02541A` | `[r9]` | `0xF768` |
| `record byte-3 state writer` | `0x02C0E0` | `[r9+#0x3]` | `0xEA1D-0xEE85 (stride 12 starts)` |
| `paired native-object result writer` | `0x032240` | `[r9+#0x76]` | `0xF08E, 0xF13A` |
| `16-entry interrupt ring writer` | `0x0398DE` | `[r2+#0xf69c]` | `0xF69C-0xF6D9 (stride 4 starts)` |
| `16-entry interrupt ring writer` | `0x0398E4` | `[r2+#0xf69c]` | `0xF69E-0xF6DB (stride 4 starts)` |
| `paired native-object reset` | `0x02DA30` | `[r12+#0x45]` | `0xF05D, 0xF109` |
| `paired native-object reset` | `0x02DA36` | `[r12+#0x74]` | `0xF08C, 0xF138` |
| `paired native-object reset` | `0x02DA46` | `[r4]` | `0xF06E, 0xF11A` |
| `fixed optional result byte` | `0x02A588` | `[r9]` | `0xE8EB` |
| `startup word zero-fill` | `0x004906` | `[r9]` | `0xE720-0xE81F (stride 2 starts)` |
| `startup word zero-fill` | `0x004918` | `[r9]` | `0xE420-0xE71F (stride 2 starts)` |
| `local six-byte clear` | `0x0351FC` | `[r5+#0xf547]` | `0xF547, 0xF548, 0xF549, 0xF54A, 0xF54B, 0xF54C` |
| `startup boundary RAM-test write` | `0x0044E0` | `[-r2]` | `0xFA14` |
| `receive-buffer checksum byte` | `0x004A20` | `[r12]` | `0xE520-0xE61F` |
| `bounded byte-search output` | `0x005AF4` | `[r14]` | `0xE537-0xE76D` |
| `flash command target` | `0x004240` | `[r12]` | `0x0000-0x4000` |
| `flash command target` | `0x00427E` | `[r12]` | `0x0000-0x4000` |
| `flash command target` | `0x004280` | `[r12]` | `0x0000-0x4000` |
| `flash command target` | `0x00428A` | `[r12]` | `0x0000-0x4000` |
| `flash command target` | `0x00430A` | `[r12]` | `0x0000-0x4000` |
| `flash command target` | `0x004310` | `[r12]` | `0x0000-0x4000` |
| `flash command target` | `0x00434E` | `[r5]` | `0x0000-0x3FFF` |
| `flash command target` | `0x004358` | `[r5]` | `0x0000-0x3FFF` |
| `flash command target` | `0x004362` | `[r5]` | `0x0000-0x3FFF` |
| `flash command target` | `0x004386` | `[r5]` | `0x0000-0x3FFF` |
| `flash command target` | `0x0043F2` | `[r5]` | `0x0000-0x3FFF` |
| `flash command target` | `0x0043FC` | `[r5]` | `0x0000-0x3FFF` |
| `local bitset word writers` | `0x023770` | `[r3]` | `0xF60A-0xF629 (stride 2 starts)` |
| `local bitset word writers` | `0x0237DC` | `[r2]` | `0xF60A-0xF629 (stride 2 starts)` |
| `local record flag writer` | `0x035B44` | `[r9+#0xa]` | `0xEA24-0xF618 (stride 12 starts)` |
| `local 95-record flag writer` | `0x035BC2` | `[r4]` | `0xEA24-0xEE8C (stride 12 starts)` |
| `byte-indexed state writers` | `0x02E8BC` | `[r6+#0xe9ba]` | `0xE9BA-0xEAB9` |
| `byte-indexed state writers` | `0x02EBF8` | `[r6+#0xe9ba]` | `0xE9BA-0xEAB9` |
| `byte-indexed state writers` | `0x02ED40` | `[r6+#0xe9cc]` | `0xE9CC-0xEACB` |
| `byte-indexed state writers` | `0x02ED72` | `[r6+#0xfc7c]` | `0xFC7C-0xFD7B` |
| `byte-indexed state writers` | `0x02ED78` | `[r6+#0xfc7c]` | `0xFC7C-0xFD7B` |
| `byte-indexed state writers` | `0x02ED82` | `[r6+#0xfc7c]` | `0xFC7C-0xFD7B` |
| `byte-indexed state word writer` | `0x02ED48` | `[r4+#0xe9c0]` | `0xE9C0-0xEBBF (stride 2 starts)` |
| `stock flash protocol stack frame` | `0x004ED6` | `[r0+#0x4]` | `0xFA04-0xFA3D` |
| `stock flash protocol stack frame` | `0x005078` | `[r0]` | `0xFA00-0xFA39` |
| `stock flash protocol stack frame` | `0x005138` | `[r0]` | `0xFA00-0xFA39` |
| `stock flash protocol stack frame` | `0x005188` | `[r0+#0x2]` | `0xFA02-0xFA3A` |
| `stock flash protocol stack frame` | `0x0051C0` | `[r0+#0x2]` | `0xFA02-0xFA3A` |
| `stock flash protocol stack frame` | `0x0051E8` | `[r0+#0x2]` | `0xFA02-0xFA3A` |
| `stock flash protocol stack frame` | `0x0051F8` | `[r0]` | `0xFA00-0xFA39` |
| `saved-register two-byte stack frame` | `0x02A860` | `[r0]` | `0xFA00-0xFA41` |
| `saved-register two-byte stack frame` | `0x02A916` | `[r0+#0x1]` | `0xFA01-0xFA41` |
| `saved-register two-byte stack frame` | `0x02A924` | `[r0+#0x1]` | `0xFA01-0xFA41` |
| `saved-register two-byte stack frame` | `0x02A938` | `[r0+#0x1]` | `0xFA01-0xFA41` |
| `saved-register two-byte stack frame` | `0x02A95E` | `[r0+#0x1]` | `0xFA01-0xFA41` |
| `saved-register two-byte stack frame` | `0x02A96C` | `[r0+#0x1]` | `0xFA01-0xFA41` |
| `saved-register two-byte stack frame` | `0x02A992` | `[r0+#0x1]` | `0xFA01-0xFA41` |
| `two-byte stack frame` | `0x02D8DA` | `[r0]` | `0xFA00-0xFA45` |
| `two-byte stack frame` | `0x02D8EA` | `[r0]` | `0xFA00-0xFA45` |
| `two-byte stack frame` | `0x02D8F4` | `[r0]` | `0xFA00-0xFA45` |
| `two-byte stack frame` | `0x02D8FC` | `[r0]` | `0xFA00-0xFA45` |
| `two-byte stack frame` | `0x02D90E` | `[r0]` | `0xFA00-0xFA45` |
| `single-save two-byte stack frame` | `0x004264` | `[r0]` | `0xFA00-0xFA42` |
| `single-save two-byte stack frame` | `0x004276` | `[r0+#0x1]` | `0xFA01-0xFA43` |
| `two-word stock copy` | `0x000044` | `[r10]` | `0xF3CE, 0xF3D0` |
| `local bounded ISR slot writer` | `0x0397A6` | `[r0]` | `0xF6F7-0xF70E` |
| `local MOVBZ ISR writer` | `0x039996` | `[r0+#0xfa62]` | `0xFA62-0xFB61` |
| `local wrapped MOVBZ ISR writer` | `0x039972` | `[r0+#0xfa62]` | `0xFA62-0xFB5E` |
| `recovered local MOVBZ ring writer` | `0x024D14` | `[r5+#0xe865]` | `0xE865-0xE964` |
| `stock flash-driver copy loop` | `0x00508A` | `[r7]` | `0xE320-0xE41F` |
| `stock flash-driver copy loop` | `0x00520A` | `[r7]` | `0xE320-0xE41F` |
| `FUN_020a26` | `0x020A92` | `[r9]` | `0xE52E-0xE550 (stride 2 starts)` |
| `FUN_020a26` | `0x020AB6` | `[r9]` | `0xE52E-0xE550 (stride 2 starts)` |
| `FUN_020a26` | `0x020ADA` | `[r9]` | `0xE52E-0xE550 (stride 2 starts)` |
| `FUN_020a26` | `0x020AA0` | `[r4]` | `0xE52F-0xE551 (stride 2 starts)` |
| `FUN_020a26` | `0x020AC4` | `[r4]` | `0xE52F-0xE551 (stride 2 starts)` |
| `FUN_020a26` | `0x020AE8` | `[r4]` | `0xE52F-0xE551 (stride 2 starts)` |
| `FUN_020f5a` | `0x020F5A` | `[r5+#0xe523]` | `0xE523-0xE622` |
| `FUN_020f5a` | `0x020F74` | `[r5+#0xe524]` | `0xE524-0xE623` |
| `FUN_020f5a` | `0x020F80` | `[r5+#0xe525]` | `0xE525-0xE624` |
| `stock startup byte-derived writers` | `0x004C76` | `[r4+#0xe523]` | `0xE523-0xE622` |
| `stock startup byte-derived writers` | `0x004CD4` | `[r4+#0xe536]` | `0xE536-0xE635` |
| `stock startup byte-derived writers` | `0x004D14` | `[r4+#0xe544]` | `0xE544-0xE643` |
| `knock channel word writers` | `0x02E7D6` | `[r5+#0xe9a8]` | `0xE9A8, 0xE9AA, 0xE9AC, 0xE9AE, 0xE9B0, 0xE9B2` |
| `knock channel word writers` | `0x02E7F2` | `[r5+#0xe9a8]` | `0xE9A8, 0xE9AA, 0xE9AC, 0xE9AE, 0xE9B0, 0xE9B2` |
| `knock channel word writers` | `0x02E810` | `[r5+#0xe9a8]` | `0xE9A8, 0xE9AA, 0xE9AC, 0xE9AE, 0xE9B0, 0xE9B2` |
| `FUN_0044e6` | `0x0045D0` | `[r0]` | `0xD800-0xF7F3 (stride 2 starts)` |
| `FUN_0044e6` | `0x004658` | `[r0]` | `0xD080-0xD0FF` |
| `FUN_022998` | `0x0229D4` | `[r2+#0xf3fc]` | `0xF3FC-0xF432 (stride 6 starts)` |
| `FUN_022998` | `0x0229F0` | `[r2+#0xf3fa]` | `0xF3FA-0xF430 (stride 6 starts)` |
| `FUN_022998` | `0x022A0C` | `[r2+#0xf3fd]` | `0xF3FD-0xF433 (stride 6 starts)` |
| `FUN_022998` | `0x022A28` | `[r2+#0xf3fb]` | `0xF3FB-0xF431 (stride 6 starts)` |
| `FUN_022998` | `0x022A44` | `[r2+#0xf3fe]` | `0xF3FE-0xF434 (stride 6 starts)` |
| `FUN_022998` | `0x022A60` | `[r2+#0xf3ff]` | `0xF3FF-0xF435 (stride 6 starts)` |
| `low serial-buffer writer` | `0x005924` | `[r7]` | `0xE74E-0xE76F` |
| `high serial-buffer writer` | `0x0218EA` | `[r7]` | `0xF2A8-0xF481` |
| `knock freeze-frame writer` | `0x02EA86` | `[r2]` | `0xD840-0xDB7F` |
| `knock freeze-frame writer` | `0x02EB70` | `[r2]` | `0xD840-0xDB7F` |
| `low countdown callback object writer` | `0x02800C` | `[r9+#0x1e]` | `0x001E` |
| `low countdown callback object writer` | `0x028014` | `[r9+#0x22]` | `0x0022` |
| `low countdown callback object writer` | `0x02801C` | `[r9+#0x24]` | `0x0024` |
| `low countdown callback byte writer` | `0x02801A` | `[r8+#0x49c4]` | `0x49C6` |
| `diagnostic receive/transmit pointer` | `0x005630` | `[r5]` | `0x0000-0xF743 (stride 58400 starts)` |
| `diagnostic receive/transmit pointer` | `0x039022` | `[r4]` | `0x0000-0xF743 (stride 58400 starts)` |
| `diagnostic receive/transmit pointer` | `0x039150` | `[r4]` | `0x0000-0xF743 (stride 58400 starts)` |
| `diagnostic response pointer` | `0x02CD5E` | `[r4]` | `0x0000-0xF843 (stride 58656 starts)` |
| `CAN status pointer` | `0x0251B0` | `[r4]` | `0x0000, 0xD005, 0xD006` |
| `CAN status pointer` | `0x0255B8` | `[r5]` | `0x0000, 0xD009, 0xD00A` |
| `F596-selected native object` | `0x02D0E2` | `[r9+#0x66]` | `0x0066, 0xF07E, 0xF12A` |
| `F596-selected native object` | `0x02D102` | `[r9+#0x68]` | `0x0068, 0xF080, 0xF12C` |
| `F596-selected native object` | `0x02D116` | `[r9+#0x6a]` | `0x006A, 0xF082, 0xF12E` |
| `F596-selected native object` | `0x02D120` | `[r9+#0x6a]` | `0x006A, 0xF082, 0xF12E` |
| `F596-selected native object` | `0x02D158` | `[r9+#0x6c]` | `0x006C, 0xF084, 0xF130` |
| `F596-selected native object` | `0x02D1C8` | `[r9+#0x5e]` | `0x005E, 0xF076, 0xF122` |
| `F596-selected native object` | `0x02D1CC` | `[r9+#0x60]` | `0x0060, 0xF078, 0xF124` |
| `F596-selected native object` | `0x02D1D0` | `[r9+#0x62]` | `0x0062, 0xF07A, 0xF126` |
| `F596-selected native object` | `0x02D1D4` | `[r9+#0x64]` | `0x0064, 0xF07C, 0xF128` |
| `F596-selected native object` | `0x02D202` | `[r9+#0x5e]` | `0x005E, 0xF076, 0xF122` |
| `F596-selected native object` | `0x02D206` | `[r9+#0x60]` | `0x0060, 0xF078, 0xF124` |
| `F596-selected native object` | `0x02D20A` | `[r9+#0x62]` | `0x0062, 0xF07A, 0xF126` |
| `F596-selected native object` | `0x02D20E` | `[r9+#0x64]` | `0x0064, 0xF07C, 0xF128` |
| `F596-selected native object` | `0x02D22A` | `[r9+#0x40]` | `0x0040, 0xF058, 0xF104` |
| `F596-selected native object` | `0x02D234` | `[r9+#0x60]` | `0x0060, 0xF078, 0xF124` |
| `F596-selected native object` | `0x02D23E` | `[r9+#0x40]` | `0x0040, 0xF058, 0xF104` |
| `F596-selected native object` | `0x02D248` | `[r9+#0x5e]` | `0x005E, 0xF076, 0xF122` |
| `F596-selected native object` | `0x02D26E` | `[r9+#0x60]` | `0x0060, 0xF078, 0xF124` |
| `F596-selected native object` | `0x02D282` | `[r9+#0x40]` | `0x0040, 0xF058, 0xF104` |
| `F596-selected native object` | `0x02D28C` | `[r9+#0x5e]` | `0x005E, 0xF076, 0xF122` |
| `F596-selected native object` | `0x02D296` | `[r9+#0x40]` | `0x0040, 0xF058, 0xF104` |
| `F596-selected native object` | `0x02D2BC` | `[r9+#0x5e]` | `0x005E, 0xF076, 0xF122` |
| `F596-selected native object` | `0x02D2D0` | `[r9+#0x40]` | `0x0040, 0xF058, 0xF104` |
| `F596-selected native object` | `0x02D2DA` | `[r9+#0x60]` | `0x0060, 0xF078, 0xF124` |
| `F596-selected native object` | `0x02D2E2` | `[r9+#0x40]` | `0x0040, 0xF058, 0xF104` |
| `F596-selected native object` | `0x02D304` | `[r9+#0x5e]` | `0x005E, 0xF076, 0xF122` |
| `F596-selected native object` | `0x02D32E` | `[r9+#0x40]` | `0x0040, 0xF058, 0xF104` |
| `F596-selected native object` | `0x02D338` | `[r9+#0x60]` | `0x0060, 0xF078, 0xF124` |
| `F596-selected native object` | `0x02D340` | `[r9+#0x40]` | `0x0040, 0xF058, 0xF104` |
| `F596-selected native object` | `0x02D362` | `[r9+#0x60]` | `0x0060, 0xF078, 0xF124` |
| `F596-selected native object` | `0x02D38C` | `[r9+#0x40]` | `0x0040, 0xF058, 0xF104` |
| `F596-selected native object` | `0x02D396` | `[r9+#0x5e]` | `0x005E, 0xF076, 0xF122` |
| `F596-selected native object` | `0x02D39E` | `[r9+#0x40]` | `0x0040, 0xF058, 0xF104` |
| `F596-selected native object` | `0x02D3BE` | `[r9+#0x64]` | `0x0064, 0xF07C, 0xF128` |
| `F596-selected native object` | `0x02D3D4` | `[r9+#0x62]` | `0x0062, 0xF07A, 0xF126` |
| `F596-selected native object` | `0x02DBCA` | `[r9+#0x82]` | `0x0082, 0xF09A, 0xF146` |
| `F596-selected native object` | `0x02DBDA` | `[r9+#0x84]` | `0x0084, 0xF09C, 0xF148` |
| `F596-selected native object` | `0x02DC08` | `[r9+#0x86]` | `0x0086, 0xF09E, 0xF14A` |
| `F596-selected native object` | `0x02DC1E` | `[r9+#0x88]` | `0x0088, 0xF0A0, 0xF14C` |
| `F596-selected native object` | `0x02DCE0` | `[r9+#0x96]` | `0x0096, 0xF0AE, 0xF15A` |
| `F596-selected native object` | `0x02DCF6` | `[r9+#0x98]` | `0x0098, 0xF0B0, 0xF15C` |
| `F596-selected native object` | `0x02DCFE` | `[r9+#0x96]` | `0x0096, 0xF0AE, 0xF15A` |
| `F596-selected native object` | `0x02DD02` | `[r9+#0x98]` | `0x0098, 0xF0B0, 0xF15C` |
| `F596-selected native object field` | `0x02D31E` | `[r4]` | `0x006E, 0xF086, 0xF132` |
| `F596-selected native object field` | `0x02D37C` | `[r4]` | `0x006E, 0xF086, 0xF132` |
| `context-indexed coefficient writer` | `0x001DBA` | `[r0+#0xfa68]` | `0xFA68, 0xFA6A, 0xFA6C, 0xFA6E, 0xFA70, 0xFA72` |
| `context-indexed coefficient writer` | `0x001E5A` | `[r0+#0xfa68]` | `0xFA68, 0xFA6A, 0xFA6C, 0xFA6E, 0xFA70, 0xFA72` |
| `timer ISR destination table` | `0x038C2C` | `[r4]` | `0xFC82, 0xFC86, 0xFC8A, 0xFC8E, 0xFC92, 0xFC96` |
| `timer ISR destination table` | `0x038C3C` | `[r4+#0x2]` | `0xFC84, 0xFC88, 0xFC8C, 0xFC90, 0xFC94, 0xFC98` |
| `serial ISR destination table` | `0x039A24` | `[r2]` | `0xFC82, 0xFC86, 0xFC8A, 0xFC8E, 0xFC92, 0xFC96` |
| `serial ISR destination table` | `0x039A36` | `[r2]` | `0xFC84, 0xFC88, 0xFC8C, 0xFC90, 0xFC94, 0xFC98` |

### `FUN_02cd98` entry gate

- Result: **proven for the stock image**
- File envelope checked: `0x02CD7E-0x02D086`
- Segment-2 computed jump sites: **9**
- Immutable ROM-table targets decoded: **95**
- Computed targets inside the envelope: **0**
- Segment-2 indirect calls: **0**
- External direct entries: `0x02D096 -> 0x02CD7E`, `0x02D0A4 -> 0x02CD7E`
- Limit: Static stock-image control flow only; corrupted return state or modified dispatch data is outside this gate.

### Computed-dispatch coverage

| segment | JMPI sites | decoded tables | entries | CALLI sites | result |
|---|---:|---:|---:|---:|---|
| `segment2` | 9 | 9 | 95 | 0 | proven |
| `segment3` | 10 | 10 | 73 | 0 | proven |

### Object-pointer entry gates

| envelope | allowed entries | external calls | result |
|---|---|---:|---|
| `0x0280B4-0x0282ED` | `0x0280B4` | 1 | proven for the stock image |
| `0x02830A-0x0284FD` | `0x02830A` | 2 | proven for the stock image |
| `0x02853C-0x0287EF` | `0x02853C` | 1 | proven for the stock image |
| `0x02F6F0-0x02FD21` | `0x02F6F0, 0x02FA64, 0x02FB86` | 4 | proven for the stock image |
- Argument proof: every external call is immediately preceded by `mov r12,r9`; each entry copies `r12` into `r8`.

### Diagnostic-reader output gate

- Result: **proven for the stock image**
- File envelope checked: `0x027A8E-0x027BBB`
- External direct entries: `0x0272A0 -> 0x027A8E`
- Pointer proof: The only entry initializes r7=0xE523; the byte-sized descriptor count permits at most 255 iterations and two output bytes per iteration.
- Limit: Static stock-image control flow only; corrupted return state or modified dispatch data is outside this gate.

### Paired-state loop entry gate

- Result: **proven for the stock image**
- File envelope checked: `0x02AD4A-0x02AF81`
- External direct entries: `0x02AD46 -> 0x02AF6E`
- Pointer proof: The preheader and loop-back paths select r8=0xF182 or 0xF18A before any r8 write.
- Limit: Static stock-image control flow only; corrupted return state or modified dispatch data is outside this gate.

### Six-byte record entry gate

- Result: **proven for the stock image**
- File envelope checked: `0x0352AC-0x035311`
- External direct entries: `0x035CE0 -> 0x0352AC`, `0x035CEC -> 0x0352AC`
- Pointer proof: The sole entry zero-extends the low byte of r12, multiplies it by six, and adds 0xF63E.
- Limit: Static stock-image control flow only; corrupted return state or modified dispatch data is outside this gate.

### Byte-indexed state entry gate

- Result: **proven for the stock image**
- File envelope checked: `0x02E4F2-0x02EDA1`
- External direct entries: `0x030FDA -> 0x02E4F2`
- Pointer proof: The sole external entry reaches MOVBZ r6,RL5 at 0x02E50A; r6 is callee-saved and has no later assignment before the function epilogue.
- Limit: Static stock-image control flow only; corrupted return state or modified dispatch data is outside this gate.

### Five-record loop entry gate

- Result: **proven for the stock image**
- File envelope checked: `0x03147A-0x031517`
- External direct entries: `0x02C3DC -> 0x03147A`
- Pointer proof: The sole entry initializes r9=1; the loop writes through r5=r9*2 and continues only while r9<6.
- Limit: Static stock-image control flow only; corrupted return state or modified dispatch data is outside this gate.

### Object subroutine entry gate

- Result: **proven for the stock image**
- File envelope checked: `0x02FF6E-0x02FFBF`
- External direct entries: `0x02F37E -> 0x02FF6E`
- Pointer proof: The sole caller passes the proven FUN_02b0cc r9 object through r12; the entry copies r12 into r9.
- Limit: Static stock-image control flow only; corrupted return state or modified dispatch data is outside this gate.

### Object metric updater entry gates

- Bridge result: **proven for the stock image**
- Bridge envelope: `0x028880-0x0288CB`
- Bridge pointer proof: The sole caller passes the proven FUN_02853c r8 object through r12; the bridge preserves it in r7 and passes it onward through r12.
- Updater result: **proven for the stock image**
- Updater envelope: `0x02FE08-0x02FEDD`
- Updater external entries: `0x0288A6 -> 0x02FE08`, `0x02F3D8 -> 0x02FE08`, `0x02F6FE -> 0x02FE08`
- Updater pointer proof: The bridge, FUN_02b0cc, and FUN_02f6f0 callers each pass the proven 0xF018/0xF0C4 object through r12; the entry copies r12 into r8.
- Limit: Static stock-image control flow only; corrupted return state or modified dispatch data is outside this gate.

### Two-record entry gate

- Result: **proven for the stock image**
- File envelope checked: `0x03CF54-0x03D051`
- External direct entries: `0x03D4E0 -> 0x03CF54`, `0x03D4F0 -> 0x03CF54`, `0x03D514 -> 0x03CF54`, `0x03D5C8 -> 0x03CF54`
- Pointer proof: Every caller loads r12 with immediate 0xEFB4 or 0xEFC0; the entry copies r12 into r8.
- Limit: Static stock-image control flow only; corrupted return state or modified dispatch data is outside this gate.

### Paired-object update entry gate

- Result: **proven for the stock image**
- File envelope checked: `0x02FD6E-0x02FE07`
- External direct entries: `0x02FA2E -> 0x02FD6E`, `0x02FA58 -> 0x02FD6E`
- Pointer proof: Both callers pass the proven FUN_02b0cc r9 value through r12; the entry copies r12 to r8.
- Limit: Static stock-image control flow only; corrupted return state or modified dispatch data is outside this gate.

### Collision-relevant unresolved external-SRAM offset operands

- Remaining offsets: **0**

| offset | accesses |
|---|---:|

## `r0` software-stack investigation

- Normal startup context: CP=0xFC00 at file 0x0046C2; r0=0xFA46 at file 0x0046D8.
- Reserved arena: **`0xFA00-0xFA45` (70 bytes)**
- Boundary: The stack grows downward from exclusive top 0xFA46. Internal RAM starts at 0xFA00; 0xF800-0xF9FF is unmapped, so a valid contiguous native stack cannot descend into external SRAM.
- Canonical predecrement write sites: **424**
- Canonical postincrement read sites: **382**
- Ordinary direct claims inside the arena: **0**
- Logger claims inside the arena: **0**
- Alternate immediate-CP blocks reviewed: **22**
- Direct stack accesses inside those blocks: **0**
- Call-context evidence: CP=0xFAE8 at file 0x038ACE: normalized call chain 0x0223C0 -> 0x024740 -> 0x030950/0x030A50 has no [-r0] stores.
- Call-context evidence: CP=0xFB28 at file 0x038FC8: file 0x038FC4 first copies the old r0 into the new bank; normalized callees 0x02BA2E/0x02BA18 have no [-r0] stores.
- Additional context evidence: CP=0xFCAE and CP=0xFCCE save the old r0 into the new bank before SCXT and make no call before POP CP.
- Result: **Reserve the full 0xFA00-0xFA45 legal software-stack arena; canonical [-r0] writes are not a valid overwrite route to 0xF596.**
- Limit: The exact deepest native frame remains unproven, but is unnecessary for collision-safe allocation because the full legal arena is reserved.

## Remaining unresolved-write gate

- Sites after canonical stack exclusion: **758**
- Sites bounded by proven value sets: **455**
- Remaining unbounded sites: **303**
- Inside named function bodies: **328**
- Outside named bodies but directly control-flow reachable: **199**
- Outside named bodies and not directly reached: **231**
- Unresolved direct-control transfers: **0**
- Limit: The collision-relevant count excludes only high-segment instructions rejected by the conservative stock reachability gate.

| base register | writes |
|---|---:|
| `r9` | 208 |
| `r5` | 128 |
| `r4` | 118 |
| `r8` | 79 |
| `r2` | 72 |
| `r0` | 66 |
| `r12` | 33 |
| `r7` | 16 |
| `r15` | 10 |
| `r6` | 10 |
| `r14` | 9 |
| `r10` | 4 |
| `r1` | 2 |
| `r3` | 2 |
| `r13` | 1 |

Top function/register groups:

| function | base register | writes |
|---|---|---:|
| `<unmapped>` | `r9` | 129 |
| `<unmapped>` | `r5` | 73 |
| `<unmapped>` | `r4` | 70 |
| `<unmapped>` | `r8` | 56 |
| `<unmapped>` | `r0` | 44 |
| `<unmapped>` | `r2` | 22 |
| `FUN_02cd98` | `r9` | 22 |
| `<unmapped>` | `r12` | 21 |
| `FUN_0044e6` | `r2` | 18 |
| `FUN_02b0cc` | `r9` | 17 |
| `FUN_001bcc` | `r15` | 10 |
| `FUN_020986` | `r5` | 10 |
| `FUN_0357d2` | `r2` | 10 |
| `FUN_028100` | `r9` | 8 |
| `FUN_0357d2` | `r4` | 7 |
| `FUN_02218a` | `r2` | 6 |
| `FUN_022998` | `r2` | 6 |
| `<unmapped>` | `r7` | 5 |
| `FUN_027b1c` | `r7` | 5 |
| `FUN_02b0cc` | `r4` | 5 |
| `FUN_02b442` | `r12` | 5 |
| `flash_write_orchestrator` | `r0` | 4 |
| `<unmapped>` | `r14` | 4 |
| `FUN_023a58` | `r8` | 4 |
| `FUN_027710` | `r12` | 4 |

Directly reachable shared/tail attribution:

- Single owner: **95**
- Multiple owners: **104**
- Unattributed: **0**
- Maximum owners on one shared site: **52**

| single originating owner | writes |
|---|---:|
| `<computed table 0xAC42>` | 38 |
| `FUN_024e30` | 15 |
| `<conservative high-segment transfer>` | 12 |
| `<computed table 0xA53C>` | 7 |
| `<computed table 0xACB4>` | 6 |
| `<computed table 0xADE8>` | 5 |
| `<computed table 0xACAA>` | 4 |
| `FUN_02c0b4` | 2 |
| `FUN_0362b4` | 2 |
| `<computed table 0xABBC>` | 1 |
| `<computed table 0xAC2C>` | 1 |
| `FUN_02ec22` | 1 |
| `FUN_0360f4` | 1 |

Single-owner priority value-set groups (`r9`, `r5`, `r4`):

| owner | base register | writes |
|---|---|---:|
| `<computed table 0xAC42>` | `r9` | 36 |
| `FUN_02cd98` | `r9` | 22 |
| `FUN_02b0cc` | `r9` | 17 |
| `FUN_024e30` | `r5` | 11 |
| `FUN_020986` | `r5` | 10 |
| `FUN_028100` | `r9` | 8 |
| `<computed table 0xA53C>` | `r5` | 7 |
| `FUN_0357d2` | `r4` | 7 |
| `FUN_02b0cc` | `r4` | 5 |
| `FUN_02dc82` | `r9` | 4 |
| `FUN_0352c0` | `r4` | 4 |
| `FUN_036128` | `r5` | 4 |
| `FUN_0362b4` | `r4` | 4 |
| `FUN_000db0` | `r5` | 3 |
| `FUN_020a26` | `r9` | 3 |
| `FUN_020a26` | `r4` | 3 |
| `FUN_020f5a` | `r5` | 3 |
| `<conservative high-segment transfer>` | `r5` | 3 |
| `FUN_02d1ca` | `r9` | 3 |
| `FUN_02d804` | `r9` | 3 |
| `<computed table 0xACAA>` | `r5` | 3 |
| `FUN_02ff78` | `r9` | 3 |
| `FUN_02152c` | `r9` | 2 |
| `FUN_021f40` | `r9` | 2 |
| `<conservative high-segment transfer>` | `r4` | 2 |

## Stack/context assignments

| register | source | assignments |
|---|---|---:|
| CP | `#0xfae4` | 1 |
| CP | `#0xfae6` | 1 |
| CP | `#0xfae8` | 1 |
| CP | `#0xfaf2` | 1 |
| CP | `#0xfaf8` | 1 |
| CP | `#0xfb00` | 1 |
| CP | `#0xfb06` | 3 |
| CP | `#0xfb20` | 4 |
| CP | `#0xfb28` | 1 |
| CP | `#0xfb48` | 1 |
| CP | `#0xfb4c` | 1 |
| CP | `#0xfb50` | 1 |
| CP | `#0xfb52` | 1 |
| CP | `#0xfb56` | 1 |
| CP | `#0xfb5c` | 1 |
| CP | `#0xfc00` | 3 |
| CP | `#0xfc42` | 1 |
| CP | `#0xfcae` | 1 |
| CP | `#0xfcce` | 1 |
| CP | `0xa314` | 1 |
| CP | `r0` | 1 |
| SP | `#0xfc00` | 1 |
| STKOV | `#0xfb64` | 1 |
| STKUN | `#0xfc00` | 1 |

## Current conclusions

- The exact stock image has three conditionally certified post-startup
  byte ranges: `0xD800-0xD83F`, `0xDB8F-0xDC1F`, and `0xE847-0xE85F`.
- Its 1,024-byte IRAM is fully classified. Two additional post-startup
  ranges are certified: `0xFC3F-0xFC41` (3 bytes) and
  `0xFD80-0xFDDB` (92 bytes), for 95 bytes total / 94 word-aligned bytes.
- `0xFD7C-0xFD7F` is not stock-runtime-owned, but is excluded because it
  overlaps the current Soft-BSL CRC table at `0xFD60-0xFD7F`.
- All five are overwritten by startup RAM tests; patch state must
  be initialized only after the main firmware handoff and is not reset-persistent.
- The conservative stock-live frontier is closed at zero unbounded reads
  and zero unbounded writes, including the interrupt-driven scheduler graph.
- DPP3 remains on reset page 3. PEC channels 3-6 remain inactive; active
  PEC-channel envelopes are reserved as implicit ownership.
- `0xDC34-0xDFFF` and `0xE000-0xE31F` remain transiently owned by
  stock DS2 download and/or Soft-BSL and are not exclusive patch RAM.
- `0xE320-0xE41F` is the stock RAM-resident flash-driver copy and execution
  window, not spare RAM.
- The full `0xFA00-0xFA45` software-stack arena is reserved; an exact
  observed low-water mark is intentionally not used as an allocation boundary.
- Hardware BSL uses CP `0xFA00`, stack `0xFA20-0xFA3F`, and first-stage
  code `0xFA40-0xFA5F`; arbitrary downloaded second-stage IRAM use is
  outside this certificate.
- No unlisted gap is certified, and no result is generalized to another
  MS41 image or to a patch that changes the proven invariants.

## Revalidation boundary

Re-run the complete isolated analyzer before using these ranges with a
different ROM or after changing hooks, scheduler reachability, native pointer
writers, DPP3, PEC configuration, context banks, or stack boundaries.
Hardware actions, production-documentation edits, and allocator integration
remain outside this investigation.
