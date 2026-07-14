from __future__ import annotations

import struct
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"
sys.path.insert(0, str(SRC_DIR))

from fbbatch.new_outlook import (  # noqa: E402
    NewOutlookAutomationError,
    NewOutlookUnavailable,
    _close_popout_compose,
    _close_started_main_window,
    _control_contains_text,
    _discard_failed_compose,
    _ensure_from_account,
    _fill_recipient_fields,
    _fill_subject,
    _fit_image_size,
    _find_recipient_input,
    _launch_new_outlook,
    _open_new_outlook_main_window,
    _open_new_mail,
    _recipient_chip_count,
    _recipient_is_visible,
    _split_recipients,
    _wait_for_main_window,
    _wait_for_saved_confirmation,
    find_new_outlook_executable,
    make_file_drop_payload,
)
from fbbatch.runner import (  # noqa: E402
    ClassicOutlookUnavailable,
    OutlookDraftResult,
    create_outlook_draft,
)


class NewOutlookTests(unittest.TestCase):
    def _draft_arguments(self) -> dict:
        return {
            "subject": "NSSR test",
            "from_account": "sender@example.com",
            "to": "to@example.com",
            "cc": "cc@example.com",
            "body_text": "Body",
            "attachments": [],
            "inline_images": [],
        }

    def test_recipient_parser_keeps_individual_email_addresses(self) -> None:
        self.assertEqual(
            _split_recipients('"One" <one@example.com>; two@example.com'),
            ["one@example.com", "two@example.com"],
        )

    def test_windows_app_alias_is_preferred_for_new_outlook(self) -> None:
        alias = Path(r"C:\Users\Example\AppData\Local\Microsoft\WindowsApps\olk.exe")
        with patch("fbbatch.new_outlook.shutil.which", return_value=str(alias)):
            self.assertEqual(find_new_outlook_executable(), alias)

    def test_non_windows_launcher_starts_olk(self) -> None:
        executable = Path("/apps/olk")
        with (
            patch("fbbatch.new_outlook.os.name", "posix"),
            patch("fbbatch.new_outlook.subprocess.Popen") as popen,
        ):
            _launch_new_outlook(executable)

        popen.assert_called_once_with([str(executable)], cwd=str(executable.parent))

    def test_main_window_detection_does_not_require_outlook_title_suffix(self) -> None:
        rectangle = Mock()
        rectangle.width.return_value = 1200
        rectangle.height.return_value = 800
        window = Mock()
        window.handle = 321
        window.element_info.class_name = "Outlook Host"
        window.window_text.return_value = "Mail"
        window.rectangle.return_value = rectangle
        window.descendants.return_value = []
        desktop = Mock()
        desktop.windows.return_value = [window]

        selected = _wait_for_main_window(desktop, timeout=0.1)

        self.assertIs(selected, window)

    def test_main_window_prefers_profile_matching_from_account(self) -> None:
        def make_window(handle: int, title: str, width: int):
            rectangle = Mock()
            rectangle.width.return_value = width
            rectangle.height.return_value = 800
            button = Mock()
            button.element_info.control_type = "Button"
            button.element_info.name = "New mail"
            button.window_text.return_value = "New mail"
            button.descendants.return_value = []
            window = Mock()
            window.handle = handle
            window.element_info.class_name = "Outlook Host"
            window.window_text.return_value = title
            window.rectangle.return_value = rectangle
            window.descendants.return_value = [button]
            return window

        falabella = make_window(1, "Inbox - Diego Pavez Verdi - Outlook", 1600)
        oracle = make_window(2, "Mail - Diego Pavez - Outlook", 1200)
        desktop = Mock()
        desktop.windows.return_value = [falabella, oracle]

        selected = _wait_for_main_window(
            desktop,
            timeout=0.1,
            from_account="diego.pavez@oracle.com",
        )

        self.assertIs(selected, oracle)

    def test_headless_new_outlook_is_restarted_before_launch(self) -> None:
        main_window = Mock()
        desktop = Mock()
        with (
            patch(
                "fbbatch.new_outlook._new_outlook_process_ids",
                side_effect=[{101}, {202}],
            ),
            patch("fbbatch.new_outlook._terminate_new_outlook_processes") as terminate,
            patch("fbbatch.new_outlook._launch_new_outlook") as launch,
            patch("fbbatch.new_outlook._wait_for_main_window", return_value=main_window),
            patch("fbbatch.new_outlook.log_outlook_processes"),
        ):
            result = _open_new_outlook_main_window(
                desktop,
                Path("olk.exe"),
                existing_outlook_handles=set(),
            )

        self.assertIs(result, main_window)
        terminate.assert_called_once_with({101}, reason="prelaunch-headless")
        launch.assert_called_once_with(Path("olk.exe"), activation="app-id")

    def test_app_id_launch_without_window_retries_alias(self) -> None:
        main_window = Mock()
        desktop = Mock()
        desktop.windows.return_value = []
        with (
            patch(
                "fbbatch.new_outlook._new_outlook_process_ids",
                side_effect=[set(), {202}],
            ),
            patch("fbbatch.new_outlook._terminate_new_outlook_processes") as terminate,
            patch("fbbatch.new_outlook._launch_new_outlook") as launch,
            patch(
                "fbbatch.new_outlook._wait_for_main_window",
                side_effect=[NewOutlookUnavailable("no window"), main_window],
            ),
            patch("fbbatch.new_outlook.log_outlook_processes"),
        ):
            result = _open_new_outlook_main_window(
                desktop,
                Path("olk.exe"),
                existing_outlook_handles=set(),
            )

        self.assertIs(result, main_window)
        terminate.assert_called_once_with({202}, reason="app-id-no-window")
        self.assertEqual(
            [call.kwargs["activation"] for call in launch.call_args_list],
            ["app-id", "alias"],
        )

    def test_new_mail_waits_until_outlook_finishes_loading(self) -> None:
        button = Mock()
        button.element_info.control_type = "Button"
        button.window_text.return_value = "New mail"
        button.is_enabled.return_value = True
        main_window = Mock()
        main_window.descendants.side_effect = [[], [], [button]]

        _open_new_mail(main_window, Mock(), timeout=1.0, poll_interval=0.0)

        self.assertEqual(main_window.descendants.call_count, 3)
        button.invoke.assert_called_once_with()

    def test_compact_new_split_button_uses_compose_shortcut(self) -> None:
        button = Mock()
        button.element_info.control_type = "SplitButton"
        button.element_info.name = "New"
        button.window_text.return_value = "New"
        button.is_enabled.return_value = True
        main_window = Mock()
        main_window.descendants.return_value = [button]
        keyboard = Mock()

        _open_new_mail(main_window, keyboard, timeout=1.0, poll_interval=0.0)

        main_window.set_focus.assert_called_once_with()
        keyboard.send_keys.assert_called_once_with("^n")
        button.invoke.assert_not_called()

    def test_new_message_button_is_treated_as_direct_compose(self) -> None:
        button = Mock()
        button.element_info.control_type = "Button"
        button.element_info.name = "New message, Ctrl+N"
        button.window_text.return_value = "New message, Ctrl+N"
        button.is_enabled.return_value = True
        main_window = Mock()
        main_window.descendants.return_value = [button]

        _open_new_mail(main_window, Mock(), timeout=1.0, poll_interval=0.0)

        button.invoke.assert_called_once_with()

    def test_from_account_menu_item_can_contain_email_in_child_text(self) -> None:
        from_button = Mock()
        from_button.element_info.control_type = "Button"
        from_button.element_info.automation_id = "compose_FROM"
        from_button.window_text.return_value = "From: external@example.com"
        child = Mock()
        child.element_info.name = "diego.pavez@oracle.com"
        child.window_text.return_value = "diego.pavez@oracle.com"
        child.descendants.return_value = []
        menu_item = Mock()
        menu_item.element_info.control_type = "MenuItem"
        menu_item.element_info.name = "Diego Pavez"
        menu_item.window_text.return_value = "Diego Pavez"
        menu_item.descendants.return_value = [child]
        compose = Mock()
        compose.descendants.return_value = [from_button]
        top_window = Mock()
        top_window.element_info.control_type = "Window"
        top_window.element_info.name = "New mail"
        top_window.window_text.return_value = "New mail"
        top_window.descendants.return_value = [menu_item]
        desktop = Mock()
        desktop.windows.return_value = [top_window]

        _ensure_from_account(desktop, compose, "diego.pavez@oracle.com")

        from_button.click_input.assert_called_once_with()
        menu_item.click_input.assert_called_once_with()

    def test_file_drop_payload_contains_absolute_utf16_paths(self) -> None:
        attachment = ROOT_DIR / "shift" / "event.txt"
        payload = make_file_drop_payload([attachment])

        offset, x, y, non_client, wide = struct.unpack("IiiII", payload[:20])
        decoded = payload[20:].decode("utf-16le")
        self.assertEqual((offset, x, y, non_client, wide), (20, 0, 0, 0, 1))
        self.assertIn(str(attachment.resolve()), decoded)
        self.assertTrue(decoded.endswith("\0\0"))

    def test_recipient_input_uses_inner_named_group(self) -> None:
        inner = Mock()
        inner.element_info.control_type = "Group"
        inner.window_text.return_value = "To"
        well = Mock()
        well.descendants.return_value = [inner]

        self.assertIs(_find_recipient_input(well, "_TO"), inner)

    def test_recipient_confirmation_does_not_depend_on_automation_id(self) -> None:
        chip = Mock()
        chip.element_info.automation_id = "0"
        chip.element_info.name = "Diego Pavez - diego.pavez@oracle.com 1 of 1"
        chip.window_text.return_value = "diego.pavez@oracle.com"
        well = Mock()
        well.descendants.return_value = [chip]

        self.assertTrue(_recipient_is_visible(well, "diego.pavez@oracle.com"))

    def test_unresolved_recipient_text_is_not_counted_as_a_chip(self) -> None:
        raw_input = Mock()
        raw_input.element_info.control_type = "Group"
        raw_input.element_info.automation_id = "recipient-input"
        raw_input.element_info.name = "one@example.com; two@example.com"
        raw_input.window_text.return_value = "one@example.com; two@example.com"
        well = Mock()
        well.descendants.return_value = [raw_input]

        self.assertEqual(_recipient_chip_count(well), 0)

    def test_to_and_cc_rows_are_pasted_with_one_tab_between_them(self) -> None:
        to_chip = Mock()
        to_chip.element_info.control_type = "Group"
        to_chip.element_info.automation_id = "REK000001"
        to_chip.window_text.return_value = "To User"
        cc_chip = Mock()
        cc_chip.element_info.control_type = "Group"
        cc_chip.element_info.automation_id = "REK000002"
        cc_chip.window_text.return_value = "CC User"
        to_well = Mock()
        to_well.element_info.control_type = "Group"
        to_well.element_info.automation_id = "compose_TO"
        to_well.descendants.return_value = [to_chip]
        cc_well = Mock()
        cc_well.element_info.control_type = "Group"
        cc_well.element_info.automation_id = "compose_CC"
        cc_well.descendants.return_value = [cc_chip]
        window = Mock()
        window.descendants.return_value = [to_well, cc_well]
        keyboard = Mock()
        to_text = '"To User" <to@example.com>'
        cc_text = '"CC User" <cc@example.com>'

        with (
            patch("fbbatch.new_outlook._set_clipboard_text") as set_clipboard,
            patch("fbbatch.new_outlook.time.sleep"),
        ):
            _fill_recipient_fields(window, to_text, cc_text, keyboard)

        self.assertEqual(
            [call.args for call in set_clipboard.call_args_list],
            [(to_text,), (cc_text,)],
        )
        self.assertEqual(
            [call.args for call in keyboard.send_keys.call_args_list],
            [("^v",), ("{TAB}",), ("^v",)],
        )

    def test_subject_confirmation_uses_uia_value_when_window_text_is_empty(self) -> None:
        subject = Mock()
        subject.element_info.control_type = "Edit"
        subject.element_info.automation_id = "compose_SUBJECT"
        subject.element_info.name = "Add a subject"
        subject.window_text.return_value = ""
        subject.get_value.return_value = "NSSR: JULIO 8 2026"
        window = Mock()
        window.descendants.return_value = [subject]
        keyboard = Mock()

        with patch("fbbatch.new_outlook._set_clipboard_text"):
            _fill_subject(window, "NSSR: JULIO 8 2026", keyboard)

        subject.set_edit_text.assert_called_once_with("NSSR: JULIO 8 2026")
        keyboard.send_keys.assert_any_call("{TAB}")

    def test_subject_assignment_uses_clipboard_when_uia_value_is_unavailable(self) -> None:
        subject = Mock()
        subject.element_info.control_type = "Edit"
        subject.element_info.automation_id = "compose_SUBJECT"
        subject.element_info.name = "NSSR test"
        subject.window_text.return_value = "NSSR test"
        subject.get_value.return_value = "NSSR test"
        subject.set_edit_text.side_effect = RuntimeError("not supported")
        window = Mock()
        window.descendants.return_value = [subject]
        keyboard = Mock()

        with patch("fbbatch.new_outlook._set_clipboard_text") as set_clipboard:
            _fill_subject(window, "NSSR test", keyboard)

        set_clipboard.assert_called_once_with("NSSR test")
        keyboard.send_keys.assert_any_call("^v")

    def test_control_text_falls_back_to_value_pattern(self) -> None:
        control = Mock()
        control.window_text.return_value = ""
        control.get_value.side_effect = RuntimeError("not supported")
        control.element_info.name = "Add a subject"
        control.iface_value.CurrentValue = "NSSR test"

        self.assertTrue(_control_contains_text(control, "NSSR test"))

    def test_failed_compose_is_closed_without_saving(self) -> None:
        button = Mock()
        button.element_info.control_type = "Button"
        button.window_text.return_value = "No"
        window = Mock()
        window.descendants.return_value = [button]

        self.assertTrue(_discard_failed_compose(window, timeout=0.1))

        window.close.assert_called_once_with()
        button.invoke.assert_called_once_with()

    def test_saved_compose_confirms_close_draft_dialog(self) -> None:
        close_button = Mock()
        close_button.element_info.control_type = "Button"
        close_button.element_info.automation_id = "windowIconClose"
        prompt = Mock()
        prompt.element_info.control_type = "Text"
        prompt.window_text.return_value = "Close draft"
        yes_button = Mock()
        yes_button.element_info.control_type = "Button"
        yes_button.window_text.return_value = "Yes"
        window = Mock()
        window.handle = 123
        window.descendants.side_effect = [[close_button], [prompt, yes_button]]

        with (
            patch("fbbatch.new_outlook._native_window_exists", side_effect=[True, False]),
            patch("fbbatch.new_outlook.time.sleep"),
        ):
            _close_popout_compose(window, timeout=1.0)

        close_button.invoke.assert_called_once_with()
        yes_button.invoke.assert_called_once_with()

    def test_saved_compose_finds_close_dialog_exposed_as_separate_window(self) -> None:
        close_button = Mock()
        close_button.element_info.control_type = "Button"
        close_button.element_info.automation_id = "windowIconClose"
        prompt = Mock()
        prompt.window_text.return_value = "Close draft"
        yes_button = Mock()
        yes_button.element_info.control_type = "Button"
        yes_button.window_text.return_value = "Yes"
        dialog = Mock(handle=456)
        dialog.window_text.return_value = "Close draft"
        dialog.descendants.return_value = [prompt, yes_button]
        desktop = Mock()
        desktop.windows.return_value = [dialog]
        window = Mock(handle=123)
        window.window_text.return_value = "NSSR test"
        window.descendants.side_effect = [[close_button], []]

        with (
            patch("fbbatch.new_outlook._native_window_exists", side_effect=[True, False]),
            patch("fbbatch.new_outlook.time.sleep"),
        ):
            _close_popout_compose(window, desktop=desktop, timeout=1.0)

        yes_button.invoke.assert_called_once_with()

    def test_saved_confirmation_ignores_status_until_settle_period(self) -> None:
        status = Mock()
        status.window_text.return_value = "Draft saved at 8:05 PM"
        window = Mock()
        window.descendants.return_value = [status]

        with (
            patch(
                "fbbatch.new_outlook.time.monotonic",
                side_effect=[0.0, 1.0, 2.0, 3.0, 6.0, 7.0],
            ),
            patch("fbbatch.new_outlook.time.sleep") as sleep,
        ):
            result = _wait_for_saved_confirmation(
                window,
                timeout=10.0,
                minimum_wait=5.0,
            )

        self.assertEqual(result, "Draft saved at 8:05 PM")
        sleep.assert_called_once_with(0.25)

    def test_new_outlook_main_window_started_by_automation_can_be_closed(self) -> None:
        window = Mock()
        window.handle = 321

        _close_started_main_window(window)

        window.close.assert_called_once_with()

    def test_inline_image_is_scaled_to_email_width(self) -> None:
        self.assertEqual(
            _fit_image_size(1346, 1025, max_width=960, max_height=720),
            (945, 720),
        )

    def test_small_inline_image_is_not_upscaled(self) -> None:
        self.assertEqual(
            _fit_image_size(640, 480, max_width=960, max_height=720),
            (640, 480),
        )

    def test_outlook_draft_uses_new_outlook_by_default_even_when_classic_is_installed(self) -> None:
        with (
            patch("fbbatch.runner._find_outlook_executable") as find_classic,
            patch("fbbatch.new_outlook.create_new_outlook_draft") as new_outlook,
            patch("fbbatch.runner._create_classic_outlook_draft") as classic_outlook,
        ):
            result = create_outlook_draft(**self._draft_arguments())

        new_outlook.assert_called_once()
        classic_outlook.assert_not_called()
        find_classic.assert_not_called()
        self.assertEqual(result, OutlookDraftResult(entry_id="new-outlook", folder_name="Drafts"))

    def test_outlook_draft_uses_classic_only_when_selected(self) -> None:
        expected = OutlookDraftResult(entry_id="classic-id", folder_name="Drafts")
        with (
            patch("fbbatch.new_outlook.create_new_outlook_draft") as new_outlook,
            patch("fbbatch.runner._find_outlook_executable", return_value=Path("OUTLOOK.EXE")),
            patch(
                "fbbatch.runner._create_classic_outlook_draft",
                return_value=expected,
            ) as classic_outlook,
        ):
            result = create_outlook_draft(
                **self._draft_arguments(),
                use_classic_outlook=True,
            )

        classic_outlook.assert_called_once()
        new_outlook.assert_not_called()
        self.assertEqual(result, expected)

    def test_selected_classic_route_does_not_fallback_when_classic_is_not_installed(self) -> None:
        with (
            patch("fbbatch.runner._find_outlook_executable", return_value=None),
            patch("fbbatch.new_outlook.create_new_outlook_draft") as new_outlook,
            patch("fbbatch.runner._create_classic_outlook_draft") as classic_outlook,
        ):
            with self.assertRaisesRegex(RuntimeError, "Classic Outlook is not installed"):
                create_outlook_draft(
                    **self._draft_arguments(),
                    use_classic_outlook=True,
                )

        new_outlook.assert_not_called()
        classic_outlook.assert_not_called()

    def test_selected_classic_route_does_not_fallback_when_mapi_is_unavailable(self) -> None:
        with (
            patch("fbbatch.runner._find_outlook_executable", return_value=Path("OUTLOOK.EXE")),
            patch(
                "fbbatch.runner._create_classic_outlook_draft",
                side_effect=ClassicOutlookUnavailable("MAPI unavailable"),
            ) as classic_outlook,
            patch("fbbatch.new_outlook.create_new_outlook_draft") as new_outlook,
        ):
            with self.assertRaisesRegex(RuntimeError, "New Outlook was not opened"):
                create_outlook_draft(
                    **self._draft_arguments(),
                    use_classic_outlook=True,
                )

        classic_outlook.assert_called_once()
        new_outlook.assert_not_called()

    def test_selected_classic_route_does_not_fallback_after_draft_failure(self) -> None:
        with (
            patch("fbbatch.runner._find_outlook_executable", return_value=Path("OUTLOOK.EXE")),
            patch(
                "fbbatch.runner._create_classic_outlook_draft",
                side_effect=RuntimeError("draft save failed"),
            ),
            patch("fbbatch.new_outlook.create_new_outlook_draft") as new_outlook,
        ):
            with self.assertRaisesRegex(RuntimeError, "draft save failed"):
                create_outlook_draft(
                    **self._draft_arguments(),
                    use_classic_outlook=True,
                )

        new_outlook.assert_not_called()

    def test_classic_outlook_is_not_opened_after_selected_new_route_fails(self) -> None:
        with (
            patch("fbbatch.runner._find_outlook_executable") as find_classic,
            patch(
                "fbbatch.new_outlook.create_new_outlook_draft",
                side_effect=NewOutlookAutomationError("controls changed"),
            ),
            patch("fbbatch.runner._create_classic_outlook_draft") as classic_outlook,
        ):
            with self.assertRaisesRegex(RuntimeError, "Classic Outlook was not opened"):
                create_outlook_draft(**self._draft_arguments())

        classic_outlook.assert_not_called()
        find_classic.assert_not_called()


if __name__ == "__main__":
    unittest.main()
