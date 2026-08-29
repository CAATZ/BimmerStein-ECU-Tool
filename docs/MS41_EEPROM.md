# MS41 24C04 EEPROM map and RAM tool

## Current status

MS41.0, MS41.1, MS41.2, and MS41.3 use the same 512-byte 24C04 interface.
The repository now contains:

- an EEPROM-only C166 RAM agent;
- a direct physical 512-byte reader;
- an offline inspector/decoder;
- a replay-safe changed-byte writer for exact 512-byte targets;
- a shared hex-style EEPROM Manager for the ECU Agent and CH341A;
- an Android full-screen EEPROM Editor with linked Decoded/Hex views and explicit Save copy;
- a read-only Android EEPROM comparison with named, check, and raw-only differences;
- family-specific transmission shortcuts and Bins integration;
- artifact-integrity, protocol, and safety tests.

The RAM agent contains no flash erase/program commands. Its stock-derived
P3.3/P3.12 I2C implementation reads every address `0x000..0x1FF` without calling
variant-specific firmware routines. RAM access is admitted only on a recognized
MS41.0-MS41.3 ECU with an installed Soft-BSL bank marker and the matching
variant door. CalGuard and flash-chip family are not admission requirements.
Exact-image execution checks cover the RAM agent and guarded write paths. These
checks do not establish electrical, bench, or on-car qualification for every
program family.

The transmission shortcut selects the family-specific persisted record:
MS41.0 `0x196`, MS41.1 `0x1CC`, and MS41.2/MS41.3 `0x1CA`. It changes only
mode bits `0..1` and the record check. Whether a calibration actively consumes
that persisted selector remains a separate firmware/configuration question.

## Physical dumps and padded RAM mirrors

A 512-byte file is not necessarily a full physical EEPROM capture. A lower
RAM mirror can be padded to that size: for MS41.2/.3, only bytes
`0x000..0x1D9` would then be authentic and `0x1DA..0x1FF` synthetic.
The lower mirror lengths differ by family, as listed below.

The inspector warns when the selected family's entire upper suffix is zero.
Zeros alone do not prove provenance, but the decoded view withholds tail
identifiers and boot status rather than interpreting possible padding as real
data. Such an image can be inspected offline, but the physical-write validator
rejects it. Shorter files are not silently padded.

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

These descriptions refer to stored bytes, not necessarily the live ECU state.
Names marked inferred describe traced behavior without claiming an unknown
BMW factory label. An invalid record can be rejected or replaced by defaults
in RAM without rewriting the stored payload.

