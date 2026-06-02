"""SpoolCLEngine — orchestrates CL account spool extraction.

Supports extracting CL account spools from a source DB and applying generated or
pre-existing `.SQL` spool files into a destination DB.

Threading: the engine is blocking by design. The UI calls `extract_one` /
`extract_many` from a worker thread and marshals status callbacks back via
`Tk.after(0, ...)`. Batch extraction uses a small worker pool so long runs can
move several accounts at once. Errors per account never abort the batch.
"""
from __future__ import annotations

import logging
import re
import tempfile
import threading
import uuid
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, Iterable

from paths import SPOOLS_CL_DIR, SPOOLS_CL_OUT_DIR, SPOOLS_CMR_OUT_DIR
from spools_cl_accounts.sqlcl import RunResult, SqlclRunner

log = logging.getLogger(__name__)


class SpoolCLStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    OK = "ok"
    ERROR = "error"
    CANCELLED = "cancelled"


@dataclass
class CLAccountResult:
    account: str
    status: SpoolCLStatus
    output_path: Path | None = None
    error: str = ""


# Lowercase country id -> Consumer Lending output folder name.
_COUNTRY_FOLDER = {
    "chile":    "Chile",
    "peru":     "Peru",
    "colombia": "Colombia",
    "mexico":   "Mexico",
}

# Conservative account regex: alphanumerics + underscore/dash, length 3..40.
# Same chars used by the original SQL substitution & by the generated filename.
_ACCOUNT_RE = re.compile(r"^[A-Za-z0-9_-]{3,40}$")
_BRANCH_RE = re.compile(r"^[A-Za-z0-9_-]{1,20}$")

CLStatusCallback = Callable[[str, SpoolCLStatus, str], None]

MAX_PARALLEL_ACCOUNTS = 3

_LEGACY_SPOOL_ROOT = r"C:\Users\Diego Pavez\Desktop\sqlcl\spools\spools_files\Accounts"
SPOOL_KIND_CONSUMER_LENDING = "consumer_lending"
SPOOL_KIND_CMR = "cmr"


def cl_template_path(country: str, spool_kind: str = SPOOL_KIND_CONSUMER_LENDING) -> Path:
    if spool_kind == SPOOL_KIND_CMR:
        return SPOOLS_CL_DIR / "CL_ACCOUNT_SPOOL_CHILE_CMR.sql"
    return SPOOLS_CL_DIR / f"CL_ACCOUNT_SPOOL_{country.upper()}2.sql"


def has_cl_template(country: str, spool_kind: str = SPOOL_KIND_CONSUMER_LENDING) -> bool:
    if spool_kind == SPOOL_KIND_CMR and country.lower() != "chile":
        return False
    return cl_template_path(country, spool_kind).is_file()


def is_valid_account(s: str) -> bool:
    return bool(_ACCOUNT_RE.match(s.strip()))


def is_valid_branch(s: str) -> bool:
    return bool(_BRANCH_RE.match(s.strip()))


def parse_accounts(text: str) -> tuple[list[str], list[str]]:
    """Split a textarea blob into valid + invalid account ids.

    Bulk input is deliberately line-based so a pasted Excel column remains
    predictable. A line with extra columns is invalid for the non-CMR flow.
    """
    valid: list[str] = []
    invalid: list[str] = []
    seen: set[str] = set()
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) != 1:
            invalid.append(line)
            continue
        s = parts[0]
        if not _ACCOUNT_RE.match(s):
            invalid.append(line)
            continue
        if s in seen:
            continue
        seen.add(s)
        valid.append(s)
    return valid, invalid


def parse_account_branches(text: str) -> tuple[list[tuple[str, str]], list[str]]:
    """Parse CMR bulk input as one `account branch` pair per line."""
    valid: list[tuple[str, str]] = []
    invalid: list[str] = []
    seen: set[str] = set()
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) != 2:
            invalid.append(line)
            continue
        account, branch = parts[0], parts[1].upper()
        if not _ACCOUNT_RE.match(account) or not _BRANCH_RE.match(branch):
            invalid.append(line)
            continue
        if account in seen:
            continue
        seen.add(account)
        valid.append((account, branch))
    return valid, invalid


def cl_output_folder_for(country: str, spool_kind: str = SPOOL_KIND_CONSUMER_LENDING) -> Path:
    if spool_kind == SPOOL_KIND_CMR:
        return SPOOLS_CMR_OUT_DIR
    folder = _COUNTRY_FOLDER.get(country.lower(), country.title())
    return SPOOLS_CL_OUT_DIR / folder


