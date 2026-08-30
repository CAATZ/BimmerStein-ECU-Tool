"""Typed application service for the internal Soft-BSL host.

Pure previews (no serial I/O) + live flash / cross-bank ops. The live ops open a
shared application DS2Interface on the given port, enter the RAM agent, and
drive the flash; physical key-cycle /
A17-flip steps are surfaced through the `prompt` callback.
"""
from dataclasses import dataclass

from engines.softbsl import softbsl_host as _sb
import ds2 as _sbds2
import ecu_info
from ms41 import MS41ECU
from engines.softbsl.softbsl_host import SoftBSLError  # stable ref (tests monkeypatch _sb)
from operation_log import send_to_sink


class CrossBankSafetyError(RuntimeError):
    """A physical-bank safety gate failed; unlike link noise, this must not baud-retry."""


D2XXRequiredError = _sb.D2XXRequiredError


class SoftBSLRecoveryStateError(RuntimeError):
    """A pre-erase attempt could not prove a safe return to normal DS2."""

    power_cycle_required = True


@dataclass
class SoftBSLBootSession:
    """CalGuard key-on recovery session kept open for a later read or write."""

    port: str
    ds2: object
    agent: object
    baud: str
    chip_family: str
    driver_signature: bytes

    @property
    def is_open(self):
        return bool(getattr(self.ds2, "is_open", False))


@dataclass
class SoftBSLWriteRecovery:
    """Live post-erase RAM-agent session retained for an in-place re-flash."""

    port: str
    ds2: object
    agent: object
    operation: str
    target: bytes
    scope: str
    baud: str
    prompt: object
    do_verify: bool
    write_bootloader: bool
    chip_family: object
    error: Exception

    @property
    def is_open(self):
        return bool(getattr(self.ds2, "is_open", False))

    def close_after_confirmed_power_cycle(self):
        """Release the host handle only after recovery or an operator-confirmed cycle."""
        self.ds2.close()


class SoftBSLWriteRecoveryRequired(SoftBSLError):
    """Erase began and the running RAM agent is deliberately being retained."""

    def __init__(self, recovery):
        self.recovery = recovery
        super().__init__(
            f"{recovery.error}. FLASH INCOMPLETE - DO NOT TURN IGNITION OFF; "
            "the Soft-BSL RAM-agent recovery session is still open"
        )


class SoftBSLFallbackExhausted(SoftBSLError):
    """Every requested Soft-BSL baud tier failed before an erase boundary."""

    def __init__(self, label, tiers, error):
        super().__init__(
            f"{label} exhausted the Soft-BSL baud fallback "
            f"({', '.join(tiers)}): {error}. For a tune or boot-preserving full-ROM "
            "recovery, select 'Force DS2 (slow)' and retry. "
            "Boot/identity/TOP operations still require Soft-BSL."
        )


class FlashFamilyMismatchError(RuntimeError):
    """An image's installed flash-driver family is unsafe for the live ECU."""


class FlashImageCompatibilityError(RuntimeError):
    """A full image's program and calibration compatibility data disagree."""


def validate_flash_image_family(image, connected_family, *, write_bootloader=False):
    """Require a known live family; compare image geometry only for boot writes."""
    image_family = ecu_info.image_chip_family(bytes(image))
    connected_family = connected_family if connected_family in ("amd", "intel") else None
    labels = {"amd": "AMD/JEDEC 29F", "intel": "Intel 28F"}

    if connected_family is None:
        raise FlashFamilyMismatchError(
            "Flash blocked before agent entry: the connected ECU flash family could not "
            "be identified. A recognized live Intel/AMD family is required to select the "
            "resident driver or RAM agent safely.")

    if write_bootloader:
        if image_family is None or connected_family is None:
            raise FlashFamilyMismatchError(
                "Flash blocked before agent entry: an armed boot-region write requires "
                "both the image driver and connected ECU flash family to be identified.")
        if image_family != connected_family:
            raise FlashFamilyMismatchError(
                "Flash blocked before agent entry: the armed boot-region write would replace "
                f"the connected ECU's {labels[connected_family]} driver with the image's "
                f"{labels[image_family]} driver.")
    return image_family

# The standard 24 KB calibration/tune partition, DS2/CPU-order @0x10000 (== ds2.read_partial /
# ms41.TUNE_DS2_BASE). A RAW contiguous read, NOT the 64 KB file-order erase sector.
_TUNE_CPU_BASE = 0x10000
_TUNE_SIZE     = 24 * 1024

# VIN/identity display window in standard full-ROM FILE order. With the
# C166/flash XOR descramble this maps to live CPU/DS2 0x0000-0x3FFF.
_BOOT_ID_FILE_BASE = 0x4000
_BOOT_ID_SIZE      = 16 * 1024
_BOTTOM_ID_SECTOR_BASE = 0x4000
_BOTTOM_ID_SECTOR_SIZE = 8 * 1024
_TOP_ID_SECTOR_BASE    = 0x0000
_TOP_ID_SECTOR_SIZE    = 64 * 1024

_BAUD_ORDER = ("high", "mid", "low")   # fast -> safe; low (9600) is pyserial-safe and needs no D2XX
_WRITE_LINK_PROBE_BASE = 0x20000
_WRITE_LINK_PROBE_SIZE = 128

_AGENT_DETAIL_PREFIXES = (
    "ds2: prepare", "staged ",
    "streaming agent", "agent running", "entered on attempt", "baud ->",
    "erase ", "erase done", "programmed up to", "verify (read-back)",
    "verify ok", "marker-0 finalize",
)


