"""Stable mutable-data paths for source, frozen, and per-user data."""

from __future__ import annotations

import os
from pathlib import Path
import sys


APP_DATA_DIRNAME = "BimmerStein ECU Tool"
DATA_DIR_ENV = "BIMMERSTEIN_DATA_DIR"


def install_root() -> Path:
    """Directory beside the executable when frozen, or the source root otherwise."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def mutable_path(*parts: str) -> Path:
    """Return a user-created data path anchored beside the portable application."""
    override = os.environ.get(DATA_DIR_ENV)
    root = Path(override) if override else install_root()
    return root.joinpath(*parts)


def user_data_root() -> Path:
    """Return the per-user data directory used across application upgrades."""
    override = os.environ.get(DATA_DIR_ENV)
    if override:
        return Path(override)
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / APP_DATA_DIRNAME
    if sys.platform == "win32":
        return Path.home() / "AppData" / "Local" / APP_DATA_DIRNAME
    return Path.home() / ".local" / "share" / APP_DATA_DIRNAME


def user_data_path(*parts: str) -> Path:
    """Return a path below the per-user application data directory."""
    return user_data_root().joinpath(*parts)
