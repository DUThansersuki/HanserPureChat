$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$VoicePython = Join-Path $ProjectRoot "voice_runtime\.venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $VoicePython)) {
    throw "Voice environment missing. Run scripts/setup_voice_runtime.ps1 first."
}
Push-Location $ProjectRoot
try {
    & $VoicePython -m voice_runtime.api
}
finally {
    Pop-Location
}
