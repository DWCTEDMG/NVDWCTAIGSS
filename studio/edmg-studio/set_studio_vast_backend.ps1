param(
  [string]$BackendUrl = "http://174.88.252.119:16486",
  [string]$RepoStudioDir = "",
  [switch]$LaunchDev,
  [switch]$LaunchPackaged,
  [switch]$SkipHealthCheck
)

$ErrorActionPreference = "Stop"

function Write-Step([string]$Message) {
  Write-Host "[edmg] $Message" -ForegroundColor Cyan
}

function Write-Warn([string]$Message) {
  Write-Host "[warn] $Message" -ForegroundColor Yellow
}

function Normalize-BackendUrl([string]$Url) {
  $trimmed = ""
  if ($null -ne $Url) { $trimmed = $Url.Trim().TrimEnd("/") }
  if (-not $trimmed) { throw "BackendUrl is empty" }
  if ($trimmed -notmatch '^https?://') { throw "BackendUrl must start with http:// or https://. Got: $trimmed" }
  return $trimmed
}

function Get-UrlHostPort([string]$Url) {
  $uri = [System.Uri]$Url
  $port = if ($uri.IsDefaultPort) { if ($uri.Scheme -eq "https") { 443 } else { 80 } } else { $uri.Port }
  return @{ Host = $uri.Host; Port = [string]$port }
}

function Set-DotEnvValue([string]$Path, [string]$Key, [string]$Value) {
  $lines = @()
  if (Test-Path $Path) {
    $lines = Get-Content -LiteralPath $Path
  }
  $pattern = "^$([regex]::Escape($Key))="
  $found = $false
  $next = foreach ($line in $lines) {
    if ($line -match $pattern) {
      $found = $true
      "$Key=$Value"
    } else {
      $line
    }
  }
  if (-not $found) {
    $next += "$Key=$Value"
  }
  $dir = Split-Path -Parent $Path
  if ($dir) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
  Set-Content -LiteralPath $Path -Value $next -Encoding UTF8
}

function Read-JsonObject([string]$Path) {
  if (-not (Test-Path $Path)) { return [ordered]@{} }
  $raw = Get-Content -LiteralPath $Path -Raw
  if (-not $raw.Trim()) { return [ordered]@{} }
  $obj = $raw | ConvertFrom-Json -ErrorAction Stop
  $hash = [ordered]@{}
  foreach ($prop in $obj.PSObject.Properties) { $hash[$prop.Name] = $prop.Value }
  return $hash
}

function Write-JsonObject([string]$Path, $Object) {
  $dir = Split-Path -Parent $Path
  if ($dir) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
  ($Object | ConvertTo-Json -Depth 20) + "`n" | Set-Content -LiteralPath $Path -Encoding UTF8
}

