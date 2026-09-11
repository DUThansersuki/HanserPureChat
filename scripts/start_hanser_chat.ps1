$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$backendRoot = Join-Path $projectRoot "backend"
$frontendRoot = Join-Path $projectRoot "chatbot"
$runtimeRoot = Join-Path $projectRoot ".runtime"
$voicePython = Join-Path $projectRoot "voice_runtime\.venv\Scripts\python.exe"
$backendPython = Join-Path $backendRoot ".venv\Scripts\python.exe"
$backendUrl = "http://127.0.0.1:8765"
$frontendUrl = "http://127.0.0.1:3000"
$voiceUrl = "http://127.0.0.1:8770"

New-Item -ItemType Directory -Path $runtimeRoot -Force | Out-Null

function Test-Endpoint([string]$Uri) {
    try {
        $response = Invoke-WebRequest -Uri $Uri -UseBasicParsing -TimeoutSec 2
        return $response.StatusCode -eq 200
    }
    catch {
        return $false
    }
}

function Wait-Endpoint(
    [string]$Uri,
    [System.Diagnostics.Process]$Process,
    [int]$TimeoutSeconds
) {
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-Endpoint $Uri) {
            return
        }
        if ($Process -and $Process.HasExited) {
            throw "Process exited early. See $runtimeRoot for logs."
        }
        Start-Sleep -Milliseconds 500
    }
    throw "Timed out waiting for $Uri. See $runtimeRoot for logs."
}

function Stop-ProcessTree([System.Diagnostics.Process]$Process) {
    if ($Process -and -not $Process.HasExited) {
        & taskkill.exe /PID $Process.Id /T /F | Out-Null
    }
}

$pythonPath = if (Test-Path -LiteralPath $backendPython) {
    $backendPython
}
else {
    (Get-Command python -ErrorAction Stop).Source
}
$pnpmCommand = Get-Command pnpm -ErrorAction SilentlyContinue
$pnpmPath = $null
if ($pnpmCommand) {
    $pnpmPath = $pnpmCommand.Source
    if (-not $pnpmPath) {
        $pnpmPath = $pnpmCommand.Path
    }
}

if (-not $pnpmPath) {
    $pnpmCandidates = @(
        (Join-Path $env:LOCALAPPDATA "pnpm\pnpm.cmd"),
        (Join-Path $env:APPDATA "npm\pnpm.cmd"),
        (Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\bin\fallback\pnpm.cmd")
    )
    $pnpmPath = $pnpmCandidates |
        Where-Object { $_ -and (Test-Path -LiteralPath $_) } |
        Select-Object -First 1
}

if (-not $pnpmPath -or -not (Test-Path -LiteralPath $pnpmPath)) {
    throw "pnpm was not found. Install pnpm or add it to PATH."
}

if (-not (Test-Path -LiteralPath (Join-Path $frontendRoot "node_modules"))) {
    Write-Host "First run: installing Vercel Chatbot dependencies..."
    Push-Location $frontendRoot
    try {
        & $pnpmPath install --frozen-lockfile
        if ($LASTEXITCODE -ne 0) {
            throw "pnpm install failed."
        }
    }
    finally {
        Pop-Location
    }
}

$backendProcess = $null
$frontendProcess = $null
$voiceProcess = $null
try {
    if ((Test-Path -LiteralPath $voicePython) -and -not (Test-Endpoint "$voiceUrl/health")) {
        $env:HANSER_VOICE_LOAD_MODEL = "true"
        $voiceProcess = Start-Process `
            -FilePath $voicePython `
            -ArgumentList "-m", "voice_runtime.api" `
            -WorkingDirectory $projectRoot `
            -RedirectStandardOutput (Join-Path $runtimeRoot "voice.out.log") `
            -RedirectStandardError (Join-Path $runtimeRoot "voice.err.log") `
            -WindowStyle Hidden `
            -PassThru
        Wait-Endpoint "$voiceUrl/health" $voiceProcess 180
    }

    if (-not (Test-Endpoint "$backendUrl/health")) {
        $backendProcess = Start-Process `
            -FilePath $pythonPath `
            -ArgumentList "run.py" `
            -WorkingDirectory $backendRoot `
            -RedirectStandardOutput (Join-Path $runtimeRoot "backend.out.log") `
            -RedirectStandardError (Join-Path $runtimeRoot "backend.err.log") `
            -WindowStyle Hidden `
            -PassThru
        Wait-Endpoint "$backendUrl/health" $backendProcess 90
    }

    $env:HANSER_API_BASE_URL = $backendUrl
    $env:HANSER_USER_ID = "local-user"
    if (-not (Test-Endpoint "$frontendUrl/ping")) {
        $frontendProcess = Start-Process `
            -FilePath $pnpmPath `
            -ArgumentList "dev", "--hostname", "127.0.0.1", "--port", "3000" `
            -WorkingDirectory $frontendRoot `
            -RedirectStandardOutput (Join-Path $runtimeRoot "frontend.out.log") `
            -RedirectStandardError (Join-Path $runtimeRoot "frontend.err.log") `
            -WindowStyle Hidden `
            -PassThru
        Wait-Endpoint "$frontendUrl/ping" $frontendProcess 90
    }

    Start-Process $frontendUrl
    Write-Host "Hanser Chat is open: $frontendUrl"
    Write-Host "Keep this window open. Close it or press Ctrl+C to stop services started by this launcher."

    if ($frontendProcess) {
        Wait-Process -Id $frontendProcess.Id
    }
    else {
        Read-Host "Services are already running. Press Enter to close this launcher"
    }
}
finally {
    Stop-ProcessTree $frontendProcess
    Stop-ProcessTree $backendProcess
    Stop-ProcessTree $voiceProcess
}
