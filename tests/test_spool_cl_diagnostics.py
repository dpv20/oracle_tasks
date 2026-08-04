from __future__ import annotations

import sys
import unittest
from pathlib import Path


SRC_DIR = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_DIR))

from spools_cl_accounts.spool_cl_engine import _sqlcl_failure_message  # noqa: E402
from spools_cl_accounts.sqlcl import RunResult  # noqa: E402


class SqlclFailureMessageTests(unittest.TestCase):
    def test_oracle_error_is_not_hidden_by_java_native_access_warning(self) -> None:
        result = RunResult(
            1,
            "Connection failed\nError Message = ORA-12154: could not resolve connect identifier\n",
            "WARNING: Restricted methods will be blocked in a future release unless native access is enabled\n",
        )

        message = _sqlcl_failure_message(result)

        self.assertIn("ORA-12154", message)
        self.assertNotIn("Restricted methods", message)

    def test_only_java_warning_reports_exit_code(self) -> None:
        result = RunResult(
            1,
            "",
            "WARNING: Restricted methods will be blocked in a future release unless native access is enabled\n",
        )

        self.assertEqual(_sqlcl_failure_message(result), "SQLcl exited with code 1")


if __name__ == "__main__":
    unittest.main()
