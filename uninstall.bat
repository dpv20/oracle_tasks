@echo off
setlocal EnableDelayedExpansion
title Oracle Tasks Chile - Uninstall

echo.
echo ============================================================
echo  Oracle Tasks Chile - Uninstall
echo ============================================================
echo.
echo This will remove:
echo   - %LOCALAPPDATA%\OracleTasksChile\ (app, sqlcl, generated spools)
echo   - %APPDATA%\OracleTasksChile\config.json (your saved credentials)
echo   - Desktop shortcut "Oracle Tasks Chile.lnk"
echo.
echo Python, Git, and SQLcl (if installed elsewhere) will NOT be removed.
echo.
set /p CONFIRM="Type YES to confirm: "
if /i not "!CONFIRM!"=="YES" (
    echo Cancelled.
    pause & exit /b 0
)

:: Kill running instance
taskkill /F /IM pythonw.exe /FI "WINDOWTITLE eq Oracle Tasks Chile" >nul 2>&1
taskkill /F /IM python.exe  /FI "WINDOWTITLE eq Oracle Tasks Chile" >nul 2>&1
timeout /t 1 /nobreak >nul

:: Remove Windows startup registration
reg delete "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v OracleTasksChile /f >nul 2>&1

:: Remove app + data
if exist "%LOCALAPPDATA%\OracleTasksChile" (
    rmdir /s /q "%LOCALAPPDATA%\OracleTasksChile"
    echo [OK] Removed %LOCALAPPDATA%\OracleTasksChile
)
if exist "%APPDATA%\OracleTasksChile" (
    rmdir /s /q "%APPDATA%\OracleTasksChile"
    echo [OK] Removed %APPDATA%\OracleTasksChile
)

:: Remove desktop shortcut
set "DESK_TMP=%TEMP%\otc_uninst_desktop.txt"
powershell -NoProfile -Command "[Environment]::GetFolderPath('Desktop')" > "!DESK_TMP!" 2>nul
set /p DESKTOP=<"!DESK_TMP!"
del /f /q "!DESK_TMP!" >nul 2>&1
if not defined DESKTOP set "DESKTOP=%USERPROFILE%\Desktop"

if exist "!DESKTOP!\Oracle Tasks Chile.lnk" (
    del /f /q "!DESKTOP!\Oracle Tasks Chile.lnk"
    echo [OK] Removed desktop shortcut.
)

set "START_TMP=%TEMP%\otc_uninst_start_menu.txt"
powershell -NoProfile -Command "[Environment]::GetFolderPath('Programs')" > "!START_TMP!" 2>nul
set /p START_MENU=<"!START_TMP!"
del /f /q "!START_TMP!" >nul 2>&1
if not defined START_MENU set "START_MENU=%APPDATA%\Microsoft\Windows\Start Menu\Programs"

set "START_DIR=!START_MENU!\Oracle Tasks Chile"
if exist "!START_DIR!\Oracle Tasks Chile.lnk" (
    del /f /q "!START_DIR!\Oracle Tasks Chile.lnk"
    echo [OK] Removed Start Menu shortcut.
)
if exist "!START_DIR!" rmdir "!START_DIR!" >nul 2>&1

echo.
echo Done.
pause
