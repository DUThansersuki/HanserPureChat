$ErrorActionPreference = "Stop"

$desktopRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$frontendRoot = Join-Path $desktopRoot "frontend"
$runtimeRoot = [IO.Path]::GetFullPath((Join-Path $desktopRoot "release\runtime\frontend"))
$allowedPrefix = ([IO.Path]::GetFullPath((Join-Path $desktopRoot "release\runtime"))).TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
if (-not $runtimeRoot.StartsWith($allowedPrefix, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to replace frontend output outside release/runtime: $runtimeRoot"
}

Push-Location $frontendRoot
try {
    & pnpm install --frozen-lockfile
    if ($LASTEXITCODE -ne 0) { throw "pnpm install failed" }
    & pnpm build
    if ($LASTEXITCODE -ne 0) { throw "Next.js build failed" }
} finally {
    Pop-Location
}

$standaloneRoot = Join-Path $frontendRoot ".next\standalone"
$staticRoot = Join-Path $frontendRoot ".next\static"
if (-not (Test-Path -LiteralPath (Join-Path $standaloneRoot "server.js"))) {
    throw "Next standalone server.js was not generated"
}
if (Test-Path -LiteralPath $runtimeRoot) {
    Remove-Item -LiteralPath $runtimeRoot -Recurse -Force
}
New-Item -ItemType Directory -Path $runtimeRoot -Force | Out-Null
Get-ChildItem -LiteralPath $standaloneRoot -Force | Copy-Item -Destination $runtimeRoot -Recurse -Force
$runtimeNextRoot = Join-Path $runtimeRoot ".next"
New-Item -ItemType Directory -Path $runtimeNextRoot -Force | Out-Null
Copy-Item -LiteralPath $staticRoot -Destination $runtimeNextRoot -Recurse -Force

$routesManifest = Get-Content -LiteralPath (Join-Path $runtimeNextRoot "routes-manifest.json") -Raw
if ($routesManifest -match "(?i)live2d|voice|artifact|document|suggestions|vote") {
    throw "Forbidden full-version route found in standalone output"
}
Write-Output "Frontend runtime ready: $runtimeRoot"
