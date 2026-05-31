param(
  [string]$BackendUrl = "http://127.0.0.1:8000",
  [string]$ProjectsRoot = "studio/edmg-studio/python_backend/data/projects",
  [string]$ProjectId = "",
  [string]$ProjectName = "",
  [int]$Fps = 24,
  [int]$Width = 960,
  [int]$Height = 540,
  [int]$TimeoutSeconds = 180,
  [string]$FfprobePath = ""
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

function Select-ProjectId {
  param(
    [string]$BaseUrl,
    [string]$RequestedId,
    [string]$RequestedName
  )
  if (-not [string]::IsNullOrWhiteSpace($RequestedId)) {
    return $RequestedId
  }
  $projectsResponse = Invoke-RestMethod -Uri "$BaseUrl/v1/projects" -TimeoutSec 15
  $projects = @($projectsResponse.projects)
  if ($projects.Count -eq 0) {
    throw "No Studio projects were returned by $BaseUrl/v1/projects."
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
$selectedProjectId = Select-ProjectId -BaseUrl $base -RequestedId $ProjectId -RequestedName $ProjectName
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
