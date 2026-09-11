$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$VoiceRoot = Join-Path $ProjectRoot "voice_runtime"
$VoicePython = Join-Path $VoiceRoot ".venv\Scripts\python.exe"

$Python312 = py -3.12 -c "import sys; print(sys.executable)" 2>$null
if (-not $Python312) {
    throw "Python 3.12 is required. Install it before running this script."
}
if (-not (Test-Path -LiteralPath $VoicePython)) {
    & $Python312 -m venv (Join-Path $VoiceRoot ".venv")
}
& $VoicePython -m pip install --upgrade pip
& $VoicePython -m pip install -r (Join-Path $VoiceRoot "requirements-base.txt")
