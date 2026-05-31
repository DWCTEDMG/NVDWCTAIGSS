param(
  [string]$KitSdkRoot = $env:OMNI_KIT_SDK_ROOT,
  [string]$BackendUrl = "http://127.0.0.1:8000",
  [string]$FfmpegPath = "",
  [string]$ProjectsRoot = "",
  [string]$ProjectId = "",
  [double]$RenderWorkerPollSeconds = 5.0,
  [switch]$AutoRunRenderWorker,
  [switch]$ListExtensions
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$kitRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$appConfig = Resolve-Path (Join-Path $kitRoot "apps\edmg.nvidia.studio.kit")
$extensionsRoot = Resolve-Path (Join-Path $kitRoot "extensions")
$sampleStage = Resolve-Path (Join-Path $kitRoot "sample_projects\audio_reactive_stage\stage.usda")
$sampleScenePlan = Resolve-Path (Join-Path $kitRoot "sample_projects\audio_reactive_stage\scene_plan.json")
if ([string]::IsNullOrWhiteSpace($ProjectsRoot)) {
  $studioRoot = Resolve-Path (Join-Path $kitRoot "..")
  $ProjectsRoot = Join-Path $studioRoot.Path "edmg-studio\python_backend\data\projects"
}
if ([string]::IsNullOrWhiteSpace($FfmpegPath)) {
  $ffmpegCommand = Get-Command ffmpeg -ErrorAction SilentlyContinue
  if ($ffmpegCommand -and $ffmpegCommand.Source) {
    $FfmpegPath = $ffmpegCommand.Source
  } else {
    $FfmpegPath = "ffmpeg"
  }
}

if ([string]::IsNullOrWhiteSpace($KitSdkRoot)) {
  throw "KitSdkRoot was not provided. Pass -KitSdkRoot or set OMNI_KIT_SDK_ROOT."
}

$resolvedSdkRoot = Resolve-Path -LiteralPath $KitSdkRoot
$kitExe = Join-Path $resolvedSdkRoot "kit.exe"
if (-not (Test-Path -LiteralPath $kitExe)) {
  throw "kit.exe was not found at $kitExe"
}

$args = @(
  $appConfig.Path,
  "--ext-folder",
  $extensionsRoot.Path,
  "--/edmg/nvidia/backend_url=$BackendUrl",
  "--/edmg/nvidia/ffmpeg_path=$FfmpegPath",
  "--/edmg/nvidia/projects_root=$ProjectsRoot",
  "--/edmg/nvidia/render_worker_autorun=$([bool]$AutoRunRenderWorker)",
  "--/edmg/nvidia/render_worker_poll_s=$RenderWorkerPollSeconds",
  "--/edmg/nvidia/sample_stage=$($sampleStage.Path)",
  "--/edmg/nvidia/sample_scene_plan=$($sampleScenePlan.Path)"
)

if (-not [string]::IsNullOrWhiteSpace($ProjectId)) {
  $args += "--/edmg/nvidia/project_id=$ProjectId"
}

if ($ListExtensions) {
  $args += "--list-exts"
}

Write-Host "[edmg-kit] SDK: $resolvedSdkRoot"
Write-Host "[edmg-kit] App: $($appConfig.Path)"
Write-Host "[edmg-kit] Backend: $BackendUrl"
Write-Host "[edmg-kit] FFmpeg: $FfmpegPath"
Write-Host "[edmg-kit] Projects: $ProjectsRoot"
if (-not [string]::IsNullOrWhiteSpace($ProjectId)) {
  Write-Host "[edmg-kit] Project ID: $ProjectId"
}
Write-Host "[edmg-kit] Auto-run render worker: $([bool]$AutoRunRenderWorker)"
Write-Host "[edmg-kit] Render worker poll seconds: $RenderWorkerPollSeconds"
Write-Host "[edmg-kit] Sample stage: $($sampleStage.Path)"

& $kitExe @args
