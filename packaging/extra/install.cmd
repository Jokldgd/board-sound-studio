@echo off
rem ============================================================================
rem  Soundboard Studio - installer launcher.
rem
rem  Why this file exists: on some Windows 11 builds the elevated installer
rem  cannot create its temp folder under the user's %TEMP% (error 5).
rem  Pointing TEMP/TMP at ProgramData avoids that.
rem
rem  Testers: just double-click this file (one UAC prompt, click Yes).
rem ============================================================================
setlocal

set "SETUP=%~dp0SoundboardStudio-Setup-1.1.0.exe"
if not exist "%SETUP%" (
    for %%F in ("%~dp0SoundboardStudio-Setup-*.exe") do set "SETUP=%%~fF"
)
if not exist "%SETUP%" (
    echo [ERROR] Setup exe not found next to this script.
    pause
    exit /b 1
)

set "TEMP=%ProgramData%\SoundboardStudioSetupTemp"
set "TMP=%TEMP%"
if not exist "%TEMP%" mkdir "%TEMP%" >nul 2>&1

echo Running: %SETUP%
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "Start-Process -FilePath '%SETUP%' -Verb RunAs"

endlocal
