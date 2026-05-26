@echo off
setlocal EnableDelayedExpansion
title Oracle Tasks Chile - Setup
cd /d "%~dp0"

echo.
echo ============================================================
echo  Oracle Tasks Chile - Setup
echo ============================================================
echo.

:: ── 1. Check / install Python ────────────────────────────────────────────────
set "PY=python"
python --version >nul 2>&1
if errorlevel 1 goto :INSTALL_PYTHON

for /f "tokens=2" %%v in ('python --version 2^>^&1') do set "PYVER=%%v"
for /f "tokens=1,2 delims=." %%a in ("!PYVER!") do (
    set "PYMAJOR=%%a"
    set "PYMINOR=%%b"
)
if !PYMAJOR! lss 3 goto :PYTHON_TOO_OLD
if !PYMAJOR! equ 3 if !PYMINOR! lss 8 goto :PYTHON_TOO_OLD
echo [OK] Python !PYVER! found.
goto :AFTER_PYTHON

:PYTHON_TOO_OLD
echo.
echo [ERROR] Python !PYVER! is too old. This app requires Python 3.8 or newer.
echo Please uninstall your current Python and run install.bat again.
pause & exit /b 1

:INSTALL_PYTHON
echo Python not found. Downloading and installing Python 3.12...
set "PYTHON_INSTALLER=%TEMP%\python_setup_!RANDOM!!RANDOM!.exe"
powershell -NoProfile -Command "try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.12.4/python-3.12.4-amd64.exe' -OutFile '!PYTHON_INSTALLER!' -UseBasicParsing } catch { Write-Host $_.Exception.Message -ForegroundColor Red; exit 1 }"
if errorlevel 1 (
    echo [ERROR] Failed to download Python installer. Check internet / proxy / firewall.
    if exist "!PYTHON_INSTALLER!" del /f /q "!PYTHON_INSTALLER!" >nul 2>&1
    pause & exit /b 1
)
"!PYTHON_INSTALLER!" /quiet InstallAllUsers=0 PrependPath=1 Include_pip=1
set "PY_RC=!errorlevel!"
if !PY_RC! neq 0 (
    echo [ERROR] Python installer failed with exit code !PY_RC!.
    if exist "!PYTHON_INSTALLER!" del /f /q "!PYTHON_INSTALLER!" >nul 2>&1
    pause & exit /b 1
)
del /f /q "!PYTHON_INSTALLER!" >nul 2>&1
echo [OK] Python installed.

set "PY="
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not defined PY if exist "%ProgramFiles%\Python312\python.exe"        set "PY=%ProgramFiles%\Python312\python.exe"
if not defined PY if exist "%ProgramFiles(x86)%\Python312\python.exe" set "PY=%ProgramFiles(x86)%\Python312\python.exe"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Launcher\py.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Launcher\py.exe -3"
if not defined PY if exist "%SystemRoot%\py.exe" set "PY=%SystemRoot%\py.exe -3"
if not defined PY (
    echo [ERROR] Python was installed but python.exe was not found in expected locations.
    echo Please close this window and run install.bat again.
    pause & exit /b 1
)
echo [OK] Using Python at: !PY!

:AFTER_PYTHON

:: ── 2. Check / install Git ───────────────────────────────────────────────────
set "GIT="
where git >nul 2>&1
if not errorlevel 1 (
    for /f "delims=" %%g in ('where git') do (
        if not defined GIT set "GIT=%%g"
    )
    echo [OK] Git found at !GIT!.
    goto :AFTER_GIT
)

echo Git not found. Downloading and installing Git (per-user, no admin)...
set "GIT_INSTALLER=%TEMP%\git_setup_!RANDOM!!RANDOM!.exe"
powershell -NoProfile -Command "try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri 'https://github.com/git-for-windows/git/releases/download/v2.45.2.windows.1/Git-2.45.2-64-bit.exe' -OutFile '!GIT_INSTALLER!' -UseBasicParsing } catch { Write-Host $_.Exception.Message -ForegroundColor Red; exit 1 }"
if errorlevel 1 (
    echo [ERROR] Failed to download Git installer. Check internet / proxy / firewall.
    if exist "!GIT_INSTALLER!" del /f /q "!GIT_INSTALLER!" >nul 2>&1
    pause & exit /b 1
)

