param(
  [switch]$SkipElectronDirBuild,
  [string]$PnpmExe = "pnpm",
  [string]$IsccExe = "",
  [string]$SevenZipExe = "",
  [string]$OutDir = "dist-inno"
)

$ErrorActionPreference = "Stop"

function Invoke-Checked($Label, [scriptblock]$Action) {
  & $Action
  if ($LASTEXITCODE -ne 0) {
    throw ($Label + " failed with exit code " + $LASTEXITCODE)
  }
}

function Get-FullPath($Path) {
  return [IO.Path]::GetFullPath($Path)
}

function Assert-ChildPath($Root, $Path, $Label) {
  $rootFull = Get-FullPath $Root
  $pathFull = Get-FullPath $Path
  if (-not $rootFull.EndsWith([IO.Path]::DirectorySeparatorChar)) {
    $rootFull = $rootFull + [IO.Path]::DirectorySeparatorChar
  }
  if (-not $pathFull.StartsWith($rootFull, [StringComparison]::OrdinalIgnoreCase)) {
    throw ($Label + " is outside expected directory. Root: " + $rootFull + " Path: " + $pathFull)
  }
}

function Remove-DirectoryIfExists($Path, $SafeRoot, $Label) {
  if (-not (Test-Path $Path)) {
    return
  }
  Assert-ChildPath $SafeRoot $Path $Label
  Remove-Item -Recurse -Force -LiteralPath $Path
}

