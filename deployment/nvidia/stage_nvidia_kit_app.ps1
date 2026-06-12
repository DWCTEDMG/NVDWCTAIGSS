param(
  [string]$KitSdkRoot = $env:OMNI_KIT_SDK_ROOT,
  [string]$OutputRoot = "dist/nvidia-workshop-app",
  [string]$BackendUrl = "http://127.0.0.1:8000",
  [string]$ProjectsRoot = "studio/edmg-studio/python_backend/data/projects",
  [string]$ProjectId = "",
  [string]$FfmpegPath = "",
  [double]$RenderWorkerPollSeconds = 5.0,
  [ValidateSet("thin", "internal-fat")]
  [string]$PackageMode = "thin",
  [switch]$IncludeKitSdk,
  [switch]$NoZip,
  [switch]$Clean
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Write-Step {
  param([string]$Message)
  Write-Host "[nvidia-kit-stage] $Message"
}

function Resolve-RepoPath {
  param([string]$PathValue)
  if ([System.IO.Path]::IsPathRooted($PathValue)) {
    return [System.IO.Path]::GetFullPath($PathValue)
  }
  return [System.IO.Path]::GetFullPath((Join-Path $repoRoot.Path $PathValue))
}

function Resolve-OptionalPath {
  param([string]$PathValue)
  if ([string]::IsNullOrWhiteSpace($PathValue)) {
    return ""
  }
  if (Test-Path -LiteralPath $PathValue) {
    return (Resolve-Path -LiteralPath $PathValue).Path
  }
  $command = Get-Command $PathValue -ErrorAction SilentlyContinue
  if ($command -and $command.Source) {
    return $command.Source
  }
  return [System.IO.Path]::GetFullPath($PathValue)
}

function Quote-PowerShellString {
  param([string]$Value)
  return "'" + ($Value -replace "'", "''") + "'"
}

function Copy-DirectoryClean {
  param(
    [string]$Source,
    [string]$Destination
  )
  if (Test-Path -LiteralPath $Destination) {
    Remove-Item -LiteralPath $Destination -Recurse -Force
  }
  New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Destination) | Out-Null
  Copy-Item -LiteralPath $Source -Destination $Destination -Recurse -Force
}

function Remove-PythonCaches {
  param([string]$Root)
  Get-ChildItem -LiteralPath $Root -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue |
    Remove-Item -Recurse -Force
  Get-ChildItem -LiteralPath $Root -Recurse -File -Include "*.pyc","*.pyo" -ErrorAction SilentlyContinue |
    Remove-Item -Force
}

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Set-Location $repoRoot

$sourceKitRoot = Resolve-Path "studio\nvidia-kit"
$outputRootResolved = Resolve-RepoPath -PathValue $OutputRoot
$bundleRoot = Join-Path $outputRootResolved "edmg-nvidia-workshop"
$zipPath = Join-Path $outputRootResolved "edmg-nvidia-workshop.zip"
$resolvedProjectsRoot = Resolve-RepoPath -PathValue $ProjectsRoot
$resolvedKitSdkRoot = Resolve-OptionalPath -PathValue $KitSdkRoot
$resolvedFfmpegPath = Resolve-OptionalPath -PathValue $FfmpegPath
$includeSdkInBundle = $PackageMode -eq "internal-fat" -or [bool]$IncludeKitSdk
if ($IncludeKitSdk -and $PackageMode -ne "internal-fat") {
  Write-Step "Treating -IncludeKitSdk as -PackageMode internal-fat."
  $PackageMode = "internal-fat"
}
$gitCommit = ""
try {
  $gitCommit = (git rev-parse --short HEAD 2>$null).Trim()
} catch {
  $gitCommit = ""
}