def cl_output_path_for(
    country: str,
    account: str,
    spool_kind: str = SPOOL_KIND_CONSUMER_LENDING,
    branch: str | None = None,
) -> Path:
    if spool_kind == SPOOL_KIND_CMR and branch:
        return cl_output_folder_for(country, spool_kind) / f"CL_Acc_Spool_{account}_{branch.strip().upper()}.SQL"
    return cl_output_folder_for(country, spool_kind) / f"CL_Acc_Spool_{account}.SQL"


def worker_count_for(account_count: int, max_workers: int = MAX_PARALLEL_ACCOUNTS) -> int:
    if account_count <= 0:
        return 0
    return min(account_count, max(1, max_workers))


def _with_exit(sql_text: str) -> str:
    if sql_text.rstrip().lower().endswith(("exit;", "exit")):
        return sql_text
    return sql_text.rstrip() + "\nexit;\n"


def _is_cancelled(cancel_event: threading.Event | None) -> bool:
    return cancel_event is not None and cancel_event.is_set()


def _cancelled_result(account: str, output_path: Path | None = None) -> CLAccountResult:
    return CLAccountResult(account, SpoolCLStatus.CANCELLED, output_path=output_path, error="Cancelled")


class SpoolCLEngine:
    """EXTRACT_ONLY: run the country template against a source DB.

    Consumer Lending output lands in SPOOLS_CL_OUT_DIR/<Country>/.
    Chile CMR output lands in SPOOLS_CMR_OUT_DIR/.
    """

    def __init__(self, runner: SqlclRunner):
        self.runner = runner

    def _render_template(self, country: str, spool_kind: str = SPOOL_KIND_CONSUMER_LENDING) -> Path:
        """Materialize a temp .sql with the output folder rewritten.

        The versioned `spools_CL/*2.sql` scripts are the non-interactive originals
        that use SQLcl positional args. We do not edit them in place; this temp
        copy points the `spool` command to the app output directory and appends
        `exit;` if the script doesn't already end with one, otherwise SQLcl sits
        at the prompt after the script finishes.
        """
        tmpl = cl_template_path(country, spool_kind)
        text = tmpl.read_text(encoding="utf-8")
        target_name = (
            "CL_Acc_Spool_&1._&2..SQL"
            if spool_kind == SPOOL_KIND_CMR else "CL_Acc_Spool_&1..SQL"
        )
        target = cl_output_folder_for(country, spool_kind) / target_name
        if spool_kind == SPOOL_KIND_CMR:
            text = text.replace(r"C:\Account_Spools\CL", str(SPOOLS_CMR_OUT_DIR))
        else:
            text = text.replace(_LEGACY_SPOOL_ROOT, str(SPOOLS_CL_OUT_DIR))
        text = re.sub(
            r'(?im)^spool\s+"?[^"\r\n]*CL_Acc_Spool_&1\.\.SQL"?\s*$',
            lambda _m: f'spool "{target}"',
            text,
            count=1,
        )
        rendered = _with_exit(text)
        out = Path(tempfile.gettempdir()) / f"oracle_tasks_{country.lower()}_{spool_kind}_{uuid.uuid4().hex[:8]}.sql"
        out.write_text(rendered, encoding="utf-8")
        return out

    def _render_existing_spool(self, spool_path: Path) -> Path:
        """Copy a generated spool to temp and append exit if needed."""
        text = spool_path.read_text(encoding="utf-8", errors="replace")
        out = Path(tempfile.gettempdir()) / f"oracle_tasks_apply_{uuid.uuid4().hex[:8]}.sql"
        out.write_text(_with_exit(text), encoding="utf-8")
        return out

    def extract_one(
        self,
        country: str,
        account: str,
        connection: str,
        on_status: CLStatusCallback | None = None,
        cancel_event: threading.Event | None = None,
        branch: str | None = None,
        spool_kind: str = SPOOL_KIND_CONSUMER_LENDING,
    ) -> CLAccountResult:
        if _is_cancelled(cancel_event):
            r = _cancelled_result(account)
            if on_status:
                on_status(account, r.status, r.error)
            return r

        if not _ACCOUNT_RE.match(account):
            r = CLAccountResult(account, SpoolCLStatus.ERROR, error="Invalid account format")
            if on_status:
                on_status(account, r.status, r.error)
            return r

        if spool_kind == SPOOL_KIND_CMR:
            branch = (branch or "").strip().upper()
            if country.lower() != "chile":
                r = CLAccountResult(account, SpoolCLStatus.ERROR, error="CMR spool is only available for Chile")
                if on_status:
                    on_status(account, r.status, r.error)
                return r
            if not _BRANCH_RE.match(branch):
                r = CLAccountResult(account, SpoolCLStatus.ERROR, error="Invalid branch format")
                if on_status:
                    on_status(account, r.status, r.error)
                return r

        if not has_cl_template(country, spool_kind):
            r = CLAccountResult(account, SpoolCLStatus.ERROR,
                              error=f"No spool template for country '{country}'")
            if on_status:
                on_status(account, r.status, r.error)
            return r

        if on_status:
            on_status(account, SpoolCLStatus.RUNNING, "")

        out_path = cl_output_path_for(country, account, spool_kind, branch)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        # Pre-clean stale file so existence on success means a fresh write.
        if out_path.exists():
            try:
                out_path.unlink()
            except OSError as e:
                log.warning("Could not remove stale spool %s: %s", out_path, e)

        rendered = self._render_template(country, spool_kind)
        args = [account, branch] if spool_kind == SPOOL_KIND_CMR else [account]
        try:
            # Wallclock cap per account. Most spools finish in under 90 s but
            # some legit cases (slow network, heavy account) can take 5-10 min;
            # 30 min is generous enough to never kill a working extraction.
            result: RunResult = self.runner.run_script(
                connection,
                rendered,
                args,
                timeout=1800,
                cancel_event=cancel_event,
            )
        finally:
            try:
                rendered.unlink()
            except OSError:
                pass

        if result.exit_code == 130 or _is_cancelled(cancel_event):
            r = _cancelled_result(account, out_path if out_path.exists() else None)
            if on_status:
                on_status(account, r.status, r.error)
            return r

        if not result.ok:
            tail = (result.stderr or result.stdout or "").strip().splitlines()
            err = tail[-1][:240] if tail else f"exit {result.exit_code}"
            r = CLAccountResult(account, SpoolCLStatus.ERROR, error=err)
            if on_status:
                on_status(account, r.status, r.error)
            return r

        if not out_path.exists():
            r = CLAccountResult(account, SpoolCLStatus.ERROR,
                              error=f"SQLcl exited 0 but spool file is missing: {out_path.name}")
            if on_status:
                on_status(account, r.status, r.error)
            return r

        r = CLAccountResult(account, SpoolCLStatus.OK, output_path=out_path)
        if on_status:
            on_status(account, r.status, "")
        return r

    def extract_many(
        self,
        country: str,
        accounts: Iterable[str],
        connection: str,
        on_status: CLStatusCallback | None = None,
        max_workers: int = MAX_PARALLEL_ACCOUNTS,
        cancel_event: threading.Event | None = None,
        branches: dict[str, str] | None = None,
        spool_kind: str = SPOOL_KIND_CONSUMER_LENDING,
    ) -> list[CLAccountResult]:
        account_list = list(accounts)
        branch_lookup = branches or {}
        workers = worker_count_for(len(account_list), max_workers)
        if workers == 0:
            return []
        if workers == 1:
            results: list[CLAccountResult] = []
            for acc in account_list:
                result = self.extract_one(
                    country,
                    acc,
                    connection,
                    on_status,
                    cancel_event,
                    branch_lookup.get(acc),
                    spool_kind,
                )
                results.append(result)
            return results

        results: list[CLAccountResult | None] = [None] * len(account_list)
        next_index = 0
        pending: set[Future] = set()
        future_to_index: dict[Future, int] = {}

        def submit_available(executor: ThreadPoolExecutor) -> None:
            nonlocal next_index
            while (
                next_index < len(account_list)
                and len(pending) < workers
                and not _is_cancelled(cancel_event)
            ):
                idx = next_index
                future = executor.submit(
                    self.extract_one,
                    country,
                    account_list[idx],
                    connection,
                    on_status,
                    cancel_event,
                    branch_lookup.get(account_list[idx]),
                    spool_kind,
                )
                pending.add(future)
                future_to_index[future] = idx
                next_index += 1

        with ThreadPoolExecutor(max_workers=workers) as executor:
            submit_available(executor)
            while pending:
                done, pending = wait(pending, timeout=0.2, return_when=FIRST_COMPLETED)
                if not done:
                    continue
                for future in done:
                    idx = future_to_index.pop(future)
                    try:
                        results[idx] = future.result()
                    except Exception as exc:
                        log.exception("Unhandled spool extraction error for %s", account_list[idx])
                        result = CLAccountResult(account_list[idx], SpoolCLStatus.ERROR, error=str(exc))
                        results[idx] = result
                        if on_status:
                            on_status(result.account, result.status, result.error)
                submit_available(executor)

            if _is_cancelled(cancel_event):
                for idx in range(next_index, len(account_list)):
                    result = _cancelled_result(account_list[idx])
                    results[idx] = result
                    if on_status:
                        on_status(result.account, result.status, result.error)

        return [r for r in results if r is not None]

    def apply_one(
        self,
        account: str,
        connection: str,
        spool_path: Path,
        on_status: CLStatusCallback | None = None,
        cancel_event: threading.Event | None = None,
    ) -> CLAccountResult:
        if _is_cancelled(cancel_event):
            r = _cancelled_result(account, spool_path)
            if on_status:
                on_status(account, r.status, r.error)
            return r

        if not spool_path.exists():
            r = CLAccountResult(account, SpoolCLStatus.ERROR, error=f"Spool not found: {spool_path.name}")
            if on_status:
                on_status(account, r.status, r.error)
            return r

        if on_status:
            on_status(account, SpoolCLStatus.RUNNING, "")

        rendered = self._render_existing_spool(spool_path)
        try:
            result: RunResult = self.runner.run_script(
                connection,
                rendered,
                [],
                timeout=1800,
                cancel_event=cancel_event,
            )
        finally:
            try:
                rendered.unlink()
            except OSError:
                pass

        if result.exit_code == 130 or _is_cancelled(cancel_event):
            r = _cancelled_result(account, spool_path)
            if on_status:
                on_status(account, r.status, r.error)
            return r

        if not result.ok:
            tail = (result.stderr or result.stdout or "").strip().splitlines()
            err = tail[-1][:240] if tail else f"exit {result.exit_code}"
            r = CLAccountResult(account, SpoolCLStatus.ERROR, output_path=spool_path, error=err)
            if on_status:
                on_status(account, r.status, r.error)
            return r

        r = CLAccountResult(account, SpoolCLStatus.OK, output_path=spool_path)
        if on_status:
            on_status(account, r.status, "")
        return r

    def apply_many(
        self,
        items: Iterable[tuple[str, Path]],
        connection: str,
        on_status: CLStatusCallback | None = None,
        max_workers: int = MAX_PARALLEL_ACCOUNTS,
        cancel_event: threading.Event | None = None,
    ) -> list[CLAccountResult]:
        item_list = list(items)
        workers = worker_count_for(len(item_list), max_workers)
        if workers == 0:
            return []
        if workers == 1:
            return [
                self.apply_one(account, connection, spool_path, on_status, cancel_event)
                for account, spool_path in item_list
            ]

        results: list[CLAccountResult | None] = [None] * len(item_list)
        next_index = 0
        pending: set[Future] = set()
        future_to_index: dict[Future, int] = {}

        def submit_available(executor: ThreadPoolExecutor) -> None:
            nonlocal next_index
            while (
                next_index < len(item_list)
                and len(pending) < workers
                and not _is_cancelled(cancel_event)
            ):
                idx = next_index
                account, spool_path = item_list[idx]
                future = executor.submit(
                    self.apply_one,
                    account,
                    connection,
                    spool_path,
                    on_status,
                    cancel_event,
                )
                pending.add(future)
                future_to_index[future] = idx
                next_index += 1

        with ThreadPoolExecutor(max_workers=workers) as executor:
            submit_available(executor)
            while pending:
                done, pending = wait(pending, timeout=0.2, return_when=FIRST_COMPLETED)
                if not done:
                    continue
                for future in done:
                    idx = future_to_index.pop(future)
                    account, spool_path = item_list[idx]
                    try:
                        results[idx] = future.result()
                    except Exception as exc:
                        log.exception("Unhandled spool inject error for %s", account)
                        result = CLAccountResult(account, SpoolCLStatus.ERROR, output_path=spool_path, error=str(exc))
                        results[idx] = result
                        if on_status:
                            on_status(result.account, result.status, result.error)
                submit_available(executor)

            if _is_cancelled(cancel_event):
                for idx in range(next_index, len(item_list)):
                    account, spool_path = item_list[idx]
                    result = _cancelled_result(account, spool_path)
                    results[idx] = result
                    if on_status:
                        on_status(result.account, result.status, result.error)

        return [r for r in results if r is not None]
