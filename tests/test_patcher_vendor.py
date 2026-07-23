import os, sys
from pathlib import Path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from engines.patcher import patch_ms41
from tests.conftest import ref
import checksum
import pytest

EXPECTED_IDS = {
    "alphan_failsafe", "amd_flash", "cal_guard", "door_0x43",
    "door_0x43_ms410", "door_0x43_ms411", "door_magic", "door_magic_ms410",
    "door_magic_ms411", "ignition_cut", "ignition_cut_v2", "ignition_cut_v3", "ignition_cut_v4",
    "ignition_cut_v5", "ignition_cut_v6", "ignition_cut_v7", "launch_control", "launch_control_v2",
    "ignition_cut_v7_ms410", "ignition_cut_v7_ms411",
    "launch_control_v2_ms412", "launch_control_v3", "launch_control_v3_ms412",
    "launch_control_v4", "launch_control_v4_ms410", "launch_control_v4_ms411",
    "launch_control_v4_ms412",
    "softbsl_loader", "softbsl_loader_legacy", "softbsl_loader_relocated_v1",
    "vanos_minrpm_ms410", "vanos_minrpm_ms411",
}


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


def test_marker_only_build_recomputes_a_golden_top_image():
    out, log = patch_ms41.build(ref("MS41.3"), [], marker="T")
    assert out[0x5FFC:0x6000] == bytes([0xA5, 0x5A, 0x54, 0xAB])
    assert any("set bank marker" in line for line in log)


def test_needs_boot_write_flags_only_the_sa1_patches():
    patches = patch_ms41.load_patches()
    boot = {pid for pid, p in patches.items() if patch_ms41.needs_boot_write(p)}
    # These edit file 0x4000-0x5FFF (SA1/boot); DS2 and un-armed soft-BSL can't write there.
    assert boot == {
        "cal_guard", "softbsl_loader", "softbsl_loader_legacy",
        "softbsl_loader_relocated_v1", "amd_flash",
    }
    # Program/cal patches are DS2-writable.
    assert patch_ms41.needs_boot_write(patches["ignition_cut_v7"]) is False
    assert patch_ms41.needs_boot_write(patches["launch_control_v4"]) is False


def test_relocated_loader_preserves_optional_descriptor_and_composes_with_guard_and_amd():
    base = ref("MS41.2")
    original_descriptor = base[0x5D36:0x5D91]
    out, _log = patch_ms41.build(
        base, ["amd_flash", "softbsl_loader", "cal_guard", "door_magic"])

    assert out[0x5D36:0x5D91] == original_descriptor
    assert out[0x55A2:0x55A4] == bytes.fromhex("921d")
    assert out[0x5C32:0x5C36] == bytes.fromhex("f075e6f5")
    assert out[0x5D92:0x5D96] == bytes.fromhex("f3f853e6")
    assert out[0x5FC4:0x5FC8] == bytes.fromhex("4fd87eb7")
    status = checksum.checksum_status(out)
    assert status["boot"] and status["program"] and status["cal"]