if ($Clean -and (Test-Path -LiteralPath $outputRootResolved)) {
  Write-Step "Cleaning $outputRootResolved"
  Remove-Item -LiteralPath $outputRootResolved -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $bundleRoot | Out-Null

Write-Step "Staging Kit app source from $($sourceKitRoot.Path)"
Copy-DirectoryClean -Source (Join-Path $sourceKitRoot.Path "apps") -Destination (Join-Path $bundleRoot "studio\nvidia-kit\apps")
Copy-DirectoryClean -Source (Join-Path $sourceKitRoot.Path "extensions") -Destination (Join-Path $bundleRoot "studio\nvidia-kit\extensions")
Copy-DirectoryClean -Source (Join-Path $sourceKitRoot.Path "sample_projects") -Destination (Join-Path $bundleRoot "studio\nvidia-kit\sample_projects")
Copy-DirectoryClean -Source (Join-Path $sourceKitRoot.Path "tools") -Destination (Join-Path $bundleRoot "studio\nvidia-kit\tools")
Copy-Item -LiteralPath (Join-Path $sourceKitRoot.Path "README.md") -Destination (Join-Path $bundleRoot "studio\nvidia-kit\README.md") -Force
Remove-PythonCaches -Root (Join-Path $bundleRoot "studio\nvidia-kit")

$deploymentDest = Join-Path $bundleRoot "deployment\nvidia"
New-Item -ItemType Directory -Force -Path $deploymentDest | Out-Null
foreach ($script in @(
  "start_nvidia_backend_local.ps1",
  "start_nvidia_runtime_local.ps1",
  "start_nvidia_optional_service.ps1",
  "stage_nvidia_kit_app.ps1",
  "smoke_nvidia_backend.ps1",
  "smoke_nvidia_kit_render_job.ps1",
  "check_nvidia_profile.ps1",
  "test_docker_gpu.ps1",
  "login_ngc.ps1"
)) {
  Copy-Item -LiteralPath (Join-Path "deployment\nvidia" $script) -Destination (Join-Path $deploymentDest $script) -Force
}
Copy-Item -LiteralPath "deployment\nvidia\.env.example" -Destination (Join-Path $deploymentDest ".env.example") -Force
Copy-Item -LiteralPath "deployment\nvidia\README.md" -Destination (Join-Path $deploymentDest "README.md") -Force

if ($includeSdkInBundle) {
  if ([string]::IsNullOrWhiteSpace($resolvedKitSdkRoot) -or -not (Test-Path -LiteralPath (Join-Path $resolvedKitSdkRoot "kit.exe"))) {
    throw "PackageMode internal-fat was requested, but no Kit SDK root containing kit.exe was resolved."
  }
  Write-Step "Including Kit SDK from $resolvedKitSdkRoot"
  Copy-DirectoryClean -Source $resolvedKitSdkRoot -Destination (Join-Path $bundleRoot "kit-sdk")
} else {
  Write-Step "Package mode thin: not copying Kit SDK into the bundle."
}

$defaultKitRoot = if ($includeSdkInBundle) { "__BUNDLED_KIT_SDK__" } else { $resolvedKitSdkRoot }
$launchLines = @(
  "param(",
  "  [string]`$KitSdkRoot = $(Quote-PowerShellString -Value $defaultKitRoot),",
  "  [string]`$BackendUrl = $(Quote-PowerShellString -Value $BackendUrl),",
  "  [string]`$ProjectsRoot = $(Quote-PowerShellString -Value $resolvedProjectsRoot),",
  "  [string]`$ProjectId = $(Quote-PowerShellString -Value $ProjectId),",
  "  [string]`$FfmpegPath = $(Quote-PowerShellString -Value $resolvedFfmpegPath),",
  "  [double]`$RenderWorkerPollSeconds = $RenderWorkerPollSeconds,",
  "  [switch]`$ListExtensions",
  ")",
  "",
  "Set-StrictMode -Version Latest",
  "`$ErrorActionPreference = `"Stop`"",
  "`$bundleRoot = Split-Path -Parent `$MyInvocation.MyCommand.Path",
  "if (`$KitSdkRoot -eq '__BUNDLED_KIT_SDK__') { `$KitSdkRoot = Join-Path `$bundleRoot 'kit-sdk' }",
  "`$launchScript = Join-Path `$bundleRoot 'studio\nvidia-kit\tools\launch_edmg_kit.ps1'",
  "if (-not (Test-Path -LiteralPath `$launchScript)) { throw `"Missing Kit launcher: `$launchScript`" }",
  "`$args = @('-NoProfile','-ExecutionPolicy','Bypass','-File',`$launchScript,'-KitSdkRoot',`$KitSdkRoot,'-BackendUrl',`$BackendUrl,'-ProjectsRoot',`$ProjectsRoot,'-RenderWorkerPollSeconds',`$RenderWorkerPollSeconds)",
  "if (-not [string]::IsNullOrWhiteSpace(`$FfmpegPath)) { `$args += @('-FfmpegPath', `$FfmpegPath) }",
  "if (-not [string]::IsNullOrWhiteSpace(`$ProjectId)) { `$args += @('-ProjectId', `$ProjectId) }",
  "if (`$ListExtensions) { `$args += '-ListExtensions' } else { `$args += '-AutoRunRenderWorker' }",
  "& powershell @args",
  "exit `$LASTEXITCODE"
)
$launchPath = Join-Path $bundleRoot "Start-EDMG-NVIDIA-Workshop.ps1"
$launchLines | Set-Content -LiteralPath $launchPath -Encoding UTF8

