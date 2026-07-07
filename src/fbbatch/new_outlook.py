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


log = logging.getLogger(__name__)
NEW_OUTLOOK_START_TIMEOUT_SECONDS = 45.0
NEW_OUTLOOK_SAVE_TIMEOUT_SECONDS = 45.0
OUTLOOK_INLINE_IMAGE_MAX_WIDTH = 960
OUTLOOK_INLINE_IMAGE_MAX_HEIGHT = 720


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
    executable = find_new_outlook_executable()
    if executable is None:
        raise NewOutlookUnavailable("New Outlook (olk.exe) is not installed.")

    existing_attachments = [Path(path) for path in attachments if path and Path(path).is_file()]
    existing_images = [Path(path) for path in inline_images if path and Path(path).is_file()]
    compose_window = None
    previous_clipboard_text = _get_clipboard_text()

    try:
        import pythoncom
        from pywinauto import Desktop, keyboard
    except ImportError as exc:
        raise NewOutlookUnavailable("New Outlook automation requires pywin32 and pywinauto.") from exc

    pythoncom.CoInitialize()
    try:
        desktop = Desktop(backend="uia")
        existing_handles = {
            window.handle
            for window in desktop.windows()
            if str(window.element_info.class_name) == "Outlook Host"
            and _has_subject_control(window)
        }
        log.info(
            "new_outlook_draft: launching path=%s subject=%r attachments=%s inline_images=%s",
            executable,
            subject,
            len(existing_attachments),
            len(existing_images),
        )
        _launch_new_outlook(executable)
        main_window = _wait_for_main_window(desktop, timeout=NEW_OUTLOOK_START_TIMEOUT_SECONDS)
        _open_new_mail(
            main_window,
            keyboard,
            timeout=NEW_OUTLOOK_START_TIMEOUT_SECONDS,
        )
        compose_surface = _wait_for_compose_window(
            desktop,
            existing_handles,
            timeout=NEW_OUTLOOK_START_TIMEOUT_SECONDS,
        )
        compose_window = _ensure_popout_window(
            desktop,
            main_window,
            compose_surface,
            timeout=NEW_OUTLOOK_START_TIMEOUT_SECONDS,
        )
        _wait_for_compose_controls(compose_window, timeout=NEW_OUTLOOK_START_TIMEOUT_SECONDS)
        _ensure_from_account(desktop, compose_window, from_account)
        _fill_recipient_field(compose_window, "_TO", _split_recipients(to), keyboard)
        _fill_recipient_field(compose_window, "_CC", _split_recipients(cc), keyboard)
        _fill_subject(compose_window, subject, keyboard)
        body = _find_body_editor(compose_window)
        body.click_input()
        keyboard.send_keys("^{HOME}")

        message_text = (
            "Confidential - Oracle Restricted \\Including External Recipients\r\n\r\n"
            f"{body_text.strip()}\r\n\r\n"
        )
        _set_clipboard_text(message_text)
        keyboard.send_keys("^v")
        time.sleep(0.4)

        if existing_attachments:
            _set_clipboard_files(existing_attachments)
            keyboard.send_keys("^v")
            _wait_for_attachment_chips(compose_window, existing_attachments, timeout=20.0)

        for image_path in existing_images:
            _set_clipboard_image(image_path)
            keyboard.send_keys("^v")
            keyboard.send_keys("{ENTER}{ENTER}")
            time.sleep(0.6)
            log.info("new_outlook_draft: inline image pasted path=%s", image_path)

        _save_draft(compose_window, keyboard)
        saved_label = _wait_for_saved_confirmation(
            compose_window,
            timeout=NEW_OUTLOOK_SAVE_TIMEOUT_SECONDS,
        )
        log.info("new_outlook_draft: save confirmed status=%r", saved_label)
        _close_popout_compose(compose_window)
        _show_drafts_folder(main_window)
        log.info("new_outlook_draft: compose window closed; draft remains in Drafts")
    except NewOutlookUnavailable:
        raise
    except Exception as exc:
        log.exception("new_outlook_draft: automation failed")
        raise NewOutlookAutomationError(str(exc)) from exc
    finally:
        try:
            _restore_clipboard_text(previous_clipboard_text)
        except Exception:
            log.warning("new_outlook_draft: could not restore clipboard text", exc_info=True)
        pythoncom.CoUninitialize()


def find_new_outlook_executable() -> Path | None:
    found = shutil.which("olk.exe")
    if found:
        return Path(found)
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        alias = Path(local_app_data) / "Microsoft" / "WindowsApps" / "olk.exe"
        if alias.exists():
            return alias
    return None


def _launch_new_outlook(executable: Path) -> None:
    if os.name == "nt":
        result = ctypes.windll.shell32.ShellExecuteW(
            None,
            "open",
            str(executable),
            None,
            str(executable.parent),
            1,
        )
        if result <= 32:
            raise NewOutlookUnavailable(f"Windows could not activate New Outlook (code {result}).")
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


def _wait_for_main_window(desktop, *, timeout: float):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        candidates = [
            window
            for window in desktop.windows()
            if str(window.element_info.class_name) == "Outlook Host"
            and window.window_text().lower().endswith(" - outlook")
        ]
        if candidates:
            return max(
                candidates,
                key=lambda window: window.rectangle().width() * window.rectangle().height(),
            )
        time.sleep(0.4)
    raise NewOutlookUnavailable("New Outlook did not open its main Mail window.")


