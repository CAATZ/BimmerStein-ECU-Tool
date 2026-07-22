"""
ds2.py — BMW DS2 diagnostic and flash protocol over K-Line for MS41.

Wire / serial settings:
    9600 baud, 8 data bits, EVEN parity, 2 stop bits, no flow control.
    NO 5-baud init — open the port and send commands directly.

Frame format (request and response):
    [ addr, length, command, data..., checksum ]
      addr     = ECU address (0x12 for MS41)
      length   = total frame length in bytes (incl. addr, length, cmd, checksum)
      checksum = XOR of every preceding byte in the frame

Half-duplex K-Line echoes our TX bytes, so we read and discard the echo
before reading the ECU response.  _discard_echo() handles this byte-by-byte
so it auto-detects adapters that suppress the echo in hardware.

Read commands (verified against factory-tool captures):
    0x00  IDENTIFY   — 42-byte ECU identification string
    0x04  READ_DTC   — fault codes
    0x05  CLEAR_DTC
    0x06  READ_MEM   — 4-byte big-endian address + 1-byte length (max 247 / 0xF7)
    0x0D  STATUS     — ECU operating status (73-byte response)

Write/flash commands:
    0xA2  PREPARE    — enables write mode (ECU replies with 0xFF status, not 0xA0)
    0x90  SEED_KEY   — BMW seed-key challenge/response (2-step)
    0x07  FLASH_OP   — sub-command byte selects operation:
              sub 0x0F  erase-sector poll   (send ×2 before erase)
              sub 0x06  erase sector        (single 0x4000 / 16 KB sector)
              sub 0x02  write block         (up to 231 data bytes per frame)

Full partial-write sequence (verified, byte-for-byte from capture):
    0xA2  prepare → 0x06 read_mem(0x2001,12) → 0x0D status
    → 0x90 BMW+XX → 0x90 KEY (unlock)
    → 0x06 read_mem(0x1CF4,3) → 0x06 read_mem(0x1000E,2)
    → 0x07/0x0F poll ×2 → 0x07/0x06 erase sector
    → 0x00 identify → 0x0D status
    → 0x07/0x02 write blocks (231 bytes each, skip all-0xFF chunks)
"""

import sys
import time
import logging
from enum import IntEnum

try:
    import serial
except ImportError:
    serial = None

log = logging.getLogger("ds2")


def _import_d2xx_serial():
    """Import the internal D2XX-backed serial class; the constructor loads/probes the driver."""
    from engines.softbsl.d2xx_serial import D2XXSerial
    return D2XXSerial

ECU_ADDR = 0x12


def _progress_bar(label):
    """TTY-only fallback used by the optional standalone recovery CLI.

    The desktop application supplies its own typed progress callback; redirected output
    intentionally stays quiet instead of emitting machine-readable console markers.
    """
    started = time.time()
    tty = sys.stdout.isatty()

    def update(done, total, _label=None):
        if not tty:
            return
        fraction = min(max(done / total if total else 1.0, 0.0), 1.0)
        width = 24
        filled = int(width * fraction)
        elapsed = time.time() - started
        rate = done / elapsed if elapsed > 0 else 0
        eta = (total - done) / rate if rate > 0 else 0
        sys.stdout.write(
            f"\r  {label:<11}[{'#' * filled}{'.' * (width - filled)}] "
            f"{fraction * 100:3.0f}% {done // 1024:>4}/{total // 1024 or 1} KB "
            f"{rate / 1024:4.1f} KB/s ETA {eta:3.0f}s ")
        sys.stdout.flush()

    return update

READ_TIMEOUT   = 1.5      # seconds: wait for first byte of ECU response
INTER_BYTE_TMO = 0.1      # seconds: inter-byte gap within a response

# ── Flash-operation pacing ──
# The ECU already self-paces every flash op by withholding its ACK until the
# physical operation completes — measured from the captures:
#     sector erase : 244-627 ms before ACK
#     write block  :  47- 88 ms before ACK
# Our execute() is synchronous (it blocks reading that ACK), so we physically
# cannot issue the next command until the ECU signals done — the ECU's latency
# IS our pacing, and READ_TIMEOUT (1.5 s) covers the worst-case erase.
#
# These extra delays replicate the *additional* gaps the factory tool leaves
# after each flash op (post-erase ≈ 275 ms; between writes a consistent ≈ 12 ms
# floor across 691 blocks).  They are insurance for timing-sensitive ECUs and
# apply ONLY to the write/erase path — the read path (proven 23/23 live, and
# run near-back-to-back by the factory tool too) is untouched.
INTER_WRITE_DELAY = 0.012  # seconds: between write blocks (tool floor ≈ 11-13 ms)
WRITE_RETRY_DELAY = 0.05   # seconds: settle before resending a failed write block

# ── Erase pacing (from the write-capture timestamps; see notes below) ─────────
# The erase ACK is NOT "erase complete": across captures the program-array erase
# ACKs in 79-627 ms — far too fast to have physically erased ~200 KB.  The factory
# tool then WAITS before writing, and spaces its pre-erase polls out:
#     pre-erase poll spacing : ~440-490 ms between the two 0x0F polls
#     post-TUNE-erase settle  : ~280 ms  (16 KB sector)
#     post-PROGRAM-erase settle: ~2.0-2.6 s  (whole program array)
# Tune-sector values (proven on hardware by the working partial write) stay small;
# the program-array erase gets the long spacing/settle the tool actually uses, so
# we never start writing into a not-yet-finished erase.
ERASE_STEP_DELAY          = 0.05   # between poll/poll/erase for a tune-sector erase
POST_ERASE_DELAY          = 0.30   # settle after a tune-sector erase ACK
PROGRAM_ERASE_STEP_DELAY  = 0.45   # between poll/poll/erase for the program-array erase
POST_PROGRAM_ERASE_DELAY  = 2.70   # settle after the program-array erase (> max 2.6 s seen)
ERASE_TIMEOUT             = 20.0   # max wait for an erase ACK (generous safety margin)


class DS2Commands(IntEnum):
    IDENTIFY      = 0x00
    READ_DTC      = 0x04
    CLEAR_DTC     = 0x05
    READ_MEM      = 0x06
    FLASH_OP      = 0x07
    TELEGRAM      = 0x0B
    STATUS        = 0x0D
    CLEAR_ADAPT   = 0x43
    SEED_KEY      = 0x90
    PREPARE       = 0xA2


class DS2Error(Exception):
    pass

class DS2Timeout(DS2Error):
    pass

class DS2ChecksumError(DS2Error):
    pass

class DS2NegativeResponse(DS2Error):
    """A structurally valid DS2 response rejected the active command."""

    def __init__(self, message, *, command=None, status=None, response=b""):
        super().__init__(message)
        self.command = command
        self.status = status
        self.response = bytes(response)
        self.payload = self.response[3:-1] if len(self.response) >= 4 else b""


# ECU flash-engine result code (DAT_00e528 in the firmware).  It is the last
# byte of every 0x07 write response (10-byte frame: [..., count, STATUS, XOR]).
# 0x01 = OK; anything else is a flash error.  Decoded from the MS41 firmware
# state machine (FUN_0040ee / the verify path at 0x527E).
FLASH_STATUS = {
    0x01: "OK",
    0x03: "target not blank (sector not erased / wrong region)",
    0x07: "phase mismatch (tune write while program-array armed)",
    0x08: "phase mismatch (program write while tune armed)",
    0x09: "post-write verify A failed",
    0x0A: "post-write verify B failed (calibration CRC-16 mismatch)",
    0x0B: "verify A/B comparison mismatch",
    0x0C: "program-phase completion sentinel not written",
    0x0D: "post-write verify C failed",
    0x0E: "verify B/C comparison mismatch",
    0x0F: "tune-phase completion sentinel not written",
}


def describe_flash_status(code: int) -> str:
    """Human-readable meaning of an ECU flash-engine status code."""
    return FLASH_STATUS.get(code, f"unknown flash status 0x{code:02X}")


def _xor(buf) -> int:
    c = 0
    for b in buf:
        c ^= b
    return c


def _isn_from_identify(payload: bytes) -> str:
    """The 4-digit ISN is the last 4 ASCII characters of the DS2 identification
    (cmd 0x00) payload — the same 4 digits written to the EWS during alignment."""
    return "".join(chr(b) for b in bytes(payload)[-4:])


