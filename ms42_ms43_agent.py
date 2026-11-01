"""Bulk C167 RAM transfers entered through the existing stock resident service."""

from hashlib import sha256
import time

from checksum import _crc
from ds2 import DS2ChecksumError, DS2Error, DS2Timeout
from ms42_ms43_resident import (
    PROFILES, BOOT_PROFILES, SUPPORTED_SOFTWARE, ResidentReader, ResidentWriter, _select_service, selected_profile,
)
from operation_log import send_to_sink


IDENTITY, READ, PROGRAM, ERASE, RESET, FINALIZE = map(ord, "IRPEXF")
BLOCK_SIZE = 1024
PACKET_ATTEMPTS = 3
FLASH_SIZE = 0x80000
SECTORS = (0, 0x4000, 0x6000, 0x8000, *range(0x10000, FLASH_SIZE, 0x10000))
STATUS = {1: "packet CRC mismatch", 2: "invalid or protected range",
          3: "receive timeout", 4: "flash verification failed",
          5: "unknown command", 6: "serial error",
          7: "boot checksum failed", 8: "program checksum failed",
          9: "calibration checksum failed", 10: "boot reference invalid",
          11: "program reference invalid", 12: "hardware/program mismatch",
          13: "calibration reference invalid", 14: "program/calibration mismatch",
          15: "completion marker verification failed", 16: "invalid checksum descriptor",
          17: "EEPROM did not become ready", 18: "EEPROM word verification failed",
          19: "EEPROM serial interface error"}


def validate_boot_image(image, family):
    from ms42_ms43_image import checksum_status, image_identity

    if len(image) != FLASH_SIZE:
        raise ValueError("Boot updates require a complete 512 KiB image")
    image_identity(image, family)
    if checksum_status(image, family)["boot"] is not True:
        raise ValueError("Boot image checksum is invalid")
    start = int.from_bytes(image[0x3C28:0x3C2C], "little")
    end = int.from_bytes(image[0x3C2C:0x3C30], "little")
    if start < 0x8000 and end >= 0x4000:
        raise ValueError("Boot checksum descriptor overlaps retained ECU data")
    profile = PROFILES[family]
    signature = b"".join(image[address:address + length] for address, length in profile["signature_spans"])
    if sha256(signature).hexdigest() not in {p["fingerprint"] for p in BOOT_PROFILES[family]}:
        raise ValueError("Replacement boot/service entry differs from the supported firmware interface")


def select_reader(ecu, log_fn=None, *, identity=None):
    return _select_service(ecu, log_fn, AgentReader, "read", identity=identity)


def select_writer(ecu, log_fn=None, *, identity=None):
    return _select_service(ecu, log_fn, AgentWriter, "write", identity=identity)


class AgentRejected(DS2Error):
    def __init__(self, command, status):
        self.command, self.status = command, status
        super().__init__(f"RAM agent {chr(command)}: {STATUS.get(status, f'error {status}')} ({status:#04x})")


class AgentStreamError(DS2Error):
    """The request may have arrived, but its wire acknowledgement is uncertain."""


def recoverable_reply(error):
    return (isinstance(error, (DS2Timeout, DS2ChecksumError, AgentStreamError))
            or isinstance(error, AgentRejected) and error.status in (1, 3, 6))


