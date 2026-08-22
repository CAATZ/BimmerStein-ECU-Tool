"""Typed in-process service for the hardware 80C166 BSL recovery path."""
from dataclasses import dataclass, field, replace
from typing import Callable, Optional
import os

from engines.bsl import bsl_unbrick as engine
from operation_log import operation_log_sink
import ecu_info
from app_paths import mutable_path


_BACKUP_DIR = str(mutable_path("backups", "bsl"))
BSL_BAUD_RATES = (9600, 19200, 38400)


@dataclass
class BSLOperationRequest:
    """Complete internal contract consumed by the BSL implementation."""
    port: str
    chip: str
    half: str = "upper"
    baud: int = 9600
    reset_line: Optional[str] = "dtr"
    reset_ms: float = 20.0
    reset_settle: float = 15.0
    reset_invert: bool = False
    progress_cb: Optional[Callable] = None
    backup_cb: Optional[Callable] = None
    destructive_cb: Optional[Callable] = None
    session_registry: list = field(default_factory=list)
    serial_factory: object = None
    backup_dir: str = _BACKUP_DIR

    region: Optional[str] = None
    ref: Optional[str] = None
    ref_bytes: Optional[bytes] = None
    fix_checksums: bool = False
    force: bool = False
    arm: bool = False
    no_backup: bool = False

    file: Optional[str] = None
    partial: bool = False
    range: Optional[str] = None
    no_alias: bool = False
    raw_hole: bool = False
    cpu_order: bool = False
    file_order: bool = True


@dataclass(frozen=True)
class BSLFlashPlan:
    """Safety-relevant flash inputs frozen by Preview and reused unchanged by ARM."""
    port: str
    region: str
    ref_path: str
    ref_bytes: bytes
    chip: str
    half: str
    baud: int
    reset_line: str
    fix_checksums: bool
    force: bool


def flash_regions(chip, half="upper"):
    """Region names for the selected physical flash geometry."""
    if chip == "29f400" and half == "upper":
        regions = engine.FLASH_REGIONS_AMD
    elif chip == "29f200" or (chip == "29f400" and half == "lower"):
        regions = engine.FLASH_REGIONS_AMD_LOWER
    else:
        regions = engine.FLASH_REGIONS
    return list(regions) + ["all"]


def create_flash_plan(port, region, ref_path, chip, half, fix_checksums=False, force=False,
                      *, baud=9600, reset_line="dtr"):
    if chip not in ("auto", "28f200", "29f200", "29f400"):
        raise ValueError(f"Unsupported flash-chip geometry {chip!r}.")
    if chip == "auto":
        raise ValueError("Select the detected flash chip explicitly; automatic geometry is unsafe for writes.")
    baud = int(baud)
    if baud not in BSL_BAUD_RATES:
        raise ValueError(f"Unsupported hardware-BSL baud rate {baud}; choose 9600, 19200, or 38400.")
    with open(ref_path, "rb") as handle:
        ref_bytes = handle.read()
    if len(ref_bytes) not in (engine.CAL_PARTIAL_SIZE, engine.MS41ECU.FULL_ROM_SIZE):
        raise ValueError(
            f"Reference is {len(ref_bytes):,} bytes; expected a 24,576-byte tune or "
            f"{engine.MS41ECU.FULL_ROM_SIZE:,}-byte full image.")
    if len(ref_bytes) == engine.MS41ECU.FULL_ROM_SIZE:
        hybrid = engine.MS41ECU.check_hybrid(ref_bytes)
        if hybrid and not force:
            raise ValueError(
                f"Flash blocked: the reference image is internally incompatible ({hybrid}).")
        image_family = ecu_info.image_chip_family(ref_bytes)
        selected_family = {
            "28f200": "intel", "29f200": "amd", "29f400": "amd",
        }[chip]
        if image_family in ("amd", "intel") and image_family != selected_family:
            labels = {"amd": "AMD/JEDEC 29F", "intel": "Intel 28F200"}
            raise ValueError(
                f"Flash blocked: the reference image carries the {labels[image_family]} driver "
                f"at file 0x423C, but the selected physical geometry is {labels[selected_family]}. "
                "Cross-family images may be built and saved, but they cannot be flashed to "
                "different flash geometry.")
    return BSLFlashPlan(
        port=str(port), region=str(region), ref_path=os.path.abspath(ref_path),
        ref_bytes=bytes(ref_bytes), chip=str(chip), half=str(half),
        baud=baud, reset_line=str(reset_line),
        fix_checksums=bool(fix_checksums), force=bool(force))


def _base_request(port, chip, half, progress=None, *, baud=9600, reset_line="dtr",
                  serial_factory=None):
    def progress_cb(done, total, label):
        if progress:
            progress(done, total, label)

    return BSLOperationRequest(
        port=port, chip=chip, half=half, baud=int(baud), reset_line=reset_line,
        progress_cb=progress_cb if progress else None, serial_factory=serial_factory)


_NOISY_LIVE_PREFIXES = (
    "ref @", "ref checksum", "phys 0x0 read", "alias 0x", "dumped 0x",
)


def _event_forwarder(log, concise=False):
    def forward(message, level="info"):
        message = str(message).strip()
        if not message:
            return
        plain = message.lower().lstrip("- ")
        if concise and plain.startswith(_NOISY_LIVE_PREFIXES):
            return
        if "refusing" in plain or "failed" in plain or "mismatch" in plain:
            level = "error"
        elif "warning" in plain or "brick" in plain or "force" in plain:
            level = "warn"
        elif "complete" in plain or "verified" in plain or "result: all" in plain:
            level = "ok"
        try:
            log(message, level)
        except TypeError:
            log(message)
    return forward


