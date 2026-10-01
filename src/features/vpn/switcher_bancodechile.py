"""Banco de Chile GlobalProtect automation isolated from legacy VPN flows."""

import os

import subprocess

import threading

import time

from typing import Optional, Tuple

from paths import DATA_DIR

from settings.config import decrypt_password

from features.vpn.logger import get_logger

_autofill_cancel = threading.Event()

BANCOCHILE = "bancochile"

GPROT_UNKNOWN = "globalprotect_unknown"

NONE = "disconnected"

BANCOCHILE_PORTAL = "bchmfa.bancochile.cl"

GP_EXE_CANDIDATES = [
    r"C:\Program Files\Palo Alto Networks\GlobalProtect\PanGPA.exe",
    r"C:\Program Files (x86)\Palo Alto Networks\GlobalProtect\PanGPA.exe",
]

GP_TITLE = "GlobalProtect"

GP_LOGIN_TITLE = "GlobalProtect Login"

GP_NOTIFICATION_TITLE_PREFIX = "GlobalProtect Notification"

GP_CLASS = "#32770"

GP_TASKBAR_CLASS = "Shell_TrayWnd"

GP_TRAY_OVERFLOW_CLASS = "TopLevelWindowForOverflowXamlIsland"

GP_TRAY_ITEM_AUTOID = "NotifyItemIcon"

GP_TRAY_ITEM_CLASS = "SystemTray.NormalButton"

GP_SHOW_HIDDEN_AUTOID = "SystemTrayIcon"

GP_BTN_CONNECT_AUTOID = "1160"

GP_STATUS_AUTOID = "1165"

GP_PORTAL_AUTOIDS = ("1128", "1119")

GP_ADAPTER_KEYWORDS = (
    "palo alto",
    "globalprotect",
    "global protect",
    "pangp",
)

GP_ADAPTER_DISCONNECTED_STATES = frozenset(
    {
        "disabled",
        "disconnected",
        "down",
        "not present",
        "lower layer down",
        "lowerlayerdown",
    }
)

GP_ADAPTER_PROBE_TIMEOUT_SECONDS = 3
GP_ADAPTER_CACHE_SECONDS = 0.75
GP_CONNECTED_ADAPTER_GRACE_SECONDS = 15.0
GP_POST_DISCONNECT_COOLDOWN_SECONDS = 4.0
GP_DISCONNECT_VERIFY_TIMEOUT_SECONDS = 20.0

_gp_adapter_cache_lock = threading.Lock()
_gp_adapter_cache: tuple[float, bool, str] = (0.0, False, "")

NO_WINDOW = subprocess.CREATE_NO_WINDOW

def _find_exe(candidates: list, override: str = "") -> Optional[str]:
    if override and os.path.exists(override):
        return override
    for path in candidates:
        if os.path.exists(path):
            return path
    return None

def _run(cmd, input_text=None, timeout=20) -> Tuple[int, str, str]:
    """Run a CLI command (hidden window). For GUI apps use _open_gui instead."""
    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            creationflags=NO_WINDOW,
        )
        stdout, stderr = proc.communicate(input=input_text, timeout=timeout)
        return proc.returncode, stdout, stderr
    except subprocess.TimeoutExpired:
        proc.kill()
        return -1, "", "Timed out"
    except Exception as e:
        return -1, "", str(e)

def _open_gui(exe_path: str) -> bool:
    """Open a GUI application mimicking a shell double-click (explicit working dir)."""
    log = get_logger()
    try:
        exists = os.path.exists(exe_path)
        size = os.path.getsize(exe_path) if exists else -1
        log.info(f"open_gui: exe={exe_path} exists={exists} size={size}")
        log.info(f"open_gui: process_cwd={os.getcwd()}")
    except Exception as e:
        log.warning(f"open_gui: pre-launch probe failed: {e}")
    try:
        import ctypes
        work_dir = os.path.dirname(exe_path)
        log.info(f"open_gui: ShellExecuteW work_dir={work_dir}")
        # SW_SHOWNORMAL = 1. ShellExecuteW returns >32 on success.
        result = ctypes.windll.shell32.ShellExecuteW(
            None, "open", exe_path, None, work_dir, 1
        )
        log.info(f"open_gui: ShellExecuteW returned {result}")
        if result > 32:
            return True
        log.warning(f"open_gui: ShellExecuteW failed (code<=32), falling back to explorer.exe")
    except Exception as e:
        log.warning(f"open_gui: ShellExecuteW exception: {e}")
    try:
        subprocess.Popen(["explorer.exe", exe_path])
        log.info("open_gui: fallback explorer.exe launch issued")
        return True
    except Exception as e:
        log.error(f"open_gui: explorer.exe fallback failed: {e}")
        return False

def _force_foreground(hwnd) -> bool:
    """Force a window to the foreground, bypassing Windows restrictions."""
    import ctypes
    import ctypes.wintypes
    user32 = ctypes.windll.user32

    # Get the thread of the current foreground window
    fg_hwnd = user32.GetForegroundWindow()
    fg_thread = user32.GetWindowThreadProcessId(fg_hwnd, None)
    our_thread = user32.GetWindowThreadProcessId(hwnd, None)

    # Attach our thread input to the foreground thread — this lets us steal focus
    if fg_thread != our_thread:
        user32.AttachThreadInput(fg_thread, our_thread, True)

    user32.ShowWindow(hwnd, 9)          # SW_RESTORE
    user32.BringWindowToTop(hwnd)
    user32.SetForegroundWindow(hwnd)
    user32.SetFocus(hwnd)

    if fg_thread != our_thread:
        user32.AttachThreadInput(fg_thread, our_thread, False)

    return True

def _get_adapter_status(
    log_matches: bool = False,
    *,
    timeout: float = GP_ADAPTER_PROBE_TIMEOUT_SECONDS,
) -> dict:
    """Use PowerShell Get-NetAdapter to check adapter states.
    Returns dict like {'cisco': 'Up', 'forti': 'Disabled', ...}
    """
    result = {"_probe_ok": False}
    log = get_logger()
    try:
        rc, out, err = _run(
            ["powershell.exe", "-NoProfile", "-Command",
             "Get-NetAdapter | Select-Object Name, InterfaceDescription, Status | "
             "Format-List"],
            timeout=max(0.1, float(timeout)),
        )
        if rc != 0:
            log.warning("adapter_status: Get-NetAdapter failed rc=%s err=%r", rc, err)
            return result
        result["_probe_ok"] = True
        current_name = ""
        current_desc = ""
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("Name"):
                current_name = line.split(":", 1)[1].strip().lower()
            elif line.startswith("InterfaceDescription"):
                current_desc = line.split(":", 1)[1].strip().lower()
            elif line.startswith("Status"):
                status = line.split(":", 1)[1].strip()
                haystack = f"{current_name} {current_desc}"
                if "cisco" in haystack or "anyconnect" in haystack:
                    result["cisco"] = status
                elif "fortinet" in haystack and "ssl" in haystack:
                    result["forti_ssl"] = status
                elif "fortinet" in haystack:
                    result["forti_ndis"] = status
                elif any(keyword in haystack for keyword in GP_ADAPTER_KEYWORDS):
                    prev = result.get("globalprotect", "")
                    if not prev or status.lower() == "up":
                        result["globalprotect"] = status
                    result.setdefault("globalprotect_all", []).append(status)
                    if log_matches:
                        log.info(
                            "adapter_status: globalprotect match "
                            f"name='{current_name}' desc='{current_desc}' status='{status}'"
                        )
    except Exception:
        pass
    return result

def _gp_adapter_status() -> str:
    """Return the best GlobalProtect adapter status seen by Get-NetAdapter."""
    _ok, status = _gp_adapter_status_sample()
    return status


def _gp_adapter_status_sample(
    *,
    max_age: float = GP_ADAPTER_CACHE_SECONDS,
    probe_timeout: float | None = None,
) -> tuple[bool, str]:
    """Return a cached tri-state adapter sample: probe success plus GP state."""
    global _gp_adapter_cache
    probe_started_at = time.monotonic()
    with _gp_adapter_cache_lock:
        sampled_at, probe_ok, status = _gp_adapter_cache
        if (
            sampled_at
            and probe_started_at - sampled_at <= max(0.0, max_age)
        ):
            return probe_ok, status

    # Do not hold the cache lock while PowerShell runs. A background status
    # refresh may already be probing; serializing both external commands would
    # let an action exceed its own disconnect deadline before its bounded probe
    # even starts.
    try:
        adapters = (
            _get_adapter_status()
            if probe_timeout is None
            else _get_adapter_status(timeout=probe_timeout)
        )
        probe_ok = bool(adapters.get("_probe_ok", True))
        status = (adapters.get("globalprotect") or "").strip()
    except Exception:
        probe_ok = False
        status = ""
    sampled_at = time.monotonic()
    with _gp_adapter_cache_lock:
        cached_at, cached_ok, cached_status = _gp_adapter_cache
        if cached_at > probe_started_at:
            return cached_ok, cached_status
        _gp_adapter_cache = (sampled_at, probe_ok, status)
    return probe_ok, status

def _gp_diagnostics():
    """Verbose dump of every GlobalProtect-related process and window for
    log-based debugging. Mirrors _forti_diagnostics."""
    log = get_logger()
    # Processes
    try:
        import psutil
        procs = []
        for p in psutil.process_iter(["pid", "name", "exe"]):
            try:
                name = (p.info.get("name") or "").lower()
                if "pan" in name or "globalprotect" in name:
                    procs.append(f"{p.info['pid']}:{p.info['name']}:{p.info.get('exe') or ''}")
            except Exception:
                pass
        log.info(f"gp_diag: processes={procs or 'NONE'}")
    except Exception as e:
        log.warning(f"gp_diag: process enum failed: {e}")

    # Windows (visible + hidden)
    try:
        import ctypes
        user32 = ctypes.windll.user32
        matches = []
        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

        def _enum(hwnd, _):
            length = user32.GetWindowTextLengthW(hwnd)
            if length:
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                title = buf.value
                low = title.lower()
                if "globalprotect" in low or "palo alto" in low:
                    visible = bool(user32.IsWindowVisible(hwnd))
                    cls_buf = ctypes.create_unicode_buffer(256)
                    user32.GetClassNameW(hwnd, cls_buf, 256)
                    matches.append(
                        f"hwnd={hwnd} visible={visible} class='{cls_buf.value}' title='{title}'"
                    )
            return True

        user32.EnumWindows(WNDENUMPROC(_enum), 0)
        if matches:
            for m in matches:
                log.info(f"gp_diag: window {m}")
        else:
            log.info("gp_diag: no GlobalProtect windows found")
    except Exception as e:
        log.warning(f"gp_diag: window enum failed: {e}")

    # Exe metadata
    try:
        gp_exe = _find_exe(GP_EXE_CANDIDATES, "")
        if gp_exe:
            exists = os.path.exists(gp_exe)
            size = os.path.getsize(gp_exe) if exists else -1
            log.info(f"gp_diag: gp_exe={gp_exe} exists={exists} size={size}")
        else:
            log.info("gp_diag: PanGPA.exe not found in candidate paths")
    except Exception as e:
        log.warning(f"gp_diag: exe metadata failed: {e}")

    # Adapter metadata. PanGPA's UI status can disappear while the embedded
    # SAML window is active, so the virtual adapter is the most useful fallback.
    try:
        adapters = _get_adapter_status(log_matches=True)
        gp_status = adapters.get("globalprotect", "")
        if gp_status:
            all_statuses = adapters.get("globalprotect_all", [])
            log.info(f"gp_diag: adapter globalprotect='{gp_status}' all={all_statuses}")
        else:
            log.info("gp_diag: no GlobalProtect/PANGP adapter matched Get-NetAdapter")
    except Exception as e:
        log.warning(f"gp_diag: adapter probe failed: {e}")

def _gp_find_main_hwnds() -> list[int]:
    """List exact GlobalProtect main-window handles without global UIA scans."""
    try:
        import ctypes
        import ctypes.wintypes

        user32 = ctypes.windll.user32
        found: list[tuple[int, bool]] = []
        WNDENUMPROC = ctypes.WINFUNCTYPE(
            ctypes.c_bool,
            ctypes.wintypes.HWND,
            ctypes.wintypes.LPARAM,
        )

        def _enum(hwnd, _):
            length = user32.GetWindowTextLengthW(hwnd)
            if length:
                title = ctypes.create_unicode_buffer(length + 1)
                class_name = ctypes.create_unicode_buffer(256)
                user32.GetWindowTextW(hwnd, title, length + 1)
                user32.GetClassNameW(hwnd, class_name, 256)
                handle = _gp_hwnd_value(hwnd)
                if (
                    title.value == GP_TITLE
                    and class_name.value == GP_CLASS
                    and _gp_is_exact_pangpa_window(handle, GP_TITLE)
                ):
                    found.append((handle, bool(user32.IsWindowVisible(hwnd))))
            return True

        user32.EnumWindows(WNDENUMPROC(_enum), 0)
        found.sort(key=lambda item: item[1], reverse=True)
        return [hwnd for hwnd, _visible in found]
    except Exception:
        return []


def _gp_get_window(timeout: float = 1.0):
    """Find the GlobalProtect main window via pywinauto UIA backend.
    Matches title=='GlobalProtect' AND class=='#32770' (filters out random
    dialogs). Returns the window wrapper or None."""
    log = get_logger()
    try:
        from pywinauto import Desktop
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                desktop = Desktop(backend="uia")
                wins = []
                for w in desktop.windows():
                    try:
                        if (w.window_text() == GP_TITLE
                                and w.element_info.class_name == GP_CLASS):
                            wins.append(w)
                    except Exception:
                        pass
                if wins:
                    # Several hidden PanGPA top-level windows can coexist.
                    # Reject empty/stale shells entirely, then prefer a visible,
                    # fully-hydrated main window instead of whichever HWND
                    # Desktop.windows() happens to return first.
                    def _score(candidate):
                        score = 0
                        has_button = bool(
                            _gp_find_descendant_by_autoid(
                                candidate,
                                GP_BTN_CONNECT_AUTOID,
                            )
                        )
                        has_status = bool(
                            _gp_find_descendant_by_autoid(candidate, GP_STATUS_AUTOID)
                        )
                        if not (has_button or has_status):
                            return None
                        try:
                            score += 10 if candidate.is_visible() else 0
                        except Exception:
                            pass
                        score += 3 if has_button else 0
                        score += 2 if has_status else 0
                        if _gp_find_portal_control(candidate):
                            score += 1
                        return score

                    hydrated = [
                        (score, candidate)
                        for candidate in wins
                        if (score := _score(candidate)) is not None
                    ]
                    if hydrated:
                        return max(hydrated, key=lambda item: item[0])[1]
            except Exception as e:
                log.debug(f"gp_get_window: iteration error: {e}")
            time.sleep(0.4)
    except Exception as e:
        log.warning(f"gp_get_window: failed: {e}")
    return None


