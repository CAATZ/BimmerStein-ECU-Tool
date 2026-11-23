"""Hardware-BSL host/state tests over a fake wire, without any physical ports."""

from dataclasses import replace
import json
from pathlib import Path

import pytest

from bsl_service import BSLOperationRequest
import ds2
import ms42_ms43_bsl as bsl
from ms42_ms43_image import PROFILES, correct_checksums
from tests.test_ms42_ms43_agent import crc16, packet
from tests.test_ms42_ms43_image import synthetic_image


def image(family):
    data = synthetic_image(family)
    profile = PROFILES[family]
    program = 0x50000 if family == "MS42" else 0x6FFF0
    data[program:program + 4] = bytes.fromhex("90 48 24 42")
    marker = profile.calibration_offset + profile.partial_size - 16
    data[marker:marker + 4] = bytes.fromhex("42 24 48 90")
    at, pointer = (0x3C04, 0x3EDE) if family == "MS42" else (0x250, 0x3ED4)
    data[at:at + 4] = pointer.to_bytes(4, "little")
    return correct_checksums(data, family)[0]


class Wire:
    native_fast_capable = True
    transport_name = "injected"
    is_open = True
    timeout = 0.2
    write_timeout = 1.0
    parity = "E"
    stopbits = 1

    def __init__(self, family, source, *, echo=True, fragment=11):
        self.family, self.image = family, bytearray(source)
        self.echo, self.fragment = echo, fragment
        self.pending, self.phase = b"", "sync"
        self.baudrate = bsl.INITIAL_BAUD
        self.rate = 125000
        self.requests, self.events = [], []
        self.flash_id = (0x20, 0xD6)
        self.fault = None
        self.erases, self.programs = [], []
        self.armed = False

    def close(self):
        self.is_open = False

    def setDTR(self, value):
        assert value is False

    setRTS = setDTR

    def reset_input_buffer(self):
        assert not self.pending

    def flush(self):
        pass

    def read(self, count):
        count = min(count, self.fragment)
        result, self.pending = self.pending[:count], self.pending[count:]
        return result

    def write(self, data):
        from engines.ms42_ms43 import hw_bsl_v1 as loader
        from engines.ms42_ms43.payload import payload

        data = bytes(data)
        self.events.append((self.phase, data))
        assert self.parity == "N"
        assert not self.pending
        response = b""
        if self.phase == "sync":
            assert data == b"\x00" and self.baudrate == 38400
            response = b"\xC5" if self.fault != "sync" else b"\x55"
            self.phase = "stage0"
        elif self.phase == "stage0":
            assert data == loader.stage0(self.family, self.rate)
            self.phase = "stage1"
        elif self.phase == "stage1":
            assert data == loader.stage1(self.family, self.rate)
            header = loader.READY[self.family] + b"".join(n.to_bytes(2, "little") for n in self.flash_id)
            response = header + crc16(header).to_bytes(2, "big")
            if self.fault == "ready_crc":
                response = response[:-1] + bytes((response[-1] ^ 1,))
            self.phase = "agent"
        elif self.phase == "agent":
            assert data[:-2] == payload(self.family)
            assert crc16(data[:-2]) == int.from_bytes(data[-2:], "big")
            response = b"K" if self.fault != "agent_crc" else b"\x5A"
            self.phase = "go"
        elif self.phase == "go":
            assert data == b"G" and self.baudrate == 38400
            response = b"\xA5" if self.fault != "baud" else b""
            self.phase = "packets"
        else:
            assert self.baudrate == self.rate
            assert crc16(data[:-2]) == int.from_bytes(data[-2:], "big")
            command, start, count = data[0], int.from_bytes(data[1:4], "big"), int.from_bytes(data[4:6], "big")
            body, status, result = data[6:-2], 0, b""
            self.requests.append((command, start, count, body))
            assert command in b"IRPE", "No DS2, reset or modifying finalization in hardware recovery"
            if command == ord("I"):
                self.armed = bool(start)
                result = self.family.replace("MS", "BS").encode()
            elif command == ord("R"):
                result = bytes(self.image[start:start + count])
            elif command == ord("E"):
                assert start >= 0x10000 or self.armed
                index = bsl.SECTORS.index(start)
                end = (*bsl.SECTORS[1:], bsl.FLASH_SIZE)[index]
                self.image[start:end] = b"\xff" * (end - start)
                self.erases.append(start)
            elif command == ord("P"):
                assert start >= 0x10000 or self.armed
                assert len(body) == count
                if self.fault == "program_failure":
                    status = 4
                else:
                    assert all(wanted & old == wanted for wanted, old in zip(body, self.image[start:start + count]))
                    self.image[start:start + count] = body
                    self.programs.append(start)
            response = packet(command, start, count, result, status=status)
            if self.fault == "overclear_program_ack" and command == ord("P"):
                index = next(i for i, byte in enumerate(body) if byte)
                self.image[start + index] &= self.image[start + index] - 1
                self.fault, response = None, b""
            elif self.fault == "lost_program_ack" and command == ord("P"):
                self.fault, response = None, b""
        self.pending = (data if self.echo else b"") + response
        return len(data)


