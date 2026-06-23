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
from datetime import datetime
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
    VERIFIED = "verified"
    ERROR = "error"
    CANCELLED = "cancelled"
    WARNING = "warning"


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

_VERIFY_TABLES: tuple[str, ...] = (
    "STTM_CUST_ACCOUNT",
    "STTB_ACCOUNT",
    "ICTM_ACC",
    "ICTB_ACC_PR",
    "ICTB_ENTRIES_HISTORY",
    "ACTB_VD_BAL",
    "ICTB_ITM_TOV",
)
_VERIFY_TABLE_SPECS: tuple[tuple[str, str, str, str | None], ...] = (
    ("STTM_CUST_ACCOUNT", "sttm_cust_account", "cust_ac_no", "branch_code"),
    ("STTB_ACCOUNT", "sttb_account", "ac_gl_no", "branch_code"),
    ("ICTM_ACC", "ictm_acc", "acc", "brn"),
    ("ICTB_ACC_PR", "ictb_acc_pr", "acc", "brn"),
    ("ICTB_ENTRIES_HISTORY", "ictb_entries_history", "acc", "brn"),
    ("ACTB_VD_BAL", "actb_vd_bal", "acc", "brn"),
    ("ICTB_ITM_TOV", "ictb_itm_tov", "acc", "brn"),
)
_INSERT_TABLE_RE = re.compile(r"^\s*insert\s+into\s+([a-z0-9_]+)\s*\(", re.IGNORECASE)
_GENERATED_HEADER_RE = re.compile(
    r"--\s*IC account data generated for branch\s+(\S+)\s+account\s+(\S+)",
    re.IGNORECASE,
)
_VERIFY_ROW_RE = re.compile(r"^\s*([A-Z0-9_]+)\s*=\s*(-?\d+)\s*$", re.IGNORECASE)
_GENERATION_ERROR_MARKER = "IC ACCOUNT DATA SCRIPT COMPLETED WITH GENERATION ERRORS"
_DB_FINAL_MARKER = "IC_DB_FINAL_OK|"
_DB_PROGRESS_RE = re.compile(
    r"^\s*IC_DB_(CHECKPOINT|FINAL)_OK\|(\d+)\|([^\r\n]+)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_CRITICAL_SQLCL_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bSQL Error:\s*Closed\s+Connection\b", re.IGNORECASE), "SQLcl closed connection"),
    (re.compile(r"\bClosed\s+Connection\b", re.IGNORECASE), "SQLcl closed connection"),
    (re.compile(r"\bORA-03113\b", re.IGNORECASE), "ORA-03113 end-of-file on communication channel"),
    (re.compile(r"\bORA-03114\b", re.IGNORECASE), "ORA-03114 not connected to Oracle"),
    (re.compile(r"\bORA-03135\b", re.IGNORECASE), "ORA-03135 connection lost contact"),
    (re.compile(r"\bORA-01012\b", re.IGNORECASE), "ORA-01012 not logged on"),
    (re.compile(r"\bORA-01033\b", re.IGNORECASE), "ORA-01033 initialization/shutdown in progress"),
    (re.compile(r"\bORA-01034\b", re.IGNORECASE), "ORA-01034 Oracle not available"),
    (re.compile(r"\bORA-01089\b", re.IGNORECASE), "ORA-01089 immediate shutdown in progress"),
    (re.compile(r"\bORA-12154\b", re.IGNORECASE), "ORA-12154 connect identifier could not be resolved"),
    (re.compile(r"\bORA-125\d{2}\b", re.IGNORECASE), "Oracle network/listener error"),
    (re.compile(r"\bSP2-\d{4}\b", re.IGNORECASE), "SQLcl/SP2 client error"),
    (re.compile(r"\bPLS-\d{4}\b", re.IGNORECASE), "PL/SQL error"),
)


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


def savings_output_path_for(country: str, account: str, output_dir: Path | None = None) -> Path:
    filename = f"IC_account_data_{account}.INC"
    if output_dir is not None:
        return Path(output_dir) / filename
    folder = _COUNTRY_FOLDER.get(country.lower(), country.title())
    return SPOOLS_SAVINGS_OUT_DIR / folder / filename


