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


if __name__ == "__main__":
    unittest.main()