function Resolve-Iscc($RequestedPath) {
  if ($RequestedPath) {
    if (Test-Path $RequestedPath) {
      return (Resolve-Path $RequestedPath).Path
    }
    throw "Inno Setup compiler not found: $RequestedPath"
  }

  $command = Get-Command "ISCC.exe" -ErrorAction SilentlyContinue
  if ($command) {
    return $command.Source
  }

  $candidates = @(
    "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    "C:\Program Files\Inno Setup 6\ISCC.exe",
    (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe")
  )
  foreach ($candidate in $candidates) {
    if (Test-Path $candidate) {
      return $candidate
    }
  }

  return ""
}

function Resolve-SevenZip($RequestedPath) {
  if ($RequestedPath) {
    if (Test-Path $RequestedPath) {
      return (Resolve-Path $RequestedPath).Path
    }
    throw "7-Zip executable not found: $RequestedPath"
  }

  foreach ($name in @("7z.exe", "7za.exe", "7zr.exe")) {
    $command = Get-Command $name -ErrorAction SilentlyContinue
    if ($command) {
      return $command.Source
    }
  }

  $candidates = @(
    "C:\Program Files\7-Zip\7z.exe",
    "C:\Program Files (x86)\7-Zip\7z.exe",
    (Join-Path $env:LOCALAPPDATA "Programs\7-Zip\7z.exe")
  )
  foreach ($candidate in $candidates) {
    if (Test-Path $candidate) {
      return $candidate
    }
  }

  return ""
}

function Escape-InnoValue($Value) {
  return ($Value -replace '"', '""')
}

$StudioDir = (Resolve-Path (Join-Path $PSScriptRoot "../..")).Path
$PackageJsonPath = Join-Path $StudioDir "package.json"
$PackageJson = Get-Content -Raw -Path $PackageJsonPath | ConvertFrom-Json
$Version = [string]$PackageJson.version
$ProductName = [string]$PackageJson.build.productName
if (-not $ProductName) { $ProductName = "EDMG NVIDIA Workshop" }
$AppId = [string]$PackageJson.build.appId
if (-not $AppId) { $AppId = "com.dwct.edmgstudio" }

if ([IO.Path]::IsPathRooted($OutDir)) {
  $OutDirAbs = Get-FullPath $OutDir
} else {
  $OutDirAbs = Get-FullPath (Join-Path $StudioDir $OutDir)
}

$DistDir = Join-Path $StudioDir "dist"
$WinUnpackedDir = Join-Path $DistDir "win-unpacked"
$PayloadRoot = Join-Path $OutDirAbs "payload"
$PayloadArchive = Join-Path $PayloadRoot "win-unpacked.7z"
$PayloadToolsDir = Join-Path $PayloadRoot "tools\7zip"
$GeneratedIss = Join-Path $OutDirAbs "edmg-studio-external.iss"
$IconPath = Join-Path $StudioDir "electron-resources\app-icon.ico"

if (-not $SkipElectronDirBuild) {
  Push-Location $StudioDir
  try {
    Invoke-Checked "pnpm run build" { & $PnpmExe run build }
    Invoke-Checked "pnpm run prepare:release-bundle" { & $PnpmExe run prepare:release-bundle }
    Invoke-Checked "pnpm run release:stage-desktop" { & $PnpmExe run release:stage-desktop }
    Invoke-Checked "pnpm run check:release-metadata" { & $PnpmExe run check:release-metadata }
    Invoke-Checked "electron-builder directory package" {
      & node scripts/run-electron-builder.mjs --dir --x64 -c.directories.app=release/staged-app
    }
  } finally {
    Pop-Location
  }
}

if (-not (Test-Path $WinUnpackedDir)) {
  throw "Electron unpacked app not found: $WinUnpackedDir. Run without -SkipElectronDirBuild first."
}

New-Item -ItemType Directory -Force -Path $OutDirAbs | Out-Null
Remove-DirectoryIfExists $PayloadRoot $OutDirAbs "payload directory"
New-Item -ItemType Directory -Force -Path $PayloadRoot | Out-Null

$ResolvedSevenZip = Resolve-SevenZip $SevenZipExe
if (-not $ResolvedSevenZip) {
  throw "7-Zip was not found. Install 7-Zip or rerun with -SevenZipExe <path-to-7z.exe>."
}

New-Item -ItemType Directory -Force -Path $PayloadToolsDir | Out-Null
$SevenZipDir = Split-Path -Parent $ResolvedSevenZip
Copy-Item -Force -LiteralPath $ResolvedSevenZip -Destination (Join-Path $PayloadToolsDir "7z.exe")
foreach ($toolFile in @("7z.dll", "License.txt")) {
  $candidate = Join-Path $SevenZipDir $toolFile
  if (Test-Path $candidate) {
    Copy-Item -Force -LiteralPath $candidate -Destination (Join-Path $PayloadToolsDir $toolFile)
  }
}

Write-Host ("[info] Creating external payload archive: " + $PayloadArchive) -ForegroundColor Cyan
Push-Location $WinUnpackedDir
try {
  Invoke-Checked "7-Zip payload archive" {
    & $ResolvedSevenZip a -t7z -mx=0 -mmt=on $PayloadArchive ".\*"
  }
} finally {
  Pop-Location
}

$OutputBaseFilename = "EDMG-NVIDIA-Workshop-Setup-" + $Version
$issLines = @(
  "; Generated by packaging/windows/build_inno_external.ps1. Do not edit this generated copy.",
  "",
  "[Setup]",
  "AppId={{faa597b4-33fe-5e5f-81a9-4db216782ca3}",
  ("AppName={0}" -f (Escape-InnoValue $ProductName)),
  ("AppVersion={0}" -f (Escape-InnoValue $Version)),
  "AppPublisher=Dwct",
  "AppPublisherURL=https://github.com/HIMOI890/DWCTGenerativeSoundStudio",
  "AppSupportURL=https://github.com/HIMOI890/DWCTGenerativeSoundStudio/issues",
  ("DefaultDirName={{localappdata}}\Programs\{0}" -f (Escape-InnoValue $ProductName)),
  ("DefaultGroupName={0}" -f (Escape-InnoValue $ProductName)),
  ("UninstallDisplayIcon={{app}}\{0}.exe" -f (Escape-InnoValue $ProductName)),
  ("OutputDir={0}" -f (Escape-InnoValue $OutDirAbs)),
  ("OutputBaseFilename={0}" -f (Escape-InnoValue $OutputBaseFilename)),
  "Compression=none",
  "SolidCompression=no",
  "WizardStyle=modern",
  "PrivilegesRequired=lowest",
  "DisableProgramGroupPage=yes",
  "CloseApplications=yes",
  ("AppMutex={0}" -f (Escape-InnoValue $AppId))
)

if (Test-Path $IconPath) {
  $issLines += ("SetupIconFile={0}" -f (Escape-InnoValue $IconPath))
}

$issLines += @(
  "",
  "[Dirs]",
  'Name: "{app}"',
  "",
  "[Files]",
  'Source: "{src}\payload\win-unpacked.7z"; DestDir: "{tmp}\edmg-studio-payload"; Flags: external deleteafterinstall',
  'Source: "{src}\payload\tools\7zip\7z.exe"; DestDir: "{tmp}\edmg-studio-tools"; Flags: external deleteafterinstall',
  'Source: "{src}\payload\tools\7zip\7z.dll"; DestDir: "{tmp}\edmg-studio-tools"; Flags: external deleteafterinstall',
  "",
  "[Icons]",
  'Name: "{group}\EDMG NVIDIA Workshop"; Filename: "{app}\EDMG NVIDIA Workshop.exe"; WorkingDir: "{app}"',
  'Name: "{userdesktop}\EDMG NVIDIA Workshop"; Filename: "{app}\EDMG NVIDIA Workshop.exe"; WorkingDir: "{app}"; Tasks: desktopicon',
  "",
  "[Tasks]",
  'Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked',
  "",
  "[Run]",
  'Filename: "{tmp}\edmg-studio-tools\7z.exe"; Parameters: "x ""{tmp}\edmg-studio-payload\win-unpacked.7z"" -o""{app}"" -aoa -y"; StatusMsg: "Extracting EDMG NVIDIA Workshop payload..."; Flags: runhidden waituntilterminated',
  'Filename: "{app}\EDMG NVIDIA Workshop.exe"; Description: "Launch EDMG NVIDIA Workshop"; Flags: nowait postinstall skipifsilent'
  "",
  "[UninstallDelete]",
  'Type: filesandordirs; Name: "{app}\*"'
)

Set-Content -Path $GeneratedIss -Value $issLines -Encoding UTF8
Write-Host ("[info] Wrote Inno script: " + $GeneratedIss) -ForegroundColor Cyan

$ResolvedIscc = Resolve-Iscc $IsccExe
if (-not $ResolvedIscc) {
  Write-Host "[warn] Inno Setup compiler (ISCC.exe) was not found." -ForegroundColor Yellow
  Write-Host "[warn] Install Inno Setup 6 or rerun with -IsccExe <path-to-ISCC.exe>." -ForegroundColor Yellow
  Write-Host ("[warn] External payload is ready at: " + $PayloadRoot) -ForegroundColor Yellow
  throw "Cannot compile Inno installer because ISCC.exe is missing."
}

Invoke-Checked "Inno Setup compile" {
  & $ResolvedIscc $GeneratedIss
}

Write-Host "Done. Inno external-payload release:" -ForegroundColor Green
Write-Host ("  Setup:   " + (Join-Path $OutDirAbs ($OutputBaseFilename + ".exe"))) -ForegroundColor Green
Write-Host ("  Payload: " + $PayloadRoot) -ForegroundColor Green
Write-Host "Ship the setup EXE and payload directory together." -ForegroundColor Cyan
