@echo off
rem Open the folder where Soundboard Studio keeps recordings.
setlocal
set "DIR="
if exist "%APPDATA%\Soundboard Studio\audio" set "DIR=%APPDATA%\Soundboard Studio\audio"
if not defined DIR if exist "%LOCALAPPDATA%\Soundboard Studio\audio" set "DIR=%LOCALAPPDATA%\Soundboard Studio\audio"
if not defined DIR if exist "%~dp0data\audio" set "DIR=%~dp0data\audio"
if not defined DIR if exist "%TEMP%\SoundboardStudio-data\audio" set "DIR=%TEMP%\SoundboardStudio-data\audio"

if defined DIR (
    echo Recording folder:
    echo   %DIR%
    start "" explorer "%DIR%"
) else (
    echo Recording folder not found yet.
    echo Expected: %APPDATA%\Soundboard Studio\audio
    pause
)
endlocal
