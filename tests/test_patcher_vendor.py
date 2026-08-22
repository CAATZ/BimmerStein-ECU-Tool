import hashlib, os, re, sys
from pathlib import Path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from engines.patcher import patch_ms41
from tests.conftest import ref
import checksum
import pytest

EXPECTED_IDS = {
    "alphan_failsafe", "alphan_failsafe_v1", "alphan_failsafe_v2", "amd_flash", "cal_guard", "cal_guard_v1",
    "cal_guard_v2", "cal_guard_v4", "door_0x43",
    "door_0x43_ms410", "door_0x43_ms411", "door_magic", "door_magic_ms410",
    "door_magic_ms411", "ignition_cut", "ignition_cut_v2", "ignition_cut_v3", "ignition_cut_v4",
    "ignition_cut_v5", "ignition_cut_v6", "ignition_cut_v7", "launch_control", "launch_control_v2",
    "ignition_cut_v7_ms410", "ignition_cut_v7_ms411",
    "ignition_cut_v8", "ignition_cut_v8_ms410", "ignition_cut_v8_ms411",
    "ignition_cut_v8_ms412",
    "ignition_cut_v9", "ignition_cut_v9_ms410", "ignition_cut_v9_ms411",
    "ignition_cut_v9_ms412",
    "launch_control_v2_ms412", "launch_control_v3", "launch_control_v3_ms412",
    "launch_control_v4", "launch_control_v4_ms410", "launch_control_v4_ms411",
    "launch_control_v4_ms412", "launch_control_v5", "launch_control_v6",
    "launch_control_v6_ms410", "launch_control_v6_ms411",
    "launch_control_v6_ms412", "launch_control_v7",
    "launch_control_v7_ms410", "launch_control_v7_ms411",
    "launch_control_v7_ms412",
    "softbsl_loader", "softbsl_loader_legacy", "softbsl_loader_relocated_v1",
    "softbsl_loader_v2", "softbsl_loader_v10",
    "vanos_minrpm_ms410", "vanos_minrpm_v2_ms410", "vanos_minrpm_ms411",
}

_CUT_STATE_ASM = {
    "ignition_cut_v9_ms410_control.asm",
    "ignition_cut_v9_ms410_gate.asm",
    "ignition_cut_v9_ms410_guards.asm",
    "ignition_cut_v9_ms411_control.asm",
    "ignition_cut_v9_ms411_gate.asm",
    "ignition_cut_v9_ms411_guards.asm",
    "ignition_cut_v9_ms4123_control.asm",
    "ignition_cut_v9_ms4123_gate.asm",
    "ignition_cut_v9_ms412_guards.asm",
    "ignition_cut_v9_ms413_control.asm",
    "ignition_cut_v9_ms413_guards.asm",
    "launch_control_v7_ms410_cave_a.asm",
    "launch_control_v7_ms411_cave_a.asm",
    "launch_control_v7_ms412_cave_a.asm",
    "launch_control_v7_ms413_cave_a.asm",
}


def test_cut_runtime_state_uses_the_certified_family_safe_byte():
    patcher = Path(__file__).resolve().parents[1] / "engines" / "patcher"
    for name in _CUT_STATE_ASM:
        source = (patcher / name).read_text(encoding="utf-8")
        assert "0xE847" in source, name
        assert "0xE812" not in source, name
        for line in source.splitlines():
            code = line.split(";", 1)[0]
            if "0xE847" in code:
                assert code.split()[0].lower() in {"movb", "andb", "orb"}, (
                    name, line)


def test_patch_asm_has_no_odd_direct_word_operands():
    patcher = Path(__file__).resolve().parents[1] / "engines" / "patcher"
    word_op = re.compile(
        r"^\s*(?:mov|cmp|add|addc|sub|subc|and|or|xor)\s", re.IGNORECASE
    )
    direct_address = re.compile(r"(?<!#)0x([0-9a-f]{4})(?![0-9a-f])", re.IGNORECASE)
    offenders = []
    for path in patcher.glob("*.asm"):
        if path.name.startswith("ignition_cut_v8_"):
            continue  # deprecated exact source; V9 replaces its odd word accesses
        for line_number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), 1):
            code = line.split(";", 1)[0]
            if not word_op.match(code):
                continue
            offenders.extend(
                (path.name, line_number, match.group(0))
                for match in direct_address.finditer(code)
                if int(match.group(1), 16) & 1
            )
    assert offenders == []


def test_release_launch_sources_use_the_beta13_fd5a_latch():
    patcher = Path(__file__).resolve().parents[1] / "engines" / "patcher"
    for path in patcher.glob("launch_control_v7*.asm"):
        source = path.read_text(encoding="utf-8")
        assert "bset 0xFD5A.6" in source, path.name
        assert "bclr 0xFD5A.6" in source, path.name
        assert re.search(r"\bmovb\s+RL4,0xFD5A\b", source), path.name


