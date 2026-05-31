param(
  [string]$EnvFile = "deployment/nvidia/.env.local",
  [string]$HostName = "127.0.0.1",
  [int]$Port = 8000,
  [string]$PythonExe = ""
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

if ([string]::IsNullOrWhiteSpace($PythonExe)) {
  $candidates = @(
    (Join-Path $repoRoot "studio/edmg-studio/python_backend/venv/Scripts/python.exe"),
    "C:\Program Files\Python310\python.exe",
    "python"
  )
  foreach ($candidate in $candidates) {
    if ($candidate -eq "python" -or (Test-Path -LiteralPath $candidate)) {
      $PythonExe = $candidate
      break
    }
  }
}

$existing = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($null -ne $existing) {
  Write-Step "Port $Port is already listening on PID $($existing.OwningProcess). Reusing that backend."
  Write-Step "Health: http://$HostName`:$Port/health"
  exit 0
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