def test_relocated_loader_and_latest_patch_descriptors_use_built_hex_artifacts():
    root = Path(__file__).resolve().parents[1]
    patches = patch_ms41.load_patches()
    loader_edits = {edit["off"]: edit["data"].lower()
                    for edit in patches["softbsl_loader"]["edits"]}

    for offset, filename in {
        0x5C32: "loader_sa1_relocated_crc.hex",
        0x5D92: "loader_sa1_relocated_main.hex",
        0x5FC4: "loader_sa1_relocated_io.hex",
    }.items():
        artifact = (root / "engines" / "softbsl" / filename).read_text().strip().lower()
        assert loader_edits[offset] == artifact

    ignition = patches["ignition_cut_v7"]
    ignition_cave = next(edit["data"] for edit in ignition["edits"] if edit["off"] == 0x39C70)
    assert ignition_cave == (
        root / "engines" / "patcher" / "ignition_cut_v7_cave.hex"
    ).read_text().strip().lower()

    launch = patches["launch_control_v4_ms412"]
    cave_b = next(edit["data"] for edit in launch["edits"] if edit["off"] == 0x39DBC)
    assert cave_b == (
        root / "engines" / "patcher" / "launch_control_v4_ms412_cave_b.hex"
    ).read_text().strip().lower()
    comparator = next(edit["data"] for edit in launch["edits"] if edit["off"] == 0x39E20)
    assert comparator == (
        root / "engines" / "patcher" / "launch_control_v4_hard_compare.hex"
    ).read_text().strip().lower()

    launch_413 = patches["launch_control_v4"]
    cave_b_413 = next(
        edit["data"] for edit in launch_413["edits"] if edit["off"] == 0x39DBC)
    assert cave_b_413 == (
        root / "engines" / "patcher" / "launch_control_v4_ms413_cave_b.hex"
    ).read_text().strip().lower()


def test_latest_switch_caves_follow_the_stock_sir_selector_order():
    patches = patch_ms41.load_patches()
    active_caves = {
        "ignition_cut_v7": bytes.fromhex(next(
            edit["data"]
            for edit in patches["ignition_cut_v7"]["edits"]
            if edit["off"] == 0x39C70
        )),
        "launch_control_v4": bytes.fromhex(next(
            edit["data"]
            for edit in patches["launch_control_v4"]["edits"]
            if edit["off"] == 0x39D00
        )),
        "launch_control_v4_ms412": bytes.fromhex(next(
            edit["data"]
            for edit in patches["launch_control_v4_ms412"]["edits"]
            if edit["off"] == 0x39D00
        )),
    }
    sir_reads = (
        bytes.fromhex("f3f861fd67f80200"),  # selector 01: fd60.9 / pin 80
        bytes.fromhex("f3f861fd67f80100"),  # selector 02: fd60.8 / pin 81
        bytes.fromhex("f3f860fd67f88000"),  # selector 04: fd60.7 / pin 82
    )

    for patch_id, cave in active_caves.items():
        offsets = [cave.find(read) for read in sir_reads]
        assert offsets == sorted(offsets), (patch_id, offsets)
        assert all(offset >= 0 for offset in offsets), (patch_id, offsets)
        assert all(cave.count(read) == 1 for read in sir_reads), patch_id

    assert active_caves["launch_control_v4"] == active_caves["launch_control_v4_ms412"]


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
        ref("MS41.2"), ["ignition_cut_v7", "launch_control_v4_ms412"])
    status = checksum.checksum_status(out)
    assert status == {
        "boot": True, "program": True, "cal": True,
        "prog_disabled": False, "cal_disabled": False,
    }
    assert patch_ms41.validate_splices(
        patch_ms41.load_patches()["launch_control_v4_ms412"]) == []
    # The MS41.2 enforcement cave preserves both native fd30.4 continuations.
    cave = bytes.fromhex(
        next(edit["data"] for edit in
             patch_ms41.load_patches()["launch_control_v4_ms412"]["edits"]
             if edit["off"] == 0x39DBC))
    assert bytes.fromhex("9a180240fa02d607fa02e807") in cave


def test_latest_ms413_program_patches_recompute_all_checksums():
    out, _log = patch_ms41.build(
        ref("MS41.3clean"), ["ignition_cut_v7", "launch_control_v4"])
    status = checksum.checksum_status(out)
    assert status["boot"] and status["program"] and status["cal"]
    assert status["prog_disabled"]


@pytest.mark.parametrize(
    "variant,patch_ids",
    [
        (
            "MS41.0",
            [
                "ignition_cut_v7_ms410",
                "launch_control_v4_ms410",
                "vanos_minrpm_ms410",
            ],
        ),
        (
            "MS41.1",
            [
                "ignition_cut_v7_ms411",
                "launch_control_v4_ms411",
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
