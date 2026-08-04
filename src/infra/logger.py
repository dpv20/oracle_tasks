"""App-wide logging: rotating file in DATA_DIR/app.log + console (when run from python.exe)."""
from datetime import datetime, timedelta
import logging
import re
import sys
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

from paths import LOG_FILE, DATA_DIR


LOG_MAX_BYTES = 5_000_000
LOG_BACKUP_COUNT = 5
LOG_RETENTION_DAYS = 90
LOG_RETENTION_CHECK_SECONDS = 24 * 60 * 60

_LOG_RECORD_START = re.compile(
    r"^(?P<timestamp>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})(?:,\d{3})? \["
)
_retention_thread_started = False
_retention_thread_lock = threading.Lock()


def setup_logger() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    kept_records, removed_records = prune_old_log_records()
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
    logging.getLogger(__name__).info(
        "Log retention finished retention_days=%s kept_records=%s removed_records=%s",
        LOG_RETENTION_DAYS,
        kept_records,
        removed_records,
    )
    _start_retention_thread()


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


def prune_old_log_records(*, now: datetime | None = None) -> tuple[int, int]:
    """Remove log records older than the configured retention period."""
    current = now or datetime.now()
    if current.tzinfo is not None:
        current = current.astimezone().replace(tzinfo=None)
    cutoff = current - timedelta(days=LOG_RETENTION_DAYS)
    kept_total = 0
    removed_total = 0

    handlers = _active_log_handlers()
    for handler in handlers:
        handler.acquire()
    try:
        for source in _existing_log_files_oldest_first():
            if not source.is_file():
                continue
            try:
                contents = source.read_text(encoding="utf-8", errors="replace")
                filtered, kept, removed = _filter_log_records(contents, cutoff)
                kept_total += kept
                removed_total += removed
                if removed == 0:
                    continue
                active_handler = next(
                    (handler for handler in handlers if _handler_path(handler) == source.resolve()),
                    None,
                )
                if source.resolve() == LOG_FILE.resolve() and active_handler is not None:
                    active_handler.flush()
                    active_handler.stream.seek(0)
                    active_handler.stream.truncate()
                    active_handler.stream.write(filtered)
                    active_handler.flush()
                elif filtered:
                    source.write_text(filtered, encoding="utf-8")
                elif source.resolve() == LOG_FILE.resolve():
                    source.write_text("", encoding="utf-8")
                else:
                    source.unlink(missing_ok=True)
            except OSError:
                logging.getLogger(__name__).exception("Could not prune application log file path=%s", source)
    finally:
        for handler in reversed(handlers):
            handler.release()

    return kept_total, removed_total


def _filter_log_records(contents: str, cutoff: datetime) -> tuple[str, int, int]:
    blocks: list[tuple[datetime | None, list[str]]] = []
    current_lines: list[str] = []
    current_timestamp: datetime | None = None

    for line in contents.splitlines(keepends=True):
        match = _LOG_RECORD_START.match(line)
        if match:
            if current_lines:
                blocks.append((current_timestamp, current_lines))
            current_lines = [line]
            try:
                current_timestamp = datetime.strptime(match.group("timestamp"), "%Y-%m-%d %H:%M:%S")
            except ValueError:
                current_timestamp = None
        else:
            current_lines.append(line)
    if current_lines:
        blocks.append((current_timestamp, current_lines))

    kept_blocks: list[str] = []
    kept = 0
    removed = 0
    for timestamp, lines in blocks:
        if timestamp is None or timestamp >= cutoff:
            kept_blocks.extend(lines)
            kept += 1
        else:
            removed += 1
    return "".join(kept_blocks), kept, removed


def _active_log_handlers() -> list[RotatingFileHandler]:
    expected = LOG_FILE.resolve()
    return [
        handler
        for handler in logging.getLogger().handlers
        if isinstance(handler, RotatingFileHandler) and _handler_path(handler) == expected
    ]


def _handler_path(handler: RotatingFileHandler) -> Path:
    return Path(handler.baseFilename).resolve()


def _start_retention_thread() -> None:
    global _retention_thread_started
    with _retention_thread_lock:
        if _retention_thread_started:
            return
        _retention_thread_started = True

    def worker() -> None:
        while True:
            time.sleep(LOG_RETENTION_CHECK_SECONDS)
            try:
                kept, removed = prune_old_log_records()
                logging.getLogger(__name__).info(
                    "Scheduled log retention finished retention_days=%s "
                    "kept_records=%s removed_records=%s",
                    LOG_RETENTION_DAYS,
                    kept,
                    removed,
                )
            except Exception:
                logging.getLogger(__name__).exception("Scheduled log retention failed")

    threading.Thread(target=worker, name="log-retention", daemon=True).start()


def _rotated_log_files() -> list[Path]:
    return [LOG_FILE.with_name(f"{LOG_FILE.name}.{index}") for index in range(1, LOG_BACKUP_COUNT + 1)]


def _existing_log_files_oldest_first() -> list[Path]:
    backups = [path for path in reversed(_rotated_log_files()) if path.is_file()]
    return [*backups, LOG_FILE]