| Offset | Len | Check | Stored meaning | Android decoded edit policy |
|---:|---:|:---:|---|---|
| `000` | 6 | No | Redundant operating-time counter: three little-endian words `n,n+1,n+2` with 16-bit wrap. The voted value uses nominal 0.1-hour ticks, not ignition cycles. | One advanced logical time edit; physical words read-only |
| `006` | 4 | No | Little-endian monotonic EEPROM save/commit count, incremented immediately before each store. | Advanced counter edit |
| `00A` | 4 | Yes | Firmware compatibility/adaptation-invalidation key. A valid key belonging to a different ECU identity causes the downstream adaptation mirror to be cleared. | Advanced raw key edit; not a normal tune parameter |
| `00E` | 68 | Yes | One row-major 16-RPM×4-load averaged knock table, one global correction byte, one reserved byte, then check. `(raw-128)*0.375°`; `0x80` is neutral. Positive stored corrections are clamped to neutral on ECU load. | Offline per-cell/global edit; preserve all other payload bytes |
| `052` | 4 | Yes | Signed little-endian Q8.8 filtered load-model correction; zero is neutral. The ID41-DAMOS-derived projection is `raw*5.46850393700787/256 mg/stroke`, with normal producer range `-32768..32512`. The filter's temporary `0x8000` bias is not the stored encoding. | Advanced engineering-unit edit |
| `056` | 6 | Yes | Little-endian Q8.8 learned VANOS reference (`u16*0.375/256` crankshaft degrees), learned-state byte at +2, reserved byte, then check. | Advanced angle and Default/Learned edits |
| `05C` | 4 | Yes | Little-endian Q8.8 closed-throttle baseline (`u16*0.46862745098039/256` throttle degrees), not measured live throttle opening. | Advanced angle edit |
| `060` | 8 | Yes | Five relative ignition/dwell-control gains plus one reserved byte. `raw/128` is the multiplier (`0x80 = 1×`), not a crankshaft sensor-wheel correction. Stored positions 1–5 refer to reference index 0, not proven physical cylinder labels. | Advanced multiplier edits |
| `068` | 40 | Yes | Centered CO-alignment state, IdleFT1, LTFT1, IdleFT2, LTFT2, then retained per-bank lambda-monitor averages/state. Idle trim is `(u16-32768)*0.00534 ms`; LTFT is `(u16-32768)*100/65535 %`. | Four trims remain direct edits; CO and raw monitor state use Allow advanced edits |
| `090` | 138 | Yes | Saved-slot count, ten internal DTC IDs, ten 12-byte occurrence/environment records, reserved bytes, and saved global state. Unused slots can be stale. | Grouped saved faults; advanced edits for admitted fields and named status bits |
| `11A` | 12 | Yes | Idle-air learned multiplier `byte/128`; signed correction words at +2/+4/+6 use `int16*100/65536 %`. They are drive-disengaged, drive-engaged, and stored A/C correction states, not fuel banks. The programmed idle-speed addition at +8 is one byte, `raw*1 RPM`; +9 is separate padding. | Advanced factor, signed corrections and programmed RPM addition |
| `126` | 32 | Yes | Rough-running/load-correction learner in firing order 1-5-3-6-2-4: counters, five signed corrections relative to cylinder 1, convergence countdown, valid flag and reserved byte. | Summary is structural; every stored value uses Allow advanced edits |
| `146` | 110 | Yes | DTC status words, ten fixed six-byte RPM/load fault envelopes, a saved freeze snapshot, a qualification counter, and a raw 6×6 cylinder relation matrix. These records are selected by internal fault ID, not occurrence-slot number. | Known envelope values/states and qualification count use advanced edits; unresolved flags/matrix semantics remain raw |
| `1B4` | 4 | Yes | Repeat-start coolant reference at +0 (`raw*0.75-48 °C`; `FF` unavailable), unresolved preserved payload byte at +1, then check. The next start subtracts a calibrated permitted drop before comparing current ECT. | Advanced temperature edit plus named **Set Not available (0xFF)** action; preserve unresolved byte |
| `1B8` | 10 | Yes | Seven wrapping monitor-completion counters: catalyst efficiency B1/B2, secondary air B1/B2, secondary-air valve sticking, tank vent/leak finalization, and aggregate misfire window; reserved byte; check. Not operating hours. | Advanced counters; preserve pad |
| `1C2` | 4 | No | Three-byte progression nonce plus pad, used for actuator/output-test anti-replay validation. | Advanced progression bytes; preserve pad |
| `1C6` | 4 | Yes | Persistent warm-up history counter at +0, unresolved preserved payload byte at +1, then check. Cold starts saturating-add ECT-indexed counts; qualified warm-ups saturating-subtract counts; the stock gate is set only above 90. | Advanced internal-count edit; preserve unresolved byte |
| `1CA` | 4 | Yes | Persisted transmission/coding word. Bits `0..1` select mode: `1` automatic, `2` manual. Preserve bits `2..15`. Used at boot only when the calibration selector's low six bits are `0x2C`. | Named transmission shortcut; masked RMW preserves other bits |
| `1CE` | 4 | Yes | Last-shutdown coolant byte (`raw*0.747-48 °C`) and consecutive warm-restart/no-cooldown count. | Advanced temperature and count |
| `1D2` | 4 | No | Peak engine speed (`raw*32 RPM`), saved stamp, and pad. | Advanced peak RPM; preserve other bytes |
| `1D6` | 4 | No | Qualified over-rev event count, saved stamp, and pad. Exact BMW event label is inferred. | Advanced count; preserve other bytes |

### Corrected adaptive-record locations by family

| Function | MS41.0 | MS41.1 | MS41.2/MS41.3 | Inspector description |
|---|---:|---:|---:|---|
| Filtered adaptive load-model correction/offset | `052..055` | `032..035` | `052..055` | `load_model_correction` |
| VVT/VANOS reference and adaptive-controller state | `056..05B` | `036..03B` | `056..05B` | `vanos_adaptation` |
| Relative ignition/dwell-control gains | `060..067` | `040..047` | `060..067` | `engine_roughness_segment_adaptation` (legacy internal key) |
| Idle-speed controller/actuator learned factor/integral accumulators | `0EA..0F5` | `0E8..0F3` | `11A..125` | `idle_regulator_adaptation` |

The VANOS and throttle Q8.8 formats, idle multiplier, and signed idle-air
corrections were cross-checked from MS41.0 DAMOS scaling through each family's
firmware load, filter/consumer, and save paths. MS41.2/.3 use identical checked
paths for these fields. This is **firmware-static evidence**, not bench or
on-car qualification. The A/C idle-air correction is already multiplied by a
calibration-dependent factor before saving; display the stored percentage,
not a reconstructed live value. These are stored states, not ordinary tune
parameters; advanced editing unlocks their known storage formats. Displayed
ranges are representable storage ranges, not universally safe operating limits.

