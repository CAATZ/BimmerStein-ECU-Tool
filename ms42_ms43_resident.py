"""Temporary MS42/MS43 resident-service transfers using the stock RAM overlays.

The stock service uses length/address/command/XOR frames in 8N1. Its length
excludes the XOR byte, unlike DS2. Command 71 returns high-byte-first words.
"""

from hashlib import sha256
import time

import transfer_rates
from checksum import _crc
from ds2 import DS2Error, DS2Timeout, DS2ChecksumError, DS2NegativeResponse, _xor
from operation_log import send_to_sink


# Identify the supported entry/overlay ABI; this is not whole-ROM integrity.
# Mutable flash markers are excluded. Runtime authorization/readback still apply.
SUPPORTED_SOFTWARE = {
    "MS42": ("0110C6", "0110C7", "0110CA"),
    "MS43": ("430037", "430055", "430064", "430065", "430066", "430069", "43X001"),
}
PROFILES = {
    "MS42": dict(challenge=bytes.fromhex("92a2be1d"),
                 key=bytes.fromhex("a2ef7ed2"), software="0110C6", ram=0xC000, jump=bytes.fromhex("FA 00 44 80"),
                 baud=166667, divisor_request=16667, identity=b"6577225403--", readiness_address=0xF736,
                 signature_spans=((0x8044, 48), (0xB800, 16), (0xB900, 56)),
                 fingerprint="31bdfd566337e48abf108fd216463dd2a43e51ec6754edf21613a30ed39f8749"),
    "MS43": dict(challenge=bytes.fromhex("b4774dc0"),
                 key=bytes.fromhex("54237db2"), software="430069", ram=0xF980, jump=bytes.fromhex("FA 00 04 80"),
                 baud=187500, divisor_request=2366, identity=b"6577355402--", readiness_address=0xF6B0,
                 signature_spans=((0x20E8, 40), (0x8004, 4), (0xBF10, 64)),
                 fingerprint="c00a2f14337e12973c9cefe34670ac8ed2bbea81c94ea502aad61475b0bebc4b"),
}

# Program updates can retain an earlier boot/service image. Identify that ABI
# from the same small live spans, independently of the application software ID.
BOOT_PROFILES = {
    "MS42": (PROFILES["MS42"],),
    "MS43": (PROFILES["MS43"], dict(
        PROFILES["MS43"], software="430055", identity=b"6577355403--",
        challenge=bytes.fromhex("fc8a1739"), key=bytes.fromhex("a0038220"),
        fingerprint="513073aa3deabe281207ec700de80ddc6bf85025867b864247e9c84d5eb6033c")),
}


def selected_profile(family, *, baud=None, profile=None):
    profile = dict(PROFILES[family] if profile is None else profile)
    if baud is None:
        baud = (125000 if transfer_rates.get_high_baud() == transfer_rates.COMPATIBILITY_HIGH_BAUD
                else profile["baud"])
    if baud not in (profile["baud"], 125000, 9600):
        raise ValueError("Unsupported RAM transfer rate")
    if baud == 125000:
        profile.update(baud=125000, divisor_request=12500 if family == "MS42" else 1577)
    elif baud == 9600:
        profile.update(baud=9600, divisor_request=None)
    return profile


class _NoResidentReply(DS2Timeout):
    pass


class ResidentRejected(DS2Error):
    def __init__(self, command, status):
        self.command, self.status = command, status
        detail = " (flash manufacturer check failed)" if status == 0xD0 else ""
        super().__init__(f"Resident command 0x{command:02X} rejected (0x{status:02X}){detail}")


def select_reader(ecu, log_fn=None):
    return _select_service(ecu, log_fn, ResidentReader, "read")


def select_writer(ecu, log_fn=None, *, identity=None):
    return _select_service(ecu, log_fn, ResidentWriter, "write", identity=identity)


