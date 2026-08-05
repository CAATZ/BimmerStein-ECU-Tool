from __future__ import annotations

import pytest

from engines import ch341a


class FakeUSBDevice:
    def __init__(self, responses=()):
        self.responses = list(responses)
        self.writes = []

    def write(self, endpoint, data, timeout):
        self.writes.append((endpoint, bytes(data), timeout))
        return len(data)

    def read(self, endpoint, length, timeout):
        assert endpoint == 0x82
        assert timeout == ch341a.USB_TIMEOUT_MS
        response = self.responses.pop(0)
        assert len(response) == length
        return response


def _programmer(responses=()):
    programmer = object.__new__(ch341a.CH341AProgrammer)
    programmer._device = FakeUSBDevice(responses)
    programmer._usb_util = None
    programmer._interface_number = 0
    programmer._ep_in = 0x82
    programmer._ep_out = 0x02
    programmer._is_open = True
    return programmer


def test_read_stream_uses_a8_and_final_nack():
    assert ch341a._read_stream(0x0FE, 2) == bytes(
        (0xAA, 0x74, 0x82, 0xA0, 0xFE, 0x74, 0x81, 0xA1,
         0xC1, 0xC0, 0x75, 0x00)
    )
    assert ch341a._read_stream(0x100, 1) == bytes(
        (0xAA, 0x74, 0x82, 0xA2, 0x00, 0x74, 0x81, 0xA3,
         0xC0, 0x75, 0x00)
    )


def test_read_splits_at_24c04_a8_boundary():
    programmer = _programmer((b"\xFE\xFF", b"\x00\x01"))

    assert programmer.read(0x0FE, 4) == b"\xFE\xFF\x00\x01"
    commands = [call[1] for call in programmer._device.writes]
    assert commands == [
        ch341a._read_stream(0x0FE, 2),
        ch341a._read_stream(0x100, 2),
    ]


def test_full_read_requires_two_matching_physical_passes():
    matching = tuple(bytes((index,)) * 32 for index in range(16)) * 2
    programmer = _programmer(matching)
    image = programmer.read_full()
    assert len(image) == ch341a.EEPROM_SIZE
    assert len(programmer._device.writes) == 32

    different = list(tuple(bytes((index,)) * 32 for index in range(16)) * 2)
    different[16] = b"\xFF" * 32
    programmer = _programmer(different)
    with pytest.raises(ch341a.ProtocolError, match="0x000"):
        programmer.read_full()


def test_write_splits_page_and_bank_and_verifies(monkeypatch):
    programmer = _programmer((b"\x11", b"\x22" * 16, b"\x33"))
    waits = []
    monkeypatch.setattr(ch341a.time, "sleep", waits.append)

    programmer.write(0x0FF, b"\x11" + b"\x22" * 16 + b"\x33")

    commands = [call[1] for call in programmer._device.writes]
    assert commands[0] == ch341a._write_stream(0x0FF, b"\x11")
    assert commands[2] == ch341a._write_stream(0x100, b"\x22" * 16)
    assert commands[4] == ch341a._write_stream(0x110, b"\x33")
    assert commands[1] == ch341a._read_stream(0x0FF, 1)
    assert commands[3] == ch341a._read_stream(0x100, 16)
    assert commands[5] == ch341a._read_stream(0x110, 1)
    assert waits == [ch341a.WRITE_CYCLE_SECONDS] * 3


def test_range_validation_blocks_wraparound():
    programmer = _programmer()
    with pytest.raises(ValueError, match="outside"):
        programmer.read(0x1FF, 2)
    with pytest.raises(ValueError, match="outside"):
        programmer.write(0x200, b"\x00")


def test_endpoint_discovery_requires_one_bulk_pair():
    class Endpoint:
        def __init__(self, address):
            self.bEndpointAddress = address
            self.bmAttributes = 2
            self.wMaxPacketSize = 32

    class Interface(list):
        bInterfaceNumber = 0

    class Util:
        ENDPOINT_TYPE_BULK = 2
        ENDPOINT_IN = 0x80
        ENDPOINT_OUT = 0

        @staticmethod
        def endpoint_type(attributes):
            return attributes & 3

        @staticmethod
        def endpoint_direction(address):
            return address & 0x80

    interface = Interface((Endpoint(0x02), Endpoint(0x82)))
    assert ch341a._bulk_endpoint_pair((interface,), Util) == (
        interface, 0x82, 0x02)


def test_detection_falls_back_to_windows_driver_without_opening(monkeypatch):
    detected = ch341a.DeviceInfo(
        r"USB\VID_1A86&PID_5512\TEST",
        None,
        None,
        False,
        "Windows service CH341_A64; driver oem186.inf",
    )
    monkeypatch.setattr(
        ch341a,
        "_libusb_devices",
        lambda: (_ for _ in ()).throw(
            ch341a.BackendUnavailable("PyUSB missing")),
    )
    monkeypatch.setattr(
        ch341a, "_windows_registry_devices", lambda: (detected,))

    result = ch341a.enumerate_devices()
    assert len(result) == 1
    assert result[0].identifier == detected.identifier
    assert not result[0].accessible
    assert "CH341_A64" in result[0].detail
    assert "PyUSB missing" in result[0].detail
    with pytest.raises(ch341a.BackendUnavailable, match="CH341_A64"):
        ch341a.open_first()


def test_windows_driver_binding_overrides_libusb_enumeration(monkeypatch):
    wch_bound = ch341a.DeviceInfo(
        r"USB\VID_1A86&PID_5512\TEST",
        None,
        None,
        False,
        "Windows service CH341_A64",
    )
    monkeypatch.setattr(
        ch341a, "_windows_registry_devices", lambda: (wch_bound,))
    monkeypatch.setattr(
        ch341a,
        "_libusb_devices",
        lambda: (object(), object(), object(), (object(),)),
    )

    assert ch341a.enumerate_devices() == (wch_bound,)
