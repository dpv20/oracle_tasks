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
    _control_contains_text,
    _discard_failed_compose,
    _fill_recipient_fields,
    _fill_subject,
    _fit_image_size,
    _find_recipient_input,
    _launch_new_outlook,
    _open_new_mail,
    _recipient_chip_count,
    _recipient_is_visible,
    _split_recipients,
    find_new_outlook_executable,
    make_file_drop_payload,
)
from fbbatch.runner import OutlookDraftResult, create_outlook_draft  # noqa: E402


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

    def test_outlook_draft_prefers_classic_outlook_when_installed(self) -> None:
        expected = OutlookDraftResult(entry_id="classic-id", folder_name="Drafts")
        with (
            patch("fbbatch.new_outlook.create_new_outlook_draft") as new_outlook,
            patch("fbbatch.runner._find_outlook_executable", return_value=Path("OUTLOOK.EXE")),
            patch(
                "fbbatch.runner._create_classic_outlook_draft",
                return_value=expected,
            ) as classic_outlook,
        ):
            result = create_outlook_draft(**self._draft_arguments())

        classic_outlook.assert_called_once()
        new_outlook.assert_not_called()
        self.assertEqual(result, expected)

    def test_new_outlook_is_used_when_classic_outlook_is_not_installed(self) -> None:
        with (
            patch("fbbatch.runner._find_outlook_executable", return_value=None),
            patch("fbbatch.new_outlook.create_new_outlook_draft") as new_outlook,
            patch("fbbatch.runner._create_classic_outlook_draft") as classic_outlook,
        ):
            result = create_outlook_draft(**self._draft_arguments())

        new_outlook.assert_called_once()
        classic_outlook.assert_not_called()
        self.assertEqual(result, OutlookDraftResult(entry_id="new-outlook", folder_name="Drafts"))

    def test_classic_outlook_is_not_opened_after_new_outlook_started_a_draft(self) -> None:
        with (
            patch("fbbatch.runner._find_outlook_executable", return_value=None),
            patch(
                "fbbatch.new_outlook.create_new_outlook_draft",
                side_effect=NewOutlookAutomationError("controls changed"),
            ),
            patch("fbbatch.runner._create_classic_outlook_draft") as classic_outlook,
        ):
            with self.assertRaisesRegex(RuntimeError, "avoid creating a duplicate"):
                create_outlook_draft(**self._draft_arguments())

        classic_outlook.assert_not_called()


if __name__ == "__main__":
    unittest.main()
