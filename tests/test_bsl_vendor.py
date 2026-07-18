import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import engines.bsl.bsl_unbrick as bu
from types import SimpleNamespace


def test_bsl_package_imports_cleanly():
    assert bu.MS41ECU is not None
    assert bu.cks is not None


def test_bsl_dtr_reset_drives_the_direct_tap_reset_line():
    bsl = object.__new__(bu.BSL)
    bsl.reset_line = "dtr"
    bsl.reset_invert = False
    bsl.ser = SimpleNamespace(dtr=None)

    bsl._drive_reset(True)
    assert bsl.ser.dtr is True
    bsl._drive_reset(False)
    assert bsl.ser.dtr is False


def test_bsl_uses_canonical_app_rom_and_checksum_modules():
    assert bu.MS41ECU.__module__ == "ms41"
    assert bu.cks.__name__ == "checksum"


def test_flash_profile_regions_present():
    assert set(bu.FLASH_REGIONS) == {"boot", "program-low", "program-mid", "tune", "program-high"}
    assert set(bu.FLASH_REGIONS_AMD) == {"low", "tune", "program-high"}
    amd, regions, label = bu._flash_profile("29f400", half="upper")
    assert amd is True
    assert "tune" in regions and "program-high" in regions
    assert "29F400" in label or "29f400" in label.lower()


def test_whole_chip_flash_order_keeps_boot_last():
    assert bu._ordered_regions(bu.FLASH_REGIONS) == [
        "tune", "program-high", "program-mid", "program-low", "boot"]
    assert bu._ordered_regions(bu.FLASH_REGIONS_AMD) == ["tune", "program-high", "low"]


def test_exact_read_synthesizes_only_known_ghost_window():
    class Fake:
        def mon_read(self, addr, size, progress=None):
            if progress:
                progress(size, size)
            return bytes([addr // 0x10000 + 1]) * size

    data = bu._read_mapped_span(Fake(), 0x8000, 0x20000, hole=bu.BSL_HOLE)
    assert data[:0x4000] == b"\x01" * 0x4000
    assert data[0x4000:0x8000] == b"\xFF" * 0x4000
    assert data[0x8000:] == b"\x02" * 0x10000


def test_exact_read_fails_closed_on_unexpected_short_read():
    class Fake:
        def mon_read(self, _addr, size, progress=None):
            return b"\xFF" * (size - 1)

    try:
        bu._read_mapped_span(Fake(), 0x10000, 0x16000)
    except bu.BSLError as error:
        assert "short flash read" in str(error)
    else:
        raise AssertionError("short read was accepted")


def test_dump_outputs_standard_full_and_tune_file_formats(tmp_path, monkeypatch):
    block = 0x4000

    class Fake:
        def mon_read(self, addr, size):
            # Make the direct low address plausibly flash so cmd_dump does not use the alias.
            return bytes([0x31 if addr == 0 else 0x42]) * size

        def mon_dump(self, start, end, alias_low=False, fill_hole=True, progress=None):
            assert alias_low is False
            if (start, end) == (0x10000, 0x16000):
                assert fill_hole is False
                return bytes((i & 0xFF) for i in range(end - start))
            assert (start, end) == (0, 0x40000)
            assert fill_hole is True
            chunks = []
            for index in range(16):
                value = 0xFF if index == 3 else index
                chunks.append(bytes([value]) * block)
            return b"".join(chunks)

    monkeypatch.setattr(bu, "_monitor", lambda _args: Fake())

    full_path = tmp_path / "full.bin"
    full_args = SimpleNamespace(
        file=str(full_path), partial=False, range=None, no_alias=False,
        raw_hole=False, cpu_order=False, file_order=True, baud=38400,
        progress_cb=None)
    assert bu.cmd_dump(full_args) == 0
    full = full_path.read_bytes()
    assert len(full) == 0x40000
    # CPU blocks are pair-swapped into standard file order. The CPU ghost block 3
    # therefore becomes file block 2 and remains synthesized as FF.
    assert [full[i * block] for i in range(6)] == [1, 0, 0xFF, 2, 5, 4]

    tune_path = tmp_path / "tune.bin"
    tune_args = SimpleNamespace(
        file=str(tune_path), partial=True, range=None, no_alias=False,
        raw_hole=False, cpu_order=False, file_order=True, baud=38400,
        progress_cb=None)
    assert bu.cmd_dump(tune_args) == 0
    tune = tune_path.read_bytes()
    assert len(tune) == bu.CAL_PARTIAL_SIZE
    assert tune == bytes((i & 0xFF) for i in range(bu.CAL_PARTIAL_SIZE))


def test_short_preerase_backup_aborts_before_erase(tmp_path):
    class Fake:
        def __init__(self):
            self.erases = 0

        def mon_read(self, _addr, size, progress=None):
            return b"\xFF" * (size - 1)

        def mon_erase(self, _addr):
            self.erases += 1
            return 0x80

    args = SimpleNamespace(
        no_backup=False, backup_dir=str(tmp_path), chip="28f200", half="upper",
        progress_cb=None, baud=9600, ref="ref.bin")
    plan = bu._region_plan(args, "tune", b"\xFF" * 0x40000, bu.FLASH_REGIONS)
    fake = Fake()
    assert bu._flash_region(fake, args, "tune", plan, amd=False) == 1
    assert fake.erases == 0


def test_variant_guard_refuses_incomplete_live_evidence():
    class Fake:
        def mon_read(self, _addr, _size, progress=None):
            return b"\xFF"

    args = SimpleNamespace(force=False)
    assert bu._variant_guard(Fake(), args, b"\xFF" * 24576, ["tune"]) == 1
