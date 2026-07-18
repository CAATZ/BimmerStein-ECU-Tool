#!/usr/bin/env python3
"""
BimmerStein ECU Tool hardware bootstrap-loader engine for the MS41 (SAB 80C166).

The 80C166 has a built-in BSL in CPU silicon, so it works even when the flash
is corrupt/unbootable.  Sequence (from the SAB80C166 datasheet):

  1. Force BSL entry IN HARDWARE: at the end of a hardware reset, ALE must be
     sampled HIGH and NMI# must be active (LOW).
        ALE   = CPU pin 25
        NMI#  = CPU pin 29   (hold low)
        RSTIN#= CPU pin 27   (pulse to reset)
     Practically: pull NMI# (pin 29) to GND, tie/pull ALE (pin 25) high, then
     pulse RSTIN# (pin 27) low->high.  Release NMI# after reset if you like.
  2. The BSL scans RXD0 for a 0x00 byte, measures its bit time (auto-baud),
     and replies 0x55 on TXD0.
  3. The next 32 bytes are stored at 0xFA40-0xFA5F (internal RAM); the BSL then
     jumps to 0xFA40 and runs them.

Serial path: a direct full-duplex tap on ASC0 = TXD0 (P3.10) -> host RX and
host TX -> RXD0 (P3.11), via an FT232.  (The Siemens BSL app-note warns against
half-duplex / single-wire K-line, so this tool only supports the direct tap.)

The whole flash (boot, program, cal) is in-circuit erasable/programmable/verifiable
over BSL — no bench programmer.  Both the original Intel 28F200 (Intel command set,
needs 12V VPP) and the AMD/JEDEC 29F200/29F400 family (single-supply, no 12V) are
supported; pick the command set with --chip.  Everyday use is the `flash` command:

  flash <region|all> --ref <image> --arm   (DRY-RUN without --arm)

Other commands: `sync` (confirm BSL entry + that loaded code runs), `dump` (read the
chip to a .bin), `read`/`write`, `verify-alias`, `vpp-on`, `businfo`.  The low blocks
(CPU 0x0-0x7FFF) are BSL-shadowed and auto-routed through a +0x40000 wrap-around alias;
the GAL maps flash A13 = !CPU A14, so a file-order .bin = CPU image XOR 0x4000.
"""
import sys
import time
import argparse
import datetime
import os

try:
    import serial
    from serial.tools import list_ports as _list_ports
except ImportError:
    serial = None

try:
    from engines.softbsl.d2xx_serial import D2XXError
except ImportError:                         # pragma: no cover - package fallback
    D2XXError = OSError

from ms41 import MS41ECU, SS1V2_PROG_SIG, SS1V2_PROG_SIG_ADDR
import checksum as cks
from operation_log import emit as _emit

__version__ = "1.2.0"                   # bump on each release; tag the commit v<version> (see CHANGELOG.md)

CAL_PARTIAL_SIZE = 24 * 1024           # a 24KB cal/tune partial (DS2 0x10000.., CPU-order)
_SERIAL_ERRS = ((serial.SerialException,) if serial else ()) + (D2XXError,)


def _import_d2xx_serial():
    """Import the shared D2XX-backed serial shim without loading the DLL yet."""
    from engines.softbsl.d2xx_serial import D2XXSerial
    return D2XXSerial


def _make_bar(label):
    """Return a progress callback cb(done, total) that draws a one-line '\\r' bar for a long
    transfer — or None if stdout isn't a TTY (so redirected/piped output isn't spammed).
    The caller (mon_read/mon_program) writes the closing newline when the transfer ends."""
    if not sys.stdout.isatty():
        return None
    t0 = time.time()

    def cb(done, total):
        w = 24
        frac = (done / total) if total else 1.0
        fill = int(w * frac)
        el = time.time() - t0
        rate = done / el if el > 0 else 0
        eta = (total - done) / rate if rate > 0 else 0
        sys.stdout.write(f"\r  {label:<11}[{'#' * fill}{'.' * (w - fill)}] {frac * 100:3.0f}% "
                         f"{done // 1024:>3}/{total // 1024 or 1} KB  {rate / 1024:4.1f} KB/s "
                         f"ETA {eta:3.0f}s ")
        sys.stdout.flush()
    return cb


class BSLError(Exception):
    pass


