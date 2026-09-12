param(
    [string]$PythonExe = "",
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"
$desktopRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$backendRoot = Join-Path $desktopRoot "backend"
$venvRoot = Join-Path $backendRoot ".venv"
$venvPython = Join-Path $venvRoot "Scripts\python.exe"
$runtimeBackendRoot = [IO.Path]::GetFullPath((Join-Path $desktopRoot "release\runtime\backend"))
$allowedPrefix = ([IO.Path]::GetFullPath((Join-Path $desktopRoot "release\runtime"))).TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
if (-not $runtimeBackendRoot.StartsWith($allowedPrefix, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to replace backend output outside release/runtime: $runtimeBackendRoot"
}

if (-not $PythonExe) {
    if (-not (Test-Path -LiteralPath $venvPython)) {
        & py -3.13 -m venv $venvRoot
        if ($LASTEXITCODE -ne 0) { throw "Unable to create backend build environment" }
    }
    $PythonExe = $venvPython
}
if (-not (Test-Path -LiteralPath $PythonExe)) {
    throw "Python executable not found: $PythonExe"
}

if (-not $SkipInstall) {
    & $PythonExe -m pip install --disable-pip-version-check -r (Join-Path $backendRoot "requirements-build.txt")
    if ($LASTEXITCODE -ne 0) { throw "backend dependency installation failed" }
}
if (Test-Path -LiteralPath $runtimeBackendRoot) {
    Remove-Item -LiteralPath $runtimeBackendRoot -Recurse -Force
}
New-Item -ItemType Directory -Path $runtimeBackendRoot -Force | Out-Null

$workRoot = Join-Path $desktopRoot "build\pyinstaller"
New-Item -ItemType Directory -Path $workRoot -Force | Out-Null
Push-Location $desktopRoot
try {
    & $PythonExe -m PyInstaller `
        --noconfirm `
        --clean `
        --onedir `
        --name hanser_backend `
        --distpath $runtimeBackendRoot `
        --workpath $workRoot `
        --specpath (Join-Path $backendRoot "packaging") `
        --paths $backendRoot `
        --collect-all transformers `
        --collect-all tokenizers `
        --add-data "$(Join-Path $backendRoot 'hanser_agent\prompts');hanser_agent\prompts" `
        --exclude-module tkinter `
        (Join-Path $backendRoot "run_desktop.py")
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller backend build failed" }
} finally {
    Pop-Location
}

$backendExe = Join-Path $runtimeBackendRoot "hanser_backend\hanser_backend.exe"
if (-not (Test-Path -LiteralPath $backendExe)) {
    throw "PyInstaller output is missing: $backendExe"
}
Write-Output "Backend runtime ready: $backendExe"
