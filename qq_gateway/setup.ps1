param([switch]$Dev)
$ErrorActionPreference = 'Stop'
$qqRoot = $PSScriptRoot
$qqPython = Join-Path $qqRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $qqPython)) {
    & py -3.13 -m venv (Join-Path $qqRoot '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'Cannot create Python 3.13 environment' }
}
$qqRequirements = if ($Dev) { 'requirements-dev.txt' } else { 'requirements.lock.txt' }
& $qqPython -m pip install -r (Join-Path $qqRoot $qqRequirements)
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed' }
$seedScript = Join-Path $qqRoot '..\chat_voice_desktop\scripts\create_seed.py'
& $qqPython $seedScript
if ($LASTEXITCODE -ne 0) { throw 'Database seed creation failed' }
Push-Location $qqRoot
try {
    & $qqPython -m hanser_qq prepare
    if ($LASTEXITCODE -ne 0) { throw 'QQ backend preparation failed' }
} finally { Pop-Location }