def _agent_log(log):
    """Downgrade routine RAM-agent mechanics without discarding diagnostics."""
    def forward(message, level="info"):
        message = str(message)
        plain = message.strip().lower()
        if level == "info" and plain.startswith(_AGENT_DETAIL_PREFIXES):
            level = "debug"
        send_to_sink(log, message, level)
    return forward


class _WriteProgressTracker:
    """Forward progress while remembering the first destructive erase boundary."""

    def __init__(self, downstream=None):
        self.downstream = downstream
        self.destructive_started = False

    def __call__(self, done, total, label=""):
        # Let a UI callback fail before we mark the operation destructive: the host invokes this
        # callback immediately before the erase opcode, so an exception here means no erase was sent.
        if self.downstream is not None:
            self.downstream(done, total, label)
        if label == "erase":
            self.destructive_started = True


def _prove_write_link(sb, tier, log):
    """Brief CRC-framed bidirectional stability gate performed before any erase."""
    for offset in (0, _WRITE_LINK_PROBE_SIZE, 2 * _WRITE_LINK_PROBE_SIZE):
        sb.crc_read(_WRITE_LINK_PROBE_BASE + offset, _WRITE_LINK_PROBE_SIZE)
    log(
        f"Soft-BSL '{tier}' baud preflight passed "
        f"(3 x {_WRITE_LINK_PROBE_SIZE}-byte CRC reads); erase is now permitted."
    )


def _baud_tiers_from(start):
    """The fall-back ladder: `start` and every tier below it (high->mid->low). A tier outside the
    ordered set (shouldn't happen) is used alone, i.e. no fall-back."""
    if start in _BAUD_ORDER:
        return list(_BAUD_ORDER[_BAUD_ORDER.index(start):])
    return [start]


def _with_baud_fallback(attempt, start_baud, log, label):
    """Retry lower tiers only when the failed attempt is still pre-erase.

    Write attempts raise :class:`SoftBSLWriteRecoveryRequired` after their first erase boundary;
    that exception is never consumed here. Reads remain idempotent and can still retry whole.
    """
    tiers = _baud_tiers_from(start_baud)
    for i, tier in enumerate(tiers):
        try:
            if i > 0:
                log(f"{label}: retrying the whole operation at '{tier}' baud "
                    f"(the '{tiers[i - 1]}' attempt failed -- link too noisy for that rate).")
            return attempt(tier)
        except SoftBSLWriteRecoveryRequired:
            raise
        except D2XXRequiredError as e:
            if tier != "low" and "low" in tiers[i + 1:]:
                log(f"{label}: D2XX could not open the selected adapter ({e}). "
                    "Skipping unsupported pyserial fast tiers and retrying at 9600 baud.")
                try:
                    return attempt("low")
                except SoftBSLWriteRecoveryRequired:
                    raise
                except SoftBSLError as low_error:
                    raise SoftBSLFallbackExhausted(label, tiers, low_error) from low_error
            raise
        except SoftBSLError as e:
            if tier == tiers[-1]:
                if len(tiers) > 1:
                    raise SoftBSLFallbackExhausted(label, tiers, e) from e
                raise
            log(f"{label}: '{tier}' baud failed before erase ({e}).")


def _recover_marker0(sb, log, finalize_sent=False):
    """Finalize E740=0 with the current RAM agent, then confirm stock DS2 is alive.

    The magic-gated ``R 9C 9C`` command now stages E740=0, calls the stock EEPROM commit
    routine, arms a minimum-period watchdog fallback, and executes protected SRST. No
    post-reset 0x5A upload is involved, so a complete
    stock restore may safely remove both door_magic and the persistent SA1 loader.

    ``finalize_sent`` is true after a verified ``flash_image`` because it already issued R.
    Reads, successful partial writes, and pre-erase cleanup issue it here at the agent's current baud. If
    marker 0 is not observable after reboot, stock program VERIFY is the hook-independent
    last resort.
    """
    try:
        return sb.finalize_marker0(already_sent=finalize_sent)
    except Exception:
        # Compatibility with lightweight test doubles; production SoftBSL always owns the method.
        if not finalize_sent:
            try:
                sb.reset()
            except Exception:
                return False
        return True


def _recover_staged_entry_marker0(ds2, log):
    """Use the still-open stock DS2 owner to restore and prove E740=0."""
    def emit(message, level="info"):
        send_to_sink(log, message, level)

    try:
        marker = bytes(ds2.read_mem(0xE740, 1))
        if marker == b"\x00":
            emit("Staged entry stopped with E740 already confirmed at 0x00.")
            return True
        emit(
            "Staged entry stopped before the RAM-agent banner with "
            f"E740={marker.hex() or '<empty>'}; running stock program VERIFY.",
            "warn",
        )
    except Exception as error:
        emit(
            "E740 could not be read after staged entry stopped; attempting the "
            f"stock program VERIFY on the same DS2 handle ({error}).",
            "warn",
        )

    try:
        ok, status = ds2.verify_program_region(log_fn=emit)
        marker = bytes(ds2.read_mem(0xE740, 1))
    except Exception as error:
        emit(
            f"Stock marker-0 recovery after staged entry failed: {error}",
            "error",
        )
        return False
    if ok is True and marker == b"\x00":
        emit("Stock program VERIFY restored and confirmed E740=0x00.", "ok")
        return True
    status_text = f"0x{status:02X}" if status is not None else "unknown"
    emit(
        "Stock marker-0 recovery was not proven after staged entry: "
        f"verify={status_text}, E740={marker.hex() or '<empty>'}.",
        "error",
    )
    return False


