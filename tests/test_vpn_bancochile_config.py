from __future__ import annotations

import sys
import unittest
from pathlib import Path


SRC_DIR = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_DIR))

from features.vpn.config_bancodechile import ensure_bancochile_profile  # noqa: E402


class _Config:
    def __init__(self, values=None) -> None:
        self.values = dict(values or {})
        self.updates: list[dict] = []
        self.read_keys: list[str] = []

    def get(self, key, default=None):
        self.read_keys.append(key)
        return self.values.get(key, default)

    def update(self, values) -> None:
        self.updates.append(dict(values))
        self.values.update(values)


class BancoChileConfigTests(unittest.TestCase):
    def test_defaults_use_only_bancochile_keys(self) -> None:
        config = _Config({"gp_portal_url": "ext.bice.cl"})

        ensure_bancochile_profile(config)

        saved = config.updates[-1]
        self.assertEqual(saved["bancochile_portal_url"], "bchmfa.bancochile.cl")
        self.assertEqual(saved["bancochile_username"], "")
        self.assertEqual(saved["bancochile_password_enc"], "")
        self.assertEqual(saved["bancochile_gp_exe_path"], "")
        self.assertTrue(saved["vpn_show_bancochile"])
        self.assertFalse(any(key.startswith("gp_") for key in saved))

    def test_legacy_bice_profile_is_never_read_or_copied(self) -> None:
        original = {
            "gp_portal_url": "https://BCHMFA.BANCOCHILE.CL/",
            "gp_username": "bancochile-user",
            "gp_password_enc": "ciphertext",
            "gp_exe_path": "PanGPA.exe",
        }
        config = _Config(original)

        ensure_bancochile_profile(config)

        self.assertEqual(config.values["bancochile_username"], "")
        self.assertEqual(config.values["bancochile_password_enc"], "")
        self.assertEqual(config.values["bancochile_gp_exe_path"], "")
        for key, value in original.items():
            self.assertEqual(config.values[key], value)
        self.assertFalse(any(key.startswith("gp_") for key in config.read_keys))

    def test_existing_banco_values_and_hidden_choice_are_preserved(self) -> None:
        config = _Config({
            "bancochile_username": "dedicated-user",
            "bancochile_password_enc": "dedicated-secret",
            "bancochile_portal_url": "custom.bancochile.cl",
            "bancochile_gp_exe_path": "dedicated.exe",
            "vpn_show_bancochile": False,
        })

        ensure_bancochile_profile(config)

        self.assertEqual(config.values["bancochile_username"], "dedicated-user")
        self.assertEqual(config.values["bancochile_password_enc"], "dedicated-secret")
        self.assertEqual(config.values["bancochile_portal_url"], "custom.bancochile.cl")
        self.assertEqual(config.values["bancochile_gp_exe_path"], "dedicated.exe")
        self.assertFalse(config.values["vpn_show_bancochile"])
        self.assertEqual(config.updates, [])


if __name__ == "__main__":
    unittest.main()
