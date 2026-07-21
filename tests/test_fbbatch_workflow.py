from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from queue import Queue
from types import SimpleNamespace
from unittest.mock import Mock, patch


ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"
sys.path.insert(0, str(SRC_DIR))

from fbbatch.runner import (  # noqa: E402
    BatchResult,
    JAVA_DEFAULT_IDLE_TIMEOUT_SECONDS,
    JAVA_EVENT_IDLE_TIMEOUT_SECONDS,
    JAVA_MAX_RUNTIME_SECONDS,
    _JavaProgress,
    _accept_outlook_profile_dialog,
    _click_outlook_profile_ok_native,
    _confirm_outlook_profile_dialog,
    _ensure_outlook_window,
    _get_outlook_mapi_namespace,
    _java_idle_timeout_seconds,
    _java_process_label,
    _java_reported_failure,
    _newest_after,
    _parse_historical_event_dates,
    _prepare_historical_event_runtime,
    _redact_process_output_for_log,
    _run_java,
    _snapshot_html_outputs,
    _start_outlook_application,
    run_batch_report,
)
from ui.fbbatch_view import (  # noqa: E402
    _DraftRetryContext,
    FBBatchSetupView,
    _classic_outlook_install_url,
    _discover_draft_retry_context,
    _next_weekday,
    _retry_context_is_valid,
    _scale_phase_progress,
)


