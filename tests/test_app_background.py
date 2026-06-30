from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from queue import SimpleQueue
from unittest.mock import Mock, patch


SRC_DIR = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_DIR))

from ui.app import OracleTasksApp  # noqa: E402


class AppBackgroundTests(unittest.TestCase):
    def test_window_close_hides_instead_of_destroying(self) -> None:
        app = OracleTasksApp.__new__(OracleTasksApp)
        app.root = Mock()

        app._on_close()

        app.root.withdraw.assert_called_once_with()
        app.root.destroy.assert_not_called()

    def test_exit_stops_tray_and_destroys_window(self) -> None:
        app = OracleTasksApp.__new__(OracleTasksApp)
        app.root = Mock()
        app._tray = Mock()
        app._views = {}
        app._shutting_down = False

        app._exit_application()

        app._tray.stop.assert_called_once_with()
        app.root.destroy.assert_called_once_with()

    def test_show_flag_restores_existing_instance(self) -> None:
        app = OracleTasksApp.__new__(OracleTasksApp)
        app.root = Mock()
        app._background_requests = SimpleQueue()
        app._shutting_down = False
        app._show_window = Mock()

        with tempfile.TemporaryDirectory() as temp_dir:
            show_flag = Path(temp_dir) / "show.flag"
            show_flag.write_text("show", encoding="utf-8")
            with patch("ui.app.SHOW_FLAG_PATH", show_flag):
                app._poll_background_requests()

            self.assertFalse(show_flag.exists())

        app._show_window.assert_called_once_with()
        app.root.after.assert_called_once_with(250, app._poll_background_requests)


if __name__ == "__main__":
    unittest.main()
