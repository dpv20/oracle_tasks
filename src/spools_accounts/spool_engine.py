"""SpoolEngine — orchestrates account spool extraction.

Phase 3 implements EXTRACT_ONLY: render `.sql.tmpl` → run on source DB →
the spool `.SQL` file lands in `SPOOLS_OUT_DIR/<Country>/`. Future phases
add EXTRACT_AND_APPLY (Phase 4) and APPLY_EXISTING (Phase 5).

Threading: the engine is blocking by design. The UI calls `extract_one` /
`extract_many` from a worker thread and marshals status callbacks back via
`Tk.after(0, ...)`. Batch extraction uses a small worker pool so long runs can
move several accounts at once. Errors per account never abort the batch.
"""
from __future__ import annotations

import logging
import re
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, Iterable

from paths import SPOOLS_OUT_DIR, SPOOLS_SQL_DIR
from spools_accounts.sqlcl import RunResult, SqlclRunner

log = logging.getLogger(__name__)


class SpoolStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    OK = "ok"
    ERROR = "error"


@dataclass
class AccountResult:
    account: str
    status: SpoolStatus
    output_path: Path | None = None
    error: str = ""


# Lowercase country id → folder name under SPOOLS_OUT_DIR (matches paths.ensure_dirs).
_COUNTRY_FOLDER = {
    "chile":    "Chile",
    "peru":     "Peru",
    "colombia": "Colombia",
    "mexico":   "Mexico",
}

# Conservative account regex: alphanumerics + underscore/dash, length 3..40.
# Same chars used by the original SQL substitution & by the generated filename.
_ACCOUNT_RE = re.compile(r"^[A-Za-z0-9_-]{3,40}$")

StatusCallback = Callable[[str, SpoolStatus, str], None]

MAX_PARALLEL_ACCOUNTS = 3


def template_path(country: str) -> Path:
    return SPOOLS_SQL_DIR / f"CL_ACCOUNT_SPOOL_{country.upper()}.sql.tmpl"


def has_template(country: str) -> bool:
    return template_path(country).is_file()


def is_valid_account(s: str) -> bool:
    return bool(_ACCOUNT_RE.match(s.strip()))


def parse_accounts(text: str) -> tuple[list[str], list[str]]:
    """Split a textarea blob into valid + invalid account ids.

    Accepts one per line plus comma/space separators. Strips inline comments
    starting with `#`. De-duplicates while preserving order.
    """
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


def output_path_for(country: str, account: str) -> Path:
    folder = _COUNTRY_FOLDER.get(country.lower(), country.title())
    return SPOOLS_OUT_DIR / folder / f"CL_Acc_Spool_{account}.SQL"


def worker_count_for(account_count: int, max_workers: int = MAX_PARALLEL_ACCOUNTS) -> int:
    if account_count <= 0:
        return 0
    return min(account_count, max(1, max_workers))


class SpoolEngine:
    """EXTRACT_ONLY: run the country template against a source DB.

    Output spool files land in SPOOLS_OUT_DIR/<Country>/.
    """

    def __init__(self, runner: SqlclRunner):
        self.runner = runner

    def _render_template(self, country: str) -> Path:
        """Materialize a temp .sql with {{SPOOL_OUT_DIR}} substituted.

        Appends `exit;` if the template doesn't already end with one — without
        it SQLcl runs the script and then sits at the prompt waiting for input,
        so the subprocess only returns when our timeout fires.
        """
        tmpl = template_path(country)
        text = tmpl.read_text(encoding="utf-8")
        rendered = text.replace("{{SPOOL_OUT_DIR}}", str(SPOOLS_OUT_DIR))
        if not rendered.rstrip().lower().endswith(("exit;", "exit")):
            rendered = rendered.rstrip() + "\nexit;\n"
        out = Path(tempfile.gettempdir()) / f"oracle_tasks_{country.lower()}_{uuid.uuid4().hex[:8]}.sql"
        out.write_text(rendered, encoding="utf-8")
        return out

    def extract_one(
        self,
        country: str,
        account: str,
        connection: str,
        on_status: StatusCallback | None = None,
    ) -> AccountResult:
        if not _ACCOUNT_RE.match(account):
            r = AccountResult(account, SpoolStatus.ERROR, error="Invalid account format")
            if on_status:
                on_status(account, r.status, r.error)
            return r

        if not has_template(country):
            r = AccountResult(account, SpoolStatus.ERROR,
                              error=f"No spool template for country '{country}'")
            if on_status:
                on_status(account, r.status, r.error)
            return r

        if on_status:
            on_status(account, SpoolStatus.RUNNING, "")

        out_path = output_path_for(country, account)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        # Pre-clean stale file so existence on success means a fresh write.
        if out_path.exists():
            try:
                out_path.unlink()
            except OSError as e:
                log.warning("Could not remove stale spool %s: %s", out_path, e)

        rendered = self._render_template(country)
        try:
            # Wallclock cap per account. Most spools finish in under 90 s but
            # some legit cases (slow network, heavy account) can take 5-10 min;
            # 30 min is generous enough to never kill a working extraction.
            result: RunResult = self.runner.run_script(connection, rendered, [account], timeout=1800)
        finally:
            try:
                rendered.unlink()
            except OSError:
                pass

        if not result.ok:
            tail = (result.stderr or result.stdout or "").strip().splitlines()
            err = tail[-1][:240] if tail else f"exit {result.exit_code}"
            r = AccountResult(account, SpoolStatus.ERROR, error=err)
            if on_status:
                on_status(account, r.status, r.error)
            return r

        if not out_path.exists():
            r = AccountResult(account, SpoolStatus.ERROR,
                              error=f"SQLcl exited 0 but spool file is missing: {out_path.name}")
            if on_status:
                on_status(account, r.status, r.error)
            return r

        r = AccountResult(account, SpoolStatus.OK, output_path=out_path)
        if on_status:
            on_status(account, r.status, "")
        return r

    def extract_many(
        self,
        country: str,
        accounts: Iterable[str],
        connection: str,
        on_status: StatusCallback | None = None,
        max_workers: int = MAX_PARALLEL_ACCOUNTS,
    ) -> list[AccountResult]:
        account_list = list(accounts)
        workers = worker_count_for(len(account_list), max_workers)
        if workers == 0:
            return []
        if workers == 1:
            return [self.extract_one(country, acc, connection, on_status) for acc in account_list]

        results: list[AccountResult | None] = [None] * len(account_list)
        with ThreadPoolExecutor(max_workers=workers) as executor:
            future_to_index = {
                executor.submit(self.extract_one, country, acc, connection, on_status): idx
                for idx, acc in enumerate(account_list)
            }
            for future in as_completed(future_to_index):
                idx = future_to_index[future]
                try:
                    results[idx] = future.result()
                except Exception as exc:
                    log.exception("Unhandled spool extraction error for %s", account_list[idx])
                    result = AccountResult(account_list[idx], SpoolStatus.ERROR, error=str(exc))
                    results[idx] = result
                    if on_status:
                        on_status(result.account, result.status, result.error)

        return [r for r in results if r is not None]
