"""Persistent user-managed calibration-definition registry."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import tempfile

from app_paths import user_data_path
import romraider_defs


class DefinitionRegistryError(RuntimeError):
    """Base error for definition registry operations."""


class DefinitionConflictError(DefinitionRegistryError):
    """Raised when an imported filename already contains different data."""

    def __init__(self, destination: Path):
        self.destination = destination
        super().__init__(f"A different definition named '{destination.name}' is registered.")


@dataclass(frozen=True)
class ImportResult:
    path: Path
    copied: bool
    identical: bool


class DefinitionRegistry:
    """Own registered XML files and remember the selected filename."""

    SETTINGS_FILENAME = "registry.json"

    def __init__(self, directory: Path | None = None):
        self.directory = Path(directory) if directory is not None else user_data_path("definitions")
        self.settings_path = self.directory / self.SETTINGS_FILENAME

    def names(self) -> list[str]:
        if not self.directory.is_dir():
            return []
        return sorted(
            (path.name for path in self.directory.iterdir()
             if path.is_file() and path.suffix.lower() == ".xml"),
            key=str.casefold,
        )

    def active_name(self) -> str | None:
        try:
            payload = json.loads(self.settings_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return None
        name = payload.get("active_definition")
        if not isinstance(name, str):
            return None
        return self._registered_name(name)

    def active_path(self) -> Path | None:
        name = self.active_name()
        return self.directory / name if name else None

    def set_active(self, name: str | None) -> None:
        registered = None if name is None else self._registered_name(name)
        if name is not None and registered is None:
            raise DefinitionRegistryError(f"Definition '{name}' is not registered.")
        self.directory.mkdir(parents=True, exist_ok=True)
        self._write_json({"active_definition": registered})

    def import_file(self, source: Path | str, *, replace: bool = False) -> ImportResult:
        source_path = Path(source)
        if not source_path.is_file():
            raise DefinitionRegistryError("The selected definition file does not exist.")
        if source_path.suffix.lower() != ".xml":
            raise DefinitionRegistryError("Definition files must use the .xml extension.")

        try:
            romraider_defs.load_definitions(source_path)
        except romraider_defs.DefinitionError as exc:
            raise DefinitionRegistryError(f"Invalid calibration definition: {exc}") from exc

        self.directory.mkdir(parents=True, exist_ok=True)
        existing_name = self._registered_name(source_path.name)
        destination = self.directory / (existing_name or source_path.name)

        if destination.is_file():
            try:
                identical = source_path.read_bytes() == destination.read_bytes()
            except OSError as exc:
                raise DefinitionRegistryError(f"Could not compare definition files: {exc}") from exc
            if identical:
                return ImportResult(destination, copied=False, identical=True)
            if not replace:
                raise DefinitionConflictError(destination)

        try:
            fd, temporary_name = tempfile.mkstemp(
                prefix=f".{destination.stem}-", suffix=".tmp", dir=self.directory
            )
            os.close(fd)
            temporary = Path(temporary_name)
            try:
                shutil.copy2(source_path, temporary)
                os.replace(temporary, destination)
            finally:
                temporary.unlink(missing_ok=True)
        except OSError as exc:
            raise DefinitionRegistryError(f"Could not register the definition: {exc}") from exc
        return ImportResult(destination, copied=True, identical=False)

    def delete(self, name: str) -> None:
        registered = self._registered_name(name)
        if registered is None:
            raise DefinitionRegistryError(f"Definition '{name}' is not registered.")
        was_active = self.active_name() == registered
        path = self.directory / registered
        try:
            path.unlink()
        except OSError as exc:
            raise DefinitionRegistryError(f"Could not delete the definition: {exc}") from exc
        if was_active:
            self.set_active(None)

    def _registered_name(self, name: str) -> str | None:
        if not name or Path(name).name != name:
            return None
        wanted = name.casefold()
        return next((item for item in self.names() if item.casefold() == wanted), None)

    def _write_json(self, payload: dict) -> None:
        temporary = self.settings_path.with_suffix(".tmp")
        try:
            temporary.write_text(
                json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            os.replace(temporary, self.settings_path)
        except OSError as exc:
            temporary.unlink(missing_ok=True)
            raise DefinitionRegistryError(f"Could not save definition selection: {exc}") from exc
