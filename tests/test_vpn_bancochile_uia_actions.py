"""Keep GlobalProtect actions on the UIA controls used by the working release."""

from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from features.vpn import switcher_bancodechile as gp


class BancoChileUIAActionsTests(unittest.TestCase):
    def test_uia_button_without_native_handle_can_be_clicked(self):
        # UIA can expose a logical button while Win32 hit-testing returns its
        # hosting HWND. That is not evidence that another window covers it.
        button = Mock(handle=0)
        button.element_info.automation_id = gp.GP_BTN_CONNECT_AUTOID
        button.window_text.return_value = "Connect"
        window = Mock()
        window.descendants.return_value = [button]

        self.assertTrue(gp._gp_invoke_connect_button(window, expected_action="connect"))
        button.click_input.assert_called_once_with()
        button.invoke.assert_not_called()

    def test_native_control_id_cannot_replace_uia_automation_id(self):
        native = Mock()
        native.element_info.automation_id = ""
        native.control_id.return_value = int(gp.GP_BTN_CONNECT_AUTOID)
        uia = Mock()
        uia.element_info.automation_id = gp.GP_BTN_CONNECT_AUTOID
        window = Mock()
        window.descendants.return_value = [native, uia]

        self.assertIs(gp._gp_find_descendant_by_autoid(window, gp.GP_BTN_CONNECT_AUTOID), uia)

    def test_action_window_uses_uia_backend_and_automation_ids(self):
        button = Mock()
        button.element_info.automation_id = gp.GP_BTN_CONNECT_AUTOID
        status = Mock()
        status.element_info.automation_id = gp.GP_STATUS_AUTOID
        window = Mock()
        window.window_text.return_value = gp.GP_TITLE
        window.element_info = SimpleNamespace(class_name=gp.GP_CLASS)
        window.descendants.return_value = [button, status]
        window.is_visible.return_value = True
        desktop = Mock()
        desktop.windows.return_value = [window]

        with patch("pywinauto.Desktop", return_value=desktop) as create_desktop:
            self.assertIs(gp._gp_get_window(timeout=0.2), window)
        create_desktop.assert_called_once_with(backend="uia")


if __name__ == "__main__":
    unittest.main()
