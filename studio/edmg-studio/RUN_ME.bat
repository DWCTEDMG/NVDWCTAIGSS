@echo off
setlocal
cd /d %~dp0

if /I "%~1"=="build-inno" goto build_inno
if /I "%~1"=="inno" goto build_inno
if /I "%~1"=="dist-inno" goto build_inno
if /I "%~1"=="build-inno-skip" goto build_inno_skip
if /I "%~1"=="legacy-electron" (
  shift
  goto legacy_launcher
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\..\deployment\nvidia\start_nvidia_runtime_local.ps1" %*
if errorlevel 1 pause
goto :eof

:legacy_launcher
if defined EDMG_STUDIO_PYTHON (
  "%EDMG_STUDIO_PYTHON%" -c "import sys; raise SystemExit(0 if (3, 10) <= sys.version_info[:2] < (3, 14) else 1)" >nul 2>nul
  if %errorlevel%==0 (
    "%EDMG_STUDIO_PYTHON%" tools\launcher_gui.py
    goto :eof
  )
)

where py >nul 2>nul
if %errorlevel%==0 (
  py -3.13 -c "import sys" >nul 2>nul && (py -3.13 tools\launcher_gui.py & goto :eof)
  py -3.12 -c "import sys" >nul 2>nul && (py -3.12 tools\launcher_gui.py & goto :eof)
  py -3.11 -c "import sys" >nul 2>nul && (py -3.11 tools\launcher_gui.py & goto :eof)
  py -3.10 -c "import sys" >nul 2>nul && (py -3.10 tools\launcher_gui.py & goto :eof)
)

where python >nul 2>nul
if %errorlevel%==0 (
  python -c "import sys; raise SystemExit(0 if (3, 10) <= sys.version_info[:2] < (3, 14) else 1)" >nul 2>nul && (python tools\launcher_gui.py & goto :eof)
)

echo Could not find a supported Python interpreter.
echo EDMG Studio requires Python 3.10 - 3.13 for the dev launcher.
echo If you already have one installed, set EDMG_STUDIO_PYTHON to that python.exe and run again.
pause

goto :eof

:build_inno
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0packaging\windows\build_inno_external.ps1"
if errorlevel 1 pause
goto :eof

:build_inno_skip
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0packaging\windows\build_inno_external.ps1" -SkipElectronDirBuild
if errorlevel 1 pause
