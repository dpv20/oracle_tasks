"""FBBatchSetup view: run Java batch reports and produce PDFs."""
from __future__ import annotations

import os
import logging
import threading
import calendar
from datetime import date, datetime, timedelta
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

from fbbatch.runner import (
    BatchResult,
    ISSUE_FIELDS,
    check_falabella_vpn,
    create_outlook_draft,
    delete_issue_template,
    default_mail_body,
    find_event_pdf_for_report_date,
    issues_for_date,
    load_saved_issues,
    render_mail_template,
    report_indicates_chile_batch_skipped,
    report_date_to_issue_date,
    run_batch_report,
    run_eod_batch_event,
    save_issue_template,
    validate_fbbatch_root,
    write_issue_properties,
)
from i18n import t

from .widgets import CardFrame, IconButton, SectionLabel


log = logging.getLogger(__name__)
ENVIRONMENTS = ("PROD", "QA", "DEV")
COUNTRIES = ("CHILE", "PERU", "COLOMBIA", "MEXICO")
TEXT_FIELDS = {"ISSUE_DETAILS", "ACTION_TAKEN", "FURTHER_ACTION_REQUIRED"}
TIME_FIELDS = {
    "TIME_REPORTED",
    "CALL_START_TIME",
    "CALL_END_TIME",
    "SOLUTION_PROVIDED_TIME",
    "PROCESS_END_TIME",
}
MONTH_ABBR = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")
DEFAULT_MAIL_TO = (
    '"Michell Zambrano" <michell.zambrano@oracle.com>; '
    '"Adarsh Kumar" <adarsh.kumar@oracle.com>; '
    '"Jackeline R Diaz Junco" <jroxadiazj@falabella.cl>; '
    '"Batch Support Flex FIF" <batchsoporteflexfif@falabella.cl>; '
    '"Ricardo Campos Barraza" <riccamposb@falabella.cl>; '
    '"Marco Aurelio Luna" <maluna@falabella.cl>; '
    '"aechacinm@Falabella.cl" <aechacinm@Falabella.cl>'
)
DEFAULT_MAIL_CC = (
    '"KANNAN MUTHUSAMY" <kannan.m@oracle.com>; '
    '"Sharath Pattabiraman" <sharath.pattabiraman@oracle.com>; '
    '"Ashwin M" <ashwin.m@oracle.com>; '
    '"Diego Pavez" <diego.pavez@oracle.com>'
)
EXAMPLE_ISSUE = {
    "DATE": "01-APR-26",
    "COUNTRY": "COLOMBIA",
    "TYPE_OF_FAILURE": "ERROR AL ABRIR FUNCION PARA CARGAR TASA DE USURA",
    "PROCESS_AFFECTED": "NOT SPECIFIED",
    "TIME_REPORTED": "09:10 PM",
    "CALL_START_TIME": "12:05 AM",
    "CALL_END_TIME": "02:30 AM",
    "ADDITIONAL_SUPPORT": "NO",
    "ESCALATED": "NO",
    "ISSUE_DETAILS": "Cliene no puede ingresar a la funcion CLDUSRT para cargar tasas de Usura mes de abril",
    "ACTION_TAKEN": "Script para insertar las tasas manuales enviado, Revision de antecedentes y evidencias, pantalla no esta cargando por posible error en proceso previo de reinicio de BD",
    "SOLUTION_PROVIDED_TIME": "12:10am",
    "FURTHER_ACTION_REQUIRED": "Se necesita revisar porque no esta cargando la pantalla, error persiste en front de FC",
    "PROCESS_END_TIME": "02:30 AM",
}


def _issue_labels() -> dict[str, str]:
    return {
        "DATE": t("fbbatch.issue.date"),
        "COUNTRY": t("fbbatch.issue.country"),
        "TYPE_OF_FAILURE": t("fbbatch.issue.failure"),
        "PROCESS_AFFECTED": t("fbbatch.issue.process"),
        "TIME_REPORTED": t("fbbatch.issue.reported"),
        "CALL_START_TIME": t("fbbatch.issue.call_start"),
        "CALL_END_TIME": t("fbbatch.issue.call_end"),
        "ADDITIONAL_SUPPORT": t("fbbatch.issue.support"),
        "ESCALATED": t("fbbatch.issue.escalated"),
        "ISSUE_DETAILS": t("fbbatch.issue.details"),
        "ACTION_TAKEN": t("fbbatch.issue.actions"),
        "SOLUTION_PROVIDED_TIME": t("fbbatch.issue.solution_time"),
        "FURTHER_ACTION_REQUIRED": t("fbbatch.issue.further"),
        "PROCESS_END_TIME": t("fbbatch.issue.end_time"),
    }


