from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock


ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"
sys.path.insert(0, str(SRC_DIR))

from fbbatch.outlook_diagnostics import (  # noqa: E402
    describe_com_object,
    describe_mapi_namespace,
    log_outlook_uia_windows,
)


class OutlookDiagnosticsTests(unittest.TestCase):
    def test_com_summary_includes_safe_version_metadata(self) -> None:
        outlook = SimpleNamespace(
            _username_="Outlook.Application",
            Name="Outlook",
            Version="16.0",
            ProductCode="test-product",
        )

        summary = describe_com_object(outlook)

        self.assertIn("Outlook.Application", summary)
        self.assertIn("Version='16.0'", summary)

    def test_namespace_summary_includes_profile_and_collection_counts(self) -> None:
        namespace = SimpleNamespace(
            Name="MAPI",
            CurrentProfileName="Exchange",
            ExchangeConnectionMode=700,
            Offline=False,
            Accounts=SimpleNamespace(Count=2),
            Stores=SimpleNamespace(Count=3),
        )

        summary = describe_mapi_namespace(namespace, include_collections=True)

        self.assertIn("CurrentProfileName='Exchange'", summary)
        self.assertIn("Accounts.Count='2'", summary)
        self.assertIn("Stores.Count='3'", summary)

    def test_namespace_summary_does_not_touch_collections_before_logon(self) -> None:
        class NamespaceBeforeLogon:
            Name = "MAPI"
            CurrentProfileName = ""
            ExchangeConnectionMode = 0
            Offline = True

            @property
            def Accounts(self):
                raise AssertionError("Accounts must not be read before Logon")

            @property
            def Stores(self):
                raise AssertionError("Stores must not be read before Logon")

        summary = describe_mapi_namespace(NamespaceBeforeLogon())

        self.assertIn("Name='MAPI'", summary)
        self.assertNotIn("Accounts", summary)

    def test_uia_inventory_ignores_unrelated_window_titles(self) -> None:
        unrelated = Mock()
        unrelated.window_text.return_value = "Private unrelated document"
        unrelated.element_info.class_name = "EditorWindow"
        outlook = Mock()
        outlook.window_text.return_value = "Inbox - Outlook"
        outlook.element_info.class_name = "Outlook Host"
        outlook.handle = 123
        outlook.descendants.return_value = []
        outlook.is_visible.return_value = True
        outlook.is_enabled.return_value = True
        desktop = Mock()
        desktop.windows.return_value = [unrelated, outlook]
        logger = Mock()

        log_outlook_uia_windows(logger, desktop, stage="test")

        combined = " ".join(str(call) for call in logger.info.call_args_list)
        self.assertIn("Inbox - Outlook", combined)
        self.assertNotIn("Private unrelated document", combined)


if __name__ == "__main__":
    unittest.main()
