"""Native CH341A USB/I2C transport for a 24C04 EEPROM.

PyUSB and libusb are loaded only when USB discovery or access is requested.
The Windows registry fallback is discovery-only, so a CH341A using WCH's
vendor driver can be reported without opening it or sending endpoint traffic.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import os
import time
from dataclasses import dataclass
from pathlib import Path


VID = 0x1A86
PID = 0x5512
EEPROM_SIZE = 512
PAGE_SIZE = 16
USB_PACKET_SIZE = 32
USB_TIMEOUT_MS = 2_000
USB_DRAIN_TIMEOUT_MS = 50
MAX_STALE_PACKETS = 32
WRITE_CYCLE_SECONDS = 0.010

_I2C_STREAM = 0xAA
_I2C_START = 0x74
_I2C_STOP = 0x75
_I2C_OUT = 0x80
_I2C_IN = 0xC0
_I2C_SET_100KHZ = 0x61
_I2C_END = 0x00


class CH341AError(RuntimeError):
    """Base error for native CH341A access."""


class BackendUnavailable(CH341AError):
    """PyUSB/libusb or a compatible device driver is unavailable."""


class DeviceNotFound(CH341AError):
    """No CH341A is connected."""


class MultipleDevices(CH341AError):
    """The operation cannot choose one programmer unambiguously."""


class ProtocolError(CH341AError):
    """USB/I2C framing, transfer, or readback validation failed."""


@dataclass(frozen=True)
class DeviceInfo:
    identifier: str
    bus: int | None
    address: int | None
    accessible: bool
    detail: str = ""


def _bundled_libusb_path(_name: str) -> str | None:
    if os.name == "nt":
        try:
            import usb1

            bundled = Path(usb1.__file__).with_name("libusb-1.0.dll")
            if bundled.is_file():
                return str(bundled)
        except (ImportError, OSError, TypeError):
            pass
    return ctypes.util.find_library(_name)


def _usb_stack():
    try:
        import usb.backend.libusb1
        import usb.core
        import usb.util
    except (ImportError, ModuleNotFoundError) as error:
        raise BackendUnavailable(f"PyUSB is not installed: {error}") from error
    backend = usb.backend.libusb1.get_backend(find_library=_bundled_libusb_path)
    if backend is None:
        raise BackendUnavailable("libusb 1.x could not be loaded")
    return usb.core, usb.util, backend


def _libusb_devices():
    core, util, backend = _usb_stack()
    try:
        devices = tuple(
            core.find(
                find_all=True,
                idVendor=VID,
                idProduct=PID,
                backend=backend,
            )
            or ()
        )
    except Exception as error:
        raise BackendUnavailable(f"libusb enumeration failed: {error}") from error
    return core, util, backend, devices


def _device_info(device) -> DeviceInfo:
    bus = getattr(device, "bus", None)
    address = getattr(device, "address", None)
    return DeviceInfo(
        f"libusb:{bus if bus is not None else '?'}:"
        f"{address if address is not None else '?'}",
        bus,
        address,
        True,
        f"USB VID_{VID:04X}&PID_{PID:04X} through libusb",
    )


def _windows_device_present(instance_id: str) -> bool:
    try:
        cfgmgr32 = ctypes.WinDLL("cfgmgr32", use_last_error=True)
        locate = cfgmgr32.CM_Locate_DevNodeW
        locate.argtypes = (
            ctypes.POINTER(ctypes.c_ulong),
            ctypes.c_wchar_p,
            ctypes.c_ulong,
        )
        locate.restype = ctypes.c_ulong
        devinst = ctypes.c_ulong()
        return locate(ctypes.byref(devinst), instance_id, 0) == 0
    except (AttributeError, OSError):
        return False


def _registry_value(key, name: str) -> str:
    try:
        import winreg

        return str(winreg.QueryValueEx(key, name)[0]).strip()
    except (OSError, TypeError, ValueError):
        return ""


def _windows_registry_devices() -> tuple[DeviceInfo, ...]:
    if os.name != "nt":
        return ()
    try:
        import winreg

        base_path = (
            rf"SYSTEM\CurrentControlSet\Enum\USB\VID_{VID:04X}&PID_{PID:04X}"
        )
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base_path) as base:
            instance_names = []
            index = 0
            while True:
                try:
                    instance_names.append(winreg.EnumKey(base, index))
                except OSError:
                    break
                index += 1
    except OSError:
        return ()

    found = []
    for instance_name in instance_names:
        instance_id = (
            rf"USB\VID_{VID:04X}&PID_{PID:04X}\{instance_name}"
        )
        if not _windows_device_present(instance_id):
            continue
        try:
            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                base_path + "\\" + instance_name,
            ) as key:
                service = _registry_value(key, "Service")
                driver_key = _registry_value(key, "Driver")
                description = _registry_value(key, "DeviceDesc")
        except OSError:
            service = driver_key = description = ""

        inf_path = ""
        if driver_key:
            try:
                with winreg.OpenKey(
                    winreg.HKEY_LOCAL_MACHINE,
                    rf"SYSTEM\CurrentControlSet\Control\Class\{driver_key}",
                ) as key:
                    inf_path = _registry_value(key, "InfPath")
            except OSError:
                pass
        details = [f"USB VID_{VID:04X}&PID_{PID:04X}"]
        if service:
            details.append(f"Windows service {service}")
        if inf_path:
            details.append(f"driver {inf_path}")
        if description:
            details.append(description.rsplit(";", 1)[-1])
        compatible = service.casefold() in {
            "winusb",
            "libusbk",
            "libusb0",
        }
        found.append(
            DeviceInfo(
                instance_id,
                None,
                None,
                compatible,
                "; ".join(details),
            )
        )
    return tuple(found)


def enumerate_devices() -> tuple[DeviceInfo, ...]:
    """Discover CH341A devices without opening them or sending USB traffic."""
    windows_devices = _windows_registry_devices()
    stack_error = None
    try:
        _core, _util, _backend, devices = _libusb_devices()
    except BackendUnavailable as error:
        stack_error = error
        devices = ()
    # Windows can enumerate devices through libusb even when their interface is
    # still owned by WCH's vendor driver. Registry service binding is the
    # authoritative access gate; USB enumeration alone is not.
    if windows_devices:
        if stack_error is not None or not devices:
            unavailable = stack_error or BackendUnavailable(
                "libusb did not enumerate this Windows device")
            return tuple(
                DeviceInfo(
                    device.identifier,
                    device.bus,
                    device.address,
                    False,
                    f"{device.detail}; {unavailable}",
                )
                for device in windows_devices
            )
        return windows_devices
    if devices:
        return tuple(_device_info(device) for device in devices)

    if stack_error is not None:
        raise stack_error
    return ()


def _validate_range(address: int, length: int) -> None:
    if not isinstance(address, int) or not isinstance(length, int):
        raise TypeError("EEPROM address and length must be integers")
    if address < 0 or length < 0 or address + length > EEPROM_SIZE:
        raise ValueError(
            f"EEPROM range 0x{address:X}+{length} is outside 0x000..0x1FF"
        )


def _control_byte(address: int, *, read: bool) -> int:
    return 0xA0 | ((address >> 7) & 0x02) | int(read)


def _read_stream(address: int, length: int) -> bytes:
    if not 1 <= length <= USB_PACKET_SIZE:
        raise ValueError("one CH341A read transaction must contain 1..32 bytes")
    commands = [
        _I2C_STREAM,
        _I2C_START,
        _I2C_OUT | 2,
        _control_byte(address, read=False),
        address & 0xFF,
        _I2C_START,
        _I2C_OUT | 1,
        _control_byte(address, read=True),
    ]
    if length > 1:
        commands.append(_I2C_IN | (length - 1))
    commands.extend((_I2C_IN, _I2C_STOP, _I2C_END))
    return bytes(commands)


def _write_stream(address: int, data: bytes) -> bytes:
    data = bytes(data)
    if not 1 <= len(data) <= PAGE_SIZE:
        raise ValueError("one 24C04 page write must contain 1..16 bytes")
    command = bytes(
        (
            _I2C_STREAM,
            _I2C_START,
            _I2C_OUT | (len(data) + 2),
            _control_byte(address, read=False),
            address & 0xFF,
        )
    ) + data + bytes((_I2C_STOP, _I2C_END))
    if len(command) > USB_PACKET_SIZE:
        raise ProtocolError("CH341A stream exceeds its 32-byte USB packet")
    return command


def _read_chunks(address: int, length: int):
    while length:
        size = min(USB_PACKET_SIZE, 0x100 - (address & 0xFF), length)
        yield address, size
        address += size
        length -= size


def _write_chunks(address: int, data: bytes):
    offset = 0
    while offset < len(data):
        current = address + offset
        size = min(
            PAGE_SIZE - (current % PAGE_SIZE),
            0x100 - (current & 0xFF),
            len(data) - offset,
        )
        yield current, data[offset:offset + size]
        offset += size


def _bulk_endpoint_pair(configuration, util):
    for interface in configuration:
        bulk_in = bulk_out = None
        for endpoint in interface:
            if util.endpoint_type(endpoint.bmAttributes) != util.ENDPOINT_TYPE_BULK:
                continue
            direction = util.endpoint_direction(endpoint.bEndpointAddress)
            if direction == util.ENDPOINT_IN:
                bulk_in = endpoint
            elif direction == util.ENDPOINT_OUT:
                bulk_out = endpoint
        if bulk_in is not None and bulk_out is not None:
            if (
                int(bulk_in.wMaxPacketSize) < USB_PACKET_SIZE
                or int(bulk_out.wMaxPacketSize) < USB_PACKET_SIZE
            ):
                raise ProtocolError("CH341A bulk endpoints are smaller than 32 bytes")
            return interface, int(bulk_in.bEndpointAddress), int(
                bulk_out.bEndpointAddress)
    raise ProtocolError("CH341A bulk IN/OUT endpoints were not found")


class CH341AProgrammer:
    def __init__(self, device, usb_util, usb_core):
        self._device = device
        self._usb_util = usb_util
        self._usb_core = usb_core
        self._interface_number = None
        self._claimed = False
        self._ep_in = None
        self._ep_out = None
        self._is_open = False
        try:
            try:
                configuration = device.get_active_configuration()
            except Exception:
                device.set_configuration()
                configuration = device.get_active_configuration()
            interface, self._ep_in, self._ep_out = _bulk_endpoint_pair(
                configuration, usb_util)
            self._interface_number = int(interface.bInterfaceNumber)
            usb_util.claim_interface(device, self._interface_number)
            self._claimed = True
            self._is_open = True
            self._send(bytes((_I2C_STREAM, _I2C_SET_100KHZ, _I2C_END)))
            self._drain_input()
        except CH341AError:
            self.close()
            raise
        except Exception as error:
            self.close()
            raise BackendUnavailable(
                "CH341A could not be opened through libusb. On Windows, bind "
                f"WinUSB/libusbK deliberately before retrying: {error}"
            ) from error

    @property
    def is_open(self) -> bool:
        return self._is_open

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()

    def _require_open(self) -> None:
        if not self._is_open:
            raise ProtocolError("CH341A programmer is closed")

    def _send(self, command: bytes) -> None:
        self._require_open()
        if len(command) > USB_PACKET_SIZE:
            raise ProtocolError("CH341A command exceeds one 32-byte USB packet")
        try:
            written = self._device.write(
                self._ep_out, command, timeout=USB_TIMEOUT_MS)
        except Exception as error:
            raise ProtocolError(f"CH341A USB write failed: {error}") from error
        if int(written) != len(command):
            raise ProtocolError(
                f"CH341A short USB write ({written}/{len(command)} bytes)")

    def _exchange(self, command: bytes, expected: int) -> bytes:
        self._send(command)
        try:
            response = bytes(
                self._device.read(
                    self._ep_in, expected, timeout=USB_TIMEOUT_MS)
            )
        except Exception as error:
            raise ProtocolError(f"CH341A USB read failed: {error}") from error
        if len(response) != expected:
            raise ProtocolError(
                f"CH341A short USB read ({len(response)}/{expected} bytes)")
        return response

    def _drain_input(self) -> None:
        for _ in range(MAX_STALE_PACKETS):
            try:
                response = self._device.read(
                    self._ep_in,
                    USB_PACKET_SIZE,
                    timeout=USB_DRAIN_TIMEOUT_MS,
                )
            except self._usb_core.USBTimeoutError:
                return
            except Exception as error:
                raise ProtocolError(
                    f"CH341A stale USB input drain failed: {error}"
                ) from error
            if not response:
                return
        raise ProtocolError("CH341A input did not become idle")

    def _read_once(self, address: int, length: int) -> bytes:
        output = bytearray()
        for current, size in _read_chunks(address, length):
            output.extend(self._exchange(_read_stream(current, size), size))
        return bytes(output)

    def read(self, address: int, length: int) -> bytes:
        self._require_open()
        _validate_range(address, length)
        return self._read_once(address, length)

    def read_full(self) -> bytes:
        """Return only two independently matching complete physical reads."""
        first = self._read_once(0, EEPROM_SIZE)
        second = self._read_once(0, EEPROM_SIZE)
        if first != second:
            mismatch = next(
                index
                for index, pair in enumerate(zip(first, second))
                if pair[0] != pair[1]
            )
            raise ProtocolError(
                f"two complete EEPROM reads differ at 0x{mismatch:03X}")
        return first

    def write(self, address: int, data: bytes) -> None:
        self._require_open()
        data = bytes(data)
        _validate_range(address, len(data))
        if not data:
            return
        for current, chunk in _write_chunks(address, data):
            self._send(_write_stream(current, chunk))
            # AT24C04C specifies a 5 ms maximum page-write cycle. A fixed,
            # bounded 10 ms wait avoids an unbounded ACK-poll loop.
            time.sleep(WRITE_CYCLE_SECONDS)
            if self._read_once(current, len(chunk)) != chunk:
                raise ProtocolError(
                    f"24C04 page readback failed at 0x{current:03X}")

    def close(self) -> None:
        self._is_open = False
        try:
            if self._claimed and self._interface_number is not None:
                self._usb_util.release_interface(
                    self._device, self._interface_number)
        except Exception:
            pass
        self._claimed = False
        try:
            self._usb_util.dispose_resources(self._device)
        except Exception:
            pass


def open_first() -> CH341AProgrammer:
    try:
        core, util, _backend, devices = _libusb_devices()
    except BackendUnavailable as error:
        detected = _windows_registry_devices()
        if detected:
            raise BackendUnavailable(
                f"{detected[0].detail}; native access requires WinUSB/libusbK"
            ) from error
        raise
    if not devices:
        detected = _windows_registry_devices()
        if detected:
            raise BackendUnavailable(
                f"{detected[0].detail}; native access requires WinUSB/libusbK")
        raise DeviceNotFound("No CH341A programmer was detected.")
    if len(devices) != 1:
        raise MultipleDevices(
            f"Detected {len(devices)} accessible CH341A programmers; connect exactly one."
        )
    return CH341AProgrammer(devices[0], util, core)
