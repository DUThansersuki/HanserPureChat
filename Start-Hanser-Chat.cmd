@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_hanser_chat.ps1"
if errorlevel 1 (
  echo.
  echo Hanser Chat failed to start. See .runtime logs for details.
  pause
)
