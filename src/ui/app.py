"""OracleTasksApp — root CTk window with simple view router."""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import webbrowser

import customtkinter as ctk

from settings.config import ConfigManager
from i18n import set_language, t
from infra.updater import check_for_update
from paths import ASSETS_DIR, REPO_ROOT
from version import __version__

from .home_view import HomeView
from .settings_view import SettingsView
from .spools_cl_view import SpoolsCLView
from .spools_savings_view import SpoolsSavingsView
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
        # Fallback size if maximizing fails; on Windows we maximize via Tk's
        # "zoomed" state, deferred so it fires after the window is realized.
        self.root.geometry("900x650")
        self.root.minsize(700, 550)
        self.root.after(0, self._maximize)
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
        if name == "spools_cl":
            return SpoolsCLView(self.container, app=self)
        if name == "spools_savings":
            return SpoolsSavingsView(self.container, app=self)
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
    def show_update_banner(self, remote_version: str | None = None) -> None:
        if remote_version:
            text = t("update.available_v", version=remote_version)
        else:
            text = t("update.available")
        self.banner.show(text, before=self.container)

    def _on_remote_version(self, remote_version: str) -> None:
        # Called on the updater's worker thread — marshal onto the UI thread.
        self.root.after(0, lambda v=remote_version: self.show_update_banner(v))

    def _on_update_click(self) -> None:
        updater = REPO_ROOT / "update.bat"
        if not updater.exists():
            webbrowser.open("https://github.com/dpv20/oracle_tasks/releases/latest")
            return
        pythonw = sys.executable
        if pythonw.lower().endswith("python.exe"):
            candidate = pythonw[: -len("python.exe")] + "pythonw.exe"
            if os.path.isfile(candidate):
                pythonw = candidate
        try:
            subprocess.Popen(
                ["cmd", "/c", "start", "", str(updater), pythonw],
                cwd=str(REPO_ROOT),
                creationflags=0x00000010,  # CREATE_NEW_CONSOLE
            )
        except OSError as e:
            log.error("Failed to launch update.bat: %s", e)
            return
        self.banner.configure(text=t("update.installing"))
        self.root.after(500, self.root.destroy)

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

    # ── window state ──
    def _maximize(self) -> None:
        """Maximize the window post-realize. `state('zoomed')` on Windows is
        the proper maximize (with title bar), not fullscreen."""
        try:
            self.root.state("zoomed")
        except Exception as e:
            log.warning("Could not maximize window: %s", e)

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
        # Silent background update check — fires the banner if origin/main is ahead.
        check_for_update(self._on_remote_version)
        self.root.mainloop()
