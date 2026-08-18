from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from spools_cl_accounts.sqlcl import SqlclRunner, _thin_sqlcl_environment


class SqlclEnvironmentTests(unittest.TestCase):
    def test_thin_environment_removes_oracle_home_and_preserves_tns_admin(self):
        with patch.dict(
            os.environ,
            {
                "ORACLE_HOME": r"C:\Oracle\client19",
                "TNS_ADMIN": r"C:\Oracle\network\admin",
            },
            clear=True,
        ):
            env = _thin_sqlcl_environment()

        self.assertNotIn("ORACLE_HOME", env)
        self.assertEqual(env["TNS_ADMIN"], r"C:\Oracle\network\admin")

    def test_thin_environment_reuses_oracle_home_network_admin(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            oracle_home = Path(temp_dir)
            network_admin = oracle_home / "network" / "admin"
            network_admin.mkdir(parents=True)
            with patch.dict(
                os.environ,
                {"Oracle_Home": str(oracle_home)},
                clear=True,
            ):
                env = _thin_sqlcl_environment()

        self.assertFalse(any(key.upper() == "ORACLE_HOME" for key in env))
        self.assertEqual(env["TNS_ADMIN"], str(network_admin))

    @patch("spools_cl_accounts.sqlcl.subprocess.run")
    def test_runner_passes_thin_environment_to_sqlcl(self, run_mock):
        run_mock.return_value.returncode = 0
        run_mock.return_value.stdout = "1\n"
        run_mock.return_value.stderr = ""

        with patch.dict(
            os.environ,
            {
                "ORACLE_HOME": r"C:\Oracle\client19",
                "TNS_ADMIN": r"C:\Oracle\network\admin",
            },
            clear=True,
        ):
            result = SqlclRunner("sql.exe").run_query(
                "user/password@DATABASE",
                "select 1 from dual",
            )

        self.assertTrue(result.ok)
        child_env = run_mock.call_args.kwargs["env"]
        self.assertFalse(any(key.upper() == "ORACLE_HOME" for key in child_env))
        self.assertEqual(child_env["TNS_ADMIN"], r"C:\Oracle\network\admin")


if __name__ == "__main__":
    unittest.main()
