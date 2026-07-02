import os
import subprocess
import threading
import time
from typing import Optional, Tuple

from paths import DATA_DIR
from settings.config import decrypt_password
from features.vpn.logger import get_logger


def _setup_comtypes_cache():
    """Redirect comtypes' generated-proxy cache to a user-writable folder.

    pywinauto's UIA backend uses comtypes, which writes generated COM proxy
    modules into its own site-packages/comtypes/gen/ on first use. When Python
    is installed system-wide (C:\\Program Files\\Python3xx), that path is
    read-only — the write raises PermissionError, UIA initialization fails
    silently, and every UIA window lookup returns None.

    1.0.15's first attempt guarded the patch with hasattr(comtypes, "gen"),
    but comtypes.gen is a lazy subpackage that isn't auto-imported with
    comtypes itself, so the guard was always False and the patch was a no-op.
    Here we force-import it first, then prepend, then also override
    _find_gen_dir directly as a fallback in case __path__ isn't honored.
    """
    cache = str(DATA_DIR / "comtypes_cache")
    log = get_logger()
    try:
        os.makedirs(cache, exist_ok=True)
        init = os.path.join(cache, "__init__.py")
        if not os.path.exists(init):
            with open(init, "w", encoding="utf-8") as f:
                f.write("# Oracle Tasks comtypes cache\n")

        # Force-load the lazy subpackage so __path__ exists, then prepend
        # our writable cache. comtypes resolves gen_dir from gen.__path__[0].
        try:
            import comtypes.gen  # noqa: F401
            if cache not in comtypes.gen.__path__:
                comtypes.gen.__path__.insert(0, cache)
            log.info(f"comtypes_cache: redirected to {cache}; "
                     f"gen.__path__={list(comtypes.gen.__path__)}")
        except Exception as e:
            log.warning(f"comtypes_cache: __path__ patch failed: {e}")

        # Belt-and-suspenders: replace _find_gen_dir directly. This is the
        # function comtypes.client calls to decide where to write proxies.
        try:
            from comtypes.client import _code_cache
            _code_cache._find_gen_dir = lambda: cache
        except Exception as e:
            log.warning(f"comtypes_cache: _find_gen_dir patch failed: {e}")
    except Exception as e:
        log.warning(f"comtypes_cache: setup failed: {e}")


_setup_comtypes_cache()


# Global cancellation flag — set to cancel any in-progress autofill
_autofill_cancel = threading.Event()

CISCO = "cisco"
FORTI = "forti"
GPROT = "globalprotect"
NONE = "disconnected"

CISCO_CLI_CANDIDATES = [
    r"C:\Program Files (x86)\Cisco\Cisco Secure Client\vpncli.exe",
    r"C:\Program Files (x86)\Cisco\Cisco AnyConnect Secure Mobility Client\vpncli.exe",
    r"C:\Program Files\Cisco\Cisco Secure Client\vpncli.exe",
]

CISCO_UI_CANDIDATES = [
    r"C:\Program Files (x86)\Cisco\Cisco Secure Client\UI\csc_ui.exe",
    r"C:\Program Files (x86)\Cisco\Cisco Secure Client\vpnui.exe",
    r"C:\Program Files (x86)\Cisco\Cisco AnyConnect Secure Mobility Client\vpnui.exe",
]

FORTI_EXE_CANDIDATES = [
    r"C:\Program Files\Fortinet\FortiClient\FortiClient.exe",
    r"C:\Program Files\Fortinet\FortiClientVPN\FortiClientVPN.exe",
    r"C:\Program Files (x86)\Fortinet\FortiClient\FortiClient.exe",
]

GP_EXE_CANDIDATES = [
    r"C:\Program Files\Palo Alto Networks\GlobalProtect\PanGPA.exe",
    r"C:\Program Files (x86)\Palo Alto Networks\GlobalProtect\PanGPA.exe",
]

# GlobalProtect window/control identifiers (verified via UIA dump from a real
# BANCO BICE installation on 2026-04-28).
GP_TITLE = "GlobalProtect"
GP_LOGIN_TITLE = "GlobalProtect Login"
GP_CLASS = "#32770"
GP_BTN_CONNECT_AUTOID = "1160"   # Connect / Disconnect "button" (reports as Pane)
GP_STATUS_AUTOID = "1165"        # Static text: Disconnected / Connecting... / Connected
GP_PORTAL_AUTOID = "1119"        # MFCMenuButton showing portal URL (e.g. ext.bice.cl)
GP_PORTAL_LABEL_AUTOID = "1314"  # 'Portal' label
GP_DESC_AUTOID = "1087"          # Description static beneath status
GP_ADAPTER_KEYWORDS = (
    "palo alto",
    "globalprotect",
    "global protect",
    "pangp",
)

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


def _find_visible_hwnd(title_fragment: str):
    """Return the hwnd of the first visible window whose title contains title_fragment."""
    import ctypes
    import ctypes.wintypes
    user32 = ctypes.windll.user32
    target = [None]

    def enum_callback(hwnd, _):
        length = user32.GetWindowTextLengthW(hwnd)
        if length > 0:
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            if title_fragment.lower() in buf.value.lower():
                if user32.IsWindowVisible(hwnd):
                    target[0] = hwnd
        return True

    WNDENUMPROC = ctypes.WINFUNCTYPE(
        ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM
    )
    user32.EnumWindows(WNDENUMPROC(enum_callback), 0)
    return target[0]


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


def _bring_window_to_front(title_fragment: str) -> bool:
    """Find a visible window and force it to the foreground."""
    hwnd = _find_visible_hwnd(title_fragment)
    if hwnd:
        return _force_foreground(hwnd)
    return False


def _wait_and_bring_to_front(title_fragment: str, timeout: float = 12.0) -> bool:
    """Poll until a window with title_fragment appears, then force it to front."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        hwnd = _find_visible_hwnd(title_fragment)
        if hwnd:
            return _force_foreground(hwnd)
        time.sleep(0.4)
    return False


FORTI_TITLE = "FortiClient - Zero Trust Fabric Agent"


def _forti_client_running() -> bool:
    """Return whether the interactive FortiClient process is still alive."""
    try:
        import psutil

        for process in psutil.process_iter(["name"]):
            try:
                if (process.info.get("name") or "").lower() == "forticlient.exe":
                    return True
            except Exception:
                continue
    except Exception:
        # Process detection is a responsiveness aid, not a connection prerequisite.
        return True
    return False


def _forti_wait(seconds: float) -> str:
    """Wait interruptibly and report when automation is cancelled or Forti exits."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if _autofill_cancel.is_set():
            return "cancelled"
        if not _forti_client_running():
            return "closed"
        time.sleep(min(0.25, max(0.0, deadline - time.monotonic())))
    return ""


def _forti_dismiss_error_dialog() -> bool:
    """Auto-click OK on the Electron crash dialog FortiClient sometimes shows on launch.
    Returns True if a dialog was found and dismissed."""
    try:
        import ctypes
        user32 = ctypes.windll.user32
        hwnd = user32.FindWindowW(None, "Error")
        if not hwnd:
            return False
        # Try every locale variant of the OK button text.
        for label in ("OK", "Aceptar", "확인", "Ok"):
            ok_hwnd = user32.FindWindowExW(hwnd, None, "Button", label)
            if ok_hwnd:
                user32.SendMessageW(ok_hwnd, 0x00F5, 0, 0)  # BM_CLICK
                return True
        # Fallback: close via WM_COMMAND IDOK=1
        user32.SendMessageW(hwnd, 0x0111, 1, 0)  # WM_COMMAND, IDOK
        return True
    except Exception:
        return False


