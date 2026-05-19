"""SpoolSavingsEngine - orchestrates Savings / IC account .INC generation.

Savings uses a different SQL generator than CL Accounts. It produces
`IC_account_data_<account>.INC` files and needs a branch code before the script
runs. The engine resolves the branch from the source DB, renders a temporary
non-interactive copy of the generator, and can apply generated/existing .INC
files to a destination DB.
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

from paths import SPOOLS_SAVINGS_DIR, SPOOLS_SAVINGS_OUT_DIR
from spools_cl_accounts.sqlcl import RunResult, SqlclRunner

log = logging.getLogger(__name__)


class SpoolSavingsStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    OK = "ok"
    ERROR = "error"
    CANCELLED = "cancelled"


@dataclass
class SavingsAccountResult:
    account: str
    status: SpoolSavingsStatus
    output_path: Path | None = None
    error: str = ""
    branch: str = ""


_COUNTRY_FOLDER = {
    "chile":    "Chile",
    "peru":     "Peru",
    "colombia": "Colombia",
    "mexico":   "Mexico",
}

_ACCOUNT_RE = re.compile(r"^[A-Za-z0-9_-]{3,40}$")
MAX_PARALLEL_SAVINGS_ACCOUNTS = 3

SavingsStatusCallback = Callable[[str, SpoolSavingsStatus, str], None]


def savings_template_path() -> Path:
    return SPOOLS_SAVINGS_DIR / "IC_account_data_falabella_v2.sql"


def has_savings_template() -> bool:
    return savings_template_path().is_file()


def is_valid_savings_account(s: str) -> bool:
    return bool(_ACCOUNT_RE.match(s.strip()))


def parse_savings_accounts(text: str) -> tuple[list[str], list[str]]:
    valid: list[str] = []
    invalid: list[str] = []
    seen: set[str] = set()
    for raw in re.split(r"[\s,;]+", text or ""):
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        if not _ACCOUNT_RE.match(s):
            invalid.append(s)
            continue
        if s in seen:
            continue
        seen.add(s)
        valid.append(s)
    return valid, invalid


def savings_output_path_for(country: str, account: str) -> Path:
    folder = _COUNTRY_FOLDER.get(country.lower(), country.title())
    return SPOOLS_SAVINGS_OUT_DIR / folder / f"IC_account_data_{account}.INC"


def worker_count_for(account_count: int, max_workers: int = MAX_PARALLEL_SAVINGS_ACCOUNTS) -> int:
    if account_count <= 0:
        return 0
    return min(account_count, max(1, max_workers))


def _with_exit(sql_text: str) -> str:
    if sql_text.rstrip().lower().endswith(("exit;", "exit")):
        return sql_text
    return sql_text.rstrip() + "\nexit;\n"


def _is_cancelled(cancel_event: threading.Event | None) -> bool:
    return cancel_event is not None and cancel_event.is_set()


def _cancelled_result(account: str, output_path: Path | None = None) -> SavingsAccountResult:
    return SavingsAccountResult(
        account,
        SpoolSavingsStatus.CANCELLED,
        output_path=output_path,
        error="Cancelled",
    )


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _tail_error(result: RunResult) -> str:
    tail = (result.stderr or result.stdout or "").strip().splitlines()
    return tail[-1][:240] if tail else f"exit {result.exit_code}"


class SpoolSavingsEngine:
    def __init__(self, runner: SqlclRunner):
        self.runner = runner

    def resolve_branch(
        self,
        account: str,
        connection: str,
        cancel_event: threading.Event | None = None,
    ) -> str | None:
        sql = f"""
select max(branch_code)
  from (
        select branch_code
          from sttm_cust_account
         where cust_ac_no = {_sql_literal(account)}
        union all
        select brn
          from ictb_acc_pr
         where acc = {_sql_literal(account)}
        union all
        select branch_code
          from sttb_account
         where ac_gl_no = {_sql_literal(account)}
       )
