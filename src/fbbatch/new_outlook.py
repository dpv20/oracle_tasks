"""Create a saved draft in New Outlook for Windows through UI Automation."""
from __future__ import annotations

import io
import ctypes
import logging
import os
import re
import shutil
import struct
import subprocess
import time
from pathlib import Path

from fbbatch.outlook_diagnostics import (
    log_outlook_environment,
    log_outlook_processes,
    log_outlook_uia_windows,
)


log = logging.getLogger(__name__)
NEW_OUTLOOK_START_TIMEOUT_SECONDS = 45.0
NEW_OUTLOOK_INITIAL_WINDOW_TIMEOUT_SECONDS = 15.0
NEW_OUTLOOK_RECOVERY_WINDOW_TIMEOUT_SECONDS = 35.0
NEW_OUTLOOK_SAVE_TIMEOUT_SECONDS = 45.0
NEW_OUTLOOK_IMAGE_PASTE_SETTLE_SECONDS = 2.5
NEW_OUTLOOK_SAVE_SETTLE_SECONDS = 5.0
OUTLOOK_INLINE_IMAGE_MAX_WIDTH = 960
OUTLOOK_INLINE_IMAGE_MAX_HEIGHT = 720
NEW_OUTLOOK_APP_USER_MODEL_ID = (
    "Microsoft.OutlookForWindows_8wekyb3d8bbwe!Microsoft.OutlookforWindows"
)
NEW_MAIL_CONTROL_TYPES = {"Button", "SplitButton", "MenuItem"}
NEW_MAIL_DIRECT_LABELS = {
    "new mail",
    "new message",
    "nuevo correo",
    "nuevo mensaje",
}
NEW_MAIL_COMPACT_LABELS = {"new", "nuevo"}


class NewOutlookUnavailable(RuntimeError):
    """New Outlook is not installed or its compose window could not be opened."""


class NewOutlookAutomationError(RuntimeError):
    """New Outlook opened, but the draft could not be completed safely."""