def test_release_patch_descriptors_match_the_beta13_distribution():
    patches = Path(__file__).resolve().parents[1] / "engines" / "patcher" / "patches"
    expected = {
        "ignition_cut_v9.json": "358c38c71ee89c2796f639582169b8830c063adfe6b38ebf47e30d0fd52b99b6",
        "ignition_cut_v9_ms410.json": "c45a387d8a3985b969fe14594733d6057c7ead4c0b311c45d48ba677c9444d06",
        "ignition_cut_v9_ms411.json": "b8f8cf8b21796fac07e2a25bf33f64c757e6bde0b5ee202f0d4f2dc6cf3369f7",
        "ignition_cut_v9_ms412.json": "91ca6e9c0c5c83c6868da531535a9e9859e541795074c2a77c47e76e9c758f95",
        "launch_control_v7.json": "c3c0648f45573efbec0dcabb5e0d0cf3e09ded8622d8d0ab8a3cce154cf67db8",
        "launch_control_v7_ms410.json": "5808447f2080565dc891b707a4b1b4a7eafdd2e6c75641f9c400ad9331a8950d",
        "launch_control_v7_ms411.json": "42054970be0cd1c8fe502cb031604c47fadc4337b3d413266a12909639212702",
        "launch_control_v7_ms412.json": "187abd24f7ad729e3662d070b03d9e55953c56ffdc40264015c226d3ef11cebb",
    }
    assert {
        name: hashlib.sha256((patches / name).read_bytes()).hexdigest()
        for name in expected
    } == expected


def test_vendored_module_loads_all_patches():
    patches = patch_ms41.load_patches()
    assert set(patches) == EXPECTED_IDS
    assert patch_ms41.FULL == 262144


def test_every_edit_restores_the_full_written_range():
    # Removal must restore every byte a patch wrote. A shorter ``expect`` span
    # would leave an invisible cave tail behind after a deprecated revision is
    # removed from the Patches tab.
    for patch in patch_ms41.load_patches().values():
        for edit in patch["edits"]:
            assert len(bytes.fromhex(edit["expect"])) == len(
                bytes.fromhex(edit["data"])
            ), (patch["id"], hex(edit["off"]))
            if "upgrade_expect" in edit:
                upgrades = edit["upgrade_expect"]
                if isinstance(upgrades, str):
                    upgrades = [upgrades]
                assert all(
                    len(bytes.fromhex(value)) == len(bytes.fromhex(edit["data"]))
                    for value in upgrades
                ), (patch["id"], hex(edit["off"]), "upgrade_expect")


def test_exact_calguard_artifact_is_registered_and_prior_revision_is_upgradable():
    from engines.patcher.cal_guard_exact import (
        CAVE_FILE, _Assembler, assemble, assemble_stub)

    patches = patch_ms41.load_patches()
    guard = patches["cal_guard"]
    cave = next(edit for edit in guard["edits"] if edit["off"] == CAVE_FILE)
    root = Path(__file__).resolve().parents[1]

    assert guard["supersedes"] == ["cal_guard_v1", "cal_guard_v2", "cal_guard_v4"]
    assert bytes.fromhex(cave["data"]) == assemble()
    assert cave["data"] == (
        root / "engines" / "patcher" / "cal_guard_exact.hex"
    ).read_text().strip()
    edits = {edit["off"]: bytes.fromhex(edit["data"]) for edit in guard["edits"]}
    assert all(edits[offset] == data for offset, data in assemble_stub().items())
    guard_bytes = bytes.fromhex(cave["data"])
    assert bytes.fromhex("f2f407a0f2f509a0") not in guard_bytes
    for address in range(0xA007, 0xA00B):
        assert bytes((0xF3, 0xFA, address & 0xFF, address >> 8)) in guard_bytes
    with pytest.raises(ValueError, match="even address"):
        _Assembler().mov_mem(4, 0xA007)

    prior_v3, _ = patch_ms41.build(
        ref("MS41.1"), ["softbsl_loader_v10", "cal_guard_v4"],
        allow_deprecated=True)
    upgraded, upgrade_log = patch_ms41.build(
        bytes(prior_v3), ["softbsl_loader", "cal_guard"])
    assert bytes(upgraded[CAVE_FILE:CAVE_FILE + len(guard_bytes)]) == guard_bytes
    assert patch_ms41.is_applied(upgraded, patches["softbsl_loader"])
    assert not patch_ms41.is_applied(upgraded, patches["cal_guard_v4"])
    assert {line for line in upgrade_log if line.startswith("removed exact ")} == {
        "removed exact predecessor softbsl_loader_v10",
        "removed exact dependent cal_guard_v4",
    }

    base = bytearray(b"\xFF" * patch_ms41.FULL)
    base[0x6025:0x602C] = b"1429861"
    base[0x0120:0x0124] = bytes.fromhex("aabbccdd")
    upgrade = {
        "id": "revision_upgrade",
        "target": "MS41.0",
        "edits": [{
            "off": 0x0120,
            "expect": "ffffffff",
            "upgrade_expect": "aabbccdd",
            "data": "01020304",
        }],
    }

    image, log = patch_ms41.build(bytes(base), ["revision_upgrade"],
                                  patches={"revision_upgrade": upgrade})
    assert image[0x0120:0x0124] == bytes.fromhex("01020304")
    assert any("exact prior revision" in line for line in log)


