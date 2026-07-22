"""Pure planning tests; none of these tests can open serial hardware."""

from collections import Counter

import pytest

from ds2_fast_contracts import FastOperation, FlashOperation, MAX_FLASH_DATA
from ds2_fast_plans import (
    FINAL_POLL_ADDRESS,
    FULL_IMAGE_SIZE,
    PROGRAM_CONTROL_ADDRESS,
    PROGRAM_HIGH_END,
    PROGRAM_HIGH_START,
    PROGRAM_ERASE_TAIL_START,
    PROGRAM_LOW_END,
    PROGRAM_LOW_START,
    ROM_BLOCK_SIZE,
    TUNE_END,
    TUNE_ERASE_ADDRESS,
    TUNE_PRE_ERASE_POLL_ADDRESS,
    TUNE_SECTOR_END,
    TUNE_SIZE,
    TUNE_START,
    UNMAPPED_RANGE,
    PlanValidationError,
    assemble_read_pass,
    build_fast_full_read_plan,
    build_fast_full_write_plan,
    build_fast_partial_read_plan,
    build_fast_partial_write_plan,
    ds2_image_to_file_layout,
    file_image_to_ds2_layout,
)
from tests.conftest import ref, ref_partial


def _assert_no_program_request_crosses_a_rom_block(requests):
    for request in requests:
        if not request.is_program:
            continue
        assert request.count <= MAX_FLASH_DATA
        assert request.address // ROM_BLOCK_SIZE == (
            request.address + request.count - 1
        ) // ROM_BLOCK_SIZE


def test_partial_read_plan_matches_the_captured_bulk_shape():
    plan = build_fast_partial_read_plan()
    assert plan.operation is FastOperation.PARTIAL_READ
    assert plan.liveness_probe.address == 0x1000C
    assert plan.liveness_probe.count == 4
    assert len(plan.passes) == 1
    assert plan.requests_per_pass == 100
    assert plan.bytes_per_pass == TUNE_SIZE
    assert plan.passes[0][0].address == TUNE_START
    assert plan.passes[0][-1].address + plan.passes[0][-1].count == TUNE_END

    verified = build_fast_partial_read_plan(verified=True)
    assert len(verified.passes) == 2
    assert verified.passes[0] == verified.passes[1]


def test_full_read_plan_covers_all_240_kib_twice_and_never_the_hole():
    plan = build_fast_full_read_plan()
    assert plan.operation is FastOperation.FULL_READ
    assert len(plan.passes) == 2
    assert plan.bytes_per_pass == 240 * 1024
    assert plan.requests_per_pass == 15 * 67
    assert plan.passes[0] == plan.passes[1]
    for request in plan.passes[0]:
        assert not (
            request.address < UNMAPPED_RANGE.end
            and request.address + request.count > UNMAPPED_RANGE.start
        )


def test_full_read_assembly_fills_only_the_known_hole_and_layout_round_trips():
    plan = build_fast_full_read_plan(pass_count=1)
    payloads = [
        bytes(((request.address + offset) & 0xFF) for offset in range(request.count))
        for request in plan.passes[0]
    ]
    ds2_image = assemble_read_pass(plan.passes[0], payloads)
    assert ds2_image[UNMAPPED_RANGE.start : UNMAPPED_RANGE.end] == (
        b"\xFF" * UNMAPPED_RANGE.size
    )
    assert ds2_image[0x0BFFF] == 0xFF
    assert ds2_image[0x10000] == 0x00

    file_image = ds2_image_to_file_layout(ds2_image)
    assert file_image_to_ds2_layout(file_image) == ds2_image


def test_read_assembly_rejects_missing_short_or_overlapping_payloads():
    plan = build_fast_partial_read_plan()
    requests = plan.passes[0][:2]
    with pytest.raises(PlanValidationError, match="count"):
        assemble_read_pass(requests, (b"x" * requests[0].count,))
    with pytest.raises(PlanValidationError, match="returned"):
        assemble_read_pass(
            requests,
            (b"x" * (requests[0].count - 1), b"y" * requests[1].count),
        )
    with pytest.raises(PlanValidationError, match="overlaps"):
        assemble_read_pass(
            (requests[0], requests[0]),
            (b"x" * requests[0].count, b"x" * requests[0].count),
        )


def test_partial_write_reference_reproduces_the_proven_live_plan():
    tune = ref_partial("MS41.3clean")
    erase_tail = b"\xFF" * (TUNE_SECTOR_END - TUNE_END)
    plan = build_fast_partial_write_plan(tune, erase_tail)

    assert plan.operation is FastOperation.PARTIAL_WRITE
    assert plan.erase.operation == FlashOperation.ERASE
    assert plan.erase.address == TUNE_ERASE_ADDRESS
    assert plan.final_poll.operation == FlashOperation.POLL
    assert plan.final_poll.address == FINAL_POLL_ADDRESS
    assert len(plan.program) == 77
    assert sum(request.count for request in plan.program) == 18_373
    assert Counter(request.count for request in plan.program) == {
        243: 75,
        103: 1,
        45: 1,
    }
    assert plan.program[-1].address + plan.program[-1].count == 0x15F80
    assert plan.effective_sector == tune + erase_tail
    _assert_no_program_request_crosses_a_rom_block(plan.program)


