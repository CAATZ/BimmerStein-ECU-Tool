# Building the Soft-BSL RAM agents

The source tree contains four C166 RAM agents:

- `agent.hex`: AMD/JEDEC-command flash devices.
- `agent_28f.hex`: Intel 28F200-command flash devices.
- `eeprom_agent.hex`: chip-independent MS41 24C04 read/replay-safe byte service.
- `st9030_agent.hex`: fixed-slot C166 ASC1 probe plus bounded,
  stock-derived ST9030 token-gate and telemetry operations.

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
python engines/softbsl/preprocess_asm.py engines/softbsl/st9030_agent_build.asm build/agent_st9030_ready.asm
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
byte-for-byte with the decoded runtime file. A rebuild is accepted only when all
four comparisons are exact. The current reviewed results are:

| Family | Load address | Size | Runtime SHA-256 |
| --- | ---: | ---: | --- |
| AMD/JEDEC | `0xD800` | 1498 | `00eea04eae248f35f77140913bd27a0ffc0003251acd361db2ee80c4b336cb72` |
| Intel 28F200 | `0xD800` | 1464 | `5c35c219cf350f9dfd936be92907b2a44d9c52e0cb40d0831f805f49f8a418c2` |
| MS41 EEPROM | `0xD800` | 1442 | `e1c17e3a4e3684ab99f8d3ca98506a1829d37315028a00ff86a04c6f4ca3949f` |
| ST9030 bounded probe/gate/telemetry | `0xD800` | 1944 | `cd43358bde39c4e2a5dd00884b7775df1662802d08886df9a209027c32706ee2` |

## ST9030 bounded probe, token gate, and telemetry

The ST9030 agent contains no flash or EEPROM write command and no arbitrary
ASC1 command, length, or payload primitive. Before a fixed-slot read it
recreates the exact stock 1429861 ASC1 configuration (`S1BG=1`,
`S1CON=0x801C`) and explicitly restores P3.8 as a high TXD1 output and P3.9 as
a high-latched RXD1 input. Its allowlisted U3-to-U2 reply slots remain:

| Slot | 9-bit command | Raw response words |
| ---: | ---: | ---: |
| 0 | `0x102` | 2 |
| 1 | `0x103` | 2 |
| 2 | `0x105` | 4 |
| 3 | `0x108` | 5 |
| 4 | `0x109` | 5 |
| 5 | `0x10A` | 12 |
| 6 | `0x10E` | 1 |

ASC0 host commands are `i`, `s`, `r slot crc16('r'+slot)`,
`g ST90 crc16('gST90')`, `t ST0B crc16('tST0B')`, `q C3 3C`, and `R 9C 9C`.
CRC16 is the reflected
`0xA001` algorithm with `0xFFFF` initial state and is transferred high byte
first. Agent identity is version 5, capabilities `0x0F`, and seven replay
slots. Replies have these exact sizes and layouts:

- `i`: 6 bytes: `version, capabilities, slot_count, entry_E740, crc_hi, crc_lo`.
- `s`: 13 bytes: `status, S1CON_be, S1BG_be, S1TIC_be, S1RIC_be, S1EIC_be,
  crc_hi, crc_lo`.
- `r`: `5 + 2*N` bytes: `status, slot, N, N raw S1RBUF words big-endian,
  crc_hi, crc_lo`.
- `g`: 40 bytes: `status, 12 raw 0x10A words big-endian, 11 derived 0x10C
  response bytes, one raw 0x10E word big-endian, crc_hi, crc_lo`. Status tells
  whether the response transmit completed; failures can contain only a
  candidate response rather than a complete wire transcript.
- `t`: 66 bytes: `status, issued_attempt_count, terminal_overall_FE52_delta_be,
  15 raw 0x10E words big-endian, 15 post-pacing observation deltas big-endian,
  crc_hi, crc_lo`. Unissued and unavailable slots remain zero after an early
  failure, and the CRC covers the complete fixed-size response.

Read status is `0=ok`, `1=invalid slot`, `2=request CRC mismatch`, `3=ASC1
transmit timeout`, `4=ASC1 receive timeout`, and `5=ASC1 error interrupt`.
Every raw S1RBUF value is retained as a 16-bit word so its ninth bit is not
lost. The fixed `g` operation configures ASC1 once for the complete exchange,
then receives exactly 12 words after `0x10A`. It proceeds only if bit 8 is
clear in all 12 raw words and the last word is exactly `0x00A0`. It rotates
the first 11 low bytes left by three positions, sends the `0x10C` header plus
those 11 eight-bit words, and only then requests exactly one `0x10E` word.
All polling loops are bounded and continue to service the watchdog. The agent
captures the complete `ST90+CRC` request tail before clearing its transcript
buffers; clearing first can lose a contiguous ASC0 byte at 187500 baud before
the polled receive helper arms itself.

