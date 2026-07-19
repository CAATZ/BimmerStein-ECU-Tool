"""Typed application service for persistent Soft-BSL installation.

The GUI calls the safety-critical engine directly and supplies typed callbacks
for progress, conversion authorization, and the mandatory key-cycle.

The brick-class sequencing and safety gates remain implemented once in the
internal Soft-BSL module; no CLI argument object, stdout capture, or subprocess
is involved in the desktop application path.
"""
from engines.softbsl import softbsl_host as _sb
from ds2 import DS2Interface as AppDS2Interface


class SoftBSLInstallError(RuntimeError):
    """The in-process install engine rejected or failed an operation."""


class SoftBSLInstallCancelled(SoftBSLInstallError):
    """The operator stopped installation before the persistent-image erase."""

    def __init__(self, message, *, phase=None):
        self.phase = phase
        super().__init__(message)


class SoftBSLInstallRecovery:
    """Application-facing wrapper for a retained installer transport."""

    def __init__(self, engine_recovery):
        self.engine_recovery = engine_recovery

    @property
    def phase(self):
        return self.engine_recovery.phase

    @property
    def port(self):
        return self.engine_recovery.port

    @property
    def is_open(self):
        return self.engine_recovery.is_open

    def close_after_confirmed_power_cycle(self):
        self.engine_recovery.close_after_confirmed_power_cycle()


class SoftBSLInstallRecoveryRequired(SoftBSLInstallError):
    """An install write failed post-erase and remains recoverable in place."""

    def __init__(self, recovery):
        self.recovery = recovery
        phase = "temporary bootstrap" if recovery.phase == "bootstrap" else "persistent target"
        super().__init__(
            f"Soft-BSL installation is incomplete during the {phase} write. "
            "DO NOT TURN IGNITION OFF; the same live recovery session is still open."
        )


_NOISY_INSTALL_PREFIXES = (
    "bootstrap door :", "target image   :", "composed ->", "image:",
    "ref @", "ck:", "identify:", "base:", "bootstrap =", "target    =",
    "== compose install", "[version gate]", "[flash-ic gate]",
    "program sig @", "driver signature @", "(amd ", "(28f ",
    "sequence:", "image ok", "-- ds2 write-mode",
    "-- wait for power-on", "0xe659 =", "program-low:",
)


def _install_log(log):
    """Keep the window concise while retaining installer detail in session logs."""
    def forward(message, level="info"):
        message = str(message).strip()
        if not message:
            return
        plain = message.lower().lstrip("- ")
        if plain.startswith(_NOISY_INSTALL_PREFIXES):
            try:
                log(message, "debug")
            except TypeError:
                log(message)
            return
        if "phase 1/3:" in plain:
            message = "Phase 1/3: preparing the temporary DS2 entry path."
        elif "phase 2/3:" in plain:
            message = "Phase 2/3: writing the persistent Soft-BSL image."
        elif "phase 3/3:" in plain:
            message = "Phase 3/3: verifying the installed image through DS2."
        elif "targeted bootstrap verify" in plain:
            message = "Verifying the temporary entry code before the ignition cycle."
        elif "bootstrap patch verified" in plain:
            message = "Temporary entry code verified."
        elif "phase 2 finalized e740=0" in plain:
            message = "Flash state finalized; the ECU rebooted into normal mode."
        elif "install done" in plain:
            # The GUI completion handler owns the one user-facing success line.
            # Keep the engine's richer terminal record in the session file.
            level = "debug"
        elif "fast bootstrap complete" in plain and "program finalized" in plain:
            level = "debug"
        elif "fast bootstrap complete" in plain:
            message = "Phase 1/3 complete; the required ignition cycle may begin."
        elif plain.startswith((
            "ecu flash-mode marker", "d2xx queue status", "staged ",
            "streaming agent", "agent running", "agent baud preflight",
            "install: assuming", "arming bootloader writes", "erase ",
            "erase done", "programmed up to", "programmed ",
            "verify (read-back)", "verify ok", "marker-0 finalize",
            "[ok] boot/param1", "[ok] program door", "[ok] program 0x43",
        )):
            level = "debug"
        elif plain.startswith("flash-ic: auto-detected"):
            family = "Intel 28F200" if "intel" in plain else "AMD/JEDEC"
            message = f"Flash command set detected: {family}."
        if "failed" in plain or "mismatch" in plain or "refusing" in plain:
            level = "error"
        elif "warning" in plain or "brick-class" in plain or "wipe" in plain:
            level = "warn"
        elif level != "debug" and (
            "verified" in plain or "complete" in plain or "install done" in plain
        ):
            level = "ok"
        try:
            log(message, level)
        except TypeError:
            log(message)
    return forward


