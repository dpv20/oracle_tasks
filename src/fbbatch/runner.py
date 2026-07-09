"""Run external FBBatchSetup Java tools and render their HTML output."""
from __future__ import annotations

import os
import json
import logging
import queue
import re
import shutil
import subprocess
import tempfile
import threading
import time
from html import unescape
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from paths import DATA_DIR, LOG_FILE, REPO_ROOT
from settings.config import decrypt_password


log = logging.getLogger(__name__)
DEFAULT_FBBATCH_ROOT = REPO_ROOT / "FBBatchSetup"
SAVED_ISSUES_FILE = DATA_DIR / "fbbatch_saved_issues.json"
FBBATCH_OUTPUT_DIR = REPO_ROOT / "outputs" / "fbbatch"
OUTLOOK_START_TIMEOUT_SECONDS = 10.0
OUTLOOK_DRAFT_SYNC_SECONDS = 15.0
OUTLOOK_PROFILE_NAME = "Exchange"
OUTLOOK_INLINE_IMAGE_MAX_WIDTH = 960
OUTLOOK_INLINE_IMAGE_MAX_HEIGHT = 720
JAVA_DEFAULT_IDLE_TIMEOUT_SECONDS = 10 * 60.0
JAVA_EVENT_IDLE_TIMEOUT_SECONDS = 40 * 60.0
JAVA_MAX_RUNTIME_SECONDS = 90 * 60.0
JAVA_EXIT_GRACE_SECONDS = 60.0
ProgressCallback = Callable[[int, str], None]
_ENV_CREDENTIAL_BUCKET = {
    "PROD": "shared_prod",
    "QA": "user_qa",
    "DEV": "user_dev",
}
_PRIMARY_TNS = {
    ("chile", "PROD"): "FXBFCL_19C_PROD_OCI",
    ("chile", "QA"): "CHILE_QA_19C",
    ("chile", "DEV"): "CHILE_DEV",
    ("peru", "PROD"): "PERU_OCI_PROD",
    ("colombia", "PROD"): "BFCO_POCISANTIAGO",
    ("mexico", "PROD"): "MX_PROD_OCI",
}
_COUNTRY_PROPERTY_PREFIX = {
    "chile": "CL",
    "peru": "PE",
    "colombia": "COL",
    "mexico": "MX",
}
SPANISH_MONTHS = (
    "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
    "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre",
)


@dataclass
class BatchResult:
    ok: bool
    message: str
    html_path: Path | None = None
    pdf_path: Path | None = None
    image_path: Path | None = None
    image_paths: list[Path] | None = None
    images_dir: Path | None = None
    output_dir: Path | None = None
    exit_code: int | None = None
    event_skipped: bool = False


@dataclass
class OutlookDraftResult:
    entry_id: str
    folder_name: str


ISSUE_FIELDS = (
    "DATE",
    "COUNTRY",
    "TYPE_OF_FAILURE",
    "PROCESS_AFFECTED",
    "TIME_REPORTED",
    "CALL_START_TIME",
    "CALL_END_TIME",
    "ADDITIONAL_SUPPORT",
    "ESCALATED",
    "ISSUE_DETAILS",
    "ACTION_TAKEN",
    "SOLUTION_PROVIDED_TIME",
    "FURTHER_ACTION_REQUIRED",
    "PROCESS_END_TIME",
)


def resolve_fbbatch_root(configured_root: str | Path | None = None) -> Path:
    root = Path(str(configured_root).strip()) if configured_root else DEFAULT_FBBATCH_ROOT
    return root.expanduser()


def _candidate_fbbatch_roots(root: Path) -> list[Path]:
    candidates = [root]
    if root.name.lower() != "fbbatchsetup":
        candidates.append(root / "FBBatchSetup")
    return candidates


def validate_fbbatch_root(configured_root: str | Path | None = None) -> tuple[bool, str, Path]:
    root = resolve_fbbatch_root(configured_root)
    required = (
        Path("CHILE") / "lib" / "FalabellaChileCustom.jar",
        Path("CHILE") / "config" / "configuration.template.properties",
        Path("CHILE") / "config" / "EODBatchEvent" / "EODBatchEvent.properties",
        Path("CHILE") / "upload" / "EODBatchEvent" / "Template" / "EODBatchEvent.sql",
        Path("CommonBatches") / "lib" / "FalabellaCommonBatches.jar",
        Path("CommonBatches") / "config" / "configuration.template.properties",
        Path("CommonBatches") / "config" / "EODBATCH" / "FBEODBatches.properties",
        Path("CommonBatches") / "upload" / "EODBATCH" / "Template" / "EODBatch.sql",
    )
    best_root = root
    missing = list(required)
    for candidate in _candidate_fbbatch_roots(root):
        candidate_missing = [rel for rel in required if not (candidate / rel).exists()]
        if not candidate_missing:
            return True, "", candidate
        if len(candidate_missing) < len(missing):
            best_root = candidate
            missing = candidate_missing
    if missing:
        return False, "Invalid FBBatchSetup folder. Missing: " + ", ".join(str(p) for p in missing), best_root
    return True, "", best_root


def materialize_fbbatch_credentials(
    configured_root: str | Path,
    env: str,
    credentials: dict,
    *,
    include_common: bool,
) -> list[Path]:
    """Create ignored Java property files from the current user's saved credentials."""
    ok, msg, root = validate_fbbatch_root(configured_root)
    if not ok:
        raise ValueError(msg)

    environment = env.strip().upper()
    bucket = _ENV_CREDENTIAL_BUCKET.get(environment)
    if bucket is None:
        raise ValueError(f"Unsupported Night Shift environment: {env}")

    required_countries = ["chile"]
    if include_common and environment == "PROD":
        required_countries.extend(("peru", "colombia", "mexico"))

    selected: dict[str, dict[str, str]] = {}
    for country in required_countries:
        selected[country] = _select_fbbatch_credential(
            credentials,
            country,
            bucket,
            _PRIMARY_TNS.get((country, environment), ""),
            environment,
        )

    generated: list[Path] = []
    try:
        chile_replacements = _credential_property_values(
            "CL_PROD" if environment == "PROD" else "CL_DEV",
            selected["chile"],
        )
        generated.append(
            _write_runtime_configuration(
                root / "CHILE" / "config" / "configuration.template.properties",
                root / "CHILE" / "config" / "configuration.properties",
                chile_replacements,
            )
        )

        if include_common:
            common_replacements = dict(chile_replacements)
            if environment == "PROD":
                for country in ("peru", "colombia", "mexico"):
                    prefix = f"{_COUNTRY_PROPERTY_PREFIX[country]}_PROD"
                    common_replacements.update(_credential_property_values(prefix, selected[country]))
            generated.append(
                _write_runtime_configuration(
                    root / "CommonBatches" / "config" / "configuration.template.properties",
                    root / "CommonBatches" / "config" / "configuration.properties",
                    common_replacements,
                )
            )
        return generated
    except Exception:
        remove_materialized_fbbatch_credentials(generated)
        raise


def remove_materialized_fbbatch_credentials(paths: list[Path]) -> None:
    for path in paths:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            log.exception("Could not remove temporary FBBatch credential file path=%s", path)


def _select_fbbatch_credential(
    credentials: dict,
    country: str,
    bucket: str,
    preferred_tns: str,
    environment: str,
) -> dict[str, str]:
    candidates: list[dict[str, str]] = []
    for by_login in credentials.get(country, {}).values():
        if not isinstance(by_login, dict):
            continue
        for credential in by_login.values():
            if isinstance(credential, dict) and credential.get("bucket") == bucket:
                candidates.append(credential)

    if not candidates:
        label = country.capitalize()
        raise ValueError(
            f"Missing {environment} credential for {label}. Add it in Settings > Credentials."
        )

    preferred = preferred_tns.upper()
    candidates.sort(
        key=lambda item: (
            0 if str(item.get("tns", "")).upper() == preferred else 1,
            1 if "DR" in str(item.get("tns", "")).upper() else 0,
            str(item.get("tns", "")).upper(),
            str(item.get("user", "")).upper(),
        )
    )
    credential = candidates[0]
    password = decrypt_password(str(credential.get("password_enc", "")))
    user = str(credential.get("user", "")).strip()
    schema = str(credential.get("schema", "")).strip()
    if not user or not password:
        label = country.capitalize()
        raise ValueError(
            f"The saved {environment} credential for {label} is incomplete. Update it in Settings > Credentials."
        )
    return {"user": f"{user}[{schema}]" if schema else user, "password": password}


def _credential_property_values(prefix: str, credential: dict[str, str]) -> dict[str, str]:
    return {
        f"{prefix}_DB_USER": credential["user"],
        f"{prefix}_DB_PASSWORD": credential["password"],
    }


