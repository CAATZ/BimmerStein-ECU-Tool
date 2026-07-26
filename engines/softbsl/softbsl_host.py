#!/usr/bin/env python3
"""Internal host library for the Soft-BSL RAM agent, with an optional CLI wrapper.

The soft-BSL rides the SAME K-line/OBD interface as DS2 (no bench harness):
  1. DS2 (8E2 @ 9600): prepare (0xA2) + seed/key unlock (0x90) -> flash phase 0xE658==2.
  2. DS2 cmd 0x9C "SBSL"<len><crc16>: the param1 stub validates, ACKs, receives the
     agent into SRAM 0xD800, CRC-checks it, kills interrupts and jumps in.
  3. The agent runs from RAM and speaks a tiny protocol (I/S/B/W/E/P/V/R) over the SAME
     line, KEEPING the firmware's DS2 framing (8E2, S0CON=0x80E7) - so the host never
     changes parity (no mid-session switch, no banner race). 8E2 also gives per-byte
     parity error detection and matches the firmware's own DS2 framing.
  4. The agent can escalate baud (9600 -> 187500) for bulk transfer (still 8E2).

Bank model: Flash A17 is a COCKPIT SWITCH. The same agent runs from RAM on either
visible half. The bank-ID marker @CPU 0x1FFC selects the host's fine BOTTOM or coarse
TOP erase geometry; boot-containing sectors remain preserved unless explicitly armed.

AMD-29F and Intel-28F use separate validated agents. Intel writes require the
correct 12 V VPP control path; hardware BSL remains the recovery backstop.
"""
import argparse
import copy
import hashlib
import json
import os
import sys
import time
from dataclasses import dataclass

import ecu_info

try:
    import serial
except ImportError:
    serial = None

from . import checksum
from . import chipdefs
from . import ds2
from .ds2 import DS2Interface, DS2Error, _xor, _progress_bar   # _progress_bar: shared TTY progress bar
from operation_log import emit as _emit, operation_log_sink


def _tty_newline():
    """Finish an interactive CLI progress line without writing during GUI use."""
    if getattr(sys.stdout, "isatty", lambda: False)():
        sys.stdout.write("\n")


# ── agent / protocol constants ────────────────────────────────────────────────
DESCR     = 0x4000                      # file <-> CPU/DS2 descramble (XOR per 16 KB)
# UNMAPPED BUS HOLE: CPU 0xC000-0xFFFF is the BUSCON1 peripheral window, not flash. A raw read there
#   returns a floating address-ramp ("00 01 02..."), not data -- the true content is blank 0xFF. The
#   'K' agent read has no bus awareness, so the HOST FF-fills this window on dump/read (as Flasher and
#   BSL-Unbricker do). Write/verify are already safe (they FF-skip blank blocks). == FILE 0x8000-0xBFFF.
HOLE_CPU  = (0xC000, 0x10000)           # unmapped CPU window == FILE 0x8000-0xBFFF after ^DESCR
def _in_hole(cpu):                      # is a linear/CPU address inside the unmapped bus window?
    return HOLE_CPU[0] <= cpu < HOLE_CPU[1]
BANNER    = 0xA5
ACK       = 0x06
CRC_FAIL  = 0x15
STAGE_READY = 0x5B                 # isolated staged-entry body greeting
STAGE_HANDSHAKE = 0xA6            # prove the selected post-header baud
DENY      = 0x03                        # agent policy-deny status (erase/program)
BLOCK     = 0x80                        # 128 B program block (fits the agent 1-byte len; even; /16K)
_SETTLE   = 0.002                       # s: line-settle pause before each command frame
PROG_RETRIES = 8                        # re-send a block/chunk on a transient status (2 fail / 4
                                        #    cksum/CRC): byte-flips on this marginal K-line are caught
                                        #    by the agent's CRC/checksum; a CRC-fail programs nothing
                                        #    and AMD re-program is idempotent, so retry.
CHUNK_SIZE   = 0x400                    # 1024 B: one 'C' frame's data span, sent as ONE CRC16-checked
                                        #    bulk burst (~8x fewer half-duplex turnarounds than the
                                        #    128B 'P' block = the loader-style reliable path). Divides
                                        #    every sector + prog_lo (0/0x10000): a chunk never straddles
                                        #    a sector/erase boundary or a 16KB DPP0 page.
CHK          = ord("C")                 # 0x43 agent chunk opcode (distinct from the DS2-layer 0x43)
CRD          = ord("K")                 # 0x4B agent CRC-checked READ opcode: n bytes + CRC16 (big-endian)
                                        #   over the SAME _crc(a3+data,0xFFFF) as the 'C' write -> high-baud
                                        #   read integrity without confirmed_read's double-read heuristic.
                                        #   Reads use the full CHUNK_SIZE (1 KB) at every baud.
MARKER_OFF   = 0x5FFC                   # file offset of the bank-ID marker (A5 5A <half> <~half>)
PARAM1_FILE  = (0x4000, 0x6000)         # file range of param1 = SA1 = bootloader (preserved)
IMAGE_SIZE   = 0x40000                  # 256 KB (one half)
# tier -> (S0BG reload byte the 'B' opcode writes, host baud). Async baud = fOSC/(64*(S0BG+1)) at BRS=0
# (C166 manual). fOSC = 24.000 MHz (12 MHz crystal x2 PLL; edge-measured, see [[reference_ms41_cpu_clock]]).
# Ladder @24.0 MHz: 9375=S0BG39, 93750=S0BG3, 187500=S0BG1, 375000=S0BG0. Host mid/high match the ECU
# exactly. 'low' is a sentinel (set_baud is SKIPPED -> the agent keeps the inherited 9600, in-tolerance vs
# the stock DS2's S0BG=38/9615). The 'B' opcode acks at the old baud, writes S0BG, then both ends switch.
BG = {"low": (38, 9600), "mid": (3, 93750), "high": (1, 187500)}
HI_BAUD = BG["high"][1]                  # 187500: high-tier host baud (referenced by the stuck-high recovery)
MAGIC_HI, MAGIC_LO = 0x9C, 0x9C         # 0x43 clear-adapts magic selector (collision-proof: real sel_lo in {00,FF})
I, S, B, W, E, P, V, R = (ord(c) for c in "ISBWEPVR")
HALF_NAME = {"T": "top/golden", "B": "bottom/working"}
_DEPRECATED_LOADER_IDS = (
    "softbsl_loader_legacy",
    "softbsl_loader_relocated_v1",
)
_DOOR_PATCH_IDS = {
    "MS41.0": ("door_0x43_ms410", "door_magic_ms410"),
    "MS41.1": ("door_0x43_ms411", "door_magic_ms411"),
    "MS41.2": ("door_0x43", "door_magic"),
    "MS41.3": ("door_0x43", "door_magic"),
}
# Exact CPU 0x1C32 CRC helper shipped by the first descriptor-safe relocation.
# It ACKs the 0x5A trigger and accepts the upload but does not issue the CRC ACK
# on real C166 hardware.  Keep this synchronized with the deprecated patch
# descriptor; it lets ordinary fast operations fail safely before firing 0x2A.
_RELOCATED_V1_CRC_CPU = 0x1C32
_RELOCATED_V1_CRC = bytes.fromhex(
    "f075e6f500d8e6f6ffff40572d10a98551c8e08df04669817c1649802d04"
    "e6f401a051c851d928d13df508510deec2f427e45c84c2f528e470454064"
    "3d02e108db00e118db00"
)


@dataclass
class InstallRequest:
    """Typed application contract for a persistent Soft-BSL installation."""
    port: str
    prompt: object
    base: object = None
    target: str = None
    bootstrap: str = None
    agent: str = None
    chip: str = "auto"
    half: str = "lower"
    trigger: str = "43"
    baud: str = "low"
    with_calguard: bool = False
    with_alphan: bool = False
    preserve_cal: bool = True
    allow_convert: object = None
    confirm_reinstall: object = None
    keycycle_retry_prompt: object = None
    phase1_reentry_prompt: object = None
    progress_cb: object = None
    ds2_factory: object = DS2Interface
    verbose: bool = False
    no_echo: bool = False
    dry_run: bool = False
    force: bool = False
    yes: bool = True
    bootstrap_verify_ranges: tuple = ()

    def __post_init__(self):
        if self.agent is None:
            self.agent = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent.hex")
        self.base_bytes = (bytes(self.base)
                           if isinstance(self.base, (bytes, bytearray, memoryview)) else None)
        self.base = None if self.base_bytes is not None else self.base
        self.cmd = "install"
        self.keycycle_prompt = self.prompt
        if self.keycycle_retry_prompt is None:
            self.keycycle_retry_prompt = getattr(self.prompt, "retry_cancel", None)
        if self.phase1_reentry_prompt is None:
            self.phase1_reentry_prompt = getattr(
                self.prompt, "phase1_reentry_retry_cancel", None
            )
        self.confirm_convert = self.allow_convert

# Sectors a full BOTTOM-half (A17 low / --half lower working bank) write erases
# (CPU/DS2 addr inside each; agent erases the containing sector). SA1/param1 is PROTECTED (skipped
# unless --write-bootloader). The fine bottom is what makes granular DS2 writes possible.
ERASE_BOTTOM = [
    (0x04000, "SA0 boot",        False),
    (0x02000, "SA2 param2",      False),
    (0x08000, "SA3 main-low",    False),
    (0x10000, "SA4 cal",         False),
    (0x20000, "SA5 prog-high",   False),
    (0x30000, "SA6 prog-high",   False),
    (0x00000, "SA1 BOOTLOADER",  True),     # only with --write-bootloader (armed via 'W')
]
# TOP half (A17 high / --half upper = factory / the dual-bank "golden" bank) = COARSE: four uniform 64K
# sectors. SA7 FUSES vectors+param1+bootloader+orchestrator+param2 into ONE 64K erase, so a full DS2 write
# is IMPOSSIBLE (the stock driver runs from SA7); only the RAM agent can. SA7 is erased+written LAST
# (SA8->SA9->SA10->SA7) so any earlier failure still leaves the old bootloader/DS2 intact.
ERASE_UPPER = [
    (0x10000, "SA8 cal",         False),
    (0x20000, "SA9 prog-high",   False),
    (0x30000, "SA10 prog-high",  False),
    (0x00000, "SA7 FUSED boot",  True),     # 64K vectors+bootloader+params; brick-class; armed via 'W'; LAST
]

# The 24 KB calibration/tune PARTITION as the factory reads/writes it: CONTIGUOUS in CPU/DS2 order
# @0x10000 (== ds2.PARTIAL_DS2_ADDR / ms41.TUNE_DS2_BASE), NOT file-descrambled. Erasing the cal block
# and writing back only these 24 KB is LOSSLESS on EVERY MS41 variant + BOTH chip families: the rest of
# the erased block is blank 0xFF (29F: SA4 0x16000-0x20000 blank; 28F200: the 96 KB main-D block
# 0x08000-0x20000 co-erases only blank 0x08000-0x0BFFF + the unmapped hole + blank 0x16000-0x20000).
TUNE_CPU_BASE     = 0x10000
TUNE_PARTIAL_SIZE = 24 * 1024


def _flash_scope(scope="full", half="lower", chip=None):
    """Return erase blocks and programming span for a chip/scope.

    Intel 28F uses its native 8K/16K/96K/128K block geometry; AMD lower-bank
    devices use SA0-SA6 and 29F400 upper uses SA7-SA10. Scopes:
      full    = whole half. program = program-high only (tune-safe). tune = the cal sector.
      program_checked = internal checksum-aware program scope: param2 + program-high.
      softbsl = boot/param1 loader + program-high door (legacy bottom-only scope).
      softbsl_ms412 = the same plus param2/program-low for the corrected program CRC.
      sa1 = the boot/param1 block (legacy scope name)."""
    chip = "29f400" if chip in (None, "auto") else chip
    if half == "upper":
        if scope == "tune":
            return [(0x10000, "SA8 cal", False)], 0x10000, 0x20000
        if scope == "program":
            return [s for s in ERASE_UPPER if s[0] in (0x20000, 0x30000)], 0x20000, 0x40000
        if scope == "sa1":
            # TOP fuses boot, param1/identity, and param2 into one 64 KB SA7 sector.
            # A sector-only edit must therefore supply/rewrite file 0x0000-0xFFFF.
            return [(0x00000, "SA7 FUSED boot", True)], 0, 0x10000
        if scope in ("program_checked", "softbsl", "softbsl_ms412"):
            raise SoftBSLError(f"scope {scope!r} is a BOTTOM-half concept -- the top half's boot is the FUSED SA7 "
                     f"(64K). Rewrite the top with `--scope full` (agent-only; SA7 written last).")
        return list(ERASE_UPPER), 0, IMAGE_SIZE            # full: SA8/SA9/SA10 (+SA7 if armed)
    if chip == "28f200":
        # Intel names are block-functional, not AMD SA numbers. Main E is ONE
        # 128 KB erase block: issuing erase at both 0x20000 and 0x30000 merely
        # erases the same block twice.
        if scope == "tune":
            return [(0x10000, "28F main-D calibration block (96K)", False)], 0x08000, 0x20000
        if scope == "program":
            return [(0x20000, "28F main-E program block (128K)", False)], 0x20000, 0x40000
        if scope == "program_checked":
            return [
                (0x20000, "28F main-E program block (128K)", False),
                (0x02000, "28F param2/program-checksum block (8K)", False),
            ], 0, IMAGE_SIZE
        if scope == "softbsl":
            return [
                (0x20000, "28F main-E program block (128K)", False),
                (0x00000, "28F boot/param1 block (8K)", True),
            ], 0, IMAGE_SIZE
        if scope == "softbsl_ms412":
            return [
                (0x20000, "28F main-E program block (128K)", False),
                (0x02000, "28F param2/program-checksum block (8K)", False),
                (0x00000, "28F boot/param1 block (8K)", True),
            ], 0, IMAGE_SIZE
        if scope == "sa1":
            return [(0x00000, "28F boot/param1 block (8K)", True)], PARAM1_FILE[0], PARAM1_FILE[1]
        # Keep the established full-image order, but use each Intel block once
        # and leave the boot/param1 block until last.
        return [
            (0x02000, "28F param2 block (8K)", False),
            (0x04000, "28F boot-A block (16K)", False),
            (0x10000, "28F main-D calibration block (96K)", False),
            (0x20000, "28F main-E program block (128K)", False),
            (0x00000, "28F boot/param1 block (8K)", True),
        ], 0, IMAGE_SIZE
    if scope == "tune":
        return [(0x10000, "SA4 cal", False)], 0x10000, 0x20000
    if scope == "program":
        secs = [s for s in ERASE_BOTTOM if s[0] in (0x20000, 0x30000)]
        return secs, 0x20000, 0x40000
    if scope == "program_checked":
        # Factory MS41 program verification covers program-high but stores the result in
        # param2 (file 0x6050 / CPU 0x2050). Rewrite SA2 atomically with SA5/SA6.
        secs = [s for s in ERASE_BOTTOM if s[0] in (0x02000, 0x20000, 0x30000)]
        return secs, 0, IMAGE_SIZE
    if scope == "softbsl":                     # SA1 (driver + 0x5A loader) + SA5/SA6 (0x2A door); CAL/boot/params UNTOUCHED
        secs = [s for s in ERASE_BOTTOM if s[0] in (0x00000, 0x20000, 0x30000)]   # SA1, SA5, SA6
        return secs, 0, IMAGE_SIZE
    if scope == "softbsl_ms412":
        # The whole-program CRC lives in param2 (file 0x6050 / CPU 0x2050).
        # Erase/rewrite SA2 along with SA1 + program-high while leaving calibration untouched.
        secs = [s for s in ERASE_BOTTOM
                if s[0] in (0x00000, 0x02000, 0x20000, 0x30000)]
        return secs, 0, IMAGE_SIZE
    if scope == "sa1":
        return [(0x00000, "SA1 param1", False)], PARAM1_FILE[0], PARAM1_FILE[1]
    return list(ERASE_BOTTOM), 0, IMAGE_SIZE


def _softbsl_prog_ok(cpu, *, include_program_low=False):
    """Select install bytes while excluding calibration and unrelated boot/application blocks."""
    return cpu < (0x4000 if include_program_low else 0x2000) or cpu >= 0x20000


def _scope_prog_ok(scope, cpu):
    """True when a CPU address belongs to the non-contiguous selected scope."""
    if scope in ("softbsl", "softbsl_ms412"):
        return _softbsl_prog_ok(cpu, include_program_low=(scope == "softbsl_ms412"))
    if scope == "program_checked":
        return 0x02000 <= cpu < 0x04000 or cpu >= 0x20000
    return True


def _effective_flash_scope(scope, image):
    """Include param2 whenever a full-image program checksum is being deployed."""
    if scope == "program" and len(image) == IMAGE_SIZE:
        return "program_checked"
    return scope


_SCOPE_LABEL = {
    "full": "full bottom half (SA0/SA2/SA3/SA4/SA5/SA6)",
    "program": "program-only (SA5+SA6; cal/SA1 untouched)",
    "program_checked": "checksum-aware program-only (SA2 CRC + SA5/SA6; cal/SA1 untouched)",
    "tune": "tune-only (SA4 cal)",
    "sa1": "SA1/param1 boot sector ONLY (BRICK-CLASS; cal/program untouched)",
    "softbsl": "soft-BSL install: SA1 (driver + 0x5A loader) + SA5/SA6 (0x2A door); CAL/boot/params UNTOUCHED (BRICK-CLASS: writes SA1)",
    "softbsl_ms412": "checksum-aware soft-BSL install: SA1 + SA2 program checksum + SA5/SA6; CAL UNTOUCHED (BRICK-CLASS: writes SA1)",
}


def _scope_label(scope, chip=None, half="lower"):
    if chip == "28f200":
        return {
            "full": "full Intel 28F200 image",
            "program": "28F main-E program block (128K; calibration/boot untouched)",
            "program_checked": "28F param2 checksum + main-E program block (calibration/boot untouched)",
            "tune": "28F main-D calibration block (96K erase)",
            "sa1": "28F boot/param1 block (8K; BRICK-CLASS)",
            "softbsl": "Soft-BSL install: 28F boot/param1 8K + main-E 128K",
            "softbsl_ms412": "Checksum-aware Soft-BSL install: 28F boot/param1 8K + param2 8K + main-E 128K",
        }[scope]
    if half == "upper":
        return {
            "full": "full 29F400 TOP half (SA8/SA9/SA10 + fused SA7)",
            "program": "TOP program-only (SA9+SA10; calibration/fused SA7 untouched)",
            "program_checked": "TOP checksum-aware program write (requires fused SA7 handling)",
            "tune": "TOP tune-only (SA8 calibration)",
            "sa1": "TOP fused SA7 identity/boot sector (64K; BRICK-CLASS)",
            "softbsl": "TOP Soft-BSL install scope (unsupported; use full TOP write)",
            "softbsl_ms412": "TOP checksum-aware Soft-BSL install scope (unsupported; use full TOP write)",
        }[scope]
    return _SCOPE_LABEL[scope]


def flash_dry_run(image, *, scope="full", write_bootloader=False, chip=None, log=_emit):
    """Print exactly what flash_image WOULD erase/program/verify - NO serial I/O."""
    requested_scope = scope
    scope = _effective_flash_scope(scope, image)
    if scope != requested_scope:
        log("  program checksum is enabled: extending program-only write to param2/SA2")
    if scope in ("sa1", "softbsl", "softbsl_ms412"):
        write_bootloader = True
    target = image_marker(image)
    half = "upper" if target == "T" and chip != "28f200" else "lower"
    sectors, prog_lo, prog_hi = _flash_scope(scope, half=half, chip=chip)
    log(f"=== DRY-RUN flash plan  (scope: {_scope_label(scope, chip, half)})  - NO ECU contact ===")
    log("  ERASE:")
    for addr, name, prot in sectors:
        if prot and not write_bootloader:
            log(f"    (skip {name} @0x{addr:05X} - protected)")
        else:
            log(f"    erase {name} @ DS2 0x{addr:05X}")
    prog = ffskip = p1skip = tot = 0
    for f in range(prog_lo, prog_hi, CHUNK_SIZE):
        if not _scope_prog_ok(scope, f ^ DESCR):
            continue
        if not write_bootloader and (
                (half == "upper" and f < 0x10000)
                or (half != "upper" and PARAM1_FILE[0] <= f < PARAM1_FILE[1])):
            p1skip += 1
            continue
        blk = image[f:f + CHUNK_SIZE]
        if not blk or blk == b"\xFF" * len(blk):
            ffskip += 1
            continue
        prog += 1
        tot += len(blk)
    log(f"  PROGRAM file 0x{prog_lo:05X}-0x{prog_hi:05X}: {prog} non-FF chunks ({tot} B); "
        f"{ffskip} FF-skipped, {p1skip} param1-skipped")
    log(f"  VERIFY: CRC read-back each programmed 0x{CHUNK_SIZE:X}-B chunk")
    log("  (nothing sent; drop --dry-run + add --port/--yes to execute)")
    return prog


def crossbank_dry_run(image, log=_emit):
    """Print the CROSS-BANK top-half write plan (SA7 written LAST) - NO serial I/O. See flash_cross_bank."""
    m = image_marker(image)
    sectors, _lo, _hi = _flash_scope("full", half="upper")       # ERASE_UPPER: SA8, SA9, SA10, SA7
    order = [s for s in sectors if not s[2]] + [s for s in sectors if s[2]]   # non-boot first, SA7 last
    log("=== CROSS-BANK top-half write PLAN (29F400 golden bank) - NO ECU contact ===")
    log(f"  image bank marker: {m!r}  (must be 'T' for the top golden image)")
    log("  precondition: agent ENTERED FROM BOTTOM so it remains the intact recovery bank.")
    log("  1) [OPERATOR] flip A17 -> UPPER (agent resident in RAM = safe)")
    log("     -> the tool then VERIFIES the flash marker @0x1FFC actually CHANGED before erasing anything:")
    log("        a forgotten flip is caught here (view unchanged) = ABORT, ZERO damage (nothing erased).")
    log("  2) arm bootloader ('W'), then erase+program the top, SA7 (fused boot) LAST:")
    tot = 0
    for addr, name, prot in order:
        lo, hi = addr, addr + 0x10000
        n = sum(1 for f in range(lo, hi, CHUNK_SIZE)
                if image[f:f + CHUNK_SIZE] and image[f:f + CHUNK_SIZE] != b"\xFF" * len(image[f:f + CHUNK_SIZE]))
        tot += n
        tag = "   [BRICK-CLASS: fused vectors/bootloader/param2, LAST]" if prot else ""
        log(f"     {name:16s} erase @DS2 0x{addr:05X}  +  program file 0x{lo:05X}-0x{hi:05X}  ({n} non-FF chunks){tag}")
    log(f"     total {tot} non-FF chunks (all-FF incl. the SA7 bus-hole 0x8000-0xBFFF are skipped)")
    log("  3) read-back verify (agent 'K' at the UPPER view)")
    log("  4) [OPERATOR] flip A17 -> LOWER  ->  reset")
    log("  SAFETY: booted from the intact BOTTOM throughout -> a failed top flash is recoverable (flip A17")
    log("          back to LOWER, boot the bottom, retry). LIVE run is BRICK-CLASS -- bench-prove first.")
    return


class SoftBSLError(Exception):
    pass


def _door_patch_ids(version):
    try:
        return _DOOR_PATCH_IDS[version]
    except KeyError:
        raise SoftBSLError(f"no Soft-BSL command-door patches for {version!r}") from None


class InstallCancelled(SoftBSLError):
    """The operator stopped a persistent install before Phase 2 erase."""

    def __init__(self, message, *, phase="pre_phase2"):
        self.phase = str(phase)
        super().__init__(message)


