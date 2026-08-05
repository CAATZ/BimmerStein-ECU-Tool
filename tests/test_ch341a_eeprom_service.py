from __future__ import annotations

from dataclasses import dataclass, replace

import pytest

import ch341a_eeprom_service as service


@dataclass(frozen=True)
class FakeDevice:
    serial: str = "CH341A-1"
    accessible: bool = True
    access_error: str = ""


class FakeProgrammer:
    def __init__(self, storage: bytearray):
        self.storage = storage
        self.is_open = False
        self.calls = []

    def __enter__(self):
        self.is_open = True
        return self

    def __exit__(self, *_args):
        self.close()

    def close(self):
        self.is_open = False

    def read_full(self):
        self.calls.append(("read_full",))
        return bytes(self.storage)

    def read(self, address, length):
        self.calls.append(("read", address, length))
        return bytes(self.storage[address:address + length])

    def write(self, address, data):
        data = bytes(data)
        self.calls.append(("write", address, data))
        self.storage[address:address + len(data)] = data


class FakeBackend:
    class CH341AError(RuntimeError):
        pass

    class BackendUnavailable(CH341AError):
        pass

    class DeviceNotFound(CH341AError):
        pass

    class MultipleDevices(CH341AError):
        pass

    def __init__(self, image: bytes, devices=(FakeDevice(),)):
        self.storage = bytearray(image)
        self.devices = tuple(devices)
        self.opened = []
        self.detection_error = None

    def enumerate_devices(self):
        if self.detection_error is not None:
            raise self.detection_error
        return self.devices

    def open_first(self):
        programmer = FakeProgrammer(self.storage)
        self.opened.append(programmer)
        return programmer


def _image_with_marker(marker: bytes) -> bytes:
    image = bytearray(service.EEPROM_SIZE)
    image[service.RECOVERY_OFFSET:service.RECOVERY_OFFSET + 3] = marker
    return bytes(image)


def _normal_image() -> bytes:
    return _image_with_marker(service.NORMAL_PROGRESSION)


def _writeable_image(variant="MS41.3") -> bytes:
    image = bytearray(service.EEPROM_SIZE)
    image[0] = 0x31
    image[-1] = 0xA5
    for field in service.eeprom_ram.fields_for_variant(variant):
        if field.checked:
            payload = image[field.offset:field.offset + field.length - 2]
            image[field.offset + field.length - 2:field.offset + field.length] = (
                service.eeprom_ram.additive_check(payload).to_bytes(2, "little")
            )
    offset = service.eeprom_ram.transmission_offset(variant)
    image[offset:offset + 4] = bytes.fromhex("0e 00 0f 00")
    image[service.RECOVERY_OFFSET:service.RECOVERY_OFFSET + 3] = (
        service.NORMAL_PROGRESSION
    )
    return bytes(image)


def _install_backend(monkeypatch, backend):
    monkeypatch.setattr(service, "_load_backend", lambda: backend)


def test_detect_status_distinguishes_no_device_backend_and_driver(monkeypatch):
    backend = FakeBackend(_normal_image(), devices=())
    _install_backend(monkeypatch, backend)
    status = service.detect_status()
    assert status.state == "not_found"
    assert not status.detected
    assert not status.ready

    backend.detection_error = backend.BackendUnavailable("libusb unavailable")
    status = service.detect_status()
    assert status.state == "backend_unavailable"
    assert "libusb unavailable" in status.message
    assert not status.detected

    backend.detection_error = None
    backend.devices = (
        FakeDevice(accessible=False, access_error="WCH CH341_A64 driver"),
    )
    status = service.detect_status()
    assert status.state == "driver_inaccessible"
    assert status.detected
    assert not status.ready
    assert "WCH CH341_A64 driver" in status.message


def test_detect_status_requires_exactly_one_accessible_device(monkeypatch):
    backend = FakeBackend(_normal_image())
    _install_backend(monkeypatch, backend)
    assert service.detect_status().ready

    backend.devices = (FakeDevice("one"), FakeDevice("two"))
    status = service.detect_status()
    assert status.state == "multiple"
    assert status.detected
    assert not status.ready


