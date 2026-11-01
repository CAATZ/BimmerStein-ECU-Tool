"""Exact X001 configuration owners; supplied runtime inputs, no ECU traffic."""

from hashlib import sha256
from pathlib import Path

import pytest

import ms42_ms43_config as config
from ms42_ms43_image import extract_calibration
from tests.test_ms42_ms43_config_daten import native as native
from tests.test_ms42_ms43_config_revisions import private_sources as private_sources
from tests.test_ms42_ms43_config_expansion import expected_selector
from tests.test_ms43_revision_config_controls import first_gear, secondary_air


# Stock owner, actual X001 owner, complete bounded instruction span.
UNCHANGED = (
    (0x90584, 0x9056E, 170), (0x90D68, 0x90D52, 70),
    (0xA01AC, 0xA01AC, 0x600), (0xA2AA8, 0xA2AA8, 88),
    (0x9F5DE, 0x9F5DE, 132), (0xA3A52, 0xA3A52, 0x218),
    (0xB01B2, 0xB00FC, 28), (0xD8ADE, 0xD8ADE, 32),
    (0xBF3C6, 0xBF3C6, 86), (0xD4968, 0xD4968, 88),
    (0xD6276, 0xD6276, 90), (0xC0D20, 0xC0CF6, 60),
    (0xC0F28, 0xC0F12, 14), (0xC0F52, 0xC0F3C, 14),
    (0xBE96C, 0xBE96C, 8), (0x9D968, 0x9D968, 0x1D6),
)
ADDED = (0xB19, 0xB1C, 0xB20, 0xB26, 0xB25, 0xCFA, 0xA83, 0xCFC,
         0xB1A, 0xB23, 0xB24, 0xB2A, 0xC52, 0xC50, 0xC4F, 0xC51, 0xE01, 0xE06)


@pytest.fixture
def x001(private_sources, native):
    return native['MS43'](private_sources['x001'], experimental=True)


def test_owners_match_actual_x001_bytes(private_sources):
    for reference, actual, size in UNCHANGED:
        assert private_sources['stock'][reference - 0x80000:reference - 0x80000 + size] == (
            private_sources['x001'][actual - 0x80000:actual - 0x80000 + size]), hex(actual)


@pytest.mark.parametrize('mode', (0, 1, 255))
@pytest.mark.parametrize('active_bank', (0, 1, 2))
@pytest.mark.parametrize('consumer_bank', (0, 1))
def test_secondary_air_bank_choice_reaches_unchanged_consumer(x001, mode, active_bank, consumer_bank):
    x001.mem.image[0x70B20] = mode
    x001.write_byte(0x8C64, active_bank)
    assert x001.run_from(0xBE130, stop_at=0xBE15C, max_steps=15).exit_reason == 'stop_at'
    banks = (0x8C6A, 0x8DD8)
    selected = 0 if mode == 0 or active_bank == 1 else 1
    assert x001.read(0xE35E) == banks[selected]
    x001.reg.set_word(9, banks[consumer_bank])
    assert x001.run_from(0xBE96C, stop_at=(0xBE974, 0xBEB86), max_steps=4).exit_reason == 'stop_at'
    assert x001.cpu.pc == (0xBE974 if consumer_bank == selected else 0xBEB86)


@pytest.mark.parametrize('entry,offset,groups', (
    (0xC0CF6, 0xB26, ((1, (0x90EC, 0x9102)),)),
    (0xC0D08, 0xB1C, ((0x800, (0x90F2, 0x9108)), (2, (0x90F4, 0x910A)),
                       (0x2000, (0x90EE, 0x9104)))),
    (0xC0F12, 0xE01, ((0x10, (0x9126,)),)),
    (0xC0F3C, 0xE01, ((0x20, (0x9126,)),)),
))
@pytest.mark.parametrize('value', (0, 1, 255))
def test_diagnosis_options_change_only_their_actual_masks(x001, entry, offset, groups, value):
    x001.mem.image[0x70000 + offset] = value
    size = 6 + sum(4 + len(addresses) * 4 for _, addresses in groups)
    for address in range(0x9000, 0x9140, 2):
        x001.write(address, 65535)
    assert x001.run_from(entry, stop_at=entry + size, max_steps=20).exit_reason == 'stop_at'
    clear_mask = bool(value) if offset == 0xE01 else not value
    changes = {address: 65535 ^ mask for mask, addresses in groups for address in addresses} if clear_mask else {}
    for address in range(0x9000, 0x9140, 2):
        assert x001.read(address) == changes.get(address, 65535)