class FBBatchSetupView(ctk.CTkFrame):
    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self._running = False
        self._last_pdf: Path | None = None
        self._last_html: Path | None = None
        self._last_images_dir: Path | None = None
        self._event_pdf: Path | None = None
        self._event_html: Path | None = None
        self._report_html: Path | None = None
        self._report_images_dir: Path | None = None
        self._full_output_dir: Path | None = None
        self._event_output_dir: Path | None = None
        self._report_output_dir: Path | None = None
        self._active_progress = "event"

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(side="top", fill="x", padx=20, pady=(20, 10))
        IconButton(
            header, text=f"< {t('common.back')}", width=100,
            command=lambda: app.show_view("home"),
        ).pack(side="left")
        ctk.CTkLabel(
            header, text="☾  " + t("fbbatch.title"),
            font=ctk.CTkFont(size=18, weight="bold"),
        ).pack(side="left", padx=15)

        self.body = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.body.pack(fill="both", expand=True, padx=20, pady=(0, 20))

        self.status_label = ctk.CTkLabel(
            self.body,
            text="",
            anchor="w",
            justify="left",
            font=ctk.CTkFont(family="Consolas", size=12),
            text_color=("gray35", "gray70"),
        )
        self.status_label.pack(fill="x")

        self._build_generate_report_card()
        self._build_event_card()
        self._build_report_card()

    def _build_generate_report_card(self) -> None:
        card = CardFrame(self.body)
        card.pack(fill="x", pady=(0, 12))
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=18, pady=16)
        SectionLabel(inner, text=t("fbbatch.full.title")).grid(row=0, column=0, columnspan=5, sticky="w")
        ctk.CTkLabel(
            inner,
            text=t("fbbatch.full.desc"),
            anchor="w",
            justify="left",
            text_color=("gray40", "gray65"),
        ).grid(row=1, column=0, columnspan=5, sticky="ew", pady=(4, 10))
        ctk.CTkLabel(inner, text=t("fbbatch.env"), width=120, anchor="w").grid(row=2, column=0, sticky="w", pady=4)
        self.full_env = ctk.CTkOptionMenu(inner, values=list(ENVIRONMENTS))
        self.full_env.grid(row=2, column=1, sticky="ew", padx=8, pady=4)
        self.full_latest_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(inner, text=t("fbbatch.report.latest"), variable=self.full_latest_var, command=self._sync_full_date_state).grid(
            row=2, column=2, sticky="w", padx=8, pady=4
        )
        self._full_selected_date = date.today() - timedelta(days=1)
        full_date_frame = ctk.CTkFrame(inner, fg_color="transparent")
        full_date_frame.grid(row=2, column=3, sticky="ew", padx=8, pady=4)
        full_date_frame.grid_columnconfigure(0, weight=1)
        self.full_date = ctk.CTkEntry(full_date_frame)
        self.full_date.insert(0, _format_issue_date(self._full_selected_date))
        self.full_date.configure(state="disabled")
        self.full_date.grid(row=0, column=0, sticky="ew")
        self.full_calendar_btn = ctk.CTkButton(
            full_date_frame,
            text=t("fbbatch.calendar"),
            width=105,
            command=self._open_full_calendar,
        )
        self.full_calendar_btn.grid(row=0, column=1, padx=(8, 0))
        IconButton(inner, text=t("fbbatch.full.run"), width=180, command=self._on_generate_full_report).grid(
            row=2, column=4, sticky="e", padx=8, pady=4
        )
        mail_row = ctk.CTkFrame(inner, fg_color="transparent")
        mail_row.grid(row=3, column=0, columnspan=5, sticky="ew", pady=(14, 0))
        mail_row.grid_columnconfigure(0, weight=1)
        self.mail_summary = ctk.CTkLabel(
            mail_row,
            text="",
            anchor="w",
            justify="left",
            text_color=("gray40", "gray65"),
        )
        self.mail_summary.grid(row=0, column=0, sticky="ew")
        IconButton(
            mail_row,
            text=t("fbbatch.mail.edit"),
            width=150,
            command=self._open_mail_settings,
        ).grid(row=0, column=1, padx=(12, 0))
        self.full_output_row = ctk.CTkFrame(inner, fg_color="transparent")
        self.full_output_row.grid(row=7, column=0, columnspan=5, sticky="e", pady=(10, 0))
        self.full_open_location_btn = ctk.CTkButton(
            self.full_output_row,
            text=t("fbbatch.open_location"),
            width=170,
            state="disabled",
            command=lambda: self._open_path(self._full_output_dir),
        )
        self.full_open_location_btn.pack(side="right", padx=5)
        self.full_progress_bar = ctk.CTkProgressBar(inner)
        self.full_progress_bar.set(0)
        self.full_progress_bar.grid(row=5, column=0, columnspan=5, sticky="ew", pady=(14, 2))
        self.full_progress_label = ctk.CTkLabel(inner, text="", anchor="w", text_color=("gray40", "gray65"), font=ctk.CTkFont(size=11))
        self.full_progress_label.grid(row=6, column=0, columnspan=5, sticky="ew")
        self.full_progress_bar.grid_remove()
        self.full_progress_label.grid_remove()
        inner.grid_columnconfigure(1, weight=1)
        inner.grid_columnconfigure(3, weight=1)
        self._sync_full_date_state()
        self._refresh_mail_summary()

    def _build_event_card(self) -> None:
        card = CardFrame(self.body)
        card.pack(fill="x", pady=(0, 12))
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=18, pady=16)
        SectionLabel(inner, text=t("fbbatch.event.title")).grid(row=0, column=0, columnspan=3, sticky="w")
        ctk.CTkLabel(
            inner,
            text=t("fbbatch.event.desc"),
            anchor="w",
            justify="left",
            text_color=("gray40", "gray65"),
        ).grid(row=1, column=0, columnspan=3, sticky="ew", pady=(4, 10))

        ctk.CTkLabel(inner, text=t("fbbatch.env"), width=120, anchor="w").grid(row=2, column=0, sticky="w", pady=4)
        self.event_env = ctk.CTkOptionMenu(inner, values=list(ENVIRONMENTS))
        self.event_env.grid(row=2, column=1, sticky="ew", padx=8, pady=4)
        IconButton(
            inner,
            text=t("fbbatch.event.run"),
            width=180,
            command=self._on_run_event,
        ).grid(row=2, column=2, padx=8, pady=4)
        self.event_progress_bar = ctk.CTkProgressBar(inner)
        self.event_progress_bar.set(0)
        self.event_progress_bar.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(12, 2))
        self.event_progress_label = ctk.CTkLabel(
            inner,
            text="",
            anchor="w",
            justify="left",
            text_color=("gray40", "gray65"),
            font=ctk.CTkFont(size=11),
        )
        self.event_progress_label.grid(row=4, column=0, columnspan=3, sticky="ew")
        self.event_output_row = ctk.CTkFrame(inner, fg_color="transparent")
        self.event_output_row.grid(row=5, column=0, columnspan=3, sticky="e", pady=(10, 0))
        self.event_open_location_btn = ctk.CTkButton(
            self.event_output_row,
            text=t("fbbatch.open_location"),
            width=170,
            state="disabled",
            command=lambda: self._open_path(self._event_output_dir),
        )
        self.event_open_location_btn.pack(side="right", padx=5)
        self._hide_progress("event")
        inner.grid_columnconfigure(1, weight=1)

    def _build_report_card(self) -> None:
        card = CardFrame(self.body)
        card.pack(fill="x", pady=(0, 12))
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=18, pady=16)
        SectionLabel(inner, text=t("fbbatch.report.title")).grid(row=0, column=0, columnspan=4, sticky="w")
        ctk.CTkLabel(
            inner,
            text=t("fbbatch.report.desc"),
            anchor="w",
            justify="left",
            text_color=("gray40", "gray65"),
        ).grid(row=1, column=0, columnspan=4, sticky="ew", pady=(4, 10))

        ctk.CTkLabel(inner, text=t("fbbatch.env"), width=120, anchor="w").grid(row=2, column=0, sticky="w", pady=4)
        self.report_env = ctk.CTkOptionMenu(inner, values=list(ENVIRONMENTS))
        self.report_env.grid(row=2, column=1, sticky="ew", padx=8, pady=4)

        self.latest_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            inner,
            text=t("fbbatch.report.latest"),
            variable=self.latest_var,
            command=self._sync_report_date_state,
        ).grid(row=2, column=2, sticky="w", padx=8, pady=4)

        self._report_selected_date = date.today() - timedelta(days=1)
        report_date_frame = ctk.CTkFrame(inner, fg_color="transparent")
        report_date_frame.grid(row=2, column=3, sticky="ew", padx=8, pady=4)
        report_date_frame.grid_columnconfigure(0, weight=1)
        self.report_date = ctk.CTkEntry(report_date_frame)
        self.report_date.insert(0, _format_issue_date(self._report_selected_date))
        self.report_date.configure(state="disabled")
        self.report_date.grid(row=0, column=0, sticky="ew")
        self.report_calendar_btn = ctk.CTkButton(
            report_date_frame,
            text=t("fbbatch.calendar"),
            width=105,
            command=self._open_report_calendar,
        )
        self.report_calendar_btn.grid(row=0, column=1, padx=(8, 0))

        self.report_issue_status = ctk.CTkLabel(
            inner,
            text="",
            anchor="w",
            justify="left",
            text_color=("gray40", "gray65"),
        )
        self.report_issue_status.grid(row=3, column=0, columnspan=4, sticky="ew", pady=(10, 0))

        issue_actions = ctk.CTkFrame(
            inner,
            fg_color="transparent",
        )
        issue_actions.grid(row=4, column=0, columnspan=2, sticky="w", pady=(16, 0))
        IconButton(
            issue_actions,
            text=t("fbbatch.issue.new"),
            width=160,
            command=self._new_issue,
        ).pack(side="left")
        IconButton(
            issue_actions,
            text=t("fbbatch.issue.view_saved"),
            width=160,
            command=self._view_issues,
        ).pack(side="left", padx=(8, 0))

        IconButton(
            inner,
            text=t("fbbatch.report.run"),
            width=180,
            command=self._on_run_report,
        ).grid(row=4, column=3, sticky="e", padx=8, pady=(26, 0))
        self.report_progress_bar = ctk.CTkProgressBar(inner)
        self.report_progress_bar.set(0)
        self.report_progress_bar.grid(row=5, column=0, columnspan=4, sticky="ew", pady=(14, 2))
        self.report_progress_label = ctk.CTkLabel(
            inner,
            text="",
            anchor="w",
            justify="left",
            text_color=("gray40", "gray65"),
            font=ctk.CTkFont(size=11),
        )
        self.report_progress_label.grid(row=6, column=0, columnspan=4, sticky="ew")
        self.report_output_row = ctk.CTkFrame(inner, fg_color="transparent")
        self.report_output_row.grid(row=7, column=0, columnspan=4, sticky="e", pady=(10, 0))
        self.report_open_location_btn = ctk.CTkButton(
            self.report_output_row,
            text=t("fbbatch.open_location"),
            width=170,
            state="disabled",
            command=lambda: self._open_path(self._report_output_dir),
        )
        self.report_open_location_btn.pack(side="right", padx=5)
        self._hide_progress("report")

        inner.grid_columnconfigure(1, weight=1)
        inner.grid_columnconfigure(3, weight=1)
        self._sync_report_date_state()
        self._sync_report_issue_status()

    def _mail_values_for_current_date(self) -> dict[str, str]:
        report_date = self._current_full_report_date()
        include_event = datetime.strptime(report_date, "%d%m%Y").date().weekday() not in (5, 6)
        from_account = (
            self.app.config.get("fbbatch_mail_from")
            or self.app.config.get("oracle_email")
            or ""
        ).strip()
        subject_template = self.app.config.get("fbbatch_mail_subject") or "NSSR : {MONTH_UPPER} {DAY} {YEAR}"
        body_template = self.app.config.get("fbbatch_mail_body") or ""
        if self._looks_like_saved_default_body(body_template):
            body_template = ""
        return {
            "subject_template": subject_template,
            "subject": render_mail_template(subject_template, report_date, include_event=include_event),
            "from_account": from_account,
            "to": self.app.config.get("fbbatch_mail_to") or DEFAULT_MAIL_TO,
            "cc": self.app.config.get("fbbatch_mail_cc") or DEFAULT_MAIL_CC,
            "body_template": body_template,
            "body": render_mail_template(body_template, report_date, include_event=include_event),
        }

    @staticmethod
    def _looks_like_saved_default_body(text: str) -> bool:
        clean = " ".join((text or "").split()).lower()
        if not clean or "{" in clean:
            return False
        return (
            clean.startswith("estimados,")
            and "a continuación, se presentan los tiempos de ejecución del batch del" in clean
        )

    def _refresh_mail_summary(self) -> None:
        values = self._mail_values_for_current_date()
        from_account = values["from_account"]
        summary = t(
            "fbbatch.mail.summary",
            subject=values["subject"],
            sender=from_account or t("fbbatch.mail.not_configured"),
        )
        if not from_account:
            summary += "\n⚠ " + t("fbbatch.mail.from_missing")
        self.mail_summary.configure(
            text=summary,
            text_color=("#a16207", "#fbbf24") if not from_account else ("gray40", "gray65"),
        )

    def _open_mail_settings(self) -> None:
        MailSettingsDialog(self, values=self._mail_values_for_current_date(), on_saved=self._save_mail_settings)

    def _save_mail_settings(self, values: dict[str, str]) -> None:
        self.app.config.set("fbbatch_mail_subject", values["subject_template"].strip() or "NSSR : {MONTH_UPPER} {DAY} {YEAR}")
        self.app.config.set("fbbatch_mail_from", values["from_account"].strip())
        if values["from_account"].strip():
            self.app.config.set("oracle_email", values["from_account"].strip())
        self.app.config.set("fbbatch_mail_to", values["to"].strip())
        self.app.config.set("fbbatch_mail_cc", values["cc"].strip())
        self.app.config.set("fbbatch_mail_body", values["body_template"].strip())
        self._refresh_mail_summary()

    def _new_issue(self) -> None:
        issue_date = self._current_report_issue_date()
        if issue_date is None:
            issue_date = (datetime.now() - timedelta(days=1)).strftime("%d-%b-%y").upper()
        IssueEditDialog(self, issue_date=issue_date, on_saved=self._sync_report_issue_status)

    def _view_issues(self) -> None:
        issue_date = self._current_report_issue_date()
        if issue_date is None:
            messagebox.showerror(t("common.error"), t("fbbatch.report.issue_date_invalid"), parent=self)
            return
        IssueListDialog(self, issue_date=issue_date, on_changed=self._sync_report_issue_status)

    def _open_full_calendar(self) -> None:
        CalendarDialog(self, selected=self._full_selected_date, on_pick=self._set_full_date)

    def _set_full_date(self, value: date) -> None:
        self._full_selected_date = value
        self.full_date.configure(state="normal")
        self.full_date.delete(0, "end")
        self.full_date.insert(0, _format_issue_date(value))
        self.full_date.configure(state="disabled")
        self._refresh_mail_summary()

    def _sync_full_date_state(self) -> None:
        if self.full_latest_var.get():
            self._full_selected_date = date.today() - timedelta(days=1)
            self.full_date.configure(state="normal")
            self.full_date.delete(0, "end")
            self.full_date.insert(0, _format_issue_date(self._full_selected_date))
            self.full_date.configure(state="disabled")
            self.full_calendar_btn.configure(state="disabled")
        else:
            self.full_calendar_btn.configure(state="normal")
        self._refresh_mail_summary()

    def _current_full_report_date(self) -> str:
        return self._full_selected_date.strftime("%d%m%Y")

    def _open_report_calendar(self) -> None:
        CalendarDialog(self, selected=self._report_selected_date, on_pick=self._set_report_date)

    def _set_report_date(self, value: date) -> None:
        self._report_selected_date = value
        self.report_date.configure(state="normal")
        self.report_date.delete(0, "end")
        self.report_date.insert(0, _format_issue_date(value))
        self.report_date.configure(state="disabled")
        self._sync_report_issue_status()

    def _sync_report_date_state(self) -> None:
        if self.latest_var.get():
            self._report_selected_date = date.today() - timedelta(days=1)
            self.report_date.configure(state="normal")
            self.report_date.delete(0, "end")
            self.report_date.insert(0, _format_issue_date(self._report_selected_date))
            self.report_date.configure(state="disabled")
            self.report_calendar_btn.configure(state="disabled")
        else:
            self.report_calendar_btn.configure(state="normal")
        self._sync_report_issue_status()

    def _current_report_issue_date(self) -> str | None:
        report_date = self._current_report_date()
        if not report_date:
            return None
        try:
            return report_date_to_issue_date(report_date)
        except ValueError:
            return None

    def _current_report_date(self) -> str | None:
        return self._report_selected_date.strftime("%d%m%Y")

    def _sync_report_issue_status(self) -> None:
        issue_date = self._current_report_issue_date()
        if not issue_date:
            self.report_issue_status.configure(text=t("fbbatch.report.issue_date_invalid"))
            return
        matches = issues_for_date(issue_date)
        self.report_issue_status.configure(
            text=t("fbbatch.report.issue_status", date=issue_date, n=len(matches))
        )

    def _on_generate_full_report(self) -> None:
        mail_values = self._mail_values_for_current_date()
        if not mail_values["from_account"]:
            messagebox.showwarning(
                t("fbbatch.mail.from_missing_title"),
                t("fbbatch.mail.from_missing"),
                parent=self,
            )
            self._open_mail_settings()
            return
        env = self.full_env.get()
        vpn_ok, vpn_message = check_falabella_vpn()
        if not vpn_ok:
            messagebox.showerror(t("fbbatch.vpn.required_title"), t("fbbatch.vpn.required", detail=vpn_message), parent=self)
            return
        if not self._confirm_prod(env):
            return
        root = self._ensure_fbbatch_root()
        if root is None:
            return
        report_date = self._current_full_report_date()
        issue_date = report_date_to_issue_date(report_date)
        issues = issues_for_date(issue_date)
        write_issue_properties(issues, root)

        subject_template = mail_values["subject_template"]
        from_account = mail_values["from_account"]
        to = mail_values["to"]
        cc = mail_values["cc"]
        body_template = mail_values["body_template"]

        latest = bool(self.full_latest_var.get())
        self._active_progress = "full"
        self._run_background(
            lambda progress: self._run_full_report(
                env=env,
                latest=latest,
                report_date=report_date,
                has_issue=bool(issues),
                root=root,
                subject_template=subject_template,
                from_account=from_account,
                to=to,
                cc=cc,
                body_template=body_template,
                credentials=self.app.config.all_credentials(),
                progress=progress,
            )
        )

    def _run_full_report(
        self,
        *,
        env: str,
        latest: bool,
        report_date: str,
        has_issue: bool,
        root: str,
        subject_template: str,
        from_account: str,
        to: str,
        cc: str,
        body_template: str,
        credentials: dict,
        progress,
    ) -> BatchResult:
        report_day = datetime.strptime(report_date, "%d%m%Y").date()
        include_event = report_day.weekday() not in (5, 6)
        event_pdf: Path | None = None

        report_result = run_batch_report(
            env,
            latest,
            report_date,
            has_issue,
            root,
            lambda percent, message: progress(1 + int(percent * 0.50), f"Report: {message}"),
            credentials=credentials,
        )
        if not report_result.ok:
            return report_result
        if not report_result.image_paths:
            return BatchResult(False, "Report images were not created.")

        chile_batch_skipped = report_indicates_chile_batch_skipped(report_result.html_path)
        if chile_batch_skipped:
            include_event = False
            progress(56, t("fbbatch.mail.event_skipped_chile"))

        if include_event:
            if latest:
                event_result = run_eod_batch_event(
                    env,
                    root,
                    lambda percent, message: progress(53 + int(percent * 0.35), f"Event: {message}"),
                    credentials=credentials,
                )
                if not event_result.ok:
                    return event_result
                event_pdf = event_result.pdf_path
                if not event_pdf or not event_pdf.exists():
                    return BatchResult(False, "Event PDF was not created.")
            else:
                event_pdf = find_event_pdf_for_report_date(report_date)
                if event_pdf is None:
                    issue_date = report_date_to_issue_date(report_date)
                    return BatchResult(
                        False,
                        f"No Event PDF found for {issue_date}. The Event process only generates the latest event, so this flow will not attach a PDF from another date.",
                    )
                progress(88, f"Using existing Event PDF: {event_pdf.name}")
        else:
            if not chile_batch_skipped:
                progress(56, t("fbbatch.mail.event_skipped"))

        progress(92, t("fbbatch.mail.creating"))
        subject = render_mail_template(subject_template, report_date, include_event=include_event)
        body = render_mail_template(body_template, report_date, include_event=include_event)
        attachments = [event_pdf] if include_event and event_pdf else []
        create_outlook_draft(
            subject=subject,
            from_account=from_account,
            to=to,
            cc=cc,
            body_text=body,
            attachments=attachments,
            inline_images=report_result.image_paths,
        )
        progress(100, t("fbbatch.mail.opened"))
        return BatchResult(
            True,
            t("fbbatch.mail.opened"),
            html_path=report_result.html_path,
            pdf_path=event_pdf,
            image_paths=report_result.image_paths,
            images_dir=report_result.images_dir,
            output_dir=report_result.output_dir,
            event_skipped=not bool(event_pdf),
        )

    def _on_run_event(self) -> None:
        env = self.event_env.get()
        if not self._confirm_prod(env):
            return
        root = self._ensure_fbbatch_root()
        if root is None:
            return
        self._active_progress = "event"
        credentials = self.app.config.all_credentials()
        self._run_background(
            lambda progress: run_eod_batch_event(
                env, root, progress, credentials=credentials
            )
        )

    def _on_run_report(self) -> None:
        env = self.report_env.get()
        if not self._confirm_prod(env):
            return
        latest = bool(self.latest_var.get())
        report_date = self._current_report_date()
        if not report_date:
            messagebox.showerror(t("common.error"), t("fbbatch.report.invalid_date"), parent=self)
            return
        issue_date = report_date_to_issue_date(report_date)
        issues = issues_for_date(issue_date)
        has_issue = bool(issues)
        root = self._ensure_fbbatch_root()
        if root is None:
            return
        write_issue_properties(issues, root)
        credentials = self.app.config.all_credentials()
        self._active_progress = "report"
        self._run_background(
            lambda progress: run_batch_report(
                env,
                latest,
                report_date,
                has_issue,
                root,
                progress,
                credentials=credentials,
            )
        )

    def _ensure_fbbatch_root(self) -> str | None:
        configured = (self.app.config.get("fbbatch_root") or "").strip()
        ok, msg, root = validate_fbbatch_root(configured or None)
        if ok:
            return str(root)
        selected = filedialog.askdirectory(
            title=t("fbbatch.root.select"),
            initialdir=str(root.parent if root.parent.exists() else Path.cwd()),
            parent=self,
        )
        if not selected:
            self.status_label.configure(text=msg, text_color=("#CF222E", "#FF6B6B"))
            return None
        ok, msg, selected_root = validate_fbbatch_root(selected)
        if not ok:
            messagebox.showerror(t("common.error"), msg, parent=self)
            return None
        self.app.config.set("fbbatch_root", str(selected_root))
        return str(selected_root)

    def _confirm_prod(self, env: str) -> bool:
        if env != "PROD":
            return True
        return messagebox.askyesno(
            t("common.warning"),
            t("fbbatch.confirm_prod"),
            parent=self,
        )

    def _run_background(self, work) -> None:
        if self._running:
            return
        self._running = True
        self._last_pdf = None
        self._last_html = None
        self._last_images_dir = None
        if self._active_progress == "event":
            self._event_pdf = None
            self._event_html = None
            self._event_output_dir = None
            self.event_open_location_btn.configure(state="disabled")
        elif self._active_progress == "report":
            self._report_html = None
            self._report_images_dir = None
            self._report_output_dir = None
            self.report_open_location_btn.configure(state="disabled")
        else:
            self._event_pdf = None
            self._event_html = None
            self._report_html = None
            self._report_images_dir = None
            self._full_output_dir = None
            self._event_output_dir = None
            self._report_output_dir = None
            self.full_open_location_btn.configure(state="disabled")
            self.event_open_location_btn.configure(state="disabled")
            self.report_open_location_btn.configure(state="disabled")
        self._show_progress(self._active_progress)
        self._progress_widgets()[0].set(0)
        self._progress_widgets()[1].configure(text=t("fbbatch.progress.starting"))
        self.status_label.configure(text="")
        threading.Thread(target=self._worker, args=(work,), daemon=True).start()

    def _worker(self, work) -> None:
        try:
            result = work(self._on_progress)
        except Exception as exc:
            log.exception("FBBatchSetup background task failed")
            result = BatchResult(False, str(exc))
        self.after(0, lambda r=result: self._finish(r))

    def _on_progress(self, percent: int, message: str) -> None:
        self.after(0, lambda p=percent, m=message: self._set_progress(p, m))

    def _set_progress(self, percent: int, message: str) -> None:
        bar, label = self._progress_widgets()
        bar.set(max(0, min(100, percent)) / 100)
        label.configure(text=f"{percent}% - {message}")

    def _progress_widgets(self):
        if self._active_progress == "full":
            return self.full_progress_bar, self.full_progress_label
        if self._active_progress == "report":
            return self.report_progress_bar, self.report_progress_label
        return self.event_progress_bar, self.event_progress_label

    def _show_progress(self, target: str) -> None:
        if target == "full":
            self.full_progress_bar.grid()
            self.full_progress_label.grid()
            return
        if target == "report":
            self.report_progress_bar.grid()
            self.report_progress_label.grid()
            return
        self.event_progress_bar.grid()
        self.event_progress_label.grid()

    def _hide_progress(self, target: str) -> None:
        if target == "full":
            self.full_progress_bar.grid_remove()
            self.full_progress_label.grid_remove()
            return
        if target == "report":
            self.report_progress_bar.grid_remove()
            self.report_progress_label.grid_remove()
            return
        self.event_progress_bar.grid_remove()
        self.event_progress_label.grid_remove()

    def _finish(self, result: BatchResult) -> None:
        self._running = False
        self._last_html = result.html_path
        self._last_pdf = result.pdf_path
        self._last_images_dir = result.images_dir
        color = ("#1A7F37", "#3FB950") if result.ok else ("#CF222E", "#FF6B6B")
        if result.ok:
            self._set_progress(100, t("fbbatch.progress.done"))
        else:
            self._progress_widgets()[1].configure(text=t("fbbatch.progress.failed"))
        self.status_label.configure(text="" if result.ok else result.message, text_color=color)
        if self._active_progress == "event":
            self._event_pdf = result.pdf_path
            self._event_html = result.html_path
            self._event_output_dir = result.output_dir
            if self._event_output_dir and self._event_output_dir.exists():
                self.event_open_location_btn.configure(state="normal")
        elif self._active_progress == "report":
            self._report_html = result.html_path
            self._report_images_dir = result.images_dir
            self._report_output_dir = result.output_dir
            if self._report_output_dir and self._report_output_dir.exists():
                self.report_open_location_btn.configure(state="normal")
        else:
            self._event_pdf = result.pdf_path
            self._report_html = result.html_path
            self._report_images_dir = result.images_dir
            self._full_output_dir = result.output_dir
            self._report_output_dir = result.output_dir
            if self._event_pdf and self._event_pdf.exists():
                self._event_output_dir = result.output_dir
                self.event_open_location_btn.configure(state="normal")
            if self._report_output_dir and self._report_output_dir.exists():
                self.report_open_location_btn.configure(state="normal")
            if self._full_output_dir and self._full_output_dir.exists():
                self.full_open_location_btn.configure(state="normal")
            if result.ok:
                messagebox.showinfo(
                    t("fbbatch.mail.ready_title"),
                    t("fbbatch.mail.opened"),
                    parent=self,
                )

    @staticmethod
    def _open_path(path: Path | None) -> None:
        if path and path.exists():
            os.startfile(str(path))