class D2XXRequiredError(SoftBSLError):
    """A fast baud tier was requested on a transport that is not D2XX."""


class _EraseBoundaryTracker:
    """Forward progress while remembering whether the first erase was issued."""

    def __init__(self, downstream=None):
        self.downstream = downstream
        self.destructive_started = False

    def __call__(self, done, total, label=""):
        # flash_image calls this immediately before the erase opcode.  If the
        # UI callback itself fails, no erase was sent and fallback remains safe.
        if self.downstream is not None:
            self.downstream(done, total, label)
        if label == "erase":
            self.destructive_started = True


@dataclass
class _RetainedInstallFlash:
    """Phase-2 RAM-agent state retained after the first erase boundary."""

    port: str
    ds2: object
    agent: object
    args: object
    image: bytes
    transfer_baud: str
    chip: object
    error: Exception

    @property
    def is_open(self):
        return bool(getattr(self.ds2, "is_open", False))

    def close_after_confirmed_power_cycle(self):
        self.ds2.close()


class _RetainedInstallFlashRequired(SoftBSLError):
    """The installer target write failed after erase and remains resumable."""

    def __init__(self, recovery):
        self.recovery = recovery
        super().__init__(
            f"{recovery.error}. FLASH INCOMPLETE - DO NOT TURN IGNITION OFF; "
            "the installer RAM-agent session is still open"
        )


@dataclass
class InstallRecovery:
    """Resumable persistent-install state wrapping its retained live transport."""

    request: object
    target: bytes
    flash_over: dict
    phase: str
    retained: object

    @property
    def port(self):
        return str(self.request.port)

    @property
    def is_open(self):
        return bool(getattr(self.retained, "is_open", False))

    def close_after_confirmed_power_cycle(self):
        self.retained.close_after_confirmed_power_cycle()


class InstallRecoveryRequired(SoftBSLError):
    """A persistent install failed post-erase and its transport was retained."""

    def __init__(self, recovery):
        self.recovery = recovery
        phase = "temporary bootstrap" if recovery.phase == "bootstrap" else "persistent target"
        cause = getattr(recovery.retained, "error", "write failed")
        super().__init__(
            f"{cause}. SOFT-BSL INSTALL INCOMPLETE during the {phase} write - "
            "DO NOT TURN IGNITION OFF; the same live recovery session is still open"
        )


# ── helpers ───────────────────────────────────────────────────────────────────
def load_agent(path):
    with open(path) as f:
        agent = bytes.fromhex("".join(f.read().split()))
    if not (0 < len(agent) <= 0x800):       # must fit SRAM 0xD800..0xDFFF (below BUF@0xE000)
        raise SoftBSLError(f"agent {len(agent)} B out of range (expected 1..2048)")
    return agent


def load_stage_payload():
    """Load and integrity-check the production two-stage bootstrap payload."""
    root = os.path.dirname(os.path.abspath(__file__))
    payload_path = os.path.join(root, "stage1_payload.hex")
    manifest_path = os.path.join(root, "stage1_manifest.json")
    try:
        payload = load_agent(payload_path)
        with open(manifest_path, encoding="utf-8") as handle:
            manifest = json.load(handle)
    except (OSError, ValueError, SoftBSLError) as exc:
        raise SoftBSLError(f"staged-entry payload is unavailable: {exc}") from exc
    expected_size = int(manifest.get("payload_size", -1))
    expected_sha = str(manifest.get("payload_sha256", ""))
    if len(payload) != expected_size or hashlib.sha256(payload).hexdigest() != expected_sha:
        raise SoftBSLError("staged-entry payload failed its packaged manifest integrity check")
    if manifest.get("status") != "production-staged-entry-v1":
        raise SoftBSLError("staged-entry payload manifest is not production-enabled")
    return payload


def image_marker(image):
    """Return 'T'/'B' from the image's bank-ID marker, or None if absent/invalid."""
    if len(image) < MARKER_OFF + 4:
        return None
    if image[MARKER_OFF] != 0xA5 or image[MARKER_OFF + 1] != 0x5A:
        return None
    half = image[MARKER_OFF + 2]
    if (half ^ 0xFF) != image[MARKER_OFF + 3]:
        return None
    return {0x54: "T", 0x42: "B"}.get(half)


