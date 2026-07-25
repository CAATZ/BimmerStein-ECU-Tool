import hashlib
import importlib.util
from pathlib import Path
import shutil

import pytest


ROOT = Path(__file__).resolve().parents[1]


def test_windows_spec_uses_gui_entry_and_excludes_private_material():
    text = (ROOT / "packaging" / "BimmerSteinECUTool.spec").read_text(encoding="utf-8")
    assert 'ROOT / "gui.py"' in text
    assert 'contents_directory="_internal"' in text
    assert 'icon=str(ROOT / "assets" / "bimmerstein_ecu_tool.ico")' in text
    assert 'version=str(ROOT / "build" / "windows_version_info.txt")' in text
    assert "disable_windowed_traceback=True" in text
    assert 'ROOT / "engines" / "patcher" / "patches"' in text
    assert '"BimmerStein MS41 Patch Definitions.xml"' in text
    assert 'ROOT / "THIRD_PARTY_LICENSES"' in text
    assert '"agent.hex"' in text and '"agent_28f.hex"' in text
    assert '"stage1_payload.hex"' in text and '"stage1_manifest.json"' in text
    assert "_private" not in text
    assert "backups" not in text


def test_distribution_verifier_rejects_missing_package(tmp_path):
    path = ROOT / "packaging" / "verify_dist.py"
    spec = importlib.util.spec_from_file_location("ms41_verify_dist", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    try:
        module.verify_distribution(tmp_path / "missing")
    except RuntimeError as error:
        assert "missing or invalid Windows executable" in str(error)
    else:
        raise AssertionError("missing package passed verification")


def test_distribution_verifier_rejects_non_x64_pe(tmp_path):
    path = ROOT / "packaging" / "verify_dist.py"
    spec = importlib.util.spec_from_file_location("ms41_verify_dist_arch", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    app_dir = tmp_path / "package"
    app_dir.mkdir()
    executable = app_dir / "BimmerStein ECU Tool.exe"
    image = bytearray(128)
    image[:2] = b"MZ"
    image[0x3C:0x40] = (64).to_bytes(4, "little")
    image[64:68] = b"PE\x00\x00"
    image[68:70] = (0x014C).to_bytes(2, "little")
    executable.write_bytes(image)

    with pytest.raises(RuntimeError, match="not x64"):
        module.verify_distribution(app_dir)


def test_distribution_verifier_rejects_runtime_data_from_release_package():
    text = (ROOT / "packaging" / "verify_dist.py").read_text(encoding="utf-8")

    assert 'for forbidden in ("_private", "tests", "docs", "defs", "definitions")' in text
    assert 'for forbidden in ("backups", "logs")' in text
    assert "if (content / forbidden).exists() or (app_dir / forbidden).exists():" in text
    assert 'app_dir / "BimmerStein-ECU-Tool-User-Manual.pdf"' in text
    assert "source-only README links leaked" in text
    assert "MSVC_RUNTIME_FILES" in text
    assert "does not match its unmodified build dependency" in text
    assert "vc_runtime_files_unmodified" in text
    assert "bundled patch definition does not match tracked source" in text
    assert "ET.parse(patch_definition)" in text


def test_msvc_runtime_verifier_rejects_modified_dependency_copy(tmp_path, monkeypatch):
    path = ROOT / "packaging" / "verify_dist.py"
    spec = importlib.util.spec_from_file_location("bimmerstein_verify_msvc", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    source = tmp_path / "source" / "VCRUNTIME140.dll"
    packaged = tmp_path / "package" / "VCRUNTIME140.dll"
    source.parent.mkdir()
    packaged.parent.mkdir()
    source.write_bytes(b"upstream-runtime")
    packaged.write_bytes(source.read_bytes())
    monkeypatch.setattr(
        module,
        "_msvc_runtime_sources",
        lambda _backend, _content: {
            Path("VCRUNTIME140.dll"): (source, "test dependency")
        },
    )

    verified = module._verify_msvc_runtime(packaged.parent)
    assert verified["VCRUNTIME140.dll"]["source"] == "test dependency"

    packaged.write_bytes(b"modified-runtime")
    with pytest.raises(RuntimeError, match="unmodified build dependency"):
        module._verify_msvc_runtime(packaged.parent)


def test_tracked_license_inventory_matches_verifier():
    path = ROOT / "packaging" / "verify_dist.py"
    spec = importlib.util.spec_from_file_location("bimmerstein_verify_licenses", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    license_dir = ROOT / "THIRD_PARTY_LICENSES"
    assert set(module.REQUIRED_LICENSE_FILES) == {
        path.name for path in license_dir.iterdir() if path.is_file()
    }
    notices = (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    for filename, expected_digest in module.REQUIRED_LICENSE_FILES.items():
        payload = (license_dir / filename).read_bytes()
        assert hashlib.sha256(payload).hexdigest() == expected_digest
        assert f"THIRD_PARTY_LICENSES/{filename}" in notices


def test_license_inventory_verifier_rejects_missing_or_changed_text(tmp_path):
    path = ROOT / "packaging" / "verify_dist.py"
    spec = importlib.util.spec_from_file_location("bimmerstein_verify_license_copy", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    app_dir = tmp_path / "package"
    shutil.copytree(ROOT / "THIRD_PARTY_LICENSES", app_dir / "THIRD_PARTY_LICENSES")
    verified = module._verify_license_inventory(app_dir)
    assert set(verified) == set(module.REQUIRED_LICENSE_FILES)

    target = app_dir / "THIRD_PARTY_LICENSES" / "Python-3.14.6-LICENSE.txt"
    target.write_bytes(target.read_bytes() + b"\nmodified\n")
    with pytest.raises(RuntimeError, match="does not match tracked source"):
        module._verify_license_inventory(app_dir)

    target.unlink()
    with pytest.raises(RuntimeError, match="license text is missing"):
        module._verify_license_inventory(app_dir)


def test_windows_version_info_contains_product_identity():
    path = ROOT / "packaging" / "generate_version_info.py"
    spec = importlib.util.spec_from_file_location("bimmerstein_version_info", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    rendered = module.render_version_info("1.2.3-rc.1")
    assert "filevers=(1, 2, 3, 0)" in rendered
    assert "StringStruct('ProductName', 'BimmerStein ECU Tool')" in rendered
    assert "StringStruct('ProductVersion', '1.2.3-rc.1')" in rendered

    beta_rendered = module.render_version_info("0.1.0b1")
    assert "filevers=(0, 1, 0, 0)" in beta_rendered
    assert "StringStruct('FileVersion', '0.1.0b1')" in beta_rendered
    assert "StringStruct('ProductVersion', '0.1.0b1')" in beta_rendered

    for invalid in ("v0.1.0b1", "0.1.0b0", "0.1.0beta1"):
        with pytest.raises(ValueError, match="BimmerStein release form"):
            module.render_version_info(invalid)


def test_release_packaging_requires_explicit_license_gates():
    text = (ROOT / "packaging" / "prepare_release.ps1").read_text(encoding="utf-8")
    assert '[ValidateSet("Commercial", "GPLv3")]' in text
    assert 'project_license = "GPL-3.0-only"' in text
    assert 'intended_use = "off-road-only"' in text
    assert "DefinitionRedistributionApproved" not in text
    assert "calibration_definitions_bundled = $true" in text
    assert "verify_dist.py" in text
    assert "b[1-9]\\d*" in text
    assert '"BimmerStein-ECU-Tool-$Version-Windows-x64"' in text
    assert '"BimmerStein-ECU-Tool-$Version-Windows-x64-Nuitka"' in text
    assert "version = $Version" in text
    assert "build_backend = $Backend" in text
    assert "experimental =" not in text
    assert 'vc_runtime_deployment = "application-local"' in text
    assert "vc_runtime_files_unmodified = $true" in text
    assert "vc_runtime_files = $verification.msvc_runtime" in text
    assert "build_installer.ps1" in text
    assert "SkipInstaller" in text
    assert "IsccPath" in text
    assert "IncludeNuitka" in text
    assert "build_windows_nuitka.ps1" in text

    build_text = (ROOT / "build_windows.ps1").read_text(encoding="utf-8")
    assert "b[1-9]\\d*" in build_text

    building = (ROOT / "BUILDING.md").read_text(encoding="utf-8")
    assert "-Version 0.1.0b11" in building
    assert "v0.1.0b11" in building
    assert "BimmerStein ECU Tool Nuitka" in building


def test_inno_installer_uses_bimmerstein_identity_and_per_user_install():
    installer = (ROOT / "packaging" / "BimmerSteinECUTool.iss").read_text(
        encoding="utf-8"
    )
    assert "AppName={#SetupAppName}" in installer
    assert '#define SetupAppName "BimmerStein ECU Tool"' in installer
    assert 'SetupAppName "BimmerStein ECU Tool (' not in installer
    assert "AppPublisher=CAATZ" in installer
    assert "PrivilegesRequired=lowest" in installer
    assert "ArchitecturesAllowed=x64compatible" in installer
    assert "ArchitecturesInstallIn64BitMode=x64compatible" in installer
    assert "DefaultDirName={localappdata}\\Programs\\{#SetupInstallDirName}" in installer
    assert "BimmerStein-ECU-Tool-{#AppVersion}-Windows-x64{#PackageSuffix}-Setup" in installer
    assert "SetupIconFile=..\\assets\\bimmerstein_ecu_tool.ico" in installer
    assert "LicenseFile={#SourceDir}\\LICENSE.txt" in installer
    assert "InfoBeforeFile={#SourceDir}\\RELEASE_NOTES.md" in installer
    assert 'Name: "desktopicon"' in installer
    assert 'Filename: "{app}\\BimmerStein ECU Tool.exe"' in installer
    assert 'Name: "{group}\\{#SetupAppName}"' in installer
    assert 'Name: "{autodesktop}\\{#SetupAppName}"' in installer

    builder = (ROOT / "packaging" / "build_installer.ps1").read_text(
        encoding="utf-8"
    )
    assert "verify_dist.py" in builder
    assert "INNO_ISCC" in builder
    assert "AppDisplayVersion" in builder
    assert "AppNumericVersion" in builder
    assert "SHA256SUMS.txt" in builder
    assert "System.Diagnostics.Process" in builder
    assert "WaitForExit" in builder
    assert "compilerProcess.ExitCode" in builder
    assert '[ValidateSet("pyinstaller", "nuitka")]' in builder
    assert '"-Nuitka"' in builder
    assert '"packaging\\verify_dist.py" --backend $Backend' in builder


def test_nuitka_build_is_explicit_and_separate():
    build = (ROOT / "build_windows_nuitka.ps1").read_text(encoding="utf-8")
    assert '"--mode=standalone"' in build
    assert '"--msvc=latest"' in build
    assert '"--enable-plugin=pyqt5"' in build
    assert '"--include-windows-runtime-dlls=yes"' in build
    assert '"--backend", "nuitka"' not in build  # PowerShell invokes these as separate tokens.
    assert '"packaging\\verify_dist.py" --backend nuitka' in build
    assert "BimmerStein ECU Tool Nuitka" in build
    assert '"--file-description=BimmerStein ECU Tool"' in build
    assert "[guid]::NewGuid()" in build
    assert "BimmerStein MS41 Patch Definitions.xml" in build

    entry = (ROOT / "packaging" / "nuitka_entry.py").read_text(encoding="utf-8")
    assert "sys.frozen = True" in entry
    assert "MS41FlashGUI" in entry
    assert "window.show_fitted()" in entry

    requirements = (ROOT / "requirements-build.txt").read_text(encoding="utf-8")
    assert "nuitka==4.1.3" in requirements
    assert "ordered-set==4.1.0" in requirements
    assert "zstandard==0.25.0" in requirements

def test_public_project_license_and_docs_are_gplv3():
    license_text = (ROOT / "LICENSE").read_text(encoding="utf-8")
    assert "GNU GENERAL PUBLIC LICENSE" in license_text
    assert "Version 3, 29 June 2007" in license_text
    assert "END OF TERMS AND CONDITIONS" in license_text
    assert "MIT License" not in license_text

    public_docs = (
        ROOT / "README.md",
        ROOT / "BUILDING.md",
        ROOT / "RELEASE_NOTES.md",
        ROOT / "manual" / "USER_MANUAL.md",
    )
    for path in public_docs:
        text = path.read_text(encoding="utf-8")
        assert "GPL-3.0-only" in text, path
        assert "OFF-ROAD" in text, path


def test_readme_uses_canonical_product_logo_and_resource_links():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert '<img src="assets/bimmerstein_ecu_tool.png"' in text
    assert 'alt="BimmerStein ECU Tool"' in text
    assert 'href="https://github.com/CAATZ/BimmerStein-ECU-Tool/releases/tag/v0.1.0b11"' in text
    assert 'href="manual/USER_MANUAL.md">User Manual</a>' in text
    assert 'href="https://github.com/CAATZ/BimmerStein-ECU-Tool/issues"' in text
    assert "## Documentation and support" in text
    assert "[Patch definitions and usage]" in text


def test_public_docs_include_product_specific_disclaimer():
    for path in (ROOT / "README.md", ROOT / "manual" / "USER_MANUAL.md"):
        text = path.read_text(encoding="utf-8")
        assert "## Disclaimer" in text, path
        assert 'provided "as is," without warranty of any kind' in text, path
        assert "off-road designation does not establish" in text, path
        assert "Nothing in this disclaimer limits the rights granted" in text, path


def test_public_docs_state_patch_tuning_definition_is_bundled():
    public_docs = (
        ROOT / "README.md",
        ROOT / "RELEASE_NOTES.md",
        ROOT / "manual" / "USER_MANUAL.md",
    )
    for path in public_docs:
        text = path.read_text(encoding="utf-8")
        normalized = " ".join(text.split())
        lowered = normalized.lower()
        assert "ignition cut" in lowered and "launch control" in lowered, path
        assert "very early stage" in lowered, path
        assert "misfire" in lowered and "fuel-trim issues" in lowered, path
        assert "extremely aggressive" in lowered, path
        assert "catalytic converters" in lowered, path
        assert "BimmerStein MS41 Patch Definitions.xml" in normalized, path
        assert "beside the executable" in lowered, path


def test_manual_declares_both_packaging_backends():
    text = (ROOT / "manual" / "USER_MANUAL.md").read_text(encoding="utf-8")
    normalized = " ".join(text.split())
    assert "PyInstaller and Nuitka packages" in normalized
    assert "-Nuitka" in text
    assert "distinct product identity" in normalized
    assert "E659=0xCC" in text


def test_build_rewrites_source_links_for_packaged_readme():
    text = (ROOT / "build_windows.ps1").read_text(encoding="utf-8")
    assert 'src="_internal/assets/bimmerstein_ecu_tool.png"' in text
    assert 'href="BimmerStein-ECU-Tool-User-Manual.pdf"' in text
    assert 'href="LICENSE.txt">License</a>' in text
    assert '(BimmerStein-ECU-Tool-User-Manual.pdf)' in text
    assert "'(LICENSE.txt)'" in text
    assert '"User manual (web-readable Markdown)"' in text
    assert '"Build and release instructions"' in text
    assert '@("Run from source", "Verify and build", "Project layout")' in text
    assert "sourceOnlyHeading" in text
    assert '"_internal\\THIRD_PARTY_LICENSES"' in text
    assert "Move-Item -LiteralPath $collectedLicenses -Destination $publicLicenses" in text
