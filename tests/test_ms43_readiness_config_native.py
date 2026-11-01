"""Original readiness initialization through the native OBD status consumer."""

import pytest

import ms42_ms43_config as config
from tests.test_ms42_ms43_config_daten import native as native, packages as packages
from tests.test_ms42_ms43_config_revisions import private_sources as private_sources


LAYOUTS = {
    '430069': (0xC0E7A, 0xE01),
    '43X001': (0xC0E7A, 0xE01),
    '430066': (0xC0E7A, 0xE26),
    '430066-later': (0xC0E86, 0xE26),
}


@pytest.mark.parametrize('key', LAYOUTS)
@pytest.mark.parametrize('excluded', (False, True))
@pytest.mark.parametrize('single_bank', (False, True))
def test_postcat_option_excludes_pending_check_from_native_obd_readiness(
        native, private_sources, packages, key, excluded, single_bank):
    image = (private_sources['stock'] if key == '430069' else
             private_sources['x001'] if key == '43X001' else packages[key].image)
    entry, offset = LAYOUTS[key]
    label = 'Excluded from readiness' if excluded else 'Required for readiness'
    edited = config.apply_config(image, {'Post-cat lambda readiness diagnosis': label},
                                 'MS43', key.split('-')[0])
    assert edited[0x70000 + offset] == int(excluded)
    assert config.read_config(edited)['Post-cat lambda readiness diagnosis'] == label
    assert edited[entry - 0x80000:entry - 0x80000 + 10] == bytes.fromhex('88608a0d04608a0d0290')
    emu = native['MS43'](edited, experimental=True)
    emu.write(0xFD1A, 0x40 if single_bank else 0x200)
    # Supply one pending activation check. Other readiness monitors are
    # already complete; the initializer must preserve or exclude this bit.
    for address in range(0x9122, 0x9138, 2):
        emu.write(address, 0)
    emu.write(0x9126, 0x10)
    assert emu.call(entry, max_steps=5000).exit_reason == 'stop_at'
    pending = emu.read(0x9126) & 0x10
    assert bool(pending) is not excluded
    # Keep only this monitor pending after initialization so the result is
    # attributable to this option, without changing executable/table bytes.
    for address in range(0x9122, 0x9138, 2):
        emu.write(address, pending if address == 0x9126 else 0)
    assert image[0x6C208:0x6C20E] == bytes.fromhex('1f1e24292825')
    assert image[0x6C21F] == 6
    assert image[0x14D42:0x14E74] == private_sources['stock'][0x14D42:0x14E74]
    assert emu.run_from(0x94D42, stop_at=0x94E74, max_steps=1000).exit_reason == 'stop_at'
    # OBD PID01's final byte: bit5 means oxygen-sensor monitoring incomplete.
    assert emu.read_byte(0xE608) == (0 if excluded else 0x20)
    expected = bytearray(image)
    expected[0x70000 + offset] = int(excluded)
    assert emu.mem.image == expected
