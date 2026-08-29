"""Self-contained BMW manual/automatic transmission conversion support."""

from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass
from enum import Enum
from hashlib import sha256
import json
import os
from pathlib import Path
import time
import uuid
from typing import Iterable, Mapping

from transmission_swap_journal import (
    ensure_directory_durable,
    write_new_file_durably,
)


AUTOMATIC_OPTION = "$205"

# Exact post-coding DME telegrams.  The final byte is the DS2 XOR checksum.
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

_BASE36 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_FA_SECTION_WIDTHS = {8: 3, 4: 4, 12: 4}


@dataclass(frozen=True)
class Zcs:
    gm: bytes
    sa: bytes
    vn: bytes

    @property
    def automatic(self) -> bool:
        return bool(int.from_bytes(self.sa, "big") & 0x08)

    def with_automatic(self, automatic: bool) -> "Zcs":
        value = int.from_bytes(self.sa, "big")
        value = value | 0x08 if automatic else value & ~0x08
        return Zcs(self.gm, value.to_bytes(8, "big"), self.vn)


@dataclass(frozen=True)
class FaV2:
    date: str
    chassis: str
    vehicle_type: str
    paint: str
    upholstery: str
    assemblies: tuple[str, str, str]
    sa: tuple[str, ...]
    e_words: tuple[str, ...]
    ho_words: tuple[str, ...]

    @property
    def automatic(self) -> bool:
        return "205" in self.sa

    def with_automatic(self, automatic: bool) -> "FaV2":
        sa = tuple(value for value in self.sa if value != "205")
        if automatic:
            # FA SA words are stored in canonical ascending order.  Preserve
            # that order when inserting the automatic-transmission option.
            sa = tuple(sorted(sa + ("205",)))
        return FaV2(
            self.date, self.chassis, self.vehicle_type, self.paint,
            self.upholstery, self.assemblies, sa, self.e_words, self.ho_words,
        )


def _xor(data: bytes) -> int:
    value = 0
    for byte in data:
        value ^= byte
    return value


def _frame(body: bytes) -> bytes:
    body = bytes(body)
    return body + bytes((_xor(body),))


def _zcs_check(prefix: str, data: bytes) -> str:
    text = prefix + data.hex().upper()
    value = sum(_BASE36.index(char) * (3 if index % 2 == 0 else 1)
                for index, char in enumerate(text)) % 36
    return _BASE36[value]


def decode_zcs(raw: bytes) -> Zcs:
    """Decode and validate the exact 20-byte GM/SA/VN representation."""
    raw = bytes(raw)
    if len(raw) != 20:
        raise ValueError("ZCS must contain exactly 20 bytes")
    parts = (("C1", raw[0:4], raw[4]),
             ("C2", raw[5:13], raw[13]),
             ("C3", raw[14:19], raw[19]))
    for prefix, data, check in parts:
        expected = ord(_zcs_check(prefix, data))
        if check != expected:
            raise ValueError(
                f"invalid {prefix} ZCS check: 0x{check:02X} != 0x{expected:02X}")
    return Zcs(parts[0][1], parts[1][1], parts[2][1])


def encode_zcs(value: Zcs) -> bytes:
    if not isinstance(value, Zcs) or tuple(map(len, (value.gm, value.sa, value.vn))) != (4, 8, 5):
        raise ValueError("ZCS fields must be 4-byte GM, 8-byte SA, and 5-byte VN")
    return b"".join((
        value.gm, _zcs_check("C1", value.gm).encode("ascii"),
        value.sa, _zcs_check("C2", value.sa).encode("ascii"),
        value.vn, _zcs_check("C3", value.vn).encode("ascii"),
    ))


def _fa_char_type(char: str) -> tuple[int, int]:
    if "0" <= char <= "9":
        return 1, ord(char) - ord("0")
    if "A" <= char <= "O":
        return 2, ord(char) - ord("A") + 1
    if "P" <= char <= "Z":
        return 3, ord(char) - ord("P")
    raise ValueError(f"unsupported vehicle-order character {char!r}")


def _fa_value_char(kind: int, value: int) -> str:
    if kind == 1 and 0 <= value <= 9:
        return chr(ord("0") + value)
    if kind == 2 and 1 <= value <= 15:
        return chr(ord("A") + value - 1)
    if kind == 3 and 0 <= value <= 10:
        return chr(ord("P") + value)
    raise ValueError("invalid vehicle-order character encoding")


def _pack_6bit(values: Iterable[int]) -> bytes:
    bits = "".join(f"{value:06b}" for value in values)
    bits += "0" * (-len(bits) % 8)
    packed = bytes(int(bits[index:index + 8], 2)
                   for index in range(0, len(bits), 8))
    return packed if packed[-1:] == b"\x00" else packed + b"\x00"


def _unpack_6bit(raw: bytes) -> list[int]:
    bits = "".join(f"{byte:08b}" for byte in raw)
    return [int(bits[index:index + 6], 2)
            for index in range(0, len(bits) - 5, 6)]


def decode_fa_v2(raw: bytes) -> FaV2:
    """Decode the exact E46 version-2 vehicle-order stream."""
    raw = bytes(raw)
    if not raw or raw[0] != 2:
        raise ValueError("only version-2 E46 vehicle orders are supported")
    values = _unpack_6bit(raw[1:])
    if len(values) < 42:
        raise ValueError("vehicle-order stream is truncated")
    try:
        fixed = "".join(chr(value + 0x20) for value in values[:41])
    except ValueError as error:
        raise ValueError("invalid fixed vehicle-order data") from error
    if any(not 0x20 <= ord(char) <= 0x5F for char in fixed):
        raise ValueError("invalid fixed vehicle-order character")

    index = 41
    sections: dict[int, tuple[str, ...]] = {8: (), 4: (), 12: ()}
    seen: set[int] = set()
    while True:
        if index >= len(values):
            raise ValueError("vehicle-order stream has no terminator")
        header = values[index]
        index += 1
        kind, char_type = header >> 2, header & 3
        if kind == 0 and char_type == 0:
            break
        width = _FA_SECTION_WIDTHS.get(kind)
        if width is None or kind in seen or char_type == 0:
            raise ValueError("unsupported vehicle-order section")
        seen.add(kind)
        text = ""
        while char_type:
            if index >= len(values):
                raise ValueError("vehicle-order section is truncated")
            encoded = values[index]
            index += 1
            text += _fa_value_char(char_type, encoded >> 2)
            char_type = encoded & 3
        if not text or len(text) % width:
            raise ValueError("invalid vehicle-order section length")
        sections[kind] = tuple(
            text[offset:offset + width]
            for offset in range(0, len(text), width)
        )

    if any(values[index:]) or any(raw[-1:]):
        raise ValueError("vehicle-order stream has non-zero trailing data")
    assemblies = tuple(fixed[offset:offset + 7] for offset in (20, 27, 34))
    if not (fixed[:4].isdigit() and fixed[4:8] == "E46_"
            and all(value == "0000000" or len(value) == 7 for value in assemblies)):
        raise ValueError("vehicle order is not an exact E46 version-2 identity")
    month, year = int(fixed[:2]), int(fixed[2:4])
    if not 1 <= month <= 12 or (year, month) < (1, 9):
        raise ValueError("vehicle order predates the supported E46 FA route")
    return FaV2(
        fixed[:4], fixed[4:8], fixed[8:12], fixed[12:16], fixed[16:20],
        assemblies, sections[8], sections[4], sections[12],
    )


def encode_fa_v2(value: FaV2) -> bytes:
    if not isinstance(value, FaV2):
        raise ValueError("an exact version-2 vehicle order is required")
    fixed = (value.date + value.chassis + value.vehicle_type + value.paint
             + value.upholstery + "".join(value.assemblies))
    if len(fixed) != 41 or any(not 0x20 <= ord(char) <= 0x5F for char in fixed):
        raise ValueError("invalid fixed vehicle-order fields")
    values = [ord(char) - 0x20 for char in fixed]
    for kind, tokens in ((8, value.sa), (4, value.e_words), (12, value.ho_words)):
        if not tokens:
            continue
        width = _FA_SECTION_WIDTHS[kind]
        if any(len(token) != width for token in tokens):
            raise ValueError("invalid vehicle-order token width")
        text = "".join(tokens)
        first_type, _ = _fa_char_type(text[0])
        values.append((kind << 2) | first_type)
        for index, char in enumerate(text):
            _char_type, char_value = _fa_char_type(char)
            next_type = _fa_char_type(text[index + 1])[0] if index + 1 < len(text) else 0
            values.append((char_value << 2) | next_type)
    values.append(0)
    return b"\x02" + _pack_6bit(values)


@dataclass(frozen=True)
class FaHolderState:
    name: str
    ident: bytes
    coding_index: int | None
    before: bytes
    stream: bytes
    after: bytes


def _bcd(byte: int) -> int:
    high, low = byte >> 4, byte & 0x0F
    if high > 9 or low > 9:
        raise ValueError(f"invalid coding-index BCD 0x{byte:02X}")
    return high * 10 + low


def _positive(ds2, frame: bytes, address: int, *, retries: int = 1,
              delay: float = 0.0) -> bytes:
    from ds2 import DS2Error

    for attempt in range(retries):
        response = bytes(ds2.send_frame(_frame(frame), resp_addr=address, timeout=2.0))
        if len(response) >= 4 and response[2] == 0xA0:
            return response
        if len(response) >= 4 and response[2] == 0xA1 and attempt + 1 < retries:
            if delay:
                time.sleep(delay)
            continue
        status = response[2] if len(response) >= 3 else -1
        raise DS2Error(
            f"module 0x{address:02X} rejected request with status 0x{status & 0xFF:02X}")
    raise AssertionError("unreachable")


def _identify_c6(ds2, address: int, *payload: int) -> bytes:
    return _positive(
        ds2, bytes((address, len(payload) + 4, 0x00, *payload)),
        address, retries=4, delay=1.0,
    )


def _extract_fa(region: bytes) -> tuple[bytes, FaV2]:
    region = bytes(region)
    if region[:1] != b"\x02":
        raise ValueError("vehicle-order region does not start with version 2")
    for end in range(33, len(region) + 1):
        if region[end - 1] != 0:
            continue
        candidate = region[:end]
        try:
            decoded = decode_fa_v2(candidate)
        except ValueError:
            continue
        return candidate, decoded
    raise ValueError("no exact version-2 vehicle order was found")


