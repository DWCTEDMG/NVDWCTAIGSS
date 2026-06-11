param(
  [string]$ProjectId = "",
  [string]$Zone = "us-central1-a",
  [string]$InstanceName = "edmg-gpu-studio",
  [string]$RepoBranch = "codex/Unified",
  [string]$RepoUrl = "https://github.com/HIMOI890/DWCTGenerativeSoundStudio.git",
  [string]$RemoteScriptLocalPath = "",
  [string]$BackendPort = "7863",
  [string]$UiPort = "5173",
  [switch]$QueueDefaultModels,
  [switch]$SkipUi
)

$ErrorActionPreference = "Stop"

function Write-Step([string]$Message) {
  Write-Host "[edmg-gcp] $Message" -ForegroundColor Cyan
}

function Invoke-Checked([string]$FilePath, [string[]]$Arguments) {
  & $FilePath @Arguments
  if ($LASTEXITCODE -ne 0) {
    throw "Command failed with exit code ${LASTEXITCODE}: $FilePath $($Arguments -join ' ')"
  }
}

function Quote-Posix([string]$Value) {
  return "'" + ($Value -replace "'", "'""'""'") + "'"
}

function Find-StudioDir([string]$ExplicitPath) {
  if ($ExplicitPath) {
    return (Resolve-Path -LiteralPath $ExplicitPath -ErrorAction Stop).Path
  }

  $current = (Get-Location).Path
  $candidate = $current
  while ($candidate) {
    if ((Test-Path (Join-Path $candidate "package.json")) -and (Test-Path (Join-Path $candidate "src\components\api.ts"))) {
      return $candidate
    }
    $child = Join-Path $candidate "studio\edmg-studio"
    if ((Test-Path (Join-Path $child "package.json")) -and (Test-Path (Join-Path $child "src\components\api.ts"))) {
      return $child
    }
    $parent = Split-Path -Parent $candidate
    if ($parent -eq $candidate -or -not $parent) { break }
    $candidate = $parent
  }

  throw "Could not find studio/edmg-studio. Run this from the repo or pass -RemoteScriptLocalPath explicitly."
}

if (-not (Get-Command gcloud -ErrorAction SilentlyContinue)) {
  throw "gcloud is not installed or not on PATH."
}

$studioDir = Find-StudioDir ""
if (-not $RemoteScriptLocalPath) {
  $RemoteScriptLocalPath = Join-Path $studioDir "edmg_gcp_gpu_bootstrap.sh"
}

$resolvedRemoteScript = (Resolve-Path -LiteralPath $RemoteScriptLocalPath -ErrorAction Stop).Path

if ($ProjectId) {
  Write-Step "Setting gcloud project to $ProjectId"
  Invoke-Checked "gcloud" @("config", "set", "project", $ProjectId)
  $CurrentProject = $ProjectId
} else {
  $CurrentProject = (& gcloud config get-value project 2>$null).Trim()
}

if (-not $CurrentProject -or $CurrentProject -eq "(unset)") {
  throw "No active gcloud project. Pass -ProjectId or run `gcloud config set project YOUR_GCP_PROJECT_ID` first."
}

$startUiFlag = if ($SkipUi) { "0" } else { "1" }
$queueModelsFlag = if ($QueueDefaultModels) { "1" } else { "0" }

Write-Step "Uploading bootstrap script to $InstanceName in $Zone"
Invoke-Checked "gcloud" @(
  "compute", "scp",
  "--project", $CurrentProject,
  "--zone", $Zone,
  $resolvedRemoteScript,
  "${InstanceName}:~/edmg_gcp_gpu_bootstrap.sh"
)

$remoteEnv = @(
  "REPO_BRANCH=$(Quote-Posix $RepoBranch)",
  "REPO_URL=$(Quote-Posix $RepoUrl)",
  "BACKEND_PORT=$(Quote-Posix $BackendPort)",
  "UI_PORT=$(Quote-Posix $UiPort)",
  "QUEUE_DEFAULT_MODELS=$(Quote-Posix $queueModelsFlag)",
  "START_UI=$(Quote-Posix $startUiFlag)"
) -join " "

$remoteCommand = "$remoteEnv chmod +x ~/edmg_gcp_gpu_bootstrap.sh && $remoteEnv bash ~/edmg_gcp_gpu_bootstrap.sh"

Write-Step "Running remote bootstrap. This will install the backend, UI dependencies, and helper scripts on the VM."
Invoke-Checked "gcloud" @(
  "compute", "ssh",
  "--project", $CurrentProject,
  "--zone", $Zone,
  $InstanceName,
  "--command", $remoteCommand
)

$PublicIp = (& gcloud compute instances describe $InstanceName --project $CurrentProject --zone $Zone --format "value(networkInterfaces[0].accessConfigs[0].natIP)").Trim()
$BackendUrl = if ($PublicIp) { "http://${PublicIp}:$BackendPort" } else { "" }
$UiUrl = if ($PublicIp -and -not $SkipUi) { "http://${PublicIp}:$UiPort/?backendUrl=$BackendUrl" } else { "" }

Write-Host ""
Write-Host "Remote bootstrap complete." -ForegroundColor Green
if ($BackendUrl) {
  Write-Host "Backend:  $BackendUrl"
  Write-Host "Health:   $BackendUrl/health"
}
if ($UiUrl) {
  Write-Host "Frontend: $UiUrl"
}
Write-Host ""
Write-Host "Next step for the local Studio app:" -ForegroundColor Green
if ($BackendUrl) {
  Write-Host "  .\set_studio_gcp_backend.ps1 -BackendUrl $BackendUrl"
} else {
  Write-Host "  Resolve the VM external IP, then run .\set_studio_gcp_backend.ps1 -BackendUrl http://YOUR_VM_IP:$BackendPort"
}
