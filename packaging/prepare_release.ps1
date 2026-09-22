param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)(?:b[1-9]\d*|-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$')]
    [string]$Version,

    [Parameter(Mandatory = $true)]
    [ValidateSet("Commercial", "GPLv3")]
    [string]$PyQtLicenseBasis,

    [ValidateSet("x64", "x86")][string[]]$Architectures = @("x64"),
    [string]$PythonPath,
    [string]$PythonX86Path,

    [switch]$SkipTests,

    [switch]$SkipInstaller,

    [string]$IsccPath
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$root = Split-Path -Parent $PSScriptRoot
$python = if ($PythonPath) { [System.IO.Path]::GetFullPath($PythonPath) } else { Join-Path $root ".venv\Scripts\python.exe" }
$nuitkaBuildScript = Join-Path $root "build_windows_nuitka.ps1"
$releaseRoot = [System.IO.Path]::GetFullPath((Join-Path $root "release"))
$releaseBoundary = $releaseRoot.TrimEnd([char[]]@('\', '/')) + [System.IO.Path]::DirectorySeparatorChar
$buildStartedAtUtc = [DateTime]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ")
$sourceCommit = ""
$sourceDirty = $null

function Assert-ReleaseSource {
    # Exported source archives have no Git provenance, even when extracted
    # inside another checkout. Never inherit an enclosing repository's commit.
    if (-not (Test-Path -LiteralPath (Join-Path $root ".git"))) {
        if ($script:sourceCommit) { throw "Git checkout disappeared during release preparation." }
        return
    }
    $commitOutput = & git -C $root rev-parse HEAD 2>$null
    if ($LASTEXITCODE -ne 0) { throw "Cannot read the release source commit." }
    $currentCommit = (($commitOutput -join "").Trim())
    $statusOutput = & git -C $root status --porcelain --untracked-files=normal 2>$null
    if ($LASTEXITCODE -ne 0) { throw "Cannot verify the release source status." }
    if (@($statusOutput).Count -gt 0) {
        throw "Release preparation requires a clean Git checkout."
    }
    $privateFiles = & git -C $root ls-files -- android/ _private/ 2>$null
    if ($LASTEXITCODE -ne 0) { throw "Cannot verify public release source paths." }
    if (@($privateFiles).Count -gt 0) {
        throw "Public release source must not track android/ or _private/."
    }
    if ($script:sourceCommit -and $script:sourceCommit -ne $currentCommit) {
        throw "Git commit changed during release preparation."
    }
    $script:sourceCommit = $currentCommit
    $script:sourceDirty = $false
}

Assert-ReleaseSource

function Stage-ReleasePackage {
    param(
        [Parameter(Mandatory = $true)][string]$SourceApp,
        [Parameter(Mandatory = $true)][string]$ReleaseName,
        [Parameter(Mandatory = $true)][ValidateSet("x64", "x86")][string]$Architecture,
        [Parameter(Mandatory = $true)][string]$PackagePython
    )

    Assert-ReleaseSource
    $releaseDir = Join-Path $releaseRoot $ReleaseName
    $archive = Join-Path $releaseRoot "$ReleaseName.zip"
    $checksum = "$archive.sha256"
    foreach ($target in @($releaseDir, $archive, $checksum)) {
        $fullTarget = [System.IO.Path]::GetFullPath($target)
        if ($fullTarget -ne $releaseRoot -and
                -not $fullTarget.StartsWith($releaseBoundary, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Refusing to replace a release path outside the release directory: $fullTarget"
        }
        if (Test-Path -LiteralPath $fullTarget) {
            Remove-Item -LiteralPath $fullTarget -Recurse -Force
        }
    }

    Copy-Item -LiteralPath $SourceApp -Destination $releaseDir -Recurse

    $verificationLines = & $PackagePython "packaging\verify_dist.py" --backend nuitka --architecture $Architecture --expected-version $Version $releaseDir
    if ($LASTEXITCODE -ne 0) { throw "Initial nuitka $Architecture release staging verification failed." }
    $verification = ($verificationLines -join [Environment]::NewLine) | ConvertFrom-Json

    $metadata = [ordered]@{
        product = "BimmerStein ECU Tool"
        developer = "CAATZ"
        repository = "https://github.com/CAATZ/BimmerStein-ECU-Tool"
        version = $Version
        platform = "Windows $Architecture"
        build_backend = "nuitka"
        built_at_utc = $buildStartedAtUtc
        source_commit = $sourceCommit
        source_dirty = $sourceDirty
        project_license = "GPL-3.0-only"
        intended_use = "off-road-only"
        pyqt_license_basis = $PyQtLicenseBasis
        calibration_definitions_bundled = $true
        vc_runtime_deployment = "application-local"
        vc_runtime_files_unmodified = $true
        vc_runtime_files = $verification.msvc_runtime
    }
    $metadata | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $releaseDir "RELEASE-METADATA.json") -Encoding utf8

    & $PackagePython "packaging\verify_dist.py" --backend nuitka --architecture $Architecture --expected-version $Version $releaseDir | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "nuitka $Architecture release staging verification failed." }

    Compress-Archive -LiteralPath $releaseDir -DestinationPath $archive -CompressionLevel Optimal
    $hash = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant()
    "$hash  $ReleaseName.zip" | Set-Content -LiteralPath $checksum -Encoding ascii

    [PSCustomObject]@{
        Architecture = $Architecture
        PackagePython = $PackagePython
        ReleaseName = $ReleaseName
        ReleaseDir = $releaseDir
        Archive = $archive
    }
}

function Build-ReleaseInstaller {
    param([Parameter(Mandatory = $true)]$Package)

    $installerArguments = @{
        Version = $Version
        SourceDir = $Package.ReleaseDir
        OutputDir = $releaseRoot
        Architecture = $Package.Architecture
        PythonPath = $Package.PackagePython
    }
    if ($IsccPath) {
        $installerArguments["IsccPath"] = $IsccPath
    }
    & (Join-Path $PSScriptRoot "build_installer.ps1") @installerArguments | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "$($Package.Architecture) Windows installer build failed." }
    Join-Path $releaseRoot "$($Package.ReleaseName)-Setup.exe"
}

Push-Location $root
try {
    & $python "engines\patcher\verify_ms412_emulator.py"
    if ($LASTEXITCODE -ne 0) {
        throw "Private MS41 patch-admission verification failed."
    }

    New-Item -ItemType Directory -Path $releaseRoot -Force | Out-Null
    $packages = @()
    foreach ($architecture in ($Architectures | Select-Object -Unique)) {
        $packagePython = if ($architecture -eq "x86") { $PythonX86Path } else { $python }
        if (-not $packagePython -or -not (Test-Path -LiteralPath $packagePython -PathType Leaf)) {
            throw "A matching Python runtime is required for $architecture. Use -PythonX86Path for x86."
        }
        & $nuitkaBuildScript -Version $Version -Architecture $architecture -PythonPath $packagePython -SkipTests:$SkipTests -SkipDocumentation:($packages.Count -gt 0)
        if ($LASTEXITCODE -ne 0) { throw "Nuitka $architecture package build failed." }
        $packages += Stage-ReleasePackage `
            -SourceApp (Join-Path $root "dist\$architecture\BimmerStein ECU Tool") `
            -ReleaseName "BimmerStein-ECU-Tool-$Version-Windows-$architecture" `
            -Architecture $architecture -PackagePython $packagePython
    }

    $artifacts = [System.Collections.Generic.List[string]]::new()
    foreach ($package in $packages) {
        $artifacts.Add($package.Archive)
        if (-not $SkipInstaller) {
            $artifacts.Add((Build-ReleaseInstaller -Package $package))
        }
    }

    $sumLines = foreach ($artifact in $artifacts) {
        if (-not (Test-Path -LiteralPath $artifact -PathType Leaf)) {
            throw "Expected release artifact is missing: $artifact"
        }
        $artifactHash = (Get-FileHash -LiteralPath $artifact -Algorithm SHA256).Hash.ToLowerInvariant()
        "$artifactHash  $([System.IO.Path]::GetFileName($artifact))"
    }
    $sumLines | Set-Content -LiteralPath (Join-Path $releaseRoot "SHA256SUMS.txt") -Encoding ascii

    foreach ($package in $packages) {
        Write-Host "Release package: $($package.Archive)"
    }
    Write-Host "Release manifest: $(Join-Path $releaseRoot 'SHA256SUMS.txt')"
}
finally {
    Pop-Location
}
