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
from engines.softbsl.softbsl_host import SoftBSLError  # stable ref (tests monkeypatch _sb)


class CrossBankSafetyError(RuntimeError):
    """A physical-bank safety gate failed; unlike link noise, this must not baud-retry."""


D2XXRequiredError = _sb.D2XXRequiredError


class SoftBSLRecoveryStateError(RuntimeError):
    """A pre-erase attempt could not prove a safe return to normal DS2."""


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


class FlashFamilyMismatchError(RuntimeError):
    """An image's installed flash-driver family is unsafe for the live ECU."""


def validate_flash_image_family(image, connected_family, *, write_bootloader=False):
    """Fail before agent entry when a full image would cross flash command sets.

    A recognized Intel/AMD driver image is never flashable to a different or
    unknown connected family. This remains true even when the boot region is not
    armed, so an ordinary full-write route cannot bypass the geometry contract.
    Any armed boot write is stricter and also rejects an unrecognized image driver.
    Offline patch build/save does not call this live-operation gate.
    """
    image_family = ecu_info.image_chip_family(bytes(image))
    connected_family = connected_family if connected_family in ("amd", "intel") else None
    labels = {"amd": "AMD/JEDEC 29F", "intel": "Intel 28F"}

    if image_family in labels and connected_family != image_family:
        live = labels.get(connected_family, "an unknown flash family")
        raise FlashFamilyMismatchError(
            f"Flash blocked before agent entry: the image carries the {labels[image_family]} "
            f"driver at file 0x423C, but the connected ECU reports {live}. "
            "Cross-family images may be built and saved, but they cannot be flashed to "
            "different flash geometry.")

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
                return attempt("low")
            raise
        except SoftBSLError as e:
            if tier == tiers[-1]:
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


def marker(image):
    """'T'/'B' bank-ID marker (file 0x5FFC), or None."""
    return _sb.image_marker(bytes(image))


def _capture(fn, *args, **kw):
    lines = []
    fn(*args, log=lines.append, **kw)
    return "\n".join(lines)


def crossbank_plan(image):
    """Text preview of the cross-bank top-half write plan — no serial I/O."""
    return _capture(_sb.crossbank_dry_run, bytes(image))


def _open_session(port, log, chip_family=None, require_d2xx=False, baud_tier=None):
    """Open the port and enter the RAM agent via the STEADY-STATE door (0x2A door_magic ->
    5a loader window), not the disposable 0x43 install-only door — 0x43 only exists during
    the one-time install bootstrap and is erased afterward, so entering via it here would
    always fail on an already-installed ECU. ensure_flash_mode() sends 0x2A, keeps K-Line
    quiet through the installed door's watchdog interval, then polls E740 with bounded DS2
    reads. Production service calls use the staged 0x5A entry at the selected exact tier;
    legacy callers retain enter_retry(trigger='5a') compatibility.

    `chip_family` ('amd'/'intel'/None) selects the flash agent: an Intel 28F200 needs
    agent_28f.hex (Intel command set + 12 V VPP); AMD/None default to agent.hex. Reads are
    chip-agnostic, but an ERASE/PROGRAM with the wrong agent silently fails on the real chip.

    On ANY entry failure: ensure_flash_mode() may have already fired the 0x2A door, committing
    E740=1 (flash-listen, non-drivable, persistent across key-cycles). Walk it back to marker 0 +
    drivable via the running agent's self-finalize before closing +
    re-raising — otherwise a missed 5a window strands a previously-drivable ECU in flash mode."""
    d = _sbds2.DS2Interface(port, baud=9600, verbose=False, echo=True)
    d.open()
    if require_d2xx and not d.uses_d2xx:
        d.close()
        raise D2XXRequiredError(f"{port} opened through {d.transport_name or 'an unknown transport'}")
    sb = _sb.SoftBSL(d, log=log)
    staged = baud_tier is not None and hasattr(sb, "enter_staged")
    try:
        agent = _sb.load_agent(_sb.agent_path_for_family(chip_family))
        sb.ensure_flash_mode(poll_ready=True)
        if staged:
            sb.enter_staged(agent, baud_tier, trigger="5a")
        else:
            sb.enter_retry(agent, trigger="5a")
    except Exception as error:
        if staged:
            # Stage-one owns its pre-erase failure/reset path.  Do not send the production-agent
            # recovery command to a stage that has not emitted the A5 handoff banner.
            try:
                d.close()
            except Exception:
                pass
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


