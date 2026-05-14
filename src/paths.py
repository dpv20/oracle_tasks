"""Filesystem paths used across the app.

Conventions:
- Code & assets that ship in the repo live in REPO_ROOT (the directory containing src/).
- User-specific runtime state lives in CONFIG_DIR (%APPDATA%\\OracleTasksChile).
- Generated/cached state (logs, spools_out, downloaded SQLcl) lives in DATA_DIR
  (%LOCALAPPDATA%\\OracleTasksChile).
"""
import os
from pathlib import Path

APP_NAME = "OracleTasksChile"

REPO_ROOT = Path(__file__).resolve().parent.parent

ASSETS_DIR = REPO_ROOT / "assets"
SPOOLS_DIR = REPO_ROOT / "spools"
TOOLS_DIR = REPO_ROOT / "tools"

CONFIG_DIR = Path(os.environ.get("APPDATA", str(Path.home()))) / APP_NAME
CONFIG_FILE = CONFIG_DIR / "config.json"

DATA_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / APP_NAME
SPOOLS_OUT_DIR = DATA_DIR / "spools_out"
SQLCL_DIR = DATA_DIR / "sqlcl"
LOG_FILE = DATA_DIR / "app.log"


def ensure_dirs() -> None:
    """Create all writable directories the app needs at runtime."""
    for d in (CONFIG_DIR, DATA_DIR, SPOOLS_OUT_DIR):
        d.mkdir(parents=True, exist_ok=True)
    for country in ("Chile", "Peru", "Colombia", "Mexico"):
        (SPOOLS_OUT_DIR / country).mkdir(parents=True, exist_ok=True)
