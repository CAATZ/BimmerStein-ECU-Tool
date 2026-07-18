param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)(?:b[1-9]\d*|-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$')]
    [string]$Version,

    [Parameter(Mandatory = $true)]
    [ValidateSet("Commercial", "GPLv3")]
    [string]$PyQtLicenseBasis,

    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$root = Split-Path -Parent $PSScriptRoot
$buildScript = Join-Path $root "build_windows.ps1"
$sourceApp = Join-Path $root "dist\BimmerStein ECU Tool"
$releaseRoot = Join-Path $root "release"
$releaseName = "BimmerStein-ECU-Tool-$Version-Windows-x64"
$releaseDir = Join-Path $releaseRoot $releaseName
$archive = Join-Path $releaseRoot "$releaseName.zip"
$checksum = "$archive.sha256"

Push-Location $root
try {
    & $buildScript -Version $Version -SkipTests:$SkipTests
    if ($LASTEXITCODE -ne 0) { throw "Windows package build failed." }

    New-Item -ItemType Directory -Path $releaseRoot -Force | Out-Null
    foreach ($target in @($releaseDir, $archive, $checksum)) {
        $fullTarget = [System.IO.Path]::GetFullPath($target)
        if (-not $fullTarget.StartsWith($releaseRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Refusing to replace a release path outside the release directory: $fullTarget"
        }
        if (Test-Path -LiteralPath $fullTarget) {
            Remove-Item -LiteralPath $fullTarget -Recurse -Force
        }
    }

    Copy-Item -LiteralPath $sourceApp -Destination $releaseDir -Recurse

    $verificationLines = & (Join-Path $root ".venv\Scripts\python.exe") "packaging\verify_dist.py" $releaseDir
    if ($LASTEXITCODE -ne 0) { throw "Initial release staging verification failed." }
    $verification = ($verificationLines -join [Environment]::NewLine) | ConvertFrom-Json

    $metadata = [ordered]@{
        product = "BimmerStein ECU Tool"
        version = $Version
        platform = "Windows x64"
        project_license = "GPL-3.0-only"
        intended_use = "off-road-only"
        pyqt_license_basis = $PyQtLicenseBasis
        calibration_definitions_bundled = $false
        vc_runtime_deployment = "application-local"
        vc_runtime_files_unmodified = $true
        vc_runtime_files = $verification.msvc_runtime
    }
    $metadata | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $releaseDir "RELEASE-METADATA.json") -Encoding utf8

    & (Join-Path $root ".venv\Scripts\python.exe") "packaging\verify_dist.py" $releaseDir
    if ($LASTEXITCODE -ne 0) { throw "Release staging verification failed." }

    Compress-Archive -LiteralPath $releaseDir -DestinationPath $archive -CompressionLevel Optimal
    $hash = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant()
    "$hash  $releaseName.zip" | Set-Content -LiteralPath $checksum -Encoding ascii

    Write-Host "Release package: $archive"
    Write-Host "SHA-256 file:  $checksum"
}
finally {
    Pop-Location
}
