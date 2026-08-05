# MS41 24C04 EEPROM map and RAM tool

## Current status

MS41.0, MS41.1, MS41.2, and MS41.3 use the same 512-byte 24C04 interface.
The repository now contains:

- an EEPROM-only C166 RAM agent;
- a direct physical 512-byte reader;
- an offline inspector/decoder;
- a replay-safe changed-byte writer for exact 512-byte targets;
- a shared hex-style EEPROM Manager for the ECU Agent and CH341A;
- family-specific transmission shortcuts and Bins integration;
- artifact-integrity, protocol, and safety tests.

The RAM agent contains no flash erase/program commands. Its stock-derived
P3.3/P3.12 I2C implementation reads every address `0x000..0x1FF` without calling
variant-specific firmware routines. RAM access is admitted only on a recognized
MS41.0-MS41.3 ECU with an installed Soft-BSL bank marker and the matching
variant door. CalGuard and flash-chip family are not admission requirements.
No EEPROM write has been performed on a physical ECU as part of this work.

The transmission shortcut selects the family-specific persisted record:
MS41.0 `0x196`, MS41.1 `0x1CC`, and MS41.2/MS41.3 `0x1CA`. It changes only
mode bits `0..1` and the record check. Whether a calibration actively consumes
that persisted selector remains a separate firmware/configuration question.

## Important correction about the supplied file

`eeprom_mirror_live.bin` is 512 bytes, but only bytes `0x000..0x1D9` are a real
474-byte RAM mirror. Bytes `0x1DA..0x1FF` are synthetic zero padding.

It is useful for decoding the lower records, but it is not a physical full
EEPROM image and must never be written as one. Its SHA-256 is:

`0a6ea504cbb9d129bd37e96c9be72ab9a956a5787ae3623c031f9a1cd52657bd`

The new RAM reader directly acquires all `0x200` bytes, including the two bank
halves and the boot/identity tail.

## Storage and integrity

The lower mirror is family-specific. MS41.0 reads `0x000..0x1A5`, MS41.1
reads `0x000..0x1DB`, and MS41.2/MS41.3 read `0x000..0x1D9`. The inspector's
`--variant` option selects the matching record boundaries and descriptions.
MS41.2/MS41.3 contain 21 records, sixteen of which end in a two-byte
little-endian check value:

```text
check = (sum(payload bytes) + 1) & 0xFFFF
```

The check is not a polynomial CRC. Five lower records are unchecked, but
unchecked does not mean safe to edit. Bytes `0x1DA..0x1DC` are outside the
lower record map. The boot/identity tail starts at `0x1DD` and uses separate
firmware routines and triplicate state voting.

## MS41.2/MS41.3 lower EEPROM record map

The live values below come from the authentic first 474 bytes of the supplied
mirror. Names marked inferred describe proven behavior without claiming an
unknown BMW factory label.

