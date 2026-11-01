"""MS42/MS43 serial services; importing this module never opens a transport.

Wire plans follow the audited original MS42/3 flasher, with strict validation,
bounded busy polling and no replay after an ambiguous destructive request.
"""

from dataclasses import dataclass
from contextlib import contextmanager
from pathlib import Path
from hashlib import sha256
import secrets
import time
import uuid

from ds2 import DS2Interface, DS2Error, DS2Timeout, DS2ChecksumError, DS2NegativeResponse, _xor
from operation_log import send_to_sink
from ms42_ms43_image import PROFILES, image_identity, correct_checksums
from ms42_ms43_vin import decode_aif, decode_vin as decode_vin, read_aif


READ_CHUNK = 123
WRITE_CHUNK = 118
AUTHORIZATION_READY_TIMEOUT = 15.0
BAUD_PAYLOADS = {
    9600: bytes.fromhex("00 25 80 00"),
    19200: bytes.fromhex("00 4B 00 00"),
    38400: bytes.fromhex("00 96 00 00"),
    50000: bytes.fromhex("00 C3 50 00"),
    107000: bytes.fromhex("01 86 A0 00"),
    125000: bytes.fromhex("01 E8 48 00"),
}
FLASH_RESULTS = {
    1: "verified", 2: "verification failed", 9: "hardware reference error",
    10: "program reference error", 11: "hardware/program mismatch",
    12: "incomplete program", 13: "calibration reference error",
    14: "program/calibration mismatch", 15: "incomplete calibration",
}
ADAPTATION_GROUPS = {
    "Irregular Running": (0x80, 0), "Closing Time": (0x40, 0),
    "Lambda": (4, 0), "Idle": (2, 0), "Knock": (1, 0),
    "Camshaft Edge": (0, 8), "Vanos": (0, 4), "Variant": (0, 1),
}


def _accepted(frame, command, *, statuses=(0xA0,), length=None, minimum=4, address=0x12):
    """Validate even injected transports at the protocol boundary."""
    frame = bytes(frame)
    if (len(frame) < minimum or len(frame) < 4 or frame[0] != address
            or frame[1] != len(frame) or _xor(frame)):
        raise DS2Error(f"Invalid MS42/MS43 response to 0x{command:02X}")
    if frame[2] not in statuses:
        raise DS2NegativeResponse(
            f"Command 0x{command:02X} rejected (status 0x{frame[2]:02X})",
            command=command, status=frame[2], response=frame,
        )
    if length is not None and len(frame) != length:
        raise DS2Error(f"Command 0x{command:02X}: expected {length} reply bytes, got {len(frame)}")
    return frame


def _hardware_from_directory(transport):
    frame = _accepted(transport.execute_frame(0x0D), 0x0D, minimum=61)
    pointer = int.from_bytes(frame[57:60], "big")
    transport._check_operation()
    time.sleep(0.1)
    return _accepted(transport.execute_frame(0x06, pointer.to_bytes(4, "big") + b"\x08"),
                     0x06, length=12)[3:11]


def detect_family(transport):
    """Read-only pointer-based detection on an already open 8E1 link."""
    hw = _hardware_from_directory(transport)
    return next((name for name, profile in PROFILES.items()
                 if hw[:6] == profile.hw_prefix.encode("ascii")), None)


def detect_connected(transport, ident=None):
    """Select MS42/43, or preserve a factory-shaped MS41 candidate for read-only probes.

    An unconfirmed MS41 candidate still needs the existing independent program
    and calibration checks before callers offer any destructive operation.
    """
    from ecu_info import decode_identification
    from ms41 import variant_from_program_id

    if ident is None:
        try:
            ident = transport.identify()
        except DS2Error:
            transport._check_operation()
            ident = b""
    decoded = decode_identification(ident)
    if variant_from_program_id(decoded["reported_identifier"]):
        return transport
    transport.set_stopbits(False)
    try:
        family = detect_family(transport)
    except DS2Error:
        transport._check_operation()
        family = None
    if family is None:
        if len(ident) == 42 and all(decoded.values()):
            transport.set_stopbits(True)
            transport.ms41_identity_unconfirmed = True
            return transport
        raise DS2Error("Unsupported ECU identity; no MS41-specific probes were sent")
    return MS42MS43ECU(transport, family, raw_ident=ident)


def open_ecu(port, *, serial_factory=None, operation_check=None, diagnostic_fn=None, verbose=False):
    """Application entry point; construction alone does not connect to hardware."""
    link = DS2Interface(port, serial_factory=serial_factory, verbose=verbose)
    link.operation_check = operation_check
    link.diagnostic_fn = diagnostic_fn
    try:
        link.open()
        return detect_connected(link)
    except Exception:
        link.close()
        raise