"!GIT_INSTALLER!" /VERYSILENT /NORESTART /SP- /CLOSEAPPLICATIONS /NOCANCEL /COMPONENTS="icons,ext\reg\shellhere,assoc,assoc_sh" /o:PathOption=CmdTools /o:BashTerminalOption=ConHost /o:DefaultBranchOption=main
set "GIT_RC=!errorlevel!"
if !GIT_RC! neq 0 (
    echo [ERROR] Git installer failed with exit code !GIT_RC!.
    if exist "!GIT_INSTALLER!" del /f /q "!GIT_INSTALLER!" >nul 2>&1
    pause & exit /b 1
)
del /f /q "!GIT_INSTALLER!" >nul 2>&1
echo [OK] Git installed.

if exist "%LOCALAPPDATA%\Programs\Git\cmd\git.exe"    set "GIT=%LOCALAPPDATA%\Programs\Git\cmd\git.exe"
if not defined GIT if exist "%ProgramFiles%\Git\cmd\git.exe"       set "GIT=%ProgramFiles%\Git\cmd\git.exe"
if not defined GIT if exist "%ProgramFiles(x86)%\Git\cmd\git.exe" set "GIT=%ProgramFiles(x86)%\Git\cmd\git.exe"
if not defined GIT (
    echo [ERROR] Git was installed but git.exe was not found in expected locations.
    echo Please close this window and run install.bat again.
    pause & exit /b 1
)
echo [OK] Using Git at: !GIT!

:AFTER_GIT

:: ── 3. Clone / update repo in LOCALAPPDATA ──────────────────────────────────
echo.
echo [1/5] Fetching application files from GitHub...
set "INSTALL_DIR=%LOCALAPPDATA%\OracleTasksChile"
set "REPO_DIR=!INSTALL_DIR!\app"
set "REPO_URL=https://github.com/dpv20/oracle_tasks.git"

if not exist "!INSTALL_DIR!" mkdir "!INSTALL_DIR!"

taskkill /F /IM pythonw.exe /FI "WINDOWTITLE eq Oracle Tasks Chile" >nul 2>&1
taskkill /F /IM python.exe  /FI "WINDOWTITLE eq Oracle Tasks Chile" >nul 2>&1
timeout /t 1 /nobreak >nul

if exist "!REPO_DIR!\.git" (
    echo Repo already cloned. Updating to latest main...
    "!GIT!" -C "!REPO_DIR!" fetch origin main
    if errorlevel 1 (
        echo [ERROR] git fetch failed. Check internet / firewall / proxy.
        pause & exit /b 1
    )
    "!GIT!" -C "!REPO_DIR!" reset --hard origin/main
    if errorlevel 1 (
        echo [ERROR] git reset --hard failed.
        pause & exit /b 1
    )
) else (
    if exist "!REPO_DIR!" rmdir /s /q "!REPO_DIR!"
    "!GIT!" clone --depth 1 --branch main "!REPO_URL!" "!REPO_DIR!"
    if errorlevel 1 (
        echo [ERROR] git clone failed. Check internet / firewall / proxy.
        echo Repo URL: !REPO_URL!
        pause & exit /b 1
    )
    "!GIT!" -C "!REPO_DIR!" fetch --unshallow origin main >nul 2>&1
)
echo [OK] Repo ready at !REPO_DIR!.

:: ── 4. Locate SQLcl ──────────────────────────────────────────────────────────
echo.
echo [2/5] Locating SQLcl...
set "SQLCL_PATH="

:: 4a. Check PATH
where sql.exe >nul 2>&1
if not errorlevel 1 (
    for /f "delims=" %%s in ('where sql.exe') do (
        if not defined SQLCL_PATH set "SQLCL_PATH=%%s"
    )
    echo [OK] SQLcl found in PATH: !SQLCL_PATH!
    goto :SQLCL_DONE
)

