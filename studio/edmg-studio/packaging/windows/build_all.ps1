param(
  [string]$PythonExe = "python",
  [string[]]$PythonArgs = @(),
  [string]$NodeExe = "node",
  [string]$PnpmExe = "pnpm"
)

$ErrorActionPreference = "Stop"
$SupportedPythonMin = [Version]"3.10"
$SupportedPythonMaxExclusive = [Version]"3.14"
$BackendSetuptoolsConstraint = "setuptools==70.0.0"

function Assert-Command($name) {
  if (-not (Get-Command $name -ErrorAction SilentlyContinue)) {
    throw "Missing required command: $name"
  }
}

function Get-PythonVersion($PythonCommand, [string[]]$ExtraArgs = @()) {
  $versionOutput = & $PythonCommand @ExtraArgs -c "import sys; print('.'.join(map(str, sys.version_info[:3])))" 2>$null
  if ($LASTEXITCODE -ne 0) {
    throw "Failed to query Python version using: $PythonCommand"
  }
  try {
    return [Version]($versionOutput.Trim())
  } catch {
    throw "Could not parse Python version output '$versionOutput' from $PythonCommand"
  }
}

function Assert-SupportedPythonVersion($PythonCommand, [string[]]$ExtraArgs = @(), [string]$Label = "Python") {
  $version = Get-PythonVersion $PythonCommand $ExtraArgs
  if ($version -lt $SupportedPythonMin -or $version -ge $SupportedPythonMaxExclusive) {
    throw ($Label + " " + $version + " is unsupported for Studio release builds. Use Python >= " + $SupportedPythonMin + " and < " + $SupportedPythonMaxExclusive + ".")
  }
  Write-Host ("[info] " + $Label + " version OK: " + $version) -ForegroundColor Cyan
  return $version
}

function Test-SupportedPythonVersion($PythonCommand, [string[]]$ExtraArgs = @()) {
  try {
    $version = Get-PythonVersion $PythonCommand $ExtraArgs
    return ($version -ge $SupportedPythonMin -and $version -lt $SupportedPythonMaxExclusive)
  } catch {
    return $false
  }
}

function Format-PythonCommand($PythonCommand, [string[]]$ExtraArgs = @()) {
  $parts = @($PythonCommand) + $ExtraArgs
  return ($parts -join " ")
}

function Resolve-PythonBootstrapCommand() {
  $candidates = @()

  if ($env:EDMG_STUDIO_PYTHON) {
    $candidates += [pscustomobject]@{ Command = $env:EDMG_STUDIO_PYTHON; Args = @(); Label = "EDMG_STUDIO_PYTHON" }
  }

  if ($PSBoundParameters.ContainsKey("PythonExe") -or $PSBoundParameters.ContainsKey("PythonArgs")) {
    $candidates += [pscustomobject]@{ Command = $PythonExe; Args = $PythonArgs; Label = "PythonExe" }
  } else {
    $candidates += [pscustomobject]@{ Command = "python"; Args = @(); Label = "python" }
    if ($IsWindows -or $env:OS -eq "Windows_NT") {
      foreach ($minor in @("3.13", "3.12", "3.11", "3.10")) {
        $candidates += [pscustomobject]@{ Command = "py"; Args = @("-$minor"); Label = "py -$minor" }
      }
      $candidates += [pscustomobject]@{ Command = "py"; Args = @("-3"); Label = "py -3" }
    }
  }

  foreach ($candidate in $candidates) {
    if (-not (Get-Command $candidate.Command -ErrorAction SilentlyContinue)) {
      continue
    }
    if (Test-SupportedPythonVersion $candidate.Command $candidate.Args) {
      $version = Get-PythonVersion $candidate.Command $candidate.Args
      Write-Host ("[info] Selected bootstrap Python: " + (Format-PythonCommand $candidate.Command $candidate.Args) + " (" + $version + ")") -ForegroundColor Cyan
      return $candidate
    }
  }

  throw ("Could not find a supported Python command. Set EDMG_STUDIO_PYTHON or install Python >= " + $SupportedPythonMin + " and < " + $SupportedPythonMaxExclusive + ".")
}

function Invoke-Checked($label, [scriptblock]$action) {
  & $action
  if ($LASTEXITCODE -ne 0) {
    throw ($label + " failed with exit code " + $LASTEXITCODE)
  }
}

