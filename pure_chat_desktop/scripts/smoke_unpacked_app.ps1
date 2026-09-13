param(
    [string]$AppPath = "",
    [int]$TimeoutSeconds = 240
)

$ErrorActionPreference = "Stop"
$desktopRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
if (-not $AppPath) {
    $AppPath = Join-Path $desktopRoot "dist\win-unpacked\Hanser Pure Chat.exe"
}
$AppPath = [IO.Path]::GetFullPath($AppPath)
if (-not (Test-Path -LiteralPath $AppPath -PathType Leaf)) {
    throw "Unpacked application is missing: $AppPath"
}

$smokeRoot = Join-Path $desktopRoot ("release\runtime\unpacked-smoke-{0}" -f (Get-Date -Format "yyyyMMdd-HHmmss"))
$frontendLog = Join-Path $smokeRoot "logs\frontend.log"
$backendLog = Join-Path $smokeRoot "logs\backend.log"
$previousDataRoot = $env:HANSER_DESKTOP_DATA_ROOT
$previousApiKey = $env:HANSER_MODEL_API_KEY
$appProcess = $null
$ready = $false

try {
    $env:HANSER_DESKTOP_DATA_ROOT = $smokeRoot
    $env:HANSER_MODEL_API_KEY = "smoke-test-placeholder"
    $appProcess = Start-Process -FilePath $AppPath -PassThru
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        $appProcess.Refresh()
        if ($appProcess.HasExited) {
            throw "Unpacked application exited before becoming ready (code=$($appProcess.ExitCode))."
        }
        if (Test-Path -LiteralPath $frontendLog) {
            $frontendText = Get-Content -LiteralPath $frontendLog -Raw
            if ($frontendText -match "Ready in") {
                $ready = $true
                break
            }
        }
        Start-Sleep -Milliseconds 500
    }
    if (-not $ready) {
        $backendTail = if (Test-Path -LiteralPath $backendLog) {
            (Get-Content -LiteralPath $backendLog -Tail 30) -join "`n"
        } else { "<backend log missing>" }
        $frontendTail = if (Test-Path -LiteralPath $frontendLog) {
            (Get-Content -LiteralPath $frontendLog -Tail 30) -join "`n"
        } else { "<frontend log missing>" }
        throw "Unpacked application readiness timed out.`nBackend:`n$backendTail`nFrontend:`n$frontendTail"
    }

    $database = Join-Path $smokeRoot "data\documents.db"
    $runtimeConfig = Join-Path $smokeRoot "runtime\config.desktop.yml"
    foreach ($path in @($database, $runtimeConfig, $backendLog, $frontendLog)) {
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
            throw "Runtime artifact is missing: $path"
        }
    }
    [pscustomobject]@{
        ready = $true
        data_root = $smokeRoot
        database_bytes = (Get-Item -LiteralPath $database).Length
        backend_log = $backendLog
        frontend_log = $frontendLog
    } | ConvertTo-Json
} finally {
    if ($null -ne $appProcess) {
        $appProcess.Refresh()
        if (-not $appProcess.HasExited) {
            $null = $appProcess.CloseMainWindow()
            if (-not $appProcess.WaitForExit(15000)) {
                Stop-Process -Id $appProcess.Id -Force
            }
        }
    }
    $packageRoot = Split-Path -Parent $AppPath
    $backendExecutable = Join-Path $packageRoot "resources\backend\hanser_backend\hanser_backend.exe"
    $lingering = Get-CimInstance Win32_Process | Where-Object {
        $_.ExecutablePath -eq $AppPath -or $_.ExecutablePath -eq $backendExecutable
    }
    foreach ($process in $lingering) {
        Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
    }
    $env:HANSER_DESKTOP_DATA_ROOT = $previousDataRoot
    $env:HANSER_MODEL_API_KEY = $previousApiKey
}
