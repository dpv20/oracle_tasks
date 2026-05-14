"""Spools view — extract/apply accounts and apply existing spool files.

Layout:
  [ Back ]  Spools / Accounts
  Country dropdown
  Source DB dropdown (any env of the selected country)
  Account number  [ entry ] [ + Add ]
  Accounts to extract (n)
    · 209991341468  [×]
    · ...
  [ Extract spools ]
  ─── results ───
  status rows (one per account, with spinner / OK / error)
  [ Open spools folder ]

Threading: extraction runs on a daemon thread; per-account status callbacks
are marshalled back to the Tk thread via `app.root.after(0, ...)`.
"""
from __future__ import annotations

import logging
import os
import subprocess
import threading
from pathlib import Path

import customtkinter as ctk
from tkinter import filedialog, messagebox

from i18n import t
from paths import SPOOLS_OUT_DIR
from settings.config import decrypt_password
from settings.credentials import to_sqlcl_arg
from spools_accounts import databases as dbs
from spools_accounts.spool_engine import (
    MAX_PARALLEL_ACCOUNTS, AccountResult, SpoolEngine, SpoolStatus,
    has_template, is_valid_account, worker_count_for,
)
from spools_accounts.sqlcl import SqlclRunner

from .widgets import AccountStatusRow, IconButton, SectionLabel

log = logging.getLogger(__name__)

# Countries supported in extract mode (need a .sql.tmpl). Mexico is hidden here
# until its template lands; apply-existing can still target countries with a
# destination DB because it does not render a country template.
_EXTRACT_COUNTRIES = [
    (c, c.title()) for c in ("chile", "peru", "colombia") if has_template(c)
]
MODE_EXTRACT = "extract"
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


