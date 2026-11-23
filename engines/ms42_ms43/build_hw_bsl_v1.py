"""Build/verify revisioned cold C167 loaders with the supplied C167 assembler."""
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

from engines.ms42_ms43.build import ASSEMBLER, upload_crc
from engines.ms42_ms43.payload import payload


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
RATES = {"MS42": {125000: 3, 166667: 2}, "MS43": {125000: 5, 187500: 3}}
CONFIG = {
    "MS42": {
        "BUS_SETUP": "        mov 0xff0c,#0x44bf\n"
        "        mov 0xfe18,#0x1007\n        mov 0xff14,#0x808e\n"
        "        mov 0xfe1a,#1\n        mov 0xff16,#0x040e",
        "TIMER_RELOAD": "0x0f9f", "FAMILY_BYTE": "0x32",
    },
    "MS43": {
        "BUS_SETUP": "        mov 0xff0c,#0x45bf\n"
        "        mov 0xfe18,#0x1008\n        mov 0xff14,#0x818e\n"
        "        mov 0xfe1a,#0x3008\n        mov 0xff16,#0x052e",
        "TIMER_RELOAD": "0x176f", "FAMILY_BYTE": "0x33",
    },
}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def resolved_source(family, baud):
    source = (HERE / "hw_bsl_v1.asm").read_text(encoding="utf-8")
    code = payload(family)
    values = dict(CONFIG[family], BAUD_RELOAD=str(RATES[family][baud]),
                  AGENT_LENGTH=str(len(code)), AGENT_CRC=hex(upload_crc(code)))
    for name, value in values.items():
        source = source.replace(f"@{name}@", value)
    if "@" in source:
        raise ValueError("Unresolved loader operand")
    return source


def assemble(source, name, assembler):
    with tempfile.TemporaryDirectory(prefix="hw-bsl-v1-", dir=HERE) as tmp:
        work = Path(tmp)
        asm, dummy, output = work / "source.asm", work / "dummy.bin", work / "output.hex"
        asm.write_text(source, encoding="utf-8")
        dummy.write_bytes(bytes(0x10000))
        command = [str(assembler), str(work), name, "-import", str(dummy),
                   "-processor", "C166:LE:16:default", "-cspec", "tasking", "-noanalysis",
                   "-scriptPath", str(ASSEMBLER.parent), "-postScript", ASSEMBLER.name,
                   str(asm), str(output), "-deleteProject"]
        run = subprocess.run(command, capture_output=True, text=True, check=False)
        log = run.stdout + run.stderr
        log_path = ROOT / ".tmp" / f"hw-bsl-v1-{name}-assembly.log"
        log_path.parent.mkdir(exist_ok=True)
        log_path.write_text(log, encoding="utf-8")
        if run.returncode or not output.exists():
            raise RuntimeError(f"Assembly failed: {log_path}")
        addresses = [int(x, 16) for x in re.findall(r"ASM 0x([0-9A-F]+) ", log)]
        labels, index = {}, 0
        for line in source.splitlines():
            line = line.split(";", 1)[0].strip()
            if not line or line.startswith("base "):
                continue
            if line.endswith(":"):
                labels[line[:-1]] = addresses[index]
            else:
                index += 1
        if index != len(addresses):
            raise ValueError("Assembler/source instruction count differs")
        return bytes.fromhex(output.read_text()), labels


