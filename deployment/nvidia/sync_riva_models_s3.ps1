[CmdletBinding()]
param(
  [ValidateSet("Status", "CreateBucket", "Upload", "Download")]
  [string]$Action = "Status",

  [string]$BucketName = "",
  [string]$Prefix = "",
  [string]$Region = "",
  [string]$EndpointUrl = "",
  [string]$ModelRepositoryPath = "",
  [string]$StorageClass = "",
  [string]$DockerImage = "amazon/aws-cli:2",
  [string]$EnvFile = "",

  [switch]$DryRun,
  [switch]$UseLocalAwsCli
)

$ErrorActionPreference = "Stop"

function Write-Step {
  param([string]$Message)
  Write-Host "[riva-s3] $Message"
}

function Import-EnvFile {
  param([string]$Path)

  if (-not (Test-Path -LiteralPath $Path)) {
    return
  }

  foreach ($line in Get-Content -LiteralPath $Path) {
    $trimmed = $line.Trim()
    if ($trimmed.Length -eq 0 -or $trimmed.StartsWith("#")) {
      continue
    }
    if ($trimmed -notmatch "^\s*([A-Za-z_][A-Za-z0-9_]*)=(.*)\s*$") {
      continue
    }

    $name = $Matches[1]
    $value = $Matches[2].Trim()
    if (($value.StartsWith('"') -and $value.EndsWith('"')) -or ($value.StartsWith("'") -and $value.EndsWith("'"))) {
      $value = $value.Substring(1, $value.Length - 2)
    }
    Set-Item -Path ("Env:{0}" -f $name) -Value $value
  }
}

function Test-Command {
  param([string]$Name)
  return $null -ne (Get-Command $Name -ErrorAction SilentlyContinue)
}

function Get-AwsCommand {
  $command = Get-Command "aws" -ErrorAction SilentlyContinue
  if ($null -ne $command) {
    return $command.Source
  }

  $standardPath = "C:\Program Files\Amazon\AWSCLIV2\aws.exe"
  if (Test-Path -LiteralPath $standardPath) {
    return $standardPath
  }

  return $null
}

function Get-LocalModelStats {
  param([string]$Path)

  if (-not (Test-Path -LiteralPath $Path)) {
    return [PSCustomObject]@{
      Path = $Path
      Exists = $false
      Files = 0
      Bytes = 0
      GiB = 0
    }
  }

  $files = @(Get-ChildItem -LiteralPath $Path -Recurse -File -ErrorAction SilentlyContinue)
  $sum = ($files | Measure-Object -Property Length -Sum).Sum
  if ($null -eq $sum) {
    $sum = 0
  }

  return [PSCustomObject]@{
    Path = $Path
    Exists = $true
    Files = $files.Count
    Bytes = $sum
    GiB = [Math]::Round($sum / 1GB, 2)
  }
}

function Test-AwsCredentialHint {
  $hasEnvKeys = -not [string]::IsNullOrWhiteSpace($env:AWS_ACCESS_KEY_ID) -and -not [string]::IsNullOrWhiteSpace($env:AWS_SECRET_ACCESS_KEY)
  $awsDir = Join-Path $env:USERPROFILE ".aws"
  $hasAwsFiles = (Test-Path -LiteralPath (Join-Path $awsDir "credentials")) -or (Test-Path -LiteralPath (Join-Path $awsDir "config"))
  $hasProfile = -not [string]::IsNullOrWhiteSpace($env:AWS_PROFILE)
  return ($hasEnvKeys -or $hasAwsFiles -or $hasProfile)
}

function Get-S3Uri {
  param(
    [string]$Bucket,
    [string]$KeyPrefix
  )

  $cleanPrefix = ($KeyPrefix -replace "\\", "/").Trim("/")
  if ([string]::IsNullOrWhiteSpace($cleanPrefix)) {
    return "s3://$Bucket"
  }
  return "s3://$Bucket/$cleanPrefix"
}

function Invoke-AwsCli {
  param(
    [string[]]$AwsArgs,
    [switch]$MountModelRepository
  )

  $awsCommand = Get-AwsCommand
  if ($UseLocalAwsCli -or $null -ne $awsCommand) {
    if ($null -eq $awsCommand) {
      throw "UseLocalAwsCli was set, but aws was not found on PATH."
    }
    & $awsCommand @AwsArgs
    if ($LASTEXITCODE -ne 0) {
      throw "aws exited with code $LASTEXITCODE"
    }
    return
  }

  if (-not (Test-Command "docker")) {
    throw "Neither aws nor docker is available. Install AWS CLI v2 or Docker Desktop."
  }

  $dockerArgs = @(
    "run",
    "--rm",
    "-e", "AWS_ACCESS_KEY_ID",
    "-e", "AWS_SECRET_ACCESS_KEY",
    "-e", "AWS_SESSION_TOKEN",
    "-e", "AWS_PROFILE",
    "-e", "AWS_REGION=$script:Region",
    "-e", "AWS_DEFAULT_REGION=$script:Region"
  )

  $awsDir = Join-Path $env:USERPROFILE ".aws"
  if (Test-Path -LiteralPath $awsDir) {
    $dockerArgs += @("-v", "${awsDir}:/root/.aws:ro")
  }

  if ($MountModelRepository) {
    $dockerArgs += @("-v", "${script:ModelRepositoryPath}:/data/riva-models")
  }

  $dockerArgs += @($script:DockerImage)
  & docker @dockerArgs @AwsArgs
  if ($LASTEXITCODE -ne 0) {
    throw "docker aws-cli exited with code $LASTEXITCODE"
  }
}

