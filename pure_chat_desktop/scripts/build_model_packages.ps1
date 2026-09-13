param(
    [string]$ReleaseTag = "pure-chat-v0.1.0-beta.1",
    [string]$Repository = "DUThansersuki/HanserPureChat"
)

$ErrorActionPreference = "Stop"
$desktopRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$hubRoot = [IO.Path]::GetFullPath((Join-Path $desktopRoot "resources\models\hub"))
$outputRoot = [IO.Path]::GetFullPath((Join-Path $desktopRoot "dist\model-packages"))
$allowedOutputPrefix = ([IO.Path]::GetFullPath((Join-Path $desktopRoot "dist"))).TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
if (-not $outputRoot.StartsWith($allowedOutputPrefix, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to write model packages outside dist: $outputRoot"
}
New-Item -ItemType Directory -Path $outputRoot -Force | Out-Null

$models = @(
    [ordered]@{
        name = "Qwen3 Embedding 0.6B"
        folder = "models--Qwen--Qwen3-Embedding-0.6B"
        version = "beta.1"
        archive = "Qwen3-Embedding-0.6B.zip"
    },
    [ordered]@{
        name = "Qwen3 Reranker 0.6B"
        folder = "models--Qwen--Qwen3-Reranker-0.6B"
        version = "beta.1"
        archive = "Qwen3-Reranker-0.6B.zip"
    }
)

$manifestModels = @()
foreach ($model in $models) {
    $source = Join-Path $hubRoot $model.folder
    if (-not (Test-Path -LiteralPath $source -PathType Container)) {
        throw "Required model directory is missing: $source"
    }
    $archivePath = Join-Path $outputRoot $model.archive
    if (Test-Path -LiteralPath $archivePath) {
        Remove-Item -LiteralPath $archivePath -Force
    }
    Write-Host "Creating $($model.archive)..."
    Compress-Archive -LiteralPath $source -DestinationPath $archivePath -CompressionLevel NoCompression

    $archiveInfo = Get-Item -LiteralPath $archivePath
    $hash = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant()
    $manifestModels += [ordered]@{
        name = $model.name
        folder = $model.folder
        version = $model.version
        archive = $model.archive
        url = "https://github.com/$Repository/releases/download/$ReleaseTag/$($model.archive)"
        bytes = $archiveInfo.Length
        sha256 = $hash
    }
}

$manifest = [ordered]@{
    schema_version = 1
    models = $manifestModels
}
$json = ($manifest | ConvertTo-Json -Depth 5) + "`n"
$utf8NoBom = [Text.UTF8Encoding]::new($false)
[IO.File]::WriteAllText((Join-Path $desktopRoot "resources\model-manifest.json"), $json, $utf8NoBom)
[IO.File]::WriteAllText((Join-Path $outputRoot "model-manifest.json"), $json, $utf8NoBom)

$manifest | ConvertTo-Json -Depth 5
Write-Host "Model packages created: $outputRoot"
