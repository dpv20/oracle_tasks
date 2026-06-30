@echo off
setlocal EnableDelayedExpansion
title Oracle Tasks Chile - Updating

:: update.bat — lives at repo root (%LOCALAPPDATA%\OracleTasksChile\app\update.bat)
:: Called by the running app on "update available" click.
:: Arg 1: optional path to python.exe/pythonw.exe used by the running app.
:: It may be absent or stale, so always resolve it for the current Windows user.

set "PY_CANDIDATE=%~1"
set "PY_INFO=%TEMP%\otc_update_python_!RANDOM!!RANDOM!.txt"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\find_update_python.ps1" -Candidate "!PY_CANDIDATE!" > "!PY_INFO!" 2>nul
if not errorlevel 1 (
    for /f "usebackq tokens=1,* delims==" %%a in ("!PY_INFO!") do (
        if /i "%%a"=="PY" set "PY=%%b"
        if /i "%%a"=="PYTHONW" set "PYTHONW=%%b"
    )
)
del /f /q "!PY_INFO!" >nul 2>&1

if not defined PY (
    echo [ERROR] Could not find a working Python installation for this user.
    echo Run install.bat once to repair Python and the application shortcuts.
    pause & exit /b 1
)
if not exist "!PY!" (
    echo [ERROR] python.exe not found: !PY!
    pause & exit /b 1
)
if not defined PYTHONW set "PYTHONW=!PY!"
if not exist "!PYTHONW!" set "PYTHONW=!PY!"
echo [OK] Using Python: !PY!

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
