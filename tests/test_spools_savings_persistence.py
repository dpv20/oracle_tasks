from __future__ import annotations

import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


SRC_DIR = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_DIR))

from spools_savings_accounts.spool_savings_engine import (  # noqa: E402
    _VERIFY_TABLE_SPECS,
    SavingsAccountResult,
    SpoolSavingsStatus,
)
from ui.spools_savings_view import SpoolsSavingsView  # noqa: E402


class SavingsPersistenceTests(unittest.TestCase):
    def test_extract_is_persisted_before_apply_starts(self) -> None:
        account = "8000100372141"
        events: list[str] = []
        extract_result = SavingsAccountResult(
            account,
            SpoolSavingsStatus.OK,
            output_path=Path("working") / f"IC_account_data_{account}.INC",
            branch="001",
        )
        apply_result = SavingsAccountResult(
            account,
            SpoolSavingsStatus.VERIFIED,
            output_path=extract_result.output_path,
            branch="001",
        )

        engine = Mock()
        engine.extract_many.return_value = [extract_result]
        engine.apply_many.side_effect = lambda *args, **kwargs: events.append("apply") or [apply_result]

        view = object.__new__(SpoolsSavingsView)
        view._post_ui = Mock(return_value=True)
        view._persist_generated_spools = Mock(
            side_effect=lambda country, results: events.append("persist") or [Path("saved.inc")]
        )
        view._persist_apply_outputs = Mock()

        with patch("ui.spools_savings_view.SpoolSavingsEngine", return_value=engine):
            view._do_run(
                run_id=1,
                country="chile",
                accounts=[account],
                inject_accounts=[account],
                source_connection="source",
                dest_connection="destination",
                sqlcl_path="sql",
                cancel_event=threading.Event(),
                extract_max_workers=1,
                inject_max_workers=1,
                verify_after_apply=True,
                extract_archive_dir=None,
            )

        self.assertLess(events.index("persist"), events.index("apply"))

    def test_failed_apply_still_persists_generated_spool(self) -> None:
        account = "809803574295"
        view = object.__new__(SpoolsSavingsView)

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "working" / f"IC_account_data_{account}.INC"
            destination = root / "saved" / source.name
            source.parent.mkdir()
            source.write_text("generated spool", encoding="utf-8")
            result = SavingsAccountResult(
                account,
                SpoolSavingsStatus.ERROR,
                output_path=source,
                error="injection failed",
            )

            with patch("ui.spools_savings_view.savings_output_path_for", return_value=destination):
                view._persist_apply_outputs("chile", [result])

            self.assertEqual(destination.read_text(encoding="utf-8"), "generated spool")


class SavingsHistoryConditionTests(unittest.TestCase):
    def test_history_template_uses_related_account(self) -> None:
        template_path = SRC_DIR.parent / "spools_savings" / "IC_account_data_falabella_v2.sql"
        history_lines = [
            line for line in template_path.read_text(encoding="utf-8").splitlines()
            if "'ACTB_HISTORY'" in line
        ]

        self.assertEqual(len(history_lines), 2)
        self.assertTrue(all("RELATED_ACCOUNT" in line for line in history_lines))
        self.assertTrue(all("AC_NO" not in line for line in history_lines))

    def test_history_verification_uses_related_account(self) -> None:
        history_spec = next(spec for spec in _VERIFY_TABLE_SPECS if spec[0] == "ACTB_HISTORY")

        self.assertEqual(history_spec, ("ACTB_HISTORY", "actb_history", "related_account", "ac_branch"))


if __name__ == "__main__":
    unittest.main()
