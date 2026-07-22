"""Offline response-contract tests for native fast DS2."""

import pytest

from ds2_fast_contracts import (
    CommitUnknownError,
    ContractViolation,
    DS2_MAX_FRAME_LENGTH,
    FastOperation,
    FlashOperation,
    FlashRequest,
    FrameValidationError,
    LinkRate,
    MAX_FLASH_DATA,
    MissingResponseError,
    ResponseStatus,
    SessionState,
    contextual_recovery_contract,
    decode_ds2_response,
    encode_ds2_frame,
    flash_reply_contract,
    read_response_contract,
    selector_ack_contract,
    validate_flash_exchange,
)


def _flash_response(operation, address, count, status=0x01):
    payload = (
        bytes((operation,))
        + int(address).to_bytes(3, "big")
        + bytes((count, status))
    )
    return encode_ds2_frame(ResponseStatus.ACK, payload)


def test_selector_frame_matches_the_captured_high_to_low_request():
    frame = encode_ds2_frame(0x90, bytes((0x26,)) + b"6577205163")
    assert frame == bytes.fromhex(
        "12 0F 90 26 36 35 37 37 32 30 35 31 36 33 AB"
    )


def test_decode_validates_length_address_and_xor_before_status():
    good = encode_ds2_frame(ResponseStatus.ACK, b"data")
    response = decode_ds2_response(
        good,
        rate=LinkRate.HIGH,
        state=SessionState.HIGH_READ,
        label="read",
    )
    assert response.payload == b"data"
    assert response.rate is LinkRate.HIGH
    assert response.state is SessionState.HIGH_READ

    bad_length = bytearray(good)
    bad_length[1] += 1
    with pytest.raises(FrameValidationError, match="length"):
        decode_ds2_response(bad_length)

    bad_xor = bytearray(good)
    bad_xor[-1] ^= 0x01
    with pytest.raises(FrameValidationError, match="checksum"):
        decode_ds2_response(bad_xor)

    bad_address = encode_ds2_frame(ResponseStatus.ACK, b"", address=0x13)
    with pytest.raises(FrameValidationError, match="address"):
        decode_ds2_response(bad_address)


def test_a1_a2_and_b0_are_only_accepted_by_an_explicit_context():
    a1 = decode_ds2_response(encode_ds2_frame(ResponseStatus.CONTEXT_A1))
    with pytest.raises(ContractViolation):
        selector_ack_contract().validate(a1)

    recovery = contextual_recovery_contract(
        ResponseStatus.CONTEXT_A1,
        ResponseStatus.CONTEXT_B0,
    )
    assert recovery.validate(a1) is a1

    a2 = decode_ds2_response(encode_ds2_frame(ResponseStatus.READINESS_A2))
    with pytest.raises(ContractViolation):
        recovery.validate(a2)


def test_read_and_selector_contracts_pin_status_and_payload_length():
    selector = decode_ds2_response(encode_ds2_frame(ResponseStatus.ACK))
    selector_ack_contract().validate(selector)

    read = decode_ds2_response(
        encode_ds2_frame(ResponseStatus.ACK, b"\x01\x02\x03\x04")
    )
    read_response_contract(4).validate(read)
    with pytest.raises(ContractViolation, match="payload length"):
        read_response_contract(3).validate(read)


def test_partial_erase_and_program_use_the_zero_reply_contract():
    erase = FlashRequest(FlashOperation.ERASE, 0x10000)
    erase_reply = validate_flash_exchange(
        FastOperation.PARTIAL_WRITE,
        erase,
        _flash_response(0x00, 0x10000, 0),
        echo_complete=True,
        state=SessionState.HIGH_PARTIAL_WRITE,
    )
    assert erase_reply.operation == 0x00

    program = FlashRequest(FlashOperation.PARTIAL_PROGRAM, 0x10100, b"abc")
    program_reply = validate_flash_exchange(
        FastOperation.PARTIAL_WRITE,
        program,
        _flash_response(0x00, 0x10103, 3),
        echo_complete=True,
        state=SessionState.HIGH_PARTIAL_WRITE,
    )
    assert program_reply.address == 0x10103
    assert program_reply.count == 3


