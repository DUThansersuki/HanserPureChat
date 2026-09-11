$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path

$requiredFiles = @(
    "assets\voices\hanser\manifest.jsonl",
    "assets\voices\hanser\reference\identity.wav",
    "assets\voices\hanser\prompts\neutral.wav",
    ".runtime\mmd\hanser_v2.0_cloth2_test\hanser_ver2.0.pmx",
    "voice_runtime\models\GPT-SoVITS-v2Pro\api_v2.py",
    "voice_runtime\models\GPT-SoVITS-v2Pro\runtime\python.exe",
    "voice_runtime\models\GPT-SoVITS-v2Pro\GPT_SoVITS\pretrained_models\s1v3.ckpt",
    "voice_runtime\models\GPT-SoVITS-v2Pro\GPT_SoVITS\pretrained_models\v2Pro\s2Gv2Pro.pth"
)

foreach ($relativePath in $requiredFiles) {
    $path = Join-Path $projectRoot $relativePath
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Integrated stack file is missing: $relativePath"
    }
    if (-not ([System.IO.Path]::GetFullPath($path)).StartsWith(
        $projectRoot,
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
        throw "Integrated stack file escapes the project: $relativePath"
    }
}

$voiceProfile = Get-Content -Raw -LiteralPath (Join-Path $projectRoot "voice_runtime\config\voice_profile.candidate.yml")
if ($voiceProfile -notmatch "backend:\s*gpt_sovits_v2pro" -or $voiceProfile -match "(?i)voxcpm") {
    throw "Voice profile is not GPT-SoVITS-only."
}

$manifestPath = Join-Path $projectRoot "assets\voices\hanser\manifest.jsonl"
Get-Content -LiteralPath $manifestPath | ForEach-Object {
    $entry = $_ | ConvertFrom-Json
    if ($entry.review_status -ne "approved") {
        throw "Unapproved voice asset in manifest: $($entry.asset_id)"
    }
}

Write-Output "Integrated stack verification passed: Chat + GPT-SoVITS Voice + MMD/L2D assets are project-local."
