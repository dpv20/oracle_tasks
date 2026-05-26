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
        # Optimized window sizing for a professional side navigation layout
        self.root.geometry("1100x720")
        self.root.minsize(850, 600)
        self.root.after(0, self._maximize)
        self._set_window_icon()

        # Left Sidebar Navigation Frame
        self.sidebar = ctk.CTkFrame(self.root, width=240, corner_radius=0, fg_color=("#f1f5f9", "#0f172a"), border_width=0)
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)

        # Brand / Logo Header
        self.brand_label = ctk.CTkLabel(
            self.sidebar,
            text="⚡ Oracle Tasks",
            font=ctk.CTkFont(size=20, weight="bold"),
            text_color=("#4f46e5", "#6366f1")
        )
        self.brand_label.pack(pady=(30, 25), padx=20, anchor="w")

        # Sidebar Menu Items
        self._menu_buttons: dict[str, ctk.CTkButton] = {}

        # Quick initialization, actual localized text labels will be loaded dynamically
        for view_name in ["home", "spools_cl", "spools_savings", "settings"]:
            btn = ctk.CTkButton(
                self.sidebar,
                text="",
                height=40,
                corner_radius=8,
                fg_color="transparent",
                text_color=("#334155", "#94a3b8"),
                hover_color=("#e2e8f0", "#1e293b"),
                anchor="w",
                font=ctk.CTkFont(size=13, weight="normal"),
                command=lambda name=view_name: self.show_view(name)
            )
            btn.pack(fill="x", padx=15, pady=4)
            self._menu_buttons[view_name] = btn

        # Theme toggle / quick settings footer
        self.sidebar_footer = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        self.sidebar_footer.pack(side="bottom", fill="x", padx=15, pady=20)

        # Separator line
        separator = ctk.CTkFrame(self.sidebar_footer, height=1, fg_color=("#e2e8f0", "#1e293b"))
        separator.pack(fill="x", pady=(0, 15))

        # Quick Theme Toggle Button
        self.theme_btn = ctk.CTkButton(
            self.sidebar_footer,
            text="",
            height=32,
            corner_radius=6,
            fg_color=("#ffffff", "#1e293b"),
            text_color=("#334155", "#f8fafc"),
            border_color=("#e2e8f0", "#334155"),
            border_width=1,
            font=ctk.CTkFont(size=12, weight="normal"),
            command=self._toggle_theme
        )
        self.theme_btn.pack(fill="x", pady=(0, 10))

        # Credits / version info
        self.credits_label = ctk.CTkLabel(
            self.sidebar_footer,
            text="",
            font=ctk.CTkFont(size=11),
            text_color=("gray45", "gray55"),
            justify="left",
            anchor="w"
        )
        self.credits_label.pack(fill="x", padx=5)

        self._update_sidebar_labels()

        # Banner for update notifications
        self.banner = UpdateBanner(self.root, on_click=self._on_update_click)

        # Main content container on the right (with premium margins)
        self.container = ctk.CTkFrame(self.root, fg_color="transparent")
        self.container.pack(side="right", fill="both", expand=True)

        self._views: dict[str, ctk.CTkFrame] = {}
        self.show_view("home")

    # ── sidebar helpers ──
    def _update_sidebar_labels(self) -> None:
        lang = self.config.get("language", "en")
        menu_labels = {
            "home": "🏠  " + ("Inicio" if lang == "es" else "Dashboard"),
            "spools_cl": "🇨🇱  " + t("home.spools_cl_button"),
            "spools_savings": "💵  " + t("home.savings_button"),
            "settings": "⚙️  " + t("settings.title"),
        }
        for view_name, label in menu_labels.items():
            if view_name in self._menu_buttons:
                self._menu_buttons[view_name].configure(text=label)

        current_theme = self.config.get("theme", "light")
        theme_text = "☀️  " + t("settings.general.theme.light") if current_theme == "light" else "🌙  " + t("settings.general.theme.dark")
        self.theme_btn.configure(text=theme_text)

        credits_text = f"v{__version__}\nDiego Pavez Verdi"
        self.credits_label.configure(text=credits_text)

    def _toggle_theme(self) -> None:
        current = self.config.get("theme", "light")
        new_theme = "dark" if current == "light" else "light"
        self.apply_theme(new_theme)
        self.rebuild_views()

    # ── view router ──
    def show_view(self, name: str) -> None:
        for v in self._views.values():
            v.pack_forget()
        if name not in self._views:
            self._views[name] = self._build_view(name)
        self._views[name].pack(fill="both", expand=True)

        # Highlight active sidebar button
        for view_name, btn in self._menu_buttons.items():
            if view_name == name:
                btn.configure(
                    fg_color=("#4f46e5", "#6366f1"),
                    text_color="white",
                    hover_color=("#4338ca", "#4f46e5"),
                    font=ctk.CTkFont(size=13, weight="bold")
                )
            else:
                btn.configure(
                    fg_color="transparent",
                    text_color=("#334155", "#94a3b8"),
                    hover_color=("#e2e8f0", "#1e293b"),
                    font=ctk.CTkFont(size=13, weight="normal")
                )

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
        self._update_sidebar_labels()
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
