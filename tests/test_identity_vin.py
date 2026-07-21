import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import identity
from ms41 import MS41ECU
from tests.conftest import SYNTHETIC_IDENTITIES, ref


def test_vin_round_trip():
    vin = SYNTHETIC_IDENTITIES["MS41.1"][1]
    packed = identity.encode_vin(vin)
    assert len(packed) == 13
    # decode expects it at VIN_OFF in a full image; splice into a blank ROM
    img = bytearray(identity.FULL_ROM_SIZE)
    img[identity.VIN_OFF:identity.VIN_OFF + 13] = packed
    assert identity.decode_vin(bytes(img)) == vin


def test_decode_vin_from_ref():
    assert identity.decode_vin(ref("MS41.1")) == SYNTHETIC_IDENTITIES["MS41.1"][1]


def test_ms41_vin_decoder_uses_the_identity_contract():
    vin = SYNTHETIC_IDENTITIES["MS41.1"][1]
    full = bytearray(identity.FULL_ROM_SIZE)
    full[identity.VIN_OFF:identity.VIN_OFF + identity.VIN_LEN] = identity.encode_vin(vin)
    assert MS41ECU.vin_from_image(full) == vin


def test_set_vin_writes_only_vin_bytes():
    base = bytearray(ref("MS41.3"))
    vin = SYNTHETIC_IDENTITIES["MS41.1"][1]
    out = identity.set_vin(bytes(base), vin)
    assert identity.decode_vin(bytes(out)) == vin
    # every byte outside the 13-byte VIN field is unchanged
    assert out[:identity.VIN_OFF] == base[:identity.VIN_OFF]
    assert out[identity.VIN_OFF + 13:] == base[identity.VIN_OFF + 13:]


def test_boot_window_vin_edit_changes_only_the_packed_field():
    full = ref("MS41.1")
    boot = full[identity.BOOT_DATA_OFF:identity.BOOT_DATA_OFF + identity.BOOT_DATA_SIZE]
    vin = SYNTHETIC_IDENTITIES["MS41.3"][1]
    out = identity.set_boot_vin(boot, vin)
    assert identity.decode_boot_identity(out).vin == vin
    rel = identity.VIN_OFF - identity.BOOT_DATA_OFF
    assert out[:rel] == boot[:rel]
    assert out[rel + identity.VIN_LEN:] == boot[rel + identity.VIN_LEN:]


def test_boot_strings_include_offsets_without_assigning_meanings():
    full = ref("MS41.1")
    boot = full[identity.BOOT_DATA_OFF:identity.BOOT_DATA_OFF + identity.BOOT_DATA_SIZE]
    strings = identity.boot_strings(boot)
    serial = SYNTHETIC_IDENTITIES["MS41.1"][0]
    assert any("1585" in text and serial in text for _offset, text in strings)
    assert all(identity.BOOT_DATA_OFF <= offset < identity.BOOT_DATA_OFF + identity.BOOT_DATA_SIZE
               for offset, _text in strings)
