@echo off
setlocal
cd /d "%~dp0.runtime\napcat\installer\NapCat.52230.Shell"
if not exist "NapCatWinBootMain.exe" (
    echo NapCat installation is missing. See NAPCAT_NEXT.md.
    pause
    exit /b 1
)
"NapCatWinBootMain.exe"
if errorlevel 1 pause