def _set_agent_baud_if_needed(sb, tier):
    """Keep compatibility with legacy fakes/manual sessions while staged entry owns the baud."""
    if tier != "low" and not getattr(sb, "staged_entry", False):
        sb.set_baud(tier)


def run_flash(port, image, scope, prompt, log, baud="low", progress_cb=None,
             do_verify=True, write_bootloader=False, chip_family=None):
    """Live full-image-scope write with a brief pre-erase link gate.

    High/mid failures may fall back only while flash is untouched. Once the host emits the
    ``erase`` boundary, an exception retains the open RAM agent and raises
    :class:`SoftBSLWriteRecoveryRequired`. Successful writes finalize E740=0; ``do_verify``
    controls only the requested read-back verification.
    """
    validate_flash_image_family(
        image, chip_family, write_bootloader=write_bootloader)
    target = bytes(image)

    def _attempt(tier):
        d, sb = _open_session(
            port, log, chip_family, require_d2xx=tier != "low", baud_tier=tier)
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
                          chip_family=None, half="B"):
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
        chip_family=chip_family)


def write_tune(port, partial, log, baud="low", progress_cb=None, do_verify=True,
               chip_family=None):
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
            port, log, chip_family, require_d2xx=tier != "low", baud_tier=tier)
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


def read_image(port, scope, baud, progress_cb, log, chip_family=None):
    """Live Fast Read: enter the RAM agent and read `scope` ('full'/'tune') back as bytes,
    with real progress_cb(done, total) movement. `chip_family`
    ('amd'/'intel'/None) picks the flash agent — reads are chip-agnostic, but using the right
    agent keeps a read + follow-up write on the same command set."""
    def _attempt(tier):
        d, sb = _open_session(
            port, log, chip_family, require_d2xx=tier != "low", baud_tier=tier)
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


def _read_identity_range(port, baud, progress_cb, log, chip_family, lo, length, label):
    """Read one file-order identity range through a disposable RAM-agent session."""
    def _attempt(tier):
        d, sb = _open_session(
            port, log, chip_family, require_d2xx=tier != "low", baud_tier=tier)
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


def read_identity_data(port, baud, progress_cb, log, chip_family=None, half="B"):
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
        "Fast read (BOOT identity)")


def read_identity_sector(port, baud, progress_cb, log, chip_family=None, half="B"):
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
        "Fast read (identity erase sector)")


def read_cross_bank_image(port, prompt, log, baud="high", progress_cb=None):
    """Read the currently-selected 29F400 TOP half through an agent entered from BOTTOM.

    The operator flips A17 only while the agent is resident in RAM.  A marker read before and
    after the flip must change before the 256 KB read begins, preventing an accidental BOTTOM
    read from being composed as the golden TOP base.  Every exit path asks for LOWER again,
    finalizes marker 0, resets, and closes the port.  Reads may safely retry at lower baud tiers.
    """
    guard_addr = _sb.MARKER_OFF ^ _sb.DESCR
    def _attempt(tier):
        d, sb = _open_session(
            port, log, chip_family="amd", require_d2xx=tier != "low", baud_tier=tier)
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


def run_cross_bank(port, image, prompt, log, chip_family=None, baud="high"):
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
                    require_d2xx=tier != "low", baud_tier=tier)
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
        sb.flash_cross_bank(bytes(image), baud=tiers[index], prompt=prompt)
    finally:
        if d is not None:
            try:
                d.close()
            except Exception:
                pass
