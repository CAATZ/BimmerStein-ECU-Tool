"""Minimal pyserial-compatible shim over the FTDI D2XX driver.

D2XX bypasses the Windows VCP layer and is the preferred transport for elevated
baud rates. The shim exposes only the serial surface used by DS2 and Soft-BSL.
SOFTBSL_D2XX=0 forces the pyserial fallback. Framing follows ``two_stop``.
"""
import ctypes as C
import re

_ft = None
FT_OK = 0
_PURGE_RX, _PURGE_TX = 1, 2
_BITS_8, _STOP_2, _STOP_1 = 8, 2, 0   # FTDI: FT_STOP_BITS_1=0, FT_STOP_BITS_2=2
_PAR_NONE, _PAR_ODD, _PAR_EVEN, _PAR_MARK, _PAR_SPACE = 0, 1, 2, 3, 4
D2XX_LATENCY_MS = 1
D2XX_LATENCY_FALLBACK_MS = 2


class D2XXError(Exception):
    pass


def _driver():
    """Load D2XX lazily so importing the application does not require the DLL."""
    global _ft
    if _ft is None:
        loader = getattr(C, "WinDLL", None)
        if loader is None:
            raise OSError("FTDI D2XX is available only on Windows")
        _ft = loader("ftd2xx.dll")
    return _ft


def _chk(st, what):
    if st != FT_OK:
        raise D2XXError(f"{what} failed (D2XX status {st})")


def _com_number(port):
    """Return the numeric part of a Windows COM name, or None for an index-only request."""
    match = re.fullmatch(r"\s*COM(\d+)\s*", str(port or ""), re.IGNORECASE)
    return int(match.group(1)) if match else None


def _close_handle(handle):
    if handle and handle.value:
        try:
            _driver().FT_Close(handle)
        except Exception:
            pass


def _open_selected(port, fallback_index=0):
    """Open the FTDI device mapped to ``port`` instead of silently using device zero."""
    wanted = _com_number(port)
    if wanted is None:
        handle = C.c_void_p()
        _chk(_driver().FT_Open(int(fallback_index), C.byref(handle)), "FT_Open")
        return handle, int(fallback_index)

    count = C.c_ulong(0)
    _chk(_driver().FT_CreateDeviceInfoList(C.byref(count)), "FT_CreateDeviceInfoList")
    if not hasattr(_driver(), "FT_GetComPortNumber"):
        raise D2XXError("this D2XX driver cannot map devices to the selected COM port")

    unavailable = 0
    query_failures = 0
    for device_index in range(count.value):
        handle = C.c_void_p()
        status = _driver().FT_Open(device_index, C.byref(handle))
        if status != FT_OK:
            unavailable += 1
            continue
        try:
            number = C.c_long(-1)
            _chk(_driver().FT_GetComPortNumber(handle, C.byref(number)), "FT_GetComPortNumber")
            if number.value == wanted:
                return handle, device_index
        except Exception:
            _close_handle(handle)
            query_failures += 1
            continue
        _close_handle(handle)

    notes = []
    if unavailable:
        notes.append(f"{unavailable} device(s) were not openable")
    if query_failures:
        notes.append(f"{query_failures} COM mapping query/queries failed")
    detail = "; " + "; ".join(notes) if notes else ""
    raise D2XXError(f"no available D2XX device is mapped to COM{wanted}{detail}")


def port_available(port=None):
    """Probe that D2XX can open the selected adapter, closing the probe immediately."""
    handle = None
    try:
        handle, _index = _open_selected(port)
        return True
    except Exception:
        return False
    finally:
        _close_handle(handle)


