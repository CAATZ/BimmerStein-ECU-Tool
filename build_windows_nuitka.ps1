param(
    [switch]$SkipTests,
    [ValidatePattern('^(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)(?:b[1-9]\d*|-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$')]
    [string]$Version = "0.0.0-dev"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$root = $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Virtual environment not found. Create .venv and install requirements-build.txt first."
}

Push-Location $root
try {
    $env:BIMMERSTEIN_VERSION = $Version
    & $python -c "import nuitka, PyQt5, reportlab, usb.core, usb.backend.libusb1, usb1"
    if ($LASTEXITCODE -ne 0) {
        throw "Nuitka build dependencies are missing. Run: .venv\Scripts\python.exe -m pip install -r requirements-build.txt"
    }
    $usb1Dll = & $python -c "from pathlib import Path; import usb1; print(Path(usb1.__file__).with_name('libusb-1.0.dll'))"
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $usb1Dll -PathType Leaf)) {
        throw "The libusb1 Windows runtime DLL is missing."
    }

    & $python "packaging\generate_icon.py"
    if ($LASTEXITCODE -ne 0) { throw "Application icon generation failed." }
    & $python "packaging\generate_version_info.py" --version $Version
    if ($LASTEXITCODE -ne 0) { throw "Windows version-metadata generation failed." }

    $env:QT_QPA_PLATFORM = "offscreen"
    & $python "packaging\capture_manual_screenshots.py"
    if ($LASTEXITCODE -ne 0) { throw "Manual screenshot generation failed." }
    & $python "packaging\build_user_manual.py"
    if ($LASTEXITCODE -ne 0) { throw "User-manual build failed." }

    & $python -m engines.softbsl.verify_agent_artifacts
    if ($LASTEXITCODE -ne 0) { throw "RAM-agent artifact verification failed." }

    if (-not $SkipTests) {
        & $python -m ruff check . --select F,E9
        if ($LASTEXITCODE -ne 0) { throw "Static analysis failed." }
        $testTempRoot = Join-Path $root ".tmp"
        New-Item -ItemType Directory -Path $testTempRoot -Force | Out-Null
        $testBaseTemp = Join-Path $testTempRoot (
            "pytest-windows-nuitka-build-" + [guid]::NewGuid().ToString("N")
        )
        & $python -m pytest -q -p no:cacheprovider --basetemp $testBaseTemp
        if ($LASTEXITCODE -ne 0) { throw "Automated tests failed." }
    }

    $appDir = Join-Path $root "dist\BimmerStein ECU Tool Nuitka"
    $buildDir = Join-Path $root "build\BimmerSteinECUToolNuitka"
    foreach ($target in @($appDir, $buildDir)) {
        $fullTarget = [System.IO.Path]::GetFullPath($target)
        if (-not $fullTarget.StartsWith($root, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Refusing to clean a build path outside the repository: $fullTarget"
        }
        if (Test-Path -LiteralPath $fullTarget) {
            Remove-Item -LiteralPath $fullTarget -Recurse -Force
        }
    }

    $outputDir = Join-Path $buildDir "output"
    $cacheDir = Join-Path $root ".tmp\nuitka-cache"
    $tempDir = Join-Path $buildDir "temp"
    New-Item -ItemType Directory -Path $outputDir -Force | Out-Null
    New-Item -ItemType Directory -Path $cacheDir -Force | Out-Null
    New-Item -ItemType Directory -Path $tempDir -Force | Out-Null
    $env:NUITKA_CACHE_DIR = $cacheDir
    $env:TEMP = $tempDir
    $env:TMP = $tempDir

    $match = [regex]::Match($Version, '^(?<major>\d+)\.(?<minor>\d+)\.(?<patch>\d+)(?<beta>b\d+)?')
    if (-not $match.Success) { throw "Could not derive Nuitka version from: $Version" }
    $betaNumber = if ($match.Groups["beta"].Success) {
        [int]$match.Groups["beta"].Value.Substring(1)
    } else { 0 }
    $numericVersion = "$($match.Groups['major'].Value).$($match.Groups['minor'].Value).$($match.Groups['patch'].Value).$betaNumber"

    $nuitkaArguments = @(
        "-m", "nuitka",
        "--mode=standalone",
        "--msvc=latest",
        "--assume-yes-for-downloads",
        "--enable-plugin=pyqt5",
        "--include-module=serial.tools.list_ports_windows",
        "--windows-console-mode=disable",
        "--include-windows-runtime-dlls=yes",
        "--include-data-dir=$root\assets=assets",
        "--include-data-dir=$root\engines\patcher\patches=engines/patcher/patches",
        "--include-data-dir=$root\THIRD_PARTY_LICENSES=THIRD_PARTY_LICENSES",
        "--include-data-files=$root\engines\softbsl\*.hex=engines/softbsl/",
        "--include-data-file=$root\engines\softbsl\stage1_manifest.json=engines/softbsl/stage1_manifest.json",
        "--include-data-file=$root\engines\softbsl\agent_manifest.json=engines/softbsl/agent_manifest.json",
        "--include-data-file=$root\engines\patcher\romraider\BimmerStein MS41 Patch Definitions.xml=BimmerStein MS41 Patch Definitions.xml",
        "--include-data-file=$root\logger_definitions\BimmerStein MS41 Logger Definitions.xml=logger_definitions/BimmerStein MS41 Logger Definitions.xml",
        "--windows-icon-from-ico=$root\assets\bimmerstein_ecu_tool.ico",
        "--output-filename=BimmerStein ECU Tool.exe",
        "--output-dir=$outputDir",
        "--report=$buildDir\nuitka-report.xml",
        "--company-name=CAATZ",
        "--product-name=BimmerStein ECU Tool",
        "--file-description=BimmerStein ECU Tool",
        "--file-version=$numericVersion",
        "--product-version=$numericVersion",
        "--copyright=Copyright (C) 2026 CAATZ",
        "$root\packaging\nuitka_entry.py"
    )
    & $python @nuitkaArguments
    if ($LASTEXITCODE -ne 0) { throw "Nuitka build failed." }

    $builtApp = Join-Path $outputDir "nuitka_entry.dist"
    if (-not (Test-Path -LiteralPath (Join-Path $builtApp "BimmerStein ECU Tool.exe") -PathType Leaf)) {
        throw "Nuitka did not produce the expected standalone application."
    }
    $usb1Target = Join-Path $builtApp "usb1"
    New-Item -ItemType Directory -Path $usb1Target -Force | Out-Null
    Copy-Item -LiteralPath $usb1Dll -Destination $usb1Target
    Move-Item -LiteralPath $builtApp -Destination $appDir

    $releaseReadme = Get-Content -Raw -LiteralPath "README.md" -Encoding utf8
    $releaseReadme = $releaseReadme.Replace(
        'href="manual/USER_MANUAL.md"',
        'href="BimmerStein-ECU-Tool-User-Manual.pdf"'
    )
    $releaseReadme = $releaseReadme.Replace(
        'href="BUILDING.md">Build Guide</a>',
        'href="LICENSE.txt">License</a>'
    )
    foreach ($sourceOnlyLink in @(
        "User manual (web-readable Markdown)",
        "Build and release instructions"
    )) {
        $releaseReadme = [regex]::Replace(
            $releaseReadme,
            "(?m)^- \[$([regex]::Escape($sourceOnlyLink))\]\([^)]+\)\r?\n?",
            ''
        )
    }
    $releaseReadme = $releaseReadme.Replace(
        '(output/pdf/BimmerStein-ECU-Tool-User-Manual.pdf)',
        '(BimmerStein-ECU-Tool-User-Manual.pdf)'
    )
    foreach ($sourceOnlyHeading in @("Run from source", "Verify and build", "Project layout")) {
        $releaseReadme = [regex]::Replace(
            $releaseReadme,
            "(?ms)^## $([regex]::Escape($sourceOnlyHeading))\r?\n.*?(?=^## |\z)",
            ''
        )
    }
    $releaseReadme = $releaseReadme.Replace('(LICENSE)', '(LICENSE.txt)')
    [System.IO.File]::WriteAllText(
        (Join-Path $appDir "README.md"),
        $releaseReadme,
        [System.Text.UTF8Encoding]::new($false)
    )
    Copy-Item -LiteralPath "LICENSE" -Destination (Join-Path $appDir "LICENSE.txt")
    Copy-Item -LiteralPath "RELEASE_NOTES.md" -Destination $appDir
    Copy-Item -LiteralPath "THIRD_PARTY_NOTICES.md" -Destination $appDir
    Copy-Item -LiteralPath "output\pdf\BimmerStein-ECU-Tool-User-Manual.pdf" -Destination $appDir

    & $python "packaging\verify_dist.py" --backend nuitka --expected-version $Version $appDir
    if ($LASTEXITCODE -ne 0) { throw "Nuitka packaged-runtime verification failed." }

    Write-Host "Nuitka package ready: dist\BimmerStein ECU Tool Nuitka"
}
finally {
    Remove-Item Env:BIMMERSTEIN_VERSION -ErrorAction SilentlyContinue
    Remove-Item Env:NUITKA_CACHE_DIR -ErrorAction SilentlyContinue
    Pop-Location
}
