"""FBBatchSetup view: run Java batch reports and produce PDFs."""
from __future__ import annotations

import os
import logging
import queue
import threading
import calendar
import webbrowser
from datetime import date, datetime, timedelta
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

from fbbatch.graph_mail import GraphDeviceCode, GraphMailClient
from fbbatch.runner import (
    BatchResult,
    FBBATCH_OUTPUT_DIR,
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
CLASSIC_OUTLOOK_INSTALL_PAGES = {
    "en": "https://support.microsoft.com/en-us/outlook/install-or-reinstall-classic-outlook-on-a-windows-pc",
    "es": "https://support.microsoft.com/es-es/outlook/install-or-reinstall-classic-outlook-on-a-windows-pc",
}
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


def _scale_phase_progress(percent: int, start: int, end: int) -> int:
    percent = max(0, min(100, int(percent)))
    if percent == 0:
        return start
    width = max(0, end - start)
    return min(end, start + max(1, (percent * width) // 100))


def _next_weekday(value: date) -> date:
    candidate = value + timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    return candidate


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


@dataclass(frozen=True)
class _DraftRetryContext:
    report_date: str
    include_event: bool
    attachments: tuple[Path, ...]
    inline_images: tuple[Path, ...]
    html_path: Path | None
    pdf_path: Path | None
    images_dir: Path | None
    output_dir: Path | None


def _retry_context_is_valid(context: _DraftRetryContext | None) -> bool:
    if context is None or not context.inline_images:
        return False
    return all(path.is_file() for path in (*context.attachments, *context.inline_images))


def _classic_outlook_install_url(language: str) -> str:
    return CLASSIC_OUTLOOK_INSTALL_PAGES.get(
        language.strip().lower(),
        CLASSIC_OUTLOOK_INSTALL_PAGES["en"],
    )


def _discover_draft_retry_context(
    report_date: str,
    *,
    output_root: Path = FBBATCH_OUTPUT_DIR,
) -> tuple[_DraftRetryContext | None, str]:
    report_day = datetime.strptime(report_date, "%d%m%Y").date()
    output_dir = output_root / f"NightShift_{report_day:%d-%m-%Y}"
    if not output_dir.is_dir():
        return None, "output"

    summary_image = output_dir / "summary.png"
    inline_images = []
    if summary_image.is_file():
        inline_images.append(summary_image)
    inline_images.extend(sorted(output_dir.glob("incident_*.png")))
    if not inline_images:
        return None, "images"

    summary_html = output_dir / "summary.html"
    include_event = report_day.weekday() not in (5, 6)
    if summary_html.is_file() and report_indicates_chile_batch_skipped(summary_html):
        include_event = False

    event_pdfs = sorted(
        output_dir.glob("EODBatchEvent_*.pdf"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    event_pdf = event_pdfs[0] if event_pdfs else None
    if include_event and event_pdf is None:
        return None, "event"

    context = _DraftRetryContext(
        report_date=report_date,
        include_event=include_event,
        attachments=(event_pdf,) if include_event and event_pdf else (),
        inline_images=tuple(inline_images),
        html_path=summary_html if summary_html.is_file() else None,
        pdf_path=event_pdf,
        images_dir=output_dir,
        output_dir=output_dir,
    )
    return context, ""


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
        self._draft_retry_context: _DraftRetryContext | None = None
        self._worker_events: queue.Queue[tuple[str, object]] | None = None
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
        self.graph_settings_btn = ctk.CTkButton(
            mail_row,
            text=t("fbbatch.graph.settings"),
            width=175,
            command=self._open_graph_settings,
        )
        self.graph_settings_btn.grid(row=0, column=1, padx=(12, 0))
        IconButton(
            mail_row,
            text=t("fbbatch.mail.edit"),
            width=150,
            command=self._open_mail_settings,
        ).grid(row=0, column=2, padx=(12, 0))
        method_row = ctk.CTkFrame(mail_row, fg_color="transparent")
        method_row.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(10, 0))
        ctk.CTkLabel(method_row, text=t("fbbatch.mail.method"), anchor="w").pack(side="left")
        self._mail_method_labels = {
            t("fbbatch.mail.method_classic"): "classic",
            t("fbbatch.mail.method_new"): "new",
            t("fbbatch.mail.method_graph"): "graph",
        }
        configured_method = str(self.app.config.get("fbbatch_mail_method", "new") or "new")
        selected_label = next(
            (label for label, method in self._mail_method_labels.items() if method == configured_method),
            t("fbbatch.mail.method_new"),
        )
        self.mail_method_control = ctk.CTkSegmentedButton(
            method_row,
            values=list(self._mail_method_labels),
            command=self._save_mail_method,
        )
        self.mail_method_control.set(selected_label)
        self.mail_method_control.pack(side="left", padx=(10, 0))
        self.full_output_row = ctk.CTkFrame(inner, fg_color="transparent")
        self.full_output_row.grid(row=7, column=0, columnspan=5, sticky="ew", pady=(10, 0))
        self.install_classic_outlook_btn = ctk.CTkButton(
            self.full_output_row,
            text=t("fbbatch.mail.install_classic"),
            width=175,
            command=self._open_classic_outlook_install_page,
        )
        self.install_classic_outlook_btn.pack(side="left", padx=5)
        self.full_open_location_btn = ctk.CTkButton(
            self.full_output_row,
            text=t("fbbatch.open_location"),
            width=170,
            state="disabled",
            command=lambda: self._open_path(self._full_output_dir),
        )
        self.full_open_location_btn.pack(side="right", padx=5)
        self.full_retry_draft_btn = ctk.CTkButton(
            self.full_output_row,
            text=t("fbbatch.mail.retry"),
            width=170,
            command=self._on_retry_draft,
        )
        self.full_retry_draft_btn.pack(side="right", padx=5)
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
        self._sync_mail_method_actions()
        self._refresh_mail_summary()

    def _build_event_card(self) -> None:
        card = CardFrame(self.body)
        card.pack(fill="x", pady=(0, 12))
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=18, pady=16)
        SectionLabel(inner, text=t("fbbatch.event.title")).grid(row=0, column=0, columnspan=5, sticky="w")
        ctk.CTkLabel(
            inner,
            text=t("fbbatch.event.desc"),
            anchor="w",
            justify="left",
            text_color=("gray40", "gray65"),
        ).grid(row=1, column=0, columnspan=5, sticky="ew", pady=(4, 10))

        ctk.CTkLabel(inner, text=t("fbbatch.env"), width=120, anchor="w").grid(row=2, column=0, sticky="w", pady=4)
        self.event_env = ctk.CTkOptionMenu(inner, values=list(ENVIRONMENTS))
        self.event_env.grid(row=2, column=1, sticky="ew", padx=8, pady=4)
        self.event_latest_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            inner,
            text=t("fbbatch.event.latest"),
            variable=self.event_latest_var,
            command=self._sync_event_date_state,
        ).grid(row=2, column=2, sticky="w", padx=8, pady=4)
        IconButton(
            inner,
            text=t("fbbatch.event.run"),
            width=180,
            command=self._on_run_event,
        ).grid(row=2, column=4, sticky="e", padx=8, pady=4)

        self._event_selected_date = date.today() - timedelta(days=1)
        self.event_date_label = ctk.CTkLabel(
            inner,
            text=t("fbbatch.event.batch_date"),
            width=120,
            anchor="w",
        )
        self.event_date_label.grid(row=3, column=0, sticky="w", pady=4)
        self.event_date_frame = ctk.CTkFrame(inner, fg_color="transparent")
        self.event_date_frame.grid(row=3, column=1, columnspan=3, sticky="ew", padx=8, pady=4)
        self.event_date_frame.grid_columnconfigure(0, weight=1)
        self.event_date = ctk.CTkEntry(self.event_date_frame)
        self.event_date.insert(0, _format_issue_date(self._event_selected_date))
        self.event_date.configure(state="disabled")
        self.event_date.grid(row=0, column=0, sticky="ew")
        self.event_calendar_btn = ctk.CTkButton(
            self.event_date_frame,
            text=t("fbbatch.calendar"),
            width=105,
            command=self._open_event_calendar,
        )
        self.event_calendar_btn.grid(row=0, column=1, padx=(8, 0))

        self.event_progress_bar = ctk.CTkProgressBar(inner)
        self.event_progress_bar.set(0)
        self.event_progress_bar.grid(row=4, column=0, columnspan=5, sticky="ew", pady=(12, 2))
        self.event_progress_label = ctk.CTkLabel(
            inner,
            text="",
            anchor="w",
            justify="left",
            text_color=("gray40", "gray65"),
            font=ctk.CTkFont(size=11),
        )
        self.event_progress_label.grid(row=5, column=0, columnspan=5, sticky="ew")
        self.event_output_row = ctk.CTkFrame(inner, fg_color="transparent")
        self.event_output_row.grid(row=6, column=0, columnspan=5, sticky="e", pady=(10, 0))
        self.event_open_location_btn = ctk.CTkButton(
            self.event_output_row,
            text=t("fbbatch.open_location"),
            width=170,
            state="disabled",
            command=lambda: self._open_path(self._event_output_dir),
        )
        self.event_open_location_btn.pack(side="right", padx=5)
        self._sync_event_date_state()
        self._hide_progress("event")
        inner.grid_columnconfigure(1, weight=1)
        inner.grid_columnconfigure(3, weight=1)

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
        return self._mail_values_for_report_date(report_date, include_event=include_event)

    def _mail_values_for_report_date(
        self,
        report_date: str,
        *,
        include_event: bool,
    ) -> dict[str, str]:
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
        method = self._current_mail_method()
        from_account = (
            str(self.app.config.get("falabella_email", "") or "").strip()
            if method == "graph"
            else values["from_account"]
        )
        summary = t(
            "fbbatch.mail.summary",
            subject=values["subject"],
            sender=from_account or t("fbbatch.mail.not_configured"),
        )
        if not from_account:
            missing_key = (
                "fbbatch.graph.falabella_missing"
                if method == "graph"
                else "fbbatch.mail.from_missing"
            )
            summary += "\n⚠ " + t(missing_key)
        self.mail_summary.configure(
            text=summary,
            text_color=("#a16207", "#fbbf24") if not from_account else ("gray40", "gray65"),
        )

    def _open_mail_settings(self) -> None:
        MailSettingsDialog(self, values=self._mail_values_for_current_date(), on_saved=self._save_mail_settings)

    def _open_graph_settings(self) -> None:
        falabella_account = str(self.app.config.get("falabella_email", "") or "").strip()
        if not falabella_account:
            messagebox.showwarning(
                t("fbbatch.graph.falabella_missing_title"),
                t("fbbatch.graph.falabella_missing"),
                parent=self,
            )
            return
        GraphSettingsDialog(
            self,
            preferred_username=falabella_account,
        )

    def _open_classic_outlook_install_page(self) -> None:
        language = str(self.app.config.get("language", "en"))
        url = _classic_outlook_install_url(language)
        log.info("night_shift: opening Classic Outlook installation page language=%s url=%s", language, url)
        try:
            opened = webbrowser.open(url, new=2)
        except OSError:
            log.exception("night_shift: could not open Classic Outlook installation page")
            opened = False
        if not opened:
            messagebox.showerror(
                t("common.error"),
                t("fbbatch.mail.install_open_failed", url=url),
                parent=self,
            )

    def _current_mail_method(self) -> str:
        label = self.mail_method_control.get()
        return self._mail_method_labels.get(label, "new")

    def _save_mail_method(self, selected_label: str) -> None:
        method = self._mail_method_labels.get(selected_label, "new")
        self.app.config.update(
            {
                "fbbatch_mail_method": method,
                "fbbatch_use_classic_outlook": method == "classic",
            }
        )
        log.info(
            "night_shift: mail draft method changed method=%s",
            method,
        )
        self._sync_mail_method_actions()
        self._refresh_mail_summary()

    def _sync_mail_method_actions(self) -> None:
        if self._current_mail_method() == "graph":
            self.graph_settings_btn.grid()
        else:
            self.graph_settings_btn.grid_remove()

    def _selected_mail_settings(self, outlook_sender: str) -> tuple[str, str] | None:
        method = self._current_mail_method()
        if method == "graph":
            falabella_account = str(self.app.config.get("falabella_email", "") or "").strip()
            if not falabella_account:
                messagebox.showwarning(
                    t("fbbatch.graph.falabella_missing_title"),
                    t("fbbatch.graph.falabella_missing"),
                    parent=self,
                )
                return None
            return method, falabella_account
        if not outlook_sender:
            messagebox.showwarning(
                t("fbbatch.mail.from_missing_title"),
                t("fbbatch.mail.from_missing"),
                parent=self,
            )
            self._open_mail_settings()
            return None
        return method, outlook_sender

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

    def _open_event_calendar(self) -> None:
        CalendarDialog(self, selected=self._event_selected_date, on_pick=self._set_event_date)

    def _set_event_date(self, value: date) -> None:
        self._event_selected_date = value
        self.event_date.configure(state="normal")
        self.event_date.delete(0, "end")
        self.event_date.insert(0, _format_issue_date(value))
        self.event_date.configure(state="disabled")

    def _sync_event_date_state(self) -> None:
        historical_widgets = (
            self.event_date_label,
            self.event_date_frame,
        )
        if self.event_latest_var.get():
            for widget in historical_widgets:
                widget.grid_remove()
            return
        for widget in historical_widgets:
            widget.grid()

    def _current_event_dates(self) -> tuple[str, str]:
        return (
            self._event_selected_date.strftime("%d%m%Y"),
            _next_weekday(self._event_selected_date).strftime("%d%m%Y"),
        )

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
        env = self.full_env.get()
        vpn_ok, vpn_message = check_falabella_vpn()
        log.info(
            "night_shift: VPN prerequisite checked ok=%s detail=%r env=%s",
            vpn_ok,
            vpn_message,
            env,
        )
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
        selected_mail = self._selected_mail_settings(from_account)
        if selected_mail is None:
            return
        mail_method, from_account = selected_mail

        latest = bool(self.full_latest_var.get())
        log.info(
            "night_shift: generate requested env=%s report_date=%s latest=%s has_issue=%s "
            "root=%s from_configured=%s to_chars=%s cc_chars=%s",
            env,
            report_date,
            latest,
            bool(issues),
            root,
            bool(from_account),
            len(to),
            len(cc),
        )
        log.info(
            "night_shift: generate selected mail method=%s",
            mail_method,
        )
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
                mail_method=mail_method,
                credentials=self.app.config.all_credentials(),
                progress=progress,
            )
        )

    def _on_retry_draft(self) -> None:
        report_date = self._current_full_report_date()
        context, missing = _discover_draft_retry_context(report_date)
        log.info(
            "night_shift: retry requested report_date=%s context_found=%s missing=%r",
            report_date,
            context is not None,
            missing,
        )
        if not _retry_context_is_valid(context):
            log.warning(
                "night_shift: retry artifacts invalid report_date=%s missing=%r context=%r",
                report_date,
                missing,
                context,
            )
            display_date = datetime.strptime(report_date, "%d%m%Y").strftime("%d-%m-%Y")
            messagebox.showerror(
                t("common.error"),
                t(
                    "fbbatch.mail.retry_missing",
                    date=display_date,
                    detail=t(f"fbbatch.mail.retry_missing_{missing or 'images'}"),
                ),
                parent=self,
            )
            return
        self._draft_retry_context = context
        log.info(
            "night_shift: retry artifacts include_event=%s attachments=%s inline_images=%s "
            "html=%s pdf=%s output_dir=%s",
            context.include_event,
            [str(path) for path in context.attachments],
            [str(path) for path in context.inline_images],
            context.html_path,
            context.pdf_path,
            context.output_dir,
        )

        mail_values = self._mail_values_for_report_date(
            context.report_date,
            include_event=context.include_event,
        )
        selected_mail = self._selected_mail_settings(mail_values["from_account"])
        if selected_mail is None:
            return
        mail_method, from_account = selected_mail
        self._active_progress = "full"
        log.info(
            "night_shift: retry selected mail method=%s",
            mail_method,
        )
        self._run_background(
            lambda progress: self._retry_draft(
                context=context,
                subject=mail_values["subject"],
                from_account=from_account,
                to=mail_values["to"],
                cc=mail_values["cc"],
                body=mail_values["body"],
                mail_method=mail_method,
                progress=progress,
            ),
            preserve_outputs=True,
        )

    @staticmethod
    def _retry_draft(
        *,
        context: _DraftRetryContext,
        subject: str,
        from_account: str,
        to: str,
        cc: str,
        body: str,
        mail_method: str,
        progress,
    ) -> BatchResult:
        log.info(
            "night_shift: retry draft starting report_date=%s include_event=%s "
            "attachments=%s inline_images=%s",
            context.report_date,
            context.include_event,
            len(context.attachments),
            len(context.inline_images),
        )
        progress(91, t("fbbatch.mail.preparing"))
        progress(94, t("fbbatch.mail.retrying"))
        create_outlook_draft(
            subject=subject,
            from_account=from_account,
            to=to,
            cc=cc,
            body_text=body,
            attachments=list(context.attachments),
            inline_images=list(context.inline_images),
            mail_method=mail_method,
        )
        log.info("night_shift: retry draft completed report_date=%s", context.report_date)
        progress(100, t("fbbatch.mail.opened"))
        return BatchResult(
            True,
            t("fbbatch.mail.opened"),
            html_path=context.html_path,
            pdf_path=context.pdf_path,
            image_paths=list(context.inline_images),
            images_dir=context.images_dir,
            output_dir=context.output_dir,
            event_skipped=not context.include_event,
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
        mail_method: str,
        credentials: dict,
        progress,
    ) -> BatchResult:
        report_day = datetime.strptime(report_date, "%d%m%Y").date()
        event_next_date = _next_weekday(report_day).strftime("%d%m%Y")
        include_event = report_day.weekday() not in (5, 6)
        event_pdf: Path | None = None
        log.info(
            "night_shift: workflow started env=%s report_date=%s latest=%s has_issue=%s "
            "weekday=%s include_event_initial=%s event_next_date_auto=%s",
            env,
            report_date,
            latest,
            has_issue,
            report_day.weekday(),
            include_event,
            event_next_date,
        )

        report_result = run_batch_report(
            env,
            latest,
            report_date,
            has_issue,
            root,
            lambda percent, message: progress(
                _scale_phase_progress(percent, 0, 50), f"Report: {message}"
            ),
            credentials=credentials,
        )
        log.info(
            "night_shift: report completed ok=%s message=%r html=%s images=%s output_dir=%s",
            report_result.ok,
            report_result.message,
            report_result.html_path,
            [str(path) for path in (report_result.image_paths or [])],
            report_result.output_dir,
        )
        if not report_result.ok:
            return report_result
        if not report_result.image_paths:
            return BatchResult(False, "Report images were not created.")

        chile_batch_skipped = report_indicates_chile_batch_skipped(report_result.html_path)
        log.info("night_shift: Chile batch skipped=%s", chile_batch_skipped)
        if chile_batch_skipped:
            include_event = False
            progress(90, t("fbbatch.mail.event_skipped_chile"))

        if include_event:
            if latest:
                progress(50, "Event: Starting EOD Batch Event")
                event_result = run_eod_batch_event(
                    env,
                    root,
                    lambda percent, message: progress(
                        _scale_phase_progress(percent, 50, 90), f"Event: {message}"
                    ),
                    credentials=credentials,
                )
                log.info(
                    "night_shift: event completed ok=%s message=%r pdf=%s output_dir=%s",
                    event_result.ok,
                    event_result.message,
                    event_result.pdf_path,
                    event_result.output_dir,
                )
                if not event_result.ok:
                    return event_result
                event_pdf = event_result.pdf_path
                if not event_pdf or not event_pdf.exists():
                    return BatchResult(False, "Event PDF was not created.")
                progress(90, t("fbbatch.event.pdf_ready"))
            else:
                event_pdf = find_event_pdf_for_report_date(report_date)
                if event_pdf is None:
                    progress(50, "Event: Starting historical EOD Batch Event")
                    event_result = run_eod_batch_event(
                        env,
                        root,
                        lambda percent, message: progress(
                            _scale_phase_progress(percent, 50, 90), f"Event: {message}"
                        ),
                        credentials=credentials,
                        latest=False,
                        event_date=report_date,
                        next_date=event_next_date,
                    )
                    log.info(
                        "night_shift: historical event completed ok=%s message=%r pdf=%s "
                        "output_dir=%s event_date=%s next_date=%s",
                        event_result.ok,
                        event_result.message,
                        event_result.pdf_path,
                        event_result.output_dir,
                        report_date,
                        event_next_date,
                    )
                    if not event_result.ok:
                        return event_result
                    event_pdf = event_result.pdf_path
                    if not event_pdf or not event_pdf.exists():
                        return BatchResult(False, "Historical Event PDF was not created.")
                    progress(90, t("fbbatch.event.pdf_ready"))
                else:
                    progress(90, f"Using existing Event PDF: {event_pdf.name}")
        else:
            if not chile_batch_skipped:
                progress(90, t("fbbatch.mail.event_skipped"))

        progress(91, t("fbbatch.mail.preparing"))
        subject = render_mail_template(subject_template, report_date, include_event=include_event)
        body = render_mail_template(body_template, report_date, include_event=include_event)
        attachments = [event_pdf] if include_event and event_pdf else []
        retry_context = _DraftRetryContext(
            report_date=report_date,
            include_event=include_event,
            attachments=tuple(attachments),
            inline_images=tuple(report_result.image_paths),
            html_path=report_result.html_path,
            pdf_path=event_pdf,
            images_dir=report_result.images_dir,
            output_dir=report_result.output_dir,
        )
        self._draft_retry_context = retry_context
        log.info(
            "night_shift: creating draft include_event=%s attachments=%s inline_images=%s "
            "from_configured=%s",
            include_event,
            [str(path) for path in attachments],
            [str(path) for path in report_result.image_paths],
            bool(from_account),
        )
        progress(94, t("fbbatch.mail.creating"))
        try:
            create_outlook_draft(
                subject=subject,
                from_account=from_account,
                to=to,
                cc=cc,
                body_text=body,
                attachments=attachments,
                inline_images=report_result.image_paths,
                mail_method=mail_method,
            )
        except Exception as exc:
            log.exception("night_shift: draft creation failed report_date=%s", report_date)
            return BatchResult(
                False,
                str(exc),
                html_path=retry_context.html_path,
                pdf_path=retry_context.pdf_path,
                image_paths=list(retry_context.inline_images),
                images_dir=retry_context.images_dir,
                output_dir=retry_context.output_dir,
                event_skipped=not retry_context.include_event,
            )
        log.info("night_shift: workflow completed report_date=%s", report_date)
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
        latest = bool(self.event_latest_var.get())
        event_date, next_date = self._current_event_dates()
        root = self._ensure_fbbatch_root()
        if root is None:
            return
        self._active_progress = "event"
        credentials = self.app.config.all_credentials()
        self._run_background(
            lambda progress: run_eod_batch_event(
                env,
                root,
                progress,
                credentials=credentials,
                latest=latest,
                event_date=event_date,
                next_date=next_date,
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

    def _run_background(self, work, *, preserve_outputs: bool = False) -> None:
        if self._running:
            return
        self._running = True
        self.full_retry_draft_btn.configure(state="disabled")
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
        elif not preserve_outputs:
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
        worker_events: queue.Queue[tuple[str, object]] = queue.Queue()
        self._worker_events = worker_events
        self.after(50, lambda events=worker_events: self._poll_worker_events(events))
        threading.Thread(
            target=self._worker,
            args=(work, worker_events),
            daemon=True,
        ).start()

    @staticmethod
    def _worker(work, worker_events: queue.Queue[tuple[str, object]]) -> None:
        def report_progress(percent: int, message: str) -> None:
            worker_events.put(("progress", (percent, message)))

        try:
            result = work(report_progress)
        except Exception as exc:
            log.exception("FBBatchSetup background task failed")
            result = BatchResult(False, str(exc))
        worker_events.put(("finish", result))

    def _poll_worker_events(
        self,
        worker_events: queue.Queue[tuple[str, object]],
    ) -> None:
        if worker_events is not self._worker_events:
            return

        while True:
            try:
                event_type, payload = worker_events.get_nowait()
            except queue.Empty:
                break

            if event_type == "progress":
                percent, message = payload
                self._set_progress(int(percent), str(message))
                continue

            if event_type == "finish":
                self._worker_events = None
                result = payload
                log.info(
                    "FBBatchSetup UI received background completion ok=%s",
                    getattr(result, "ok", False),
                )
                self._finish(result)
                return

        self.after(50, lambda events=worker_events: self._poll_worker_events(events))

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
        self.full_retry_draft_btn.configure(state="normal")
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
            context = self._draft_retry_context
            self._event_pdf = context.pdf_path if context else result.pdf_path
            self._report_html = context.html_path if context else result.html_path
            self._report_images_dir = context.images_dir if context else result.images_dir
            self._full_output_dir = context.output_dir if context else result.output_dir
            self._report_output_dir = self._full_output_dir
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


class GraphSettingsDialog(ctk.CTkToplevel):
    def __init__(
        self,
        master,
        *,
        preferred_username: str,
    ) -> None:
        super().__init__(master)
        self.preferred_username = preferred_username
        self._busy = False
        self._active_device_code: GraphDeviceCode | None = None
        self._device_login_url = ""
        self._result_queue: queue.Queue[tuple[str, object, str]] = queue.Queue()
        self.title(t("fbbatch.graph.settings"))
        self.transient(master.winfo_toplevel())
        self.geometry("780x430")
        self.minsize(720, 400)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self._close_dialog)

        wrap = ctk.CTkFrame(self, fg_color="transparent")
        wrap.pack(fill="both", expand=True, padx=22, pady=20)
        wrap.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            wrap,
            text=t("fbbatch.graph.settings"),
            font=ctk.CTkFont(size=18, weight="bold"),
            anchor="w",
        ).grid(row=0, column=0, sticky="ew", pady=(0, 12))

        ctk.CTkLabel(
            wrap,
            text=t("fbbatch.graph.description"),
            anchor="w",
            justify="left",
            text_color=("gray40", "gray65"),
            wraplength=700,
        ).grid(row=1, column=0, sticky="ew", pady=(0, 16))

        self.device_frame = ctk.CTkFrame(wrap)
        self.device_frame.grid(row=2, column=0, sticky="ew", pady=(0, 14), ipady=10)
        self.device_frame.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(
            self.device_frame,
            text=t("fbbatch.graph.device_code"),
            width=130,
            anchor="w",
        ).grid(row=0, column=0, sticky="w", padx=(14, 0))
        self.device_code_label = ctk.CTkLabel(
            self.device_frame,
            text="",
            anchor="w",
            font=ctk.CTkFont(family="Consolas", size=20, weight="bold"),
        )
        self.device_code_label.grid(row=0, column=1, sticky="w", padx=(0, 10))
        self.copy_code_button = ctk.CTkButton(
            self.device_frame,
            text=t("fbbatch.graph.copy_code"),
            width=115,
            command=self._copy_device_code,
        )
        self.copy_code_button.grid(row=0, column=2, padx=(0, 8))
        self.open_login_button = ctk.CTkButton(
            self.device_frame,
            text=t("fbbatch.graph.open_sign_in"),
            width=170,
            command=self._open_device_login,
        )
        self.open_login_button.grid(row=0, column=3)
        self.device_frame.grid_remove()

        self.status_label = ctk.CTkLabel(
            wrap,
            text=t("fbbatch.graph.not_connected"),
            anchor="w",
            justify="left",
            text_color=("gray35", "gray70"),
        )
        self.status_label.grid(row=3, column=0, sticky="ew", pady=(0, 18))

        auth_actions = ctk.CTkFrame(wrap, fg_color="transparent")
        auth_actions.grid(row=4, column=0, sticky="w")
        self.sign_in_button = ctk.CTkButton(
            auth_actions,
            text=t("fbbatch.graph.sign_in"),
            width=170,
            command=self._sign_in_with_code,
        )
        self.sign_in_button.pack(side="left")
        self.test_button = ctk.CTkButton(
            auth_actions,
            text=t("fbbatch.graph.test"),
            width=115,
            command=self._test_connection,
        )
        self.test_button.pack(side="left", padx=(10, 0))
        self.sign_out_button = ctk.CTkButton(
            auth_actions,
            text=t("fbbatch.graph.sign_out"),
            width=115,
            fg_color="transparent",
            border_width=1,
            text_color=("gray10", "gray90"),
            command=self._sign_out,
        )
        self.sign_out_button.pack(side="left", padx=(10, 0))

        actions = ctk.CTkFrame(wrap, fg_color="transparent")
        actions.grid(row=5, column=0, sticky="e", pady=(24, 0))
        ctk.CTkButton(
            actions,
            text=t("common.close"),
            width=130,
            fg_color="transparent",
            border_width=1,
            text_color=("gray10", "gray90"),
            command=self._close_dialog,
        ).pack(side="right")

        self.after(50, self._center_on_screen)
        self.after(120, self._refresh_cached_status)
        self.after(100, self._poll_results)

    def _refresh_cached_status(self) -> None:
        self._set_status(t("fbbatch.graph.checking"))

        def worker() -> None:
            try:
                identity = GraphMailClient().cached_identity(self.preferred_username)
            except Exception as exc:
                log.exception("graph_mail: cached account inspection failed")
                self._result_queue.put(("cache", "", str(exc)))
                return
            account = identity.username if identity else ""
            self._result_queue.put(("cache", account, ""))

        threading.Thread(target=worker, name="graph-cache-status", daemon=True).start()

    def _finish_cached_status(self, *, account: str = "", error: str = "") -> None:
        if not self.winfo_exists():
            return
        if self._busy:
            return
        if error:
            self._set_status(error, error=True)
        elif account:
            self._set_status(t("fbbatch.graph.cached", account=account))
        else:
            self._set_status(t("fbbatch.graph.not_connected"))

    def _sign_in_with_code(self) -> None:
        if self._busy:
            return
        self._hide_device_code()
        self._set_busy(True)
        self._set_status(t("fbbatch.graph.requesting_code"))

        def worker() -> None:
            try:
                client = GraphMailClient()
                device_code = client.initiate_device_sign_in()
                self._active_device_code = device_code
                self._result_queue.put(("device", device_code, ""))
                identity = client.complete_device_sign_in(
                    device_code,
                    self.preferred_username,
                )
            except Exception as exc:
                log.exception("graph_mail: device-code settings action failed")
                self._result_queue.put(("action", "", str(exc)))
                return
            self._result_queue.put(("action", identity.username, ""))

        threading.Thread(target=worker, name="graph-device-sign-in", daemon=True).start()

    def _test_connection(self) -> None:
        self._run_graph_action(
            t("fbbatch.graph.testing"),
            lambda client: client.test_connection(self.preferred_username),
        )

    def _sign_out(self) -> None:
        self._run_graph_action(t("fbbatch.graph.signing_out"), self._sign_out_client)

    @staticmethod
    def _sign_out_client(client: GraphMailClient):
        client.sign_out()
        return None

    def _run_graph_action(self, pending_text: str, action) -> None:
        if self._busy:
            return
        self._set_busy(True)
        self._set_status(pending_text)

        def worker() -> None:
            try:
                client = GraphMailClient()
                identity = action(client)
            except Exception as exc:
                log.exception("graph_mail: settings action failed")
                self._result_queue.put(("action", "", str(exc)))
                return
            account = identity.username if identity else ""
            self._result_queue.put(("action", account, ""))

        threading.Thread(target=worker, name="graph-settings", daemon=True).start()

    def _poll_results(self) -> None:
        if not self.winfo_exists():
            return
        while True:
            try:
                result_type, payload, error = self._result_queue.get_nowait()
            except queue.Empty:
                break
            if result_type == "cache":
                self._finish_cached_status(account=str(payload), error=error)
            elif result_type == "device" and isinstance(payload, GraphDeviceCode):
                self._show_device_code(payload)
            else:
                self._finish_action(account=str(payload), error=error)
        self.after(100, self._poll_results)

    def _show_device_code(self, device_code: GraphDeviceCode) -> None:
        self._active_device_code = device_code
        self._device_login_url = device_code.verification_uri
        self.device_code_label.configure(text=device_code.user_code)
        self.device_frame.grid()
        self.copy_code_button.configure(state="normal")
        self.open_login_button.configure(state="normal")
        self._set_status(
            t("fbbatch.graph.enter_code", code=device_code.user_code),
            success=True,
        )
        self._open_device_login()

    def _hide_device_code(self) -> None:
        self._active_device_code = None
        self._device_login_url = ""
        self.device_code_label.configure(text="")
        self.device_frame.grid_remove()

    def _copy_device_code(self) -> None:
        code = self.device_code_label.cget("text")
        if not code:
            return
        self.clipboard_clear()
        self.clipboard_append(code)
        self._set_status(t("fbbatch.graph.code_copied", code=code), success=True)

    def _open_device_login(self) -> None:
        if not self._device_login_url:
            return
        try:
            opened = webbrowser.open(self._device_login_url, new=2)
        except OSError:
            log.exception("graph_mail: could not open device login page")
            opened = False
        if not opened:
            self._set_status(t("fbbatch.graph.device_login_failed"), error=True)

    def _finish_action(self, *, account: str = "", error: str = "") -> None:
        if not self.winfo_exists():
            return
        self._set_busy(False)
        if error:
            self._hide_device_code()
            self._set_status(error, error=True)
        elif account:
            self._hide_device_code()
            self._set_status(t("fbbatch.graph.connected", account=account), success=True)
        else:
            self._hide_device_code()
            self._set_status(t("fbbatch.graph.signed_out"))

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        state = "disabled" if busy else "normal"
        for button in (
            self.sign_in_button,
            self.test_button,
            self.sign_out_button,
        ):
            button.configure(state=state)

    def _set_status(self, text: str, *, error: bool = False, success: bool = False) -> None:
        color = ("gray35", "gray70")
        if error:
            color = ("#b91c1c", "#f87171")
        elif success:
            color = ("#047857", "#34d399")
        self.status_label.configure(text=text, text_color=color)

    def _close_dialog(self) -> None:
        GraphMailClient.cancel_device_sign_in(self._active_device_code)
        self.destroy()

    def _center_on_screen(self) -> None:
        self.update_idletasks()
        width, height = self.winfo_width(), self.winfo_height()
        x = max(0, (self.winfo_screenwidth() - width) // 2)
        y = max(0, (self.winfo_screenheight() - height) // 2)
        self.geometry(f"+{x}+{y}")


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