def _run_handler(handler, request, log, *, concise=False):
    try:
        with operation_log_sink(_event_forwarder(log, concise=concise)):
            rc = handler(request)
        return int(rc or 0)
    except (engine.BSLError,) + engine._SERIAL_ERRS as error:
        _event_forwarder(log)(f"BSL communication failed: {error}", "error")
        return 1
    except Exception as error:
        _event_forwarder(log)(f"BSL operation failed: {error}", "error")
        return 1
    finally:
        for session in reversed(request.session_registry):
            try:
                session.close()
            except Exception:
                pass


def _flash_request(plan, arm, progress=None, *, serial_factory=None,
                   destructive_cb=None, backup_dir=None, backup_cb=None):
    request = _base_request(
        plan.port, plan.chip, plan.half, progress,
        baud=plan.baud, reset_line=plan.reset_line,
        serial_factory=serial_factory)
    return replace(
        request, region=plan.region, ref=plan.ref_path, ref_bytes=plan.ref_bytes,
        fix_checksums=plan.fix_checksums, force=plan.force, arm=bool(arm),
        destructive_cb=destructive_cb, backup_cb=backup_cb,
        backup_dir=request.backup_dir if backup_dir is None else os.fspath(backup_dir))


def _log_transport(log, port, baud, reset_line):
    _event_forwarder(log)(
        f"Hardware BSL transport: {port}, {int(baud):,} baud, direct ASC0/8N1, "
        f"no echo, {str(reset_line).upper()} reset.")


def flash_dry_run(plan, log):
    _log_transport(log, plan.port, plan.baud, plan.reset_line)
    return _run_handler(engine.cmd_flash, _flash_request(plan, arm=False), log)


def flash_arm(plan, log, progress=None, *, serial_factory=None,
              destructive_cb=None, backup_dir=None, backup_cb=None):
    _log_transport(log, plan.port, plan.baud, plan.reset_line)
    return _run_handler(
        engine.cmd_flash, _flash_request(
            plan, arm=True, progress=progress, serial_factory=serial_factory,
            destructive_cb=destructive_cb, backup_dir=backup_dir,
            backup_cb=backup_cb),
        log, concise=True)


def _diag(handler, port, chip, half, log, progress=None, *, baud=9600,
          reset_line="dtr", serial_factory=None, **fields):
    request = replace(
        _base_request(
            port, chip, half, progress, baud=baud, reset_line=reset_line,
            serial_factory=serial_factory),
        **fields)
    return _run_handler(handler, request, log)


def sync(port, chip, half, log, *, baud=9600, reset_line="dtr", progress=None,
         serial_factory=None):
    return _diag(
        engine.cmd_sync, port, chip, half, log, progress,
        baud=baud, reset_line=reset_line, serial_factory=serial_factory)


def chip_id(port, chip, half, log, *, baud=9600, reset_line="dtr", progress=None,
            serial_factory=None):
    return _diag(
        engine.cmd_id, port, chip, half, log, progress,
        baud=baud, reset_line=reset_line, serial_factory=serial_factory)


def businfo(port, chip, half, log, *, baud=9600, reset_line="dtr", progress=None,
            serial_factory=None):
    return _diag(
        engine.cmd_businfo, port, chip, half, log, progress,
        baud=baud, reset_line=reset_line, serial_factory=serial_factory)


def verify_alias(port, chip, half, log, *, baud=9600, reset_line="dtr", progress=None,
                 serial_factory=None):
    return _diag(
        engine.cmd_verify_alias, port, chip, half, log, progress,
        baud=baud, reset_line=reset_line, serial_factory=serial_factory)


def vpp_on(port, chip, half, log, *, baud=9600, reset_line="dtr", progress=None,
           serial_factory=None):
    if chip != "28f200":
        _event_forwarder(log)(
            "VPP control applies only to the Intel 28F200; AMD/JEDEC flash is single-supply.",
            "warn")
        return 2
    return _diag(
        engine.cmd_vpp_on, port, chip, half, log, progress,
        baud=baud, reset_line=reset_line, serial_factory=serial_factory)


def _dump(port, outfile, chip, half, log, progress=None, *, partial, baud=9600,
          reset_line="dtr", serial_factory=None):
    return _diag(
        engine.cmd_dump, port, chip, half, log, progress,
        baud=baud, reset_line=reset_line, serial_factory=serial_factory,
        file=outfile, partial=bool(partial), range=None, no_alias=False,
        raw_hole=False, cpu_order=False, file_order=True)


def dump_full(port, outfile, chip, half, log, progress=None, *, baud=9600,
              reset_line="dtr", serial_factory=None):
    """Read one visible 256 KB flash bank into a standard file-order image."""
    return _dump(
        port, outfile, chip, half, log, progress,
        partial=False, baud=baud, reset_line=reset_line,
        serial_factory=serial_factory)


def dump_tune(port, outfile, chip, half, log, progress=None, *, baud=9600,
              reset_line="dtr", serial_factory=None):
    """Read the standard 24 KB CPU/DS2-order calibration partial."""
    return _dump(
        port, outfile, chip, half, log, progress,
        partial=True, baud=baud, reset_line=reset_line,
        serial_factory=serial_factory)


# Compatibility for older internal callers: an unqualified dump is a full read.
dump = dump_full
