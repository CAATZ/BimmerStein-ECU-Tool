"""Pure planners and memory-layout helpers for native fast DS2.

Nothing in this module opens a port or sends a byte. Its output is immutable
and must be consumed by a future, separately reviewed transport/session layer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence, Tuple

from ds2_fast_contracts import (
    FastOperation,
    FlashOperation,
    FlashRequest,
    READ_MEMORY_COMMAND,
    encode_ds2_frame,
)


FULL_IMAGE_SIZE = 0x40000
ROM_BLOCK_SIZE = 0x4000
MAX_READ_DATA = 247
MAX_WRITE_DATA = 231

TUNE_START = 0x10000
TUNE_SIZE = 0x6000
TUNE_END = TUNE_START + TUNE_SIZE
TUNE_SECTOR_END = 0x20000

PROGRAM_LOW_START = 0x02000
PROGRAM_LOW_END = 0x06000
PROGRAM_ERASE_TAIL_START = PROGRAM_LOW_END
PROGRAM_ERASE_TAIL_END = 0x08000
PROGRAM_HIGH_START = 0x20000
PROGRAM_HIGH_END = 0x40000

PROGRAM_CONTROL_ADDRESS = 0x02000
TUNE_PRE_ERASE_POLL_ADDRESS = 0x00100
TUNE_ERASE_ADDRESS = 0x10000
FINAL_POLL_ADDRESS = 0x01D07


class PlanValidationError(ValueError):
    """Input cannot be represented safely by the proven stock-ECU contract."""


@dataclass(frozen=True, order=True)
class AddressRange:
    start: int
    end: int
    name: str = ""

    def __post_init__(self) -> None:
        if not 0 <= self.start < self.end <= FULL_IMAGE_SIZE:
            raise ValueError(
                f"invalid DS2 range 0x{self.start:X}..0x{self.end:X}"
            )

    @property
    def size(self) -> int:
        return self.end - self.start


MAPPED_RANGES: Tuple[AddressRange, ...] = (
    AddressRange(0x00000, 0x0C000, "mapped low"),
    AddressRange(0x10000, 0x40000, "mapped high"),
)

UNMAPPED_RANGE = AddressRange(0x0C000, 0x10000, "unmapped hole")

PRESERVED_RANGES: Tuple[AddressRange, ...] = (
    AddressRange(0x00000, PROGRAM_LOW_START, "boot"),
    AddressRange(
        PROGRAM_ERASE_TAIL_START,
        PROGRAM_ERASE_TAIL_END,
        "program erase tail",
    ),
    AddressRange(PROGRAM_ERASE_TAIL_END, TUNE_START, "program gap"),
    AddressRange(TUNE_END, TUNE_SECTOR_END, "tune erase tail"),
)


@dataclass(frozen=True)
class ReadRequest:
    address: int
    count: int

    def __post_init__(self) -> None:
        if not 0 <= self.address <= 0xFFFFFFFF:
            raise ValueError("read address must fit in four bytes")
        if not 1 <= self.count <= MAX_READ_DATA:
            raise ValueError(f"read count must be 1..{MAX_READ_DATA}")
        if self.address + self.count > 0x100000000:
            raise ValueError("read request exceeds four-byte address space")

    @property
    def payload(self) -> bytes:
        return self.address.to_bytes(4, "big") + bytes((self.count,))

    @property
    def frame(self) -> bytes:
        return encode_ds2_frame(READ_MEMORY_COMMAND, self.payload)


@dataclass(frozen=True)
class ReadPlan:
    operation: FastOperation
    liveness_probe: ReadRequest
    passes: Tuple[Tuple[ReadRequest, ...], ...]
    output_size: int
    mapped_ranges: Tuple[AddressRange, ...]

    @property
    def bytes_per_pass(self) -> int:
        if not self.passes:
            return 0
        return sum(request.count for request in self.passes[0])

    @property
    def requests_per_pass(self) -> int:
        return len(self.passes[0]) if self.passes else 0


@dataclass(frozen=True)
class PartialWritePlan:
    operation: FastOperation
    erase: FlashRequest
    program: Tuple[FlashRequest, ...]
    final_poll: FlashRequest
    effective_sector: bytes
    readback_ranges: Tuple[AddressRange, ...]

    @property
    def high_flash_requests(self) -> Tuple[FlashRequest, ...]:
        return (self.erase,) + self.program + (self.final_poll,)


@dataclass(frozen=True)
class FullWritePlan:
    operation: FastOperation
    program_polls: Tuple[FlashRequest, ...]
    program_erase: FlashRequest
    primer: FlashRequest
    program: Tuple[FlashRequest, ...]
    tune_polls_before: Tuple[FlashRequest, ...]
    tune_erase: FlashRequest
    tune: Tuple[FlashRequest, ...]
    tune_polls_after: Tuple[FlashRequest, ...]
    final_poll: FlashRequest
    effective_target_ds2: bytes
    readback_ranges: Tuple[AddressRange, ...]

    @property
    def data_requests(self) -> Tuple[FlashRequest, ...]:
        return (self.primer,) + self.program + self.tune

    @property
    def high_flash_requests(self) -> Tuple[FlashRequest, ...]:
        return (
            self.program_polls
            + (self.program_erase, self.primer)
            + self.program
            + self.tune_polls_before
            + (self.tune_erase,)
            + self.tune
            + self.tune_polls_after
            + (self.final_poll,)
        )


def _read_chunks(start: int, end: int) -> Tuple[ReadRequest, ...]:
    requests = []
    address = start
    while address < end:
        count = min(MAX_READ_DATA, end - address)
        requests.append(ReadRequest(address, count))
        address += count
    return tuple(requests)


def _mapped_block_ranges() -> Tuple[AddressRange, ...]:
    ranges = []
    for block_start in range(0, FULL_IMAGE_SIZE, ROM_BLOCK_SIZE):
        block_end = block_start + ROM_BLOCK_SIZE
        if block_start == UNMAPPED_RANGE.start:
            continue
        ranges.append(
            AddressRange(block_start, block_end, f"mapped block 0x{block_start:05X}")
        )
    return tuple(ranges)


def build_fast_partial_read_plan(*, verified: bool = False) -> ReadPlan:
    """Plan the captured contiguous 24 KiB partial read."""

    requests = _read_chunks(TUNE_START, TUNE_END)
    passes = (requests, requests) if verified else (requests,)
    return ReadPlan(
        operation=FastOperation.PARTIAL_READ,
        liveness_probe=ReadRequest(0x1000C, 4),
        passes=passes,
        output_size=TUNE_SIZE,
        mapped_ranges=(AddressRange(TUNE_START, TUNE_END, "tune"),),
    )

def build_fast_full_read_plan(*, pass_count: int = 2) -> ReadPlan:
    """Plan every mapped 0x4000 block, excluding only the known 16 KiB hole."""

    if pass_count < 1:
        raise ValueError("full read requires at least one pass")
    requests = tuple(
        request
        for address_range in _mapped_block_ranges()
        for request in _read_chunks(address_range.start, address_range.end)
    )
    return ReadPlan(
        operation=FastOperation.FULL_READ,
        liveness_probe=ReadRequest(0x1000C, 4),
        passes=tuple(requests for _ in range(pass_count)),
        output_size=FULL_IMAGE_SIZE,
        mapped_ranges=MAPPED_RANGES,
    )


def assemble_read_pass(
    requests: Sequence[ReadRequest],
    payloads: Sequence[bytes],
    *,
    output_size: int = FULL_IMAGE_SIZE,
    fill: int = 0xFF,
) -> bytes:
    """Assemble addressed read payloads and reject missing/overlapping bytes."""

    if len(requests) != len(payloads):
        raise PlanValidationError(
            f"read payload count {len(payloads)} does not match requests {len(requests)}"
        )
    if not 0 <= fill <= 0xFF:
        raise ValueError("fill must fit in one byte")
    output = bytearray((fill,)) * output_size
    covered = bytearray(output_size)
    for request, payload in zip(requests, payloads):
        payload = bytes(payload)
        if len(payload) != request.count:
            raise PlanValidationError(
                f"read at 0x{request.address:06X} returned {len(payload)} bytes, "
                f"expected {request.count}"
            )
        end = request.address + request.count
        if not 0 <= request.address < end <= output_size:
            raise PlanValidationError(
                f"read at 0x{request.address:06X} lies outside output image"
            )
        if any(covered[request.address:end]):
            raise PlanValidationError(
                f"read at 0x{request.address:06X} overlaps prior data"
            )
        output[request.address:end] = payload
        covered[request.address:end] = b"\x01" * request.count
    return bytes(output)


def swap_adjacent_rom_blocks(image: bytes) -> bytes:
    """Convert between DS2/CPU order and standard file order (its own inverse)."""

    image = bytes(image)
    if len(image) != FULL_IMAGE_SIZE:
        raise PlanValidationError(
            f"full image must be {FULL_IMAGE_SIZE} bytes, got {len(image)}"
        )
    output = bytearray(FULL_IMAGE_SIZE)
    blocks = FULL_IMAGE_SIZE // ROM_BLOCK_SIZE
    for destination_block in range(blocks):
        source_block = destination_block ^ 1
        destination = destination_block * ROM_BLOCK_SIZE
        source = source_block * ROM_BLOCK_SIZE
        output[destination : destination + ROM_BLOCK_SIZE] = image[
            source : source + ROM_BLOCK_SIZE
        ]
    return bytes(output)


def ds2_image_to_file_layout(image: bytes) -> bytes:
    return swap_adjacent_rom_blocks(image)


def file_image_to_ds2_layout(image: bytes) -> bytes:
    return swap_adjacent_rom_blocks(image)


def _does_not_cross_rom_block(request: FlashRequest) -> bool:
    if not request.is_program:
        return True
    return request.address // ROM_BLOCK_SIZE == (
        request.address + request.count - 1
    ) // ROM_BLOCK_SIZE


def _sparse_program_requests(
    data: bytes,
    *,
    start: int,
    operation: FlashOperation,
    trim_final: bool,
) -> Tuple[FlashRequest, ...]:
    """Fixed-size sparse chunks, clamped at every 0x4000 boundary."""

    data = bytes(data)
    last_data_end = len(data)
    while last_data_end and data[last_data_end - 1] == 0xFF:
        last_data_end -= 1
    requests = []
    offset = 0
    while offset < len(data):
        address = start + offset
        boundary = min(
            (address & ~(ROM_BLOCK_SIZE - 1)) + ROM_BLOCK_SIZE,
            start + len(data),
        )
        grid_count = min(MAX_WRITE_DATA, boundary - address)
        count = grid_count
        if trim_final and offset < last_data_end < offset + count:
            count = last_data_end - offset
        chunk = data[offset : offset + count]
        if chunk != b"\xFF" * count:
            request = FlashRequest(int(operation), address, chunk)
            if not _does_not_cross_rom_block(request):
                raise AssertionError("planner emitted a cross-boundary program request")
            requests.append(request)
        offset += grid_count
    return tuple(requests)


def _program_window_requests(
    ds2_image: bytes,
    start: int,
    end: int,
) -> Tuple[FlashRequest, ...]:
    """Plan captured full-write behavior for a program window."""

    requests = []
    unit_start = start
    while unit_start < end:
        unit_end = min(
            (unit_start & ~(ROM_BLOCK_SIZE - 1)) + ROM_BLOCK_SIZE,
            end,
        )
        unit = ds2_image[unit_start:unit_end]
        if unit != b"\xFF" * len(unit):
            last_data = len(unit)
            while unit[last_data - 1] == 0xFF:
                last_data -= 1
            address = unit_start
            data_end = unit_start + last_data
            while address < data_end:
                count = min(MAX_WRITE_DATA, unit_end - address)
                request = FlashRequest(
                    int(FlashOperation.FULL_PROGRAM),
                    address,
                    ds2_image[address : address + count],
                )
                if not _does_not_cross_rom_block(request):
                    raise AssertionError(
                        "planner emitted a cross-boundary program request"
                    )
                requests.append(request)
                address += count
        unit_start = unit_end
    return tuple(requests)


def build_fast_partial_write_plan(
    tune: bytes,
    erase_tail: bytes,
) -> PartialWritePlan:
    tune = bytes(tune)
    erase_tail = bytes(erase_tail)
    if len(tune) != TUNE_SIZE:
        raise PlanValidationError(
            f"partial target must be {TUNE_SIZE} bytes, got {len(tune)}"
        )
    expected_tail = TUNE_SECTOR_END - TUNE_END
    if len(erase_tail) != expected_tail:
        raise PlanValidationError(
            f"tune erase tail must be {expected_tail} bytes, got {len(erase_tail)}"
        )
    non_ff = sum(byte != 0xFF for byte in erase_tail)
    if non_ff:
        raise PlanValidationError(
            f"tune erase tail contains {non_ff} non-0xFF bytes"
        )
    program = _sparse_program_requests(
        tune,
        start=TUNE_START,
        operation=FlashOperation.PARTIAL_PROGRAM,
        trim_final=False,
    )
    return PartialWritePlan(
        operation=FastOperation.PARTIAL_WRITE,
        erase=FlashRequest(int(FlashOperation.ERASE), TUNE_ERASE_ADDRESS),
        program=program,
        final_poll=FlashRequest(int(FlashOperation.POLL), FINAL_POLL_ADDRESS),
        effective_sector=tune + erase_tail,
        readback_ranges=(
            AddressRange(TUNE_START, TUNE_SECTOR_END, "tune physical sector"),
        ),
    )


def _first_difference(left: bytes, right: bytes, address_range: AddressRange) -> int:
    for address in range(address_range.start, address_range.end):
        if left[address] != right[address]:
            return address
    raise AssertionError("range was reported different without a differing byte")


def build_fast_full_write_plan(
    target_file_image: bytes,
    verified_backup_file_image: bytes,
    *,
    program_only: bool = False,
) -> FullWritePlan:
    """Plan the captured full-write phases without performing any I/O.

    ``program_only`` is used by the disposable stock-DS2 bootstrap deployment:
    it keeps the exact program erase/program control sequence but does not
    impose the full-write tune-sector-tail gate, because that sector is never
    erased or programmed by that operation.
    """

    target_ds2 = file_image_to_ds2_layout(target_file_image)
    backup_ds2 = file_image_to_ds2_layout(verified_backup_file_image)

    for address_range in PRESERVED_RANGES:
        target_part = target_ds2[address_range.start : address_range.end]
        backup_part = backup_ds2[address_range.start : address_range.end]
        if target_part != backup_part:
            address = _first_difference(target_ds2, backup_ds2, address_range)
            raise PlanValidationError(
                f"target differs from verified backup in preserved "
                f"{address_range.name} at DS2 0x{address:06X}"
            )

    if not program_only:
        erase_tail = backup_ds2[TUNE_END:TUNE_SECTOR_END]
        non_ff_tail = sum(byte != 0xFF for byte in erase_tail)
        if non_ff_tail:
            raise PlanValidationError(
                f"verified backup tune erase tail contains {non_ff_tail} non-0xFF bytes"
            )

    # The decompiled stock full/program erase path targets 0x2200, 0x4000,
    # and 0x20000.  On the Intel 28F200 geometry the 0x4000 target erases
    # through 0x7FFF, while the capture only rewrites through 0x5FFF.
    # Refuse any source whose erased-but-unwritten 0x6000..0x7FFF tail is
    # not already blank.
    program_erase_tail = backup_ds2[
        PROGRAM_ERASE_TAIL_START:PROGRAM_ERASE_TAIL_END
    ]
    non_ff_program_tail = sum(byte != 0xFF for byte in program_erase_tail)
    if non_ff_program_tail:
        raise PlanValidationError(
            "verified backup program erase tail contains "
            f"{non_ff_program_tail} non-0xFF bytes"
        )

    program = (
        _program_window_requests(target_ds2, PROGRAM_LOW_START, PROGRAM_LOW_END)
        + _program_window_requests(
            target_ds2, PROGRAM_HIGH_START, PROGRAM_HIGH_END
        )
    )
    tune = () if program_only else _sparse_program_requests(
        target_ds2[TUNE_START:TUNE_END],
        start=TUNE_START,
        operation=FlashOperation.FULL_PROGRAM,
        trim_final=True,
    )
    poll_program = FlashRequest(
        int(FlashOperation.POLL), PROGRAM_CONTROL_ADDRESS
    )
    poll_tune_before = FlashRequest(
        int(FlashOperation.POLL), TUNE_PRE_ERASE_POLL_ADDRESS
    )
    poll_tune_after = FlashRequest(int(FlashOperation.POLL), TUNE_ERASE_ADDRESS)
    return FullWritePlan(
        operation=FastOperation.FULL_WRITE,
        program_polls=(poll_program, poll_program),
        program_erase=FlashRequest(
            int(FlashOperation.ERASE), PROGRAM_CONTROL_ADDRESS
        ),
        primer=FlashRequest(
            int(FlashOperation.FULL_PROGRAM),
            PROGRAM_LOW_START,
            b"\xFF" * 128,
        ),
        program=program,
        tune_polls_before=(poll_tune_before, poll_tune_before),
        tune_erase=FlashRequest(
            int(FlashOperation.ERASE), TUNE_ERASE_ADDRESS
        ),
        tune=tune,
        tune_polls_after=(poll_tune_after, poll_tune_after),
        final_poll=FlashRequest(int(FlashOperation.POLL), FINAL_POLL_ADDRESS),
        effective_target_ds2=target_ds2,
        readback_ranges=MAPPED_RANGES,
    )
