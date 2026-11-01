"""MS42 raw storage restore followed by the original native journal scanner.

Host transfers use the strict injected wire peer. The resulting flash bytes
then enter exact0110C6 instructions in the canonical emulator, with supplied
application context. This is not an autonomous startup or physical ECU test.
"""

import pytest

import ms42_ms43_storage as storage
from tests.test_ms42_ms43 import Journal
from tests.test_ms42_ms43_agent_workflow import agent_workflow as agent_workflow
from tests.test_ms42_ms43_config_native import native as native


@pytest.mark.parametrize("agent_workflow", ["MS42"], indirect=True)
@pytest.mark.parametrize("bad_record_crc", [False, True])
def test_retained_restore_preserves_native_record_selection_and_corruption(
        agent_workflow, native, bad_record_crc):
    ecu, peer = agent_workflow
    target = bytearray(peer.original)
    if bad_record_crc:
        target[0x47FE] ^= 1  # Corrupt the latest record; never repair it implicitly.

    def scan(image):
        emu = native("MS42")
        emu.mem.image[:] = image
        result = emu.call(0x7684E, max_steps=200000)
        assert result.exit_reason == "stop_at"
        assert bytes(emu.mem.image) == image
        return tuple(emu.read(address) for address in (0xFD18, 0xF92C, 0xF92E))

    expected = scan(target)
    peer.image[0x4000:0x8000] = bytes(0x4000)
    snapshot = storage.make_snapshot("MS42", "retained", target[0x4000:0x8000], software_id="0110C6")
    result = storage.restore_storage(ecu, snapshot, journal=Journal())
    assert result["verified"] and bytes(peer.image) == bytes(target)
    assert scan(peer.image) == expected
    if not bad_record_crc:
        assert expected == (0x1000, 0x400, 1)  # Original selected record at4400.
