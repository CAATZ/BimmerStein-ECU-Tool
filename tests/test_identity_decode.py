import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import identity
from tests.conftest import SYNTHETIC_IDENTITIES, ref


def test_decode_ms41_1_serial_and_isn():
    info = identity.decode_identity(ref("MS41.1"))
    serial = SYNTHETIC_IDENTITIES["MS41.1"][0]
    assert info.serial == serial
    assert info.isn4 == serial[-4:]
    assert info.part == "1437806"
    assert info.layout_ok is True


def test_decode_ms41_3_serial_and_isn():
    info = identity.decode_identity(ref("MS41.3"))
    serial = SYNTHETIC_IDENTITIES["MS41.3"][0]
    assert info.serial == serial
    assert info.isn4 == serial[-4:]
    assert info.layout_ok is True


def test_decode_partial_refuses():
    info = identity.decode_identity(b"\x00" * 0x6000)  # 24 KB cal partial size
    assert info.layout_ok is False
    assert info.serial is None