class AgentReader(ResidentReader):
    read_source = "ECU RAM agent read"
    route = "ram_agent"
    write_boot = False

    def _agent_payload(self):
        from engines.ms42_ms43.payload import payload
        return payload(self.ecu.family)

    def enter(self, log_fn=None):
        from engines.ms42_ms43.payload import UPLOAD_READY, UPLOAD_REJECTED, VERIFY_PREFIX_SIZE

        code = bytes(self._agent_payload())
        if not VERIFY_PREFIX_SIZE <= len(code) <= 0x800 or len(code) & 1:
            raise ValueError("RAM agent must fit the aligned 2 KiB native upload window")
        ResidentReader.enter(self, log_fn)
        for offset in range(0, len(code), 250):
            self._entry_protocol = None
            self._exchange(0x78, offset.to_bytes(2, "big") + code[offset:offset + 250])
            self._entry_protocol = "resident"
        shadow = 0xF602 if self.ecu.family == "MS42" else 0xF902
        self._entry_protocol = None
        prefix = ResidentReader._read(self, shadow, VERIFY_PREFIX_SIZE)
        self._entry_protocol = "resident"
        if prefix != code[:VERIFY_PREFIX_SIZE]:
            raise DS2Error("RAM agent verifier readback differs; agent was not executed")
        # The new command loop does not return through the native 79 dispatcher.
        self._entry_protocol = None
        self._exchange(0x79, response=False)
        # Wait for local CRC verification before transmitting the first request.
        ready = self.transport._read_exact(1, 2.0)
        if ready == bytes((UPLOAD_REJECTED,)):
            raise DS2Error("RAM agent upload CRC failed; agent body was not started")
        if not ready:
            raise DS2Timeout("RAM agent upload verification did not respond")
        if ready != bytes((UPLOAD_READY,)):
            raise DS2Error("Unexpected RAM agent upload verification response")
        self._confirm_identity()
        if log_fn:
            send_to_sink(log_fn, f"{self.ecu.family} RAM agent ready: 1 KiB transfers at {self.profile['baud']:,} baud.")

    def _confirm_identity(self):
        result = self._read_packet(int(self.write_boot), 4, command=IDENTITY)
        if result != (b"BS42" if self.ecu.family == "MS42" else b"BS43"):
            raise DS2Error("Unexpected RAM agent identity")

    def _validate_transfer(self, command, offset, count, data):
        if command not in (IDENTITY, READ, PROGRAM, ERASE, RESET, FINALIZE):
            raise ValueError("Unknown RAM agent command")
        if type(offset) is not int or type(count) is not int:
            raise ValueError("RAM agent address and count must be integers")
        if command in (READ, PROGRAM):
            if offset < 0 or offset & 1 or count & 1 or not 2 <= count <= BLOCK_SIZE or offset + count > FLASH_SIZE:
                raise ValueError("RAM agent range must be aligned and inside the 512 KiB flash")
        elif command == IDENTITY and (offset not in (0, 1) or count != 4):
            raise ValueError("Invalid RAM agent identity request")
        elif command == ERASE and (offset not in SECTORS or count):
            raise ValueError("Invalid RAM agent erase sector")
        elif command in (RESET, FINALIZE) and (offset or count):
            raise ValueError("Invalid RAM agent reset/finalize request")
        if command in (PROGRAM, ERASE) and offset < 0x10000 and not self.write_boot:
            raise ValueError("Boot writing was not selected")
        if (command == PROGRAM and len(data) != count) or (command != PROGRAM and data):
            raise ValueError("RAM agent payload length does not match the command")

    def _transfer(self, command, offset, count, data=b""):
        data = bytes(data)
        self._validate_transfer(command, offset, count, data)
        header = bytes((command,)) + offset.to_bytes(3, "big") + count.to_bytes(2, "big")
        message = header + data
        message += _crc(message, 0xFFFF).to_bytes(2, "big")
        link = self.transport
        link._check_operation()
        link._checked_io(link._ser.reset_input_buffer)
        if link._checked_io(link._ser.write, message) != len(message):
            raise AgentStreamError("Incomplete RAM agent request")
        link._flush_output()
        # A blocking exact echo read already waits for TX; no per-packet sleep.
        if link.echo and link._read_exact(len(message), 2.0 + len(message) * 10 / link.baud) != message:
            raise AgentStreamError("RAM agent K-line echo mismatch")
        response = link._read_exact(7, 60.0 if command in (ERASE, FINALIZE) else 8.0)
        if len(response) != 7:
            raise DS2Timeout("Incomplete RAM agent response header")
        if response[:6] != header:
            raise AgentStreamError("RAM agent response does not match the requested command and range")
        status = response[6]
        length = count if status == 0 and command in (READ, IDENTITY) else 0
        tail = link._read_exact(length + 2, 2.0)
        if len(tail) != length + 2:
            raise DS2Timeout("Incomplete RAM agent response")
        if _crc(response + tail[:-2], 0xFFFF) != int.from_bytes(tail[-2:], "big"):
            raise DS2ChecksumError("RAM agent response CRC mismatch")
        if status:
            raise AgentRejected(command, status)
        link._check_operation()
        return tail[:-2]

    def _drain_until_quiet(self):
        # Native rx/rx_abort allow 500 T1 ticks for an incomplete frame, then
        # another 500 quiet ticks. Never send a retry into that abandoned frame.
        link, serial = self.transport, self.transport._ser
        previous = serial.timeout
        deadline = time.monotonic() + 3.0
        quiet_since = time.monotonic()
        try:
            link._checked_io(setattr, serial, "timeout", 0.05)
            while time.monotonic() < deadline:
                if link._checked_io(serial.read, BLOCK_SIZE + 16):
                    quiet_since = time.monotonic()
                elif time.monotonic() - quiet_since >= 1.1:
                    return
                time.sleep(0.01)
            raise DS2Timeout("RAM agent stream did not become quiet; automatic retry stopped")
        finally:
            serial.timeout = previous

    def _read_packet(self, offset, count, *, command=READ):
        for attempt in range(PACKET_ATTEMPTS):
            try:
                return self._transfer(command, offset, count)
            except DS2Error as error:
                if not recoverable_reply(error) or attempt + 1 == PACKET_ATTEMPTS:
                    raise
                self._drain_until_quiet()

    def _read(self, address, length, progress_cb=None):
        if type(address) is not int or type(length) is not int:
            raise ValueError("RAM agent address and length must be integers")
        offset = address - 0x80000
        if (offset < 0
                or length < 0 or offset & 1 or length & 1 or offset + length > FLASH_SIZE):
            raise ValueError("RAM agent reads require an aligned flash range")
        result = bytearray()
        while len(result) < length:
            count = min(BLOCK_SIZE, length - len(result))
            result.extend(self._read_packet(offset + len(result), count))
            if progress_cb:
                progress_cb(len(result), length, f"{self.ecu.family} RAM read")
        return bytes(result)

    def _reset(self):
        self._transfer(RESET, 0, 0)
        self._restore_diagnostics()

    def _lower_baud(self, log_fn=None):
        previous = self.profile["baud"]
        state = getattr(self.ecu, "last_write", None)
        if (previous == 9600 or self.operation == "write" and state is not None
                and state.destructive_started and not state.completed):
            return False
        self._drain_until_quiet()
        self.active = False
        self.ecu.rate_confirmed = False
        self._reset()
        if self.ecu._identity_fields() != self.identity:
            self.ecu.rate_confirmed = False
            self.ecu._report_transfer_rate(0, "unconfirmed")
            raise DS2Error("ECU identity changed during RAM recovery; baud fallback stopped")
        self.profile = selected_profile(self.ecu.family, baud=125000 if previous > 125000 else 9600,
                                        profile=self.profile)
        if log_fn:
            send_to_sink(log_fn, f"RAM transfer: retrying at {self.profile['baud']:,} baud after confirmed recovery.")
        return True

    def _read_operation(self, address, length, progress_cb, log_fn):
        while True:
            try:
                return self._read(address, length, progress_cb)
            except DS2Error as error:
                if not recoverable_reply(error) or not self._lower_baud(log_fn):
                    raise
                self.enter(log_fn)

    def read_full(self, progress_cb=None, log_fn=None):
        return self._read_operation(0x80000, FLASH_SIZE, progress_cb, log_fn)

    def read_tune(self, progress_cb=None, log_fn=None):
        profile = self.ecu.profile
        return self._read_operation(0x80000 + profile.calibration_offset, profile.partial_size, progress_cb, log_fn)


