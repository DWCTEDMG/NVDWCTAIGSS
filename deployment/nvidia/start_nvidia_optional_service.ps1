param(
  [ValidateSet("nim", "riva", "triton", "audio2face", "ace", "cosmos", "nemo", "all")]
  [string[]]$Service = @("triton"),
  [string]$EnvFile = "deployment/nvidia/.env.local",
  [string]$BackendUrl = "http://127.0.0.1:7863",
  [switch]$Pull,
  [switch]$ConfigureEnv,
  [switch]$ConfigureOnly,
  [switch]$EnableRivaAsr,
  [string]$TritonModel = "",
  [string]$Audio2FaceGeneratePath = "/v1/a2f/jobs",
  [string]$AceGeneratePath = "/v1/avatar/animation",
  [string]$CosmosInferPath = "/v1/infer",
  [int]$TimeoutSeconds = 180
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Write-Step {
  param([string]$Message)
  Write-Host "[nvidia-service] $Message"
}

function Import-EnvFile {
  param([string]$Path)
  if (-not (Test-Path -LiteralPath $Path)) {
    return
  }
  foreach ($line in Get-Content -LiteralPath $Path) {
    $trimmed = $line.Trim()
    if (-not $trimmed -or $trimmed.StartsWith("#") -or -not $trimmed.Contains("=")) {
      continue
    }
    $name, $value = $trimmed.Split("=", 2)
    $name = $name.Trim()
    $value = $value.Trim().Trim('"').Trim("'")
    if ($name) {
      [Environment]::SetEnvironmentVariable($name, $value, "Process")
    }
  }
}

function Set-EnvFileValue {
  param(
    [string]$Path,
    [string]$Name,
    [string]$Value
  )
  $lines = @()
  if (Test-Path -LiteralPath $Path) {
    $lines = @(Get-Content -LiteralPath $Path)
  }
  $updated = $false
  $out = foreach ($line in $lines) {
    if ($line -match "^\s*$([regex]::Escape($Name))\s*=") {
      $updated = $true
      "$Name=$Value"
    } else {
      $line
    }
  }
  if (-not $updated) {
    $out += "$Name=$Value"
  }
  $parent = Split-Path -Parent $Path
  if ($parent -and -not (Test-Path -LiteralPath $parent)) {
    New-Item -ItemType Directory -Force -Path $parent | Out-Null
  }
  $out | Set-Content -LiteralPath $Path -Encoding UTF8
  [Environment]::SetEnvironmentVariable($Name, $Value, "Process")
}

function Get-EnvInt {
  param(
    [string]$Name,
    [int]$Default
  )
  try {
    $raw = [Environment]::GetEnvironmentVariable($Name, "Process")
    if ([string]::IsNullOrWhiteSpace($raw)) {
      return $Default
    }
    return [int]$raw
  } catch {
    return $Default
  }
}

function Test-TcpPort {
  param(
    [string]$HostName,
    [int]$Port,
    [int]$TimeoutMs = 2000
  )
  $client = [System.Net.Sockets.TcpClient]::new()
  try {
    $async = $client.BeginConnect($HostName, $Port, $null, $null)
    if (-not $async.AsyncWaitHandle.WaitOne($TimeoutMs)) {
      return $false
    }
    $client.EndConnect($async)
    return $true
  } catch {
    return $false
  } finally {
    $client.Close()
  }
}

function Wait-HttpReady {
  param(
    [string]$Url,
    [int]$TimeoutSeconds
  )
  $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
  while ((Get-Date) -lt $deadline) {
    try {
      $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 4
      if ([int]$response.StatusCode -ge 200 -and [int]$response.StatusCode -lt 500) {
        return $true
      }
    } catch {
      Start-Sleep -Seconds 3
      continue
    }
    Start-Sleep -Seconds 3
  }
  return $false
}

function Wait-TcpReady {
  param(
    [string]$HostName,
    [int]$Port,
    [int]$TimeoutSeconds
  )
  $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
  while ((Get-Date) -lt $deadline) {
    if (Test-TcpPort -HostName $HostName -Port $Port) {
      return $true
    }
    Start-Sleep -Seconds 3
  }
  return $false
}

function Get-DefinitionProbeEndpoint {
  param([hashtable]$Definition)
  if (-not $Definition.ContainsKey("ProbeKind")) {
    return $null
  }

  switch ([string]$Definition.ProbeKind) {
    "http" {
      if (-not $Definition.ContainsKey("ProbeUrl")) {
        return $null
      }
      try {
        $uri = [System.Uri]([string]$Definition.ProbeUrl)
        return [pscustomobject]@{
          Host = $uri.Host
          Port = [int]$uri.Port
        }
      } catch {
        return $null
      }
    }
    "tcp" {
      if ($Definition.ContainsKey("Host") -and $Definition.ContainsKey("Port")) {
        return [pscustomobject]@{
          Host = [string]$Definition.Host
          Port = [int]$Definition.Port
        }
      }
    }
  }
  return $null
}

function Test-DefinitionPortOpen {
  param([hashtable]$Definition)
  $endpoint = Get-DefinitionProbeEndpoint -Definition $Definition
  if ($null -eq $endpoint) {
    return $false
  }
  return Test-TcpPort -HostName $endpoint.Host -Port ([int]$endpoint.Port) -TimeoutMs 750
}

function Invoke-NvidiaCompose {
  param(
    [string]$Profile,
    [string]$ComposeService,
    [string]$EnvPath
  )
  $baseArgs = @(
    "compose",
    "--profile",
    $Profile,
    "--env-file",
    $EnvPath,
    "-f",
    "docker-compose.starlift.yml",
    "-f",
    "deployment/nvidia/docker-compose.nvidia.yml"
  )
  if ($Pull) {
    Write-Step "Pulling $ComposeService image for profile $Profile"
    & docker @($baseArgs + @("pull", $ComposeService))
    if ($LASTEXITCODE -ne 0) {
      throw "docker compose pull failed for $ComposeService"
    }
  }
  Write-Step "Starting $ComposeService through profile $Profile"
  & docker @($baseArgs + @("up", "-d", $ComposeService))
  if ($LASTEXITCODE -ne 0) {
    throw "docker compose up failed for $ComposeService"
  }
}

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Set-Location $repoRoot
$envPath = if ([System.IO.Path]::IsPathRooted($EnvFile)) {
  [System.IO.Path]::GetFullPath($EnvFile)
} else {
  [System.IO.Path]::GetFullPath((Join-Path $repoRoot.Path $EnvFile))
}
if (-not (Test-Path -LiteralPath $envPath)) {
  throw "Env file not found: $envPath. Copy deployment/nvidia/.env.example to deployment/nvidia/.env.local first."
}
Import-EnvFile -Path $envPath

$nimPort = Get-EnvInt -Name "EDMG_NIM_LLM_PORT" -Default 8001
$rivaGrpcPort = Get-EnvInt -Name "EDMG_RIVA_GRPC_PORT" -Default 50051
$tritonHttpPort = Get-EnvInt -Name "EDMG_TRITON_HTTP_PORT" -Default 8002
$audio2facePort = Get-EnvInt -Name "EDMG_AUDIO2FACE_HTTP_PORT" -Default 8011
$cosmosPort = Get-EnvInt -Name "EDMG_COSMOS_HTTP_PORT" -Default 8012
$currentRivaAsr = [Environment]::GetEnvironmentVariable("EDMG_NVIDIA_RIVA_ASR", "Process")
if ([string]::IsNullOrWhiteSpace($currentRivaAsr)) {
  $currentRivaAsr = "0"
}

$definitions = @{
  nim = @{
    Profile = "nim"
    ComposeService = "nvidia-nim-llm"
    ProbeKind = "http"
    ProbeUrl = "http://127.0.0.1:$nimPort/v1/models"
    Env = @{
      EDMG_NVIDIA_NIM_URL = "http://127.0.0.1:$nimPort"
      EDMG_AI_OPENAI_COMPAT_BASE_URL = "http://127.0.0.1:$nimPort/v1"
    }
  }
  riva = @{
    Profile = "riva"
    ComposeService = "nvidia-riva"
    ProbeKind = "tcp"
    Host = "127.0.0.1"
    Port = $rivaGrpcPort
    Env = @{
      EDMG_NVIDIA_RIVA_URL = "127.0.0.1:$rivaGrpcPort"
      EDMG_RIVA_URL = "127.0.0.1:$rivaGrpcPort"
      EDMG_NVIDIA_RIVA_ASR = $(if ($EnableRivaAsr) { "1" } else { $currentRivaAsr })
    }
  }
  triton = @{
    Profile = "triton"
    ComposeService = "nvidia-triton"
    ProbeKind = "http"
    ProbeUrl = "http://127.0.0.1:$tritonHttpPort/v2/health/ready"
    Env = @{
      EDMG_NVIDIA_TRITON_URL = "http://127.0.0.1:$tritonHttpPort"
    }
  }
  audio2face = @{
    Profile = "audio2face"
    ComposeService = "nvidia-audio2face"
    ProbeKind = "tcp"
    Host = "127.0.0.1"
    Port = $audio2facePort
    Env = @{
      EDMG_NVIDIA_AUDIO2FACE_URL = "http://127.0.0.1:$audio2facePort"
      EDMG_NVIDIA_AUDIO2FACE_GENERATE_PATH = $Audio2FaceGeneratePath
    }
  }
  ace = @{
    Profile = "ace"
    ComposeService = "nvidia-audio2face"
    ProbeKind = "tcp"
    Host = "127.0.0.1"
    Port = $audio2facePort
    Env = @{
      EDMG_NVIDIA_ACE_URL = "http://127.0.0.1:$audio2facePort"
      EDMG_NVIDIA_ACE_GENERATE_PATH = $AceGeneratePath
    }
  }
  cosmos = @{
    Profile = "cosmos"
    ComposeService = "nvidia-cosmos"
    ProbeKind = "tcp"
    Host = "127.0.0.1"
    Port = $cosmosPort
    Env = @{
      EDMG_NVIDIA_COSMOS_URL = "http://127.0.0.1:$cosmosPort"
      EDMG_NVIDIA_COSMOS_INFER_PATH = $CosmosInferPath
    }
  }
  nemo = @{
    Profile = "nemo"
    ComposeService = "nvidia-nemo"
    ProbeKind = "none"
    Env = @{}
  }
}

if (-not [string]::IsNullOrWhiteSpace($TritonModel)) {
  $definitions.triton.Env["EDMG_NVIDIA_TRITON_MODEL"] = $TritonModel
}

$requested = New-Object System.Collections.Generic.List[string]
foreach ($item in $Service) {
  if ($item -eq "all") {
    foreach ($name in @("nim", "riva", "triton", "audio2face", "cosmos", "nemo")) {
      if (-not $requested.Contains($name)) {
        $requested.Add($name)
      }
    }
  } elseif (-not $requested.Contains($item)) {
    $requested.Add($item)
  }
}

foreach ($name in $requested) {
  $definition = $definitions[$name]
  if ($ConfigureEnv -or $ConfigureOnly) {
    foreach ($entry in $definition.Env.GetEnumerator()) {
      Set-EnvFileValue -Path $envPath -Name $entry.Key -Value ([string]$entry.Value)
      Write-Step "Configured $($entry.Key) in $EnvFile"
    }
  }

  if ($ConfigureOnly) {
    Write-Step "ConfigureOnly supplied; skipped container start for $name."
    continue
  }

  if (Test-DefinitionPortOpen -Definition $definition) {
    Write-Step "$name probe port is already bound; skipped compose up and waiting for the existing service."
  } else {
    Invoke-NvidiaCompose -Profile $definition.Profile -ComposeService $definition.ComposeService -EnvPath $envPath
  }

  switch ($definition.ProbeKind) {
    "http" {
      Write-Step "Waiting for $name HTTP probe: $($definition.ProbeUrl)"
      $ready = Wait-HttpReady -Url $definition.ProbeUrl -TimeoutSeconds $TimeoutSeconds
    }
    "tcp" {
      Write-Step "Waiting for $name TCP probe: $($definition.Host):$($definition.Port)"
      $ready = Wait-TcpReady -HostName $definition.Host -Port ([int]$definition.Port) -TimeoutSeconds $TimeoutSeconds
    }
    default {
      $ready = $true
      Write-Step "$name has no simple readiness probe; container start was requested."
    }
  }

  if ($ready) {
    Write-Step "$name probe passed."
  } else {
    Write-Step "$name did not become reachable within $TimeoutSeconds seconds."
  }
}

if ($ConfigureEnv -or $ConfigureOnly) {
  Write-Step "Restart the backend to load new env values:"
  Write-Step "powershell -NoProfile -ExecutionPolicy Bypass -File deployment/nvidia/start_nvidia_runtime_local.ps1 -BackendUrl `"$BackendUrl`" -StopExistingBackend -SkipKit"
}
Write-Step "Backend service status endpoint: $($BackendUrl.TrimEnd('/'))/v1/nvidia/services"
