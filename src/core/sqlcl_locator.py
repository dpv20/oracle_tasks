"""Locate SQLcl on the user's machine.

Cascade:
  1. PATH (`where sql`)
  2. Common install locations
  3. None  → caller decides what to do (prompt, download, etc.)
"""
from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

from paths import DATA_DIR

log = logging.getLogger(__name__)

COMMON_PATHS = [
    Path(os.path.expanduser("~")) / "Desktop" / "sqlcl" / "bin" / "sql.exe",
    Path(os.path.expanduser("~")) / "Desktop" / "sqlcl" / "sqlcl" / "bin" / "sql.exe",
    Path(os.path.expanduser("~")) / "sqlcl" / "bin" / "sql.exe",
    Path("C:/sqlcl/bin/sql.exe"),
    DATA_DIR / "sqlcl" / "bin" / "sql.exe",
    DATA_DIR / "sqlcl" / "sqlcl" / "bin" / "sql.exe",
]


def locate_sqlcl() -> str | None:
    """Return absolute path to sql.exe, or None if not found."""
    found = shutil.which("sql")
    if found:
        p = Path(found)
        # Resolve `.bat`/`.cmd` shims to the real sql.exe sibling when possible
        if p.suffix.lower() in (".bat", ".cmd"):
            sib = p.parent / "sql.exe"
            if sib.exists():
                log.info("SQLcl found via PATH (resolved shim): %s", sib)
                return str(sib)
        log.info("SQLcl found via PATH: %s", p)
        return str(p)

    for cand in COMMON_PATHS:
        if cand.exists():
            log.info("SQLcl found at common location: %s", cand)
            return str(cand)

    log.info("SQLcl not found")
    return None
