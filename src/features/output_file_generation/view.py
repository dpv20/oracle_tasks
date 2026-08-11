"""CustomTkinter view for read-only Generic Interface output reconstruction."""
from __future__ import annotations

import logging
import os
import queue
import re
import threading
from datetime import date
from pathlib import Path
import tkinter as tk
from tkinter import messagebox

import customtkinter as ctk

from i18n import t
from paths import OUTPUT_FILES_OUT_DIR
from ui.calendar_dialog import CalendarDialog
from ui.widgets import CardFrame, IconButton

from .formats import InterfaceSpec, validate_process_ref
from .models import GenerationRequest, GenerationResult, OracleTarget
from .service import (
    GenerationCancelled,
    OutputFileGenerationError,
    OutputFileGenerationService,
)


log = logging.getLogger(__name__)

_ENV_TAG = {"prod": "PROD"}
_TARGET_COUNTRIES = ("chile", "peru", "colombia", "mexico")
_AUTO_INTERFACE_SEPARATOR = "----------------"
_DEFAULT_AUTO_DETECT = False
_DEFAULT_AUTO_DETECT_DATE = False
_DEFAULT_DISCLAIMER_EXPANDED = False
_PRIORITY_INTERFACE_LABELS = ("CHISALCA", "OFDOBIEL", "OFICOWCG")
_COUNTRY_LABELS = {
    "chile": "Chile",
    "peru": "Peru",
    "colombia": "Colombia",
    "mexico": "Mexico",
}
_CONNECTION_RE = re.compile(
    r"\b[^\s/]+(?:\[[^\]]+\])?/[^\s@]+@[^\s]+",
    re.IGNORECASE,
)


def _safe_log_message(value: object) -> str:
    redacted = _CONNECTION_RE.sub("[connection redacted]", str(value or ""))
    compact = " ".join(redacted.split())
    return compact[:500] or "Output reconstruction failed"


def _interface_options(
    specs: tuple[InterfaceSpec, ...],
) -> tuple[tuple[str, str], ...]:
    """Return stable display labels mapped to canonical input codes."""
    options = [
        (
            "CHISALCA" if spec.input_code == "CHISALCA" else spec.output_code,
            spec.input_code,
        )
        for spec in specs
    ]
    priority = {
        label: position
        for position, label in enumerate(_PRIORITY_INTERFACE_LABELS)
    }
    ordered = sorted(
        options,
        key=lambda option: (
            priority.get(option[0], len(priority)),
            option[0],
            option[1],
        ),
    )
    labels = [label for label, _input_code in ordered]
    if len(labels) != len(set(labels)):
        raise RuntimeError("Output interface dropdown labels must be unique")
    return tuple(ordered)


def _filtered_interface_values(
    values: tuple[str, ...],
    query: str,
) -> tuple[str, ...]:
    """Filter interface labels by a case-insensitive typed prefix."""
    prefix = str(query or "").strip().casefold()
    if not prefix:
        return values
    return tuple(value for value in values if value.casefold().startswith(prefix))


