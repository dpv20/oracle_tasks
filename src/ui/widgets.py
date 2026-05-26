"""Reusable widgets used across views."""
from __future__ import annotations

from typing import Callable

import customtkinter as ctk


class UpdateBanner(ctk.CTkFrame):
    """Top banner that becomes visible when an update is available."""

    def __init__(self, master, on_click: Callable[[], None], **kw):
        super().__init__(master, fg_color="#4f46e5", corner_radius=0, **kw)
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


class CardFrame(ctk.CTkFrame):
    """A premium, modern card frame with custom light/dark colors and nice borders."""

    def __init__(self, master, **kw):
        kw.setdefault("fg_color", ("#ffffff", "#1e293b"))
        kw.setdefault("border_color", ("#e2e8f0", "#334155"))
        kw.setdefault("border_width", 1)
        kw.setdefault("corner_radius", 12)
        super().__init__(master, **kw)


class IconButton(ctk.CTkButton):
    """Wrapper for ctk.CTkButton with our preferred sizing/styling."""

    def __init__(self, master, **kw):
        kw.setdefault("corner_radius", 8)
        kw.setdefault("height", 36)
        kw.setdefault("fg_color", ("#4f46e5", "#6366f1"))
        kw.setdefault("hover_color", ("#4338ca", "#4f46e5"))
        kw.setdefault("text_color", "white")
        super().__init__(master, **kw)


class SectionLabel(ctk.CTkLabel):
    """Bold section heading used in Settings tabs."""

    def __init__(self, master, text: str, **kw):
        kw.setdefault("font", ctk.CTkFont(size=14, weight="bold"))
        kw.setdefault("anchor", "w")
        kw.setdefault("text_color", ("#1e293b", "#f8fafc"))
        super().__init__(master, text=text, **kw)


class AccountStatusRow(ctk.CTkFrame):
    """One row per account in the Spools CL view: status glyph + account + message.

    States: pending (…), running (⟳), ok (✓), error (✗), cancelled (-).
    """

    _GLYPH = {
        "pending": "⏳",
        "running": "🔄",
        "extracting": "🔄",
        "ready_to_inject": "⏳",
        "injecting": "📤",
        "ok":      "✅",
        "error":   "❌",
        "cancelled": "➖",
    }
    _COLOR = {
        "pending": ("gray50", "gray60"),
        "running": ("#1F6FEB", "#3FB950"),
        "extracting": ("#1F6FEB", "#3FB950"),
        "ready_to_inject": ("#1F6FEB", "#3FB950"),
        "injecting": ("#9333ea", "#c084fc"),
        "ok":      ("#1A7F37", "#3FB950"),
        "error":   ("#CF222E", "#FF6B6B"),
        "cancelled": ("gray45", "gray60"),
    }

    def __init__(self, master, account: str, **kw):
        kw.setdefault("fg_color", ("#f8fafc", "#0f172a"))
        kw.setdefault("border_color", ("#e2e8f0", "#1e293b"))
        kw.setdefault("border_width", 1)
        kw.setdefault("corner_radius", 8)
        super().__init__(master, **kw)
        self.account = account
        self._status = "pending"

        self._status_label = ctk.CTkLabel(
            self, text=self._GLYPH["pending"], width=24,
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=self._COLOR["pending"],
        )
        self._status_label.pack(side="left", padx=(10, 6), pady=8)

        ctk.CTkLabel(
            self, text=account, anchor="w",
            font=ctk.CTkFont(family="Consolas", size=12, weight="bold"),
            text_color=("#0f172a", "#ffffff"),
        ).pack(side="left", padx=(0, 8), pady=8)

        self._msg_label = ctk.CTkLabel(
            self, text="", anchor="e",
            font=ctk.CTkFont(size=11),
            text_color=("gray45", "gray60"),
        )
        self._msg_label.pack(side="right", padx=12, fill="x", expand=True, pady=8)

    def set_status(self, status: str, message: str = "") -> None:
        self._status = status
        self._status_label.configure(
            text=self._GLYPH.get(status, "?"),
            text_color=self._COLOR.get(status, ("gray50", "gray60")),
        )
        self._msg_label.configure(text=message)
