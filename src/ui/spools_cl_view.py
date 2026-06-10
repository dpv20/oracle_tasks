"""Spools CL view — extract/apply CL accounts and apply existing CL spool files.

Layout:
  [ Back ]  Spools / CL Accounts
  Country dropdown
  Source DB dropdown (any env of the selected country)
  Account number  [ entry ] [ + Add ]
  Accounts to extract (n)
    · 209991341468  [×]
    · ...
  [ Extract CL spools ]
  ─── results ───
  status rows (one per account, with spinner / OK / error)
  [ Open CL spools folder ]

Threading: extraction runs on a daemon thread; per-account status callbacks
are marshalled back to the Tk thread via `app.root.after(0, ...)`.
"""
from __future__ import annotations

import logging
import os
import subprocess
import tkinter as tk
import threading
from pathlib import Path

import customtkinter as ctk
from tkinter import filedialog, messagebox

from i18n import t
from settings.config import decrypt_password
from settings.credentials import to_sqlcl_arg
from spools_cl_accounts import databases as dbs
from spools_cl_accounts.spool_cl_engine import (
    MAX_PARALLEL_ACCOUNTS, CLAccountResult, SpoolCLEngine, SpoolCLStatus,
    SPOOL_KIND_CMR, SPOOL_KIND_CONSUMER_LENDING,
    cl_output_folder_for,
    has_cl_template, is_valid_account, is_valid_branch, parse_account_branches,
    parse_accounts, worker_count_for,
)
from spools_cl_accounts.sqlcl import SqlclRunner

from .widgets import AccountStatusRow, CardFrame, IconButton, SectionLabel

log = logging.getLogger(__name__)

# Countries supported in extract mode need a non-interactive *2.sql script.
# Apply-existing can still target countries with a destination DB because it
# does not render a country template.
_EXTRACT_COUNTRIES = [
    (c, c.title()) for c in dbs.countries() if has_cl_template(c)
]
MODE_EXTRACT = "extract"
MODE_EXTRACT_ONLY = "extract_only"
MODE_APPLY_EXISTING = "apply_existing"

# Order in which envs appear in the Source DB dropdown.
_ENV_DISPLAY_ORDER = ("prod", "bup_prod", "qa", "bup_qa", "dev")
_DEST_ENV_DISPLAY_ORDER = ("qa", "bup_qa", "dev")
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
_TERMINAL_STATUSES = {SpoolCLStatus.OK, SpoolCLStatus.ERROR, SpoolCLStatus.CANCELLED}
_BULK_DIALOG_SIZE = (560, 430)