class SpoolsView(ctk.CTkFrame):
    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self._status_rows: dict[str, AccountStatusRow] = {}
        self._pending_accounts: list[str] = []
        self._inject_flags: dict[str, bool] = {}
        self._completed_steps = 0
        self._running = False
        self._db_lookup: dict[str, dict] = {}
        self._dest_db_lookup: dict[str, dict] = {}
        self._country_lookup: dict[str, str] = {}
        self._existing_spool_path: Path | None = None

        # ── header ──
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(side="top", fill="x", padx=20, pady=(20, 10))
        IconButton(
            header, text=f"← {t('common.back')}", width=100,
            command=lambda: app.show_view("home"),
        ).pack(side="left")
        ctk.CTkLabel(
            header, text=t("spools.title"),
            font=ctk.CTkFont(size=18, weight="bold"),
        ).pack(side="left", padx=15)

        # ── form area ──
        form = ctk.CTkFrame(self, fg_color="transparent")
        form.pack(side="top", fill="x", padx=20, pady=(0, 8))
        form.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(form, text=t("spools.mode"), anchor="w", width=160).grid(
            row=0, column=0, padx=4, pady=4, sticky="w",
        )
        self.mode_segment = ctk.CTkSegmentedButton(
            form,
            values=[t("spools.mode.extract"), t("spools.mode.apply_existing")],
            command=lambda _v: self._on_mode_change(),
        )
        self.mode_segment.grid(row=0, column=1, padx=4, pady=4, sticky="ew")
        self.mode_segment.set(t("spools.mode.extract"))

        ctk.CTkLabel(form, text=t("spools.country"), anchor="w", width=160).grid(
            row=1, column=0, padx=4, pady=4, sticky="w",
        )
        self._country_lookup = {label: cid for cid, label in _EXTRACT_COUNTRIES}
        self.country_var = ctk.StringVar(value=_EXTRACT_COUNTRIES[0][1] if _EXTRACT_COUNTRIES else "")
        self.country_menu = ctk.CTkOptionMenu(
            form,
            values=[label for _, label in _EXTRACT_COUNTRIES] or ["—"],
            variable=self.country_var,
            command=lambda _v: self._refresh_db_options(),
        )
        self.country_menu.grid(row=1, column=1, padx=4, pady=4, sticky="ew")

        self.source_label = ctk.CTkLabel(form, text=t("spools.source_db"), anchor="w", width=160)
        self.source_label.grid(
            row=2, column=0, padx=4, pady=4, sticky="w",
        )
        self.db_var = ctk.StringVar(value="")
        self.db_menu = ctk.CTkOptionMenu(form, values=["—"], variable=self.db_var)
        self.db_menu.grid(row=2, column=1, padx=4, pady=4, sticky="ew")

        self.dest_label = ctk.CTkLabel(form, text=t("spools.destination_db"), anchor="w", width=160)
        self.dest_label.grid(
            row=3, column=0, padx=4, pady=4, sticky="w",
        )
        self.dest_db_var = ctk.StringVar(value="")
        self.dest_db_menu = ctk.CTkOptionMenu(form, values=["-"], variable=self.dest_db_var)
        self.dest_db_menu.grid(row=3, column=1, padx=4, pady=4, sticky="ew")

        self.existing_spool_label = ctk.CTkLabel(
            form, text=t("spools.existing_spool"), anchor="w", width=160,
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
            text=t("spools.browse_spool"),
            width=110,
            command=self._on_browse_existing_spool,
        ).pack(side="left", padx=(8, 0))
        self.existing_spool_label.grid(row=4, column=0, padx=4, pady=4, sticky="w")
        self.existing_spool_frame.grid(row=4, column=1, padx=4, pady=4, sticky="ew")

        # ── account input row ──
        self.account_row = ctk.CTkFrame(self, fg_color="transparent")
        self.account_row.pack(fill="x", padx=20, pady=(8, 2))
        ctk.CTkLabel(
            self.account_row, text=t("spools.account_number"), anchor="w", width=160,
        ).pack(side="left", padx=4)
        self.account_entry = ctk.CTkEntry(
            self.account_row, font=ctk.CTkFont(family="Consolas", size=12),
            placeholder_text="e.g. 209991341468",
        )
        self.account_entry.pack(side="left", padx=4, fill="x", expand=True)
        self.account_entry.bind("<Return>", lambda _e: self._on_add_account())
        IconButton(
            self.account_row, text=t("spools.add_account"), width=90,
            command=self._on_add_account,
        ).pack(side="left", padx=4)

        # ── pending accounts list ──
        self.pending_header = SectionLabel(self, text=t("spools.accounts_summary", n=0))
        self.pending_header.pack(anchor="w", padx=24, pady=(8, 2))

        self.account_split = ctk.CTkFrame(self, fg_color="transparent")
        self.account_split.pack(fill="both", expand=True, padx=20, pady=(0, 6))
        self.account_split.grid_columnconfigure(0, weight=1, uniform="account_lists")
        self.account_split.grid_columnconfigure(1, weight=1, uniform="account_lists")
        self.account_split.grid_rowconfigure(1, weight=1)

        self.extract_only_header = SectionLabel(self.account_split, text=t("spools.extract_only_header", n=0))
        self.extract_only_header.grid(row=0, column=0, sticky="ew", padx=(0, 6), pady=(0, 4))
        self.inject_header = SectionLabel(self.account_split, text=t("spools.inject_header", n=0))
        self.inject_header.grid(row=0, column=1, sticky="ew", padx=(6, 0), pady=(0, 4))

        self.extract_only_frame = ctk.CTkScrollableFrame(self.account_split, height=180)
        self.extract_only_frame.grid(row=1, column=0, sticky="nsew", padx=(0, 6))
        self.inject_frame = ctk.CTkScrollableFrame(self.account_split, height=180)
        self.inject_frame.grid(row=1, column=1, sticky="nsew", padx=(6, 0))
        self._render_pending_accounts()

        # ── actions ──
        self.actions_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.actions_frame.pack(fill="x", padx=20, pady=(0, 4))
        self.run_btn = IconButton(
            self.actions_frame, text=t("spools.run_extract_only"), width=220,
            command=self._on_run,
        )
        self.run_btn.pack(side="left")
        ctk.CTkButton(
            self.actions_frame, text=t("spools.open_folder"), width=180,
            command=self._on_open_folder,
        ).pack(side="left", padx=(10, 0))
        self.summary_label = ctk.CTkLabel(
            self.actions_frame, text="", anchor="e",
            text_color=("gray35", "gray70"),
        )
        self.summary_label.pack(side="right", padx=8, fill="x", expand=True)

        # ── results area ──
        self.results_frame = ctk.CTkScrollableFrame(self, height=140)
        self.results_frame.pack(fill="both", expand=True, padx=20, pady=(4, 8))

        self._apply_mode_visibility()
        self._refresh_db_options()

    # ── DB dropdown ──
    def _current_mode(self) -> str:
        if self.mode_segment.get() == t("spools.mode.apply_existing"):
            return MODE_APPLY_EXISTING
        return MODE_EXTRACT

    def _is_apply_existing_mode(self) -> bool:
        return self._current_mode() == MODE_APPLY_EXISTING

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
        self._refresh_country_options(previous_country)
        self._apply_mode_visibility()
        self._refresh_db_options()
        self._refresh_run_button()

    def _apply_mode_visibility(self) -> None:
        if self._is_apply_existing_mode():
            self.source_label.grid_remove()
            self.db_menu.grid_remove()
            self.existing_spool_label.grid()
            self.existing_spool_frame.grid()
            self.account_row.pack_forget()
            self.pending_header.pack_forget()
            self.account_split.pack_forget()
            return

        self.source_label.grid()
        self.db_menu.grid()
        self.existing_spool_label.grid_remove()
        self.existing_spool_frame.grid_remove()
        if not self.account_row.winfo_manager():
            self.account_row.pack(fill="x", padx=20, pady=(8, 2), before=self.actions_frame)
        if not self.pending_header.winfo_manager():
            self.pending_header.pack(anchor="w", padx=24, pady=(8, 2), before=self.actions_frame)
        if not self.account_split.winfo_manager():
            self.account_split.pack(fill="both", expand=True, padx=20, pady=(0, 6), before=self.actions_frame)

    def _selected_country_id(self) -> str | None:
        label = self.country_var.get()
        return self._country_lookup.get(label)

    def _refresh_db_options(self) -> None:
        country = self._selected_country_id()
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

    def _on_browse_existing_spool(self) -> None:
        country = self._selected_country_id()
        initial_dir = SPOOLS_OUT_DIR / (country.title() if country else "")
        path = filedialog.askopenfilename(
            parent=self,
            title=t("spools.select_spool_file"),
            initialdir=str(initial_dir if initial_dir.exists() else SPOOLS_OUT_DIR),
            filetypes=[("SQL files", "*.sql *.SQL"), ("All files", "*.*")],
        )
        if not path:
            return
        self._existing_spool_path = Path(path)
        self.existing_spool_var.set(str(self._existing_spool_path))

    @staticmethod
    def _account_from_spool_path(spool_path: Path) -> str:
        stem = spool_path.stem
        prefix = "CL_Acc_Spool_"
        if stem.upper().startswith(prefix.upper()):
            return stem[len(prefix):] or stem
        return stem

    # ── pending accounts ──
    def _on_add_account(self) -> None:
        raw = self.account_entry.get().strip()
        if not raw:
            return
        if not is_valid_account(raw):
            messagebox.showerror(t("common.error"),
                                 t("spools.invalid_account", acc=raw), parent=self)
            return
        if raw in self._pending_accounts:
            messagebox.showinfo(t("common.info"), t("spools.duplicate_account"), parent=self)
            return
        self._pending_accounts.append(raw)
        self._inject_flags[raw] = True
        self.account_entry.delete(0, "end")
        self.account_entry.focus_set()
        self._render_pending_accounts()

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
        return [acc for acc in self._pending_accounts if self._inject_flags.get(acc, False)]

    def _render_pending_accounts(self) -> None:
        for w in self.extract_only_frame.winfo_children():
            w.destroy()
        for w in self.inject_frame.winfo_children():
            w.destroy()
        inject_accounts = self._selected_inject_accounts()
        self.pending_header.configure(text=t("spools.accounts_summary", n=len(self._pending_accounts)))
        self.extract_only_header.configure(text=t("spools.extract_only_header", n=len(self._pending_accounts)))
        self.inject_header.configure(text=t("spools.inject_header", n=len(inject_accounts)))

        for acc in self._pending_accounts:
            self._render_extract_row(self.extract_only_frame, acc)
        for acc in inject_accounts:
            self._render_inject_row(self.inject_frame, acc)
        self._refresh_run_button()

    def _render_extract_row(self, parent, account: str) -> None:
        row = ctk.CTkFrame(parent, fg_color=("gray92", "gray18"), corner_radius=4)
        row.pack(fill="x", padx=4, pady=2)
        ctk.CTkLabel(
            row, text=account, anchor="w",
            font=ctk.CTkFont(family="Consolas", size=12),
        ).pack(side="left", padx=(10, 6), pady=4, fill="x", expand=True)
        ctk.CTkButton(
            row, text="x", width=32, height=24,
            fg_color=("#D9534F", "#A8322C"), hover_color=("#C9302C", "#8B1F1A"),
            text_color="white",
            command=lambda a=account: self._remove_pending(a),
        ).pack(side="right", padx=(4, 8), pady=4)
        if not self._inject_flags.get(account, False):
            ctk.CTkButton(
                row, text=t("spools.move_to_inject"), width=90, height=24,
                command=lambda a=account: self._set_inject_flag(a, True),
            ).pack(side="right", padx=4, pady=4)

    def _render_inject_row(self, parent, account: str) -> None:
        row = ctk.CTkFrame(parent, fg_color=("gray92", "gray18"), corner_radius=4)
        row.pack(fill="x", padx=4, pady=2)
        ctk.CTkLabel(
            row, text=account, anchor="w",
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
        if self._is_apply_existing_mode():
            key = "spools.run_apply_existing"
        else:
            key = "spools.run_extract_apply" if self._selected_inject_accounts() else "spools.run_extract_only"
        self.run_btn.configure(text=t(key))

    # ── run ──
    def _on_run(self) -> None:
        if self._running:
            return
        country = self._selected_country_id()
        if self._is_apply_existing_mode():
            self._on_run_apply_existing(country)
            return
        if not country or not has_template(country):
            messagebox.showerror(
                t("common.error"),
                t("spools.no_template", country=(country or "(none)").title()),
                parent=self,
            )
            return
        db = self._selected_db()
        if not db:
            messagebox.showerror(t("common.error"), t("spools.invalid_db"), parent=self)
            return

        accounts = list(self._pending_accounts)
        if not accounts:
            messagebox.showerror(t("common.error"), t("spools.no_pending"), parent=self)
            return
        inject_accounts = self._selected_inject_accounts()
        dest_db = self._selected_dest_db() if inject_accounts else None
        if inject_accounts:
            if not dest_db:
                messagebox.showerror(t("common.error"), t("spools.invalid_destination_db"), parent=self)
                return
            if db["id"].upper() == dest_db["id"].upper():
                messagebox.showerror(t("common.error"), t("spools.same_source_destination"), parent=self)
                return

        sqlcl_path = (self.app.config.get("sqlcl_path") or "").strip()
        if not sqlcl_path or not os.path.exists(sqlcl_path):
            messagebox.showerror(t("common.error"), t("spools.no_sqlcl"), parent=self)
            return

        source_cred = self._credential_for_db(country, db["id"])
        if not source_cred:
            messagebox.showerror(t("common.error"),
                                 t("spools.no_creds", db=db["id"]), parent=self)
            return

        dest_connection = ""
        if inject_accounts and dest_db:
            dest_cred = self._credential_for_db(country, dest_db["id"])
            if not dest_cred:
                messagebox.showerror(t("common.error"),
                                     t("spools.no_creds", db=dest_db["id"]), parent=self)
                return
            dest_connection = self._connection_for_credential(dest_cred, dest_db["id"])
            listed = ", ".join(inject_accounts[:12])
            if len(inject_accounts) > 12:
                listed += ", ..."
            ok = messagebox.askyesno(
                t("spools.confirm_title"),
                t(
                    "spools.confirm_inject",
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

        # Reset results UI and create one row per account
        for w in self.results_frame.winfo_children():
            w.destroy()
        self._status_rows = {}
        for acc in accounts:
            row = AccountStatusRow(self.results_frame, account=acc)
            row.pack(fill="x", padx=4, pady=2)
            self._status_rows[acc] = row

        self._running = True
        self._completed_steps = 0
        self.run_btn.configure(state="disabled")
        self.summary_label.configure(text=t("spools.extracting", done=0, total=len(accounts)))

        threading.Thread(
            target=self._do_run,
            args=(country, accounts, inject_accounts, source_connection, dest_connection, sqlcl_path),
            daemon=True,
        ).start()

    def _on_run_apply_existing(self, country: str | None) -> None:
        if not country:
            messagebox.showerror(t("common.error"), t("spools.invalid_country"), parent=self)
            return

        dest_db = self._selected_dest_db()
        if not dest_db:
            messagebox.showerror(t("common.error"), t("spools.invalid_destination_db"), parent=self)
            return

        spool_path = self._existing_spool_path
        if spool_path is None and self.existing_spool_var.get().strip():
            spool_path = Path(self.existing_spool_var.get().strip())
        if spool_path is None:
            messagebox.showerror(t("common.error"), t("spools.no_existing_spool"), parent=self)
            return
        if spool_path.suffix.lower() != ".sql":
            messagebox.showerror(t("common.error"), t("spools.invalid_spool_file"), parent=self)
            return
        if not spool_path.is_file():
            messagebox.showerror(
                t("common.error"),
                t("spools.spool_file_missing", file=spool_path.name),
                parent=self,
            )
            return

        sqlcl_path = (self.app.config.get("sqlcl_path") or "").strip()
        if not sqlcl_path or not os.path.exists(sqlcl_path):
            messagebox.showerror(t("common.error"), t("spools.no_sqlcl"), parent=self)
            return

        dest_cred = self._credential_for_db(country, dest_db["id"])
        if not dest_cred:
            messagebox.showerror(t("common.error"),
                                 t("spools.no_creds", db=dest_db["id"]), parent=self)
            return
        dest_connection = self._connection_for_credential(dest_cred, dest_db["id"])
        account = self._account_from_spool_path(spool_path)

        ok = messagebox.askyesno(
            t("spools.confirm_title"),
            t(
                "spools.confirm_apply_existing",
                file=spool_path.name,
                db=dest_db["id"],
            ),
            icon="warning",
            default=messagebox.NO,
            parent=self,
        )
        if not ok:
            return

        for w in self.results_frame.winfo_children():
            w.destroy()
        self._status_rows = {}
        row = AccountStatusRow(self.results_frame, account=account)
        row.pack(fill="x", padx=4, pady=2)
        self._status_rows[account] = row

        self._running = True
        self._completed_steps = 0
        self.run_btn.configure(state="disabled")
        self.summary_label.configure(text=t("spools.injecting", done=0, total=1))

        threading.Thread(
            target=self._do_apply_existing,
            args=(account, spool_path, dest_connection, sqlcl_path),
            daemon=True,
        ).start()

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

    def _do_run(
        self,
        country: str,
        accounts: list[str],
        inject_accounts: list[str],
        source_connection: str,
        dest_connection: str,
        sqlcl_path: str,
    ) -> None:
        engine = SpoolEngine(SqlclRunner(sqlcl_path))
        total = len(accounts)
        workers = worker_count_for(total, MAX_PARALLEL_ACCOUNTS)
        log.info("Starting spool extraction batch: accounts=%s workers=%s", total, workers)
        inject_set = set(inject_accounts)

        def on_extract_status(account: str, status: SpoolStatus, msg: str) -> None:
            display = msg
            if status == SpoolStatus.RUNNING:
                display = t("spools.status_extracting")
            elif status == SpoolStatus.OK:
                display = (
                    t("spools.status_ready_to_inject")
                    if account in inject_set else t("spools.status_spool_saved")
                )
            self.app.root.after(
                0,
                lambda a=account, s=status, m=display, T=total:
                    self._apply_status(a, s, m, T, "spools.extracting"),
            )

        results: list[AccountResult] = engine.extract_many(
            country,
            accounts,
            source_connection,
            on_extract_status,
            max_workers=MAX_PARALLEL_ACCOUNTS,
        )
        extract_ok = sum(1 for result in results if result.status == SpoolStatus.OK)
        extract_err = sum(1 for result in results if result.status != SpoolStatus.OK)
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
            and by_account[acc].status == SpoolStatus.OK
            and by_account[acc].output_path is not None
        ]
        if not apply_items:
            self.app.root.after(0, lambda: self._finish(extract_ok, extract_err, 0, 0, total, 0))
            return

        self.app.root.after(0, lambda total_=len(apply_items): self._start_inject_stage(total_))
        apply_workers = worker_count_for(len(apply_items), MAX_PARALLEL_ACCOUNTS)
        log.info("Starting spool inject batch: accounts=%s workers=%s", len(apply_items), apply_workers)

        def on_apply_status(account: str, status: SpoolStatus, msg: str) -> None:
            display = msg
            if status == SpoolStatus.RUNNING:
                display = t("spools.status_injecting")
            elif status == SpoolStatus.OK:
                display = t("spools.status_injected")
            self.app.root.after(
                0,
                lambda a=account, s=status, m=display, T=len(apply_items):
                    self._apply_status(a, s, m, T, "spools.injecting"),
            )

        apply_results = engine.apply_many(
            apply_items,
            dest_connection,
            on_apply_status,
            max_workers=MAX_PARALLEL_ACCOUNTS,
        )
        inject_ok = sum(1 for result in apply_results if result.status == SpoolStatus.OK)
        inject_err = sum(1 for result in apply_results if result.status != SpoolStatus.OK)
        for result in apply_results:
            log.info(
                "Spool inject result: %s status=%s out=%s err=%s",
                result.account, result.status.value, result.output_path, result.error,
            )
        self.app.root.after(
            0,
            lambda: self._finish(extract_ok, extract_err, inject_ok, inject_err, total, len(apply_items)),
        )

    def _do_apply_existing(
        self,
        account: str,
        spool_path: Path,
        dest_connection: str,
        sqlcl_path: str,
    ) -> None:
        engine = SpoolEngine(SqlclRunner(sqlcl_path))
        log.info("Starting existing spool apply: account=%s spool=%s", account, spool_path)

        def on_apply_status(account_: str, status: SpoolStatus, msg: str) -> None:
            display = msg
            if status == SpoolStatus.RUNNING:
                display = t("spools.status_injecting")
            elif status == SpoolStatus.OK:
                display = t("spools.status_injected")
            self.app.root.after(
                0,
                lambda a=account_, s=status, m=display:
                    self._apply_status(a, s, m, 1, "spools.injecting"),
            )

        result = engine.apply_one(account, dest_connection, spool_path, on_apply_status)
        log.info(
            "Existing spool apply result: %s status=%s out=%s err=%s",
            result.account, result.status.value, result.output_path, result.error,
        )
        ok = 1 if result.status == SpoolStatus.OK else 0
        err = 0 if result.status == SpoolStatus.OK else 1
        self.app.root.after(0, lambda: self._finish_apply_existing(ok, err))

    def _apply_status(self, account: str, status: SpoolStatus, message: str,
                       total: int, summary_key: str) -> None:
        row = self._status_rows.get(account)
        if row is not None:
            row.set_status(status.value, message)
        if status in (SpoolStatus.OK, SpoolStatus.ERROR):
            self._completed_steps += 1
            self.summary_label.configure(text=t(summary_key, done=self._completed_steps, total=total))

    def _start_inject_stage(self, total: int) -> None:
        self._completed_steps = 0
        self.summary_label.configure(text=t("spools.injecting", done=0, total=total))

    def _finish(
        self,
        extract_ok: int,
        extract_err: int,
        inject_ok: int,
        inject_err: int,
        extract_total: int,
        inject_total: int,
    ) -> None:
        self._running = False
        self.run_btn.configure(state="normal")
        if inject_total:
            self.summary_label.configure(
                text=t(
                    "spools.summary_extract_inject",
                    extract_ok=extract_ok,
                    extract_total=extract_total,
                    inject_ok=inject_ok,
                    inject_total=inject_total,
                    err=extract_err + inject_err,
                ),
            )
        elif extract_err == 0:
            self.summary_label.configure(text=t("spools.summary_ok", ok=extract_ok, total=extract_total))
        else:
            self.summary_label.configure(
                text=t("spools.summary_mixed", ok=extract_ok, err=extract_err, total=extract_total),
            )

    def _finish_apply_existing(self, ok: int, err: int) -> None:
        self._running = False
        self.run_btn.configure(state="normal")
        self.summary_label.configure(
            text=t("spools.summary_apply_existing", ok=ok, total=ok + err, err=err),
        )

    # ── open folder ──
    def _on_open_folder(self) -> None:
        country = self._selected_country_id()
        folder = SPOOLS_OUT_DIR / (country.title() if country else "")
        folder.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(str(folder))  # Windows-only — same as the rest of the app
        except OSError as e:
            log.warning("Could not open folder %s: %s", folder, e)
            subprocess.Popen(["explorer", str(folder)])
