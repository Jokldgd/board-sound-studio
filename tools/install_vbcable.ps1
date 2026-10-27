# VB-CABLE 虚拟声卡 一键安装 / 校验脚本（Windows）
#
# 用法（必须管理员）：
#   1) 右键「开始菜单」→「终端(管理员)」或「Windows PowerShell(管理员)」
#   2) cd F:\BoardSound\soundboard_studio
#   3) powershell -ExecutionPolicy Bypass -File .\tools\install_vbcable.ps1
#
# 它做四件事：
#   · 下载官方包（download.vb-audio.com，强制 TLS 1.2）
#   · 校验安装器的数字签名（必须是 VB-Audio，防中间人替换）
#   · 静默安装（VBCABLE_Setup_x64.exe -i -h）
#   · 用本项目的设备枚举确认 CABLE Input / CABLE Output 真的出现了
#
# 卸载：powershell -ExecutionPolicy Bypass -File .\tools\install_vbcable.ps1 -Uninstall
# 已手动下好包：加 -SkipDownload，并把 zip 放到 $env:TEMP\VBCABLE_install\

param(
    [string]$Work = "$env:TEMP\VBCABLE_install",
    [switch]$SkipDownload,
    [switch]$Uninstall
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

function Write-Step($text) { Write-Host ""; Write-Host "== $text ==" -ForegroundColor Cyan }
function Write-Ok($text)   { Write-Host "   [OK] $text" -ForegroundColor Green }
function Write-Warn($text) { Write-Host "   [!]  $text" -ForegroundColor Yellow }
function Write-Err($text)  { Write-Host "   [X]  $text" -ForegroundColor Red }

$Url = 'https://download.vb-audio.com/Download_CABLE/VBCABLE_Driver_Pack43.zip'
$Zip = Join-Path $Work 'VBCABLE_Driver_Pack43.zip'
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)

Write-Step '检查管理员权限'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Err '需要管理员权限。请用「以管理员身份运行」的终端重跑本脚本。'
    exit 1
}
Write-Ok ("已是管理员（{0}）" -f $identity.Name)

New-Item -ItemType Directory -Force -Path $Work | Out-Null

if (-not $SkipDownload) {
    Write-Step '下载官方安装包'
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $downloaded = $false
    try {
        Invoke-WebRequest -Uri $Url -OutFile $Zip -UseBasicParsing -TimeoutSec 180
        $downloaded = $true
        Write-Ok ("下载完成：{0}" -f $Zip)
    } catch {
        Write-Warn ("PowerShell 下载失败：{0}" -f $_.Exception.Message)
    }
    if (-not $downloaded) {
        Write-Step '改用 curl 重试'
        & curl.exe -L --ssl-no-revoke --max-time 300 -o $Zip $Url
        if ($LASTEXITCODE -eq 0 -and (Test-Path $Zip)) {
            $downloaded = $true
            Write-Ok 'curl 下载完成'
        }
    }
    if (-not $downloaded) {
        Write-Err '下载失败。请手动从 https://vb-audio.com/Cable/ 下载 VBCABLE_Driver_Pack43.zip'
        Write-Err ("放到 {0} 后加 -SkipDownload 重跑" -f $Zip)
        exit 2
    }
} else {
    if (-not (Test-Path $Zip)) {
        Write-Err ("找不到 {0}（去掉 -SkipDownload 让脚本自己下，或把 zip 放到该路径）" -f $Zip)
        exit 2
    }
    Write-Ok ("使用已存在的安装包：{0}" -f $Zip)
}

Write-Host ("   SHA256: {0}" -f (Get-FileHash -Algorithm SHA256 -Path $Zip).Hash)

Write-Step '解压'
$extract = Join-Path $Work 'extracted'
if (Test-Path $extract) { Remove-Item -Recurse -Force $extract }
Expand-Archive -Path $Zip -DestinationPath $extract -Force
Write-Ok ("已解压到 {0}" -f $extract)

$setup = Get-ChildItem -Path $extract -Recurse -Filter 'VBCABLE_Setup_x64.exe' | Select-Object -First 1
if (-not $setup) {
    Write-Err '解压后没找到 VBCABLE_Setup_x64.exe（压缩包结构异常？）'
    exit 3
}

