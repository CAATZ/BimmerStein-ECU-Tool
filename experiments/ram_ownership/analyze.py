#!/usr/bin/env python3
"""Conservative, isolated MS41.3 RAM-ownership evidence collector."""

from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import re
from collections import Counter
from pathlib import Path


PROJECTS = Path(__file__).resolve().parents[3]
REPO = Path(__file__).resolve().parents[2]
EMULATOR = PROJECTS.parent / "ECU Emulator"
DEFAULT_DECOMP = PROJECTS / "Decompilation" / "MS41.3"
DEFAULT_REFERENCE = (
    PROJECTS / "_shared" / "REF_MS41.3" / "MS41.3_s52_stock_fullread.bin"
)
ASM_FILES = ("d_first32k.asm", "d_ph.asm", "d_progtop.asm")
LOW_CODE_END = 0x6300
INPUT_FILES = ("logger_ram_shinde1.json", *ASM_FILES)
RECOVERED_ASM = (
    Path(__file__).parent / "evidence" / "ghidra_recovered_code.asm"
)
VERIFIED_OVERLAP_ASM = (
    Path(__file__).parent / "evidence" / "verified_overlap.asm"
)
CONSERVATIVE_ROOT_ASM = (
    Path(__file__).parent / "evidence" / "ghidra_conservative_roots.asm"
)
REACHABILITY_EDGE_ASM = (
    Path(__file__).parent / "evidence" / "ghidra_reachability_edges.asm"
)
LOWER_COMPUTED_TARGET_ASM = (
    Path(__file__).parent / "evidence" / "ghidra_lower_computed_targets.asm"
)
EXTRA_INPUTS = (REPO / "engines" / "softbsl" / "agent_build.asm",)
CPU_MANUAL = PROJECTS / "_shared" / "Datasheets" / "CPU - c166 - User Manual.pdf"
CPU_DATASHEET = (
    PROJECTS
    / "_shared"
    / "Datasheets"
    / "CPU - SAB80C166W-M-T3 - Datasheet.pdf"
)
BSL_GUIDE = (
    PROJECTS
    / "_shared"
    / "Datasheets"
    / "CPU - BSL - 80c166BSL - Guide.pdf"
)
RAM_MAP_DOC = DEFAULT_DECOMP / "ms41_ram_map.md"
GAL_REFERENCE = PROJECTS / "_shared" / "Hardware" / "GAL16V8_Reference.md"
HARDWARE_WORKBOOK = (
    PROJECTS / "_shared" / "Hardware" / "MS41_Hardware_Reference.xlsx"
)
BOOT_FOOTPRINT = (
    EMULATOR
    / "tests"
    / "boot"
    / "boot_ram_footprint_ms41_3.json"
)
RUNTIME_FOOTPRINT = Path(__file__).parent / "evidence" / "runtime_ram_footprint.json"
FUNCTION_BODIES = Path(__file__).parent / "evidence" / "ghidra_function_bodies.tsv"
EXTRA_INPUTS = (
    *EXTRA_INPUTS,
    Path(__file__).resolve(),
    Path(__file__).parent / "trace_runtime.py",
    Path(__file__).parent / "ghidra" / "DumpFunctionBodies.java",
    Path(__file__).parent / "ghidra" / "RecoverDisasm.java",
    FUNCTION_BODIES,
    RECOVERED_ASM,
    VERIFIED_OVERLAP_ASM,
    CONSERVATIVE_ROOT_ASM,
    REACHABILITY_EDGE_ASM,
    LOWER_COMPUTED_TARGET_ASM,
    CPU_MANUAL,
    CPU_DATASHEET,
    BSL_GUIDE,
    RAM_MAP_DOC,
    GAL_REFERENCE,
    HARDWARE_WORKBOOK,
    BOOT_FOOTPRINT,
    RUNTIME_FOOTPRINT,
    EMULATOR / "ms41emu" / "memory.py",
    EMULATOR / "ms41emu" / "isa" / "bits.py",
    EMULATOR / "ms41emu" / "isa" / "moves.py",
    EMULATOR / "tests" / "diff.py",
    EMULATOR / "tests" / "golden" / "ram_ownership_bmov_0x4a.json",
    EMULATOR / "tests" / "golden" / "ram_ownership_movb_indirect_mem_0xa4.json",
    Path(__file__).parent / "evidence" / "oracle_bmov_0x4a.json",
    Path(__file__).parent / "evidence" / "oracle_movb_indirect_mem_0xa4.json",
    *(EMULATOR / path for path in (
        "tests/boot/test_boot_ram_footprint.py",
        "tests/test_ds2.py",
        "tests/test_flash.py",
        "tests/test_fun_024670.py",
    )),
)

REGIONS = (
    ("unmapped", 0xC000, 0xD000),
    ("CAN controller", 0xD000, 0xD800),
    ("external SRAM", 0xD800, 0xF800),
    ("unmapped", 0xF800, 0xFA00),
    ("internal RAM", 0xFA00, 0xFE00),
    ("SFR", 0xFE00, 0x10000),
)
RAM_REGIONS = {"external SRAM", "internal RAM"}
PROVEN_NONEXECUTABLE_LOW_RANGES = (
    {
        "start": 0x004200,
        "end": 0x004230,
        "evidence": (
            "Literal/table bytes between the terminating vector JMPS at 0x0041FC "
            "and the copied flash-driver entry at 0x004230."
        ),
    },
)
ADDRESS_MAP_FINDINGS = (
    {
        "range": "0xF000-0xF1FF",
        "classification": "external SRAM on the exact SAB80C166W",
        "evidence": (
            "SAB80C166W datasheet PDF pp. 9 and 40: 1 KB on-chip RAM and one "
            "512-byte SFR area at physical 0xFE00-0xFFFF; no ESFR block. Stock "
            "firmware uses 0xF018/0xF0C4 as high-traffic object bases."
        ),
        "discrepancy": (
            "Existing non-investigation memory-map/emulator region labels call this "
            "an ESFR overlay; those files were intentionally not edited."
        ),
    },
)
BSL_EVIDENCE_AUDIT = {
    "claim_under_review": (
        "0xF000-0xF1FF is hidden by an ESFR overlay, reportedly observed by a "
        "2026-06-23 BSL write/readback sweep."
    ),
    "retained_sources": [
        str(RAM_MAP_DOC),
        str(GAL_REFERENCE),
        str(HARDWARE_WORKBOOK),
    ],
    "retained_evidence": [
        "Narrative conclusions and the BSL monitor setup recipe.",
        "GAL pinout/equations and an 8 KB multi-block SRAM /CE decode.",
    ],
    "missing_evidence": [
        "Per-address pre-write, write, and post-write readback results.",
        "A raw sweep log/capture and an alias/overlay comparison.",
    ],
    "result": "not independently hardware-verified from retained artifacts",
    "working_classification": (
        "The exact SAB80C166W datasheet, GAL SRAM decode, and dense native "
        "firmware use support external SRAM at 0xF000-0xF1FF."
    ),
    "safety_effect": (
        "None: 0xF000-0xF1FF remains rejected for patches because native "
        "firmware owns it densely."
    ),
}
CANDIDATES = (
    (
        "0xE000-0xE31F",
        0xE000,
        0xE320,
        "Also occupied by the Soft-BSL 0xE000-0xE3FF buffer while its agent owns the CPU.",
    ),
    (
        "0xE74E-0xE7FF",
        0xE74E,
        0xE800,
        "Native low-firmware routines access 0xE74E-0xE750 and 0xE75C-0xE75F.",
    ),
    (
        "0xF000-0xF1FF",
        0xF000,
        0xF200,
        "Exact-part external SRAM, but densely owned by native per-bank objects.",
    ),
    (
        "0xF200-0xF7FF",
        0xF200,
        0xF800,
        "Broad prior candidate; expected to contain native firmware state.",
    ),
)
FUN_02CD98_ENVELOPE = (0x02CD7E, 0x02D087)
SEGMENT2_JMPI_TABLES = (
    (0x020DB2, 0xA5B8, 5),
    (0x020E48, 0xA5C2, 5),
    (0x02381E, 0xABBC, 6),
    (0x024D0A, 0xA53C, 6),
    (0x025752, 0xA548, 24),
    (0x02791A, 0xA580, 28),
    (0x02CB9E, 0xAC2C, 11),
    (0x02D1FE, 0xAC42, 5),
    (0x02E66E, 0xACAA, 5),
)
SEGMENT3_JMPI_TABLES = (
    (0x0316A4, 0xAD0A, 6),
    (0x031B92, 0xAD16, 5),
    (0x035E16, 0xACB4, 7),
    (0x03606E, 0xACC2, 12),
    (0x03646A, 0xACDA, 9),
    (0x036900, 0xACEC, 8),
    (0x036D6E, 0xACFC, 7),
    (0x039270, 0xAF3E, 5),
    (0x03D9F2, 0xAD20, 8),
    (0x03EABA, 0xADE8, 6),
)
JMPI_TABLES = (*SEGMENT2_JMPI_TABLES, *SEGMENT3_JMPI_TABLES)
LOWER_COMPUTED_TABLES = (
    (0x0000EC, 0xAE1E, 129),
    (0x00030A, 0xAE12, 6),
    (0x000FF2, 0xAF60, 0x66),
    (0x00106E, 0xAF48, 6),
    (0x001130, 0xAF54, 6),
    (0x0015D6, 0xB092, 36),
    (0x007C54, 0xAD30, 18),
    (0x007F1A, 0xAD54, 31),
    (0x007FA0, 0xAD92, 16),
)
CONTROL_REGS = {
    0xFE10: "CP",
    0xFE12: "SP",
    0xFE14: "STKOV",
    0xFE16: "STKUN",
}
BOOT_RAM_TEST_WORDS = {
    0xA300: 0xFDFE,
    0xA302: 0xD800,
    0xA304: 0xDBFE,
    0xA306: 0xE420,
    0xA308: 0xF7FE,
    0xA30A: 0xDC00,
    0xA30C: 0xE41E,
    0xA30E: 0xD080,
    0xA310: 0xD0FF,
    0xA312: 0xF7F2,
    0xA314: 0xFA00,
}
LIFETIME_CLAIMS = (
    {
        "range": "0xD080-0xD0FF",
        "start": 0xD080,
        "end": 0xD100,
        "owner": "startup byte RAM self-test",
        "lifetime": "reset/startup before the main firmware handoff",
        "evidence": "d_first32k.asm 0x004642-0x0046BE; REF table 0xA30E/0xA310",
    },
    {
        "range": "0xD800-0xF7F3",
        "start": 0xD800,
        "end": 0xF7F4,
        "owner": "startup word RAM self-test",
        "lifetime": "reset/startup before the main firmware handoff",
        "evidence": "d_first32k.asm 0x0045BC-0x004640; REF table 0xA302-0xA312",
    },
    {
        "range": "0xFA00-0xFDFF",
        "start": 0xFA00,
        "end": 0xFE00,
        "owner": "startup internal-RAM self-test",
        "lifetime": "reset/startup before the main firmware handoff",
        "evidence": "d_first32k.asm 0x0044C8-0x0045A8; REF table 0xA300/0xA314",
    },
    {
        "range": "0xFA00-0xFA1F",
        "start": 0xFA00,
        "end": 0xFA20,
        "owner": "stock startup general-purpose register bank at CP=0xFA00",
        "lifetime": "startup internal-RAM test",
        "evidence": "d_first32k.asm 0x004534; exact table word 0xA314=0xFA00",
    },
    {
        "range": "0xFA16-0xFA35",
        "start": 0xFA16,
        "end": 0xFA36,
        "owner": "stock startup general-purpose register bank at CP=0xFA16",
        "lifetime": "startup internal-RAM test",
        "evidence": "d_first32k.asm 0x0044B0-0x0044C0; 0xA314+0x16",
    },
    {
        "range": "0xFA00-0xFA1F",
        "start": 0xFA00,
        "end": 0xFA20,
        "owner": "C166 hardware-BSL general-purpose register bank",
        "lifetime": "built-in hardware bootstrap loader",
        "evidence": "80C166 BSL guide PDF page 8: CP=0xFA00",
    },
    {
        "range": "0xFA20-0xFA3F",
        "start": 0xFA20,
        "end": 0xFA40,
        "owner": "C166 hardware-BSL system stack",
        "lifetime": "built-in hardware bootstrap loader",
        "evidence": "80C166 BSL guide PDF pages 8 and 17: SP=0xFA40 and map",
    },
    {
        "range": "0xFA40-0xFA5F",
        "start": 0xFA40,
        "end": 0xFA60,
        "owner": "C166 hardware-BSL first-stage code",
        "lifetime": "32-byte hardware-bootstrap receive and execution",
        "evidence": "SAB80C166W datasheet PDF page 21; 80C166 BSL guide PDF page 5",
    },
    {
        "range": "0xFA00-0xFA1F",
        "start": 0xFA00,
        "end": 0xFA20,
        "owner": "Soft-BSL agent general-purpose register bank",
        "lifetime": "current repository Soft-BSL agent owns the CPU",
        "evidence": "engines/softbsl/agent_build.asm line 60: CP=0xFA00",
    },
    {
        "range": "0xFB64-0xFBFF",
        "start": 0xFB64,
        "end": 0xFC00,
        "owner": "Soft-BSL agent system-stack envelope",
        "lifetime": "current repository Soft-BSL agent owns the CPU",
        "evidence": (
            "agent_build.asm line 59 sets SP=0xFC00 and inherits the stock "
            "STKOV=0xFB64/STKUN=0xFC00 bounds"
        ),
    },
    {
        "range": "0xFD60-0xFD7F",
        "start": 0xFD60,
        "end": 0xFD80,
        "owner": "Soft-BSL agent nibble CRC table",
        "lifetime": "current repository Soft-BSL agent owns the CPU",
        "evidence": "engines/softbsl/agent_build.asm line 36: NIBTBL=0xFD60",
    },
    {
        "range": "0xDC20-0xE31F",
        "start": 0xDC20,
        "end": 0xE320,
        "owner": "stock authenticated DS2 memory-download target",
        "lifetime": "diagnostic command 0x00 write/verify",
        "evidence": "d_ph.asm 0x02080E-0x02086E",
    },
    {
        "range": "0xE000-0xE3FF",
        "start": 0xE000,
        "end": 0xE400,
        "owner": "Soft-BSL chunk buffer",
        "lifetime": "Soft-BSL agent owns the CPU; interrupts are disabled",
        "evidence": "engines/softbsl/agent_build.asm BUF",
    },
    {
        "range": "0xE320-0xE41F",
        "start": 0xE320,
        "end": 0xE420,
        "owner": "stock RAM-resident flash driver copy",
        "lifetime": "stock erase/program preparation and RAM execution",
        "evidence": "d_first32k.asm 0x005082-0x005098 and 0x005202-0x005216",
    },
    {
        "range": "0xE420-0xE528",
        "start": 0xE420,
        "end": 0xE529,
        "owner": "stock diagnostic flash protocol state and scan buffer",
        "lifetime": "stock erase/program request handling",
        "evidence": "d_first32k.asm 0x004F16-0x005420; indexed fill at 0x0051AC-0x0051B0",
    },
)
PEC_RAM_CLAIMS = (
    {
        "range": "0xE520-0xE61E",
        "owner": "PEC2 variable-length byte source envelope",
        "lifetime": "PEC2 active; runtime count is 0xE521, source increments from 0xE520",
        "evidence": "d_ph.asm 0x02762C-0x027644",
    },
    {
        "range": "0xE621-0xE724",
        "owner": "PEC0 transfer-profile envelope",
        "lifetime": "PEC0 diagnostic profile active; includes runtime-count destination at 0xE626",
        "evidence": "d_ph.asm 0x0396DE-0x039764",
    },
    {
        "range": "0xF6DE-0xF6DF",
        "owner": "PEC0 two-byte incrementing destination",
        "lifetime": "PEC0 control 0x0302 active",
        "evidence": "d_ph.asm 0x039568-0x039578",
    },
    {
        "range": "0xF6E2-0xF6F6",
        "owner": "PEC0 fixed transfer profiles",
        "lifetime": "PEC0 controls 0x0302/0x0304/0x0504/0x0507 active",
        "evidence": "d_ph.asm 0x0395B4-0x03965E",
    },
    {
        "range": "0xF710-0xF725",
        "owner": "PEC0 fixed incrementing destinations",
        "lifetime": "PEC0 controls 0x0305/0x030C active",
        "evidence": "d_ph.asm 0x039674-0x0396CC",
    },
    {
        "range": "0xF727-0xF731",
        "owner": "PEC0 eleven-byte incrementing source",
        "lifetime": "PEC0 control 0x050B active",
        "evidence": "d_ph.asm 0x039704-0x039714",
    },
    {
        "range": "0xFA8A-0xFA8B",
        "owner": "PEC1 word destination",
        "lifetime": "PEC1 control 0x0001 active",
        "evidence": "d_ph.asm 0x038E7E-0x038E8E",
    },
    {
        "range": "0xFA94-0xFAA9",
        "owner": "ADC PEC7 sample buffer",
        "lifetime": "PEC7 active: 11 incrementing word transfers",
        "evidence": "d_ph.asm 0x02C6BC-0x02C6CC and 0x038A60-0x038A70",
    },
)

POINTER_BASE_INVESTIGATIONS = (
    {
        "function": "FUN_02cd98",
        "base_register": "r9",
        "candidate_bases": ["0xF018", "0xF0C4"],
        "candidate_access_envelopes": ["0xF053-0xF0BB", "0xF0FF-0xF167"],
        "evidence": (
            "d_ph.asm 0x02F1D4-0x02F1EA assigns only 0xF0C4 or 0xF018 "
            "through the direct 0xF596 pointer slot. After A14 normalization, "
            "CALLS 0x028D7E at file 0x02D096/0x02D0A4 enters file 0x02CD7E, "
            "whose wrapper loads r9 from 0xF596 at 0x02CD86. CALLS 0x02CD98 at "
            "file 0x029796/0x029A66 instead targets file 0x028D98."
        ),
        "status": "closed by the guarded 0xF596 pointer-slot fixed point",
        "blockers": [],
    },
    {
        "function": "FUN_02b0cc",
        "base_register": "r9",
        "candidate_bases": ["0xF018", "0xF0C4"],
        "candidate_access_envelopes": ["0xF01A-0xF06D", "0xF0C6-0xF119"],
        "evidence": (
            "The stock far-pointer table enters file 0x02F190, 0x02B03C, or "
            "0x02B356 (reference file offsets 0x6458, 0x6510, 0x6514). "
            "The r9 paths select 0xF018/0xF0C4 at file 0x02B054/0x02B05A or "
            "0x02F20A/0x02F220; file 0x02F252 passes that value through r12 "
            "to the normalized file entry at 0x02F0CC."
        ),
        "status": "stock value set resolved; native ownership envelope",
        "blockers": [],
    },
    {
        "function": "FUN_02b0cc",
        "base_register": "r4",
        "candidate_bases": ["r9 plus local constant"],
        "candidate_access_envelopes": ["0xF042-0xF16D"],
        "evidence": (
            "Thirteen sites derive r4 locally from the already-proven "
            "FUN_02b0cc r9 value set; file 0x02F3A8 is additionally gated by "
            "a comparison proving 0xEF60 equals r9 before adding 0x35. The "
            "r8-derived sites are reached only through calls passing r12=r9."
        ),
        "status": "closed by the current entry gates and proven pointer rules",
        "blockers": [],
    },
    {
        "function": "FUN_02b0cc",
        "base_register": "r8",
        "candidate_bases": ["0xF018", "0xF0C4"],
        "candidate_access_envelopes": ["0xF01E-0xF0F9"],
        "evidence": (
            "All three stock entry envelopes are direct-call-only, every call "
            "is immediately preceded by mov r12,r9, and each entry copies "
            "r12 to r8."
        ),
        "status": "all fourteen r8 write sites resolved",
        "blockers": [],
    },
    {
        "function": "FUN_020986",
        "base_register": "r5",
        "candidate_bases": ["0x0000-0x00FF"],
        "candidate_access_envelopes": ["0xE865-0xE964"],
        "evidence": (
            "Each of the ten writes at file 0x024B14-0x024CCE immediately "
            "loads r5 with MOVBZ from byte 0xFAC2, proving r5 is 0x0000-0x00FF "
            "regardless of the byte's producer."
        ),
        "status": "full byte-derived value set resolved; native ring-buffer envelope",
        "blockers": [],
    },
    {
        "function": "FUN_0044e6",
        "base_register": "r5",
        "candidate_bases": ["0x0000-0x00FF", "0xE650 pointer slot at file 0x005630"],
        "candidate_access_envelopes": ["0xE523-0xE624", "0xE747-0xE846"],
        "evidence": (
            "Six startup-helper writes at file 0x0049CC-0x004D42 derive r5 "
            "through MOVBZ from a byte. The seventh, file 0x005630, loads r5 "
            "from the mutable word slot 0xE650."
        ),
        "status": "closed by the guarded 0xE650 pointer-slot fixed point",
        "blockers": [],
    },
    {
        "function": "FUN_0044e6",
        "base_register": "r2",
        "candidate_bases": [
            "0xD080-0xD0FF",
            "0xD800-0xF7F2",
            "0xFA00-0xFDFE",
        ],
        "candidate_access_envelopes": [
            "0xD080-0xD0FF",
            "0xD800-0xF7F3",
            "0xFA00-0xFDFF",
        ],
        "evidence": (
            "The exact stock image's immutable table at logical 0xA300-0xA314 "
            "supplies every startup RAM-test boundary used at file "
            "0x0044C8-0x0046BE."
        ),
        "status": "18 startup RAM-test pointer sites resolved",
        "blockers": [],
    },
    {
        "function": "FUN_0357d2",
        "base_register": "r2",
        "candidate_bases": ["6 * zero-extended byte index"],
        "candidate_access_envelopes": ["0xF63E-0xFC3D"],
        "evidence": (
            "Every site rebuilds r2 from the low byte of r8 with MOVBZ, then "
            "computes r2 = index * 6 before the fixed 0xF63E-0xF643 offset."
        ),
        "status": "all ten r2 write sites resolved",
        "blockers": [],
    },
    {
        "function": "FUN_0357d2",
        "base_register": "r4",
        "candidate_bases": ["fixed base plus bounded byte index * 6 or * 12"],
        "candidate_access_envelopes": ["0xEA24-0xFC3D"],
        "evidence": (
            "Six sites use 0xF642/0xF643 plus a MOVBZ-bounded byte index * 6; "
            "file 0x035A82 uses 0xEA24 plus the MOVBZ-bounded RL6 index * 12."
        ),
        "status": "all seven r4 write sites resolved",
        "blockers": [],
    },
    {
        "function": "FUN_001bcc",
        "base_register": "r15",
        "candidate_bases": ["0xFA46", "0xFA52"],
        "candidate_access_envelopes": ["0xFA46-0xFA5D"],
        "evidence": (
            "File 0x001D68 loads r15=0xFA52; the only alternate assignment at "
            "0x001D74 changes it to 0xFA46 before all ten writes."
        ),
        "status": "all ten r15 write sites resolved",
        "blockers": [],
    },
    {
        "function": "FUN_028100",
        "base_register": "r9",
        "candidate_bases": ["0xF018", "0xF0C4"],
        "candidate_access_envelopes": ["0xF018-0xF0F7"],
        "evidence": (
            "The only stock entry to file 0x0280B4 is called at 0x02F312 "
            "immediately after mov r12,r9; the entry copies r12 into r9."
        ),
        "status": "all eight r9 write sites resolved",
        "blockers": [],
    },
    {
        "function": "FUN_02218a",
        "base_register": "r2",
        "candidate_bases": ["6 * zero-extended byte index"],
        "candidate_access_envelopes": ["0xF63E-0xFC3D"],
        "evidence": (
            "Each site rebuilds r2 from MOVBZ-bounded RL6 and computes "
            "r2 = index * 6 before a fixed 0xF63E-0xF643 offset."
        ),
        "status": "all six r2 write sites resolved",
        "blockers": [],
    },
    {
        "function": "FUN_022998",
        "base_register": "r2",
        "candidate_bases": ["6 * zero-extended byte index"],
        "candidate_access_envelopes": ["0xF3FA-0xF9F9"],
        "evidence": (
            "Each site rebuilds r2 from MOVBZ-bounded RL6 and computes "
            "r2 = index * 6 before a fixed 0xF3FA-0xF3FF offset."
        ),
        "status": "all six r2 write sites resolved",
        "blockers": [],
    },
    {
        "function": "FUN_027b1c",
        "base_register": "r7",
        "candidate_bases": ["0xE523 plus at most 2 bytes per descriptor"],
        "candidate_access_envelopes": ["0xE523-0xE720"],
        "evidence": (
            "The stock entry gate admits only file 0x027A8E. That path initializes "
            "r7=0xE523, and the byte-sized descriptor count permits at most 255 "
            "iterations with two output bytes per iteration."
        ),
        "status": "all five r7 write sites resolved",
        "blockers": [],
    },
    {
        "function": "FUN_02ada4/FUN_02aeb8",
        "base_register": "r8",
        "candidate_bases": ["0xF182", "0xF18A"],
        "candidate_access_envelopes": ["0xF182-0xF193"],
        "evidence": (
            "The stock loop-entry gate admits only the preheader/loop-back path. "
            "Each iteration selects r8=0xF182 or 0xF18A before any r8 write."
        ),
        "status": "all nine r8 write sites resolved",
        "blockers": [],
    },
    {
        "function": "FUN_0352c0",
        "base_register": "r14/r4",
        "candidate_bases": ["0xF63E + 6 * zero-extended byte index"],
        "candidate_access_envelopes": ["0xF63E-0xFC3C"],
        "evidence": (
            "The stock entry gate admits only file 0x0352AC. The entry zero-extends "
            "the low byte of r12, multiplies it by six, and adds 0xF63E."
        ),
        "status": "all eight r14/r4 write sites resolved",
        "blockers": [],
    },
    {
        "function": "paired-object updater at file 0x02FD6E",
        "base_register": "r8",
        "candidate_bases": ["0xF018", "0xF0C4"],
        "candidate_access_envelopes": ["0xF024-0xF0D3"],
        "evidence": (
            "The stock entry gate has two callers, each immediately passing the "
            "proven FUN_02b0cc r9 object through r12; the entry copies r12 to r8."
        ),
        "status": "all eight r8 write sites resolved",
        "blockers": [],
    },
    {
        "function": "FUN_02c024",
        "base_register": "r2",
        "candidate_bases": ["6 * zero-extended byte index"],
        "candidate_access_envelopes": ["0xF63F-0xFC3D"],
        "evidence": (
            "Each write immediately rebuilds r2 from MOVBZ-bounded RL7 and "
            "computes r2 = index * 6."
        ),
        "status": "all four r2 write sites resolved",
        "blockers": [],
    },
    {
        "function": "FUN_036128",
        "base_register": "r5",
        "candidate_bases": ["zero-extended low byte of r8"],
        "candidate_access_envelopes": ["0xF748-0xF847"],
        "evidence": "Each write immediately bounds r5 with MOVBZ from RL5.",
        "status": "all four r5 write sites resolved",
        "blockers": [],
    },
    {
        "function": "FUN_0362b4",
        "base_register": "r4",
        "candidate_bases": ["zero-extended low byte of r8"],
        "candidate_access_envelopes": ["0xF748-0xF847"],
        "evidence": "Each write immediately bounds r4 with MOVBZ from RL4.",
        "status": "all four r4 write sites resolved",
        "blockers": [],
    },
    {
        "function": "FUN_020a26",
        "base_register": "r9/r4",
        "candidate_bases": ["0xE52E plus three 6-word serialization loops"],
        "candidate_access_envelopes": ["0xE52E-0xE551"],
        "evidence": (
            "r9 starts at 0xE52E; each of three six-iteration loops writes two "
            "bytes and advances r9 by two."
        ),
        "status": "all six r9/r4 write sites resolved",
        "blockers": [],
    },
    {
        "function": "FUN_020f5a",
        "base_register": "r5",
        "candidate_bases": ["zero-extended stack byte"],
        "candidate_access_envelopes": ["0xE523-0xE624"],
        "evidence": "Each write immediately bounds r5 with MOVBZ from RL5.",
        "status": "all three r5 write sites resolved",
        "blockers": [],
    },
    {
        "function": "FUN_0044e6",
        "base_register": "r4",
        "candidate_bases": ["zero-extended counted-loop byte"],
        "candidate_access_envelopes": ["0xE523-0xE643"],
        "evidence": "Each write immediately bounds r4 with MOVBZ from RL4.",
        "status": "all three r4 write sites resolved",
        "blockers": [],
    },
    {
        "function": "FUN_0044e6",
        "base_register": "r0",
        "candidate_bases": ["stock startup RAM-test table ranges"],
        "candidate_access_envelopes": ["0xD080-0xD0FF", "0xD800-0xF7F3"],
        "evidence": (
            "The exact immutable startup table supplies r0 for the word and byte "
            "RAM-test loops at file 0x0045D0 and 0x004658."
        ),
        "status": "two startup r0 write sites resolved",
        "blockers": [],
    },
    {
        "function": "FUN_00098a",
        "base_register": "r5",
        "candidate_bases": ["0xE523-0xE621"],
        "candidate_access_envelopes": ["0xE523-0xE621"],
        "evidence": (
            "At file 0x004E2E r5 copies r9, which starts at 0xE523 and advances "
            "once per iteration of an 8-bit count."
        ),
        "status": "counted-loop value set resolved",
        "blockers": [],
    },
)
COUNTDOWN_RECORD_BASES = (
    0xEA26, 0xEA56, 0xEA62, 0xEB22, 0xEB2E, 0xEB3A, 0xEB46, 0xEB52,
    0xEB5E, 0xEBE2, 0xEC36, 0xEC4E, 0xEC5A, 0xED4A, 0xED56, 0xED62,
    0xED6E, 0xED7A, 0xED86, 0xEE46, 0xEE52,
)
STATE_RECORD_BASES = (
    0xEA32, 0xEB76, 0xEBFA, 0xEC06, 0xEC12, 0xECDE, 0xECEA, 0xEE16,
    0xEE22, 0xEE5E, 0xEE76, 0xEE82,
)
PAIRED_COUNTDOWN_RECORD_BASES = (
    0xEA9E, 0xEAAA, 0xEACE, 0xEADA, 0xEAE6, 0xEAF2, 0xEAFE, 0xEB0A,
    0xEB16, 0xEB9A, 0xEBA6, 0xEBBE, 0xEBCA, 0xEBD6, 0xEBEE, 0xED26,
    0xED32, 0xED92, 0xED9E, 0xEDC2, 0xEDCE, 0xEE0A,
)
RECORD_STATUS_HELPER_BASES = tuple(sorted({
    *COUNTDOWN_RECORD_BASES,
    *STATE_RECORD_BASES,
    *PAIRED_COUNTDOWN_RECORD_BASES,
    0xEC2A,
}))
CRC_UPDATE_DESTINATIONS = (0xF482, 0xF484, 0xF486)
BYTE_SEARCH_OUTPUTS = tuple(sorted({
    *range(0xE537, 0xE53F),
    *range(0xE54B, 0xE557),
    *range(0xE55F, 0xE56F),
    *range(0xE754, 0xE760),
    *range(0xE760, 0xE767),
    *range(0xE767, 0xE76E),
}))

PROVEN_POINTER_VALUE_SETS = (
    {
        "owner": "FUN_02b0cc",
        "base_register": "r9",
        "values": (0xF018, 0xF0C4),
        "evidence": (
            "r9 is selected as 0xF018 or 0xF0C4 at file 0x02B054/0x02B05A "
            "and 0x02F20A/0x02F220 before the shared FUN_02b0cc bodies."
        ),
    },
    {
        "owner": "FUN_02b0cc",
        "base_register": "r4",
        "pcs": (0x028276, 0x0282C4),
        "values": (0xF057, 0xF103),
        "evidence": "r4 is r9 + 0x3F immediately before each write.",
    },
    {
        "label": "object-entry gated r4",
        "base_register": "r4",
        "pcs": (0x028220,),
        "values": (0xF057, 0xF103),
        "evidence": "The enclosing stock entry gate proves r4 is r9 + 0x3F.",
    },
    {
        "owner": "FUN_028100",
        "base_register": "r4",
        "pcs": (0x028102,),
        "values": (0xF04A, 0xF0F6),
        "evidence": "The proven entry value is r9; r4 is r9 + 0x32.",
    },
    {
        "owner": "FUN_02b0cc",
        "base_register": "r4",
        "pcs": (0x02B07C,),
        "values": (0xF08C, 0xF138),
        "evidence": "r4 is r9 + 0x74 immediately before the write.",
    },
    {
        "owner": "FUN_02b0cc",
        "base_register": "r4",
        "pcs": (0x02B090,),
        "values": (0xF06C, 0xF118),
        "evidence": "r4 is r9 + 0x54 immediately before the write.",
    },
    {
        "owner": "FUN_02b0cc",
        "base_register": "r4",
        "pcs": (0x02B200,),
        "values": (0xF06A, 0xF116),
        "evidence": "r4 is r9 + 0x52 immediately before the write.",
    },
    {
        "owner": "FUN_02b0cc",
        "base_register": "r4",
        "pcs": (0x02F35E,),
        "values": (0xF04F, 0xF0FB),
        "evidence": "r4 is r9 + 0x37 immediately before the write.",
    },
    {
        "owner": "FUN_02b0cc",
        "base_register": "r4",
        "pcs": (0x02F374,),
        "values": (0xF042, 0xF0EE),
        "evidence": "r4 is r9 + 0x2A immediately before the write.",
    },
    {
        "owner": "FUN_02b0cc",
        "base_register": "r4",
        "pcs": (0x02F3A8,),
        "values": (0xF04D, 0xF0F9),
        "evidence": (
            "The path compares 0xEF60 to r9, exits on mismatch, then adds "
            "0x35 to that proven-equal value."
        ),
    },
    {
        "owner": "FUN_02b0cc",
        "base_register": "r4",
        "pcs": (0x02845A,),
        "values": (0xF0BE, 0xF16A),
        "evidence": "Entry proof establishes r8=r12=r9; r4 is r8 + 0xA6.",
    },
    {
        "owner": "FUN_02b0cc",
        "base_register": "r4",
        "pcs": (0x02846E,),
        "values": (0xF0C0, 0xF16C),
        "evidence": "Entry proof establishes r8=r12=r9; r4 is r8 + 0xA8.",
    },
    {
        "owner": "FUN_02b0cc",
        "base_register": "r4",
        "pcs": (0x02864E,),
        "values": (0xF04C, 0xF0F8),
        "evidence": "Entry proof establishes r8=r12=r9; r4 is r8 + 0x34.",
    },
    {
        "owner": "FUN_02b0cc",
        "base_register": "r4",
        "pcs": (0x02FC22, 0x02FCDC),
        "values": (0xF049, 0xF0F5),
        "evidence": "Entry proof establishes r8=r12=r9; r4 is r8 + 0x31.",
    },
    {
        "owner": "FUN_02b0cc",
        "base_register": "r8",
        "pcs": (
            0x0286B4,
            0x028770,
            0x0287A2,
            0x0287A6,
            0x02F7DA,
            0x02F7DE,
            0x02F7E4,
            0x02F7F2,
            0x02F800,
            0x02F80E,
            0x02F81C,
            0x02FAB0,
            0x02FB3A,
            0x02FBF6,
        ),
        "values": (0xF018, 0xF0C4),
        "evidence": (
            "Stock entry gates prove every path passes r9 through r12 and "
            "copies r12 into r8."
        ),
    },
    {
        "owner": "FUN_0287c2",
        "base_register": "r8",
        "pcs": (0x0287C8, 0x0287D6, 0x0287E4),
        "values": (0xF018, 0xF0C4),
        "evidence": (
            "The enclosing FUN_02853c stock entry gate proves every path passes "
            "r9 through r12 and copies r12 into r8."
        ),
    },
    {
        "label": "object-entry gated r8",
        "base_register": "r8",
        "pcs": (0x0287AC, 0x0287BA, 0x02FCB2),
        "values": (0xF018, 0xF0C4),
        "evidence": (
            "The enclosing stock entry gates prove every path passes r9 through "
            "r12 and copies r12 into r8."
        ),
    },
    {
        "label": "object-entry gated r9",
        "base_register": "r9",
        "pcs": (0x028226, 0x02822A),
        "values": (0xF018, 0xF0C4),
        "evidence": "The enclosing stock entry gate preserves the proven r9 object base.",
    },
    {
        "owner": "FUN_020986",
        "base_register": "r5",
        "values": range(0x100),
        "evidence": (
            "MOVBZ from byte 0xFAC2 immediately precedes each write, so r5 "
            "is necessarily 0x0000-0x00FF."
        ),
    },
    {
        "owner": "flash_write_orchestrator",
        "base_register": "r5",
        "pcs": (0x0051B0,),
        "values": range(0x100),
        "evidence": "MOVBZ from byte 0xE73A immediately establishes the scan-buffer index.",
    },
    {
        "label": "FUN_024e30 receive-ring byte writers",
        "base_register": "r5",
        "pcs": (0x007912, 0x007952),
        "values": range(0x20),
        "evidence": "MOVBZ reads the 5-bit 0xFAC2 ring index immediately before each write.",
    },
    {
        "label": "FUN_024e30 transmit-ring byte writers",
        "base_register": "r5",
        "pcs": (
            0x0079F2,
            0x007A22,
            0x007A78,
            0x007ACC,
            0x007B1C,
            0x007B4A,
            0x007BCC,
            0x007ED6,
        ),
        "values": range(0x20),
        "evidence": "MOVBZ reads the 5-bit 0xFAC4 ring index immediately before each write.",
    },
    {
        "label": "FUN_024e30 transmit-ring byte writers",
        "base_register": "r4",
        "pcs": (0x0079BE,),
        "values": range(0x20),
        "evidence": "MOVBZ reads the 5-bit 0xFAC4 ring index immediately before the write.",
    },
    {
        "label": "FUN_024e30 transmit-ring byte writers",
        "base_register": "r2",
        "pcs": (0x007B9A,),
        "values": range(0x20),
        "evidence": "MOVBZ reads the 5-bit 0xFAC4 ring index immediately before the write.",
    },
    {
        "label": "FUN_024e30 countdown table",
        "base_register": "r7",
        "pcs": (0x007C46,),
        "values": range(0xF770, 0xF794, 2),
        "evidence": "The 18-entry word loop initializes r7 to 0xF770 and advances by two.",
    },
    {
        "label": "FUN_024e30 countdown table",
        "base_register": "r7",
        "pcs": (0x007F0C,),
        "values": range(0xF794, 0xF7D2, 2),
        "evidence": "The 31-entry word loop initializes r7 to 0xF794 and advances by two.",
    },
    {
        "label": "FUN_024e30 countdown table",
        "base_register": "r7",
        "pcs": (0x007F92,),
        "values": range(0xF7D2, 0xF7F2, 2),
        "evidence": "The 16-entry word loop initializes r7 to 0xF7D2 and advances by two.",
    },
    {
        "label": "stock startup byte-derived writers",
        "base_register": "r5",
        "pcs": (0x0049CC, 0x0049E6, 0x0049F2, 0x004B26, 0x004C54, 0x004D42),
        "values": range(0x100),
        "evidence": "MOVBZ from a byte immediately establishes r5.",
    },
    {
        "owner": "FUN_00098a",
        "base_register": "r5",
        "pcs": (0x004E2E,),
        "values": range(0xE523, 0xE622),
        "evidence": (
            "r9 starts at 0xE523 and advances once per iteration of an "
            "8-bit count, so the copied r5 value cannot exceed 0xE621."
        ),
    },
    {
        "owner": "FUN_0044e6",
        "base_register": "r2",
        "pcs": (0x0044EC, 0x0044FA, 0x004510),
        "values": range(0xFA00, 0xFA16, 2),
        "evidence": "The first stock startup RAM-test range is 0xFA00-0xFA14.",
    },
    {
        "owner": "FUN_0044e6",
        "base_register": "r2",
        "pcs": (0x00452A,),
        "values": range(0xFA02, 0xFA18, 2),
        "evidence": "Predecrement covers 0xFA00-0xFA14 in the first RAM-test range.",
    },
    {
        "owner": "FUN_0044e6",
        "base_register": "r2",
        "pcs": (0x004564, 0x004572, 0x004588),
        "values": range(0xFA16, 0xFE00, 2),
        "evidence": "The second stock startup RAM-test range is 0xFA16-0xFDFE.",
    },
    {
        "owner": "FUN_0044e6",
        "base_register": "r2",
        "pcs": (0x004558, 0x0045A2),
        "values": range(0xFA18, 0xFE02, 2),
        "evidence": "Predecrement covers 0xFA16-0xFDFE in the second RAM-test range.",
    },
    {
        "owner": "FUN_0044e6",
        "base_register": "r2",
        "pcs": (0x0045EC, 0x0045F6),
        "values": range(0xD800, 0xF7F4, 2),
        "evidence": (
            "The exact stock table and 0xA000 marker select adjacent word-test "
            "ranges spanning 0xD800-0xF7F2."
        ),
    },
    {
        "owner": "FUN_0044e6",
        "base_register": "r2",
        "pcs": (0x0045E0, 0x004610),
        "values": range(0xD802, 0xF7F6, 2),
        "evidence": (
            "Predecrement covers the stock external-RAM word-test ranges "
            "0xD800-0xF7F2."
        ),
    },
    {
        "owner": "FUN_0044e6",
        "base_register": "r2",
        "pcs": (0x004678, 0x004686, 0x00469A),
        "values": range(0xD080, 0xD100),
        "evidence": "The stock byte RAM-test boundaries are 0xD080-0xD0FF.",
    },
    {
        "owner": "FUN_0044e6",
        "base_register": "r2",
        "pcs": (0x00466C, 0x0046B8),
        "values": range(0xD081, 0xD101),
        "evidence": "Predecrement covers the byte RAM-test range 0xD080-0xD0FF.",
    },
    {
        "owner": "FUN_0357d2",
        "base_register": "r2",
        "pcs": (
            0x035A10,
            0x035A20,
            0x035A30,
            0x035A40,
            0x035A50,
            0x035AF2,
            0x035B02,
            0x035B12,
            0x035B22,
            0x035B32,
        ),
        "values": range(0, 0x5FB, 6),
        "evidence": "MOVBZ bounds the source byte before r2 = index * 6.",
    },
    {
        "owner": "FUN_0357d2",
        "base_register": "r4",
        "pcs": (0x0358B8, 0x0359D6, 0x035A00, 0x035ABA, 0x035AE2),
        "values": range(0xF642, 0xFC3D, 6),
        "evidence": "r4 is 0xF642 plus a MOVBZ-bounded byte index * 6.",
    },
    {
        "owner": "FUN_0357d2",
        "base_register": "r4",
        "pcs": (0x035A70,),
        "values": range(0xF643, 0xFC3E, 6),
        "evidence": "r4 is 0xF643 plus a MOVBZ-bounded byte index * 6.",
    },
    {
        "owner": "FUN_0357d2",
        "base_register": "r4",
        "pcs": (0x035A82,),
        "values": range(0xEA24, 0xF619, 12),
        "evidence": "r4 is 0xEA24 plus a MOVBZ-bounded RL6 index * 12.",
    },
    {
        "owner": "FUN_001bcc",
        "base_register": "r15",
        "values": (0xFA46, 0xFA52),
        "evidence": "The local branch selects only 0xFA46 or 0xFA52.",
    },
    {
        "owner": "FUN_028100",
        "base_register": "r9",
        "values": (0xF018, 0xF0C4),
        "evidence": "The proven entry path passes the FUN_02b0cc r9 value through r12.",
    },
    {
        "owner": "FUN_02218a",
        "base_register": "r2",
        "values": range(0, 0x5FB, 6),
        "evidence": "MOVBZ bounds RL6 before r2 = index * 6.",
    },
    {
        "owner": "FUN_027b1c",
        "base_register": "r7",
        "values": range(0xE523, 0xE720),
        "evidence": (
            "The sole stock entry initializes r7=0xE523; a byte-sized count "
            "limits the loop to 255 descriptors and each advances r7 by at most two."
        ),
    },
    {
        "owner": "FUN_02ada4",
        "base_register": "r8",
        "values": (0xF182, 0xF18A),
        "evidence": "The stock loop-entry proof forces the local r8 selection first.",
    },
    {
        "owner": "FUN_02aeb8",
        "base_register": "r8",
        "values": (0xF182, 0xF18A),
        "evidence": "The stock loop-entry proof forces the local r8 selection first.",
    },
    {
        "owner": "FUN_0352c0",
        "base_register": "r14",
        "values": range(0xF63E, 0xFC39, 6),
        "evidence": "r14 is 0xF63E plus a zero-extended byte index * 6.",
    },
    {
        "owner": "FUN_0352c0",
        "base_register": "r4",
        "values": range(0xF642, 0xFC3D, 6),
        "evidence": "r4 is the proven r14 value plus four.",
    },
    {
        "label": "paired-object updater",
        "base_register": "r8",
        "pcs": (
            0x02FD90,
            0x02FDAC,
            0x02FDB2,
            0x02FDBA,
            0x02FDD4,
            0x02FDF0,
            0x02FDF6,
            0x02FDFE,
        ),
        "values": (0xF018, 0xF0C4),
        "evidence": "The entry proof copies the proven caller r9 value through r12.",
    },
    {
        "owner": "FUN_02c024",
        "base_register": "r2",
        "values": range(0, 0x5FB, 6),
        "evidence": "MOVBZ bounds RL7 before r2 = index * 6.",
    },
    {
        "owner": "FUN_036128",
        "base_register": "r5",
        "pcs": (0x036172, 0x036180, 0x0361B8, 0x0361C6),
        "values": range(0x100),
        "derivation": "movbz",
        "evidence": "MOVBZ from RL5 immediately establishes r5.",
    },
    {
        "owner": "FUN_0362b4",
        "base_register": "r4",
        "pcs": (0x0362DE, 0x0362F8, 0x036330, 0x03634A),
        "values": range(0x100),
        "derivation": "movbz",
        "evidence": "MOVBZ from RL4 immediately establishes r4.",
    },
    {
        "owner": "FUN_03635e",
        "base_register": "r4",
        "pcs": (0x036368, 0x036382),
        "values": range(0x100),
        "derivation": "movbz",
        "evidence": "MOVBZ from RL4 immediately establishes r4.",
    },
    {
        "owner": "FUN_029898",
        "base_register": "r5",
        "values": range(0x20),
        "evidence": "Each write immediately loads r5 from the 0x1F-masked ring index.",
    },
    {
        "owner": "FUN_02e52e",
        "base_register": "r5",
        "values": range(0, 0x200, 2),
        "evidence": (
            "The stock entry gate forces r6 through MOVBZ at file 0x02E50A; "
            "each write copies r6 and doubles it."
        ),
    },
    {
        "owner": "FUN_0314a8",
        "base_register": "r5",
        "pcs": (0x0314F0, 0x03150C),
        "values": (2, 4, 6, 8, 10),
        "evidence": "The gated five-iteration loop sets r9=1..5 and r5=r9*2.",
    },
    {
        "owner": "FUN_02ff78",
        "base_register": "r9",
        "values": (0xF018, 0xF0C4),
        "evidence": (
            "The sole gated caller passes the proven FUN_02b0cc r9 object "
            "through r12; the entry copies r12 into r9."
        ),
    },
    {
        "label": "object metric updater",
        "base_register": "r8",
        "pcs": (
            0x02FE52,
            0x02FE6E,
            0x02FE86,
            0x02FEA2,
            0x02FEAC,
            0x02FED2,
        ),
        "values": (0xF018, 0xF0C4),
        "evidence": (
            "All three gated callers pass a proven object pointer through r12; "
            "the entry copies r12 into r8."
        ),
    },
    {
        "owner": "thunk_FUN_0348ac",
        "base_register": "r8",
        "values": (0xEFB4, 0xEFC0),
        "evidence": "All four gated callers pass the immediate base 0xEFB4 or 0xEFC0.",
    },
    {
        "owner": "thunk_FUN_0348ac",
        "base_register": "r4",
        "pcs": (0x03D042,),
        "values": (0xEFB8, 0xEFC4),
        "evidence": "The gated r8 base is incremented by four immediately before the write.",
    },
    {
        "label": "local MOVBZ r5 writers",
        "base_register": "r5",
        "pcs": (
            0x02253A,
            0x022544,
            0x023A2A,
            0x023AAA,
            0x023B74,
            0x023C7E,
            0x023DF2,
            0x023E72,
            0x024A2C,
            0x024A88,
            0x024AB6,
            0x024AE4,
            0x024D44,
            0x024D7A,
            0x024DB0,
            0x024DE2,
            0x024E18,
            0x024E48,
            0x024E78,
            0x029850,
            0x02988E,
            0x02996E,
            0x02C65A,
            0x0361FC,
            0x036208,
            0x037230,
        ),
        "values": range(0x100),
        "derivation": "movbz",
        "evidence": "Each explicit site immediately zero-extends r5 before writing.",
    },
    {
        "label": "local MOVBZ r4 writers",
        "base_register": "r4",
        "pcs": (
            0x020FA0,
            0x021088,
            0x0226E0,
            0x022B1C,
            0x023B84,
            0x023C8E,
            0x029820,
            0x029930,
            0x02C288,
            0x02ED96,
            0x035DDA,
            0x03621E,
            0x036228,
            0x03723E,
            0x03724A,
            0x037262,
            0x037296,
        ),
        "values": range(0x100),
        "derivation": "movbz",
        "evidence": "Each explicit site immediately zero-extends r4 before writing.",
    },
    {
        "label": "local MOVBZ r0 writer",
        "base_register": "r0",
        "pcs": (0x039964,),
        "values": range(0x100),
        "derivation": "movbz",
        "evidence": "The explicit site immediately zero-extends r0 before writing.",
    },
    {
        "label": "local doubled-MOVBZ r5 writers",
        "base_register": "r5",
        "pcs": (
            0x02358E,
            0x0235B6,
            0x0236FE,
            0x02B77A,
            0x02B7E0,
            0x0335A0,
            0x034442,
            0x034464,
        ),
        "values": range(0, 0x200, 2),
        "derivation": "movbz_x2",
        "evidence": "Each explicit site zero-extends r5 and doubles it before writing.",
    },
    {
        "label": "local doubled-MOVBZ r4 writers",
        "base_register": "r4",
        "pcs": (0x03464E, 0x03572E, 0x0371F2, 0x037208),
        "values": range(0, 0x200, 2),
        "derivation": "movbz_x2",
        "evidence": "Each explicit site zero-extends r4 and doubles it before writing.",
    },
    {
        "label": "local six-word copy",
        "base_register": "r4",
        "pcs": (0x02292E,),
        "values": range(0, 12, 2),
        "evidence": "RL7 starts at zero and the loop stops after index five.",
    },
    {
        "label": "local 60-byte state clear",
        "base_register": "r9",
        "pcs": (0x0351C0,),
        "values": range(0xF63E, 0xF67A),
        "evidence": "The loop initializes r9 to 0xF63E and advances it through 60 byte writes.",
    },
    {
        "label": "local 95-record flag clear",
        "base_register": "r2",
        "pcs": (0x0351DA, 0x0351E8),
        "values": range(0, 0x469, 12),
        "evidence": "RL6 runs from 0 through 94 and the local arithmetic forms r2 = 12 * RL6.",
    },
    {
        "label": "local bitset word writers",
        "base_register": "r2",
        "pcs": (0x0354E0,),
        "values": range(0xF5F2, 0xF5FE, 2),
        "evidence": "The loop bounds the high nibble of its 0-94 byte index to 0-5.",
    },
    {
        "label": "local bitset word writers",
        "base_register": "r2",
        "pcs": (0x035500,),
        "values": range(0xF5FE, 0xF60A, 2),
        "evidence": "The loop bounds the high nibble of its 0-94 byte index to 0-5.",
    },
    {
        "label": "local bitset word writers",
        "base_register": "r2",
        "pcs": (0x0355E0,),
        "values": range(0xF60A, 0xF616, 2),
        "evidence": "The loop bounds the high nibble of its 0-94 byte index to 0-5.",
    },
    {
        "label": "local bitset word writers",
        "base_register": "r2",
        "pcs": (0x035698,),
        "values": range(0xF616, 0xF622, 2),
        "evidence": "The loop bounds the high nibble of its 0-94 byte index to 0-5.",
    },
    {
        "label": "local six-byte record status writer",
        "base_register": "r4",
        "pcs": (0x023FF4,),
        "values": range(0xF642, 0xFC37, 6),
        "evidence": "A non-0xFF byte result is multiplied by six and added to 0xF642.",
    },
    {
        "label": "local six-byte mask writers",
        "base_register": "r5",
        "pcs": (0x02383E, 0x023870, 0x0238A0, 0x0238D0, 0x023900, 0x023930),
        "values": range(0xF547, 0xF54D),
        "evidence": "Each six-iteration loop sets r5 to 0xF547 plus its 0-5 counter.",
    },
    {
        "label": "countdown record updater",
        "base_register": "r8",
        "pcs": (
            0x023988, 0x0239FC, 0x023A00, 0x023A3A, 0x023A44,
            0x023A4E, 0x023A58, 0x023A74, 0x023A7A, 0x023A82,
            0x023ABC, 0x023AD4, 0x023B08,
        ),
        "values": COUNTDOWN_RECORD_BASES,
        "exact_values": True,
        "evidence": "Every live stock caller passes one proven immediate record base in r12.",
    },
    {
        "label": "countdown record byte-10 writers",
        "base_register": "r4",
        "pcs": (0x023998, 0x0239BA, 0x0239DC),
        "values": tuple(value + 10 for value in COUNTDOWN_RECORD_BASES),
        "exact_values": True,
        "evidence": "r4 is the gated countdown-record base plus ten.",
    },
    {
        "label": "countdown record byte-11 writer",
        "base_register": "r4",
        "pcs": (0x0239C8,),
        "values": tuple(value + 11 for value in COUNTDOWN_RECORD_BASES),
        "exact_values": True,
        "evidence": "r4 is the gated countdown-record base plus eleven.",
    },
    {
        "label": "countdown record byte-2 writer",
        "base_register": "r4",
        "pcs": (0x023AEA,),
        "values": tuple(value + 2 for value in COUNTDOWN_RECORD_BASES),
        "exact_values": True,
        "evidence": "r4 is the gated countdown-record base plus two.",
    },
    {
        "label": "state record updater",
        "base_register": "r9",
        "pcs": (
            0x023B5A, 0x023B98, 0x023BA2, 0x023BAC, 0x023BB6,
            0x023BD6, 0x023BDE, 0x023BE8, 0x023C00,
        ),
        "values": STATE_RECORD_BASES,
        "exact_values": True,
        "evidence": "Every live stock caller passes one proven immediate record base in r12.",
    },
    {
        "label": "state record byte-10 writer",
        "base_register": "r4",
        "pcs": (0x023B46,),
        "values": tuple(value + 10 for value in STATE_RECORD_BASES),
        "exact_values": True,
        "evidence": "r4 is the gated state-record base plus ten.",
    },
    {
        "label": "state record byte-2 writer",
        "base_register": "r4",
        "pcs": (0x023C16,),
        "values": tuple(value + 2 for value in STATE_RECORD_BASES),
        "exact_values": True,
        "evidence": "r4 is the gated state-record base plus two.",
    },
    {
        "label": "paired countdown record updater",
        "base_register": "r8",
        "pcs": (
            0x023D50, 0x023DC4, 0x023DC8, 0x023E02, 0x023E0C,
            0x023E16, 0x023E20, 0x023E3C, 0x023E42, 0x023E4A,
            0x023E94, 0x023ED0, 0x023ED4,
        ),
        "values": PAIRED_COUNTDOWN_RECORD_BASES,
        "exact_values": True,
        "evidence": "Every live stock caller passes one proven immediate record base in r12.",
    },
    {
        "label": "paired countdown record byte-10 writers",
        "base_register": "r4",
        "pcs": (0x023D60, 0x023D82, 0x023DA4),
        "values": tuple(value + 10 for value in PAIRED_COUNTDOWN_RECORD_BASES),
        "exact_values": True,
        "evidence": "r4 is the gated paired-record base plus ten.",
    },
    {
        "label": "paired countdown record byte-11 writer",
        "base_register": "r4",
        "pcs": (0x023D90,),
        "values": tuple(value + 11 for value in PAIRED_COUNTDOWN_RECORD_BASES),
        "exact_values": True,
        "evidence": "r4 is the gated paired-record base plus eleven.",
    },
    {
        "label": "paired countdown record byte-2 writer",
        "base_register": "r4",
        "pcs": (0x023EAA,),
        "values": tuple(value + 2 for value in PAIRED_COUNTDOWN_RECORD_BASES),
        "exact_values": True,
        "evidence": "r4 is the gated paired-record base plus two.",
    },
    {
        "label": "record-status helper",
        "base_register": "r9",
        "pcs": (0x02378A, 0x02379C),
        "values": RECORD_STATUS_HELPER_BASES,
        "exact_values": True,
        "evidence": "All live helper calls forward a gated record base through r12.",
    },
    {
        "label": "record-status helper byte-10 writers",
        "base_register": "r4",
        "pcs": (0x02373A, 0x0237B8),
        "values": tuple(value + 10 for value in RECORD_STATUS_HELPER_BASES),
        "exact_values": True,
        "evidence": "r4 is the gated helper record base plus ten.",
    },
    {
        "label": "local bounded table-copy destination",
        "base_register": "r4",
        "pcs": (0x022716,),
        "values": range(0xF343, 0xF3BB),
        "evidence": (
            "The outer loop caps RL6 below 10 and the inner loop caps RL7 below "
            "12; r4 is 0xF343 + 12*RL6 + RL7."
        ),
    },
    {
        "label": "local zero-fill destination",
        "base_register": "r9",
        "pcs": (0x022942,),
        "values": range(0xF3FA, 0xF436),
        "evidence": "r9 starts at 0xF3FA and the byte loop is capped at 60 writes.",
    },
    {
        "label": "record byte-10 mask writer",
        "base_register": "r4",
        "pcs": (0x023806,),
        "values": range(0xEA24, 0xF619, 12),
        "evidence": "r4 is 0xEA1A + 12*MOVBZ(RL4) + 10.",
    },
    {
        "label": "record byte-3 countdown writer",
        "base_register": "r4",
        "pcs": (0x023F92,),
        "values": range(0xEA1D, 0xEE86, 12),
        "evidence": "r4 is 0xEA1A + 12 times a proven 0..94 record ID, plus 3.",
    },
    {
        "label": "record byte-10 mask writer",
        "base_register": "r4",
        "pcs": (0x023FB2,),
        "values": range(0xEA24, 0xEE8D, 12),
        "evidence": "r4 is 0xEA1A + 12 times a proven 0..94 record ID, plus 10.",
    },
    {
        "label": "CRC accumulator writer",
        "base_register": "r14",
        "pcs": (0x03747E, 0x037486),
        "values": CRC_UPDATE_DESTINATIONS,
        "exact_values": True,
        "evidence": "Every live stock caller passes one proven immediate accumulator in r14.",
    },
    {
        "label": "fixed native status byte",
        "base_register": "r9",
        "pcs": (0x02541A,),
        "values": (0xF768,),
        "evidence": "r9 is initialized to 0xF768 at the function entry and is unchanged.",
    },
    {
        "label": "record byte-3 state writer",
        "base_register": "r9",
        "pcs": (0x02C0E0,),
        "values": range(0xEA1A, 0xEE83, 12),
        "evidence": "r9 is 0xEA1A + 12 times a proven 0..94 record ID.",
    },
    {
        "label": "paired native-object result writer",
        "base_register": "r9",
        "pcs": (0x032240,),
        "values": (0xF018, 0xF0C4),
        "evidence": "The loop selects 0xF018 or 0xF0C4 before the write.",
    },
    {
        "label": "16-entry interrupt ring writer",
        "base_register": "r2",
        "pcs": (0x0398DE,),
        "values": range(0, 64, 4),
        "evidence": "The boot-zeroed 0xF698 index remains in 0..15 and is multiplied by four.",
    },
    {
        "label": "16-entry interrupt ring writer",
        "base_register": "r2",
        "pcs": (0x0398E4,),
        "values": range(2, 64, 4),
        "evidence": "The bounded four-byte ring offset is incremented by two.",
    },
    {
        "label": "paired native-object reset",
        "base_register": "r12",
        "pcs": (0x02DA30, 0x02DA36),
        "values": (0xF018, 0xF0C4),
        "evidence": "The only live callers pass 0xF018 or 0xF0C4 in r12.",
    },
    {
        "label": "paired native-object reset",
        "base_register": "r4",
        "pcs": (0x02DA46,),
        "values": (0xF06E, 0xF11A),
        "evidence": "r4 is the gated object base plus 0x56.",
    },
    {
        "label": "fixed optional result byte",
        "base_register": "r9",
        "pcs": (0x02A588,),
        "values": (0xE8EB,),
        "evidence": "The sole live caller passes 0xE8EB in r13; entry copies it to r9.",
    },
    {
        "label": "startup word zero-fill",
        "base_register": "r9",
        "pcs": (0x004906,),
        "values": range(0xE720, 0xE820, 2),
        "evidence": "r9 starts at 0xE720 and the loop stops after 0xE81E.",
    },
    {
        "label": "startup word zero-fill",
        "base_register": "r9",
        "pcs": (0x004918,),
        "values": range(0xE420, 0xE720, 2),
        "evidence": "r9 starts at 0xE420 and the loop stops after 0xE71E.",
    },
    {
        "label": "local six-byte clear",
        "base_register": "r5",
        "pcs": (0x0351FC,),
        "values": range(6),
        "evidence": "RL6 starts at zero and the loop stops after index five.",
    },
    {
        "label": "startup boundary RAM-test write",
        "base_register": "r2",
        "pcs": (0x0044E0,),
        "values": (0xFA16,),
        "evidence": "The exact 0xA314 startup-table word is 0xFA00; r2 is 0xFA16 before predecrement.",
    },
    {
        "label": "receive-buffer checksum byte",
        "base_register": "r12",
        "pcs": (0x004A20,),
        "values": range(0xE520, 0xE620),
        "evidence": "r12 starts at 0xE520 and advances at most 255 byte iterations.",
    },
    {
        "label": "bounded byte-search output",
        "base_register": "r14",
        "pcs": (0x005AF4,),
        "values": BYTE_SEARCH_OUTPUTS,
        "exact_values": True,
        "evidence": "All live callers pass one fixed output buffer and byte length.",
    },
    {
        "label": "flash command target",
        "base_register": "r12",
        "pcs": (0x004240, 0x00427E, 0x004280, 0x00428A, 0x00430A, 0x004310),
        "values": range(0x4000),
        "evidence": "The 0xE656 flash offset is masked to 14 bits; the loop wraps after 0x3FFE.",
    },
    {
        "label": "flash command target",
        "base_register": "r5",
        "pcs": (0x00434E, 0x004358, 0x004362, 0x004386, 0x0043F2, 0x0043FC),
        "values": range(0x4000),
        "evidence": "Every write reloads the proven 14-bit flash offset from 0xE656.",
    },
    {
        "label": "flash command target",
        "base_register": "r4",
        "pcs": (0x00438C,),
        "values": range(0x4000),
        "evidence": "The read reloads the proven 14-bit flash offset from 0xE656.",
    },
    {
        "label": "local bitset word writers",
        "base_register": "r3",
        "pcs": (0x023770,),
        "values": range(0xF60A, 0xF62A, 2),
        "evidence": "r3 is 0xF60A plus twice the high nibble of MOVBZ(RL6).",
    },
    {
        "label": "local bitset word writers",
        "base_register": "r2",
        "pcs": (0x0237DC,),
        "values": range(0xF60A, 0xF62A, 2),
        "evidence": "r2 is 0xF60A plus twice the high nibble of MOVBZ(RL6).",
    },
    {
        "label": "local record flag writer",
        "base_register": "r9",
        "pcs": (0x035B44,),
        "values": range(0xEA1A, 0xF60F, 12),
        "evidence": "r9 is 0xEA1A plus 12 times the MOVBZ record byte.",
    },
    {
        "label": "local 95-record flag writer",
        "base_register": "r4",
        "pcs": (0x035BC2,),
        "values": range(0xEA24, 0xEE8D, 12),
        "evidence": "RL6 runs from 0 through 94 and r4 is 0xEA24 + 12*RL6.",
    },
    {
        "label": "byte-indexed state writers",
        "base_register": "r6",
        "pcs": (0x02E8BC, 0x02EBF8, 0x02ED40, 0x02ED72, 0x02ED78, 0x02ED82),
        "values": range(0x100),
        "evidence": (
            "The sole stock entry zero-extends r6 from a byte and never assigns "
            "r6 again before the function epilogue."
        ),
    },
    {
        "label": "byte-indexed state word writer",
        "base_register": "r4",
        "pcs": (0x02ED48,),
        "values": range(0, 0x200, 2),
        "evidence": "The gated byte value in r6 is copied to r4 and doubled.",
    },
    {
        "label": "stock flash protocol stack frame",
        "base_register": "r0",
        "pcs": (
            0x00498E,
            0x0049C6,
            0x0049D0,
            0x0049E0,
            0x0049EA,
            0x0049EC,
            0x004ED6,
            0x005078,
            0x0050F8,
            0x005138,
            0x005178,
            0x005188,
            0x0051BA,
            0x0051C0,
            0x0051E2,
            0x0051E8,
            0x0051F8,
            0x005218,
            0x005232,
            0x005276,
            0x0053AC,
        ),
        "values": range(0xFA00, 0xFA39),
        "evidence": (
            "The normal r0 software stack is confined to 0xFA00-0xFA45; this "
            "routine saves four registers and reserves six more bytes."
        ),
    },
    {
        "label": "saved-register two-byte stack frame",
        "base_register": "r0",
        "pcs": (0x02A860, 0x02A916, 0x02A924, 0x02A938, 0x02A95E, 0x02A96C, 0x02A992),
        "values": range(0xFA00, 0xFA41),
        "evidence": (
            "The normal r0 software stack is confined to 0xFA00-0xFA45; this "
            "routine saves two registers and reserves two more bytes."
        ),
    },
    {
        "label": "two-byte stack frame",
        "base_register": "r0",
        "pcs": (0x02D8DA, 0x02D8EA, 0x02D8F4, 0x02D8FC, 0x02D90E),
        "values": range(0xFA00, 0xFA45),
        "evidence": (
            "The normal r0 software stack is confined to 0xFA00-0xFA45 and "
            "this routine reserves two local bytes."
        ),
    },
    {
        "label": "single-save two-byte stack frame",
        "base_register": "r0",
        "pcs": (0x004264, 0x004276),
        "values": range(0xFA00, 0xFA43),
        "evidence": (
            "The normal r0 software stack is confined to 0xFA00-0xFA45; this "
            "routine saves one register and reserves two local bytes."
        ),
    },
    {
        "label": "stock arithmetic stack arguments",
        "base_register": "r0",
        "pcs": (0x0000C4, 0x0000CC),
        "values": range(0xFA00, 0xFA44),
        "evidence": (
            "Both reads consume the two word arguments above the reserved r0 "
            "software stack; a valid pair cannot start above 0xFA43."
        ),
    },
    {
        "label": "DPP selector read",
        "base_register": "r5",
        "pcs": (0x00005A,),
        "values": (0, 2, 4, 6),
        "exact_values": True,
        "evidence": "r5 is the top two bits of r4 shifted into an even word index.",
    },
    {
        "label": "byte-indexed dispatch table",
        "base_register": "r7",
        "pcs": (0x000306, 0x00106A, 0x00112C),
        "values": range(0x100),
        "evidence": "Each table index is immediately established by MOVBZ from a byte.",
    },
    {
        "label": "interrupt register-bank frame",
        "base_register": "r2",
        "pcs": (0x0006EC,),
        "values": (0xFA46, 0xFA52),
        "exact_values": True,
        "evidence": (
            "Every live interrupt entry selects one of the two fixed register-bank "
            "frames before this shared epilogue."
        ),
    },
    {
        "label": "interrupt descriptor table",
        "base_register": "r2",
        "pcs": (0x000FF4, 0x000FFC, 0x00100A, 0x00102C),
        "values": (0xFA46, 0xFA52),
        "exact_values": True,
        "evidence": "Every live interrupt entry initializes r2 to 0xFA46 or 0xFA52.",
    },
    {
        "label": "interrupt descriptor table",
        "base_register": "r3",
        "pcs": (0x00103E, 0x001050),
        "values": (0xFA4E, 0xFA5A),
        "exact_values": True,
        "evidence": "Every live interrupt entry initializes r3 to 0xFA4E or 0xFA5A.",
    },
    {
        "label": "master interrupt descriptor frames",
        "base_register": "r2",
        "pcs": (0x0000EE, 0x0000F6, 0x000104, 0x000126),
        "values": (0, *range(0xFA46, 0xFA5B, 2)),
        "exact_values": True,
        "evidence": (
            "The dedicated CP=0xFB06 bank starts zero, is initialized at "
            "0x02BFDA, and every master-dispatch r2 mutation is a fixed "
            "0xFA46/0xFA52 frame base or the sole four-byte record advance."
        ),
    },
    {
        "label": "master interrupt descriptor frames",
        "base_register": "r3",
        "pcs": (0x000138, 0x000154),
        "values": (0, 0xFA4E, 0xFA5A),
        "exact_values": True,
        "evidence": (
            "The dedicated CP=0xFB06 bank starts zero and every reachable "
            "master-dispatch r3 writer installs 0xFA4E or 0xFA5A."
        ),
    },
    {
        "label": "master scheduler dispatch table",
        "base_register": "r6",
        "pcs": (0x0000E8,),
        "values": range(2, 0x102, 2),
        "evidence": (
            "The proven master scheduler state is 0x00-0x7F before the "
            "increment, then MOVBZ and doubling produce 0x0002-0x0100."
        ),
    },
    {
        "label": "master context-indexed lookup",
        "base_register": "r6",
        "pcs": (0x00011E, 0x000146, 0x00014C),
        "values": range(6),
        "evidence": (
            "MOVBZ reads the proven 0..5 context index at 0xFA80 immediately "
            "before each immutable lookup."
        ),
    },
    {
        "label": "byte-derived interrupt lookup",
        "base_register": "r6",
        "pcs": (0x001024,),
        "values": range(0, 0x200, 2),
        "evidence": "r6 is a zero-extended byte doubled before the shared dispatch.",
    },
    {
        "label": "context-indexed interrupt lookup",
        "base_register": "r6",
        "pcs": (0x001048,),
        "values": range(6),
        "evidence": "r6 is loaded from the proven 0..5 context byte with MOVBZ.",
    },
    {
        "label": "six-context interrupt table",
        "base_register": "r8",
        "pcs": (0x000EB4, 0x000EB8, 0x000EBC),
        "values": range(6),
        "evidence": "All six direct entries set r8 to one of 0, 1, 2, 3, 4, or 5.",
    },
    {
        "label": "bounded interrupt dispatch table",
        "base_register": "r2",
        "pcs": (0x0015D0,),
        "values": range(0, 0x48, 2),
        "evidence": "The immediately preceding unsigned comparison rejects r2 above 0x46.",
    },
    {
        "label": "byte-derived interrupt lookup",
        "base_register": "r2",
        "pcs": (0x0019DE,),
        "values": range(0x100),
        "evidence": "MOVBZ establishes r2 and the following byte add keeps it in 0..255.",
    },
    {
        "label": "shift-bounded interrupt lookup",
        "base_register": "r2",
        "pcs": (0x001A04,),
        "values": range(0x80),
        "evidence": "A 16-bit value is shifted right by nine immediately before the read.",
    },
    {
        "label": "fixed startup configuration selector",
        "base_register": "r12",
        "pcs": (0x004720, 0x004726),
        "values": (0, 4, 8, 12, 16),
        "exact_values": True,
        "evidence": "All five live callers pass one of the fixed selectors 0, 4, 8, 12, or 16.",
    },
    {
        "label": "bounded receive-buffer scan",
        "base_register": "r12",
        "pcs": (0x004A0A,),
        "values": range(0xE520, 0xE620),
        "evidence": "r12 starts at 0xE520 and advances by a bounded byte count.",
    },
    {
        "label": "bounded receive-buffer window",
        "base_register": "r9",
        "pcs": (0x004B22,),
        "values": range(0xE520, 0xE620),
        "evidence": "The byte-derived offset and four-byte wrapped scan stay in the receive buffer.",
    },
    {
        "label": "fixed seven-byte startup table",
        "base_register": "r4",
        "pcs": (0x004C6E,),
        "values": range(7),
        "evidence": "The local byte counter starts at zero and loops only while below seven.",
    },
    {
        "label": "fixed ten-byte startup table",
        "base_register": "r4",
        "pcs": (0x004CCC, 0x004D0C),
        "values": range(10),
        "evidence": "The local byte counter starts at zero and loops only while below ten.",
    },
    {
        "label": "DPP-selected flash scan",
        "base_register": "r7",
        "pcs": (0x004E28,),
        "values": range(0x4000),
        "evidence": "r7 is masked to 14 bits and explicitly wrapped after 0x3FFF.",
    },
    {
        "label": "stock flash-driver source copy",
        "base_register": "r9",
        "pcs": (0x00508A,),
        "values": range(0x032E, 0x042E),
        "evidence": "The fixed 256-byte copy starts at file/logical address 0x032E.",
    },
    {
        "label": "stock flash-driver source copy",
        "base_register": "r9",
        "pcs": (0x00520A,),
        "values": range(0x0230, 0x0330),
        "evidence": "The fixed 256-byte copy starts at file/logical address 0x0230.",
    },
    {
        "label": "bounded flash scan",
        "base_register": "r9",
        "pcs": (0x005146,),
        "values": range(0x4000),
        "evidence": "The scan starts from the proven E656 offset and wraps after 0x3FFF.",
    },
    {
        "label": "DPP-selected flash probe",
        "base_register": "r4",
        "pcs": (0x00532A, 0x00537A),
        "values": range(0x4000),
        "evidence": "r4 is explicitly masked with 0x3FFF immediately before each probe.",
    },
    {
        "label": "two-word stock copy",
        "base_register": "r10",
        "pcs": (0x000044,),
        "values": (0xF3CE, 0xF3D0),
        "evidence": (
            "The sole stock caller initializes r10=0xF3CE and r3=2; the loop "
            "advances r10 by two after each word."
        ),
    },
    {
        "label": "two-word stock copy",
        "base_register": "r4",
        "pcs": (0x000044,),
        "values": (0xF4FE, 0xF500),
        "evidence": (
            "The sole stock caller initializes r4=0xF4FE and r3=2; the loop "
            "post-increments r4 by two after each word."
        ),
    },
    {
        "label": "knock freeze-frame reader",
        "base_register": "r5",
        "pcs": (0x02E8B2,),
        "values": range(0xD840, 0xDB80),
        "evidence": (
            "The proven knock-task entry bounds r6 to 0..5 and the byte index "
            "at 0xE9B9 to 0..255, so D840 + (r6 << 6) + index stays in "
            "0xD840-0xDB7F."
        ),
    },
    {
        "label": "local bounded ISR slot writer",
        "base_register": "r0",
        "pcs": (0x0397A6,),
        "values": range(0xF6F7, 0xF70F),
        "evidence": "The ISR masks its byte index to 0x00-0x17 before adding 0xF6F7.",
    },
    {
        "label": "local MOVBZ ISR writer",
        "base_register": "r0",
        "pcs": (0x039996,),
        "values": range(0x100),
        "evidence": "MOVBZ from byte 0xFA5F immediately establishes r0.",
    },
    {
        "label": "local wrapped MOVBZ ISR writer",
        "base_register": "r0",
        "pcs": (0x039972,),
        "values": range(0xFD),
        "evidence": (
            "MOVBZ gives 0-255; subtracting three and adding six only on borrow "
            "maps 0-2 to 3-5 and 3-255 to 0-252."
        ),
    },
    {
        "label": "recovered local MOVBZ ring writer",
        "base_register": "r5",
        "pcs": (0x024D14,),
        "values": range(0x100),
        "evidence": "The recovered overlapping instruction follows MOVBZ r5,0xFAC2.",
    },
    {
        "label": "stock flash-driver copy loop",
        "base_register": "r7",
        "pcs": (0x00508A, 0x00520A),
        "values": range(0xE320, 0xE420),
        "evidence": "Each loop initializes r7 to 0xE320 and increments through 0xE41F.",
    },
    {
        "owner": "FUN_020a26",
        "base_register": "r9",
        "values": range(0xE52E, 0xE551, 2),
        "evidence": "Three fixed six-iteration loops advance r9 from 0xE52E by two.",
    },
    {
        "owner": "FUN_020a26",
        "base_register": "r4",
        "values": range(0xE52F, 0xE552, 2),
        "evidence": "r4 copies r9 after the first byte increment in each loop.",
    },
    {
        "owner": "FUN_020f5a",
        "base_register": "r5",
        "values": range(0x100),
        "evidence": "MOVBZ from RL5 immediately establishes r5.",
    },
    {
        "label": "stock startup byte-derived writers",
        "base_register": "r4",
        "pcs": (0x004C76, 0x004CD4, 0x004D14),
        "values": range(0x100),
        "evidence": "MOVBZ from RL4 immediately establishes r4.",
    },
    {
        "label": "knock channel word writers",
        "base_register": "r5",
        "pcs": (0x02E7D6, 0x02E7F2, 0x02E810),
        "values": range(0, 12, 2),
        "evidence": (
            "The proven knock-task entry maps the 0..5 context index through "
            "the immutable six-byte channel permutation into r6, then copies "
            "r6 << 1 into r5 immediately before each write."
        ),
    },
    {
        "owner": "FUN_0044e6",
        "base_register": "r0",
        "pcs": (0x0045D0,),
        "values": range(0xD800, 0xF7F4, 2),
        "evidence": "The exact startup table bounds the word RAM-test ranges.",
    },
    {
        "owner": "FUN_0044e6",
        "base_register": "r0",
        "pcs": (0x004658,),
        "values": range(0xD080, 0xD100),
        "evidence": "The exact startup table bounds the byte RAM-test range.",
    },
)
R0_STACK_CONTEXT_EVIDENCE = {
    "normal_context": (
        "CP=0xFC00 at file 0x0046C2; r0=0xFA46 at file 0x0046D8."
    ),
    "reserved_arena": "0xFA00-0xFA45",
    "arena_bytes": 70,
    "boundary": (
        "The stack grows downward from exclusive top 0xFA46. Internal RAM starts "
        "at 0xFA00; 0xF800-0xF9FF is unmapped, so a valid contiguous native "
        "stack cannot descend into external SRAM."
    ),
    "alternate_cp_blocks_reviewed": 22,
    "alternate_cp_direct_stack_accesses": 0,
    "call_contexts": [
        (
            "CP=0xFAE8 at file 0x038ACE: normalized call chain "
            "0x0223C0 -> 0x024740 -> 0x030950/0x030A50 has no [-r0] stores."
        ),
        (
            "CP=0xFB28 at file 0x038FC8: file 0x038FC4 first copies the old "
            "r0 into the new bank; normalized callees 0x02BA2E/0x02BA18 "
            "have no [-r0] stores."
        ),
    ],
    "saved_without_calls": (
        "CP=0xFCAE and CP=0xFCCE save the old r0 into the new bank before "
        "SCXT and make no call before POP CP."
    ),
    "status": (
        "Reserve the full 0xFA00-0xFA45 legal software-stack arena; canonical "
        "[-r0] writes are not a valid overwrite route to 0xF596."
    ),
    "limit": (
        "The exact deepest native frame remains unproven, but is unnecessary "
        "for collision-safe allocation because the full legal arena is reserved."
    ),
}

ASM_RE = re.compile(
    r"^\s*(?P<pc>[0-9a-fA-F]{4,6}):\s+"
    r"(?P<bytes>[0-9a-fA-F?]+)\s+(?P<body>.*?)\s*$"
)
DIRECT_RE = re.compile(
    r"^0x(?P<address>[0-9a-fA-F]{1,6})(?:\.(?:0x)?[0-9a-fA-F]+)?$"
)
INDIRECT_RE = re.compile(
    r"^\[(?P<pre>-)?r(?P<reg>\d+)(?P<post>\+)?"
    r"(?:\+#0x(?P<offset>[0-9a-fA-F]+))?\]$"
)
REGISTER_RE = re.compile(r"^r(?P<reg>\d+)$")
BYTE_REGISTER_RE = re.compile(r"^R[HL](?P<reg>\d+)$", re.IGNORECASE)
IMMEDIATE_RE = re.compile(r"^#0x(?P<value>[0-9a-fA-F]+)$")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def normalize_absolute_code_target(address: int) -> int:
    return address ^ 0x4000


def reference_word_values(
    image: bytes,
    logical: int,
    count: int = 256,
) -> tuple[int, ...]:
    offset = (logical & 0x3FFF) ^ 0x4000
    return tuple(sorted({
        int.from_bytes(image[offset + 2 * index : offset + 2 * index + 2], "little")
        for index in range(count)
    }))


def calibration_word(image: bytes, logical: int) -> int:
    offset = (0x10000 + logical) ^ 0x4000
    return int.from_bytes(image[offset : offset + 2], "little")


def region_for(address: int) -> str | None:
    for name, start, end in REGIONS:
        if start <= address < end:
            return name
    return None


def is_ram(address: int) -> bool:
    return region_for(address) in RAM_REGIONS


def split_operands(text: str) -> list[str]:
    operands: list[str] = []
    start = depth = 0
    for index, char in enumerate(text):
        if char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
        elif char == "," and depth == 0:
            operands.append(text[start:index].strip())
            start = index + 1
    if text.strip():
        operands.append(text[start:].strip())
    return operands


def parse_functions(
    path: Path,
) -> tuple[list[int], list[tuple[int, int, str]], dict[int, str]]:
    functions = []
    entries = {}
    for line in path.read_text(encoding="utf-8").splitlines()[1:]:
        entry, name, ranges = line.split("\t")
        entries[int(entry, 16)] = name
        for address_range in ranges.split(","):
            start_text, end_text = address_range.split("-")
            functions.append((int(start_text, 16), int(end_text, 16) + 1, name))
    functions.sort()
    return [item[0] for item in functions], functions, entries


def function_for(
    pc: int, starts: list[int], functions: list[tuple[int, int, str]]
) -> str:
    index = bisect.bisect_right(starts, pc) - 1
    if index >= 0:
        start, end, name = functions[index]
        if start <= pc < end:
            return name
    return "<unmapped>"


def parse_instruction(line: str, source: str, line_number: int) -> dict | None:
    match = ASM_RE.match(line)
    if not match:
        return None
    raw_body = match["body"]
    body = raw_body.split("->", 1)[0].strip()
    if not body:
        return None
    mnemonic, _, operand_text = body.partition(" ")
    target_match = re.search(r"->\s*([0-9a-fA-F]+)", raw_body)
    mnemonic = mnemonic.lower()
    target = int(target_match.group(1), 16) if target_match else None
    if target is not None and mnemonic in {"calla", "calls", "jmpa", "jmps"}:
        target = normalize_absolute_code_target(target)
    return {
        "pc": int(match["pc"], 16),
        "size": len(match["bytes"]) // 2,
        "source": source,
        "line": line_number,
        "instruction": body,
        "mnemonic": mnemonic,
        "operands": split_operands(operand_text),
        "target": target,
    }


def load_instructions(
    decomp: Path, functions: list[tuple[int, int, str]]
) -> tuple[list[dict], list[dict]]:
    by_pc: dict[int, dict] = {}
    coverage = []
    low_limit = max(
        LOW_CODE_END,
        max(end for start, end, _name in functions if start < 0x8000),
    )
    high_limit = max(
        end for start, end, _name in functions if 0x20000 <= start < 0x40000
    )
    sources = [
        (filename, decomp / filename) for filename in ASM_FILES
    ] + [
        (RECOVERED_ASM.name, RECOVERED_ASM),
        (VERIFIED_OVERLAP_ASM.name, VERIFIED_OVERLAP_ASM),
        (CONSERVATIVE_ROOT_ASM.name, CONSERVATIVE_ROOT_ASM),
        (REACHABILITY_EDGE_ASM.name, REACHABILITY_EDGE_ASM),
        (LOWER_COMPUTED_TARGET_ASM.name, LOWER_COMPUTED_TARGET_ASM),
    ]
    for filename, path in sources:
        instructions = []
        for line_number, line in enumerate(
            path.read_text(encoding="utf-8", errors="replace").splitlines(), 1
        ):
            instruction = parse_instruction(line, filename, line_number)
            if not instruction:
                continue
            pc = instruction["pc"]
            in_code_extent = (
                filename == "d_first32k.asm"
                and pc < low_limit
                or filename != "d_first32k.asm"
                and pc < high_limit
            )
            if in_code_extent:
                instructions.append(instruction)
                by_pc.setdefault(instruction["pc"], instruction)
        coverage.append(
            {
                "source": filename,
                "instruction_count": len(instructions),
                "start": f"0x{min(item['pc'] for item in instructions):06X}",
                "end": f"0x{max(item['pc'] for item in instructions):06X}",
            }
        )
    return sorted(by_pc.values(), key=lambda item: item["pc"]), coverage


def width_for(mnemonic: str) -> int:
    if mnemonic in {"bset", "bclr", "jb", "jnb", "bmov", "bmovn"}:
        return 2
    if mnemonic.startswith(
        ("movb", "cmpb", "addb", "subb", "andb", "orb", "xorb")
    ):
        return 1
    return 2


def access_kind(mnemonic: str, operand_index: int) -> str | None:
    if mnemonic.startswith(("call", "jmp", "ret")):
        return None
    if mnemonic.startswith(("mov", "bmov")):
        return "write" if operand_index == 0 else "read"
    if mnemonic.startswith(("cmp", "test", "push", "jb", "jnb")):
        return "read"
    if mnemonic.startswith("pop"):
        return "write"
    if mnemonic in {"bset", "bclr"}:
        return "read/write"
    return "read/write" if operand_index == 0 else "read"


def direct_address(operand: str) -> int | None:
    match = DIRECT_RE.match(operand)
    return int(match["address"], 16) if match else None


def immediate(operand: str) -> int | None:
    match = IMMEDIATE_RE.match(operand)
    return int(match["value"], 16) if match else None


def exact_register_update(
    values: dict[int, int], mnemonic: str, operands: list[str]
) -> None:
    if not operands:
        return
    destination = REGISTER_RE.match(operands[0])
    if not destination:
        byte_destination = BYTE_REGISTER_RE.match(operands[0])
        if byte_destination:
            values.pop(int(byte_destination["reg"]), None)
        return
    register = int(destination["reg"])
    source_immediate = immediate(operands[1]) if len(operands) > 1 else None
    source_register = REGISTER_RE.match(operands[1]) if len(operands) > 1 else None

    if mnemonic == "mov":
        if source_immediate is not None:
            values[register] = source_immediate & 0xFFFF
        elif source_register and int(source_register["reg"]) in values:
            values[register] = values[int(source_register["reg"])]
        else:
            values.pop(register, None)
        return

    if register in values and source_immediate is not None:
        value = values[register]
        operations = {
            "add": lambda: value + source_immediate,
            "sub": lambda: value - source_immediate,
            "and": lambda: value & source_immediate,
            "or": lambda: value | source_immediate,
            "xor": lambda: value ^ source_immediate,
            "shl": lambda: value << source_immediate,
            "shr": lambda: value >> source_immediate,
        }
        if mnemonic in operations:
            values[register] = operations[mnemonic]() & 0xFFFF
            return

    if not mnemonic.startswith(("cmp", "test")):
        values.pop(register, None)


def instruction_successors(item: dict) -> tuple[list[int], bool]:
    mnemonic = item["mnemonic"]
    next_pc = item["pc"] + item["size"]
    successors = []
    unknown_transfer = False

    if mnemonic in {"retp", "rets", "reti"}:
        pass
    elif mnemonic in {"jmpi", "calli"}:
        unknown_transfer = True
        if mnemonic == "calli" or "cc_UC" not in item["operands"]:
            successors.append(next_pc)
    elif mnemonic.startswith("call") or mnemonic in {"pcall", "trap"}:
        if item["target"] is not None:
            successors.append(item["target"])
        successors.append(next_pc)
    elif mnemonic in {"jmpr", "jmpa", "jmps"}:
        if item["target"] is not None:
            successors.append(item["target"])
        if mnemonic != "jmps" and "cc_UC" not in item["operands"]:
            successors.append(next_pc)
    elif mnemonic in {"jb", "jnb", "jbc", "jnbs"}:
        if item["target"] is not None:
            successors.append(item["target"])
        successors.append(next_pc)
    else:
        successors.append(next_pc)

    return successors, unknown_transfer


def direct_successors(item: dict, by_pc: dict[int, dict]) -> tuple[list[int], bool]:
    successors, unknown_transfer = instruction_successors(item)
    return [pc for pc in successors if pc in by_pc], unknown_transfer


def direct_control_flow_analysis(
    instructions: list[dict],
    root_owners: dict[int, set[str]],
    named_owners: dict[int, str],
    table_targets: tuple[dict, ...] | list[dict] = (),
) -> tuple[set[int], list[str], dict[int, set[str]]]:
    by_pc = {item["pc"]: item for item in instructions}
    table_by_site: dict[int, list[tuple[int, str]]] = {}
    for target in table_targets:
        table_by_site.setdefault(int(target["site"], 16), []).append(
            (
                int(target["file_target"], 16),
                f"<computed table {target['table']}>",
            )
        )
    owners: dict[int, set[str]] = {}
    unknown_transfers = []
    pending = [
        (pc, labels) for pc, labels in root_owners.items() if pc in by_pc
    ]

    while pending:
        pc, labels = pending.pop()
        merged = owners.get(pc, set()) | labels
        if merged == owners.get(pc):
            continue
        owners[pc] = merged
        item = by_pc[pc]
        successors, unknown_transfer = direct_successors(item, by_pc)
        if unknown_transfer and pc in table_by_site:
            unknown_transfer = False
            for target, label in table_by_site[pc]:
                if target in by_pc:
                    pending.append((target, {label}))
        if unknown_transfer:
            unknown_transfers.append(f"0x{pc:06X} {item['instruction']}")
        for successor in successors:
            successor_labels = (
                {named_owners[successor]}
                if successor in named_owners
                else merged
            )
            pending.append((successor, successor_labels))

    return set(owners), sorted(set(unknown_transfers)), owners


def conservative_lower_roots(
    instructions: list[dict],
    image: bytes,
) -> tuple[dict[int, set[str]], dict]:
    by_pc = {item["pc"]: item for item in instructions}
    patterns = []
    roots: dict[int, set[str]] = {}
    rejected_unaligned = 0
    for file_offset in range(0, len(image) - 3, 2):
        opcode = image[file_offset]
        if opcode not in {0xDA, 0xFA}:  # CALLS / JMPS
            continue
        cpu_target = (
            image[file_offset + 1] << 16
            | int.from_bytes(image[file_offset + 2 : file_offset + 4], "little")
        )
        file_target = normalize_absolute_code_target(cpu_target)
        if not 0 <= file_target < LOW_CODE_END:
            continue
        if file_target & 1:
            rejected_unaligned += 1
            continue
        roots.setdefault(file_target, set()).add(
            "<conservative absolute transfer>"
        )
        patterns.append(
            {
                "file_offset": f"0x{file_offset:06X}",
                "opcode": f"0x{opcode:02X}",
                "cpu_target": f"0x{cpu_target:06X}",
                "file_target": f"0x{file_target:06X}",
            }
        )

    missing_roots = sorted(root for root in roots if root not in by_pc)
    if missing_roots:
        raise RuntimeError(
            "conservative lower roots are not decoded: "
            + ", ".join(f"0x{pc:06X}" for pc in missing_roots)
        )
    return roots, {
        "status": "proven for the stock image",
        "full_image_scanned": "0x000000-0x03FFFF at every even byte",
        "calls_jmps_patterns": len(patterns),
        "unique_decoded_lower_targets": len(roots),
        "rejected_unaligned_targets": rejected_unaligned,
        "missing_lower_roots": [],
        "root_patterns": patterns,
        "proof": (
            "Every possible even-aligned CALLS/JMPS byte pattern in the exact "
            "256 KiB image is scanned. Every aligned target inside the immutable "
            "lower code/thunk extent 0x000000-0x0062FF is decoded."
        ),
    }


def conservative_high_segment_reachability(
    instructions: list[dict],
    table_targets: list[dict],
    image: bytes,
) -> tuple[set[int], dict]:
    by_pc = {item["pc"]: item for item in instructions}
    table_by_site: dict[int, list[int]] = {}
    for item in table_targets:
        table_by_site.setdefault(int(item["site"], 16), []).append(
            int(item["file_target"], 16)
        )

    lower_patterns = []
    pattern_roots = set()
    for file_offset in range(0, 0x20000 - 3, 2):
        opcode = image[file_offset]
        if opcode not in {0xDA, 0xFA}:  # CALLS / JMPS
            continue
        cpu_target = (
            image[file_offset + 1] << 16
            | int.from_bytes(image[file_offset + 2 : file_offset + 4], "little")
        )
        file_target = normalize_absolute_code_target(cpu_target)
        if not 0x20000 <= file_target < 0x40000:
            continue
        pattern_roots.add(file_target)
        lower_patterns.append(
            {
                "file_offset": f"0x{file_offset:06X}",
                "opcode": f"0x{opcode:02X}",
                "cpu_target": f"0x{cpu_target:06X}",
                "file_target": f"0x{file_target:06X}",
            }
        )

    roots = pattern_roots | {
        target
        for targets in table_by_site.values()
        for target in targets
        if 0x20000 <= target < 0x40000
    }
    missing_roots = sorted(root for root in roots if root not in by_pc)
    if missing_roots:
        raise RuntimeError(
            "conservative high-segment roots are not decoded: "
            + ", ".join(f"0x{pc:06X}" for pc in missing_roots)
        )

    reachable = set()
    pending = list(roots)
    missing_edges = []
    unknown_transfers = []
    while pending:
        pc = pending.pop()
        if pc in reachable:
            continue
        reachable.add(pc)
        instruction = by_pc[pc]
        successors, unknown = instruction_successors(instruction)
        if instruction["mnemonic"] == "jmpi":
            targets = table_by_site.get(pc)
            if targets is None:
                unknown_transfers.append(
                    f"0x{pc:06X} {instruction['instruction']}"
                )
            else:
                successors.extend(targets)
                unknown = False
        if unknown:
            unknown_transfers.append(
                f"0x{pc:06X} {instruction['instruction']}"
            )
        for successor in successors:
            if not 0x20000 <= successor < 0x40000:
                continue
            if successor not in by_pc:
                missing_edges.append(
                    {
                        "pc": f"0x{pc:06X}",
                        "instruction": instruction["instruction"],
                        "target": f"0x{successor:06X}",
                    }
                )
                continue
            pending.append(successor)

    if missing_edges or unknown_transfers:
        raise RuntimeError(
            "conservative high-segment control flow is incomplete: "
            + json.dumps(
                {
                    "missing_edges": missing_edges,
                    "unknown_transfers": sorted(set(unknown_transfers)),
                },
                sort_keys=True,
            )
        )
    high_instruction_count = sum(
        0x20000 <= pc < 0x40000 for pc in by_pc
    )
    return reachable, {
        "status": "proven for the stock image",
        "lower_flash_scanned": "0x000000-0x01FFFF at every even byte",
        "lower_calls_jmps_patterns": len(lower_patterns),
        "lower_unique_high_targets": len(pattern_roots),
        "decoded_dispatch_targets": sum(
            len(targets) for targets in table_by_site.values()
        ),
        "decoded_high_instructions": high_instruction_count,
        "reachable_high_instructions": len(reachable),
        "unreachable_high_instructions": high_instruction_count - len(reachable),
        "missing_high_roots": [],
        "missing_high_edges": [],
        "unknown_high_indirect_transfers": [],
        "root_patterns": lower_patterns,
        "proof": (
            "Every possible even-aligned CALLS/JMPS byte pattern in the lower "
            "128 KiB is rooted, including false positives in data. Segment-2/3 "
            "JMPI tables are decoded completely and those segments contain no CALLI."
        ),
        "limit": (
            "Stock image and immutable dispatch data only; corrupted stack/dispatch "
            "state or modified firmware is outside this reachability gate."
        ),
    }


def access_record(
    instruction: dict,
    function: str,
    address: int,
    width: int,
    kind: str,
    method: str,
    operand: str,
) -> dict:
    return {
        "address": f"0x{address:04X}",
        "width": width,
        "kind": kind,
        "method": method,
        "pc": f"0x{instruction['pc']:06X}",
        "function": function,
        "source": instruction["source"],
        "line": instruction["line"],
        "operand": operand,
        "instruction": instruction["instruction"],
    }


def logger_claims(path: Path) -> list[dict]:
    claims = []
    for parameter in json.loads(path.read_text(encoding="utf-8")):
        address = int(parameter["addr"], 16)
        if address < 0x20:  # ADC mux indexes, not RAM addresses.
            continue
        width = 2 if parameter.get("stype") in {"int16", "uint16"} else 1
        claims.append(
            {
                "address": f"0x{address:04X}",
                "width": width,
                "id": parameter["id"],
                "name": parameter["name"],
            }
        )
    return claims


def covered_bytes(accesses: list[dict], start: int, end: int) -> set[int]:
    covered = set()
    for access in accesses:
        address = int(access["address"], 16)
        covered.update(range(max(address, start), min(address + access["width"], end)))
    return covered


def overlaps_hint(item: dict, start: int, end: int) -> bool:
    hint = item.get("offset_hint")
    return hint is not None and start <= int(hint, 16) < end


def claim_bounds(claim: dict) -> tuple[int, int]:
    start, end = claim["range"].split("-")
    return int(start, 16), int(end, 16) + 1


def implicit_ownership(controls: list[dict]) -> list[dict]:
    claims = [
        {
            "range": "0xFA00-0xFA45",
            "owner": "r0 software-stack arena",
            "lifetime": "normal/shared-r0 firmware contexts",
            "evidence": (
                "r0=0xFA46 at d_first32k.asm 0x0046D8; descending [-r0] "
                "traffic; exact-part internal RAM begins at 0xFA00"
            ),
        },
        {
            "range": "0xFB64-0xFBFF",
            "owner": "CPU system stack",
            "lifetime": "normal firmware runtime",
            "evidence": "d_first32k.asm 0x004468-0x004470; C166 manual pp. 3-4/B-4",
        },
        {
            "range": "0xFDE0-0xFDFF",
            "owner": "C166 PEC source/destination pointer workspace",
            "lifetime": "hardware event transfers",
            "evidence": "d_ph.asm PEC pointer writes; C166 PEC architecture",
        },
        {
            "range": "0xF6F7-0xF70E",
            "owner": "SSC receive ring buffer",
            "lifetime": "normal SSC receive interrupts",
            "evidence": "d_ph.asm 0x039790-0x0397A6; index is masked to 0x1F and accepted through 0x17",
        },
        *PEC_RAM_CLAIMS,
    ]
    banks: dict[int, list[str]] = {}
    for item in controls:
        base = immediate(item["source_operand"])
        if item["register"] == "CP" and base is not None and 0xFA00 <= base <= 0xFDE0:
            banks.setdefault(base, []).append(item["pc"])
    for base, pcs in sorted(banks.items()):
        claims.append(
            {
                "range": f"0x{base:04X}-0x{base + 31:04X}",
                "owner": "CPU general-purpose register bank",
                "lifetime": f"while CP=0x{base:04X}",
                "evidence": ", ".join(pcs),
            }
        )
    return claims


def candidate_gaps(
    ordinary_accesses: list[dict],
    implicit: list[dict],
    unresolved: list[dict],
    minimum_size: int = 16,
    region_name: str = "external SRAM",
) -> list[dict]:
    claimed = {
        address
        for item in ordinary_accesses
        for address in range(
            int(item["address"], 16), int(item["address"], 16) + item["width"]
        )
    }
    for claim in implicit:
        start, end = claim_bounds(claim)
        claimed.update(range(start, end))

    gaps = []
    for _name, region_start, region_end in REGIONS:
        if region_for(region_start) != region_name:
            continue
        cursor = region_start
        while cursor < region_end:
            while cursor < region_end and cursor in claimed:
                cursor += 1
            start = cursor
            while cursor < region_end and cursor not in claimed:
                cursor += 1
            if cursor - start < minimum_size:
                continue

            boundaries = {start, cursor}
            for claim in LIFETIME_CLAIMS:
                if start < claim["start"] < cursor:
                    boundaries.add(claim["start"])
                if start < claim["end"] < cursor:
                    boundaries.add(claim["end"])
            points = sorted(boundaries)
            for gap_start, gap_end in zip(points, points[1:]):
                if gap_end - gap_start < minimum_size:
                    continue
                hints = [
                    item
                    for item in unresolved
                    if overlaps_hint(item, gap_start, gap_end)
                ]
                lifetime = [
                    item
                    for item in LIFETIME_CLAIMS
                    if item["start"] < gap_end and gap_start < item["end"]
                ]
                if lifetime:
                    status = "transiently claimed"
                elif unresolved:
                    status = "unsafe: unbounded 16-bit pointers"
                else:
                    status = "unproven: runtime evidence outstanding"
                gaps.append(
                    {
                        "range": f"0x{gap_start:04X}-0x{gap_end - 1:04X}",
                        "bytes": gap_end - gap_start,
                        "status": status,
                        "unresolved_offset_hints": len(hints),
                        "lifetime_claims": [item["owner"] for item in lifetime],
                    }
                )
    return sorted(gaps, key=lambda item: (-item["bytes"], item["range"]))


def ownership_intervals(
    ordinary_accesses: list[dict],
    logger_accesses: list[dict],
    implicit: list[dict],
    certified_ranges: list[dict],
    start: int = 0xFA00,
    end: int = 0xFE00,
) -> tuple[list[dict], dict]:
    normal = [set() for _ in range(end - start)]
    transient = [set() for _ in range(end - start)]

    for address in covered_bytes(ordinary_accesses, start, end):
        normal[address - start].add("stock static access")
    for address in covered_bytes(logger_accesses, start, end):
        normal[address - start].add("SHINDE1 logger claim")
    for claim in implicit:
        claim_start, claim_end = claim_bounds(claim)
        for address in range(max(start, claim_start), min(end, claim_end)):
            normal[address - start].add(claim["owner"])
    for claim in LIFETIME_CLAIMS:
        for address in range(max(start, claim["start"]), min(end, claim["end"])):
            transient[address - start].add(claim["owner"])

    certified = set()
    for item in certified_ranges:
        range_start, range_end = claim_bounds(item)
        certified.update(range(range_start, range_end))

    def signature(address: int) -> tuple:
        index = address - start
        return (
            tuple(sorted(normal[index])),
            tuple(sorted(transient[index])),
            address in certified,
        )

    intervals = []
    cursor = start
    while cursor < end:
        interval_start = cursor
        current = signature(cursor)
        cursor += 1
        while cursor < end and signature(cursor) == current:
            cursor += 1
        normal_owners, transient_owners, is_certified = current
        if normal_owners:
            status = "owned/reserved"
        elif is_certified:
            status = "conditionally available after startup"
        else:
            status = "transient-owned; not certified"
        intervals.append(
            {
                "range": f"0x{interval_start:04X}-0x{cursor - 1:04X}",
                "bytes": cursor - interval_start,
                "status": status,
                "normal_runtime_owners": list(normal_owners),
                "transient_owners": list(transient_owners),
            }
        )

    normal_claimed = sum(bool(owners) for owners in normal)
    return intervals, {
        "bytes": end - start,
        "normal_runtime_claimed_bytes": normal_claimed,
        "normal_runtime_unclaimed_bytes": end - start - normal_claimed,
        "certified_bytes": len(certified),
        "transient_only_excluded_bytes": end - start - normal_claimed - len(certified),
    }


def boot_footprint() -> dict:
    fixture = json.loads(BOOT_FOOTPRINT.read_text(encoding="utf-8"))
    return {
        "source": fixture["source"],
        "entry": fixture["entry"],
        "stop": fixture["stop"],
        "steps": fixture["stop_step"],
        "snapshot_ranges": [
            f"0x{int(item['lo'], 16):04X}-0x{int(item['hi'], 16) - 1:04X}"
            for item in fixture["ranges"]
        ],
        "non_ff_bytes_at_stop": sum(
            byte != 0xFF
            for item in fixture["ranges"]
            for byte in bytes.fromhex(item["hex"])
        ),
        "excluded_range": (
            f"0x{int(fixture['exclude']['lo'], 16):04X}-"
            f"0x{int(fixture['exclude']['hi'], 16) - 1:04X}"
        ),
        "excluded_reason": fixture["exclude"]["reason"],
    }


def stock_entry_gate(
    instructions: list[dict],
    table_targets: list[dict],
    start: int,
    end: int,
    allowed_entries: set[int],
    live_sources: set[int] | None = None,
) -> dict:
    by_pc = {item["pc"]: item for item in instructions}
    external_edges = []
    for item in instructions:
        if live_sources is not None and item["pc"] not in live_sources:
            continue
        if start <= item["pc"] < end:
            continue
        successors, _unknown = direct_successors(item, by_pc)
        for target in successors:
            if start <= target < end:
                external_edges.append(
                    {
                        "pc": f"0x{item['pc']:06X}",
                        "instruction": item["instruction"],
                        "file_target": f"0x{target:06X}",
                    }
                )
    computed_inside = [
        item
        for item in table_targets
        if start <= int(item["file_target"], 16) < end
        and not start <= int(item["site"], 16) < end
    ]
    internal_computed = [
        item
        for item in table_targets
        if start <= int(item["file_target"], 16) < end
        and start <= int(item["site"], 16) < end
    ]
    segment2_calli = [
        item
        for item in instructions
        if item["mnemonic"] == "calli" and 0x20000 <= item["pc"] < 0x30000
    ]
    proven = (
        not segment2_calli
        and external_edges
        and all(
            int(item["file_target"], 16) in allowed_entries
            for item in external_edges + computed_inside
        )
    )
    return {
        "status": "proven for the stock image" if proven else "not proven",
        "function_file_envelope": f"0x{start:06X}-0x{end - 1:06X}",
        "allowed_entries": [f"0x{entry:06X}" for entry in sorted(allowed_entries)],
        "computed_targets_inside_envelope": computed_inside,
        "internal_computed_targets": internal_computed,
        "segment2_calli_sites": [f"0x{item['pc']:06X}" for item in segment2_calli],
        "external_direct_entries": external_edges,
        "limit": (
            "Static stock-image control flow only; corrupted return state or "
            "modified dispatch data is outside this gate."
        ),
    }


def constant_immediate_call_arguments(
    gate: dict,
    register: str,
    instructions: list[dict],
    branch_targets: set[int],
) -> tuple[tuple[int, ...], list[dict]]:
    positions = {item["pc"]: index for index, item in enumerate(instructions)}
    values = set()
    arguments = []
    pattern = re.compile(rf"^mov {register},#0x([0-9a-f]+)$")
    for edge in gate["external_direct_entries"]:
        call_pc = int(edge["pc"], 16)
        position = positions[call_pc]
        intervening = []
        found = False
        for candidate in reversed(instructions[max(0, position - 10) : position]):
            if candidate["mnemonic"] in {"calla", "calls", "jmpa", "jmps", "jmpi", "jmpr", "rets"}:
                break
            intervening.append(candidate["pc"])
            if not candidate["operands"] or candidate["operands"][0] != register:
                continue
            match = pattern.fullmatch(candidate["instruction"])
            if not match or any(pc in branch_targets for pc in intervening[:-1]):
                break
            values.add(int(match.group(1), 16))
            arguments.append(
                {
                    "call_pc": f"0x{call_pc:06X}",
                    "assignment_pc": f"0x{candidate['pc']:06X}",
                    "value": f"0x{int(match.group(1), 16):04X}",
                }
            )
            found = True
            break
        if not found:
            raise RuntimeError(
                f"constant {register} call argument proof failed at 0x{call_pc:06X}"
            )
    return tuple(sorted(values)), arguments


def analyze(decomp: Path, reference: Path) -> dict:
    starts, functions, _function_entries = parse_functions(FUNCTION_BODIES)
    instructions, coverage = load_instructions(decomp, functions)
    named_owners = {
        item["pc"]: owner
        for item in instructions
        if (
            owner := function_for(item["pc"], starts, functions)
        ) != "<unmapped>"
    }
    vector_roots = {
        item["pc"]
        for item in instructions
        if 0x4000 <= item["pc"] < 0x4200 and item["mnemonic"] == "jmps"
    }
    root_owners: dict[int, set[str]] = {}
    for pc in vector_roots:
        root_owners.setdefault(pc, set()).add("<interrupt vectors>")
    root_owners.setdefault(0x4430, set()).add("<startup>")
    reachable_pcs, unknown_control_transfers, control_flow_owners = (
        direct_control_flow_analysis(
            instructions,
            root_owners,
            named_owners,
        )
    )
    branch_targets = {item["target"] for item in instructions if item["target"] is not None}
    values: dict[int, int] = {}
    direct, resolved, unresolved, controls = [], [], [], []
    current_function = None

    for instruction in instructions:
        function = function_for(instruction["pc"], starts, functions)
        if function != current_function or instruction["pc"] in branch_targets:
            values.clear()
            current_function = function

        width = width_for(instruction["mnemonic"])
        postincrements = []
        for index, operand in enumerate(instruction["operands"]):
            kind = access_kind(instruction["mnemonic"], index)
            if kind is None:
                continue

            address = direct_address(operand)
            if address is not None and region_for(address):
                direct.append(
                    access_record(
                        instruction, function, address, width, kind, "direct", operand
                    )
                )
                continue

            indirect = INDIRECT_RE.match(operand)
            if not indirect:
                continue
            register = int(indirect["reg"])
            if indirect["pre"] and register in values:
                values[register] = (values[register] - width) & 0xFFFF
            offset = int(indirect["offset"], 16) if indirect["offset"] else 0
            if register in values:
                address = (values[register] + offset) & 0xFFFF
                if region_for(address):
                    resolved.append(
                        access_record(
                            instruction,
                            function,
                            address,
                            width,
                            kind,
                            "exact straight-line indirect",
                            operand,
                        )
                    )
            else:
                unresolved.append(
                    {
                        "pc": f"0x{instruction['pc']:06X}",
                        "function": function,
                        "source": instruction["source"],
                        "line": instruction["line"],
                        "kind": kind,
                        "width": width,
                        "base_register": f"r{register}",
                        "offset_hint": f"0x{offset:04X}" if offset else None,
                        "operand": operand,
                        "instruction": instruction["instruction"],
                        "direct_control_flow_reachable": (
                            instruction["pc"] in reachable_pcs
                        ),
                    }
                )
            if indirect["post"]:
                postincrements.append(register)

        operands = instruction["operands"]
        if (
            instruction["mnemonic"] in {"mov", "scxt"}
            and len(operands) > 1
            and direct_address(operands[0]) in CONTROL_REGS
        ):
            destination = direct_address(operands[0])
            controls.append(
                {
                    "register": CONTROL_REGS[destination],
                    "pc": f"0x{instruction['pc']:06X}",
                    "source_operand": operands[1],
                    "instruction": instruction["instruction"],
                }
            )

        exact_register_update(values, instruction["mnemonic"], operands)
        for register in postincrements:
            if register in values:
                values[register] = (values[register] + width) & 0xFFFF
        if instruction["mnemonic"].startswith("call"):
            values.clear()
        if instruction["mnemonic"] in {"jmpr", "jmpa", "jmps", "jmpi"}:
            values.clear()

    logger = logger_claims(decomp / "logger_ram_shinde1.json")
    image = reference.read_bytes()
    boot_fixture = json.loads(BOOT_FOOTPRINT.read_text(encoding="utf-8"))
    boot_ram_test_words = {
        logical: int.from_bytes(
            image[0x6300 + logical - 0xA300 : 0x6302 + logical - 0xA300],
            "little",
        )
        for logical in BOOT_RAM_TEST_WORDS
    }
    if boot_ram_test_words != BOOT_RAM_TEST_WORDS or image[0x6000] == 0xA8:
        raise RuntimeError("reference boot RAM-test table no longer matches proof")

    cp_controls = [item for item in controls if item["register"] == "CP"]
    immediate_cp_bases = {
        base
        for item in cp_controls
        if (base := immediate(item["source_operand"])) is not None
    }
    expected_immediate_cp_bases = {
        0xFAE4,
        0xFAE6,
        0xFAE8,
        0xFAF2,
        0xFAF8,
        0xFB00,
        0xFB06,
        0xFB20,
        0xFB28,
        0xFB48,
        0xFB4C,
        0xFB50,
        0xFB52,
        0xFB56,
        0xFB5C,
        0xFC00,
        0xFC42,
        0xFCAE,
        0xFCCE,
    }
    startup_dynamic_cp = {
        "0x0044C0": boot_ram_test_words[0xA314] + 0x16,
        "0x004534": boot_ram_test_words[0xA314],
    }
    non_immediate_cp = {
        item["pc"]: item["source_operand"]
        for item in cp_controls
        if immediate(item["source_operand"]) is None
    }
    if (
        immediate_cp_bases != expected_immediate_cp_bases
        or non_immediate_cp != {"0x0044C0": "r0", "0x004534": "0xa314"}
        or startup_dynamic_cp != {"0x0044C0": 0xFA16, "0x004534": 0xFA00}
    ):
        raise RuntimeError("CP bank inventory no longer matches the exact-image proof")
    cp_restore_sites = [
        item
        for item in instructions
        if item["mnemonic"] == "pop"
        and item["operands"]
        and direct_address(item["operands"][0]) == 0xFE10
    ]
    known_cp_bases = sorted(
        immediate_cp_bases | set(startup_dynamic_cp.values())
    )
    context_bank_invariant = {
        "status": "proven for the exact stock image",
        "explicit_assignment_sites": len(cp_controls),
        "pop_restore_sites": len(cp_restore_sites),
        "startup_table_word_0xA314": (
            f"0x{boot_ram_test_words[0xA314]:04X}"
        ),
        "startup_dynamic_assignments": {
            pc: f"0x{base:04X}" for pc, base in startup_dynamic_cp.items()
        },
        "known_cp_bases": [f"0x{base:04X}" for base in known_cp_bases],
        "reserved_bank_ranges": [
            f"0x{base:04X}-0x{base + 31:04X}" for base in known_cp_bases
        ],
        "proof": (
            "Every explicit stock CP write is either one of the enumerated "
            "immediate bases or the startup-table-derived 0xFA16/0xFA00 pair. "
            "POP CP sites only restore a CP previously saved by SCXT or an "
            "interrupt context, so the known-bank set is closed."
        ),
        "limit": "Any patch that writes CP requires a new bank-ownership audit.",
    }

    stack_controls = [item for item in controls if item["register"] != "CP"]
    stack_assignments = {
        (item["register"], item["pc"], item["source_operand"])
        for item in stack_controls
    }
    expected_stack_assignments = {
        ("STKOV", "0x004468", "#0xfb64"),
        ("STKUN", "0x00446C", "#0xfc00"),
        ("SP", "0x004470", "#0xfc00"),
    }
    if stack_assignments != expected_stack_assignments:
        raise RuntimeError("system-stack bounds no longer match the exact-image proof")
    system_stack_invariant = {
        "status": "proven for the exact stock image",
        "normal_runtime_envelope": "0xFB64-0xFBFF",
        "assignments": stack_controls,
        "proof": (
            "Startup sets STKOV=0xFB64, STKUN=0xFC00, and SP=0xFC00; "
            "there are no other explicit stock writes to these registers."
        ),
        "soft_bsl": (
            "The current agent resets SP to 0xFC00 and inherits the same bounds."
        ),
        "hardware_bsl": (
            "Built-in BSL is separate: CP=0xFA00, SP=0xFA40, with the "
            "documented stack shown at 0xFA20-0xFA3F."
        ),
        "limit": (
            "Any patch or replacement loader that changes SP, STKOV, or STKUN "
            "requires a new stack audit."
        ),
    }

    entry_start, entry_end = FUN_02CD98_ENVELOPE
    segment2_jmpi = [
        item
        for item in instructions
        if item["mnemonic"] == "jmpi" and 0x20000 <= item["pc"] < 0x30000
    ]
    segment3_jmpi = [
        item
        for item in instructions
        if item["mnemonic"] == "jmpi" and 0x30000 <= item["pc"] < 0x40000
    ]
    segment2_calli = [
        item
        for item in instructions
        if item["mnemonic"] == "calli" and 0x20000 <= item["pc"] < 0x30000
    ]
    segment3_calli = [
        item
        for item in instructions
        if item["mnemonic"] == "calli" and 0x30000 <= item["pc"] < 0x40000
    ]
    table_targets = []
    for site, logical, count in JMPI_TABLES:
        file_offset = (logical & 0x3FFF) ^ 0x4000
        for index in range(count):
            offset = file_offset + 2 * index
            cpu_ip = int.from_bytes(image[offset : offset + 2], "little")
            table_targets.append(
                {
                    "site": f"0x{site:06X}",
                    "table": f"0x{logical:04X}",
                    "index": index,
                    "cpu_ip": f"0x{cpu_ip:04X}",
                    "file_target": (
                        f"0x{normalize_absolute_code_target((site & 0x30000) | cpu_ip):06X}"
                    ),
                }
            )
    lower_indirect_sites = {
        item["pc"]
        for item in instructions
        if item["pc"] < 0x20000
        and item["mnemonic"] in {"calli", "jmpi"}
    }
    expected_lower_indirect_sites = {
        *(site for site, _logical, _count in LOWER_COMPUTED_TABLES),
    }
    lower_table_targets = []
    for site, logical, count in LOWER_COMPUTED_TABLES:
        file_offset = (logical & 0x3FFF) ^ 0x4000
        for index in range(count):
            offset = file_offset + 2 * index
            cpu_ip = int.from_bytes(image[offset : offset + 2], "little")
            lower_table_targets.append(
                {
                    "site": f"0x{site:06X}",
                    "table": f"0x{logical:04X}",
                    "index": index,
                    "cpu_ip": f"0x{cpu_ip:04X}",
                    "file_target": (
                        f"0x{normalize_absolute_code_target(cpu_ip):06X}"
                    ),
                }
            )
    if lower_indirect_sites != expected_lower_indirect_sites:
        raise RuntimeError(
            "lower computed-control proof changed: "
            + json.dumps(sorted(f"0x{pc:06X}" for pc in lower_indirect_sites))
        )
    table_targets.extend(lower_table_targets)
    instructions_by_pc = {item["pc"]: item for item in instructions}
    missing_lower_targets = sorted(
        {
            int(item["file_target"], 16)
            for item in lower_table_targets
        }
        - set(instructions_by_pc)
    )
    if missing_lower_targets:
        raise RuntimeError(
            "lower computed targets are not decoded: "
            + ", ".join(f"0x{pc:06X}" for pc in missing_lower_targets)
        )
    high_reachable_pcs, high_reachability_gate = (
        conservative_high_segment_reachability(
            instructions,
            table_targets,
            image,
        )
    )
    root_owners, lower_reachability_gate = conservative_lower_roots(
        instructions,
        image,
    )
    for pc in vector_roots:
        root_owners.setdefault(pc, set()).add("<interrupt vectors>")
    root_owners.setdefault(0x4430, set()).add("<startup>")
    for item in high_reachability_gate["root_patterns"]:
        root_owners.setdefault(
            int(item["file_target"], 16), set()
        ).add("<conservative high-segment transfer>")
    for item in table_targets:
        target = int(item["file_target"], 16)
        if 0x20000 <= target < 0x40000:
            root_owners.setdefault(target, set()).add(
                f"<computed table {item['table']}>"
            )
    reachable_pcs, unknown_control_transfers, control_flow_owners = (
        direct_control_flow_analysis(
            instructions,
            root_owners,
            named_owners,
            table_targets,
        )
    )
    if unknown_control_transfers:
        raise RuntimeError(
            "unresolved stock computed control transfers remain: "
            + json.dumps(unknown_control_transfers)
        )
    for accesses in (direct, resolved, unresolved):
        for item in accesses:
            item["direct_control_flow_reachable"] = (
                int(item["pc"], 16) in reachable_pcs
            )
    master_table_targets = [
        item for item in lower_table_targets if item["table"] == "0xAE1E"
    ]
    master_reachable_pcs, master_unknown, _master_owners = (
        direct_control_flow_analysis(
            instructions,
            {0x0000DC: {"<master scheduler entry>"}} | {
                int(item["file_target"], 16): {"<master scheduler table>"}
                for item in master_table_targets
            },
            named_owners,
            lower_table_targets,
        )
    )
    master_mutations = {
        register: {
            item["pc"]: item["instruction"]
            for item in instructions
            if item["pc"] in master_reachable_pcs
            and item["pc"] < 0x1000
            and item["operands"]
            and item["operands"][0].lower() == register
            and not item["mnemonic"].startswith(("cmp", "test"))
        }
        for register in ("rl0", "r2", "r3")
    }
    expected_master_mutations = {
        "rl0": {
            0x0000E2: "addb RL0,#0x1",
            0x0006D0: "movb RL0,#0x38",
            0x000748: "movb RL0,#0x39",
            0x0007E0: "movb RL0,#0x3c",
            0x000B1C: "movb RL0,#0x74",
            0x000B94: "movb RL0,#0x75",
            0x000BB2: "movb RL0,#0x1",
            0x000C36: "movb RL0,#0x3a",
            0x000C3E: "movb RL0,#0x76",
            0x000CB2: "movb RL0,#0x1",
            0x000CC6: "movb RL0,#0x3d",
            0x000E14: "movb RL0,#0x7c",
            0x000E22: "movb RL0,#0x7d",
            0x000E38: "movb RL0,#0x7d",
            0x000E42: "movb RL0,#0x7e",
        },
        "r2": {
            0x000116: "add r2,#0x4",
            0x0003B6: "mov r2,#0xfa46",
            0x000470: "mov r2,#0xfa52",
            0x000526: "mov r2,#0xfa46",
            0x000804: "mov r2,#0xfa52",
            0x0008BE: "mov r2,#0xfa46",
            0x000974: "mov r2,#0xfa52",
        },
        "r3": {
            0x0003BA: "mov r3,#0xfa4e",
            0x000474: "mov r3,#0xfa5a",
            0x00052A: "mov r3,#0xfa4e",
            0x000808: "mov r3,#0xfa5a",
            0x0008C2: "mov r3,#0xfa4e",
            0x000978: "mov r3,#0xfa5a",
        },
    }
    master_bank_range = next(
        item
        for item in boot_fixture["ranges"]
        if int(item["lo"], 16) <= 0xFB06
        and 0xFB0E <= int(item["hi"], 16)
    )
    master_bank_boot = bytes.fromhex(master_bank_range["hex"])[
        0xFB06 - int(master_bank_range["lo"], 16) :
        0xFB0E - int(master_bank_range["lo"], 16)
    ]
    if (
        master_unknown
        or len(master_table_targets) != 129
        or master_table_targets[-1]["index"] != 128
        or master_table_targets[-1]["file_target"] != "0x000E42"
        or master_bank_boot != bytes(8)
        or master_mutations != expected_master_mutations
        or instructions_by_pc[0x02BFDA]["instruction"] != "scxt 0xfe10,#0xfb06"
        or instructions_by_pc[0x02BFE0]["instruction"] != "movb RL0,#0x7f"
        or instructions_by_pc[0x02BFE4]["instruction"] != "mov r2,#0xfa46"
        or instructions_by_pc[0x02BFE8]["instruction"] != "mov r1,#0xfab8"
    ):
        raise RuntimeError(
            "master scheduler context-bank proof changed: "
            + json.dumps(
                {
                    "unknown": master_unknown,
                    "table_entries": len(master_table_targets),
                    "boot": master_bank_boot.hex(),
                    "mutations": master_mutations,
                },
                sort_keys=True,
            )
        )
    master_dispatch_invariant = {
        "status": "proven for the stock image",
        "context_bank": "0xFB06-0xFB0D",
        "boot_bytes": master_bank_boot.hex(),
        "table": "0xAE1E",
        "table_entries": len(master_table_targets),
        "selector_before_increment": "0x00-0x7F",
        "selector_after_increment": "0x01-0x80",
        "r2_frame_envelope": "0xFA46-0xFA5A",
        "r3_values": ["0xFA4E", "0xFA5A"],
        "proof": (
            "The dedicated bank boots zero; the high ISR initializer installs "
            "RL0=0x7F and r2=0xFA46. Exhaustive master-graph mutation checks "
            "show only fixed RL0 states through 0x7E, fixed r2/r3 frame bases, "
            "and one four-byte r2 record advance."
        ),
    }
    lower_dispatch_coverage = {
        "status": "proven for the stock image",
        "indirect_sites": len(lower_indirect_sites),
        "decoded_table_sites": len(LOWER_COMPUTED_TABLES),
        "decoded_table_entries": len(lower_table_targets),
        "reachable_table_sites": sorted(
            f"0x{site:06X}"
            for site in expected_lower_indirect_sites & reachable_pcs
        ),
        "unreachable_table_sites": sorted(
            f"0x{site:06X}"
            for site in expected_lower_indirect_sites - reachable_pcs
        ),
        "master_scheduler": master_dispatch_invariant,
        "proof": (
            "Every lower-segment CALLI/JMPI site and immutable table is decoded "
            "in full. Table targets enter the stock graph only when their "
            "dispatch site is reachable from a conservative root."
        ),
    }
    segment2_dispatch_proven = (
        {item["pc"] for item in segment2_jmpi}
        == {item[0] for item in SEGMENT2_JMPI_TABLES}
        and not segment2_calli
    )
    segment3_dispatch_proven = (
        {item["pc"] for item in segment3_jmpi}
        == {item[0] for item in SEGMENT3_JMPI_TABLES}
        and not segment3_calli
    )
    dispatch_table_coverage = {
        "segment2": {
            "status": "proven" if segment2_dispatch_proven else "not proven",
            "jmpi_sites": len(segment2_jmpi),
            "tables": len(SEGMENT2_JMPI_TABLES),
            "entries": sum(item[2] for item in SEGMENT2_JMPI_TABLES),
            "calli_sites": len(segment2_calli),
        },
        "segment3": {
            "status": "proven" if segment3_dispatch_proven else "not proven",
            "jmpi_sites": len(segment3_jmpi),
            "tables": len(SEGMENT3_JMPI_TABLES),
            "entries": sum(item[2] for item in SEGMENT3_JMPI_TABLES),
            "calli_sites": len(segment3_calli),
        },
    }
    nonexecutable_low_pcs: set[int] = set()
    nonexecutable_low_gate = []
    gate_instructions = {item["pc"]: item for item in instructions}
    for claim in PROVEN_NONEXECUTABLE_LOW_RANGES:
        start, end = claim["start"], claim["end"]
        decoded_pcs = {
            item["pc"] for item in instructions if start <= item["pc"] < end
        }
        reachable_inside = sorted(decoded_pcs & reachable_pcs)
        computed_inside = [
            item
            for item in table_targets
            if start <= int(item["file_target"], 16) < end
        ]
        if (
            reachable_inside
            or computed_inside
            or gate_instructions[0x0041FC]["instruction"] != "jmps 0x0022fc"
            or gate_instructions[0x004230]["instruction"] != "mov [-r0],r9"
        ):
            raise RuntimeError("non-executable low-range proof no longer holds")
        nonexecutable_low_pcs.update(decoded_pcs)
        nonexecutable_low_gate.append(
            {
                "range": f"0x{start:06X}-0x{end - 1:06X}",
                "status": "proven non-executable in the stock image",
                "decoded_instruction_starts": len(decoded_pcs),
                "reachable_instruction_starts": [],
                "computed_entries": [],
                "evidence": claim["evidence"],
                "limit": (
                    "Stock control flow and decoded dispatch tables only; the "
                    "copied flash-driver source begins at the exclusive end."
                ),
            }
        )
    entry_gate = stock_entry_gate(
        instructions,
        table_targets,
        entry_start,
        entry_end,
        {entry_start},
    )
    entry_gate_proven = (
        segment2_dispatch_proven
        and entry_gate["status"] == "proven for the stock image"
    )
    entry_gate.update(
        {
            "status": (
                "proven for the stock image" if entry_gate_proven else "not proven"
            ),
            "required_wrapper_entry": f"0x{entry_start:06X}",
            "segment2_jmpi_sites": len(segment2_jmpi),
            "expected_rom_tables": len(SEGMENT2_JMPI_TABLES),
            "rom_table_entries": sum(item[2] for item in SEGMENT2_JMPI_TABLES),
        }
    )
    object_pointer_entry_gates = {
        "FUN_0280b4": stock_entry_gate(
            instructions, table_targets, 0x0280B4, 0x0282EE, {0x0280B4}
        ),
        "FUN_02830a": stock_entry_gate(
            instructions, table_targets, 0x02830A, 0x0284FE, {0x02830A}
        ),
        "FUN_02853c": stock_entry_gate(
            instructions, table_targets, 0x02853C, 0x0287F0, {0x02853C}
        ),
        "FUN_02f6f0": stock_entry_gate(
            instructions,
            table_targets,
            0x02F6F0,
            0x02FD22,
            {0x02F6F0, 0x02FA64, 0x02FB86},
        ),
    }
    diagnostic_reader_entry_gate = stock_entry_gate(
        instructions,
        table_targets,
        0x027A8E,
        0x027BBC,
        {0x027A8E},
    )
    if (
        not entry_gate_proven
        or diagnostic_reader_entry_gate["status"] != "proven for the stock image"
        or {
            int(edge["pc"], 16)
            for edge in diagnostic_reader_entry_gate["external_direct_entries"]
        }
        != {0x0272A0}
    ):
        raise RuntimeError(
            "diagnostic-reader entry-path proof no longer holds: "
            + json.dumps(diagnostic_reader_entry_gate, sort_keys=True)
        )
    diagnostic_reader_entry_gate["pointer_proof"] = (
        "The only entry initializes r7=0xE523; the byte-sized descriptor count "
        "permits at most 255 iterations and two output bytes per iteration."
    )
    paired_state_loop_entry_gate = stock_entry_gate(
        instructions,
        table_targets,
        0x02AD4A,
        0x02AF82,
        {0x02AD4A, 0x02AF6E},
    )
    if (
        not entry_gate_proven
        or paired_state_loop_entry_gate["status"] != "proven for the stock image"
        or {
            int(edge["pc"], 16)
            for edge in paired_state_loop_entry_gate["external_direct_entries"]
        }
        != {0x02AD46}
    ):
        raise RuntimeError(
            "paired-state loop entry-path proof no longer holds: "
            + json.dumps(paired_state_loop_entry_gate, sort_keys=True)
        )
    paired_state_loop_entry_gate["pointer_proof"] = (
        "The preheader and loop-back paths select r8=0xF182 or 0xF18A "
        "before any r8 write."
    )
    six_byte_record_entry_gate = stock_entry_gate(
        instructions,
        table_targets,
        0x0352AC,
        0x035312,
        {0x0352AC},
    )
    if (
        not entry_gate_proven
        or six_byte_record_entry_gate["status"] != "proven for the stock image"
        or {
            int(edge["pc"], 16)
            for edge in six_byte_record_entry_gate["external_direct_entries"]
        }
        != {0x035CE0, 0x035CEC}
    ):
        raise RuntimeError(
            "six-byte record entry-path proof no longer holds: "
            + json.dumps(six_byte_record_entry_gate, sort_keys=True)
        )
    six_byte_record_entry_gate["pointer_proof"] = (
        "The sole entry zero-extends the low byte of r12, multiplies it by six, "
        "and adds 0xF63E."
    )
    instructions_by_pc = {item["pc"]: item for item in instructions}
    stock_entry_sources = {
        item["pc"]
        for item in instructions
        if (
            item["pc"] < 0x20000
            and item["pc"] not in nonexecutable_low_pcs
        )
        or item["pc"] in high_reachable_pcs
    }
    record_update_entry_gates = {
        "countdown": stock_entry_gate(
            instructions,
            table_targets,
            0x023956,
            0x023B1C,
            {0x023956},
            stock_entry_sources,
        ),
        "state": stock_entry_gate(
            instructions,
            table_targets,
            0x023B1C,
            0x023C36,
            {0x023B1C},
            stock_entry_sources,
        ),
        "paired_countdown": stock_entry_gate(
            instructions,
            table_targets,
            0x023D20,
            0x023EE8,
            {0x023D20},
            stock_entry_sources,
        ),
        "status_helper": stock_entry_gate(
            instructions,
            table_targets,
            0x023710,
            0x0237E6,
            {0x023710},
            stock_entry_sources,
        ),
    }
    if any(
        gate["status"] != "proven for the stock image"
        for gate in record_update_entry_gates.values()
    ):
        raise RuntimeError(
            "record updater entry-path proof no longer holds: "
            + json.dumps(record_update_entry_gates, sort_keys=True)
        )
    derived_record_arguments = {
        name: constant_immediate_call_arguments(
            record_update_entry_gates[name],
            "r12",
            instructions,
            branch_targets,
        )
        for name in ("countdown", "state", "paired_countdown")
    }
    derived_record_bases = {
        name: result[0] for name, result in derived_record_arguments.items()
    }
    expected_record_bases = {
        "countdown": COUNTDOWN_RECORD_BASES,
        "state": STATE_RECORD_BASES,
        "paired_countdown": PAIRED_COUNTDOWN_RECORD_BASES,
    }
    if derived_record_bases != expected_record_bases:
        raise RuntimeError(
            "record updater constant argument proof no longer holds: "
            + json.dumps(derived_record_arguments, sort_keys=True)
        )
    derived_descriptor_arguments = {
        name: constant_immediate_call_arguments(
            record_update_entry_gates[name],
            "r13",
            instructions,
            branch_targets,
        )
        for name in ("countdown", "state", "paired_countdown")
    }
    derived_descriptor_bases = {
        name: values
        for name, (values, _arguments) in derived_descriptor_arguments.items()
    }
    descriptor_pointer_values = {
        name: tuple(sorted({
            reference_word_values(image, base + offset, 1)[0]
            for base in bases
            for offset in (8, 10, 12, 14)
        }))
        for name, bases in derived_descriptor_bases.items()
    }
    derived_descriptor_ids = tuple(sorted({
        (address - 0xA5CC) // 16
        for addresses, _arguments in derived_descriptor_arguments.values()
        for address in addresses
    }))
    if any(
        address < 0xA5CC
        or address > 0xABAC
        or (address - 0xA5CC) % 16
        for addresses, _arguments in derived_descriptor_arguments.values()
        for address in addresses
    ):
        raise RuntimeError(
            "record descriptor argument proof changed: "
            + json.dumps(derived_descriptor_arguments, sort_keys=True)
        )
    lookup_offset = (0xAC21 & 0x3FFF) ^ 0x4000
    record_lookup = image[lookup_offset : lookup_offset + 0x100]
    record_lookup_outputs = tuple(sorted({
        record_lookup.index(record_id, 0, 0xFF)
        for record_id in range(0x5F)
        if image[((0xA5CD + 16 * record_id) & 0x3FFF) ^ 0x4000] & 0x60
        and record_id in record_lookup[:0xFF]
    }))
    record_lookup_base_values = tuple(6 * value for value in record_lookup_outputs)
    if (
        record_lookup_outputs != tuple(range(10))
        or any(record_id >= 0x5F for record_id in derived_descriptor_ids)
    ):
        raise RuntimeError(
            "record identifier lookup proof changed: "
            + json.dumps(
                {
                    "descriptor_ids": derived_descriptor_ids,
                    "lookup_outputs": record_lookup_outputs,
                }
            )
        )
    record_identifier_invariant = {
        "status": "proven for the stock image",
        "record_ids": "0x00-0x5E",
        "active_lookup_outputs": [
            f"0x{value:02X}" for value in record_lookup_outputs
        ],
        "proof": (
            "All live record-updater callers pass an aligned descriptor in the "
            "95-entry A5CC table. EE92/EEF1 contain only those IDs; the stock "
            "AC21 lookup maps every active ID to output 0..9 or 0xFF."
        ),
    }
    record_comparator_entry_gate = stock_entry_gate(
        instructions,
        table_targets,
        0x03520C,
        0x03526A,
        {0x03520C},
        stock_entry_sources,
    )
    record_comparator_call_sites = {
        int(edge["pc"], 16)
        for edge in record_comparator_entry_gate["external_direct_entries"]
    }
    record_comparator_r8_values = tuple(
        0xF63E + 6 * value for value in record_lookup_outputs
    )
    if (
        record_comparator_entry_gate["status"] != "proven for the stock image"
        or record_comparator_call_sites != {0x0358E4, 0x03595E}
        or any(
            instructions_by_pc[pc - 0x16]["instruction"]
            != "mov r12,#0xf556"
            or instructions_by_pc[pc - 6]["instruction"]
            != "mov r13,#0xf63e"
            or instructions_by_pc[pc - 2]["instruction"]
            != "add r13,r5"
            for pc in record_comparator_call_sites
        )
    ):
        raise RuntimeError(
            "record-comparator entry proof changed: "
            + json.dumps(record_comparator_entry_gate, sort_keys=True)
        )
    record_comparator_entry_gate["pointer_proof"] = (
        "Both live callers pass r12=0xF556 and derive r13 as 0xF63E plus "
        "six times the proven 0..9 native record-lookup output."
    )
    low_record_compare_entry_gate = stock_entry_gate(
        instructions,
        table_targets,
        0x005B4C,
        0x005BCC,
        {0x005B4C},
        stock_entry_sources,
    )
    low_record_extended_entry_gate = stock_entry_gate(
        instructions,
        table_targets,
        0x005BCC,
        0x005C14,
        {0x005BCC},
        stock_entry_sources,
    )
    if (
        low_record_compare_entry_gate["status"] != "proven for the stock image"
        or low_record_extended_entry_gate["status"] != "proven for the stock image"
        or {
            int(edge["pc"], 16)
            for edge in low_record_compare_entry_gate["external_direct_entries"]
        }
        != {0x0052AA, 0x005BD8}
        or {
            int(edge["pc"], 16)
            for edge in low_record_extended_entry_gate["external_direct_entries"]
        }
        != {0x0052D6}
        or instructions_by_pc[0x0052A2]["instruction"] != "mov r12,#0xe537"
        or instructions_by_pc[0x0052A6]["instruction"] != "mov r13,#0xe54b"
        or instructions_by_pc[0x0052CE]["instruction"] != "mov r12,#0xe54b"
        or instructions_by_pc[0x0052D2]["instruction"] != "mov r13,#0xe55f"
        or instructions_by_pc[0x005BD0]["instruction"] != "mov r8,r13"
        or instructions_by_pc[0x005BD2]["instruction"] != "mov r9,r12"
        or instructions_by_pc[0x005BD4]["instruction"] != "mov r12,r9"
        or instructions_by_pc[0x005BD6]["instruction"] != "mov r13,r8"
    ):
        raise RuntimeError("low record-comparison entry proof changed")
    low_record_compare_entry_gate["pointer_proof"] = (
        "The direct caller compares 0xE537 with 0xE54B. The only forwarding "
        "caller is itself gated to 0xE54B and 0xE55F."
    )
    low_record_extended_entry_gate["pointer_proof"] = (
        "Its sole stock caller passes r12=0xE54B and r13=0xE55F."
    )
    helper_call_sites = {
        int(edge["pc"], 16)
        for edge in record_update_entry_gates["status_helper"][
            "external_direct_entries"
        ]
    }
    helper_forwarding_calls = {
        0x023ACC,
        0x023B02,
        0x023BF8,
        0x023C28,
        0x023E8C,
        0x023EC0,
    }
    helper_fixed_calls = {0x023D18, 0x03F04E, 0x03F1A6, 0x03F258}
    if helper_call_sites != helper_forwarding_calls | helper_fixed_calls or any(
        instructions_by_pc[pc - 4]["instruction"] not in {
            "mov r12,r8",
            "mov r12,r9",
        }
        for pc in helper_forwarding_calls
    ) or any(
        instructions_by_pc[pc - 8]["instruction"] != "mov r12,#0xec2a"
        for pc in helper_fixed_calls
    ):
        raise RuntimeError(
            "record-status helper caller set changed: "
            + json.dumps(
                {
                    "call_sites": sorted(f"{pc:06X}" for pc in helper_call_sites),
                    "r12_sources": {
                        f"{pc:06X}": instructions_by_pc[
                            pc - (4 if pc in helper_forwarding_calls else 8)
                        ]["instruction"]
                        for pc in helper_call_sites
                    },
                },
                sort_keys=True,
            )
        )
    record_update_entry_gates["status_helper"]["pointer_proof"] = (
        "Six calls forward the gated caller record through r12; four pass the "
        "fixed 0xEC2A record."
    )
    for name in ("countdown", "state", "paired_countdown"):
        record_update_entry_gates[name]["constant_arguments"] = (
            derived_record_arguments[name][1]
        )
        record_update_entry_gates[name]["pointer_proof"] = (
            "Every live stock caller passes one exact immediate r12 record base; "
            f"{len(derived_record_bases[name])} unique bases are proven."
        )
    word_copy_entry_gate = stock_entry_gate(
        instructions,
        table_targets,
        0x000042,
        0x00004E,
        {0x000044},
    )
    if (
        word_copy_entry_gate["status"] != "proven for the stock image"
        or {
            int(edge["pc"], 16)
            for edge in word_copy_entry_gate["external_direct_entries"]
        }
        != {0x022884}
        or instructions_by_pc[0x02287A]["instruction"] != "mov r4,#0xf4fe"
        or instructions_by_pc[0x02287E]["instruction"] != "mov r10,#0xf3ce"
        or instructions_by_pc[0x022882]["instruction"] != "mov r3,#0x2"
    ):
        raise RuntimeError(
            "two-word copy entry-path proof no longer holds: "
            + json.dumps(word_copy_entry_gate, sort_keys=True)
        )
    word_copy_entry_gate["pointer_proof"] = (
        "The sole caller sets r4=0xF4FE, r10=0xF3CE, and r3=2; the loop "
        "increments both pointers by two per word."
    )
    crc_update_entry_gate = stock_entry_gate(
        instructions,
        table_targets,
        0x03744E,
        0x037496,
        {0x03744E},
        stock_entry_sources,
    )
    crc_destinations, crc_arguments = constant_immediate_call_arguments(
        crc_update_entry_gate,
        "r14",
        instructions,
        branch_targets,
    )
    if (
        crc_update_entry_gate["status"] != "proven for the stock image"
        or crc_destinations != CRC_UPDATE_DESTINATIONS
    ):
        raise RuntimeError(
            "CRC accumulator entry-path proof no longer holds: "
            + json.dumps(crc_update_entry_gate, sort_keys=True)
        )
    crc_update_entry_gate["constant_arguments"] = crc_arguments
    crc_update_entry_gate["pointer_proof"] = (
        "Every live stock caller passes 0xF482, 0xF484, or 0xF486 in r14."
    )
    checksum_table_words = tuple(
        calibration_word(image, 0x5E00 + 2 * index)
        for index in range(16)
    )
    checksum_start_values = tuple(
        value - 2 for value in checksum_table_words
    )
    checksum_next_values = tuple(
        calibration_word(image, value)
        for value in checksum_start_values
    )
    checksum_expected_bases = (
        calibration_word(image, 0x5DFE),
        *checksum_next_values,
    )
    f2a6_writers = {
        item["pc"]
        for item in direct
        if int(item["address"], 16) == 0xF2A6
        and item["kind"] in {"write", "read/write"}
    }
    if (
        f2a6_writers != {"0x02DE42"}
        or instructions_by_pc[0x02DE3E]["instruction"] != "mov r4,#0x5e00"
        or instructions_by_pc[0x03760A]["instruction"] != "cmp r4,#0x10"
        or checksum_table_words[0] != 2
        or max(
            *checksum_start_values,
            *checksum_next_values,
            *checksum_expected_bases,
        ) >= 0xC000
    ):
        raise RuntimeError("background checksum pointer-chain proof changed")
    checksum_scan_invariant = {
        "status": "proven for the stock image",
        "table": "0x5E00",
        "entries": len(checksum_table_words),
        "start_values": [
            f"0x{value:04X}" for value in checksum_start_values
        ],
        "next_values": [
            f"0x{value:04X}" for value in checksum_next_values
        ],
        "proof": (
            "The sole F2A6 writer installs immutable table 0x5E00. The "
            "16-entry scan and every decoded start/next pointer stay below "
            "the DPP3 RAM domain."
        ),
    }
    object_reset_entry_gate = stock_entry_gate(
        instructions,
        table_targets,
        0x02DA2E,
        0x02DA4A,
        {0x02DA2E},
        stock_entry_sources,
    )
    object_reset_bases, object_reset_arguments = constant_immediate_call_arguments(
        object_reset_entry_gate,
        "r12",
        instructions,
        branch_targets,
    )
    if (
        object_reset_entry_gate["status"] != "proven for the stock image"
        or object_reset_bases != (0xF018, 0xF0C4)
    ):
        raise RuntimeError(
            "object-reset entry-path proof is incomplete: "
            + json.dumps(object_reset_entry_gate, sort_keys=True)
        )
    object_reset_entry_gate["constant_arguments"] = object_reset_arguments
    object_reset_entry_gate["pointer_proof"] = (
        "The only live callers pass 0xF018 or 0xF0C4 in r12."
    )
    optional_result_entry_gate = stock_entry_gate(
        instructions,
        table_targets,
        0x02A538,
        0x02A5B2,
        {0x02A538},
        stock_entry_sources,
    )
    optional_result_bases, optional_result_arguments = (
        constant_immediate_call_arguments(
            optional_result_entry_gate,
            "r13",
            instructions,
            branch_targets,
        )
    )
    if (
        optional_result_entry_gate["status"] != "proven for the stock image"
        or optional_result_bases != (0xE8EB,)
    ):
        raise RuntimeError(
            "optional-result entry-path proof is incomplete: "
            + json.dumps(optional_result_entry_gate, sort_keys=True)
        )
    optional_result_entry_gate["constant_arguments"] = optional_result_arguments
    optional_result_entry_gate["pointer_proof"] = (
        "The sole live caller passes 0xE8EB in r13; entry copies it to r9."
    )
    byte_search_entry_gate = stock_entry_gate(
        instructions,
        table_targets,
        0x005ADE,
        0x005B4C,
        {0x005ADE},
        stock_entry_sources,
    )
    byte_search_bases, byte_search_base_arguments = (
        constant_immediate_call_arguments(
            byte_search_entry_gate,
            "r14",
            instructions,
            branch_targets,
        )
    )
    byte_search_lengths, byte_search_length_arguments = (
        constant_immediate_call_arguments(
            byte_search_entry_gate,
            "r13",
            instructions,
            branch_targets,
        )
    )
    byte_search_pairs = {
        int(base["call_pc"], 16): (
            int(base["value"], 16),
            int(length["value"], 16),
        )
        for base, length in zip(
            byte_search_base_arguments,
            byte_search_length_arguments,
        )
    }
    byte_search_source_pairs = {
        0x005288: (0x9CEF, 8),
        0x00529A: (0xA001, 12),
        0x0052C6: (reference_word_values(image, 0xA376, 1)[0], 16),
        0x005A98: (0xA001, 12),
        0x005AA6: (0xA025, 7),
        0x005AB4: (0xA03A, 7),
    }
    byte_search_source_addresses = tuple(sorted({
        address
        for base, length in byte_search_source_pairs.values()
        for address in range(base, base + length)
    }))
    if (
        byte_search_entry_gate["status"] != "proven for the stock image"
        or byte_search_bases
        != (0xE537, 0xE54B, 0xE55F, 0xE754, 0xE760, 0xE767)
        or byte_search_lengths != (7, 8, 12, 16)
        or byte_search_pairs
        != {
            0x005288: (0xE537, 8),
            0x00529A: (0xE54B, 12),
            0x0052C6: (0xE55F, 16),
            0x005A98: (0xE754, 12),
            0x005AA6: (0xE760, 7),
            0x005AB4: (0xE767, 7),
        }
    ):
        raise RuntimeError(
            "byte-search output proof is incomplete: "
            + json.dumps(byte_search_entry_gate, sort_keys=True)
        )
    byte_search_entry_gate["base_arguments"] = byte_search_base_arguments
    byte_search_entry_gate["source_pairs"] = {
        f"0x{pc:06X}": [f"0x{base:04X}", length]
        for pc, (base, length) in byte_search_source_pairs.items()
    }
    byte_search_entry_gate["length_arguments"] = byte_search_length_arguments
    byte_search_entry_gate["pointer_proof"] = (
        "All six live callers pass a fixed output base and a length of 7, 8, "
        "12, or 16 bytes."
    )
    low_serial_entry_gate = stock_entry_gate(
        instructions,
        table_targets,
        0x0058C8,
        0x0059B0,
        {0x0058C8},
        stock_entry_sources,
    )
    low_serial_retry_entry_gate = stock_entry_gate(
        instructions,
        table_targets,
        0x0059B0,
        0x005A02,
        {0x0059B0},
        stock_entry_sources,
    )
    low_retry_bases, low_retry_base_arguments = (
        constant_immediate_call_arguments(
            low_serial_retry_entry_gate,
            "r12",
            instructions,
            branch_targets,
        )
    )
    low_retry_lengths, low_retry_length_arguments = (
        constant_immediate_call_arguments(
            low_serial_retry_entry_gate,
            "r14",
            instructions,
            branch_targets,
        )
    )
    if (
        low_serial_entry_gate["status"] != "proven for the stock image"
        or low_serial_retry_entry_gate["status"] != "proven for the stock image"
        or {
            int(edge["pc"], 16)
            for edge in low_serial_entry_gate["external_direct_entries"]
        }
        != {0x0059D2, 0x005AD8}
        or low_retry_bases != (0xE74E, 0xE754)
        or low_retry_lengths != (4, 0x1A)
        or instructions_by_pc[0x0058D0]["instruction"] != "mov r7,r12"
        or instructions_by_pc[0x0058D2]["instruction"] != "mov r8,r14"
        or instructions_by_pc[0x005924]["instruction"] != "movb [r7],0xe65c"
        or instructions_by_pc[0x00596C]["instruction"] != "add r7,#0x1"
        or instructions_by_pc[0x005986]["instruction"] != "cmp r8,#0x0"
        or instructions_by_pc[0x0059B8]["instruction"] != "mov r7,r14"
        or instructions_by_pc[0x0059BC]["instruction"] != "mov r9,r12"
        or instructions_by_pc[0x0059CA]["instruction"] != "mov r12,r9"
        or instructions_by_pc[0x0059CE]["instruction"] != "movbz r14,RL7"
        or instructions_by_pc[0x005ACA]["instruction"] != "mov r12,#0xe74e"
        or instructions_by_pc[0x005AD2]["instruction"] != "mov r14,#0x22"
    ):
        raise RuntimeError(
            "low serial-buffer pointer proof is incomplete: "
            + json.dumps(
                {
                    "entry": low_serial_entry_gate,
                    "retry": low_serial_retry_entry_gate,
                },
                sort_keys=True,
            )
        )
    low_serial_entry_gate["pointer_proof"] = (
        "The direct 34-byte transfer spans 0xE74E-0xE76F; both retry-wrapper "
        "transfers are strict subranges."
    )
    low_serial_retry_entry_gate["base_arguments"] = low_retry_base_arguments
    low_serial_retry_entry_gate["length_arguments"] = low_retry_length_arguments
    low_serial_destinations = tuple(range(0xE74E, 0xE770))

    high_serial_entry_gate = stock_entry_gate(
        instructions,
        table_targets,
        0x021896,
        0x021964,
        {0x021896},
        stock_entry_sources,
    )
    high_serial_retry_entry_gate = stock_entry_gate(
        instructions,
        table_targets,
        0x021964,
        0x0219B2,
        {0x021964},
        stock_entry_sources,
    )
    high_retry_call_sites = {
        int(edge["pc"], 16)
        for edge in high_serial_retry_entry_gate["external_direct_entries"]
    }
    high_retry_bases, high_retry_base_arguments = (
        constant_immediate_call_arguments(
            high_serial_retry_entry_gate,
            "r12",
            instructions,
            branch_targets,
        )
    )
    high_retry_lengths, high_retry_length_arguments = (
        constant_immediate_call_arguments(
            high_serial_retry_entry_gate,
            "r14",
            instructions,
            branch_targets,
        )
    )
    high_retry_pairs = {
        int(base["call_pc"], 16): (
            int(base["value"], 16),
            int(length["value"], 16),
        )
        for base, length in zip(
            high_retry_base_arguments,
            high_retry_length_arguments,
        )
    }
    if (
        high_serial_entry_gate["status"] != "proven for the stock image"
        or high_serial_retry_entry_gate["status"] != "proven for the stock image"
        or {
            int(edge["pc"], 16)
            for edge in high_serial_entry_gate["external_direct_entries"]
        }
        != {0x021986}
        or high_retry_call_sites
        != {
            0x02240C,
            0x022426,
            0x022458,
            0x022486,
            0x0224AC,
            0x0224E2,
            0x022572,
            0x0225A6,
            0x0225EC,
            0x0226B2,
            0x02274C,
            0x022776,
            0x0227BE,
            0x022874,
            0x022914,
            0x022B42,
            0x022B66,
            0x022BAA,
            0x022BBA,
            0x022BFA,
            0x022C1A,
        }
        or any(
            not (0xF2A8 <= base and base + length <= 0xF482)
            for base, length in high_retry_pairs.values()
        )
        or instructions_by_pc[0x02189E]["instruction"] != "mov r7,r12"
        or instructions_by_pc[0x0218A0]["instruction"] != "mov r8,r14"
        or instructions_by_pc[0x0218EA]["instruction"] != "movb [r7],0xe65c"
        or instructions_by_pc[0x021920]["instruction"] != "add r7,#0x1"
        or instructions_by_pc[0x02193A]["instruction"] != "cmp r8,#0x0"
        or instructions_by_pc[0x02196C]["instruction"] != "mov r7,r14"
        or instructions_by_pc[0x021970]["instruction"] != "mov r9,r12"
        or instructions_by_pc[0x02197E]["instruction"] != "mov r12,r9"
        or instructions_by_pc[0x021982]["instruction"] != "movbz r14,RL7"
    ):
        raise RuntimeError(
            "high serial-buffer pointer proof is incomplete: "
            + json.dumps(
                {
                    "entry": high_serial_entry_gate,
                    "retry": high_serial_retry_entry_gate,
                    "fixed_pairs": {
                        f"0x{pc:06X}": [f"0x{base:04X}", length]
                        for pc, (base, length) in high_retry_pairs.items()
                    },
                },
                sort_keys=True,
            )
        )
    high_serial_entry_gate["pointer_proof"] = (
        "The only live stock path is the retry wrapper; all 21 live callers "
        "pass fixed buffers contained by 0xF2A8-0xF481."
    )
    high_serial_retry_entry_gate["fixed_base_arguments"] = (
        high_retry_base_arguments
    )
    high_serial_retry_entry_gate["fixed_length_arguments"] = (
        high_retry_length_arguments
    )
    high_serial_retry_entry_gate["fixed_bases"] = [
        f"0x{value:04X}" for value in high_retry_bases
    ]
    high_serial_retry_entry_gate["fixed_lengths"] = list(high_retry_lengths)
    high_serial_destinations = tuple(range(0xF2A8, 0xF482))
    calibration_parser_specs = {
        "byte_axis_a": (0x0308AC, 0x0308FC),
        "byte_axis_b": (0x0308FC, 0x030950),
        "word_axis_a": (0x030950, 0x03099E),
        "word_axis_b": (0x03099E, 0x0309F0),
        "byte_lookup": (0x030A34, 0x030A50),
        "word_lookup": (0x030A50, 0x030A6A),
        "byte_bilinear": (0x030A6A, 0x030AB8),
        "word_bilinear": (0x030AB8, 0x030B02),
        "byte_axis_simple_a": (0x030B02, 0x030B28),
        "byte_axis_simple_b": (0x030B28, 0x030B52),
    }
    calibration_parser_gates = {
        name: stock_entry_gate(
            instructions,
            table_targets,
            start,
            end,
            {start},
            stock_entry_sources,
        )
        for name, (start, end) in calibration_parser_specs.items()
    }
    calibration_parser_arguments = {
        name: constant_immediate_call_arguments(
            gate,
            "r12",
            instructions,
            branch_targets,
        )
        for name, gate in calibration_parser_gates.items()
    }
    calibration_parser_bases = tuple(sorted({
        value
        for values, _arguments in calibration_parser_arguments.values()
        for value in values
    }))
    calibration_axis_descriptor_bases = tuple(sorted({
        value
        for name in (
            "byte_axis_a",
            "byte_axis_b",
            "word_axis_a",
            "word_axis_b",
            "byte_axis_simple_a",
            "byte_axis_simple_b",
        )
        for value in calibration_parser_arguments[name][0]
    }))
    calibration_axis_bases = tuple(sorted({
        calibration_word(image, value)
        for value in calibration_axis_descriptor_bases
    }))
    if (
        any(
            gate["status"] != "proven for the stock image"
            for gate in calibration_parser_gates.values()
        )
        or not calibration_parser_bases
        or max(calibration_parser_bases) >= 0xC000
        or max(calibration_axis_bases) >= 0xC000
    ):
        raise RuntimeError(
            "calibration parser entry proof changed: "
            + json.dumps(calibration_parser_gates, sort_keys=True)
        )
    calibration_parser_invariant = {
        "status": "proven for the stock image",
        "descriptor_bases": [
            f"0x{value:04X}" for value in calibration_parser_bases
        ],
        "axis_bases": [
            f"0x{value:04X}" for value in calibration_axis_bases
        ],
        "proof": (
            "Every stock caller passes an immediate logical address below "
            "0xC000; the referenced calibration axes and lookup tables remain "
            "in the non-DPP3 logical domain."
        ),
    }
    calibration_axis_lengths = tuple(sorted({
        image[((0x10000 + value) ^ 0x4000)]
        for value in calibration_axis_bases
    }))
    calibration_lookup_specs = {
        "byte_scalar": (0x030BA6, 0x030BB2),
        "word_scalar": (0x030BB2, 0x030BBE),
        "byte_grid": (0x030BBE, 0x030BD8),
        "word_grid": (0x030BD8, 0x030BF2),
    }
    calibration_lookup_gates = {
        name: stock_entry_gate(
            instructions,
            table_targets,
            start,
            end,
            {start},
            stock_entry_sources,
        )
        for name, (start, end) in calibration_lookup_specs.items()
    }
    calibration_lookup_arguments = {
        name: constant_immediate_call_arguments(
            gate,
            "r12",
            instructions,
            branch_targets,
        )
        for name, gate in calibration_lookup_gates.items()
    }
    calibration_lookup_bases = tuple(sorted({
        value
        for values, _arguments in calibration_lookup_arguments.values()
        for value in values
    }))
    calibration_lookup_max_address = (
        max(calibration_lookup_bases)
        + 2 * (max(calibration_axis_lengths) ** 2 + max(calibration_axis_lengths))
    )
    if (
        any(
            gate["status"] != "proven for the stock image"
            for gate in calibration_lookup_gates.values()
        )
        or not calibration_lookup_bases
        or max(calibration_axis_lengths) != 20
        or calibration_lookup_max_address >= 0xC000
    ):
        raise RuntimeError(
            "calibration lookup entry proof changed: "
            + json.dumps(calibration_lookup_gates, sort_keys=True)
        )
    calibration_lookup_invariant = {
        "status": "proven for the stock image",
        "table_bases": [
            f"0x{value:04X}" for value in calibration_lookup_bases
        ],
        "maximum_axis_length": max(calibration_axis_lengths),
        "conservative_maximum_address": (
            f"0x{calibration_lookup_max_address:04X}"
        ),
        "proof": (
            "Every stock caller passes an immediate calibration-table base. "
            "All referenced axis lengths are at most 20, so even the "
            "conservative word-grid bound remains below the DPP3 RAM domain."
        ),
    }
    boot_range = next(
        item
        for item in boot_fixture["ranges"]
        if int(item["lo"], 16) <= 0xF698 < int(item["hi"], 16)
    )
    boot_ring_index = bytes.fromhex(boot_range["hex"])[
        0xF698 - int(boot_range["lo"], 16)
    ]
    ring_index_writers = {
        item["pc"]
        for item in direct
        if int(item["address"], 16) == 0xF698
        and item["kind"] in {"write", "read/write"}
    }
    if (
        boot_ring_index != 0
        or ring_index_writers != {"0x0398F0"}
        or [
            instructions_by_pc[pc]["instruction"]
            for pc in (0x0398D8, 0x0398DC, 0x0398E2, 0x0398E8, 0x0398EA, 0x0398EC, 0x0398F0)
        ]
        != [
            "mov r2,0xf698",
            "shl r2,#0x2",
            "add r2,#0x2",
            "add r2,#0x2",
            "shr r2,#0x2",
            "and r2,#0xf",
            "mov 0xf698,r2",
        ]
    ):
        raise RuntimeError("interrupt ring-index invariant changed")
    ring_index_invariant = {
        "slot": "0xF698",
        "boot_value": boot_ring_index,
        "direct_writers": sorted(ring_index_writers),
        "proven_values": "0x0000-0x000F",
        "proof": (
            "The independent boot fixture has zero at 0xF698. Its sole direct "
            "writer stores (old + 1) & 0xF after the two four-byte ring writes."
        ),
    }
    fc50_boot_range = next(
        item
        for item in boot_fixture["ranges"]
        if int(item["lo"], 16) <= 0xFC50
        and 0xFC52 <= int(item["hi"], 16)
    )
    fc50_boot_value = int.from_bytes(
        bytes.fromhex(fc50_boot_range["hex"])[
            0xFC50 - int(fc50_boot_range["lo"], 16):
            0xFC52 - int(fc50_boot_range["lo"], 16)
        ],
        "little",
    )
    fc50_writers = {
        item["pc"]
        for item in direct
        if int(item["address"], 16) == 0xFC50
        and item["kind"] in {"write", "read/write"}
    }
    if (
        fc50_boot_value != 0
        or fc50_writers != {"0x02DE38"}
        or instructions_by_pc[0x02DE34]["instruction"] != "mov r4,#0x2ad6"
    ):
        raise RuntimeError("FC50 stack-offset invariant changed")
    fc50_effective_bases = tuple(sorted({
        stack_pointer
        for stack_pointer in range(0xFA00, 0xFA46, 2)
    } | {
        (stack_pointer + 0x2AD6) & 0xFFFF
        for stack_pointer in range(0xFA00, 0xFA46, 2)
    }))
    fc50_stack_offset_invariant = {
        "slot": "0xFC50",
        "boot_value": fc50_boot_value,
        "direct_writers": sorted(fc50_writers),
        "proven_values": ["0x0000", "0x2AD6"],
        "proof": (
            "The boot fixture starts FC50 at zero and its sole direct writer "
            "installs 0x2AD6; adding either value to the bounded software "
            "stack pointer cannot reach a candidate RAM gap."
        ),
    }
    context_index_assignments = {
        0x0003C8: 1,
        0x000538: 3,
        0x000816: 4,
        0x0008D0: 5,
        0x000986: 0,
        0x000CAE: 0,
        0x000CC2: 3,
        0x0011A6: 1,
        0x00123C: 2,
        0x0012D2: 3,
        0x00137E: 4,
        0x001414: 5,
        0x0014A8: 0,
    }
    context_index_writers = {
        int(item["pc"], 16)
        for item in direct
        if int(item["address"], 16) == 0xFA80
        and item["kind"] in {"write", "read/write"}
    }
    boot_context_index = bytes.fromhex(boot_range["hex"])[
        0xFA80 - int(boot_range["lo"], 16)
    ]
    if (
        boot_context_index != 0
        or context_index_writers != set(context_index_assignments)
        or any(
            instructions_by_pc[pc - 2]["instruction"] != f"movb RL6,#0x{value:x}"
            for pc, value in context_index_assignments.items()
        )
    ):
        raise RuntimeError("context-index invariant changed")
    context_index_invariant = {
        "slot": "0xFA80",
        "boot_value": boot_context_index,
        "direct_writers": [
            {
                "pc": f"0x{pc:06X}",
                "value": value,
            }
            for pc, value in sorted(context_index_assignments.items())
        ],
        "proven_values": sorted(set(context_index_assignments.values())),
        "proof": (
            "The independent boot fixture has zero at 0xFA80, and every direct "
            "writer is immediately preceded by a fixed RL6 value in 0..5."
        ),
    }
    countdown_callback_entry_gate = stock_entry_gate(
        instructions,
        table_targets,
        0x027FB2,
        0x028024,
        {0x027FB2},
        live_sources=stock_entry_sources,
    )
    countdown_table_offset = (0xAD54 & 0x3FFF) ^ 0x4000
    countdown_callback_words = tuple(
        int.from_bytes(
            image[
                countdown_table_offset + 2 * index :
                countdown_table_offset + 2 * index + 2
            ],
            "little",
        )
        for index in range(31)
    )
    dpp2_writers = {
        item["pc"]: item["instruction"]
        for item in direct
        if int(item["address"], 16) == 0xFE04
        and item["kind"] in {"write", "read/write"}
    }
    expected_dpp2_writers = {
        "0x0044A8": "mov 0xfe04,#0x0",
        "0x0046D4": "mov 0xfe04,#0x0",
        "0x038FD8": "mov 0xfe04,#0x0",
        "0x03935C": "pop 0xfe04",
        "0x03937C": "mov 0xfe04,#0x0",
        "0x0393FC": "pop 0xfe04",
        "0x03941C": "mov 0xfe04,#0x0",
        "0x039458": "pop 0xfe04",
    }
    countdown_scheduler_instructions = {
        0x007F00: "mov r7,#0xf794",
        0x007F04: "mov r8,#0x0",
        0x007F06: "mov r9,[r7]",
        0x007F08: "jmpr cc_EQ,0x007f1c",
        0x007F0A: "sub r9,#0x1",
        0x007F0C: "mov [r7],r9",
        0x007F0E: "cmp r9,#0x0",
        0x007F10: "jmpr cc_NE,0x007f1c",
        0x007F12: "mov r4,r8",
        0x007F14: "shl r4,#0x1",
        0x007F16: "mov r5,[r4+#0xad54]",
        0x007F1A: "calli cc_UC,[r5]",
        0x007F1C: "add r7,#0x2",
        0x007F1E: "add r8,#0x1",
        0x007F20: "cmp r8,#0x1f",
        0x007F24: "jmpr cc_C,0x007f06",
    }
    if (
        countdown_callback_entry_gate["status"] != "proven for the stock image"
        or {
            int(edge["pc"], 16)
            for edge in countdown_callback_entry_gate["external_direct_entries"]
        }
        != {0x00737E}
        or len(countdown_callback_words) != 31
        or countdown_callback_words[2] != 0x336A
        or countdown_callback_words.count(0x336A) != 1
        or dpp2_writers != expected_dpp2_writers
        or any(
            instructions_by_pc[pc]["instruction"] != instruction
            for pc, instruction in countdown_scheduler_instructions.items()
        )
        or any(
            instructions_by_pc[pc]["operands"]
            and instructions_by_pc[pc]["operands"][0] in {"r8", "r9"}
            for pc in range(0x00736A, 0x00737E, 2)
            if pc in instructions_by_pc
        )
        or instructions_by_pc[0x00737E]["instruction"] != "calls 0x023fb2"
    ):
        raise RuntimeError(
            "low countdown callback pointer proof changed: "
            + json.dumps(countdown_callback_entry_gate, sort_keys=True)
        )
    countdown_callback_entry_gate["table"] = "0xAD54"
    countdown_callback_entry_gate["table_entries"] = len(
        countdown_callback_words
    )
    countdown_callback_entry_gate["callback_index"] = 2
    countdown_callback_entry_gate["proven_registers"] = {
        "r8": "0x0002",
        "r9": "0x0000",
    }
    countdown_callback_entry_gate["pointer_proof"] = (
        "DPP2 is initialized to zero and every later direct writer either "
        "reasserts zero or restores the saved value. The immutable 31-entry "
        "0xAD54 countdown table contains callback IP 0x336A exactly once, at "
        "index 2. The scheduler increments r8 from zero and calls only after "
        "decrementing that slot into r9=0. The callback preserves r8/r9 before "
        "its sole live call into file 0x027FB2; all high direct callers are "
        "rejected by the conservative stock reachability gate."
    )
    knock_task_entry_gate = stock_entry_gate(
        instructions,
        table_targets,
        0x02E4F2,
        0x02EC22,
        {0x02E4F2},
    )
    knock_channel_table_offset = (0xAC74 & 0x3FFF) ^ 0x4000
    knock_channel_map = tuple(
        image[knock_channel_table_offset + index] for index in range(6)
    )
    if (
        knock_task_entry_gate["status"] != "proven for the stock image"
        or {
            int(edge["pc"], 16)
            for edge in knock_task_entry_gate["external_direct_entries"]
        }
        != {0x030FDA}
        or knock_channel_map != (3, 4, 5, 0, 1, 2)
        or max(context_index_assignments.values()) > 5
        or instructions_by_pc[0x030F4A]["instruction"] != "movb RL2,0xfa80"
        or instructions_by_pc[0x030F4E]["instruction"] != "movb 0xfc9f,RL2"
        or instructions_by_pc[0x02E502]["instruction"] != "movbz r4,0xfc9f"
        or instructions_by_pc[0x02E506]["instruction"] != "movb RL5,[r4+#0xac74]"
        or instructions_by_pc[0x02E50A]["instruction"] != "movbz r6,RL5"
        or instructions_by_pc[0x02EA74]["instruction"] != "mov r5,r6"
        or instructions_by_pc[0x02EA76]["instruction"] != "shl r5,#0x6"
        or instructions_by_pc[0x02EA78]["instruction"] != "mov r2,#0xd840"
        or instructions_by_pc[0x02EA7E]["instruction"] != "movb RL5,[r6+#0xe9ba]"
        or instructions_by_pc[0x02EA82]["instruction"] != "movbz r5,RL5"
        or instructions_by_pc[0x02EA86]["instruction"] != "movb [r2],RL4"
        or instructions_by_pc[0x02EB60]["instruction"] != "mov r5,r6"
        or instructions_by_pc[0x02EB62]["instruction"] != "shl r5,#0x6"
        or instructions_by_pc[0x02EB64]["instruction"] != "mov r2,#0xd840"
        or instructions_by_pc[0x02EB6A]["instruction"] != "movbz r5,0xe9b9"
        or instructions_by_pc[0x02EB70]["instruction"] != "movb [r2],RL4"
    ):
        raise RuntimeError(
            "knock freeze-frame pointer proof changed: "
            + json.dumps(knock_task_entry_gate, sort_keys=True)
        )
    knock_task_entry_gate["pointer_proof"] = (
        "The sole stock entry copies the proven 0..5 context index through "
        "0xFC9F and a six-byte permutation table into r6. Both stores use "
        "0xD840 + (r6 << 6) plus a zero-extended byte index, conservatively "
        "bounding them to 0xD840-0xDB7F."
    )
    knock_task_entry_gate["channel_map"] = list(knock_channel_map)
    knock_freeze_destinations = tuple(range(0xD840, 0xDB80))
    boot_flash_offset = int.from_bytes(
        bytes.fromhex(boot_range["hex"])[
            0xE656 - int(boot_range["lo"], 16) :
            0xE658 - int(boot_range["lo"], 16)
        ],
        "little",
    )
    flash_offset_writers = {
        int(item["pc"], 16)
        for item in direct
        if int(item["address"], 16) == 0xE656
        and item["kind"] in {"write", "read/write"}
    }
    r8_flash_offset_assignments = {
        item["pc"]
        for item in instructions
        if 0x004EF2 <= item["pc"] < 0x00518C
        and item["operands"]
        and item["operands"][0] == "r8"
    }
    if (
        boot_flash_offset != 0
        or flash_offset_writers
        != {0x0050AC, 0x0050C2, 0x0050D8, 0x0050F0, 0x00518C, 0x0051DE}
        or r8_flash_offset_assignments
        != {0x004EF2, 0x004EF4, 0x004EF8, 0x004EFE, 0x004F00}
        or instructions_by_pc[0x004E90]["instruction"] != "movb RL4,0xe426"
        or instructions_by_pc[0x004E94]["instruction"] != "movb 0xe736,RL4"
        or instructions_by_pc[0x004EF4]["instruction"] != "movbz r8,0xe425"
        or instructions_by_pc[0x004EF8]["instruction"] != "shl r8,#0x8"
        or instructions_by_pc[0x004EFA]["instruction"] != "movbz r5,0xe426"
        or instructions_by_pc[0x004EFE]["instruction"] != "or r8,r5"
        or instructions_by_pc[0x004F00]["instruction"] != "and r8,#0x3fff"
        or instructions_by_pc[0x0050A8]["instruction"] != "mov r5,#0x2200"
        or instructions_by_pc[0x0050C0]["instruction"] != "mov r5,#0x0"
        or instructions_by_pc[0x0050D6]["instruction"] != "mov r5,#0x0"
        or instructions_by_pc[0x0050EE]["instruction"] != "mov r5,#0x0"
        or instructions_by_pc[0x0051C4]["instruction"] != "movbz r4,0xe736"
        or instructions_by_pc[0x0051C8]["instruction"] != "and r4,#0x1"
        or instructions_by_pc[0x0051CA]["instruction"] != "jmpr cc_EQ,0x0051ec"
        or instructions_by_pc[0x0051DE]["instruction"] != "sub 0xe656,r4"
        or instructions_by_pc[0x0042E6]["instruction"] != "mov r4,#0x3ffe"
        or instructions_by_pc[0x0042EA]["instruction"] != "add r12,#0x2"
        or instructions_by_pc[0x0042F6]["instruction"] != "mov r12,#0x0"
    ):
        raise RuntimeError(
            "flash-offset invariant changed: "
            + json.dumps(
                {
                    "boot": boot_flash_offset,
                    "writers": sorted(f"{pc:06X}" for pc in flash_offset_writers),
                    "r8_assignments": sorted(
                        f"{pc:06X}" for pc in r8_flash_offset_assignments
                    ),
                },
                sort_keys=True,
            )
        )
    flash_offset_invariant = {
        "slot": "0xE656",
        "boot_value": boot_flash_offset,
        "direct_writers": [f"0x{pc:06X}" for pc in sorted(flash_offset_writers)],
        "proven_values": "0x0000-0x3FFF",
        "proof": (
            "The request-derived offset is masked with 0x3FFF; all other "
            "assignments are 0 or 0x2200, and the program loop wraps after 0x3FFE."
        ),
    }
    if (
        instructions_by_pc[0x038C22]["instruction"] != "movbz r1,0xfa80"
        or instructions_by_pc[0x038C26]["instruction"] != "add r1,r1"
        or instructions_by_pc[0x038C28]["instruction"] != "mov r4,[r1+#0xadf4]"
        or instructions_by_pc[0x039A1A]["instruction"] != "movbz r0,0xfa80"
        or instructions_by_pc[0x039A1E]["instruction"] != "add r0,r0"
        or instructions_by_pc[0x039A20]["instruction"] != "mov r2,[r0+#0xb166]"
        or instructions_by_pc[0x001D3A]["instruction"] != "movbz r12,0xfa80"
        or instructions_by_pc[0x001D3E]["instruction"] != "shl r12,#0x1"
        or instructions_by_pc[0x001DA6]["instruction"] != "mov r0,[r12+#0xb134]"
        or instructions_by_pc[0x001E3C]["instruction"] != "movbz r12,0xfa80"
        or instructions_by_pc[0x001E40]["instruction"] != "shl r12,#0x1"
        or instructions_by_pc[0x001E46]["instruction"] != "mov r0,[r12+#0xb140]"
    ):
        raise RuntimeError("interrupt destination-table derivation changed")
    timer_destinations = reference_word_values(image, 0xADF4, 6)
    serial_destinations = reference_word_values(image, 0xB166, 6)
    interrupt_lookup_destinations_a = reference_word_values(image, 0xB11C, 6)
    interrupt_lookup_destinations_b = reference_word_values(image, 0xB128, 6)
    coefficient_destinations_a = reference_word_values(image, 0xB134, 6)
    coefficient_destinations_b = reference_word_values(image, 0xB140, 6)
    if (
        timer_destinations
        != (0xFC82, 0xFC86, 0xFC8A, 0xFC8E, 0xFC92, 0xFC96)
        or serial_destinations
        != (0xFC82, 0xFC86, 0xFC8A, 0xFC8E, 0xFC92, 0xFC96)
        or interrupt_lookup_destinations_a
        != (0xFC7C, 0xFC7D, 0xFC7E, 0xFC7F, 0xFC80, 0xFC81)
        or interrupt_lookup_destinations_b
        != (0xFC7C, 0xFC7D, 0xFC7E, 0xFC7F, 0xFC80, 0xFC81)
        or coefficient_destinations_a != (0, 2, 4, 6, 8, 10)
        or coefficient_destinations_b != (0, 2, 4, 6, 8, 10)
    ):
        raise RuntimeError("reference pointer-table values changed")
    guarded_pointer_slots = (0xE650, 0xE65A, 0xF00E, 0xF010, 0xF596)
    boot_bytes = bytes.fromhex(boot_range["hex"])
    boot_lo = int(boot_range["lo"], 16)
    guarded_slot_boot_values = {
        slot: int.from_bytes(
            boot_bytes[slot - boot_lo : slot - boot_lo + 2],
            "little",
        )
        for slot in guarded_pointer_slots
    }
    guarded_slot_direct_writers = {
        slot: {
            item["pc"]: item["instruction"]
            for item in direct
            if item["kind"] in {"write", "read/write"}
            and int(item["address"], 16) < slot + 2
            and int(item["address"], 16) + item["width"] > slot
        }
        for slot in guarded_pointer_slots
    }
    expected_guarded_slot_direct_writers = {
        0xE650: {
            "0x005634": "add 0xe650,r4",
            "0x005672": "mov 0xe650,r5",
            "0x00567A": "add 0xe650,r4",
            "0x039026": "add 0xe650,r4",
            "0x03908A": "mov 0xe650,r4",
            "0x039092": "add 0xe650,r4",
            "0x039148": "add 0xe650,r4",
            "0x039224": "mov 0xe650,r4",
        },
        0xE65A: {
            "0x0056C4": "mov 0xe65a,r4",
            "0x0056EE": "mov 0xe65a,r4",
            "0x02CB04": "mov 0xe65a,r4",
            "0x0391B2": "mov 0xe65a,r2",
        },
        0xF00E: {
            "0x024FA8": "mov 0xf00e,r5",
            "0x024FBA": "mov 0xf00e,r5",
        },
        0xF010: {
            "0x024FA0": "mov 0xf010,r4",
            "0x024FB2": "mov 0xf010,r4",
        },
        0xF596: {
            "0x02F1D8": "mov 0xf596,r4",
            "0x02F1EA": "mov 0xf596,r4",
        },
    }
    guarded_slot_instruction_expectations = {
        0x00563E: "addb 0xe652,RL4",
        0x005642: "movb RL4,0xe652",
        0x005646: "cmpb RL4,0xe421",
        0x00566E: "mov r5,#0xe420",
        0x0056B8: "movb RL4,0xe521",
        0x0056BC: "movb 0xe652,RL4",
        0x0056C0: "mov r4,#0xe520",
        0x0056EC: "add r4,#0x1",
        0x0056F4: "subb 0xe652,RL4",
        0x024F9C: "mov r4,#0xd005",
        0x024FA4: "mov r5,#0xd009",
        0x024FAE: "mov r4,#0xd006",
        0x024FB6: "mov r5,#0xd00a",
        0x02CB00: "mov r4,#0xf744",
        0x02CB08: "movb RL4,0xf750",
        0x02CB0C: "subb RL4,#0x1",
        0x02CB0E: "movb 0xf761,RL4",
        0x02CD6A: "movb RL4,#0x1",
        0x02CD6C: "subb 0xf761,RL4",
        0x02D0C2: "mov r9,0xf596",
        0x02D1D8: "mov r9,[r0+]",
        0x02D1DE: "mov r9,0xf596",
        0x02D476: "mov r9,[r0+]",
        0x02DBB0: "mov r9,0xf596",
        0x02DD06: "mov r9,[r0+]",
        0x02F1D4: "mov r4,#0xf0c4",
        0x02F1E6: "mov r4,#0xf018",
        0x039030: "addb 0xe652,RL4",
        0x039034: "movb RL4,0xe652",
        0x039038: "cmpb RL4,0xe421",
        0x039086: "mov r4,#0xe420",
        0x03913C: "movb RL4,0xf761",
        0x039140: "cmpb RL4,#0xe",
        0x039144: "jmpr cc_NC,0x03915c",
        0x0391A2: "movb RL5,0xf761",
        0x0391A6: "cmpb RL5,#0x0",
        0x0391B0: "add r2,#0x1",
        0x039220: "mov r4,#0xf736",
        0x03922A: "movb RL4,#0x1",
        0x03922C: "movb 0xf761,RL4",
    }
    f596_scope_specs = {
        "object_math": (
            0x02D0C0,
            0x02D1DC,
            {0x02D328, 0x02D386},
        ),
        "object_state": (
            0x02D1DC,
            0x02D47A,
            {0x02D854},
        ),
        "object_metrics": (
            0x02DBAE,
            0x02DD0A,
            {0x02D324, 0x02D382},
        ),
    }
    f596_scope_gates = {
        label: stock_entry_gate(
            instructions,
            table_targets,
            start,
            end,
            {start},
        )
        for label, (start, end, _callers) in f596_scope_specs.items()
    }
    if (
        guarded_slot_boot_values
        != {slot: 0 for slot in guarded_pointer_slots}
        or guarded_slot_direct_writers
        != expected_guarded_slot_direct_writers
        or any(
            instructions_by_pc[pc]["instruction"] != instruction
            for pc, instruction in guarded_slot_instruction_expectations.items()
        )
        or any(
            f596_scope_gates[label]["status"]
            != "proven for the stock image"
            or {
                int(edge["pc"], 16)
                for edge in f596_scope_gates[label]["external_direct_entries"]
            }
            != callers
            for label, (_start, _end, callers) in f596_scope_specs.items()
        )
    ):
        raise RuntimeError("guarded native pointer-slot proof changed")
    e650_values = (
        0,
        *range(0xE420, 0xE521),
        *range(0xF736, 0xF744),
    )
    e65a_values = (
        0,
        *range(0xE520, 0xE621),
        *range(0xF744, 0xF844),
    )
    f00e_values = (0, 0xD009, 0xD00A)
    f010_values = (0, 0xD005, 0xD006)
    f596_values = (0, 0xF018, 0xF0C4)
    f596_scopes = tuple(
        (start, end) for start, end, _callers in f596_scope_specs.values()
    )
    f596_r9_write_pcs = tuple(sorted(
        int(item["pc"], 16)
        for item in unresolved
        if item["kind"] in {"write", "read/write"}
        and item["base_register"] == "r9"
        and int(item["pc"], 16) in high_reachable_pcs
        and any(
            start <= int(item["pc"], 16) < end
            for start, end in f596_scopes
        )
    ))
    if len(f596_r9_write_pcs) != 43:
        raise RuntimeError(
            "guarded F596 object-write set changed: "
            + json.dumps(
                [f"0x{pc:06X}" for pc in f596_r9_write_pcs]
            )
        )
    f596_r9_read_pcs = tuple(sorted(
        int(item["pc"], 16)
        for item in unresolved
        if item["kind"] == "read"
        and item["base_register"] == "r9"
        and int(item["pc"], 16) in high_reachable_pcs
        and any(
            start <= int(item["pc"], 16) < end
            for start, end in f596_scopes
        )
    ))
    if len(f596_r9_read_pcs) != 63:
        raise RuntimeError(
            "guarded F596 object-read set changed: "
            + json.dumps([f"0x{pc:06X}" for pc in f596_r9_read_pcs])
        )
    guarded_slot_fixed_point_proof = (
        "The independent boot fixture has zero in all five word slots. Exact "
        "direct-writer enumeration proves E650/E65A are seeded at native "
        "diagnostic buffers and advanced only under byte counters, F00E/F010 "
        "select fixed CAN registers, and F596 selects 0xF018 or 0xF0C4. "
        "The startup RAM self-test and zero-fill are retained as explicit "
        "pre-runtime writers. A simultaneous post-boot fixed-point check rejects "
        "the proof unless every remaining stock-live indirect write is bounded "
        "and none of those destinations overlaps any guarded slot."
    )
    startup_guarded_slot_write_pcs = {
        0x0045D0,
        0x0045E0,
        0x0045EC,
        0x0045F6,
        0x004610,
        0x004918,
    }
    startup_guarded_slot_instructions = {
        pc: instructions_by_pc[pc]["instruction"]
        for pc in startup_guarded_slot_write_pcs
    }
    if startup_guarded_slot_instructions != {
        0x0045D0: "mov [r0],r2",
        0x0045E0: "mov [-r2],r6",
        0x0045EC: "mov [r2],r9",
        0x0045F6: "mov [r2],r9",
        0x004610: "mov [-r2],r3",
        0x004918: "mov [r9],r4",
    }:
        raise RuntimeError("startup guarded-slot writer set changed")
    proven_pointer_rules = (
        *PROVEN_POINTER_VALUE_SETS,
        {
            "owner": "FUN_022998",
            "base_register": "r2",
            "values": record_lookup_base_values,
            "exact_values": True,
            "evidence": record_identifier_invariant["proof"],
        },
        {
            "label": "guarded F596 object reads",
            "base_register": "r9",
            "pcs": f596_r9_read_pcs,
            "values": f596_values,
            "exact_values": True,
            "evidence": guarded_slot_fixed_point_proof,
        },
        {
            "label": "guarded F596 derived object reads",
            "base_register": "r4",
            "pcs": (0x02D31A, 0x02D378),
            "values": tuple(value + 0x6E for value in f596_values),
            "exact_values": True,
            "evidence": (
                "The local r4 value is the guarded F596-backed r9 object plus 0x6E."
            ),
        },
        {
            "label": "bounded native status-table reads",
            "base_register": "r4",
            "pcs": (
                0x02BB56, 0x02BB60, 0x02BB68,
                0x02BB94, 0x02BB9E, 0x02BBA6,
                0x02BBD2, 0x02BBDC, 0x02BBE4,
                0x02BC10, 0x02BC1A, 0x02BC22,
                0x02BC50, 0x02BC5A, 0x02BC62,
            ),
            "values": range(16),
            "evidence": "Each unrolled counted loop keeps its table index below 16.",
        },
        {
            "label": "bounded byte-indexed state reads",
            "base_register": "r4",
            "pcs": (
                0x02E7BC, 0x02E7DE, 0x02E7FE, 0x02E838,
                0x02E846, 0x02E854, 0x02E89C, 0x02E8CE,
                0x02E8DC, 0x02E924, 0x02E990, 0x02EC04,
                0x02ECD0,
            ),
            "values": range(0x100),
            "evidence": "The local r4 base is rebuilt from the byte-sized loop index.",
        },
        {
            "label": "bounded byte-indexed state reads",
            "base_register": "r6",
            "pcs": (0x02E7C0, 0x02E914, 0x02E930, 0x02ECAE),
            "values": range(0x100),
            "evidence": "The state-machine r6 selector is byte-sized.",
        },
        {
            "label": "bounded knock-channel reads",
            "base_register": "r6",
            "pcs": (0x02EA7E, 0x02EA88, 0x02ED56, 0x02ED60, 0x02ED7C),
            "values": range(6),
            "evidence": knock_task_entry_gate["pointer_proof"],
        },
        {
            "label": "bounded knock freeze-frame reads",
            "base_register": "r5",
            "pcs": (0x02EAAE,),
            "values": range(0xD840, 0xDB80),
            "evidence": knock_task_entry_gate["pointer_proof"],
        },
        {
            "label": "bounded knock coefficient reads",
            "base_register": "r4",
            "pcs": (0x02EC9E,),
            "values": range(0, 12, 2),
            "evidence": knock_task_entry_gate["pointer_proof"],
        },
        {
            "label": "bounded record-table reads",
            "base_register": "r4",
            "pcs": (0x0357D4,),
            "values": range(0x5F),
            "evidence": record_identifier_invariant["proof"],
        },
        {
            "label": "bounded record-metadata reads",
            "base_register": "r4",
            "pcs": (0x0357F6, 0x035804, 0x035812, 0x03588A),
            "values": range(0, 0x5F0, 16),
            "evidence": record_identifier_invariant["proof"],
        },
        {
            "label": "bounded native record reads",
            "base_register": "r4",
            "pcs": (
                0x0358B2, 0x0359D0, 0x0359FA, 0x035A6C,
                0x035A7C, 0x035AB4, 0x035ADC,
            ),
            "values": range(0xF642, 0xFC3D, 6),
            "evidence": "r4 is 0xF642 plus six times a byte-sized record index.",
        },
        {
            "label": "bounded native record reads",
            "base_register": "r5",
            "pcs": (0x03590A, 0x035984),
            "values": range(0, 0x5FB, 6),
            "evidence": "r5 is six times a byte-sized record index.",
        },
        {
            "label": "bounded active-record reads",
            "base_register": "r9",
            "pcs": (0x0357E8, 0x035B36),
            "values": range(0xEA1A, 0xEE83, 12),
            "evidence": record_identifier_invariant["proof"],
        },
        {
            "label": "bounded active-record reads",
            "base_register": "r13",
            "pcs": (0x023854,),
            "values": range(0xEA1A, 0xEE83, 12),
            "evidence": record_identifier_invariant["proof"],
        },
        {
            "label": "bounded active-record byte-10 reads",
            "base_register": "r4",
            "pcs": (0x035BBC,),
            "values": range(0xEA24, 0xEE8D, 12),
            "evidence": record_identifier_invariant["proof"],
        },
        {
            "label": "record-comparator fixed-record reads",
            "base_register": "r9",
            "pcs": (0x035214, 0x035232, 0x035238, 0x03525C),
            "values": (0xF556,),
            "exact_values": True,
            "evidence": record_comparator_entry_gate["pointer_proof"],
        },
        {
            "label": "record-comparator lookup-record reads",
            "base_register": "r8",
            "pcs": (0x035220, 0x035228, 0x035246, 0x03524E),
            "values": record_comparator_r8_values,
            "exact_values": True,
            "evidence": record_comparator_entry_gate["pointer_proof"],
        },
        {
            "label": "bounded record-list reads",
            "base_register": "r4",
            "pcs": (
                0x0362CA, 0x0362E6, 0x036302,
                0x03631C, 0x036338, 0x036354,
            ),
            "values": range(0x5F),
            "evidence": record_identifier_invariant["proof"],
        },
        {
            "label": "bounded record-metadata reads",
            "base_register": "r4",
            "pcs": (
                0x0362D2, 0x0362EE, 0x036324,
                0x036340, 0x03635C,
            ),
            "values": range(0, 0x5F0, 16),
            "evidence": record_identifier_invariant["proof"],
        },
        {
            "label": "bounded active-record reads",
            "base_register": "r5",
            "pcs": (0x0362BE, 0x036310),
            "values": range(0, 0x469, 12),
            "evidence": record_identifier_invariant["proof"],
        },
        {
            "label": "bounded four-word ring reads",
            "base_register": "r5",
            "pcs": (
                0x030C5E, 0x030C60, 0x030C6E, 0x030C70,
                0x030C8E, 0x030C90, 0x030C9E, 0x030CA0,
            ),
            "values": range(0xF69C, 0xF6DC, 4),
            "evidence": "The ring index is masked to four bits before scaling by four.",
        },
        {
            "label": "bounded saved-record-list reads",
            "base_register": "r4",
            "pcs": (0x02299A, 0x022A6A),
            "values": range(10),
            "evidence": "The saved list count is clamped to ten entries.",
        },
        {
            "label": "bounded lookup-record reads",
            "base_register": "r5",
            "pcs": (
                0x0229B6, 0x0229C6, 0x0229E2, 0x0229FE,
                0x022A1A, 0x022A36, 0x022A52,
            ),
            "values": record_lookup_base_values,
            "exact_values": True,
            "evidence": record_identifier_invariant["proof"],
        },
        {
            "label": "bounded six-byte status reads",
            "base_register": "r4",
            "pcs": (0x022B16,),
            "values": range(6),
            "evidence": "The copy loop stops after six byte entries.",
        },
        {
            "label": "bounded record-list reads",
            "base_register": "r4",
            "pcs": (
                0x023F44, 0x023F6E,
                0x036242, 0x03625C, 0x03627C, 0x03629A,
                0x02BAC0, 0x02BAD6, 0x02BAF6,
            ),
            "values": range(0x5F),
            "evidence": record_identifier_invariant["proof"],
        },
        {
            "label": "bounded record-metadata reads",
            "base_register": "r4",
            "pcs": (0x023F76, 0x036264, 0x0362A2, 0x02BAC8),
            "values": range(0, 0x5F0, 16),
            "evidence": record_identifier_invariant["proof"],
        },
        {
            "label": "bounded active-record reads",
            "base_register": "r5",
            "pcs": (
                0x036250, 0x03628A,
                0x02BAE4, 0x02BB04,
            ),
            "values": range(0, 0x469, 12),
            "evidence": record_identifier_invariant["proof"],
        },
        {
            "label": "bounded active-record reads",
            "base_register": "r9",
            "pcs": (0x023F58, 0x023F62, 0x023F7E, 0x023F9A, 0x02C0D8),
            "values": range(0xEA1A, 0xEE83, 12),
            "evidence": record_identifier_invariant["proof"],
        },
        {
            "label": "bounded record-list reads",
            "base_register": "r4",
            "pcs": (
                0x02BCB8, 0x02BCC8, 0x02BCEC,
                0x02BCFC, 0x02BD0C, 0x02BD1C,
            ),
            "values": range(0, 0x5F0, 16),
            "evidence": record_identifier_invariant["proof"],
        },
        {
            "label": "bounded active-record reads",
            "base_register": "r5",
            "pcs": (0x02BCAA, 0x02BCDE),
            "values": range(0, 0x469, 12),
            "evidence": record_identifier_invariant["proof"],
        },
        {
            "label": "record-status helper reads",
            "base_register": "r9",
            "pcs": (
                0x02371A, 0x023724, 0x023748, 0x023780,
                0x02378E, 0x023792, 0x0237A0,
            ),
            "values": RECORD_STATUS_HELPER_BASES,
            "exact_values": True,
            "evidence": record_update_entry_gates["status_helper"]["pointer_proof"],
        },
        {
            "label": "record-status helper reads",
            "base_register": "r4",
            "pcs": (0x023734, 0x0237B2),
            "values": tuple(value + 10 for value in RECORD_STATUS_HELPER_BASES),
            "exact_values": True,
            "evidence": "r4 is the gated helper record base plus ten.",
        },
        {
            "label": "record-status descriptor reads",
            "base_register": "r8",
            "pcs": (0x023778,),
            "values": tuple(sorted({
                0xA88C,
                *(
                    base
                    for bases in derived_descriptor_bases.values()
                    for base in bases
                ),
            })),
            "exact_values": True,
            "evidence": record_update_entry_gates["status_helper"]["pointer_proof"],
        },
        {
            "label": "record-status bitset reads",
            "base_register": "r3",
            "pcs": (0x02376C,),
            "values": range(0xF60A, 0xF62A, 2),
            "evidence": "r3 is 0xF60A plus twice the high nibble of the record identifier.",
        },
        {
            "label": "record-status bitset reads",
            "base_register": "r2",
            "pcs": (0x0237D8,),
            "values": range(0xF60A, 0xF62A, 2),
            "evidence": "r2 is 0xF60A plus twice the high nibble of the record identifier.",
        },
        {
            "label": "countdown-record reads",
            "base_register": "r8",
            "pcs": (
                0x023966, 0x02396E, 0x02397E, 0x02399E, 0x0239A8,
                0x0239CA, 0x023A18, 0x023AD8,
            ),
            "values": COUNTDOWN_RECORD_BASES,
            "exact_values": True,
            "evidence": record_update_entry_gates["countdown"]["pointer_proof"],
        },
        {
            "label": "countdown-descriptor reads",
            "base_register": "r7",
            "pcs": (
                0x023974, 0x023A34, 0x023A3E, 0x023A48, 0x023AF4,
            ),
            "values": derived_descriptor_bases["countdown"],
            "exact_values": True,
            "evidence": record_update_entry_gates["countdown"]["pointer_proof"],
        },
        {
            "label": "countdown record byte-10 reads",
            "base_register": "r4",
            "pcs": (0x023992, 0x0239B4, 0x0239D6),
            "values": tuple(value + 10 for value in COUNTDOWN_RECORD_BASES),
            "exact_values": True,
            "evidence": "r4 is the gated countdown-record base plus ten.",
        },
        {
            "label": "countdown record byte-11 reads",
            "base_register": "r4",
            "pcs": (0x0239C4,),
            "values": tuple(value + 11 for value in COUNTDOWN_RECORD_BASES),
            "exact_values": True,
            "evidence": "r4 is the gated countdown-record base plus eleven.",
        },
        {
            "label": "countdown-descriptor target reads",
            "base_register": "r4",
            "pcs": (0x023A38, 0x023A42, 0x023A4C),
            "values": descriptor_pointer_values["countdown"],
            "exact_values": True,
            "evidence": "r4 is one of the four immutable pointer fields in a gated countdown descriptor.",
        },
        {
            "label": "countdown stack-local reads",
            "base_register": "r0",
            "pcs": (0x023A86,),
            "values": range(0xFA00, 0xFA46, 2),
            "evidence": "The read consumes the function's reserved r0 software-stack argument.",
        },
        {
            "label": "state-record reads",
            "base_register": "r9",
            "pcs": (0x023B24, 0x023C04),
            "values": STATE_RECORD_BASES,
            "exact_values": True,
            "evidence": record_update_entry_gates["state"]["pointer_proof"],
        },
        {
            "label": "state-descriptor reads",
            "base_register": "r8",
            "pcs": (
                0x023B30, 0x023B92, 0x023B9C, 0x023BA6,
                0x023BB0, 0x023BBE, 0x023BEA, 0x023C1A,
            ),
            "values": derived_descriptor_bases["state"],
            "exact_values": True,
            "evidence": record_update_entry_gates["state"]["pointer_proof"],
        },
        {
            "label": "state record byte-10 reads",
            "base_register": "r4",
            "pcs": (0x023B40,),
            "values": tuple(value + 10 for value in STATE_RECORD_BASES),
            "exact_values": True,
            "evidence": "r4 is the gated state-record base plus ten.",
        },
        {
            "label": "state record byte-2 reads",
            "base_register": "r4",
            "pcs": (0x023C12,),
            "values": tuple(value + 2 for value in STATE_RECORD_BASES),
            "exact_values": True,
            "evidence": "r4 is the gated state-record base plus two.",
        },
        {
            "label": "state-descriptor target reads",
            "base_register": "r4",
            "pcs": (0x023B96, 0x023BA0, 0x023BAA, 0x023BB4),
            "values": descriptor_pointer_values["state"],
            "exact_values": True,
            "evidence": "r4 is one of the four immutable pointer fields in a gated state descriptor.",
        },
        {
            "label": "state record-list reads",
            "base_register": "r4",
            "pcs": (0x023B7C,),
            "values": range(0x5F),
            "evidence": record_identifier_invariant["proof"],
        },
        {
            "label": "paired-countdown record reads",
            "base_register": "r8",
            "pcs": (
                0x023D30, 0x023D38, 0x023D46, 0x023D66, 0x023D70,
                0x023D92, 0x023DE0, 0x023E98,
            ),
            "values": PAIRED_COUNTDOWN_RECORD_BASES,
            "exact_values": True,
            "evidence": record_update_entry_gates["paired_countdown"]["pointer_proof"],
        },
        {
            "label": "paired-countdown descriptor reads",
            "base_register": "r7",
            "pcs": (
                0x023D3E, 0x023DFC, 0x023E06, 0x023E10,
                0x023E1A, 0x023E28, 0x023E80, 0x023EB4,
            ),
            "values": derived_descriptor_bases["paired_countdown"],
            "exact_values": True,
            "evidence": record_update_entry_gates["paired_countdown"]["pointer_proof"],
        },
        {
            "label": "paired-countdown record byte-10 reads",
            "base_register": "r4",
            "pcs": (0x023D5A, 0x023D7C, 0x023D9E),
            "values": tuple(value + 10 for value in PAIRED_COUNTDOWN_RECORD_BASES),
            "exact_values": True,
            "evidence": "r4 is the gated paired-countdown record base plus ten.",
        },
        {
            "label": "paired-countdown record byte-11 reads",
            "base_register": "r4",
            "pcs": (0x023D8C,),
            "values": tuple(value + 11 for value in PAIRED_COUNTDOWN_RECORD_BASES),
            "exact_values": True,
            "evidence": "r4 is the gated paired-countdown record base plus eleven.",
        },
        {
            "label": "paired-countdown record byte-2 reads",
            "base_register": "r4",
            "pcs": (0x023EA6,),
            "values": tuple(value + 2 for value in PAIRED_COUNTDOWN_RECORD_BASES),
            "exact_values": True,
            "evidence": "r4 is the gated paired-countdown record base plus two.",
        },
        {
            "label": "paired-countdown descriptor target reads",
            "base_register": "r4",
            "pcs": (0x023E00, 0x023E0A, 0x023E14, 0x023E1E),
            "values": descriptor_pointer_values["paired_countdown"],
            "exact_values": True,
            "evidence": "r4 is one of the four immutable pointer fields in a gated paired-countdown descriptor.",
        },
        {
            "label": "paired-countdown stack-local reads",
            "base_register": "r0",
            "pcs": (0x023E4E,),
            "values": range(0xFA00, 0xFA46, 2),
            "evidence": "The read consumes the function's reserved r0 software-stack argument.",
        },
        {
            "label": "bounded 95-record scan reads",
            "base_register": "r13",
            "pcs": (0x023820, 0x023884, 0x0238B4, 0x0238E4, 0x023914),
            "values": range(0xEA1A, 0xEE83, 12),
            "evidence": "The outer record scan stops after the 95 native records.",
        },
        {
            "label": "bounded six-byte mask reads",
            "base_register": "r5",
            "pcs": (0x023838, 0x02386A, 0x02389A, 0x0238CA, 0x0238FA, 0x02392A),
            "values": range(0xF547, 0xF54D),
            "evidence": "Each inner loop scans exactly the six native mask bytes.",
        },
        {
            "label": "bounded record byte-10 reads",
            "base_register": "r4",
            "pcs": (0x023800,),
            "values": range(0xEA24, 0xF619, 12),
            "evidence": "r4 is the active record base plus ten.",
        },
        {
            "label": "bounded record dispatch reads",
            "base_register": "r4",
            "pcs": (0x02381C,),
            "values": range(0xABBC, 0xABC8, 2),
            "evidence": "The decoded immutable dispatch table has six word entries.",
        },
        {
            "label": "countdown-descriptor reads",
            "base_register": "r7",
            "pcs": (0x023A52, 0x023A60, 0x023ABE),
            "values": derived_descriptor_bases["countdown"],
            "exact_values": True,
            "evidence": record_update_entry_gates["countdown"]["pointer_proof"],
        },
        {
            "label": "countdown-descriptor target reads",
            "base_register": "r4",
            "pcs": (0x023A56,),
            "values": descriptor_pointer_values["countdown"],
            "exact_values": True,
            "evidence": "r4 is an immutable pointer field in a gated countdown descriptor.",
        },
        {
            "label": "countdown record byte-2 reads",
            "base_register": "r4",
            "pcs": (0x023AE6,),
            "values": tuple(value + 2 for value in COUNTDOWN_RECORD_BASES),
            "exact_values": True,
            "evidence": "r4 is the gated countdown-record base plus two.",
        },
        {
            "label": "bounded record-list reads",
            "base_register": "r4",
            "pcs": (0x023C86, 0x023FB6, 0x023FCC, 0x023FF8, 0x0362B0, 0x036370),
            "values": range(0x5F),
            "evidence": record_identifier_invariant["proof"],
        },
        {
            "label": "bounded fixed descriptor-pointer reads",
            "base_register": "r4",
            "pcs": (0x023CA0,),
            "values": reference_word_values(image, 0xA894, 1),
            "exact_values": True,
            "evidence": "r4 is loaded from the immutable 0xA894 descriptor word.",
        },
        {
            "label": "bounded fixed descriptor-pointer reads",
            "base_register": "r5",
            "pcs": (0x023CA8,),
            "values": reference_word_values(image, 0xA896, 1),
            "exact_values": True,
            "evidence": "r5 is loaded from the immutable 0xA896 descriptor word.",
        },
        {
            "label": "bounded active-record field reads",
            "base_register": "r4",
            "pcs": (0x023F8E,),
            "values": range(0xEA1D, 0xEE86, 12),
            "evidence": record_identifier_invariant["proof"],
        },
        {
            "label": "bounded active-record field reads",
            "base_register": "r4",
            "pcs": (0x023FAC,),
            "values": range(0xEA24, 0xEE8D, 12),
            "evidence": record_identifier_invariant["proof"],
        },
        {
            "label": "bounded native record reads",
            "base_register": "r4",
            "pcs": (0x023FEE,),
            "values": range(0xF642, 0xFC37, 6),
            "evidence": "r4 is 0xF642 plus six times a byte-sized lookup result.",
        },
        {
            "label": "bounded record-metadata reads",
            "base_register": "r4",
            "pcs": (0x036378,),
            "values": range(0, 0x5F0, 16),
            "evidence": record_identifier_invariant["proof"],
        },
        {
            "label": "bounded record-bitset reads",
            "base_register": "r2",
            "pcs": (0x035694,),
            "values": range(0xF616, 0xF622, 2),
            "evidence": "The record loop is bounded below 0x5F before dividing the index by sixteen.",
        },
        {
            "label": "byte-search input reads",
            "base_register": "r5",
            "pcs": (0x005AF4, 0x005B14),
            "values": byte_search_source_addresses,
            "exact_values": True,
            "evidence": byte_search_entry_gate["pointer_proof"],
        },
        {
            "label": "byte-search input reads",
            "base_register": "r2",
            "pcs": (0x005B16,),
            "values": byte_search_source_addresses,
            "exact_values": True,
            "evidence": byte_search_entry_gate["pointer_proof"],
        },
        {
            "label": "low serial stack-local reads",
            "base_register": "r0",
            "pcs": (0x0057D8, 0x0057E0, 0x0057FE, 0x00591E),
            "values": range(0xFA00, 0xFA46, 2),
            "evidence": "These reads consume reserved r0 software-stack locals.",
        },
        {
            "label": "low serial transfer-buffer reads",
            "base_register": "r7",
            "pcs": (0x00592A,),
            "values": low_serial_destinations,
            "exact_values": True,
            "evidence": low_serial_entry_gate["pointer_proof"],
        },
        {
            "label": "low serial source-buffer reads",
            "base_register": "r8",
            "pcs": (0x005886,),
            "values": low_serial_destinations,
            "exact_values": True,
            "evidence": low_serial_entry_gate["pointer_proof"],
        },
        {
            "label": "low record comparator left bytes",
            "base_register": "r5",
            "pcs": (0x005B5E,),
            "values": (*range(0xE537, 0xE53C), *range(0xE54B, 0xE550)),
            "exact_values": True,
            "evidence": low_record_compare_entry_gate["pointer_proof"],
        },
        {
            "label": "low record comparator right bytes",
            "base_register": "r2",
            "pcs": (0x005B60,),
            "values": (*range(0xE54B, 0xE550), *range(0xE55F, 0xE564)),
            "exact_values": True,
            "evidence": low_record_compare_entry_gate["pointer_proof"],
        },
        {
            "label": "low record comparator left record",
            "base_register": "r12",
            "pcs": (0x005B74, 0x005B7E, 0x005BB8),
            "values": (0xE537, 0xE54B),
            "exact_values": True,
            "evidence": low_record_compare_entry_gate["pointer_proof"],
        },
        {
            "label": "low record comparator right record",
            "base_register": "r13",
            "pcs": (0x005B88, 0x005B92, 0x005BB4),
            "values": (0xE54B, 0xE55F),
            "exact_values": True,
            "evidence": low_record_compare_entry_gate["pointer_proof"],
        },
        {
            "label": "low extended-record left record",
            "base_register": "r8",
            "pcs": (0x005BE4, 0x005BF0, 0x005BFC),
            "values": (0xE55F,),
            "exact_values": True,
            "evidence": low_record_extended_entry_gate["pointer_proof"],
        },
        {
            "label": "low extended-record right record",
            "base_register": "r9",
            "pcs": (0x005BE8, 0x005BF4, 0x005C00),
            "values": (0xE54B,),
            "exact_values": True,
            "evidence": low_record_extended_entry_gate["pointer_proof"],
        },
        {
            "label": "high serial stack-local reads",
            "base_register": "r0",
            "pcs": (0x0217A4, 0x0217AC, 0x0217C6, 0x0218E4),
            "values": range(0xFA00, 0xFA46, 2),
            "evidence": "These reads consume reserved r0 software-stack locals.",
        },
        {
            "label": "high serial transfer-buffer reads",
            "base_register": "r7",
            "pcs": (0x0218F0,),
            "values": high_serial_destinations,
            "exact_values": True,
            "evidence": high_serial_entry_gate["pointer_proof"],
        },
        {
            "label": "high serial transfer-buffer reads",
            "base_register": "r12",
            "pcs": (0x0219B8,),
            "values": high_serial_destinations,
            "exact_values": True,
            "evidence": high_serial_entry_gate["pointer_proof"],
        },
        {
            "label": "high serial payload reads",
            "base_register": "r8",
            "pcs": (0x021854,),
            "values": high_serial_destinations,
            "exact_values": True,
            "evidence": high_serial_entry_gate["pointer_proof"],
        },
        {
            "label": "calibration parser descriptor reads",
            "base_register": "r12",
            "pcs": (0x0308AE, 0x0308FE, 0x030952, 0x0309A0, 0x030B02, 0x030B28),
            "values": calibration_parser_bases,
            "exact_values": True,
            "evidence": calibration_parser_invariant["proof"],
        },
        {
            "label": "calibration axis reads",
            "base_register": "r3",
            "pcs": (
                0x0308B0, 0x0308C6, 0x0308E6, 0x0308EC,
                0x030900, 0x03091A, 0x03093A, 0x030940,
                0x030954, 0x030968, 0x030988, 0x03098E,
                0x0309A2, 0x0309BA, 0x0309DA, 0x0309E0,
                0x030B04, 0x030B12, 0x030B2A, 0x030B3C,
            ),
            "values": range(0xC000),
            "evidence": calibration_parser_invariant["proof"],
        },
        {
            "label": "calibration lookup-table reads",
            "base_register": "r1",
            "pcs": (
                0x030A3A, 0x030A44, 0x030A58, 0x030A60,
                0x030A7E, 0x030A86, 0x030A9C, 0x030AA4,
                0x030ACE, 0x030AD4, 0x030AEA, 0x030AF0,
            ),
            "values": range(0xC000),
            "evidence": calibration_parser_invariant["proof"],
        },
        {
            "label": "calibration scalar/grid reads",
            "base_register": "r1",
            "pcs": (0x030BAE, 0x030BBA, 0x030BD4, 0x030BEE),
            "values": range(0xC000),
            "evidence": calibration_lookup_invariant["proof"],
        },
        {
            "label": "bounded byte lookup reads",
            "base_register": "r4",
            "pcs": (0x036152, 0x036190),
            "values": range(0x100),
            "evidence": "r4 is rebuilt with MOVBZ from the byte-sized loop index.",
        },
        {
            "label": "bounded byte-derived record reads",
            "base_register": "r5",
            "pcs": (0x036160, 0x03619E),
            "values": range(0, 0xC00, 12),
            "evidence": "r5 is twelve times a byte read from the immutable lookup table.",
        },
        {
            "label": "bounded event-ring reads",
            "base_register": "r4",
            "pcs": (0x024EC4, 0x024EF8, 0x024F2C),
            "values": range(0x20),
            "evidence": "Each event-ring index is masked to five bits.",
        },
        {
            "label": "bounded event-dispatch reads",
            "base_register": "r10",
            "pcs": (0x024ED4, 0x024ED6, 0x024F08, 0x024F0A, 0x024F3C, 0x024F3E),
            "values": range(0xA410, 0xA810, 4),
            "evidence": "r10 is 0xA410 plus four times the byte-sized event identifier.",
        },
        {
            "label": "bounded lower scheduler reads",
            "base_register": "r7",
            "pcs": (0x007C40,),
            "values": range(0xF770, 0xF794, 2),
            "evidence": "The native loop scans exactly eighteen word countdown slots.",
        },
        {
            "label": "bounded lower scheduler reads",
            "base_register": "r7",
            "pcs": (0x007F06,),
            "values": range(0xF794, 0xF7D2, 2),
            "evidence": "The native loop scans exactly thirty-one word countdown slots.",
        },
        {
            "label": "bounded lower scheduler reads",
            "base_register": "r7",
            "pcs": (0x007F8C,),
            "values": range(0xF7D2, 0xF7F2, 2),
            "evidence": "The native loop scans exactly sixteen word countdown slots.",
        },
        {
            "label": "bounded lower callback-table reads",
            "base_register": "r4",
            "pcs": (0x007C50,),
            "values": range(0, 0x24, 2),
            "evidence": "The native loop bounds the callback-table word index below eighteen.",
        },
        {
            "label": "bounded lower callback-table reads",
            "base_register": "r4",
            "pcs": (0x007F16,),
            "values": range(0, 0x3E, 2),
            "evidence": "The native loop bounds the callback-table word index below thirty-one.",
        },
        {
            "label": "bounded lower callback-table reads",
            "base_register": "r4",
            "pcs": (0x007F9C,),
            "values": range(0, 0x20, 2),
            "evidence": "The native loop bounds the callback-table word index below sixteen.",
        },
        {
            "label": "bounded byte-indexed metadata reads",
            "base_register": "r4",
            "pcs": (
                0x035276, 0x03535E, 0x035378, 0x035390, 0x0353AC,
                0x0354B8, 0x0355B8, 0x035670,
            ),
            "values": range(0, 0x1000, 16),
            "evidence": "Each r4 value is sixteen times a byte-sized record identifier.",
        },
        {
            "label": "bounded immutable lookup reads",
            "base_register": "r5",
            "pcs": (0x035292,),
            "values": range(0x100),
            "evidence": "The search counter is explicitly stopped at 0xFF.",
        },
        {
            "label": "bounded record-bitset reads",
            "base_register": "r2",
            "pcs": (0x0354DC,),
            "values": range(0xF5F2, 0xF5FE, 2),
            "evidence": "The record loop is bounded below 0x5F before dividing the index by sixteen.",
        },
        {
            "label": "bounded record-bitset reads",
            "base_register": "r2",
            "pcs": (0x0354FC,),
            "values": range(0xF5FE, 0xF60A, 2),
            "evidence": "The record loop is bounded below 0x5F before dividing the index by sixteen.",
        },
        {
            "label": "bounded record-bitset reads",
            "base_register": "r2",
            "pcs": (0x0355DC,),
            "values": range(0xF60A, 0xF616, 2),
            "evidence": "The record loop is bounded below 0x5F before dividing the index by sixteen.",
        },
        {
            "label": "bounded computed-dispatch reads",
            "base_register": "r4",
            "pcs": (0x024D08,),
            "values": range(0xA53C, 0xA548, 2),
            "evidence": "The decoded immutable dispatch table has six word entries.",
        },
        {
            "label": "bounded computed-dispatch reads",
            "base_register": "r4",
            "pcs": (0x025750,),
            "values": range(0xA548, 0xA578, 2),
            "evidence": "The decoded immutable dispatch table has twenty-four word entries.",
        },
        {
            "label": "bounded computed-dispatch reads",
            "base_register": "r4",
            "pcs": (0x035E14,),
            "values": range(0xACB4, 0xACC2, 2),
            "evidence": "The decoded immutable dispatch table has seven word entries.",
        },
        {
            "label": "bounded computed-dispatch reads",
            "base_register": "r5",
            "pcs": (0x03606C,),
            "values": range(0xACC2, 0xACDA, 2),
            "evidence": "The decoded immutable dispatch table has twelve word entries.",
        },
        {
            "label": "bounded computed-dispatch reads",
            "base_register": "r4",
            "pcs": (0x036468,),
            "values": range(0xACDA, 0xACEC, 2),
            "evidence": "The decoded immutable dispatch table has nine word entries.",
        },
        {
            "label": "bounded computed-dispatch reads",
            "base_register": "r4",
            "pcs": (0x0368FE,),
            "values": range(0xACEC, 0xACFC, 2),
            "evidence": "The decoded immutable dispatch table has eight word entries.",
        },
        {
            "label": "bounded computed-dispatch reads",
            "base_register": "r4",
            "pcs": (0x036D6C,),
            "values": range(0xACFC, 0xAD0A, 2),
            "evidence": "The decoded immutable dispatch table has seven word entries.",
        },
        {
            "label": "bounded computed-dispatch reads",
            "base_register": "r1",
            "pcs": (0x038C28,),
            "values": range(0, 12, 2),
            "evidence": "The decoded immutable timer table has six word entries.",
        },
        {
            "label": "bounded computed-dispatch reads",
            "base_register": "r4",
            "pcs": (0x03926E,),
            "values": range(0xAF3E, 0xAF48, 2),
            "evidence": "The state byte is range-checked to four before indexing the five-word dispatch table.",
        },
        {
            "label": "background checksum source reads",
            "base_register": "r4",
            "pcs": (0x037464,),
            "values": range(0x4000),
            "evidence": "The source offset is masked to 14 bits after selecting its DPP flash page.",
        },
        {
            "label": "background checksum accumulator reads",
            "base_register": "r14",
            "pcs": (0x03746E, 0x037476, 0x037482),
            "values": crc_destinations,
            "exact_values": True,
            "evidence": crc_update_entry_gate["pointer_proof"],
        },
        {
            "label": "background checksum expected-value reads",
            "base_register": "r4",
            "pcs": (0x0375F6,),
            "values": checksum_expected_bases,
            "exact_values": True,
            "evidence": checksum_scan_invariant["proof"],
        },
        {
            "label": "background checksum table reads",
            "base_register": "r5",
            "pcs": (0x03761C,),
            "values": range(0x5E00, 0x5E20, 2),
            "evidence": checksum_scan_invariant["proof"],
        },
        {
            "label": "background checksum start reads",
            "base_register": "r4",
            "pcs": (0x037624,),
            "values": checksum_start_values,
            "exact_values": True,
            "evidence": checksum_scan_invariant["proof"],
        },
        {
            "label": "background checksum object reads",
            "base_register": "r4",
            "pcs": (0x037634, 0x03763C),
            "values": (checksum_table_words[0],),
            "exact_values": True,
            "evidence": checksum_scan_invariant["proof"],
        },
        {
            "label": "guarded F596 object reads",
            "base_register": "r9",
            "pcs": (
                0x03648C, 0x03649E, 0x0364AE,
                0x0364B4, 0x0364BE, 0x0364C4,
                0x0364CE, 0x0364D4,
            ),
            "values": f596_values,
            "exact_values": True,
            "evidence": guarded_slot_fixed_point_proof,
        },
        {
            "label": "bounded native response-buffer reads",
            "base_register": "r4",
            "pcs": (0x035DD2,),
            "values": range(0x100),
            "evidence": "The response-buffer index is byte-sized.",
        },
        {
            "label": "bounded six-byte state reads",
            "base_register": "r4",
            "pcs": (0x0361DA,),
            "values": range(6),
            "evidence": "The native loop stops after six byte entries.",
        },
        {
            "label": "bounded knock freeze-frame reads",
            "base_register": "r2",
            "pcs": (0x022506,),
            "values": range(0xD840, 0xDB80),
            "evidence": "The nested loops cover six channels of 64 bytes at 0xD840.",
        },
        {
            "label": "bounded record-ID reads",
            "base_register": "r4",
            "pcs": (0x0226DA, 0x0226E6),
            "values": range(10),
            "evidence": "The loop count is clamped to ten before indexing the native record-ID array.",
        },
        {
            "label": "bounded active-record payload reads",
            "base_register": "r5",
            "pcs": (0x022716,),
            "values": tuple(
                base + field
                for base in range(0xEA1A, 0xEE83, 12)
                for field in range(12)
            ),
            "exact_values": True,
            "evidence": record_identifier_invariant["proof"],
        },
        {
            "label": "bounded record-bitset reads",
            "base_register": "r4",
            "pcs": (0x022926,),
            "values": range(0, 12, 2),
            "evidence": "The loop scans exactly six native record-bitset words.",
        },
        {
            "label": "bounded stack-local reads",
            "base_register": "r0",
            "pcs": (
                0x02A568, 0x02A99E,
                0x02BDC6, 0x02BDEA, 0x02BE32, 0x02BEFC,
                0x02D908, 0x02D91A,
                0x0366BC,
            ),
            "values": range(0xFA00, 0xFA46, 2),
            "evidence": "These reads consume reserved r0 software-stack arguments or locals.",
        },
        {
            "label": "bounded FC50 stack-relative read",
            "base_register": "r0",
            "pcs": (0x0223C8,),
            "values": fc50_effective_bases,
            "exact_values": True,
            "evidence": fc50_stack_offset_invariant["proof"],
        },
        {
            "label": "bounded immutable pointer reads",
            "base_register": "r13",
            "pcs": (0x02AB7A,),
            "values": reference_word_values(image, 0x33E6, 1),
            "exact_values": True,
            "evidence": "r13 is loaded from the immutable reference-image word at 0x33E6.",
        },
        {
            "label": "bounded immutable pointer reads",
            "base_register": "r13",
            "pcs": (0x02AB90,),
            "values": reference_word_values(image, 0x33E8, 1),
            "exact_values": True,
            "evidence": "r13 is loaded from the immutable reference-image word at 0x33E8.",
        },
        {
            "label": "bounded calibration-pointer reads",
            "base_register": "r12",
            "pcs": (0x024578, 0x02459C, 0x0245DC, 0x02460E, 0x024748),
            "values": tuple(sorted({
                *reference_word_values(image, 0x33C0, 1),
                *reference_word_values(image, 0x361E, 1),
                *reference_word_values(image, 0x362C, 1),
                *reference_word_values(image, 0x33CA, 1),
                *reference_word_values(image, 0x33EE, 1),
            })),
            "exact_values": True,
            "evidence": "Each base is loaded from one immutable reference-image calibration pointer word.",
        },
        {
            "label": "bounded object-reset field reads",
            "base_register": "r4",
            "pcs": (0x02DA40,),
            "values": tuple(value + 0x56 for value in object_reset_bases),
            "exact_values": True,
            "evidence": object_reset_entry_gate["pointer_proof"],
        },
        {
            "label": "low serial-buffer writer",
            "base_register": "r7",
            "pcs": (0x005924,),
            "values": low_serial_destinations,
            "exact_values": True,
            "evidence": low_serial_entry_gate["pointer_proof"],
        },
        {
            "label": "high serial-buffer writer",
            "base_register": "r7",
            "pcs": (0x0218EA,),
            "values": high_serial_destinations,
            "exact_values": True,
            "evidence": high_serial_entry_gate["pointer_proof"],
        },
        {
            "label": "knock freeze-frame writer",
            "base_register": "r2",
            "pcs": (0x02EA86, 0x02EB70),
            "values": knock_freeze_destinations,
            "exact_values": True,
            "evidence": knock_task_entry_gate["pointer_proof"],
        },
        {
            "label": "low countdown callback object writer",
            "base_register": "r9",
            "pcs": (0x02800C, 0x028014, 0x02801C),
            "values": (0,),
            "exact_values": True,
            "evidence": countdown_callback_entry_gate["pointer_proof"],
        },
        {
            "label": "low countdown callback object reader",
            "base_register": "r9",
            "pcs": (0x028008,),
            "values": (0,),
            "exact_values": True,
            "evidence": countdown_callback_entry_gate["pointer_proof"],
        },
        {
            "label": "low countdown callback byte writer",
            "base_register": "r8",
            "pcs": (0x02801A,),
            "values": (2,),
            "exact_values": True,
            "evidence": countdown_callback_entry_gate["pointer_proof"],
        },
        {
            "label": "diagnostic receive/transmit pointer",
            "base_register": "r5",
            "pcs": (0x005630,),
            "values": e650_values,
            "exact_values": True,
            "evidence": guarded_slot_fixed_point_proof,
        },
        {
            "label": "diagnostic receive/transmit pointer",
            "base_register": "r4",
            "pcs": (0x039022, 0x039150),
            "values": e650_values,
            "exact_values": True,
            "evidence": guarded_slot_fixed_point_proof,
        },
        {
            "label": "diagnostic response pointer",
            "base_register": "r4",
            "pcs": (
                0x0056E4,
                0x02CB16,
                0x02CB36,
                0x02CD4A,
                0x02CD50,
                0x02CD5E,
            ),
            "values": e65a_values,
            "exact_values": True,
            "evidence": guarded_slot_fixed_point_proof,
        },
        {
            "label": "diagnostic response pointer",
            "base_register": "r2",
            "pcs": (0x03919E,),
            "values": e65a_values,
            "exact_values": True,
            "evidence": guarded_slot_fixed_point_proof,
        },
        {
            "label": "diagnostic receive/transmit pointer",
            "base_register": "r4",
            "pcs": (0x0365A0,),
            "values": e650_values,
            "exact_values": True,
            "evidence": guarded_slot_fixed_point_proof,
        },
        {
            "label": "diagnostic receive/transmit pointer",
            "base_register": "r2",
            "pcs": (0x0365A8,),
            "values": e650_values,
            "exact_values": True,
            "evidence": guarded_slot_fixed_point_proof,
        },
        {
            "label": "CAN status pointer",
            "base_register": "r4",
            "pcs": (0x025064, 0x0251AA, 0x0251B0),
            "values": f010_values,
            "exact_values": True,
            "evidence": guarded_slot_fixed_point_proof,
        },
        {
            "label": "CAN status pointer",
            "base_register": "r5",
            "pcs": (0x0255B8,),
            "values": f00e_values,
            "exact_values": True,
            "evidence": guarded_slot_fixed_point_proof,
        },
        {
            "label": "CAN status pointer",
            "base_register": "r4",
            "pcs": (0x02555A, 0x025578, 0x025596),
            "values": f00e_values,
            "exact_values": True,
            "evidence": guarded_slot_fixed_point_proof,
        },
        {
            "label": "F596-selected native object",
            "base_register": "r9",
            "pcs": f596_r9_write_pcs,
            "values": f596_values,
            "exact_values": True,
            "evidence": guarded_slot_fixed_point_proof,
        },
        {
            "label": "F596-selected native object field",
            "base_register": "r4",
            "pcs": (0x02D31E, 0x02D37C),
            "values": tuple(value + 0x6E for value in f596_values),
            "exact_values": True,
            "evidence": guarded_slot_fixed_point_proof,
        },
        {
            "label": "context-indexed interrupt lookup table",
            "base_register": "r12",
            "pcs": (0x001D40, 0x001DA6, 0x001E46),
            "values": range(0, 12, 2),
            "evidence": context_index_invariant["proof"],
        },
        {
            "label": "context-indexed interrupt lookup table",
            "base_register": "r14",
            "pcs": (0x001E14,),
            "values": range(0, 12, 2),
            "evidence": context_index_invariant["proof"],
        },
        {
            "label": "context-indexed interrupt byte",
            "base_register": "r0",
            "pcs": (0x001D44,),
            "values": interrupt_lookup_destinations_a,
            "exact_values": True,
            "evidence": "The immutable 0xB11C table contains the six exact byte pointers.",
        },
        {
            "label": "context-indexed interrupt byte",
            "base_register": "r0",
            "pcs": (0x001E18,),
            "values": interrupt_lookup_destinations_b,
            "exact_values": True,
            "evidence": "The immutable 0xB128 table contains the six exact byte pointers.",
        },
        {
            "label": "paired interrupt descriptor",
            "base_register": "r0",
            "pcs": (0x001D7C,),
            "values": (0xFA46, 0xFA52),
            "exact_values": True,
            "evidence": "The immediately preceding branch selects exactly 0xFA46 or 0xFA52.",
        },
        {
            "label": "context-indexed interrupt coefficient",
            "base_register": "r0",
            "pcs": (0x001DAE,),
            "values": coefficient_destinations_a,
            "exact_values": True,
            "evidence": "The immutable 0xB134 table contains the six exact offsets.",
        },
        {
            "label": "context-indexed interrupt coefficient",
            "base_register": "r0",
            "pcs": (0x001E4E,),
            "values": coefficient_destinations_b,
            "exact_values": True,
            "evidence": "The immutable 0xB140 table contains the six exact offsets.",
        },
        {
            "label": "six-context interrupt calibration",
            "base_register": "r13",
            "pcs": (0x001D90, 0x001EAA),
            "values": range(6),
            "evidence": context_index_invariant["proof"],
        },
        {
            "label": "context-indexed coefficient writer",
            "base_register": "r0",
            "pcs": (0x001DBA,),
            "values": coefficient_destinations_a,
            "exact_values": True,
            "evidence": (
                "r0 is one of the six reference-image 0xB134 words selected by "
                "the proven 0..5 context index."
            ),
        },
        {
            "label": "context-indexed coefficient writer",
            "base_register": "r0",
            "pcs": (0x001E5A,),
            "values": coefficient_destinations_b,
            "exact_values": True,
            "evidence": (
                "r0 is one of the six reference-image 0xB140 words selected by "
                "the proven 0..5 context index."
            ),
        },
        {
            "label": "timer ISR destination table",
            "base_register": "r4",
            "pcs": (0x038C2C, 0x038C3C),
            "values": timer_destinations,
            "exact_values": True,
            "evidence": (
                "r4 is one of six reference-image 0xADF4 words selected by the "
                "proven 0..5 context index."
            ),
        },
        {
            "label": "serial ISR destination table",
            "base_register": "r2",
            "pcs": (0x039A24,),
            "values": serial_destinations,
            "exact_values": True,
            "evidence": (
                "r2 is one of six reference-image 0xB166 words selected by the "
                "proven 0..5 context index."
            ),
        },
        {
            "label": "serial ISR destination table",
            "base_register": "r2",
            "pcs": (0x039A36,),
            "values": tuple(sorted({(value + 2) & 0xFFFF for value in serial_destinations})),
            "exact_values": True,
            "evidence": "r2 is the reference-image 0xB166 table word plus two.",
        },
        {
            "label": "bounded serial ISR channel reads",
            "base_register": "r0",
            "pcs": (0x03991E, 0x039926, 0x03998A),
            "values": range(6),
            "evidence": "The interrupt channel selector is constrained to the six native serial channels.",
        },
        {
            "label": "bounded serial ISR channel-table reads",
            "base_register": "r0",
            "pcs": (0x039930,),
            "values": range(0, 12, 2),
            "evidence": "The six-channel interrupt selector is doubled before indexing the word table.",
        },
        {
            "label": "bounded 16-entry interrupt-ring reads",
            "base_register": "r0",
            "pcs": (0x0399D8,),
            "values": range(16),
            "evidence": ring_index_invariant["proof"],
        },
        {
            "label": "bounded 16-entry interrupt-ring destination reads",
            "base_register": "r0",
            "pcs": (0x039A20,),
            "values": range(0, 32, 2),
            "evidence": ring_index_invariant["proof"],
        },
    )
    instruction_positions = {
        item["pc"]: index for index, item in enumerate(instructions)
    }
    computed_entry_pcs = {
        int(item["file_target"], 16) for item in table_targets
    }
    for rule in proven_pointer_rules:
        derivation = rule.get("derivation")
        if derivation not in {"movbz", "movbz_x2"}:
            continue
        for pc in rule["pcs"]:
            segment_dispatch_proven = (
                segment2_dispatch_proven
                if 0x020000 <= pc < 0x030000
                else segment3_dispatch_proven
                if 0x030000 <= pc < 0x040000
                else False
            )
            if not segment_dispatch_proven:
                raise RuntimeError("local MOVBZ proof requires complete segment dispatch")
            previous = instructions[instruction_positions[pc] - 1]
            if (
                previous["pc"] + previous["size"] != pc
                or pc in branch_targets
                or pc in computed_entry_pcs
            ):
                raise RuntimeError(f"local MOVBZ entry proof failed at 0x{pc:06X}")
            initializer = previous
            if derivation == "movbz_x2":
                initializer = instructions[instruction_positions[pc] - 2]
                if (
                    initializer["pc"] + initializer["size"] != previous["pc"]
                    or previous["pc"] in branch_targets
                    or previous["pc"] in computed_entry_pcs
                    or previous["mnemonic"] != "shl"
                    or previous["operands"] != [rule["base_register"], "#0x1"]
                ):
                    raise RuntimeError(f"local MOVBZ doubling proof failed at 0x{pc:06X}")
            if (
                initializer["mnemonic"] != "movbz"
                or not initializer["operands"]
                or initializer["operands"][0] != rule["base_register"]
            ):
                raise RuntimeError(f"local MOVBZ range proof failed at 0x{pc:06X}")
    byte_indexed_state_entry_gate = stock_entry_gate(
        instructions,
        table_targets,
        0x02E4F2,
        0x02EDA2,
        {0x02E4F2},
    )
    r6_assignments = {
        item["pc"]
        for item in instructions
        if 0x02E4F2 <= item["pc"] < 0x02EDA0
        and item["operands"]
        and item["operands"][0] == "r6"
    }
    if (
        not entry_gate_proven
        or byte_indexed_state_entry_gate["status"] != "proven for the stock image"
        or {
            int(edge["pc"], 16)
            for edge in byte_indexed_state_entry_gate["external_direct_entries"]
        }
        != {0x030FDA}
        or r6_assignments != {0x02E50A}
        or instructions_by_pc[0x02E50A]["instruction"] != "movbz r6,RL5"
    ):
        raise RuntimeError(
            "byte-indexed state entry-path proof no longer holds: "
            + json.dumps(byte_indexed_state_entry_gate, sort_keys=True)
        )
    byte_indexed_state_entry_gate["pointer_proof"] = (
        "The sole external entry reaches MOVBZ r6,RL5 at 0x02E50A; r6 is "
        "callee-saved and has no later assignment before the function epilogue."
    )
    five_record_loop_entry_gate = stock_entry_gate(
        instructions,
        table_targets,
        0x03147A,
        0x031518,
        {0x03147A},
    )
    if (
        not segment3_dispatch_proven
        or five_record_loop_entry_gate["status"] != "proven for the stock image"
        or {
            int(edge["pc"], 16)
            for edge in five_record_loop_entry_gate["external_direct_entries"]
        }
        != {0x02C3DC}
        or instructions_by_pc[0x0314E8]["instruction"] != "mov r9,#0x1"
        or instructions_by_pc[0x031512]["instruction"] != "cmp r9,#0x6"
    ):
        raise RuntimeError(
            "five-record loop entry-path proof no longer holds: "
            + json.dumps(five_record_loop_entry_gate, sort_keys=True)
        )
    five_record_loop_entry_gate["pointer_proof"] = (
        "The sole entry initializes r9=1; the loop writes through r5=r9*2 "
        "and continues only while r9<6."
    )
    object_subroutine_entry_gate = stock_entry_gate(
        instructions,
        table_targets,
        0x02FF6E,
        0x02FFC0,
        {0x02FF6E},
    )
    if (
        not segment2_dispatch_proven
        or object_subroutine_entry_gate["status"] != "proven for the stock image"
        or {
            int(edge["pc"], 16)
            for edge in object_subroutine_entry_gate["external_direct_entries"]
        }
        != {0x02F37E}
        or instructions_by_pc[0x02F37C]["instruction"] != "mov r12,r9"
        or instructions_by_pc[0x02FF70]["instruction"] != "mov r9,r12"
    ):
        raise RuntimeError(
            "object subroutine entry-path proof no longer holds: "
            + json.dumps(object_subroutine_entry_gate, sort_keys=True)
        )
    object_subroutine_entry_gate["pointer_proof"] = (
        "The sole caller passes the proven FUN_02b0cc r9 object through r12; "
        "the entry copies r12 into r9."
    )
    object_bridge_entry_gate = stock_entry_gate(
        instructions,
        table_targets,
        0x028880,
        0x0288CC,
        {0x028880},
    )
    object_metric_entry_gate = stock_entry_gate(
        instructions,
        table_targets,
        0x02FE08,
        0x02FEDE,
        {0x02FE08},
    )
    if (
        not segment2_dispatch_proven
        or object_bridge_entry_gate["status"] != "proven for the stock image"
        or {
            int(edge["pc"], 16)
            for edge in object_bridge_entry_gate["external_direct_entries"]
        }
        != {0x028740}
        or object_metric_entry_gate["status"] != "proven for the stock image"
        or {
            int(edge["pc"], 16)
            for edge in object_metric_entry_gate["external_direct_entries"]
        }
        != {0x0288A6, 0x02F3D8, 0x02F6FE}
        or instructions_by_pc[0x02873C]["instruction"] != "mov r12,r8"
        or instructions_by_pc[0x028888]["instruction"] != "mov r7,r12"
        or instructions_by_pc[0x0288A0]["instruction"] != "mov r12,r7"
        or instructions_by_pc[0x02F3D2]["instruction"] != "mov r12,r9"
        or instructions_by_pc[0x02F6F8]["instruction"] != "mov r12,r8"
        or instructions_by_pc[0x02FE10]["instruction"] != "mov r8,r12"
    ):
        raise RuntimeError(
            "object metric entry-path proof no longer holds: "
            + json.dumps(
                {
                    "bridge": object_bridge_entry_gate,
                    "metric": object_metric_entry_gate,
                },
                sort_keys=True,
            )
        )
    object_bridge_entry_gate["pointer_proof"] = (
        "The sole caller passes the proven FUN_02853c r8 object through r12; "
        "the bridge preserves it in r7 and passes it onward through r12."
    )
    object_metric_entry_gate["pointer_proof"] = (
        "The bridge, FUN_02b0cc, and FUN_02f6f0 callers each pass the proven "
        "0xF018/0xF0C4 object through r12; the entry copies r12 into r8."
    )
    two_record_entry_gate = stock_entry_gate(
        instructions,
        table_targets,
        0x03CF54,
        0x03D052,
        {0x03CF54},
    )
    two_record_call_sites = {0x03D4E0, 0x03D4F0, 0x03D514, 0x03D5C8}
    if (
        not segment3_dispatch_proven
        or two_record_entry_gate["status"] != "proven for the stock image"
        or {
            int(edge["pc"], 16)
            for edge in two_record_entry_gate["external_direct_entries"]
        }
        != two_record_call_sites
        or any(
            instructions_by_pc[pc - 4]["instruction"]
            not in {"mov r12,#0xefb4", "mov r12,#0xefc0"}
            for pc in two_record_call_sites
        )
        or instructions_by_pc[0x03CF58]["instruction"] != "mov r8,r12"
    ):
        raise RuntimeError(
            "two-record entry-path proof no longer holds: "
            + json.dumps(two_record_entry_gate, sort_keys=True)
        )
    two_record_entry_gate["pointer_proof"] = (
        "Every caller loads r12 with immediate 0xEFB4 or 0xEFC0; "
        "the entry copies r12 into r8."
    )
    paired_object_update_entry_gate = stock_entry_gate(
        instructions,
        table_targets,
        0x02FD6E,
        0x02FE08,
        {0x02FD6E},
    )
    paired_object_call_sites = {0x02FA2E, 0x02FA58}
    if (
        not entry_gate_proven
        or paired_object_update_entry_gate["status"] != "proven for the stock image"
        or {
            int(edge["pc"], 16)
            for edge in paired_object_update_entry_gate["external_direct_entries"]
        }
        != paired_object_call_sites
        or any(
            instructions_by_pc[pc - 2]["instruction"] != "mov r12,r9"
            for pc in paired_object_call_sites
        )
    ):
        raise RuntimeError(
            "paired-object update entry-path proof no longer holds: "
            + json.dumps(paired_object_update_entry_gate, sort_keys=True)
        )
    paired_object_update_entry_gate["pointer_proof"] = (
        "Both callers pass the proven FUN_02b0cc r9 value through r12; "
        "the entry copies r12 to r8."
    )
    object_pointer_call_sites = {
        0x02F312,
        0x02F32E,
        0x02F344,
        0x02F3B8,
        0x02F29C,
        0x02F2B2,
        0x02F2D0,
        0x02F2DC,
    }
    observed_object_pointer_calls = {
        int(edge["pc"], 16)
        for gate in object_pointer_entry_gates.values()
        for edge in gate["external_direct_entries"]
    }
    if any(
        item["status"] != "proven for the stock image"
        for item in object_pointer_entry_gates.values()
    ) or observed_object_pointer_calls != object_pointer_call_sites or any(
        instructions_by_pc[pc - 2]["instruction"] != "mov r12,r9"
        for pc in object_pointer_call_sites
    ):
        raise RuntimeError(
            "object-pointer entry-path proof no longer holds: "
            + json.dumps(object_pointer_entry_gates, sort_keys=True)
        )
    for item in object_pointer_entry_gates.values():
        item["argument_proof"] = "every external call is immediately preceded by mov r12,r9"
    implicit = implicit_ownership(controls)
    def stock_live(item: dict) -> bool:
        pc = int(item["pc"], 16)
        return (
            (pc < 0x20000 and pc in reachable_pcs)
            or pc in high_reachable_pcs
        )

    live_direct = [item for item in direct if stock_live(item)]
    live_resolved = [item for item in resolved if stock_live(item)]
    live_unresolved = [item for item in unresolved if stock_live(item)]
    logger_accesses = [
        {"address": item["address"], "width": item["width"]} for item in logger
    ]
    unresolved_writes = [
        item for item in unresolved if item["kind"] in {"write", "read/write"}
    ]
    r0_predecrement_writes = [
        item
        for item in unresolved_writes
        if item["base_register"] == "r0" and item["operand"] == "[-r0]"
    ]
    other_unresolved_writes = [
        item
        for item in unresolved_writes
        if not (item["base_register"] == "r0" and item["operand"] == "[-r0]")
    ]
    reachable_unmapped_writes = [
        item
        for item in other_unresolved_writes
        if item["function"] == "<unmapped>"
        and item["direct_control_flow_reachable"]
    ]
    owner_attribution = [
        {
            **item,
            "owners": sorted(
                control_flow_owners.get(int(item["pc"], 16), set())
            ),
        }
        for item in reachable_unmapped_writes
    ]
    single_owner_writes = []
    for item in other_unresolved_writes:
        owners = (
            {item["function"]}
            if item["function"] != "<unmapped>"
            else control_flow_owners.get(int(item["pc"], 16), set())
        )
        if len(owners) == 1:
            single_owner_writes.append((next(iter(owners)), item))

    def bounded_pointer_record(rule: dict, owner: str, item: dict) -> dict:
        indirect = INDIRECT_RE.match(item["operand"])
        if not indirect:
            raise RuntimeError("proven pointer rule matched a non-pointer operand")
        offset = int(indirect["offset"], 16) if indirect["offset"] else 0
        if indirect["pre"]:
            offset -= item["width"]
        effective_values = [
            (value + offset) & 0xFFFF for value in rule["values"]
        ]
        compact = len(effective_values) <= 8
        enumerate_values = compact or rule.get("exact_values", False)
        stride = (
            effective_values[1] - effective_values[0]
            if len(effective_values) > 1
            else 0
        )
        regular = all(
            right - left == stride
            for left, right in zip(effective_values, effective_values[1:])
        )
        envelope_start = min(effective_values)
        envelope_end = max(effective_values) + item["width"] - 1
        return {
            **item,
            "owner": owner,
            "base_values": (
                [f"0x{value:04X}" for value in rule["values"]]
                if enumerate_values
                else []
            ),
            "base_value_summary": (
                ", ".join(f"0x{value:04X}" for value in rule["values"])
                if compact
                else (
                    f"{len(rule['values'])} exact values in "
                    f"0x{rule['values'][0]:04X}-0x{rule['values'][-1]:04X}"
                    if enumerate_values
                    else f"0x{rule['values'][0]:04X}-0x{rule['values'][-1]:04X}"
                )
            ),
            "effective_addresses": (
                [f"0x{value:04X}" for value in effective_values]
                if enumerate_values
                else []
            ),
            "effective_address_summary": (
                ", ".join(f"0x{value:04X}" for value in effective_values)
                if compact
                else (
                    f"0x{envelope_start:04X}-0x{envelope_end:04X}"
                    + (f" (stride {stride} starts)" if stride > 1 else "")
                )
            ),
            "effective_address_envelope": [
                f"0x{envelope_start:04X}",
                f"0x{envelope_end:04X}",
            ],
            "guarded_pointer_slot_hits": [
                f"0x{slot:04X}"
                for slot in guarded_pointer_slots
                if any(
                    value < slot + 2
                    and value + item["width"] > slot
                    for value in effective_values
                )
            ],
            "regular": regular,
            "evidence": rule["evidence"],
        }

    bounded_value_set_writes = []
    for rule in proven_pointer_rules:
        if "owner" not in rule and "pcs" not in rule:
            raise RuntimeError("owner-free pointer rules require explicit PCs")
        candidates = (
            single_owner_writes
            if "owner" in rule
            else [(rule["label"], item) for item in other_unresolved_writes]
        )
        for owner, item in candidates:
            if (
                ("owner" in rule and owner != rule["owner"])
                or item["base_register"] != rule["base_register"]
                or (
                    "pcs" in rule
                    and int(item["pc"], 16) not in rule["pcs"]
                )
            ):
                continue
            bounded_value_set_writes.append(
                bounded_pointer_record(rule, owner, item)
            )
    bounded_pcs = [item["pc"] for item in bounded_value_set_writes]
    if len(bounded_pcs) != len(set(bounded_pcs)):
        raise RuntimeError("a proven pointer write site matched more than one rule")
    if any(
        not item["effective_addresses"] and not item["regular"]
        for item in bounded_value_set_writes
    ):
        raise RuntimeError("large proven pointer value sets must be regular")
    bounded_pc_set = set(bounded_pcs)
    bounded_write_keys = {
        (item["pc"], item["operand"], item["kind"])
        for item in bounded_value_set_writes
    }
    stack_accesses = [
        item
        for item in live_unresolved
        if item["base_register"] == "r0"
        and item["operand"] in {"[-r0]", "[r0+]"}
    ]
    stack_access_keys = {
        (item["pc"], item["operand"], item["kind"])
        for item in stack_accesses
    }
    startup_self_test_accesses = [
        item
        for item in live_unresolved
        if 0x0044C8 <= int(item["pc"], 16) < 0x0046BE
    ]
    startup_self_test_access_keys = {
        (item["pc"], item["operand"], item["kind"])
        for item in startup_self_test_accesses
    }
    if len(startup_self_test_access_keys) != 40:
        raise RuntimeError("startup RAM self-test access set changed")
    other_live_unresolved_accesses = [
        item
        for item in live_unresolved
        if (item["pc"], item["operand"], item["kind"])
        not in bounded_write_keys
        | stack_access_keys
        | startup_self_test_access_keys
    ]
    single_owner_accesses = []
    for item in other_live_unresolved_accesses:
        owners = (
            {item["function"]}
            if item["function"] != "<unmapped>"
            else control_flow_owners.get(int(item["pc"], 16), set())
        )
        if len(owners) == 1:
            single_owner_accesses.append((next(iter(owners)), item))
    proven_pointer_access_keys = set()
    duplicate_pointer_access_keys = set()
    bounded_value_set_read_accesses = []
    for rule in proven_pointer_rules:
        candidates = (
            single_owner_accesses
            if "owner" in rule
            else [
                (rule["label"], item)
                for item in other_live_unresolved_accesses
            ]
        )
        for owner, item in candidates:
            if (
                ("owner" in rule and owner != rule["owner"])
                or item["base_register"] != rule["base_register"]
                or (
                    "pcs" in rule
                    and int(item["pc"], 16) not in rule["pcs"]
                )
            ):
                continue
            key = (item["pc"], item["operand"], item["kind"])
            if key in proven_pointer_access_keys:
                duplicate_pointer_access_keys.add(key)
            proven_pointer_access_keys.add(key)
            bounded_value_set_read_accesses.append(
                bounded_pointer_record(rule, owner, item)
            )
    if duplicate_pointer_access_keys:
        raise RuntimeError(
            "a pointer access matched more than one rule: "
            + json.dumps(sorted(duplicate_pointer_access_keys))
        )
    if any(
        not item["effective_addresses"] and not item["regular"]
        for item in bounded_value_set_read_accesses
    ):
        raise RuntimeError("large proven pointer read value sets must be regular")
    collision_relevant_unbounded_accesses = [
        {
            **item,
            "owners": sorted(
                {item["function"]}
                if item["function"] != "<unmapped>"
                else control_flow_owners.get(int(item["pc"], 16), set())
            ),
        }
        for item in other_live_unresolved_accesses
        if (item["pc"], item["operand"], item["kind"])
        not in proven_pointer_access_keys
    ]
    unreachable_low_writes = [
        item
        for item in other_unresolved_writes
        if item["pc"] not in bounded_pc_set
        and int(item["pc"], 16) in nonexecutable_low_pcs
    ]
    unreachable_high_writes = [
        item
        for item in other_unresolved_writes
        if item["pc"] not in bounded_pc_set
        and int(item["pc"], 16) >= 0x20000
        and not stock_live(item)
    ]
    live_unbounded_writes = [
        item
        for item in other_unresolved_writes
        if item["pc"] not in bounded_pc_set and stock_live(item)
    ]
    guarded_slot_resolved_hits = [
        item
        for item in resolved
        if stock_live(item)
        and item["kind"] in {"write", "read/write"}
        and any(
            int(item["address"], 16) < slot + 2
            and int(item["address"], 16) + item["width"] > slot
            for slot in guarded_pointer_slots
        )
    ]
    all_guarded_slot_bounded_hits = [
        {
            "pc": item["pc"],
            "instruction": item["instruction"],
            "slots": item["guarded_pointer_slot_hits"],
        }
        for item in bounded_value_set_writes
        if item["guarded_pointer_slot_hits"] and stock_live(item)
    ]
    startup_guarded_slot_bounded_hits = [
        item
        for item in all_guarded_slot_bounded_hits
        if int(item["pc"], 16) in startup_guarded_slot_write_pcs
    ]
    guarded_slot_bounded_hits = [
        item
        for item in all_guarded_slot_bounded_hits
        if int(item["pc"], 16) not in startup_guarded_slot_write_pcs
    ]
    if (
        {
            int(item["pc"], 16)
            for item in startup_guarded_slot_bounded_hits
        }
        != startup_guarded_slot_write_pcs
        or
        live_unbounded_writes
        or guarded_slot_resolved_hits
        or guarded_slot_bounded_hits
    ):
        raise RuntimeError(
            "guarded native pointer-slot fixed point failed: "
            + json.dumps(
                {
                    "live_unbounded": live_unbounded_writes,
                    "resolved_slot_hits": guarded_slot_resolved_hits,
                    "bounded_slot_hits": guarded_slot_bounded_hits,
                },
                sort_keys=True,
            )
        )
    guarded_pointer_slot_invariant = {
        "status": "proven for the stock image",
        "slots": {
            f"0x{slot:04X}": {
                "boot_value": f"0x{guarded_slot_boot_values[slot]:04X}",
                "direct_writers": sorted(
                    guarded_slot_direct_writers[slot]
                ),
                "proven_values": (
                    [f"0x{value:04X}" for value in f00e_values]
                    if slot == 0xF00E
                    else [f"0x{value:04X}" for value in f010_values]
                    if slot == 0xF010
                    else [f"0x{value:04X}" for value in f596_values]
                    if slot == 0xF596
                    else "bounded native diagnostic-buffer cursor"
                ),
            }
            for slot in guarded_pointer_slots
        },
        "remaining_stock_live_unbounded_writes": [],
        "resolved_indirect_slot_hits": [],
        "bounded_indirect_slot_hits": [],
        "pre_runtime_writers": startup_guarded_slot_bounded_hits,
        "proof": guarded_slot_fixed_point_proof,
        "limit": (
            "Exact stock image and its normal counter/state-machine invariants; "
            "corrupted RAM or modified native code is outside this proof."
        ),
    }
    bounded_value_set_accesses = [
        {
            "address": (
                address
                if address is not None
                else item["effective_address_envelope"][0]
            ),
            "width": (
                item["width"]
                if address is not None
                else (
                    int(item["effective_address_envelope"][1], 16)
                    - int(item["effective_address_envelope"][0], 16)
                    + 1
                )
            ),
            "kind": item["kind"],
            "method": "proven pointer value set",
            "pc": item["pc"],
            "function": item["owner"],
            "source": item["source"],
            "line": item["line"],
            "operand": item["operand"],
            "instruction": item["instruction"],
        }
        for item in (*bounded_value_set_writes, *bounded_value_set_read_accesses)
        for address in (item["effective_addresses"] or [None])
    ]

    def overlaps_access(item: dict, start: int, end: int) -> bool:
        exact = item.get("effective_addresses", [])
        if exact:
            return any(
                int(address, 16) < end
                and int(address, 16) + item["width"] > start
                for address in exact
            )
        if envelope := item.get("effective_address_envelope"):
            low, high = int(envelope[0], 16), int(envelope[1], 16) + 1
        else:
            low = int(item["address"], 16)
            high = low + item["width"]
        return low < end and high > start

    def post_startup_stock_writes(start: int, end: int) -> list[dict]:
        return [
            item
            for item in (*direct, *resolved, *bounded_value_set_writes)
            if item["kind"] in {"write", "read/write"}
            and stock_live(item)
            and not 0x0044C8 <= int(item["pc"], 16) < 0x0046BE
            and overlaps_access(item, start, end)
        ]

    dpp3_writes = post_startup_stock_writes(0xFE06, 0xFE08)
    dpp3_stock_live_accesses = [
        item
        for item in (*direct, *resolved)
        if stock_live(item) and overlaps_access(item, 0xFE06, 0xFE08)
    ]
    dpp3_unreachable_write_decodes = [
        item
        for item in direct
        if item["kind"] in {"write", "read/write"}
        and not stock_live(item)
        and overlaps_access(item, 0xFE06, 0xFE08)
    ]
    if dpp3_writes or collision_relevant_unbounded_accesses:
        raise RuntimeError(
            "DPP3 page invariant is not closed: "
            + json.dumps(
                {
                    "writes": dpp3_writes,
                    "unbounded": collision_relevant_unbounded_accesses,
                },
                sort_keys=True,
            )
        )
    dpp3_invariant = {
        "status": "proven for the exact stock image",
        "logical_window": "0xC000-0xFFFF",
        "reset_page": "0x0003",
        "stock_live_accesses": dpp3_stock_live_accesses,
        "post_startup_stock_live_writes": [],
        "stock_unreachable_write_decodes": dpp3_unreachable_write_decodes,
        "manual_evidence": (
            "C166 user manual PDF page 263 (printed B-4): DPP3 at 0xFE06 "
            "resets to 0x0003; PDF page 123 states SFRs reside in data page 3."
        ),
        "proof": (
            "The exact firmware has no stock-live direct, resolved-indirect, "
            "or bounded-indirect write to DPP3, and no collision-relevant "
            "unbounded access remains. DPP3 therefore stays on reset page 3."
        ),
        "limit": (
            "A patch that writes DPP3 invalidates this logical-to-physical "
            "address proof and must be re-audited."
        ),
    }

    pec_control_registers = {
        3: 0xFEC6,
        4: 0xFEC8,
        5: 0xFECA,
        6: 0xFECC,
    }
    inactive_pec_channels = []
    for channel, control in pec_control_registers.items():
        source = 0xFDE0 + 4 * channel
        destination = source + 2
        control_writes = post_startup_stock_writes(control, control + 2)
        pointer_writes = post_startup_stock_writes(source, destination + 2)
        startup_pointer_test_writes = [
            item
            for item in bounded_value_set_writes
            if stock_live(item)
            and 0x0044C8 <= int(item["pc"], 16) < 0x0046BE
            and overlaps_access(item, source, destination + 2)
        ]
        if control_writes or pointer_writes:
            raise RuntimeError(
                f"PEC{channel} inactivity invariant is not closed: "
                + json.dumps(
                    {
                        "control_writes": control_writes,
                        "pointer_writes": pointer_writes,
                    },
                    sort_keys=True,
                )
            )
        inactive_pec_channels.append(
            {
                "channel": channel,
                "control_register": f"0x{control:04X}",
                "source_pointer": f"0x{source:04X}",
                "destination_pointer": f"0x{destination:04X}",
                "reset_control": "0x0000",
                "post_startup_control_writes": [],
                "post_startup_pointer_writes": [],
                "startup_ram_test_write_sites": len(
                    startup_pointer_test_writes
                ),
            }
        )
    inactive_pec_controls = set(pec_control_registers.values())
    pec_unreachable_control_write_decodes = [
        item
        for item in direct
        if item["kind"] in {"write", "read/write"}
        and not stock_live(item)
        and any(
            overlaps_access(item, address, address + 2)
            for address in inactive_pec_controls
        )
    ]
    pec_inactive_invariant = {
        "status": "proven for the exact stock image",
        "channels": inactive_pec_channels,
        "stock_unreachable_control_write_decodes": (
            pec_unreachable_control_write_decodes
        ),
        "manual_evidence": (
            "C166 user manual PDF page 265 (printed B-6) gives PECC3-PECC6 "
            "reset value 0x0000; PDF page 27 states that when the transfer "
            "counter reaches zero, service proceeds through the standard "
            "interrupt rather than a PEC transfer."
        ),
        "proof": (
            "Channels 3-6 reset inactive and have no post-startup stock-live "
            "write to their PECC control or source/destination pointer words."
        ),
        "limit": (
            "A patch that reprograms PECC3-PECC6 or their pointer words "
            "invalidates this inactivity proof."
        ),
    }

    r0_postincrement_reads = [
        item
        for item in unresolved
        if item["kind"] in {"read", "read/write"}
        and item["base_register"] == "r0"
        and item["operand"] == "[r0+]"
    ]
    r0_stack = {
        **R0_STACK_CONTEXT_EVIDENCE,
        "predecrement_write_sites": len(r0_predecrement_writes),
        "postincrement_read_sites": len(r0_postincrement_reads),
        "ordinary_direct_claims_inside_arena": sum(
            int(item["address"], 16) < 0xFA46
            and int(item["address"], 16) + item["width"] > 0xFA00
            for item in direct
        ),
        "logger_claims_inside_arena": sum(
            int(item["address"], 16) < 0xFA46
            and int(item["address"], 16) + item["width"] > 0xFA00
            for item in logger
        ),
        "other_unresolved_write_sites": (
            len(other_unresolved_writes)
        ),
    }
    unresolved_write_investigation = {
        "sites_after_stack_exclusion": len(other_unresolved_writes),
        "sites_bounded_by_proven_value_sets": len(bounded_value_set_writes),
        "remaining_unbounded_sites": (
            len(other_unresolved_writes) - len(bounded_value_set_writes)
        ),
        "stock_unreachable_high_sites": len(unreachable_high_writes),
        "collision_relevant_unbounded_sites": len(live_unbounded_writes),
        "inside_named_function_bodies": sum(
            item["function"] != "<unmapped>" for item in other_unresolved_writes
        ),
        "outside_named_function_bodies": sum(
            item["function"] == "<unmapped>" for item in other_unresolved_writes
        ),
        "outside_named_but_directly_reachable": sum(
            item["function"] == "<unmapped>"
            and item["direct_control_flow_reachable"]
            for item in other_unresolved_writes
        ),
        "outside_named_and_not_directly_reached": sum(
            item["function"] == "<unmapped>"
            and not item["direct_control_flow_reachable"]
            for item in other_unresolved_writes
        ),
        "reachable_unmapped_owner_attribution": {
            "sites": len(owner_attribution),
            "site_records": owner_attribution,
            "single_owner_sites": sum(
                len(item["owners"]) == 1 for item in owner_attribution
            ),
            "shared_owner_sites": sum(
                len(item["owners"]) > 1 for item in owner_attribution
            ),
            "unattributed_sites": sum(
                not item["owners"] for item in owner_attribution
            ),
            "by_owner": [
                {"owner": owner, "writes": count}
                for owner, count in Counter(
                    owner
                    for item in owner_attribution
                    for owner in item["owners"]
                ).most_common()
            ],
            "by_single_owner": [
                {"owner": owner, "writes": count}
                for owner, count in Counter(
                    item["owners"][0]
                    for item in owner_attribution
                    if len(item["owners"]) == 1
                ).most_common()
            ],
            "maximum_owners_per_site": max(
                (len(item["owners"]) for item in owner_attribution),
                default=0,
            ),
        },
        "single_owner_function_register_groups": [
            {
                "owner": owner,
                "base_register": register,
                "writes": count,
            }
            for (owner, register), count in Counter(
                (owner, item["base_register"])
                for owner, item in single_owner_writes
            ).most_common()
        ],
        "by_base_register": [
            {"base_register": register, "writes": count}
            for register, count in Counter(
                item["base_register"] for item in other_unresolved_writes
            ).most_common()
        ],
        "by_function_and_register": [
            {
                "function": function,
                "base_register": register,
                "writes": count,
            }
            for (function, register), count in Counter(
                (item["function"], item["base_register"])
                for item in other_unresolved_writes
            ).most_common()
        ],
        "collision_relevant_by_function_and_register": [
            {
                "function": function,
                "base_register": register,
                "writes": count,
            }
            for (function, register), count in Counter(
                (item["function"], item["base_register"])
                for item in live_unbounded_writes
            ).most_common()
        ],
        "unknown_direct_control_transfers": unknown_control_transfers,
        "limit": (
            "The collision-relevant count excludes only high-segment instructions "
            "rejected by the conservative stock reachability gate."
        ),
    }
    live_bounded_value_set_accesses = [
        item for item in bounded_value_set_accesses if stock_live(item)
    ]
    ordinary_bounded_value_set_accesses = [
        item
        for item in live_bounded_value_set_accesses
        if item["function"] not in {"FUN_0044e6", "flash_write_orchestrator"}
    ]
    collision_relevant_unresolved = collision_relevant_unbounded_accesses
    static_accesses = (
        live_direct + live_resolved + ordinary_bounded_value_set_accesses
    )
    gaps = candidate_gaps(
        static_accesses + logger_accesses,
        implicit,
        collision_relevant_unresolved,
    )
    runtime = json.loads(RUNTIME_FOOTPRINT.read_text(encoding="utf-8"))
    for gap in gaps:
        gap_start, gap_end = claim_bounds(gap)
        gap["runtime_scenarios"] = [
            name
            for name, scenario in runtime["scenarios"].items()
            if any(
                max(gap_start, claim_bounds(item)[0])
                < min(gap_end, claim_bounds(item)[1])
                for item in scenario["touched_ranges"]
            )
        ]
    certified_gap_specs = {
        "0xD800-0xD83F": "0xD800-0xD83F",
        "0xDB8F-0xDC1F": "0xDB90-0xDC1F",
        "0xE847-0xE85F": "0xE848-0xE85F",
    }
    certified_post_startup_ranges = []
    for gap in gaps:
        word_range = certified_gap_specs.get(gap["range"])
        if word_range is None:
            continue
        gap_start, gap_end = claim_bounds(gap)
        overlapping_implicit = [
            item
            for item in implicit
            if max(gap_start, claim_bounds(item)[0])
            < min(gap_end, claim_bounds(item)[1])
        ]
        if (
            collision_relevant_unbounded_accesses
            or live_unbounded_writes
            or dpp3_invariant["status"] != "proven for the exact stock image"
            or pec_inactive_invariant["status"]
            != "proven for the exact stock image"
            or gap["lifetime_claims"] != ["startup word RAM self-test"]
            or gap["runtime_scenarios"] != ["boot"]
            or gap["unresolved_offset_hints"]
            or overlapping_implicit
        ):
            raise RuntimeError(
                "post-startup gap certification changed: "
                + json.dumps(
                    {
                        "gap": gap,
                        "implicit": overlapping_implicit,
                    },
                    sort_keys=True,
                )
            )
        gap["status"] = "certified free after startup handoff"
        gap["word_aligned_subrange"] = word_range
        certified_post_startup_ranges.append(
            {
                "range": gap["range"],
                "bytes": gap["bytes"],
                "word_aligned_subrange": word_range,
                "lifetime_boundary": (
                    "after the startup RAM self-test and main firmware handoff"
                ),
                "native_runtime_owners": [],
            }
        )
    if {
        item["range"] for item in certified_post_startup_ranges
    } != set(certified_gap_specs):
        raise RuntimeError("the expected post-startup RAM gaps were not reconstructed")

    iram_gaps = candidate_gaps(
        static_accesses + logger_accesses,
        implicit,
        collision_relevant_unresolved,
        minimum_size=1,
        region_name="internal RAM",
    )
    for gap in iram_gaps:
        gap_start, gap_end = claim_bounds(gap)
        gap["runtime_scenarios"] = [
            name
            for name, scenario in runtime["scenarios"].items()
            if any(
                max(gap_start, claim_bounds(item)[0])
                < min(gap_end, claim_bounds(item)[1])
                for item in scenario["touched_ranges"]
            )
        ]
    certified_iram_specs = {
        "0xFC3F-0xFC41": "0xFC40-0xFC41",
        "0xFD80-0xFDDB": "0xFD80-0xFDDB",
    }
    certified_iram_ranges = []
    for gap in iram_gaps:
        word_range = certified_iram_specs.get(gap["range"])
        if word_range is None:
            continue
        gap_start, gap_end = claim_bounds(gap)
        overlapping_implicit = [
            item
            for item in implicit
            if max(gap_start, claim_bounds(item)[0])
            < min(gap_end, claim_bounds(item)[1])
        ]
        if (
            collision_relevant_unbounded_accesses
            or live_unbounded_writes
            or context_bank_invariant["status"]
            != "proven for the exact stock image"
            or system_stack_invariant["status"]
            != "proven for the exact stock image"
            or pec_inactive_invariant["status"]
            != "proven for the exact stock image"
            or gap["lifetime_claims"] != ["startup internal-RAM self-test"]
            or gap["runtime_scenarios"] != ["boot"]
            or gap["unresolved_offset_hints"]
            or overlapping_implicit
        ):
            raise RuntimeError(
                "internal-RAM certification changed: "
                + json.dumps(
                    {"gap": gap, "implicit": overlapping_implicit},
                    sort_keys=True,
                )
            )
        gap["status"] = "certified free after startup handoff"
        gap["word_aligned_subrange"] = word_range
        certified_iram_ranges.append(
            {
                "range": gap["range"],
                "bytes": gap["bytes"],
                "word_aligned_subrange": word_range,
                "lifetime_boundary": (
                    "after the startup internal-RAM self-test and main handoff"
                ),
                "native_runtime_owners": [],
            }
        )
    if {
        item["range"] for item in certified_iram_ranges
    } != set(certified_iram_specs):
        raise RuntimeError("the expected internal-RAM gaps were not reconstructed")

    iram_intervals, iram_summary = ownership_intervals(
        static_accesses,
        logger_accesses,
        implicit,
        certified_iram_ranges,
    )
    if (
        iram_summary["certified_bytes"] != 95
        or iram_summary["transient_only_excluded_bytes"] != 4
    ):
        raise RuntimeError(
            "internal-RAM ownership summary changed: "
            + json.dumps(iram_summary, sort_keys=True)
        )
    iram_certification = {
        "status": "conditionally certified for exact-image post-startup use",
        "range": "0xFA00-0xFDFF",
        **iram_summary,
        "word_aligned_certified_bytes": 94,
        "stock_static_bytes": len(
            covered_bytes(static_accesses, 0xFA00, 0xFE00)
        ),
        "logger_claims": sum(
            0xFA00 <= int(item["address"], 16) < 0xFE00 for item in logger
        ),
        "certified_post_startup_ranges": certified_iram_ranges,
        "excluded_transient_only_range": {
            "range": "0xFD7C-0xFD7F",
            "reason": (
                "No stock-runtime owner, but the current Soft-BSL 16-word "
                "CRC table at 0xFD60-0xFD7F occupies these four otherwise-free bytes."
            ),
        },
        "conditions": [
            "Use only with the exact MS41.3 reference image and current loader contracts.",
            "Initialize only after the stock startup internal-RAM self-test and main handoff.",
            "Do not expect contents to survive reset.",
            "Do not change CP banks, SP/STKOV/STKUN, PEC configuration, or Soft-BSL scratch addresses.",
            "Use only the listed even-address subranges for 16-bit objects.",
            (
                "Hardware-BSL coverage ends at its documented built-in register bank, "
                "stack, and 32-byte first stage; an arbitrary downloaded second stage "
                "may deliberately use any remaining IRAM."
            ),
        ],
        "proof": (
            "All 1,024 bytes are classified. Normal stock/logger/implicit "
            "ownership accounts for the claimed bytes; the only normal-unclaimed "
            "bytes reconstruct as 0xFC3F-0xFC41 and 0xFD7C-0xFDDB. The current "
            "Soft-BSL CRC table excludes 0xFD7C-0xFD7F, leaving the two certified "
            "ranges with zero stock access, logger claim, stack/context/PEC claim, "
            "or post-startup runtime touch."
        ),
    }

    offset_counts = Counter(
        item["offset_hint"]
        for item in collision_relevant_unresolved
        if item["offset_hint"]
        and region_for(int(item["offset_hint"], 16)) == "external SRAM"
    )
    offset_hints = [
        {"offset": offset, "accesses": count}
        for offset, count in sorted(
            offset_counts.items(), key=lambda item: (-item[1], int(item[0], 16))
        )
    ]
    pointer_hotspots = [
        {"function": function, "base_register": register, "accesses": count}
        for (function, register), count in Counter(
            (item["function"], item["base_register"])
            for item in collision_relevant_unresolved
            if item["function"] != "<unmapped>"
        ).most_common()
    ]

    candidates = []
    for name, start, end, note in CANDIDATES:
        direct_bytes = covered_bytes(live_direct, start, end)
        resolved_bytes = covered_bytes(
            live_resolved + ordinary_bounded_value_set_accesses, start, end
        )
        logged_bytes = covered_bytes(logger_accesses, start, end)
        hints = [
            item
            for item in collision_relevant_unresolved
            if overlaps_hint(item, start, end)
        ]
        lifetime = [
            item
            for item in LIFETIME_CLAIMS
            if item["start"] < end and start < item["end"]
        ]
        if direct_bytes or resolved_bytes or logged_bytes:
            status = "rejected: native static/logger ownership"
        elif lifetime:
            status = "shared transient ownership: not exclusive patch RAM"
        elif collision_relevant_unresolved:
            status = "unsafe: unresolved indirect ownership"
        else:
            status = "unproven: indirect/stack/PEC/runtime evidence outstanding"
        candidates.append(
            {
                "range": name,
                "status": status,
                "direct_bytes": len(direct_bytes),
                "resolved_indirect_bytes": len(resolved_bytes),
                "logger_bytes": len(logged_bytes),
                "unresolved_indirect_accesses": len(
                    collision_relevant_unresolved
                ),
                "unresolved_offset_hints": len(hints),
                "lifetime_claims": [item["owner"] for item in lifetime],
                "note": note,
            }
        )

    region_summary = []
    for name, start, end in REGIONS:
        accesses = [
            item
            for item in static_accesses
            if start <= int(item["address"], 16) < end
        ]
        logger_in_region = [
            item for item in logger if start <= int(item["address"], 16) < end
        ]
        region_summary.append(
            {
                "region": name,
                "range": f"0x{start:04X}-0x{end - 1:04X}",
                "static_accesses": len(accesses),
                "static_bytes": len(covered_bytes(accesses, start, end)),
                "logger_claims": len(logger_in_region),
            }
        )

    inputs = {}
    for filename in INPUT_FILES:
        path = decomp / filename
        inputs[str(path)] = {"bytes": path.stat().st_size, "sha256": sha256(path)}
    for path in EXTRA_INPUTS:
        inputs[str(path)] = {"bytes": path.stat().st_size, "sha256": sha256(path)}
    inputs[str(reference)] = {
        "bytes": reference.stat().st_size,
        "sha256": sha256(reference),
    }
    certification = {
        "status": "conditionally certified for post-startup patch planning",
        "image": str(reference),
        "image_sha256": inputs[str(reference)]["sha256"],
        "certified_post_startup_ranges": certified_post_startup_ranges,
        "conditions": [
            "Use only with this exact MS41.3 stock image and unchanged native control flow.",
            "Initialize and use the RAM only after the startup RAM self-test and main firmware handoff.",
            "Do not expect contents to survive reset; startup deliberately overwrites every certified byte.",
            "Do not write DPP3 or reconfigure PEC channels 3-6 or their pointer words.",
            "Re-run this analyzer after any native patch changes a hook, pointer invariant, scheduler path, stack boundary, or PEC configuration.",
            "Use the listed even-address subranges for 16-bit objects.",
        ],
        "excluded_transient_ranges": [
            {
                "range": "0xDC20-0xDFFF",
                "reason": "stock authenticated DS2 memory-download target",
            },
            {
                "range": "0xE000-0xE31F",
                "reason": (
                    "stock authenticated DS2 memory-download target and "
                    "Soft-BSL chunk buffer"
                ),
            },
        ],
        "proof": (
            "The conservative stock-live frontier is zero unresolved reads and "
            "zero unresolved writes. The certified gaps have no direct, "
            "resolved-indirect, bounded-indirect, logger, active-PEC, stack, "
            "context-bank, or post-startup lifetime claim; emulator scenarios "
            "touch them only during the independently bounded boot RAM test."
        ),
        "scope_limit": (
            "This is an exact-image, post-startup ownership certificate, not a "
            "universal MS41-family allocator or a reset-safe RAM claim."
        ),
    }

    report = {
        "scope": "isolated MS41.3 static RAM ownership investigation",
        "certification": certification,
        "iram_ownership": {
            **iram_certification,
            "context_bank_invariant": context_bank_invariant,
            "system_stack_invariant": system_stack_invariant,
            "candidate_gaps": iram_gaps,
            "interval_map": iram_intervals,
        },
        "address_map_findings": list(ADDRESS_MAP_FINDINGS),
        "retained_bsl_evidence_audit": BSL_EVIDENCE_AUDIT,
        "inputs": inputs,
        "assembly_coverage": coverage,
        "emulator_boot_footprint": boot_footprint(),
        "emulator_runtime_footprint": runtime,
        "boot_ram_self_test": {
            "table": {
                f"0x{address:04X}": f"0x{value:04X}"
                for address, value in boot_ram_test_words.items()
            },
            "variant_marker_0xA000": f"0x{image[0x6000]:02X}",
            "claimed_ranges": [
                "0xD080-0xD0FF",
                "0xD800-0xF7F3",
                "0xFA00-0xFDFF",
            ],
            "lifetime": "reset/startup before the main firmware handoff",
        },
        "counts": {
            "instructions": len(instructions),
            "direct_accesses": len(direct),
            "stock_reachable_direct_accesses": len(live_direct),
            "resolved_indirect_accesses": len(resolved),
            "stock_reachable_resolved_indirect_accesses": len(live_resolved),
            "unresolved_indirect_accesses": len(unresolved),
            "stock_reachable_unresolved_indirect_accesses": len(live_unresolved),
            "unresolved_indirect_writes": sum(
                item["kind"] in {"write", "read/write"} for item in unresolved
            ),
            "unresolved_r0_predecrement_writes": len(r0_predecrement_writes),
            "unresolved_writes_excluding_r0_predecrement": (
                len(other_unresolved_writes)
            ),
            "unresolved_writes_after_proven_value_sets": (
                len(other_unresolved_writes) - len(bounded_value_set_writes)
            ),
            "proven_value_set_write_sites": len(bounded_value_set_writes),
            "proven_value_set_read_sites": len(
                bounded_value_set_read_accesses
            ),
            "stock_unreachable_low_write_sites": len(
                unreachable_low_writes
            ),
            "stock_unreachable_high_write_sites": len(
                unreachable_high_writes
            ),
            "collision_relevant_unbounded_write_sites": len(
                live_unbounded_writes
            ),
            "collision_relevant_unbounded_access_sites": len(
                collision_relevant_unbounded_accesses
            ),
            "startup_self_test_indirect_access_sites": len(
                startup_self_test_accesses
            ),
            "unresolved_plain_pointer_accesses": sum(
                item["offset_hint"] is None for item in unresolved
            ),
            "unresolved_indexed_pointer_accesses": sum(
                item["offset_hint"] is not None for item in unresolved
            ),
            "unresolved_unmapped_accesses": sum(
                item["function"] == "<unmapped>" for item in unresolved
            ),
            "logger_claims": len(logger),
        },
        "pointer_reachability_bound": {
            "domain": "16-bit logical address before DPP translation",
            "result": (
                "No collision-relevant unbounded pointer remains. Every "
                "stock-live indirect access is classified as a bounded native "
                "value set, canonical stack access, or startup RAM-test access."
            ),
            "certified_exclusions": [
                (
                    f"{len(unreachable_low_writes)} decoded write sites are "
                    "rejected inside proven non-executable low-image bytes."
                ),
                (
                    f"{len(unreachable_high_writes)} write sites are rejected "
                    "as unreachable by the conservative stock high-segment gate."
                ),
                (
                    f"{len(startup_self_test_accesses)} startup RAM self-test "
                    "accesses end before the main firmware handoff."
                ),
                "Canonical r0 stack traffic is confined to reserved 0xFA00-0xFA45.",
                "Single-owner FUN_02b0cc r9 writes use base 0xF018 or 0xF0C4.",
                "Thirteen FUN_02b0cc r4 writes stay inside 0xF042-0xF16D.",
                "All fourteen FUN_02b0cc r8 writes stay inside 0xF01E-0xF0F9.",
                "Single-owner FUN_020986 r5 writes stay inside 0xE865-0xE964.",
                "Six FUN_0044e6 r5 writes stay inside 0xE523-0xE846.",
                "The FUN_00098a r5 write stays inside 0xE523-0xE621.",
                "FUN_0044e6 r2 RAM-test writes stay inside the stock test ranges.",
                "FUN_0357d2 r2/r4 writes stay inside 0xEA24-0xFC3D.",
                "FUN_001bcc r15 writes stay inside 0xFA46-0xFA5D.",
                "FUN_028100 r9 writes stay inside 0xF018-0xF0F7.",
                "FUN_02218a/FUN_022998 r2 writes stay inside 0xF3FA-0xFC3D.",
                "FUN_027b1c r7 writes stay inside 0xE523-0xE720.",
                "FUN_02ada4/FUN_02aeb8 r8 writes stay inside 0xF182-0xF193.",
                "FUN_0352c0 r14/r4 writes stay inside 0xF63E-0xFC3C.",
                "The paired-object updater r8 writes stay inside 0xF024-0xF0D3.",
                "FUN_02c024 r2 writes stay inside 0xF63F-0xFC3D.",
                "FUN_036128 r5 writes stay inside 0xF748-0xF847.",
                "FUN_0362b4 r4 writes stay inside 0xF748-0xF847.",
                "FUN_020a26 r9/r4 writes stay inside 0xE52E-0xE551.",
                "FUN_020f5a r5 writes stay inside 0xE523-0xE624.",
                "Three FUN_0044e6 r4 writes stay inside 0xE523-0xE643.",
                "Two FUN_0044e6 r0 writes stay inside the stock RAM-test ranges.",
                "Sixty-eight immediate MOVBZ-derived write bases are locally bounded.",
                "FUN_02e52e r5 writes stay inside 0xE9A8-0xEBA7.",
                "FUN_0314a8 r5 writes use only the five even bases 0x0002-0x000A.",
                "FUN_02ff78 r9 writes stay inside the two native object records.",
                "The object metric updater r8 writes stay inside 0xF02A-0xF0D7.",
                "The two-record updater writes stay inside 0xEFB4-0xEFC5.",
                "FUN_024e30 ring/countdown writes stay inside 0xE865-0xE8A4 and 0xF770-0xF7F1.",
                "The stock flash-orchestrator scan write stays inside 0xE428-0xE527.",
                "The low/high serial-buffer writers stay inside their proven native transfer buffers.",
                "Locally bounded state-clear, bitset, record, ISR, and flash-copy loops stay inside their explicit native arrays.",
            ],
        },
        "pointer_base_investigations": list(POINTER_BASE_INVESTIGATIONS),
        "proven_pointer_value_set_writes": bounded_value_set_writes,
        "proven_pointer_value_set_reads": bounded_value_set_read_accesses,
        "r0_software_stack_investigation": r0_stack,
        "unresolved_write_investigation": unresolved_write_investigation,
        "control_flow_entry_investigation": entry_gate,
        "lower_computed_dispatch_coverage": lower_dispatch_coverage,
        "dispatch_table_coverage": dispatch_table_coverage,
        "nonexecutable_low_gate": nonexecutable_low_gate,
        "stock_unreachable_low_writes": unreachable_low_writes,
        "lower_segment_reachability_gate": lower_reachability_gate,
        "high_segment_reachability_gate": high_reachability_gate,
        "stock_unreachable_high_writes": unreachable_high_writes,
        "collision_relevant_unbounded_writes": live_unbounded_writes,
        "collision_relevant_unbounded_accesses": (
            collision_relevant_unbounded_accesses
        ),
        "startup_self_test_indirect_accesses": startup_self_test_accesses,
        "object_pointer_entry_gates": object_pointer_entry_gates,
        "diagnostic_reader_entry_gate": diagnostic_reader_entry_gate,
        "paired_state_loop_entry_gate": paired_state_loop_entry_gate,
        "six_byte_record_entry_gate": six_byte_record_entry_gate,
        "word_copy_entry_gate": word_copy_entry_gate,
        "crc_update_entry_gate": crc_update_entry_gate,
        "checksum_scan_invariant": checksum_scan_invariant,
        "object_reset_entry_gate": object_reset_entry_gate,
        "optional_result_entry_gate": optional_result_entry_gate,
        "byte_search_entry_gate": byte_search_entry_gate,
        "calibration_parser_invariant": calibration_parser_invariant,
        "calibration_lookup_invariant": calibration_lookup_invariant,
        "low_serial_entry_gate": low_serial_entry_gate,
        "low_serial_retry_entry_gate": low_serial_retry_entry_gate,
        "high_serial_entry_gate": high_serial_entry_gate,
        "high_serial_retry_entry_gate": high_serial_retry_entry_gate,
        "ring_index_invariant": ring_index_invariant,
        "fc50_stack_offset_invariant": fc50_stack_offset_invariant,
        "context_index_invariant": context_index_invariant,
        "countdown_callback_entry_gate": countdown_callback_entry_gate,
        "guarded_pointer_slot_invariant": guarded_pointer_slot_invariant,
        "knock_task_entry_gate": knock_task_entry_gate,
        "flash_offset_invariant": flash_offset_invariant,
        "record_identifier_invariant": record_identifier_invariant,
        "record_comparator_entry_gate": record_comparator_entry_gate,
        "low_record_compare_entry_gate": low_record_compare_entry_gate,
        "low_record_extended_entry_gate": low_record_extended_entry_gate,
        "record_update_entry_gates": record_update_entry_gates,
        "byte_indexed_state_entry_gate": byte_indexed_state_entry_gate,
        "five_record_loop_entry_gate": five_record_loop_entry_gate,
        "object_subroutine_entry_gate": object_subroutine_entry_gate,
        "object_bridge_entry_gate": object_bridge_entry_gate,
        "object_metric_entry_gate": object_metric_entry_gate,
        "two_record_entry_gate": two_record_entry_gate,
        "paired_object_update_entry_gate": paired_object_update_entry_gate,
        "regions": region_summary,
        "candidates": candidates,
        "candidate_gaps": gaps,
        "lifetime_claims": list(LIFETIME_CLAIMS),
        "implicit_ownership_claims": implicit,
        "pec_analysis": {
            "pointer_workspace": "0xFDE0-0xFDFF",
            "complete_direct_configurations_observed_for_channels": [0, 1, 2, 7],
            "inactive_channels": pec_inactive_invariant,
            "ram_claims": list(PEC_RAM_CLAIMS),
            "limit": (
                "Active-channel RAM envelopes remain reserved. Reprogramming "
                "any PEC channel requires a new ownership audit."
            ),
        },
        "dpp3_evidence": dpp3_invariant,
        "unresolved_pointer_hotspots": pointer_hotspots,
        "external_sram_unresolved_offset_hints": offset_hints,
        "control_register_assignments": controls,
        "direct_accesses": direct,
        "resolved_indirect_accesses": resolved,
        "unresolved_indirect_accesses": unresolved,
        "stock_reachable_unresolved_indirect_accesses": live_unresolved,
        "logger_claims": logger,
    }

    claimed = {
        address
        for item in direct + logger_accesses
        for address in range(
            int(item["address"], 16), int(item["address"], 16) + item["width"]
        )
    }
    for expected in (0xF576, 0xF6E5, 0xF762):
        if expected not in claimed:
            raise RuntimeError(f"expected native/logger claim 0x{expected:04X} was missed")
    if not report["candidates"][2]["status"].startswith("rejected"):
        raise RuntimeError("0xF200-0xF7FF must not survive the static ownership gate")
    return report


def markdown(report: dict) -> str:
    lines = [
        "# Isolated MS41.3 RAM ownership investigation",
        "",
        "> This is generated research evidence, not current documentation or a production",
        "> allocator. Its certificate applies only to the exact stock image and post-startup",
        "> lifetime stated below.",
        "",
        "## Inputs",
        "",
        "| source | bytes | SHA-256 |",
        "|---|---:|---|",
    ]
    for path, item in report["inputs"].items():
        lines.append(f"| `{path}` | {item['bytes']} | `{item['sha256']}` |")

    certification = report["certification"]
    lines.extend(
        [
            "",
            "## Post-startup certification",
            "",
            f"- Status: **{certification['status']}**",
            f"- Exact image SHA-256: `{certification['image_sha256']}`",
            f"- Proof: {certification['proof']}",
            f"- Scope limit: {certification['scope_limit']}",
            "",
            "| certified range | bytes | word-aligned subrange | lifetime boundary |",
            "|---|---:|---|---|",
        ]
    )
    for item in certification["certified_post_startup_ranges"]:
        lines.append(
            f"| `{item['range']}` | {item['bytes']} | "
            f"`{item['word_aligned_subrange']}` | {item['lifetime_boundary']} |"
        )
    lines.extend(["", "Conditions:", ""])
    lines.extend(f"- {item}" for item in certification["conditions"])
    lines.extend(["", "Explicitly excluded transient ranges:", ""])
    lines.extend(
        f"- `{item['range']}`: {item['reason']}."
        for item in certification["excluded_transient_ranges"]
    )

    iram = report["iram_ownership"]
    lines.extend(
        [
            "",
            "## MS41.3 internal RAM map",
            "",
            f"- Status: **{iram['status']}**",
            f"- Physical IRAM: `{iram['range']}` = **{iram['bytes']} bytes**",
            f"- Normal stock/logger/implicit ownership: "
            f"**{iram['normal_runtime_claimed_bytes']} bytes**",
            f"- Normal-runtime unclaimed: **{iram['normal_runtime_unclaimed_bytes']} bytes**",
            f"- Certified after startup: **{iram['certified_bytes']} bytes** "
            f"(**{iram['word_aligned_certified_bytes']} word-aligned bytes**)",
            f"- Transient-only exclusion: **{iram['transient_only_excluded_bytes']} bytes**",
            f"- Stock static-access bytes: **{iram['stock_static_bytes']}**",
            f"- Logger claims in IRAM: **{iram['logger_claims']}**",
            f"- Proof: {iram['proof']}",
            "",
            "| certified IRAM range | bytes | word-aligned subrange | lifetime boundary |",
            "|---|---:|---|---|",
        ]
    )
    for item in iram["certified_post_startup_ranges"]:
        lines.append(
            f"| `{item['range']}` | {item['bytes']} | "
            f"`{item['word_aligned_subrange']}` | {item['lifetime_boundary']} |"
        )
    exclusion = iram["excluded_transient_only_range"]
    lines.extend(
        [
            "",
            f"- `{exclusion['range']}` is excluded: {exclusion['reason']}",
            "",
            "Conditions:",
            "",
        ]
    )
    lines.extend(f"- {item}" for item in iram["conditions"])

    context = iram["context_bank_invariant"]
    stack = iram["system_stack_invariant"]
    lines.extend(
        [
            "",
            "### Context, stack, and loader closure",
            "",
            f"- Context banks: **{context['status']}**. {context['proof']}",
            f"- Known CP bases: `{', '.join(context['known_cp_bases'])}`",
            f"- CP writes/restores: **{context['explicit_assignment_sites']} explicit**, "
            f"**{context['pop_restore_sites']} POP restores**",
            f"- System stack: **{stack['status']}**, envelope "
            f"`{stack['normal_runtime_envelope']}`. {stack['proof']}",
            f"- Soft-BSL: {stack['soft_bsl']}",
            f"- Hardware BSL: {stack['hardware_bsl']}",
            "",
            "### Complete byte-class interval map",
            "",
            "Every byte in `0xFA00-0xFDFF` appears exactly once below. Precise",
            "instruction/function access records remain in the JSON evidence.",
            "",
            "| range | bytes | status | normal-runtime owners | transient owners |",
            "|---|---:|---|---|---|",
        ]
    )
    for item in iram["interval_map"]:
        lines.append(
            f"| `{item['range']}` | {item['bytes']} | {item['status']} | "
            f"{', '.join(item['normal_runtime_owners']) or '-'} | "
            f"{', '.join(item['transient_owners']) or '-'} |"
        )

    lines.extend(["", "## Address-map working finding", ""])
    for item in report["address_map_findings"]:
        lines.extend(
            [
                f"- `{item['range']}`: **{item['classification']}**.",
                f"  Evidence: {item['evidence']}",
                f"  Boundary: {item['discrepancy']}",
            ]
        )

    bsl = report["retained_bsl_evidence_audit"]
    lines.extend(
        [
            "",
            "### Retained BSL evidence audit",
            "",
            f"- Claim reviewed: {bsl['claim_under_review']}",
            f"- Result: **{bsl['result']}**.",
            f"- Retained: {' '.join(bsl['retained_evidence'])}",
            f"- Missing: {' '.join(bsl['missing_evidence'])}",
            f"- Working classification: {bsl['working_classification']}",
            f"- Safety effect: {bsl['safety_effect']}",
        ]
    )

    counts = report["counts"]
    lines.extend(
        [
            "",
            "## Static coverage",
            "",
            f"- Instructions scanned: **{counts['instructions']}**",
            f"- Direct mapped-memory accesses: **{counts['direct_accesses']}**",
            f"  - Stock-reachable: **{counts['stock_reachable_direct_accesses']}**",
            f"- Exact indirect logical-address accesses: **{counts['resolved_indirect_accesses']}**",
            f"  - Stock-reachable: **{counts['stock_reachable_resolved_indirect_accesses']}**",
            f"- Unresolved indirect accesses: **{counts['unresolved_indirect_accesses']}**",
            f"  - Stock-reachable: **{counts['stock_reachable_unresolved_indirect_accesses']}**",
            f"  - Bounded read sites: **{counts['proven_value_set_read_sites']}**",
            f"  - Collision-relevant unbounded sites: "
            f"**{counts['collision_relevant_unbounded_access_sites']}**",
            f"  - Writes or read/writes: **{counts['unresolved_indirect_writes']}**",
            f"  - Canonical `[-r0]` stack writes: "
            f"**{counts['unresolved_r0_predecrement_writes']}**",
            f"  - Other unresolved writes: "
            f"**{counts['unresolved_writes_excluding_r0_predecrement']}**",
            f"    - Bounded by proven pointer value sets: "
            f"**{counts['proven_value_set_write_sites']}**",
            f"    - Still unbounded: "
            f"**{counts['unresolved_writes_after_proven_value_sets']}**",
            f"      - Proven non-executable low-image sites: "
            f"**{counts['stock_unreachable_low_write_sites']}**",
            f"      - Stock-unreachable high-segment sites: "
            f"**{counts['stock_unreachable_high_write_sites']}**",
            f"      - Collision-relevant sites: "
            f"**{counts['collision_relevant_unbounded_write_sites']}**",
            f"  - Plain pointers: **{counts['unresolved_plain_pointer_accesses']}**",
            f"  - Indexed pointers: **{counts['unresolved_indexed_pointer_accesses']}**",
            f"  - Outside named decompiler functions: **{counts['unresolved_unmapped_accesses']}**",
            f"- Logger address claims: **{counts['logger_claims']}**",
            "",
            "| assembly source | instructions | range |",
            "|---|---:|---|",
        ]
    )
    for item in report["assembly_coverage"]:
        lines.append(
            f"| `{item['source']}` | {item['instruction_count']} | "
            f"`{item['start']}-{item['end']}` |"
        )

    lines.extend(
        [
            "",
            "### Proven non-executable low-image bytes",
            "",
        ]
    )
    for item in report["nonexecutable_low_gate"]:
        lines.extend(
            [
                f"- Range `{item['range']}`: **{item['status']}**",
                f"  - Evidence: {item['evidence']}",
                f"  - Limit: {item['limit']}",
            ]
        )

    lower_reachability = report["lower_segment_reachability_gate"]
    lines.extend(
        [
            "",
            "### Conservative lower-segment roots",
            "",
            f"- Status: **{lower_reachability['status']}**",
            f"- Full-image scan: `{lower_reachability['full_image_scanned']}`",
            f"- `CALLS`/`JMPS` patterns: **{lower_reachability['calls_jmps_patterns']}**; "
            f"unique decoded lower targets: "
            f"**{lower_reachability['unique_decoded_lower_targets']}**",
            f"- Proof: {lower_reachability['proof']}",
        ]
    )

    reachability = report["high_segment_reachability_gate"]
    lines.extend(
        [
            "",
            "### Conservative high-segment reachability",
            "",
            f"- Status: **{reachability['status']}**",
            f"- Lower-flash scan: `{reachability['lower_flash_scanned']}`",
            f"- `CALLS`/`JMPS` patterns: **{reachability['lower_calls_jmps_patterns']}**; "
            f"unique high targets: **{reachability['lower_unique_high_targets']}**",
            f"- Decoded JMPI table targets: **{reachability['decoded_dispatch_targets']}**",
            f"- High instructions: **{reachability['decoded_high_instructions']}** decoded; "
            f"**{reachability['reachable_high_instructions']}** reachable; "
            f"**{reachability['unreachable_high_instructions']}** unreachable",
            f"- Proof: {reachability['proof']}",
            f"- Limit: {reachability['limit']}",
        ]
    )

    ram_test = report["boot_ram_self_test"]
    lines.extend(
        [
            "",
            "### Stock startup RAM self-test",
            "",
            f"- Immutable boundary table: "
            f"`{', '.join(f'{key}={value}' for key, value in ram_test['table'].items())}`",
            f"- Exact-image variant marker `0xA000`: "
            f"`{ram_test['variant_marker_0xA000']}`",
            f"- Proven destructive-test envelopes: "
            f"`{', '.join(ram_test['claimed_ranges'])}`",
            f"- Lifetime: {ram_test['lifetime']}.",
        ]
    )

    boot = report["emulator_boot_footprint"]
    lines.extend(
        [
            "",
            "## Emulator/oracle boot gate",
            "",
            f"- Source: `{boot['source']}`",
            f"- Execution: `{boot['entry']}` to `{boot['stop']}` in **{boot['steps']}** instructions",
            f"- Frozen window snapshots: `{', '.join(boot['snapshot_ranges'])}`",
            f"- Non-`0xFF` bytes at the handoff: **{boot['non_ff_bytes_at_stop']}**",
            f"- Excluded transient stack: `{boot['excluded_range']}`",
            "",
            "This proves the emulator reproduces the oracle's reset-to-main RAM snapshot.",
            "It is boot-lifetime evidence, not a steady-state free-space declaration.",
        ]
    )

    lines.extend(
        [
            "",
            "### Interpretation limits",
            "",
            "- Indirect results are 16-bit logical addresses. The exact-image DPP3",
            "  invariant below proves the stock E/F logical-to-physical mapping.",
            "- Absolute/far CALL/JMP targets are raw CPU addresses and are normalized",
            "  to full-read file addresses with low-word A14 XOR. Relative",
            "  targets and all reported instruction PCs remain file addresses.",
            f"- **{counts['stock_reachable_unresolved_indirect_accesses']}** "
            "stock-reachable accesses are not locally constant by straight-line",
            "  propagation; all are nevertheless classified by bounded pointer, stack,",
            "  or startup rules, leaving a zero collision-relevant frontier.",
            "- Static operands do not expose CPU GPR banks, stack writes, or PEC payload",
            "  transfers; the separately reconstructed claims are included in the gap gate.",
            "",
            "### Pointer reachability bound",
            "",
            f"- Domain: {report['pointer_reachability_bound']['domain']}.",
            f"- Result: {report['pointer_reachability_bound']['result']}",
        ]
    )
    lines.extend(
        f"- Certified exclusion: {item}"
        for item in report["pointer_reachability_bound"]["certified_exclusions"]
    )
    lines.extend(
        [
            "",
            "### DPP3 boundary",
            "",
            f"- Status: **{report['dpp3_evidence']['status']}**",
            f"- Reset page: `{report['dpp3_evidence']['reset_page']}`",
            f"- Post-startup stock-live writes: "
            f"**{len(report['dpp3_evidence']['post_startup_stock_live_writes'])}**",
            f"- Proof: {report['dpp3_evidence']['proof']}",
            f"- Manual evidence: {report['dpp3_evidence']['manual_evidence']}",
            f"- Limit: {report['dpp3_evidence']['limit']}",
        ]
    )

    runtime = report["emulator_runtime_footprint"]
    lines.extend(
        [
            "",
            "## Emulator scenario RAM footprints",
            "",
            f"- Completed cases: **{runtime['passed']}**",
            f"- Unsupported direct cases: **{len(runtime['unsupported_direct_cases'])}**",
            f"- Scope: {runtime['scope']}",
            f"- Certification: {runtime['certification']}",
            "",
            "| scenario | complete | unsupported | read bytes | write bytes | touched bytes | touched ranges |",
            "|---|---:|---:|---:|---:|---:|---|",
        ]
    )
    for name, item in runtime["scenarios"].items():
        ranges = ", ".join(entry["range"] for entry in item["touched_ranges"])
        lines.append(
            f"| {name} | {item['complete_cases']} | {item['unsupported_cases']} | "
            f"{item['read_bytes']} | {item['write_bytes']} | "
            f"{item['touched_bytes']} | `{ranges}` |"
        )
    lines.extend(["", "Runtime limits:", ""])
    lines.extend(f"- {item}" for item in runtime["limitations"])

    lines.extend(
        [
            "",
            "## Region evidence",
            "",
            "| region | range | static accesses | covered bytes | logger claims |",
            "|---|---|---:|---:|---:|",
        ]
    )
    for item in report["regions"]:
        lines.append(
            f"| {item['region']} | `{item['range']}` | {item['static_accesses']} | "
            f"{item['static_bytes']} | {item['logger_claims']} |"
        )

    lines.extend(
        [
            "",
            "## Lifetime-specific ownership",
            "",
            "| range | owner | lifetime | evidence |",
            "|---|---|---|---|",
        ]
    )
    for item in report["lifetime_claims"]:
        lines.append(
            f"| `{item['range']}` | {item['owner']} | {item['lifetime']} | "
            f"`{item['evidence']}` |"
        )

    lines.extend(
        [
            "",
            "## Implicit CPU/hardware RAM ownership",
            "",
            "| range | owner | lifetime | evidence |",
            "|---|---|---|---|",
        ]
    )
    for item in report["implicit_ownership_claims"]:
        lines.append(
            f"| `{item['range']}` | {item['owner']} | {item['lifetime']} | "
            f"`{item['evidence']}` |"
        )

    pec = report["pec_analysis"]
    lines.extend(
        [
            "",
            "### PEC reconstruction",
            "",
            f"- Complete direct configurations observed for channels: "
            f"`{', '.join(map(str, pec['complete_direct_configurations_observed_for_channels']))}`",
            f"- Pointer workspace: `{pec['pointer_workspace']}`",
            f"- Channels 3-6: **{pec['inactive_channels']['status']}**",
            f"- Proof: {pec['inactive_channels']['proof']}",
            f"- Manual evidence: {pec['inactive_channels']['manual_evidence']}",
            f"- Limit: {pec['limit']}",
        ]
    )
    for item in pec["inactive_channels"]["channels"]:
        lines.append(
            f"  - PEC{item['channel']}: control `{item['control_register']}`, "
            f"pointers `{item['source_pointer']}`/`{item['destination_pointer']}`, "
            "no post-startup writes."
        )

    lines.extend(
        [
            "",
            "## Candidate gate",
            "",
            "| range | status | direct bytes | indirect bytes | logger bytes | unresolved indirect | offset hints | note |",
            "|---|---|---:|---:|---:|---:|---:|---|",
        ]
    )
    for item in report["candidates"]:
        lines.append(
            f"| `{item['range']}` | {item['status']} | {item['direct_bytes']} | "
            f"{item['resolved_indirect_bytes']} | {item['logger_bytes']} | "
            f"{item['unresolved_indirect_accesses']} | "
            f"{item['unresolved_offset_hints']} | {item['note']} |"
        )

    lines.extend(
        [
            "",
            "## Ranked ordinary static gaps",
            "",
            "These are maximal gaps in ordinary direct/resolved/logger/implicit claims.",
            "They are split at transient-owner boundaries. Only rows explicitly marked",
            "**certified free after startup handoff** are patch-planning claims.",
            "",
            "| rank | range | bytes | status | word-aligned | offset hints | emulator scenarios | transient owners |",
            "|---:|---|---:|---|---|---:|---|---|",
        ]
    )
    for rank, item in enumerate(report["candidate_gaps"], 1):
        lines.append(
            f"| {rank} | `{item['range']}` | {item['bytes']} | {item['status']} | "
            f"`{item.get('word_aligned_subrange', '-')}` | "
            f"{item['unresolved_offset_hints']} | "
            f"{', '.join(item['runtime_scenarios']) or '-'} | "
            f"{', '.join(item['lifetime_claims']) or '-'} |"
        )

    lines.extend(
        [
            "",
            "### Collision-relevant unresolved pointer hotspots",
            "",
            f"- Remaining sites: **{len(report['unresolved_pointer_hotspots'])}**",
            "",
            "| function | base register | accesses |",
            "|---|---|---:|",
        ]
    )
    for item in report["unresolved_pointer_hotspots"][:25]:
        lines.append(
            f"| `{item['function']}` | `{item['base_register']}` | {item['accesses']} |"
        )

    lines.extend(
        [
            "",
            "### Named-pointer value-set investigations",
            "",
            "| function/base | candidate bases | implied access envelopes | status |",
            "|---|---|---|---|",
        ]
    )
    for item in report["pointer_base_investigations"]:
        lines.append(
            f"| `{item['function']} {item['base_register']}` | "
            f"`{', '.join(item['candidate_bases'])}` | "
            f"`{', '.join(item['candidate_access_envelopes'])}` | {item['status']} |"
        )
        lines.append(f"| evidence | {item['evidence']} | | |")
        if item["blockers"]:
            lines.append(f"| blockers | {' '.join(item['blockers'])} | | |")

    lines.extend(
        [
            "",
            "### Proven multi-value pointer writes",
            "",
            "These sites have more than one possible address, but every address is bounded.",
            "",
            "| owner | PC | operand | possible addresses |",
            "|---|---|---|---|",
        ]
    )
    for item in report["proven_pointer_value_set_writes"]:
        lines.append(
            f"| `{item['owner']}` | `{item['pc']}` | `{item['operand']}` | "
            f"`{item['effective_address_summary']}` |"
        )

    entry_gate = report["control_flow_entry_investigation"]
    lines.extend(
        [
            "",
            "### `FUN_02cd98` entry gate",
            "",
            f"- Result: **{entry_gate['status']}**",
            f"- File envelope checked: `{entry_gate['function_file_envelope']}`",
            f"- Segment-2 computed jump sites: **{entry_gate['segment2_jmpi_sites']}**",
            f"- Immutable ROM-table targets decoded: **{entry_gate['rom_table_entries']}**",
            f"- Computed targets inside the envelope: "
            f"**{len(entry_gate['computed_targets_inside_envelope'])}**",
            f"- Segment-2 indirect calls: **{len(entry_gate['segment2_calli_sites'])}**",
            "- External direct entries: "
            + ", ".join(
                f"`{item['pc']} -> {item['file_target']}`"
                for item in entry_gate["external_direct_entries"]
            ),
            f"- Limit: {entry_gate['limit']}",
        ]
    )

    lines.extend(
        [
            "",
            "### Computed-dispatch coverage",
            "",
            "| segment | JMPI sites | decoded tables | entries | CALLI sites | result |",
            "|---|---:|---:|---:|---:|---|",
        ]
    )
    for segment, item in report["dispatch_table_coverage"].items():
        lines.append(
            f"| `{segment}` | {item['jmpi_sites']} | {item['tables']} | "
            f"{item['entries']} | {item['calli_sites']} | {item['status']} |"
        )

    lines.extend(
        [
            "",
            "### Object-pointer entry gates",
            "",
            "| envelope | allowed entries | external calls | result |",
            "|---|---|---:|---|",
        ]
    )
    for item in report["object_pointer_entry_gates"].values():
        lines.append(
            f"| `{item['function_file_envelope']}` | "
            f"`{', '.join(item['allowed_entries'])}` | "
            f"{len(item['external_direct_entries'])} | {item['status']} |"
        )
    lines.append(
        "- Argument proof: every external call is immediately preceded by "
        "`mov r12,r9`; each entry copies `r12` into `r8`."
    )

    reader_gate = report["diagnostic_reader_entry_gate"]
    lines.extend(
        [
            "",
            "### Diagnostic-reader output gate",
            "",
            f"- Result: **{reader_gate['status']}**",
            f"- File envelope checked: `{reader_gate['function_file_envelope']}`",
            "- External direct entries: "
            + ", ".join(
                f"`{item['pc']} -> {item['file_target']}`"
                for item in reader_gate["external_direct_entries"]
            ),
            f"- Pointer proof: {reader_gate['pointer_proof']}",
            f"- Limit: {reader_gate['limit']}",
        ]
    )

    loop_gate = report["paired_state_loop_entry_gate"]
    lines.extend(
        [
            "",
            "### Paired-state loop entry gate",
            "",
            f"- Result: **{loop_gate['status']}**",
            f"- File envelope checked: `{loop_gate['function_file_envelope']}`",
            "- External direct entries: "
            + ", ".join(
                f"`{item['pc']} -> {item['file_target']}`"
                for item in loop_gate["external_direct_entries"]
            ),
            f"- Pointer proof: {loop_gate['pointer_proof']}",
            f"- Limit: {loop_gate['limit']}",
        ]
    )

    record_gate = report["six_byte_record_entry_gate"]
    lines.extend(
        [
            "",
            "### Six-byte record entry gate",
            "",
            f"- Result: **{record_gate['status']}**",
            f"- File envelope checked: `{record_gate['function_file_envelope']}`",
            "- External direct entries: "
            + ", ".join(
                f"`{item['pc']} -> {item['file_target']}`"
                for item in record_gate["external_direct_entries"]
            ),
            f"- Pointer proof: {record_gate['pointer_proof']}",
            f"- Limit: {record_gate['limit']}",
        ]
    )

    byte_gate = report["byte_indexed_state_entry_gate"]
    lines.extend(
        [
            "",
            "### Byte-indexed state entry gate",
            "",
            f"- Result: **{byte_gate['status']}**",
            f"- File envelope checked: `{byte_gate['function_file_envelope']}`",
            "- External direct entries: "
            + ", ".join(
                f"`{item['pc']} -> {item['file_target']}`"
                for item in byte_gate["external_direct_entries"]
            ),
            f"- Pointer proof: {byte_gate['pointer_proof']}",
            f"- Limit: {byte_gate['limit']}",
        ]
    )

    five_record_gate = report["five_record_loop_entry_gate"]
    lines.extend(
        [
            "",
            "### Five-record loop entry gate",
            "",
            f"- Result: **{five_record_gate['status']}**",
            f"- File envelope checked: `{five_record_gate['function_file_envelope']}`",
            "- External direct entries: "
            + ", ".join(
                f"`{item['pc']} -> {item['file_target']}`"
                for item in five_record_gate["external_direct_entries"]
            ),
            f"- Pointer proof: {five_record_gate['pointer_proof']}",
            f"- Limit: {five_record_gate['limit']}",
        ]
    )

    object_subroutine_gate = report["object_subroutine_entry_gate"]
    lines.extend(
        [
            "",
            "### Object subroutine entry gate",
            "",
            f"- Result: **{object_subroutine_gate['status']}**",
            f"- File envelope checked: `{object_subroutine_gate['function_file_envelope']}`",
            "- External direct entries: "
            + ", ".join(
                f"`{item['pc']} -> {item['file_target']}`"
                for item in object_subroutine_gate["external_direct_entries"]
            ),
            f"- Pointer proof: {object_subroutine_gate['pointer_proof']}",
            f"- Limit: {object_subroutine_gate['limit']}",
        ]
    )

    object_bridge_gate = report["object_bridge_entry_gate"]
    object_metric_gate = report["object_metric_entry_gate"]
    lines.extend(
        [
            "",
            "### Object metric updater entry gates",
            "",
            f"- Bridge result: **{object_bridge_gate['status']}**",
            f"- Bridge envelope: `{object_bridge_gate['function_file_envelope']}`",
            f"- Bridge pointer proof: {object_bridge_gate['pointer_proof']}",
            f"- Updater result: **{object_metric_gate['status']}**",
            f"- Updater envelope: `{object_metric_gate['function_file_envelope']}`",
            "- Updater external entries: "
            + ", ".join(
                f"`{item['pc']} -> {item['file_target']}`"
                for item in object_metric_gate["external_direct_entries"]
            ),
            f"- Updater pointer proof: {object_metric_gate['pointer_proof']}",
            f"- Limit: {object_metric_gate['limit']}",
        ]
    )

    two_record_gate = report["two_record_entry_gate"]
    lines.extend(
        [
            "",
            "### Two-record entry gate",
            "",
            f"- Result: **{two_record_gate['status']}**",
            f"- File envelope checked: `{two_record_gate['function_file_envelope']}`",
            "- External direct entries: "
            + ", ".join(
                f"`{item['pc']} -> {item['file_target']}`"
                for item in two_record_gate["external_direct_entries"]
            ),
            f"- Pointer proof: {two_record_gate['pointer_proof']}",
            f"- Limit: {two_record_gate['limit']}",
        ]
    )

    update_gate = report["paired_object_update_entry_gate"]
    lines.extend(
        [
            "",
            "### Paired-object update entry gate",
            "",
            f"- Result: **{update_gate['status']}**",
            f"- File envelope checked: `{update_gate['function_file_envelope']}`",
            "- External direct entries: "
            + ", ".join(
                f"`{item['pc']} -> {item['file_target']}`"
                for item in update_gate["external_direct_entries"]
            ),
            f"- Pointer proof: {update_gate['pointer_proof']}",
            f"- Limit: {update_gate['limit']}",
        ]
    )

    lines.extend(
        [
            "",
            "### Collision-relevant unresolved external-SRAM offset operands",
            "",
            f"- Remaining offsets: **{len(report['external_sram_unresolved_offset_hints'])}**",
            "",
            "| offset | accesses |",
            "|---|---:|",
        ]
    )
    for item in report["external_sram_unresolved_offset_hints"][:25]:
        lines.append(f"| `{item['offset']}` | {item['accesses']} |")

    r0_stack = report["r0_software_stack_investigation"]
    lines.extend(
        [
            "",
            "## `r0` software-stack investigation",
            "",
            f"- Normal startup context: {r0_stack['normal_context']}",
            f"- Reserved arena: **`{r0_stack['reserved_arena']}` "
            f"({r0_stack['arena_bytes']} bytes)**",
            f"- Boundary: {r0_stack['boundary']}",
            f"- Canonical predecrement write sites: "
            f"**{r0_stack['predecrement_write_sites']}**",
            f"- Canonical postincrement read sites: "
            f"**{r0_stack['postincrement_read_sites']}**",
            f"- Ordinary direct claims inside the arena: "
            f"**{r0_stack['ordinary_direct_claims_inside_arena']}**",
            f"- Logger claims inside the arena: "
            f"**{r0_stack['logger_claims_inside_arena']}**",
            f"- Alternate immediate-CP blocks reviewed: "
            f"**{r0_stack['alternate_cp_blocks_reviewed']}**",
            f"- Direct stack accesses inside those blocks: "
            f"**{r0_stack['alternate_cp_direct_stack_accesses']}**",
        ]
    )
    lines.extend(f"- Call-context evidence: {item}" for item in r0_stack["call_contexts"])
    lines.extend(
        [
            f"- Additional context evidence: {r0_stack['saved_without_calls']}",
            f"- Result: **{r0_stack['status']}**",
            f"- Limit: {r0_stack['limit']}",
        ]
    )

    write_investigation = report["unresolved_write_investigation"]
    lines.extend(
        [
            "",
            "## Remaining unresolved-write gate",
            "",
            f"- Sites after canonical stack exclusion: "
            f"**{write_investigation['sites_after_stack_exclusion']}**",
            f"- Sites bounded by proven value sets: "
            f"**{write_investigation['sites_bounded_by_proven_value_sets']}**",
            f"- Remaining unbounded sites: "
            f"**{write_investigation['remaining_unbounded_sites']}**",
            f"- Inside named function bodies: "
            f"**{write_investigation['inside_named_function_bodies']}**",
            f"- Outside named bodies but directly control-flow reachable: "
            f"**{write_investigation['outside_named_but_directly_reachable']}**",
            f"- Outside named bodies and not directly reached: "
            f"**{write_investigation['outside_named_and_not_directly_reached']}**",
            f"- Unresolved direct-control transfers: "
            f"**{len(write_investigation['unknown_direct_control_transfers'])}**",
            f"- Limit: {write_investigation['limit']}",
            "",
            "| base register | writes |",
            "|---|---:|",
        ]
    )
    for item in write_investigation["by_base_register"]:
        lines.append(f"| `{item['base_register']}` | {item['writes']} |")

    lines.extend(
        [
            "",
            "Top function/register groups:",
            "",
            "| function | base register | writes |",
            "|---|---|---:|",
        ]
    )
    for item in write_investigation["by_function_and_register"][:25]:
        lines.append(
            f"| `{item['function']}` | `{item['base_register']}` | "
            f"{item['writes']} |"
        )

    attribution = write_investigation["reachable_unmapped_owner_attribution"]
    lines.extend(
        [
            "",
            "Directly reachable shared/tail attribution:",
            "",
            f"- Single owner: **{attribution['single_owner_sites']}**",
            f"- Multiple owners: **{attribution['shared_owner_sites']}**",
            f"- Unattributed: **{attribution['unattributed_sites']}**",
            f"- Maximum owners on one shared site: "
            f"**{attribution['maximum_owners_per_site']}**",
            "",
            "| single originating owner | writes |",
            "|---|---:|",
        ]
    )
    for item in attribution["by_single_owner"][:25]:
        lines.append(f"| `{item['owner']}` | {item['writes']} |")

    lines.extend(
        [
            "",
            "Single-owner priority value-set groups (`r9`, `r5`, `r4`):",
            "",
            "| owner | base register | writes |",
            "|---|---|---:|",
        ]
    )
    priority_groups = [
        item
        for item in write_investigation["single_owner_function_register_groups"]
        if item["base_register"] in {"r9", "r5", "r4"}
    ]
    for item in priority_groups[:25]:
        lines.append(
            f"| `{item['owner']}` | `{item['base_register']}` | "
            f"{item['writes']} |"
        )

    control_counts = Counter(
        (item["register"], item["source_operand"])
        for item in report["control_register_assignments"]
    )
    lines.extend(
        [
            "",
            "## Stack/context assignments",
            "",
            "| register | source | assignments |",
            "|---|---|---:|",
        ]
    )
    for (register, source), count in sorted(control_counts.items()):
        lines.append(f"| {register} | `{source}` | {count} |")

    lines.extend(
        [
            "",
            "## Current conclusions",
            "",
            "- The exact stock image has three conditionally certified post-startup",
            "  byte ranges: `0xD800-0xD83F`, `0xDB8F-0xDC1F`, and `0xE847-0xE85F`.",
            "- Its 1,024-byte IRAM is fully classified. Two additional post-startup",
            "  ranges are certified: `0xFC3F-0xFC41` (3 bytes) and",
            "  `0xFD80-0xFDDB` (92 bytes), for 95 bytes total / 94 word-aligned bytes.",
            "- `0xFD7C-0xFD7F` is not stock-runtime-owned, but is excluded because it",
            "  overlaps the current Soft-BSL CRC table at `0xFD60-0xFD7F`.",
            "- All five are overwritten by startup RAM tests; patch state must",
            "  be initialized only after the main firmware handoff and is not reset-persistent.",
            "- The conservative stock-live frontier is closed at zero unbounded reads",
            "  and zero unbounded writes, including the interrupt-driven scheduler graph.",
            "- DPP3 remains on reset page 3. PEC channels 3-6 remain inactive; active",
            "  PEC-channel envelopes are reserved as implicit ownership.",
            "- `0xDC34-0xDFFF` and `0xE000-0xE31F` remain transiently owned by",
            "  stock DS2 download and/or Soft-BSL and are not exclusive patch RAM.",
            "- `0xE320-0xE41F` is the stock RAM-resident flash-driver copy and execution",
            "  window, not spare RAM.",
            "- The full `0xFA00-0xFA45` software-stack arena is reserved; an exact",
            "  observed low-water mark is intentionally not used as an allocation boundary.",
            "- Hardware BSL uses CP `0xFA00`, stack `0xFA20-0xFA3F`, and first-stage",
            "  code `0xFA40-0xFA5F`; arbitrary downloaded second-stage IRAM use is",
            "  outside this certificate.",
            "- No unlisted gap is certified, and no result is generalized to another",
            "  MS41 image or to a patch that changes the proven invariants.",
            "",
            "## Revalidation boundary",
            "",
            "Re-run the complete isolated analyzer before using these ranges with a",
            "different ROM or after changing hooks, scheduler reachability, native pointer",
            "writers, DPP3, PEC configuration, context banks, or stack boundaries.",
            "Hardware actions, production-documentation edits, and allocator integration",
            "remain outside this investigation.",
            "",
        ]
    )
    return "\n".join(lines)


def self_test() -> None:
    direct = parse_instruction(
        "020016: 75f8e5f6       orb 0xf6e5,RL4", "sample.asm", 1
    )
    assert direct and direct_address(direct["operands"][0]) == 0xF6E5
    assert width_for(direct["mnemonic"]) == 1
    assert access_kind(direct["mnemonic"], 0) == "read/write"

    indirect = parse_instruction(
        "021bda: f4e4b6f2       movb RL7,[r4+#0xf2b6]", "sample.asm", 2
    )
    assert indirect
    parsed = INDIRECT_RE.match(indirect["operands"][1])
    assert parsed and int(parsed["offset"], 16) == 0xF2B6

    bit = parse_instruction(
        "020002: 2fe0           bset 0xffc0.0x2", "sample.asm", 3
    )
    assert bit and direct_address(bit["operands"][0]) == 0xFFC0

    far_call = parse_instruction(
        "02d096: da027e8d       calls 0x028d7e -> 028d7e", "sample.asm", 4
    )
    assert far_call and far_call["target"] == 0x02CD7E

    low_call = parse_instruction(
        "0046e2: ca00f608       calls 0x0008f6 -> 0008f6", "sample.asm", 5
    )
    assert low_call and low_call["target"] == 0x0048F6

    relative_jump = parse_instruction(
        "02cd90: 2d04 jmpr cc_EQ,0x02cd9a -> 02cd9a", "sample.asm", 6
    )
    assert relative_jump and relative_jump["target"] == 0x02CD9A
    assert normalize_absolute_code_target(0x028D7E) == 0x02CD7E
    assert normalize_absolute_code_target(0x0008F6) == 0x0048F6

    cfg = [
        parse_instruction(
            "001000: e6f43412 mov r4,#0x1234", "sample.asm", 7
        ),
        parse_instruction(
            "001004: 2d02 jmpr cc_EQ,0x00100a -> 00100a", "sample.asm", 8
        ),
        parse_instruction("001006: db00 rets", "sample.asm", 9),
        parse_instruction(
            "00100a: 9c08 jmpi cc_UC,[r8] -> 00ffff", "sample.asm", 10
        ),
    ]
    reachable, unknown, owners = direct_control_flow_analysis(
        [item for item in cfg if item],
        {0x1000: {"root"}},
        {0x1000: "root"},
    )
    assert reachable == {0x1000, 0x1004, 0x1006, 0x100A}
    assert len(unknown) == 1
    assert owners[0x100A] == {"root"}
    entry_cfg = [
        parse_instruction(
            "000ff8: 0d03 jmpr cc_UC,0x001000 -> 001000", "sample.asm", 11
        ),
        *cfg[:3],
    ]
    gate = stock_entry_gate(
        [item for item in entry_cfg if item], [], 0x1000, 0x1006, {0x1000}
    )
    assert gate["status"] == "proven for the stock image"
    assert gate["external_direct_entries"][0]["file_target"] == "0x001000"

    claims = implicit_ownership(
        [{"register": "CP", "source_operand": "#0xfae4", "pc": "0x038A42"}]
    )
    assert any(item["range"] == "0xFA00-0xFA45" for item in claims)
    assert any(item["range"] == "0xFAE4-0xFB03" for item in claims)
    assert claim_bounds({"range": "0xE320-0xE41F"}) == (0xE320, 0xE420)

    values = {4: 0xF000}
    exact_register_update(values, "movb", ["RL4", "#0x1"])
    assert 4 not in values

    gaps = candidate_gaps([{"address": "0xD800", "width": 16}], [], [])
    assert any(
        item["range"] == "0xD810-0xDC1F"
        and item["status"] == "transiently claimed"
        for item in gaps
    )
    assert any(
        item["range"] == "0xE320-0xE3FF"
        and item["status"] == "transiently claimed"
        for item in gaps
    )
    assert not candidate_gaps(
        [{"address": "0xFA00", "width": 0x400}],
        [],
        [],
        minimum_size=1,
        region_name="internal RAM",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--decomp", type=Path, default=DEFAULT_DECOMP)
    parser.add_argument("--reference", type=Path, default=DEFAULT_REFERENCE)
    parser.add_argument("--out", type=Path, default=Path(__file__).parent / "evidence")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        self_test()
        print("self-test: ok")
        return 0

    report = analyze(args.decomp.resolve(), args.reference.resolve())
    json_text = json.dumps(report, indent=2, sort_keys=True) + "\n"
    markdown_text = markdown(report)
    outputs = {
        args.out / "static_ram_ownership.json": json_text,
        args.out / "static_ram_ownership.md": markdown_text,
    }

    if args.check:
        stale = [
            str(path)
            for path, expected in outputs.items()
            if not path.exists() or path.read_text(encoding="utf-8") != expected
        ]
        if stale:
            print("stale evidence:", *stale, sep="\n  ")
            return 1
        print("evidence: current")
        return 0

    args.out.mkdir(parents=True, exist_ok=True)
    for path, content in outputs.items():
        path.write_text(content, encoding="utf-8")
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