def worker_count_for(account_count: int, max_workers: int = MAX_PARALLEL_SAVINGS_ACCOUNTS) -> int:
    if account_count <= 0:
        return 0
    return min(account_count, max(1, max_workers))


def _with_exit(sql_text: str) -> str:
    if sql_text.rstrip().lower().endswith(("exit;", "exit")):
        return sql_text
    return sql_text.rstrip() + "\nexit;\n"


def _ignore_duplicate_inserts(sql_text: str) -> str:
    if "DUP_VAL_ON_INDEX" in sql_text:
        return sql_text

    lines = sql_text.splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if (
            re.match(r"^\s*INSERT\s+INTO\b", line, re.IGNORECASE)
            and i + 1 < len(lines)
            and re.match(r"^\s*VALUES\b", lines[i + 1], re.IGNORECASE)
        ):
            out.append("BEGIN")
            out.append(line)
            out.append(lines[i + 1])
            out.append("EXCEPTION")
            out.append("  WHEN DUP_VAL_ON_INDEX THEN NULL;")
            out.append("END;")
            out.append("/")
            i += 2
            continue
        out.append(line)
        i += 1
    return "\n".join(out) + ("\n" if sql_text.endswith("\n") else "")


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
    tail = _combined_output(result).strip().splitlines()
    return tail[-1][:240] if tail else f"exit {result.exit_code}"


def _combined_output(result: RunResult) -> str:
    parts = []
    if result.stdout:
        parts.append(result.stdout)
    if result.stderr:
        parts.append(result.stderr)
    return "\n".join(parts)


def _line_for_offset(text: str, offset: int) -> str:
    start = text.rfind("\n", 0, offset) + 1
    end = text.find("\n", offset)
    if end == -1:
        end = len(text)
    return text[start:end].strip()


def _critical_sqlcl_error(result: RunResult) -> str:
    output = _combined_output(result)
    for pattern, description in _CRITICAL_SQLCL_PATTERNS:
        match = pattern.search(output)
        if match:
            line = _line_for_offset(output, match.start())
            return f"{description}: {line[:220]}"
    return ""


def _apply_log_path(spool_path: Path) -> Path:
    return spool_path.with_name(f"{spool_path.stem}_apply.log")


def _write_apply_log(account: str, spool_path: Path, result: RunResult) -> Path | None:
    log_path = _apply_log_path(spool_path)
    body = [
        f"Applied at: {datetime.now().isoformat(timespec='seconds')}",
        f"Account: {account}",
        f"Spool: {spool_path}",
        f"Exit code: {result.exit_code}",
        "",
        "=== STDOUT ===",
        result.stdout or "",
        "",
        "=== STDERR ===",
        result.stderr or "",
    ]
    try:
        log_path.write_text("\n".join(body), encoding="utf-8", errors="replace")
        return log_path
    except OSError as exc:
        log.warning("Could not write savings apply log %s: %s", log_path, exc)
        return None


def _inspect_spool(spool_path: Path) -> tuple[dict[str, int], str]:
    counts = {table: 0 for table in _VERIFY_TABLES}
    branch = ""
    try:
        with spool_path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if not branch:
                    header = _GENERATED_HEADER_RE.search(line)
                    if header:
                        branch = header.group(1).upper()

                insert = _INSERT_TABLE_RE.match(line)
                if insert:
                    table = insert.group(1).upper()
                    if table in counts:
                        counts[table] += 1
    except OSError as exc:
        log.warning("Could not inspect savings spool %s: %s", spool_path, exc)
    return counts, branch


def _db_progress(result: RunResult) -> tuple[bool, str]:
    final_seen = False
    last_kind = ""
    last_dml = ""
    last_reason = ""
    for match in _DB_PROGRESS_RE.finditer(_combined_output(result)):
        last_kind = match.group(1).upper()
        last_dml = match.group(2)
        last_reason = match.group(3).strip()
        if last_kind == "FINAL":
            final_seen = True

    if not last_kind:
        return False, "no DB progress marker reached"

    label = "final marker" if last_kind == "FINAL" else "last checkpoint"
    return final_seen, f"{label}: {last_dml} DML - {last_reason}"


