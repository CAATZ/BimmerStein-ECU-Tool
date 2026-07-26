#!/usr/bin/env python3
"""Build and viability-test the repaired CalGuard V5 / Soft-BSL V4 pair.

The bench-failed V4/V3 pair and hardware-proven V3/V2 rollback pair remain
separate exact descriptors.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import checksum  # noqa: E402
from engines.patcher import cal_guard_exact  # noqa: E402


V3_PATCH_PATH = (
    ROOT / "engines" / "patcher" / "patches"
    / "cal_guard.json"
)
V3_PATCH = json.loads(V3_PATCH_PATH.read_text(encoding="utf-8"))
V1_PATCH = json.loads(
    (ROOT / "engines" / "patcher" / "patches" / "cal_guard_v1.json").read_text(
        encoding="utf-8"
    )
)
V2_PATCH = json.loads(
    (ROOT / "engines" / "patcher" / "patches" / "cal_guard_v2.json").read_text(
        encoding="utf-8"
    )
)
FAILED_CALGUARD_PATH = (
    ROOT / "engines" / "patcher" / "patches"
    / "cal_guard_v4_bench_failed.json"
)
FAILED_CALGUARD = json.loads(FAILED_CALGUARD_PATH.read_text(encoding="utf-8"))
V2_SOFTBSL_PATH = (
    ROOT / "engines" / "patcher" / "patches" / "softbsl_loader_v2.json"
)
V2_SOFTBSL = json.loads(V2_SOFTBSL_PATH.read_text(encoding="utf-8"))
FAILED_SOFTBSL_PATH = (
    ROOT
    / "engines"
    / "patcher"
    / "patches"
    / "softbsl_loader_v3_bench_failed.json"
)
FAILED_SOFTBSL = json.loads(
    FAILED_SOFTBSL_PATH.read_text(encoding="utf-8")
)
ACTIVE_CALGUARD_PATH = (
    ROOT / "engines" / "patcher" / "patches" / "cal_guard.json"
)
ACTIVE_SOFTBSL_PATH = (
    ROOT / "engines" / "patcher" / "patches" / "softbsl_loader.json"
)
CAVE_CPU = cal_guard_exact.CAVE_CPU
CAVE_FILE = CAVE_CPU ^ 0x4000
MAIN_LIMIT = cal_guard_exact.CAVE_SIZE
SHARED_CRC_FILE = 0x5C32
SHARED_CRC_CPU = SHARED_CRC_FILE ^ 0x4000
SHARED_CRC_LIMIT = 0x5C80 - SHARED_CRC_FILE
CAL_IDS_FILE = 0x5C8C
CAL_IDS_CPU = CAL_IDS_FILE ^ 0x4000
CAL_IDS_LIMIT = 0x5C9B - CAL_IDS_FILE
COMPATIBILITY_LOOP_FILE = 0x5CA0
COMPATIBILITY_LOOP_CPU = COMPATIBILITY_LOOP_FILE ^ 0x4000
COMPATIBILITY_LOOP_LIMIT = 0x5CB6 - COMPATIBILITY_LOOP_FILE
COMPATIBILITY_ENTRY_FILE = 0x5E00
COMPATIBILITY_ENTRY_CPU = COMPATIBILITY_ENTRY_FILE ^ 0x4000
COMPATIBILITY_ENTRY_LIMIT = CAVE_FILE - COMPATIBILITY_ENTRY_FILE
FIND_END_FILE = 0x5FEA
FIND_END_CPU = FIND_END_FILE ^ 0x4000
FIND_END_LIMIT = 0x5FFC - FIND_END_FILE
V3_SPLICE = next(edit for edit in V3_PATCH["edits"] if edit["off"] == 0x493A)
V3_CAVE = next(edit for edit in V3_PATCH["edits"] if edit["off"] == CAVE_FILE)
SPLICE_FILE = V3_SPLICE["off"]
SPLICE_EXPECT = bytes.fromhex(V3_SPLICE["expect"])
SPLICE_DATA = bytes.fromhex(V3_SPLICE["data"])
BOOT_EXIT = cal_guard_exact.BOOT_EXIT
RECOVER_EXIT = cal_guard_exact.RECOVER_EXIT
PRESERVED_SENTINELS = {
    2: 0x2202,
    3: 0x3303,
    6: 0x6606,
    7: 0x7707,
    8: 0x8808,
    9: 0x9909,
}
STACK_SENTINEL = 0xF600

if V3_PATCH.get("version") != "V3":
    raise RuntimeError("integrity experiment requires the registered CalGuard V3")
if bytes.fromhex(V3_CAVE["data"]) != cal_guard_exact.assemble():
    raise RuntimeError("registered CalGuard V3 does not match its generator")

CC_UC = 0x0
CC_EQ = 0x2
CC_NE = 0x3
CC_C = 0x8
CC_NC = 0x9
CC_ULE = 0xF


class Assembler:
    """Small two-pass emitter for only the already-supported C166 forms used here."""

    def __init__(self, origin: int, externals: dict[str, int] | None = None):
        self.origin = origin
        self.code = bytearray()
        self.labels: dict[str, int] = {}
        self.externals = externals or {}
        self.fixups: list[tuple[str, int, str, int]] = []

    @property
    def pc(self) -> int:
        return self.origin + len(self.code)

    def label(self, name: str) -> None:
        if name in self.labels:
            raise ValueError(f"duplicate label: {name}")
        self.labels[name] = self.pc

    def emit(self, *values: int) -> None:
        self.code.extend(value & 0xFF for value in values)

    def word(self, value: int) -> None:
        self.emit(value, value >> 8)

    def mov_ri(self, reg: int, value: int) -> None:
        self.emit(0xE6, 0xF0 + reg, value, value >> 8)

    def mov_ri4(self, reg: int, value: int) -> None:
        if not 0 <= value <= 15:
            raise ValueError(value)
        self.emit(0xE0, (value << 4) | reg)

    def movb_ri4(self, byte_reg: int, value: int) -> None:
        if not 0 <= value <= 15:
            raise ValueError(value)
        self.emit(0xE1, (value << 4) | byte_reg)

    def mov_r_label(self, reg: int, label: str) -> None:
        self.emit(0xE6, 0xF0 + reg, 0, 0)
        self.fixups.append(("word", len(self.code) - 2, label, 0))

    def mov_rr(self, dst: int, src: int) -> None:
        self.emit(0xF0, (dst << 4) | src)

    def mov_r_mem(self, reg: int, address: int) -> None:
        if address & 1:
            raise ValueError(
                f"C166 word access requires an even address, got 0x{address:04X}"
            )
        self.emit(0xF2, 0xF0 + reg, address, address >> 8)

    def movb_r_mem(self, byte_reg: int, address: int) -> None:
        self.emit(0xF3, 0xF0 + byte_reg, address, address >> 8)

    def mov_r_indirect(self, dst: int, pointer: int) -> None:
        self.emit(0xA8, (dst << 4) | pointer)

    def mov_r_postinc(self, dst: int, pointer: int) -> None:
        self.emit(0x98, (dst << 4) | pointer)

    def movb_r_indirect(self, byte_reg: int, pointer: int) -> None:
        self.emit(0xA9, (byte_reg << 4) | pointer)

    def movb_r_postinc(self, byte_reg: int, pointer: int) -> None:
        self.emit(0x99, (byte_reg << 4) | pointer)

    def mov_dpp_i(self, dpp: int, value: int) -> None:
        self.emit(0xE6, dpp, value, value >> 8)

    def mov_dpp_r(self, dpp: int, reg: int) -> None:
        # The native Tasking dual-stack transfer is compact and keeps SP balanced.
        self.push(reg)
        self.emit(0xFC, dpp)

    def cmp_ri(self, reg: int, value: int) -> None:
        self.emit(0x46, 0xF0 + reg, value, value >> 8)

    def cmp_rr(self, left: int, right: int) -> None:
        self.emit(0x40, (left << 4) | right)

    def cmpb_ri(self, byte_reg: int, value: int) -> None:
        self.emit(0x47, 0xF0 + byte_reg, value, 0)

    def cmpb_rr(self, left: int, right: int) -> None:
        self.emit(0x41, (left << 4) | right)

    def add_i(self, reg: int, value: int) -> None:
        if not 0 <= value <= 7:
            raise ValueError(value)
        self.emit(0x08, (reg << 4) | value)

    def sub_i(self, reg: int, value: int) -> None:
        if not 0 <= value <= 7:
            raise ValueError(value)
        self.emit(0x28, (reg << 4) | value)

    def shr_i(self, reg: int, value: int) -> None:
        self.emit(0x7C, (value << 4) | reg)

    def xor_i(self, reg: int, value: int) -> None:
        self.emit(0x56, 0xF0 + reg, value, value >> 8)

    def xorb_rr(self, left: int, right: int) -> None:
        self.emit(0x51, (left << 4) | right)

    def push(self, reg: int) -> None:
        self.emit(0xEC, 0xF0 + reg)

    def pop(self, reg: int) -> None:
        self.emit(0xFC, 0xF0 + reg)

    def srvwdt(self) -> None:
        self.emit(0xA7, 0x58, 0xA7, 0xA7)

    def ret(self) -> None:
        self.emit(0xCB, 0x00)

    def rets(self) -> None:
        self.emit(0xDB, 0x00)

    def jmpr(self, cc: int, label: str) -> None:
        self.emit((cc << 4) | 0x0D, 0)
        self.fixups.append(("rel8w", len(self.code) - 1, label, 0))

    def jnb_reg_bit(self, reg: int, bit: int, label: str) -> None:
        """Jump when a GPR bit is clear; encoding is oracle-verified by ms41emu."""
        if not 0 <= reg <= 15 or not 0 <= bit <= 15:
            raise ValueError((reg, bit))
        self.emit(0x9A, 0xF0 | reg, 0, bit << 4)
        self.fixups.append(("rel8w4", len(self.code) - 2, label, 0))

    def jmpa(self, cc: int, address: int) -> None:
        self.emit(0xEA, cc << 4, address, address >> 8)

    def jmpa_label(self, cc: int, label: str) -> None:
        self.emit(0xEA, cc << 4, 0, 0)
        self.fixups.append(("word", len(self.code) - 2, label, 0))

    def calla(self, label: str) -> None:
        self.emit(0xCA, 0x00, 0, 0)
        self.fixups.append(("word", len(self.code) - 2, label, 0))

    def finish(self) -> tuple[bytes, dict[str, int]]:
        for kind, position, label, addend in self.fixups:
            target = self.labels.get(label, self.externals.get(label))
            if target is None:
                raise ValueError(f"unknown label: {label}")
            target += addend
            if kind == "word":
                self.code[position:position + 2] = target.to_bytes(2, "little")
                continue
            instruction = self.origin + position - (
                2 if kind == "rel8w4" else 1
            )
            instruction_len = 4 if kind == "rel8w4" else 2
            delta = target - (instruction + instruction_len)
            if delta & 1:
                raise ValueError(f"unaligned relative branch to {label}")
            rel = delta // 2
            if not -128 <= rel <= 127:
                raise ValueError(f"relative branch out of range to {label}: {rel}")
            self.code[position] = rel & 0xFF
        return bytes(self.code), dict(self.labels)


def _emit_crc_range(a: Assembler) -> None:
    """Emit the one CRC-16 core shared by CalGuard and Soft-BSL."""
    # r4=start, r5=end-exclusive, r6=state. Advances r4; clobbers r2/r3.
    a.label("crc_range")
    a.cmp_rr(4, 5)
    a.jmpr(CC_EQ, "crc_done")
    a.movb_r_postinc(3 * 2, 4)  # RL3,[r4+]
    a.xorb_rr(6 * 2, 3 * 2)     # RL6 ^= RL3
    a.mov_ri4(2, 8)
    a.label("crc_bit")
    # Test the source bit directly. The failed pair shifted first and branched
    # on a PSW flag whose one-bit SHR semantics differed between the emulator's
    # SLEIGH model and the physical C166.
    a.jnb_reg_bit(6, 0, "crc_zero_bit")
    a.shr_i(6, 1)
    a.xor_i(6, 0xA001)
    a.jmpr(CC_UC, "crc_bit_done")
    a.label("crc_zero_bit")
    a.shr_i(6, 1)
    a.label("crc_bit_done")
    a.sub_i(2, 1)
    a.jmpr(CC_NE, "crc_bit")
    a.srvwdt()
    a.jmpr(CC_UC, "crc_range")
    a.label("crc_done")
    a.ret()


def assemble_shared_crc() -> tuple[bytes, dict[str, int]]:
    a = Assembler(SHARED_CRC_CPU)

    # Preserve Soft-BSL's fixed 0x1C32 entry and RL4=0/1 return contract.
    # The upload end remains in r5; the generic core advances only r4.
    a.label("softbsl_crc16_check")
    a.push(2)
    a.push(3)
    a.mov_ri(4, 0xD800)
    a.mov_ri(6, 0xFFFF)
    a.calla("crc_range")
    a.pop(3)
    a.pop(2)
    a.movb_r_mem(4 * 2 + 1, 0xE427)  # RH4 = expected CRC high
    a.movb_r_mem(4 * 2, 0xE428)      # RL4 = expected CRC low
    a.cmp_rr(6, 4)
    a.jmpr(CC_NE, "softbsl_crc_bad")
    a.movb_ri4(4 * 2, 0)
    a.rets()
    a.label("softbsl_crc_bad")
    a.movb_ri4(4 * 2, 1)
    a.rets()

    _emit_crc_range(a)
    return a.finish()


def assemble_calguard_helpers() -> tuple[
    dict[str, tuple[int, bytes]], dict[str, int]
]:
    segments: dict[str, tuple[int, bytes]] = {}
    symbols: dict[str, int] = {}

    # V3's supported legacy calibration suffixes. Read only while DPP0=0.
    ids = Assembler(CAL_IDS_CPU)
    ids.label("cal_ids")
    for signature in (
        0x3231,  # "12"
        0x3036,  # "60"
        0x3134,  # "41"
        0x3234,  # "42"
        0x3935,  # "59"
        0x3538,  # "85"
    ):
        ids.word(signature)
    code, labels = ids.finish()
    segments["cal_ids"] = (CAL_IDS_FILE, code)
    symbols.update(labels)

    # Return with Z=1 only after all four exact compatibility bytes match.
    loop = Assembler(COMPATIBILITY_LOOP_CPU)
    loop.label("compatibility_loop")
    loop.movb_r_postinc(3 * 2, 4)  # RL3 = calibration byte
    loop.movb_r_postinc(2 * 2, 5)  # RL2 = program byte
    loop.cmpb_rr(3 * 2, 2 * 2)
    loop.jmpr(CC_NE, "compatibility_done")
    loop.sub_i(7, 1)
    loop.jmpr(CC_NE, "compatibility_loop")
    loop.label("compatibility_done")
    loop.ret()
    # Unreachable revision tag.  It gives the repaired CalGuard an exact
    # installed-state signature without changing any ECU identity bytes or
    # runtime behavior.
    loop.emit(0xCC, 0x00)
    code, labels = loop.finish()
    segments["compatibility_loop"] = (COMPATIBILITY_LOOP_FILE, code)
    symbols.update(labels)

    # The setup stub exactly fills the unused tail after the Soft-BSL main.
    entry = Assembler(COMPATIBILITY_ENTRY_CPU, symbols)
    entry.label("compatibility_match")
    entry.mov_ri(4, 0x000C)
    entry.mov_ri(5, 0xA007)
    entry.mov_ri4(7, 4)
    entry.calla("compatibility_loop")
    entry.ret()
    code, labels = entry.finish()
    segments["compatibility_entry"] = (COMPATIBILITY_ENTRY_FILE, code)
    symbols.update(labels)

    # Input r5 is end-exclusive. Return it trimmed past trailing 0xFF bytes.
    finder = Assembler(FIND_END_CPU)
    finder.label("find_end")
    finder.cmp_rr(4, 5)
    finder.jmpr(CC_EQ, "find_end_done")
    finder.sub_i(5, 1)
    finder.movb_r_indirect(3 * 2, 5)  # RL3,[r5]
    finder.cmpb_ri(3 * 2, 0xFF)
    finder.jmpr(CC_EQ, "find_end")
    finder.add_i(5, 1)
    finder.label("find_end_done")
    finder.ret()
    code, labels = finder.finish()
    segments["find_end"] = (FIND_END_FILE, code)
    symbols.update(labels)

    return segments, symbols


def assemble() -> tuple[
    bytes, bytes, dict[str, tuple[int, bytes]], dict[str, int]
]:
    shared_crc, shared_symbols = assemble_shared_crc()
    helpers, helper_symbols = assemble_calguard_helpers()
    externals = {**shared_symbols, **helper_symbols}
    a = Assembler(CAVE_CPU, externals)

    # Preserve the stock fast listener decision before touching stack or DPPs.
    a.movb_r_mem(5 * 2, 0xE740)  # RL5
    a.cmpb_ri(5 * 2, 1)
    a.jmpa(CC_EQ, RECOVER_EXIT)

    for reg in (2, 3, 6, 7, 8, 9):
        a.push(reg)
    a.mov_dpp_i(0, 4)

    # Mirror production V3 exactly: safe byte reads for odd-address fields,
    # strict SS1v2 pairing, supported legacy CAL suffixes, then all four
    # Firmware Compatibility ID bytes.
    for address, value in (
        (0x33BB, 0x53),
        (0x33BC, 0x53),
        (0x33BD, 0x31),
        (0x33BE, 0x76),
        (0x33BF, 0x32),
    ):
        a.movb_r_mem(4 * 2, address)  # RL4
        a.cmpb_ri(4 * 2, value)
        a.jmpr(CC_NE, "cal_legacy")

    a.mov_dpp_i(2, 15)
    a.mov_r_mem(6, 0x9A9A)
    a.mov_r_mem(7, 0x9A9C)
    a.mov_dpp_i(2, 0)
    a.cmp_ri(6, 0x119A)
    a.jmpr(CC_NE, "class_fail")
    a.cmp_ri(7, 0x9063)
    a.jmpr(CC_NE, "class_fail")
    a.jmpr(CC_UC, "compare_compatibility")

    a.label("cal_legacy")
    a.mov_dpp_i(2, 15)
    a.mov_r_mem(6, 0x9A9A)
    a.mov_r_mem(7, 0x9A9C)
    a.mov_dpp_i(2, 0)
    a.cmp_ri(6, 0x119A)
    a.jmpr(CC_NE, "check_legacy_suffix")
    a.cmp_ri(7, 0x9063)
    a.jmpr(CC_EQ, "class_fail")

    a.label("check_legacy_suffix")
    a.mov_r_mem(6, 0x000E)
    a.mov_dpp_i(0, 0)
    a.mov_r_label(4, "cal_ids")
    a.mov_ri4(5, 6)
    a.label("cal_id_loop")
    a.mov_r_postinc(7, 4)
    a.cmp_rr(6, 7)
    a.jmpr(CC_EQ, "legacy_supported")
    a.sub_i(5, 1)
    a.jmpr(CC_NE, "cal_id_loop")
    a.jmpr(CC_UC, "class_fail")

    a.label("class_fail")
    a.jmpa_label(CC_UC, "fail")

    a.label("legacy_supported")
    a.mov_dpp_i(0, 4)

    a.label("compare_compatibility")
    a.mov_dpp_i(2, 0)
    a.calla("compatibility_match")
    a.jmpr(CC_NE, "class_fail")
    a.mov_dpp_i(0, 0)  # integrity CRC ranges start in physical page zero

    a.label("classified")

    # Boot-sector CRC: CPU 0000:1C14, init 4711, stored LE at 1C80.
    a.mov_ri4(4, 0)
    a.mov_ri(5, 0x1C14)
    a.mov_ri(6, 0x4711)
    a.calla("crc_range")
    a.mov_r_mem(3, 0x1C80)
    a.cmp_rr(6, 3)
    a.jmpr(CC_NE, "fail")

    # Native program chain, including native trailing-FF trimming.
    a.movb_r_mem(6 * 2 + 1, 0x2066)  # RH6 = init high byte
    a.movb_r_mem(6 * 2, 0x2067)      # RL6 = init low byte
    a.mov_ri(4, 0x2100)
    a.mov_ri(5, 0x4000)
    a.calla("find_end")
    a.calla("crc_range")

    a.mov_dpp_i(1, 1)
    a.mov_ri(4, 0x4000)
    a.mov_ri(5, 0x8000)
    a.calla("find_end")
    a.calla("crc_range")

    # Find the last non-FF physical upper-flash page (DPP2 pages 15 down to 8).
    a.mov_ri4(7, 15)
    a.label("upper_find_page")
    a.mov_dpp_r(2, 7)
    a.mov_ri(4, 0x8000)
    a.mov_ri(5, 0xC000)
    a.calla("find_end")
    a.cmp_rr(4, 5)
    a.jmpr(CC_NE, "upper_found")
    a.sub_i(7, 1)
    a.cmp_ri(7, 8)
    a.jmpr(CC_NC, "upper_find_page")
    a.jmpr(CC_UC, "program_compare")

    a.label("upper_found")
    a.mov_rr(8, 7)
    a.mov_rr(9, 5)
    a.mov_ri4(7, 8)
    a.label("upper_crc_page")
    a.mov_dpp_r(2, 7)
    a.mov_ri(4, 0x8000)
    a.cmp_rr(7, 8)
    a.jmpr(CC_NE, "upper_full_page")
    a.mov_rr(5, 9)
    a.jmpr(CC_UC, "upper_crc_call")
    a.label("upper_full_page")
    a.mov_ri(5, 0xC000)
    a.label("upper_crc_call")
    a.calla("crc_range")
    a.cmp_rr(7, 8)
    a.jmpr(CC_EQ, "program_compare")
    a.add_i(7, 1)
    a.jmpr(CC_UC, "upper_crc_page")

    a.label("program_compare")
    a.mov_r_mem(3, 0x2050)
    a.cmp_rr(6, 3)
    a.jmpr(CC_NE, "fail")

    # Native calibration linked chain.  Bound traversal to 20 entries and reject
    # malformed/backward/out-of-window links before using them as flash pointers.
    a.mov_dpp_i(0, 4)
    a.mov_r_mem(4, 0x0000)
    a.cmp_ri(4, 0x004E)
    a.jmpr(CC_NE, "fail")
    a.mov_ri4(4, 0)
    a.mov_ri(7, 20)
    a.label("cal_chain")
    a.mov_r_indirect(5, 4)
    a.cmp_ri(5, 0xFFFF)
    a.jmpr(CC_EQ, "pass")
    a.cmp_ri(5, 0x4000)
    a.jmpr(CC_NC, "fail")
    a.cmp_rr(5, 4)
    a.jmpr(CC_ULE, "fail")
    a.movb_r_mem(6 * 2 + 1, 0x000E)  # RH6 = init high byte
    a.movb_r_mem(6 * 2, 0x000F)      # RL6 = init low byte
    a.calla("crc_range")
    a.mov_r_indirect(3, 5)
    a.cmp_rr(6, 3)
    a.jmpr(CC_NE, "fail")
    a.mov_rr(4, 5)
    a.add_i(4, 2)
    a.sub_i(7, 1)
    a.jmpr(CC_NE, "cal_chain")
    a.jmpr(CC_UC, "fail")

    # One cleanup path for both decisions.  r4/r5 are the stock-clobbered pair.
    a.label("pass")
    a.mov_ri4(5, 1)
    a.jmpr(CC_UC, "cleanup")
    a.label("fail")
    a.mov_ri4(5, 0)
    a.label("cleanup")
    a.mov_dpp_i(0, 4)
    a.mov_dpp_i(1, 5)
    a.mov_dpp_i(2, 0)
    for reg in (9, 8, 7, 6, 3, 2):
        a.pop(reg)
    a.cmp_ri(5, 1)
    a.jmpa(CC_EQ, BOOT_EXIT)
    a.jmpa(CC_UC, RECOVER_EXIT)
    main, main_symbols = a.finish()
    return (
        main,
        shared_crc,
        helpers,
        {**shared_symbols, **helper_symbols, **main_symbols},
    )


def _blank_edit(offset: int, data: bytes) -> dict:
    return {
        "off": offset,
        "expect": (b"\xFF" * len(data)).hex(),
        "data": data.hex(),
    }


def _patched_window(
    original: bytes, offset: int, source_patch: dict
) -> bytes:
    """Overlay one predecessor descriptor onto an edit-sized stock window."""
    result = bytearray(original)
    end = offset + len(result)
    for edit in source_patch["edits"]:
        edit_offset = int(edit["off"])
        edit_data = bytes.fromhex(edit["data"])
        edit_end = edit_offset + len(edit_data)
        lo = max(offset, edit_offset)
        hi = min(end, edit_end)
        if lo < hi:
            result[lo - offset:hi - offset] = edit_data[
                lo - edit_offset:hi - edit_offset
            ]
    return bytes(result)


def _upgrade_edit(
    offset: int, data: bytes, *predecessors: dict
) -> dict:
    """Build one exact stock-or-known-predecessor edit."""
    edit = _blank_edit(offset, data)
    original = bytes.fromhex(edit["expect"])
    upgrades: list[str] = []
    for predecessor in predecessors:
        prior = _patched_window(original, offset, predecessor)
        if prior != original and prior.hex() not in upgrades:
            upgrades.append(prior.hex())
    if upgrades:
        edit["upgrade_expect"] = upgrades
    return edit


def _softbsl_edit(offset: int) -> dict:
    source = next(edit for edit in V2_SOFTBSL["edits"] if edit["off"] == offset)
    data = bytes.fromhex(source["data"])
    edit = _upgrade_edit(offset, data, V2_SOFTBSL, FAILED_SOFTBSL)
    edit["expect"] = source["expect"]
    return edit


def softbsl_descriptor(shared_crc: bytes, symbols: dict[str, int]) -> dict:
    return {
        "id": "softbsl_loader",
        "label": "V4 repaired shared-CRC loader",
        "version": "V4",
        "title": "Soft-BSL 0x5A loader",
        "user_description": (
            "Persistent Soft-BSL loader with repaired CRC validation; "
            "required by CalGuard V5."
        ),
        "description": (
            "Persistent 0x5A loader with flag-independent CRC-16 validation."
        ),
        "status": "EMULATOR VERIFIED - HARDWARE UNTESTED",
        "tested": False,
        "target": "MS41.3",
        "targets": ["MS41.0", "MS41.1", "MS41.2", "MS41.3"],
        "recompute": ["boot_crc"],
        "requires": [],
        "conflicts": [],
        "supersedes": [
            "softbsl_loader_legacy",
            "softbsl_loader_relocated_v1",
            "softbsl_loader_v2",
            "softbsl_loader_v3_bench_failed",
        ],
        "edits": [
            _softbsl_edit(0x55A2),
            _upgrade_edit(
                SHARED_CRC_FILE,
                shared_crc,
                V2_SOFTBSL,
                FAILED_SOFTBSL,
            ),
            _softbsl_edit(0x5D92),
            _softbsl_edit(0x5FC4),
            _softbsl_edit(0x5FFC),
        ],
        "shared_crc": {
            "entry": symbols["softbsl_crc16_check"],
            "core": symbols["crc_range"],
            "file_base": SHARED_CRC_FILE,
            "limit": SHARED_CRC_LIMIT,
            "used": len(shared_crc),
        },
    }


def calguard_descriptor(
    main: bytes,
    helpers: dict[str, tuple[int, bytes]],
    symbols: dict[str, int],
) -> dict:
    predecessor_guards = (
        V1_PATCH,
        V2_PATCH,
        V3_PATCH,
        FAILED_CALGUARD,
    )
    return {
        "id": "cal_guard",
        "label": "V5 integrity guard",
        "version": "V5",
        "title": "CalGuard integrity guard",
        "user_description": (
            "Blocks engine operation when firmware identity or integrity checks "
            "fail, while retaining the stock DS2 listener path."
        ),
        "description": (
            "Checks compatibility plus boot, program, and calibration integrity. "
            "Requires Soft-BSL V4."
        ),
        "status": "EMULATOR VERIFIED - HARDWARE UNTESTED",
        "tested": False,
        "target": "MS41.3",
        "targets": ["MS41.0", "MS41.1", "MS41.2", "MS41.3"],
        "recompute": ["boot_crc", "program"],
        "requires": ["softbsl_loader"],
        "conflicts": [],
        "supersedes": [
            "cal_guard_v1",
            "cal_guard_v2",
            "cal_guard_v3_compatibility",
            "cal_guard_v4_bench_failed",
        ],
        "cave": {
            "region": "sa1-fragmented-softbsl-compatible",
            "base": CAVE_FILE,
            "splice_off": SPLICE_FILE,
            "splice_op": "jmpa",
            "relocatable": False,
            "main_base": CAVE_FILE,
            "main_cpu": CAVE_CPU,
            "main_limit": MAIN_LIMIT,
            "main_used": len(main),
            "helpers": {
                name: {
                    "file_base": offset,
                    "cpu_base": offset ^ 0x4000,
                    "used": len(code),
                }
                for name, (offset, code) in helpers.items()
            },
            "shared_crc_core": symbols["crc_range"],
            "symbols": {name: address for name, address in sorted(symbols.items())},
        },
        "edits": [
            _upgrade_edit(
                SPLICE_FILE,
                SPLICE_DATA,
                *predecessor_guards,
            )
            | {"expect": SPLICE_EXPECT.hex()},
            *(
                _upgrade_edit(offset, code, *predecessor_guards)
                for offset, code in helpers.values()
            ),
            _upgrade_edit(CAVE_FILE, main, *predecessor_guards),
        ],
    }


def _ranges(descriptor: dict) -> list[tuple[int, int]]:
    return [
        (int(edit["off"]), int(edit["off"]) + len(bytes.fromhex(edit["data"])))
        for edit in descriptor["edits"]
    ]


def _assert_pair(soft_descriptor: dict, guard_descriptor: dict) -> None:
    assert soft_descriptor["id"] == "softbsl_loader"
    assert soft_descriptor["version"] == "V4"
    assert guard_descriptor["id"] == "cal_guard"
    assert guard_descriptor["version"] == "V5"
    assert guard_descriptor["requires"] == ["softbsl_loader"]
    assert not soft_descriptor.get("deprecated")
    assert not guard_descriptor.get("deprecated")
    for soft_range in _ranges(soft_descriptor):
        for guard_range in _ranges(guard_descriptor):
            assert (
                soft_range[1] <= guard_range[0]
                or guard_range[1] <= soft_range[0]
            ), (soft_range, guard_range)
    assert (
        soft_descriptor["shared_crc"]["core"]
        == guard_descriptor["cave"]["shared_crc_core"]
    )
    active_crc = next(
        edit["data"] for edit in soft_descriptor["edits"]
        if edit["off"] == SHARED_CRC_FILE
    )
    failed_crc = next(
        edit["data"] for edit in FAILED_SOFTBSL["edits"]
        if edit["off"] == SHARED_CRC_FILE
    )
    assert active_crc != failed_crc
    assert [edit["off"] for edit in soft_descriptor["edits"]] == [
        0x55A2, SHARED_CRC_FILE, 0x5D92, 0x5FC4, 0x5FFC,
    ]
    assert [edit["off"] for edit in guard_descriptor["edits"]] == [
        SPLICE_FILE,
        CAL_IDS_FILE,
        COMPATIBILITY_LOOP_FILE,
        COMPATIBILITY_ENTRY_FILE,
        FIND_END_FILE,
        CAVE_FILE,
    ]


def write_artifacts() -> tuple[
    bytes, bytes, dict[str, tuple[int, bytes]], dict[str, int], dict, dict
]:
    main, shared_crc, helpers, symbols = assemble()
    if len(main) > MAIN_LIMIT:
        raise SystemExit(
            f"CalGuard v4 main is {len(main)} bytes; slot is only {MAIN_LIMIT} bytes"
        )
    limits = {
        "cal_ids": CAL_IDS_LIMIT,
        "compatibility_loop": COMPATIBILITY_LOOP_LIMIT,
        "compatibility_entry": COMPATIBILITY_ENTRY_LIMIT,
        "find_end": FIND_END_LIMIT,
    }
    if len(shared_crc) > SHARED_CRC_LIMIT:
        raise SystemExit(
            f"shared CRC block is {len(shared_crc)} bytes; "
            f"slot is only {SHARED_CRC_LIMIT} bytes"
        )
    for name, (_offset, code) in helpers.items():
        if len(code) > limits[name]:
            raise SystemExit(
                f"{name} helper is {len(code)} bytes; "
                f"slot is only {limits[name]} bytes"
            )

    soft_descriptor = softbsl_descriptor(shared_crc, symbols)
    guard_descriptor = calguard_descriptor(main, helpers, symbols)
    _assert_pair(soft_descriptor, guard_descriptor)
    (HERE / "cal_guard_v5_main.hex").write_text(main.hex() + "\n", encoding="ascii")
    (HERE / "cal_guard_v5_shared_crc.hex").write_text(
        shared_crc.hex() + "\n", encoding="ascii"
    )
    for name, (_offset, code) in helpers.items():
        (HERE / f"cal_guard_v5_{name}.hex").write_text(
            code.hex() + "\n", encoding="ascii"
        )
    (HERE / "cal_guard_v5.json").write_text(
        json.dumps(guard_descriptor, indent=2) + "\n",
        encoding="utf-8",
    )
    (HERE / "softbsl_loader_v4.json").write_text(
        json.dumps(soft_descriptor, indent=2) + "\n",
        encoding="utf-8",
    )
    map_text = "\n".join(
        f"{address:04X} {name}" for name, address in sorted(
            symbols.items(), key=lambda item: item[1]
        )
    )
    (HERE / "cal_guard_v5.map").write_text(map_text + "\n", encoding="ascii")
    return (
        main,
        shared_crc,
        helpers,
        symbols,
        soft_descriptor,
        guard_descriptor,
    )


def register_descriptors(soft_descriptor: dict, guard_descriptor: dict) -> None:
    """Publish the verified pair into this worktree's active patch registry."""
    ACTIVE_SOFTBSL_PATH.write_text(
        json.dumps(soft_descriptor, indent=2) + "\n",
        encoding="utf-8",
    )
    ACTIVE_CALGUARD_PATH.write_text(
        json.dumps(guard_descriptor, indent=2) + "\n",
        encoding="utf-8",
    )


