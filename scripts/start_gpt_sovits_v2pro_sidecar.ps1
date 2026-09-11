param(
    [string]$ConfigPath = "",
    [int]$Port = 9880
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$DistributionRoot = Join-Path $ProjectRoot "voice_runtime\models\GPT-SoVITS-v2Pro"
$ResolvedDistribution = (Resolve-Path -LiteralPath $DistributionRoot).Path
$Python = Join-Path $ResolvedDistribution "runtime\python.exe"
$Api = Join-Path $ResolvedDistribution "api_v2.py"
if (-not $ConfigPath) {
    $ConfigPath = Join-Path $ProjectRoot "voice_runtime\config\gpt_sovits_v2pro.sidecar.candidate.yml"
}
$ResolvedConfig = (Resolve-Path -LiteralPath $ConfigPath).Path

foreach ($RequiredPath in @($Python, $Api, $ResolvedConfig)) {
    if (-not (Test-Path -LiteralPath $RequiredPath -PathType Leaf)) {
        throw "Required GPT-SoVITS file is missing: $RequiredPath"
    }
}
if ($Port -lt 1 -or $Port -gt 65535) {
    throw "Port must be between 1 and 65535."
}

Push-Location $ResolvedDistribution
try {
    & $Python $Api -a 127.0.0.1 -p $Port -c $ResolvedConfig
}
finally {
    Pop-Location
}
