import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import identity
from tests.conftest import SYNTHETIC_IDENTITIES, ref


def test_graft_moves_serial_and_vin_only():
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

    # exactly the serial field and the VIN field changed; nothing else
    tb = bytearray(target)
    for i in range(len(out)):
        in_serial = identity.SERIAL_OFF <= i < identity.SERIAL_NUL_OFF + 1
        in_vin = identity.VIN_OFF <= i < identity.VIN_OFF + identity.VIN_LEN
        if not (in_serial or in_vin):
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
