param(
  [string]$EnvFile = "deployment/nvidia/.env.local",
  [string]$BackendUrl = "http://127.0.0.1:8000",
  [string]$KitSdkRoot = $env:OMNI_KIT_SDK_ROOT,
  [string]$ProjectId = "",
  [string]$FfmpegPath = "",
  [string]$FfprobePath = "",
  [int]$TimeoutSeconds = 240,
  [double]$RenderWorkerPollSeconds = 2.0,
  [switch]$ReuseExistingBackend,
  [switch]$ReuseExistingKit
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Write-Step {
  param([string]$Message)
  Write-Host "[nvidia-live-smoke] $Message"
}

function Resolve-RepoPath {
  param([string]$PathValue)
  if ([System.IO.Path]::IsPathRooted($PathValue)) {
    return [System.IO.Path]::GetFullPath($PathValue)
  }
  return [System.IO.Path]::GetFullPath((Join-Path $repoRoot.Path $PathValue))
}

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Set-Location $repoRoot

$backendBase = $BackendUrl.TrimEnd("/")
$resolvedEnvFile = Resolve-RepoPath -PathValue $EnvFile
$runtimeScript = Resolve-Path "deployment\nvidia\start_nvidia_runtime_local.ps1"
$smokeScript = Resolve-Path "deployment\nvidia\smoke_nvidia_kit_render_job.ps1"

Write-Step "Preflight: $resolvedEnvFile"
powershell -NoProfile -ExecutionPolicy Bypass -File deployment\nvidia\check_nvidia_profile.ps1 -EnvFile $resolvedEnvFile

$runtimeArgs = @(
  "-NoProfile",
  "-ExecutionPolicy",
  "Bypass",
  "-File",
  $runtimeScript.Path,
  "-EnvFile",
  $resolvedEnvFile,
  "-BackendUrl",
  $backendBase,
  "-RenderWorkerPollSeconds",
  $RenderWorkerPollSeconds
)
if (-not [string]::IsNullOrWhiteSpace($KitSdkRoot)) {
  $runtimeArgs += @("-KitSdkRoot", $KitSdkRoot)
}
if (-not [string]::IsNullOrWhiteSpace($ProjectId)) {
  $runtimeArgs += @("-ProjectId", $ProjectId)
}
if (-not [string]::IsNullOrWhiteSpace($FfmpegPath)) {
  $runtimeArgs += @("-FfmpegPath", $FfmpegPath)
}
if ($ReuseExistingBackend) {
  $runtimeArgs += "-ReuseExistingBackend"
} else {
  $runtimeArgs += "-StopExistingBackend"
}
if (-not $ReuseExistingKit) {
  $runtimeArgs += "-StopExistingKit"
}

Write-Step "Starting NVIDIA runtime"
& powershell @runtimeArgs
if ($LASTEXITCODE -ne 0) {
  throw "Runtime starter failed with exit code $LASTEXITCODE"
}

$smokeArgs = @(
  "-NoProfile",
  "-ExecutionPolicy",
  "Bypass",
  "-File",
  $smokeScript.Path,
  "-BackendUrl",
  $backendBase,
  "-TimeoutSeconds",
  $TimeoutSeconds
)
if (-not [string]::IsNullOrWhiteSpace($ProjectId)) {
  $smokeArgs += @("-ProjectId", $ProjectId)
}
if (-not [string]::IsNullOrWhiteSpace($FfprobePath)) {
  $smokeArgs += @("-FfprobePath", $FfprobePath)
}

Write-Step "Running Kit render-job smoke"
& powershell @smokeArgs
if ($LASTEXITCODE -ne 0) {
  throw "Kit render-job smoke failed with exit code $LASTEXITCODE"
}

Write-Step "Live GPU smoke complete"
