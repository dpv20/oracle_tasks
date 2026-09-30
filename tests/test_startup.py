from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


SRC_DIR = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_DIR))

from infra.startup import (  # noqa: E402
    LEGACY_RUN_VALUE_NAME,
    RUN_VALUE_NAME,
    _is_installed_copy,
    _startup_command,
    sync_startup_registration,
)
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

    @patch("infra.startup._is_installed_copy", return_value=True)
    @patch("infra.startup._startup_command", return_value="startup-command")
    def test_disabling_startup_removes_new_and_legacy_entries(
        self, _command, _installed
    ) -> None:
        import winreg

        key = Mock()
        context = Mock()
        context.__enter__ = Mock(return_value=key)
        context.__exit__ = Mock(return_value=False)
        with (
            patch.object(winreg, "CreateKey", return_value=context),
            patch.object(winreg, "QueryValueEx", return_value=("old", winreg.REG_SZ)),
            patch.object(winreg, "DeleteValue") as delete_value,
        ):
            self.assertTrue(sync_startup_registration(False))

        delete_value.assert_any_call(key, RUN_VALUE_NAME)
        delete_value.assert_any_call(key, LEGACY_RUN_VALUE_NAME)


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

    def test_vpn_and_settings_callbacks_delegate(self) -> None:
        actions: list[str] = []
        tray = TrayController(
            on_open=lambda: None,
            on_exit=lambda: None,
            open_label="Open",
            exit_label="Exit",
            on_settings=lambda: actions.append("settings"),
            on_vpn=lambda target: actions.append(target),
        )

        tray._settings()
        tray._vpn("forti")

        self.assertEqual(actions, ["settings", "forti"])

    def test_bancochile_menu_item_has_independent_visibility_and_target(self) -> None:
        actions: list[str] = []
        is_visible = [False]

        class _MenuItem:
            def __init__(self, text, action, **options) -> None:
                self.text = text
                self.action = action
                self.visible = options.get("visible")

        class _Menu:
            SEPARATOR = object()

            def __init__(self, *items) -> None:
                self.items = items

        class _Icon:
            def __init__(self, _name, _image, _title, menu) -> None:
                self.menu = menu

            def run_detached(self) -> None:
                pass

        pystray = SimpleNamespace(MenuItem=_MenuItem, Menu=_Menu, Icon=_Icon)
        tray = TrayController(
            on_open=lambda: None,
            on_exit=lambda: None,
            open_label="Open",
            exit_label="Exit",
            on_vpn=actions.append,
            labels={"bancochile": "Banco de Chile VPN"},
            show_bancochile=lambda: is_visible[0],
        )

        with (
            patch.dict(sys.modules, {"pystray": pystray}),
            patch("PIL.Image.open") as open_image,
            patch.object(TrayController, "_render_icon", return_value=object()),
        ):
            open_image.return_value.convert.return_value = object()
            self.assertTrue(tray.start())

        item = next(
            candidate
            for candidate in tray._icon.menu.items
            if getattr(candidate, "text", None) == "Banco de Chile VPN"
        )
        self.assertFalse(item.visible(None))
        is_visible[0] = True
        self.assertTrue(item.visible(None))

        item.action(None, None)

        self.assertEqual(actions, ["bancochile"])


if __name__ == "__main__":
    unittest.main()