class MailSettingsDialog(ctk.CTkToplevel):
    def __init__(self, master, *, values: dict[str, str], on_saved):
        super().__init__(master)
        self.on_saved = on_saved
        self._generated_body = values["body"]
        self.title(t("fbbatch.mail.edit"))
        self.transient(master.winfo_toplevel())
        self.geometry("920x620")
        self.minsize(760, 520)
        self.grab_set()

        wrap = ctk.CTkFrame(self, fg_color="transparent")
        wrap.pack(fill="both", expand=True, padx=18, pady=18)
        wrap.grid_columnconfigure(1, weight=1)
        wrap.grid_rowconfigure(5, weight=1)
        ctk.CTkLabel(
            wrap,
            text=t("fbbatch.mail.edit"),
            font=ctk.CTkFont(size=18, weight="bold"),
            anchor="w",
        ).grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 12))

        self.subject = ctk.CTkEntry(wrap)
        self.subject.insert(0, values["subject_template"])
        self.from_account = ctk.CTkEntry(wrap, placeholder_text="name@oracle.com")
        self.from_account.insert(0, values["from_account"])
        self.to = ctk.CTkEntry(wrap)
        self.to.insert(0, values["to"])
        self.cc = ctk.CTkEntry(wrap)
        self.cc.insert(0, values["cc"])
        self.body = ctk.CTkTextbox(wrap, height=220)
        self.body.insert("1.0", values["body_template"] or values["body"])

        rows = (
            (t("fbbatch.mail.subject"), self.subject),
            (t("fbbatch.mail.from"), self.from_account),
            ("To", self.to),
            ("Cc", self.cc),
        )
        for row, (label, widget) in enumerate(rows, start=1):
            ctk.CTkLabel(wrap, text=label, width=130, anchor="w").grid(row=row, column=0, sticky="w", pady=5)
            widget.grid(row=row, column=1, sticky="ew", pady=5)
        ctk.CTkLabel(wrap, text=t("fbbatch.mail.body"), width=130, anchor="w").grid(row=5, column=0, sticky="nw", pady=5)
        self.body.grid(row=5, column=1, sticky="nsew", pady=5)

        actions = ctk.CTkFrame(wrap, fg_color="transparent")
        actions.grid(row=6, column=0, columnspan=2, sticky="e", pady=(16, 0))
        ctk.CTkButton(actions, text=t("common.cancel"), width=130, fg_color="transparent", border_width=1, text_color=("gray10", "gray90"), command=self.destroy).pack(side="right", padx=(8, 0))
        ctk.CTkButton(actions, text=t("settings.general.apply"), width=160, command=self._save).pack(side="right")
        self.after(50, self._center_on_screen)

    def _center_on_screen(self) -> None:
        self.update_idletasks()
        width, height = self.winfo_width(), self.winfo_height()
        screen_width = self.winfo_screenwidth()
        screen_height = self.winfo_screenheight()
        x = max(0, (screen_width - width) // 2)
        y = max(0, (screen_height - height) // 2)
        self.geometry(f"+{x}+{y}")

    def _save(self) -> None:
        body_text = self.body.get("1.0", "end").strip()
        body_template = "" if body_text == self._generated_body.strip() else body_text
        self.on_saved({
            "subject_template": self.subject.get().strip(),
            "from_account": self.from_account.get().strip(),
            "to": self.to.get().strip(),
            "cc": self.cc.get().strip(),
            "body_template": body_template,
        })
        self.destroy()


class IssueEditDialog(ctk.CTkToplevel):
    def __init__(self, master, *, issue_date: str, on_saved, issue: dict[str, str] | None = None, saved_name: str | None = None):
        super().__init__(master)
        self.on_saved = on_saved
        self._initial_issue = issue or {}
        self._saved_name = saved_name
        self._text_placeholders: dict[str, str] = {}
        self._text_has_placeholder: set[str] = set()
        self._selected_date = _parse_issue_date(self._initial_issue.get("DATE") or issue_date)
        self._calendar_open = False

        self.title(t("fbbatch.issue.edit") if saved_name else t("fbbatch.issue.new"))
        self.transient(master.winfo_toplevel())
        self.geometry("980x560")
        self.minsize(820, 500)
        self.grab_set()

        wrap = ctk.CTkFrame(self, fg_color="transparent")
        wrap.pack(fill="both", expand=True, padx=18, pady=18)
        wrap.grid_columnconfigure(0, weight=1)
        wrap.grid_rowconfigure(1, weight=1)
        ctk.CTkLabel(
            wrap,
            text=t("fbbatch.issue.edit") if saved_name else t("fbbatch.issue.new"),
            font=ctk.CTkFont(size=18, weight="bold"),
            anchor="w",
        ).grid(row=0, column=0, sticky="ew", pady=(0, 10))

        form = ctk.CTkScrollableFrame(wrap, fg_color="transparent")
        form.grid(row=1, column=0, sticky="nsew")
        self.widgets: dict[str, object] = {}
        labels = _issue_labels()

        for row, field in enumerate(ISSUE_FIELDS):
            ctk.CTkLabel(form, text=labels[field], anchor="w", width=170).grid(
                row=row, column=0, sticky="nw", padx=(0, 8), pady=5
            )
            if field == "COUNTRY":
                widget = ctk.CTkOptionMenu(form, values=list(COUNTRIES))
                widget.set(self._initial_issue.get(field) or "CHILE")
            elif field == "DATE":
                widget = ctk.CTkEntry(form)
                widget.insert(0, _format_issue_date(self._selected_date))
                widget.bind("<Button-1>", lambda _event: self._open_calendar())
                widget.bind("<Key>", lambda _event: "break")
            elif field in TIME_FIELDS:
                widget = TimePicker(form, self._initial_issue.get(field) or EXAMPLE_ISSUE.get(field, ""))
            elif field in TEXT_FIELDS:
                widget = ctk.CTkTextbox(form, height=74)
                if self._initial_issue.get(field):
                    widget.insert("1.0", self._initial_issue[field])
                else:
                    self._set_text_placeholder(field, widget, EXAMPLE_ISSUE.get(field, ""))
            else:
                placeholder = EXAMPLE_ISSUE.get(field, "")
                widget = ctk.CTkEntry(form, placeholder_text=placeholder)
                if self._initial_issue.get(field):
                    widget.insert(0, self._initial_issue[field])
                elif field in {"ADDITIONAL_SUPPORT", "ESCALATED"}:
                    widget.configure(placeholder_text=placeholder or "NO")
            widget.grid(row=row, column=1, sticky="ew", pady=5)
            self.widgets[field] = widget
        form.grid_columnconfigure(1, weight=1)

        buttons = ctk.CTkFrame(wrap, fg_color="transparent")
        buttons.grid(row=2, column=0, sticky="ew", pady=(14, 0))
        ctk.CTkButton(
            buttons,
            text=t("common.cancel"),
            width=110,
            fg_color="transparent",
            border_width=1,
            text_color=("#334155", "#f8fafc"),
            hover_color=("gray80", "gray20"),
            command=self.destroy,
        ).pack(side="right", padx=5)
        ctk.CTkButton(
            buttons,
            text=t("fbbatch.saved.save"),
            width=140,
            command=self._on_save,
        ).pack(side="right", padx=5)
        self.after(50, self._center_on_screen)

    def _open_calendar(self) -> None:
        if self._calendar_open:
            return
        self._calendar_open = True
        CalendarDialog(
            self,
            selected=self._selected_date,
            on_pick=self._set_date,
            on_close=self._calendar_closed,
        )

    def _calendar_closed(self) -> None:
        self._calendar_open = False

    def _set_date(self, value: date) -> None:
        self._selected_date = value
        widget = self.widgets.get("DATE")
        if isinstance(widget, ctk.CTkEntry):
            widget.delete(0, "end")
            widget.insert(0, _format_issue_date(value))

    def _set_text_placeholder(self, field: str, widget: ctk.CTkTextbox, text: str) -> None:
        self._text_placeholders[field] = text
        self._text_has_placeholder.add(field)
        widget.insert("1.0", text)
        widget.configure(text_color=("gray45", "gray55"))
        widget.bind("<FocusIn>", lambda _event, f=field, w=widget: self._clear_text_placeholder(f, w))
        widget.bind("<FocusOut>", lambda _event, f=field, w=widget: self._restore_text_placeholder(f, w))

    def _clear_text_placeholder(self, field: str, widget: ctk.CTkTextbox) -> None:
        if field not in self._text_has_placeholder:
            return
        widget.delete("1.0", "end")
        widget.configure(text_color=("#000000", "#f8fafc"))
        self._text_has_placeholder.discard(field)

    def _restore_text_placeholder(self, field: str, widget: ctk.CTkTextbox) -> None:
        if widget.get("1.0", "end").strip():
            return
        self._text_has_placeholder.add(field)
        widget.insert("1.0", self._text_placeholders.get(field, ""))
        widget.configure(text_color=("gray45", "gray55"))

    def _on_save(self) -> None:
        issue = self._collect_issue()
        invalid_time = [
            _issue_labels()[field]
            for field, widget in self.widgets.items()
            if field in TIME_FIELDS and isinstance(widget, TimePicker) and not widget.is_valid()
        ]
        if invalid_time:
            messagebox.showerror(t("common.error"), t("fbbatch.issue.invalid_time"), parent=self)
            return
        missing = [field for field in ISSUE_FIELDS if not issue.get(field, "").strip()]
        if missing:
            messagebox.showerror(t("common.error"), t("fbbatch.issue.required_all"), parent=self)
            return
        issue_date = issue["DATE"].strip()
        country = issue.get("COUNTRY", "CHILE")
        failure = issue.get("TYPE_OF_FAILURE", "").strip() or "Issue"
        suffix = datetime.now().strftime("%H%M%S")
        if self._saved_name:
            delete_issue_template(self._saved_name)
        save_issue_template(f"{issue_date} {country} {failure[:35]} {suffix}", issue)
        if callable(self.on_saved):
            self.on_saved()
        self.destroy()

    def _collect_issue(self) -> dict[str, str]:
        issue: dict[str, str] = {}
        for field, widget in self.widgets.items():
            if isinstance(widget, ctk.CTkTextbox):
                issue[field] = "" if field in self._text_has_placeholder else widget.get("1.0", "end").strip()
            elif isinstance(widget, ctk.CTkOptionMenu):
                issue[field] = widget.get().strip()
            elif isinstance(widget, TimePicker):
                issue[field] = widget.get()
            else:
                issue[field] = widget.get().strip()
        return issue

    def _center_on_screen(self) -> None:
        self.update_idletasks()
        w, h = self.winfo_width(), self.winfo_height()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"+{max(0, (sw - w) // 2)}+{max(0, (sh - h) // 2)}")


class TimePicker(ctk.CTkFrame):
    def __init__(self, master, example: str = ""):
        super().__init__(master, fg_color="transparent")
        hour, minute, period = _parse_time(example)
        self.hour = ctk.CTkEntry(self, width=76, placeholder_text="HH")
        self.minute = ctk.CTkEntry(self, width=76, placeholder_text="MM")
        self.period = ctk.CTkOptionMenu(self, values=["AM", "PM"], width=76)
        self.hour.insert(0, hour)
        self.minute.insert(0, minute)
        self.period.set(period)
        self.hour.pack(side="left")
        ctk.CTkLabel(self, text=":").pack(side="left", padx=4)
        self.minute.pack(side="left")
        self.period.pack(side="left", padx=(8, 0))

    def get(self) -> str:
        if not self.is_complete():
            return ""
        return f"{int(self.hour.get().strip()):02d}:{int(self.minute.get().strip()):02d} {self.period.get()}"

    def is_complete(self) -> bool:
        return bool(self.hour.get().strip() and self.minute.get().strip() and self.period.get().strip())

    def is_valid(self) -> bool:
        if not self.is_complete():
            return False
        hour = self.hour.get().strip()
        minute = self.minute.get().strip()
        return hour.isdigit() and minute.isdigit() and 1 <= int(hour) <= 12 and 0 <= int(minute) <= 59


class IssueListDialog(ctk.CTkToplevel):
    def __init__(self, master, *, issue_date: str, on_changed):
        super().__init__(master)
        self.issue_date = issue_date
        self.on_changed = on_changed
        self.title(t("fbbatch.issue.view_saved"))
        self.transient(master.winfo_toplevel())
        self.geometry("760x460")
        self.minsize(640, 360)
        self.grab_set()

        wrap = ctk.CTkFrame(self, fg_color="transparent")
        wrap.pack(fill="both", expand=True, padx=18, pady=18)
        ctk.CTkLabel(
            wrap,
            text=t("fbbatch.issue.saved_title", date=issue_date),
            font=ctk.CTkFont(size=18, weight="bold"),
            anchor="w",
        ).pack(fill="x", pady=(0, 10))
        self.list_frame = ctk.CTkScrollableFrame(wrap, fg_color="transparent")
        self.list_frame.pack(fill="both", expand=True)
        ctk.CTkButton(wrap, text=t("common.close"), width=120, command=self.destroy).pack(
            side="right", pady=(12, 0)
        )
        self._render()
        self.after(50, self._center_on_screen)

    def _matching_items(self) -> list[dict[str, object]]:
        wanted = self.issue_date.strip().upper()
        return [
            item
            for item in load_saved_issues()
            if str(item.get("issue", {}).get("DATE", "")).strip().upper() == wanted
        ]

    def _render(self) -> None:
        for child in self.list_frame.winfo_children():
            child.destroy()
        items = self._matching_items()
        if not items:
            ctk.CTkLabel(
                self.list_frame,
                text=t("fbbatch.issue.none_saved"),
                anchor="w",
                text_color=("gray45", "gray60"),
            ).pack(fill="x", pady=12)
            return
        for item in items:
            issue = item.get("issue", {})
            name = str(item.get("name", ""))
            row = ctk.CTkFrame(self.list_frame, fg_color=("gray95", "gray18"), corner_radius=8)
            row.pack(fill="x", pady=(0, 8))
            country = str(issue.get("COUNTRY", ""))
            failure = str(issue.get("TYPE_OF_FAILURE", ""))
            details = str(issue.get("ISSUE_DETAILS", ""))
            label = ctk.CTkLabel(
                row,
                text=f"{country} - {failure}\n{details}",
                anchor="w",
                justify="left",
                wraplength=500,
            )
            label.pack(side="left", fill="x", expand=True, padx=12, pady=10)
            actions = ctk.CTkFrame(row, fg_color="transparent")
            actions.pack(side="right", padx=12, pady=10)
            ctk.CTkButton(
                actions,
                text=t("fbbatch.saved.edit"),
                width=95,
                command=lambda n=name, i=dict(issue): self._edit(n, i),
            ).pack(side="left", padx=(0, 8))
            ctk.CTkButton(
                actions,
                text=t("fbbatch.saved.delete"),
                width=95,
                fg_color=("#dc2626", "#b91c1c"),
                hover_color=("#b91c1c", "#991b1b"),
                command=lambda n=name: self._delete(n),
            ).pack(side="left")

    def _edit(self, name: str, issue: dict[str, str]) -> None:
        IssueEditDialog(
            self,
            issue_date=issue.get("DATE") or self.issue_date,
            issue=issue,
            saved_name=name,
            on_saved=self._after_edit,
        )

    def _after_edit(self) -> None:
        if callable(self.on_changed):
            self.on_changed()
        self._render()

    def _delete(self, name: str) -> None:
        if not messagebox.askyesno(t("common.warning"), t("fbbatch.saved.delete_confirm"), parent=self):
            return
        delete_issue_template(name)
        if callable(self.on_changed):
            self.on_changed()
        self._render()

    def _center_on_screen(self) -> None:
        self.update_idletasks()
        w, h = self.winfo_width(), self.winfo_height()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"+{max(0, (sw - w) // 2)}+{max(0, (sh - h) // 2)}")


