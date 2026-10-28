@echo off
rem ============================================================================
rem  以管理员身份运行 Soundboard Studio 安装包，并绕开 "无法创建临时目录 / 错误 5"。
rem
rem  背景：Windows 11 较新版本（含 26200 这类 Insider 构建）默认开启
rem  "Administrator Protection"：提权后的进程可能拿不到当前用户 %TEMP% 的写权限，
rem  于是 Inno Setup 在解压自己的临时文件时报 Error 5。
rem  解决：把 setup 的 TEMP/TMP 指到一个管理员可写的目录（ProgramData）。
rem
rem  用法：双击本文件（会弹 UAC，点"是"）。
rem ============================================================================
setlocal

set "SETUP=%~dp0..\installer\SoundboardStudio-Setup-1.0.0.exe"
if not exist "%SETUP%" (
    echo [ERROR] installer not found: %SETUP%
    pause
    exit /b 1
)

set "TEMP=%ProgramData%\SoundboardStudioSetupTemp"
set "TMP=%TEMP%"
if not exist "%TEMP%" mkdir "%TEMP%" >nul 2>&1

echo Using TEMP=%TEMP%
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "Start-Process -FilePath '%SETUP%' -Verb RunAs"

endlocal