| Offset | Len | Check | Meaning and live interpretation | Edit policy |
|---:|---:|:---:|---|---|
| `000` | 6 | No | Three little-endian progression words. Firmware votes them, increments the base once per startup, saves base/base+1/base+2, and stamps the base into fault/history records. Live base `0x235B` = 9051 ignition/operating cycles. | Read-only history |
| `006` | 4 | No | Little-endian monotonic EEPROM save/commit count. It is incremented immediately before each store. Live `45122`. | Read-only history |
| `00A` | 4 | Yes | Firmware compatibility/adaptation-invalidation key. A valid key belonging to a different ECU identity causes the downstream adaptation mirror to be cleared. Live payload `0x02CD`, check `0x00D0`. | Never expose as a normal edit |
| `00E` | 68 | Yes | 64-cell averaged knock-adaptation table, one global correction byte, one reserved byte, then check. Cells use offset-binary ignition correction `(raw-128)*0.375°`; `0x80` is neutral. Live table and global value are neutral. | Read/reset candidate only after field-aware validation |
| `052` | 4 | Yes | Two-byte offset-binary filtered adaptive load-model correction/offset. Live zero; physical engineering units remain unresolved. This is not a directly stored barometric-altitude value. | Read-only |
| `056` | 6 | Yes | Three bounded VVT/VANOS reference and adaptive-controller state bytes plus one reserved byte. Live payload `34 23 01 00`. | Read-only adaptive state |
| `05C` | 4 | Yes | Learned throttle-position adaptation word. Live raw `0x1F13` = 7955; the logger's provisional scale gives about `12.14%`. | Prefer a defined reset operation, not arbitrary editing |
| `060` | 8 | Yes | Five offset-binary engine-roughness segment adaptive values plus one reserved byte. These are learned crankshaft sensor-wheel segment-time corrections used by misfire detection. Live values are neutral `0x80`. | Read/reset candidate only after a defined service workflow |
| `068` | 40 | Yes | Active bank/base adaptation, IdleFT1, LTFT1, IdleFT2, LTFT2, then internal saved values whose individual meanings remain unresolved. `0x8000` is neutral for the active words. Live active values are neutral; cached tail is zero. Idle trim scale is `(u16-32768)*5.34 µs`; LTFT is `(u16-32768)*100/65535 %`. | Field-aware reset candidate; preserve unresolved bytes |
| `090` | 138 | Yes | DTC occurrence count, ten DTC identifiers, ten 12-byte occurrence/environment records, reserved bytes, and flags/coding. Live count is zero, so remaining slot bytes are inactive/stale. | Diagnostic memory, not a tune parameter |
| `11A` | 12 | Yes | Idle-speed controller/actuator learned multiplicative factor and integral accumulators. The factor is neutral `0x80`; one internal correction byte still lacks a resolved physical-unit label. | Read-only |
| `126` | 32 | Yes | Per-cylinder rough-running/load-correction learning, counters, correction words, accumulator, and completion flag. Live state is the default/unlearned value. | Read/reset candidate only after a defined service workflow |
| `146` | 110 | Yes | DTC status words, ten six-byte fault slots, freeze-frame/environment data, and ancillary snapshot bytes. | Diagnostic memory, not editable parameters |
| `1B4` | 4 | Yes | Coolant-temperature related saved state. The firmware behavior is traced, but the concise factory label remains unresolved. Live raw `0x7A`; with the associated calibration offset, the effective threshold is about `40.1°C`. | Read-only service state |
| `1B8` | 10 | Yes | Seven persistent diagnostic-event counters plus one reserved byte. This is not operating hours. Live counters are all zero. | Read-only diagnostic history |
| `1C2` | 4 | No | Three-byte progression nonce plus pad, used for actuator/output-test anti-replay validation. Live `0C 0D 0E 00`. | Never arbitrary-edit |
| `1C6` | 4 | Yes | Persistent load-collective (`Lastkollektiv`) internal model accumulator. Live zero. | Read-only |
| `1CA` | 4 | Yes | Persisted transmission/coding word. Only bits `0..1` select mode: `1` automatic, `2` manual. Bits `2..15` have separate ownership and must be preserved. Live `0E 00 0F 00`: manual, auxiliary bits `0x000C`, valid check. It affects boot only when the calibration selector is `0x2C`. | Named transmission shortcut; masked RMW preserves all other bits |
| `1CE` | 4 | Yes | Last-shutdown coolant raw byte and consecutive warm-restart/no-cooldown count. Live raw `0x7C` ≈ `44.6°C`, count zero. | Read-only history |
| `1D2` | 4 | No | Peak RPM divided by 32, ignition-cycle stamp, and pad. Live zero. It is write-only telemetry in the discovered paths. | Read-only history |
| `1D6` | 4 | No | Qualified over-rev event count, last-cycle stamp, and pad. The behavior is strong; exact BMW label is inferred. Live zero. | Read-only history |

### Corrected adaptive-record locations by family

| Function | MS41.0 | MS41.1 | MS41.2/MS41.3 | Inspector description |
|---|---:|---:|---:|---|
| Filtered adaptive load-model correction/offset | `052..055` | `032..035` | `052..055` | `load_model_correction` |
| VVT/VANOS reference and adaptive-controller state | `056..05B` | `036..03B` | `056..05B` | `vanos_adaptation` |
| Engine-roughness segment adaptive values | `060..067` | `040..047` | `060..067` | `engine_roughness_segment_adaptation` |
| Idle-speed controller/actuator learned factor/integral accumulators | `0EA..0F5` | `0E8..0F3` | `11A..125` | `idle_regulator_adaptation` |