def marker(image):
    """'T'/'B' bank-ID marker (file 0x5FFC), or None."""
    return _sb.image_marker(bytes(image))


def full_write_requires_softbsl(live_marker, image):
    """TOP's fused SA7 geometry cannot be rewritten by the resident DS2 driver."""
    return live_marker == "T" or marker(image) == "T"


def calguard_recovery_ready(ds2, log):
    """Read-only probe for a CalGuard-held direct 0x5A recovery path."""
    return _sb.SoftBSL(ds2, log=_agent_log(log)).calguard_direct_entry_ready()


def _capture(fn, *args, **kw):
    lines = []
    fn(*args, log=lines.append, **kw)
    return "\n".join(lines)


def crossbank_plan(image):
    """Text preview of the cross-bank top-half write plan — no serial I/O."""
    return _capture(_sb.crossbank_dry_run, bytes(image))


def _open_session(port, log, chip_family=None, require_d2xx=False, baud_tier=None,
                  entry_mode="auto", boot_timeout=8.0, agent_payload=None,
                  serial_factory=None):
    """Open the port and enter the RAM agent through the persistent 0x5A loader.

    Automatic normal entry uses 0x2A door_magic first. Built-in flash agents may
    auto-detect an exact CalGuard mismatch and skip 0x2A; custom agents use the
    normal door unless recovery is explicitly requested with ``direct`` or ``boot``.
    ``entry_mode='boot'`` first catches CalGuard V4's key-on token poll. The disposable
    0x43 install door is never used here.

    `chip_family` ('amd'/'intel'/None) selects the flash agent: an Intel 28F200 needs
    agent_28f.hex (Intel command set + 12 V VPP); AMD/None default to agent.hex. Boot
    recovery instead reads the preserved boot driver's signature after acknowledgement
    and selects the agent from that evidence. ``agent_payload`` lets another narrowly
    scoped RAM service reuse this entry owner without inheriting either flash agent.

    On ANY entry failure: ensure_flash_mode() may have already fired the 0x2A door, committing
    E740=1 (flash-listen, non-drivable, persistent across key-cycles). Walk it back to marker 0 +
    drivable via the running agent's self-finalize before closing +
    re-raising — otherwise a missed 5a window strands a previously-drivable ECU in flash mode."""
    if entry_mode not in ("auto", "direct", "boot"):
        raise ValueError(f"unknown Soft-BSL entry mode {entry_mode!r}")
    custom_agent = bytes(agent_payload) if agent_payload is not None else None
    if custom_agent is not None and not (0 < len(custom_agent) <= 0x800):
        raise SoftBSLError(
            f"agent {len(custom_agent)} B out of range (expected 1..2048)")
    if (
        custom_agent is None
        and entry_mode == "direct"
        and chip_family not in ("amd", "intel")
    ):
        raise SoftBSLError(
            "Soft-BSL recovery requires a known Intel/AMD flash-driver family; "
            "no port was opened and nothing was erased.")

    ds2_kwargs = {"baud": 9600, "verbose": False, "echo": True}
    if serial_factory is not None:
        ds2_kwargs["serial_factory"] = serial_factory
    d = _sbds2.DS2Interface(port, **ds2_kwargs)
    d.open()
    if require_d2xx and not bool(
        getattr(d, "native_fast_capable", False)
    ):
        d.close()
        raise D2XXRequiredError(
            f"{port} opened through "
            f"{d.transport_name or 'an unsupported transport'}")
    # Detailed agent mechanics remain in the session file, while the GUI keeps
    # phase outcomes, warnings, errors, and recovery instructions visible.
    sb = _sb.SoftBSL(d, log=_agent_log(log))
    staged = baud_tier is not None and hasattr(sb, "enter_staged")
    try:
        direct = entry_mode in ("direct", "boot") or (
            custom_agent is None
            and bool(getattr(
                sb, "calguard_direct_entry_ready", lambda: False)())
        )
        resolved_chip_family = chip_family
        if entry_mode == "boot":
            sb.prearm_calguard_boot(timeout=boot_timeout)
            if custom_agent is not None:
                resolved_chip_family = None
                log(
                    "CalGuard recovery acknowledged for custom RAM service; "
                    "flash-family detection is not required."
                )
            else:
                signature = bytes(
                    d.read_mem(ecu_info.DRV_SIG_ADDR, ecu_info.DRV_SIG_LEN)
                )
                resolved_chip_family = ecu_info.chip_family(signature)
                if resolved_chip_family not in ("amd", "intel"):
                    raise SoftBSLError(
                        "CalGuard recovery acknowledged, but the preserved boot "
                        "flash-driver signature was not recognized "
                        f"({signature.hex() or 'no data'}). No RAM agent was "
                        "loaded and nothing was erased."
                    )
                sb.boot_chip_family = resolved_chip_family
                sb.boot_driver_signature = signature
                log(
                    "CalGuard recovery detected "
                    + (
                        "Intel 28F200"
                        if resolved_chip_family == "intel" else
                        "AMD 29F200/29F400"
                    )
                    + " from the preserved boot flash driver."
                )
        if (
            custom_agent is None
            and direct
            and resolved_chip_family not in ("amd", "intel")
        ):
            raise SoftBSLError(
                "Direct Soft-BSL recovery requires a known Intel/AMD flash-driver family; "
                "no agent was loaded and nothing was erased.")
        agent = (
            custom_agent
            if custom_agent is not None
            else _sb.load_agent(_sb.agent_path_for_family(resolved_chip_family))
        )
        if direct:
            if entry_mode == "direct":
                log("Forced direct Soft-BSL recovery: sending staged 0x5A without 0x2A.")
        else:
            sb.ensure_flash_mode(poll_ready=True)
        if staged:
            sb.enter_staged(agent, baud_tier, trigger="5a")
        else:
            sb.enter_retry(agent, trigger="5a")
    except (Exception, KeyboardInterrupt) as error:
        if staged:
            # No production-agent banner was reached, so its reset command is unavailable.
            # Keep this exact DS2 handle open long enough to restore/prove stock marker 0.
            recovered = False
            try:
                recovered = _recover_staged_entry_marker0(d, log)
            except Exception:
                pass
            try:
                d.close()
            except Exception:
                pass
            if not recovered:
                raise SoftBSLRecoveryStateError(
                    "staged Soft-BSL entry stopped before the RAM-agent banner and "
                    "E740=0 / normal stock DS2 could not be proven on the same handle; "
                    "automatic baud fallback is blocked and an ignition cycle is required"
                ) from error
            raise
        recovered = False
        try:
            recovered = bool(_recover_marker0(sb, log))
        except Exception:
            pass
        try:
            d.close()
        except Exception:
            pass
        if not recovered:
            raise SoftBSLRecoveryStateError(
                "Soft-BSL entry failed and a safe E740=0 return could not be confirmed; "
                "automatic baud fallback is blocked"
            ) from error
        raise
    if staged:
        sb.staged_entry = True
    return d, sb