def _find_refs(root: Path) -> dict[str, Path]:
    rules = {
        "MS41.0": ("ref_ms41.0", "full"),
        "MS41.1": ("ref_ms41.1", "full"),
        "MS41.2": ("ref_ms41.2", "full"),
        "MS41.3": ("ref_ms41.3", "stock", "full"),
    }
    images = [
        path for path in root.rglob("*.bin")
        if path.is_file() and path.stat().st_size == checksum.FULL_ROM_SIZE
    ]
    found = {}
    for variant, required in rules.items():
        candidates = []
        for path in images:
            text = path.as_posix().lower()
            name = path.name.lower()
            if all(token in text for token in required):
                if variant == "MS41.3" and "cksum" in name:
                    continue
                candidates.append(path)
        if not candidates:
            raise SystemExit(f"no {variant} full reference found under {root}")
        found[variant] = sorted(candidates)[0]
    return found


def _apply_descriptor(image: bytes, patch: dict) -> bytes:
    out = bytearray(image)
    for edit in patch["edits"]:
        offset = edit["off"]
        expected = bytes.fromhex(edit["expect"])
        data = bytes.fromhex(edit["data"])
        if len(expected) != len(data):
            raise AssertionError((patch["id"], offset, "edit length mismatch"))
        actual = bytes(out[offset:offset + len(expected)])
        if actual != expected:
            raise AssertionError(
                (patch["id"], f"0x{offset:X}", expected.hex(), actual.hex())
            )
        out[offset:offset + len(data)] = data
    return bytes(out)


