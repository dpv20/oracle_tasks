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
    _fit_image_size,
    _launch_new_outlook,
    _open_new_mail,
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

    def test_outlook_draft_prefers_new_outlook(self) -> None:
        with (
            patch("fbbatch.new_outlook.create_new_outlook_draft") as new_outlook,
            patch("fbbatch.runner._create_classic_outlook_draft") as classic_outlook,
        ):
            result = create_outlook_draft(**self._draft_arguments())

        new_outlook.assert_called_once()
        classic_outlook.assert_not_called()
        self.assertEqual(result, OutlookDraftResult(entry_id="new-outlook", folder_name="Drafts"))

    def test_classic_outlook_is_fallback_when_new_outlook_is_unavailable(self) -> None:
        expected = OutlookDraftResult(entry_id="classic-id", folder_name="Drafts")
        with (
            patch(
                "fbbatch.new_outlook.create_new_outlook_draft",
                side_effect=NewOutlookUnavailable("not installed"),
            ),
            patch("fbbatch.runner._create_classic_outlook_draft", return_value=expected) as classic_outlook,
        ):
            result = create_outlook_draft(**self._draft_arguments())

        classic_outlook.assert_called_once()
        self.assertEqual(result, expected)

    def test_classic_outlook_is_fallback_when_new_outlook_automation_fails(self) -> None:
        expected = OutlookDraftResult(entry_id="classic-id", folder_name="Drafts")
        with (
            patch(
                "fbbatch.new_outlook.create_new_outlook_draft",
                side_effect=NewOutlookAutomationError("controls changed"),
            ),
            patch("fbbatch.runner._create_classic_outlook_draft", return_value=expected) as classic_outlook,
        ):
            result = create_outlook_draft(**self._draft_arguments())

        classic_outlook.assert_called_once()
        self.assertEqual(result, expected)


if __name__ == "__main__":
    unittest.main()