def create_new_outlook_draft(
    *,
    subject: str,
    from_account: str,
    to: str,
    cc: str,
    body_text: str,
    attachments: list[Path],
    inline_images: list[Path],
) -> None:
    started_at = time.monotonic()
    stage = "detect-executable"
    desktop = None
    log.info(
        "new_outlook_draft: ===== automation started subject=%r from=%r to_count=%s "
        "cc_count=%s attachments_requested=%s inline_images_requested=%s =====",
        subject,
        from_account,
        len(_split_recipients(to)),
        len(_split_recipients(cc)),
        len([path for path in attachments if path]),
        len([path for path in inline_images if path]),
    )
    log_outlook_environment(log, stage="new-outlook-start")
    executable = find_new_outlook_executable()
    if executable is None:
        log.error("new_outlook_draft: olk.exe was not found")
        raise NewOutlookUnavailable("New Outlook (olk.exe) is not installed.")

    existing_attachments = [Path(path) for path in attachments if path and Path(path).is_file()]
    existing_images = [Path(path) for path in inline_images if path and Path(path).is_file()]
    compose_window = None
    main_window = None
    started_main_window = False
    draft_saved = False
    compose_closed = False
    previous_clipboard_text = _get_clipboard_text()

    try:
        import pythoncom
    except ImportError as exc:
        raise NewOutlookUnavailable("New Outlook automation requires pywin32 and pywinauto.") from exc

    pythoncom.CoInitialize()
    try:
        try:
            from pywinauto import Desktop, keyboard
        except ImportError as exc:
            raise NewOutlookUnavailable(
                "New Outlook automation requires pywin32 and pywinauto."
            ) from exc
        stage = "initialize-uia"
        log.info("new_outlook_draft: COM initialized for Outlook automation thread")
        desktop = Desktop(backend="uia")
        stage = "inventory-existing-compose"
        existing_outlook_handles = {
            window.handle
            for window in desktop.windows()
            if str(window.element_info.class_name) == "Outlook Host"
        }
        existing_handles = {
            window.handle
            for window in desktop.windows()
            if str(window.element_info.class_name) == "Outlook Host"
            and _has_subject_control(window)
        }
        log.info(
            "new_outlook_draft: existing Outlook handles=%s compose_handles=%s",
            sorted(existing_outlook_handles),
            sorted(existing_handles),
        )
        log.info(
            "new_outlook_draft: opening main window path=%s subject=%r attachments=%s "
            "inline_images=%s",
            executable,
            subject,
            len(existing_attachments),
            len(existing_images),
        )
        stage = "open-main-window"
        main_window = _open_new_outlook_main_window(
            desktop,
            executable,
            existing_outlook_handles=existing_outlook_handles,
            from_account=from_account,
        )
        started_main_window = main_window.handle not in existing_outlook_handles
        log.info(
            "new_outlook_draft: main window ready handle=%s title=%r started_here=%s elapsed=%.2fs",
            getattr(main_window, "handle", "<unknown>"),
            main_window.window_text(),
            started_main_window,
            time.monotonic() - started_at,
        )
        stage = "open-new-mail"
        _open_new_mail(
            main_window,
            keyboard,
            timeout=NEW_OUTLOOK_START_TIMEOUT_SECONDS,
        )
        stage = "wait-compose-window"
        compose_surface = _wait_for_compose_window(
            desktop,
            existing_handles,
            timeout=NEW_OUTLOOK_START_TIMEOUT_SECONDS,
        )
        compose_window = compose_surface
        log.info(
            "new_outlook_draft: compose surface ready handle=%s main_handle=%s",
            getattr(compose_surface, "handle", "<unknown>"),
            getattr(main_window, "handle", "<unknown>"),
        )
        stage = "ensure-popout"
        compose_window = _ensure_popout_window(
            desktop,
            main_window,
            compose_surface,
            timeout=NEW_OUTLOOK_START_TIMEOUT_SECONDS,
        )
        log.info(
            "new_outlook_draft: popout compose ready handle=%s title=%r",
            getattr(compose_window, "handle", "<unknown>"),
            compose_window.window_text(),
        )
        stage = "wait-compose-controls"
        _wait_for_compose_controls(compose_window, timeout=NEW_OUTLOOK_START_TIMEOUT_SECONDS)
        stage = "select-from-account"
        _ensure_from_account(desktop, compose_window, from_account)
        # New mail opens with focus in To. Preserve that native sequence:
        # paste To, Tab to Cc, paste Cc, then populate subject and body.
        stage = "fill-recipients"
        _fill_recipient_fields(compose_window, to, cc, keyboard)
        stage = "fill-subject"
        _fill_subject(compose_window, subject, keyboard)
        stage = "find-body-editor"
        body = _find_body_editor(compose_window)

        message_text = (
            "Confidential - Oracle Restricted \\Including External Recipients\r\n\r\n"
            f"{body_text.strip()}\r\n\r\n"
        )
        stage = "fill-body"
        _fill_body(body, message_text, keyboard)

        if existing_attachments:
            stage = "paste-attachments"
            _set_clipboard_files(existing_attachments)
            keyboard.send_keys("^v")
            _wait_for_attachment_chips(compose_window, existing_attachments, timeout=20.0)

        for image_path in existing_images:
            stage = "paste-inline-image"
            _set_clipboard_image(image_path)
            keyboard.send_keys("^v")
            # New Outlook consumes bitmap clipboard data asynchronously. Keep
            # it available until the editor has materialized the image.
            time.sleep(NEW_OUTLOOK_IMAGE_PASTE_SETTLE_SECONDS)
            keyboard.send_keys("{ENTER}{ENTER}")
            log.info(
                "new_outlook_draft: inline image pasted and settled delay=%.1fs path=%s",
                NEW_OUTLOOK_IMAGE_PASTE_SETTLE_SECONDS,
                image_path,
            )

        stage = "save-draft"
        _save_draft(compose_window, keyboard)
        stage = "confirm-save"
        saved_label = _wait_for_saved_confirmation(
            compose_window,
            timeout=NEW_OUTLOOK_SAVE_TIMEOUT_SECONDS,
            minimum_wait=NEW_OUTLOOK_SAVE_SETTLE_SECONDS,
        )
        draft_saved = True
        log.info("new_outlook_draft: save confirmed status=%r", saved_label)
        stage = "close-compose"
        _close_popout_compose(compose_window, desktop=desktop)
        compose_closed = True
        stage = "show-drafts-folder"
        _show_drafts_folder(main_window)
        log.info(
            "new_outlook_draft: compose window closed; draft remains in Drafts elapsed=%.2fs",
            time.monotonic() - started_at,
        )
        log.info("new_outlook_draft: ===== automation completed =====")
    except NewOutlookUnavailable:
        log.exception(
            "new_outlook_draft: unavailable stage=%s elapsed=%.2fs",
            stage,
            time.monotonic() - started_at,
        )
        if desktop is not None:
            log_outlook_uia_windows(log, desktop, stage=f"new-unavailable-{stage}")
        if compose_window is not None and not draft_saved:
            compose_closed = _discard_failed_compose(compose_window)
        elif compose_window is not None and not compose_closed:
            log.warning(
                "new_outlook_draft: saved compose remains open for manual recovery; "
                "it will not be discarded"
            )
        raise
    except Exception as exc:
        log.exception(
            "new_outlook_draft: automation failed stage=%s elapsed=%.2fs",
            stage,
            time.monotonic() - started_at,
        )
        if desktop is not None:
            log_outlook_uia_windows(log, desktop, stage=f"new-failed-{stage}")
        if compose_window is not None and not draft_saved:
            compose_closed = _discard_failed_compose(compose_window)
        elif compose_window is not None and not compose_closed:
            log.warning(
                "new_outlook_draft: saved compose remains open for manual recovery; "
                "it will not be discarded"
            )
        raise NewOutlookAutomationError(str(exc)) from exc
    finally:
        try:
            _restore_clipboard_text(previous_clipboard_text)
        except Exception:
            log.warning("new_outlook_draft: could not restore clipboard text", exc_info=True)
        if (
            started_main_window
            and main_window is not None
            and (compose_window is None or compose_closed)
        ):
            _close_started_main_window(main_window)
        log_outlook_processes(log, stage="new-outlook-finally")
        pythoncom.CoUninitialize()


def find_new_outlook_executable() -> Path | None:
    found = shutil.which("olk.exe")
    if found:
        path = Path(found)
        log.info("new_outlook_draft: olk.exe found through PATH path=%s", path)
        return path
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        alias = Path(local_app_data) / "Microsoft" / "WindowsApps" / "olk.exe"
        if alias.exists():
            log.info("new_outlook_draft: olk.exe found through WindowsApps alias path=%s", alias)
            return alias
        log.info("new_outlook_draft: WindowsApps olk.exe alias missing path=%s", alias)
    else:
        log.info("new_outlook_draft: LOCALAPPDATA is unset; WindowsApps alias cannot be checked")
    return None