def _gp_find_monitor_control(win, control_id: str):
    """Read a native MFC control without using the action UIA tree."""
    hidden_match = None
    try:
        for ctrl in win.descendants():
            try:
                if str(ctrl.control_id()) != str(control_id):
                    continue
                if ctrl.is_visible():
                    return ctrl
                if hidden_match is None:
                    hidden_match = ctrl
            except Exception:
                continue
    except Exception:
        pass
    return hidden_match


def _gp_find_monitor_portal_control(win):
    for control_id in GP_PORTAL_AUTOIDS:
        ctrl = _gp_find_monitor_control(win, control_id)
        if ctrl:
            return ctrl
    return None


def _gp_get_monitor_window(timeout: float = 1.0):
    """Get a passive native snapshot; never focus or activate the GP popup.

    Native HWND enumeration avoids background desktop-wide UIA scans. These
    Win32 wrappers are only for reading status; connection actions use the
    established UIA window and AutomationId controls independently.
    """
    log = get_logger()
    try:
        from pywinauto import Desktop

        deadline = time.monotonic() + max(0.0, timeout)
        desktop = Desktop(backend="win32")
        while time.monotonic() < deadline:
            hydrated = []
            for hwnd in _gp_find_main_hwnds():
                try:
                    win = desktop.window(handle=hwnd).wrapper_object()
                    button = _gp_find_monitor_control(win, GP_BTN_CONNECT_AUTOID)
                    status_control = _gp_find_monitor_control(win, GP_STATUS_AUTOID)
                    try:
                        button_text = (button.window_text() or "").strip().casefold()
                    except Exception:
                        button_text = ""
                    try:
                        status_text = (status_control.window_text() or "").strip()
                    except Exception:
                        status_text = ""
                    portal_control = _gp_find_monitor_portal_control(win)
                    if "connect" not in button_text or not (status_text or portal_control):
                        continue
                    try:
                        score = 10 if win.is_visible() else 0
                    except Exception:
                        score = 0
                    score += 3 + (2 if status_text else 0) + (1 if portal_control else 0)
                    hydrated.append((score, win))
                except Exception as exc:
                    log.debug("gp_monitor: could not read hwnd=%s: %s", hwnd, exc)
            if hydrated:
                return max(hydrated, key=lambda item: item[0])[1]
            remaining = deadline - time.monotonic()
            if remaining > 0:
                time.sleep(min(0.4, remaining))
    except Exception as exc:
        log.debug("gp_monitor: native snapshot failed: %s", exc)
    return None


def _gp_get_monitor_portal_text(win) -> str:
    ctrl = _gp_find_monitor_portal_control(win)
    if not ctrl:
        return ""
    candidates = []
    try:
        candidates.append(ctrl.window_text())
    except Exception:
        pass
    try:
        candidates.append(ctrl.get_value())
    except Exception:
        pass
    try:
        legacy = ctrl.legacy_properties()
        candidates.extend((legacy.get("Value", ""), legacy.get("Name", "")))
    except Exception:
        pass
    short_candidate = ""
    for candidate in candidates:
        normalized = _normalize_gp_portal(candidate)
        if normalized and "." in normalized:
            return normalized
        if normalized and normalized != "portal":
            short_candidate = normalized
    return short_candidate


def _gp_get_monitor_status_text(win) -> str:
    try:
        ctrl = _gp_find_monitor_control(win, GP_STATUS_AUTOID)
        return (ctrl.window_text() or "").strip() if ctrl else ""
    except Exception:
        return ""




def _gp_process_running(process_name: str) -> bool:
    try:
        import psutil

        expected = str(process_name or "").casefold()
        return any(
            str(proc.info.get("name") or "").casefold() == expected
            for proc in psutil.process_iter(["name"])
        )
    except Exception:
        return False


def _gp_find_exact_top_level_hwnd(window_class: str) -> int | None:
    """Return a visible top-level HWND whose native class matches exactly."""
    try:
        import ctypes
        import ctypes.wintypes

        user32 = ctypes.windll.user32
        try:
            user32.FindWindowExW.argtypes = (
                ctypes.wintypes.HWND,
                ctypes.wintypes.HWND,
                ctypes.wintypes.LPCWSTR,
                ctypes.wintypes.LPCWSTR,
            )
            user32.FindWindowExW.restype = ctypes.wintypes.HWND
            previous = ctypes.wintypes.HWND(0)
            while True:
                hwnd = user32.FindWindowExW(
                    ctypes.wintypes.HWND(0),
                    previous,
                    window_class,
                    None,
                )
                if not hwnd:
                    break
                if user32.IsWindowVisible(hwnd):
                    return _gp_hwnd_value(hwnd)
                previous = hwnd
        except Exception:
            # EnumWindows below is an independent fallback for Explorer/XAML
            # versions where FindWindowExW intermittently misses the island.
            pass

        found: list[int] = []
        WNDENUMPROC = ctypes.WINFUNCTYPE(
            ctypes.c_bool,
            ctypes.wintypes.HWND,
            ctypes.wintypes.LPARAM,
        )

        def _enum(hwnd, _):
            if not user32.IsWindowVisible(hwnd):
                return True
            class_name = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, class_name, 256)
            if class_name.value == window_class:
                found.append(_gp_hwnd_value(hwnd))
            return True

        user32.EnumWindows(WNDENUMPROC(_enum), 0)
        return found[0] if found else None
    except Exception as exc:
        get_logger().debug(
            "gp_tray: native class lookup failed for %s: %s",
            window_class,
            exc,
        )
        return None


def _gp_uia_control_text(control) -> str:
    try:
        return (control.window_text() or "").strip()
    except Exception:
        try:
            return (control.element_info.name or "").strip()
        except Exception:
            return ""


def _gp_is_exact_tray_icon(control) -> bool:
    """Accept only Explorer's exact GlobalProtect notification-area item."""
    try:
        info = control.element_info
        name = _gp_uia_control_text(control).casefold()
        return (
            info.control_type == "Button"
            and info.automation_id == GP_TRAY_ITEM_AUTOID
            and info.class_name == GP_TRAY_ITEM_CLASS
            and (name == "globalprotect" or name.startswith("globalprotect "))
        )
    except Exception:
        return False


def _gp_find_exact_tray_icon(container):
    """Return one unambiguous GlobalProtect tray icon from an exact subtree."""
    try:
        matches = [
            control
            for control in container.descendants()
            if _gp_is_exact_tray_icon(control)
        ]
    except Exception:
        return None
    if len(matches) != 1:
        if len(matches) > 1:
            get_logger().warning(
                "gp_tray: refusing ambiguous GlobalProtect icons count=%s",
                len(matches),
            )
        return None
    return matches[0]


def _gp_is_exact_show_hidden_button(control) -> bool:
    try:
        info = control.element_info
        name = _gp_uia_control_text(control).casefold()
        return (
            info.control_type == "Button"
            and info.automation_id == GP_SHOW_HIDDEN_AUTOID
            and info.class_name == GP_TRAY_ITEM_CLASS
            and name == "show hidden icons"
        )
    except Exception:
        return False


def _gp_find_show_hidden_button(taskbar):
    """Find Windows 11's notification-overflow toggle inside the taskbar."""
    try:
        for control in taskbar.descendants():
            if _gp_is_exact_show_hidden_button(control):
                return control
    except Exception:
        pass
    return None


def _gp_main_window_is_visible(win) -> bool:
    try:
        return bool(win and win.is_visible())
    except Exception:
        return False


def _gp_any_native_main_window_visible() -> bool:
    """Detect a visible exact PanGPA popup even before controls hydrate."""
    try:
        import ctypes

        user32 = ctypes.windll.user32
        return any(
            bool(user32.IsWindowVisible(hwnd))
            for hwnd in _gp_find_main_hwnds()
        )
    except Exception:
        return False


def _gp_uia_control_is_actionable(control) -> bool:
    try:
        if not control.is_visible() or not control.is_enabled():
            return False
        rect = control.rectangle()
        return rect.width() > 0 and rect.height() > 0
    except Exception:
        return False


def _gp_activate_overflow_toggle(toggle) -> bool:
    """Use one revalidated physical click to show Explorer's tray overflow."""
    if not _gp_is_exact_show_hidden_button(toggle):
        return False
    if not _gp_uia_control_is_actionable(toggle):
        return False
    try:
        toggle.click_input()
        return True
    except Exception as exc:
        get_logger().warning("gp_tray: overflow toggle action failed: %s", exc)
        return False


def _gp_activate_from_system_tray(timeout: float = 4.0) -> bool:
    """Click the exact GlobalProtect tray icon at most once.

    Explorer's Windows 11 notification area is XAML-backed. Native window
    enumeration first scopes UIA to the exact taskbar/overflow HWNDs, avoiding
    a potentially blocking desktop-wide UIA scan. This helper only opens the
    PanGPA popup; it never touches its Connect/Disconnect control.
    """
    log = get_logger()
    try:
        from pywinauto import Desktop

        taskbar_hwnd = _gp_find_exact_top_level_hwnd(GP_TASKBAR_CLASS)
        if not taskbar_hwnd:
            log.info("gp_tray: Shell_TrayWnd not found")
            return False
        desktop = Desktop(backend="uia")
        taskbar = desktop.window(handle=taskbar_hwnd).wrapper_object()

        icon = _gp_find_exact_tray_icon(taskbar)
        overflow_hwnd = _gp_find_exact_top_level_hwnd(GP_TRAY_OVERFLOW_CLASS)
        if not icon and not overflow_hwnd:
            toggle = _gp_find_show_hidden_button(taskbar)
            if not toggle:
                log.info("gp_tray: Show Hidden Icons button not found")
                return False
            if not _gp_activate_overflow_toggle(toggle):
                return False
            log.info("gp_tray: notification overflow opened")

        deadline = time.monotonic() + max(0.2, timeout)
        while not icon and time.monotonic() < deadline:
            if _autofill_cancel.is_set():
                return False

            # The icon can be configured as always visible, or migrate there
            # while PanGPA starts. Re-wrap only the exact taskbar HWND.
            try:
                taskbar = desktop.window(handle=taskbar_hwnd).wrapper_object()
                icon = _gp_find_exact_tray_icon(taskbar)
            except Exception:
                icon = None

            if not icon:
                overflow_hwnd = _gp_find_exact_top_level_hwnd(
                    GP_TRAY_OVERFLOW_CLASS
                )
                if overflow_hwnd:
                    try:
                        overflow = desktop.window(
                            handle=overflow_hwnd
                        ).wrapper_object()
                        icon = _gp_find_exact_tray_icon(overflow)
                    except Exception:
                        icon = None
            if not icon and _autofill_cancel.wait(0.2):
                return False

        if not icon:
            log.info("gp_tray: exact GlobalProtect icon not found")
            return False

        # A manual click can race this worker. Never invoke the tray item if
        # the popup has become visible, because that would hide it again.
        live_window = _gp_get_window(timeout=0.2)
        if (
            _gp_main_window_is_visible(live_window)
            or _gp_any_native_main_window_visible()
        ):
            log.info("gp_tray: main popup became visible before tray click")
            return True

        if not _gp_is_exact_tray_icon(icon) or not _gp_uia_control_is_actionable(icon):
            log.warning("gp_tray: icon identity/visibility changed before click")
            return False
        try:
            icon.click_input()
            log.info(
                "gp_tray: clicked exact notification icon name=%r",
                _gp_uia_control_text(icon),
            )
            return True
        except Exception as exc:
            # A physical click can be delivered before the wrapper raises. Do
            # not issue a second click or an InvokePattern fallback here.
            log.warning("gp_tray: exact icon click raised: %s", exc)
            return False
    except Exception as exc:
        log.warning("gp_tray: activation failed: %s", exc)
        return False


def _ensure_gp_main_window(exe_path: str, *, deadline: float | None = None):
    """Open PanGPA's popup through its exact notification-area icon."""
    log = get_logger()

    def _time_left() -> float | None:
        if deadline is None:
            return None
        return max(0.0, deadline - time.monotonic())

    def _bounded_timeout(requested: float) -> float:
        remaining = _time_left()
        if remaining is None:
            return requested
        return min(requested, remaining)

    initial_timeout = _bounded_timeout(0.6)
    if initial_timeout <= 0.0:
        return None
    existing = _gp_get_window(timeout=initial_timeout)
    if _gp_main_window_is_visible(existing):
        return existing

    launched = False
    if not _gp_process_running("PanGPA.exe"):
        log.info(
            "gp_window: starting PanGPA before tray activation (PanGPS=%s)",
            _gp_process_running("PanGPS.exe"),
        )
        if (_time_left() == 0.0) or not _open_gui(exe_path):
            return existing
        launched = True

        # ShellExecute can occasionally open the popup itself. Give it a
        # bounded opportunity so a later tray invoke cannot toggle it closed.
        launch_deadline = time.monotonic() + 1.5
        if deadline is not None:
            launch_deadline = min(launch_deadline, deadline)
        while time.monotonic() < launch_deadline:
            if _autofill_cancel.is_set():
                return None
            lookup_timeout = _bounded_timeout(0.25)
            if lookup_timeout <= 0.0:
                break
            launched_window = _gp_get_window(timeout=lookup_timeout)
            if _gp_main_window_is_visible(launched_window):
                return launched_window
            if launched_window:
                existing = launched_window
            wait_time = _bounded_timeout(0.15)
            if wait_time <= 0.0 or _autofill_cancel.wait(wait_time):
                return None

    if _autofill_cancel.is_set() or _time_left() == 0.0:
        return None
    activation_timeout = _bounded_timeout(8.0 if launched else 4.0)
    # The tray helper has a deliberate 200 ms minimum polling window. Do not
    # start it when the caller's operation has less time remaining than that.
    if activation_timeout < 0.2:
        return None
    activated = _gp_activate_from_system_tray(timeout=activation_timeout)
    if activated:
        visible_deadline = time.monotonic() + 4.0
        if deadline is not None:
            visible_deadline = min(visible_deadline, deadline)
        last_hydrated = existing
        while time.monotonic() < visible_deadline:
            if _autofill_cancel.is_set():
                return None
            lookup_timeout = _bounded_timeout(0.35)
            if lookup_timeout <= 0.0:
                break
            win = _gp_get_window(timeout=lookup_timeout)
            if win:
                last_hydrated = win
                if _gp_main_window_is_visible(win):
                    log.info("gp_window: visible after exact tray activation")
                    return win
            wait_time = _bounded_timeout(0.15)
            if wait_time <= 0.0 or _autofill_cancel.wait(wait_time):
                return None
        existing = last_hydrated

    # Ask PanGPA to expose its own active popup. ShowWindow on one of its hidden
    # MFC dialogs can expose an obsolete shell whose Connect button is inert.
    # Such a shell must never become our fallback action target.
    if exe_path and not _autofill_cancel.is_set() and _time_left() != 0.0:
        log.info("gp_window: requesting popup through PanGPA launcher")
        if not _open_gui(exe_path):
            return None
        restore_deadline = time.monotonic() + 2.0
        if deadline is not None:
            restore_deadline = min(restore_deadline, deadline)
        while time.monotonic() < restore_deadline:
            if _autofill_cancel.is_set():
                return None
            lookup_timeout = _bounded_timeout(0.25)
            if lookup_timeout <= 0.0:
                break
            restored = _gp_get_window(timeout=lookup_timeout)
            if _gp_main_window_is_visible(restored):
                return restored
            wait_time = _bounded_timeout(0.1)
            if wait_time <= 0.0 or _autofill_cancel.wait(wait_time):
                return None
    return None

