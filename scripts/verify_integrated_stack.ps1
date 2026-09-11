$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path

$requiredFiles = @(
    "assets\voices\hanser\manifest.jsonl",
    "assets\voices\hanser\training\v1\clip_000180.wav",
    ".runtime\mmd\hanser_v2.0_cloth2_test\hanser_ver2.0.pmx",
    "voice_runtime\models\VoxCPM2\config.json",
    "voice_runtime\models\VoxCPM2\model.safetensors",
    "voice_runtime\models\VoxCPM2\audiovae.pth",
    "voice_runtime\.venv\Scripts\python.exe",
    "voice_runtime\tts\voxcpm2_hybrid.py",
    "backend\hanser_agent\render_bridge.py",
    "chatbot\components\voice\voice-provider.tsx",
    "chatbot\components\live2d\mmd-stage.tsx",
    "chatbot\hooks\use-active-chat.tsx",
    "chatbot\lib\voice\playback-controller.ts",
    "chatbot\lib\live2d\timeline-evaluator.ts",
    "chatbot\lib\live2d\mmd-rig-adapter.ts",
    "scripts\start_hanser_chat.ps1"
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
$requiredVoiceSettings = @(
    "backend:\s*voxcpm2_hybrid",
    "id:\s*openbmb/VoxCPM2",
    "local_path:\s*models/VoxCPM2",
    "model_device:\s*cuda",
    "audio_vae_device:\s*cpu",
    "identity_reference_id:\s*hanser-voxcpm2-identity-clip180-v1",
    "default_style_prompt_id:\s*hanser-voxcpm2-neutral-clip180-v1"
)
foreach ($pattern in $requiredVoiceSettings) {
    if ($voiceProfile -notmatch $pattern) {
        throw "Voice profile is missing required VoxCPM2 hybrid setting: $pattern"
    }
}
if ($voiceProfile -match "(?i)gpt.?sovits|9880|api_v2") {
    throw "Legacy GPT Voice setting remains in the active Voice profile."
}

$manifestEntries = Get-Content -LiteralPath (Join-Path $projectRoot "assets\voices\hanser\manifest.jsonl") |
    Where-Object { $_.Trim() } |
    ForEach-Object { $_ | ConvertFrom-Json }
foreach ($entry in $manifestEntries) {
    if ($entry.review_status -ne "approved") {
        throw "Unapproved voice asset in manifest: $($entry.asset_id)"
    }
}
$activeAssetIds = @($manifestEntries.asset_id)
$expectedClipHash = "bfb98ceb99be7b9323426404d87586e3ee1503fd8b9dedd1f0debad0202f3f3c"
foreach ($assetId in @(
    "hanser-voxcpm2-identity-clip180-v1",
    "hanser-voxcpm2-neutral-clip180-v1"
)) {
    if ($assetId -notin $activeAssetIds) {
        throw "Active VoxCPM2 voice asset is missing from manifest: $assetId"
    }
    $asset = $manifestEntries | Where-Object { $_.asset_id -eq $assetId }
    if ($asset.source_path -ne "H:\voice_process\without_bgm_clarified\clip_000180.wav" -or
        $asset.source_sha256 -ne $expectedClipHash) {
        throw "Active VoxCPM2 voice asset has incomplete source provenance: $assetId"
    }
}
$clipPath = Join-Path $projectRoot "assets\voices\hanser\training\v1\clip_000180.wav"
if ((Get-FileHash -Algorithm SHA256 -LiteralPath $clipPath).Hash.ToLowerInvariant() -ne $expectedClipHash) {
    throw "Project-local clip_000180.wav does not match the reviewed source revision."
}

$backendConfig = Get-Content -Raw -LiteralPath (Join-Path $projectRoot "backend\config.example.yml")
foreach ($pattern in @(
    "speech_runtime_enabled:\s*true",
    "dynamic_live2d_enabled:\s*true",
    'render_profile_revision:\s*"hanser-voxcpm2-mmd-integrated-1"',
    'voice_runtime_base_url:\s*"http://127\.0\.0\.1:8770"'
)) {
    if ($backendConfig -notmatch $pattern) {
        throw "Chat backend integration setting is missing: $pattern"
    }
}

$launcher = Get-Content -Raw -LiteralPath (Join-Path $projectRoot "scripts\start_hanser_chat.ps1")
if ($launcher -notmatch 'HANSER_VOICE_LOAD_MODEL\s*=\s*"true"') {
    throw "Integrated launcher does not enable the configured Voice runtime."
}
if ($launcher -match "(?i)gpt.?sovits|9880|api_v2") {
    throw "Integrated launcher still references legacy GPT Voice."
}

$release = Get-Content -Raw -LiteralPath (Join-Path $projectRoot "release\performance-stack.candidate.yml")
foreach ($pattern in @(
    "engine_adapter:\s*voxcpm2_text_1",
    "voice_profile:\s*hanser-voxcpm2-hybrid-clip180-20260911-1",
    "voice_backend:\s*voxcpm2_hybrid_vae_cpu_1",
    "rig_profile:\s*hanser-rig-candidate-1"
)) {
    if ($release -notmatch $pattern) {
        throw "Release composition is missing revision: $pattern"
    }
}

$renderBridge = Get-Content -Raw -LiteralPath (Join-Path $projectRoot "backend\hanser_agent\render_bridge.py")
if ($renderBridge -notmatch '/internal/v1/jobs' -or $renderBridge -notmatch '/internal/v1/visual-plans') {
    throw "Chat backend does not expose both Voice and visual-plan bridges."
}
$playback = Get-Content -Raw -LiteralPath (Join-Path $projectRoot "chatbot\lib\voice\playback-controller.ts")
$mmdStage = Get-Content -Raw -LiteralPath (Join-Path $projectRoot "chatbot\components\live2d\mmd-stage.tsx")
$chatHook = Get-Content -Raw -LiteralPath (Join-Path $projectRoot "chatbot\hooks\use-active-chat.tsx")
if ($playback -notmatch 'samplePerformance\(\)' -or $playback -notmatch 'sampleOffset') {
    throw "Voice playback does not expose its sample-clock performance state."
}
if ($mmdStage -notmatch 'samplePerformance' -or $mmdStage -notmatch 'MmdRigAdapter') {
    throw "MMD/L2D stage is not connected to Voice playback state."
}
if ($chatHook -notmatch 'dynamic_live2d:\s*true' -or $chatHook -notmatch 'speech:\s*voiceRef\.current\.enabled') {
    throw "Chat does not request the active Voice + L2D composition."
}

Write-Output "Static integrated-stack verification passed: Chat -> VoxCPM2 hybrid Voice -> shared 48 kHz timeline -> MMD/L2D. No service or model was started."
