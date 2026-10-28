@echo off
rem ============================================================================
rem  Install / repair VB-CABLE virtual audio cable.
rem  Runs the bundled VBCABLE_Setup_x64.exe elevated, then checks whether the
rem  CABLE Input / CABLE Output endpoints exist.
rem ============================================================================
setlocal
cd /d "%~dp0"

set "SETUP=%~dp0redist\VBCABLE_Setup_x64.exe"
if not exist "%SETUP%" (
    echo [ERROR] Missing: %SETUP%
    echo Reinstall Soundboard Studio to restore it.
    pause
    exit /b 1
)

echo Installing VB-CABLE (a UAC prompt will appear, click Yes) ...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "Start-Process -FilePath '%SETUP%' -ArgumentList '-i' -Verb RunAs -Wait"

echo.
echo Checking audio endpoints ...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$render='HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio\Render';" ^
  "$capture='HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio\Capture';" ^
  "$prop='{a45c254e-df1c-4efd-8020-67d146a850e0},2';" ^
  "function Find($root,$needle){ foreach($k in Get-ChildItem $root -ErrorAction SilentlyContinue){ $p=Join-Path $k.PSPath 'Properties'; $v=(Get-ItemProperty -Path $p -Name $prop -ErrorAction SilentlyContinue).$prop; if($v -and $v.ToUpper().Contains($needle)){ return $v } } return $null }" ^
  "$i=Find $render 'CABLE INPUT'; $o=Find $capture 'CABLE OUTPUT';" ^
  "if($i){Write-Host ('  [OK] Playback : ' + $i)}else{Write-Host '  [MISSING] CABLE Input'};" ^
  "if($o){Write-Host ('  [OK] Recording: ' + $o)}else{Write-Host '  [MISSING] CABLE Output'};" ^
  "if(-not $i -or -not $o){Write-Host ''; Write-Host 'Driver installed but endpoints not visible yet - please REBOOT once.'; exit 1}"

if errorlevel 1 (
    echo.
    echo Reboot is usually required after installing an audio driver.
    choice /C YN /M "Reboot now"
    if errorlevel 2 goto :done
    shutdown /r /t 15 /c "VB-CABLE installed - reboot required"
) else (
    echo.
    echo VB-CABLE is ready. You can close this window.
)

:done
echo.
pause
endlocal