Write-Step '校验数字签名（防止安装包被替换）'
$sig = Get-AuthenticodeSignature -FilePath $setup.FullName
$subject = ''
if ($sig.SignerCertificate) { $subject = $sig.SignerCertificate.Subject }
Write-Host ("   签名状态: {0}" -f $sig.Status)
Write-Host ("   签名主体: {0}" -f $subject)
if ($sig.Status -ne 'Valid' -or $subject -notmatch 'VB-Audio|Vincent Burel|VBAudio') {
    Write-Err '签名无效或主体不是 VB-Audio —— 出于安全考虑，已停止安装。'
    exit 4
}
Write-Ok '签名有效且为 VB-Audio'

if ($Uninstall) {
    Write-Step '卸载 VB-CABLE'
    Start-Process -FilePath $setup.FullName -ArgumentList '-u', '-h' -Wait
    Write-Ok '卸载命令已执行（可能建议重启）'
} else {
    Write-Step '静默安装（驱动安装，可能需要几秒到十几秒）'
    Start-Process -FilePath $setup.FullName -ArgumentList '-i', '-h' -Wait
    Write-Ok '安装程序已结束'
}

Write-Step '重启音频服务（让端点立刻出现）'
foreach ($service in @('AudioEndpointBuilder', 'Audiosrv')) {
    try {
        Restart-Service -Name $service -Force -ErrorAction Stop
        Write-Ok ("{0} 已重启" -f $service)
    } catch {
        Write-Warn ("{0} 重启失败（{1}），注销/重启一次即可" -f $service, $_.Exception.Message)
    }
}

Write-Step '用本项目的设备枚举确认端点'
$python = Get-Command python -ErrorAction SilentlyContinue
if ($python) {
    $probePath = Join-Path $env:TEMP 'vbcheck.py'
    $probeLines = @(
        'import sys, os',
        'sys.path.insert(0, os.environ.get("STUDIO_ROOT", "."))',
        'from core import device_enum as de',
        'rules = de.merge_rules(None)',
        'status = de.detect_vb_cable(rules, show_all=True)',
        'print("  " + status.summary())',
        'print("  playback:", status.playback.name if status.playback else "(missing)")',
        'print("  capture :", status.capture.name if status.capture else "(missing)")',
        'raise SystemExit(0 if status.complete else 5)'
    )
    $probeLines -join "`n" | Out-File -FilePath $probePath -Encoding ascii
    Push-Location $ProjectRoot
    $code = 5
    try {
        $env:STUDIO_ROOT = $ProjectRoot
        # 端点可能晚一两秒出现：最多等 12 秒
        for ($attempt = 1; $attempt -le 6; $attempt++) {
            & $python.Source $probePath
            $code = $LASTEXITCODE
            if ($code -eq 0) { break }
            Start-Sleep -Seconds 2
        }
    } finally {
        Pop-Location
    }
    if ($code -eq 0) {
        Write-Ok 'CABLE Input 与 CABLE Output 都已出现'
    } else {
        Write-Warn '端点尚未全部出现：多数情况下注销/重启一次就好'
    }
} else {
    Write-Warn 'PATH 里没有 python，跳过自动校验。装完打开本程序看顶栏徽章即可。'
}

Write-Step '接下来（在本程序里）'
Write-Host '   1. 顶栏路由选「虚拟声卡（推荐，本地通过系统监听）」'
Write-Host '   2. 顶栏应显示绿色「输出 → CABLE Input」；点「测试音」图标'
Write-Host '   3. 本地听不到 → Win+R → control mmsys.cpl → 录制 → CABLE Output → 属性'
Write-Host '      →「侦听」→ 勾选「侦听此设备」→「通过此设备播放」选耳机 → 应用'
Write-Host '   4. 语音软件输入设备选「CABLE Output (VB-Audio Virtual Cable)」，关掉降噪'
Write-Host '   5. 再点一次「测试音」：本地听到 + 对面听到 = 配置完成'

Write-Host ""
Write-Host '完成。若音频设备暂时不生效，注销一次或重启。' -ForegroundColor Cyan
