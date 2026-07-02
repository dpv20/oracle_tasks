"""Current-user Windows startup registration for the installed app."""
from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

from paths import DATA_DIR, REPO_ROOT

log = logging.getLogger(__name__)

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE_NAME = "OracleTasksChile"
LEGACY_RUN_VALUE_NAME = "VPNSwitcher"


def _pythonw_executable(executable: str | Path | None = None) -> Path:
    current = Path(executable or sys.executable)
    if current.name.lower() == "pythonw.exe":
        return current
    pythonw = current.with_name("pythonw.exe")
    return pythonw if pythonw.is_file() else current


def _startup_command(
    repo_root: Path = REPO_ROOT,
    executable: str | Path | None = None,
) -> str:
    script = repo_root / "src" / "main.py"
    return subprocess.list2cmdline(
        [str(_pythonw_executable(executable)), str(script), "--background"]
    )


def _is_installed_copy(repo_root: Path = REPO_ROOT, data_dir: Path = DATA_DIR) -> bool:
    expected = data_dir / "app"
    return os.path.normcase(str(repo_root.resolve())) == os.path.normcase(
        str(expected.resolve())
    )


def sync_startup_registration(enabled: bool = True) -> bool:
    """Apply the current-user startup preference for the installed app."""
    if os.name != "nt" or not _is_installed_copy():
        return False

    try:
        import winreg

        command = _startup_command()
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            current: str | None
            try:
                current = winreg.QueryValueEx(key, RUN_VALUE_NAME)[0]
            except FileNotFoundError:
                current = None
            if enabled and current != command:
                winreg.SetValueEx(key, RUN_VALUE_NAME, 0, winreg.REG_SZ, command)
                log.info("Registered Windows startup command: %s", command)
            elif not enabled and current is not None:
                winreg.DeleteValue(key, RUN_VALUE_NAME)
                log.info("Removed Windows startup registration")
            try:
                winreg.DeleteValue(key, LEGACY_RUN_VALUE_NAME)
                log.info("Removed legacy VPN Switcher startup registration")
            except FileNotFoundError:
                pass
        return True
    except OSError as exc:
        log.warning("Could not register Windows startup: %s", exc)
        return False