Gate status is `0=ok`, `1=request CRC mismatch`, `2=request magic mismatch`,
`3..5=0x10A transmit/receive/error`, `6=0x10A ninth bit set`, `7=0x10A status
not A0`, `8..10=0x10C header/payload/error`, `11..13=0x10E
transmit/receive/error`, `14=0x10E ninth bit set`, `15=0x10E A1 pending`,
`16=0x10E FF explicit failure`, and `17=unexpected 0x10E status`. This bounded
gate revision performs one `0x10E` query only: A1 is returned as pending, never
reported as completion and never retried implicitly. This is a bounded
stock-derived operation, not the stock firmware's complete paced A1 retry
policy. The image has no caller-selected C166-to-ST9030 command, length, or
payload.

The fixed `t` operation captures the complete `ST0B+CRC` request tail before
clearing 96 bytes of transcript storage, zeroes the attempt count before
validating CRC/magic, configures ASC1 once, snapshots CAPCOM T1 (`FE52`), and
transmits exactly `0x010B,0x0002,0x0000,0x0000,0x0000` once. It then issues
bounded `0x010E` polls. Only exact ninth-bit-clear `0x00A1` permits another
poll; `0x00A0` means only that readiness was observed, not that telemetry was
read or completed. Each attempt snapshots FE52 immediately before its blocking
`0x10E` transmit/receive operation, so both transmit and receive time count
toward the stock-derived minimum interval.
The watchdog is serviced while waiting until the unsigned attempt delta is at
least `0x19` (the stock helper loops through `0x18`), with an independent
`0xFFFF`-poll fail-closed guard if T1 stalls.

The overall window begins before the fixed 0x10B transmit. A post-pacing
observation must remain at most `0x176`; `0x177` or later is preserved but
classified as expiry before interpreting even an A0. The operation issues at
most 15 polls (the initial poll plus 14 retries), preserves every raw word and
its post-pacing overall observation, never attempts a sixteenth poll, never
resends `0x010B`, never sends `0x010D`, and exposes no generic mailbox
primitive. These timestamps are conservative evidence samples after the
blocking receive/pacing point, not cycle-identical stock interrupt timestamps.
Raw status words with any high-byte bit set are rejected by the agent; the host
also rejects values outside the 9-bit S1RBUF range. The attempt-cap status is a
defensive invariant guard and is unreachable with valid non-wrapping FE52
progress because 15 intervals of `0x19` already reach `0x177`.

Telemetry status is `0=0x10E/A0 readiness observed`, `1=request CRC mismatch`,
`2=request magic mismatch`, `3=0x10B header transmit timeout`, `4=0x10B
payload transmit timeout`, `5=0x10B ASC1 error`, `6..8=active 0x10E
transmit/receive/error`, `9=0x10E high bits set`, `10=0x10E/FF`,
`11=unexpected 0x10E status`, `12=FE52 stall guard`, `13=overall FE52 expiry`,
and `14=defensive attempt-cap invariant`. No status represents `0x10D` data
because version 5 deliberately stops when readiness is observed.

Artifact verification by itself proves source/payload identity only. A bounded
2026-08-10 run on an isolated, current-limited MS41.3 bench ECU subsequently
completed the fixed `0x10A` receive, three-byte-left-rotated `0x10C` response,
and one-shot `0x10E/A0` acknowledgement with payload SHA-256
`8eb992f3f272766f2549b7fed91aa5f17dc41a198417591e7eadf2c99dbc6a9a`.
Protected return to normal DS2 was confirmed. This is powered proof only of the
bounded token-gate exchange under the tested conditions; it is not vehicle
proof, a live-flash hash, an arbitrary ST9030 command path, or mask-ROM access.
The added fixed telemetry operation and its new payload hash have only offline
source/artifact and emulator admission until a separately authorized bench run;
the earlier token-gate result does not constitute telemetry hardware proof.

The source-tree host is `engines.softbsl.st9030_proxy`. With none of
`--replay`, `--stock-gate`, or `--stock-telemetry`, it performs exact
local-image and live-install admission,
loads the RAM agent, records the C166 ASC1 register snapshot, and exits through
the protected normal-reset path. Each `--replay SLOT` is an explicit active
request from the fixed table. `--stock-gate` selects only the fixed operation
above; `--stock-telemetry` selects only the fixed telemetry operation. Active
modes are mutually exclusive. The existing `q`/`R` protected normal-reset and
recovery behavior is unchanged. Do not run an active mode on a vehicle or
load-connected ECU; a first telemetry bench session requires separate hardware
authorization and an isolated, current-limited setup.

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
