from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


SRC_DIR = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_DIR))

from features.vpn.service import (  # noqa: E402
    CISCO,
    FORTI,
    NONE,
    VPNService,
    _com_apartment,
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


class _ClosedFortiController(_FakeController):
    def __init__(self) -> None:
        super().__init__(NONE)

    def connect_forti(self):
        self.calls.append("connect_forti")
        return False, "FortiClient was closed before the connection completed."


class _CountingController(_FakeController):
    def __init__(self) -> None:
        super().__init__(NONE)
        self.status_reads = 0

    def get_status(self) -> str:
        self.status_reads += 1
        return self.status


def _bridge(controller: _FakeController) -> VPNService:
    bridge = VPNService()
    bridge._controller = controller
    bridge._controller_module = _ControllerModule()
    bridge._reload_controller_config = lambda _controller: None
    return bridge


class VPNServiceTests(unittest.TestCase):
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

    @patch("features.vpn.service.time.sleep", return_value=None)
    def test_status_retries_after_transient_disconnected_read(self, _sleep) -> None:
        statuses = iter((NONE, FORTI))
        controller = type("StatusController", (), {"get_status": lambda self: next(statuses)})()

        self.assertEqual(_read_controller_status(controller), FORTI)

    @patch("features.vpn.service.time.sleep", return_value=None)
    def test_switch_disconnects_current_vpn_before_connecting_target(self, _sleep) -> None:
        controller = _FakeController(CISCO)
        result = _bridge(controller).switch_to(FORTI)

        self.assertTrue(result.ok)
        self.assertEqual(result.status, FORTI)
        self.assertEqual(controller.calls, ["disconnect_cisco", "connect_forti"])

    @patch("features.vpn.service.time.sleep", return_value=None)
    def test_switch_stops_when_current_vpn_cannot_disconnect(self, _sleep) -> None:
        controller = _FakeController(CISCO, disconnect_ok=False)
        result = _bridge(controller).switch_to(FORTI)

        self.assertFalse(result.ok)
        self.assertEqual(result.status, CISCO)
        self.assertEqual(controller.calls, ["disconnect_cisco"])

    @patch("features.vpn.service.time.sleep", return_value=None)
    def test_no_vpn_disconnects_active_connection(self, _sleep) -> None:
        controller = _FakeController(FORTI)
        result = _bridge(controller).switch_to(NONE)

        self.assertTrue(result.ok)
        self.assertEqual(result.status, NONE)
        self.assertEqual(controller.calls, ["disconnect_forti"])

    @patch("features.vpn.service.time.sleep", return_value=None)
    def test_transient_forti_window_failure_is_retried_once(self, _sleep) -> None:
        controller = _TransientFortiController()
        messages: list[str] = []

        result = _bridge(controller).switch_to(FORTI, messages.append)

        self.assertTrue(result.ok)
        self.assertEqual(result.status, FORTI)
        self.assertEqual(controller.calls, ["connect_forti", "connect_forti"])
        self.assertIn("FortiClient is still starting. Retrying...", messages)

    @patch("features.vpn.service.time.sleep", return_value=None)
    def test_switch_uses_one_status_read_before_connecting(self, _sleep) -> None:
        controller = _CountingController()

        result = _bridge(controller).switch_to(FORTI)

        self.assertTrue(result.ok)
        self.assertEqual(controller.status_reads, 2)

    @patch("features.vpn.service.time.sleep", return_value=None)
    def test_closing_forti_finishes_as_cancelled_without_settle_wait(self, sleep) -> None:
        controller = _ClosedFortiController()

        result = _bridge(controller).switch_to(FORTI)

        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "cancelled")
        self.assertEqual(result.status, NONE)
        sleep.assert_not_called()

    def test_background_status_skips_an_active_operation(self) -> None:
        controller = _CountingController()
        service = _bridge(controller)
        service._operation_lock.acquire()
        try:
            status = service.try_get_status()
        finally:
            service._operation_lock.release()

        self.assertIsNone(status)
        self.assertEqual(controller.status_reads, 0)


if __name__ == "__main__":
    unittest.main()