function Resolve-BackendPackageDir($PyBackendDir) {
  $candidates = @(
    (Join-Path $PyBackendDir "edmg_studio_backend"),
    (Join-Path $PyBackendDir "src\edmg_studio_backend")
  )

  foreach ($candidate in $candidates) {
    if (Test-Path $candidate) {
      return $candidate
    }
  }

  $checked = $candidates -join ", "
  throw "Backend package folder not found. Checked: $checked"
}

function Get-BundledFfmpegPath($StudioDir) {
  return Join-Path $StudioDir "electron-resources\bin\ffmpeg.exe"
}

function Ensure-BundledFfmpeg($StudioDir) {
  $bundled = Get-BundledFfmpegPath $StudioDir
  if (Test-Path $bundled) {
    Write-Host ("[info] Bundled FFmpeg ready: " + $bundled) -ForegroundColor Cyan
    return $bundled
  }

  $script = Join-Path $StudioDir "packaging\windows\get_ffmpeg.ps1"
  if (-not (Test-Path $script)) {
    throw "Missing FFmpeg staging script: $script"
  }

  Write-Host "[info] Bundled FFmpeg missing; downloading/staging it now..." -ForegroundColor Yellow
  Invoke-Checked "stage bundled FFmpeg" {
    & powershell -NoProfile -ExecutionPolicy Bypass -File $script -OutDir "./electron-resources/bin"
  }

  if (-not (Test-Path $bundled)) {
    throw "Bundled FFmpeg staging failed: $bundled"
  }

  Write-Host ("[info] Bundled FFmpeg staged: " + $bundled) -ForegroundColor Green
  return $bundled
}

function Check-Port($port, $label) {
  Write-Host ("Port " + $port + " (" + $label + "):") -NoNewline
  $found = $false

  if (Get-Command Get-NetTCPConnection -ErrorAction SilentlyContinue) {
    try {
      $conns = Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue
      if ($conns) {
        Write-Host " LISTENING" -ForegroundColor Yellow
        foreach ($c in $conns) {
          $pid = $c.OwningProcess
          $pname = ""
          try { $pname = (Get-Process -Id $pid -ErrorAction SilentlyContinue).ProcessName } catch {}
          Write-Host ("  " + $c.LocalAddress + ":" + $c.LocalPort + "  pid=" + $pid + "  " + $pname)
        }
        $found = $true
      }
    } catch {}
  }

  if (-not $found) {
    try {
      $lines = & netstat -ano | Select-String (":$port\s")
      if ($lines) {
        Write-Host " IN USE" -ForegroundColor Yellow
        foreach ($l in $lines) {
          $parts = ($l.ToString() -split "\s+") | Where-Object { $_ -ne "" }
          $pid = $parts[-1]
          $pname = ""
          try { $pname = (Get-Process -Id $pid -ErrorAction SilentlyContinue).ProcessName } catch {}
          Write-Host ("  " + $l.ToString().Trim() + "  proc=" + $pname)
        }
        $found = $true
      }
    } catch {}
  }

  if (-not $found) {
    Write-Host " free" -ForegroundColor Green
  }
}