class EventProgressTests(unittest.TestCase):
    def test_output_snapshot_never_reuses_an_unchanged_html(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            stale = output_dir / "BatchReport_12-07-2026.html"
            stale.write_text("old", encoding="utf-8")
            snapshot = _snapshot_html_outputs(output_dir)

            self.assertIsNone(_newest_after(output_dir, snapshot))

            stale.write_text("new report contents", encoding="utf-8")
            self.assertEqual(_newest_after(output_dir, snapshot), stale)

    def test_java_success_exit_is_rejected_when_output_reports_db_failure(self) -> None:
        output = (
            "FBConnection Unable to establish a connection to the Oracle database.\n"
            "FBEODBatchTimingApp Exception occurred during processing.\n"
        )

        message = _java_reported_failure(output)

        self.assertIn("could not connect", message)

    def test_batch_report_does_not_reuse_stale_html_when_java_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output_dir = root / "CommonBatches" / "output" / "EODBATCH"
            output_dir.mkdir(parents=True)
            stale = output_dir / "BatchReport_12-07-2026.html"
            stale.write_text("old", encoding="utf-8")
            with (
                patch(
                    "fbbatch.runner.validate_fbbatch_root",
                    return_value=(True, "", root),
                ),
                patch(
                    "fbbatch.runner._run_java",
                    return_value=BatchResult(True, "Completed.", exit_code=0),
                ),
            ):
                result = run_batch_report(
                    "PROD",
                    False,
                    "17072026",
                    False,
                    root,
                )

            self.assertFalse(result.ok)
            self.assertIn("no new HTML output", result.message)
            self.assertIsNone(result.html_path)

    def test_historical_event_dates_require_real_processing_order(self) -> None:
        batch_day, next_day = _parse_historical_event_dates("17072026", "20072026")

        self.assertEqual(batch_day, date(2026, 7, 17))
        self.assertEqual(next_day, date(2026, 7, 20))
        with self.assertRaisesRegex(ValueError, "must be after"):
            _parse_historical_event_dates("20072026", "20072026")

    def test_historical_event_suggests_next_weekday(self) -> None:
        self.assertEqual(_next_weekday(date(2026, 7, 17)), date(2026, 7, 20))
        self.assertEqual(_next_weekday(date(2026, 7, 20)), date(2026, 7, 21))

    def test_standalone_historical_event_uses_automatic_next_weekday(self) -> None:
        view = SimpleNamespace(_event_selected_date=date(2026, 7, 17))

        self.assertEqual(
            FBBatchSetupView._current_event_dates(view),
            ("17072026", "20072026"),
        )

    def test_historical_event_runtime_overrides_only_the_temporary_copy(self) -> None:
        original = (
            "EVENT_NAMES=CUENTAS CASA QUE GENERAN LIQUIDACIÓN\n"
            "OUTPUT_FILE_PATH=output/EODBatchEvent/\n"
            "STTM_DATES_QUERY = SELECT TODAY,PREV_WORKING_DAY FROM sttm_dates\n"
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "source"
            properties = source / "config" / "EODBatchEvent" / "EODBatchEvent.properties"
            properties.parent.mkdir(parents=True)
            properties.write_bytes(original.encode("cp1252"))
            (source / "upload" / "EODBatchEvent" / "Template").mkdir(parents=True)
            runtime = Path(temp_dir) / "runtime"

            _prepare_historical_event_runtime(
                source,
                runtime,
                date(2026, 7, 17),
                date(2026, 7, 20),
            )

            runtime_text = (
                runtime / "config" / "EODBatchEvent" / "EODBatchEvent.properties"
            ).read_text(encoding="cp1252")
            self.assertEqual(properties.read_text(encoding="cp1252"), original)
            self.assertIn("LIQUIDACIÓN", runtime_text)
            self.assertIn("2026-07-17 00:00:00.0", runtime_text)
            self.assertIn("2026-07-20 00:00:00.0", runtime_text)
            self.assertNotIn("TO_TIMESTAMP", runtime_text)
            self.assertIn(
                "SELECT '2026-07-20 00:00:00.0' AS TODAY, "
                "'2026-07-17 00:00:00.0' AS PREV_WORKING_DAY FROM DUAL",
                runtime_text,
            )
            self.assertIn("FROM DUAL", runtime_text)
            self.assertTrue((runtime / "output" / "EODBatchEvent").is_dir())

    def test_manual_full_report_generates_missing_historical_event(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            report_html = output_dir / "BatchReport_17-07-2026.html"
            report_html.write_text("<html>regular batch</html>", encoding="utf-8")
            report_image = output_dir / "summary.png"
            report_image.write_bytes(b"image")
            event_pdf = output_dir / "EODBatchEvent_17-07-2026.pdf"
            event_pdf.write_bytes(b"pdf")
            report_result = BatchResult(
                True,
                "ready",
                html_path=report_html,
                image_paths=[report_image],
                images_dir=output_dir,
                output_dir=output_dir,
            )
            event_result = BatchResult(
                True,
                "ready",
                pdf_path=event_pdf,
                output_dir=output_dir,
            )
            view = SimpleNamespace(_draft_retry_context=None)
            updates: list[tuple[int, str]] = []

            with (
                patch("ui.fbbatch_view.run_batch_report", return_value=report_result),
                patch("ui.fbbatch_view.find_event_pdf_for_report_date", return_value=None),
                patch("ui.fbbatch_view.run_eod_batch_event", return_value=event_result) as run_event,
                patch("ui.fbbatch_view.create_outlook_draft") as create_draft,
            ):
                result = FBBatchSetupView._run_full_report(
                    view,
                    env="PROD",
                    latest=False,
                    report_date="17072026",
                    has_issue=False,
                    root="FBBatchSetup",
                    subject_template="NSSR: {DAY}",
                    from_account="sender@example.com",
                    to="to@example.com",
                    cc="cc@example.com",
                    body_template="Body {DAY}",
                    mail_method="classic",
                    credentials={},
                    progress=lambda percent, message: updates.append((percent, message)),
                )

            self.assertTrue(result.ok)
            self.assertEqual(run_event.call_args.kwargs["latest"], False)
            self.assertEqual(run_event.call_args.kwargs["event_date"], "17072026")
            self.assertEqual(run_event.call_args.kwargs["next_date"], "20072026")
            self.assertEqual(create_draft.call_args.kwargs["attachments"], [event_pdf])
            self.assertEqual(create_draft.call_args.kwargs["inline_images"], [report_image])

    def test_background_worker_queues_progress_and_completion(self) -> None:
        events: Queue[tuple[str, object]] = Queue()
        result = SimpleNamespace(ok=True, message="done")

        FBBatchSetupView._worker(
            lambda progress: (progress(40, "Report: 4/10"), result)[1],
            events,
        )

        self.assertEqual(events.get_nowait(), ("progress", (40, "Report: 4/10")))
        self.assertEqual(events.get_nowait(), ("finish", result))

    def test_background_worker_queues_failure_completion(self) -> None:
        events: Queue[tuple[str, object]] = Queue()

        with patch("ui.fbbatch_view.log.exception"):
            FBBatchSetupView._worker(
                lambda _progress: (_ for _ in ()).throw(RuntimeError("failed")),
                events,
            )

        event_type, result = events.get_nowait()
        self.assertEqual(event_type, "finish")
        self.assertFalse(result.ok)
        self.assertEqual(result.message, "failed")

    def test_background_events_are_applied_by_ui_poller(self) -> None:
        events: Queue[tuple[str, object]] = Queue()
        result = SimpleNamespace(ok=True, message="done")
        events.put(("progress", (40, "Report: 4/10")))
        events.put(("progress", (100, "Ready")))
        events.put(("finish", result))
        view = SimpleNamespace(
            _worker_events=events,
            _set_progress=Mock(),
            _finish=Mock(),
            after=Mock(),
        )

        FBBatchSetupView._poll_worker_events(view, events)

        self.assertEqual(
            view._set_progress.call_args_list,
            [
                unittest.mock.call(40, "Report: 4/10"),
                unittest.mock.call(100, "Ready"),
            ],
        )
        view._finish.assert_called_once_with(result)
        view.after.assert_not_called()
        self.assertIsNone(view._worker_events)

    def test_graph_settings_button_only_shows_for_graph_method(self) -> None:
        view = SimpleNamespace(
            _current_mail_method=Mock(return_value="new"),
            graph_settings_btn=Mock(),
        )

        FBBatchSetupView._sync_mail_method_actions(view)
        view.graph_settings_btn.grid_remove.assert_called_once_with()
        view.graph_settings_btn.grid.assert_not_called()

        view.graph_settings_btn.reset_mock()
        view._current_mail_method.return_value = "graph"
        FBBatchSetupView._sync_mail_method_actions(view)
        view.graph_settings_btn.grid.assert_called_once_with()
        view.graph_settings_btn.grid_remove.assert_not_called()

    def test_classic_outlook_install_page_matches_app_language(self) -> None:
        self.assertIn("/es-es/", _classic_outlook_install_url("es"))
        self.assertIn("/en-us/", _classic_outlook_install_url("en"))
        self.assertIn("/en-us/", _classic_outlook_install_url("unknown"))

    def test_java_output_logging_redacts_common_secret_formats(self) -> None:
        line = "password=Secret123 user=scott connect=scott/tiger@CHILE_QA_19C Event=DEVENGO"

        cleaned = _redact_process_output_for_log(line)

        self.assertNotIn("Secret123", cleaned)
        self.assertNotIn("tiger", cleaned)
        self.assertIn("password=<redacted>", cleaned)
        self.assertIn("scott/<redacted>@CHILE_QA_19C", cleaned)
        self.assertIn("Event=DEVENGO", cleaned)

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

    def test_draft_retry_requires_existing_images_and_attachments(self) -> None:
        image = ROOT_DIR / "shift" / "report_no_issue.txt"
        attachment = ROOT_DIR / "shift" / "event.txt"
        context = _DraftRetryContext(
            report_date="08072026",
            include_event=True,
            attachments=(attachment,),
            inline_images=(image,),
            html_path=None,
            pdf_path=attachment,
            images_dir=None,
            output_dir=None,
        )

        self.assertTrue(_retry_context_is_valid(context))

    def test_draft_retry_rejects_missing_generated_image(self) -> None:
        context = _DraftRetryContext(
            report_date="08072026",
            include_event=False,
            attachments=(),
            inline_images=(ROOT_DIR / "missing-summary.png",),
            html_path=None,
            pdf_path=None,
            images_dir=None,
            output_dir=None,
        )

        self.assertFalse(_retry_context_is_valid(context))

    def test_draft_retry_discovers_outputs_generated_by_separate_actions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "NightShift_08-07-2026"
            output_dir.mkdir()
            (output_dir / "summary.png").write_bytes(b"png")
            (output_dir / "incident_01.png").write_bytes(b"png")
            event_pdf = output_dir / "EODBatchEvent_08-07-2026.pdf"
            event_pdf.write_bytes(b"pdf")

            context, missing = _discover_draft_retry_context(
                "08072026",
                output_root=Path(temp_dir),
            )

        self.assertEqual(missing, "")
        self.assertIsNotNone(context)
        self.assertEqual(len(context.inline_images), 2)
        self.assertEqual(context.attachments, (event_pdf,))

    def test_draft_retry_reports_missing_event_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "NightShift_08-07-2026"
            output_dir.mkdir()
            (output_dir / "summary.png").write_bytes(b"png")

            context, missing = _discover_draft_retry_context(
                "08072026",
                output_root=Path(temp_dir),
            )

        self.assertIsNone(context)
        self.assertEqual(missing, "event")

    def test_draft_retry_allows_weekend_without_event_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "NightShift_05-07-2026"
            output_dir.mkdir()
            (output_dir / "summary.png").write_bytes(b"png")

            context, missing = _discover_draft_retry_context(
                "05072026",
                output_root=Path(temp_dir),
            )

        self.assertEqual(missing, "")
        self.assertIsNotNone(context)
        self.assertFalse(context.include_event)
        self.assertEqual(context.attachments, ())


class OutlookStartupTests(unittest.TestCase):
    def test_profile_dialog_watcher_initializes_com_on_its_thread(self) -> None:
        with (
            patch("pythoncom.CoInitialize") as co_initialize,
            patch("pythoncom.CoUninitialize") as co_uninitialize,
            patch("fbbatch.runner._watch_outlook_profile_dialog", return_value=True) as watcher,
        ):
            result = _confirm_outlook_profile_dialog(
                profile_name="Exchange",
                timeout=5,
            )

        self.assertTrue(result)
        co_initialize.assert_called_once_with()
        watcher.assert_called_once_with(
            profile_name="Exchange",
            timeout=5,
            stop_event=None,
        )
        co_uninitialize.assert_called_once_with()

    def test_mapi_namespace_falls_back_to_session_property(self) -> None:
        namespace = object()
        outlook = SimpleNamespace(
            GetNamespace=Mock(side_effect=AttributeError("GetNamespace")),
            Session=namespace,
        )

        self.assertIs(_get_outlook_mapi_namespace(outlook), namespace)

    def test_outlook_is_started_minimized_with_exchange_profile(self) -> None:
        outlook = object()
        client = Mock()
        client.GetActiveObject.side_effect = [RuntimeError("not running"), outlook]
        executable = Path(r"C:\Program Files\Microsoft Office\root\Office16\OUTLOOK.EXE")

        with (
            patch("fbbatch.runner._find_outlook_executable", return_value=executable),
            patch("fbbatch.runner.subprocess.Popen") as popen,
            patch("fbbatch.runner._start_outlook_profile_dialog_helper") as profile_helper,
            patch("fbbatch.runner._classic_outlook_started_by_app", False),
        ):
            result, started_here = _start_outlook_application(client, timeout=1)

        self.assertIs(result, outlook)
        self.assertTrue(started_here)
        client.Dispatch.assert_not_called()
        self.assertEqual(
            popen.call_args.args[0],
            [str(executable), "/profile", "Exchange"],
        )
        self.assertEqual(popen.call_args.kwargs["cwd"], str(executable.parent))
        self.assertIn("startupinfo", popen.call_args.kwargs)
        self.assertEqual(popen.call_args.kwargs["startupinfo"].wShowWindow, 2)
        self.assertNotIn("creationflags", popen.call_args.kwargs)
        profile_helper.assert_called_once()
        self.assertEqual(profile_helper.call_args.kwargs["profile_name"], "Exchange")
        self.assertEqual(profile_helper.call_args.kwargs["timeout"], 45.0)
        profile_helper.return_value.join.assert_called_once_with(timeout=1.0)

    def test_hidden_outlook_launch_failure_is_reported(self) -> None:
        client = Mock()
        client.GetActiveObject.side_effect = RuntimeError("not running")
        executable = Path(r"C:\Program Files\Microsoft Office\root\Office16\OUTLOOK.EXE")

        with (
            patch("fbbatch.runner._find_outlook_executable", return_value=executable),
            patch("fbbatch.runner.subprocess.Popen", side_effect=OSError("launch failed")),
            patch("fbbatch.runner._start_outlook_profile_dialog_helper") as profile_helper,
            patch("fbbatch.runner._classic_outlook_started_by_app", False),
        ):
            with self.assertRaisesRegex(RuntimeError, "Exchange profile"):
                _start_outlook_application(client, timeout=0)

        client.Dispatch.assert_not_called()
        profile_helper.return_value.join.assert_called_once_with(timeout=1.0)

    def test_classic_startup_timeout_terminates_launched_process(self) -> None:
        client = Mock()
        client.GetActiveObject.side_effect = RuntimeError("not running")
        executable = Path(r"C:\Program Files\Microsoft Office\root\Office16\OUTLOOK.EXE")
        process = Mock(pid=456)
        process.poll.return_value = None

        with (
            patch("fbbatch.runner._find_outlook_executable", return_value=executable),
            patch("fbbatch.runner.subprocess.Popen", return_value=process),
            patch("fbbatch.runner._start_outlook_profile_dialog_helper") as profile_helper,
            patch("fbbatch.runner._classic_outlook_started_by_app", False),
        ):
            with self.assertRaisesRegex(RuntimeError, "did not register"):
                _start_outlook_application(client, timeout=0)

        profile_helper.return_value.join.assert_called_once_with(timeout=1.0)
        process.terminate.assert_called_once_with()
        process.wait.assert_called_once_with(timeout=5.0)

    def test_profile_dialog_selects_exchange_and_accepts(self) -> None:
        combo = Mock()
        combo.element_info.control_type = "ComboBox"
        combo.window_text.return_value = "Other profile"
        button = Mock()
        button.element_info.control_type = "Button"
        button.window_text.return_value = "OK"
        dialog = Mock()
        dialog.descendants.return_value = [combo, button]

        accepted = _accept_outlook_profile_dialog(dialog, "Exchange")

        self.assertTrue(accepted)
        combo.select.assert_called_once_with("Exchange")
        button.invoke.assert_called_once_with()

    def test_profile_dialog_accepts_preselected_exchange(self) -> None:
        combo = Mock()
        combo.element_info.control_type = "ComboBox"
        combo.window_text.return_value = "Exchange"
        button = Mock()
        button.element_info.control_type = "Button"
        button.window_text.return_value = "Aceptar"
        dialog = Mock()
        dialog.descendants.return_value = [combo, button]

        accepted = _accept_outlook_profile_dialog(dialog, "Exchange")

        self.assertTrue(accepted)
        combo.select.assert_not_called()
        button.invoke.assert_called_once_with()

    def test_profile_dialog_can_be_handled_inside_microsoft_parent_window(self) -> None:
        title = Mock()
        title.element_info.control_type = "Text"
        title.window_text.return_value = "Choose Profile"
        combo = Mock()
        combo.element_info.control_type = "ComboBox"
        combo.window_text.return_value = "Exchange"
        button = Mock()
        button.element_info.control_type = "Button"
        button.window_text.return_value = "OK"
        parent = Mock()
        parent.window_text.return_value = "Microsoft"
        parent.descendants.return_value = [title, combo, button]

        self.assertTrue(_accept_outlook_profile_dialog(parent, "Exchange"))
        button.invoke.assert_called_once_with()

    def test_profile_dialog_uses_native_ok_when_uia_hides_the_button(self) -> None:
        dialog = Mock()
        dialog.handle = 100
        dialog.descendants.return_value = []

        with patch(
            "fbbatch.runner._click_outlook_profile_ok_native",
            return_value=True,
        ) as native_ok:
            accepted = _accept_outlook_profile_dialog(dialog, "Exchange")

        self.assertTrue(accepted)
        native_ok.assert_called_once_with(dialog)

    def test_native_profile_confirmation_clicks_standard_idok(self) -> None:
        dialog = SimpleNamespace(handle=100)
        with (
            patch("win32gui.GetDlgItem", return_value=200),
            patch("win32gui.SendMessage") as send_message,
        ):
            accepted = _click_outlook_profile_ok_native(dialog)

        self.assertTrue(accepted)
        self.assertEqual(send_message.call_args.args[0], 200)

    def test_missing_explorer_opens_visible_outlook_window(self) -> None:
        folder = SimpleNamespace(Display=Mock())
        namespace = SimpleNamespace(GetDefaultFolder=Mock(return_value=folder))
        outlook = SimpleNamespace(Explorers=SimpleNamespace(Count=0))

        _ensure_outlook_window(outlook, namespace)

        namespace.GetDefaultFolder.assert_called_once_with(6)
        folder.Display.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
