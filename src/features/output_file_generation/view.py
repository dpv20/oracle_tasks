"""CustomTkinter view for read-only Generic Interface output reconstruction."""
from __future__ import annotations

import logging
import os
import queue
import re
import threading
from pathlib import Path
from tkinter import messagebox

import customtkinter as ctk

from i18n import t
from paths import OUTPUT_FILES_OUT_DIR
from ui.widgets import CardFrame, IconButton

from .formats import validate_process_ref
from .models import GenerationRequest, GenerationResult, OracleTarget
from .service import (
    GenerationCancelled,
    OutputFileGenerationError,
    OutputFileGenerationService,
)


log = logging.getLogger(__name__)

_ENV_TAG = {"prod": "PROD"}
_TARGET_COUNTRIES = ("chile", "peru", "colombia", "mexico")
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


class OutputFileGenerationView(ctk.CTkFrame):
    def __init__(self, master, app) -> None:
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.service = OutputFileGenerationService(app.config)
        self._running = False
        self._cancel_event: threading.Event | None = None
        self._events: queue.Queue[tuple[str, object]] = queue.Queue()
        self._db_lookup: dict[str, dict] = {}
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

        ctk.CTkLabel(form, text=t("output_files.prod_db"), width=170, anchor="w").grid(
            row=0, column=0, padx=4, pady=5, sticky="w"
        )
        self.db_var = ctk.StringVar(value="—")
        self.db_menu = ctk.CTkOptionMenu(form, values=["—"], variable=self.db_var)
        self.db_menu.grid(row=0, column=1, padx=4, pady=5, sticky="ew")

        ctk.CTkLabel(form, text=t("output_files.process_ref"), width=170, anchor="w").grid(
            row=1, column=0, padx=4, pady=5, sticky="w"
        )
        self.process_ref_entry = ctk.CTkEntry(
            form,
            placeholder_text="1234567",
            font=ctk.CTkFont(family="Consolas", size=13),
        )
        self.process_ref_entry.grid(row=1, column=1, padx=4, pady=5, sticky="ew")
        self.process_ref_entry.bind("<Return>", lambda _event: self._start())

        ctk.CTkLabel(
            form,
            text=t(
                "output_files.auto_detection",
                count=len(self.service.supported_interfaces()),
            ),
            justify="left",
            anchor="w",
            width=500,
            wraplength=500,
            text_color=("#1D4ED8", "#60A5FA"),
        ).grid(row=2, column=0, columnspan=2, padx=4, pady=(10, 4), sticky="ew")

        self.overwrite_var = ctk.BooleanVar(value=False)
        self.overwrite_checkbox = ctk.CTkCheckBox(
            form,
            text=t("output_files.overwrite"),
            variable=self.overwrite_var,
        )
        self.overwrite_checkbox.grid(
            row=3,
            column=0,
            columnspan=2,
            padx=4,
            pady=(9, 2),
            sticky="w",
        )

        ctk.CTkLabel(
            form,
            text=t("output_files.read_only_note"),
            justify="left",
            anchor="w",
            width=500,
            wraplength=500,
            text_color=("#475569", "#94a3b8"),
        ).grid(row=4, column=0, columnspan=2, padx=4, pady=(12, 0), sticky="ew")

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
            overwrite=bool(self.overwrite_var.get()),
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
        state = "disabled" if running else "normal"
        for widget in (
            self.db_menu,
            self.process_ref_entry,
            self.overwrite_checkbox,
        ):
            widget.configure(state=state)
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
