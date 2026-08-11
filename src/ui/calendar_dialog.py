"""Reusable calendar dialog for date-only selections."""
from __future__ import annotations

import calendar
from datetime import date

import customtkinter as ctk

from i18n import t


_MONTH_ABBR = (
    "JAN",
    "FEB",
    "MAR",
    "APR",
    "MAY",
    "JUN",
    "JUL",
    "AUG",
    "SEP",
    "OCT",
    "NOV",
    "DEC",
)


class CalendarDialog(ctk.CTkToplevel):
    """Modal month calendar that reports a date only after an explicit pick."""

    def __init__(self, master, *, selected: date, on_pick, on_close=None):
        super().__init__(master)
        self.on_pick = on_pick
        self.on_close = on_close
        self.current = date(selected.year, selected.month, 1)
        self.selected = selected
        self.title(t("common.calendar"))
        self.transient(master.winfo_toplevel())
        self._window_size = (360, 360)
        self._set_initial_geometry()
        self.resizable(False, False)
        self.grab_set()

        wrap = ctk.CTkFrame(self, fg_color="transparent")
        wrap.pack(fill="both", expand=True, padx=16, pady=16)
        nav = ctk.CTkFrame(wrap, fg_color="transparent")
        nav.pack(fill="x", pady=(0, 10))
        ctk.CTkButton(nav, text="<", width=42, command=self._prev_month).pack(
            side="left"
        )
        self.title_label = ctk.CTkLabel(
            nav,
            text="",
            font=ctk.CTkFont(size=15, weight="bold"),
        )
        self.title_label.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(nav, text=">", width=42, command=self._next_month).pack(
            side="right"
        )
        self.grid_frame = ctk.CTkFrame(wrap, fg_color="transparent")
        self.grid_frame.pack(fill="both", expand=True)
        self._render()
        self.protocol("WM_DELETE_WINDOW", self._close)

    def _render(self) -> None:
        for child in self.grid_frame.winfo_children():
            child.destroy()
        self.title_label.configure(
            text=f"{_MONTH_ABBR[self.current.month - 1]} {self.current.year}"
        )
        for column, label in enumerate(("Mo", "Tu", "We", "Th", "Fr", "Sa", "Su")):
            ctk.CTkLabel(self.grid_frame, text=label, width=42).grid(
                row=0,
                column=column,
                padx=2,
                pady=2,
            )
        month = calendar.Calendar(firstweekday=0)
        for row, week in enumerate(
            month.monthdatescalendar(self.current.year, self.current.month),
            start=1,
        ):
            for column, day in enumerate(week):
                same_month = day.month == self.current.month
                is_selected = day == self.selected
                button = ctk.CTkButton(
                    self.grid_frame,
                    text=str(day.day),
                    width=42,
                    height=32,
                    state="normal" if same_month else "disabled",
                    fg_color=(
                        ("#4f46e5", "#6366f1")
                        if is_selected
                        else ("#ffffff", "#1e293b")
                    ),
                    text_color=(
                        "white" if is_selected else ("#0f172a", "#f8fafc")
                    ),
                    command=lambda value=day: self._pick(value),
                )
                button.grid(row=row, column=column, padx=2, pady=2)

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

    def _set_initial_geometry(self) -> None:
        width, height = self._window_size
        screen_width = self.winfo_screenwidth()
        screen_height = self.winfo_screenheight()
        x = max(0, (screen_width - width) // 2)
        y = max(0, (screen_height - height) // 2)
        self.geometry(f"{width}x{height}+{x}+{y}")