class BSL:
    LOAD_ADDR = 0xFA40          # BSL loads 32 bytes here and jumps
    SYNC_BYTE = 0x00           # host -> ECU (auto-baud reference)
    ACK_BYTE  = 0x55           # ECU -> host (BSL acknowledge)

    def __init__(self, port, baud=9600, timeout=2.0,
                 reset_line=None, reset_hold=0.02, reset_settle=0.015, reset_invert=False):
        self.baud = baud
        self.port = port
        self.timeout = timeout
        self.reset_line = reset_line          # None | "rts" | "dtr": FTDI output that drives RSTIN#
        self.reset_hold = reset_hold          # s to hold reset asserted
        self.reset_settle = reset_settle      # s to wait after release before sync
        self.reset_invert = reset_invert      # flip polarity (transistor/inverter in the path)
        self.transport_name = None
        self.ser = None
        self._open_transport()
        if self.reset_line:                   # don't leave the CPU held in reset on open
            try:
                self._drive_reset(False)
            except Exception as e:
                _emit(f"  WARN: could not set {self.reset_line.upper()} ({e})")

    def _open_transport(self, *, prefer_d2xx=True):
        """Open direct ASC0 using D2XX first, with a pyserial fallback.

        The built-in 80C166 BSL is not DS2: it auto-baud-locks on a raw 8N1
        ``0x00`` and expects a full-duplex direct tap.  D2XX is therefore
        configured explicitly for 8N1 here rather than inheriting DS2's 8E2.
        """
        d2xx_error = None
        if prefer_d2xx and os.environ.get("BSL_D2XX", "1") != "0":
            try:
                D2XXSerial = _import_d2xx_serial()
                self.ser = D2XXSerial(
                    port=self.port, baudrate=self.baud,
                    bytesize=8, parity=0, stopbits=1,
                    timeout=self.timeout, write_timeout=3.0,
                    two_stop=False)
                self.transport_name = "d2xx"
                _emit(f"  BSL transport: D2XX direct ASC0/8N1 ({self.port})")
                return
            except Exception as error:
                d2xx_error = error
                _emit(f"  D2XX unavailable ({error}); falling back to pyserial")

        if serial is None:
            detail = f"; D2XX unavailable: {d2xx_error}" if d2xx_error else ""
            raise BSLError(
                "no BSL serial transport is available: install the FTDI D2XX driver "
                "or pyserial" + detail)

        self.ser = serial.Serial(
            self.port, self.baud, bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE, stopbits=serial.STOPBITS_ONE,
            timeout=self.timeout)
        self.transport_name = "pyserial"
        _emit(f"  BSL transport: pyserial direct ASC0/8N1 ({self.port})")

    def close(self):
        """Close the direct-tap serial handle. Safe to call repeatedly."""
        try:
            if self.ser:
                self.ser.close()
        except Exception:
            pass

    # ── low level ──────────────────────────────────────────────────────────
    def _read(self, n, deadline):
        out = bytearray()
        while len(out) < n and time.time() < deadline:
            b = self.ser.read(n - len(out))
            if b:
                out += b
        return bytes(out)

    # ── optional FTDI-driven CPU reset (auto BSL entry) ────────────────────
    def _drive_reset(self, assert_reset):
        # pyserial True 'asserts' the line -> FTDI RTS#/DTR# pin goes LOW.  RSTIN# is
        # active-low, so assert_reset=True => pin LOW => held in reset (no invert).
        lvl = bool(assert_reset) ^ bool(self.reset_invert)
        if self.reset_line == "rts":
            if hasattr(self.ser, "setRTS"):
                self.ser.setRTS(lvl)
            else:
                self.ser.rts = lvl
        elif self.reset_line == "dtr":
            if hasattr(self.ser, "setDTR"):
                self.ser.setDTR(lvl)
            else:
                self.ser.dtr = lvl

    def pulse_reset(self, log=_emit):
        """Pulse RSTIN# via the chosen FTDI output so the CPU re-enters BSL with no
        hands: ALE-high + NMI#-low are static straps, and the RSTIN# rising edge
        latches BSL mode.  No-op unless --reset-line was given."""
        if not self.reset_line:
            return
        log(f"reset: pulsing RSTIN# via {self.reset_line.upper()} "
            f"(hold {self.reset_hold * 1000:.0f}ms, settle {self.reset_settle * 1000:.0f}ms"
            f"{', inverted' if self.reset_invert else ''})")
        self._drive_reset(True)                 # hold CPU in reset
        time.sleep(self.reset_hold)
        self.ser.reset_input_buffer()           # drop the reset-transient noise
        self._drive_reset(False)                # release -> CPU samples straps -> BSL
        time.sleep(self.reset_settle)

    def _reopen(self, log=_emit):
        """Close and reopen the port — recovers an FT232 that Windows USB selective-suspend put
        to sleep, or that wedged after a long idle (a clean reopen re-inits the device)."""
        try:
            self.ser.close()
        except Exception:
            pass
        time.sleep(0.3)
        # Reuse the transport family that was selected for this session.  If
        # D2XX was selected but the adapter became unavailable, _open_transport
        # still falls back safely to pyserial for the retry.
        self._open_transport(prefer_d2xx=(self.transport_name == "d2xx"))
        if self.reset_line:
            try:
                self._drive_reset(False)
            except Exception:
                pass
        log("  FT232 port reopened.")

    # ── BSL entry handshake ────────────────────────────────────────────────
    def sync(self, log=_emit):
        """Send 0x00, wait for the BSL's 0x55.  Returns True on success.

        Safe to call repeatedly.  If this never returns True, the ECU is not in
        BSL mode (check ALE/NMI/RSTIN wiring) or the serial path is wrong.
        """
        self.pulse_reset(log)                   # auto-reset into BSL if --reset-line set
        try:
            self.ser.reset_input_buffer()
            self.ser.write(bytes([self.SYNC_BYTE]))
            self.ser.flush()
        except _SERIAL_ERRS as e:               # FT232 wedged/suspended -> reopen + retry once
            log(f"  serial write stalled ({e.__class__.__name__}: {e}) — reopening the FT232…")
            self._reopen(log)
            self.pulse_reset(log)
            self.ser.reset_input_buffer()
            self.ser.write(bytes([self.SYNC_BYTE]))
            self.ser.flush()
        deadline = time.time() + 3.0
        saw = bytearray()
        while time.time() < deadline:
            b = self.ser.read(1)
            if not b:
                continue
            saw += b
            if b[0] == self.ACK_BYTE:
                log(f"BSL sync OK — got 0x55 (raw: {saw.hex(' ')})")
                return True
            # keep reading past any line noise until the 0x55 (or the timeout)
        log(f"No 0x55 within 3 s (raw: {saw.hex(' ') or '<nothing>'})")
        if saw and getattr(self, "baud", 9600) > 19200:
            log("  hint: a non-empty garbled reply usually means the bootstrap auto-baud "
                "can't lock this high — drop to --speed mid (19200) or slow (9600).")
        return False

    # ── load and run a 0xFA40 stub ─────────────────────────────────────────
    def load_stub(self, stub: bytes):
        """Send the 32-byte BSL payload.  The ECU stores it at 0xFA40 and jumps.

        `stub` may be <=32 bytes; it is zero-padded to exactly 32.
        """
        if len(stub) > 32:
            raise BSLError(f"stub is {len(stub)} bytes; BSL accepts only 32")
        frame = bytes(stub).ljust(32, b"\x00")
        self.ser.reset_input_buffer()
        self.ser.write(frame)
        self.ser.flush()
        # ECU is now executing at 0xFA40

    def read(self, n, timeout=2.0):
        return self._read(n, time.time() + timeout)

    def write(self, data: bytes):
        self.ser.write(bytes(data))
        self.ser.flush()

    # ── load + drive the RAM monitor ───────────────────────────────────────
    def start_monitor(self, log=_emit, flash=False, amd=False):
        """sync -> load the 32-byte preloader -> feed it the monitor -> ping.

        The preloader (loaded by the BSL at 0xFA40) reads exactly MONITOR_LOAD_LEN
        bytes into IRAM 0xFA60 then jumps there.  Returns True if the monitor
        answers a ping with 0xA5.  flash=True loads a flash monitor (P/R/E/F) instead
        of the general MONITOR (P/R/W/C): MONITOR_FLASH_AMD if amd else MONITOR_FLASH —
        same preloader, same 0xFA60 entry.
        """
        if not self.sync(log):
            return False
        log("loading preloader…")
        self.load_stub(PRELOADER)                 # 32-byte BSL frame; BSL jumps to it
        time.sleep(0.05)
        mon = (MONITOR_FLASH_AMD if amd else MONITOR_FLASH) if flash else MONITOR
        label = "FLASH-AMD " if (flash and amd) else "FLASH " if flash else ""
        frame = bytes(mon).ljust(MONITOR_LOAD_LEN, b"\x00")
        log(f"loading {label}monitor ({len(mon)} B, padded to {MONITOR_LOAD_LEN})…")
        self.ser.reset_input_buffer()
        written = self.ser.write(frame)
        if written is not None and written != len(frame):
            raise BSLError(
                f"short monitor write: {written}/{len(frame)} bytes queued")
        self.ser.flush()
        # pyserial.flush() waits for the UART to shift the queued bytes, so its
        # 50 ms delay starts after the complete monitor frame. The shared D2XX
        # shim intentionally leaves flush() non-blocking for echoed DS2 traffic;
        # without an explicit 8N1 wire-time guard, the following ping is placed
        # directly behind the 512-byte frame and arrives before the new monitor
        # has completed its handoff. Keep this drain guard local to hardware BSL
        # so native DS2 and Soft-BSL transfer timing remains unchanged.
        drain_seconds = (
            len(frame) * 10.0 / self.baud
            if self.transport_name == "d2xx" else 0.0
        )
        time.sleep(drain_seconds + 0.05)
        return self.mon_ping(log)

    def mon_ping(self, log=_emit):
        self.ser.reset_input_buffer()
        self.ser.write(b"P")
        self.ser.flush()
        r = self.read(1, timeout=1.0)
        ok = (r == b"\xa5")
        log(f"monitor ping -> {r.hex() or '<nothing>'} ({'OK' if ok else 'FAIL'})")
        return ok

    def mon_read(self, addr, length, progress=None):
        """Read `length` bytes from 24-bit `addr` via the monitor 'R' command.

        Each 'R' sets DPP0 to a 16KB page and reads within it, so we chunk on 16KB
        boundaries (page = addr>>14, in-page offset = addr & 0x3FFF).  With a `progress`
        callback we sub-chunk to 4KB so the bar advances smoothly."""
        out = bytearray()
        total = length
        cap = 4096 if progress else 0x4000
        while length > 0:
            page = (addr >> 14) & 0xFFFF
            off = addr & 0x3FFF
            n = min(length, 0x4000 - off, cap)    # stay inside the DPP0 16KB window
            cmd = bytes([0x52, page & 0xFF, (page >> 8) & 0xFF,
                         off & 0xFF, (off >> 8) & 0xFF, n & 0xFF, (n >> 8) & 0xFF])
            self.ser.reset_input_buffer()
            self.ser.write(cmd)
            self.ser.flush()
            chunk = self.read(n, timeout=2.0 + n * 0.002)
            out += chunk
            if progress:
                progress(len(out), total)
            if len(chunk) < n:                    # short read -> stop
                break
            addr += n
            length -= n
        if progress:
            sys.stdout.write("\n"); sys.stdout.flush()
        return bytes(out)

    def mon_write(self, addr, data):
        """Write `data` to 24-bit `addr` via the monitor 'W' command (RAM/SFR).
        Returns True if every 16KB chunk is ACKed with 'K'."""
        data = bytes(data)
        i = 0
        while i < len(data):
            page = (addr >> 14) & 0xFFFF
            off = addr & 0x3FFF
            n = min(len(data) - i, 0x4000 - off)  # stay inside the DPP0 16KB window
            cmd = bytes([0x57, page & 0xFF, (page >> 8) & 0xFF,
                         off & 0xFF, (off >> 8) & 0xFF, n & 0xFF, (n >> 8) & 0xFF]) + data[i:i + n]
            self.ser.reset_input_buffer()
            self.ser.write(cmd)
            self.ser.flush()
            ack = self.read(1, timeout=2.0)
            if ack != b"\x4b":                     # 'K'
                return False
            addr += n
            i += n
        return True

    def mon_dump(self, start, end, log=_emit, alias_low=False, fill_hole=True, progress=None):
        """Read physical [start,end) via the monitor in 16KB-aligned chunks.

        Only the known CPU 0xC000-0xFFFF ghost window is synthesized as 0xFF. Any short
        transport read outside that exact hole is an error; it must never be converted into
        plausible flash data. In BSL mode 0x0-0x7FFF normally returns the bootstrap-ROM
        shadow (fa 00 00 01…), not the flash; with alias_low=True those chunks are read
        through the +BSL_ALIAS wrap-around so they return the REAL flash.  fill_hole
        0xFF-fills the BSL_HOLE range (unmapped — a raw read floats to an address-ramp;
        true content is 0xFF, matching original/stock dumps).  Returns the bytes
        (indexed by the requested physical address, not the alias)."""
        out = bytearray()
        addr = start
        while addr < end:
            nxt = min((addr & ~0x3FFF) + 0x4000, end)   # to next 16KB boundary or end
            n = nxt - addr
            ra = addr + BSL_ALIAS if (alias_low and addr < BSL_SHADOW_HI) else addr
            ghost_only = (fill_hole and BSL_HOLE[0] <= addr and nxt <= BSL_HOLE[1])
            if ghost_only:
                data = bytearray(b"\xff" * n)
            else:
                data = bytearray(self.mon_read(ra, n))
                if len(data) != n:
                    raise BSLError(
                        f"short flash read @0x{addr:05X}: {len(data)}/{n} bytes; "
                        "refusing to synthesize transport loss as 0xFF")
            note = (f"  (via alias 0x{ra:05X})" if ra != addr else "")
            if fill_hole:                               # 0xFF-fill the unmapped floating hole
                h0, h1 = max(addr, BSL_HOLE[0]), min(nxt, BSL_HOLE[1])
                if h0 < h1:
                    data[h0 - addr:h1 - addr] = b"\xff" * (h1 - h0)
                    note += f"  (0xFF-filled unmapped 0x{h0:05X}-0x{h1 - 1:05X})"
            out += data
            log(f"  dumped 0x{addr:05X}..0x{nxt - 1:05X}" + note)
            addr = nxt
            if progress:
                progress(addr - start, end - start)
        return bytes(out)

    def mon_call(self, addr, timeout=3.0):
        """Call a segment-0 routine at 16-bit `addr` via the monitor 'C' command.
        The routine MUST end in RET.  Returns True if the monitor replies 'D' (0x44)."""
        cmd = bytes([0x43, addr & 0xFF, (addr >> 8) & 0xFF])
        self.ser.reset_input_buffer()
        self.ser.write(cmd)
        self.ser.flush()
        return self.read(1, timeout=timeout) == b"\x44"

    def mon_erase(self, addr, timeout=30.0, log=_emit):
        """Erase the 28F200 block containing 24-bit `addr` via the 'E' command
        (0x20 setup / 0xD0 confirm / poll SR.7 with a nested timeout / 0xFF read-array).
        Returns the final 28F200 status byte, or None on no reply.  Decode:
          SR.7 (0x80)=1 ready (0=timed-out/busy), SR.5 (0x20)=1 erase error,
          SR.3 (0x08)=1 VPP low.  Needs 12V on VPP.  timeout must exceed the monitor's
          own poll budget (~8s) so a rejected/slow erase returns a real SR, not None."""
        page = (addr >> 14) & 0xFFFF
        off = addr & 0x3FFF
        cmd = bytes([0x45, page & 0xFF, (page >> 8) & 0xFF, off & 0xFF, (off >> 8) & 0xFF])
        self.ser.reset_input_buffer()
        self.ser.write(cmd)
        self.ser.flush()
        sr = self.read(1, timeout=timeout)
        return sr[0] if sr else None

    # C166 port SFRs (Pn register, DPn direction) and their bit-addressable offsets
    # (SAB80C166 datasheet: P2=0xFFC0/E0, DP2=0xFFC2/E1, P3=0xFFC4/E2, DP3=0xFFC6/E3).
    _PORT_SFR = {2: (0xFFC0, 0xFFC2), 3: (0xFFC4, 0xFFC6)}
    _BITOFF   = {2: (0xE0, 0xE1), 3: (0xE2, 0xE3)}   # (Pn bitoff, DPn bitoff)
    _VPP_SCRATCH = 0xFC80                             # free IRAM for the bit-op routine

    def set_vpp(self, on, pins, log=_emit):
        """Switch the VPP-gate port bit(s) on/off.  The C166 will NOT write SFRs via the
        monitor's indirect 'W' (SFR reads work, but writes do not persist), so instead
        we BUILD a routine of DIRECT bit ops (bset/bclr DPn.b + Pn.b — the addressing the
        firmware uses), load it to scratch IRAM via 'W' (IRAM writes DO work), and run it
        via 'C'.  bset=(bit<<4)|0xF, bclr=(bit<<4)|0xE.  Reads each Pn back to report the
        result.  Needs a monitor with W+C (the general MONITOR), not the flash monitor."""
        rtn = bytearray()
        for port, bit in pins:
            p_off, dp_off = self._BITOFF[port]
            if on:
                rtn += bytes([(bit << 4) | 0x0F, dp_off])   # bset DPn.bit  (-> output)
                rtn += bytes([(bit << 4) | 0x0F, p_off])    # bset Pn.bit   (-> high)
            else:
                rtn += bytes([(bit << 4) | 0x0E, p_off])    # bclr Pn.bit   (-> low)
        rtn += bytes([0xCB, 0x00])                          # ret
        if not self.mon_write(self._VPP_SCRATCH, bytes(rtn)):
            log("  VPP: routine load (W) not ACKed — monitor has no W?"); return False
        if not self.mon_call(self._VPP_SCRATCH):
            log("  VPP: routine call (C) didn't return — monitor has no C, or bad routine.")
            return False
        for port, bit in pins:                              # read each port back (mon_read works on SFRs)
            v = self.mon_read(self._PORT_SFR[port][0], 2)
            val = (v[0] | (v[1] << 8)) if len(v) == 2 else None
            set_ = (val is not None) and bool(val & (1 << bit))
            log(f"  VPP {'ON ' if on else 'OFF'}: P{port}.{bit} routine ran; read-back "
                f"P{port}=0x{(val or 0):04X} bit{bit}={'1' if set_ else '0'}"
                + ("" if set_ == on else "  (pin may be clamped by its load — trust the meter at VPP)"))
        return True

    def mon_read_id(self, routine=None, result_addr=0xFCC0, log=_emit):
        """Read the flash manufacturer + device ID via a Read-Identifier routine — loaded to IRAM
        scratch with 'W' and launched with 'C' (the general monitor; this needs W+C, like set_vpp).
        Defaults to the 28F200 (Intel) READ_ID_ROUTINE, which writes 0x90, reads chip words 0/1
        through the +0x40000 alias, writes 0xFF (read-array), and stashes the two ID words at 0xFCC0.
        Pass routine=READ_ID_AMD_ROUTINE, result_addr=READ_ID_AMD_RESULT for the 29F200 AMD/JEDEC
        autoselect.  Non-destructive (no VPP).  Returns (manufacturer, device) or (None, None)."""
        routine = READ_ID_ROUTINE if routine is None else routine
        if not self.mon_write(self._VPP_SCRATCH, routine):             # load to 0xFC80
            log("  read-id: routine load (W) not ACKed — monitor has no W?"); return None, None
        if not self.mon_call(self._VPP_SCRATCH):                       # run it
            log("  read-id: routine call (C) didn't return — monitor has no C, or bad routine.")
            return None, None
        res = self.mon_read(result_addr, 4)                            # mfr (LE) + device (LE)
        if len(res) < 4:
            return None, None
        return res[0] | (res[1] << 8), res[2] | (res[3] << 8)

    def mon_program(self, addr, data, log=_emit, progress=None):
        """Program `data` to flash at 24-bit `addr` via the 'F' command (28F200
        word-program: per word 0x40 / data word / poll SR.7).  Chunks on 16KB DPP0
        pages; `addr` and every chunk length stay word-aligned (even). D2XX and
        progress-reporting callers sub-chunk to 4KB. Returns True iff every chunk
        is ACKed 'K'. Needs 12V on VPP/RP# for an Intel 28F200."""
        data = bytes(data)
        if (addr & 1) or (len(data) & 1):
            raise ValueError("flash program needs an even addr and length (16-bit words)")
        i = 0
        total = len(data)
        # A 16 KB frame needs ~4.27 s on the wire at 38,400 baud, longer than
        # the shared D2XX shim's 3 s write timeout. The GUI already selects
        # 4 KB through its progress callback, but correctness must not depend
        # on whether a caller requested progress reporting.
        cap = 4096 if (progress or self.transport_name == "d2xx") else 0x4000
        while i < len(data):
            page = (addr >> 14) & 0xFFFF
            off = addr & 0x3FFF
            n = min(len(data) - i, 0x4000 - off, cap)
            if n & 1:                              # keep chunks word-aligned
                n -= 1
            cmd = bytes([0x46, page & 0xFF, (page >> 8) & 0xFF,
                         off & 0xFF, (off >> 8) & 0xFF, n & 0xFF, (n >> 8) & 0xFF]) + data[i:i + n]
            self.ser.reset_input_buffer()
            written = self.ser.write(cmd)
            if written is not None and written != len(cmd):
                if progress:
                    sys.stdout.write("\n"); sys.stdout.flush()
                log(
                    f"  program @0x{addr:05X} x{n}: SHORT TRANSPORT WRITE "
                    f"({written}/{len(cmd)} command bytes)"
                )
                return False
            self.ser.flush()
            ack = self.read(1, timeout=5.0 + n * 0.003)
            if ack != b"\x4b":                     # 'K'
                if progress:
                    sys.stdout.write("\n"); sys.stdout.flush()
                log(f"  program @0x{addr:05X} x{n}: NO ACK (got {ack.hex() or '<nothing>'})")
                return False
            addr += n
            i += n
            if progress:
                progress(i, total)
        if progress:
            sys.stdout.write("\n"); sys.stdout.flush()
        return True

    @staticmethod
    def list_ports():
        return [p.device for p in _list_ports.comports()] if serial else []