def _select_service(ecu, log_fn, service, operation, *, identity=None):
    if (not hasattr(ecu.transport._ser, "parity")
            or not ecu.transport.native_fast_capable and getattr(service, "route", None) != "ram_agent"):
        return None
    # Write preflight already validated this operation's live identity.
    identity = ecu._identity_fields() if identity is None else dict(identity)
    profile = PROFILES[ecu.family]
    if identity["software_id"] not in SUPPORTED_SOFTWARE[ecu.family]:
        if log_fn:
            send_to_sink(log_fn, f"This software uses the existing stock DS2 {operation} route.")
        return None
    if ecu._resident_signature_matches is None:
        signature = b"".join(ecu.read_memory(address, length)
                             for address, length in profile["signature_spans"])
        fingerprint = sha256(signature).hexdigest()
        matched = next((candidate for candidate in BOOT_PROFILES[ecu.family]
                        if candidate["fingerprint"] == fingerprint), None)
        ecu._resident_profile = matched
        ecu._resident_signature_matches = matched is not None
    if not ecu._resident_signature_matches:
        if log_fn:
            send_to_sink(log_fn, f"Boot/service code differs; using the existing stock DS2 {operation} route.")
        return None
    return service(ecu, identity)


class ResidentReader:
    read_source = "ECU RAM loader read"
    commands = (0x01, 0x02, 0x71, 0x78, 0x79)
    operation = "read"

    def __init__(self, ecu, identity=None):
        self.ecu = ecu
        self.transport = ecu.transport
        # Keep the ECU divisor and host rate together through retained retries.
        baud = (9600 if getattr(self, "route", None) == "ram_agent"
                and not self.transport.native_fast_capable else None)
        self.profile = selected_profile(ecu.family, baud=baud,
                                        profile=getattr(ecu, "_resident_profile", None))
        self.identity = identity
        self._entry_protocol = None

    def enter(self, log_fn=None):
        ecu, profile = self.ecu, self.profile
        self._entry_protocol = None
        ecu.set_baud(9600)
        # FC retains the authorization handler until its key; finish measurements first.
        record = self._ram_authorization_record()
        self._entry_protocol = None
        try:
            challenge = ecu._authorization_frame(
                b"\xFC\x00\x00\x00", log_fn=log_fn, phase="RAM challenge",
                readiness_address=profile["readiness_address"])
        except DS2Error as error:
            from ms42_ms43 import AuthorizationBusyError
            if (isinstance(error, AuthorizationBusyError) and error.phase == "RAM challenge"
                    or isinstance(error, DS2NegativeResponse) and error.command == 0x90
                    and error.status == 0xA2 and len(error.response) == 4):
                self._entry_protocol = "ds2"
            raise
        data = challenge[3:-1]
        # Repeating the key after an already-authorized ACK revokes authorization.
        if data != b"\x00":
            if record is not None:
                if data != record[:4]:
                    raise DS2Error("RAM-entry authorization record does not match the ECU challenge")
                key = record[4:]
            elif data == profile["challenge"]:
                key = profile["key"]
            else:
                received = data.hex(" ").upper() or "empty"
                expected = profile["challenge"].hex(" ").upper()
                raise DS2Error(f"Unexpected RAM authorization challenge (received {received}; expected {expected})")
            accepted = ecu._authorization_frame(key, log_fn=log_fn, phase="RAM key",
                                                readiness_address=profile["readiness_address"])
            if accepted[3:-1] != b"\x00":
                raise DS2Error("RAM authorization was not acknowledged")
        self._entry_protocol = "ds2"
        start = profile["ram"] + 20
        payload = profile["jump"]
        header = (_crc(payload, 0x544D).to_bytes(2, "little") + bytes(2)
                  + start.to_bytes(4, "little")
                  + (start + len(payload) - 1).to_bytes(4, "little")
                  + ecu.profile.hw_prefix.encode("ascii") + b"MT")
        bridge = header + payload
        self._entry_protocol = None
        try:
            ecu._flash(0, profile["ram"], bridge)
        except DS2NegativeResponse as error:
            if len(error.response) == 4:
                self._entry_protocol = "ds2"
            raise
        self._entry_protocol = "ds2"
        # The native upload checks each stored byte. Verify its complete image
        # independently before the first execution request as well.
        if ecu.read_memory(profile["ram"], len(bridge)) != bridge:
            raise DS2Error("Temporary RAM handoff failed readback verification")
        ecu.rate_confirmed = False
        ecu._report_transfer_rate(0, "transition")
        self._entry_protocol = None
        self.transport.send_no_response(0x53, b"\xFC")
        self._host_format(9600, "N")
        time.sleep(0.1)
        # No execution ACK exists. Retry only this harmless startup probe;
        # an early request can arrive before the resident UART is initialized.
        for attempt in range(3):
            try:
                identity = self._exchange(0x02, count=12)
                break
            except _NoResidentReply:
                if attempt == 2:
                    raise
                time.sleep(0.05)
        if identity != profile["identity"]:
            raise DS2Error("Unexpected resident-service identity")
        self._entry_protocol = "resident"
        if profile["baud"] != 9600:
            self._entry_protocol = None
            self._exchange(0x01, b"\x00" + profile["divisor_request"].to_bytes(2, "big") + bytes(2))
            self._host_format(profile["baud"], "N")
            if self._exchange(0x02, count=12) != profile["identity"]:
                raise DS2Error("Resident baud change was not confirmed")
            self._entry_protocol = "resident"
        ecu._report_transfer_rate(profile["baud"], "RAM transfer")
        if log_fn:
            send_to_sink(log_fn, f"Temporary RAM handoff: {ecu.family} {self.operation}s at {profile['baud']:,} baud.")

    def _ram_authorization_record(self):
        # Matching service code can retain different factory authorization data.
        setup = b"\x01\x08" + b"".join(
            b"\x00" + address.to_bytes(4, "big") for address in range(0x3F5C, 0x3F64))
        cleanup = True
        try:
            try:
                reply = self.ecu.execute(0x0B, setup)
            except DS2NegativeResponse as error:
                # The boot-only dispatcher returns FF without changing session state.
                if error.command == 0x0B and error.status == 0xFF and len(error.response) == 4:
                    cleanup = False
                    self._entry_protocol = "ds2"
                    return None
                raise
            if reply:
                raise DS2Error("RAM-entry measurement setup was not acknowledged")
            record = self.ecu.execute(0x0B, b"\x00")
        finally:
            if cleanup:
                if self.ecu.execute(0x0B, b"\xFF"):
                    raise DS2Error("RAM-entry measurement cleanup was not acknowledged")
                self._entry_protocol = "ds2"
        if len(record) != 8:
            raise DS2Error("RAM-entry authorization record must contain eight bytes")
        return record

    def _fallback_to_ds2(self, error, log_fn=None):
        """Recover a failed entry only while its command dispatcher is known."""
        if not isinstance(error, DS2Error) or self._entry_protocol not in ("ds2", "resident"):
            return False
        self.transport._check_operation()
        self.ecu.rate_confirmed = False
        try:
            if self._entry_protocol == "resident":
                ResidentReader._reset(self)
            else:
                self._restore_diagnostics()
            if self.ecu._identity_fields() != self.identity:
                raise DS2Error("ECU identity changed during stock DS2 recovery")
        except Exception as recovery_error:
            self.ecu.rate_confirmed = False
            raise DS2Error(f"{error}; stock DS2 recovery failed: {recovery_error}") from recovery_error
        if log_fn:
            send_to_sink(log_fn, f"RAM entry failed: {error}. Using stock DS2 fallback.", "warning")
        return True

    def finish(self, log_fn=None):
        self._reset()
        if self.ecu._identity_fields() != self.identity:
            self.ecu.rate_confirmed = False
            self.ecu._report_transfer_rate(0, "unconfirmed")
            raise DS2Error("ECU identity changed after the RAM read")
        if log_fn:
            send_to_sink(log_fn, "RAM read complete; normal diagnostics restored at 9,600 baud.")

    def _reset(self):
        # 78 stages four SRST bytes in RAM; 79 restores and executes them.
        # No flash erase, program, boot marker or persistent patch is used.
        self._exchange(0x78, bytes.fromhex("00 00 B7 48 B7 B7"))
        staged = 0xF602 if self.ecu.family == "MS42" else 0xF902
        if ResidentReader._read(self, staged, 4) != bytes.fromhex("B7 48 B7 B7"):
            raise DS2Error("RAM reset payload failed readback verification")
        self._entry_protocol = None
        self._exchange(0x79, response=False)
        self._restore_diagnostics()

    def _restore_diagnostics(self):
        self.ecu._report_transfer_rate(0, "transition")
        self._host_format(9600, "E")
        from ms42_ms43 import _accepted
        deadline = time.monotonic() + 5.0
        while True:
            self.transport._check_operation()
            time.sleep(0.2)
            try:
                _accepted(self.transport.execute_frame(0x00, timeout=0.5), 0x00, length=46)
                break
            except DS2Timeout:
                if time.monotonic() >= deadline:
                    raise DS2Timeout("Normal diagnostics did not return after the RAM reset")
        self.ecu.rate_confirmed = True
        self._entry_protocol = "ds2"
        self.ecu._report_transfer_rate(9600, "DS2 connection")

    def read_full(self, progress_cb=None, log_fn=None):
        return self._read(0x80000, 0x80000, progress_cb)

    def read_tune(self, progress_cb=None, log_fn=None):
        profile = self.ecu.profile
        return self._read(0x80000 + profile.calibration_offset, profile.partial_size, progress_cb)

    def _exchange(self, command, args=b"", *, count=0, response=True, timeout=2.0):
        # Command 75 erases the resident sector on entry. Never use it here.
        if command not in self.commands:
            raise ValueError(f"Command is not part of the resident {self.operation} workflow")
        if len(args) > 252:
            raise ValueError("Resident request is too long")
        link = self.transport
        link._check_operation()
        message = bytes((len(args) + 3, 1, command)) + bytes(args)
        message += bytes((_xor(message),))
        link._checked_io(link._ser.reset_input_buffer)
        written = link._checked_io(link._ser.write, message)
        if written != len(message):
            raise DS2Error("Incomplete resident-service request")
        link._flush_output()
        if link.echo and link._discard_echo(message) != message:
            raise DS2Error("Resident-service K-line echo mismatch")
        if not response:
            return b""
        header = link._read_exact(1, timeout)
        if not header:
            raise _NoResidentReply(f"No resident-service reply to 0x{command:02X}")
        if header[0] < 3:
            raise DS2Error("Invalid resident-service response length")
        reply = header + link._read_exact(header[0], 0.5)
        if len(reply) != header[0] + 1:
            raise DS2Timeout("Incomplete resident-service response")
        if _xor(reply):
            raise DS2ChecksumError("Invalid resident-service response XOR")
        if reply[1] != 1:
            raise DS2Error("Unexpected resident-service response address")
        if reply[2] != 0xA0:
            raise ResidentRejected(command, reply[2])
        if len(reply) != count + 4:
            raise DS2Error("Unexpected resident-service response size")
        link._check_operation()
        return reply[3:-1]

    def _host_format(self, baud, parity):
        link = self.transport
        link._checked_io(setattr, link._ser, "parity", parity)
        link._checked_io(setattr, link._ser, "baudrate", baud)
        link.baud = baud
        link._BITS_PER_CHAR = 10 if parity == "N" else 11

    def _read(self, address, length, progress_cb=None):
        if (type(address) is not int or type(length) is not int or address < 0
                or length < 0 or address & 1 or length & 1
                or address + length > 0x1000000):
            raise ValueError("Resident reads require an aligned, bounded byte range")
        result = bytearray()
        while len(result) < length:
            count = min(252, length - len(result))
            args = (address + len(result)).to_bytes(3, "big") + (count // 2).to_bytes(2, "big")
            words = self._exchange(0x71, args, count=count)
            chunk = bytearray(count)
            chunk[0::2], chunk[1::2] = words[1::2], words[0::2]
            result.extend(chunk)
            if progress_cb:
                progress_cb(len(result), length, f"{self.ecu.family} fast read")
        return bytes(result)


class ResidentWriter(ResidentReader):
    """Stock outer erase/program overlays; DS2 retains final validation ownership."""

    operation = "write"
    chunk_size = 248

    def __init__(self, ecu, identity=None):
        super().__init__(ecu, identity)
        self.program_command = 0x74 if ecu.family == "MS42" else 0x7A
        self.commands = (*self.commands, 0x73, self.program_command)
        self.active = False
        self.metadata = None
        self._write_overlay = None
        if ecu.family == "MS42":
            self.program_sectors = (8, 9, 10, 4, 5, 6)
            self.cal_sector = 7
            self.program_marker, self.cal_marker = 0x50000, 0x4FFF0
            self.primary, self.secondary = 0x4FFD6, 0x7FFE6
            self.pointers = (0x502D6, 0x502DE, 0x502E2)
        else:
            self.program_sectors = (8, 9, 4, 5, 6, 7)
            self.cal_sector = 10
            self.program_marker, self.cal_marker = 0x6FFF0, 0x7FFF0
            self.primary, self.secondary = 0x7FFD0, 0x4FFE6
            self.pointers = (0x6ED46, 0x6ED3A, 0x6ED3E)

    def _exchange(self, command, *args, **kwargs):
        # The native dispatcher replaces its cached overlay on command changes.
        if command != self._write_overlay:
            self._write_overlay = None
        return super()._exchange(command, *args, **kwargs)

    def _prepare_command(self, command, journal, state):
        if self.ecu.family != "MS43" or self._write_overlay == command:
            return
        from ms43_resident_writer import bootstrap

        phase = state.phase
        state.phase = "prepare RAM flash access"
        try:
            code = bootstrap(command, software=self.profile["software"])
            for offset in range(0, len(code), 250):
                self._exchange(0x78, (0x400 + offset).to_bytes(2, "big") + code[offset:offset + 250])
            jump = bytes.fromhex("EA 00 00 E4")
            self._exchange(0x78, bytes(2) + jump)
            if self._read(0xE400, len(code)) != code or self._read(0xF902, 4) != jump:
                raise DS2Error("Temporary RAM writer failed readback verification")
            identifiers = self._exchange(0x79, count=4)
            manufacturer = int.from_bytes(identifiers[:2], "little")
            device = int.from_bytes(identifiers[2:], "little")
            journal.append("resident_flash_identified", command=command,
                           manufacturer=manufacturer, device=device)
            # As in stock DS2, the admitted ECU profile owns the flash contract;
            # chip IDs are diagnostic data, not a manufacturer allowlist.
            self._write_overlay = command
        except Exception as error:
            # Before the first flash request, a failed setup can safely return
            # to diagnostics; a previously interrupted write keeps its session.
            if not state.destructive_started:
                self.active = False
                try:
                    self._reset()
                except Exception as reset_error:
                    self.ecu.rate_confirmed = False
                    raise DS2Error(f"{error}; restoring diagnostics also failed: {reset_error}") from error
            raise
        state.phase = phase

    def _bytes(self, offset, length):
        start = offset & ~1
        data = self._read(0x80000 + start, ((offset + length + 1) & ~1) - start)
        return data[offset - start:offset - start + length]

    def _program(self, offset, data, journal, state):
        data = bytes(data)
        if (offset < 0x10000 or offset + len(data) > 0x80000
                or offset & 1 or not data or len(data) & 1 or len(data) > 248):
            raise ValueError("Resident program range must be aligned and preserve the bootloader")
        if self.ecu.family == "MS42":
            words = bytearray(len(data))
            words[0::2], words[1::2] = data[1::2], data[0::2]
            args = offset.to_bytes(3, "big") + words
        else:
            args = b"\x02" + offset.to_bytes(3, "big") + data
        self._prepare_command(self.program_command, journal, state)
        state.address = offset
        journal.append("program_requested", address=offset, length=len(data))
        self.transport._check_operation()
        state.destructive_started = True
        self._exchange(self.program_command, args)
        state.bytes_written += len(data)
        journal.append("program_accepted", address=offset, length=len(data))

    def _erase(self, sector, journal, state):
        if sector not in range(4, 11):
            raise ValueError("Resident erase must preserve all boot sectors")
        offset = (sector - 3) * 0x10000
        args = bytes((sector,)) if self.ecu.family == "MS42" else b"\x02" + offset.to_bytes(3, "big")
        self._prepare_command(0x73, journal, state)
        state.address = offset
        journal.append("erase_requested", address=offset)
        if self.ecu.diagnostic_fn:
            self.ecu.diagnostic_fn("erase_requested", {"address": offset, "family": self.ecu.family})
        self.transport._check_operation()
        state.destructive_started = True
        self._exchange(0x73, args, timeout=60.0)
        journal.append("erase_accepted", address=offset)

    def _invalidate(self, offset, journal, state):
        if self._bytes(offset, 4) != b"\xff" * 4:
            self._program(offset, bytes(4), journal, state)

    def _snapshot_identity(self, full):
        # Match native DS2's two guarded 26-byte backups before erasing their source.
        primary = self._bytes(self.primary, 26)
        make_primary = (full and primary == b"\xff" * 26
                        and self._bytes(self.program_marker, 4) == bytes.fromhex("90 48 24 42"))
        if make_primary:
            pieces = []
            for pointer, count in zip(self.pointers, (12, 7, 7)):
                pointer_value = int.from_bytes(self._bytes(pointer, 4), "little")
                if pointer_value == 0xFFFFFFFF:
                    pieces.append(b"\xff" * count)
                    continue
                offset = pointer_value & 0x7FFFF
                if offset + count > 0x80000:
                    raise DS2Error("Identity backup descriptor leaves the flash image")
                pieces.append(self._bytes(offset, count))
            primary = b"".join(pieces)
        cal_valid = self._bytes(self.cal_marker, 4) == bytes.fromhex("42 24 48 90")
        self.metadata = primary, make_primary, cal_valid

    def _confirm_retained(self):
        if self._exchange(0x02, count=12) != self.profile["identity"]:
            raise DS2Error("Retained resident identity changed; write was not restarted")

    def run(self, image, regions, *, full, progress_cb, log_fn, verify, journal, state):
        if self.active:
            self._confirm_retained()
        else:
            from ms42_ms43 import AuthorizationBusyError
            try:
                self.enter(log_fn)
            except Exception as error:
                if not (isinstance(error, AuthorizationBusyError) and error.phase == "RAM challenge"):
                    self.ecu.rate_confirmed = False
                raise
            self.active = True
        image = self._prepare_image(image, regions, journal, state, log_fn)
        state.phase = "preserve identity"
        if self.metadata is None:
            self._snapshot_identity(full)
        primary, make_primary, cal_valid = self.metadata
        if make_primary and self._bytes(self.primary, 26) == b"\xff" * 26:
            self._program(self.primary, primary, journal, state)
        secondary_written = False
        total = sum(length for _address, _offset, length in regions)
        base = 0
        for index, (_address, source, length) in enumerate(regions):
            calibration = index == len(regions) - 1
            offset = self.ecu.profile.calibration_offset if calibration else source
            if calibration:
                state.phase = "preserve identity"
                if cal_valid and self._bytes(self.secondary, 26) == b"\xff" * 26:
                    self._program(self.secondary, primary, journal, state)
                    secondary_written = True
                state.phase = "invalidate calibration marker"
                self._invalidate(self.cal_marker, journal, state)
                state.phase = "erase calibration"
                self._erase(self.cal_sector, journal, state)
            elif index == 0:
                state.phase = "invalidate program marker"
                self._invalidate(self.program_marker, journal, state)
                state.phase = "erase program"
                for sector in self.program_sectors:
                    self._erase(sector, journal, state)
            state.phase = "program calibration" if calibration else "program"
            cursor = 0
            while cursor < length:
                self.transport._check_operation()
                if image[source + cursor:source + cursor + 2] == b"\xff\xff" and cursor + 1 < length:
                    cursor += 2
                    continue
                count = min(self.chunk_size, length - cursor)
                while count > 2 and image[source + cursor + count - 2:source + cursor + count] == b"\xff\xff":
                    count -= 2
                data = image[source + cursor:source + cursor + count]
                self._program(offset + cursor, data + (b"\xff" if len(data) & 1 else b""), journal, state)
                cursor += count
                if progress_cb:
                    progress_cb(base + cursor, total, f"{self.ecu.family} RAM write")
            base += length
            if progress_cb:
                progress_cb(base, total, f"{self.ecu.family} RAM write")
        if verify:
            state.phase = "readback verification"
            done = 0
            for index, (_address, source, length) in enumerate(regions):
                offset = self.ecu.profile.calibration_offset if index == len(regions) - 1 else source
                expected = bytearray(image[source:source + length])
                if secondary_written and offset <= self.secondary < offset + length:
                    expected[self.secondary - offset:self.secondary - offset + 26] = primary
                callback = (lambda n, _total, _label, base=done, length=length: progress_cb(
                    base + min(n, length), total, f"{self.ecu.family} verify")) if progress_cb else None
                actual = self._read(0x80000 + offset, length + (length & 1), callback)[:length]
                if actual != expected:
                    state.address = offset + next(i for i, pair in enumerate(zip(actual, expected)) if pair[0] != pair[1])
                    raise DS2Error(f"Readback differs at flash offset 0x{state.address:06X}")
                done += length
            state.verified = True
        self._finish_write(image, regions, journal, state, log_fn, progress_cb)

    def _prepare_image(self, image, regions, journal, state, log_fn):
        return image

    def _finish_write(self, image, regions, journal, state, log_fn, progress_cb):
        from ms42_ms43_image import image_identity
        self._write_phase("restore diagnostics", "restoring diagnostics", journal, state, log_fn, progress_cb)
        # Once reset starts, a missing reply cannot prove which protocol is active.
        self.active = False
        self._reset()
        # A full write can change software; only the known image owns this RAM address.
        readiness_address = (self.profile["readiness_address"]
                             if image_identity(image, self.ecu.family)["software_id"] in SUPPORTED_SOFTWARE[self.ecu.family]
                             else None)
        self._finalize(regions, journal, state, log_fn, progress_cb, readiness_address=readiness_address)

    def _write_phase(self, phase, label, journal, state, log_fn, progress_cb):
        state.phase = phase
        journal.append("phase_started", phase=phase)
        if log_fn:
            send_to_sink(log_fn, f"{self.ecu.family}: {label}.")
        if progress_cb:
            progress_cb(0, 0, f"{self.ecu.family} {label}")

    def _finalize(self, regions, journal, state, log_fn, progress_cb=None, *, readiness_address=None):
        self._write_phase("firmware validation", "checking firmware", journal, state, log_fn, progress_cb)
        deadline = time.monotonic() + 60.0
        while True:
            frame = self.ecu._frame(0x0A, statuses=(0xA0, 0xA1))
            if frame[2] == 0xA0:
                if len(frame) != 5:
                    raise DS2Error("Unexpected firmware validation response")
                if frame[3] != 0:
                    raise DS2Error(f"ECU firmware checksum validation failed (mask 0x{frame[3]:02X})")
                break
            if len(frame) != 4:
                raise DS2Error("Unexpected firmware validation busy response")
            if time.monotonic() >= deadline:
                raise DS2Timeout("Firmware validation remained busy after the RAM write")
            time.sleep(0.1)
        self._write_phase("authorization readiness", "waiting for authorization", journal, state, log_fn, progress_cb)
        self.ecu.unlock(readiness_address=readiness_address)
        # Native calibration finalization checks and publishes both region markers.
        address = regions[-1][0]
        self._write_phase("finalize calibration", "finalizing calibration", journal, state, log_fn, progress_cb)
        journal.append("finalize_requested", address=address)
        self.ecu._flash(0x0F, address)
        self._write_phase("completion markers", "checking completion markers", journal, state, log_fn, progress_cb)
        program_address = self.program_marker + (0x80000 if self.ecu.family == "MS43" else 0)
        for marker, expected in ((program_address, bytes.fromhex("90 48 24 42")),
                                 (self.cal_marker, bytes.fromhex("42 24 48 90"))):
            if self.ecu.read_memory(marker, 4) != expected:
                raise DS2Error(f"Firmware did not finalize the marker at ECU address 0x{marker:06X}")
        state.finalized = True
        state.completed = True
        state.phase = "complete"
        self.ecu._pending_image = None
        self.ecu._pending_writer = None
        journal.append("finalize_accepted", address=address)
        if log_fn:
            send_to_sink(log_fn, "RAM write validated and finalized by the stock ECU firmware.")
