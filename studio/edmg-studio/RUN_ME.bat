@echo off
setlocal
cd /d %~dp0

if /I "%~1"=="build-inno" goto build_inno
if /I "%~1"=="inno" goto build_inno
if /I "%~1"=="dist-inno" goto build_inno
if /I "%~1"=="build-inno-skip" goto build_inno_skip

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\..\deployment\nvidia\start_nvidia_runtime_local.ps1" %*
if errorlevel 1 pause
goto :eof

:build_inno
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0packaging\windows\build_inno_external.ps1"
if errorlevel 1 pause
goto :eof

:build_inno_skip
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0packaging\windows\build_inno_external.ps1" -SkipElectronDirBuild
if errorlevel 1 pause
