from __future__ import annotations

import sys
import unittest
from pathlib import Path


SRC_DIR = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_DIR))

from settings.config import DEFAULTS, ConfigManager  # noqa: E402
from settings.database_failover import (  # noqa: E402
    DATABASE_FAILOVER_PAIRS,
    DEFAULT_DATABASE_FAILOVER,
    DEFAULT_DATABASE_PREFERENCES,
    alternative_alias,
    normalize_database_preferences,
)
from spools_cl_accounts.database_failover import (  # noqa: E402
    ConnectionCandidate,
    FailoverSqlclRunner,
    connection_failure_reason,
    fallback_candidates_for_source,
)
from spools_cl_accounts.sqlcl import RunResult  # noqa: E402


class ExplicitDatabasePairTests(unittest.TestCase):
    def test_only_the_two_approved_pairs_exist(self) -> None:
        pairs = {
            (pair.country, frozenset(alias.upper() for alias in pair.aliases))
            for pair in DATABASE_FAILOVER_PAIRS
        }

        self.assertEqual(
            pairs,
            {
                (
                    "chile",
                    frozenset({"FXBFCL_19C_PROD_OCI", "FXBFCL_19C_PROD_OCI_DR"}),
                ),
                (
                    "colombia",
                    frozenset({"BFCO_POCISANTIAGO", "BFCO_POCISAOPALO"}),
                ),
            },
        )

    def test_pairs_work_in_both_directions(self) -> None:
        flags = dict(DEFAULT_DATABASE_FAILOVER)

        self.assertEqual(
            alternative_alias("chile", "fxbfcl_19c_prod_oci", flags),
            "FXBFCL_19C_PROD_OCI_DR",
        )
        self.assertEqual(
            alternative_alias("chile", "FXBFCL_19C_PROD_OCI_DR", flags),
            "FXBFCL_19C_PROD_OCI",
        )
        self.assertEqual(
            alternative_alias("colombia", "bfco_pocisantiago", flags),
            "BFCO_POCISAOPALO",
        )
        self.assertEqual(
            alternative_alias("colombia", "BFCO_POCISAOPALO", flags),
            "BFCO_POCISANTIAGO",
        )

    def test_unlisted_or_disabled_databases_never_fail_over(self) -> None:
        self.assertIsNone(
            alternative_alias("mexico", "MX_PROD_OCI", DEFAULT_DATABASE_FAILOVER)
        )

    def test_preferences_accept_only_aliases_from_the_explicit_pairs(self) -> None:
        self.assertEqual(
            normalize_database_preferences(
                {
                    "chile": "fxbfcl_19c_prod_oci_dr",
                    "colombia": "not_an_approved_alias",
                }
            ),
            {
                "chile": "FXBFCL_19C_PROD_OCI_DR",
                "colombia": "BFCO_POCISANTIAGO",
            },
        )
        self.assertEqual(
            normalize_database_preferences(None),
            {
                "chile": "FXBFCL_19C_PROD_OCI_DR",
                "colombia": "BFCO_POCISANTIAGO",
            },
        )
        self.assertIsNone(
            alternative_alias(
                "chile",
                "FXBFCL_19C_PROD_OCI",
                {"chile_prod_oci": False},
            )
        )


class ConnectionFailureClassificationTests(unittest.TestCase):
    def test_oracle_listener_errors_are_retryable(self) -> None:
        result = RunResult(
            1,
            "",
            "ORA-12514: TNS:listener does not currently know of service requested",
        )

        self.assertEqual(connection_failure_reason(result), "ORA-12514")

    def test_network_adapter_errors_are_retryable(self) -> None:
        result = RunResult(1, "IO Error: The Network Adapter could not establish", "")

        self.assertEqual(connection_failure_reason(result), "Oracle connection error")

    def test_functional_auth_and_cancel_errors_are_not_retryable(self) -> None:
        self.assertIsNone(connection_failure_reason(RunResult(1, "ORA-00942", "")))
        self.assertIsNone(connection_failure_reason(RunResult(1, "ORA-01017", "")))
        self.assertIsNone(connection_failure_reason(RunResult(130, "ORA-12514", "Cancelled")))

    def test_timeout_is_not_retryable_even_if_output_mentions_connectivity(self) -> None:
        self.assertIsNone(
            connection_failure_reason(
                RunResult(124, "ORA-12514", "Timed out after 120s")
            )
        )


class _FakeRunner:
    exe = "sql.exe"

    def __init__(self, results: list[RunResult]) -> None:
        self.results = list(results)
        self.calls: list[tuple[str, str]] = []

    def _result(self) -> RunResult:
        if not self.results:
            raise AssertionError("Unexpected SQLcl call")
        return self.results.pop(0)

    def run_query(self, connection, sql, timeout=30.0, cancel_event=None):
        self.calls.append(("query", connection))
        return self._result()

    def run_script(
        self,
        connection,
        script_path,
        args=None,
        timeout=None,
        cancel_event=None,
    ):
        self.calls.append(("script", connection))
        return self._result()