The load-model state is the signed filtered difference between throttle-model
load and measured/filtered load. Positive values raise corrected load and
negative values lower it. The exact common storage domain is signed Q8.8 load
counts; the `5.46850393700787 mg/stroke` per whole count projection comes from
the MS41.0 ID41 DAMOS `lm_add_te_ll` scale and is homologous on the later
families. MS41.0 restores only the signed high byte at `053`; its low byte at
`052` is saved and checksummed but not restored, so the editor exposes the high
byte in `5.468503937 mg/stroke` steps and retains the low byte as separately
named advanced raw state. MS41.1 and MS41.2/.3 restore the complete word and
therefore retain `0.0213613435 mg/stroke` fractional steps.

The repeat-start coolant-reference record is at MS41.0 `180..183`, MS41.1
`1B6..1B9`, and MS41.2/.3 `1B4..1B7`. Only payload byte +0 is active. It is
captured while the repeat-start timer is active; `FF` is the unavailable,
expired, failed-check sentinel. At the next start, firmware computes
`max(saved ECT - calibrated drop, 0)` and compares current ECT with that
threshold. The byte is therefore a stored reference, not a status word or live
coolant. Payload byte +1 participates in the additive check but has no admitted
independent producer or consumer and remains advanced raw. The decoded editor's
named **Set Not available (0xFF)** action writes only byte +0 and the owning
record check; entering a temperature restores the ordinary quantized encoding.

MS41.1 `1C8..1CB` and MS41.2/.3 `1C6..1C9` similarly use only payload byte +0
for a persistent saturating warm-up-history counter. Cold-start ECT selects an
increment of 10, 6, 2, or 0 counts; a qualified warm-up selects a decrement of
0, 2, 7, or 20 counts. The stock gate is set only when the counter is above
90; equality clears it. This is not engine
load, elapsed time, or a physical unit. Payload byte +1 remains checked and
preserved raw. Canonical MS41.0 has no homologous record.

The programmed idle-speed addition occupies one byte: MS41.0 `0x0F2`,
MS41.1 `0x0F0`, and MS41.2/.3 `0x122`. Its nominal scale is **one RPM per
count**, not RPM/32. Firmware multiplies the command by a coolant-dependent
factor before adding it to the idle target; it is neither the complete idle
target nor a promise of the resulting engine speed. The diagnostic acceptance
ceiling is calibration-dependent. Exact load and commit paths copy only this
byte; the next byte is preserved, not an editable high byte. The legacy
`idle_speed_command_raw` field ID remains stable despite the corrected units.
This interpretation is firmware-static evidence across the canonical families.

### Fuel-record remainder

The leading word of every fuel record is stored CO alignment. `0x8000` is
neutral and the MS41.0 DAMOS representation is
`(raw-32768)*100/65536 %`; the other families use the homologous centered
state. OEM service commands read only `(high_byte-0x80)` and write integral
`0x100` steps. The offline editor retains the full word and places it behind
**Allow advanced edits**.

After the four named trims, MS41.0 has only its record check. MS41.1 stores two
per-bank upstream-O2 monitor retained indices at `052..055`. Their stock valid
range is 0 through 30 and `FFFF` means invalid/uninitialized; the exact BMW
short noun remains unresolved. Interleaved bank 1/bank 2 monitor bytes follow at
`056..065`. MS41.2/.3 store the corresponding interleaved monitor words at
`072..089` and threshold bytes at `08A..08D`.

BMW's E5/E6 name closes the first two values per bank as upstream-O2
**regulation-frequency metrics**. E7/E8 closes the next four as upstream-O2
**transition-time metrics**. Firmware divides accumulated monitor counts by a
calibration window, but does not close a Hz or ms conversion, so the editor
shows internal normalized counts. The final two bytes per bank are learned
upper and lower O2 switching-voltage thresholds. Their voltage-domain role is
closed, but an exact volts-per-count conversion is not; the editor therefore
shows ADC counts. All remain advanced-editable diagnostic history, not tune
parameters.

The older misfire interpretation is superseded: the exact E5/E6 and E7/E8
descriptors identify lambda/O2 monitoring; misfire and rough-running records
are separate. Editing any named value updates only its containing fuel-record
check and preserves every sibling byte.

### Rough-running correction learner

MS41.1 and MS41.2/.3 persist the same 30-byte payload plus check. Relative
offsets are `+0` u32 qualifying-event count, `+4` six u16 per-cylinder counts,
`+16` five signed correction words, `+26` unsigned convergence countdown,
`+28` learned/valid flag, and `+29` reserved byte. Exact firmware tables close
the slot order as firing order **1-5-3-6-2-4**. Cylinder 1 is the zero/reference,
so the five stored words are cylinders 5, 3, 6, 2 and 4 relative to cylinder 1.

