"""Exact SS1v2 CEL admission and real installed-byte negative controls."""
import os
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def installed_cel_pair():
    if not all(Path(os.environ.get(name, "__missing__")).is_dir()
               for name in ("MS41EMU_ROOT", "MS41_TEST_DATA_ROOT")):
        pytest.skip("canonical emulator and exact private reference images required")
    from engines.patcher import verify_ms412_emulator as gate

    image, _ = gate.patch_ms41.build(
        gate._cut_reference("MS41.3"), ["ignition_cut_v19", "launch_control_v19"], allow_deprecated=True)
    return gate, gate._bind_image(image, "SS1v2")


def test_installed_pair_preserves_cel_and_qualifies_knock_observations(installed_cel_pair):
    from engines.patcher.verify_cel_interaction import verify_cel_interaction

    gate, image = installed_cel_pair
    result = verify_cel_interaction(gate, image)
    assert all(result[name] > 0 for name in (
        "static_regions", "alarm", "knock", "timing", "recovery", "fuel", "shared_state"))


def test_cel_helper_change_invalidates_admission_fingerprint(installed_cel_pair, monkeypatch):
    from engines.patcher import verify_cel_interaction

    gate, _image = installed_cel_pair
    helper = Path(verify_cel_interaction.__file__).resolve()
    fingerprint = gate._admission_fingerprint()
    read_bytes = Path.read_bytes

    def changed_helper(path):
        data = read_bytes(path)
        return data + b"\n# fingerprint test change\n" if path.resolve() == helper else data

    with monkeypatch.context() as patch:
        patch.setattr(Path, "read_bytes", changed_helper)
        assert gate._admission_fingerprint() != fingerprint
    assert gate._admission_fingerprint() == fingerprint


@pytest.mark.parametrize("cpu,expected,replacement,failure", (
    pytest.param(0x29B86, "7e11", "6e11", "CEL protected region",
                 id="cel-clears-wrong-shared-flag"),
    pytest.param(0x3F286, "4fe2", "1fe2", "CEL window shared-state",
                 id="knock-window-writes-cel-pin"),
    pytest.param(0x2A7B8, "fa03eef1", "f0465c14", "CEL cut knock history",
                 id="native-knock-bypasses-guard"),
))
def test_installed_byte_defects_fail_cel_admission(
        installed_cel_pair, cpu, expected, replacement, failure):
    from engines.patcher.verify_cel_interaction import verify_cel_interaction

    gate, image = installed_cel_pair
    changed = bytearray(image)
    offset = cpu ^ 0x4000
    original, defect = bytes.fromhex(expected), bytes.fromhex(replacement)
    assert len(original) == len(defect)
    assert changed[offset:offset + len(original)] == original
    changed[offset:offset + len(defect)] = defect
    # Valid CRCs isolate the installed-code defect from checksum rejection.
    changed, _ = gate.checksum.correct_checksums(changed)
    mutant = gate._bind_image(changed, "SS1v2")
    assert gate.checksum.verify_checksum(bytearray(mutant))[0]
    assert mutant[offset:offset + len(defect)] == defect
    with pytest.raises(AssertionError, match=failure):
        verify_cel_interaction(gate, mutant)
