from pathlib import Path

import pytest

from definition_registry import (
    DefinitionConflictError,
    DefinitionRegistry,
    DefinitionRegistryError,
)
import rom_analyzer
import romraider_defs


def _definition(path: Path, marker: str = "41") -> Path:
    path.write_text(
        f"""<?xml version="1.0" encoding="UTF-8"?>
<roms>
  <rom>
    <romid>
      <xmlid>ID{marker}</xmlid>
      <internalidaddress>0xE</internalidaddress>
      <internalidstring>{marker}</internalidstring>
      <filesize>24kb</filesize>
      <submodel>Test {marker}</submodel>
      <ecuid>TEST</ecuid>
    </romid>
    <table name="Test Scalar" category="Test" storageaddress="0x20"
           sizex="1" sizey="1" storagetype="uint8">
      <scaling units="raw" expression="x" format="0" />
    </table>
  </rom>
</roms>
""",
        encoding="utf-8",
    )
    return path


def test_registry_import_select_persist_and_delete(tmp_path):
    source = _definition(tmp_path / "MS41.xml")
    registry = DefinitionRegistry(tmp_path / "registered")

    result = registry.import_file(source)
    assert result.copied is True
    assert result.identical is False
    assert result.path.read_bytes() == source.read_bytes()
    assert registry.names() == ["MS41.xml"]

    registry.set_active("MS41.xml")
    reopened = DefinitionRegistry(registry.directory)
    assert reopened.active_name() == "MS41.xml"
    assert reopened.active_path() == result.path

    reopened.delete("MS41.xml")
    assert reopened.names() == []
    assert reopened.active_path() is None


def test_registry_preserves_order_migrates_legacy_and_removes_deleted_entries(tmp_path):
    first = _definition(tmp_path / "First.xml", "41")
    second = _definition(tmp_path / "Second.xml", "60")
    registry = DefinitionRegistry(tmp_path / "registered")
    registry.import_file(first)
    registry.import_file(second)

    registry.set_active_order(["Second.xml", "First.xml"])
    reopened = DefinitionRegistry(registry.directory)
    assert reopened.active_names() == ["Second.xml", "First.xml"]
    assert reopened.active_paths() == [
        registry.directory / "Second.xml",
        registry.directory / "First.xml",
    ]

    reopened.delete("Second.xml")
    assert reopened.active_names() == ["First.xml"]

    reopened.settings_path.write_text(
        '{"active_definition": "First.xml"}\n', encoding="utf-8"
    )
    assert reopened.active_names() == ["First.xml"]

    with pytest.raises(DefinitionRegistryError, match="more than once"):
        reopened.set_active_order(["First.xml", "first.XML"])


def test_identical_import_reuses_registered_copy(tmp_path):
    source = _definition(tmp_path / "MS41.xml")
    registry = DefinitionRegistry(tmp_path / "registered")
    registry.import_file(source)

    again = registry.import_file(source)
    assert again.copied is False
    assert again.identical is True


def test_different_same_name_requires_explicit_replace(tmp_path):
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    first_dir.mkdir()
    second_dir.mkdir()
    first = _definition(first_dir / "MS41.xml", "41")
    second = _definition(second_dir / "MS41.xml", "60")
    registry = DefinitionRegistry(tmp_path / "registered")
    registry.import_file(first)

    with pytest.raises(DefinitionConflictError):
        registry.import_file(second)

    replaced = registry.import_file(second, replace=True)
    assert replaced.copied is True
    assert replaced.path.read_bytes() == second.read_bytes()


def test_invalid_xml_and_unregistered_selection_are_rejected(tmp_path):
    invalid = tmp_path / "invalid.xml"
    invalid.write_text("<not-roms />", encoding="utf-8")
    registry = DefinitionRegistry(tmp_path / "registered")

    with pytest.raises(DefinitionRegistryError, match="root element"):
        registry.import_file(invalid)
    with pytest.raises(DefinitionRegistryError, match="not registered"):
        registry.set_active("..\\outside.xml")


def test_loader_cache_refreshes_when_file_changes(tmp_path):
    definition = _definition(tmp_path / "MS41.xml", "41")
    first = romraider_defs.load_definitions(definition)
    _definition(definition, "60")
    second = romraider_defs.load_definitions(definition)
    assert first is not second


def test_analyzer_uses_only_the_explicit_definition(tmp_path):
    definition = _definition(tmp_path / "MS41.xml", "41")
    tune = bytearray(b"\xFF" * rom_analyzer.TUNE_SIZE)
    tune[0xE:0x10] = b"41"
    tune[0x20] = 7

    without = rom_analyzer.analyze(tune)
    with_selected = rom_analyzer.analyze(tune, definition)

    assert without.params == []
    assert any("No calibration definition is selected" in item for item in without.warnings)
    assert ("Test", "Test Scalar", "7", "raw") in with_selected.params
    assert with_selected.matched_label == "Test 41  [ID41]"