def _forti_diagnostics():
    """Dump every Forti-related process + every window containing 'forti' (any case)
    into the log, visible or hidden. Called when normal detection fails so we can
    debug remotely from the log file."""
    log = get_logger()
    # --- Processes ---
    try:
        import psutil
        procs = []
        for p in psutil.process_iter(["pid", "name", "exe"]):
            try:
                name = (p.info.get("name") or "").lower()
                if "forti" in name:
                    procs.append(f"{p.info['pid']}:{p.info['name']}")
            except Exception:
                pass
        log.info(f"forti_diag: processes={procs or 'NONE'}")
    except Exception as e:
        log.warning(f"forti_diag: process enum failed: {e}")

    # --- Windows (all top-level, visible or hidden) ---
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
                if "forti" in title.lower() or title == "Error":
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
                log.info(f"forti_diag: window {m}")
        else:
            log.info("forti_diag: no forti/error windows found")
    except Exception as e:
        log.warning(f"forti_diag: window enum failed: {e}")

    # --- FortiClient executable metadata ---
    try:
        forti_exe = _find_exe(FORTI_EXE_CANDIDATES, "")
        if forti_exe:
            exists = os.path.exists(forti_exe)
            size = os.path.getsize(forti_exe) if exists else -1
            log.info(f"forti_diag: forti_exe={forti_exe} exists={exists} size={size}")
            # Version info via ctypes (no pywin32 dep)
            try:
                import ctypes
                from ctypes import wintypes
                ver = ctypes.windll.version
                size_needed = ver.GetFileVersionInfoSizeW(forti_exe, None)
                if size_needed:
                    buf = ctypes.create_string_buffer(size_needed)
                    if ver.GetFileVersionInfoW(forti_exe, 0, size_needed, buf):
                        lplp = ctypes.c_void_p()
                        u = ctypes.c_uint()
                        if ver.VerQueryValueW(buf, "\\", ctypes.byref(lplp), ctypes.byref(u)):
                            class FFI(ctypes.Structure):
                                _fields_ = [
                                    ("dwSignature", wintypes.DWORD),
                                    ("dwStrucVersion", wintypes.DWORD),
                                    ("dwFileVersionMS", wintypes.DWORD),
                                    ("dwFileVersionLS", wintypes.DWORD),
                                    ("dwProductVersionMS", wintypes.DWORD),
                                    ("dwProductVersionLS", wintypes.DWORD),
                                    ("dwFileFlagsMask", wintypes.DWORD),
                                    ("dwFileFlags", wintypes.DWORD),
                                    ("dwFileOS", wintypes.DWORD),
                                    ("dwFileType", wintypes.DWORD),
                                    ("dwFileSubtype", wintypes.DWORD),
                                    ("dwFileDateMS", wintypes.DWORD),
                                    ("dwFileDateLS", wintypes.DWORD),
                                ]
                            ffi = ctypes.cast(lplp, ctypes.POINTER(FFI)).contents
                            v = (
                                ffi.dwFileVersionMS >> 16,
                                ffi.dwFileVersionMS & 0xFFFF,
                                ffi.dwFileVersionLS >> 16,
                                ffi.dwFileVersionLS & 0xFFFF,
                            )
                            log.info(f"forti_diag: forti_exe_version={'.'.join(map(str, v))}")
            except Exception as e:
                log.warning(f"forti_diag: version probe failed: {e}")
            # Critical resource files
            try:
                folder = os.path.dirname(forti_exe)
                resources = os.path.join(folder, "resources")
                asar = os.path.join(resources, "app.asar")
                log.info(
                    f"forti_diag: resources_dir exists={os.path.isdir(resources)} "
                    f"app.asar exists={os.path.isfile(asar)} "
                    f"app.asar size={os.path.getsize(asar) if os.path.isfile(asar) else -1}"
                )
            except Exception as e:
                log.warning(f"forti_diag: resource probe failed: {e}")
        else:
            log.info("forti_diag: forti_exe not found in any candidate path")
    except Exception as e:
        log.warning(f"forti_diag: exe metadata failed: {e}")

    # --- VC++ Redistributables (root cause candidate for JS TraceLog crash) ---
    try:
        import winreg
        vc_keys = [
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x86"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\VisualStudio\14.0\VC\Runtimes\x64"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\VisualStudio\14.0\VC\Runtimes\x86"),
        ]
        found_any = False
        for root, path in vc_keys:
            try:
                with winreg.OpenKey(root, path) as k:
                    ver_val = winreg.QueryValueEx(k, "Version")[0]
                    installed = winreg.QueryValueEx(k, "Installed")[0]
                    log.info(f"forti_diag: VC++ {path} Version={ver_val} Installed={installed}")
                    found_any = True
            except FileNotFoundError:
                continue
            except Exception as e:
                log.warning(f"forti_diag: VC++ probe {path} failed: {e}")
        if not found_any:
            log.warning("forti_diag: NO VC++ 2015-2022 runtime keys found in registry")
    except Exception as e:
        log.warning(f"forti_diag: VC++ enum failed: {e}")

    # --- Recent Application Error events for FortiClient ---
    try:
        cmd = [
            "wevtutil", "qe", "Application",
            "/q:*[System[Provider[@Name='Application Error'] and TimeCreated[timediff(@SystemTime) <= 600000]]]",
            "/c:5", "/rd:true", "/f:text",
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10,
                          creationflags=0x08000000)
        out = (r.stdout or "").strip()
        if out:
            # Just log FortiClient-related snippets (keep log size sane)
            lines = out.splitlines()
            forti_lines = [l for l in lines if "forti" in l.lower()]
            if forti_lines:
                for l in forti_lines[:10]:
                    log.info(f"forti_diag: evtlog: {l.strip()}")
            else:
                log.info("forti_diag: evtlog: no recent FortiClient Application Errors")
        else:
            log.info("forti_diag: evtlog: no recent Application Errors")
    except Exception as e:
        log.warning(f"forti_diag: event log query failed: {e}")


def _forti_monitor_processes(seconds: int = 10):
    """Sample FortiClient.exe PIDs over `seconds` and log which ones die + exit codes."""
    log = get_logger()
    try:
        import psutil, ctypes
        kernel32 = ctypes.windll.kernel32
        start_pids = {}
        for p in psutil.process_iter(["pid", "name"]):
            try:
                if (p.info.get("name") or "").lower() == "forticlient.exe":
                    start_pids[p.info["pid"]] = p
            except Exception:
                pass
        log.info(f"forti_monitor: initial FortiClient.exe PIDs={list(start_pids.keys())}")
        time.sleep(seconds)
        still_alive = []
        died = []
        for pid, p in start_pids.items():
            try:
                if p.is_running() and p.status() != psutil.STATUS_ZOMBIE:
                    still_alive.append(pid)
                else:
                    died.append(pid)
            except Exception:
                died.append(pid)
        # Try to get exit codes for dead ones
        exit_codes = {}
        for pid in died:
            try:
                PROCESS_QUERY_LIMITED = 0x1000
                h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED, False, pid)
                if h:
                    code = ctypes.c_ulong()
                    kernel32.GetExitCodeProcess(h, ctypes.byref(code))
                    exit_codes[pid] = code.value
                    kernel32.CloseHandle(h)
            except Exception:
                pass
        log.info(f"forti_monitor: after {seconds}s alive={still_alive} died={died} exit_codes={exit_codes}")
    except Exception as e:
        log.warning(f"forti_monitor: failed: {e}")


def _forti_restore_tray_window() -> bool:
    """Find and restore a hidden FortiClient tray window without launching a new process.
    Returns True if a FortiClient window (visible or hidden) was found and shown."""
    log = get_logger()
    try:
        import ctypes
        user32 = ctypes.windll.user32
        found = [None]
        found_title = [""]

        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

        def _enum(hwnd, _):
            length = user32.GetWindowTextLengthW(hwnd)
            if length:
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                if buf.value == FORTI_TITLE:
                    found[0] = hwnd
                    found_title[0] = buf.value
                    return False  # stop enumeration
            return True

        user32.EnumWindows(WNDENUMPROC(_enum), 0)
        if found[0]:
            was_visible = bool(user32.IsWindowVisible(found[0]))
            sw_rc = user32.ShowWindow(found[0], 9)   # SW_RESTORE
            fg_rc = user32.SetForegroundWindow(found[0])
            log.info(f"forti_restore: hwnd={found[0]} title='{found_title[0]}' "
                     f"was_visible={was_visible} ShowWindow={sw_rc} SetForeground={fg_rc}")
            return True
        log.info("forti_restore: no window matching FORTI_TITLE found")
    except Exception as e:
        log.warning(f"forti_restore: exception {e}")
    return False


