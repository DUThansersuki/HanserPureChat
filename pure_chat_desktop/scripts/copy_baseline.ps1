[CmdletBinding()]
param(
    [switch]$ManifestOnly
)

$ErrorActionPreference = "Stop"

$desktopRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$sourceRoot = (Resolve-Path (Join-Path $desktopRoot "..")).Path

function Get-RelativePath([string]$BasePath, [string]$TargetPath) {
    $normalizedBase = $BasePath.TrimEnd("\", "/") + [IO.Path]::DirectorySeparatorChar
    $baseUri = [Uri]::new($normalizedBase)
    $targetUri = [Uri]::new($TargetPath)
    return [Uri]::UnescapeDataString(
        $baseUri.MakeRelativeUri($targetUri).ToString()
    ).Replace("/", [IO.Path]::DirectorySeparatorChar)
}

if ((Split-Path -Leaf $desktopRoot) -ne "pure_chat_desktop") {
    throw "Unexpected desktop target: $desktopRoot"
}

$sourceBranch = "codex/chat-l2d-gpt-voice-full"
$sourceCommit = (& git -C $sourceRoot rev-parse $sourceBranch).Trim()
if (-not $sourceCommit) {
    throw "Unable to resolve source branch $sourceBranch"
}

$directoryCopies = @(
    @{ Source = "chatbot/app"; Destination = "frontend/app" },
    @{ Source = "chatbot/artifacts"; Destination = "frontend/artifacts" },
    @{ Source = "chatbot/components"; Destination = "frontend/components" },
    @{ Source = "chatbot/hooks"; Destination = "frontend/hooks" },
    @{ Source = "chatbot/lib"; Destination = "frontend/lib" },
    @{ Source = "chatbot/public"; Destination = "frontend/public" },
    @{ Source = "backend/hanser_agent"; Destination = "backend/hanser_agent" }
)

$fileCopies = @(
    @{ Source = "chatbot/.env.example"; Destination = "frontend/.env.example" },
    @{ Source = "chatbot/biome.jsonc"; Destination = "frontend/biome.jsonc" },
    @{ Source = "chatbot/components.json"; Destination = "frontend/components.json" },
    @{ Source = "chatbot/instrumentation-client.ts"; Destination = "frontend/instrumentation-client.ts" },
    @{ Source = "chatbot/instrumentation.ts"; Destination = "frontend/instrumentation.ts" },
    @{ Source = "chatbot/next.config.ts"; Destination = "frontend/next.config.ts" },
    @{ Source = "chatbot/package.json"; Destination = "frontend/package.json" },
    @{ Source = "chatbot/pnpm-lock.yaml"; Destination = "frontend/pnpm-lock.yaml" },
    @{ Source = "chatbot/pnpm-workspace.yaml"; Destination = "frontend/pnpm-workspace.yaml" },
    @{ Source = "chatbot/postcss.config.mjs"; Destination = "frontend/postcss.config.mjs" },
    @{ Source = "chatbot/proxy.ts"; Destination = "frontend/proxy.ts" },
    @{ Source = "chatbot/tsconfig.json"; Destination = "frontend/tsconfig.json" },
    @{ Source = "backend/config.example.yml"; Destination = "backend/config.source.example.yml" },
    @{ Source = "backend/requirements.txt"; Destination = "backend/requirements.source.txt" },
    @{ Source = "backend/requirements-dev.txt"; Destination = "backend/requirements-dev.source.txt" },
    @{ Source = "backend/run_chat_only.py"; Destination = "backend/run_chat_only.source.py" },
    @{ Source = "source_data/documents.db"; Destination = "resources/database/documents.dev.db" },
    @{ Source = "source_data/userdict.txt"; Destination = "resources/userdict.txt" }
)

if (-not $ManifestOnly) {
    $targets = @($directoryCopies.Destination + $fileCopies.Destination)
    foreach ($relativeTarget in $targets) {
        $target = [IO.Path]::GetFullPath((Join-Path $desktopRoot $relativeTarget))
        if (-not $target.StartsWith($desktopRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
            throw "Target escaped desktop root: $target"
        }
        if (Test-Path -LiteralPath $target) {
            throw "Refusing to overwrite existing baseline target: $target"
        }
    }

    foreach ($item in $directoryCopies) {
        $source = Join-Path $sourceRoot $item.Source
        $destination = Join-Path $desktopRoot $item.Destination
        New-Item -ItemType Directory -Path (Split-Path -Parent $destination) -Force | Out-Null
        Copy-Item -LiteralPath $source -Destination $destination -Recurse
    }

    foreach ($item in $fileCopies) {
        $source = Join-Path $sourceRoot $item.Source
        $destination = Join-Path $desktopRoot $item.Destination
        New-Item -ItemType Directory -Path (Split-Path -Parent $destination) -Force | Out-Null
        Copy-Item -LiteralPath $source -Destination $destination
    }
}

$entries = foreach ($item in $directoryCopies) {
    $sourceBase = Join-Path $sourceRoot $item.Source
    Get-ChildItem -LiteralPath $sourceBase -Recurse -File | ForEach-Object {
        $sourceRelative = (Get-RelativePath $sourceRoot $_.FullName).Replace("\", "/")
        $destinationRelative = (Get-RelativePath $sourceBase $_.FullName).Replace("\", "/")
        [ordered]@{
            source = $sourceRelative
            destination = ($item.Destination.TrimEnd("/") + "/" + $destinationRelative)
            bytes = $_.Length
            sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        }
    }
}

$entries += foreach ($item in $fileCopies) {
    $source = Join-Path $sourceRoot $item.Source
    $file = Get-Item -LiteralPath $source
    [ordered]@{
        source = $item.Source.Replace("\", "/")
        destination = $item.Destination.Replace("\", "/")
        bytes = $file.Length
        sha256 = (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash.ToLowerInvariant()
    }
}

$manifest = [ordered]@{
    schema_version = 1
    created_at = [DateTimeOffset]::Now.ToString("o")
    source_branch = $sourceBranch
    source_commit = $sourceCommit
    source_root = "H:/HanserAgent"
    copy_mode = "filesystem snapshot; source tree was not modified"
    persona_selector = "persona-v2-production"
    persona_package_id = "hanser-persona-v2-production-20260910"
    style_generation = "persona-v2-style-profanity15-1c71903569618d5d"
    exclusions = @(
        "all source node_modules, .next, .venv and runtime outputs",
        "voice_runtime",
        "renderer",
        "assets/live2d",
        "assets/voices",
        "audit_artifacts",
        "backend/data/eval run outputs",
        "source_data backups and raw documents"
    )
    files = @($entries | Sort-Object source)
}

$manifestDirectory = Join-Path $desktopRoot "release"
New-Item -ItemType Directory -Path $manifestDirectory -Force | Out-Null
$manifestPath = Join-Path $manifestDirectory "source-baseline.json"
$json = $manifest | ConvertTo-Json -Depth 8
[IO.File]::WriteAllText($manifestPath, $json + [Environment]::NewLine, [Text.UTF8Encoding]::new($false))

Write-Host "Copied $($entries.Count) baseline files."
Write-Host "Source commit: $sourceCommit"
Write-Host "Manifest: $manifestPath"
