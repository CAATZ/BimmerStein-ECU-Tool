"""ECU Number and native ISN are read-only, independently validated identifiers."""

import hashlib
import json

import pytest

import ms42_ms43_diagnostics as diagnostics
from tests.test_ms42_ms43 import reply
from tests.test_ms42_ms43_diagnostics import ECU_NUMBER, ISN_SNAPSHOTS, identity_ecu


NATIVE_READS = {"MS42": (0xF906, 8), "MS43": (0xE2D0, 28)}


def override_memory_reply(link, address, frame):
    original = link.responder

    def respond(command, args):
        if command == 6 and int.from_bytes(args[:4], "big") == address:
            return frame
        return original(command, args)

    link.responder = respond


def snapshot_text(data):
    data.pop("sha256", None)
    data["sha256"] = hashlib.sha256(json.dumps(
        data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return json.dumps(data)


@pytest.mark.parametrize("family", ["MS42", "MS43"])
def test_native_triplet_decodes_numeric_isn_without_byte_swapping(family):
    assert diagnostics.decode_isn(ISN_SNAPSHOTS[family], family) == {"isn": "1234", "isn_status": "Stored"}


@pytest.mark.parametrize("family", ["MS42", "MS43"])
@pytest.mark.parametrize("word", [0, 1, 2])
def test_each_isn_redundancy_word_is_checked(family, word):
    raw = bytearray(ISN_SNAPSHOTS[family])
    raw[(22 if family == "MS43" else 0) + 2 * word] ^= 1
    assert diagnostics.decode_isn(raw, family) == {"isn": None, "isn_status": "Invalid ISN data"}


@pytest.mark.parametrize("family", ["MS42", "MS43"])
@pytest.mark.parametrize("stored_status,expected", [
    (b"\x55\xAA", "Invalid ISN data"),  # Native numeric AA55 means invalid, not valid 55AA.
    (b"\x00\x00", "Unavailable"),
    (b"\x34\x12", "Unavailable"),
])
def test_native_status_prevents_exposing_an_unverified_triplet(family, stored_status, expected):
    raw = bytearray(ISN_SNAPSHOTS[family])
    start = 0 if family == "MS43" else 6
    raw[start:start + 2] = stored_status
    assert diagnostics.decode_isn(raw, family) == {"isn": None, "isn_status": expected}


@pytest.mark.parametrize("family", ["MS42", "MS43"])
def test_erased_native_isn_is_not_stored(family):
    assert diagnostics.decode_isn(b"\xFF" * NATIVE_READS[family][1], family) == {
        "isn": None, "isn_status": "Not stored"}


@pytest.mark.parametrize("family", ["MS42", "MS43"])
def test_unpaired_status_does_not_expose_stale_isn_words(family):
    raw = bytearray(ISN_SNAPSHOTS[family])
    start = 0 if family == "MS43" else 6
    raw[start:start + 2] = b"\xFF\xFF"
    assert diagnostics.decode_isn(raw, family) == {"isn": None, "isn_status": "Invalid ISN data"}


def test_ms43_erased_words_with_live_descriptor_are_not_reported_empty():
    raw = bytearray(b"\xFF" * 28)
    raw[10:12] = b"\x01\x00"
    assert diagnostics.decode_isn(raw, "MS43") == {"isn": None, "isn_status": "Invalid ISN data"}


@pytest.mark.parametrize("family", ["MS42", "MS43"])
@pytest.mark.parametrize("words,expected", [
    (bytes.fromhex("FF FF 00 FF FF 00"), "0000"),
    (bytes.fromhex("00 00 FF 00 00 FF"), "FFFF"),
])
def test_boundary_isn_values_are_distinct_from_unstored_data(family, words, expected):
    raw = bytearray(ISN_SNAPSHOTS[family])
    start = 22 if family == "MS43" else 0
    raw[start:start + 6] = words
    assert diagnostics.decode_isn(raw, family) == {"isn": expected, "isn_status": "Stored"}


@pytest.mark.parametrize("family", ["MS42", "MS43"])
@pytest.mark.parametrize("delta", [-1, 1])
def test_partial_or_overlong_native_snapshot_is_rejected(family, delta):
    with pytest.raises(ValueError, match="Incomplete ISN snapshot"):
        diagnostics.decode_isn(b"\xFF" * (NATIVE_READS[family][1] + delta), family)


def test_ms41_isn_never_enters_the_ms42_ms43_decoder():
    with pytest.raises(ValueError, match="MS42 or MS43"):
        diagnostics.decode_isn(ISN_SNAPSHOTS["MS42"], "MS41.2")


@pytest.mark.parametrize("family", ["MS42", "MS43"])
def test_read_format_and_snapshot_roundtrip_keep_distinct_identifiers(family):
    ecu, link = identity_ecu(family)
    info = diagnostics.read_identity(ecu)
    assert info["ecu_number"] == ECU_NUMBER.decode() == "87654321"
    assert info["dme_production_serial"] == "12345678"
    assert info["isn"] == "1234" and info["isn_status"] == "Stored"
    assert not info["warnings"]
    memory_reads = [(int.from_bytes(args[:4], "big"), args[4])
                    for _, command, args in link.calls if command == 6]
    assert memory_reads.count((0x3F80, 8)) == 1
    assert memory_reads.count(NATIVE_READS[family]) == 1
    assert all(command in (0, 6, 0x0D, 0x0B) for _, command, _ in link.calls)
    assert not any(0x3F00 <= address < 0x3F64 for address, _ in memory_reads)
    formatted = diagnostics.format_identity(info)
    assert "ECU Number: 87654321" in formatted and "ISN: 1234" in formatted
    loaded = diagnostics.decode_snapshot(diagnostics.encode_snapshot(info), family=family)
    for key in ("ecu_number", "dme_production_serial", "isn", "isn_status", "aif_raw"):
        assert loaded[key] == info[key]
    assert "ECU Number: 87654321" in diagnostics.format_snapshot(loaded)


@pytest.mark.parametrize("family,software,address", [
    ("MS42", b"0110AD", 0x50036), ("MS43", b"430056", 0xEFFBA),
])
def test_unknown_software_never_probes_a_guessed_isn_address(family, software, address):
    ecu, link = identity_ecu(family)
    override_memory_reply(link, address, reply(software))
    info = diagnostics.read_identity(ecu)
    assert info["software_id"] == software.decode()
    assert info["isn"] is None and info["isn_status"] == "Unavailable"
    assert info["ecu_number"] == "87654321" and info["vin"] == "WBAAM31010FG12345"
    assert any("ISN" in warning for warning in info["warnings"])
    assert not any(command == 6 and int.from_bytes(args[:4], "big") in (0xF906, 0xE2D0)
                   for _, command, args in link.calls)


@pytest.mark.parametrize("family", ["MS42", "MS43"])
@pytest.mark.parametrize("field", ["ecu_number", "isn"])
def test_identifier_read_failure_keeps_other_identity_and_export_fields(family, field):
    ecu, link = identity_ecu(family)
    override_memory_reply(link, 0x3F80 if field == "ecu_number" else NATIVE_READS[family][0],
                          reply(status=0xA2))
    info = diagnostics.read_identity(ecu)
    assert info[field] is None
    assert info["vin"] == "WBAAM31010FG12345" and info["dme_production_serial"] == "12345678"
    if field == "ecu_number":
        assert info["isn"] == "1234" and info["isn_status"] == "Stored"
    else:
        assert info["ecu_number"] == "87654321" and info["isn_status"] == "Unavailable"
    assert len(info["warnings"]) == 1 and "unavailable" in info["warnings"][0]
    loaded = diagnostics.decode_snapshot(diagnostics.encode_snapshot(info))
    assert loaded[field] is None and loaded["isn_status"] == info["isn_status"]


@pytest.mark.parametrize("family", ["MS42", "MS43"])
@pytest.mark.parametrize("erased", [False, True])
def test_invalid_and_erased_isn_states_are_preserved_in_snapshots(family, erased):
    ecu, link = identity_ecu(family)
    raw = bytearray(b"\xFF" * NATIVE_READS[family][1] if erased else ISN_SNAPSHOTS[family])
    if not erased:
        raw[22 if family == "MS43" else 0] ^= 1
    override_memory_reply(link, NATIVE_READS[family][0], reply(raw))
    info = diagnostics.read_identity(ecu)
    expected = "Not stored" if erased else "Invalid ISN data"
    assert info["isn"] is None and info["isn_status"] == expected
    assert bool(info["warnings"]) is not erased
    loaded = diagnostics.decode_snapshot(diagnostics.encode_snapshot(info))
    assert loaded["isn"] is None and loaded["isn_status"] == expected
    assert expected in diagnostics.format_identity(loaded)


@pytest.mark.parametrize("raw", [b"\xFF" * 8, b"1234567", b"12\x00AB345"])
def test_malformed_ecu_number_does_not_replace_production_serial_or_isn(raw):
    ecu, link = identity_ecu("MS43")
    override_memory_reply(link, 0x3F80, reply(raw))
    info = diagnostics.read_identity(ecu)
    assert info["ecu_number"] is None
    assert info["dme_production_serial"] == "12345678" and info["isn"] == "1234"
    assert len(info["warnings"]) == 1 and "ECU Number" in info["warnings"][0]


@pytest.mark.parametrize("family", ["MS42", "MS43"])
def test_old_snapshot_without_optional_identifiers_still_loads(family):
    ecu, _ = identity_ecu(family)
    data = json.loads(diagnostics.encode_snapshot(diagnostics.read_identity(ecu)))
    for key in ("ecu_number", "isn", "isn_status"):
        data.pop(key)
    loaded = diagnostics.decode_snapshot(snapshot_text(data))
    assert loaded["ecu_number"] is loaded["isn"] is loaded["isn_status"] is None
    assert loaded["vin"] == "WBAAM31010FG12345" and loaded["dme_production_serial"] == "12345678"


@pytest.mark.parametrize("field,value", [("ecu_number", "11111111"), ("isn", "5678"),
                                       ("isn_status", "Not stored")])
def test_snapshot_checksum_covers_new_identifier_fields(field, value):
    ecu, _ = identity_ecu("MS43")
    data = json.loads(diagnostics.encode_snapshot(diagnostics.read_identity(ecu)))
    data[field] = value
    with pytest.raises(ValueError, match="checksum"):
        diagnostics.decode_snapshot(json.dumps(data))


@pytest.mark.parametrize("field,value", [("ecu_number", []), ("ecu_number", "123456789"),
                                       ("ecu_number", "Ｎ1234567"), ("isn", 1234),
                                       ("isn", "123"), ("isn", "12G4"), ("isn", "abcd"),
                                       ("isn_status", "Ready")])
def test_invalid_identifier_metadata_is_rejected_even_with_valid_snapshot_checksum(field, value):
    ecu, _ = identity_ecu("MS43")
    data = json.loads(diagnostics.encode_snapshot(diagnostics.read_identity(ecu)))
    data[field] = value
    with pytest.raises(ValueError, match="Invalid stored ECU identifiers"):
        diagnostics.decode_snapshot(snapshot_text(data))