def _forti_get_window(timeout: float = 1.0):
    """Find the FortiClient Electron window via pywinauto UIA backend.
    Returns the window wrapper or None."""
    log = get_logger()
    try:
        from pywinauto import Desktop
        deadline = time.time() + timeout
        while time.time() < deadline:
            _forti_dismiss_error_dialog()
            try:
                desktop = Desktop(backend="uia")
                wins = [w for w in desktop.windows()
                        if w.window_text() == FORTI_TITLE]
            except Exception as e:
                # UIA init can fail with PermissionError when comtypes can't
                # write its proxy cache (system-wide Python install). Surface
                # it so users know to grant write access or run from a venv.
                log.warning(f"_forti_get_window: UIA query failed: {e}")
                wins = []
            if wins:
                return wins[0]
            time.sleep(0.5)
    except Exception as e:
        log.warning(f"_forti_get_window: outer failure: {e}")
    return None


def _find_signin_window():
    """Find the FortiClient SAML sign-in popup (Chromium window).
    Matches English, Spanish, and FortiClient-titled popups."""
    try:
        from pywinauto import Desktop
        desktop = Desktop(backend="win32")
        for w in desktop.windows():
            title = w.window_text().lower()
            if ("sign in to your account" in title
                    or "iniciar sesión" in title
                    or ("forticlient" in title and "(" in title)):
                return w
    except Exception:
        pass
    return None


def _focus_and_type(hwnd, text, press_enter=True):
    """Force-focus a window, clear the field, type text, optionally press Enter.
    Retries focus for up to 10 seconds. Only types if we actually have focus.
    Aborts immediately if _autofill_cancel is set."""
    from pywinauto import keyboard
    import ctypes
    user32 = ctypes.windll.user32

    if _autofill_cancel.is_set():
        return False

    # Keep trying to get focus for up to 10 seconds
    deadline = time.time() + 10
    got_focus = False
    while time.time() < deadline:
        if _autofill_cancel.is_set():
            return False
        _force_foreground(hwnd)
        time.sleep(0.5)
        if user32.GetForegroundWindow() == hwnd:
            got_focus = True
            break
        time.sleep(0.8)

    if not got_focus or _autofill_cancel.is_set():
        return False

    time.sleep(0.3)
    if user32.GetForegroundWindow() != hwnd or _autofill_cancel.is_set():
        return False

    keyboard.send_keys("^a", pause=0.05)
    time.sleep(0.15)
    keyboard.send_keys(text, with_spaces=True, pause=0.02)
    time.sleep(0.2)
    if press_enter:
        keyboard.send_keys("{ENTER}", pause=0.05)
    return True


def _get_signin_page_text() -> str:
    """Try to read all visible text from the sign-in Chromium window via UIA.
    Returns concatenated lowercase text of all accessible elements.
    Also writes to a debug log for troubleshooting."""
    import os, tempfile
    result = ""
    try:
        from pywinauto import Desktop
        desktop = Desktop(backend="uia")
        for w in desktop.windows():
            title = w.window_text().lower()
            if "sign in to your account" in title or "iniciar sesión" in title:
                texts = []
                for ctrl in w.descendants():
                    try:
                        t = ctrl.window_text().strip()
                        if t:
                            texts.append(t.lower())
                    except Exception:
                        pass
                result = " ".join(texts)
                break
    except Exception as e:
        result = f"[ERROR: {e}]"

    # Write debug log
    try:
        log_path = os.path.join(tempfile.gettempdir(), "vpnswitcher_uia_debug.txt")
        with open(log_path, "w", encoding="utf-8") as f:
            f.write(result)
    except Exception:
        pass

    return result


def _check_password_rejected(timeout: float = 8.0, title_before: str = "") -> bool:
    """Detect if the password was rejected using red pixel + page text detection.

    Microsoft shows 'Your account or password is incorrect' in bright red.
    However, some verification pages ('We need to verify more') also have red
    elements — so when red pixels are detected we re-read the page text to
    confirm it's an actual password error, not a verification/MFA step.
    """
    # Wait for the page to react (title change or timeout)
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(0.5)
        sign_in_win = _find_signin_window()
        if not sign_in_win:
            return False  # Window gone — success
        if title_before and sign_in_win.window_text() != title_before:
            break  # Page reacted

    time.sleep(2.0)  # Let page fully render

    sign_in_win = _find_signin_window()
    if not sign_in_win:
        return False

    if not _window_has_red_error(sign_in_win.handle):
        return False  # No red pixels — password accepted, moving on

    # Red pixels detected — could be wrong password OR a verification page.
    # Re-read page text to distinguish.
    page = _detect_signin_page(sign_in_win.handle)
    if page == "mfa":
        _click_authenticator_button()
        return False

    text = _get_clipboard_text().lower()
    verification_keywords = [
        "verify", "verificar", "approve", "aprobar", "authenticator",
        "more information", "más información", "we need", "necesitamos",
        "additional", "conditional", "access", "sign-in options",
    ]
    if any(kw in text for kw in verification_keywords):
        # It's a verification/MFA page with red UI elements — not a wrong password
        _click_authenticator_button()
        return False

    return True  # Red pixels + error keywords = actual wrong password


def _window_has_red_error(hwnd) -> bool:
    """Capture the sign-in window and check for red error text pixels.
    Microsoft's error color is approx R>180, G<90, B<90.
    Returns True if enough red pixels found (error visible)."""
    import ctypes, ctypes.wintypes
    try:
        from PIL import ImageGrab
        rect = ctypes.wintypes.RECT()
        ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
        left, top, right, bottom = rect.left, rect.top, rect.right, rect.bottom
        w = right - left
        h = bottom - top

        # Capture the middle region of the window where error text appears
        region = (
            left + w // 6,
            top + h // 4,
            right - w // 6,
            top + (h * 2) // 3,
        )
        img = ImageGrab.grab(bbox=region)

        red_count = sum(
            1 for r, g, b in img.getdata()
            if r > 180 and g < 90 and b < 90
        )
        return red_count > 50

    except Exception:
        return False


def _get_clipboard_text() -> str:
    """Read text from the Windows clipboard via PowerShell Get-Clipboard."""
    try:
        _, out, _ = _run(
            ["powershell.exe", "-NoProfile", "-Command", "Get-Clipboard"],
            timeout=5,
        )
        return out.strip()
    except Exception:
        return ""


def _detect_signin_page(hwnd) -> str:
    """Detect which sign-in page is shown by reading text via Ctrl+A + Ctrl+C.

    Works across different PCs, branding, window sizes, and languages.
    After reading, clicks the input field area to restore focus for typing.

    Returns:
        'password' — password entry page
        'email'    — email/username entry page
        'mfa'      — MFA / verify identity page
        'unknown'  — could not determine
    """
    from pywinauto import keyboard
    import ctypes
    user32 = ctypes.windll.user32

    try:
        _force_foreground(hwnd)
        time.sleep(0.3)
        if user32.GetForegroundWindow() != hwnd:
            return "unknown"

        # Click on the banner/logo area at the very top of the page content
        # (~50 px below the window top, above any interactive elements on both pages).
        # This gives Chromium real keyboard focus and blurs any focused input,
        # so Ctrl+A selects ALL page text — not just the focused input field.
        import ctypes.wintypes
        rect = ctypes.wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        cx = (rect.left + rect.right) // 2
        cy = rect.top + 50   # just below title bar → banner/logo area, always safe
        user32.SetCursorPos(cx, cy)
        time.sleep(0.05)
        user32.mouse_event(0x0002, 0, 0, 0, 0)  # MOUSEEVENTF_LEFTDOWN
        time.sleep(0.02)
        user32.mouse_event(0x0004, 0, 0, 0, 0)  # MOUSEEVENTF_LEFTUP
        time.sleep(0.2)

        # Select all page text and copy to clipboard
        keyboard.send_keys("^a", pause=0.05)
        time.sleep(0.15)
        keyboard.send_keys("^c", pause=0.05)
        time.sleep(0.2)

        text = _get_clipboard_text().lower()

        # Debug log
        try:
            import os, tempfile
            log_path = os.path.join(tempfile.gettempdir(), "vpnswitcher_page_detect.txt")
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"page_text: {repr(text[:300])}\n")
        except Exception:
            pass

        if not text:
            result = "unknown"
        elif any(kw in text for kw in [
            "enter password", "contraseña", "forgot my password",
            "olvidé mi contraseña", "forgot password",
        ]):
            result = "password"
        elif any(kw in text for kw in [
            "verify your identity", "verificar tu identidad",
            "approve sign in", "aprobar solicitud", "authenticator",
        ]):
            result = "mfa"
        else:
            result = "email"

        # Email page:    1 Tab from body → email input
        # Password page: 2 Tabs from body → skip back-arrow button → password input
        tab_count = 2 if result == "password" else 1
        for _ in range(tab_count):
            keyboard.send_keys("{TAB}", pause=0.1)
            time.sleep(0.15)

        return result

    except Exception:
        return "unknown"


