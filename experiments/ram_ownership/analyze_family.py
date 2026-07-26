#!/usr/bin/env python3
"""Exact-image RAM scan for the pending canonical MS41 variants."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

import analyze as core


PROJECTS = Path(__file__).resolve().parents[3]
LOGGER_XML = (
    PROJECTS
    / "_shared"
    / "REF_MS41.3"
    / "rr logger.dyno"
    / "2023 MS41 Logger Definitions.xml"
)
PROFILES = {
    "MS41.2": {
        "ecu_id": "1406464",
        "reference": (
            PROJECTS / "_shared" / "REF_MS41.2" / "E36 M3 Stock Full Read.bin"
        ),
        "sha256": "2d3ab7db6fe0f9a1f4680339f416aeeef43871730cd468cfd990f6dc80c03208",
        "listing": (PROJECTS / "Decompilation" / "MS41.2" / "ms41_2_disasm.asm"),
        "functions": (
            Path(__file__).parent / "evidence" / "family_raw" / "ms41_2_functions.tsv"
        ),
        "computed_tables": (
            *core.LOWER_COMPUTED_TABLES,
            *core.JMPI_TABLES,
            (0x0244C8, 0xA400, 8),
        ),
        "supplemental": (
            Path(__file__).parent / "evidence" / "family_raw" / "ms41_2_recovered.asm",
        ),
        "supporting": (PROJECTS / "Decompilation" / "MS41.2" / "fn_records.tsv",),
    },
    "MS41.1": {
        "ecu_id": "1437806",
        "reference": (
            PROJECTS
            / "_shared"
            / "REF_MS41.1"
            / "1437806_WBACD4321TAV42533_fullread.bin"
        ),
        "sha256": "6129b8e8884cd8321c94546e2beba4c8b4afcfca0d7749a341a82cea37f99aa3",
        "listing": (PROJECTS / "Decompilation" / "MS41.1" / "ms41_1_disasm.asm"),
        "functions": (
            Path(__file__).parent / "evidence" / "family_raw" / "ms41_1_functions.tsv"
        ),
        "computed_tables": (
            (0x0000EC, 0xAE44, 129),
            (0x00023C, 0xAE2C, 6),
            (0x00030A, 0xAE38, 6),
            (0x000FF2, 0xAF86, 0x66),
            (0x00106E, 0xAF6E, 6),
            (0x001130, 0xAF7A, 6),
            (0x0015D6, 0xB0B8, 36),
            (0x007C90, 0xAD50, 18),
            (0x020C74, 0xA5B8, 5),
            (0x020D0A, 0xA5C2, 5),
            (0x0244CC, 0xA400, 8),
            (0x024C4A, 0xA53C, 6),
            (0x025674, 0xA548, 24),
            (0x027828, 0xA580, 28),
            (0x02CE0E, 0xAC40, 11),
            (0x02D4E2, 0xAC56, 5),
            (0x02E9E2, 0xACBE, 5),
            (0x03065E, 0xAD0C, 8),
            (0x030ACC, 0xAD1C, 7),
            (0x03311C, 0xAD2A, 6),
            (0x0336A8, 0xAD36, 5),
            (0x036DE8, 0xACC8, 6),
            (0x0375F0, 0xACD4, 7),
            (0x037848, 0xACE2, 12),
            (0x037C44, 0xACFA, 9),
            (0x0385B2, 0xAE0E, 6),
            (0x03ADB8, 0xAF64, 5),
            (0x03F4C8, 0xAD40, 8),
        ),
        "supplemental": (
            Path(__file__).parent / "evidence" / "family_raw" / "ms41_1_recovered.asm",
        ),
        "supporting": (
            Path(__file__).parent / "evidence" / "family_raw" / "ms41_1_callgraph.tsv",
            Path(__file__).parent
            / "evidence"
            / "family_raw"
            / "ms41_1_indirect_pcode.tsv",
        ),
    },
    "MS41.0": {
        "ecu_id": "1429861",
        "reference": (
            PROJECTS
            / "_shared"
            / "REF_MS41.0"
            / "1429861_WBACE71010EU86940_fullread_323.bin"
        ),
        "sha256": "c61674c5812f5fe0a4a6f86e96311e9a8e540a905f9e7f681fdd045254249521",
        "listing": (PROJECTS / "Decompilation" / "MS41.0" / "ms41_0_disasm.asm"),
        "functions": (
            Path(__file__).parent / "evidence" / "family_raw" / "ms41_0_functions.tsv"
        ),
        "computed_tables": (
            (0x0001BA, 0xAA1A, 6),
            (0x000288, 0xAA26, 6),
            (0x001554, 0xAC9C, 36),
            (0x007448, 0xA964, 14),
            (0x022FA2, 0xA934, 5),
            (0x024450, 0xA400, 8),
            (0x024B16, 0xA4F8, 6),
            (0x025448, 0xA504, 21),
            (0x026AC2, 0xA536, 28),
            (0x027A88, 0xA56E, 5),
            (0x027AC8, 0xA578, 5),
            (0x028DDC, 0xA93E, 6),
            (0x0291A8, 0xA94A, 5),
            (0x02B302, 0xA954, 8),
            (0x03466C, 0xA9FC, 6),
        ),
        "supplemental": (
            Path(__file__).parent / "evidence" / "family_raw" / "ms41_0_recovered.asm",
            Path(__file__).parent
            / "evidence"
            / "family_raw"
            / "ms41_0_low_recovered.asm",
        ),
        "supporting": (
            PROJECTS / "Decompilation" / "MS41.0" / "fn_records.tsv",
            Path(__file__).parent
            / "evidence"
            / "family_raw"
            / "ms41_0_indirect_pcode.tsv",
            Path(__file__).parent
            / "evidence"
            / "family_raw"
            / "ms41_0_indirect_pcode_part2.tsv",
        ),
    },
}
SOURCE_REPORT = Path(__file__).parent / "evidence" / "static_ram_ownership.json"
SOURCE_REFERENCE = PROJECTS / "_shared" / "REF_MS41.3" / "MS41.3_s52_stock_fullread.bin"
SOURCE_XRAM_CANDIDATES = (
    (0xD800, 0xD83F),
    (0xDB8F, 0xDC1F),
    (0xE847, 0xE85F),
)
SOURCE_IRAM_CANDIDATES = ((0xFC3F, 0xFC41), (0xFD80, 0xFDDB))
EXPECTED_CP_BASES = {
    "MS41.0": {
        0xFA00,
        0xFA16,
        0xFC00,
        0xFC20,
        0xFC22,
        0xFC24,
        0xFC2E,
        0xFC34,
        0xFC5C,
        0xFC64,
        0xFC84,
        0xFCA4,
        0xFCC4,
        0xFCC8,
        0xFCCC,
        0xFCCE,
        0xFCD2,
        0xFCD8,
    },
    "MS41.1": {
        0xFA00,
        0xFA16,
        0xFB2E,
        0xFB30,
        0xFB32,
        0xFB3C,
        0xFB44,
        0xFC00,
        0xFC42,
        0xFCAE,
        0xFCB4,
        0xFCC2,
        0xFCDC,
        0xFCFC,
        0xFD66,
        0xFD86,
        0xFD8A,
        0xFD8C,
        0xFD90,
        0xFD96,
    },
    "MS41.2": {
        0xFA00,
        0xFA16,
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
    },
}
STARTUP_XRAM_END = {"MS41.0": 0xF201, "MS41.1": 0xF7EF, "MS41.2": 0xF7F3}
EXPECTED_CERTIFIED = {
    "MS41.0": {
        "xram": SOURCE_XRAM_CANDIDATES,
        "iram": ((0xFD80, 0xFDDB),),
    },
    "MS41.1": {
        "xram": ((0xD800, 0xD83F), (0xDB8F, 0xDBA3), (0xE847, 0xE85F)),
        "iram": ((0xFC3F, 0xFC41), (0xFDB6, 0xFDDB)),
    },
    "MS41.2": {
        "xram": SOURCE_XRAM_CANDIDATES,
        "iram": SOURCE_IRAM_CANDIDATES,
    },
}
SPECIAL_R0_SITES = {
    "MS41.0": {0x02BB8C},
    "MS41.1": {0x03A67A},
    "MS41.2": {0x038B18},
}
SPECIAL_R0_PROOFS = {
    "MS41.0": {
        "envelopes": ((0x0000, 0x01FE), (0x2B4A, 0x2D48)),
        "evidence": (
            "FC50 is written only with 0x2B4A (or reset zero); the masked "
            "FAFA index contributes at most 0x1FE."
        ),
    },
    "MS41.1": {
        "envelopes": ((0x0000, 0x01FE), (0x2DB6, 0x2FB4)),
        "evidence": (
            "FC50 is written only with 0x2DB6 (or reset zero); the masked "
            "FA9E index contributes at most 0x1FE."
        ),
    },
    "MS41.2": {
        "envelopes": ((0x0000, 0x01FE), (0x2AD6, 0x2CD4)),
        "evidence": (
            "FC50 is written only with 0x2AD6 (or reset zero); the masked "
            "FA9E index contributes at most 0x1FE."
        ),
    },
}
MANUAL_RULES = {
    "MS41.0": (
        {
            "name": "high serial helpers",
            "pc": (0x020300, 0x0204FF),
            "envelopes": ((0xE44B, 0xE4DA), (0xED01, 0xF0FF)),
            "evidence": (
                "Exact call arguments and loop guards bound the receive payload, "
                "checksum, and transfer buffers."
            ),
        },
        {
            "name": "record and checksum helpers",
            "pc": (0x020C00, 0x021DFF),
            "envelopes": (
                (0xA000, 0xCFFF),
                (0xE900, 0xF7FF),
                (0xFB00, 0xFBFF),
            ),
            "evidence": (
                "Immediate record/descriptor arguments, byte-sized indexes, and "
                "the exact A8A2 descriptor words close both record updaters."
            ),
        },
        {
            "name": "status pointer helpers",
            "pc": (0x024E00, 0x0252FF),
            "envelopes": ((0xD000, 0xD0FF), (0xED00, 0xF1FF)),
            "evidence": "All mutable status pointers have exact D0xx/EDxx/F1xx values.",
        },
        {
            "name": "calibration parser and lookup",
            "pc": (0x028000, 0x0283FF),
            "envelopes": ((0x0000, 0xC002), (0xFB5A, 0xFB5A)),
            "evidence": "Parser descriptors and lookup axes stay at or below 0xC002.",
        },
        {
            "name": "timer and serial interrupt tables",
            "pc": (0x02BC00, 0x02BFFF),
            "envelopes": (
                (0xA000, 0xBFFF),
                (0xF100, 0xF1FF),
                (0xFB2C, 0xFB43),
            ),
            "evidence": (
                "FA80 is exactly 0..5; AA08/AD70 contain the six FB2C-FB40 "
                "destinations and the bounded receive ring ends at F16A."
            ),
        },
        {
            "name": "byte-indexed E885 writer",
            "pc": (0x02DC00, 0x02DCFF),
            "envelopes": ((0xE885, 0xE984),),
            "evidence": "The index is zero-extended from one byte.",
        },
        {
            "name": "high serial interrupt block",
            "pc": (0x036600, 0x0367FF),
            "envelopes": (
                (0xA000, 0xBFFF),
                (0xFA62, 0xFA73),
                (0xFB2C, 0xFB43),
            ),
            "evidence": (
                "FA5F is sourced from the proven 0..5 FA80 selector; exact "
                "channel and destination tables close every indirect access."
            ),
        },
    ),
    "MS41.1": (
        {
            "name": "flash-protocol pointer",
            "pc": (0x0043F0, 0x0043FF),
            "envelopes": (
                (0x0000, 0x0000),
                (0x2200, 0x2200),
                (0xE320, 0xE528),
            ),
            "evidence": (
                "E656 is written only with zero, 0x2200, or the bounded stock "
                "flash buffer pointer."
            ),
        },
        {
            "name": "high serial helpers",
            "pc": (0x021700, 0x0218FF),
            "envelopes": ((0xF2DA, 0xF4A0),),
            "evidence": "Every call passes an exact F2DA-F49C buffer and byte count.",
        },
        {
            "name": "indexed record tables",
            "pc": (0x022600, 0x0230FF),
            "envelopes": ((0xA000, 0xCFFF), (0xEEA0, 0xF7FF)),
            "evidence": (
                "All indexes are byte-sized or loop-bounded; fixed bases run "
                "from EEAA through F656."
            ),
        },
        {
            "name": "record updater family",
            "pc": (0x023C00, 0x023FFF),
            "envelopes": (
                (0xA000, 0xBFFF),
                (0xE862, 0xE864),
                (0xE8D0, 0xF7FF),
                (0xFC3C, 0xFC3C),
                (0xFC82, 0xFC9D),
            ),
            "evidence": (
                "Every direct call supplies an exact EAxx-EE0A record and "
                "A6xx-A8xx descriptor; referenced target words were enumerated."
            ),
        },
        {
            "name": "immutable dispatch table",
            "pc": (0x024400, 0x0244FF),
            "envelopes": ((0xA400, 0xA40F),),
            "evidence": "The masked index selects one of eight immutable A400 words.",
        },
        {
            "name": "status pointer helpers",
            "pc": (0x024F00, 0x0254FF),
            "envelopes": (
                (0x0000, 0x0000),
                (0xD000, 0xD00A),
                (0xF700, 0xF7FF),
            ),
            "evidence": "All mutable status pointers have exact zero/D00x/F7xx values.",
        },
        {
            "name": "byte-indexed E885 writer",
            "pc": (0x02A500, 0x02A5FF),
            "envelopes": ((0xE885, 0xE984),),
            "evidence": "The index is zero-extended from one byte.",
        },
        {
            "name": "descriptor and record bridges",
            "pc": (0x02BE00, 0x02C1FF),
            "envelopes": (
                (0xA000, 0xBFFF),
                (0xE862, 0xE864),
                (0xEA00, 0xEEFF),
                (0xF692, 0xF6D1),
                (0xFC3C, 0xFC3C),
                (0xFC82, 0xFC99),
            ),
            "evidence": (
                "Immutable descriptors and exact call arguments close the "
                "relocated record updater and timer tables."
            ),
        },
        {
            "name": "paired native objects",
            "pc": (0x02FB00, 0x02FEFF),
            "envelopes": ((0xF030, 0xF1A7),),
            "evidence": "The object base is selected only as F030 or F0EC.",
        },
        {
            "name": "calibration parser and lookup",
            "pc": (0x032400, 0x0327FF),
            "envelopes": ((0x0000, 0xC002), (0xFB24, 0xFB24)),
            "evidence": "Parser descriptors and lookup axes stay at or below 0xC002.",
        },
        {
            "name": "immutable metadata lookup",
            "pc": (0x035F00, 0x035FFF),
            "envelopes": ((0xA000, 0xAD20),),
            "evidence": "The A5CD/AC35 metadata indexes remain in calibration space.",
        },
        {
            "name": "six-result lookup",
            "pc": (0x037500, 0x0375FF),
            "envelopes": ((0xF59E, 0xF5BC),),
            "evidence": "The helper result is checked below six before scaling.",
        },
        {
            "name": "timer destination table",
            "pc": (0x03A700, 0x03A7FF),
            "envelopes": ((0xA000, 0xBFFF), (0xFC82, 0xFC99)),
            "evidence": "AE1A contains the six FC82-FC96 timer destinations.",
        },
        {
            "name": "diagnostic pointer profiles",
            "pc": (0x03AB00, 0x03ADFF),
            "envelopes": (
                (0xA000, 0xBFFF),
                (0xE421, 0xE51F),
                (0xE520, 0xE620),
            ),
            "evidence": (
                "The E650/E65A pointer slots are closed by exact initialization "
                "and byte-sized lengths."
            ),
        },
        {
            "name": "high serial interrupt block",
            "pc": (0x03B200, 0x03B5FF),
            "envelopes": (
                (0xA000, 0xBFFF),
                (0xF692, 0xF6EF),
                (0xFA62, 0xFA73),
                (0xFC82, 0xFC99),
            ),
            "evidence": (
                "FA5F is 0..5; both interrupt rings and the B18C destination "
                "table are independently bounded."
            ),
        },
    ),
    "MS41.2": (
        {
            "name": "immutable dispatch table",
            "pc": (0x0244C6, 0x0244C6),
            "envelopes": ((0xA400, 0xA40F),),
            "evidence": "The masked index selects one of eight immutable A400 words.",
        },
        {
            "name": "fixed optional result byte",
            "pc": (0x02A588, 0x02A588),
            "envelopes": ((0xE8EB, 0xE8EB),),
            "evidence": "The only non-null output argument is the exact E8EB byte.",
        },
    ),
}
MS413_SUPPLEMENTAL_LISTINGS = (
    PROJECTS / "Decompilation" / "MS41.3" / "d_first32k.asm",
    PROJECTS / "Decompilation" / "MS41.3" / "d_ph.asm",
    PROJECTS / "Decompilation" / "MS41.3" / "d_progtop.asm",
    Path(__file__).parent / "evidence" / "ghidra_recovered_code.asm",
    Path(__file__).parent / "evidence" / "verified_overlap.asm",
    Path(__file__).parent / "evidence" / "ghidra_conservative_roots.asm",
    Path(__file__).parent / "evidence" / "ghidra_reachability_edges.asm",
    Path(__file__).parent / "evidence" / "ghidra_lower_computed_targets.asm",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def load_listing(
    path: Path, image: bytes, supplemental_paths: tuple[Path, ...] = ()
) -> tuple[list[dict], dict]:
    by_pc = {}
    with path.open(encoding="utf-8", errors="replace") as stream:
        for line_number, line in enumerate(stream, 1):
            item = core.parse_instruction(line, path.name, line_number)
            if item is not None and not 0x10000 <= item["pc"] < 0x20000:
                by_pc.setdefault(item["pc"], item)

    supplemental_added = 0
    supplemental_rejected = 0
    for supplemental in (*supplemental_paths, *MS413_SUPPLEMENTAL_LISTINGS):
        with supplemental.open(encoding="utf-8", errors="replace") as stream:
            for line_number, line in enumerate(stream, 1):
                match = core.ASM_RE.match(line)
                item = core.parse_instruction(line, supplemental.name, line_number)
                if (
                    match is None
                    or item is None
                    or item["pc"] in by_pc
                    or 0x10000 <= item["pc"] < 0x20000
                ):
                    continue
                raw = bytes.fromhex(match["bytes"])
                if image[item["pc"] : item["pc"] + len(raw)] != raw:
                    supplemental_rejected += 1
                    continue
                by_pc[item["pc"]] = item
                supplemental_added += 1
    return sorted(by_pc.values(), key=lambda item: item["pc"]), {
        "primary_instructions": sum(
            item["source"] == path.name for item in by_pc.values()
        ),
        "exact_ms413_supplemental_instructions": supplemental_added,
        "rejected_ms413_supplemental_instructions": supplemental_rejected,
    }


def scan_accesses(
    instructions: list[dict], functions_path: Path
) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    starts, functions, _entries = core.parse_functions(functions_path)
    branch_targets = {
        item["target"] for item in instructions if item["target"] is not None
    }
    values: dict[int, int] = {}
    direct, resolved, unresolved, controls = [], [], [], []
    current_function = None

    for instruction in instructions:
        function = core.function_for(instruction["pc"], starts, functions)
        if function != current_function or instruction["pc"] in branch_targets:
            values.clear()
            current_function = function
        width = core.width_for(instruction["mnemonic"])
        postincrements = []
        for index, operand in enumerate(instruction["operands"]):
            kind = core.access_kind(instruction["mnemonic"], index)
            if kind is None:
                continue
            address = core.direct_address(operand)
            if address is not None and core.region_for(address):
                direct.append(
                    core.access_record(
                        instruction,
                        function,
                        address,
                        width,
                        kind,
                        "direct",
                        operand,
                    )
                )
                continue
            indirect = core.INDIRECT_RE.match(operand)
            if indirect is None:
                continue
            register = int(indirect["reg"])
            if indirect["pre"] and register in values:
                values[register] = (values[register] - width) & 0xFFFF
            offset = int(indirect["offset"], 16) if indirect["offset"] else 0
            if register in values:
                address = (values[register] + offset) & 0xFFFF
                if core.region_for(address):
                    resolved.append(
                        core.access_record(
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
                    }
                )
            if indirect["post"]:
                postincrements.append(register)

        operands = instruction["operands"]
        if (
            instruction["mnemonic"] in {"mov", "scxt"}
            and len(operands) > 1
            and core.direct_address(operands[0]) in core.CONTROL_REGS
        ):
            destination = core.direct_address(operands[0])
            controls.append(
                {
                    "register": core.CONTROL_REGS[destination],
                    "pc": f"0x{instruction['pc']:06X}",
                    "source_operand": operands[1],
                    "instruction": instruction["instruction"],
                }
            )

        core.exact_register_update(
            values, instruction["mnemonic"], instruction["operands"]
        )
        for register in postincrements:
            if register in values:
                values[register] = (values[register] + width) & 0xFFFF
        if instruction["mnemonic"].startswith("call"):
            values.clear()
        if instruction["mnemonic"] in {"jmpr", "jmpa", "jmps", "jmpi"}:
            values.clear()

    return direct, resolved, unresolved, controls


def logger_claims(ecu_id: str) -> list[dict]:
    raw = LOGGER_XML.read_text(encoding="utf-8", errors="replace")
    raw = re.sub(r"<!DOCTYPE.*?\]>", "", raw, flags=re.DOTALL)
    import xml.etree.ElementTree as ET

    root = ET.fromstring(raw)
    ds2 = next(item for item in root.iter("protocol") if item.get("id") == "DS2")
    claims = []
    widths = {
        "int8": 1,
        "uint8": 1,
        "int16": 2,
        "uint16": 2,
        "int32": 4,
        "uint32": 4,
        "float": 4,
    }
    for parameter in ds2.findall(".//ecuparam"):
        conversion = parameter.find("./conversions/conversion")
        width = widths.get(
            conversion.get("storagetype", "uint8")
            if conversion is not None
            else "uint8",
            1,
        )
        for ecu in parameter.findall("./ecu"):
            ids = {item.strip() for item in ecu.get("id", "").split(",")}
            if ecu_id not in ids:
                continue
            for address_node in ecu.findall("./address"):
                address = int((address_node.text or "0").strip(), 0)
                if address < 0x20 or not core.is_ram(address):
                    continue
                claims.append(
                    {
                        "address": f"0x{address:04X}",
                        "width": max(width, int(address_node.get("length", "1"))),
                        "id": parameter.get("id"),
                        "name": parameter.get("name"),
                    }
                )
    return claims


def control_flow(
    instructions: list[dict],
    functions_path: Path,
    image: bytes,
    tables: tuple[tuple[int, int, int], ...],
) -> tuple[set[int], dict]:
    starts, functions, entries = core.parse_functions(functions_path)
    named_owners = {
        item["pc"]: owner
        for item in instructions
        if (owner := core.function_for(item["pc"], starts, functions)) != "<unmapped>"
    }
    table_targets = []
    for site, logical, count in tables:
        offset = (logical & 0x3FFF) ^ 0x4000
        for index in range(count):
            cpu_ip = int.from_bytes(
                image[offset + 2 * index : offset + 2 * index + 2],
                "little",
            )
            target = (site & 0x30000) | cpu_ip if site >= 0x20000 else cpu_ip
            table_targets.append(
                {
                    "site": f"0x{site:06X}",
                    "table": f"0x{logical:04X}",
                    "index": index,
                    "cpu_ip": f"0x{cpu_ip:04X}",
                    "file_target": (
                        f"0x{core.normalize_absolute_code_target(target):06X}"
                    ),
                }
            )

    by_pc = {item["pc"] for item in instructions}
    roots = {0x000000: {"<reset vector>"}, 0x004430: {"<startup>"}}
    pattern_roots = []
    for offset in range(0, len(image) - 3, 2):
        if image[offset] not in {0xDA, 0xFA}:  # CALLS / JMPS
            continue
        cpu_target = image[offset + 1] << 16 | int.from_bytes(
            image[offset + 2 : offset + 4], "little"
        )
        target = core.normalize_absolute_code_target(cpu_target)
        in_root_domain = target < core.LOW_CODE_END or (
            offset < 0x20000 and 0x20000 <= target < 0x40000
        )
        if not in_root_domain or target not in by_pc:
            continue
        roots.setdefault(target, set()).add("<exact-image absolute transfer>")
        pattern_roots.append(
            {
                "file_offset": f"0x{offset:06X}",
                "file_target": f"0x{target:06X}",
            }
        )
    for item in instructions:
        if 0x4000 <= item["pc"] < 0x4200 and item["mnemonic"] == "jmps":
            roots.setdefault(item["pc"], set()).add("<interrupt vectors>")
    reachable, unknown, _owners = core.direct_control_flow_analysis(
        instructions, roots, named_owners, table_targets
    )
    return reachable, {
        "status": "proven" if not unknown else "blocked",
        "computed_tables": len(tables),
        "computed_entries": len(table_targets),
        "reachable_instructions": len(reachable),
        "unknown_reachable_control_transfers": unknown,
        "root_policy": (
            "Reset, startup, interrupt vectors, and exact-image CALLS/JMPS "
            "patterns whose decoded targets exist in the exact listing are roots. "
            "Direct control flow and the immutable tables close the graph."
        ),
        "absolute_pattern_roots": pattern_roots,
        "missing_computed_targets": sorted(
            {
                item["file_target"]
                for item in table_targets
                if int(item["file_target"], 16) not in by_pc
            }
        ),
    }


def byte_gaps(accesses: list[dict], start: int, end: int) -> list[dict]:
    claimed = core.covered_bytes(accesses, start, end)
    gaps = []
    cursor = start
    while cursor < end:
        while cursor < end and cursor in claimed:
            cursor += 1
        gap_start = cursor
        while cursor < end and cursor not in claimed:
            cursor += 1
        if cursor > gap_start:
            gaps.append(
                {
                    "range": f"0x{gap_start:04X}-0x{cursor - 1:04X}",
                    "bytes": cursor - gap_start,
                }
            )
    return sorted(gaps, key=lambda item: (-item["bytes"], item["range"]))


def scan_variant(name: str) -> dict:
    profile = PROFILES[name]
    reference = profile["reference"]
    digest = sha256(reference)
    if digest != profile["sha256"]:
        raise RuntimeError(f"{name} reference hash changed: {digest}")
    image = reference.read_bytes()
    instructions, listing_coverage = load_listing(
        profile["listing"], image, profile["supplemental"]
    )
    direct, resolved, unresolved, controls = scan_accesses(
        instructions, profile["functions"]
    )
    reachable, control_flow_gate = control_flow(
        instructions,
        profile["functions"],
        image,
        profile["computed_tables"],
    )
    for accesses in (direct, resolved, unresolved):
        for item in accesses:
            item["direct_control_flow_reachable"] = int(item["pc"], 16) in reachable
    logger = logger_claims(profile["ecu_id"])
    ordinary = direct + resolved + logger
    return {
        "variant": name,
        "ecu_id": profile["ecu_id"],
        "reference": str(reference),
        "reference_sha256": digest,
        "listing": str(profile["listing"]),
        "listing_sha256": sha256(profile["listing"]),
        "functions": str(profile["functions"]),
        "functions_sha256": sha256(profile["functions"]),
        "supplemental_inputs": [
            {"path": str(path), "sha256": sha256(path)}
            for path in profile["supplemental"]
        ],
        "supporting_inputs": [
            {"path": str(path), "sha256": sha256(path)}
            for path in profile["supporting"]
        ],
        "listing_coverage": listing_coverage,
        "counts": {
            "instructions": len(instructions),
            "direct_accesses": len(direct),
            "resolved_indirect_accesses": len(resolved),
            "unresolved_indirect_accesses": len(unresolved),
            "unresolved_writes": sum(
                item["kind"] in {"write", "read/write"} for item in unresolved
            ),
            "logger_claims": len(logger),
            "stock_reachable_direct_accesses": sum(
                item["direct_control_flow_reachable"] for item in direct
            ),
            "stock_reachable_resolved_indirect_accesses": sum(
                item["direct_control_flow_reachable"] for item in resolved
            ),
            "stock_reachable_unresolved_indirect_accesses": sum(
                item["direct_control_flow_reachable"] for item in unresolved
            ),
        },
        "direct_accesses": direct,
        "resolved_indirect_accesses": resolved,
        "unresolved_indirect_accesses": unresolved,
        "control_register_assignments": controls,
        "control_flow_gate": control_flow_gate,
        "logger_claims": logger,
        "ordinary_gaps": {
            "xram": byte_gaps(ordinary, 0xD800, 0xF800),
            "iram": byte_gaps(ordinary, 0xFA00, 0xFE00),
        },
    }


def bytes_for_ranges(
    ranges: tuple[tuple[int, int], ...] | list[tuple[int, int]],
) -> set:
    return {address for start, end in ranges for address in range(start, end + 1)}


def compress_bytes(addresses: set[int]) -> tuple[tuple[int, int], ...]:
    if not addresses:
        return ()
    ordered = sorted(addresses)
    ranges = []
    start = previous = ordered[0]
    for address in ordered[1:]:
        if address != previous + 1:
            ranges.append((start, previous))
            start = address
        previous = address
    ranges.append((start, previous))
    return tuple(ranges)


def format_range(start: int, end: int) -> str:
    return f"0x{start:04X}-0x{end:04X}"


def word_range(start: int, end: int) -> str | None:
    start += start & 1
    if not end & 1:
        end -= 1
    return format_range(start, end) if start <= end else None


def word_bytes(ranges: tuple[tuple[int, int], ...]) -> int:
    total = 0
    for start, end in ranges:
        start += start & 1
        if not end & 1:
            end -= 1
        total += max(0, end - start + 1)
    return total


def range_records(ranges: tuple[tuple[int, int], ...]) -> list[dict]:
    return [
        {
            "range": format_range(start, end),
            "bytes": end - start + 1,
            "word_aligned_subrange": word_range(start, end),
        }
        for start, end in ranges
    ]


def class_intervals(
    start: int, end: int, certified: tuple[tuple[int, int], ...]
) -> list[dict]:
    available = bytes_for_ranges(certified)
    intervals = []
    cursor = start
    while cursor <= end:
        status = (
            "conditionally available after startup"
            if cursor in available
            else "owned/reserved; no allocation certificate"
        )
        interval_start = cursor
        while cursor <= end:
            next_status = (
                "conditionally available after startup"
                if cursor in available
                else "owned/reserved; no allocation certificate"
            )
            if next_status != status:
                break
            cursor += 1
        intervals.append(
            {
                "range": format_range(interval_start, cursor - 1),
                "bytes": cursor - interval_start,
                "status": status,
            }
        )
    return intervals


def exact_source_transfer(
    item: dict,
    image: bytes,
    source_image: bytes,
    source_live: dict[tuple[int, str, str, int], dict],
) -> dict | None:
    pc = int(item["pc"], 16)
    key_tail = (item["base_register"], item["kind"], item["width"])
    if pc < 8 or pc + 16 > len(image):
        return None
    context = image[pc - 8 : pc + 16]
    same_key = (pc, *key_tail)
    if same_key in source_live and context == source_image[pc - 8 : pc + 16]:
        return {"method": "same-PC exact 24-byte context", "source_pc": item["pc"]}
    source_offset = source_image.find(context)
    if source_offset < 0 or source_image.find(context, source_offset + 1) >= 0:
        return None
    source_pc = source_offset + 8
    if (source_pc, *key_tail) not in source_live:
        return None
    return {
        "method": "unique relocated exact 24-byte context",
        "source_pc": f"0x{source_pc:06X}",
    }


def manual_rule_for(name: str, item: dict) -> dict | None:
    pc = int(item["pc"], 16)
    if item["base_register"] == "r0":
        if pc in SPECIAL_R0_SITES[name]:
            return {
                "name": "FC50 bounded lookup",
                "pc": (pc, pc),
                **SPECIAL_R0_PROOFS[name],
            }
        return {
            "name": "r0 software-stack frame",
            "pc": (pc, pc),
            "envelopes": ((0xFA00, 0xFA45),),
            "evidence": (
                "Startup fixes r0 at FA46; all normal saves/restores and local "
                "frames remain in the proven FA00-FA45 software-stack arena."
            ),
        }
    matches = [
        rule for rule in MANUAL_RULES[name] if rule["pc"][0] <= pc <= rule["pc"][1]
    ]
    if len(matches) > 1:
        raise RuntimeError(f"{name} overlapping manual rules at 0x{pc:06X}")
    return matches[0] if matches else None


def context_banks(name: str, raw: dict) -> tuple[int, ...]:
    bases = {0xFA00, 0xFA16}
    for item in raw["control_register_assignments"]:
        if item["register"] != "CP":
            continue
        value = core.immediate(item["source_operand"])
        if value is not None:
            bases.add(value)
    if bases != EXPECTED_CP_BASES[name]:
        raise RuntimeError(
            f"{name} context-bank inventory changed: "
            + ", ".join(f"0x{base:04X}" for base in sorted(bases))
        )
    stack = {
        (item["register"], item["pc"], item["source_operand"])
        for item in raw["control_register_assignments"]
        if item["register"] != "CP"
    }
    expected_stack = {
        ("STKOV", "0x004468", "#0xfb64"),
        ("STKUN", "0x00446C", "#0xfc00"),
        ("SP", "0x004470", "#0xfc00"),
    }
    if stack != expected_stack:
        raise RuntimeError(f"{name} system-stack assignments changed")
    return tuple(sorted(bases))


def live_special_register_writes(name: str) -> tuple[list[dict], list[dict]]:
    profile = PROFILES[name]
    image = profile["reference"].read_bytes()
    instructions, _coverage = load_listing(
        profile["listing"], image, profile["supplemental"]
    )
    reachable, _gate = control_flow(
        instructions,
        profile["functions"],
        image,
        profile["computed_tables"],
    )
    pec3_6 = set(range(0xFEC6, 0xFECE, 2)) | set(range(0xFDEC, 0xFDFC, 2))
    dpp3_writes, pec_writes = [], []
    for instruction in instructions:
        if instruction["pc"] not in reachable:
            continue
        for index, operand in enumerate(instruction["operands"]):
            address = core.direct_address(operand)
            if core.access_kind(instruction["mnemonic"], index) not in {
                "write",
                "read/write",
            }:
                continue
            record = {
                "pc": f"0x{instruction['pc']:06X}",
                "instruction": instruction["instruction"],
            }
            if address == 0xFE06:
                dpp3_writes.append(record)
            if address in pec3_6:
                pec_writes.append({"register": f"0x{address:04X}", **record})
    return dpp3_writes, pec_writes


def record_claimed_bytes(records: list[dict], reachable_only: bool = False) -> set:
    claimed = set()
    for item in records:
        if reachable_only and not item["direct_control_flow_reachable"]:
            continue
        address = int(item["address"], 16)
        claimed.update(range(address, address + item["width"]))
    return claimed


def startup_table(name: str) -> dict:
    image = PROFILES[name]["reference"].read_bytes()
    values = {
        0xA300 + offset: int.from_bytes(
            image[0x6300 + offset : 0x6302 + offset], "little"
        )
        for offset in range(0, 22, 2)
    }
    expected = {
        0xA300: 0xFDFE,
        0xA302: 0xD800,
        0xA304: 0xDBFE,
        0xA306: 0xE420,
        0xA308: 0xF7FE,
        0xA30A: 0xDC00,
        0xA30C: 0xE41E,
        0xA30E: 0xD080,
        0xA310: 0xD0FF,
        0xA312: STARTUP_XRAM_END[name] - 1,
        0xA314: 0xFA00,
    }
    if values != expected:
        raise RuntimeError(f"{name} startup RAM-test table changed")
    return {f"0x{key:04X}": f"0x{value:04X}" for key, value in values.items()}


def certify_variant(
    name: str,
    raw: dict,
    source_image: bytes,
    source_live: dict[tuple[int, str, str, int], dict],
) -> dict:
    profile = PROFILES[name]
    image = profile["reference"].read_bytes()
    live_unresolved = [
        item
        for item in raw["unresolved_indirect_accesses"]
        if item["direct_control_flow_reachable"]
    ]
    transferred = []
    rule_counts: dict[str, int] = {}
    rules_used: dict[str, dict] = {}
    unclosed = []
    for item in live_unresolved:
        transfer = exact_source_transfer(item, image, source_image, source_live)
        if transfer is not None:
            transferred.append({"pc": item["pc"], **transfer})
            continue
        rule = manual_rule_for(name, item)
        if rule is None:
            unclosed.append(item)
            continue
        rule_counts[rule["name"]] = rule_counts.get(rule["name"], 0) + 1
        rules_used[rule["name"]] = rule
    if unclosed:
        raise RuntimeError(
            f"{name} has unclosed stock-live indirect accesses: "
            + ", ".join(item["pc"] for item in unclosed)
        )

    cp_bases = context_banks(name, raw)
    dpp3_writes, pec3_6_writes = live_special_register_writes(name)
    if dpp3_writes or pec3_6_writes:
        raise RuntimeError(f"{name} DPP3/PEC3-6 invariant changed")

    claimed = record_claimed_bytes(raw["direct_accesses"], True)
    claimed |= record_claimed_bytes(raw["resolved_indirect_accesses"], True)
    claimed |= record_claimed_bytes(raw["logger_claims"])
    claimed |= bytes_for_ranges(((0xFA00, 0xFA45), (0xFB64, 0xFBFF)))
    claimed |= bytes_for_ranges(tuple((base, base + 31) for base in cp_bases))
    claimed |= bytes_for_ranges(((0xFDE0, 0xFDFF),))
    for rule in rules_used.values():
        claimed |= bytes_for_ranges(rule["envelopes"])

    xram = EXPECTED_CERTIFIED[name]["xram"]
    iram = EXPECTED_CERTIFIED[name]["iram"]
    if bytes_for_ranges(xram) & claimed:
        raise RuntimeError(f"{name} XRAM candidate acquired an owner")
    if bytes_for_ranges(iram) & claimed:
        raise RuntimeError(f"{name} IRAM candidate acquired an owner")

    rule_evidence = []
    for rule_name, count in sorted(rule_counts.items()):
        rule = rules_used[rule_name]
        rule_evidence.append(
            {
                "name": rule_name,
                "stock_live_sites": count,
                "pc_range": format_range(*rule["pc"]),
                "effective_address_envelopes": [
                    format_range(*span) for span in rule["envelopes"]
                ],
                "evidence": rule["evidence"],
            }
        )

    return {
        "variant": name,
        "ecu_id": raw["ecu_id"],
        "reference": raw["reference"],
        "reference_sha256": raw["reference_sha256"],
        "control_flow_gate": raw["control_flow_gate"],
        "indirect_closure": {
            "status": "proven for the exact image",
            "stock_live_sites": len(live_unresolved),
            "exact_source_proof_transfers": len(transferred),
            "manual_target_specific_sites": sum(rule_counts.values()),
            "unclosed_sites": len(unclosed),
            "transfer_methods": {
                method: sum(item["method"] == method for item in transferred)
                for method in sorted({item["method"] for item in transferred})
            },
            "manual_rules": rule_evidence,
        },
        "startup_ram_test": {
            "table": startup_table(name),
            "xram_range": format_range(0xD800, STARTUP_XRAM_END[name]),
            "iram_range": "0xFA00-0xFDFF",
            "lifetime": "reset/startup before the main firmware handoff",
        },
        "context_bank_invariant": {
            "status": "proven for the exact image",
            "known_cp_bases": [f"0x{base:04X}" for base in cp_bases],
            "reserved_ranges": [format_range(base, base + 31) for base in cp_bases],
        },
        "system_stack_invariant": {
            "status": "proven for the exact image",
            "normal_runtime_envelope": "0xFB64-0xFBFF",
            "r0_software_stack": "0xFA00-0xFA45",
        },
        "dpp3_invariant": {
            "status": "proven unchanged at reset page 3",
            "stock_live_writes": dpp3_writes,
        },
        "pec3_6_invariant": {
            "status": "proven inactive for the exact image",
            "stock_live_control_or_pointer_writes": pec3_6_writes,
        },
        "loader_lifetimes": {
            "hardware_bsl": {
                "range": "0xFA00-0xFA5F",
                "status": "reserved during built-in bootstrap execution",
            },
            "stock_ds2_download_target": "0xDC20-0xE31F",
            "stock_ram_flash_driver": "0xE320-0xE41F",
            "stock_flash_protocol_state": "0xE420-0xE528",
            "soft_bsl": (
                {
                    "status": "supported by the current repository",
                    "chunk_buffer": "0xE000-0xE3FF",
                    "crc_table": "0xFD60-0xFD7F",
                    "cp": "0xFA00",
                    "stack": "0xFB64-0xFBFF",
                }
                if name == "MS41.2"
                else {
                    "status": (
                        "not supported by the current repository; memory "
                        "non-collision alone is not a port certificate"
                    )
                }
            ),
        },
        "certification": {
            "status": "conditionally certified for exact-image post-startup use",
            "xram": {
                "ranges": range_records(xram),
                "certified_bytes": len(bytes_for_ranges(xram)),
                "word_aligned_certified_bytes": word_bytes(xram),
                "complete_byte_class_map": class_intervals(0xD800, 0xF7FF, xram),
            },
            "iram": {
                "ranges": range_records(iram),
                "certified_bytes": len(bytes_for_ranges(iram)),
                "word_aligned_certified_bytes": word_bytes(iram),
                "complete_byte_class_map": class_intervals(0xFA00, 0xFDFF, iram),
            },
            "conditions": [
                "Use only with the exact image hash shown above.",
                "Initialize only after the startup RAM self-test and main handoff.",
                "Do not expect contents to survive reset.",
                "Do not change DPP3, CP banks, stack bounds, or PEC configuration.",
                "Re-run the analyzer after native control-flow or pointer changes.",
            ],
        },
        "inputs": {
            "listing": {
                "path": raw["listing"],
                "sha256": raw["listing_sha256"],
            },
            "functions": {
                "path": raw["functions"],
                "sha256": raw["functions_sha256"],
            },
            "supplemental": raw["supplemental_inputs"],
            "supporting": raw["supporting_inputs"],
        },
    }


def source_variant(source: dict) -> dict:
    xram = SOURCE_XRAM_CANDIDATES
    iram = SOURCE_IRAM_CANDIDATES
    return {
        "variant": "MS41.3",
        "ecu_id": "12/41/42/59/60/85 family reference",
        "reference": str(SOURCE_REFERENCE),
        "reference_sha256": sha256(SOURCE_REFERENCE),
        "source_report": str(SOURCE_REPORT),
        "source_report_sha256": sha256(SOURCE_REPORT),
        "certification": {
            "status": source["certification"]["status"],
            "xram": {
                "ranges": range_records(xram),
                "certified_bytes": len(bytes_for_ranges(xram)),
                "word_aligned_certified_bytes": 232,
                "complete_byte_class_map": class_intervals(0xD800, 0xF7FF, xram),
            },
            "iram": {
                "ranges": range_records(iram),
                "certified_bytes": len(bytes_for_ranges(iram)),
                "word_aligned_certified_bytes": 94,
                "complete_byte_class_map": class_intervals(0xFA00, 0xFDFF, iram),
            },
        },
        "loader_lifetimes": {
            "soft_bsl": {
                "status": "supported by the current repository",
                "chunk_buffer": "0xE000-0xE3FF",
                "crc_table": "0xFD60-0xFD7F",
            }
        },
    }


def family_intersection(variants: dict[str, dict]) -> dict:
    result = {}
    for region in ("xram", "iram"):
        byte_sets = []
        for variant in variants.values():
            ranges = tuple(
                (
                    int(item["range"].split("-")[0], 16),
                    int(item["range"].split("-")[1], 16),
                )
                for item in variant["certification"][region]["ranges"]
            )
            byte_sets.append(bytes_for_ranges(ranges))
        common = compress_bytes(set.intersection(*byte_sets))
        result[region] = {
            "ranges": range_records(common),
            "certified_bytes": len(bytes_for_ranges(common)),
            "word_aligned_certified_bytes": word_bytes(common),
        }
    return result


def final_report(raw_variants: dict[str, dict]) -> dict:
    source = json.loads(SOURCE_REPORT.read_text(encoding="utf-8"))
    if (
        source["collision_relevant_unbounded_accesses"]
        or source["collision_relevant_unbounded_writes"]
    ):
        raise RuntimeError("MS41.3 source proof is no longer closed")
    source_image = SOURCE_REFERENCE.read_bytes()
    source_live = {
        (
            int(item["pc"], 16),
            item["base_register"],
            item["kind"],
            item["width"],
        ): item
        for item in source["stock_reachable_unresolved_indirect_accesses"]
    }
    variants = {
        name: certify_variant(name, raw, source_image, source_live)
        for name, raw in raw_variants.items()
    }
    variants["MS41.3"] = source_variant(source)
    ordered = {name: variants[name] for name in sorted(variants)}
    return {
        "scope": (
            "Collision-safe post-startup allocation map for the exact canonical "
            "MS41.0/MS41.1/MS41.2/MS41.3 images; not a universal firmware-family map."
        ),
        "method": (
            "MS41.3 certified candidates were re-tested against each exact target "
            "image using conservative control-flow closure, exact source-proof "
            "transfers, target-specific pointer bounds, ECU-ID logger claims, "
            "context banks, stacks, PEC, DPP3, loaders, and startup lifetimes."
        ),
        "source_baseline": {
            "report": str(SOURCE_REPORT),
            "report_sha256": sha256(SOURCE_REPORT),
            "reference": str(SOURCE_REFERENCE),
            "reference_sha256": sha256(SOURCE_REFERENCE),
        },
        "variants": ordered,
        "family_safe_intersection": family_intersection(ordered),
        "limits": [
            "No unlisted range is certified available.",
            "A different firmware hash needs a new run.",
            "Soft-BSL transport/entry support is separate from RAM non-collision.",
            "Hardware execution and readback remain required before deployment.",
        ],
    }


def markdown_report(report: dict) -> str:
    lines = [
        "# MS41 family RAM ownership investigation",
        "",
        "> Isolated evidence report. It does not modify or replace current project documentation.",
        "",
        report["scope"],
        "",
        "## Family-safe intersection",
        "",
        "| region | certified ranges | bytes | word-aligned bytes |",
        "|---|---|---:|---:|",
    ]
    for region in ("xram", "iram"):
        item = report["family_safe_intersection"][region]
        ranges = ", ".join(f"`{span['range']}`" for span in item["ranges"]) or "-"
        lines.append(
            f"| {region.upper()} | {ranges} | {item['certified_bytes']} | "
            f"{item['word_aligned_certified_bytes']} |"
        )
    lines.extend(
        [
            "",
            "## Exact-image results",
            "",
            "| variant | ECU/reference | SHA-256 | XRAM | IRAM |",
            "|---|---|---|---|---|",
        ]
    )
    for name, variant in report["variants"].items():
        cert = variant["certification"]
        xram = ", ".join(f"`{item['range']}`" for item in cert["xram"]["ranges"])
        iram = ", ".join(f"`{item['range']}`" for item in cert["iram"]["ranges"])
        lines.append(
            f"| {name} | `{variant['ecu_id']}` | `{variant['reference_sha256']}` | "
            f"{xram or '-'} | {iram or '-'} |"
        )
    lines.extend(
        [
            "",
            "All ranges are conditional post-startup allocations. Startup overwrites "
            "them, so none is reset-retained.",
            "",
            "## Proof gates",
            "",
            "| variant | reachable instructions | live unresolved sites | exact proof transfers | target-specific sites | unclosed |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for name, variant in report["variants"].items():
        if name == "MS41.3":
            lines.append(
                "| MS41.3 | see source report | see source report | see source report | "
                "see source report | 0 |"
            )
            continue
        closure = variant["indirect_closure"]
        gate = variant["control_flow_gate"]
        lines.append(
            f"| {name} | {gate['reachable_instructions']} | "
            f"{closure['stock_live_sites']} | "
            f"{closure['exact_source_proof_transfers']} | "
            f"{closure['manual_target_specific_sites']} | "
            f"{closure['unclosed_sites']} |"
        )
    for name, variant in report["variants"].items():
        lines.extend(["", f"## {name} complete allocation-class map", ""])
        for region in ("xram", "iram"):
            item = variant["certification"][region]
            lines.extend(
                [
                    f"### {region.upper()}",
                    "",
                    "| range | bytes | class |",
                    "|---|---:|---|",
                ]
            )
            lines.extend(
                f"| `{span['range']}` | {span['bytes']} | {span['status']} |"
                for span in item["complete_byte_class_map"]
            )
        if name != "MS41.3":
            lines.extend(["", "### Target-specific indirect proof families", ""])
            lines.extend(
                [
                    "| proof family | sites | effective address envelopes |",
                    "|---|---:|---|",
                ]
            )
            lines.extend(
                "| {name} | {sites} | {envelopes} |".format(
                    name=rule["name"],
                    sites=rule["stock_live_sites"],
                    envelopes=", ".join(
                        f"`{span}`" for span in rule["effective_address_envelopes"]
                    ),
                )
                for rule in variant["indirect_closure"]["manual_rules"]
            )
    lines.extend(
        [
            "",
            "## Loader boundaries",
            "",
            "- All four stock images use `0xDC20-0xE31F` as the authenticated DS2 "
            "download target, `0xE320-0xE41F` for the RAM flash driver, and "
            "`0xE420-0xE528` for flash-protocol state.",
            "- Current Soft-BSL support is limited to MS41.2/MS41.3. Its chunk buffer "
            "is `0xE000-0xE3FF` and CRC table is `0xFD60-0xFD7F`; neither overlaps "
            "the certified ranges.",
            "- MS41.0/MS41.1 RAM non-collision does not imply Soft-BSL boot, entry, "
            "transport, or flash support.",
            "",
            "## Limits",
            "",
        ]
    )
    lines.extend(f"- {item}" for item in report["limits"])
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--variant", choices=[*PROFILES, "all"], default="all")
    parser.add_argument(
        "--out",
        type=Path,
        default=Path(__file__).parent / "evidence" / "family_raw",
    )
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    variants = PROFILES if args.variant == "all" else (args.variant,)
    raw_variants = {name: scan_variant(name) for name in variants}
    outputs = {
        args.out / f"{name.lower().replace('.', '_')}.json": (
            json.dumps(raw, indent=2, sort_keys=True) + "\n"
        )
        for name, raw in raw_variants.items()
    }
    if args.variant == "all":
        report = final_report(raw_variants)
        report_path = args.out.parent / "family_ram_ownership.json"
        outputs[report_path] = json.dumps(report, indent=2, sort_keys=True) + "\n"
        outputs[report_path.with_suffix(".md")] = markdown_report(report)
    if args.check:
        stale = [
            str(path)
            for path, expected in outputs.items()
            if not path.exists() or path.read_text(encoding="utf-8") != expected
        ]
        if stale:
            print("stale evidence:", *stale, sep="\n  ")
            return 1
        print("family raw evidence: current")
        return 0
    args.out.mkdir(parents=True, exist_ok=True)
    for path, content in outputs.items():
        path.write_text(content, encoding="utf-8")
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