def _assert_identity_unchanged(stock: bytes, patched: bytes) -> None:
    # Every already-programmed byte in the native descriptor/identity region is
    # immutable. The experimental fragments may consume only erased 0xFF bytes.
    for offset in range(0x5C82, 0x5D92):
        if stock[offset] != 0xFF and patched[offset] != stock[offset]:
            raise AssertionError(("identity/descriptor byte changed", hex(offset)))

    immutable = {
        "1585 + serial/ISN": (0x5CE0, 0x5CEF),
        "coding + VIN": (0x5CF4, 0x5D14),
        "optional descriptor": (0x5D36, 0x5D92),
        "program compatibility ID": (0x6007, 0x600B),
        "calibration compatibility ID": (0x1400C, 0x14010),
    }
    for name, (start, end) in immutable.items():
        if patched[start:end] != stock[start:end]:
            raise AssertionError((name, hex(start), hex(end)))


def _patched(image: bytes, soft_descriptor: dict, guard_descriptor: dict) -> bytes:
    paired = _apply_descriptor(image, soft_descriptor)
    paired = _apply_descriptor(paired, guard_descriptor)
    _assert_identity_unchanged(image, paired)

    out, _details = checksum.correct_checksums(
        bytearray(paired), correct_program=True
    )
    _assert_identity_unchanged(image, out)
    status = checksum.checksum_status(out)
    if not all(status[name] for name in ("boot", "program", "cal")):
        raise AssertionError(status)
    return bytes(out)


