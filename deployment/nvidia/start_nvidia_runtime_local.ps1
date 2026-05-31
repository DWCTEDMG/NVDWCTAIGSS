param(
  [string]$EnvFile = "deployment/nvidia/.env.local",
  [string]$BackendUrl = "http://127.0.0.1:8000",
  [string]$HostName = "127.0.0.1",
  [int]$Port = 8000,
  [string]$KitSdkRoot = $env:OMNI_KIT_SDK_ROOT,
  [string]$ProjectId = "",
  [string]$FfmpegPath = "",
  [double]$RenderWorkerPollSeconds = 5.0,
  [switch]$NoBootstrapBackend,
  [switch]$ReuseExistingBackend,
  [switch]$StopExistingBackend,
  [switch]$SkipKit,
  [switch]$StopExistingKit
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Write-Step {
  param([string]$Message)
  Write-Host "[nvidia-runtime] $Message"
}

function Resolve-KitSdkRoot {
  param([string]$Requested)
  $candidates = @()
  if (-not [string]::IsNullOrWhiteSpace($Requested)) {
    $candidates += $Requested
  }
  $candidates += @(
    "D:\NVIDIA\Omniverse\kit-sdk-110.1.1",
    "C:\NVIDIA\Omniverse\kit-sdk-110.1.1"
  )
  foreach ($root in @("D:\NVIDIA\Omniverse", "C:\NVIDIA\Omniverse")) {
    if (Test-Path -LiteralPath $root) {
      $latest = Get-ChildItem -LiteralPath $root -Directory -Filter "kit-sdk-*" -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1
      if ($latest) {
        $candidates += $latest.FullName
      }
    }
  }
  foreach ($candidate in $candidates) {
    if ([string]::IsNullOrWhiteSpace($candidate)) {
      continue
    }
    $kitExe = Join-Path $candidate "kit.exe"
    if (Test-Path -LiteralPath $kitExe) {
      return (Resolve-Path -LiteralPath $candidate).Path
    }
  }
  throw "No Kit SDK root with kit.exe was found. Pass -KitSdkRoot or set OMNI_KIT_SDK_ROOT."
}

function Resolve-FfmpegPath {
  param([string]$Requested)
  $candidates = @()
  if (-not [string]::IsNullOrWhiteSpace($Requested)) {
    $candidates += $Requested
  }
  if (-not [string]::IsNullOrWhiteSpace($env:EDMG_FFMPEG_PATH)) {
    $candidates += $env:EDMG_FFMPEG_PATH
  }
  $commands = @("ffmpeg", "ffmpeg.exe")
  foreach ($candidate in $candidates) {
    if ([string]::IsNullOrWhiteSpace($candidate)) {
      continue
    }
    if (Test-Path -LiteralPath $candidate) {
      return (Resolve-Path -LiteralPath $candidate).Path
    }
  }
  foreach ($command in $commands) {
    $found = Get-Command $command -ErrorAction SilentlyContinue
    if ($found -and $found.Source) {
      return $found.Source
    }
  }
  throw "FFmpeg was not found. Install FFmpeg, add it to PATH, set EDMG_FFMPEG_PATH, or pass -FfmpegPath."
}

function Wait-BackendHealth {
  param(
    [string]$Url,
    [int]$TimeoutSeconds = 90
  )
  $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
  while ((Get-Date) -lt $deadline) {
    try {
      return Invoke-RestMethod -Uri "$Url/health" -TimeoutSec 3
    } catch {
      Start-Sleep -Seconds 2
    }
  }
  throw "Backend did not become healthy at $Url within $TimeoutSeconds seconds."
}

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Set-Location $repoRoot

$logRoot = Join-Path $env:TEMP "edmg-nvidia-runtime"
New-Item -ItemType Directory -Force -Path $logRoot | Out-Null
$backendStdout = Join-Path $logRoot "backend.stdout.log"
$backendStderr = Join-Path $logRoot "backend.stderr.log"
$kitStdout = Join-Path $logRoot "kit.stdout.log"
$kitStderr = Join-Path $logRoot "kit.stderr.log"

$backendScript = Resolve-Path "deployment\nvidia\start_nvidia_backend_local.ps1"
$backendArgs = @(
  "-NoProfile",
  "-ExecutionPolicy",
  "Bypass",
  "-File",
  $backendScript.Path,
  "-EnvFile",
  $EnvFile,
  "-HostName",
  $HostName,
  "-Port",
  $Port
)
if ($NoBootstrapBackend) {
  $backendArgs += "-NoBootstrap"
}
if ($StopExistingBackend) {
  $backendArgs += "-StopExisting"
} elseif ($ReuseExistingBackend) {
  $backendArgs += "-ReuseExisting"
}

Write-Step "Starting or reusing backend at $BackendUrl"
Remove-Item -LiteralPath $backendStdout,$backendStderr -ErrorAction SilentlyContinue
$backendProcess = Start-Process `
  -WindowStyle Hidden `
  -FilePath powershell `
  -ArgumentList $backendArgs `
  -WorkingDirectory $repoRoot.Path `
  -RedirectStandardOutput $backendStdout `
  -RedirectStandardError $backendStderr `
  -PassThru

$health = Wait-BackendHealth -Url $BackendUrl
Write-Step "Backend health ok: $($health | ConvertTo-Json -Compress)"

try {
  $diagnostics = Invoke-RestMethod -Uri "$BackendUrl/v1/nvidia/diagnostics" -TimeoutSec 20
  $readiness = $diagnostics.nvidia.readiness
  if ($readiness) {
    Write-Step "NVIDIA readiness: $($readiness.level) - $($readiness.summary)"
  }
} catch {
  Write-Step "NVIDIA diagnostics unavailable: $($_.Exception.Message)"
}

if ($backendProcess.HasExited) {
  Write-Step "Backend launcher exited after reuse/bootstrap check. Logs: $backendStdout"
} else {
  Write-Step "Backend process started with launcher PID $($backendProcess.Id). Logs: $backendStdout"
}

if ($SkipKit) {
  Write-Step "Skipping Kit launch because -SkipKit was supplied."
  exit 0
}

$resolvedKitRoot = Resolve-KitSdkRoot -Requested $KitSdkRoot
$resolvedFfmpeg = Resolve-FfmpegPath -Requested $FfmpegPath
$kitScript = Resolve-Path "studio\nvidia-kit\tools\launch_edmg_kit.ps1"

$existingKit = Get-Process kit -ErrorAction SilentlyContinue
if ($existingKit) {
  if ($StopExistingKit) {
    Write-Step "Stopping existing Kit process(es): $($existingKit.Id -join ', ')"
    $existingKit | Stop-Process -Force
    Start-Sleep -Seconds 2
  } else {
    Write-Step "Kit is already running on PID(s): $($existingKit.Id -join ', '). Pass -StopExistingKit to relaunch with current settings."
    exit 0
  }
}

$kitArgs = @(
  "-NoProfile",
  "-ExecutionPolicy",
  "Bypass",
  "-File",
  $kitScript.Path,
  "-KitSdkRoot",
  $resolvedKitRoot,
  "-BackendUrl",
  $BackendUrl,
  "-FfmpegPath",
  $resolvedFfmpeg,
  "-AutoRunRenderWorker",
  "-RenderWorkerPollSeconds",
  $RenderWorkerPollSeconds
)
if (-not [string]::IsNullOrWhiteSpace($ProjectId)) {
  $kitArgs += @("-ProjectId", $ProjectId)
}

Write-Step "Launching Kit SDK: $resolvedKitRoot"
Write-Step "FFmpeg: $resolvedFfmpeg"
if (-not [string]::IsNullOrWhiteSpace($ProjectId)) {
  Write-Step "Project ID: $ProjectId"
}
Remove-Item -LiteralPath $kitStdout,$kitStderr -ErrorAction SilentlyContinue
$kitProcess = Start-Process `
  -WindowStyle Hidden `
  -FilePath powershell `
  -ArgumentList $kitArgs `
  -WorkingDirectory $repoRoot.Path `
  -RedirectStandardOutput $kitStdout `
  -RedirectStandardError $kitStderr `
  -PassThru

Write-Step "Kit launcher PID $($kitProcess.Id). Logs: $kitStdout"
Write-Step "Runtime starter complete."
