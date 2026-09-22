# Build the supported Windows package.
& "$PSScriptRoot\build_windows_nuitka.ps1" @args
exit $LASTEXITCODE
