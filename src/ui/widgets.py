"""Reusable widgets used across views."""
from __future__ import annotations

from typing import Callable

import customtkinter as ctk


class UpdateBanner(ctk.CTkFrame):
    """Top banner that becomes visible when an update is available."""

    def __init__(self, master, on_click: Callable[[], None], **kw):
        super().__init__(master, fg_color="#2563eb", corner_radius=0, **kw)
        self._on_click = on_click
        self._label = ctk.CTkLabel(
            self,
            text="",
            text_color="white",
            cursor="hand2",
            font=ctk.CTkFont(size=13, weight="bold"),
        )
        self._label.pack(pady=8, padx=12)
        self._label.bind("<Button-1>", lambda _e: self._on_click())
        self.bind("<Button-1>", lambda _e: self._on_click())

    def show(self, text: str, before=None) -> None:
        self._label.configure(text=text)
        if before is not None:
            self.pack(side="top", fill="x", before=before)
        else:
            self.pack(side="top", fill="x")

    def hide(self) -> None:
        self.pack_forget()


class IconButton(ctk.CTkButton):
    """Wrapper for ctk.CTkButton with our preferred sizing/styling."""

    def __init__(self, master, **kw):
        kw.setdefault("corner_radius", 8)
        kw.setdefault("height", 36)
        super().__init__(master, **kw)


class SectionLabel(ctk.CTkLabel):
    """Bold section heading used in Settings tabs."""

    def __init__(self, master, text: str, **kw):
        kw.setdefault("font", ctk.CTkFont(size=14, weight="bold"))
        kw.setdefault("anchor", "w")
        super().__init__(master, text=text, **kw)


class AccountStatusRow(ctk.CTkFrame):
    """One row per account in the Spools view: status glyph + account + message.

    States: pending (…), running (⟳), ok (✓), error (✗), cancelled (-).
    """

    _GLYPH = {
        "pending": "…",
        "running": "⟳",
        "ok":      "✓",
        "error":   "✗",
        "cancelled": "-",
    }
    _COLOR = {
        "pending": ("gray50", "gray60"),
        "running": ("#1F6FEB", "#3FB950"),
        "ok":      ("#1A7F37", "#3FB950"),
        "error":   ("#CF222E", "#FF6B6B"),
        "cancelled": ("gray45", "gray60"),
    }

    def __init__(self, master, account: str, **kw):
        super().__init__(master, fg_color="transparent", **kw)
        self.account = account
        self._status = "pending"

        self._status_label = ctk.CTkLabel(
            self, text=self._GLYPH["pending"], width=24,
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=self._COLOR["pending"],
        )
        self._status_label.pack(side="left", padx=(8, 6))

        ctk.CTkLabel(
            self, text=account, anchor="w",
            font=ctk.CTkFont(family="Consolas", size=12),
        ).pack(side="left", padx=(0, 8))

        self._msg_label = ctk.CTkLabel(
            self, text="", anchor="e",
            font=ctk.CTkFont(size=10),
            text_color=("gray45", "gray60"),
        )
        self._msg_label.pack(side="right", padx=8, fill="x", expand=True)

    def set_status(self, status: str, message: str = "") -> None:
        self._status = status
        self._status_label.configure(
            text=self._GLYPH.get(status, "?"),
            text_color=self._COLOR.get(status, ("gray50", "gray60")),
        )
        self._msg_label.configure(text=message)
