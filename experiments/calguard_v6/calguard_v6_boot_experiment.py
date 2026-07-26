#!/usr/bin/env python3
"""Build and emulator-test the isolated boot-only CalGuard V6 pair.

Every edit is constrained to the physical 8 KiB boot block represented by
file[0x4000:0x6000]. The script has no registration or ECU I/O path.
"""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import importlib.util
from io import StringIO
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
V5_SCRIPT = (
    ROOT / "experiments" / "calguard_v4" / "calguard_v4_experiment.py"
)
BOOT_FILE_START = 0x4000
BOOT_FILE_END = 0x6000
PREPARE_FILE = 0x5FEA
PREPARE_CPU = PREPARE_FILE ^ 0x4000
PREPARE_LIMIT = 0x5FFC - PREPARE_FILE


def _load_v5():
    spec = importlib.util.spec_from_file_location("calguard_v5_experiment", V5_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {V5_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


V5 = _load_v5()


def _subb_i4(a, byte_reg: int, value: int) -> None:
    if not 0 <= byte_reg <= 15 or not 0 <= value <= 7:
        raise ValueError((byte_reg, value))
    a.emit(0x29, (byte_reg << 4) | value)


def _emit_crc_bit(a, name: str) -> None:
    zero = f"{name}_zero"
    done = f"{name}_done"
    a.jnb_reg_bit(6, 0, zero)
    a.shr_i(6, 1)
    a.xor_i(6, 0xA001)
    a.jmpr(V5.CC_UC, done)
    a.label(zero)
    a.shr_i(6, 1)
    a.label(done)


def assemble_shared_crc() -> tuple[bytes, dict[str, int]]:
    """Pack the Soft-BSL entry and two-bit-unrolled shared core into 78 bytes."""
    a = V5.Assembler(
        V5.SHARED_CRC_CPU,
        {"softbsl_crc_prepare": PREPARE_CPU},
    )
    a.label("softbsl_crc16_check")
    a.push(3)
    a.calla("softbsl_crc_prepare")
    a.pop(3)
    a.movb_r_mem(4 * 2 + 1, 0xE427)  # RH4 = expected CRC high
    a.movb_r_mem(4 * 2, 0xE428)      # RL4 = expected CRC low
    a.cmp_rr(6, 4)
    a.jmpr(V5.CC_NE, "softbsl_crc_bad")
    a.movb_ri4(4 * 2, 0)
    a.rets()
    a.label("softbsl_crc_bad")
    a.movb_ri4(4 * 2, 1)
    a.rets()

    # r4=start, r5=end-exclusive, r6=state. Advances r4; clobbers r3.
    # RH3 is the four-iteration counter while RL3 holds each source byte.
    a.label("crc_range")
    a.label("crc_byte")
    a.cmp_rr(4, 5)
    a.jmpr(V5.CC_EQ, "crc_done")
    a.movb_r_postinc(3 * 2, 4)
    a.xorb_rr(6 * 2, 3 * 2)
    a.movb_ri4(3 * 2 + 1, 4)
    a.label("crc_pair")
    _emit_crc_bit(a, "crc_bit_a")
    _emit_crc_bit(a, "crc_bit_b")
    _subb_i4(a, 3 * 2 + 1, 1)
    a.jmpr(V5.CC_NE, "crc_pair")
    a.srvwdt()
    a.jmpr(V5.CC_UC, "crc_byte")
    a.label("crc_done")
    a.ret()
    return a.finish()


def assemble_prepare(shared_symbols: dict[str, int]) -> tuple[bytes, dict[str, int]]:
    """Soft-BSL-only setup in the boot fragment vacated by CalGuard find_end."""
    a = V5.Assembler(PREPARE_CPU, shared_symbols)
    a.label("softbsl_crc_prepare")
    a.mov_ri(4, 0xD800)
    a.mov_ri(6, 0xFFFF)
    a.jmpa_label(V5.CC_UC, "crc_range")
    return a.finish()


def assemble_helpers() -> tuple[
    dict[str, tuple[int, bytes]], dict[str, int]
]:
    helpers, symbols = V5.assemble_calguard_helpers()
    helpers.pop("find_end")
    symbols.pop("find_end")
    return helpers, symbols


def assemble_main(
    shared_symbols: dict[str, int],
    helper_symbols: dict[str, int],
) -> tuple[bytes, dict[str, int]]:
    """Compact the duplicate classifier and embed the unchanged find_end helper."""
    a = V5.Assembler(
        V5.CAVE_CPU,
        {**shared_symbols, **helper_symbols},
    )

    a.movb_r_mem(5 * 2, 0xE740)
    a.cmpb_ri(5 * 2, 1)
    a.jmpa(V5.CC_EQ, V5.RECOVER_EXIT)

    for reg in (2, 3, 6, 7, 8, 9):
        a.push(reg)
    a.mov_dpp_i(0, 4)

    # Read the program classification pair once for both SS1v2 and legacy paths.
    a.mov_dpp_i(2, 15)
    a.mov_r_mem(6, 0x9A9A)
    a.mov_r_mem(7, 0x9A9C)
    a.mov_dpp_i(2, 0)

    for address, value in (
        (0x33BB, 0x53),
        (0x33BC, 0x53),
        (0x33BD, 0x31),
        (0x33BE, 0x76),
        (0x33BF, 0x32),
    ):
        a.movb_r_mem(4 * 2, address)
        a.cmpb_ri(4 * 2, value)
        a.jmpr(V5.CC_NE, "cal_legacy")

    a.cmp_ri(6, 0x119A)
    a.jmpr(V5.CC_NE, "class_fail")
    a.cmp_ri(7, 0x9063)
    a.jmpr(V5.CC_NE, "class_fail")
    a.jmpr(V5.CC_UC, "compare_compatibility")

    a.label("cal_legacy")
    a.cmp_ri(6, 0x119A)
    a.jmpr(V5.CC_NE, "check_legacy_suffix")
    a.cmp_ri(7, 0x9063)
    a.jmpr(V5.CC_EQ, "class_fail")

    a.label("check_legacy_suffix")
    a.mov_r_mem(6, 0x000E)
    a.mov_dpp_i(0, 0)
    a.mov_r_label(4, "cal_ids")
    a.mov_ri4(5, 6)
    a.label("cal_id_loop")
    a.mov_r_postinc(7, 4)
    a.cmp_rr(6, 7)
    a.jmpr(V5.CC_EQ, "legacy_supported")
    a.sub_i(5, 1)
    a.jmpr(V5.CC_NE, "cal_id_loop")
    a.jmpr(V5.CC_UC, "class_fail")

    a.label("class_fail")
    a.jmpr(V5.CC_UC, "fail")

    a.label("legacy_supported")
    a.mov_dpp_i(0, 4)

    a.label("compare_compatibility")
    a.mov_dpp_i(2, 0)
    a.calla("compatibility_match")
    a.jmpr(V5.CC_NE, "class_fail")
    a.mov_dpp_i(0, 0)

    a.label("classified")
    a.mov_ri4(4, 0)
    a.mov_ri(5, 0x1C14)
    a.mov_ri(6, 0x4711)
    a.calla("crc_range")
    a.mov_r_mem(3, 0x1C80)
    a.cmp_rr(6, 3)
    a.jmpr(V5.CC_NE, "fail")

    a.movb_r_mem(6 * 2 + 1, 0x2066)
    a.movb_r_mem(6 * 2, 0x2067)
    a.mov_ri(4, 0x2100)
    a.mov_ri(5, 0x4000)
    a.calla("find_end")
    a.calla("crc_range")

    a.mov_dpp_i(1, 1)
    a.mov_ri(4, 0x4000)
    a.mov_ri(5, 0x8000)
    a.calla("find_end")
    a.calla("crc_range")

    a.mov_ri4(7, 15)
    a.label("upper_find_page")
    a.mov_dpp_r(2, 7)
    a.mov_ri(4, 0x8000)
    a.mov_ri(5, 0xC000)
    a.calla("find_end")
    a.cmp_rr(4, 5)
    a.jmpr(V5.CC_NE, "upper_found")
    a.sub_i(7, 1)
    a.cmp_ri(7, 8)
    a.jmpr(V5.CC_NC, "upper_find_page")
    a.jmpr(V5.CC_UC, "program_compare")

    a.label("upper_found")
    a.mov_rr(8, 7)
    a.mov_rr(9, 5)
    a.mov_ri4(7, 8)
    a.label("upper_crc_page")
    a.mov_dpp_r(2, 7)
    a.mov_ri(4, 0x8000)
    a.cmp_rr(7, 8)
    a.jmpr(V5.CC_NE, "upper_full_page")
    a.mov_rr(5, 9)
    a.jmpr(V5.CC_UC, "upper_crc_call")
    a.label("upper_full_page")
    a.mov_ri(5, 0xC000)
    a.label("upper_crc_call")
    a.calla("crc_range")
    a.cmp_rr(7, 8)
    a.jmpr(V5.CC_EQ, "program_compare")
    a.add_i(7, 1)
    a.jmpr(V5.CC_UC, "upper_crc_page")

    a.label("program_compare")
    a.mov_r_mem(3, 0x2050)
    a.cmp_rr(6, 3)
    a.jmpr(V5.CC_NE, "fail")

    a.mov_dpp_i(0, 4)
    a.mov_r_mem(4, 0x0000)
    a.cmp_ri(4, 0x004E)
    a.jmpr(V5.CC_NE, "fail")
    a.mov_ri4(4, 0)
    a.mov_ri(7, 20)
    a.label("cal_chain")
    a.mov_r_indirect(5, 4)
    a.cmp_ri(5, 0xFFFF)
    a.jmpr(V5.CC_EQ, "pass")
    a.cmp_ri(5, 0x4000)
    a.jmpr(V5.CC_NC, "fail")
    a.cmp_rr(5, 4)
    a.jmpr(V5.CC_ULE, "fail")
    a.movb_r_mem(6 * 2 + 1, 0x000E)
    a.movb_r_mem(6 * 2, 0x000F)
    a.calla("crc_range")
    a.mov_r_indirect(3, 5)
    a.cmp_rr(6, 3)
    a.jmpr(V5.CC_NE, "fail")
    a.mov_rr(4, 5)
    a.add_i(4, 2)
    a.sub_i(7, 1)
    a.jmpr(V5.CC_NE, "cal_chain")
    a.jmpr(V5.CC_UC, "fail")

    a.label("pass")
    a.mov_ri4(5, 1)
    a.jmpr(V5.CC_UC, "cleanup")
    a.label("fail")
    a.mov_ri4(5, 0)
    a.label("cleanup")
    a.mov_dpp_i(0, 4)
    a.mov_dpp_i(1, 5)
    a.mov_dpp_i(2, 0)
    for reg in (9, 8, 7, 6, 3, 2):
        a.pop(reg)
    a.cmp_ri(5, 1)
    a.jmpa(V5.CC_EQ, V5.BOOT_EXIT)
    a.jmpa(V5.CC_UC, V5.RECOVER_EXIT)

    # Unreachable from either decision; called only while scanning program ends.
    a.label("find_end")
    a.cmp_rr(4, 5)
    a.jmpr(V5.CC_EQ, "find_end_done")
    a.sub_i(5, 1)
    a.movb_r_indirect(3 * 2, 5)
    a.cmpb_ri(3 * 2, 0xFF)
    a.jmpr(V5.CC_EQ, "find_end")
    a.add_i(5, 1)
    a.label("find_end_done")
    a.ret()
    return a.finish()


def assemble() -> tuple[
    bytes,
    bytes,
    bytes,
    dict[str, tuple[int, bytes]],
    dict[str, int],
]:
    helpers, helper_symbols = assemble_helpers()
    shared_crc, shared_symbols = assemble_shared_crc()
    prepare, prepare_symbols = assemble_prepare(shared_symbols)
    main, main_symbols = assemble_main(
        shared_symbols,
        helper_symbols,
    )
    symbols = {
        **shared_symbols,
        **prepare_symbols,
        **helper_symbols,
        **main_symbols,
    }
    return main, shared_crc, prepare, helpers, symbols


def _add_predecessor_guards(descriptor: dict, predecessor: dict) -> None:
    for edit in descriptor["edits"]:
        expected = bytes.fromhex(edit["expect"])
        prior = V5._patched_window(expected, int(edit["off"]), predecessor)
        if prior == expected:
            continue
        upgrades = edit.setdefault("upgrade_expect", [])
        if prior.hex() not in upgrades:
            upgrades.append(prior.hex())


def descriptors(
    main: bytes,
    shared_crc: bytes,
    prepare: bytes,
    helpers: dict[str, tuple[int, bytes]],
    symbols: dict[str, int],
) -> tuple[dict, dict, dict]:
    v5_main, v4_shared, v5_helpers, v5_symbols = V5.assemble()
    soft_v4 = V5.softbsl_descriptor(v4_shared, v5_symbols)
    guard_v5 = V5.calguard_descriptor(v5_main, v5_helpers, v5_symbols)

    soft = V5.softbsl_descriptor(shared_crc, symbols)
    soft.update(
        {
            "label": "V5 boot-only two-bit CRC loader",
            "version": "V5",
            "user_description": (
                "Persistent Soft-BSL loader with a faster boot-resident shared "
                "CRC core; required by CalGuard V6."
            ),
            "description": (
                "Boot-only 0x5A loader with flag-independent two-bit CRC."
            ),
            "status": "ISOLATED EMULATOR EXPERIMENT",
            "tested": False,
        }
    )
    padded_prepare = prepare.ljust(PREPARE_LIMIT, b"\xFF")
    soft["edits"].insert(
        -1,
        V5._upgrade_edit(PREPARE_FILE, padded_prepare, guard_v5),
    )
    soft["shared_crc"].update(
        {
            "prepare": symbols["softbsl_crc_prepare"],
            "used": len(shared_crc),
        }
    )
    _add_predecessor_guards(soft, soft_v4)

    guard = V5.calguard_descriptor(main, helpers, symbols)
    guard.update(
        {
            "label": "V6 boot-only integrity guard",
            "version": "V6",
            "user_description": (
                "Blocks engine operation when firmware identity or integrity "
                "checks fail; all guard code remains in the boot block."
            ),
            "description": (
                "Full boot, program, and calibration integrity using the "
                "boot-resident Soft-BSL V5 CRC core."
            ),
            "status": "ISOLATED EMULATOR EXPERIMENT",
            "tested": False,
        }
    )
    guard["cave"].update(
        {
            "region": "boot-only-fragmented-softbsl-compatible",
            "shared_crc_core": symbols["crc_range"],
            "find_end": symbols["find_end"],
        }
    )
    _add_predecessor_guards(guard, guard_v5)

    assert len(main) <= V5.MAIN_LIMIT
    assert len(shared_crc) <= V5.SHARED_CRC_LIMIT
    assert len(prepare) <= PREPARE_LIMIT
    assert [edit["off"] for edit in soft["edits"]] == [
        0x55A2,
        V5.SHARED_CRC_FILE,
        0x5D92,
        0x5FC4,
        PREPARE_FILE,
        0x5FFC,
    ]
    assert [edit["off"] for edit in guard["edits"]] == [
        V5.SPLICE_FILE,
        V5.CAL_IDS_FILE,
        V5.COMPATIBILITY_LOOP_FILE,
        V5.COMPATIBILITY_ENTRY_FILE,
        V5.CAVE_FILE,
    ]
    for patch in (soft, guard):
        for start, end in V5._ranges(patch):
            assert BOOT_FILE_START <= start < end <= BOOT_FILE_END, (
                patch["id"],
                hex(start),
                hex(end),
            )
    for soft_start, soft_end in V5._ranges(soft):
        for guard_start, guard_end in V5._ranges(guard):
            assert soft_end <= guard_start or guard_end <= soft_start
    return soft, guard_v5, guard


def _apply_upgrade(image: bytes, patch: dict) -> bytes:
    out = bytearray(image)
    for edit in patch["edits"]:
        offset = int(edit["off"])
        data = bytes.fromhex(edit["data"])
        actual = bytes(out[offset:offset + len(data)])
        allowed = {
            bytes.fromhex(edit["expect"]),
            *(bytes.fromhex(value) for value in edit.get("upgrade_expect", [])),
        }
        if actual == data:
            continue
        if actual not in allowed:
            raise AssertionError((patch["id"], hex(offset), actual.hex()))
        out[offset:offset + len(data)] = data
    return bytes(out)


def _patched(image: bytes, soft: dict, guard: dict) -> bytes:
    out = V5._apply_descriptor(image, soft)
    out = V5._apply_descriptor(out, guard)
    V5._assert_identity_unchanged(image, out)
    out, _details = V5.checksum.correct_checksums(
        bytearray(out), correct_program=True
    )
    V5._assert_identity_unchanged(image, out)
    return bytes(out)


def _write_artifacts(
    main: bytes,
    shared_crc: bytes,
    prepare: bytes,
    symbols: dict[str, int],
    soft: dict,
    guard: dict,
) -> None:
    artifacts = {
        "cal_guard_v6_boot_main.hex": main.hex() + "\n",
        "cal_guard_v6_boot_shared_crc.hex": shared_crc.hex() + "\n",
        "softbsl_v5_crc_prepare.hex": prepare.hex() + "\n",
        "cal_guard_v6_boot.json": json.dumps(guard, indent=2) + "\n",
        "softbsl_loader_v5_boot.json": json.dumps(soft, indent=2) + "\n",
    }
    for name, text in artifacts.items():
        (HERE / name).write_text(text, encoding="ascii")
    map_text = "\n".join(
        f"{address:04X} {name}"
        for name, address in sorted(symbols.items(), key=lambda item: item[1])
    )
    (HERE / "cal_guard_v6_boot.map").write_text(
        map_text + "\n", encoding="ascii"
    )


def verify(
    main: bytes,
    shared_crc: bytes,
    prepare: bytes,
    helpers: dict[str, tuple[int, bytes]],
    symbols: dict[str, int],
    soft: dict,
    guard_v5: dict,
    guard: dict,
    test_data_root: Path,
    emulator_root: Path,
) -> None:
    sys.path.insert(0, str(emulator_root))
    from ms41emu import Emulator

    captured = StringIO()
    with redirect_stdout(captured):
        V5.verify(
            main,
            shared_crc,
            helpers,
            symbols,
            soft,
            guard,
            test_data_root,
            emulator_root,
        )
    for line in captured.getvalue().splitlines():
        print(
            line
            .replace("CalGuard V5", "CalGuard V6 boot-only")
            .replace("Soft-BSL V4", "Soft-BSL V5 boot-only")
        )

    refs = V5._find_refs(test_data_root)
    v5_shared = V5.assemble()[1]
    soft_v4 = V5.softbsl_descriptor(v5_shared, V5.assemble()[3])
    print("\nValid-image boot-only comparison:")
    for variant, path in refs.items():
        stock = path.read_bytes()
        v5_image = V5._patched(stock, soft_v4, guard_v5)
        v6_image = _patched(stock, soft, guard)

        migrated = _apply_upgrade(v5_image, soft)
        migrated = _apply_upgrade(migrated, guard)
        migrated, _details = V5.checksum.correct_checksums(
            bytearray(migrated), correct_program=True
        )
        V5._assert_identity_unchanged(stock, migrated)
        assert bytes(migrated) == v6_image, (variant, "V5 migration")

        v5_result = V5._run(Emulator, v5_image)
        v6_result = V5._run(Emulator, v6_image)
        assert v5_result.final_pc == V5.BOOT_EXIT
        assert v6_result.final_pc == V5.BOOT_EXIT

        # Model a failed ordinary full-ROM write that leaves only the protected
        # boot block intact. Normal entry must fail closed; E740 must still take
        # the three-instruction stock-listener path before any missing image data.
        boot_survivor = bytearray(b"\xFF" * len(v6_image))
        boot_survivor[BOOT_FILE_START:BOOT_FILE_END] = v6_image[
            BOOT_FILE_START:BOOT_FILE_END
        ]
        failed_full = V5._run(Emulator, bytes(boot_survivor))
        listener = V5._run(Emulator, bytes(boot_survivor), e740=1)
        assert failed_full.final_pc == V5.RECOVER_EXIT
        assert listener.final_pc == V5.RECOVER_EXIT and listener.steps == 3
        V5._assert_hygiene(failed_full, (variant, "boot-only survivor"))
        V5._assert_hygiene(listener, (variant, "boot-only survivor E740"))

        saved = v5_result.steps - v6_result.steps
        percent = saved * 100 / v5_result.steps
        print(
            f"{variant:6} V5={v5_result.steps:,}  V6={v6_result.steps:,}  "
            f"saved={saved:,} ({percent:.1f}%); "
            "boot-only survivor=RECOVER/E740-3"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--verify",
        action="store_true",
        help="run the canonical emulator matrix; never registers the experiment",
    )
    parser.add_argument(
        "--test-data-root",
        default=r"C:\Users\crist\MS41 Projects\_shared",
    )
    parser.add_argument(
        "--emulator-root",
        default=r"C:\Users\crist\ECU Emulator",
    )
    args = parser.parse_args()

    main_code, shared_crc, prepare, helpers, symbols = assemble()
    soft, guard_v5, guard = descriptors(
        main_code, shared_crc, prepare, helpers, symbols
    )
    _write_artifacts(
        main_code, shared_crc, prepare, symbols, soft, guard
    )
    print(
        f"built isolated boot-only pair: CalGuard V6 main "
        f"{len(main_code)}/{V5.MAIN_LIMIT}; shared CRC "
        f"{len(shared_crc)}/{V5.SHARED_CRC_LIMIT}; Soft-BSL prepare "
        f"{len(prepare)}/{PREPARE_LIMIT}"
    )
    if args.verify:
        verify(
            main_code,
            shared_crc,
            prepare,
            helpers,
            symbols,
            soft,
            guard_v5,
            guard,
            Path(args.test_data_root),
            Path(args.emulator_root),
        )


if __name__ == "__main__":
    main()
