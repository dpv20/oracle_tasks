"""Spools Savings view - extract/apply Savings / IC account .INC files."""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
import tkinter as tk
import threading
import zipfile
from datetime import datetime
from pathlib import Path

import customtkinter as ctk
from tkinter import filedialog, messagebox

from i18n import t
from paths import SPOOLS_SAVINGS_OUT_DIR
from settings.config import decrypt_password
from settings.credentials import to_sqlcl_arg
from spools_cl_accounts import databases as dbs
from spools_cl_accounts.sqlcl import SqlclRunner
from spools_savings_accounts.spool_savings_engine import (
    MAX_PARALLEL_SAVINGS_ACCOUNTS,
    SavingsAccountResult,
    savings_output_path_for,
    SpoolSavingsEngine,
    SpoolSavingsStatus,
    has_savings_template,
    is_valid_savings_account,
    parse_savings_accounts,
    worker_count_for,
)

from .widgets import AccountStatusRow, CardFrame, IconButton, SectionLabel

log = logging.getLogger(__name__)

MODE_EXTRACT = "extract"
MODE_EXTRACT_ONLY = "extract_only"
MODE_APPLY_EXISTING = "apply_existing"

_ENV_DISPLAY_ORDER = ("prod", "bup_prod", "qa", "bup_qa", "dev")
_DEST_ENV_DISPLAY_ORDER = ("qa", "bup_qa", "dev")
_COUNTRIES = [(c, c.title()) for c in dbs.countries()]
_APPLY_EXISTING_COUNTRIES = [
    (c, c.title())
    for c in dbs.countries()
    if any(dbs.databases_for(c, env=env) for env in _DEST_ENV_DISPLAY_ORDER)
]
_ENV_TAG = {
    "prod":     "PROD",
    "bup_prod": "BUP PROD",
    "qa":       "QA",
    "bup_qa":   "BUP QA",
    "dev":      "DEV",
}

_PRIMARY_BUTTON_FG = ("#1F6FEB", "#1A5BBF")
_PRIMARY_BUTTON_HOVER = ("#1A5BBF", "#154A9F")
_CANCEL_BUTTON_FG = ("#D9534F", "#A8322C")
_CANCEL_BUTTON_HOVER = ("#C9302C", "#8B1F1A")
_TERMINAL_STATUSES = {
    SpoolSavingsStatus.OK,
    SpoolSavingsStatus.VERIFIED,
    SpoolSavingsStatus.WARNING,
    SpoolSavingsStatus.ERROR,
    SpoolSavingsStatus.CANCELLED,
}
_APPLY_SUCCESS_STATUSES = {
    SpoolSavingsStatus.OK,
    SpoolSavingsStatus.VERIFIED,
    SpoolSavingsStatus.WARNING,
}
_BULK_DIALOG_SIZE = (560, 430)


