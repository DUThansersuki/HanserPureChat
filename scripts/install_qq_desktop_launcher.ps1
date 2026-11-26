$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$desktop = [Environment]::GetFolderPath('Desktop')
$launcher = Join-Path $desktop 'Hanser-QQ.cmd'
$content = "@echo off`r`ncall `"$projectRoot\Start-QQ.cmd`"`r`n"
[IO.File]::WriteAllText($launcher, $content, [Text.Encoding]::Default)
$logRoot = Join-Path $projectRoot 'qq_gateway\.runtime\retrieval_logs'
New-Item -ItemType Directory -Path $logRoot -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $projectRoot 'qq_gateway\REPLY_CONTROLS.md') -Destination (Join-Path $logRoot 'README.md') -Force
$wsh = New-Object -ComObject WScript.Shell
$shortcut = $wsh.CreateShortcut((Join-Path $desktop 'Hanser-QQ-Logs.lnk'))
$shortcut.TargetPath = $logRoot
$shortcut.Save()
Write-Output $launcher
