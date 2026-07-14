"""Microsoft Graph authentication and Night Shift draft creation."""
from __future__ import annotations

import base64
import html
import json
import logging
import mimetypes
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests

from paths import CONFIG_DIR


log = logging.getLogger(__name__)

GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"
GRAPH_SCOPES = ("Mail.ReadWrite",)
# Public client used by the official Microsoft Graph PowerShell SDK. This is
# the same device-code identity used by Connect-MgGraph without -ClientId.
GRAPH_POWERSHELL_CLIENT_ID = "14d82eec-204b-4c2f-b7e8-296a70dab67e"
GRAPH_ORGANIZATIONS_TENANT = "organizations"
GRAPH_CACHE_FILE = CONFIG_DIR / "graph_token_cache.dat"
SMALL_ATTACHMENT_LIMIT = 3 * 1024 * 1024
UPLOAD_CHUNK_SIZE = 10 * 320 * 1024


class GraphMailError(RuntimeError):
    """Base error for Graph configuration, authentication, or mail calls."""


class GraphAuthenticationRequired(GraphMailError):
    """Raised when device-code sign-in is required before creating a draft."""


@dataclass(frozen=True)
class GraphIdentity:
    username: str
    display_name: str = ""


@dataclass(frozen=True)
class GraphDraftResult:
    message_id: str
    web_link: str = ""


@dataclass(frozen=True)
class GraphDeviceCode:
    user_code: str
    verification_uri: str
    message: str
    flow: dict[str, Any]


def split_recipients(raw: str) -> list[str]:
    recipients: list[str] = []
    for chunk in re.split(r";|\n", raw or ""):
        text = chunk.strip()
        if not text:
            continue
        match = re.search(r"<([^>]+)>", text)
        address = (match.group(1) if match else text).strip()
        if address:
            recipients.append(address)
    return recipients


def _recipient_payload(raw: str) -> list[dict[str, dict[str, str]]]:
    return [{"emailAddress": {"address": address}} for address in split_recipients(raw)]


def _import_msal():
    try:
        import msal
    except ImportError as exc:
        raise GraphMailError(
            "Microsoft Graph support is not installed. Run update.bat or install msal."
        ) from exc
    return msal


def _protect_cache(serialized: str) -> bytes:
    try:
        import win32crypt

        protected = win32crypt.CryptProtectData(
            serialized.encode("utf-8"),
            "OracleTasksChile Microsoft Graph",
            None,
            None,
            None,
            0,
        )
        return base64.b64encode(protected)
    except Exception as exc:
        raise GraphMailError("The Microsoft Graph token cache could not be encrypted.") from exc


def _unprotect_cache(payload: bytes) -> str:
    try:
        import win32crypt

        protected = base64.b64decode(payload)
        _, clear = win32crypt.CryptUnprotectData(protected, None, None, None, 0)
        return clear.decode("utf-8")
    except Exception as exc:
        raise GraphMailError("The Microsoft Graph token cache could not be decrypted.") from exc