def _launch_new_outlook(executable: Path, *, activation: str = "app-id") -> None:
    if os.name == "nt":
        if activation == "app-id":
            target = "explorer.exe"
            parameters = f"shell:AppsFolder\\{NEW_OUTLOOK_APP_USER_MODEL_ID}"
            working_directory = None
        elif activation == "alias":
            target = str(executable)
            parameters = None
            working_directory = str(executable.parent)
        else:
            raise ValueError(f"Unsupported New Outlook activation method: {activation}")
        result = ctypes.windll.shell32.ShellExecuteW(
            None,
            "open",
            target,
            parameters,
            working_directory,
            1,
        )
        log.info(
            "new_outlook_draft: ShellExecuteW returned=%s activation=%s target=%s "
            "parameters=%r cwd=%s",
            result,
            activation,
            target,
            parameters,
            working_directory,
        )
        if result <= 32:
            raise NewOutlookUnavailable(
                f"Windows could not activate New Outlook through {activation} (code {result})."
            )
        return
    try:
        subprocess.Popen([str(executable)], cwd=str(executable.parent))
    except OSError as exc:
        raise NewOutlookUnavailable(f"New Outlook could not be started: {exc}") from exc


def _split_recipients(raw: str) -> list[str]:
    recipients: list[str] = []
    for chunk in re.split(r";|\n", raw or ""):
        text = chunk.strip()
        if not text:
            continue
        match = re.search(r"<([^>]+)>", text)
        recipients.append((match.group(1) if match else text).strip())
    return recipients


def _open_new_outlook_main_window(
    desktop,
    executable: Path,
    *,
    existing_outlook_handles: set[int],
    from_account: str = "",
):
    protected_process_ids = _new_outlook_process_ids()
    if not existing_outlook_handles and protected_process_ids:
        log.warning(
            "new_outlook_draft: headless olk.exe detected before launch pids=%s; restarting it",
            sorted(protected_process_ids),
        )
        _terminate_new_outlook_processes(protected_process_ids, reason="prelaunch-headless")
        protected_process_ids = set()

    log.info(
        "new_outlook_draft: launching activation=app-id protected_pids=%s",
        sorted(protected_process_ids),
    )
    _launch_new_outlook(executable, activation="app-id")
    log_outlook_processes(log, stage="new-outlook-after-app-id-launch")
    try:
        return _wait_for_main_window(
            desktop,
            timeout=NEW_OUTLOOK_INITIAL_WINDOW_TIMEOUT_SECONDS,
            from_account=from_account,
        )
    except NewOutlookUnavailable:
        if _outlook_host_handles(desktop):
            raise

    current_process_ids = _new_outlook_process_ids()
    recovery_process_ids = current_process_ids - protected_process_ids
    log.warning(
        "new_outlook_draft: app-id activation produced no window; recovery_pids=%s",
        sorted(recovery_process_ids),
    )
    _terminate_new_outlook_processes(recovery_process_ids, reason="app-id-no-window")
    log.info("new_outlook_draft: retrying launch activation=alias")
    _launch_new_outlook(executable, activation="alias")
    log_outlook_processes(log, stage="new-outlook-after-alias-retry")
    return _wait_for_main_window(
        desktop,
        timeout=NEW_OUTLOOK_RECOVERY_WINDOW_TIMEOUT_SECONDS,
        from_account=from_account,
    )


def _new_outlook_process_ids() -> set[int]:
    try:
        import psutil
    except ImportError:
        log.warning("new_outlook_draft: psutil unavailable; olk.exe recovery is disabled")
        return set()

    process_ids: set[int] = set()
    for process in psutil.process_iter(["pid", "name"]):
        try:
            if str(process.info.get("name") or "").casefold() == "olk.exe":
                process_ids.add(int(process.info["pid"]))
        except (OSError, psutil.Error, TypeError, ValueError):
            continue
    return process_ids


def _terminate_new_outlook_processes(process_ids: set[int], *, reason: str) -> None:
    if not process_ids:
        return
    try:
        import psutil
    except ImportError:
        return

    processes = []
    for process_id in sorted(process_ids):
        try:
            process = psutil.Process(process_id)
            if process.name().casefold() != "olk.exe":
                continue
            process.terminate()
            processes.append(process)
            log.info(
                "new_outlook_draft: terminated headless olk.exe pid=%s reason=%s",
                process_id,
                reason,
            )
        except (OSError, psutil.Error):
            log.debug(
                "new_outlook_draft: olk.exe pid changed during recovery pid=%s reason=%s",
                process_id,
                reason,
                exc_info=True,
            )
    if not processes:
        return
    _, alive = psutil.wait_procs(processes, timeout=5.0)
    for process in alive:
        try:
            process.kill()
            log.warning(
                "new_outlook_draft: killed unresponsive headless olk.exe pid=%s reason=%s",
                process.pid,
                reason,
            )
        except (OSError, psutil.Error):
            pass


def _outlook_host_handles(desktop) -> set[int]:
    handles: set[int] = set()
    try:
        windows = desktop.windows()
    except Exception:
        return handles
    for window in windows:
        try:
            if str(window.element_info.class_name) == "Outlook Host":
                handles.add(int(window.handle))
        except (AttributeError, TypeError, ValueError):
            continue
    return handles


