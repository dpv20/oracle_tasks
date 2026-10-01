from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


SRC_DIR = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_DIR))

from features.vpn import controller  # noqa: E402


class FortiControllerTests(unittest.TestCase):
    def test_custom_saml_flow_stops_when_forticlient_is_closed(self) -> None:
        with patch.object(controller, "_forti_client_running", return_value=False):
            result = controller._forti_autofill_custom_flow(
                "user@example.com",
                "secret",
                ["username", "password", "mfa"],
            )

        self.assertEqual(result, "closed")


class VPNMonitorStatusTests(unittest.TestCase):
    def test_failed_adapter_probe_keeps_partial_legacy_adapter_fields(self) -> None:
        output = """
Name : Ethernet 4
InterfaceDescription : PANGP Virtual Ethernet Adapter Secure
Status : Up
"""
        with patch.object(controller, "_run", return_value=(1, output, "warning")):
            adapters = controller._get_adapter_status()

        self.assertFalse(adapters["_probe_ok"])
        self.assertEqual(adapters["globalprotect"], "Up")

    def test_monitor_uses_adapter_snapshot_without_globalprotect_uia(self) -> None:
        instance = controller.VPNController({})

        with (
            patch.object(instance, "_cisco_connected", return_value=False),
            patch.object(instance, "_forti_connected", return_value=False),
            patch.object(
                controller,
                "_get_adapter_status",
                return_value={"_probe_ok": True, "globalprotect": "Up"},
            ) as adapters,
            patch.object(instance, "_gp_connected") as gp_connected,
        ):
            status = instance.get_monitor_status()

        self.assertEqual(status, controller.GPROT)
        adapters.assert_called_once_with()
        gp_connected.assert_not_called()

    def test_monitor_returns_unknown_when_adapter_probe_fails(self) -> None:
        instance = controller.VPNController({})

        with (
            patch.object(instance, "_cisco_connected", return_value=False),
            patch.object(
                controller,
                "_get_adapter_status",
                return_value={"_probe_ok": False},
            ),
            patch.object(instance, "_forti_connected") as forti_connected,
            patch.object(instance, "_gp_connected") as gp_connected,
        ):
            status = instance.get_monitor_status()

        self.assertIsNone(status)
        forti_connected.assert_not_called()
        gp_connected.assert_not_called()


if __name__ == "__main__":
    unittest.main()
