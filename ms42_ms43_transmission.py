"""Source-backed E39 MS42/MS43 conversion through the shared transaction owner."""

import uuid

import transmission_conversion as tx
from ms42_ms43_transmission_profiles import (
    E39_DONORS, E39_EGS_RELEASE_HISTORY, E39_GM_SUFFIXES, E39_US_MANUAL_PROFILES,
)


_STEPTRONIC = 0x0001000000000000
_SHORT_AXLE = 0x0008000000000000
_PROFILES = {**E39_DONORS, **E39_US_MANUAL_PROFILES}
# NCS SIA_LITER_GRENZE: sedan manual/automatic, touring manual/automatic.
_SERVICE_FUEL_LIMITS = {
    "M52B20": (0x08CA, 0x09AB, 0x0960, 0x0A41),
    "M52B25": (0x092E, 0x0A0F, 0x0992, 0x0A73),
    "M52B28": (0x0947, 0x0A41, 0x0992, 0x0A73),
    "M54B22": (0x08CA, 0x09AB, 0x0947, 0x09DD),
    "M54B25": (0x092E, 0x0A0F, 0x0992, 0x0A41),
    "M54B30": (0x0947, 0x0A73, 0x09AB, 0x0AA5),
}


def e39_profile(zcs: tx.Zcs, family: str):
    prefix = zcs.gm[:2].hex().upper()
    for manual, profile in _PROFILES.items():
        if (profile[0] == family and prefix in {manual, profile[4]}
                and zcs.gm[2:] == E39_GM_SUFFIXES.get(manual, b"\0\0")):
            source = (tx.Transmission.MANUAL if prefix == manual
                      else tx.Transmission.AUTOMATIC)
            return manual, profile, source
    raise ValueError(f"{family} E39 vehicle type {zcs.gm.hex().upper()} is not mapped")


def e39_target_order(zcs: tx.Zcs, family: str, target: tx.Transmission) -> tx.Zcs:
    manual, profile, _source = e39_profile(zcs, family)
    automatic = target is tx.Transmission.AUTOMATIC
    if automatic and manual in E39_US_MANUAL_PROFILES:
        raise ValueError(
            "US E39 automatic conversion needs a verified production-date and "
            "gearbox-selector choice; automatic-to-manual conversion is supported")
    sa = int.from_bytes(zcs.sa, "big") & ~_STEPTRONIC
    if automatic:
        sa |= _STEPTRONIC
    return tx.Zcs(bytes.fromhex(profile[4] if automatic else manual) + zcs.gm[2:],
                  sa.to_bytes(8, "big"), zcs.vn)


def e39_egs_admission(zcs: tx.Zcs, family: str) -> tuple[str, frozenset[int]]:
    manual, profile, _source = e39_profile(zcs, family)
    if manual in E39_US_MANUAL_PROFILES:
        raise ValueError("US E39 reverse automatic conversion is not yet defined")
    short = bool(int.from_bytes(zcs.sa, "big") & _SHORT_AXLE)
    release = profile[6] if short and profile[6] is not None else profile[5]
    return ("GS8600" if family == "MS42" else "GS8604",
            E39_EGS_RELEASE_HISTORY[release])


def read_e39_cluster(ds2, *, identity: bytes | None = None,
                      validate: bool = True) -> tx.ClusterStoreState:
    ident, ci = tx._require_ident(ds2, 0x80, 18, (6, 7, 11, 12), identity=identity)
    diagnostic_index = tx._bcd(ident[9])
    if ci in (6, 7) and 30 <= diagnostic_index <= 40:
        mask, automatic, profile = 0xC0, 0x40, "KMB"
    elif ci in (11, 12) and 5 <= diagnostic_index <= 14:
        mask, automatic, profile = 0xE0, 0xC0, "IKE"
    else:
        raise ValueError("this conversion requires a reviewed E39 KMB or KMBI cluster")
    state = tx.ClusterStoreState(
        "E39", ident, ci, 0x2C, tx._read_words(ds2, 0x2C, 0x54),
        140, 180, mask, 0, automatic, 0x2C, 0x2D, 0x7F, 89, profile)
    if validate:
        tx._validate_cluster_checksum(state)
    return state