def _wait_for_main_window(desktop, *, timeout: float, from_account: str = ""):
    log.info("new_outlook_draft: waiting for main Mail window timeout=%.1fs", timeout)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        candidates = []
        for window in desktop.windows():
            try:
                if str(window.element_info.class_name) != "Outlook Host":
                    continue
                if _has_subject_control(window):
                    continue
                rectangle = window.rectangle()
                if rectangle.width() <= 0 or rectangle.height() <= 0:
                    continue
                candidates.append(window)
            except Exception:
                continue
        if candidates:
            selected = max(
                candidates,
                key=lambda window: _main_window_score(window, from_account=from_account),
            )
            log.info(
                "new_outlook_draft: main Mail candidates=%s selected_handle=%s title=%r",
                len(candidates),
                getattr(selected, "handle", "<unknown>"),
                selected.window_text(),
            )
            return selected
        time.sleep(0.4)
    log_outlook_processes(log, stage="new-main-window-timeout")
    log_outlook_uia_windows(log, desktop, stage="new-main-window-timeout")
    raise NewOutlookUnavailable("New Outlook did not open its main Mail window.")


def _main_window_score(window, *, from_account: str = "") -> tuple[int, int, int, int]:
    title = str(window.window_text() or "").casefold()
    account_match = _window_account_match_score(window, from_account)
    has_new_mail = 0
    try:
        has_new_mail = int(
            any(
                _new_mail_control_kind(control) is not None
                for control in window.descendants()
            )
        )
    except Exception:
        pass
    rectangle = window.rectangle()
    return (
        account_match,
        has_new_mail,
        int("outlook" in title or title.startswith("mail")),
        rectangle.width() * rectangle.height(),
    )


def _window_account_match_score(window, from_account: str) -> int:
    wanted = from_account.strip().casefold()
    if not wanted:
        return 0

    title = str(window.window_text() or "").strip().casefold()
    try:
        searchable = " ".join(
            _control_search_text(control)
            for control in window.descendants()
        )
    except Exception:
        searchable = ""
    if wanted in searchable:
        return 3

    local_part = wanted.split("@", 1)[0]
    expected_name = " ".join(part for part in re.split(r"[._-]+", local_part) if part)
    title_parts = [part.strip() for part in title.split(" - ")]
    if expected_name and expected_name in title_parts:
        return 2
    if expected_name and expected_name in title:
        return 1
    return 0


def _open_new_mail(
    main_window,
    keyboard,
    *,
    timeout: float,
    poll_interval: float = 0.4,
) -> None:
    log.info("new_outlook_draft: waiting for New mail/New control timeout=%.1fs", timeout)
    deadline = time.monotonic() + max(0.0, timeout)
    scans = 0
    last_button_count = 0
    last_control_labels: list[str] = []
    while time.monotonic() < deadline:
        scans += 1
        try:
            controls = main_window.descendants()
            buttons = [control for control in controls if _new_mail_control_kind(control)]
            last_control_labels = _button_like_control_labels(controls)
        except Exception:
            buttons = []
            log.debug("new_outlook_draft: New mail control scan failed while Outlook loads", exc_info=True)
        last_button_count = len(buttons)

        for button in buttons:
            try:
                if not button.is_enabled():
                    continue
            except Exception:
                pass
            kind = _new_mail_control_kind(button)
            label = _preferred_control_label(button)
            control_type = str(getattr(button.element_info, "control_type", ""))

            # Some New Outlook builds collapse the compose split button to
            # just "New". Invoking it may open the type menu instead of Mail,
            # so use Outlook's native compose shortcut on the validated window.
            if kind == "compact":
                try:
                    main_window.set_focus()
                    keyboard.send_keys("^n")
                    log.info(
                        "new_outlook_draft: compact compose control found; sent Ctrl+N "
                        "label=%r type=%s scans=%s",
                        label,
                        control_type,
                        scans,
                    )
                    return
                except Exception:
                    log.debug(
                        "new_outlook_draft: Ctrl+N failed for compact compose control",
                        exc_info=True,
                    )
            try:
                button.invoke()
            except Exception:
                button.click_input()
            log.info(
                "new_outlook_draft: compose control invoked label=%r type=%s scans=%s",
                label,
                control_type,
                scans,
            )
            return
        time.sleep(max(0.0, poll_interval))

    log.warning(
        "new_outlook_draft: New mail button timeout scans=%s last_button_count=%s "
        "main_handle=%s title=%r button_like_controls=%s",
        scans,
        last_button_count,
        getattr(main_window, "handle", "<unknown>"),
        main_window.window_text(),
        last_control_labels,
    )
    try:
        main_window.set_focus()
        keyboard.send_keys("^n")
        log.info("new_outlook_draft: compose control not identified; sent Ctrl+N fallback")
    except Exception as exc:
        raise NewOutlookUnavailable(
            "New Outlook opened, but its New mail control could not be activated."
        ) from exc


def _new_mail_control_kind(control) -> str | None:
    control_type = str(getattr(control.element_info, "control_type", ""))
    if control_type not in NEW_MAIL_CONTROL_TYPES:
        return None
    labels = _control_labels(control)
    if any(_label_matches_prefix(label, NEW_MAIL_DIRECT_LABELS) for label in labels):
        return "direct"
    if any(label in NEW_MAIL_COMPACT_LABELS for label in labels):
        return "compact"
    return None


def _control_labels(control) -> set[str]:
    values: list[object] = []
    try:
        values.append(control.window_text())
    except Exception:
        pass
    try:
        values.append(control.element_info.name)
    except Exception:
        pass
    labels = set()
    for value in values:
        if not isinstance(value, str):
            continue
        normalized = re.sub(r"\s+", " ", value.replace("&", " ")).strip().casefold()
        if normalized:
            labels.add(normalized)
    return labels


def _preferred_control_label(control) -> str:
    labels = sorted(_control_labels(control), key=lambda value: (len(value), value))
    return labels[0] if labels else ""


