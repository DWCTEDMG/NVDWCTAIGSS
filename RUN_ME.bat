@echo off
setlocal
cd /d %~dp0
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0deployment\nvidia\start_nvidia_runtime_local.ps1" %*
if errorlevel 1 pause
