$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$qqRoot = Join-Path $projectRoot 'qq_gateway'
$python = Join-Path $qqRoot '.venv\Scripts\python.exe'
$napcatRoot = Join-Path $qqRoot '.runtime\napcat\installer\NapCat.52230.Shell'
$boot = Join-Path $napcatRoot 'NapCatWinBootMain.exe'
$webUiLauncher = Join-Path $PSScriptRoot 'open_qq_webui.ps1'
if (-not (Test-Path -LiteralPath $python)) { throw 'Run qq_gateway\setup.ps1 first.' }
if (-not (Test-Path -LiteralPath $boot)) { throw 'NapCat is missing; see qq_gateway\NAPCAT_NEXT.md.' }

$mutex = New-Object System.Threading.Mutex($false, 'Local\HanserQQDesktopLauncher')
if (-not $mutex.WaitOne(0)) {
    Write-Host 'Hanser QQ launcher is already open.'
    $mutex.Dispose()
    & $webUiLauncher
    exit 0
}
Push-Location $qqRoot
try {
    # The bundled NapCat quick-login example passes the account as its first
    # argument. Reuse QQ's saved session; never store a password in this script.
    $botId = & $python -c "from pathlib import Path; from hanser_qq.config import load_settings; s=load_settings(Path('config.yml')); print(next(iter(s.gateway.bot_ids), ''))"
    if ($LASTEXITCODE -ne 0) { throw 'Cannot read QQ configuration.' }
    $existing = Get-CimInstance Win32_Process -Filter "Name='QQ.exe' OR Name='NapCatWinBootMain.exe'" |
        Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($napcatRoot + '\', [StringComparison]::OrdinalIgnoreCase) }
    if (-not $existing) {
        $startArgs = @{
            FilePath = $boot
            WorkingDirectory = $napcatRoot
            WindowStyle = 'Hidden'
            RedirectStandardOutput = (Join-Path $qqRoot '.runtime\napcat-boot.out.log')
            RedirectStandardError = (Join-Path $qqRoot '.runtime\napcat-boot.err.log')
        }
        if ($botId) { $startArgs.ArgumentList = @($botId.Trim()) }
        Start-Process @startArgs | Out-Null
        Write-Host 'NapCat started with saved-account quick login.'
        Write-Host 'Opening NapCat WebUI with automatic token login.'
    }
    & $webUiLauncher -WaitForServer:(-not [bool]$existing)
    $ErrorActionPreference = 'Continue'
    & $python -m hanser_qq status *> $null
    $alreadyRunning = $LASTEXITCODE -eq 0
    $ErrorActionPreference = 'Stop'
    if ($alreadyRunning) {
        Write-Host 'Hanser QQ is already running. NapCat will reconnect automatically.'
    }
    else {
        Write-Host 'Starting Hanser QQ. Keep this window open; Ctrl+C stops its chat services.'
        & $python -m hanser_qq run
        if ($LASTEXITCODE -ne 0) { throw 'Hanser QQ exited; inspect qq_gateway\.runtime logs.' }
    }
}
finally {
    Pop-Location
    $mutex.ReleaseMutex()
    $mutex.Dispose()
}
