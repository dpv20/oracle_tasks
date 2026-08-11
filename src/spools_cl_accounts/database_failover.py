"""SQLcl source-connection failover for explicitly equivalent PROD databases."""
from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

from settings.database_failover import alternative_alias
from spools_cl_accounts.databases import configured_databases
from spools_cl_accounts.sqlcl import RunResult, SqlclRunner

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ConnectionCandidate:
    alias: str
    connection: str = field(repr=False)


_CONNECTION_ORA_RE = re.compile(
    r"\bORA-(?:"
    r"01012|01033|01034|01089|"
    r"03113|03114|03135|"
    r"12154|12162|"
    r"12504|12505|12514|12516|12518|12519|12520|12521|12525|12528|"
    r"12535|12537|12541|12543|12545|12547|12560|12571|12637|17002"
    r")\b",
    re.IGNORECASE,
)
_CONNECTION_TEXT_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"listener does not currently know",
        r"no listener",
        r"network adapter could not establish",
        r"connection (?:was )?refused",
        r"connection (?:was )?reset",
        r"closed connection",
        r"not connected to oracle",
        r"connect(?:ion)? timed out",
        r"could not resolve (?:the )?connect identifier",
        r"io error:\s*(?:the network adapter|connection|socket)",
        r"tns:.*(?:listener|connect|resolve)",
    )
)


def connection_failure_reason(result: RunResult) -> str | None:
    """Return a safe reason when a SQLcl result represents connectivity loss."""
    if result.ok or result.exit_code in {124, 130}:
        return None
    output = "\n".join((result.stderr or "", result.stdout or ""))
    match = _CONNECTION_ORA_RE.search(output)
    if match:
        return match.group(0).upper()
    for pattern in _CONNECTION_TEXT_PATTERNS:
        if pattern.search(output):
            return "Oracle connection error"
    return None


def fallback_candidates_for_source(
    config,
    country: str,
    source_database: dict,
    connection_builder: Callable[[dict, str], str],
) -> tuple[ConnectionCandidate, ...]:
    """Resolve a saved credential for the selected source's equivalent alias.

    The same login key is preferred. If the alternative alias has only one PROD
    credential, that credential is unambiguous and can also be used.
    """
    selected_alias = str(source_database.get("id") or "").strip()
    fallback_alias = alternative_alias(
        country,
        selected_alias,
        config.get("database_failover", {}),
    )
    if not fallback_alias:
        return ()

    credentials = config.all_credentials()
    rows = [
        row
        for row in configured_databases(credentials, country, ("prod",))
        if str(row.get("id") or "").upper() == fallback_alias.upper()
    ]
    if not rows:
        log.info(
            "Database failover unavailable: country=%s source=%s alternative=%s has no PROD credential",
            country,
            selected_alias,
            fallback_alias,
        )
        return ()

    source_credential_key = str(source_database.get("credential_key") or "").upper()
    matching_login = [
        row
        for row in rows
        if source_credential_key
        and str(row.get("credential_key") or "").upper() == source_credential_key
    ]
    if matching_login:
        selected_row = matching_login[0]
    elif len(rows) == 1:
        selected_row = rows[0]
    else:
        log.warning(
            "Database failover unavailable: country=%s source=%s alternative=%s has ambiguous credentials",
            country,
            selected_alias,
            fallback_alias,
        )
        return ()

    credential = config.get_credential(
        country,
        str(selected_row.get("database_key") or fallback_alias),
        str(selected_row.get("credential_key") or "") or None,
    )
    if not credential:
        log.warning(
            "Database failover credential disappeared: country=%s alternative=%s",
            country,
            fallback_alias,
        )
        return ()

    return (
        ConnectionCandidate(
            alias=fallback_alias,
            connection=connection_builder(credential, fallback_alias),
        ),
    )


class FailoverSqlclRunner:
    """Delegate SQLcl calls and retry connectivity errors on equivalent aliases.

    Only calls made with ``primary.connection`` participate in failover. Calls
    with destination credentials are passed directly to the underlying runner,
    which keeps apply/injection behavior unchanged.
    """

    def __init__(
        self,
        runner: SqlclRunner,
        primary: ConnectionCandidate,
        alternatives: Iterable[ConnectionCandidate],
    ) -> None:
        self._runner = runner
        self._primary_connection = primary.connection
        self._candidates = (primary, *tuple(alternatives))
        self._preferred_alias = primary.alias
        self._lock = threading.Lock()

    @property
    def exe(self) -> str:
        return self._runner.exe

    def _ordered_candidates(self) -> tuple[ConnectionCandidate, ...]:
        with self._lock:
            preferred = self._preferred_alias
        return tuple(
            sorted(
                self._candidates,
                key=lambda candidate: candidate.alias.upper() != preferred.upper(),
            )
        )

    def _remember(self, alias: str) -> None:
        with self._lock:
            self._preferred_alias = alias

    def _run(self, operation: Callable[[str], RunResult]) -> RunResult:
        candidates = self._ordered_candidates()
        last_result: RunResult | None = None
        for index, candidate in enumerate(candidates):
            result = operation(candidate.connection)
            last_result = result
            reason = connection_failure_reason(result)
            if not reason:
                self._remember(candidate.alias)
                if index > 0:
                    log.info("Database failover connected using %s", candidate.alias)
                return result
            if index + 1 >= len(candidates):
                break
            next_alias = candidates[index + 1].alias
            log.warning(
                "Database connection failed for %s (%s); trying equivalent database %s",
                candidate.alias,
                reason,
                next_alias,
            )
        assert last_result is not None
        return last_result

    def run_query(
        self,
        connection: str,
        sql: str,
        timeout: float = 30.0,
        cancel_event: threading.Event | None = None,
    ) -> RunResult:
        if connection != self._primary_connection or len(self._candidates) == 1:
            return self._runner.run_query(connection, sql, timeout, cancel_event)
        return self._run(
            lambda candidate_connection: self._runner.run_query(
                candidate_connection,
                sql,
                timeout,
                cancel_event,
            )
        )

    def run_script(
        self,
        connection: str,
        script_path: str | Path,
        args: list[str] | None = None,
        timeout: float | None = None,
        cancel_event: threading.Event | None = None,
    ) -> RunResult:
        if connection != self._primary_connection or len(self._candidates) == 1:
            return self._runner.run_script(connection, script_path, args, timeout, cancel_event)
        return self._run(
            lambda candidate_connection: self._runner.run_script(
                candidate_connection,
                script_path,
                args,
                timeout,
                cancel_event,
            )
        )