class GraphMailClient:
    def __init__(
        self,
        *,
        cache_path: Path = GRAPH_CACHE_FILE,
        session: requests.Session | None = None,
    ) -> None:
        self.tenant_id = GRAPH_ORGANIZATIONS_TENANT
        self.client_id = GRAPH_POWERSHELL_CLIENT_ID
        self.cache_path = Path(cache_path)
        self.session = session or requests.Session()
        msal = _import_msal()
        self.cache = msal.SerializableTokenCache()
        self._load_cache()
        self.app = msal.PublicClientApplication(
            self.client_id,
            authority=f"https://login.microsoftonline.com/{self.tenant_id}",
            token_cache=self.cache,
        )

    def _load_cache(self) -> None:
        if not self.cache_path.is_file():
            return
        try:
            serialized = _unprotect_cache(self.cache_path.read_bytes())
            self.cache.deserialize(serialized)
            log.info("graph_mail: encrypted token cache loaded path=%s", self.cache_path)
        except GraphMailError:
            log.exception("graph_mail: token cache could not be loaded; ignoring stale cache")

    def _save_cache(self) -> None:
        if not getattr(self.cache, "has_state_changed", False):
            return
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_bytes(_protect_cache(self.cache.serialize()))
        log.info("graph_mail: encrypted token cache saved path=%s", self.cache_path)

    def cached_identity(self, preferred_username: str = "") -> GraphIdentity | None:
        account = self._select_account(preferred_username)
        if not account:
            return None
        return self._identity_from_account(account)

    def initiate_device_sign_in(self) -> GraphDeviceCode:
        log.info(
            "graph_mail: device-code sign-in starting tenant=%s client=%s",
            self.tenant_id,
            self.client_id,
        )
        flow = self.app.initiate_device_flow(scopes=list(GRAPH_SCOPES))
        user_code = str(flow.get("user_code") or "").strip()
        verification_uri = str(
            flow.get("verification_uri")
            or flow.get("verification_url")
            or "https://microsoft.com/devicelogin"
        ).strip()
        if not user_code:
            detail = str(
                flow.get("error_description")
                or flow.get("error")
                or flow.get("message")
                or "Microsoft did not return a device code."
            )
            raise GraphAuthenticationRequired(
                "Microsoft Graph could not start code sign-in. " + detail
            )
        log.info(
            "graph_mail: device code issued verification_uri=%s expires_in=%s",
            verification_uri,
            flow.get("expires_in"),
        )
        return GraphDeviceCode(
            user_code=user_code,
            verification_uri=verification_uri,
            message=str(flow.get("message") or ""),
            flow=flow,
        )

    def complete_device_sign_in(
        self,
        device_code: GraphDeviceCode,
        preferred_username: str = "",
    ) -> GraphIdentity:
        result = self.app.acquire_token_by_device_flow(device_code.flow)
        self._save_cache()
        token = self._token_from_result(result)
        identity = self._identity_from_result(result)
        preferred = (preferred_username or "").strip()
        if preferred and identity.username and identity.username.lower() != preferred.lower():
            log.warning(
                "graph_mail: device-code account differs from configured From account "
                "signed_in=%r from=%r",
                identity.username,
                preferred,
            )
        self._request(
            "GET",
            f"{GRAPH_BASE_URL}/me/mailFolders/drafts?$select=id,displayName",
            token=token,
            expected={200},
        )
        log.info("graph_mail: device-code sign-in completed username=%r", identity.username)
        return identity

    @staticmethod
    def cancel_device_sign_in(device_code: GraphDeviceCode | None) -> None:
        if device_code is not None:
            device_code.flow["expires_at"] = 0

    def sign_out(self) -> None:
        accounts = list(self.app.get_accounts())
        for account in accounts:
            self.app.remove_account(account)
        self._save_cache()
        log.info("graph_mail: signed out cached_accounts_removed=%s", len(accounts))

    def test_connection(self, preferred_username: str = "") -> GraphIdentity:
        token, identity = self._acquire_silent_token(preferred_username)
        self._request(
            "GET",
            f"{GRAPH_BASE_URL}/me/mailFolders/drafts?$select=id,displayName,totalItemCount",
            token=token,
            expected={200},
        )
        log.info("graph_mail: connection test succeeded username=%r", identity.username)
        return identity

    def create_draft(
        self,
        *,
        subject: str,
        from_account: str,
        to: str,
        cc: str,
        body_text: str,
        attachments: list[Path],
        inline_images: list[Path],
    ) -> GraphDraftResult:
        token, identity = self._acquire_silent_token(from_account)
        if from_account and identity.username:
            if from_account.strip().lower() != identity.username.strip().lower():
                raise GraphMailError(
                    f"Microsoft Graph is signed in as {identity.username}, but From account is "
                    f"{from_account}. Open Graph settings and sign in with the correct account."
                )

        existing_attachments = [Path(path) for path in attachments if path and Path(path).is_file()]
        existing_images = [Path(path) for path in inline_images if path and Path(path).is_file()]
        body_html, inline_items = _build_html_body(body_text, existing_images)
        payload = {
            "subject": subject,
            "body": {"contentType": "HTML", "content": body_html},
            "toRecipients": _recipient_payload(to),
            "ccRecipients": _recipient_payload(cc),
        }
        log.info(
            "graph_mail: creating draft username=%r to_count=%s cc_count=%s attachments=%s inline_images=%s",
            identity.username,
            len(payload["toRecipients"]),
            len(payload["ccRecipients"]),
            len(existing_attachments),
            len(existing_images),
        )
        response = self._request(
            "POST",
            f"{GRAPH_BASE_URL}/me/messages",
            token=token,
            expected={201},
            json_payload=payload,
        )
        message = response.json()
        message_id = str(message.get("id") or "")
        if not message_id:
            raise GraphMailError("Microsoft Graph created a draft without returning its message ID.")

        try:
            for image_path, content_id in inline_items:
                self._add_attachment(
                    token,
                    message_id,
                    image_path,
                    is_inline=True,
                    content_id=content_id,
                )
            for attachment in existing_attachments:
                self._add_attachment(token, message_id, attachment)
        except Exception:
            log.exception("graph_mail: attachment upload failed; deleting incomplete draft id=%s", message_id)
            try:
                self._request(
                    "DELETE",
                    f"{GRAPH_BASE_URL}/me/messages/{quote(message_id, safe='')}",
                    token=token,
                    expected={204},
                )
            except Exception:
                log.exception("graph_mail: incomplete draft cleanup failed id=%s", message_id)
            raise

        log.info("graph_mail: draft created id=%s", message_id)
        return GraphDraftResult(
            message_id=message_id,
            web_link=str(message.get("webLink") or ""),
        )

    def _select_account(self, preferred_username: str = "") -> dict[str, Any] | None:
        accounts = list(self.app.get_accounts())
        preferred = (preferred_username or "").strip().lower()
        if preferred:
            for account in accounts:
                username = str(account.get("username") or "").strip().lower()
                if username == preferred:
                    return account
        return accounts[0] if accounts else None

    def _acquire_silent_token(self, preferred_username: str = "") -> tuple[str, GraphIdentity]:
        account = self._select_account(preferred_username)
        if not account:
            raise GraphAuthenticationRequired(
                "Microsoft Graph is not signed in. Open Graph settings and select Sign in with code."
            )
        result = self.app.acquire_token_silent(list(GRAPH_SCOPES), account=account)
        self._save_cache()
        if not result:
            raise GraphAuthenticationRequired(
                "Microsoft Graph authorization expired. Open Graph settings and sign in with code again."
            )
        return self._token_from_result(result), self._identity_from_result(result, account)

    @staticmethod
    def _token_from_result(result: dict[str, Any] | None) -> str:
        if result and result.get("access_token"):
            return str(result["access_token"])
        detail = ""
        if result:
            detail = str(result.get("error_description") or result.get("error") or "")
        if "AADSTS50105" in detail.upper():
            raise GraphAuthenticationRequired(
                "Your organization blocks Microsoft Graph Command Line Tools for this account "
                "unless an Entra administrator assigns access. Use an authorized account, "
                "select Classic Outlook, or ask the administrator to grant access."
            )
        raise GraphAuthenticationRequired(
            "Microsoft Graph authorization failed. "
            + (detail or "Sign in with code again from Graph settings.")
        )

    @staticmethod
    def _identity_from_account(account: dict[str, Any]) -> GraphIdentity:
        return GraphIdentity(username=str(account.get("username") or ""))

    @classmethod
    def _identity_from_result(
        cls,
        result: dict[str, Any],
        account: dict[str, Any] | None = None,
    ) -> GraphIdentity:
        claims = result.get("id_token_claims") or {}
        username = str(
            claims.get("preferred_username")
            or claims.get("upn")
            or claims.get("email")
            or (account or {}).get("username")
            or ""
        )
        display_name = str(claims.get("name") or "")
        return GraphIdentity(username=username, display_name=display_name)

    def _add_attachment(
        self,
        token: str,
        message_id: str,
        path: Path,
        *,
        is_inline: bool = False,
        content_id: str = "",
    ) -> None:
        size = path.stat().st_size
        if size < SMALL_ATTACHMENT_LIMIT:
            payload: dict[str, Any] = {
                "@odata.type": "#microsoft.graph.fileAttachment",
                "name": path.name,
                "contentType": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
                "contentBytes": base64.b64encode(path.read_bytes()).decode("ascii"),
                "isInline": is_inline,
            }
            if content_id:
                payload["contentId"] = content_id
            self._request(
                "POST",
                f"{GRAPH_BASE_URL}/me/messages/{quote(message_id, safe='')}/attachments",
                token=token,
                expected={201},
                json_payload=payload,
                timeout=120,
            )
            log.info("graph_mail: attachment added path=%s size=%s inline=%s", path, size, is_inline)
            return

        self._upload_large_attachment(
            token,
            message_id,
            path,
            is_inline=is_inline,
            content_id=content_id,
        )

    def _upload_large_attachment(
        self,
        token: str,
        message_id: str,
        path: Path,
        *,
        is_inline: bool,
        content_id: str,
    ) -> None:
        size = path.stat().st_size
        item: dict[str, Any] = {
            "attachmentType": "file",
            "name": path.name,
            "size": size,
            "contentType": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
            "isInline": is_inline,
        }
        if content_id:
            item["contentId"] = content_id
        response = self._request(
            "POST",
            f"{GRAPH_BASE_URL}/me/messages/{quote(message_id, safe='')}/attachments/createUploadSession",
            token=token,
            expected={201},
            json_payload={"AttachmentItem": item},
        )
        upload_url = str(response.json().get("uploadUrl") or "")
        if not upload_url:
            raise GraphMailError(f"Microsoft Graph did not return an upload URL for {path.name}.")

        with path.open("rb") as handle:
            start = 0
            while start < size:
                chunk = handle.read(UPLOAD_CHUNK_SIZE)
                end = start + len(chunk) - 1
                upload_response = self.session.put(
                    upload_url,
                    data=chunk,
                    headers={
                        "Content-Type": "application/octet-stream",
                        "Content-Length": str(len(chunk)),
                        "Content-Range": f"bytes {start}-{end}/{size}",
                    },
                    timeout=180,
                )
                if upload_response.status_code not in {200, 201, 202}:
                    raise GraphMailError(_response_error(upload_response))
                start = end + 1
        log.info("graph_mail: large attachment uploaded path=%s size=%s inline=%s", path, size, is_inline)

    def _request(
        self,
        method: str,
        url: str,
        *,
        token: str,
        expected: set[int],
        json_payload: dict[str, Any] | None = None,
        timeout: int = 60,
    ) -> requests.Response:
        try:
            response = self.session.request(
                method,
                url,
                headers={"Authorization": f"Bearer {token}"},
                json=json_payload,
                timeout=timeout,
            )
        except requests.RequestException as exc:
            raise GraphMailError(f"Microsoft Graph request failed: {exc}") from exc
        if response.status_code not in expected:
            raise GraphMailError(_response_error(response))
        return response