def test_alphan_v3_registers_and_upgrades_only_exact_v1_v2_bytes():
    patches = patch_ms41.load_patches()
    current = patches["alphan_failsafe"]
    predecessors = [
        patches["alphan_failsafe_v1"],
        patches["alphan_failsafe_v2"],
    ]

    assert current["version"] == "V3"
    assert current["supersedes"] == [
        "alphan_failsafe_v1", "alphan_failsafe_v2"]
    assert current.get("deprecated") is not True
    assert current["status"] == "EMULATOR VERIFIED - ON-CAR TEST REQUIRED"
    assert all(patch["deprecated"] for patch in predecessors)
    assert all("BROKEN" in patch["status"] for patch in predecessors)
    assert patch_ms41.validate_splices(current) == []
    assert patch_ms41.validate_splices(predecessors[0])
    assert patch_ms41.validate_splices(predecessors[1]) == []

    current_edits = {edit["off"]: edit for edit in current["edits"]}
    for predecessor in predecessors:
        for prior_edit in predecessor["edits"]:
            upgrades = current_edits[prior_edit["off"]]["upgrade_expect"]
            assert prior_edit["data"] in upgrades

        legacy, _ = patch_ms41.build(
            ref("MS41.3"), [predecessor["id"]], allow_deprecated=True)
        upgraded, log = patch_ms41.build(legacy, ["alphan_failsafe"])
        assert patch_ms41.is_applied(upgraded, current)
        assert all(
            not patch_ms41.is_applied(upgraded, prior)
            for prior in predecessors
        )
        assert sum("exact prior revision" in line for line in log) == 2

        reverted = patch_ms41.revert(upgraded, current)
        for edit in current["edits"]:
            expected = bytes.fromhex(edit["expect"])
            assert reverted[edit["off"]:edit["off"] + len(expected)] == expected


@pytest.mark.parametrize(
    "variant,ignition_id,ignition_offsets,launch_id,launch_offsets",
    [
        ("MS41.0", "ignition_cut_v9_ms410",
         (0x36820, 0x36A00, 0x36D20),
         "launch_control_v7_ms410", (0x36B00,)),
        ("MS41.1", "ignition_cut_v9_ms411",
         (0x3B680, 0x3B8C0, 0x3BBE0),
         "launch_control_v7_ms411", (0x3B9C0,)),
        ("MS41.2", "ignition_cut_v9_ms412",
         (0x39C70, 0x39EA0, 0x3A1A0),
         "launch_control_v7_ms412", (0x39F80,)),
        ("MS41.3", "ignition_cut_v9",
         (0x39C70, 0x39EA0, 0x3A1A0),
         "launch_control_v7", (0x39F80,)),
    ],
)
def test_previous_local_v8_v6_revision_upgrades_in_place(
        variant, ignition_id, ignition_offsets, launch_id, launch_offsets):
    patches = patch_ms41.load_patches()
    image = bytearray(ref(variant))
    for patch_id, changed_offsets in (
        (ignition_id, ignition_offsets), (launch_id, launch_offsets),
    ):
        for edit in patches[patch_id]["edits"]:
            payload = (
                edit["upgrade_expect"][-1]
                if edit["off"] in changed_offsets
                else edit["data"]
            )
            raw = bytes.fromhex(payload)
            image[edit["off"]:edit["off"] + len(raw)] = raw

    upgraded, log = patch_ms41.build(
        bytes(image), [ignition_id, launch_id], patches=patches)
    assert patch_ms41.is_applied(upgraded, patches[ignition_id])
    assert patch_ms41.is_applied(upgraded, patches[launch_id])
    assert sum("exact prior revision" in line for line in log) == 4


def test_check_base_accepts_ms41_3_and_rejects_blank():
    assert patch_ms41.check_base(ref("MS41.3"), "MS41.3") is None
    assert patch_ms41.check_base(bytes(patch_ms41.FULL), "MS41.3") is not None


def test_ms412_base_gate_is_distinct_from_ms413_and_unknown_targets_fail_closed():
    assert patch_ms41.check_base(ref("MS41.2"), "MS41.2") is None
    assert patch_ms41.check_base(ref("MS41.3"), "MS41.2") is not None
    assert patch_ms41.check_base(ref("MS41.2"), "MS41.3") is not None
    assert "unsupported" in patch_ms41.check_base(ref("MS41.2"), "MS41.9")


def test_older_softbsl_base_gates_are_exact_and_distinct():
    for variant in ("MS41.0", "MS41.1"):
        assert patch_ms41.check_base(ref(variant), variant) is None
    assert patch_ms41.check_base(ref("MS41.0"), "MS41.1") is not None
    assert patch_ms41.check_base(ref("MS41.1"), "MS41.0") is not None


