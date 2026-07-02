from __future__ import annotations

import json
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch


SRC_DIR = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_DIR))

from features.vpn.service import VPNService  # noqa: E402
from settings import config as config_module  # noqa: E402


class _Config:
    def __init__(self, data: dict) -> None:
        self.data = data

    def load(self) -> None:
        pass


class VPNConfigTests(unittest.TestCase):
    def test_legacy_vpn_settings_are_migrated(self) -> None:
        legacy = {
            "cisco_host": "vpn.oracle.test",
            "cisco_username": "user",
            "cisco_password": "plain-secret",
            "forti_username": "user@oracle.com",
            "forti_password_enc": "forti-dpapi",
            "gp_portal_url": "ext.bice.cl",
            "show_forti": False,
            "show_gp": True,
            "start_with_windows": False,
        }
        merged = deepcopy(config_module.DEFAULTS)

        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "config.json"
            source.write_text(json.dumps(legacy), encoding="utf-8")
            with (
                patch.object(config_module, "LEGACY_VPN_CONFIG", source),
                patch.object(
                    config_module, "encrypt_password", return_value="cisco-dpapi"
                ),
            ):
                config_module.ConfigManager._migrate_legacy_vpn_settings(merged)

        self.assertEqual(merged["cisco_host"], "vpn.oracle.test")
        self.assertEqual(merged["cisco_password_enc"], "cisco-dpapi")
        self.assertEqual(merged["forti_password_enc"], "forti-dpapi")
        self.assertFalse(merged["vpn_show_forti"])
        self.assertTrue(merged["vpn_show_bice"])
        self.assertFalse(merged["start_with_windows"])

    @patch("features.vpn.service.decrypt_password", return_value="decrypted")
    def test_runtime_controller_receives_decrypted_cisco_password(self, decrypt) -> None:
        service = VPNService(_Config({"cisco_password_enc": "ciphertext"}))

        runtime = service._read_config()

        decrypt.assert_called_once_with("ciphertext")
        self.assertEqual(runtime["cisco_password"], "decrypted")
        self.assertNotIn("cisco_password", service.config.data)


if __name__ == "__main__":
    unittest.main()
