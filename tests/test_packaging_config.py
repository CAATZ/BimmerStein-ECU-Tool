import hashlib
import importlib.util
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]


def test_default_windows_build_uses_the_supported_compiler():
    text = (ROOT / "build_windows.ps1").read_text(encoding="utf-8")
    assert 'build_windows_nuitka.ps1" @args' in text
    assert not (ROOT / "packaging" / "BimmerSteinECUTool.spec").exists()
    requirements = (ROOT / "requirements-build.txt").read_text(encoding="utf-8")
    assert "pyinstaller" not in requirements.lower()


def _offline_checks():
    spec = importlib.util.spec_from_file_location(
        "offline_checks", ROOT / "packaging/check_offline.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_development_checks_keep_full_qualification_explicit():
    checks = _offline_checks()
    extra = "tests/test_ms42_ms43_storage_edit.py"
    args = checks.pytest_arguments(False, [extra, checks.DEV_TESTS[0]], False)
    assert args.count(checks.DEV_TESTS[0]) == 1
    assert extra in args
    assert all((ROOT / path.split("::")[0]).is_file() for path in checks.DEV_TESTS)
    assert checks.ADMISSION_TEST not in args
    assert not any("emulator_gate" in path for path in args)
    assert "--deselect" not in checks.pytest_arguments(True, [], False)
    assert checks.pytest_arguments(True, [], True)[-2:] == [
        "--deselect", checks.ADMISSION_TEST]
    release = (ROOT / "packaging/prepare_release.ps1").read_text(encoding="utf-8")
    assert release.index('"packaging\\check_offline.py" --full --skip-admission') < release.index(
        "foreach ($architecture")
    invocation = next(line for line in release.splitlines() if "& $nuitkaBuildScript" in line)
    assert "-SkipTests " in invocation and "-SkipTests:$SkipTests" not in invocation


@pytest.mark.parametrize("changed", [None, "fingerprint", "groups", "source", "result", "xml", "hash"])
def test_native_admission_reuse_requires_current_completed_proof(tmp_path, monkeypatch, changed):
    import json
    from types import SimpleNamespace
    from engines import patcher

    checks = _offline_checks()
    evidence = tmp_path / "_private/validation/ms41-admission"
    evidence.mkdir(parents=True)
    test = tmp_path / "tests/test_ms412_emulator_gate.py"
    gate = tmp_path / "engines/patcher/verify_ms412_emulator.py"
    for path in (test, gate):
        path.parent.mkdir(parents=True)
        path.write_bytes(b"exact source")
    result = evidence / "result.xml"
    result.write_text('<testsuite><testcase classname="tests.test_ms412_emulator_gate" '
                      'name="test_latest_ms412_patch_set_passes_canonical_emulator_gate">'
                      + ('<failure />' if changed == "xml" else '')
                      + '</testcase></testsuite>', encoding="utf-8")
    proof = {
        "result": "FAIL" if changed == "result" else "PASS", "source_drift": [],
        "junit": "result.xml", "junit_sha256": hashlib.sha256(result.read_bytes()).hexdigest(),
        "gate_source_sha256": hashlib.sha256(gate.read_bytes()).hexdigest(),
        "test_source_sha256": hashlib.sha256(test.read_bytes()).hexdigest(),
        "groups": ["all"] if changed != "groups" else [],
        "admission_fingerprint": "a" * 64,
    }
    (evidence / "proof.json").write_text(json.dumps(proof), encoding="utf-8")
    monkeypatch.setattr(patcher, "verify_ms412_emulator", SimpleNamespace(
        GROUPS={"all": None}, _admission_fingerprint=lambda: ("b" if changed == "fingerprint" else "a") * 64),
        raising=False)
    monkeypatch.setattr(sys, "path", sys.path.copy())
    if changed == "source":
        test.write_bytes(b"changed test")
    if changed == "hash":
        result.write_bytes(b"different result")
    assert ("reusing a complete PASS" in checks.admission_status(tmp_path)) == (changed is None)


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


def test_distribution_verifier_rejects_wrong_native_architecture(tmp_path):
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
    image[68:70] = (0x014C if module.HOST_ARCHITECTURE == "x64" else 0x8664).to_bytes(2, "little")
    executable.write_bytes(image)

    with pytest.raises(RuntimeError, match=f"not {module.HOST_ARCHITECTURE}"):
        module.verify_distribution(app_dir)


def test_distribution_verifier_requires_exact_patch_tree(tmp_path):
    path = ROOT / "packaging" / "verify_dist.py"
    spec = importlib.util.spec_from_file_location("bimmerstein_verify_patches", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    source = ROOT / "engines" / "patcher" / "patches"
    packaged = tmp_path / "patches"
    shutil.copytree(source, packaged)

    assert module._verify_patch_tree(packaged) == len(list(source.glob("*.json")))
    target = next(packaged.glob("*.json"))
    original = target.read_bytes()
    target.unlink()
    with pytest.raises(RuntimeError, match="inventory differs"):
        module._verify_patch_tree(packaged)
    target.write_bytes(original + b"\n")
    with pytest.raises(RuntimeError, match="does not match tracked source"):
        module._verify_patch_tree(packaged)


def test_distribution_verifier_checks_document_version_and_public_terms(tmp_path):
    path = ROOT / "packaging" / "verify_dist.py"
    spec = importlib.util.spec_from_file_location("bimmerstein_verify_public", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    document = tmp_path / "README.md"
    document.write_text("BimmerStein 0.1.0b14", encoding="utf-8")

    module._verify_document_version(document, "0.1.0b14")
    with pytest.raises(RuntimeError, match="does not identify"):
        module._verify_document_version(document, "0.1.0b13")
    for term in module.PROHIBITED_PUBLIC_TERMS:
        document.write_text(f"Close {term.upper()} before use", encoding="utf-8")
        with pytest.raises(RuntimeError, match="prohibited public reference"):
            module._verify_public_terms((document,))
    document.write_text("Generated by " + "".join(("A", "I")), encoding="utf-8")
    with pytest.raises(RuntimeError, match="prohibited public reference"):
        module._verify_public_terms((document,))
    document.write_text("AIF and Path.joinpath remain ordinary code", encoding="utf-8")
    module._verify_public_terms((document,))
    family_guide = tmp_path / "MS42_MS43.md"
    family_guide.write_text(module.PROHIBITED_PUBLIC_TERMS[-1], encoding="utf-8")
    with pytest.raises(RuntimeError, match="prohibited public reference"):
        module._verify_public_documents(tmp_path)
    assert module._numeric_release_version("0.1.0b14") == (0, 1, 0, 14)


def test_distribution_verifier_rejects_runtime_data_from_release_package():
    text = (ROOT / "packaging" / "verify_dist.py").read_text(encoding="utf-8")

    assert 'for forbidden in ("_private", "android", "tests", "docs", "defs", "definitions")' in text
    assert 'for forbidden in ("backups", "logs")' in text
    assert "if (content / forbidden).exists() or (app_dir / forbidden).exists():" in text
    assert 'app_dir / "BimmerStein-ECU-Tool-User-Manual.pdf"' in text
    assert "source-only README links leaked" in text
    assert "MSVC_RUNTIME_FILES" in text
    assert 'content / "usb1" / "libusb-1.0.dll"' in text
    assert "does not match its unmodified build dependency" in text
    assert 'content / "engines" / "softbsl" / "eeprom_agent.hex"' in text
    assert "vc_runtime_files_unmodified" in text
    assert "bundled patch definition does not match tracked source" in text
    assert "ET.parse(patch_definition)" in text
    assert "bundled logger definition does not match tracked source" in text
    assert "ET.parse(logger_definition)" in text


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
    assert "filevers=(0, 1, 0, 1)" in beta_rendered
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
    assert '"BimmerStein-ECU-Tool-$Version-Windows-$architecture"' in text
    assert 'developer = "CAATZ"' in text
    assert 'repository = "https://github.com/CAATZ/BimmerStein-ECU-Tool"' in text
    assert "version = $Version" in text
    assert 'build_backend = "nuitka"' in text
    assert "built_at_utc = $buildStartedAtUtc" in text
    assert "source_commit = $sourceCommit" in text
    assert "source_dirty = $sourceDirty" in text
    assert "git -C $root rev-parse HEAD" in text
    assert "git -C $root status --porcelain --untracked-files=normal" in text
    assert "experimental =" not in text
    assert 'vc_runtime_deployment = "application-local"' in text
    assert "vc_runtime_files_unmodified = $true" in text
    assert "vc_runtime_files = $verification.msvc_runtime" in text
    assert "build_installer.ps1" in text
    assert "SkipInstaller" in text
    assert "IsccPath" in text
    assert "IncludeNuitka" not in text
    assert "PythonX86Path" in text
    assert "build_windows_nuitka.ps1" in text
    admission = '"engines\\patcher\\verify_ms412_emulator.py"'
    assert admission in text
    assert text.index(admission) < text.index("& $nuitkaBuildScript")

    build_text = (ROOT / "build_windows_nuitka.ps1").read_text(encoding="utf-8")
    assert "b[1-9]\\d*" in build_text

    building = (ROOT / "BUILDING.md").read_text(encoding="utf-8")
    assert "-Version 0.1.0b19" in building
    assert "v0.1.0b19" in building
    assert "Nuitka" in building and "x86" in building


def test_inno_installer_uses_bimmerstein_identity_and_per_user_install():
    installer = (ROOT / "packaging" / "BimmerSteinECUTool.iss").read_text(
        encoding="utf-8"
    )
    assert "AppName={#SetupAppName}" in installer
    assert '#define SetupAppName "BimmerStein ECU Tool"' in installer
    assert '#define SetupInstallDirName "BimmerStein ECU Tool"' in installer
    assert "SetupShortcutSuffix" not in installer
    assert "ArchitecturesAllowed=x86compatible" in installer
    assert 'Name: "{app}\\plugins\\__pycache__"' in installer
    assert '#define AppVersion "0.1.0b19"' in installer
    assert '#define AppDisplayVersion "0.1.0 Beta 19"' in installer
    assert '#define AppNumericVersion "0.1.0.19"' in installer
    assert r'#define SourceDir "..\release\BimmerStein-ECU-Tool-0.1.0b19-Windows-x64"' in installer
    assert 'SetupAppName "BimmerStein ECU Tool (' not in installer
    assert "AppPublisher=CAATZ" in installer
    assert "PrivilegesRequired=lowest" in installer
    assert "ArchitecturesAllowed=x64compatible" in installer
    assert "ArchitecturesInstallIn64BitMode=x64compatible" in installer
    assert "DefaultDirName={localappdata}\\Programs\\{#SetupInstallDirName}" in installer
    assert "BimmerStein-ECU-Tool-{#AppVersion}-Windows-{#Architecture}-Setup" in installer
    assert "SetupIconFile=..\\assets\\bimmerstein_ecu_tool.ico" in installer
    assert "LicenseFile={#SourceDir}\\LICENSE.txt" in installer
    assert "InfoBeforeFile={#SourceDir}\\RELEASE_NOTES.md" in installer
    assert 'Name: "desktopicon"' in installer
    assert 'Filename: "{app}\\BimmerStein ECU Tool.exe"' in installer
    assert "DefaultGroupName={#SetupAppName}" in installer
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
    assert "-WindowStyle Hidden -PassThru" in builder
    assert "WaitForExit" in builder
    assert "compilerProcess.ExitCode" in builder
    assert '[ValidateSet("x64", "x86")]' in builder
    assert "PackageSuffix" not in builder
    assert '"packaging\\verify_dist.py" --backend nuitka --architecture $Architecture --expected-version $Version' in builder


@pytest.mark.skipif(sys.platform != "win32", reason="Windows release script")
def test_installer_rejects_release_prefix_sibling():
    result = subprocess.run(
        [
            "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
            str(ROOT / "packaging" / "build_installer.ps1"),
            "-Version", "0.1.0b14",
            "-SourceDir", str(ROOT / "release-sibling"),
        ],
        cwd=ROOT,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "inside the repository release directory" in (result.stdout + result.stderr)


def test_nuitka_build_is_explicit_and_separate():
    build = (ROOT / "build_windows_nuitka.ps1").read_text(encoding="utf-8")
    assert '"--mode=standalone"' in build
    assert '"--msvc=latest"' in build
    assert '"--enable-plugin=pyqt5"' in build
    assert '"--include-windows-runtime-dlls=yes"' in build
    assert "Path(usb1.__file__).with_name('libusb-1.0.dll')" in build
    assert "Copy-Item -LiteralPath $usb1Dll -Destination $usb1Target" in build
    assert '"--backend", "nuitka"' not in build  # PowerShell invokes these as separate tokens.
    assert '"packaging\\verify_dist.py" --backend nuitka --expected-version $Version --architecture $Architecture' in build
    assert "dist\\$Architecture\\BimmerStein ECU Tool" in build
    assert '"--file-description=BimmerStein ECU Tool"' in build
    assert '"packaging\\check_offline.py"' in build
    assert "BimmerStein MS41 Patch Definitions.xml" in build
    assert "BimmerStein MS41 Logger Definitions.xml" in build

    entry = (ROOT / "packaging" / "nuitka_entry.py").read_text(encoding="utf-8")
    assert "sys.frozen = True" in entry
    assert "MS41FlashGUI" in entry
    assert "window.show_fitted()" in entry

    requirements = (ROOT / "requirements-build.txt").read_text(encoding="utf-8")
    assert "nuitka==4.1.3" in requirements
    assert "ordered-set==4.1.0" in requirements
    assert "zstandard==0.25.0" in requirements


def test_nuitka_checks_both_ms42_ms43_payloads_even_when_tests_are_skipped():
    build = (ROOT / "build_windows_nuitka.ps1").read_text(encoding="utf-8")
    admission = build[:build.index("if (-not $SkipTests)")]
    for arguments, label in (("--verify", "MS42/MS43 RAM-agent"),
                             ("--verify --eeprom", "MS43 EEPROM-agent")):
        assert (f"& $python -m engines.ms42_ms43.build {arguments}\n"
                f'    if ($LASTEXITCODE -ne 0) {{ throw "{label} artifact verification failed." }}') in admission


def test_ch341a_usb_runtime_dependencies_are_pinned_and_licensed():
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    notices = (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")

    assert "PyUSB==1.3.1" in requirements
    assert 'libusb1==3.4.0; sys_platform == "win32"' in requirements
    assert "THIRD_PARTY_LICENSES/PyUSB-1.3.1-BSD-3-Clause.txt" in notices
    assert "THIRD_PARTY_LICENSES/libusb1-3.4.0-COPYING.LESSER.txt" in notices

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
    assert 'href="https://github.com/CAATZ/BimmerStein-ECU-Tool/releases"' in text
    assert 'href="manual/user_manual.md">user manual</a>' in text.lower()
    assert 'href="https://github.com/CAATZ/BimmerStein-ECU-Tool/issues"' in text
    assert "## Documentation and support" in text
    assert "(output/pdf/BimmerStein-ECU-Tool-User-Manual.pdf)" in text


def test_public_docs_include_product_specific_disclaimer():
    for path in (ROOT / "README.md", ROOT / "manual" / "USER_MANUAL.md"):
        text = path.read_text(encoding="utf-8")
        normalized = " ".join(text.lower().split())
        assert "disclaimer" in normalized, path
        assert '"as is' in normalized and "without warranty" in normalized, path
        assert "off-road" in normalized and "responsible" in normalized, path
        assert "rights granted" in normalized and "version 3" in normalized, path


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
        assert (
            "very early stage" in lowered or "experimental" in lowered
        ), path
        assert "unburned fuel" in lowered, path
        assert "catalytic converters" in lowered, path
        assert "BimmerStein MS41 Patch Definitions.xml" in normalized, path
        assert "beside the executable" in lowered, path


def test_manual_explains_architecture_and_simple_installation_names():
    text = (ROOT / "manual" / "USER_MANUAL.md").read_text(encoding="utf-8")
    normalized = " ".join(text.split())
    assert "x64 for 64-bit Windows or x86 for 32-bit Windows" in normalized
    assert "existing installation location" in normalized
    assert "Windows 7 SP1" in normalized
    assert "E659=0xCC" in text


@pytest.mark.skipif(sys.platform != "win32", reason="Windows release script")
@pytest.mark.parametrize("scenario, expected_error", [
    ("dirty_initial", "requires a clean Git checkout"),
    ("dirty_after_first", "requires a clean Git checkout"),
    ("changed_commit", "Git commit changed"),
    ("clean", None),
    ("archive", None),
])
def test_release_source_check_rejects_drift_and_preserves_archive_unknown(
        tmp_path, scenario, expected_error):
    if scenario != "archive":
        (tmp_path / ".git").mkdir()
    release_script = ROOT / "packaging" / "prepare_release.ps1"
    # Execute the real check with a deterministic Git command substitute. This
    # creates neither commits nor packages and never runs the expensive gate.
    command = r'''
$ErrorActionPreference = "Stop"
$root = '__ROOT__'
$sourceCommit = ""
$sourceDirty = $null
$scenario = '__SCENARIO__'
$script:mockCommit = 'a' * 40
$script:mockDirty = $scenario -eq 'dirty_initial'
function git {
    if ($scenario -eq 'archive') { throw "Archive incorrectly queried enclosing Git repository" }
    $global:LASTEXITCODE = 0
    if ($args -contains 'rev-parse') { $script:mockCommit }
    elseif ($script:mockDirty) { ' M source.py' }
}
$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    '__SCRIPT__', [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count) { throw "Invalid release script syntax" }
$definition = $ast.Find({ param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
    $node.Name -eq 'Assert-ReleaseSource'
}, $true)
Invoke-Expression $definition.Extent.Text
Assert-ReleaseSource
if ($scenario -eq 'dirty_after_first') { $script:mockDirty = $true }
if ($scenario -eq 'changed_commit') { $script:mockCommit = 'b' * 40 }
Assert-ReleaseSource
@{commit=$sourceCommit; dirty=$sourceDirty} | ConvertTo-Json -Compress
'''.replace("__ROOT__", str(tmp_path).replace("'", "''")).replace(
        "__SCRIPT__", str(release_script).replace("'", "''")).replace("__SCENARIO__", scenario)
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", command],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, check=False)
    if expected_error:
        assert result.returncode != 0
        assert expected_error in result.stdout + result.stderr
    else:
        import json
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout) == {
            "commit": "" if scenario == "archive" else "a" * 40,
            "dirty": None if scenario == "archive" else False,
        }


def test_release_source_checks_precede_gate_and_package_replacement():
    text = (ROOT / "packaging" / "prepare_release.ps1").read_text(encoding="utf-8")
    first_check = text.index("\nAssert-ReleaseSource\n")
    assert first_check < text.index('& $python "engines\\patcher\\verify_ms412_emulator.py"')
    stage = text.split("function Stage-ReleasePackage {", 1)[1].split(
        "function Build-ReleaseInstaller {", 1)[0]
    assert stage.index("Assert-ReleaseSource") < stage.index("Remove-Item")
    assert stage.index("Assert-ReleaseSource") < stage.index("source_dirty = $sourceDirty")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows release script")
@pytest.mark.parametrize("tracked_path, rejected", [
    ("android/private.txt", True), ("_private/proof.txt", True),
    ("android-notes.txt", False),
])
def test_clean_git_release_source_excludes_private_root_directories(
        tmp_path, tracked_path, rejected):
    def git(*args):
        return subprocess.run(
            ["git", "-C", str(tmp_path), *args], stdin=subprocess.DEVNULL,
            capture_output=True, text=True, check=True)

    git("init", "--quiet")
    target = tmp_path / tracked_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("synthetic release fixture\n", encoding="utf-8")
    git("add", "--", tracked_path)
    git("-c", "user.name=Release Test", "-c", "user.email=release@example.invalid",
        "-c", "commit.gpgsign=false", "commit", "--quiet", "-m", "synthetic fixture")
    assert git("status", "--porcelain").stdout == ""
    script = ROOT / "packaging" / "prepare_release.ps1"
    command = r'''
$ErrorActionPreference = "Stop"
$root = '__ROOT__'
$sourceCommit = ""
$sourceDirty = $null
$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    '__SCRIPT__', [ref]$tokens, [ref]$parseErrors)
$definition = $ast.Find({ param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
    $node.Name -eq 'Assert-ReleaseSource'
}, $true)
Invoke-Expression $definition.Extent.Text
Assert-ReleaseSource
'''.replace("__ROOT__", str(tmp_path).replace("'", "''")).replace(
        "__SCRIPT__", str(script).replace("'", "''"))
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", command],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, check=False)
    if rejected:
        assert result.returncode != 0
        assert "must not track" in result.stdout + result.stderr
    else:
        assert result.returncode == 0, result.stderr


def test_build_rewrites_source_links_for_packaged_readme():
    text = (ROOT / "build_windows_nuitka.ps1").read_text(encoding="utf-8")
    assert 'href="BimmerStein-ECU-Tool-User-Manual.pdf"' in text
    assert 'href="LICENSE.txt">License</a>' in text
    assert '(BimmerStein-ECU-Tool-User-Manual.pdf)' in text
    assert "'(LICENSE.txt)'" in text
    assert "Replace('(docs/MS42_MS43.md)', '(MS42_MS43.md)')" in text
    assert r"',\s+shared by desktop and Android'" in text
    assert 'Copy-Item -LiteralPath "docs\\MS42_MS43.md" -Destination $appDir' in text
    assert '"User manual (web-readable Markdown)"' in text
    assert '"Build and release instructions"' in text
    assert '@("Run from source", "Verify and build", "Project layout")' in text
    assert "sourceOnlyHeading" in text
    assert "--include-data-dir=$root\\THIRD_PARTY_LICENSES=THIRD_PARTY_LICENSES" in text


@pytest.mark.parametrize("separator", (" ", "\n", "\r\n"))
def test_packaged_readme_removes_wrapped_source_platform_note(tmp_path, separator):
    script = (ROOT / "build_windows_nuitka.ps1").read_text(encoding="utf-8")
    start = script.index("    $releaseReadme = Get-Content")
    end = script.index('\n    Copy-Item -LiteralPath "LICENSE"', start)
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    readme += "\nExample," + separator + "shared by desktop and Android.\n"
    (tmp_path / "README.md").write_text(readme, encoding="utf-8")
    output = tmp_path / "package"
    output.mkdir()
    command = "$appDir='" + str(output).replace("'", "''") + "'\n" + script[start:end]
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", command], cwd=tmp_path,
        stdin=subprocess.DEVNULL, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    packaged = (output / "README.md").read_text(encoding="utf-8")
    assert "Android" not in packaged
    assert "Example." in packaged
    assert "(MS42_MS43.md)" in packaged
    assert "(MS41_EEPROM.md)" in packaged
    assert "manual/USER_MANUAL.md" not in packaged
    assert "BUILDING.md" not in packaged
    assert "(BimmerStein-ECU-Tool-User-Manual.pdf)" in packaged