def test_ms413_base_gate_uses_program_signature_and_either_cal_marker():
    base = ref("MS41.3")
    no_credit = bytearray(base); no_credit[0x11F60:0x11F68] = b"\xFF" * 8
    no_ss1 = bytearray(base); no_ss1[0x173BB:0x173C0] = b"\xFF" * 5
    no_cal_markers = bytearray(no_ss1); no_cal_markers[0x11F60:0x11F68] = b"\xFF" * 8
    no_program = bytearray(base); no_program[0x39A9A:0x39A9E] = b"\xFF" * 4

    assert patch_ms41.check_base(bytes(no_credit), "MS41.3") is None
    assert patch_ms41.check_base(bytes(no_ss1), "MS41.3") is None
    assert "calibration marker" in patch_ms41.check_base(bytes(no_cal_markers), "MS41.3")
    assert "program signature" in patch_ms41.check_base(bytes(no_program), "MS41.3")


def test_calguard_compose_requires_its_strict_runtime_marker():
    credit_only = bytearray(ref("MS41.3"))
    credit_only[0x173BB:0x173C0] = b"\xFF" * 5

    assert patch_ms41.check_base(bytes(credit_only), "MS41.3") is None
    with pytest.raises(patch_ms41.PatchError, match="strict SS1v2"):
        patch_ms41.build(bytes(credit_only), ["cal_guard"])
    image, _log = patch_ms41.build(bytes(credit_only), ["alphan_failsafe"])
    assert patch_ms41.is_applied(
        image, patch_ms41.load_patches()["alphan_failsafe"])


def test_marker_only_build_recomputes_a_golden_top_image():
    out, log = patch_ms41.build(ref("MS41.3"), [], marker="T")
    assert out[0x5FFC:0x6000] == bytes([0xA5, 0x5A, 0x54, 0xAB])
    assert any("set bank marker" in line for line in log)


def test_needs_boot_write_flags_only_the_sa1_patches():
    patches = patch_ms41.load_patches()
    boot = {pid for pid, p in patches.items() if patch_ms41.needs_boot_write(p)}
    # These edit file 0x4000-0x5FFF (SA1/boot); DS2 and un-armed soft-BSL can't write there.
    assert boot == {
        "cal_guard", "cal_guard_v1", "cal_guard_v2", "cal_guard_v4",
        "softbsl_loader",
        "softbsl_loader_legacy", "softbsl_loader_relocated_v1",
        "softbsl_loader_v2", "softbsl_loader_v10", "amd_flash",
    }
    # Program/cal patches are DS2-writable.
    assert patch_ms41.needs_boot_write(patches["ignition_cut_v9"]) is False
    assert patch_ms41.needs_boot_write(patches["launch_control_v7"]) is False


def test_relocated_loader_preserves_optional_descriptor_and_composes_with_guard_and_amd():
    base = ref("MS41.2")
    original_aif = base[0x5D07:0x5F8B]
    out, _log = patch_ms41.build(
        base, ["amd_flash", "softbsl_loader", "cal_guard", "door_magic"])

    assert out[0x5D07:0x5F8B] == original_aif
    assert out[0x55A2:0x55A4] == bytes.fromhex("8c1f")
    assert out[0x4412:0x4416] == bytes.fromhex("4fd87eb7")
    assert out[0x5C32:0x5C36] == bytes.fromhex("f075e6f5")
    assert out[0x5CA0:0x5CA4] == bytes.fromhex("4ed8f7f8")
    assert out[0x5F8C:0x5F90] == bytes.fromhex("f3f853e6")
    status = checksum.checksum_status(out)
    assert status["boot"] and status["program"] and status["cal"]


@pytest.mark.parametrize("variant", ["MS41.0", "MS41.1", "MS41.2", "MS41.3"])
def test_softbsl_v11_exactly_relocates_v10_and_anchors_the_full_hook(variant):
    patches = patch_ms41.load_patches()
    current = patches["softbsl_loader"]
    predecessor = patches["softbsl_loader_v10"]
    predecessor_guard = patches["cal_guard_v4"]

    assert current["version"] == "V11"
    assert current["supersedes"][-1] == "softbsl_loader_v10"
    assert predecessor["version"] == "V10"
    assert predecessor["deprecated"] is True
    current_hook = next(edit for edit in current["edits"] if edit["off"] == 0x55A0)
    prior_hook = next(edit for edit in predecessor["edits"] if edit["off"] == 0x55A0)
    assert current_hook == {"off": 0x55A0, "expect": "da00440a", "data": "da008c1f"}
    assert prior_hook["data"] == "da00921d"

    v2_image, _ = patch_ms41.build(
        ref(variant), ["softbsl_loader_v10", "cal_guard_v4"],
        allow_deprecated=True)
    assert patch_ms41.is_applied(v2_image, predecessor)
    assert patch_ms41.is_applied(v2_image, predecessor_guard)

    upgraded, log = patch_ms41.build(v2_image, ["softbsl_loader"])
    assert patch_ms41.is_applied(upgraded, current)
    assert not patch_ms41.is_applied(upgraded, predecessor)
    assert not patch_ms41.is_applied(upgraded, predecessor_guard)
    assert "removed exact predecessor softbsl_loader_v10" in log
    assert "removed exact dependent cal_guard_v4" in log

    partial_guard = bytearray(v2_image)
    partial_guard[0x5E20] ^= 0x01
    with pytest.raises(patch_ms41.PatchError, match="PARTIAL predecessor dependency"):
        patch_ms41.build(partial_guard, ["softbsl_loader"])
    status = checksum.checksum_status(upgraded)
    assert status["boot"] and status["program"] and status["cal"]


