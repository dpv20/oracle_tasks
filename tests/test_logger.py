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


if __name__ == "__main__":
    unittest.main()
