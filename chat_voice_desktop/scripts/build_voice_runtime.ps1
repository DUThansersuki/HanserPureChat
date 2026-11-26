[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$PythonDistribution,
    [string]$SitePackages = ""
)

$ErrorActionPreference = "Stop"
$desktopRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$source = [IO.Path]::GetFullPath($PythonDistribution)
$target = [IO.Path]::GetFullPath((Join-Path $desktopRoot "release\runtime\voice\python"))
$allowedRoot = [IO.Path]::GetFullPath((Join-Path $desktopRoot "release\runtime\voice"))

if (-not (Test-Path -LiteralPath (Join-Path $source "python.exe") -PathType Leaf)) {
    throw "PythonDistribution must contain python.exe"
}
if ($source -match "(?i)[\\/]\.venv([\\/]|$)") {
    throw "A development .venv is not a relocatable Voice runtime distribution"
}
$sitePackagesSource = $null
if ($SitePackages) {
    $sitePackagesSource = [IO.Path]::GetFullPath($SitePackages)
    if (-not (Test-Path -LiteralPath $sitePackagesSource -PathType Container)) {
        throw "SitePackages must be an existing directory"
    }
}
if (-not $target.StartsWith(
    $allowedRoot.TrimEnd("\", "/") + [IO.Path]::DirectorySeparatorChar,
    [StringComparison]::OrdinalIgnoreCase
)) {
    throw "Voice runtime target escaped release/runtime/voice"
}
if (Test-Path -LiteralPath $target) {
    Remove-Item -LiteralPath $target -Recurse -Force
}
New-Item -ItemType Directory -Path (Split-Path -Parent $target) -Force | Out-Null
Copy-Item -LiteralPath $source -Destination $target -Recurse

if ($sitePackagesSource) {
    $targetSitePackages = Join-Path $target "Lib\site-packages"
    New-Item -ItemType Directory -Path $targetSitePackages -Force | Out-Null
    Get-ChildItem -LiteralPath $sitePackagesSource -Force | ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination $targetSitePackages -Recurse -Force
    }
}

$python = Join-Path $target "python.exe"
& $python -c "import fastapi, torch, torchaudio, voxcpm; from voxcpm import VoxCPM; assert torch.cuda.is_available(); print(torch.__version__, torchaudio.__version__)"
if ($LASTEXITCODE -ne 0) {
    throw "Relocatable Voice Python validation failed"
}
Write-Host "Voice Python runtime ready: $target"