def test_full_erase_poll_and_program_echo_their_operations():
    for operation, address in (
        (FlashOperation.POLL, 0x2000),
        (FlashOperation.ERASE, 0x2000),
    ):
        request = FlashRequest(operation, address)
        reply = validate_flash_exchange(
            FastOperation.FULL_WRITE,
            request,
            _flash_response(operation, address, 0),
            echo_complete=True,
        )
        assert reply.operation == operation

    request = FlashRequest(
        FlashOperation.FULL_PROGRAM, 0x2000, b"x" * MAX_FLASH_DATA
    )
    reply = validate_flash_exchange(
        FastOperation.FULL_WRITE,
        request,
        _flash_response(0x02, 0x20F3, MAX_FLASH_DATA),
        echo_complete=True,
    )
    assert reply.address == 0x20F3


def test_program_payload_fills_the_accepted_ds2_frame_ceiling():
    request = FlashRequest(
        FlashOperation.FULL_PROGRAM, 0x2000, b"x" * MAX_FLASH_DATA
    )

    assert MAX_FLASH_DATA == 243
    assert len(request.frame) == DS2_MAX_FRAME_LENGTH == 0xFC
    with pytest.raises(ValueError, match="1..243"):
        FlashRequest(
            FlashOperation.FULL_PROGRAM,
            0x2000,
            b"x" * (MAX_FLASH_DATA + 1),
        )


def test_partial_and_full_program_contracts_cannot_be_interchanged():
    partial = FlashRequest(FlashOperation.PARTIAL_PROGRAM, 0x10000, b"x")
    full = FlashRequest(FlashOperation.FULL_PROGRAM, 0x2000, b"x")
    with pytest.raises(ValueError, match="not valid"):
        flash_reply_contract(FastOperation.FULL_WRITE, partial)
    with pytest.raises(ValueError, match="not valid"):
        flash_reply_contract(FastOperation.PARTIAL_WRITE, full)


@pytest.mark.parametrize(
    "operation,address,count,status,match",
    (
        (0x03, 0x20F3, 243, 0x01, "operation"),
        (0x02, 0x20F2, 243, 0x01, "address/cursor"),
        (0x02, 0x20F3, 242, 0x01, "count"),
        (0x02, 0x20F3, 243, 0x03, "flash status"),
    ),
)
def test_full_program_rejects_every_wrong_reply_field(
    operation, address, count, status, match
):
    request = FlashRequest(
        FlashOperation.FULL_PROGRAM, 0x2000, b"x" * MAX_FLASH_DATA
    )
    with pytest.raises(ContractViolation, match=match):
        validate_flash_exchange(
            FastOperation.FULL_WRITE,
            request,
            _flash_response(operation, address, count, status),
            echo_complete=True,
        )


def test_missing_or_corrupt_destructive_ack_is_commit_unknown_and_never_retryable():
    request = FlashRequest(FlashOperation.FULL_PROGRAM, 0x2000, b"abc")
    with pytest.raises(CommitUnknownError) as missing:
        validate_flash_exchange(
            FastOperation.FULL_WRITE,
            request,
            None,
            echo_complete=True,
        )
    assert missing.value.retry_allowed is False
    assert missing.value.request is request

    corrupt = bytearray(_flash_response(0x02, 0x2003, 3))
    corrupt[-1] ^= 1
    with pytest.raises(CommitUnknownError, match="checksum"):
        validate_flash_exchange(
            FastOperation.FULL_WRITE,
            request,
            corrupt,
            echo_complete=True,
        )


def test_no_echo_does_not_claim_that_a_destructive_request_committed():
    request = FlashRequest(FlashOperation.ERASE, 0x2000)
    with pytest.raises(MissingResponseError):
        validate_flash_exchange(
            FastOperation.FULL_WRITE,
            request,
            None,
            echo_complete=False,
        )