def _write_runtime_configuration(template: Path, target: Path, replacements: dict[str, str]) -> Path:
    text = template.read_text(encoding="utf-8")
    for key, value in replacements.items():
        escaped_value = value.replace("\\", "\\\\").replace("\r", "").replace("\n", "")
        pattern = rf"(?m)^(\s*{re.escape(key)}\s*[=:]\s*).*$"
        text, count = re.subn(pattern, lambda match: match.group(1) + escaped_value, text)
        if count != 1:
            raise ValueError(f"Missing property {key} in {template}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return target


def check_falabella_vpn() -> tuple[bool, str]:
    """Return True only when the Fortinet SSL/Falabella adapter is Up."""
    try:
        adapters = _network_adapter_statuses()
    except Exception as exc:  # noqa: BLE001 - diagnostics should not crash the UI.
        return False, f"Could not verify VPN status: {exc}"

    if not adapters:
        return False, "Could not read Windows network adapter status."

    forti_matches: list[str] = []
    forti_up = False
    other_active: list[str] = []
    for adapter in adapters:
        haystack = f"{adapter['name']} {adapter['description']}".lower()
        status = adapter["status"].strip()
        status_low = status.lower()
        label = adapter["name"] or adapter["description"] or "adapter"
        if _is_falabella_vpn_adapter(haystack):
            forti_matches.append(f"{label}: {status}")
            if status_low == "up":
                forti_up = True
        elif status_low == "up" and ("cisco" in haystack or "anyconnect" in haystack):
            other_active.append("Oracle VPN - Cisco Secure Client")
        elif status_low == "up" and any(token in haystack for token in ("globalprotect", "pangp", "palo alto")):
            other_active.append("BICE VPN - GlobalProtect")

    if forti_up and not other_active:
        return True, "Falabella VPN is connected."
    if forti_up and other_active:
        return False, "Falabella VPN is connected, but another VPN is also active: " + ", ".join(sorted(set(other_active))) + ". Disconnect the other VPN first."
    if other_active:
        return False, "Wrong VPN connected: " + ", ".join(sorted(set(other_active))) + ". Connect Falabella VPN - FortiClient first."
    if forti_matches:
        return False, "Falabella VPN was detected but is not connected (" + "; ".join(forti_matches) + ")."
    return False, "Falabella VPN adapter was not detected. Open the VPN tab and connect Falabella - FortiClient."


def _network_adapter_statuses() -> list[dict[str, str]]:
    completed = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-Command",
            "Get-NetAdapter | Select-Object Name, InterfaceDescription, Status | Format-List",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    adapters: list[dict[str, str]] = []
    current = {"name": "", "description": "", "status": ""}
    for raw_line in completed.stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("Name"):
            current["name"] = line.split(":", 1)[1].strip()
        elif line.startswith("InterfaceDescription"):
            current["description"] = line.split(":", 1)[1].strip()
        elif line.startswith("Status"):
            current["status"] = line.split(":", 1)[1].strip()
            adapters.append(current)
            current = {"name": "", "description": "", "status": ""}
    return adapters


def _is_falabella_vpn_adapter(haystack: str) -> bool:
    return (
        ("fortinet" in haystack and "ssl" in haystack)
        or "fortissl" in haystack
        or "forticlient" in haystack
    )


def run_eod_batch_event(
    env: str,
    fbbatch_root: str | Path | None = None,
    progress: ProgressCallback | None = None,
    *,
    credentials: dict | None = None,
) -> BatchResult:
    ok, msg, base_root = validate_fbbatch_root(fbbatch_root)
    if not ok:
        return BatchResult(False, msg)
    generated_configs: list[Path] = []
    try:
        if credentials is not None:
            generated_configs = materialize_fbbatch_credentials(
                base_root, env, credentials, include_common=False
            )
        root = base_root / "CHILE"
        output_dir = root / "output" / "EODBatchEvent"
        output_dir.mkdir(parents=True, exist_ok=True)
        before = _latest_html(output_dir)
        _emit_progress(progress, 1, "Starting EOD Batch Event")
        result = _run_java(
            root,
            "com.fellabela.custom.chile.eodevent.FBCLEODBatchEventApplication",
            f"{env}\n",
            progress=progress,
            progress_kind="event",
        )
        html_path = _newest_after(output_dir, before)
        return _with_pdf(result, html_path, "event", "EODBatchEvent", progress)
    except (OSError, ValueError) as exc:
        return BatchResult(False, str(exc))
    finally:
        remove_materialized_fbbatch_credentials(generated_configs)


def run_batch_report(
    env: str,
    latest: bool,
    report_date: str,
    has_issue: bool,
    fbbatch_root: str | Path | None = None,
    progress: ProgressCallback | None = None,
    *,
    credentials: dict | None = None,
) -> BatchResult:
    ok, msg, base_root = validate_fbbatch_root(fbbatch_root)
    if not ok:
        return BatchResult(False, msg)
    generated_configs: list[Path] = []
    try:
        if credentials is not None:
            generated_configs = materialize_fbbatch_credentials(
                base_root, env, credentials, include_common=True
            )
        root = base_root / "CommonBatches"
        output_dir = root / "output" / "EODBATCH"
        output_dir.mkdir(parents=True, exist_ok=True)
        before = _latest_html(output_dir)
        _emit_progress(progress, 1, "Starting EOD Batch Report")
        latest_answer = "Y" if latest else "N"
        issue_answer = "Y" if has_issue else "N"
        lines = [env, latest_answer]
        if not latest:
            lines.append(report_date)
        lines.append(issue_answer)
        if not has_issue:
            lines.append("N")
        result = _run_java(
            root,
            "com.fellabela.custom.common.eodbatch.FBEODBatchTimingApp",
            "\n".join(lines) + "\n",
            progress=progress,
            progress_kind="report_issue" if has_issue else "report_no_issue",
        )
        html_path = _newest_after(output_dir, before)
        return _with_report_images(result, html_path, "report", "BatchReport", progress)
    except (OSError, ValueError) as exc:
        return BatchResult(False, str(exc))
    finally:
        remove_materialized_fbbatch_credentials(generated_configs)


def write_issue_properties(issues: list[dict[str, str]], fbbatch_root: str | Path | None = None) -> Path:
    root = resolve_fbbatch_root(fbbatch_root)
    issue_path = root / "CommonBatches" / "upload" / "EODBATCH" / "EODBatchIssue.properties"
    lines = [
        "################### Issue details ##################################",
        "",
        f"NO_ISSUE_OCCURRED = {len(issues)}",
        "",
    ]
    for idx, issue in enumerate(issues, start=1):
        for field in ISSUE_FIELDS:
            value = (issue.get(field) or "").replace("\r\n", " ").replace("\n", " ").strip()
            lines.append(f"{idx}_{field}={value}")
        lines.append("")
    issue_path.parent.mkdir(parents=True, exist_ok=True)
    issue_path.write_text("\n".join(lines), encoding="utf-8")
    return issue_path


def load_saved_issues() -> list[dict[str, str]]:
    if not SAVED_ISSUES_FILE.exists():
        return []
    try:
        data = json.loads(SAVED_ISSUES_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    out: list[dict[str, str]] = []
    for item in data:
        if isinstance(item, dict) and isinstance(item.get("name"), str):
            issue = item.get("issue")
            if isinstance(issue, dict):
                out.append({"name": item["name"], "issue": {k: str(v) for k, v in issue.items()}})
    return out


def save_issue_template(name: str, issue: dict[str, str]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    name = name.strip()
    if not name:
        raise ValueError("Issue name is required")
    saved = [item for item in load_saved_issues() if item["name"].lower() != name.lower()]
    saved.append({"name": name, "issue": {field: issue.get(field, "") for field in ISSUE_FIELDS}})
    saved.sort(key=lambda item: item["name"].lower())
    SAVED_ISSUES_FILE.write_text(json.dumps(saved, indent=2, ensure_ascii=False), encoding="utf-8")


def delete_issue_template(name: str) -> None:
    saved = [item for item in load_saved_issues() if item["name"] != name]
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SAVED_ISSUES_FILE.write_text(json.dumps(saved, indent=2, ensure_ascii=False), encoding="utf-8")


def report_date_to_issue_date(report_date: str) -> str:
    """Convert ddMMyyyy report date to the issue DATE format used by properties."""
    from datetime import datetime

    return datetime.strptime(report_date, "%d%m%Y").strftime("%d-%b-%y").upper()


def report_date_parts(report_date: str) -> dict[str, str]:
    from datetime import datetime

    parsed = datetime.strptime(report_date, "%d%m%Y").date()
    month = SPANISH_MONTHS[parsed.month - 1]
    return {
        "DAY": str(parsed.day),
        "DAY2": f"{parsed.day:02d}",
        "MONTH": month,
        "MONTH_UPPER": month.upper(),
        "YEAR": str(parsed.year),
        "DATE_LONG": f"{parsed.day} de {month} de {parsed.year}",
    }


def default_mail_body(report_date: str, *, include_event: bool) -> str:
    parts = report_date_parts(report_date)
    if include_event:
        return (
            "Estimados,\n\n"
            f"    Adjunto el Informe de eventos EOD (End of Day) de Chile del batch de {parts['DATE_LONG']}.\n\n"
            f"    A continuación, se presentan los tiempos de ejecución del batch del {parts['DATE_LONG']}."
        )
    return (
        "Estimados,\n\n"
        f"    A continuación, se presentan los tiempos de ejecución del batch del {parts['DATE_LONG']}."
    )


def render_mail_template(template: str, report_date: str, *, include_event: bool) -> str:
    text = template.strip() or default_mail_body(report_date, include_event=include_event)
    return text.format(**report_date_parts(report_date))


def find_event_pdf_for_report_date(report_date: str) -> Path | None:
    from datetime import datetime

    parsed = datetime.strptime(report_date, "%d%m%Y").date()
    date_tokens = {
        parsed.strftime("%d-%m-%Y"),
        parsed.strftime("%d_%m_%Y"),
        parsed.strftime("%d%m%Y"),
    }
    if not FBBATCH_OUTPUT_DIR.exists():
        return None
    candidates = sorted(
        (
            path
            for path in FBBATCH_OUTPUT_DIR.rglob("*.pdf")
            if "event" in path.stem.lower()
        ),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for candidate in candidates:
        normalized = candidate.stem.replace(" ", "_")
        if any(token in normalized for token in date_tokens):
            return candidate
    return None


def create_outlook_draft(
    *,
    subject: str,
    from_account: str,
    to: str,
    cc: str,
    body_text: str,
    attachments: list[Path],
    inline_images: list[Path],
) -> OutlookDraftResult:
    from fbbatch.new_outlook import (
        NewOutlookAutomationError,
        NewOutlookUnavailable,
        create_new_outlook_draft,
    )

    classic_executable = _find_outlook_executable()
    if classic_executable is not None:
        log.info(
            "outlook_draft: using Classic Outlook primary path executable=%s",
            classic_executable,
        )
        return _create_classic_outlook_draft(
            subject=subject,
            from_account=from_account,
            to=to,
            cc=cc,
            body_text=body_text,
            attachments=attachments,
            inline_images=inline_images,
        )

    log.info("outlook_draft: Classic Outlook not installed; using New Outlook")
    try:
        create_new_outlook_draft(
            subject=subject,
            from_account=from_account,
            to=to,
            cc=cc,
            body_text=body_text,
            attachments=attachments,
            inline_images=inline_images,
        )
        log.info("outlook_draft: completed with New Outlook")
        return OutlookDraftResult(entry_id="new-outlook", folder_name="Drafts")
    except NewOutlookUnavailable as exc:
        raise RuntimeError(
            f"Neither Classic Outlook nor New Outlook could be opened.\nLog: {LOG_FILE}"
        ) from exc
    except NewOutlookAutomationError as exc:
        log.error("outlook_draft: New Outlook draft started but could not be completed: %s", exc)
        raise RuntimeError(
            "New Outlook opened the email but could not complete it safely. "
            f"The Classic Outlook fallback was not opened to avoid creating a duplicate.\nLog: {LOG_FILE}"
        ) from exc

    raise AssertionError("New Outlook draft creation returned unexpectedly.")


def _create_classic_outlook_draft(
    *,
    subject: str,
    from_account: str,
    to: str,
    cc: str,
    body_text: str,
    attachments: list[Path],
    inline_images: list[Path],
) -> OutlookDraftResult:
    import html
    try:
        import pythoncom
        import win32com.client
    except ImportError as exc:
        raise RuntimeError("Outlook automation is not available. Install Outlook and pywin32.") from exc

    log.info(
        "outlook_draft: start subject=%r from=%r to_count=%s cc_count=%s attachments=%s inline_images=%s",
        subject,
        from_account,
        len(_split_outlook_recipients(to)),
        len(_split_outlook_recipients(cc)),
        len([path for path in attachments if path]),
        len([path for path in inline_images if path]),
    )
    outlook = None
    started_outlook = False
    pythoncom.CoInitialize()
    try:
        try:
            outlook, started_outlook = _start_outlook_application(win32com.client)
            namespace = outlook.GetNamespace("MAPI")
            log.info("outlook_draft: got MAPI namespace")
            try:
                namespace.Logon("Exchange", "", False, False)
                log.info("outlook_draft: namespace logon profile=Exchange ok")
            except Exception:
                log.exception("outlook_draft: namespace logon profile=Exchange failed; trying default profile")
                namespace.Logon("", "", False, False)
                log.info("outlook_draft: namespace logon default profile ok")
            _ensure_outlook_window(outlook, namespace)
        except Exception as exc:
            raise RuntimeError("Outlook is not installed or could not be opened.") from exc

        wanted_account = from_account.strip().lower()
        selected = None
        if wanted_account:
            account_labels = []
            for account in outlook.Session.Accounts:
                smtp = str(getattr(account, "SmtpAddress", "") or "").lower()
                display = str(getattr(account, "DisplayName", "") or "").lower()
                account_labels.append(f"{display or '<no display>'} <{smtp or '<no smtp>'}>")
                if wanted_account in (smtp, display):
                    selected = account
                    break
            log.info("outlook_draft: available accounts=%s", "; ".join(account_labels) or "<none>")
            if selected is None:
                raise RuntimeError(f"Outlook account not found: {from_account}")
            log.info(
                "outlook_draft: selected account display=%r smtp=%r store=%r",
                _outlook_attr(selected, "DisplayName"),
                _outlook_attr(selected, "SmtpAddress"),
                _outlook_store_name(selected),
            )

        drafts_folder = _get_account_drafts_folder(namespace, selected)
        mail = _outlook_call(
            "create item in account Drafts",
            lambda: drafts_folder.Items.Add("IPM.Note"),
            attempts=3,
        )
        log.info(
            "outlook_draft: mail item created directly in %s",
            _outlook_folder_label(drafts_folder),
        )
        if selected is not None:
            mail.SendUsingAccount = selected
            log.info("outlook_draft: SendUsingAccount assigned")

        mail.Subject = subject
        _add_recipients(mail, to, 1)
        _add_recipients(mail, cc, 2)
        log.info("outlook_draft: recipients added total=%s", getattr(mail.Recipients, "Count", "<unknown>"))
        if not _outlook_call("resolve recipients", mail.Recipients.ResolveAll, attempts=3):
            unresolved = []
            for recipient in mail.Recipients:
                if not recipient.Resolved:
                    unresolved.append(str(recipient.Name))
            detail = ", ".join(unresolved) if unresolved else "unknown recipient"
            raise RuntimeError(f"Outlook could not resolve recipient(s): {detail}")

        for attachment in attachments:
            if attachment and attachment.exists():
                _outlook_call(
                    "add attachment",
                    lambda path=attachment: mail.Attachments.Add(str(path.resolve())),
                    attempts=3,
                )
                log.info("outlook_draft: attachment added path=%s", attachment)

        inline_image_html = _attach_inline_images(mail, inline_images)
        body_html = "<br>".join(html.escape(line).replace(" ", "&nbsp;") for line in body_text.splitlines())
        mail.HTMLBody = (
            "<html><body>"
            "<p>Confidential - Oracle Restricted \\Including External Recipients</p>"
            f"<div>{body_html}</div><br>"
            f"{inline_image_html}"
            + "</body></html>"
        )
        _outlook_call("save draft with inline images", mail.Save)
        log.info(
            "outlook_draft: saved without opening inspector saved=%r entry_id=%r parent=%r",
            _outlook_attr(mail, "Saved"),
            _outlook_attr(mail, "EntryID"),
            _outlook_parent_label(mail),
        )
        draft_state = OutlookDraftResult(
            entry_id=_outlook_attr(mail, "EntryID"),
            folder_name=_outlook_attr(drafts_folder, "Name") or "Drafts",
        )
        log.info(
            "outlook_draft: Save succeeded; treating item as saved in %s entry_id=%r",
            _outlook_folder_label(drafts_folder),
            draft_state.entry_id,
        )
        log.info(
            "outlook_draft: completed entry_id=%r folder=%r inspector_created=False",
            draft_state.entry_id,
            draft_state.folder_name,
        )
        log.info(
            "outlook_draft: waiting %.1fs for Exchange/New Outlook draft sync",
            OUTLOOK_DRAFT_SYNC_SECONDS,
        )
        time.sleep(OUTLOOK_DRAFT_SYNC_SECONDS)
        return OutlookDraftResult(
            entry_id=draft_state.entry_id,
            folder_name=draft_state.folder_name,
        )
    except Exception as exc:
        log.exception("outlook_draft: failed")
        raise RuntimeError(f"{exc}\nLog: {LOG_FILE}") from exc
    finally:
        if started_outlook and outlook is not None:
            try:
                outlook.Quit()
                log.info("outlook_draft: closed Classic Outlook started by Oracle Tasks")
            except Exception:
                log.warning(
                    "outlook_draft: draft is saved but Classic Outlook could not be closed automatically",
                    exc_info=True,
                )
        pythoncom.CoUninitialize()


def _start_outlook_application(client, timeout: float = OUTLOOK_START_TIMEOUT_SECONDS):
    try:
        outlook = client.GetActiveObject("Outlook.Application")
        log.info("outlook_draft: using active Outlook.Application")
        return outlook, False
    except Exception:
        pass

    executable = _find_outlook_executable()
    dispatched = None
    if executable is not None:
        log.info(
            "outlook_draft: starting visible Outlook path=%s profile=%s",
            executable,
            OUTLOOK_PROFILE_NAME,
        )
        subprocess.Popen(
            [str(executable), "/profile", OUTLOOK_PROFILE_NAME],
            cwd=str(executable.parent),
        )
        _start_outlook_profile_dialog_helper(
            profile_name=OUTLOOK_PROFILE_NAME,
            timeout=timeout,
        )
    else:
        log.info("outlook_draft: OUTLOOK.EXE not found; starting through COM")
        dispatched = client.Dispatch("Outlook.Application")

    deadline = time.monotonic() + max(0.0, timeout)
    while time.monotonic() < deadline:
        try:
            outlook = client.GetActiveObject("Outlook.Application")
            log.info("outlook_draft: Outlook.Application became active")
            return outlook, True
        except Exception:
            time.sleep(0.5)

    if dispatched is not None:
        return dispatched, True
    log.warning("outlook_draft: active Outlook wait timed out; falling back to COM Dispatch")
    return client.Dispatch("Outlook.Application"), True


def _start_outlook_profile_dialog_helper(
    *,
    profile_name: str,
    timeout: float,
) -> threading.Thread:
    helper = threading.Thread(
        target=_confirm_outlook_profile_dialog,
        kwargs={"profile_name": profile_name, "timeout": timeout},
        name="outlook-profile-dialog",
        daemon=True,
    )
    helper.start()
    return helper


def _confirm_outlook_profile_dialog(*, profile_name: str, timeout: float) -> bool:
    try:
        from pywinauto import Desktop
    except ImportError:
        log.warning("outlook_draft: pywinauto unavailable; cannot confirm Outlook profile dialog")
        return False

    deadline = time.monotonic() + max(0.0, timeout)
    desktop = Desktop(backend="uia")
    dialog_titles = {
        "choose profile",
        "elegir perfil",
        "seleccionar perfil",
    }
    while time.monotonic() < deadline:
        try:
            windows = desktop.windows()
        except Exception:
            log.debug("outlook_draft: profile dialog scan failed", exc_info=True)
            time.sleep(0.25)
            continue

        for window in windows:
            try:
                title = window.window_text().strip().casefold()
                controls = window.descendants()
                nested_title = next(
                    (
                        control.window_text().strip().casefold()
                        for control in controls
                        if control.window_text().strip().casefold() in dialog_titles
                    ),
                    "",
                )
                if title not in dialog_titles and not nested_title:
                    continue
                detected_title = title if title in dialog_titles else nested_title
                log.info("outlook_draft: Outlook profile dialog detected title=%r", detected_title)
                if _accept_outlook_profile_dialog(window, profile_name):
                    log.info("outlook_draft: Outlook profile confirmed profile=%s", profile_name)
                    return True
            except Exception:
                log.debug("outlook_draft: profile dialog changed while handling it", exc_info=True)
        time.sleep(0.25)

    log.info("outlook_draft: Outlook profile dialog was not shown before timeout")
    return False


def _accept_outlook_profile_dialog(dialog, profile_name: str) -> bool:
    controls = dialog.descendants()
    combo_boxes = [
        control
        for control in controls
        if str(control.element_info.control_type) == "ComboBox"
    ]
    if combo_boxes:
        combo = combo_boxes[0]
        current = combo.window_text().strip().casefold()
        if profile_name.casefold() not in current:
            try:
                combo.select(profile_name)
            except Exception:
                log.warning(
                    "outlook_draft: could not select Outlook profile=%s current=%r",
                    profile_name,
                    current,
                    exc_info=True,
                )
                return False

    accepted_labels = {"ok", "aceptar"}
    buttons = [
        control
        for control in controls
        if str(control.element_info.control_type) == "Button"
        and control.window_text().strip().casefold() in accepted_labels
    ]
    if not buttons:
        log.warning("outlook_draft: Outlook profile dialog has no OK/Accept button")
        return False

    try:
        buttons[0].invoke()
    except Exception:
        buttons[0].click_input()
    return True


def _find_outlook_executable() -> Path | None:
    found = shutil.which("OUTLOOK.EXE")
    if found:
        return Path(found)

    candidates: list[Path] = []
    for variable in ("ProgramFiles", "ProgramFiles(x86)"):
        base = os.environ.get(variable)
        if base:
            candidates.extend(
                Path(base) / "Microsoft Office" / "root" / office / "OUTLOOK.EXE"
                for office in ("Office16", "Office15")
            )

    try:
        import winreg

        registry_path = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\OUTLOOK.EXE"
        for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                with winreg.OpenKey(root, registry_path) as key:
                    value = winreg.QueryValue(key, None)
                    if value:
                        candidates.insert(0, Path(value.strip('"')))
            except OSError:
                continue
    except ImportError:
        pass

    return next((candidate for candidate in candidates if candidate.is_file()), None)


def _ensure_outlook_window(outlook, namespace) -> None:
    try:
        if int(outlook.Explorers.Count) > 0:
            log.info("outlook_draft: Outlook explorer window already open")
            return
    except Exception:
        log.exception("outlook_draft: could not inspect Outlook explorer windows")

    inbox = _outlook_call(
        "get Outlook inbox for visible window",
        lambda: namespace.GetDefaultFolder(6),
        attempts=3,
    )
    _outlook_call("open visible Outlook window", inbox.Display, attempts=3)
    log.info("outlook_draft: opened visible Outlook explorer before draft creation")


def _add_recipients(mail, raw: str, recipient_type: int) -> None:
    for recipient in _split_outlook_recipients(raw):
        item = mail.Recipients.Add(recipient)
        item.Type = recipient_type


def _outlook_attr(obj, attr: str) -> str:
    try:
        return str(getattr(obj, attr, "") or "")
    except Exception as exc:
        return f"<{attr} error: {exc}>"


def _outlook_store_name(account) -> str:
    try:
        return _outlook_attr(account.DeliveryStore, "DisplayName")
    except Exception as exc:
        return f"<store error: {exc}>"


def _get_account_drafts_folder(namespace, account):
    if account is not None:
        try:
            folder = _outlook_call(
                "get account Drafts folder",
                lambda: account.DeliveryStore.GetDefaultFolder(16),
                attempts=3,
            )
            if folder is not None:
                log.info("outlook_draft: account Drafts folder=%s", _outlook_folder_label(folder))
                return folder
        except Exception:
            log.exception("outlook_draft: account Drafts lookup failed")

    folder = _outlook_call("get default Drafts folder", lambda: namespace.GetDefaultFolder(16), attempts=3)
    if folder is None:
        raise RuntimeError("Outlook did not return a Drafts folder for the selected account.")
    log.info("outlook_draft: fallback Drafts folder=%s", _outlook_folder_label(folder))
    return folder


def _outlook_folder_label(folder) -> str:
    if folder is None:
        return "<none>"
    name = _outlook_attr(folder, "Name")
    entry_id = _outlook_attr(folder, "EntryID")
    store_id = _outlook_attr(folder, "StoreID")
    return f"{name} entry_id={entry_id[:24]} store_id={store_id[:24]}"


def _outlook_parent_label(mail) -> str:
    try:
        return _outlook_folder_label(getattr(mail, "Parent", None))
    except Exception as exc:
        return f"<Parent error: {exc}>"


def _outlook_call(label: str, func, *, attempts: int = 5, delay: float = 0.6):
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return func()
        except Exception as exc:  # noqa: BLE001 - Outlook COM often needs retry after transient busy/RPC states.
            last_exc = exc
            if attempt >= attempts:
                break
            log.warning(
                "outlook_draft: %s failed attempt %s/%s: %s; retrying",
                label,
                attempt,
                attempts,
                exc,
            )
            time.sleep(delay * attempt)
    raise last_exc or RuntimeError(f"Outlook call failed: {label}")


def _safe_filename(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9._ -]+", "_", value or "").strip(" ._")
    return (text[:80] or "mail")


def _split_outlook_recipients(raw: str) -> list[str]:
    recipients: list[str] = []
    for chunk in re.split(r";|\n", raw or ""):
        text = chunk.strip()
        if not text:
            continue
        match = re.search(r"<([^>]+)>", text)
        recipients.append((match.group(1) if match else text).strip())
    return recipients


def _attach_inline_images(mail, images: list[Path]) -> str:
    import html

    existing = [image for image in images if image.exists()]
    if not existing:
        log.info("outlook_draft: no inline report images to attach")
        return ""

    chunks: list[str] = []
    stamp = time.time_ns()
    for index, image in enumerate(existing, start=1):
        content_id = f"fbbatch-{stamp}-{index}@oracle-tasks"
        attachment = _outlook_call(
            "add inline image attachment",
            lambda path=image: mail.Attachments.Add(str(path.resolve()), 1, 0, path.name),
            attempts=3,
        )
        property_accessor = attachment.PropertyAccessor
        _outlook_call(
            "set inline image Content-ID",
            lambda pa=property_accessor, cid=content_id: pa.SetProperty(
                "http://schemas.microsoft.com/mapi/proptag/0x3712001F",
                cid,
            ),
            attempts=3,
        )
        optional_properties = (
            ("0x370E001F", "image/png"),
            ("0x7FFE000B", True),
            ("0x37140003", 4),
        )
        for property_tag, value in optional_properties:
            try:
                property_accessor.SetProperty(
                    f"http://schemas.microsoft.com/mapi/proptag/{property_tag}",
                    value,
                )
            except Exception:
                log.warning(
                    "outlook_draft: optional inline image property failed tag=%s path=%s",
                    property_tag,
                    image,
                    exc_info=True,
                )
        escaped_id = html.escape(content_id, quote=True)
        display_width, display_height = _inline_image_display_size(
            image,
            max_width=OUTLOOK_INLINE_IMAGE_MAX_WIDTH,
            max_height=OUTLOOK_INLINE_IMAGE_MAX_HEIGHT,
        )
        chunks.append(
            f'<div style="margin:16px 0;"><img src="cid:{escaped_id}" '
            f'width="{display_width}" height="{display_height}" '
            f'style="display:block;width:{display_width}px;max-width:100%;height:auto;"></div>'
        )
        log.info("outlook_draft: inline CID image attached path=%s cid=%s", image, content_id)
    return "".join(chunks)


def _inline_image_display_size(
    image_path: Path,
    *,
    max_width: int,
    max_height: int,
) -> tuple[int, int]:
    try:
        from PIL import Image

        with Image.open(image_path) as image:
            scale = min(1.0, max_width / image.width, max_height / image.height)
            return (
                max(1, round(image.width * scale)),
                max(1, round(image.height * scale)),
            )
    except (OSError, ValueError):
        log.warning("outlook_draft: could not inspect inline image size path=%s", image_path)
        return max_width, max_height


def issues_for_date(issue_date: str) -> list[dict[str, str]]:
    wanted = issue_date.strip().upper()
    return [
        item["issue"]
        for item in load_saved_issues()
        if str(item.get("issue", {}).get("DATE", "")).strip().upper() == wanted
    ]


def _run_java(
    root: Path,
    main_class: str,
    stdin_text: str,
    *,
    progress: ProgressCallback | None = None,
    progress_kind: str = "",
) -> BatchResult:
    if not root.exists():
        return BatchResult(False, f"FBBatchSetup folder not found: {root}")
    java = shutil.which("java")
    if not java:
        return BatchResult(False, "Java was not found in PATH.")
    tracker = _JavaProgress(progress_kind, progress)
    process_label = _java_process_label(progress_kind)
    idle_timeout = _java_idle_timeout_seconds(progress_kind)
    started = time.monotonic()
    log.info(
        "fbbatch_java: starting process=%s class=%s root=%s idle_timeout=%ss max_runtime=%ss",
        process_label,
        main_class,
        root,
        int(idle_timeout),
        int(JAVA_MAX_RUNTIME_SECONDS),
    )
    try:
        process = subprocess.Popen(
            [java, "-cp", "lib/*", main_class],
            cwd=str(root),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if process.stdin:
            process.stdin.write(stdin_text)
            process.stdin.close()
        lines: list[str] = []
        output_queue: queue.Queue[object] = queue.Queue()
        output_finished = object()
        reader_errors: list[BaseException] = []

        def read_output() -> None:
            try:
                if process.stdout:
                    for output_line in process.stdout:
                        output_queue.put(output_line)
            except Exception as exc:  # Preserve reader failures for the worker thread.
                reader_errors.append(exc)
            finally:
                output_queue.put(output_finished)

        reader = threading.Thread(
            target=read_output,
            name=f"fbbatch-{progress_kind or 'java'}-output",
            daemon=True,
        )
        reader.start()
        last_output = started
        last_heartbeat_minute = 0
        timeout_message = ""

        while True:
            now = time.monotonic()
            elapsed = now - started
            idle = now - last_output
            if elapsed >= JAVA_MAX_RUNTIME_SECONDS:
                timeout_message = (
                    f"{process_label} exceeded the {int(JAVA_MAX_RUNTIME_SECONDS // 60)}-minute "
                    "safety limit."
                )
                break
            if idle >= idle_timeout:
                timeout_message = (
                    f"{process_label} stopped after {int(idle_timeout // 60)} minutes "
                    "without output."
                )
                break

            idle_minutes = int(idle // 60)
            if idle_minutes > last_heartbeat_minute:
                tracker.heartbeat(idle)
                last_heartbeat_minute = idle_minutes

            wait_seconds = max(
                0.05,
                min(
                    1.0,
                    JAVA_MAX_RUNTIME_SECONDS - elapsed,
                    idle_timeout - idle,
                ),
            )
            try:
                item = output_queue.get(timeout=wait_seconds)
            except queue.Empty:
                if process.poll() is not None and not reader.is_alive():
                    break
                continue

            if item is output_finished:
                break
            line = str(item)
            lines.append(line)
            last_output = time.monotonic()
            last_heartbeat_minute = 0
            tracker.update(line)

        if timeout_message:
            process.kill()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                log.warning("fbbatch_java: process did not exit promptly after kill process=%s", process_label)
            output_tail = _safe_tail("".join(lines))
            log.error(
                "fbbatch_java: timeout process=%s elapsed=%.1fs idle=%.1fs message=%s output_tail=%s",
                process_label,
                time.monotonic() - started,
                time.monotonic() - last_output,
                timeout_message,
                output_tail,
            )
            return BatchResult(False, timeout_message)

        exit_code = process.wait(timeout=JAVA_EXIT_GRACE_SECONDS)
        reader.join(timeout=1)
        if reader_errors:
            log.warning(
                "fbbatch_java: output reader failed process=%s error=%s",
                process_label,
                reader_errors[-1],
            )
    except subprocess.TimeoutExpired:
        process.kill()
        message = f"{process_label} finished its output but did not exit cleanly."
        log.exception("fbbatch_java: exit timeout process=%s", process_label)
        return BatchResult(False, message)
    except OSError as exc:
        log.exception("fbbatch_java: could not start process=%s", process_label)
        return BatchResult(False, f"Could not start Java: {exc}")

    combined_output = "".join(lines)
    ok = exit_code == 0
    elapsed = time.monotonic() - started
    if ok:
        _emit_progress(progress, 90, "Java process completed")
        log.info("fbbatch_java: completed process=%s elapsed=%.1fs", process_label, elapsed)
    else:
        log.error(
            "fbbatch_java: failed process=%s exit_code=%s elapsed=%.1fs output_tail=%s",
            process_label,
            exit_code,
            elapsed,
            _safe_tail(combined_output),
        )
    message = "Completed." if ok else _safe_tail(combined_output)
    return BatchResult(ok, message, exit_code=exit_code)


def _java_process_label(progress_kind: str) -> str:
    if progress_kind == "event":
        return "EOD Batch Event"
    if progress_kind.startswith("report"):
        return "EOD Batch Report"
    return "Java process"


def _java_idle_timeout_seconds(progress_kind: str) -> float:
    if progress_kind == "event":
        return JAVA_EVENT_IDLE_TIMEOUT_SECONDS
    return JAVA_DEFAULT_IDLE_TIMEOUT_SECONDS


class _JavaProgress:
    EVENT_TOTAL = 34
    REPORT_TOTAL = 10

    def __init__(self, kind: str, callback: ProgressCallback | None):
        self.kind = kind
        self.callback = callback
        self.events_seen: set[str] = set()
        self.event_summaries_seen: set[str] = set()
        self.process_seen: set[str] = set()
        self.last_event_name = ""
        self.last_percent = 0

    def update(self, line: str) -> None:
        line = line.strip()
        if not line or not self.callback:
            return
        lower = line.lower()
        if "successfully connected" in lower:
            if self.last_percent < 20:
                self._emit(8, "Connected to database")
            return
        if self.kind == "event":
            self._update_event(line)
        elif self.kind.startswith("report"):
            self._update_report(line)

    def _update_event(self, line: str) -> None:
        match = re.search(r"Event=(.*?)\s+recordsCount=", line)
        if match:
            event_name = match.group(1).strip()
            self.events_seen.add(event_name)
            self.last_event_name = event_name
            pct = min(90, 5 + int((len(self.events_seen) / self.EVENT_TOTAL) * 85))
            self._emit(pct, f"Event {len(self.events_seen)}/{self.EVENT_TOTAL}: {event_name[:70]}")
            return
        if len(self.events_seen) < self.EVENT_TOTAL:
            return
        summary = re.match(r"(.+?)-->\s*[\d.]+\s*$", line)
        if summary:
            event_name = summary.group(1).strip()
            self.event_summaries_seen.add(event_name)
            count = len(self.event_summaries_seen)
            pct = min(92, 90 + int((count / self.EVENT_TOTAL) * 2))
            self._emit(pct, f"Building event report {count}/{self.EVENT_TOTAL}")

    def heartbeat(self, idle_seconds: float) -> None:
        if self.kind != "event" or not self.events_seen:
            return
        minutes = max(1, int(idle_seconds // 60))
        self._emit(
            self.last_percent,
            f"Still processing ({minutes} min without a new Event); last completed "
            f"{len(self.events_seen)}/{self.EVENT_TOTAL}: {self.last_event_name[:60]}",
        )

    def _update_report(self, line: str) -> None:
        match = re.search(r"FBEODBatchProcessInfo\(country=(.*?),\s*process=(.*?),", line)
        if match:
            key = f"{match.group(1)} {match.group(2)}"
            self.process_seen.add(key)
            pct = min(89, 12 + int((len(self.process_seen) / self.REPORT_TOTAL) * 76))
            self._emit(pct, f"Batch timing {len(self.process_seen)}/{self.REPORT_TOTAL}: {key}")
            return
        if "generating latest report" in line.lower():
            self._emit(max(self.last_percent, 12), "Generating latest report")
            return
        if "number of issues occurred" in line.lower():
            self._emit(max(self.last_percent, 88), "Adding issue details")
            return
        if "report has been generated successfully" in line.lower():
            self._emit(90, "Java report generated")

    def _emit(self, percent: int, message: str) -> None:
        percent = max(self.last_percent, min(99, percent))
        self.last_percent = percent
        _emit_progress(self.callback, percent, message)


def _emit_progress(callback: ProgressCallback | None, percent: int, message: str) -> None:
    if callback:
        callback(max(0, min(100, int(percent))), message)


def _with_pdf(result: BatchResult, html_path: Path | None, output_kind: str, default_stem: str, progress: ProgressCallback | None = None) -> BatchResult:
    if html_path and html_path.exists():
        _emit_progress(progress, 93, "Copying HTML output")
        local_html = _copy_to_project_output(html_path, output_kind, default_stem)
        result.html_path = local_html
        result.output_dir = local_html.parent
        pdf_path = local_html.with_suffix(".pdf")
        _emit_progress(progress, 96, "Creating PDF")
        pdf_ok, pdf_msg = html_to_pdf(local_html, pdf_path)
        if pdf_ok:
            result.pdf_path = pdf_path
            try:
                local_html.unlink()
                result.html_path = None
                log.info("fbbatch_event: removed local HTML after PDF creation path=%s", local_html)
            except OSError:
                log.exception("fbbatch_event: could not remove local HTML path=%s", local_html)
            _emit_progress(progress, 100, "Report ready")
        elif result.ok:
            result.message = f"HTML generated, but PDF conversion failed: {pdf_msg}"
    elif result.ok:
        result.ok = False
        result.message = f"{default_stem} finished, but no new HTML output was found."
    return result


def _copy_to_project_output(html_path: Path, output_kind: str, default_stem: str) -> Path:
    target_dir = _night_shift_output_dir(html_path, output_kind, default_stem)
    target_dir.mkdir(parents=True, exist_ok=True)
    name = html_path.name or f"{default_stem}.html"
    target = target_dir / name
    if target.resolve() != html_path.resolve():
        shutil.copy2(html_path, target)
    return target


def _night_shift_output_dir(html_path: Path, output_kind: str, default_stem: str) -> Path:
    date_match = re.search(r"(?<!\d)(\d{2}-\d{2}-\d{4})(?!\d)", html_path.stem)
    if date_match:
        folder_name = f"NightShift_{date_match.group(1)}"
    else:
        fallback = _safe_filename(html_path.stem or default_stem).replace(" ", "_")
        folder_name = f"NightShift_{fallback or output_kind}"
    return FBBATCH_OUTPUT_DIR / folder_name


def _with_report_images(result: BatchResult, html_path: Path | None, output_kind: str, default_stem: str, progress: ProgressCallback | None = None) -> BatchResult:
    if html_path and html_path.exists():
        _emit_progress(progress, 93, "Copying HTML output")
        local_html = _copy_to_project_output(html_path, output_kind, default_stem)
        result.html_path = local_html
        result.output_dir = local_html.parent
        images_dir = local_html.parent
        _emit_progress(progress, 96, "Creating image segments")
        image_paths, image_msg = report_html_to_segment_images(local_html, images_dir)
        result.images_dir = images_dir
        result.image_paths = image_paths
        if result.ok and image_msg:
            result.message = image_msg
        if result.ok:
            _emit_progress(progress, 100, "Report ready")
    elif result.ok:
        result.ok = False
        result.message = f"{default_stem} finished, but no new HTML output was found."
    return result


def report_indicates_chile_batch_skipped(html_path: Path | None) -> bool:
    if not html_path or not html_path.exists():
        return False
    try:
        text = _strip_tags(html_path.read_text(encoding="utf-8", errors="replace")).lower()
    except OSError:
        return False
    text = re.sub(r"\s+", " ", text)
    return (
        "durante feriados y fin de semana no se ejecuta el proceso batch en chile" in text
        or "no se ejecuta el proceso batch en chile" in text
    )


def _with_image(result: BatchResult, html_path: Path | None, default_stem: str) -> BatchResult:
    result.html_path = html_path
    if html_path and html_path.exists():
        image_path = html_path.with_suffix(".png")
        image_ok, image_msg = html_to_png(html_path, image_path)
        if image_ok:
            result.image_path = image_path
        elif result.ok:
            result.message = f"HTML generated, but image conversion failed: {image_msg}"
    elif result.ok:
        result.message = f"{default_stem} finished, but no new HTML output was found."
    return result


def report_html_to_segment_images(html_path: Path, images_dir: Path) -> tuple[list[Path], str]:
    text = html_path.read_text(encoding="utf-8")
    segments = _build_report_segment_html(text)
    if not segments:
        return [], "HTML generated, but no report segments were found."
    images_dir.mkdir(parents=True, exist_ok=True)
    for pattern in ("summary.html", "summary.png", "incident_*.html", "incident_*.png"):
        for stale_path in images_dir.glob(pattern):
            stale_path.unlink(missing_ok=True)
    image_paths: list[Path] = []
    failures: list[str] = []
    for name, segment_html in segments:
        segment_path = images_dir / f"{name}.html"
        image_path = images_dir / f"{name}.png"
        segment_path.write_text(segment_html, encoding="utf-8")
        try:
            ok, msg = html_to_cropped_png(segment_path, image_path)
        except Exception as exc:  # noqa: BLE001 - keep report generation resilient.
            ok, msg = False, str(exc)
        if ok:
            image_paths.append(image_path)
        else:
            failures.append(f"{name}: {msg}")
    if failures:
        return image_paths, "HTML generated, but some images failed: " + "; ".join(failures)
    return image_paths, f"HTML and {len(image_paths)} image segment(s) created."


def _render_segment_png(name: str, source_html: str, image_path: Path) -> None:
    from PIL import Image, ImageDraw, ImageFont

    regular = _pil_font(16)
    bold = _pil_font(16, bold=True)
    title_font = _pil_font(26, bold=True)
    small_bold = _pil_font(15, bold=True)
    if name == "summary":
        img = _render_summary_image(source_html, regular, bold, title_font, small_bold)
    else:
        idx = int(name.rsplit("_", 1)[1]) - 1
        blocks = _incident_row_blocks(source_html)
        img = _render_incident_image(blocks[idx], regular, bold, small_bold)
    image_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(image_path)


def _render_summary_image(html: str, regular, bold, title_font, small_bold):
    from PIL import Image, ImageDraw

    rows = _summary_rows(html)
    img = Image.new("RGB", (920, 735), "white")
    draw = ImageDraw.Draw(img)
    navy = "#0c2d5a"
    blue = "#1f4f8f"
    border = "#d9e3ee"
    light = "#f2f7fb"
    draw.text((26, 52), "NIGHT SHIFT STATUS REPORT : BANCO FALABELLA", fill=navy, font=title_font)
    draw.text((740, 52), "ORACLE", fill="#d1493f", font=title_font)
    date_text = _regex_text(html, r"Fecha de EOD:\s*<b>(.*?)</b>") or ""
    draw.text((26, 92), f"Fecha de EOD: {date_text}", fill="#244c7d", font=regular)
    status = _regex_text(html, r"Esatdo de Batch:\s*<b>(.*?)</b>") or _regex_text(html, r"Estado de Batch:\s*<b>(.*?)</b>") or ""
    draw.rectangle((27, 132, 550, 171), fill="#fffbe6")
    draw.rectangle((27, 132, 32, 171), fill="#c0392b")
    draw.text((51, 146), "Esatdo de Batch:", fill="#d12a1e", font=regular)
    draw.text((183, 146), status, fill="#d12a1e", font=bold)
    draw.text((324, 207), "Tiempos de Ejecucion de Batch", fill="#003366", font=bold)

    x0, y0 = 27, 227
    widths = [126, 122, 218, 218, 176]
    headers = ["PAIS", "PROCESO", "HORA INICO", "HORA FIN", "TIEMPO TOTAL"]
    row_h = 44
    x = x0
    for w, header in zip(widths, headers):
        draw.rectangle((x, y0, x + w, y0 + row_h), fill=blue, outline=border)
        _center_text(draw, (x, y0, x + w, y0 + row_h), header, small_bold, "white")
        x += w
    y = y0 + row_h
    for i, row in enumerate(rows):
        x = x0
        fill = light if i % 2 == 0 else "#f8fbfd"
        for j, (w, value) in enumerate(zip(widths, row)):
            cell_fill = "#eef6fb" if j == 0 else fill
            draw.rectangle((x, y, x + w, y + row_h), fill=cell_fill, outline=border)
            _center_text(draw, (x, y, x + w, y + row_h), value, regular, "#003f6f" if j == 0 else "#001b33")
            x += w
        y += row_h
    return img


def _render_incident_image(rows_html: str, regular, bold, small_bold):
    from PIL import Image, ImageDraw

    data = _incident_data(rows_html)
    blue = "#1f4f8f"
    border = "#d9e3ee"
    pale = "#f7fbfe"
    warn = "#fffbe6"
    red = "#d12a1e"
    x0 = 20
    label_w = 240
    total_w = 858
    row_h = 44
    detail_labels = [
        "SOPORTE ADICIONAL",
        "ESCALAMIENTO",
        "DETALLES DEL PROBLEMA",
        "ACCIONES REALIZADAS",
        "HORA DE ENTREGA DE LA SOLUCION",
        "SE REQUIEREN ACCIONES ADICIONALES",
        "HORA DE FIN DEL PROCESO",
    ]
    row_specs: list[tuple[str, str, int]] = []
    for label in ["TIPO DE PROBLEMA", "PROCESO AFECTADO", *detail_labels]:
        min_h = 66 if label in {"HORA DE ENTREGA DE LA SOLUCION", "SE REQUIEREN ACCIONES ADICIONALES"} else row_h
        value_w = total_w - label_w
        h = max(min_h, _wrapped_text_height(data.get(label, ""), regular, value_w - 20) + 24)
        row_specs.append((label, data.get(label, ""), h))

    height = 46 + row_h + sum(h for _, _, h in row_specs[:2]) + row_h + sum(h for _, _, h in row_specs[2:]) + 38
    img = Image.new("RGB", (900, max(610, height)), "white")
    draw = ImageDraw.Draw(img)
    draw.text((338, 27), "DETALLES DEL INCIDENTE", fill="#c42622", font=bold)
    y = 46
    date_value_w = 120
    pais_label_w = 170
    pais_value_w = total_w - label_w - date_value_w - pais_label_w
    cells = [
        (x0, label_w, "FECHA", blue, "white", small_bold),
        (x0 + label_w, date_value_w, data.get("FECHA", ""), pale, "#001b33", regular),
        (x0 + label_w + date_value_w, pais_label_w, "PAIS", blue, "white", small_bold),
        (x0 + label_w + date_value_w + pais_label_w, pais_value_w, data.get("PAIS", ""), pale, "#001b33", regular),
    ]
    for x, w, text, fill, color, font in cells:
        draw.rectangle((x, y, x + w, y + row_h), fill=fill, outline=border)
        _draw_wrapped_text(draw, text, (x + 10, y + 8, x + w - 10, y + row_h - 8), font, color)
    y += row_h
    for label, value, h in row_specs[:2]:
        _incident_full_row(draw, x0, y, total_w, label_w, h, label, value, blue, pale, border, regular, small_bold)
        y += h
    draw.rectangle((x0, y, x0 + label_w, y + row_h), fill=blue, outline=border)
    _draw_wrapped_text(draw, "HORA DEL REPORTE", (x0 + 10, y + 8, x0 + label_w - 10, y + row_h - 8), small_bold, "white")
    cols = [
        (x0 + label_w, 120, data.get("HORA DEL REPORTE", ""), pale, "#001b33", regular),
        (x0 + label_w + 120, 170, "Inicio de llamada", warn, red, small_bold),
        (x0 + label_w + 290, 95, data.get("Inicio de llamada", ""), pale, "#001b33", regular),
        (x0 + label_w + 385, 140, "Fin de llamada", warn, red, small_bold),
        (x0 + label_w + 525, total_w - label_w - 525, data.get("Fin de llamada", ""), pale, "#001b33", regular),
    ]
    for x, w, text, fill, color, font in cols:
        draw.rectangle((x, y, x + w, y + row_h), fill=fill, outline=border)
        _draw_wrapped_text(draw, text, (x + 10, y + 8, x + w - 10, y + row_h - 8), font, color)
    y += row_h
    for label, value, h in row_specs[2:]:
        _incident_full_row(draw, x0, y, total_w, label_w, h, label, value, blue, pale, border, regular, small_bold)
        y += h
    return img


def _incident_full_row(draw, x, y, total_w, label_w, h, label, value, blue, pale, border, regular, bold):
    draw.rectangle((x, y, x + label_w, y + h), fill=blue, outline=border)
    _draw_wrapped_text(draw, _wrap_label(label), (x + 10, y + 8, x + label_w - 10, y + h - 8), bold, "white")
    draw.rectangle((x + label_w, y, x + total_w, y + h), fill=pale, outline=border)
    _draw_wrapped_text(draw, value, (x + label_w + 10, y + 8, x + total_w - 10, y + h - 8), regular, "#001b33")


def _pil_font(size: int, *, bold: bool = False):
    from PIL import ImageFont

    names = ["arialbd.ttf" if bold else "arial.ttf", "segoeuib.ttf" if bold else "segoeui.ttf"]
    for name in names:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _center_text(draw, box, text, font, fill):
    left, top, right, bottom = box
    bbox = draw.textbbox((0, 0), text, font=font)
    x = left + ((right - left) - (bbox[2] - bbox[0])) / 2
    y = top + ((bottom - top) - (bbox[3] - bbox[1])) / 2
    draw.text((x, y), text, fill=fill, font=font)


def _wrap_text_to_width(text: str, font, max_width: int) -> list[str]:
    text = str(text or "").strip()
    if not text:
        return [""]
    lines: list[str] = []
    for raw_line in text.splitlines() or [text]:
        words = raw_line.split()
        if not words:
            lines.append("")
            continue
        line = ""
        for word in words:
            candidate = f"{line} {word}".strip()
            if _text_width(candidate, font) <= max_width:
                line = candidate
                continue
            if line:
                lines.append(line)
            line = word
            while _text_width(line, font) > max_width and len(line) > 1:
                chunk = line
                while _text_width(chunk, font) > max_width and len(chunk) > 1:
                    chunk = chunk[:-1]
                lines.append(chunk)
                line = line[len(chunk):]
        if line:
            lines.append(line)
    return lines or [""]


def _draw_wrapped_text(draw, text: str, box: tuple[int, int, int, int], font, fill: str) -> None:
    left, top, right, _bottom = box
    line_h = _line_height(font) + 4
    for idx, line in enumerate(_wrap_text_to_width(text, font, max(1, right - left))):
        draw.text((left, top + idx * line_h), line, fill=fill, font=font)


def _wrapped_text_height(text: str, font, max_width: int) -> int:
    return len(_wrap_text_to_width(text, font, max(1, max_width))) * (_line_height(font) + 4)


def _line_height(font) -> int:
    bbox = font.getbbox("Ag")
    return bbox[3] - bbox[1]


def _text_width(text: str, font) -> int:
    bbox = font.getbbox(text)
    return bbox[2] - bbox[0]


def _strip_tags(value: str) -> str:
    return unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", value))).strip()


def _regex_text(html: str, pattern: str) -> str:
    match = re.search(pattern, html, re.I | re.S)
    return _strip_tags(match.group(1)) if match else ""


def _summary_rows(html: str) -> list[list[str]]:
    first_table = html.split('<table>', 1)[1].split("</table>", 1)[0] if "<table>" in html else ""
    rows_html = re.findall(r"<tr>(.*?)</tr>", first_table, re.I | re.S)
    rows: list[list[str]] = []
    country = ""
    for row_html in rows_html:
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row_html, re.I | re.S)
        cells = [_strip_tags(cell) for cell in cells]
        if len(cells) == 5:
            country = cells[0]
            rows.append(cells)
        elif len(cells) == 4:
            rows.append([country, *cells])
    return rows


def _incident_data(rows_html: str) -> dict[str, str]:
    row_parts = re.findall(r"<tr>(.*?)</tr>", rows_html, re.I | re.S)
    out: dict[str, str] = {}
    for row in row_parts:
        cells = re.findall(r"<(?:th|td)[^>]*>(.*?)</(?:th|td)>", row, re.I | re.S)
        texts = [_strip_tags(cell) for cell in cells]
        if not texts:
            continue
        if texts[0] == "FECHA":
            out["FECHA"] = texts[1] if len(texts) > 1 else ""
            out["PAIS"] = texts[3] if len(texts) > 3 else ""
        elif texts[0] == "HORA DEL REPORTE":
            out["HORA DEL REPORTE"] = texts[1] if len(texts) > 1 else ""
            out["Inicio de llamada"] = texts[3] if len(texts) > 3 else ""
            out["Fin de llamada"] = texts[5] if len(texts) > 5 else ""
        elif len(texts) > 1:
            out[texts[0]] = texts[1]
    return out


def _wrap_label(label: str) -> str:
    if label == "HORA DE ENTREGA DE LA SOLUCION":
        return "HORA DE ENTREGA DE LA\nSOLUCION"
    if label == "SE REQUIEREN ACCIONES ADICIONALES":
        return "SE REQUIEREN ACCIONES\nADICIONALES"
    return label


def _build_report_segment_html(html: str) -> list[tuple[str, str]]:
    head = _between(html, "<head>", "</head>")
    summary_inner = _summary_inner_html(html)
    segments: list[tuple[str, str]] = []
    if summary_inner:
        segments.append(("summary", _wrap_segment_html(head, summary_inner)))

    incident_rows = _incident_row_blocks(html)
    for idx, rows in enumerate(incident_rows, start=1):
        incident_table = (
            '<table style="margin-top: 0;">'
            '<caption style="color:#c0392b;">DETALLES DEL INCIDENTE</caption>'
            f"<tbody>{rows}</tbody></table>"
        )
        segments.append((f"incident_{idx:02d}", _wrap_segment_html(head, incident_table)))
    return segments


def _summary_inner_html(html: str) -> str:
    if '<div class="container">' not in html:
        return ""
    container = html.split('<div class="container">', 1)[1]
    marker = '<table style="margin-top: 32px;"><caption style="color:#c0392b;">DETALLES DEL INCIDENTE</caption>'
    if marker in container:
        container = container.split(marker, 1)[0]
    elif "</body>" in container:
        container = container.split("</body>", 1)[0]
    return container.strip()


def _incident_row_blocks(html: str) -> list[str]:
    marker = '<caption style="color:#c0392b;">DETALLES DEL INCIDENTE</caption>'
    if marker not in html:
        return []
    after_caption = html.split(marker, 1)[1]
    body = _between(after_caption, "<tbody>", "</tbody>")
    if not body:
        return []
    separator = re.compile(r"<tr>\s*<td\s+colspan=\"7\"\s+style=\"height:30px;\s*background:white;\">\s*</td>\s*</tr>", re.I)
    return [part.strip() for part in separator.split(body) if part.strip()]


def _wrap_segment_html(head: str, body_inner: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>{head}
<style>
body {{ background: #ffffff; padding: 14px; }}
.container {{ margin: 0; box-shadow: none; border-radius: 0; padding: 12px; }}
</style>
</head>
<body><div class="container">{body_inner}</div></body>
</html>
"""


def _between(text: str, start: str, end: str) -> str:
    if start not in text:
        return ""
    tail = text.split(start, 1)[1]
    if end not in tail:
        return ""
    return tail.split(end, 1)[0]


def html_to_pdf(html_path: Path, pdf_path: Path) -> tuple[bool, str]:
    browser = _find_browser()
    if not browser:
        return False, "Edge/Chrome was not found."
    with tempfile.TemporaryDirectory(prefix="fbbatch_browser_") as profile_dir:
        args = [
            str(browser),
            "--headless",
            "--disable-gpu",
            "--disable-gpu-compositing",
            "--disable-software-rasterizer",
            "--disable-accelerated-2d-canvas",
            "--disable-features=UseSkiaRenderer,VizDisplayCompositor",
            f"--user-data-dir={profile_dir}",
            f"--print-to-pdf={pdf_path.resolve()}",
            html_path.resolve().as_uri(),
        ]
        try:
            completed = subprocess.run(
                args,
                check=True,
                capture_output=True,
                timeout=120,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            return False, _browser_error(exc)
    return (pdf_path.exists(), "PDF created." if pdf_path.exists() else "PDF was not created.")


def html_to_cropped_png(html_path: Path, image_path: Path) -> tuple[bool, str]:
    ok, message = html_to_png(
        html_path,
        image_path,
        window_size="1400,5000",
        device_scale_factor=1.5,
    )
    if not ok:
        return False, message
    try:
        _crop_png_to_content(image_path)
    except (OSError, ValueError) as exc:
        return False, f"Image created, but cropping failed: {exc}"
    return True, "Image created from its HTML segment."


def _crop_png_to_content(image_path: Path, *, padding: int = 24) -> None:
    from PIL import Image, ImageChops

    with Image.open(image_path) as source:
        image = source.convert("RGB")
    background = Image.new("RGB", image.size, "white")
    bounds = ImageChops.difference(image, background).getbbox()
    if bounds is None:
        raise ValueError("the browser capture is blank")
    left, top, right, bottom = bounds
    crop_box = (
        max(0, left - padding),
        max(0, top - padding),
        min(image.width, right + padding),
        min(image.height, bottom + padding),
    )
    image.crop(crop_box).save(image_path)


def html_to_png(
    html_path: Path,
    image_path: Path,
    *,
    window_size: str = "1100,1600",
    device_scale_factor: float | None = None,
) -> tuple[bool, str]:
    browser = _find_browser()
    if not browser:
        return False, "Edge/Chrome was not found."
    image_path.parent.mkdir(parents=True, exist_ok=True)
    last_error = "The browser did not create an image."
    for attempt in range(1, 4):
        image_path.unlink(missing_ok=True)
        with tempfile.TemporaryDirectory(prefix="fbbatch_browser_") as profile_dir:
            args = [
                str(browser),
                "--headless=new",
                "--disable-gpu",
                "--disable-dev-shm-usage",
                "--hide-scrollbars",
                "--no-first-run",
                "--no-default-browser-check",
                "--run-all-compositor-stages-before-draw",
                "--virtual-time-budget=1000",
                f"--user-data-dir={profile_dir}",
                f"--window-size={window_size}",
                f"--screenshot={image_path.resolve()}",
                html_path.resolve().as_uri(),
            ]
            if device_scale_factor is not None:
                args.insert(-1, f"--force-device-scale-factor={device_scale_factor}")
            try:
                subprocess.run(
                    args,
                    check=True,
                    capture_output=True,
                    timeout=120,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
                last_error = _browser_error(exc)
                log.warning("Browser PNG attempt %s failed: %s", attempt, last_error)
                continue
            if not _wait_for_valid_png(image_path):
                last_error = "The browser finished, but did not create a valid image."
                log.warning("Browser PNG attempt %s failed: %s", attempt, last_error)
                continue
            return True, "Image created."
    image_path.unlink(missing_ok=True)
    return False, f"{last_error} (3 attempts)."


def _wait_for_valid_png(image_path: Path, *, timeout: float = 10.0) -> bool:
    from PIL import Image

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with Image.open(image_path) as image:
                image.verify()
            return True
        except (FileNotFoundError, OSError):
            time.sleep(0.1)
    return False


def _browser_error(exc: BaseException) -> str:
    if isinstance(exc, subprocess.CalledProcessError):
        stderr = exc.stderr.decode(errors="replace").strip() if isinstance(exc.stderr, bytes) else str(exc.stderr or "").strip()
        stdout = exc.stdout.decode(errors="replace").strip() if isinstance(exc.stdout, bytes) else str(exc.stdout or "").strip()
        detail = stderr or stdout
        return f"{exc}; {detail}" if detail else str(exc)
    return str(exc)


def _legacy_html_to_pdf(html_path: Path, pdf_path: Path) -> tuple[bool, str]:
    browser = _find_browser()
    if not browser:
        return False, "Edge/Chrome was not found."
    try:
        subprocess.run(
            [
                str(browser),
                "--headless",
                "--disable-gpu",
                f"--print-to-pdf={pdf_path.resolve()}",
                html_path.resolve().as_uri(),
            ],
            check=True,
            capture_output=True,
            timeout=120,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    return (pdf_path.exists(), "PDF created." if pdf_path.exists() else "PDF was not created.")


def _legacy_html_to_png(html_path: Path, image_path: Path, *, window_size: str = "1100,1600") -> tuple[bool, str]:
    browser = _find_browser()
    if not browser:
        return False, "Edge/Chrome was not found."
    try:
        subprocess.run(
            [
                str(browser),
                "--headless",
                "--disable-gpu",
                "--hide-scrollbars",
                f"--window-size={window_size}",
                f"--screenshot={image_path.resolve()}",
                html_path.resolve().as_uri(),
            ],
            check=True,
            capture_output=True,
            timeout=120,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    return (image_path.exists(), "Image created." if image_path.exists() else "Image was not created.")


def _find_browser() -> Path | None:
    for exe in ("chrome.exe", "msedge.exe"):
        found = shutil.which(exe)
        if found:
            return Path(found)
    candidates = [
        Path(os.environ.get("ProgramFiles", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
        Path(os.environ.get("ProgramFiles(x86)", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
        Path(os.environ.get("ProgramFiles", "")) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
        Path(os.environ.get("ProgramFiles(x86)", "")) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
    ]
    return next((p for p in candidates if p.exists()), None)


def _latest_html(output_dir: Path) -> Path | None:
    if not output_dir.exists():
        return None
    files = list(output_dir.glob("*.html"))
    return max(files, key=lambda p: p.stat().st_mtime, default=None)


def _newest_after(output_dir: Path, before: Path | None) -> Path | None:
    latest = _latest_html(output_dir)
    if latest is None:
        return None
    if before is None:
        return latest
    if latest == before and latest.stat().st_mtime <= before.stat().st_mtime:
        return latest
    return latest


def _safe_tail(text: str) -> str:
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return "Java process failed."
    return "\n".join(lines[-4:])[:800]
