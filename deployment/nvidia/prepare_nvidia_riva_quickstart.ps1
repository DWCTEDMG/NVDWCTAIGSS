param(
  [string]$Version = "2.19.0",
  [string]$DestRoot = "starlift-data/nvidia/riva-quickstart",
  [string]$ModelRepository = "starlift-data/nvidia/riva-models",
  [string]$WslDistro = "Ubuntu",
  [switch]$SkipDownload,
  [switch]$RunInit,
  [switch]$RunStart
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Write-Step {
  param([string]$Message)
  Write-Host "[riva-quickstart] $Message"
}

function Resolve-RepoPath {
  param([string]$Path)
  $repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
  if ([System.IO.Path]::IsPathRooted($Path)) {
    return [System.IO.Path]::GetFullPath($Path)
  }
  return [System.IO.Path]::GetFullPath((Join-Path $repoRoot.Path $Path))
}

function ConvertTo-WslPath {
  param([string]$WindowsPath)
  if ($WindowsPath -match "^([A-Za-z]):[\\/](.*)$") {
    $drive = $Matches[1].ToLowerInvariant()
    $rest = $Matches[2] -replace '\\', '/'
    return "/mnt/$drive/$rest"
  }
  $portablePath = $WindowsPath -replace '\\', '/'
  $result = & wsl.exe -d $WslDistro -- wslpath -a $portablePath
  if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($result)) {
    throw "Failed to convert Windows path to WSL path: $WindowsPath"
  }
  return ($result | Select-Object -First 1).Trim()
}

function Quote-Bash {
  param([string]$Value)
  return "'" + ($Value -replace "'", "'`"`"'" ) + "'"
}

function Set-ShAssignment {
  param(
    [string]$Path,
    [string]$Name,
    [string]$Value,
    [switch]$Quoted
  )
  $assignment = if ($Quoted) {
    $escaped = $Value -replace '\\', '\\' -replace '"', '\"'
    "$Name=`"$escaped`""
  } else {
    "$Name=$Value"
  }
  $lines = @(Get-Content -LiteralPath $Path)
  $updated = $false
  $out = foreach ($line in $lines) {
    if ($line -match "^\s*$([regex]::Escape($Name))\s*=") {
      $updated = $true
      $assignment
    } else {
      $line
    }
  }
  if (-not $updated) {
    $out += $assignment
  }
  $out | Set-Content -LiteralPath $Path -Encoding UTF8
}

function Invoke-WslBash {
  param(
    [string]$WorkingDirectory,
    [string]$ScriptName
  )
  $quotedDir = Quote-Bash $WorkingDirectory
  $command = "cd $quotedDir && bash ./$ScriptName"
  Write-Step "Running in WSL ${WslDistro}: $command"
  & wsl.exe -d $WslDistro -- bash -lc $command
  if ($LASTEXITCODE -ne 0) {
    throw "WSL command failed: $ScriptName"
  }
}

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Set-Location $repoRoot

$destRootPath = Resolve-RepoPath $DestRoot
$modelRepoPath = Resolve-RepoPath $ModelRepository
New-Item -ItemType Directory -Force -Path $destRootPath | Out-Null
New-Item -ItemType Directory -Force -Path $modelRepoPath | Out-Null

$ngc = Get-Command ngc -ErrorAction SilentlyContinue
if (-not $ngc) {
  $ngc = Get-Command "C:\Program Files\NVIDIA Corporation\NGCCLI\amd64\ngc.exe" -ErrorAction SilentlyContinue
}
if (-not $ngc) {
  throw "NGC CLI was not found. Install NVIDIA NGC CLI or add ngc.exe to PATH."
}

if (-not $SkipDownload) {
  Write-Step "Downloading nvidia/riva/riva_quickstart:${Version} to $destRootPath"
  & $ngc.Source registry resource download-version "nvidia/riva/riva_quickstart:${Version}" --dest $destRootPath
  if ($LASTEXITCODE -ne 0) {
    throw "NGC quickstart download failed."
  }
} else {
  Write-Step "SkipDownload supplied; reusing $destRootPath"
}

$quickstart = Get-ChildItem -LiteralPath $destRootPath -Directory |
  Where-Object { $_.Name -like "riva_quickstart*" -and $_.Name -like "*$Version*" } |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 1
if (-not $quickstart) {
  throw "Riva quickstart directory for version $Version was not found under $destRootPath."
}

$configPath = Join-Path $quickstart.FullName "config.sh"
if (-not (Test-Path -LiteralPath $configPath)) {
  throw "Riva quickstart config.sh was not found: $configPath"
}

$wslModelRepo = ConvertTo-WslPath $modelRepoPath
Write-Step "Configuring ASR-only Riva quickstart: $configPath"
Set-ShAssignment -Path $configPath -Name "service_enabled_asr" -Value "true"
Set-ShAssignment -Path $configPath -Name "service_enabled_tts" -Value "false"
Set-ShAssignment -Path $configPath -Name "service_enabled_nmt" -Value "false"
Set-ShAssignment -Path $configPath -Name "riva_model_loc" -Value $wslModelRepo -Quoted

$wslQuickstart = ConvertTo-WslPath $quickstart.FullName
Write-Step "Quickstart directory: $($quickstart.FullName)"
Write-Step "WSL quickstart directory: $wslQuickstart"
Write-Step "Model repository: $modelRepoPath"
Write-Step "WSL model repository: $wslModelRepo"

if ($RunInit) {
  Invoke-WslBash -WorkingDirectory $wslQuickstart -ScriptName "riva_init.sh"
} else {
  Write-Step "RunInit not supplied; skipped riva_init.sh."
}

if ($RunStart) {
  Invoke-WslBash -WorkingDirectory $wslQuickstart -ScriptName "riva_start.sh"
} else {
  Write-Step "RunStart not supplied; skipped riva_start.sh."
}

Write-Step "After Riva is running, enable backend ASR with:"
Write-Step "powershell -NoProfile -ExecutionPolicy Bypass -File deployment/nvidia/start_nvidia_optional_service.ps1 -Service riva -ConfigureOnly -EnableRivaAsr"
