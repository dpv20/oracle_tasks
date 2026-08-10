from __future__ import annotations

import queue
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, call, patch


SRC_DIR = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_DIR))

from features.output_file_generation.view import (  # noqa: E402
    OutputFileGenerationView,
    _TARGET_COUNTRIES,
)
from features.output_file_generation.models import (  # noqa: E402
    GenerationRequest,
    OracleTarget,
)
from features.output_file_generation.service import (  # noqa: E402
    OutputFileGenerationError,
)


class _FakeVar:
    def __init__(self, value: object) -> None:
        self.value = value

    def get(self):
        return self.value

    def set(self, value: object) -> None:
        self.value = value


def _target(
    country: str,
    *,
    tns: str | None = None,
    database_key: str | None = None,
    credential_key: str = "SHARED-PROD",
) -> dict[str, object]:
    alias = tns or f"{country.upper()}_PROD"
    return {
        "country": country,
        "env": "prod",
        "id": alias,
        "label": "Shared PROD",
        "database_key": database_key or f"{country.upper()}-PROD-DB",
        "credential_key": credential_key,
        "credential_label": "batch_user",
        "credential_count": 1,
    }


class OutputFileGenerationViewTests(unittest.TestCase):
    def _bare_view(self) -> OutputFileGenerationView:
        view = object.__new__(OutputFileGenerationView)
        view._db_lookup = {}
        view.db_var = _FakeVar("—")
        view.db_menu = Mock()
        view.status_label = Mock()
        view.status_label.cget.return_value = "ready"
        return view

    def test_refresh_targets_combines_all_supported_countries(self) -> None:
        view = self._bare_view()
        rows = {country: [_target(country)] for country in _TARGET_COUNTRIES}
        view.service = Mock()
        view.service.targets.side_effect = lambda country: rows[country]

        view._refresh_targets()

        self.assertEqual(
            view.service.targets.call_args_list,
            [call(country) for country in _TARGET_COUNTRIES],
        )
        self.assertEqual(len(view._db_lookup), 4)
        self.assertEqual(
            {str(target["country"]) for target in view._db_lookup.values()},
            set(_TARGET_COUNTRIES),
        )
        for country, display_name in (
            ("chile", "Chile"),
            ("peru", "Peru"),
            ("colombia", "Colombia"),
            ("mexico", "Mexico"),
        ):
            self.assertTrue(
                any(
                    display_name in label
                    and str(target["country"]) == country
                    and str(target["id"]) in label
                    for label, target in view._db_lookup.items()
                )
            )

    def test_database_labels_disambiguate_country_and_database(self) -> None:
        chile = _target("chile", tns="SHARED_PROD")
        colombia = _target("colombia", tns="SHARED_PROD")

        chile_label = OutputFileGenerationView._database_label(chile)
        colombia_label = OutputFileGenerationView._database_label(colombia)

        self.assertNotEqual(chile_label, colombia_label)
        self.assertIn("Chile", chile_label)
        self.assertIn("Colombia", colombia_label)
        self.assertIn("SHARED_PROD", chile_label)
        self.assertIn("SHARED_PROD", colombia_label)

    def test_matching_database_label_preserves_exact_country(self) -> None:
        view = self._bare_view()
        chile = _target("chile", tns="SHARED_PROD", database_key="PROD-DB")
        colombia = _target(
            "colombia",
            tns="SHARED_PROD",
            database_key="PROD-DB",
        )
        chile_label = OutputFileGenerationView._database_label(chile)
        colombia_label = OutputFileGenerationView._database_label(colombia)
        view._db_lookup = {
            colombia_label: colombia,
            chile_label: chile,
        }

        self.assertEqual(view._matching_database_label(chile), chile_label)
        self.assertEqual(view._matching_database_label(colombia), colombia_label)

    def test_start_builds_target_with_selected_country(self) -> None:
        view = self._bare_view()
        database = _target(
            "colombia",
            tns="BFCO_POCISANTIAGO",
            database_key="CO-PROD-DB",
            credential_key="CO-SHARED-PROD",
        )
        view._db_lookup = {"selected": database}
        view.db_var.set("selected")
        view._running = False
        view.process_ref_entry = Mock()
        view.process_ref_entry.get.return_value = "5328716"
        view.overwrite_var = _FakeVar(True)
        view._set_controls_running = Mock()
        view._set_preview = Mock()
        view.progress = Mock()
        view.after = Mock()
        view._worker = Mock()

        with patch(
            "features.output_file_generation.view.threading.Thread"
        ) as thread_class:
            view._start()

        request, cancel_event = thread_class.call_args.kwargs["args"]
        self.assertEqual(request.target.country, "colombia")
        self.assertEqual(request.target.database_key, "CO-PROD-DB")
        self.assertEqual(request.target.credential_key, "CO-SHARED-PROD")
        self.assertEqual(request.target.tns, "BFCO_POCISANTIAGO")
        self.assertEqual(request.process_ref_no, "5328716")
        self.assertTrue(request.overwrite)
        self.assertIs(cancel_event, view._cancel_event)
        thread_class.return_value.start.assert_called_once_with()

    def test_open_folder_uses_selected_country(self) -> None:
        view = self._bare_view()
        view._db_lookup = {"selected": _target("colombia")}
        view.db_var.set("selected")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with (
                patch(
                    "features.output_file_generation.view.OUTPUT_FILES_OUT_DIR",
                    root,
                ),
                patch(
                    "features.output_file_generation.view.os.startfile",
                    create=True,
                ) as startfile,
            ):
                view._open_folder()

            expected = root / "Colombia"
            self.assertTrue(expected.is_dir())
            startfile.assert_called_once_with(str(expected))

    def test_open_folder_keeps_the_last_successful_result_country(self) -> None:
        view = self._bare_view()
        view._db_lookup = {"selected": _target("chile")}
        view.db_var.set("selected")

        with tempfile.TemporaryDirectory() as temp_dir:
            expected = Path(temp_dir) / "Colombia"
            view._last_output_folder = expected
            with patch(
                "features.output_file_generation.view.os.startfile",
                create=True,
            ) as startfile:
                view._open_folder()

            self.assertTrue(expected.is_dir())
            startfile.assert_called_once_with(str(expected))

    def test_expected_error_is_logged_with_safe_request_metadata(self) -> None:
        view = self._bare_view()
        view._events = queue.Queue()
        view.service = Mock()
        view.service.generate.side_effect = OutputFileGenerationError(
            "batch_user[proxy]/very-secret@BFCO_POCISANTIAGO "
            "ORA-01017: invalid login\nretry the request"
        )
        request = GenerationRequest(
            target=OracleTarget(
                country="colombia",
                database_key="CO-PROD-DB",
                credential_key="CO-SHARED-PROD",
                tns="BFCO_POCISANTIAGO",
                label="Colombia PROD Santiago",
            ),
            process_ref_no="5328716",
        )

        with self.assertLogs(
            "features.output_file_generation.view",
            level="WARNING",
        ) as captured:
            view._worker(request, Mock())

        log_text = "\n".join(captured.output)
        self.assertIn("country=colombia", log_text)
        self.assertIn("tns=BFCO_POCISANTIAGO", log_text)
        self.assertIn("process_ref=5328716", log_text)
        self.assertIn("[connection redacted]", log_text)
        self.assertIn("ORA-01017: invalid login retry the request", log_text)
        self.assertNotIn("very-secret", log_text)
        self.assertNotIn("CO-SHARED-PROD", log_text)
        self.assertEqual(
            view._events.get_nowait(),
            (
                "error",
                "batch_user[proxy]/very-secret@BFCO_POCISANTIAGO "
                "ORA-01017: invalid login\nretry the request",
            ),
        )


if __name__ == "__main__":
    unittest.main()