def test_softbsl_v11_rejects_a_corrupted_boot_hook_prefix():
    base = bytearray(ref("MS41.3"))
    base[0x55A0:0x55A4] = bytes.fromhex("cc00440a")

    with pytest.raises(
            patch_ms41.PatchError, match=r"softbsl_loader @0x055A0"):
        patch_ms41.build(bytes(base), ["softbsl_loader"])


def test_relocated_loader_and_latest_patch_descriptors_use_built_hex_artifacts():
    root = Path(__file__).resolve().parents[1]
    patches = patch_ms41.load_patches()
    alphan = patches["alphan_failsafe"]
    alphan_cave = next(
        edit["data"] for edit in alphan["edits"]
        if edit["off"] == alphan["cave"]["base"]
    )
    alphan_artifact = (
        root / "engines" / "patcher" / "alphan_failsafe_v3.hex"
    ).read_text().strip().lower()
    assert alphan_cave.startswith(alphan_artifact)
    assert set(bytes.fromhex(alphan_cave[len(alphan_artifact):])) <= {0xFF}

    loader_edits = {edit["off"]: edit["data"].lower()
                    for edit in patches["softbsl_loader"]["edits"]}

    for offset, filename in {
        0x4412: "loader_sa1_relocated_io.hex",
        0x5C32: "loader_sa1_relocated_crc.hex",
        0x5CA0: "loader_sa1_relocated_tx.hex",
        0x5F8C: "loader_sa1_relocated_main.hex",
    }.items():
        artifact = (root / "engines" / "softbsl" / filename).read_text().strip().lower()
        assert loader_edits[offset] == artifact

    ignition = patches["ignition_cut_v9"]
    for patch_id, offset, artifact_name in (
        ("ignition_cut_v9_ms410", 0x36820, "ignition_cut_v9_ms410_gate.hex"),
        ("ignition_cut_v9_ms411", 0x3B680, "ignition_cut_v9_ms411_gate.hex"),
        ("ignition_cut_v9_ms412", 0x39C70, "ignition_cut_v9_ms4123_gate.hex"),
        ("ignition_cut_v9", 0x39C70, "ignition_cut_v9_ms4123_gate.hex"),
    ):
        gate = next(
            edit["data"] for edit in patches[patch_id]["edits"]
            if edit["off"] == offset
        )
        artifact = (
            root / "engines" / "patcher" / artifact_name
        ).read_text().strip().lower()
        assert gate.startswith(artifact)
        assert set(bytes.fromhex(gate[len(artifact):])) <= {0xFF}
    control = next(edit["data"] for edit in ignition["edits"] if edit["off"] == 0x39EA0)
    assert control == (
        root / "engines" / "patcher" / "ignition_cut_v9_ms413_control.hex"
    ).read_text().strip().lower()
    for patch_id, offset, artifact_name in (
        ("ignition_cut_v9_ms410", 0x36A00, "ignition_cut_v9_ms410_control.hex"),
        ("ignition_cut_v9_ms411", 0x3B8C0, "ignition_cut_v9_ms411_control.hex"),
        ("ignition_cut_v9_ms412", 0x39EA0, "ignition_cut_v9_ms4123_control.hex"),
    ):
        payload = next(
            edit["data"] for edit in patches[patch_id]["edits"]
            if edit["off"] == offset
        )
        assert payload == (
            root / "engines" / "patcher" / artifact_name
        ).read_text().strip().lower()

    launch = patches["launch_control_v7_ms412"]
    cave_a = next(edit["data"] for edit in launch["edits"] if edit["off"] == 0x39F80)
    assert cave_a == (
        root / "engines" / "patcher" / "launch_control_v7_ms412_cave_a.hex"
    ).read_text().strip().lower()
    cave_b = next(edit["data"] for edit in launch["edits"] if edit["off"] == 0x3A100)
    assert cave_b == (
        root / "engines" / "patcher" / "launch_control_v4_ms412_cave_b.hex"
    ).read_text().strip().lower()
    comparator = next(edit["data"] for edit in launch["edits"] if edit["off"] == 0x3A140)
    assert comparator == (
        root / "engines" / "patcher"
        / "launch_control_v4_hard_compare.hex"
    ).read_text().strip().lower()

    launch_413 = patches["launch_control_v7"]
    cave_a_413 = next(
        edit["data"] for edit in launch_413["edits"] if edit["off"] == 0x39F80)
    assert cave_a_413 == (
        root / "engines" / "patcher" / "launch_control_v7_ms413_cave_a.hex"
    ).read_text().strip().lower()
    for patch_id, offset, artifact_name in (
        ("launch_control_v7_ms410", 0x36B00, "launch_control_v7_ms410_cave_a.hex"),
        ("launch_control_v7_ms411", 0x3B9C0, "launch_control_v7_ms411_cave_a.hex"),
    ):
        payload = next(
            edit["data"] for edit in patches[patch_id]["edits"]
            if edit["off"] == offset
        )
        assert payload == (
            root / "engines" / "patcher" / artifact_name
        ).read_text().strip().lower()
    cave_b_413 = next(
        edit["data"] for edit in launch_413["edits"] if edit["off"] == 0x3A100)
    assert cave_b_413 == (
        root / "engines" / "patcher" / "launch_control_v5_ms413_cave_b.hex"
    ).read_text().strip().lower()
    comparator_413 = next(
        edit["data"] for edit in launch_413["edits"] if edit["off"] == 0x3A140)
    assert comparator_413 == (
        root / "engines" / "patcher"
        / "launch_control_v5_ms413_hard_compare.hex"
    ).read_text().strip().lower()

    launch_411 = patches["launch_control_v7_ms411"]
    for offset, expected_sha256 in (
        (0x3BB40, "95827ad805a95ce1b26e807f9055780e3dcab1a9fd5943968786a6b1ea710ff5"),
        (0x3BB80, "53be6c9dab10c05016eb482bc4c8277d72a2ed16a51c3bacf71d49f2c402a827"),
    ):
        payload = next(
            edit["data"] for edit in launch_411["edits"]
            if edit["off"] == offset
        )
        assert hashlib.sha256(bytes.fromhex(payload)).hexdigest() == expected_sha256

    for patch_id, artifact_name, cave_offset in (
        ("ignition_cut_v9_ms410", "ignition_cut_v9_ms410_guards.hex", 0x36D20),
        ("ignition_cut_v9_ms411", "ignition_cut_v9_ms411_guards.hex", 0x3BBE0),
        ("ignition_cut_v9_ms412", "ignition_cut_v9_ms412_guards.hex", 0x3A1A0),
        ("ignition_cut_v9", "ignition_cut_v9_ms413_guards.hex", 0x3A1A0),
    ):
        cave = next(
            edit["data"].lower() for edit in patches[patch_id]["edits"]
            if edit["off"] == cave_offset
        )
        artifact = (
            root / "engines" / "patcher" / artifact_name
        ).read_text().strip().lower()
        assert cave.startswith(artifact)
        assert set(bytes.fromhex(cave[len(artifact):])) <= {0xFF}


