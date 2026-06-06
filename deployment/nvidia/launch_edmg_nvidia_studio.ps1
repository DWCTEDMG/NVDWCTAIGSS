param(
  [string]$KitSdkRoot = $env:OMNI_KIT_SDK_ROOT,
  [string]$BackendUrl = "http://127.0.0.1:8000",
  [int]$BackendPort = 8000,
  [switch]$ListExtensions
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Write-Step {
  param([string]$Message)
  Write-Host "[edmg-nvidia-studio] $Message"
}

function Test-BackendHealth {
  param([string]$Url)
  try {
    $null = Invoke-RestMethod -Uri "$($Url.TrimEnd('/'))/health" -TimeoutSec 2
    return $true
  } catch {
    return $false
  }
}

function Wait-BackendHealth {
  param(
    [string]$Url,
    [int]$TimeoutSeconds = 90
  )
  $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
  while ((Get-Date) -lt $deadline) {
    if (Test-BackendHealth -Url $Url) {
      return $true
    }
    Start-Sleep -Seconds 2
  }
  return $false
}

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$defaultKitSdk = "D:\NVIDIA\Omniverse\kit-sdk-110.1.1"
if ([string]::IsNullOrWhiteSpace($KitSdkRoot)) {
  $KitSdkRoot = $defaultKitSdk
}
if (-not (Test-Path -LiteralPath (Join-Path $KitSdkRoot "kit.exe"))) {
  throw "kit.exe was not found under $KitSdkRoot. Set OMNI_KIT_SDK_ROOT or pass -KitSdkRoot."
}

$logRoot = Join-Path $repoRoot "logs"
New-Item -ItemType Directory -Force -Path $logRoot | Out-Null
$backendLog = Join-Path $logRoot "edmg-nvidia-backend-launch.log"
$backendErr = Join-Path $logRoot "edmg-nvidia-backend-launch.err.log"

if (Test-BackendHealth -Url $BackendUrl) {
  Write-Step "Backend already running: $BackendUrl"
} else {
  $backendScript = Join-Path $repoRoot "deployment\nvidia\start_nvidia_backend_local.ps1"
  if (-not (Test-Path -LiteralPath $backendScript)) {
    throw "Missing backend launcher: $backendScript"
  }
  Write-Step "Starting backend: $BackendUrl"
  Write-Step "Backend log: $backendLog"
  Start-Process `
    -FilePath "powershell" `
    -WindowStyle Hidden `
    -WorkingDirectory $repoRoot `
    -RedirectStandardOutput $backendLog `
    -RedirectStandardError $backendErr `
    -ArgumentList @(
      "-NoProfile",
      "-ExecutionPolicy",
      "Bypass",
      "-File",
      $backendScript,
      "-Port",
      "$BackendPort",
      "-ReuseExisting"
    ) | Out-Null
  if (-not (Wait-BackendHealth -Url $BackendUrl -TimeoutSeconds 90)) {
    throw "Backend did not become healthy at $BackendUrl. See $backendLog and $backendErr."
  }
}

$launchScript = Join-Path $repoRoot "studio\nvidia-kit\tools\launch_edmg_kit.ps1"
if (-not (Test-Path -LiteralPath $launchScript)) {
  throw "Missing Kit launcher: $launchScript"
}

$kitArgs = @(
  "-NoProfile",
  "-ExecutionPolicy",
  "Bypass",
  "-File",
  $launchScript,
  "-KitSdkRoot",
  $KitSdkRoot,
  "-BackendUrl",
  $BackendUrl,
  "-AutoRunRenderWorker"
)
if ($ListExtensions) {
  $kitArgs += "-ListExtensions"
}

Write-Step "Launching Kit: $KitSdkRoot"
& powershell @kitArgs
exit $LASTEXITCODE
