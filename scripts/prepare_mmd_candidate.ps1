param(
    [Parameter(Mandatory = $true)]
    [string]$SourceRoot,
    [string]$DestinationRoot = ""
)

$ErrorActionPreference = "Stop"
$project = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$source = (Resolve-Path -LiteralPath $SourceRoot).Path
$runtime = Join-Path $project ".runtime"
New-Item -ItemType Directory -Force -Path $runtime | Out-Null
if (-not $DestinationRoot) {
    $DestinationRoot = Join-Path $runtime "mmd\hanser_v2.0_cloth2_test"
}
$destination = [System.IO.Path]::GetFullPath($DestinationRoot)

if (-not $destination.StartsWith($runtime, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "DestinationRoot 必须位于项目 .runtime 目录内。"
}

New-Item -ItemType Directory -Force -Path $destination | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $destination "tex") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $destination "new") | Out-Null

Copy-Item -LiteralPath (Join-Path $source "hanser_ver2.0.pmx") -Destination $destination -Force
Get-ChildItem -LiteralPath (Join-Path $source "tex") -File |
    Copy-Item -Destination (Join-Path $destination "tex") -Force
Copy-Item -LiteralPath (Join-Path $source "tex\cloth2.png") `
    -Destination (Join-Path $destination "new\cloth1_BaseColor.png") -Force

Write-Output "MMD candidate prepared: $destination"
