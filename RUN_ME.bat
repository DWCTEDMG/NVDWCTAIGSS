@echo off
setlocal
cd /d %~dp0
if /I "%~1"=="legacy-electron" (
  call "%~dp0studio\edmg-studio\RUN_ME.bat" %*
  goto :eof
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0deployment\nvidia\start_nvidia_runtime_local.ps1" %*
if errorlevel 1 pause
