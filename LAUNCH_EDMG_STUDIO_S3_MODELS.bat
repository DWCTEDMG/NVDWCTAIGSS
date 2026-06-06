@echo off
setlocal
cd /d %~dp0

set AWS_PROFILE=nvdwctaigss-riva-sync
set AWS_REGION=us-east-1
set AWS_DEFAULT_REGION=us-east-1
set EDMG_AWS_MODEL_CACHE=1
set EDMG_AWS_MODEL_CACHE_BUCKET=edmg-model-cache-291685935665-us-east-1
set EDMG_AWS_MODEL_CACHE_PREFIX=models
set EDMG_AWS_MODEL_CACHE_STORAGE_CLASS=STANDARD
set EDMG_MODEL_STORAGE_MODE=cloud_only

REM Treat the mounted S3 model prefix as Studio's model root for this launch.
set EDMG_STUDIO_MODELS_DIR=X:\

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0deployment\aws\mount_model_cache_rclone.ps1"
if errorlevel 1 (
  echo Failed to mount S3 model cache.
  pause
  exit /b 1
)

call "%~dp0RUN_ME.bat" %*
exit /b %ERRORLEVEL%