:: 4b. Check common locations
set "COMMON_PATHS=%USERPROFILE%\Desktop\sqlcl\bin\sql.exe;%USERPROFILE%\sqlcl\bin\sql.exe;C:\sqlcl\bin\sql.exe;!INSTALL_DIR!\sqlcl\bin\sql.exe"
for %%p in ("%USERPROFILE%\Desktop\sqlcl\bin\sql.exe" "%USERPROFILE%\sqlcl\bin\sql.exe" "C:\sqlcl\bin\sql.exe" "!INSTALL_DIR!\sqlcl\bin\sql.exe") do (
    if not defined SQLCL_PATH if exist %%p set "SQLCL_PATH=%%~p"
)
if defined SQLCL_PATH (
    echo [OK] SQLcl found at: !SQLCL_PATH!
    goto :SQLCL_DONE
)

:: 4c. Interactive menu
echo.
echo SQLcl was not found on your system.
echo.
echo Choose an option:
echo   [1] I already have SQLcl - let me enter the path to sql.exe
echo   [2] Download SQLcl from Oracle (~95MB) - recommended
echo   [3] Skip for now (configure later in Settings)
echo.
set /p SQLCL_CHOICE="Your choice [1/2/3]: "

if "!SQLCL_CHOICE!"=="1" goto :SQLCL_MANUAL
if "!SQLCL_CHOICE!"=="2" goto :SQLCL_DOWNLOAD
if "!SQLCL_CHOICE!"=="3" goto :SQLCL_SKIP
echo [WARN] Invalid choice. Skipping for now.
goto :SQLCL_SKIP

:SQLCL_MANUAL
set /p SQLCL_PATH="Full path to sql.exe (e.g. C:\sqlcl\bin\sql.exe): "
if not exist "!SQLCL_PATH!" (
    echo [ERROR] File not found: !SQLCL_PATH!
    echo Skipping for now. Configure in Settings later.
    set "SQLCL_PATH="
)
goto :SQLCL_DONE

:SQLCL_DOWNLOAD
echo Downloading SQLcl from Oracle (this may take a few minutes)...
set "SQLCL_ZIP=%TEMP%\sqlcl_!RANDOM!!RANDOM!.zip"
powershell -NoProfile -Command "try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri 'https://download.oracle.com/otn_software/java/sqldeveloper/sqlcl-latest.zip' -OutFile '!SQLCL_ZIP!' -UseBasicParsing } catch { Write-Host $_.Exception.Message -ForegroundColor Red; exit 1 }"
if errorlevel 1 (
    echo [ERROR] SQLcl download failed. Check internet / firewall / corporate proxy.
    echo You can configure SQLcl path manually later in Settings.
    if exist "!SQLCL_ZIP!" del /f /q "!SQLCL_ZIP!" >nul 2>&1
    set "SQLCL_PATH="
    goto :SQLCL_DONE
)
echo Extracting...
if exist "!INSTALL_DIR!\sqlcl" rmdir /s /q "!INSTALL_DIR!\sqlcl" >nul 2>&1
powershell -NoProfile -Command "Expand-Archive -LiteralPath '!SQLCL_ZIP!' -DestinationPath '!INSTALL_DIR!' -Force"
if errorlevel 1 (
    echo [ERROR] Failed to extract SQLcl zip.
    set "SQLCL_PATH="
    goto :SQLCL_DONE
)
del /f /q "!SQLCL_ZIP!" >nul 2>&1
if exist "!INSTALL_DIR!\sqlcl\bin\sql.exe" (
    set "SQLCL_PATH=!INSTALL_DIR!\sqlcl\bin\sql.exe"
    echo [OK] SQLcl installed at !SQLCL_PATH!
) else (
    echo [WARN] SQLcl extracted but sql.exe not found at expected location.
    set "SQLCL_PATH="
)
goto :SQLCL_DONE

:SQLCL_SKIP
echo [INFO] Skipped SQLcl setup. Configure it later in Settings.
set "SQLCL_PATH="

:SQLCL_DONE

:: ── 5. Install pip dependencies ──────────────────────────────────────────────
echo.
echo [3/5] Installing dependencies (this may take a minute)...
echo.
!PY! -m pip install -r "!REPO_DIR!\requirements.txt"
if errorlevel 1 (
    echo.
    echo [ERROR] Failed to install dependencies. See pip output above.
    pause & exit /b 1
)
echo.
echo [OK] Dependencies installed.

:: ── 5.5. Persist SQLcl path into config.json ─────────────────────────────────
:: Helper script avoids cmd.exe quoting traps with python -c inside if blocks.
if defined SQLCL_PATH call :SAVE_SQLCL_PATH