The [MS4X Siemens keyword translation](https://www.ms4x.net/index.php?title=Siemens_Keyword_Translation)
corroborates vocabulary such as `LOAD`, `MDL`, `COR`, `VVT`, `ER`, `SEG`,
`ISC`, and `ISA`. It is terminology evidence, not an address map or proof of
an exact compound MS41 symbol.

### What is and is not a “parameter”

The EEPROM is not another tune map. It mostly stores:

- adaptive state;
- DTC and freeze-frame history;
- startup/save counters;
- service/output-test anti-replay state;
- shutdown snapshots;
- boot/flash state and identity copies;
- family-specific cached or unresolved slots without a proven direct consumer.

The editor therefore emphasizes read/annotate/export and named shortcuts. For
expert raw edits, **Update Checks for Edited Records** recalculates only known
checked records whose payload changed from the loaded image. It does not repair
untouched invalid records. The final write validator still requires every
edited checked record to contain a valid target check.

## Tail map (`0x1DD..0x1FF`)

The supplied padded mirror has no authentic tail. A separate direct 512-byte
chip dump established one complete physical tail. Its values are evidence for
that dump, not universal defaults.

| Range | Meaning |
|---:|---|
| `1DD..1DF` | Triplicate boot/flash progression state. `00 01 02` votes to normal state `E740=0`; `03 04 05` votes to normal state `E740=3`; seeded recovery `01 02 03` votes to the stock flash listener at `E740=1`. |
| `1E0` | Fourth byte written with the boot-state sequence; exact role unresolved. The direct dump contains `0F`. |
| `1E1..1E2` | Unknown; no dedicated normal writer found. The direct dump contains `FF 0E`. |
| `1E3..1EE` | Twelve-byte ASCII program/identify descriptor, live `111009091202`; exact BMW label unresolved. |
| `1EF..1F5` | Seven-byte BMW DME part/HW-variant number copy, `1406464` in the direct dump. |
| `1F6..1FC` | Second copy of the same seven-byte number, also `1406464` in the direct dump. |
| `1FD..1FE` | Unknown; no dedicated normal writer found. The direct dump contains `00 00`. |
| `1FF` | No proven normal parameter meaning. The direct dump contains `00`. |

The whole tail is boot/identity-owned. It has no casual named editor. Expert
raw edits still pass the same exact-image backup, compare, write, and readback
gates; Seed ECU remains a separate three-byte operation.

## RAM-agent design

Admission is deliberately narrow:

1. Confirm a recognized MS41.0-MS41.3 program identity.
2. Confirm a live Soft-BSL `B`/`T` bank marker.
3. Confirm the matching variant's installed `0x2A` door hook.
4. Accept only the known `E740` states `0`, `1`, or `3`.
5. Enter through the normal installed Soft-BSL staged path. The persistent
   loader receives the small speed-loader stage at 9,600 baud; that stage then
   receives the 1,442-byte EEPROM agent at 187,500, 93,750, or 9,600 baud.
   GUI `auto` tries high first and repeats the complete entry at low before any
   EEPROM write if fast entry fails. CalGuard recovery remains an explicit route.
6. Require agent v3 with full-read, generic-byte-write, self-contained-I2C, and
   conditional-finalizer capabilities.
7. Require stable full reads, then reject an image if all 512 bytes contain one
   repeated value; this includes common disconnected/stuck-bus `00` and `FF`
   results.

The agent occupies transient takeover RAM:

- code `D800..DDA1`;
- two 512-byte read buffers at `E000..E3FF`;
- transaction state at `E400+`;
- software stack below `E600`;
- context and system stacks at the existing `FA00`/`FC00` reservations.

For a dump, the agent physically reads all 512 bytes twice into differently
prefilled buffers, compares them, and returns the data plus CRC16. The host asks
for two matching complete dumps as a second integrity layer.

Exit is `q C3 3C`. An agent entered with `E740=1` writes `E740=0`, invokes the
common stock marker finalizer at CPU `0x1A62`, and then executes protected SRST.
Entries that began in state `0` or `3` reset without changing that state. The
shared Soft-BSL failure cleanup `R 9C 9C` is an alias for the same conditional
finalizer. Stock DS2 with `E740=0` or `3` must answer before the port is released;
a power cycle alone does not clear `E740=1`.

## Writer transaction

`write_image()` accepts an exact 512-byte target for the selected family. The
shared Manager exposes expert raw editing plus a masked transmission shortcut.
The host applies these gates:

1. Reject uniform captures and zero-padded RAM mirrors as write targets.
2. Read a stable complete before-image and save it immutably.
3. Permit untouched invalid records, but require every changed checked record
   to contain its correct additive check.
4. Build only changed one-byte operations. For a changed checked payload,
   invalidate its low check byte first, write payload bytes, then restore both
   check bytes last.
5. Ask for explicit confirmation after the before-image and exact hashes exist.
6. For each operation, the agent reads twice, compares the expected byte,
   writes once, and reads twice for exact replacement.
7. The request CRC covers command, address, expected byte, and replacement. If
   an exact request is replayed after the write completed, the agent returns
   success without writing again.
8. Read all 512 bytes back, require the exact target, archive the after-image,
   then return to normal DS2.

If a reply or readback is uncertain, the same RAM session remains open. The host
reads the full chip, permits only before/target values at planned offsets, and
resumes only the remaining operations after explicit confirmation. It never
opens a second session or blindly restarts the original sequence.

## Application workflow

The EEPROM tab has three panels: **Loaded EEPROM Image**, **ECU Agent**, and
**CH341A Programmer**. Files and EEPROM entries from Bins load into the same
image panel, and both writers use that displayed image. **EEPROM Manager** opens
the shared 32-by-16 hex editor and decoded field table. Successful reads are
first archived in Bins as `EEPROM` with source and layout metadata; only then is
an optional external copy offered. ROM flash/config/patch actions stay
unavailable for a 512-byte EEPROM entry.

CH341A retains the narrow **Seed ECU Recovery** action. Its inverse is
**Restore Pre-Seed State**, which uses the exact saved `00 01 02` or `03 04 05`
source rather than guessing a marker.

## Commands

Offline inspection is safe and performs no serial I/O:

```powershell
python -m engines.softbsl.eeprom_ram inspect `
  "path\to\eeprom_mirror_live.bin"
```

Direct physical read (live hardware action; requires installed Soft-BSL and
defaults to staged high baud with complete 9,600-baud fallback):

```powershell
python -m engines.softbsl.eeprom_ram dump COM1 `
  -o "backups\eeprom\MS41_COM1_24C04_full.bin" --baud auto
```

The guarded writer exists as
`engines.softbsl.eeprom_ram.write_image()`; the GUI uses it for edited targets.
`write_transmission()` remains the narrow CLI shortcut and resolves the offset
from the connected program family. Its CLI requires `--yes-i-understand` plus
typed `WRITE EEPROM`; a retained partial sequence uses typed `RESUME EEPROM`.

## Evidence boundary

- Record geometry, check algorithm, and consumers/writers come from exhaustive
  direct-call traces of the canonical MS41.0, MS41.1, and MS41.2 programs.
  MS41.3 uses the byte-identical MS41.2 EEPROM application paths and layout.
- Exact RAM-agent bytes are assembler-manifest checked. The exact 1,442-byte v3
  payload performs full reads on MS41.0, MS41.1, MS41.2, and MS41.3 reference
  images in exact execution checks. Generic writes, exact replay, stale compares,
  I2C failures, and both conditional finalizer commands are exact-byte tested.
- Exact-image functional proof is not UART timing, electrical, bench, or on-car
  proof.
- The current physical ECU has not been written by this tool.
- OEM labels for the `00A` key, one `11A` correction byte, the `1B4` latch, and
  especially the `1D6` event remain inferred from behavior.
