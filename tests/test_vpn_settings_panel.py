from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call, patch


SRC_DIR = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_DIR))

from features.vpn import settings_panel  # noqa: E402
from features.vpn import settings_bancodechile  # noqa: E402
import i18n  # noqa: E402


class _Value:
    def __init__(self, value) -> None:
        self.value = value

    def get(self):
        return self.value


def _bare_panel():
    config = Mock()
    panel = SimpleNamespace(
        app=SimpleNamespace(config=config),
        on_saved=Mock(),
        cisco_host=_Value("oracle-host"),
        cisco_user=_Value("oracle-user"),
        cisco_password=_Value("oracle-password"),
        cisco_cli=_Value("vpncli.exe"),
        forti_user=_Value("falabella-user"),
        forti_password=_Value("falabella-password"),
        forti_exe=_Value("forti.exe"),
        forti_connect=_Value(""),
        forti_disconnect=_Value(""),
        forti_flow=_Value("detect"),
        step_email=_Value(True),
        step_password=_Value(True),
        step_mfa=_Value(True),
        show_forti=_Value(True),
        gp_user=_Value("bice-user"),
        gp_password=_Value("bice-password"),
        gp_portal=_Value("ext.bice.cl"),
        gp_exe=_Value("PanGPA.exe"),
        show_bice=_Value(True),
        _bancochile_settings=Mock(),
    )
    return panel, config


class VPNSettingsPanelTests(unittest.TestCase):
    def test_vpn_tab_names_are_plain_profile_names(self) -> None:
        previous = i18n.get_language()
        try:
            for language in ("en", "es"):
                with self.subTest(language=language):
                    i18n.set_language(language)
                    diagnostics = "Diagnostics" if language == "en" else "Diagnostico"
                    self.assertEqual(
                        settings_panel.VPNSettingsPanel._tab_names(),
                        (
                            "Oracle",
                            "Falabella",
                            "BICE",
                            "Banco de Chile",
                            diagnostics,
                        ),
                    )
        finally:
            i18n.set_language(previous)

    def test_visibility_checkbox_labels_are_plain_profile_names(self) -> None:
        previous = i18n.get_language()
        try:
            for language in ("en", "es"):
                with self.subTest(language=language):
                    i18n.set_language(language)
                    self.assertEqual(i18n.t("vpn.show_forti"), "Falabella")
                    self.assertEqual(i18n.t("vpn.show_bice"), "BICE")
                    self.assertEqual(
                        i18n.t("vpn.show_bancochile"),
                        "Banco de Chile",
                    )
                    self.assertEqual(
                        i18n.t("settings.vpn.show_forti"),
                        "Falabella",
                    )
                    self.assertEqual(i18n.t("settings.vpn.show_bice"), "BICE")
                    self.assertEqual(
                        i18n.t("settings.vpn.show_bancochile"),
                        "Banco de Chile",
                    )
        finally:
            i18n.set_language(previous)

    def test_show_profile_selects_bancochile_credentials_tab(self) -> None:
        panel = SimpleNamespace(
            tabs=Mock(),
            _profile_tabs={"bancochile": "Banco de Chile"},
        )

        settings_panel.VPNSettingsPanel.show_profile(panel, "bancochile")

        panel.tabs.set.assert_called_once_with("Banco de Chile")

    def test_save_keeps_legacy_payload_and_calls_banco_section_separately(self) -> None:
        panel, config = _bare_panel()

        with (
            patch.object(
                settings_panel,
                "encrypt_password",
                side_effect=lambda value: f"cipher:{value}",
            ) as encrypt,
            patch.object(settings_panel.messagebox, "showinfo"),
            patch.object(settings_panel, "t", side_effect=lambda key, **_kwargs: key),
        ):
            settings_panel.VPNSettingsPanel._save(panel)

        saved = config.update.call_args.args[0]
        self.assertEqual(saved["gp_username"], "bice-user")
        self.assertEqual(saved["gp_password_enc"], "cipher:bice-password")
        self.assertEqual(saved["gp_portal_url"], "ext.bice.cl")
        self.assertFalse(any(key.startswith("bancochile_") for key in saved))
        self.assertEqual(
            [item for item in encrypt.call_args_list if "bice-password" in item.args],
            [call("bice-password")],
        )
        panel._bancochile_settings.save.assert_called_once_with()
        panel.on_saved.assert_called_once_with()

    def test_empty_bancochile_portal_uses_expected_default(self) -> None:
        config = Mock()
        section = SimpleNamespace(
            app=SimpleNamespace(config=config),
            username=_Value("bancochile-user"),
            password=_Value("bancochile-password"),
            portal=_Value("   "),
            gp_exe=_Value("BancoPanGPA.exe"),
            flow=_Value("settings.vpn.flow_custom"),
            step_account=_Value(True),
            step_password=_Value(False),
            step_mfa=_Value(True),
            visible=_Value(True),
        )

        with (
            patch.object(
                settings_bancodechile,
                "encrypt_password",
                return_value="cipher:bancochile-password",
            ),
            patch.object(
                settings_bancodechile,
                "t",
                side_effect=lambda key, **_kwargs: key,
            ),
        ):
            settings_bancodechile.BancoChileSettingsSection.save(section)

        saved = config.update.call_args.args[0]
        self.assertEqual(saved["bancochile_username"], "bancochile-user")
        self.assertEqual(
            saved["bancochile_password_enc"],
            "cipher:bancochile-password",
        )
        self.assertEqual(
            saved["bancochile_portal_url"],
            "bchmfa.bancochile.cl",
        )
        self.assertEqual(saved["bancochile_gp_exe_path"], "BancoPanGPA.exe")
        self.assertEqual(saved["bancochile_flow_mode"], "custom")
        self.assertEqual(saved["bancochile_flow_steps"], ["account", "mfa"])
        self.assertNotIn("gp_username", saved)
        self.assertNotIn("gp_password_enc", saved)


if __name__ == "__main__":
    unittest.main()