:: ── 6. Generate .ico from new_icon.png if missing ────────────────────────────
call :MAKE_ICO

:: ── 7. Create shortcut ───────────────────────────────────────────────────────
echo.
echo [4/5] Creating shortcut...
set "SCRIPT=!REPO_DIR!\src\main.py"
set "ICON=!REPO_DIR!\assets\icono.ico"
if not exist "!ICON!" set "ICON=!REPO_DIR!\assets\new_icon.png"

set "DESK_TMP=%TEMP%\otc_desktop.txt"
powershell -NoProfile -Command "[Environment]::GetFolderPath('Desktop')" > "!DESK_TMP!" 2>nul
set /p DESKTOP=<"!DESK_TMP!"
del /f /q "!DESK_TMP!" >nul 2>&1
if not defined DESKTOP set "DESKTOP=%USERPROFILE%\Desktop"
if not exist "!DESKTOP!" set "DESKTOP=%USERPROFILE%\Desktop"

set "PYPATH_TMP=%TEMP%\otc_pypath.txt"
!PY! "!REPO_DIR!\tools\find_pythonw.py" > "!PYPATH_TMP!" 2>nul
set /p PYTHONW=<"!PYPATH_TMP!"
del /f /q "!PYPATH_TMP!" >nul 2>&1
if not exist "!PYTHONW!" (
    echo [ERROR] Could not locate pythonw.exe or python.exe in Python install.
    pause & exit /b 1
)

set "LNK_PATH=!DESKTOP!\Oracle Tasks Chile.lnk"
powershell -NoProfile -Command "$ws=New-Object -ComObject WScript.Shell; $s=$ws.CreateShortcut('!LNK_PATH!'); $s.TargetPath='!PYTHONW!'; $s.Arguments='\"!SCRIPT!\"'; $s.WorkingDirectory='!REPO_DIR!'; $s.IconLocation='!ICON!'; $s.Description='Oracle Tasks Chile'; $s.Save()"
if errorlevel 1 (
    echo [WARN] Could not create desktop shortcut. App is still installed at !REPO_DIR!.
) else (
    echo [OK] Desktop shortcut created: !LNK_PATH!
    if exist "!REPO_DIR!\tools\set_aumid.ps1" (
        powershell -NoProfile -ExecutionPolicy Bypass -File "!REPO_DIR!\tools\set_aumid.ps1" -LnkPath "!LNK_PATH!" -AUMID "Oracle.OracleTasksChile.1" >nul 2>&1
        if errorlevel 1 (
            echo [WARN] Could not set AppUserModelID on shortcut; taskbar pin may show Python icon.
        ) else (
            echo [OK] Shortcut AppUserModelID set.
        )
    )
)

:: ── Done ─────────────────────────────────────────────────────────────────────
echo.
echo [5/5] Setup complete!
echo.
echo ============================================================
echo  Oracle Tasks Chile is ready!
echo.
echo   Desktop shortcut  : Oracle Tasks Chile.lnk
echo   Installed at      : !REPO_DIR!
echo   Auto-update       : enabled (git pull on new version)
echo   To uninstall      : run uninstall.bat
echo   You can now delete this setup folder.
echo ============================================================
echo.

echo Launching Oracle Tasks Chile...
start "" "!PYTHONW!" "!SCRIPT!"
echo.
pause
exit /b 0

:: ── Subroutines ──────────────────────────────────────────────────────────────

:SAVE_SQLCL_PATH
!PY! "!REPO_DIR!\tools\save_sqlcl_path.py" "!SQLCL_PATH!"
if errorlevel 1 (
    echo [WARN] Could not write SQLcl path to config.json. Set it manually in Settings.
) else (
    echo [OK] SQLcl path saved to config.
)
exit /b 0

:MAKE_ICO
if exist "!REPO_DIR!\assets\icono.ico" exit /b 0
if not exist "!REPO_DIR!\assets\new_icon.png" exit /b 0
echo Generating icono.ico from new_icon.png...
!PY! "!REPO_DIR!\tools\make_ico.py" "!REPO_DIR!\assets\new_icon.png" "!REPO_DIR!\assets\icono.ico" >nul 2>&1
if errorlevel 1 echo [WARN] Could not generate .ico (Pillow may not be installed yet).
exit /b 0