# ── the soft-BSL session ──────────────────────────────────────────────────────
class SoftBSL:
    def __init__(self, ds2, log=_emit):
        self.ds2 = ds2
        self.log = log

    # -- raw comms: write + discard our own half-duplex echo, then read the reply --
    def _ser(self):
        return self.ds2._ser

    def _txs(self, data):
        ser = self._ser()
        ser.reset_input_buffer()
        ser.write(data)
        ser.flush()
        # echo arrives as the bytes go out (half-duplex); allow TX time + margin.
        tmo = 2.0 + len(data) * 12 / max(self.ds2.baud, 1)
        self.ds2._read_exact(len(data), tmo)        # consume + discard the echo

    def _tx(self, byte):
        self._txs(bytes([byte]))

    def _rx(self, timeout=2.0):
        b = self.ds2._read_exact(1, timeout)
        if not b:
            raise SoftBSLError("timeout waiting for agent byte")
        return b[0]

    @staticmethod
    def _addr3(a):
        return bytes([(a >> 16) & 0xFF, (a >> 8) & 0xFF, a & 0xFF])

    # -- entry: DS2 unlock -> trigger upload -> jump (stays 8E2 throughout) --
    def _ds2_unlock(self):
        # Mirror the PROVEN write-mode setup (the stock DS2 write flow):
        # 0xA2 prepare -> read 0x2001 -> 0x0D status -> shared 0x90 seed/key.
        # Soft-BSL bootstrap still enters through the stock authorization
        # handler, so it must not add an E659 gate or override the shared
        # challenge/attempt policy.
        d = self.ds2
        self.log("DS2: prepare (0xA2) -> read 0x2001 -> status (0x0D) -> shared seed/key (0x90) ...")
        d._prepare()
        d.read_mem(0x2001, 12)
        d.status()
        d.unlock_write()

    def _trigger_frame(self, agent, trigger):
        """Build (frame, crc) for the chosen trigger. crc = CRC16 over the agent (init 0xFFFF,
        MUST match the stub's crc16_check)."""
        d = self.ds2
        crc = checksum._crc(agent, 0xFFFF)
        ln = len(agent).to_bytes(2, "big")
        if trigger == "43":
            # CURRENT: the program-region call-slot splice @0x27354. DS2 cmd 0x43 (clear-adapts)
            # with the MAGIC selector 0x9C9C. Frame: 12 0A 43 9C 9C <lenHi><lenLo> <crcHi><crcLo> <xor>.
            # Stub reads sel@E423:E424, agentlen@E425:E426, crc16@E427:E428 (frame buffer @0xE420).
            body = bytes([d.ecu_addr, 0x0A, 0x43, MAGIC_HI, MAGIC_LO]) + ln + crc.to_bytes(2, "big")
        elif trigger == "5a":
            # The persistent boot/param1 loader (after install; the temporary 0x43 hook is gone).
            # DS2 cmd 0x5A + selector 0x9C9C -> SA1 dispatcher default @0x15A0 ->
            # descriptor-safe relocated loader main @CPU 0x1D92.
            # Same frame shape as 43: 12 0A 5A 9C 9C <lenHi><lenLo> <crcHi><crcLo> <xor>. CLEAN LOCKED
            # (NO unlock). Mirrors the proven trigger_sa1.py.
            body = bytes([d.ecu_addr, 0x0A, 0x5A, MAGIC_HI, MAGIC_LO]) + ln + crc.to_bytes(2, "big")
        elif trigger == "9c":
            # LEGACY: the param1 stub. DS2 cmd 0x9C "SBSL"<len><crc>.
            args = b"SBSL" + ln + crc.to_bytes(2, "big")
            body = bytes([d.ecu_addr, 4 + len(args), 0x9C]) + args
        else:
            raise SoftBSLError(f"unknown trigger {trigger!r} (want '43', '5a', or '9c')")
        return body + bytes([_xor(body)]), crc

    def _stream_and_confirm(self, agent):
        # Stream the agent into SRAM 0xD800, check the stub's CRC ACK, then the 0xA5 jump banner.
        # The agent reinit'd ASC0 to the SAME 8E2 framing the firmware uses for DS2, so the host
        # stays 8E2 the whole session - no parity switch, banner is a clean jump confirmation.
        self.log(f"streaming agent ({len(agent)} B) into SRAM 0xD800 ...")
        self._txs(agent)
        a = self._rx(3.0)
        if a == CRC_FAIL:
            raise SoftBSLError("stub reports agent CRC FAIL (corrupt upload)")
        if a != ACK:
            raise SoftBSLError(f"no CRC-OK ACK after stream - got 0x{a:02X}")
        b = self.ds2._read_exact(1, 0.5)
        if not b:
            raise SoftBSLError("no agent banner after jump (agent did not start)")
        if b[0] != BANNER:
            raise SoftBSLError(f"bad agent banner 0x{b[0]:02X} (expected 0xA5)")
        self.log("agent running (8E2); banner 0xA5 received.")

    @staticmethod
    def _stage_header(tier, agent):
        """Build the isolated stage-one header (sent at inherited 9600 baud)."""
        try:
            s0bg, _rate = BG[tier]
        except KeyError as exc:
            raise SoftBSLError(f"unknown staged baud tier {tier!r}") from exc
        crc = checksum._crc(agent, 0xFFFF)
        raw = (b"S2" + bytes([s0bg]) + len(agent).to_bytes(2, "big") +
               crc.to_bytes(2, "big"))
        return raw + bytes([_xor(raw)])

    def _retune_staged_host(self, tier):
        """Retune only the host after stage one changes the ECU's ASC0 divisor."""
        _s0bg, rate = BG[tier]
        self._ser().baudrate = rate
        self.ds2.baud = rate
        self._ser().reset_input_buffer()
        time.sleep(0.003)

    def enter_staged(self, agent, tier, trigger="5a", stage_payload=None,
                     ack_timeout=2.0):
        """Enter the unchanged production agent through the qualified two-stage loader.

        The small bootstrap is accepted by the persistent SA1 loader at 9600, then relocates
        itself to the independently verified SRAM tail.  It receives the unchanged production
        agent at the requested exact tier and only after its CRC ACK does control reach the
        ordinary ``0xA5`` agent banner.  No erase/program command is available in stage one.
        """
        if tier not in BG:
            raise SoftBSLError(f"unknown staged baud tier {tier!r}")
        if stage_payload is None:
            stage_payload = load_stage_payload()
        self.staged_entry = False
        frame, _crc = self._trigger_frame(stage_payload, trigger)
        self.log(f"staged {trigger} entry "
                 f"(stage={len(stage_payload)} B, tier={tier}, agent={len(agent)} B) ...")
        try:
            self._txs(frame)
            if self._rx(ack_timeout) != ACK:
                raise SoftBSLError("staged loader trigger rejected")
            self._txs(stage_payload)
            if self._rx(3.0) != ACK:
                raise SoftBSLError("persistent loader rejected the staged payload CRC")
            if self._rx(1.0) != STAGE_READY:
                raise SoftBSLError("stage one did not announce its 0x5B ready byte")

            # Stage one ACKs its compact header at the inherited baud, then changes ASC0 itself.
            self._txs(self._stage_header(tier, agent))
            if self._rx(ack_timeout) != ACK:
                raise SoftBSLError(f"stage-one {tier} header was rejected")
            self._retune_staged_host(tier)
            self._tx(STAGE_HANDSHAKE)
            if self._rx(0.75) != ACK:
                raise SoftBSLError(f"stage-one {tier} baud handshake failed")
            self._stream_and_confirm(agent)
            self.staged_entry = True
            self.log(f"staged entry complete at {BG[tier][1]} baud")
        except Exception:
            # Stage failure is pre-erase and its own failure path returns to stock DS2. Keep the
            # host ready for the next lower-tier attempt; never issue a production reset here.
            try:
                self._ser().baudrate = 9600
                self.ds2.baud = 9600
                self._ser().reset_input_buffer()
            except Exception:
                pass
            raise

    def enter(self, agent, trigger="43", ack_timeout=2.0):
        """Fire the trigger -> stream + jump. trigger='43' = the program-region 0x43/9C9C
        call-slot splice, sent in LOCKED mode (NO unlock: the splice is only reachable while
        0xF732 != 0x55; a seed/key unlock routes 0x43 to the write-mode dispatcher that bypasses
        it). trigger='9c' = the legacy param1 stub, which DOES require the seed/key unlock."""
        if trigger == "9c":
            self._ds2_unlock()
        frame, crc = self._trigger_frame(agent, trigger)
        label = {"43": "0x43/9C9C clear-adapts magic", "5a": "0x5A/9C9C SA1-bootloader",
                 "9c": "0x9C 'SBSL'"}.get(trigger, trigger)
        self.log(f"{label} trigger (len={len(agent)}, crc=0x{crc:04X}) ...")
        self._txs(frame)
        a = self._rx(ack_timeout)
        if a != ACK:
            raise SoftBSLError(
                f"{label} trigger rejected (phase/magic/auth) - got 0x{a:02X}, want ACK 0x06")
        self._stream_and_confirm(agent)


    # -- agent protocol --
    def identify(self):
        self._tx(I)
        return chr(self._rx())                      # 'T' / 'B' (marker half byte)

    def switched(self):
        self._tx(S)
        return chr(self._rx())                      # re-identify after a cockpit-switch flip

    def set_baud(self, tier):
        s0bg, rate = BG[tier]
        time.sleep(_SETTLE)
        self._txs(bytes([B, s0bg]))                 # single-frame (see program())
        if self._rx() != ACK:                       # agent ACKs at the OLD baud, then switches
            raise SoftBSLError("baud-set NAK")
        time.sleep(0.005)                           # let the agent's asc0_set_bg finish its S0R
        #                                             stop/reload/restart + RX flush before we retune
        self._ser().baudrate = rate
        self.ds2.baud = rate                        # keep echo timing in sync
        self._ser().reset_input_buffer()            # drop the ack echo + transition noise at the NEW rate
        time.sleep(0.003)                           # both ends quiet before the first fast frame
        self.log(f"baud -> {rate} (S0BG={s0bg})")

    def arm_bootloader(self):
        time.sleep(_SETTLE)
        self._txs(bytes([W]) + b"SBSL")             # single-frame (see program())
        if self._rx() != ACK:
            raise SoftBSLError("bootloader arm refused")

    def erase(self, addr):
        time.sleep(_SETTLE)
        a3 = self._addr3(addr)
        ck = sum(a3) & 0xFF                          # addr checksum (agent c_erase verifies it, so a
        self._txs(bytes([E]) + a3 + bytes([ck]))    # flipped erase addr is rejected, not mis-erased)
        try:
            st = self._rx(timeout=30.0)             # 1 ok / 2 fail / 3 deny / 4 addr-cksum. 30 s (was 20):
        except SoftBSLError:                        #   the Intel 28F main-block erase is ~18 s worst-case
            return 0                                #   (+WDT-service+protocol) -> a 20 s cap could time out
        #                                             MID-erase and a retry mid-erase desyncs the 1-threaded
        #                                             agent. AMD 29F erase is ~1-2 s so this never waits long.
        if st not in (1, 2, 3, 4):                  # residual-echo insurance (see program())
            extra = self.ds2._read_exact(1, 0.5)
            if extra:
                st = extra[0]
        return st

    def program(self, addr, data):
        if not (0 < len(data) <= 0xFF):
            raise SoftBSLError(f"program block {len(data)} B out of range (1..255)")
        # SINGLE-FRAME send (root-cause fix, confirmed by the wire log + analysis): the old
        # per-segment _tx/_txs each ran ser.reset_input_buffer(), which raced the in-flight K-line
        # echo and clipped a byte -> the status read landed on a data byte (e.g. 0x32). One
        # continuous frame (the proven agent-stream pattern) has a single flush on a settled line.
        time.sleep(_SETTLE)
        a3 = self._addr3(addr)
        # checksum covers addr+len+data (matches the agent's rx_addr_len_data) so a flipped
        # ADDRESS byte is caught (-> status 4) instead of silently programming the wrong sector.
        ck = (sum(a3) + len(data) + sum(data)) & 0xFF
        frame = bytes([P]) + a3 + bytes([len(data)]) + data + bytes([ck])
        self._txs(frame)
        st = self._rx(timeout=5.0)                  # 1 ok / 2 fail / 3 deny / 4 checksum
        if st not in (1, 2, 3, 4):                  # residual-echo insurance: an out-of-range value
            extra = self.ds2._read_exact(1, 0.5)    #   is a skew artifact; the real status follows
            if extra:
                st = extra[0]
        return st

    def program_chunk(self, addr, data):
        """Program a contiguous span as ONE CRC16-checked 'C' frame - the loader-style reliable
        path (one half-duplex turnaround per ~1KB vs per 128B, where the flips happen). Pads to
        exactly CHUNK_SIZE with 0xFF; the agent always receives CHUNK_SIZE and the v5 FF-transparent
        RMW makes the pad bytes a no-op. Returns 1 ok / 2 program-fail / 3 policy-deny / 4 CRC."""
        if not (0 < len(data) <= CHUNK_SIZE):
            raise SoftBSLError(f"chunk {len(data)} B out of range (1..{CHUNK_SIZE})")
        if len(data) < CHUNK_SIZE:
            data = data + b"\xFF" * (CHUNK_SIZE - len(data))     # FF-pad to exactly CHUNK_SIZE
        time.sleep(_SETTLE)
        a3 = self._addr3(addr)
        ck = checksum._crc(a3 + data, 0xFFFF)                    # CRC covers ADDRESS+data (brick-fix):
        #   the agent folds a2,a1,a0 then the 1024 data bytes, so a flipped address byte fails the CRC
        #   -> status 4 -> nothing programmed (was data-only = a flipped addr silently mis-targeted)
        frame = bytes([CHK]) + a3 + data + ck.to_bytes(2, "big")   # crc big-endian
        self._txs(frame)
        # A status-read timeout (dropped status byte, or a desync the agent's bounded rx recovered
        # from) is RETRYABLE - the agent is back at main; return sentinel 0 so flash_image re-sends.
        try:
            st = self._rx(timeout=8.0)                           # 1 ok / 2 fail / 3 deny / 4 CRC
        except SoftBSLError:
            return 0
        if st not in (1, 2, 3, 4):                               # residual-echo insurance (see program())
            extra = self.ds2._read_exact(1, 0.5)
            if extra:
                st = extra[0]
        return st

    def read_back(self, addr, n):
        time.sleep(_SETTLE)
        self._txs(bytes([V]) + self._addr3(addr) + bytes([n]))   # single-frame (see program())
        return self.ds2._read_exact(n, timeout=2.0 + n * 12 / max(self.ds2.baud, 1))

    def confirmed_read(self, addr, n, tries=5):
        """FALLBACK reliable read for the no-CRC 'V' opcode: read until TWO CONSECUTIVE reads agree.
        A marginal-K-line flip is random (won't repeat identically), so two matching reads = high
        confidence; agent + host run at the SAME exact baud so there's no systematic error to fool it.
        Superseded by crc_read ('K', in-agent CRC) for read/dump/verify; kept for the plain 'V' path and
        as a belt-and-suspenders fallback. Raises after `tries`."""
        prev = self.read_back(addr, n)
        for _ in range(tries - 1):
            cur = self.read_back(addr, n)
            if cur == prev:
                return cur
            prev = cur
        raise SoftBSLError(f"read @0x{addr:05X} did not stabilize after {tries} tries "
                           f"(link too noisy at this baud - drop --baud or check the wiring)")

    def crc_read(self, addr, n, tries=PROG_RETRIES):
        """CRC-checked read of n bytes (1..CHUNK_SIZE) via the agent 'K' opcode - the read counterpart of
        program_chunk. The agent reads n flash bytes (page-walking DPP0), folds the address + data through
        the SAME crc16 as the write path (init 0xFFFF), and returns data followed by CRC16 (big-endian).
        The host recomputes the CRC over the address it SENT + the data received; a mismatch (a marginal
        K-line flip at high baud) triggers a re-request. This is the in-agent-CRC read: integrity WITHOUT
        confirmed_read's double-read. Raises after `tries` failed reads.

        Contiguous CPU read: the agent walks DPP0 across 16 KB CPU pages, so a raw-CPU caller (cmd_read)
        may span pages freely. A FILE-order caller (cmd_dump) must keep each call within one 16 KB file
        block so the f^DESCR mapping stays linear (see cmd_dump)."""
        if not (0 < n <= CHUNK_SIZE):
            raise SoftBSLError(f"crc_read {n} B out of range (1..{CHUNK_SIZE})")
        a3 = self._addr3(addr)
        for _t in range(tries):
            time.sleep(_SETTLE)
            self._txs(bytes([CRD]) + a3 + n.to_bytes(2, "big"))          # single-frame (see program())
            payload = self.ds2._read_exact(n + 2, timeout=2.0 + (n + 2) * 12 / max(self.ds2.baud, 1))
            plen = len(payload) if payload else 0
            if payload and plen == n + 2:
                data = bytes(payload[:n])
                rx_crc = (payload[n] << 8) | payload[n + 1]              # CRC16 big-endian
                calc = checksum._crc(a3 + data, 0xFFFF)
                if calc == rx_crc:
                    return data
            self._ser().reset_input_buffer()                             # resync before the retry
        raise SoftBSLError(f"crc_read @0x{addr:05X} n={n} failed after {tries} tries "
                           f"(link too noisy - drop --baud or check the wiring)")

    def read_range(self, lo, length, *, chunk=CHUNK_SIZE, progress_cb=None,
                  raw_hole=False, descramble=True, log_fn=None):
        """CRC-verified read of [lo, lo+length). Two addressing modes (kept separate from the
        CLI `dump`/`read` commands — this is the GUI-facing entry point):
          descramble=True  (default): `lo` is a FILE offset; each 16 KB block is XOR-0x4000
              descrambled to its CPU address, and the unmapped bus hole is synthesized as FF.
              Use for a full-image dump (a saved 256 KB .bin is file-order).
          descramble=False: `lo` is a RAW CPU/DS2 address, read contiguously (like `cmd_read`).
              Use for the 24 KB tune partition @0x10000, which is DS2-order (== ds2.read_partial /
              ms41.TUNE_DS2_BASE), NOT the file-order erase sector.
        progress_cb(done, total) is called once before each chunk and once more at completion."""
        hi = lo + length
        out, f, holed = bytearray(), lo, 0
        while f < hi:
            if progress_cb:
                progress_cb(f - lo, hi - lo)
            n = min(chunk, hi - f, 0x4000 - (f & 0x3FFF))     # keep each call within one 16 KB page
            cpu = (f ^ DESCR) if descramble else f
            if descramble and _in_hole(cpu) and not raw_hole:
                out += b"\xFF" * n            # unmapped bus -> synthesize the true blank FF
                holed += n
            else:
                out += self.crc_read(cpu, n)  # CRC-verified read (in-agent CRC16)
            f += n
        if progress_cb:
            progress_cb(hi - lo, hi - lo)
        if holed and log_fn:
            log_fn(f"  0xFF-filled {holed} B unmapped hole (CPU 0xC000-0xFFFF; "
                   f"floating bus, NOT flash)")
        return bytes(out)

    def write_tune_partial(self, partial, *, do_verify=True, progress_cb=None):
        """Write the 24 KB calibration/tune PARTITION to the CURRENTLY-VISIBLE bank — the agent
        counterpart of ds2.write_partial, for a Fast (soft-BSL) tune write. Erase the cal block
        @CPU 0x10000, then program the 24 KB contiguously to CPU 0x10000 (partial[i] -> CPU
        0x10000+i, FF-skipping all-FF chunks exactly as the factory tool skips the trailing tune
        FF), then read-back verify. NO full image + NO bank marker: a partial writes only the cal
        block of the running bank, which is what a tune update is (flash_image's marker/half-select
        is a full-image install concern). Byte-identical to flash_image(scope='tune') on a full
        image (both place partial[i] at CPU 0x10000+i); the erase is lossless because the rest of
        the cal block is blank on every MS41 variant (see TUNE_CPU_BASE)."""
        if len(partial) != TUNE_PARTIAL_SIZE:
            raise SoftBSLError(f"tune partial is {len(partial)} B, expected {TUNE_PARTIAL_SIZE}")
        base = TUNE_CPU_BASE

        # --- erase the cal block (29F: SA4 64K / 28F200: main-D 96K; agent erases the HW block) ---
        # Production uses this exact boundary to disable automatic reset/baud fallback.
        # It fires immediately before the destructive erase opcode.
        if progress_cb:
            progress_cb(0, TUNE_PARTIAL_SIZE, "erase")
        self.log(f"erase cal block @ DS2 0x{base:05X} ...")
        st = self.erase(base)
        etries = 0
        while st in (2, 4) and etries < PROG_RETRIES:      # transient fail(2)/addr-cksum(4) only; NOT 0
            etries += 1
            self.log(f"  erase retry {etries}/{PROG_RETRIES} (status {st})")
            st = self.erase(base)
        if st != 1:
            hint = ""
            if st == DENY:
                hint = " = unexpected policy-deny (calibration is writable on either visible half)"
            raise SoftBSLError(f"cal erase failed (status {st}{hint})")
        # --- program 24 KB contiguously @CPU 0x10000, FF-skipping (mirrors write_partial) ---
        nprog = 0; t0 = time.time()
        for off in range(0, TUNE_PARTIAL_SIZE, CHUNK_SIZE):    # CHUNK_SIZE (1024) divides 0x10000-alignment
            if progress_cb:                                    #  so no 1 KB chunk crosses a 16 KB page
                progress_cb(off, TUNE_PARTIAL_SIZE, "program")
            blk = partial[off:off + CHUNK_SIZE]
            if not blk or blk == b"\xFF" * len(blk):
                continue                                       # erased flash already reads FF
            cpu = base + off
            st = self.program_chunk(cpu, blk)
            tries = 0
            while st in (0, 2, 4) and tries < PROG_RETRIES:    # transient (timeout/fail/CRC); re-send (idempotent)
                tries += 1
                self.log(f"  retry {tries}/{PROG_RETRIES} chunk @CPU 0x{cpu:05X} (status {st})")
                st = self.program_chunk(cpu, blk)
            if st != 1:
                raise SoftBSLError(f"tune chunk @CPU 0x{cpu:05X} failed (status {st}"
                                   f"{' = policy-deny' if st == DENY else ''}) after {tries} retries")
            nprog += 1
        if progress_cb:
            progress_cb(TUNE_PARTIAL_SIZE, TUNE_PARTIAL_SIZE, "program")
        dt = time.time() - t0
        self.log(f"tune programmed: {nprog} chunks in {dt:.1f}s.")

        # --- read-back verify (CRC-read each programmed 1 KB chunk, compare) ---
        if do_verify:
            self.log("tune verify (read-back) ...")
            bad = 0
            for off in range(0, TUNE_PARTIAL_SIZE, CHUNK_SIZE):
                if progress_cb:
                    progress_cb(off, TUNE_PARTIAL_SIZE, "verify")
                blk = partial[off:off + CHUNK_SIZE]
                if not blk or blk == b"\xFF" * len(blk):
                    continue
                back = self.crc_read(base + off, len(blk))
                if back != blk:
                    bad += 1
                    self.log(f"  MISMATCH @CPU 0x{base + off:05X}")
                    if bad >= 5:
                        break
            if progress_cb and not bad:
                progress_cb(TUNE_PARTIAL_SIZE, TUNE_PARTIAL_SIZE, "verify")
            if bad:
                raise SoftBSLError(f"tune verify failed ({bad}+ mismatched blocks)")
            self.log("tune verify OK.")

    def reset(self):
        # MAGIC-GATED marker-0 finalize + reset ('R' 9C 9C). The RAM agent commits E740=0
        # through the stock EEPROM routine, arms the minimum watchdog as a hardware fallback,
        # and executes protected SRST. A complete stock
        # restore can therefore remove every persistent Soft-BSL hook/loader safely.
        # Keep five copies for marginal-link resilience, but send them as one contiguous burst:
        # a rejected/corrupt copy leaves the parser ready to find the next R 9C 9C; the first valid
        # copy resets immediately, without requiring the agent to survive five host turnarounds.
        self._txs(bytes([R, 0x9C, 0x9C]) * 5)
        self.log("marker-0 finalize/SRST sent (R 9C 9C x5 contiguous; ECU reboots NORMAL).")

    def finalize_marker0(self, already_sent=False):
        """Commit marker 0 with this RAM agent and confirm the reboot over stock DS2.

        ``already_sent`` is used after ``flash_image(do_verify=True)``, whose successful
        verify already calls :meth:`reset`. The stock VERIFY fallback needs no Soft-BSL
        hook and therefore also works after a complete return-to-stock boot/program write.
        """
        if not already_sent:
            self.reset()
        try:
            self._ser().baudrate = 9600
            self.ds2.baud = 9600
            self._ser().reset_input_buffer()
        except Exception:
            pass
        marker_args = (0xE740).to_bytes(4, "big") + b"\x01"
        # SRST normally returns stock DS2 in about 0.20 s. Poll with a short
        # first-byte timeout instead of imposing a fixed watchdog wait.
        for _ in range(20):
            try:
                if self.ds2.execute(
                        ds2.DS2Commands.READ_MEM, marker_args, timeout=0.05) == b"\x00":
                    self.log("marker-0 finalize confirmed; ECU rebooted into the application.")
                    return True
            except Exception:
                pass
            time.sleep(0.02)
        self.log("marker 0 was not confirmed after reset; trying stock program VERIFY ...")
        try:
            ok, _status = self.ds2.verify_program_region(
                log_fn=lambda message, *_args: self.log(message))
            if ok:
                return True
        except Exception:
            pass
        self.log("WARNING: marker-0 finalization could not be confirmed; inspect E740 before retrying.")
        return False

    def ensure_flash_mode(self, wait=1.2, *, poll_ready=False, ready_guard=0.6,
                          ready_timeout=2.5, poll_timeout=0.05, poll_gap=0.02):
        """Guarantee the ECU is in flash mode (E740=1) with a FRESH clean-locked 5a window, entirely in
        software (no physical key-cycle). If E740==1 already -> no-op (return False). If NORMAL (0x03) or
        unknown -> send the DS2 0x2A door_magic (sets E740=1 + WDT-reboots -> opens the window), wait for
        the reboot, and confirm E740=1 (return True)."""
        # The bank marker is intentionally revision-neutral, so distinguish the one known-bad
        # relocation by its exact loader bytes while stock DS2 is still alive.  This check is
        # read-only and occurs before the 0x2A reset: a detected v1 ECU stays drivable and can be
        # upgraded by the installer's disposable 0x43 bootstrap path.
        try:
            loader_crc = self.ds2.read_mem(
                _RELOCATED_V1_CRC_CPU, len(_RELOCATED_V1_CRC))
        except Exception:
            loader_crc = None                       # a reboot-window hiccup must not mask entry
        if loader_crc == _RELOCATED_V1_CRC:
            raise SoftBSLError(
                "installed Soft-BSL is the deprecated non-triggering relocated v1 "
                "(0x5A ACKs but stalls after the RAM-agent upload). No 0x2A reset was sent. "
                "Use Soft-BSL > Install Soft-BSL with this corrected build to upgrade it "
                "through the disposable 0x43 bootstrap; do not retry a fast read/write first.")
        try:
            e = self.ds2.read_mem(0xE740, 1)[0]
        except Exception:
            e = None
        if e == 0x01:
            return False                                   # already flash mode; window handled by enter_retry
        etxt = f"0x{e:02X}" if e is not None else "?? (read hiccup mid-reboot; treating as not-flash)"
        self.log(f"E740={etxt} (not flash mode) -> DS2 0x2A door to enter flash mode + open the 5a window ...")
        try:
            self.ds2.send_no_response(0x2A)           # ECU resets; no reply is possible
        except Exception:
            pass
        if poll_ready:
            # A raw 0x5A frame sent while the CPU/UART is rebooting can leave residual address
            # bytes on K-Line. Poll the harmless stock READ_MEM command instead; the existing
            # path already proves that reading E740 immediately before 0x5A preserves the clean
            # loader window. A valid E740=1 response proves the reboot and DS2 parser are ready.
            marker_args = (0xE740).to_bytes(4, "big") + b"\x01"
            # The installed v1 door intentionally spins until its inherited watchdog expires.
            # Keep K-Line quiet through that reset interval; live tests showed that hammering
            # either raw 0x5A or DS2 READ_MEM from t=0 prevents a clean loader-window catch.
            time.sleep(ready_guard)
            deadline = time.perf_counter() + ready_timeout
            attempts = 0
            while time.perf_counter() < deadline:
                attempts += 1
                try:
                    if self.ds2.execute(
                            ds2.DS2Commands.READ_MEM, marker_args,
                            timeout=poll_timeout) == b"\x01":
                        self.log(f"  in flash mode (E740=0x01) after {attempts} readiness polls; "
                                 "catching the 5a window ...")
                        self._ser().reset_input_buffer()
                        return True
                except Exception:
                    pass
                time.sleep(poll_gap)
            raise SoftBSLError(
                f"0x2A did not return stock DS2 with E740=0x01 within {ready_timeout:.2f}s")
        time.sleep(wait)                               # compatibility path; production may use bounded polling
        self._ser().reset_input_buffer()
        try:
            e2 = self.ds2.read_mem(0xE740, 1)[0]
            if e2 != 0x01:
                raise SoftBSLError(
                    f"0x2A did not enter flash mode (E740=0x{e2:02X}) - is the 0x2A door flashed?")
            self.log("  in flash mode (E740=0x01); catching the 5a window ...")
        except SoftBSLError:
            raise
        except Exception:
            pass                                       # read hiccup mid-reboot; enter_retry will settle it
        self._ser().reset_input_buffer()
        return True

    def calguard_direct_entry_ready(self):
        """True when the installed CalGuard gate is holding a mismatch in flash-listen."""
        try:
            if self.ds2.read_mem(0xE740, 1) == b"\x01":
                return False
            if not _live_patch_applied(self.ds2, "cal_guard"):
                return False
            cal_v, prog_v, broad_consistent = _detect_ecu_variant(
                self.ds2, accept_credit=False)
            cal_id, program_id, _family, exact_consistent = (
                _detect_firmware_compatibility(self.ds2)
            )
        except Exception as error:
            self.log(f"CalGuard direct-entry preflight unavailable ({error}); using normal entry.")
            return False
        if broad_consistent and exact_consistent:
            return False
        self.log(
            "CalGuard mismatch listener detected "
            f"(cal={cal_v or 'unknown'}/{cal_id or 'unknown'}, "
            f"program={prog_v or 'unknown'}/{program_id or 'unknown'}, E740!=1); "
            "entering Soft-BSL directly without 0x2A.")
        return True

    def enter_retry(self, agent, trigger="5a", tries=14, gap=0.12, ack_timeout=2.0):
        """enter(), but hammer the trigger to CATCH the brief post-reboot clean-locked window (a single
        enter can hit the ECU mid-reboot or a stale window). Used by flash/dump for hands-off entry."""
        last = None
        for attempt in range(tries):
            try:
                self._ser().reset_input_buffer()
                self.enter(agent, trigger=trigger, ack_timeout=ack_timeout)
                if attempt:
                    self.log(f"  entered on attempt {attempt + 1}")
                return
            except SoftBSLError as e:
                last = e
                time.sleep(gap)
        raise SoftBSLError(f"could not enter via '{trigger}' - window not caught after {tries} tries "
                           f"(last: {last}). Key-cycle + retry, or check the door is flashed.")

    # -- high level: select the right half, then flash one image --
    def select_half(self, target, prompt, chip=None):
        cur = self.identify()
        if cur not in HALF_NAME:
            raise SoftBSLError(f"identify returned 0x{ord(cur):02X} - agent not responding cleanly")
        if chip == "28f200":
            self.log(f"image marker: {cur!r} (working image; Intel 28F200)")
        else:
            self.log(f"visible half: {cur!r} ({HALF_NAME[cur]})")
        if cur != target:
            prompt(f"  Flip the cockpit switch to the {target!r} ({HALF_NAME[target]}) half, "
                   f"then press Enter... ")
            cur = self.switched()
            self.log(f"after flip: {cur!r}")
            if cur != target:
                raise SoftBSLError(f"switch reads {cur!r}, expected {target!r} - aborting (no writes done)")
        return cur

    def flash_image(self, image, *, scope="full", write_bootloader=False,
                    baud="high", do_verify=True, prompt=input, assume_half=None,
                    progress_cb=None, chip=None, baud_is_set=False):
        """Flash a checksum-CORRECTED image to the half it is marked for.

        The host does NOT compute image checksums - run checksum.py / build_softbsl_image.py
        first. Blank (all-FF) blocks and SA1/param1 are skipped (param1 unless armed).
        assume_half ('B'/'T') skips the marker-based cockpit-switch identify — used by the
        bootstrap install, where the just-installed ECU's param1 has no marker yet (the agent
        reports a blank/FF marker as bottom for half detection).
        """
        requested_scope = scope
        scope = _effective_flash_scope(scope, image)
        if scope != requested_scope:
            self.log("program checksum is enabled: extending program-only write to param2/SA2")
        target = image_marker(image)
        if target is None:
            # A stock/plain image (e.g. flashing a soft-BSL ECU back to stock) carries no bank marker.
            # That is NOT a showstopper: assume BOTTOM because no bank-selection evidence exists.
            # select_half() below still confirms the ECU's LIVE visible half is bottom before any erase.
            self.log("WARNING: image has no bank-ID marker @0x5FFC (e.g. a plain stock image) -- assuming "
                     "BOTTOM/working bank. On a dual-bank ECU, make sure the cockpit switch is on bottom.")
            target = "B"
        if assume_half:
            if assume_half != target:
                raise SoftBSLError(f"image marker {target!r} != assumed half {assume_half!r}")
            self.log(f"install: assuming visible half = {assume_half!r} (param1 marker may be blank; "
                     f"agent treats FF as bottom). Skipping the cockpit-switch identify.")
        else:
            self.select_half(target, prompt, chip=chip)

        if baud != "low" and not baud_is_set:
            self.set_baud(baud)
        if write_bootloader or scope in ("sa1", "softbsl", "softbsl_ms412"):
            write_bootloader = True                      # SA1/param1 scope needs the 'W' arm
            self.log("ARMING bootloader writes (SA1/param1) - opt-in")
            self.arm_bootloader()

        # scope: full / program (SA5+SA6) / tune (SA4) / sa1 (param1). Erasing a sector clears the
        # whole 64K, so we reprogram the whole scope range; FF blocks are skipped so the blank tail
        # stays cheap (FIX(FIE-1): whole-sector reprogram, not just the used head).
        half = "upper" if target == "T" and chip != "28f200" else "lower"
        sectors, prog_lo, prog_hi = _flash_scope(scope, half=half, chip=chip)

        # --- erase ---
        t_erase = time.time()
        erase_started = False
        for addr, name, protected in sectors:
            if protected and not write_bootloader:
                continue
            if not erase_started:
                # Production uses this exact boundary to disable automatic reset/baud fallback.
                # It fires immediately before the first destructive erase opcode.
                if progress_cb:
                    progress_cb(0, prog_hi - prog_lo, "erase")
                erase_started = True
            self.log(f"erase {name} @ DS2 0x{addr:05X} ...")
            st = self.erase(addr)
            etries = 0
            # Retry ONLY the transient 2 (fail) / 4 (addr-cksum) statuses -- NEVER 0 (host 30 s timeout):
            # re-issuing an 'E' frame while the single-threaded agent is still mid-erase desyncs it. A 0 is
            # treated as a hard failure so the user re-runs from a known state (erase is idempotent).
            while st in (2, 4) and etries < PROG_RETRIES:
                etries += 1
                self.log(f"  erase retry {etries}/{PROG_RETRIES} {name} (status {st})")
                st = self.erase(addr)
            if st != 1:
                raise SoftBSLError(f"erase {name} failed (status {st}"
                                   f"{' = policy-deny' if st == DENY else ''})")
        self.log(f"erase done ({time.time() - t_erase:.1f}s).")

        # --- program in 1024B CRC16 chunks (skip all-FF + param1) ---
        nprog = 0; prog_bytes = 0; t_prog = time.time()
        bar = None if progress_cb else _progress_bar("program")
        for f in range(prog_lo, prog_hi, CHUNK_SIZE):
            if bar:
                bar(f - prog_lo, prog_hi - prog_lo)           # advance over skipped FF too -> reaches 100%
            if progress_cb:
                progress_cb(f - prog_lo, prog_hi - prog_lo, "program")
            if not _scope_prog_ok(scope, f ^ DESCR):
                continue
            if not write_bootloader and (
                    (half == "upper" and f < 0x10000)
                    or (half != "upper" and PARAM1_FILE[0] <= f < PARAM1_FILE[1])):
                continue                                      # CHUNK_SIZE divides param1's 8K boundary
            blk = image[f:f + CHUNK_SIZE]
            if not blk or blk == b"\xFF" * len(blk):
                continue                                      # FF-skip (sector already erased to FF)
            cpu = f ^ DESCR
            st = self.program_chunk(cpu, blk)
            tries = 0
            # 0 (status-timeout) / 2 (program-fail) / 4 (CRC mismatch) are transient: a CRC-fail or
            # timeout programmed NOTHING (or programmed correctly + the status dropped), and AMD
            # re-program is idempotent, so re-send the chunk. 3 (policy-deny) is permanent.
            while st in (0, 2, 4) and tries < PROG_RETRIES:
                tries += 1
                if bar:
                    _tty_newline()                    # break the bar line before the retry note
                self.log(f"  retry {tries}/{PROG_RETRIES} chunk @file 0x{f:05X} (status {st})")
                st = self.program_chunk(cpu, blk)
            if st != 1:
                if bar:
                    _tty_newline()
                raise SoftBSLError(f"chunk @file 0x{f:05X} failed (status {st}"
                                   f"{' = policy-deny' if st == DENY else ''}) after {tries} retries")
            nprog += 1
            prog_bytes += len(blk)
            if not bar and (f & 0x3FFF) == 0:
                self.log(f"  programmed up to file 0x{f:05X}")
        if bar:
            bar(prog_hi - prog_lo, prog_hi - prog_lo); _tty_newline()
        if progress_cb:
            progress_cb(prog_hi - prog_lo, prog_hi - prog_lo, "program")
        dt_prog = time.time() - t_prog
        self.log(f"programmed {nprog} chunks = {prog_bytes} B in {dt_prog:.1f}s "
                 f"= {int(prog_bytes / dt_prog) if dt_prog else 0} B/s (write, baud={baud}).")

        # --- read-back verify (every programmed block) ---
        if do_verify:
            self.log("verify (read-back) ...")
            bad = 0
            t_verify = time.time()
            vbar = None if progress_cb else _progress_bar("verify")
            for f in range(prog_lo, prog_hi, CHUNK_SIZE):
                if vbar:
                    vbar(f - prog_lo, prog_hi - prog_lo)
                if progress_cb:
                    progress_cb(f - prog_lo, prog_hi - prog_lo, "verify")
                if not _scope_prog_ok(scope, f ^ DESCR):
                    continue
                if not write_bootloader and (
                        (half == "upper" and f < 0x10000)
                        or (half != "upper" and PARAM1_FILE[0] <= f < PARAM1_FILE[1])):
                    continue
                blk = image[f:f + CHUNK_SIZE]
                if not blk or blk == b"\xFF" * len(blk):
                    continue
                back = self.crc_read(f ^ DESCR, len(blk))         # CRC-verified 1 KB read-back
                if back != blk:
                    bad += 1
                    if vbar:
                        _tty_newline()
                    self.log(f"  MISMATCH @file 0x{f:05X}")
                    if bad >= 5:
                        break
            if vbar:
                if not bad:
                    vbar(prog_hi - prog_lo, prog_hi - prog_lo)   # snap to 100% only on a clean pass
                _tty_newline()                           # terminate the bar line either way
            if progress_cb and not bad:
                progress_cb(prog_hi - prog_lo, prog_hi - prog_lo, "verify")
            if bad:
                raise SoftBSLError(f"verify failed ({bad}+ mismatched blocks)")
            self.log(f"verify OK ({time.time() - t_verify:.1f}s).")
            self.reset()
        else:
            # The enclosing application/CLI recovery finalizes marker 0 and resets after this
            # method returns. This is separate from optional host read-back verification.
            self.log("Read-back verification skipped (Verify off). ECU-side finalization will be "
                     "completed by the caller.")

    def flash_cross_bank(self, image, *, baud="low", do_verify=True, prompt=input, skip_marker_guard=False,
                         guard_addr=0x1FFC):
        """CROSS-BANK top-half write (29F400 golden bank). PRECONDITION: the agent was ENTERED FROM THE
        BOTTOM so that bank stays intact as the recovery path. Sequence: arm -> [operator flips A17 to
        UPPER] -> erase+program the coarse upper map with the FUSED SA7 (boot/vectors) written LAST ->
        read-back verify -> [operator flips A17 to LOWER] -> reset. Booted from the intact BOTTOM
        throughout, so a failed top flash is recoverable (flip A17 back, boot the bottom, retry).
        BRICK-CLASS (writes SA7). The image MUST be a TOP golden image (marker 'T' @0x5FFC).

        skip_marker_guard: IDENTICAL-HALVES override. When the top is a byte-clone of the bottom (both
        banks hold the same image / same marker), the marker cannot change across a real flip, so the
        change-guard would false-abort forever. With this set the guard is bypassed and the flip is
        confirmed OUT-OF-BAND by the operator's DVM reading of the A17 line (a direct, stronger proof).
        For the ONE bootstrap write only; afterwards the top carries a distinct 'T' marker and the guard
        self-restores. Never enable this without a meter physically on A17 reading HIGH (UPPER)."""
        m = image_marker(image)
        if m != "T":
            raise SoftBSLError(f"cross-bank needs a TOP golden image (marker 'T' @0x5FFC); got {m!r}. "
                               "Compose + mark the golden-top image first.")
        crossbank_dry_run(image, self.log)                        # show the plan first
        sectors, _lo, _hi = _flash_scope("full", half="upper")    # ERASE_UPPER
        order = [s for s in sectors if not s[2]] + [s for s in sectors if s[2]]   # SA7 LAST
        SEC = 0x10000                                             # every top sector is a uniform 64K
        if baud != "low" and not getattr(self, "staged_entry", False):
            self.set_baud(baud)
        self.log("ARMING bootloader writes (the fused SA7 boot sector needs the 'W' arm)")
        self.arm_bootloader()
        # SAFETY GATE: A17 is a HARDWARE switch we can't read. If the operator forgets to flip it, these
        # "top" writes would hit the BOTTOM (erase SA1 bootloader + SA4/SA5/SA6 = corrupt the working bank).
        # A 'K' read of a HALF-SPECIFIC byte (reads never re-latch the policy) reflects the CURRENT view --
        # verify it actually CHANGED across the flip before erasing anything. guard_addr defaults to the bank
        # marker @0x1FFC; when the two banks share a marker but differ elsewhere (e.g. one has the door_magic
        # splice, the other is stock), point it at that differing byte for a REAL flip-proof instead.
        pre = self.crc_read(guard_addr, 4)                       # guard byte at the BOTTOM view
        prompt("\n  >>> FLIP THE A17 COCKPIT SWITCH TO **UPPER** NOW, then press Enter."
               "\n      (agent is in RAM = safe; the tool will NOT re-identify, so its cached 'bottom' "
               "policy keeps writes allowed.) ")
        post = self.crc_read(guard_addr, 4)                      # view AFTER the (claimed) flip
        if post == pre:
            if not skip_marker_guard:
                raise SoftBSLError(f"A17 did NOT change -- the flip-guard byte @0x{guard_addr:05X} still reads "
                                   f"{pre.hex()} (the BOTTOM view). You are STILL on the bottom half; NOTHING "
                                   "was erased. Flip A17 -> UPPER and re-run. (This guard stops a forgotten "
                                   "flip from erasing/overwriting the working bottom bank.)")
            # IDENTICAL-HALVES override: both banks read the SAME at guard_addr, so the guard can't confirm a
            # flip and would false-abort. Bypassed; the flip is proven OUT-OF-BAND by the operator's DVM (A17
            # measured HIGH). Prefer a differing guard_addr over this whenever the halves differ somewhere.
            self.log(f"  guard byte @0x{guard_addr:05X} UNCHANGED ({pre.hex()}) -- override [--i-metered-a17]: "
                     "guard BYPASSED; trusting the OPERATOR's metered A17=HIGH (UPPER) confirmation.")
        else:
            self.log(f"  A17 flip CONFIRMED: guard @0x{guard_addr:05X} {pre.hex()} (bottom) -> {post.hex()} (top).")
        for addr, name, prot in order:
            lo, hi = addr, addr + SEC
            self.log(f"erase {name} @ DS2 0x{addr:05X} ...")
            st = self.erase(addr); etries = 0
            while st in (2, 4) and etries < PROG_RETRIES:
                etries += 1
                self.log(f"  erase retry {etries}/{PROG_RETRIES} {name} (status {st})")
                st = self.erase(addr)
            if st != 1:
                raise SoftBSLError(f"erase {name} failed (status {st}). A17 is UPPER -- flip back to LOWER "
                                   "+ boot the bottom to recover.")
            bar = _progress_bar("prog " + name.split()[0])
            for f in range(lo, hi, CHUNK_SIZE):
                if bar:
                    bar(f - lo, SEC)
                blk = image[f:f + CHUNK_SIZE]
                if not blk or blk == b"\xFF" * len(blk):
                    continue                                      # FF (incl. the SA7 bus-hole) skipped
                cpu = f ^ DESCR
                st = self.program_chunk(cpu, blk); tries = 0
                while st in (0, 2, 4) and tries < PROG_RETRIES:
                    tries += 1
                    if bar:
                        _tty_newline()
                    self.log(f"  retry {tries}/{PROG_RETRIES} chunk @file 0x{f:05X} (status {st})")
                    st = self.program_chunk(cpu, blk)
                if st != 1:
                    if bar:
                        _tty_newline()
                    raise SoftBSLError(f"chunk @file 0x{f:05X} ({name}) failed (status {st}). A17 UPPER -- "
                                       "flip to LOWER + boot the bottom to recover.")
            if bar:
                bar(SEC, SEC); _tty_newline()
            self.log(f"  {name} programmed.")
        if do_verify:
            self.log("verify (read-back @ the UPPER view) ...")
            bad = 0
            for addr, name, prot in order:
                lo, hi = addr, addr + SEC
                # The CRC-read command is proven for 1 KB and normal full-image
                # verification already uses it. Avoid the legacy 128-byte
                # transaction size here while still comparing every byte.
                for f in range(lo, hi, CHUNK_SIZE):
                    blk = image[f:f + CHUNK_SIZE]
                    if not blk or blk == b"\xFF" * len(blk):
                        continue
                    back = self.crc_read(f ^ DESCR, len(blk))
                    if back != blk:
                        bad += 1
                        self.log(f"  MISMATCH @file 0x{f:05X} ({name})")
                        if bad >= 5:
                            break
                if bad >= 5:
                    break
            if bad:
                raise SoftBSLError(f"top verify failed ({bad}+ mismatched blocks). A17 UPPER -- flip to "
                                   "LOWER + boot the bottom; the golden top is bad, re-run.")
            self.log("verify OK -- golden top written.")
        prompt("\n  >>> FLIP THE A17 COCKPIT SWITCH BACK TO **LOWER** NOW, then press Enter. ")
        self.reset()
        self.log("cross-bank top write complete; golden top established (marker 'T'). ECU reboots.")


