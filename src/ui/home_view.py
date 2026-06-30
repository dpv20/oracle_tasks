"""Home view — welcome dashboard with feature shortcut cards."""
from __future__ import annotations

import customtkinter as ctk
from PIL import Image

from i18n import t
from paths import ASSETS_DIR

from .widgets import CardFrame, IconButton


class HomeView(ctk.CTkFrame):
    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app = app

        # Main scrollable container to support all screen resolutions gracefully
        self.scroll_container = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.scroll_container.pack(fill="both", expand=True, padx=25, pady=25)

        # Welcome Section Header
        self.header_frame = ctk.CTkFrame(self.scroll_container, fg_color="transparent")
        self.header_frame.pack(fill="x", anchor="w", pady=(15, 20))

        # Dynamic localization for welcome dashboard elements
        lang = self.app.config.get("language", "en")
        if lang == "es":
            welcome_title = "Panel de Automatización Oracle"
            welcome_subtitle = "Flujos de trabajo ágiles para spools y gestión de sucursales Falabella."
            cl_title = "▣  Consumer Lending"
            cl_desc = "Extrae y aplica spools de cuentas CL Consumer Lending entre ambientes origen y destino de prueba."
            cl_btn = "Iniciar Consumer Lending"
            sav_title = "💵  Spools de Ahorros e Inversión"
            sav_desc = "Genera, gestiona e inyecta archivos INC de spools de cuentas de Ahorro y Fondos de Inversión entre ambientes de base de datos."
            sav_btn = "Iniciar Spools Ahorros"
            cmr_title = "◇  CMR Chile"
            cmr_desc = "Extrae y aplica spools CMR para Chile usando número de cuenta y branch."
            cmr_btn = "Iniciar CMR"
            branch_title = "🌿  Crear Sucursal Falabella"
            branch_desc = "Crea y mantiene datos de sucursales Falabella usando campos de negocio estandarizados."
            branch_btn = "Próximamente"
        else:
            welcome_title = "Oracle Automation Dashboard"
            welcome_subtitle = "Sleek database spool and Falabella branch management workflows in a unified panel."
            cl_title = "▣  Consumer Lending"
            cl_desc = "Extract and apply CL Consumer Lending account spools across source and test database environments."
            cl_btn = "Launch Consumer Lending"
            sav_title = "💵  Savings & Investment Accounts"
            sav_desc = "Generate, manage, and inject spool INC scripts for Savings and Mutual Fund structures across configured environments."
            sav_btn = "Launch Savings Spools"
            cmr_title = "◇  CMR Chile"
            cmr_desc = "Extract and apply Chile CMR spools using account number and branch."
            cmr_btn = "Launch CMR"
            branch_title = "🌿  Create Falabella Branch"
            branch_desc = "Create and maintain Falabella branch records using standardized business fields."
            branch_btn = "Coming Soon"

        branch_title = "☾  " + t("fbbatch.title")
        branch_desc = t("fbbatch.home.desc")
        branch_btn = t("fbbatch.home.open")

        header_copy = ctk.CTkFrame(self.header_frame, fg_color="transparent")
        header_copy.pack(side="left", fill="x", expand=True, anchor="w")

        self.welcome_lbl = ctk.CTkLabel(
            header_copy,
            text=welcome_title,
            font=ctk.CTkFont(size=26, weight="bold"),
            text_color=("#0f172a", "#ffffff"),
            anchor="w"
        )
        self.welcome_lbl.pack(anchor="w")

        self.subtitle_lbl = ctk.CTkLabel(
            header_copy,
            text=welcome_subtitle,
            font=ctk.CTkFont(size=14),
            text_color=("gray45", "gray55"),
            anchor="w"
        )
        self.subtitle_lbl.pack(anchor="w", pady=(5, 0))

        icon_path = ASSETS_DIR / "vpn_switcher.png"
        self._vpn_icon = ctk.CTkImage(
            light_image=Image.open(icon_path),
            dark_image=Image.open(icon_path),
            size=(48, 48),
        )
        self.vpn_status_button = ctk.CTkButton(
            self.header_frame,
            text=t("vpn.nav"),
            image=self._vpn_icon,
            compound="left",
            width=180,
            height=72,
            corner_radius=8,
            border_width=2,
            border_color=("#94a3b8", "#64748b"),
            fg_color=("#ffffff", "#111827"),
            hover_color=("#f1f5f9", "#1e293b"),
            text_color=("#0f172a", "#f8fafc"),
            font=ctk.CTkFont(size=16, weight="bold"),
            command=lambda: self.app.show_view("vpn"),
        )
        self.vpn_status_button.pack(side="right", padx=(20, 0))

        # Cards Grid Layout Frame
        self.cards_frame = ctk.CTkFrame(self.scroll_container, fg_color="transparent")
        self.cards_frame.pack(fill="both", expand=True, pady=10)
        self.cards_frame.grid_columnconfigure((0, 1), weight=1, uniform="home_cards")
        self.cards_frame.grid_rowconfigure((0, 1), weight=1, uniform="home_cards")

        # ── Card 1: Consumer Lending (CL) Spools ──
        self.cl_card = CardFrame(self.cards_frame)
        self.cl_card.grid(row=0, column=0, padx=10, pady=10, sticky="nsew")
        self._build_card_content(
            self.cl_card,
            title=cl_title,
            description=cl_desc,
            btn_text=cl_btn,
            command=self._on_spools_cl
        )

        # ── Card 2: Savings Spools ──
        self.sav_card = CardFrame(self.cards_frame)
        self.sav_card.grid(row=0, column=1, padx=10, pady=10, sticky="nsew")
        self._build_card_content(
            self.sav_card,
            title=sav_title,
            description=sav_desc,
            btn_text=sav_btn,
            command=self._on_savings_accounts
        )

        # ── Card 3: CMR Chile ──
        self.cmr_card = CardFrame(self.cards_frame)
        self.cmr_card.grid(row=1, column=0, padx=10, pady=10, sticky="nsew")
        self._build_card_content(
            self.cmr_card,
            title=cmr_title,
            description=cmr_desc,
            btn_text=cmr_btn,
            command=self._on_cmr_chile
        )

        # ── Card 4: Create Falabella Branch ──
        self.branch_card = CardFrame(self.cards_frame)
        self.branch_card.grid(row=1, column=1, padx=10, pady=10, sticky="nsew")
        self._build_card_content(
            self.branch_card,
            title=branch_title,
            description=branch_desc,
            btn_text=branch_btn,
            command=self._on_fbbatch
        )

    def _build_card_content(self, card: CardFrame, title: str, description: str, btn_text: str, command: callable, disabled: bool = False) -> None:
        # Card inner padding container
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=20, pady=20)
        inner.grid_rowconfigure(1, weight=1)  # Description stretches vertically

        # Title Label
        title_lbl = ctk.CTkLabel(
            inner,
            text=title,
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color=("#1e293b", "#f8fafc"),
            anchor="w",
            justify="left"
        )
        title_lbl.pack(fill="x", anchor="w", pady=(0, 12))

        # Description Label
        desc_lbl = ctk.CTkLabel(
            inner,
            text=description,
            font=ctk.CTkFont(size=12),
            text_color=("gray45", "gray60"),
            justify="left",
            wraplength=220,
            anchor="nw"
        )
        desc_lbl.pack(fill="both", expand=True, pady=(0, 20))

        # Card Button
        if disabled:
            btn = ctk.CTkButton(
                inner,
                text=btn_text,
                height=36,
                corner_radius=8,
                state="disabled",
                fg_color=("#e2e8f0", "#2d3748"),
                text_color=("#94a3b8", "#718096"),
                font=ctk.CTkFont(size=12, weight="bold")
            )
        else:
            btn = IconButton(
                inner,
                text=btn_text,
                height=36,
                command=command,
                font=ctk.CTkFont(size=12, weight="bold")
            )
        btn.pack(fill="x", side="bottom")

    def _on_spools_cl(self) -> None:
        self.app.show_consumer_lending()

    def _on_savings_accounts(self) -> None:
        self.app.show_view("spools_savings")

    def _on_cmr_chile(self) -> None:
        self.app.show_cmr_chile()

    def _on_fbbatch(self) -> None:
        self.app.show_view("fbbatch")
