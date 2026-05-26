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
set "DESK_TMP=%TEMP%\otc_update_desktop.txt"
powershell -NoProfile -Command "[Environment]::GetFolderPath('Desktop')" > "!DESK_TMP!" 2>nul
set /p DESKTOP=<"!DESK_TMP!"
del /f /q "!DESK_TMP!" >nul 2>&1
if not defined DESKTOP set "DESKTOP=%USERPROFILE%\Desktop"

set "START_TMP=%TEMP%\otc_update_start_menu.txt"
powershell -NoProfile -Command "[Environment]::GetFolderPath('Programs')" > "!START_TMP!" 2>nul
set /p START_MENU=<"!START_TMP!"
del /f /q "!START_TMP!" >nul 2>&1
if not defined START_MENU set "START_MENU=%APPDATA%\Microsoft\Windows\Start Menu\Programs"

set "LNK_PATH=!DESKTOP!\Oracle Tasks Chile.lnk"
set "START_LNK_PATH=!START_MENU!\Oracle Tasks Chile\Oracle Tasks Chile.lnk"
if exist "!LNK_PATH!" (
    start "" "!LNK_PATH!"
) else if exist "!START_LNK_PATH!" (
    start "" "!START_LNK_PATH!"
) else (
    start "" "!PYTHONW!" "%~dp0src\main.py"
)
exit /b 0