def e39_cluster_target(state: tx.ClusterStoreState, zcs: tx.Zcs, family: str,
                        target: tx.Transmission) -> bytes:
    _manual, profile, _source = e39_profile(zcs, family)
    if state.profile == "IKE" and state.coding_index == 11 and family == "MS43":
        raise ValueError("KMBI coding index 11 has no reviewed M54 service thresholds")
    output = bytearray(tx.build_ms41_cluster_target(
        state, tx.encode_zcs(e39_target_order(zcs, family, target)), target))
    automatic = target is tx.Transmission.AUTOMATIC
    can_index = tx._cluster_absolute_slice(state, 88, 1).start
    output[can_index] = (output[can_index] & ~0x08) | (0x08 if automatic else 0)
    touring = profile[2][:2] in {"DP", "DR", "DS"}
    fuel_limit = _SERVICE_FUEL_LIMITS[profile[1]][2 * touring + automatic]
    fuel_slice = tx._cluster_absolute_slice(state, 232, 2)
    previous = int.from_bytes(output[fuel_slice], "big")
    output[fuel_slice] = ((previous & 0xF000) | fuel_limit).to_bytes(2, "big")
    tx._cluster_checksum_image(state, output)
    return bytes(output)


def write_e39_cluster(ds2, state: tx.ClusterStoreState, target: bytes,
                      *, restoring: bool = False) -> tx.ClusterStoreState:
    current = read_e39_cluster(ds2, validate=not restoring)
    if current.ident != state.ident or current.coding_index != state.coding_index:
        raise RuntimeError("E39 instrument-cluster identity changed")
    if not restoring and current != state:
        raise RuntimeError("E39 instrument-cluster storage changed since preflight")
    tx._write_cluster_target_words(ds2, state, current.data, target)
    verified = read_e39_cluster(ds2)
    if verified.ident != state.ident or verified.data != bytes(target):
        raise RuntimeError("E39 instrument-cluster storage verification failed")
    return verified


def read_e39_stability(ds2) -> tx.CodingState:
    ident, ci = tx._require_ident(ds2, 0x56, 16, (9, 10))
    name, count = ("ASC57", 1) if ci == 9 else ("DSC57", 4)
    response = tx._positive(ds2, bytes((0x56, 6, 8, 0, count)), 0x56)
    data = response[3:-1]
    if len(data) != count:
        raise ValueError(f"{name} coding response has the wrong length")
    if name == "DSC57" and data[0] != 0xA5:
        raise ValueError("DSC5.7 coding marker is invalid")
    return tx.CodingState(name, ident, ci, data)


def e39_stability_target(state: tx.CodingState, zcs: tx.Zcs, family: str,
                         target: tx.Transmission) -> bytes:
    manual, profile, _source = e39_profile(zcs, family)
    engine = profile[1]
    us = manual in E39_US_MANUAL_PROFILES
    automatic = target is tx.Transmission.AUTOMATIC
    short = bool(int.from_bytes(zcs.sa, "big") & _SHORT_AXLE)
    data = bytearray(state.data)
    if state.name == "ASC57" and state.coding_index == 9 and len(data) == 1:
        if engine.startswith("M52"):
            value = (2 if engine == "M52B20" else 1) if automatic else 0
        else:
            value = 2 if engine == "M54B25" and not (short or us) else 1
        data[0] = (data[0] & ~0xC0) | (value << 6)
    elif state.name == "DSC57" and state.coding_index == 10 and len(data) == 4:
        if engine in {"M52B20", "M54B22"}:
            value = 20 if automatic else 19
        elif not automatic:
            value = 18 if engine in {"M52B25", "M54B25"} else 12
        elif us:
            value = 11
        elif short or profile[2] in {"DM37", "DM59"}:
            value = 11 if engine != "M54B25" else 9
        else:
            value = 10 if engine.startswith("M52") else 9
        # C_DSC57 subtracts bytes 2 and 3 from its checksum basis. Retain the
        # connected module's basis when changing only the vehicle variant.
        data[1] = (data[1] + data[2] - value) & 0xFF
        data[2] = value
    else:
        raise ValueError("this E39 stability-control coding profile is not mapped")
    return bytes(data)


def write_e39_stability(ds2, state: tx.CodingState, target: bytes,
                        *, restoring: bool = False) -> tx.CodingState:
    current = tx._read_addressed_coding(
        ds2, state.name, (state.coding_index,), state.address, len(state.data))
    if current.ident != state.ident or current.coding_index != state.coding_index:
        raise RuntimeError("E39 stability-control identity changed")
    if not restoring and current != state:
        raise RuntimeError("E39 stability-control coding changed since preflight")
    if len(target) != len(state.data):
        raise ValueError("E39 stability-control target has the wrong length")
    if current.data != bytes(target):
        # The BMW C_ASC5 / C_DSC57 jobs identify with command 00 before 09.
        tx._positive(ds2, bytes((0x56, len(target) + 5, 9, state.address)) + target, 0x56)
    verified = tx._read_addressed_coding(
        ds2, state.name, (state.coding_index,), state.address, len(target))
    if verified.ident != state.ident or verified.data != bytes(target):
        raise RuntimeError("E39 stability-control coding verification failed")
    return verified


