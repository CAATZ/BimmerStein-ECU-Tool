"""Read-only identity contracts from complete native owners in BMW DATEN.

Each sparse application runs with explicitly named original C6/069 boot data.
These are analysis combinations, not claims of recovered original full images.
"""

from types import SimpleNamespace

import pytest

import ms42_ms43_diagnostics as diagnostics
from ms42_ms43_identity import PAIRING_POINTERS
from ms42_ms43_offline import decode_identity
from tests.test_ms42_ms43_config_daten import packages as packages, native as native
from tests.test_ms42_ms43_config_revisions import private_sources as private_sources
from tests.test_ms42_ms43_diagnostics import identity_ecu
from tests.test_ms42_ms43_offline import identity_image
from tests.test_ms42_live_revisions_native import native_ecu as ms42_ecu
from tests.test_ms43_revision_live_native import native_ecu as ms43_ecu, OWNERS
from tests.test_ms43_x001_live_native import read


RESTORES = {"0110C7": (0x2951A, 0xF906, 8), "0110CA": (0x2951A, 0xF906, 8),
            "430037": (0xB2A50, 0xE2B0, 28), "430064": (0xB30FE, 0xE2D0, 28),
            "430065": (0xB0F34, 0xE2D0, 28), "430066": (0xAEC98, 0xE2D0, 28)}


@pytest.fixture(params=(*RESTORES, "430066-later"))
def revision(request, packages, private_sources, native):
    key = request.param
    software = key.split("-")[0]
    family = "MS42" if software.startswith("01") else "MS43"
    base = private_sources["c6" if family == "MS42" else "stock"]
    if software == "0110C7":
        memory = dict(private_sources["c7_memory"])
        memory.update(private_sources["c7_cal_memory"])
    else:
        memory = packages[key].memory
    image = bytearray(b"\xff" * 0x80000)
    image[:0x10000] = base[:0x10000]
    for address, byte in memory.items():
        image[address & 0x7FFFF] = byte
    emu = native[family](bytes(image), experimental=True)

    def trace(pc, opcode):
        assert all(at in memory or at & 0x7FFFF < 0x10000 for at in range(pc, pc + 4)), hex(pc)
    emu.cpu.set_trace(trace)
    return SimpleNamespace(emu=emu, image=bytes(image), base=base, memory=memory, software=software,
                           family=family, restore=RESTORES[software], owners=OWNERS.get(software))


def test_actual_application_contains_the_identity_owner_and_reference_descriptors(revision):
    entry, _, _ = revision.restore
    size = 0x140 if revision.family == "MS42" else 0x2A0
    assert all(at in revision.memory for at in range(entry, entry + size))
    image = revision.image
    if revision.family == "MS42":
        assert image[0x2951A:0x2965A] == revision.base[0x2951A:0x2965A]
        descriptors = (0x502D6, 0x502FA)
    else:
        descriptors = (0xEED46, 0xEED5E)
    for address in descriptors:
        assert all(at in revision.memory for at in range(address, address + 4))
        at = address & 0x7FFFF
        assert image[at:at + 4] == revision.base[at:at + 4]


def records(family):
    start = PAIRING_POINTERS[family][1]
    for slot in range(1 if family == "MS42" else 3):
        original = identity_image(family, slot=slot)[start:start + 0x42]
        for corrupt in (None, 0, 1, 2):
            data = bytearray(original)
            if corrupt is not None:
                data[slot * 6 + corrupt * 2] ^= 0x12
            yield data
    for fill in (0, 255):
        yield bytes((fill,)) * 0x42
    if family == "MS43":
        for values in ((0xFEFE, 0xECEC, 0xECEC), (0xFFFF, 0xFEFE, 0xECEC)):
            data = bytearray(identity_image(family, slot=1)[start:start + 0x42])
            data[0x12:0x18] = b"".join(v.to_bytes(2, "little") for v in values)
            yield data


def test_each_original_isn_restore_matches_offline_redundancy_and_virgin_decoding(revision):
    emu = revision.emu
    entry, address, count = revision.restore
    start = PAIRING_POINTERS[revision.family][1]
    for data in records(revision.family):
        emu.mem.image[start:start + 0x42] = data
        original = bytes(emu.mem.image)
        expected = decode_identity(original)
        assert emu.call(entry, max_steps=500).exit_reason == "stop_at"
        raw = (read(emu, address, count) if revision.family == "MS43" else
               bytes(emu.read_byte(address + i) for i in range(count)))
        actual = diagnostics.decode_isn(raw, revision.family)
        assert actual == {key: expected[key] for key in ("isn", "isn_status")}
        assert bytes(emu.mem.image) == original


def test_identity_display_reads_that_revisions_actual_native_isn_mirror(revision):
    emu = revision.emu
    entry, address, _ = revision.restore
    start = PAIRING_POINTERS[revision.family][1]
    reference = identity_image(revision.family)
    emu.mem.image[start:start + 0x42] = reference[start:start + 0x42]
    assert emu.call(entry, max_steps=500).exit_reason == "stop_at"
    original = bytes(emu.mem.image)
    ecu, _ = identity_ecu(revision.family)
    canned = ecu.transport.responder
    measured = (ms42_ecu(emu) if revision.family == "MS42" else ms43_ecu(revision)).transport.responder
    ecu.transport.responder = lambda command, args: (canned(command, args) if command in (0, 0x0D)
                                                     else measured(command, args))
    info = diagnostics.read_identity(ecu)
    assert info["software_id"] == revision.software
    expected = decode_identity(original)
    assert info["isn"] == expected["isn"] and info["isn_status"] == expected["isn_status"]
    assert info["flash_driver"] == "AMD-compatible word-program driver"
    assert info["program_calibration_match"] in ("References agree", "Reference mismatch")
    addresses = [int.from_bytes(args[:4], "big") for _, command, args in ecu.transport.calls if command == 6]
    assert address in addresses
    assert bytes(emu.mem.image) == original


def test_unknown_revision_keeps_isn_unavailable():
    image = identity_image("MS43")
    image[0x6FFBA:0x6FFC0] = b"430056"
    assert decode_identity(image)["isn"] is None
    assert ("MS43", "430056") not in diagnostics._ISN_LAYOUTS


def test_native_pairing_restore_reports_the_revision_specific_unpaired_status(revision):
    from ms42_ms43_vin import PAIRING_STATUS
    from ms42_ms43_vin_write import PAIRING_LAYOUT

    emu = revision.emu
    entry, _, _ = revision.restore
    start, count, _ = PAIRING_LAYOUT[revision.family]
    before = bytes(emu.mem.image)
    emu.mem.image[start:start + count] = b"\xff" * count
    target = bytes(emu.mem.image)
    assert emu.call(entry, max_steps=500).exit_reason == "stop_at"
    address = PAIRING_STATUS[revision.family][revision.software]
    raw = (read(emu, address, 2) if revision.family == "MS43" else
           bytes(emu.read_byte(address + i) for i in range(2)))
    assert raw == b"\xff\xff"
    assert bytes(emu.mem.image) == target
    assert target[:start] == before[:start] and target[start + count:] == before[start + count:]