def test_inaccessible_detected_driver_never_opens_or_reads(tmp_path, monkeypatch):
    backend = FakeBackend(
        _normal_image(),
        devices=(
            FakeDevice(
                accessible=False,
                access_error="WCH CH341_A64 driver",
            ),
        ),
    )
    _install_backend(monkeypatch, backend)

    with pytest.raises(service.DeviceNotReady) as raised:
        service.read_eeprom(tmp_path / "must-not-exist.bin")

    assert raised.value.status.state == "driver_inaccessible"
    assert backend.opened == []
    assert not (tmp_path / "must-not-exist.bin").exists()


def test_read_eeprom_saves_exact_immutable_capture_with_warnings(
    tmp_path, monkeypatch
):
    backend = FakeBackend(_normal_image())
    _install_backend(monkeypatch, backend)
    output = tmp_path / "physical.bin"

    capture = service.read_eeprom(output)

    assert capture.image == _normal_image()
    assert capture.path == output
    assert output.read_bytes() == _normal_image()
    assert capture.seedable
    assert capture.warnings
    assert backend.opened[0].calls == [("read_full",)]
    assert not backend.opened[0].is_open

    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        service.read_eeprom(output)
    assert len(backend.opened) == 1


def test_read_eeprom_can_return_unsaved_capture(monkeypatch):
    backend = FakeBackend(_normal_image())
    _install_backend(monkeypatch, backend)

    capture = service.read_eeprom()

    assert capture.image == _normal_image()
    assert capture.path is None


def test_read_eeprom_can_capture_without_assuming_a_layout(monkeypatch):
    backend = FakeBackend(_normal_image())
    _install_backend(monkeypatch, backend)

    capture = service.read_eeprom(variant=None)

    assert capture.image == _normal_image()
    assert capture.variant is None
    assert capture.warnings == (
        "WARNING: EEPROM layout is unresolved; lower checked records "
        "were not interpreted.",
    )


def test_generic_write_uses_checked_byte_plan_and_exact_full_readback(
    tmp_path, monkeypatch
):
    original = _writeable_image()
    backend = FakeBackend(original)
    _install_backend(monkeypatch, backend)
    target = service.eeprom_ram.set_transmission_mode(original, "at", "MS41.3")
    before_path = tmp_path / "before.bin"
    prompts = []

    def confirm(message):
        assert before_path.read_bytes() == original
        prompts.append(message)
        return True

    result = service.write_eeprom(
        target,
        "MS41.3",
        before_path,
        confirm=confirm,
    )

    assert len(backend.opened) == 1
    assert len(prompts) == 1
    assert result.before.image == original
    assert result.after.image == target
    assert result.changed_offsets == service.eeprom_ram.changed_offsets(
        original, target
    )
    assert result.before.path.read_bytes() == original
    assert result.after.path.read_bytes() == target
    writes = [
        call
        for call in backend.opened[0].calls
        if call[0] == "write"
    ]
    assert writes == [
        ("write", operation.offset, bytes((operation.replacement,)))
        for operation in service.eeprom_ram.build_write_plan(
            original, target, "MS41.3"
        )
    ]


def test_generic_write_identical_target_is_saved_zero_write_success(
    tmp_path, monkeypatch
):
    original = _writeable_image()
    backend = FakeBackend(original)
    _install_backend(monkeypatch, backend)
    before_path = tmp_path / "before.bin"

    result = service.write_eeprom(
        original,
        "MS41.3",
        before_path,
        confirm=lambda _message: pytest.fail("confirmation must not be requested"),
    )

    assert result.before is result.after
    assert result.changed_offsets == ()
    assert result.before.path == before_path
    assert before_path.read_bytes() == original
    assert not (tmp_path / "before.after.bin").exists()
    assert len(backend.opened) == 1
    assert backend.opened[0].calls == [("read_full",)]


def test_generic_write_cancel_keeps_before_image_and_sends_no_write(
    tmp_path, monkeypatch
):
    original = _writeable_image()
    target = service.eeprom_ram.set_transmission_mode(
        original, "at", "MS41.3")
    backend = FakeBackend(original)
    _install_backend(monkeypatch, backend)
    before_path = tmp_path / "before.bin"

    with pytest.raises(service.WriteCancelled) as raised:
        service.write_eeprom(
            target,
            "MS41.3",
            before_path,
            confirm=lambda _message: False,
        )

    assert raised.value.before.path == before_path
    assert before_path.read_bytes() == original
    assert not (tmp_path / "before.after.bin").exists()
    assert backend.opened[0].calls == [("read_full",)]