class SpoolsSavingsView(ctk.CTkFrame):
    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self._status_rows: dict[str, AccountStatusRow] = {}
        self._pending_accounts: list[str] = []
        self._inject_flags: dict[str, bool] = {}
        self._completed_steps = 0
        self._run_id = 0
        self._active_summary_phase: str | None = None
        self._cancel_event: threading.Event | None = None
        self._running = False
        self._db_lookup: dict[str, dict] = {}
        self._dest_db_lookup: dict[str, dict] = {}
        self._country_lookup: dict[str, str] = {}
        self._existing_spool_path: Path | None = None
        self.serial_accounts_var = tk.BooleanVar(value=False)

        # ── header ──
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(side="top", fill="x", padx=25, pady=(25, 15))
        ctk.CTkLabel(
            header, text=t("spools_savings.title"),
            font=ctk.CTkFont(size=24, weight="bold"),
            text_color=("#0f172a", "#ffffff"),
        ).pack(side="left")

        # ── config card ──
        self.config_card = CardFrame(self)
        self.config_card.pack(side="top", fill="x", padx=25, pady=(0, 15))

        # Grid inside config card with inner padding
        form = ctk.CTkFrame(self.config_card, fg_color="transparent")
        form.pack(fill="x", padx=20, pady=20)
        form.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(form, text=t("spools_savings.mode"), anchor="w", width=160).grid(
            row=0, column=0, padx=4, pady=4, sticky="w",
        )
        self.mode_segment = ctk.CTkSegmentedButton(
            form,
            values=[
                t("spools_savings.mode.extract"),
                t("spools_savings.mode.extract_only"),
                t("spools_savings.mode.apply_existing"),
            ],
            command=lambda _v: self._on_mode_change(),
        )
        self.mode_segment.grid(row=0, column=1, padx=4, pady=4, sticky="ew")
        self.mode_segment.set(t("spools_savings.mode.extract"))

        ctk.CTkLabel(form, text=t("spools_savings.country"), anchor="w", width=160).grid(
            row=1, column=0, padx=4, pady=4, sticky="w",
        )
        self._country_lookup = {label: cid for cid, label in _COUNTRIES}
        self.country_var = ctk.StringVar(value=_COUNTRIES[0][1] if _COUNTRIES else "")
        self.country_menu = ctk.CTkOptionMenu(
            form,
            values=[label for _, label in _COUNTRIES] or ["—"],
            variable=self.country_var,
            command=lambda _v: self._refresh_db_options(),
        )
        self.country_menu.grid(row=1, column=1, padx=4, pady=4, sticky="ew")

        self.source_label = ctk.CTkLabel(form, text=t("spools_savings.source_db"), anchor="w", width=160)
        self.source_label.grid(row=2, column=0, padx=4, pady=4, sticky="w")
        self.db_var = ctk.StringVar(value="")
        self.db_menu = ctk.CTkOptionMenu(form, values=["—"], variable=self.db_var)
        self.db_menu.grid(row=2, column=1, padx=4, pady=4, sticky="ew")

        self.dest_label = ctk.CTkLabel(form, text=t("spools_savings.destination_db"), anchor="w", width=160)
        self.dest_label.grid(row=3, column=0, padx=4, pady=4, sticky="w")
        self.dest_db_var = ctk.StringVar(value="")
        self.dest_db_menu = ctk.CTkOptionMenu(form, values=["-"], variable=self.dest_db_var)
        self.dest_db_menu.grid(row=3, column=1, padx=4, pady=4, sticky="ew")

        self.existing_spool_label = ctk.CTkLabel(
            form, text=t("spools_savings.existing_spool"), anchor="w", width=160,
        )
        self.existing_spool_frame = ctk.CTkFrame(form, fg_color="transparent")
        self.existing_spool_var = ctk.StringVar(value="")
        self.existing_spool_entry = ctk.CTkEntry(
            self.existing_spool_frame,
            textvariable=self.existing_spool_var,
            state="readonly",
        )
        self.existing_spool_entry.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(
            self.existing_spool_frame,
            text=t("spools_savings.browse_spool"),
            width=110,
            command=self._on_browse_existing_spool,
        ).pack(side="left", padx=(8, 0))
        self.existing_spool_label.grid(row=4, column=0, padx=4, pady=4, sticky="w")
        self.existing_spool_frame.grid(row=4, column=1, padx=4, pady=4, sticky="ew")

        # ── accounts card ──
        self.accounts_card = CardFrame(self)
        self.accounts_card.pack(side="top", fill="both", expand=True, padx=25, pady=(0, 15))

        # Inner padding for accounts panel
        accounts_inner = ctk.CTkFrame(self.accounts_card, fg_color="transparent")
        accounts_inner.pack(fill="both", expand=True, padx=20, pady=20)

        # ── account input row ──
        self.account_row = ctk.CTkFrame(accounts_inner, fg_color="transparent")
        self.account_row.pack(fill="x", padx=4, pady=(0, 4))
        ctk.CTkLabel(
            self.account_row, text=t("spools_savings.account_number"), anchor="w", width=140,
        ).pack(side="left", padx=4)
        self.account_entry = ctk.CTkEntry(
            self.account_row,
            font=ctk.CTkFont(family="Consolas", size=12),
            placeholder_text="e.g. 8000109678685",
        )
        self.account_entry.pack(side="left", padx=4, fill="x", expand=True)
        self.account_entry.bind("<Return>", lambda _e: self._on_add_account())
        IconButton(
            self.account_row, text=t("spools_savings.add_account"), width=90,
            command=self._on_add_account,
        ).pack(side="left", padx=4)
        IconButton(
            self.account_row, text=t("spools_savings.add_many_accounts"), width=120,
            command=self._open_bulk_accounts_dialog,
        ).pack(side="left", padx=4)
        self.pending_header = SectionLabel(accounts_inner, text=t("spools_savings.accounts_summary", n=0))
        self.pending_header.pack(anchor="w", padx=6, pady=(10, 4))

        self.account_split = ctk.CTkFrame(accounts_inner, fg_color="transparent")
        self.account_split.pack(fill="both", expand=True, padx=4, pady=(0, 4))
        self.account_split.grid_columnconfigure(0, weight=1, uniform="savings_account_lists")
        self.account_split.grid_columnconfigure(1, weight=1, uniform="savings_account_lists")
        self.account_split.grid_rowconfigure(1, weight=1)

        self.extract_only_header = SectionLabel(
            self.account_split, text=t("spools_savings.extract_only_header", n=0),
        )
        self.extract_only_header.grid(row=0, column=0, sticky="ew", padx=(0, 6), pady=(0, 4))
        self.inject_header_bar = ctk.CTkFrame(self.account_split, fg_color="transparent")
        self.inject_header_bar.grid(row=0, column=1, sticky="ew", padx=(6, 0), pady=(0, 4))
        self.inject_header = SectionLabel(self.inject_header_bar, text=t("spools_savings.inject_header", n=0))
        self.inject_header.pack(side="left")
        self.serial_accounts_check = ctk.CTkCheckBox(
            self.inject_header_bar,
            text=t("spools_savings.serial_accounts"),
            variable=self.serial_accounts_var,
            width=150,
        )
        self.serial_accounts_check.pack(side="right", padx=(8, 0))

        self.extract_only_frame = ctk.CTkScrollableFrame(self.account_split, height=180)
        self.extract_only_frame.grid(row=1, column=0, sticky="nsew", padx=(0, 6))
        self.inject_frame = ctk.CTkScrollableFrame(self.account_split, height=180)
        self.inject_frame.grid(row=1, column=1, sticky="nsew", padx=(6, 0))
        self._bind_account_list_resize()
        self._render_pending_accounts()

        # ── actions ──
        self.actions_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.actions_frame.pack(fill="x", padx=25, pady=(0, 4))
        self.run_btn = IconButton(
            self.actions_frame, text=t("spools_savings.run_extract_only"), width=220,
            command=self._on_run,
        )
        self.run_btn.pack(side="left")
        self.open_folder_btn = ctk.CTkButton(
            self.actions_frame, text=t("spools_savings.open_folder"), width=220,
            command=self._on_open_folder,
        )
        self.open_folder_btn.pack(side="left", padx=(10, 0))
        self.summary_label = ctk.CTkLabel(
            self.actions_frame,
            text="",
            anchor="e",
            text_color=("gray35", "gray70"),
        )
        self.summary_label.pack(side="right", padx=8, fill="x", expand=True)

        # ── results card ──
        self.results_card = CardFrame(self)
        # NOT packed by default, shown dynamically during extraction/apply

        results_inner = ctk.CTkFrame(self.results_card, fg_color="transparent")
        results_inner.pack(fill="both", expand=True, padx=20, pady=20)

        results_header_row = ctk.CTkFrame(results_inner, fg_color="transparent")
        results_header_row.pack(fill="x", pady=(0, 10))

        self.results_title_label = SectionLabel(results_header_row, text="Execution Progress")
        self.results_title_label.pack(side="left")

        self.back_to_accounts_btn = IconButton(
            results_header_row,
            text="← " + ("Volver" if self.app.config.get("language") == "es" else "Back"),
            width=100,
            height=28,
            command=self._show_accounts_card
        )
        self.back_to_accounts_btn.pack(side="right")

        self.result_detail_label = ctk.CTkLabel(
            results_inner,
            text="",
            anchor="w",
            justify="left",
            wraplength=760,
            text_color=("gray35", "gray70"),
        )
        self.result_detail_label.pack(fill="x", pady=(0, 10))

        self.results_frame = ctk.CTkScrollableFrame(
            results_inner,
            fg_color=("#f8fafc", "#0f172a"),
            border_color=("#e2e8f0", "#1e293b"),
            border_width=1,
            corner_radius=10
        )
        self.results_frame.pack(fill="both", expand=True)

        self._apply_mode_visibility()
        self._refresh_db_options()

    def _current_mode(self) -> str:
        mode = self.mode_segment.get()
        if mode == t("spools_savings.mode.apply_existing"):
            return MODE_APPLY_EXISTING
        if mode == t("spools_savings.mode.extract_only"):
            return MODE_EXTRACT_ONLY
        return MODE_EXTRACT

    def _is_apply_existing_mode(self) -> bool:
        return self._current_mode() == MODE_APPLY_EXISTING

    def _is_extract_only_mode(self) -> bool:
        return self._current_mode() == MODE_EXTRACT_ONLY

    def _country_options(self) -> list[tuple[str, str]]:
        return _APPLY_EXISTING_COUNTRIES if self._is_apply_existing_mode() else _COUNTRIES

    def _refresh_country_options(self, previous_country: str | None = None) -> None:
        options = self._country_options()
        labels = [label for _, label in options] or ["—"]
        self._country_lookup = {label: cid for cid, label in options}
        self.country_menu.configure(values=labels)
        selected = next((label for cid, label in options if cid == previous_country), None)
        self.country_var.set(selected or labels[0])

    def _on_mode_change(self) -> None:
        previous_country = self._selected_country_id()
        self._refresh_country_options(previous_country)
        self._apply_mode_visibility()
        self._refresh_db_options()
        self._refresh_run_button()
        self._render_pending_accounts()

    def _apply_mode_visibility(self) -> None:
        if self._is_apply_existing_mode():
            self.source_label.grid_remove()
            self.db_menu.grid_remove()
            self.dest_label.grid()
            self.dest_db_menu.grid()
            self.existing_spool_label.grid()
            self.existing_spool_frame.grid()
            self.accounts_card.pack_forget()
            self.results_card.pack_forget()
            return

        self.source_label.grid()
        self.db_menu.grid()
        if self._is_extract_only_mode():
            self.dest_label.grid_remove()
            self.dest_db_menu.grid_remove()
        else:
            self.dest_label.grid()
            self.dest_db_menu.grid()
        self.existing_spool_label.grid_remove()
        self.existing_spool_label.grid_remove()
        self.existing_spool_frame.grid_remove()
        self.results_card.pack_forget()
        self._apply_account_list_visibility()
        if not self.accounts_card.winfo_manager():
            self.accounts_card.pack(side="top", fill="both", expand=True, padx=25, pady=(0, 15), before=self.actions_frame)

    def _apply_account_list_visibility(self) -> None:
        if not hasattr(self, "inject_header"):
            return
        if self._is_extract_only_mode():
            self.inject_header_bar.grid_remove()
            self.inject_frame.grid_remove()
            self.account_split.grid_columnconfigure(0, weight=1, uniform="")
            self.account_split.grid_columnconfigure(1, weight=0, uniform="")
            self.extract_only_header.grid_configure(columnspan=2, padx=(0, 0))
            self.extract_only_frame.grid(row=1, column=0, columnspan=2, sticky="nsew", padx=(0, 0))
            self.after_idle(self._sync_account_list_widths)
            return

        self.account_split.grid_columnconfigure(0, weight=1, uniform="savings_account_lists")
        self.account_split.grid_columnconfigure(1, weight=1, uniform="savings_account_lists")
        self.extract_only_header.grid_configure(columnspan=1, padx=(0, 6))
        self.extract_only_frame.grid(row=1, column=0, columnspan=1, sticky="nsew", padx=(0, 6))
        self.inject_header_bar.grid(row=0, column=1, sticky="ew", padx=(6, 0), pady=(0, 4))
        self.inject_frame.grid(row=1, column=1, sticky="nsew", padx=(6, 0))
        self.after_idle(self._sync_account_list_widths)

    def _show_accounts_card(self) -> None:
        self.results_card.pack_forget()
        self.accounts_card.pack(side="top", fill="both", expand=True, padx=25, pady=(0, 15), before=self.actions_frame)

    def _show_results_card(self) -> None:
        self.accounts_card.pack_forget()
        self.results_card.pack(side="top", fill="both", expand=True, padx=25, pady=(0, 15), before=self.actions_frame)

    def _selected_country_id(self) -> str | None:
        return self._country_lookup.get(self.country_var.get())

    def _refresh_db_options(self) -> None:
        country = self._selected_country_id()
        self._refresh_open_folder_button()
        labels: list[str] = []
        dest_labels: list[str] = []
        self._db_lookup = {}
        self._dest_db_lookup = {}
        if country:
            for env in _ENV_DISPLAY_ORDER:
                for db in dbs.databases_for(country, env=env):
                    tag = _ENV_TAG.get(env, env.upper())
                    label = f"{tag}  ·  {db['label']}  ·  {db['id']}"
                    labels.append(label)
                    self._db_lookup[label] = db
            for env in _DEST_ENV_DISPLAY_ORDER:
                for db in dbs.databases_for(country, env=env):
                    tag = _ENV_TAG.get(env, env.upper())
                    label = f"{tag}  ·  {db['label']}  ·  {db['id']}"
                    dest_labels.append(label)
                    self._dest_db_lookup[label] = db

        self.db_menu.configure(values=labels or ["—"])
        self.db_var.set(labels[0] if labels else "—")
        self.dest_db_menu.configure(values=dest_labels or ["-"])
        self.dest_db_var.set(dest_labels[0] if dest_labels else "-")

    def _selected_db(self) -> dict | None:
        return self._db_lookup.get(self.db_var.get())

    def _selected_dest_db(self) -> dict | None:
        return self._dest_db_lookup.get(self.dest_db_var.get())

    def _refresh_open_folder_button(self) -> None:
        if not hasattr(self, "open_folder_btn"):
            return
        country_label = self.country_var.get()
        if not country_label or country_label == "—":
            country_label = t("spools_savings.country")
        self.open_folder_btn.configure(text=t("spools_savings.open_country_folder", country=country_label))

    def _on_browse_existing_spool(self) -> None:
        country = self._selected_country_id()
        initial_dir = SPOOLS_SAVINGS_OUT_DIR / (country.title() if country else "")
        path = filedialog.askopenfilename(
            parent=self,
            title=t("spools_savings.select_spool_file"),
            initialdir=str(initial_dir if initial_dir.exists() else SPOOLS_SAVINGS_OUT_DIR),
            filetypes=[("Savings spool files", "*.inc *.INC *.sql *.SQL"), ("All files", "*.*")],
        )
        if not path:
            return
        self._existing_spool_path = Path(path)
        self.existing_spool_var.set(str(self._existing_spool_path))

    @staticmethod
    def _account_from_spool_path(spool_path: Path) -> str:
        stem = spool_path.stem
        prefix = "IC_account_data_"
        if stem.upper().startswith(prefix.upper()):
            return stem[len(prefix):] or stem
        return stem

    def _on_add_account(self) -> None:
        raw = self.account_entry.get().strip()
        if not raw:
            return
        if not is_valid_savings_account(raw):
            messagebox.showerror(
                t("common.error"),
                t("spools_savings.invalid_account", acc=raw),
                parent=self,
            )
            return
        if raw in self._pending_accounts:
            messagebox.showinfo(t("common.info"), t("spools_savings.duplicate_account"), parent=self)
            return
        self._pending_accounts.append(raw)
        self._inject_flags[raw] = True
        self.account_entry.delete(0, "end")
        self.account_entry.focus_set()
        self._render_pending_accounts()

    def _open_bulk_accounts_dialog(self) -> None:
        dialog = ctk.CTkToplevel(self)
        dialog.title(t("spools_savings.bulk_title"))
        width, height = _BULK_DIALOG_SIZE
        self._center_dialog(dialog, width, height)
        dialog.minsize(500, 360)
        dialog.transient(self.winfo_toplevel())
        dialog.grab_set()
        dialog.grid_columnconfigure(0, weight=1)
        dialog.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(
            dialog,
            text=t("spools_savings.bulk_hint"),
            anchor="w",
            justify="left",
            wraplength=520,
        ).grid(row=0, column=0, padx=18, pady=(18, 8), sticky="ew")

        text_box = ctk.CTkTextbox(dialog, font=ctk.CTkFont(family="Consolas", size=12))
        text_box.grid(row=1, column=0, padx=18, pady=(0, 10), sticky="nsew")
        self._install_textbox_placeholder(text_box, t("spools_savings.bulk_placeholder"))
        text_box.focus_set()

        status_var = ctk.StringVar(value="")
        status_label = ctk.CTkLabel(
            dialog,
            textvariable=status_var,
            anchor="w",
            justify="left",
            wraplength=520,
            text_color=("gray35", "gray70"),
        )
        status_label.grid(row=2, column=0, padx=18, pady=(0, 8), sticky="ew")

        button_row = ctk.CTkFrame(dialog, fg_color="transparent")
        button_row.grid(row=3, column=0, padx=18, pady=(0, 16), sticky="ew")
        button_row.grid_columnconfigure(0, weight=1)

        def submit() -> None:
            stats = self._add_bulk_accounts(text_box.get("1.0", "end"))
            invalid = stats["invalid"]
            if invalid:
                status_label.configure(text_color=("#8A5A00", "#F0C36D"))
                status_var.set(
                    t(
                        "spools_savings.bulk_result_invalid",
                        added=stats["added"],
                        duplicates=stats["duplicates"],
                        invalid=len(invalid),
                        items=self._format_invalid_preview(invalid),
                    )
                )
                return
            if stats["added"]:
                dialog.destroy()
                return
            status_label.configure(text_color=("#8A5A00", "#F0C36D"))
            if stats["duplicates"]:
                status_var.set(t("spools_savings.bulk_result_duplicates", duplicates=stats["duplicates"]))
            else:
                status_var.set(t("spools_savings.bulk_result_empty"))

        ctk.CTkButton(
            button_row,
            text=t("common.cancel"),
            width=120,
            fg_color=("gray70", "gray28"),
            hover_color=("gray60", "gray35"),
            command=dialog.destroy,
        ).grid(row=0, column=1, padx=(0, 8), sticky="e")
        ctk.CTkButton(
            button_row,
            text=t("spools_savings.bulk_add"),
            width=140,
            command=submit,
        ).grid(row=0, column=2, sticky="e")

    def _add_bulk_accounts(self, text: str) -> dict[str, object]:
        valid, invalid = parse_savings_accounts(text)
        added = 0
        duplicates = 0
        for account in valid:
            if account in self._pending_accounts:
                duplicates += 1
                continue
            self._pending_accounts.append(account)
            self._inject_flags[account] = True
            added += 1
        if added:
            self._render_pending_accounts()
        return {"added": added, "duplicates": duplicates, "invalid": invalid}

    @staticmethod
    def _format_invalid_preview(invalid: list[str]) -> str:
        if not invalid:
            return "-"
        preview = ", ".join(invalid[:12])
        if len(invalid) > 12:
            preview += ", ..."
        return preview

    def _center_dialog(self, dialog: ctk.CTkToplevel, width: int, height: int) -> None:
        parent = self.winfo_toplevel()
        parent.update_idletasks()
        px, py = parent.winfo_rootx(), parent.winfo_rooty()
        pw, ph = parent.winfo_width(), parent.winfo_height()
        if pw <= 1 or ph <= 1:
            sw, sh = dialog.winfo_screenwidth(), dialog.winfo_screenheight()
            x, y = (sw - width) // 2, (sh - height) // 2
        else:
            x, y = px + (pw - width) // 2, py + (ph - height) // 2
        dialog.geometry(f"{width}x{height}+{max(0, x)}+{max(0, y)}")

    @staticmethod
    def _install_textbox_placeholder(text_box: ctk.CTkTextbox, text: str) -> None:
        placeholder = ctk.CTkLabel(
            text_box,
            text=text,
            font=ctk.CTkFont(family="Consolas", size=12),
            text_color=("gray58", "gray48"),
            justify="left",
            anchor="nw",
        )

        def refresh(_event=None) -> None:
            has_text = bool(text_box.get("1.0", "end-1c").strip())
            if has_text:
                placeholder.place_forget()
            else:
                placeholder.place(x=10, y=8)

        def focus_textbox(_event=None) -> str:
            text_box.focus_set()
            return "break"

        placeholder.bind("<Button-1>", focus_textbox)
        text_box.bind("<KeyRelease>", refresh)
        text_box.bind("<<Paste>>", lambda _e: text_box.after(1, refresh))
        refresh()

    def _remove_pending(self, account: str) -> None:
        try:
            self._pending_accounts.remove(account)
        except ValueError:
            return
        self._inject_flags.pop(account, None)
        self._render_pending_accounts()

    def _set_inject_flag(self, account: str, value: bool) -> None:
        self._inject_flags[account] = value
        self._render_pending_accounts()

    def _selected_inject_accounts(self) -> list[str]:
        if self._is_extract_only_mode():
            return []
        return [acc for acc in self._pending_accounts if self._inject_flags.get(acc, False)]

    def _render_pending_accounts(self) -> None:
        for frame in (self.extract_only_frame, self.inject_frame):
            setattr(frame, "_oracle_tasks_row_layout_state", None)
        for w in self.extract_only_frame.winfo_children():
            w.destroy()
        for w in self.inject_frame.winfo_children():
            w.destroy()
        inject_accounts = [] if self._is_extract_only_mode() else self._selected_inject_accounts()
        self.pending_header.configure(text=t("spools_savings.accounts_summary", n=len(self._pending_accounts)))
        self.extract_only_header.configure(text=t("spools_savings.extract_only_header", n=len(self._pending_accounts)))
        self.inject_header.configure(text=t("spools_savings.inject_header", n=len(inject_accounts)))
        for acc in self._pending_accounts:
            self._render_extract_row(self.extract_only_frame, acc)
        for acc in inject_accounts:
            self._render_inject_row(self.inject_frame, acc)
        self._refresh_run_button()
        self.after_idle(self._sync_account_list_widths)
        self.after(50, self._sync_account_list_widths)

    def _bind_account_list_resize(self) -> None:
        for frame in (self.extract_only_frame, self.inject_frame):
            canvas = getattr(frame, "_parent_canvas", None)
            parent_frame = getattr(frame, "_parent_frame", None)
            if canvas is not None:
                canvas.bind(
                    "<Configure>",
                    lambda _event, f=frame: self.after_idle(lambda frame=f: self._sync_account_list_width(frame)),
                    add="+",
                )
            if parent_frame is not None:
                parent_frame.bind(
                    "<Configure>",
                    lambda _event, f=frame: self.after_idle(lambda frame=f: self._sync_account_list_width(frame)),
                    add="+",
                )

    def _sync_account_list_widths(self) -> None:
        for frame in (self.extract_only_frame, self.inject_frame):
            self._sync_account_list_width(frame)

    @staticmethod
    def _sync_account_list_width(frame) -> None:
        try:
            canvas = getattr(frame, "_parent_canvas", None)
            window_id = getattr(frame, "_create_window_id", None)
            if canvas is None or window_id is None:
                return
            width = max(120, canvas.winfo_width())
            children = frame.winfo_children()
            layout_state = (width, len(children))
            if getattr(frame, "_oracle_tasks_row_layout_state", None) == layout_state:
                return
            setattr(frame, "_oracle_tasks_row_layout_state", layout_state)
            canvas.itemconfigure(window_id, width=width)
            for child in children:
                if isinstance(child, ctk.CTkFrame):
                    child.configure(height=45)
                    child.pack_configure(fill="x", padx=4, pady=2)
                    child.pack_propagate(False)
        except tk.TclError:
            return

    def _render_extract_row(self, parent, account: str) -> None:
        row = ctk.CTkFrame(parent, fg_color=("gray92", "gray18"), corner_radius=4, height=45)
        row.pack(fill="x", padx=4, pady=2)
        row.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            row,
            text=account,
            anchor="w",
            font=ctk.CTkFont(family="Consolas", size=12),
        ).grid(row=0, column=0, padx=(10, 6), pady=4, sticky="ew")
        button_col = 1
        ctk.CTkButton(
            row,
            text="x",
            width=32,
            height=24,
            fg_color=("#D9534F", "#A8322C"),
            hover_color=("#C9302C", "#8B1F1A"),
            text_color="white",
            command=lambda a=account: self._remove_pending(a),
        ).grid(row=0, column=button_col, padx=(4, 8), pady=4, sticky="e")
        button_col += 1
        if not self._is_extract_only_mode() and not self._inject_flags.get(account, False):
            ctk.CTkButton(
                row,
                text=t("spools_savings.move_to_inject"),
                width=90,
                height=24,
                command=lambda a=account: self._set_inject_flag(a, True),
            ).grid(row=0, column=button_col, padx=4, pady=4, sticky="e")

    def _render_inject_row(self, parent, account: str) -> None:
        row = ctk.CTkFrame(parent, fg_color=("gray92", "gray18"), corner_radius=4, height=45)
        row.pack(fill="x", padx=4, pady=2)
        row.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            row,
            text=account,
            anchor="w",
            font=ctk.CTkFont(family="Consolas", size=12),
        ).grid(row=0, column=0, padx=(10, 6), pady=4, sticky="ew")
        ctk.CTkButton(
            row,
            text="x",
            width=32,
            height=24,
            fg_color=("#D9534F", "#A8322C"),
            hover_color=("#C9302C", "#8B1F1A"),
            text_color="white",
            command=lambda a=account: self._set_inject_flag(a, False),
        ).grid(row=0, column=1, padx=(4, 8), pady=4, sticky="e")

    def _refresh_run_button(self) -> None:
        if not hasattr(self, "run_btn") or self._running:
            return
        if self._is_apply_existing_mode():
            key = "spools_savings.run_apply_existing"
        elif self._is_extract_only_mode():
            key = "spools_savings.run_extract_only"
        else:
            key = "spools_savings.run_extract_apply" if self._selected_inject_accounts() else "spools_savings.run_extract_only"
        self.run_btn.configure(text=t(key))

    def _set_run_button_running(self, running: bool) -> None:
        if running:
            if hasattr(self, "serial_accounts_check"):
                self.serial_accounts_check.configure(state="disabled")
            self.run_btn.configure(
                text=t("spools_savings.cancel"),
                command=self._on_cancel,
                state="normal",
                fg_color=_CANCEL_BUTTON_FG,
                hover_color=_CANCEL_BUTTON_HOVER,
                text_color="white",
            )
            return
        if hasattr(self, "serial_accounts_check"):
            self.serial_accounts_check.configure(state="normal")
        self.run_btn.configure(
            command=self._on_run,
            state="normal",
            fg_color=_PRIMARY_BUTTON_FG,
            hover_color=_PRIMARY_BUTTON_HOVER,
            text_color="white",
        )
        self._refresh_run_button()

    def _on_cancel(self) -> None:
        if not self._running:
            return
        if self._cancel_event is not None:
            self._cancel_event.set()
        self.run_btn.configure(text=t("spools_savings.cancelling"), state="disabled")
        self.summary_label.configure(text=t("spools_savings.cancel_requested"))

    def _on_run(self) -> None:
        if self._running:
            return
        country = self._selected_country_id()
        if self._is_apply_existing_mode():
            self._on_run_apply_existing(country)
            return
        if not country:
            messagebox.showerror(t("common.error"), t("spools_savings.invalid_country"), parent=self)
            return
        if not has_savings_template():
            messagebox.showerror(t("common.error"), t("spools_savings.no_template"), parent=self)
            return
        db = self._selected_db()
        if not db:
            messagebox.showerror(t("common.error"), t("spools_savings.invalid_db"), parent=self)
            return
        accounts = list(self._pending_accounts)
        if not accounts:
            messagebox.showerror(t("common.error"), t("spools_savings.no_pending"), parent=self)
            return
        inject_accounts = [] if self._is_extract_only_mode() else self._selected_inject_accounts()
        dest_db = self._selected_dest_db() if inject_accounts else None
        if inject_accounts:
            if not dest_db:
                messagebox.showerror(t("common.error"), t("spools_savings.invalid_destination_db"), parent=self)
                return
            if db["id"].upper() == dest_db["id"].upper():
                messagebox.showerror(t("common.error"), t("spools_savings.same_source_destination"), parent=self)
                return

        extract_archive_dir: Path | None = None
        if not inject_accounts:
            default_dir = SPOOLS_SAVINGS_OUT_DIR / (country.title() if country else "")
            selected_dir = filedialog.askdirectory(
                parent=self,
                title=t("spools_savings.select_extract_folder"),
                initialdir=str(default_dir if default_dir.exists() else SPOOLS_SAVINGS_OUT_DIR),
            )
            if not selected_dir:
                return
            extract_archive_dir = Path(selected_dir)
        sqlcl_path = (self.app.config.get("sqlcl_path") or "").strip()
        if not sqlcl_path or not os.path.exists(sqlcl_path):
            messagebox.showerror(t("common.error"), t("spools_savings.no_sqlcl"), parent=self)
            return

        source_cred = self._credential_for_db(country, db["id"])
        if not source_cred:
            messagebox.showerror(t("common.error"), t("spools_savings.no_creds", db=db["id"]), parent=self)
            return

        dest_connection = ""
        if inject_accounts and dest_db:
            dest_cred = self._credential_for_db(country, dest_db["id"])
            if not dest_cred:
                messagebox.showerror(t("common.error"), t("spools_savings.no_creds", db=dest_db["id"]), parent=self)
                return
            dest_connection = self._connection_for_credential(dest_cred, dest_db["id"])
            listed = ", ".join(inject_accounts[:12])
            if len(inject_accounts) > 12:
                listed += ", ..."
            ok = messagebox.askyesno(
                t("spools_savings.confirm_title"),
                t(
                    "spools_savings.confirm_inject",
                    n=len(inject_accounts),
                    db=dest_db["id"],
                    accounts=listed,
                ),
                icon="warning",
                default=messagebox.NO,
                parent=self,
            )
            if not ok:
                return

        source_connection = self._connection_for_credential(source_cred, db["id"])
        self._show_results_card()
        self._prepare_results(accounts)

        self._running = True
        self._completed_steps = 0
        self._run_id += 1
        run_id = self._run_id
        self._active_summary_phase = "extract"
        self._cancel_event = threading.Event()
        cancel_event = self._cancel_event
        self.result_detail_label.configure(text="")
        self._set_run_button_running(True)
        self.summary_label.configure(text=t("spools_savings.extracting", done=0, total=len(accounts)))
        extract_max_workers = MAX_PARALLEL_SAVINGS_ACCOUNTS
        inject_max_workers = 1 if self.serial_accounts_var.get() else MAX_PARALLEL_SAVINGS_ACCOUNTS
        verify_after_apply = bool(self.app.config.get("verify_savings_apply", False))

        threading.Thread(
            target=self._do_run,
            args=(
                run_id,
                country,
                accounts,
                inject_accounts,
                source_connection,
                dest_connection,
                sqlcl_path,
                cancel_event,
                extract_max_workers,
                inject_max_workers,
                verify_after_apply,
                extract_archive_dir,
            ),
            daemon=True,
        ).start()

    def _on_run_apply_existing(self, country: str | None) -> None:
        if not country:
            messagebox.showerror(t("common.error"), t("spools_savings.invalid_country"), parent=self)
            return
        dest_db = self._selected_dest_db()
        if not dest_db:
            messagebox.showerror(t("common.error"), t("spools_savings.invalid_destination_db"), parent=self)
            return
        spool_path = self._existing_spool_path
        if spool_path is None and self.existing_spool_var.get().strip():
            spool_path = Path(self.existing_spool_var.get().strip())
        if spool_path is None:
            messagebox.showerror(t("common.error"), t("spools_savings.no_existing_spool"), parent=self)
            return
        if spool_path.suffix.lower() not in {".inc", ".sql"}:
            messagebox.showerror(t("common.error"), t("spools_savings.invalid_spool_file"), parent=self)
            return
        if not spool_path.is_file():
            messagebox.showerror(
                t("common.error"),
                t("spools_savings.spool_file_missing", file=spool_path.name),
                parent=self,
            )
            return
        sqlcl_path = (self.app.config.get("sqlcl_path") or "").strip()
        if not sqlcl_path or not os.path.exists(sqlcl_path):
            messagebox.showerror(t("common.error"), t("spools_savings.no_sqlcl"), parent=self)
            return
        dest_cred = self._credential_for_db(country, dest_db["id"])
        if not dest_cred:
            messagebox.showerror(t("common.error"), t("spools_savings.no_creds", db=dest_db["id"]), parent=self)
            return
        dest_connection = self._connection_for_credential(dest_cred, dest_db["id"])
        account = self._account_from_spool_path(spool_path)
        ok = messagebox.askyesno(
            t("spools_savings.confirm_title"),
            t(
                "spools_savings.confirm_apply_existing",
                file=spool_path.name,
                db=dest_db["id"],
            ),
            icon="warning",
            default=messagebox.NO,
            parent=self,
        )
        if not ok:
            return

        self._show_results_card()
        self._prepare_results([account])
        self._running = True
        self._completed_steps = 0
        self._run_id += 1
        run_id = self._run_id
        self._active_summary_phase = "apply_existing"
        self._cancel_event = threading.Event()
        cancel_event = self._cancel_event
        self.result_detail_label.configure(text="")
        self._set_run_button_running(True)
        self.summary_label.configure(text=t("spools_savings.injecting", done=0, total=1))
        verify_after_apply = bool(self.app.config.get("verify_savings_apply", False))

        threading.Thread(
            target=self._do_apply_existing,
            args=(run_id, account, spool_path, dest_connection, sqlcl_path, cancel_event, verify_after_apply),
            daemon=True,
        ).start()

    def _prepare_results(self, accounts: list[str]) -> None:
        for w in self.results_frame.winfo_children():
            w.destroy()
        self._status_rows = {}
        for acc in accounts:
            row = AccountStatusRow(self.results_frame, account=acc)
            row.pack(fill="x", padx=4, pady=2)
            self._status_rows[acc] = row

    def _credential_for_db(self, country: str, db_id: str) -> dict | None:
        cred = self.app.config.get_credential(country, db_id)
        if cred is None:
            by_login = self.app.config.all_credentials().get(country, {}).get(db_id.upper(), {})
            cred = next(iter(by_login.values()), None)
        return cred

    @staticmethod
    def _connection_for_credential(cred: dict, tns: str) -> str:
        password = decrypt_password(cred.get("password_enc", ""))
        return to_sqlcl_arg(
            cred.get("user", ""),
            cred.get("schema") or None,
            password,
            tns,
        )

    def _post_ui(self, callback) -> bool:
        try:
            if not self.winfo_exists():
                return False
            self.app.root.after(0, callback)
            return True
        except (RuntimeError, tk.TclError):
            return False

    def _do_run(
        self,
        run_id: int,
        country: str,
        accounts: list[str],
        inject_accounts: list[str],
        source_connection: str,
        dest_connection: str,
        sqlcl_path: str,
        cancel_event: threading.Event,
        extract_max_workers: int,
        inject_max_workers: int,
        verify_after_apply: bool,
        extract_archive_dir: Path | None,
    ) -> None:
        engine = SpoolSavingsEngine(SqlclRunner(sqlcl_path))
        total = len(accounts)
        workers = worker_count_for(total, extract_max_workers)
        log.info("Starting Savings extraction batch: accounts=%s workers=%s", total, workers)
        inject_set = set(inject_accounts)
        temp_dir = tempfile.TemporaryDirectory(prefix="oracle_tasks_savings_")
        working_dir = Path(temp_dir.name)

        def on_extract_status(account: str, status: SpoolSavingsStatus, msg: str) -> None:
            display = msg
            if status == SpoolSavingsStatus.RUNNING and not display:
                display = t("spools_savings.status_extracting")
            elif status == SpoolSavingsStatus.OK:
                display = (
                    t("spools_savings.status_ready_to_inject")
                    if account in inject_set else t("spools_savings.status_spool_saved")
                )
            elif status == SpoolSavingsStatus.CANCELLED:
                display = t("spools_savings.status_cancelled")
            self._post_ui(
                lambda a=account, s=status, m=display, T=total, r=run_id:
                    self._apply_status(a, s, m, T, "spools_savings.extracting", r, "extract")
            )

        try:
            results = engine.extract_many(
                country,
                accounts,
                source_connection,
                on_extract_status,
                max_workers=extract_max_workers,
                cancel_event=cancel_event,
                output_dir=working_dir,
            )
            extract_ok = sum(1 for result in results if result.status == SpoolSavingsStatus.OK)
            extract_err = total - extract_ok
            for result in results:
                log.info(
                    "Savings extract result: %s status=%s branch=%s out=%s err=%s",
                    result.account, result.status.value, result.branch, result.output_path, result.error,
                )

            by_account = {result.account: result for result in results}
            apply_items = [
                (acc, by_account[acc].output_path)
                for acc in inject_accounts
                if by_account.get(acc)
                and by_account[acc].status == SpoolSavingsStatus.OK
                and by_account[acc].output_path is not None
            ]
            if cancel_event.is_set() or not apply_items:
                details = self._classify_extract_apply(accounts, results, [])
                if not cancel_event.is_set():
                    try:
                        if extract_archive_dir is not None:
                            archive_path = self._create_extract_archive(country, results, extract_archive_dir)
                            if archive_path is not None:
                                details["archive"] = [str(archive_path)]
                        else:
                            self._persist_generated_spools(country, results)
                    except OSError as exc:
                        log.exception("Could not persist Savings extract output")
                        details["save_error"] = [str(exc)]
                self._post_ui(
                    lambda r=run_id, d=details, c=cancel_event.is_set(): self._finish(
                        extract_ok, extract_err, 0, 0, total, 0, r, d, c,
                    )
                )
                return

            self._post_ui(
                lambda total_=len(apply_items), r=run_id, e=cancel_event: self._start_inject_stage(total_, r, e)
            )
            apply_workers = worker_count_for(len(apply_items), inject_max_workers)
            log.info("Starting Savings inject batch: accounts=%s workers=%s", len(apply_items), apply_workers)

            def on_apply_status(account: str, status: SpoolSavingsStatus, msg: str) -> None:
                display = msg
                if status == SpoolSavingsStatus.RUNNING:
                    display = t("spools_savings.status_injecting")
                elif status == SpoolSavingsStatus.OK:
                    display = msg or t("spools_savings.status_injected")
                elif status == SpoolSavingsStatus.VERIFIED:
                    display = msg or t("spools_savings.status_verified")
                elif status == SpoolSavingsStatus.WARNING:
                    display = msg or t("spools_savings.status_injected_warning")
                elif status == SpoolSavingsStatus.CANCELLED:
                    display = t("spools_savings.status_cancelled")
                self._post_ui(
                    lambda a=account, s=status, m=display, T=len(apply_items), r=run_id:
                        self._apply_status(a, s, m, T, "spools_savings.injecting", r, "inject")
                )

            apply_results = engine.apply_many(
                apply_items,
                dest_connection,
                on_apply_status,
                max_workers=inject_max_workers,
                cancel_event=cancel_event,
                verify_after_apply=verify_after_apply,
            )
            inject_ok = sum(1 for result in apply_results if result.status in _APPLY_SUCCESS_STATUSES)
            inject_err = len(apply_items) - inject_ok
            details = self._classify_extract_apply(accounts, results, apply_results)
            if not cancel_event.is_set():
                try:
                    self._persist_apply_outputs(country, apply_results)
                    self._persist_generated_spools(
                        country,
                        [result for result in results if result.account not in inject_set],
                    )
                except OSError as exc:
                    log.exception("Could not persist Savings apply output")
                    details["save_error"] = [str(exc)]
            self._post_ui(
                lambda r=run_id, d=details, c=cancel_event.is_set(): self._finish(
                    extract_ok, extract_err, inject_ok, inject_err, total, len(apply_items), r, d, c,
                )
            )
        finally:
            temp_dir.cleanup()

    def _do_apply_existing(
        self,
        run_id: int,
        account: str,
        spool_path: Path,
        dest_connection: str,
        sqlcl_path: str,
        cancel_event: threading.Event,
        verify_after_apply: bool,
    ) -> None:
        engine = SpoolSavingsEngine(SqlclRunner(sqlcl_path))
        log.info("Starting existing Savings apply: account=%s spool=%s", account, spool_path)

        def on_apply_status(account_: str, status: SpoolSavingsStatus, msg: str) -> None:
            display = msg
            if status == SpoolSavingsStatus.RUNNING:
                display = t("spools_savings.status_injecting")
            elif status == SpoolSavingsStatus.OK:
                display = msg or t("spools_savings.status_injected")
            elif status == SpoolSavingsStatus.VERIFIED:
                display = msg or t("spools_savings.status_verified")
            elif status == SpoolSavingsStatus.WARNING:
                display = msg or t("spools_savings.status_injected_warning")
            elif status == SpoolSavingsStatus.CANCELLED:
                display = t("spools_savings.status_cancelled")
            self._post_ui(
                lambda a=account_, s=status, m=display, r=run_id:
                    self._apply_status(a, s, m, 1, "spools_savings.injecting", r, "apply_existing")
            )

        result = engine.apply_one(
            account,
            dest_connection,
            spool_path,
            on_apply_status,
            cancel_event,
            verify_after_apply=verify_after_apply,
        )
        ok = 1 if result.status in _APPLY_SUCCESS_STATUSES else 0
        err = 0 if result.status in _APPLY_SUCCESS_STATUSES else 1
        self._post_ui(
            lambda r=run_id, c=cancel_event.is_set(): self._finish_apply_existing(ok, err, r, result, c)
        )

    def _persist_generated_spools(self, country: str, results: list[SavingsAccountResult]) -> list[Path]:
        saved: list[Path] = []
        for result in results:
            if result.status != SpoolSavingsStatus.OK or result.output_path is None:
                continue
            if not result.output_path.exists():
                continue
            dest = savings_output_path_for(country, result.account)
            dest.parent.mkdir(parents=True, exist_ok=True)
            if result.output_path != dest:
                shutil.copy2(result.output_path, dest)
            saved.append(dest)
        return saved

    def _persist_apply_outputs(self, country: str, results: list[SavingsAccountResult]) -> None:
        for result in results:
            if result.output_path is None:
                continue
            dest = savings_output_path_for(country, result.account)
            dest.parent.mkdir(parents=True, exist_ok=True)
            if result.status in _APPLY_SUCCESS_STATUSES and result.output_path.exists():
                shutil.copy2(result.output_path, dest)
            log_path = result.output_path.with_name(f"{result.output_path.stem}_apply.log")
            if log_path.exists():
                shutil.copy2(log_path, dest.with_name(f"{dest.stem}_apply.log"))

    def _create_extract_archive(
        self,
        country: str,
        results: list[SavingsAccountResult],
        archive_dir: Path,
    ) -> Path | None:
        files = [
            result.output_path
            for result in results
            if result.status == SpoolSavingsStatus.OK and result.output_path is not None and result.output_path.exists()
        ]
        if not files:
            return None
        archive_dir.mkdir(parents=True, exist_ok=True)
        country_part = (country or "country").title().replace(" ", "_")
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        archive_path = archive_dir / f"Savings_Spools_{country_part}_{stamp}.zip"
        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for path in files:
                zf.write(path, arcname=path.name)
        return archive_path
    @staticmethod
    def _classify_extract_apply(
        accounts: list[str],
        extract_results: list[SavingsAccountResult],
        apply_results: list[SavingsAccountResult],
    ) -> dict[str, list[str]]:
        extracted_ok = {result.account for result in extract_results if result.status == SpoolSavingsStatus.OK}
        injected_ok = {
            result.account
            for result in apply_results
            if result.status in _APPLY_SUCCESS_STATUSES
        }
        return {
            "injected": [acc for acc in accounts if acc in injected_ok],
            "only_extracted": [acc for acc in accounts if acc in extracted_ok and acc not in injected_ok],
            "nothing": [acc for acc in accounts if acc not in extracted_ok],
        }

    @staticmethod
    def _format_accounts(accounts: list[str]) -> str:
        return ", ".join(accounts) if accounts else "-"

    def _show_extract_apply_details(self, details: dict[str, list[str]]) -> None:
        text = t(
            "spools_savings.detail_extract_apply",
            injected=self._format_accounts(details.get("injected", [])),
            only_extracted=self._format_accounts(details.get("only_extracted", [])),
            nothing=self._format_accounts(details.get("nothing", [])),
        )
        archive = details.get("archive", [])
        if archive:
            text += "\n" + t("spools_savings.detail_archive", file=archive[0])
        save_error = details.get("save_error", [])
        if save_error:
            text += "\n" + t("spools_savings.detail_save_error", error=save_error[0])
        self.result_detail_label.configure(text=text)

    def _show_apply_existing_details(self, account: str, ok: bool) -> None:
        self.result_detail_label.configure(
            text=t(
                "spools_savings.detail_apply_existing",
                injected=account if ok else "-",
                nothing="-" if ok else account,
            ),
        )

    def _apply_status(
        self,
        account: str,
        status: SpoolSavingsStatus,
        message: str,
        total: int,
        summary_key: str,
        run_id: int,
        phase: str,
    ) -> None:
        if run_id != self._run_id:
            return
        row = self._status_rows.get(account)
        if row is not None:
            status_val = status.value
            if status_val == "running":
                status_val = "injecting" if "inject" in phase or "apply" in phase else "extracting"
            elif status_val == "ok" and phase == "extract" and account in self._selected_inject_accounts():
                status_val = "ready_to_inject"
            row.set_status(status_val, message)
        if status in _TERMINAL_STATUSES and self._active_summary_phase == phase:
            self._completed_steps += 1
            self.summary_label.configure(text=t(summary_key, done=self._completed_steps, total=total))

    def _start_inject_stage(
        self,
        total: int,
        run_id: int,
        cancel_event: threading.Event | None = None,
    ) -> None:
        if run_id != self._run_id or self._active_summary_phase is None or (
            cancel_event is not None and cancel_event.is_set()
        ):
            return
        self._completed_steps = 0
        self._active_summary_phase = "inject"
        self.summary_label.configure(text=t("spools_savings.injecting", done=0, total=total))

    def _finish(
        self,
        extract_ok: int,
        extract_err: int,
        inject_ok: int,
        inject_err: int,
        extract_total: int,
        inject_total: int,
        run_id: int,
        details: dict[str, list[str]],
        cancelled: bool,
    ) -> None:
        if run_id != self._run_id:
            return
        self._running = False
        self._active_summary_phase = None
        self._cancel_event = None
        self._set_run_button_running(False)
        self._show_extract_apply_details(details)
        if cancelled:
            self.summary_label.configure(
                text=t(
                    "spools_savings.summary_cancelled",
                    extract_ok=extract_ok,
                    extract_total=extract_total,
                    inject_ok=inject_ok,
                    inject_total=inject_total,
                ),
            )
        elif inject_total:
            self.summary_label.configure(
                text=t(
                    "spools_savings.summary_extract_inject",
                    extract_ok=extract_ok,
                    extract_total=extract_total,
                    inject_ok=inject_ok,
                    inject_total=inject_total,
                    err=extract_err + inject_err,
                ),
            )
        elif extract_err == 0:
            self.summary_label.configure(text=t("spools_savings.summary_ok", ok=extract_ok, total=extract_total))
        else:
            self.summary_label.configure(
                text=t("spools_savings.summary_mixed", ok=extract_ok, err=extract_err, total=extract_total),
            )

    def _finish_apply_existing(
        self,
        ok: int,
        err: int,
        run_id: int,
        result: SavingsAccountResult,
        cancelled: bool,
    ) -> None:
        if run_id != self._run_id:
            return
        self._running = False
        self._active_summary_phase = None
        self._cancel_event = None
        self._set_run_button_running(False)
        self._show_apply_existing_details(result.account, result.status in _APPLY_SUCCESS_STATUSES)
        if cancelled:
            self.summary_label.configure(text=t("spools_savings.summary_apply_existing_cancelled"))
            return
        self.summary_label.configure(
            text=t("spools_savings.summary_apply_existing", ok=ok, total=ok + err, err=err),
        )

    def _on_open_folder(self) -> None:
        country = self._selected_country_id()
        folder = SPOOLS_SAVINGS_OUT_DIR / (country.title() if country else "")
        folder.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(str(folder))
        except OSError as e:
            log.warning("Could not open folder %s: %s", folder, e)
            subprocess.Popen(["explorer", str(folder)])