# ── HELLO stub — proves loaded code runs ────────────────────────────────────
# @0xFA40: send 0xA5 once on ASC0, then spin.  Uses SFR SHORT addressing so it
# does NOT depend on the BSL's DPP3 value.
#   E6 58 A5 00   mov  S0TBUF, #0x00A5   ; short addr 0x58 = S0TBUF (0xFEB0)
#   0D FF         jmpr cc_UC, $          ; spin (the 0xA5 is sent before the spin)
HELLO_STUB = bytes.fromhex("E658A500" "0DFF")

def _bsl_frame(base: bytes) -> bytes:
    """Zero-pad a stub to the 32-byte BSL payload the ECU loads at 0xFA40."""
    return bytes(base).ljust(32, b"\x00")


def _send_load(bsl, frame):
    """Send the 32-byte BSL load over the full-duplex tap."""
    bsl.ser.reset_input_buffer()
    bsl.ser.write(frame)
    bsl.ser.flush()


# ── PRELOADER + MONITOR (the BSL loads PRELOADER at 0xFA40) ──────────────────
# PRELOADER receives exactly MONITOR_LOAD_LEN bytes over ASC0 into IRAM 0xFA60..
# then jumps to 0xFA60.
PRELOADER = bytes.fromhex("E6F060FA9AB7FE70A400B2FE7EB786F05FFC3DF8EA0060FA")
MONITOR_LOAD_LEN = 512
# MONITOR (loaded at 0xFA60): ASC0 command loop, DPP0 paging.
#   'P'                                   -> reply 0xA5  (ping)
#   'R' pgLo pgHi offLo offHi lenLo lenHi -> set DPP0=page; reply <len> bytes from [off]
#   'W' ... <len data>                    -> set DPP0=page; store at [off]; reply 'K'
#   'C' offLo offHi                       -> calli a RAM routine (must 'ret'); reply 'D' (0x44)
#   page=addr>>14, off=addr&0x3FFF; host chunks so off+len<=0x4000.
MONITOR = bytes.fromhex(
    "9AB7FE70F3F0B2FE7EB747F050002D0B47F052002D0F47F057002D3847F0"
    "43002D67EA0060FAE658A5009AB6FE707EB6EA0060FA9AB7FE70F3F4B2FE"
    "7EB79AB7FE70F3F5B2FE7EB79AB7FE70F3F8B2FE7EB79AB7FE70F3F9B2FE"
    "7EB79AB7FE70F3FCB2FE7EB79AB7FE70F3FDB2FE7EB7F6F200FE4860EA20"
    "60FAA9E47EB6F6F7B0FE9AB6FE70084128610DF49AB7FE70F3F4B2FE7EB7"
    "9AB7FE70F3F5B2FE7EB79AB7FE70F3F8B2FE7EB79AB7FE70F3F9B2FE7EB7"
    "9AB7FE70F3FCB2FE7EB79AB7FE70F3FDB2FE7EB7F6F200FE48602D099AB7"
    "FE70F3FEB2FE7EB7B9E4084128610DF57EB6E6584B009AB6FE70EA0060FA"
    "9AB7FE70F3F8B2FE7EB79AB7FE70F3F9B2FE7EB7AB047EB6E65844009AB6"
    "FE70EA0060FA")
# MONITOR_FLASH (loaded at 0xFA60 in place of MONITOR): P/R + 28F200 erase/program.
# Stack-free (no calli/push) so it's safe to fill IRAM.  Intel 28F200 command set:
#   'P'                                   -> 0xA5 (ping)
#   'R' pgLo pgHi offLo offHi lenLo lenHi -> set DPP0=page; reply <len> bytes (read-back)
#   'E' pgLo pgHi offLo offHi             -> 0x20/0xD0 erase block, poll SR.7; reply SR byte
#   'F' pgLo pgHi offLo offHi lenLo lenHi <data> -> per word 0x40/data/poll SR.7; reply 'K'
#                                            (len + off must be EVEN)
# Entry enables the WR# alternate output (bset DP3.13+P3.13) — the BSL leaves WR# high-Z so
# external writes never strobe the flash WE# (reads work via the dedicated RD# pin); without
# it, erase/program silently no-op.  E turns VPP on (P2.6) and leaves it on; F streams data
# with no settle delay (a delay there overruns the 1-byte UART RX).  NEEDS 12V on VPP/RP#.
MONITOR_FLASH = bytes.fromhex(
    "DFE3DFE29AB7FE70F3F0B2FE7EB747F05000EA2092FA47F05200EA20A0FA47F0"
    "4500EA20F8FA47F04600EA207CFBEA0064FAE658A5009AB6FE707EB6EA0064FA"
    "9AB7FE70F3F4B2FE7EB79AB7FE70F3F5B2FE7EB79AB7FE70F3F8B2FE7EB79AB7"
    "FE70F3F9B2FE7EB79AB7FE70F3FCB2FE7EB79AB7FE70F3FDB2FE7EB7F6F200FE"
    "4860EA2064FAA9E47EB6F6F7B0FE9AB6FE70084128610DF49AB7FE70F3F4B2FE"
    "7EB79AB7FE70F3F5B2FE7EB79AB7FE70F3F8B2FE7EB79AB7FE70F3F9B2FE7EB7"
    "F6F200FE6FE16FE06FE36FE2E6F32000E6F1FFFF28113DFE28313DFAE6F52000"
    "B854E6F5D000B854E6F64000E6F1FFFFE6F57000B854A9E4F00767F080003D04"
    "28113DF628613DF2E6F5FF00B8547EB6F6F7B0FE9AB6FE70EA0064FA9AB7FE70"
    "F3F4B2FE7EB79AB7FE70F3F5B2FE7EB79AB7FE70F3F8B2FE7EB79AB7FE70F3F9"
    "B2FE7EB79AB7FE70F3FCB2FE7EB79AB7FE70F3FDB2FE7EB7F6F200FE6FE16FE0"
    "6FE36FE24860EA2002FC9AB7FE70F3FEB2FE7EB79AB7FE70F3FFB2FE7EB7E6F5"
    "4000B854B874E6F1FFFFE6F57000B854A90467F080003D0228113DF708422862"
    "0DE1E6F5FF00B8547EB6E6584B009AB6FE70EA0064FA")
# MONITOR_FLASH_AMD (470 B): the AMD/JEDEC 29F200/29F400 flash monitor — same P/R/E/F protocol and
# same MAIN/ping/read as MONITOR_FLASH, but E/F use the AMD command set (unlock AA@0x555/55@0x2AA,
# erase 80/AA/55/30@sector, program A0+data) with DQ6 toggle-bit + DQ5 polling, and NO VPP (29F2/4xx
# is single-supply, and on the retrofit board the 12V net feeds the chip's RESET#).  WORD command/
# data writes (strobe WE#); BYTE status reads (DQ6/DQ5 in the low byte).  Word cmd addrs 0x555/0x2AA
# are byte offsets 0xAAA/0x554 (x16 flash, flash A0 = CPU A1).  E replies a synthesized Intel-style
# SR (0x80 ok / 0x20 fail) so the host's _decode_sr/erase check is reused unchanged; F replies 'K'.
# asm/monitor_flash_amd.asm, Ghidra-built; == .hex byte-for-byte.  MONITOR_FLASH (28F200) is untouched.
MONITOR_FLASH_AMD = bytes.fromhex(
    "DFE3DFE29AB7FE70F3F0B2FE7EB747F05000EA2092FA47F05200EA20A0FA47F04500EA20F8FA47F04600EA2092FB"
    "EA0064FAE658A5009AB6FE707EB6EA0064FA9AB7FE70F3F4B2FE7EB79AB7FE70F3F5B2FE7EB79AB7FE70F3F8B2FE"
    "7EB79AB7FE70F3F9B2FE7EB79AB7FE70F3FCB2FE7EB79AB7FE70F3FDB2FE7EB7F6F200FE4860EA2064FAA9E47EB6"
    "F6F7B0FE9AB6FE70084128610DF49AB7FE70F3F4B2FE7EB79AB7FE70F3F5B2FE7EB79AB7FE70F3F8B2FE7EB79AB7"
    "FE70F3F9B2FE7EB7F6F200FEE6F25405E6F3AA0AE6F5AA00B853E6F55500B852E6F58000B853E6F5AA00B853E6F5"
    "5500B852E6F53000B854E6F0002028013DFEE6F68000E6F1FFFFA904A9A4510A67F040002D0A67FA20003D042811"
    "3DF528613DF1E6F720000D02E6F780007EB6F6F7B0FE9AB6FE70EA0064FA9AB7FE70F3F4B2FE7EB79AB7FE70F3F5"
    "B2FE7EB79AB7FE70F3F8B2FE7EB79AB7FE70F3F9B2FE7EB79AB7FE70F3FCB2FE7EB79AB7FE70F3FDB2FE7EB7F6F2"
    "00FEE6F25405E6F3AA0A4860EA2028FC9AB7FE70F3FEB2FE7EB79AB7FE70F3FFB2FE7EB7E6F5AA00B853E6F55500"
    "B852E6F5A000B853B874E6F1FFFFA904A9A4510A67F040002D0567FA20003D0228113DF5084228620DD97EB6E658"
    "4B009AB6FE70EA0064FA")
# READ_ID_ROUTINE (44 B, loaded to IRAM scratch 0xFC80 via the general monitor's 'W', run via
# 'C'): enable WR# (bset DP3.13/P3.13) / DPP0=0x11 / write 0x90 (Read ID) / read chip words 0,1
# through the +0x40000 alias / write 0xFF (read-array) / store the two ID words at 0xFCC0.
# Non-destructive — no VPP, no erase/program.  asm/read_id.asm, Ghidra-built; does NOT touch
# MONITOR_FLASH (the proven flash monitor stays byte-for-byte unchanged).
READ_ID_ROUTINE = bytes.fromhex(
    "DFE3DFE2E6001100E004E6F59000B854A864E024A874E004E6F5FF00B854E6F4C0FCB864E6F4C2FCB874CB00")
# READ_ID_AMD_ROUTINE (68 B): the 29F200 (AMD/JEDEC) autoselect, same harness as READ_ID_ROUTINE
# but the AMD command set instead of Intel's single 0x90: unlock AA->word 0x555 / 55->word 0x2AA,
# autoselect 90->word 0x555, read mfr (word 0) + device (word 1), reset F0.  The flash is x16 on a
# byte bus (flash A0 = CPU A1) so the WORD addrs 0x555/0x2AA are BYTE offsets 0xAAA/0x554; the GAL
# only inverts A14 (above A10) so the command decode is unaffected, and like the Intel routine it
# goes through the +0x40000 shadow alias (chip0 = CPU 0x4000, BSL-shadowed).  Results land at
# 0xFCE0/0xFCE2 (NOT 0xFCC0 — the longer AMD body would overwrite its own tail there).  Non-
# destructive: no VPP, no erase/program; F0 restores read-array.  asm/read_id_amd.asm, Ghidra-built.
READ_ID_AMD_ROUTINE = bytes.fromhex(
    "DFE3DFE2E6001100E6F4AA0AE6F5AA00B854E6F45405E6F55500B854E6F4AA0AE6F59000B854E004A864E024"
    "A874E004E6F5F000B854E6F4E0FCB864E6F4E2FCB874CB00")