def _label_matches_prefix(label: str, expected: set[str]) -> bool:
    return any(
        label == candidate
        or label.startswith(f"{candidate} ")
        or label.startswith(f"{candidate},")
        for candidate in expected
    )


def _button_like_control_labels(controls) -> list[str]:
    labels = {
        f"{control.element_info.control_type}:{label}"
        for control in controls
        if str(getattr(control.element_info, "control_type", "")) in NEW_MAIL_CONTROL_TYPES
        for label in _control_labels(control)
    }
    return sorted(labels)[:40]


def _wait_for_compose_window(desktop, existing_handles: set[int], *, timeout: float):
    log.info(
        "new_outlook_draft: waiting for compose window timeout=%.1fs excluded_handles=%s",
        timeout,
        sorted(existing_handles),
    )
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for window in desktop.windows():
            if window.handle in existing_handles:
                continue
            if str(window.element_info.class_name) != "Outlook Host":
                continue
            if _has_subject_control(window):
                log.info(
                    "new_outlook_draft: compose window found handle=%s title=%r",
                    getattr(window, "handle", "<unknown>"),
                    window.window_text(),
                )
                return window
        time.sleep(0.4)
    log_outlook_uia_windows(log, desktop, stage="new-compose-window-timeout")
    raise NewOutlookUnavailable("New Outlook did not open a New mail compose window.")


def _has_subject_control(window) -> bool:
    try:
        return any(
            control.element_info.control_type == "Edit"
            and str(control.element_info.automation_id).endswith("_SUBJECT")
            for control in window.descendants()
        )
    except Exception:
        return False


def _ensure_popout_window(desktop, main_window, compose_surface, *, timeout: float):
    if compose_surface.handle != main_window.handle:
        log.info("new_outlook_draft: compose is already a popout window")
        return compose_surface
    popout_buttons = [
        control
        for control in compose_surface.descendants()
        if control.element_info.control_type == "Button"
        and str(control.element_info.automation_id) == "popoutCompose"
    ]
    if not popout_buttons:
        log_outlook_uia_windows(log, desktop, stage="new-popout-control-missing")
        raise NewOutlookAutomationError("New Outlook did not expose the Pop Out compose control.")
    existing_handles = {window.handle for window in desktop.windows()}
    log.info(
        "new_outlook_draft: invoking Pop Out control existing_handles=%s",
        sorted(existing_handles),
    )
    popout_buttons[0].invoke()
    return _wait_for_compose_window(desktop, existing_handles, timeout=timeout)


def _wait_for_compose_controls(window, *, timeout: float) -> None:
    log.info("new_outlook_draft: waiting for compose controls timeout=%.1fs", timeout)
    deadline = time.monotonic() + timeout
    last_control_count = 0
    last_has_subject = False
    last_has_from = False
    while time.monotonic() < deadline:
        controls = window.descendants()
        last_control_count = len(controls)
        has_subject = any(
            control.element_info.control_type == "Edit"
            and str(control.element_info.automation_id).endswith("_SUBJECT")
            for control in controls
        )
        has_from = any(
            control.element_info.control_type == "Button"
            and str(control.element_info.automation_id).endswith("_FROM")
            for control in controls
        )
        last_has_subject = has_subject
        last_has_from = has_from
        if has_subject and has_from:
            log.info(
                "new_outlook_draft: compose controls ready controls=%s",
                last_control_count,
            )
            return
        time.sleep(0.4)
    log.warning(
        "new_outlook_draft: compose controls timeout controls=%s has_subject=%s has_from=%s",
        last_control_count,
        last_has_subject,
        last_has_from,
    )
    raise NewOutlookUnavailable("New Outlook opened, but its compose controls did not load.")


def _ensure_from_account(desktop, window, from_account: str) -> None:
    wanted = from_account.strip().lower()
    if not wanted:
        log.info("new_outlook_draft: From account is empty; keeping Outlook default")
        return
    from_buttons = [
        control
        for control in window.descendants()
        if control.element_info.control_type == "Button"
        and str(control.element_info.automation_id).endswith("_FROM")
    ]
    if not from_buttons:
        log.warning("new_outlook_draft: From account controls found=0 wanted=%s", wanted)
        raise NewOutlookAutomationError("New Outlook did not expose the From account control.")
    from_button = from_buttons[0]
    current = from_button.window_text().lower()
    log.info(
        "new_outlook_draft: From account check wanted=%s current=%r controls=%s",
        wanted,
        current,
        len(from_buttons),
    )
    if wanted in current:
        log.info("new_outlook_draft: correct From account already selected account=%s", wanted)
        return

    from_button.click_input()
    deadline = time.monotonic() + 8.0
    while time.monotonic() < deadline:
        for top_window in desktop.windows():
            for control in [top_window] + top_window.descendants():
                if control.element_info.control_type not in ("MenuItem", "ListItem", "Button"):
                    continue
                if wanted in _control_search_text(control):
                    control.click_input()
                    log.info("new_outlook_draft: selected From account=%s", wanted)
                    return
        time.sleep(0.3)
    raise NewOutlookAutomationError(
        f"New Outlook is using a different From account and {from_account} could not be selected."
    )


def _control_search_text(control) -> str:
    values = [
        str(control.window_text() or ""),
        str(getattr(control.element_info, "name", "") or ""),
    ]
    try:
        for descendant in control.descendants():
            values.append(str(descendant.window_text() or ""))
            values.append(str(getattr(descendant.element_info, "name", "") or ""))
    except Exception:
        pass
    return " ".join(values).casefold()