def _requires_final_db_marker(spool_path: Path) -> bool:
    try:
        with spool_path.open("r", encoding="utf-8", errors="replace") as fh:
            return any(_DB_FINAL_MARKER in line for line in fh)
    except OSError as exc:
        log.warning("Could not inspect savings spool markers %s: %s", spool_path, exc)
        return False


def _spool_generation_error(spool_path: Path) -> str:
    try:
        with spool_path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if _GENERATION_ERROR_MARKER in line:
                    return "Spool was generated with fatal errors; regenerate it before applying."
    except OSError as exc:
        return f"Could not read spool before apply: {exc}"
    return ""


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
        text = _ignore_duplicate_inserts(text)
        out = Path(tempfile.gettempdir()) / f"oracle_tasks_savings_apply_{uuid.uuid4().hex[:8]}.sql"
        out.write_text(_with_exit(text), encoding="utf-8")
        return out

    def _verify_account_apply(
        self,
        account: str,
        connection: str,
        spool_path: Path,
        cancel_event: threading.Event | None = None,
    ) -> tuple[str, str]:
        expected, branch = _inspect_spool(spool_path)
        selects: list[str] = []
        for label, table, account_col, branch_col in _VERIFY_TABLE_SPECS:
            where = f"{account_col} = {_sql_literal(account)}"
            if branch and branch_col:
                where += f" and {branch_col} = {_sql_literal(branch)}"
            selects.append(f"select '{label}=' || count(*) from {table} where {where}")

        result = self.runner.run_query(
            connection,
            "\nunion all\n".join(selects),
            timeout=180,
            cancel_event=cancel_event,
        )
        if result.exit_code == 130 or _is_cancelled(cancel_event):
            return "unavailable", "verification cancelled"
        if not result.ok:
            return "unavailable", f"verification query failed: {_tail_error(result)}"

        critical = _critical_sqlcl_error(result)
        if critical:
            return "unavailable", f"verification query failed: {critical}"

        actual: dict[str, int] = {}
        for line in (result.stdout or "").splitlines():
            match = _VERIFY_ROW_RE.match(line)
            if match:
                actual[match.group(1).upper()] = int(match.group(2))

        failures: list[str] = []
        for table in ("STTM_CUST_ACCOUNT", "STTB_ACCOUNT", "ICTM_ACC"):
            if actual.get(table, 0) < 1:
                failures.append(f"{table} missing")

        for table in _VERIFY_TABLES:
            expected_count = expected.get(table, 0)
            if expected_count <= 0:
                continue
            actual_count = actual.get(table)
            if actual_count is None:
                failures.append(f"{table} not returned")
            elif actual_count != expected_count:
                failures.append(f"{table} expected {expected_count}, found {actual_count}")

        if failures:
            suffix = f" for branch {branch}" if branch else ""
            return "failed", "post-apply verification failed" + suffix + ": " + "; ".join(failures[:8])

        compared = ", ".join(
            f"{table}={expected[table]}"
            for table in _VERIFY_TABLES
            if expected.get(table, 0) > 0
        )
        return "ok", f"verified key tables{(' for branch ' + branch) if branch else ''}: {compared or 'existence checks only'}"

    def extract_one(
        self,
        country: str,
        account: str,
        connection: str,
        on_status: SavingsStatusCallback | None = None,
        cancel_event: threading.Event | None = None,
        output_dir: Path | None = None,
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

        out_path = savings_output_path_for(country, account, output_dir)
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

        generation_error = _spool_generation_error(out_path)
        if generation_error:
            r = SavingsAccountResult(account, SpoolSavingsStatus.ERROR, output_path=out_path, error=generation_error, branch=branch)
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
        output_dir: Path | None = None,
    ) -> list[SavingsAccountResult]:
        account_list = list(accounts)
        workers = worker_count_for(len(account_list), max_workers)
        if workers == 0:
            return []
        if workers == 1:
            return [
                self.extract_one(country, account, connection, on_status, cancel_event, output_dir)
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
                    output_dir,
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
        verify_after_apply: bool = True,
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

        generation_error = _spool_generation_error(spool_path)
        if generation_error:
            r = SavingsAccountResult(account, SpoolSavingsStatus.ERROR, output_path=spool_path, error=generation_error)
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
                cancel_event=cancel_event,
            )
        finally:
            try:
                rendered.unlink()
            except OSError:
                pass

        log_path = _write_apply_log(account, spool_path, result)
        log_hint = f" Log: {log_path.name}" if log_path else ""

        if result.exit_code == 130 or _is_cancelled(cancel_event):
            r = _cancelled_result(account, spool_path)
            if on_status:
                on_status(account, r.status, r.error)
            return r

        final_marker_seen, progress_message = _db_progress(result)
        requires_final_marker = _requires_final_db_marker(spool_path)

        if not result.ok:
            error = f"{_tail_error(result)}. {progress_message}.{log_hint}".strip()
            r = SavingsAccountResult(account, SpoolSavingsStatus.ERROR, output_path=spool_path, error=error)
            if on_status:
                on_status(account, r.status, r.error)
            return r

        critical = _critical_sqlcl_error(result)
        if critical:
            error = f"{critical}. {progress_message}.{log_hint}".strip()
            r = SavingsAccountResult(account, SpoolSavingsStatus.ERROR, output_path=spool_path, error=error)
            if on_status:
                on_status(account, r.status, r.error)
            return r

        if requires_final_marker and not final_marker_seen:
            error = f"Apply did not reach final DB marker. {progress_message}.{log_hint}".strip()
            r = SavingsAccountResult(account, SpoolSavingsStatus.ERROR, output_path=spool_path, error=error)
            if on_status:
                on_status(account, r.status, r.error)
            return r

        if not verify_after_apply:
            message = f"injected; post-apply verification disabled.{log_hint}".strip()
            r = SavingsAccountResult(account, SpoolSavingsStatus.OK, output_path=spool_path)
            if on_status:
                on_status(account, r.status, message)
            return r

        if on_status:
            on_status(account, SpoolSavingsStatus.RUNNING, "verifying...")
        verify_status, verification_message = self._verify_account_apply(
            account,
            connection,
            spool_path,
            cancel_event,
        )

        if verify_status == "failed":
            error = f"{verification_message}. {progress_message}.{log_hint}".strip()
            r = SavingsAccountResult(account, SpoolSavingsStatus.ERROR, output_path=spool_path, error=error)
            if on_status:
                on_status(account, r.status, r.error)
            return r

        if verify_status == "unavailable":
            warning = f"Warning: apply finished, but verification could not run. {verification_message}.{log_hint}".strip()
            r = SavingsAccountResult(account, SpoolSavingsStatus.WARNING, output_path=spool_path, error=warning)
            if on_status:
                on_status(account, r.status, warning)
            return r

        r = SavingsAccountResult(account, SpoolSavingsStatus.VERIFIED, output_path=spool_path)
        if on_status:
            on_status(account, r.status, verification_message)
        return r

    def apply_many(
        self,
        items: Iterable[tuple[str, Path]],
        connection: str,
        on_status: SavingsStatusCallback | None = None,
        max_workers: int = MAX_PARALLEL_SAVINGS_ACCOUNTS,
        cancel_event: threading.Event | None = None,
        verify_after_apply: bool = True,
    ) -> list[SavingsAccountResult]:
        item_list = list(items)
        workers = worker_count_for(len(item_list), max_workers)
        if workers == 0:
            return []
        if workers == 1:
            return [
                self.apply_one(
                    account,
                    connection,
                    spool_path,
                    on_status,
                    cancel_event,
                    verify_after_apply,
                )
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
                    verify_after_apply,
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