class DS2Interface:
    """DS2 over an FTDI D2XX or pyserial K-Line transport. 9600 8E2, no init."""

    def __init__(self, port: str, ecu_addr: int = ECU_ADDR, baud: int = 9600,
                 verbose: bool = False, echo: bool = True):
        self.port     = port
        self.baud     = baud
        self.ecu_addr = ecu_addr
        self.verbose  = verbose
        self.echo     = echo
        self._ser     = None
        self.transport_name = None

    @property
    def uses_d2xx(self):
        return self.transport_name == "d2xx"

    # ── connection ───────────────────────────────────────────────────────────
    def open(self):
        import os
        # Prefer direct FTDI D2XX over the Windows VCP path because it is more
        # reliable at elevated baud rates. SOFTBSL_D2XX=0 forces pyserial.
        d2xx_pref = os.environ.get("SOFTBSL_D2XX", "1") != "0"
        self._ser = None
        self.transport_name = None
        d2xx_error = None
        if d2xx_pref:
            try:
                D2XXSerial = _import_d2xx_serial()
                self._ser = D2XXSerial(port=self.port, baudrate=self.baud,
                                       timeout=READ_TIMEOUT, write_timeout=3.0, two_stop=True)
                self.transport_name = "d2xx"
                log.debug("DS2 port %s open via D2XX (%d 8E2)", self.port, self.baud)
            except Exception as e:
                d2xx_error = e
                log.debug("D2XX transport unavailable (%s); falling back to pyserial", e)
                self._ser = None
        if self._ser is None:
            if serial is None:
                detail = f"; D2XX unavailable: {d2xx_error}" if d2xx_error else ""
                raise ImportError(
                    "no serial transport is available: install pyserial or the FTDI D2XX driver"
                    + detail)
            self._ser = serial.Serial(
                port          = self.port,
                baudrate      = self.baud,
                bytesize      = serial.EIGHTBITS,
                parity        = serial.PARITY_EVEN,
                stopbits      = serial.STOPBITS_TWO,
                timeout       = READ_TIMEOUT,
                write_timeout = 3.0,   # prevent write() blocking forever on broken adapters
                dsrdtr        = False, # disable DSR/DTR flow control — can cause OS-level hangs
                rtscts        = False, # disable RTS/CTS hardware flow control
            )
            self.transport_name = "pyserial"
            log.debug("DS2 port %s open via pyserial (%d 8E2)", self.port, self.baud)
        try:
            self._ser.setDTR(False)
            self._ser.setRTS(False)
        except Exception:
            pass

    def close(self):
        if self._ser and self._ser.is_open:
            self._ser.close()

    @property
    def is_open(self) -> bool:
        return self._ser is not None and self._ser.is_open

    # ── low-level transport ──────────────────────────────────────────────────
    def _read_exact(self, n: int, timeout: float) -> bytes:
        self._ser.timeout = timeout
        buf = bytearray()
        while len(buf) < n:
            chunk = self._ser.read(n - len(buf))
            if not chunk:
                break
            buf.extend(chunk)
        return bytes(buf)

    # 9600 8E2 = 12 bits per character → 1.25 ms/char.
    _BITS_PER_CHAR = 12    # 1 start + 8 data + 1 parity + 2 stop
    # Per-read timeout used when consuming echo bytes.  Must cover the USB-serial
    # receive latency of the adapter.  FT232 with latency timer set to 1 ms
    # (standard requirement for BMW diagnostic/flash tools) delivers bytes within
    # ~2 ms of physical arrival.  5 ms gives a comfortable margin.
    _ECHO_READ_TMO = 0.005  # 5 ms — suits FT232 @ 1 ms latency timer

    def _discard_echo(self, frame: bytes):
        """Consume echoed TX bytes from a half-duplex K-Line bus.

        Strategy:
          1. Sleep for exactly the frame TX time so all bytes have left the
             adapter and are on the wire.
          2. Read exactly len(frame) bytes with _ECHO_READ_TMO timeout.
             The timeout must cover Windows USB-serial receive latency (≤16 ms),
             after which all echo bytes should be in the software buffer.
          3. Leave the RX buffer untouched — the ECU response follows immediately
             and must not be flushed.

        This is more reliable than either byte-by-byte matching (stops on first
        mismatch, leaving residual echo bytes that corrupt the next response) or
        a buffer-flush approach (which discards the ECU response when the frame
        is long and the ACK arrives before the flush window closes).

        Verified against FT232 adapter with latency timer = 1 ms (standard for
        BMW diagnostic / flash tools).  If self.echo is False the adapter
        suppresses the echo and we skip.
        """
        if not self.echo:
            return b""
        # Wait for TX to complete on the wire before reading echo
        tx_time = len(frame) * self._BITS_PER_CHAR / self.baud
        time.sleep(tx_time)
        # Read and discard exactly len(frame) echo bytes
        return self._read_exact(len(frame), self._ECHO_READ_TMO)

    def _command_frame(self, command: int, args: bytes = b"") -> bytes:
        """Build one complete DME-addressed DS2 command frame."""
        args = bytes(args)
        length = len(args) + 4
        frame = bytes([self.ecu_addr, length, command]) + args
        return frame + bytes([_xor(frame)])

    def send_no_response(self, command: int, args: bytes = b"") -> None:
        """Transmit a complete DS2 command without waiting for an ECU response.

        This is for commands that deliberately reset the ECU and therefore cannot
        reply. The method still checks that the adapter accepted every byte and,
        on a half-duplex K-line, that the complete transmitted frame echoed back.
        """
        if not self.is_open:
            raise DS2Error("port not open")
        frame = self._command_frame(command, args)
        if self.verbose:
            log.debug("TX (no response expected) %s", frame.hex(" "))
        self._ser.reset_input_buffer()
        written = self._ser.write(frame)
        if written != len(frame):
            raise DS2Error(
                f"short write for command 0x{command:02X}: {written}/{len(frame)} bytes")
        self._ser.flush()
        echoed = self._discard_echo(frame)
        if self.echo:
            if len(echoed) != len(frame):
                raise DS2Timeout(
                    f"short K-line echo for command 0x{command:02X}: "
                    f"{len(echoed)}/{len(frame)} bytes")
            if echoed != frame:
                raise DS2Error(
                    f"K-line echo mismatch for command 0x{command:02X}")

    def execute(self, command: int, args: bytes = b"", timeout: float = None) -> bytes:
        """
        Send a DS2 command and return the response payload (data bytes only,
        addr/length/command/checksum stripped).  Raises on timeout/checksum error.

        timeout — seconds to wait for the first response byte.  Defaults to
        READ_TIMEOUT; pass a larger value for slow ops (e.g. a full program-array
        erase, which the ECU does not ACK until physically complete).
        """
        if not self.is_open:
            raise DS2Error("port not open")
        if timeout is None:
            timeout = READ_TIMEOUT
        frame = self._command_frame(command, args)
        if self.verbose:
            log.debug("TX %s", frame.hex(" "))

        self._ser.reset_input_buffer()
        self._ser.write(frame)
        self._ser.flush()
        self._discard_echo(frame)

        # Read response header: [addr, length]
        head = self._read_exact(2, timeout)
        if len(head) < 2:
            raise DS2Timeout(
                f"no response to command 0x{command:02X} (got {len(head)} byte(s))")
        if head[0] != self.ecu_addr:
            raise DS2Error(
                f"unexpected response address 0x{head[0]:02X} (expected 0x{self.ecu_addr:02X})")
        resp_len = head[1]
        if resp_len < 4:
            raise DS2Error(f"implausible response length {resp_len}")

        # Read remaining bytes of response
        rest = self._read_exact(resp_len - 2, INTER_BYTE_TMO + 0.5)
        resp = head + rest
        if len(resp) != resp_len:
            raise DS2Timeout(
                f"short response to 0x{command:02X}: expected {resp_len}, got {len(resp)}")
        if self.verbose:
            log.debug("RX %s", resp.hex(" "))
        if _xor(resp[:-1]) != resp[-1]:
            raise DS2ChecksumError(
                f"bad checksum: calc 0x{_xor(resp[:-1]):02X} != 0x{resp[-1]:02X}")

        # resp = [addr, length, status, data..., checksum]
        # status 0xA0 = positive ACK (normal commands)
        # status 0xFF = PREPARE (0xA2) positive response — documented exception
        # anything else is a NAK from the ECU
        status = resp[2]
        if status not in (0xA0, 0xFF):
            raise DS2NegativeResponse(
                f"ECU NAK on cmd 0x{command:02X}: status=0x{status:02X}  "
                f"frame={resp.hex(' ')}",
                command=command,
                status=status,
                response=resp,
            )

        return resp[3:-1]

    # ── read commands ────────────────────────────────────────────────────────
    def identify(self) -> bytes:
        return self.execute(DS2Commands.IDENTIFY)

    def read_isn(self) -> str:
        """Read the 4-digit ISN live over DS2 (identification cmd 0x00, last 4 digits)."""
        return _isn_from_identify(self.identify())

    def send_frame(self, frame: bytes, resp_addr: int, timeout: float = None) -> bytes:
        """Put a COMPLETE pre-built DS2 frame on the wire (e.g. an EWS 0x44/0x61
        write built by identity.ews_frames) and return the validated response frame
        from module `resp_addr`. Unlike execute() (DME 0x12), this targets an
        arbitrary module and returns the raw reply for the caller to interpret.

        `frame` must be a full [addr, len, cmd, data..., xor] byte sequence.
        """
        if not self.is_open:
            raise DS2Error("port not open")
        if timeout is None:
            timeout = READ_TIMEOUT
        frame = bytes(frame)
        self._ser.reset_input_buffer()
        self._ser.write(frame)
        self._ser.flush()
        self._discard_echo(frame)

        head = self._read_exact(2, timeout)
        if len(head) < 2:
            raise DS2Timeout(f"no response from module 0x{resp_addr:02X} "
                             f"(got {len(head)} byte(s))")
        if head[0] != resp_addr:
            raise DS2Error(f"unexpected response address 0x{head[0]:02X} "
                           f"(expected 0x{resp_addr:02X})")
        resp_len = head[1]
        if resp_len < 3:
            raise DS2Error(f"implausible response length {resp_len}")
        rest = self._read_exact(resp_len - 2, INTER_BYTE_TMO + 0.5)
        resp = head + rest
        if len(resp) != resp_len:
            raise DS2Timeout(f"short response from 0x{resp_addr:02X}: "
                             f"expected {resp_len}, got {len(resp)}")
        if _xor(resp[:-1]) != resp[-1]:
            raise DS2ChecksumError(f"bad checksum on module 0x{resp_addr:02X} response")
        return resp

    def read_dtc(self, specific_fault: int = 1) -> bytes:
        return self.execute(DS2Commands.READ_DTC, bytes([specific_fault & 0xFF]))

    def clear_dtc(self) -> bytes:
        return self.execute(DS2Commands.CLEAR_DTC)

    # ── DS2 0x0B/0x01 batch telegram (MS41-specific) ─────────────────────────
    #
    # Wire format verified from serial capture ("Capture Live Data Telegram.csv"):
    #
    # Setup frame (136 bytes, sent ONCE to register 24 parameters in 2 groups):
    #   [0x12][0x88][0x0B][0x01][session_id=0x1A]
    #   [grp1_hdr: 02 0E 00 00 07][20 entries × 5 bytes]
    #   [grp2_hdr: 02 0E 00 00 06][ 4 entries × 5 bytes]
    #   [XOR checksum]
    #
    # Entry format (5 bytes): [SIZE_FLAG][0x00][0x00][ADDR_HI][ADDR_LO]
    #   SIZE_FLAG 0x00 → 1-byte response | SIZE_FLAG 0x01 → 2-byte response
    #
    # Poll frame (6 bytes, repeated ~112 ms apart):
    #   [0x12][0x06][0x0B][0x00][session_id=0x1A][XOR]
    #
    # Response (42 bytes):
    #   [0x12][0x2A][0xA0]
    #   [2 bytes grp1 status][26 bytes grp1 param data]
    #   [2 bytes grp2 status][ 8 bytes grp2 param data]
    #   [XOR]
    #   → payload returned by poll: 38 bytes (data[3:-1])

    # Per-ECU-family addresses for entries that vary across MS41 variants.
    # Entries 0-4, 6-13, 18-23 are shared (same address on every MS41).
    #
    # Columns: (tps, lt_b1, lt_b2, st_b1_hi, st_b2_hi, injector_pw, engine_load)
    #   tps   — throttle position (1b)
    #   lt_b1/b2 — long-term additive fuel trim banks 1/2 (2b, unsigned LE, centre 0x8000)
    #   st_b1/b2 — short-term integrator banks 1/2 (1b = high byte of LE 16b value,
    #              addr = standard_ST_addr + 1; formula (x-128)*100/256 → ±50% range)
    #              Cross-checked: 1406464 st_b1=0xF01F = 0xF01E+1 ✓, st_b2=0xF0CB = 0xF0CA+1 ✓
    _BATCH_ECU_ADDRS = {
        # MS41.1 — E36/E39/Z3 M52
        "1437806": (0xE8D7, 0xF048, 0xF104, 0xF037, 0xF0F3, 0xEF96, 0xFC52),
        "1438068": (0xE8D7, 0xF048, 0xF104, 0xF037, 0xF0F3, 0xEF96, 0xFC52),
        # MS41.0
        "1429861": (0xE8D7, 0xED6E, 0xEDA8, 0xED5D, 0xED97, 0xECBC, 0xFAFC),
        "1432401": (0xE8D7, 0xED6E, 0xEDA8, 0xED5D, 0xED97, 0xECBC, 0xFAFC),
        "1429373": (0xE8D7, 0xED6E, 0xEDA8, 0xED5D, 0xED97, 0xECBC, 0xFAFC),
        "1438137": (0xE8D7, 0xED6E, 0xEDA8, 0xED5D, 0xED97, 0xECBC, 0xFAFC),
        # MS41.2 E36 M3 S52 / MS41.3 bench build (shares MS41.2 RAM layout)
        "1406464": (0xE8D0, 0xF030, 0xF0DC, 0xF01F, 0xF0CB, 0xEF7E, 0xFC52),
        "SHINDE1": (0xE8D0, 0xF030, 0xF0DC, 0xF01F, 0xF0CB, 0xEF7E, 0xFC52),
    }
    _BATCH_ECU_DEFAULT = (0xE8D7, 0xF048, 0xF104, 0xF037, 0xF0F3, 0xEF96, 0xFC52)

    # Session ID used in both setup and poll frames
    _BATCH_SESSION_ID = 0x1A

    @classmethod
    def _build_batch_setup(cls, ecu_id: str = "", entries=None) -> bytes:
        """Build the 132-byte default or caller-supplied batch setup payload."""
        if entries is not None:
            return cls._build_custom_batch_setup(entries)
        key = str(ecu_id)[:7].strip() if ecu_id else ""
        tps, lt1, lt2, st1, st2, inj, load = cls._BATCH_ECU_ADDRS.get(
            key, cls._BATCH_ECU_DEFAULT)
        return bytes.fromhex(
            "011A"                  # subcmd=0x01, session_id=0x1A
            "020E000007"            # group 1 header (5 bytes)
            "010000DA36"            # entry  0: 2b Idle Valve Position
            f"010000{inj:04X}"       # entry  1: 2b Injector Pulse Width
            "000000E989"            # entry  2: 1b @ 0xE989  Ignition Advance
            "000000E98D"            # entry  3: 1b @ 0xE98D  Knock Retard
            "000000DA63"            # entry  4: 1b Vehicle Speed
            f"000000{tps:04X}"       # entry  5: 1b @ tps     Throttle Position
            "010000DA2A"            # entry  6: 2b @ 0xDA2A  Engine RPM
            "010000DA34"            # entry  7: 2b @ 0xDA34  Mass Air Flow
            "000000DA5A"            # entry  8: 1b @ 0xDA5A  Coolant Temp
            "000000DA50"            # entry  9: 1b @ 0xDA50  Intake Air Temp
            "000000FC9D"            # entry 10: 1b @ 0xFC9D  Battery Voltage
            "000000E9D9"            # entry 11: 1b Global Knock Retard
            "000000E9E6"            # entry 12: 1b VANOS Advance
            "000000DA56"            # entry 13: 1b @ 0xDA56  CPV Duty Cycle
            f"010000{lt1:04X}"      # entry 14: 2b @ lt1    Fuel Trim LT B1
            f"010000{lt2:04X}"      # entry 15: 2b @ lt2    Fuel Trim LT B2
            f"000000{st1:04X}"      # entry 16: 1b @ st1    Fuel Trim ST B1
            f"000000{st2:04X}"      # entry 17: 1b @ st2    Fuel Trim ST B2
            "000000FD24"            # entry 18: 1b engine-state byte
            "000000FD14"            # entry 19: 1b operating-state byte
            "020E000006"            # group 2 header (5 bytes)
            f"010000{load:04X}"      # entry 20: 2b Engine Load
            "010000FA9A"            # entry 21: 2b Front O2 Bank 1 ADC
            "010000FA98"            # entry 22: 2b Front O2 Bank 2 ADC
            "010000FA9E"            # entry 23: 2b MAF ADC
        )

    @classmethod
    def _build_custom_batch_setup(cls, entries) -> bytes:
        """Serialize and validate an ordered 24-entry live-data profile."""
        plan = tuple(entries)
        if len(plan) != 24:
            raise ValueError(f"DS2 batch setup requires 24 entries, got {len(plan)}")

        normalized = []
        for address, length in plan:
            address = int(address)
            length = int(length)
            if length not in (1, 2):
                raise ValueError(f"unsupported DS2 batch entry length {length}")
            if not 0 <= address <= 0xFFFF:
                raise ValueError(f"DS2 batch address outside 16-bit RAM: 0x{address:X}")
            normalized.append((address, length))
        if sum(length for _address, length in normalized[:20]) != 26:
            raise ValueError("DS2 batch group 1 must return exactly 26 data bytes")
        if sum(length for _address, length in normalized[20:]) != 8:
            raise ValueError("DS2 batch group 2 must return exactly 8 data bytes")

        payload = bytearray((0x01, cls._BATCH_SESSION_ID))
        payload.extend(bytes.fromhex("020E000007"))
        for address, length in normalized[:20]:
            payload.extend((length - 1, 0x00, 0x00, address >> 8, address & 0xFF))
        payload.extend(bytes.fromhex("020E000006"))
        for address, length in normalized[20:]:
            payload.extend((length - 1, 0x00, 0x00, address >> 8, address & 0xFF))
        return bytes(payload)

    def setup_telegram_batch(self, ecu_id: str = "", entries=None) -> None:
        """Send the one-time 0x0B/0x01 setup frame to register 24 batch parameters.

        ecu_id — ECU part-number string (first 7 chars of identify() response).
                 Selects the correct RAM addresses for TPS and fuel trims.
        Must be called once before polling with poll_telegram_batch().
        The ECU responds with a 4-byte ACK (empty payload).

        ``entries`` may supply an ordered profile as ``(address, byte_length)``
        pairs. Group data lengths remain fixed at 26 and 8 bytes.
        """
        self.execute(DS2Commands.TELEGRAM, self._build_batch_setup(ecu_id, entries))

    def poll_telegram_batch(self) -> bytes:
        """Poll the registered batch parameters via cmd 0x0B/0x00.

        Returns the 38-byte data payload:
          bytes  0- 1: group 1 status (skip)
          bytes  2-27: 20 parameter values (group 1)
          bytes 28-29: group 2 status (skip)
          bytes 30-37:  4 parameter values (group 2)

        Raises DS2Error / DS2Timeout on communication failure.
        Call setup_telegram_batch() once before the poll loop.
        """
        return self.execute(DS2Commands.TELEGRAM,
                            bytes([0x00, self._BATCH_SESSION_ID]))

    # Adaptation-clear sub-byte values:
    #   sub1=0xFF sub2=0xFF → clear all adaptations
    #   sub1=0x02 sub2=0x00 → clear idle adaptation
    #   sub1=0x01 sub2=0x00 → clear knock adaptation
    #   sub1=0x04 sub2=0x00 → clear lambda/fuel trim adaptation
    #   sub1=0x08 sub2=0x00 → clear throttle adaptation
    ADAPT_ALL      = (0xFF, 0xFF)
    ADAPT_IDLE     = (0x02, 0x00)
    ADAPT_KNOCK    = (0x01, 0x00)
    ADAPT_LAMBDA   = (0x04, 0x00)
    ADAPT_THROTTLE = (0x08, 0x00)

    def clear_adaptations(self, sub1: int = 0xFF, sub2: int = 0xFF) -> bytes:
        """Clear learned adaptations (DS2 command 0x43).

        sub1, sub2 select which adaptation to clear — use the ADAPT_* class
        constants for convenience.  ECU responds with a 0xA0 ACK (empty payload).
        """
        return self.execute(DS2Commands.CLEAR_ADAPT, bytes([sub1, sub2]))

    def status(self) -> bytes:
        """0x0D status command — returns 69-byte ECU status payload."""
        return self.execute(DS2Commands.STATUS)

    # VIN encoding: BMW MS4x stores the VIN as 13 bytes using 6-bit packing
    # (4 VIN chars per 3 bytes, CHAR_MAP = digits + uppercase).
    # Source: mscoder project (github.com/sprytnyk/mscoder), confirmed against
    # live ECU reads without retaining any per-unit identity in this repository.
    _VIN_ADDR   = 0x1D07
    _VIN_BYTES  = 13        # 13 encoded bytes → 17-char VIN
    _VIN_CHARS  = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"

    @classmethod
    def _decode_vin(cls, raw: bytes) -> str:
        """Decode 13 6-bit-packed bytes into a 17-character VIN string.

        Returns "" if any 6-bit group falls outside the character map (e.g. an
        unprogrammed/0xFF VIN field), so read_vin rejects it cleanly instead of
        raising IndexError.
        """
        raw = raw.rjust(15, b"\x00")
        chars = []
        for i in range(0, 15, 3):
            x = (raw[i] << 16) | (raw[i + 1] << 8) | raw[i + 2]
            for s in (18, 12, 6, 0):
                idx = (x >> s) & 0x3F
                if idx >= len(cls._VIN_CHARS):
                    return ""
                chars.append(cls._VIN_CHARS[idx])
        return "".join(chars)[3:]

    def read_vin(self) -> str:
        """Return the decoded 17-char VIN, or empty string if unreadable / unprogrammed.

        Reads 13 encoded bytes from 0x1D07 and decodes the 6-bit-packed VIN.
        Returns empty string if the result is not a valid VIN pattern.
        """
        import re
        _VIN_RE = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")
        raw = self.read_mem(self._VIN_ADDR, self._VIN_BYTES)
        vin = self._decode_vin(raw)
        return vin if _VIN_RE.match(vin) else ""

    def read_mem(self, address: int, length: int) -> bytes:
        """Read `length` (1..247) bytes from DS2 memory address (32-bit)."""
        if not (0 < length <= 0xF7):
            raise ValueError("DS2 read length must be 1..247")
        args = address.to_bytes(4, "big") + bytes([length])
        return self.execute(DS2Commands.READ_MEM, args)

    def read_memory_range(self, start: int, total: int, chunk: int = 0xF7,
                          progress_cb=None, log_fn=None) -> bytes:
        """Read a contiguous range via repeated READ_MEM (247-byte blocks).

        Retries each chunk up to 3 times before raising.
        """
        out = bytearray()
        off = 0
        while off < total:
            n   = min(chunk, total - off)
            err = None
            for attempt in range(3):
                try:
                    out.extend(self.read_mem(start + off, n))
                    err = None
                    break
                except DS2Error as e:
                    err = e
                    if log_fn:
                        log_fn(f"Read retry {attempt+1}/3 at 0x{start+off:06X}: {e}", "warn")
                    time.sleep(0.05)
            if err:
                raise err
            off += n
            if progress_cb:
                progress_cb(off, total, "DS2 read")
        return bytes(out)

    # DS2 address of the 24 KB calibration/tune partition.
    # Verified: a partial-read capture reconstructs the 24 KB partial bin 100%
    # from DS2 0x10000 using 247-byte blocks.
    PARTIAL_DS2_ADDR = 0x10000
    PARTIAL_SIZE     = 24 * 1024

    def read_partial(self, progress_cb=None, log_fn=None) -> bytes:
        """Read the 24 KB calibration/tune partition (DS2 0x10000)."""
        return self.read_memory_range(
            self.PARTIAL_DS2_ADDR, self.PARTIAL_SIZE,
            chunk=0xF7, progress_cb=progress_cb, log_fn=log_fn)

    # Full 256 KB ROM read.  DS2 address space maps to the .bin layout by
    # swapping each adjacent pair of 0x4000 blocks:
    #   file_offset = DS2_addr XOR 0x4000  (within each pair)
    # Verified against a factory-tool full-read capture — reconstructs the
    # image 100% including boot sector.
    FULL_SIZE = 256 * 1024
    _BLOCK    = 0x4000

    def read_full(self, progress_cb=None, log_fn=None) -> bytes:
        """Read the complete 256 KB ROM (block-swapped .bin layout)."""
        nblk = self.FULL_SIZE // self._BLOCK   # 16 blocks
        ds2  = bytearray(self.FULL_SIZE)
        failed = []
        for blk in range(nblk):
            a = blk * self._BLOCK
            # Thread progress down to the actual 247-byte READ_MEM chunk level
            # (matches write_full/write_partial) instead of only ticking once
            # per 16 KB block — the per-block-only update was the cause of the
            # visibly jerky bar on a full read (16 big jumps vs ~1000 smooth ones).
            block_cb = None
            if progress_cb:
                # read_memory_range calls this with (done, total, label) — three
                # positional args. Capture the block base under its OWN name (base);
                # a bare `a=a` default gets clobbered by the third positional (the
                # label str), which then makes `a + done` a str+int TypeError.
                block_cb = lambda done, _total, _label="", base=a: progress_cb(
                    base + done, self.FULL_SIZE, "DS2 full read")
            try:
                ds2[a:a + self._BLOCK] = self.read_memory_range(
                    a, self._BLOCK, chunk=0xF7,
                    progress_cb=block_cb, log_fn=log_fn)
            except DS2Error as e:
                failed.append(a)
                if log_fn:
                    log_fn(f"DS2 block 0x{a:05X} unreadable ({e})", "error")
            if progress_cb:
                progress_cb((blk + 1) * self._BLOCK, self.FULL_SIZE, "DS2 full read")
        # NEVER return a partially-read image as if it were a valid backup — a
        # 0xFF-filled hole flashed back would brick the ECU.  Fail loudly instead.
        if failed:
            raise DS2Error(
                f"Full read FAILED: {len(failed)}/{nblk} blocks did not respond "
                f"(first @0x{failed[0]:05X}). No file written — the image would be "
                f"incomplete.")

        # Swap adjacent 0x4000 block pairs → .bin file layout.
        out = bytearray(self.FULL_SIZE)
        for blk in range(nblk):
            out[blk * self._BLOCK:(blk + 1) * self._BLOCK] = \
                ds2[(blk ^ 1) * self._BLOCK:((blk ^ 1) + 1) * self._BLOCK]

        # The DS2 0xC000-0xFFFF page is UNMAPPED — no chip drives the bus there, so a
        # raw read returns a floating-bus address ramp.  The true content is 0xFF
        # (a blank/floating region, not real data), so
        # 0xFF-fill it instead of polluting the image with bus noise.  After the block
        # swap, DS2 0xC000-0xFFFF (block 3) lands at file 0x8000-0xBFFF (block 2).
        out[0x8000:0xC000] = b"\xff" * 0x4000
        if log_fn:
            log_fn("0xFF-filled unmapped DS2 0xC000-0xFFFF (file 0x8000-0xBFFF)", "info")
        return bytes(out)

    # ── MS41 write-unlock (seed-key) ─────────────────────────────────────────
    # Reverse-engineered from sub_436a30 in the Galetto disassembly.
    # The ECU stores TX frame (8 bytes) + RX frame (46 bytes) as a 54-byte
    # buffer at arg1+0x25004e.
    # KEY[i] = (buf[XX+8+i] + buf[49+i] + buf[26+i]) & 0xFF  for i in 0..3
    # Verified against six ECUs (1437806 ×2, 1406464 ×2, xbyte ECU1, xbyte ECU2).

    @staticmethod
    def _compute_ms41_key(XX: int, rx_data_42: bytes) -> bytes:
        """Compute the 4-byte write-unlock key from the BMW challenge response."""
        assert 0 <= XX <= 41
        assert len(rx_data_42) == 42
        tx_frame = bytearray([0x12, 0x08, 0x90, 0x42, 0x4D, 0x57, XX, 0x00])
        tx_frame[7] = _xor(tx_frame[:7])
        rx_header = [0x12, 0x2E, 0xA0]
        rx_xor    = _xor(rx_header + list(rx_data_42))
        buf       = list(tx_frame) + rx_header + list(rx_data_42) + [rx_xor]
        ecx_5     = XX + 8
        return bytes((buf[ecx_5 + i] + buf[49 + i] + buf[26 + i]) & 0xFF
                     for i in range(4))

    def unlock_write(self, XX: int = None, *, log_fn=None, progress_cb=None) -> bytes:
        """Perform the MS41 DS2 write-unlock handshake (command 0x90).

        Sends BMW+XX challenge frame, receives 42-byte ECU response,
        computes the 4-byte key, sends it.  ACK payload is b'\\x00'.

        Args:
            XX: Challenge byte 0..41. Defaults to the capture- and live-proven
                initial challenge 0x1E.

        Firmware limits (decoded from the MS41 unlock handler @0x4A84 — do not
        defeat by looping):
          * Only TWO wrong-key attempts are allowed (counter 0xE74B); a 3rd
            locks the ECU out until a power-cycle.  We compute the correct key,
            so a single attempt should always pass — never auto-retry on a key
            mismatch.
          * E658 state 1 means the ECU expects a key. A BMW challenge must not
            be repeated in that state because it would count as a wrong key.
            Production defaults to the single captured initial challenge.
        """
        from ds2_write_authorization import (
            AUTHORIZATION_STATE_ADDRESS,
            CAPTURED_INITIAL_CHALLENGE,
            FLASH_MODE_MARKER_ADDRESS,
            INITIAL_SEED_RETRY_DELAY,
            MAX_INITIAL_SEED_ATTEMPTS,
            WRONG_KEY_COUNTER_ADDRESS,
        )

        if XX is None:
            XX = CAPTURED_INITIAL_CHALLENGE
        XX = int(XX)
        if not 0 <= XX <= 41:
            raise ValueError("write challenge must be in the stock range 0..41")
        max_seed_attempts = MAX_INITIAL_SEED_ATTEMPTS
        seed_retry_delay = INITIAL_SEED_RETRY_DELAY

        def read_authorization_state():
            state_data = self.read_mem(AUTHORIZATION_STATE_ADDRESS, 1)
            counter_data = self.read_mem(WRONG_KEY_COUNTER_ADDRESS, 1)
            if len(state_data) != 1 or len(counter_data) != 1:
                raise DS2Error("Could not read the stock write-authorization state")
            state = state_data[0]
            counter = counter_data[0]
            log.debug("MS41 write auth state: E658=%d E74B=%d", state, counter)
            return state, counter

        def observe_flash_mode_marker(label):
            marker_data = self.read_mem(FLASH_MODE_MARKER_ADDRESS, 1)
            if len(marker_data) != 1:
                raise DS2Error("Could not read the stock flash-mode marker E740")
            marker = marker_data[0]
            message = f"MS41 flash-mode marker ({label}): E740=0x{marker:02X}"
            log.info(message)
            if log_fn:
                try:
                    log_fn(message, "info")
                except TypeError:
                    log_fn(message)
            return marker

        state, wrong_keys = read_authorization_state()
        observe_flash_mode_marker("initial authorization")
        if wrong_keys >= 2:
            raise DS2Error(
                "Write authorization is locked (E74B >= 2); turn ignition off, "
                "wait 10 seconds, then turn ignition on before flashing"
            )
        if state == 1:
            raise DS2Error(
                "ECU is already waiting for an authorization key (E658=1); "
                "turn ignition off, wait 10 seconds, then turn ignition on "
                "before flashing"
            )
        if state not in (0, 2):
            raise DS2Error(f"Unexpected write-authorization state E658={state}")

        bmw_payload = bytes([0x42, 0x4D, 0x57, XX])
        if state == 2:
            already = self.execute(0x90, bmw_payload)
            if already != b"\x00":
                raise DS2Error(
                    "ECU reported E658=2 but did not confirm existing authorization"
                )
            log.info("MS41 existing write authorization confirmed")
            return already

        initial_wrong_keys = wrong_keys

        rx_data = None
        last_error = None
        for attempt in range(1, max_seed_attempts + 1):
            try:
                rx_data = self.execute(0x90, bmw_payload)
                break
            except DS2Error as error:
                retryable_empty_a1 = isinstance(error, DS2NegativeResponse) and (
                    error.command == 0x90
                    and error.status == 0xA1
                    and error.payload == b""
                )
                if not retryable_empty_a1:
                    raise
                last_error = error
                try:
                    state, wrong_keys = read_authorization_state()
                    observe_flash_mode_marker(f"after challenge A1 attempt {attempt}")
                except DS2Error as state_error:
                    raise DS2Error(
                        "Write-seed response was ambiguous and ECU authorization "
                        "state could not be confirmed; ignition cycle required"
                    ) from state_error
                if state != 0 or wrong_keys != initial_wrong_keys:
                    raise DS2Error(
                        "Write-seed response was ambiguous and ECU authorization "
                        f"state changed (E658={state}, E74B={wrong_keys}); turn "
                        "ignition off, wait 10 seconds, then turn ignition on"
                    ) from error
                if attempt >= max_seed_attempts:
                    raise DS2NegativeResponse(
                        f"Write seed unavailable after {attempt} bounded "
                        f"BMW/0x{XX:02X} challenges while E658 remained zero",
                        command=getattr(error, "command", 0x90),
                        status=getattr(error, "status", None),
                        response=getattr(error, "response", b""),
                    ) from error
                wait_message = (
                    f"ECU seed not ready; waiting {seed_retry_delay:g} seconds "
                    "before one final retry"
                )
                log.info(wait_message)
                if log_fn:
                    try:
                        log_fn(wait_message, "warn")
                    except TypeError:
                        log_fn(wait_message)
                if progress_cb:
                    progress_cb(0, 0, wait_message)
                time.sleep(seed_retry_delay)
                self._prepare()
                self.read_mem(0x2001, 12)
                self.status()
        if rx_data is None:
            raise last_error or DS2Error("Write seed challenge produced no response")
        # If the ECU is still unlocked from a prior write in this power cycle, it
        # answers the challenge with the short ACK (payload b'\x00') instead of
        # the 42-byte seed.  Treat that as "already unlocked" and skip the key
        # step — otherwise a second flash without a power-cycle fails with
        # "BMW challenge response: expected 42 bytes, got 1".  (execute() has
        # already validated the status byte was a positive 0xA0 ACK.)
        if rx_data == b"\x00":
            log.info("MS41 already unlocked (challenge returned ACK) — skipping key")
            return rx_data
        if len(rx_data) != 42:
            raise DS2Error(
                f"BMW challenge response: expected 42 bytes, got {len(rx_data)}")

        key = self._compute_ms41_key(XX, rx_data)
        log.debug("unlock_write: XX=0x%02X  key=%s", XX, key.hex())
        ack = self.execute(0x90, key)

        if ack != b"\x00":
            raise DS2NegativeResponse(
                f"Write unlock failed — ECU returned {ack.hex(' ')!r}")
        log.info("MS41 write unlock OK")
        return ack

    # ── flash / write commands ───────────────────────────────────────────────
    # All 0x07 responses are 10 bytes:
    #   [0x12, 0x0A, 0xA0, sub, addr_hi, addr_mid, addr_lo, count, 0x01, XOR]
    # For write (sub=0x02): addr_hi/mid/lo = NEXT expected write address,
    # count = 0xE7 (always).  Verified byte-for-byte from partial-write capture.

    WRITE_CHUNK   = 231    # 0xE7 bytes of payload per write frame
    WRITE_RETRIES = 3      # resend a failed write block up to 3× (matches Galetto)

    def _prepare(self) -> bytes:
        """0xA2 prepare-for-write command.  ECU returns status 0xFF (normal)."""
        return self.execute(DS2Commands.PREPARE)

    def _flash_sub(self, sub: int, addr_3b: bytes, payload: bytes = b"",
                   timeout: float = None) -> bytes:
        """Send a 0x07 flash sub-command and return the response data payload."""
        if len(addr_3b) != 3:
            raise ValueError("addr_3b must be exactly 3 bytes")
        args = bytes([sub]) + addr_3b + bytes([len(payload)]) + payload
        return self.execute(DS2Commands.FLASH_OP, args, timeout=timeout)

    @staticmethod
    def _ds2_addr3(ds2_addr: int) -> bytes:
        """Return DS2 address as 3-byte big-endian (used in 0x07 frames)."""
        return ds2_addr.to_bytes(3, "big")

    def _erase_sector(self, ds2_addr: int, log_fn=None,
                      step: float = ERASE_STEP_DELAY,
                      settle: float = POST_ERASE_DELAY) -> None:
        """Erase the flash sector at ds2_addr.

        Sends erase-poll (0x0F) twice then erase-sector (0x06).  `step` is the
        gap between those sub-commands and `settle` is the wait after the erase
        ACK before the caller writes.  The erase ACK is fast (it does NOT mean
        the erase is physically done), so `settle` must cover the real erase
        time — short for a 16 KB tune sector, seconds for the whole program
        array (see PROGRAM_ERASE_* constants).
        """
        a3 = self._ds2_addr3(ds2_addr)
        self._flash_sub(0x0F, a3)   # poll ×1
        time.sleep(step)
        self._flash_sub(0x0F, a3)   # poll ×2
        time.sleep(step)
        self._flash_sub(0x06, a3, timeout=ERASE_TIMEOUT)   # erase
        time.sleep(settle)
        if log_fn:
            log_fn(f"Flash sector at DS2 0x{ds2_addr:06X} erased")

    def _write_block(self, ds2_addr: int, data: bytes, log_fn=None) -> None:
        """Write one flash block (1..231 bytes) at ds2_addr.

        The ECU ACKs with [sub=0x02, next_addr(3), 0xE7, 0x01].

        Resends the same block up to WRITE_RETRIES times on a NAK or comms
        error before giving up — mirrors the Galetto write loop, which retries
        each block ~3× before failing.  Resending the same frame to the same
        address is safe: on freshly-erased flash it is idempotent, and the frame
        carries its address explicitly so a retry targets the same bytes.
        Raises DS2NegativeResponse / DS2Error only after all attempts fail.
        """
        if not (0 < len(data) <= self.WRITE_CHUNK):
            raise ValueError(f"Write block data must be 1..{self.WRITE_CHUNK} bytes")
        a3 = self._ds2_addr3(ds2_addr)
        last_err = None
        for attempt in range(self.WRITE_RETRIES):
            try:
                resp = self._flash_sub(0x02, a3, data)
            except DS2Error as e:
                # comms-level failure (timeout / NAK / checksum) — retryable
                last_err = e
                if log_fn:
                    log_fn(f"Write retry {attempt+1}/{self.WRITE_RETRIES} at "
                           f"0x{ds2_addr:06X}: {last_err}", "warn")
                time.sleep(WRITE_RETRY_DELAY)
                continue
            if resp and resp[0] == 0x02:
                # resp[5] = ECU flash-engine status (DAT_00e528): 0x01 = OK.
                # A non-OK code is a real flash error (e.g. 0x03 = not blank),
                # NOT a comms glitch — retrying the same block won't help, so
                # surface it immediately with the decoded meaning.
                fs = resp[5] if len(resp) > 5 else 0x01
                if fs != 0x01:
                    raise DS2NegativeResponse(
                        f"Flash error writing 0x{ds2_addr:06X}: "
                        f"{describe_flash_status(fs)} (status=0x{fs:02X})")
                # Brief gap before the next block — matches the factory tool's
                # ~12 ms floor (the ECU's ~50 ms write-ACK latency paces most).
                time.sleep(INTER_WRITE_DELAY)
                return
            last_err = DS2NegativeResponse(
                f"Write block at 0x{ds2_addr:06X} NAK: {resp.hex(' ')}")
            if log_fn:
                log_fn(f"Write retry {attempt+1}/{self.WRITE_RETRIES} at "
                       f"0x{ds2_addr:06X}: {last_err}", "warn")
            time.sleep(WRITE_RETRY_DELAY)
        raise last_err

    def _write_program_sectors(self, ds2: bytes, lo: int, hi: int, log_fn=None,
                               progress_cb=None, prog_total=0, prog_done=0):
        """Write the program range DS2 [lo, hi) one 0x4000-aligned sector at a time.

        The ECU only accepts a write whose start address is contiguous with the
        previous block OR a 0x4000-aligned sector boundary; a non-aligned resume
        after a skip is NAK'd (0xB0).  So we cannot FF-skip to the next non-FF
        block (that lands mid-sector — the 0x00C7D6 brick).  Instead:

          * Entirely-0xFF aligned sectors are skipped (resume lands on the next
            sector's aligned base).
          * A sector containing any data is written from its (clamped) base in
            <=231-byte blocks that never cross the 0x4000 boundary, up to and
            including the block holding the last non-FF byte.  Leading/internal
            0xFF inside a data sector IS written (it's a no-op on erased flash and
            keeps the run contiguous); trailing 0xFF whole-blocks are not.

        The 107/214-byte
        partials are just the last block of each sector clamped to the boundary.
        Returns (blocks_written, blocks_skipped).
        """
        written = skipped = 0
        off = lo
        while off < hi:
            sec_end = min((off & ~0x3FFF) + 0x4000, hi)
            sector = ds2[off:sec_end]
            if sector == b"\xFF" * len(sector):
                skipped += (len(sector) + self.WRITE_CHUNK - 1) // self.WRITE_CHUNK
                off = sec_end
                if progress_cb:
                    progress_cb(prog_done + off - lo, prog_total, "DS2 full write")
                continue
            # Absolute address one past the last non-FF byte in this sector.
            last = len(sector)
            while sector[last - 1] == 0xFF:
                last -= 1
            last_data_end = off + last
            w = off
            while w < last_data_end:
                sz = min(self.WRITE_CHUNK, sec_end - w)
                self._write_block(w, bytes(ds2[w:w + sz]), log_fn=log_fn)
                written += 1
                w += sz
                if progress_cb:
                    progress_cb(prog_done + w - lo, prog_total, "DS2 full write")
            off = sec_end
        return written, skipped

    PROGRAM_VERIFY_DS2_ADDR = 0x001D07

    def verify_program_region(self, log_fn=None):
        """Run the stock program-integrity finalize and return (ok, status).

        A passing 0x07/0x0F check also commits E740=0. Soft-BSL installation
        uses this through the application's shared DS2 transport.
        """
        def _log(message, level="info"):
            if log_fn:
                log_fn(message, level) if level != "info" else log_fn(message)

        _log(f"Program verify: re-open + FLASH_OP verify "
             f"@DS2 0x{self.PROGRAM_VERIFY_DS2_ADDR:06X} ...")
        self._prepare()
        self.read_mem(0x2001, 12)
        self.status()
        t0 = time.time()
        while self.read_mem(0xE659, 1)[0] != 0xCC:
            if time.time() - t0 > 25.0:
                _log("0xE659 not ready for verify; skipped", "warn")
                return None, None
            time.sleep(0.5)
        self.unlock_write(log_fn=_log)
        self.read_mem(0x1CF4, 3)
        self.status()
        resp = self._flash_sub(0x0F, self._ds2_addr3(self.PROGRAM_VERIFY_DS2_ADDR))
        status = resp[-1] if resp else None
        ok = status == 0x01
        status_text = f"0x{status:02X}" if status is not None else "?? (no response)"
        _log(f"Program verify status = {status_text} "
             f"({'PASS (program OK; E740->0 finalized)' if ok else 'NOT 0x01 -- program may be BAD'})",
             "info" if ok else "warn")
        return ok, status

    def verify_deployed_program(self, ds2_image, log_fn=None, progress_cb=None):
        """Read back both writable program windows and compare them byte-for-byte.

        The stock finalize verifies only fixed firmware signatures. This additional read
        covers custom hooks and patch caves deployed during Soft-BSL installation.
        Returns ``(ok, bytes_compared, mismatches, first_bad_address)``.
        """
        def report(message, level="info"):
            if log_fn:
                try:
                    log_fn(message, level)
                except TypeError:
                    log_fn(message)

        ranges = ((0x2000, 0x6000), (0x20000, 0x40000))
        verify_total = sum(hi - lo for lo, hi in ranges)
        verify_done = 0
        compared = mismatches = 0
        first_bad = None
        for lo, hi in ranges:
            range_progress = None
            if progress_cb:
                range_progress = (
                    lambda done, _total, _label=None, base=verify_done:
                    progress_cb(base + done, verify_total, "bootstrap read-back"))
            actual = self.read_memory_range(
                lo, hi - lo, chunk=0xF7, progress_cb=range_progress)
            expected = bytes(ds2_image[lo:hi])
            count = min(len(actual), len(expected))
            compared += count
            for index in range(count):
                if actual[index] != expected[index]:
                    mismatches += 1
                    if first_bad is None:
                        first_bad = lo + index
            if len(actual) != len(expected):
                mismatches += abs(len(actual) - len(expected))
                if first_bad is None:
                    first_bad = lo + count
            verify_done += hi - lo

        ok = mismatches == 0 and compared == verify_total
        if ok:
            report(f"Program read-back verified: {compared:,} bytes match.", "ok")
        else:
            where = f"0x{first_bad:06X}" if first_bad is not None else "unknown"
            report(
                f"Program read-back mismatch: {mismatches:,} byte(s); first at {where}.",
                "error")
        return ok, compared, mismatches, first_bad

    # ── high-level write ─────────────────────────────────────────────────────
    def write_partial(self, data: bytes, progress_cb=None, log_fn=None,
                      skip_unlock: bool = False, skip_prepare: bool = False) -> None:
        """Write the 24 KB tune/calibration partition (DS2 0x10000–0x15FFF).

        Sequence:
          prepare → read(0x2001,12) → status → unlock → read(0x1CF4,3)
          → read(0x1000E,2) → erase 0x10000 → status → write blocks (skip all-0xFF)

        Args:
            data: Exactly 24576 bytes.  Must pass checksum validation before call.

        The stock program-integrity finalizer is always run after programming.  It commits
        E740=0 inside the ECU and is separate from any optional host read-back verification.
        """
        if len(data) != self.PARTIAL_SIZE:
            raise ValueError(
                f"Partial write expects {self.PARTIAL_SIZE} bytes, got {len(data)}")

        def _log(msg, level="info"):
            if log_fn:
                log_fn(msg, level) if level != "info" else log_fn(msg)

        start = self.PARTIAL_DS2_ADDR   # 0x10000

        if not skip_prepare:
            _log("Preparing ECU for write (0xA2)…")
            self._prepare()

            _log("Reading ECU state (0x2001)…")
            self.read_mem(0x2001, 12)

            _log("Requesting ECU status (0x0D)…")
            self.status()

        if not skip_unlock:
            _log("Unlocking write (DS2 seed-key)…")
            self.unlock_write(log_fn=_log, progress_cb=progress_cb)

        # Post-unlock diagnostic reads (observed in capture)
        self.read_mem(0x1CF4, 3)
        self.read_mem(0x1000E, 2)

        _log(f"Erasing tune sector (DS2 0x{start:06X})…")
        self._erase_sector(start, log_fn=log_fn)

        # Capture shows STATUS only between erase and the first write block
        # (no IDENTIFY).  Matched byte-for-byte against Capture Partial Write.csv.
        self.status()

        # Skip all-0xFF blocks.  Two reasons:
        #   1. The trailing FF at the end of the tune region (0x15EBF-0x16000)
        #      must NOT be written — the ECU NAKs (0xB0) a write there.  The
        #      factory tool stops at the last real block (0x15DD8).
        #   2. The remaining non-FF blocks all sit inside the single erased tune
        #      sector, so the skip-induced jump (e.g. 0x13624 → 0x15DD8) is an
        #      INTRA-sector jump, which the ECU accepts.  (Cross-sector jumps to
        #      a non-aligned address are what fail — see write_full Phase 1.)
        _log("Writing tune data…")
        n               = len(data)
        # Factory scheme: WRITE_CHUNK (231-byte) blocks straight through, skipping
        # any all-0xFF block.  The
        # trailing FF tail is all-0xFF blocks, so it is skipped and never NAK'd 0xB0.
        off             = 0
        chunks_written  = 0
        chunks_skipped  = 0

        while off < n:
            sz    = min(self.WRITE_CHUNK, n - off)
            chunk = data[off:off + sz]

            if chunk == b"\xFF" * sz:
                chunks_skipped += 1
            else:
                self._write_block(start + off, chunk, log_fn=log_fn)
                chunks_written += 1

            off += sz
            if progress_cb:
                progress_cb(off, n, "Flash write")

        _log(f"Tune write complete: {chunks_written} blocks written, "
             f"{chunks_skipped} skipped (all-0xFF)")

        # ECU-side protocol finalization is mandatory even when the host Verify checkbox is
        # off.  This does not read or compare the tune bytes; the stock ECU operation commits
        # the clean marker (E740=0) after validating the program-integrity gate.
        _log("Running stock program-integrity finalizer (0x07/0x0F @ DS2 0x001D07)…")
        ok, status = self.verify_program_region(log_fn=log_fn)
        if not ok:
            shown = f"0x{status:02X}" if status is not None else "no status"
            raise DS2NegativeResponse(
                f"Stock write finalizer did not pass ({shown}); E740=0 was not committed"
            )
        _log("Stock write finalizer passed; E740=0 committed.")

    # ── full ROM write ───────────────────────────────────────────────────────
    # On BOTH MS41.1 (ECU 1437806) and MS41.2 (ECU 1406464) the sequence is
    # identical: erase 0x002000, write program,
    # erase 0x010000, write tune.
    #
    # Flash address map (DS2 address space, 256 KB):
    #   DS2 0x000000-0x001FFF  boot block (hardware-protected, NEVER written)
    #   DS2 0x002000-0x00FFFF  program-low   (written in Phase 1)
    #   DS2 0x010000-0x01FFFF  tune/cal      (written in Phase 2)
    #   DS2 0x020000-0x03FFFF  program-high  (written in Phase 1)
    #
    # File → DS2 mapping (same XOR-0x4000 swap as read):
    #   ds2_addr = (file_block ^ 1) * 0x4000 + (file_offset % 0x4000)
    #
    # Write sequence (matched byte-for-byte against a real full-write capture on
    # BOTH MS41.1 and MS41.2):
    #   Phase 1 — program:
    #     prepare → read(0x2001,12) → status → unlock → read(0x1CF4,3)
    #     → erase 0x002000  (ONE erase clears the whole program array)
    #     → write DS2 0x002000..0x00FFFF then 0x020000..0x03FFFF, one
    #       0x4000-aligned sector at a time (_write_program_sectors)
    #     (tune sector 0x010000-0x01FFFF is deliberately excluded from Phase 1)
    #
    #   Phase 2 — tune sector (written last):
    #     → erase 0x010000 → status
    #     → write DS2 0x010000..0x01FFFF (skip all-0xFF; intra-sector jumps OK)
    #
    # THE RULE (from the captures + HW): a write start address must be contiguous
    # with the previous block OR a 0x4000-aligned sector boundary.  Phase 1 spans
    # many program sectors, so it must resume every FF-skip on a 0x4000 boundary
    # (a non-aligned resume like 0x5F2A→0xC7D6 is NAK'd 0xB0 — the brick).  The
    # tune region is a single erased sector, so its FF-skip jumps stay intra-
    # sector and are accepted (proven by the working partial write).
    #
    # NOTE: the capture also emits one initial 128-byte write at 0x002000 that
    # is immediately overwritten by the following 231-byte block (same data, to
    # freshly-erased flash → identical end state).  We omit that redundant block;
    # the resulting flash content is byte-identical.  See review notes.

    # DS2 address of the tune sector start/end (excluded from Phase 1)
    _TUNE_DS2_START = 0x010000
    _TUNE_DS2_END   = 0x01FFFF   # 64 KB tune sector

    def write_full(self, data: bytes, progress_cb=None, log_fn=None,
                   skip_unlock: bool = False, skip_prepare: bool = False) -> None:
        """Write the complete 256 KB ROM image over DS2.

        This is a two-phase write:
          Phase 1 writes the program sectors (0x002000-0x00FFFF and 0x020000-0x03FFFF),
          Phase 2 erases and writes the tune/cal sector (0x010000-0x01FFFF).

        Sectors are erased before each phase.  The boot block (0x000000-0x001FFF)
        is hardware-protected and is never written.

        Args:
            data: Exactly 262144 bytes (full 256 KB ROM, standard file layout).
        """
        if len(data) != self.FULL_SIZE:
            raise ValueError(
                f"Full ROM write expects {self.FULL_SIZE} bytes, got {len(data)}")

        def _log(msg, level="info"):
            if log_fn:
                log_fn(msg, level) if level != "info" else log_fn(msg)

        # ── Build DS2 address space from file (inverse block-swap) ──────────
        # File uses the XOR-0x4000 layout: file block N ↔ DS2 block N^1.
        ds2 = bytearray(self.FULL_SIZE)
        nblk = self.FULL_SIZE // self._BLOCK
        for blk in range(nblk):
            src = blk * self._BLOCK
            dst = (blk ^ 1) * self._BLOCK
            ds2[dst:dst + self._BLOCK] = data[src:src + self._BLOCK]

        # ── Phase 1 preamble ─────────────────────────────────────────────────
        if not skip_prepare:
            _log("Preparing ECU for write (0xA2)…")
            self._prepare()
            _log("Reading ECU state (0x2001)…")
            self.read_mem(0x2001, 12)
            _log("Requesting ECU status (0x0D)…")
            self.status()
        if not skip_unlock:
            _log("Unlocking write (DS2 seed-key)…")
            self.unlock_write(log_fn=_log, progress_cb=progress_cb)
        self.read_mem(0x1CF4, 3)

        # ── Phase 1 — program sectors ─────────────────────────────────────────
        # The capture issues a SINGLE erase at 0x002000 before writing both the
        # program-low (0x002000-0x00FFFF) and program-high (0x020000-0x03FFFF)
        # ranges — this one ECU-level erase clears the whole program array.  We
        # do NOT erase 0x020000/0x030000 separately: the real tool doesn't, and
        # those addresses are not confirmed valid erase-sector bases.
        _log("Erasing program array (DS2 0x002000)…")
        # The program-array erase clears the whole ~200 KB region; the tool
        # spaces its polls ~0.45 s apart and waits ~2.6 s after the ACK before
        # writing (the ACK is not erase-complete).  Match that so we never write
        # into an unfinished erase.
        self._erase_sector(0x002000, log_fn=log_fn,
                           step=PROGRAM_ERASE_STEP_DELAY,
                           settle=POST_PROGRAM_ERASE_DELAY)

        # Phase 1 writes the two PROGRAM windows only.  These are the exact DS2
        # program ranges (the two PROGRAM windows), which write ONLY:
        #     program-low  DS2 0x002000-0x005FFF  (never past ~0x5F95)
        #     tune         DS2 0x010000-0x015FFF  (Phase 2)
        #     program-high DS2 0x020000-0x03FFFF
        # Everything else — boot (0x0-0x1FFF), the gap 0x006000-0x00FFFF, and
        # 0x016000-0x01FFFF — is NEVER written by the factory tool and must be
        # left alone.  Writing into the 0x008000-0x00FFFF gap is what produced the
        # 0xC000/0xC7D6 0xB0 NAKs (that region is outside the writable program
        # array; a real ROM may hold factory data there that the flasher does not
        # touch).  Validated against both real full-write captures.
        # Progress is reported as ONE bar spanning both phases: a single grand
        # total of program bytes + tune bytes, with Phase 2 continuing where
        # Phase 1 left off (so the bar fills 0→100% once, not twice).
        _log("Writing program data (Phase 1)…")
        prog_low   = (0x002000, 0x006000)
        prog_high  = (0x020000, self.FULL_SIZE)
        prog_bytes = (prog_low[1] - prog_low[0]) + (prog_high[1] - prog_high[0])
        tune_span  = self._TUNE_DS2_END - self._TUNE_DS2_START + 1
        grand_total = prog_bytes + tune_span
        w1, s1 = self._write_program_sectors(
            ds2, *prog_low, log_fn=log_fn, progress_cb=progress_cb,
            prog_total=grand_total, prog_done=0)
        w2, s2 = self._write_program_sectors(
            ds2, *prog_high, log_fn=log_fn, progress_cb=progress_cb,
            prog_total=grand_total, prog_done=prog_low[1] - prog_low[0])
        written1, skipped1 = w1 + w2, s1 + s2

        _log(f"Phase 1 complete: {written1} blocks written, {skipped1} skipped.")

        # ── Phase 2 — tune/cal sector ─────────────────────────────────────────
        _log("Erasing tune sector (DS2 0x010000)…")
        self._erase_sector(self._TUNE_DS2_START, log_fn=log_fn)

        # Capture shows STATUS only between the tune erase and the first tune
        # write block (no IDENTIFY).  Matched against Capture Full Write.csv.
        self.status()

        # Same FF-skip as write_partial: the trailing FF must not be written
        # (0xB0), and the remaining real blocks stay inside the erased tune
        # sector so the skip jumps are intra-sector and accepted.
        written2 = skipped2 = 0
        _log("Writing tune/cal data (Phase 2)…")
        off = self._TUNE_DS2_START
        while off <= self._TUNE_DS2_END:
            sz    = min(self.WRITE_CHUNK, self._TUNE_DS2_END - off + 1)
            chunk = bytes(ds2[off:off + sz])
            if chunk == b"\xFF" * sz:
                skipped2 += 1
            else:
                self._write_block(off, chunk, log_fn=log_fn)
                written2 += 1
            off += sz
            if progress_cb:
                progress_cb(prog_bytes + (off - self._TUNE_DS2_START),
                            grand_total, "DS2 full write")

        _log(f"Phase 2 complete: {written2} blocks written, {skipped2} skipped.")
        _log(f"Full ROM write done: {written1+written2} blocks written total.")

        # Keep ECU-side finalization separate from the optional host read-back.  Every
        # successful DS2 write must leave the stock clean marker, even with Verify OFF.
        _log("Running stock program-integrity finalizer (0x07/0x0F @ DS2 0x001D07)…")
        ok, status = self.verify_program_region(log_fn=log_fn)
        if not ok:
            shown = f"0x{status:02X}" if status is not None else "no status"
            raise DS2NegativeResponse(
                f"Stock write finalizer did not pass ({shown}); E740=0 was not committed"
            )
        _log("Stock write finalizer passed; E740=0 committed.")

    # ── utility ──────────────────────────────────────────────────────────────
    @staticmethod
    def list_ports():
        if serial is None:
            return []
        # NB: `from serial.tools import ...` (not `import serial.tools...`) so we
        # don't rebind the module-level `serial` name as a function local, which
        # would make the `serial is None` check above raise UnboundLocalError.
        from serial.tools import list_ports as _list_ports
        return [p.device for p in _list_ports.comports()]
