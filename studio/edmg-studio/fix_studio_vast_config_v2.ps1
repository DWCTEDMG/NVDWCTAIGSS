param(
  [string]$BackendUrl = "http://174.88.252.119:16486",
  [string]$RepoStudioDir = "",
  [switch]$LaunchDev,
  [switch]$LaunchPackaged,
  [switch]$SkipHealthCheck,
  [switch]$EnableDirector
)

$ErrorActionPreference = "Stop"

function Write-Step([string]$Message) {
  Write-Host "[edmg] $Message" -ForegroundColor Cyan
}

function Write-Warn([string]$Message) {
  Write-Host "[warn] $Message" -ForegroundColor Yellow
}

function Write-TextNoBom([string]$Path, [string]$Text) {
  $dir = Split-Path -Parent $Path
  if ($dir) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
  $encoding = New-Object System.Text.UTF8Encoding($false)
  [System.IO.File]::WriteAllText($Path, $Text, $encoding)
}

function Read-TextUtf8([string]$Path) {
  if (-not (Test-Path -LiteralPath $Path)) { return "" }
  $text = [System.IO.File]::ReadAllText($Path, [System.Text.Encoding]::UTF8)
  if ($text.Length -gt 0 -and [int][char]$text[0] -eq 65279) {
    $text = $text.Substring(1)
  }
  return $text
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
  $port = 80
  if ($uri.IsDefaultPort) {
    if ($uri.Scheme -eq "https") { $port = 443 } else { $port = 80 }
  } else {
    $port = $uri.Port
  }
  return @{ Host = $uri.Host; Port = [string]$port }
}

function Convert-ObjectToOrderedHash($Object) {
  $hash = [ordered]@{}
  if ($null -eq $Object) { return $hash }
  foreach ($prop in $Object.PSObject.Properties) {
    $hash[$prop.Name] = $prop.Value
  }
  return $hash
}

function Read-JsonObject([string]$Path) {
  if (-not (Test-Path -LiteralPath $Path)) { return [ordered]@{} }
  $raw = Read-TextUtf8 $Path
  if (-not $raw.Trim()) { return [ordered]@{} }
  try {
    $obj = $raw | ConvertFrom-Json -ErrorAction Stop
    return Convert-ObjectToOrderedHash $obj
  } catch {
    Write-Warn "Could not parse JSON at $Path. Replacing it with a clean object. Error: $($_.Exception.Message)"
    return [ordered]@{}
  }
}

function Write-JsonObject([string]$Path, $Object) {
  $json = ($Object | ConvertTo-Json -Depth 30)
  Write-TextNoBom $Path ($json + "`n")
}

function Set-DotEnvValue([string]$Path, [string]$Key, [string]$Value) {
  $raw = ""
  if (Test-Path -LiteralPath $Path) { $raw = Read-TextUtf8 $Path }
  $lines = @()
  if ($raw.Length -gt 0) { $lines = $raw -split "`r?`n" }
  $pattern = "^$([regex]::Escape($Key))="
  $found = $false
  $next = New-Object System.Collections.Generic.List[string]
  foreach ($line in $lines) {
    if ($line -match $pattern) {
      if (-not $found) { $next.Add("$Key=$Value") }
      $found = $true
    } elseif ($line -ne "") {
      $next.Add($line)
    }
  }
  if (-not $found) { $next.Add("$Key=$Value") }
  Write-TextNoBom $Path (($next -join "`n") + "`n")
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

  throw "Could not find studio/edmg-studio. Run from that folder or pass -RepoStudioDir."
}

$BackendUrl = Normalize-BackendUrl $BackendUrl
$parts = Get-UrlHostPort $BackendUrl
$backendHost = $parts.Host
$backendPort = $parts.Port
$studioDir = Find-StudioDir $RepoStudioDir
$directorSpawn = "0"
if ($EnableDirector) { $directorSpawn = "1" }

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
    Write-Warn "Continuing. If Studio cannot connect, verify the Vast backend is running on public port $backendPort."
  }
}

# Dev browser / Vite config.
$envPath = Join-Path $studioDir ".env"
Set-DotEnvValue $envPath "VITE_EDMG_BACKEND_URL" $BackendUrl
Set-DotEnvValue $envPath "EDMG_STUDIO_BACKEND_MODE" "external"
Set-DotEnvValue $envPath "EDMG_STUDIO_BACKEND_HOST" $backendHost
Set-DotEnvValue $envPath "EDMG_STUDIO_BACKEND_PORT" $backendPort
Set-DotEnvValue $envPath "EDMG_STUDIO_BACKEND_URL" $BackendUrl
Set-DotEnvValue $envPath "EDMG_STUDIO_SPAWN_BACKEND" "0"
Set-DotEnvValue $envPath "EDMG_DIRECTOR_SPAWN" $directorSpawn
Write-Step "Updated .env with no BOM"

# Dev Electron config.
$launcherPath = Join-Path $studioDir "launcher_env.json"
$launcher = Read-JsonObject $launcherPath
$launcher["EDMG_STUDIO_BACKEND_MODE"] = "external"
$launcher["EDMG_STUDIO_BACKEND_HOST"] = $backendHost
$launcher["EDMG_STUDIO_BACKEND_PORT"] = $backendPort
$launcher["EDMG_STUDIO_BACKEND_URL"] = $BackendUrl
$launcher["EDMG_STUDIO_SPAWN_BACKEND"] = "0"
$launcher["EDMG_DIRECTOR_SPAWN"] = $directorSpawn
Write-JsonObject $launcherPath $launcher
Write-Step "Updated launcher_env.json with no BOM"

# Future packaged builds. This file must be UTF-8 without BOM because main.mjs uses JSON.parse(fs.readFileSync(..., 'utf8')).
$runtimePath = Join-Path $studioDir "electron-resources\runtime-defaults.json"
$runtime = Read-JsonObject $runtimePath
$runtime["backend"] = [ordered]@{
  host = $backendHost
  port = [int]$backendPort
  url = $BackendUrl
  spawnBackend = $false
}
$runtime["director"] = [ordered]@{
  spawnDirector = $EnableDirector.IsPresent
}
Write-JsonObject $runtimePath $runtime
Write-Step "Updated electron-resources/runtime-defaults.json with no BOM"

# Already installed packaged app config.
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
Write-Step "Updated packaged bootstrap with no BOM: $bootstrapPath"

Write-Host ""
Write-Host "Configured EDMG NVIDIA Workshop for Vast backend:" -ForegroundColor Green
Write-Host "  $BackendUrl"
Write-Host ""
Write-Host "Now run dev with:" -ForegroundColor Green
Write-Host "  `$env:EDMG_DIRECTOR_SPAWN='0'"
Write-Host "  pnpm dev"
Write-Host ""
Write-Host "Packaged app:" -ForegroundColor Green
Write-Host "  Close EDMG NVIDIA Workshop completely, then reopen it."
Write-Host ""
Write-Host "Direct frontend URL, if needed:" -ForegroundColor Green
Write-Host "  http://174.88.252.119:16476/?backendUrl=$BackendUrl"

if ($LaunchDev) {
  Write-Step "Launching pnpm dev with EDMG_DIRECTOR_SPAWN=0"
  Start-Process powershell.exe -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", "cd `"$studioDir`"; `$env:EDMG_DIRECTOR_SPAWN='0'; pnpm dev")
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
    Write-Warn "Packaged EDMG NVIDIA Workshop exe not found. Launch it manually."
  }
}
