param(
    [string]$SourceDatabase = "",
    [switch]$SkipBackendInstall
)

$ErrorActionPreference = "Stop"
$desktopRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))

$modelManifest = Join-Path $desktopRoot "resources\model-manifest.json"
if (-not (Test-Path -LiteralPath $modelManifest -PathType Leaf)) {
    throw "Model release manifest is missing. Run scripts\build_model_packages.ps1 first."
}
$modelRelease = Get-Content -LiteralPath $modelManifest -Raw | ConvertFrom-Json
foreach ($model in $modelRelease.models) {
    if ([int64]$model.bytes -le 0 -or $model.sha256 -match '^0{64}$') {
        throw "Model release manifest has placeholder metadata. Run scripts\build_model_packages.ps1 first."
    }
}

$seedDatabase = Join-Path $desktopRoot "resources\database\documents.seed.db"
$seedPython = Join-Path $desktopRoot "backend\.venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $seedPython)) {
    $seedPython = "python"
}
if ($SourceDatabase) {
    $sourceDatabase = [IO.Path]::GetFullPath($SourceDatabase)
    & $seedPython (Join-Path $PSScriptRoot "export_seed.py") $sourceDatabase $seedDatabase
    if ($LASTEXITCODE -ne 0) { throw "database seed export failed" }
} elseif (-not (Test-Path -LiteralPath $seedDatabase)) {
    $sourceDatabase = if ($env:HANSER_SEED_SOURCE) {
        [IO.Path]::GetFullPath($env:HANSER_SEED_SOURCE)
    } else {
        Join-Path $desktopRoot "resources\database\documents.dev.db"
    }
    if (-not (Test-Path -LiteralPath $sourceDatabase)) {
        throw "No seed source or existing seed found. Pass -SourceDatabase or set HANSER_SEED_SOURCE."
    }
    & $seedPython (Join-Path $PSScriptRoot "export_seed.py") $sourceDatabase $seedDatabase
    if ($LASTEXITCODE -ne 0) { throw "database seed export failed" }
}
& $seedPython (Join-Path $PSScriptRoot "verify_seed.py") $seedDatabase
if ($LASTEXITCODE -ne 0) { throw "database seed verification failed" }

& (Join-Path $PSScriptRoot "build_frontend.ps1")
& (Join-Path $PSScriptRoot "build_desktop.ps1")
& (Join-Path $PSScriptRoot "build_backend.ps1") -SkipInstall:$SkipBackendInstall

Push-Location $desktopRoot
try {
    & pnpm exec electron-builder --win nsis --x64
    if ($LASTEXITCODE -ne 0) { throw "electron-builder failed" }
    & (Join-Path $PSScriptRoot "verify_package.ps1")
} finally {
    Pop-Location
}
