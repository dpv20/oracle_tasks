from __future__ import annotations

import sys
import unittest
from pathlib import Path


SRC_DIR = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_DIR))

from settings.credentials import parse  # noqa: E402
from spools_cl_accounts.databases import (  # noqa: E402
    configured_databases,
    databases_for,
    find_db,
)


class ChileQaAliasTests(unittest.TestCase):
    def test_catalog_supports_both_chile_qa_19c_aliases(self) -> None:
        ids = {db["id"] for db in databases_for("chile", "qa")}

        self.assertIn("CHILE_QA_19C", ids)
        self.assertIn("FXBFCL_19C_QA", ids)

    def test_fxbfcl_alias_is_classified_as_chile_qa(self) -> None:
        db = find_db("fxbfcl_19c_qa")
        credential = parse("user[proxy]/password@FXBFCL_19C_QA")

        self.assertIsNotNone(db)
        self.assertEqual(db["country"], "chile")
        self.assertEqual(db["env"], "qa")
        self.assertIsNotNone(credential)
        self.assertEqual(credential.country, "chile")
        self.assertEqual(credential.bucket, "user_qa")


class ConfiguredDatabaseTests(unittest.TestCase):
    def test_only_saved_aliases_are_returned(self) -> None:
        credentials = {
            "chile": {
                "TEAM_PROD": {
                    "USER": {
                        "user": "user",
                        "schema": "",
                        "tns": "TEAM_PROD",
                        "bucket": "shared_prod",
                    },
                },
                "TEAM_QA": {
                    "USER[PROXY]": {
                        "user": "user",
                        "schema": "proxy",
                        "tns": "TEAM_QA",
                        "bucket": "user_qa",
                    },
                },
            },
        }

        source = configured_databases(credentials, "chile", ("prod", "qa", "dev"))
        destination = configured_databases(credentials, "chile", ("qa", "dev"))

        self.assertEqual([db["id"] for db in source], ["TEAM_PROD", "TEAM_QA"])
        self.assertEqual([db["id"] for db in destination], ["TEAM_QA"])
        self.assertNotIn("CHILE_QA_19C", {db["id"] for db in source})

    def test_multiple_logins_for_one_alias_remain_selectable(self) -> None:
        credentials = {
            "chile": {
                "TEAM_QA": {
                    "FIRST[ONE]": {
                        "user": "first",
                        "schema": "one",
                        "tns": "TEAM_QA",
                        "bucket": "user_qa",
                    },
                    "SECOND[TWO]": {
                        "user": "second",
                        "schema": "two",
                        "tns": "TEAM_QA",
                        "bucket": "user_qa",
                    },
                },
            },
        }

        entries = configured_databases(credentials, "chile", ("qa",))

        self.assertEqual(len(entries), 2)
        self.assertEqual({entry["credential_count"] for entry in entries}, {2})
        self.assertEqual(
            {entry["credential_key"] for entry in entries},
            {"FIRST[ONE]", "SECOND[TWO]"},
        )

    def test_saved_storage_key_is_preserved_when_tns_is_different(self) -> None:
        credentials = {
            "chile": {
                "LEGACY_QA_KEY": {
                    "USER": {
                        "user": "user",
                        "schema": "",
                        "tns": "TEAM_QA",
                        "bucket": "user_qa",
                    },
                },
            },
        }

        entries = configured_databases(credentials, "chile", ("qa",))

        self.assertEqual(entries[0]["id"], "TEAM_QA")
        self.assertEqual(entries[0]["database_key"], "LEGACY_QA_KEY")


if __name__ == "__main__":
    unittest.main()