# ── CLI ───────────────────────────────────────────────────────────────────────
def _agent_default():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent.hex")


def agent_path_for_family(family):
    """Path to the flash agent .hex for a DRIVER FAMILY: 'intel'/'28f200' -> agent_28f.hex (Intel
    0x40/0x20+0xD0 command set + 12 V VPP bracket); everything else (incl. 'amd'/None) -> agent.hex
    (AMD 0xAA/0x55 command set). Each uses its matching command set and payload. Sending the AMD
    agent to an Intel 28F200 can't erase/program (invalid cmds + no VPP) -> it fails, doesn't brick,
    but doesn't work: pick the right one from the detected driver family."""
    name = "agent_28f.hex" if family in ("intel", "28f200") else "agent.hex"
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), name)


def _open(args, require_d2xx=False):
    if not args.port:
        raise SoftBSLError("no serial port was selected")
    ds2_factory = getattr(args, "ds2_factory", DS2Interface)
    d = ds2_factory(args.port, baud=9600, verbose=args.verbose, echo=not args.no_echo)
    try:
        d.open()
    except Exception:
        # A transport constructor/open can fail after acquiring its native
        # handle. Never strand that handle outside the caller's cleanup path.
        try:
            d.close()
        except Exception:
            pass
        raise
    if require_d2xx and not getattr(d, "uses_d2xx", False):
        d.close()
        raise D2XXRequiredError(
            f"{args.port} opened through {getattr(d, 'transport_name', None) or 'a non-D2XX transport'}")
    return d


def _session(args, require_d2xx=False):
    """Open DS2, enter soft-BSL, return (ds2, SoftBSL). Caller must close ds2.
    HANDS-OFF (auto_flash + trigger 5a): from a NORMAL/running ECU, auto-send the 0x2A door to enter
    flash mode + open the 5a window, then rapid-retry the enter to catch it — no physical key-cycle."""
    agent = load_agent(args.agent)
    d = _open(args, require_d2xx=require_d2xx)
    try:
        sb = SoftBSL(d)
        # Keep hand-built/test request objects on the legacy path unless they
        # explicitly identify the temporary 0x43 installer door.
        trigger = getattr(args, "trigger", None)
        if getattr(args, "auto_flash", False) and trigger == "5a":
            sb.ensure_flash_mode()                # 0x2A from normal -> flash mode + fresh 5a window
            sb.enter_retry(agent, trigger="5a")   # catch the post-reboot window
        elif getattr(args, "hammer_entry", False):
            sb.enter_retry(agent, trigger=trigger)  # catch a software self-reboot window
        else:
            sb.enter(agent, trigger=trigger)
    except Exception:
        # In an assignment such as ``d, sb = _session(...)``, an entry failure
        # prevents the caller from ever receiving ``d``. Close it here so the
        # D2XX handle cannot remain locked until process exit.
        try:
            d.close()
        except Exception as close_error:
            _emit(
                f"  transport cleanup after failed agent entry also failed: {close_error}",
                level="error",
            )
        raise
    return d, sb


def _baud_candidates(start):
    order = ("high", "mid", "low")
    return order[order.index(start):] if start in order else (start,)


def _progress_adapter(callback, label):
    """Normalize DS2's optional source label to the GUI's operation label."""
    if callback is None:
        return None
    return lambda done, total, _source_label=None: callback(done, total, label)


def _session_with_baud_fallback(args):
    """Enter the disposable agent and prove a baud before any erase.

    The temporary 0x43 door and persistent 0x5A door share the staged
    handshake. Installer attempts therefore use staged entry at the fast
    tiers; the known legacy 9600 entry remains the final fallback. Each
    failed tier is abandoned while flash is still untouched. Once erase
    begins, cmd_flash does not restart the brick-class operation at another
    baud.
    """
    tiers = _baud_candidates(getattr(args, "baud", "low"))
    # Hand-built/test request objects may omit a trigger; preserve their
    # legacy session behavior unless 0x43 is explicit.
    trigger = getattr(args, "trigger", None)
    d = sb = None
    index = 0
    while index < len(tiers):
        tier = tiers[index]
        staged_attempt = trigger == "43" and tier != "low"
        try:
            if staged_attempt:
                # 0x43 selects the temporary program-region loader; the staged wire
                # protocol is otherwise identical to the persistent 0x5A path.
                agent = load_agent(args.agent)
                d = _open(args, require_d2xx=True)
                sb = SoftBSL(d)
                sb.enter_staged(agent, tier, trigger=trigger)
            else:
                d, sb = _session(args, require_d2xx=tier != "low")
            if tier != "low" and not getattr(sb, "staged_entry", False):
                sb.set_baud(tier)
            # Three independent CRC-framed reads prove both directions at the
            # selected rate. CPU 0x20000 is mapped program flash on every chip.
            for offset in (0x20000, 0x20080, 0x20100):
                sb.crc_read(offset, BLOCK)
            _emit(f"  agent baud preflight: '{tier}' passed (3 CRC reads); erase is now permitted.")
            return d, sb, tier
        except Exception as error:
            _emit(f"  agent baud preflight: '{tier}' failed before erase ({error}).")
            # A staged-entry failure is pre-erase and owns its no-reset boundary.
            # Once the normal agent has started, the existing reset cleanup remains
            # appropriate for a failed CRC preflight.
            if sb is not None and not (staged_attempt and not getattr(sb, "staged_entry", False)):
                try:
                    sb.reset()
                except Exception:
                    pass
            if d is not None:
                try:
                    d.close()
                except Exception:
                    pass
            d = sb = None
            if isinstance(error, D2XXRequiredError) and "low" in tiers[index + 1:]:
                index = tiers.index("low")
                _emit("  D2XX is unavailable for the selected adapter; skipping unsupported fast "
                      "tiers and retrying at 9600 baud. Flash is still untouched.")
            else:
                index += 1
                if index < len(tiers):
                    _emit(f"  retrying agent entry at '{tiers[index]}' baud; flash is still untouched.")
            if index < len(tiers):
                time.sleep(1.5)
    raise SoftBSLError(f"agent link failed baud preflight at every tier: {', '.join(tiers)}")


def _check_image_checksums(image):
    """Require boot/cal plus program CRC whenever the image enables that ECU gate."""
    d = bytearray(image)
    boot_ok = bool(checksum.bootloader_checksum_ok(d))
    cal_ok, _n_ok, _n_tot = checksum._cal_verify(d)
    program_ok = checksum._prog_calc(d) == checksum._u16le(d, checksum._PROG_STORE)
    program_required = d[checksum.CHECKSUM_SWITCH_ADDR] != checksum.CK_DISABLED
    _ok, details = checksum.verify_checksum(d)
    if not program_required and not program_ok:
        details.append("Program checksum mismatch is ignored because its ECU gate is disabled.")
    return boot_ok and cal_ok and (program_ok or not program_required), details


def cmd_ports(args):
    ports = DS2Interface.list_ports()
    _emit("serial ports:", ", ".join(ports) if ports else "(none found)")


def cmd_ping(args):
    d, sb = _session(args)
    try:
        half = sb.identify()
        _emit(f"PING OK - agent alive, visible half = {half!r} "
              f"({HALF_NAME.get(half, 'unknown')}). No erase performed.")
        sb.reset()
    finally:
        d.close()


def cmd_identify(args):
    d, sb = _session(args)
    try:
        _emit(f"bank-ID marker (visible half): {sb.identify()!r}")
        sb.reset()
    finally:
        d.close()


def _resolve_chip(args):
    """--chip -> a chip key, or None for the default proven 29F400/AMD flow. 'auto' does NOT
    probe here (real ID autodetect = the `id` cmd, needs the agent 'D' opcode, Phase 3)."""
    chip = getattr(args, "chip", "auto")
    return None if chip in ("auto", "29f400") else chip


def _select_agent_for_chip(args, chip):
    """For a non-default chip, point --agent at the family's agent.

    AMD (29F) -> agent.hex. Intel (28F) -> agent_28f.hex (Intel
    0x40/0x20+0xD0/0x70/0xFF commands + the P2.6/P3.6 12 V VPP bracket)."""
    if chip is None:
        return
    fam = chipdefs.FAMILY[chip]
    agent_path = os.path.join(os.path.dirname(_agent_default()), fam["agent"])
    if fam["cmdset"] != "amd":
        if not os.path.exists(agent_path):
            raise SoftBSLError(f"--chip {chip}: {fam['agent']} not assembled yet. The source exists "
                     f"(agent_28f_build.asm); assemble it via Ghidra AssembleC166 -> {fam['agent']} "
                     f"(flat hex @0xD800), or use BSL-Unbricker --chip {chip} (HW BSL, 12 V).")
    args.agent = agent_path   # amd -> agent.hex ; intel -> agent_28f.hex (gated above)


def cmd_flash(args):
    image = open(args.image, "rb").read()
    if len(image) != IMAGE_SIZE:
        raise SoftBSLError(f"image is {len(image)} B, expected {IMAGE_SIZE} (256 KB)")
    scope = args.scope
    chip = _resolve_chip(args)
    _select_agent_for_chip(args, chip)
    target = image_marker(image)
    half = "upper" if target == "T" and chip != "28f200" else "lower"
    _emit(f"image: {args.image}  marker={target!r} ({HALF_NAME.get(target, '??')})  scope={scope}"
          + (f"  chip={chip}" if chip else ""))
    ck_ok, details = _check_image_checksums(image)
    for x in details:
        _emit("  ck:", x)
    if args.dry_run:
        if getattr(args, "cross_bank", False):
            crossbank_dry_run(image)
            return
        if not ck_ok:
            _emit("  (dry-run note: checksums flagged. On MS41.3 the PROGRAM checksum is unenforced "
                  "[0x605C=0xFF]; boot+cal are what matter. A real flash of an invalid image needs --force.)")
        flash_dry_run(image, scope=scope, write_bootloader=args.write_bootloader,
                      chip=chip or "29f400")
        return
    if not ck_ok and not args.force:
        raise SoftBSLError("image checksums are NOT valid - run checksum.py/build_softbsl_image.py first "
                 "(or pass --force to flash anyway).")
    if not args.yes:
        _what = ("the 29F400 GOLDEN TOP half via CROSS-BANK (SA8/SA9/SA10 + fused SA7 LAST)"
                 if getattr(args, "cross_bank", False) else _scope_label(scope, chip, half))
        if input(f"About to ERASE+PROGRAM: {_what}. Type 'yes' to proceed: ").strip() != "yes":
            raise SoftBSLError("aborted.")
        if getattr(args, "cross_bank", False) and input(
                "  Cross-bank writes the FUSED SA7 boot sector on the TOP half (brick-class; recovery = flip "
                "A17->LOWER + key-cycle, HW-BSL backstop). Keep A17 UPPER + untouched during the whole write. "
                "Type 'FLASH TOP': ").strip() != "FLASH TOP":
            raise SoftBSLError("aborted.")
        if scope == "sa1":
            if half == "upper":
                phrase = input(
                    "  TOP identity shares the complete 64K FUSED SA7 boot sector (brick-class; "
                    "recovery = select intact BOTTOM + Soft-BSL). Type 'FLASH SA7': ").strip()
                if phrase != "FLASH SA7":
                    raise SoftBSLError("aborted.")
            elif input(
                    "  SA1 is the BOOT SECTOR (brick-class; recovery = Soft-BSL when reachable, "
                    "otherwise HW BSL). Type 'FLASH SA1': ").strip() != "FLASH SA1":
                raise SoftBSLError("aborted.")
    # FIX(EP-1): --trigger 9c seed/key-unlocks and routes 0x43 to the wrong dispatcher; redirect to
    # 43. 43 (normal-mode splice) and 5a (in-flash-mode SA1 loader, proven) are both allowed.
    if args.trigger == "9c":
        args.trigger = "43"
    args.auto_flash = True          # hands-off: with --trigger 5a, auto-0x2A from normal + catch the window
    baud_preflight = getattr(args, "baud_fallback", False)
    if baud_preflight:
        d, sb, transfer_baud = _session_with_baud_fallback(args)
    else:
        d, sb = _session(args)
        transfer_baud = args.baud
    retain_on_failure = bool(getattr(args, "retain_on_failure", False))
    erase_tracker = _EraseBoundaryTracker(getattr(args, "progress_cb", None))
    retain_for_recovery = False
    try:
        try:
            if getattr(args, "cross_bank", False):
                # Top-half golden write while preserving BOTTOM as the intact recovery bank.
                # flash_cross_bank prompts A17 UPPER, writes SA7 last, then prompts LOWER.
                sb.flash_cross_bank(image, baud=args.baud, do_verify=not args.no_verify,
                                    skip_marker_guard=getattr(args, "i_metered_a17", False),
                                    guard_addr=getattr(args, "guard_addr", None) or 0x1FFC)
            else:
                sb.flash_image(image, scope=scope, write_bootloader=args.write_bootloader,
                               baud=transfer_baud, do_verify=not args.no_verify, assume_half=args.assume_half,
                               progress_cb=erase_tracker,
                               chip=chip or "29f400",
                               baud_is_set=baud_preflight and transfer_baud != "low")
        except Exception as flash_err:
            if getattr(args, "cross_bank", False):
                # CROSS-BANK recovery is the OPPOSITE of the bottom path: A17 is still UPPER, so an
                # auto-reset would boot the half-written top = dead. The intact BOTTOM is the safety net.
                _emit(f"\n** CROSS-BANK TOP FLASH FAILED: {flash_err}")
                _emit("   >>> FLIP A17 BACK TO **LOWER** NOW, then key-cycle -> the ECU boots the INTACT")
                _emit("       BOTTOM bank (untouched by this write). Re-probe, then retry the cross-bank.")
                _emit("       (NOT auto-resetting -- a reset with A17 still UPPER would boot the half-written top.)")
                try:
                    d.close()
                except Exception:
                    pass
                raise SoftBSLError(f"cross-bank aborted: {flash_err}")
            if retain_on_failure and erase_tracker.destructive_started:
                # Phase 2 writes the boot/parameter region.  Once its first
                # erase begins, the only safe automatic action is to preserve
                # this exact RAM agent and host handle for an in-place retry.
                retain_for_recovery = True
                recovery = _RetainedInstallFlash(
                    port=str(args.port),
                    ds2=d,
                    agent=sb,
                    args=copy.copy(args),
                    image=bytes(image),
                    transfer_baud=transfer_baud,
                    chip=chip,
                    error=flash_err,
                )
                _emit(
                    "FLASH INCOMPLETE: erase began, so the installer is preserving "
                    "the live RAM agent. DO NOT TURN IGNITION OFF."
                )
                raise _RetainedInstallFlashRequired(recovery) from flash_err
            # A flash or verify failure can leave the agent running and stuck at high
            # baud -- the crc_read read-back verify runs at the HIGH baud (187500) EVEN under --baud low, so a mid-verify
            # glitch (e.g. a noisy K-line: 'crc_read ... link too noisy') strands the agent there. A 9600
            # reset is garbage to it -> DS2 goes silent. Reset at BOTH bauds (hammered) so the ECU reboots
            # to a known state, then confirm it's responsive -- instead of leaving it stranded-high.
            _emit(f"\n** FLASH FAILED: {flash_err}")
            _emit("   resetting the agent across bauds (187500/192000 then 9600) so the ECU isn't stranded-high ...")
            for b in (HI_BAUD, 192000, 9600):   # reset at the new high (187500) AND old (192000) AND low
                try:
                    sb._ser().baudrate = b; sb.ds2.baud = b
                    time.sleep(0.2); sb._ser().reset_input_buffer()
                    sb.reset(); time.sleep(0.3); sb.reset()      # 'R' 9C9C x5, twice, at this baud
                except Exception:
                    pass
            try:
                sb._ser().baudrate = 9600; sb.ds2.baud = 9600
                time.sleep(2.0); sb._ser().reset_input_buffer()
            except Exception:
                pass
            alive = None
            for _ in range(10):
                try:
                    alive = sb.ds2.identify().hex()[:22]; break
                except Exception:
                    time.sleep(0.5)
            if alive:
                _emit(f"   ECU RESPONSIVE again (identify={alive}) -- rebooted to a known state. "
                      "Re-check E740/markers + re-flash if the write was incomplete.")
            else:
                _emit("   ECU still silent -- KEY-CYCLE + re-probe; if truly dead, use BSL-Unbricker (HW BSL backstop).")
            raise SoftBSLError(f"flash aborted (agent reset at both bauds): {flash_err}")
        _emit("FLASH COMPLETE.")
        # The verified flash path already sent R; verify-off needs R here. Either way the SAME
        # running RAM agent commits marker 0 before reboot — no 0x2A/0x5A re-entry is required.
        if getattr(args, "reset_recover", False) or not getattr(args, "stay_flash", False):
            setattr(args, "_recovered",
                    sb.finalize_marker0(already_sent=not args.no_verify))
        else:
            _emit("  --stay-flash: leaving E740=1 (flash mode); run `recover-phase` to return to normal.")
    finally:
        if not retain_for_recovery:
            d.close()


def _resume_retained_install_flash(recovery, progress_cb=None):
    """Re-run installer Phase 2 through its existing RAM-agent session."""
    if not isinstance(recovery, _RetainedInstallFlash):
        raise TypeError("recovery must be a retained installer flash")
    if not recovery.is_open:
        raise SoftBSLError("the retained installer RAM-agent session is closed")

    args = copy.copy(recovery.args)
    sb = recovery.agent
    tracker = _EraseBoundaryTracker(progress_cb)
    try:
        # Re-prove the current live link without reopening COM, re-entering the
        # door, or changing baud.  A failed retry still retains the same handle.
        for offset in (0x20000, 0x20080, 0x20100):
            sb.crc_read(offset, BLOCK)
        _emit(
            f"  retained agent baud preflight: '{recovery.transfer_baud}' passed "
            "(3 CRC reads); retry erase is now permitted."
        )
        sb.flash_image(
            recovery.image,
            scope=args.scope,
            write_bootloader=args.write_bootloader,
            baud=recovery.transfer_baud,
            do_verify=not args.no_verify,
            assume_half=args.assume_half,
            progress_cb=tracker,
            chip=recovery.chip or "29f400",
            # The retained agent is already running at this exact tier.
            baud_is_set=recovery.transfer_baud != "low",
        )
    except Exception as error:
        recovery.error = error
        _emit(
            "Installer recovery retry did not complete. Keep ignition ON; "
            "the RAM-agent session remains open."
        )
        raise _RetainedInstallFlashRequired(recovery) from error

    try:
        recovered = sb.finalize_marker0(already_sent=not args.no_verify)
        setattr(args, "_recovered", recovered)
    finally:
        # flash_image has completed and issued its terminal reset when verify is
        # enabled.  The RAM-agent recovery window no longer exists after this point.
        recovery.ds2.close()
    return bool(recovered)




def cmd_reset(args):
    d, sb = _session(args)
    try:
        sb.reset()
    finally:
        d.close()