Signedness and scale are established by the runtime multiplier path. The
engineering display is `raw*100/2^22 %` relative correction (one count is
approximately `0.0000238419%`); positive values reduce the corrected base and
negative values increase it. The runtime applies the equivalent two-stage
integer multiply/shift; diagnostic `raw>>8` is only a coarse export, not the
engineering conversion. The convergence field is initialized from a
calibration times 24 and driven toward zero; it is not a sixth correction or a
time value. The summary row only collapses the record; every stored child value
remains advanced-editable.

The signed load-model format and relative ignition-gain interpretation were
also closed through exact load/save and runtime consumer chains. Older
offset-binary load and crankshaft-segment labels are superseded. The latter's
legacy internal keys remain stable, but user-facing labels and units are
corrected. No physical cylinder mapping is inferred from array order.

### Knock learning is a 16×4 table

All four supported layouts persist one logical learned-knock table with 16
engine-speed rows and four load columns. Storage is row-major:
`cell = rpm_row*4 + load_column`. On load, the ECU copies each persisted cell
into the same position in all six live per-cylinder tables. On save, it
averages the six corresponding live cells back into the one persisted table.
The overall knock correction remains a separate scalar.

The RPM and load breakpoints live in calibration flash, not in the EEPROM.
The Android editor therefore identifies displayed numeric breakpoints as
canonical reference axes; a tuned ROM can change them. Exact axes should come
from the matching ROM and definition when those are available. The canonical
references are:

| Layout reference | RPM-axis SA | Load-axis SA | Shape |
|---|---:|---:|---:|
| MS41.0 `1429861` / ID41 | `236D` | `239A` | 16×4 |
| MS41.1 `1437806` / ID60 | `25D9` | `2606` | 16×4 |
| MS41.2 `1406464` / ID12 | `235B` | `2388` | 16×4 |
| MS41.3 `1406464` / SS1v2 | `235B` | `2388` | 16×4 |

### MS41.1 knock packing differs

MS41.1's record is `0x00E..0x031`: **32 packed bytes represent 64 logical
knock cells**, followed by the global correction at `0x02E`, a reserved byte
at `0x02F`, and the two-byte check at `0x030..0x031`.

This is **STATIC** in canonical `1437806`: loader `0x021A78` iterates `0x20`
stored bytes and expands their low/high nibbles into adjacent cells of six
`0x40`-byte live tables at `D840`; runtime `0x02E9C2..0x02E9CC` selects
`rpm_row*4 + load_column`; saver `0x022694` averages the six tables and packs
the two adjacent cells back into each byte before the `0x02277E` record write.

Each nibble `n` expands to RAM value `128-2*n`, hence a stored correction of
`-0.75*n` degrees. The even cell is the low nibble; the odd cell is the high
nibble. Packed `00` is neutral for both cells, while synthetic example `A3`
means `-2.25°` and `-7.5°`. The range is `-11.25..0°` in `0.75°` steps.
An offline cell edit changes only that nibble and its record check; its paired
cell, global correction, reserved byte, and unrelated records remain intact.

MS41.0/.2/.3 instead store 64 ordinary offset-binary bytes. Their neutral byte
is `80`, and their scale is `(raw-128)*0.375°`. The global correction uses
this ordinary byte format on every family. Values above neutral are clamped
to zero correction when loaded. These persisted arrays are averaged learned
corrections, not six independent per-cylinder maps. The editor intentionally
labels its built-in breakpoint values as canonical references because the
standalone EEPROM cannot prove the axes in a tuned ROM.

### Operating time and saved-fault records

Bytes `000..005` are one redundant 16-bit operating-time counter, not three
independent cycle parameters. Let the physical words be `a,b,c`. Stock firmware
votes `a` when `b == a+1` or `c == a+2`; otherwise it votes `b-1` when
`c == b+1`; if neither pair survives, it falls back to zero. All arithmetic is
modulo 65536. Its nominal diagnostic scale is 0.1 hour per tick. The value wraps,
so it is not a lifetime odometer. The decoded editor shows the voted value and
whether all three copies agree. Editing it writes `n,n+1,n+2` atomically; an
equivalent quantized input is a true no-op and does not silently normalize a
damaged sequence.

The saved-fault occurrence record is family-specific:

| Family | Checked record | Count / ten IDs | Slot base and stride | Saved global word / check |
|---|---:|---:|---:|---:|
| MS41.0 | `074..0E9` | `074` / `075..07E` | `07F + 10*i`, 10 bytes | `0E6..0E7` / `0E8..0E9` |
| MS41.1 | `068..0E7` | `068` / `069..072` | `073 + 11*i`, 11 bytes | `0E4..0E5` / `0E6..0E7` |
| MS41.2/.3 | `090..119` | `090` / `091..09A` | `09B + 12*i`, 12 bytes | `116..117` / `118..119` |

Only `min(count,10)` slots are displayed. Internal ID zero is valid; slot order
and duplicates are preserved. Each family has its own exact internal-ID to
public-code table, and MS41.1 persists eleven bytes from a twelve-byte runtime
slot. Out-of-range IDs, counts above ten, and failed record checks are warned,
but their physical bytes are never rewritten merely by inspection. A failed
check makes the values stale archival bytes because the stock loader rejects
that record; no saved slot is presented as a current live fault.

