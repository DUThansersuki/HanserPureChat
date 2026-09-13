param(
    [int]$Port = 18777,
    [int]$TimeoutSeconds = 240
)

$ErrorActionPreference = "Stop"
$desktopRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$runtimeRoot = [IO.Path]::GetFullPath((Join-Path $desktopRoot "release\runtime"))
$runtimePrefix = $runtimeRoot.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
$executable = [IO.Path]::GetFullPath(
    (Join-Path $runtimeRoot "backend\hanser_backend\hanser_backend.exe")
)
if (-not $executable.StartsWith($runtimePrefix, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to launch backend outside release/runtime: $executable"
}
if (-not (Test-Path -LiteralPath $executable)) {
    throw "Packaged backend does not exist: $executable"
}

$configTemplate = [IO.Path]::GetFullPath((Join-Path $desktopRoot "backend\config.desktop.example.yml"))
$modelRoot = [IO.Path]::GetFullPath((Join-Path $desktopRoot "resources\models"))
$seedDatabase = [IO.Path]::GetFullPath((Join-Path $desktopRoot "resources\database\documents.seed.db"))
$smokeRoot = Join-Path $runtimeRoot "backend-smoke"
$smokeDataRoot = Join-Path $smokeRoot "data"
$smokeDatabase = Join-Path $smokeDataRoot "documents.db"
$config = Join-Path $smokeRoot "config.desktop.yml"
New-Item -ItemType Directory -Path $smokeDataRoot -Force | Out-Null
Copy-Item -LiteralPath $seedDatabase -Destination $smokeDatabase -Force
$configText = Get-Content -LiteralPath $configTemplate -Raw
$configText = $configText.Replace(
    '"../resources/database/documents.dev.db"',
    '"' + $smokeDatabase.Replace('\', '/') + '"'
)
$configText = $configText.Replace(
    '"../resources/data"',
    '"' + $smokeDataRoot.Replace('\', '/') + '"'
)
$configText = $configText.Replace(
    '"../resources/userdict.txt"',
    '"' + ([IO.Path]::GetFullPath((Join-Path $desktopRoot "resources\userdict.txt"))).Replace('\', '/') + '"'
)
$configText = $configText.Replace("local_files_only: false", "local_files_only: true")
Set-Content -LiteralPath $config -Value $configText -Encoding UTF8
$stdout = Join-Path $runtimeRoot "backend-smoke.stdout.log"
$stderr = Join-Path $runtimeRoot "backend-smoke.stderr.log"
$token = "packaged-backend-smoke-token"
$previousEnvironment = @{
    HANSER_CONFIG = $env:HANSER_CONFIG
    HANSER_BACKEND_PORT = $env:HANSER_BACKEND_PORT
    HANSER_DESKTOP_TOKEN = $env:HANSER_DESKTOP_TOKEN
    OPENAI_API_KEY = $env:OPENAI_API_KEY
    HF_HOME = $env:HF_HOME
}

$env:HANSER_CONFIG = $config
$env:HANSER_BACKEND_PORT = [string]$Port
$env:HANSER_DESKTOP_TOKEN = $token
$env:OPENAI_API_KEY = "not-used-by-health-check"
$env:HF_HOME = $modelRoot

$backendProcess = Start-Process `
    -FilePath $executable `
    -WorkingDirectory (Split-Path -Parent $executable) `
    -WindowStyle Hidden `
    -PassThru `
    -RedirectStandardOutput $stdout `
    -RedirectStandardError $stderr

try {
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $lastHealth = $null
    while ((Get-Date) -lt $deadline -and -not $backendProcess.HasExited) {
        try {
            $response = Invoke-RestMethod `
                -Uri "http://127.0.0.1:$Port/health" `
                -Headers @{ "X-Hanser-Desktop-Token" = $token } `
                -TimeoutSec 3
            $lastHealth = $response
            if ($response.ok -and $response.chat_ready) {
                $response | ConvertTo-Json -Depth 5
                return
            }
        } catch {
            Start-Sleep -Milliseconds 500
        }
    }
    Write-Output "Packaged backend failed health check. exit=$($backendProcess.ExitCode)"
    if ($lastHealth) {
        $lastHealth | ConvertTo-Json -Depth 5
    }
    if (Test-Path -LiteralPath $stderr) {
        Get-Content -LiteralPath $stderr -Tail 120
    }
    throw "Packaged backend did not become chat-ready."
} finally {
    if (-not $backendProcess.HasExited) {
        Stop-Process -Id $backendProcess.Id -Force
        $backendProcess.WaitForExit()
    }
    foreach ($name in $previousEnvironment.Keys) {
        if ($null -eq $previousEnvironment[$name]) {
            Remove-Item -LiteralPath "Env:$name" -ErrorAction SilentlyContinue
        } else {
            Set-Item -LiteralPath "Env:$name" -Value $previousEnvironment[$name]
        }
    }
}
