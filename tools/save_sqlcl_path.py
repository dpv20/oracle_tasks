"""Persist the SQLcl path into %APPDATA%\\OracleTasksChile\\config.json.

Called by install.bat after locating sql.exe. Kept as a standalone script so
install.bat doesn't need a multi-line python -c invocation with quoting that
trips up cmd.exe inside `if (...)` blocks.
"""
import json
import os
import sys


def main() -> int:
    if len(sys.argv) < 2 or not sys.argv[1].strip():
        return 0
    sqlcl_path = sys.argv[1]
    cfg_dir = os.path.join(os.environ.get("APPDATA", ""), "OracleTasksChile")
    cfg_path = os.path.join(cfg_dir, "config.json")
    os.makedirs(cfg_dir, exist_ok=True)
    cfg: dict = {}
    if os.path.exists(cfg_path):
        try:
            with open(cfg_path, encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            pass
    cfg["sqlcl_path"] = sqlcl_path
    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
