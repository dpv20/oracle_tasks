from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"
sys.path.insert(0, str(SRC_DIR))

from fbbatch.runner import (  # noqa: E402
    JAVA_DEFAULT_IDLE_TIMEOUT_SECONDS,
    JAVA_EVENT_IDLE_TIMEOUT_SECONDS,
    JAVA_MAX_RUNTIME_SECONDS,
    _JavaProgress,
    _ensure_outlook_window,
    _java_idle_timeout_seconds,
    _java_process_label,
    _run_java,
    _start_outlook_application,
)
from ui.fbbatch_view import _scale_phase_progress  # noqa: E402


class EventProgressTests(unittest.TestCase):
    def test_java_watchdog_allows_long_running_event(self) -> None:
        self.assertEqual(JAVA_DEFAULT_IDLE_TIMEOUT_SECONDS, 10 * 60)
        self.assertEqual(JAVA_EVENT_IDLE_TIMEOUT_SECONDS, 40 * 60)
        self.assertGreaterEqual(JAVA_MAX_RUNTIME_SECONDS, 90 * 60)
        self.assertEqual(_java_idle_timeout_seconds("event"), 40 * 60)
        self.assertEqual(_java_idle_timeout_seconds("report_no_issue"), 10 * 60)
        self.assertEqual(_java_process_label("event"), "EOD Batch Event")
        self.assertEqual(_java_process_label("report_no_issue"), "EOD Batch Report")

    def test_java_runner_streams_event_output_until_process_finishes(self) -> None:
        process = SimpleNamespace(
            stdin=Mock(),
            stdout=iter(
                [
                    "Event=DEVENGO recordsCount=10\n",
                    "Event=TRANSFERENCIAS DE LINEA DE CREDITO recordsCount=20\n",
                ]
            ),
            poll=Mock(return_value=0),
            wait=Mock(return_value=0),
            kill=Mock(),
        )
        updates: list[tuple[int, str]] = []

        with (
            patch("fbbatch.runner.shutil.which", return_value="java"),
            patch("fbbatch.runner.subprocess.Popen", return_value=process),
        ):
            result = _run_java(
                ROOT_DIR,
                "example.EventApplication",
                "PROD\n",
                progress=lambda percent, message: updates.append((percent, message)),
                progress_kind="event",
            )

        self.assertTrue(result.ok)
        self.assertTrue(any("TRANSFERENCIAS DE LINEA DE CREDITO" in message for _, message in updates))
        self.assertEqual(updates[-1], (90, "Java process completed"))
        process.kill.assert_not_called()

    def test_event_transcript_advances_through_events_and_summary(self) -> None:
        updates: list[tuple[int, str]] = []
        tracker = _JavaProgress("event", lambda percent, message: updates.append((percent, message)))

        for line in (ROOT_DIR / "shift" / "event.txt").read_text(encoding="utf-8").splitlines():
            tracker.update(line)

        event_updates = [item for item in updates if item[1].startswith("Event ")]
        summary_updates = [item for item in updates if item[1].startswith("Building event report ")]
        percentages = [percent for percent, _ in updates]

        self.assertEqual(len(event_updates), 34)
        self.assertEqual(len(summary_updates), 34)
        self.assertIn("TRANSFERENCIAS DE LINEA DE CREDITO", event_updates[23][1])
        self.assertEqual(event_updates[23][0], 65)
        self.assertEqual(_scale_phase_progress(event_updates[23][0], 50, 90), 76)
        self.assertEqual(event_updates[-1][0], 90)
        self.assertEqual(summary_updates[-1][0], 92)
        self.assertEqual(percentages, sorted(percentages))

    def test_event_heartbeat_reports_long_query_without_fake_progress(self) -> None:
        updates: list[tuple[int, str]] = []
        tracker = _JavaProgress("event", lambda percent, message: updates.append((percent, message)))
        tracker.update("Event=TRANSFERENCIAS DE LINEA DE CREDITO recordsCount=61.436")
        checkpoint = updates[-1][0]

        tracker.heartbeat(5 * 60)

        self.assertEqual(updates[-1][0], checkpoint)
        self.assertIn("5 min without a new Event", updates[-1][1])
        self.assertIn("TRANSFERENCIAS DE LINEA DE CREDITO", updates[-1][1])

    def test_full_report_phase_scaling(self) -> None:
        self.assertEqual(_scale_phase_progress(0, 0, 50), 0)
        self.assertEqual(_scale_phase_progress(100, 0, 50), 50)
        self.assertEqual(_scale_phase_progress(0, 50, 90), 50)
        self.assertEqual(_scale_phase_progress(50, 50, 90), 70)
        self.assertEqual(_scale_phase_progress(100, 50, 90), 90)


class OutlookStartupTests(unittest.TestCase):
    def test_outlook_executable_is_started_before_active_object_is_returned(self) -> None:
        outlook = object()
        client = Mock()
        client.GetActiveObject.side_effect = [RuntimeError("not running"), outlook]
        executable = Path(r"C:\Program Files\Microsoft Office\root\Office16\OUTLOOK.EXE")

        with (
            patch("fbbatch.runner._find_outlook_executable", return_value=executable),
            patch("fbbatch.runner.subprocess.Popen") as popen,
        ):
            result = _start_outlook_application(client, timeout=1)

        self.assertIs(result, outlook)
        popen.assert_called_once_with([str(executable)], cwd=str(executable.parent))
        client.Dispatch.assert_not_called()

    def test_missing_explorer_opens_visible_outlook_window(self) -> None:
        folder = SimpleNamespace(Display=Mock())
        namespace = SimpleNamespace(GetDefaultFolder=Mock(return_value=folder))
        outlook = SimpleNamespace(Explorers=SimpleNamespace(Count=0))

        _ensure_outlook_window(outlook, namespace)

        namespace.GetDefaultFolder.assert_called_once_with(6)
        folder.Display.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
