param(
    [string]$Name = "SoundboardStudio",
    [switch]$KeepBackup
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$dist = Join-Path $root "dist\$Name"
$staging = Join-Path $root "dist_staging"
$backup = Join-Path $root ".build_backup"

Push-Location $root
try {
    if (Test-Path $dist) {
        Remove-Item -Recurse -Force $backup -ErrorAction SilentlyContinue
        New-Item -ItemType Directory -Force $backup | Out-Null
        foreach ($item in @("config.json", "audio", "studio.log")) {
            $src = Join-Path $dist $item
            if (Test-Path $src) {
                Copy-Item $src (Join-Path $backup $item) -Recurse -Force
                Write-Host "[backup] $item"
            }
        }
    }

    Remove-Item -Recurse -Force $staging, (Join-Path $root "build"), (Join-Path $root ".tmp") -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force (Join-Path $root ".tmp") | Out-Null
    $env:TMP = Join-Path $root ".tmp"
    $env:TEMP = $env:TMP

    $args = @(
        "-m", "PyInstaller", "--noconfirm", "--windowed", "--name", $Name,
        "--icon", (Join-Path $root "assets\icon.ico"),
        "--add-data", "assets;assets",
        "--add-data", "resources;resources",
        "--collect-all", "pyaudiowpatch",
        "--collect-all", "pycaw",
        "--collect-all", "comtypes",
        "--collect-binaries", "sounddevice",
        "--collect-binaries", "soundfile",
        "--collect-data", "soundfile",
        "--hidden-import", "psutil",
        "--distpath", $staging,
        "main.py"
    )
    & python @pyArgs
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed (exit $LASTEXITCODE)" }

    if (-not (Test-Path (Join-Path $staging $Name))) { throw "missing build output" }
    Remove-Item -Recurse -Force $dist -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force (Split-Path -Parent $dist) | Out-Null
    Move-Item (Join-Path $staging $Name) $dist

    if (Test-Path $backup) {
        foreach ($item in @("config.json", "audio", "studio.log")) {
            $src = Join-Path $backup $item
            if (Test-Path $src) {
                Copy-Item $src (Join-Path $dist $item) -Recurse -Force
                Write-Host "[restore] $item"
            }
        }
        if (-not $KeepBackup) { Remove-Item -Recurse -Force $backup }
    }

    Remove-Item -Recurse -Force $staging, (Join-Path $root "build"), (Join-Path $root ".tmp") -ErrorAction SilentlyContinue
    $exe = Join-Path $dist "$Name.exe"
    Write-Host ""
    Write-Host "BUILD OK: $exe"
    $size = (Get-Item $exe).Length / 1MB
    Write-Host ("size {0:N1} MB" -f $size)
    Write-Host ("wav count: {0}" -f (Get-ChildItem (Join-Path $dist "audio") -File -Filter *.wav -ErrorAction SilentlyContinue | Measure-Object).Count)
}
finally {
    Pop-Location
}
