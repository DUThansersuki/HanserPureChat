param(
    [switch]$SkipBackendInstall,
    [switch]$SkipModelCopy
)

$ErrorActionPreference = "Stop"
$desktopRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))

if (-not $SkipModelCopy) {
    & (Join-Path $PSScriptRoot "prepare_models.ps1")
}

$sourceDatabase = Join-Path $desktopRoot "resources\database\documents.dev.db"
$seedDatabase = Join-Path $desktopRoot "resources\database\documents.seed.db"
$seedPython = Join-Path $desktopRoot "backend\.venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $seedPython)) {
    $seedPython = "python"
}
& $seedPython (Join-Path $PSScriptRoot "export_seed.py") $sourceDatabase $seedDatabase
if ($LASTEXITCODE -ne 0) { throw "database seed export failed" }

& (Join-Path $PSScriptRoot "build_frontend.ps1")
& (Join-Path $PSScriptRoot "build_desktop.ps1")
& (Join-Path $PSScriptRoot "build_backend.ps1") -SkipInstall:$SkipBackendInstall

Push-Location $desktopRoot
try {
    & pnpm exec electron-builder --win nsis --x64
    if ($LASTEXITCODE -ne 0) { throw "electron-builder failed" }
} finally {
    Pop-Location
}