def open_boot_recovery(port, log, *, baud="high", timeout=15.0,
                       serial_factory=None):
    """Catch CalGuard V4 at key-on and retain the running RAM agent."""
    d, sb = _open_session(
        port,
        log,
        None,
        require_d2xx=True,
        baud_tier=baud,
        entry_mode="boot",
        boot_timeout=timeout,
        serial_factory=serial_factory,
    )
    return SoftBSLBootSession(
        port=str(port),
        ds2=d,
        agent=sb,
        baud=baud,
        chip_family=sb.boot_chip_family,
        driver_signature=sb.boot_driver_signature,
    )


def close_boot_recovery(session, log):
    """Return a retained boot-recovery session to marker 0, then close COM."""
    if not isinstance(session, SoftBSLBootSession):
        raise TypeError("session must be a SoftBSLBootSession")
    if not session.is_open:
        return True
    session.agent.log = _agent_log(log)
    if not _recover_marker0(session.agent, log):
        return False
    session.ds2.close()
    return True


def read_boot_recovery(session, scope, progress_cb, log):
    """Read through an already-running CalGuard/Soft-BSL recovery agent."""
    if not isinstance(session, SoftBSLBootSession):
        raise TypeError("session must be a SoftBSLBootSession")
    if not session.is_open:
        raise SoftBSLRecoveryStateError("the CalGuard recovery session is closed")
    session.agent.log = _agent_log(log)
    if scope == "tune":
        return session.agent.read_range(
            _TUNE_CPU_BASE,
            _TUNE_SIZE,
            progress_cb=progress_cb,
            descramble=False,
            log_fn=log,
        )
    if scope == "full":
        return session.agent.read_range(
            0,
            _sb.IMAGE_SIZE,
            progress_cb=progress_cb,
            descramble=True,
            log_fn=log,
        )
    raise ValueError(f"unknown retained read scope {scope!r}")


def read_boot_recovery_range(session, address, length):
    """CRC-read a small raw CPU-address range without closing the session."""
    if not isinstance(session, SoftBSLBootSession):
        raise TypeError("session must be a SoftBSLBootSession")
    if not session.is_open:
        raise SoftBSLRecoveryStateError("the CalGuard recovery session is closed")
    return session.agent.read_range(address, length, descramble=False)


def _retained_write_failed(session, operation, target, scope, prompt, do_verify,
                           write_bootloader, tracker, error, log):
    if not tracker.destructive_started:
        raise error
    recovery = SoftBSLWriteRecovery(
        port=session.port,
        ds2=session.ds2,
        agent=session.agent,
        operation=operation,
        target=target,
        scope=scope,
        baud=session.baud,
        prompt=prompt,
        do_verify=bool(do_verify),
        write_bootloader=bool(write_bootloader),
        chip_family=session.chip_family,
        error=error,
    )
    log(
        "FLASH INCOMPLETE: erase began, so the retained CalGuard/Soft-BSL "
        "session remains open. DO NOT TURN IGNITION OFF."
    )
    raise SoftBSLWriteRecoveryRequired(recovery) from error


