# Run from Windows PowerShell on Tyler's machine.
# Direct Vast.ai EDMG reinstall/bootstrap for instance 36066304.
# This bypasses vast.py JSON parsing entirely and uses the direct IP/ports you pasted.

param(
  [string]$InstanceId = "36066304",
  [string]$PublicIp = "174.88.252.119",
  [string]$SshPort = "16370",
  [string]$SshKey = "C:\Users\Tyler\.ssh\vast_ed25519_20260502",
  [string]$RemoteScriptLocalPath = ".\edmg_remote_reinstall_ports.sh",
  [string]$RepoBranch = "codex/Unified",
  [string]$RepoUrl = "https://github.com/HIMOI890/DWCTGenerativeSoundStudio.git",
  [string]$HfToken = "",
  [string]$BackendContainerPort = "8080",
  [string]$UiContainerPort = "1111",
  [string]$BackendPublicPort = "16486",
  [string]$UiPublicPort = "16476"
)

$ErrorActionPreference = "Stop"

function Invoke-Checked {
  param([string]$FilePath, [string[]]$Arguments)
  & $FilePath @Arguments
  if ($LASTEXITCODE -ne 0) {
    throw "Command failed with exit code ${LASTEXITCODE}: $FilePath $($Arguments -join ' ')"
  }
}

if (-not (Test-Path $SshKey)) { throw "SSH key does not exist: $SshKey" }
if (-not (Test-Path $RemoteScriptLocalPath)) {
  throw "Cannot find $RemoteScriptLocalPath. Put edmg_remote_reinstall_ports.sh next to this PS1 or pass -RemoteScriptLocalPath."
}

Write-Host "Instance: $InstanceId"
Write-Host "Using direct SSH: root@$PublicIp -p $SshPort"
Write-Host "Backend will run on container port $BackendContainerPort -> public http://${PublicIp}:$BackendPublicPort"
Write-Host "UI will run on container port $UiContainerPort -> public http://${PublicIp}:$UiPublicPort"

Write-Host "Testing SSH..."
Invoke-Checked "ssh" @(
  "-o", "StrictHostKeyChecking=accept-new",
  "-o", "BatchMode=yes",
  "-o", "ConnectTimeout=15",
  "-i", $SshKey,
  "-p", $SshPort,
  "root@$PublicIp",
  "echo SSH_OK"
)

Write-Host "Uploading reinstall script..."
Invoke-Checked "scp" @(
  "-o", "StrictHostKeyChecking=accept-new",
  "-i", $SshKey,
  "-P", $SshPort,
  $RemoteScriptLocalPath,
  "root@${PublicIp}:/tmp/edmg_remote_reinstall_ports.sh"
)

Write-Host "Running reinstall/bootstrap on Vast instance. This can take a while."
$remoteEnv = "REPO_BRANCH='$RepoBranch' REPO_URL='$RepoUrl' BACKEND_PORT='$BackendContainerPort' UI_PORT='$UiContainerPort'"
if ($HfToken) { $remoteEnv = "$remoteEnv HF_TOKEN='$HfToken'" }

Invoke-Checked "ssh" @(
  "-o", "StrictHostKeyChecking=accept-new",
  "-i", $SshKey,
  "-p", $SshPort,
  "root@$PublicIp",
  "$remoteEnv bash /tmp/edmg_remote_reinstall_ports.sh"
)

Write-Host ""
Write-Host "Done. Use these URLs:"
$backendUrl = "http://${PublicIp}:$BackendPublicPort"
$uiUrl = "http://${PublicIp}:$UiPublicPort/?backendUrl=$backendUrl"
Write-Host "Backend: $backendUrl"
Write-Host "Health:  $backendUrl/health"
Write-Host "Docs:    $backendUrl/docs"
Write-Host "Frontend: $uiUrl"
Write-Host ""
Write-Host "Remote logs:"
Write-Host "  /workspace/studio-home/logs/backend.log"
Write-Host "  /workspace/studio-home/logs/ui.log"
Write-Host "  /workspace/studio-home/logs/install-models.log"
