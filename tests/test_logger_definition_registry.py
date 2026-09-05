import json

import pytest

import logger_definition_registry as registry_module
from logger_definition import LoggerDefinitionError
from logger_definition_registry import (
    LoggerDefinitionRegistry,
    LoggerDefinitionRegistryError,
)


def test_logger_definition_registry_imports_one_file_and_resets(tmp_path, monkeypatch):
    bundled = tmp_path / "Bundled.xml"
    bundled.write_text("<logger />", encoding="utf-8")
    selected = tmp_path / "Selected.xml"
    selected.write_text("<logger id='selected' />", encoding="utf-8")
    monkeypatch.setattr(registry_module, "parse_logger_definition", lambda _path: object())
    registry = LoggerDefinitionRegistry(tmp_path / "data", bundled)

    assert registry.status().bundled is True
    imported = registry.import_file(selected)
    assert imported.bundled is False
    assert imported.name == "Selected.xml"
    assert imported.path.read_bytes() == selected.read_bytes()

    reset = registry.reset_to_bundled()
    assert reset.bundled is True
    assert reset.path == bundled
    assert not list((tmp_path / "data").glob("*.xml"))


def test_logger_definition_registry_keeps_previous_selection_when_validation_fails(
    tmp_path, monkeypatch,
):
    bundled = tmp_path / "Bundled.xml"
    bundled.write_text("<logger />", encoding="utf-8")
    first = tmp_path / "First.xml"
    first.write_text("<logger id='first' />", encoding="utf-8")
    rejected = tmp_path / "Rejected.xml"
    rejected.write_text("not xml", encoding="utf-8")
    registry = LoggerDefinitionRegistry(tmp_path / "data", bundled)
    monkeypatch.setattr(registry_module, "parse_logger_definition", lambda _path: object())
    registry.import_file(first)
    monkeypatch.setattr(
        registry_module,
        "parse_logger_definition",
        lambda _path: (_ for _ in ()).throw(LoggerDefinitionError("bad XML")),
    )

    with pytest.raises(LoggerDefinitionRegistryError, match="bad XML"):
        registry.import_file(rejected)

    assert registry.status().name == "First.xml"
    assert registry.active_path().read_bytes() == first.read_bytes()


@pytest.mark.parametrize("payload", [None, [], "Selected.xml", 1, True])
def test_logger_definition_registry_ignores_non_object_settings(tmp_path, payload):
    bundled = tmp_path / "Bundled.xml"
    bundled.write_text("<logger />", encoding="utf-8")
    registry = LoggerDefinitionRegistry(tmp_path / "data", bundled)
    registry.directory.mkdir()
    settings = json.dumps(payload)
    registry.settings_path.write_text(settings, encoding="utf-8")

    assert registry.active_path() == bundled
    assert registry.status().bundled is True
    assert registry.settings_path.read_text(encoding="utf-8") == settings
