from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from spools_cl_accounts.sqlcl import (
    SqlclRunner,
    _force_jdbc_thin,
    _thin_sqlcl_environment,
)


class SqlclThinConnectionTests(unittest.TestCase):
    def test_tns_alias_is_forced_to_jdbc_thin(self):
        self.assertEqual(
            _force_jdbc_thin("user/password@DATABASE"),
            "user/password@jdbc:oracle:thin:@DATABASE",
        )

    def test_proxy_and_at_sign_in_password_are_preserved(self):
        self.assertEqual(
            _force_jdbc_thin("proxy[schema]/pass@word@DATABASE"),
            "proxy[schema]/pass@word@jdbc:oracle:thin:@DATABASE",
        )

    def test_existing_thin_url_is_not_changed(self):
        connection = "user/password@jdbc:oracle:thin:@DATABASE"
        self.assertEqual(_force_jdbc_thin(connection), connection)

    def test_unrecognized_connection_is_left_unchanged(self):
        self.assertEqual(_force_jdbc_thin("/nolog"), "/nolog")


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
        self.assertEqual(
            run_mock.call_args.args[0][3],
            "user/password@jdbc:oracle:thin:@DATABASE",
        )

    @patch("spools_cl_accounts.sqlcl.subprocess.run")
    def test_script_with_args_uses_explicit_jdbc_thin(self, run_mock):
        run_mock.return_value.returncode = 0
        run_mock.return_value.stdout = ""
        run_mock.return_value.stderr = ""

        result = SqlclRunner("sql.exe").run_script(
            "user/password@DATABASE",
            "extract.sql",
            args=["123"],
        )

        self.assertTrue(result.ok)
        self.assertEqual(
            run_mock.call_args.args[0][3],
            "user/password@jdbc:oracle:thin:@DATABASE",
        )


if __name__ == "__main__":
    unittest.main()
