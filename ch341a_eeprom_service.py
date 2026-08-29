"""Guarded high-level CH341A operations for an MS41 24C04 EEPROM."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from engines.softbsl import eeprom_ram


_ch341a = None


EEPROM_SIZE = eeprom_ram.EEPROM_SIZE
RECOVERY_OFFSET = 0x1DD
NORMAL_PROGRESSION = bytes((0, 1, 2))
NORMAL_STATE_3_PROGRESSION = bytes((3, 4, 5))
SEEDABLE_PROGRESSIONS = (NORMAL_PROGRESSION, NORMAL_STATE_3_PROGRESSION)
RECOVERY_PROGRESSION = bytes((1, 2, 3))
RECOVERY_OFFSETS = tuple(range(RECOVERY_OFFSET, RECOVERY_OFFSET + 3))


class CH341AEepromServiceError(RuntimeError):
    """A high-level EEPROM policy or verification gate failed."""


class DeviceNotReady(CH341AEepromServiceError):
    def __init__(self, status: "DeviceStatus"):
        self.status = status
        super().__init__(status.message)


class StableReadError(CH341AEepromServiceError):
    """The programmer did not provide one stable, exact 512-byte image."""


class SeedRefused(CH341AEepromServiceError):
    """The prior capture or recovery progression is not seedable."""


class SeedCancelled(CH341AEepromServiceError):
    """The operator declined the exact marker write."""

    def __init__(self, before: "Capture"):
        self.before = before
        super().__init__(
            "Seed ECU was cancelled after the immutable before-image was saved; "
            "no write was sent."
        )


class SeedVerificationError(CH341AEepromServiceError):
    """Readback could not prove that only the recovery progression changed."""

    def __init__(
        self,
        message: str,
        *,
        before: "Capture",
        after: "Capture | None" = None,
    ):
        self.before = before
        self.after = after
        super().__init__(message)


class WriteCancelled(CH341AEepromServiceError):
    """The operator declined a prepared EEPROM image write."""

    def __init__(self, before: "Capture"):
        self.before = before
        super().__init__(
            "EEPROM write was cancelled after the immutable before-image was "
            "saved; no write was sent."
        )


class WriteVerificationError(CH341AEepromServiceError):
    """A full-image write could not be proven exact."""

    def __init__(
        self,
        message: str,
        *,
        before: "Capture",
        after: "Capture | None" = None,
    ):
        self.before = before
        self.after = after
        super().__init__(message)


@dataclass(frozen=True)
class DeviceStatus:
    state: str
    message: str
    devices: tuple[object, ...] = ()

    @property
    def ready(self) -> bool:
        return self.state == "ready"

    @property
    def detected(self) -> bool:
        return bool(self.devices)


@dataclass(frozen=True)
class Capture:
    image: bytes
    path: Path | None
    sha256: str
    warnings: tuple[str, ...]
    device: object
    variant: str | None = "MS41.3"

    @property
    def marker(self) -> bytes:
        return self.image[RECOVERY_OFFSET:RECOVERY_OFFSET + 3]

    @property
    def seedable(self) -> bool:
        return self.marker in SEEDABLE_PROGRESSIONS


@dataclass(frozen=True)
class SeedResult:
    before: Capture
    after: Capture
    changed_offsets: tuple[int, ...]


@dataclass(frozen=True)
class WriteResult:
    before: Capture
    after: Capture
    changed_offsets: tuple[int, ...]


def _load_backend():
    global _ch341a
    if _ch341a is None:
        from engines import ch341a
        _ch341a = ch341a
    return _ch341a


def configure_backend(backend) -> None:
    """Install an injected platform backend before discovery."""
    global _ch341a
    _ch341a = backend


def _device_accessible(device: object) -> bool:
    return getattr(device, "accessible", True) is not False


def _device_access_detail(device: object) -> str:
    return str(
        getattr(device, "access_error", "")
        or getattr(device, "detail", "")
        or getattr(device, "driver", "")
    ).strip()


def detect_status() -> DeviceStatus:
    """Perform USB discovery only; this never reads or writes the EEPROM."""
    try:
        backend = _load_backend()
    except (ImportError, ModuleNotFoundError) as error:
        return DeviceStatus(
            "backend_unavailable",
            f"CH341A native USB backend is unavailable: {error}",
        )
    try:
        devices = tuple(backend.enumerate_devices())
    except backend.BackendUnavailable as error:
        return DeviceStatus(
            "backend_unavailable",
            f"CH341A native USB backend is unavailable: {error}",
        )
    except backend.DeviceNotFound:
        devices = ()
    except backend.MultipleDevices as error:
        return DeviceStatus("multiple", str(error))
    except backend.CH341AError as error:
        return DeviceStatus("error", f"CH341A detection failed: {error}")

    if not devices:
        return DeviceStatus("not_found", "No CH341A programmer was detected.")
    if len(devices) != 1:
        return DeviceStatus(
            "multiple",
            f"Detected {len(devices)} CH341A programmers; connect exactly one.",
            devices,
        )
    device = devices[0]
    if not _device_accessible(device):
        detail = _device_access_detail(device)
        suffix = f" ({detail})" if detail else ""
        return DeviceStatus(
            "driver_inaccessible",
            "CH341A detected, but the native USB backend cannot access it"
            f"{suffix}. Bind a supported WinUSB/libusb driver before reading.",
            devices,
        )
    return DeviceStatus(
        "ready",
        "One CH341A programmer is available. Read and save the full EEPROM "
        "before Seed ECU can be enabled.",
        devices,
    )


def _require_ready() -> DeviceStatus:
    status = detect_status()
    if not status.ready:
        raise DeviceNotReady(status)
    return status


def _require_new_path(path: str | Path, description: str) -> Path:
    target = Path(path)
    if target.exists():
        raise FileExistsError(f"refusing to overwrite {description} {target}")
    return target


def _after_path(backup_path: Path) -> Path:
    suffix = backup_path.suffix or ".bin"
    return backup_path.with_name(f"{backup_path.stem}.after{suffix}")


def _stable_image(programmer, backend) -> bytes:
    try:
        image = bytes(programmer.read_full())
    except backend.CH341AError as error:
        raise StableReadError(f"stable 512-byte EEPROM read failed: {error}") from error
    if len(image) != EEPROM_SIZE:
        raise StableReadError(
            f"stable EEPROM read returned {len(image)} bytes; expected {EEPROM_SIZE}"
        )
    try:
        return eeprom_ram.validate_physical_capture(image)
    except eeprom_ram.EepromError as error:
        raise StableReadError(str(error)) from error


def _checked_warnings(image: bytes, variant: str | None) -> tuple[str, ...]:
    if variant is None:
        layouts = eeprom_ram.detect_layouts(image)
        if not layouts:
            return (
                "WARNING: EEPROM layout is unresolved; lower checked records "
                "were not interpreted.",
            )
        variant = layouts[0]
    failed = tuple(
        row["offset"]
        for row in eeprom_ram.field_report(image, variant)
        if row["checked"] and not row["check_ok"]
    )
    if not failed:
        return ()
    offsets = ", ".join(f"0x{offset:03X}" for offset in failed)
    return (
        f"WARNING: {len(failed)} lower checked EEPROM record(s) are invalid "
        f"({offsets}). This does not block the narrow 0x1DD recovery marker write.",
    )


def _capture(
    image: bytes,
    path: Path | None,
    device: object,
    variant: str | None = "MS41.3",
) -> Capture:
    image = eeprom_ram.validate_image(image)
    return Capture(
        image,
        path,
        hashlib.sha256(image).hexdigest(),
        _checked_warnings(image, variant),
        device,
        variant,
    )


def _validate_capture(capture: Capture, operation: str) -> None:
    if not isinstance(capture, Capture):
        raise TypeError(f"{operation} requires the Capture returned by read_eeprom()")
    try:
        image = eeprom_ram.validate_physical_capture(capture.image)
    except eeprom_ram.EepromError as error:
        raise SeedRefused(str(error)) from error
    if hashlib.sha256(image).hexdigest() != capture.sha256:
        raise SeedRefused("the prior stable capture hash is inconsistent")
    if capture.path is None:
        raise SeedRefused(f"{operation} requires a saved prior capture")
    try:
        saved = capture.path.read_bytes()
    except OSError as error:
        raise SeedRefused(
            f"the prior stable capture is no longer available: {error}"
        ) from error
    if saved != image:
        raise SeedRefused(
            "the saved prior stable capture no longer matches its in-memory image"
        )


def _validate_prior_capture(capture: Capture) -> None:
    _validate_capture(capture, "Seed ECU")
    if capture.marker == RECOVERY_PROGRESSION:
        raise SeedRefused("EEPROM is already seeded with progression 01 02 03")
    if not capture.seedable:
        raise SeedRefused(
            f"EEPROM progression {capture.marker.hex(' ')} is malformed or unsupported; "
            "it will not be repaired automatically"
        )


def read_eeprom(
    output_path: str | Path | None = None,
    *,
    variant: str | None = "MS41.3",
) -> Capture:
    """Read a stable physical image, optionally publishing an immutable capture."""
    if variant is not None:
        eeprom_ram.fields_for_variant(variant)
    target = (
        _require_new_path(output_path, "EEPROM capture")
        if output_path is not None else None
    )
    status = _require_ready()
    backend = _load_backend()
    with backend.open_first() as programmer:
        image = _stable_image(programmer, backend)
    saved = eeprom_ram.save_capture(target, image) if target is not None else None
    return _capture(image, saved, status.devices[0], variant)


def seed_ecu(
    expected: Capture,
    backup_path: str | Path,
    *,
    confirm: Callable[[str], bool],
) -> SeedResult:
    """Write only 0x1DD..0x1DF after binding to a prior stable capture."""
    _validate_prior_capture(expected)
    before_path = _require_new_path(backup_path, "EEPROM before-image")
    after_path = _require_new_path(_after_path(before_path), "EEPROM after-image")
    status = _require_ready()
    backend = _load_backend()

    with backend.open_first() as programmer:
        try:
            before_image = _stable_image(programmer, backend)
        except StableReadError as error:
            raise SeedRefused(
                f"pre-write read was rejected: {error}; no write was sent"
            ) from error
        if before_image != expected.image:
            raise SeedRefused(
                "the current EEPROM no longer matches the prior stable capture; "
                "read it again before seeding"
            )
        before_saved = eeprom_ram.save_capture(before_path, before_image)
        before = _capture(
            before_image, before_saved, status.devices[0], expected.variant)

        message = (
            "ECU power must be OFF and isolated from the programmer.\n\n"
            f"Write only EEPROM 0x1DD..0x1DF:\n"
            f"{before.marker.hex(' ')} -> {RECOVERY_PROGRESSION.hex(' ')}\n\n"
            f"Immutable before-image: {before.path}\n"
            f"SHA-256: {before.sha256}"
        )
        if before.warnings:
            message += "\n\n" + "\n".join(before.warnings)
        try:
            accepted = bool(confirm(message))
        except (EOFError, KeyboardInterrupt):
            accepted = False
        if not accepted:
            raise SeedCancelled(before)

        comparison = bytes(programmer.read(RECOVERY_OFFSET, 3))
        if comparison != before.marker:
            raise SeedVerificationError(
                "compare-before-write failed at 0x1DD; no write was sent",
                before=before,
            )

        try:
            programmer.write(RECOVERY_OFFSET, RECOVERY_PROGRESSION)
            marker_readback = bytes(programmer.read(RECOVERY_OFFSET, 3))
            after_image = _stable_image(programmer, backend)
        except (backend.CH341AError, StableReadError) as error:
            raise SeedVerificationError(
                "the marker write/readback did not complete with a proven state; "
                "do not repeat Seed ECU until a new stable full-chip read is saved",
                before=before,
            ) from error
        try:
            after_saved = eeprom_ram.save_capture(after_path, after_image)
        except OSError as error:
            raise SeedVerificationError(
                "the marker write was read back, but the mandatory after-image "
                "could not be archived; save a new stable full-chip read before "
                "attempting any other operation",
                before=before,
            ) from error
        after = _capture(
            after_image, after_saved, status.devices[0], expected.variant)

    changed = tuple(
        offset
        for offset, (old, new) in enumerate(zip(before.image, after.image))
        if old != new
    )
    if marker_readback != RECOVERY_PROGRESSION:
        raise SeedVerificationError(
            "marker readback did not return 01 02 03", before=before, after=after
        )
    if after.marker != RECOVERY_PROGRESSION:
        raise SeedVerificationError(
            "full readback does not contain progression 01 02 03",
            before=before,
            after=after,
        )
    if changed != RECOVERY_OFFSETS:
        rendered = ", ".join(f"0x{offset:03X}" for offset in changed) or "none"
        raise SeedVerificationError(
            f"full readback changed unexpected offsets ({rendered}); expected only "
            "0x1DD, 0x1DE, and 0x1DF",
            before=before,
            after=after,
        )
    return SeedResult(before, after, changed)


def _write_eeprom(
    target: bytes,
    variant: str,
    backup_path: str | Path,
    *,
    confirm: Callable[[str], bool],
    expected: Capture | None = None,
) -> WriteResult:
    """Write one exact 512-byte image using compare-before-write byte updates."""
    target = eeprom_ram.validate_write_image(target, variant)
    before_path = _require_new_path(backup_path, "EEPROM before-image")
    status = _require_ready()
    backend = _load_backend()

    with backend.open_first() as programmer:
        before_image = _stable_image(programmer, backend)
        if expected is not None and before_image != expected.image:
            raise SeedRefused(
                "the current EEPROM no longer matches the prior stable capture; "
                "read it again before writing"
            )
        plan = eeprom_ram.build_write_plan(before_image, target, variant)
        after_path = (
            _require_new_path(_after_path(before_path), "EEPROM after-image")
            if plan else None
        )
        before_saved = eeprom_ram.save_capture(before_path, before_image)
        before = _capture(before_image, before_saved, status.devices[0], variant)
        changed = eeprom_ram.changed_offsets(before_image, target)
        if not plan:
            return WriteResult(before, before, ())
        message = (
            "ECU power must be OFF and isolated from the programmer.\n\n"
            f"Write {len(changed)} changed byte(s) for {variant}.\n"
            f"Immutable before-image: {before.path}\n"
            f"Before SHA-256: {before.sha256}\n"
            f"Target SHA-256: {hashlib.sha256(target).hexdigest()}"
        )
        if before.warnings:
            message += "\n\n" + "\n".join(before.warnings)
        try:
            accepted = bool(confirm(message))
        except (EOFError, KeyboardInterrupt):
            accepted = False
        if not accepted:
            raise WriteCancelled(before)

        try:
            for operation in plan:
                current = bytes(programmer.read(operation.offset, 1))
                if current != bytes((operation.expected,)):
                    raise CH341AEepromServiceError(
                        f"compare-before-write failed at 0x{operation.offset:03X}")
                programmer.write(operation.offset, bytes((operation.replacement,)))
                readback = bytes(programmer.read(operation.offset, 1))
                if readback != bytes((operation.replacement,)):
                    raise CH341AEepromServiceError(
                        f"byte readback failed at 0x{operation.offset:03X}")
            after_image = _stable_image(programmer, backend)
        except (Exception, KeyboardInterrupt) as error:
            after = None
            try:
                partial = _stable_image(programmer, backend)
                partial_saved = eeprom_ram.save_capture(after_path, partial)
                after = _capture(
                    partial, partial_saved, status.devices[0], variant)
            except (Exception, KeyboardInterrupt):
                pass
            raise WriteVerificationError(
                "EEPROM write did not complete with a proven exact state; "
                "do not retry until a new stable full-chip read is saved",
                before=before,
                after=after,
            ) from error

        try:
            after_saved = eeprom_ram.save_capture(after_path, after_image)
        except OSError as error:
            raise WriteVerificationError(
                "EEPROM was read back, but the mandatory after-image could not "
                "be archived; save a new stable full-chip read before continuing",
                before=before,
            ) from error
        after = _capture(after_image, after_saved, status.devices[0], variant)

    if after.image != target:
        offsets = eeprom_ram.changed_offsets(after.image, target)
        rendered = ", ".join(f"0x{offset:03X}" for offset in offsets[:12])
        raise WriteVerificationError(
            f"full readback differs from the target at {rendered}",
            before=before,
            after=after,
        )
    return WriteResult(before, after, changed)


def write_eeprom(
    target: bytes,
    variant: str,
    backup_path: str | Path,
    *,
    confirm: Callable[[str], bool],
) -> WriteResult:
    """Write a target image after capturing the connected EEPROM in-session."""
    return _write_eeprom(
        target,
        variant,
        backup_path,
        confirm=confirm,
    )


def restore_pre_seed(
    expected_seeded: Capture,
    original: Capture,
    backup_path: str | Path,
    *,
    confirm: Callable[[str], bool],
) -> WriteResult:
    """Restore the exact three-byte progression from a saved pre-seed image."""
    _validate_capture(expected_seeded, "Restore Pre-Seed State")
    _validate_capture(original, "Restore Pre-Seed State")
    if original.variant != expected_seeded.variant:
        raise SeedRefused("the pre-seed image uses a different MS41 layout")
    if expected_seeded.marker != RECOVERY_PROGRESSION:
        raise SeedRefused("the current saved capture is not seeded with 01 02 03")
    if original.marker not in SEEDABLE_PROGRESSIONS:
        raise SeedRefused(
            "the selected pre-seed image does not contain 00 01 02 or 03 04 05")
    if eeprom_ram.changed_offsets(expected_seeded.image, original.image) != RECOVERY_OFFSETS:
        raise SeedRefused(
            "the selected pre-seed image differs outside 0x1DD..0x1DF")
    return _write_eeprom(
        original.image,
        original.variant,
        backup_path,
        confirm=confirm,
        expected=expected_seeded,
    )


def recover_pre_seed_capture(
    expected_seeded: Capture,
    candidate_paths,
) -> Capture | None:
    """Recover one exact saved inverse after a programmer reconnect."""
    _validate_capture(expected_seeded, "Restore Pre-Seed State")
    if expected_seeded.marker != RECOVERY_PROGRESSION:
        return None
    matches = {}
    for value in candidate_paths:
        path = Path(value)
        try:
            image = path.read_bytes()
            candidate = _capture(
                image, path, expected_seeded.device, expected_seeded.variant,
            )
            _validate_capture(candidate, "Restore Pre-Seed State")
        except (OSError, TypeError, ValueError, SeedRefused, eeprom_ram.EepromError):
            continue
        if (
            candidate.marker in SEEDABLE_PROGRESSIONS
            and eeprom_ram.changed_offsets(expected_seeded.image, candidate.image)
            == RECOVERY_OFFSETS
        ):
            matches.setdefault(candidate.sha256, candidate)
    if len(matches) > 1:
        raise SeedRefused(
            "multiple distinct exact pre-seed images match this seeded EEPROM"
        )
    return next(iter(matches.values()), None)
