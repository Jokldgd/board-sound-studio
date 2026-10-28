@echo off
rem Recording diagnostic (double-click me).
rem Records 3 seconds, writes "diag-recording.txt" next to this file.
setlocal
cd /d "%~dp0"
set "PY=python"
where py >nul 2>nul && set "PY=py -3"
%PY% "%~dp0tools\diagnose_recording.py" --out "%~dp0诊断结果.txt"
echo.
pause
endlocal
