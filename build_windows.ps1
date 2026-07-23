param(
    [switch]$SkipTests,
    [ValidatePattern('^(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)(?:b[1-9]\d*|-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$')]
    [string]$Version = "0.0.0-dev"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$root = $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    throw "Virtual environment not found. Create .venv and install requirements-build.txt first."
}

Push-Location $root
try {
    $env:BIMMERSTEIN_VERSION = $Version
    & $python -c "import PyInstaller, PyQt5, reportlab"
    if ($LASTEXITCODE -ne 0) {
        throw "Build dependencies are missing. Run: .venv\Scripts\python.exe -m pip install -r requirements-build.txt"
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
            "pytest-windows-build-" + [guid]::NewGuid().ToString("N")
        )
        & $python -m pytest -q -p no:cacheprovider --basetemp $testBaseTemp
        if ($LASTEXITCODE -ne 0) { throw "Automated tests failed." }
    }

    $appDir = Join-Path $root "dist\BimmerStein ECU Tool"
    $buildDir = Join-Path $root "build\BimmerSteinECUTool"
    foreach ($target in @($appDir, $buildDir)) {
        $fullTarget = [System.IO.Path]::GetFullPath($target)
        if (-not $fullTarget.StartsWith($root, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Refusing to clean a build path outside the repository: $fullTarget"
        }
        if (Test-Path -LiteralPath $fullTarget) {
            Remove-Item -LiteralPath $fullTarget -Recurse -Force
        }
    }

    & $python -m PyInstaller --clean --noconfirm "packaging\BimmerSteinECUTool.spec"
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed." }

    # PyInstaller data files live under _internal in one-folder mode. License
    # texts are public release documents, so expose the collected inventory at
    # the package root without keeping a duplicate hidden copy.
    $collectedLicenses = Join-Path $appDir "_internal\THIRD_PARTY_LICENSES"
    $publicLicenses = Join-Path $appDir "THIRD_PARTY_LICENSES"
    if (-not (Test-Path -LiteralPath $collectedLicenses -PathType Container)) {
        throw "PyInstaller did not collect the tracked third-party license inventory."
    }
    Move-Item -LiteralPath $collectedLicenses -Destination $publicLicenses
    $collectedDefinition = Join-Path $appDir "_internal\BimmerStein MS41 Patch Definitions.xml"
    if (-not (Test-Path -LiteralPath $collectedDefinition -PathType Leaf)) {
        throw "PyInstaller did not collect the patch definition."
    }
    Move-Item -LiteralPath $collectedDefinition -Destination $appDir

    $releaseReadme = Get-Content -Raw -LiteralPath "README.md" -Encoding utf8
    $releaseReadme = $releaseReadme.Replace(
        'src="assets/bimmerstein_ecu_tool.png"',
        'src="_internal/assets/bimmerstein_ecu_tool.png"'
    )
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

    & $python "packaging\verify_dist.py"
    if ($LASTEXITCODE -ne 0) { throw "Packaged-runtime verification failed." }

    Write-Host "Windows package ready: dist\BimmerStein ECU Tool"
}
finally {
    Remove-Item Env:BIMMERSTEIN_VERSION -ErrorAction SilentlyContinue
    Pop-Location
}
