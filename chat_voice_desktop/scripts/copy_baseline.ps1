[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

$targetRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$sourceRoot = (Resolve-Path (Join-Path $targetRoot "..")).Path

if ((Split-Path -Leaf $targetRoot) -ne "chat_voice_desktop") {
    throw "Unexpected target root: $targetRoot"
}

function Resolve-ContainedTarget([string]$RelativePath) {
    $target = [IO.Path]::GetFullPath((Join-Path $targetRoot $RelativePath))
    $prefix = $targetRoot.TrimEnd("\", "/") + [IO.Path]::DirectorySeparatorChar
    if (-not $target.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Target escaped chat_voice_desktop: $target"
    }
    return $target
}

function Add-FileCopy(
    [System.Collections.Generic.List[object]]$Items,
    [string]$Source,
    [string]$Destination
) {
    $sourcePath = [IO.Path]::GetFullPath((Join-Path $sourceRoot $Source))
    if (-not (Test-Path -LiteralPath $sourcePath -PathType Leaf)) {
        throw "Required baseline file is missing: $sourcePath"
    }
    $Items.Add([pscustomobject]@{
        Source = $sourcePath
        SourceRelative = $Source.Replace("\", "/")
        DestinationRelative = $Destination.Replace("\", "/")
    })
}

function Add-TreeCopy(
    [System.Collections.Generic.List[object]]$Items,
    [string]$Source,
    [string]$Destination,
    [string[]]$ExcludedDirectoryNames = @(),
    [string[]]$ExcludedRelativePaths = @()
) {
    $sourcePath = [IO.Path]::GetFullPath((Join-Path $sourceRoot $Source))
    if (-not (Test-Path -LiteralPath $sourcePath -PathType Container)) {
        throw "Required baseline directory is missing: $sourcePath"
    }
    $sourcePrefix = $sourcePath.TrimEnd("\", "/") + [IO.Path]::DirectorySeparatorChar
    Get-ChildItem -LiteralPath $sourcePath -Recurse -File | ForEach-Object {
        $relative = $_.FullName.Substring($sourcePrefix.Length).Replace("\", "/")
        $segments = $relative.Split("/")
        if (@($segments | Where-Object { $ExcludedDirectoryNames -contains $_ }).Count -gt 0) {
            return
        }
        if ($ExcludedRelativePaths -contains $relative) {
            return
        }
        $Items.Add([pscustomobject]@{
            Source = $_.FullName
            SourceRelative = ($Source.TrimEnd("/") + "/" + $relative).Replace("\", "/")
            DestinationRelative = ($Destination.TrimEnd("/") + "/" + $relative).Replace("\", "/")
        })
    }
}

$copies = [System.Collections.Generic.List[object]]::new()

# Pure Chat is the only Chat baseline. Generated and local runtime directories are excluded.
foreach ($directory in @("backend", "desktop", "frontend")) {
    Add-TreeCopy $copies "pure_chat_desktop/$directory" $directory @(
        ".venv", "node_modules", ".next", ".next-build", "__pycache__", ".pytest_cache"
    )
}
Add-TreeCopy $copies "pure_chat_desktop/scripts" "scripts" @() @("copy_baseline.ps1")
foreach ($file in @(
    ".gitignore",
    "ARCHITECTURE.md",
    "package.json",
    "pnpm-lock.yaml",
    "pnpm-workspace.yaml",
    "tsconfig.desktop.json"
)) {
    Add-FileCopy $copies "pure_chat_desktop/$file" $file
}
Add-FileCopy $copies "pure_chat_desktop/resources/userdict.txt" "resources/userdict.txt"
Add-FileCopy $copies "pure_chat_desktop/resources/database/documents.seed.db" "resources/database/documents.seed.db"

# Voice is a separate Python runtime. Models, environments, jobs, and caches are not copied here.
Add-TreeCopy $copies "voice_runtime" "voice_runtime" @(
    ".venv", "models", "data", "__pycache__", ".pytest_cache"
) @(
    "config/voice_profile.candidate.yml",
    "config/rig_profile.candidate.yml"
)
Add-FileCopy $copies "voice_runtime/config/voice_profile.candidate.yml" "voice_runtime/config/voice_profile.chat_voice.yml"

# Only the Voice UI/control files are migrated from the integrated frontend.
foreach ($file in @(
    "chatbot/lib/voice/client.ts",
    "chatbot/lib/voice/contracts.ts",
    "chatbot/lib/voice/playback-controller.ts",
    "chatbot/lib/voice/segment-queue.ts",
    "chatbot/lib/voice/segment-queue.test.ts",
    "chatbot/components/voice/voice-provider.tsx",
    "chatbot/components/voice/voice-controls.tsx"
)) {
    Add-FileCopy $copies $file ($file -replace '^chatbot/', 'frontend/')
}
Add-TreeCopy $copies "contracts/generated" "contracts/generated"

# First release runtime assets are intentionally limited to the reviewed clip180 pair.
Add-FileCopy $copies "assets/voices/hanser/training/v1/clip_000180.wav" "assets/voices/hanser/training/v1/clip_000180.wav"
Add-FileCopy $copies "assets/voices/hanser/training/v1/clip_000180.txt" "assets/voices/hanser/training/v1/clip_000180.txt"

$duplicateTargets = $copies | Group-Object DestinationRelative | Where-Object Count -gt 1
if ($duplicateTargets) {
    throw "Duplicate baseline destinations: $($duplicateTargets.Name -join ', ')"
}

foreach ($item in $copies) {
    $target = Resolve-ContainedTarget $item.DestinationRelative
    if (Test-Path -LiteralPath $target) {
        throw "Refusing to overwrite existing baseline target: $target"
    }
}

foreach ($item in $copies) {
    $target = Resolve-ContainedTarget $item.DestinationRelative
    New-Item -ItemType Directory -Path (Split-Path -Parent $target) -Force | Out-Null
    Copy-Item -LiteralPath $item.Source -Destination $target
}

$sourceCommit = (& git -C $sourceRoot rev-parse HEAD).Trim()
$sourceBranch = (& git -C $sourceRoot branch --show-current).Trim()
$entries = foreach ($item in $copies) {
    $file = Get-Item -LiteralPath $item.Source
    [ordered]@{
        source = $item.SourceRelative
        destination = $item.DestinationRelative
        bytes = $file.Length
        sha256 = (Get-FileHash -LiteralPath $item.Source -Algorithm SHA256).Hash.ToLowerInvariant()
    }
}
$manifest = [ordered]@{
    schema_version = 1
    created_at = [DateTimeOffset]::Now.ToString("o")
    source_branch = $sourceBranch
    source_commit = $sourceCommit
    source_worktree = "chat_voice_desktop already existed as an untracked documentation directory before the copy"
    chat_baseline = "pure_chat_desktop"
    voice_source = "voice_runtime"
    voice_ui_source = "chatbot/lib/voice and chatbot/components/voice"
    persona_package_id = "hanser-persona-v2-candidate-20260914-evidence-voice1"
    style_generation = "persona-v2-style-profanity15-1c71903569618d5d"
    asset_scope = @(
        "hanser-voxcpm2-identity-clip180-v1",
        "hanser-voxcpm2-neutral-clip180-v1"
    )
    exclusions = @(
        ".venv and shared Python environments",
        "node_modules and shared JavaScript dependencies",
        "build, dist, .next, caches, logs, and runtime data",
        "existing writable chat and voice databases",
        "voice models (deferred to P4 resource freeze)",
        "all voice assets except reviewed clip_000180",
        "Live2D, MMD, renderer, ASR, VAD, and microphone code"
    )
    files = @($entries | Sort-Object destination)
}

$manifestPath = Resolve-ContainedTarget "release/manifests/source-baseline.json"
New-Item -ItemType Directory -Path (Split-Path -Parent $manifestPath) -Force | Out-Null
$json = $manifest | ConvertTo-Json -Depth 8
[IO.File]::WriteAllText(
    $manifestPath,
    $json + [Environment]::NewLine,
    [Text.UTF8Encoding]::new($false)
)

Write-Host "Copied $($copies.Count) baseline files into chat_voice_desktop."
Write-Host "Source commit: $sourceCommit"
Write-Host "Manifest: $manifestPath"
