"""App-wide logging: rotating file in DATA_DIR/app.log + console (when run from python.exe)."""
import logging
from logging.handlers import RotatingFileHandler

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
