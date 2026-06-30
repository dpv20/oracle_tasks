"""Bridge to an installed VPN Switcher without opening its main window."""
from __future__ import annotations

import importlib.util
import json
import logging
import os
import subprocess
import sys
import threading
import time
import winreg
import warnings
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from paths import DATA_DIR


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

VPN_SWITCHER_INSTALL_URL = (
    "https://raw.githubusercontent.com/dpv20/oracle_vpn/main/install.bat"
)
_STARTUP_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_STARTUP_VALUE = "VPNSwitcher"

ProgressCallback = Callable[[str], None]


@dataclass(frozen=True)
class VPNInstallation:
    root: Path | None
    source_dir: Path | None
    config_path: Path

    @property
    def available(self) -> bool:
        if not self.root or not self.source_dir:
            return False
        required = (
            self.source_dir / "vpn_controller.py",
            self.source_dir / "config_manager.py",
            self.source_dir / "logger.py",
            self.source_dir / "main.py",
        )
        return all(path.is_file() for path in required)


@dataclass(frozen=True)
class VPNResult:
    ok: bool
    message: str
    status: str = NONE


def discover_vpn_switcher() -> VPNInstallation:
    local_app_data = Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
    app_data = Path(os.environ.get("APPDATA", str(Path.home())))
    config_path = app_data / "VPNSwitcher" / "config.json"

    override = os.environ.get("VPN_SWITCHER_ROOT", "").strip()
    candidates = []
    if override:
        candidates.append(Path(override).expanduser())
    candidates.extend(
        (
            local_app_data / "VPNSwitcher" / "app",
            local_app_data / "VPNSwitcher",
        )
    )
    for root in candidates:
        source_dir = root / "src"
        installation = VPNInstallation(root, source_dir, config_path)
        if installation.available:
            return installation
    return VPNInstallation(None, None, config_path)


