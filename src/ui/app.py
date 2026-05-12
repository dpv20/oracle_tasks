"""OracleTasksApp — root CTk window with simple view router."""
from __future__ import annotations

import logging
import os

import customtkinter as ctk

from core.config import ConfigManager
from i18n import set_language, t
from paths import ASSETS_DIR
from version import __version__

from .home_view import HomeView
from .settings_view import SettingsView
from .widgets import UpdateBanner

log = logging.getLogger(__name__)


class OracleTasksApp:
    def __init__(self) -> None:
        self.config = ConfigManager()

        # Apply persisted language + theme BEFORE creating widgets
        set_language(self.config.get("language", "en"))
        ctk.set_appearance_mode(self.config.get("theme", "light"))
        ctk.set_default_color_theme("blue")

        self.root = ctk.CTk()
        self.root.title(t("app.title"))
        self.root.geometry("900x650")
        self.root.minsize(700, 550)
        self._set_window_icon()

        self.banner = UpdateBanner(self.root, on_click=self._on_update_click)

        self.container = ctk.CTkFrame(self.root, fg_color="transparent")
        self.container.pack(fill="both", expand=True)

        self._views: dict[str, ctk.CTkFrame] = {}
        self.show_view("home")

    # ── view router ──
    def show_view(self, name: str) -> None:
        for v in self._views.values():
            v.pack_forget()
        if name not in self._views:
            self._views[name] = self._build_view(name)
        self._views[name].pack(fill="both", expand=True)

    def _build_view(self, name: str) -> ctk.CTkFrame:
        if name == "home":
            return HomeView(self.container, app=self)
        if name == "settings":
            return SettingsView(self.container, app=self)
        raise ValueError(f"Unknown view: {name}")

    def rebuild_views(self) -> None:
        """Recreate every view (used after language/theme change to refresh labels)."""
        current = next((n for n, v in self._views.items() if v.winfo_ismapped()), "home")
        for v in self._views.values():
            v.destroy()
        self._views.clear()
        self.show_view(current)
        self.root.title(t("app.title"))

    # ── update banner ──
    def show_update_banner(self) -> None:
        self.banner.show(t("update.available"))

    def _on_update_click(self) -> None:
        # Phase 6: trigger update.bat. For now just log.
        log.info("Update banner clicked — update flow lands in Phase 6.")

    # ── theme/language switching ──
    def apply_language(self, lang: str) -> None:
        if lang == self.config.get("language"):
            return
        self.config.set("language", lang)
        set_language(lang)
        self.rebuild_views()

    def apply_theme(self, theme: str) -> None:
        if theme == self.config.get("theme"):
            return
        self.config.set("theme", theme)
        ctk.set_appearance_mode(theme)

    # ── window icon ──
    def _set_window_icon(self) -> None:
        ico = ASSETS_DIR / "icono.ico"
        if ico.exists():
            try:
                self.root.iconbitmap(default=str(ico))
            except Exception as e:
                log.warning("Could not set window icon: %s", e)

    # ── main loop ──
    def run(self) -> None:
        log.info("Oracle Tasks Chile v%s starting (lang=%s, theme=%s)",
                 __version__, self.config.get("language"), self.config.get("theme"))
        self.root.mainloop()