def _gp_find_descendant_by_autoid(win, auto_id: str):
    """Walk descendants and return the first element whose UIA AutomationId matches."""
    try:
        for ctrl in win.descendants():
            try:
                if ctrl.element_info.automation_id == auto_id:
                    return ctrl
            except Exception:
                pass
    except Exception:
        pass
    return None

def _gp_get_status_text(win) -> str:
    """Read the status static (auto_id=1165). Returns text such as
    'Disconnected', 'Connecting...', 'Connected', or '' on failure."""
    log = get_logger()
    try:
        ctrl = _gp_find_descendant_by_autoid(win, GP_STATUS_AUTOID)
        if ctrl:
            text = (ctrl.window_text() or "").strip()
            log.info(f"gp_status: '{text}'")
            return text
        log.warning(f"gp_status: status static (auto_id={GP_STATUS_AUTOID}) not found")
    except Exception as e:
        log.warning(f"gp_status: read failed: {e}")
    return ""

def _gp_get_button_label(win) -> str:
    """Read the Connect/Disconnect pane label (auto_id=1160)."""
    try:
        ctrl = _gp_find_descendant_by_autoid(win, GP_BTN_CONNECT_AUTOID)
        if ctrl:
            return (ctrl.window_text() or "").strip()
    except Exception:
        pass
    return ""

def _normalize_gp_portal(value: object) -> str:
    portal = str(value or "").strip().lower()
    portal = portal.removeprefix("https://").removeprefix("http://")
    return portal.rstrip("/")

def _gp_portal_matches(displayed: object, configured: object) -> bool:
    """Match both full portal hosts and PanGPA's connected short label."""
    shown = _normalize_gp_portal(displayed)
    expected = _normalize_gp_portal(configured)
    if not shown or not expected:
        return False
    return shown == expected or shown == expected.split(".", 1)[0]

def _gp_get_portal_text(win) -> str:
    """Return the portal displayed by PanGPA's MFC portal control."""
    ctrl = _gp_find_portal_control(win)
    if not ctrl:
        return ""
    candidates = []
    try:
        candidates.append(ctrl.window_text())
    except Exception:
        pass
    try:
        candidates.append(ctrl.get_value())
    except Exception:
        pass
    try:
        legacy = ctrl.legacy_properties()
        candidates.extend((legacy.get("Value", ""), legacy.get("Name", "")))
    except Exception:
        pass
    short_candidate = ""
    for candidate in candidates:
        normalized = _normalize_gp_portal(candidate)
        if normalized and "." in normalized:
            return normalized
        if normalized and normalized != "portal":
            short_candidate = normalized
    return short_candidate

def _gp_find_portal_control(win):
    for auto_id in GP_PORTAL_AUTOIDS:
        ctrl = _gp_find_descendant_by_autoid(win, auto_id)
        if ctrl:
            return ctrl
    return None

def _gp_get_last_portal() -> str:
    """Read GlobalProtect's last portal as a status-only fallback."""
    try:
        import winreg

        key_path = r"SOFTWARE\Palo Alto Networks\GlobalProtect\Settings"
        views = (0, getattr(winreg, "KEY_WOW64_64KEY", 0), getattr(winreg, "KEY_WOW64_32KEY", 0))
        for view in views:
            try:
                with winreg.OpenKey(
                    winreg.HKEY_LOCAL_MACHINE,
                    key_path,
                    0,
                    winreg.KEY_READ | view,
                ) as key:
                    value, _kind = winreg.QueryValueEx(key, "LastUrl")
                normalized = _normalize_gp_portal(value)
                if normalized:
                    return normalized
            except OSError:
                continue
    except Exception:
        pass
    return ""

def _gp_set_portal(win, portal: str) -> bool:
    """Select and verify a portal in the disconnected GlobalProtect window."""
    log = get_logger()
    desired = _normalize_gp_portal(portal)
    if not desired:
        return False
    current = _gp_get_portal_text(win)
    if current == desired:
        log.info("gp_portal: desired portal already selected")
        return True

    ctrl = _gp_find_portal_control(win)
    if not ctrl:
        log.error("gp_portal: portal control not found")
        return False

    changed = False
    try:
        ctrl.set_edit_text(desired)
        changed = True
    except Exception:
        try:
            ctrl.click_input()
            ctrl.set_focus()
            from pywinauto.keyboard import send_keys

            send_keys("^a")
            send_keys(desired, with_spaces=True)
            changed = True
        except Exception as exc:
            log.error("gp_portal: could not enter selected portal: %s", exc)
            return False

    if changed:
        time.sleep(0.2)
    selected = _gp_get_portal_text(win)
    if selected != desired:
        log.error(
            "gp_portal: verification failed expected='%s' actual='%s'",
            desired,
            selected or "unknown",
        )
        return False
    log.info("gp_portal: selected portal='%s'", desired)
    return True

def _gp_login_window_present() -> bool:
    """Check whether the SAML 'GlobalProtect Login' sub-window is visible.
    Used during the connect polling loop to log whether we are waiting on user
    interaction inside the embedded WebView2 vs the tunnel just being slow."""
    try:
        import ctypes
        user32 = ctypes.windll.user32
        found = [False]
        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

        def _enum(hwnd, _):
            length = user32.GetWindowTextLengthW(hwnd)
            if length:
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                if (
                    buf.value == GP_LOGIN_TITLE
                    and _gp_is_exact_pangpa_window(hwnd, GP_LOGIN_TITLE)
                ):
                    if user32.IsWindowVisible(hwnd):
                        found[0] = True
                        return False
            return True

        user32.EnumWindows(WNDENUMPROC(_enum), 0)
        return found[0]
    except Exception:
        return False

def _gp_dump_descendants(win, max_items: int = 40):
    """Dump the first N descendants of the GlobalProtect window with their
    auto_id and class. Called when status/button auto_ids are not found, so
    we can detect if Palo Alto changed control IDs in a future version."""
    log = get_logger()
    try:
        log.info("gp_descendants: dumping window tree (auto_ids may have changed)")
        count = 0
        for ctrl in win.descendants():
            if count >= max_items:
                log.info(f"gp_descendants: truncated at {max_items} items")
                break
            try:
                info = ctrl.element_info
                log.info(
                    f"gp_descendants: type={getattr(info, 'control_type', '?')} "
                    f"auto_id='{getattr(info, 'automation_id', '')}' "
                    f"class='{getattr(info, 'class_name', '')}' "
                    f"name='{(ctrl.window_text() or '')[:60]}'"
                )
                count += 1
            except Exception:
                pass
    except Exception as e:
        log.warning(f"gp_descendants: dump failed: {e}")



def _gp_invoke_connect_button(
    win,
    *,
    expected_action: str,
    prefer_click_input: bool = True,
) -> bool:
    """Send one verified Connect/Disconnect action.

    PanGPA's UIA InvokePattern can report success without dispatching the
    button.  A physical ``click_input`` is therefore the production default.
    Never run a second method immediately after an ambiguous exception; the
    caller observes the state transition and owns the one bounded retry.
    """
    log = get_logger()
    try:
        expected = str(expected_action or "").strip().casefold()
        if expected not in {"connect", "disconnect"}:
            log.error("gp_invoke: invalid expected_action=%r", expected_action)
            return False

        def _matches_action(control) -> tuple[bool, str]:
            try:
                current_label = (control.window_text() or "").strip()
            except Exception:
                current_label = ""
            normalized = current_label.casefold()
            matches = (
                expected == "connect"
                and "connect" in normalized
                and "disconnect" not in normalized
            ) or (
                expected == "disconnect"
                and "disconnect" in normalized
            )
            return matches, current_label

        ctrl = _gp_find_descendant_by_autoid(win, GP_BTN_CONNECT_AUTOID)
        if not ctrl:
            log.warning(f"gp_invoke: button auto_id={GP_BTN_CONNECT_AUTOID} not found")
            return False
        action_matches, label = _matches_action(ctrl)
        if not action_matches:
            log.warning(
                "gp_invoke: refusing button action expected=%r actual_label=%r",
                expected_action,
                label,
            )
            return False
        log.info(f"gp_invoke: clicking button (label='{label}')")
        action_name = "click_input" if prefer_click_input else "invoke"
        try:
            getattr(ctrl, action_name)()
            log.info("gp_invoke: %s() succeeded", action_name)
            return True
        except Exception as action_error:
            log.info(
                "gp_invoke: %s() raised after a verified %s action (%s); "
                "deferring to transition verification",
                action_name,
                expected,
                action_error,
            )
            return True
    except Exception as e:
        log.error(f"gp_invoke: failed: {e}")
    return False

def _gp_get_login_window(
    timeout: float = 1.0,
    *,
    ignored_hwnds: set[int] | None = None,
):
    """Find the SAML 'GlobalProtect Login' window (class #32770) via UIA.

    PanGPA renders the SAML auth flow inside an embedded WebView2 hosted in
    this dialog. When an identity provider returns Microsoft, the WebView2
    sometimes shows a 'Pick an account' picker — we need the UIA wrapper to
    walk its descendants and click the configured account.
    """
    log = get_logger()
    try:
        from pywinauto import Desktop
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                hwnd = _gp_find_login_hwnd(ignored_hwnds=ignored_hwnds)
                if not hwnd:
                    time.sleep(0.2)
                    continue
                if not _gp_is_exact_pangpa_window(hwnd, GP_LOGIN_TITLE):
                    log.warning(
                        "gp_get_login_window: refusing window whose native "
                        "identity changed hwnd=%s",
                        hwnd,
                    )
                    time.sleep(0.2)
                    continue
                desktop = Desktop(backend="uia")
                try:
                    wrapped = desktop.window(handle=hwnd).wrapper_object()
                    if not _gp_is_exact_pangpa_window(hwnd, GP_LOGIN_TITLE):
                        log.warning(
                            "gp_get_login_window: identity changed while wrapping "
                            "hwnd=%s",
                            hwnd,
                        )
                        time.sleep(0.2)
                        continue
                    log.info("gp_get_login_window: wrapped Win32 hwnd=%s", hwnd)
                    return wrapped
                except Exception as exc:
                    log.debug(
                        "gp_get_login_window: could not wrap hwnd=%s: %s",
                        hwnd,
                        exc,
                    )
            except Exception as e:
                log.debug(f"gp_get_login_window: iteration error: {e}")
            time.sleep(0.2)
    except Exception as e:
        log.warning(f"gp_get_login_window: failed: {e}")
    return None

def _gp_find_login_hwnd(*, ignored_hwnds: set[int] | None = None):
    """Find the Win32 hwnd for the GlobalProtect SAML dialog."""
    ignored = {
        _gp_hwnd_value(hwnd)
        for hwnd in (ignored_hwnds or ())
        if _gp_hwnd_value(hwnd)
    }
    try:
        import ctypes
        import ctypes.wintypes
        user32 = ctypes.windll.user32
        found = []
        WNDENUMPROC = ctypes.WINFUNCTYPE(
            ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM
        )

        def _enum(hwnd, _):
            length = user32.GetWindowTextLengthW(hwnd)
            if length:
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                if buf.value == GP_LOGIN_TITLE:
                    cls_buf = ctypes.create_unicode_buffer(256)
                    user32.GetClassNameW(hwnd, cls_buf, 256)
                    handle = _gp_hwnd_value(hwnd)
                    if (
                        handle not in ignored
                        and cls_buf.value == GP_CLASS
                        and _gp_is_exact_pangpa_window(handle, GP_LOGIN_TITLE)
                    ):
                        found.append((handle, bool(user32.IsWindowVisible(hwnd))))
            return True

        user32.EnumWindows(WNDENUMPROC(_enum), 0)
        if not found:
            return None
        visible = [hwnd for hwnd, is_visible in found if is_visible]
        return visible[0] if visible else found[0][0]
    except Exception:
        return None

def _gp_close_login_window(
    hwnd=None,
    *,
    ignored_hwnds: set[int] | None = None,
) -> bool:
    """Post a non-blocking close only to the exact GlobalProtect SAML dialog."""
    log = get_logger()
    try:
        import ctypes

        user32 = ctypes.windll.user32
        ignored = {
            _gp_hwnd_value(value)
            for value in (ignored_hwnds or ())
            if _gp_hwnd_value(value)
        }
        candidate = hwnd or _gp_find_login_hwnd(ignored_hwnds=ignored)
        if not candidate:
            return False

        candidate = _gp_hwnd_value(candidate)
        if candidate in ignored:
            log.info("gp_saml: refusing to close ignored stale login hwnd=%s", candidate)
            return False
        if not _gp_is_exact_pangpa_window(candidate, GP_LOGIN_TITLE):
            title, class_name, process_name = _gp_native_window_identity(candidate)
            log.warning(
                "gp_saml: refusing to close unexpected window hwnd=%s title=%r "
                "class=%r process=%r",
                candidate,
                title,
                class_name,
                process_name,
            )
            return False

        if not user32.PostMessageW(candidate, 0x0010, 0, 0):  # WM_CLOSE
            log.warning("gp_saml: PostMessage(WM_CLOSE) failed for hwnd=%s", candidate)
            return False
        log.info("gp_saml: posted WM_CLOSE to terminal login window hwnd=%s", candidate)
        return True
    except Exception as exc:
        log.debug("gp_saml: could not close terminal login window: %s", exc)
        return False

