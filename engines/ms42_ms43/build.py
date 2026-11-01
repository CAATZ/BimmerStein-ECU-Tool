"""Rebuild checked C167 payloads with the supplied assembler integration.

The --verify option checks retained artifacts without rebuilding.
A rebuild uses a disposable project.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
from pprint import pformat
import re
import subprocess
import tempfile


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
ASSEMBLER = ROOT / "engines/softbsl/tools/AssembleC166.java"
VERIFY_PREFIX_SIZE = 0x96
CONFIG = {
    "MS42": {
        "BUFFER_DPP": "0", "BUS_SETUP": "", "IDENTITY": "0x3234",
        "DESCRIPTOR_SEG": "5", "PROGRAM_ID_PTR": "0x02d6", "CAL_ID_PTR": "0x02fa",
        "CAL_MARKER_OFFSET": "0xfff0", "CAL_MARKER_SEG": "4",
        "PROGRAM_MARKER_OFFSET": "0", "PROGRAM_MARKER_SEG": "5",
        "IDENTITY_LAST_BYTE": "        jmpr cc_EQ,identity_done",
        # Stock ADDRSEL2=00C1 selects external SRAM below local MCU space.
        "FLASH_READ_GUARD": "        cmp r9,#0\n        jmpr cc_NE,read_flash\n"
        "        cmp r8,#0xc000\n        jmpa cc_NC,descriptor_error\nread_flash:",
        "CRC_TABLE_CURSOR": "", "CRC_POINTER": "raw_pointer", "CRC_TABLE_HELPER": "",
        # MS42 compares its byte index against the complete count word.
        "CRC_COUNT": "        cmp r4,#0xff\n        jmpa cc_UGT,descriptor_error",
        "CAL_CRC_EARLY": "",
        "CAL_FIRST_END_CHECK": "        cmp r12,#9\n        jmpr cc_NE,crc_first_checked\n"
        "        mov r4,r14\n        and r4,r15\n        cmp r4,#0xffff\n"
        "        jmpa cc_EQ,finalize_error",
        # MS42 finishes CRC traversal before accepting the calibration flag.
        "CAL_EXCEPTION_AFTER": "        cmp r12,#9\n        jmpr cc_NE,crc_compare\n"
        "        mov r8,0xf638\n        mov r9,0xf63a\n"
        "        add r8,#0x1e\n        addc r9,#0\n        calla cc_UC,far_word\n"
        "        cmp r4,#0xffff\n        jmpr cc_NE,crc_compare\n"
        "        mov r8,#0x02d6\n        mov r9,#5\n"
        "        calla cc_UC,indirect_pointer\n        mov r4,r8\n        and r4,#1\n"
        "        xor r4,#1\n        add r4,#0x1b\n        sub r8,r4\n        subc r9,#0\n"
        "        calla cc_UC,far_byte\n        cmp r4,#0xa8\n"
        "        jmpa cc_NE,finalize_error\n        ret\n"
        "crc_compare:",
    },
    "MS43": {
        "BUFFER_DPP": "0xc0",
        "BUS_SETUP": "        mov 0xfe18,#0x1008\n"
        "        mov 0xff0c,#0x45bf\n        mov 0xff14,#0x818e",
        "IDENTITY": "0x3334",
        "DESCRIPTOR_SEG": "6", "PROGRAM_ID_PTR": "0xed46", "CAL_ID_PTR": "0xed5e",
        "CAL_MARKER_OFFSET": "0xfff0", "CAL_MARKER_SEG": "7",
        "PROGRAM_MARKER_OFFSET": "0xfff0", "PROGRAM_MARKER_SEG": "6",
        "IDENTITY_LAST_BYTE": "",
        # The stock boot context maps local MCU space and CS2 SRAM here.
        # Preserve raw provenance: their high flash aliases remain valid.
        "FLASH_READ_GUARD": "        cmp r9,#0\n        jmpr cc_NE,read_check_sram\n"
        "        cmp r8,#0xe000\n        jmpa cc_NC,descriptor_error\n"
        "        jmpr cc_UC,read_flash\nread_check_sram:\n"
        "        cmp r9,#4\n        jmpr cc_NE,read_flash\n"
        "        cmp r8,#0x2000\n        jmpa cc_C,descriptor_error\nread_flash:",
        # Stock's fixed program/cal tables cross between DPP0/1 flash pages.
        # DPP2 addresses SRAM, so consumed table pointers there are not flash.
        "CRC_TABLE_CURSOR": "        and r8,#0x7fff",
        "CRC_POINTER": "crc_table_pointer",
        "CRC_TABLE_HELPER": "crc_table_pointer:\n"
        "        cmp r12,#7\n        jmpa cc_EQ,raw_pointer\n"
        "        cmp r8,#0x8000\n        jmpa cc_NC,descriptor_error\n"
        "        mov r2,r8\n        and r8,#0x3fff\n        mov r9,#7\n"
        "        cmp r2,#0x4000\n        jmpr cc_C,crc_table_mapped\n"
        "        mov r9,#0xe\n        or r8,#0xc000\n"
        "crc_table_mapped:\n        calla cc_UC,raw_pointer\n"
        "        mov r8,r2\n        add r8,#4\n        ret",
        # MS43 reads only the count byte; the adjacent byte is ignored.
        "CRC_COUNT": "        and r4,#0xff",
        # MS43's native A8 bypass ignores seed/count/ranges. Only START's
        # FFFFFFFF sentinel precedes it; unused pointers must not be bounded.
        "CAL_CRC_EARLY": "        mov r8,#0x3fe4\n        mov r9,#7\n"
        "        calla cc_UC,far_word\n        push r4\n        calla cc_UC,far_word\n"
        "        pop r5\n        and r4,r5\n        cmp r4,#0xffff\n"
        "        jmpa cc_EQ,finalize_error\n"
        "        mov r8,#0x3ffe\n        calla cc_UC,far_word\n"
        "        cmp r4,#0xffff\n        jmpr cc_NE,cal_crc_required\n"
        "        mov r8,#0xffb0\n        mov r9,#6\n        calla cc_UC,far_byte\n"
        "        cmp r4,#0xa8\n        jmpa cc_NE,finalize_error\n"
        "        jmpa cc_UC,cal_crc_done\ncal_crc_required:",
        "CAL_FIRST_END_CHECK": "", "CAL_EXCEPTION_AFTER": "",
    },
}
for group, offset in (("PROGRAM", 0x2CE), ("CAL", 0x2F2)):
    CONFIG["MS42"][f"{group}_CRC_SETUP"] = (
        f"        mov r8,#{offset:#x}\n        mov r9,#5\n"
        "        calla cc_UC,far_pointer\n        mov 0xf638,r4\n        mov 0xf63a,r5\n"
        "        calla cc_UC,indirect_pointer\n        calla cc_UC,far_word\n        mov r10,r4\n"
        "        mov r8,0xf638\n        mov r9,0xf63a")
for group, seed, descriptor, segment in (("PROGRAM", 0xED42, 0xFDE0, 6), ("CAL", 0xED5A, 0x3FE0, 7)):
    CONFIG["MS43"][f"{group}_CRC_SETUP"] = (
        f"        mov r8,#{seed:#x}\n        mov r9,#6\n"
        "        calla cc_UC,indirect_pointer\n        calla cc_UC,far_word\n        mov r10,r4\n"
        f"        mov r8,#{descriptor:#x}\n        mov r9,#{segment}")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def resolve(source: str, family: str) -> str:
    for name, value in CONFIG[family].items():
        source = source.replace(f"@{name}@", value)
    return source.replace("@UPLOAD_PADDING@", "        nop\n" * 23)


def upload_crc(data: bytes) -> int:
    value = 0xFFFF
    for byte in data:
        value ^= byte
        for _ in range(8):
            value = (value >> 1) ^ (0xA001 if value & 1 else 0)
    return value


def upload_operands(blob: bytes, symbols: dict) -> dict[int, bytes]:
    """Bind the trusted verifier's fixed-width immediates to its covered body."""
    if symbols["body_start"] != 0xE000 + VERIFY_PREFIX_SIZE:
        raise ValueError("Upload verifier must fit the native 150-byte staging prefix")
    operands = {}
    for name, opcode, value in (
        ("upload_length", b"\xe6\xfd", len(blob) - VERIFY_PREFIX_SIZE),
        ("upload_crc", b"\x46\xfa", upload_crc(blob[VERIFY_PREFIX_SIZE:])),
    ):
        offset = symbols[name] - 0xE000
        if not 0 <= offset <= VERIFY_PREFIX_SIZE - 4 or blob[offset:offset + 2] != opcode:
            raise ValueError("Upload verifier metadata instruction changed")
        operands[offset + 2] = value.to_bytes(2, "little")
    return operands


