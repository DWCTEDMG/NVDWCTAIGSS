param(
  [string]$BackendUrl = "http://127.0.0.1:8000",
  [string]$ScenePlanPath = "studio/nvidia-kit/sample_projects/audio_reactive_stage/scene_plan.json"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Write-Step {
  param([string]$Message)
  Write-Host "[nvidia-smoke] $Message"
}

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Set-Location $repoRoot

$base = $BackendUrl.TrimEnd("/")
Write-Step "Backend: $base"

try {
  $status = Invoke-RestMethod -Method Get -Uri "$base/v1/nvidia/status" -TimeoutSec 15
} catch {
  throw "Unable to reach $base/v1/nvidia/status ($($_.Exception.Message))"
}

try {
  $diagnostics = Invoke-RestMethod -Method Get -Uri "$base/v1/nvidia/diagnostics" -TimeoutSec 20
} catch {
  throw "Unable to reach $base/v1/nvidia/diagnostics ($($_.Exception.Message))"
}

$profile = $status.nvidia
Write-Step "NVIDIA mode: $($profile.enabled)"
Write-Step "NVIDIA profile: $($profile.profile)"
Write-Step "NGC key configured: $($profile.credentials.ngc_api_key_configured)"
Write-Step "NIM configured: $($profile.services.nim.configured)"
Write-Step "Riva configured: $($profile.services.riva.configured)"
Write-Step "Riva ASR enabled: $($profile.services.riva.asr_enabled)"
Write-Step "Omniverse configured: $($profile.services.omniverse.configured)"

$diag = $diagnostics.nvidia
Write-Step "GPU ready: $($diag.host.gpu.ok)"
if ($diag.host.gpu.gpus.Count -gt 0) {
  Write-Step "GPU device: $($diag.host.gpu.gpus[0].name)"
}
Write-Step "Docker NVIDIA runtime: $($diag.host.docker.nvidia_runtime)"
Write-Step "NIM endpoint reachable: $($diag.services.nim.probe.reachable)"
Write-Step "Riva endpoint reachable: $($diag.services.riva.probe.reachable)"
if ($null -ne $diag.readiness) {
  Write-Step "Readiness: $($diag.readiness.level) - $($diag.readiness.summary)"
  Write-Step "Planner ready: $($diag.readiness.planner_ready)"
  Write-Step "Official local stack ready: $($diag.readiness.official_local_ready)"
  if ($diag.readiness.next_actions.Count -gt 0) {
    foreach ($action in @($diag.readiness.next_actions | Select-Object -First 4)) {
      Write-Step "Next action [$($action.id)]: $($action.fix)"
    }
  }
}

if (-not (Test-Path -LiteralPath $ScenePlanPath)) {
  Write-Step "Scene plan not found, skipping USD scene-plan smoke: $ScenePlanPath"
  exit 0
}

$scenePlan = Get-Content -LiteralPath $ScenePlanPath -Raw
try {
  $sceneResponse = Invoke-RestMethod `
    -Method Post `
    -Uri "$base/v1/usd/scene-plan" `
    -ContentType "application/json" `
    -Body $scenePlan `
    -TimeoutSec 15
} catch {
  throw "Scene-plan validation failed ($($_.Exception.Message))"
}

Write-Step "Scene plan ok: $($sceneResponse.ok)"
Write-Step "USD project id: $($sceneResponse.usd_metadata.'edmg:projectId')"
Write-Step "USD scene count: $($sceneResponse.usd_metadata.'edmg:sceneCount')"
Write-Step "USDA preview bytes: $($sceneResponse.usd_stage.text.Length)"
Write-Step "Smoke complete"
