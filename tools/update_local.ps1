# Update / install Soundboard Studio for the current user (no admin needed).
#
# 1.3.3 起用户数据就在程序目录（exe 同级）：audio\ + config.json。
# 更新流程（按用户要求）：
#   1) 停掉正在运行的实例
#   2) 把旧版本目录里的 audio\ + config.json 压成一个 zip，放到
#      %LOCALAPPDATA%\SoundboardStudio-backup\data-<时间>.zip
#   3) 只用 /E 复制程序文件（**排除** audio、temp、config.json、studio.log）
#   4) 把 zip 解压回新目录（录音/配置原样回来）
#   5) 建快捷方式并启动
#
# 用法：
#   powershell -ExecutionPolicy Bypass -File .\tools\update_local.ps1
#   ... -DryRun   只打印计划
#   ... -NoLaunch 更新后不启动

param(
    [string]$Source = "",
    [switch]$DryRun,
    [switch]$NoLaunch
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
if (-not $Source) { $Source = Join-Path $root "dist\SoundboardStudio" }

$target = Join-Path $env:LOCALAPPDATA "Programs\Soundboard Studio"
$legacyData = Join-Path $env:APPDATA "Soundboard Studio"     # 1.2.x 的旧位置
$backupRoot = Join-Path $env:LOCALAPPDATA "SoundboardStudio-backup"
$exeName = "SoundboardStudio.exe"

function Say($msg) { Write-Host $msg }

if (-not (Test-Path (Join-Path $Source $exeName))) {
    throw "Build not found: $Source\$exeName"
}
Say "source : $Source"
Say "target : $target"
Say "backup : $backupRoot"

# ---------------------------------------------------------------- 1) stop app
$running = Get-Process SoundboardStudio -ErrorAction SilentlyContinue
if ($running) {
    Say "stopping running instance ..."
    if (-not $DryRun) { $running | Stop-Process -Force; Start-Sleep -Milliseconds 700 }
}

# ---------------------------------------------------------------- 2) backup
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$backupZip = Join-Path $backupRoot "data-$stamp.zip"
$dataFolder = $null
foreach ($cand in @($target, $legacyData)) {
    if ((Test-Path (Join-Path $cand "audio")) -or (Test-Path (Join-Path $cand "config.json"))) {
        $dataFolder = $cand
        break
    }
}

if ($dataFolder) {
    Say "backup user data from: $dataFolder"
    if (-not $DryRun) {
        New-Item -ItemType Directory -Force $backupRoot | Out-Null
        $items = @()
        foreach ($name in @("audio", "config.json")) {
            $path = Join-Path $dataFolder $name
            if (Test-Path $path) { $items += $path }
        }
        if ($items.Count -gt 0) {
            Compress-Archive -Path $items -DestinationPath $backupZip -CompressionLevel Optimal -Force
            Say ("  -> " + $backupZip)
        }
    } else {
        Say "[dry-run] would zip audio\ + config.json to $backupZip"
    }
} else {
    Say "no existing user data (fresh install)"
}

# ---------------------------------------------------------------- 3) app files
if ($DryRun) {
    Say "[dry-run] would copy $Source -> $target (excluding audio/temp/config/log)"
} else {
    New-Item -ItemType Directory -Force $target | Out-Null
    $robocopyArgs = @(
        $Source, $target, "/E",
        "/XD", "audio", "temp",
        "/XF", "config.json", "studio.log", "studio.log.*",
        "/NFL", "/NDL", "/NJH", "/NJS", "/NP", "/R:2", "/W:1"
    )
    & robocopy @robocopyArgs | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "robocopy failed (exit $LASTEXITCODE)" }
    if (-not (Test-Path (Join-Path $target $exeName))) { throw "copy failed: $exeName missing" }
}

# ---------------------------------------------------------------- 4) restore
if (Test-Path $backupZip) {
    Say "restoring user data ..."
    if (-not $DryRun) {
        Expand-Archive -Path $backupZip -DestinationPath $target -Force
        $restoredAudio = Join-Path $target "audio"
        $count = 0
        if (Test-Path $restoredAudio) {
            $count = (Get-ChildItem $restoredAudio -File -ErrorAction SilentlyContinue | Measure-Object).Count
        }
        Say "  restored $count files into $restoredAudio"
    }
} elseif ($dataFolder -and (-not $DryRun)) {
    # 没打 zip 的极端情况：至少把 audio/config 复制过去
    foreach ($name in @("audio", "config.json")) {
        $from = Join-Path $dataFolder $name
        if (Test-Path $from) { Copy-Item $from (Join-Path $target $name) -Recurse -Force }
    }
}

# ---------------------------------------------------------------- 5) shortcuts
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
$desktop = [Environment]::GetFolderPath("Desktop")
$programs = [Environment]::GetFolderPath("Programs")
$startMenu = Join-Path $programs "Soundboard Studio"
if (-not $DryRun) { New-Item -ItemType Directory -Force $startMenu | Out-Null }
New-Shortcut (Join-Path $desktop "Soundboard Studio.lnk") $exePath
New-Shortcut (Join-Path $startMenu "Soundboard Studio.lnk") $exePath

# ---------------------------------------------------------------- 6) launch
Say ""
Say "updated: $exePath"
if ((-not $NoLaunch) -and (-not $DryRun)) {
    Start-Process -FilePath $exePath -WorkingDirectory $target
}