class _SearchableComboBox(ctk.CTkFrame):
    """Editable selector with an inline, non-modal suggestion list.

    CustomTkinter's combo-box dropdown is a native Tk menu.  Posting that menu
    while handling every key release transfers keyboard interaction away from
    the entry on Windows.  Keeping the suggestions in the normal widget tree
    lets typing remain responsive and avoids relying on CustomTkinter internals.
    """

    def __init__(
        self,
        master,
        *,
        values: list[str],
        variable: tk.Variable,
        command,
    ) -> None:
        super().__init__(master, fg_color="transparent")
        self._values = list(values)
        self._variable = variable
        self._command = command
        self._state = "normal"

        self.grid_columnconfigure(0, weight=1)
        self.entry = ctk.CTkEntry(self, textvariable=variable)
        self.entry.grid(row=0, column=0, sticky="ew")
        self.dropdown_button = ctk.CTkButton(
            self,
            text="▼",
            width=34,
            command=self._toggle_suggestions,
        )
        self.dropdown_button.grid(row=0, column=1, padx=(4, 0), sticky="ns")

        self.suggestion_frame = ctk.CTkFrame(
            self,
            corner_radius=6,
            border_width=1,
        )
        self.suggestions = tk.Listbox(
            self.suggestion_frame,
            activestyle="none",
            borderwidth=0,
            exportselection=False,
            highlightthickness=0,
            selectmode=tk.BROWSE,
            font=("Segoe UI", 10),
        )
        self.suggestions.pack(
            side="left",
            fill="both",
            expand=True,
            padx=(2, 0),
            pady=2,
        )
        self.suggestion_scrollbar = ctk.CTkScrollbar(
            self.suggestion_frame,
            width=14,
            command=self.suggestions.yview,
        )
        self.suggestion_scrollbar.pack(
            side="right",
            fill="y",
            padx=(2, 2),
            pady=2,
        )
        self.suggestions.configure(yscrollcommand=self.suggestion_scrollbar.set)

        self.entry.bind("<FocusIn>", self._select_existing_value, add="+")
        self.entry.bind("<Down>", self._focus_first_suggestion, add="+")
        self.entry.bind("<Escape>", self._on_escape, add="+")
        self.entry.bind("<FocusOut>", self._on_focus_out, add="+")
        self.suggestions.bind(
            "<ButtonRelease-1>",
            self._commit_suggestion,
            add="+",
        )
        self.suggestions.bind("<Return>", self._commit_suggestion, add="+")
        self.suggestions.bind("<Escape>", self._on_escape, add="+")
        self.suggestions.bind("<FocusOut>", self._on_focus_out, add="+")

    def bind(self, sequence=None, command=None, add=True):
        return self.entry.bind(sequence, command, add=add)

    def get(self) -> str:
        return str(self._variable.get() or "")

    def set(self, value: str) -> None:
        self._variable.set(value)

    def focus_set(self) -> None:
        self.entry.focus_set()

    def configure(self, require_redraw: bool = False, **kwargs) -> None:
        values = kwargs.pop("values", None)
        if values is not None:
            self._values = list(values)

        state = kwargs.pop("state", None)
        if state is not None:
            self._state = str(state)
            self.entry.configure(state=state)
            self.dropdown_button.configure(state=state)
            if state == "disabled":
                self.hide_suggestions()

        if kwargs:
            super().configure(require_redraw=require_redraw, **kwargs)

    config = configure

    def show_suggestions(self, values: tuple[str, ...] | list[str] | None = None) -> None:
        if values is not None:
            self._values = list(values)
        if self._state == "disabled" or not self._values:
            self.hide_suggestions()
            return

        self.suggestions.delete(0, tk.END)
        for value in self._values:
            self.suggestions.insert(tk.END, value)
        self.suggestions.configure(height=min(6, len(self._values)))
        self._apply_suggestion_colors()
        self.suggestion_frame.grid(
            row=1,
            column=0,
            columnspan=2,
            sticky="ew",
            pady=(3, 0),
        )
        self.suggestion_frame.lift()

    def hide_suggestions(self) -> None:
        self.suggestion_frame.grid_remove()

    def _toggle_suggestions(self) -> None:
        if self.suggestion_frame.winfo_manager():
            self.hide_suggestions()
        else:
            self.show_suggestions()

    def _select_existing_value(self, _event: object | None = None) -> None:
        self.after_idle(self._select_entry_contents)

    def _select_entry_contents(self) -> None:
        if self._state != "disabled":
            self.entry.select_range(0, tk.END)
            self.entry.icursor(tk.END)

    def _focus_first_suggestion(self, _event: object | None = None) -> str:
        if not self.suggestion_frame.winfo_manager():
            self.show_suggestions()
        if self.suggestions.size():
            self.suggestions.selection_clear(0, tk.END)
            self.suggestions.selection_set(0)
            self.suggestions.activate(0)
            self.suggestions.focus_set()
        return "break"

    def _commit_suggestion(self, _event: object | None = None) -> str:
        selection = self.suggestions.curselection()
        if not selection:
            return "break"
        value = str(self.suggestions.get(selection[0]))
        self._variable.set(value)
        self.hide_suggestions()
        self.entry.focus_set()
        if self._command is not None:
            self._command(value)
        return "break"

    def _on_escape(self, _event: object | None = None) -> str:
        self.hide_suggestions()
        self.entry.focus_set()
        return "break"

    def _on_focus_out(self, _event: object | None = None) -> None:
        self.after_idle(self._hide_if_focus_left_selector)

    def _hide_if_focus_left_selector(self) -> None:
        focused = self.focus_get()
        while focused is not None:
            if focused is self:
                return
            focused = getattr(focused, "master", None)
        self.hide_suggestions()

    def _apply_suggestion_colors(self) -> None:
        dark = ctk.get_appearance_mode().lower() == "dark"
        self.suggestions.configure(
            background="#2B2B2B" if dark else "#FFFFFF",
            foreground="#DCE4EE" if dark else "#111827",
            selectbackground="#1F6AA5" if dark else "#3B8ED0",
            selectforeground="#FFFFFF",
        )