function Find-StudioDir([string]$Explicit) {
  if ($Explicit) {
    $resolved = Resolve-Path -LiteralPath $Explicit -ErrorAction Stop
    return $resolved.Path
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

  throw "Could not find studio/edmg-studio. Run this from the repo or pass -RepoStudioDir."
}

$BackendUrl = Normalize-BackendUrl $BackendUrl
$parts = Get-UrlHostPort $BackendUrl
$backendHost = $parts.Host
$backendPort = $parts.Port
$studioDir = Find-StudioDir $RepoStudioDir

Write-Step "Studio dir: $studioDir"
Write-Step "Backend URL: $BackendUrl"

if (-not $SkipHealthCheck) {
  try {
    $healthUrl = "$BackendUrl/health"
    Write-Step "Checking $healthUrl"
    $health = Invoke-WebRequest -UseBasicParsing -Uri $healthUrl -TimeoutSec 12
    Write-Step "Backend health HTTP $($health.StatusCode)"
  } catch {
    Write-Warn "Backend health check failed: $($_.Exception.Message)"
    Write-Warn "Continuing anyway; make sure the Vast backend is running on public port $backendPort."
  }
}

# 1) Browser/Vite dev frontend: api.ts reads VITE_EDMG_BACKEND_URL from .env.
$envPath = Join-Path $studioDir ".env"
Set-DotEnvValue $envPath "VITE_EDMG_BACKEND_URL" $BackendUrl
Set-DotEnvValue $envPath "EDMG_STUDIO_BACKEND_MODE" "external"
Set-DotEnvValue $envPath "EDMG_STUDIO_BACKEND_HOST" $backendHost
Set-DotEnvValue $envPath "EDMG_STUDIO_BACKEND_PORT" $backendPort
Set-DotEnvValue $envPath "EDMG_STUDIO_BACKEND_URL" $BackendUrl
Set-DotEnvValue $envPath "EDMG_STUDIO_SPAWN_BACKEND" "0"
Write-Step "Updated dev .env"

# 2) Future packaged builds: Electron reads electron-resources/runtime-defaults.json.
$runtimePath = Join-Path $studioDir "electron-resources\runtime-defaults.json"
$runtime = Read-JsonObject $runtimePath
$runtime["backend"] = [ordered]@{
  host = $backendHost
  port = [int]$backendPort
  url = $BackendUrl
  spawnBackend = $false
}
Write-JsonObject $runtimePath $runtime
Write-Step "Updated electron-resources/runtime-defaults.json for future packaged builds"

# 3) Dev Electron main process: main.mjs reads launcher_env.json in dev.
$launcherPath = Join-Path $studioDir "launcher_env.json"
$launcher = Read-JsonObject $launcherPath
$launcher["EDMG_STUDIO_BACKEND_MODE"] = "external"
$launcher["EDMG_STUDIO_BACKEND_HOST"] = $backendHost
$launcher["EDMG_STUDIO_BACKEND_PORT"] = $backendPort
$launcher["EDMG_STUDIO_BACKEND_URL"] = $BackendUrl
$launcher["EDMG_STUDIO_SPAWN_BACKEND"] = "0"
Write-JsonObject $launcherPath $launcher
Write-Step "Updated launcher_env.json for dev Electron"

# 4) Already-installed packaged app: main.mjs reads %APPDATA%\EDMG NVIDIA Workshop\bootstrap.json.
$bootstrapPath = Join-Path $env:APPDATA "EDMG NVIDIA Workshop\bootstrap.json"
$bootstrap = Read-JsonObject $bootstrapPath
$bootstrap["backendSettings"] = [ordered]@{
  mode = "external"
  host = $backendHost
  port = $backendPort
  url = $BackendUrl
}
$bootstrap["updatedAt"] = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
Write-JsonObject $bootstrapPath $bootstrap
Write-Step "Updated packaged app bootstrap: $bootstrapPath"

Write-Host ""
Write-Host "Configured EDMG NVIDIA Workshop for Vast backend:" -ForegroundColor Green
Write-Host "  Backend:  $BackendUrl"
Write-Host "  Host:     $backendHost"
Write-Host "  Port:     $backendPort"
Write-Host ""
Write-Host "Dev app:" -ForegroundColor Green
Write-Host "  cd `"$studioDir`""
Write-Host "  pnpm dev"
Write-Host ""
Write-Host "Packaged app:" -ForegroundColor Green
Write-Host "  Close and reopen EDMG NVIDIA Workshop. It will read $bootstrapPath"
Write-Host ""
Write-Host "Vast web UI URL, if needed:" -ForegroundColor Green
Write-Host "  http://174.88.252.119:16476/?backendUrl=$BackendUrl"

if ($LaunchDev) {
  Write-Step "Launching pnpm dev"
  Start-Process powershell.exe -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", "cd `"$studioDir`"; pnpm dev")
}

if ($LaunchPackaged) {
  $exeCandidates = @(
    (Join-Path $env:LOCALAPPDATA "Programs\EDMG NVIDIA Workshop\EDMG NVIDIA Workshop.exe"),
    (Join-Path $env:PROGRAMFILES "EDMG NVIDIA Workshop\EDMG NVIDIA Workshop.exe"),
    (Join-Path ${env:ProgramFiles(x86)} "EDMG NVIDIA Workshop\EDMG NVIDIA Workshop.exe")
  ) | Where-Object { $_ -and (Test-Path $_) }
  if ($exeCandidates.Count -gt 0) {
    Write-Step "Launching packaged EDMG NVIDIA Workshop: $($exeCandidates[0])"
    Start-Process $exeCandidates[0]
  } else {
    Write-Warn "Packaged EDMG NVIDIA Workshop exe not found under LocalAppData/Program Files. Launch it manually."
  }
}