# ── folded-in subcommands ──────────────────────────────────────────────────────
def cmd_id(args):
    """Show the flash-IC profile for --chip (region map / supply / agent). Live mfr+device-ID
    autodetect over the agent needs a 'D' opcode (Phase 3); if --port is given, also reports the
    bank-ID marker via a soft-BSL session."""
    chip = getattr(args, "chip", "auto")
    keys = ["29f400", "29f200", "28f200"] if chip == "auto" else [chip]
    if chip == "auto":
        _emit("chip: auto (default flow = 29f400/AMD; live mfr/device autodetect = Phase 3)")
    for k in keys:
        f = chipdefs.FAMILY[k]
        _emit(f"  {k}: {f['label']}  [cmdset={f['cmdset']}, 12V={'yes' if f['vpp12'] else 'no'}, agent={f['agent']}]")
    if args.port:
        d = _open(args)      # clean stock-DS2 READ-ONLY: chip + firmware markers, NO agent trigger, NO reset
        try:
            det_fam, det_sig = _detect_flash_chip(d)       # driver family (stock DS2 has no silicon read-ID)
            _emit(f"  flash-IC (SA1 driver sig @DS2 0x023C = {det_sig.hex() or 'unreadable'}): "
                  f"{_DRV_FAMILY_LABEL.get(det_fam, 'UNKNOWN -- matched neither AMD (e00e0d58) nor Intel (e6f45000)')}")
            cal_v, prog_v, consistent = _detect_ecu_variant(d)
            _emit(f"  firmware markers: cal={cal_v}  program={prog_v}  consistent={consistent}")
            _emit("  (bank-ID marker 'T'/'B' needs the agent running -> use the `identify` command for that)")
        finally:
            d.close()


def _parse_range(s, default=None):
    """Unified range syntax (hex or decimal, 0x ok), returns (start, length):
        START:END  -> an end-exclusive span; length = END - START  (END must exceed START)
        START+LEN  -> a start plus a byte length; length = LEN      (LEN must be positive)
    Both `read` and `dump` share this parser, so their range arguments read identically.
    Falls back to `default` (itself a (start, length) tuple) when s is None."""
    if not s:
        return default
    _EX = "use START:END or START+LEN (hex ok), e.g. 0x2000:0x2100 or 0x2000+0x100"
    sep = "+" if "+" in s else (":" if ":" in s else "")
    a, _, b = s.partition(sep) if sep else (s, "", "")
    if not sep or not a or not b:
        raise SoftBSLError(f"bad range {s!r}: {_EX}")
    try:
        start, second = int(a, 0), int(b, 0)
    except ValueError:
        raise SoftBSLError(f"bad range {s!r}: values must be numeric -- {_EX}")
    if start < 0:
        raise SoftBSLError(f"bad range {s!r}: START must be non-negative")
    if sep == "+":
        length = second
        if length <= 0:
            raise SoftBSLError(f"bad range {s!r}: LEN in START+LEN must be positive")
    else:  # START:END
        length = second - start
        if length <= 0:
            raise SoftBSLError(f"bad range {s!r}: END must exceed START in START:END "
                     f"(start=0x{start:X}, end=0x{second:X}). For a byte length, {_EX}.")
    return start, length


def cmd_read(args):
    """Read bytes from a RAW DS2/CPU address via the AGENT at --baud (range = START:END or START+LEN,
    hex ok). The address is a raw CPU/linear address -- NO file descramble, unlike `dump`. The agent's
    clean single baud-set is the point - `--baud high` reads at 187500."""
    addr, n = _parse_range(args.range)              # (start, length); the parser guarantees length > 0
    d, sb = _session(args)
    rchunk = CHUNK_SIZE                          # full 1 KB per 'K' at every baud
    if any(_in_hole(a) for a in (addr, addr + n - 1)):     # raw CPU addr: no ^DESCR here (cmd_read is raw)
        sb.log(f"  WARNING: 0x{addr:05X}:0x{n:X} touches the unmapped bus hole (CPU 0xC000-0xFFFF); "
               f"those bytes are a floating-bus address-ramp, NOT flash (true content 0xFF).")
    bar = None
    try:
        if args.baud != "low":
            sb.set_baud(args.baud)
        out = bytearray()
        if n > CHUNK_SIZE:                                     # bar only earns its keep on bulk reads
            bar = _progress_bar("read")
        while len(out) < n:                                    # raw CPU addr: agent page-walks, no align
            if bar:
                bar(len(out), n)
            out += sb.crc_read(addr + len(out), min(rchunk, n - len(out)))
        if bar:
            bar(n, n); _tty_newline()
        bar = None                                            # closed cleanly -> skip the finally newline
        _emit(f"0x{addr:05X}: {bytes(out).hex()}")
    finally:
        if bar:                 # left open by a mid-transfer crc_read error -> terminate the bar line
            _tty_newline()
        try:
            sb.finalize_marker0()  # no post-reset hook/recovery-agent dependency
        except Exception:
            pass
        d.close()


def cmd_dump(args):
    """Dump flash to FILE via the AGENT at --baud (range = START:END or START+LEN file offsets, same
    syntax as `read`; default = the whole bottom half 0:0x40000). Offsets are FILE offsets (descrambled
    per 16 KB block), NOT raw CPU addresses like `read`. At `--baud high` the agent runs a CLEAN single
    S0BG change = fast. Reads are CRC16-VERIFIED end to end (the agent 'K'
    opcode returns data + CRC over addr+data), so a dump is reliable at any baud; a bad chunk is re-sent."""
    lo, length = _parse_range(args.range, default=(0, IMAGE_SIZE))     # (start, length); parser ensures length > 0
    hi = lo + length
    if hi > IMAGE_SIZE:
        raise SoftBSLError(f"dump range 0x{lo:X}..0x{hi:X} exceeds the {IMAGE_SIZE // 1024} KB image "
                 f"(max file offset 0x{IMAGE_SIZE:X}); trim the range.")
    args.auto_flash = True          # hands-off: with --trigger 5a, auto-0x2A from normal + catch the window
    d, sb = _session(args)
    rchunk = CHUNK_SIZE                          # full 1 KB per 'K' at every baud
    raw_hole = getattr(args, "raw_hole", False)
    bar = None
    try:
        if args.baud != "low":
            sb.set_baud(args.baud)
        out, f, holed = bytearray(), lo, 0
        bar = _progress_bar("dump")
        while f < hi:
            if bar:
                bar(f - lo, hi - lo)
            # <=rchunk per 'K' call, never crossing a 16 KB file block (keeps f^DESCR linear -> the agent's
            # contiguous CPU read == a contiguous file span). 1024 divides 16384, so a chunk stays whole.
            n = min(rchunk, hi - f, 0x4000 - (f & 0x3FFF))
            cpu = f ^ DESCR
            if _in_hole(cpu) and not raw_hole:
                out += b"\xFF" * n            # unmapped bus -> synthesize the true blank FF (see HOLE_CPU)
                holed += n
            else:
                out += sb.crc_read(cpu, n)             # CRC-verified file-order read (in-agent CRC16)
            f += n
            if not bar and (f & 0x3FFF) == 0:
                sb.log(f"  read up to file 0x{f:05X}")
        if bar:
            bar(hi - lo, hi - lo); _tty_newline()
        bar = None                                            # closed cleanly -> skip the finally newline
        open(args.out, "wb").write(out)
        if holed:
            sb.log(f"  0xFF-filled {holed} B unmapped hole (file 0x8000-0xBFFF / CPU 0xC000-0xFFFF; "
                   f"floating bus, NOT flash{' -- pass --raw-hole to keep the raw float' if not raw_hole else ''})")
        _emit(f"dumped {len(out)} B (file/chip order, baud={args.baud}) -> {args.out}")
    finally:
        if bar:                 # left open by a mid-transfer crc_read error -> terminate the bar line
            _tty_newline()
        if not getattr(args, "stay_flash", False):
            try:
                sb.finalize_marker0()  # no post-reset hook/recovery-agent dependency
            except Exception:
                pass
        d.close()


def cmd_mode_switch(args):
    """Send a DS2 command (default 0x2A door_magic) that flips the flash phase, wait for the WDT
    reboot, reconnect, and report 0xE740 (0x03=normal / 0x01=flash-mode). DS2-only, no agent."""
    cmd = int(args.command, 0)
    d = _open(args)
    try:
        try:
            _emit("identify:", d.identify().hex()[:24])
        except Exception as e:
            raise SoftBSLError(f"no comms ({e}) - check cable / key-on")
        before = d.read_mem(0xE740, 1)[0]
        _emit(f"E740 before = 0x{before:02X}  (expect 0x03 normal)")
        if before != 0x03:
            _emit("  WARN: not a clean normal key-on; key-cycle first for a clean switch.")
        _emit(f"sending DS2 0x{cmd:02X} -> ECU should reset (no reply expected) ...")
        try:
            r = d.execute(cmd, timeout=1.5)
            _emit(f"  got a reply {r.hex()} - if the ECU did NOT reset, that door may not be flashed.")
        except DS2Error as e:
            _emit(f"  no reply / timeout (EXPECTED - ECU resetting): {e}")
    finally:
        d.close()
    _emit("waiting ~3 s for the reboot ...")
    time.sleep(3.0)
    d = _open(args)
    try:
        time.sleep(0.5)
        after = d.read_mem(0xE740, 1)[0]
        msg = ("FLASH MODE (SA1 0x5A loader live; `recover-phase` to exit)" if after == 0x01 else
               "still NORMAL (door not flashed, or cmd never reached the handler)" if after == 0x03 else
               f"0x{after:02X} unexpected")
        _emit(f"E740 after reboot = 0x{after:02X}  ->  {msg}")
    finally:
        d.close()


# 12-byte SRAM recovery agent streamed through the SA1 0x5A door. It runs the firmware's OWN
# flash-complete finalize and then WDT-resets the CPU so it reboots into the RUNNING application
# (the reboot is the whole point -- committing the marker alone leaves the CPU in the flash-listen
# dispatcher, where DTCs/live-data never start; there is no DS2-reachable software reset from there).
#   movb RL4,#N        E1 (N<<4)|8   ; N = the flash-phase marker to write to 0xE740
#   movb 0xE740,RL4    F7 F8 40 E7   ; stage the marker in the XRAM/SFR cell
#   calls 0x00,0x1A62  DA 00 62 1A   ; firmware EEPROM-commit: stores triplet (N, N+1, N+2) @0x1DD
#   jmpr  self         0D FF         ; spin -> WDT reset -> reboot; boot-vote 0x1A1C restores E740=N
# Marker 0 is the factory verified-complete state used by every production finalizer.
RECOVERY_AGENT = bytes(
    [0xE1, 0x08, 0xF7, 0xF8, 0x40, 0xE7, 0xDA, 0x00, 0x62, 0x1A, 0x0D, 0xFF]
)  # E740=0


def cmd_recover_phase(args):
    """Exit 'stuck in flash mode': stream a 12-B agent via the SA1 0x5A door that sets 0xE740=0 +
    commits EEPROM 0x1DD (the mirror of the enter-agent), then WDT-resets to NORMAL. PRE: ECU in
    flash mode (E740=1) + a clean LOCKED key-on (key-cycle, NO unlock)."""
    agent = RECOVERY_AGENT
    crc = checksum._crc(agent, 0xFFFF)
    ln = len(agent).to_bytes(2, "big")
    _emit(f"recovery agent: {len(agent)} B, CRC16=0x{crc:04X} -> E740=0 + commit -> NORMAL boot")
    if not args.yes and input("  Proceed? this WRITES the EEPROM flash-state. Type 'yes': ").strip().lower() != "yes":
        raise SoftBSLError("aborted.")
    d = _open(args)
    sb = SoftBSL(d)
    try:
        try:
            pre = d.read_mem(0xE740, 1)[0]
            _emit(f"  pre-state: 0xE740=0x{pre:02X} (0x01=flash / 0x00=clean normal)"
                  + ("  NOTE: already clean-normal - harmless no-op." if pre == 0x00 else ""))
        except Exception as e:
            _emit(f"  (pre-read skipped: {e})")
        body = bytes([d.ecu_addr, 0x0A, 0x5A, 0x9C, 0x9C]) + ln + crc.to_bytes(2, "big")
        frame = body + bytes([_xor(body)])
        _emit(f"trigger frame: {frame.hex(' ')}")
        sb._txs(frame)
        if sb._rx(2.0) != ACK:
            raise SoftBSLError("SA1 0x5A trigger rejected - not in flash phase / not clean-locked (key-cycle).")
        _emit("SA1 loader ACK -> streaming the recovery agent ...")
        sb._txs(agent)
        a = sb._rx(3.0)
        if a == CRC_FAIL:
            raise SoftBSLError("loader reports CRC FAIL - corrupt stream; nothing ran, key-cycle + retry.")
        if a != ACK:
            raise SoftBSLError(f"no CRC-OK ACK - got 0x{a:02X}")
        _emit(">>> COMMITTED: E740=0, EEPROM written, WDT reset -> NORMAL boot. Key-cycle + verify E740=0x00.")
    finally:
        d.close()