def _fill_recipient_fields(window, to_text: str, cc_text: str, keyboard) -> None:
    to_recipients = _split_recipients(to_text)
    cc_recipients = _split_recipients(cc_text)
    if not to_recipients:
        log.info("new_outlook_draft: no To recipients configured")
        return

    log.info(
        "new_outlook_draft: pasting recipient rows to_count=%s cc_count=%s to_chars=%s cc_chars=%s",
        len(to_recipients),
        len(cc_recipients),
        len(to_text),
        len(cc_text),
    )
    _set_clipboard_text(to_text.strip())
    keyboard.send_keys("^v")
    log.info("new_outlook_draft: To row paste sent")
    # New Outlook resolves the whole pasted row without Enter. Keeping focus
    # untouched is important because one Tab then reaches the Cc row.
    time.sleep(3.0)

    if cc_recipients:
        keyboard.send_keys("{TAB}")
        log.info("new_outlook_draft: one Tab sent from To row to Cc row")
        _set_clipboard_text(cc_text.strip())
        keyboard.send_keys("^v")
        log.info("new_outlook_draft: Cc row paste sent")
        time.sleep(3.0)

    _wait_for_recipient_confirmation(window, "_TO", len(to_recipients), timeout=20.0)
    if cc_recipients:
        _wait_for_recipient_confirmation(window, "_CC", len(cc_recipients), timeout=20.0)
    log.info(
        "new_outlook_draft: recipient fields completed to_count=%s cc_count=%s",
        len(to_recipients),
        len(cc_recipients),
    )
def _find_recipient_well(window, automation_suffix: str):
    wells = [
        control
        for control in window.descendants()
        if control.element_info.control_type == "Group"
        and str(control.element_info.automation_id).endswith(automation_suffix)
    ]
    return wells[0] if wells else None


def _find_recipient_input(well, automation_suffix: str):
    wanted_label = automation_suffix.removeprefix("_").casefold()
    candidates = [
        control
        for control in well.descendants()
        if control.element_info.control_type in ("Edit", "Group")
        and control.window_text().strip().casefold() == wanted_label
    ]
    if candidates:
        return candidates[0]
    raise NewOutlookAutomationError(
        f"New Outlook did not expose the input surface for recipient field {automation_suffix}."
    )


def _recipient_is_visible(well, recipient: str) -> bool:
    wanted = recipient.strip().casefold()
    for control in well.descendants():
        values: list[str] = []
        try:
            values.append(str(control.window_text()))
        except Exception:
            pass
        try:
            values.append(str(control.element_info.name))
        except Exception:
            pass
        if any(wanted in value.casefold() for value in values):
            return True
    return False


def _recipient_chip_count(well) -> int:
    chip_ids: set[str] = set()
    described_totals: list[int] = []
    position_pattern = re.compile(r"\b\d+\s+(?:of|de)\s+(\d+)\b", re.IGNORECASE)
    for control in well.descendants():
        if control.element_info.control_type == "Group":
            automation_id = str(control.element_info.automation_id)
            if automation_id.startswith("REK"):
                chip_ids.add(automation_id)
        try:
            value = str(control.window_text())
        except Exception:
            value = ""
        match = position_pattern.search(value)
        if match:
            described_totals.append(int(match.group(1)))
    return max([len(chip_ids), *described_totals], default=0)


def _wait_for_recipient_confirmation(
    window,
    automation_suffix: str,
    expected_count: int,
    *,
    timeout: float,
) -> None:
    deadline = time.monotonic() + timeout
    confirmed = 0
    previous_confirmed = -1
    while time.monotonic() < deadline:
        well = _find_recipient_well(window, automation_suffix)
        if well is not None:
            confirmed = _recipient_chip_count(well)
            if confirmed != previous_confirmed:
                log.info(
                    "new_outlook_draft: recipient resolution field=%s confirmed=%s expected=%s",
                    automation_suffix,
                    confirmed,
                    expected_count,
                )
                previous_confirmed = confirmed
            if confirmed >= expected_count:
                return
        time.sleep(0.2)
    raise NewOutlookAutomationError(
        f"New Outlook resolved {confirmed} of {expected_count} recipients in the selected field."
    )


def _fill_subject(window, subject: str, keyboard) -> None:
    fields = [
        control
        for control in window.descendants()
        if control.element_info.control_type == "Edit"
        and str(control.element_info.automation_id).endswith("_SUBJECT")
    ]
    if not fields:
        raise NewOutlookAutomationError("New Outlook did not expose the Subject field.")
    field = fields[0]
    field.click_input()
    try:
        field.set_edit_text(subject)
        log.info("new_outlook_draft: subject assigned through UIA chars=%s", len(subject))
    except Exception:
        log.debug(
            "new_outlook_draft: UIA subject assignment failed; using clipboard fallback",
            exc_info=True,
        )
        keyboard.send_keys("^a")
        _set_clipboard_text(subject)
        keyboard.send_keys("^v")
        log.info("new_outlook_draft: subject pasted from clipboard chars=%s", len(subject))

    # Publish the field value before moving on to recipient controls.
    keyboard.send_keys("{TAB}")
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if _control_contains_text(field, subject):
            log.info("new_outlook_draft: subject completed")
            return
        time.sleep(0.15)
    raise NewOutlookAutomationError("New Outlook did not confirm the message subject.")


def _control_contains_text(control, expected: str) -> bool:
    wanted = expected.strip().casefold()
    values: list[str] = []

    for getter_name in ("get_value", "window_text"):
        try:
            value = getattr(control, getter_name)()
        except Exception:
            continue
        if value is not None:
            values.append(str(value))

    try:
        values.append(str(control.element_info.name))
    except Exception:
        pass

    try:
        values.append(str(control.iface_value.CurrentValue))
    except Exception:
        pass

    return any(wanted in value.strip().casefold() for value in values)