class VPNSwitcherBridge:
    """Use VPN Switcher's installed controller and per-user configuration."""

    def __init__(self) -> None:
        self.installation = discover_vpn_switcher()
        self._controller = None
        self._controller_module = None
        self._config_defaults: dict = {}
        self._load_lock = threading.Lock()
        self._operation_lock = threading.Lock()

    @property
    def available(self) -> bool:
        return self.installation.available

    @property
    def install_path(self) -> Path | None:
        return self.installation.root

    @property
    def configured(self) -> bool:
        return self.installation.config_path.is_file()

    def rediscover(self) -> bool:
        installation = discover_vpn_switcher()
        if installation.source_dir != self.installation.source_dir:
            self._controller = None
            self._controller_module = None
            self._config_defaults = {}
        self.installation = installation
        return self.available

    def get_status(self) -> str:
        with _com_apartment():
            controller = self._get_controller()
            self._reload_controller_config(controller)
            return _read_controller_status(controller)

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
        self.ensure_background_running()
        self._clear_autofill_cancel()

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
                "FortiClient rejected the saved password. Update it in VPN Switcher Settings.",
                self._safe_status(controller),
            )
        time.sleep(3)
        return VPNResult(ok, message, self._safe_status(controller))

    def ensure_background_running(self) -> tuple[bool, str]:
        if not self.available:
            return False, "VPN Switcher is not installed."
        if self.is_background_running():
            return True, "VPN Switcher background process is running."
        if not self.configured:
            return False, "VPN Switcher must be opened once to finish its setup."

        command = _read_startup_command()
        try:
            if command:
                subprocess.Popen(
                    command,
                    cwd=str(self.installation.root),
                    creationflags=_background_creation_flags(),
                    close_fds=True,
                )
            else:
                pythonw = Path(sys.executable).with_name("pythonw.exe")
                executable = pythonw if pythonw.is_file() else Path(sys.executable)
                subprocess.Popen(
                    [str(executable), str(self.installation.source_dir / "main.py")],
                    cwd=str(self.installation.root),
                    creationflags=_background_creation_flags(),
                    close_fds=True,
                )
        except OSError as exc:
            log.exception("Could not start VPN Switcher in the background")
            return False, f"Could not start VPN Switcher: {exc}"

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if self.is_background_running():
                return True, "VPN Switcher background process started."
            time.sleep(0.4)
        return False, "VPN Switcher was launched but its background process was not detected."

    def is_background_running(self) -> bool:
        if not self.available:
            return False
        target = str((self.installation.source_dir / "main.py").resolve()).lower()
        try:
            import psutil

            for process in psutil.process_iter(("cmdline",)):
                try:
                    command_line = " ".join(process.info.get("cmdline") or []).lower()
                    if target in command_line:
                        return True
                except (psutil.AccessDenied, psutil.NoSuchProcess):
                    continue
        except Exception:
            log.exception("Could not inspect VPN Switcher background process")
        return False

    def launch_installer(self) -> tuple[bool, str]:
        try:
            import requests

            response = requests.get(VPN_SWITCHER_INSTALL_URL, timeout=30)
            response.raise_for_status()
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            installer = DATA_DIR / "install_vpn_switcher.bat"
            installer.write_bytes(response.content)
            subprocess.Popen(
                ["cmd.exe", "/c", str(installer)],
                cwd=str(DATA_DIR),
                creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
            )
            return True, "VPN Switcher installer opened. Complete it, then refresh this tab."
        except Exception as exc:
            log.exception("Could not launch VPN Switcher installer")
            return False, f"Could not launch the VPN Switcher installer: {exc}"

    def _get_controller(self):
        if not self.available:
            raise RuntimeError("VPN Switcher is not installed.")
        with self._load_lock:
            if self._controller is not None:
                return self._controller

            source_dir = self.installation.source_dir
            logger_module = _load_installed_module("logger", source_dir / "logger.py")
            config_module = _load_installed_module(
                "config_manager", source_dir / "config_manager.py"
            )
            controller_module = _load_installed_module(
                "vpn_controller", source_dir / "vpn_controller.py"
            )
            del logger_module
            self._config_defaults = dict(getattr(config_module, "DEFAULTS", {}))
            self._controller_module = controller_module
            self._controller = controller_module.VPNController(self._read_config())
            return self._controller

    def _read_config(self) -> dict:
        config = dict(self._config_defaults)
        try:
            loaded = json.loads(
                self.installation.config_path.read_text(encoding="utf-8")
            )
            if isinstance(loaded, dict):
                config.update(loaded)
        except FileNotFoundError:
            pass
        except (OSError, json.JSONDecodeError):
            log.exception("Could not read VPN Switcher configuration")
        return config

    def _reload_controller_config(self, controller) -> None:
        controller.config = self._read_config()

    def _clear_autofill_cancel(self) -> None:
        cancel = getattr(self._controller_module, "_autofill_cancel", None)
        if cancel is not None:
            cancel.clear()

    @staticmethod
    def _safe_status(controller) -> str:
        return _read_controller_status(controller)

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


def _read_controller_status(controller, attempts: int = 3) -> str:
    """Retry VPN Switcher's status read to absorb transient Get-NetAdapter failures."""
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
    """Initialize COM for the worker thread used by pywinauto UIA."""
    initialized = False
    try:
        # pythoncom initializes COM as part of its import, so select MTA first.
        if not hasattr(sys, "coinit_flags"):
            sys.coinit_flags = 0
        import pythoncom

        coinit_mta = getattr(pythoncom, "COINIT_MULTITHREADED", 0)
        try:
            pythoncom.CoInitializeEx(coinit_mta)
            initialized = True
        except Exception:
            # A host may already have initialized this thread as STA.
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
        if initialized:
            try:
                pythoncom.CoUninitialize()
            except Exception:
                log.exception("Could not uninitialize COM for VPN automation")


def _load_installed_module(name: str, path: Path):
    existing = sys.modules.get(name)
    existing_path = getattr(existing, "__file__", None) if existing else None
    if existing_path and Path(existing_path).resolve() == path.resolve():
        return existing
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load VPN Switcher module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _read_startup_command() -> str:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _STARTUP_KEY) as key:
            value, _ = winreg.QueryValueEx(key, _STARTUP_VALUE)
            return str(value).strip()
    except OSError:
        return ""


def _background_creation_flags() -> int:
    return (
        getattr(subprocess, "CREATE_NO_WINDOW", 0)
        | getattr(subprocess, "DETACHED_PROCESS", 0)
        | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    )


def _emit(callback: ProgressCallback | None, message: str) -> None:
    if callback:
        callback(message)
