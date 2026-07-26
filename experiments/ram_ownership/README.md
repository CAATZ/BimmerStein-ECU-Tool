# MS41.3 RAM ownership investigation

This directory is isolated research. It does not modify or supersede the current
RAM-map documentation, patch descriptors, patch engine, or flasher behavior.

`analyze.py` conservatively combines:

- direct RAM/SFR/CAN operands from the complete low and program-high assembly listings;
- overlapping instruction streams recovered from disposable/read-only Ghidra imports;
- a conservative high-segment reachability gate rooted from every possible
  even-aligned lower-flash `CALLS`/`JMPS` pattern and all decoded JMPI tables;
- register-indirect logical addresses resolved inside straight-line basic blocks;
- unresolved indirect-access evidence;
- exact non-contiguous function bodies exported read-only from Ghidra;
- SHINDE1 logger addresses; and
- stack, context-bank, DPP3, and PEC ownership visible in the firmware, C166 manual,
  and exact SAB80C166W datasheet;
- the documented hardware-BSL register bank, stack, and 32-byte first stage, plus
  the current Soft-BSL register bank, inherited system stack, and CRC table;
- retained-source limits behind the historical BSL/ESFR claim and canonical `r0`
  software-stack traffic, reserving its complete legal `0xFA00-0xFA45` arena;
- ranked ordinary static gaps split at transient-owner boundaries; and
- trusted emulator tests plus isolated post-boot ADC/PEC/SSC/pin ISR body slices.

It writes deterministic evidence under `evidence/`. Absence of a reference is
not enough by itself: a range is certified only when the exact-image
reachability, bounded-pointer, DPP3, PEC, stack, logger, runtime, and lifetime
gates all close. The current report conditionally certifies three XRAM ranges
for use only after the stock startup handoff: `0xD800-0xD83F`,
`0xDB8F-0xDC1F`, and `0xE847-0xE85F`. It also classifies every byte of the
1,024-byte IRAM and certifies `0xFC3F-0xFC41` plus `0xFD80-0xFDDB` after
startup: 95 bytes total, of which 94 are word-aligned. `0xFD7C-0xFD7F` is
explicitly excluded because the current Soft-BSL CRC table owns it.
Unsupported emulator instructions are recorded as coverage gaps; no opcode
behavior is guessed.

```powershell
python experiments\ram_ownership\analyze.py --self-test
python experiments\ram_ownership\trace_runtime.py
python experiments\ram_ownership\analyze.py
python experiments\ram_ownership\trace_runtime.py --check
python experiments\ram_ownership\analyze.py --check
```

Refresh the function-body evidence before the static pass when the Ghidra
project changes:

```powershell
& "C:\Users\crist\Downloads\ghidra_12.1.2_PUBLIC_20260605\ghidra_12.1.2_PUBLIC\support\analyzeHeadless.bat" `
  "C:\Users\crist\MS41 Projects\Decompilation\proj" "ms41_3" `
  -process "MS41.3_s52_stock_fullread.bin" -noanalysis -readOnly `
  -scriptPath "C:\Users\crist\MS41 Projects\Flasher-Dev\experiments\ram_ownership\ghidra" `
  -postScript "DumpFunctionBodies.java" `
  "C:\Users\crist\MS41 Projects\Flasher-Dev\experiments\ram_ownership\evidence\ghidra_function_bodies.tsv"
```

The independent emulator/oracle boot gate is:

```powershell
Push-Location "C:\Users\crist\ECU Emulator"
python -m pytest -q tests\boot\test_boot_ram_footprint.py tests\test_ds2.py tests\test_flash.py tests\test_fun_024670.py
Pop-Location
```

The two ISR opcode additions are pinned separately by Ghidra-oracle fixtures:

```powershell
Push-Location "C:\Users\crist\ECU Emulator"
python -m pytest -q tests\isa\test_bits.py::test_bmov_direct_bits_match_oracle_window tests\isa\test_moves.py::test_movb_indirect_from_long_memory_matches_oracle_window
python -m pytest -q tests\test_diff.py -k "ram_ownership_bmov or ram_ownership_movb"
Pop-Location
```

Hardware sampling, canary writes, current-documentation edits, and production
RAM allocation are intentionally outside this investigation phase.