def _click_authenticator_button(timeout: float = 8.0):
    """After password accepted, 'Verify your identity' page may appear with
    multiple MFA options. Click 'Approve a request on my Microsoft Authenticator app'.
    If already on 'Approve sign in request' page (push already sent), does nothing."""
    try:
        deadline = time.time() + timeout
        while time.time() < deadline:
            sign_in_win = _find_signin_window()
            if not sign_in_win:
                return True  # Window closed — done

            page_text = _get_signin_page_text()

            # Already on the waiting-for-push page — nothing to click
            waiting_keywords = ["approve sign in request", "aprobar solicitud de inicio",
                                "open your authenticator app", "abre tu aplicación",
                                "enter the number", "ingresa el número"]
            if any(kw in page_text for kw in waiting_keywords):
                return True

            # On "Verify your identity / choose method" page — click Authenticator option
            choose_keywords = ["approve a request", "aprobar una solicitud",
                               "authenticator app", "aplicación authenticator",
                               "verify your identity", "verificar tu identidad"]
            if any(kw in page_text for kw in choose_keywords):
                from pywinauto import Desktop
                desktop = Desktop(backend="uia")
                for w in desktop.windows():
                    wtitle = w.window_text().lower()
                    if "sign in to your account" in wtitle or "iniciar sesión" in wtitle:
                        for ctrl in w.descendants():
                            try:
                                ctrl_text = ctrl.window_text().lower()
                                if ("approve a request" in ctrl_text
                                        or "aprobar una solicitud" in ctrl_text
                                        or ("authenticator" in ctrl_text and len(ctrl_text) < 80)):
                                    ctrl.click_input()
                                    return True
                            except Exception:
                                pass
                        break

            time.sleep(0.5)
    except Exception:
        pass
    return False


def _forti_autofill_signin(username: str, password: str) -> str:
    """Wait for the FortiClient SAML sign-in popup and auto-fill credentials.

    Adaptively detects which page is shown (email or password) before typing,
    so it works on PCs that skip the email page or show password directly.

    Returns:
        'ok'             — credentials submitted (or no popup appeared)
        'wrong_password' — password was rejected by the sign-in page
        'no_popup'       — no sign-in popup appeared
    """
    try:
        # Step 1: Wait for the sign-in window to appear (up to 20s)
        deadline = time.time() + 20
        sign_in_win = None
        while time.time() < deadline:
            if _autofill_cancel.is_set():
                return "ok"
            if not _forti_client_running():
                return "closed"
            sign_in_win = _find_signin_window()
            if sign_in_win:
                break
            time.sleep(0.5)

        if not sign_in_win:
            return "no_popup"

        if _forti_wait(1.5) == "closed":
            return "closed"

        # Step 2: Detect page — leaves input focused via Tab at the end
        page = _detect_signin_page(sign_in_win.handle)

        if page == "mfa":
            _click_authenticator_button()
            return "ok"

        title_before = ""
        password_typed = False

        # Helper: type into the already-focused input.
        # Uses SetForegroundWindow (not SetFocus) so Chromium keeps its internal
        # focus on the input that Tab already selected. No Ctrl+A — inputs are
        # empty on first load so we just type directly.
        def _type_into_focused(text, press_enter=True) -> bool:
            import ctypes
            from pywinauto import keyboard as kb
            if _autofill_cancel.is_set():
                return False
            # Light re-activation: make the window active without triggering
            # WM_SETFOCUS which would reset Chromium's internal focus to body.
            ctypes.windll.user32.SetForegroundWindow(sign_in_win.handle)
            time.sleep(0.15)
            kb.send_keys(text, with_spaces=True, pause=0.03)
            time.sleep(0.1)
            if press_enter:
                kb.send_keys("{ENTER}", pause=0.05)
            return True

        # Step 3a: Email page — type email, then wait for transition
        if page == "email" and username:
            if _autofill_cancel.is_set():
                return "ok"
            _type_into_focused(username, press_enter=True)

            # Wait for page to transition (up to 9 s, one detection check every 3 s).
            # Don't loop _detect_signin_page rapidly — each call clicks the page.
            for _ in range(3):
                if _autofill_cancel.is_set():
                    return "ok"
                if _forti_wait(3.0) == "closed":
                    return "closed"
                sign_in_win = _find_signin_window()
                if not sign_in_win:
                    return "ok"
                page = _detect_signin_page(sign_in_win.handle)
                if page in ("password", "mfa"):
                    break

        if page == "mfa":
            _click_authenticator_button()
            return "ok"

        # Step 3b: Type password — only when detection confirmed password page.
        # Do NOT use "email" as a fallback: if the page didn't transition it means
        # the email wasn't accepted, and typing the password here would put it in
        # the email field.
        if password and page == "password":
            if _autofill_cancel.is_set():
                return "ok"
            sign_in_win = _find_signin_window()
            if sign_in_win:
                title_before = sign_in_win.window_text()
                if _type_into_focused(password, press_enter=True):
                    password_typed = True

        # Step 4: Check if password was rejected, otherwise click Authenticator
        if password_typed:
            if _check_password_rejected(timeout=10, title_before=title_before):
                return "wrong_password"
            if not _forti_client_running():
                return "closed"
            _click_authenticator_button()

        if not _forti_client_running():
            return "closed"
        return "ok"

    except Exception:
        return "ok"


