"""System-tray controller for keeping Oracle Tasks alive in the background."""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from app_identity import APP_DISPLAY_NAME
from paths import ASSETS_DIR

log = logging.getLogger(__name__)


class TrayController:
    def __init__(
        self,
        on_open: Callable[[], None],
        on_exit: Callable[[], None],
        open_label: str,
        exit_label: str,
        on_settings: Callable[[], None] | None = None,
        on_vpn: Callable[[str], None] | None = None,
        labels: dict[str, str] | None = None,
        show_forti: Callable[[], bool] | None = None,
        show_bice: Callable[[], bool] | None = None,
    ) -> None:
        self._on_open = on_open
        self._on_exit = on_exit
        self._open_label = open_label
        self._exit_label = exit_label
        self._on_settings = on_settings
        self._on_vpn = on_vpn
        self._labels = labels or {}
        self._show_forti = show_forti or (lambda: True)
        self._show_bice = show_bice or (lambda: True)
        self._icon = None
        self._base_image = None
        self._status = "disconnected"

    def start(self) -> bool:
        if self._icon is not None:
            return True
        try:
            import pystray
            from PIL import Image

            source = ASSETS_DIR / "new_icon.png"
            if not source.is_file():
                source = ASSETS_DIR / "icono.ico"
            self._base_image = Image.open(source).convert("RGBA")
            menu_items: list[Any] = [
                pystray.MenuItem(self._open_label, self._open, default=True),
            ]
            if self._on_vpn:
                menu_items.extend((
                    pystray.Menu.SEPARATOR,
                    pystray.MenuItem(
                        self._labels.get("cisco", "Oracle VPN (Cisco)"),
                        lambda _icon=None, _item=None: self._vpn("cisco"),
                    ),
                    pystray.MenuItem(
                        self._labels.get("forti", "Falabella VPN (FortiClient)"),
                        lambda _icon=None, _item=None: self._vpn("forti"),
                        visible=lambda _item: self._show_forti(),
                    ),
                    pystray.MenuItem(
                        self._labels.get("globalprotect", "BICE VPN (GlobalProtect)"),
                        lambda _icon=None, _item=None: self._vpn("globalprotect"),
                        visible=lambda _item: self._show_bice(),
                    ),
                    pystray.MenuItem(
                        self._labels.get("disconnected", "No VPN"),
                        lambda _icon=None, _item=None: self._vpn("disconnected"),
                    ),
                ))
            if self._on_settings:
                menu_items.extend((
                    pystray.Menu.SEPARATOR,
                    pystray.MenuItem(
                        self._labels.get("settings", "Settings"), self._settings
                    ),
                ))
            menu_items.append(pystray.MenuItem(self._exit_label, self._exit))
            menu = pystray.Menu(*menu_items)
            self._icon = pystray.Icon(
                "OracleTasksChile",
                self._render_icon(),
                APP_DISPLAY_NAME,
                menu,
            )
            self._icon.run_detached()
            return True
        except Exception as exc:
            self._icon = None
            log.exception("Could not start the system tray icon: %s", exc)
            return False

    def set_vpn_status(self, status: str) -> None:
        self._status = status
        if self._icon is None:
            return
        self._icon.icon = self._render_icon()
        label = self._labels.get(status, "No VPN")
        self._icon.title = f"{APP_DISPLAY_NAME} - {label}"
        try:
            self._icon.update_menu()
        except Exception:
            pass

    def refresh_menu(self) -> None:
        if self._icon is None:
            return
        try:
            self._icon.update_menu()
        except Exception:
            pass

    def _render_icon(self):
        from PIL import ImageDraw

        image = self._base_image.copy()
        size = min(image.size)
        radius = max(4, size // 7)
        margin = max(2, size // 20)
        right = image.width - margin
        bottom = image.height - margin
        colors = {
            "cisco": "#cf3f32",
            "forti": "#16a34a",
            "globalprotect": "#2563eb",
            "disconnected": "#64748b",
        }
        draw = ImageDraw.Draw(image)
        bounds = (right - radius * 2, bottom - radius * 2, right, bottom)
        draw.ellipse(bounds, fill="white")
        inset = max(1, radius // 4)
        draw.ellipse(
            tuple(value + inset if index < 2 else value - inset for index, value in enumerate(bounds)),
            fill=colors.get(self._status, colors["disconnected"]),
        )
        return image

    def stop(self) -> None:
        icon, self._icon = self._icon, None
        if icon is None:
            return
        try:
            icon.stop()
        except Exception as exc:
            log.warning("Could not stop the system tray icon: %s", exc)

    def _open(self, _icon=None, _item=None) -> None:
        self._on_open()

    def _exit(self, _icon=None, _item=None) -> None:
        self._on_exit()

    def _settings(self, _icon=None, _item=None) -> None:
        if self._on_settings:
            self._on_settings()

    def _vpn(self, target: str) -> None:
        if self._on_vpn:
            self._on_vpn(target)
