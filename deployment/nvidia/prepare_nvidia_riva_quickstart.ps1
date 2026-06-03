param(
  [string]$Version = "2.19.0",
  [string]$DestRoot = "starlift-data/nvidia/riva-quickstart",
  [string]$ModelRepository = "D:/Nvidia/NVDWCTAIGSS/riva-models",
  [string]$EnvFile = "deployment/nvidia/.env.local",
  [string]$WslDistro = "Ubuntu",
  [switch]$SkipDownload,
  [switch]$RunInit,
  [switch]$RunStart
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$script:NgcApiKey = ""

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

function Get-EnvFileValue {
  param(
    [string]$Path,
    [string]$Name
  )
  if (-not (Test-Path -LiteralPath $Path)) {
    return ""
  }
  foreach ($line in Get-Content -LiteralPath $Path) {
    $trimmed = $line.Trim()
    if (-not $trimmed -or $trimmed.StartsWith("#") -or -not $trimmed.Contains("=")) {
      continue
    }
    $key, $value = $trimmed.Split("=", 2)
    if ($key.Trim() -eq $Name) {
      return $value.Trim().Trim('"').Trim("'")
    }
  }
  return ""
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
  $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
  [System.IO.File]::WriteAllText($Path, (($out -join "`n") + "`n"), $utf8NoBom)
}

function Write-DockerWrapper {
  param([string]$QuickstartPath)
  $wrapperDir = Join-Path $QuickstartPath ".wsl-bin"
  New-Item -ItemType Directory -Force -Path $wrapperDir | Out-Null
  $wrapperPath = Join-Path $wrapperDir "docker"
  $content = @(
    "#!/usr/bin/env bash",
    "args=()",
    'for arg in "$@"; do',
    '  case "$arg" in',
    '    -it|-ti|-i|-t|--tty|--interactive) ;;',
    '    *) args+=("$arg") ;;',
    '  esac',
    "done",
    'exec "/mnt/c/Program Files/Docker/Docker/resources/bin/docker.exe" "${args[@]}"'
  )
  [System.IO.File]::WriteAllText($wrapperPath, (($content -join "`n") + "`n"), [System.Text.Encoding]::ASCII)
}

function Sync-RivaRmirFiles {
  param([string]$ModelRepositoryPath)

  $rmirRoot = Join-Path $ModelRepositoryPath "rmir"
  if (-not (Test-Path -LiteralPath $rmirRoot)) {
    return 0
  }

  $rootPath = [System.IO.Path]::GetFullPath((Resolve-Path -LiteralPath $rmirRoot).Path)
  $rmirFiles = @(Get-ChildItem -LiteralPath $rootPath -Recurse -Filter "*.rmir" -File)
  foreach ($file in $rmirFiles) {
    $sourcePath = [System.IO.Path]::GetFullPath($file.FullName)
    if (-not $sourcePath.StartsWith($rootPath, [System.StringComparison]::OrdinalIgnoreCase)) {
      throw "Refusing to copy RMIR outside model repository: $sourcePath"
    }

    $targetPath = [System.IO.Path]::GetFullPath((Join-Path $rootPath $file.Name))
    if (-not $targetPath.StartsWith($rootPath, [System.StringComparison]::OrdinalIgnoreCase)) {
      throw "Refusing to copy RMIR outside model repository: $targetPath"
    }
    if ($sourcePath -ieq $targetPath) {
      continue
    }

    if (Test-Path -LiteralPath $targetPath) {
      $target = Get-Item -LiteralPath $targetPath
      if ($target.Length -eq $file.Length) {
        continue
      }
      throw "RMIR name collision with different size: $targetPath"
    }
    Copy-Item -LiteralPath $sourcePath -Destination $targetPath
  }

  return @(Get-ChildItem -LiteralPath $rootPath -Filter "*.rmir" -File).Count
}

function Invoke-WslBash {
  param(
    [string]$WorkingDirectory,
    [string]$ScriptName
  )
  $quotedDir = Quote-Bash $WorkingDirectory
  $wrapperDir = "$WorkingDirectory/.wsl-bin"
  $quotedWrapperDir = Quote-Bash $wrapperDir
  $command = "chmod +x $quotedWrapperDir/docker && export PATH=`"$wrapperDir`:`$PATH`" && cd $quotedDir && bash ./$ScriptName"
  Write-Step "Running in WSL ${WslDistro}: $command"
  $oldNgcApiKey = $env:NGC_API_KEY
  $oldWslEnv = $env:WSLENV
  try {
    if (-not [string]::IsNullOrWhiteSpace($script:NgcApiKey)) {
      $env:NGC_API_KEY = $script:NgcApiKey
      $wslEntries = @()
      if (-not [string]::IsNullOrWhiteSpace($oldWslEnv)) {
        $wslEntries += $oldWslEnv
      }
      if ($wslEntries -notcontains "NGC_API_KEY/u") {
        $wslEntries += "NGC_API_KEY/u"
      }
      $env:WSLENV = ($wslEntries -join ":")
    }
    & wsl.exe -d $WslDistro -- bash -lc $command
    if ($LASTEXITCODE -ne 0) {
      throw "WSL command failed: $ScriptName"
    }
  } finally {
    if ($null -eq $oldNgcApiKey) {
      Remove-Item Env:\NGC_API_KEY -ErrorAction SilentlyContinue
    } else {
      $env:NGC_API_KEY = $oldNgcApiKey
    }
    if ($null -eq $oldWslEnv) {
      Remove-Item Env:\WSLENV -ErrorAction SilentlyContinue
    } else {
      $env:WSLENV = $oldWslEnv
    }
  }
}

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Set-Location $repoRoot
$envPath = Resolve-RepoPath $EnvFile
$script:NgcApiKey = Get-EnvFileValue -Path $envPath -Name "NGC_API_KEY"
if (-not [string]::IsNullOrWhiteSpace($script:NgcApiKey)) {
  Write-Step "Loaded NGC_API_KEY from $EnvFile for WSL Riva scripts."
}

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
$existingRmirCount = Sync-RivaRmirFiles -ModelRepositoryPath $modelRepoPath
Write-Step "Configuring ASR-only Riva quickstart: $configPath"
Set-ShAssignment -Path $configPath -Name "service_enabled_asr" -Value "true"
Set-ShAssignment -Path $configPath -Name "service_enabled_nlp" -Value "false"
Set-ShAssignment -Path $configPath -Name "service_enabled_tts" -Value "false"
Set-ShAssignment -Path $configPath -Name "service_enabled_nmt" -Value "false"
Set-ShAssignment -Path $configPath -Name "riva_model_loc" -Value $wslModelRepo -Quoted
if ($existingRmirCount -gt 0) {
  Set-ShAssignment -Path $configPath -Name "use_existing_rmirs" -Value "true"
  Write-Step "Found $existingRmirCount local RMIR file(s); Riva init will skip NGC model downloads."
} else {
  Set-ShAssignment -Path $configPath -Name "use_existing_rmirs" -Value "false"
}

$wslQuickstart = ConvertTo-WslPath $quickstart.FullName
Write-DockerWrapper -QuickstartPath $quickstart.FullName
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