@pytest.mark.parametrize('value', (0, 1, 255))
def test_dmtl_switch_also_controls_the_original_runtime_task(x001, value):
    x001.mem.image[0x70B1C] = value
    assert x001.run_from(0xA3A52, stop_at=(0xA3A5A, 0xA3C6A), max_steps=3).exit_reason == 'stop_at'
    assert x001.cpu.pc == (0xA3A5A if value else 0xA3C6A)


@pytest.mark.parametrize('mode', (0, 1, 2, 3, 255))
@pytest.mark.parametrize('automatic', (False, True))
def test_both_charge_owners_keep_transmission_choice(x001, mode, automatic):
    for entry in (0xB00FC, 0xD8ADE):
        code = x001.mem.image[entry - 0x80000:entry - 0x80000 + 28]
        rejected = entry + 24 + code[23] * 2
        assert rejected == entry + 28 + code[26] * 2
        x001.mem.image[0x70B1A] = mode
        x001.write(0xFD9C, int(automatic))
        assert x001.run_from(entry, stop_at=(entry + 28, rejected), max_steps=12).exit_reason == 'stop_at'
        assert x001.cpu.pc == (entry + 28 if mode == 3 or mode == (2 if automatic else 1) else rejected)


@pytest.mark.parametrize('automatic', (False, True))
@pytest.mark.parametrize('responsive', (False, True))
@pytest.mark.parametrize('rising', (False, True))
def test_both_hysteresis_edges_keep_selected_transmission(x001, automatic, responsive, rising):
    x001.mem.image[0x70B23 + int(automatic)] = int(responsive)
    x001.mem.image[0x70B23 + int(not automatic)] = int(not responsive)
    x001.write(0xFD9C, int(automatic) << 8)
    x001.write(0xFD2C, 3)
    x001.write_byte(0x81A8, 41)
    x001.write_byte(0x81A9, 93)
    entry = 0xBF3C6 + (48 if rising else 0)
    assert x001.run_from(entry, stop_at=entry + 38, max_steps=12).exit_reason == 'stop_at'
    changes = responsive if rising else not responsive
    assert x001.read(0xFD2C) == 3 | (int(changes) << 14)
    assert x001.read_byte(0x81A9) == (41 if changes else 93)


@pytest.mark.parametrize('enabled,automatic', ((False, False), (False, True), (True, False), (True, True)))
@pytest.mark.parametrize('demand', (0, 500, 1200))
@pytest.mark.parametrize('gear', (1, 2))
def test_relocated_first_gear_limit_and_native_consumer(x001, native, private_sources, enabled, automatic, demand, gear):
    base = native['MS43'](private_sources['stock'], experimental=True)
    common = (0xB2A, 0x9AC, 0xFD9C, 0x81AC, 0x8023)
    assert first_gear(x001, (0x9056E, *common), enabled, automatic, demand, gear) == (
        first_gear(base, (0x90584, *common), enabled, automatic, demand, gear))


@pytest.mark.parametrize('state', (0, 1, 2, 3, 4, 255))
@pytest.mark.parametrize('value', (0, 1))
def test_automated_clutch_modes_preserve_unoffered_state_two(x001, state, value):
    offsets = (0xC52, 0xC50, 0xC53, 0xC4F, 0xC51)
    code = expected_selector(0xFB57, 0xFD1E, 7, offsets)
    assert x001.mem.image[0x54968:0x54968 + len(code)] == code
    x001.mem.image[0x70000 + offsets[min(state, 4)]] = value
    x001.write_byte(0xFB57, state)
    x001.write(0xFD1E, 0xA55A)
    assert x001.run_from(0xD4968, stop_at=0xD4968 + len(code), max_steps=25).exit_reason == 'stop_at'
    assert x001.read(0xFD1E) == (0xA55A & ~0x80) | value << 7