@dataclass
class WriteState:
    family: str
    operation: str
    phase: str = "validation"
    address: int | None = None
    destructive_started: bool = False
    bytes_written: int = 0
    finalized: bool = False
    verified: bool = False
    completed: bool = False
    journal_path: str | None = None
    target_sha256: str | None = None


class FlashInterrupted(DS2Error):
    def __init__(self, error, state):
        self.state = state
        self.original_error = error
        self.completion_unknown = state.destructive_started and not state.completed
        detail = (" Flash incomplete; keep ignition on and preserve the session for recovery."
                  if self.completion_unknown else " No erase or program request was sent.")
        super().__init__(f"{state.family} {state.phase}: {error}.{detail}")


class BaudTransitionError(DS2Error):
    """A failed rate handoff must not trigger an automatic fallback or write replay."""

    programming_completed = False


class AuthorizationBusyError(DS2Timeout):
    """The current authorization phase exhausted its bounded readiness wait."""

    def __init__(self, family, phase):
        self.phase = phase
        detail = (". No RAM handoff was sent. Keep ignition ON and retry the operation."
                  if phase == "RAM challenge" else "")
        super().__init__(f"{family} authorization remained busy (A1) after "
                         f"{AUTHORIZATION_READY_TIMEOUT:g} seconds ({phase}){detail}")


class ProgrammingCompletedError(DS2Error):
    """Programming succeeded but follow-up failed; never offer replay."""

    programming_completed = True

    def __init__(self, error, state):
        self.state = state
        self.original_error = error
        super().__init__(
            f"Programming completed, but follow-up failed: {error}. "
            "Do not repeat the write; perform the required ignition cycle and reconnect.")


