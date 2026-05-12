"""Settings view — Credentials, General, About tabs."""
from __future__ import annotations

import logging
import os
from tkinter import filedialog, messagebox

import customtkinter as ctk

from settings.config import decrypt_password, encrypt_password
from settings.credentials import (
    credential_dict,
    parse,
    parse_many,
)
from i18n import t
from version import __version__

from .widgets import IconButton, SectionLabel

log = logging.getLogger(__name__)

COUNTRIES = [("chile", "Chile"), ("peru", "Peru"), ("colombia", "Colombia"), ("mexico", "Mexico")]
ENVS = [
    ("shared_prod", "PROD (shared)"),
    ("user_qa", "QA"),
    ("user_dev", "DEV"),
    ("user_bup_qa", "BUP QA"),
    ("user_bup_prod", "BUP PROD"),
]
# Display order + short label for the saved-credentials tree
ENV_DISPLAY = [
    ("shared_prod",   "PROD"),
    ("user_qa",       "QA"),
    ("user_dev",      "DEV"),
    ("user_bup_qa",   "BUP QA"),
    ("user_bup_prod", "BUP PROD"),
]
ENV_LABEL = dict(ENV_DISPLAY)


class SettingsView(ctk.CTkFrame):
    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app = app

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(side="top", fill="x", padx=20, pady=(20, 10))
        IconButton(
            header, text=f"← {t('common.back')}", width=100,
            command=lambda: app.show_view("home"),
        ).pack(side="left")
        ctk.CTkLabel(
            header, text=t("settings.title"),
            font=ctk.CTkFont(size=18, weight="bold"),
        ).pack(side="left", padx=15)

        self.tabs = ctk.CTkTabview(self)
        self.tabs.pack(fill="both", expand=True, padx=20, pady=(5, 20))

        self.tabs.add(t("settings.tab.credentials"))
        self.tabs.add(t("settings.tab.general"))
        self.tabs.add(t("settings.tab.about"))

        self._build_credentials_tab(self.tabs.tab(t("settings.tab.credentials")))
        self._build_general_tab(self.tabs.tab(t("settings.tab.general")))
        self._build_about_tab(self.tabs.tab(t("settings.tab.about")))

    # ── Credentials tab ──
    def _build_credentials_tab(self, parent):
        # Sub-tabview: Form (default) | Paste
        sub = ctk.CTkTabview(parent)
        sub.pack(fill="both", expand=True, padx=5, pady=5)
        sub.add(t("settings.cred.mode.form"))
        sub.add(t("settings.cred.mode.paste"))

        self._build_form_mode(sub.tab(t("settings.cred.mode.form")))
        self._build_paste_mode(sub.tab(t("settings.cred.mode.paste")))

        # Saved-credentials list at the bottom (shared across both sub-tabs)
        self._build_credentials_list(parent)

    def _build_paste_mode(self, parent):
        ctk.CTkLabel(
            parent, text=t("settings.cred.paste.help"),
            anchor="w", justify="left",
            text_color=("gray30", "gray70"),
        ).pack(fill="x", padx=10, pady=(10, 5))

        self.paste_text = ctk.CTkTextbox(parent, height=160, font=ctk.CTkFont(family="Consolas", size=12))
        self.paste_text.pack(fill="both", expand=True, padx=10, pady=5)

        IconButton(
            parent, text=t("settings.cred.paste.button"),
            command=self._on_paste_save,
        ).pack(anchor="e", padx=10, pady=(0, 10))

    def _build_form_mode(self, parent):
        ctk.CTkLabel(
            parent, text=t("settings.cred.form.help"),
            anchor="w", justify="left",
            text_color=("gray30", "gray70"),
        ).pack(fill="x", padx=10, pady=(10, 5))

        wrap = ctk.CTkFrame(parent, fg_color="transparent")
        wrap.pack(fill="x", padx=10, pady=5)

        self.form_country = ctk.CTkOptionMenu(wrap, values=[label for _, label in COUNTRIES])
        self.form_env = ctk.CTkOptionMenu(wrap, values=[label for _, label in ENVS])
        self.form_user = ctk.CTkEntry(wrap, placeholder_text="user  or  user[schema]")
        self.form_password = ctk.CTkEntry(wrap, show="•")
        self.form_tns = ctk.CTkEntry(wrap, placeholder_text="e.g. CHILE_QA_19C")

        rows = [
            (t("settings.cred.form.country"), self.form_country),
            (t("settings.cred.form.env"), self.form_env),
            (t("settings.cred.form.user"), self.form_user),
            (t("settings.cred.form.password"), self.form_password),
            (t("settings.cred.form.tns"), self.form_tns),
        ]
        for i, (label, widget) in enumerate(rows):
            ctk.CTkLabel(wrap, text=label, anchor="w", width=180).grid(row=i, column=0, padx=5, pady=4, sticky="w")
            widget.grid(row=i, column=1, padx=5, pady=4, sticky="ew")
        wrap.grid_columnconfigure(1, weight=1)

        IconButton(
            parent, text=t("settings.cred.form.save"),
            command=self._on_form_save,
        ).pack(anchor="e", padx=10, pady=10)

    def _build_credentials_list(self, parent):
        wrap = ctk.CTkFrame(parent)
        wrap.pack(fill="both", expand=True, padx=5, pady=(10, 5))

        SectionLabel(wrap, text=t("settings.cred.list.title")).pack(fill="x", padx=10, pady=(8, 4))

        self.creds_list_frame = ctk.CTkScrollableFrame(wrap, height=320)
        self.creds_list_frame.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self._render_credentials_list()

    def _render_credentials_list(self):
        for w in self.creds_list_frame.winfo_children():
            w.destroy()

        all_creds = self.app.config.all_credentials()

        # 2x2 grid of country tiles
        grid = ctk.CTkFrame(self.creds_list_frame, fg_color="transparent")
        grid.pack(fill="both", expand=True, padx=5, pady=5)
        grid.grid_columnconfigure(0, weight=1)
        grid.grid_columnconfigure(1, weight=1)

        for idx, (country, country_label) in enumerate(COUNTRIES):
            n = sum(len(v) for v in all_creds.get(country, {}).values())
            count_key = "settings.cred.list.country_count_one" if n == 1 else "settings.cred.list.country_count_many"
            tile_text = f"{country_label}\n{t(count_key, n=n)}"
            r, c = divmod(idx, 2)
            ctk.CTkButton(
                grid, text=tile_text, height=64,
                font=ctk.CTkFont(size=14, weight="bold"),
                anchor="center",
                command=lambda cid=country, lbl=country_label: self._open_country_dialog(cid, lbl),
            ).grid(row=r, column=c, padx=6, pady=6, sticky="nsew")

    def _open_country_dialog(self, country: str, country_label: str):
        CountryCredentialsDialog(
            self, self.app,
            country=country, country_label=country_label,
            on_changed=self._render_credentials_list,
        )

    def _on_paste_save(self):
        text = self.paste_text.get("1.0", "end").strip()
        if not text:
            return
        parsed, unparsed = parse_many(text)
        if not parsed:
            messagebox.showinfo(t("common.info"), t("settings.cred.paste.empty"))
            return
        summary = "\n".join(
            f"  • {c.country} / {c.bucket} :  {c.user}@{c.tns}"
            for c in parsed
        )
        msg = t("settings.cred.paste.summary", n=len(parsed)) + "\n\n" + summary
        if unparsed:
            msg += "\n\n" + t("settings.cred.paste.invalid", n=len(unparsed)) + "\n"
            msg += "\n".join(f"  • {l}" for l in unparsed[:10])
        msg += "\n\n" + t("settings.cred.paste.confirm")
        if not messagebox.askyesno(t("settings.title"), msg):
            return
        for c in parsed:
            self.app.config.set_credential(c.country, c.tns, credential_dict(c))
        self.paste_text.delete("1.0", "end")
        self._render_credentials_list()
        log.info("Saved %d credentials via paste", len(parsed))

    def _on_form_save(self):
        country_label = self.form_country.get()
        env_label = self.form_env.get()
        country = next((cid for cid, lbl in COUNTRIES if lbl == country_label), None)
        bucket = next((bid for bid, lbl in ENVS if lbl == env_label), None)
        user = self.form_user.get().strip()        # may include `[schema]` inline
        password = self.form_password.get()
        tns = self.form_tns.get().strip()
        if not (country and bucket and user and password and tns):
            messagebox.showerror(t("common.error"), t("settings.cred.form.required"))
            return
        # Build a synthetic line and reuse the parser. The user field is allowed to
        # contain `[schema]` inline, the parser will pull it out.
        synthetic = f"{user}/{password}@{tns}"
        c = parse(synthetic)
        if c is None:
            messagebox.showerror(t("common.error"), t("settings.cred.form.invalid"))
            return
        # Override inferred country/bucket with what the user picked.
        self.app.config.set_credential(country, tns, credential_dict(c, bucket=bucket, tns=tns))
        for w in (self.form_user, self.form_password, self.form_tns):
            w.delete(0, "end")
        self._render_credentials_list()
        messagebox.showinfo(t("common.info"), t("settings.cred.saved"))

    # ── General tab ──
    def _build_general_tab(self, parent):
        wrap = ctk.CTkFrame(parent, fg_color="transparent")
        wrap.pack(fill="x", padx=10, pady=10)

        # Language
        SectionLabel(wrap, text=t("settings.general.language")).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 4))
        self.lang_var = ctk.StringVar(value=self.app.config.get("language", "en"))
        ctk.CTkRadioButton(
            wrap, text=t("settings.general.lang.en"), variable=self.lang_var, value="en",
            command=self._on_language,
        ).grid(row=1, column=0, sticky="w", padx=20, pady=2)
        ctk.CTkRadioButton(
            wrap, text=t("settings.general.lang.es"), variable=self.lang_var, value="es",
            command=self._on_language,
        ).grid(row=1, column=1, sticky="w", padx=20, pady=2)

        # Theme
        SectionLabel(wrap, text=t("settings.general.theme")).grid(row=2, column=0, columnspan=3, sticky="w", pady=(15, 4))
        self.theme_var = ctk.StringVar(value=self.app.config.get("theme", "light"))
        ctk.CTkRadioButton(
            wrap, text=t("settings.general.theme.light"), variable=self.theme_var, value="light",
            command=self._on_theme,
        ).grid(row=3, column=0, sticky="w", padx=20, pady=2)
        ctk.CTkRadioButton(
            wrap, text=t("settings.general.theme.dark"), variable=self.theme_var, value="dark",
            command=self._on_theme,
        ).grid(row=3, column=1, sticky="w", padx=20, pady=2)

        # SQLcl
        SectionLabel(wrap, text=t("settings.general.sqlcl")).grid(row=4, column=0, columnspan=3, sticky="w", pady=(15, 4))
        self.sqlcl_entry = ctk.CTkEntry(wrap)
        self.sqlcl_entry.insert(0, self.app.config.get("sqlcl_path") or "")
        self.sqlcl_entry.grid(row=5, column=0, columnspan=2, sticky="ew", padx=20, pady=2)
        ctk.CTkButton(
            wrap, text=t("settings.general.browse"), width=100,
            command=self._on_browse_sqlcl,
        ).grid(row=5, column=2, padx=5, pady=2)
        ctk.CTkButton(
            wrap, text=t("settings.general.detect"), width=100,
            command=self._on_detect_sqlcl,
        ).grid(row=6, column=2, padx=5, pady=2)

        # Test connection
        SectionLabel(wrap, text=t("settings.general.test_section")).grid(
            row=7, column=0, columnspan=3, sticky="w", pady=(15, 4),
        )
        self._test_options = self._collect_test_options()
        values = [opt[0] for opt in self._test_options] or [t("settings.general.test_no_creds")]
        self.test_db_select = ctk.CTkOptionMenu(wrap, values=values)
        self.test_db_select.grid(row=8, column=0, columnspan=2, sticky="ew", padx=20, pady=2)
        ctk.CTkButton(
            wrap, text=t("settings.general.test"), width=100,
            command=self._on_test_connection,
        ).grid(row=8, column=2, padx=5, pady=2)
        self.test_status_label = ctk.CTkLabel(
            wrap, text="", anchor="w", justify="left",
            font=ctk.CTkFont(family="Consolas", size=11),
        )
        self.test_status_label.grid(row=9, column=0, columnspan=3, sticky="ew", padx=20, pady=(2, 4))

        wrap.grid_columnconfigure(0, weight=1)
        wrap.grid_columnconfigure(1, weight=1)

        # Apply button
        IconButton(
            parent, text=t("settings.general.apply"),
            command=self._on_apply_general,
        ).pack(anchor="e", padx=10, pady=15)

    def _collect_test_options(self) -> list[tuple[str, str, str, str]]:
        """Flatten saved creds into (display, country, db_name, cred_key) tuples."""
        out: list[tuple[str, str, str, str]] = []
        all_creds = self.app.config.all_credentials()
        for country, country_label in COUNTRIES:
            for db_name, by_login in all_creds.get(country, {}).items():
                for cred_key in by_login:
                    display = f"{country_label} · {db_name} · {cred_key}"
                    out.append((display, country, db_name, cred_key))
        return out

    def _on_test_connection(self):
        sel = self.test_db_select.get()
        opt = next((o for o in self._test_options if o[0] == sel), None)
        if not opt:
            self._show_test_result(False, t("settings.general.test_no_creds"))
            return
        sqlcl_path = (self.sqlcl_entry.get() or "").strip()
        if not sqlcl_path or not os.path.exists(sqlcl_path):
            self._show_test_result(False, t("settings.general.test_no_sqlcl"))
            return
        _, country, db_name, cred_key = opt
        cred = self.app.config.get_credential(country, db_name, cred_key)
        if not cred:
            self._show_test_result(False, t("settings.general.test_no_creds"))
            return

        self.test_status_label.configure(
            text=t("settings.general.test_running"),
            text_color=("gray35", "gray70"),
        )

        import threading
        threading.Thread(
            target=self._do_test_connection,
            args=(sqlcl_path, cred, db_name),
            daemon=True,
        ).start()

    def _do_test_connection(self, sqlcl_path: str, cred: dict, db_name: str):
        from spools_accounts.sqlcl import SqlclRunner
        from settings.credentials import to_sqlcl_arg

        password = decrypt_password(cred.get("password_enc", ""))
        connection = to_sqlcl_arg(
            cred.get("user", ""),
            cred.get("schema") or None,
            password,
            cred.get("tns") or db_name,
        )
        result = SqlclRunner(sqlcl_path).run_query(connection, "select 1 from dual")

        if result.ok and "1" in result.stdout:
            msg = t("settings.general.test_ok")
            ok = True
        else:
            ok = False
            err_line = (result.stderr or result.stdout or "").strip().splitlines()
            tail = err_line[-1] if err_line else ""
            msg = t("settings.general.test_fail", code=result.exit_code)
            if tail:
                msg += f"\n{tail[:240]}"

        log.info("Test connection result: ok=%s code=%s db=%s user=%s",
                 ok, result.exit_code, db_name, cred.get("user"))
        self.after(0, lambda: self._show_test_result(ok, msg))

    def _show_test_result(self, ok: bool, msg: str):
        color = ("#1A7F37", "#3FB950") if ok else ("#CF222E", "#FF6B6B")
        self.test_status_label.configure(text=msg, text_color=color)

    def _on_language(self):
        self.app.apply_language(self.lang_var.get())

    def _on_theme(self):
        self.app.apply_theme(self.theme_var.get())

    def _on_browse_sqlcl(self):
        f = filedialog.askopenfilename(
            title="Select sql.exe",
            filetypes=[("SQLcl executable", "sql.exe"), ("All files", "*.*")],
        )
        if f:
            self.sqlcl_entry.delete(0, "end")
            self.sqlcl_entry.insert(0, f)

    def _on_detect_sqlcl(self):
        from spools_accounts.sqlcl_locator import locate_sqlcl
        found = locate_sqlcl()
        if found:
            self.sqlcl_entry.delete(0, "end")
            self.sqlcl_entry.insert(0, found)
            messagebox.showinfo(t("common.info"), f"SQLcl: {found}")
        else:
            messagebox.showwarning(t("common.warning"), t("settings.general.sqlcl_not_found"))

    def _on_apply_general(self):
        self.app.config.set("sqlcl_path", self.sqlcl_entry.get().strip())
        messagebox.showinfo(t("common.info"), t("settings.cred.saved"))

    # ── About tab ──
    def _build_about_tab(self, parent):
        wrap = ctk.CTkFrame(parent, fg_color="transparent")
        wrap.pack(fill="both", expand=True, padx=20, pady=20)
        ctk.CTkLabel(wrap, text=t("app.title"), font=ctk.CTkFont(size=20, weight="bold")).pack(anchor="w", pady=(0, 10))
        ctk.CTkLabel(wrap, text=f"{t('settings.about.version')}: {__version__}", anchor="w").pack(anchor="w", pady=2)
        ctk.CTkLabel(wrap, text=f"{t('settings.about.repo')}: https://github.com/dpv20/oracle_tasks", anchor="w").pack(anchor="w", pady=2)
        ctk.CTkLabel(wrap, text=f"{t('settings.about.creator')}: Diego Pavez Verdi", anchor="w").pack(anchor="w", pady=(15, 2))
        ctk.CTkLabel(wrap, text=f"{t('settings.about.email')}: diego.pavez@oracle.com", anchor="w").pack(anchor="w", pady=2)
        ctk.CTkLabel(wrap, text=f"{t('settings.about.phone')}: +569 95293023", anchor="w").pack(anchor="w", pady=2)


