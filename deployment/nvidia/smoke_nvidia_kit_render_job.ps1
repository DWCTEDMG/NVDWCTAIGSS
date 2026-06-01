param(
  [string]$BackendUrl = "http://127.0.0.1:8000",
  [string]$ProjectsRoot = "studio/edmg-studio/python_backend/data/projects",
  [string]$ProjectId = "",
  [string]$ProjectName = "",
  [int]$Fps = 24,
  [int]$Width = 960,
  [int]$Height = 540,
  [int]$TimeoutSeconds = 180,
  [string]$FfprobePath = "",
  [switch]$SeedProjectIfMissing,
  [string]$SeedProjectName = "EDMG NVIDIA Live Smoke"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Write-Step {
  param([string]$Message)
  Write-Host "[nvidia-kit-smoke] $Message"
}

function Resolve-FfprobePath {
  param([string]$Requested)
  if (-not [string]::IsNullOrWhiteSpace($Requested) -and (Test-Path -LiteralPath $Requested)) {
    return (Resolve-Path -LiteralPath $Requested).Path
  }
  $ffprobe = Get-Command ffprobe -ErrorAction SilentlyContinue
  if ($ffprobe -and $ffprobe.Source) {
    return $ffprobe.Source
  }
  return ""
}

function New-SeededSmokeProject {
  param(
    [string]$BaseUrl,
    [string]$Name
  )
  Write-Step "Creating smoke project: $Name"
  $createBody = @{ name = $Name } | ConvertTo-Json -Compress
  $created = Invoke-RestMethod `
    -Uri "$BaseUrl/v1/projects" `
    -Method Post `
    -ContentType "application/json" `
    -Body $createBody `
    -TimeoutSec 20
  $id = [string]$created.project.id
  if ([string]::IsNullOrWhiteSpace($id)) {
    throw "Backend did not return an id for the smoke project."
  }

  $scenePlan = @{
    project_id = $id
    title = $Name
    duration_s = 12
    bpm = 128
    provider = "local-smoke-seed"
    scenes = @(
      @{
        id = "intro"
        start_s = 0
        end_s = 6
        prompt = "RTX neon stage opening with reactive floor lights"
        camera = "wide push in"
        look = "cyan magenta volumetric haze"
        motion = "four beat pulse"
        usd_variant = "live-smoke-intro"
      },
      @{
        id = "drop"
        start_s = 6
        end_s = 12
        prompt = "Drop section with brighter light columns and reflective set pieces"
        camera = "slow orbit"
        look = "white amber rim light"
        motion = "strobe hits"
        usd_variant = "live-smoke-drop"
      }
    )
  }
  $saveBody = @{
    scene_plan = $scenePlan
    planner = @{
      provider = "local-smoke-seed"
      model = "seeded-production-usda"
      variant_index = 0
      variant_count = 1
    }
  } | ConvertTo-Json -Depth 30 -Compress
  $saved = Invoke-RestMethod `
    -Uri "$BaseUrl/v1/projects/$id/nvidia/scene-plan/save" `
    -Method Post `
    -ContentType "application/json" `
    -Body $saveBody `
    -TimeoutSec 30
  if (-not $saved.ok) {
    throw "Smoke project scene-plan save failed."
  }
  Write-Step "Seeded smoke scene plan: $id"
  return $id
}

function Select-ProjectId {
  param(
    [string]$BaseUrl,
    [string]$RequestedId,
    [string]$RequestedName,
    [bool]$SeedWhenMissing
  )
  if (-not [string]::IsNullOrWhiteSpace($RequestedId)) {
    try {
      Invoke-RestMethod -Uri "$BaseUrl/v1/projects/$RequestedId" -TimeoutSec 15 | Out-Null
      return $RequestedId
    } catch {
      if (-not $SeedWhenMissing) {
        return $RequestedId
      }
      Write-Step "Requested project was not found; seeding a smoke project instead."
      return New-SeededSmokeProject -BaseUrl $BaseUrl -Name $SeedProjectName
    }
  }
  $projectsResponse = Invoke-RestMethod -Uri "$BaseUrl/v1/projects" -TimeoutSec 15
  $projects = @($projectsResponse.projects)
  if ($projects.Count -eq 0) {
    if ($SeedWhenMissing) {
      return New-SeededSmokeProject -BaseUrl $BaseUrl -Name $SeedProjectName
    }
    throw "No Studio projects were returned by $BaseUrl/v1/projects. Pass -SeedProjectIfMissing to create a smoke project."
  }
  if (-not [string]::IsNullOrWhiteSpace($RequestedName)) {
    $needle = $RequestedName.Trim().ToLowerInvariant()
    foreach ($project in $projects) {
      $id = [string]$project.id
      $name = [string]$project.name
      if ($id.ToLowerInvariant() -eq $needle -or $name.ToLowerInvariant() -eq $needle) {
        return $id
      }
    }
    if ($SeedWhenMissing) {
      return New-SeededSmokeProject -BaseUrl $BaseUrl -Name $RequestedName
    }
    throw "No project matched '$RequestedName'."
  }
  return [string]$projects[0].id
}

function Resolve-ProjectPath {
  param(
    [string]$Root,
    [string]$ProjectId,
    [string]$RelativePath
  )
  $projectRoot = Join-Path (Resolve-Path -LiteralPath $Root).Path $ProjectId
  $candidate = Join-Path $projectRoot ($RelativePath -replace "/", "\")
  return [System.IO.Path]::GetFullPath($candidate)
}

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Set-Location $repoRoot

$base = $BackendUrl.TrimEnd("/")
$projectsRootResolved = (Resolve-Path -LiteralPath $ProjectsRoot).Path
$selectedProjectId = Select-ProjectId -BaseUrl $base -RequestedId $ProjectId -RequestedName $ProjectName -SeedWhenMissing ([bool]$SeedProjectIfMissing)
Write-Step "Backend: $base"
Write-Step "Project: $selectedProjectId"
Write-Step "Projects root: $projectsRootResolved"

$health = Invoke-RestMethod -Uri "$base/health" -TimeoutSec 10
Write-Step "Backend health: $($health | ConvertTo-Json -Compress)"

$diagnostics = Invoke-RestMethod -Uri "$base/v1/nvidia/diagnostics" -TimeoutSec 20
if ($diagnostics.nvidia.readiness) {
  Write-Step "NVIDIA readiness: $($diagnostics.nvidia.readiness.level) - $($diagnostics.nvidia.readiness.summary)"
}

$export = Invoke-RestMethod `
  -Uri "$base/v1/projects/$selectedProjectId/nvidia/scene-plan/export" `
  -Method Post `
  -ContentType "application/json" `
  -Body "{}" `
  -TimeoutSec 30
Write-Step "USDA export: $($export.export.path)"

$renderPayload = @{
  fps = $Fps
  width = $Width
  height = $Height
} | ConvertTo-Json -Compress
$job = Invoke-RestMethod `
  -Uri "$base/v1/projects/$selectedProjectId/nvidia/render-job" `
  -Method Post `
  -ContentType "application/json" `
  -Body $renderPayload `
  -TimeoutSec 30
Write-Step "Render job: $($job.export.path)"
Write-Step "Requested movie: $($job.job.outputs.movie)"

$jobPath = Resolve-ProjectPath -Root $projectsRootResolved -ProjectId $selectedProjectId -RelativePath $job.export.path
if (-not (Test-Path -LiteralPath $jobPath)) {
  throw "Render job file was not found: $jobPath"
}

$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
while ((Get-Date) -lt $deadline) {
  $payload = Get-Content -Raw -LiteralPath $jobPath | ConvertFrom-Json
  if ($payload.status -in @("completed", "failed", "canceled")) {
    break
  }
  Start-Sleep -Seconds 5
}

$payload = Get-Content -Raw -LiteralPath $jobPath | ConvertFrom-Json
Write-Step "Final status: $($payload.status)"
if ($payload.status -ne "completed") {
  $message = if ($payload.result) { $payload.result.message } else { "" }
  throw "Render job did not complete: $($payload.status) $message"
}

$movieRel = [string]$payload.result.outputs.movie
if ([string]::IsNullOrWhiteSpace($movieRel)) {
  $movieRel = [string]$job.job.outputs.movie
}
$moviePath = Resolve-ProjectPath -Root $projectsRootResolved -ProjectId $selectedProjectId -RelativePath $movieRel
if (-not (Test-Path -LiteralPath $moviePath)) {
  throw "Movie was reported but not found: $moviePath"
}
Write-Step "Movie: $moviePath"

$ffprobe = Resolve-FfprobePath -Requested $FfprobePath
if ($ffprobe) {
  $probe = & $ffprobe -v error -show_entries format=duration,size -show_streams -of json $moviePath
  $probeJson = $probe | ConvertFrom-Json
  $stream = @($probeJson.streams)[0]
  Write-Step "Movie probe: $($stream.codec_name) $($stream.width)x$($stream.height) $($stream.avg_frame_rate) duration $($probeJson.format.duration)s size $($probeJson.format.size)"
} else {
  Write-Step "ffprobe not found; skipped media metadata validation."
}

Write-Step "Kit render-job smoke complete"
