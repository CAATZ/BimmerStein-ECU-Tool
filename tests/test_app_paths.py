from pathlib import Path
import sys

import app_paths


def test_source_mutable_path_is_anchored_to_repository_root():
    assert app_paths.mutable_path("backups") == Path(app_paths.__file__).resolve().parent / "backups"


def test_frozen_mutable_path_is_anchored_beside_executable(tmp_path, monkeypatch):
    executable = tmp_path / "BimmerStein ECU Tool.exe"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(executable))
    assert app_paths.mutable_path("logs") == tmp_path / "logs"


def test_user_data_path_uses_local_app_data(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert app_paths.user_data_path("definitions") == (
        tmp_path / "BimmerStein ECU Tool" / "definitions"
    )


def test_mobile_data_override_routes_every_mutable_path(tmp_path, monkeypatch):
    monkeypatch.setenv(app_paths.DATA_DIR_ENV, str(tmp_path))
    assert app_paths.mutable_path("journals") == tmp_path / "journals"
    assert app_paths.user_data_path("definitions") == tmp_path / "definitions"
