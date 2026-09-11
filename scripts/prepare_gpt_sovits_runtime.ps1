param(
    [Parameter(Mandatory = $true)]
    [string]$SourceRoot
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$source = (Resolve-Path -LiteralPath $SourceRoot).Path
$destination = Join-Path $projectRoot "voice_runtime\models\GPT-SoVITS-v2Pro"

New-Item -ItemType Directory -Force -Path $destination | Out-Null
& robocopy.exe $source $destination /E /R:2 /W:1 /XD logs TEMP output .idea __pycache__ /XF *.log
if ($LASTEXITCODE -ge 8) {
    throw "GPT-SoVITS runtime copy failed with robocopy exit code $LASTEXITCODE."
}

foreach ($relativePath in @(
    "api_v2.py",
    "runtime\python.exe",
    "GPT_SoVITS\pretrained_models\s1v3.ckpt",
    "GPT_SoVITS\pretrained_models\v2Pro\s2Gv2Pro.pth"
)) {
    $required = Join-Path $destination $relativePath
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "Required GPT-SoVITS runtime file is missing: $required"
    }
}

Write-Output "GPT-SoVITS runtime prepared inside project: $destination"
