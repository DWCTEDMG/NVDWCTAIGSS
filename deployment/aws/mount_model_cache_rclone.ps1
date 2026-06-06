[CmdletBinding()]
param(
  [string]$Remote = "edmg-s3:edmg-model-cache-291685935665-us-east-1/models",
  [string]$MountPoint = "X:",
  [string]$AwsProfile = "nvdwctaigss-riva-sync",
  [string]$Region = "us-east-1",
  [string]$CacheDir = "C:\NVDWCTAIGSS\cache\rclone-vfs",
  [string]$LogFile = "C:\NVDWCTAIGSS\logs\rclone-s3-models.log",
  [string]$MaxCacheSize = "40G"
)

$ErrorActionPreference = "Stop"

function Test-MountReady {
  param([string]$Path)
  return Test-Path -LiteralPath ($Path.TrimEnd("\") + "\")
}

if (-not (Get-Command rclone -ErrorAction SilentlyContinue)) {
  throw "rclone is not installed or not on PATH."
}

$env:AWS_PROFILE = $AwsProfile
$env:AWS_REGION = $Region
$env:AWS_DEFAULT_REGION = $Region

New-Item -ItemType Directory -Force -Path (Split-Path -Parent $LogFile), $CacheDir | Out-Null

if (Test-MountReady $MountPoint) {
  Write-Host "$MountPoint is already mounted."
  exit 0
}

$args = @(
  "mount",
  $Remote,
  $MountPoint,
  "--vfs-cache-mode", "full",
  "--vfs-cache-max-size", $MaxCacheSize,
  "--vfs-cache-max-age", "24h",
  "--cache-dir", $CacheDir,
  "--dir-cache-time", "10m",
  "--poll-interval", "0",
  "--log-file", $LogFile,
  "--log-level", "INFO"
)

Start-Process -FilePath "rclone" -ArgumentList $args -WindowStyle Hidden
Start-Sleep -Seconds 3

if (-not (Test-MountReady $MountPoint)) {
  Write-Host "rclone mount did not become ready. Recent log:"
  if (Test-Path -LiteralPath $LogFile) {
    Get-Content -LiteralPath $LogFile -Tail 40
  }
  throw "Failed to mount $Remote at $MountPoint."
}

Write-Host "Mounted $Remote at $MountPoint."
