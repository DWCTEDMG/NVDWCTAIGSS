param(
  [Parameter(Mandatory = $true)]
  [string]$BackendUrl,
  [string]$RepoStudioDir = "",
  [switch]$LaunchDev,
  [switch]$LaunchPackaged,
  [switch]$SkipHealthCheck
)

$ErrorActionPreference = "Stop"

$ManagedBackendHost = "127.0.0.1"
$ManagedBackendPort = "7863"

function Write-Step([string]$Message) {
  Write-Host "[edmg-gcp] $Message" -ForegroundColor Cyan
}

function Write-Warn([string]$Message) {
  Write-Host "[edmg-gcp][warn] $Message" -ForegroundColor Yellow
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
    return Convert-ObjectToOrderedHash ($raw | ConvertFrom-Json -ErrorAction Stop)
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
    return (Resolve-Path -LiteralPath $Explicit -ErrorAction Stop).Path
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
$studioDir = Find-StudioDir $RepoStudioDir

Write-Step "Studio dir: $studioDir"
Write-Step "Remote backend URL: $BackendUrl"

if (-not $SkipHealthCheck) {
  try {
    $health = Invoke-WebRequest -UseBasicParsing -Uri "$BackendUrl/health" -TimeoutSec 12
    Write-Step "Backend health HTTP $($health.StatusCode)"
  } catch {
    Write-Warn "Backend health check failed: $($_.Exception.Message)"
    Write-Warn "Continuing anyway. Verify the GCP backend is reachable before launching Studio."
  }
}

$envFiles = @(
  (Join-Path $studioDir ".env"),
  (Join-Path $studioDir ".env.local")
)
foreach ($envPath in $envFiles) {
  Set-DotEnvValue $envPath "VITE_EDMG_BACKEND_URL" $BackendUrl
  Set-DotEnvValue $envPath "EDMG_BACKEND_URL" $BackendUrl
  Set-DotEnvValue $envPath "EDMG_STUDIO_BACKEND_MODE" "external"
  Set-DotEnvValue $envPath "EDMG_STUDIO_BACKEND_HOST" $ManagedBackendHost
  Set-DotEnvValue $envPath "EDMG_STUDIO_BACKEND_PORT" $ManagedBackendPort
  Set-DotEnvValue $envPath "EDMG_STUDIO_BACKEND_URL" $BackendUrl
  Set-DotEnvValue $envPath "EDMG_STUDIO_SPAWN_BACKEND" "0"
  Write-Step "Updated $envPath"
}

$launcherPath = Join-Path $studioDir "launcher_env.json"
$launcher = Read-JsonObject $launcherPath
$launcher["EDMG_STUDIO_BACKEND_MODE"] = "external"
$launcher["EDMG_STUDIO_BACKEND_HOST"] = $ManagedBackendHost
$launcher["EDMG_STUDIO_BACKEND_PORT"] = $ManagedBackendPort
$launcher["EDMG_STUDIO_BACKEND_URL"] = $BackendUrl
$launcher["EDMG_STUDIO_SPAWN_BACKEND"] = "0"
Write-JsonObject $launcherPath $launcher
Write-Step "Updated launcher_env.json"

$runtimePath = Join-Path $studioDir "electron-resources\runtime-defaults.json"
$runtime = Read-JsonObject $runtimePath
$runtime["backend"] = [ordered]@{
  host = $ManagedBackendHost
  port = [int]$ManagedBackendPort
  url = $BackendUrl
  spawnBackend = $false
}
Write-JsonObject $runtimePath $runtime
Write-Step "Updated runtime-defaults.json"

$bootstrapPath = Join-Path $env:APPDATA "EDMG NVIDIA Workshop\bootstrap.json"
$bootstrap = Read-JsonObject $bootstrapPath
$bootstrap["backendSettings"] = [ordered]@{
  mode = "external"
  host = $ManagedBackendHost
  port = $ManagedBackendPort
  url = $BackendUrl
}
$bootstrap["updatedAt"] = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
Write-JsonObject $bootstrapPath $bootstrap
Write-Step "Updated packaged app bootstrap: $bootstrapPath"

try {
  $uri = [System.Uri]$BackendUrl
  $browserUiUrl = "http://$($uri.Host):5173/?backendUrl=$BackendUrl"
  Write-Host ""
  Write-Host "Browser frontend example:" -ForegroundColor Green
  Write-Host "  $browserUiUrl"
} catch {
}

Write-Host ""
Write-Host "Configured EDMG NVIDIA Workshop for the external GCP backend:" -ForegroundColor Green
Write-Host "  $BackendUrl"
Write-Host ""
Write-Host "Desktop app:" -ForegroundColor Green
Write-Host "  Close EDMG NVIDIA Workshop completely, then reopen it."
Write-Host ""
Write-Host "Dev app:" -ForegroundColor Green
Write-Host "  cd `"$studioDir`""
Write-Host "  pnpm dev"

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
    Write-Warn "Packaged EDMG NVIDIA Workshop exe not found. Launch it manually."
  }
}
