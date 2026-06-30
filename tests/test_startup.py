from __future__ import annotations

import sys
import unittest
from pathlib import Path


SRC_DIR = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_DIR))

from infra.startup import _is_installed_copy, _startup_command  # noqa: E402
from infra.tray import TrayController  # noqa: E402


class StartupRegistrationTests(unittest.TestCase):
    def test_installed_copy_is_detected(self) -> None:
        data_dir = Path(r"C:\Users\Michell Zambrano\AppData\Local\OracleTasksChile")

        self.assertTrue(_is_installed_copy(data_dir / "app", data_dir))
        self.assertFalse(_is_installed_copy(Path(r"C:\work\Spool_maker"), data_dir))

    def test_startup_command_is_hidden_and_quotes_paths(self) -> None:
        repo = Path(r"C:\Users\Michell Zambrano\AppData\Local\OracleTasksChile\app")
        pythonw = Path(
            r"C:\Users\Michell Zambrano\AppData\Local\Programs\Python\Python312\pythonw.exe"
        )

        command = _startup_command(repo, pythonw)

        self.assertIn(f'"{pythonw}"', command)
        self.assertIn(f'"{repo / "src" / "main.py"}"', command)
        self.assertTrue(command.endswith(" --background"))


class TrayControllerTests(unittest.TestCase):
    def test_menu_callbacks_delegate_without_touching_tk(self) -> None:
        actions: list[str] = []
        tray = TrayController(
            on_open=lambda: actions.append("open"),
            on_exit=lambda: actions.append("exit"),
            open_label="Open",
            exit_label="Exit",
        )

        tray._open()
        tray._exit()

        self.assertEqual(actions, ["open", "exit"])


if __name__ == "__main__":
    unittest.main()