def _gp_terminal_title_matches(title: str, portal: str = "") -> bool:
    """Return whether a PanGPA top-level title is safe terminal-window scope."""
    normalized_title = str(title or "").strip().casefold()
    if normalized_title == GP_LOGIN_TITLE.casefold():
        return True
    notification_prefix = f"{GP_NOTIFICATION_TITLE_PREFIX.casefold()} - "
    if not normalized_title.startswith(notification_prefix):
        return False
    expected_portal = _normalize_gp_portal(portal)
    if not expected_portal:
        return True
    return normalized_title == (
        f"{GP_NOTIFICATION_TITLE_PREFIX} - {expected_portal.split('.', 1)[0]}"
    ).casefold()

def _gp_hwnd_value(hwnd) -> int:
    """Normalize either a ctypes HWND wrapper or a plain integer handle."""
    try:
        return int(getattr(hwnd, "value", hwnd) or 0)
    except (TypeError, ValueError):
        return 0

def _gp_native_window_identity(hwnd) -> tuple[str, str, str]:
    """Read title, class and process name for a native top-level window."""
    try:
        import ctypes
        import ctypes.wintypes
        import psutil

        user32 = ctypes.windll.user32
        handle = _gp_hwnd_value(hwnd)
        if not handle or not user32.IsWindow(handle):
            return "", "", ""
        title_length = user32.GetWindowTextLengthW(handle)
        title_buffer = ctypes.create_unicode_buffer(title_length + 1)
        class_buffer = ctypes.create_unicode_buffer(256)
        process_id = ctypes.wintypes.DWORD()
        user32.GetWindowTextW(handle, title_buffer, title_length + 1)
        user32.GetClassNameW(handle, class_buffer, 256)
        user32.GetWindowThreadProcessId(handle, ctypes.byref(process_id))
        process_name = ""
        if process_id.value:
            try:
                process_name = psutil.Process(process_id.value).name()
            except Exception:
                process_name = ""
        return title_buffer.value, class_buffer.value, process_name
    except Exception:
        return "", "", ""


def _gp_is_exact_pangpa_window(hwnd, expected_title: str) -> bool:
    """Verify a top-level window immediately before Banco credential UI use."""
    title, class_name, process_name = _gp_native_window_identity(hwnd)
    return (
        title == expected_title
        and class_name == GP_CLASS
        and process_name.casefold() == "pangpa.exe"
    )

def _gp_list_terminal_windows(portal: str = "") -> list[tuple[int, str]]:
    """List only PanGPA Login/Notification windows, including hidden ones."""
    try:
        import ctypes
        import ctypes.wintypes

        user32 = ctypes.windll.user32
        found: list[tuple[int, str]] = []
        WNDENUMPROC = ctypes.WINFUNCTYPE(
            ctypes.c_bool,
            ctypes.wintypes.HWND,
            ctypes.wintypes.LPARAM,
        )

        def _enum(hwnd, _):
            title, class_name, process_name = _gp_native_window_identity(hwnd)
            if (
                class_name == GP_CLASS
                and process_name.casefold() == "pangpa.exe"
                and _gp_terminal_title_matches(title, portal)
            ):
                found.append((_gp_hwnd_value(hwnd), title))
            return True

        user32.EnumWindows(WNDENUMPROC(_enum), 0)
        return found
    except Exception:
        return []

def _gp_post_close_terminal_window(hwnd: int, expected_title: str) -> bool:
    """Revalidate a terminal PanGPA HWND immediately before posting WM_CLOSE."""
    log = get_logger()
    try:
        import ctypes

        title, class_name, process_name = _gp_native_window_identity(hwnd)
        if (
            title != expected_title
            or class_name != GP_CLASS
            or process_name.casefold() != "pangpa.exe"
            or not _gp_terminal_title_matches(title)
        ):
            log.warning(
                "gp_cleanup: refusing changed window hwnd=%s title=%r class=%r process=%r",
                hwnd,
                title,
                class_name,
                process_name,
            )
            return False
        if not ctypes.windll.user32.PostMessageW(hwnd, 0x0010, 0, 0):
            log.warning("gp_cleanup: PostMessage(WM_CLOSE) failed hwnd=%s", hwnd)
            return False
        log.info("gp_cleanup: posted WM_CLOSE hwnd=%s title=%r", hwnd, title)
        return True
    except Exception as exc:
        log.debug("gp_cleanup: could not close hwnd=%s: %s", hwnd, exc)
        return False

def _gp_cleanup_terminal_windows(portal: str = "", wait_seconds: float = 0.0) -> int:
    """Best-effort cleanup of stale/success Login and Notification windows.

    The main ``GlobalProtect`` window is deliberately out of scope.  A short
    grace period catches the Notification window when PanGPA creates it just
    after the tunnel reaches Connected.
    """
    rounds = max(1, int(max(0.0, wait_seconds) / 0.25) + 1)
    posted: set[int] = set()
    for round_index in range(rounds):
        candidates = sorted(
            _gp_list_terminal_windows(portal),
            key=lambda item: (
                1 if item[1].casefold() == GP_LOGIN_TITLE.casefold() else 0
            ),
        )
        for hwnd, title in candidates:
            if hwnd in posted:
                continue
            if _gp_post_close_terminal_window(hwnd, title):
                posted.add(hwnd)
        if round_index < rounds - 1:
            time.sleep(0.25)
    return len(posted)


def _gp_login_reports_success(
    *,
    ignored_hwnds: set[int] | None = None,
) -> bool:
    """Return True only when a fresh exact Login window exposes a success page."""
    ignored = set(ignored_hwnds or ())
    login_win = (
        _gp_get_login_window(timeout=0.25, ignored_hwnds=ignored)
        if ignored
        else _gp_get_login_window(timeout=0.25)
    )
    if not login_win:
        return False
    hwnd = _gp_hwnd_value(getattr(login_win, "handle", 0))
    if not hwnd or hwnd in ignored or not _gp_is_exact_pangpa_window(
        hwnd,
        GP_LOGIN_TITLE,
    ):
        return False
    try:
        texts = [_gp_control_text(ctrl).casefold() for ctrl in login_win.descendants()]
        page_text = " ".join(text for text in texts if text)
    except Exception:
        return False
    return any(
        marker in page_text
        for marker in (
            "login successful",
            "sign-in successful",
            "sign in successful",
            "inicio de sesión exitoso",
            "inicio de sesión correcto",
        )
    )


def _gp_cleanup_after_confirmed_connection(
    portal: str,
    *,
    attempts: int = 5,
    interval: float = 0.4,
    ignored_login_hwnds: set[int] | None = None,
) -> bool:
    """Close success dialogs only after adapter and exact-portal evidence.

    PanGPA can hide or rebuild its main popup just as the tunnel comes up.  An
    exact Banco notification is therefore valid portal evidence even when that
    popup is unreadable.  A generic Login window is only closed when its page
    explicitly reports success and the persisted portal still matches exactly.
    """
    log = get_logger()
    ignored = set(ignored_login_hwnds or ())
    for attempt in range(max(1, attempts)):
        win = _gp_get_window(timeout=0.5)
        status = _gp_get_status_text(win).strip().casefold() if win else ""
        live_portal = _gp_get_portal_text(win) if win else ""
        probe_ok, sampled_adapter = _gp_adapter_status_sample(max_age=0.0)
        adapter = sampled_adapter.strip().casefold()
        terminal_windows = _gp_list_terminal_windows(portal)
        exact_notification = any(
            title.casefold() != GP_LOGIN_TITLE.casefold()
            for _hwnd, title in terminal_windows
        )
        live_portal_confirmed = (
            status == "connected" and _gp_portal_matches(live_portal, portal)
        )
        successful_login = False
        if (
            probe_ok
            and adapter == "up"
            and not live_portal_confirmed
            and not exact_notification
            and _gp_portal_matches(_gp_get_last_portal(), portal)
        ):
            successful_login = _gp_login_reports_success(
                ignored_hwnds=ignored,
            )
        if (
            probe_ok
            and adapter == "up"
            and (live_portal_confirmed or exact_notification or successful_login)
        ):
            _gp_cleanup_terminal_windows(portal, wait_seconds=1.5)
            return True
        if attempt < max(1, attempts) - 1:
            time.sleep(max(0.0, interval))
    log.info(
        "gp_cleanup: skipped terminal dialogs; strong tunnel confirmation "
        "did not settle in time"
    )
    return False

def _gp_wait_for_connect_transition(
    portal: str,
    *,
    ignored_login_hwnds: set[int] | None = None,
    attempts: int = 8,
    require_adapter_for_terminal_ui: bool = False,
) -> str:
    """Verify that a Connect action produced observable GlobalProtect progress.

    UIA ``InvokePattern`` can return success while PanGPA silently discards the
    action.  Do not enter the long SAML monitor until the main state changes, a
    fresh Login dialog appears, or the adapter actually comes up.
    """
    ignored = set(ignored_login_hwnds or ())
    for attempt in range(max(1, attempts)):
        if _autofill_cancel.is_set():
            return "cancelled"

        win = _gp_get_window(timeout=0.35)
        status = _gp_get_status_text(win).strip().casefold() if win else ""
        button = _gp_get_button_label(win).strip().casefold() if win else ""
        if status.startswith("connecting"):
            return "started"
        if status == "connection failed":
            return "failed"

        fresh_login_window = any(
            hwnd not in ignored and title == GP_LOGIN_TITLE
            for hwnd, title in _gp_list_terminal_windows(portal)
        )
        if fresh_login_window:
            return "started"

        terminal_ui = status == "connected" or (
            "disconnect" in button and "connect" in button
        )
        if terminal_ui and not require_adapter_for_terminal_ui:
            return "connected_status" if status == "connected" else "started"

        # Get-NetAdapter starts a subprocess, so probe it only every few frames.
        if attempt % 3 == 2:
            probe_ok, adapter = _gp_adapter_status_sample(max_age=0.0)
            if probe_ok and adapter.casefold() == "up":
                return "connected_adapter"
        if attempt < max(1, attempts) - 1:
            time.sleep(0.35)
    return "not_started"



