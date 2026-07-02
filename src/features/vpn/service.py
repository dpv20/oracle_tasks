"""Thread-safe facade around the VPN controller embedded in Oracle Tasks."""
from __future__ import annotations

import logging
import sys
import threading
import time
import warnings
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Callable

from settings.config import ConfigManager, decrypt_password

log = logging.getLogger(__name__)

CISCO = "cisco"
FORTI = "forti"
GPROT = "globalprotect"
NONE = "disconnected"
VPN_TARGETS = (CISCO, FORTI, GPROT, NONE)

_FORTI_RETRY_ERRORS = (
    "window did not appear",
    "could not find connect",
    "did not open the sign-in window",
)

ProgressCallback = Callable[[str], None]
StatusCallback = Callable[[str], None]
MONITOR_INTERVAL_SECONDS = 15


@dataclass(frozen=True)
class VPNResult:
    ok: bool
    message: str
    status: str = NONE
    error_code: str = ""


class VPNService:
    """Expose the migrated VPN controller without a second application."""

    def __init__(self, config: ConfigManager | None = None) -> None:
        self.config = config or ConfigManager()
        self._controller = None
        self._controller_module = None
        self._load_lock = threading.Lock()
        self._operation_lock = threading.Lock()
        self._monitor_stop = threading.Event()
        self._monitor_thread: threading.Thread | None = None

    @property
    def busy(self) -> bool:
        return self._operation_lock.locked()

    def get_status(self) -> str:
        with self._operation_lock:
            with _com_apartment():
                controller = self._get_controller()
                self._reload_controller_config(controller)
                return _read_controller_status(controller)

    def try_get_status(self) -> str | None:
        """Return one quick status sample, or skip if a VPN operation is active."""
        if not self._operation_lock.acquire(blocking=False):
            return None
        try:
            with _com_apartment():
                controller = self._get_controller()
                self._reload_controller_config(controller)
                return _read_controller_status(controller, attempts=1)
        finally:
            self._operation_lock.release()

    def switch_to(
        self,
        target: str,
        progress: ProgressCallback | None = None,
    ) -> VPNResult:
        if target not in VPN_TARGETS:
            return VPNResult(False, f"Unsupported VPN target: {target}")
        with self._operation_lock:
            with _com_apartment():
                return self._switch_to(target, progress)

    def _switch_to(
        self,
        target: str,
        progress: ProgressCallback | None,
    ) -> VPNResult:
        controller = self._get_controller()
        self._reload_controller_config(controller)
        self._clear_autofill_cancel()

        _emit(progress, "Checking current VPN...")
        current = self._safe_status(controller)
        if target == NONE:
            return self._disconnect_everything(controller, progress)
        if current == target:
            return VPNResult(True, "VPN is already connected.", current)

        if current != NONE:
            _emit(progress, f"Disconnecting {status_display_name(current)}...")
            ok, message = self._disconnect(controller, current)
            if not ok:
                return VPNResult(False, message, self._safe_status(controller))
            time.sleep(1)
            remaining = self._safe_status(controller)
            if remaining not in (NONE, target):
                return VPNResult(
                    False,
                    f"Could not disconnect {status_display_name(remaining)}.",
                    remaining,
                )

        _emit(progress, f"Connecting {status_display_name(target)}...")
        ok, message = self._connect(controller, target)
        if target == FORTI and not ok and _is_recoverable_forti_error(message):
            log.warning("Transient FortiClient failure; retrying once: %s", message)
            _emit(progress, "FortiClient is still starting. Retrying...")
            time.sleep(2)
            self._clear_autofill_cancel()
            ok, message = self._connect(controller, target)
        if message == "__WRONG_PASSWORD__":
            return VPNResult(
                False,
                "FortiClient rejected the saved password. Update it in Settings > VPN.",
                self._safe_status(controller),
                "wrong_password",
            )
        if not ok and message.startswith("FortiClient was closed"):
            return VPNResult(False, message, NONE, "cancelled")
        if not ok:
            return VPNResult(False, message, self._safe_status(controller))
        time.sleep(3)
        return VPNResult(ok, message, self._safe_status(controller))

    def retry_forti_credentials(self) -> VPNResult:
        with self._operation_lock:
            with _com_apartment():
                controller = self._get_controller()
                self._reload_controller_config(controller)
                self._clear_autofill_cancel()
                ok, message = controller.retry_forti_credentials()
                if message == "__WRONG_PASSWORD__":
                    return VPNResult(
                        False,
                        "FortiClient rejected the saved password.",
                        self._safe_status(controller),
                        "wrong_password",
                    )
                time.sleep(2)
                return VPNResult(ok, message, self._safe_status(controller))

    def start_monitor(self, on_status: StatusCallback) -> None:
        """Keep visual VPN status current without changing any connection."""
        if self._monitor_thread and self._monitor_thread.is_alive():
            return
        self._monitor_stop.clear()

        def worker() -> None:
            while not self._monitor_stop.wait(MONITOR_INTERVAL_SECONDS):
                try:
                    status = self.try_get_status()
                    if status is not None:
                        on_status(status)
                except Exception:
                    log.exception("Background VPN status check failed")

        self._monitor_thread = threading.Thread(
            target=worker,
            daemon=True,
            name="vpn-status-monitor",
        )
        self._monitor_thread.start()

    def stop_monitor(self) -> None:
        self._monitor_stop.set()

    def _get_controller(self):
        with self._load_lock:
            if self._controller is not None:
                return self._controller
            import features.vpn.controller as controller_module

            self._controller_module = controller_module
            self._controller = controller_module.VPNController(self._read_config())
            return self._controller

    def _read_config(self) -> dict:
        data = dict(self.config.data)
        data["cisco_password"] = decrypt_password(
            str(data.get("cisco_password_enc") or "")
        )
        return data

    def _reload_controller_config(self, controller) -> None:
        controller.config = self._read_config()

    def _clear_autofill_cancel(self) -> None:
        cancel = getattr(self._controller_module, "_autofill_cancel", None)
        if cancel is not None:
            cancel.clear()

    @staticmethod
    def _safe_status(controller) -> str:
        return _read_controller_status(controller, attempts=1)

    @staticmethod
    def _connect(controller, target: str) -> tuple[bool, str]:
        methods = {
            CISCO: controller.connect_cisco,
            FORTI: controller.connect_forti,
            GPROT: controller.connect_globalprotect,
        }
        return methods[target]()

    @staticmethod
    def _disconnect(controller, status: str) -> tuple[bool, str]:
        methods = {
            CISCO: controller.disconnect_cisco,
            FORTI: controller.disconnect_forti,
            GPROT: controller.disconnect_globalprotect,
        }
        method = methods.get(status)
        return method() if method else (True, "No VPN was active.")

    def _disconnect_everything(
        self,
        controller,
        progress: ProgressCallback | None,
    ) -> VPNResult:
        for _ in range(3):
            current = self._safe_status(controller)
            if current == NONE:
                return VPNResult(True, "All VPN connections are disconnected.", NONE)
            _emit(progress, f"Disconnecting {status_display_name(current)}...")
            ok, message = self._disconnect(controller, current)
            if not ok:
                return VPNResult(False, message, self._safe_status(controller))
            time.sleep(1)
        status = self._safe_status(controller)
        return VPNResult(
            status == NONE,
            "All VPN connections are disconnected."
            if status == NONE
            else f"Could not disconnect {status_display_name(status)}.",
            status,
        )


