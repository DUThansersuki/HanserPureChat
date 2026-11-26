param([switch]$VoiceOnly, [switch]$Voice)
$ErrorActionPreference = 'Stop'
$desktopRoot = $PSScriptRoot

if (-not $VoiceOnly) {
    $backend = Join-Path $desktopRoot 'backend\.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $backend)) {
        & py -3.13 -m venv (Join-Path $desktopRoot 'backend\.venv')
        if ($LASTEXITCODE -ne 0) { throw 'Cannot create desktop Python 3.13 environment.' }
    }
    & $backend -m pip install -r (Join-Path $desktopRoot 'backend\requirements-runtime.txt')
    if ($LASTEXITCODE -ne 0) { throw 'Cannot install desktop backend dependencies.' }
    & $backend (Join-Path $desktopRoot 'scripts\create_seed.py')
    if ($LASTEXITCODE -ne 0) { throw 'Cannot create the database seed.' }

    Push-Location $desktopRoot
    try {
        & pnpm install --frozen-lockfile
        if ($LASTEXITCODE -ne 0) { throw 'Cannot install Electron dependencies.' }
        & pnpm build:desktop
        if ($LASTEXITCODE -ne 0) { throw 'Cannot build Electron.' }
        & pnpm build:frontend
        if ($LASTEXITCODE -ne 0) { throw 'Cannot build the frontend.' }
    } finally { Pop-Location }
}

if ($VoiceOnly -or $Voice) {
    $voicePython = Join-Path $desktopRoot 'voice_runtime\.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $voicePython)) {
        & py -3.12 -m venv (Join-Path $desktopRoot 'voice_runtime\.venv')
        if ($LASTEXITCODE -ne 0) { throw 'Cannot create Voice Python 3.12 environment.' }
    }
    & $voicePython -m pip install --index-url https://download.pytorch.org/whl/cu130 torch==2.11.0+cu130 torchaudio==2.11.0+cu130
    if ($LASTEXITCODE -ne 0) { throw 'Cannot install CUDA PyTorch.' }
    & $voicePython -m pip install -r (Join-Path $desktopRoot 'voice_runtime\requirements-base.txt') -r (Join-Path $desktopRoot 'voice_runtime\requirements-cuda-lock.txt')
    if ($LASTEXITCODE -ne 0) { throw 'Cannot install voice dependencies.' }
    & $voicePython -m pip install --no-deps -r (Join-Path $desktopRoot 'voice_runtime\requirements-voxcpm.txt')
    if ($LASTEXITCODE -ne 0) { throw 'Cannot install the pinned VoxCPM2 inference package.' }
}