function Doctor($RepoRoot, $StudioDir, $PyBackendDir, $BackendPkgDir, $BundledFfmpegPath) {
  Write-Host "== Preflight Doctor ==" -ForegroundColor Cyan
  $repoPath = $RepoRoot.Path
  Write-Host ("RepoRoot: " + $repoPath)
  Write-Host ("Path length: " + $repoPath.Length)
  if ($repoPath.Length -gt 160) {
    Write-Host "[warn] Repo path is long. Consider moving to C:\EDMG\ to avoid Windows path issues." -ForegroundColor Yellow
  }

  try {
    $pyv = & $PythonExe @PythonArgs -c "import sys; print(sys.version)"
    Write-Host ("Python: " + $pyv.Trim())
  } catch {
    Write-Host "[fail] Python not runnable." -ForegroundColor Red
  }

  try {
    $pipv = & $PythonExe @PythonArgs -m pip --version
    Write-Host ("pip: " + $pipv.Trim())
  } catch {}

  try {
    $nv = & $NodeExe --version
    Write-Host ("Node: " + $nv.Trim())
  } catch {
    Write-Host "[warn] node not runnable (UI build will fail)." -ForegroundColor Yellow
  }

  try {
    $pnpmv = & $PnpmExe --version
    Write-Host ("pnpm: " + $pnpmv.Trim())
  } catch {}

  $ff = $env:EDMG_FFMPEG_PATH
  if (-not $ff -and (Test-Path $BundledFfmpegPath)) {
    $ff = $BundledFfmpegPath
  }
  if (-not $ff) { $ff = "ffmpeg" }
  try {
    $ffv = & $ff -version
    Write-Host ("FFmpeg: " + ($ffv | Select-Object -First 1))
  } catch {
    Write-Host "[warn] FFmpeg not found. Internal rendering will rely on PATH or a bundled binary." -ForegroundColor Yellow
  }

  try {
    $driveLetter = $repoPath.Substring(0,1)
    $drive = Get-PSDrive -Name $driveLetter
    $gb = [math]::Round($drive.Free / 1GB, 2)
    Write-Host ("Disk free on " + $driveLetter + ": " + $gb + " GB")
    if ($gb -lt 20) {
      Write-Host "[warn] Low disk space. Video renders + node_modules can be large." -ForegroundColor Yellow
    }
  } catch {}

  Write-Host "== Port checks ==" -ForegroundColor Cyan
  Check-Port 7863 "Studio backend"
  Check-Port 8188 "ComfyUI"
  Check-Port 8001 "NVIDIA NIM"
  Check-Port 50051 "NVIDIA Riva"
  Write-Host "================" -ForegroundColor Cyan
  Write-Host ("Backend package: " + $BackendPkgDir) -ForegroundColor Cyan
}

function Move-ExistingFolder($SourceDir, $DestRoot, $Label) {
  if (-not (Test-Path $SourceDir)) {
    return
  }

  New-Item -ItemType Directory -Force -Path $DestRoot | Out-Null
  $ts = Get-Date -Format "yyyyMMdd_HHmmss"
  $backup = Join-Path $DestRoot ($Label + "_" + $ts)
  Move-Item -Force $SourceDir $backup
  Write-Host ("[info] Moved " + $SourceDir + " -> " + $backup) -ForegroundColor Yellow
}

function Test-ReparsePoint($Path) {
  if (-not (Test-Path $Path)) {
    return $false
  }
  $item = Get-Item -Force $Path
  return (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)
}

function Migrate-LegacyData($RepoRoot, $StudioDir, $PyBackendDir) {
  $DestData = Join-Path $StudioDir "data"
  $MigrationsDir = Join-Path $StudioDir "_legacy_migrations"
  New-Item -ItemType Directory -Force -Path $DestData | Out-Null
  New-Item -ItemType Directory -Force -Path $MigrationsDir | Out-Null

  $LegacyBackendData = Join-Path $PyBackendDir "data"
  if (Test-Path $LegacyBackendData) {
    Write-Host "[info] Found legacy python_backend/data. Migrating into studio/data." -ForegroundColor Yellow
    Copy-Item -Recurse -Force (Join-Path $LegacyBackendData "*") $DestData -ErrorAction SilentlyContinue
    Move-ExistingFolder $LegacyBackendData $MigrationsDir "python_backend_data"
  }

  $LegacyRootData = Join-Path $RepoRoot "data"
  if (Test-Path $LegacyRootData) {
    if (Test-ReparsePoint $LegacyRootData) {
      Write-Host "[info] Repo-root data/ is already a link; leaving it in place." -ForegroundColor Cyan
      return
    }
    Write-Host "[info] Found legacy repo-root data/. Migrating into studio/data." -ForegroundColor Yellow
    Copy-Item -Recurse -Force (Join-Path $LegacyRootData "*") $DestData -ErrorAction SilentlyContinue
    Move-ExistingFolder $LegacyRootData $MigrationsDir "repo_root_data"
    try {
      cmd /c "mklink /J `"$LegacyRootData`" `"$DestData`"" | Out-Null
      Write-Host "[info] Recreated repo-root data/ as a junction to studio/data." -ForegroundColor Yellow
    } catch {
      Write-Host ("[warn] Could not recreate repo-root data junction: " + $_.Exception.Message) -ForegroundColor Yellow
    }
  }
}

Assert-Command $PnpmExe
$PythonBootstrap = Resolve-PythonBootstrapCommand
$PythonExe = $PythonBootstrap.Command
$PythonArgs = $PythonBootstrap.Args
Assert-Command $PythonExe
Assert-SupportedPythonVersion $PythonExe $PythonArgs "Bootstrap Python" | Out-Null

