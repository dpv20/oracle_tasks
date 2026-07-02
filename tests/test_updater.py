from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


SRC_DIR = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_DIR))

from infra.updater import (  # noqa: E402
    _CREATE_NEW_CONSOLE,
    get_update_info,
    launch_update,
)


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

    @patch("infra.updater._query_remote_version", return_value="9.0.0")
    @patch("infra.updater._find_git", return_value="git.exe")
    @patch("infra.updater._REPO_ROOT")
    def test_manual_update_check_reports_available(self, repo, _git, _query) -> None:
        repo.__truediv__.return_value.is_dir.return_value = True

        info = get_update_info()

        self.assertTrue(info["ok"])
        self.assertTrue(info["available"])
        self.assertEqual(info["latest"], "9.0.0")


if __name__ == "__main__":
    unittest.main()
