"""Offline-first BMW manual/automatic transmission conversion planning.

This module owns no FA/ZCS/EEPROM writer.  It only returns ``READY`` when
callers confirm that every exact codec and write owner required by the plan
exists.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Mapping


AUTOMATIC_OPTION = "$205"

# Exact reset frames for the supported MS42 and MS43 program families:
#   MS42 clear all adaptations -> 12 06 43 FF FF
#   MS43 reset variant adaptation -> 12 06 43 00 01
# The final byte below is the normal DS2 XOR checksum.
MS42_CLEAR_ALL_ADAPTATIONS_FRAME = bytes.fromhex("12 06 43 FF FF 57")
MS43_RESET_VARIANT_ADAPTATION_FRAME = bytes.fromhex("12 06 43 00 01 56")

# The transformation remains owned by engines.softbsl.eeprom_ram.  These are
# planner metadata only; the family suffix never selects an address by itself.
MS41_EEPROM_RECORD_ADDRESS = {
    "MS41.0": 0x196,
    "MS41.1": 0x1CC,
    "MS41.2": 0x1CA,
    "MS41.3": 0x1CA,
}
MS41_TRANSMISSION_FLAG_ADDRESS = {
    "MS41.0": 0xFD4C,
    "MS41.1": 0xFD5C,
    "MS41.2": 0xFD5C,
    "MS41.3": 0xFD5C,
}
MS41_TRANSMISSION_FLAG_MASK = 0x80


class PlanStatus(str, Enum):
    READY = "Ready"
    ACTION_REQUIRED = "Action required"
    UNSUPPORTED = "Unsupported"


class OrderFormat(str, Enum):
    ZCS = "ZCS"
    FA = "FA"


class Transmission(str, Enum):
    MANUAL = "manual"
    AUTOMATIC = "automatic"


class MS41Selector(str, Enum):
    DYNAMIC = "AT/MT (auto)"
    MANUAL_ONLY = "MT Only"
    AUTOMATIC_ONLY = "AT Only"


@dataclass(frozen=True)
class ModuleState:
    """A normalized module observation from the shared coding registry.

    Names are references only. ``profile_exact`` and ``transmission_exact``
    are the explicit evidence gates; a non-empty label never satisfies them.
    ``presence_exact`` distinguishes an observed absence from a failed probe.
    """

    reachable: bool
    profile: str | None = None
    writer_available: bool = False
    reader_available: bool = False
    profile_exact: bool = False
    observed_transmission: Transmission | None = None
    transmission_exact: bool = False
    presence_exact: bool = False


@dataclass(frozen=True)
class OrderCopy:
    """One decoded vehicle-order copy.

    ``codec`` names the built-in codec, while ``codec_exact`` is its explicit
    evidence gate. ``canonical_digest`` is SHA-256 over the complete canonical
    order, not merely its options or holder-specific raw bytes.
    """

    order_format: OrderFormat
    options: frozenset[str]
    codec: str | None = None
    checksum_valid: bool | None = None
    writer_available: bool = False
    canonical_digest: bytes | None = None
    reader_available: bool = False
    codec_exact: bool = False


@dataclass(frozen=True)
class ConversionRequest:
    dme_family: str
    target: Transmission
    production_year: int | None = None
    production_month: int | None = None
    reported_order_format: OrderFormat | None = None
    order_copies: Mapping[str, OrderCopy] = field(default_factory=dict)
    modules: Mapping[str, ModuleState] = field(default_factory=dict)
    egs: ModuleState | None = None
    mechanical_swap_confirmed: bool = False
    chassis: str | None = None


@dataclass(frozen=True)
class ZcsCounterpart:
    """Reviewed GM/SA/VN replacement bound to one exact source identity."""

    target: Transmission
    gm: str
    sa: str
    vn: str
    chassis: str
    dme_family: str
    source_digest: bytes
    source_transmission: Transmission
    relationship_reviewed: bool = False
    profile_exact: bool = False
    checksum_valid: bool | None = None
    writer_available: bool = False


@dataclass(frozen=True)
class MS41ConversionRequest:
    chassis: str
    dme_family: str
    target: Transmission
    counterpart: ZcsCounterpart | None = None
    source_zcs: OrderCopy | None = None
    modules: Mapping[str, ModuleState] = field(default_factory=dict)
    selector: MS41Selector | None = None
    eeprom_transmission: Transmission | None = None
    eeprom_checksum_valid: bool | None = None
    eeprom_writer_available: bool = False
    softbsl_installed: bool | None = None
    egs: ModuleState | None = None
    mechanical_swap_confirmed: bool = False


@dataclass(frozen=True)
class ConversionPlan:
    status: PlanStatus
    title: str
    reasons: tuple[str, ...]
    warnings: tuple[str, ...]
    changes: tuple[str, ...]
    expected_order_format: OrderFormat | None
    updated_options: frozenset[str] | None
    post_coding_frame: bytes | None
    verification_address: int | None = None
    verification_mask: int | None = None

    @property
    def can_write(self) -> bool:
        return self.status is PlanStatus.READY and bool(self.changes)


_ORDER_HOLDERS = {
    OrderFormat.ZCS: ("EWS", "KMB"),
    OrderFormat.FA: ("AKMB", "ALSZ"),
}
_REQUIRED_MODULES = {
    OrderFormat.ZCS: ("DME", "EWS", "KMB", "DSC"),
    OrderFormat.FA: ("DME", "EWS", "AKMB", "ALSZ", "DSC"),
}
_MODULE_LABELS = {
    "DME": "engine computer",
    "EWS": "immobilizer",
    "KMB": "instrument cluster",
    "AKMB": "instrument cluster",
    "ALSZ": "lighting module",
    "DSC": "stability-control module",
    "ASC_DSC": "traction/stability-control module",
    "IKE": "instrument cluster",
}


def expected_order_format(
    dme_family: str,
    production_year: int | None = None,
    production_month: int | None = None,
) -> OrderFormat | None:
    """Classify the exact E46 order era; None means more identity is needed."""
    family = str(dme_family).strip().upper()
    if family == "MS42":
        return OrderFormat.ZCS
    if family != "MS43":
        return None
    if production_year is None or production_month is None:
        return None
    if not 1 <= production_month <= 12:
        raise ValueError("production month must be between 1 and 12")
    return (OrderFormat.FA if (production_year, production_month) >= (2001, 9)
            else OrderFormat.ZCS)


def change_transmission_option(
    options: Iterable[str], target: Transmission
) -> frozenset[str]:
    """Add/remove the exact E46 automatic-transmission option, preserving all else."""
    target = Transmission(target)
    updated = {str(option).strip().upper() for option in options}
    if not all(updated):
        raise ValueError("vehicle-order options cannot be empty")
    if target is Transmission.AUTOMATIC:
        updated.add(AUTOMATIC_OPTION)
    else:
        updated.discard(AUTOMATIC_OPTION)
    return frozenset(updated)


def post_coding_frame(dme_family: str) -> bytes:
    """Return the exact proven DME post-coding telegram; never writes by itself."""
    family = str(dme_family).strip().upper()
    if family == "MS42":
        return MS42_CLEAR_ALL_ADAPTATIONS_FRAME
    if family == "MS43":
        return MS43_RESET_VARIANT_ADAPTATION_FRAME
    raise ValueError(f"unsupported DME family: {dme_family}")


def _plan(
    status: PlanStatus,
    title: str,
    reasons: list[str],
    warnings: list[str],
    changes: list[str],
    order_format: OrderFormat | None,
    options: frozenset[str] | None = None,
    frame: bytes | None = None,
    verification_address: int | None = None,
    verification_mask: int | None = None,
) -> ConversionPlan:
    return ConversionPlan(
        status, title, tuple(reasons), tuple(warnings), tuple(changes),
        order_format, options, frame, verification_address, verification_mask,
    )


def _is_sha256(value: object) -> bool:
    return isinstance(value, bytes) and len(value) == 32


def _normalized_options(options: Iterable[str]) -> frozenset[str] | None:
    try:
        values = tuple(options)
    except TypeError:
        return None
    if not values or not all(isinstance(value, str) and value.strip() for value in values):
        return None
    return frozenset(value.strip().upper() for value in values)


def _check_module(
    name: str,
    state: ModuleState | None,
    current: Transmission,
    require_writer: bool,
    reasons: list[str],
    unsupported: list[str],
) -> None:
    label = _MODULE_LABELS[name]
    if not isinstance(state, ModuleState) or state.reachable is not True:
        reasons.append(f"Connect to the {label} ({name}).")
        return
    if state.reader_available is not True:
        reasons.append(f"The exact {label} ({name}) reader is not available.")
    if (not isinstance(state.profile, str) or not state.profile.strip()
            or state.profile_exact is not True):
        unsupported.append(f"The {label} ({name}) coding version is not exactly supported.")
    if state.transmission_exact is not True:
        reasons.append(f"Read the decoded transmission state from the {label} ({name}).")
    else:
        try:
            observed = Transmission(state.observed_transmission)
        except (TypeError, ValueError):
            unsupported.append(f"The {label} ({name}) returned an unknown transmission state.")
        else:
            if observed is not current:
                unsupported.append(
                    f"The {label} ({name}) reports {observed.value}, but the vehicle order "
                    f"reports {current.value}."
                )
    if require_writer and state.writer_available is not True:
        reasons.append(f"The exact {label} ({name}) writer is not available.")


def _check_egs(
    state: ModuleState | None,
    target: Transmission,
    reasons: list[str],
    unsupported: list[str],
) -> None:
    if (not isinstance(state, ModuleState) or state.presence_exact is not True
            or type(state.reachable) is not bool):
        reasons.append("Observe the automatic-transmission computer (EGS) presence exactly.")
        return
    if target is Transmission.MANUAL:
        if state.reachable:
            reasons.append("The EGS is still communicating; disconnect it after the mechanical swap.")
        return
    if not state.reachable:
        reasons.append("An automatic conversion requires a compatible, communicating EGS.")
        return
    if state.reader_available is not True:
        reasons.append("The exact EGS identity reader is not available.")
    if (not isinstance(state.profile, str) or not state.profile.strip()
            or state.profile_exact is not True):
        unsupported.append("The observed EGS is not an exact supported transmission profile.")
    if state.transmission_exact is not True:
        reasons.append("Read the decoded transmission state from the EGS.")
        return
    try:
        observed = Transmission(state.observed_transmission)
    except (TypeError, ValueError):
        unsupported.append("The EGS returned an unknown transmission state.")
    else:
        if observed is not Transmission.AUTOMATIC:
            unsupported.append("The observed EGS is not an automatic-transmission module.")


def plan_e46_conversion(request: ConversionRequest) -> ConversionPlan:
    """Build a humanized, non-writing conversion plan from connected-car facts."""
    family = str(request.dme_family).strip().upper()
    chassis = (request.chassis.strip().upper()
               if isinstance(request.chassis, str) else "")
    try:
        target = Transmission(request.target)
    except (TypeError, ValueError):
        return _plan(PlanStatus.UNSUPPORTED, "Unsupported transmission target",
                     [f"Transmission target {request.target!r} is not supported."],
                     [], [], None)

    if chassis != "E46":
        return _plan(PlanStatus.UNSUPPORTED, "Exact E46 identity required",
                     ["Confirm that the connected chassis is exactly E46 before planning."],
                     [], [], None)
    if family not in {"MS42", "MS43"}:
        return _plan(PlanStatus.UNSUPPORTED, "This engine computer is not supported",
                     ["E46 conversion currently supports exact MS42 and MS43 paths only."],
                     [], [], None)

    try:
        order_format = expected_order_format(
            family, request.production_year, request.production_month)
    except ValueError as error:
        return _plan(PlanStatus.UNSUPPORTED, "Invalid production date",
                     [str(error)], [], [], None)

    if order_format is None:
        return _plan(PlanStatus.ACTION_REQUIRED, "Production date required",
                     ["Read the vehicle production month before choosing the MS43 ZCS/FA path."],
                     [], [], None)
    if request.reported_order_format is None:
        return _plan(PlanStatus.ACTION_REQUIRED, "Vehicle order must be read first",
                     [f"Read the {order_format.value} from both vehicle-order holders."],
                     [], [], order_format)
    try:
        reported_order_format = OrderFormat(request.reported_order_format)
    except (TypeError, ValueError):
        return _plan(PlanStatus.UNSUPPORTED, "Vehicle-order format is not supported",
                     [f"Unknown vehicle-order format: {request.reported_order_format!r}."],
                     [], [], order_format)
    if reported_order_format is not order_format:
        return _plan(PlanStatus.UNSUPPORTED, "Vehicle identity does not agree",
                     [f"This {family} build date requires {order_format.value}, but the car reported "
                      f"{reported_order_format.value}. Nothing will be written."],
                     [], [], order_format)

    reasons: list[str] = []
    unsupported: list[str] = []
    warnings: list[str] = []
    changes: list[str] = []
    holders = _ORDER_HOLDERS[order_format]
    copies: dict[str, OrderCopy] = {}

    for holder in holders:
        copy = request.order_copies.get(holder)
        if not isinstance(copy, OrderCopy):
            reasons.append(f"Read the {order_format.value} from {holder}.")
            continue
        copies[holder] = copy
        try:
            copy_format = OrderFormat(copy.order_format)
        except (TypeError, ValueError):
            unsupported.append(f"{holder} returned an unknown vehicle-order format.")
            continue
        if copy_format is not order_format:
            unsupported.append(f"{holder} returned {copy_format.value}, not {order_format.value}.")
        if copy.reader_available is not True:
            reasons.append(f"The exact {holder} {order_format.value} reader is not available.")
        if (not isinstance(copy.codec, str) or not copy.codec.strip()
                or copy.codec_exact is not True):
            unsupported.append(
                f"No exact built-in {order_format.value} codec is available for {holder}."
            )
        if not _is_sha256(copy.canonical_digest):
            unsupported.append(
                f"{holder} has no exact canonical full-order SHA-256 identity."
            )
        if copy.checksum_valid is not True:
            reasons.append(
                f"{holder} {order_format.value} checksum is invalid."
                if copy.checksum_valid is False else
                f"{holder} {order_format.value} checksum has not been validated."
            )

    if len(copies) != len(holders):
        status = PlanStatus.UNSUPPORTED if unsupported else PlanStatus.ACTION_REQUIRED
        title = ("Vehicle-order decode is not supported" if unsupported else
                 "Both vehicle-order copies are required")
        return _plan(status, title,
                     unsupported + reasons, warnings, changes, order_format)

    first, second = (copies[holder] for holder in holders)
    if (_is_sha256(first.canonical_digest) and _is_sha256(second.canonical_digest)
            and first.canonical_digest != second.canonical_digest):
        unsupported.append(
            f"{holders[0]} and {holders[1]} contain different complete "
            f"{order_format.value} identities."
        )

    option_sets = [_normalized_options(copies[holder].options) for holder in holders]
    if None in option_sets:
        unsupported.append("A decoded vehicle order contains invalid or empty options.")
        current_options = frozenset()
    else:
        current_options = option_sets[0]
        if option_sets[0] != option_sets[1]:
            unsupported.append(
                f"{holders[0]} and {holders[1]} contain inconsistent decoded options."
            )
    if unsupported:
        return _plan(PlanStatus.UNSUPPORTED, "Vehicle identity does not agree",
                     unsupported + reasons, warnings, changes, order_format)

    updated_options = change_transmission_option(current_options, target)
    current = (Transmission.AUTOMATIC if AUTOMATIC_OPTION in current_options
               else Transmission.MANUAL)
    converting = current is not target

    for name in _REQUIRED_MODULES[order_format]:
        _check_module(
            name, request.modules.get(name), current, converting,
            reasons, unsupported,
        )
    _check_egs(request.egs, target, reasons, unsupported)

    if request.mechanical_swap_confirmed is not True:
        reasons.append(f"Confirm that the {target.value} gearbox and required wiring are installed.")

    if not converting:
        if unsupported:
            return _plan(PlanStatus.UNSUPPORTED, "Connected coding does not agree",
                         unsupported + reasons, warnings, changes, order_format,
                         current_options)
        status = PlanStatus.ACTION_REQUIRED if reasons else PlanStatus.READY
        title = ("Hardware check required" if reasons else
                 f"Already configured for a {target.value} gearbox")
        return _plan(status, title, reasons, warnings, changes, order_format,
                     current_options)

    for holder in holders:
        copy = copies[holder]
        if copy.writer_available is not True:
            reasons.append(f"The exact {holder} {order_format.value} writer is not available.")

    action = "Add" if target is Transmission.AUTOMATIC else "Remove"
    changes.append(
        f"{action} Automatic transmission ({AUTOMATIC_OPTION}) in the "
        f"{order_format.value} stored by {' and '.join(holders)}."
    )
    changes.append(
        f"Recode the immobilizer, instrument cluster, and stability control "
        f"for a {target.value} gearbox."
    )
    frame = post_coding_frame(family)
    if family == "MS42":
        changes.append("Clear all MS42 engine adaptations after coding.")
        warnings.append(
            "MS42 has no separate learned-variant reset; idle, fuel, throttle, "
            "and other engine adaptations will relearn."
        )
    else:
        changes.append("Reset only the MS43 learned transmission variant.")

    if unsupported:
        return _plan(PlanStatus.UNSUPPORTED, "This car cannot be converted yet",
                     unsupported + reasons, warnings, changes, order_format,
                     updated_options, frame)
    if reasons:
        return _plan(PlanStatus.ACTION_REQUIRED, "Resolve these items before coding",
                     reasons, warnings, changes, order_format, updated_options, frame)
    return _plan(PlanStatus.READY, f"Ready to convert to {target.value}", [],
                 warnings, changes, order_format, updated_options, frame)


def plan_ms41_conversion(request: MS41ConversionRequest) -> ConversionPlan:
    """Plan an MS41 conversion without performing a partial vehicle write."""
    chassis = request.chassis.strip().upper() if isinstance(request.chassis, str) else ""
    family = request.dme_family.strip().upper() if isinstance(request.dme_family, str) else ""
    try:
        target = Transmission(request.target)
    except (TypeError, ValueError):
        return _plan(PlanStatus.UNSUPPORTED, "Unsupported transmission target",
                     [f"Transmission target {request.target!r} is not supported."],
                     [], [], OrderFormat.ZCS)

    if chassis not in {"E36", "E39"}:
        return _plan(PlanStatus.UNSUPPORTED, "This MS41 vehicle is not supported",
                     ["The current exact MS41 conversion paths cover E36 and E39 only."],
                     [], [], OrderFormat.ZCS)
    if family not in MS41_EEPROM_RECORD_ADDRESS:
        return _plan(PlanStatus.UNSUPPORTED, "Exact MS41 family required",
                     ["Identify MS41.0, MS41.1, MS41.2, or MS41.3 before planning."],
                     [], [], OrderFormat.ZCS)

    reasons: list[str] = []
    unsupported: list[str] = []
    warnings: list[str] = []
    changes: list[str] = []
    verification_address = MS41_TRANSMISSION_FLAG_ADDRESS[family]
    source_transmission: Transmission | None = None

    source_zcs = request.source_zcs
    if not isinstance(source_zcs, OrderCopy):
        reasons.append("Read the exact connected source ZCS before selecting a counterpart.")
    else:
        try:
            source_format = OrderFormat(source_zcs.order_format)
        except (TypeError, ValueError):
            source_format = None
        if source_format is not OrderFormat.ZCS:
            unsupported.append("The connected source identity is not an exact ZCS decode.")
        if source_zcs.reader_available is not True:
            reasons.append("The exact connected-car ZCS reader is not available.")
        if (not isinstance(source_zcs.codec, str) or not source_zcs.codec.strip()
                or source_zcs.codec_exact is not True):
            unsupported.append("No exact built-in codec decoded the connected source ZCS.")
        if not _is_sha256(source_zcs.canonical_digest):
            unsupported.append("The connected source ZCS has no canonical full-order SHA-256 identity.")
        if source_zcs.checksum_valid is not True:
            reasons.append(
                "The connected source ZCS checksum is invalid."
                if source_zcs.checksum_valid is False else
                "Validate the connected source ZCS checksum before coding."
            )
        source_options = _normalized_options(source_zcs.options)
        if source_options is None:
            unsupported.append("The connected source ZCS contains invalid or empty options.")
        else:
            source_transmission = (
                Transmission.AUTOMATIC if AUTOMATIC_OPTION in source_options
                else Transmission.MANUAL
            )

    counterpart = request.counterpart
    if counterpart is None:
        unsupported.append(
            "No exact reviewed GM/SA/VN counterpart is available for this vehicle."
        )
    else:
        try:
            counterpart_target = Transmission(counterpart.target)
        except (TypeError, ValueError):
            counterpart_target = None
        try:
            counterpart_source = Transmission(counterpart.source_transmission)
        except (TypeError, ValueError):
            counterpart_source = None
        if counterpart_target is not target:
            unsupported.append("The selected GM/SA/VN counterpart is for another gearbox.")
        if counterpart_source is None:
            unsupported.append("The reviewed counterpart has no exact source transmission state.")
        elif counterpart_source is target:
            unsupported.append("The reviewed counterpart does not describe a transmission conversion.")
        if str(counterpart.chassis).strip().upper() != chassis:
            unsupported.append("The reviewed counterpart is for another chassis.")
        if str(counterpart.dme_family).strip().upper() != family:
            unsupported.append("The reviewed counterpart is for another DME family.")
        if counterpart.relationship_reviewed is not True:
            unsupported.append("The source-to-target ZCS relationship has not been reviewed exactly.")
        if counterpart.profile_exact is not True or not all(
                isinstance(value, str) and value.strip()
                for value in (counterpart.gm, counterpart.sa, counterpart.vn)):
            unsupported.append("The target GM/SA/VN counterpart is incomplete or not exact.")
        if not _is_sha256(counterpart.source_digest):
            unsupported.append("The counterpart has no exact canonical source ZCS identity.")
        elif (isinstance(source_zcs, OrderCopy)
              and _is_sha256(source_zcs.canonical_digest)
              and counterpart.source_digest != source_zcs.canonical_digest):
            unsupported.append("The counterpart was reviewed for a different source ZCS.")
        if (source_transmission is not None and counterpart_source is not None
                and counterpart_source is not source_transmission):
            unsupported.append(
                f"The counterpart starts from {counterpart_source.value}, but the connected "
                f"ZCS reports {source_transmission.value}."
            )
        if counterpart.checksum_valid is not True:
            reasons.append(
                "The GM/SA/VN counterpart checksum is invalid."
                if counterpart.checksum_valid is False else
                "Validate the GM/SA/VN counterpart checksum before coding."
            )
        if counterpart.writer_available is not True:
            reasons.append("The exact GM/SA/VN writer is not available.")

    required_modules = (("DME", "EWS", "ASC_DSC", "IKE") if chassis == "E39"
                        else ("DME", "EWS", "ASC_DSC"))
    if source_transmission is not None:
        for name in required_modules:
            _check_module(
                name, request.modules.get(name), source_transmission, True,
                reasons, unsupported,
            )
    else:
        reasons.append("Module transmission states cannot be compared until source ZCS is exact.")
    _check_egs(request.egs, target, reasons, unsupported)
    if request.mechanical_swap_confirmed is not True:
        reasons.append(f"Confirm that the {target.value} gearbox and required wiring are installed.")

    try:
        selector = MS41Selector(request.selector) if request.selector is not None else None
    except (TypeError, ValueError):
        selector = None
        unsupported.append(f"Unknown MS41 transmission selector: {request.selector!r}.")

    if selector is None:
        if request.selector is None:
            reasons.append("Read the exact DME calibration transmission selector.")
    elif selector is MS41Selector.DYNAMIC:
        if request.eeprom_checksum_valid is not True:
            reasons.append(
                f"The {family} EEPROM transmission record checksum is invalid."
                if request.eeprom_checksum_valid is False else
                f"Validate the {family} EEPROM transmission record before coding."
            )
        try:
            eeprom_transmission = (
                Transmission(request.eeprom_transmission)
                if request.eeprom_transmission is not None else None
            )
        except (TypeError, ValueError):
            eeprom_transmission = None
            unsupported.append("The EEPROM transmission value is not recognized.")

        if eeprom_transmission is None:
            reasons.append("Read the current transmission value from the EEPROM record.")
        elif eeprom_transmission is not target:
            address = MS41_EEPROM_RECORD_ADDRESS[family]
            changes.append(
                f"Update bits 0-1 in the {family} EEPROM record at 0x{address:03X} "
                f"for a {target.value} gearbox, preserving bits 2-15 and rebuilding "
                "only that record's additive check."
            )
            if request.softbsl_installed is not True:
                reasons.append(
                    "Installed Soft-BSL is required for the dynamic EEPROM change."
                    if request.softbsl_installed is False else
                    "Confirm that Soft-BSL is installed before the dynamic EEPROM change."
                )
            if request.eeprom_writer_available is not True:
                reasons.append("The exact family-specific EEPROM writer is not available.")
            warnings.append(
                "MT Only or AT Only will not be substituted when the dynamic EEPROM "
                "path is unavailable."
            )
    else:
        fixed_transmission = (
            Transmission.MANUAL if selector is MS41Selector.MANUAL_ONLY
            else Transmission.AUTOMATIC
        )
        if fixed_transmission is not target:
            unsupported.append(
                f"The calibration selector is fixed for a {fixed_transmission.value} "
                "gearbox. This planner will not use another fixed selector as a fallback."
            )
        else:
            warnings.append(
                f"The DME calibration is already fixed for a {target.value} gearbox; "
                "the EEPROM transmission record is not used."
            )

    holders = "EWS and IKE" if chassis == "E39" else "EWS and the Concept-1 cluster"
    changes.insert(
        0,
        f"Write the exact {target.value} GM/SA/VN counterpart to {holders}.",
    )
    changes.append(
        f"Recode EWS, ASC/DSC, {'IKE, ' if chassis == 'E39' else ''}and DME "
        f"for a {target.value} gearbox."
    )
    changes.append(
        f"Key-cycle and verify XRAM 0x{verification_address:04X} bit 7: "
        "set means automatic and clear means manual."
    )

    if chassis == "E36":
        reasons.append(
            "The E36 Concept-1 cluster requires an external ADS step; this tool "
            "must not claim a complete K-line-only conversion."
        )

    if unsupported:
        return _plan(PlanStatus.UNSUPPORTED, "This MS41 car cannot be converted yet",
                     unsupported + reasons, warnings, changes, OrderFormat.ZCS,
                     verification_address=verification_address,
                     verification_mask=MS41_TRANSMISSION_FLAG_MASK)
    if reasons:
        title = ("External ADS step required" if chassis == "E36" else
                 "Resolve these items before coding")
        return _plan(PlanStatus.ACTION_REQUIRED, title, reasons, warnings, changes,
                     OrderFormat.ZCS, verification_address=verification_address,
                     verification_mask=MS41_TRANSMISSION_FLAG_MASK)
    return _plan(PlanStatus.READY, f"Ready to convert {chassis} to {target.value}",
                 [], warnings, changes, OrderFormat.ZCS,
                 verification_address=verification_address,
                 verification_mask=MS41_TRANSMISSION_FLAG_MASK)