READ_ID_AMD_RESULT = 0xFCE0    # where READ_ID_AMD_ROUTINE stashes (mfr, device); host reads it back


def _bsl(args):
    """Build a BSL session from a typed request or optional CLI namespace."""
    bsl = BSL(args.port, args.baud,
              reset_line=args.reset_line, reset_hold=args.reset_ms / 1000.0,
              reset_settle=args.reset_settle / 1000.0, reset_invert=args.reset_invert)
    registry = getattr(args, "session_registry", None)
    if registry is not None:
        registry.append(bsl)
    return bsl


def _monitor(args):
    """Build a BSL and bring up the general RAM monitor (P/R/W/C); None on failure."""
    bsl = _bsl(args)
    if not bsl.start_monitor():
        _emit("RESULT: monitor did not come up (no 0xA5 ping). Check the load timing / wiring.")
        return None
    return bsl


def cmd_sync(args):
    """Confirm BSL entry (0x55) AND that loaded code runs (HELLO stub -> 0xA5)."""
    bsl = _bsl(args)
    if not bsl.sync():
        _emit("RESULT: no response — BSL did not enter (check ALE/NMI#/RSTIN# and the tap).")
        return 1
    _emit("BSL entered (0x55); loading the HELLO stub to confirm loaded code runs…")
    _send_load(bsl, _bsl_frame(HELLO_STUB))
    data = bsl.read(128, timeout=2.0)
    _emit(f"  raw          : {data.hex(' ') or '<nothing>'}")
    seen = 0xA5 in data
    _emit("RESULT:", "BSL OK + loaded code RUNS (0xA5 seen)" if seen else
          "BSL entered but the stub did NOT run (no 0xA5) — check the full-duplex tap")
    return 0 if seen else 1


def cmd_read(args):
    bsl = _monitor(args)
    if not bsl:
        return 1
    addr_s, _, len_s = args.addr_len.partition(":")
    addr, length = int(addr_s, 0), int(len_s, 0)
    data = bsl.mon_read(addr, length)
    _emit(f"  read 0x{addr:X} x{length}: {data.hex(' ') or '<nothing>'}")
    ok = len(data) == length
    _emit("RESULT:", "READ OK" if ok else f"SHORT READ ({len(data)}/{length})")
    return 0 if ok else 1


def cmd_write(args):
    bsl = _monitor(args)
    if not bsl:
        return 1
    addr_s, _, hex_s = args.addr_hex.partition(":")
    addr = int(addr_s, 0)
    payload = bytes.fromhex(hex_s.replace(" ", ""))
    if not bsl.mon_write(addr, payload):
        _emit("RESULT: write not ACKed"); return 1
    back = bsl.mon_read(addr, len(payload))
    _emit(f"  wrote {len(payload)} B to 0x{addr:X}; read-back {back.hex(' ') or '<nothing>'} "
          f"(expected {payload.hex(' ')})")
    match = (back == payload)
    _emit("RESULT:", "WRITE VERIFIED (read-back matches)" if match else "WRITE MISMATCH")
    return 0 if match else 1


def cmd_dump(args):
    bsl = _monitor(args)
    if not bsl:
        return 1
    if getattr(args, "partial", False):
        lo, hi = 0x10000, 0x16000       # DS2 0x10000-0x15FFF = the 24 KB cal/tune partition
        _emit(f"dumping the 24 KB cal/tune partial (DS2 0x{lo:05X}-0x{hi - 1:05X}, CPU/DS2 order)…")
        data = bsl.mon_dump(
            lo, hi, alias_low=False, fill_hole=False,
            progress=_progress_for(args, "BSL tune dump"))   # tune has no hole; no swap
        with open(args.file, "wb") as f:
            f.write(bytes(data))
        _emit(f"RESULT: wrote {len(data)} bytes (24 KB CPU-order cal partial) to {args.file} — "
              f"flash it with `flash tune --ref {args.file}`.")
        return 0
    start, end = 0x0, 0x40000           # whole chip by default
    if args.range:
        s, _, e = args.range.partition(":")
        start, end = int(s, 0), int(e, 0)
    # auto shadow-bypass: if the dump touches 0x0-0x7FFF and the direct read isn't real flash
    # (the BSL boot-ROM shadow on the 28F200, OR plain 0xFF on the 29F400 retrofit) while the
    # +0x40000 wrap-around does read flash, route the low range through the alias.  Decide from
    # CONTENT, not the boot-ROM signature, so it works on both chips (mirrors _flash_region).
    alias_low = False
    if start < BSL_SHADOW_HI and not args.no_alias:
        direct = bsl.mon_read(0x0, 16)
        alias  = bsl.mon_read(BSL_ALIAS, 16)

        def _is_flash(d):   # plausibly real flash: not the boot-ROM shadow, not blank 0xFF
            return len(d) >= 4 and d[:4] != BSL_BOOTROM_SIG and any(b != 0xFF for b in d)

        if _is_flash(direct):
            _emit("  0x0-0x7FFF reads real flash directly — no alias needed.")
        elif _is_flash(alias):
            alias_low = True
            _emit(f"  0x0-0x7FFF is shadowed/blank but the +0x{BSL_ALIAS:X} alias reads real flash "
                  f"— routing the low range through the wrap-around.")
        elif alias[:4] != BSL_BOOTROM_SIG:
            alias_low = True
            _emit(f"  0x0-0x7FFF blank/ambiguous — routing the low range through the +0x{BSL_ALIAS:X} "
                  f"alias (the shadowed-low default).")
        else:
            _emit("  0x0-0x7FFF shadowed and the alias didn't read flash — the low range will be "
                  "the raw shadow.")
    _emit(f"dumping physical 0x{start:X}..0x{end:X} ({end - start} bytes) — "
          f"~{(end - start) * 10 / args.baud:.0f}s at {args.baud} baud…")
    if not args.raw_hole and start < BSL_HOLE[1] and end > BSL_HOLE[0]:
        _emit(f"  0xFF-filling the unmapped hole 0x{BSL_HOLE[0]:05X}-0x{BSL_HOLE[1] - 1:05X} "
              f"(--raw-hole keeps the raw float).")
    data = bsl.mon_dump(
        start, end, alias_low=alias_low, fill_hole=not args.raw_hole,
        progress=_progress_for(args, "BSL dump"))
    full = bytearray(b"\xff" * end)                 # physical/CPU-indexed image
    full[start:end] = data
    # Default is file/chip order (bench-flashable, standard .bin); --cpu-order keeps the raw image.
    if args.cpu_order:
        order = "physical/CPU (raw — NOT directly re-flashable; omit --cpu-order for a flashable .bin)"
    else:
        if end % 0x8000:
            _emit(f"  NOTE: file-order needs a 0x8000-aligned end (0x{end:X} isn't); the trailing "
                  f"odd 16KB block can't be paired — pass --cpu-order for a raw dump.")
        full = bytearray(_swap_block_order(full))   # CPU/physical -> file/chip-physical order
        order = "file/chip-physical (bench-flashable)"
    with open(args.file, "wb") as f:
        f.write(full)
    _emit(f"RESULT: wrote {len(full)} bytes to {args.file} in {order} order"
          + (", 0x0-0x7FFF via alias" if alias_low else "") + ".")
    return 0


def cmd_verify_alias(args):
    bsl = _monitor(args)
    if not bsl:
        return 1
    # Self-contained proof the +0x40000 wrap-around reads the REAL flash (no reference file):
    # the directly-readable flash (>=0x8000) must read IDENTICALLY through its high alias.
    _emit("ALIAS SELF-CHECK — does the +0x%05X wrap-around reach real flash?" % BSL_ALIAS)
    d0, a0 = bsl.mon_read(0x0, 16), bsl.mon_read(BSL_ALIAS, 16)
    _emit(f"  phys 0x00000 direct : {d0.hex(' ') or '<nothing>'}")
    _emit(f"  phys 0x{BSL_ALIAS:05X} alias  : {a0.hex(' ') or '<nothing>'}")
    shadowed = d0[:4] == BSL_BOOTROM_SIG
    alias_flash = a0[:4] != BSL_BOOTROM_SIG and any(b != 0xFF for b in a0)
    _emit(f"  0x0 is {'the BSL boot-ROM shadow' if shadowed else 'directly readable'}; "
          f"alias reads {'REAL FLASH' if alias_flash else 'NOT flash (boot-ROM/empty)'}")
    _emit("  wrap cross-check (directly-readable flash vs its +alias — must MATCH):")
    allmatch = True
    for p in (0x08000, 0x10000, 0x18000, 0x20000, 0x30000, 0x3FFF0):
        dd, aa = bsl.mon_read(p, 16), bsl.mon_read(p + BSL_ALIAS, 16)
        ok = dd == aa and len(dd) == 16
        allmatch = allmatch and ok
        _emit(f"    0x{p:05X} vs 0x{p + BSL_ALIAS:05X}: {'MATCH' if ok else 'DIFF '}  {dd.hex(' ')}")
    good = alias_flash and allmatch
    _emit(f"RESULT: alias read path {'CONFIRMED RELIABLE' if good else 'NOT confirmed'} "
          f"(reaches-flash={alias_flash}, wrap-consistent={allmatch}). "
          + ("Safe to read/flash 0x0-0x7FFF via the alias." if good else
             "Do NOT trust alias writes until this passes."))
    return 0 if good else 1


def cmd_vpp_on(args):
    bsl = _monitor(args)
    if not bsl:
        return 1
    ok = bsl.set_vpp(True, [(2, 6)])                # VPP gate = P2.6
    _emit("RESULT: VPP rail switched ON via P2.6"
          + ("" if ok else " (a port op failed — check the monitor)") +
          ".\n  Measure ~12V at the 28F200 VPP pin now. Any other command resets the pin.")
    return 0 if ok else 1


def cmd_businfo(args):
    bsl = _monitor(args)
    if not bsl:
        return 1
    complete = True
    for name, a in (("SYSCON", 0xFF12), ("BUSCON0", 0xFF0C), ("BUSCON1", 0xFF14),
                    ("ADDRSEL1", 0xFE18), ("BUSCON2", 0xFF16), ("ADDRSEL2", 0xFE1A)):
        v = bsl.mon_read(a, 2)
        complete = complete and len(v) == 2
        val = f"0x{int.from_bytes(v, 'little'):04X}" if len(v) == 2 else "<short>"
        _emit(f"  {name:8} @0x{a:04X} = {val}   raw {v.hex(' ') or '--'}")
    _emit("RESULT:", "bus config read" if complete else "INCOMPLETE bus config read")
    return 0 if complete else 1


# JEDEC manufacturer codes seen on MS41-class 2 Mbit boot-block flash.
_FLASH_MFR = {
    0x0089: "Intel",          0x0001: "AMD/Spansion", 0x0004: "Fujitsu",
    0x0020: "STMicro",        0x00C2: "Macronix",     0x00BF: "SST",
    0x001F: "Atmel",          0x0037: "AMIC",
}
# Known boot-block flash IDs. Intel uses its status-register command set; the
# AMD/Fujitsu/ST/Macronix second-source parts share the AMD/JEDEC command set.
_FLASH_IDS = {
    (0x0089, 0x2274): "Intel 28F200BX-T  (2 Mbit, top-boot)",
    (0x0089, 0x2275): "Intel 28F200BX-B  (2 Mbit, bottom-boot)",
    # 29F200 family — 2 Mbit / 256 KB:
    (0x0001, 0x2257): "AMD Am29F200BB  (2 Mbit, bottom-boot)",
    (0x0001, 0x2251): "AMD Am29F200BT  (2 Mbit, top-boot)",
    (0x0004, 0x2257): "Fujitsu MBM29F200BC  (2 Mbit, bottom-boot)",
    (0x0004, 0x2251): "Fujitsu MBM29F200TC  (2 Mbit, top-boot)",
    (0x0020, 0x2257): "ST M29F200BB  (2 Mbit, bottom-boot)",
    (0x0020, 0x2251): "ST M29F200BT  (2 Mbit, top-boot)",
    (0x00C2, 0x2257): "Macronix MX29F200B  (2 Mbit, bottom-boot)",
    (0x00C2, 0x2251): "Macronix MX29F200T  (2 Mbit, top-boot)",
    # 29F400 family — 4 Mbit / 512 KB (same AMD/JEDEC command set):
    (0x0001, 0x22AB): "AMD Am29F400BB  (4 Mbit, bottom-boot)",
    (0x0001, 0x2223): "AMD Am29F400BT  (4 Mbit, top-boot)",
    (0x0004, 0x22AB): "Fujitsu MBM29F400BC  (4 Mbit, bottom-boot)",
    (0x0004, 0x2223): "Fujitsu MBM29F400TC  (4 Mbit, top-boot)",
    (0x00C2, 0x22AB): "Macronix MX29F400B  (4 Mbit, bottom-boot)",
    (0x00C2, 0x2223): "Macronix MX29F400T  (4 Mbit, top-boot)",
}


