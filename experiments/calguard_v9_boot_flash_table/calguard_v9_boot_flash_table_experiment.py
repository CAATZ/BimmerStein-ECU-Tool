#!/usr/bin/env python3
"""Build and test the isolated boot-only CalGuard V9 / Soft-BSL V8 pair.

The CRC nibble table lives in boot flash, never RAM.  This script does not
register patches or communicate with an ECU.
"""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from copy import deepcopy
import hashlib
import importlib.util
from io import StringIO
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
V8_SCRIPT = (
    ROOT
    / "experiments"
    / "calguard_v8_boot_carry"
    / "calguard_v8_boot_carry_experiment.py"
)

WRAPPER_FILE = 0x4412
WRAPPER_CPU = WRAPPER_FILE ^ 0x4000
WRAPPER_LIMIT = 0x4430 - WRAPPER_FILE
TABLE_FILE = 0x5C32
TABLE_CPU = TABLE_FILE ^ 0x4000
TABLE_LOGICAL_DPP0 = TABLE_CPU
TABLE_SIZE = 32
SHARED_FILE = TABLE_FILE + TABLE_SIZE
SHARED_CPU = SHARED_FILE ^ 0x4000
SHARED_LIMIT = 0x5C80 - SHARED_FILE
COMPATIBILITY_LOOP_FILE = 0x5C8B
FIND_END_FILE = 0x5C9F
FIND_END_CPU = FIND_END_FILE ^ 0x4000
FIND_END_LIMIT = 0x5CB6 - FIND_END_FILE
CAL_IDS_FILE = 0x5FB8
CAL_IDS_CPU = CAL_IDS_FILE ^ 0x4000