def test_no_current_patch_owns_any_ms412_aif_byte():
    aif = (0x5D07, 0x5F8B)
    offenders = []
    for patch_id, patch in patch_ms41.load_patches().items():
        if patch.get("deprecated"):
            continue
        for start, end in patch_ms41._ranges(patch):
            if patch_ms41._overlap((start, end), aif):
                offenders.append((patch_id, start, end))
    assert offenders == []


@pytest.mark.parametrize(
    "variant,patch_id,heater_sites",
    [
        ("MS41.1", "ignition_cut_v9_ms411",
         (0x259E4, 0x26052, 0x267BE, 0x26AC0)),
        ("MS41.2", "ignition_cut_v9_ms412",
         (0x25AC2, 0x26130, 0x2689C, 0x26B9E)),
        ("MS41.3", "ignition_cut_v9",
         (0x25AC2, 0x26130, 0x2689C, 0x26B9E)),
    ],
)
def test_ignition_cut_guards_leave_o2_heater_diagnostics_untouched(
        variant, patch_id, heater_sites):
    stock = ref(variant)
    patched, _log = patch_ms41.build(stock, [patch_id])
    for site in heater_sites:
        assert patched[site:site + 4] == stock[site:site + 4]


def test_latest_switch_caves_follow_the_stock_sir_selector_order():
    patches = patch_ms41.load_patches()
    active_caves = {
        "ignition_cut_v9": bytes.fromhex(next(
            edit["data"]
            for edit in patches["ignition_cut_v9"]["edits"]
            if edit["off"] == 0x39EA0
        )),
        "launch_control_v7": bytes.fromhex(next(
            edit["data"]
            for edit in patches["launch_control_v7"]["edits"]
            if edit["off"] == 0x39F80
        )),
        "launch_control_v7_ms412": bytes.fromhex(next(
            edit["data"]
            for edit in patches["launch_control_v7_ms412"]["edits"]
            if edit["off"] == 0x39F80
        )),
    }
    launch_reads = (
        bytes.fromhex("f3f861fd6982"),      # selector 01: fd60.9 / pin 80
        bytes.fromhex("f3f861fd6981"),      # selector 02: fd60.8 / pin 81
        bytes.fromhex("f3f860fd67f88000"),  # selector 04: fd60.7 / pin 82
    )

    for patch_id, cave in active_caves.items():
        sir_reads = (
            (
                bytes.fromhex("f3fa61fd69a2"),
                bytes.fromhex("f3fa61fd69a1"),
                bytes.fromhex("f3fa60fd67fa8000"),
            )
            if patch_id == "ignition_cut_v9"
            else launch_reads
        )
        offsets = [cave.find(read) for read in sir_reads]
        assert offsets == sorted(offsets), (patch_id, offsets)
        assert all(offset >= 0 for offset in offsets), (patch_id, offsets)
        assert all(cave.count(read) == 1 for read in sir_reads), patch_id

    ms413 = active_caves["launch_control_v7"]
    ms412 = active_caves["launch_control_v7_ms412"]
    assert len(ms413) == len(ms412)
    assert ms413 != ms412


