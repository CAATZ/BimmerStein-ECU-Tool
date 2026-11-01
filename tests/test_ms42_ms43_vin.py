"""Pure AIF planning and existing protocol readers; transports are fakes only."""

from types import SimpleNamespace

import pytest

from ms42_ms43 import MS42MS43ECU, decode_vin
from ms42_ms43_vin import (AIF_ADDRESSES, AIF_RECORD_COUNT, AIF_RECORD_SIZE, AIF_SIZE,
                          current_vin, decode_aif, encode_vin, plan_vin, read_aif)
from tests.test_ms42_ms43 import Link, reply
from tests.test_ms42_ms43_diagnostics import aif_image, aif_record


OLD_VIN = "WBAAM31010FG12345"
NEW_VIN = "WBAAM31010FG54321"


@pytest.mark.parametrize("vin", [OLD_VIN, NEW_VIN, "123456789ABCDEFGH"])
def test_encoder_matches_independent_bmw_fixture(vin):
    assert encode_vin(vin) == aif_record(vin)[:13]
    assert decode_vin(encode_vin(vin)) == vin
    assert encode_vin("  " + vin.lower() + "  ") == encode_vin(vin)


@pytest.mark.parametrize("vin", ["", OLD_VIN[:-1], OLD_VIN + "1", "I" + OLD_VIN[1:],
                                  "O" + OLD_VIN[1:], "Q" + OLD_VIN[1:],
                                  "Ｗ" + OLD_VIN[1:], "ß" + OLD_VIN[2:],
                                  " " + OLD_VIN[1:], None, 123])
def test_entered_vin_validation(vin):
    with pytest.raises(ValueError, match="VIN must"):
        encode_vin(vin)


@pytest.mark.parametrize("vin", ["0123456789ABCDEFG", "0" * 17])
def test_record_reader_keeps_all_seventeen_characters(vin):
    record = bytearray(b"\xFF" * AIF_RECORD_SIZE)
    record[:13], record[13] = encode_vin(vin), 0
    assert current_vin(aif_image(record)) == vin


def test_legacy_decoder_remains_permissive_and_zero_behavior_unchanged():
    assert decode_vin(aif_record("WBAAM31I10FG12345")[:13]) == "WBAAM31I10FG12345"
    assert decode_vin(bytes(13)) is None
    with pytest.raises(ValueError, match="documented alphabet"):
        decode_vin((63).to_bytes(13, "big"))


def test_latest_valid_vin_and_native_marker_difference_are_explicit():
    last = bytearray(aif_record(NEW_VIN))
    history = aif_image(aif_record(), last)
    assert current_vin(history) == NEW_VIN
    last[13] = 1
    decoded = decode_aif(aif_image(aif_record(), last))
    assert decoded["vin"] == OLD_VIN
    assert decoded["native_vin"] == NEW_VIN
    assert decoded["aif_warnings"] == ["Latest native VIN differs from the BMW-valid AIF VIN"]


@pytest.mark.parametrize("packed,expected", [
    (b"\xA5\x17", "20.10.23"),
    (b"\xFE\x63", "31.12.99"),
    (b"\xE9\x00", "29.02.00"),  # 1900/2000 cannot be distinguished; do not reject a possible leap day.
    (b"\xE9\x04", "29.02.04"),
    (b"\xE9\x01", None),
    (b"\xF1\x00", None),  # 30 February.
    (b"\xFA\x00", None),  # 31 April.
    (b"\x01\x00", None),  # Day zero.
    (b"\x78\x00", None),  # Month zero.
    (b"\x7E\x80", None),  # Month thirteen.
    (b"\x7F\x80", None),  # Month fifteen.
    (b"\x7B\x64", None),  # BMW's two ASCII digits become ':0' for year 100.
    (b"\x7B\x6D", None),  # ':9'.
    (b"\x7B\x6E", None),  # ';0'.
    (b"\x7B\x77", None),  # ';9'.
    (b"\x7B\x78", None),  # '<0'.
    (b"\x7B\x7F", None),  # '<7'; never infer 2027.
])
def test_recorded_date_validation_does_not_assign_a_century(packed, expected):
    record = bytearray(aif_record())
    record[14:16] = packed
    decoded = decode_aif(aif_image(record))
    assert decoded["programming_date"] == expected
    assert decoded["aif_records"][0]["raw"] == record.hex()