class SpoolsCLView(ctk.CTkFrame):
    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self._status_rows: dict[str, AccountStatusRow] = {}
        self._pending_accounts: list[str] = []
        self._inject_flags: dict[str, bool] = {}
        self._account_branches: dict[str, str] = {}
        self._completed_steps = 0
        self._run_id = 0
        self._active_summary_phase: str | None = None
        self._cancel_event: threading.Event | None = None
        self._running = False
        self._db_lookup: dict[str, dict] = {}
        self._dest_db_lookup: dict[str, dict] = {}
        self._country_lookup: dict[str, str] = {}
        self._existing_spool_paths: list[Path] = []
        self._last_existing_spool_dir: Path | None = None

        # ── header ──
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(side="top", fill="x", padx=25, pady=(25, 15))
        ctk.CTkLabel(
            header, text=t("spools_cl.title"),
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

        ctk.CTkLabel(form, text=t("spools_cl.mode"), anchor="w", width=160).grid(
            row=0, column=0, padx=4, pady=4, sticky="w",
        )
        self.mode_segment = ctk.CTkSegmentedButton(
            form,
            values=[
                t("spools_cl.mode.extract"),
                t("spools_cl.mode.extract_only"),
                t("spools_cl.mode.apply_existing"),
            ],
            command=lambda _v: self._on_mode_change(),
        )
        self.mode_segment.grid(row=0, column=1, padx=4, pady=4, sticky="ew")
        self.mode_segment.set(t("spools_cl.mode.extract"))

        ctk.CTkLabel(form, text=t("spools_cl.country"), anchor="w", width=160).grid(
            row=1, column=0, padx=4, pady=4, sticky="w",
        )
        self._country_lookup = {label: cid for cid, label in _EXTRACT_COUNTRIES}
        self.country_var = ctk.StringVar(value=_EXTRACT_COUNTRIES[0][1] if _EXTRACT_COUNTRIES else "")
        self.country_menu = ctk.CTkOptionMenu(
            form,
            values=[label for _, label in _EXTRACT_COUNTRIES] or ["—"],
            variable=self.country_var,
            command=lambda _v: self._on_country_change(),
        )
        self.country_menu.grid(row=1, column=1, padx=4, pady=4, sticky="ew")

        self.spool_type_label = ctk.CTkLabel(
            form, text=t("spools_cl.spool_type"), anchor="w", width=160,
        )
        self.spool_type_label.grid(row=2, column=0, padx=4, pady=4, sticky="w")
        self.spool_type_segment = ctk.CTkSegmentedButton(
            form,
            values=[
                t("spools_cl.spool_type.consumer_lending"),
                t("spools_cl.spool_type.cmr"),
            ],
            command=lambda _v: self._on_spool_type_change(),
        )
        self.spool_type_segment.grid(row=2, column=1, padx=4, pady=4, sticky="ew")
        self.spool_type_segment.set(t("spools_cl.spool_type.consumer_lending"))

        self.source_label = ctk.CTkLabel(form, text=t("spools_cl.source_db"), anchor="w", width=160)
        self.source_label.grid(
            row=3, column=0, padx=4, pady=4, sticky="w",
        )
        self.db_var = ctk.StringVar(value="")
        self.db_menu = ctk.CTkOptionMenu(form, values=["—"], variable=self.db_var)
        self.db_menu.grid(row=3, column=1, padx=4, pady=4, sticky="ew")

        self.dest_label = ctk.CTkLabel(form, text=t("spools_cl.destination_db"), anchor="w", width=160)
        self.dest_label.grid(
            row=4, column=0, padx=4, pady=4, sticky="w",
        )
        self.dest_db_var = ctk.StringVar(value="")
        self.dest_db_menu = ctk.CTkOptionMenu(form, values=["-"], variable=self.dest_db_var)
        self.dest_db_menu.grid(row=4, column=1, padx=4, pady=4, sticky="ew")

        self.existing_spool_label = ctk.CTkLabel(
            form, text=t("spools_cl.existing_spool"), anchor="w", width=160,
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
            text=t("spools_cl.browse_spool"),
            width=110,
            command=self._on_browse_existing_spool,
        ).pack(side="left", padx=(8, 0))
        self.existing_spool_label.grid(row=5, column=0, padx=4, pady=4, sticky="w")
        self.existing_spool_frame.grid(row=5, column=1, padx=4, pady=4, sticky="ew")

        # ── accounts card ──
        self.accounts_card = CardFrame(self)
        self.accounts_card.pack(side="top", fill="both", expand=True, padx=25, pady=(0, 15))

        # Inner padding for accounts panel
        accounts_inner = ctk.CTkFrame(self.accounts_card, fg_color="transparent")
        accounts_inner.pack(fill="both", expand=True, padx=20, pady=20)

        # ── account input row ──
        self.account_row = ctk.CTkFrame(accounts_inner, fg_color="transparent")
        self.account_row.pack(fill="x", padx=4, pady=(0, 4))
        self.account_label = ctk.CTkLabel(
            self.account_row, text=t("spools_cl.account_number"), anchor="w", width=140,
        )
        self.account_label.pack(side="left", padx=4)
        self.account_entry = ctk.CTkEntry(
            self.account_row, font=ctk.CTkFont(family="Consolas", size=12),
            placeholder_text="e.g. 209991341468",
        )
        self.account_entry.pack(side="left", padx=4, fill="x", expand=True)
        self.account_entry.bind("<Return>", lambda _e: self._on_add_account())
        self.branch_label = ctk.CTkLabel(
            self.account_row, text=t("spools_cl.branch"), anchor="w", width=58,
        )
        self.branch_entry = ctk.CTkEntry(
            self.account_row,
            width=90,
            font=ctk.CTkFont(family="Consolas", size=12),
            placeholder_text="U01",
        )
        self.branch_entry.bind("<Return>", lambda _e: self._on_add_account())
        IconButton(
            self.account_row, text=t("spools_cl.add_account"), width=90,
            command=self._on_add_account,
        ).pack(side="left", padx=4)
        IconButton(
            self.account_row, text=t("spools_cl.add_many_accounts"), width=120,
            command=self._open_bulk_accounts_dialog,
        ).pack(side="left", padx=4)

        # ── pending accounts list ──
        self.pending_header = SectionLabel(accounts_inner, text=t("spools_cl.accounts_summary", n=0))
        self.pending_header.pack(anchor="w", padx=6, pady=(10, 4))

        self.account_split = ctk.CTkFrame(accounts_inner, fg_color="transparent")
        self.account_split.pack(fill="both", expand=True, padx=4, pady=(0, 4))
        self.account_split.grid_columnconfigure(0, weight=1, uniform="account_lists")
        self.account_split.grid_columnconfigure(1, weight=1, uniform="account_lists")
        self.account_split.grid_rowconfigure(1, weight=1)

        self.extract_only_header = SectionLabel(self.account_split, text=t("spools_cl.extract_only_header", n=0))
        self.extract_only_header.grid(row=0, column=0, sticky="ew", padx=(0, 6), pady=(0, 4))
        self.inject_header = SectionLabel(self.account_split, text=t("spools_cl.inject_header", n=0))
        self.inject_header.grid(row=0, column=1, sticky="ew", padx=(6, 0), pady=(0, 4))

        self.extract_only_frame = ctk.CTkScrollableFrame(self.account_split, height=180)
        self.extract_only_frame.grid(row=1, column=0, sticky="nsew", padx=(0, 6))
        self.inject_frame = ctk.CTkScrollableFrame(self.account_split, height=180)
        self.inject_frame.grid(row=1, column=1, sticky="nsew", padx=(6, 0))
        self._render_pending_accounts()

        # ── existing spools selection card ──
        self.existing_spools_card = CardFrame(self)
        existing_spools_inner = ctk.CTkFrame(self.existing_spools_card, fg_color="transparent")
        existing_spools_inner.pack(fill="both", expand=True, padx=20, pady=20)
        self.existing_spools_header = SectionLabel(
            existing_spools_inner,
            text=t("spools_cl.selected_existing_spools", n=0),
        )
        self.existing_spools_header.pack(anchor="w", padx=6, pady=(0, 8))
        self.existing_spools_frame = ctk.CTkScrollableFrame(existing_spools_inner, height=220)
        self.existing_spools_frame.pack(fill="both", expand=True, padx=4, pady=(0, 4))
        self._render_existing_spools()

        # ── actions ──
        self.actions_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.actions_frame.pack(fill="x", padx=25, pady=(0, 4))
        self.run_btn = IconButton(
            self.actions_frame, text=t("spools_cl.run_extract_only"), width=220,
            command=self._on_run,
        )
        self.run_btn.pack(side="left")
        self.open_folder_btn = ctk.CTkButton(
            self.actions_frame, text=t("spools_cl.open_folder"), width=220,
            command=self._on_open_folder,
        )
        self.open_folder_btn.pack(side="left", padx=(10, 0))
        self.summary_label = ctk.CTkLabel(
            self.actions_frame, text="", anchor="e",
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

    # ── DB dropdown ──
    def _current_mode(self) -> str:
        mode = self.mode_segment.get()
        if mode == t("spools_cl.mode.apply_existing"):
            return MODE_APPLY_EXISTING
        if mode == t("spools_cl.mode.extract_only"):
            return MODE_EXTRACT_ONLY
        return MODE_EXTRACT

    def _is_apply_existing_mode(self) -> bool:
        return self._current_mode() == MODE_APPLY_EXISTING

    def _is_extract_only_mode(self) -> bool:
        return self._current_mode() == MODE_EXTRACT_ONLY

    def _is_cmr_selected(self) -> bool:
        return (
            self._selected_country_id() == "chile"
            and self.spool_type_segment.get() == t("spools_cl.spool_type.cmr")
        )

    def _is_cmr_mode(self) -> bool:
        return not self._is_apply_existing_mode() and self._is_cmr_selected()

    def _current_spool_kind(self) -> str:
        return SPOOL_KIND_CMR if self._is_cmr_selected() else SPOOL_KIND_CONSUMER_LENDING

    def _country_options(self) -> list[tuple[str, str]]:
        return _APPLY_EXISTING_COUNTRIES if self._is_apply_existing_mode() else _EXTRACT_COUNTRIES

    def _refresh_country_options(self, previous_country: str | None = None) -> None:
        options = self._country_options()
        labels = [label for _, label in options] or ["—"]
        self._country_lookup = {label: cid for cid, label in options}
        self.country_menu.configure(values=labels)
        selected = next((label for cid, label in options if cid == previous_country), None)
        self.country_var.set(selected or labels[0])

    def _on_mode_change(self) -> None:
        previous_country = self._selected_country_id()
        self._clear_existing_spools()
        self._refresh_country_options(previous_country)
        self._apply_mode_visibility()
        self._refresh_db_options()
        self._refresh_run_button()
        self._render_pending_accounts()

    def _on_spool_type_change(self) -> None:
        self._clear_pending_accounts()
        self._clear_existing_spools()
        self._apply_branch_visibility()
        self._refresh_open_folder_button()
        self._refresh_run_button()

    def _on_country_change(self) -> None:
        self._clear_existing_spools()
        self._refresh_db_options()

    def _clear_pending_accounts(self) -> None:
        if not self._pending_accounts and not self._account_branches:
            return
        self._pending_accounts.clear()
        self._inject_flags.clear()
        self._account_branches.clear()
        self._render_pending_accounts()

    def select_consumer_lending(self) -> None:
        if self._running:
            return
        self.mode_segment.set(t("spools_cl.mode.extract"))
        self.spool_type_segment.set(t("spools_cl.spool_type.consumer_lending"))
        self._clear_pending_accounts()
        self._apply_mode_visibility()
        self._refresh_db_options()
        self._refresh_run_button()

    def select_cmr_chile(self) -> None:
        if self._running:
            return
        self.mode_segment.set(t("spools_cl.mode.extract"))
        self._refresh_country_options("chile")
        self.spool_type_segment.set(t("spools_cl.spool_type.cmr"))
        self._clear_pending_accounts()
        self._apply_mode_visibility()
        self._refresh_db_options()
        self._refresh_run_button()

    def _apply_mode_visibility(self) -> None:
        if self._is_apply_existing_mode():
            self._apply_spool_type_visibility()
            self._apply_branch_visibility()
            self.source_label.grid_remove()
            self.db_menu.grid_remove()
            self.dest_label.grid()
            self.dest_db_menu.grid()
            self.existing_spool_label.grid()
            self.existing_spool_frame.grid()
            self.accounts_card.pack_forget()
            self.results_card.pack_forget()
            if not self.existing_spools_card.winfo_manager():
                self.existing_spools_card.pack(
                    side="top", fill="both", expand=True, padx=25, pady=(0, 15),
                    before=self.actions_frame,
                )
            self._render_existing_spools()
            return

        self._apply_spool_type_visibility()
        self.source_label.grid()
        self.db_menu.grid()
        if self._is_extract_only_mode():
            self.dest_label.grid_remove()
            self.dest_db_menu.grid_remove()
        else:
            self.dest_label.grid()
            self.dest_db_menu.grid()
        self.existing_spool_label.grid_remove()
        self.existing_spool_frame.grid_remove()
        self.existing_spools_card.pack_forget()
        self.results_card.pack_forget()
        self._apply_account_list_visibility()
        if not self.accounts_card.winfo_manager():
            self.accounts_card.pack(side="top", fill="both", expand=True, padx=25, pady=(0, 15), before=self.actions_frame)

    def _apply_account_list_visibility(self) -> None:
        if not hasattr(self, "inject_header"):
            return
        if self._is_extract_only_mode():
            self.inject_header.grid_remove()
            self.inject_frame.grid_remove()
            self.account_split.grid_columnconfigure(0, weight=1, uniform="")
            self.account_split.grid_columnconfigure(1, weight=0, uniform="")
            self.extract_only_header.grid_configure(columnspan=2, padx=(0, 0))
            self.extract_only_frame.grid_configure(columnspan=2, padx=(0, 0))
            return

        self.account_split.grid_columnconfigure(0, weight=1, uniform="account_lists")
        self.account_split.grid_columnconfigure(1, weight=1, uniform="account_lists")
        self.extract_only_header.grid_configure(columnspan=1, padx=(0, 6))
        self.extract_only_frame.grid_configure(columnspan=1, padx=(0, 6))
        self.inject_header.grid(row=0, column=1, sticky="ew", padx=(6, 0), pady=(0, 4))
        self.inject_frame.grid(row=1, column=1, sticky="nsew", padx=(6, 0))

    def _apply_spool_type_visibility(self) -> None:
        if self._selected_country_id() != "chile":
            self.spool_type_segment.set(t("spools_cl.spool_type.consumer_lending"))
            self.spool_type_label.grid_remove()
            self.spool_type_segment.grid_remove()
        else:
            self.spool_type_label.grid()
            self.spool_type_segment.grid()
        self._apply_branch_visibility()
        if not self._is_cmr_mode() and self._account_branches:
            self._account_branches.clear()
            self._render_pending_accounts()

    def _apply_branch_visibility(self) -> None:
        if self._is_cmr_mode():
            if not self.branch_label.winfo_manager():
                self.branch_label.pack(side="left", padx=(8, 4), after=self.account_entry)
            if not self.branch_entry.winfo_manager():
                self.branch_entry.pack(side="left", padx=4, after=self.branch_label)
            return
        self.branch_entry.delete(0, "end")
        self.branch_label.pack_forget()
        self.branch_entry.pack_forget()

    def _selected_country_id(self) -> str | None:
        label = self.country_var.get()
        return self._country_lookup.get(label)

    def _show_accounts_card(self) -> None:
        self.results_card.pack_forget()
        if self._is_apply_existing_mode():
            self.accounts_card.pack_forget()
            self.existing_spools_card.pack(side="top", fill="both", expand=True, padx=25, pady=(0, 15), before=self.actions_frame)
            self._render_existing_spools()
            return
        self.existing_spools_card.pack_forget()
        self.accounts_card.pack(side="top", fill="both", expand=True, padx=25, pady=(0, 15), before=self.actions_frame)

    def _show_results_card(self) -> None:
        self.accounts_card.pack_forget()
        self.existing_spools_card.pack_forget()
        self.results_card.pack(side="top", fill="both", expand=True, padx=25, pady=(0, 15), before=self.actions_frame)

    def _refresh_db_options(self) -> None:
        country = self._selected_country_id()
        self._apply_spool_type_visibility()
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
        if not labels:
            self.db_menu.configure(values=["—"])
            self.db_var.set("—")
            self.dest_db_menu.configure(values=["-"])
            self.dest_db_var.set("-")
            return
        self.db_menu.configure(values=labels)
        self.db_var.set(labels[0])
        if dest_labels:
            self.dest_db_menu.configure(values=dest_labels)
            self.dest_db_var.set(dest_labels[0])
        else:
            self.dest_db_menu.configure(values=["-"])
            self.dest_db_var.set("-")

    def _selected_db(self) -> dict | None:
        return self._db_lookup.get(self.db_var.get())

    def _selected_dest_db(self) -> dict | None:
        return self._dest_db_lookup.get(self.dest_db_var.get())

    def _refresh_open_folder_button(self) -> None:
        if not hasattr(self, "open_folder_btn"):
            return
        if self._current_spool_kind() == SPOOL_KIND_CMR:
            self.open_folder_btn.configure(text=t("spools_cl.open_cmr_folder"))
            return
        country_label = self.country_var.get()
        if not country_label or country_label == "—":
            country_label = t("spools_cl.country")
        self.open_folder_btn.configure(text=t("spools_cl.open_country_folder", country=country_label))

    def _set_existing_spool_paths(self, paths: list[Path]) -> None:
        self._existing_spool_paths = paths
        if not paths:
            self.existing_spool_var.set("")
        elif len(paths) == 1:
            self.existing_spool_var.set(str(paths[0]))
        else:
            self.existing_spool_var.set(t("spools_cl.selected_spools", n=len(paths)))
        if hasattr(self, "existing_spools_frame"):
            self._render_existing_spools()
        self._refresh_run_button()

    def _clear_existing_spools(self) -> None:
        if self._existing_spool_paths or self.existing_spool_var.get():
            self._set_existing_spool_paths([])
        self._last_existing_spool_dir = None

    def _add_existing_spool_paths(self, paths: list[Path]) -> None:
        merged = list(self._existing_spool_paths)
        seen = {str(path).lower() for path in merged}
        for path in paths:
            key = str(path).lower()
            if key in seen:
                continue
            merged.append(path)
            seen.add(key)
        self._set_existing_spool_paths(merged)

    def _remove_existing_spool_path(self, spool_path: Path) -> None:
        key = str(spool_path).lower()
        self._set_existing_spool_paths([
            path for path in self._existing_spool_paths
            if str(path).lower() != key
        ])

    def _on_browse_existing_spool(self) -> None:
        country = self._selected_country_id()
        default_dir = cl_output_folder_for(country or "", self._current_spool_kind())
        initial_dir = self._last_existing_spool_dir or default_dir
        paths = filedialog.askopenfilenames(
            parent=self,
            title=t("spools_cl.select_spool_file"),
            initialdir=str(initial_dir if initial_dir.exists() else default_dir),
            filetypes=[("SQL files", "*.sql *.SQL"), ("All files", "*.*")],
        )
        if not paths:
            return
        selected_paths = [Path(path) for path in paths]
        self._last_existing_spool_dir = selected_paths[-1].parent
        self._add_existing_spool_paths(selected_paths)

    @staticmethod
    def _account_from_spool_path(
        spool_path: Path,
        spool_kind: str = SPOOL_KIND_CONSUMER_LENDING,
    ) -> str:
        stem = spool_path.stem
        prefix = "CL_Acc_Spool_"
        if stem.upper().startswith(prefix.upper()):
            payload = stem[len(prefix):]
            if spool_kind == SPOOL_KIND_CMR:
                account, sep, branch = payload.rpartition("_")
                if sep and is_valid_account(account) and is_valid_branch(branch):
                    return f"{account}  {branch.upper()}"
            return payload or stem
        return stem

    @staticmethod
    def _apply_existing_items(
        spool_paths: list[Path],
        spool_kind: str = SPOOL_KIND_CONSUMER_LENDING,
    ) -> list[tuple[str, Path]]:
        accounts = [SpoolsCLView._account_from_spool_path(path, spool_kind) for path in spool_paths]
        counts: dict[str, int] = {}
        for account in accounts:
            counts[account] = counts.get(account, 0) + 1
        seen: dict[str, int] = {}
        items: list[tuple[str, Path]] = []
        for account, path in zip(accounts, spool_paths):
            if counts[account] == 1:
                items.append((account, path))
                continue
            seen[account] = seen.get(account, 0) + 1
            items.append((f"{account} #{seen[account]}", path))
        return items

    @staticmethod
    def _format_spool_file_names(spool_paths: list[Path], limit: int = 12) -> str:
        names = [path.name for path in spool_paths]
        if len(names) > limit:
            hidden = len(names) - limit
            names = names[:limit] + [f"... (+{hidden})"]
        return "\n   ".join(names)

    @staticmethod
    def _format_result_accounts(results: list[CLAccountResult], ok: bool) -> list[str]:
        return [
            result.account
            for result in results
            if (result.status == SpoolCLStatus.OK) == ok
        ]

    @staticmethod
    def _pending_account_key(account: str, branch: str = "") -> str:
        return f"{account}::{branch.upper()}" if branch else account

    @staticmethod
    def _split_pending_account_key(account_key: str) -> tuple[str, str]:
        if "::" not in account_key:
            return account_key, ""
        account, branch = account_key.rsplit("::", 1)
        return account, branch.upper()

    def _render_existing_spools(self) -> None:
        for widget in self.existing_spools_frame.winfo_children():
            widget.destroy()
        self.existing_spools_header.configure(
            text=t("spools_cl.selected_existing_spools", n=len(self._existing_spool_paths)),
        )
        if not self._existing_spool_paths:
            ctk.CTkLabel(
                self.existing_spools_frame,
                text=t("spools_cl.no_selected_existing_spools"),
                anchor="w",
                text_color=("gray45", "gray65"),
            ).pack(fill="x", padx=8, pady=8)
            return

        for label, spool_path in self._apply_existing_items(
            self._existing_spool_paths,
            self._current_spool_kind(),
        ):
            row = ctk.CTkFrame(self.existing_spools_frame, fg_color=("gray92", "gray18"), corner_radius=4)
            row.pack(fill="x", padx=4, pady=2)
            text_col = ctk.CTkFrame(row, fg_color="transparent")
            text_col.pack(side="left", fill="x", expand=True, padx=(10, 6), pady=5)
            ctk.CTkLabel(
                text_col,
                text=f"{label}  -  {spool_path.name}",
                anchor="w",
                font=ctk.CTkFont(family="Consolas", size=12),
            ).pack(fill="x")
            ctk.CTkLabel(
                text_col,
                text=str(spool_path.parent),
                anchor="w",
                font=ctk.CTkFont(size=11),
                text_color=("gray42", "gray65"),
            ).pack(fill="x")
            ctk.CTkButton(
                row,
                text="x",
                width=32,
                height=24,
                fg_color=("#D9534F", "#A8322C"),
                hover_color=("#C9302C", "#8B1F1A"),
                text_color="white",
                command=lambda path=spool_path: self._remove_existing_spool_path(path),
            ).pack(side="right", padx=(4, 8), pady=4)

    # ── pending accounts ──
    def _on_add_account(self) -> None:
        raw = self.account_entry.get().strip()
        if not raw:
            return
        if not is_valid_account(raw):
            messagebox.showerror(t("common.error"),
                                 t("spools_cl.invalid_account", acc=raw), parent=self)
            return
        branch = ""
        if self._is_cmr_mode():
            branch = self.branch_entry.get().strip().upper()
            if not branch:
                messagebox.showerror(t("common.error"), t("spools_cl.branch_required"), parent=self)
                return
            if not is_valid_branch(branch):
                messagebox.showerror(
                    t("common.error"),
                    t("spools_cl.invalid_branch", branch=branch),
                    parent=self,
                )
                return
        account_key = self._pending_account_key(raw, branch if self._is_cmr_mode() else "")
        if account_key in self._pending_accounts:
            messagebox.showinfo(t("common.info"), t("spools_cl.duplicate_account"), parent=self)
            return
        self._pending_accounts.append(account_key)
        self._inject_flags[account_key] = True
        if branch:
            self._account_branches[account_key] = branch
        self.account_entry.delete(0, "end")
        self.branch_entry.delete(0, "end")
        self.account_entry.focus_set()
        self._render_pending_accounts()

    def _open_bulk_accounts_dialog(self) -> None:
        dialog = ctk.CTkToplevel(self)
        dialog.title(t("spools_cl.bulk_title"))
        width, height = _BULK_DIALOG_SIZE
        self._center_dialog(dialog, width, height)
        dialog.minsize(500, 360)
        dialog.transient(self.winfo_toplevel())
        dialog.grab_set()
        dialog.grid_columnconfigure(0, weight=1)
        dialog.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(
            dialog,
            text=t("spools_cl.bulk_hint_cmr" if self._is_cmr_mode() else "spools_cl.bulk_hint"),
            anchor="w",
            justify="left",
            wraplength=520,
        ).grid(row=0, column=0, padx=18, pady=(18, 8), sticky="ew")

        text_box = ctk.CTkTextbox(dialog, font=ctk.CTkFont(family="Consolas", size=12))
        text_box.grid(row=1, column=0, padx=18, pady=(0, 10), sticky="nsew")
        self._install_textbox_placeholder(
            text_box,
            t("spools_cl.bulk_placeholder_cmr" if self._is_cmr_mode() else "spools_cl.bulk_placeholder"),
        )
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
            invalid_preview = self._format_invalid_preview(stats["invalid"])
            if stats["invalid"]:
                status_label.configure(text_color=("#8A5A00", "#F0C36D"))
                status_var.set(
                    t(
                        "spools_cl.bulk_result_invalid",
                        added=stats["added"],
                        duplicates=stats["duplicates"],
                        invalid=len(stats["invalid"]),
                        items=invalid_preview,
                    )
                )
                return
            if stats["added"]:
                dialog.destroy()
                return
            status_label.configure(text_color=("#8A5A00", "#F0C36D"))
            if stats["duplicates"]:
                status_var.set(t("spools_cl.bulk_result_duplicates", duplicates=stats["duplicates"]))
            else:
                status_var.set(t("spools_cl.bulk_result_empty"))

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
            text=t("spools_cl.bulk_add"),
            width=140,
            command=submit,
        ).grid(row=0, column=2, sticky="e")

    def _add_bulk_accounts(self, text: str) -> dict[str, object]:
        if self._is_cmr_mode():
            valid_pairs, invalid = parse_account_branches(text)
            valid = [
                self._pending_account_key(account, branch)
                for account, branch in valid_pairs
            ]
            branches = {
                self._pending_account_key(account, branch): branch
                for account, branch in valid_pairs
            }
        else:
            valid, invalid = parse_accounts(text)
            branches = {}
        added = 0
        duplicates = 0
        for account in valid:
            if account in self._pending_accounts:
                duplicates += 1
                continue
            self._pending_accounts.append(account)
            self._inject_flags[account] = True
            if account in branches:
                self._account_branches[account] = branches[account]
            added += 1

        if added:
            self._render_pending_accounts()

        return {
            "added": added,
            "duplicates": duplicates,
            "invalid": invalid,
        }

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
        self._account_branches.pop(account, None)
        self._render_pending_accounts()

    def _set_inject_flag(self, account: str, value: bool) -> None:
        self._inject_flags[account] = value
        self._render_pending_accounts()

    def _selected_inject_accounts(self) -> list[str]:
        if self._is_extract_only_mode():
            return []
        return [acc for acc in self._pending_accounts if self._inject_flags.get(acc, False)]

    def _account_label(self, account: str) -> str:
        account, token_branch = self._split_pending_account_key(account)
        branch = self._account_branches.get(self._pending_account_key(account, token_branch), token_branch)
        return f"{account}  {branch}" if branch else account

    def _render_pending_accounts(self) -> None:
        for w in self.extract_only_frame.winfo_children():
            w.destroy()
        for w in self.inject_frame.winfo_children():
            w.destroy()
        inject_accounts = [] if self._is_extract_only_mode() else self._selected_inject_accounts()
        self.pending_header.configure(text=t("spools_cl.accounts_summary", n=len(self._pending_accounts)))
        self.extract_only_header.configure(text=t("spools_cl.extract_only_header", n=len(self._pending_accounts)))
        self.inject_header.configure(text=t("spools_cl.inject_header", n=len(inject_accounts)))

        for acc in self._pending_accounts:
            self._render_extract_row(self.extract_only_frame, acc)
        for acc in inject_accounts:
            self._render_inject_row(self.inject_frame, acc)
        self._refresh_run_button()

    def _render_extract_row(self, parent, account: str) -> None:
        row = ctk.CTkFrame(parent, fg_color=("gray92", "gray18"), corner_radius=4)
        row.pack(fill="x", padx=4, pady=2)
        ctk.CTkLabel(
            row, text=self._account_label(account), anchor="w",
            font=ctk.CTkFont(family="Consolas", size=12),
        ).pack(side="left", padx=(10, 6), pady=4, fill="x", expand=True)
        ctk.CTkButton(
            row, text="x", width=32, height=24,
            fg_color=("#D9534F", "#A8322C"), hover_color=("#C9302C", "#8B1F1A"),
            text_color="white",
            command=lambda a=account: self._remove_pending(a),
        ).pack(side="right", padx=(4, 8), pady=4)
        if not self._is_extract_only_mode() and not self._inject_flags.get(account, False):
            ctk.CTkButton(
                row, text=t("spools_cl.move_to_inject"), width=90, height=24,
                command=lambda a=account: self._set_inject_flag(a, True),
            ).pack(side="right", padx=4, pady=4)

    def _render_inject_row(self, parent, account: str) -> None:
        row = ctk.CTkFrame(parent, fg_color=("gray92", "gray18"), corner_radius=4)
        row.pack(fill="x", padx=4, pady=2)
        ctk.CTkLabel(
            row, text=self._account_label(account), anchor="w",
            font=ctk.CTkFont(family="Consolas", size=12),
        ).pack(side="left", padx=(10, 6), pady=4, fill="x", expand=True)
        ctk.CTkButton(
            row, text="x", width=32, height=24,
            fg_color=("#D9534F", "#A8322C"), hover_color=("#C9302C", "#8B1F1A"),
            text_color="white",
            command=lambda a=account: self._set_inject_flag(a, False),
        ).pack(side="right", padx=(4, 8), pady=4)

    def _refresh_run_button(self) -> None:
        if not hasattr(self, "run_btn"):
            return
        if self._running:
            return
        if self._is_apply_existing_mode():
            key = "spools_cl.run_apply_existing"
        elif self._is_extract_only_mode():
            key = "spools_cl.run_extract_only"
        else:
            key = "spools_cl.run_extract_apply" if self._selected_inject_accounts() else "spools_cl.run_extract_only"
        self.run_btn.configure(text=t(key))

    def _set_run_button_running(self, running: bool) -> None:
        if running:
            self.run_btn.configure(
                text=t("spools_cl.cancel"),
                command=self._on_cancel,
                state="normal",
                fg_color=_CANCEL_BUTTON_FG,
                hover_color=_CANCEL_BUTTON_HOVER,
                text_color="white",
            )
            return

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
        self.run_btn.configure(text=t("spools_cl.cancelling"), state="disabled")
        self.summary_label.configure(text=t("spools_cl.cancel_requested"))

    # ── run ──
    def _on_run(self) -> None:
        if self._running:
            return
        country = self._selected_country_id()
        if self._is_apply_existing_mode():
            self._on_run_apply_existing(country)
            return
        spool_kind = self._current_spool_kind()
        if not country or not has_cl_template(country, spool_kind):
            messagebox.showerror(
                t("common.error"),
                t("spools_cl.no_template", country=(country or "(none)").title()),
                parent=self,
            )
            return
        db = self._selected_db()
        if not db:
            messagebox.showerror(t("common.error"), t("spools_cl.invalid_db"), parent=self)
            return

        accounts = list(self._pending_accounts)
        if not accounts:
            messagebox.showerror(t("common.error"), t("spools_cl.no_pending"), parent=self)
            return
        if spool_kind == SPOOL_KIND_CMR:
            missing_branch = [account for account in accounts if account not in self._account_branches]
            if missing_branch:
                messagebox.showerror(t("common.error"), t("spools_cl.branch_required"), parent=self)
                return
        inject_accounts = [] if self._is_extract_only_mode() else self._selected_inject_accounts()
        dest_db = self._selected_dest_db() if inject_accounts else None
        if inject_accounts:
            if not dest_db:
                messagebox.showerror(t("common.error"), t("spools_cl.invalid_destination_db"), parent=self)
                return
            if db["id"].upper() == dest_db["id"].upper():
                messagebox.showerror(t("common.error"), t("spools_cl.same_source_destination"), parent=self)
                return

        sqlcl_path = (self.app.config.get("sqlcl_path") or "").strip()
        if not sqlcl_path or not os.path.exists(sqlcl_path):
            messagebox.showerror(t("common.error"), t("spools_cl.no_sqlcl"), parent=self)
            return

        source_cred = self._credential_for_db(country, db["id"])
        if not source_cred:
            messagebox.showerror(t("common.error"),
                                 t("spools_cl.no_creds", db=db["id"]), parent=self)
            return

        dest_connection = ""
        if inject_accounts and dest_db:
            dest_cred = self._credential_for_db(country, dest_db["id"])
            if not dest_cred:
                messagebox.showerror(t("common.error"),
                                     t("spools_cl.no_creds", db=dest_db["id"]), parent=self)
                return
            dest_connection = self._connection_for_credential(dest_cred, dest_db["id"])
            listed = ", ".join(self._account_label(account) for account in inject_accounts[:12])
            if len(inject_accounts) > 12:
                listed += ", ..."
            ok = messagebox.askyesno(
                t("spools_cl.confirm_title"),
                t(
                    "spools_cl.confirm_inject",
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
        self.summary_label.configure(text=t("spools_cl.extracting", done=0, total=len(accounts)))

        threading.Thread(
            target=self._do_run,
            args=(
                run_id, country, accounts, inject_accounts, source_connection,
                dest_connection, sqlcl_path, cancel_event, dict(self._account_branches), spool_kind,
            ),
            daemon=True,
        ).start()

    def _on_run_apply_existing(self, country: str | None) -> None:
        if not country:
            messagebox.showerror(t("common.error"), t("spools_cl.invalid_country"), parent=self)
            return

        dest_db = self._selected_dest_db()
        if not dest_db:
            messagebox.showerror(t("common.error"), t("spools_cl.invalid_destination_db"), parent=self)
            return

        spool_paths = list(self._existing_spool_paths)
        if not spool_paths:
            messagebox.showerror(t("common.error"), t("spools_cl.no_existing_spool"), parent=self)
            return
        invalid_file = next((path for path in spool_paths if path.suffix.lower() != ".sql"), None)
        if invalid_file is not None:
            messagebox.showerror(t("common.error"), t("spools_cl.invalid_spool_file"), parent=self)
            return
        missing_file = next((path for path in spool_paths if not path.is_file()), None)
        if missing_file is not None:
            messagebox.showerror(
                t("common.error"),
                t("spools_cl.spool_file_missing", file=missing_file.name),
                parent=self,
            )
            return

        sqlcl_path = (self.app.config.get("sqlcl_path") or "").strip()
        if not sqlcl_path or not os.path.exists(sqlcl_path):
            messagebox.showerror(t("common.error"), t("spools_cl.no_sqlcl"), parent=self)
            return

        dest_cred = self._credential_for_db(country, dest_db["id"])
        if not dest_cred:
            messagebox.showerror(t("common.error"),
                                 t("spools_cl.no_creds", db=dest_db["id"]), parent=self)
            return
        dest_connection = self._connection_for_credential(dest_cred, dest_db["id"])
        items = self._apply_existing_items(spool_paths, self._current_spool_kind())

        ok = messagebox.askyesno(
            t("spools_cl.confirm_title"),
            t(
                "spools_cl.confirm_apply_existing",
                n=len(items),
                files=self._format_spool_file_names(spool_paths),
                db=dest_db["id"],
            ),
            icon="warning",
            default=messagebox.NO,
            parent=self,
        )
        if not ok:
            return

        self._show_results_card()
        self._prepare_results([account for account, _path in items])

        self._running = True
        self._completed_steps = 0
        self._run_id += 1
        run_id = self._run_id
        self._active_summary_phase = "apply_existing"
        self._cancel_event = threading.Event()
        cancel_event = self._cancel_event
        self.result_detail_label.configure(text="")
        self._set_run_button_running(True)
        self.summary_label.configure(text=t("spools_cl.injecting", done=0, total=len(items)))

        threading.Thread(
            target=self._do_apply_existing,
            args=(run_id, items, dest_connection, sqlcl_path, cancel_event),
            daemon=True,
        ).start()

    def _prepare_results(self, accounts: list[str]) -> None:
        for w in self.results_frame.winfo_children():
            w.destroy()
        self._status_rows = {}
        for acc in accounts:
            row = AccountStatusRow(self.results_frame, account=self._account_label(acc))
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
        branches: dict[str, str],
        spool_kind: str,
    ) -> None:
        engine = SpoolCLEngine(SqlclRunner(sqlcl_path))
        total = len(accounts)
        workers = worker_count_for(total, MAX_PARALLEL_ACCOUNTS)
        log.info("Starting spool extraction batch: accounts=%s workers=%s", total, workers)
        inject_set = set(inject_accounts)

        def on_extract_status(account: str, status: SpoolCLStatus, msg: str) -> None:
            display = msg
            if status == SpoolCLStatus.RUNNING:
                display = t("spools_cl.status_extracting")
            elif status == SpoolCLStatus.OK:
                display = (
                    t("spools_cl.status_ready_to_inject")
                    if account in inject_set else t("spools_cl.status_spool_saved")
                )
            elif status == SpoolCLStatus.CANCELLED:
                display = t("spools_cl.status_cancelled")
            self._post_ui(
                lambda a=account, s=status, m=display, T=total, r=run_id:
                    self._apply_status(a, s, m, T, "spools_cl.extracting", r, "extract")
            )

        results: list[CLAccountResult] = engine.extract_many(
            country,
            accounts,
            source_connection,
            on_extract_status,
            max_workers=MAX_PARALLEL_ACCOUNTS,
            cancel_event=cancel_event,
            branches=branches,
            spool_kind=spool_kind,
        )
        extract_ok = sum(1 for result in results if result.status == SpoolCLStatus.OK)
        extract_err = total - extract_ok
        for result in results:
            log.info(
                "Spool extract result: %s status=%s out=%s err=%s",
                result.account, result.status.value, result.output_path, result.error,
            )

        by_account = {result.account: result for result in results}
        apply_items = [
            (acc, by_account[acc].output_path)
            for acc in inject_accounts
            if by_account.get(acc)
            and by_account[acc].status == SpoolCLStatus.OK
            and by_account[acc].output_path is not None
        ]
        if cancel_event.is_set() or not apply_items:
            details = self._classify_extract_apply(accounts, results, [])
            self._post_ui(
                lambda r=run_id, d=details, c=cancel_event.is_set(): self._finish(
                    extract_ok, extract_err, 0, 0, total, 0, r, d, c,
                )
            )
            return

        self._post_ui(
            lambda total_=len(apply_items), r=run_id, e=cancel_event: self._start_inject_stage(total_, r, e)
        )
        apply_workers = worker_count_for(len(apply_items), MAX_PARALLEL_ACCOUNTS)
        log.info("Starting spool inject batch: accounts=%s workers=%s", len(apply_items), apply_workers)

        def on_apply_status(account: str, status: SpoolCLStatus, msg: str) -> None:
            display = msg
            if status == SpoolCLStatus.RUNNING:
                display = t("spools_cl.status_injecting")
            elif status == SpoolCLStatus.OK:
                display = t("spools_cl.status_injected")
            self._post_ui(
                lambda a=account, s=status, m=display, T=len(apply_items), r=run_id:
                    self._apply_status(a, s, m, T, "spools_cl.injecting", r, "inject")
            )

        apply_results = engine.apply_many(
            apply_items,
            dest_connection,
            on_apply_status,
            max_workers=MAX_PARALLEL_ACCOUNTS,
            cancel_event=cancel_event,
        )
        inject_ok = sum(1 for result in apply_results if result.status == SpoolCLStatus.OK)
        inject_err = len(apply_items) - inject_ok
        for result in apply_results:
            log.info(
                "Spool inject result: %s status=%s out=%s err=%s",
                result.account, result.status.value, result.output_path, result.error,
            )
        details = self._classify_extract_apply(accounts, results, apply_results)
        self._post_ui(
            lambda r=run_id, d=details, c=cancel_event.is_set(): self._finish(
                extract_ok, extract_err, inject_ok, inject_err, total, len(apply_items), r, d, c,
            )
        )

    def _do_apply_existing(
        self,
        run_id: int,
        items: list[tuple[str, Path]],
        dest_connection: str,
        sqlcl_path: str,
        cancel_event: threading.Event,
    ) -> None:
        engine = SpoolCLEngine(SqlclRunner(sqlcl_path))
        log.info("Starting existing spool apply batch: spools=%s", len(items))
        total = len(items)

        def on_apply_status(account_: str, status: SpoolCLStatus, msg: str) -> None:
            display = msg
            if status == SpoolCLStatus.RUNNING:
                display = t("spools_cl.status_injecting")
            elif status == SpoolCLStatus.OK:
                display = t("spools_cl.status_injected")
            elif status == SpoolCLStatus.CANCELLED:
                display = t("spools_cl.status_cancelled")
            self._post_ui(
                lambda a=account_, s=status, m=display, r=run_id:
                    self._apply_status(a, s, m, total, "spools_cl.injecting", r, "apply_existing")
            )

        results = engine.apply_many(
            items,
            dest_connection,
            on_apply_status,
            max_workers=MAX_PARALLEL_ACCOUNTS,
            cancel_event=cancel_event,
        )
        for result in results:
            log.info(
                "Existing spool apply result: %s status=%s out=%s err=%s",
                result.account, result.status.value, result.output_path, result.error,
            )
        ok = sum(1 for result in results if result.status == SpoolCLStatus.OK)
        err = total - ok
        self._post_ui(
            lambda r=run_id, c=cancel_event.is_set(): self._finish_apply_existing(ok, err, r, results, c)
        )

    @staticmethod
    def _classify_extract_apply(
        accounts: list[str],
        extract_results: list[CLAccountResult],
        apply_results: list[CLAccountResult],
    ) -> dict[str, list[str]]:
        extracted_ok = {
            result.account
            for result in extract_results
            if result.status == SpoolCLStatus.OK
        }
        injected_ok = {
            result.account
            for result in apply_results
            if result.status == SpoolCLStatus.OK
        }
        return {
            "injected": [acc for acc in accounts if acc in injected_ok],
            "only_extracted": [
                acc for acc in accounts
                if acc in extracted_ok and acc not in injected_ok
            ],
            "nothing": [acc for acc in accounts if acc not in extracted_ok],
        }

    def _format_accounts(self, accounts: list[str]) -> str:
        return ", ".join(self._account_label(account) for account in accounts) if accounts else "-"

    def _show_extract_apply_details(self, details: dict[str, list[str]]) -> None:
        self.result_detail_label.configure(
            text=t(
                "spools_cl.detail_extract_apply",
                injected=self._format_accounts(details.get("injected", [])),
                only_extracted=self._format_accounts(details.get("only_extracted", [])),
                nothing=self._format_accounts(details.get("nothing", [])),
            ),
        )

    def _show_apply_existing_details(self, results: list[CLAccountResult]) -> None:
        self.result_detail_label.configure(
            text=t(
                "spools_cl.detail_apply_existing",
                injected=self._format_accounts(self._format_result_accounts(results, ok=True)),
                nothing=self._format_accounts(self._format_result_accounts(results, ok=False)),
            ),
        )

    def _apply_status(
        self,
        account: str,
        status: SpoolCLStatus,
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
        self.summary_label.configure(text=t("spools_cl.injecting", done=0, total=total))

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
                    "spools_cl.summary_cancelled",
                    extract_ok=extract_ok,
                    extract_total=extract_total,
                    inject_ok=inject_ok,
                    inject_total=inject_total,
                ),
            )
        elif inject_total:
            self.summary_label.configure(
                text=t(
                    "spools_cl.summary_extract_inject",
                    extract_ok=extract_ok,
                    extract_total=extract_total,
                    inject_ok=inject_ok,
                    inject_total=inject_total,
                    err=extract_err + inject_err,
                ),
            )
        elif extract_err == 0:
            self.summary_label.configure(text=t("spools_cl.summary_ok", ok=extract_ok, total=extract_total))
        else:
            self.summary_label.configure(
                text=t("spools_cl.summary_mixed", ok=extract_ok, err=extract_err, total=extract_total),
            )

    def _finish_apply_existing(
        self,
        ok: int,
        err: int,
        run_id: int,
        results: list[CLAccountResult],
        cancelled: bool,
    ) -> None:
        if run_id != self._run_id:
            return
        self._running = False
        self._active_summary_phase = None
        self._cancel_event = None
        self._set_run_button_running(False)
        self._show_apply_existing_details(results)
        if cancelled:
            self.summary_label.configure(text=t("spools_cl.summary_apply_existing_cancelled"))
            return
        self.summary_label.configure(
            text=t("spools_cl.summary_apply_existing", ok=ok, total=ok + err, err=err),
        )

    # ── open folder ──
    def _on_open_folder(self) -> None:
        country = self._selected_country_id()
        folder = cl_output_folder_for(country or "", self._current_spool_kind())
        folder.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(str(folder))  # Windows-only — same as the rest of the app
        except OSError as e:
            log.warning("Could not open folder %s: %s", folder, e)
            subprocess.Popen(["explorer", str(folder)])
