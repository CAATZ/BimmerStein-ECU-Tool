param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)(?:b[1-9]\d*|-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$')]
    [string]$Version,

    [string]$SourceDir,

    [string]$OutputDir,

    [string]$IsccPath,

    [ValidateSet("pyinstaller", "nuitka")]
    [string]$Backend = "pyinstaller"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$root = Split-Path -Parent $PSScriptRoot
$releaseRoot = Join-Path $root "release"
$isExperimental = $Backend -eq "nuitka"
$packageSuffix = if ($isExperimental) { "-Nuitka-Experimental" } else { "" }
$releaseName = "BimmerStein-ECU-Tool-$Version-Windows-x64$packageSuffix"
if (-not $SourceDir) {
    $SourceDir = Join-Path $releaseRoot $releaseName
}
if (-not $OutputDir) {
    $OutputDir = $releaseRoot
}

$sourcePath = [System.IO.Path]::GetFullPath($SourceDir)
$outputPath = [System.IO.Path]::GetFullPath($OutputDir)
$releasePath = [System.IO.Path]::GetFullPath($releaseRoot)
if (-not $sourcePath.StartsWith($releasePath, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Installer source must be inside the repository release directory: $sourcePath"
}
if (-not $outputPath.StartsWith($releasePath, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Installer output must be inside the repository release directory: $outputPath"
}
if (-not (Test-Path -LiteralPath $sourcePath -PathType Container)) {
    throw "Prepared release directory not found: $sourcePath"
}

$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Virtual environment not found. Create .venv and install requirements-build.txt first."
}

$compilerCandidates = @(
    $IsccPath,
    $env:INNO_ISCC,
    "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    "C:\Program Files\Inno Setup 6\ISCC.exe",
    "C:\tmp\ECUEditor-InnoSetup6\ISCC.exe"
)
$compiler = $compilerCandidates |
    Where-Object { $_ -and (Test-Path -LiteralPath $_ -PathType Leaf) } |
    Select-Object -First 1
if (-not $compiler) {
    throw "Inno Setup compiler ISCC.exe was not found. Pass -IsccPath or set INNO_ISCC."
}
$compiler = [System.IO.Path]::GetFullPath($compiler)

Push-Location $root
try {
    & $python "packaging\verify_dist.py" --backend $Backend $sourcePath
    if ($LASTEXITCODE -ne 0) { throw "Installer source-package verification failed." }

    $match = [regex]::Match(
        $Version,
        '^(?<major>\d+)\.(?<minor>\d+)\.(?<patch>\d+)(?<beta>b\d+)?'
    )
    if (-not $match.Success) { throw "Could not derive installer version from: $Version" }

    $major = $match.Groups["major"].Value
    $minor = $match.Groups["minor"].Value
    $patch = $match.Groups["patch"].Value
    $betaNumber = 0
    $displayVersion = $Version
    if ($match.Groups["beta"].Success) {
        $betaNumber = [int]$match.Groups["beta"].Value.Substring(1)
        $displayVersion = "$major.$minor.$patch Beta $betaNumber"
    }
    $numericVersion = "$major.$minor.$patch.$betaNumber"

    New-Item -ItemType Directory -Path $outputPath -Force | Out-Null
    $installerName = "$releaseName-Setup.exe"
    $installer = Join-Path $outputPath $installerName
    $installerChecksum = "$installer.sha256"
    foreach ($target in @($installer, $installerChecksum)) {
        $fullTarget = [System.IO.Path]::GetFullPath($target)
        if (-not $fullTarget.StartsWith($releasePath, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Refusing to replace an installer artifact outside the release directory: $fullTarget"
        }
        if (Test-Path -LiteralPath $fullTarget) {
            Remove-Item -LiteralPath $fullTarget -Force
        }
    }

    $compilerArguments = @(
        "/Qp",
        "/DAppVersion=$Version",
        "/DAppDisplayVersion=$displayVersion",
        "/DAppNumericVersion=$numericVersion",
        "/DPackageSuffix=$packageSuffix",
        "/DSourceDir=$sourcePath",
        "/DOutputDir=$outputPath",
        "packaging\BimmerSteinECUTool.iss"
    )
    if ($isExperimental) {
        $compilerArguments = @("/DNuitkaExperimental") + $compilerArguments
    }
    $compilerArgumentText = ($compilerArguments | ForEach-Object {
        '"' + $_.Replace('"', '\"') + '"'
    }) -join ' '
    $compilerProcess = [System.Diagnostics.Process]::Start(
        $compiler,
        $compilerArgumentText
    )
    if ($null -eq $compilerProcess) { throw "Inno Setup compiler did not start." }
    $compilerProcess.WaitForExit()
    if ($compilerProcess.ExitCode -ne 0) { throw "Inno Setup compilation failed." }
    if (-not (Test-Path -LiteralPath $installer -PathType Leaf)) {
        throw "Inno Setup did not produce the expected installer: $installer"
    }

    $versionInfo = (Get-Item -LiteralPath $installer).VersionInfo
    $productName = $versionInfo.ProductName.Trim()
    $productVersion = $versionInfo.ProductVersion.Trim()
    $expectedProductName = if ($isExperimental) {
        "BimmerStein ECU Tool (Nuitka Experimental)"
    } else {
        "BimmerStein ECU Tool"
    }
    if ($productName -ne $expectedProductName) {
        throw "Installer product metadata is incorrect: $($versionInfo.ProductName)"
    }
    if ($productVersion -ne $numericVersion) {
        throw "Installer version metadata is incorrect: $($versionInfo.ProductVersion)"
    }

    $installerHash = (Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash.ToLowerInvariant()
    "$installerHash  $installerName" |
        Set-Content -LiteralPath $installerChecksum -Encoding ascii

    $portableArchive = Join-Path $outputPath "$releaseName.zip"
    $sumLines = @()
    if (Test-Path -LiteralPath $portableArchive -PathType Leaf) {
        $portableHash = (Get-FileHash -LiteralPath $portableArchive -Algorithm SHA256).Hash.ToLowerInvariant()
        $sumLines += "$portableHash  $releaseName.zip"
    }
    $sumLines += "$installerHash  $installerName"
    $sumLines | Set-Content -LiteralPath (Join-Path $outputPath "SHA256SUMS.txt") -Encoding ascii

    Write-Host "Windows installer: $installer"
    Write-Host "SHA-256 file:     $installerChecksum"
}
finally {
    Pop-Location
}