def write_tune_boot_recovery(session, partial, log, progress_cb=None, do_verify=True):
    """Write a tune through the retained recovery agent, with no route fallback."""
    if not isinstance(session, SoftBSLBootSession):
        raise TypeError("session must be a SoftBSLBootSession")
    if not session.is_open:
        raise SoftBSLRecoveryStateError("the CalGuard recovery session is closed")
    target = bytes(partial)
    tracker = _WriteProgressTracker(progress_cb)
    session.agent.log = _agent_log(log)
    try:
        _prove_write_link(session.agent, session.baud, log)
        session.agent.write_tune_partial(
            target,
            do_verify=do_verify,
            progress_cb=tracker,
        )
    except Exception as error:
        _retained_write_failed(
            session, "tune", target, "tune", None, do_verify, False,
            tracker, error, log)
    if not do_verify:
        log("Read-back verification skipped (Verify off). ECU-side finalization will continue.")
    try:
        if not _recover_marker0(session.agent, log):
            raise SoftBSLRecoveryStateError(
                "tune write completed, but E740=0 / stock DS2 finalization was not confirmed")
    finally:
        session.ds2.close()
    return True


def run_flash_boot_recovery(
        session, image, scope, prompt, log, progress_cb=None,
        do_verify=True, write_bootloader=False):
    """Write a full image through the retained recovery agent, with no fallback."""
    if not isinstance(session, SoftBSLBootSession):
        raise TypeError("session must be a SoftBSLBootSession")
    if not session.is_open:
        raise SoftBSLRecoveryStateError("the CalGuard recovery session is closed")
    target = bytes(image)
    if scope != "sa1":
        hybrid_error = MS41ECU.check_hybrid(target)
        if hybrid_error:
            raise FlashImageCompatibilityError(
                f"Flash blocked before erase: {hybrid_error}")
    validate_flash_image_family(
        target, session.chip_family, write_bootloader=write_bootloader)
    tracker = _WriteProgressTracker(progress_cb)
    session.agent.log = _agent_log(log)
    finalized_by_flash = False
    try:
        _prove_write_link(session.agent, session.baud, log)
        session.agent.flash_image(
            target,
            scope=scope,
            baud=session.baud,
            prompt=prompt,
            do_verify=do_verify,
            write_bootloader=write_bootloader,
            progress_cb=tracker,
            chip="28f200" if session.chip_family == "intel" else "29f400",
            baud_is_set=session.baud != "low",
        )
        finalized_by_flash = bool(do_verify)
    except Exception as error:
        _retained_write_failed(
            session, "image", target, scope, prompt, do_verify,
            write_bootloader, tracker, error, log)
    if not do_verify:
        log("Read-back verification skipped (Verify off). ECU-side finalization will continue.")
    try:
        if not _recover_marker0(
                session.agent, log, finalize_sent=finalized_by_flash):
            raise SoftBSLRecoveryStateError(
                "full write completed, but E740=0 / stock DS2 finalization was not confirmed")
    finally:
        session.ds2.close()
    return True


def _set_agent_baud_if_needed(sb, tier):
    """Keep compatibility with legacy fakes/manual sessions while staged entry owns the baud."""
    if tier != "low" and not getattr(sb, "staged_entry", False):
        sb.set_baud(tier)


def run_flash(port, image, scope, prompt, log, baud="low", progress_cb=None,
             do_verify=True, write_bootloader=False, chip_family=None,
             entry_mode="auto", serial_factory=None):
    """Live full-image-scope write with a brief pre-erase link gate.

    High/mid failures may fall back only while flash is untouched. Once the host emits the
    ``erase`` boundary, an exception retains the open RAM agent and raises
    :class:`SoftBSLWriteRecoveryRequired`. Successful writes finalize E740=0; ``do_verify``
    controls only the requested read-back verification.
    """
    if scope != "sa1":
        hybrid_error = MS41ECU.check_hybrid(bytes(image))
        if hybrid_error:
            raise FlashImageCompatibilityError(
                f"Flash blocked before agent entry: {hybrid_error}")
    validate_flash_image_family(
        image, chip_family, write_bootloader=write_bootloader)
    target = bytes(image)

    def _attempt(tier):
        d, sb = _open_session(
            port, log, chip_family, require_d2xx=tier != "low", baud_tier=tier,
            entry_mode=entry_mode, serial_factory=serial_factory)
        tracker = _WriteProgressTracker(progress_cb)
        finalized_by_flash = False
        write_complete = False
        retain_for_recovery = False
        try:
            _set_agent_baud_if_needed(sb, tier)
            _prove_write_link(sb, tier, log)
            sb.flash_image(target, scope=scope, baud=tier, prompt=prompt,
                           do_verify=do_verify, write_bootloader=write_bootloader,
                           progress_cb=tracker,
                           chip="28f200" if chip_family == "intel" else "29f400",
                           baud_is_set=tier != "low")
            finalized_by_flash = bool(do_verify)
            write_complete = True
            if not do_verify:
                log("Read-back verification skipped (Verify off). ECU-side finalization will continue.")
            if not _recover_marker0(sb, log, finalize_sent=finalized_by_flash):
                raise SoftBSLRecoveryStateError(
                    "write completed, but E740=0 / stock DS2 finalization was not confirmed"
                )
        except Exception as error:
            if not write_complete and tracker.destructive_started:
                retain_for_recovery = True
                recovery = SoftBSLWriteRecovery(
                    port=str(port), ds2=d, agent=sb, operation="image", target=target,
                    scope=scope, baud=tier, prompt=prompt, do_verify=bool(do_verify),
                    write_bootloader=bool(write_bootloader), chip_family=chip_family,
                    error=error,
                )
                log(
                    "FLASH INCOMPLETE: erase began, so Soft-BSL is preserving the live RAM "
                    "agent. DO NOT TURN IGNITION OFF."
                )
                raise SoftBSLWriteRecoveryRequired(recovery) from error
            if not write_complete:
                if not _recover_marker0(sb, log):
                    raise SoftBSLRecoveryStateError(
                        "pre-erase write attempt failed and E740=0 / stock DS2 could not be "
                        "confirmed; automatic baud fallback is blocked"
                    ) from error
            raise
        finally:
            if not retain_for_recovery:
                try:
                    d.close()
                except Exception:
                    pass
    # Boot-region writes use the same rule as ordinary full/tune writes: a failed CRC link gate may
    # downshift while flash is untouched; SoftBSLWriteRecoveryRequired stops the ladder after erase.
    return _with_baud_fallback(_attempt, baud, log, f"Fast write ({scope})")


