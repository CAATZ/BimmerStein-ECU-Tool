#!/usr/bin/env python3
"""Trace RAM reads/writes made by the existing MS41 emulator scenario tests."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from collections import defaultdict
from pathlib import Path


PROJECTS = Path(__file__).resolve().parents[3]
EMULATOR = PROJECTS.parent / "ECU Emulator"
TESTS = (
    "tests/boot/test_boot_ram_footprint.py",
    "tests/test_ds2.py",
    "tests/test_flash.py",
    "tests/test_fun_024670.py",
)
DIRECT_CASES = (
    ("adc_t3_primary", 0x38AF8, 0x38BA6, 0xFAE8, ((0xFD00, 0, 2), (0xFD02, 0, 2))),
    (
        "adc_t3_secondary",
        0x38AF8,
        0x38BA6,
        0xFAE8,
        ((0xFD00, 0, 2), (0xFD02, 0x10, 2)),
    ),
    ("pin_latch_low", 0x394DC, 0x394E8, 0xFB48, ((0xFF04, 0, 2),)),
    ("pin_latch_high", 0x394DC, 0x394E8, 0xFB48, ((0xFF04, 0x7000, 2),)),
    ("ssc_rx", 0x3978E, 0x397AE, 0xFB50, ((0xF6E2, 5, 1),)),
)


def is_ram(address: int) -> bool:
    return (
        0xD800 <= address < 0xF800
        or 0xFA00 <= address < 0xFE00
    )


def compact(addresses: set[int]) -> list[dict]:
    ranges = []
    for address in sorted(addresses):
        if not ranges or address != ranges[-1]["end"] + 1:
            ranges.append({"start": address, "end": address})
        else:
            ranges[-1]["end"] = address
    return [
        {
            "range": f"0x{item['start']:04X}-0x{item['end']:04X}",
            "bytes": item["end"] - item["start"] + 1,
        }
        for item in ranges
    ]


class TracePlugin:
    def __init__(self) -> None:
        self.current = None
        self.accesses = defaultdict(lambda: {"reads": set(), "writes": set()})
        self.outcomes = {}
        self.unsupported = {}

    def pytest_runtest_logstart(self, nodeid, location) -> None:
        self.current = nodeid

    def pytest_runtest_logreport(self, report) -> None:
        if report.when == "call":
            self.outcomes[report.nodeid] = report.outcome

    def pytest_runtest_logfinish(self, nodeid, location) -> None:
        self.current = None

    def record(self, kind: str, address: int) -> None:
        if self.current and is_ram(address):
            self.accesses[self.current][kind].add(address)


def scenario_for(nodeid: str) -> str:
    if nodeid.startswith("direct::adc_t3"):
        return "adc_timer_isr"
    if nodeid.startswith("direct::pin_latch"):
        return "pin_latch_isr"
    if nodeid.startswith("direct::pec0_"):
        return "pec0_config_isr"
    if nodeid == "direct::ssc_rx":
        return "ssc_rx_isr"
    if "test_boot_ram_footprint.py" in nodeid:
        return "boot"
    if "test_ds2.py" in nodeid:
        return "ds2"
    if "test_flash.py" in nodeid:
        return "flash"
    return "feature_dispatch"


def booted_emulator(Emulator, reference):
    fixture = json.loads(
        (EMULATOR / "tests" / "boot" / "boot_ram_footprint_ms41_3.json").read_text(
            encoding="utf-8"
        )
    )
    emu = Emulator.load(reference, seg0_from_flash=True, flash_writable=True)
    emu.cpu.csp = 0
    result = emu.run_from(
        int(fixture["entry"], 16),
        stop_at=(int(fixture["stop"], 16),),
        max_steps=300000,
    )
    if result.exit_reason != "stop_at":
        raise RuntimeError("MS41.3 boot did not reach the trusted 0x0924 handoff")
    return emu


def run_direct_case(
    plugin, emu, name, entry_file, stop_file, context_pointer, preloads
) -> None:
    from ms41emu.errors import UnsupportedOpcode, UnsupportedOperand

    for address, value, width in preloads:
        (emu.write_byte if width == 1 else emu.write)(address, value)
    entry = entry_file ^ 0x4000
    stop = stop_file ^ 0x4000
    emu.reg.cp = context_pointer
    emu.reg.dpp[0] = 4
    nodeid = f"direct::{name}"
    plugin.current = nodeid
    try:
        emu.cpu.csp = entry >> 16
        try:
            result = emu.run_from(
                entry & 0xFFFF, stop_at=(stop & 0xFFFF,), max_steps=300000
            )
        except (UnsupportedOpcode, UnsupportedOperand) as error:
            plugin.outcomes[nodeid] = "unsupported"
            plugin.unsupported[nodeid] = str(error)
            return
    finally:
        plugin.current = None
    if result.exit_reason != "stop_at":
        raise RuntimeError(
            f"{name} stopped at 0x{result.final_pc:04X}: {result.exit_reason}"
        )
    plugin.outcomes[nodeid] = "passed"


def run_direct_cases(plugin, Emulator, reference) -> None:
    for case in DIRECT_CASES:
        run_direct_case(plugin, booted_emulator(Emulator, reference), *case)

    for bit in range(14):
        preloads = (
            (0xFDDC, 1 << bit, 2),
            (0xFF72, 0, 1),
            (0xFF74, 0, 1),
            (0xE64A, 0xFF, 1),
        )
        run_direct_case(
            plugin,
            booted_emulator(Emulator, reference),
            f"pec0_profile_bit_{bit}",
            0x3955A,
            0x39770,
            0xFB4C,
            preloads,
        )


def trace() -> dict:
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(EMULATOR))
    import pytest
    from ms41emu.emulator import Emulator
    from ms41emu.memory import Memory
    from tests.reffix import REF_BINS

    plugin = TracePlugin()
    original_read = Memory._window_read
    original_write = Memory._window_write

    def traced_read(memory, address):
        plugin.record("reads", address)
        return original_read(memory, address)

    def traced_write(memory, address, value):
        plugin.record("writes", address)
        return original_write(memory, address, value)

    Memory._window_read = traced_read
    Memory._window_write = traced_write
    previous_directory = Path.cwd()
    try:
        os.chdir(EMULATOR)
        with tempfile.TemporaryDirectory(prefix="ram-ownership-") as temp:
            result = pytest.main(
                [
                    "-q",
                    "-p",
                    "no:cacheprovider",
                    "--basetemp",
                    temp,
                    *TESTS,
                ],
                plugins=[plugin],
            )
        run_direct_cases(plugin, Emulator, REF_BINS[".3"])
    finally:
        os.chdir(previous_directory)
        Memory._window_read = original_read
        Memory._window_write = original_write
    if result != pytest.ExitCode.OK:
        raise RuntimeError(f"emulator scenario suite failed with pytest exit {result}")

    tests = []
    groups = defaultdict(lambda: {"reads": set(), "writes": set()})
    group_outcomes = defaultdict(lambda: defaultdict(int))
    for nodeid in sorted(plugin.outcomes):
        accesses = plugin.accesses[nodeid]
        scenario = scenario_for(nodeid)
        groups[scenario]["reads"].update(accesses["reads"])
        groups[scenario]["writes"].update(accesses["writes"])
        group_outcomes[scenario][plugin.outcomes[nodeid]] += 1
        tests.append(
            {
                "nodeid": nodeid,
                "outcome": plugin.outcomes[nodeid],
                "read_bytes": len(accesses["reads"]),
                "write_bytes": len(accesses["writes"]),
                "read_ranges": compact(accesses["reads"]),
                "write_ranges": compact(accesses["writes"]),
            }
        )

    scenarios = {}
    for name, accesses in sorted(groups.items()):
        touched = accesses["reads"] | accesses["writes"]
        scenarios[name] = {
            "complete_cases": group_outcomes[name]["passed"],
            "unsupported_cases": group_outcomes[name]["unsupported"],
            "read_bytes": len(accesses["reads"]),
            "write_bytes": len(accesses["writes"]),
            "touched_bytes": len(touched),
            "read_ranges": compact(accesses["reads"]),
            "write_ranges": compact(accesses["writes"]),
            "touched_ranges": compact(touched),
        }
    return {
        "scope": (
            "existing emulator tests plus sliced post-boot ISR bodies; pytest "
            "setup/assertion accesses are included, direct-case preloads are excluded, "
            "and unsupported slices contribute only the prefix executed before failure"
        ),
        "certification": "An untouched byte is not certified free.",
        "limitations": [
            "Interrupt scheduling is not modeled; body slices are invoked explicitly.",
            "SCXT prologues and POP/RETI epilogues are excluded and their CP/DPP context is seeded.",
            "The ADC slice starts at its common post-status path; the PEC eligibility guard is skipped and its pending profile bit is seeded.",
            "PEC payload transfers are hardware events and are not executed by these body slices.",
            "The emulator does not model CP-window register-bank aliasing.",
            "Unsupported opcodes and operands are recorded, never guessed or counted as complete.",
        ],
        "tests": list(TESTS),
        "direct_cases": [
            *(name for name, *_rest in DIRECT_CASES),
            *(f"pec0_profile_bit_{bit}" for bit in range(14)),
        ],
        "passed": sum(outcome == "passed" for outcome in plugin.outcomes.values()),
        "unsupported_direct_cases": plugin.unsupported,
        "scenarios": scenarios,
        "per_test": tests,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--out",
        type=Path,
        default=Path(__file__).parent / "evidence" / "runtime_ram_footprint.json",
    )
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    text = json.dumps(trace(), indent=2, sort_keys=True) + "\n"
    if args.check:
        if not args.out.exists() or args.out.read_text(encoding="utf-8") != text:
            print(f"stale evidence: {args.out}")
            return 1
        print("runtime evidence: current")
        return 0
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8")
    print(args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
