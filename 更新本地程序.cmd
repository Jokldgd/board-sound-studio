@echo off
rem Update Soundboard Studio for the current user (no admin required).
rem Double-click this file.
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\update_local.ps1" %*
echo.
pause
endlocal