def _verify_softbsl_crc(Emulator, image: bytes, symbols: dict[str, int]) -> None:
    # Temporary test-only CALLS trampoline.  The repaired shared block now uses
    # 76/78 bytes, so place the trampoline in an unrelated flash location in
    # the emulator copy instead of overwriting the CRC loop's final branch.
    trampoline_cpu = 0x0100
    trampoline_file = trampoline_cpu ^ 0x4000
    stop = trampoline_cpu + 4
    test_image = bytearray(image)
    test_image[trampoline_file:trampoline_file + 4] = bytes(
        (0xDA, 0x00, symbols["softbsl_crc16_check"] & 0xFF,
         symbols["softbsl_crc16_check"] >> 8)
    )
    payload = bytes(range(1, 38))
    expected = checksum._crc(payload, 0xFFFF)

    for label, stored, wanted in (
        ("valid", expected, 0),
        ("invalid", expected ^ 1, 1),
    ):
        emu = Emulator.load(bytes(test_image))
        for index, value in enumerate(payload):
            emu.write_byte(0xD800 + index, value)
        emu.write_byte(0xE427, stored >> 8)
        emu.write_byte(0xE428, stored & 0xFF)
        emu.reg.r[5] = 0xD800 + len(payload)
        emu.reg.r[2] = 0x2222
        emu.reg.r[3] = 0x3333
        starting_sp = emu.reg.sp
        result = emu.run_from(
            trampoline_cpu, stop_at=(stop,), max_steps=100_000
        )
        assert result.final_pc == stop, (label, result)
        assert result.regs["r"][4] & 0xFF == wanted, (label, result)
        assert result.regs["sp"] == starting_sp, (label, "SP", result)
        assert result.regs["r"][2:4] == [0x2222, 0x3333], (
            label, "r2/r3", result
        )


