import os
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _optional_directory(variable: str) -> Path | None:
    value = os.environ.get(variable, "").strip()
    if not value:
        return None
    path = Path(value).expanduser()
    return path if path.is_dir() else None


def _find_reference(root: Path | None, variant: str) -> Path | None:
    if root is None:
        return None
    marker = f"ref_{variant.lower()}"
    candidates = []
    for path in root.rglob("*.bin"):
        if not path.is_file() or path.stat().st_size != 0x40000:
            continue
        parts = (root.name.lower(),) + tuple(
            part.lower() for part in path.relative_to(root).parts
        )
        name = path.name.lower()
        if marker not in parts or "full" not in name:
            continue
        if variant == "MS41.3" and ("stock" not in name or "cksum" in name):
            continue
        candidates.append(path)
    return sorted(candidates)[0] if candidates else None


EMU_ROOT = _optional_directory("MS41EMU_ROOT")
TEST_DATA_ROOT = _optional_directory("MS41_TEST_DATA_ROOT")
REF = _find_reference(TEST_DATA_ROOT, "MS41.2")
REF_413 = _find_reference(TEST_DATA_ROOT, "MS41.3")


def test_latest_ms412_patch_set_passes_canonical_emulator_gate():
    if EMU_ROOT is None or not (EMU_ROOT / "ms41emu").is_dir():
        pytest.skip("canonical ms41emu package unavailable; set MS41EMU_ROOT")
    if REF is None or REF_413 is None:
        pytest.skip(
            "MS41.2/MS41.3 references unavailable; set MS41_TEST_DATA_ROOT"
        )

    os.environ["MS41EMU_ROOT"] = str(EMU_ROOT)
    os.environ["MS41_TEST_DATA_ROOT"] = str(TEST_DATA_ROOT)
    sys.path.insert(0, str(ROOT))
    from engines.patcher import verify_ms412_emulator as gate

    assert gate.EMU_ROOT == EMU_ROOT
    assert gate.STOCK_PATH == REF
    assert gate.STOCK_413_PATH == REF_413
    gate.main()
