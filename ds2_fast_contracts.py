"""Offline contracts for native fast DS2 on unmodified MS41 ECUs.

This module deliberately contains no serial-port, D2XX, GUI, or flash-session
code. It gives the later transport layer strict frame and response contracts
without weakening the existing low-rate ds2.py execute path.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, IntEnum
from typing import FrozenSet, Optional


ECU_ADDRESS = 0x12
DS2_MIN_FRAME_LENGTH = 4
DS2_MAX_FRAME_LENGTH = 0xFC
FLASH_COMMAND = 0x07
READ_MEMORY_COMMAND = 0x06
# 243 data bytes produce a 252-byte (0xFC) DS2 frame, the accepted frame ceiling.
MAX_FLASH_DATA = 243


class FastDS2Error(Exception):
    """Base class for native-fast offline validation failures."""


class FrameValidationError(FastDS2Error):
    """A byte sequence is not a structurally valid DS2 response frame."""


class ContractViolation(FastDS2Error):
    """A valid DS2 frame violates the response contract for this transition."""

    def __init__(self, message: str, *, response_status: Optional[int] = None):
        self.response_status = (
            None if response_status is None else int(response_status)
        )
        super().__init__(message)


class MissingResponseError(FastDS2Error):
    """No ECU response was available for a request."""


class CommitUnknownError(FastDS2Error):
    """A destructive request was echoed but its commit state cannot be known."""

    retry_allowed = False

    def __init__(self, request: "FlashRequest", reason: str):
        self.request = request
        self.reason = reason
        super().__init__(
            f"commit unknown for flash operation 0x{request.operation:02X} "
            f"at 0x{request.address:06X}: {reason}"
        )


class LinkRate(Enum):
    """ECU divisor-derived rates, rounded to integer host requests.

    LOW remains 9600 in production because the qualified K-Line adapter does
    not transmit/echo when D2XX is opened at 9615.  HIGH is exact and must not
    be substituted with the capture decoder's conventional 192000 label.
    """

    UNKNOWN = 0
    LOW = 9_600
    MID = 19_737
    HIGH = 187_500


class SessionState(Enum):
    """Wire-visible states needed by the future native-fast session."""

    UNKNOWN = "unknown"
    CLOSED = "closed"
    LOW_READY = "low_ready"
    TOKEN_KNOWN = "token_known"
    AUTHORIZED_LOW = "authorized_low"
    ARMED_LOW = "armed_low"
    MID = "mid"
    HIGH_READ = "high_read"
    HIGH_PARTIAL_WRITE = "high_partial_write"
    HIGH_FULL_PROGRAM = "high_full_program"
    HIGH_FULL_TUNE = "high_full_tune"
    LOW_RECOVERY = "low_recovery"
    WRITE_FINALIZE_HIGH = "write_finalize_high"
    POWER_CYCLE_REQUIRED = "power_cycle_required"
    COMMIT_UNKNOWN = "commit_unknown"
    COMPLETE = "complete"
    FAILED = "failed"


class FastOperation(Enum):
    PARTIAL_READ = "partial_read"
    FULL_READ = "full_read"
    PARTIAL_WRITE = "partial_write"
    FULL_WRITE = "full_write"


class ResponseStatus(IntEnum):
    ACK = 0xA0
    CONTEXT_A1 = 0xA1
    READINESS_A2 = 0xA2
    CONTEXT_B0 = 0xB0
    READY_FF = 0xFF


class FlashOperation(IntEnum):
    PARTIAL_PROGRAM = 0x00
    FULL_PROGRAM = 0x02
    EEPROM_WRITE = 0x03
    ERASE = 0x06
    POLL = 0x0F


def xor_bytes(data: bytes) -> int:
    value = 0
    for byte in bytes(data):
        value ^= byte
    return value


def encode_ds2_frame(
    command: int,
    payload: bytes = b"",
    *,
    address: int = ECU_ADDRESS,
) -> bytes:
    """Encode one ordinary DS2 frame without performing any I/O."""

    payload = bytes(payload)
    if not 0 <= int(command) <= 0xFF:
        raise ValueError("DS2 command must fit in one byte")
    if not 0 <= int(address) <= 0xFF:
        raise ValueError("DS2 address must fit in one byte")
    length = len(payload) + DS2_MIN_FRAME_LENGTH
    if length > DS2_MAX_FRAME_LENGTH:
        raise ValueError(
            f"DS2 frame length {length} exceeds maximum {DS2_MAX_FRAME_LENGTH}"
        )
    prefix = bytes((int(address), length, int(command))) + payload
    return prefix + bytes((xor_bytes(prefix),))


@dataclass(frozen=True)
class DS2Frame:
    """A structurally valid DS2 frame without request/response interpretation."""

    raw: bytes
    address: int
    command: int
    payload: bytes


def decode_ds2_frame(
    raw: bytes,
    *,
    expected_address: int = ECU_ADDRESS,
) -> DS2Frame:
    """Validate address, length, and XOR for a request or response frame."""

    raw = bytes(raw)
    if len(raw) < DS2_MIN_FRAME_LENGTH:
        raise FrameValidationError(
            f"DS2 frame is only {len(raw)} bytes; minimum is {DS2_MIN_FRAME_LENGTH}"
        )
    if raw[0] != expected_address:
        raise FrameValidationError(
            f"DS2 frame address is 0x{raw[0]:02X}, expected 0x{expected_address:02X}"
        )
    if raw[1] != len(raw):
        raise FrameValidationError(
            f"DS2 length field is {raw[1]}, actual frame length is {len(raw)}"
        )
    if len(raw) > DS2_MAX_FRAME_LENGTH:
        raise FrameValidationError(
            f"DS2 frame length {len(raw)} exceeds {DS2_MAX_FRAME_LENGTH}"
        )
    if xor_bytes(raw) != 0:
        raise FrameValidationError("DS2 frame XOR checksum is invalid")
    return DS2Frame(raw, raw[0], raw[2], raw[3:-1])


@dataclass(frozen=True)
class DS2Response:
    """A structurally valid response plus the context in which it was decoded."""

    raw: bytes
    address: int
    status: int
    payload: bytes
    rate: LinkRate
    state: SessionState
    label: str


def decode_ds2_response(
    raw: bytes,
    *,
    rate: LinkRate = LinkRate.UNKNOWN,
    state: SessionState = SessionState.UNKNOWN,
    label: str = "",
    expected_address: int = ECU_ADDRESS,
) -> DS2Response:
    """Validate address, length, and XOR without assigning global status meaning."""

    frame = decode_ds2_frame(raw, expected_address=expected_address)
    return DS2Response(
        raw=frame.raw,
        address=frame.address,
        status=frame.command,
        payload=frame.payload,
        rate=rate,
        state=state,
        label=label,
    )


@dataclass(frozen=True)
class StatusResponseContract:
    """Contextual status/payload rule for a non-flash response."""

    name: str
    allowed_statuses: FrozenSet[int]
    exact_payload_length: Optional[int] = None

    def validate(self, response: DS2Response) -> DS2Response:
        if response.status not in self.allowed_statuses:
            expected = ", ".join(
                f"0x{status:02X}" for status in sorted(self.allowed_statuses)
            )
            raise ContractViolation(
                f"{self.name}: status 0x{response.status:02X}, expected {expected}",
                response_status=response.status,
            )
        if (
            self.exact_payload_length is not None
            and len(response.payload) != self.exact_payload_length
        ):
            raise ContractViolation(
                f"{self.name}: payload length {len(response.payload)}, expected "
                f"{self.exact_payload_length}"
            )
        return response


def selector_ack_contract() -> StatusResponseContract:
    return StatusResponseContract(
        "selector acknowledgement",
        frozenset((ResponseStatus.ACK,)),
        exact_payload_length=0,
    )


def read_response_contract(length: int) -> StatusResponseContract:
    if not 1 <= length <= 247:
        raise ValueError("fast DS2 read length must be 1..247")
    return StatusResponseContract(
        "read-memory response",
        frozenset((ResponseStatus.ACK,)),
        exact_payload_length=length,
    )


def contextual_recovery_contract(
    *statuses: int,
    exact_payload_length: Optional[int] = None,
    name: str = "contextual recovery response",
) -> StatusResponseContract:
    """Create an explicitly scoped A1/A2/B0 contract."""

    if not statuses:
        raise ValueError("at least one contextual status is required")
    allowed_contextual = {
        int(ResponseStatus.CONTEXT_A1),
        int(ResponseStatus.READINESS_A2),
        int(ResponseStatus.CONTEXT_B0),
    }
    normalized = frozenset(int(status) for status in statuses)
    if not normalized <= allowed_contextual:
        raise ValueError("only contextual A1, A2, and B0 statuses may be requested")
    return StatusResponseContract(name, normalized, exact_payload_length)


@dataclass(frozen=True)
class FlashRequest:
    """One planned command-0x07 request in DS2 address order."""

    operation: int
    address: int
    data: bytes = b""

    def __post_init__(self) -> None:
        operation = int(self.operation)
        data = bytes(self.data)
        object.__setattr__(self, "operation", operation)
        object.__setattr__(self, "data", data)
        if operation not in {int(item) for item in FlashOperation}:
            raise ValueError(f"unsupported flash operation 0x{operation:02X}")
        if not 0 <= self.address <= 0xFFFFFF:
            raise ValueError("flash address must fit in three bytes")
        is_program = operation in (
            int(FlashOperation.PARTIAL_PROGRAM),
            int(FlashOperation.FULL_PROGRAM),
        )
        if is_program and not 1 <= len(data) <= MAX_FLASH_DATA:
            raise ValueError(
                f"program payload must be 1..{MAX_FLASH_DATA} bytes, got {len(data)}"
            )
        if operation == int(FlashOperation.EEPROM_WRITE) and len(data) != 4:
            raise ValueError("EEPROM transmission record must be exactly four bytes")
        if not is_program and operation != int(FlashOperation.EEPROM_WRITE) and data:
            raise ValueError("poll and erase requests cannot contain program data")

    @property
    def count(self) -> int:
        return len(self.data)

    @property
    def is_program(self) -> bool:
        return self.operation in (
            int(FlashOperation.PARTIAL_PROGRAM),
            int(FlashOperation.FULL_PROGRAM),
        )

    @property
    def destructive(self) -> bool:
        return self.is_program or self.operation in (
            int(FlashOperation.EEPROM_WRITE),
            int(FlashOperation.ERASE),
        )

    @property
    def advances_cursor(self) -> bool:
        return self.is_program

    @property
    def payload(self) -> bytes:
        return (
            bytes((self.operation,))
            + self.address.to_bytes(3, "big")
            + bytes((self.count,))
            + self.data
        )

    @property
    def frame(self) -> bytes:
        return encode_ds2_frame(FLASH_COMMAND, self.payload)


@dataclass(frozen=True)
class FlashReply:
    operation: int
    address: int
    count: int
    status: int
    response: DS2Response


@dataclass(frozen=True)
class FlashReplyContract:
    """Expected six-byte flash payload for one labeled request."""

    name: str
    operation: int
    address: int
    count: Optional[int]
    allowed_statuses: FrozenSet[int] = frozenset((0x01,))

    def validate(self, response: DS2Response) -> FlashReply:
        StatusResponseContract(
            self.name,
            frozenset((ResponseStatus.ACK,)),
            exact_payload_length=6,
        ).validate(response)
        operation = response.payload[0]
        address = int.from_bytes(response.payload[1:4], "big")
        count = response.payload[4]
        status = response.payload[5]
        if operation != self.operation:
            raise ContractViolation(
                f"{self.name}: operation 0x{operation:02X}, expected "
                f"0x{self.operation:02X}"
            )
        if address != self.address:
            raise ContractViolation(
                f"{self.name}: address/cursor 0x{address:06X}, expected "
                f"0x{self.address:06X}"
            )
        if self.count is not None and count != self.count:
            raise ContractViolation(
                f"{self.name}: count {count}, expected {self.count}"
            )
        if status not in self.allowed_statuses:
            expected = ", ".join(
                f"0x{item:02X}" for item in sorted(self.allowed_statuses)
            )
            raise ContractViolation(
                f"{self.name}: flash status 0x{status:02X}, expected "
                f"{expected}"
            )
        return FlashReply(operation, address, count, status, response)


def flash_reply_contract(
    mode: FastOperation,
    request: FlashRequest,
    *,
    allowed_statuses: FrozenSet[int] = frozenset((0x01,)),
) -> FlashReplyContract:
    """Build the mode-specific reply rule observed in the captures."""

    if mode is FastOperation.PARTIAL_WRITE:
        if request.operation == int(FlashOperation.FULL_PROGRAM):
            raise ValueError("operation 0x02 is not valid in the partial-write state")
        if request.operation in (
            int(FlashOperation.PARTIAL_PROGRAM),
            int(FlashOperation.ERASE),
        ):
            response_operation = int(FlashOperation.PARTIAL_PROGRAM)
        else:
            response_operation = request.operation
    elif mode is FastOperation.FULL_WRITE:
        if request.operation == int(FlashOperation.PARTIAL_PROGRAM):
            raise ValueError("operation 0x00 is not valid in the full-write state")
        if request.operation == int(FlashOperation.EEPROM_WRITE):
            raise ValueError("operation 0x03 is not valid in the full-write state")
        response_operation = request.operation
    else:
        raise ValueError("flash reply contracts require a write operation mode")

    expected_address = (
        request.address + request.count
        if request.advances_cursor
        else request.address
    )
    # Stock EEPROM operation 0x03 leaves EA00:EA02 unchanged and copies an
    # otherwise stale RL6 into the reply count byte.  Bind its operation,
    # original address, status, and immediate readback; do not invent a count.
    expected_count = (
        None
        if request.operation == int(FlashOperation.EEPROM_WRITE)
        else request.count if request.advances_cursor else 0
    )
    return FlashReplyContract(
        name=(
            f"{mode.value} operation 0x{request.operation:02X} "
            f"at 0x{request.address:06X}"
        ),
        operation=response_operation,
        address=expected_address,
        count=expected_count,
        allowed_statuses=frozenset(int(status) for status in allowed_statuses),
    )


def validate_flash_exchange(
    mode: FastOperation,
    request: FlashRequest,
    raw_response: Optional[bytes],
    *,
    echo_complete: bool,
    rate: LinkRate = LinkRate.HIGH,
    state: SessionState = SessionState.UNKNOWN,
    label: str = "",
    allowed_statuses: FrozenSet[int] = frozenset((0x01,)),
) -> FlashReply:
    """Validate one exchange and classify an unreadable destructive ACK."""

    if raw_response is None:
        if request.destructive and echo_complete:
            raise CommitUnknownError(request, "no ECU response")
        raise MissingResponseError("no ECU response")
    try:
        response = decode_ds2_response(
            raw_response,
            rate=rate,
            state=state,
            label=label,
        )
    except FrameValidationError as error:
        if request.destructive and echo_complete:
            raise CommitUnknownError(request, str(error)) from error
        raise
    return flash_reply_contract(
        mode,
        request,
        allowed_statuses=allowed_statuses,
    ).validate(response)