def write_identity_sector(port, sector, prompt, log, baud="high", progress_cb=None,
                          chip_family=None, half="B", serial_factory=None):
    """Write the complete identity-containing erase sector on the visible half.

    The host flash primitive consumes a file-order 256 KB container even for a
    scoped operation. Build that container here so GUI callers cannot accidentally
    widen the scope. BOTTOM uses the fine 8 KB SA1 sector; 29F400 TOP uses the
    fused 64 KB SA7 sector. Verification and boot-write arming are mandatory.
    """
    half = str(half).upper()
    if half not in ("B", "T"):
        raise ValueError(f"identity half must be 'B' or 'T', got {half!r}")
    if half == "T" and chip_family != "amd":
        raise ValueError("TOP identity writes require the AMD/29F400 agent and 64 KB SA7 geometry")
    sector = bytes(sector)
    if half == "T":
        lo, expected = _TOP_ID_SECTOR_BASE, _TOP_ID_SECTOR_SIZE
    else:
        lo, expected = _BOTTOM_ID_SECTOR_BASE, _BOTTOM_ID_SECTOR_SIZE
    hi = lo + expected
    if len(sector) != expected:
        raise ValueError(
            f"{half} identity sector must be exactly {expected} bytes, got {len(sector)}")
    image = bytearray(b"\xFF" * _sb.IMAGE_SIZE)
    image[lo:hi] = sector
    if _sb.image_marker(image) != half:
        raise ValueError(
            f"identity sector does not contain a valid {half!r} bank marker at file 0x{_sb.MARKER_OFF:05X}")
    return run_flash(
        port, bytes(image), "sa1", prompt, log, baud=baud,
        progress_cb=progress_cb, do_verify=True, write_bootloader=True,
        chip_family=chip_family, serial_factory=serial_factory)


def write_tune(port, partial, log, baud="low", progress_cb=None, do_verify=True,
               chip_family=None, entry_mode="auto", serial_factory=None):
    """Live Fast TUNE write: enter the RAM agent and write the 24 KB calibration/tune PARTITION to
    the running bank via SoftBSL.write_tune_partial — the agent counterpart of ds2.write_partial.
    Unlike run_flash, this takes a 24 KB partial (NOT a 256 KB full image) and needs NO bank marker:
    flash_image's full-image/marker model is for full/install writes; a tune update just rewrites the
    running bank's cal block. `chip_family` ('amd'/'intel'/None) picks the flash agent (Intel 28F200
    -> agent_28f.hex; the AMD agent can't erase/program an Intel chip). Successful writes finalize
    to marker 0; post-erase failures retain the RAM agent for an in-place retry."""
    target = bytes(partial)

    def _attempt(tier):
        d, sb = _open_session(
            port, log, chip_family, require_d2xx=tier != "low", baud_tier=tier,
            entry_mode=entry_mode, serial_factory=serial_factory)
        tracker = _WriteProgressTracker(progress_cb)
        write_complete = False
        retain_for_recovery = False
        try:
            _set_agent_baud_if_needed(sb, tier)
            _prove_write_link(sb, tier, log)
            sb.write_tune_partial(target, do_verify=do_verify, progress_cb=tracker)
            write_complete = True
            if not do_verify:
                log("Read-back verification skipped (Verify off). ECU-side finalization will continue.")
            if not _recover_marker0(sb, log):
                raise SoftBSLRecoveryStateError(
                    "tune write completed, but E740=0 / stock DS2 finalization was not confirmed"
                )
        except Exception as error:
            if not write_complete and tracker.destructive_started:
                retain_for_recovery = True
                recovery = SoftBSLWriteRecovery(
                    port=str(port), ds2=d, agent=sb, operation="tune", target=target,
                    scope="tune", baud=tier, prompt=None, do_verify=bool(do_verify),
                    write_bootloader=False, chip_family=chip_family, error=error,
                )
                log(
                    "FLASH INCOMPLETE: tune erase began, so Soft-BSL is preserving the live "
                    "RAM agent. DO NOT TURN IGNITION OFF."
                )
                raise SoftBSLWriteRecoveryRequired(recovery) from error
            if not write_complete:
                if not _recover_marker0(sb, log):
                    raise SoftBSLRecoveryStateError(
                        "pre-erase tune attempt failed and E740=0 / stock DS2 could not be "
                        "confirmed; automatic baud fallback is blocked"
                    ) from error
            raise
        finally:
            if not retain_for_recovery:
                try:
                    d.close()
                except Exception:
                    pass
    return _with_baud_fallback(_attempt, baud, log, "Fast tune write")


