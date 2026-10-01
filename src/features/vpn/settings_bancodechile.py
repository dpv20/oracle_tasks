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

        ctk.CTkLabel(
            body,
            text=t("settings.vpn.flow_mode"),
            anchor="w",
        ).grid(row=5, column=0, sticky="w", padx=(10, 8), pady=5)
        flow_values = [
            t("settings.vpn.flow_detect"),
            t("settings.vpn.flow_custom"),
        ]
        self.flow = ctk.CTkOptionMenu(body, values=flow_values)
        self.flow.set(
            flow_values[1]
            if self.app.config.get("bancochile_flow_mode", "detect") == "custom"
            else flow_values[0]
        )
        self.flow.grid(row=5, column=1, sticky="ew", padx=(0, 10), pady=5)

        steps = self.app.config.get(
            "bancochile_flow_steps", ["account", "password", "mfa"]
        )
        self.step_account = ctk.BooleanVar(value="account" in steps)
        self.step_password = ctk.BooleanVar(value="password" in steps)
        self.step_mfa = ctk.BooleanVar(value="mfa" in steps)
        ctk.CTkLabel(
            body,
            text=t("settings.vpn.flow_steps"),
            anchor="w",
        ).grid(row=6, column=0, sticky="nw", padx=(10, 8), pady=7)
        step_row = ctk.CTkFrame(body, fg_color="transparent")
        step_row.grid(row=6, column=1, sticky="w", padx=(0, 10), pady=5)
        for label, variable in (
            (t("settings.vpn.step_account"), self.step_account),
            (t("settings.vpn.step_password"), self.step_password),
            (t("settings.vpn.step_mfa"), self.step_mfa),
        ):
            ctk.CTkCheckBox(step_row, text=label, variable=variable).pack(
                side="left", padx=(0, 12)
            )
        ctk.CTkLabel(
            body,
            text=t("settings.vpn.bancochile_account_hint"),
            anchor="w",
            justify="left",
            wraplength=640,
            text_color=("#475569", "#94a3b8"),
        ).grid(row=7, column=0, columnspan=2, sticky="ew", padx=10, pady=(0, 4))

        self.visible = ctk.BooleanVar(
            value=bool(self.app.config.get("vpn_show_bancochile", True))
        )
        ctk.CTkCheckBox(
            body,
            text=t("settings.vpn.show_bancochile"),
            variable=self.visible,
        ).grid(row=8, column=0, columnspan=2, sticky="w", padx=10, pady=8)

    def save(self) -> None:
        steps: list[str] = []
        if self.step_account.get():
            steps.append("account")
        if self.step_password.get():
            steps.append("password")
        if self.step_mfa.get():
            steps.append("mfa")
        flow_mode = (
            "custom"
            if self.flow.get() == t("settings.vpn.flow_custom")
            else "detect"
        )
        self.app.config.update({
            "bancochile_username": self.username.get().strip(),
            "bancochile_password_enc": encrypt_password(self.password.get()),
            "bancochile_portal_url": self.portal.get().strip()
            or "bchmfa.bancochile.cl",
            "bancochile_gp_exe_path": self.gp_exe.get().strip(),
            "bancochile_flow_mode": flow_mode,
            "bancochile_flow_steps": steps,
            "vpn_show_bancochile": bool(self.visible.get()),
        })
