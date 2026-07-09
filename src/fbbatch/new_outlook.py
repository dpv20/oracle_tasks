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
        log.info("new_outlook_draft: COM initialized for Outlook automation thread")
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
        # New mail opens with focus in To. Preserve that native sequence:
        # paste To, Tab to Cc, paste Cc, then populate subject and body.
        _fill_recipient_fields(compose_window, to, cc, keyboard)
        _fill_subject(compose_window, subject, keyboard)
        body = _find_body_editor(compose_window)

        message_text = (
            "Confidential - Oracle Restricted \\Including External Recipients\r\n\r\n"
            f"{body_text.strip()}\r\n\r\n"
        )
        _fill_body(body, message_text, keyboard)

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
        if compose_window is not None:
            _discard_failed_compose(compose_window)
        raise
    except Exception as exc:
        log.exception("new_outlook_draft: automation failed")
        if compose_window is not None:
            _discard_failed_compose(compose_window)
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


def _fill_recipient_fields(window, to_text: str, cc_text: str, keyboard) -> None:
    to_recipients = _split_recipients(to_text)
    cc_recipients = _split_recipients(cc_text)
    if not to_recipients:
        return

    _set_clipboard_text(to_text.strip())
    keyboard.send_keys("^v")
    # New Outlook resolves the whole pasted row without Enter. Keeping focus
    # untouched is important because one Tab then reaches the Cc row.
    time.sleep(3.0)

    if cc_recipients:
        keyboard.send_keys("{TAB}")
        _set_clipboard_text(cc_text.strip())
        keyboard.send_keys("^v")
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
    while time.monotonic() < deadline:
        well = _find_recipient_well(window, automation_suffix)
        if well is not None:
            confirmed = _recipient_chip_count(well)
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
    except Exception:
        log.debug(
            "new_outlook_draft: UIA subject assignment failed; using clipboard fallback",
            exc_info=True,
        )
        keyboard.send_keys("^a")
        _set_clipboard_text(subject)
        keyboard.send_keys("^v")

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
    return editors[-1]


def _fill_body(body, message_text: str, keyboard) -> None:
    body.click_input()
    keyboard.send_keys("^{HOME}")
    _set_clipboard_text(message_text)
    keyboard.send_keys("^v")
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