Within a slot, byte 0 is status at save, byte 1 is a saturating fault-debounce
accumulator in caller-specific internal counts,
byte 2 is occurrence frequency, byte 3 is the aging/logistics counter, bytes
4..7 are saved environment bytes, and bytes 8..9 are a big-endian operating-time
counter snapshot in 0.1-hour ticks. On MS41.1 and MS41.2/.3, byte 10 is a
secondary fault-management state: bits 0..1 are progression stage 0..3, `0C`
is the terminal-stage latch pair, `10` marks a handled transition, `20` marks
delay elapsed, and `40` marks delay initialized. MS41.2/.3 byte 11 is the
secondary delay countdown in unscaled internal counts; MS41.1 does not persist
that runtime countdown. Unsupported sibling bits remain raw. Public fault 100
also gives bytes 6..7 a confirmed little-endian self-test reason-word
interpretation; individual reason bits remain unresolved.

For the six ignition-coil fault descriptors, bytes 6..7 are one big-endian
per-cylinder timer word, not two independent environment bytes. MS41.0 stores
the ignition-feedback spark-burn duration at `raw*0.00534004716564 ms`;
MS41.1 has the same timer mode, producer, consumer, and homologous scale.
MS41.2/.3 repurpose the pair as a code-proven rough-running/smoothness metric,
but its physical engineering scale remains unresolved, so it is shown as one
raw 16-bit count.

Named saved-status bits are battery short (`01`), ground short (`02`), open
circuit (`04`), plausibility/out-of-range (`08`), emissions relevant (`10`),
stored after debounce (`20`), present at save (`40`), and sporadic (`80`). The
three lower circuit
qualifiers are exposed only for public codes that have an admitted normal BMW
status row. `40` means present **when saved**, not present now, and `80` means
sporadic, not simply “historical.” Named bit edits are masked read/modify/write
operations and preserve all sibling bits.

Environment values receive units only when the exact firmware descriptor points
to an independently identified source. The closed set now includes engine speed,
compressed air mass, filtered load, processed throttle angle, throttle/MAF/IAT
signal voltage, idle-actuator duty, front and rear oxygen-sensor voltage/heater
duty, banked short-term fuel trim, vehicle speed, battery voltage, coolant and
intake-air temperature, coolant-sensor voltage, purge duty, tank-pressure sensor
voltage, tracked front-oxygen-sensor envelope, knock gain/noise, measured VANOS
position, a raw transmission gear/status nibble, and startup-latched coolant and
intake-air temperatures. MS41.1 also closes the rear-oxygen-sensor setpoint-error
pair; the same descriptor positions remain raw on MS41.2/.3 because their exact
producers differ. The engine operating-state and gear/status bytes are
source-identified but remain raw because their individual states are not closed.
Every other environment byte remains raw. This source whitelist deliberately
does not reuse BMW per-code labels: known descriptor/label mismatches would
otherwise give real bytes the wrong physical meaning. After the combined-word
rules, MS41.0 has no unresolved environment positions; MS41.1 retains 20 and
MS41.2/.3 retain 22 code-owned positions without a safe physical conversion.
The saved operating counter is shown as the counter at save; without a trusted
current counter the editor does not invent an “hours ago” value.

Public auxiliary-air faults 245 and 246 store four separate snapshots: startup
coolant temperature, startup intake-air temperature, engine speed, and battery
voltage. On MS41.2/.3 the live RAM address used for the RPM byte is also named
`T_NB_DTE` by a diagnostic program at another point in its lifetime. The terminal
fault path overwrites that address with RPM immediately before saving the fault,
so interpreting archived bytes 6..7 as a little-endian timer was incorrect.

The admitted saved-byte scales are: RPM `raw*32`, air mass `raw*4 kg/h`, load
`raw*5.4471 mg/stroke`, throttle angle `raw*0.4686°`, throttle-signal voltage
`raw*0.01952 V`, other admitted sensor voltages `raw*0.0196 V`, idle-actuator
duty `raw*0.3906%`, oxygen-heater duty `raw*0.391%`, STFT
`raw*0.3906-50%`, speed `raw km/h`, battery `raw*0.1020 V`, and temperature
`raw*0.7471-48°C`. Newly closed scales are front-oxygen envelope and tank-pressure
signal `raw*5/256 V`, knock gain `raw*0.00392`, knock/noise `raw*0.02 V`,
MS41.0 VANOS `raw*0.375 crank degrees`, later-family VANOS `raw*0.3745 crank
degrees`, purge command `raw*0.391%`, and MS41.1 rear-oxygen setpoint error
`(raw-128)*0.0390625 V`.