@pytest.mark.parametrize("packed", [b"\xE9\x00", b"\xFA\x00", b"\x7B\x7F"])
def test_vin_edit_preserves_date_bytes_even_when_ambiguous_or_unavailable(packed):
    record = bytearray(aif_record())
    record[14:16] = packed
    history = aif_image(record)
    plan = plan_vin(history, NEW_VIN)
    assert plan.original_history == history
    assert plan.target_history[:AIF_RECORD_SIZE] == record
    assert plan.record[14:] == record[14:]


@pytest.mark.parametrize("first", [b"\xFF\xFF", b"\xFF\x2C"])
def test_uncommitted_and_interrupted_slots_are_retained_but_not_selected(first):
    dirty = bytearray(aif_record(NEW_VIN))
    dirty[:2] = first
    history = aif_image(aif_record(), dirty)
    result = decode_aif(history)
    assert result["programming_count"] == 1
    assert result["vin"] == result["native_vin"] == OLD_VIN
    assert len(result["aif_records"]) == 2
    assert result["aif_records"][1]["raw"] == dirty.hex()
    assert "unfinished" in result["aif_warnings"][0]
    planned = plan_vin(history, NEW_VIN)
    assert planned.mode == "append" and planned.needs_sector_rewrite
    assert planned.record_index == 1
    assert planned.target_history[:46] == history[:46]
    assert current_vin(planned.target_history) == NEW_VIN


def test_damaged_occupied_record_is_not_discarded_as_free():
    damaged = bytearray(aif_record(NEW_VIN))
    damaged[:2] = b"\xFE\xFF"
    result = decode_aif(aif_image(aif_record(), damaged))
    assert result["programming_count"] == 2
    assert result["aif_records"][1]["raw"] == damaged.hex()
    assert result["vin"] is None and result["native_vin"] is None
    assert "invalid packed VIN" in result["aif_warnings"][0]


def test_dirty_slot_before_committed_record_is_visible_without_hiding_newest():
    dirty = bytearray(aif_record())
    dirty[:2] = b"\xFF\xFF"
    result = decode_aif(aif_image(dirty, aif_record(NEW_VIN)))
    assert result["vin"] == NEW_VIN and result["programming_count"] == 1
    assert result["aif_slot_states"][:2] == ["uncommitted", "occupied"]
    assert result["aif_warnings"]


@pytest.mark.parametrize("history", [None, 644, b"", bytes(643), bytes(645),
                                      b"\xFF" * 46 + aif_image(aif_record())[:-46]])
def test_malformed_history_is_rejected_before_a_plan(history):
    with pytest.raises(ValueError):
        decode_aif(history)
    with pytest.raises(ValueError):
        plan_vin(history, NEW_VIN)


def test_append_preserves_every_old_record_and_effective_non_vin_field():
    first = bytearray(aif_record())
    last = bytearray(aif_record(zb=7560408))
    last[16] = last[22] = last[26] = last[40] = last[43] = last[45] = 0xFF
    last[31:35] = b"\x12\x34\x56\x78"
    history = aif_image(first, last)
    before = decode_aif(history)
    plan = plan_vin(history, NEW_VIN)
    assert plan.mode == "append" and not plan.needs_sector_rewrite
    assert plan.record_index == 2 and plan.vin == NEW_VIN
    assert plan.original_history == history
    assert plan.target_history[:92] == history[:92]
    assert plan.target_history[138:] == history[138:]
    assert plan.record[31:35] == last[31:35]
    after = decode_aif(plan.target_history)
    for name in ("programming_date", "recorded_software_number", "recorded_change_index",
                 "recorded_authority_number", "recorded_zb_zusb", "programmer",
                 "workshop_code", "recorded_distance_km"):
        assert after[name] == before[name]
    assert after["vin"] == NEW_VIN and after["native_vin"] == NEW_VIN