class CountryCredentialsDialog(ctk.CTkToplevel):
    """Popup listing all credentials for one country, grouped by env.

    Edit/Delete happen inside the popup. After any change, the dialog re-renders
    and `on_changed` is invoked so the parent (tile grid) refreshes its counts.
    """

    def __init__(self, master, app, *, country: str, country_label: str, on_changed):
        super().__init__(master)
        self.app = app
        self.country = country
        self.country_label = country_label
        self.on_changed = on_changed

        self.title(t("settings.cred.list.country_dialog_title", country=country_label))
        self.transient(master.winfo_toplevel())
        self.grab_set()
        self.geometry("620x520")

        # Header
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.pack(fill="x", padx=20, pady=(15, 4))
        ctk.CTkLabel(
            head, text=country_label,
            font=ctk.CTkFont(size=18, weight="bold"),
            anchor="w",
        ).pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(
            self, text=t("settings.cred.list.country_dialog_hint"),
            text_color=("gray45", "gray60"), anchor="w", justify="left",
            wraplength=560,
        ).pack(fill="x", padx=20, pady=(0, 8))

        self.body = ctk.CTkScrollableFrame(self)
        self.body.pack(fill="both", expand=True, padx=15, pady=(0, 10))

        ctk.CTkButton(
            self, text=t("common.close"), width=110,
            command=self.destroy,
        ).pack(anchor="e", padx=20, pady=(0, 15))

        self._render()
        self.after(50, self._center_on_screen)

    def _center_on_screen(self):
        self.update_idletasks()
        w, h = self.winfo_width(), self.winfo_height()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        x = max(0, (sw - w) // 2)
        y = max(0, (sh - h) // 2)
        self.geometry(f"+{x}+{y}")

    def _render(self):
        for w in self.body.winfo_children():
            w.destroy()

        by_db = self.app.config.all_credentials().get(self.country, {})
        by_bucket: dict[str, list[tuple[str, str, dict]]] = {}
        for db_name, by_login in by_db.items():
            for cred_key, cred in by_login.items():
                bucket = cred.get("bucket") or "_unknown"
                by_bucket.setdefault(bucket, []).append((db_name, cred_key, cred))

        if not any(by_bucket.values()):
            ctk.CTkLabel(
                self.body, text=t("settings.cred.list.country_empty"),
                text_color=("gray45", "gray55"),
            ).pack(anchor="w", padx=5, pady=8)
            return

        for bucket, env_label in ENV_DISPLAY:
            creds_in_bucket = by_bucket.get(bucket, [])
            if not creds_in_bucket:
                continue
            ctk.CTkLabel(
                self.body, text=env_label,
                font=ctk.CTkFont(size=12, weight="bold"),
                text_color=("gray25", "gray80"), anchor="w",
            ).pack(fill="x", padx=4, pady=(8, 2))
            for db_name, cred_key, cred in sorted(
                creds_in_bucket, key=lambda x: (x[0].upper(), x[1])
            ):
                self._row(db_name, cred_key, cred)

        unknown = by_bucket.get("_unknown", [])
        if unknown:
            ctk.CTkLabel(
                self.body, text="?",
                font=ctk.CTkFont(size=12, weight="bold"),
                text_color=("gray45", "gray55"), anchor="w",
            ).pack(fill="x", padx=4, pady=(8, 2))
            for db_name, cred_key, cred in unknown:
                self._row(db_name, cred_key, cred)

    def _row(self, db_name: str, cred_key: str, cred: dict):
        row = ctk.CTkFrame(self.body, fg_color=("gray92", "gray18"), corner_radius=4)
        row.pack(fill="x", padx=10, pady=2)
        user = cred.get("user", "")
        schema = cred.get("schema", "")
        login = f"{user}[{schema}]" if schema else user
        label = f"{login} @ {cred.get('tns', db_name)}"
        ctk.CTkLabel(
            row, text=label, anchor="w",
            font=ctk.CTkFont(family="Consolas", size=11),
        ).pack(side="left", fill="x", expand=True, padx=8, pady=4)
        ctk.CTkButton(
            row, text=t("settings.cred.list.delete"), width=66, height=24,
            fg_color=("#D9534F", "#A8322C"), hover_color=("#C9302C", "#8B1F1A"),
            text_color="white",
            command=lambda d=db_name, k=cred_key: self._on_delete(d, k),
        ).pack(side="right", padx=(2, 6), pady=4)
        ctk.CTkButton(
            row, text=t("settings.cred.list.edit"), width=66, height=24,
            fg_color=("#1F6FEB", "#1A5BBF"), hover_color=("#1856B8", "#13428F"),
            text_color="white",
            command=lambda d=db_name, k=cred_key: self._on_edit(d, k),
        ).pack(side="right", padx=2, pady=4)

    def _on_edit(self, db_name: str, cred_key: str):
        cred = self.app.config.get_credential(self.country, db_name, cred_key)
        if not cred:
            return
        CredentialEditDialog(
            self, self.app,
            country=self.country, db_name=db_name, cred_key=cred_key, cred=cred,
            on_saved=self._after_change,
        )

    def _on_delete(self, db_name: str, cred_key: str):
        if not messagebox.askyesno(
            t("common.warning"),
            t("settings.cred.edit.delete_confirm"),
            parent=self,
        ):
            return
        self.app.config.delete_credential(self.country, db_name, cred_key)
        log.info("Deleted credential: %s/%s/%s", self.country, db_name, cred_key)
        self._after_change()

    def _after_change(self):
        self._render()
        if callable(self.on_changed):
            self.on_changed()


class CredentialEditDialog(ctk.CTkToplevel):
    """Modal dialog to edit a stored credential.

    On save: deletes the credential at its original (country, db_name, cred_key)
    location and re-inserts it under the (possibly new) values. This covers
    user/schema/tns/country/bucket changes uniformly.
    """

    def __init__(self, master, app, *, country, db_name, cred_key, cred, on_saved):
        super().__init__(master)
        self.app = app
        self.on_saved = on_saved
        self._orig = (country, db_name, cred_key)

        self.title(t("settings.cred.edit.title"))
        self.transient(master.winfo_toplevel())
        self.resizable(False, False)
        self.grab_set()

        # Pre-fill from cred
        user = cred.get("user", "")
        schema = cred.get("schema", "")
        login = f"{user}[{schema}]" if schema else user
        password = decrypt_password(cred.get("password_enc", ""))
        tns = cred.get("tns", db_name)
        bucket = cred.get("bucket", "") or "shared_prod"

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=20, pady=15)

        # Country
        self.country_var = ctk.StringVar(
            value=next((lbl for cid, lbl in COUNTRIES if cid == country), country.title())
        )
        ctk.CTkOptionMenu(
            body, variable=self.country_var, values=[lbl for _, lbl in COUNTRIES],
        ).grid(row=0, column=1, padx=5, pady=4, sticky="ew")

        # Env / bucket
        self.env_var = ctk.StringVar(
            value=next((lbl for bid, lbl in ENVS if bid == bucket), ENVS[0][1])
        )
        ctk.CTkOptionMenu(
            body, variable=self.env_var, values=[lbl for _, lbl in ENVS],
        ).grid(row=1, column=1, padx=5, pady=4, sticky="ew")

        # User (with optional [schema])
        self.user_entry = ctk.CTkEntry(body, placeholder_text="user  or  user[schema]")
        self.user_entry.insert(0, login)
        self.user_entry.grid(row=2, column=1, padx=5, pady=4, sticky="ew")

        # Password
        self.password_entry = ctk.CTkEntry(body, show="•")
        self.password_entry.insert(0, password)
        self.password_entry.grid(row=3, column=1, padx=5, pady=4, sticky="ew")

        # TNS
        self.tns_entry = ctk.CTkEntry(body)
        self.tns_entry.insert(0, tns)
        self.tns_entry.grid(row=4, column=1, padx=5, pady=4, sticky="ew")

        labels = [
            t("settings.cred.form.country"),
            t("settings.cred.form.env"),
            t("settings.cred.form.user"),
            t("settings.cred.form.password"),
            t("settings.cred.form.tns"),
        ]
        for i, lbl in enumerate(labels):
            ctk.CTkLabel(body, text=lbl, anchor="w", width=140).grid(
                row=i, column=0, padx=5, pady=4, sticky="w"
            )
        body.grid_columnconfigure(1, weight=1)

        # Buttons
        btns = ctk.CTkFrame(self, fg_color="transparent")
        btns.pack(fill="x", padx=20, pady=(0, 15))
        ctk.CTkButton(
            btns, text=t("settings.cred.edit.cancel"), width=110,
            fg_color="transparent", border_width=1, hover_color=("gray80", "gray20"),
            command=self.destroy,
        ).pack(side="right", padx=4)
        ctk.CTkButton(
            btns, text=t("settings.cred.edit.save"), width=140,
            command=self._on_save,
        ).pack(side="right", padx=4)

        self.after(50, self._center_on_screen)

    def _center_on_screen(self):
        self.update_idletasks()
        w, h = self.winfo_width(), self.winfo_height()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        x = max(0, (sw - w) // 2)
        y = max(0, (sh - h) // 2)
        self.geometry(f"+{x}+{y}")

    def _on_save(self):
        country_label = self.country_var.get()
        env_label = self.env_var.get()
        country = next((cid for cid, lbl in COUNTRIES if lbl == country_label), None)
        bucket = next((bid for bid, lbl in ENVS if lbl == env_label), None)
        user_field = self.user_entry.get().strip()
        password = self.password_entry.get()
        tns = self.tns_entry.get().strip()

        if not (country and bucket and user_field and password and tns):
            messagebox.showerror(t("common.error"), t("settings.cred.form.required"), parent=self)
            return

        c = parse(f"{user_field}/{password}@{tns}")
        if c is None:
            messagebox.showerror(t("common.error"), t("settings.cred.form.invalid"), parent=self)
            return

        orig_country, orig_db, orig_key = self._orig
        # Always remove the original entry first, then re-insert with new values.
        # Covers country/db/login/bucket/password changes uniformly.
        self.app.config.delete_credential(orig_country, orig_db, orig_key)
        new_cred = {
            "user": c.user,
            "schema": c.schema or "",
            "password_enc": encrypt_password(c.password),
            "tns": tns,
            "bucket": bucket,
        }
        self.app.config.set_credential(country, tns, new_cred)
        log.info(
            "Edited credential: %s/%s/%s -> %s/%s/%s",
            orig_country, orig_db, orig_key, country, tns, c.user,
        )
        if callable(self.on_saved):
            self.on_saved()
        self.destroy()
