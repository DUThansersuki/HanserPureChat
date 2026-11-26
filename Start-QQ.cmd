@echo off
setlocal
cd /d "%~dp0qq_gateway"
if not exist ".venv\Scripts\python.exe" (
    echo Run qq_gateway\setup.ps1 first.
    pause
    exit /b 1
)
.venv\Scripts\python.exe -X utf8 -m hanser_qq run
exit /b %errorlevel%
