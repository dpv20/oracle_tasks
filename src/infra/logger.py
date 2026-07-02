"""App-wide logging: rotating file in DATA_DIR/app.log + console (when run from python.exe)."""
import logging
import shutil
from logging.handlers import RotatingFileHandler
from pathlib import Path

from paths import LOG_FILE, DATA_DIR


def setup_logger() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        LOG_FILE, maxBytes=2_000_000, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if not any(isinstance(h, RotatingFileHandler) for h in root.handlers):
        root.addHandler(handler)


def export_log(destination: str | Path) -> Path:
    target = Path(destination)
    if not LOG_FILE.is_file():
        raise FileNotFoundError(LOG_FILE)
    for handler in logging.getLogger().handlers:
        if isinstance(handler, RotatingFileHandler):
            handler.flush()
    shutil.copy2(LOG_FILE, target)
    return target


def clear_log() -> None:
    """Truncate the active rotating log without detaching its handler."""
    handled = False
    expected = str(LOG_FILE.resolve()).lower()
    for handler in logging.getLogger().handlers:
        if not isinstance(handler, RotatingFileHandler):
            continue
        if str(Path(handler.baseFilename).resolve()).lower() != expected:
            continue
        handler.acquire()
        try:
            handler.flush()
            handler.stream.seek(0)
            handler.stream.truncate()
            handled = True
        finally:
            handler.release()
    if not handled:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        LOG_FILE.write_text("", encoding="utf-8")
    logging.getLogger(__name__).info("Log cleared from Settings")
