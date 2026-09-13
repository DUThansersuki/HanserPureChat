param(
    [string]$UnpackedRoot = "",
    [string]$Installer = ""
)

$ErrorActionPreference = "Stop"
$desktopRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
if (-not $UnpackedRoot) {
    $UnpackedRoot = Join-Path $desktopRoot "dist\win-unpacked"
}
if (-not $Installer) {
    $Installer = Join-Path $desktopRoot "dist\HanserPureChatSetup-0.1.0-x64.exe"
}
$UnpackedRoot = [IO.Path]::GetFullPath($UnpackedRoot)
$Installer = [IO.Path]::GetFullPath($Installer)

$requiredFiles = @(
    "Hanser Pure Chat.exe",
    "resources\app.asar",
    "resources\frontend\server.js",
    "resources\frontend\vendor\node_modules\next",
    "resources\frontend\.next\routes-manifest.json",
    "resources\backend\hanser_backend\hanser_backend.exe",
    "resources\database\documents.seed.db",
    "resources\defaults\config.desktop.example.yml",
    "resources\models\hub\models--Qwen--Qwen3-Embedding-0.6B",
    "resources\models\hub\models--Qwen--Qwen3-Reranker-0.6B"
)
foreach ($relativePath in $requiredFiles) {
    $candidate = Join-Path $UnpackedRoot $relativePath
    if (-not (Test-Path -LiteralPath $candidate)) {
        throw "Packaged artifact is missing: $relativePath"
    }
}
if (-not (Test-Path -LiteralPath $Installer -PathType Leaf)) {
    throw "Installer is missing: $Installer"
}

$routesManifest = Join-Path $UnpackedRoot "resources\frontend\.next\routes-manifest.json"
$routes = Get-Content -LiteralPath $routesManifest -Raw | ConvertFrom-Json
$actualRoutes = @($routes.staticRoutes.page) + @($routes.dynamicRoutes.page)
$requiredRoutes = @("/", "/chat/[id]", "/api/chat", "/api/health", "/api/history", "/api/messages")
foreach ($route in $requiredRoutes) {
    if ($actualRoutes -notcontains $route) {
        throw "Required route is missing from packaged frontend: $route"
    }
}
$forbiddenPattern = "(?i)(live2d|l2d|voice|artifact|auth)"
$forbiddenRoutes = @($actualRoutes | Where-Object { $_ -match $forbiddenPattern })
if ($forbiddenRoutes.Count -gt 0) {
    throw "Forbidden routes found: $($forbiddenRoutes -join ', ')"
}
$nextModule = Get-Item -LiteralPath (Join-Path $UnpackedRoot "resources\frontend\vendor\node_modules\next") -Force
if ($nextModule.Attributes -band [IO.FileAttributes]::ReparsePoint) {
    throw "Packaged frontend contains a non-portable Next dependency link."
}

$gitCommit = (& git -C $desktopRoot rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0) { throw "Unable to read git commit" }
$gitStatus = @(& git -C $desktopRoot status --porcelain)
$installerInfo = Get-Item -LiteralPath $Installer
$seedPath = Join-Path $UnpackedRoot "resources\database\documents.seed.db"
$backendPath = Join-Path $UnpackedRoot "resources\backend\hanser_backend\hanser_backend.exe"
$manifest = [ordered]@{
    generated_at = (Get-Date).ToUniversalTime().ToString("o")
    git_commit = $gitCommit
    git_dirty = ($gitStatus.Count -gt 0)
    product = "Hanser Pure Chat"
    version = "0.1.0"
    architecture = "x64"
    installer = [ordered]@{
        file = $installerInfo.Name
        bytes = $installerInfo.Length
        sha256 = (Get-FileHash -LiteralPath $Installer -Algorithm SHA256).Hash.ToLowerInvariant()
    }
    packaged_runtime = [ordered]@{
        backend_bytes = (Get-Item -LiteralPath $backendPath).Length
        seed_database_bytes = (Get-Item -LiteralPath $seedPath).Length
        routes = @($actualRoutes | Sort-Object)
    }
}
$manifestPath = Join-Path $desktopRoot "dist\latest-build-manifest.json"
$manifest | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $manifestPath -Encoding utf8
$manifest | ConvertTo-Json -Depth 6
Write-Host "Package verification passed: $manifestPath"
