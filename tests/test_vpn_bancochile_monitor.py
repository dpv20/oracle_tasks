from __future__ import annotations

"""Keep passive Banco status independent from the working UIA action path."""

from contextlib import ExitStack
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from features.vpn import switcher_bancodechile as switcher  # noqa: E402


class _NativeControl:
    def __init__(self, control_id: str, text: str):
        self._control_id = int(control_id)
        self.text = text

    def control_id(self):
        return self._control_id

    def window_text(self):
        return self.text

    def is_visible(self):
        return True


class BancoChilePassiveMonitorTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.window = Mock()
        self.window.is_visible.return_value = False
        self.window.descendants.return_value = [
            _NativeControl(switcher.GP_BTN_CONNECT_AUTOID, "Disconnect"),
            _NativeControl(switcher.GP_STATUS_AUTOID, "Connected"),
            _NativeControl(switcher.GP_PORTAL_AUTOIDS[0], "bchmfa"),
        ]
        self.desktop = Mock()
        self.desktop.window.return_value.wrapper_object.return_value = self.window
        self.desktop_factory = self.stack.enter_context(
            patch("pywinauto.Desktop", return_value=self.desktop)
        )
        self.stack.enter_context(
            patch.object(switcher, "_gp_find_main_hwnds", return_value=[1234])
        )
        for name in (
            "_gp_get_window", "_gp_find_descendant_by_autoid", "_gp_get_portal_text",
            "_gp_get_status_text", "_gp_invoke_connect_button", "_open_gui",
        ):
            self.stack.enter_context(
                patch.object(switcher, name, side_effect=AssertionError(f"action path: {name}"))
            )
        self.stack.enter_context(
            patch.object(switcher, "_gp_login_window_present", return_value=False)
        )
        self.instance = switcher.BancoChileSwitcher({})

    def test_monitor_identifies_manual_connection_with_native_controls_only(self):
        self.assertEqual(self.instance.get_bancochile_status("Up"), switcher.BANCOCHILE)
        self.assertTrue(self.instance.is_bancochile_active())
        self.assertEqual(self.instance._gp_connected_target(), switcher.BANCOCHILE)

        self.assertEqual(self.desktop_factory.call_count, 3)
        for call in self.desktop_factory.call_args_list:
            self.assertEqual(call.kwargs, {"backend": "win32"})
        self.desktop.windows.assert_not_called()
        self.window.set_focus.assert_not_called()
        self.window.click_input.assert_not_called()

    def test_terminal_adapter_samples_override_hidden_stale_connected_popup(self):
        self.assertEqual(self.instance.get_bancochile_status("Up"), switcher.BANCOCHILE)
        self.assertEqual(self.instance.get_bancochile_status("Disabled"), switcher.BANCOCHILE)
        self.assertEqual(self.instance.get_bancochile_status("Disabled"), switcher.NONE)
        self.window.set_focus.assert_not_called()
        self.window.click_input.assert_not_called()


if __name__ == "__main__":
    unittest.main()
