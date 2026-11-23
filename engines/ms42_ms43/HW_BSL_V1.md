# MS42/MS43 hardware BSL loader, revision 1

This separate entry loads the existing bulk agent without executing installed
ECU firmware. Existing `agent.asm`, `payload.py` and their manifest are retained
unchanged. The host must explicitly select the board family and admit the flash
geometry; the loader's family banner identifies its own compiled variant.

The source is independently authored. No third-party executable or downloaded
agent bytes are included. The C167CR manual, chapter 14, defines the silicon
handoff: 32 bytes at FA40, CP=FA00, SP/STKUN=FA40, STKOV=FA0C, reset DPPs,
disabled watchdog, and ASC0 8N1 with the divisor established by autobaud.
The functional emulator starts at this documented boundary; it does not emulate
the silicon BSL ROM, electrical boot strap, or autobaud measurement.

## Wire contract

1. At 38,400/8N1, the host sends `00` and requires C167 identification `C5`.
   A K-line adapter may also return the host's `00` echo. No sync replay is safe
   after the ROM may have entered its 32-byte receive state.
2. Send the exact 32-byte `stage0()` followed by the selected 1,024-byte
   `stage1(family, baud)`. Stage0 receives stage1 at F600..F9FF.
3. Stage1 establishes its CP/DPP/stack and checks CRC16/A001/FFFF over its first
   1,022 bytes against its two-byte little-endian trailer. A mismatch emits `5A`
   and stops before external-bus setup or flash commands. As with any tiny ROM
   loader, its first executable verifier is a trusted bootstrap boundary.
4. Valid stage1 configures the board bus and timer, performs flash reset,
   autoselect, ID reads, then reset to array mode. It returns eleven bytes:
   `HB42 01` or `HB43 01` (five ASCII/binary bytes), manufacturer16LE,
   device16LE, and CRC16BE over the preceding nine bytes.
5. Send the existing family bulk agent followed by CRC16BE. The loader requires
   both the received CRC and its compiled expected length/CRC to match. It
   returns `K` at 38,400. No arbitrary address or code length is accepted.
6. Send `G` at 38,400, consume the adapter echo, then change the host to the
   selected rate. Stage1 waits 50 T1 ticks before changing ASC0 and entering
   E000. The unchanged bulk verifier returns `A5`, then its CRC packet protocol
   is available. Invalid input emits `5A` and stops; stage1 UART/TX waits are bounded.

Supported rates are 125,000 for both families, 166,667 for MS42 and 187,500 for
MS43. Initial autobaud is retained until the explicit `K`/`G` transition. The
loader never resets into potentially damaged firmware on a failed transfer.

## Initialization and evidence boundaries

MS42 bus/SRAM settings come from 0110C6 resident startup 008044..00808C:
SYSCON=E206, BUSCON0=44BF, ADDRSEL1=1007, BUSCON1=808E, ADDRSEL2=0001,
BUSCON2=040E. MS43 uses the current custom agent's DS2 flash alias settings
(ADDRSEL1=1008, BUSCON0=45BF, BUSCON1=818E) and resident 008108..00810C SRAM
settings (ADDRSEL2=3008, BUSCON2=052E). No SSC, output latch or actuator setup
is copied into this loader.

Native resident startup supplies the timer design: T6CON=80C0 and T01CON=4800,
with CAPREL=0F9F for MS42 or 176F for MS43. This loader also initializes T6,
T1 and T1REL explicitly. The inferred 16/24 MHz CPU profiles give the existing
agent's approximate millisecond timebase; these clocks and bus electrical
behavior still require physical qualification. CP=FC00, SP/STKUN=FC00 and
STKOV=FB00 match the unchanged bulk-agent entry contract.

`build_hw_bsl_v1.py` uses the supplied C167 assembler and emits the revisioned
module and manifest. `--verify` checks source/assembler hashes, all four
resolved variants, loader CRC trailers, and the unchanged bulk payload hashes:

```powershell
python -m engines.ms42_ms43.build_hw_bsl_v1 --verify
```

Native coverage lives in the canonical ECU Emulator's
`tests/test_c167_hw_bsl_v1.py` and `docs/MS42_MS43_EMULATION.md`. It checks blank
and corrupt flash, nonzero initial RAM, downloaded startup execution, CRC
rejections, rate selection, and boot-sector recovery using the existing C167
devices/AMD model. This is bounded native execution, not physical ECU recovery,
power-loss testing, or qualification of every board/flash revision.
