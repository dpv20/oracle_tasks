"""Banco de Chile settings kept separate from every legacy VPN profile."""
from __future__ import annotations

from collections.abc import Callable

import customtkinter as ctk

from i18n import t
from settings.config import decrypt_password, encrypt_password


class BancoChileSettingsSection:
    """Own Banco de Chile widgets and persist only Banco-specific keys."""

    def __init__(self, app) -> None:
        self.app = app

    def build(self, parent, entry: Callable):
        body = ctk.CTkScrollableFrame(parent, fg_color="transparent")
        body.pack(fill="both", expand=True)
        self.username = entry(
            body,
            0,
            t("settings.vpn.username"),
            self.app.config.get("bancochile_username", ""),
        )
        self.password = entry(
            body,
            1,
            t("settings.vpn.password"),
            decrypt_password(self.app.config.get("bancochile_password_enc", "")),
            secret=True,
        )
        self.portal = entry(
            body,
            2,
            t("settings.vpn.gp_portal"),
            self.app.config.get(
                "bancochile_portal_url", "bchmfa.bancochile.cl"
            ),
        )
        ctk.CTkLabel(
            body,
            text=t("settings.vpn.bancochile_mfa_hint"),
            anchor="w",
            justify="left",
            wraplength=640,
            text_color=("#475569", "#94a3b8"),
        ).grid(row=3, column=0, columnspan=2, sticky="ew", padx=10, pady=(8, 4))
        self.gp_exe = entry(
            body,
            4,
            t("settings.vpn.gp_exe"),
            self.app.config.get("bancochile_gp_exe_path", ""),
        )
        self.visible = ctk.BooleanVar(
            value=bool(self.app.config.get("vpn_show_bancochile", True))
        )
        ctk.CTkCheckBox(
            body,
            text=t("settings.vpn.show_bancochile"),
            variable=self.visible,
        ).grid(row=5, column=0, columnspan=2, sticky="w", padx=10, pady=8)

    def save(self) -> None:
        self.app.config.update({
            "bancochile_username": self.username.get().strip(),
            "bancochile_password_enc": encrypt_password(self.password.get()),
            "bancochile_portal_url": self.portal.get().strip()
            or "bchmfa.bancochile.cl",
            "bancochile_gp_exe_path": self.gp_exe.get().strip(),
            "vpn_show_bancochile": bool(self.visible.get()),
        })