def cmd_deploy_splice(args):
    """DS2-ONLY (no soft-BSL agent) deploy of a program-region image: erase the program array +
    rewrite program-low + program-high over stock DS2. Installs a program-region splice/door image
    (e.g. the 0x43 or 0x2A door). The boot block (CPU 0x0-0x1FFF) is HW-protected + never touched.
    FINALIZES over pure stock DS2 (NO 0x5A/soft-BSL, and the CAL/TUNE is NEVER touched): the program
    erase commits the flash-phase marker to 1 (stuck-in-flash), then the stock program-integrity VERIFY
    (07/0F @0x1D07) both (a) checks the freshly-written program's CRC/signature and (b) on a full pass
    commits E740=0 (the factory 'verified-complete' normal state; 0 and 3 are functionally identical on
    the run path -- only E740==1 = flash). One operation performs finalization and the integrity gate,
    without erasing calibration. ``--no-finalize`` intentionally leaves E740=1 for recovery.
    THEN a byte-for-byte READ-BACK verify reads the deployed program back and compares to the written
    image -- the stock verify only checks ~36 fixed signature bytes, NOT our custom patches, so this is
    what actually catches an error in door_magic/cal_guard/amd_flash/0x43. --no-readback skips it."""
    img = open(args.image, "rb").read()
    if len(img) != DS2Interface.FULL_SIZE:
        raise SoftBSLError(f"image is {len(img)} B, expected {DS2Interface.FULL_SIZE} (256 KB)")
    block = DS2Interface._BLOCK
    ds2img = bytearray(len(img))                                  # inverse 16 KB block-swap -> DS2 order
    for blk in range(len(img) // block):
        s, t = blk * block, (blk ^ 1) * block
        ds2img[t:t + block] = img[s:s + block]
    _emit(f"image OK ({len(img)} B); program-region DS2 write (cal SKIPPED, tune preserved)")
    finalize = not getattr(args, "no_finalize", False)
    readback = not getattr(args, "no_readback", False)
    verify_ranges = list(getattr(args, "verify_ranges", ()) or ())
    if args.dry_run:
        note = (" (live run finalizes: program verify 07/0F @0x1D07 -> E740=0, NO CAL touch)"
                if finalize else " (--no-finalize: leaves E740=1 stuck)")
        if verify_ranges:
            note += f" + targeted read-back of {len(verify_ranges)} bootstrap patch range(s)"
        elif readback:
            note += " + byte-for-byte program read-back verify"
        _emit("dry-run: image + block-swap verified, no serial I/O." + note)
        return

    if not args.yes and input(
            "This ERASES + REWRITES the program region (cal/tune untouched). Type 'yes': ").strip() != "yes":
        raise SoftBSLError("aborted (nothing written).")

    # Stock DS2 already supports the capture-qualified direct 187500-baud
    # write. Prefer that native D2XX path for the bootstrap deployment; only
    # fall back to the legacy 9600 writer when the fast session proves the ECU
    # is still low-rate and untouched before its erase boundary. ``--no-finalize``
    # remains on the legacy path because the native program-only session always
    # performs the stock finalizer and intentionally leaves the ECU at high rate
    # until the required manual ignition cycle.
    native_fast_done = False
    if finalize:
        import ds2_native_fast_service

        probe = None
        try:
            preflight = _live_preflight(args)
            if preflight is None:
                probe = _open(args)
                uses_d2xx = bool(getattr(probe, "uses_d2xx", False))
                live_family, live_sig = _detect_flash_chip(probe)
            else:
                uses_d2xx = bool(preflight["uses_d2xx"])
                live_family = preflight["flash_family"]
                live_sig = preflight["flash_signature"]
            if not uses_d2xx:
                _emit("   native fast bootstrap skipped: active DS2 transport is not D2XX.", "warn")
            else:
                # Release the read-only DS2 probe before the native service
                # opens its own D2XX handle on the same COM port. A compose
                # install normally reuses the preflight captured before its
                # native-fast base read and therefore has no probe here.
                if probe is not None:
                    probe.close()
                    probe = None
                target_family = ecu_info.image_chip_family(img)
                if live_family is None:
                    raise SoftBSLError(
                        "native fast bootstrap requires a recognized live flash-driver family; "
                        f"signature={live_sig.hex() or '<unreadable>'}"
                    )
                if target_family != live_family:
                    raise SoftBSLError(
                        "bootstrap image/live flash-driver family mismatch: "
                        f"image={target_family or 'unknown'} live={live_family}"
                    )
                _emit(
                    f"-- native fast DS2 bootstrap ({live_family.upper()}, exact 187500 baud; tune untouched) --"
                )

                fast_result = ds2_native_fast_service.write_program_d2xx(
                    args.port,
                    img,
                    connected_family=live_family,
                    # Preserve deploy-splice's default readback contract, but
                    # perform it at high rate inside the native session. The
                    # installer supplies targeted ranges instead, so it does
                    # not incur a duplicate full-program read.
                    verify_write=bool(readback and not verify_ranges),
                    progress_cb=getattr(args, "progress_cb", None),
                )
                native_fast_done = True
                _emit(
                    "== FAST BOOTSTRAP COMPLETE == program finalized; ECU remains at "
                    "high-rate flash-listen until the required ignition cycle; tune untouched. =="
                )
                if getattr(fast_result, "verified", False):
                    _emit(
                        f"== FAST READ-BACK VERIFIED == {fast_result.verified_bytes} program bytes byte-perfect. =="
                    )
        except ds2_native_fast_service.NativeWriteRecoveryRequired:
            # The service deliberately retains the live D2XX handle after an
            # erase-time failure. Do not reopen, downshift, or cycle it.
            raise
        except ds2_native_fast_service.NativeFastPreEraseFailure as error:
            if error.reentry_not_ready or error.power_cycle_required:
                if bool(getattr(args, "phase1_reentry_recovery", False)):
                    # Preserve the structured service failure for the install-only
                    # operator recovery loop. The native service has already closed
                    # its D2XX handle; this function's finally also closes any probe
                    # before the installer invokes the GUI callback.
                    raise
                if error.power_cycle_required:
                    raise SoftBSLError(
                        f"native fast bootstrap was not started: {error}. Nothing "
                        "was erased, but the write-authorization state is ambiguous. "
                        "Turn ignition OFF, wait at least 10 seconds, turn ignition "
                        "ON, then retry"
                    ) from error
                raise SoftBSLError(
                    f"native fast bootstrap was not started: {error}. Nothing "
                    "was erased. Turn ignition OFF, wait at least 10 seconds, "
                    "turn ignition ON, then retry"
                ) from error
            if error.seed_unavailable:
                raise SoftBSLError(
                    "native fast bootstrap was not started: the ECU remained safely "
                    "locked and did not make a write seed available after the bounded "
                    "quiet retry. Nothing was erased, and the DS2 9600 fallback was not "
                    "attempted because it would repeat the same authorization request. "
                    "Turn ignition OFF, wait at least 10 seconds, turn ignition ON, then retry"
                ) from error
            if bool(getattr(args, "native_fast_retry_only", False)):
                raise SoftBSLError(
                    "native fast bootstrap retry failed before erase; the legacy "
                    f"9600 writer was not attempted: {error}"
                ) from error
            if not error.safe_legacy_fallback:
                raise SoftBSLError(
                    "native fast bootstrap failed before erase without a confirmed "
                    f"low-rate fallback: {error}"
                ) from error
            _emit(
                f"   native fast bootstrap failed before erase ({error}); "
                "restarting the complete program write at DS2 9600.",
                "warn",
            )
        finally:
            if probe is not None:
                probe.close()

    if native_fast_done:
        if getattr(fast_result, "power_cycle_required", True):
            _emit(
                "== FAST BOOTSTRAP COMPLETE == stock full-program finalization left "
                "the ECU at high rate; perform the required ignition cycle before "
                "low-rate targeted verification. =="
            )
            return
        # The native writer already ran the stock finalizer. Keep the installer’s
        # existing targeted byte check, but perform it over a fresh normal
        # low-rate DS2 handle just as the legacy path does.
        if verify_ranges:
            _emit("-- targeted bootstrap verify: reading only the new 0x43 hook/cave bytes --")
            d = _open(args)
            try:
                verify_total = sum(length for _addr, length, _label in verify_ranges)
                verify_done = 0
                external_progress = getattr(args, "progress_cb", None)
                for addr, length, label in verify_ranges:
                    range_progress = None
                    if external_progress:
                        range_progress = (
                            lambda done, _total, _tag=None, base=verify_done:
                            external_progress(base + done, verify_total, "bootstrap verify")
                        )
                    got = bytes(d.read_memory_range(
                        addr, length, chunk=0xF7, progress_cb=range_progress))
                    want = bytes(ds2img[addr:addr + length])
                    if got != want:
                        raise SoftBSLError(
                            f"bootstrap verify FAILED for {label} @DS2 0x{addr:05X}: "
                            f"read {got.hex()}, expected {want.hex()}"
                        )
                    _emit(f"   [OK] {label} @DS2 0x{addr:05X}: {length} bytes")
                    verify_done += length
                _emit("== BOOTSTRAP PATCH VERIFIED == 0x43 entry hook/cave are byte-perfect. ==")
            finally:
                d.close()
        return

    d = _open(args)
    try:
        _emit("identify:", d.identify().hex())

        def log(msg, level="info"):
            _emit(f"   {msg}")

        _emit("-- DS2 write-mode unlock --")
        d._prepare()
        d.read_mem(0x2001, 12)
        d.status()
        d.unlock_write()
        d.read_mem(0x1CF4, 3)
        _emit("   unlock OK (0xE658 -> write phase)")
        _emit("-- erase program array (DS2 0x002000; ~2.7 s settle) --")
        d._erase_sector(0x002000, log_fn=log,
                        step=ds2.PROGRAM_ERASE_STEP_DELAY, settle=ds2.POST_PROGRAM_ERASE_DELAY)
        _emit("-- write program-low (0x2000-0x5FFF) + program-high (0x20000-0x3FFFF) --")
        _external_progress = getattr(args, "progress_cb", None)
        pbar = (_progress_adapter(_external_progress, "bootstrap program")
                if _external_progress else _progress_bar("program"))
        lo_len = 0x006000 - 0x002000
        ptotal = lo_len + (d.FULL_SIZE - 0x020000)
        w1, s1 = d._write_program_sectors(ds2img, 0x002000, 0x006000, log_fn=log,
                                          progress_cb=pbar, prog_total=ptotal, prog_done=0)
        w2, s2 = d._write_program_sectors(ds2img, 0x020000, d.FULL_SIZE, log_fn=log,
                                          progress_cb=pbar, prog_total=ptotal, prog_done=lo_len)
        if pbar:
            pbar(ptotal, ptotal)
            if not _external_progress:
                _tty_newline()
        _emit(f"   program-low: {w1} blocks ({s1} FF); program-high: {w2} blocks ({s2} FF). Cal SKIPPED.")
        # The program-array erase committed E740=1 (flash mode). FINALIZE with NO 0x5A and NO CAL touch:
        # the stock program VERIFY (07/0F @0x1D07) from E740==1 runs the firmware's program CRC/signature
        # check and, on a full pass (E742==1), commits E740=0 (verified-complete, normal) -- one op does
        # both finalization and the integrity gate, and the tune is never erased. If the program is
        # blank or invalid, the verify will not commit 0 (E740
        # left at 1 or 3) -> loud warning + recover-phase.
        if finalize:
            _emit("-- finalize: program-integrity VERIFY (07/0F @0x001D07) -> E740=0 (stock DS2, NO 0x5A, NO CAL) --")
            ok, st = d.verify_program_region(log_fn=log)
            e = None
            try:
                e = d.read_mem(0xE740, 1)[0]
            except Exception:
                pass
            estr = ("0x%02X" % e) if e is not None else "??"
            ststr = ("0x%02X" % st) if st is not None else "??"
            if ok and e == 0:
                _emit("== DEPLOY COMPLETE == program VERIFIED (0x01); E740 -> 0 (NORMAL/verified); tune untouched. ==")
            elif e is not None and e != 1:
                _emit(f"== DEPLOY DONE == WARNING: program verify {ststr} (not a clean pass); E740={estr} (NORMAL). "
                      "The program write may be imperfect -- re-flash if it misbehaves.")
            else:
                _emit(f"== DEPLOY WARNING == verify did NOT finalize (status {ststr}, E740={estr}). Program likely "
                      "blank/bad + the ECU may be in FLASH mode -- run `recover-phase` and RE-FLASH.")
        else:
            _emit("== DEPLOY COMPLETE (--no-finalize) == E740 left at 1 (FLASH); a key-cycle will NOT clear"
                  " it -- run `recover-phase`, or re-run without --no-finalize.")
        if verify_ranges:
            _emit("-- targeted bootstrap verify: reading only the new 0x43 hook/cave bytes --")
            verify_total = sum(length for _addr, length, _label in verify_ranges)
            verify_done = 0
            external_progress = getattr(args, "progress_cb", None)
            for addr, length, label in verify_ranges:
                range_progress = None
                if external_progress:
                    range_progress = (
                        lambda done, _total, _tag=None, base=verify_done:
                        external_progress(base + done, verify_total, "bootstrap verify"))
                got = bytes(d.read_memory_range(
                    addr, length, chunk=0xF7, progress_cb=range_progress))
                want = bytes(ds2img[addr:addr + length])
                if got != want:
                    raise SoftBSLError(
                        f"bootstrap verify FAILED for {label} @DS2 0x{addr:05X}: "
                        f"read {got.hex()}, expected {want.hex()}")
                _emit(f"   [OK] {label} @DS2 0x{addr:05X}: {length} bytes")
                verify_done += length
            _emit("== BOOTSTRAP PATCH VERIFIED == 0x43 entry hook/cave are byte-perfect. ==")
        elif readback:
            # OUR OWN verification: the stock 07/0F verify only checks ~36 fixed signature bytes (NOT our
            # patches). Read every deployed program byte back (stock DS2, read-only) and compare to what we
            # wrote -- the only way to catch a bad byte in door_magic/cal_guard/amd_flash/0x43. ~3-4 min
            # @9600. --no-readback skips.
            _emit("-- read-back verify: comparing deployed program bytes to the written image (read-only) --")
            rok, rtot, rmis, rfb = d.verify_deployed_program(
                ds2img, log_fn=log, progress_cb=getattr(args, "progress_cb", None))
            if rok:
                _emit(f"== READ-BACK VERIFIED == {rtot} program bytes byte-perfect (custom patches included). ==")
            else:
                _emit(f"== !! READ-BACK MISMATCH == {rmis}/{rtot} bytes differ (first @DS2 0x{rfb:06X}) -- the"
                      " flash is CORRUPT; RE-FLASH before driving. ==")
    finally:
        d.close()


def _verify_install(args, target):
    """Read the key install markers back via stock DS2 and compare to the target image."""
    from engines.patcher.patch_ms41 import load_patches
    version = _patch_base_version(target)
    bootstrap_door_id, persistent_door_id = _door_patch_ids(version)
    patch_defs = load_patches()
    persistent_off = patch_defs[persistent_door_id]["cave"]["splice_off"]
    bootstrap_off = patch_defs[bootstrap_door_id]["cave"]["splice_off"]
    spots = [
        ("boot/param1 bank-ID marker @0x5FFC", 0x5FFC, 4),
        ("boot/param1 0x5A hook @0x55A0", 0x55A0, 4),
        ("boot/param1 loader CRC helper @0x5C32", 0x5C32, 4),
        ("boot/param1 loader main @0x5D92", 0x5D92, 4),
        ("boot/param1 loader I/O helper @0x5FC4", 0x5FC4, 4),
        (f"program door        @0x{persistent_off:05X}", persistent_off, 4),
        (f"program 0x43 slot   @0x{bootstrap_off:05X}", bootstrap_off, 4),
    ]
    d = _open(args)
    ok = True
    try:
        for _ in range(10):                              # wake the cold/marginal K-line
            try:
                d.read_mem(0xE740, 1)
                break
            except Exception:
                time.sleep(0.5)
        for name, foff, n in spots:
            got = d.read_mem(foff ^ DESCR, n)
            exp = target[foff:foff + n]
            good = got == exp
            ok &= good
            _emit(f"  [{'OK' if good else 'MISMATCH'}] {name}: {got.hex()} (target {exp.hex()})")
    finally:
        d.close()
    _emit("VERIFY:", "OK - boot/param1 loader + program door match the target."
          if ok else "*** MISMATCH - re-flash / investigate before trusting the boot. ***")
    return ok


# ── MS41 variant gate — mirrors Patcher/patchlib/cal_guard_gate.asm + ms41_variant.check_hybrid ──
# MS41.3 is the SS1v2/ABHISHEK community firmware (NOT a factory version); factory = MS41.2. Detection
# uses cal_guard's EXACT markers, NOT the coarse non-FF byte count: the SS1v2 cal STRING and the exact
# 0x39A9A program SIGNATURE (a non-FF count can't separate .3 from a .1 program that also fills that tail).
_CALID_VARIANT = {"12": "MS41.2", "60": "MS41.1", "41": "MS41.0", "42": "MS41.0", "59": "MS41.0", "85": "MS41.0"}
_ECUID_PROG_VARIANT = {"064": "MS41.2", "378": "MS41.1", "380": "MS41.1",
                       "298": "MS41.0", "324": "MS41.0", "293": "MS41.0", "381": "MS41.0"}
_SS1V2 = b"SS1v2"
_ABHISHEK = b"ABHISHEK"
_PROG_SIG_3 = bytes.fromhex("9a116390")          # cal_guard .3 program code signature @ file 0x39A9A

# ── FLASH-IC detection over stock DS2 (READ-ONLY) ──────────────────────────────────────────────
# A stock ECU CANNOT return the silicon mfr/device ID over DS2: RE-confirmed high-confidence that
# NEITHER resident driver dispatch (AMD @0x152E / Intel two-tier @0x27246) has a flash 0x90-autoselect
# read-identifier -- the only genuine read-ID code (read_id*.asm) is a STREAMED agent, and the stock
# captures contain zero read-ID frames. So we detect the INSTALLED FLASH DRIVER instead: the SA1 driver
# entry @ file 0x423C (= DS2 0x023C, block 0, provably readable over stock DS2) is the AMD flash driver
# on a 29F re-chip vs the stock Intel driver on a 28F. The first 8 bytes are byte-identical across
# MS41.0/.1/.2/.3 and every softbsl/door patch (nearest edit 1786 B away); all 4 leading bytes differ
# AMD-vs-Intel (Hamming 16) so no K-line bit-flip can cross them.
_DRV_SIG_ADDR  = 0x023C                                    # DS2 addr = file 0x423C ^ 0x4000 (SA1 driver entry)
_DRV_SIG_AMD   = bytes.fromhex("e00e0d58f04ec084")         # AMD driver (amd_flash) -> AMD cmd set / agent.hex
_DRV_SIG_INTEL = bytes.fromhex("e6f45000b84c6fe0")         # stock Intel driver     -> Intel cmd set / agent_28f.hex
# The signature identifies the DRIVER FAMILY, NOT the exact silicon: the same AMD driver serves BOTH the
# 29F200 and the 29F400 (bottom half), so an AMD hit means "29F200 / 29F400 bottom half" -- both use the
# AMD command set + agent.hex, so the family is all the flow needs. (Pass --chip 29f200 explicitly only if
# you need that part's specific region map.) Intel hit = 28F200.
_DRV_FAMILY_LABEL = {"amd":   "29F200 / 29F400 bottom-half (AMD driver)",
                     "intel": "28F200 (Intel driver)"}
_COMPAT_CAL_ADDR = 0x1000C
_COMPAT_PROGRAM_ADDR = 0x2007
_CODING_FAMILY_ADDR = 0x1CF4


def _detect_ecu_variant(d, *, accept_credit=True):
    """Read the cal_guard markers over stock DS2 (READ-ONLY, brick-safe) and return
    (cal_variant, prog_variant, consistent). ``accept_credit`` preserves the broader
    offline/install detector; False mirrors CalGuard runtime exactly: strict SS1v2,
    otherwise CAL-ID, with no ABHISHEK shortcut. DS2 addr = file ^ 0x4000.
    PROGRAM side: the exact 9a116390 signature @file 0x39A9A else ECU-ID[2:5] @0x6025."""
    def rd(a, n):
        for _ in range(4):
            try:
                return d.read_mem(a, n)
            except Exception:
                time.sleep(0.3)
        return b""
    ss1 = rd(0x133BB, 5)
    credit = rd(0x15F60, 8) if accept_credit else b""
    calid = rd(0x1000E, 8)
    cal_v = ("MS41.3" if (ss1 == _SS1V2 or (accept_credit and credit == _ABHISHEK))
             else _CALID_VARIANT.get(calid[:2].decode("latin1", "ignore")))
    sig = rd(0x3DA9A, 4); ecuid = rd(0x2025, 7)          # program markers (file 0x39A9A / 0x6025)
    prog_v = "MS41.3" if sig == _PROG_SIG_3 else _ECUID_PROG_VARIANT.get(ecuid[2:5].decode("latin1", "ignore"))
    consistent = cal_v is not None and cal_v == prog_v
    return cal_v, prog_v, consistent


def _detect_firmware_compatibility(d):
    """Return the live canonical program/cal IDs, coding family, and exact match state."""
    def read(address, length):
        for _ in range(4):
            try:
                return d.read_mem(address, length)
            except Exception:
                time.sleep(0.3)
        return b""

    cal_raw = read(_COMPAT_CAL_ADDR, 4)
    program_raw = read(_COMPAT_PROGRAM_ADDR, 4)
    family = read(_CODING_FAMILY_ADDR, 3)
    cal_id = cal_raw.decode("ascii") if len(cal_raw) == 4 and cal_raw.isdigit() else None
    program_id = (
        program_raw.decode("ascii")
        if len(program_raw) == 4 and program_raw.isdigit() else None
    )
    if len(family) != 3 or not family.isdigit():
        family = None
    return cal_id, program_id, family, bool(
        cal_id and program_id and cal_id == program_id
    )


def _live_patch_applied(d, patch_id):
    """Read every post-patch descriptor byte from the live ECU."""
    from engines.patcher.patch_ms41 import load_patches
    patch = load_patches()[patch_id]
    for edit in patch["edits"]:
        expected = bytes.fromhex(edit["data"])
        actual = d.read_memory_range(int(edit["off"]) ^ DESCR, len(expected))
        if actual != expected:
            return False
    return True


def _detect_flash_chip(d):
    """Read the SA1 flash-driver signature over stock DS2 (READ-ONLY, brick-safe) and infer the DRIVER
    FAMILY. Stock DS2 has NO silicon read-ID (see _DRV_SIG_* notes), so this reports which flash driver is
    installed -- NOT the exact part: "amd" (= 29F200 OR 29F400 bottom half; both share the driver + agent.hex),
    "intel" (= 28F200), or None when the signature matches neither (caller MUST fail safe -- never guess a
    command set). Returns (family_or_None, raw_bytes) so callers can log the actual bytes on a miss."""
    sig = b""
    for _ in range(4):
        try:
            sig = d.read_mem(_DRV_SIG_ADDR, len(_DRV_SIG_AMD)); break
        except Exception:
            time.sleep(0.3)
    if sig == _DRV_SIG_AMD:
        return "amd", sig
    if sig == _DRV_SIG_INTEL:
        return "intel", sig
    return None, sig


def _store_live_preflight(args, d, flash_family, flash_signature):
    """Carry one read-only live preflight through the complete install.

    Compose installs may perform a native-fast full base read.  Reopening DS2
    afterward solely to rediscover the same variant and driver family disturbs
    the post-read authorization context.  Capture all required evidence before
    that read and reuse it for the version gate and Phase-1 bootstrap.
    """

    cal_v, prog_v, broad_consistent = _detect_ecu_variant(
        d, accept_credit=False)
    cal_id, program_id, coding_family, exact_consistent = (
        _detect_firmware_compatibility(d)
    )
    evidence = {
        "port": str(getattr(args, "port", "")),
        "uses_d2xx": bool(getattr(d, "uses_d2xx", False)),
        "flash_family": flash_family,
        "flash_signature": bytes(flash_signature),
        "cal_variant": cal_v,
        "program_variant": prog_v,
        "cal_compatibility_id": cal_id,
        "program_compatibility_id": program_id,
        "coding_family": coding_family,
        "broad_consistent": bool(broad_consistent),
        "exact_consistent": bool(exact_consistent),
        "consistent": bool(broad_consistent and exact_consistent),
    }
    args._live_preflight = evidence
    return evidence


def _live_preflight(args):
    evidence = getattr(args, "_live_preflight", None)
    if not isinstance(evidence, dict):
        return None
    if evidence.get("port") != str(getattr(args, "port", "")):
        return None
    required = {
        "uses_d2xx",
        "flash_family",
        "flash_signature",
        "cal_variant",
        "program_variant",
        "cal_compatibility_id",
        "program_compatibility_id",
        "coding_family",
        "broad_consistent",
        "exact_consistent",
        "consistent",
    }
    return evidence if required <= evidence.keys() else None


# ── runtime image composition (base + patches) so the repo ships ZERO firmware .bins ──────────────
# The base program is BMW/community copyright -> not publishable. What IS publishable = our patches
# (the Patcher's JSONs) + this host + the agent. So we obtain the base at RUNTIME (read the ECU, or a
# user-supplied --base file) and compose the flashable image from base + patches via the Patcher.
def _compose_image(base_bytes, patch_ids, *, marker=None, return_log=False):
    """Compose base + patches in-process through the internal patch module.

    ``patch_ms41.build`` is deliberately pure: it validates the fingerprint,
    expect anchors, dependencies and collisions, then recomputes checksums without
    file or subprocess I/O.  Keeping this boundary in-process makes the same safety
    engine available to both the GUI service and the optional CLI wrapper.
    """
    try:
        from engines.patcher.patch_ms41 import (
            PatchError, build, is_applied, load_patches,
        )
    except ImportError as e:
        raise SoftBSLError(f"patch module could not be loaded: {e}") from e
    try:
        patches = load_patches()
        def applied_for_target(patch):
            if is_applied(base_bytes, patch):
                return True
            if marker not in ("B", "T"):
                return False
            # softbsl_loader owns the default B marker as one of its edits. A golden-TOP image
            # intentionally overrides only those four bytes to T after applying the loader, so
            # ordinary is_applied() reports a false partial. Compare every edit with the requested
            # marker substituted at the overlap instead.
            marker_bytes = bytes([0xA5, 0x5A, ord(marker), ord(marker) ^ 0xFF])
            for edit in patch["edits"]:
                off = int(edit["off"])
                expected = bytearray(bytes.fromhex(edit["data"]))
                lo = max(off, MARKER_OFF)
                hi = min(off + len(expected), MARKER_OFF + len(marker_bytes))
                if lo < hi:
                    expected[lo - off:hi - off] = marker_bytes[lo - MARKER_OFF:hi - MARKER_OFF]
                if bytes(base_bytes[off:off + len(expected)]) != bytes(expected):
                    return False
            return True

        missing = [patch_id for patch_id in patch_ids
                   if not applied_for_target(patches[patch_id])]
        if not missing and marker is None:
            result = bytes(base_bytes)
            return (result, []) if return_log else result
        if marker is None:
            image, lines = build(bytes(base_bytes), missing, patches=patches)
        else:
            # ``build`` deliberately supports an empty missing list here: an already-patched
            # image may still need its bank marker changed from B to T with boot CRC recomputed.
            image, lines = build(bytes(base_bytes), missing, patches=patches, marker=marker)
        return (image, lines) if return_log else image
    except PatchError as e:
        raise SoftBSLError(f"patch compose [{'+'.join(patch_ids)}] failed: {e}") from e


def _read_ecu_base(d, log=_emit, progress_cb=None):
    """Read the ECU's full 256 KB firmware over legacy stock DS2 at 9600.

    This remains the deterministic fallback for adapters without the native fast reader and for
    injected/test DS2 factories. The live installer prefers :func:`_read_ecu_base_fast`, which is
    still stock DS2 (no Soft-BSL agent and no writes) but uses the already-proven native D2XX baud
    escalation.
    """
    log("  reading the ECU as the base @ 9600 (stock DS2 fallback; or use --base) ...")
    bar = (_progress_adapter(progress_cb, "base read")
           if progress_cb else _progress_bar("base read"))
    base = bytes(d.read_full(progress_cb=bar, log_fn=None))
    if bar and not progress_cb:
        _tty_newline()
    if len(base) != IMAGE_SIZE:
        raise SoftBSLError(f"ECU read returned {len(base)} B, expected {IMAGE_SIZE}")
    return base


def _read_ecu_base_fast(args, log=_emit, progress_cb=None):
    """Read the current ECU image through the native fast *stock-DS2* reader.

    The native reader performs its own token/selector handshake (9600 -> exact production high
    rate), CRC-checks every block, restores the link to low rate, and returns normal file layout.
    It never enters Soft-BSL and never erases or programs flash, so it is safe before the disposable
    0x43 bootstrap is deployed. A caller may fall back to ``_read_ecu_base`` if D2XX is unavailable
    or the read-only fast session cannot complete.
    """
    from ds2_fast_read import read_full_d2xx

    log("  reading the ECU as the base via native fast stock DS2 (exact 187500 baud) ...")
    bar = (_progress_adapter(progress_cb, "base read")
           if progress_cb else _progress_bar("base read"))
    result = read_full_d2xx(
        args.port,
        progress_cb=bar,
        echo=not getattr(args, "no_echo", False),
    )
    if bar and not progress_cb:
        _tty_newline()
    base = bytes(result.file_image)
    if len(base) != IMAGE_SIZE:
        raise SoftBSLError(f"fast stock DS2 read returned {len(base)} B, expected {IMAGE_SIZE}")
    log(f"  fast stock DS2 base read complete ({len(base)} B; link returned to 9600)")
    return base


def _bootstrap_verify_ranges(patch_ids):
    """Return only patch edits that Phase 1 actually deploys via stock DS2."""
    from engines.patcher.patch_ms41 import load_patches
    patch_defs = load_patches()
    ranges = []
    for patch_id in patch_ids:
        for edit in patch_defs[patch_id]["edits"]:
            file_off = int(edit["off"])
            length = len(bytes.fromhex(edit["data"]))
            cpu_off = file_off ^ DESCR
            if ((0x02000 <= cpu_off < 0x06000) or
                    (0x20000 <= cpu_off < 0x40000)):
                ranges.append((cpu_off, length, patch_id))
    return ranges


def _patch_state(image, patch):
    """Return absent/applied/legacy/partial for one patch's exact edit bytes."""
    states = []
    for edit in patch["edits"]:
        offset = int(edit["off"])
        expected = bytes.fromhex(edit["expect"])
        applied = bytes.fromhex(edit["data"])
        current = bytes(image[offset:offset + len(applied)])
        legacy = edit.get("upgrade_expect", [])
        if isinstance(legacy, str):
            legacy = [legacy]
        legacy = [bytes.fromhex(value) for value in legacy]
        states.append("applied" if current == applied
                      else "legacy" if current in legacy
                      else "absent" if current == expected
                      else "partial")
    if states and all(state == "applied" for state in states):
        return "applied"
    if states and all(state in ("applied", "legacy") for state in states):
        return "legacy"
    if states and all(state == "absent" for state in states):
        return "absent"
    return "partial"


def _patches_overlap(left, right):
    """Return whether any byte edit in two patch descriptors overlaps."""
    for a in left.get("edits", ()):
        alo = int(a["off"])
        ahi = alo + len(bytes.fromhex(a["data"]))
        for b in right.get("edits", ()):
            blo = int(b["off"])
            bhi = blo + len(bytes.fromhex(b["data"]))
            if alo < bhi and blo < ahi:
                return True
    return False


def _temporary_bootstrap_base(base, patch_defs, door_id="door_0x43"):
    """Temporarily remove an exact applied patch that occupies the disposable 0x43 cave.

    A persistent feature patch may legitimately use the same program-high cave as the one-shot
    0x43 bootstrap. Phase 1 can displace that exact patch, and Phase 2's target is composed from
    the original base so the persistent feature is restored. Unknown/partial states are never
    treated as recoverable here; the caller will still fail closed in the normal compose path.
    """
    door = patch_defs[door_id]
    result = bytes(base)
    displaced = []
    from engines.patcher.patch_ms41 import is_applied, revert
    for patch_id, patch in patch_defs.items():
        if patch_id == door_id or not _patches_overlap(patch, door):
            continue
        if is_applied(result, patch):
            result = bytes(revert(result, patch))
            displaced.append(patch_id)
    return result, tuple(displaced)


def _door_bootstrap_conflicts_are_exact(base, patch_defs, door_id="door_0x43"):
    """Whether a partial 0x43 state is explained by an exact overlapping persistent patch."""
    door = patch_defs[door_id]
    if _patch_state(base, door) != "partial":
        return False
    from engines.patcher.patch_ms41 import is_applied
    return any(
        patch_id != door_id
        and _patches_overlap(patch, door)
        and is_applied(base, patch)
        for patch_id, patch in patch_defs.items()
    )


def _confirm_reinstall(
        args, base, patch_defs, patch_ids, bootstrap_door_id="door_0x43"):
    """Confirm a complete existing install; reject ambiguous partial patches."""
    states = {patch_id: _patch_state(base, patch_defs[patch_id])
              for patch_id in patch_ids}
    partial = [patch_id for patch_id, state in states.items()
               if state == "partial"
               and not (patch_id == bootstrap_door_id
                        and _door_bootstrap_conflicts_are_exact(
                            base, patch_defs, bootstrap_door_id))]
    if partial:
        raise SoftBSLError(
            "partial/inconsistent patch state detected for "
            + ", ".join(partial)
            + "; refusing to treat this as a safe reinstall")
    if states.get(bootstrap_door_id) == "partial":
        _emit("  0x43 bootstrap cave is occupied by an exact persistent patch; "
              "Phase 1 will displace it temporarily and Phase 2 will restore it.")

    if not any(states.get(pid) == "applied"
               for pid in ("softbsl_loader", *_DEPRECATED_LOADER_IDS)):
        return states

    installed = [patch_id for patch_id, state in states.items()
                 if state == "applied"]
    message = (
        "This ECU already has the Soft-BSL boot/param1 loader installed"
        + (f" ({', '.join(installed)} detected)." if installed else ".")
        + "\n\nReinstalling will rewrite the same brick-class boot/program "
          "blocks. Continue with the reinstall?"
    )
    callback = getattr(args, "confirm_reinstall", None)
    accepted = callback(message) if callback else (
        input(message + "\nType 'REINSTALL' to continue: ").strip() == "REINSTALL"
    )
    if not accepted:
        raise SoftBSLError("Soft-BSL reinstall cancelled; nothing was written.")
    _emit("  reinstall confirmed: already-applied patches will be reused.")
    return states


def _patch_base_version(base):
    """Return the one supported, internally-consistent patch target for a full image."""
    from ms41 import MS41ECU
    from engines.patcher import patch_ms41

    base = bytes(base)
    hybrid = MS41ECU.check_hybrid(base)
    program_compat = MS41ECU.read_program_compatibility_id(base)
    calibration_compat = MS41ECU.read_calibration_compatibility_id(base)
    matches = [
        version for version in patch_ms41.FINGERPRINTS
        if patch_ms41.check_base(base, version) is None
    ]
    if (hybrid or program_compat is None or calibration_compat is None
            or program_compat != calibration_compat
            or len(matches) != 1 or matches[0] not in _DOOR_PATCH_IDS):
        resolved = MS41ECU.resolve_version(base)
        raise SoftBSLError(
            "base must exactly match a supported, internally consistent patch "
            f"fingerprint (cal={resolved.get('cal') or 'unknown'}, "
            f"program={resolved.get('program') or 'unknown'}, "
            f"compat={program_compat or 'unknown'}/{calibration_compat or 'unknown'}"
            f"{'; ' + hybrid if hybrid else ''})")
    return matches[0]


def _persistent_patch_plan(base, chip, *, with_calguard=False, with_alphan=False):
    """Return ``(clean_base, target_patch_ids, driver_patch_ids)`` for a persistent image.

    This is the shared composition contract for the ordinary installer and the 29F400 golden
    TOP builder.  It keeps the flash-driver family gate, disposable-door removal and persistent
    patch set identical in both paths.
    """
    base = bytes(base)
    if len(base) != IMAGE_SIZE:
        raise SoftBSLError(f"base is {len(base)} B, expected {IMAGE_SIZE} (256 KB)")
    version = _patch_base_version(base)
    if version != "MS41.3" and with_alphan:
        raise SoftBSLError(
            "the alphan_failsafe restore patch is only applicable to MS41.3")

    driver_off = _DRV_SIG_ADDR ^ DESCR
    base_driver = base[driver_off:driver_off + len(_DRV_SIG_INTEL)]
    base_is_intel = base_driver == _DRV_SIG_INTEL
    base_is_amd = base_driver == _DRV_SIG_AMD
    is_amd_target = str(chip).startswith("29")
    if is_amd_target:
        if not (base_is_intel or base_is_amd):
            raise SoftBSLError(
                f"base flash-driver @0x423C = {base_driver.hex()} is neither Intel "
                f"({_DRV_SIG_INTEL.hex()}) nor AMD ({_DRV_SIG_AMD.hex()}); cannot build a valid 29F image")
        driver_patches = ["amd_flash"] if base_is_intel else []
    else:
        if not base_is_intel:
            what = "the AMD driver" if base_is_amd else f"an unrecognized driver ({base_driver.hex()})"
            raise SoftBSLError(
                f"base carries {what} @0x423C, but the target chip is 28F and needs the Intel driver; "
                "there is no AMD-to-Intel reverse patch")
        driver_patches = []

    from engines.patcher.patch_ms41 import load_patches, is_applied, revert
    patch_defs = load_patches()
    bootstrap_door_id, persistent_door_id = _door_patch_ids(version)
    clean_base = base
    for old_loader_id in _DEPRECATED_LOADER_IDS:
        old_loader = patch_defs.get(old_loader_id)
        if old_loader and is_applied(clean_base, old_loader):
            # Exact installed-state matching makes this safe for both the descriptor-overlapping
            # legacy loader and the non-triggering first relocation. Restore each revision's
            # declared pre-patch bytes before composing the current CRC loader.
            clean_base = bytes(revert(clean_base, old_loader))
    if is_applied(clean_base, patch_defs[bootstrap_door_id]):
        clean_base = bytes(revert(clean_base, patch_defs[bootstrap_door_id]))
    patch_ids = (["softbsl_loader", persistent_door_id]
                 + (["cal_guard"] if with_calguard else [])
                 + (["alphan_failsafe"] if with_alphan else [])
                 + driver_patches)
    # Reinstall composition is idempotent: normalize any selected persistent patch that is
    # already present before asking the patch builder to apply it again. Unselected feature
    # patches (for example an existing AlphaN or cal_guard when the option is not requested)
    # remain in the target and are preserved byte-for-byte.
    for patch_id in patch_ids:
        patch = patch_defs.get(patch_id)
        if patch and is_applied(clean_base, patch):
            clean_base = bytes(revert(clean_base, patch))
    return clean_base, patch_ids, driver_patches


def compose_persistent_image(base, chip, *, with_calguard=False, with_alphan=False, marker=None):
    """Pure persistent-image composition shared by install and golden-TOP workflows.

    Returns ``(image, patch_ids, build_log)``.  ``marker='T'`` produces a golden-bank image and
    recomputes its boot CRC even when every persistent patch was already present in the base.
    """
    clean_base, patch_ids, _driver_patches = _persistent_patch_plan(
        base, chip, with_calguard=with_calguard, with_alphan=with_alphan)
    image, build_log = _compose_image(
        clean_base, patch_ids, marker=marker, return_log=True)
    return image, patch_ids, build_log


def _install_resolve_images(args):
    """Compose the install's 0x43-bootstrap + door_magic-target from patches (base = --base file, else read
    the ECU over stock DS2) UNLESS explicit target+bootstrap image files were given (legacy). Sets
    args.target / args.bootstrap to the composed temp paths. This is what lets the repo ship ZERO .bins."""
    if getattr(args, "target", None) and getattr(args, "bootstrap", None):
        return                                       # legacy: explicit pre-built image files
    if getattr(args, "target", None) or getattr(args, "bootstrap", None):
        raise SoftBSLError("compose mode: pass NEITHER target nor --bootstrap (both are composed from patches), "
                 "or BOTH for the legacy pre-built-file mode.")
    dry = getattr(args, "dry_run", False)
    base_src = getattr(args, "base", None)
    base_inline = getattr(args, "base_bytes", None)
    chip = getattr(args, "chip", "auto")
    _emit("\n== compose install images from patches (no shipped .bins) ==")
    if dry and base_src is None and base_inline is None:
        raise SoftBSLError("  install --dry-run can't read the ECU; pass --base <supported-stock-MS41.bin> (+ --chip) to dry-run the compose.")
    if dry and chip == "auto":
        raise SoftBSLError("  install --dry-run compose: pass an explicit --chip (auto-detect needs the ECU).")

    base = None
    base_source = None
    live_base_needed = base_src is None and base_inline is None
    # Native fast DS2 is a read-only stock-ECU operation. Use it for real installs when the
    # normal DS2 factory is in use; injected factories in tests/app callers retain the legacy
    # deterministic path. Chip auto-detection still uses one short stock-DS2 session first.
    factory = getattr(args, "ds2_factory", DS2Interface)
    factory_name = getattr(factory, "__name__", "")
    factory_module = getattr(factory, "__module__", "")
    native_factory = (
        factory is DS2Interface
        or (factory_name == "DS2Interface" and factory_module in {
            "ds2", "engines.softbsl.ds2"
        })
    )
    use_fast_base = (
        live_base_needed and not dry and bool(getattr(args, "port", None))
        and native_factory
    )
    if use_fast_base and chip != "auto":
        # Capture every live safety marker before the native-fast base read so
        # the remaining install never needs to reopen DS2 merely to repeat the
        # same preflight.
        d = _open(args)
        try:
            det, sig = _detect_flash_chip(d)
            _store_live_preflight(args, d, det, sig)
        finally:
            d.close()
        try:
            base = _read_ecu_base_fast(
                args, progress_cb=getattr(args, "progress_cb", None))
            base_source = "native fast stock DS2"
        except Exception as fast_error:
            _emit(f"  native fast stock-DS2 base read unavailable ({fast_error}); "
                  "falling back to legacy 9600 DS2.")
            d = _open(args)
            try:
                base = _read_ecu_base(d, progress_cb=getattr(args, "progress_cb", None))
            finally:
                d.close()
            base_source = "legacy stock DS2 fallback"
    elif chip == "auto" or live_base_needed:
        d = _open(args)
        try:
            det, sig = _detect_flash_chip(d)
            _store_live_preflight(args, d, det, sig)
            if chip == "auto":
                if det is None:
                    raise SoftBSLError(f"  flash-IC auto-detect FAILED (boot/param1 driver sig @0x023C = "
                             f"{sig.hex() or 'unreadable'}); pass --chip.")
                chip = "29f400" if det == "amd" else "28f200"
                _emit(f"  chip: auto-detected {chip} ({det.upper()} driver)")
            if live_base_needed:
                if use_fast_base:
                    # Close the probe before opening the D2XX fast reader (FTDI handles are
                    # exclusive). No later installer phase repeats this preflight.
                    d.close()
                    d = None
                    try:
                        base = _read_ecu_base_fast(
                            args, progress_cb=getattr(args, "progress_cb", None))
                        base_source = "native fast stock DS2"
                    except Exception as fast_error:
                        _emit(f"  native fast stock-DS2 base read unavailable ({fast_error}); "
                              "falling back to legacy 9600 DS2.")
                        d = _open(args)
                        base = _read_ecu_base(
                            d, progress_cb=getattr(args, "progress_cb", None))
                        base_source = "legacy stock DS2 fallback"
                else:
                    base = _read_ecu_base(
                        d, progress_cb=getattr(args, "progress_cb", None))
                    base_source = "legacy stock DS2"
        finally:
            if d is not None:
                d.close()
    if chip != getattr(args, "chip", "auto"):
        args.chip = chip                             # pin the resolved chip for the downstream gate
    if base is None:
        if base_inline is not None:
            base = bytes(base_inline)
            _emit(f"  base: cached application full read ({len(base)} B)")
        else:
            base = open(base_src, "rb").read()
            _emit(f"  base: {base_src} ({len(base)} B, file)")
    else:
        _emit(f"  base: ECU read ({len(base)} B, {base_source or 'stock DS2'})")

    if len(base) != IMAGE_SIZE:
        raise SoftBSLError(f"  base is {len(base)} B, expected {IMAGE_SIZE} (256 KB).")
    try:
        target_version = _patch_base_version(base)
    except SoftBSLError as error:
        raise SoftBSLError(f"  {error}")
    args.target_version = target_version
    _emit(f"  patch target: {target_version}")
    bootstrap_door_id, _persistent_door_id = _door_patch_ids(target_version)

    try:
        target_base, tgt_patches, amd = _persistent_patch_plan(
            base, chip,
            with_calguard=getattr(args, "with_calguard", False),
            with_alphan=getattr(args, "with_alphan", False))
    except SoftBSLError as error:
        raise SoftBSLError(f"  {error}")
    if not amd and str(chip).startswith("29"):
        _emit("  (base already carries the AMD driver -- amd_flash not needed)")
    boot_patches = ["softbsl_loader", bootstrap_door_id] + amd
    from engines.patcher.patch_ms41 import load_patches
    patch_defs = load_patches()
    relevant_patches = list(dict.fromkeys(boot_patches + tgt_patches))
    old_loaders_installed = [
        patch_id for patch_id in _DEPRECATED_LOADER_IDS
        if patch_id in patch_defs and _patch_state(base, patch_defs[patch_id]) == "applied"
    ]
    confirm_patches = list(relevant_patches)
    if old_loaders_installed:
        confirm_patches = [pid for pid in confirm_patches if pid != "softbsl_loader"]
        confirm_patches.extend(old_loaders_installed)
        if "softbsl_loader_relocated_v1" in old_loaders_installed:
            _emit("  non-triggering relocated loader v1 detected: migrating to the current CRC implementation")
        if "softbsl_loader_legacy" in old_loaders_installed:
            _emit("  legacy loader @0x5D36 detected: migrating to the descriptor-safe relocated layout")
    _confirm_reinstall(
        args, base, patch_defs, confirm_patches,
        bootstrap_door_id=bootstrap_door_id)
    _emit(f"  bootstrap = [{', '.join(boot_patches)}]")
    _emit(f"  target    = [{', '.join(tgt_patches)}]")
    import tempfile
    # Phase 1's stock-DS2 erase must rewrite the program array, but a second
    # 144 KB byte-for-byte read at 9600 is unnecessary. The stock integrity
    # finalize checks the program, and these exact patch-edit ranges prove the
    # new 0x43 entry hook/cave itself landed before the mandatory key-cycle.
    # Phase 2 rewrites + verifies all program-high bytes through the agent, but
    # it intentionally leaves DS2 program-low untouched. Verify that small 16K
    # range in full, plus the temporary 0x43 edits in program-high.
    args.bootstrap_verify_ranges = [
        (0x02000, 0x04000, "program-low safety range"),
        *_bootstrap_verify_ranges(boot_patches),
    ]
    # target_base has the selected persistent patches normalized and any exact legacy/0x43 loader
    # safely reverted. A different persistent feature may occupy the same code cave as the
    # disposable door; displace only that exact patch for Phase 1. Phase 2 is still composed from
    # target_base, so the feature returns in the final image.
    bootstrap_base, displaced = _temporary_bootstrap_base(
        target_base, patch_defs, bootstrap_door_id)
    if displaced:
        _emit("  bootstrap temporarily displaces: " + ", ".join(displaced)
              + " (restored by the Phase-2 target)")
    boot_img = _compose_image(bootstrap_base, boot_patches)
    tgt_img = _compose_image(target_base, tgt_patches)
    td = tempfile.mkdtemp(prefix="softbsl_install_")
    args.bootstrap = os.path.join(td, "bootstrap_0x43.bin")
    args.target = os.path.join(td, "target.bin")
    with open(args.bootstrap, "wb") as f:
        f.write(boot_img)
    with open(args.target, "wb") as f:
        f.write(tgt_img)
    _emit(f"  composed -> {td}\n")


def _ms41_install_scope(version, preserve_cal=True):
    """Calibration-safe persistent-install scope for a consistent target version."""
    if not preserve_cal:
        return "full"
    if version in _DOOR_PATCH_IDS:
        return "softbsl_ms412"
    raise SoftBSLError(f"no calibration-safe Soft-BSL install scope for {version!r}")


def _ms413_install_scope(preserve_cal=True):
    """Backward-compatible wrapper used by older callers/tests."""
    return _ms41_install_scope("MS41.3", preserve_cal)


def _normalize_install_image(image):
    """Normalize to the coding family in the boot image being written."""
    from ms41 import MS41ECU

    start = MS41ECU.CODING_FAMILY_FILE_ADDR
    coding_family = bytes(image[start:start + 3])
    normalized = MS41ECU.graft_coding_family(bytes(image), coding_family)
    normalized, _details = checksum.correct_checksums(normalized)
    hybrid = MS41ECU.check_hybrid(normalized)
    if hybrid:
        raise SoftBSLError(
            f"coding-family normalization produced an incompatible image: {hybrid}")
    return bytes(normalized)


def _store_normalized_install_images(args, target, bootstrap):
    import tempfile

    directory = tempfile.mkdtemp(prefix="softbsl_compat_")
    args.target = os.path.join(directory, "target.bin")
    args.bootstrap = os.path.join(directory, "bootstrap_0x43.bin")
    with open(args.target, "wb") as stream:
        stream.write(target)
    with open(args.bootstrap, "wb") as stream:
        stream.write(bootstrap)
    return target, bootstrap


def _install_copy(args, **overrides):
    ns = copy.copy(args)
    for key, value in overrides.items():
        setattr(ns, key, value)
    return ns


def _install_recovery(args, target, flash_over, phase, retained):
    return InstallRecovery(
        request=args,
        target=bytes(target),
        flash_over=dict(flash_over),
        phase=phase,
        retained=retained,
    )


def _install_keycycle(args):
    prompt = getattr(args, "keycycle_prompt", None)
    retry_prompt = getattr(args, "keycycle_retry_prompt", None)
    message = (
        "KEY-CYCLE the ECU NOW: switch ignition OFF and wait ~10 s, then switch "
        "ignition ON and continue. This reboot is mandatory so the freshly-flashed "
        "0x43 door is armed."
    )
    if prompt is not None:
        prompt(message)
    else:
        input(
            "\n  >>> KEY-CYCLE the ECU NOW (ignition OFF, wait ~10 s, then ignition ON),"
            " then press Enter ..."
            "\n      (mandatory -- the freshly-flashed 0x43 door is inert until this reboot arms the"
            " normal-mode dispatcher) "
        )

    while True:
        probe = None
        readiness_error = None
        try:
            _emit("Confirming that the ECU rebooted into normal 9600-baud DS2 before Phase 2 ...")
            probe = _open(args)
            identity = probe.identify()
            if not identity:
                raise SoftBSLError("stock DS2 identify returned an empty response")
        except Exception as error:
            readiness_error = error
        finally:
            if probe is not None:
                try:
                    probe.close()
                except Exception as close_error:
                    readiness_error = SoftBSLError(
                        f"post-key-cycle DS2 probe could not release the serial port: {close_error}"
                    )
                    _emit(
                        f"Post-key-cycle DS2 probe cleanup failed: {close_error}",
                        level="error",
                    )
        if readiness_error is not None:
            detail = (
                "The ECU did not answer normal 9600-baud DS2 after the required ignition "
                f"cycle ({readiness_error}).\n\n"
                "Confirm ignition OFF, wait approximately 10 seconds, then ignition ON. "
                "Retry checks the ECU again. Cancel stops safely before Phase 2 erase."
            )
            _emit(
                f"Post-key-cycle stock DS2 readiness was not confirmed: {readiness_error}",
                level="warn",
            )
            if retry_prompt is not None:
                retry = bool(retry_prompt(detail))
            else:
                retry = input(
                    "\n  ECU did not answer normal DS2 after the key-cycle. "
                    "Turn ignition ON, then type R to retry or C to cancel before Phase 2: "
                ).strip().lower() in ("r", "retry")
            if not retry:
                raise InstallCancelled(
                    "installation cancelled after the temporary entry path was written; "
                    "Phase 2 erase was not started",
                    phase="post_phase1",
                ) from readiness_error
            continue
        _emit("Post-key-cycle stock DS2 readiness confirmed; Phase 2 may begin.", level="ok")
        return


def _run_install_target_phase(args, target, flash_over):
    _emit(
        "\n=== PHASE 2/3: flash the target (boot/param1 0x5A loader + 0x2A dispatcher; "
        "0x43 slot erased) via the 0x43 door ==="
    )
    flash_ns = _install_copy(args, dry_run=False, **flash_over)
    try:
        cmd_flash(flash_ns)
    except _RetainedInstallFlashRequired as error:
        raise InstallRecoveryRequired(
            _install_recovery(args, target, flash_over, "target", error.recovery)
        ) from error

    recovered = getattr(flash_ns, "_recovered", None)
    if recovered:
        _emit(
            "\n  Phase 2 finalized E740=0 (marker 0, drivable) inside the running "
            "0x43 RAM agent before reset -- no 0x2A/0x5A re-entry, no second agent, "
            "no key-cycle."
        )
    else:
        _emit(
            "\n  ** Phase 2 did not confirm E740=0 -- if the ECU isn't drivable, "
            "key-cycle + run `recover-phase`."
        )


def _finish_install(args, target):
    _emit("\n=== PHASE 3/3: verify via stock DS2 ===")
    _verify_install(args, target)
    _emit(
        "\n>>> INSTALL DONE: soft-BSL base installed (boot/param1 0x5A loader + "
        "0x2A dispatcher), 0x43 bootstrap gone, E740=0 (marker 0), your cal/tune preserved."
    )


def _continue_install_after_bootstrap(args, target, flash_over):
    _install_keycycle(args)
    _run_install_target_phase(args, target, flash_over)
    _finish_install(args, target)


def resume_install_recovery(recovery, progress_cb=None):
    """Resume a failed install without reopening or replacing its retained handle."""
    if not isinstance(recovery, InstallRecovery):
        raise TypeError("recovery must be an InstallRecovery")
    if not recovery.is_open:
        raise SoftBSLError("the retained Soft-BSL installer recovery session is closed")

    recovery.request.progress_cb = progress_cb
    if recovery.phase == "bootstrap":
        import ds2_native_fast_service
        try:
            ds2_native_fast_service.resume_recovery(
                recovery.retained,
                progress_cb=progress_cb,
            )
        except ds2_native_fast_service.NativeWriteRecoveryRequired as error:
            recovery.retained = error.recovery
            raise InstallRecoveryRequired(recovery) from error
        _continue_install_after_bootstrap(
            recovery.request,
            recovery.target,
            recovery.flash_over,
        )
        return True

    if recovery.phase == "target":
        try:
            recovered = _resume_retained_install_flash(
                recovery.retained,
                progress_cb=progress_cb,
            )
        except _RetainedInstallFlashRequired as error:
            recovery.retained = error.recovery
            raise InstallRecoveryRequired(recovery) from error
        if recovered:
            _emit(
                "\n  Phase 2 recovery finalized E740=0 before reset; continuing "
                "with the stock-DS2 install verification."
            )
        else:
            _emit(
                "\n  ** Phase 2 recovery did not confirm E740=0; continuing with "
                "the stock-DS2 install verification."
            )
        _finish_install(recovery.request, recovery.target)
        return True

    raise SoftBSLError(f"unknown installer recovery phase {recovery.phase!r}")


def cmd_install(args):
    """Guided persistent Soft-BSL install. Bootstrap a disposable 0x43 door via stock
    DS2 -> [KEY-CYCLE] -> full-flash the TARGET (SA1 loader + program door + cal) via the 0x43 door
    INCLUDING param1 -> verify. End state: soft-BSL loader in SA1 + the target's program door
    (e.g. door_magic 0x2A); the 0x43 bootstrap is erased by the full flash. Writing SA1 is BRICK-CLASS
    (recovery = HW BSL).

    KEY-CYCLE MODEL: Phase 1's deploy splice finalizes to E740=0 (normal mode, stock
    program VERIFY 07/0F @0x001D07; NO CAL touch), so the ECU is drivable after it -- but the 0x43 door
    is inert until a reboot re-arms the normal-mode dispatcher (confirmed with the door byte-verified in
    flash: no ACK in-session, fires after one key-cycle). There is NO DS2-reachable software reset, so the
    KC#1 between deploy and trigger is a MANDATORY MANUAL key-cycle. The post-Phase-2 reboot IS
    hands-off: the RAM agent's hybrid SRST boots the
    final image, then verify. So the whole install costs exactly ONE physical key-cycle (= factory).

    IMAGES: pass NO target/bootstrap to COMPOSE them at runtime from patches (base = --base file, else read
    the ECU over stock DS2) -- so no firmware .bin ships. Or pass explicit target + --bootstrap files (legacy)."""
    _install_resolve_images(args)                    # compose bootstrap+target from patches unless files were given
    target = open(args.target, "rb").read()
    if len(target) != IMAGE_SIZE:
        raise SoftBSLError(f"target is {len(target)} B, expected {IMAGE_SIZE} (256 KB)")
    target_version = _patch_base_version(target)
    args.target_version = target_version
    bootstrap = open(args.bootstrap, "rb").read()
    if len(bootstrap) != IMAGE_SIZE:
        raise SoftBSLError(f"bootstrap image is not {IMAGE_SIZE} B")
    _emit("== persistent Soft-BSL install ==")
    _emit(f"  bootstrap door : {args.bootstrap}   (a 0x43-door image; disposable)")
    _emit(f"  target image   : {args.target}   (MUST have the SA1 loader + your program door, NO leftover 0x43)")
    kc = "key-cycle"

    def _sub(**over):
        ns = copy.copy(args)
        for k, v in over.items():
            setattr(ns, k, v)
        return ns

    # ── PRE-FLIGHT VERSION GATE (mirrors cal_guard_gate.asm / ms41_variant.check_hybrid) ──
    # A consistent live ECU matching the composed target keeps its calibration. The install scope
    # rewrites param2/SA2 because the corrected program CRC is stored at file 0x6050 (CPU 0x2050).
    # Cross-version targets require an explicit full conversion; hybrids always fail closed.
    preserve_cal = bool(getattr(args, "preserve_cal", True))
    install_scope = _ms41_install_scope(target_version, preserve_cal)
    if args.dry_run:
        _emit(f"\n  [version gate] target={target_version}; a matching consistent live ECU uses "
              f"scope={install_scope} and preserves calibration.")
        _emit("    A different consistent version requires explicit CONVERT (full write + cal wipe); "
              "a program/cal hybrid is refused.")
        _emit("  [flash-IC gate] a LIVE run auto-detects the driver FAMILY from the boot/param1")
        _emit("    driver signature @DS2 0x023C")
        _emit("    (AMD e00e0d58 = 29F200/29F400-bottom / Intel e6f45000 = 28F200); --chip auto adopts it")
        _emit("    (28F uses the proven Intel agent + 12 V VPP); an explicit --chip is cross-checked and")
        _emit("    REFUSED on a family mismatch.")
    else:
        preflight = _live_preflight(args)
        if preflight is None:
            dg = _open(args)  # CLEAN stock-DS2 read only; no agent trigger.
            try:
                det_fam, det_sig = _detect_flash_chip(dg)
                preflight = _store_live_preflight(args, dg, det_fam, det_sig)
            finally:
                dg.close()
        else:
            det_fam = preflight["flash_family"]
            det_sig = preflight["flash_signature"]
        cal_v = preflight["cal_variant"]
        prog_v = preflight["program_variant"]
        cal_compat = preflight["cal_compatibility_id"]
        program_compat = preflight["program_compatibility_id"]
        coding_family = preflight["coding_family"]
        broad_consistent = preflight["broad_consistent"]
        exact_consistent = preflight["exact_consistent"]
        consistent = preflight["consistent"]
        # ── flash-IC reconcile (checked FIRST: the WRONG flash command set = an instant brick) ──
        raw_chip = getattr(args, "chip", "auto")
        det_label = _DRV_FAMILY_LABEL.get(det_fam)
        if raw_chip == "auto":
            if det_fam is None:
                raise SoftBSLError(f"  X flash-IC auto-detect FAILED: boot/param1 driver sig @DS2 0x023C = "
                         f"{det_sig.hex() or '<unreadable>'} matched neither AMD ({_DRV_SIG_AMD.hex()}) nor "
                         f"Intel ({_DRV_SIG_INTEL.hex()}). Re-run with an explicit --chip once you confirm the silicon.")
            _emit(f"\n  flash-IC: auto-detected {det_label} (boot/param1 driver sig @DS2 0x023C).")
            if det_fam == "intel":
                args.chip = "28f200"                        # downstream cmd_flash/_resolve_chip -> Intel agent
            # det_fam == "amd": leave args.chip='auto' -> default AMD flow (agent.hex; serves 29F200 +
            # 29F400-bottom -- the driver sig can't tell them apart; pass --chip 29f200 for that region map).
        else:
            want_fam = chipdefs.FAMILY[raw_chip]["cmdset"]
            if det_fam and det_fam != want_fam:
                raise SoftBSLError(f"  X --chip {raw_chip} ({want_fam}) but the ECU's boot/param1 driver is {det_fam.upper()} "
                         f"(sig @DS2 0x023C = {det_sig.hex()}) -- a {want_fam} command set against a {det_fam} chip "
                         f"is BRICK-CLASS. Aborting (pass the matching --chip, or --force to override).")
            xcheck = f"MATCH ({det_label})" if det_fam else f"skipped (sig {det_sig.hex() or 'unreadable'})"
            _emit(f"\n  flash-IC: --chip {raw_chip}; boot/param1 driver cross-check = {xcheck}")
        _emit(
            f"\n  version gate: ECU cal={cal_v}/{cal_compat or 'unknown'}  "
            f"program={prog_v}/{program_compat or 'unknown'}  "
            f"consistent={consistent}; target={target_version}")
        if not broad_consistent:
            raise SoftBSLError(f"  X ECU is a program<->cal HYBRID (cal={cal_v} / program={prog_v}) -- REFUSING to flash. "
                     f"Recover to a consistent image (BSL-Unbricker) before installing soft-BSL.")
        if not exact_consistent or coding_family is None:
            raise SoftBSLError(
                "  X ECU Firmware Compatibility ID is missing or mismatched "
                f"(cal={cal_compat or 'unknown'} / "
                f"program={program_compat or 'unknown'}). "
                "Recover to a consistent image before installing Soft-BSL.")

        from ms41 import MS41ECU
        target_program_compat = MS41ECU.read_program_compatibility_id(target)
        target_cal_compat = MS41ECU.read_calibration_compatibility_id(target)
        target_compat_available = bool(
            target_program_compat and target_cal_compat
        )
        exact_target_match = (
            target_program_compat == program_compat
            and target_cal_compat == cal_compat
            if target_compat_available else True
        )
        normalized_target = normalized_bootstrap = None
        if (target_compat_available and preserve_cal and cal_v == target_version
                and prog_v == target_version):
            normalized_target = _normalize_install_image(target)
            normalized_bootstrap = _normalize_install_image(bootstrap)
            target_program_compat = (
                MS41ECU.read_program_compatibility_id(normalized_target))
            target_cal_compat = (
                MS41ECU.read_calibration_compatibility_id(normalized_target))
            exact_target_match = (
                target_program_compat == program_compat
                and target_cal_compat == cal_compat
            )

        if (cal_v == target_version and prog_v == target_version
                and exact_target_match):
            if preserve_cal:
                if normalized_target is not None:
                    target, bootstrap = _store_normalized_install_images(
                        args, normalized_target, normalized_bootstrap)
                scope_text = ("28F boot/param1 + param2/checksum + main-E"
                              if args.chip == "28f200"
                              else "29F SA1 + SA2/checksum + SA5/SA6")
                _emit(
                    f"  -> {target_version}/{cal_compat} confirmed: "
                    f"CAL-SKIP install ({scope_text}; calibration untouched).")
            else:
                install_scope = "full"
                _emit(f"  !! {target_version} confirmed, but calibration preservation was explicitly disabled.")
                _emit("  -> FULL write INCLUDING the CAL from the composed base (BRICK-CLASS).")
        else:
            _emit(
                f"\n  !! The ECU is running {prog_v}/{program_compat}; "
                f"the composed target is {target_version}"
                + (
                    f"/{target_program_compat or 'unknown'}."
                ))
            _emit(f"     Converting to {target_version} requires a FULL WRITE that erases and replaces the "
                  f"current {cal_v} calibration/tune.")
            _confirm_convert = getattr(args, "confirm_convert", None)
            convert_ok = (_confirm_convert() if _confirm_convert is not None else
                          input(f"     Type 'CONVERT' to wipe the cal and convert to {target_version} "
                                "(anything else aborts): ").strip() == "CONVERT")
            if not convert_ok:
                raise SoftBSLError("  aborted (no conversion).")
            install_scope = "full"
            _emit("  -> CONVERT: FULL write INCLUDING the CAL.")
    if install_scope == "softbsl_ms412":
        _ph2 = ("28F boot/param1 8K + param2/checksum 8K + main-E 128K (CAL-safe)"
                if args.chip == "28f200"
                else "29F SA1 + SA2/checksum + SA5/SA6 (CAL-safe)")
    elif install_scope == "softbsl":
        _ph2 = ("28F boot/param1 8K + main-E 128K (CAL-safe)"
                if args.chip == "28f200"
                else "29F SA1 + SA5/SA6 (CAL-safe)")
    else:
        _ph2 = "FULL incl. CAL (CONVERT)"
    _bnote = f"@ {args.baud} baud" + ("" if args.baud == "low" else " (fast; D2XX-proven)")
    _emit(f"\n  sequence: 1) DS2 deploy 0x43 bootstrap  ->{kc}->  2) flash {_ph2} via 0x43 {_bnote} "
          f"(BRICK-CLASS: writes boot/param1)  -> agent hybrid SRST  ->  3) verify")
    flash_over = dict(image=args.target, scope=install_scope, write_bootloader=True, no_verify=False,
                      assume_half="B", baud=args.baud, trigger="43", force=args.force, yes=True,
                      baud_fallback=True,
                      reset_recover=True,  # 0x43 RAM agent commits E740=0 BEFORE hybrid SRST; NO re-entry
                      retain_on_failure=True)
    if args.dry_run:
        _emit("\n--- PHASE 1 (dry-run): bootstrap door via stock DS2 ---")
        cmd_deploy_splice(_sub(image=args.bootstrap, dry_run=True, yes=True,
                               no_readback=True,
                               verify_ranges=getattr(args, "bootstrap_verify_ranges", ())))
        _emit(f"\n--- PHASE 2 (dry-run): flash target via 0x43 (scope={install_scope}) ---")
        cmd_flash(_sub(dry_run=True, **flash_over))
        _emit("\n--- PHASE 3 (dry-run): would verify boot/param1 marker/hook/loader + program door via stock DS2 ---")
        return
    if not args.yes and input("\n  Proceed with the LIVE install (writes the boot sector)? Type 'yes': ").strip() != "yes":
        raise SoftBSLError("aborted.")
    _emit("\n=== PHASE 1/3: bootstrap 0x43 door via stock DS2 (program region; boot untouched) ===")
    native_fast_retry_only = False
    while True:
        try:
            cmd_deploy_splice(
                _sub(
                    image=args.bootstrap,
                    dry_run=False,
                    yes=True,
                    no_readback=True,
                    verify_ranges=getattr(args, "bootstrap_verify_ranges", ()),
                    phase1_reentry_recovery=True,
                    native_fast_retry_only=native_fast_retry_only,
                )
            )
        except Exception as error:
            # Import locally so the standalone host CLI does not load the native
            # production stack unless this install path actually uses it.
            import ds2_native_fast_service
            if isinstance(error, ds2_native_fast_service.NativeWriteRecoveryRequired):
                raise InstallRecoveryRequired(
                    _install_recovery(args, target, flash_over, "bootstrap", error.recovery)
                ) from error
            if (
                isinstance(error, ds2_native_fast_service.NativeFastPreEraseFailure)
                and (error.reentry_not_ready or error.power_cycle_required)
            ):
                recovery_prompt = getattr(args, "phase1_reentry_prompt", None)
                if recovery_prompt is None:
                    raise SoftBSLError(
                        f"native fast bootstrap was not started: {error}. Nothing "
                        "was erased. Turn ignition OFF, wait at least 10 seconds, "
                        "turn ignition ON, then retry"
                    ) from error
                if error.power_cycle_required:
                    reason = (
                        "The ECU did not accept or confirm the stock DS2 write "
                        "authorization. The temporary Soft-BSL write was not started. "
                        "No erase or flash command was sent, and nothing was erased. "
                        "The authorization state is ambiguous, so the host will not "
                        "retry the key or fall back to slow DS2."
                    )
                    cancel_detail = "no erase or flash command was sent"
                    cancel_phase = "pre_phase1_authorization"
                else:
                    reason = (
                        "The ECU did not finish its previous native-fast session, so "
                        "the temporary Soft-BSL write was not started. No challenge, "
                        "selector, erase, or flash command was sent, and nothing was "
                        "erased."
                    )
                    cancel_detail = (
                        "no challenge, selector, erase, or flash command was sent"
                    )
                    cancel_phase = "pre_phase1"
                message = (
                    f"{reason}\n\n"
                    "The serial port has been disconnected and released.\n\n"
                    "Turn ignition OFF, wait at least 10 seconds, then turn ignition ON. "
                    "After ignition is ON, click Retry to continue. Click Cancel to stop "
                    "the entire Soft-BSL installation."
                )
                if not bool(recovery_prompt(str(args.port), message)):
                    raise InstallCancelled(
                        f"installation cancelled before the temporary Phase 1 write; "
                        f"{cancel_detail}",
                        phase=cancel_phase,
                    ) from error
                # An explicit Retry repeats only this already-prepared native-fast
                # program-only write. It must never downshift into the legacy writer.
                native_fast_retry_only = True
                continue
            raise
        break

    # The disposable 0x43 door is inert until boot initialization re-arms the
    # normal-mode dispatcher, so this physical ignition cycle remains mandatory.
    _continue_install_after_bootstrap(args, target, flash_over)


def install(request, log):
    """Run a persistent install through the typed in-process application API."""
    if not isinstance(request, InstallRequest):
        raise TypeError("request must be an InstallRequest")
    with operation_log_sink(log):
        cmd_install(request)


def resume_install(recovery, log, progress_cb=None):
    """Resume a retained install through the typed in-process application API."""
    if not isinstance(recovery, InstallRecovery):
        raise TypeError("recovery must be an InstallRecovery")
    with operation_log_sink(log):
        return resume_install_recovery(recovery, progress_cb=progress_cb)




def main():
    # Never let a stray non-ASCII glyph (e.g. an em-dash echoed from ECU data, or a warning symbol)
    # crash the tool with UnicodeEncodeError on a legacy cp1252 Windows console -- degrade to '?' instead.
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="soft-BSL: one CLI to trigger/flash/recover an MS41 ECU over the K-line.")
    ap.add_argument("--port", help="serial port (e.g. COM1)")
    ap.add_argument("--agent", default=_agent_default(), help="agent hex (default: agent.hex)")
    ap.add_argument("--chip", choices=["auto", "28f200", "29f200", "29f400"], default="auto",
                    help="flash IC. auto (default) = install/id auto-detect the DRIVER FAMILY from the boot/param1 "
                         "sig @DS2 0x023C (AMD e00e0d58 = 29F200/29F400-bottom / Intel e6f45000 = 28F200; stock "
                         "DS2 has no silicon read-ID); an explicit value is cross-checked vs that sig and refused "
                         "on a family mismatch. 28f200 needs the Intel agent_28f.hex + Intel-driver images")
    ap.add_argument("--half", choices=["upper", "lower"], default="lower",
                    help="29f400 only: A17 strap (lower=working fine-sector bank [default], "
                         "upper=golden coarse-sector bank)")
    ap.add_argument("--trigger", choices=["43", "5a", "9c"], default="43",
                    help="soft-BSL door: 43=program-region 0x43/9C9C splice (default), "
                         "5a=SA1 bootloader (flash-mode), 9c=legacy param1 stub")
    ap.add_argument("--no-echo", action="store_true", help="adapter suppresses the half-duplex echo")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("ports", help="list serial ports").set_defaults(func=cmd_ports)
    sub.add_parser("ping", help="enter + identify, NO erase (safe test; --trigger 43/5a picks the door)"
                   ).set_defaults(func=cmd_ping)
    sub.add_parser("identify", help="enter + report the LIVE bank-ID marker via the agent "
                   "(vs `chip` = the static flash-IC profile)").set_defaults(func=cmd_identify)
    sub.add_parser("reset", help="enter + reset the ECU").set_defaults(func=cmd_reset)

    sub.add_parser("chip", aliases=["id"],
                   help="show the static flash-IC profile for --chip (+ live marker if --port); alias: id"
                   ).set_defaults(func=cmd_id)

    pr = sub.add_parser("read", help="read a range via the agent at --baud (START:END or START+LEN; raw CPU addr)")
    pr.add_argument("range", help="START:END (END is EXCLUSIVE) or START+LEN, hex ok, e.g. 0x23354:0x23394 "
                                  "or 0x23354+0x40. (positional here; on `dump` the same grammar is the --range flag)")
    pr.add_argument("--baud", choices=list(BG), default="low", help="agent baud tier (high=187500)")
    pr.set_defaults(func=cmd_read)

    pdmp = sub.add_parser("dump", help="dump flash to FILE via the agent at --baud (file order)")
    pdmp.add_argument("out", help="output .bin")
    pdmp.add_argument("--range", help="START:END (END EXCLUSIVE) or START+LEN file offsets, hex ok -- same "
                                      "grammar as read's positional range, e.g. 0x20000:0x40000 or 0x20000+0x20000. "
                                      "Default = the whole bottom half 0:0x40000. (file offsets, descrambled per "
                                      "16 KB -- not raw CPU addrs like `read`)")
    pdmp.add_argument("--baud", choices=list(BG), default="low",
                      help="agent baud tier (high=187500 = real S0BG=1 @fOSC 24.0; mid=93750)")
    pdmp.add_argument("--raw-hole", action="store_true",
                      help="keep the raw floating-bus read of the unmapped hole (file 0x8000-0xBFFF); "
                           "default 0xFF-fills it (the true blank content)")
    pdmp.add_argument("--stay-flash", action="store_true",
                      help="leave E740=1 (flash mode) after the dump for chained ops; default returns to "
                           "E740=0 (NORMAL/drivable). With --trigger 5a, dump also auto-enters flash mode "
                           "from a running ECU (0x2A door), so it's hands-off end to end.")
    pdmp.set_defaults(func=cmd_dump)

    pms = sub.add_parser("mode-switch", help="send a DS2 door cmd (default 0x2A) -> flash mode; report E740")
    pms.add_argument("--command", default="0x2A", help="DS2 command byte (default 0x2A door_magic)")
    pms.set_defaults(func=cmd_mode_switch)

    prp = sub.add_parser("recover-phase", help="exit stuck-in-flash-mode: E740->0 via the SA1 0x5A door")
    prp.add_argument("--yes", action="store_true", help="skip the confirm prompt")
    prp.set_defaults(func=cmd_recover_phase)

    pds = sub.add_parser("deploy-splice",
                         help="DS2-only: erase+rewrite the program region from an image (cal/tune preserved)")
    pds.add_argument("image", help="256 KB image with the program-region splice/door")
    pds.add_argument("--dry-run", action="store_true", help="verify image + block-swap, no serial I/O")
    pds.add_argument("--yes", action="store_true", help="skip the confirm prompt")
    pds.add_argument("--no-finalize", action="store_true",
                     help="skip the E740->0 finalize (leaves the ECU stuck in flash mode -- NOT recommended; "
                          "the default runs the stock program VERIFY to commit E740=0, NO CAL touch)")
    pds.add_argument("--no-readback", action="store_true",
                     help="skip our own byte-for-byte program read-back verify (default ON: re-reads the "
                          "deployed program + compares to the image, catching errors in custom patches the "
                          "stock 36-byte signature gate misses; ~seconds fast / ~4 min @9600)")
    pds.set_defaults(func=cmd_deploy_splice)

    pin = sub.add_parser("install",
                         help="guided persistent Soft-BSL install: bootstrap 0x43 -> agent flash -> verify")
    pin.add_argument("target", nargs="?", help="[legacy] a pre-built 256 KB target image. OMIT to COMPOSE "
                                               "it (+ the bootstrap) from patches -- see --base.")
    pin.add_argument("--bootstrap", help="[legacy] a pre-built 0x43-door bootstrap image. Omit with `target` to compose.")
    pin.add_argument("--base", help="COMPOSE mode: a consistent MS41.0-MS41.3 base .bin to patch. Omit to read the base "
                                    "off the ECU over stock DS2 (default). Ignored if `target`+`--bootstrap` are given.")
    pin.add_argument("--with-calguard", action="store_true",
                     help="compose mode: add the cal_guard no-brick version gate to the target (recommended).")
    pin.add_argument("--with-alphan", action="store_true",
                     help="compose mode: add alphan_failsafe (MAF-unplug failover) to the target. NOTE: that "
                          "patch embeds BMW-derived tables -- only if you have it locally.")
    pin.add_argument("--dry-run", action="store_true", help="plan both phases, no serial I/O / no key-cycles "
                                                            "(compose mode needs --base + --chip for a dry-run)")
    pin.add_argument("--force", action="store_true", help="flash even if target/base checksums look invalid")
    pin.add_argument("--yes", action="store_true", help="skip the top-level confirm (NOT the key-cycle prompts)")
    pin.add_argument("--baud", choices=list(BG), default="low",
                     help="Phase-2 agent flash baud tier (default low=9600, the conservative brick-class "
                          "default; high=187500 is ~2-3x faster and PROVEN clean under the D2XX transport, "
                          "incl. the SA1 boot-sector write). Phase-1 prefers native-fast exact 187500 "
                          "with a pre-erase fallback to 9600 when D2XX is unavailable or unstable.")
    # NOTE: --chip is GLOBAL (defined on the top-level parser, like --port) and must
    # precede the subcommand: `softbsl_host.py --chip 28f200 install <target> ...`.
    # For --chip auto, install detects the command-set family from the boot/param1 driver signature.
    # an explicit --chip is cross-checked against that sig and REFUSED on a family mismatch.
    pin.set_defaults(func=cmd_install)


    pf = sub.add_parser("flash", help="enter + flash the image (scope-selectable)")
    pf.add_argument("image", help="checksum-corrected .bin (marker decides the half)")
    pf.add_argument("--scope", choices=["full", "program", "tune", "sa1", "softbsl", "softbsl_ms412"], default="full",
                    help="full=whole image; program=program-high (tune-safe); tune=calibration; "
                         "sa1=boot/param1 (brick-class); softbsl=boot/param1+program-high CAL-safe install")
    pf.add_argument("--dry-run", action="store_true", help="print the erase/program/verify plan; no ECU contact")
    pf.add_argument("--write-bootloader", action="store_true",
                    help="ALSO write SA1/param1 (opt-in; implied by --scope sa1)")
    pf.add_argument("--baud", choices=list(BG), default="low",
                    help="agent bulk-transfer baud tier (default low=9600; high=187500 = real S0BG=1 @fOSC 24.0)")
    pf.add_argument("--no-verify", action="store_true", help="skip the read-back verify")
    pf.add_argument("--stay-flash", action="store_true",
                    help="leave E740=1 (flash mode) after flashing for chained agent ops; default sets "
                         "E740=0 (NORMAL/drivable) through the running RAM agent, no key-cycle")
    pf.add_argument("--reset-recover", dest="reset_recover", action="store_true",
                    help="recover via the SAME loaded agent's OWN 'R' hybrid SRST -> E740=0 (marker 0); NO 0x2A, "
                         "NO recover_normal. Used by `install`; end state is normal/drivable at marker 0.")
    pf.add_argument("--force", action="store_true", help="flash even if image checksums look invalid")
    pf.add_argument("--assume-half", choices=["B", "T"], default=None,
                    help="skip the cockpit-switch identify and ASSERT the visible half (used by install)")
    pf.add_argument("--cross-bank", dest="cross_bank", action="store_true",
                    help="29F400 GOLDEN-TOP write: enter the agent from the BOTTOM, then (operator-prompted) "
                         "flip A17 -> UPPER and flash the coarse top map (SA7 fused boot LAST), flip back. "
                         "image must be a top golden image (marker 'T'). Booted from the intact bottom = "
                         "recoverable. BRICK-CLASS (writes SA7); bench-prove before trusting. Dry-run first.")
    pf.add_argument("--i-metered-a17", dest="i_metered_a17", action="store_true",
                    help="CROSS-BANK identical-halves (clone) override: the two banks hold the SAME image so "
                         "the marker-change guard can't detect the flip -- BYPASS it and rely on your DVM "
                         "reading of A17=HIGH instead. Use ONLY with a meter physically on A17. After the "
                         "first golden-top write the halves differ ('T' vs 'B') and the normal guard resumes.")
    pf.add_argument("--guard-addr", dest="guard_addr", type=lambda s: int(s, 16), default=None,
                    help="CROSS-BANK flip-guard read address (hex CPU addr; default 0x1FFC = bank marker). "
                         "When the two banks share the marker but DIFFER elsewhere (e.g. door_magic present "
                         "on one, stock on the other), point the guard at that differing byte for a REAL "
                         "software flip-proof instead of the metered override.")
    pf.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    pf.set_defaults(func=cmd_flash)


    args = ap.parse_args()
    try:
        args.func(args)
    except (SoftBSLError, DS2Error) as e:
        raise SystemExit(f"ERROR: {e}")
    except KeyboardInterrupt:
        raise SystemExit("\ninterrupted.")


if __name__ == "__main__":
    main()
