param([switch]$WaitForServer)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$configPath = Join-Path $projectRoot 'qq_gateway\.runtime\napcat\installer\NapCat.52230.Shell\versions\9.9.33-52230\resources\app\napcat\config\webui.json'
$webConfig = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
$baseUrl = 'http://127.0.0.1:{0}/webui/' -f $webConfig.port

if ($WaitForServer) {
    $deadline = (Get-Date).AddSeconds(30)
    do {
        try {
            Invoke-WebRequest -Uri $baseUrl -UseBasicParsing -TimeoutSec 2 | Out-Null
            break
        }
        catch [System.Net.WebException] {
            if ((Get-Date) -ge $deadline) { throw 'NapCat WebUI is not ready; double-click Hanser-QQ again to reopen it.' }
            Start-Sleep -Milliseconds 300
        }
    } while ($true)
}

# NapCat's bundled login page accepts ?token= and submits it automatically.
# Read the current local credential each time; never echo or copy it to scripts.
$webUrl = $baseUrl + '?token=' + [Uri]::EscapeDataString([string]$webConfig.token)
Start-Process -FilePath $webUrl | Out-Null