def _forti_autofill_custom_flow(username: str, password: str, steps: list) -> str:
    """Execute a user-defined sign-in sequence without page detection.

    steps: ordered subset of ["username", "password", "mfa"]
    Each step is executed in order; unchecked steps are skipped.

    Returns 'ok', 'wrong_password', or 'no_popup'.
    """
    try:
        # Wait for sign-in window
        deadline = time.time() + 20
        sign_in_win = None
        while time.time() < deadline:
            if _autofill_cancel.is_set():
                return "ok"
            if not _forti_client_running():
                return "closed"
            sign_in_win = _find_signin_window()
            if sign_in_win:
                break
            time.sleep(0.5)

        if not sign_in_win:
            return "no_popup"

        if _forti_wait(1.5) == "closed":
            return "closed"

        title_before = ""
        password_typed = False
        # Track if we already typed username so we know the password page
        # will have a back arrow (needs 2 Tabs instead of 1).
        typed_username = False

        def _focus_and_type_step(tab_count: int, text: str, press_enter: bool = True) -> bool:
            """Click banner to give Chromium focus, Tab to input, then type."""
            import ctypes, ctypes.wintypes
            from pywinauto import keyboard as kb
            user32 = ctypes.windll.user32

            nonlocal sign_in_win
            sign_in_win = _find_signin_window()
            if not sign_in_win or _autofill_cancel.is_set():
                return False

            _force_foreground(sign_in_win.handle)
            time.sleep(0.2)
            if user32.GetForegroundWindow() != sign_in_win.handle:
                return False

            # Click banner area (top+50) to give Chromium keyboard focus
            rect = ctypes.wintypes.RECT()
            user32.GetWindowRect(sign_in_win.handle, ctypes.byref(rect))
            cx = (rect.left + rect.right) // 2
            cy = rect.top + 50
            user32.SetCursorPos(cx, cy)
            time.sleep(0.05)
            user32.mouse_event(0x0002, 0, 0, 0, 0)
            time.sleep(0.02)
            user32.mouse_event(0x0004, 0, 0, 0, 0)
            time.sleep(0.2)

            for _ in range(tab_count):
                kb.send_keys("{TAB}", pause=0.1)
                time.sleep(0.15)

            # Light re-activation before typing
            user32.SetForegroundWindow(sign_in_win.handle)
            time.sleep(0.15)
            kb.send_keys(text, with_spaces=True, pause=0.03)
            time.sleep(0.1)
            if press_enter:
                kb.send_keys("{ENTER}", pause=0.05)
            return True

        for step in steps:
            if _autofill_cancel.is_set():
                return "ok"

            if step == "username" and username:
                # Email page: 1 Tab from banner → email input
                _focus_and_type_step(tab_count=1, text=username)
                typed_username = True
                if _forti_wait(3.0) == "closed":
                    return "closed"

            elif step == "password" and password:
                # Password page after email: back arrow present → 2 Tabs
                # Password page as first step (no email): no back arrow → 1 Tab
                tab_count = 2 if typed_username else 1
                sign_in_win = _find_signin_window()
                if sign_in_win:
                    title_before = sign_in_win.window_text()
                if _focus_and_type_step(tab_count=tab_count, text=password):
                    password_typed = True

            elif step == "mfa":
                # If we just typed a password, verify it wasn't rejected before
                # touching the MFA button — the MFA approve button only exists
                # once the password is accepted. Without this check the loop
                # would `return "ok"` and the caller would never learn the
                # password was wrong.
                if password_typed:
                    if _check_password_rejected(timeout=10, title_before=title_before):
                        return "wrong_password"
                    if not _forti_client_running():
                        return "closed"
                    password_typed = False  # already verified
                _click_authenticator_button()
                if not _forti_client_running():
                    return "closed"
                break

        if password_typed:
            if _check_password_rejected(timeout=10, title_before=title_before):
                return "wrong_password"
            if not _forti_client_running():
                return "closed"
            _click_authenticator_button()

        if not _forti_client_running():
            return "closed"
        return "ok"

    except Exception:
        return "ok"


def _forti_normalize_button_text(text: str) -> str:
    return " ".join((text or "").strip().lower().split())


def _forti_button_matches(ctrl_text: str, keywords) -> bool:
    """Match a Forti button label without treating Disconnect as Connect."""
    import re

    text = _forti_normalize_button_text(ctrl_text)
    if isinstance(keywords, str):
        keywords = [keywords]
    for keyword in keywords:
        kw = _forti_normalize_button_text(keyword)
        if not kw:
            continue
        if text == kw:
            return True
        if re.search(rf"(?<![a-z0-9]){re.escape(kw)}(?![a-z0-9])", text):
            return True
    return False


def _forti_click_button(win, button_texts, timeout: float = 5.0) -> bool:
    """Find and click a button in the FortiClient Electron window via UIA.
    button_texts can be a string or list of strings (e.g. English + Spanish).
    Matches whole button words/phrases so Connect never matches Disconnect."""
    if isinstance(button_texts, str):
        button_texts = [button_texts]
    log = get_logger()
    try:
        deadline = time.time() + timeout
        while time.time() < deadline:
            for ctrl in win.descendants():
                try:
                    if ctrl.element_info.control_type == "Button":
                        if _forti_button_matches(ctrl.window_text(), button_texts):
                            log.info(f"FortiClient: clicking button '{ctrl.window_text()}'")
                            ctrl.click()
                            return True
                except Exception:
                    pass
            time.sleep(0.5)
    except Exception as e:
        log.error(f"FortiClient: _forti_click_button error: {e}")
    log.warning(f"FortiClient: button not found (candidates: {button_texts})")
    return False


def _cisco_click_connect():
    """Find the Cisco Secure Client window and click its Connect button.
    Retries for up to 8 seconds to give the window time to fully render."""
    try:
        from pywinauto import Desktop
        deadline = time.time() + 8
        while time.time() < deadline:
            try:
                desktop = Desktop(backend="win32")
                wins = [w for w in desktop.windows() if w.window_text() == "Cisco Secure Client"]
                for win in wins:
                    for ctrl in win.descendants():
                        try:
                            if (ctrl.window_text().lower() in ("connect", "conectar")
                                    and ctrl.friendly_class_name() == "Button"
                                    and ctrl.is_enabled()):
                                ctrl.click()
                                return True
                        except Exception:
                            pass
            except Exception:
                pass
            time.sleep(0.5)
    except Exception:
        pass
    return False


def _get_adapter_status(log_matches: bool = False) -> dict:
    """Use PowerShell Get-NetAdapter to check adapter states.
    Returns dict like {'cisco': 'Up', 'forti': 'Disabled', ...}
    """
    result = {}
    log = get_logger()
    try:
        rc, out, err = _run(
            ["powershell.exe", "-NoProfile", "-Command",
             "Get-NetAdapter | Select-Object Name, InterfaceDescription, Status | "
             "Format-List"],
            timeout=10,
        )
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
    try:
        return (_get_adapter_status().get("globalprotect") or "").strip()
    except Exception:
        return ""


# ── GlobalProtect (Palo Alto / BICE) ─────────────────────────────────────────

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
                    return wins[0]
            except Exception as e:
                log.debug(f"gp_get_window: iteration error: {e}")
            time.sleep(0.4)
    except Exception as e:
        log.warning(f"gp_get_window: failed: {e}")
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
                if buf.value == GP_LOGIN_TITLE:
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


def _gp_invoke_connect_button(win) -> bool:
    """Invoke the Connect/Disconnect pane (auto_id=1160). The element reports
    as Pane (MFC quirk) but accepts InvokePattern. Falls back to .click()."""
    log = get_logger()
    try:
        ctrl = _gp_find_descendant_by_autoid(win, GP_BTN_CONNECT_AUTOID)
        if not ctrl:
            log.warning(f"gp_invoke: button auto_id={GP_BTN_CONNECT_AUTOID} not found")
            return False
        label = (ctrl.window_text() or "").strip()
        log.info(f"gp_invoke: clicking button (label='{label}')")
        try:
            ctrl.invoke()
            log.info("gp_invoke: invoke() succeeded")
            return True
        except Exception as e1:
            log.info(f"gp_invoke: invoke() failed ({e1}), trying click_input()")
            try:
                ctrl.click_input()
                log.info("gp_invoke: click_input() succeeded")
                return True
            except Exception as e2:
                log.warning(f"gp_invoke: click_input() failed: {e2}")
    except Exception as e:
        log.error(f"gp_invoke: failed: {e}")
    return False


def _gp_get_login_window(timeout: float = 1.0):
    """Find the SAML 'GlobalProtect Login' window (class #32770) via UIA.

    PanGPA renders the SAML auth flow inside an embedded WebView2 hosted in
    this dialog. When BICE's federation returns Microsoft, the WebView2
    sometimes shows a 'Pick an account' picker — we need the UIA wrapper to
    walk its descendants and click the configured account.
    """
    log = get_logger()
    try:
        from pywinauto import Desktop
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                desktop = Desktop(backend="uia")
                for w in desktop.windows():
                    try:
                        if (w.window_text() == GP_LOGIN_TITLE
                                and w.element_info.class_name == GP_CLASS):
                            return w
                    except Exception:
                        pass
            except Exception as e:
                log.debug(f"gp_get_login_window: iteration error: {e}")
            time.sleep(0.4)
    except Exception as e:
        log.warning(f"gp_get_login_window: failed: {e}")
    return None


def _gp_find_login_hwnd():
    """Find the Win32 hwnd for the GlobalProtect SAML dialog."""
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
                    if cls_buf.value == GP_CLASS:
                        found.append((hwnd, bool(user32.IsWindowVisible(hwnd))))
            return True

        user32.EnumWindows(WNDENUMPROC(_enum), 0)
        if not found:
            return None
        visible = [hwnd for hwnd, is_visible in found if is_visible]
        return visible[0] if visible else found[0][0]
    except Exception:
        return None


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
    """Invoke() then click_input() on a control. Logs which path worked."""
    log = get_logger()
    try:
        ctrl.invoke()
        log.info(f"gp_picker: invoke() on {where} succeeded")
        return True
    except Exception:
        pass
    try:
        ctrl.click_input()
        log.info(f"gp_picker: click_input() on {where} succeeded")
        return True
    except Exception as e:
        log.debug(f"gp_picker: click_input() on {where} failed: {e}")
    return False


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


