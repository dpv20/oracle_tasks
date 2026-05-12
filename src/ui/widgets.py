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