def verify(*, eeprom=False) -> None:
    """Check checked-in source and emitted bytes without running an assembler."""
    source_name, prefix = ("eeprom.asm", "eeprom_") if eeprom else ("agent.asm", "")
    families = {"MS43"} if eeprom else set(CONFIG)
    source = (HERE / source_name).read_text(encoding="utf-8")
    manifest = json.loads((HERE / f"{prefix}manifest.json").read_text(encoding="utf-8"))
    spec = importlib.util.spec_from_file_location("bulk_payload", HERE / f"{prefix}payload.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    checks = [
        manifest["source_sha256"] == digest(source.encode()),
        manifest["assembler"] == "engines/softbsl/tools/AssembleC166.java",
        manifest["assembler_sha256"] == digest(ASSEMBLER.read_bytes()),
        set(manifest["families"]) == families == set(module.SYMBOLS),
        manifest["language"] == "C166:LE:16:default",
        manifest["load_address"] == "0xE000" and module.BASE == 0xE000,
        module.BUFFER == 0x9000 and module.MAX_BLOCK == 1024,
        module.VERIFY_PREFIX_SIZE == VERIFY_PREFIX_SIZE,
        module.UPLOAD_READY == 0xA5 and module.UPLOAD_REJECTED == 0x5A,
    ]
    for family, record in manifest["families"].items():
        blob = module.payload(family)
        checks.extend([
            0 < len(blob) <= 0x800 and not len(blob) % 2,
            record["size"] == len(blob),
            record["sha256"] == digest(blob),
            record["resolved_source_sha256"] == digest(resolve(source, family).encode()),
            record["symbols"] == module.SYMBOLS[family],
            all(0xE000 <= address < 0xE000 + len(blob) and not address % 2
                for address in record["symbols"].values()),
            all(blob[offset:offset + 2] == value
                for offset, value in upload_operands(blob, record["symbols"]).items()),
        ])
    if not all(checks):
        raise ValueError("C167 bulk agent source, manifest or embedded payload differs; rebuild")
    print(f"{'MS43 EEPROM' if eeprom else 'MS42/MS43 bulk'} agent source, manifest, payloads and symbols verified")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ghidra", type=Path)
    parser.add_argument("--verify", action="store_true", help="check retained bytes without rebuilding")
    parser.add_argument("--eeprom", action="store_true", help="use the separate MS43 EEPROM payload")
    args = parser.parse_args()
    if args.verify:
        verify(eeprom=args.eeprom)
        return
    if args.ghidra is None:
        parser.error("--ghidra is required when rebuilding")
    source_name, prefix = ("eeprom.asm", "eeprom_") if args.eeprom else ("agent.asm", "")
    source = (HERE / source_name).read_text(encoding="utf-8")
    results = {}
    for family in (["MS43"] if args.eeprom else CONFIG):
        resolved = resolve(source, family)
        with tempfile.TemporaryDirectory(prefix="c167-agent-", dir=HERE) as tmp:
            work = Path(tmp)
            asm = work / "agent.asm"
            asm.write_text(resolved, encoding="utf-8")
            dummy = work / "dummy.bin"
            dummy.write_bytes(bytes(0x10000))
            output = work / "agent.hex"
            command = [
                str(args.ghidra), str(work), family, "-import", str(dummy),
                "-processor", "C166:LE:16:default", "-cspec", "tasking",
                "-noanalysis", "-scriptPath", str(ASSEMBLER.parent),
                "-postScript", ASSEMBLER.name, str(asm), str(output), "-deleteProject",
            ]
            run = subprocess.run(command, capture_output=True, text=True, check=False)
            log = run.stdout + run.stderr
            log_path = ROOT / ".tmp" / f"{prefix}bulk-{family.lower()}-assembly.log"
            log_path.parent.mkdir(exist_ok=True)
            log_path.write_text(log, encoding="utf-8")
            if run.returncode or not output.exists():
                raise RuntimeError(f"{family} assembly failed; see {log_path}")
            blob = bytes.fromhex(output.read_text())
            if not 0 < len(blob) <= 0x800 or len(blob) % 2:
                raise ValueError(f"{family} payload length {len(blob)} exceeds native staging")
            addresses = [int(x, 16) for x in re.findall(r"ASM 0x([0-9A-F]+) ", log)]
            labels = {}
            index = 0
            for line in resolved.splitlines():
                line = line.split(";", 1)[0].strip()
                if not line or line.startswith("base "):
                    continue
                if line.endswith(":"):
                    labels[line[:-1]] = addresses[index]
                else:
                    index += 1
            if index != len(addresses):
                raise ValueError("Assembler listing/source instruction count differs")
            bound = bytearray(blob)
            for offset, value in upload_operands(blob, labels).items():
                if blob[offset:offset + 2] != b"\xff\xff":
                    raise ValueError("Upload verifier metadata placeholder changed")
                bound[offset:offset + 2] = value
            blob = bytes(bound)
            results[family] = {"blob": blob, "symbols": labels, "resolved_sha256": digest(resolved.encode())}
    generated = [
        f'"""Generated by build.py; edit {source_name}, never the payload bytes.\n',
        'No firmware files or assembler are needed at application runtime.\n"""\n',
        "BASE = 0xE000\nBUFFER = 0x9000\nMAX_BLOCK = 1024\n",
        "VERIFY_PREFIX_SIZE = 0x96\nUPLOAD_READY = 0xA5\nUPLOAD_REJECTED = 0x5A\n",
        "_PAYLOADS = {\n",
    ]
    for family, result in results.items():
        hex_data = result["blob"].hex()
        generated.append(f'    "{family}": bytes.fromhex(\n')
        generated.extend(f'        "{hex_data[i:i+96]}"\n' for i in range(0, len(hex_data), 96))
        generated.append("    ),\n")
    generated.append("}\n\nSYMBOLS = " + pformat({k: v["symbols"] for k, v in results.items()}, width=88, sort_dicts=False) + "\n\n\n")
    generated.append('def payload(family: str) -> bytes:\n    """Return the checked agent for a qualified ECU family."""\n    return _PAYLOADS[family]\n')
    (HERE / f"{prefix}payload.py").write_text("".join(generated), encoding="utf-8")
    manifest = {
        "source_sha256": digest(source.encode()),
        "assembler": "engines/softbsl/tools/AssembleC166.java",
        "assembler_sha256": digest(ASSEMBLER.read_bytes()),
        "language": "C166:LE:16:default",
        "load_address": "0xE000",
        "families": {
            k: {"size": len(v["blob"]), "sha256": digest(v["blob"]),
                "resolved_source_sha256": v["resolved_sha256"], "symbols": v["symbols"]}
            for k, v in results.items()
        },
    }
    (HERE / f"{prefix}manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    for family, result in results.items():
        print(f'{family}: {len(result["blob"])} bytes, {digest(result["blob"])}')


if __name__ == "__main__":
    main()
