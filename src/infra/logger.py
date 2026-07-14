"""App-wide logging: rotating file in DATA_DIR/app.log + console (when run from python.exe)."""
import logging
import sys
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path

from paths import LOG_FILE, DATA_DIR


LOG_MAX_BYTES = 5_000_000
LOG_BACKUP_COUNT = 5


def setup_logger() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        LOG_FILE, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT, encoding="utf-8"
    )
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s [%(levelname)s] pid=%(process)d thread=%(threadName)s %(name)s: %(message)s"
        )
    )
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if not any(isinstance(h, RotatingFileHandler) for h in root.handlers):
        root.addHandler(handler)
    _install_exception_hooks()
    logging.getLogger(__name__).info(
        "Logging initialized path=%s max_bytes=%s backups=%s",
        LOG_FILE,
        LOG_MAX_BYTES,
        LOG_BACKUP_COUNT,
    )


def _install_exception_hooks() -> None:
    def log_main_exception(exc_type, exc_value, traceback) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, traceback)
            return
        logging.getLogger("uncaught").critical(
            "Unhandled main-thread exception",
            exc_info=(exc_type, exc_value, traceback),
        )

    def log_thread_exception(args) -> None:
        if args.exc_type is SystemExit:
            return
        logging.getLogger("uncaught").critical(
            "Unhandled worker-thread exception thread=%r",
            args.thread.name if args.thread else "<unknown>",
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    def log_unraisable_exception(args) -> None:
        logging.getLogger("uncaught").critical(
            "Unraisable exception object_type=%s error_message=%r",
            type(args.object).__name__ if args.object is not None else "<none>",
            args.err_msg,
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    sys.excepthook = log_main_exception
    threading.excepthook = log_thread_exception
    sys.unraisablehook = log_unraisable_exception


def export_log(destination: str | Path) -> Path:
    target = Path(destination)
    if not LOG_FILE.is_file():
        raise FileNotFoundError(LOG_FILE)
    logging.getLogger(__name__).info("Log export requested destination=%s", target)
    for handler in logging.getLogger().handlers:
        if isinstance(handler, RotatingFileHandler):
            handler.flush()

    sources = _existing_log_files_oldest_first()
    target_resolved = target.resolve()
    if any(source.resolve() == target_resolved for source in sources):
        raise OSError("The exported log cannot overwrite an active application log.")
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("wb") as exported:
        for source in sources:
            exported.write(f"\n===== {source.name} =====\n".encode("utf-8"))
            exported.write(source.read_bytes())
            exported.write(b"\n")
    return target


def clear_log() -> None:
    """Truncate the active log and remove its rotated history."""
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
    for backup in _rotated_log_files():
        backup.unlink(missing_ok=True)
    logging.getLogger(__name__).info("Log cleared from Settings")


def _rotated_log_files() -> list[Path]:
    return [LOG_FILE.with_name(f"{LOG_FILE.name}.{index}") for index in range(1, LOG_BACKUP_COUNT + 1)]


def _existing_log_files_oldest_first() -> list[Path]:
    backups = [path for path in reversed(_rotated_log_files()) if path.is_file()]
    return [*backups, LOG_FILE]
