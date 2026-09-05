"""Persistent one-file logger-definition selection."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import tempfile

from app_paths import user_data_path
from logger_definition import (
    BUNDLED_LOGGER_DEFINITION_NAME,
    LoggerDefinitionError,
    bundled_logger_definition_path,
    parse_logger_definition,
)


MAX_LOGGER_DEFINITION_BYTES = 16 * 1024 * 1024


class LoggerDefinitionRegistryError(RuntimeError):
    """Raised when the selected logger definition cannot be stored or loaded."""


@dataclass(frozen=True)
class LoggerDefinitionStatus:
    name: str
    bundled: bool
    path: Path


class LoggerDefinitionRegistry:
    """Own one optional external XML, otherwise use the bundled definition."""

    SETTINGS_FILENAME = "registry.json"

    def __init__(self, directory: Path | None = None, bundled_path: Path | None = None):
        self.directory = (
            Path(directory) if directory is not None
            else user_data_path("logger-definitions")
        )
        self.settings_path = self.directory / self.SETTINGS_FILENAME
        self.bundled_path = (
            Path(bundled_path) if bundled_path is not None
            else bundled_logger_definition_path()
        )
        self.bundled_name = (
            self.bundled_path.name if bundled_path is not None
            else BUNDLED_LOGGER_DEFINITION_NAME
        )

    def active_path(self) -> Path:
        external = self._external_path()
        if external is not None:
            return external
        if not self.bundled_path.is_file():
            raise LoggerDefinitionRegistryError(
                "The bundled logger definition is missing."
            )
        return self.bundled_path

    def status(self) -> LoggerDefinitionStatus:
        path = self.active_path()
        external = self._external_path()
        return LoggerDefinitionStatus(
            name=path.name if external is not None else self.bundled_name,
            bundled=external is None,
            path=path,
        )

    def import_file(self, source: Path | str) -> LoggerDefinitionStatus:
        source_path = Path(source)
        self._validate_filename(source_path.name)
        try:
            size = source_path.stat().st_size
        except OSError as exc:
            raise LoggerDefinitionRegistryError(
                "The selected logger definition does not exist."
            ) from exc
        if size < 1 or size > MAX_LOGGER_DEFINITION_BYTES:
            raise LoggerDefinitionRegistryError(
                "Logger definitions must be between 1 byte and 16 MiB."
            )

        self.directory.mkdir(parents=True, exist_ok=True)
        destination = self.directory / source_path.name
        temporary: Path | None = None
        try:
            fd, temporary_name = tempfile.mkstemp(
                prefix=".logger-definition-", suffix=".xml", dir=self.directory,
            )
            os.close(fd)
            temporary = Path(temporary_name)
            shutil.copyfile(source_path, temporary)
            try:
                parse_logger_definition(temporary)
            except LoggerDefinitionError as exc:
                raise LoggerDefinitionRegistryError(
                    f"Invalid logger definition: {exc}"
                ) from exc
            os.replace(temporary, destination)
            temporary = None
            self._write_selection(destination.name)
            for path in self.directory.iterdir():
                if (
                    path.is_file()
                    and path.suffix.lower() == ".xml"
                    and path != destination
                ):
                    path.unlink(missing_ok=True)
        except LoggerDefinitionRegistryError:
            raise
        except OSError as exc:
            raise LoggerDefinitionRegistryError(
                f"Could not save the logger definition: {exc}"
            ) from exc
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return self.status()

    def reset_to_bundled(self) -> LoggerDefinitionStatus:
        if not self.bundled_path.is_file():
            raise LoggerDefinitionRegistryError(
                "The bundled logger definition is missing."
            )
        self.directory.mkdir(parents=True, exist_ok=True)
        self._write_selection(None)
        try:
            for path in self.directory.iterdir():
                if path.is_file() and path.suffix.lower() == ".xml":
                    path.unlink(missing_ok=True)
        except OSError as exc:
            raise LoggerDefinitionRegistryError(
                f"Could not remove the imported logger definition: {exc}"
            ) from exc
        return self.status()

    @staticmethod
    def _validate_filename(name: str) -> None:
        if (
            not name
            or len(name) > 255
            or "/" in name
            or "\\" in name
            or Path(name).name != name
            or not name.lower().endswith(".xml")
        ):
            raise LoggerDefinitionRegistryError(
                "Logger definitions must use a plain .xml filename."
            )

    def _external_path(self) -> Path | None:
        try:
            payload = json.loads(self.settings_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return None
        if not isinstance(payload, dict):
            return None
        name = payload.get("active")
        try:
            self._validate_filename(name)
        except (LoggerDefinitionRegistryError, TypeError):
            return None
        path = self.directory / name
        return path if path.is_file() else None

    def _write_selection(self, name: str | None) -> None:
        temporary = self.settings_path.with_suffix(".tmp")
        try:
            temporary.write_text(
                json.dumps({"active": name}, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            os.replace(temporary, self.settings_path)
        except OSError as exc:
            temporary.unlink(missing_ok=True)
            raise LoggerDefinitionRegistryError(
                f"Could not save the logger definition selection: {exc}"
            ) from exc
