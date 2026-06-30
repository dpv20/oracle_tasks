from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


SRC_DIR = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_DIR))

from infra.updater import _CREATE_NEW_CONSOLE, launch_update  # noqa: E402


class UpdateLaunchTests(unittest.TestCase):
    @patch("infra.updater.subprocess.Popen")
    def test_launch_handles_user_paths_with_spaces(self, popen) -> None:
        updater = Path(
            r"C:\Users\Michell Zambrano\AppData\Local\OracleTasksChile\app\update.bat"
        )
        python = r"C:\Users\Michell Zambrano\AppData\Local\Programs\Python\Python312\pythonw.exe"

        launch_update(updater, python)

        popen.assert_called_once_with(
            ["cmd.exe", "/d", "/c", "call", str(updater), python],
            cwd=str(updater.parent),
            creationflags=_CREATE_NEW_CONSOLE,
        )


if __name__ == "__main__":
    unittest.main()