class MS42MS43ECU:
    """Family services composed with the existing DS2 transport."""

    FULL_SIZE = 0x80000

    native_fast_capable = False

    def __init__(self, transport, family, raw_ident=b""):
        self.transport = transport
        self.family = family.upper()
        self.profile = PROFILES[self.family]
        self.PARTIAL_SIZE = self.profile.partial_size
        self.last_write = None
        self.raw_ident = bytes(raw_ident)
        self._pending_image = None
        self._pending_live = None
        self._pending_serial = None
        self._pending_full = False
        self._pending_verify = False
        self._pending_write_boot = False
        self._pending_preserve_identity = True
        self._pending_writer = None
        self._pending_vin = None  # VIN edits and virginize share one retained identity transaction.
        self._pending_storage = None  # Explicit EEPROM/retained-flash restore only.
        self._resident_signature_matches = None
        self._resident_profile = None
        self.rate_confirmed = True
        self.transfer_rate_cb = None

    def _report_transfer_rate(self, baud, source):
        if self.transfer_rate_cb:
            self.transfer_rate_cb(baud, source)

    @property
    def write_state(self):
        return self.last_write

    @property
    def port(self):
        return self.transport.port

    @property
    def is_open(self):
        return self.transport.is_open

    @property
    def baud(self):
        return self.transport.baud

    @property
    def baud_uncertain(self):
        return not self.rate_confirmed

    @property
    def can_retry_write(self):
        if self._pending_storage is not None:
            return self._pending_storage.can_retry
        if self._pending_vin is not None:
            return self._pending_vin.can_retry
        return bool(self._pending_image is not None and self.is_open
                    and getattr(self.transport, "_ser", None) is self._pending_serial
                    and (self.rate_confirmed or self._pending_writer is not None
                         and self._pending_writer.active))

    @property
    def uses_d2xx(self):
        return self.transport.uses_d2xx

    @property
    def echo(self):
        return self.transport.echo

    @property
    def operation_check(self):
        return self.transport.operation_check

    @operation_check.setter
    def operation_check(self, check):
        self.transport.operation_check = check

    @property
    def diagnostic_fn(self):
        return self.transport.diagnostic_fn

    @diagnostic_fn.setter
    def diagnostic_fn(self, callback):
        self.transport.diagnostic_fn = callback

    def close(self):
        self._resident_signature_matches = None
        self._resident_profile = None
        self._pending_writer = None
        if self._pending_vin is not None:
            self._pending_vin.active = False
        if self._pending_storage is not None:
            self._pending_storage.active = False
        self.transport.close()

    def cancel_pending_io(self):
        self.transport.cancel_pending_io()

    def _check_operation(self):
        self.transport._check_operation()

    def _check_rate(self):
        if not self.rate_confirmed:
            raise BaudTransitionError("Serial rate is unconfirmed; reconnect before sending another command")

    def _frame(self, command, args=b"", *, timeout=None, statuses=(0xA0,), length=None, minimum=4):
        if self._pending_storage is not None:
            raise DS2Error("An incomplete storage restore owns this connection; retry it first")
        if self._pending_vin is not None:
            raise DS2Error("An incomplete identity write owns this connection; retry it first")
        self._check_operation()
        self._check_rate()
        frame = self.transport.execute_frame(command, args, timeout=timeout)
        self._check_operation()
        return _accepted(frame, command, statuses=statuses, length=length, minimum=minimum)

    def execute(self, command, args=b"", timeout=None):
        return self._frame(command, args, timeout=timeout)[3:-1]

    def send_frame(self, frame, resp_addr, timeout=None):
        self._check_rate()
        return self.transport.send_frame(frame, resp_addr, timeout, strict_echo=True)

    def send_bmw_fast(self, body, target, timeout=None):
        self._check_rate()
        return self.transport.send_bmw_fast(body, target, timeout)

    def _identity_fields(self):
        shift = 0x80000 if self.family == "MS43" else 0
        hardware = self.read_memory(self.profile.hw_offset + shift, 8).decode("ascii")
        software = self.read_memory(self.profile.sw_offset + shift, 6).decode("ascii")
        if not hardware.startswith(self.profile.hw_prefix):
            raise DS2Error(f"Connected hardware identity does not match {self.family}")
        if not all(char.isascii() and char.isalnum() for char in hardware + software):
            raise DS2Error("Invalid hardware/software identity characters")
        return {"family": self.family, "hardware_id": hardware, "software_id": software}

    def identify(self):
        info = self._identity_fields()
        info["raw_ident"] = self.raw_ident
        pointer, raw = read_aif(self)
        info.update(aif_address=pointer, aif_raw=raw)
        try:
            history = decode_aif(raw)
            info.update(vin=history["vin"], native_vin=history["native_vin"],
                        aif_warnings=history["aif_warnings"])
        except ValueError as error:
            info.update(vin=None, vin_error=str(error))
        return info

    def read_memory(self, address, length, progress_cb=None, log_fn=None):
        maximum = 0xF0000 if self.family == "MS43" else 0x1000000
        if (not isinstance(address, int) or not isinstance(length, int)
                or address < 0 or length < 0 or address + length > maximum):
            raise ValueError("Read is outside the family address range")
        result = bytearray()
        while len(result) < length:
            count = min(READ_CHUNK, length - len(result))
            args = (address + len(result)).to_bytes(4, "big") + bytes((count,))
            for attempt in range(5):
                try:
                    reply = self._frame(0x06, args, length=count + 4)
                    break
                except (DS2Timeout, DS2ChecksumError):
                    self._check_operation()
                    if attempt == 4:
                        raise
                    time.sleep(0.05)
            result.extend(reply[3:-1])
            if progress_cb:
                progress_cb(len(result), length, f"{self.family} read")
        return bytes(result)

    read_mem = read_memory
    read_memory_range = read_memory

    def read_full(self, progress_cb=None, log_fn=None):
        image = bytearray(b"\xff" * self.FULL_SIZE)
        total = sum(length for _ecu, _file, length in self.profile.read_regions)
        done = 0
        for address, offset, length in self.profile.read_regions:
            callback = (lambda n, _total, label, base=done: progress_cb(base + n, total, label)
                        ) if progress_cb else None
            image[offset:offset + length] = self.read_memory(address, length, callback)
            done += length
        if log_fn:
            send_to_sink(log_fn, "Logical full read complete; reserved gaps and ECU-masked bytes are FF.")
        return bytes(image)

    def read_tune(self, progress_cb=None, log_fn=None):
        return self.read_memory(self.profile.calibration_offset, self.profile.partial_size,
                                progress_cb, log_fn)

    read_partial = read_tune

    def set_baud(self, baud, delay=0.1):
        if baud not in BAUD_PAYLOADS:
            raise ValueError("Unsupported MS42/MS43 baud setting")
        self._check_operation()
        self._check_rate()
        if baud == self.baud:
            self._report_transfer_rate(baud, "DS2 connection")
            return
        self.rate_confirmed = False
        self._report_transfer_rate(0, "transition")
        try:
            _accepted(self.transport.execute_frame(0x91, BAUD_PAYLOADS[baud]), 0x91)
            self._check_operation()
            time.sleep(delay)
            self.transport._checked_io(setattr, self.transport._ser, "baudrate", baud)
            self.transport.baud = baud
            self.rate_confirmed = True
            self._report_transfer_rate(baud, "DS2 connection")
        except DS2NegativeResponse as error:
            if error.status in (0xA2, 0xB0):
                self.rate_confirmed = True
            self._report_transfer_rate(self.baud if self.rate_confirmed else 0,
                                       "DS2 connection" if self.rate_confirmed else "unconfirmed")
            raise BaudTransitionError(f"ECU rejected serial rate {baud}; no automatic fallback was attempted") from error
        except Exception as error:
            self._report_transfer_rate(0, "unconfirmed")
            raise BaudTransitionError(
                f"Serial rate change to {baud} is unconfirmed; reconnect before further commands") from error

    @contextmanager
    def read_transfer(self, force_slow=False, log_fn=None, *, use_agent=False):
        """Use the temporary resident service only for standalone ROM reads."""
        if self._pending_image is not None or self._pending_vin is not None or self._pending_storage is not None:
            raise DS2Error("An incomplete write owns this connection; retry that write first")
        target = (125000 if not force_slow and self.transport.native_fast_capable else 9600)
        self.set_baud(target)
        if use_agent:
            from ms42_ms43_agent import select_reader
        else:
            from ms42_ms43_resident import select_reader
        reader = None if force_slow else select_reader(self, log_fn)
        if reader is None:
            yield self
            self.set_baud(9600)
            return
        try:
            try:
                reader.enter(log_fn)
            except Exception as error:
                if not reader._fallback_to_ds2(error, log_fn):
                    raise
                reader = None
                self.set_baud(target)
            if reader is None:
                yield self
                self.set_baud(9600)
            else:
                yield reader
                reader.finish(log_fn)
        except Exception as error:
            if reader is None:
                raise
            if isinstance(error, AuthorizationBusyError) and error.phase == "RAM challenge":
                # No challenge was consumed; repeating it cannot be mistaken for a key.
                raise
            self.rate_confirmed = False
            self._report_transfer_rate(0, "unconfirmed")
            raise DS2Error(
                f"RAM read stopped: {error}. Turn ignition off, wait 10 seconds, "
                "then turn it on and reconnect before another ECU operation.") from error

    @contextmanager
    def transfer(self, force_slow=False):
        """Use the original application's default rate; restore only after success."""
        target = (125000 if not force_slow and getattr(self.transport, "native_fast_capable", False)
                  else 9600)
        previous_write = self.last_write
        if self._pending_storage is not None:
            self._check_operation()
            if not self._pending_storage.can_retry:
                raise DS2Error("Storage recovery requires the original open RAM-agent connection")
        elif self._pending_vin is not None:
            self._check_operation()
            if not self._pending_vin.can_retry:
                raise DS2Error("Identity recovery requires the original open RAM-agent connection")
        elif self._pending_image is None:
            self.set_baud(target)
        else:
            self._check_operation()
            if self._pending_writer is None or not self._pending_writer.active:
                self._check_rate()
        yield self
        try:
            self.set_baud(9600)
        except BaudTransitionError as error:
            if self.last_write is not previous_write and self.last_write and self.last_write.completed:
                error.programming_completed = True
                error.args = ("Programming completed, but restoring 9600 baud failed. "
                              "Do not repeat the write; perform the required ignition cycle and reconnect.",)
            raise

    def _authorization_frame(self, payload, *, log_fn=None, phase="authorization", readiness_address=None,
                             retry_initial_rejection=False):
        # Stock startup needs roughly 9-10 seconds. A1 consumes no request;
        # retain this phase's payload and never retry a lost or malformed reply.
        deadline = None
        # Both families clear prior BMW authorization with A2 on the first FC request.
        reenter = retry_initial_rejection or phase == "RAM challenge" and payload == b"\xFC\x00\x00\x00"
        if log_fn:
            send_to_sink(log_fn, f"{self.family} {phase}: requesting ECU authorization.")
        while deadline is None or time.monotonic() < deadline:
            response = self._frame(0x90, payload, statuses=(0xA0, 0xA1, 0xA2) if reenter else (0xA0, 0xA1))
            if response[2] == 0xA0:
                if deadline is not None and log_fn:
                    send_to_sink(log_fn, f"{self.family} {phase}: ECU authorization is ready.")
                return response
            if len(response) != 4:
                raise DS2Error("Unlock readiness response must contain 4 bytes")
            if response[2] == 0xA2:
                reenter = False
                deadline = time.monotonic() + AUTHORIZATION_READY_TIMEOUT
                if log_fn:
                    send_to_sink(log_fn, f"{self.family} {phase}: retrying initial service authorization after A2.")
            elif deadline is None:
                deadline = time.monotonic() + AUTHORIZATION_READY_TIMEOUT
                if log_fn:
                    send_to_sink(log_fn, f"{self.family} {phase}: waiting for ECU readiness (A1); keep ignition ON.")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            self._check_operation()
            time.sleep(min(0.2, remaining))
            if readiness_address is not None:
                # Poll the admitted firmware's one-byte state, not its stateful challenge handler.
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    self._check_operation()
                    try:
                        ready = self._frame(0x06, readiness_address.to_bytes(4, "big") + b"\x01",
                                            timeout=min(0.5, remaining), length=5)
                    except DS2Timeout:
                        ready = None
                    if ready is not None and ready[3] == 4:
                        break
                    time.sleep(min(0.2, max(0, deadline - time.monotonic())))
        raise AuthorizationBusyError(self.family, phase)

    def unlock(self, *, readiness_address=None, retry_initial_rejection=False):
        challenge = secrets.randbelow(23) + 1
        frame = self._authorization_frame(b"BMW" + bytes((challenge,)), readiness_address=readiness_address,
                                          retry_initial_rejection=retry_initial_rejection)
        if len(frame) == 46:
            key = bytes((frame[(challenge + i) % frame[1]] + frame[18 + i] + frame[41 + i]) & 0xFF
                        for i in range(4))
            frame = self._authorization_frame(key, readiness_address=readiness_address)
        elif len(frame) != 5:
            raise DS2Error("Unlock response must contain 5 or 46 bytes")
        if frame[3:-1] != b"\x00":
            raise DS2Error("Unlock was not acknowledged: expected one zero payload byte")

    def _flash(self, operation, address, data=b"", *, checked=True, busy_timeout=60.0):
        args = bytes((operation,)) + address.to_bytes(3, "big") + bytes((len(data),)) + data
        deadline = time.monotonic() + busy_timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise DS2Timeout("MS42/MS43 flash finalization remained busy")
            frame = self._frame(0x07, args, timeout=min(60.0, remaining),
                                statuses=(0xA0, 0xA1) if operation == 0x0F else (0xA0,))
            if frame[2] != 0xA1:
                break
            self._check_operation()
            time.sleep(min(0.05, max(0, deadline - time.monotonic())))
        if checked:
            if len(frame) < 10:
                raise DS2Error("Flash verification reply is too short")
            if frame[8] != 1:
                raise DS2Error(FLASH_RESULTS.get(frame[8], f"Unknown flash result 0x{frame[8]:02X}"))

    def _write_region(self, image, address, offset, length, progress_cb, base, total, journal, state):
        end = offset + length
        cursor = offset
        while cursor < end:
            self._check_operation()
            # Only skip complete erased words; retain an odd final byte in the transfer plan.
            if cursor + 1 < end and image[cursor:cursor + 2] == b"\xff\xff":
                cursor += 2
                continue
            count = min(WRITE_CHUNK, end - cursor)
            while count > 2 and image[cursor + count - 2:cursor + count] == b"\xff\xff":
                count -= 2
            state.address = address + cursor - offset
            journal.append("program_requested", address=state.address, length=count)
            self._flash(2, state.address, image[cursor:cursor + count])
            state.bytes_written += count
            journal.append("program_accepted", address=state.address, length=count)
            cursor += count
            if progress_cb:
                progress_cb(base + cursor - offset, total, f"{self.family} write")
        if progress_cb:
            progress_cb(base + length, total, f"{self.family} write")

    def _write(self, data, *, full, progress_cb, log_fn, verify, checksum_context,
               correct_checksum, journal, recovering=False, use_ram=False,
               use_agent=False, write_boot=False, preserve_identity=True):
        if type(preserve_identity) is not bool:
            raise ValueError("preserve_identity must be True or False")
        if self._pending_storage is not None:
            raise DS2Error("An incomplete storage restore owns this connection; retry it first")
        if self._pending_vin is not None:
            raise DS2Error("An incomplete identity write owns this connection; retry it first")
        if write_boot and not (full and (recovering or (use_ram and use_agent))):
            raise ValueError("Boot updates require a full image and the custom RAM agent")
        if self._pending_image is not None and not recovering:
            raise DS2Error("An incomplete write is retained; explicitly retry the same image")
        image = bytes(data)
        if full and len(image) != self.FULL_SIZE:
            raise ValueError("Program and calibration writing requires a 512 KiB full image")
        if not full and len(image) == self.FULL_SIZE:
            start = self.profile.calibration_offset
            image = image[start:start + self.profile.partial_size]
        identity = image_identity(image, self.family)
        if full:
            start = self.profile.calibration_offset
            calibration_identity = image_identity(image[start:start + self.profile.partial_size], self.family)
            if calibration_identity["software_id"] != identity["software_id"]:
                raise ValueError("Image program and calibration software IDs do not match")
        if recovering:
            if self._pending_writer is None or not self._pending_writer.active:
                self._check_rate()
            if getattr(self.transport, "_ser", None) is not self._pending_serial:
                raise DS2Error("Recovery requires the original retained connection")
            # Program identifiers may have been erased. Bind deliberate recovery
            # to this open transport and the previously validated immutable image.
            live = self._pending_live
        else:
            live = self._identity_fields()
        if identity["partial"]:
            if identity["hardware_id"][:6] != live["hardware_id"][:6]:
                raise ValueError("Calibration hardware family does not match the connected ECU")
        elif identity["hardware_id"] != live["hardware_id"]:
            raise ValueError("Image hardware ID does not match the connected ECU")
        if not full and identity["software_id"] != live["software_id"]:
            raise ValueError("Calibration software ID does not match the connected program")
        if correct_checksum:
            if self.family == "MS43" and identity["partial"] and checksum_context is None:
                checksum_context = self.read_memory(0xEFDB8, 40)
            image, changes = correct_checksums(image, self.family, context=checksum_context)
            if log_fn:
                for change in changes:
                    send_to_sink(log_fn, change)
        elif log_fn:
            send_to_sink(log_fn, "Image checksum correction was disabled by the caller.", "warning")
        calibration_source = 0 if identity["partial"] else self.profile.calibration_offset
        regions = list(self.profile.program_write_regions) if full else []
        calibration = (self.profile.calibration_offset, calibration_source,
                       self.profile.calibration_write_length)
        regions.append(calibration)
        writer = self._pending_writer if recovering else None
        if use_ram and not recovering:
            if use_agent:
                from ms42_ms43_agent import select_writer
            else:
                from ms42_ms43_resident import select_writer
            writer = select_writer(self, log_fn, identity=live)
        if write_boot:
            if writer is None:
                raise ValueError("Boot updates require a supported custom RAM agent; no DS2 write was started")
            from ms42_ms43_agent import validate_boot_image
            validate_boot_image(image, self.family)
            writer.write_boot = True
            writer.preserve_identity = preserve_identity
        target_hash = sha256(image).hexdigest()
        if journal is None:
            from app_paths import mutable_path
            from ds2_fast_safety import OperationJournal
            journal = OperationJournal(
                Path(mutable_path("backups", "ms42_ms43", "journals")) / f"{uuid.uuid4()}.jsonl",
                operation=f"{self.family.lower()}_{'full' if full else 'tune'}_write",
                metadata={"family": self.family, "verify": bool(verify),
                          "route": getattr(writer, "route", "resident") if writer is not None else "ds2",
                          "write_boot": bool(write_boot),
                          "preserve_identity": bool(write_boot and preserve_identity),
                          "target_sha256": target_hash, "image_length": len(image)},
            )
        state = WriteState(self.family, "full" if full else "tune",
                           journal_path=str(journal.path) if hasattr(journal, "path") else None,
                           target_sha256=target_hash)
        state.destructive_started = recovering
        self.last_write = state
        self._pending_image = image
        self._pending_live = dict(live)
        self._pending_serial = getattr(self.transport, "_ser", None)
        self._pending_full = full
        self._pending_verify = verify
        self._pending_write_boot = write_boot
        self._pending_preserve_identity = preserve_identity
        self._pending_writer = writer
        total = sum(region[2] for region in regions)
        try:
            stock_fallback = False
            if writer is not None:
                state.phase = "RAM handoff"
                while True:
                    try:
                        if not writer.active:
                            state.phase = "RAM handoff"
                        writer.run(self._pending_image, regions, full=full, progress_cb=progress_cb, log_fn=log_fn,
                                   verify=verify, journal=journal, state=state)
                        break
                    except Exception as error:
                        if (not state.destructive_started and state.phase == "RAM handoff"
                                and not writer.active and not recovering and not write_boot
                                and writer._fallback_to_ds2(error, log_fn)):
                            journal.append("route_fallback", route="ds2", error=str(error))
                            writer = self._pending_writer = None
                            stock_fallback = True
                            self.set_baud(125000 if self.transport.native_fast_capable else 9600)
                            break
                        if (state.destructive_started or getattr(writer, "route", None) != "ram_agent"
                                or not writer.active):
                            raise
                        from ms42_ms43_agent import recoverable_reply
                        if not recoverable_reply(error) or not writer._lower_baud(log_fn):
                            raise
                        journal.append("baud_fallback", baud=writer.profile["baud"], error=str(error))
            if writer is None:
                state.phase = "unlock"
                journal.append("unlock_requested")
                if stock_fallback:
                    # Completed FC authorization first rejects BMW and resets readiness.
                    self.unlock(retry_initial_rejection=True,
                                readiness_address=self._resident_profile["readiness_address"])
                else:
                    self.unlock()
                prepare = regions[0][0]
                state.phase = "prepare"
                journal.append("prepare_requested", address=prepare)
                self._flash(0x0F, prepare, checked=False)
                base = 0
                for index, (address, offset, length) in enumerate(regions):
                    is_calibration = (address, offset, length) == calibration
                    state.phase = "erase calibration" if is_calibration else "erase program"
                    # MS42's second program span belongs to the first program erase.
                    if index == 0 or is_calibration:
                        self._check_operation()
                        state.address = address
                        journal.append("erase_requested", address=address)
                        if self.diagnostic_fn:
                            self.diagnostic_fn("erase_requested", {"address": address, "family": self.family})
                        self._check_operation()
                        state.destructive_started = True
                        self._flash(6, address)
                        journal.append("erase_accepted", address=address)
                    state.phase = "program calibration" if is_calibration else "program"
                    self._write_region(image, address, offset, length, progress_cb, base, total, journal, state)
                    base += length
                    if is_calibration or index == len(regions) - 2:
                        state.phase = "finalize calibration" if is_calibration else "finalize program"
                        finalize_address = address if is_calibration else regions[0][0]
                        journal.append("finalize_requested", address=finalize_address)
                        self._flash(0x0F, finalize_address, checked=is_calibration)
                        journal.append("finalize_accepted", address=finalize_address)
            state.finalized = True
            if verify and writer is None:
                state.phase = "readback verification"
                done = 0
                for address, offset, length in regions:
                    callback = (lambda n, _total, _label, base=done: progress_cb(
                        base + n, total, f"{self.family} verify")) if progress_cb else None
                    actual = self.read_memory(address, length, callback)
                    expected = image[offset:offset + length]
                    if actual != expected:
                        mismatch = next(i for i, pair in enumerate(zip(actual, expected)) if pair[0] != pair[1])
                        state.address = address + mismatch
                        raise DS2Error(f"Readback differs at ECU address 0x{state.address:06X}")
                    done += length
                state.verified = True
            state.phase = "complete"
            state.completed = True
            self._pending_image = None
            self._pending_writer = None
            journal.finish("success", verified=state.verified, bytes_written=state.bytes_written)
            if writer is not None and progress_cb:
                progress_cb(total, total, f"{self.family} write complete")
            if log_fn:
                send_to_sink(log_fn, "Write complete. Switch ignition off, wait 10 seconds, then switch it on.")
        except Exception as error:
            if writer is not None and not writer.active and not self.rate_confirmed:
                try:
                    self._report_transfer_rate(0, "unconfirmed")
                except Exception:
                    # A failed display update must not replace the operation error.
                    pass
            if state.completed:
                completed = ProgrammingCompletedError(error, state)
                if not getattr(journal, "closed", False):
                    try:
                        journal.finish("failed", programming_completed=True, phase=state.phase,
                                       error=str(error), verified=state.verified,
                                       bytes_written=state.bytes_written)
                    except Exception as journal_error:
                        completed.args = (f"{completed} Completion journal also failed: {journal_error}.",)
                raise completed from error
            if not getattr(journal, "closed", False):
                try:
                    journal.finish("commit_unknown" if state.destructive_started else "aborted",
                                   phase=state.phase, address=state.address, error_type=type(error).__name__,
                                   error=str(error), command=getattr(error, "command", None),
                                   status=getattr(error, "status", None))
                except Exception as journal_error:
                    error = DS2Error(f"{error}; journal finalization also failed: {journal_error}")
            if not state.destructive_started:
                self._pending_image = None
                self._pending_writer = None
            raise FlashInterrupted(error, state) from error
        return state

    def write_full(self, data, progress_cb=None, log_fn=None, *, verify=False,
                   checksum_context=None, correct_checksum=True, journal=None, use_ram=False,
                   use_agent=False, write_boot=False, preserve_identity=True):
        return self._write(data, full=True, progress_cb=progress_cb, log_fn=log_fn, verify=verify,
                           checksum_context=checksum_context, correct_checksum=correct_checksum,
                           journal=journal, use_ram=use_ram, use_agent=use_agent, write_boot=write_boot,
                           preserve_identity=preserve_identity)

    def write_tune(self, data, progress_cb=None, log_fn=None, *, verify=False,
                   checksum_context=None, correct_checksum=True, journal=None, use_ram=False,
                   use_agent=False):
        return self._write(data, full=False, progress_cb=progress_cb, log_fn=log_fn, verify=verify,
                           checksum_context=checksum_context, correct_checksum=correct_checksum,
                           journal=journal, use_ram=use_ram, use_agent=use_agent)

    write_partial = write_tune

    def retry_write(self, progress_cb=None, log_fn=None, *, verify=None, journal=None):
        """Explicitly restart the retained target after a failed destructive operation."""
        if self._pending_storage is not None:
            from ms42_ms43_storage import retry_storage
            return retry_storage(self, progress_cb=progress_cb, log_fn=log_fn, journal=journal)
        if self._pending_vin is not None:
            from ms42_ms43_vin_write import retry_vin
            return retry_vin(self, progress_cb, log_fn, journal=journal)
        if self._pending_image is None or not self.is_open:
            raise DS2Error("No incomplete write is retained on this open connection")
        return self._write(self._pending_image, full=self._pending_full, progress_cb=progress_cb,
                           log_fn=log_fn, verify=self._pending_verify if verify is None else verify,
                           checksum_context=None, correct_checksum=False, journal=journal, recovering=True,
                           write_boot=self._pending_write_boot, preserve_identity=self._pending_preserve_identity)

    def read_supply_voltages(self):
        """BMW supply measurements from one group read; MS43 battery is after the main relay."""
        index, scale = (43, 0.10156) if self.family == "MS43" else (23, 0.10196078)
        frame = self._frame(0x0B, b"\x03", minimum=index + 2)
        values = {"battery": frame[index] * scale}
        if self.family == "MS43":
            values["ignition"] = frame[25] * scale
        return values

    def read_voltage(self):
        return self.read_supply_voltages()["battery"]

    read_battery_voltage = read_voltage

    def read_dtcs(self):
        records = []
        for start in range(1, 256, 10):
            frame = self._frame(4, bytes((start,)), minimum=5)
            if (len(frame) - 5) % 10:
                raise DS2Error("DTC response contains an incomplete ten-byte record")
            for offset in range(4, len(frame) - 1, 10):
                raw = frame[offset:offset + 10]
                records.append({"code": raw[0], "status": raw[1], "raw": raw,
                                "fields": tuple(int.from_bytes(raw[n:n + 2], "little") for n in (2, 4, 6, 8))})
            if frame[3] < start + 10:
                return records
        raise DS2Error("DTC pagination exceeded the one-byte index range")

    def clear_dtcs(self):
        self._frame(5)

    clear_dtc = clear_dtcs

    @property
    def adaptation_groups(self):
        groups = dict(ADAPTATION_GROUPS)
        groups["Throttle" if self.family == "MS43" else "Height Correction"] = (
            8 if self.family == "MS43" else 16, 0)
        return groups

    def clear_adaptations(self, groups):
        selected = list(groups)
        if not selected:
            raise ValueError("Select at least one adaptation group")
        first = second = 0
        for name in selected:
            if name not in self.adaptation_groups:
                raise ValueError(f"Unknown {self.family} adaptation group: {name}")
            a, b = self.adaptation_groups[name]
            first |= a
            second |= b
        self._frame(0x43, bytes((first, second)))

    def _ews_prepare(self):
        request = bytes.fromhex("44 04 67 27")
        _accepted(self.send_frame(request, 0x44), 0x67, address=0x44)

    def sync_ews(self):
        frame = self._frame(0x6D, statuses=(0xA0, 0xA2))
        if frame[2] == 0xA2:
            self._ews_prepare()
            frame = self._frame(0x6D)
        return self._ews_result(frame, {0: "Synchronized", 1: "ECU/EWS mismatch",
                                       2: "ECU cannot process the start value"})

    def reset_ews_start_value(self):
        self._ews_prepare()
        frame = self._frame(0x6C, b"\x01", minimum=5)
        if frame[3] == 2:
            frame = self._frame(0x6C, b"\x00", minimum=5)
        return self._ews_result(frame, {0: "Ready to receive a new start value", 1: "Start value received",
                                       2: "Start value not received", 3: "Stored start value is corrupt"})

    @staticmethod
    def _ews_result(frame, results):
        if len(frame) < 5 or frame[3] not in results:
            raise DS2Error("Unexpected EWS alignment result")
        return {"code": frame[3], "message": results[frame[3]]}
