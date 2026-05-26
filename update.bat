@echo off
setlocal EnableDelayedExpansion
title Oracle Tasks Chile - Updating

:: update.bat — lives at repo root (%LOCALAPPDATA%\OracleTasksChile\app\update.bat)
:: Called by the running app on "update available" click.
:: Arg 1: full path to pythonw.exe of the Python that runs the app.

set "PYTHONW=%~1"
if not defined PYTHONW set "PYTHONW=%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe"
if not exist "!PYTHONW!" (
    echo [ERROR] pythonw.exe not found: !PYTHONW!
    pause & exit /b 1
)
set "PY=!PYTHONW:pythonw.exe=python.exe!"

:: Give the calling app time to exit, then force-kill anything left over.
timeout /t 2 /nobreak >nul
taskkill /F /IM pythonw.exe /FI "WINDOWTITLE eq Oracle Tasks Chile" >nul 2>&1
taskkill /F /IM python.exe  /FI "WINDOWTITLE eq Oracle Tasks Chile" >nul 2>&1
timeout /t 1 /nobreak >nul

:: Find git
set "GIT="
where git >nul 2>&1
if not errorlevel 1 (
    for /f "delims=" %%g in ('where git') do (
        if not defined GIT set "GIT=%%g"
    )
)
if not defined GIT if exist "%LOCALAPPDATA%\Programs\Git\cmd\git.exe" set "GIT=%LOCALAPPDATA%\Programs\Git\cmd\git.exe"
if not defined GIT if exist "%ProgramFiles%\Git\cmd\git.exe"           set "GIT=%ProgramFiles%\Git\cmd\git.exe"
if not defined GIT (
    echo [ERROR] git.exe not found.
    pause & exit /b 1
)

cd /d "%~dp0"

echo Fetching latest from origin/main...
"!GIT!" fetch origin main
if errorlevel 1 (
    echo [ERROR] git fetch failed.
    pause & exit /b 1
)

echo Applying update (reset --hard origin/main)...
"!GIT!" reset --hard origin/main
if errorlevel 1 (
    echo [ERROR] git reset --hard failed.
    pause & exit /b 1
)

echo Updating dependencies...
"!PY!" -m pip install -r requirements.txt
if errorlevel 1 (
    echo [WARN] pip install returned non-zero; app will still launch.
)

echo Relaunching Oracle Tasks Chile...
start "" "!PYTHONW!" "%~dp0src\main.py"
exit /b 0