### Dedicated fault-management envelopes and counters

MS41.2 stores ten fixed six-byte records at `152 + 6*i`. Their lookup order is
internal IDs `44,45,46,47,48,49,57,58,0D,0E`: cylinder misfire in firing order
1-5-3-6-2-4, mixture deviation bank 1/2, then post-catalyst lambda regulation
bank 1/2. These are shared group-retention records, not one record per saved
occurrence. The editor nests a matching record under the first saved fault card;
duplicate saved IDs show a reference to that same record, and unmatched records
remain separate collapsible fault cards.

Bytes +0/+2 are the observed minimum/maximum RPM buckets (`raw*32 RPM`), +1/+3
are minimum/maximum filtered load (`raw*5.4470588235 mg/stroke`), +4 is state,
and +5 is a recovery/delay countdown in internal counts. State bits `01` and
`02` are exposed together as one masked neutral progression control; `02`
selects delayed secondary progression. Bits `10/20` select mutually exclusive
internal source/mode paths, but their human names and all remaining bits are
unresolved. The countdown is seeded with `50` hex and decremented toward zero;
it has no seconds, cycle, or distance conversion. Named edits preserve every
unresolved state bit and update only the containing `146..1B3` record check.

MS41.1 must not inherit that physical map. Its canonical program reconstructs
22 live six-byte management records from lossy six-bit packed parallel arrays
inside `114..1B5`. Groups A/B/C each hold IDs `44..49`, while group D holds
`57,58,0D,0E`. A is produced from the 600-count short-window severe-misfire
source and reaches the severe-misfire output-mask path. B and C share the
3000-count long-window source: B is retained before an internal phase switch;
C is retained after that switch and an additional qualification threshold.
Those runtime distinctions are static; interpreting A as catalyst-protection
and B/C as emissions-relevant remains inferred rather than a recovered supplier
name. Group D belongs to mixture-deviation and post-catalyst lambda-regulation
paths. For each record, source bytes `p0/p1/p2` decode to
`q0=p0>>2`, `q1=((p0&3)<<4)|(p1>>4)`, `q2=((p1&15)<<2)|(p2>>6)`, and
`q3=p2&63`; the restored envelope bytes are `(q<<2)|2`. Consequently their
two discarded low bits cannot be recovered. RPM uses `raw*32`, load uses
`raw*5.4470588235`, and an edit quantizes to the real four-raw-count storage
step. The arrays are sparse rather than contiguous, so the editor performs a
masked repack at the exact source locations, preserves every neighboring `q`
value, and updates only the enclosing `114..1B5` record check. State bits `03`
are exposed as neutral stages, `0C` as the terminal latch pair, and `40` as the
record-valid bit; other flag bits remain raw. The countdown remains in internal
counts. MS41.3 uses the MS41.2 layout homologously; this is not a claim that
every custom derivative has been independently traced.

The ancillary byte at MS41.1 `1B2` or MS41.2/.3 `1AB` is a saturating
fault-history/DTC-mask qualification counter in internal counts, not drive
cycles. MS41.1 `122..127` and MS41.2/.3 `1AC..1B1` are six retained raw rows in
order 1-5-3-6-2-4; the low six bit positions use the same order. Stock code
restores and saves the bytes, clears one cylinder's row and column, globally
clears all rows, and ORs the aggregate. Exhaustive live-range tracing found no
nonzero/set writer and no per-cell consumer. Therefore `0` means only
clear/unset and `1` means only a retained raw bit with unresolved provenance;
neither correlation nor co-occurrence is claimed. The decoder shows all 36
independent cells, including the diagonal, without forcing symmetry, and shows
reserved high bits 6-7 separately.

The checked counter record contains seven bytes, one reserved byte, and its
two-byte additive check. Both MS41.1 and MS41.2/.3 assign them one-for-one to
catalyst-efficiency completions bank 1/2, secondary-air-system completions bank
1/2, secondary-air-valve mechanical-sticking evaluations, tank-vent/leak
diagnostic finalizations, and six-cylinder misfire evaluation windows. Four
MS41.1 increment sites live in raw code islands omitted by the linear Ghidra
export; direct canonical-image scanning and control-flow comparison recover the
same seven roles without projecting later-family labels. Active increments are
ordinary byte adds, so `255` wraps to `0`; DS2 service `89/1` clears all seven.

MS41.1 and MS41.2/.3 also retain a later freeze snapshot; MS41.0 does not have
this corresponding structure. Directly admitted values are:

