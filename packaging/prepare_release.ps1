param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)(?:b[1-9]\d*|-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$')]
    [string]$Version,

    [Parameter(Mandatory = $true)]
    [ValidateSet("Commercial", "GPLv3")]
    [string]$PyQtLicenseBasis,

    [switch]$IncludeExperimentalNuitka,

    [switch]$SkipTests,

    [switch]$SkipInstaller,

    [string]$IsccPath
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
$standardBuildScript = Join-Path $root "build_windows.ps1"
$nuitkaBuildScript = Join-Path $root "build_windows_nuitka.ps1"
$releaseRoot = Join-Path $root "release"

function Stage-ReleasePackage {
    param(
        [Parameter(Mandatory = $true)][string]$SourceApp,
        [Parameter(Mandatory = $true)][string]$ReleaseName,
        [Parameter(Mandatory = $true)][ValidateSet("pyinstaller", "nuitka")][string]$Backend,
        [Parameter(Mandatory = $true)][bool]$Experimental
    )

    $releaseDir = Join-Path $releaseRoot $ReleaseName
    $archive = Join-Path $releaseRoot "$ReleaseName.zip"
    $checksum = "$archive.sha256"
    foreach ($target in @($releaseDir, $archive, $checksum)) {
        $fullTarget = [System.IO.Path]::GetFullPath($target)
        if (-not $fullTarget.StartsWith($releaseRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Refusing to replace a release path outside the release directory: $fullTarget"
        }
        if (Test-Path -LiteralPath $fullTarget) {
            Remove-Item -LiteralPath $fullTarget -Recurse -Force
        }
    }

    Copy-Item -LiteralPath $SourceApp -Destination $releaseDir -Recurse

    $verificationLines = & $python "packaging\verify_dist.py" --backend $Backend $releaseDir
    if ($LASTEXITCODE -ne 0) { throw "Initial $Backend release staging verification failed." }
    $verification = ($verificationLines -join [Environment]::NewLine) | ConvertFrom-Json

    $metadata = [ordered]@{
        product = "BimmerStein ECU Tool"
        version = $Version
        platform = "Windows x64"
        build_backend = $Backend
        experimental = $Experimental
        project_license = "GPL-3.0-only"
        intended_use = "off-road-only"
        pyqt_license_basis = $PyQtLicenseBasis
        calibration_definitions_bundled = $false
        vc_runtime_deployment = "application-local"
        vc_runtime_files_unmodified = $true
        vc_runtime_files = $verification.msvc_runtime
    }
    $metadata | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $releaseDir "RELEASE-METADATA.json") -Encoding utf8

    & $python "packaging\verify_dist.py" --backend $Backend $releaseDir | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "$Backend release staging verification failed." }

    Compress-Archive -LiteralPath $releaseDir -DestinationPath $archive -CompressionLevel Optimal
    $hash = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant()
    "$hash  $ReleaseName.zip" | Set-Content -LiteralPath $checksum -Encoding ascii

    [PSCustomObject]@{
        Backend = $Backend
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
        Backend = $Package.Backend
    }
    if ($IsccPath) {
        $installerArguments["IsccPath"] = $IsccPath
    }
    & (Join-Path $PSScriptRoot "build_installer.ps1") @installerArguments | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "$($Package.Backend) Windows installer build failed." }
    Join-Path $releaseRoot "$($Package.ReleaseName)-Setup.exe"
}

Push-Location $root
try {
    & $standardBuildScript -Version $Version -SkipTests:$SkipTests
    if ($LASTEXITCODE -ne 0) { throw "Windows PyInstaller package build failed." }

    New-Item -ItemType Directory -Path $releaseRoot -Force | Out-Null
    $packages = @()
    $packages += Stage-ReleasePackage `
        -SourceApp (Join-Path $root "dist\BimmerStein ECU Tool") `
        -ReleaseName "BimmerStein-ECU-Tool-$Version-Windows-x64" `
        -Backend pyinstaller `
        -Experimental $false

    if ($IncludeExperimentalNuitka) {
        # The standard build above owns the source validation gate. Avoid running
        # the same full suite twice while compiling the alternate frozen backend.
        & $nuitkaBuildScript -Version $Version -SkipTests
        if ($LASTEXITCODE -ne 0) { throw "Experimental Nuitka package build failed." }
        $packages += Stage-ReleasePackage `
            -SourceApp (Join-Path $root "dist\BimmerStein ECU Tool Nuitka Experimental") `
            -ReleaseName "BimmerStein-ECU-Tool-$Version-Windows-x64-Nuitka-Experimental" `
            -Backend nuitka `
            -Experimental $true
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
    if ($IncludeExperimentalNuitka) {
        Write-Warning "The Nuitka installer and portable ZIP are EXPERIMENTAL; the regular PyInstaller installer remains recommended."
    }
}
finally {
    Pop-Location
}
