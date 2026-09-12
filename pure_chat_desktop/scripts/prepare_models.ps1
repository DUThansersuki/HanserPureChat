param(
    [string]$HuggingFaceHub = ""
)

$ErrorActionPreference = "Stop"
$desktopRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$modelRoot = [IO.Path]::GetFullPath((Join-Path $desktopRoot "resources\models"))
$allowedPrefix = ([IO.Path]::GetFullPath((Join-Path $desktopRoot "resources"))).TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
if (-not $modelRoot.StartsWith($allowedPrefix, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to prepare models outside desktop resources: $modelRoot"
}
if (-not $HuggingFaceHub) {
    $userProfilePath = [Environment]::GetFolderPath([Environment+SpecialFolder]::UserProfile)
    $HuggingFaceHub = Join-Path $userProfilePath ".cache\huggingface\hub"
}

$targetHub = Join-Path $modelRoot "hub"
New-Item -ItemType Directory -Path $targetHub -Force | Out-Null
$modelNames = @(
    "models--Qwen--Qwen3-Embedding-0.6B",
    "models--Qwen--Qwen3-Reranker-0.6B"
)
foreach ($modelName in $modelNames) {
    $source = Join-Path $HuggingFaceHub $modelName
    if (-not (Test-Path -LiteralPath $source)) {
        throw "Required model cache is missing: $source"
    }
    $destination = Join-Path $targetHub $modelName
    if (Test-Path -LiteralPath $destination) {
        Remove-Item -LiteralPath $destination -Recurse -Force
    }
    Copy-Item -LiteralPath $source -Destination $destination -Recurse -Force
    Write-Output "Prepared $modelName"
}
