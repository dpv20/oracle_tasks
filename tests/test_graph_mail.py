from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image


ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"
sys.path.insert(0, str(SRC_DIR))

from fbbatch.graph_mail import (  # noqa: E402
    GRAPH_ORGANIZATIONS_TENANT,
    GRAPH_POWERSHELL_CLIENT_ID,
    GraphAuthenticationRequired,
    GraphDeviceCode,
    GraphDraftResult,
    GraphIdentity,
    GraphMailClient,
    GraphMailError,
    _build_html_body,
    split_recipients,
)
from fbbatch.runner import create_outlook_draft  # noqa: E402


class _Response:
    def __init__(self, payload: dict, status_code: int = 201) -> None:
        self._payload = payload
        self.status_code = status_code
        self.reason = "Created"
        self.text = ""

    def json(self) -> dict:
        return self._payload


class GraphMailTests(unittest.TestCase):
    def test_recipient_parser_supports_display_names(self) -> None:
        self.assertEqual(
            split_recipients('"One" <one@example.com>; two@example.com\nThree <three@example.com>'),
            ["one@example.com", "two@example.com", "three@example.com"],
        )

    def test_uses_graph_powershell_public_device_code_identity(self) -> None:
        self.assertEqual(GRAPH_ORGANIZATIONS_TENANT, "organizations")
        self.assertEqual(GRAPH_POWERSHELL_CLIENT_ID, "14d82eec-204b-4c2f-b7e8-296a70dab67e")

    def test_device_code_flow_is_started_and_completed(self) -> None:
        client = object.__new__(GraphMailClient)
        client.tenant_id = "tenant"
        client.client_id = "client"
        client.app = Mock()
        client.app.initiate_device_flow.return_value = {
            "user_code": "ABCD-EFGH",
            "verification_uri": "https://microsoft.com/devicelogin",
            "message": "Enter the code",
        }
        client.app.acquire_token_by_device_flow.return_value = {
            "access_token": "token",
            "id_token_claims": {"preferred_username": "sender@example.com"},
        }
        client._save_cache = Mock()  # type: ignore[method-assign]
        client._request = Mock(return_value=_Response({}))  # type: ignore[method-assign]

        device_code = client.initiate_device_sign_in()
        identity = client.complete_device_sign_in(device_code, "sender@example.com")

        self.assertEqual(device_code.user_code, "ABCD-EFGH")
        self.assertEqual(identity, GraphIdentity("sender@example.com"))
        client.app.initiate_device_flow.assert_called_once_with(scopes=["Mail.ReadWrite"])
        client.app.acquire_token_by_device_flow.assert_called_once_with(device_code.flow)
        client._request.assert_called_once()

    def test_device_code_flow_reports_an_initialization_error(self) -> None:
        client = object.__new__(GraphMailClient)
        client.tenant_id = "tenant"
        client.client_id = "client"
        client.app = Mock()
        client.app.initiate_device_flow.return_value = {
            "error": "unauthorized_client",
            "error_description": "Public client flow is disabled.",
        }

        with self.assertRaisesRegex(GraphAuthenticationRequired, "Public client flow is disabled"):
            client.initiate_device_sign_in()

    def test_device_code_flow_can_be_cancelled(self) -> None:
        flow = {"user_code": "ABCD", "expires_at": 123}
        device_code = GraphDeviceCode("ABCD", "https://example.com", "", flow)

        GraphMailClient.cancel_device_sign_in(device_code)

        self.assertEqual(flow["expires_at"], 0)

    def test_oracle_assignment_error_is_explained(self) -> None:
        with self.assertRaisesRegex(GraphAuthenticationRequired, "Entra administrator"):
            GraphMailClient._token_from_result(
                {
                    "error": "authorization_declined",
                    "error_description": "AADSTS50105: The signed in user is blocked.",
                }
            )

    def test_html_body_references_each_inline_image_by_cid(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            image = Path(temp_dir) / "summary.png"
            Image.new("RGB", (1200, 800), "white").save(image)
            document, items = _build_html_body("Hello\nWorld", [image])

        self.assertIn("Hello<br>World", document)
        self.assertEqual(len(items), 1)
        self.assertIn(f"cid:{items[0][1]}", document)

    def test_create_draft_adds_files_after_message_creation(self) -> None:
        client = object.__new__(GraphMailClient)
        client._acquire_silent_token = Mock(  # type: ignore[method-assign]
            return_value=("token", GraphIdentity("sender@example.com"))
        )
        client._request = Mock(  # type: ignore[method-assign]
            side_effect=[_Response({"id": "draft-id", "webLink": "https://example/draft"}), _Response({"id": "attachment"})]
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            attachment = Path(temp_dir) / "event.pdf"
            attachment.write_bytes(b"pdf")
            result = client.create_draft(
                subject="NSSR",
                from_account="sender@example.com",
                to='"One" <one@example.com>',
                cc="two@example.com",
                body_text="Body",
                attachments=[attachment],
                inline_images=[],
            )

        self.assertEqual(result, GraphDraftResult("draft-id", "https://example/draft"))
        self.assertEqual(client._request.call_count, 2)
        create_call = client._request.call_args_list[0]
        self.assertEqual(create_call.args[:2], ("POST", "https://graph.microsoft.com/v1.0/me/messages"))
        self.assertEqual(
            create_call.kwargs["json_payload"]["toRecipients"][0]["emailAddress"]["address"],
            "one@example.com",
        )

    def test_create_draft_rejects_a_different_signed_in_account(self) -> None:
        client = object.__new__(GraphMailClient)
        client._acquire_silent_token = Mock(  # type: ignore[method-assign]
            return_value=("token", GraphIdentity("other@example.com"))
        )
        with self.assertRaisesRegex(GraphMailError, "signed in as other@example.com"):
            client.create_draft(
                subject="NSSR",
                from_account="sender@example.com",
                to="one@example.com",
                cc="",
                body_text="Body",
                attachments=[],
                inline_images=[],
            )

    def test_runner_uses_graph_as_an_exclusive_route(self) -> None:
        graph_client = Mock()
        graph_client.create_draft.return_value = GraphDraftResult("graph-id")
        with patch("fbbatch.graph_mail.GraphMailClient", return_value=graph_client) as client_type:
            result = create_outlook_draft(
                subject="NSSR",
                from_account="sender@example.com",
                to="one@example.com",
                cc="",
                body_text="Body",
                attachments=[],
                inline_images=[],
                mail_method="graph",
            )

        self.assertEqual(result.entry_id, "graph-id")
        client_type.assert_called_once_with()
        graph_client.create_draft.assert_called_once()


if __name__ == "__main__":
    unittest.main()