"""
        result = self.runner.run_query(connection, sql, timeout=120, cancel_event=cancel_event)
        if result.exit_code == 130 or _is_cancelled(cancel_event):
            return None
        if not result.ok:
            log.warning("Could not resolve branch for %s: %s", account, _tail_error(result))
            return None
        for line in (result.stdout or "").splitlines():
            branch = line.strip()
            if branch:
                return branch
        return None

    def _render_template(self, country: str, account: str, branch: str, out_path: Path) -> Path:
        text = savings_template_path().read_text(encoding="utf-8")
        text = re.sub(r"(?im)^accept\s+Branch\s+prompt\s+'.*?'\s*$", "", text)
        text = re.sub(r"(?im)^accept\s+Account\s+prompt\s+'.*?'\s*$", "", text)
        text = re.sub(
            r"(?im)^spool\s+D:\\IC_account_data_&Account\.\.INC\s*$",
            lambda _m: f'spool "{out_path}"',
            text,
        )
        text = re.sub(
            r"(?im)^prompt\s+Generated\s+D:\\IC_account_data_&Account\.\.INC\s*$",
            lambda _m: f"prompt Generated {out_path}",
            text,
        )
        if "dbms_output.enable(null);" not in text:
            text = text.replace(
                "begin\n    put_line('WHENEVER SQLERROR CONTINUE;');",
                "begin\n    dbms_output.enable(null);\n    put_line('WHENEVER SQLERROR CONTINUE;');",
                1,
            )
        rendered = _with_exit(f"define Branch = {branch}\ndefine Account = {account}\n{text}")
        out = Path(tempfile.gettempdir()) / f"oracle_tasks_savings_{country.lower()}_{uuid.uuid4().hex[:8]}.sql"
        out.write_text(rendered, encoding="utf-8")
        return out

    def _render_existing_spool(self, spool_path: Path) -> Path:
        text = spool_path.read_text(encoding="utf-8", errors="replace")
        out = Path(tempfile.gettempdir()) / f"oracle_tasks_savings_apply_{uuid.uuid4().hex[:8]}.sql"
        out.write_text(_with_exit(text), encoding="utf-8")
        return out

    def extract_one(
        self,
        country: str,
        account: str,
        connection: str,
        on_status: SavingsStatusCallback | None = None,
        cancel_event: threading.Event | None = None,
    ) -> SavingsAccountResult:
        if _is_cancelled(cancel_event):
            r = _cancelled_result(account)
            if on_status:
                on_status(account, r.status, r.error)
            return r

        if not _ACCOUNT_RE.match(account):
            r = SavingsAccountResult(account, SpoolSavingsStatus.ERROR, error="Invalid account format")
            if on_status:
                on_status(account, r.status, r.error)
            return r

        if not has_savings_template():
            r = SavingsAccountResult(account, SpoolSavingsStatus.ERROR, error="Savings template not found")
            if on_status:
                on_status(account, r.status, r.error)
            return r

        if on_status:
            on_status(account, SpoolSavingsStatus.RUNNING, "resolving branch...")

        out_path = savings_output_path_for(country, account)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if out_path.exists():
            try:
                out_path.unlink()
            except OSError as e:
                log.warning("Could not remove stale savings spool %s: %s", out_path, e)

        branch = self.resolve_branch(account, connection, cancel_event)
        if _is_cancelled(cancel_event):
            r = _cancelled_result(account, out_path if out_path.exists() else None)
            if on_status:
                on_status(account, r.status, r.error)
            return r
        if not branch:
            r = SavingsAccountResult(account, SpoolSavingsStatus.ERROR, error="Branch not found")
            if on_status:
                on_status(account, r.status, r.error)
            return r

        if on_status:
            on_status(account, SpoolSavingsStatus.RUNNING, f"extracting branch {branch}...")

        rendered = self._render_template(country, account, branch, out_path)
        try:
            result = self.runner.run_script(
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
            r = _cancelled_result(account, out_path if out_path.exists() else None)
            if on_status:
                on_status(account, r.status, r.error)
            return r

        if not result.ok:
            r = SavingsAccountResult(account, SpoolSavingsStatus.ERROR, error=_tail_error(result), branch=branch)
            if on_status:
                on_status(account, r.status, r.error)
            return r

        if not out_path.exists():
            r = SavingsAccountResult(
                account,
                SpoolSavingsStatus.ERROR,
                error=f"SQLcl exited 0 but spool file is missing: {out_path.name}",
                branch=branch,
            )
            if on_status:
                on_status(account, r.status, r.error)
            return r

        r = SavingsAccountResult(account, SpoolSavingsStatus.OK, output_path=out_path, branch=branch)
        if on_status:
            on_status(account, r.status, "")
        return r

    def extract_many(
        self,
        country: str,
        accounts: Iterable[str],
        connection: str,
        on_status: SavingsStatusCallback | None = None,
        max_workers: int = MAX_PARALLEL_SAVINGS_ACCOUNTS,
        cancel_event: threading.Event | None = None,
    ) -> list[SavingsAccountResult]:
        account_list = list(accounts)
        workers = worker_count_for(len(account_list), max_workers)
        if workers == 0:
            return []
        if workers == 1:
            return [
                self.extract_one(country, account, connection, on_status, cancel_event)
                for account in account_list
            ]

        results: list[SavingsAccountResult | None] = [None] * len(account_list)
        next_index = 0
        pending: set[Future] = set()
        future_to_index: dict[Future, int] = {}

        def submit_available(executor: ThreadPoolExecutor) -> None:
            nonlocal next_index
            while next_index < len(account_list) and len(pending) < workers and not _is_cancelled(cancel_event):
                idx = next_index
                future = executor.submit(
                    self.extract_one,
                    country,
                    account_list[idx],
                    connection,
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
                    try:
                        results[idx] = future.result()
                    except Exception as exc:
                        log.exception("Unhandled savings extraction error for %s", account_list[idx])
                        result = SavingsAccountResult(account_list[idx], SpoolSavingsStatus.ERROR, error=str(exc))
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
        on_status: SavingsStatusCallback | None = None,
        cancel_event: threading.Event | None = None,
    ) -> SavingsAccountResult:
        if _is_cancelled(cancel_event):
            r = _cancelled_result(account, spool_path)
            if on_status:
                on_status(account, r.status, r.error)
            return r

        if not spool_path.exists():
            r = SavingsAccountResult(account, SpoolSavingsStatus.ERROR, error=f"Spool not found: {spool_path.name}")
            if on_status:
                on_status(account, r.status, r.error)
            return r

        if on_status:
            on_status(account, SpoolSavingsStatus.RUNNING, "")

        rendered = self._render_existing_spool(spool_path)
        try:
            result = self.runner.run_script(
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
            r = SavingsAccountResult(account, SpoolSavingsStatus.ERROR, output_path=spool_path, error=_tail_error(result))
            if on_status:
                on_status(account, r.status, r.error)
            return r

        r = SavingsAccountResult(account, SpoolSavingsStatus.OK, output_path=spool_path)
        if on_status:
            on_status(account, r.status, "")
        return r

    def apply_many(
        self,
        items: Iterable[tuple[str, Path]],
        connection: str,
        on_status: SavingsStatusCallback | None = None,
        max_workers: int = MAX_PARALLEL_SAVINGS_ACCOUNTS,
        cancel_event: threading.Event | None = None,
    ) -> list[SavingsAccountResult]:
        item_list = list(items)
        workers = worker_count_for(len(item_list), max_workers)
        if workers == 0:
            return []
        if workers == 1:
            return [
                self.apply_one(account, connection, spool_path, on_status, cancel_event)
                for account, spool_path in item_list
            ]

        results: list[SavingsAccountResult | None] = [None] * len(item_list)
        next_index = 0
        pending: set[Future] = set()
        future_to_index: dict[Future, int] = {}

        def submit_available(executor: ThreadPoolExecutor) -> None:
            nonlocal next_index
            while next_index < len(item_list) and len(pending) < workers and not _is_cancelled(cancel_event):
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
                        log.exception("Unhandled savings apply error for %s", account)
                        result = SavingsAccountResult(account, SpoolSavingsStatus.ERROR, output_path=spool_path, error=str(exc))
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
