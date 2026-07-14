from __future__ import annotations

import logging
import sys
import tempfile
import unittest
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


if __name__ == "__main__":
    unittest.main()
