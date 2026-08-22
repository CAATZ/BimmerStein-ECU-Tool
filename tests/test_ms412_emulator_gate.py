import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _optional_directory(variable: str) -> Path | None:
    value = os.environ.get(variable, "").strip()
    if not value:
        return None
    path = Path(value).expanduser()
    return path if path.is_dir() else None


EMU_ROOT = _optional_directory("MS41EMU_ROOT")
TEST_DATA_ROOT = _optional_directory("MS41_TEST_DATA_ROOT")
if EMU_ROOT is not None:
    sys.path.insert(0, str(EMU_ROOT))
    from ms41emu.references import resolve_reference
    REF_410 = resolve_reference(".0", root=TEST_DATA_ROOT, required=False)
    REF_411 = resolve_reference(".1", root=TEST_DATA_ROOT, required=False)
    REF = resolve_reference(".2", root=TEST_DATA_ROOT, required=False)
    REF_413 = resolve_reference(".3", root=TEST_DATA_ROOT, required=False)
else:
    REF_410 = REF_411 = REF = REF_413 = None


def test_latest_ms412_patch_set_passes_canonical_emulator_gate():
    if EMU_ROOT is None or not (EMU_ROOT / "ms41emu").is_dir():
        pytest.skip("canonical ms41emu package unavailable; set MS41EMU_ROOT")
    if None in (REF_410, REF_411, REF, REF_413):
        pytest.skip(
            "MS41.0-MS41.3 references unavailable; set MS41_TEST_DATA_ROOT"
        )

    os.environ["MS41EMU_ROOT"] = str(EMU_ROOT)
    os.environ["MS41_TEST_DATA_ROOT"] = str(TEST_DATA_ROOT)
    sys.path.insert(0, str(ROOT))
    from engines.patcher import verify_ms412_emulator as gate

    assert gate.EMU_ROOT == EMU_ROOT
    assert gate.STOCK_410_PATH == REF_410
    assert gate.STOCK_411_PATH == REF_411
    assert gate.STOCK_PATH == REF
    assert gate.STOCK_413_PATH == REF_413
    assert None not in gate.ADMISSION_REGISTRY.values()
    assert set(gate.ADMISSION_REGISTRY.values()) <= set(gate.GROUPS)
    gate.main()


def test_admission_list_needs_no_private_inputs():
    env = os.environ.copy()
    env.pop("MS41EMU_ROOT", None)
    env.pop("MS41_TEST_DATA_ROOT", None)
    result = subprocess.run(
        [sys.executable, str(
            ROOT / "engines" / "patcher" / "verify_ms412_emulator.py"), "--list"],
        cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
        capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "cal-guard", "loader-doors", "intel-flash", "amd-flash",
        "st9030-proxy",
        "features-ms410", "features-ms411", "features-ms412", "features-ms413",
    ]
