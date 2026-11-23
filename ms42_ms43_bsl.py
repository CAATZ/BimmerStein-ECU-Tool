"""MS42/MS43 hardware bootstrap recovery using the shared RAM flash agent.

Every new connection requires manual hardware-BSL entry. No stock firmware,
DS2 authorization, automatic reset, or MS41 address transform is used here.
"""

from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import time
from uuid import uuid4

from checksum import _crc
from ds2 import DS2Error, DS2Interface, DS2Timeout
from ds2_fast_safety import OperationJournal
from ms42_ms43 import MS42MS43ECU, WriteState
from ms42_ms43_agent import AgentWriter, BLOCK_SIZE, FLASH_SIZE, SECTORS
from ms42_ms43_identity import IDENTITY_OFFSET, IDENTITY_SIZE, graft_identity
from ms42_ms43_image import (
    PROFILES, checksum_status, correct_checksums, extract_calibration,
    image_identity, image_profile,
)
from operation_log import emit


INITIAL_BAUD = 38400
BAUD_RATES = {"MS42": (125000, 166667), "MS43": (125000, 187500)}
# Device IDs, not the uploaded agent banner, admit the x16 bottom-boot geometry.
FLASH_IDS = {(0x01, 0x22AB), (0x04, 0x22AB), (0x20, 0x00D6), (0xC2, 0x22AB)}


class _Link(DS2Interface):
    def _read_exact(self, n, timeout):
        """Accumulate USB fragments within one deadline, including cable echo."""
        deadline, result = time.monotonic() + timeout, bytearray()
        previous = self._ser.timeout
        try:
            while len(result) < n:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._checked_io(setattr, self._ser, "timeout", remaining)
                chunk = self._checked_io(self._ser.read, n - len(result))
                if not chunk:
                    break
                result.extend(chunk)
            return bytes(result)
        finally:
            self._ser.timeout = previous


class HardwareBSL(AgentWriter):
    """Shares packet validation and flash recovery, with a cold-entry lifecycle."""

    route = "hardware_bsl"
    read_source = "Hardware BSL raw flash read"

    def __init__(self, request):
        family = request.family
        if family not in BAUD_RATES or request.baud not in BAUD_RATES[family]:
            raise ValueError("Unsupported MS42/MS43 hardware-BSL family or transfer rate")
        self.request = request
        self.transport = _Link(request.port, baud=INITIAL_BAUD, two_stop=False,
                               serial_factory=request.serial_factory)
        self.ecu = MS42MS43ECU(self.transport, family)
        self.profile = {"baud": request.baud}
        self.active = False
        self.write_boot = False
        self.flash_id = None
        self.cal_sector, self.program_sectors = -1, ()
        self._repair_regions = ()

    def close(self):
        self.transport.close()
        self.active = False

    def _send(self, data):
        link = self.transport
        if link._checked_io(link._ser.write, data) != len(data):
            raise DS2Error("Short hardware-BSL upload; re-enter hardware boot mode before retry")
        link._flush_output()
        if link.echo and link._read_exact(len(data), 2.0 + len(data) * 10 / link.baud) != data:
            raise DS2Error("Hardware-BSL cable echo differs; re-enter hardware boot mode before retry")

    def enter(self, log_fn=None):
        from engines.ms42_ms43 import hw_bsl_v1 as loader

        family, link = self.ecu.family, self.transport
        first, second = loader.stage0(family, self.profile["baud"]), loader.stage1(family, self.profile["baud"])
        code = bytes(self._agent_payload())
        if len(first) != 32 or len(second) != 1024 or len(code) != loader.AGENT_LENGTH[family]:
            raise ValueError("Hardware-BSL payload layout differs from the checked build")
        if _crc(code, 0xFFFF) != loader.AGENT_CRC[family]:
            raise ValueError("Hardware-BSL loader and RAM agent do not match")
        link.open()
        link._checked_io(setattr, link._ser, "parity", "N")
        link._BITS_PER_CHAR = 10
        link._checked_io(link._ser.reset_input_buffer)
        # A repeated zero after C5 would become executable bootstrap data.
        link.echo = False
        self._send(b"\x00")
        reply = link._read_exact(1, 3.0)
        if reply == b"\x00":
            link.echo = True
            reply = link._read_exact(1, 3.0)
        if reply != b"\xC5":
            raise DS2Error("C167 bootstrap did not answer C5; re-enter hardware boot mode and retry")
        self._send(first)
        self._send(second)
        reply = link._read_exact(11, 3.0)
        if (len(reply) != 11 or reply[:5] != loader.READY[family]
                or _crc(reply[:9], 0xFFFF) != int.from_bytes(reply[9:], "big")):
            raise DS2Error("Hardware-BSL loader identity/CRC failed; nothing erased")
        self.flash_id = (int.from_bytes(reply[5:7], "little"), int.from_bytes(reply[7:9], "little"))
        self._send(code + _crc(code, 0xFFFF).to_bytes(2, "big"))
        if link._read_exact(1, 3.0) != b"K":
            raise DS2Error("Hardware-BSL agent upload was not verified; nothing erased")
        self._send(b"G")
        link._checked_io(setattr, link._ser, "baudrate", self.profile["baud"])
        link.baud = self.profile["baud"]
        if link._read_exact(1, 3.0) != b"\xA5":
            raise DS2Timeout("Hardware-BSL baud/agent handoff failed; re-enter boot mode before retry")
        self._confirm_identity()
        self.active = True
        emit(f"{family} hardware BSL ready at {link.baud:,} baud; flash ID "
             f"{self.flash_id[0]:04X}/{self.flash_id[1]:04X}.")

    def read_full(self, progress_cb=None, log_fn=None):
        return self._read(0x80000, FLASH_SIZE, progress_cb)

    def read_tune(self, progress_cb=None, log_fn=None):
        profile = self.ecu.profile
        return self._read(0x80000 + profile.calibration_offset, profile.partial_size, progress_cb)

    def _lower_baud(self, log_fn=None):
        return False

    def _reset(self):
        raise DS2Error("Hardware BSL requires a manual ignition cycle; no automatic reset was sent")