def prepare_e39_swap(ds2, dme_ident: bytes, program: str, family: str,
                      target: tx.Transmission, ews: tx.ZcsHolderState, *, cluster_identity: bytes | None = None
                      ) -> tx.ConnectedSwapSession:
    zcs = tx.decode_zcs(ews.raw)
    manual, profile, source = e39_profile(zcs, family)
    us = manual in E39_US_MANUAL_PROFILES
    target_zcs = tx.encode_zcs(e39_target_order(zcs, family, target))
    cluster = read_e39_cluster(ds2, identity=cluster_identity)
    if tx.encode_zcs(tx.cluster_zcs(cluster)) != ews.raw:
        raise ValueError("immobilizer and instrument cluster contain different vehicle orders")
    if tx.cluster_transmission(cluster) is not source:
        raise ValueError("instrument-cluster coding disagrees with the vehicle order")
    ews_coding = tx.read_ews_transmission(ds2)
    if ews_coding.ident != ews.ident or ews_coding.coding_index != ews.coding_index:
        raise ValueError("immobilizer identity changed during compatibility checking")
    if (source is tx.Transmission.AUTOMATIC or us) and not tx.ews_starter_interlock_active(ews_coding):
        raise ValueError("this vehicle order requires the immobilizer starter interlock")
    if tx.read_e46_dme_transmission(ds2) is not source:
        raise ValueError("engine-computer active transmission mode disagrees with the vehicle order")
    stability = read_e39_stability(ds2)
    if e39_stability_target(stability, zcs, family, source) != stability.data:
        raise ValueError("stability-control vehicle variant disagrees with the vehicle order")
    stability_target = e39_stability_target(stability, zcs, family, target)

    egs = tx._probe_egs(ds2)
    egs_family, egs_ident = egs if egs else (None, None)
    egs_zb = None
    if target is tx.Transmission.MANUAL and egs is not None:
        raise ValueError("disconnect the automatic-transmission computer before manual coding")
    if target is tx.Transmission.AUTOMATIC:
        expected_family, allowed = e39_egs_admission(zcs, family)
        if egs_family != expected_family:
            raise ValueError(f"this vehicle requires a compatible {expected_family} transmission computer")
        egs_family, egs_ident, egs_zb = tx.read_egs_aif_zb(ds2, family=egs_family, ident=egs_ident)
        if egs_zb not in allowed:
            raise ValueError(f"{egs_family} assembly {egs_zb} is not reviewed for {profile[3]}")
    if source is target:
        return tx._blocked_swap(target, f"Already configured for {target.value}",
                                "The connected modules already agree with the selected transmission.",
                                family=family, chassis="E39", source=source)
    warnings = (("Engine adaptations will relearn after the MS42 reset.",)
                if family == "MS42" else ())
    if target is tx.Transmission.MANUAL:
        warnings += (("US manual coding retains the immobilizer starter-interlock input."
                      if us else "Manual coding disables the immobilizer starter-interlock input."),)
    return tx.ConnectedSwapSession(
        uuid.uuid4().hex, "ready", f"Ready to convert E39 to {target.value}",
        (), warnings, (
            "Update the exact E39 vehicle type and Steptronic option in EWS and the cluster.",
            "Recode the starter interlock, cluster gear display/service threshold and stability variant.",
            "Reset the DME transmission adaptation, then verify after an ignition cycle.",
        ), source, target, family, "E39", dme_ident=dme_ident, program=program,
        order_format=tx.OrderFormat.ZCS, ews_zcs=ews, ews_coding=ews_coding,
        cluster=cluster, stability=stability, target_zcs=target_zcs,
        target_ews_coding=tx.build_ews_transmission_target(
            ews_coding, tx.Transmission.AUTOMATIC if us else target),
        target_cluster=e39_cluster_target(cluster, zcs, family, target),
        target_stability=stability_target, egs_family=egs_family,
        egs_ident=egs_ident, egs_zb=egs_zb,
    )