def test_ms413_launch_uses_erased_tail_and_preserves_live_boost_table():
    patches = patch_ms41.load_patches()
    launch = patches["launch_control_v7"]
    expected_cals = {
        "LC_SW": 0x107E0,
        "LC_CUTTYPE": 0x107E1,
        "LC_CLUTCHPOL": 0x107E2,
        "LC_MAXRPM": 0x107E3,
        "LC_ARMSPEED": 0x107E4,
        "LC_MAXSPEED": 0x107E5,
        "LC_MINTPS": 0x107E6,
        "LC_HARDRPM": 0x107E7,
        "LC_HYST": 0x107E8,
        "LC_IPW": 0x107E9,
    }
    assert launch["cave"]["cals"] == expected_cals

    stock = ref("MS41.3")
    assert stock[0x107E0:0x107EB] == b"\xFF" * 11
    boost_table = stock[0x1752C:0x1756C]
    patched, _log = patch_ms41.build(
        stock, ["ignition_cut_v9", "launch_control_v7"])
    assert patched[0x107E0:0x107EB] == b"\xFF" * 11
    assert patched[0x1752C:0x1756C] == boost_table


@pytest.mark.parametrize(
    "variant,patch_id,hyst_offset,ipw_offset",
    [
        ("MS41.0", "launch_control_v7_ms410", 0x17028, 0x17029),
        ("MS41.1", "launch_control_v7_ms411", 0x17718, 0x17719),
        ("MS41.2", "launch_control_v7_ms412", 0x17534, 0x17535),
        ("MS41.3", "launch_control_v7", 0x107E8, 0x107E9),
    ],
)
def test_launch_ignition_calibrations_use_erased_variant_specific_bytes(
        variant, patch_id, hyst_offset, ipw_offset):
    cals = patch_ms41.load_patches()[patch_id]["cave"]["cals"]
    assert cals["LC_HYST"] == hyst_offset
    assert cals["LC_IPW"] == ipw_offset
    assert ref(variant)[hyst_offset:ipw_offset + 2] == b"\xFF" * 3


def test_ms413_launch_relocation_changes_only_address_bearing_instructions():
    patches = patch_ms41.load_patches()
    current = bytes.fromhex(next(
        edit["data"]
        for edit in patches["launch_control_v5"]["edits"]
        if edit["off"] == 0x39D00
    ))
    legacy = bytes.fromhex(next(
        edit["data"]
        for edit in patches["launch_control_v4"]["edits"]
        if edit["off"] == 0x39D00
    ))
    expected = legacy
    for old, new in {
        "f3f82c35": "f3f8e047",
        "f3f82d35": "f3f8e147",
        "f3fa2e35": "f3fae247",
        "43f82f35": "43f8e347",
        "43f83035": "43f8e447",
        "43f83135": "43f8e547",
        "43f83235": "43f8e647",
    }.items():
        expected = expected.replace(bytes.fromhex(old), bytes.fromhex(new))

    assert current == expected
    assert bytes.fromhex("2d35") in current  # original JMPR opcode/displacement