def test_partial_write_refuses_a_non_ff_erase_tail():
    tune = b"\xFF" * TUNE_SIZE
    tail = bytearray(b"\xFF" * (TUNE_SECTOR_END - TUNE_END))
    tail[-1] = 0
    with pytest.raises(PlanValidationError, match="non-0xFF"):
        build_fast_partial_write_plan(tune, tail)


def test_full_write_reference_has_exact_control_addresses_and_primer():
    image = ref("MS41.3clean")
    plan = build_fast_full_write_plan(image, image)

    assert plan.operation is FastOperation.FULL_WRITE
    assert [request.address for request in plan.program_polls] == [
        PROGRAM_CONTROL_ADDRESS,
        PROGRAM_CONTROL_ADDRESS,
    ]
    assert plan.program_erase.address == PROGRAM_CONTROL_ADDRESS
    assert plan.primer.address == PROGRAM_LOW_START
    assert plan.primer.data == b"\xFF" * 128
    assert [request.address for request in plan.tune_polls_before] == [
        TUNE_PRE_ERASE_POLL_ADDRESS,
        TUNE_PRE_ERASE_POLL_ADDRESS,
    ]
    assert plan.tune_erase.address == TUNE_ERASE_ADDRESS
    assert [request.address for request in plan.tune_polls_after] == [
        TUNE_ERASE_ADDRESS,
        TUNE_ERASE_ADDRESS,
    ]
    assert plan.final_poll.address == FINAL_POLL_ADDRESS
    assert len(plan.data_requests) == 651
    _assert_no_program_request_crosses_a_rom_block(plan.data_requests)


def test_write_plans_trim_the_final_request_to_the_last_non_ff_byte():
    tune = bytearray(b"\xFF" * TUNE_SIZE)
    tune[0] = 0
    tune[MAX_FLASH_DATA + 4] = 0
    partial = build_fast_partial_write_plan(
        bytes(tune), b"\xFF" * (TUNE_SECTOR_END - TUNE_END)
    )
    assert [request.count for request in partial.program] == [243, 5]

    ds2_image = bytearray(b"\xFF" * FULL_IMAGE_SIZE)
    ds2_image[PROGRAM_LOW_START] = 0
    ds2_image[PROGRAM_LOW_START + MAX_FLASH_DATA + 4] = 0
    image = ds2_image_to_file_layout(ds2_image)
    full = build_fast_full_write_plan(image, image, program_only=True)
    assert [request.count for request in full.program] == [243, 5]


@pytest.mark.parametrize(
    "ds2_address",
    (
        0x00010,
        0x08000,
        0x0C000,
        0x16000,
    ),
)
def test_full_write_refuses_target_differences_in_every_preserved_class(ds2_address):
    backup_ds2 = bytearray(b"\xFF" * FULL_IMAGE_SIZE)
    target_ds2 = bytearray(backup_ds2)
    target_ds2[ds2_address] = 0
    target = ds2_image_to_file_layout(target_ds2)
    backup = ds2_image_to_file_layout(backup_ds2)
    with pytest.raises(PlanValidationError, match="preserved"):
        build_fast_full_write_plan(target, backup)


def test_full_write_refuses_non_ff_data_in_the_tune_erase_tail():
    ds2_image = bytearray(b"\xFF" * FULL_IMAGE_SIZE)
    ds2_image[TUNE_END] = 0
    image = ds2_image_to_file_layout(ds2_image)
    with pytest.raises(PlanValidationError, match="tune erase tail"):
        build_fast_full_write_plan(image, image)


def test_program_only_plan_does_not_gate_on_untouched_tune_erase_tail():
    ds2_image = bytearray(b"\xFF" * FULL_IMAGE_SIZE)
    ds2_image[TUNE_END] = 0
    image = ds2_image_to_file_layout(ds2_image)

    plan = build_fast_full_write_plan(image, image, program_only=True)

    assert plan.tune == ()


def test_full_write_refuses_non_ff_data_in_the_program_erase_tail():
    ds2_image = bytearray(b"\xFF" * FULL_IMAGE_SIZE)
    ds2_image[PROGRAM_ERASE_TAIL_START] = 0
    image = ds2_image_to_file_layout(ds2_image)
    with pytest.raises(PlanValidationError, match="program erase tail"):
        build_fast_full_write_plan(image, image)


def test_full_write_data_is_confined_to_proven_writable_windows():
    image = ref("MS41.3clean")
    plan = build_fast_full_write_plan(image, image)
    for request in plan.program:
        assert (
            PROGRAM_LOW_START <= request.address < PROGRAM_LOW_END
            or PROGRAM_HIGH_START <= request.address < PROGRAM_HIGH_END
        )
    for request in plan.tune:
        assert TUNE_START <= request.address < TUNE_END
