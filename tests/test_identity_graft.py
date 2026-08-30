import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import pytest
import identity
from tests.conftest import SYNTHETIC_IDENTITIES, ref


DEPRECATED_AIF_PATCH_IDS = (
    "softbsl_loader_legacy",
    "softbsl_loader_relocated_v1",
    "softbsl_loader_v2",
    "softbsl_loader_v3_bench_failed",
    "softbsl_loader_v9",
    "softbsl_loader_v10",
    "cal_guard_v1",
    "cal_guard_v2",
    "cal_guard_v3_compatibility",
    "cal_guard_v4_bench_failed",
    "cal_guard_v4",
)


def test_graft_copies_only_the_two_admitted_ranges_without_private_images():
    target = bytes([0xA5]) * 0x6100
    source = bytes([0x5A]) * 0x6100

    out = identity.graft_identity(target, source)

    for start, end in identity.IDENTITY_GRAFT_RANGES:
        assert out[start:end] == source[start:end]
    assert out[:identity.PRODUCTION_OFF] == target[:identity.PRODUCTION_OFF]
    assert out[identity.PRODUCTION_END:identity.AIF_OFF] == target[
        identity.PRODUCTION_END:identity.AIF_OFF
    ]
    assert out[identity.AIF_END:] == target[identity.AIF_END:]


@pytest.mark.parametrize("patch_id", DEPRECATED_AIF_PATCH_IDS)
def test_graft_restores_every_exact_deprecated_aif_payload(patch_id):
    from engines.patcher import patch_ms41

    target = bytes([0xA5]) * 0x6100
    source = bytearray([0x5A]) * 0x6100
    patch = patch_ms41.load_patches()[patch_id]
    expected = {}

    for edit in patch["edits"]:
        offset = int(edit["off"])
        applied = bytes.fromhex(edit["data"])
        preimage = bytes.fromhex(edit["expect"])
        lo = max(offset, identity.AIF_OFF)
        hi = min(offset + len(applied), identity.AIF_END)
        if lo >= hi:
            continue
        rel_lo, rel_hi = lo - offset, hi - offset
        source[lo:hi] = applied[rel_lo:rel_hi]
        expected.update(zip(range(lo, hi), preimage[rel_lo:rel_hi]))

    frozen = bytes(source)
    out = identity.graft_identity(target, frozen)

    assert expected
    assert all(out[address] == value for address, value in expected.items())
    assert all(
        out[address] == frozen[address]
        for address in range(identity.AIF_OFF, identity.AIF_END)
        if address not in expected
    )
    assert out[identity.PRODUCTION_OFF:identity.PRODUCTION_END] == frozen[
        identity.PRODUCTION_OFF:identity.PRODUCTION_END
    ]


def test_graft_moves_production_identity_and_aif_history_only():
    source = ref("MS41.1")   # donor identity
    target = ref("MS41.3")   # a .3 base
    out = identity.graft_identity(target, source)

    src = identity.decode_identity(source)
    got = identity.decode_identity(bytes(out))
    source_serial, source_vin = SYNTHETIC_IDENTITIES["MS41.1"]
    assert got.serial == src.serial == source_serial
    assert got.isn4 == source_serial[-4:]
    assert identity.decode_vin(bytes(out)) == source_vin

    # the target's part number (firmware-common) is unchanged — it is a .3 base
    assert got.part == identity.decode_identity(target).part

    # The complete production and AIF ranges come from the live ECU. The gap
    # containing coding-family data and the firmware-owned ZIF remain the target's.
    for start, end in identity.IDENTITY_GRAFT_RANGES:
        assert out[start:end] == source[start:end]
    assert out[identity.PRODUCTION_END:identity.AIF_OFF] == target[
        identity.PRODUCTION_END:identity.AIF_OFF
    ]
    assert out[0x6001:0x6072] == target[0x6001:0x6072]

    # exactly the admitted graft ranges changed; nothing else
    tb = bytearray(target)
    for i in range(len(out)):
        in_graft = any(start <= i < end for start, end in identity.IDENTITY_GRAFT_RANGES)
        if not in_graft:
            assert out[i] == tb[i], f"unexpected change at 0x{i:05X}"


def test_graft_is_checksum_neutral():
    """Every byte the graft changes lies in the un-checksummed gap 0x5C14-0x6100
    (boot CRC ends 0x5C14, program CRC starts 0x6100), so no checksum is disturbed.
    Asserted structurally — not via verify_checksum, whose result on a given base
    ROM is independent of the graft."""
    source = ref("MS41.1")
    target = ref("MS41.3")
    out = identity.graft_identity(target, source)
    tb = bytearray(target)
    changed = [i for i in range(len(out)) if out[i] != tb[i]]
    assert changed, "graft changed nothing (donor == target?)"
    offenders = [hex(i) for i in changed if not (0x5C14 <= i < 0x6100)]
    assert not offenders, f"graft changed a checksummed byte: {offenders}"


def test_graft_rejects_undersized_input():
    import pytest
    with pytest.raises(ValueError):
        identity.graft_identity(b"\x00" * 0x6000, b"\x00" * 0x40000)