def _install_args(*, port, target=None, bootstrap=None, base=None, with_calguard=False,
                  allow_convert=False, prompt, baud="low", progress_cb=None,
                  confirm_reinstall=None, preserve_cal=True):
    """Build the typed internal installation request."""
    return _sb.InstallRequest(
        port=port, prompt=prompt, target=target, bootstrap=bootstrap, base=base,
        with_calguard=with_calguard, allow_convert=lambda: bool(allow_convert),
        baud=baud, progress_cb=progress_cb, ds2_factory=AppDS2Interface,
        confirm_reinstall=confirm_reinstall, preserve_cal=bool(preserve_cal),
    )


def _run_install(args, log):
    """Run the typed installer and normalize its domain error for the GUI."""
    try:
        _sb.install(args, _install_log(log))
    except _sb.InstallRecoveryRequired as error:
        raise SoftBSLInstallRecoveryRequired(
            SoftBSLInstallRecovery(error.recovery)
        ) from error
    except _sb.InstallCancelled as error:
        raise SoftBSLInstallCancelled(
            str(error), phase=getattr(error, "phase", None)
        ) from error
    except _sb.SoftBSLError as error:
        raise SoftBSLInstallError(str(error)) from error
    return 0


def resume_install(recovery, log, progress_cb=None):
    """Continue a retained install without reopening COM or re-entering its door."""
    if not isinstance(recovery, SoftBSLInstallRecovery):
        raise TypeError("recovery must be a SoftBSLInstallRecovery")
    try:
        return _sb.resume_install(
            recovery.engine_recovery,
            _install_log(log),
            progress_cb=progress_cb,
        )
    except _sb.InstallRecoveryRequired as error:
        recovery.engine_recovery = error.recovery
        raise SoftBSLInstallRecoveryRequired(recovery) from error
    except _sb.InstallCancelled as error:
        raise SoftBSLInstallCancelled(
            str(error), phase=getattr(error, "phase", None)
        ) from error
    except _sb.SoftBSLError as error:
        raise SoftBSLInstallError(str(error)) from error


def install(target, bootstrap, port, allow_convert, prompt, log, baud="low", progress_cb=None):
    """Guided install from two pre-built images through the in-process engine."""
    args = _install_args(port=port, target=target, bootstrap=bootstrap,
                         allow_convert=allow_convert, prompt=prompt,
                         baud=baud, progress_cb=progress_cb)
    return _run_install(args, log)


def install_compose(port, base, with_calguard, allow_convert, prompt, log, baud="low",
                    progress_cb=None, confirm_reinstall=None, preserve_cal=True):
    """Prepare and install Soft-BSL without pre-built firmware images.

    With ``base=None``, the connected ECU's full image is the source and therefore preserves its
    identity. A supplied base must be a consistent MS41.2 or MS41.3 full image. ``allow_convert``
    authorizes a cross-version replacement; ``preserve_cal`` is honored when the connected ECU
    and composed target are the same consistent version. Returns zero on success.
    """
    args = _install_args(port=port, base=base, with_calguard=with_calguard,
                         allow_convert=allow_convert, prompt=prompt,
                         baud=baud, progress_cb=progress_cb,
                         confirm_reinstall=confirm_reinstall,
                         preserve_cal=preserve_cal)
    return _run_install(args, log)


def compose_persistent_target(base, with_calguard=True, *, marker="T", chip="29f400"):
    """Build the persistent Soft-BSL target without serial I/O.

    The regular installer and golden-TOP workflow share the command-set gate, persistent patch
    composition, optional cal_guard, and checksum handling.
    Returns ``(image, patch_ids, build_log)``.
    """
    try:
        return _sb.compose_persistent_image(
            bytes(base), chip, with_calguard=bool(with_calguard), marker=marker)
    except _sb.SoftBSLError as error:
        raise SoftBSLInstallError(str(error)) from error


def d2xx_available(port=None):
    """True only when D2XX can open the selected adapter (or any device if no port is given)."""
    try:
        from engines.softbsl import d2xx_serial
        return bool(d2xx_serial.port_available(port))
    except Exception:
        return False
