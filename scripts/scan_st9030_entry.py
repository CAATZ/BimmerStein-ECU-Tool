"""Offline scan for possible C166 -> ST9030 code-entry primitives.

This is a triage report, not an exploit finder.  It only reports exact
disassembly evidence: native mailbox call sites, E64A length assignments,
ST9 command-header writes, and indirect jumps.  It never opens a serial port
or modifies firmware.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


LINE_RE = re.compile(r"^(?P<address>[0-9a-f]+):\s+\S+\s+(?P<asm>.*)$", re.I)
HEX_RE = re.compile(r"0x([0-9a-f]+)", re.I)


def read_listing(path: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for number, raw in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        match = LINE_RE.match(raw.strip())
        if not match:
            continue
        rows.append(
            {
                "line": number,
                "address": int(match.group("address"), 16),
                "asm": match.group("asm").strip(),
            }
        )
    return rows


def hit(row: dict[str, object], reason: str) -> dict[str, object]:
    return {"address": f"0x{int(row['address']):06X}", "line": row["line"], "asm": row["asm"], "reason": reason}


def scan(path: Path) -> dict[str, object]:
    rows = read_listing(path)
    mailbox_calls: list[dict[str, object]] = []
    external_input_writes: list[dict[str, object]] = []
    e64a_writes: list[dict[str, object]] = []
    command_writes: list[dict[str, object]] = []
    command_write_sites: list[dict[str, object]] = []
    indirect_jumps: list[dict[str, object]] = []
    table_refs: list[dict[str, object]] = []
    variable_header_writes: list[dict[str, object]] = []
    register_constants: dict[str, int] = {}
    for index, row in enumerate(rows):
        asm = str(row["asm"]).lower()
        constant = re.search(r"movb\s+(rl\d+),#0x([0-9a-f]+)", asm)
        if constant:
            register_constants[constant.group(1)] = int(constant.group(2), 16)
        if re.search(r"\bcalls\s+0x0339ea\b", asm):
            mailbox_calls.append(hit(row, "native generic ST9 mailbox exchange call"))
        if "e64a" in asm and ("movb 0xe64a" in asm or "mov 0xe64a" in asm):
            match = re.search(r"#0x([0-9a-f]+)", asm)
            reason = "E64A constant assignment" if match else "E64A non-constant assignment candidate"
            item = hit(row, reason)
            if match:
                item["value"] = int(match.group(1), 16)
            else:
                source = re.search(r"e64a,(rl\d+)", asm)
                if source and source.group(1) in register_constants:
                    item["value"] = register_constants[source.group(1)]
                    item["reason"] = "E64A assignment from recently established constant"
            e64a_writes.append(item)
        if "0xfeb8" in asm and "mov" in asm:
            command = re.search(r"#0x([0-9a-f]+)", asm)
            item = hit(row, "S1TBUF write")
            if command:
                value = int(command.group(1), 16)
                item["value"] = value
                if 0x100 < value <= 0x10F:
                    command_writes.append(item)
                    command_write_sites.append(item)
            else:
                variable_header_writes.append(item)
        if any(token in asm for token in ("e620", "e621", "e622", "e623", "e624", "e64a", "e625", "e626", "e62e")) and any(
            token in asm for token in ("movb ", "mov ", "movbz ", "movbs ")
        ):
            if "#0x" not in asm and "e64a," not in asm and "e625," not in asm:
                external_input_writes.append(hit(row, "mailbox-related RAM read/write candidate"))
        if "jmpi" in asm:
            item = hit(row, "indirect jump")
            context = [str(previous["asm"]) for previous in rows[max(0, index - 8):index]]
            bases = re.findall(r"add\s+r\d+,#0x([0-9a-f]+)", " ".join(context), re.I)
            if bases:
                item["preceding_rom_table_bases"] = [f"0x{int(base, 16):04X}" for base in bases]
                item["classification"] = "bounded indirect dispatch through a preceding constant table base"
            else:
                item["classification"] = "vector or indirect dispatch; no nearby constant table base detected"
            indirect_jumps.append(item)
        if "#0xa580" in asm:
            table_refs.append(hit(row, "fixed selector dispatch table reference"))

    constants = sorted({int(item["value"]) for item in e64a_writes if "value" in item})
    commands = sorted({int(item["value"]) for item in command_writes if "value" in item})
    fixed_table_jumps = [item for item in indirect_jumps if item.get("preceding_rom_table_bases")]
    tableless_jumps = [item for item in indirect_jumps if not item.get("preceding_rom_table_bases")]
    vector_addresses = {"0x0000EC", "0x00030A", "0x000FF2", "0x001130", "0x0015D6"}
    vector_jumps = [item for item in tableless_jumps if item["address"] in vector_addresses]
    ambiguous_jumps = [item for item in tableless_jumps if item not in vector_jumps]
    return {
        "source": str(path),
        "parsed_instruction_lines": len(rows),
        "mailbox_calls_0339ea": mailbox_calls,
        "mailbox_related_ram_candidates": external_input_writes,
        "e64a_writes": e64a_writes,
        "e64a_constants": [f"0x{value:02X}" for value in constants],
        "s1tbuf_command_writes": command_writes,
        "s1tbuf_command_values": [f"0x{value:03X}" for value in commands],
        "s1tbuf_command_write_sites": command_write_sites,
        "s1tbuf_variable_writes": variable_header_writes,
        "indirect_jumps": indirect_jumps,
        "fixed_selector_table_refs": table_refs,
        "indirect_transfer_summary": {
            "total_jmpi_sites": len(indirect_jumps),
            "fixed_rom_table_dispatches": len(fixed_table_jumps),
            "early_vector_or_interrupt_dispatches": len(vector_jumps),
            "tableless_ambiguous_sites": len(ambiguous_jumps),
            "fixed_rom_table_bases": sorted(
                {base for item in fixed_table_jumps for base in item.get("preceding_rom_table_bases", [])}
            ),
        },
        "c166_memory_service": {
            "entry_file_address": "0x020670",
            "decompiler_symbol": "FUN_020670",
            "inputs": ["E423", "E424", "E425", "E426", "E427"],
            "description": "DS2/C166-side bounded memory service; not an ST9030 mailbox address/length primitive",
            "st9030_code_entry": False,
        },
        "conclusion": {
            "fixed_mailbox_path": bool(mailbox_calls and constants == [1, 9]),
            "caller_selected_st9_address_or_length_found": bool(variable_header_writes),
            "c166_memory_service_is_not_st9030_entry": True,
            "next_step": "No variable S1TBUF write or writable ST9030 call target was found; retain passive capture or invasive donor imaging as the next evidence paths.",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("listing", type=Path)
    parser.add_argument("--json", type=Path, help="write the report as JSON")
    args = parser.parse_args()
    report = scan(args.listing)
    rendered = json.dumps(report, indent=2) + "\n"
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
