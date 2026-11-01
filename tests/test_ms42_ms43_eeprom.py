"""EEPROM selection and host bounds; native device execution lives in ECU Emulator."""
from types import SimpleNamespace

import pytest

import ds2
import ms42_ms43
from ms42_ms43_eeprom import EepromAgent, select_eeprom
from tests.test_ms42_ms43_agent import BytePipe


@pytest.fixture
def service(monkeypatch):
    monkeypatch.setattr(ds2, "_import_d2xx_serial", lambda: pytest.fail("Real driver access"))
    pipe = BytePipe("MS43")
    link = ds2.DS2Interface("INJECTED_EEPROM_ONLY", two_stop=False)
    link._ser, link.transport_name, link.baud = pipe, "injected", 187500
    return EepromAgent(ms42_ms43.MS42MS43ECU(link, "MS43")), pipe


@pytest.mark.parametrize("cmd,offset,count,data", [
    ("E", 0, 0, b""), ("F", 0, 0, b""), ("I", 1, 4, b""),
    ("R", -2, 2, b""), ("R", 1, 2, b""), ("R", 2046, 4, b""),
    ("R", 0, 1026, b""), ("R", 0, 0, b""), ("R", 0, 3, b""),
    ("P", 0, 4, b"1234"), ("P", 0, 2, b"1"), ("P", True, 2, b"12"),
    ("X", 1, 0, b""), ("R", 0, 2, b"12"),
])
def test_bad_ranges_and_flash_commands_never_reach_serial(service, cmd, offset, count, data):
    reader, pipe = service
    with pytest.raises(ValueError):
        reader._transfer(ord(cmd), offset, count, data)
    assert not pipe.requests


def test_read_all_uses_exact_two_blocks_and_progress(service):
    reader, pipe = service
    progress = []
    assert reader.read_all(lambda *row: progress.append(row)) == bytes(range(256)) * 8
    assert [(int.from_bytes(p[1:4], "big"), int.from_bytes(p[4:6], "big"))
            for p in pipe.requests] == [(0, 1024), (1024, 1024)]
    assert [p[:2] for p in progress] == [(1024, 2048), (2048, 2048)]


def test_ms42_is_not_assumed_to_have_this_device():
    assert select_eeprom(SimpleNamespace(family="MS42")) is None


def test_provided_identity_is_reused_and_standard_serial_supported(service, monkeypatch):
    reader, pipe = service
    ecu = reader.ecu
    ecu._resident_signature_matches = True
    identity = {"software_id": "430069", "hardware_id": "7545150"}
    monkeypatch.setattr(ecu, "_identity_fields", lambda: pytest.fail("Redundant identity read"))
    pipe.native_fast_capable = False
    selected = select_eeprom(ecu, identity=identity)
    assert isinstance(selected, EepromAgent)
    assert selected.identity == identity
    assert selected.profile["baud"] == 9600


def test_unknown_software_does_not_advertise_nonexistent_ds2_eeprom_route(service):
    reader, _pipe = service
    logs = []
    assert select_eeprom(reader.ecu, logs.append, identity={"software_id": "999999"}) is None
    assert not logs


@pytest.mark.parametrize("baud", [125000, 187500])
def test_eeprom_profile_honors_compatibility_toggle(service, monkeypatch, baud):
    import transfer_rates
    reader, _pipe = service
    reader.ecu._resident_signature_matches = True
    monkeypatch.setattr(transfer_rates, "get_high_baud", lambda: baud)
    selected = select_eeprom(reader.ecu, identity={"software_id": "430069"})
    assert selected.profile["baud"] == baud


def test_payload_has_independent_identity_and_manifest():
    from engines.ms42_ms43.build import verify
    from engines.ms42_ms43.payload import payload
    from engines.ms42_ms43.eeprom_payload import payload as eeprom_payload
    verify(eeprom=True)
    verify()
    assert payload("MS43") != eeprom_payload("MS43")


def test_production_enter_uploads_only_eeprom_payload_and_checks_its_identity(service, monkeypatch):
    from engines.ms42_ms43.eeprom_payload import payload
    from ms42_ms43_resident import ResidentReader

    reader, _pipe = service
    code = payload("MS43")
    commands = []
    monkeypatch.setattr(ResidentReader, "enter", lambda self, log_fn=None: None)
    monkeypatch.setattr(ResidentReader, "_read", lambda self, address, count: code[:count])
    monkeypatch.setattr(reader, "_exchange", lambda cmd, args=b"", **kw: commands.append((cmd, args, kw)))
    monkeypatch.setattr(reader.transport, "_read_exact", lambda count, timeout: b"\xA5")
    def identify(offset, count, *, command):
        assert (offset, count, command) == (0, 4, ord("I"))
        return b"BE43"
    monkeypatch.setattr(reader, "_read_packet", identify)
    reader.enter()
    assert b"".join(args[2:] for cmd, args, _ in commands if cmd == 0x78) == code
    assert commands[-1] == (0x79, b"", {"response": False})
