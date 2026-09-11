param(
    [string]$SourceRoot = "H:\hanser_ver2.0",
    [string]$DestinationRoot = "H:\HanserAgent\.runtime\mmd\hanser_v2.0_cloth2_test"
)

$ErrorActionPreference = "Stop"
$source = (Resolve-Path -LiteralPath $SourceRoot).Path
$runtime = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..\.runtime")).Path
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