def _find_body_editor(window):
    editors = [
        control
        for control in window.descendants()
        if control.element_info.control_type == "Edit"
        and not str(control.element_info.automation_id)
    ]
    if not editors:
        raise NewOutlookAutomationError("New Outlook did not expose the message body editor.")
    log.info("new_outlook_draft: body editor candidates=%s", len(editors))
    return editors[-1]


def _fill_body(body, message_text: str, keyboard) -> None:
    body.click_input()
    keyboard.send_keys("^{HOME}")
    _set_clipboard_text(message_text)
    keyboard.send_keys("^v")
    log.info("new_outlook_draft: body paste sent chars=%s", len(message_text))
    marker = "Confidential - Oracle Restricted"
    deadline = time.monotonic() + 4.0
    while time.monotonic() < deadline:
        if marker.casefold() in body.window_text().casefold():
            log.info("new_outlook_draft: body text completed")
            return
        time.sleep(0.2)
    raise NewOutlookAutomationError("New Outlook did not confirm the message body.")


def _wait_for_attachment_chips(window, attachments: list[Path], *, timeout: float) -> None:
    wanted = {path.name.casefold() for path in attachments}
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        visible = [
            control.window_text().strip().casefold()
            for control in window.descendants()
            if control.window_text().strip()
        ]
        if all(any(filename in label for label in visible) for filename in wanted):
            log.info("new_outlook_draft: attachment paste confirmed files=%s", sorted(wanted))
            return
        time.sleep(0.4)
    raise NewOutlookAutomationError(
        "New Outlook did not confirm every attachment: " + ", ".join(path.name for path in attachments)
    )


def _save_draft(window, keyboard) -> None:
    file_buttons = [
        control
        for control in window.descendants()
        if control.element_info.control_type == "Button"
        and control.window_text().strip().lower() in ("file", "archivo")
    ]
    if not file_buttons:
        log.warning("new_outlook_draft: File menu controls found=0")
        raise NewOutlookAutomationError("New Outlook did not expose its File menu.")
    log.info("new_outlook_draft: opening File menu controls=%s", len(file_buttons))
    file_buttons[0].click_input()
    time.sleep(0.25)
    keyboard.send_keys("{ENTER}")
    log.info("new_outlook_draft: Save draft command submitted through File menu")


def _wait_for_saved_confirmation(
    window,
    *,
    timeout: float,
    minimum_wait: float = 0.0,
) -> str:
    started_at = time.monotonic()
    not_before = started_at + max(0.0, minimum_wait)
    log.info(
        "new_outlook_draft: waiting for Draft saved confirmation timeout=%.1fs "
        "minimum_wait=%.1fs",
        timeout,
        minimum_wait,
    )
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for control in window.descendants():
            text = control.window_text().strip()
            lowered = text.lower()
            if "draft saved" in lowered or "borrador guardado" in lowered:
                if time.monotonic() < not_before:
                    break
                log.info("new_outlook_draft: Draft saved confirmation found text=%r", text)
                return text
        time.sleep(0.25)
    log.warning(
        "new_outlook_draft: Draft saved confirmation timed out handle=%s title=%r",
        getattr(window, "handle", "<unknown>"),
        window.window_text(),
    )
    raise NewOutlookAutomationError("New Outlook did not confirm that the message was saved in Drafts.")


def _close_popout_compose(window, *, desktop=None, timeout: float = 20.0) -> None:
    handle = int(getattr(window, "handle", 0) or 0)
    close_buttons = [
        control
        for control in window.descendants()
        if control.element_info.control_type == "Button"
        and str(control.element_info.automation_id) == "windowIconClose"
    ]
    if close_buttons:
        log.info("new_outlook_draft: closing compose through window close control")
        close_buttons[0].invoke()
    else:
        log.info("new_outlook_draft: closing compose through window.close fallback")
        window.close()

    deadline = time.monotonic() + max(0.0, timeout)
    confirmation_submitted = False
    close_prompt_labels = ("close draft", "cerrar borrador")
    save_labels = {"yes", "si", "sí", "save", "guardar"}
    while time.monotonic() < deadline:
        if not _native_window_exists(handle, window):
            log.info("new_outlook_draft: compose window closure confirmed handle=%s", handle)
            return
        try:
            candidates = [window]
            if desktop is not None:
                candidates.extend(
                    candidate
                    for candidate in desktop.windows()
                    if getattr(candidate, "handle", None) != handle
                )
            for candidate in candidates:
                controls = candidate.descendants()
                texts = [str(candidate.window_text() or "").strip().casefold()]
                texts.extend(
                    str(control.window_text() or "").strip().casefold()
                    for control in controls
                )
                has_close_prompt = any(
                    any(label in text for label in close_prompt_labels)
                    for text in texts
                    if text
                )
                if not has_close_prompt or confirmation_submitted:
                    continue
                save_buttons = [
                    control
                    for control in controls
                    if control.element_info.control_type == "Button"
                    and control.window_text().replace("&", "").strip().casefold() in save_labels
                ]
                if save_buttons:
                    try:
                        save_buttons[0].invoke()
                    except Exception:
                        save_buttons[0].click_input()
                    log.info("new_outlook_draft: Close draft confirmed with Yes/Save")
                else:
                    candidate.set_focus()
                    candidate.type_keys("{ENTER}", set_foreground=True)
                    log.info("new_outlook_draft: Close draft confirmed with Enter fallback")
                confirmation_submitted = True
                break
        except Exception:
            log.debug("new_outlook_draft: waiting for compose window to close", exc_info=True)
        time.sleep(0.2)

    raise NewOutlookAutomationError(
        "New Outlook saved the draft but did not close its compose window."
    )