def test_restore_pre_seed_state_is_exact_inverse(tmp_path, monkeypatch):
    original_image = _writeable_image()
    backend = FakeBackend(original_image)
    _install_backend(monkeypatch, backend)
    original = service.read_eeprom(tmp_path / "original.bin")
    backend.storage[
        service.RECOVERY_OFFSET:service.RECOVERY_OFFSET + 3
    ] = service.RECOVERY_PROGRESSION
    seeded = service.read_eeprom(tmp_path / "seeded.bin")

    result = service.restore_pre_seed(
        seeded,
        original,
        tmp_path / "restore-before.bin",
        confirm=lambda _message: True,
    )

    assert result.after.image == original_image
    assert result.changed_offsets == service.RECOVERY_OFFSETS


def test_restore_pre_seed_still_rejects_a_stale_seeded_capture(
    tmp_path, monkeypatch
):
    original_image = _writeable_image()
    backend = FakeBackend(original_image)
    _install_backend(monkeypatch, backend)
    original = service.read_eeprom(tmp_path / "original.bin")
    backend.storage[
        service.RECOVERY_OFFSET:service.RECOVERY_OFFSET + 3
    ] = service.RECOVERY_PROGRESSION
    seeded = service.read_eeprom(tmp_path / "seeded.bin")
    backend.storage[0x100] ^= 0xFF

    with pytest.raises(service.SeedRefused, match="no longer matches"):
        service.restore_pre_seed(
            seeded,
            original,
            tmp_path / "restore-before.bin",
            confirm=lambda _message: True,
        )

    assert not (tmp_path / "restore-before.bin").exists()
    assert not any(
        call[0] == "write" for programmer in backend.opened for call in programmer.calls
    )


@pytest.mark.parametrize(
    ("change", "match"),
    (({"device": object()}, "different CH341A"),
     ({"variant": "MS41.1"}, "different MS41 layout")),
)
def test_restore_pre_seed_rejects_mismatched_original(
    tmp_path, monkeypatch, change, match
):
    original_image = _writeable_image()
    backend = FakeBackend(original_image)
    _install_backend(monkeypatch, backend)
    original = service.read_eeprom(tmp_path / "original.bin")
    seeded_image = bytearray(original_image)
    seeded_image[
        service.RECOVERY_OFFSET:service.RECOVERY_OFFSET + 3
    ] = service.RECOVERY_PROGRESSION
    seeded_path = tmp_path / "seeded.bin"
    seeded_path.write_bytes(seeded_image)
    seeded = service._capture(
        bytes(seeded_image), seeded_path, original.device, original.variant)

    with pytest.raises(service.SeedRefused, match=match):
        service.restore_pre_seed(
            seeded,
            replace(original, **change),
            tmp_path / "restore-before.bin",
            confirm=lambda _message: True,
        )


def test_read_eeprom_rejects_non_512_byte_result(tmp_path, monkeypatch):
    backend = FakeBackend(bytes(511))
    _install_backend(monkeypatch, backend)

    with pytest.raises(service.StableReadError, match="511 bytes"):
        service.read_eeprom(tmp_path / "short.bin")

    assert not (tmp_path / "short.bin").exists()


@pytest.mark.parametrize("fill", (0x00, 0xFF, 0xA5))
def test_read_eeprom_rejects_uniform_physical_reads(
    tmp_path, monkeypatch, fill
):
    backend = FakeBackend(bytes((fill,)) * service.EEPROM_SIZE)
    _install_backend(monkeypatch, backend)
    output = tmp_path / f"uniform-{fill:02x}.bin"

    with pytest.raises(service.StableReadError, match=f"0x{fill:02X}"):
        service.read_eeprom(output)

    assert not output.exists()
    assert backend.opened[0].calls == [("read_full",)]