| Value | MS41.1 | MS41.2/.3 | Storage |
|---|---:|---:|---|
| Engine speed | `1AA..1AB` | `18E..18F` | LE16, 1 RPM/count |
| Engine load | `1AC` | `190` | u8 high-byte snapshot, `raw*5.4471 mg/stroke` |
| Coolant temperature | `1AE` | `191` | u8 ECT snapshot, `raw*0.747-48 °C` |
| Vehicle speed | `1AD` | `196` | u8, 1 km/h/count |
| STFT 1 / STFT 2 | `196..199` | `192..195` | two LE16 words, `(raw-32768)*100/65535 %` |
| LTFT 1 / LTFT 2 | `1A2..1A5` | `1A0..1A3` | same percent storage |
| Last stored lambda-integrator step (PP1), bank 1 / bank 2 | `19A..19D` | `198..19B` | two unsigned LE16 step magnitudes in raw STFT counts |
| Lambda-controller state 1 / 2 | `19E..1A1` | `19C..19F` | two LE16 words; bit 3 of each low byte means regulation active |
| PT2 lambda-controller state, bank 1 / bank 2 | `1A6..1A9` | `1A4..1A7` | two LE16 words; bit 1 participates in an unresolved archived diagnostic classification |
| Associated internal ID | `1AF` | `1A8` | u8; `FF` is no particular fault |
| State / flags | `1B0..1B1` | `1A9..1AA` | state low 3 bits; flags `10` availability and `20/40` replacement latches |

The decoded values are available only when both checked records pass, at least
one occurrence slot is stored, the availability bit is set, and the associated
ID is a saved recognized ID or `FF`. Otherwise numeric values display
**Unavailable**, never plausible zeros, while the raw archival payload remains
visible. Their exact storage formats can still be advanced-edited deliberately;
such an edit changes only those bytes and the containing snapshot-record check.
It does not set the availability bit, change the associated ID, repair the
separate occurrence record, or manufacture a newly captured live snapshot.

The cross-family state producer/consumer trace admits three low-three-bit
states. Reset/no snapshot is `0`. A local DME fault-associated capture writes
`1`. An ID `FF` capture of an external drivetrain-CAN transition writes `2`;
restoring it also restores companion runtime state `20`. The exact CAN event is
unresolved and is not called an EGS fault, shift, or emissions event. Values
`3..7` are not emitted by the traced stock producers and retain a non-stock
label. Flag bits `20/40` form a behavioral retention tier: `00` ordinary can be
replaced by protected or locked, `20` protected can be replaced only by locked,
and `40` locked blocks later captures. Stock producers make `60` mutually
exclusive; an image containing it is inconsistent but effectively locked.
These are not BMW severity or priority labels. Stock save persists only mask
`70`, so flag bits `01..08` and `80`, plus upper state bits, remain raw. The
editor warns without blocking when availability and state disagree, non-stock
flag bits are present, both tier bits are set, or a tier exists while
availability is clear. Current reference captures contain only state/flags
`00/00`; the nonzero meanings above are firmware-static evidence, not
capture-frequency or on-car validation.

The saved-load producer copies the high byte of the live `FC52` load word. The
display intentionally follows BMW's saved-environment endpoint-normalized
contract (`5.4471 mg/stroke` per archived byte), rather than reusing the
learned load-model correction scale. The adjacent coolant byte is the direct
result of the canonical ECT linearization path: MS41.1 calls the `06F2` ECT
curve and saves the result to `1AE`, while MS41.2/.3 calls the `06CC` ECT curve
and saves the result to `191`. Its admitted display conversion is the same
`raw*0.747-48 °C` diagnostic ECT contract. PP1 word ownership and byte order are
closed by the exact snapshot producers and controller writers: it is the last
slew/ramp step magnitude stored by a lambda-integrator branch, in raw STFT
counts. Some branches apply a newly computed step without refreshing this slot,
so it is not current STFT and not necessarily the last applied step. PT2 remains
a controller-state word. Its bit 1 is proven to participate in the archived
lambda-state diagnostic classification: the derived numeric code is 2 when
saved lambda-state bit 3 is set, otherwise 8 when PT2 bit 1 is set, otherwise
1; unavailable bank 2 produces 0. The human meanings of codes 1, 2, and 8 are
unresolved, so no friendly PT2 state label is invented. The
complete PT2 and lambda-controller words remain advanced-editable; only bit 3
of each saved lambda word receives a named masked edit because it is the sole
cross-family bit closed as regulation active.

These field roles, family layouts and scales are firmware-static evidence from
the canonical programs and admitted diagnostic definitions. They are not bench
or on-car validation of arbitrary edited adaptation or fault-history values.

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

Android additionally provides explicit **Record checks → Repair selected**.
The user selects known checked records and confirms the warning before any
check-only repair. Only those two-byte checks change; payloads, unrelated
records and tail bytes are preserved. Repair is one undoable draft operation,
not proof that the accepted payload is sensible or safe.

## Tail map (`0x1DD..0x1FF`)

The common boot/identity routines own this tail. A padded RAM mirror does not
establish any of its values; possible synthetic tail values are withheld from
the decoded view.

