"""VPN tab backed by the installed VPN Switcher controller."""
from __future__ import annotations

import threading
from tkinter import messagebox

import customtkinter as ctk

from i18n import t
from vpn_integration import (
    CISCO,
    FORTI,
    GPROT,
    NONE,
    VPNResult,
    VPNSwitcherBridge,
    status_display_name,
)

from .vpn_colors import VPN_COLORS, VPN_HOVER_COLORS
from .widgets import CardFrame, IconButton


class VPNView(ctk.CTkFrame):
    POLL_MS = 5_000
    _CARD_DATA = (
        (CISCO, "vpn.oracle", "vpn.oracle.subtitle"),
        (FORTI, "vpn.falabella", "vpn.falabella.subtitle"),
        (GPROT, "vpn.bice", "vpn.bice.subtitle"),
        (NONE, "vpn.none", "vpn.none.subtitle"),
    )

    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.bridge = VPNSwitcherBridge()
        self._running = False
        self._refreshing = False
        self._status = NONE
        self._poll_id = None
        self._visible = False
        self._buttons: dict[str, ctk.CTkButton] = {}
        self._cards: dict[str, CardFrame] = {}

        self.body = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.body.pack(fill="both", expand=True, padx=25, pady=25)

        header = ctk.CTkFrame(self.body, fg_color="transparent")
        header.pack(fill="x", pady=(0, 18))
        IconButton(
            header,
            text=f"< {t('common.back')}",
            width=100,
            command=lambda: app.show_view("home"),
        ).pack(side="left")
        ctk.CTkLabel(
            header,
            text=t("vpn.title"),
            font=ctk.CTkFont(size=22, weight="bold"),
            text_color=("#0f172a", "#ffffff"),
        ).pack(side="left", padx=16)
        self.refresh_button = ctk.CTkButton(
            header,
            text=t("vpn.refresh"),
            width=130,
            height=36,
            corner_radius=8,
            fg_color=("#3b82c4", "#2563a5"),
            hover_color=("#2563a5", "#1d4f7a"),
            command=self.refresh_status,
        )
        self.refresh_button.pack(side="right")

        self.status_band = ctk.CTkFrame(
            self.body,
            height=112,
            corner_radius=8,
            fg_color=("#f8fafc", "#111827"),
            border_width=1,
            border_color=("#dbe3ec", "#334155"),
        )
        self.status_band.pack(fill="x", pady=(0, 14))
        self.status_band.pack_propagate(False)
        self.status_dot = ctk.CTkLabel(
            self.status_band,
            text="●",
            width=48,
            font=ctk.CTkFont(size=30),
            text_color="#64748b",
        )
        self.status_dot.pack(side="left", padx=(24, 12))
        status_copy = ctk.CTkFrame(self.status_band, fg_color="transparent")
        status_copy.pack(side="left", fill="both", expand=True, pady=18)
        self.status_title = ctk.CTkLabel(
            status_copy,
            text=t("vpn.checking"),
            anchor="w",
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color=("#0f172a", "#f8fafc"),
        )
        self.status_title.pack(fill="x", anchor="w")
        self.status_detail = ctk.CTkLabel(
            status_copy,
            text="",
            anchor="w",
            font=ctk.CTkFont(size=13),
            text_color=("#475569", "#94a3b8"),
        )
        self.status_detail.pack(fill="x", anchor="w", pady=(3, 0))

        self.install_panel = ctk.CTkFrame(
            self.body,
            corner_radius=8,
            fg_color=("#fff7ed", "#2a1f14"),
            border_width=1,
            border_color=("#fdba74", "#9a5b20"),
        )
        install_copy = ctk.CTkFrame(self.install_panel, fg_color="transparent")
        install_copy.pack(side="left", fill="both", expand=True, padx=18, pady=14)
        self.install_title = ctk.CTkLabel(
            install_copy,
            text=t("vpn.install.title"),
            anchor="w",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=("#9a3412", "#fdba74"),
        )
        self.install_title.pack(fill="x")
        self.install_detail = ctk.CTkLabel(
            install_copy,
            text=t("vpn.install.detail"),
            anchor="w",
            justify="left",
            wraplength=650,
            text_color=("#7c2d12", "#fed7aa"),
        )
        self.install_detail.pack(fill="x", pady=(3, 0))
        self.install_button = IconButton(
            self.install_panel,
            text=t("vpn.install.action"),
            width=180,
            command=self._install_vpn_switcher,
        )
        self.install_button.pack(side="right", padx=18, pady=14)

        controls = ctk.CTkFrame(self.body, fg_color="transparent")
        controls.pack(fill="x", pady=(0, 14))
        controls.grid_columnconfigure((0, 1), weight=1, uniform="vpn_cards")
        controls.grid_rowconfigure((0, 1), minsize=145)
        for index, (target, title_key, subtitle_key) in enumerate(self._CARD_DATA):
            card = CardFrame(controls, corner_radius=8)
            card.grid(
                row=index // 2,
                column=index % 2,
                sticky="nsew",
                padx=(0, 7) if index % 2 == 0 else (7, 0),
                pady=(0, 7) if index < 2 else (7, 0),
            )
            card.grid_columnconfigure(0, weight=1)
            ctk.CTkLabel(
                card,
                text=t(title_key),
                anchor="w",
                font=ctk.CTkFont(size=16, weight="bold"),
                text_color=("#0f172a", "#f8fafc"),
            ).grid(row=0, column=0, sticky="ew", padx=18, pady=(17, 2))
            ctk.CTkLabel(
                card,
                text=t(subtitle_key),
                anchor="w",
                text_color=("#64748b", "#94a3b8"),
            ).grid(row=1, column=0, sticky="ew", padx=18)
            button = IconButton(
                card,
                text=t("vpn.disconnect") if target == NONE else t("vpn.connect"),
                width=150,
                command=lambda value=target: self._switch_to(value),
            )
            button.grid(row=2, column=0, sticky="e", padx=18, pady=(12, 17))
            self._cards[target] = card
            self._buttons[target] = button

        footer = ctk.CTkFrame(self.body, fg_color="transparent")
        footer.pack(fill="x")
        self.progress = ctk.CTkProgressBar(footer, mode="indeterminate", height=8)
        self.progress.pack(fill="x", pady=(0, 8))
        self.progress.set(0)
        self.message_label = ctk.CTkLabel(
            footer,
            text=t("vpn.ready"),
            anchor="w",
            justify="left",
            wraplength=760,
            text_color=("#475569", "#94a3b8"),
        )
        self.message_label.pack(fill="x")
        self.service_label = ctk.CTkLabel(
            footer,
            text="",
            anchor="w",
            justify="left",
            text_color=("#64748b", "#64748b"),
            font=ctk.CTkFont(size=11),
        )
        self.service_label.pack(fill="x", pady=(3, 0))

    def on_show(self) -> None:
        self._visible = True
        self._poll_id = self.after(100, self.refresh_status)

    def on_hide(self) -> None:
        self._visible = False
        self._cancel_poll()

    def refresh_status(self) -> None:
        self._cancel_poll()
        if self._running or self._refreshing:
            self._schedule_poll()
            return
        self._refreshing = True
        self.refresh_button.configure(state="disabled")
        self.message_label.configure(text=t("vpn.checking"))

        def worker() -> None:
            try:
                available = self.bridge.rediscover()
                if not available:
                    self._ui(self._show_unavailable)
                    return
                background_ok, background_message = self.bridge.ensure_background_running()
                status = self.bridge.get_status()
                self._ui(
                    lambda: self._apply_status(
                        status,
                        background_ok=background_ok,
                        background_message=background_message,
                    )
                )
            except Exception as exc:
                self._ui(lambda: self._show_error(str(exc)))
            finally:
                self._ui(self._finish_refresh)

        threading.Thread(target=worker, daemon=True).start()

    def _switch_to(self, target: str) -> None:
        if self._running:
            return
        if not self.bridge.available:
            self._show_unavailable()
            return
        self._running = True
        self._cancel_poll()
        self._set_controls_enabled(False)
        self.progress.start()
        self.message_label.configure(text=t("vpn.working"))

        def progress(message: str) -> None:
            self._ui(lambda: self.message_label.configure(text=message))

        def worker() -> None:
            try:
                result = self.bridge.switch_to(target, progress)
            except Exception as exc:
                result = VPNResult(False, str(exc), self._status)
            self._ui(lambda: self._finish_switch(result))

        threading.Thread(target=worker, daemon=True).start()

    def _finish_switch(self, result: VPNResult) -> None:
        self._running = False
        self.progress.stop()
        self.progress.set(0)
        self._apply_status(result.status, message=result.message)
        if not result.ok:
            messagebox.showerror(t("common.error"), result.message, parent=self)
        self._schedule_poll()

    def _install_vpn_switcher(self) -> None:
        if self._running:
            return
        self._running = True
        self.install_button.configure(state="disabled")
        self.message_label.configure(text=t("vpn.install.downloading"))

        def worker() -> None:
            ok, message = self.bridge.launch_installer()
            self._ui(lambda: self._finish_install(ok, message))

        threading.Thread(target=worker, daemon=True).start()

    def _finish_install(self, ok: bool, message: str) -> None:
        self._running = False
        self.install_button.configure(state="normal")
        self.message_label.configure(
            text=message,
            text_color=("#166534", "#86efac") if ok else ("#b91c1c", "#fca5a5"),
        )
        if not ok:
            messagebox.showerror(t("common.error"), message, parent=self)
        self._schedule_poll()

    def _apply_status(
        self,
        status: str,
        *,
        background_ok: bool = True,
        background_message: str = "",
        message: str = "",
    ) -> None:
        self._status = status
        self.install_panel.pack_forget()
        connected = status != NONE
        self.status_dot.configure(text_color=VPN_COLORS.get(status, VPN_COLORS[NONE]))
        self.status_title.configure(
            text=t("vpn.connected") if connected else t("vpn.disconnected")
        )
        self.status_detail.configure(text=status_display_name(status))
        self.message_label.configure(
            text=message or t("vpn.ready"),
            text_color=("#475569", "#94a3b8"),
        )
        location = str(self.bridge.install_path or "")
        service_state = t("vpn.background.running") if background_ok else t("vpn.background.stopped")
        details = background_message or service_state
        self.service_label.configure(
            text=t("vpn.service.summary", state=service_state, path=location) + f"\n{details}"
        )
        self._style_cards()
        self._set_controls_enabled(not self._running and self.bridge.configured)

    def _show_unavailable(self) -> None:
        self._status = NONE
        self.status_dot.configure(text_color="#dc2626")
        self.status_title.configure(text=t("vpn.unavailable"))
        self.status_detail.configure(text=t("vpn.install.detail"))
        self.install_panel.pack(fill="x", pady=(0, 14), after=self.status_band)
        self.message_label.configure(text=t("vpn.install.detail"))
        self.service_label.configure(text="")
        self._set_controls_enabled(False)
        self._style_cards()

    def _show_error(self, message: str) -> None:
        self.install_panel.pack_forget()
        self.status_dot.configure(text_color="#dc2626")
        self.status_title.configure(text=t("vpn.status.error"))
        self.status_detail.configure(text=message)
        self.message_label.configure(
            text=message,
            text_color=("#b91c1c", "#fca5a5"),
        )
        self._set_controls_enabled(False)

    def _finish_refresh(self) -> None:
        self._refreshing = False
        self.refresh_button.configure(state="normal")
        self._schedule_poll()

    def _style_cards(self) -> None:
        for target, card in self._cards.items():
            active = self.bridge.available and target == self._status
            target_color = VPN_COLORS[target]
            card.configure(
                border_width=2 if active else 1,
                border_color=target_color if active else ("#e2e8f0", "#334155"),
            )
            button = self._buttons[target]
            if active:
                button.configure(
                    text=t("vpn.active") if target != NONE else t("vpn.no_active"),
                    fg_color=target_color,
                    hover_color=target_color,
                )
            else:
                button.configure(
                    text=t("vpn.disconnect") if target == NONE else t("vpn.connect"),
                    fg_color=target_color,
                    hover_color=VPN_HOVER_COLORS[target],
                )

    def _set_controls_enabled(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        for target, button in self._buttons.items():
            if enabled and target == self._status:
                button.configure(state="disabled")
            else:
                button.configure(state=state)

    def _schedule_poll(self) -> None:
        self._cancel_poll()
        if self._visible and self.winfo_exists():
            self._poll_id = self.after(self.POLL_MS, self.refresh_status)

    def _cancel_poll(self) -> None:
        if self._poll_id is not None:
            try:
                self.after_cancel(self._poll_id)
            except Exception:
                pass
            self._poll_id = None

    def _ui(self, callback) -> None:
        try:
            if self.winfo_exists():
                self.after(0, callback)
        except Exception:
            pass

    def destroy(self) -> None:
        self._visible = False
        self._cancel_poll()
        super().destroy()