def test_empty_history_adds_only_vin_and_validity_without_invented_metadata():
    plan = plan_vin(aif_image(), NEW_VIN)
    assert plan.record_index == 0 and plan.mode == "append"
    assert plan.record[:13] == encode_vin(NEW_VIN) and plan.record[13] == 0
    assert plan.record[14:] == b"\xFF" * 32
    assert not plan.needs_sector_rewrite


def test_full_history_replaces_current_vin_only_without_rotating_or_losing_fields():
    records = [aif_record(zb=7560406 + index) for index in range(AIF_RECORD_COUNT)]
    history = aif_image(*records)
    plan = plan_vin(history, NEW_VIN)
    assert plan.record_index == 13 and plan.mode == "replace"
    assert plan.needs_sector_rewrite
    offset = 13 * AIF_RECORD_SIZE
    assert plan.target_history[:offset] == history[:offset]
    assert plan.target_history[offset + 14:] == history[offset + 14:]
    assert plan.record[14:] == records[-1][14:]
    assert current_vin(plan.target_history) == NEW_VIN


def test_matching_current_vin_does_not_consume_a_record():
    history = aif_image(aif_record(), aif_record(NEW_VIN))
    plan = plan_vin(history, NEW_VIN.lower())
    assert plan.mode == "unchanged" and not plan.needs_sector_rewrite
    assert plan.target_history == plan.original_history == history
    assert plan.record_index == 1


def test_matching_older_valid_vin_does_not_ignore_newer_native_vin():
    last = bytearray(aif_record(NEW_VIN))
    last[13] = 1
    plan = plan_vin(aif_image(aif_record(), last), OLD_VIN)
    assert plan.mode == "append"
    assert decode_aif(plan.target_history)["native_vin"] == OLD_VIN


@pytest.mark.parametrize("family,address", [(family, address)
                                            for family, address in AIF_ADDRESSES.items()]
                                           + [("MS43", 0xF800)])
def test_protocol_identity_reads_latest_history_once_through_published_pointer(family, address):
    history = aif_image(aif_record(), aif_record(NEW_VIN))
    directory = bytearray(63)
    directory[60:63] = address.to_bytes(3, "big")
    def respond(command, args):
        if command == 0x0D:
            return reply(directory)
        assert command == 6
        start = int.from_bytes(args[:4], "big") - address
        return reply(history[start:start + args[4]])
    link = Link(respond)
    ecu = MS42MS43ECU(link, family, raw_ident=b"known")
    ecu._identity_fields = lambda: {"family": family, "hardware_id": "test", "software_id": "test"}
    result = ecu.identify()
    assert result["vin"] == result["native_vin"] == NEW_VIN
    assert result["aif_raw"] == history and result["aif_address"] == address
    assert [call[1] for call in link.calls] == [0x0D] + [6] * 6
    assert sum(call[2][-1] for call in link.calls[1:]) == AIF_SIZE


@pytest.mark.parametrize("family,maximum", [("MS42", 0x1000000), ("MS43", 0xF0000)])
def test_directory_bounds_checked_before_read(family, maximum):
    directory = bytearray(67)
    directory[63:66] = (maximum - AIF_SIZE + 1).to_bytes(3, "big")
    ecu = SimpleNamespace(family=family, read_memory=lambda *_: pytest.fail("out-of-range read"))
    with pytest.raises(ValueError, match="outside"):
        read_aif(ecu, directory)


def test_short_directory_and_short_aif_do_not_become_empty_history():
    ecu = SimpleNamespace(family="MS43", read_memory=lambda *_: b"\xFF" * 13)
    with pytest.raises(ValueError, match="directory"):
        read_aif(ecu, bytes(66))
    with pytest.raises(ValueError, match="644"):
        read_aif(ecu, bytes(67))
