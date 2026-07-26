#!/usr/bin/env python3
"""Build and test the isolated boot-only CalGuard V8 / Soft-BSL V7 pair.

This removes V7's stack-RAM CRC table. The replacement uses the physical
C166 SHR carry flag and keeps every persistent edit inside the boot block.
The script has no registration or ECU I/O path.
"""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import hashlib
import importlib.util
from io import StringIO
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
V6_SCRIPT = (
    ROOT / "experiments" / "calguard_v6" / "calguard_v6_boot_experiment.py"
)


def _load_v6():
    spec = importlib.util.spec_from_file_location(
        "calguard_v6_boot_experiment", V6_SCRIPT
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {V6_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


V6 = _load_v6()
V5 = V6.V5


def _use_physical_shr1_model(emulator_root: Path) -> None:
    """Correct the emulator's documented SLEIGH-only SHR #1 flag quirk."""
    sys.path.insert(0, str(emulator_root))
    from ms41emu.isa import bits
    from ms41emu.isa.flags import set_post_flags

    oracle_shr = bits.set_shr_flags

    def physical_shr(psw, value: int, count: int) -> int:
        if count != 1:
            return oracle_shr(psw, value, count)
        value &= 0xFFFF
        result = value >> 1
        psw.C = bool(value & 1)
        psw.V = False
        psw.E = False
        set_post_flags(psw, result)
        return result

    bits.set_shr_flags = physical_shr


def _emit_carry_crc_bit(a, name: str) -> None:
    a.shr_i(6, 1)
    a.jmpr(V5.CC_NC, f"{name}_done")
    a.xor_i(6, 0xA001)
    a.label(f"{name}_done")


def assemble_shared_crc() -> tuple[bytes, dict[str, int]]:
    """Pack the Soft-BSL entry and two-bit carry CRC into 78 bytes."""
    a = V5.Assembler(
        V5.SHARED_CRC_CPU,
        {"softbsl_crc_prepare": V6.PREPARE_CPU},
    )
    a.label("softbsl_crc16_check")
    a.push(3)
    a.calla("softbsl_crc_prepare")
    a.pop(3)
    a.movb_r_mem(4 * 2 + 1, 0xE427)
    a.movb_r_mem(4 * 2, 0xE428)
    a.cmp_rr(6, 4)
    a.jmpr(V5.CC_NE, "softbsl_crc_bad")
    a.movb_ri4(4 * 2, 0)
    a.rets()
    a.label("softbsl_crc_bad")
    a.movb_ri4(4 * 2, 1)
    a.rets()

    # r4=start, r5=end-exclusive, r6=state. Advances r4; clobbers r3.
    # SHR #1 puts the old low bit in CARRY on the physical C166.
    a.label("crc_range")
    a.label("crc_byte")
    a.cmp_rr(4, 5)
    a.jmpr(V5.CC_EQ, "crc_done")
    a.movb_r_postinc(3 * 2, 4)
    a.xorb_rr(6 * 2, 3 * 2)
    a.movb_ri4(3 * 2 + 1, 4)
    a.label("crc_pair")
    for index in range(2):
        _emit_carry_crc_bit(a, f"crc_bit_{index}")
    V6._subb_i4(a, 3 * 2 + 1, 1)
    a.jmpr(V5.CC_NE, "crc_pair")
    a.srvwdt()
    a.jmpr(V5.CC_UC, "crc_byte")
    a.label("crc_done")
    a.ret()
    return a.finish()


def assemble():
    helpers, helper_symbols = V6.assemble_helpers()
    shared, shared_symbols = assemble_shared_crc()
    prepare, prepare_symbols = V6.assemble_prepare(shared_symbols)
    main, main_symbols = V6.assemble_main(shared_symbols, helper_symbols)
    symbols = {
        **shared_symbols,
        **prepare_symbols,
        **helper_symbols,
        **main_symbols,
    }
    return main, shared, prepare, helpers, symbols


def descriptors(main, shared, prepare, helpers, symbols):
    soft, guard_v5, guard = V6.descriptors(
        main, shared, prepare, helpers, symbols
    )
    old_main, old_shared, old_prepare, old_helpers, old_symbols = V6.assemble()
    old_soft, _old_guard_v5, old_guard = V6.descriptors(
        old_main, old_shared, old_prepare, old_helpers, old_symbols
    )
    v5_main, v5_shared, v5_helpers, v5_symbols = V5.assemble()
    soft_v4 = V5.softbsl_descriptor(v5_shared, v5_symbols)
    shared_index = next(
        index
        for index, edit in enumerate(soft["edits"])
        if int(edit["off"]) == V5.SHARED_CRC_FILE
    )
    soft["edits"][shared_index] = V5._upgrade_edit(
        V5.SHARED_CRC_FILE,
        shared.ljust(V5.SHARED_CRC_LIMIT, b"\xFF"),
        V5.V2_SOFTBSL,
        V5.FAILED_SOFTBSL,
        soft_v4,
        old_soft,
    )
    V6._add_predecessor_guards(soft, old_soft)
    V6._add_predecessor_guards(guard, old_guard)

    soft.update(
        {
            "label": "V7 boot-only carry-CRC loader",
            "version": "V7",
            "user_description": (
                "Persistent Soft-BSL loader with a boot-only carry-based CRC "
                "core; required by CalGuard V8."
            ),
            "description": "Boot-only 0x5A loader with no persistent RAM table.",
            "status": "ISOLATED EMULATOR EXPERIMENT",
            "tested": False,
        }
    )
    soft["shared_crc"].update(
        {
            "algorithm": "CRC-16/IBM reflected, C166 SHR carry",
            "used": len(shared),
        }
    )
    soft["shared_crc"].pop("table_builder", None)
    soft["shared_crc"].pop("table_ram", None)
    soft["shared_crc"].pop("table_size", None)

    guard.update(
        {
            "label": "V8 boot-only carry-CRC integrity guard",
            "version": "V8",
            "user_description": (
                "Blocks engine operation when firmware identity or integrity "
                "checks fail; every persistent byte remains in the boot block."
            ),
            "description": (
                "Full boot, program, and calibration integrity using the "
                "boot-only Soft-BSL V7 carry CRC core."
            ),
            "status": "ISOLATED EMULATOR EXPERIMENT",
            "tested": False,
        }
    )
    guard["cave"].pop("table_builder", None)
    guard["cave"].pop("table_ram", None)
    guard["cave"]["shared_crc_core"] = symbols["crc_range"]
    return soft, guard_v5, guard, old_soft, old_guard


def _matches(image: bytes, patch: dict) -> bool:
    return all(
        image[int(edit["off"]):int(edit["off"]) + len(bytes.fromhex(edit["data"]))]
        == bytes.fromhex(edit["data"])
        for edit in patch["edits"]
    )


def _diff_ranges(before: bytes, after: bytes) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    start = None
    for offset, (old, new) in enumerate(zip(before, after)):
        if old != new and start is None:
            start = offset
        elif old == new and start is not None:
            ranges.append((start, offset))
            start = None
    if start is not None:
        ranges.append((start, len(before)))
    return ranges


def _write_exact(path: Path, data: bytes) -> None:
    if path.exists() and path.read_bytes() != data:
        raise FileExistsError(f"refusing to overwrite different artifact: {path}")
    path.write_bytes(data)


def _write_artifacts(main, shared, prepare, symbols, soft, guard) -> None:
    artifacts = {
        "cal_guard_v8_boot_main.hex": main.hex() + "\n",
        "cal_guard_v8_boot_shared_crc.hex": shared.hex() + "\n",
        "softbsl_v7_crc_prepare.hex": prepare.hex() + "\n",
        "cal_guard_v8_boot.json": json.dumps(guard, indent=2) + "\n",
        "softbsl_loader_v7_boot.json": json.dumps(soft, indent=2) + "\n",
    }
    for name, text in artifacts.items():
        (HERE / name).write_text(text, encoding="ascii")
    map_text = "\n".join(
        f"{address:04X} {name}"
        for name, address in sorted(symbols.items(), key=lambda item: item[1])
    )
    (HERE / "cal_guard_v8_boot.map").write_text(
        map_text + "\n", encoding="ascii"
    )


def verify(
    main,
    shared,
    prepare,
    helpers,
    symbols,
    soft,
    guard_v5,
    guard,
    old_soft,
    old_guard,
    test_data_root: Path,
    emulator_root: Path,
) -> None:
    _use_physical_shr1_model(emulator_root)
    from ms41emu import Emulator
    print("emulator SHR #1 model: physical C166 carry semantics")

    captured = StringIO()
    with redirect_stdout(captured):
        V6.verify(
            main,
            shared,
            prepare,
            helpers,
            symbols,
            soft,
            guard_v5,
            guard,
            test_data_root,
            emulator_root,
        )
    for line in captured.getvalue().splitlines():
        print(
            line.replace("CalGuard V6 boot-only", "CalGuard V8 boot-carry")
            .replace("Soft-BSL V5 boot-only", "Soft-BSL V7 boot-carry")
        )

    print("\nV6-to-V8 valid-image performance:")
    for variant, path in V5._find_refs(test_data_root).items():
        stock = path.read_bytes()
        old_image = V6._patched(stock, old_soft, old_guard)
        image = V6._patched(stock, soft, guard)
        old_result = V5._run(Emulator, old_image)
        result = V5._run(Emulator, image)
        assert old_result.final_pc == result.final_pc == V5.BOOT_EXIT
        assert not any(
            key in soft["shared_crc"]
            for key in ("table_builder", "table_ram", "table_size")
        )
        saved = old_result.steps - result.steps
        print(
            f"{variant:6} V6={old_result.steps:,}  V8={result.steps:,}  "
            f"saved={saved:,} ({saved * 100 / old_result.steps:.1f}%); "
            "RAM-table=none"
        )


def prepare_bench_image(
    base_path: Path,
    out_dir: Path,
    soft: dict,
    guard: dict,
    emulator_root: Path,
) -> None:
    source = base_path.read_bytes()
    if len(source) != V5.checksum.FULL_ROM_SIZE:
        raise SystemExit(f"{base_path} is not a 256 KiB full read")
    source_ok, source_checksums = V5.checksum.verify_checksum(bytearray(source))
    if not source_ok:
        raise SystemExit("source checksums are invalid:\n" + "\n".join(source_checksums))

    candidate = V6._apply_upgrade(source, soft)
    candidate = V6._apply_upgrade(candidate, guard)
    candidate, fixed = V5.checksum.correct_checksums(
        bytearray(candidate), correct_program=True
    )
    candidate = bytes(candidate)
    V5._assert_identity_unchanged(source, candidate)
    candidate_ok, candidate_checksums = V5.checksum.verify_checksum(
        bytearray(candidate)
    )
    assert candidate_ok, candidate_checksums
    assert _matches(candidate, soft) and _matches(candidate, guard)

    changed = _diff_ranges(source, candidate)
    assert changed and all(
        V6.BOOT_FILE_START <= start < end <= V6.BOOT_FILE_END
        for start, end in changed
    ), changed

    _use_physical_shr1_model(emulator_root)
    from ms41emu import Emulator

    boot = V5._run(Emulator, candidate)
    listener = V5._run(Emulator, candidate, e740=1)
    assert boot.final_pc == V5.BOOT_EXIT
    assert listener.final_pc == V5.RECOVER_EXIT and listener.steps == 3
    V5._assert_hygiene(boot, "bench valid")
    V5._assert_hygiene(listener, "bench E740")

    out_dir.mkdir(parents=True, exist_ok=True)
    stem = base_path.stem
    candidate_path = (
        out_dir / f"{stem}_CALGUARD_V8_SOFTBSL_V7_BOOT_ONLY.bin"
    )
    report_path = out_dir / f"{stem}_CALGUARD_V8_SOFTBSL_V7_REPORT.txt"
    _write_exact(candidate_path, candidate)

    def sha(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    report = "\n".join(
        [
            f"source: {base_path.resolve()}",
            f"source_sha256: {sha(source)}",
            f"candidate: {candidate_path.resolve()}",
            f"candidate_sha256: {sha(candidate)}",
            f"program_id: {source[0x6025:0x602C].decode('ascii', 'replace')}",
            f"cal_marker: {source[0x173BB:0x173C0].decode('ascii', 'replace')}",
            f"program_compatibility_id: {source[0x6007:0x600B].hex()}",
            f"cal_compatibility_id: {source[0x1400C:0x14010].hex()}",
            f"candidate_changed_bytes: {sum(e - s for s, e in changed)}",
            "candidate_changed_ranges: "
            + ", ".join(f"0x{s:05X}..0x{e:05X}" for s, e in changed),
            f"candidate_boot_steps: {boot.steps}",
            f"candidate_e740_listener_steps: {listener.steps}",
            "crc_ram_table: none",
            "source_checksums:",
            *(f"  {line}" for line in source_checksums),
            "candidate_checksum_corrections:",
            *(f"  {line}" for line in fixed),
            "candidate_checksums:",
            *(f"  {line}" for line in candidate_checksums),
            "",
        ]
    ).encode()
    _write_exact(report_path, report)
    print(f"candidate: {candidate_path}")
    print(f"report:    {report_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", action="store_true")
    parser.add_argument(
        "--test-data-root",
        default=r"C:\Users\crist\MS41 Projects\_shared",
    )
    parser.add_argument(
        "--emulator-root",
        default=r"C:\Users\crist\ECU Emulator",
    )
    parser.add_argument("--base", type=Path)
    parser.add_argument(
        "--out-dir", type=Path, default=ROOT / "output" / "bench"
    )
    args = parser.parse_args()

    main_code, shared, prepare, helpers, symbols = assemble()
    soft, guard_v5, guard, old_soft, old_guard = descriptors(
        main_code, shared, prepare, helpers, symbols
    )
    _write_artifacts(main_code, shared, prepare, symbols, soft, guard)
    print(
        f"built isolated boot-carry pair: CalGuard V8 main "
        f"{len(main_code)}/{V5.MAIN_LIMIT}; shared CRC "
        f"{len(shared)}/{V5.SHARED_CRC_LIMIT}; Soft-BSL prepare "
        f"{len(prepare)}/{V6.PREPARE_LIMIT}"
    )
    if args.verify:
        verify(
            main_code,
            shared,
            prepare,
            helpers,
            symbols,
            soft,
            guard_v5,
            guard,
            old_soft,
            old_guard,
            Path(args.test_data_root),
            Path(args.emulator_root),
        )
    if args.base:
        prepare_bench_image(
            args.base, args.out_dir, soft, guard, Path(args.emulator_root)
        )


if __name__ == "__main__":
    main()