def _load_v8():
    spec = importlib.util.spec_from_file_location(
        "calguard_v8_boot_carry_experiment", V8_SCRIPT
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {V8_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


V8 = _load_v8()
V6 = V8.V6
V5 = V8.V5


def _nibble_value(value: int) -> int:
    for _ in range(4):
        value = (value >> 1) ^ (0xA001 if value & 1 else 0)
    return value


CRC_TABLE = b"".join(
    _nibble_value(index).to_bytes(2, "little") for index in range(16)
)
assert len(CRC_TABLE) == TABLE_SIZE


def _shl_i(a, reg: int, value: int) -> None:
    a.emit(0x5C, (value << 4) | reg)


def _xor_rr(a, destination: int, source: int) -> None:
    a.emit(0x50, (destination << 4) | source)


def _mov_r_offset(a, destination: int, pointer: int, offset: int) -> None:
    a.emit(0xD4, (destination << 4) | pointer, offset, offset >> 8)


def _emit_nibble_fold(a) -> None:
    # r3 = (r6 & 0x000F) * 2.
    a.mov_rr(3, 6)
    _shl_i(a, 3, 12)
    a.shr_i(3, 11)
    _mov_r_offset(a, 3, 3, TABLE_LOGICAL_DPP0)
    a.shr_i(6, 4)
    _xor_rr(a, 6, 3)


def assemble_wrapper(
    shared_symbols: dict[str, int],
) -> tuple[bytes, dict[str, int]]:
    a = V5.Assembler(
        WRAPPER_CPU,
        {
            **shared_symbols,
            "softbsl_crc_prepare": V6.PREPARE_CPU,
        },
    )
    a.label("softbsl_crc16_check")
    a.calla("softbsl_crc_prepare")
    a.mov_dpp_i(0, 4)
    a.movb_r_mem(4 * 2 + 1, 0xE427)
    a.movb_r_mem(4 * 2, 0xE428)
    a.cmp_rr(6, 4)
    a.jmpr(V5.CC_NE, "softbsl_crc_bad")
    a.movb_ri4(4 * 2, 0)
    a.rets()
    a.label("softbsl_crc_bad")
    a.movb_ri4(4 * 2, 1)
    a.rets()
    code, symbols = a.finish()
    assert len(code) <= WRAPPER_LIMIT
    return code, symbols


def assemble_shared_crc() -> tuple[bytes, dict[str, int]]:
    a = V5.Assembler(SHARED_CPU)
    # r4=start, r5=end-exclusive, r6=state. Advances r4; clobbers r3.
    a.label("crc_range")
    a.label("crc_byte")
    a.cmp_rr(4, 5)
    a.jmpr(V5.CC_EQ, "crc_done")
    a.movb_r_postinc(3 * 2, 4)
    a.xorb_rr(6 * 2, 3 * 2)
    _emit_nibble_fold(a)
    _emit_nibble_fold(a)
    a.srvwdt()
    a.jmpr(V5.CC_UC, "crc_byte")
    a.label("crc_done")
    a.ret()
    code, symbols = a.finish()
    assert len(code) <= SHARED_LIMIT
    return code, symbols


def assemble_prepare(
    shared_symbols: dict[str, int],
) -> tuple[bytes, dict[str, int]]:
    a = V5.Assembler(V6.PREPARE_CPU, shared_symbols)
    a.label("softbsl_crc_prepare")
    a.mov_dpp_i(0, 0)
    a.mov_ri(4, 0xD800)
    a.mov_ri(6, 0xFFFF)
    a.jmpa_label(V5.CC_UC, "crc_range")
    code, symbols = a.finish()
    assert len(code) <= V6.PREPARE_LIMIT
    return code, symbols


class _CalDpp1MainAssembler(V5.Assembler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._cal_pending = False
        self._cal_active = False
        self._cal_start_pending = False
        self._link_bound_pending = False

    def label(self, name: str) -> None:
        super().label(name)
        if name == "program_compare":
            self._cal_pending = True

    def mov_dpp_i(self, dpp: int, value: int) -> None:
        if self._cal_pending and dpp == 0 and value == 4:
            super().mov_dpp_i(1, 4)
            self._cal_pending = False
            self._cal_active = True
            self._cal_start_pending = True
            return
        super().mov_dpp_i(dpp, value)

    def mov_r_mem(self, reg: int, address: int) -> None:
        if self._cal_active and address == 0:
            address = 0x4000
        super().mov_r_mem(reg, address)

    def movb_r_mem(self, byte_reg: int, address: int) -> None:
        if self._cal_active and address in (0x000E, 0x000F):
            address += 0x4000
        super().movb_r_mem(byte_reg, address)

    def mov_ri4(self, reg: int, value: int) -> None:
        if self._cal_start_pending and reg == 4 and value == 0:
            self.mov_ri(4, 0x4000)
            self._cal_start_pending = False
            return
        super().mov_ri4(reg, value)

    def cmp_ri(self, reg: int, value: int) -> None:
        super().cmp_ri(reg, value)
        if self._cal_active and reg == 5 and value == 0x4000:
            self._link_bound_pending = True

    def jmpr(self, cc: int, label: str) -> None:
        super().jmpr(cc, label)
        if self._link_bound_pending:
            assert cc == V5.CC_NC and label == "fail"
            # Fallthrough means the bound check proved bit 14 is clear.
            self.xor_i(5, 0x4000)
            self._link_bound_pending = False


def assemble_main(
    shared_symbols: dict[str, int],
    helper_symbols: dict[str, int],
) -> tuple[bytes, bytes, dict[str, int]]:
    original_assembler = V5.Assembler
    V5.Assembler = _CalDpp1MainAssembler
    try:
        code, symbols = V6.assemble_main(shared_symbols, helper_symbols)
    finally:
        V5.Assembler = original_assembler

    old_find = symbols["find_end"]
    find_offset = old_find - V5.CAVE_CPU
    find_code = code[find_offset:]
    main = bytearray(code[:find_offset])
    old_call = bytes((0xCA, 0x00, old_find & 0xFF, old_find >> 8))
    new_call = bytes((0xCA, 0x00, FIND_END_CPU & 0xFF, FIND_END_CPU >> 8))
    assert main.count(old_call) == 3
    main = bytearray(bytes(main).replace(old_call, new_call))

    symbols["find_end_done"] = (
        FIND_END_CPU + symbols["find_end_done"] - old_find
    )
    symbols["find_end"] = FIND_END_CPU
    assert len(find_code) <= FIND_END_LIMIT
    assert len(main) <= V5.MAIN_LIMIT
    return bytes(main), find_code, symbols


def assemble():
    old_helper_layout = (
        V5.CAL_IDS_FILE,
        V5.CAL_IDS_CPU,
        V5.COMPATIBILITY_LOOP_FILE,
        V5.COMPATIBILITY_LOOP_CPU,
    )
    V5.CAL_IDS_FILE = CAL_IDS_FILE
    V5.CAL_IDS_CPU = CAL_IDS_CPU
    V5.COMPATIBILITY_LOOP_FILE = COMPATIBILITY_LOOP_FILE
    V5.COMPATIBILITY_LOOP_CPU = COMPATIBILITY_LOOP_FILE ^ 0x4000
    try:
        helpers, helper_symbols = V6.assemble_helpers()
    finally:
        (
            V5.CAL_IDS_FILE,
            V5.CAL_IDS_CPU,
            V5.COMPATIBILITY_LOOP_FILE,
            V5.COMPATIBILITY_LOOP_CPU,
        ) = old_helper_layout
    cal_ids = helpers.pop("cal_ids")[1]
    shared, shared_symbols = assemble_shared_crc()
    wrapper, wrapper_symbols = assemble_wrapper(shared_symbols)
    prepare, prepare_symbols = assemble_prepare(shared_symbols)
    main, find_end, main_symbols = assemble_main(
        shared_symbols, helper_symbols
    )
    assert len(main) <= CAL_IDS_FILE - V5.CAVE_FILE
    main = main.ljust(CAL_IDS_FILE - V5.CAVE_FILE, b"\xFF") + cal_ids
    assert len(main) == V5.MAIN_LIMIT
    helpers = {**helpers, "find_end": (FIND_END_FILE, find_end)}
    symbols = {
        **shared_symbols,
        **wrapper_symbols,
        **prepare_symbols,
        **helper_symbols,
        **main_symbols,
    }
    return main, wrapper, shared, prepare, helpers, symbols


def _known_edit(
    template: dict, data: bytes, *predecessors: dict
) -> dict:
    edit = {
        "off": int(template["off"]),
        "expect": template["expect"],
        "data": data.hex(),
    }
    original = bytes.fromhex(edit["expect"])
    upgrades = list(template.get("upgrade_expect", []))
    for predecessor in predecessors:
        prior = V5._patched_window(original, edit["off"], predecessor).hex()
        if prior not in (edit["expect"], edit["data"]) and prior not in upgrades:
            upgrades.append(prior)
    if upgrades:
        edit["upgrade_expect"] = upgrades
    return edit


def descriptors(main, wrapper, shared, prepare, helpers, symbols):
    v5_main, v5_shared, v5_helpers, v5_symbols = V5.assemble()
    soft_v4 = V5.softbsl_descriptor(v5_shared, v5_symbols)
    guard_v5 = V5.calguard_descriptor(v5_main, v5_helpers, v5_symbols)

    v6_parts = V6.assemble()
    soft_v6, _unused_guard_v5, guard_v6 = V6.descriptors(*v6_parts)
    v8_parts = V8.assemble()
    soft_v8, _unused_guard_v5, guard_v8, _old_soft, _old_guard = (
        V8.descriptors(*v8_parts)
    )
    soft_predecessors = (soft_v4, soft_v6, soft_v8)

    old_edits = {int(edit["off"]): edit for edit in soft_v8["edits"]}
    loader_data = bytearray.fromhex(old_edits[0x5D92]["data"])
    old_entry = int(soft_v8["shared_crc"]["entry"])
    old_call = bytes((0xDA, 0x00, old_entry & 0xFF, old_entry >> 8))
    new_call = bytes(
        (
            0xDA,
            0x00,
            symbols["softbsl_crc16_check"] & 0xFF,
            symbols["softbsl_crc16_check"] >> 8,
        )
    )
    assert loader_data.count(old_call) == 1
    loader_data = bytearray(bytes(loader_data).replace(old_call, new_call))

    soft = deepcopy(soft_v8)
    soft["edits"] = [
        deepcopy(old_edits[0x55A2]),
        V5._upgrade_edit(
            WRAPPER_FILE,
            wrapper.ljust(WRAPPER_LIMIT, b"\xFF"),
            guard_v5,
            guard_v6,
            guard_v8,
        ),
        V5._upgrade_edit(TABLE_FILE, CRC_TABLE, *soft_predecessors),
        V5._upgrade_edit(
            SHARED_FILE,
            shared.ljust(SHARED_LIMIT, b"\xFF"),
            *soft_predecessors,
        ),
        _known_edit(
            old_edits[0x5D92], bytes(loader_data), *soft_predecessors
        ),
        deepcopy(old_edits[0x5FC4]),
        V5._upgrade_edit(
            V6.PREPARE_FILE,
            prepare.ljust(V6.PREPARE_LIMIT, b"\xFF"),
            guard_v5,
            soft_v6,
            soft_v8,
        ),
        deepcopy(old_edits[0x5FFC]),
    ]
    soft.update(
        {
            "label": "V8 boot-flash table CRC loader",
            "version": "V8",
            "user_description": (
                "Persistent Soft-BSL loader with a boot-flash CRC table; "
                "required by CalGuard V9."
            ),
            "description": "Boot-only 0x5A loader; no RAM CRC table.",
            "status": "ISOLATED EMULATOR EXPERIMENT",
            "tested": False,
        }
    )
    soft["shared_crc"].update(
        {
            "entry": symbols["softbsl_crc16_check"],
            "core": symbols["crc_range"],
            "file_base": SHARED_FILE,
            "limit": SHARED_LIMIT,
            "used": len(shared),
            "prepare": symbols["softbsl_crc_prepare"],
            "algorithm": "CRC-16/IBM reflected, boot-flash nibble table",
            "table_file": TABLE_FILE,
            "table_cpu": TABLE_CPU,
            "table_size": TABLE_SIZE,
            "table_ram": None,
        }
    )

    padded_main = main.ljust(V5.MAIN_LIMIT, b"\xFF")
    guard = V5.calguard_descriptor(padded_main, helpers, symbols)
    for predecessor in (guard_v5, guard_v6, guard_v8):
        V6._add_predecessor_guards(guard, predecessor)
    guard.update(
        {
            "label": "V9 boot-flash table integrity guard",
            "version": "V9",
            "user_description": (
                "Blocks engine operation when firmware identity or integrity "
                "checks fail; every persistent byte remains in the boot block."
            ),
            "description": (
                "Full boot, program, and calibration integrity using the "
                "boot-flash Soft-BSL V8 CRC core."
            ),
            "status": "ISOLATED EMULATOR EXPERIMENT",
            "tested": False,
        }
    )
    guard["cave"].update(
        {
            "region": "boot-only-fragmented-softbsl-compatible",
            "main_used": len(main),
            "shared_crc_core": symbols["crc_range"],
            "find_end": symbols["find_end"],
            "crc_table_file": TABLE_FILE,
            "crc_table_size": TABLE_SIZE,
        }
    )
    guard["cave"]["helpers"]["cal_ids"] = {
        "file_base": CAL_IDS_FILE,
        "cpu_base": CAL_IDS_CPU,
        "used": 12,
    }

    for patch in (soft, guard):
        for start, end in V5._ranges(patch):
            assert V6.BOOT_FILE_START <= start < end <= V6.BOOT_FILE_END
    for soft_range in V5._ranges(soft):
        for guard_range in V5._ranges(guard):
            assert (
                soft_range[1] <= guard_range[0]
                or guard_range[1] <= soft_range[0]
            )
    return soft, guard_v5, guard, soft_v8, guard_v8


def _write_artifacts(
    main, wrapper, shared, prepare, helpers, symbols, soft, guard
):
    artifacts = {
        "cal_guard_v9_boot_main.hex": main.hex() + "\n",
        "softbsl_v8_crc_wrapper.hex": wrapper.hex() + "\n",
        "cal_guard_v9_boot_shared_crc.hex": shared.hex() + "\n",
        "cal_guard_v9_boot_crc_table.hex": CRC_TABLE.hex() + "\n",
        "softbsl_v8_crc_prepare.hex": prepare.hex() + "\n",
        "softbsl_v8_main.hex": next(
            edit["data"] for edit in soft["edits"]
            if edit["off"] == 0x5D92
        ) + "\n",
        "cal_guard_v9_boot.json": json.dumps(guard, indent=2) + "\n",
        "softbsl_loader_v8_boot.json": json.dumps(soft, indent=2) + "\n",
    }
    for name, text in artifacts.items():
        (HERE / name).write_text(text, encoding="ascii")
    map_text = "\n".join(
        f"{address:04X} {name}"
        for name, address in sorted(symbols.items(), key=lambda item: item[1])
    )
    (HERE / "cal_guard_v9_boot.map").write_text(
        map_text + "\n", encoding="ascii"
    )


def _verify_wrapper_crc(Emulator, image, symbols) -> None:
    trampoline_cpu = 0x0100
    trampoline_file = trampoline_cpu ^ 0x4000
    stop = trampoline_cpu + 4
    test_image = bytearray(image)
    entry = symbols["softbsl_crc16_check"]
    test_image[trampoline_file:trampoline_file + 4] = bytes(
        (0xDA, 0x00, entry & 0xFF, entry >> 8)
    )
    payload = bytes(range(1, 38))
    expected = V5.checksum._crc(payload, 0xFFFF)

    for stored, wanted in ((expected, 0), (expected ^ 1, 1)):
        emu = Emulator.load(bytes(test_image))
        for index, value in enumerate(payload):
            emu.write_byte(0xD800 + index, value)
        emu.write_byte(0xE427, stored >> 8)
        emu.write_byte(0xE428, stored & 0xFF)
        emu.reg.r[5] = 0xD800 + len(payload)
        emu.reg.r[2] = 0x2222
        starting_sp = emu.reg.sp
        result = emu.run_from(
            trampoline_cpu, stop_at=(stop,), max_steps=100_000
        )
        assert result.final_pc == stop
        assert result.regs["r"][4] & 0xFF == wanted
        assert result.regs["r"][2] == 0x2222
        assert result.regs["sp"] == starting_sp


def verify(
    main,
    shared,
    prepare,
    helpers,
    symbols,
    soft,
    guard_v5,
    guard,
    soft_v8,
    guard_v8,
    test_data_root: Path,
    emulator_root: Path,
) -> None:
    V8._use_physical_shr1_model(emulator_root)
    from ms41emu import Emulator

    captured = StringIO()
    original_crc_check = V5._verify_softbsl_crc
    V5._verify_softbsl_crc = _verify_wrapper_crc
    try:
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
    finally:
        V5._verify_softbsl_crc = original_crc_check
    for line in captured.getvalue().splitlines():
        print(
            line.replace("CalGuard V6 boot-only", "CalGuard V9 flash-table")
            .replace("Soft-BSL V5 boot-only", "Soft-BSL V8 flash-table")
            .replace(" V6=", " V9=")
        )

    print("\nV8-to-V9 valid-image performance:")
    for variant, path in V5._find_refs(test_data_root).items():
        stock = path.read_bytes()
        v8_image = V6._patched(stock, soft_v8, guard_v8)
        v9_image = V6._patched(stock, soft, guard)
        v8_result = V5._run(Emulator, v8_image)
        v9_result = V5._run(Emulator, v9_image)
        listener = V5._run(Emulator, v9_image, e740=1)
        assert v8_result.final_pc == v9_result.final_pc == V5.BOOT_EXIT
        assert listener.final_pc == V5.RECOVER_EXIT and listener.steps == 3
        assert v9_result.regs["dpp"][3] == 3
        saved = v8_result.steps - v9_result.steps
        print(
            f"{variant:6} V8={v8_result.steps:,}  V9={v9_result.steps:,}  "
            f"saved={saved:,} ({saved * 100 / v8_result.steps:.1f}%); "
            "DPPs=restored, RAM-table=none"
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
    assert V8._matches(candidate, soft) and V8._matches(candidate, guard)

    changed = V8._diff_ranges(source, candidate)
    assert changed and all(
        V6.BOOT_FILE_START <= start < end <= V6.BOOT_FILE_END
        for start, end in changed
    )

    V8._use_physical_shr1_model(emulator_root)
    from ms41emu import Emulator

    boot = V5._run(Emulator, candidate)
    listener = V5._run(Emulator, candidate, e740=1)
    assert boot.final_pc == V5.BOOT_EXIT
    assert listener.final_pc == V5.RECOVER_EXIT and listener.steps == 3
    assert boot.regs["dpp"][3] == 3
    V5._assert_hygiene(boot, "bench valid")
    V5._assert_hygiene(listener, "bench E740")

    out_dir.mkdir(parents=True, exist_ok=True)
    candidate_path = (
        out_dir
        / f"{base_path.stem}_CALGUARD_V9_SOFTBSL_V8_BOOT_ONLY.bin"
    )
    report_path = (
        out_dir / f"{base_path.stem}_CALGUARD_V9_SOFTBSL_V8_REPORT.txt"
    )
    V8._write_exact(candidate_path, candidate)

    sha = lambda data: hashlib.sha256(data).hexdigest()
    report = "\n".join(
        [
            f"source: {base_path.resolve()}",
            f"source_sha256: {sha(source)}",
            f"candidate: {candidate_path.resolve()}",
            f"candidate_sha256: {sha(candidate)}",
            f"candidate_changed_bytes: {sum(e - s for s, e in changed)}",
            "candidate_changed_ranges: "
            + ", ".join(f"0x{s:05X}..0x{e:05X}" for s, e in changed),
            f"candidate_boot_steps: {boot.steps}",
            f"candidate_e740_listener_steps: {listener.steps}",
            "persistent_edits: boot sector only",
            "crc_ram_table: none",
            f"crc_flash_table: 0x{TABLE_FILE:05X}..0x{TABLE_FILE + TABLE_SIZE:05X}",
            "source_checksums:",
            *(f"  {line}" for line in source_checksums),
            "candidate_checksum_corrections:",
            *(f"  {line}" for line in fixed),
            "candidate_checksums:",
            *(f"  {line}" for line in candidate_checksums),
            "",
        ]
    ).encode()
    V8._write_exact(report_path, report)
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

    main_code, wrapper, shared, prepare, helpers, symbols = assemble()
    soft, guard_v5, guard, soft_v8, guard_v8 = descriptors(
        main_code, wrapper, shared, prepare, helpers, symbols
    )
    _write_artifacts(
        main_code, wrapper, shared, prepare, helpers, symbols, soft, guard
    )
    print(
        f"built isolated flash-table pair: CalGuard V9 main "
        f"{len(main_code)}/{V5.MAIN_LIMIT}; shared CRC "
        f"{len(shared)}/{SHARED_LIMIT}; Soft-BSL prepare "
        f"{len(prepare)}/{V6.PREPARE_LIMIT}; find_end "
        f"{len(helpers['find_end'][1])}/{FIND_END_LIMIT}"
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
            soft_v8,
            guard_v8,
            Path(args.test_data_root),
            Path(args.emulator_root),
        )
    if args.base:
        prepare_bench_image(
            args.base, args.out_dir, soft, guard, Path(args.emulator_root)
        )


if __name__ == "__main__":
    main()