@pytest.mark.parametrize('inhibit', (None, 'disabled', 'source', 'flag', 'request', 'limit', 'coolant', 'existing_cut'))
def test_traction_cut_preserves_each_native_prerequisite(x001, inhibit):
    x001.mem.image[0x70E06] = int(inhibit != 'disabled')
    x001.write_byte(0x8023, 4 if inhibit == 'source' else 1)
    x001.write(0xFD08, 0x2000 if inhibit == 'flag' else 0)
    x001.reg.set_word(6, 2 if inhibit == 'request' else 1)
    x001.write(0xE0B4, 0xFFFF if inhibit == 'limit' else 1000)
    x001.mem.image[0x70D0B] = 100
    x001.write_byte(0x8433, 0 if inhibit == 'coolant' else 255)
    x001.write_byte(0x8029, int(inhibit == 'existing_cut'))
    x001.write(0xE0B8, 0x4000)
    x001.write_byte(0x8024, 5)
    seen = set()
    x001.cpu.set_trace(lambda pc, op: seen.add(pc))
    assert x001.run_from(0x90D52, stop_at=0x90D98, max_steps=500).exit_reason == 'stop_at'
    assert (0x90D82 in seen) == (inhibit is None)
    assert x001.read_byte(0x8023) == (4 if inhibit == 'source' else 1)
    if inhibit:
        assert x001.read_byte(0x8024) == 0


@pytest.fixture
def actuator_context(monkeypatch, private_sources, native):
    import tests
    root = Path.home() / 'ECU Emulator'
    monkeypatch.setattr(tests, '__path__', [*tests.__path__, str(root / 'tests')])
    from tests.test_c167_vanos_idle import prepare_actuators, call_native
    cls = native['MS43']
    # Only the constructor's accepted fixture hash changes; native execution,
    # peripherals and the original image bytes retain the canonical model.
    image = private_sources['x001']
    monkeypatch.setattr(cls, 'IMAGE_SHA256', sha256(image).hexdigest())
    return prepare_actuators(cls, image), call_native


@pytest.mark.parametrize('threshold', (151, 255))
def test_purge_operation_threshold_controls_native_output_for_all_input_bytes(actuator_context, threshold):
    emu, call_native = actuator_context
    call_native(emu, 0xA0070)
    emu.mem.image[0x70CFA] = threshold
    seen = set()
    emu.cpu.set_trace(lambda pc, op: seen.add(pc))
    for coolant in range(256):
        seen.clear()
        for address, value in ((0xFDAA, 0x1000), (0xFD78, 0x40), (0xFD94, 8), (0x8720, 0x4000)):
            emu.write(address, value)
        emu.write_byte(0x8433, coolant)
        call_native(emu, 0xA01AC)
        assert (0xA0200 in seen) == (coolant > threshold)
        if threshold == 255:
            assert emu.read(0x8720) == 0
            assert emu.read(0xFD94) & 0x7F == 1
            assert emu.read(0xE638) == 622


@pytest.mark.parametrize('threshold', (178, 255))
def test_purge_flow_threshold_preserves_explicit_diagnostic_request(actuator_context, threshold):
    emu, call_native = actuator_context
    emu.mem.image[0x70CFC] = threshold
    for coolant in range(256):
        emu.write(0xFD2C, 0x200)
        emu.write(0xFD36, 0x10)
        emu.write_byte(0x8433, coolant)
        call_native(emu, 0x9F5DE)
        assert bool(emu.read(0xFD96) & 0x800) == (coolant > threshold)
    emu.write_byte(0x8176, 1)
    call_native(emu, 0x9F5DE)
    assert emu.read(0xFD96) & 0x800


@pytest.mark.parametrize('fault', (0, 1, 2))
def test_purge_electrical_limit_retains_recorded_faults(actuator_context, fault):
    emu, _ = actuator_context
    def update():
        emu.write(0xFDE0 + 2 * fault, 0x40)
        emu.write_byte(0xFC0C, 0)
        assert emu.run_from(0xA2AA8, stop_at=0xA2B00, max_steps=10000).exit_reason == 'stop_at'
    emu.mem.image[0x70A83] = 0
    for _ in range(8):
        update()
    assert emu.read_byte(0x93F6) == 1 << fault
    assert emu.read_byte(0x93F7) == 0
    emu.mem.image[0x70A83] = 10
    for _ in range(8):
        update()
    assert emu.read_byte(0x93F6) & 0x20
    assert emu.read_byte(0x93F7) == 10
    emu.mem.image[0x70A83] = 0
    update()
    assert emu.read_byte(0x93F6) & 0x20