def test_seed_ecu_requires_prior_saved_capture(tmp_path, monkeypatch):
    backend = FakeBackend(_normal_image())
    _install_backend(monkeypatch, backend)
    capture = service.read_eeprom(tmp_path / "prior.bin")
    capture.path.unlink()

    with pytest.raises(service.SeedRefused, match="no longer available"):
        service.seed_ecu(
            capture,
            tmp_path / "before.bin",
            confirm=lambda _message: True,
        )
    assert len(backend.opened) == 1


def test_seed_ecu_cancels_after_mandatory_backup_without_writing(
    tmp_path, monkeypatch
):
    backend = FakeBackend(_normal_image())
    _install_backend(monkeypatch, backend)
    capture = service.read_eeprom(tmp_path / "prior.bin")
    before_path = tmp_path / "before.bin"

    with pytest.raises(service.SeedCancelled) as raised:
        service.seed_ecu(
            capture,
            before_path,
            confirm=lambda message: (
                "00 01 02 -> 01 02 03" in message and False
            ),
        )

    assert raised.value.before.path == before_path
    assert before_path.read_bytes() == _normal_image()
    assert not (tmp_path / "before.after.bin").exists()
    assert not any(
        call[0] == "write" for programmer in backend.opened for call in programmer.calls
    )


@pytest.mark.parametrize(
    "source_marker",
    service.SEEDABLE_PROGRESSIONS,
    ids=("state-0", "state-3"),
)
def test_seed_ecu_writes_only_progression_and_proves_full_exact_diff(
    tmp_path, monkeypatch, source_marker
):
    original = _image_with_marker(source_marker)
    backend = FakeBackend(original)
    _install_backend(monkeypatch, backend)
    capture = service.read_eeprom(tmp_path / "prior.bin")
    before_path = tmp_path / "before.bin"
    prompts = []

    result = service.seed_ecu(
        capture,
        before_path,
        confirm=lambda message: prompts.append(message) or True,
    )

    expected_after = bytearray(original)
    expected_after[
        service.RECOVERY_OFFSET:service.RECOVERY_OFFSET + 3
    ] = service.RECOVERY_PROGRESSION
    expected_after = bytes(expected_after)
    assert result.changed_offsets == service.RECOVERY_OFFSETS
    assert result.before.image == original
    assert result.after.image == expected_after
    assert result.before.path.read_bytes() == original
    assert result.after.path == tmp_path / "before.after.bin"
    assert result.after.path.read_bytes() == expected_after
    assert result.before.warnings
    assert prompts and "0x1DD..0x1DF" in prompts[0]
    assert f"{source_marker.hex(' ')} -> 01 02 03" in prompts[0]

    seed_programmer = backend.opened[1]
    writes = [call for call in seed_programmer.calls if call[0] == "write"]
    assert writes == [
        ("write", service.RECOVERY_OFFSET, service.RECOVERY_PROGRESSION)
    ]
    assert seed_programmer.calls == [
        ("read_full",),
        ("read", service.RECOVERY_OFFSET, 3),
        ("write", service.RECOVERY_OFFSET, service.RECOVERY_PROGRESSION),
        ("read", service.RECOVERY_OFFSET, 3),
        ("read_full",),
    ]


def test_seed_ecu_rejects_stale_capture_before_backup_or_write(
    tmp_path, monkeypatch
):
    backend = FakeBackend(_normal_image())
    _install_backend(monkeypatch, backend)
    capture = service.read_eeprom(tmp_path / "prior.bin")
    backend.storage[0x100] = 0x55

    with pytest.raises(service.SeedRefused, match="no longer matches"):
        service.seed_ecu(
            capture,
            tmp_path / "before.bin",
            confirm=lambda _message: True,
        )

    assert not (tmp_path / "before.bin").exists()
    assert not any(
        call[0] == "write" for programmer in backend.opened for call in programmer.calls
    )


@pytest.mark.parametrize("fill", (0x00, 0xFF, 0xA5))
def test_seed_ecu_rejects_uniform_prewrite_read_without_writing(
    tmp_path, monkeypatch, fill
):
    original = _image_with_marker(service.NORMAL_STATE_3_PROGRESSION)
    backend = FakeBackend(original)
    _install_backend(monkeypatch, backend)
    capture = service.read_eeprom(tmp_path / "prior.bin")
    backend.storage[:] = bytes((fill,)) * service.EEPROM_SIZE
    confirmed = []

    with pytest.raises(service.SeedRefused, match="no write was sent"):
        service.seed_ecu(
            capture,
            tmp_path / "before.bin",
            confirm=lambda _message: confirmed.append(True) or True,
        )

    assert confirmed == []
    assert not (tmp_path / "before.bin").exists()
    assert not any(
        call[0] == "write" for programmer in backend.opened for call in programmer.calls
    )