def _gp_handle_saml_signin(username: str, total_timeout: float = 45.0) -> str:
    """Drive Microsoft's SAML sign-in inside the GlobalProtect Login window.

    Two pages can show up depending on whether the user has cached accounts:

      • 'Pick an account' — picker rows of cached identities. We click the
        row whose text matches `username` (the row is usually a clickable
        container; the matching text node may be a deeper static, so we
        walk up to parent / grandparent if the direct click fails).

      • 'Sign in' — empty email input prompting for the user's address. We
        focus the first Edit descendant, clear it with Ctrl+A/Delete, type
        `username` and press Enter to submit.

    Returns 'picker', 'email', or 'timeout'. The previous handler timed out
    in 15 s, which sometimes fired before PanGPA's WebView2 had even
    rendered the SAML window — the new default extends that window so the
    page has time to appear before we give up.
    """
    log = get_logger()
    if not username:
        log.info("gp_saml: no configured gp_username — skipping SAML handler")
        return "timeout"

    target = username.strip().lower()
    log.info(f"gp_saml: watching SAML window for picker row or empty email "
             f"input (target='{target}', timeout={total_timeout}s)")
    deadline = time.time() + total_timeout

    while time.time() < deadline:
        try:
            login_win = _gp_get_login_window(timeout=0.6)
            if not login_win:
                time.sleep(0.6)
                continue
            _gp_prepare_login_window(login_win)

            try:
                descendants = list(login_win.descendants())
            except Exception as e:
                log.warning(f"gp_saml: descendants() failed: {e}")
                time.sleep(0.7)
                continue

            # 1) Picker-row path: a node whose text contains the configured
            # email. The clickable row may be the node itself or an ancestor.
            use_another_ctrl = None
            for ctrl in descendants:
                try:
                    text = (ctrl.window_text() or "").strip().lower()
                except Exception:
                    continue
                if not text:
                    continue
                if ("use another account" in text
                        or "usar otra cuenta" in text
                        or "usar una cuenta diferente" in text):
                    use_another_ctrl = ctrl
                if target not in text:
                    continue
                log.info(f"gp_saml: picker row matched text='{text[:80]}'")
                if _gp_try_click_with_ancestors(ctrl, "picker_match"):
                    return "picker"

            # If Microsoft shows cached accounts but not the configured BICE
            # account (or UIA splits the row text oddly), choose the explicit
            # email-entry route instead of making the user click it manually.
            if use_another_ctrl:
                log.info("gp_saml: target account row not found; clicking Use another account")
                if _gp_try_click_with_ancestors(use_another_ctrl, "use_another_account"):
                    time.sleep(1.0)
                    continue

            # 2) Email-input path: an Edit control on the 'Sign in' page.
            # Type the configured username into it and submit with Enter.
            for ctrl in descendants:
                try:
                    ct = getattr(ctrl.element_info, "control_type", "")
                except Exception:
                    ct = ""
                if ct != "Edit":
                    continue
                try:
                    ctrl.set_focus()
                    time.sleep(0.25)
                    from pywinauto.keyboard import send_keys
                    send_keys("^a")
                    time.sleep(0.05)
                    send_keys("{DELETE}")
                    time.sleep(0.05)
                    send_keys(username, with_spaces=True)
                    time.sleep(0.2)
                    send_keys("{ENTER}")
                    log.info("gp_saml: typed username into Edit + Enter")
                    return "email"
                except Exception as e:
                    log.warning(f"gp_saml: typing into edit failed: {e}")
                    continue
        except Exception as e:
            log.warning(f"gp_saml: iteration error: {e}")
        time.sleep(0.8)

    log.info("gp_saml: no picker row or email input found within timeout")
    return "timeout"

    log.info("gp_picker: no matching account row appeared within timeout "
             "(picker may not be showing — that is normal)")
    return False