def test_modified_pump_mode_is_not_mislabeled_as_unchanged(x001, native, private_sources):
    base = native['MS43'](private_sources['stock'], experimental=True)
    layout = (0xBEEBC, 0xB25, 0x80F5, (3, 7, 8))
    for mode in (0, 1, 2, 255):
        for previous in range(4):
            assert secondary_air(x001, layout, mode, previous) == secondary_air(base, layout, mode, previous)
    assert secondary_air(base, layout, 3, 0) == (0, (True, True, False))
    assert secondary_air(x001, layout, 3, 0) == (0, (False, True, False))


@pytest.mark.parametrize('mode', range(4))
@pytest.mark.parametrize('learned', (False, True))
def test_existing_lambda_choices_match_inside_extended_x001_owner(x001, native, private_sources, mode, learned):
    base = native['MS43'](private_sources['stock'], experimental=True)
    results = []
    for emu, entry, end in ((base, 0x9DB3E, 0x9DBC8), (x001, 0x96D8A, 0x96E4C)):
        emu.mem.image[0x70B19] = mode
        for address in (0x80F6, 0x80F7, 0x8C64):
            emu.write_byte(address, 0xA5)
        for address in (0xFD1A, 0xFD26, 0xFD9C):
            emu.write(address, 0xA55A)
        emu.write(0xFD78, 0x80 if learned else 0)
        assert emu.run_from(entry, stop_at=(end, 0x9D968), max_steps=100).exit_reason == 'stop_at'
        results.append((emu.cpu.pc == 0x9D968,
                        tuple(emu.read_byte(a) for a in (0x80F6, 0x80F7, 0x8C64)),
                        tuple(emu.read(a) for a in (0xFD1A, 0xFD26, 0xFD9C))))
    assert results[0] == results[1]


@pytest.mark.parametrize('partial', (False, True))
@pytest.mark.parametrize('offset', ADDED)
def test_verified_choices_use_existing_editor_and_preserve_all_other_bytes(private_sources, offset, partial):
    data = private_sources['x001']
    if partial:
        data = extract_calibration(data)
    feature = next(f for f in config.features_for('MS43', '43X001') if f.byte == offset)
    address = offset if partial else 0x70000 + offset
    for label, value in feature.options:
        updated = config.apply_config(data, {feature.name: label})
        expected = bytearray(data)
        expected[address] = value
        assert updated == expected
        assert config.read_config(updated)[feature.name] == label


@pytest.mark.parametrize('partial', (False, True))
@pytest.mark.parametrize('offset,value', ((0xB19, 4), (0xB19, 5), (0xB19, 6), (0xB25, 3), (0xB25, 255)))
def test_changed_custom_modes_are_preserved_when_editing_another_setting(private_sources, partial, offset, value):
    data = bytearray(private_sources['x001'])
    data[0x70000 + offset] = value
    if partial:
        data = extract_calibration(data)
    feature = next(f for f in config.features_for('MS43', '43X001') if f.byte == offset)
    assert config.read_config(data)[feature.name] == f'Custom (0x{value:02X})'
    updated = config.apply_config(data, {'First-gear torque limitation': 'Enabled'})
    expected = bytearray(data)
    expected[(0 if partial else 0x70000) + 0xB2A] = 1
    assert updated == expected


def test_x001_does_not_offer_removed_or_changed_selector_options(private_sources):
    features = {f.name: f for f in config.features_for('MS43', '43X001')}
    assert 'Cruise control main switch' not in features
    assert 'Exhaust temperature sensors' not in features
    assert [v for _, v in features['Lambda sensor configuration'].options] == list(range(4))
    assert [v for _, v in features['Secondary air pump'].options] == [0, 1, 2]
    # X001 explicitly calls its extended initializer; this is not a copied
    # software label standing in for the actual relocated owner.
    assert private_sources['x001'][0x1DB3E:0x1DB46] == bytes.fromhex('da098a6dda0958dd')