_CMDSET_NAME = {"intel": "Intel", "amd": "AMD/JEDEC"}   # for the per-method readout label


def cmd_id(args):
    bsl = _monitor(args)
    if not bsl:
        return 1
    # which command set(s) to try: a known --chip forces one; 'auto' tries Intel then AMD.
    order = {"28f200": ["intel"], "29f200": ["amd"], "29f400": ["amd"],
             "auto": ["intel", "amd"]}[args.chip]
    match = None
    for kind in order:
        if kind == "amd":
            mfr, dev = bsl.mon_read_id(READ_ID_AMD_ROUTINE, READ_ID_AMD_RESULT)
        else:
            mfr, dev = bsl.mon_read_id()                       # 28F200 / Intel default
        cmdset = _CMDSET_NAME[kind]
        if mfr is None:
            _emit(f"  [{cmdset:9}] read-id routine failed to load/run.")
            continue
        name = _FLASH_IDS.get((mfr, dev))
        mfr_name = _FLASH_MFR.get(mfr)
        _emit(f"  [{cmdset:9}] manufacturer 0x{mfr:04X}"
              f"{'  (' + mfr_name + ')' if mfr_name else ''}   device 0x{dev:04X}"
              f"{'   = ' + name if name else ''}")
        if name:
            match = (cmdset, mfr, dev, name)
            break
    if match:
        cmdset, mfr, dev, name = match
        _emit(f"RESULT: {name} — confirmed via the {cmdset} command set.")
        return 0
    _emit("RESULT: ID read back, but no (manufacturer, device) matched the known table. If a real "
          "manufacturer code shows above (e.g. 0x0001 AMD / 0x0004 Fujitsu / 0x0020 ST) with an "
          "unlisted device, note it and we'll add the exact part. If both methods returned "
          "flash-looking bytes, the Read-ID command didn't latch (WR#/alias) — check the tap.")
    return 1


# ── 28F200 flasher ──────────────────────────────────────────────────────────
# 28F200 erase-BLOCK map in CPU addresses.  A GAL on the board inverts CPU A14 to drive
# flash A13 (Flash A13 = !CPU A14), so chip_addr = cpu_addr XOR 0x4000 — every 16KB block
# is swapped with its neighbour.  The monitor drives CPU addresses and the chip latches the
# erase block from the CHIP address, so the CPU ranges below are the datasheet's chip blocks
# XOR 0x4000 (hence the chip's HW-locked boot block sits at CPU 0x4000-0x7FFF, while the CPU
# reset vectors `fa 00 30 04` live in a param block).  Erase is block-granular; each `span`
# is one full block, programmed back from the reference.
#
#   CPU range        chip block (XOR 0x4000)  size  region
#   0x00000-0x01FFF  param1 (chip 0x4000)      8K   boot (reset/trap vectors)
#   0x02000-0x03FFF  param2 (chip 0x6000)      8K   program-low
#   0x04000-0x07FFF  BOOT A (chip 0x0000)     16K   program-mid  **HW-locked, RP#=12V**
#   0x08000-0x1FFFF  main D (chip 0x8000)     96K   tune (cal); CPU 0xC000-0xFFFF unmapped (hole)
#   0x20000-0x3FFFF  main E (chip 0x20000)   128K   program-high
#
# `low=True` = block in the BSL-shadowed CPU 0x0-0x7FFF; cmd_flash routes its ops through the
# +0x40000 wrap-around alias.  `rp12=True` = the chip's HW-locked boot block (needs RP#=12V,
# which shares the VPP net here).  Reference .bin is file/chip order; CPU<->file is XOR 0x4000.
BSL_BOOTROM_SIG = bytes.fromhex("fa000001")   # phys 0x0 in BSL mode if 0x0-0x7FFF is shadowed
BSL_SHADOW_HI   = 0x8000                       # the bootstrap-ROM overlay covers phys 0x0-0x7FFF
# SHADOW BYPASS: the 80C166W external bus is ~256KB and WRAPS, so physical addr+0x40000 presents
# addr&0x3FFFF on the wire but lands OUTSIDE the 0x0-0x7FFF overlay slot — so the CPU runs a real
# external cycle and the flash drives the REAL low data (reading 0x40000 returns the flash boot
# vectors, not the boot-ROM).  The low blocks are read/erased/programmed/verified through +0x40000.
BSL_ALIAS = 0x40000
# UNMAPPED HOLE (CPU 0xC000-0xFFFF): no chip drives the bus here, so a raw read returns a floating
# address-ramp; the true content is 0xFF (matches stock dumps).  Dumps 0xFF-fill it (--raw-hole
# keeps the raw float) and the flasher never writes/verifies it (the tune region `hole`).
BSL_HOLE = (0x0C000, 0x10000)


def _real_ranges(lo, hi, hole=None):
    """Return mapped flash subranges after subtracting the known CPU ghost window."""
    if not hole or hi <= hole[0] or lo >= hole[1]:
        return [(lo, hi)]
    ranges = []
    if lo < hole[0]:
        ranges.append((lo, min(hi, hole[0])))
    if hi > hole[1]:
        ranges.append((max(lo, hole[1]), hi))
    return ranges


def _progress_for(args, label):
    external = getattr(args, "progress_cb", None)
    if external:
        return lambda done, total: external(done, total, label)
    return _make_bar(label)


def _read_mapped_span(bsl, lo, hi, *, hole=None, alias=0, progress=None):
    """Read every real flash byte exactly and synthesize only the explicit ghost window."""
    out = bytearray(b"\xff" * (hi - lo))
    ranges = _real_ranges(lo, hi, hole)
    total = sum(end - start for start, end in ranges)
    completed = 0
    for start, end in ranges:
        size = end - start
        segment_progress = None
        if progress:
            segment_progress = lambda done, _size, base=completed: progress(base + done, total)
        data = bytes(bsl.mon_read(start + alias, size, progress=segment_progress))
        if len(data) != size:
            raise BSLError(
                f"short flash read @0x{start:05X}: {len(data)}/{size} bytes; "
                "no erase/program decision can be made")
        out[start - lo:end - lo] = data
        completed += size
    if progress:
        progress(total, total)
    return bytes(out)


