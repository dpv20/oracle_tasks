"""Global dialog for exporting and clearing application diagnostics."""
from __future__ import annotations

from datetime import datetime
from tkinter import filedialog, messagebox

import customtkinter as ctk

from i18n import t
from infra.logger import clear_log, export_log
from paths import LOG_FILE


class LogsDialog(ctk.CTkToplevel):
    WIDTH = 560
    HEIGHT = 300

    def __init__(self, master) -> None:
        super().__init__(master)
        self.withdraw()
        self.title(t("logs.title"))
        self.geometry(f"{self.WIDTH}x{self.HEIGHT}")
        self.resizable(False, False)
        self.transient(master.winfo_toplevel())
        self.protocol("WM_DELETE_WINDOW", self._close)

        content = ctk.CTkFrame(self, fg_color="transparent")
        content.pack(fill="both", expand=True, padx=28, pady=24)

        ctk.CTkLabel(
            content,
            text=t("logs.title"),
            anchor="w",
            font=ctk.CTkFont(size=22, weight="bold"),
        ).pack(fill="x")
        ctk.CTkLabel(
            content,
            text=t("logs.description"),
            anchor="w",
            justify="left",
            wraplength=500,
            text_color=("gray35", "gray70"),
            font=ctk.CTkFont(size=13),
        ).pack(fill="x", pady=(8, 18))

        ctk.CTkLabel(
            content,
            text=t("logs.location", path=str(LOG_FILE)),
            anchor="w",
            justify="left",
            wraplength=500,
            text_color=("gray40", "gray65"),
            font=ctk.CTkFont(size=11),
        ).pack(fill="x")

        actions = ctk.CTkFrame(content, fg_color="transparent")
        actions.pack(fill="x", pady=(24, 0))
        actions.grid_columnconfigure((0, 1), weight=1, uniform="logs-actions")

        ctk.CTkButton(
            actions,
            text=t("logs.export"),
            height=42,
            corner_radius=6,
            command=self._export,
        ).grid(row=0, column=0, sticky="ew", padx=(0, 8))
        ctk.CTkButton(
            actions,
            text=t("logs.clear"),
            height=42,
            corner_radius=6,
            fg_color=("#cf3f32", "#b93b30"),
            hover_color=("#b73328", "#9f3027"),
            command=self._clear,
        ).grid(row=0, column=1, sticky="ew", padx=(8, 0))

        ctk.CTkButton(
            content,
            text=t("common.close"),
            width=120,
            height=34,
            corner_radius=6,
            fg_color=("#64748b", "#475569"),
            hover_color=("#526176", "#3d4b5d"),
            command=self._close,
        ).pack(side="bottom", anchor="e")

        self.after(50, self._show_centered)

    def _show_centered(self) -> None:
        self.update_idletasks()
        parent = self.master.winfo_toplevel()
        parent.update_idletasks()
        x = parent.winfo_rootx() + max(0, (parent.winfo_width() - self.WIDTH) // 2)
        y = parent.winfo_rooty() + max(0, (parent.winfo_height() - self.HEIGHT) // 2)
        screen_width = self.winfo_screenwidth()
        screen_height = self.winfo_screenheight()
        x = min(max(0, x), max(0, screen_width - self.WIDTH))
        y = min(max(0, y), max(0, screen_height - self.HEIGHT))
        self.geometry(f"{self.WIDTH}x{self.HEIGHT}+{x}+{y}")
        self.deiconify()
        self.grab_set()
        self.lift()
        self.focus_force()

    def _export(self) -> None:
        if not LOG_FILE.is_file():
            messagebox.showwarning(t("common.warning"), t("logs.no_log"), parent=self)
            return
        destination = filedialog.asksaveasfilename(
            parent=self,
            title=t("logs.export_title"),
            defaultextension=".log",
            initialfile=f"oracle-tasks-{datetime.now():%Y%m%d-%H%M%S}.log",
            filetypes=[("Log files", "*.log"), ("All files", "*.*")],
        )
        if not destination:
            return
        try:
            export_log(destination)
        except OSError as exc:
            messagebox.showerror(t("common.error"), str(exc), parent=self)
            return
        messagebox.showinfo(
            t("common.info"),
            t("logs.exported", path=destination),
            parent=self,
        )

    def _clear(self) -> None:
        if not messagebox.askyesno(
            t("logs.clear"),
            t("logs.clear_confirm"),
            parent=self,
        ):
            return
        try:
            clear_log()
        except OSError as exc:
            messagebox.showerror(t("common.error"), str(exc), parent=self)
            return
        messagebox.showinfo(t("common.info"), t("logs.cleared"), parent=self)

    def _close(self) -> None:
        try:
            self.grab_release()
        except Exception:
            pass
        self.destroy()