def _gp_prepare_login_window(login_win=None) -> bool:
    """Restore and resize GlobalProtect Login if WebView2 opens collapsed.

    On some GlobalProtect/WebView2 combinations the SAML dialog appears as a
    title/header only. The page exists, but Microsoft never paints enough of
    the picker for UIA to find account rows. Restoring and enforcing a sane
    client size makes the picker/edit controls materialize.
    """
    log = get_logger()
    try:
        import ctypes
        import ctypes.wintypes
        user32 = ctypes.windll.user32
        hwnd = getattr(login_win, "handle", None) or _gp_find_login_hwnd()
        if not hwnd:
            return False

        rect = ctypes.wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        was_visible = bool(user32.IsWindowVisible(hwnd))
        before = (rect.left, rect.top, rect.right, rect.bottom)

        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        time.sleep(0.1)
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        width = rect.right - rect.left
        height = rect.bottom - rect.top

        resized = False
        if width < 560 or height < 420:
            screen_w = user32.GetSystemMetrics(0)
            screen_h = user32.GetSystemMetrics(1)
            target_w = min(760, max(640, screen_w - 80))
            target_h = min(640, max(520, screen_h - 80))
            x = max(0, (screen_w - target_w) // 2)
            y = max(0, (screen_h - target_h) // 3)
            user32.MoveWindow(hwnd, x, y, target_w, target_h, True)
            resized = True

        try:
            _force_foreground(hwnd)
        except Exception:
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)

        if resized or not was_visible:
            user32.GetWindowRect(hwnd, ctypes.byref(rect))
            after = (rect.left, rect.top, rect.right, rect.bottom)
            log.info(
                "gp_saml: restored login window "
                f"hwnd={hwnd} was_visible={was_visible} rect={before} -> {after}"
            )
        return True
    except Exception as e:
        log.warning(f"gp_saml: prepare login window failed: {e}")
        return False

def _gp_try_click(ctrl, where: str) -> bool:
    """Send one physical click to a verified SAML control.

    WebView2 can report a successful UIA InvokePattern without navigating. A
    physical click is the only supported path here, and an exception is treated
    as ambiguous instead of issuing a second action immediately.
    """
    log = get_logger()
    try:
        visible = getattr(ctrl, "is_visible", None)
        enabled = getattr(ctrl, "is_enabled", None)
        if callable(visible) and not visible():
            return False
        if callable(enabled) and not enabled():
            return False
        ctrl.click_input()
        log.info(f"gp_picker: click_input() on {where} succeeded")
        return True
    except Exception as e:
        log.info(
            "gp_picker: click_input() on %s raised after one physical action "
            "(%s); waiting for page-state verification",
            where,
            e,
        )
        return True

def _gp_try_click_with_ancestors(ctrl, where: str, max_depth: int = 5) -> bool:
    """Try clicking a UIA node, then walk up to clickable row containers."""
    current = ctrl
    for depth in range(max_depth + 1):
        if not current:
            return False
        label = where if depth == 0 else f"{where}_ancestor{depth}"
        if _gp_try_click(current, label):
            return True
        try:
            current = current.parent()
        except Exception:
            return False
    return False

def _gp_control_text(ctrl) -> str:
    try:
        return str(ctrl.window_text() or "").strip()
    except Exception:
        return ""

def _gp_control_type(ctrl) -> str:
    try:
        return str(getattr(ctrl.element_info, "control_type", "") or "")
    except Exception:
        return ""

def _gp_set_edit_value(ctrl, value: str) -> bool:
    """Set a WebView2 edit through UIA without using the clipboard."""
    try:
        ctrl.iface_value.SetValue(value)
        return True
    except Exception:
        pass
    try:
        ctrl.set_edit_text(value)
        return True
    except Exception:
        return False

def _gp_submit_login_page(descendants, edit_ctrl, labels: tuple[str, ...]) -> bool:
    for ctrl in descendants:
        if _gp_control_type(ctrl) != "Button":
            continue
        text = _gp_control_text(ctrl).casefold()
        if text and any(label in text for label in labels):
            if _gp_try_click_with_ancestors(ctrl, "saml_submit"):
                return True
    try:
        edit_ctrl.set_focus()
        edit_ctrl.type_keys("{ENTER}")
        return True
    except Exception:
        return False

def _gp_edit_descriptor(ctrl) -> str:
    values = [_gp_control_text(ctrl)]
    try:
        info = ctrl.element_info
        values.extend(
            (
                str(getattr(info, "name", "") or ""),
                str(getattr(info, "automation_id", "") or ""),
                str(getattr(info, "class_name", "") or ""),
            )
        )
    except Exception:
        pass
    return " ".join(value.casefold() for value in values if value)

def _gp_is_password_edit(ctrl) -> bool:
    """Return True only when UIA identifies this Edit as a password field."""
    if _gp_control_type(ctrl) != "Edit":
        return False
    try:
        info = ctrl.element_info
        if bool(getattr(info, "is_password", False)):
            return True
        raw_element = getattr(info, "element", None)
        if raw_element is not None and bool(
            getattr(raw_element, "CurrentIsPassword", False)
        ):
            return True
    except Exception:
        pass
    descriptor = _gp_edit_descriptor(ctrl)
    return any(
        marker in descriptor
        for marker in ("password", "passwd", "contraseña")
    )

def _gp_find_edit(
    descendants,
    hints: tuple[str, ...],
    *,
    allow_single_fallback: bool = True,
):
    edits = [ctrl for ctrl in descendants if _gp_control_type(ctrl) == "Edit"]
    for ctrl in edits:
        descriptor = _gp_edit_descriptor(ctrl)
        if any(hint in descriptor for hint in hints):
            return ctrl
    if allow_single_fallback and len(edits) == 1:
        return edits[0]
    return None

def _gp_handle_saml_signin(
    username: str,
    password_provider=None,
    total_timeout: float = 120.0,
    connected_probe=None,
    status_probe=None,
    progress=None,
    flow_mode: str = "detect",
    flow_steps=None,
    ignored_login_hwnds: set[int] | None = None,
    connected_adapter_grace: float = GP_CONNECTED_ADAPTER_GRACE_SECONDS,
) -> str:
    """Drive known Microsoft SAML pages and stop safely on unknown pages.

    The password is requested lazily only after the page is unambiguously a
    password prompt. MFA is never automated; the function keeps the dialog
    visible and waits for the GlobalProtect tunnel to come up.
    """
    log = get_logger()
    target = str(username or "").strip().casefold()
    custom_flow = str(flow_mode or "detect").strip().casefold() == "custom"
    configured_steps = {
        str(step).strip().casefold()
        for step in (flow_steps or ())
        if str(step).strip().casefold() in {"account", "password", "mfa"}
    }
    if not custom_flow:
        configured_steps = {"account", "password", "mfa"}
    ignored = {
        _gp_hwnd_value(hwnd)
        for hwnd in (ignored_login_hwnds or ())
        if _gp_hwnd_value(hwnd)
    }
    deadline = time.time() + total_timeout
    password_submitted = False
    mfa_reported = False
    unknown_reported = False
    account_wait_reported = False
    password_wait_reported = False
    password_prompt_without_edit_frames = 0
    password_error_present_at_submit = False
    password_error_cleared_after_submit = False
    persistent_password_error_frames = 0
    stale_password_error_grace_frames = 12
    password_submit_wait_frames = 0
    password_submit_retry_done = False
    account_click_attempts = 0
    account_click_wait_frames = 0
    saw_connecting = False
    terminal_state_frames = 0
    terminal_state_samples = 0
    prepared_login_hwnds: set[int] = set()
    connected_ui_started_at: float | None = None

    log.info("gp_saml: monitoring Microsoft sign-in flow (timeout=%ss)", total_timeout)
    while time.time() < deadline:
        if _autofill_cancel.is_set():
            log.info("gp_saml: cancelled")
            return "cancelled"
        connection_state = ""
        if callable(status_probe):
            try:
                connection_state = str(status_probe() or "").strip().casefold()
            except Exception as exc:
                log.debug("gp_saml: status probe failed: %s", exc)
            # PanGPA's status label can remain frozen on Connected after a
            # manual disconnect.  Treat it only as UI progress; the separate
            # connected_probe must confirm a successful adapter sample at Up.
            if connection_state == "connected":
                if connected_ui_started_at is None:
                    connected_ui_started_at = time.monotonic()
            elif connection_state.startswith("connecting"):
                connected_ui_started_at = None
                saw_connecting = True
                terminal_state_frames = 0
            elif saw_connecting and connection_state == "connection failed":
                log.warning("gp_saml: main window reports Connection Failed")
                if ignored:
                    _gp_close_login_window(ignored_hwnds=ignored)
                else:
                    _gp_close_login_window()
                return "connection_failed"
            elif saw_connecting and connection_state in {
                "disconnected",
                "not connected",
            }:
                terminal_state_frames += 1
                terminal_state_samples += 1
                if terminal_state_frames >= 3 or terminal_state_samples >= 8:
                    log.warning(
                        "gp_saml: connection returned to terminal state='%s' "
                        "(consecutive=%s total=%s)",
                        connection_state,
                        terminal_state_frames,
                        terminal_state_samples,
                    )
                    if ignored:
                        _gp_close_login_window(ignored_hwnds=ignored)
                    else:
                        _gp_close_login_window()
                    return "connection_failed"
            elif connection_state:
                connected_ui_started_at = None
                terminal_state_frames = 0
        try:
            if callable(connected_probe) and connected_probe():
                log.info("gp_saml: tunnel connected")
                return "connected"
        except Exception as exc:
            log.debug("gp_saml: connection probe failed: %s", exc)

        login_win = (
            _gp_get_login_window(timeout=0.5, ignored_hwnds=ignored)
            if ignored
            else _gp_get_login_window(timeout=0.5)
        )
        if login_win and connection_state == "connected":
            # A live, fresh SAML page (especially MFA) can coexist with a stale
            # Connected label from the prior session. Do not spend the adapter
            # grace period while the user is still completing that page.
            connected_ui_started_at = None
        if (
            not login_win
            and connected_ui_started_at is not None
            and time.monotonic() - connected_ui_started_at
            >= max(0.0, connected_adapter_grace)
        ):
            log.warning(
                "gp_saml: UI remained Connected for %.1fs without a confirmed "
                "Up adapter",
                max(0.0, connected_adapter_grace),
            )
            return "adapter_not_up"
        if not login_win:
            time.sleep(0.5)
            continue
        login_hwnd = int(getattr(login_win, "handle", 0) or 0)
        if login_hwnd and not _gp_is_exact_pangpa_window(
            login_hwnd,
            GP_LOGIN_TITLE,
        ):
            log.error(
                "gp_saml: refusing credential automation because the login "
                "window identity changed hwnd=%s",
                login_hwnd,
            )
            return "unknown_page"
        if not login_hwnd or login_hwnd not in prepared_login_hwnds:
            _gp_prepare_login_window(login_win)
            if login_hwnd:
                prepared_login_hwnds.add(login_hwnd)
        try:
            descendants = list(login_win.descendants())
        except Exception as exc:
            log.warning("gp_saml: descendants() failed: %s", exc)
            time.sleep(0.5)
            continue

        texts = [_gp_control_text(ctrl).casefold() for ctrl in descendants]
        page_text = " ".join(text for text in texts if text)

        authentication_failed_markers = (
            "authentication failed",
            "please contact the administrator for further assistance",
            "autenticación fallida",
            "contacte al administrador",
        )
        if any(marker in page_text for marker in authentication_failed_markers):
            log.warning("gp_saml: GlobalProtect reported Authentication Failed")
            if ignored:
                _gp_close_login_window(
                    getattr(login_win, "handle", None),
                    ignored_hwnds=ignored,
                )
            else:
                _gp_close_login_window(getattr(login_win, "handle", None))
            return "authentication_failed"

        wrong_password_markers = (
            "password is incorrect",
            "incorrect password",
            "account or password is incorrect",
            "contraseña es incorrecta",
            "cuenta o contraseña es incorrecta",
        )
        wrong_password_page = any(
            marker in page_text for marker in wrong_password_markers
        )
        denied_markers = (
            "request denied",
            "access denied",
            "sign-in was blocked",
            "solicitud rechazada",
            "acceso denegado",
        )
        if any(marker in page_text for marker in denied_markers):
            log.warning("gp_saml: sign-in request was denied")
            return "denied"

        mfa_approval_markers = (
            "approve sign in request",
            "approve sign-in request",
            "open your authenticator app and approve",
            "aprobar solicitud de inicio de sesión",
            "aprueba la solicitud de inicio de sesión",
            "abra la aplicación authenticator y apruebe",
        )
        mfa_number_markers = (
            "enter the number",
            "escriba el número",
            "ingresa el número",
        )
        mfa_page = any(marker in page_text for marker in mfa_approval_markers) or (
            "authenticator" in page_text
            and any(marker in page_text for marker in mfa_number_markers)
        )
        if mfa_page:
            if not mfa_reported:
                log.info("gp_saml: MFA approval is required")
                if callable(progress) and "mfa" in configured_steps:
                    progress("Approve the MFA request in Microsoft Authenticator.")
                mfa_reported = True
            time.sleep(0.5)
            continue

        password_markers = (
            "enter password",
            "enter your password",
            "escriba su contraseña",
            "ingrese su contraseña",
            "introduzca su contraseña",
        )
        password_prompt = (
            any(marker in page_text for marker in password_markers)
            or wrong_password_page
        )
        password_edit = next(
            (ctrl for ctrl in descendants if _gp_is_password_edit(ctrl)),
            None,
        )
        password_page = password_prompt and password_edit is not None
        if password_submitted:
            if not wrong_password_page:
                if password_error_present_at_submit:
                    password_error_cleared_after_submit = True
                persistent_password_error_frames = 0
            elif (
                not password_error_present_at_submit
                or password_error_cleared_after_submit
            ):
                log.warning("gp_saml: Microsoft rejected the saved password")
                return "wrong_password"
            else:
                # On a retry, Microsoft's previous error remains in WebView2
                # while the new password is being processed.  Treating that
                # same DOM immediately as a fresh rejection races the SAML
                # navigation and rejects a corrected password locally.
                persistent_password_error_frames += 1
                if persistent_password_error_frames >= stale_password_error_grace_frames:
                    log.warning(
                        "gp_saml: password error persisted after the corrected "
                        "password grace period"
                    )
                    return "wrong_password"
                time.sleep(0.5)
                continue
            if password_page and not wrong_password_page:
                password_submit_wait_frames += 1
                if (
                    password_submit_wait_frames >= 8
                    and not password_submit_retry_done
                ):
                    if _autofill_cancel.is_set():
                        return "cancelled"
                    log.warning(
                        "gp_saml: password page did not navigate; retrying the "
                        "verified submit action once"
                    )
                    if not _gp_submit_login_page(
                        descendants,
                        password_edit,
                        ("sign in", "iniciar sesión", "continuar", "next", "siguiente"),
                    ):
                        return "password_submit_failed"
                    password_submit_retry_done = True
                    password_submit_wait_frames = 0
                elif password_submit_retry_done and password_submit_wait_frames >= 16:
                    log.warning(
                        "gp_saml: password page remained unchanged after the "
                        "single physical submit retry"
                    )
                    return "password_submit_failed"
                time.sleep(0.5)
                continue
        if password_prompt and password_edit is None:
            password_prompt_without_edit_frames += 1
            if password_prompt_without_edit_frames < 6:
                # WebView2 can expose the heading a few frames before the Edit.
                time.sleep(0.5)
                continue
            log.warning("gp_saml: password prompt has no verified password field")
            return "wrong_password" if wrong_password_page else "unknown_page"
        password_prompt_without_edit_frames = 0
        if password_page:
            if password_submitted:
                time.sleep(0.5)
                continue
            if "password" not in configured_steps:
                if not password_wait_reported:
                    log.info("gp_saml: custom flow leaves password entry manual")
                    if callable(progress):
                        progress("Enter the Microsoft password manually.")
                    password_wait_reported = True
                time.sleep(0.5)
                continue
            edit = password_edit
            password = str(password_provider() if callable(password_provider) else "")
            if not password:
                log.info("gp_saml: password prompt detected but no password is configured")
                return "password_required"
            if _autofill_cancel.is_set():
                password = ""
                return "cancelled"
            if not _gp_set_edit_value(edit, password):
                password = ""
                log.warning("gp_saml: could not write the password through UIA")
                return "password_input_failed"
            password = ""
            if _autofill_cancel.is_set():
                return "cancelled"
            if not _gp_submit_login_page(
                descendants,
                edit,
                ("sign in", "iniciar sesión", "continuar", "next", "siguiente"),
            ):
                log.warning("gp_saml: password entered but submit action failed")
                return "password_submit_failed"
            password_submitted = True
            password_error_present_at_submit = wrong_password_page
            password_error_cleared_after_submit = False
            persistent_password_error_frames = 0
            password_submit_wait_frames = 0
            password_submit_retry_done = False
            log.info("gp_saml: password submitted")
            if callable(progress):
                progress("Password submitted. Waiting for MFA approval or connection.")
            time.sleep(0.7)
            continue

        # Account picker. An exact identity can also appear as a header on the
        # password and MFA pages, so click it only on an explicitly classified
        # picker and never after the password has already been submitted.
        account_picker_markers = (
            "pick an account",
            "choose an account",
            "select an account",
            "elegir una cuenta",
            "elige una cuenta",
            "seleccionar una cuenta",
            "selecciona una cuenta",
            "use another account",
            "usar otra cuenta",
            "usar una cuenta diferente",
        )
        account_picker_page = any(
            marker in page_text for marker in account_picker_markers
        )
        if account_picker_page and not password_submitted:
            if "account" not in configured_steps:
                if not account_wait_reported:
                    log.info("gp_saml: custom flow leaves account selection manual")
                    if callable(progress):
                        progress("Select the Banco de Chile Microsoft account manually.")
                    account_wait_reported = True
                time.sleep(0.5)
                continue
            if not target:
                log.info("gp_saml: account picker detected but no account is configured")
                return "username_required"
            if account_click_attempts:
                account_click_wait_frames += 1
                if account_click_attempts >= 2 or account_click_wait_frames < 4:
                    time.sleep(0.5)
                    continue
            account_clicked = False
            for ctrl, text in zip(descendants, texts):
                if target and " ".join(text.split()) == target:
                    log.info("gp_saml: configured account row found")
                    if _autofill_cancel.is_set():
                        return "cancelled"
                    if _gp_try_click_with_ancestors(ctrl, "picker_match"):
                        account_clicked = True
                        account_click_attempts += 1
                        account_click_wait_frames = 0
                        break
            if account_clicked:
                time.sleep(0.7)
                continue
            if not account_wait_reported:
                log.info("gp_saml: exact configured account row is not available")
                if callable(progress):
                    progress("Select the saved Banco de Chile account manually.")
                account_wait_reported = True
            time.sleep(0.5)
            continue

        email_markers = (
            "email, phone, or skype",
            "enter email",
            "correo electrónico, teléfono o skype",
            "escriba su correo",
        )
        if not target and any(marker in page_text for marker in email_markers):
            log.info("gp_saml: email prompt detected but no username is configured")
            return "username_required"
        if target and any(marker in page_text for marker in email_markers):
            # Banco's supported flow selects the saved Microsoft account row.
            # Never type an email address into a WebView2 page automatically.
            if not account_wait_reported:
                log.info("gp_saml: email page left for manual completion")
                if callable(progress):
                    progress("Select the saved Banco de Chile account; email is not typed automatically.")
                account_wait_reported = True
            time.sleep(0.5)
            continue

        # Unknown SAML pages are deliberately passive: never type a
        # username or secret into an unclassified control.
        if page_text and not unknown_reported and callable(progress):
            progress("Complete any remaining Microsoft sign-in prompt.")
            unknown_reported = True
        log.debug("gp_saml: waiting on an unclassified Microsoft page")
        time.sleep(0.5)

    log.warning("gp_saml: timed out while disconnected")
    return "mfa_timeout" if mfa_reported else "timeout"

class BancoChileSwitcher:
    def __init__(self, config: dict):
            self.config = config
            self._last_gp_target = None
            # ``None`` means no status sample has been requested yet.  The
            # coordinator uses this explicit signal to distinguish a real
            # non-Banco GlobalProtect portal from a failed Get-NetAdapter
            # probe; both intentionally map to GPROT_UNKNOWN at the public
            # status boundary.
            self._last_status_probe_ok: bool | None = None
            # Distinguish a PanGPA status label that is merely stale after a
            # disconnect from the short adapter/UI skew while a new tunnel starts.
            # This flag is set only after disconnect_globalprotect has confirmed
            # two terminal adapter samples.
            self._gp_disconnect_confirmed = False
            self._terminal_adapter_samples = 0
            self._next_connect_not_before = 0.0

    @property
    def last_status_probe_ok(self) -> bool | None:
        """Whether the adapter probe used by the latest status read succeeded."""
        return self._last_status_probe_ok

    def _gp_connected_target(self) -> str:
        win = _gp_get_monitor_window(timeout=0.4)
        portal = _gp_get_monitor_portal_text(win) if win else ""
        if not portal and self._last_gp_target == BANCOCHILE:
            # During an app-initiated Connecting/SAML flow the registry can
            # still contain the previous LastUrl. Preserve the target that was
            # actually selected until the live portal control becomes readable.
            return self._last_gp_target
        portal = portal or _gp_get_last_portal()
        normalized = _normalize_gp_portal(portal)
        bancochile_portal = _normalize_gp_portal(
            self.config.get("bancochile_portal_url", BANCOCHILE_PORTAL)
            or BANCOCHILE_PORTAL
        )
        if _gp_portal_matches(normalized, bancochile_portal):
            self._last_gp_target = BANCOCHILE
            return BANCOCHILE
        if normalized:
            self._last_gp_target = None
            return GPROT_UNKNOWN
        return self._last_gp_target or GPROT_UNKNOWN

    def is_bancochile_active(self) -> bool:
        """Classify Banco only from the live PanGPA portal control.

        The registry's LastUrl may belong to an earlier session, so it is not
        safe evidence when deciding whether another GlobalProtect route may run.
        """
        win = _gp_get_monitor_window(timeout=0.4)
        portal = _gp_get_monitor_portal_text(win) if win else ""
        expected = _normalize_gp_portal(
            self.config.get("bancochile_portal_url", BANCOCHILE_PORTAL)
            or BANCOCHILE_PORTAL
        )
        active = bool(portal) and _gp_portal_matches(portal, expected)
        if active:
            self._last_gp_target = BANCOCHILE
        return active

    def get_bancochile_status(self, adapter_status: str | None = None) -> str:
        """Report Banco independently from legacy GlobalProtect UI state.

        Once this switcher confirms teardown, a stale PanGPA ``Connected``
        label must not make this Banco-only service own another profile.
        """
        if adapter_status is None:
            probe_ok, sampled_status = _gp_adapter_status_sample()
        else:
            # Callers that already ran the shared adapter probe pass its result
            # explicitly.  An empty value here means a successful probe found
            # no GlobalProtect adapter; it is not a transport/probe failure.
            probe_ok, sampled_status = True, adapter_status
        self._last_status_probe_ok = probe_ok
        if not probe_ok:
            # Preserve ownership/debounce exactly as-is.  A technical failure
            # must never manufacture either a connected or disconnected state.
            return GPROT_UNKNOWN

        adapter_state = str(sampled_status or "").strip().casefold()
        adapter_terminal = (
            not adapter_state or adapter_state in GP_ADAPTER_DISCONNECTED_STATES
        )
        if adapter_state == "up":
            self._terminal_adapter_samples = 0
        elif adapter_terminal:
            self._terminal_adapter_samples += 1
        else:
            self._terminal_adapter_samples = 0
        win = _gp_get_monitor_window(timeout=0.4)
        portal = _gp_get_monitor_portal_text(win) if win else ""
        expected = _normalize_gp_portal(
            self.config.get("bancochile_portal_url", BANCOCHILE_PORTAL)
            or BANCOCHILE_PORTAL
        )
        live_banco = bool(portal) and _gp_portal_matches(portal, expected)
        if portal and not live_banco:
            # Positive evidence of another live portal always wins over stale
            # ownership left by a cancelled Banco attempt.
            self._last_gp_target = None
            self._gp_disconnect_confirmed = False
            return GPROT_UNKNOWN
        if (
            self._gp_disconnect_confirmed
            and adapter_terminal
        ):
            self._last_gp_target = None
            return NONE
        if live_banco:
            self._last_gp_target = BANCOCHILE
        banco_owned = live_banco or self._last_gp_target == BANCOCHILE
        status = _gp_get_monitor_status_text(win).strip().casefold() if win else ""
        if not banco_owned:
            # No Banco portal/attempt plus terminal (or completely absent)
            # GlobalProtect evidence means the failed Banco attempt owns
            # nothing. An Up/connecting/connected state remains unknown so we
            # never operate on another profile.
            if adapter_terminal or status in {
                "disconnected",
                "not connected",
                "connection failed",
            }:
                return NONE
            return GPROT_UNKNOWN
        if adapter_state == "up":
            return BANCOCHILE

        if adapter_terminal:
            if status in {"disconnected", "not connected", "connection failed"}:
                self._last_gp_target = None
                return NONE
            if (
                status == "connected"
                and self._terminal_adapter_samples >= 2
                and not _gp_login_window_present()
            ):
                # Manual disconnect can leave PanGPA's Connected label frozen.
                # Two independent terminal adapter samples are stronger than
                # that stale UI, while one sample remains safe during startup.
                self._last_gp_target = None
                return NONE

        if live_banco and status.startswith(("connected", "connecting", "disconnecting")):
            return BANCOCHILE
        if self._last_gp_target == BANCOCHILE and _gp_login_window_present():
            return BANCOCHILE
        if adapter_terminal or status in {
            "disconnected",
            "not connected",
            "connection failed",
        }:
            self._last_gp_target = None
            return NONE
        # The app initiated Banco and the UI is temporarily unavailable or in
        # an unclassified transition. Keep ownership until a terminal state.
        return BANCOCHILE

    def connect_bancochile(self, progress=None) -> Tuple[bool, str]:
            while True:
                remaining = self._next_connect_not_before - time.monotonic()
                if remaining <= 0:
                    break
                if _autofill_cancel.is_set():
                    return False, "__GP_CANCELLED__"
                if callable(progress):
                    progress(
                        "Waiting for the previous GlobalProtect sign-in to close "
                        f"({int(remaining + 0.999)}s)..."
                    )
                if _autofill_cancel.wait(min(1.0, remaining)):
                    return False, "__GP_CANCELLED__"

            result = self._connect_bancochile_profile(progress=progress)
            ok, message = result
            if ok:
                self._next_connect_not_before = 0.0
            elif message == "__GP_CANCELLED__" or any(
                marker in message.casefold()
                for marker in (
                    "stopped connecting",
                    "authentication failed",
                    "mfa sign-in",
                    "authenticator approval",
                    "did not connect before",
                )
            ):
                self._next_connect_not_before = time.monotonic() + 20.0
            return result

    def _connect_bancochile_profile(self, progress=None) -> Tuple[bool, str]:
            """Launch (if needed), bring the GlobalProtect window forward, click
            Connect, complete known Microsoft pages and wait for manual MFA."""
            log = get_logger()
            if _autofill_cancel.is_set():
                return False, "__GP_CANCELLED__"
            profile_name = "Banco de Chile"
            portal = (
                self.config.get("bancochile_portal_url", BANCOCHILE_PORTAL).strip()
                or BANCOCHILE_PORTAL
            )
            username = self.config.get("bancochile_username", "").strip()
            password_enc = self.config.get("bancochile_password_enc", "")

            log.info(
                "connect_globalprotect: ===== profile=%s attempt started =====",
                BANCOCHILE,
            )
            log.info("connect_globalprotect: configured portal='%s'", portal)
            _gp_diagnostics()

            # 1. Try to find an existing window first
            win = _gp_get_window(timeout=2)
            log.info(f"connect_globalprotect: initial window found={bool(win)}")

            # 2. Launch PanGPA.exe if no window yet
            if not win:
                gp_exe = _find_exe(
                    GP_EXE_CANDIDATES,
                    self.config.get("bancochile_gp_exe_path", ""),
                )
                log.info(f"connect_globalprotect: gp_exe={gp_exe}")
                if not gp_exe:
                    log.error("connect_globalprotect: PanGPA.exe not found")
                    return False, "GlobalProtect not installed (PanGPA.exe not found)."
                if _autofill_cancel.is_set():
                    return False, "__GP_CANCELLED__"
                log.info(f"connect_globalprotect: launching {gp_exe}")
                _open_gui(gp_exe)
                win = _gp_get_window(timeout=15)
                log.info(f"connect_globalprotect: post-launch window found={bool(win)}")

            if not win:
                log.error("connect_globalprotect: window did not appear after launch")
                _gp_diagnostics()
                return False, "GlobalProtect window did not appear. Open it from the system tray and retry."

            # 3. Bring window forward (it can be hidden in the tray)
            try:
                win.set_focus()
                log.info("connect_globalprotect: window focused")
            except Exception as e:
                log.warning(f"connect_globalprotect: set_focus failed: {e}")

            # 4. Read state and never click a Disconnect control as Connect.
            status_before = _gp_get_status_text(win)
            button_label = _gp_get_button_label(win)
            log.info(f"connect_globalprotect: pre-click status='{status_before}' button='{button_label}'")
            if not status_before and not button_label:
                log.warning("connect_globalprotect: could not read status or button — Palo Alto may have changed auto_ids; dumping tree:")
                _gp_dump_descendants(win)

            # PanGPA reports one of: Disconnected / Not Connected / Connecting... /
            # Connected / Disconnecting... / Connection Failed. Only bail out when
            # the status is exactly 'Connected' — substring matching here used to
            # mis-classify 'Not Connected' as connected and silently no-op.
            status_state = status_before.strip().casefold()
            button_state = button_label.strip().casefold()
            adapter_probe_ok, sampled_adapter = _gp_adapter_status_sample(max_age=0.0)
            if not adapter_probe_ok:
                return False, (
                    "Could not verify the GlobalProtect adapter state. "
                    "No connection action was taken; retry in a moment."
                )
            adapter_state = sampled_adapter.strip().casefold()
            adapter_up = adapter_state == "up"
            require_strong_connect_transition = self._gp_disconnect_confirmed
            terminal_ui = status_state == "connected" or "disconnect" in button_state
            explicit_terminal_adapter = (
                not adapter_state or adapter_state in GP_ADAPTER_DISCONNECTED_STATES
            )

            # PanGPA may leave Connected/Disconnect visible after teardown, including
            # across an app restart. Never press that shared toggle as Connect when
            # teardown was confirmed here or the adapter explicitly reports a
            # terminal state. Wait for a safe Connect control or real progress.
            stale_transition_ui = status_state.startswith(("connecting", "disconnecting"))
            stale_terminal_ui = terminal_ui and not stale_transition_ui
            ambiguous_post_disconnect_ui = (
                self._gp_disconnect_confirmed and (terminal_ui or stale_transition_ui)
            ) or (explicit_terminal_adapter and stale_terminal_ui)
            if (
                ambiguous_post_disconnect_ui
                and not adapter_up
            ):
                require_strong_connect_transition = True
                log.info(
                    "connect_globalprotect: waiting for ambiguous terminal UI to settle"
                )
                settled = False
                for _ in range(8):
                    if _autofill_cancel.is_set():
                        return False, "__GP_CANCELLED__"
                    time.sleep(0.35)
                    if _autofill_cancel.is_set():
                        return False, "__GP_CANCELLED__"
                    candidate = _gp_get_window(timeout=0.5)
                    if not candidate:
                        continue
                    candidate_status = _gp_get_status_text(candidate).strip().casefold()
                    candidate_button = _gp_get_button_label(candidate).strip().casefold()
                    candidate_probe_ok, candidate_sample = _gp_adapter_status_sample(
                        max_age=0.0
                    )
                    if not candidate_probe_ok:
                        log.warning(
                            "connect_globalprotect: adapter probe failed while "
                            "waiting for stale UI to settle"
                        )
                        continue
                    candidate_adapter = candidate_sample.strip().casefold()
                    win = candidate
                    status_state = candidate_status
                    button_state = candidate_button
                    adapter_state = candidate_adapter
                    adapter_up = candidate_adapter == "up"
                    if adapter_up:
                        self._gp_disconnect_confirmed = False
                        settled = True
                        break
                    # Connecting can also be the final stale frame of the attempt
                    # just cancelled.  Do not resume SAML until the old generation
                    # reaches a safe Connect control (or the adapter truly comes Up).
                    safe_connect_control = (
                        candidate_status
                        in {"disconnected", "not connected", "connection failed"}
                        and "connect" in candidate_button
                        and "disconnect" not in candidate_button
                    )
                    if safe_connect_control:
                        settled = True
                        break
                if not settled:
                    return False, (
                        "GlobalProtect is still settling after the previous disconnect. "
                        "No button was pressed; wait a moment and retry."
                    )

            if adapter_up:
                self._gp_disconnect_confirmed = False
            if adapter_up:
                connected_target = self._gp_connected_target()
                if connected_target == BANCOCHILE:
                    self._last_gp_target = BANCOCHILE
                    log.info("connect_globalprotect: requested profile is already connected")
                    _gp_cleanup_after_confirmed_connection(portal)
                    return True, f"{profile_name} VPN is already connected."
                log.warning(
                    "connect_globalprotect: another GlobalProtect profile is active (%s)",
                    connected_target,
                )
                return False, "Another GlobalProtect portal is connected. Disconnect it first."

            resume_existing_attempt = False
            if status_state.startswith("connecting") or "disconnect" in button_state:
                active_target = self._gp_connected_target()
                if active_target != BANCOCHILE:
                    return False, "Another GlobalProtect portal is connecting. Wait or disconnect it first."
                if status_state.startswith("connecting") or _gp_login_window_present():
                    resume_existing_attempt = True
                    self._last_gp_target = BANCOCHILE
                    log.info("connect_globalprotect: resuming existing sign-in attempt")
                    if callable(progress):
                        progress(f"{profile_name} GlobalProtect is already connecting...")
                else:
                    return False, "GlobalProtect is changing state. Wait a moment and retry."

            stale_login_hwnds: set[int] = set()
            connect_transition = "started" if resume_existing_attempt else ""
            if not resume_existing_attempt:
                if _autofill_cancel.is_set():
                    return False, "__GP_CANCELLED__"
                # A prior successful/failed SAML flow can leave hidden Login and
                # Notification dialogs behind.  At this point the main window is
                # explicitly disconnected, so those auxiliary windows are stale.
                if status_state in {"disconnected", "not connected", "connection failed"}:
                    _gp_cleanup_terminal_windows(portal, wait_seconds=0.5)
                stale_login_hwnds = {
                    hwnd for hwnd, title in _gp_list_terminal_windows(portal)
                    if title == GP_LOGIN_TITLE
                }
                if not _gp_set_portal(win, portal):
                    return False, f"Could not select the {profile_name} GlobalProtect portal."
                self._last_gp_target = BANCOCHILE
                if _autofill_cancel.is_set():
                    return False, "__GP_CANCELLED__"

                # 5. Click Connect
                if not _gp_invoke_connect_button(win, expected_action="connect"):
                    log.error("connect_globalprotect: failed to click Connect button")
                    _gp_dump_descendants(win)
                    _gp_diagnostics()
                    return False, "Could not click Connect button in GlobalProtect window."

                connect_transition = _gp_wait_for_connect_transition(
                    portal,
                    ignored_login_hwnds=stale_login_hwnds,
                    require_adapter_for_terminal_ui=require_strong_connect_transition,
                )
                if connect_transition == "not_started":
                    if _autofill_cancel.is_set():
                        return False, "__GP_CANCELLED__"
                    # Even a physical click can be lost while PanGPA is rebuilding
                    # its popup. Reuse the current hydrated window when possible;
                    # reopen it only when no usable main window remains.
                    log.warning(
                        "connect_globalprotect: Connect had no observable effect; "
                        "retrying once with click_input"
                    )
                    if callable(progress):
                        progress("GlobalProtect ignored the first Connect action. Retrying once...")
                    retry_win = _gp_get_window(timeout=1.5)
                    if not retry_win:
                        gp_exe = _find_exe(
                            GP_EXE_CANDIDATES,
                            self.config.get("bancochile_gp_exe_path", ""),
                        )
                        if gp_exe:
                            if _autofill_cancel.is_set():
                                return False, "__GP_CANCELLED__"
                            _open_gui(gp_exe)
                        retry_win = _gp_get_window(timeout=8)
                    if not retry_win:
                        return False, (
                            "GlobalProtect ignored Connect and its main window could not be "
                            "reopened for a safe retry."
                        )
                    retry_status = _gp_get_status_text(retry_win).strip().casefold()
                    retry_button = _gp_get_button_label(retry_win).strip().casefold()
                    retry_terminal_ui = retry_status == "connected" or (
                        "disconnect" in retry_button and "connect" in retry_button
                    )
                    late_login_hwnds = {
                        hwnd for hwnd, title in _gp_list_terminal_windows(portal)
                        if title == GP_LOGIN_TITLE
                    }
                    if late_login_hwnds - stale_login_hwnds:
                        # The first action did work, but WebView2 published its
                        # Login window after the bounded transition poll.  Never
                        # close that fresh window or press the shared toggle again.
                        log.info(
                            "connect_globalprotect: a fresh Login window appeared "
                            "before the physical retry"
                        )
                        connect_transition = "started"
                    elif (
                        require_strong_connect_transition
                        and retry_terminal_ui
                        and not retry_status.startswith("connecting")
                    ):
                        if _gp_adapter_status().strip().casefold() == "up":
                            connect_transition = "connected_adapter"
                        else:
                            return False, (
                                "GlobalProtect still shows the previous Connected state after "
                                "disconnect. No second button was pressed; wait a moment and retry."
                            )
                    elif retry_status == "connected":
                        connect_transition = "connected_status"
                    elif retry_status.startswith("connecting") or "disconnect" in retry_button:
                        connect_transition = "started"
                    elif (
                        retry_status in {"disconnected", "not connected", "connection failed"}
                        and "connect" in retry_button
                        and "disconnect" not in retry_button
                    ):
                        # Once the first Connect has been sent, no Login window is
                        # safe to close: WebView2 may publish the valid window at
                        # any point during this retry preparation.  Only handles
                        # known before the first action remain stale/ignored.
                        retry_stale_login_hwnds = set(stale_login_hwnds)
                        if _autofill_cancel.is_set():
                            return False, "__GP_CANCELLED__"
                        if not _gp_set_portal(retry_win, portal):
                            return False, (
                                f"Could not reselect the {profile_name} GlobalProtect portal "
                                "for the retry."
                            )
                        if _autofill_cancel.is_set():
                            return False, "__GP_CANCELLED__"
                        # Revalidate both independent signals immediately before the
                        # only physical retry.  The first action may have completed
                        # while PanGPA kept a stale Connect label visible.
                        latest_adapter = _gp_adapter_status().strip().casefold()
                        if latest_adapter == "up":
                            connect_transition = "connected_adapter"
                        elif latest_adapter not in GP_ADAPTER_DISCONNECTED_STATES:
                            return False, (
                                "GlobalProtect adapter status is unknown. No second Connect "
                                "button was pressed; retry after refreshing status."
                            )
                        else:
                            latest_status = _gp_get_status_text(retry_win).strip().casefold()
                            latest_button = _gp_get_button_label(retry_win).strip().casefold()
                            if latest_status.startswith("connecting"):
                                connect_transition = "started"
                            elif not (
                                latest_status
                                in {"disconnected", "not connected", "connection failed"}
                                and "connect" in latest_button
                                and "disconnect" not in latest_button
                            ):
                                return False, (
                                    "GlobalProtect state changed before the safe retry. "
                                    "No second button was pressed."
                                )
                            else:
                                fresh_before_retry = {
                                    hwnd
                                    for hwnd, title in _gp_list_terminal_windows(portal)
                                    if title == GP_LOGIN_TITLE
                                    and hwnd not in retry_stale_login_hwnds
                                }
                                if fresh_before_retry:
                                    connect_transition = "started"
                                else:
                                    if _autofill_cancel.is_set():
                                        return False, "__GP_CANCELLED__"
                                    if not _gp_invoke_connect_button(
                                        retry_win,
                                        expected_action="connect",
                                        prefer_click_input=True,
                                    ):
                                        return False, "Could not retry Connect in GlobalProtect."
                                    connect_transition = _gp_wait_for_connect_transition(
                                        portal,
                                        ignored_login_hwnds=retry_stale_login_hwnds,
                                        require_adapter_for_terminal_ui=(
                                            require_strong_connect_transition
                                        ),
                                    )
                    else:
                        return False, (
                            "GlobalProtect did not reach a safe state for the Connect retry."
                        )

                if connect_transition == "cancelled":
                    return False, "__GP_CANCELLED__"
                if connect_transition == "failed":
                    return False, (
                        "GlobalProtect stopped connecting before sign-in started. Retry the connection."
                    )
                if connect_transition == "not_started":
                    return False, (
                        "GlobalProtect ignored both Connect attempts. The operation was stopped "
                        "instead of waiting for the full sign-in timeout."
                    )

            if connect_transition in {"started", "connected_status", "connected_adapter"}:
                self._gp_disconnect_confirmed = False

            # 5b. Watch connection and known Microsoft pages concurrently.  The
            # encrypted secret is decrypted only if a password page is detected.
            password_requested = [False]

            def _password_provider():
                password_requested[0] = True
                return decrypt_password(password_enc)

            last_main_status = [""]
            next_adapter_probe = [0.0]
            adapter_connected = [False]

            def _status_probe():
                current_window = _gp_get_window(timeout=0.2)
                status = (
                    _gp_get_status_text(current_window).strip().casefold()
                    if current_window
                    else ""
                )
                last_main_status[0] = status
                return status

            def _connected_probe():
                # Only a successful adapter probe at Up confirms the tunnel.
                # PanGPA's UI can remain frozen on Connected after teardown.
                now = time.time()
                if now >= next_adapter_probe[0]:
                    probe_ok, sampled = _gp_adapter_status_sample(max_age=0.0)
                    adapter_connected[0] = (
                        probe_ok and sampled.strip().casefold() == "up"
                    )
                    next_adapter_probe[0] = now + 2.0
                return adapter_connected[0]

            if connect_transition == "connected_adapter":
                outcome = "connected"
            else:
                if connect_transition == "connected_status":
                    last_main_status[0] = "connected"
                outcome = _gp_handle_saml_signin(
                    username,
                    password_provider=_password_provider,
                    total_timeout=150.0,
                    connected_probe=_connected_probe,
                    status_probe=_status_probe,
                    progress=progress,
                    flow_mode=self.config.get("bancochile_flow_mode", "detect"),
                    flow_steps=self.config.get(
                        "bancochile_flow_steps",
                        ["account", "password", "mfa"],
                    ),
                    ignored_login_hwnds=stale_login_hwnds,
                )
            log.info("connect_globalprotect: SAML outcome=%s", outcome)
            if outcome == "connected":
                final_probe_ok, final_adapter = _gp_adapter_status_sample(max_age=0.0)
                if not final_probe_ok:
                    return False, (
                        "GlobalProtect reported Connected, but the adapter state "
                        "could not be verified. Retry status detection in a moment."
                    )
                if final_adapter.strip().casefold() != "up":
                    return False, (
                        "GlobalProtect reported Connected, but its adapter is not Up. "
                        "The connection was not accepted as successful."
                    )
                self._last_gp_target = BANCOCHILE
                self._gp_disconnect_confirmed = False
                # Closing is best-effort and never changes the connection result.
                # Require both exact UI confirmation and the live adapter before
                # touching SAML/Notification windows; adapter-only success can be
                # transient while Microsoft is still completing the handoff.
                _gp_cleanup_after_confirmed_connection(portal)
                if password_requested[0]:
                    return True, f"{profile_name} VPN connected after password and MFA."
                return True, f"{profile_name} VPN connected without requesting the saved password."
            if outcome == "password_required":
                return False, "__GP_PASSWORD_REQUIRED__"
            if outcome == "username_required":
                return False, "__GP_USERNAME_REQUIRED__"
            if outcome == "wrong_password":
                return False, "__GP_WRONG_PASSWORD__"
            if outcome == "cancelled":
                return False, "__GP_CANCELLED__"
            if outcome == "denied":
                return False, "Microsoft denied the MFA sign-in request."
            if outcome == "mfa_timeout":
                return False, "Timed out waiting for Microsoft Authenticator approval."
            if outcome == "connection_failed":
                return False, "GlobalProtect stopped connecting before sign-in completed. Retry the connection."
            if outcome == "authentication_failed":
                return False, (
                    f"{profile_name} GlobalProtect reported Authentication Failed. "
                    "The portal rejected the Microsoft SAML session. Retry the connection; "
                    "if it repeats, the VPN access or SAML mapping must be checked by its "
                    "administrator."
                )
            if outcome == "adapter_not_up":
                return False, (
                    "GlobalProtect reported Connected, but its adapter did not "
                    "reach Up within the safe verification window. Retry after "
                    "the client finishes settling."
                )
            if outcome in {
                "password_input_failed",
                "password_submit_failed",
                "email_input_failed",
                "email_submit_failed",
                "unknown_page",
            }:
                return False, "GlobalProtect sign-in page could not be completed safely."
            _gp_diagnostics()
            return False, "GlobalProtect did not connect before the timeout."

    def disconnect_bancochile(self) -> Tuple[bool, str]:
            log = get_logger()
            log.info("disconnect_globalprotect: ===== attempt started =====")
            if _autofill_cancel.is_set():
                return False, "__GP_CANCELLED__"
            _gp_diagnostics()

            active_portal = (
                self.config.get("bancochile_portal_url", BANCOCHILE_PORTAL)
                or BANCOCHILE_PORTAL
            )

            def _finished(
                message: str,
                *,
                apply_reconnect_cooldown: bool = False,
            ) -> Tuple[bool, str]:
                self._gp_disconnect_confirmed = True
                self._last_gp_target = None
                if apply_reconnect_cooldown:
                    self._next_connect_not_before = max(
                        self._next_connect_not_before,
                        time.monotonic() + GP_POST_DISCONNECT_COOLDOWN_SECONDS,
                    )
                _gp_cleanup_terminal_windows(active_portal, wait_seconds=0.5)
                return True, message

            gp_exe = _find_exe(
                GP_EXE_CANDIDATES,
                self.config.get("bancochile_gp_exe_path", ""),
            )
            if _autofill_cancel.is_set():
                return False, "__GP_CANCELLED__"
            # Always ask PanGPA for a visible, freshly wrapped main popup before
            # inspecting the shared Connect/Disconnect toggle. This is harmless
            # when it is already visible and avoids acting on a hidden stale HWND.
            win = _ensure_gp_main_window(gp_exe or "")

            if not win:
                if _autofill_cancel.is_set():
                    return False, "__GP_CANCELLED__"
                log.error("disconnect_globalprotect: window not found")
                return False, "GlobalProtect window not found."

            try:
                win.set_focus()
            except Exception:
                pass

            status_before = _gp_get_status_text(win)
            button_label = _gp_get_button_label(win)
            log.info(f"disconnect_globalprotect: pre-click status='{status_before}' button='{button_label}'")

            # A disconnected-looking UI must not cause us to press the shared
            # Connect/Disconnect toggle.  Let the bounded adapter polling below
            # confirm teardown instead; the adapter can lag behind PanGPA's text.
            low_before = status_before.strip().lower()
            adapter_probe_ok, adapter_sample = _gp_adapter_status_sample(max_age=0.0)
            if not adapter_probe_ok:
                return False, (
                    "Could not verify the GlobalProtect adapter state. "
                    "No disconnect action was taken; retry in a moment."
                )
            adapter_before = adapter_sample.strip().casefold()
            button_state = button_label.strip().casefold()
            disconnect_deadline = (
                time.monotonic() + GP_DISCONNECT_VERIFY_TIMEOUT_SECONDS
            )

            def _reacquire_disconnect_snapshot():
                """Return one visible, current popup snapshot for a physical action."""
                if _autofill_cancel.is_set():
                    return "cancelled", None, "", "", ""
                if time.monotonic() >= disconnect_deadline:
                    return "timed_out", None, "", "", ""
                fresh_win = _ensure_gp_main_window(
                    gp_exe or "",
                    deadline=disconnect_deadline,
                )
                if _autofill_cancel.is_set():
                    return "cancelled", None, "", "", ""
                if not fresh_win:
                    return (
                        "timed_out" if time.monotonic() >= disconnect_deadline else "missing",
                        None,
                        "",
                        "",
                        "",
                    )
                fresh_status = _gp_get_status_text(fresh_win).strip().casefold()
                fresh_button = _gp_get_button_label(fresh_win).strip().casefold()
                remaining = disconnect_deadline - time.monotonic()
                if remaining < 0.1:
                    return "timed_out", fresh_win, fresh_status, fresh_button, ""
                fresh_probe_ok, fresh_sample = _gp_adapter_status_sample(
                    max_age=0.0,
                    probe_timeout=min(GP_ADAPTER_PROBE_TIMEOUT_SECONDS, remaining),
                )
                if not fresh_probe_ok:
                    return "adapter_unknown", fresh_win, fresh_status, fresh_button, ""
                fresh_adapter = fresh_sample.strip().casefold()
                return (
                    "ready",
                    fresh_win,
                    fresh_status,
                    fresh_button,
                    fresh_adapter,
                )

            def _snapshot_has_safe_disconnect(
                snapshot_status: str,
                snapshot_button: str,
                snapshot_adapter: str,
                *,
                require_adapter_up: bool = False,
            ) -> bool:
                if "disconnect" not in snapshot_button:
                    return False
                if require_adapter_up:
                    return (
                        snapshot_adapter == "up"
                        and (
                            snapshot_status == "connected"
                            or snapshot_status.startswith("connecting")
                        )
                    )
                if snapshot_status == "connected":
                    # A terminal/unknown adapter with a lingering Connected label
                    # is the stale-frame race that can turn a second click into a
                    # new Connect attempt.
                    return snapshot_adapter == "up"
                # Connecting can be cancelled before the adapter reaches Up.
                return snapshot_status.startswith("connecting")

            terminal_before = low_before in {
                "disconnected",
                "not connected",
                "connection failed",
            }
            connecting_before = low_before.startswith("connecting")
            disconnecting_before = low_before.startswith("disconnecting")
            connect_button_before = (
                "connect" in button_state and "disconnect" not in button_state
            )
            disconnect_button_before = "disconnect" in button_state
            stale_connected_without_adapter = (
                low_before == "connected"
                and disconnect_button_before
                and (
                    not adapter_before
                    or adapter_before in GP_ADAPTER_DISCONNECTED_STATES
                )
            )
            initially_disconnected_ui = (
                terminal_before or (not low_before and connect_button_before)
            )
            disconnect_action_sent = False
            disconnect_already_in_progress = disconnecting_before
            if disconnecting_before:
                log.info(
                    "disconnect_globalprotect: UI already reports Disconnecting; "
                    "waiting without pressing the toggle again"
                )
            elif connecting_before:
                if _snapshot_has_safe_disconnect(
                    low_before,
                    button_state,
                    adapter_before,
                ):
                    if _autofill_cancel.is_set():
                        return False, "__GP_CANCELLED__"
                    if not _gp_invoke_connect_button(win, expected_action="disconnect"):
                        return False, "Could not cancel the GlobalProtect connection attempt."
                    disconnect_action_sent = True
                else:
                    log.info(
                        "disconnect_globalprotect: UI is Connecting but its Disconnect "
                        "control is not ready; waiting for a safe cancellation control"
                    )
            elif stale_connected_without_adapter:
                log.info(
                    "disconnect_globalprotect: UI says Connected but the adapter is "
                    "already terminal; treating the toggle as stale until independent "
                    "state confirms a tunnel or a safe disconnected UI"
                )
            elif not initially_disconnected_ui and _snapshot_has_safe_disconnect(
                low_before,
                button_state,
                adapter_before,
            ):
                if _autofill_cancel.is_set():
                    return False, "__GP_CANCELLED__"
                if not _gp_invoke_connect_button(win, expected_action="disconnect"):
                    log.error("disconnect_globalprotect: failed to click Disconnect button")
                    return False, "Could not click Disconnect button."
                disconnect_action_sent = True
            elif not initially_disconnected_ui:
                log.info(
                    "disconnect_globalprotect: visible state/adapter did not jointly "
                    "confirm a safe Disconnect action; waiting without clicking"
                )
            else:
                log.info(
                    "disconnect_globalprotect: UI already looks disconnected; "
                    "waiting for adapter confirmation without clicking the toggle"
                )

            # Wait for state to flip.  The popup can hide normally after a physical
            # click, so keep probing the adapter and only rehydrate the main window
            # after a bounded period with no observable effect.
            last_status = status_before
            relaunched = False
            physical_retry = False
            cancel_after_action = False
            cancel_verification_attempts = 0
            adapter_clear_samples = (
                1
                if initially_disconnected_ui
                and (
                    not adapter_before
                    or adapter_before in GP_ADAPTER_DISCONNECTED_STATES
                )
                else 0
            )
            saw_adapter_probe_failure = False
            attempt_index = 0
            while (
                attempt_index < 18
                and time.monotonic() < disconnect_deadline
            ):
                attempt = attempt_index
                attempt_index += 1
                if _autofill_cancel.is_set():
                    if not (disconnect_action_sent or disconnect_already_in_progress):
                        return False, "__GP_CANCELLED__"
                    cancel_after_action = True
                remaining = disconnect_deadline - time.monotonic()
                if remaining <= 0.0:
                    break
                time.sleep(min(0.2 if cancel_after_action else 0.7, remaining))
                if _autofill_cancel.is_set():
                    if not (disconnect_action_sent or disconnect_already_in_progress):
                        return False, "__GP_CANCELLED__"
                    cancel_after_action = True
                remaining = disconnect_deadline - time.monotonic()
                if remaining <= 0.0:
                    break
                cur_win = _gp_get_window(timeout=min(0.5, remaining))
                cur_status = _gp_get_status_text(cur_win) if cur_win else ""
                if cur_status and cur_status != last_status:
                    log.info(f"disconnect_globalprotect: status '{last_status}' -> '{cur_status}'")
                    last_status = cur_status
                cur_low = cur_status.strip().lower()
                if cur_low.startswith("disconnecting"):
                    disconnect_already_in_progress = True
                cur_button = (
                    _gp_get_button_label(cur_win).strip().casefold() if cur_win else ""
                )
                terminal_status = cur_low in {
                    "disconnected",
                    "not connected",
                    "connection failed",
                }
                connect_button = "connect" in cur_button and "disconnect" not in cur_button
                ui_reports_disconnected = (
                    terminal_status or (not cur_low and connect_button)
                )

                if (
                    stale_connected_without_adapter
                    and cur_low.startswith("connecting")
                    and "disconnect" in cur_button
                ):
                    log.info(
                        "disconnect_globalprotect: a fresh Connecting state replaced "
                        "the stale Connected UI; its Disconnect control is now safe"
                    )
                    stale_connected_without_adapter = False
                    adapter_clear_samples = 0

                adapter_only_confirmation_allowed = (
                    disconnect_action_sent or disconnect_already_in_progress
                )
                should_probe_adapter = ui_reports_disconnected or stale_connected_without_adapter or (
                    adapter_only_confirmation_allowed
                    and (cancel_after_action or not cur_win or attempt % 3 == 2)
                )
                if should_probe_adapter:
                    remaining = disconnect_deadline - time.monotonic()
                    if remaining < 0.1:
                        break
                    probe_ok, sampled_adapter = _gp_adapter_status_sample(
                        max_age=0.0,
                        probe_timeout=min(
                            GP_ADAPTER_PROBE_TIMEOUT_SECONDS,
                            remaining,
                        ),
                    )
                    if not probe_ok:
                        saw_adapter_probe_failure = True
                        adapter_clear_samples = 0
                        log.warning(
                            "disconnect_globalprotect: adapter probe failed during "
                            "teardown verification"
                        )
                        continue
                    adapter_state = sampled_adapter.strip().casefold()
                    if stale_connected_without_adapter and adapter_state == "up":
                        log.info(
                            "disconnect_globalprotect: adapter became Up; the Connected "
                            "toggle is now safe to use"
                        )
                        stale_connected_without_adapter = False
                        adapter_clear_samples = 0
                    elif (
                        (
                            not adapter_state
                            or adapter_state in GP_ADAPTER_DISCONNECTED_STATES
                        )
                        and (
                            ui_reports_disconnected
                            or not stale_connected_without_adapter
                        )
                    ):
                        adapter_clear_samples += 1
                        if adapter_clear_samples >= 2:
                            log.info(
                                "disconnect_globalprotect: adapter confirms disconnected "
                                "state=%r",
                                adapter_state,
                            )
                            completed_transition = (
                                disconnect_action_sent
                                or disconnect_already_in_progress
                            )
                            finished = _finished(
                                "GlobalProtect disconnected."
                                if completed_transition
                                else "GlobalProtect already disconnected.",
                                apply_reconnect_cooldown=completed_transition,
                            )
                            if cancel_after_action:
                                return False, "__GP_CANCELLED__"
                            return finished
                    else:
                        adapter_clear_samples = 0

                safe_disconnect_control = (
                    cur_win
                    and not stale_connected_without_adapter
                    and (
                        cur_low == "connected"
                        or cur_low.startswith("connecting")
                    )
                    and "disconnect" in cur_button
                )
                if (
                    safe_disconnect_control
                    and not disconnect_action_sent
                    and not cancel_after_action
                    and attempt >= 1
                ):
                    log.info(
                        "disconnect_globalprotect: UI returned to Connected; "
                        "sending the first safe Disconnect action"
                    )
                    (
                        snapshot_kind,
                        action_win,
                        action_status,
                        action_button,
                        action_adapter,
                    ) = _reacquire_disconnect_snapshot()
                    if snapshot_kind == "cancelled":
                        return False, "__GP_CANCELLED__"
                    if snapshot_kind != "ready":
                        log.info(
                            "disconnect_globalprotect: popup could not be reacquired "
                            "before the first deferred Disconnect action"
                        )
                        continue
                    if not _snapshot_has_safe_disconnect(
                        action_status,
                        action_button,
                        action_adapter,
                    ):
                        if action_status.startswith("disconnecting"):
                            disconnect_already_in_progress = True
                        log.info(
                            "disconnect_globalprotect: state changed before the first "
                            "deferred Disconnect action; no button was pressed"
                        )
                        continue
                    if not _gp_invoke_connect_button(
                        action_win,
                        expected_action="disconnect",
                    ):
                        return False, "Could not click the GlobalProtect Disconnect button."
                    disconnect_action_sent = True
                    adapter_clear_samples = 0
                    continue

                if (
                    safe_disconnect_control
                    and disconnect_action_sent
                    and not cancel_after_action
                    and not physical_retry
                    and attempt >= 3
                ):
                    log.warning(
                        "disconnect_globalprotect: first action had no observable effect; "
                        "retrying once with click_input"
                    )
                    (
                        snapshot_kind,
                        retry_win,
                        latest_status,
                        latest_button,
                        retry_adapter,
                    ) = _reacquire_disconnect_snapshot()
                    if snapshot_kind == "cancelled":
                        return False, "__GP_CANCELLED__"
                    if snapshot_kind != "ready":
                        log.info(
                            "disconnect_globalprotect: popup could not be reacquired "
                            "before the physical retry"
                        )
                        continue
                    if retry_adapter != "up":
                        log.info(
                            "disconnect_globalprotect: skipping physical retry because "
                            "adapter is no longer confirmed Up (state=%r)",
                            retry_adapter or "unknown",
                        )
                        if (
                            not retry_adapter
                            or retry_adapter in GP_ADAPTER_DISCONNECTED_STATES
                        ):
                            # Once teardown is independently visible, a later stale
                            # Connected label must never re-enable the second click.
                            physical_retry = True
                        continue
                    if not _snapshot_has_safe_disconnect(
                        latest_status,
                        latest_button,
                        retry_adapter,
                        require_adapter_up=True,
                    ):
                        log.info(
                            "disconnect_globalprotect: state changed before physical retry; "
                            "continuing verification without another click"
                        )
                        continue
                    if not _gp_invoke_connect_button(
                        retry_win,
                        expected_action="disconnect",
                        prefer_click_input=True,
                    ):
                        return False, "Could not retry the GlobalProtect Disconnect button."
                    physical_retry = True
                    continue

                needs_rehydrate = (
                    (not cur_win or (not cur_status and not cur_button))
                    and not cancel_after_action
                    and not relaunched
                    and (
                        not disconnect_action_sent
                        or attempt >= 8
                    )
                )
                if needs_rehydrate:
                    log.info(
                        "disconnect_globalprotect: main controls disappeared; reopening "
                        "PanGPA to verify the action"
                    )
                    (
                        snapshot_kind,
                        rehydrated_win,
                        rehydrated_status,
                        rehydrated_button,
                        rehydrated_adapter,
                    ) = _reacquire_disconnect_snapshot()
                    if snapshot_kind == "cancelled":
                        return False, "__GP_CANCELLED__"
                    relaunched = True
                    if snapshot_kind == "ready":
                        cur_win = rehydrated_win
                        log.info(
                            "disconnect_globalprotect: rehydrated snapshot status=%r "
                            "button=%r adapter=%r",
                            rehydrated_status,
                            rehydrated_button,
                            rehydrated_adapter,
                        )
                    else:
                        log.info(
                            "disconnect_globalprotect: PanGPA popup remained unavailable "
                            "after the bounded rehydrate attempt"
                        )

                if cancel_after_action:
                    cancel_verification_attempts += 1
                    if cancel_verification_attempts >= 4:
                        log.info(
                            "disconnect_globalprotect: cancellation requested after the "
                            "Disconnect action; bounded teardown verification ended"
                        )
                        return False, "__GP_CANCELLED__"

            if time.monotonic() >= disconnect_deadline:
                log.warning(
                    "disconnect_globalprotect: verification reached %.1fs deadline",
                    GP_DISCONNECT_VERIFY_TIMEOUT_SECONDS,
                )
            if saw_adapter_probe_failure:
                return False, (
                    "GlobalProtect disconnect could not be verified because the "
                    "adapter status probe failed."
                )
            log.warning(f"disconnect_globalprotect: did not confirm disconnect, last='{last_status}'")
            return False, "GlobalProtect did not finish disconnecting."