@pytest.fixture(params=("MS42", "MS43"))
def setup(request, tmp_path, monkeypatch):
    family = request.param
    source = image(family)
    peer = Wire(family, source)

    def factory(**kwargs):
        assert kwargs["port"] == "FAKE_HW_BSL_ONLY"
        assert kwargs["baudrate"] == 38400 and kwargs["two_stop"] is False
        return peer

    args = BSLOperationRequest(
        port="FAKE_HW_BSL_ONLY", chip="29f400", half="full", family=family,
        baud=125000, reset_line=None, serial_factory=factory, region="all",
        ref_bytes=source, arm=True, backup_dir=str(tmp_path), preserve_identity=True)
    monkeypatch.setattr(ds2, "_import_d2xx_serial", lambda: pytest.fail("Physical driver opened"))
    return args, peer


@pytest.mark.parametrize("echo", (True, False))
def test_bootstrap_accepts_separated_echo_and_c5_and_every_single_byte_fragment(setup, echo):
    args, peer = setup
    peer.echo, peer.fragment = echo, 1
    session = bsl._connect(args)
    assert session.active and session.flash_id == (0x20, 0xD6)
    assert session.read_full() == bytes(peer.image)
    assert peer.events[0] == ("sync", b"\x00")
    assert not peer.erases and not peer.pending


@pytest.mark.parametrize("fault", ("sync", "ready_crc", "agent_crc", "baud"))
def test_entry_failure_never_replays_sync_or_issues_flash_commands(setup, fault):
    args, peer = setup
    peer.fault = fault
    with pytest.raises(ds2.DS2Error):
        bsl.cmd_flash(args)
    assert sum(phase == "sync" for phase, _ in peer.events) == 1
    assert not peer.erases and not peer.programs


def test_preview_and_invalid_image_do_not_open_port(setup):
    args, peer = setup
    assert bsl.cmd_flash(replace(args, arm=False)) == 0
    assert not peer.events
    bad = bytearray(args.ref_bytes)
    bad[0x100] ^= 1
    with pytest.raises(ValueError, match="checksums"):
        bsl.cmd_flash(replace(args, ref_bytes=bytes(bad)))
    assert not peer.events


@pytest.mark.parametrize("fields", ({"no_backup": True}, {"force": True},
                                  {"preserve_identity": False, "fix_checksums": True}))
def test_recovery_guards_cannot_be_bypassed_before_connect(setup, fields):
    args, peer = setup
    with pytest.raises(ValueError):
        bsl.cmd_flash(replace(args, **fields))
    assert not peer.events


def test_full_recovery_preserves_identity_retained_data_and_writes_boot_last(setup):
    args, peer = setup
    original = bytes(peer.image)
    target = bytearray(args.ref_bytes)
    target[0x3C40:0x3C4A] = b"DONOR-DATA"
    target[0x4000:0x8000] = b"\xff" * 0x4000
    target[0x200:0x204] = b"BOOT"
    target[0x8100:0x8104] = b"BOOT"
    target[0x30000:0x30004] = b"TEST"
    args.ref_bytes = correct_checksums(target, args.family)[0]
    observed = []

    def before_erase():
        backups = list(Path(args.backup_dir).glob("*.bin"))
        assert len(backups) == 2 and all(p.stat().st_size == bsl.FLASH_SIZE for p in backups)
        assert not peer.erases
        observed.append(True)

    args.destructive_cb = before_erase
    assert bsl.cmd_flash(args) == 0
    assert observed == [True]
    assert peer.image[0x4000:0x8000] == original[0x4000:0x8000]
    assert peer.image[0x3C40:0x3C4A] == original[0x3C40:0x3C4A]
    assert peer.erases[-2:] == [0x8000, 0]
    prepared = next(Path(args.backup_dir).glob("*_target.bin")).read_bytes()
    assert peer.image == prepared
    records = [json.loads(line) for line in next(Path(args.backup_dir).glob("*.jsonl")).read_text().splitlines()]
    assert records[-1]["fields"]["outcome"] == "success"
    assert all(command not in (ord("X"), ord("F")) for command, *_ in peer.requests)


def test_exact_backup_restore_recovers_blank_flash(setup):
    args, peer = setup
    peer.image[:] = b"\xff" * bsl.FLASH_SIZE
    args.preserve_identity = False
    assert bsl.cmd_flash(args) == 0
    assert peer.image == args.ref_bytes
    assert peer.erases[-2:] == [0x8000, 0]


