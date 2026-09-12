$ErrorActionPreference = "Stop"

$desktopRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$outputRoot = [IO.Path]::GetFullPath((Join-Path $desktopRoot "build\desktop"))
$allowedPrefix = ([IO.Path]::GetFullPath((Join-Path $desktopRoot "build"))).TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
if (-not $outputRoot.StartsWith($allowedPrefix, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to replace desktop output outside build: $outputRoot"
}
if (Test-Path -LiteralPath $outputRoot) {
    Remove-Item -LiteralPath $outputRoot -Recurse -Force
}

Push-Location $desktopRoot
try {
    & pnpm exec tsc -p tsconfig.desktop.json
    if ($LASTEXITCODE -ne 0) { throw "desktop TypeScript compilation failed" }
} finally {
    Pop-Location
}

Copy-Item -LiteralPath (Join-Path $desktopRoot "desktop\setup.html") -Destination $outputRoot -Force
Copy-Item -LiteralPath (Join-Path $desktopRoot "desktop\status.html") -Destination $outputRoot -Force
Write-Output "Electron main process ready: $outputRoot"
