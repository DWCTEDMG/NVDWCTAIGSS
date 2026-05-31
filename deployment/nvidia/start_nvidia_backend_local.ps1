param(
  [string]$EnvFile = "deployment/nvidia/.env.local",
  [string]$HostName = "127.0.0.1",
  [int]$Port = 8000,
  [string]$PythonExe = "",
  [switch]$NoBootstrap,
  [switch]$ReuseExisting,
  [switch]$StopExisting
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Write-Step {
  param([string]$Message)
  Write-Host "[nvidia-backend] $Message"
}

function Import-EnvFile {
  param([string]$Path)
  if (-not (Test-Path -LiteralPath $Path)) {
    Write-Step "Env file not found: $Path. Continuing with current shell environment."
    return
  }
  foreach ($line in Get-Content -LiteralPath $Path) {
    $trimmed = $line.Trim()
    if (-not $trimmed -or $trimmed.StartsWith("#") -or -not $trimmed.Contains("=")) {
      continue
    }
    $name, $value = $trimmed.Split("=", 2)
    $name = $name.Trim()
    $value = $value.Trim().Trim('"').Trim("'")
    if ($name) {
      [Environment]::SetEnvironmentVariable($name, $value, "Process")
    }
  }
}

function Use-HostLoopbackForLocalBackend {
  $names = @(
    "EDMG_AI_OPENAI_COMPAT_BASE_URL",
    "EDMG_NVIDIA_NIM_URL",
    "EDMG_NVIDIA_RIVA_URL",
    "EDMG_RIVA_URL",
    "EDMG_NVIDIA_OMNIVERSE_URL",
    "EDMG_NVIDIA_TRITON_URL",
    "EDMG_NVIDIA_AUDIO2FACE_URL",
    "EDMG_NVIDIA_ACE_URL",
    "EDMG_NVIDIA_COSMOS_URL"
  )
  foreach ($name in $names) {
    $value = [Environment]::GetEnvironmentVariable($name, "Process")
    if ([string]::IsNullOrWhiteSpace($value)) {
      continue
    }
    $normalized = $value.Replace("host.docker.internal", "127.0.0.1")
    if ($normalized -ne $value) {
      [Environment]::SetEnvironmentVariable($name, $normalized, "Process")
    }
  }
}

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Set-Location $repoRoot
$backendRoot = Resolve-Path (Join-Path $repoRoot "studio\edmg-studio\python_backend")
$venvRoot = Join-Path $backendRoot ".venv"
$venvPython = Join-Path $venvRoot "Scripts\python.exe"
$legacyVenvPython = Join-Path $backendRoot "venv\Scripts\python.exe"

function Test-PythonPath {
  param([string]$Candidate)
  return -not [string]::IsNullOrWhiteSpace($Candidate) -and ($Candidate -eq "python" -or (Test-Path -LiteralPath $Candidate))
}

function Test-CompatiblePython {
  param([string]$Candidate)
  if (-not (Test-PythonPath $Candidate)) {
    return $false
  }
  Push-Location $backendRoot
  try {
    & $Candidate -c "import sys; raise SystemExit(0 if (3, 10) <= sys.version_info[:2] < (3, 14) else 1)" 2>$null
    return $LASTEXITCODE -eq 0
  } finally {
    Pop-Location
  }
}

function Test-BackendPythonReady {
  param([string]$Candidate)
  if (-not (Test-CompatiblePython $Candidate)) {
    return $false
  }
  Push-Location $backendRoot
  try {
    & $Candidate -c "import importlib.util; missing=[m for m in ('uvicorn','fastapi','edmg_studio_backend') if importlib.util.find_spec(m) is None]; raise SystemExit(0 if not missing else 1)" 2>$null
    return $LASTEXITCODE -eq 0
  } finally {
    Pop-Location
  }
}

function Find-CompatibleSeedPython {
  $candidates = @(
    "C:\Program Files\Python313\python.exe",
    "C:\Program Files\Python312\python.exe",
    "C:\Program Files\Python311\python.exe",
    "C:\Program Files\Python310\python.exe",
    "python"
  )
  foreach ($candidate in $candidates) {
    if (Test-CompatiblePython $candidate) {
      return $candidate
    }
  }
  return ""
}

function Initialize-BackendVenv {
  param([string]$SeedPython)
  if ([string]::IsNullOrWhiteSpace($SeedPython)) {
    throw "No compatible Python found. Install Python 3.10, 3.11, 3.12, or 3.13 and rerun this script."
  }
  Write-Step "Creating backend virtual environment: $venvRoot"
  & $SeedPython -m venv $venvRoot
  if ($LASTEXITCODE -ne 0) {
    throw "Failed to create backend virtual environment with $SeedPython"
  }
  Write-Step "Installing backend dependencies into .venv"
  & $venvPython -m pip install --upgrade pip
  if ($LASTEXITCODE -ne 0) {
    throw "Failed to upgrade pip in $venvRoot"
  }
  & $venvPython -m pip install -e .
  if ($LASTEXITCODE -ne 0) {
    throw "Failed to install backend package into $venvRoot"
  }
}

function Get-ExistingBackendNvidiaEnabled {
  param([string]$Url)
  try {
    $status = Invoke-RestMethod -Uri "$Url/v1/nvidia/status" -TimeoutSec 3
    return [bool]$status.nvidia.enabled
  } catch {
    return $false
  }
}

$existing = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($null -ne $existing) {
  $existingUrl = "http://$HostName`:$Port"
  $existingNvidiaEnabled = Get-ExistingBackendNvidiaEnabled -Url $existingUrl
  if ($existingNvidiaEnabled -or $ReuseExisting) {
    $modeText = if ($existingNvidiaEnabled) { "NVIDIA-enabled" } else { "non-NVIDIA" }
    Write-Step "Port $Port is already listening on PID $($existing.OwningProcess). Reusing $modeText backend."
    Write-Step "Health: $existingUrl/health"
    exit 0
  }
  if ($StopExisting) {
    Write-Step "Stopping existing non-NVIDIA listener on port $Port, PID $($existing.OwningProcess)."
    Stop-Process -Id $existing.OwningProcess -Force
    Start-Sleep -Seconds 2
  } else {
    throw "Port $Port is already listening on PID $($existing.OwningProcess), but NVIDIA mode is not enabled. Stop that process, rerun with -StopExisting, or pass -ReuseExisting to keep it."
  }
}

if ([string]::IsNullOrWhiteSpace($PythonExe)) {
  $candidates = @(
    $venvPython,
    $legacyVenvPython,
    "python"
  )
  foreach ($candidate in $candidates) {
    if (Test-BackendPythonReady $candidate) {
      $PythonExe = $candidate
      break
    }
  }
  if ([string]::IsNullOrWhiteSpace($PythonExe)) {
    if ($NoBootstrap) {
      throw "No ready backend Python found. Rerun without -NoBootstrap or pass -PythonExe pointing to Python 3.10-3.13 with backend dependencies installed."
    }
    $seedPython = Find-CompatibleSeedPython
    Initialize-BackendVenv -SeedPython $seedPython
    $PythonExe = $venvPython
  }
} elseif (-not (Test-BackendPythonReady $PythonExe)) {
  throw "PythonExe is not ready for the backend. Use Python 3.10-3.13 with uvicorn/fastapi installed, or omit -PythonExe so this script can bootstrap .venv."
}

Import-EnvFile -Path $EnvFile
Use-HostLoopbackForLocalBackend
$mode = if ([string]::IsNullOrWhiteSpace($env:EDMG_NVIDIA_MODE)) { "1" } else { $env:EDMG_NVIDIA_MODE }
$profile = if ([string]::IsNullOrWhiteSpace($env:EDMG_NVIDIA_PROFILE)) { "omniverse" } else { $env:EDMG_NVIDIA_PROFILE }
[Environment]::SetEnvironmentVariable("EDMG_NVIDIA_MODE", $mode, "Process")
[Environment]::SetEnvironmentVariable("EDMG_NVIDIA_PROFILE", $profile, "Process")

Write-Step "Starting backend on http://$HostName`:$Port"
Write-Step "NVIDIA mode: $env:EDMG_NVIDIA_MODE profile: $env:EDMG_NVIDIA_PROFILE"
Write-Step "NIM base URL: $env:EDMG_AI_OPENAI_COMPAT_BASE_URL"
Write-Step "NIM model: $env:EDMG_AI_OPENAI_COMPAT_MODEL"
Write-Step "Riva URI: $env:EDMG_NVIDIA_RIVA_URL"
Write-Step "Riva ASR enabled: $env:EDMG_NVIDIA_RIVA_ASR"
Write-Step "NGC_API_KEY configured: $(-not [string]::IsNullOrWhiteSpace($env:NGC_API_KEY))"
Write-Step "Python: $PythonExe"

Push-Location "studio/edmg-studio/python_backend"
try {
  & $PythonExe -m uvicorn edmg_studio_backend.app:app --host $HostName --port $Port
} finally {
  Pop-Location
}
