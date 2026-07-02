"""Configuration panel owned by the VPN feature."""
from __future__ import annotations

from datetime import datetime
from tkinter import filedialog, messagebox

import customtkinter as ctk

from i18n import t
from infra.logger import clear_log, export_log
from paths import LOG_FILE
from settings.config import decrypt_password, encrypt_password
from ui.widgets import IconButton


class VPNSettingsPanel(ctk.CTkFrame):
    def __init__(self, master, app, on_saved=None) -> None:
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.on_saved = on_saved

        tabs = ctk.CTkTabview(self)
        tabs.pack(fill="both", expand=True, padx=8, pady=(8, 4))
        oracle_name = t("settings.vpn.oracle")
        falabella_name = t("settings.vpn.falabella")
        bice_name = t("settings.vpn.bice")
        diagnostics_name = t("settings.vpn.diagnostics")
        for name in (oracle_name, falabella_name, bice_name, diagnostics_name):
            tabs.add(name)

        self._build_oracle(tabs.tab(oracle_name))
        self._build_falabella(tabs.tab(falabella_name))
        self._build_bice(tabs.tab(bice_name))
        self._build_diagnostics(tabs.tab(diagnostics_name))

        IconButton(
            self,
            text=t("settings.vpn.save"),
            command=self._save,
        ).pack(anchor="e", padx=12, pady=(4, 10))

    @staticmethod
    def _entry(parent, row: int, label: str, value: str = "", *, secret=False):
        ctk.CTkLabel(parent, text=label, anchor="w", width=190).grid(
            row=row,
            column=0,
            sticky="w",
            padx=(10, 8),
            pady=5,
        )
        entry = ctk.CTkEntry(parent, show="*" if secret else "")
        if value:
            entry.insert(0, value)
        entry.grid(row=row, column=1, sticky="ew", padx=(0, 10), pady=5)
        parent.grid_columnconfigure(1, weight=1)
        return entry

    def _build_oracle(self, parent) -> None:
        body = ctk.CTkScrollableFrame(parent, fg_color="transparent")
        body.pack(fill="both", expand=True)
        self.cisco_host = self._entry(
            body, 0, t("settings.vpn.host"), self.app.config.get("cisco_host", "")
        )
        self.cisco_user = self._entry(
            body,
            1,
            t("settings.vpn.username"),
            self.app.config.get("cisco_username", ""),
        )
        self.cisco_password = self._entry(
            body,
            2,
            t("settings.vpn.password"),
            decrypt_password(self.app.config.get("cisco_password_enc", "")),
            secret=True,
        )
        self.cisco_cli = self._entry(
            body,
            3,
            t("settings.vpn.cisco_cli"),
            self.app.config.get("cisco_cli_path", ""),
        )

    def _build_falabella(self, parent) -> None:
        body = ctk.CTkScrollableFrame(parent, fg_color="transparent")
        body.pack(fill="both", expand=True)
        self.forti_user = self._entry(
            body,
            0,
            t("settings.vpn.username"),
            self.app.config.get("forti_username", ""),
        )
        self.forti_password = self._entry(
            body,
            1,
            t("settings.vpn.password"),
            decrypt_password(self.app.config.get("forti_password_enc", "")),
            secret=True,
        )
        self.forti_exe = self._entry(
            body,
            2,
            t("settings.vpn.forti_exe"),
            self.app.config.get("forti_exe_path", ""),
        )
        self.forti_connect = self._entry(
            body,
            3,
            t("settings.vpn.connect_cmd"),
            self.app.config.get("forti_connect_cmd", ""),
        )
        self.forti_disconnect = self._entry(
            body,
            4,
            t("settings.vpn.disconnect_cmd"),
            self.app.config.get("forti_disconnect_cmd", ""),
        )

        ctk.CTkLabel(body, text=t("settings.vpn.flow_mode"), anchor="w").grid(
            row=5, column=0, sticky="w", padx=(10, 8), pady=5
        )
        flow_values = [t("settings.vpn.flow_detect"), t("settings.vpn.flow_custom")]
        self.forti_flow = ctk.CTkOptionMenu(body, values=flow_values)
        self.forti_flow.set(
            flow_values[1]
            if self.app.config.get("forti_flow_mode", "detect") == "custom"
            else flow_values[0]
        )
        self.forti_flow.grid(row=5, column=1, sticky="ew", padx=(0, 10), pady=5)

        steps = self.app.config.get(
            "forti_flow_steps", ["username", "password", "mfa"]
        )
        self.step_email = ctk.BooleanVar(value="username" in steps)
        self.step_password = ctk.BooleanVar(value="password" in steps)
        self.step_mfa = ctk.BooleanVar(value="mfa" in steps)
        ctk.CTkLabel(body, text=t("settings.vpn.flow_steps"), anchor="w").grid(
            row=6, column=0, sticky="nw", padx=(10, 8), pady=7
        )
        step_row = ctk.CTkFrame(body, fg_color="transparent")
        step_row.grid(row=6, column=1, sticky="w", padx=(0, 10), pady=5)
        for label, variable in (
            (t("settings.vpn.step_email"), self.step_email),
            (t("settings.vpn.step_password"), self.step_password),
            (t("settings.vpn.step_mfa"), self.step_mfa),
        ):
            ctk.CTkCheckBox(step_row, text=label, variable=variable).pack(
                side="left", padx=(0, 12)
            )

        self.show_forti = ctk.BooleanVar(
            value=bool(self.app.config.get("vpn_show_forti", True))
        )
        ctk.CTkCheckBox(
            body,
            text=t("settings.vpn.show_forti"),
            variable=self.show_forti,
        ).grid(row=7, column=0, columnspan=2, sticky="w", padx=10, pady=8)

    def _build_bice(self, parent) -> None:
        body = ctk.CTkScrollableFrame(parent, fg_color="transparent")
        body.pack(fill="both", expand=True)
        self.gp_user = self._entry(
            body, 0, t("settings.vpn.username"), self.app.config.get("gp_username", "")
        )
        self.gp_password = self._entry(
            body,
            1,
            t("settings.vpn.password"),
            decrypt_password(self.app.config.get("gp_password_enc", "")),
            secret=True,
        )
        self.gp_portal = self._entry(
            body,
            2,
            t("settings.vpn.gp_portal"),
            self.app.config.get("gp_portal_url", ""),
        )
        self.gp_exe = self._entry(
            body, 3, t("settings.vpn.gp_exe"), self.app.config.get("gp_exe_path", "")
        )
        self.show_bice = ctk.BooleanVar(
            value=bool(self.app.config.get("vpn_show_bice", False))
        )
        ctk.CTkCheckBox(
            body,
            text=t("settings.vpn.show_bice"),
            variable=self.show_bice,
        ).grid(row=4, column=0, columnspan=2, sticky="w", padx=10, pady=8)

    def _build_diagnostics(self, parent) -> None:
        actions = ctk.CTkFrame(parent, fg_color="transparent")
        actions.pack(anchor="w", padx=18, pady=18)
        ctk.CTkButton(
            actions,
            text=t("settings.vpn.save_log"),
            width=180,
            command=self._save_log,
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            actions,
            text=t("settings.vpn.clear_log"),
            width=140,
            fg_color=("#b91c1c", "#991b1b"),
            hover_color=("#991b1b", "#7f1d1d"),
            command=self._clear_log,
        ).pack(side="left")

    def _save_log(self) -> None:
        if not LOG_FILE.is_file():
            messagebox.showwarning(
                t("common.warning"), t("settings.vpn.no_log"), parent=self
            )
            return
        destination = filedialog.asksaveasfilename(
            parent=self,
            title=t("settings.vpn.save_log"),
            defaultextension=".log",
            initialfile=f"oracle-tasks-{datetime.now():%Y%m%d-%H%M%S}.log",
            filetypes=[("Log files", "*.log"), ("All files", "*.*")],
        )
        if not destination:
            return
        try:
            export_log(destination)
            messagebox.showinfo(
                t("common.info"),
                t("settings.vpn.log_saved", path=destination),
                parent=self,
            )
        except OSError as exc:
            messagebox.showerror(t("common.error"), str(exc), parent=self)

    def _clear_log(self) -> None:
        if not messagebox.askyesno(
            t("settings.vpn.clear_log"),
            t("settings.vpn.clear_log_confirm"),
            parent=self,
        ):
            return
        try:
            clear_log()
            messagebox.showinfo(
                t("common.info"), t("settings.vpn.log_cleared"), parent=self
            )
        except OSError as exc:
            messagebox.showerror(t("common.error"), str(exc), parent=self)

    def _save(self) -> None:
        steps = []
        if self.step_email.get():
            steps.append("username")
        if self.step_password.get():
            steps.append("password")
        if self.step_mfa.get():
            steps.append("mfa")
        flow_mode = (
            "custom"
            if self.forti_flow.get() == t("settings.vpn.flow_custom")
            else "detect"
        )
        self.app.config.update({
            "cisco_host": self.cisco_host.get().strip(),
            "cisco_username": self.cisco_user.get().strip(),
            "cisco_password_enc": encrypt_password(self.cisco_password.get()),
            "cisco_cli_path": self.cisco_cli.get().strip(),
            "forti_username": self.forti_user.get().strip(),
            "forti_password_enc": encrypt_password(self.forti_password.get()),
            "forti_exe_path": self.forti_exe.get().strip(),
            "forti_connect_cmd": self.forti_connect.get().strip(),
            "forti_disconnect_cmd": self.forti_disconnect.get().strip(),
            "forti_flow_mode": flow_mode,
            "forti_flow_steps": steps,
            "vpn_show_forti": bool(self.show_forti.get()),
            "gp_username": self.gp_user.get().strip(),
            "gp_password_enc": encrypt_password(self.gp_password.get()),
            "gp_portal_url": self.gp_portal.get().strip() or "ext.bice.cl",
            "gp_exe_path": self.gp_exe.get().strip(),
            "vpn_show_bice": bool(self.show_bice.get()),
        })
        if callable(self.on_saved):
            self.on_saved()
        messagebox.showinfo(
            t("common.info"), t("settings.vpn.saved"), parent=self
        )
