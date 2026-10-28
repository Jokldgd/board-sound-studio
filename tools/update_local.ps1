# Update / install Soundboard Studio for the current user (no admin needed).
#
# What it does:
#   1. stops a running instance
#   2. copies the freshly built app from dist\SoundboardStudio to
#      %LOCALAPPDATA%\Programs\Soundboard Studio
#   3. moves any user data found next to the old copy into
#      %APPDATA%\Soundboard Studio (audio\ + config.json)  -> never deletes
#   4. creates Desktop / Start Menu shortcuts
#   5. launches the app
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File .\tools\update_local.ps1
#   ... -DryRun          # print what would happen, change nothing
#   ... -NoLaunch        # do not start the app afterwards

param(
    [string]$Source = "",
    [switch]$DryRun,
    [switch]$NoLaunch
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
if (-not $Source) { $Source = Join-Path $root "dist\SoundboardStudio" }

$target = Join-Path $env:LOCALAPPDATA "Programs\Soundboard Studio"
$dataDir = Join-Path $env:APPDATA "Soundboard Studio"
$audioDir = Join-Path $dataDir "audio"
$exeName = "SoundboardStudio.exe"

function Say($msg) { Write-Host $msg }

if (-not (Test-Path (Join-Path $Source $exeName))) {
    throw "Build not found: $Source\$exeName"
}
Say "source : $Source"
Say "target : $target"
Say "data   : $dataDir"

# ---------------------------------------------------------------- 1) stop app
$running = Get-Process SoundboardStudio -ErrorAction SilentlyContinue
if ($running) {
    Say "stopping running instance ..."
    if (-not $DryRun) { $running | Stop-Process -Force; Start-Sleep -Milliseconds 600 }
}

# ---------------------------------------------------------------- 2) data first
# Copy user data out of the old per-user app dir BEFORE mirroring the build.
$sourcesForData = @()
if (Test-Path (Join-Path $target "audio")) { $sourcesForData += (Join-Path $target "audio") }
if (Test-Path (Join-Path $Source "audio")) { $sourcesForData += (Join-Path $Source "audio") }

if (-not $DryRun) {
    New-Item -ItemType Directory -Force $audioDir | Out-Null
}
$existing = @{}
if (Test-Path $audioDir) {
    Get-ChildItem $audioDir -File -ErrorAction SilentlyContinue |
        ForEach-Object { $existing[$_.Name] = $true }
}

$copied = 0
foreach ($dir in $sourcesForData) {
    Get-ChildItem $dir -File -ErrorAction SilentlyContinue | ForEach-Object {
        if ($existing.ContainsKey($_.Name)) { return }
        if ($_.Extension -notin ".wav", ".mp3", ".flac", ".ogg", ".json") { return }
        Say ("  data: " + $_.Name)
        if (-not $DryRun) { Copy-Item $_.FullName (Join-Path $audioDir $_.Name) -Force }
        $existing[$_.Name] = $true
        $copied++
    }
}
# config.json: keep existing, otherwise take the newest one we can find
$configTarget = Join-Path $dataDir "config.json"
if (-not (Test-Path $configTarget)) {
    foreach ($cand in @((Join-Path $target "config.json"), (Join-Path $Source "config.json"))) {
        if (Test-Path $cand) {
            Say "  config: $cand"
            if (-not $DryRun) { Copy-Item $cand $configTarget -Force }
            break
        }
    }
}
Say "data files copied: $copied"

# ---------------------------------------------------------------- 3) app files
if ($DryRun) {
    Say "[dry-run] would mirror $Source -> $target"
} else {
    New-Item -ItemType Directory -Force $target | Out-Null
    # /MIR keeps no stale Qt DLLs; app dir holds no user data since 1.2.0
    $args = @($Source, $target, "/MIR", "/NFL", "/NDL", "/NJH", "/NJS", "/NP", "/R:2", "/W:1")
    & robocopy @args | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "robocopy failed (exit $LASTEXITCODE)" }
    # robocopy mirrors, but make sure the exe really is there
    if (-not (Test-Path (Join-Path $target $exeName))) { throw "copy failed: $exeName missing" }
}

# ---------------------------------------------------------------- 4) shortcuts
function New-Shortcut($path, $exe) {
    if ($DryRun) { Say "[dry-run] shortcut $path"; return }
    $shell = New-Object -ComObject WScript.Shell
    $lnk = $shell.CreateShortcut($path)
    $lnk.TargetPath = $exe
    $lnk.WorkingDirectory = Split-Path -Parent $exe
    $lnk.IconLocation = "$exe,0"
    $lnk.Description = "Soundboard Studio"
    $lnk.Save()
}

$exePath = Join-Path $target $exeName
New-Shortcut (Join-Path ([Environment]::GetFolderPath("Desktop")) "Soundboard Studio.lnk") $exePath
$startMenu = Join-Path ([Environment]::GetFolderPath("Programs")) "Soundboard Studio"
if (-not $DryRun) { New-Item -ItemType Directory -Force $startMenu | Out-Null }
New-Shortcut (Join-Path $startMenu "Soundboard Studio.lnk") $exePath

# ---------------------------------------------------------------- 5) launch
Say ""
Say "updated: $exePath"
if ((-not $NoLaunch) -and (-not $DryRun)) {
    Start-Process -FilePath $exePath -WorkingDirectory $target
}