def status_display_name(status: str) -> str:
    return {
        CISCO: "Oracle VPN (Cisco Secure Client)",
        FORTI: "Falabella VPN (FortiClient)",
        GPROT: "BICE VPN (GlobalProtect)",
        NONE: "No VPN",
    }.get(status, "Unknown VPN")


def _read_controller_status(controller, attempts: int = 2) -> str:
    for attempt in range(max(1, attempts)):
        try:
            status = str(controller.get_status())
            if status in (CISCO, FORTI, GPROT):
                return status
        except Exception:
            log.exception("VPN status detection failed")
        if attempt + 1 < attempts:
            time.sleep(0.2)
    return NONE


def _is_recoverable_forti_error(message: str) -> bool:
    normalized = (message or "").lower()
    return any(fragment in normalized for fragment in _FORTI_RETRY_ERRORS)


@contextmanager
def _com_apartment():
    """Initialize COM for worker threads used by pywinauto UIA."""
    initialized = False
    pythoncom = None
    try:
        if not hasattr(sys, "coinit_flags"):
            sys.coinit_flags = 0
        import pythoncom as _pythoncom

        pythoncom = _pythoncom
        coinit_mta = getattr(pythoncom, "COINIT_MULTITHREADED", 0)
        try:
            pythoncom.CoInitializeEx(coinit_mta)
            initialized = True
        except Exception:
            pythoncom.CoInitialize()
            initialized = True
    except Exception:
        log.exception("Could not initialize COM for VPN automation")

    try:
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message=(
                    "Revert to STA COM threading mode|"
                    "Apply externally defined coinit_flags:.*"
                ),
                category=UserWarning,
                module="pywinauto",
            )
            yield
    finally:
        if initialized and pythoncom is not None:
            try:
                pythoncom.CoUninitialize()
            except Exception:
                log.exception("Could not uninitialize COM for VPN automation")


def _emit(callback: ProgressCallback | None, message: str) -> None:
    if callback:
        callback(message)