def test_seed_ecu_refuses_already_seeded_or_malformed_progression(
    tmp_path, monkeypatch
):
    for marker, match in (
        (service.RECOVERY_PROGRESSION, "already seeded"),
        (b"\x00\x02\x03", "malformed or unsupported"),
        (b"\x02\x03\x04", "malformed or unsupported"),
    ):
        image = bytearray(_normal_image())
        image[service.RECOVERY_OFFSET:service.RECOVERY_OFFSET + 3] = marker
        backend = FakeBackend(bytes(image))
        _install_backend(monkeypatch, backend)
        capture = service.read_eeprom(tmp_path / f"{marker.hex()}.bin")

        with pytest.raises(service.SeedRefused, match=match):
            service.seed_ecu(
                capture,
                tmp_path / f"{marker.hex()}.before.bin",
                confirm=lambda _message: True,
            )
        assert len(backend.opened) == 1


def test_seed_ecu_archives_and_rejects_unexpected_readback_diff(
    tmp_path, monkeypatch
):
    class CorruptingProgrammer(FakeProgrammer):
        def write(self, address, data):
            super().write(address, data)
            self.storage[0x100] ^= 0xFF

    backend = FakeBackend(_normal_image())

    def open_corrupting():
        programmer = CorruptingProgrammer(backend.storage)
        backend.opened.append(programmer)
        return programmer

    backend.open_first = open_corrupting
    _install_backend(monkeypatch, backend)
    capture = service.read_eeprom(tmp_path / "prior.bin")

    with pytest.raises(service.SeedVerificationError) as raised:
        service.seed_ecu(
            capture,
            tmp_path / "before.bin",
            confirm=lambda _message: True,
        )

    assert "0x100" in str(raised.value)
    assert raised.value.after is not None
    assert raised.value.after.path.exists()
    assert (tmp_path / "before.bin").read_bytes() == _normal_image()


def test_seed_ecu_turns_write_transport_error_into_uncertain_verification(
    tmp_path, monkeypatch
):
    backend = FakeBackend(_normal_image())

    class FailingProgrammer(FakeProgrammer):
        def write(self, address, data):
            super().write(address, data)
            raise backend.CH341AError("lost USB reply")

    def open_failing():
        programmer = FailingProgrammer(backend.storage)
        backend.opened.append(programmer)
        return programmer

    _install_backend(monkeypatch, backend)
    capture = service.read_eeprom(tmp_path / "prior.bin")
    backend.open_first = open_failing

    with pytest.raises(service.SeedVerificationError) as raised:
        service.seed_ecu(
            capture,
            tmp_path / "before.bin",
            confirm=lambda _message: True,
        )

    assert "new stable full-chip read" in str(raised.value)
    assert raised.value.after is None
    assert raised.value.before.path.read_bytes() == _normal_image()
    assert not (tmp_path / "before.after.bin").exists()


def test_seed_ecu_reports_after_image_archive_failure_as_unverified(
    tmp_path, monkeypatch
):
    backend = FakeBackend(_normal_image())
    _install_backend(monkeypatch, backend)
    capture = service.read_eeprom(tmp_path / "prior.bin")
    real_save = service.eeprom_ram.save_capture
    saves = []

    def fail_second_save(path, image):
        saves.append(path)
        if len(saves) == 2:
            raise OSError("disk full")
        return real_save(path, image)

    monkeypatch.setattr(service.eeprom_ram, "save_capture", fail_second_save)

    with pytest.raises(service.SeedVerificationError) as raised:
        service.seed_ecu(
            capture,
            tmp_path / "before.bin",
            confirm=lambda _message: True,
        )

    assert "could not be archived" in str(raised.value)
    assert raised.value.after is None
    assert (tmp_path / "before.bin").exists()
    assert not (tmp_path / "before.after.bin").exists()
