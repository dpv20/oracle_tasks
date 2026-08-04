from __future__ import annotations

import logging
import sys
import tempfile
import unittest
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from unittest.mock import patch


SRC_DIR = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_DIR))

from infra import logger as logger_module  # noqa: E402


class LoggerTests(unittest.TestCase):
    def test_clear_log_keeps_rotating_handler_usable(self) -> None:
        root = logging.getLogger()
        with tempfile.TemporaryDirectory() as temp_dir:
            log_file = Path(temp_dir) / "app.log"
            handler = RotatingFileHandler(log_file, encoding="utf-8")
            root.addHandler(handler)
            try:
                root.warning("before-clear")
                handler.flush()
                with patch.object(logger_module, "LOG_FILE", log_file):
                    logger_module.clear_log()
                root.warning("after-clear")
                handler.flush()
                contents = log_file.read_text(encoding="utf-8")
            finally:
                root.removeHandler(handler)
                handler.close()

        self.assertNotIn("before-clear", contents)
        self.assertIn("after-clear", contents)

    def test_export_log_includes_rotated_history_oldest_first(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            log_file = Path(temp_dir) / "app.log"
            log_file.write_text("active", encoding="utf-8")
            log_file.with_name("app.log.1").write_text("newer", encoding="utf-8")
            log_file.with_name("app.log.2").write_text("older", encoding="utf-8")
            destination = Path(temp_dir) / "exported.log"

            with (
                patch.object(logger_module, "LOG_FILE", log_file),
                patch.object(logger_module, "LOG_BACKUP_COUNT", 2),
            ):
                logger_module.export_log(destination)

            contents = destination.read_text(encoding="utf-8")

        self.assertLess(contents.index("older"), contents.index("newer"))
        self.assertLess(contents.index("newer"), contents.index("active"))
        self.assertIn("===== app.log.2 =====", contents)

    def test_clear_log_removes_rotated_history(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            log_file = Path(temp_dir) / "app.log"
            log_file.write_text("active", encoding="utf-8")
            backup = log_file.with_name("app.log.1")
            backup.write_text("old", encoding="utf-8")

            with (
                patch.object(logger_module, "LOG_FILE", log_file),
                patch.object(logger_module, "DATA_DIR", Path(temp_dir)),
                patch.object(logger_module, "LOG_BACKUP_COUNT", 1),
            ):
                logger_module.clear_log()

            contents = log_file.read_text(encoding="utf-8")
            backup_exists = backup.exists()

        self.assertFalse(backup_exists)
        self.assertEqual(contents, "")

    def test_prune_old_log_records_keeps_only_last_ninety_days(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            log_file = Path(temp_dir) / "app.log"
            backup = log_file.with_name("app.log.1")
            log_file.write_text(
                "2026-04-01 08:00:00,000 [ERROR] old: old active record\n"
                "Traceback from the old record\n"
                "2026-08-01 09:00:00,000 [INFO] current: recent record\n"
                "Recent continuation\n",
                encoding="utf-8",
            )
            backup.write_text(
                "2026-03-01 07:00:00,000 [INFO] old: old backup record\n",
                encoding="utf-8",
            )

            with (
                patch.object(logger_module, "LOG_FILE", log_file),
                patch.object(logger_module, "LOG_BACKUP_COUNT", 1),
            ):
                kept, removed = logger_module.prune_old_log_records(
                    now=datetime(2026, 8, 4, 12, 0, 0)
                )

            contents = log_file.read_text(encoding="utf-8")

        self.assertNotIn("old active record", contents)
        self.assertNotIn("Traceback from the old record", contents)
        self.assertIn("recent record", contents)
        self.assertIn("Recent continuation", contents)
        self.assertFalse(backup.exists())
        self.assertEqual(kept, 1)
        self.assertEqual(removed, 2)

    def test_prune_old_log_records_keeps_exactly_ninety_days(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            log_file = Path(temp_dir) / "app.log"
            log_file.write_text(
                "2026-05-06 12:00:00,000 [INFO] boundary: keep me\n",
                encoding="utf-8",
            )

            with (
                patch.object(logger_module, "LOG_FILE", log_file),
                patch.object(logger_module, "LOG_BACKUP_COUNT", 0),
            ):
                kept, removed = logger_module.prune_old_log_records(
                    now=datetime(2026, 8, 4, 12, 0, 0)
                )

            contents = log_file.read_text(encoding="utf-8")

        self.assertIn("keep me", contents)
        self.assertEqual(kept, 1)
        self.assertEqual(removed, 0)

    def test_prune_open_active_log_keeps_handler_usable(self) -> None:
        root = logging.getLogger()
        with tempfile.TemporaryDirectory() as temp_dir:
            log_file = Path(temp_dir) / "app.log"
            log_file.write_text(
                "2026-01-01 08:00:00,000 [INFO] old: remove me\n"
                "2026-08-01 08:00:00,000 [INFO] recent: keep me\n",
                encoding="utf-8",
            )
            handler = RotatingFileHandler(log_file, encoding="utf-8")
            handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
            root.addHandler(handler)
            try:
                with (
                    patch.object(logger_module, "LOG_FILE", log_file),
                    patch.object(logger_module, "LOG_BACKUP_COUNT", 0),
                ):
                    logger_module.prune_old_log_records(now=datetime(2026, 8, 4, 12, 0, 0))
                root.warning("after retention")
                handler.flush()
                contents = log_file.read_text(encoding="utf-8")
            finally:
                root.removeHandler(handler)
                handler.close()

        self.assertNotIn("remove me", contents)
        self.assertIn("keep me", contents)
        self.assertIn("after retention", contents)


if __name__ == "__main__":
    unittest.main()