def resume_write_recovery(recovery, *, progress_cb=None, log=lambda *_args: None):
    """Re-erase and re-flash the same target through a retained RAM-agent session.

    This never reopens COM, changes baud, or returns through the 0x2A/0x5A entry path.  Any
    retry failure keeps the same session open.  A successful write finalizes E740=0 and closes it.
    """
    if not isinstance(recovery, SoftBSLWriteRecovery):
        raise TypeError("recovery must be a SoftBSLWriteRecovery")
    if not recovery.is_open:
        raise SoftBSLRecoveryStateError("the retained Soft-BSL recovery session is closed")

    sb = recovery.agent
    sb.log = log
    tracker = _WriteProgressTracker(progress_cb)
    try:
        _prove_write_link(sb, recovery.baud, log)
        if recovery.operation == "tune":
            sb.write_tune_partial(
                recovery.target,
                do_verify=recovery.do_verify,
                progress_cb=tracker,
            )
            finalized_by_flash = False
        elif recovery.operation == "image":
            sb.flash_image(
                recovery.target,
                scope=recovery.scope,
                baud=recovery.baud,
                prompt=recovery.prompt,
                do_verify=recovery.do_verify,
                write_bootloader=recovery.write_bootloader,
                progress_cb=tracker,
                chip="28f200" if recovery.chip_family == "intel" else "29f400",
                baud_is_set=recovery.baud != "low",
            )
            finalized_by_flash = bool(recovery.do_verify)
        else:
            raise SoftBSLRecoveryStateError(
                f"unknown retained Soft-BSL operation {recovery.operation!r}"
            )
    except Exception as error:
        recovery.error = error
        log(
            "Soft-BSL recovery retry did not complete. Keep ignition ON; the RAM agent "
            "and host session remain open."
        )
        raise SoftBSLWriteRecoveryRequired(recovery) from error

    try:
        if not _recover_marker0(sb, log, finalize_sent=finalized_by_flash):
            raise SoftBSLRecoveryStateError(
                "recovery write completed, but E740=0 / stock DS2 finalization was not confirmed"
            )
    finally:
        recovery.ds2.close()
    return True


def read_image(port, scope, baud, progress_cb, log, chip_family=None,
               entry_mode="auto", serial_factory=None):
    """Live Fast Read: enter the RAM agent and read `scope` ('full'/'tune') back as bytes,
    with real progress_cb(done, total) movement. `chip_family`
    ('amd'/'intel'/None) picks the flash agent — reads are chip-agnostic, but using the right
    agent keeps a read + follow-up write on the same command set."""
    def _attempt(tier):
        d, sb = _open_session(
            port, log, chip_family, require_d2xx=tier != "low", baud_tier=tier,
            entry_mode=entry_mode, serial_factory=serial_factory)
        try:
            _set_agent_baud_if_needed(sb, tier)
            if scope == "tune":
                # the STANDARD 24 KB partition: DS2/CPU-order @0x10000, read RAW (descramble=False) so it
                # is byte-identical to ds2.read_partial — NOT the 64 KB file-order erase sector that
                # _flash_scope('tune') returns (the old bug: a 64 KB, wrongly-ordered partial).
                result = sb.read_range(_TUNE_CPU_BASE, _TUNE_SIZE, progress_cb=progress_cb,
                                       descramble=False, log_fn=log)
                return result
            # full: the whole 256 KB image in FILE order (descrambled), matching a saved full .bin.
            result = sb.read_range(0, _sb.IMAGE_SIZE, progress_cb=progress_cb,
                                   descramble=True, log_fn=log)
            return result
        finally:
            # ALWAYS recover (even on a failed/partial read — a raised-baud crc_read failure can leave the
            # agent stuck-high). A read entered flash mode via the 0x2A door (E740=1), so this is needed to
            # return to marker 0 + a rebooted, DS2-responsive ECU. See _recover_marker0.
            _recover_marker0(sb, log)
            try:
                d.close()
            except Exception:
                pass
    # A noisy-link read that exhausts its per-chunk CRC retries at the chosen rate is re-read whole at
    # each lower baud (reads are idempotent); low (9600) needs no D2XX. See _with_baud_fallback.
    return _with_baud_fallback(_attempt, baud, log, f"Fast read ({scope})")


def _read_identity_range(port, baud, progress_cb, log, chip_family, lo, length,
                         label, serial_factory=None):
    """Read one file-order identity range through a disposable RAM-agent session."""
    def _attempt(tier):
        d, sb = _open_session(
            port, log, chip_family, require_d2xx=tier != "low", baud_tier=tier,
            serial_factory=serial_factory)
        try:
            _set_agent_baud_if_needed(sb, tier)
            return sb.read_range(
                lo, length, progress_cb=progress_cb, descramble=True, log_fn=log)
        finally:
            _recover_marker0(sb, log)
            try:
                d.close()
            except Exception:
                pass

    return _with_baud_fallback(_attempt, baud, log, label)


def read_identity_data(port, baud, progress_cb, log, chip_family=None, half="B",
                       serial_factory=None):
    """Read enough cached data to display and safely edit identity on one half.

    Uses the same disposable RAM-agent session, high->mid->low baud fallback,
    marker-0 recovery, and transport cleanup as the other Soft-BSL dumps.
    BOTTOM reads the 16 KB identity/descriptor window. TOP reads the complete
    64 KB fused SA7 sector so a later VIN write can restore every erased byte.
    """
    half = str(half).upper()
    if half == "T":
        if chip_family != "amd":
            raise ValueError("TOP identity reads require AMD/29F400 geometry")
        lo, length = _TOP_ID_SECTOR_BASE, _TOP_ID_SECTOR_SIZE
    elif half == "B":
        lo, length = _BOOT_ID_FILE_BASE, _BOOT_ID_SIZE
    else:
        raise ValueError(f"identity half must be 'B' or 'T', got {half!r}")
    return _read_identity_range(
        port, baud, progress_cb, log, chip_family, lo, length,
        "Fast read (BOOT identity)", serial_factory=serial_factory)