def _native_window_exists(handle: int, window) -> bool:
    if os.name == "nt" and handle:
        try:
            return bool(ctypes.windll.user32.IsWindow(handle))
        except Exception:
            log.debug("new_outlook_draft: native IsWindow check failed", exc_info=True)
    try:
        return bool(window.exists(timeout=0))
    except Exception:
        return False


def _discard_failed_compose(window, *, timeout: float = 5.0) -> bool:
    try:
        window.close()
    except Exception:
        log.warning("new_outlook_draft: could not request closing failed compose", exc_info=True)
        return False

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            discard_buttons = [
                control
                for control in window.descendants()
                if control.element_info.control_type == "Button"
                and control.window_text().strip().casefold() in ("no", "discard", "descartar")
            ]
            if discard_buttons:
                discard_buttons[0].invoke()
                log.info("new_outlook_draft: incomplete compose discarded")
                return True
        except Exception:
            log.debug("new_outlook_draft: waiting for close-draft confirmation", exc_info=True)
        time.sleep(0.2)
    log.warning("new_outlook_draft: incomplete compose could not be discarded automatically")
    return False


def _show_drafts_folder(main_window) -> None:
    draft_folders = [
        control
        for control in main_window.descendants()
        if control.element_info.control_type == "TreeItem"
        and control.window_text().strip().lower().startswith(("drafts", "borradores"))
    ]
    if draft_folders:
        draft_folders[0].select()
        log.info("new_outlook_draft: main window navigated to Drafts")
    else:
        log.warning("new_outlook_draft: Drafts tree item was not found after save")


def _close_started_main_window(main_window) -> None:
    """Close only the New Outlook window opened by this automation attempt."""
    try:
        handle = getattr(main_window, "handle", "<unknown>")
        main_window.close()
        log.info("new_outlook_draft: closed main window started by Oracle Tasks handle=%s", handle)
    except Exception:
        log.warning(
            "new_outlook_draft: New Outlook main window started by Oracle Tasks could not be closed",
            exc_info=True,
        )


def _open_clipboard(win32clipboard, *, attempts: int = 10) -> None:
    for attempt in range(attempts):
        try:
            win32clipboard.OpenClipboard()
            return
        except OSError:
            if attempt + 1 >= attempts:
                raise
            time.sleep(0.05 * (attempt + 1))


def _get_clipboard_text() -> str | None:
    try:
        import win32clipboard

        _open_clipboard(win32clipboard)
        try:
            if win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_UNICODETEXT):
                return str(win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT))
        finally:
            win32clipboard.CloseClipboard()
    except Exception:
        log.debug("new_outlook_draft: clipboard text snapshot unavailable", exc_info=True)
    return None


def _set_clipboard_text(value: str) -> None:
    import win32clipboard

    _open_clipboard(win32clipboard)
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32clipboard.CF_UNICODETEXT, value)
    finally:
        win32clipboard.CloseClipboard()


def _set_clipboard_files(paths: list[Path]) -> None:
    import win32clipboard

    payload = make_file_drop_payload(paths)
    _open_clipboard(win32clipboard)
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32clipboard.CF_HDROP, payload)
    finally:
        win32clipboard.CloseClipboard()


def make_file_drop_payload(paths: list[Path]) -> bytes:
    absolute_paths = [str(Path(path).resolve()) for path in paths]
    file_list = ("\0".join(absolute_paths) + "\0\0").encode("utf-16le")
    return struct.pack("IiiII", 20, 0, 0, 0, 1) + file_list


def _set_clipboard_image(
    image_path: Path,
    *,
    max_width: int = OUTLOOK_INLINE_IMAGE_MAX_WIDTH,
    max_height: int = OUTLOOK_INLINE_IMAGE_MAX_HEIGHT,
) -> None:
    import win32clipboard
    from PIL import Image

    with Image.open(image_path) as source:
        image = source.convert("RGB")
        target_size = _fit_image_size(
            image.width,
            image.height,
            max_width=max_width,
            max_height=max_height,
        )
        if target_size != image.size:
            image = image.resize(target_size, Image.Resampling.LANCZOS)
            log.info(
                "new_outlook_draft: inline image resized for email source=%sx%s target=%sx%s path=%s",
                source.width,
                source.height,
                image.width,
                image.height,
                image_path,
            )
        output = io.BytesIO()
        image.save(output, "BMP")
    dib = output.getvalue()[14:]
    _open_clipboard(win32clipboard)
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32clipboard.CF_DIB, dib)
    finally:
        win32clipboard.CloseClipboard()


def _fit_image_size(
    width: int,
    height: int,
    *,
    max_width: int,
    max_height: int,
) -> tuple[int, int]:
    if width <= 0 or height <= 0 or max_width <= 0 or max_height <= 0:
        return width, height
    scale = min(1.0, max_width / width, max_height / height)
    return max(1, round(width * scale)), max(1, round(height * scale))


def _restore_clipboard_text(value: str | None) -> None:
    import win32clipboard

    _open_clipboard(win32clipboard)
    try:
        win32clipboard.EmptyClipboard()
        if value is not None:
            win32clipboard.SetClipboardData(win32clipboard.CF_UNICODETEXT, value)
    finally:
        win32clipboard.CloseClipboard()