def test_ignition_v7_is_anchored_only_at_the_six_channel_p1l_final_stage():
    patches = patch_ms41.load_patches()
    patch = patches["ignition_cut_v7"]
    cave = bytes.fromhex(next(
        edit["data"] for edit in patch["edits"] if edit["off"] == 0x39C70
    ))

    assert [edit["off"] for edit in patch["edits"]] == [
        0x3992A, 0x3998E, 0x39C70,
    ]
    assert patch["edits"][0]["expect"] == "65f204ff"   # andb P1L,RL1
    assert patch["edits"][1]["expect"] == "65f204ff"
    assert patch["edits"][0]["data"] == "da0370dc"     # calls 03:DC70
    assert patch["edits"][1]["data"] == "da0370dc"
    assert cave.count(bytes.fromhex("65f204ff")) == 1    # stock-only replay
    assert bytes.fromhex("efe2") not in cave             # no deprecated P3.14 write

    for variant in ("MS41.2", "MS41.3"):
        stock = ref(variant)
        assert stock[0x3992A:0x3992E] == bytes.fromhex("65f204ff")
        assert stock[0x3998E:0x39992] == bytes.fromhex("65f204ff")
        assert stock[0x6DB2:0x6DB8] == bytes.fromhex("7ebd7bb76f9f")
        assert stock[0x6DC4:0x6DCA] == bytes.fromhex("76ad5bb66d9b")

        # CC6 is configured as interrupt-only compare mode 0; the separate CC7
        # ISR releases the selected final stage by ORing its mask into P1L.
        assert stock[0x1016:0x101A] == bytes.fromhex("1aaa0c0f")
        assert stock[0x105C:0x1060] == bytes.fromhex("1aaac0f0")
        assert stock[0x399C2:0x399C6] == bytes.fromhex("73825efa")

        # Native startup leaves P1L high/off. The independent protection ISR
        # then watches exactly P1L.0..5 and forces each bit high when its own
        # FA62..FA67 down-counter expires: six physical ignition channels.
        assert stock[0x2C8A8:0x2C8AC] == bytes.fromhex("e682ff00")
        for bit in range(6):
            block = 0x38BCA + bit * 14
            assert stock[block:block + 4] == bytes(
                (0x8A, 0x82, 0x05, bit << 4)
            )
            assert stock[block + 4:block + 8] == bytes(
                (0x05, 0x8F, 0x62 + bit, 0xFA)
            )
            assert stock[block + 10:block + 12] == bytes(
                ((bit << 4) | 0x0F, 0x82)
            )


def test_latest_ms412_program_patches_recompute_enabled_program_checksum():
    out, _log = patch_ms41.build(
        ref("MS41.2"), ["ignition_cut_v9_ms412", "launch_control_v7_ms412"])
    status = checksum.checksum_status(out)
    assert status == {
        "boot": True, "program": True, "cal": True,
        "prog_disabled": False, "cal_disabled": False,
    }
    assert patch_ms41.validate_splices(
        patch_ms41.load_patches()["launch_control_v7_ms412"]) == []
    # The MS41.2 enforcement cave preserves both native fd30.4 continuations.
    cave = bytes.fromhex(
        next(edit["data"] for edit in
             patch_ms41.load_patches()["launch_control_v7_ms412"]["edits"]
             if edit["off"] == 0x3A100))
    assert bytes.fromhex("9a180240fa02d607fa02e807") in cave


def test_latest_ms413_program_patches_recompute_all_checksums():
    out, _log = patch_ms41.build(
        ref("MS41.3clean"),
        ["alphan_failsafe", "ignition_cut_v9", "launch_control_v7"],
    )
    status = checksum.checksum_status(out)
    assert status["boot"] and status["program"] and status["cal"]
    assert status["prog_disabled"]


@pytest.mark.parametrize(
    "variant,patch_ids",
    [
        (
            "MS41.0",
            [
                "ignition_cut_v9_ms410",
                "launch_control_v7_ms410",
                "vanos_minrpm_v2_ms410",
            ],
        ),
        (
            "MS41.1",
            [
                "ignition_cut_v9_ms411",
                "launch_control_v7_ms411",
                "vanos_minrpm_ms411",
            ],
        ),
    ],
)
def test_latest_older_firmware_feature_ports_compose_and_recompute_program(
        variant, patch_ids):
    out, _log = patch_ms41.build(ref(variant), patch_ids)
    status = checksum.checksum_status(out)
    baseline = checksum.checksum_status(ref(variant))
    assert status["boot"] and status["program"] and status["cal"]
    assert status["prog_disabled"] == baseline["prog_disabled"]
    for patch_id in patch_ids:
        assert patch_ms41.is_applied(out, patch_ms41.load_patches()[patch_id])


@pytest.mark.parametrize(
    "variant,old_ids,new_ids",
    [
        (
            "MS41.0",
            ["ignition_cut_v8_ms410", "launch_control_v6_ms410"],
            ["ignition_cut_v9_ms410", "launch_control_v7_ms410"],
        ),
        (
            "MS41.1",
            ["ignition_cut_v8_ms411", "launch_control_v6_ms411"],
            ["ignition_cut_v9_ms411", "launch_control_v7_ms411"],
        ),
        (
            "MS41.2",
            ["ignition_cut_v8_ms412", "launch_control_v6_ms412"],
            ["ignition_cut_v9_ms412", "launch_control_v7_ms412"],
        ),
        (
            "MS41.3",
            ["ignition_cut_v8", "launch_control_v6"],
            ["ignition_cut_v9", "launch_control_v7"],
        ),
    ],
)
def test_v9_v7_exact_upgrade_replaces_v8_v6(
        variant, old_ids, new_ids):
    patches = patch_ms41.load_patches()
    old_image, _ = patch_ms41.build(
        ref(variant), old_ids, allow_deprecated=True)
    upgraded, _ = patch_ms41.build(old_image, new_ids)

    assert all(patch_ms41.is_applied(upgraded, patches[patch_id])
               for patch_id in new_ids)
    assert all(not patch_ms41.is_applied(upgraded, patches[patch_id])
               for patch_id in old_ids)
    status = checksum.checksum_status(upgraded)
    assert status["boot"] and status["program"] and status["cal"]
