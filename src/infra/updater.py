"""Background update check.

Mirrors the vpn app pattern: at startup we run a silent background thread that
does `git fetch origin main` then `git show origin/main:src/version.py` to read
the remote version. If it's higher than the local `__version__`, the caller is
invoked so the UI can surface its update banner.

Every failure mode (no network, no git, repo not git-installed) returns
silently — a routine update check must never bother the user.

The actual update is applied by `update.bat` (launched by the UI on click).
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Callable

from version import __version__

log = logging.getLogger(__name__)

_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_CREATE_NEW_CONSOLE = getattr(subprocess, "CREATE_NEW_CONSOLE", 0x00000010)
# src/infra/updater.py → repo root is two parents up
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _find_git() -> str | None:
    found = shutil.which("git")
    if found:
        return found
    for candidate in (
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Git\cmd\git.exe"),
        os.path.expandvars(r"%ProgramFiles%\Git\cmd\git.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Git\cmd\git.exe"),
    ):
        if os.path.isfile(candidate):
            return candidate
    return None


def _version_tuple(v: str) -> tuple[int, ...]:
    parts: list[int] = []
    for chunk in v.split("."):
        digits = "".join(c for c in chunk if c.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def _query_remote_version(git: str, repo_dir: Path) -> str | None:
    try:
        subprocess.run(
            [git, "-C", str(repo_dir), "fetch", "origin", "main"],
            check=True, capture_output=True, timeout=20,
            creationflags=_CREATE_NO_WINDOW,
        )
        result = subprocess.run(
            [git, "-C", str(repo_dir), "show", "origin/main:src/version.py"],
            check=True, capture_output=True, timeout=10,
            creationflags=_CREATE_NO_WINDOW,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        log.info("Update check: git fetch/show failed: %s", e)
        return None
    remote_src = result.stdout.decode("utf-8", errors="ignore")
    m = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', remote_src)
    return m.group(1) if m else None


def check_for_update(on_update_available: Callable[[str], None]) -> None:
    """Non-blocking update check. Calls `on_update_available(remote_version)`
    once, on the worker thread, only when remote > local. Caller is responsible
    for marshalling that callback onto the UI thread.
    """
    def _worker() -> None:
        try:
            if not (_REPO_ROOT / ".git").is_dir():
                return
            git = _find_git()
            if not git:
                return
            remote = _query_remote_version(git, _REPO_ROOT)
            if not remote:
                return
            if _version_tuple(remote) > _version_tuple(__version__):
                log.info("Update available: %s (local %s)", remote, __version__)
                on_update_available(remote)
        except Exception as e:
            log.info("Update check: unexpected error: %s", e)
    threading.Thread(target=_worker, daemon=True).start()


def launch_update(updater: Path, python_executable: str) -> subprocess.Popen:
    """Launch update.bat in a new console without cmd.exe path splitting."""
    return subprocess.Popen(
        [
            "cmd.exe",
            "/d",
            "/c",
            "call",
            str(updater),
            str(python_executable),
        ],
        cwd=str(updater.parent),
        creationflags=_CREATE_NEW_CONSOLE,
    )