if ([string]::IsNullOrWhiteSpace($EnvFile)) {
  $candidateEnvFile = Join-Path $PSScriptRoot ".env.local"
  if (Test-Path -LiteralPath $candidateEnvFile) {
    $EnvFile = $candidateEnvFile
  }
}
if (-not [string]::IsNullOrWhiteSpace($EnvFile)) {
  Import-EnvFile -Path $EnvFile
}

if ([string]::IsNullOrWhiteSpace($BucketName)) {
  $BucketName = $env:EDMG_RIVA_S3_BUCKET
}
if ([string]::IsNullOrWhiteSpace($Prefix)) {
  $Prefix = if ($env:EDMG_RIVA_S3_PREFIX) { $env:EDMG_RIVA_S3_PREFIX } else { "nvdwctaigss/riva-models" }
}
if ([string]::IsNullOrWhiteSpace($Region)) {
  $Region = if ($env:AWS_REGION) { $env:AWS_REGION } elseif ($env:AWS_DEFAULT_REGION) { $env:AWS_DEFAULT_REGION } else { "us-east-1" }
}
if ([string]::IsNullOrWhiteSpace($EndpointUrl)) {
  $EndpointUrl = if ($env:EDMG_S3_ENDPOINT_URL) { $env:EDMG_S3_ENDPOINT_URL } elseif ($env:AWS_ENDPOINT_URL) { $env:AWS_ENDPOINT_URL } else { "" }
}
if ([string]::IsNullOrWhiteSpace($StorageClass)) {
  $StorageClass = if ($env:EDMG_RIVA_S3_STORAGE_CLASS) { $env:EDMG_RIVA_S3_STORAGE_CLASS } else { "STANDARD" }
}
if ([string]::IsNullOrWhiteSpace($ModelRepositoryPath)) {
  if ($env:EDMG_RIVA_MODEL_REPOSITORY) {
    $ModelRepositoryPath = $env:EDMG_RIVA_MODEL_REPOSITORY
  } elseif (Test-Path -LiteralPath "D:\Nvidia\NVDWCTAIGSS\riva-models") {
    $ModelRepositoryPath = "D:\Nvidia\NVDWCTAIGSS\riva-models"
  } else {
    $ModelRepositoryPath = "C:\NVDWCTAIGSS\starlift-data\nvidia\riva-models"
  }
}

$script:Region = $Region
$script:DockerImage = $DockerImage
$script:ModelRepositoryPath = [System.IO.Path]::GetFullPath($ModelRepositoryPath)

$stats = Get-LocalModelStats -Path $script:ModelRepositoryPath
Write-Step "local model repository"
$stats | Format-List

if ($Action -eq "Status" -and [string]::IsNullOrWhiteSpace($BucketName)) {
  Write-Step "no bucket configured. Set EDMG_RIVA_S3_BUCKET or pass -BucketName to check remote S3 state."
  return
}

if ([string]::IsNullOrWhiteSpace($BucketName)) {
  throw "BucketName is required. Set EDMG_RIVA_S3_BUCKET or pass -BucketName."
}

$s3Uri = Get-S3Uri -Bucket $BucketName -KeyPrefix $Prefix
$awsBaseArgs = @("--region", $Region)
if (-not [string]::IsNullOrWhiteSpace($EndpointUrl)) {
  $awsBaseArgs += @("--endpoint-url", $EndpointUrl)
}
if (-not [string]::IsNullOrWhiteSpace($env:AWS_PROFILE)) {
  $awsBaseArgs += @("--profile", $env:AWS_PROFILE)
}

if (-not (Test-AwsCredentialHint)) {
  if ($Action -eq "Status") {
    Write-Warning "No AWS credentials were found in environment variables or $env:USERPROFILE\.aws. Remote status was skipped."
    return
  }
  throw "No AWS credentials were found. Configure AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY, AWS_PROFILE, or files under $env:USERPROFILE\.aws."
}

switch ($Action) {
  "CreateBucket" {
    Write-Step "creating bucket s3://$BucketName in $Region"
    Invoke-AwsCli -AwsArgs ($awsBaseArgs + @("s3", "mb", "s3://$BucketName"))
  }
  "Upload" {
    if (-not $stats.Exists) {
      throw "Local model repository does not exist: $script:ModelRepositoryPath"
    }
    $syncSource = if ($null -ne (Get-AwsCommand)) { $script:ModelRepositoryPath } else { "/data/riva-models" }
    $args = $awsBaseArgs + @("s3", "sync", $syncSource, $s3Uri, "--no-progress", "--only-show-errors", "--storage-class", $StorageClass)
    if ($DryRun) {
      $args += @("--dryrun")
    }
    Write-Step "uploading $script:ModelRepositoryPath to $s3Uri"
    Invoke-AwsCli -AwsArgs $args -MountModelRepository
  }
  "Download" {
    if (-not (Test-Path -LiteralPath $script:ModelRepositoryPath)) {
      New-Item -ItemType Directory -Force -Path $script:ModelRepositoryPath | Out-Null
    }
    $syncTarget = if ($null -ne (Get-AwsCommand)) { $script:ModelRepositoryPath } else { "/data/riva-models" }
    $args = $awsBaseArgs + @("s3", "sync", $s3Uri, $syncTarget, "--no-progress", "--only-show-errors")
    if ($DryRun) {
      $args += @("--dryrun")
    }
    Write-Step "downloading $s3Uri to $script:ModelRepositoryPath"
    Invoke-AwsCli -AwsArgs $args -MountModelRepository
  }
  "Status" {
    Write-Step "remote object summary for $s3Uri"
    Invoke-AwsCli -AwsArgs ($awsBaseArgs + @("s3", "ls", $s3Uri, "--recursive", "--summarize", "--human-readable"))
  }
}