$StudioDir = Resolve-Path (Join-Path $PSScriptRoot "../..")
$RepoRoot = Resolve-Path (Join-Path $StudioDir "../..")
$PyBackendDir = Join-Path $StudioDir "python_backend"

if (-not (Test-Path $StudioDir)) {
  throw "Studio directory not found: $StudioDir"
}
if (-not (Test-Path $PyBackendDir)) {
  throw "Python backend directory not found: $PyBackendDir"
}

$BackendPkgDir = Resolve-BackendPackageDir $PyBackendDir
$BundledFfmpegPath = Get-BundledFfmpegPath $StudioDir
Doctor $RepoRoot $StudioDir $PyBackendDir $BackendPkgDir $BundledFfmpegPath
Migrate-LegacyData $RepoRoot $StudioDir $PyBackendDir
$BundledFfmpegPath = Ensure-BundledFfmpeg $StudioDir

Write-Host "[1/4] Building Python backend (PyInstaller)..."
Push-Location $PyBackendDir

if (-not (Test-Path "venv")) {
  & $PythonExe @PythonArgs -m venv venv
}

$VenvPython = Join-Path $PyBackendDir "venv\Scripts\python.exe"
if (Test-Path $VenvPython) {
  if (-not (Test-SupportedPythonVersion $VenvPython @())) {
    Write-Host "[warn] Existing backend venv uses an unsupported Python; recreating it." -ForegroundColor Yellow
    Pop-Location
    Remove-Item -Recurse -Force (Join-Path $PyBackendDir "venv")
    Push-Location $PyBackendDir
    & $PythonExe @PythonArgs -m venv venv
  }
}

if (-not (Test-Path $VenvPython)) {
  throw "Virtual environment python not found: $VenvPython"
}
Assert-SupportedPythonVersion $VenvPython @() "Backend venv Python" | Out-Null

Invoke-Checked "upgrade backend packaging tools" {
  & $VenvPython -m pip install -U pip wheel $BackendSetuptoolsConstraint
}
Invoke-Checked "install backend bundle" {
  & $VenvPython -m pip install -e ".[studio_bundle]"
}
Invoke-Checked "install pyinstaller" {
  & $VenvPython -m pip install pyinstaller
}
Invoke-Checked "build backend via pyinstaller" {
  & $VenvPython -m PyInstaller .\pyinstaller.spec --clean --noconfirm
}

$BackendExe = Join-Path $PyBackendDir "dist\edmg-studio-backend\edmg-studio-backend.exe"
if (-not (Test-Path $BackendExe)) {
  $BackendExe = Join-Path $PyBackendDir "dist\edmg-studio-backend.exe"
}
if (-not (Test-Path $BackendExe)) {
  throw "Backend build failed: cannot find edmg-studio-backend.exe"
}

Pop-Location

Write-Host "[2/4] Staging backend into Electron resources..."
$BackendDstDir = Join-Path $StudioDir "electron-resources\backend"
New-Item -ItemType Directory -Force -Path $BackendDstDir | Out-Null
Copy-Item -Force $BackendExe (Join-Path $BackendDstDir "edmg-studio-backend.exe")

Write-Host "[3/4] Installing UI dependencies..."
Push-Location $StudioDir
if (Test-Path "pnpm-lock.yaml") {
  try {
    Invoke-Checked "pnpm install --frozen-lockfile" {
      & $PnpmExe install --frozen-lockfile
    }
  } catch {
    Write-Host ("[warn] pnpm install --frozen-lockfile failed; retrying with pnpm install. " + $_.Exception.Message) -ForegroundColor Yellow
    Invoke-Checked "pnpm install fallback" {
      & $PnpmExe install
    }
  }
} else {
  Invoke-Checked "pnpm install" {
    & $PnpmExe install
  }
}

Write-Host "[4/4] Building installer (electron-builder)..."
Invoke-Checked "pnpm run check:tooling" {
  & $PnpmExe run check:tooling
}
Invoke-Checked "pnpm run dist:win" {
  & $PnpmExe run dist:win
}
Pop-Location

Write-Host "Done. Final installer artifacts: studio/edmg-studio/dist/" -ForegroundColor Green
Write-Host "Staged desktop app: studio/edmg-studio/release/staged-app/" -ForegroundColor Cyan