$manifest = [ordered]@{
  name = "edmg-nvidia-workshop"
  created_at = (Get-Date).ToString("s")
  git_commit = $gitCommit
  repo_root = $repoRoot.Path
  backend_url = $BackendUrl
  projects_root = $resolvedProjectsRoot
  kit_sdk_root = $resolvedKitSdkRoot
  kit_sdk_included = [bool]$includeSdkInBundle
  package_mode = $PackageMode
  kit_sdk_policy = if ($includeSdkInBundle) {
    "internal-fat: SDK copied into bundle for internal/licensed use only"
  } else {
    "thin: SDK is not redistributed; launcher resolves an installed SDK path"
  }
  ffmpeg_path = $resolvedFfmpegPath
  project_id = $ProjectId
  render_worker_poll_seconds = $RenderWorkerPollSeconds
  launcher = "Start-EDMG-NVIDIA-Workshop.ps1"
  contents = @(
    "studio/nvidia-kit/apps",
    "studio/nvidia-kit/extensions",
    "studio/nvidia-kit/sample_projects",
    "studio/nvidia-kit/tools",
    "deployment/nvidia"
  )
}
$manifestPath = Join-Path $bundleRoot "bundle-manifest.json"
$manifest | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $manifestPath -Encoding UTF8

$bundleReadme = @"
# EDMG NVIDIA Workshop Bundle

Launch the staged Kit app with:

````powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\Start-EDMG-NVIDIA-Workshop.ps1
````

Use `-ListExtensions` for a fast Kit extension resolution check. Default bundles
are thin: they do not include the Kit SDK and launch against the SDK path
recorded in `bundle-manifest.json` or a path you pass with `-KitSdkRoot`.
Internal fat bundles can be created with `-PackageMode internal-fat` only after
confirming the selected Kit SDK can be copied for the intended audience.
"@
$bundleReadme | Set-Content -LiteralPath (Join-Path $bundleRoot "README.md") -Encoding UTF8

if (-not $NoZip) {
  if (Test-Path -LiteralPath $zipPath) {
    Remove-Item -LiteralPath $zipPath -Force
  }
  Write-Step "Writing zip $zipPath"
  Compress-Archive -LiteralPath $bundleRoot -DestinationPath $zipPath -Force
}

Write-Step "Bundle staged: $bundleRoot"
if (-not $NoZip) {
  Write-Step "Bundle zip: $zipPath"
}
