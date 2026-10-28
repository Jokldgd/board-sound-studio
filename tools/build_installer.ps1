# Build Soundboard Studio installer:
#   1) PyInstaller (spec)      -> dist\SoundboardStudio\
#   2) prepare VB-CABLE setup  -> packaging\redist\VBCABLE_Setup_x64.exe
#   3) Inno Setup compile      -> installer\SoundboardStudio-Setup-<ver>.exe
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File .\tools\build_installer.ps1
#   ... -SkipBuild                 # only compile the installer
#   ... -CableZip D:\VBCABLE_Driver_Pack43.zip
#   ... -Iscc "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
#
# Notes:
# * User data in dist\SoundboardStudio (config.json, audio\, studio.log) is
#   backed up before the build and restored afterwards.
# * The VB-CABLE installer is NOT committed to git (see .gitignore); this
#   script downloads it and verifies the Authenticode signature.

param(
    [switch]$SkipBuild,
    [string]$CableZip = "",
    [string]$Iscc = ""
)

$ErrorActionPreference = "Stop"
# Resolve python explicitly: the "python" in PATH may be the Microsoft Store
# stub, which returns no exit code (breaks $LASTEXITCODE checks).
$python = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $python) { throw "python not found in PATH" }
$root = Split-Path -Parent $PSScriptRoot
$dist = Join-Path $root "dist\SoundboardStudio"
$packaging = Join-Path $root "packaging"
$redist = Join-Path $packaging "redist"
$backup = Join-Path $root ".build_backup"
$cableExe = Join-Path $redist "VBCABLE_Setup_x64.exe"
$cableUrl = "https://download.vb-audio.com/Download_CABLE/VBCABLE_Driver_Pack43.zip"

Push-Location $root
try {
    # ---------------------------------------------------------------- 1) build
    if (-not $SkipBuild) {
        Write-Host "[1/3] PyInstaller ..."
        if (Test-Path $dist) {
            Remove-Item -Recurse -Force $backup -ErrorAction SilentlyContinue
            New-Item -ItemType Directory -Force $backup | Out-Null
            foreach ($item in @("config.json", "audio", "studio.log")) {
                $src = Join-Path $dist $item
                if (Test-Path $src) { Copy-Item $src (Join-Path $backup $item) -Recurse -Force }
            }
        }
        Remove-Item -Recurse -Force (Join-Path $root "build"), (Join-Path $root ".tmp") -ErrorAction SilentlyContinue
        New-Item -ItemType Directory -Force (Join-Path $root ".tmp") | Out-Null
        $env:TMP = Join-Path $root ".tmp"
        $env:TEMP = $env:TMP

        & $python -m PyInstaller (Join-Path $packaging "SoundboardStudio.spec") `
            --noconfirm --distpath (Join-Path $root "dist") --workpath (Join-Path $root "build")
        if (-not (Test-Path (Join-Path $dist "SoundboardStudio.exe"))) {
            throw "PyInstaller did not produce dist\SoundboardStudio\SoundboardStudio.exe"
        }

        if (Test-Path $backup) {
            foreach ($item in @("config.json", "audio", "studio.log")) {
                $src = Join-Path $backup $item
                if (Test-Path $src) { Copy-Item $src (Join-Path $dist $item) -Recurse -Force }
            }
            Remove-Item -Recurse -Force $backup
        }
        Write-Host "      -> $dist"
    }

    # ------------------------------------------------------------ 2) VB-CABLE
    Write-Host "[2/3] VB-CABLE setup ..."
    New-Item -ItemType Directory -Force $redist | Out-Null
    if (-not (Test-Path $cableExe)) {
        $zip = $CableZip
        if (-not $zip) {
            New-Item -ItemType Directory -Force (Join-Path $root ".tmp") | Out-Null
            $zip = Join-Path $root ".tmp\VBCABLE_Driver_Pack43.zip"
            Write-Host "      downloading $cableUrl"
            # 用 Python 下载：部分环境下 Invoke-WebRequest 的 TLS 会失败
            & $python (Join-Path $packaging "download_cable.py") $zip $cableUrl | Out-Host
            if (-not (Test-Path $zip) -or (Get-Item $zip).Length -lt 100000) {
                throw "VB-CABLE download failed (check network / pass -CableZip)"
            }
        }
        if (-not (Test-Path $zip)) { throw "VB-CABLE zip not found: $zip" }
        $tmpDir = Join-Path $root ".tmp\vbcable_extract"
        Remove-Item -Recurse -Force $tmpDir -ErrorAction SilentlyContinue
        # 用 Python 解压：Expand-Archive 在重定向输出的环境里会因 Write-Progress 报错
        & $python (Join-Path $packaging "extract_cable.py") $zip $tmpDir | Out-Host
        $found = Get-ChildItem $tmpDir -Recurse -Filter "VBCABLE_Setup_x64.exe" | Select-Object -First 1
        if (-not $found) { throw "VBCABLE_Setup_x64.exe not found inside the zip" }
        Copy-Item $found.FullName $cableExe -Force
        Remove-Item -Recurse -Force $tmpDir -ErrorAction SilentlyContinue
        if ($CableZip -eq "") { Remove-Item $zip -Force -ErrorAction SilentlyContinue }
    }

    # signature check (refuse unknown publisher)
    $sig = Get-AuthenticodeSignature $cableExe
    $subject = ""
    if ($sig.SignerCertificate) { $subject = $sig.SignerCertificate.Subject }
    if ($subject -notmatch "VB-Audio|Vincent Burel|VBAudio") {
        throw "VB-CABLE signature check failed (subject: '$subject'). Refusing to bundle it."
    }
    Write-Host "      signature OK: $subject"

    # --------------------------------------------------------------- 3) Inno
    Write-Host "[3/3] Inno Setup compile ..."
    if (-not $Iscc) {
        $candidates = @(
            "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
            "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
            "${env:ProgramFiles(x86)}\Inno Setup 5\ISCC.exe"
        )
        foreach ($c in $candidates) {
            if (Test-Path $c) { $Iscc = $c; break }
        }
    }
    if (-not $Iscc -or -not (Test-Path $Iscc)) {
        throw @"
Inno Setup (ISCC.exe) not found.
Install it once (6.5+ recommended), then re-run:
  winget install --id JRSoftware.InnoSetup -e
or download: https://jrsoftware.org/isdl.php
"@
    }
    & $Iscc (Join-Path $packaging "setup.iss")
    if ($LASTEXITCODE -ne 0) { throw "ISCC failed (exit $LASTEXITCODE)" }

    $installer = Get-ChildItem (Join-Path $root "installer") -Filter "SoundboardStudio-Setup-*.exe" |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (-not $installer) { throw "installer output not found" }
    Write-Host ""
    Write-Host "BUILD OK: $($installer.FullName)"
    Write-Host ("size {0:N1} MB" -f ($installer.Length / 1MB))
}
finally {
    Pop-Location
}
