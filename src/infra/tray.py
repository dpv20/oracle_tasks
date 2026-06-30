"""System-tray controller for keeping Oracle Tasks alive in the background."""
from __future__ import annotations

import logging
from collections.abc import Callable

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
    ) -> None:
        self._on_open = on_open
        self._on_exit = on_exit
        self._open_label = open_label
        self._exit_label = exit_label
        self._icon = None

    def start(self) -> bool:
        if self._icon is not None:
            return True
        try:
            import pystray
            from PIL import Image

            source = ASSETS_DIR / "new_icon.png"
            if not source.is_file():
                source = ASSETS_DIR / "icono.ico"
            image = Image.open(source).convert("RGBA")
            menu = pystray.Menu(
                pystray.MenuItem(self._open_label, self._open, default=True),
                pystray.MenuItem(self._exit_label, self._exit),
            )
            self._icon = pystray.Icon(
                "OracleTasksChile",
                image,
                APP_DISPLAY_NAME,
                menu,
            )
            self._icon.run_detached()
            return True
        except Exception as exc:
            self._icon = None
            log.exception("Could not start the system tray icon: %s", exc)
            return False

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
