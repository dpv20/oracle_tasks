"""VPN tab backed by the controller embedded in Oracle Tasks."""
from __future__ import annotations

import threading
from tkinter import messagebox

import customtkinter as ctk

from i18n import t
from features.vpn.service import (
    CISCO,
    FORTI,
    GPROT,
    NONE,
    VPNResult,
)
from features.vpn.coordinator_bancodechile import (
    BANCOCHILE,
    BANCOCHILE_LEGACY_HANDOFF,
    status_display_name,
)

from ui.widgets import CardFrame, IconButton

from .colors import (
    VPN_COLORS as LEGACY_VPN_COLORS,
    VPN_HOVER_COLORS as LEGACY_VPN_HOVER_COLORS,
)
from .colors_bancodechile import (
    VPN_COLORS_BANCOCHILE,
    VPN_HOVER_COLORS_BANCOCHILE,
)
from .settings_panel import VPNSettingsPanel

VPN_COLORS = {**LEGACY_VPN_COLORS, **VPN_COLORS_BANCOCHILE}
VPN_HOVER_COLORS = {
    **LEGACY_VPN_HOVER_COLORS,
    **VPN_HOVER_COLORS_BANCOCHILE,
}


class VPNView(ctk.CTkFrame):
    _CARD_DATA = (
        (CISCO, "vpn.oracle", "vpn.oracle.subtitle"),
        (FORTI, "vpn.falabella", "vpn.falabella.subtitle"),
        (GPROT, "vpn.bice", "vpn.bice.subtitle"),
        (BANCOCHILE, "vpn.bancochile", "vpn.bancochile.subtitle"),
        (NONE, "vpn.none", "vpn.none.subtitle"),
    )

    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.service = app.vpn_service
        self.bancochile_service = app.bancochile_vpn_service
        self._running = False
        self._refreshing = False
        self._status = NONE
        self._retry_after_save = False
        self._retry_gp_target_after_save: str | None = None
        self._requested_target = NONE
        self._operation_is_bancochile = False
        self._bancochile_operation_token: object | None = None
        self._buttons: dict[str, ctk.CTkButton] = {}
        self._cards: dict[str, CardFrame] = {}

        self.tabs = ctk.CTkTabview(self)
        self.tabs.pack(fill="both", expand=True, padx=20, pady=15)
        self._connections_tab = t("vpn.tab.connections")
        self._settings_tab = t("vpn.tab.settings")
        self.tabs.add(self._connections_tab)
        self.tabs.add(self._settings_tab)

        self.body = ctk.CTkScrollableFrame(
            self.tabs.tab(self._connections_tab), fg_color="transparent"
        )
        self.body.pack(fill="both", expand=True, padx=5, pady=5)

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
        visibility_controls = ctk.CTkFrame(header, fg_color="transparent")
        visibility_controls.pack(side="right")
        self.show_forti_var = ctk.BooleanVar(
            value=bool(self.app.config.get("vpn_show_forti", True))
        )
        self.show_forti_checkbox = ctk.CTkCheckBox(
            visibility_controls,
            text=t("vpn.show_forti"),
            variable=self.show_forti_var,
            command=self._toggle_forti,
        )
        self.show_forti_checkbox.pack(side="left", padx=(0, 16))
        self.show_bice_var = ctk.BooleanVar(
            value=bool(self.app.config.get("vpn_show_bice", False))
        )
        self.show_bice_checkbox = ctk.CTkCheckBox(
            visibility_controls,
            text=t("vpn.show_bice"),
            variable=self.show_bice_var,
            command=self._toggle_bice,
        )
        self.show_bice_checkbox.pack(side="left", padx=(0, 16))
        self.show_bancochile_var = ctk.BooleanVar(
            value=bool(self.app.config.get("vpn_show_bancochile", True))
        )
        self.show_bancochile_checkbox = ctk.CTkCheckBox(
            visibility_controls,
            text=t("vpn.show_bancochile"),
            variable=self.show_bancochile_var,
            command=self._toggle_bancochile,
        )
        self.show_bancochile_checkbox.pack(side="left", padx=(0, 16))

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
        self.refresh_button = ctk.CTkButton(
            self.status_band,
            text=t("vpn.refresh"),
            width=130,
            height=36,
            corner_radius=8,
            fg_color=("#3b82c4", "#2563a5"),
            hover_color=("#2563a5", "#1d4f7a"),
            command=self._refresh_selected_status,
        )
        self.refresh_button.pack(side="right", padx=20)
        status_copy = ctk.CTkFrame(self.status_band, fg_color="transparent")
        status_copy.pack(side="left", fill="both", expand=True, pady=18)
        self.status_title = ctk.CTkLabel(
            status_copy,
            text=t("vpn.status.pending"),
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

        self.controls = ctk.CTkFrame(self.body, fg_color="transparent")
        self.controls.pack(fill="x", pady=(0, 14))
        self.controls.grid_columnconfigure((0, 1), weight=1, uniform="vpn_cards")
        self.controls.grid_rowconfigure((0, 1), minsize=145)
        for index, (target, title_key, subtitle_key) in enumerate(self._CARD_DATA):
            card = CardFrame(self.controls, corner_radius=8)
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
                command=lambda value=target: self._on_target_button(value),
            )
            button.grid(row=2, column=0, sticky="e", padx=18, pady=(12, 17))
            self._cards[target] = card
            self._buttons[target] = button
        self._apply_bice_visibility()

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
        self.cancel_button = ctk.CTkButton(
            footer,
            text=t("common.cancel"),
            width=110,
            height=34,
            corner_radius=8,
            fg_color="#dc2626",
            hover_color="#b91c1c",
            state="disabled",
            command=self._cancel_current,
        )
        self.settings_panel = VPNSettingsPanel(
            self.tabs.tab(self._settings_tab),
            app,
            on_saved=self._refresh_preferences_with_bancochile,
        )
        self.settings_panel.pack(fill="both", expand=True)

    def on_show(self) -> None:
        self.show_forti_var.set(bool(self.app.config.get("vpn_show_forti", True)))
        self.show_bice_var.set(bool(self.app.config.get("vpn_show_bice", False)))
        self.show_bancochile_var.set(
            bool(self.app.config.get("vpn_show_bancochile", True))
        )
        self._apply_bice_visibility()
        if self.bancochile_service.last_status is not None:
            self._apply_status(self.bancochile_service.last_status)
        elif self.service.last_status is not None:
            self._apply_status(self.service.last_status)

    def on_hide(self) -> None:
        self._retry_after_save = False
        self._retry_gp_target_after_save = None

    def show_settings(self) -> None:
        self.tabs.set(self._settings_tab)

    def _refresh_selected_status(self) -> None:
        """Refresh all VPNs through the same monitor classification path."""
        self.refresh_status()

    def refresh_status(self) -> None:
        if self._running or self._refreshing:
            return
        self._refreshing = True
        self.refresh_button.configure(state="disabled")
        self._set_controls_enabled(False)
        self.message_label.configure(text=t("vpn.checking"))

        def worker() -> None:
            try:
                snapshot = self.bancochile_service.try_get_monitored_status()
                if snapshot is not None:
                    revision, status = snapshot
                    self._ui(
                        lambda: self._apply_monitored_status(status, revision)
                    )
                else:
                    self._ui(
                        lambda: self.message_label.configure(text=t("vpn.ready"))
                    )
            except Exception as exc:
                self._ui(lambda: self._show_error(str(exc)))
            finally:
                self._ui(self._finish_refresh)

        threading.Thread(target=worker, daemon=True).start()

    def _switch_to(self, target: str) -> None:
        if self._running:
            return
        self._running = True
        self._set_controls_enabled(False)
        self.refresh_button.configure(state="disabled")
        self.progress.start()
        self.message_label.configure(text=t("vpn.working"))

        def progress(message: str) -> None:
            self._ui(lambda: self.message_label.configure(text=message))

        def worker() -> None:
            try:
                result = self.service.switch_to(target, progress)
            except Exception as exc:
                result = VPNResult(False, str(exc), self._status)
            self._ui(lambda: self._finish_switch(result))

        threading.Thread(target=worker, daemon=True).start()

    def _finish_switch(self, result: VPNResult) -> None:
        self._running = False
        self.progress.stop()
        self.progress.set(0)
        self.refresh_button.configure(state="normal")
        self._apply_status(result.status, message=result.message)
        if result.error_code == "wrong_password":
            self._handle_wrong_password()
            return
        if not result.ok and result.error_code != "cancelled":
            messagebox.showerror(t("common.error"), result.message, parent=self)

    def _switch_bancochile(self, target: str) -> None:
        """Run Banco-owned transitions without changing the legacy UI path."""
        if self._running or not self.app._try_begin_vpn_action():
            return
        prepare = getattr(
            self.bancochile_service,
            "prepare_bancochile_operation",
            None,
        )
        token = prepare() if callable(prepare) else None
        if token is None:
            self.app._finish_vpn_action_gate()
            return
        self.app._advance_vpn_result_revision()
        self._bancochile_operation_token = token
        self._requested_target = target
        self._operation_is_bancochile = True
        self._running = True
        self._set_controls_enabled(False)
        self.refresh_button.configure(state="disabled")
        self.cancel_button.configure(state="normal")
        self._set_bancochile_cancel_visible(True)
        self.progress.start()
        self.message_label.configure(text=t("vpn.working"))

        def progress(message: str) -> None:
            if message == BANCOCHILE_LEGACY_HANDOFF:
                self._ui(self._finish_bancochile_phase)
                return
            self._ui(lambda: self.message_label.configure(text=message))

        def worker() -> None:
            try:
                try:
                    result = self.bancochile_service.switch_to(
                        target,
                        progress,
                        operation_token=token,
                    )
                except Exception as exc:
                    result = VPNResult(False, str(exc), self._status)
                self._ui(lambda: self._finish_bancochile(result))
            finally:
                self.app._finish_vpn_action_gate()

        try:
            threading.Thread(target=worker, daemon=True).start()
        except Exception:
            self.bancochile_service.abandon_prepared_operation(token)
            self.app._finish_vpn_action_gate()
            raise

    def _finish_bancochile_phase(self) -> None:
        """Hide Banco-only cancellation before the legacy service takes over."""
        self.cancel_button.configure(state="disabled")
        self._set_bancochile_cancel_visible(False)
        self._operation_is_bancochile = False
        self._bancochile_operation_token = None

    def _finish_bancochile(self, result: VPNResult) -> None:
        self._running = False
        self.progress.stop()
        self.progress.set(0)
        self.refresh_button.configure(state="normal")
        self.cancel_button.configure(state="disabled")
        self._set_bancochile_cancel_visible(False)
        self._operation_is_bancochile = False
        self._bancochile_operation_token = None
        self._apply_status(result.status, message=result.message)
        if result.error_code == "wrong_password":
            self._handle_wrong_password()
            return
        if result.error_code in {
            "gp_username_required",
            "gp_password_required",
            "gp_wrong_password",
        }:
            self._handle_gp_password(result.message)
            return
        if not result.ok and result.error_code != "cancelled":
            messagebox.showerror(t("common.error"), result.message, parent=self)

    def _refresh_preferences(self) -> None:
        self.show_forti_var.set(bool(self.app.config.get("vpn_show_forti", True)))
        self.show_bice_var.set(bool(self.app.config.get("vpn_show_bice", False)))
        self._apply_bice_visibility()
        self.app._tray.refresh_menu()
        if self._retry_after_save:
            self._retry_after_save = False
            self.tabs.set(self._connections_tab)
            self.after(300, self._retry_forti_credentials)

    def _refresh_preferences_with_bancochile(self) -> None:
        """Add Banco settings behavior around the unchanged legacy callback."""
        pending_forti_retry = self._retry_after_save
        if pending_forti_retry:
            # Keep the Git callback itself unchanged, but own its delayed retry
            # here so a Banco transition cannot start inside the 300 ms gap.
            self._retry_after_save = False
            self._retry_gp_target_after_save = None
        self.show_bancochile_var.set(
            bool(self.app.config.get("vpn_show_bancochile", True))
        )
        self._refresh_preferences()
        if pending_forti_retry:
            self.tabs.set(self._connections_tab)
            self._running = True
            try:
                self.after(300, self._run_pending_forti_retry)
            except Exception:
                self._running = False
                raise
        elif self._retry_gp_target_after_save:
            target = self._retry_gp_target_after_save
            self._retry_gp_target_after_save = None
            self.tabs.set(self._connections_tab)
            self.after(300, lambda: self._switch_bancochile(target))

    def _run_pending_forti_retry(self) -> None:
        """Start the unchanged Forti retry only outside a Banco transition."""
        self._running = False
        if (
            self.app._vpn_action_in_progress()
            or self.bancochile_service.owns_bancochile
        ):
            return
        self.app._advance_vpn_result_revision()
        self._retry_forti_credentials()

    def _handle_wrong_password(self) -> None:
        retry = messagebox.askokcancel(
            t("vpn.credentials.title"),
            t("vpn.credentials.rejected"),
            parent=self,
        )
        if retry:
            self._retry_after_save = True
            self.tabs.set(self._settings_tab)

    def _handle_gp_password(self, message: str) -> None:
        edit_settings = messagebox.askokcancel(
            t("vpn.gp_credentials.title"),
            t("vpn.gp_credentials.open_settings", detail=message),
            parent=self,
        )
        if edit_settings:
            # Banco is the most recent prompt; do not retain a stale Forti
            # retry that could start after these credentials are saved.
            self._retry_after_save = False
            self._retry_gp_target_after_save = self._requested_target
            self.settings_panel.show_profile(self._requested_target)
            self.tabs.set(self._settings_tab)

    def _retry_forti_credentials(self) -> None:
        if self._running:
            return
        self._running = True
        self._set_controls_enabled(False)
        self.refresh_button.configure(state="disabled")
        self.progress.start()
        self.message_label.configure(text=t("vpn.credentials.retrying"))

        def worker() -> None:
            try:
                result = self.service.retry_forti_credentials()
            except Exception as exc:
                result = VPNResult(False, str(exc), self._status)
            self._ui(lambda: self._finish_switch(result))

        threading.Thread(target=worker, daemon=True).start()

    def _apply_monitored_status(self, status: str, revision: int) -> None:
        """Ignore a refresh result superseded by a VPN ownership transition."""
        if self.bancochile_service.is_status_snapshot_current(revision):
            self._apply_status(status)

    def _on_target_button(self, target: str) -> None:
        """Route only Banco-owned work through Banco-specific UI handling."""
        if getattr(self, "_refreshing", False) or self.app._vpn_action_in_progress():
            return
        bank_owned = self.bancochile_service.owns_bancochile
        if target == BANCOCHILE and (target == self._status or bank_owned):
            self._switch_bancochile(NONE)
            return
        if target == BANCOCHILE or bank_owned:
            self._switch_bancochile(target)
            return
        if self._running:
            return
        self.app._advance_vpn_result_revision()
        self._switch_to(target)

    def _cancel_current(self) -> None:
        if not self._running or not self._operation_is_bancochile:
            return
        self.bancochile_service.cancel_current(self._bancochile_operation_token)
        self.cancel_button.configure(state="disabled")
        self.message_label.configure(text=t("vpn.cancelling"))

    def _set_bancochile_cancel_visible(self, visible: bool) -> None:
        """Show Banco's cancel action without changing the legacy footer layout."""
        self.message_label.pack_forget()
        self.cancel_button.pack_forget()
        if visible:
            self.cancel_button.pack(side="right", padx=(12, 0))
            self.message_label.pack(side="left", fill="x", expand=True)
        else:
            self.message_label.pack(fill="x")

    def _toggle_bice(self) -> None:
        self.app.config.set("vpn_show_bice", bool(self.show_bice_var.get()))
        self._apply_bice_visibility()
        self.app._tray.refresh_menu()

    def _toggle_forti(self) -> None:
        self.app.config.set("vpn_show_forti", bool(self.show_forti_var.get()))
        self._apply_bice_visibility()
        self.app._tray.refresh_menu()

    def _toggle_bancochile(self) -> None:
        self.app.config.set(
            "vpn_show_bancochile", bool(self.show_bancochile_var.get())
        )
        self._apply_bice_visibility()
        self.app._tray.refresh_menu()

    def _apply_bice_visibility(self) -> None:
        for card in self._cards.values():
            card.grid_remove()
        visible = [CISCO]
        if bool(self.app.config.get("vpn_show_forti", True)):
            visible.append(FORTI)
        if self.show_bice_var.get():
            visible.append(GPROT)
        if self.show_bancochile_var.get():
            visible.append(BANCOCHILE)
        visible.append(NONE)
        for index, target in enumerate(visible):
            self._cards[target].grid(
                row=index // 2,
                column=index % 2,
                columnspan=1,
                sticky="nsew",
                padx=(0, 7) if index % 2 == 0 else (7, 0),
                pady=(0, 7) if index < 2 else (7, 0),
            )
        if len(visible) % 2:
            self._cards[visible[-1]].grid_configure(
                column=0,
                columnspan=2,
                padx=0,
            )

    def _apply_status(
        self,
        status: str,
        *,
        message: str = "",
    ) -> None:
        if self._running and not message:
            return
        self._status = status
        self.app._tray.set_vpn_status(status)
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
        self._style_cards()
        self._set_controls_enabled(not self._running and not self._refreshing)

    def _show_error(self, message: str) -> None:
        self.status_dot.configure(text_color="#dc2626")
        self.status_title.configure(text=t("vpn.status.error"))
        self.status_detail.configure(text=message)
        self.message_label.configure(
            text=message,
            text_color=("#b91c1c", "#fca5a5"),
        )
        self._set_controls_enabled(not self._running and not self._refreshing)

    def _finish_refresh(self) -> None:
        self._refreshing = False
        if not self._running:
            self.refresh_button.configure(state="normal")
            self._set_controls_enabled(True)

    def _style_cards(self) -> None:
        for target, card in self._cards.items():
            active = target == self._status
            target_color = VPN_COLORS[target]
            card.configure(
                border_width=2 if active else 1,
                border_color=target_color if active else ("#e2e8f0", "#334155"),
            )
            button = self._buttons[target]
            if active:
                if target == BANCOCHILE:
                    button.configure(
                        text=t("vpn.disconnect"),
                        fg_color=target_color,
                        hover_color=VPN_HOVER_COLORS[target],
                    )
                else:
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
            if enabled and target == self._status and target != BANCOCHILE:
                button.configure(state="disabled")
            else:
                button.configure(state=state)

    def _ui(self, callback) -> None:
        try:
            if self.winfo_exists():
                self.after(0, callback)
        except Exception:
            pass
