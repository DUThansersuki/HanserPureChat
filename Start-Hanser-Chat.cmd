@echo off
setlocal
cd /d "%~dp0chat_voice_desktop"
set "HANSER_PYTHON_EXE=%CD%\backend\.venv\Scripts\python.exe"
if not exist "%HANSER_PYTHON_EXE%" (
    echo Run chat_voice_desktop\setup.ps1 first.
    pause
    exit /b 1
)
call pnpm start
exit /b %errorlevel%
