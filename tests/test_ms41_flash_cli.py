from types import SimpleNamespace

import pytest

import ms41_flash
from checksum import checksum_status, verify_checksum
from tests.conftest import ref


def test_cli_corrects_all_ms413_checksums(
        tmp_path, capsys):
    image = bytearray(ref("MS41.3clean"))
    image[0x14020] ^= 0x01
    program_checksum = bytes(image[0x6050:0x6052])
    source = tmp_path / "ms413.bin"
    output = tmp_path / "ms413_fixed.bin"
    source.write_bytes(image)

    args = SimpleNamespace(
        check_file=None,
        fix_file=str(source),
        output=str(output),
        verbose=False,
    )
    with pytest.raises(SystemExit) as stopped:
        ms41_flash.run_offline(args)

    assert stopped.value.code == 0
    corrected = output.read_bytes()
    assert corrected[0x6050:0x6052] != program_checksum
    assert checksum_status(corrected)["program"] is True
    assert verify_checksum(corrected)[0] is True
    console = capsys.readouterr().out
    assert "Program corrected" in console


@pytest.mark.parametrize("filename", ["rom.bin", "rom.BIN", "rom"])
def test_cli_default_checksum_output_preserves_source_and_directory(
        tmp_path, monkeypatch, filename):
    directory = tmp_path / "original.bin.images"
    directory.mkdir()
    source = directory / filename
    original = bytes(256 * 1024)
    corrected = bytes([1]) * len(original)
    source.write_bytes(original)
    monkeypatch.setattr(
        "checksum.correct_checksums", lambda _data: (corrected, []))
    args = SimpleNamespace(
        check_file=None, fix_file=str(source), output=None, verbose=False,
    )

    with pytest.raises(SystemExit) as stopped:
        ms41_flash.run_offline(args)

    assert stopped.value.code == 0
    assert source.read_bytes() == original
    assert (directory / "rom_cksum.bin").read_bytes() == corrected
