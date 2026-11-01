"""Run bounded development checks, or an explicitly requested full qualification."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
ADMISSION_TEST = (
    "tests/test_ms412_emulator_gate.py::"
    "test_latest_ms412_patch_set_passes_canonical_emulator_gate"
)
DEV_TESTS = tuple("tests/" + name + ".py" for name in (
    "test_ds2_transport", "test_ds2_program_readback", "test_ds2_native_fast_service",
    "test_ds2_legacy_recovery_wire", "test_ds2_legacy_recovery", "test_ds2_fast_slim_write",
    "test_ds2_fast_safety", "test_ds2_fast_read", "test_ds2_fast_plans",
    "test_ds2_fast_partial_write", "test_ds2_fast_full_write", "test_ds2_fast_contracts",
    "test_ds2_ews", "test_ds2_compatibility_baud", "test_checksum_policy",
    "test_ms42_ms43", "test_ms42_ms43_image", "test_ms42_ms43_agent",
    "test_ms42_ms43_agent_finalize", "test_ms42_ms43_agent_recovery",
    "test_ms42_ms43_agent_workflow", "test_ms42_ms43_baud_recovery",
    "test_ms42_ms43_ram_write", "test_ms42_ms43_ram_write_rejections",
    "test_softbsl_recovery", "test_gui_operation_ownership", "test_packaging_config",
    "test_test_environment",
)) + (
    "tests/test_gui_smoke.py::test_native_write_passes_verify_choice_without_acquiring_backup",
    "tests/test_gui_smoke.py::test_native_write_handoff_requires_complete_original_identity",
    "tests/test_gui_smoke.py::test_softbsl_installer_post_erase_failure_keeps_port_and_recovery",
)


def admission_status(root: Path = ROOT) -> str:
    """Check a completed private result without executing any firmware."""
    evidence = root / "_private/validation/ms41-admission"
    try:
        proof = json.loads((evidence / "proof.json").read_text(encoding="utf-8"))
        if proof.get("result") != "PASS" or proof.get("source_drift") != []:
            raise ValueError("previous qualification did not complete cleanly")
        junit = (evidence / proof["junit"]).resolve()
        if not junit.is_relative_to(evidence.resolve()):
            raise ValueError("qualification result is outside its evidence folder")
        payload = junit.read_bytes()
        if hashlib.sha256(payload).hexdigest() != proof["junit_sha256"]:
            raise ValueError("qualification result hash differs")
        cases = [case for case in ET.fromstring(payload).iter("testcase")
                 if case.get("name") == ADMISSION_TEST.split("::")[1]
                 and case.get("classname") == "tests.test_ms412_emulator_gate"]
        if len(cases) != 1 or any(cases[0].find(tag) is not None
                                  for tag in ("failure", "error", "skipped")):
            raise ValueError("native admission has no completed passing result")
        for field, relative in (
            ("gate_source_sha256", "engines/patcher/verify_ms412_emulator.py"),
            ("test_source_sha256", "tests/test_ms412_emulator_gate.py"),
        ):
            if hashlib.sha256((root / relative).read_bytes()).hexdigest() != proof[field]:
                raise ValueError("qualification source changed")
        sys.path.insert(0, str(root))
        from engines.patcher import verify_ms412_emulator as gate
        if set(proof["groups"]) != set(gate.GROUPS):
            raise ValueError("qualification does not cover all current groups")
        if proof["admission_fingerprint"] != gate._admission_fingerprint():
            raise ValueError("firmware qualification inputs changed")
        return "MS41 qualification: reusing a complete PASS for these exact inputs."
    except (OSError, ValueError, KeyError, ImportError, ET.ParseError, SystemExit) as exc:
        return f"MS41 qualification needed ({exc}); not run by development checks."


def pytest_arguments(full: bool, extra: list[str], skip_admission: bool) -> list[str]:
    args = ["-q", "-p", "no:cacheprovider"]
    if not full:
        args.extend(dict.fromkeys((*DEV_TESTS, *extra)))
    if skip_admission:
        args.extend(("--deselect", ADMISSION_TEST))
    return args


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--full", action="store_true", help="run the entire offline suite")
    parser.add_argument("--skip-admission", action="store_true",
                        help="public packaging only: native admission already ran successfully")
    parser.add_argument("tests", nargs="*", help="additional changed-area tests for the dev check")
    args = parser.parse_args(argv)
    if args.skip_admission and not args.full:
        parser.error("--skip-admission requires --full")
    work = ROOT / ".tmp/offline-checks"
    work.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    started = time.monotonic()
    summary = {"scope": "full" if args.full else "development", "result": "FAIL"}
    if not args.full:
        summary["ms41_qualification"] = admission_status()
        print(summary["ms41_qualification"], flush=True)
    try:
        subprocess.run([sys.executable, "-m", "ruff", "check", ".", "--select", "F,E9"],
                       cwd=ROOT, env=env, check=True)
        command = [sys.executable, "-m", "pytest", *pytest_arguments(
            args.full, args.tests, args.skip_admission),
            "--basetemp", str(work / "pytest"), "--junitxml", str(work / "results.xml")]
        summary["pytest_arguments"] = command[3:]
        subprocess.run(command, cwd=ROOT, env=env, check=True)
        summary["result"] = "PASS"
        return 0
    except subprocess.CalledProcessError as exc:
        return exc.returncode
    finally:
        summary["seconds"] = round(time.monotonic() - started, 3)
        (work / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