def _save_region_backup(args, region, plan, physical_data):
    """Save a unique, directly reusable full file-order container for this region backup."""
    physical = bytearray(b"\xff" * 0x40000)
    physical[plan["lo"]:plan["hi"]] = physical_data
    file_order = _swap_block_order(physical)
    directory = getattr(args, "backup_dir", None) or os.path.join("backups", "bsl")
    os.makedirs(directory, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    filename = (f"bsl_{args.chip}_{getattr(args, 'half', 'upper')}_{region}_"
                f"0x{plan['lo']:05X}_{stamp}.bin")
    path = os.path.join(directory, filename)
    with open(path, "xb") as handle:
        handle.write(file_order)
    return path
FLASH_REGIONS = {
    "boot":         dict(erase=0x00000, span=(0x00000, 0x02000), low=True),             # ->param1 8K: vectors
    "program-low":  dict(erase=0x02000, span=(0x02000, 0x04000), low=True),             # ->param2 8K
    "program-mid":  dict(erase=0x04000, span=(0x04000, 0x08000), low=True, rp12=True),  # ->BOOT blk A 16K (HW-lock)
    "tune":         dict(erase=0x10000, span=(0x08000, 0x20000), hole=BSL_HOLE),  # ->main D 96K cal/tune; CPU 0xC000-0xFFFF unmapped (BSL_HOLE)
    "program-high": dict(erase=0x20000, span=(0x20000, 0x40000)),                       # ->main E 128K
}
# 29F400 (AMD/JEDEC) erase-SECTOR map for the FACTORY strap (Flash A17 HIGH).  On the Am29F400BB
# (bottom-boot, 512 KB) A17-high routes the CPU to the chip's UPPER 256 KB = FOUR UNIFORM 64 KB
# sectors (datasheet Table 3, A17=1): SA7 (CPU 0x0-0xFFFF), SA8 (0x10000-0x1FFFF), SA9 (0x20000-
# 0x2FFFF), SA10 (0x30000-0x3FFFF).  The fine boot sectors (16K/8K/8K/32K) sit in the UNUSED lower
# half.  CPU addr = chip XOR 0x4000; erase is sector-granular (AMD 0x30@sector); single-supply (NO 12V).
# Region -> sector mapping:
#   `low`          = SA7 (64 KB): its low 32 KB (CPU 0x0-0x7FFF) holds boot+program-low+bootloader+
#                    drivers; the upper 32 KB (0x8000-0xFFFF) is the empty gap + unmapped hole (left erased).
#   `tune`         = SA8 (64 KB): cal.
#   `program-high` = SA9 + SA10 (two 64 KB): main maps.
# A genuine 29F200 (256 KB, no A17 half selection) exposes its bottom-boot
# small sectors instead. This upper map is 29F400-specific; the A17-low map is below.
FLASH_REGIONS_AMD = {
    # ★ CPU 0x0-0xFFFF is ONE 64K sector (the chip's SA7).  WHY: this board straps the 29F400's A17
    # HIGH — the 256K 28F200 it replaced had no A17, so the board never routed it (netlist: Flash A16
    # = CPU A17, Flash A17 = pull-up).  A17 high => the CPU only sees the chip's UPPER 256K, which on
    # a bottom-boot Am29F400B is four UNIFORM 64K sectors (SA7-SA10) — the small boot sectors sit in
    # the unused LOWER half.  So boot + program-low + program-mid all live inside SA7 and erase as a
    # unit; tune is one sector and program-high spans two sectors.
    # `low` IS that 64K sector: one erase clears it; we program/verify only the 0x0-0x7FFF data
    # (0x8000-0xFFFF is the empty gap + the unmapped hole).  A per-region flash of boot/program-low/
    # program-mid separately would brick — they aren't separate sectors here.  (No WP#: SA7 is a
    # middle upper-half sector, not one of the two WP#-protectable outermost boot sectors.)
    "low":          dict(erase=0x00000, span=(0x00000, 0x08000), low=True),
    "tune":         dict(erase=0x10000, span=(0x10000, 0x20000)),                      # SA8 64K (CPU 0x10000-0x1FFFF): cal/tune (no hole)
    "program-high": dict(erase=(0x20000, 0x30000), span=(0x20000, 0x40000)),           # SA9+SA10 (two 64K): two erases
}


# A17-low (lower-half) AMD map for compatible retrofit wiring. The factory board
# straps Flash A17 HIGH (pull-up), so the CPU sees the chip's UPPER 256K = four uniform 64K sectors (the
# FLASH_REGIONS_AMD map above).  If A17 is REWIRED LOW, the CPU instead sees the chip's LOWER 256K, which
# on a bottom-boot Am29F400B is the REAL boot-block region: small sectors SA1 8K (CPU 0x0, vectors) / SA2
# 8K (CPU 0x2000) / SA0 16K (CPU 0x4000) + SA4-6 64K.  Then boot/program-low/program-mid are SEPARATE
# sectors that erase individually (no 64K group erase), and program-mid (SA0) is one of the two
# WP#-protectable outermost boot sectors.  tune/program-high use the SAME CPU erase addresses as the upper
# map (the strap only re-routes them to other physical 64K sectors, transparent to the tool).  CAVEAT:
# selecting the lower half may expose a blank half, so a complete compatible image must already be
# available for recovery. FLASH_REGIONS_AMD above remains the factory A17-high map.
FLASH_REGIONS_AMD_LOWER = {
    "boot":         dict(erase=0x00000, span=(0x00000, 0x02000), low=True),            # SA1 8K (chip 0x4000): vectors
    "program-low":  dict(erase=0x02000, span=(0x02000, 0x04000), low=True),            # SA2 8K (chip 0x6000)
    "program-mid":  dict(erase=0x04000, span=(0x04000, 0x08000), low=True, wp=True),   # SA0 16K (chip 0x0): bottom-boot, WP#-protectable
    "tune":         dict(erase=0x10000, span=(0x10000, 0x20000)),                      # SA4 64K
    "program-high": dict(erase=(0x20000, 0x30000), span=(0x20000, 0x40000)),           # SA5+SA6
}


def _flash_profile(chip, half="upper"):
    """Pick the command set, region map, and display label for a flash operation.

    The 29F200 is a 256K bottom-boot chip with NO A17 pin, so it presents the fine bottom-boot
    sectors natively — it IS the 29F400's lower half.  --chip 29f200 therefore always uses the
    fine-sector map (FLASH_REGIONS_AMD_LOWER); --half does not apply.

    The 29F400 is 512K with A17. --half selects the map for the board's Flash-A17
    strap: upper uses four 64 KB sectors; lower exposes the bottom-boot sectors.
    """
    if chip == "29f200":
        return True, FLASH_REGIONS_AMD_LOWER, ("29F200 (256K, no A17 — native bottom-boot fine "
                                               "sectors), AMD command set, no 12V")
    if chip == "29f400":
        if half == "lower":
            return True, FLASH_REGIONS_AMD_LOWER, ("29F400 LOWER half (A17 low) — small boot "
                                                   "sectors, retrofit wiring, no 12 V")
        return True, FLASH_REGIONS_AMD, "29F400 upper half (A17 high, factory) — no 12V"
    return False, FLASH_REGIONS, "28F200 (Intel command set, needs 12V VPP)"


def _ordered_regions(regions):
    """Program recoverable data first and the reset/boot region last."""
    preferred = ("tune", "program-high", "program-mid", "program-low", "boot", "low")
    return [name for name in preferred if name in regions]


def _file_to_phys(ref, lo, hi):
    """Reference bytes for physical [lo,hi).  file_off = phys XOR 0x4000 (the
    confirmed block-swap; bit-14 flip is constant within a 16KB page)."""
    out = bytearray()
    addr = lo
    while addr < hi:
        page_end = min((addr & ~0x3FFF) + 0x4000, hi)
        n = page_end - addr
        f = addr ^ 0x4000
        out += ref[f:f + n]
        addr = page_end
    return bytes(out)


def _swap_block_order(img):
    """XOR-0x4000 per-16KB-block swap — converts a CPU/physical-order image to
    file/chip-physical (bench-flashable, DS2 .bin) order, and vice-versa (self-
    inverse).  Swaps each adjacent pair of 16KB blocks (the GAL inverts CPU A14 ->
    flash A13).  A trailing odd 16KB block with no partner is left in place."""
    BLK = 0x4000
    out = bytearray(img)
    n = len(img)
    b = 0
    while b * BLK < n:
        s, d = b * BLK, (b ^ 1) * BLK     # partner block = this block's index XOR 1
        if s + BLK <= n and d + BLK <= n:
            out[s:s + BLK] = img[d:d + BLK]
        b += 1
    return bytes(out)


def _decode_sr(sr):
    if sr is None:
        return "no reply (monitor stuck or erase never returned)"
    bits = []
    bits.append("ready" if sr & 0x80 else "BUSY/timed-out")
    if sr & 0x20: bits.append("ERASE-ERROR(SR.5)")
    if sr & 0x10: bits.append("PROG-ERROR(SR.4)")
    if sr & 0x08: bits.append("VPP-LOW(SR.3) — is 12V on the VPP/RP# rail?")
    return f"0x{sr:02X} [" + ", ".join(bits) + "]"



def _region_plan(args, region, ref, regions):
    """Geometry + reference bytes (physical order) for one region of the given regions map.
    Returns a dict; raises ValueError(msg) if `ref` is the wrong kind/size for this region."""
    spec = regions[region]
    erase = spec["erase"]
    erase_addrs = list(erase) if isinstance(erase, (list, tuple)) else [erase]   # 1+ sector bases
    (lo, hi) = spec["span"]
    low, rp12, wp = spec.get("low", False), spec.get("rp12", False), spec.get("wp", False)
    hole = spec.get("hole")                        # (h_lo,h_hi) CPU range: unmapped, never touch
    in_hole = (lambda a: bool(hole) and hole[0] <= a < hole[1])

    is_partial = (len(ref) == CAL_PARTIAL_SIZE)    # 24KB cal/tune partial
    if is_partial:
        if region != "tune":
            raise ValueError(f"a 24KB cal partial only covers 'tune' (CPU 0x10000-0x15FFF); "
                             f"region '{region}' needs a full file-order image.")
        # partial[i] -> CPU 0x10000+i  (ds2.PARTIAL_DS2_ADDR=0x10000, no block-swap; DS2 addr ==
        # the CPU/firmware address the BSL monitor uses, so it drops straight into the tune block)
        refdata = bytearray(b"\xff" * (hi - lo))
        base = 0x10000 - lo                        # span index of CPU 0x10000
        refdata[base:base + len(ref)] = ref
        refdata = bytes(refdata)
    else:
        if len(ref) != MS41ECU.FULL_ROM_SIZE:
            raise ValueError(
                f"reference is {len(ref)} B; region '{region}' requires exactly one 256 KB "
                "file-order image" + (" or a 24 KB cal partial" if region == "tune" else ""))
        refdata = _file_to_phys(ref, lo, hi)

    # program only the non-0xFF window: erase leaves the block 0xFF and the 28F200 can't drive a
    # 1->0 that isn't there, so 0xFF ref bytes are no-ops.  Hole bytes are never real flash.
    nz = [i for i, b in enumerate(refdata) if b != 0xFF and not in_hole(lo + i)]
    w_lo, w_hi = (nz[0] & ~1, (nz[-1] + 2) & ~1) if nz else (0, 0)    # word-align the window out
    prog_chunks = []                               # program chunks that skip the hole
    if w_hi > w_lo:
        if hole and lo + w_lo < hole[1] and hole[0] < lo + w_hi:
            h0, h1 = hole[0] - lo, hole[1] - lo
            if w_lo < h0: prog_chunks.append((w_lo, h0))
            if h1 < w_hi: prog_chunks.append((h1, w_hi))
        else:
            prog_chunks.append((w_lo, w_hi))
    return dict(erase_addrs=erase_addrs, lo=lo, hi=hi, low=low, rp12=rp12, wp=wp, hole=hole,
                in_hole=in_hole, refdata=refdata, w_lo=w_lo, w_hi=w_hi,
                prog_chunks=prog_chunks, is_partial=is_partial)


def _print_plan(args, region, p, ref, amd=False):
    lo, hi, w_lo, w_hi = p["lo"], p["hi"], p["w_lo"], p["w_hi"]
    refdata, hole = p["refdata"], p["hole"]
    kind = "24KB cal partial" if p["is_partial"] else "file-order"
    ea = p["erase_addrs"]
    _emit(f"== FLASH PLAN: region '{region}' ==")
    _emit(f"  reference     : {args.ref} ({len(ref)} B, {kind})")
    unit = "sector" if amd else "block"
    low_note = "  (SA7 is a 64 KB sector; only its low 32 KB is mapped data)" if (amd and region == "low") else ""
    _emit(f"  erase {'sectors' if len(ea) > 1 else 'block  '}: {', '.join('0x%05X' % a for a in ea)}"
          f"  -> {unit}-granular erase: clears the WHOLE {unit}(s) containing the listed address(es)"
          f"{low_note}")
    _emit(f"  program window: 0x{lo + w_lo:05X}..0x{lo + w_hi:05X}  ({w_hi - w_lo} B of real data; "
          f"rest of block stays 0xFF)")
    if hole:
        _emit(f"  unmapped hole : 0x{hole[0]:05X}..0x{hole[1]:05X} — floating bus, NOT real flash; "
              f"never written or verified (erase still clears the whole block).")
    if p["is_partial"]:
        _emit(f"  cal mapping   : partial -> CPU 0x10000..0x{0x10000 + len(ref):05X} "
              f"(DS2/CPU order; rest of the tune block left 0xFF)")
    else:
        _emit(f"  verify span   : 0x{lo:05X}..0x{hi:05X}  (file 0x{lo ^ 0x4000:05X}.. via XOR 0x4000)")
    if w_hi > w_lo:
        _emit(f"  ref @0x{lo + w_lo:05X}   : {refdata[w_lo:w_lo + 16].hex(' ')} …")
    _emit(f"  ref checksum  : {sum(refdata) & 0xFFFFFFFF:08X} over {len(refdata)} B")
    if p["rp12"]:
        _emit( "  ** RP#=12V    : 28F200 HW-LOCKED boot block — RP# must be 11.4-12.6V for the "
               "erase/program. On this ECU RP# shares the VPP net, so the VPP 12V covers it.")
    if p["wp"]:
        _emit( "  ** WP#        : 29F2xx/4xx bottom-boot sector — WP#-protectable. If WP# (the old "
               "VPP-net pin on the retrofit) sits low this sector is locked: erase/program no-op and "
               "the read-back verify will flag it. The cal/main sectors are NOT WP#-gated.")
    if p["low"]:
        _emit( "  ** BSL SHADOW : 0x0-0x7FFF is overlaid by the BSL bootstrap-ROM; the run "
               "pre-flights phys 0x0 and, if shadowed, routes ALL ops for this region")
        _emit(f"                  through the +0x{BSL_ALIAS:X} wrap-around alias (reaches the "
               "real flash).")
    if amd:
        _emit( "  REQUIRES      : NO 12V — 29F2xx/4xx is single-supply (makes its own program "
               "voltage). The monitor drives WR# only; the 12V VPP net stays OFF (it feeds the "
               "chip's RESET# on this retrofit).")
    else:
        _emit( "  REQUIRES      : 12V on the 28F200 VPP pin (else erase/program no-op; SR.3 flags it)")


def _live_program_variant(bsl):
    """Read exact live program markers, including the BSL-shadowed ECU ID via its alias."""
    sig_cpu = SS1V2_PROG_SIG_ADDR ^ 0x4000
    sig = bytes(bsl.mon_read(sig_cpu, len(SS1V2_PROG_SIG)))
    ecu_id = bytes(bsl.mon_read(BSL_ALIAS + (0x6025 ^ 0x4000), 7))
    if len(sig) != len(SS1V2_PROG_SIG) or len(ecu_id) != 7:
        raise BSLError(
            f"short live program-marker read (sig {len(sig)}/{len(SS1V2_PROG_SIG)}, "
            f"ECU ID {len(ecu_id)}/7)")
    probe = bytearray(b"\xff" * MS41ECU.FULL_ROM_SIZE)
    probe[SS1V2_PROG_SIG_ADDR:SS1V2_PROG_SIG_ADDR + len(sig)] = sig
    probe[0x6025:0x602C] = ecu_id
    blank = all(byte == 0xFF for byte in sig + ecu_id)
    return MS41ECU.detect_program_variant(bytes(probe)), blank


def _variant_guard(bsl, args, ref, regions):
    """Fail-closed program/cal compatibility gate for partial and whole-chip writes."""
    ref_cal = MS41ECU.detect_variant(ref)
    ref_program = (MS41ECU.detect_program_variant(ref)
                   if len(ref) == MS41ECU.FULL_ROM_SIZE else None)
    ref_id = MS41ECU.read_calid(ref)
    if len(ref) == MS41ECU.FULL_ROM_SIZE:
        hybrid = MS41ECU.check_hybrid(ref)
        if hybrid:
            _emit(f"  ** HYBRID ROM  : {hybrid}")
            if not args.force:
                _emit("RESULT: reference is internally cross-variant — refusing.")
                return 1

    try:
        live_cal_bytes = bytes(bsl.mon_read(0x10000, MS41ECU.TUNE_SIZE))
        if len(live_cal_bytes) != MS41ECU.TUNE_SIZE:
            raise BSLError(
                f"short live calibration read: {len(live_cal_bytes)}/{MS41ECU.TUNE_SIZE} bytes")
        live_cal = MS41ECU.detect_variant(live_cal_bytes)
        live_program, live_program_blank = _live_program_variant(bsl)
    except Exception as error:
        _emit(f"RESULT: variant preflight could not read exact live evidence ({error}) — refusing.")
        return 0 if args.force else 1

    live_cal_blank = all(byte == 0xFF for byte in live_cal_bytes)
    live_blank = live_cal_blank and live_program_blank
    _emit(f"  variant guard : ref program={ref_program or '?'} / cal={ref_cal or '?'} "
          f"(CAL ID {ref_id or '?'})  |  live program={live_program or '?'} / "
          f"cal={('BLANK' if live_cal_blank else live_cal or '?')}")

    all_write = set(regions) in (set(FLASH_REGIONS), set(FLASH_REGIONS_AMD),
                                 set(FLASH_REGIONS_AMD_LOWER))
    if live_blank and not all_write:
        _emit("RESULT: ECU appears blank; a partial-region write cannot establish a complete image.")
        return 0 if args.force else 1

    program_regions = {"boot", "program-low", "program-mid", "program-high", "low"}
    writes_program = bool(program_regions & set(regions))
    writes_program_signature = "program-high" in regions
    writes_cal = "tune" in regions

    if writes_program and not writes_program_signature:
        # Low/program-mid writes do not replace the high-region SS1v2 signature. Their reference
        # must belong to the program already present; otherwise they create a mixed program image.
        if not ref_program or not live_program or ref_program != live_program:
            _emit("RESULT: low/program-only reference does not match the live program variant — refusing.")
            return 0 if args.force else 1

    target_program = ref_program if writes_program_signature else live_program
    target_cal = ref_cal if writes_cal else live_cal
    if not target_program or not target_cal:
        _emit("RESULT: target program/cal variant could not be proven — refusing.")
        return 0 if args.force else 1
    if target_program != target_cal:
        _emit(f"RESULT: operation would leave a hybrid ({target_program} program / {target_cal} cal) — refusing.")
        return 0 if args.force else 1
    _emit(f"  variant result: target remains internally consistent {target_program}.")
    return 0


def _checksum_guard(args, ref, regions):
    """Verify (and with --fix-checksums, correct) the reference's MS41 checksums — file-only, no
    hardware.  Prints status; returns (ref_maybe_fixed, hard_block).  A bad checksum whose ECU
    verification is DISABLED only warns (e.g. the MS41.3 program checksum)."""
    if len(ref) not in (cks.FULL_ROM_SIZE, cks.TUNE_SIZE):
        _emit(f"  checksums     : ref is {len(ref)} B (not a full ROM or 24KB partial) — SKIPPED.")
        return ref, False

    if args.fix_checksums:
        var = MS41ECU.detect_variant(ref) if MS41ECU else None
        corr_prog = (var != "MS41.3")              # MS41.3 program-checksum layout unconfirmed (+ disabled)
        fixed, notes = cks.correct_checksums(bytearray(ref), correct_program=corr_prog)
        ref = bytes(fixed)
        for n in notes:
            _emit(f"  fix-checksums : {n}")

    ok, details = cks.verify_checksum(bytearray(ref))
    for d in details:
        _emit(f"  checksum      : {d}")
    if ok:
        return ref, False

    st = cks.checksum_status(ref)
    involves_cal = "tune" in regions
    # "low" (the 64 KB SA7 sector; its low 32 KB holds boot + program-low + bootloader) writes the
    # boot + program-low data, so it affects both the boot and program checksums — treat it as program-involving.
    involves_prog = bool({"boot", "program-low", "program-mid", "program-high", "low"} & set(regions))
    involves_boot = bool({"boot", "low"} & set(regions))
    hard = False
    if involves_cal and st["cal"] is False:
        if st["cal_disabled"]:
            _emit("  note          : cal checksum is bad but the cal CRC check is DISABLED — allowing.")
        else:
            _emit("  ** CAL CHECKSUM invalid and the cal CRC check is ENABLED — the ECU would reject it.")
            hard = True
    if involves_prog and st["program"] is False:
        if st["prog_disabled"]:
            _emit("  note          : program checksum is bad but program verification is DISABLED "
                  "(0x605C) — allowing (this is normal on MS41.3).")
        else:
            _emit("  ** PROGRAM CHECKSUM invalid and verification is ENABLED (0x605C) — would brick.")
            hard = True
    if involves_boot and st["boot"] is False:
        _emit("  ** BOOT CHECKSUM invalid for a boot/low write — refusing unless fixed or forced.")
        hard = True
    if hard:
        _emit("  -> re-run with --fix-checksums to correct it, or --force to flash anyway.")
    return ref, hard


def _flash_region(bsl, args, region, p, amd=False):
    """Erase + program + verify one region using an already-started monitor.  0=ok, 1=fail."""
    lo, hi, refdata, hole = p["lo"], p["hi"], p["refdata"], p["hole"]
    in_hole, rp12, erase_addrs = p["in_hole"], p["rp12"], p["erase_addrs"]
    _emit(f"\n-- '{region}'  (0x{lo:05X}..0x{hi:05X}) --")

    # shadow pre-flight for the low blocks -> route ALL ops through the +BSL_ALIAS wrap-around.
    # We decide from CONTENT, not the boot-ROM signature: the 28F200 shows the boot-ROM sig at direct
    # 0x0 while the 29F400 retrofit shows 0xFF there — in both the REAL low data is only via the alias.
    acc = 0
    if p["low"]:
        head  = bsl.mon_read(0x0, 16)
        ahead = bsl.mon_read(BSL_ALIAS, 16)
        _emit(f"  phys 0x0 read : {head.hex(' ') or '<nothing>'}")
        _emit(f"  alias 0x{BSL_ALIAS:05X}: {ahead.hex(' ') or '<nothing>'}")
        if len(head) != 16 or len(ahead) != 16:
            _emit("RESULT: low-region alias preflight returned a short read; nothing erased.")
            return 1

        def _is_flash(d):   # plausibly real flash: not the boot-ROM shadow, not blank 0xFF
            return len(d) >= 4 and d[:4] != BSL_BOOTROM_SIG and any(x != 0xFF for x in d)

        if _is_flash(head):
            _emit("  (direct 0x0 reads real flash — shadow absent; addressing directly)")
        elif _is_flash(ahead):
            acc = BSL_ALIAS
            _emit(f"  shadow bypass : direct 0x0 isn't real flash (shadow/blank); the +0x{BSL_ALIAS:X} "
                  f"alias reads flash -> routing ALL ops for this region through it.")
        elif len(ahead) == 16 and ahead[:4] != BSL_BOOTROM_SIG:
            acc = BSL_ALIAS     # both sides blank/ambiguous (virgin low region) but alias isn't the
            _emit(f"  shadow bypass : 0x0 region blank/ambiguous; routing through +0x{BSL_ALIAS:X} "
                  f"(the shadowed-low default — run `verify-alias` to confirm the wrap first).")
        else:
            _emit("RESULT: 0x0-0x7FFF shadowed AND the +0x40000 alias did not read flash -> "
                  "region unreachable here. Nothing erased."); return 1

    if not args.no_backup:
        try:
            before = _read_mapped_span(
                bsl, lo, hi, hole=hole, alias=acc,
                progress=_progress_for(args, "BSL backup"))
            bpath = _save_region_backup(args, region, p, before)
        except Exception as error:
            _emit(f"RESULT: exact pre-erase backup failed ({error}); nothing erased.")
            return 1
        _emit(f"  backup        : saved reusable file-order region container to {bpath}")

    for ea in erase_addrs:
        _emit(f"  erasing 0x{ea:05X}" + (f" via alias 0x{ea + acc:05X}" if acc else "") +
              (f"  ({len(erase_addrs)} sectors in this span)" if len(erase_addrs) > 1 else
               f"  (whole {hi - lo} B block)") + "…")
        sr = bsl.mon_erase(ea + acc)
        _emit(f"  erase status  : {_decode_sr(sr)}")
        if sr is None or not (sr & 0x80) or (sr & 0x28):
            if p.get("wp"):
                hint = "this is the WP#-protected bottom-boot sector — its WP# pin is likely held low"
            elif amd:
                hint = ("a 29F2xx/4xx erase shouldn't VPP-fail (single-supply) — suspect the WR#/bus "
                        "or that this sector is WP#-locked")
            elif rp12:
                hint = "check the 12V VPP supply (RP# shares it for this HW-locked block)"
            else:
                hint = "check the 12V VPP supply (else the 28F200 erase no-ops; SR.3 flags it)"
            _emit(f"RESULT: erase did NOT complete cleanly — aborting before any program. {hint}.")
            return 1

    try:
        chk = _read_mapped_span(
            bsl, lo, hi, hole=hole, alias=acc,
            progress=_progress_for(args, "post-erase"))
    except Exception as error:
        _emit(f"RESULT: post-erase read was incomplete ({error}); no program attempted.")
        return 1
    bad = next((lo + i for i, b in enumerate(chk) if b != 0xFF and not in_hole(lo + i)), None)
    if bad is not None:
        _emit(f"  post-erase    : NOT all-0xFF (first @0x{bad:05X}=0x{chk[bad - lo]:02X}) — erase "
              f"didn't clear the block. No program attempted."); return 1
    _emit("  post-erase    : real-flash region reads 0xFF (OK)" +
          (f"; hole 0x{hole[0]:05X}-0x{hole[1]:05X} ignored" if hole else ""))

    if p["prog_chunks"]:
        for c_lo, c_hi in p["prog_chunks"]:
            n = c_hi - c_lo
            _emit(f"  programming   : {n} B @0x{lo + c_lo:05X} from reference "
                  f"(~{n * 10 / args.baud:.0f}s at {args.baud})…")
            if not bsl.mon_program(lo + acc + c_lo, refdata[c_lo:c_hi],
                                   progress=_progress_for(args, "BSL program")):
                _emit("RESULT: program not ACKed — STOPPED. Region partially written; re-arm to "
                      "retry (it re-erases first)."); return 1
    else:
        _emit("  programming   : reference is all-0xFF here — nothing to write (block erased).")

    try:
        back = _read_mapped_span(
            bsl, lo, hi, hole=hole, alias=acc,
            progress=_progress_for(args, "BSL verify"))
    except Exception as error:
        _emit(f"RESULT: verify read was incomplete ({error}). Region must be treated as unverified.")
        return 1
    diffs = [i for i, (a, b) in enumerate(zip(back, refdata)) if a != b and not in_hole(lo + i)]
    ok = (not diffs and len(back) == hi - lo)
    _emit(f"  verify span   : {'MATCH' if ok else f'{len(diffs)} byte(s) differ'} "
          f"(read {len(back)}/{hi - lo} B" + (", hole skipped" if hole else "") + ")")
    if ok:
        _emit(f"  RESULT        : '{region}' FLASHED + VERIFIED (read-back == reference)."); return 0
    if diffs:
        i = diffs[0]
        _emit(f"  first diff @0x{lo + i:05X}: flash {back[i]:02X} vs ref {refdata[i]:02X}")
    _emit("RESULT: VERIFY MISMATCH. If read-back is all-0xFF the writes didn't reach the flash "
          + ("(WR#/WP#, or wrong sector)" if amd else "(no VPP)")
          + "; else the reference differs from what was programmed.")
    return 1


def cmd_flash(args):
    if not args.ref:
        _emit("need --ref <reference .bin> (full file-order image, or a 24KB cal for 'tune')")
        return 2
    ref = getattr(args, "ref_bytes", None)
    if ref is None:
        try:
            ref = open(args.ref, "rb").read()
        except OSError as e:
            _emit(f"cannot open --ref: {e}"); return 2
    ref = bytes(ref)

    if args.chip == "auto":
        _emit("REFUSING: flash geometry cannot be auto-detected safely. Run Chip ID, then select "
              "28f200, 29f200, or 29f400 explicitly before Preview/ARM.")
        return 2
    if len(ref) not in (CAL_PARTIAL_SIZE, MS41ECU.FULL_ROM_SIZE):
        _emit(f"reference is {len(ref)} B; expected exactly 24576 B (tune partial) or "
              f"{MS41ECU.FULL_ROM_SIZE} B (full file-order image).")
        return 2

    # Byte-order guard: a FULL ref must be FILE/chip order — the writer descrambles it to
    # CPU/physical.  A CPU-order image (e.g. a `dump --cpu-order`) would be double-scrambled
    # and brick the ECU.  (Only fulls: a 24 KB partial is CPU-order by design and is skipped.)
    if MS41ECU and len(ref) == MS41ECU.FULL_ROM_SIZE and MS41ECU.looks_cpu_order(ref):
        _emit("REFUSING: the reference appears to use CPU/physical order instead of standard "
              "file/chip order. Writing it as-is would scramble the image incorrectly.")
        _emit("  Select a standard file-order full dump, or use Force only after independently "
              "confirming the image order.")
        if not args.force:
            return 2
        _emit("  Force enabled: proceeding with the operator-confirmed image order.")

    amd, regions_map, chip_label = _flash_profile(args.chip, args.half)
    _emit(f"== FLASH CHIP: {chip_label} ==")
    if args.chip == "29f200":
        _emit("  NOTE: 29F200 uses the bottom-boot fine-sector map and has no A17 half selection.")
    elif args.chip == "29f400" and args.half == "lower":
        _emit("  WARNING: the 29F400 lower-half map requires Flash A17 strapped LOW and compatible "
              "board/GAL routing. Confirm the retrofit wiring and lower-half image before flashing; "
              "factory A17-high hardware uses the upper half.")

    if args.region != "all" and args.region not in regions_map:
        _emit(f"region '{args.region}' isn't valid for this chip ({chip_label}). "
              f"Valid regions: {', '.join(regions_map)}, all.")
        return 2
    if args.region == "all":
        regions = _ordered_regions(regions_map)
        if len(ref) != MS41ECU.FULL_ROM_SIZE:
            _emit(f"'all' needs exactly one {MS41ECU.FULL_ROM_SIZE}-byte file-order image; "
                  f"got {len(ref)} B.")
            return 2
    else:
        regions = [args.region]

    _emit("== CHECKSUMS ==")
    ref, ck_block = _checksum_guard(args, ref, regions)

    try:
        plans = {r: _region_plan(args, r, ref, regions_map) for r in regions}
    except ValueError as e:
        _emit(str(e)); return 2

    for r in regions:
        _print_plan(args, r, plans[r], ref, amd=amd)
    _emit("\n  variant guard : ON (exact live program + calibration evidence checked before any erase)")
    if ck_block:
        _emit("  checksum block: an ENABLED checksum is invalid — --arm will refuse without "
              "--fix-checksums or --force.")
    if not args.arm:
        _emit("\nDRY RUN — nothing was sent. Re-run with --arm to execute.")
        return 0

    if ck_block and not args.force:
        _emit("\nRESULT: checksum issue (see CHECKSUMS above) — refusing to flash. Re-run with "
              "--fix-checksums to correct it, or --force to flash anyway."); return 1

    _emit("\n--arm given: executing.")
    bsl = _bsl(args)
    if not bsl.start_monitor(flash=True, amd=amd):
        _emit("RESULT: flash monitor did not come up (no 0xA5). Check wiring/timing."); return 1
    # One monitor load services every region of an 'all' run.  The Intel monitor enables WR# (P3.13)
    # at entry and the VPP gate (P2.6) at the first erase, held on; the AMD monitor enables WR# only
    # (no VPP — single-supply chip).  A reset clears the port bits either way.
    if amd:
        _emit("  wr#           : AMD monitor drives WR# (P3.13) from entry; NO 12V/VPP (P2.6 stays off).")
    else:
        _emit("  vpp/wr#       : monitor drives WR# (P3.13) from entry and VPP (P2.6) from the first "
              "erase, held on through programming; a reset clears them.")
    if _variant_guard(bsl, args, ref, regions):
        return 1

    for r in regions:
        rc = _flash_region(bsl, args, r, plans[r], amd=amd)
        if rc:
            if len(regions) > 1:
                _emit(f"\nRESULT: 'all' STOPPED at '{r}'. Earlier regions are flashed; re-run to "
                      f"resume (every region re-erases first).")
            return rc
    if len(regions) > 1:
        _emit(f"\nRESULT: ALL {len(regions)} regions FLASHED + VERIFIED.")
    return 0


def main():
    for _s in (sys.stdout, sys.stderr):             # never let a non-cp1252 char crash a print
        try:
            _s.reconfigure(errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="MS41 80C166 BSL unbrick")
    ap.add_argument(
        "--version",
        action="version",
        version=f"BimmerStein ECU Tool BSL-Unbricker {__version__}",
    )
    ap.add_argument("--port", required=True, help="serial port, e.g. COM3")
    ap.add_argument("--baud", type=int, default=9600,
                    help="BSL baud (host picks it; ECU auto-bauds). default 9600")
    ap.add_argument("--speed", choices=["slow", "mid", "fast"], default=None,
                    help="convenience baud presets (override --baud): slow=9600, mid=19200, "
                         "fast=38400. The ECU's BOOTSTRAP auto-baud (in CPU silicon, before our "
                         "monitor loads) sets the limit: 9600/19200 lock cleanly; higher rates "
                         "may not (a garbled sync reply = it couldn't lock — drop a preset). "
                         "mid (19200) is the proven sweet spot here.")
    ap.add_argument("--reset-line", choices=["rts", "dtr"], default=None,
                    help="auto-pulse the CPU reset into BSL before each sync, via this FTDI "
                         "output. RTS and DTR are the PC->device OUTPUTS; CTS/DSR/DCD are inputs "
                         "you cannot drive. Wire it to RSTIN# (active-low). Omit to reset by hand.")
    ap.add_argument("--reset-ms", type=float, default=20.0, metavar="MS",
                    help="ms to hold the reset asserted (default 20).")
    ap.add_argument("--reset-settle", type=float, default=15.0, metavar="MS",
                    help="ms to wait after releasing reset before sync, for the BSL to come up "
                         "(default 15).")
    ap.add_argument("--reset-invert", action="store_true",
                    help="invert reset polarity — use if a transistor/inverter sits between the "
                         "FTDI pin and RSTIN# (so the line must go HIGH to assert reset).")
    ap.add_argument("--half", choices=["upper", "lower"], default="upper",
                     help="29F400 only: which chip half the board's Flash-A17 strap selects. "
                          "'upper' (default; A17 high) uses the factory pull-up. 'lower' (A17 rewired "
                          "low) exposes the bottom-boot small sectors. Going low needs compatible GAL "
                          "routing (drop its A17 decode dependency, or cut the "
                         "flash-pin-3-to-GAL trace), else RAM/CAN deassert and the ECU dies; re-flash the "
                         "image into that half first. Ignored for 28F200 and 29F200 (a 29F200 has no A17 "
                         "and always uses the bottom-boot fine sectors).")
    ap.add_argument("--chip", choices=["auto", "28f200", "29f200", "29f400"], default="auto",
                    help="flash command set: 28f200 = Intel (status-register, needs 12V VPP); "
                         "29f200/29f400 = AMD/JEDEC (unlock-cycle + data-poll, single-supply, no 12V) "
                         "— identical command set, they differ only in sector map. 29f200 (2 Mbit, no "
                         "A17) uses the native bottom-boot fine sectors; 29f400 (4 Mbit) uses --half "
                         "to pick the A17 strap's half. 'auto' (default) is diagnostic-only: it tries "
                         "Intel then AMD for `id`; flashing requires an explicit chip geometry.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("ports", help="list serial ports")
    sub.add_parser("sync", help="confirm BSL entry (0x55) and that loaded code runs (0xA5)")

    rp = sub.add_parser("read", help="read memory and print hex")
    rp.add_argument("addr_len", metavar="ADDR:LEN",
                    help="24-bit address and byte count, e.g. 0x10000:256")

    wp = sub.add_parser("write", help="write hex bytes to scratch RAM and verify (NOT flash — "
                                      "flash needs erase/program; use the `flash` command)")
    wp.add_argument("addr_hex", metavar="ADDR:HEX",
                    help="address and hex bytes, e.g. 0xFC80:DEADBEEF (free IRAM scratch)")

    dp = sub.add_parser("dump", help="dump flash to a .bin file")
    dp.add_argument("file", help="output .bin path")
    dp.add_argument("--range", metavar="START:END", default=None,
                    help="physical range to dump (default the whole chip 0x0:0x40000)")
    dp.add_argument("--file-order", action="store_true",
                    help="(now the DEFAULT) save in file/chip order (block-swap XOR 0x4000) — a "
                         "bench-flashable .bin; kept for explicitness / back-compat")
    dp.add_argument("--cpu-order", action="store_true",
                    help="save the raw physical/CPU-order image instead (NOT directly re-flashable — "
                         "the flasher would double-scramble it)")
    dp.add_argument("--partial", action="store_true",
                    help="dump ONLY the 24 KB cal/tune partition (DS2 0x10000-0x15FFF), CPU/DS2 "
                         "order — a clean partial flashable via `flash tune --ref`")
    dp.add_argument("--no-alias", action="store_true",
                    help="do NOT route the BSL-shadowed 0x0-0x7FFF through the +0x40000 alias")
    dp.add_argument("--raw-hole", action="store_true",
                    help="keep the raw floating-bus read of the unmapped CPU 0xC000-0xFFFF hole "
                         "(default 0xFF-fills it to match stock dumps)")

    sub.add_parser("verify-alias", help="prove the +0x40000 wrap-around reaches real flash "
                                        "(run before trusting the alias for writes)")
    sub.add_parser("vpp-on", help="switch VPP (P2.6) on so you can meter ~12V at the 28F200 "
                                  "VPP pin; any other command resets it")
    sub.add_parser("businfo", help="read SYSCON/BUSCON/ADDRSEL to inspect the bus config")
    sub.add_parser("id", help="read the flash chip's manufacturer + device ID (non-destructive, no "
                              "12V). Honors --chip: auto (default) tries the Intel 28F200 then the "
                              "AMD/JEDEC 29F200 autoselect; --chip 29f200 forces the AMD command set.")

    fp = sub.add_parser("flash", help="erase+program+verify a region from a reference (DRY-RUN unless "
                                      "--arm). 28F200 needs 12V on VPP/RP#; 29F200/29F400 are single-supply.")
    fp.add_argument("region",
                    choices=list(dict.fromkeys(list(FLASH_REGIONS) + list(FLASH_REGIONS_AMD)
                                               + list(FLASH_REGIONS_AMD_LOWER))) + ["all"],
                    help="erase region (CPU addrs; chip block = XOR 0x4000). Valid set depends on "
                         "--chip. 28F200: 'tune'(96K)/'program-high'(128K) direct; 'boot'(8K, reset "
                         "VECTORS)/'program-low'(8K)/'program-mid'(16K HW-locked, RP#=12V) in the "
                         "BSL-shadowed 0x0-0x7FFF via the +0x40000 alias. 29F400 (A17-high upper half): "
                         "'tune'(64K=SA8)/'program-high'(2x64K=SA9+SA10) direct; 'low' = SA7, the 64K "
                         "sector at CPU 0x0-0xFFFF (low 32K = boot+program-low+bootloader; upper 32K = "
                         "gap+hole), flashed as ONE unit via the +0x40000 alias (the whole 64K sector "
                         "erases together). 29F200 (and 29F400 --half lower) = bottom-boot fine sectors: "
                         "'boot'(8K=SA1, VECTORS)/'program-low'(8K=SA2)/'program-mid'(16K=SA0) separate "
                         "via the alias, 'tune'(64K=SA4), 'program-high'(2x64K=SA5+SA6). 'all' = every "
                         "region in one monitor session (needs a full image) — for a virgin/dead chip.")
    fp.add_argument("--ref", metavar="FILE", default=None,
                    help="reference to program from: a full file-order .bin (e.g. an exact-ECU "
                         "dump or corrected image) for any region, OR a 24KB cal partial (DS2 "
                         "tune file) for the 'tune' region only.")
    fp.add_argument("--arm", action="store_true",
                    help="actually ERASE + PROGRAM + VERIFY the chip. WITHOUT --arm this is a "
                         "DRY RUN: it only prints the plan (which block erases, the program "
                         "window, the variant check) and never opens the port or touches the "
                         "ECU — safe to review first. Add --arm only when the plan looks right.")
    fp.add_argument("--no-backup", action="store_true",
                    help="skip saving the current span to a .bin before erasing")
    fp.add_argument("--fix-checksums", action="store_true",
                    help="recompute and correct the reference's MS41 checksums (boot + cal, plus "
                         "program for non-MS41.3) in memory before flashing, instead of refusing a "
                         "bad-checksum image. Only the stored checksum bytes change.")
    fp.add_argument("--force", action="store_true",
                    help="override the guards (flash even on a cross-variant image OR a bad, "
                         "ENABLED checksum). Use only when you are certain — either can brick the ECU.")
    args = ap.parse_args()
    if args.speed:                                  # preset overrides --baud
        args.baud = {"slow": 9600, "mid": 19200, "fast": 38400}[args.speed]

    if args.cmd == "ports":
        _emit("\n".join(BSL.list_ports()) or "(none)"); return 0
    try:
        return {
            "sync": cmd_sync, "read": cmd_read, "write": cmd_write, "dump": cmd_dump,
            "verify-alias": cmd_verify_alias, "vpp-on": cmd_vpp_on, "businfo": cmd_businfo,
            "id": cmd_id,
            "flash": cmd_flash,
        }[args.cmd](args)
    except (BSLError,) + _SERIAL_ERRS as e:
        _emit(f"\nSERIAL ERROR ({e.__class__.__name__}): {e}")
        _emit("This is the USB-to-serial adapter, not the ECU. An FT232 left idle gets put to "
              "sleep by Windows USB selective-suspend (or wedges after a long idle), so the "
              "next access stalls. Fixes, easiest first:")
        _emit("  1. Unplug/replug the FT232 and re-run (the tool also auto-reopens on sync).")
        _emit("  2. Disable USB selective-suspend: Device Manager > the COM port (and the USB "
              "Root Hub) > Power Management > uncheck 'Allow the computer to turn off this "
              "device'; and Power Options > USB settings > USB selective suspend > Disabled.")
        _emit("  Re-running is safe: a stall during sync/load flashed nothing; a stall "
              "mid-program leaves that region partial, and every region re-erases first.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
