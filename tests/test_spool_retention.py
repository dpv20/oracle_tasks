from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


SRC_DIR = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_DIR))

from infra import spool_retention as retention_module  # noqa: E402
from infra.spool_retention import (  # noqa: E402
    cleanup_extract_archives,
    cleanup_managed_spools,
)


NOW = datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc)


class SpoolRetentionTests(unittest.TestCase):
    def test_default_cleanup_includes_separate_cmr_folder(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cl_root = root / "cl"
            cmr_root = root / "cmr"
            savings_root = root / "savings"
            for folder in (cl_root, cmr_root, savings_root):
                folder.mkdir()

            cmr_spool = cmr_root / "CL_Acc_Spool_123456789012_U01.SQL"
            cmr_archive = cmr_root / "CMR_Spools_Chile_20260601_120000.zip"
            unrelated = cmr_root / "notes.txt"
            for path in (cmr_spool, cmr_archive, unrelated):
                path.write_text("old", encoding="utf-8")
                self._set_age(path, days=45)

            with (
                patch.object(retention_module, "SPOOLS_CL_OUT_DIR", cl_root),
                patch.object(retention_module, "SPOOLS_CMR_OUT_DIR", cmr_root),
                patch.object(retention_module, "SPOOLS_SAVINGS_OUT_DIR", savings_root),
            ):
                result = cleanup_managed_spools(now=NOW)

            self.assertFalse(cmr_spool.exists())
            self.assertFalse(cmr_archive.exists())
            self.assertTrue(unrelated.exists())
            self.assertEqual(result.deleted, 2)

    def test_managed_cleanup_deletes_only_old_recognized_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            nested = root / "Chile"
            nested.mkdir()
            old_spool = nested / "CL_Acc_Spool_229990064720.SQL"
            recent_spool = nested / "CL_Acc_Spool_108108207990.SQL"
            unrelated = root / "creditos mexico.rar"
            old_spool.write_text("old", encoding="utf-8")
            recent_spool.write_text("recent", encoding="utf-8")
            unrelated.write_text("keep", encoding="utf-8")
            self._set_age(old_spool, days=31)
            self._set_age(recent_spool, days=3)
            self._set_age(unrelated, days=90)

            result = cleanup_managed_spools(
                now=NOW,
                rules=((root, ("cl_acc_spool_*.sql",)),),
            )

            self.assertFalse(old_spool.exists())
            self.assertTrue(recent_spool.exists())
            self.assertTrue(unrelated.exists())
            self.assertEqual(result.scanned, 2)
            self.assertEqual(result.deleted, 1)
            self.assertEqual(result.failed, 0)

    def test_managed_cleanup_recognizes_savings_spool_and_apply_log(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            spool = root / "IC_account_data_809800079573.INC"
            apply_log = root / "IC_account_data_809800079573_apply.log"
            spool.write_text("spool", encoding="utf-8")
            apply_log.write_text("log", encoding="utf-8")
            self._set_age(spool, days=45)
            self._set_age(apply_log, days=45)

            result = cleanup_managed_spools(
                now=NOW,
                rules=((root, ("ic_account_data_*.inc", "ic_account_data_*_apply.log")),),
            )

            self.assertFalse(spool.exists())
            self.assertFalse(apply_log.exists())
            self.assertEqual(result.deleted, 2)

    def test_selected_folder_cleanup_only_deletes_matching_old_zip(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            old_archive = root / "CL_Spools_Chile_20260601_120000.zip"
            recent_archive = root / "CL_Spools_Chile_20260801_120000.zip"
            unrelated_archive = root / "personal_backup.zip"
            nested_archive = root / "nested" / "CL_Spools_Chile_20260501_120000.zip"
            nested_archive.parent.mkdir()
            for path in (old_archive, recent_archive, unrelated_archive, nested_archive):
                path.write_bytes(b"zip")
            self._set_age(old_archive, days=40)
            self._set_age(recent_archive, days=2)
            self._set_age(unrelated_archive, days=90)
            self._set_age(nested_archive, days=90)

            result = cleanup_extract_archives(root, ("CL_Spools_",), now=NOW)

            self.assertFalse(old_archive.exists())
            self.assertTrue(recent_archive.exists())
            self.assertTrue(unrelated_archive.exists())
            self.assertTrue(nested_archive.exists())
            self.assertEqual(result.scanned, 2)
            self.assertEqual(result.deleted, 1)

    def test_file_exactly_thirty_days_old_is_kept(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            archive = root / "Savings_Spools_Chile_20260705_120000.zip"
            archive.write_bytes(b"zip")
            self._set_age(archive, days=30)

            cleanup_extract_archives(root, ("Savings_Spools_",), now=NOW)

            self.assertTrue(archive.exists())

    @staticmethod
    def _set_age(path: Path, *, days: int) -> None:
        timestamp = (NOW - timedelta(days=days)).timestamp()
        os.utime(path, (timestamp, timestamp))


if __name__ == "__main__":
    unittest.main()