class VPNController:
    def __init__(self, config: dict):
        self.config = config

    # ── status detection ──────────────────────────────────────────────────────

    def get_status(self) -> str:
        if self._cisco_connected():
            return CISCO
        adapters = _get_adapter_status()
        if self._forti_connected(adapters):
            return FORTI
        if self._gp_connected(adapters):
            return GPROT
        return NONE

    def _cisco_connected(self) -> bool:
        """Use vpncli state — the authoritative and fast source."""
        cli = _find_exe(CISCO_CLI_CANDIDATES, self.config.get("cisco_cli_path", ""))
        if cli:
            rc, out, err = _run([cli, "state"], timeout=8)
            combined = out + err
            for line in combined.splitlines():
                low = line.strip().lower()
                if "state: connected" in low and "disconnected" not in low:
                    return True
        return False

    def _gp_connected(self, adapters: Optional[dict] = None) -> bool:
        """Detect GlobalProtect connection via the status static in the
        (visible or hidden) PanGPA window. The adapter is also checked as a
        secondary signal."""
        try:
            adapters = adapters if adapters is not None else _get_adapter_status()
            adapter_status = (adapters.get("globalprotect") or "").lower()
            if adapter_status == "up":
                return True
        except Exception:
            pass
        try:
            win = _gp_get_window(timeout=0.4)
            if win:
                # PanGPA status text is exactly one of:
                #   'Disconnected', 'Not Connected', 'Connecting...', 'Connected',
                #   'Disconnecting...', 'Connection Failed'.
                # Substring matching is unsafe — 'Not Connected' contains
                # 'connected' as a substring. Match the connected state exactly.
                text = _gp_get_status_text(win).strip().lower()
                if text == "connected":
                    return True
        except Exception:
            pass
        return False

    def _forti_connected(self, adapters: Optional[dict] = None) -> bool:
        """Check if the Fortinet SSL VPN adapter is Up via PowerShell."""
        adapters = adapters if adapters is not None else _get_adapter_status()
        # The SSL VPN adapter (Ethernet 3) goes to "Up" when tunnel is active
        ssl_status = adapters.get("forti_ssl", "")
        return ssl_status.lower() == "up"

    # ── Cisco connect / disconnect ────────────────────────────────────────────

    def connect_cisco(self) -> Tuple[bool, str]:
        log = get_logger()
        log.info("connect_cisco: attempt started")
        host = self.config.get("cisco_host", "").strip()
        username = self.config.get("cisco_username", "").strip()
        password = self.config.get("cisco_password", "").strip()

        # If we have full credentials, try the CLI first
        if host and username and password:
            cli = _find_exe(CISCO_CLI_CANDIDATES, self.config.get("cisco_cli_path", ""))
            if cli:
                log.info(f"connect_cisco: trying CLI ({cli}) → {host}")
                stdin_data = f"{username}\n{password}\ny\n"
                rc, out, err = _run([cli, "-s", "connect", host],
                                    input_text=stdin_data, timeout=40)
                combined = (out + err).lower()
                if "state: connected" in combined and "disconnected" not in combined:
                    log.info("connect_cisco: CLI connected")
                    return True, "Connected to Cisco Secure Client."
                log.warning(f"connect_cisco: CLI did not connect (rc={rc}), falling back to GUI")

        # Open the Cisco Secure Client GUI, bring it to front, and click Connect
        ui = _find_exe(CISCO_UI_CANDIDATES)
        if ui:
            log.info("connect_cisco: opening GUI and clicking Connect")
            if not _bring_window_to_front("Cisco Secure Client"):
                _open_gui(ui)
            _cisco_click_connect()
            return True, "Connecting via Cisco Secure Client…"

        log.error("connect_cisco: Cisco Secure Client not found")
        return False, "Cisco Secure Client not found on this PC."

    def disconnect_cisco(self) -> Tuple[bool, str]:
        cli = _find_exe(CISCO_CLI_CANDIDATES, self.config.get("cisco_cli_path", ""))
        if not cli:
            return False, "Cisco VPN CLI not found."
        _run([cli, "disconnect"], timeout=20)
        time.sleep(2)
        if not self._cisco_connected():
            return True, "Cisco Secure Client disconnected."
        return False, "Cisco disconnect may have failed — check the Cisco app."

    # ── FortiClient connect / disconnect ──────────────────────────────────────

    def connect_forti(self) -> Tuple[bool, str]:
        log = get_logger()
        log.info("connect_forti: attempt started")

        # 1. Custom command
        cmd = self.config.get("forti_connect_cmd", "").strip()
        if cmd:
            try:
                subprocess.Popen(cmd, shell=True)
                log.info("connect_forti: custom command sent")
                return True, "FortiClient connect command sent."
            except Exception as e:
                log.error(f"connect_forti: custom command failed: {e}")
                return False, f"FortiClient custom command failed: {e}"

        if self._forti_connected():
            log.info("connect_forti: Forti SSL adapter already Up; skipping Connect click")
            return True, "FortiClient already connected."

        # 2. Try to find and focus the FortiClient window (launch if needed)
        just_launched = False
        win = _forti_get_window()
        log.info(f"connect_forti: initial _forti_get_window returned win={bool(win)}")
        if not win:
            # FortiClient may be running as a tray process with its window hidden.
            # Restore the hidden window instead of launching a second instance
            # (a second instance causes a JS crash on some versions).
            restored = _forti_restore_tray_window()
            log.info(f"connect_forti: tray restore attempt, found={restored}")
            if restored:
                win = _forti_get_window(timeout=5)
                log.info(f"connect_forti: after restore _forti_get_window returned win={bool(win)}")

        if not win:
            log.info("connect_forti: no existing window found; launching FortiClient")
            forti_exe = _find_exe(FORTI_EXE_CANDIDATES, self.config.get("forti_exe_path", ""))
            if not forti_exe:
                log.error("connect_forti: FortiClient exe not found")
                return False, "FortiClient not found. Set the path in Settings."

            # Kill zombie FortiClient.exe processes from previous failed launches.
            # Each crashed launch leaves orphaned processes that accumulate and
            # block fresh window creation. Service processes (FortiTray, FortiVPN,
            # FortiSSLVPNdaemon, FortiSettings) are preserved.
            try:
                import psutil
                killed = 0
                for p in psutil.process_iter(["pid", "name"]):
                    try:
                        if (p.info.get("name") or "").lower() == "forticlient.exe":
                            p.kill()
                            killed += 1
                    except Exception:
                        pass
                log.info(f"connect_forti: killed {killed} zombie FortiClient.exe processes")
                if killed:
                    time.sleep(0.5)
            except Exception as e:
                log.warning(f"connect_forti: zombie cleanup failed: {e}")

            log.info(f"connect_forti: launching {forti_exe}")
            _open_gui(forti_exe)
            win = _forti_get_window(timeout=12)
            just_launched = bool(win)
            if not win:
                dismissed = _forti_dismiss_error_dialog()
                log.warning(f"connect_forti: window not found after launch, dismissed_dialog={dismissed}")
                log.info("connect_forti: diagnostics after failed launch:")
                _forti_diagnostics()
                _forti_monitor_processes(seconds=10)

        if win:
            log.info("connect_forti: FortiClient window found")
            try:
                win.set_focus()
            except Exception:
                pass

            # Electron renders the window ~1s after launch, but the React event
            # handlers aren't wired yet — a Connect click in that gap is silently
            # dropped and the SAML popup never appears (logged as no_popup).
            # Wait briefly so the button's click handler is live before clicking.
            if just_launched:
                log.info("connect_forti: fresh launch — waiting 2.5s for Electron hydration")
                time.sleep(2.5)

            connect_buttons = ["Connect", "Conectar", "SAML Login"]
            disconnect_buttons = ["Disconnect", "Desconectar"]
            clicked_connect = _forti_click_button(win, connect_buttons)

            if not clicked_connect:
                log.info("connect_forti: Connect/SAML Login not found; checking for stale Disconnect state")
                if _forti_click_button(win, disconnect_buttons, timeout=1.5):
                    log.warning(
                        "connect_forti: Forti UI showed Disconnect while adapter is down; "
                        "resetting FortiClient before connecting"
                    )
                    time.sleep(4)
                    win = _forti_get_window(timeout=6)
                    if not win:
                        forti_exe = _find_exe(
                            FORTI_EXE_CANDIDATES,
                            self.config.get("forti_exe_path", ""),
                        )
                        if forti_exe:
                            log.info("connect_forti: relaunching FortiClient after stale Disconnect")
                            _open_gui(forti_exe)
                            win = _forti_get_window(timeout=12)
                            just_launched = bool(win)

                    if win:
                        try:
                            win.set_focus()
                        except Exception:
                            pass
                        if just_launched:
                            log.info(
                                "connect_forti: fresh launch after reset - "
                                "waiting 2.5s for Electron hydration"
                            )
                            time.sleep(2.5)
                        clicked_connect = _forti_click_button(win, connect_buttons, timeout=8)

            if not clicked_connect:
                log.warning("connect_forti: no Connect/SAML Login button was clicked")
                return False, "Could not find Connect in FortiClient. Open FortiClient and try again."

            # Auto-fill sign-in credentials if saved
            username = self.config.get("forti_username", "").strip()
            password = decrypt_password(self.config.get("forti_password_enc", ""))
            if username or password:
                flow_mode  = self.config.get("forti_flow_mode", "detect")
                flow_steps = self.config.get("forti_flow_steps",
                                             ["username", "password", "mfa"])

                def _run_autofill():
                    log.info(f"connect_forti: autofill starting (mode={flow_mode})")
                    if flow_mode == "custom":
                        return _forti_autofill_custom_flow(username, password, flow_steps)
                    return _forti_autofill_signin(username, password)

                result = _run_autofill()
                log.info(f"connect_forti: autofill result={result}")

                # Click was silently dropped → SAML popup never appeared.
                # Re-click Connect once and retry autofill.
                if result == "no_popup":
                    log.info("connect_forti: no_popup — retrying Connect click")
                    try:
                        win.set_focus()
                    except Exception:
                        pass
                    time.sleep(1.5)
                    if _forti_click_button(win, connect_buttons):
                        result = _run_autofill()
                        log.info(f"connect_forti: autofill retry result={result}")
                    else:
                        log.warning("connect_forti: retry Connect click failed")

                if result == "wrong_password":
                    return False, "__WRONG_PASSWORD__"
                if result == "closed":
                    return False, "FortiClient was closed before the connection completed."
                if result == "no_popup":
                    return False, "FortiClient did not open the sign-in window. Try again."
                return True, "Credentials submitted — approve MFA if prompted."

            return True, "Connecting via FortiClient…"

        log.error("connect_forti: window did not appear after launch")
        return False, "FortiClient launched but window did not appear. Try again."

    def disconnect_forti(self) -> Tuple[bool, str]:
        # 1. Custom command
        cmd = self.config.get("forti_disconnect_cmd", "").strip()
        if cmd:
            try:
                subprocess.run(cmd, shell=True, timeout=15, creationflags=NO_WINDOW)
                time.sleep(2)
                if not self._forti_connected():
                    return True, "FortiClient disconnected."
            except Exception as e:
                return False, f"Custom disconnect failed: {e}"

        # 2. Find the FortiClient window and click Disconnect
        win = _forti_get_window()
        if not win:
            forti_exe = _find_exe(FORTI_EXE_CANDIDATES, self.config.get("forti_exe_path", ""))
            if forti_exe:
                _open_gui(forti_exe)
                win = _forti_get_window(timeout=10)

        if win:
            try:
                win.set_focus()
            except Exception:
                pass
            if _forti_click_button(win, ["Disconnect", "Desconectar"]):
                time.sleep(3)
                if not self._forti_connected():
                    return True, "FortiClient disconnected."
                return True, "Disconnect clicked — waiting for FortiClient to finish."
            return False, "Could not find Disconnect button in FortiClient."

        return False, "FortiClient window not found."

    def disconnect_all(self) -> Tuple[bool, str]:
        status = self.get_status()
        if status == CISCO:
            return self.disconnect_cisco()
        if status == FORTI:
            return self.disconnect_forti()
        if status == GPROT:
            return self.disconnect_globalprotect()
        return True, "No VPN was active."

    # ── GlobalProtect connect / disconnect ────────────────────────────────────

    def connect_globalprotect(self) -> Tuple[bool, str]:
        """Launch (if needed), bring the GlobalProtect window forward, click
        Connect, and wait passively while SAML completes. Verbose-logged so
        remote debugging from the log file is possible."""
        log = get_logger()
        log.info("connect_globalprotect: ===== attempt started =====")
        portal = self.config.get("gp_portal_url", "ext.bice.cl").strip() or "ext.bice.cl"
        log.info(f"connect_globalprotect: configured portal='{portal}'")
        _gp_diagnostics()

        # 1. Try to find an existing window first
        win = _gp_get_window(timeout=2)
        log.info(f"connect_globalprotect: initial window found={bool(win)}")

        # 2. Launch PanGPA.exe if no window yet
        if not win:
            gp_exe = _find_exe(GP_EXE_CANDIDATES, self.config.get("gp_exe_path", ""))
            log.info(f"connect_globalprotect: gp_exe={gp_exe}")
            if not gp_exe:
                log.error("connect_globalprotect: PanGPA.exe not found")
                return False, "GlobalProtect not installed (PanGPA.exe not found)."
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

        # 4. Read state
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
        if status_before.strip().lower() == "connected":
            log.info("connect_globalprotect: already connected, nothing to do")
            return True, "GlobalProtect already connected."

        # If button currently says Disconnect, GlobalProtect thinks it's connected
        # but our status check says otherwise — log and proceed to click anyway.
        if "disconnect" in button_label.lower():
            log.warning(f"connect_globalprotect: button label is '{button_label}' "
                        f"but status='{status_before}'. Will click anyway.")

        # 5. Click Connect
        if not _gp_invoke_connect_button(win):
            log.error("connect_globalprotect: failed to click Connect button")
            _gp_dump_descendants(win)
            _gp_diagnostics()
            return False, "Could not click Connect button in GlobalProtect window."

        # 5b. Drive Microsoft's SAML page (picker row click OR email input
        # fill, whichever appears). On a fresh PC there are no cached
        # accounts and Microsoft goes straight to 'Sign in' with an empty
        # email field — the previous picker-only handler would time out and
        # the user would be stranded staring at the blank input. The 45 s
        # timeout also covers slow WebView2 boots where the SAML window
        # takes 15-20 s to appear after the Connect click.
        gp_user = self.config.get("gp_username", "").strip()
        if gp_user:
            try:
                outcome = _gp_handle_saml_signin(gp_user, total_timeout=45.0)
                log.info(f"connect_globalprotect: saml signin outcome={outcome}")
            except Exception as e:
                log.warning(f"connect_globalprotect: saml signin handler failed: {e}")

        # 6. Poll status. SAML auth happens inside an embedded WebView2 and may
        # require user interaction (account selection, MFA). We wait passively.
        log.info("connect_globalprotect: Connect clicked, polling status (timeout=120s)")
        deadline = time.time() + 120
        last_status = status_before
        last_login_visible = False
        last_adapter_status = ""
        while time.time() < deadline:
            time.sleep(2)
            adapter_status = _gp_adapter_status()
            if adapter_status != last_adapter_status:
                log.info(
                    "connect_globalprotect: adapter status="
                    f"'{adapter_status or 'unknown'}'"
                )
                last_adapter_status = adapter_status
            if adapter_status.lower() == "up":
                log.info("connect_globalprotect: tunnel UP via adapter")
                return True, "GlobalProtect connected."
            cur_win = _gp_get_window(timeout=0.5) or win
            cur_status = _gp_get_status_text(cur_win)
            if cur_status and cur_status != last_status:
                log.info(f"connect_globalprotect: status '{last_status}' -> '{cur_status}'")
                last_status = cur_status
            login_visible = _gp_login_window_present()
            if login_visible != last_login_visible:
                log.info(f"connect_globalprotect: SAML login window visible={login_visible}")
                last_login_visible = login_visible
            low = cur_status.strip().lower()
            # Exact match — see the note on status strings above. 'Not
            # Connected' must not be treated as a successful tunnel.
            if low == "connected":
                log.info("connect_globalprotect: tunnel UP")
                return True, "GlobalProtect connected."
            if "fail" in low or "error" in low or "denied" in low:
                log.warning(f"connect_globalprotect: failure status '{cur_status}'")
                _gp_diagnostics()
                return False, f"GlobalProtect: {cur_status}"

        log.warning(f"connect_globalprotect: timeout — last status='{last_status}'")
        _gp_diagnostics()
        return True, f"GlobalProtect: complete the SAML login. Last status: {last_status or 'unknown'}"

    def disconnect_globalprotect(self) -> Tuple[bool, str]:
        log = get_logger()
        log.info("disconnect_globalprotect: ===== attempt started =====")
        _gp_diagnostics()

        win = _gp_get_window(timeout=2)
        if not win:
            gp_exe = _find_exe(GP_EXE_CANDIDATES, self.config.get("gp_exe_path", ""))
            log.info(f"disconnect_globalprotect: launching {gp_exe} to find window")
            if gp_exe:
                _open_gui(gp_exe)
                win = _gp_get_window(timeout=10)

        if not win:
            log.error("disconnect_globalprotect: window not found")
            return False, "GlobalProtect window not found."

        try:
            win.set_focus()
        except Exception:
            pass

        status_before = _gp_get_status_text(win)
        button_label = _gp_get_button_label(win)
        log.info(f"disconnect_globalprotect: pre-click status='{status_before}' button='{button_label}'")

        # If already disconnected, no need to click. PanGPA uses both
        # 'Disconnected' and 'Not Connected' depending on prior state.
        low_before = status_before.strip().lower()
        if low_before in ("disconnected", "not connected"):
            log.info("disconnect_globalprotect: already disconnected")
            return True, "GlobalProtect already disconnected."

        if not _gp_invoke_connect_button(win):
            log.error("disconnect_globalprotect: failed to click Disconnect button")
            return False, "Could not click Disconnect button."

        # Wait for state to flip
        deadline = time.time() + 15
        last_status = status_before
        while time.time() < deadline:
            time.sleep(1)
            cur_win = _gp_get_window(timeout=0.4) or win
            cur_status = _gp_get_status_text(cur_win)
            if cur_status and cur_status != last_status:
                log.info(f"disconnect_globalprotect: status '{last_status}' -> '{cur_status}'")
                last_status = cur_status
            cur_low = cur_status.strip().lower()
            if cur_low in ("disconnected", "not connected"):
                log.info("disconnect_globalprotect: confirmed disconnected")
                return True, "GlobalProtect disconnected."

        log.warning(f"disconnect_globalprotect: did not confirm disconnect, last='{last_status}'")
        return True, "Disconnect clicked — GlobalProtect may still be finishing."

    def retry_forti_credentials(self, failed_step: str = "password") -> Tuple[bool, str]:
        """Retry the password after a rejection (username is assumed correct).
        The sign-in window must still be open on the password page.
        Checks for rejection again so the UI can re-prompt if still wrong."""
        if _autofill_cancel.is_set():
            return False, "Cancelled."

        time.sleep(0.8)  # Let Settings window fully close

        sign_in_win = _find_signin_window()
        if not sign_in_win:
            return False, "La ventana de sign-in se cerró. Intenta conectar FortiClient de nuevo."

        password = decrypt_password(self.config.get("forti_password_enc", ""))
        title_before = sign_in_win.window_text()
        _focus_and_type(sign_in_win.handle, password, press_enter=True)

        # Check if the new password was also rejected
        if _check_password_rejected(timeout=10, title_before=title_before):
            return False, "__WRONG_PASSWORD__"

        # Password accepted — try clicking the Authenticator approve button
        _click_authenticator_button()
        return True, "Nueva contraseña enviada — aprueba MFA si se solicita."
