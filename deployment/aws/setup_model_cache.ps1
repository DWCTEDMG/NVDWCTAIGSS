[CmdletBinding()]
param(
  [string]$BucketName = "",
  [string]$Region = "",
  [string]$Prefix = "models",
  [string]$Profile = "",
  [string]$StorageClass = "STANDARD",
  [switch]$SkipLifecycle
)

$ErrorActionPreference = "Stop"

function Write-Step {
  param([string]$Message)
  Write-Host ""
  Write-Host "==> $Message"
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

function Invoke-Aws {
  param([string[]]$Arguments)
  & $script:AwsCommand @script:AwsGlobalArgs @Arguments
  if ($LASTEXITCODE -ne 0) {
    throw "aws $($Arguments -join ' ') failed with exit code $LASTEXITCODE"
  }
}

function Invoke-AwsCapture {
  param([string[]]$Arguments)
  $output = & $script:AwsCommand @script:AwsGlobalArgs @Arguments
  if ($LASTEXITCODE -ne 0) {
    throw "aws $($Arguments -join ' ') failed with exit code $LASTEXITCODE"
  }
  return (($output | Out-String).Trim())
}

function New-AwsJsonArgument {
  param([string]$Json)
  $path = Join-Path $env:TEMP ("edmg-aws-{0}.json" -f ([Guid]::NewGuid().ToString("N")))
  Set-Content -LiteralPath $path -Value $Json -Encoding ASCII -NoNewline
  return "file://$($path.Replace('\', '/'))"
}

function Test-S3BucketAccess {
  param([string]$Name)
  $oldErrorActionPreference = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  try {
    & $script:AwsCommand @script:AwsGlobalArgs "s3api" "head-bucket" "--bucket" $Name *> $null
    return $LASTEXITCODE -eq 0
  } finally {
    $ErrorActionPreference = $oldErrorActionPreference
  }
}

function New-SafeBucketName {
  param(
    [string]$AccountId,
    [string]$BucketRegion
  )
  $regionTag = ($BucketRegion.ToLowerInvariant() -replace "[^a-z0-9-]", "-").Trim("-")
  return "edmg-model-cache-$AccountId-$regionTag"
}

function Assert-BucketName {
  param([string]$Name)
  if ($Name -notmatch "^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$") {
    throw "S3 bucket name must be 3-63 chars using lowercase letters, numbers, and hyphens."
  }
}

$script:AwsCommand = Get-AwsCommand
if ($null -eq $script:AwsCommand) {
  throw "AWS CLI is not installed or not on PATH. Install AWS CLI v2, restart PowerShell, then rerun this script."
}

if (-not $Region.Trim()) {
  $Region = if ($env:AWS_REGION) { $env:AWS_REGION } elseif ($env:AWS_DEFAULT_REGION) { $env:AWS_DEFAULT_REGION } else { "us-east-1" }
}
$Region = $Region.Trim()
$Prefix = ($Prefix -replace "\\", "/").Trim("/")
$StorageClass = ($StorageClass.Trim() -replace "\s+", "_").ToUpperInvariant()

$script:AwsGlobalArgs = @("--region", $Region)
if ($Profile.Trim()) {
  $script:AwsGlobalArgs += @("--profile", $Profile.Trim())
}

Write-Step "Checking AWS CLI credentials"
$AccountId = Invoke-AwsCapture @("sts", "get-caller-identity", "--query", "Account", "--output", "text")
if (-not $AccountId.Trim()) {
  throw "Could not resolve AWS account id."
}

if (-not $BucketName.Trim()) {
  $BucketName = New-SafeBucketName -AccountId $AccountId.Trim() -BucketRegion $Region
}
$BucketName = $BucketName.Trim().ToLowerInvariant()
Assert-BucketName -Name $BucketName

if (Test-S3BucketAccess -Name $BucketName) {
  Write-Step "Reusing existing bucket s3://$BucketName"
} else {
  Write-Step "Creating bucket s3://$BucketName in $Region"
  if ($Region -eq "us-east-1") {
    Invoke-Aws @("s3api", "create-bucket", "--bucket", $BucketName)
  } else {
    Invoke-Aws @(
      "s3api", "create-bucket",
      "--bucket", $BucketName,
      "--create-bucket-configuration", "LocationConstraint=$Region"
    )
  }
}

Write-Step "Blocking public access"
Invoke-Aws @(
  "s3api", "put-public-access-block",
  "--bucket", $BucketName,
  "--public-access-block-configuration",
  "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true"
)

Write-Step "Enforcing bucket-owner object ownership"
$ownership = @{
  Rules = @(
    @{ ObjectOwnership = "BucketOwnerEnforced" }
  )
} | ConvertTo-Json -Depth 5 -Compress
Invoke-Aws @("s3api", "put-bucket-ownership-controls", "--bucket", $BucketName, "--ownership-controls", (New-AwsJsonArgument $ownership))

Write-Step "Enabling AES256 default encryption"
$encryption = @{
  Rules = @(
    @{
      ApplyServerSideEncryptionByDefault = @{ SSEAlgorithm = "AES256" }
      BucketKeyEnabled = $true
    }
  )
} | ConvertTo-Json -Depth 5 -Compress
Invoke-Aws @("s3api", "put-bucket-encryption", "--bucket", $BucketName, "--server-side-encryption-configuration", (New-AwsJsonArgument $encryption))

Write-Step "Enabling versioning"
Invoke-Aws @("s3api", "put-bucket-versioning", "--bucket", $BucketName, "--versioning-configuration", "Status=Enabled")

if (-not $SkipLifecycle) {
  Write-Step "Adding lifecycle rule for old noncurrent model versions"
  $filterPrefix = if ($Prefix) { "$Prefix/" } else { "" }
  $lifecycle = @{
    Rules = @(
      @{
        ID = "ExpireNoncurrentModelVersions"
        Status = "Enabled"
        Filter = @{ Prefix = $filterPrefix }
        NoncurrentVersionExpiration = @{ NoncurrentDays = 30 }
      }
    )
  } | ConvertTo-Json -Depth 8 -Compress
  Invoke-Aws @("s3api", "put-bucket-lifecycle-configuration", "--bucket", $BucketName, "--lifecycle-configuration", (New-AwsJsonArgument $lifecycle))
}

Write-Step "EDMG AWS S3 model cache environment"
Write-Host "`$env:EDMG_AWS_MODEL_CACHE='1'"
Write-Host "`$env:EDMG_AWS_MODEL_CACHE_BUCKET='$BucketName'"
Write-Host "`$env:EDMG_AWS_MODEL_CACHE_PREFIX='$Prefix'"
Write-Host "`$env:EDMG_AWS_MODEL_CACHE_STORAGE_CLASS='$StorageClass'"
Write-Host "`$env:AWS_REGION='$Region'"
if ($Profile.Trim()) {
  Write-Host "`$env:AWS_PROFILE='$($Profile.Trim())'"
}

Write-Host ""
Write-Host "Model cache prefix:"
Write-Host "s3://$BucketName/$Prefix"
