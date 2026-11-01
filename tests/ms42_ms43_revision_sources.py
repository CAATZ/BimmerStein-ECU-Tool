"""Private exact sources and explicitly composed boot/application test inputs.

DATEN has no original boot image. A composed input proves only the named known
boot plus those actual application/calibration bytes, never a factory full ROM.
"""
from functools import lru_cache
from hashlib import sha256
import importlib.util
import os
import pytest
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
SHARED = Path(os.environ.get("MS41_TEST_DATA_ROOT", ROOT.parent / "_shared"))
DATEN = ROOT / "tests/reference_data/packages"
FULL = {
    "0110C6": ("REF_MS42/Siemens_MS42_0110C6_E46_M52TUB28_EU3_LHD.bin",
               "010d6bf17bcfe46a9f436ba8e11dff375466ee948aaaf10d43a1d8325757b2cc"),
    "430069": ("REF_MS43/Siemens_MS43_MS430069_E46_M54B30_US.bin",
               "eb687fdd8eee4a0671dbe1d3294b4c25788b6cfa60c29f15d3b77293981909da"),
    "430055": ("REF_MS43/430055.bin",
               "820bce7aea24933673fce0b6dc4b24b4f672b4e6dbd43476dcecc5826feec1f8"),
}
PROGRAMS = {
    "0110C7": ("7505859A.0PA", "d6a727e169360426d78b253e5d86f4bbc629667ee0f470f9e530d9c72a0d6b0e"),
    "0110CA": ("7526753A.0PA", "266c74a1b1b3fb1547c964c1fbd73bdb4a79385b43c11408504bb099483cb1a2"),
    "430037": ("7511570A.0PA", "dd64d18d2e0bb898dba95abbac82edca358e1311a48c72fd9b5b0ade640010c5"),
    "430064": ("7528050A.0PA", "18464c777122df8560a82b95207254254de37227f21d04b85941dfd6a2a18195"),
    "430065": ("4482751A.0PA", "48ef9c17be2c0becb98f1783262d58a6202a0b24a16b0eca923628b9bd10c56d"),
    "430066": ("7545150A.0PA", "d75e04dd6e6bf77b100f6cdf3d10b298f23c5207c9d90973b0fa7007932cf972"),
    "430066-later": ("7572342A.0PA", "91181c383c39b4ad98986e9b0b834f700a3f52d955d8dc87238c6b256395d2d4"),
    "430069-later": ("7571017A.0PA", "761d01669e6a28b850ff20b04e073ceaa956a007ad43d39244b2ec1ad69ec215"),
}
CALIBRATIONS = {
    "0110C7": ("P7513731.0DA", "e7caca6915e7f12d24567581521681cedd6c7712a77ae562bffc7bfaf2dbe948"),
    "0110CA": ("P7526756.0DA", "39f7fd6e0927f0a445a1616e879aa3f9fb31501dcb1686c12586f16f9fc90445"),
    "430037": ("O7509402.0DA", "70e5ceb3094bf793cb8dea74cb4c8e4dd9b74e9b0ad5a76f6f70ecc7eecab981"),
    "430064": ("O7532587.0DA", "557021fcb24533e37d182d6c33bfba32b54de7d2b8963b51afa3253bfe49e3ef"),
    "430065": ("O4485524.0DA", "a252206b19e53a8098964b97538afa55d18e34b5ce15b10758df400a99d44e8a"),
    "430066": ("O4834242.0DA", "579089afe66f6d0aae8ec363301ee8f449ff9117d5aa7ecb8854f8b962f68231"),
    "430069": ("O7564455.0DA", "e98252efbefe325d0601bb6cafdfa428198b2149b2bc73c28e781975c1b13d89"),
}


@lru_cache
def full_image(software):
    name, digest = FULL[software]
    path = SHARED / name
    if not path.is_file():
        pytest.skip("Matching firmware reference is unavailable")
    data = path.read_bytes()
    assert len(data) == 0x80000 and sha256(data).hexdigest() == digest
    return data


@lru_cache
def package_memory(key):
    software = key.split("-")[0]
    folder = "MDS42" if key.startswith("0110") else "MDS43"
    parser_path = DATEN / "export_daten.py"
    if not parser_path.is_file():
        pytest.skip("Matching package parser is unavailable")
    spec = importlib.util.spec_from_file_location("transfer_daten_parser", parser_path)
    parser = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(parser)
    memory = {}
    for name, digest in (PROGRAMS[key], CALIBRATIONS[software]):
        path = DATEN / "packages" / folder / name
        if not path.is_file():
            pytest.skip("Matching firmware package is unavailable")
        assert sha256(path.read_bytes()).hexdigest() == digest
        supplied, _ = parser.parse_package(path)
        assert all((address & 0x7FFFF) >= 0x10000 for address in supplied)
        memory.update(supplied)
    return memory


@lru_cache
def composed_image(software, boot_software):
    family = "MS42" if software.startswith("0110") else "MS43"
    assert boot_software.startswith("0110") == (family == "MS42")
    boot = full_image(boot_software)
    if software in FULL:
        data = boot[:0x10000] + full_image(software)[0x10000:]
        provenance = (FULL[boot_software], FULL[software])
    else:
        image = bytearray(b"\xff" * 0x80000)
        image[:0x10000] = boot[:0x10000]
        for address, value in package_memory(software).items():
            image[address & 0x7FFFF] = value
        data = bytes(image)
        provenance = (FULL[boot_software], PROGRAMS[software], CALIBRATIONS[software.split("-")[0]])
    return SimpleNamespace(image=data, family=family, software=software.split("-")[0],
                           boot_software=boot_software, provenance=provenance)