def _open_new_mail(
    main_window,
    keyboard,
    *,
    timeout: float,
    poll_interval: float = 0.4,
) -> None:
    deadline = time.monotonic() + max(0.0, timeout)
    while time.monotonic() < deadline:
        try:
            buttons = [
                control
                for control in main_window.descendants()
                if control.element_info.control_type == "Button"
                and control.window_text().strip().lower() in ("new mail", "nuevo correo")
            ]
        except Exception:
            buttons = []
            log.debug("new_outlook_draft: New mail control scan failed while Outlook loads", exc_info=True)

        for button in buttons:
            try:
                if not button.is_enabled():
                    continue
            except Exception:
                pass
            try:
                button.invoke()
            except Exception:
                button.click_input()
            log.info("new_outlook_draft: New mail invoked after Outlook finished loading")
            return
        time.sleep(max(0.0, poll_interval))

    raise NewOutlookUnavailable(
        "New Outlook opened, but its New mail button did not finish loading."
    )


def _wait_for_compose_window(desktop, existing_handles: set[int], *, timeout: float):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for window in desktop.windows():
            if window.handle in existing_handles:
                continue
            if str(window.element_info.class_name) != "Outlook Host":
                continue
            if _has_subject_control(window):
                return window
        time.sleep(0.4)
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
        return compose_surface
    popout_buttons = [
        control
        for control in compose_surface.descendants()
        if control.element_info.control_type == "Button"
        and str(control.element_info.automation_id) == "popoutCompose"
    ]
    if not popout_buttons:
        raise NewOutlookAutomationError("New Outlook did not expose the Pop Out compose control.")
    existing_handles = {window.handle for window in desktop.windows()}
    popout_buttons[0].invoke()
    return _wait_for_compose_window(desktop, existing_handles, timeout=timeout)


def _wait_for_compose_controls(window, *, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        controls = window.descendants()
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
        if has_subject and has_from:
            return
        time.sleep(0.4)
    raise NewOutlookUnavailable("New Outlook opened, but its compose controls did not load.")


def _ensure_from_account(desktop, window, from_account: str) -> None:
    wanted = from_account.strip().lower()
    if not wanted:
        return
    from_buttons = [
        control
        for control in window.descendants()
        if control.element_info.control_type == "Button"
        and str(control.element_info.automation_id).endswith("_FROM")
    ]
    if not from_buttons:
        raise NewOutlookAutomationError("New Outlook did not expose the From account control.")
    from_button = from_buttons[0]
    current = from_button.window_text().lower()
    if wanted in current:
        log.info("new_outlook_draft: correct From account already selected account=%s", wanted)
        return

    from_button.click_input()
    deadline = time.monotonic() + 8.0
    while time.monotonic() < deadline:
        for top_window in desktop.windows():
            for control in [top_window] + top_window.descendants():
                if wanted not in control.window_text().lower():
                    continue
                if control.element_info.control_type in ("MenuItem", "ListItem", "Button"):
                    control.click_input()
                    log.info("new_outlook_draft: selected From account=%s", wanted)
                    return
        time.sleep(0.3)
    raise NewOutlookAutomationError(
        f"New Outlook is using a different From account and {from_account} could not be selected."
    )


def _fill_recipient_field(window, automation_suffix: str, recipients: list[str], keyboard) -> None:
    if not recipients:
        return
    wells = [
        control
        for control in window.descendants()
        if control.element_info.control_type == "Group"
        and str(control.element_info.automation_id).endswith(automation_suffix)
    ]
    if not wells:
        raise NewOutlookAutomationError(f"New Outlook did not expose recipient field {automation_suffix}.")
    well = wells[0]
    well.click_input()
    for recipient in recipients:
        _set_clipboard_text(recipient)
        keyboard.send_keys("^v{ENTER}")
        time.sleep(0.15)


def _fill_subject(window, subject: str, keyboard) -> None:
    fields = [
        control
        for control in window.descendants()
        if control.element_info.control_type == "Edit"
        and str(control.element_info.automation_id).endswith("_SUBJECT")
    ]
    if not fields:
        raise NewOutlookAutomationError("New Outlook did not expose the Subject field.")
    fields[0].click_input()
    keyboard.send_keys("^a")
    _set_clipboard_text(subject)
    keyboard.send_keys("^v")
    time.sleep(0.2)


def _find_body_editor(window):
    editors = [
        control
        for control in window.descendants()
        if control.element_info.control_type == "Edit"
        and not str(control.element_info.automation_id)
    ]
    if not editors:
        raise NewOutlookAutomationError("New Outlook did not expose the message body editor.")
    return editors[-1]


def _wait_for_attachment_chips(window, attachments: list[Path], *, timeout: float) -> None:
    wanted = {path.stem.lower() for path in attachments}
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        visible = {
            control.window_text().strip().lower()
            for control in window.descendants()
            if control.element_info.control_type == "ListItem" and control.window_text().strip()
        }
        if all(any(stem in label for label in visible) for stem in wanted):
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
        raise NewOutlookAutomationError("New Outlook did not expose its File menu.")
    file_buttons[0].click_input()
    time.sleep(0.25)
    keyboard.send_keys("{ENTER}")


def _wait_for_saved_confirmation(window, *, timeout: float) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for control in window.descendants():
            text = control.window_text().strip()
            lowered = text.lower()
            if "draft saved" in lowered or "borrador guardado" in lowered:
                return text
        time.sleep(0.5)
    raise NewOutlookAutomationError("New Outlook did not confirm that the message was saved in Drafts.")


def _close_popout_compose(window) -> None:
    close_buttons = [
        control
        for control in window.descendants()
        if control.element_info.control_type == "Button"
        and str(control.element_info.automation_id) == "windowIconClose"
    ]
    if close_buttons:
        close_buttons[0].invoke()
    else:
        window.close()


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