def read_identity_sector(port, baud, progress_cb, log, chip_family=None, half="B",
                         serial_factory=None):
    """Read the exact erase sector used by a VIN write (BOTTOM SA1 or TOP SA7)."""
    half = str(half).upper()
    if half == "T":
        if chip_family != "amd":
            raise ValueError("TOP identity-sector reads require AMD/29F400 geometry")
        lo, length = _TOP_ID_SECTOR_BASE, _TOP_ID_SECTOR_SIZE
    elif half == "B":
        lo, length = _BOTTOM_ID_SECTOR_BASE, _BOTTOM_ID_SECTOR_SIZE
    else:
        raise ValueError(f"identity half must be 'B' or 'T', got {half!r}")
    return _read_identity_range(
        port, baud, progress_cb, log, chip_family, lo, length,
        "Fast read (identity erase sector)", serial_factory=serial_factory)


def read_cross_bank_image(port, prompt, log, baud="high", progress_cb=None,
                          serial_factory=None):
    """Read the currently-selected 29F400 TOP half through an agent entered from BOTTOM.

    The operator flips A17 only while the agent is resident in RAM.  A marker read before and
    after the flip must change before the 256 KB read begins, preventing an accidental BOTTOM
    read from being composed as the golden TOP base.  Every exit path asks for LOWER again,
    finalizes marker 0, resets, and closes the port.  Reads may safely retry at lower baud tiers.
    """
    guard_addr = _sb.MARKER_OFF ^ _sb.DESCR
    def _attempt(tier):
        d, sb = _open_session(
            port, log, chip_family="amd", require_d2xx=tier != "low", baud_tier=tier,
            serial_factory=serial_factory)
        may_be_upper = False
        try:
            _set_agent_baud_if_needed(sb, tier)
            before = sb.crc_read(guard_addr, 4)
            prompt(
                "READ TOP BASE: flip the A17 cockpit switch to UPPER now, then continue.\n\n"
                "The RAM agent is already running from the intact BOTTOM bank. This step is "
                "read-only; the tool will verify that the visible bank changed before reading.")
            may_be_upper = True
            after = sb.crc_read(guard_addr, 4)
            if after == before:
                raise CrossBankSafetyError(
                    f"A17 flip could not be confirmed: guard @0x{guard_addr:05X} remained "
                    f"{before.hex()}. No image was accepted; the BOTTOM bank may still be visible.")
            log(f"A17 flip confirmed for TOP read: {before.hex()} -> {after.hex()}.")
            image = sb.read_range(
                0, _sb.IMAGE_SIZE, progress_cb=progress_cb,
                descramble=True, log_fn=log)
            prompt(
                "TOP base read is complete. Flip the A17 cockpit switch back to LOWER now, "
                "then continue so the ECU can reset into the working bank.")
            may_be_upper = False
            return bytes(image)
        finally:
            try:
                if may_be_upper:
                    # A read/CRC failure can occur while A17 is physically upper. Make the required
                    # recovery position a modal action, not a line that can be missed in the log.
                    prompt(
                        "TOP read stopped before completion. Before recovery/reset, flip the A17 "
                        "cockpit switch back to LOWER, then continue.")
                    may_be_upper = False
            finally:
                _recover_marker0(sb, log)
                try:
                    d.close()
                except Exception:
                    pass

    return _with_baud_fallback(_attempt, baud, log, "Golden-TOP base read")


def run_cross_bank(port, image, prompt, log, chip_family=None, baud="high",
                   serial_factory=None, progress_cb=None):
    """Live BRICK-CLASS: enter from the bottom half, then flash the 29F400 golden
    TOP half (SA7 fused boot written LAST; A17 flips prompted). Recoverable if
    booted from the intact bottom."""
    validate_flash_image_family(image, chip_family, write_bootloader=True)
    # This brick-class path gets the same staged entry, but its fallback ladder ends at the
    # session handoff.  flash_cross_bank has its own erase boundary and must never be retried
    # automatically after that point.
    d = sb = None
    tiers = _baud_tiers_from(baud)
    try:
        for index, tier in enumerate(tiers):
            try:
                d, sb = _open_session(
                    port, log, chip_family=chip_family,
                    require_d2xx=tier != "low", baud_tier=tier,
                    serial_factory=serial_factory)
                break
            except D2XXRequiredError:
                if tier != "low" and "low" in tiers[index + 1:]:
                    continue
                raise
            except SoftBSLError as error:
                if tier == tiers[-1]:
                    raise
                log(f"Golden-TOP write: staged '{tier}' entry failed before erase ({error}); "
                    f"trying '{tiers[index + 1]}'.")
        if sb is None:
            raise SoftBSLError("Golden-TOP write could not enter a staged RAM session")
        sb.flash_cross_bank(
            bytes(image), baud=tiers[index], prompt=prompt,
            progress_cb=progress_cb,
        )
    finally:
        if d is not None:
            try:
                d.close()
            except Exception:
                pass
