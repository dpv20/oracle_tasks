"""Oracle Tasks Chile — entry point.

Responsibilities:
- DPI awareness (Windows tells us not to scale)
- AppUserModelID (taskbar groups under our icon, not python.exe)
- Single-instance guard (named mutex + flag file for re-show)
- Hand off to ui.app.OracleTasksApp
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from app_identity import APP_USER_MODEL_ID
from paths import SHOW_FLAG_PATH


def _set_dpi_aware() -> None:
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass


def _set_app_user_model_id() -> None:
    try:
        import ctypes
        set_app_id = ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID
        set_app_id.argtypes = [ctypes.c_wchar_p]
        set_app_id.restype = ctypes.c_long
        set_app_id(APP_USER_MODEL_ID)
    except Exception:
        pass


def _single_instance_guard() -> None:
    """If another instance is running, drop a flag file it'll see and exit silently."""
    try:
        import ctypes
        ctypes.windll.kernel32.CreateMutexW(None, False, "OracleTasksChile_SingleInstance")
        if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
            try:
                SHOW_FLAG_PATH.parent.mkdir(parents=True, exist_ok=True)
                with SHOW_FLAG_PATH.open("w", encoding="utf-8") as f:
                    f.write("show")
            except Exception:
                pass
            sys.exit(0)
        try:
            SHOW_FLAG_PATH.unlink(missing_ok=True)
        except OSError:
            pass
    except Exception:
        pass


def main() -> None:
    _set_dpi_aware()
    _set_app_user_model_id()
    _single_instance_guard()

    from paths import ensure_dirs
    ensure_dirs()

    from infra.logger import setup_logger
    setup_logger()

    from ui.app import OracleTasksApp
    OracleTasksApp(start_hidden="--background" in sys.argv[1:]).run()


if __name__ == "__main__":
    main()