class OutputFileGenerationView(ctk.CTkFrame):
    def __init__(self, master, app) -> None:
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.service = OutputFileGenerationService(app.config)
        self._running = False
        self._cancel_event: threading.Event | None = None
        self._events: queue.Queue[tuple[str, object]] = queue.Queue()
        self._db_lookup: dict[str, dict] = {}
        self._interface_lookup: dict[str, str] = {}
        self._interface_values: tuple[str, ...] = ()
        self._last_manual_interface = ""
        self._selected_input_date: date | None = None
        self._last_output_folder: Path | None = None

        self._build_header()
        self._build_footer()
        self._build_body()
        self._refresh_targets()

    def _build_header(self) -> None:
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(side="top", fill="x", padx=25, pady=(25, 15))
        IconButton(
            header,
            text=f"< {t('common.back')}",
            width=100,
            command=lambda: self.app.show_view("home"),
        ).pack(side="left")
        title_box = ctk.CTkFrame(header, fg_color="transparent")
        title_box.pack(side="left", padx=(16, 0), fill="x", expand=True)
        ctk.CTkLabel(
            title_box,
            text=t("output_files.title"),
            font=ctk.CTkFont(size=24, weight="bold"),
            text_color=("#0f172a", "#ffffff"),
            anchor="w",
        ).pack(fill="x", anchor="w")
        ctk.CTkLabel(
            title_box,
            text=t("output_files.subtitle"),
            text_color=("gray45", "gray60"),
            anchor="w",
            justify="left",
            width=380,
            wraplength=380,
        ).pack(fill="x", anchor="w", pady=(3, 0))

    def _build_body(self) -> None:
        self.body = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.body.pack(side="top", fill="both", expand=True, padx=25, pady=(0, 12))

        config_card = CardFrame(self.body)
        config_card.pack(fill="x", pady=(0, 14))
        form = ctk.CTkFrame(config_card, fg_color="transparent")
        form.pack(fill="x", padx=20, pady=20)
        form.grid_columnconfigure(1, weight=1)
        form.grid_columnconfigure(2, weight=0)

        ctk.CTkLabel(form, text=t("output_files.prod_db"), width=170, anchor="w").grid(
            row=0, column=0, padx=4, pady=5, sticky="w"
        )
        self.db_var = ctk.StringVar(value="—")
        self.db_menu = ctk.CTkOptionMenu(form, values=["—"], variable=self.db_var)
        self.db_menu.grid(
            row=0,
            column=1,
            columnspan=2,
            padx=4,
            pady=5,
            sticky="ew",
        )

        ctk.CTkLabel(form, text=t("output_files.process_ref"), width=170, anchor="w").grid(
            row=1, column=0, padx=4, pady=5, sticky="w"
        )
        self.process_ref_entry = ctk.CTkEntry(
            form,
            placeholder_text="1234567",
            font=ctk.CTkFont(family="Consolas", size=13),
        )
        self.process_ref_entry.grid(
            row=1,
            column=1,
            columnspan=2,
            padx=4,
            pady=5,
            sticky="ew",
        )
        self.process_ref_entry.bind("<Return>", lambda _event: self._start())

        ctk.CTkLabel(
            form,
            text=t("output_files.interface_output"),
            width=170,
            anchor="w",
        ).grid(row=2, column=0, padx=4, pady=5, sticky="w")
        interface_options = _interface_options(self.service.supported_interfaces())
        self._interface_lookup = dict(interface_options)
        self._interface_values = tuple(label for label, _code in interface_options)
        initial_interface = (
            self._interface_values[0]
            if self._interface_values
            else _AUTO_INTERFACE_SEPARATOR
        )
        self._last_manual_interface = initial_interface
        self.interface_var = ctk.StringVar(value=initial_interface)
        self.interface_menu = _SearchableComboBox(
            form,
            values=list(self._interface_values) or [_AUTO_INTERFACE_SEPARATOR],
            variable=self.interface_var,
            command=self._on_interface_selected,
        )
        self.interface_menu.grid(row=2, column=1, padx=4, pady=5, sticky="ew")
        self.interface_menu.bind("<KeyRelease>", self._on_interface_key_release)
        self.interface_menu.bind("<Return>", self._on_interface_return)

        self.auto_detect_var = ctk.BooleanVar(value=_DEFAULT_AUTO_DETECT)
        self.auto_detect_checkbox = ctk.CTkCheckBox(
            form,
            text=t("output_files.detect_interface_automatically"),
            variable=self.auto_detect_var,
            command=self._on_auto_detect_changed,
        )
        self.auto_detect_checkbox.grid(
            row=2,
            column=2,
            padx=(12, 4),
            pady=5,
            sticky="nw",
        )

        ctk.CTkLabel(
            form,
            text=t("output_files.input_launch_date"),
            width=170,
            anchor="w",
        ).grid(row=3, column=0, padx=4, pady=5, sticky="w")
        self.input_date_display_var = ctk.StringVar(
            value=t("output_files.choose_input_date")
        )
        self.input_date_frame = ctk.CTkFrame(form, fg_color="transparent")
        self.input_date_frame.grid_columnconfigure(0, weight=1)
        self.input_date_entry = ctk.CTkEntry(
            self.input_date_frame,
            textvariable=self.input_date_display_var,
            state="readonly",
        )
        self.input_date_entry.grid(row=0, column=0, sticky="ew")
        self.input_date_calendar_button = ctk.CTkButton(
            self.input_date_frame,
            text=t("common.calendar"),
            width=105,
            command=self._open_input_date_calendar,
        )
        self.input_date_calendar_button.grid(
            row=0,
            column=1,
            padx=(8, 0),
        )
        self.input_date_frame.grid(
            row=3,
            column=1,
            padx=4,
            pady=5,
            sticky="ew",
        )

        self.auto_detect_date_var = ctk.BooleanVar(value=_DEFAULT_AUTO_DETECT_DATE)
        self.auto_detect_date_checkbox = ctk.CTkCheckBox(
            form,
            text=t("output_files.detect_date_automatically"),
            variable=self.auto_detect_date_var,
            command=self._on_auto_detect_date_changed,
        )
        self.auto_detect_date_checkbox.grid(
            row=3,
            column=2,
            padx=(12, 4),
            pady=5,
            sticky="w",
        )

        self.disclaimer_expanded_var = ctk.BooleanVar(
            value=_DEFAULT_DISCLAIMER_EXPANDED
        )
        self.disclaimer_button = ctk.CTkButton(
            form,
            text=t("output_files.disclaimer_show"),
            command=self._toggle_disclaimer,
            fg_color="transparent",
            hover_color=("#dbeafe", "#1e3a5f"),
            text_color=("#1D4ED8", "#60A5FA"),
            anchor="w",
            width=150,
            height=28,
        )
        # CustomTkinter rejects Tk's takefocus option in CTkButton.__init__.
        # Apply the native Frame option only after CTk has built the widget.
        tk.Frame.configure(self.disclaimer_button, takefocus=True)
        self.disclaimer_button.grid(
            row=4,
            column=0,
            columnspan=3,
            padx=4,
            pady=(10, 0),
            sticky="w",
        )
        # CTkButton.bind targets its internal canvas. Bind the focusable outer
        # Tk widget explicitly so keyboard activation works when Tab focuses it.
        tk.Misc.bind(
            self.disclaimer_button,
            "<Return>",
            self._activate_disclaimer_from_keyboard,
            add="+",
        )
        tk.Misc.bind(
            self.disclaimer_button,
            "<space>",
            self._activate_disclaimer_from_keyboard,
            add="+",
        )
        self.disclaimer_body = ctk.CTkLabel(
            form,
            text=t("output_files.disclaimer_body"),
            justify="left",
            anchor="w",
            width=500,
            wraplength=500,
            text_color=("#1D4ED8", "#60A5FA"),
        )
        self.disclaimer_body.grid(
            row=5,
            column=0,
            columnspan=3,
            padx=4,
            pady=(4, 4),
            sticky="ew",
        )
        self._sync_disclaimer_visibility()

        ctk.CTkLabel(
            form,
            text=t("output_files.read_only_note"),
            justify="left",
            anchor="w",
            width=500,
            wraplength=500,
            text_color=("#475569", "#94a3b8"),
        ).grid(row=6, column=0, columnspan=3, padx=4, pady=(12, 0), sticky="ew")

        result_card = CardFrame(self.body)
        result_card.pack(fill="both", expand=True)
        result_inner = ctk.CTkFrame(result_card, fg_color="transparent")
        result_inner.pack(fill="both", expand=True, padx=20, pady=20)
        ctk.CTkLabel(
            result_inner,
            text=t("output_files.preview"),
            anchor="w",
            font=ctk.CTkFont(size=15, weight="bold"),
        ).pack(fill="x", pady=(0, 8))
        self.preview = ctk.CTkTextbox(
            result_inner,
            height=230,
            wrap="none",
            font=ctk.CTkFont(family="Consolas", size=12),
        )
        self.preview.pack(fill="both", expand=True)
        self.preview.configure(state="disabled")

    def _build_footer(self) -> None:
        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.pack(side="bottom", fill="x", padx=25, pady=(0, 20))
        self.progress = ctk.CTkProgressBar(footer, mode="indeterminate", height=7)
        self.progress.pack(fill="x", pady=(0, 8))
        self.progress.set(0)
        self.status_label = ctk.CTkLabel(
            footer,
            text=t("output_files.ready"),
            anchor="w",
            justify="left",
            width=480,
            wraplength=480,
            text_color=("#475569", "#94a3b8"),
        )
        self.status_label.pack(fill="x", pady=(0, 8))
        action_row = ctk.CTkFrame(footer, fg_color="transparent")
        action_row.pack(fill="x")
        self.open_folder_button = ctk.CTkButton(
            action_row,
            text=t("output_files.open_folder"),
            width=145,
            command=self._open_folder,
            fg_color=("#64748b", "#475569"),
            hover_color=("#475569", "#334155"),
        )
        self.open_folder_button.pack(side="right", padx=(8, 0))
        self.cancel_button = ctk.CTkButton(
            action_row,
            text=t("common.cancel"),
            width=110,
            state="disabled",
            command=self._cancel,
            fg_color=("#D9534F", "#A8322C"),
            hover_color=("#C9302C", "#8B1F1A"),
        )
        self.cancel_button.pack(side="right", padx=(8, 0))
        self.generate_button = IconButton(
            action_row,
            text=t("output_files.generate"),
            width=145,
            command=self._start,
        )
        self.generate_button.pack(side="right", padx=(8, 0))

    def on_show(self) -> None:
        if not self._running:
            self._refresh_targets()

    def on_hide(self) -> None:
        pass

    def _refresh_targets(self) -> None:
        previous = self._selected_database()
        self._db_lookup = {}
        for country in _TARGET_COUNTRIES:
            for target in self.service.targets(country):
                option = dict(target)
                option["country"] = country
                label = self._database_label(option)
                self._db_lookup[label] = option
        labels = list(self._db_lookup)
        self.db_menu.configure(values=labels or ["—"])
        selected = self._matching_database_label(previous)
        self.db_var.set(selected or (labels[0] if labels else "—"))
        if not labels:
            self.status_label.configure(
                text=t("output_files.no_prod_credentials"),
                text_color=("#9A6700", "#D29922"),
            )
        elif self.status_label.cget("text") == t("output_files.no_prod_credentials"):
            self.status_label.configure(
                text=t("output_files.ready"),
                text_color=("#475569", "#94a3b8"),
            )

    def _selected_database(self) -> dict | None:
        return self._db_lookup.get(self.db_var.get())

    @staticmethod
    def _database_label(target: dict) -> str:
        country = str(target.get("country") or "").strip().lower()
        country_label = _COUNTRY_LABELS.get(country, country.title())
        env = str(target.get("env") or "")
        tag = _ENV_TAG.get(env, env.upper())
        login = (
            f"  ·  {target['credential_label']}"
            if target.get("credential_count", 1) > 1
            else ""
        )
        return (
            f"{country_label}  ·  {tag}  ·  "
            f"{target['label']}  ·  {target['id']}{login}"
        )

    def _matching_database_label(self, previous: dict | None) -> str | None:
        if previous is None:
            return None
        key = (
            str(previous.get("country") or "").lower(),
            str(previous.get("database_key") or "").upper(),
            str(previous.get("id") or "").upper(),
            str(previous.get("credential_key") or "").upper(),
        )
        return next(
            (
                label
                for label, target in self._db_lookup.items()
                if (
                    str(target.get("country") or "").lower(),
                    str(target.get("database_key") or "").upper(),
                    str(target.get("id") or "").upper(),
                    str(target.get("credential_key") or "").upper(),
                )
                == key
            ),
            None,
        )

    def _matching_interface_label(self, value: object) -> str | None:
        needle = str(value or "").strip().casefold()
        if not needle:
            return None
        return next(
            (
                label
                for label in self._interface_values
                if label.casefold() == needle
            ),
            None,
        )

    def _on_interface_selected(self, value: str) -> None:
        if bool(self.auto_detect_var.get()):
            return
        label = self._matching_interface_label(value)
        if label is None:
            return
        self._last_manual_interface = label
        self.interface_var.set(label)
        self.interface_menu.configure(values=list(self._interface_values))
        self._hide_interface_suggestions()

    def _on_interface_key_release(self, event: object | None = None) -> None:
        if bool(self.auto_detect_var.get()) or getattr(self, "_running", False):
            return
        matches = _filtered_interface_values(
            self._interface_values,
            str(self.interface_var.get() or ""),
        )
        self.interface_menu.configure(values=list(matches))
        keysym = str(getattr(event, "keysym", "") or "")
        if not matches:
            self._hide_interface_suggestions()
            return
        if keysym in {
            "Escape",
            "Return",
            "Tab",
            "Up",
            "Down",
            "Left",
            "Right",
        }:
            return
        self._show_interface_suggestions(matches)

    def _show_interface_suggestions(self, values: tuple[str, ...]) -> None:
        self.interface_menu.show_suggestions(values)

    def _hide_interface_suggestions(self) -> None:
        self.interface_menu.hide_suggestions()

    def _on_interface_return(self, _event: object | None = None) -> str:
        if bool(self.auto_detect_var.get()):
            return "break"
        typed = str(self.interface_var.get() or "")
        exact = self._matching_interface_label(typed)
        matches = _filtered_interface_values(self._interface_values, typed)
        selected = exact or (matches[0] if len(matches) == 1 else None)
        if selected is not None:
            self._on_interface_selected(selected)
        return "break"

    def _close_interface_dropdown(self) -> None:
        self._hide_interface_suggestions()

    def _on_auto_detect_changed(self) -> None:
        automatic = bool(self.auto_detect_var.get())
        if automatic:
            self._close_interface_dropdown()
            current = str(self.interface_var.get() or "")
            label = self._matching_interface_label(current)
            if label is not None:
                self._last_manual_interface = label
            self.interface_menu.configure(
                values=[_AUTO_INTERFACE_SEPARATOR],
                state="disabled",
            )
            self.interface_var.set(_AUTO_INTERFACE_SEPARATOR)
            return

        values = list(self._interface_values)
        restored = self._last_manual_interface
        if restored not in self._interface_lookup:
            restored = values[0] if values else _AUTO_INTERFACE_SEPARATOR
        self.interface_menu.configure(
            values=values or [_AUTO_INTERFACE_SEPARATOR],
            state=(
                "disabled"
                if getattr(self, "_running", False) or not values
                else "normal"
            ),
        )
        self.interface_var.set(restored)

    def _on_auto_detect_date_changed(self) -> None:
        if bool(self.auto_detect_date_var.get()):
            self._selected_input_date = None
            self.input_date_display_var.set(t("output_files.choose_input_date"))
        self._sync_input_date_controls()

    def _toggle_disclaimer(self) -> None:
        self.disclaimer_expanded_var.set(
            not bool(self.disclaimer_expanded_var.get())
        )
        self._sync_disclaimer_visibility()

    def _activate_disclaimer_from_keyboard(
        self,
        _event: object | None = None,
    ) -> str:
        self._toggle_disclaimer()
        return "break"

    def _sync_disclaimer_visibility(self) -> None:
        expanded = bool(self.disclaimer_expanded_var.get())
        if expanded:
            self.disclaimer_body.grid()
        else:
            self.disclaimer_body.grid_remove()
        self.disclaimer_button.configure(
            text=t(
                "output_files.disclaimer_hide"
                if expanded
                else "output_files.disclaimer_show"
            )
        )

    def _sync_input_date_controls(self, running: bool | None = None) -> None:
        is_running = (
            bool(getattr(self, "_running", False))
            if running is None
            else bool(running)
        )
        manual = not bool(self.auto_detect_date_var.get())
        if manual:
            self.input_date_frame.grid()
        else:
            self.input_date_frame.grid_remove()
        enabled = manual and not is_running
        self.input_date_entry.configure(
            state="readonly" if enabled else "disabled"
        )
        self.input_date_calendar_button.configure(
            state="normal" if enabled else "disabled"
        )

    def _open_input_date_calendar(self) -> None:
        if (
            getattr(self, "_running", False)
            or bool(self.auto_detect_date_var.get())
        ):
            return
        CalendarDialog(
            self,
            selected=self._selected_input_date or date.today(),
            on_pick=self._set_input_date,
        )

    def _set_input_date(self, value: date) -> None:
        self._selected_input_date = value
        self.input_date_display_var.set(value.isoformat())
        self._sync_input_date_controls()

    def _start(self) -> None:
        if self._running:
            return
        database = self._selected_database()
        if database is None:
            messagebox.showerror(
                t("common.error"),
                t("output_files.invalid_db"),
                parent=self,
            )
            return
        country = str(database.get("country") or "").strip().lower()
        if country not in _TARGET_COUNTRIES:
            messagebox.showerror(
                t("common.error"),
                t("output_files.invalid_db"),
                parent=self,
            )
            return
        process_ref = self.process_ref_entry.get().strip()
        if not process_ref:
            messagebox.showerror(
                t("common.error"),
                t("output_files.process_required"),
                parent=self,
            )
            return
        try:
            validate_process_ref(process_ref)
        except ValueError:
            messagebox.showerror(
                t("common.error"),
                t("output_files.process_invalid"),
                parent=self,
            )
            return
        interface_code: str | None = None
        if not bool(self.auto_detect_var.get()):
            label = self._matching_interface_label(self.interface_var.get())
            interface_code = self._interface_lookup.get(label or "")
            if interface_code is None:
                messagebox.showerror(
                    t("common.error"),
                    t("output_files.interface_required"),
                    parent=self,
                )
                return
            self._last_manual_interface = label
            self.interface_var.set(label)
            self.interface_menu.configure(values=list(self._interface_values))
        input_file_date: str | None = None
        if not bool(self.auto_detect_date_var.get()):
            if self._selected_input_date is None:
                messagebox.showerror(
                    t("common.error"),
                    t("output_files.input_date_required"),
                    parent=self,
                )
                return
            input_file_date = self._selected_input_date.strftime("%Y%m%d")
        target = OracleTarget(
            country=country,
            database_key=str(database.get("database_key") or database["id"]),
            credential_key=str(database.get("credential_key") or ""),
            tns=str(database["id"]),
            label=str(database.get("label") or database["id"]),
        )
        request = GenerationRequest(
            target=target,
            process_ref_no=process_ref,
            overwrite=True,
            interface_code=interface_code,
            input_file_date=input_file_date,
        )

        self._running = True
        self._cancel_event = threading.Event()
        self._set_controls_running(True)
        self._set_preview("")
        self.status_label.configure(
            text=t("output_files.working"),
            text_color=("#475569", "#94a3b8"),
        )
        self.progress.start()
        threading.Thread(
            target=self._worker,
            args=(request, self._cancel_event),
            daemon=True,
        ).start()
        self.after(75, self._poll_events)

    def _worker(
        self,
        request: GenerationRequest,
        cancel_event: threading.Event,
    ) -> None:
        try:
            result = self.service.generate(request, cancel_event=cancel_event)
        except GenerationCancelled:
            self._events.put(("cancelled", None))
        except OutputFileGenerationError as exc:
            log.warning(
                "Output reconstruction failed country=%s tns=%s process_ref=%s "
                "reason=%s",
                request.target.country,
                request.target.tns,
                request.process_ref_no,
                _safe_log_message(exc),
            )
            self._events.put(("error", str(exc)))
        except Exception:
            log.exception("Unexpected output-file generation failure")
            self._events.put(("error", t("output_files.unexpected_error")))
        else:
            self._events.put(("success", result))

    def _poll_events(self) -> None:
        terminal = False
        while True:
            try:
                kind, payload = self._events.get_nowait()
            except queue.Empty:
                break
            terminal = True
            if kind == "success":
                self._finish_success(payload)
            elif kind == "cancelled":
                self._finish_cancelled()
            else:
                self._finish_error(str(payload))
        if self._running and not terminal:
            self.after(75, self._poll_events)

    def _cancel(self) -> None:
        if not self._running or self._cancel_event is None:
            return
        self._cancel_event.set()
        self.cancel_button.configure(state="disabled", text=t("output_files.cancelling"))
        self.status_label.configure(text=t("output_files.cancel_requested"))

    def _finish_success(self, payload: object) -> None:
        result = payload
        if not isinstance(result, GenerationResult):
            self._finish_error(t("output_files.unexpected_error"))
            return
        self._last_output_folder = result.output_path.parent
        self._finish_common()
        counts = ", ".join(
            f"{status}={count}" for status, count in sorted(result.status_counts.items())
        )
        source_label = t(f"output_files.source_{result.source.value}")
        self.status_label.configure(
            text=t(
                "output_files.completed",
                file=result.output_path.name,
                rows=result.body_count,
                source=source_label,
            ),
            text_color=("#1A7F37", "#3FB950"),
        )
        body_preview = list(result.lines[1:-1][:12])
        omitted = result.body_count - len(body_preview)
        preview_lines = [
            t("output_files.preview_file", value=result.output_path),
            t(
                "output_files.preview_interface",
                value=f"{result.input_code} -> {result.output_code}",
            ),
            t("output_files.preview_process", value=result.process_ref_no),
            t("output_files.preview_source", value=source_label),
            t("output_files.preview_date", value=result.file_date),
            t(
                "output_files.preview_rows",
                value=(
                    f"{result.body_count} ({counts})"
                    if counts
                    else str(result.body_count)
                ),
            ),
            f"SHA-256: {result.sha256}",
            "",
            result.lines[0],
            *body_preview,
        ]
        if omitted > 0:
            preview_lines.append(t("output_files.preview_omitted", count=omitted))
        preview_lines.append(result.lines[-1])
        self._set_preview("\n".join(preview_lines))

    def _finish_cancelled(self) -> None:
        self._finish_common()
        self.status_label.configure(
            text=t("output_files.cancelled"),
            text_color=("#9A6700", "#D29922"),
        )

    def _finish_error(self, message: str) -> None:
        self._finish_common()
        self.status_label.configure(
            text=message,
            text_color=("#CF222E", "#FF6B6B"),
        )
        self._set_preview(message)

    def _finish_common(self) -> None:
        self._running = False
        self._cancel_event = None
        self.progress.stop()
        self.progress.set(0)
        self.cancel_button.configure(text=t("common.cancel"))
        self._set_controls_running(False)

    def _set_controls_running(self, running: bool) -> None:
        if running:
            self._close_interface_dropdown()
        state = "disabled" if running else "normal"
        for widget in (
            self.db_menu,
            self.process_ref_entry,
            self.auto_detect_checkbox,
            self.auto_detect_date_checkbox,
        ):
            widget.configure(state=state)
        self.interface_menu.configure(
            state=(
                "disabled"
                if running
                or bool(self.auto_detect_var.get())
                or not self._interface_values
                else "normal"
            )
        )
        self._sync_input_date_controls(running=running)
        self.generate_button.configure(state=state)
        self.cancel_button.configure(state="normal" if running else "disabled")

    def _set_preview(self, value: str) -> None:
        self.preview.configure(state="normal")
        self.preview.delete("1.0", "end")
        if value:
            self.preview.insert("1.0", value)
        self.preview.configure(state="disabled")

    def _open_folder(self) -> None:
        folder = getattr(self, "_last_output_folder", None)
        if folder is None:
            database = self._selected_database()
            country = str((database or {}).get("country") or "").strip().lower()
            folder_name = _COUNTRY_LABELS.get(country)
            folder = (
                OUTPUT_FILES_OUT_DIR / folder_name
                if folder_name
                else OUTPUT_FILES_OUT_DIR
            )
        try:
            folder.mkdir(parents=True, exist_ok=True)
            os.startfile(str(folder))
        except OSError as exc:
            messagebox.showerror(
                t("common.error"),
                t("output_files.open_failed", error=str(exc)),
                parent=self,
            )