| Range | Meaning |
|---:|---|
| `1DD..1DF` | Triplicate boot/flash progression state. `00 01 02` votes to normal state `E740=0`; `03 04 05` votes to normal state `E740=3`; seeded recovery `01 02 03` votes to the stock flash listener at `E740=1`. |
| `1E0` | Fourth byte written with the boot-state sequence; exact role unresolved. |
| `1E1..1E2` | Unknown; no dedicated normal writer found. |
| `1E3..1EE` | Twelve-byte ASCII BMW DATEN program reference (`ZL_Referenz`), not VIN, ISN, or feature bits. |
| `1EF..1F5` | Seven-byte BMW DATEN `HW-NR` program-reference mirror, not a ZB/ZUSB assembly number. |
| `1F6..1FC` | Second copy of the same seven-byte program reference. |
| `1FD..1FE` | Unknown; no dedicated normal writer found. |
| `1FF` | No proven normal parameter meaning. |

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

An EEPROM dump taken inside an agent session that entered with `E740=1` sees the
temporary `01 02 03` physical progression. Successful exit finalizes marker zero
after that dump. Starting another agent read re-enters state 1, so it is not an
independent inspection of the post-exit tail; use confirmed normal DS2 for the
exit state or an isolated, unpowered CH341A read for external physical proof.

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

### Android full-screen EEPROM Editor

**Open EEPROM Editor** in Library and the Service EEPROM action open the same
full-screen offline editor. Recognized EEPROM files are excluded from the
calibration ECU Editor and table-import donors; generic ROM definition support
is unchanged. Unknown imported EEPROMs require an explicit MS41 family layout.

- **Decoded** and **Hex** show the same 512-byte working draft. Decoded groups
  coding, fuel trims, knock, adaptations, history, saved faults, diagnostics,
  identification, and unknown data; field information exposes units, raw bytes, offsets,
  evidence confidence, and record-check status. Search accepts a label, serialized
  field ID, decimal offset, or hexadecimal offset; **Edited**, **Invalid check**, and
  **Advanced** filters work across every category.
- The tablet category rail collapses; narrow screens use a compact category
  selector. Fixed collapsible edit controls and the existing numeric keypad
  avoid covering the data with a draggable sheet. Rotation retains the draft,
  view, selection, and undo/redo history.
- Transmission, the four fuel trims, and learned knock cells/global correction
  remain directly editable. **⋮ → Allow advanced edits** unlocks other known
  numeric formats, CO alignment, retained raw fuel/lambda-monitor state,
  rough-running counters/corrections, named states, exact-length ASCII program references, and
  recognized boot progressions after a warning. Saved fault slots are grouped
  by physical slot; their admitted counts, sensor values, time snapshots and
  masked status bits use the same advanced gate. Unavailable freeze-snapshot
  values remain visibly unavailable rather than becoming fake zeros. The unlock survives rotation
  for the current file/layout; changing layout or opening another image resets it.
  Unresolved units remain labeled raw, and opaque blocks retain expert Hex access.
- **Save copy** first opens **Review Changes**, separating named before/after
  values, automatic record-check updates, and raw-only byte ranges. Every row
  jumps back to its decoded field or exact Hex byte. Confirming creates a
  separate Library image; editing never replaces the source, creates a file per
  keystroke, or writes to an ECU. Leaving an unsaved draft routes its save option
  through the same review.
- Numeric input is range-checked and quantized to the real storage step.
  Only changed checked-record payloads trigger check updates; a no-op leaves
  even an invalid record unchanged. Editing an invalid payload can make the
  remaining bytes in that record acceptable to the ECU, so warnings remain
  visible. **⋮ → Record checks** shows stored/expected checks and allows explicit
  selected-record repair after confirmation; it shares the same undo/redo history.
  No automatic blanket repair of unrelated invalid records is performed.
- **Compare** is read-only and accepts Library images, an external file, or a
  completed ECU read only when both inputs are exactly 512 bytes. Compatible
  layouts show named values, record-check validity changes, and unresolved raw
  ranges with filename, SHA-256, family, and provenance for both sides. Unknown
  or incompatible family layouts still receive a complete raw-byte comparison,
  but named decoding fails closed with an explicit warning. Comparison never
  edits, repairs, saves, or writes either input.

Physical EEPROM writes remain separate, explicitly authorized operations
with their existing before-image backup, write admission, and readback rules.

## Commands

Offline inspection is safe and performs no serial I/O:

```powershell
python -m engines.softbsl.eeprom_ram inspect `
  "path\to\eeprom.bin"
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
  images. Generic writes, exact replay, stale compares, I2C failures, and both
  conditional finalizer commands are exact-byte tested. This does not establish
  UART timing, electrical, bench, or on-car proof.
- OEM labels for the `00A` key, one `11A` correction byte, and especially the
  `1D6` event remain inferred from behavior.