def _run(Emulator, image: bytes, e740: int = 3):
    emu = Emulator.load(image)
    emu.reg.dpp[0] = 4
    emu.reg.dpp[1] = 5
    emu.reg.dpp[2] = 0
    emu.reg.sp = STACK_SENTINEL
    for reg, value in PRESERVED_SENTINELS.items():
        emu.reg.r[reg] = value
    emu.write_byte(0xE740, e740)
    result = emu.run_from(
        CAVE_CPU, stop_at=(BOOT_EXIT, RECOVER_EXIT), max_steps=25_000_000
    )
    return result


def _run_to(Emulator, image: bytes, stops: tuple[int, ...]):
    emu = Emulator.load(image)
    emu.reg.dpp[0] = 4
    emu.reg.dpp[1] = 5
    emu.reg.dpp[2] = 0
    emu.write_byte(0xE740, 3)
    result = emu.run_from(CAVE_CPU, stop_at=stops, max_steps=25_000_000)
    return result, tuple(emu.reg.r), tuple(emu.reg.dpp)


def _flip_covered_byte(image: bytes, start: int, end: int) -> bytes:
    out = bytearray(image)
    offset = next(
        index for index in range(start, end)
        if out[index] not in (0x00, 0xFF)
    )
    out[offset] ^= 0x01
    return bytes(out)