class FailoverSqlclRunnerTests(unittest.TestCase):
    @staticmethod
    def _runner(results: list[RunResult]) -> tuple[FailoverSqlclRunner, _FakeRunner]:
        fake = _FakeRunner(results)
        runner = FailoverSqlclRunner(
            fake,
            ConnectionCandidate("PRIMARY", "primary-secret"),
            (ConnectionCandidate("SECONDARY", "secondary-secret"),),
        )
        return runner, fake

    def test_query_retries_connection_error_and_remembers_working_alias(self) -> None:
        runner, fake = self._runner(
            [
                RunResult(1, "ORA-12514", ""),
                RunResult(0, "first result", ""),
                RunResult(0, "second result", ""),
            ]
        )

        first = runner.run_query("primary-secret", "select 1 from dual")
        second = runner.run_query("primary-secret", "select 2 from dual")

        self.assertTrue(first.ok)
        self.assertTrue(second.ok)
        self.assertEqual(
            fake.calls,
            [
                ("query", "primary-secret"),
                ("query", "secondary-secret"),
                ("query", "secondary-secret"),
            ],
        )

    def test_script_retries_connection_error(self) -> None:
        runner, fake = self._runner(
            [RunResult(1, "", "ORA-03113"), RunResult(0, "done", "")]
        )

        result = runner.run_script("primary-secret", "extract.sql")

        self.assertTrue(result.ok)
        self.assertEqual(
            fake.calls,
            [("script", "primary-secret"), ("script", "secondary-secret")],
        )

    def test_functional_error_does_not_retry(self) -> None:
        runner, fake = self._runner([RunResult(1, "ORA-00942", "")])

        result = runner.run_query("primary-secret", "select * from missing")

        self.assertFalse(result.ok)
        self.assertEqual(fake.calls, [("query", "primary-secret")])

    def test_timeout_does_not_retry(self) -> None:
        runner, fake = self._runner(
            [RunResult(124, "ORA-12514", "Timed out after 120s")]
        )

        result = runner.run_query("primary-secret", "select 1 from dual")

        self.assertFalse(result.ok)
        self.assertEqual(result.exit_code, 124)
        self.assertEqual(fake.calls, [("query", "primary-secret")])

    def test_cancel_does_not_retry(self) -> None:
        runner, fake = self._runner(
            [RunResult(130, "ORA-12514", "Cancelled by user")]
        )

        result = runner.run_query("primary-secret", "select 1 from dual")

        self.assertFalse(result.ok)
        self.assertEqual(result.exit_code, 130)
        self.assertEqual(fake.calls, [("query", "primary-secret")])

    def test_destination_connection_is_never_redirected(self) -> None:
        runner, fake = self._runner([RunResult(0, "done", "")])

        result = runner.run_script("destination-secret", "apply.sql")

        self.assertTrue(result.ok)
        self.assertEqual(fake.calls, [("script", "destination-secret")])


class _FakeConfig:
    def __init__(self, credentials: dict, flags: dict[str, bool] | None = None) -> None:
        self._credentials = credentials
        self._flags = flags or dict(DEFAULT_DATABASE_FAILOVER)

    def get(self, key, default=None):
        return self._flags if key == "database_failover" else default

    def all_credentials(self):
        return self._credentials

    def get_credential(self, country, database_key, credential_key=None):
        by_login = self._credentials.get(country, {}).get(database_key.upper(), {})
        if credential_key:
            return by_login.get(credential_key.upper())
        return next(iter(by_login.values()), None)


class FailoverCredentialResolutionTests(unittest.TestCase):
    def test_resolves_saved_prod_credential_for_exact_alternative(self) -> None:
        config = _FakeConfig(
            {
                "chile": {
                    "DR_STORE": {
                        "TEAM": {
                            "user": "team",
                            "password": "hidden",
                            "tns": "FXBFCL_19C_PROD_OCI_DR",
                            "bucket": "shared_prod",
                        }
                    }
                }
            }
        )
        source = {
            "id": "FXBFCL_19C_PROD_OCI",
            "database_key": "PRIMARY_STORE",
            "credential_key": "TEAM",
        }

        candidates = fallback_candidates_for_source(
            config,
            "chile",
            source,
            lambda credential, alias: f"{credential['user']}@{alias}",
        )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].alias, "FXBFCL_19C_PROD_OCI_DR")
        self.assertEqual(candidates[0].connection, "team@FXBFCL_19C_PROD_OCI_DR")

    def test_missing_alternative_credential_disables_runtime_fallback(self) -> None:
        candidates = fallback_candidates_for_source(
            _FakeConfig({"chile": {}}),
            "chile",
            {"id": "FXBFCL_19C_PROD_OCI", "credential_key": "TEAM"},
            lambda credential, alias: "unused",
        )

        self.assertEqual(candidates, ())


class ConfigMigrationTests(unittest.TestCase):
    def test_old_config_gets_both_pairs_enabled_by_default(self) -> None:
        config = object.__new__(ConfigManager)

        merged = config._merge_defaults({"version": 8, "database_failover": "invalid"})

        self.assertEqual(merged["database_failover"], DEFAULT_DATABASE_FAILOVER)
        self.assertEqual(
            merged["fbbatch_preferred_databases"],
            DEFAULT_DATABASE_PREFERENCES,
        )
        self.assertEqual(merged["version"], DEFAULTS["version"])


if __name__ == "__main__":
    unittest.main()