class CalendarDialog(ctk.CTkToplevel):
    def __init__(self, master, *, selected: date, on_pick, on_close=None):
        super().__init__(master)
        self.on_pick = on_pick
        self.on_close = on_close
        self.current = date(selected.year, selected.month, 1)
        self.selected = selected
        self.title(t("fbbatch.calendar"))
        self.transient(master.winfo_toplevel())
        self._window_size = (360, 360)
        self._set_initial_geometry()
        self.resizable(False, False)
        self.grab_set()

        wrap = ctk.CTkFrame(self, fg_color="transparent")
        wrap.pack(fill="both", expand=True, padx=16, pady=16)
        nav = ctk.CTkFrame(wrap, fg_color="transparent")
        nav.pack(fill="x", pady=(0, 10))
        ctk.CTkButton(nav, text="<", width=42, command=self._prev_month).pack(side="left")
        self.title_label = ctk.CTkLabel(nav, text="", font=ctk.CTkFont(size=15, weight="bold"))
        self.title_label.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(nav, text=">", width=42, command=self._next_month).pack(side="right")
        self.grid_frame = ctk.CTkFrame(wrap, fg_color="transparent")
        self.grid_frame.pack(fill="both", expand=True)
        self._render()
        self.protocol("WM_DELETE_WINDOW", self._close)

    def _render(self) -> None:
        for child in self.grid_frame.winfo_children():
            child.destroy()
        self.title_label.configure(text=f"{MONTH_ABBR[self.current.month - 1]} {self.current.year}")
        for col, label in enumerate(("Mo", "Tu", "We", "Th", "Fr", "Sa", "Su")):
            ctk.CTkLabel(self.grid_frame, text=label, width=42).grid(row=0, column=col, padx=2, pady=2)
        cal = calendar.Calendar(firstweekday=0)
        for row, week in enumerate(cal.monthdatescalendar(self.current.year, self.current.month), start=1):
            for col, day in enumerate(week):
                same_month = day.month == self.current.month
                is_selected = day == self.selected
                btn = ctk.CTkButton(
                    self.grid_frame,
                    text=str(day.day),
                    width=42,
                    height=32,
                    state="normal" if same_month else "disabled",
                    fg_color=("#4f46e5", "#6366f1") if is_selected else ("#ffffff", "#1e293b"),
                    text_color="white" if is_selected else ("#0f172a", "#f8fafc"),
                    command=lambda d=day: self._pick(d),
                )
                btn.grid(row=row, column=col, padx=2, pady=2)

    def _pick(self, value: date) -> None:
        self.on_pick(value)
        self._close()

    def _close(self) -> None:
        if callable(self.on_close):
            self.on_close()
        self.destroy()

    def _prev_month(self) -> None:
        year = self.current.year if self.current.month > 1 else self.current.year - 1
        month = self.current.month - 1 if self.current.month > 1 else 12
        self.current = date(year, month, 1)
        self._render()

    def _next_month(self) -> None:
        year = self.current.year if self.current.month < 12 else self.current.year + 1
        month = self.current.month + 1 if self.current.month < 12 else 1
        self.current = date(year, month, 1)
        self._render()

    def _center_on_screen(self) -> None:
        self.update_idletasks()
        w, h = self.winfo_width(), self.winfo_height()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"+{max(0, (sw - w) // 2)}+{max(0, (sh - h) // 2)}")

    def _set_initial_geometry(self) -> None:
        w, h = self._window_size
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        x = max(0, (sw - w) // 2)
        y = max(0, (sh - h) // 2)
        self.geometry(f"{w}x{h}+{x}+{y}")


def _format_issue_date(value: date) -> str:
    return f"{value.day:02d}-{MONTH_ABBR[value.month - 1]}-{value.year % 100:02d}"


def _parse_issue_date(value: str) -> date:
    try:
        return datetime.strptime(value.strip().upper(), "%d-%b-%y").date()
    except ValueError:
        return date.today() - timedelta(days=1)


def _parse_time(value: str) -> tuple[str, str, str]:
    try:
        parsed = datetime.strptime(value.strip().upper(), "%I:%M %p")
        return parsed.strftime("%I"), parsed.strftime("%M"), parsed.strftime("%p")
    except ValueError:
        return "09", "00", "PM"
