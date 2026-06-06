@echo off
setlocal

set "REPO_ROOT=%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%REPO_ROOT%deployment\nvidia\launch_edmg_nvidia_studio.ps1"
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" pause
exit /b %EXIT_CODE%