def _prefix_after(before: bytes, stream: bytes, block_size: int) -> bytes:
    if len(stream) > len(before):
        raise ValueError("vehicle order exceeds holder capacity")
    written = ((len(stream) + block_size - 1) // block_size) * block_size
    return stream.ljust(written, b"\xFF") + bytes(before)[written:]


def _akmb_geometry(coding_index: int) -> tuple[int, int, int, int, int]:
    if coding_index < 7 or 20 <= coding_index <= 22:
        return 0x08, 0x10, 0x20, 0x20, 13
    return 0x03, 0xC2, 0x10, 0x10, 12


def _read_akmb_region(ds2) -> tuple[bytes, int, bytes]:
    ident = _identify_c6(ds2, 0x80)
    if len(ident) < 10:
        raise ValueError("instrument-cluster identification is too short")
    ci = _bcd(ident[8])
    selector, start, step, count, blocks = _akmb_geometry(ci)
    result = bytearray()
    for index in range(blocks):
        address = start + index * step
        response = _positive(ds2, bytes((
            0x80, 0x09, 0x06, selector, 0x00,
            address >> 8, address & 0xFF, count,
        )), 0x80)
        data = response[3:-1]
        if len(data) != 32:
            raise ValueError("instrument-cluster vehicle-order block has the wrong length")
        result.extend(data)
    return ident, ci, bytes(result)


def read_akmb_fa(ds2) -> FaHolderState:
    ident, ci, before = _read_akmb_region(ds2)
    stream, _decoded = _extract_fa(before)
    return FaHolderState("AKMB", ident, ci, before, stream, before)


def write_akmb_fa(ds2, state: FaHolderState, stream: bytes) -> FaHolderState:
    ident, ci, before = _read_akmb_region(ds2)
    if (ident != state.ident or ci != state.coding_index
            or before != state.before):
        raise RuntimeError("instrument-cluster vehicle order changed since preflight")
    if ci is None:
        raise ValueError("instrument-cluster coding index is unavailable")
    selector, start, step, count, _blocks = _akmb_geometry(ci)
    after = _prefix_after(state.before, bytes(stream), 32)
    blocks = (len(stream) + 31) // 32
    for index in range(blocks):
        address = start + index * step
        data = after[index * 32:(index + 1) * 32]
        _positive(ds2, bytes((
            0x80, 0x29, 0x07, selector, 0x00,
            address >> 8, address & 0xFF, count,
        )) + data, 0x80)
    time.sleep(1.0)
    verified = read_akmb_fa(ds2)
    if verified.before != after or verified.stream != bytes(stream):
        raise RuntimeError("instrument-cluster vehicle-order verification failed")
    return FaHolderState(
        state.name, verified.ident, verified.coding_index,
        state.before, verified.stream, verified.before,
    )


def _read_alsz_region(ds2) -> tuple[bytes, bytes]:
    ident = _identify_c6(ds2, 0xD0)
    result = bytearray()
    for index in range(18):
        response = _positive(
            ds2, bytes((0xD0, 0x05, 0x15, index)),
            0xD0, retries=16, delay=0.025,
        )
        data = response[3:-1]
        if len(data) != 16:
            raise ValueError("lighting-module vehicle-order block has the wrong length")
        result.extend(data)
    return ident, bytes(result)


def read_alsz_fa(ds2) -> FaHolderState:
    ident, before = _read_alsz_region(ds2)
    stream, _decoded = _extract_fa(before)
    return FaHolderState("ALSZ", ident, None, before, stream, before)


def write_alsz_fa(ds2, state: FaHolderState, stream: bytes) -> FaHolderState:
    ident, before = _read_alsz_region(ds2)
    if ident != state.ident or before != state.before:
        raise RuntimeError("lighting-module vehicle order changed since preflight")
    if len(stream) > 0x11F:
        raise ValueError("vehicle order exceeds lighting-module capacity")
    after = _prefix_after(state.before, bytes(stream), 16)
    blocks = (len(stream) + 15) // 16
    for index in range(blocks):
        data = after[index * 16:(index + 1) * 16]
        _positive(
            ds2, bytes((0xD0, 0x15, 0x16, index)) + data,
            0xD0, retries=16, delay=0.025,
        )
    time.sleep(1.0)
    verified = read_alsz_fa(ds2)
    if verified.before != after or verified.stream != bytes(stream):
        raise RuntimeError("lighting-module vehicle-order verification failed")
    return FaHolderState(
        state.name, verified.ident, None, state.before,
        verified.stream, verified.before,
    )


def restore_fa_holder(ds2, state: FaHolderState) -> None:
    """Restore every archived holder block, then require exact equality."""
    if state.name == "AKMB":
        ident, _current_ci, _before = _read_akmb_region(ds2)
        ci = state.coding_index
        if ident != state.ident or ci is None:
            raise RuntimeError("instrument-cluster identity changed during recovery")
        selector, start, step, count, _blocks = _akmb_geometry(ci)
        for index in range(len(state.before) // 32):
            address = start + index * step
            _positive(ds2, bytes((
                0x80, 0x29, 0x07, selector, 0x00,
                address >> 8, address & 0xFF, count,
            )) + state.before[index * 32:(index + 1) * 32], 0x80)
        time.sleep(1.0)
        if _read_akmb_region(ds2)[2] != state.before:
            raise RuntimeError("instrument-cluster recovery verification failed")
        return
    if state.name != "ALSZ":
        raise ValueError(f"unknown vehicle-order holder {state.name!r}")
    ident, _before = _read_alsz_region(ds2)
    if ident != state.ident:
        raise RuntimeError("lighting-module identity changed during recovery")
    for index in range(18):
        _positive(
            ds2,
            bytes((0xD0, 0x15, 0x16, index))
            + state.before[index * 16:(index + 1) * 16],
            0xD0, retries=16, delay=0.025,
        )
    time.sleep(1.0)
    if _read_alsz_region(ds2)[1] != state.before:
        raise RuntimeError("lighting-module recovery verification failed")


@dataclass(frozen=True)
class ZcsHolderState:
    name: str
    ident: bytes
    coding_index: int
    raw: bytes
    selector5_tail: bytes = b""


@dataclass(frozen=True)
class CodingState:
    name: str
    ident: bytes
    coding_index: int
    data: bytes
    address: int = 0


@dataclass(frozen=True)
class ClusterStoreState:
    chassis: str
    ident: bytes
    coding_index: int
    first_word: int
    data: bytes
    zcs_offset: int
    transmission_offset: int | None
    transmission_mask: int
    manual_value: int
    automatic_value: int | None
    checksum_word: int | None
    checksum_data_start: int | None
    checksum_data_end: int | None
    checksum_byte_offset: int | None = None
    profile: str | None = None


_DME_PROGRAM_FAMILY = {
    "1437806": "MS41.1", "1438068": "MS41.1",
    "1429861": "MS41.0", "1432401": "MS41.0",
    "1429373": "MS41.0", "1438137": "MS41.0",
    "1406464": "MS41.2", "SHINDE1": "MS41.3",
    "7500255": "MS42", "7505859": "MS42", "7526753": "MS42",
    "4482751": "MS43", "7511570": "MS43", "7528050": "MS43",
    "7545150": "MS43", "7551615": "MS43", "7571017": "MS43",
    "7572342": "MS43",
}


def classify_dme_ident(identity: bytes) -> tuple[str, str]:
    """Return the exact supported program number and DME family."""
    identity = bytes(identity)
    if len(identity) < 7:
        raise ValueError("engine-computer identification is too short")
    try:
        program = identity[:7].decode("ascii").strip().upper()
    except UnicodeDecodeError as error:
        raise ValueError("engine-computer identification is not ASCII") from error
    family = _DME_PROGRAM_FAMILY.get(program)
    if family is None:
        raise ValueError(f"engine-computer program {program or 'unknown'} is not supported")
    return program, family


def _require_ident(ds2, address: int, expected_length: int,
                   supported_indexes: Iterable[int]) -> tuple[bytes, int]:
    ident = _identify_c6(ds2, address)
    if len(ident) != expected_length:
        raise ValueError(
            f"module 0x{address:02X} identification length {len(ident)} is not supported")
    coding_index = _bcd(ident[8])
    if coding_index not in set(supported_indexes):
        raise ValueError(
            f"module 0x{address:02X} coding index {coding_index:02d} is not supported")
    return ident, coding_index


def _read_ews_zcs_raw(ds2) -> ZcsHolderState:
    ident, coding_index = _require_ident(ds2, 0x44, 16, (1, 2, 81, 82))
    block4 = _positive(ds2, bytes((0x44, 0x05, 0x69, 0x04)), 0x44)
    block5 = _positive(ds2, bytes((0x44, 0x05, 0x69, 0x05)), 0x44)
    data4, data5 = block4[3:-1], block5[3:-1]
    if len(data4) != 16 or len(data5) != 16:
        raise ValueError("immobilizer vehicle-order response has the wrong length")
    raw = data4 + data5[:4]
    return ZcsHolderState("EWS", ident, coding_index, raw, data5[4:])


def read_ews_zcs(ds2) -> ZcsHolderState:
    state = _read_ews_zcs_raw(ds2)
    raw = state.raw
    decode_zcs(raw)
    return state


def write_ews_zcs(ds2, state: ZcsHolderState, raw: bytes) -> ZcsHolderState:
    if state.name != "EWS":
        raise ValueError("an immobilizer vehicle-order snapshot is required")
    raw = encode_zcs(decode_zcs(raw))
    current = read_ews_zcs(ds2)
    if current != state:
        raise RuntimeError("immobilizer vehicle order changed since preflight")
    selector4 = raw[:16]
    selector5 = raw[16:20] + state.selector5_tail
    extra = b"\x00" if state.coding_index in (81, 82) else b""
    for selector, data in ((4, selector4), (5, selector5)):
        body = bytes((0x44, 5 + len(extra) + len(data), 0x6A, selector)) + extra + data
        _positive(ds2, body, 0x44)
    verified = read_ews_zcs(ds2)
    if verified.raw != raw or verified.selector5_tail != state.selector5_tail:
        raise RuntimeError("immobilizer vehicle-order verification failed")
    return verified


def restore_ews_zcs(ds2, state: ZcsHolderState) -> None:
    current = _read_ews_zcs_raw(ds2)
    if current.ident != state.ident or current.coding_index != state.coding_index:
        raise RuntimeError("immobilizer identity changed during recovery")
    # Use the archived selector-5 tail, even if the partially written copy no
    # longer decodes as a coherent ZCS.
    extra = b"\x00" if state.coding_index in (81, 82) else b""
    for selector, data in ((4, state.raw[:16]),
                           (5, state.raw[16:20] + state.selector5_tail)):
        _positive(
            ds2,
            bytes((0x44, 5 + len(extra) + len(data), 0x6A, selector)) + extra + data,
            0x44,
        )
    if _read_ews_zcs_raw(ds2) != state:
        raise RuntimeError("immobilizer vehicle-order recovery verification failed")


# Exact C_EWS3 coding layouts used by the supported E39/E46 profiles.
# The tuple is (coding-block length, automatic PARK/NEUTRAL bit value).
_EWS_TRANSMISSION_PROFILES = {
    1: (4, 0),
    2: (5, 0),
    81: (5, 1),
}


def _validate_ews_variant(ident: bytes, coding_index: int) -> None:
    diagnostic_index = ident[9]
    if coding_index in (1, 2):
        valid = diagnostic_index & 0x80 == 0
    else:
        valid = diagnostic_index & 0x80 != 0 and diagnostic_index != 0x82
    if not valid:
        raise ValueError("immobilizer diagnostic variant does not match its coding profile")


def read_ews_transmission(ds2) -> CodingState:
    """Read the complete EWS coding block containing the starter interlock."""
    ident, coding_index = _require_ident(
        ds2, 0x44, 16, _EWS_TRANSMISSION_PROFILES)
    _validate_ews_variant(ident, coding_index)
    response = _positive(ds2, bytes((0x44, 0x04, 0x08)), 0x44)
    data = response[3:-1]
    expected_length, _automatic_value = _EWS_TRANSMISSION_PROFILES[coding_index]
    if len(data) != expected_length:
        raise ValueError("immobilizer coding response has the wrong length")
    return CodingState("EWS", ident, coding_index, data)


def ews_starter_interlock_active(state: CodingState) -> bool:
    if state.name != "EWS" or state.coding_index not in _EWS_TRANSMISSION_PROFILES:
        raise ValueError("an exact immobilizer coding snapshot is required")
    expected_length, automatic_value = _EWS_TRANSMISSION_PROFILES[
        state.coding_index]
    if len(state.data) != expected_length:
        raise ValueError("immobilizer coding block has the wrong length")
    return (state.data[0] & 0x01) == automatic_value


def build_ews_transmission_target(state: CodingState,
                                  target: "Transmission") -> bytes:
    target = Transmission(target)
    # Validate the exact profile and current bit before preserving the rest.
    ews_starter_interlock_active(state)
    _length, automatic_value = _EWS_TRANSMISSION_PROFILES[state.coding_index]
    target_value = automatic_value if target is Transmission.AUTOMATIC else 1 - automatic_value
    data = bytearray(state.data)
    data[0] = (data[0] & ~0x01) | target_value
    return bytes(data)


def write_ews_transmission(ds2, state: CodingState,
                           target_data: bytes) -> CodingState:
    current = read_ews_transmission(ds2)
    if current != state:
        raise RuntimeError("immobilizer coding changed since preflight")
    target_data = bytes(target_data)
    if len(target_data) != len(state.data):
        raise ValueError("immobilizer target coding has the wrong length")
    _positive(
        ds2,
        bytes((0x44, len(target_data) + 4, 0x09)) + target_data,
        0x44,
    )
    verified = read_ews_transmission(ds2)
    if verified.data != target_data:
        raise RuntimeError("immobilizer coding verification failed")
    return verified


def restore_ews_transmission(ds2, state: CodingState) -> None:
    current = read_ews_transmission(ds2)
    if (current.ident != state.ident
            or current.coding_index != state.coding_index):
        raise RuntimeError("immobilizer identity changed during recovery")
    _positive(
        ds2,
        bytes((0x44, len(state.data) + 4, 0x09)) + state.data,
        0x44,
    )
    if read_ews_transmission(ds2) != state:
        raise RuntimeError("immobilizer coding recovery verification failed")


def _read_words(ds2, start: int, count: int) -> bytes:
    if not 0 <= start <= 0xFFFF or not 0 < count <= 0x10000 - start:
        raise ValueError("invalid instrument-cluster word range")
    result = bytearray()
    offset = 0
    while offset < count:
        size = min(8, count - offset)
        address = start + offset
        response = _positive(ds2, bytes((
            0x80, 0x09, 0x06, 0x03, 0x00,
            address >> 8, address & 0xFF, size,
        )), 0x80, retries=4, delay=0.025)
        if len(response) != 4 + 2 * size:
            raise ValueError("instrument-cluster storage response has the wrong length")
        result.extend(response[3:-1])
        offset += size
    return bytes(result)


def _write_words(ds2, start: int, before: bytes, after: bytes) -> None:
    before, after = bytes(before), bytes(after)
    if len(before) != len(after) or len(before) % 2:
        raise ValueError("instrument-cluster word images must have equal even lengths")
    words = len(before) // 2
    index = 0
    while index < words:
        if before[index * 2:index * 2 + 2] == after[index * 2:index * 2 + 2]:
            index += 1
            continue
        end = index + 1
        while (end < words and end - index < 8
               and before[end * 2:end * 2 + 2] != after[end * 2:end * 2 + 2]):
            end += 1
        address = start + index
        data = after[index * 2:end * 2]
        count = end - index
        _positive(ds2, bytes((
            0x80, 9 + len(data), 0x07, 0x03, 0x00,
            address >> 8, address & 0xFF, count,
        )) + data, 0x80, retries=4, delay=0.025)
        index = end


_E36_CLUSTER_TRANSMISSION = {
    2: (0xC0, 0x00, 0x40, 0x80),
    3: (0xE0, 0x00, 0x40, 0x80),
}
_E39_CLUSTER_TRANSMISSION = {
    3: (0xC0, 0x00, 0x40, 0x40),
    4: (0xC0, 0x00, 0x40, 0x40),
    5: (0xC0, 0x00, 0x40, 0x40),
    6: (0xC0, 0x00, 0x40, 0x40),
    7: (0xC0, 0x00, 0x40, 0x40),
}


def read_ms41_cluster_store(ds2, chassis: str) -> ClusterStoreState:
    chassis = str(chassis).upper()
    profiles = (_E36_CLUSTER_TRANSMISSION if chassis == "E36"
                else _E39_CLUSTER_TRANSMISSION if chassis == "E39" else None)
    if profiles is None:
        raise ValueError("MS41 cluster storage supports E36 and E39 only")
    ident, coding_index = _require_ident(ds2, 0x80, 18, profiles)
    profile = "KMB"
    if chassis == "E39":
        family_index = ident[9]
        if 0x01 <= family_index <= 0x04:
            if coding_index != 5:
                raise ValueError(
                    f"E39 IKE coding index {coding_index:02d} is not supported")
            profile = "IKE"
        elif (0x1E <= family_index <= 0x1F
              or 0x20 <= family_index <= 0x28):
            profile = "KMB"
        elif 0x05 <= family_index <= 0x0E:
            raise ValueError("the fitted E39 IKI cluster profile is not supported")
        else:
            raise ValueError(
                f"E39 cluster family 0x{family_index:02X} is not supported")
    first_word, last_word = ((0x6C, 0xBF) if chassis == "E36" else (0x2C, 0x7F))
    data = _read_words(ds2, first_word, last_word - first_word + 1)
    mask, manual, five_speed, four_speed = profiles[coding_index]
    return ClusterStoreState(
        chassis, ident, coding_index, first_word, data,
        268 if chassis == "E36" else 140,
        308 if chassis == "E36" else 180,
        mask, manual, five_speed, first_word,
        first_word + 1, last_word, 0xD9 if chassis == "E36" else None, profile,
    )


def _cluster_absolute_slice(state: ClusterStoreState, byte_offset: int,
                            size: int) -> slice:
    start = byte_offset - state.first_word * 2
    if start < 0 or start + size > len(state.data):
        raise ValueError("instrument-cluster field is outside the archived range")
    return slice(start, start + size)


def cluster_zcs(state: ClusterStoreState) -> Zcs:
    field = _cluster_absolute_slice(state, state.zcs_offset, 20)
    return decode_zcs(state.data[field])


def cluster_transmission(state: ClusterStoreState) -> "Transmission":
    if state.transmission_offset is None:
        raise ValueError("instrument cluster has no reviewed transmission field")
    index = _cluster_absolute_slice(state, state.transmission_offset, 1).start
    value = state.data[index] & state.transmission_mask
    if value == state.manual_value:
        return Transmission.MANUAL
    if state.chassis in {"E36", "E39"}:
        if state.profile == "IKE":
            automatic_values = {0x40, 0x80, 0xC0}
        else:
            profiles = (_E36_CLUSTER_TRANSMISSION if state.chassis == "E36"
                        else _E39_CLUSTER_TRANSMISSION)
            automatic_values = set(profiles[state.coding_index][2:])
        if value in automatic_values:
            return Transmission.AUTOMATIC
    if state.automatic_value is not None and value == state.automatic_value:
        return Transmission.AUTOMATIC
    raise ValueError(
        f"instrument-cluster transmission value 0x{value:02X} is not supported")


def _cluster_checksum_image(state: ClusterStoreState, data: bytearray) -> None:
    if (state.checksum_word is None or state.checksum_data_start is None
            or state.checksum_data_end is None):
        return
    checksum_slice = _cluster_absolute_slice(
        state, state.checksum_data_start * 2,
        (state.checksum_data_end - state.checksum_data_start + 1) * 2,
    )
    checksum = _xor(data[checksum_slice])
    stored = _cluster_absolute_slice(
        state,
        (state.checksum_byte_offset if state.checksum_byte_offset is not None
         else state.checksum_word * 2),
        1,
    ).start
    data[stored] = checksum


def _validate_cluster_checksum(state: ClusterStoreState) -> None:
    if state.checksum_word is None:
        return
    data = bytearray(state.data)
    checksum_offset = (state.checksum_byte_offset if state.checksum_byte_offset is not None
                       else state.checksum_word * 2)
    stored = data[_cluster_absolute_slice(state, checksum_offset, 1).start]
    check = bytearray(data)
    _cluster_checksum_image(state, check)
    expected = check[_cluster_absolute_slice(state, checksum_offset, 1).start]
    if stored != expected:
        raise ValueError("instrument-cluster storage checksum is invalid")


def build_ms41_cluster_target(state: ClusterStoreState, raw_zcs: bytes,
                              target: "Transmission", *, four_speed: bool = False,
                              steptronic: bool = False) -> bytes:
    _validate_cluster_checksum(state)
    target = Transmission(target)
    raw_zcs = encode_zcs(decode_zcs(raw_zcs))
    output = bytearray(state.data)
    output[_cluster_absolute_slice(state, state.zcs_offset, 20)] = raw_zcs
    if state.transmission_offset is None:
        raise ValueError("instrument cluster has no reviewed transmission field")
    index = _cluster_absolute_slice(state, state.transmission_offset, 1).start
    if target is Transmission.MANUAL:
        value = state.manual_value
    elif state.profile == "IKE" and steptronic:
        value = 0xC0
    elif state.profile == "IKE" and four_speed:
        value = 0x80
    elif four_speed:
        profiles = (_E36_CLUSTER_TRANSMISSION if state.chassis == "E36"
                    else _E39_CLUSTER_TRANSMISSION)
        value = profiles[state.coding_index][3]
    elif state.automatic_value is not None:
        value = state.automatic_value
    else:
        raise ValueError("instrument cluster has no supported automatic value")
    output[index] = (output[index] & ~state.transmission_mask) | value
    _cluster_checksum_image(state, output)
    return bytes(output)


def write_cluster_store(ds2, state: ClusterStoreState, target_data: bytes) -> ClusterStoreState:
    current = read_ms41_cluster_store(ds2, state.chassis)
    if current != state:
        raise RuntimeError("instrument-cluster storage changed since preflight")
    _write_cluster_target_words(ds2, state, state.data, target_data)
    verified = read_ms41_cluster_store(ds2, state.chassis)
    if verified.data != bytes(target_data):
        raise RuntimeError("instrument-cluster coding verification failed")
    _validate_cluster_checksum(verified)
    return verified


def restore_cluster_store(ds2, state: ClusterStoreState) -> None:
    current = read_ms41_cluster_store(ds2, state.chassis)
    if current.ident != state.ident or current.coding_index != state.coding_index:
        raise RuntimeError("instrument-cluster identity changed during recovery")
    _write_cluster_target_words(ds2, state, current.data, state.data)
    if read_ms41_cluster_store(ds2, state.chassis) != state:
        raise RuntimeError("instrument-cluster recovery verification failed")


def _read_addressed_coding(ds2, name: str, supported_indexes: Iterable[int],
                           address: int = 0, count: int = 1) -> CodingState:
    ident, coding_index = _require_ident(ds2, 0x56, 16, supported_indexes)
    response = _positive(
        ds2, bytes((0x56, 0x06, 0x08, address, count)), 0x56)
    data = response[3:-1]
    if len(data) != count:
        raise ValueError(f"{name} coding response has the wrong length")
    return CodingState(name, ident, coding_index, data, address)


def read_asc5_transmission(ds2, chassis: str) -> CodingState:
    indexes = (1,) if str(chassis).upper() == "E36" else (6, 8)
    return _read_addressed_coding(ds2, "ASC5", indexes)


def write_asc5_transmission(ds2, state: CodingState,
                            target: "Transmission") -> CodingState:
    target = Transmission(target)
    current = _read_addressed_coding(
        ds2, state.name, (state.coding_index,), state.address, len(state.data))
    if current != state:
        raise RuntimeError("traction-control coding changed since preflight")
    data = bytearray(state.data)
    data[0] = (data[0] & ~0x10) | (0x10 if target is Transmission.AUTOMATIC else 0)
    _identify_c6(ds2, 0x56)
    _positive(ds2, bytes((
        0x56, len(data) + 5, 0x09, state.address,
    )) + data, 0x56)
    verified = _read_addressed_coding(
        ds2, state.name, (state.coding_index,), state.address, len(data))
    if verified.data != bytes(data):
        raise RuntimeError("traction-control coding verification failed")
    return verified


def read_mk20_transmission(ds2) -> CodingState:
    ident, coding_index = _require_ident(ds2, 0x56, 16, (3, 4, 5))
    response = _positive(ds2, bytes((0x56, 0x04, 0x08)), 0x56)
    data = response[3:-1]
    if len(data) != 12:
        raise ValueError("MK20 coding response has the wrong length")
    return CodingState("MK20", ident, coding_index, data)


def write_mk20_transmission(ds2, state: CodingState,
                            target: "Transmission") -> CodingState:
    target = Transmission(target)
    current = read_mk20_transmission(ds2)
    if current != state:
        raise RuntimeError("MK20 coding changed since preflight")
    data = bytearray(state.data)
    data[2] = (data[2] & ~0x80) | (0x80 if target is Transmission.AUTOMATIC else 0)
    _identify_c6(ds2, 0x56)
    _positive(ds2, bytes((0x56, len(data) + 4, 0x09)) + data, 0x56)
    time.sleep(4.0)
    verified = read_mk20_transmission(ds2)
    if verified.data != bytes(data):
        raise RuntimeError("MK20 coding verification failed")
    return verified


def restore_transmission_coding(ds2, state: CodingState) -> None:
    if state.name == "EWS":
        restore_ews_transmission(ds2, state)
        return
    if state.name == "ASC5":
        current = _read_addressed_coding(
            ds2, state.name, (state.coding_index,), state.address, len(state.data))
        if current.ident != state.ident or current.coding_index != state.coding_index:
            raise RuntimeError(f"{state.name} identity changed during recovery")
        _identify_c6(ds2, 0x56)
        _positive(ds2, bytes((
            0x56, len(state.data) + 5, 0x09, state.address,
        )) + state.data, 0x56)
        verified = _read_addressed_coding(
            ds2, state.name, (state.coding_index,), state.address, len(state.data))
    elif state.name == "MK20":
        current = read_mk20_transmission(ds2)
        if current.ident != state.ident or current.coding_index != state.coding_index:
            raise RuntimeError(f"{state.name} identity changed during recovery")
        _identify_c6(ds2, 0x56)
        _positive(ds2, bytes((0x56, len(state.data) + 4, 0x09)) + state.data, 0x56)
        time.sleep(4.0)
        verified = read_mk20_transmission(ds2)
    else:
        raise ValueError(f"unknown coding snapshot {state.name!r}")
    if verified.data != state.data:
        raise RuntimeError(f"{state.name} recovery verification failed")


def coding_transmission(state: CodingState) -> "Transmission":
    if state.name == "ASC5":
        value = state.data[0] & 0x10
    elif state.name == "MK20":
        value = state.data[2] & 0x80
    else:
        raise ValueError(f"unknown coding snapshot {state.name!r}")
    return Transmission.AUTOMATIC if value else Transmission.MANUAL


_E36_ZCS_PAIRS = (
    ("1151", "1161", "BF51", "BF61"), ("1152", "1162", "BF52", "BF62"),
    ("1171", "1181", "BF71", "BF81"), ("1172", "1182", "BF72", "BF82"),
    ("1173", "1183", "BF73", "BF83"), ("11A1", "11B1", "BG11", "BG21"),
    ("11A2", "11B2", "BG12", "BG22"), ("11A3", "11B3", "BG13", "BG23"),
    ("1431", "1441", "BJ31", "BJ41"), ("1432", "1442", "BJ32", "BJ42"),
    ("1471", "1481", "BJ71", "BJ81"), ("1472", "1482", "BJ72", "BJ82"),
    ("1473", "1483", "BJ73", "BJ83"), ("14E1", "14F1", "BK71", "BK81"),
    ("14E2", "14F2", "BK72", "BK82"), ("14E3", "14F3", "BK73", "BK83"),
    ("1651", "1661", "CB51", "CB61"), ("1652", "1662", "CB52", "CB62"),
    ("1657", "1667", "CB57", "CB67"), ("1671", "1681", "CB71", "CB81"),
    ("1672", "1682", "CB72", "CB82"), ("1271", "1281", "CP71", "CP81"),
    ("1272", "1282", "CP72", "CP82"), ("1675", "1685", "CB75", "CB85"),
    ("1677", "1687", "CB77", "CB87"), ("1678", "1688", "CB78", "CB88"),
    ("16A1", "16B1", "CD11", "CD21"), ("16A2", "16B2", "CD12", "CD22"),
    ("16A3", "16B3", "CD13", "CD23"), ("16A4", "16B4", "CD14", "CD24"),
    ("16A8", "16B8", "CD18", "CD28"), ("16C3", "16D3", "CD33", "CD43"),
    ("1851", "1861", "CE51", "CE61"), ("1852", "1862", "CE52", "CE62"),
    ("1871", "1881", "CE71", "CE81"), ("1872", "1882", "CE72", "CE82"),
    ("18A1", "18B1", "CF11", "CF21"), ("18A2", "18B2", "CF12", "CF22"),
    ("1C31", "1C41", "CG31", "CG41"), ("1D11", "1D21", "CT31", "CT41"),
)
_E39_ZCS_PAIRS = (
    ("5311", "5321", "DD11", "DD21"), ("5312", "5322", "DD12", "DD22"),
    ("5331", "5341", "DD31", "DD41"), ("5332", "5342", "DD32", "DD42"),
    ("5337", "5347", "DD37", "DD47"), ("5351", "5361", "DD51", "DD61"),
    ("5352", "5362", "DD52", "DD62"), ("5353", "5363", "DD53", "DD63"),
    ("5711", "5721", "DH11", "DH21"), ("5712", "5722", "DH12", "DH22"),
    ("5731", "5741", "DH31", "DH41"), ("5732", "5742", "DH32", "DH42"),
    ("5751", "5761", "DH51", "DH61"), ("5752", "5762", "DH52", "DH62"),
)
_SA202 = 0x0001000000000000
_SA204 = 0x0008000000000000
_SA963 = 0x0000000000000800

_E36_EGS_ADMISSION = {
    "BF83": ("GS834", frozenset((1423326, 1423432))),
    "BJ83": ("GS834", frozenset((1423326, 1423432))),
    "BG23": ("GS834", frozenset((1423436,))),
    "BK83": ("GS834", frozenset((1423436,))),
    "CG41": ("GS832", frozenset((1423262,))),
    "CT41": ("GS832", frozenset((1423262,))),
}

_E39_GS832_520 = frozenset((1422780, 1422972))
_E39_GS832_523 = frozenset((
    1422538, 1422613, 1422622, 1422782, 1422940,
))
_E39_GS832_523_S204 = frozenset((1423118, 1423228))
_E39_GS832_528 = frozenset((1422540, 1422615, 1422624, 1422784))
_E39_GS832_528_S204 = frozenset((1423144,))

_E46_EGS_ADMISSION = {
    ("MS42", "AM51"): ("GS8600", frozenset((
        1423390, 1423487, 1423678, 7504952,
    ))),
    ("MS43", "EV51"): ("GS8604", frozenset((
        7507497, 7510270, 7511859, 7512065, 7514302,
        7515453, 7516764, 7523484, 7529014, 7546090,
    ))),
}


@dataclass(frozen=True)
class Ms41ZcsTarget:
    chassis: str
    source: Transmission
    target: Transmission
    source_type: str
    target_type: str
    raw: bytes
    four_speed: bool
    requires_ews3_ci82: bool
    steptronic: bool


def _pair_for_prefix(chassis: str, prefix: str):
    pairs = _E36_ZCS_PAIRS if chassis == "E36" else _E39_ZCS_PAIRS
    for manual_prefix, auto_prefix, manual_type, auto_type in pairs:
        if prefix == manual_prefix:
            return Transmission.MANUAL, manual_prefix, auto_prefix, manual_type, auto_type
        if prefix == auto_prefix:
            return Transmission.AUTOMATIC, manual_prefix, auto_prefix, manual_type, auto_type
    raise ValueError(f"connected {chassis} vehicle type {prefix} has no exact swap pair")


def _e39_sa202_required(target_type: str, production: tuple[int, int],
                        gm: bytes, sa_value: int) -> bool:
    excluded_market = ((int.from_bytes(gm, "big") & 0x0000FF00) in (0x0700, 0x2400)
                       or bool(sa_value & _SA963))
    if target_type in {"DD21", "DD22", "DD47", "DH21", "DH22"}:
        return True
    if target_type in {"DD41", "DD42"}:
        return production >= (1996, 9) and not excluded_market
    if target_type in {"DD61", "DD62"}:
        return production >= (1996, 3) and not excluded_market
    if target_type in {"DH41", "DH42", "DH61", "DH62"}:
        return not excluded_market
    return False


def derive_ms41_zcs_target(raw: bytes, chassis: str, target: "Transmission",
                           production: tuple[int, int] | None = None) -> Ms41ZcsTarget:
    zcs = decode_zcs(raw)
    chassis = str(chassis).upper()
    if chassis not in {"E36", "E39"}:
        raise ValueError("MS41 vehicle-order conversion supports E36 and E39 only")
    target = Transmission(target)
    prefix = zcs.gm.hex().upper()[:4]
    source, manual_prefix, auto_prefix, manual_type, auto_type = _pair_for_prefix(
        chassis, prefix)
    target_prefix = manual_prefix if target is Transmission.MANUAL else auto_prefix
    target_type = manual_type if target is Transmission.MANUAL else auto_type
    gm = bytes.fromhex(target_prefix + zcs.gm.hex().upper()[4:])
    sa_value = int.from_bytes(zcs.sa, "big")
    if chassis == "E39":
        if target is Transmission.MANUAL:
            sa_value &= ~_SA202
        else:
            if production is None:
                raise ValueError("E39 production month is required for automatic coding")
            if _e39_sa202_required(target_type, production, gm, sa_value):
                sa_value |= _SA202
            else:
                sa_value &= ~_SA202
    target_zcs = Zcs(gm, sa_value.to_bytes(8, "big"), zcs.vn)
    return Ms41ZcsTarget(
        chassis, source, target,
        manual_type if source is Transmission.MANUAL else auto_type,
        target_type, encode_zcs(target_zcs), target_type == "DD63",
        target_type in {"CT31", "CT41"},
        bool(sa_value & _SA202),
    )


def _ms41_egs_admission(target: Ms41ZcsTarget) -> tuple[str, frozenset[int]]:
    if target.chassis == "E36":
        try:
            return _E36_EGS_ADMISSION[target.target_type]
        except KeyError:
            raise ValueError(
                f"E36 automatic donor admission is not reviewed for {target.target_type}"
            ) from None

    sa204 = bool(int.from_bytes(decode_zcs(target.raw).sa, "big") & _SA204)
    vehicle_type = target.target_type
    if vehicle_type in {"DD21", "DD22", "DH21", "DH22"}:
        if sa204:
            raise ValueError(f"E39 SA204 donor admission is not reviewed for {vehicle_type}")
        return "GS832", _E39_GS832_520
    if vehicle_type in {"DD41", "DD42", "DD47", "DH41", "DH42"}:
        if sa204:
            if vehicle_type not in {"DD41", "DD42", "DD47"}:
                raise ValueError(
                    f"E39 SA204 donor admission is not reviewed for {vehicle_type}")
            return "GS832", _E39_GS832_523_S204
        return "GS832", _E39_GS832_523
    if vehicle_type in {"DD61", "DD62", "DH61", "DH62"}:
        if sa204:
            if vehicle_type not in {"DD61", "DD62"}:
                raise ValueError(
                    f"E39 SA204 donor admission is not reviewed for {vehicle_type}")
            return "GS832", _E39_GS832_528_S204
        return "GS832", _E39_GS832_528
    if vehicle_type == "DD63":
        if sa204:
            raise ValueError("E39 SA204 donor admission is not reviewed for DD63")
        return "GS834", frozenset((1423438,))
    raise ValueError(
        f"E39 automatic donor admission is not reviewed for {vehicle_type}")


def _e46_egs_admission(family: str, order_format: "OrderFormat",
                       *, zcs: Zcs | None = None,
                       fa: FaV2 | None = None,
                       production: tuple[int, int] | None = None
                       ) -> tuple[str, frozenset[int]]:
    if family == "MS42":
        if order_format is not OrderFormat.ZCS or zcs is None:
            raise ValueError("MS42 automatic conversion requires an exact ZCS identity")
        if zcs.gm[:2] != bytes.fromhex("6651"):
            raise ValueError("MS42 automatic donor admission currently supports AM51 only")
        if production is None or not (1997, 12) <= production <= (2000, 5):
            raise ValueError("AM51 production date is outside the reviewed 12/97-05/00 range")
        return _E46_EGS_ADMISSION[(family, "AM51")]

    if family == "MS43":
        if order_format is not OrderFormat.FA or fa is None:
            raise ValueError("MS43 conversion requires an exact version-2 FA identity")
        if fa.vehicle_type != "EV51":
            raise ValueError("MS43 conversion currently supports EV51 only")
        month, year = int(fa.date[:2]), int(fa.date[2:])
        if not (2001, 9) <= (2000 + year, month) <= (2004, 11):
            raise ValueError("EV51 production date is outside the reviewed 09/01-11/04 range")
        if production is None or not (2001, 9) <= production <= (2004, 11):
            raise ValueError("connected EV51 production date is outside the reviewed 09/01-11/04 range")
        return _E46_EGS_ADMISSION[(family, "EV51")]

    raise ValueError(f"automatic donor admission is not supported for {family}")


def read_ews_production_month(ds2) -> tuple[int, int]:
    response = _positive(ds2, bytes((0x44, 0x05, 0x69, 0x0B)), 0x44)
    if len(response) < 21:
        raise ValueError("immobilizer production-date response is too short")
    day, month, year = (_bcd(value) for value in response[17:20])
    if not 1 <= day <= 31 or not 1 <= month <= 12:
        raise ValueError("immobilizer production date is invalid")
    return (1900 + year if year >= 80 else 2000 + year), month


def ms41_selector(ds2) -> "MS41Selector":
    value = ds2.read_mem(0x10005, 1)[0] & 0x3F
    try:
        return {0x2C: MS41Selector.DYNAMIC,
                0x00: MS41Selector.MANUAL_ONLY,
                0x2B: MS41Selector.AUTOMATIC_ONLY}[value]
    except KeyError:
        raise ValueError(
            f"DME transmission selector 0x{value:02X} is not supported") from None


def read_ms41_runtime_transmission(ds2, family: str) -> "Transmission":
    try:
        address = MS41_TRANSMISSION_FLAG_ADDRESS[family]
    except KeyError:
        raise ValueError(f"unsupported MS41 family {family!r}") from None
    data = bytes(ds2.read_mem(address, 1))
    if len(data) != 1:
        raise ValueError("engine-computer runtime transmission response is invalid")
    return (Transmission.AUTOMATIC
            if data[0] & MS41_TRANSMISSION_FLAG_MASK
            else Transmission.MANUAL)


def read_egs_family(ds2) -> tuple[str, bytes]:
    response = _positive(ds2, bytes((0x32, 0x04, 0x00)), 0x32)
    if len(response) < 16 or response[1] != len(response):
        raise ValueError("automatic-transmission computer identity is too short")
    try:
        logical = response[10:12].decode("ascii")
    except UnicodeDecodeError as error:
        raise ValueError("automatic-transmission identity is invalid") from error
    if logical == "23":
        family = "GS834"
    elif logical == "28":
        family = "GS8600"
    elif logical == "2H":
        family = "GS8604"
    elif logical == "2A":
        family = "GS855"
    elif logical == "32":
        family = "GS836" if chr(response[14]) in {"1", "4"} else "GS832"
    else:
        raise ValueError("automatic-transmission computer family is not supported")
    if family in {"GS832", "GS834", "GS8600", "GS8604"} and len(response) < 0x2E:
        raise ValueError("automatic-transmission computer identity is incomplete")
    return family, response


def _egs_aif_block(ds2, selector: int, address: int, count: int) -> bytes:
    if not 0 <= address <= 0xFFFFFF:
        raise ValueError("automatic-transmission AIF address is invalid")
    response = _positive(ds2, bytes((
        0x32, 0x09, 0x06, selector,
        address >> 16, address >> 8 & 0xFF, address & 0xFF, count,
    )), 0x32, retries=4, delay=0.05)
    expected = count + 4
    if len(response) != expected or response[1] != expected:
        raise ValueError("automatic-transmission AIF response has the wrong length")
    return response


def read_egs_aif_zb(ds2, *, family: str | None = None,
                    ident: bytes | None = None) -> tuple[str, bytes, int]:
    """Read one reviewed EGS identity and its latest programmed AIF ZB number."""
    if family is None or ident is None:
        family, ident = read_egs_family(ds2)
    ident = bytes(ident)
    if family not in {"GS832", "GS834", "GS8600", "GS8604"}:
        raise ValueError(f"{family} AIF admission is not supported")

    discovery = _positive(
        ds2, bytes((0x32, 0x04, 0x0D)), 0x32,
        retries=4, delay=0.05,
    )
    if (len(discovery) < 0x43 or discovery[1] != len(discovery)):
        raise ValueError("automatic-transmission AIF discovery response is too short")
    base = int.from_bytes(discovery[0x3F:0x42], "big")

    if family == "GS832":
        record = _egs_aif_block(ds2, 0x02, base, 0x21)
    else:
        selector = 0x02 if family == "GS834" else 0x00
        latest_address = None
        for slot in range(14):
            address = base + slot * 0x2E
            record = _egs_aif_block(ds2, selector, address, 0x2E)
            if any(value == 0xFF for value in record[3:6]):
                if slot == 0:
                    if record[3] != 0xFF:
                        raise ValueError("automatic-transmission AIF first slot is malformed")
                    raise ValueError("automatic-transmission AIF is not programmed")
                break
            latest_address = address
        if latest_address is None:
            raise ValueError("automatic-transmission AIF is not programmed")
        record = _egs_aif_block(ds2, selector, latest_address, 0x2E)

    if len(record) <= 0x21 or record[0x21] == 0:
        raise ValueError("automatic-transmission AIF ZB number is not valid")
    zb = int.from_bytes(record[0x1E:0x21], "big")
    if zb in {0, 0xFFFFFF}:
        raise ValueError("automatic-transmission AIF ZB number is not programmed")
    return family, ident, zb


def read_e46_dme_transmission(ds2) -> "Transmission":
    """Read the active MS42/MS43 transmission configuration from ECU_CONFIG."""
    response = _positive(
        ds2, bytes((0x12, 0x05, 0x0B, 0x94)), 0x12,
        retries=4, delay=0.05,
    )
    if len(response) < 5 or response[1] != len(response):
        raise ValueError("engine-computer configuration response is incomplete")
    if response[3] == 0xFF:
        raise ValueError("engine-computer transmission configuration is not supported")
    return (Transmission.AUTOMATIC if response[3] & 0x10
            else Transmission.MANUAL)


_E46_KMB_EARLY = frozenset((2, 3, 4, 5, 6, 20, 21, 22))
_E46_KMB_LATE = frozenset((7, 8, 23, 24))


def _write_cluster_target_words(ds2, state: ClusterStoreState,
                                before: bytes, target: bytes) -> None:
    """Write changed data first and publish the checksum word last."""
    before, target = bytes(before), bytes(target)
    if state.checksum_word is None:
        _write_words(ds2, state.first_word, before, target)
        return
    checksum = _cluster_absolute_slice(state, state.checksum_word * 2, 2)
    intermediate = bytearray(target)
    intermediate[checksum] = before[checksum]
    _write_words(ds2, state.first_word, before, intermediate)
    _write_words(ds2, state.first_word, intermediate, target)
    if state.chassis == "E36":
        _positive(ds2, bytes.fromhex("80 04 12"), 0x80)
        time.sleep(5.0)


def read_e46_cluster_store(ds2, *, validate: bool = True) -> ClusterStoreState:
    ident, coding_index = _require_ident(
        ds2, 0x80, 18, _E46_KMB_EARLY | _E46_KMB_LATE)
    if coding_index in _E46_KMB_EARLY:
        first_word, last_word = 0x1F, 0xFF
        zcs_offset, transmission_offset = 104, 78
        checksum_word, checksum_start, checksum_end, checksum_byte = (
            0x1F, 0x20, 0xFF, 0x3F)
        mask, automatic = 0x02, 0x02
    else:
        first_word, last_word = 0x38, 0xC1
        zcs_offset, transmission_offset = 368, 112
        checksum_word, checksum_start, checksum_end, checksum_byte = (
            0xB7, 0x38, 0xB6, 0x16E)
        mask, automatic = 0x18, 0x08
    state = ClusterStoreState(
        "E46", ident, coding_index, first_word,
        _read_words(ds2, first_word, last_word - first_word + 1),
        zcs_offset, transmission_offset, mask, 0x00, automatic,
        checksum_word, checksum_start, checksum_end, checksum_byte,
    )
    if validate:
        _validate_cluster_checksum(state)
    return state


def e46_cluster_zcs(state: ClusterStoreState) -> Zcs:
    if state.chassis != "E46":
        raise ValueError("an E46 cluster snapshot is required")
    return cluster_zcs(state)


def e46_cluster_transmission(state: ClusterStoreState) -> "Transmission":
    if state.chassis != "E46":
        raise ValueError("an E46 cluster snapshot is required")
    if state.coding_index >= 20:
        raise ValueError("M3 sequential-transmission profiles are not supported by this conversion")
    return cluster_transmission(state)


def build_e46_cluster_target(state: ClusterStoreState, raw_zcs: bytes | None,
                             target: "Transmission") -> bytes:
    if state.chassis != "E46" or state.coding_index >= 20:
        raise ValueError("this E46 instrument-cluster profile is not supported")
    _validate_cluster_checksum(state)
    target = Transmission(target)
    output = bytearray(state.data)
    if raw_zcs is not None:
        output[_cluster_absolute_slice(state, state.zcs_offset, 20)] = encode_zcs(
            decode_zcs(raw_zcs))
    index = _cluster_absolute_slice(state, state.transmission_offset, 1).start
    value = state.automatic_value if target is Transmission.AUTOMATIC else state.manual_value
    if value is None:
        raise ValueError("instrument cluster has no supported target transmission value")
    output[index] = (output[index] & ~state.transmission_mask) | value
    _cluster_checksum_image(state, output)
    return bytes(output)


def write_e46_cluster_store(ds2, state: ClusterStoreState,
                            target_data: bytes) -> ClusterStoreState:
    current = read_e46_cluster_store(ds2)
    if current != state:
        raise RuntimeError("E46 instrument-cluster storage changed since preflight")
    _write_cluster_target_words(ds2, state, state.data, target_data)
    verified = read_e46_cluster_store(ds2)
    if verified.data != bytes(target_data):
        raise RuntimeError("E46 instrument-cluster coding verification failed")
    return verified


def restore_e46_cluster_store(ds2, state: ClusterStoreState) -> None:
    current = read_e46_cluster_store(ds2, validate=False)
    if current.ident != state.ident or current.coding_index != state.coding_index:
        raise RuntimeError("E46 instrument-cluster identity changed during recovery")
    _write_cluster_target_words(ds2, state, current.data, state.data)
    if read_e46_cluster_store(ds2) != state:
        raise RuntimeError("E46 instrument-cluster recovery verification failed")


def read_e46_kmb_zcs(ds2) -> ZcsHolderState:
    state = read_e46_cluster_store(ds2)
    raw = encode_zcs(cluster_zcs(state))
    return ZcsHolderState("KMB", state.ident, state.coding_index, raw)


def write_e46_kmb_zcs(ds2, state: ZcsHolderState, raw: bytes) -> ZcsHolderState:
    if state.name != "KMB":
        raise ValueError("an E46 cluster vehicle-order snapshot is required")
    raw = encode_zcs(decode_zcs(raw))
    current_store = read_e46_cluster_store(ds2)
    current = ZcsHolderState(
        "KMB", current_store.ident, current_store.coding_index,
        encode_zcs(cluster_zcs(current_store)),
    )
    if current != state:
        raise RuntimeError("instrument-cluster vehicle order changed since preflight")
    target_data = bytearray(current_store.data)
    target_data[_cluster_absolute_slice(
        current_store, current_store.zcs_offset, 20)] = raw
    _cluster_checksum_image(current_store, target_data)
    write_e46_cluster_store(ds2, current_store, target_data)
    verified = read_e46_kmb_zcs(ds2)
    if verified.raw != raw:
        raise RuntimeError("instrument-cluster vehicle-order verification failed")
    return verified


def restore_e46_kmb_zcs(ds2, state: ZcsHolderState) -> None:
    current_store = read_e46_cluster_store(ds2)
    current = ZcsHolderState(
        "KMB", current_store.ident, current_store.coding_index,
        encode_zcs(cluster_zcs(current_store)),
    )
    if current.ident != state.ident or current.coding_index != state.coding_index:
        raise RuntimeError("instrument-cluster identity changed during recovery")
    target_data = bytearray(current_store.data)
    target_data[_cluster_absolute_slice(
        current_store, current_store.zcs_offset, 20)] = state.raw
    _cluster_checksum_image(current_store, target_data)
    write_e46_cluster_store(ds2, current_store, target_data)
    if read_e46_kmb_zcs(ds2) != state:
        raise RuntimeError("instrument-cluster vehicle-order recovery verification failed")


@dataclass(frozen=True)
class Mk60State:
    ident: bytes
    coding_index: int
    data: bytes


_MK60_INDEXES = frozenset((1, 3, 4, 5, 9, 10, 12, 13))


def _bmw_fast_payload(response: bytes, service: int, identifier: bytes = b"") -> bytes:
    response = bytes(response)
    payload = response[4:-1]
    expected = bytes(((service + 0x40) & 0xFF,)) + bytes(identifier)
    if not payload.startswith(expected):
        raise ValueError("stability-control response does not match the request")
    return payload[len(expected):]


def read_mk60_transmission(ds2, *, validate: bool = True) -> Mk60State:
    ident = bytes(ds2.send_bmw_fast(
        bytes.fromhex("B8 29 F1 02 1A 80"), target=0x29, timeout=2.0))
    _bmw_fast_payload(ident, 0x1A, b"\x80")
    if len(ident) <= 13:
        raise ValueError("MK60 identification response is too short")
    coding_index = _bcd(ident[13])
    if coding_index not in _MK60_INDEXES:
        raise ValueError(f"MK60 coding index {coding_index:02d} is not supported")
    response = bytes(ds2.send_bmw_fast(
        bytes.fromhex("B8 29 F1 03 22 30 00"), target=0x29, timeout=2.0))
    data = _bmw_fast_payload(response, 0x22, b"\x30\x00")
    if len(data) != 15:
        raise ValueError("MK60 coding response has the wrong length")
    if validate:
        if data[0] != (_xor(data[1:]) + 1) & 0xFF:
            raise ValueError("MK60 coding checksum is invalid")
        value = data[2] & 0xC0
        if value not in (0x00, 0x40):
            raise ValueError("MK60 is coded for a sequential gearbox")
    return Mk60State(ident, coding_index, data)


def mk60_transmission(state: Mk60State) -> "Transmission":
    return (Transmission.AUTOMATIC if state.data[2] & 0xC0 == 0x40
            else Transmission.MANUAL)


def _write_mk60_data(ds2, data: bytes) -> None:
    data = bytes(data)
    if len(data) != 15 or data[0] != (_xor(data[1:]) + 1) & 0xFF:
        raise ValueError("invalid MK60 coding block")
    response = bytes(ds2.send_bmw_fast(
        bytes.fromhex("B8 29 F1 02 10 87"), target=0x29, timeout=2.0))
    _bmw_fast_payload(response, 0x10, b"\x87")
    response = bytes(ds2.send_bmw_fast(
        bytes.fromhex("B8 29 F1 12 2E 30 00") + data,
        target=0x29, timeout=2.0,
    ))
    _bmw_fast_payload(response, 0x2E, b"\x30\x00")


def write_mk60_transmission(ds2, state: Mk60State,
                            target: "Transmission") -> Mk60State:
    target = Transmission(target)
    current = read_mk60_transmission(ds2)
    if current != state:
        raise RuntimeError("MK60 coding changed since preflight")
    data = bytearray(state.data)
    data[2] = (data[2] & ~0xC0) | (0x40 if target is Transmission.AUTOMATIC else 0)
    data[0] = (_xor(data[1:]) + 1) & 0xFF
    _write_mk60_data(ds2, data)
    verified = read_mk60_transmission(ds2)
    if verified.data != bytes(data):
        raise RuntimeError("MK60 coding verification failed")
    return verified


def restore_mk60(ds2, state: Mk60State) -> None:
    current = read_mk60_transmission(ds2, validate=False)
    if current.ident != state.ident or current.coding_index != state.coding_index:
        raise RuntimeError("MK60 identity changed during recovery")
    _write_mk60_data(ds2, state.data)
    if read_mk60_transmission(ds2) != state:
        raise RuntimeError("MK60 recovery verification failed")


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
            if request.eeprom_writer_available is not True:
                reasons.append(
                    "The stock K-Line transmission-record writer is not available.")
            warnings.append(
                "The tool changes only the checked transmission record; it does not "
                "replace the calibration with MT Only or AT Only."
            )
    else:
        fixed_transmission = (
            Transmission.MANUAL if selector is MS41Selector.MANUAL_ONLY
            else Transmission.AUTOMATIC
        )
        if fixed_transmission is not target:
            unsupported.append(
                f"This custom tune is fixed for a {fixed_transmission.value} gearbox. "
                "Set its Transmission option to AT/MT, flash the tune, then check "
                "compatibility again."
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


@dataclass
class ConnectedSwapSession:
    token: str | None
    status: str
    title: str
    reasons: tuple[str, ...]
    warnings: tuple[str, ...]
    changes: tuple[str, ...]
    source: Transmission | None
    target: Transmission
    family: str | None
    chassis: str | None
    dme_ident: bytes = b""
    program: str | None = None
    order_format: OrderFormat | None = None
    ews_zcs: ZcsHolderState | None = None
    ews_coding: CodingState | None = None
    cluster: ClusterStoreState | None = None
    akmb_fa: FaHolderState | None = None
    alsz_fa: FaHolderState | None = None
    stability: CodingState | Mk60State | None = None
    target_zcs: bytes | None = None
    target_ews_coding: bytes | None = None
    target_fa: bytes | None = None
    target_cluster: bytes | None = None
    target_stability: bytes | None = None
    selector: MS41Selector | None = None
    eeprom_before: bytes | None = None
    eeprom_target: bytes | None = None
    eeprom_variant: str | None = None
    egs_family: str | None = None
    egs_ident: bytes | None = None
    egs_zb: int | None = None
    phase: str = "prepared"
    written: list[str] = field(default_factory=list)
    archive_path: str | None = None

    @property
    def ready(self) -> bool:
        return self.status == "ready" and bool(self.token)

    def wire(self, *, completed: bool = False,
             requires_key_cycle: bool = False) -> dict:
        return {
            "token": self.token,
            "status": self.status,
            "title": self.title,
            "reasons": list(self.reasons),
            "warnings": list(self.warnings),
            "changes": list(self.changes),
            "source": self.source.value if self.source else "unknown",
            "target": self.target.value,
            "family": self.family,
            "chassis": self.chassis,
            "requires_key_cycle": bool(requires_key_cycle),
            "phase": self.phase,
            "completed": bool(completed),
        }


def ms41_eeprom_record(data: bytes, family: str) -> bytes:
    """Return the four-byte transmission record from a record or legacy dump."""
    data = bytes(data)
    if len(data) == 4:
        return data
    if len(data) == 512:
        offset = MS41_EEPROM_RECORD_ADDRESS[family]
        return data[offset:offset + 4]
    raise ValueError("DME transmission record must be exactly four bytes")


def connected_swap_eeprom_record(
        session: ConnectedSwapSession, *, target: bool = False) -> bytes:
    data = session.eeprom_target if target else session.eeprom_before
    if data is None or session.family not in MS41_EEPROM_RECORD_ADDRESS:
        raise ValueError("this conversion has no MS41 transmission record")
    return ms41_eeprom_record(data, session.family)


def _require_supported_connected_swap(session: ConnectedSwapSession) -> None:
    if session.chassis in {"E36", "E39"} and session.family in {
            "MS41.0", "MS41.1", "MS41.2", "MS41.3"}:
        return
    if session.chassis == "E46" and session.family in {"MS42", "MS43"}:
        return
    raise ValueError(
        "this K-line build supports complete transmission conversion only on "
        "reviewed K-line-accessible E36/E39 MS41 and E46 MS42/MS43 profiles")


def _blocked_swap(target: Transmission, title: str, reason: str,
                  *, family: str | None = None,
                  chassis: str | None = None,
                  source: Transmission | None = None) -> ConnectedSwapSession:
    return ConnectedSwapSession(
        None, "unsupported", title, (reason,), (), (), source, target,
        family, chassis, phase="blocked",
    )


def _classify_ms41_chassis(raw_zcs: bytes) -> str:
    prefix = decode_zcs(raw_zcs).gm.hex().upper()[:4]
    e36 = {item for pair in _E36_ZCS_PAIRS for item in pair[:2]}
    e39 = {item for pair in _E39_ZCS_PAIRS for item in pair[:2]}
    if prefix in e36:
        return "E36"
    if prefix in e39:
        return "E39"
    raise ValueError(f"connected MS41 vehicle type {prefix} has no exact swap profile")


def _read_ms41_stability(ds2, chassis: str) -> CodingState:
    from ds2 import DS2Timeout

    if chassis == "E39":
        return read_asc5_transmission(ds2, chassis)
    try:
        return read_asc5_transmission(ds2, chassis)
    except (ValueError, DS2Timeout) as asc_error:
        try:
            return read_mk20_transmission(ds2)
        except Exception:
            raise asc_error


def _read_e46_stability(ds2) -> CodingState | Mk60State:
    from ds2 import DS2Timeout
    try:
        return read_mk60_transmission(ds2)
    except DS2Timeout:
        return read_mk20_transmission(ds2)


def _stability_transmission(state: CodingState | Mk60State) -> Transmission:
    return (mk60_transmission(state) if isinstance(state, Mk60State)
            else coding_transmission(state))


def _probe_egs(ds2) -> tuple[str, bytes] | None:
    from ds2 import DS2Timeout
    try:
        return read_egs_family(ds2)
    except DS2Timeout:
        return None


def _prepare_ms41_swap(ds2, dme_ident: bytes, program: str, family: str,
                       target: Transmission,
                       eeprom_image: bytes | None) -> ConnectedSwapSession:
    from ds2 import DS2Timeout
    from engines.softbsl import eeprom_ram

    ews = read_ews_zcs(ds2)
    chassis = _classify_ms41_chassis(ews.raw)
    allowed_ews = ({1, 2, 82} if chassis == "E36" else {1, 2, 81})
    if ews.coding_index not in allowed_ews:
        raise ValueError(
            f"{chassis} immobilizer coding index {ews.coding_index:02d} is not supported")
    production = read_ews_production_month(ds2) if chassis == "E39" else None
    zcs_target = derive_ms41_zcs_target(ews.raw, chassis, target, production)
    if zcs_target.requires_ews3_ci82 and ews.coding_index != 82:
        raise ValueError("this E36 vehicle type requires the exact EWS3 coding profile")
    ews_coding = read_ews_transmission(ds2)
    if (ews_coding.ident != ews.ident
            or ews_coding.coding_index != ews.coding_index):
        raise ValueError("immobilizer identity changed during compatibility checking")
    if (zcs_target.source is Transmission.AUTOMATIC
            and not ews_starter_interlock_active(ews_coding)):
        raise ValueError("automatic vehicle order requires the immobilizer starter interlock")

    try:
        cluster = read_ms41_cluster_store(ds2, chassis)
    except DS2Timeout:
        if chassis != "E36":
            raise
        return _blocked_swap(
            target, "E36 requires ADS/L-line access",
            "The fitted instrument cluster did not answer through the supported "
            "Compact K-line route. Ordinary E36 clusters require ADS/L-line access. "
            "No coding was changed.",
            family=family, chassis=chassis, source=zcs_target.source,
        )
    except ValueError as error:
        if chassis != "E36":
            raise
        return _blocked_swap(
            target, "E36 instrument cluster is not compatible",
            f"{error}. No coding was changed.",
            family=family, chassis=chassis, source=zcs_target.source,
        )
    if encode_zcs(cluster_zcs(cluster)) != ews.raw:
        raise ValueError("the immobilizer and instrument cluster contain different vehicle orders")
    if cluster_transmission(cluster) is not zcs_target.source:
        raise ValueError("instrument-cluster transmission coding disagrees with the vehicle order")
    try:
        stability = _read_ms41_stability(ds2, chassis)
    except DS2Timeout:
        if chassis != "E36":
            raise
        return _blocked_swap(
            target, "E36 traction control requires ADS/L-line access",
            "The fitted traction-control module is not available through the "
            "supported ASC5/MK20 K-line routes. ASC+T requires ADS/L-line access. "
            "No coding was changed.",
            family=family, chassis=chassis, source=zcs_target.source,
        )
    except ValueError as error:
        if chassis != "E36":
            raise
        return _blocked_swap(
            target, "E36 traction control is not compatible",
            f"{error}. No coding was changed.",
            family=family, chassis=chassis, source=zcs_target.source,
        )
    if coding_transmission(stability) is not zcs_target.source:
        raise ValueError("traction/stability-control coding disagrees with the vehicle order")

    selector = ms41_selector(ds2)
    if selector is not MS41Selector.DYNAMIC:
        fixed = (Transmission.MANUAL if selector is MS41Selector.MANUAL_ONLY
                 else Transmission.AUTOMATIC)
        raise ValueError(
            f"this custom tune is fixed for {fixed.value}; set its Transmission "
            "option to AT/MT, flash the tune, then check compatibility again")
    if eeprom_image is None:
        raise ValueError(
            "read the exact four-byte DME transmission record before compatibility checking")
    if len(eeprom_image) == 512:
        # Legacy archives/callers may still supply a physical dump. New coding
        # sessions retain only the exact record this operation can change.
        eeprom_image = eeprom_ram.validate_write_image(eeprom_image, family)
        layouts = eeprom_ram.detect_layouts(eeprom_image)
        if family not in layouts and not (
                family in {"MS41.2", "MS41.3"}
                and set(layouts) == {"MS41.2", "MS41.3"}):
            raise ValueError("DME identity and EEPROM layout do not agree")
    eeprom_before = ms41_eeprom_record(eeprom_image, family)
    record = eeprom_ram.decode_transmission_record(eeprom_before)
    if not record["check_ok"]:
        raise ValueError("the DME EEPROM transmission record checksum is invalid")
    if record["mode"] != zcs_target.source.value:
        raise ValueError("DME EEPROM transmission mode disagrees with the vehicle order")
    if read_ms41_runtime_transmission(ds2, family) is not zcs_target.source:
        raise ValueError("engine-computer runtime transmission mode disagrees with the vehicle order")
    eeprom_target = eeprom_ram.make_transmission_record_from_record(
        eeprom_before,
        "at" if target is Transmission.AUTOMATIC else "mt",
    )

    egs = _probe_egs(ds2)
    egs_family = egs[0] if egs else None
    egs_ident = egs[1] if egs else None
    egs_zb = None
    if target is Transmission.MANUAL and egs is not None:
        raise ValueError(
            "the automatic-transmission computer is still communicating; finish the "
            "mechanical swap and disconnect it before coding")
    if target is Transmission.AUTOMATIC:
        if egs is None:
            raise ValueError(
                "a compatible automatic-transmission computer is not communicating")
        expected_family, allowed_zb = _ms41_egs_admission(zcs_target)
        if egs_family != expected_family:
            raise ValueError(
                f"this vehicle requires a supported {expected_family} transmission computer")
        egs_family, egs_ident, egs_zb = read_egs_aif_zb(
            ds2, family=egs_family, ident=egs_ident)
        if egs_zb not in allowed_zb:
            raise ValueError(
                f"{egs_family} assembly {egs_zb} is not reviewed for {zcs_target.target_type}")

    if zcs_target.source is target:
        return _blocked_swap(
            target, f"Already configured for a {target.value} transmission",
            "The vehicle order, immobilizer, instrument cluster, traction/stability "
            "control, DME, EEPROM, and transmission-computer state already agree "
            "with the selected transmission.",
            family=family, chassis=chassis, source=zcs_target.source,
        )

    cluster_target = build_ms41_cluster_target(
        cluster, zcs_target.raw, target, four_speed=zcs_target.four_speed,
        steptronic=zcs_target.steptronic)
    ews_coding_target = build_ews_transmission_target(ews_coding, target)
    stability_target = bytearray(stability.data)
    if stability.name == "ASC5":
        stability_target[0] = ((stability_target[0] & ~0x10)
                               | (0x10 if target is Transmission.AUTOMATIC else 0))
    else:
        stability_target[2] = ((stability_target[2] & ~0x80)
                               | (0x80 if target is Transmission.AUTOMATIC else 0))
    changes = (
        f"Update the {chassis} vehicle order in the immobilizer and instrument cluster.",
        f"Set the immobilizer starter interlock for {target.value} operation.",
        f"Recode the instrument cluster and traction/stability control for {target.value}.",
        "Update only the DME EEPROM transmission record and verify its checksum/readback.",
        "Require an ignition cycle, then independently verify every changed module.",
    )
    warnings = ((
        "Manual coding disables the immobilizer starter-interlock input. "
        "A retained clutch-start switch will no longer block cranking.",
    ) if target is Transmission.MANUAL else ())
    return ConnectedSwapSession(
        uuid.uuid4().hex, "ready", f"Ready to convert {chassis} to {target.value}",
        (), warnings, changes, zcs_target.source, target, family, chassis,
        dme_ident=dme_ident, program=program, order_format=OrderFormat.ZCS,
        ews_zcs=ews, ews_coding=ews_coding, cluster=cluster,
        stability=stability, target_zcs=zcs_target.raw,
        target_ews_coding=ews_coding_target, target_cluster=cluster_target,
        target_stability=bytes(stability_target), selector=selector,
        eeprom_before=eeprom_before, eeprom_target=eeprom_target,
        eeprom_variant=family, egs_family=egs_family,
        egs_ident=egs_ident, egs_zb=egs_zb,
    )


def _prepare_e46_swap(ds2, dme_ident: bytes, program: str, family: str,
                      target: Transmission) -> ConnectedSwapSession:
    cluster = read_e46_cluster_store(ds2)
    if cluster.coding_index >= 20:
        raise ValueError("M3 sequential-transmission profiles are not a manual/automatic swap path")

    production = read_ews_production_month(ds2)
    order_format = (OrderFormat.FA if family == "MS43" and production >= (2001, 9)
                    else OrderFormat.ZCS)
    ews = None
    akmb = alsz = None
    zcs = fa = None
    source: Transmission
    target_zcs = target_fa = None
    if order_format is OrderFormat.FA:
        akmb = read_akmb_fa(ds2)
        alsz = read_alsz_fa(ds2)
        if akmb.stream != alsz.stream:
            raise ValueError("instrument cluster and lighting module contain different vehicle orders")
        fa = decode_fa_v2(akmb.stream)
        source = Transmission.AUTOMATIC if fa.automatic else Transmission.MANUAL
        target_fa = encode_fa_v2(fa.with_automatic(target is Transmission.AUTOMATIC))
    else:
        ews = read_ews_zcs(ds2)
        cluster_raw = encode_zcs(cluster_zcs(cluster))
        if ews.raw != cluster_raw:
            raise ValueError("immobilizer and instrument cluster contain different vehicle orders")
        zcs = decode_zcs(ews.raw)
        source = Transmission.AUTOMATIC if zcs.automatic else Transmission.MANUAL
        target_zcs = encode_zcs(zcs.with_automatic(target is Transmission.AUTOMATIC))

    expected_egs_family, allowed_zb = _e46_egs_admission(
        family, order_format, zcs=zcs, fa=fa, production=production)

    ews_coding = read_ews_transmission(ds2)
    if ews_coding.coding_index != 81:
        raise ValueError("E46 transmission conversion requires the exact EWS3 coding profile")
    if (ews is not None
            and (ews_coding.ident != ews.ident
                 or ews_coding.coding_index != ews.coding_index)):
        raise ValueError("immobilizer identity changed during compatibility checking")
    if (source is Transmission.AUTOMATIC
            and not ews_starter_interlock_active(ews_coding)):
        raise ValueError("automatic vehicle order requires the immobilizer starter interlock")

    if read_e46_dme_transmission(ds2) is not source:
        raise ValueError(
            "engine-computer active transmission configuration disagrees with the vehicle order")
    if e46_cluster_transmission(cluster) is not source:
        raise ValueError("instrument-cluster transmission coding disagrees with the vehicle order")
    stability = _read_e46_stability(ds2)
    if _stability_transmission(stability) is not source:
        raise ValueError("stability-control coding disagrees with the vehicle order")

    egs = _probe_egs(ds2)
    egs_family = egs[0] if egs else None
    egs_ident = egs[1] if egs else None
    egs_zb = None
    if target is Transmission.MANUAL and egs is not None:
        raise ValueError(
            "the automatic-transmission computer is still communicating; finish the "
            "mechanical swap and disconnect it before coding")
    if target is Transmission.AUTOMATIC:
        if egs is None:
            raise ValueError("a compatible automatic-transmission computer is not communicating")
        if egs_family != expected_egs_family:
            raise ValueError(
                f"this vehicle requires a supported {expected_egs_family} transmission computer")
        egs_family, egs_ident, egs_zb = read_egs_aif_zb(
            ds2, family=egs_family, ident=egs_ident)
        if egs_zb not in allowed_zb:
            vehicle_type = fa.vehicle_type if fa is not None else "AM51"
            raise ValueError(
                f"{egs_family} assembly {egs_zb} is not reviewed for {vehicle_type}")

    if source is target:
        return _blocked_swap(
            target, f"Already configured for a {target.value} transmission",
            "The vehicle order, immobilizer, instrument cluster, stability control, "
            "DME, and transmission-computer state already agree with the selected "
            "transmission.",
            family=family, chassis="E46", source=source,
        )

    cluster_target = build_e46_cluster_target(cluster, target_zcs, target)
    ews_coding_target = build_ews_transmission_target(ews_coding, target)
    if isinstance(stability, Mk60State):
        stability_target = bytearray(stability.data)
        stability_target[2] = ((stability_target[2] & ~0xC0)
                               | (0x40 if target is Transmission.AUTOMATIC else 0))
        stability_target[0] = (_xor(stability_target[1:]) + 1) & 0xFF
    else:
        stability_target = bytearray(stability.data)
        stability_target[2] = ((stability_target[2] & ~0x80)
                               | (0x80 if target is Transmission.AUTOMATIC else 0))
    holders = ("instrument cluster and lighting module" if order_format is OrderFormat.FA
               else "immobilizer and instrument cluster")
    changes = (
        f"Update Automatic transmission ({AUTOMATIC_OPTION}) in the {holders}.",
        f"Set the immobilizer starter interlock for {target.value} operation.",
        f"Recode the instrument cluster and stability control for {target.value}.",
        ("Clear all engine adaptations after MS42 coding."
         if family == "MS42" else
         "Reset the learned MS43 transmission variant after coding."),
        "Require an ignition cycle, then independently verify every changed module.",
    )
    warnings = (("Engine adaptations will relearn after the MS42 reset.",)
                if family == "MS42" else ())
    if target is Transmission.MANUAL:
        warnings += (
            "Manual coding disables the immobilizer starter-interlock input. "
            "A retained clutch-start switch will no longer block cranking.",
        )
    return ConnectedSwapSession(
        uuid.uuid4().hex, "ready", f"Ready to convert E46 to {target.value}",
        (), warnings, changes, source, target, family, "E46",
        dme_ident=dme_ident, program=program, order_format=order_format,
        ews_zcs=ews, ews_coding=ews_coding, cluster=cluster,
        akmb_fa=akmb, alsz_fa=alsz, stability=stability,
        target_zcs=target_zcs, target_ews_coding=ews_coding_target,
        target_fa=target_fa,
        target_cluster=cluster_target, target_stability=bytes(stability_target),
        egs_family=egs_family, egs_ident=egs_ident, egs_zb=egs_zb,
    )


def prepare_connected_swap(ds2, target: str | Transmission,
                           *, eeprom_image: bytes | None = None) -> ConnectedSwapSession:
    """Read every required module and return a zero-write compatibility result."""
    try:
        target = Transmission(target)
    except (TypeError, ValueError):
        return _blocked_swap(Transmission.MANUAL, "Unsupported transmission target",
                             f"Transmission target {target!r} is not supported.")
    try:
        dme_ident = bytes(ds2.identify())
        program, family = classify_dme_ident(dme_ident)
        if family.startswith("MS41"):
            return _prepare_ms41_swap(
                ds2, dme_ident, program, family, target, eeprom_image)
        return _prepare_e46_swap(ds2, dme_ident, program, family, target)
    except Exception as error:
        return _blocked_swap(
            target, "This vehicle cannot be converted safely",
            str(error) or type(error).__name__,
        )


def _hex(value: bytes | None) -> str | None:
    return bytes(value).hex().upper() if value is not None else None


_RECOVERY_DATACLASSES = {
    value.__name__: value for value in (
        FaHolderState, ZcsHolderState, CodingState, ClusterStoreState,
        Mk60State, ConnectedSwapSession,
    )
}
_RECOVERY_ENUMS = {
    value.__name__: value for value in (
        OrderFormat, Transmission, MS41Selector,
    )
}


def _encode_recovery_value(value):
    if isinstance(value, Enum):
        return {"$enum": type(value).__name__, "value": value.value}
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, bytes):
        return {"$bytes": value.hex().upper()}
    if is_dataclass(value):
        return {
            "$type": type(value).__name__,
            "fields": {
                item.name: _encode_recovery_value(getattr(value, item.name))
                for item in fields(value)
            },
        }
    if isinstance(value, tuple):
        return {"$tuple": [_encode_recovery_value(item) for item in value]}
    if isinstance(value, list):
        return [_encode_recovery_value(item) for item in value]
    raise TypeError(f"unsupported recovery value {type(value).__name__}")


def _decode_recovery_value(value):
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, list):
        return [_decode_recovery_value(item) for item in value]
    if not isinstance(value, dict):
        raise ValueError("invalid recovery value")
    if set(value) == {"$bytes"}:
        text = value["$bytes"]
        if (not isinstance(text, str) or len(text) % 2
                or any(char not in "0123456789ABCDEF" for char in text)):
            raise ValueError("invalid recovery byte string")
        return bytes.fromhex(text)
    if set(value) == {"$enum", "value"}:
        enum_type = _RECOVERY_ENUMS.get(value["$enum"])
        if enum_type is None:
            raise ValueError("unknown recovery enum")
        try:
            return enum_type(value["value"])
        except (TypeError, ValueError) as error:
            raise ValueError("invalid recovery enum value") from error
    if set(value) == {"$tuple"} and isinstance(value["$tuple"], list):
        return tuple(_decode_recovery_value(item) for item in value["$tuple"])
    if set(value) == {"$type", "fields"}:
        value_type = _RECOVERY_DATACLASSES.get(value["$type"])
        encoded_fields = value["fields"]
        if value_type is None or not isinstance(encoded_fields, dict):
            raise ValueError("unknown recovery record type")
        expected = {item.name for item in fields(value_type)}
        if set(encoded_fields) != expected:
            raise ValueError("recovery record fields do not match the installed version")
        return value_type(**{
            name: _decode_recovery_value(item)
            for name, item in encoded_fields.items()
        })
    raise ValueError("invalid recovery record")


def serialize_connected_swap(session: ConnectedSwapSession) -> dict:
    """Return the complete immutable conversion plan for durable recovery."""
    if not isinstance(session, ConnectedSwapSession) or not session.ready:
        raise ValueError("a ready transmission conversion is required")
    return _encode_recovery_value(session)


def deserialize_connected_swap(payload: dict) -> ConnectedSwapSession:
    """Restore a checked conversion plan; live identities are still re-read."""
    session = _decode_recovery_value(payload)
    if not isinstance(session, ConnectedSwapSession) or not session.ready:
        raise ValueError("recovery archive does not contain a ready conversion")
    if (not isinstance(session.token, str) or len(session.token) != 32
            or any(char not in "0123456789abcdef" for char in session.token)):
        raise ValueError("recovery conversion reference is invalid")
    program, family = classify_dme_ident(session.dme_ident)
    if program != session.program or family != session.family:
        raise ValueError("recovery engine-computer identity is inconsistent")
    _require_supported_connected_swap(session)
    if session.phase != "prepared" or session.written:
        raise ValueError("immutable recovery plan contains mutable progress")
    if family.startswith("MS41"):
        lengths = {
            len(session.eeprom_before or b""),
            len(session.eeprom_target or b""),
        }
        if session.eeprom_variant != family or lengths not in ({4}, {512}):
            raise ValueError("recovery DME EEPROM plan is incomplete")
    elif session.eeprom_before is not None or session.eeprom_target is not None:
        raise ValueError("unexpected DME EEPROM data in E46 recovery plan")
    return session


def load_connected_swap_archive(path: str | os.PathLike) -> ConnectedSwapSession:
    path = Path(path).resolve()
    if not path.is_file() or path.stat().st_size > 2_000_000:
        raise ValueError("transmission recovery archive is missing or too large")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("transmission recovery archive cannot be read") from error
    if not isinstance(payload, dict) or payload.get("schema") != 2:
        raise ValueError("transmission recovery archive version is not supported")
    session = deserialize_connected_swap(payload.get("session"))
    session.archive_path = str(path)
    return session


def archive_connected_swap(session: ConnectedSwapSession,
                           directory: str | os.PathLike) -> Path:
    """Durably archive every pre-write owner before the first module write."""
    if not session.ready:
        raise RuntimeError("only a ready conversion can be archived")
    directory = ensure_directory_durable(directory)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = directory / f"transmission-{session.chassis}-{stamp}-{session.token[:8]}.json"
    payload = {
        "schema": 2,
        "token": session.token,
        "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "program": session.program,
        "family": session.family,
        "chassis": session.chassis,
        "source": session.source.value if session.source else None,
        "target": session.target.value,
        "dme_ident": _hex(session.dme_ident),
        "ews": ({
            "ident": _hex(session.ews_zcs.ident),
            "coding_index": session.ews_zcs.coding_index,
            "zcs": _hex(session.ews_zcs.raw),
            "selector5_tail": _hex(session.ews_zcs.selector5_tail),
        } if session.ews_zcs else None),
        "ews_coding": ({
            "ident": _hex(session.ews_coding.ident),
            "coding_index": session.ews_coding.coding_index,
            "data": _hex(session.ews_coding.data),
        } if session.ews_coding else None),
        "cluster": ({
            "ident": _hex(session.cluster.ident),
            "coding_index": session.cluster.coding_index,
            "first_word": session.cluster.first_word,
            "data": _hex(session.cluster.data),
        } if session.cluster else None),
        "akmb_fa": _hex(session.akmb_fa.before) if session.akmb_fa else None,
        "alsz_fa": _hex(session.alsz_fa.before) if session.alsz_fa else None,
        "stability": _hex(session.stability.data) if session.stability else None,
        "eeprom_record": (
            _hex(connected_swap_eeprom_record(session))
            if session.family in MS41_EEPROM_RECORD_ADDRESS else None
        ),
        "egs": ({
            "family": session.egs_family,
            "ident": _hex(session.egs_ident),
            "aif_zb": session.egs_zb,
        } if session.egs_family else None),
        "session": serialize_connected_swap(session),
    }
    write_new_file_durably(
        path,
        (json.dumps(payload, sort_keys=True, indent=2) + "\n").encode("utf-8"),
    )
    session.archive_path = str(path)
    return path


def _assert_egs_snapshot(ds2, session: ConnectedSwapSession,
                         *, final: bool = False) -> None:
    label = "after coding" if final else "since compatibility checking"
    egs = _probe_egs(ds2)
    if session.target is Transmission.MANUAL:
        if egs is not None:
            raise RuntimeError("automatic-transmission computer is still communicating")
        return
    if (egs is None or session.egs_family is None
            or session.egs_ident is None or session.egs_zb is None):
        raise RuntimeError(f"automatic-transmission computer is unavailable {label}")
    family, ident = egs
    if family != session.egs_family or ident != session.egs_ident:
        raise RuntimeError(f"automatic-transmission computer identity changed {label}")
    _family, _ident, zb = read_egs_aif_zb(
        ds2, family=family, ident=ident)
    if zb != session.egs_zb:
        raise RuntimeError(f"automatic-transmission computer assembly changed {label}")


def _assert_session_fresh(ds2, session: ConnectedSwapSession) -> None:
    if bytes(ds2.identify()) != session.dme_ident:
        raise RuntimeError("engine-computer identity changed since compatibility checking")
    if (session.chassis == "E46" and session.source is not None
            and read_e46_dme_transmission(ds2) is not session.source):
        raise RuntimeError(
            "engine-computer transmission configuration changed since compatibility checking")
    if (session.family and session.family.startswith("MS41")
            and session.source is not None
            and read_ms41_runtime_transmission(ds2, session.family) is not session.source):
        raise RuntimeError(
            "engine-computer runtime transmission mode changed since compatibility checking")
    if session.ews_zcs is not None and read_ews_zcs(ds2) != session.ews_zcs:
        raise RuntimeError("immobilizer vehicle order changed since compatibility checking")
    if (session.ews_coding is not None
            and read_ews_transmission(ds2) != session.ews_coding):
        raise RuntimeError("immobilizer coding changed since compatibility checking")
    if session.cluster is not None:
        current_cluster = (read_e46_cluster_store(ds2) if session.chassis == "E46"
                           else read_ms41_cluster_store(ds2, session.chassis))
        if current_cluster != session.cluster:
            raise RuntimeError("instrument-cluster storage changed since compatibility checking")
    if session.akmb_fa is not None and read_akmb_fa(ds2) != session.akmb_fa:
        raise RuntimeError("instrument-cluster vehicle order changed since compatibility checking")
    if session.alsz_fa is not None and read_alsz_fa(ds2) != session.alsz_fa:
        raise RuntimeError("lighting-module vehicle order changed since compatibility checking")
    if session.stability is not None:
        if isinstance(session.stability, Mk60State):
            current_stability = read_mk60_transmission(ds2)
        elif session.stability.name == "ASC5":
            current_stability = _read_addressed_coding(
                ds2, "ASC5", (session.stability.coding_index,),
                session.stability.address, len(session.stability.data))
        else:
            current_stability = read_mk20_transmission(ds2)
        if current_stability != session.stability:
            raise RuntimeError("stability-control coding changed since compatibility checking")
    _assert_egs_snapshot(ds2, session)


def _connected_owner_names(session: ConnectedSwapSession) -> tuple[str, ...]:
    names = []
    if session.ews_zcs is not None and session.target_zcs is not None:
        names.append("ews_zcs")
    if session.ews_coding is not None and session.target_ews_coding is not None:
        names.append("ews_coding")
    if session.akmb_fa is not None and session.target_fa is not None:
        names.append("akmb_fa")
    if session.alsz_fa is not None and session.target_fa is not None:
        names.append("alsz_fa")
    if session.cluster is not None and session.target_cluster is not None:
        names.append("cluster")
    if session.stability is not None and session.target_stability is not None:
        names.append("stability")
    return tuple(names)


def _connected_owner_target(session: ConnectedSwapSession, name: str) -> bytes:
    if name == "ews_zcs":
        return session.target_zcs + session.ews_zcs.selector5_tail
    if name == "ews_coding":
        return session.target_ews_coding
    if name == "akmb_fa":
        return _prefix_after(session.akmb_fa.before, session.target_fa, 32)
    if name == "alsz_fa":
        return _prefix_after(session.alsz_fa.before, session.target_fa, 16)
    if name == "cluster":
        return session.target_cluster
    if name == "stability":
        return session.target_stability
    raise ValueError(f"unknown transmission conversion owner {name!r}")


def _write_connected_owner(ds2, session: ConnectedSwapSession, name: str) -> None:
    if name == "ews_zcs":
        write_ews_zcs(ds2, session.ews_zcs, session.target_zcs)
    elif name == "ews_coding":
        write_ews_transmission(
            ds2, session.ews_coding, session.target_ews_coding)
    elif name == "akmb_fa":
        write_akmb_fa(ds2, session.akmb_fa, session.target_fa)
    elif name == "alsz_fa":
        write_alsz_fa(ds2, session.alsz_fa, session.target_fa)
    elif name == "cluster":
        if session.chassis == "E46":
            write_e46_cluster_store(ds2, session.cluster, session.target_cluster)
        else:
            write_cluster_store(ds2, session.cluster, session.target_cluster)
    elif name == "stability":
        if isinstance(session.stability, Mk60State):
            written = write_mk60_transmission(ds2, session.stability, session.target)
        elif session.stability.name == "ASC5":
            written = write_asc5_transmission(ds2, session.stability, session.target)
        else:
            written = write_mk20_transmission(ds2, session.stability, session.target)
        if written.data != session.target_stability:
            raise RuntimeError("stability-control target did not match the reviewed plan")
    else:
        raise ValueError(f"unknown transmission conversion owner {name!r}")


def _restore_connected_owner(ds2, session: ConnectedSwapSession, name: str) -> None:
    if name == "stability":
        if isinstance(session.stability, Mk60State):
            restore_mk60(ds2, session.stability)
        else:
            restore_transmission_coding(ds2, session.stability)
    elif name == "cluster":
        if session.chassis == "E46":
            restore_e46_cluster_store(ds2, session.cluster)
        else:
            restore_cluster_store(ds2, session.cluster)
    elif name == "alsz_fa":
        restore_fa_holder(ds2, session.alsz_fa)
    elif name == "akmb_fa":
        restore_fa_holder(ds2, session.akmb_fa)
    elif name == "ews_zcs":
        restore_ews_zcs(ds2, session.ews_zcs)
    elif name == "ews_coding":
        restore_ews_transmission(ds2, session.ews_coding)
    else:
        raise ValueError(f"unknown transmission conversion owner {name!r}")


def rollback_connected_modules(ds2, session: ConnectedSwapSession,
                               *, all_owners: bool = False) -> None:
    errors = []
    names = (_connected_owner_names(session) if all_owners
             else tuple(session.written))
    for name in reversed(names):
        try:
            _restore_connected_owner(ds2, session, name)
        except Exception as error:
            errors.append(f"{name}: {error}")
    if errors:
        raise RuntimeError("conversion rollback needs recovery: " + "; ".join(errors))
    session.written.clear()
    session.phase = "rolled_back"


def write_connected_modules(ds2, session: ConnectedSwapSession,
                            *, archive_dir: str | os.PathLike,
                            journal=None, journal_id: str | None = None,
                            reuse_archive: bool = False,
                            eeprom_current: bytes | None = None) -> dict:
    """Write all normal K-line owners as one verified rollback transaction."""
    _require_supported_connected_swap(session)
    if not session.ready or session.phase != "prepared":
        raise RuntimeError("conversion plan is not ready for writing")
    _assert_session_fresh(ds2, session)
    if session.family and session.family.startswith("MS41"):
        if eeprom_current is None:
            raise RuntimeError(
                "read the DME transmission record again immediately before coding")
        if ms41_eeprom_record(eeprom_current, session.family) != (
                connected_swap_eeprom_record(session)):
            raise RuntimeError(
                "DME transmission record changed since compatibility checking; "
                "no module was written")
    operation_id = journal_id or session.token
    if reuse_archive:
        if not session.archive_path or not Path(session.archive_path).is_file():
            raise RuntimeError("the durable conversion archive is unavailable")
        archive_path = Path(session.archive_path)
    else:
        archive_path = archive_connected_swap(session, archive_dir)
    if journal is not None:
        journal.create(
            operation_id,
            plan={
                "token": session.token,
                "family": session.family,
                "chassis": session.chassis,
                "source": session.source.value if session.source else None,
                "target": session.target.value,
            },
            archive_path=archive_path,
        )
    dme_reset_attempted = False
    try:
        for name in _connected_owner_names(session):
            target = _connected_owner_target(session, name)
            if journal is not None:
                journal.mark_write_intent(
                    operation_id, name,
                    {"target_sha256": sha256(target).hexdigest()},
                )
            session.written.append(name)
            _write_connected_owner(ds2, session, name)
            if journal is not None:
                journal.mark_write_complete(operation_id, name)
        if session.chassis == "E46":
            if journal is not None:
                journal.mark_write_intent(
                    operation_id, "dme_post_coding",
                    {"frame": post_coding_frame(session.family).hex().upper()},
                )
            dme_reset_attempted = True
            response = bytes(ds2.send_frame(
                post_coding_frame(session.family), resp_addr=0x12, timeout=2.0))
            if len(response) < 4 or response[2] != 0xA0:
                raise RuntimeError("engine-computer post-coding reset was rejected")
            if journal is not None:
                journal.mark_write_complete(operation_id, "dme_post_coding")
                journal.mark_awaiting_cycle(operation_id)
            session.phase = "awaiting_cycle"
            session.status = "action_required"
            session.title = "Coding written; ignition cycle required"
        else:
            session.phase = "modules_written"
    except Exception as error:
        try:
            rollback_connected_modules(ds2, session)
        except Exception as rollback_error:
            if journal is not None:
                journal.mark_failed(
                    operation_id,
                    f"write failed: {error}; rollback failed: {rollback_error}",
                )
            raise
        if journal is not None:
            journal.mark_failed(
                operation_id,
                f"write failed and every attempted owner was restored: {error}",
            )
            if not dme_reset_attempted:
                journal.mark_restored(operation_id)
        if dme_reset_attempted:
            session.phase = "recovery"
            session.status = "action_required"
            session.title = "DME reset result needs guided recovery"
        raise
    return session.wire(requires_key_cycle=session.phase == "awaiting_cycle")


def mark_eeprom_write_intent(session: ConnectedSwapSession, journal,
                             *, journal_id: str | None = None,
                             backup_path: str | os.PathLike | None = None,
                             restoring_original: bool = False) -> None:
    target = connected_swap_eeprom_record(
        session, target=not restoring_original)
    details = {"target_sha256": sha256(target).hexdigest()}
    if backup_path is not None:
        details["backup_path"] = str(Path(backup_path).resolve())
    journal.mark_write_intent(
        journal_id or session.token,
        "restore_dme_eeprom" if restoring_original else "dme_eeprom",
        details,
    )


def mark_eeprom_written(session: ConnectedSwapSession, after: bytes,
                        *, journal=None, journal_id: str | None = None) -> dict:
    if session.phase != "modules_written" or session.eeprom_target is None:
        raise RuntimeError("DME EEPROM write is not expected for this conversion")
    if ms41_eeprom_record(after, session.family) != connected_swap_eeprom_record(
            session, target=True):
        raise RuntimeError("DME EEPROM writeback does not match the reviewed target")
    if journal is not None:
        operation_id = journal_id or session.token
        journal.mark_write_complete(operation_id, "dme_eeprom")
        journal.mark_awaiting_cycle(operation_id)
    session.phase = "awaiting_cycle"
    session.status = "action_required"
    session.title = "Coding written; ignition cycle required"
    return session.wire(requires_key_cycle=True)


def _verify_egs(ds2, session: ConnectedSwapSession) -> None:
    _assert_egs_snapshot(ds2, session, final=True)


def verify_connected_swap(ds2, session: ConnectedSwapSession,
                          *, eeprom_after: bytes | None = None,
                          journal=None, journal_id: str | None = None) -> dict:
    """Verify every independent owner after the mandatory ignition cycle."""
    if session.phase != "awaiting_cycle":
        raise RuntimeError("conversion is not awaiting final verification")
    if bytes(ds2.identify()) != session.dme_ident:
        raise RuntimeError("engine-computer identity changed after coding")
    if (session.chassis == "E46"
            and read_e46_dme_transmission(ds2) is not session.target):
        raise RuntimeError("engine-computer active transmission mode is incorrect")
    for name in _connected_owner_names(session):
        if (_read_connected_owner_image(ds2, session, name)
                != _connected_owner_target(session, name)):
            raise RuntimeError(f"{name} final verification failed")
    if session.family and session.family.startswith("MS41"):
        if (eeprom_after is None
                or ms41_eeprom_record(eeprom_after, session.family)
                != connected_swap_eeprom_record(session, target=True)):
            raise RuntimeError(
                "read the DME transmission record again for final verification")
        if read_ms41_runtime_transmission(ds2, session.family) is not session.target:
            raise RuntimeError("engine-computer runtime transmission mode is incorrect")
    _verify_egs(ds2, session)
    if journal is not None:
        journal.mark_final_verified(journal_id or session.token)
    session.phase = "complete"
    session.status = "ready"
    session.title = "Transmission conversion completed and verified"
    return session.wire(completed=True)


def recoverable_connected_swaps(journal) -> tuple:
    """Return the newest unsuperseded durable operation for each conversion."""
    all_records = journal.load_all()
    superseded = {
        str(record.plan.get("supersedes"))
        for record in all_records if record.plan.get("supersedes")
    }
    visible = [record for record in all_records
               if record.incomplete and record.operation_id not in superseded]
    newest = {}
    for record in visible:
        token = str(record.plan.get("token") or "")
        if token:
            current = newest.get(token)
            if (current is None
                    or record.path.stat().st_mtime_ns > current.path.stat().st_mtime_ns):
                newest[token] = record
    return tuple(sorted(
        newest.values(), key=lambda record: record.path.stat().st_mtime_ns,
        reverse=True,
    ))


def settle_superseded_connected_swaps(journal, journal_id: str) -> None:
    """Close every older journal only after its replacement is terminal."""
    record = journal.load(journal_id)
    if record.incomplete:
        raise RuntimeError("replacement transmission recovery is not complete")
    prior_id = record.plan.get("supersedes")
    while prior_id:
        prior = journal.load(str(prior_id))
        next_id = prior.plan.get("supersedes")
        if prior.incomplete:
            if prior.phase != "failed":
                journal.mark_failed(
                    prior.operation_id,
                    f"resolved by recovery operation {journal_id}",
                )
            journal.mark_restored(prior.operation_id)
        prior_id = next_id


def load_connected_swap_journal(record) -> ConnectedSwapSession:
    """Rehydrate an incomplete conversion from its checksum-bound archive."""
    session = load_connected_swap_archive(record.archive_path)
    plan = record.plan
    expected = {
        "token": session.token,
        "family": session.family,
        "chassis": session.chassis,
        "source": session.source.value if session.source else None,
        "target": session.target.value,
    }
    for key, value in expected.items():
        if plan.get(key) != value:
            raise ValueError(f"recovery journal {key} does not match its archive")
    attempted = {write.owner for write in record.writes}
    session.written = [
        name for name in _connected_owner_names(session)
        if name in attempted or f"target_{name}" in attempted
        or f"restore_{name}" in attempted
    ]
    active_phase = record.failed_from if record.phase == "failed" else record.phase
    session.phase = (
        "rollback_awaiting_cycle"
        if active_phase == "awaiting_cycle" and plan.get("action") == "original"
        else "awaiting_cycle"
        if active_phase == "awaiting_cycle"
        else "recovery"
    )
    session.status = "action_required"
    session.title = (
        "Ignition cycle and final verification are still required"
        if "awaiting_cycle" in session.phase
        else "Interrupted transmission conversion found"
    )
    return session


def _connected_owner_before(session: ConnectedSwapSession, name: str) -> bytes:
    if name == "ews_zcs":
        return session.ews_zcs.raw + session.ews_zcs.selector5_tail
    if name == "ews_coding":
        return session.ews_coding.data
    if name == "akmb_fa":
        return session.akmb_fa.before
    if name == "alsz_fa":
        return session.alsz_fa.before
    if name == "cluster":
        return session.cluster.data
    if name == "stability":
        return session.stability.data
    raise ValueError(f"unknown transmission conversion owner {name!r}")


def _read_connected_owner_image(ds2, session: ConnectedSwapSession,
                                name: str) -> bytes:
    """Read a complete owner while tolerating an interrupted data image."""
    if name == "ews_zcs":
        current = _read_ews_zcs_raw(ds2)
        expected = session.ews_zcs
        if (current.ident != expected.ident
                or current.coding_index != expected.coding_index):
            raise RuntimeError("immobilizer identity differs from the recovery archive")
        return current.raw + current.selector5_tail
    if name == "ews_coding":
        current = read_ews_transmission(ds2)
        expected = session.ews_coding
        if (current.ident != expected.ident
                or current.coding_index != expected.coding_index):
            raise RuntimeError("immobilizer identity differs from the recovery archive")
        return current.data
    if name == "akmb_fa":
        ident, coding_index, data = _read_akmb_region(ds2)
        expected = session.akmb_fa
        if ident != expected.ident or coding_index != expected.coding_index:
            raise RuntimeError("instrument-cluster identity differs from the recovery archive")
        return data
    if name == "alsz_fa":
        ident, data = _read_alsz_region(ds2)
        if ident != session.alsz_fa.ident:
            raise RuntimeError("lighting-module identity differs from the recovery archive")
        return data
    if name == "cluster":
        current = (read_e46_cluster_store(ds2, validate=False)
                   if session.chassis == "E46"
                   else read_ms41_cluster_store(ds2, session.chassis))
        expected = session.cluster
        if (current.ident != expected.ident
                or current.coding_index != expected.coding_index):
            raise RuntimeError("instrument-cluster identity differs from the recovery archive")
        return current.data
    if name == "stability":
        expected = session.stability
        if isinstance(expected, Mk60State):
            current = read_mk60_transmission(ds2, validate=False)
        elif expected.name == "ASC5":
            current = _read_addressed_coding(
                ds2, "ASC5", (expected.coding_index,),
                expected.address, len(expected.data))
        else:
            current = read_mk20_transmission(ds2)
        if (current.ident != expected.ident
                or current.coding_index != expected.coding_index):
            raise RuntimeError("stability-control identity differs from the recovery archive")
        return current.data
    raise ValueError(f"unknown transmission conversion owner {name!r}")


def _recovery_owner_attempts(records, owners: tuple[str, ...]
                             ) -> tuple[set[str], set[str]]:
    attempted: set[str] = set()
    uncertain: set[str] = set()
    for record in records:
        for write in record.writes:
            base = next((name for name in owners if write.owner in {
                name, f"restore_{name}", f"target_{name}",
            }), None)
            if base is None:
                continue
            attempted.add(base)
            if not write.complete:
                uncertain.add(base)
    return attempted, uncertain


def _recovery_chain(journal, operation_id: str,
                    archive_path: str | os.PathLike) -> tuple:
    expected_archive = Path(archive_path).resolve()
    records = []
    seen = set()
    while operation_id:
        if operation_id in seen or len(records) >= 32:
            raise RuntimeError("transmission recovery journal chain is invalid")
        seen.add(operation_id)
        record = journal.load(operation_id)
        if record.archive_path.resolve() != expected_archive:
            raise RuntimeError("recovery journal does not match the conversion archive")
        records.append(record)
        operation_id = str(record.plan.get("supersedes") or "")
    return tuple(records)


def recover_connected_modules(ds2, session: ConnectedSwapSession, action: str,
                              *, journal, journal_id: str,
                              supersedes: str,
                              eeprom_current: bytes | None = None) -> dict:
    """Preflight every owner, then repair only the interrupted transaction."""
    _require_supported_connected_swap(session)
    if action not in {"target", "original"}:
        raise ValueError("recovery action must be target or original")
    prior_chain = _recovery_chain(journal, supersedes, session.archive_path)
    if bytes(ds2.identify()) != session.dme_ident:
        raise RuntimeError("engine-computer identity differs from the recovery archive")
    _assert_egs_snapshot(ds2, session)

    owners = _connected_owner_names(session)
    attempted, uncertain = _recovery_owner_attempts(prior_chain, owners)
    current = {
        name: _read_connected_owner_image(ds2, session, name)
        for name in owners
    }
    for name in owners:
        before = _connected_owner_before(session, name)
        target = _connected_owner_target(session, name)
        if name not in attempted and current[name] != before:
            raise RuntimeError(
                f"{name} changed after the interrupted conversion; no recovery write was made")
        if name in attempted:
            if name not in uncertain and current[name] not in {before, target}:
                raise RuntimeError(
                    f"{name} is neither the archived original nor reviewed target")
            if (name in uncertain
                    and (len(current[name]) != len(before)
                         or len(target) != len(before)
                         or any(value not in {old, new} for value, old, new
                                in zip(current[name], before, target)))):
                raise RuntimeError(
                    f"{name} contains changes outside the interrupted target")

    writes = tuple(write for record in prior_chain for write in record.writes)
    write_names = {write.owner: write for write in writes}
    dme_attempted = any(name in write_names for name in {
        "dme_post_coding", "target_dme_post_coding",
        "restore_dme_configuration",
    })
    if session.chassis == "E46":
        dme_mode = read_e46_dme_transmission(ds2)
        allowed_modes = ({session.source, session.target}
                         if dme_attempted else {session.source})
        if dme_mode not in allowed_modes:
            raise RuntimeError("engine-computer coding is not a recoverable source/target state")

    eeprom_attempts = [write for write in writes if write.owner in {
        "dme_eeprom", "restore_dme_eeprom", "target_dme_eeprom",
    }]
    eeprom_attempted = bool(eeprom_attempts)
    if session.family and session.family.startswith("MS41"):
        if eeprom_current is None:
            raise RuntimeError("read the DME transmission record before recovery")
        eeprom_current = ms41_eeprom_record(eeprom_current, session.family)
        before = connected_swap_eeprom_record(session)
        target = connected_swap_eeprom_record(session, target=True)
        if not eeprom_attempted and eeprom_current != before:
            raise RuntimeError(
                "DME transmission record changed after compatibility checking; "
                "no recovery write was made")
        if eeprom_attempted and eeprom_current not in {before, target}:
            if (all(write.complete for write in eeprom_attempts)
                    or any(value not in {old, new} for value, old, new
                           in zip(eeprom_current, before, target))):
                raise RuntimeError(
                    "DME transmission record is not a recoverable interrupted state")
        runtime_mode = read_ms41_runtime_transmission(ds2, session.family)
        allowed_modes = ({session.source, session.target}
                         if eeprom_attempted else {session.source})
        if runtime_mode not in allowed_modes:
            raise RuntimeError("engine-computer runtime mode is not recoverable")

    if (action == "original" and not attempted
            and not dme_attempted and not eeprom_attempted):
        for record in prior_chain:
            if not record.incomplete:
                continue
            if record.phase != "failed":
                journal.mark_failed(
                    record.operation_id,
                    "recovery preflight proved that no write was attempted",
                )
            journal.mark_restored(record.operation_id)
        session.phase = "restored"
        session.status = "ready"
        session.title = "Original coding was unchanged and verified"
        result = session.wire(completed=True)
        result["restored"] = True
        return result

    journal.create(
        journal_id,
        plan={
            "token": session.token,
            "family": session.family,
            "chassis": session.chassis,
            "source": session.source.value if session.source else None,
            "target": session.target.value,
            "action": action,
            "supersedes": supersedes,
        },
        archive_path=session.archive_path,
    )
    try:
        # Only an owner that the interrupted transaction actually attempted is
        # normalized. Untouched archived owners were proven exact above.
        for name in reversed(tuple(name for name in owners if name in attempted)):
            journal.mark_write_intent(
                journal_id, f"restore_{name}",
                {"target_sha256": sha256(
                    _connected_owner_before(session, name)).hexdigest()},
            )
            _restore_connected_owner(ds2, session, name)
            journal.mark_write_complete(journal_id, f"restore_{name}")

        session.written.clear()
        if action == "target":
            for name in owners:
                target = _connected_owner_target(session, name)
                journal.mark_write_intent(
                    journal_id, f"target_{name}",
                    {"target_sha256": sha256(target).hexdigest()},
                )
                session.written.append(name)
                _write_connected_owner(ds2, session, name)
                journal.mark_write_complete(journal_id, f"target_{name}")
            dme_owner = "target_dme_post_coding"
            next_phase = "modules_written"
        else:
            dme_owner = "restore_dme_configuration"
            next_phase = ("rollback_modules_written" if eeprom_attempted
                          else "rollback_awaiting_cycle")

        if session.chassis == "E46":
            frame = post_coding_frame(session.family)
            journal.mark_write_intent(
                journal_id, dme_owner, {"frame": frame.hex().upper()})
            response = bytes(ds2.send_frame(
                frame, resp_addr=0x12, timeout=2.0))
            if len(response) < 4 or response[2] != 0xA0:
                raise RuntimeError("engine-computer post-coding reset was rejected")
            journal.mark_write_complete(journal_id, dme_owner)
            journal.mark_awaiting_cycle(journal_id)
            next_phase = ("awaiting_cycle" if action == "target"
                          else "rollback_awaiting_cycle")
        elif action == "original" and not eeprom_attempted:
            journal.mark_awaiting_cycle(journal_id)
        session.phase = next_phase
        session.status = "action_required"
        session.title = (
            "Reviewed coding repaired; ignition cycle required"
            if action == "target" else
            "Original coding restored; ignition cycle required"
        )
        return session.wire(requires_key_cycle="awaiting_cycle" in next_phase)
    except Exception as error:
        journal.mark_failed(journal_id, str(error) or type(error).__name__)
        session.phase = "recovery"
        raise


def mark_original_eeprom_written(session: ConnectedSwapSession, after: bytes,
                                 *, journal, journal_id: str) -> dict:
    if session.phase != "rollback_modules_written" or session.eeprom_before is None:
        raise RuntimeError("original DME EEPROM restoration is not expected")
    if ms41_eeprom_record(after, session.family) != connected_swap_eeprom_record(session):
        raise RuntimeError(
            "DME transmission record does not match the archived original")
    journal.mark_write_complete(journal_id, "restore_dme_eeprom")
    journal.mark_awaiting_cycle(journal_id)
    session.phase = "rollback_awaiting_cycle"
    session.status = "action_required"
    session.title = "Original coding restored; ignition cycle required"
    return session.wire(requires_key_cycle=True)


def verify_connected_original(ds2, session: ConnectedSwapSession,
                              *, eeprom_after: bytes | None = None,
                              journal=None, journal_id: str | None = None) -> dict:
    """Verify the complete archived source coding after a recovery rollback."""
    if session.phase != "rollback_awaiting_cycle":
        raise RuntimeError("original coding is not awaiting final verification")
    if bytes(ds2.identify()) != session.dme_ident:
        raise RuntimeError("engine-computer identity changed after recovery")
    if (session.chassis == "E46"
            and read_e46_dme_transmission(ds2) is not session.source):
        raise RuntimeError("engine-computer original transmission mode was not restored")
    for name in _connected_owner_names(session):
        if (_read_connected_owner_image(ds2, session, name)
                != _connected_owner_before(session, name)):
            raise RuntimeError(f"{name} original coding was not restored")
    if session.family and session.family.startswith("MS41"):
        if (eeprom_after is None
                or ms41_eeprom_record(eeprom_after, session.family)
                != connected_swap_eeprom_record(session)):
            raise RuntimeError("DME transmission record was not restored")
        if read_ms41_runtime_transmission(ds2, session.family) is not session.source:
            raise RuntimeError("engine-computer original runtime mode was not restored")
    _verify_egs(ds2, session)
    if journal is not None:
        journal.mark_final_verified(journal_id)
    session.phase = "restored"
    session.status = "ready"
    session.title = "Original transmission coding restored and verified"
    result = session.wire(completed=True)
    result["restored"] = True
    return result
