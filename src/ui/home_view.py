"""Home view — entry screen with task buttons."""
from __future__ import annotations

import customtkinter as ctk
from tkinter import messagebox

from i18n import t
from version import __version__

from .widgets import IconButton


class HomeView(ctk.CTkFrame):
    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app = app

        # Top toolbar — task buttons (only spools active for now)
        toolbar = ctk.CTkFrame(self, fg_color="transparent")
        toolbar.pack(side="top", fill="x", padx=20, pady=(20, 10))

        IconButton(
            toolbar,
            text=t("home.spools_button"),
            command=self._on_spools,
            width=190,
        ).pack(side="left", padx=(0, 8))

        IconButton(
            toolbar,
            text=t("home.savings_button"),
            command=self._on_savings_accounts,
            width=220,
        ).pack(side="left", padx=(0, 8))

        IconButton(
            toolbar,
            text=t("home.create_branch_button"),
            command=self._on_create_branch,
            width=150,
        ).pack(side="left", padx=(0, 8))

        # Reserved placeholders for future task tiles
        for _ in range(1):
            ph = ctk.CTkButton(
                toolbar,
                text="—",
                state="disabled",
                width=120,
                corner_radius=8,
                height=36,
            )
            ph.pack(side="left", padx=4)

        # Center placeholder
        center = ctk.CTkFrame(self, fg_color="transparent")
        center.pack(fill="both", expand=True)
        ctk.CTkLabel(
            center,
            text=t("home.placeholder"),
            font=ctk.CTkFont(size=14),
            text_color=("gray40", "gray60"),
        ).place(relx=0.5, rely=0.5, anchor="center")

        # Bottom row: Settings (left) + credit (right)
        bottom = ctk.CTkFrame(self, fg_color="transparent")
        bottom.pack(side="bottom", fill="x", padx=20, pady=15)

        IconButton(
            bottom,
            text=t("home.settings_button"),
            command=self._on_settings,
            width=140,
        ).pack(side="left")

        ctk.CTkLabel(
            bottom,
            text=f"{t('home.created_by')}  ·  v{__version__}",
            text_color=("gray40", "gray60"),
            font=ctk.CTkFont(size=11),
        ).pack(side="right")

    def _on_spools(self) -> None:
        self.app.show_view("spools")

    def _on_savings_accounts(self) -> None:
        messagebox.showinfo(t("common.info"), t("home.savings_coming_soon"), parent=self)

    def _on_create_branch(self) -> None:
        messagebox.showinfo(t("common.info"), t("home.create_branch_coming_soon"), parent=self)

    def _on_settings(self) -> None:
        self.app.show_view("settings")