class D2XXSerial:
    """The pyserial.Serial subset used by ds2.py/softbsl_host, backed by D2XX.
    A Windows COM name is resolved to its matching D2XX device; ``index`` is used only
    when no COM name is supplied."""

    def __init__(self, port=None, baudrate=9600, bytesize=_BITS_8, parity=_PAR_EVEN,
                 timeout=1.0, write_timeout=3.0, index=0, two_stop=True, **_ignore):
        try:
            self._h, self.index = _open_selected(port, index)
            self._open = True
            self.port = port
            self.transport_name = "d2xx"
            self._baud = int(baudrate)
            self._timeout = timeout
            self._wtimeout = write_timeout
            _stop = _STOP_2 if two_stop else _STOP_1
            _parity = self._parity_code(parity)
            _chk(_driver().FT_SetDataCharacteristics(
                self._h, int(bytesize), _stop, _parity), "SetDataCharacteristics")
            _chk(_driver().FT_SetBaudRate(self._h, self._baud), "SetBaudRate")
            # Prefer 1 ms receive latency. Drivers that reject it use the compatible
            # 2 ms fallback instead of abandoning D2XX.
            self.latency_timer_ms = None
            for latency_ms in (D2XX_LATENCY_MS, D2XX_LATENCY_FALLBACK_MS):
                try:
                    _chk(
                        _driver().FT_SetLatencyTimer(self._h, C.c_ubyte(latency_ms)),
                        f"SetLatencyTimer({latency_ms})",
                    )
                    self.latency_timer_ms = latency_ms
                    break
                except Exception:
                    continue
            self._apply_timeouts()
            # A freshly acquired adapter must not inherit either receive-side
            # echo bytes or queued transmit bytes from a previous owner.  Use
            # one atomic D2XX purge so every DS2/native/Soft-BSL handoff starts
            # from a known-empty host transport.
            self.reset_buffers()
        except Exception:
            _close_handle(getattr(self, "_h", None))
            self._open = False
            raise

    @staticmethod
    def _parity_code(parity):
        """Translate pyserial-style parity values to FTDI constants.

        DS2 uses 8E2; the hardware 80C166 BSL uses direct ASC0 8N1.  The old
        shim always configured even parity, which was correct only for DS2 and
        made it unsuitable as the BSL transport.
        """
        if parity in (None, _PAR_NONE, "N", "n", "NONE", "none"):
            return _PAR_NONE
        if parity in (_PAR_ODD, "O", "o", "ODD", "odd"):
            return _PAR_ODD
        if parity in (_PAR_EVEN, "E", "e", "EVEN", "even"):
            return _PAR_EVEN
        if parity in (_PAR_MARK, "M", "m", "MARK", "mark"):
            return _PAR_MARK
        if parity in (_PAR_SPACE, "S", "s", "SPACE", "space"):
            return _PAR_SPACE
        raise ValueError(f"unsupported FTDI parity {parity!r}")

    def _apply_timeouts(self):
        r = int(max(1, round((self._timeout or 0.001) * 1000)))
        w = int(max(1, round((self._wtimeout or 3.0) * 1000)))
        _chk(_driver().FT_SetTimeouts(self._h, r, w), "SetTimeouts")

    # -- baudrate (get/set) --
    @property
    def baudrate(self):
        return self._baud

    @baudrate.setter
    def baudrate(self, b):
        self._baud = int(b)
        _chk(_driver().FT_SetBaudRate(self._h, self._baud), "SetBaudRate")

    # -- timeout (get/set, seconds like pyserial) --
    @property
    def timeout(self):
        return self._timeout

    @timeout.setter
    def timeout(self, t):
        self._timeout = t
        self._apply_timeouts()

    # -- io --
    def write(self, data):
        data = bytes(data)
        n = C.c_ulong(0)
        buf = (C.c_ubyte * len(data))(*data)
        _chk(_driver().FT_Write(self._h, buf, len(data), C.byref(n)), "FT_Write")
        return n.value

    def read(self, count):
        # FT_Read blocks until `count` bytes OR the read timeout, returning the actual count read
        # (== pyserial read(n) with timeout). ds2._read_exact loops on this until n or empty.
        if count <= 0:
            return b""
        buf = (C.c_ubyte * count)()
        n = C.c_ulong(0)
        _chk(_driver().FT_Read(self._h, buf, count, C.byref(n)), "FT_Read")
        return bytes(buf[:n.value])

    def flush(self):
        # pyserial flush() waits for TX drain; on D2XX the subsequent echo-read already covers timing.
        pass

    def reset_input_buffer(self):
        _chk(_driver().FT_Purge(self._h, _PURGE_RX), "Purge RX")

    def reset_output_buffer(self):
        _chk(_driver().FT_Purge(self._h, _PURGE_TX), "Purge TX")

    def reset_buffers(self):
        _chk(
            _driver().FT_Purge(self._h, _PURGE_RX | _PURGE_TX),
            "Purge RX/TX",
        )

    def queue_status(self):
        """Return the D2XX receive, transmit, and event queue counts."""
        rx = C.c_ulong(0)
        tx = C.c_ulong(0)
        events = C.c_ulong(0)
        _chk(
            _driver().FT_GetStatus(
                self._h, C.byref(rx), C.byref(tx), C.byref(events)
            ),
            "GetStatus",
        )
        return rx.value, tx.value, events.value

    @property
    def in_waiting(self):
        return self.queue_status()[0]

    # -- modem lines (K-line: both driven low/inactive like the pyserial path) --
    def setDTR(self, on):
        _chk(_driver().FT_SetDtr(self._h) if on else _driver().FT_ClrDtr(self._h), "DTR")

    def setRTS(self, on):
        _chk(_driver().FT_SetRts(self._h) if on else _driver().FT_ClrRts(self._h), "RTS")

    # -- lifecycle --
    @property
    def is_open(self):
        return self._open

    def close(self):
        if self._open:
            try:
                _driver().FT_Close(self._h)
            finally:
                self._open = False
