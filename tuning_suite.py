"""Discover the installed desktop editor and its explicit file-open contract."""

import json
import os
from pathlib import Path

EXECUTABLE = "BimmerStein-Tuning-Suite.exe"
CAPABILITIES = "tuning-suite-capabilities.json"
UNINSTALL_KEY = (
    r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
    r"\{C07E0C75-50B4-4EC6-88EF-895305A52E89}_is1"
)


def installation_candidates():
    """Only the product's installer locations; never an unrelated PATH command."""
    try:
        import winreg
    except ImportError:
        return
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(hive, UNINSTALL_KEY, 0, winreg.KEY_READ | view) as key:
                    for name in ("InstallLocation", "Inno Setup: App Path"):
                        try:
                            value, _kind = winreg.QueryValueEx(key, name)
                            if isinstance(value, str) and value:
                                yield Path(value) / EXECUTABLE
                        except OSError:
                            pass
            except OSError:
                pass
    for name, child in (("LOCALAPPDATA", "Programs"), ("ProgramFiles", ""),
                        ("ProgramFiles(x86)", "")):
        directory = os.environ.get(name)
        if directory:
            yield Path(directory) / child / "BimmerStein Tuning Suite" / EXECUTABLE


def installed_editor():
    for candidate in installation_candidates():
        try:
            if candidate.is_file():
                return candidate.resolve()
        except OSError:
            continue
    return None


def handoff_editor():
    """Return a launchable installation or a short user-facing unavailability reason."""
    executable = installed_editor()
    if executable is None:
        return None, "Install BimmerStein Tuning Suite to edit this image."
    try:
        capabilities = json.loads((executable.parent / CAPABILITIES).read_text(encoding="utf-8"))
        if capabilities.get("open_bin_argument") == "--open":
            return executable, "Open an editable copy in BimmerStein Tuning Suite."
    except (OSError, ValueError, AttributeError):
        pass
    return None, "Update BimmerStein Tuning Suite to a version supporting Open in Tuning Suite."
