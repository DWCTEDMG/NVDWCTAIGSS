param(
  [string]$EnvFile = "deployment/nvidia/.env.local"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Write-Step {
  param([string]$Message)
  Write-Host "[nvidia-profile] $Message"
}

function Test-CommandExists {
  param([string]$Name)
  return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

function Read-EnvFileValue {
  param(
    [string]$Path,
    [string]$Name
  )
  if (-not (Test-Path -LiteralPath $Path)) {
    return ""
  }
  $pattern = "^\s*$([regex]::Escape($Name))\s*=\s*(.*)\s*$"
  foreach ($line in Get-Content -LiteralPath $Path) {
    if ($line -match $pattern) {
      return ($Matches[1].Trim().Trim('"').Trim("'"))
    }
  }
  return ""
}

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Set-Location $repoRoot

Write-Step "Repository: $repoRoot"

if (-not (Test-CommandExists "docker")) {
  throw "Docker was not found on PATH. Install Docker Desktop or add docker.exe to PATH."
}

$dockerVersion = docker --version
Write-Step "Docker: $dockerVersion"

if (Test-CommandExists "ffmpeg") {
  try {
    $ffmpegVersion = (ffmpeg -version 2>$null | Select-Object -First 1)
    Write-Step "FFmpeg: $ffmpegVersion"
  } catch {
    Write-Step "FFmpeg: found, but version check failed ($($_.Exception.Message))"
  }
} else {
  Write-Step "FFmpeg: not found on PATH; install FFmpeg or pass -FfmpegPath to studio/nvidia-kit/tools/launch_edmg_kit.ps1 for MP4 output"
}

if (Test-CommandExists "nvidia-smi") {
  try {
    $gpuLines = @(nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader 2>$null)
    if ($gpuLines.Count -gt 0) {
      foreach ($gpuLine in $gpuLines | Select-Object -First 3) {
        Write-Step "GPU: $gpuLine"
      }
      if ($gpuLines.Count -gt 3) {
        Write-Step "GPU: +$($gpuLines.Count - 3) more"
      }
    } else {
      Write-Step "GPU: nvidia-smi returned no devices"
    }
  } catch {
    Write-Step "GPU: nvidia-smi check failed ($($_.Exception.Message))"
  }
} else {
  Write-Step "GPU: nvidia-smi not found on PATH"
}

try {
  $dockerRuntimes = docker info --format "{{json .Runtimes}}"
  $hasNvidiaRuntime = $dockerRuntimes -match '"nvidia"'
  $runtimeStatus = if ($hasNvidiaRuntime) { "yes" } else { "no" }
  Write-Step "Docker NVIDIA runtime: $runtimeStatus"
  if (-not $hasNvidiaRuntime) {
    Write-Step "Fix: enable Docker Desktop WSL2 integration and NVIDIA Container Toolkit, then run deployment/nvidia/test_docker_gpu.ps1"
  }
} catch {
  Write-Step "Docker NVIDIA runtime: unable to inspect ($($_.Exception.Message))"
}

if (-not (Test-Path -LiteralPath $EnvFile)) {
  Write-Step "No $EnvFile found. Copy deployment/nvidia/.env.example to .env.local before running local NVIDIA services."
  $composeEnvFile = "deployment/nvidia/.env.example"
} else {
  Write-Step "Env file present: $EnvFile"
  $composeEnvFile = $EnvFile
}

$shellNgc = [string]$env:NGC_API_KEY
$fileNgc = Read-EnvFileValue -Path $EnvFile -Name "NGC_API_KEY"
$hasNgc = -not [string]::IsNullOrWhiteSpace($shellNgc) -or -not [string]::IsNullOrWhiteSpace($fileNgc)
$ngcStatus = if ($hasNgc) { "yes" } else { "no" }
Write-Step "NGC_API_KEY configured: $ngcStatus"
if (-not $hasNgc) {
  Write-Step "Fix: create an NGC API key, save it in deployment/nvidia/.env.local, then run deployment/nvidia/login_ngc.ps1"
}

Write-Step "Rendering base Compose config with $composeEnvFile"
docker compose --env-file $composeEnvFile -f docker-compose.starlift.yml -f deployment/nvidia/docker-compose.nvidia.yml config | Out-Null

Write-Step "Rendering optional nvidia-local Compose config with $composeEnvFile"
docker compose --profile nvidia-local --env-file $composeEnvFile -f docker-compose.starlift.yml -f deployment/nvidia/docker-compose.nvidia.yml config | Out-Null

Write-Step "Preflight complete"
