"""Spools view — Phase 3 EXTRACT_ONLY mode.

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

import customtkinter as ctk
from tkinter import messagebox

from i18n import t
from paths import SPOOLS_OUT_DIR
from settings.config import decrypt_password
from settings.credentials import to_sqlcl_arg
from spools_accounts import databases as dbs
from spools_accounts.spool_engine import (
    AccountResult, SpoolEngine, SpoolStatus,
    has_template, is_valid_account,
)
from spools_accounts.sqlcl import SqlclRunner

from .widgets import AccountStatusRow, IconButton, SectionLabel

log = logging.getLogger(__name__)

# Countries supported in Phase 3 (need a .sql.tmpl). Mexico is hidden here
# until its template lands; the rest of the app still treats it as a country.
_PHASE3_COUNTRIES = [
    (c, c.title()) for c in ("chile", "peru", "colombia") if has_template(c)
]

# Order in which envs appear in the Source DB dropdown.
_ENV_DISPLAY_ORDER = ("prod", "bup_prod", "qa", "bup_qa", "dev")
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
        self._running = False
        self._db_lookup: dict[str, dict] = {}

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

        ctk.CTkLabel(form, text=t("spools.country"), anchor="w", width=160).grid(
            row=0, column=0, padx=4, pady=4, sticky="w",
        )
        self.country_var = ctk.StringVar(value=_PHASE3_COUNTRIES[0][1] if _PHASE3_COUNTRIES else "")
        self.country_menu = ctk.CTkOptionMenu(
            form,
            values=[label for _, label in _PHASE3_COUNTRIES] or ["—"],
            variable=self.country_var,
            command=lambda _v: self._refresh_db_options(),
        )
        self.country_menu.grid(row=0, column=1, padx=4, pady=4, sticky="ew")

        ctk.CTkLabel(form, text=t("spools.source_db"), anchor="w", width=160).grid(
            row=1, column=0, padx=4, pady=4, sticky="w",
        )
        self.db_var = ctk.StringVar(value="")
        self.db_menu = ctk.CTkOptionMenu(form, values=["—"], variable=self.db_var)
        self.db_menu.grid(row=1, column=1, padx=4, pady=4, sticky="ew")

        # ── account input row ──
        acc_row = ctk.CTkFrame(self, fg_color="transparent")
        acc_row.pack(fill="x", padx=20, pady=(8, 2))
        ctk.CTkLabel(
            acc_row, text=t("spools.account_number"), anchor="w", width=160,
        ).pack(side="left", padx=4)
        self.account_entry = ctk.CTkEntry(
            acc_row, font=ctk.CTkFont(family="Consolas", size=12),
            placeholder_text="e.g. 209991341468",
        )
        self.account_entry.pack(side="left", padx=4, fill="x", expand=True)
        self.account_entry.bind("<Return>", lambda _e: self._on_add_account())
        IconButton(
            acc_row, text=t("spools.add_account"), width=90,
            command=self._on_add_account,
        ).pack(side="left", padx=4)

        # ── pending accounts list ──
        self.pending_header = SectionLabel(self, text=t("spools.added_accounts", n=0))
        self.pending_header.pack(anchor="w", padx=24, pady=(8, 2))
        self.pending_frame = ctk.CTkScrollableFrame(self, height=100)
        self.pending_frame.pack(fill="x", padx=20, pady=(0, 6))
        self._render_pending_accounts()

        # ── actions ──
        actions = ctk.CTkFrame(self, fg_color="transparent")
        actions.pack(fill="x", padx=20, pady=(0, 4))
        self.run_btn = IconButton(
            actions, text=t("spools.run"), width=180,
            command=self._on_run,
        )
        self.run_btn.pack(side="left")
        self.summary_label = ctk.CTkLabel(
            actions, text="", anchor="e",
            text_color=("gray35", "gray70"),
        )
        self.summary_label.pack(side="right", padx=8, fill="x", expand=True)

        # ── results area ──
        self.results_frame = ctk.CTkScrollableFrame(self, height=140)
        self.results_frame.pack(fill="both", expand=True, padx=20, pady=(4, 8))

        # ── bottom row ──
        bottom = ctk.CTkFrame(self, fg_color="transparent")
        bottom.pack(side="bottom", fill="x", padx=20, pady=(0, 15))
        ctk.CTkButton(
            bottom, text=t("spools.open_folder"), width=180,
            command=self._on_open_folder,
        ).pack(side="left")

        self._refresh_db_options()

    # ── DB dropdown ──
    def _selected_country_id(self) -> str | None:
        label = self.country_var.get()
        return next((cid for cid, lbl in _PHASE3_COUNTRIES if lbl == label), None)

    def _refresh_db_options(self) -> None:
        country = self._selected_country_id()
        labels: list[str] = []
        self._db_lookup = {}
        if country:
            for env in _ENV_DISPLAY_ORDER:
                for db in dbs.databases_for(country, env=env):
                    tag = _ENV_TAG.get(env, env.upper())
                    label = f"{tag}  ·  {db['label']}  ·  {db['id']}"
                    labels.append(label)
                    self._db_lookup[label] = db
        if not labels:
            self.db_menu.configure(values=["—"])
            self.db_var.set("—")
            return
        self.db_menu.configure(values=labels)
        self.db_var.set(labels[0])

    def _selected_db(self) -> dict | None:
        return self._db_lookup.get(self.db_var.get())

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
        self.account_entry.delete(0, "end")
        self.account_entry.focus_set()
        self._render_pending_accounts()

    def _remove_pending(self, account: str) -> None:
        try:
            self._pending_accounts.remove(account)
        except ValueError:
            return
        self._render_pending_accounts()

    def _render_pending_accounts(self) -> None:
        for w in self.pending_frame.winfo_children():
            w.destroy()
        self.pending_header.configure(
            text=t("spools.added_accounts", n=len(self._pending_accounts)),
        )
        for acc in self._pending_accounts:
            row = ctk.CTkFrame(self.pending_frame, fg_color=("gray92", "gray18"), corner_radius=4)
            row.pack(fill="x", padx=4, pady=2)
            ctk.CTkLabel(
                row, text=acc, anchor="w",
                font=ctk.CTkFont(family="Consolas", size=12),
            ).pack(side="left", padx=10, pady=4, fill="x", expand=True)
            ctk.CTkButton(
                row, text="×", width=32, height=24,
                fg_color=("#D9534F", "#A8322C"), hover_color=("#C9302C", "#8B1F1A"),
                text_color="white",
                command=lambda a=acc: self._remove_pending(a),
            ).pack(side="right", padx=4, pady=4)

    # ── run ──
    def _on_run(self) -> None:
        if self._running:
            return
        country = self._selected_country_id()
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

        sqlcl_path = (self.app.config.get("sqlcl_path") or "").strip()
        if not sqlcl_path or not os.path.exists(sqlcl_path):
            messagebox.showerror(t("common.error"), t("spools.no_sqlcl"), parent=self)
            return

        # Pick any saved credential for the chosen DB. If several, fall back to first.
        cred = self.app.config.get_credential(country, db["id"])
        if cred is None:
            by_login = self.app.config.all_credentials().get(country, {}).get(db["id"].upper(), {})
            cred = next(iter(by_login.values()), None)
        if not cred:
            messagebox.showerror(t("common.error"),
                                 t("spools.no_creds", db=db["id"]), parent=self)
            return

        accounts = list(self._pending_accounts)
        if not accounts:
            messagebox.showerror(t("common.error"), t("spools.no_pending"), parent=self)
            return

        password = decrypt_password(cred.get("password_enc", ""))
        connection = to_sqlcl_arg(
            cred.get("user", ""),
            cred.get("schema") or None,
            password,
            cred.get("tns") or db["id"],
        )

        # Reset results UI and create one row per account
        for w in self.results_frame.winfo_children():
            w.destroy()
        self._status_rows = {}
        for acc in accounts:
            row = AccountStatusRow(self.results_frame, account=acc)
            row.pack(fill="x", padx=4, pady=2)
            self._status_rows[acc] = row

        self._running = True
        self.run_btn.configure(state="disabled")
        self.summary_label.configure(text=t("spools.running", done=0, total=len(accounts)))

        threading.Thread(
            target=self._do_run,
            args=(country, accounts, connection, sqlcl_path),
            daemon=True,
        ).start()

    def _do_run(self, country: str, accounts: list[str], connection: str, sqlcl_path: str) -> None:
        engine = SpoolEngine(SqlclRunner(sqlcl_path))
        ok = err = 0
        total = len(accounts)
        for i, acc in enumerate(accounts, 1):
            def on_status(account: str, status: SpoolStatus, msg: str,
                          done=i, total_=total) -> None:
                self.app.root.after(
                    0,
                    lambda a=account, s=status, m=msg, d=done, T=total_:
                        self._apply_status(a, s, m, d, T),
                )
            result: AccountResult = engine.extract_one(country, acc, connection, on_status)
            if result.status == SpoolStatus.OK:
                ok += 1
            else:
                err += 1
            log.info(
                "Spool result: %s status=%s out=%s err=%s",
                acc, result.status.value, result.output_path, result.error,
            )
        self.app.root.after(0, lambda: self._finish(ok, err, total))

    def _apply_status(self, account: str, status: SpoolStatus, message: str,
                       done: int, total: int) -> None:
        row = self._status_rows.get(account)
        if row is not None:
            row.set_status(status.value, message)
        # Update the progress counter only when an account finishes (OK/ERROR),
        # not when it just moves to RUNNING.
        if status in (SpoolStatus.OK, SpoolStatus.ERROR):
            self.summary_label.configure(text=t("spools.running", done=done, total=total))

    def _finish(self, ok: int, err: int, total: int) -> None:
        self._running = False
        self.run_btn.configure(state="normal")
        if err == 0:
            self.summary_label.configure(text=t("spools.summary_ok", ok=ok, total=total))
        else:
            self.summary_label.configure(
                text=t("spools.summary_mixed", ok=ok, err=err, total=total),
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