class AgentWriter(AgentReader, ResidentWriter):
    operation = "write"
    chunk_size = BLOCK_SIZE

    def run(self, image, regions, **kwargs):
        from ms42_ms43_image import image_identity

        # Select from the immutable target before any destructive operation.
        self.target_software = image_identity(image, self.ecu.family)["software_id"]
        self.ram_finalize = self.target_software in SUPPORTED_SOFTWARE[self.ecu.family]
        return super().run(image, regions, **kwargs)

    def _prepare_image(self, image, regions, journal, state, log_fn):
        if self.write_boot and getattr(self, "preserve_identity", False):
            from ms42_ms43_identity import IDENTITY_OFFSET, IDENTITY_SIZE, PAIRING_POINTERS, graft_identity

            original = image
            image = getattr(self, "_grafted_image", None)
            if image is None:
                state.phase = "preserve boot identity"
                live = self._bytes(IDENTITY_OFFSET, IDENTITY_SIZE)
                pointer = self._bytes(PAIRING_POINTERS[self.ecu.family][0], 4) if self.ecu.family == "MS43" else None
                image = graft_identity(original, live, self.ecu.family, pairing_pointer=pointer)
                validate_boot_image(image, self.ecu.family)
                digest = sha256(image).hexdigest()
                journal.append("identity_grafted", source_offset=IDENTITY_OFFSET, source_data=live.hex(),
                               pairing_pointer=pointer.hex() if pointer is not None else None,
                               input_target_sha256=sha256(original).hexdigest(), target_sha256=digest)
                self._grafted_image = image
                self.ecu._pending_image = image
                state.target_sha256 = digest
                if log_fn:
                    send_to_sink(log_fn, "Live VIN/AIF, ISN/pairing and ECU identifiers preserved in the boot image.")
        # Only a normal image write owns complete intended sectors. Standalone
        # identity transactions retain their own backup/recovery contracts.
        self._repair_image, self._repair_regions = bytes(image), tuple(regions)
        self._repair_targets = {}
        self._repair_used = self._repairing = False
        self._verified_sector = None
        return image

    def _confirm_retained(self):
        self._confirm_identity()

    def _program(self, offset, data, journal, state):
        data = bytes(data)
        if (offset < (0 if self.write_boot else 0x10000) or offset & 1
                or offset + len(data) > FLASH_SIZE or len(data) & 1
                or not 2 <= len(data) <= BLOCK_SIZE):
            raise ValueError("RAM agent program range is invalid or boot writing was not selected")
        state.address = offset
        journal.append("program_requested", address=offset, length=len(data))
        self.transport._check_operation()
        state.destructive_started = True
        targets = getattr(self, "_repair_targets", {})
        for start, target in targets.items():
            lo, hi = max(start, offset), min(start + len(target), offset + len(data))
            if lo < hi:
                target[lo - start:hi - start] = data[lo - offset:hi - offset]
        verified = getattr(self, "_verified_sector", None)
        already_verified = (verified is not None and offset >= verified[0]
                            and verified[1][offset - verified[0]:offset - verified[0] + len(data)] == data)
        if not already_verified:
            for attempt in range(PACKET_ATTEMPTS):
                try:
                    self._transfer(PROGRAM, offset, len(data), data)
                    break
                except DS2Error as error:
                    if not hasattr(self, "_repair_targets") or not recoverable_reply(error):
                        raise
                    self._drain_until_quiet()
                    actual = self._bytes(offset, len(data))
                    if actual == data:
                        journal.append("program_confirmed_by_readback", address=offset, length=len(data))
                        break
                    mismatch = next((i for i, (wanted, old) in enumerate(zip(data, actual))
                                     if wanted & ~old), None)
                    if mismatch is not None:
                        self._repair_sector(offset + mismatch, journal, state)
                        actual = self._bytes(offset, len(data))
                        if actual == data:
                            break
                        if any(wanted & ~old for wanted, old in zip(data, actual)):
                            raise DS2Error("RAM program still needs erase after sector recovery; keep the ECU powered for retry")
                    if attempt + 1 == PACKET_ATTEMPTS:
                        raise error
                    journal.append("program_retry", address=offset, length=len(data), attempt=attempt + 2)
        if not getattr(self, "_repairing", False):
            state.bytes_written += len(data)
        verified = getattr(self, "_verified_sector", None)
        if verified is not None:
            start, contents = verified
            lo, hi = max(start, offset), min(start + len(contents), offset + len(data))
            if lo < hi:
                updated = bytearray(contents)
                updated[lo - start:hi - start] = data[lo - offset:hi - offset]
                self._verified_sector = start, bytes(updated)
        journal.append("program_accepted", address=offset, length=len(data))

    def _erase(self, sector, journal, state):
        if type(sector) is not int or not 0 <= sector < len(SECTORS):
            raise ValueError("Invalid RAM agent erase sector")
        offset = SECTORS[sector]
        if offset < 0x10000 and not self.write_boot:
            raise ValueError("Boot writing was not selected")
        state.address = offset
        journal.append("erase_requested", address=offset)
        if self.ecu.diagnostic_fn:
            self.ecu.diagnostic_fn("erase_requested", {"address": offset, "family": self.ecu.family})
        self.transport._check_operation()
        state.destructive_started = True
        end = SECTORS[sector + 1] if sector + 1 < len(SECTORS) else FLASH_SIZE
        for attempt in range(PACKET_ATTEMPTS):
            try:
                self._transfer(ERASE, offset, 0)
                break
            except DS2Error as error:
                if not hasattr(self, "_repair_targets") or not recoverable_reply(error):
                    raise
                self._drain_until_quiet()
                if self._bytes(offset, end - offset) == b"\xff" * (end - offset):
                    journal.append("erase_confirmed_by_readback", address=offset)
                    break
                if attempt + 1 == PACKET_ATTEMPTS:
                    raise
                journal.append("erase_retry", address=offset, attempt=attempt + 2)
        journal.append("erase_accepted", address=offset)
        if hasattr(self, "_repair_targets"):
            self._verified_sector = None
            if self.write_boot and sector in (0, 3):
                self._repair_targets[offset] = bytearray(self._repair_image[offset:end])
            elif sector == self.cal_sector or (len(self._repair_regions) > 1 and sector in self.program_sectors):
                # The selected plan deliberately leaves omitted tails erased.
                target = bytearray(b"\xff" * (end - offset))
                for index, (_address, source, length) in enumerate(self._repair_regions):
                    dest = self.ecu.profile.calibration_offset if index == len(self._repair_regions) - 1 else source
                    lo, hi = max(offset, dest), min(end, dest + length)
                    if lo < hi:
                        target[lo - offset:hi - offset] = self._repair_image[source + lo - dest:source + hi - dest]
                self._repair_targets[offset] = target

    def _repair_sector(self, address, journal, state):
        targets = getattr(self, "_repair_targets", {})
        start = next((start for start, target in targets.items() if start <= address < start + len(target)), None)
        if start is None or getattr(self, "_repair_used", False):
            raise DS2Error("RAM program readback needs erase; no complete sector repair remains. Keep the ECU powered for retry")
        expected = bytes(targets[start])
        self._repair_used = True
        journal.append("sector_repair_requested", address=start, length=len(expected))
        self._erase(SECTORS.index(start), journal, state)
        self._repair_targets[start] = bytearray(expected)
        self._repairing = True
        try:
            for cursor in range(0, len(expected), BLOCK_SIZE):
                chunk = expected[cursor:cursor + BLOCK_SIZE]
                if chunk != b"\xff" * len(chunk):
                    self._program(start + cursor, chunk, journal, state)
        finally:
            self._repairing = False
        if self._bytes(start, len(expected)) != expected:
            raise DS2Error("RAM sector recovery readback differs; keep the ECU powered for retry")
        self._verified_sector = start, expected
        journal.append("sector_repair_verified", address=start, length=len(expected))

    def _finish_write(self, image, regions, journal, state, log_fn, progress_cb):
        if self.write_boot:
            self.ecu._resident_signature_matches = None
            self.ecu._resident_profile = None
            state.phase = "write boot"
            # Each sector is verified while the complete agent remains in RAM.
            # 4000..7FFF contains retained data (native MS42 adaptation journals).
            for sector in (3, 0):
                start = SECTORS[sector]
                end = SECTORS[sector + 1]
                expected = image[start:end]
                if self._read(0x80000 + start, end - start) == expected:
                    continue
                self._erase(sector, journal, state)
                for offset in range(start, end, BLOCK_SIZE):
                    chunk = image[offset:min(offset + BLOCK_SIZE, end)]
                    if chunk != b"\xff" * len(chunk):
                        self._program(offset, chunk, journal, state)
                if self._read(0x80000 + start, end - start) != expected:
                    state.address = start
                    raise DS2Error(f"Boot readback differs in sector {sector}; keep the ECU powered for retry")
                if progress_cb:
                    progress_cb(0x8000 if sector == 3 else 0xC000, 0xC000,
                                f"{self.ecu.family} boot verified")
            journal.append("boot_verified", sectors=[3, 0], length=0xC000)
        if not self.ram_finalize:
            return super()._finish_write(image, regions, journal, state, log_fn, progress_cb)
        self._write_phase("RAM finalization", "checking and finalizing firmware", journal, state, log_fn, progress_cb)
        journal.append("finalize_requested", route=self.route)
        self._transfer(FINALIZE, 0, 0)
        self._write_phase("completion markers", "checking completion markers", journal, state, log_fn, progress_cb)
        for offset, expected in ((self.program_marker, bytes.fromhex("90 48 24 42")),
                                 (self.cal_marker, bytes.fromhex("42 24 48 90"))):
            if self._bytes(offset, 4) != expected:
                raise DS2Error(f"RAM agent did not finalize the marker at flash offset 0x{offset:06X}")
        # Verified flash completion must not become a write retry if reset fails.
        state.finalized = state.completed = True
        self.ecu._pending_image = self.ecu._pending_writer = None
        journal.append("finalize_accepted", route=self.route)
        self._write_phase("restore diagnostics", "restoring diagnostics", journal, state, log_fn, progress_cb)
        # A lost reset reply leaves the active protocol uncertain, as before.
        self.active = False
        self._reset()
        if self.ecu._identity_fields() != dict(self.identity, software_id=self.target_software):
            self.ecu.rate_confirmed = False
            raise DS2Error("ECU identity changed after the RAM write")
        state.phase = "complete"
        if log_fn:
            send_to_sink(log_fn, "RAM write validated and finalized by the RAM agent.")