def _flip_at(image: bytes, offset: int) -> bytes:
    out = bytearray(image)
    out[offset] ^= 0x01
    return bytes(out)


def _mismatch_exact_compatibility_id(image: bytes) -> bytes:
    out = bytearray(image)
    out[0x1400C] ^= 0x01
    out, _details = checksum.correct_checksums(out, correct_program=True)
    status = checksum.checksum_status(out)
    if not all(status[name] for name in ("boot", "program", "cal")):
        raise AssertionError(status)
    return bytes(out)


def _break_second_cal_link(image: bytes) -> bytes:
    first_store = 0x14000 + int.from_bytes(image[0x14000:0x14002], "little")
    second_link = first_store + 2
    return _flip_at(image, second_link)


def _assert_hygiene(result, case) -> None:
    assert result.regs["sp"] == STACK_SENTINEL, (case, "SP", result.regs["sp"])
    assert tuple(result.regs["dpp"][:3]) == (4, 5, 0), (
        case, "DPP", result.regs["dpp"]
    )
    for reg, value in PRESERVED_SENTINELS.items():
        assert result.regs["r"][reg] == value, (
            case, f"r{reg}", result.regs["r"][reg], value
        )


def verify(
    main: bytes,
    shared_crc: bytes,
    helpers: dict[str, tuple[int, bytes]],
    symbols: dict[str, int],
    soft_descriptor: dict,
    guard_descriptor: dict,
    test_data_root: Path, emulator_root: Path,
) -> None:
    sys.path.insert(0, str(emulator_root))
    from ms41emu import Emulator

    refs = _find_refs(test_data_root)
    built = {
        name: _patched(path.read_bytes(), soft_descriptor, guard_descriptor)
        for name, path in refs.items()
    }
    _verify_softbsl_crc(Emulator, next(iter(built.values())), symbols)
    results = []

    for variant, image in built.items():
        valid = _run(Emulator, image)
        if valid.final_pc != BOOT_EXIT:
            debug = _run_to(
                Emulator, image, (symbols["cal_chain"], symbols["fail"])
            )
            raise AssertionError((variant, "valid", valid, debug))
        _assert_hygiene(valid, (variant, "valid"))
        results.append((variant, "valid", "BOOT", valid.steps))

        listener = _run(Emulator, image, e740=1)
        assert listener.final_pc == RECOVER_EXIT, (variant, "E740", listener)
        _assert_hygiene(listener, (variant, "E740=1"))
        results.append((variant, "E740=1", "LISTENER", listener.steps))

        faults = {
            "exact compatibility ID": _mismatch_exact_compatibility_id(image),
            "boot byte": _flip_covered_byte(image, 0x4000, 0x5C14),
            "program low byte": _flip_covered_byte(image, 0x0100, 0x3F00),
            "program upper byte": _flip_covered_byte(image, 0x20000, 0x3F000),
            "cal data byte": _flip_covered_byte(image, 0x14100, 0x19F00),
        }
        for label, broken in faults.items():
            result = _run(Emulator, broken)
            assert result.final_pc == RECOVER_EXIT, (variant, label, result)
            _assert_hygiene(result, (variant, label))
            results.append((variant, label, "RECOVER", result.steps))

        # The four core mutations above cover every family. These extra structural
        # cases exercise the stored fields and malformed-link guards once.
        if variant == "MS41.2":
            first_cal_store = next(checksum._cal_entries(image))[0]
            extra_faults = {
                "boot stored CRC": _flip_at(image, 0x5C80),
                "program stored CRC": _flip_at(image, 0x6050),
                "cal stored CRC": _flip_at(image, first_cal_store),
                "cal chain link": _break_second_cal_link(image),
            }
            for label, broken in extra_faults.items():
                result = _run(Emulator, broken)
                assert result.final_pc == RECOVER_EXIT, (variant, label, result)
                _assert_hygiene(result, (variant, label))
                results.append((variant, label, "RECOVER", result.steps))

            fast_broken = _run(
                Emulator, extra_faults["program stored CRC"], e740=1
            )
            assert fast_broken.final_pc == RECOVER_EXIT and fast_broken.steps == 3
            _assert_hygiene(fast_broken, (variant, "E740=1 + corrupt"))
            results.append(
                (variant, "E740=1 + corrupt", "LISTENER", fast_broken.steps)
            )

    variants = tuple(built)
    for index, variant in enumerate(variants):
        donor = variants[(index + 1) % len(variants)]
        hybrid = bytearray(built[variant])
        hybrid[0x14000:0x1A000] = built[donor][0x14000:0x1A000]
        result = _run(Emulator, bytes(hybrid))
        assert result.final_pc == RECOVER_EXIT, (variant, donor, result)
        _assert_hygiene(result, (variant, donor))
        results.append((variant, f"cal from {donor}", "RECOVER", result.steps))

    print(
        f"CalGuard V5 main: {len(main)}/{MAIN_LIMIT} bytes; "
        f"shared CRC: {len(shared_crc)}/{SHARED_CRC_LIMIT} bytes; "
        f"helpers: {sum(len(code) for _offset, code in helpers.values())} bytes"
    )
    print("Soft-BSL V4 shared CRC entry -> valid=0, invalid=1")
    for variant, case, decision, steps in results:
        print(
            f"{variant:6} {case:20} -> "
            f"{decision:8} ({steps:,} steps)"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--verify", action="store_true",
        help="run all four private references in the canonical emulator",
    )
    parser.add_argument(
        "--test-data-root",
        default=os.environ.get("MS41_TEST_DATA_ROOT", ""),
    )
    parser.add_argument(
        "--emulator-root",
        default=os.environ.get("MS41EMU_ROOT", ""),
    )
    parser.add_argument(
        "--register", action="store_true",
        help="after a successful --verify, replace this worktree's active descriptors",
    )
    args = parser.parse_args()
    if args.register and not args.verify:
        parser.error("--register requires --verify")

    (
        main_code,
        shared_crc,
        helpers,
        symbols,
        soft_descriptor,
        guard_descriptor,
    ) = write_artifacts()
    print(
        f"built isolated CalGuard V5: main {len(main_code)}/{MAIN_LIMIT} bytes; "
        f"shared CRC {len(shared_crc)}/{SHARED_CRC_LIMIT} bytes; "
        f"helpers {sum(len(code) for _offset, code in helpers.values())} bytes"
    )
    if args.verify:
        if not args.test_data_root or not args.emulator_root:
            raise SystemExit(
                "--verify needs --test-data-root/MS41_TEST_DATA_ROOT and "
                "--emulator-root/MS41EMU_ROOT"
            )
        verify(
            main_code,
            shared_crc,
            helpers,
            symbols,
            soft_descriptor,
            guard_descriptor,
            Path(args.test_data_root), Path(args.emulator_root),
        )
        if args.register:
            register_descriptors(soft_descriptor, guard_descriptor)
            print("registered CalGuard V5 / Soft-BSL V4 in this worktree")


if __name__ == "__main__":
    main()
