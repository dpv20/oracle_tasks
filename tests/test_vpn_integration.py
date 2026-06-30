from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


SRC_DIR = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_DIR))

from vpn_integration import (  # noqa: E402
    CISCO,
    FORTI,
    NONE,
    VPNSwitcherBridge,
    _com_apartment,
    _select_bottom_right_button,
    _read_controller_status,
)


class _CancelFlag:
    def clear(self) -> None:
        pass


class _ControllerModule:
    _autofill_cancel = _CancelFlag()


class _FakeController:
    def __init__(self, status: str = CISCO, disconnect_ok: bool = True) -> None:
        self.status = status
        self.disconnect_ok = disconnect_ok
        self.calls: list[str] = []
        self.config = {}

    def get_status(self) -> str:
        return self.status

    def disconnect_cisco(self):
        self.calls.append("disconnect_cisco")
        if self.disconnect_ok:
            self.status = NONE
        return self.disconnect_ok, "Cisco disconnected" if self.disconnect_ok else "failed"

    def disconnect_forti(self):
        self.calls.append("disconnect_forti")
        self.status = NONE
        return True, "Forti disconnected"

    def disconnect_globalprotect(self):
        self.calls.append("disconnect_globalprotect")
        self.status = NONE
        return True, "GlobalProtect disconnected"

    def connect_cisco(self):
        self.calls.append("connect_cisco")
        self.status = CISCO
        return True, "Cisco connected"

    def connect_forti(self):
        self.calls.append("connect_forti")
        self.status = FORTI
        return True, "Forti connected"

    def connect_globalprotect(self):
        self.calls.append("connect_globalprotect")
        return True, "GlobalProtect connected"


class _TransientFortiController(_FakeController):
    def __init__(self) -> None:
        super().__init__(NONE)
        self.attempts = 0

    def connect_forti(self):
        self.calls.append("connect_forti")
        self.attempts += 1
        if self.attempts == 1:
            return False, "FortiClient launched but window did not appear. Try again."
        self.status = FORTI
        return True, "Forti connected"


def _bridge(controller: _FakeController) -> VPNSwitcherBridge:
    bridge = VPNSwitcherBridge()
    bridge._controller = controller
    bridge._controller_module = _ControllerModule()
    bridge._reload_controller_config = lambda _controller: None
    bridge.ensure_background_running = lambda: (True, "running")
    return bridge


class VPNSwitcherBridgeTests(unittest.TestCase):
    def test_vpn_settings_selects_bottom_right_button(self) -> None:
        buttons = [
            (101, 200, 700),
            (202, 800, 700),
            (303, 900, 100),
        ]

        self.assertEqual(_select_bottom_right_button(buttons), 202)

    def test_com_is_initialized_for_worker_thread(self) -> None:
        pythoncom = SimpleNamespace(
            COINIT_MULTITHREADED=0,
            CoInitializeEx=Mock(),
            CoInitialize=Mock(),
            CoUninitialize=Mock(),
        )

        with patch.dict(sys.modules, {"pythoncom": pythoncom}):
            with _com_apartment():
                pass

        pythoncom.CoInitializeEx.assert_called_once_with(0)
        pythoncom.CoInitialize.assert_not_called()
        pythoncom.CoUninitialize.assert_called_once_with()

    @patch("vpn_integration.time.sleep", return_value=None)
    def test_status_retries_after_transient_disconnected_read(self, _sleep) -> None:
        statuses = iter((NONE, FORTI))
        controller = type("StatusController", (), {"get_status": lambda self: next(statuses)})()

        self.assertEqual(_read_controller_status(controller), FORTI)

    @patch("vpn_integration.time.sleep", return_value=None)
    def test_switch_disconnects_current_vpn_before_connecting_target(self, _sleep) -> None:
        controller = _FakeController(CISCO)
        result = _bridge(controller).switch_to(FORTI)

        self.assertTrue(result.ok)
        self.assertEqual(result.status, FORTI)
        self.assertEqual(controller.calls, ["disconnect_cisco", "connect_forti"])

    @patch("vpn_integration.time.sleep", return_value=None)
    def test_switch_stops_when_current_vpn_cannot_disconnect(self, _sleep) -> None:
        controller = _FakeController(CISCO, disconnect_ok=False)
        result = _bridge(controller).switch_to(FORTI)

        self.assertFalse(result.ok)
        self.assertEqual(result.status, CISCO)
        self.assertEqual(controller.calls, ["disconnect_cisco"])

    @patch("vpn_integration.time.sleep", return_value=None)
    def test_no_vpn_disconnects_active_connection(self, _sleep) -> None:
        controller = _FakeController(FORTI)
        result = _bridge(controller).switch_to(NONE)

        self.assertTrue(result.ok)
        self.assertEqual(result.status, NONE)
        self.assertEqual(controller.calls, ["disconnect_forti"])

    @patch("vpn_integration.time.sleep", return_value=None)
    def test_transient_forti_window_failure_is_retried_once(self, _sleep) -> None:
        controller = _TransientFortiController()
        messages: list[str] = []

        result = _bridge(controller).switch_to(FORTI, messages.append)

        self.assertTrue(result.ok)
        self.assertEqual(result.status, FORTI)
        self.assertEqual(controller.calls, ["connect_forti", "connect_forti"])
        self.assertIn("FortiClient is still starting. Retrying...", messages)


if __name__ == "__main__":
    unittest.main()