def _connect(request):
    session = HardwareBSL(request)
    request.session_registry.append(session)
    session.enter()
    return session


def _check_full(image, family):
    identity = image_identity(image, family)
    if identity["partial"]:
        raise ValueError("Full recovery requires a complete 512 KiB image")
    extract_calibration(image)
    status = checksum_status(image, family)
    if any(status[group] is not True for group in ("boot", "program", "cal")):
        raise ValueError("Recovery image checksums are invalid or unavailable: " + "; ".join(status["details"]))
    program = 0x50000 if family == "MS42" else 0x6FFF0
    cal = PROFILES[family].calibration_offset + PROFILES[family].partial_size - 16
    if image[program:program + 4] != bytes.fromhex("90 48 24 42") or image[cal:cal + 4] != bytes.fromhex("42 24 48 90"):
        raise ValueError("Recovery image lacks valid program/calibration completion markers")
    return bytes(image)


def _reference(request):
    if request.family not in BAUD_RATES or request.baud not in BAUD_RATES[request.family]:
        raise ValueError("Unsupported MS42/MS43 hardware-BSL family or transfer rate")
    if request.region not in ("all", "tune"):
        raise ValueError("MS42/MS43 recovery supports full image or calibration")
    if request.force or request.no_backup:
        raise ValueError("Hardware-BSL recovery requires image validation and a durable backup")
    if request.region == "all" and not request.preserve_identity and request.fix_checksums:
        raise ValueError("Exact backup restore cannot modify checksums; select an already valid backup")
    data = bytes(request.ref_bytes) if request.ref_bytes is not None else Path(request.ref).read_bytes()
    identity = image_identity(data, request.family)
    if request.region == "all":
        if request.fix_checksums:
            data, _ = correct_checksums(data, request.family)
        data = _check_full(data, request.family)
    elif not identity["partial"]:
        data = extract_calibration(data)
    return data


def prepare_target(request, reference, live):
    """Freeze the complete intended array before the first erase, including tails."""
    if len(live) != FLASH_SIZE:
        raise ValueError("A complete raw pre-erase flash read is required")
    known = image_profile(live)
    if known is not None and known.name != request.family:
        raise ValueError("Connected flash identity disagrees with the selected ECU family")
    if request.region == "tune":
        identity = image_identity(live, request.family)
        if image_identity(reference, request.family)["software_id"] != identity["software_id"]:
            raise ValueError("Calibration software differs from the live program; use full recovery")
        status = checksum_status(live, request.family)
        if any(status[group] is not True for group in ("boot", "program")):
            raise ValueError("Calibration recovery requires valid boot/program checksums; use full recovery")
        if request.fix_checksums:
            reference, _ = correct_checksums(reference, request.family, context=live)
        profile = PROFILES[request.family]
        target = bytearray(live)
        target[profile.calibration_offset:profile.calibration_offset + profile.partial_size] = reference
    else:
        target = reference
        if request.preserve_identity:
            window = live[IDENTITY_OFFSET:IDENTITY_OFFSET + IDENTITY_SIZE]
            if window in (bytes(IDENTITY_SIZE), b"\xff" * IDENTITY_SIZE):
                raise ValueError("Live ECU identity is missing; use an explicitly selected trusted exact backup")
            target = graft_identity(target, window, request.family,
                                    pairing_pointer=live[0x250:0x254] if request.family == "MS43" else None)
            target = bytearray(target)
            target[0x4000:0x8000] = live[0x4000:0x8000]
    return _check_full(target, request.family)