def test_missing_live_identity_requires_explicit_exact_restore(setup):
    args, peer = setup
    peer.image[:] = b"\xff" * bsl.FLASH_SIZE
    with pytest.raises(ValueError, match="identity is missing"):
        bsl.cmd_flash(args)
    assert not peer.erases


def test_tune_recovery_repairs_bad_calibration_and_preserves_other_half_of_ms42_sector(setup):
    args, peer = setup
    profile = PROFILES[args.family]
    original = bytes(peer.image)
    peer.image[profile.calibration_offset + 0x100] ^= 0x80
    args.region = "tune"
    args.ref_bytes = original[profile.calibration_offset:profile.calibration_offset + profile.partial_size]
    assert bsl.cmd_flash(args) == 0
    assert bytes(peer.image) == original
    assert peer.erases == [profile.calibration_offset & ~0xFFFF]


def test_unknown_geometry_refuses_before_backup_or_erase(setup):
    args, peer = setup
    peer.flash_id = (0x7E, 0x1234)
    with pytest.raises(ValueError, match="Flash ID"):
        bsl.cmd_flash(args)
    assert not peer.erases and not list(Path(args.backup_dir).iterdir())


def test_lost_program_ack_uses_readback_without_duplicate_program(setup, monkeypatch):
    args, peer = setup
    target = bytearray(args.ref_bytes)
    target[0x30000:0x30004] = b"TEST"
    args.ref_bytes = correct_checksums(target, args.family)[0]
    peer.fault = "lost_program_ack"
    drains = []
    monkeypatch.setattr(bsl.HardwareBSL, "_drain_until_quiet", lambda self: drains.append(True))
    assert bsl.cmd_flash(args) == 0
    assert drains == [True]
    assert len(peer.programs) == len(set(peer.programs))


def test_overcleared_bit_recovery_restores_the_complete_calibration_sector(setup, monkeypatch):
    args, peer = setup
    profile = PROFILES[args.family]
    original = bytes(peer.image)
    target = bytearray(original)
    target[profile.calibration_offset + 0x100] ^= 0x80
    args.ref_bytes = correct_checksums(target, args.family)[0]
    args.region = "tune"
    peer.fault = "overclear_program_ack"
    monkeypatch.setattr(bsl.HardwareBSL, "_drain_until_quiet", lambda self: None)
    assert bsl.cmd_flash(args) == 0
    assert bytes(peer.image) == args.ref_bytes
    start = profile.calibration_offset & ~0xFFFF
    assert peer.erases == [start, start]
    assert peer.image[:profile.calibration_offset] == original[:profile.calibration_offset]


def test_permanent_flash_failure_keeps_prepared_target_and_never_resets(setup):
    args, peer = setup
    target = bytearray(args.ref_bytes)
    target[0x30000:0x30004] = b"TEST"
    args.ref_bytes = correct_checksums(target, args.family)[0]
    peer.fault = "program_failure"
    with pytest.raises(ds2.DS2Error, match="Prepared image retained"):
        bsl.cmd_flash(args)
    assert len(peer.erases) == 1
    assert len(list(Path(args.backup_dir).glob("*_target.bin"))) == 1
    assert all(command not in (ord("X"), ord("F")) for command, *_ in peer.requests)


def test_backup_callback_failure_stops_before_arming_flash(setup):
    args, peer = setup
    args.backup_cb = lambda path: (_ for _ in ()).throw(OSError("backup catalog failed"))
    with pytest.raises(OSError, match="backup catalog"):
        bsl.cmd_flash(args)
    assert not peer.erases and not peer.armed


def test_dump_is_raw_and_does_not_overwrite_existing_file(setup, tmp_path):
    args, peer = setup
    args.file = str(tmp_path / "raw.bin")
    assert bsl.cmd_dump(args) == 0
    assert Path(args.file).read_bytes() == bytes(peer.image)
    with pytest.raises(FileExistsError):
        bsl._save(args.file, b"wrong")
    assert Path(args.file).read_bytes() == bytes(peer.image)


def test_exact_read_uses_one_deadline_for_fragmented_input(monkeypatch):
    now = [0.0]

    class Slow:
        timeout = 10.0

        def read(self, count):
            now[0] += 0.4
            return b"x"

    link = bsl._Link("NEVER_OPENED")
    link._ser = Slow()
    monkeypatch.setattr(bsl.time, "monotonic", lambda: now[0])
    assert link._read_exact(100, 1.0) == b"xxx"
    assert link._ser.timeout == 10.0


def test_ui_and_host_rates_match_the_compiled_loader_variants():
    from bsl_service import MS42_MS43_BSL_BAUD_RATES
    from engines.ms42_ms43.hw_bsl_v1 import SUPPORTED_BAUDS

    assert bsl.BAUD_RATES == MS42_MS43_BSL_BAUD_RATES == SUPPORTED_BAUDS
