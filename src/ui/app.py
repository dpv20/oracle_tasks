"""OracleTasksApp — root CTk window with simple view router."""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import webbrowser
from tkinter import messagebox

import customtkinter as ctk

from app_identity import APP_DISPLAY_NAME, APP_USER_MODEL_ID
from settings.config import ConfigManager
from i18n import set_language, t
from infra.updater import check_for_update
from paths import ASSETS_DIR, REPO_ROOT
from version import __version__

from .home_view import HomeView
from .fbbatch_view import FBBatchSetupView
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
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
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
        for view_name in ["home", "spools_cl", "spools_savings", "fbbatch", "settings"]:
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
            "spools_cl": "▣  " + t("home.spools_cl_button"),
            "spools_savings": "💵  " + t("home.savings_button"),
            "settings": "⚙️  " + t("settings.title"),
        }
        menu_labels["fbbatch"] = "FB  " + t("fbbatch.nav")
        for view_name, label in menu_labels.items():
            if view_name in self._menu_buttons:
                self._menu_buttons[view_name].configure(text=label)

        current_theme = self.config.get("theme", "light")
        theme_text = "☀️  " + t("settings.general.theme.light") if current_theme == "light" else "🌙  " + t("settings.general.theme.dark")
        self.theme_btn.configure(text=theme_text)

        credits_text = f"v{__version__}\nDiego Pavez Verdi"
        self.credits_label.configure(text=credits_text)

    def _toggle_theme(self) -> None:
        if self._has_running_work():
            self._warn_running_work()
            return
        current = self.config.get("theme", "light")
        new_theme = "dark" if current == "light" else "light"
        self.apply_theme(new_theme)
        self.rebuild_views()

    def _has_running_work(self) -> bool:
        return any(bool(getattr(view, "_running", False)) for view in self._views.values())

    def _warn_running_work(self) -> None:
        lang = self.config.get("language", "en")
        msg = (
            "Wait for the current extraction/injection to finish, or cancel it, before changing the interface or closing the app."
            if lang == "en"
            else "Espera a que termine la extracción/inyección actual, o cancélala, antes de cambiar la interfaz o cerrar la app."
        )
        messagebox.showwarning(t("app.title"), msg, parent=self.root)

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

    def show_consumer_lending(self) -> None:
        if self._has_running_work():
            self._warn_running_work()
            return
        self.show_view("spools_cl")
        view = self._views.get("spools_cl")
        if hasattr(view, "select_consumer_lending"):
            view.select_consumer_lending()

    def show_cmr_chile(self) -> None:
        if self._has_running_work():
            self._warn_running_work()
            return
        self.show_view("spools_cl")
        view = self._views.get("spools_cl")
        if hasattr(view, "select_cmr_chile"):
            view.select_cmr_chile()

    def _build_view(self, name: str) -> ctk.CTkFrame:
        if name == "home":
            return HomeView(self.container, app=self)
        if name == "settings":
            return SettingsView(self.container, app=self)
        if name == "spools_cl":
            return SpoolsCLView(self.container, app=self)
        if name == "spools_savings":
            return SpoolsSavingsView(self.container, app=self)
        if name == "fbbatch":
            return FBBatchSetupView(self.container, app=self)
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
        if self._has_running_work():
            self._warn_running_work()
            return
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

    def _on_close(self) -> None:
        if self._has_running_work():
            self._warn_running_work()
            return
        self.root.destroy()

    # ── theme/language switching ──
    def apply_language(self, lang: str) -> None:
        if lang == self.config.get("language"):
            return
        if self._has_running_work():
            self._warn_running_work()
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
        """Set both the Tk icon and the Win32 window-level icons.

        `iconbitmap(default=...)` covers the window's title bar but on
        Windows 10/11 it often serves a low-res frame to the taskbar; we
        additionally send WM_SETICON with explicitly-sized HICONs loaded
        from the .ico, which is what taskbar and Alt-Tab actually read.
        """
        ico = ASSETS_DIR / "icono.ico"
        if not ico.exists():
            return
        try:
            self.root.iconbitmap(default=str(ico))
        except Exception as e:
            log.warning("iconbitmap failed: %s", e)
        try:
            from PIL import Image, ImageTk
            png = ASSETS_DIR / "new_icon.png"
            source = png if png.exists() else ico
            icon_img = Image.open(source).convert("RGBA").resize(
                (256, 256), Image.Resampling.LANCZOS
            )
            self._tk_icon = ImageTk.PhotoImage(icon_img)
            self.root.iconphoto(True, self._tk_icon)
        except Exception as e:
            log.warning("iconphoto failed: %s", e)
        # Defer the Win32 WM_SETICON until the window has an HWND (after Tk
        # has actually mapped it). Without after(), winfo_id() returns 0.
        self.root.after(0, self._apply_win32_icons)

    def _apply_win32_icons(self) -> None:
        try:
            import ctypes
        except Exception:
            return
        ico = ASSETS_DIR / "icono.ico"
        try:
            frame = str(self.root.wm_frame())
            hwnd = int(frame, 0) if frame.lower().startswith("0x") else int(frame)
        except Exception:
            try:
                hwnd = self.root.winfo_id()
            except Exception:
                return
        if not hwnd:
            return
        # Constants
        IMAGE_ICON = 1
        LR_LOADFROMFILE = 0x00000010
        LR_DEFAULTSIZE = 0x00000040
        WM_SETICON = 0x0080
        ICON_SMALL = 0
        ICON_BIG = 1
        ICON_SMALL2 = 2
        user32 = ctypes.windll.user32
        user32.LoadImageW.restype = ctypes.c_void_p
        user32.SendMessageW.argtypes = [
            ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p,
        ]
        # Big icon (taskbar / Alt-Tab) — 32x32 is the canonical "big".
        h_big = user32.LoadImageW(
            None, str(ico), IMAGE_ICON, 256, 256, LR_LOADFROMFILE,
        )
        # Small icon (title bar) — 16x16.
        h_small = user32.LoadImageW(
            None, str(ico), IMAGE_ICON, 16, 16, LR_LOADFROMFILE,
        )
        if h_big:
            user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, h_big)
        if h_small:
            user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, h_small)
            user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL2, h_small)
        self._apply_taskbar_identity(hwnd, ico)

    # ── main loop ──
    def _apply_taskbar_identity(self, hwnd: int, ico: os.PathLike[str]) -> None:
        """Set per-window AppUserModel properties for taskbar pinning.

        The process is still pythonw.exe, so Windows otherwise offers to pin
        "Python". These properties tell Explorer what command, name, icon, and
        AppUserModelID belong to this window.
        """
        try:
            import ctypes
            import uuid
            from ctypes import wintypes
        except Exception:
            return

        class GUID(ctypes.Structure):
            _fields_ = [
                ("Data1", wintypes.DWORD),
                ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD),
                ("Data4", ctypes.c_ubyte * 8),
            ]

            @classmethod
            def from_string(cls, value: str) -> "GUID":
                return cls.from_buffer_copy(uuid.UUID(value).bytes_le)

        class PROPERTYKEY(ctypes.Structure):
            _fields_ = [("fmtid", GUID), ("pid", wintypes.DWORD)]

        class PROPVARIANT(ctypes.Structure):
            _fields_ = [
                ("vt", wintypes.USHORT),
                ("wReserved1", wintypes.USHORT),
                ("wReserved2", wintypes.USHORT),
                ("wReserved3", wintypes.USHORT),
                ("p", ctypes.c_void_p),
            ]

        method = ctypes.WINFUNCTYPE

        class IPropertyStoreVtbl(ctypes.Structure):
            _fields_ = [
                ("QueryInterface", method(ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p))),
                ("AddRef", method(wintypes.ULONG, ctypes.c_void_p)),
                ("Release", method(wintypes.ULONG, ctypes.c_void_p)),
                ("GetCount", method(ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD))),
                ("GetAt", method(ctypes.c_long, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(PROPERTYKEY))),
                ("GetValue", method(ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(PROPERTYKEY), ctypes.POINTER(PROPVARIANT))),
                ("SetValue", method(ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(PROPERTYKEY), ctypes.POINTER(PROPVARIANT))),
                ("Commit", method(ctypes.c_long, ctypes.c_void_p)),
            ]

        class IPropertyStore(ctypes.Structure):
            _fields_ = [("lpVtbl", ctypes.POINTER(IPropertyStoreVtbl))]

        def failed(hr: int) -> bool:
            return ctypes.c_long(hr).value < 0

        app_model_fmtid = GUID.from_string("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3")
        keys = {
            "id": PROPERTYKEY(app_model_fmtid, 5),
            "relaunch_command": PROPERTYKEY(app_model_fmtid, 2),
            "relaunch_icon": PROPERTYKEY(app_model_fmtid, 3),
            "relaunch_name": PROPERTYKEY(app_model_fmtid, 4),
        }

        iid_property_store = GUID.from_string("886d8eeb-8cf2-4446-8d02-cdba1dbdcf99")
        store_ptr = ctypes.c_void_p()
        shell32 = ctypes.windll.shell32
        shell32.SHGetPropertyStoreForWindow.argtypes = [
            wintypes.HWND, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p),
        ]
        shell32.SHGetPropertyStoreForWindow.restype = ctypes.c_long
        hr = shell32.SHGetPropertyStoreForWindow(
            wintypes.HWND(hwnd), ctypes.byref(iid_property_store), ctypes.byref(store_ptr)
        )
        if failed(hr) or not store_ptr:
            log.warning("SHGetPropertyStoreForWindow failed: 0x%08x", hr & 0xFFFFFFFF)
            return

        ole32 = ctypes.windll.ole32
        ole32.CoTaskMemAlloc.argtypes = [ctypes.c_size_t]
        ole32.CoTaskMemAlloc.restype = ctypes.c_void_p
        ole32.PropVariantClear.argtypes = [ctypes.POINTER(PROPVARIANT)]
        ole32.PropVariantClear.restype = ctypes.c_long

        store = ctypes.cast(store_ptr, ctypes.POINTER(IPropertyStore))
        vtbl = store.contents.lpVtbl.contents

        def set_string(key: PROPERTYKEY, value: str) -> None:
            buf = ctypes.create_unicode_buffer(value)
            ptr = ole32.CoTaskMemAlloc(ctypes.sizeof(buf))
            if not ptr:
                return
            ctypes.memmove(ptr, buf, ctypes.sizeof(buf))
            pv = PROPVARIANT()
            pv.vt = 31  # VT_LPWSTR
            pv.p = ptr
            try:
                set_hr = vtbl.SetValue(store_ptr, ctypes.byref(key), ctypes.byref(pv))
                if failed(set_hr):
                    log.warning("SetValue failed: 0x%08x", set_hr & 0xFFFFFFFF)
            finally:
                ole32.PropVariantClear(ctypes.byref(pv))

        try:
            set_string(keys["id"], APP_USER_MODEL_ID)
            set_string(keys["relaunch_command"], self._taskbar_relaunch_command())
            set_string(keys["relaunch_icon"], str(ico))
            set_string(keys["relaunch_name"], APP_DISPLAY_NAME)
            commit_hr = vtbl.Commit(store_ptr)
            if failed(commit_hr):
                log.warning("IPropertyStore.Commit failed: 0x%08x", commit_hr & 0xFFFFFFFF)
        finally:
            vtbl.Release(store_ptr)

    def _taskbar_relaunch_command(self) -> str:
        pythonw = os.path.join(sys.prefix, "pythonw.exe")
        if not os.path.isfile(pythonw):
            pythonw = sys.executable
        script = REPO_ROOT / "src" / "main.py"
        return f'"{pythonw}" "{script}"'

    def run(self) -> None:
        log.info("Oracle Tasks Chile v%s starting (lang=%s, theme=%s)",
                 __version__, self.config.get("language"), self.config.get("theme"))
        # Silent background update check — fires the banner if origin/main is ahead.
        check_for_update(self._on_remote_version)
        self.root.mainloop()