def _response_error(response: requests.Response) -> str:
    detail = ""
    try:
        payload = response.json()
        error = payload.get("error") or {}
        detail = str(error.get("message") or payload.get("error_description") or "")
    except (ValueError, AttributeError):
        detail = (response.text or "").strip()[:500]
    return f"Microsoft Graph returned HTTP {response.status_code}: {detail or response.reason}"


def _build_html_body(body_text: str, images: list[Path]) -> tuple[str, list[tuple[Path, str]]]:
    body_lines = "<br>".join(
        html.escape(line).replace(" ", "&nbsp;") for line in (body_text or "").splitlines()
    )
    image_html: list[str] = []
    inline_items: list[tuple[Path, str]] = []
    for index, image in enumerate(images, start=1):
        content_id = f"night-shift-graph-{index}-{image.stat().st_mtime_ns}@oracle-tasks"
        width, height = _image_display_size(image, max_width=960, max_height=720)
        image_html.append(
            f'<div style="margin:16px 0;"><img src="cid:{html.escape(content_id, quote=True)}" '
            f'width="{width}" height="{height}" '
            f'style="display:block;width:{width}px;max-width:100%;height:auto;"></div>'
        )
        inline_items.append((image, content_id))
    document = (
        "<html><body>"
        "<p>Confidential - Oracle Restricted \\Including External Recipients</p>"
        f"<div>{body_lines}</div><br>"
        + "".join(image_html)
        + "</body></html>"
    )
    return document, inline_items


def _image_display_size(path: Path, *, max_width: int, max_height: int) -> tuple[int, int]:
    try:
        from PIL import Image

        with Image.open(path) as image:
            scale = min(1.0, max_width / image.width, max_height / image.height)
            return max(1, round(image.width * scale)), max(1, round(image.height * scale))
    except Exception:
        log.warning("graph_mail: could not inspect inline image dimensions path=%s", path, exc_info=True)
        return max_width, max_height