def verify():
    spec = importlib.util.spec_from_file_location("hw_bsl", HERE / "hw_bsl_v1.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    manifest = json.loads((HERE / "hw_bsl_v1_manifest.json").read_text())
    assert manifest["revision"] == 1
    assert manifest["assembler_sha256"] == digest(ASSEMBLER.read_bytes())
    for name in ("hw_bsl_v1.asm", "hw_bsl_v1_stage0.asm"):
        assert manifest["sources"][name] == digest((HERE / name).read_text(encoding="utf-8").encode())
    assert len(module.stage0()) == 32
    assert manifest["stage0_sha256"] == digest(module.stage0())
    assert module.SUPPORTED_BAUDS == {family: tuple(rates) for family, rates in RATES.items()}
    for family, rates in RATES.items():
        code = payload(family)
        assert module.AGENT_LENGTH[family] == len(code)
        assert module.AGENT_CRC[family] == upload_crc(code)
        assert manifest["agent_sha256"][family] == digest(code)
        for baud in rates:
            record = manifest["variants"][f"{family}-{baud}"]
            blob = module.stage1(family, baud)
            assert len(blob) == 1024 and upload_crc(blob[:-2]) == int.from_bytes(blob[-2:], "little")
            assert record["sha256"] == digest(blob)
            assert record["resolved_sha256"] == digest(resolved_source(family, baud).encode())
            assert record["symbols"] == module.SYMBOLS[family, baud]
    print("HW BSL v1: source, assembler, all 4 variants and unchanged bulk payloads verified")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assembler", type=Path)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    if args.verify:
        verify()
        return
    if args.assembler is None:
        parser.error("--assembler is required for assembly")
    stage0_source = (HERE / "hw_bsl_v1_stage0.asm").read_text(encoding="utf-8")
    first, first_symbols = assemble(stage0_source, "stage0", args.assembler)
    if len(first) != 32:
        raise ValueError(f"ROM stage must be exactly 32 bytes, got {len(first)}")
    results = {}
    for family, rates in RATES.items():
        for baud in rates:
            source = resolved_source(family, baud)
            code, symbols = assemble(source, f"{family}-{baud}", args.assembler)
            if not 0 < len(code) <= 1022 or len(code) % 2:
                raise ValueError("Loader exceeds its F600..F9FF allocation")
            blob = code + bytes(1022 - len(code))
            blob += upload_crc(blob).to_bytes(2, "little")
            results[family, baud] = (blob, symbols, digest(source.encode()), len(code))
    generated = ['"""Generated HW BSL revision 1; edit its ASM and build_hw_bsl_v1.py.\n"""\n',
                 "INITIAL_BAUD = 38400\nSTAGE0_ADDRESS = 0xFA40\nSTAGE1_ADDRESS = 0xF600\n",
                 "STAGE1_SIZE = 1024\nREADY = {'MS42': b'HB42\\x01', 'MS43': b'HB43\\x01'}\n",
                 "ACCEPTED = b'K'\nGO = b'G'\nREJECTED = b'\\x5a'\n",
                 f"SUPPORTED_BAUDS = { {family: tuple(rates) for family, rates in RATES.items()}!r}\n",
                 f"AGENT_LENGTH = { {family: len(payload(family)) for family in RATES}!r}\n",
                 f"AGENT_CRC = { {family: upload_crc(payload(family)) for family in RATES}!r}\n",
                 f"_STAGE0 = bytes.fromhex('{first.hex()}')\n_STAGE1 = {{\n"]
    for key, (blob, _, _, _) in results.items():
        raw = blob.hex()
        generated.append(f"    {key!r}: bytes.fromhex(\n")
        generated.extend(f"        '{raw[i:i+96]}'\n" for i in range(0, len(raw), 96))
        generated.append("    ),\n")
    generated += ["}\nSYMBOLS = " + pformat({key: record[1] for key, record in results.items()}, sort_dicts=False),
                  "\n\n\ndef stage0(family=None, baud=None):\n    return _STAGE0\n",
                  "\n\ndef stage1(family, baud=125000):\n    return _STAGE1[family, baud]\n"]
    (HERE / "hw_bsl_v1.py").write_text("".join(generated), encoding="utf-8")
    manifest = {
        "revision": 1, "assembler_sha256": digest(ASSEMBLER.read_bytes()),
        "sources": {name: digest((HERE / name).read_text(encoding="utf-8").encode())
                    for name in ("hw_bsl_v1.asm", "hw_bsl_v1_stage0.asm")},
        "stage0_sha256": digest(first), "stage0_symbols": first_symbols,
        "agent_sha256": {family: digest(payload(family)) for family in RATES},
        "variants": {f"{family}-{baud}": {"sha256": digest(blob), "symbols": symbols,
                     "resolved_sha256": resolved, "code_size": size}
                     for (family, baud), (blob, symbols, resolved, size) in results.items()},
    }
    (HERE / "hw_bsl_v1_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    verify()


if __name__ == "__main__":
    main()