def _save(path, data):
    import os

    with Path(path).open("xb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def cmd_sync(request):
    _connect(request)
    emit("Connection verified. Re-enter hardware boot mode before starting another operation.")
    return 0


def cmd_id(request):
    session = _connect(request)
    if session.flash_id not in FLASH_IDS:
        emit("Flash geometry is not admitted for writing; raw reads remain available.", level="warn")
    emit("The selected family is a board selection; the loaded agent banner does not identify the ECU.")
    return 0


def cmd_dump(request):
    if Path(request.file).exists():
        raise FileExistsError(f"Refusing to overwrite {request.file}")
    session = _connect(request)
    data = (session.read_tune(request.progress_cb) if request.partial else session.read_full(request.progress_cb))
    _save(request.file, data)
    emit(f"Saved {len(data):,} raw bytes to {request.file}. Re-enter hardware boot mode for another operation.")
    return 0


def cmd_flash(request):
    reference = _reference(request)
    if not request.arm:
        emit(f"{request.family} hardware-BSL preview: {request.region}, {len(reference):,} input bytes, "
             f"{request.baud:,} baud; full backup and readback required.")
        emit("Preserve live identity and retained data." if request.preserve_identity else
             "Exact backup restore: identity and retained flash data will be replaced.")
        if request.region == "tune":
            emit("Calibration compatibility/checksums require the live full image before erase.")
        return 0
    session = _connect(request)
    if session.flash_id not in FLASH_IDS:
        raise ValueError("Flash ID does not identify an admitted 512 KiB x16 bottom-boot device; nothing erased")
    live = session.read_full(request.progress_cb)
    target = prepare_target(request, reference, live)
    directory = Path(request.backup_dir)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "_" + uuid4().hex[:12]
    base = directory / f"bsl_{request.family}_{stamp}"
    backup, prepared = base.with_suffix(".bin"), base.with_name(base.name + "_target.bin")
    _save(backup, live)
    _save(prepared, target)
    if request.backup_cb:
        request.backup_cb(str(backup))
    emit(f"Pre-erase backup: {backup}")
    emit(f"Prepared recovery image: {prepared}")
    state = WriteState(request.family, "hardware_bsl", target_sha256=sha256(target).hexdigest())
    session.ecu.last_write = state
    session._repair_image = target
    session._repair_targets = {}
    session._repair_used = session._repairing = False
    session._verified_sector = None
    ends = (*SECTORS[1:], FLASH_SIZE)
    changed = [i for i, (start, end) in enumerate(zip(SECTORS, ends)) if live[start:end] != target[start:end]]
    # Restore executable boot last; every sector is independently read back.
    changed.sort(key=lambda i: (i in (0, 3), i == 0, i))
    with OperationJournal(base.with_suffix(".jsonl"), operation="hardware_bsl", metadata={
            "family": request.family, "port": request.port, "baud": request.baud,
            "flash_id": session.flash_id, "backup": str(backup), "prepared_image": str(prepared),
            "backup_sha256": sha256(live).hexdigest(), "target_sha256": state.target_sha256,
            "preserve_identity": request.preserve_identity, "sectors": changed}) as journal:
        state.journal_path = str(journal.path)
        try:
            if changed:
                session.write_boot = any(SECTORS[i] < 0x10000 for i in changed)
                session._confirm_identity()
                if request.destructive_cb:
                    request.destructive_cb()
            for index, sector in enumerate(changed):
                start, end = SECTORS[sector], ends[sector]
                state.phase = "erase"
                session._repair_targets[start] = bytearray(target[start:end])
                session._erase(sector, journal, state)
                if session._bytes(start, end - start) != b"\xff" * (end - start):
                    raise DS2Error(f"Erase blank check failed at 0x{start:05X}")
                state.phase = "program"
                for offset in range(start, end, BLOCK_SIZE):
                    chunk = target[offset:min(offset + BLOCK_SIZE, end)]
                    if chunk != b"\xff" * len(chunk):
                        session._program(offset, chunk, journal, state)
                state.phase = "sector readback"
                if session._bytes(start, end - start) != target[start:end]:
                    raise DS2Error(f"Recovery sector readback differs at 0x{start:05X}")
                journal.append("sector_verified", address=start, length=end - start)
                if request.progress_cb:
                    request.progress_cb(index + 1, len(changed), f"{request.family} recovery sectors verified")
            state.phase = "full readback"
            if session.read_full(request.progress_cb) != target:
                raise DS2Error("Full flash readback differs from the prepared recovery image")
            state.verified = state.completed = True
            journal.finish("success", target_sha256=state.target_sha256, ignition_cycle_required=True)
        except Exception as error:
            journal.finish("commit_unknown" if state.destructive_started else "failed",
                           phase=state.phase, address=state.address, error=str(error))
            if state.destructive_started:
                raise DS2Error(f"Recovery incomplete: {error}. Prepared image retained at {prepared}; "
                               "re-enter hardware boot mode and select Restore exact backup for that image.") from error
            raise
    emit("Full recovery readback verified. Release the boot strap, cycle ignition, then reconnect normally.")
    return 0
