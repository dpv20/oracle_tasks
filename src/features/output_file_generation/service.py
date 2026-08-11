"""Read-only Oracle service that reconstructs supported outgoing GI files."""
from __future__ import annotations

import hashlib
import logging
import os
import re
import tempfile
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from paths import OUTPUT_FILES_OUT_DIR
from settings.config import ConfigManager, decrypt_password
from settings.credentials import to_sqlcl_arg
from settings.database_failover import alternative_alias
from spools_cl_accounts.database_failover import connection_failure_reason
from spools_cl_accounts.databases import configured_databases, find_db
from spools_cl_accounts.sqlcl import RunResult, SqlclRunner

from .formats import (
    CHISALOU_CONTRACTS,
    InterfaceSpec,
    OFICOWCG_TRANSACTION_CONTRACTS,
    build_footer,
    build_header,
    format_error_code,
    format_error_description,
    format_error_description_list,
    format_form_message,
    format_oacmclos_error_descriptions,
    normalize_interface_code,
    physical_filename,
    serialize_lines,
    split_error_codes,
    split_oacmclos_error_codes,
    spec_for_code,
    supported_specs,
    validate_file_date,
    validate_output_lines,
    validate_process_ref,
)
from .upload_adapters import upload_body_adapter
from .models import (
    DataSourceChoice,
    GenerationRequest,
    GenerationResult,
    OracleTarget,
    ProcessCandidate,
)
from .queries import (
    FramedQuery,
    build_body_query,
    build_chisalca_teller_query,
    build_database_time_query,
    build_discovery_query,
    build_error_message_details_query,
    build_error_messages_query,
    build_file_date_query,
    build_footer_status_query,
    build_input_physical_filename_query,
    build_interface_last_run_date_query,
    build_mappings_query,
    build_oficowcg_coverage_query,
    build_process_contract_query,
    build_qsimtp_snapshot_guard_query,
    build_stdinrou_dependency_guard_query,
    build_udf_details_owner_query,
    output_outside_markers,
    parse_framed_output,
)


log = logging.getLogger(__name__)

_COUNTRY_FOLDERS = {
    "chile": "Chile",
    "peru": "Peru",
    "colombia": "Colombia",
    "mexico": "Mexico",
}


def _uses_transactional_oficowcg_contract(spec: InterfaceSpec) -> bool:
    return spec.contract in OFICOWCG_TRANSACTION_CONTRACTS
_SQL_ERROR_RE = re.compile(
    r"\b(?:ORA|SP2|PLS)-\d{4,5}\b|\bSQL\s+Error\b|\bClosed\s+Connection\b",
    re.IGNORECASE,
)
_CONNECTION_RE = re.compile(r"\b[^\s/]+(?:\[[^\]]+\])?/[^\s@]+@[^\s]+", re.IGNORECASE)
_SQLCL_LOGIN_USER_RE = re.compile(r"^\s*USER\s*=", re.IGNORECASE)
_SQLCL_LOGIN_URL_RE = re.compile(r"^\s*URL\s*=", re.IGNORECASE)
_SQLCL_LOGIN_ERROR_RE = re.compile(r"^\s*Error Message\s*=\s*(.*)$", re.IGNORECASE)
_ORACLE_IDENTIFIER_RE = re.compile(r"^[A-Z][A-Z0-9_$#]{0,127}$")
_CHISALCA_TELLER_BATCH_SIZE = 500


@dataclass(frozen=True)
class _BodyRecord:
    status: str
    body_prefix: str
    error_code: str = ""
    error_param: str = ""
    fields: tuple[str, ...] = ()
    output_status: str = ""


@dataclass(frozen=True)
class _ChisalcaUploadRow:
    normalized_status: str
    raw_status: str
    xref: str
    fallback_account: str
    fallback_branch: str
    fallback_amount: str
    fallback_date: str
    fallback_currency: str
    ccicode: str
    error_code: str
    error_param: str


@dataclass(frozen=True)
class _ChisalcaTellerRow:
    xref: str
    count: str
    transaction_reference: str
    output_xref: str
    account: str
    branch: str
    amount: str
    transaction_date: str
    currency: str


@dataclass(frozen=True)
class _QsimtpSnapshotMetadata:
    upload_rows: int
    distinct_record_references: int
    distinct_alt_accounts: int
    processed_uploads: int
    failed_uploads: int
    other_status_rows: int
    processed_with_ten_rows: int
    processed_bad_log_rows: int
    failed_with_log_rows: int
    matched_live_rows: int
    live_rows: int
    unique_live_keys: int
    live_accounts: int
    live_sim_dates: int
    null_live_keys: int
    cursor_ties: int
    active_upload_rows: int
    active_file_log_rows: int
    active_requested_file_log_rows: int
    latest_archive_upload_process: str
    latest_archive_file_log_process: str
    in_progress_control_rows: int

    @property
    def expected_body_rows(self) -> int:
        return self.live_rows + self.failed_uploads


@dataclass(frozen=True)
class _StdinrouDependencyMetadata:
    custom_rows: int
    custom_distinct_references: int
    standard_rows: int
    standard_distinct_references: int
    standard_interface_rows: int
    custom_missing_from_standard: int
    standard_missing_from_custom: int
    custom_null_statuses: int
    standard_null_statuses: int
    custom_processed: int
    custom_unprocessed: int
    standard_processed: int
    standard_unprocessed: int
    custom_filename_count: int
    custom_physical_filename: str


@dataclass(frozen=True)
class _OficowcgCoverageMetadata:
    upload_rows: int
    nonnull_record_references: int
    distinct_record_references: int
    uploads_with_one_gic: int
    uploads_with_one_output: int
    uploads_without_output: int
    uploads_with_multiple_outputs: int
    branch_b_rows_without_one_owner: int


@dataclass(frozen=True)
class _OficowcgRegionalCoverageMetadata:
    upload_rows: int
    nonnull_record_references: int
    distinct_record_references: int
    uploads_with_output: int
    processed_gic_rows_without_one_owner: int
    cursor_rows: int
    cursor_rows_with_multiple_rejections: int
    rejected_rows_with_multiple_error_lookups: int


class OutputFileGenerationError(RuntimeError):
    """An actionable, already-sanitized generation failure."""


class GenerationCancelled(OutputFileGenerationError):
    """The user cancelled the SQLcl operation."""


class _RetryableDatabaseConnectionError(OutputFileGenerationError):
    """A structured connection failure that may restart the whole generation."""

    def __init__(self, message: str, *, reason: str, stage: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.stage = stage


def _sqlcl_login_diagnostic(stdout: str) -> str:
    """Extract only the error portion of SQLcl's structured login banner."""
    lines = (stdout or "").replace("\r\n", "\n").replace("\r", "\n").splitlines()
    if not any(_SQLCL_LOGIN_USER_RE.match(line) for line in lines):
        return ""
    if not any(_SQLCL_LOGIN_URL_RE.match(line) for line in lines):
        return ""
    error_indexes = [
        index
        for index, line in enumerate(lines)
        if _SQLCL_LOGIN_ERROR_RE.match(line)
    ]
    if len(error_indexes) != 1:
        return ""
    index = error_indexes[0]
    match = _SQLCL_LOGIN_ERROR_RE.match(lines[index])
    assert match is not None
    diagnostic = [match.group(1).strip()]
    for line in lines[index + 1 : index + 5]:
        stripped = line.strip()
        if re.match(r"^(?:ORA|TNS)-\d+\b", stripped, re.IGNORECASE):
            diagnostic.append(stripped)
    return "\n".join(part for part in diagnostic if part)


def _connection_failure_reason_for_query(
    result: RunResult,
    query: FramedQuery,
) -> str | None:
    """Classify connectivity without inspecting client rows inside the frame."""
    if result.exit_code in {124, 130}:
        return None
    stdout = (result.stdout or "").replace("\r\n", "\n").replace("\r", "\n")
    begin = stdout.find(query.begin_marker)
    end = stdout.find(query.end_marker)
    outside = ""
    # Without a complete frame arbitrary stdout cannot be separated safely from
    # rows already fetched for the client. The sole exception is SQLcl's exact
    # structured login banner, from which only the Error Message is extracted.
    if begin >= 0 and end >= begin:
        outside = output_outside_markers(stdout, query)
    elif begin < 0:
        outside = _sqlcl_login_diagnostic(stdout)
    diagnostic = "\n".join(
        part for part in (result.stderr or "", outside) if part
    )
    if not diagnostic.strip():
        return None
    # A few SQLcl versions have returned a zero exit code with an Oracle error
    # outside the markers. Use a synthetic failing result solely for the shared
    # connection-error classifier; framed client data is never included.
    classified = RunResult(
        result.exit_code if not result.ok else 1,
        "",
        diagnostic,
    )
    return connection_failure_reason(classified)


class OutputFileGenerationService:
    def __init__(
        self,
        config: ConfigManager,
        *,
        runner_factory: Callable[[str], SqlclRunner] = SqlclRunner,
        decryptor: Callable[[str], str] = decrypt_password,
        output_root: Path = OUTPUT_FILES_OUT_DIR,
    ) -> None:
        self.config = config
        self._runner_factory = runner_factory
        self._decryptor = decryptor
        self.output_root = Path(output_root)

    def targets(self, country: str = "chile") -> list[dict]:
        """Return configured PROD logins; saved credentials remain the truth source."""
        country_key = (country or "").strip().lower()
        _folder_for_country(country_key)
        return [
            row
            for row in configured_databases(
                self.config.all_credentials(),
                country=country_key,
                envs=("prod",),
            )
            if _catalog_allows_prod_target(country_key, str(row.get("id") or ""))
        ]

    @staticmethod
    def supported_interfaces() -> tuple[InterfaceSpec, ...]:
        return supported_specs()

    def generate(
        self,
        request: GenerationRequest,
        *,
        cancel_event: threading.Event | None = None,
    ) -> GenerationResult:
        _folder_for_country(request.target.country)
        process_ref = _as_generation_error(validate_process_ref, request.process_ref_no)
        selected_spec = _resolve_requested_interface(request.interface_code)
        requested_file_date = (
            _as_generation_error(validate_file_date, request.input_file_date)
            if request.input_file_date is not None
            else None
        )

        sqlcl_path = str(self.config.get("sqlcl_path", "") or "").strip()
        if not sqlcl_path or not Path(sqlcl_path).is_file():
            raise OutputFileGenerationError(
                "SQLcl is not configured or the selected executable no longer exists"
            )

        target = self._validated_prod_target(request.target)
        credential = self.config.get_credential(
            target.country,
            target.database_key,
            target.credential_key,
        )
        if not credential:
            raise OutputFileGenerationError("The selected PROD credential no longer exists")
        if not self._credential_still_matches_prod(target, credential):
            raise OutputFileGenerationError(
                "The selected credential is no longer configured for PROD"
            )

        trusted_request = replace(
            request,
            target=target,
            input_file_date=requested_file_date,
        )

        _raise_if_cancelled(cancel_event)
        password = ""
        connection = ""
        fallback_connection = ""
        try:
            password = self._decryptor(str(credential.get("password_enc") or ""))
            if not password:
                raise OutputFileGenerationError(
                    "The selected PROD password could not be decrypted for this Windows user"
                )
            connection = to_sqlcl_arg(
                str(credential.get("user") or ""),
                str(credential.get("schema") or "") or None,
                password,
                target.tns,
            )
            runner = self._runner_factory(sqlcl_path)
            try:
                return self._generate_connected(
                    request=trusted_request,
                    process_ref=process_ref,
                    runner=runner,
                    connection=connection,
                    cancel_event=cancel_event,
                    selected_spec=selected_spec,
                )
            except _RetryableDatabaseConnectionError as primary_failure:
                _raise_if_cancelled(cancel_event)
                fallback = self._alternative_prod_attempt(target)
                if fallback is None:
                    raise
                fallback_target, fallback_connection = fallback
                log.warning(
                    "Output database connection failed alias=%s stage=%s "
                    "reason=%s; restarting complete generation on equivalent alias=%s",
                    target.tns,
                    primary_failure.stage,
                    primary_failure.reason,
                    fallback_target.tns,
                )
                _raise_if_cancelled(cancel_event)
                fallback_request = replace(trusted_request, target=fallback_target)
                try:
                    return self._generate_connected(
                        request=fallback_request,
                        process_ref=process_ref,
                        runner=runner,
                        connection=fallback_connection,
                        cancel_event=cancel_event,
                        selected_spec=selected_spec,
                    )
                except _RetryableDatabaseConnectionError as fallback_failure:
                    log.warning(
                        "Equivalent output database connection also failed "
                        "alias=%s stage=%s reason=%s; no further retry",
                        fallback_target.tns,
                        fallback_failure.stage,
                        fallback_failure.reason,
                    )
                    raise
        finally:
            fallback_connection = ""
            connection = ""
            password = ""

    def _validated_prod_target(self, target: OracleTarget) -> OracleTarget:
        country = (target.country or "").strip().lower()
        requested = (
            (target.database_key or "").strip().upper(),
            (target.credential_key or "").strip().upper(),
            (target.tns or "").strip().upper(),
        )
        matches = [
            row
            for row in self.targets(country)
            if (
                str(row.get("database_key") or "").strip().upper(),
                str(row.get("credential_key") or "").strip().upper(),
                str(row.get("id") or "").strip().upper(),
            )
            == requested
        ]
        if len(matches) != 1:
            raise OutputFileGenerationError(
                "The selected database is not a current PROD target"
            )
        selected = matches[0]
        return OracleTarget(
            country=country,
            database_key=str(selected["database_key"]),
            credential_key=str(selected["credential_key"]),
            tns=str(selected["id"]),
            label=str(selected.get("label") or selected["id"]),
        )

    def _alternative_prod_attempt(
        self,
        target: OracleTarget,
    ) -> tuple[OracleTarget, str] | None:
        """Resolve one unambiguous, usable equivalent PROD connection lazily."""
        fallback_alias = alternative_alias(
            target.country,
            target.tns,
            self.config.get("database_failover", {}),
        )
        if not fallback_alias:
            return None

        rows = [
            row
            for row in self.targets(target.country)
            if str(row.get("id") or "").strip().upper()
            == fallback_alias.upper()
        ]
        same_login = [
            row
            for row in rows
            if str(row.get("credential_key") or "").strip().upper()
            == target.credential_key.strip().upper()
        ]
        if len(same_login) == 1:
            selected = same_login[0]
        elif len(same_login) > 1 or len(rows) != 1:
            log.warning(
                "Output database failover unavailable primary=%s alternative=%s "
                "reason=ambiguous-or-missing-PROD-credential",
                target.tns,
                fallback_alias,
            )
            return None
        else:
            selected = rows[0]

        fallback_target = OracleTarget(
            country=target.country,
            database_key=str(selected.get("database_key") or fallback_alias),
            credential_key=str(selected.get("credential_key") or ""),
            tns=str(selected.get("id") or fallback_alias),
            label=str(selected.get("label") or fallback_alias),
        )
        credential = self.config.get_credential(
            fallback_target.country,
            fallback_target.database_key,
            fallback_target.credential_key,
        )
        if not credential or not self._credential_still_matches_prod(
            fallback_target,
            credential,
        ):
            log.warning(
                "Output database failover unavailable primary=%s alternative=%s "
                "reason=credential-no-longer-valid-for-PROD",
                target.tns,
                fallback_alias,
            )
            return None

        fallback_password = ""
        try:
            fallback_password = self._decryptor(
                str(credential.get("password_enc") or "")
            )
            if not fallback_password:
                raise ValueError("empty decrypted password")
            connection = to_sqlcl_arg(
                str(credential.get("user") or ""),
                str(credential.get("schema") or "") or None,
                fallback_password,
                fallback_target.tns,
            )
        except Exception:
            log.warning(
                "Output database failover unavailable primary=%s alternative=%s "
                "reason=password-could-not-be-decrypted",
                target.tns,
                fallback_alias,
            )
            return None
        finally:
            fallback_password = ""
        return fallback_target, connection

    @staticmethod
    def _credential_still_matches_prod(
        target: OracleTarget,
        credential: dict,
    ) -> bool:
        snapshot = {
            target.country: {
                target.database_key: {
                    target.credential_key: dict(credential),
                }
            }
        }
        rows = configured_databases(
            snapshot,
            country=target.country,
            envs=("prod",),
        )
        expected = (
            target.database_key.upper(),
            target.credential_key.upper(),
            target.tns.upper(),
        )
        return _catalog_allows_prod_target(target.country, target.tns) and any(
            (
                str(row.get("database_key") or "").upper(),
                str(row.get("credential_key") or "").upper(),
                str(row.get("id") or "").upper(),
            )
            == expected
            for row in rows
        )

    def _generate_connected(
        self,
        *,
        request: GenerationRequest,
        process_ref: str,
        runner: SqlclRunner,
        connection: str,
        cancel_event: threading.Event | None,
        selected_spec: InterfaceSpec | None,
    ) -> GenerationResult:
        detection = "automatic" if selected_spec is None else "manual"
        log.info(
            "Output reconstruction started country=%s tns=%s process_ref=%s "
            "detection=%s interface=%s",
            request.target.country,
            request.target.tns,
            process_ref,
            detection,
            selected_spec.input_code if selected_spec is not None else "<automatic>",
        )

        # A manual selection already identifies the event.  Restrict discovery
        # to that interface so old processes do not force a scan of every GI
        # archive.  DCSTOUT/STDINROU retain the full view because their verified
        # QA footers are process-global and must reject shared process numbers.
        discovery_input_code = (
            selected_spec.input_code
            if selected_spec is not None
            and selected_spec.contract not in {"dcstout", "stdinrou"}
            else None
        )
        discovery_rows = self._execute(
            runner,
            connection,
            build_discovery_query(process_ref, discovery_input_code),
            timeout=120,
            cancel_event=cancel_event,
        )
        candidates = _parse_candidates(discovery_rows)

        if selected_spec is not None:
            spec, candidate = _select_candidate(
                candidates,
                {},
                selected_spec=selected_spec,
            )
            _validate_manual_process_scope(spec, candidate, candidates)
            mapping_rows = self._execute(
                runner,
                connection,
                build_mappings_query((selected_spec.input_code,)),
                timeout=90,
                cancel_event=cancel_event,
            )
            mappings = _parse_mappings(mapping_rows)
        else:
            mapping_rows: list[str] = []
            if candidates:
                mapping_rows = self._execute(
                    runner,
                    connection,
                    build_mappings_query(
                        candidate.input_code for candidate in candidates
                    ),
                    timeout=90,
                    cancel_event=cancel_event,
                )
            mappings = _parse_mappings(mapping_rows)
            spec, candidate = _select_candidate(candidates, mappings)
        _validate_mapping(spec, mappings)
        if (
            spec.input_code in {"IFCHKPRT", "IFIWDCLG"}
            and candidate.source is not DataSourceChoice.ACTIVE
        ):
            raise OutputFileGenerationError(
                f"{spec.input_code} archive reconstruction is not supported: "
                "the active GITB_FILE_MASTER filename and active clearing data "
                "required by the QA contract are unavailable"
            )
        if (
            spec.input_code == "IFICOWCG"
            and candidate.source is not DataSourceChoice.ACTIVE
        ):
            dependencies = (
                "retained GITM_CLEARING_LOG, IFTB_CLEARING_UPLOAD, "
                "IFTB_CLEARING_UPLOAD_C, CSTB_CLEARING_REJECTION, and active "
                "GITB_FILE_MASTER data"
                if request.target.country in {"colombia", "mexico", "peru"}
                else (
                    "retained GITM_CLEARING_LOG, IFTB_CLEARING_UPLOAD, and "
                    "active GITB_FILE_MASTER data"
                )
            )
            raise OutputFileGenerationError(
                "IFICOWCG archive upload rows were found, but OFICOWCG cannot "
                "be reconstructed from them alone: the QA contract also requires "
                f"{dependencies}. Generation was stopped "
                "instead of creating an empty output file"
            )
        if (
            request.target.country != "chile"
            and candidate.source is DataSourceChoice.ARCHIVE
            and spec.input_code in {"CHISALCA", "IFDOBIEL"}
        ):
            country_label = _COUNTRY_FOLDERS.get(
                request.target.country,
                request.target.country.title(),
            )
            raise OutputFileGenerationError(
                f"{spec.output_code} archive reconstruction for {country_label} "
                "is not verified against a retained regional upload. Use a "
                "current ACTIVE process or add a country-specific archive fixture"
            )
        if (
            spec.input_code == "GIUDFUPD"
            and candidate.source is not DataSourceChoice.ACTIVE
        ):
            raise OutputFileGenerationError(
                "GIUDFUPD archive reconstruction is unavailable: QA reads "
                "GITM_UDF_UPLOAD_DETAILS and PROD has no archived equivalent"
            )

        spec = _validate_country_contract(request.target.country, spec)

        auxiliary_owner = ""
        auxiliary_owner_rows: list[str] = []
        if spec.input_code == "GIUDFUPD":
            auxiliary_owner_rows = self._execute(
                runner,
                connection,
                build_udf_details_owner_query(),
                timeout=90,
                cancel_event=cancel_event,
            )
            auxiliary_owner = _parse_auxiliary_owner(auxiliary_owner_rows)

        # ARCHIVAL_DATE is when Oracle moved the rows, not necessarily the
        # business date used by GLOBAL.APPLICATION_DATE in the QA package.
        # Resolve that date from the original input log for every source.
        date_rows = self._execute(
            runner,
            connection,
            build_file_date_query(
                process_ref,
                spec.input_code,
                candidate.source,
            ),
            timeout=90,
            cancel_event=cancel_event,
        )
        file_date = _parse_file_date_metadata(date_rows)
        if not file_date:
            raise OutputFileGenerationError(
                "The file date could not be detected automatically from this process"
            )
        file_date = _as_generation_error(validate_file_date, file_date)
        if (
            request.input_file_date is not None
            and file_date != request.input_file_date
        ):
            selected_display = (
                f"{request.input_file_date[6:8]}-"
                f"{request.input_file_date[4:6]}-"
                f"{request.input_file_date[:4]}"
            )
            detected_display = (
                f"{file_date[6:8]}-{file_date[4:6]}-{file_date[:4]}"
            )
            raise OutputFileGenerationError(
                "The selected input date does not match the PROD file log: "
                f"selected {selected_display}, detected {detected_display}. "
                "The selected date is a safety check and never overrides "
                "Oracle's persisted process date"
            )

        qsimtp_snapshot_rows: list[str] = []
        qsimtp_snapshot: _QsimtpSnapshotMetadata | None = None
        if spec.input_code == "IFQSIMTP":
            qsimtp_snapshot_rows = self._execute(
                runner,
                connection,
                build_qsimtp_snapshot_guard_query(process_ref, candidate.source),
                timeout=300,
                cancel_event=cancel_event,
            )
            qsimtp_snapshot = _parse_qsimtp_snapshot_metadata(
                qsimtp_snapshot_rows,
                process_ref=process_ref,
                source=candidate.source,
                candidate_upload_rows=candidate.upload_record_count,
            )

        stdinrou_dependency_rows: list[str] = []
        stdinrou_dependencies: _StdinrouDependencyMetadata | None = None
        if spec.input_code == "STDINRTS":
            stdinrou_dependency_rows = self._execute(
                runner,
                connection,
                build_stdinrou_dependency_guard_query(
                    process_ref,
                    candidate.source,
                ),
                timeout=300,
                cancel_event=cancel_event,
            )
            stdinrou_dependencies = _parse_stdinrou_dependency_metadata(
                stdinrou_dependency_rows,
                candidate_upload_rows=candidate.upload_record_count,
            )

        database_time = ""
        filename_database_time = ""
        database_date = ""
        interface_last_run_rows: list[str] = []
        if spec.header_uses_last_run_date:
            interface_last_run_rows = self._execute(
                runner,
                connection,
                build_interface_last_run_date_query(spec.input_code),
                timeout=90,
                cancel_event=cancel_event,
            )
            database_date = _parse_interface_last_run_date(
                interface_last_run_rows
            )
        if spec.timestamped_header:
            # QA builds these headers before opening the body cursor. Capture
            # the database clock at the same stage so large files do not shift
            # the contractual timestamp by their extraction duration.
            # For historical OACMCLOS reconstruction the persisted process
            # date above replaces GLOBAL.APPLICATION_DATE. One PROD clock
            # reading supplies FN_SYSDATE's full calendar/timestamp to the
            # header and its time portion to the physical filename.
            clock_format = (
                "YYYYMMDDHH24MISS"
                if spec.header_uses_database_date
                else spec.database_time_format
            )
            time_rows = self._execute(
                runner,
                connection,
                build_database_time_query(clock_format),
                timeout=60,
                cancel_event=cancel_event,
            )
            clock_value = next(
                (row.strip() for row in time_rows if row.strip()),
                "",
            )
            if spec.header_uses_database_date:
                if not re.fullmatch(r"[0-9]{14}", clock_value):
                    raise OutputFileGenerationError(
                        "Oracle returned an invalid database timestamp"
                    )
                database_date = _as_generation_error(
                    validate_file_date,
                    clock_value[:8],
                )
                database_time = clock_value[8:]
            else:
                database_time = clock_value

        input_physical_filename = ""
        if spec.header_uses_input_filename:
            filename_rows = self._execute(
                runner,
                connection,
                build_input_physical_filename_query(
                    process_ref,
                    spec.input_code,
                    candidate.source,
                ),
                timeout=90,
                cancel_event=cancel_event,
            )
            input_physical_filename = _parse_input_physical_filename(filename_rows)
            if (
                stdinrou_dependencies is not None
                and stdinrou_dependencies.custom_physical_filename
                != input_physical_filename
            ):
                raise OutputFileGenerationError(
                    "STDINRTS custom source and GITB_FILE_MASTER disagree on "
                    "the physical input filename"
                )

        body_rows = self._execute_body_rows(
            runner=runner,
            connection=connection,
            process_ref=process_ref,
            spec=spec,
            source=candidate.source,
            file_date=file_date,
            auxiliary_owner=auxiliary_owner,
            country=request.target.country,
            cancel_event=cancel_event,
        )
        body_records, status_counts = _parse_body_records(body_rows, spec)
        if (
            spec.input_code == "CHISALCA"
            and len(body_records) != candidate.upload_record_count
        ):
            raise OutputFileGenerationError(
                "CHISALCA body rows do not cover all detected upload rows; "
                "the archived file-log index keys may be incomplete"
            )
        if spec.input_code == "IFICOWCG" and not body_records:
            if _uses_transactional_oficowcg_contract(spec):
                country_label = _COUNTRY_FOLDERS.get(
                    request.target.country,
                    request.target.country.title(),
                )
                raise OutputFileGenerationError(
                    f"IFICOWCG upload rows were found, but {country_label} OFICOWCG has "
                    "no clearing output rows for this process. GITM_CLEARING_LOG, "
                    "IFTB_CLEARING_UPLOAD, or IFTB_CLEARING_UPLOAD_C may already "
                    "have been cleaned up; generation was stopped instead of "
                    "inventing SUCC, ERRO, or REJR states"
                )
            raise OutputFileGenerationError(
                "IFICOWCG has no reconstructible clearing output rows for this "
                "process. Verify GITM_CLEARING_LOG and IFTB_CLEARING_UPLOAD; "
                "generation was stopped instead of creating an empty output file"
            )
        oficowcg_coverage_rows: list[str] = []
        if spec.input_code == "IFICOWCG":
            oficowcg_coverage_rows = self._execute(
                runner,
                connection,
                build_oficowcg_coverage_query(
                    process_ref,
                    country=request.target.country,
                    file_date=file_date,
                ),
                timeout=300,
                cancel_event=cancel_event,
            )
            _parse_oficowcg_coverage_metadata(
                oficowcg_coverage_rows,
                candidate_upload_rows=candidate.upload_record_count,
                country=request.target.country,
                body_record_count=len(body_records),
            )
        if (
            spec.input_code == "IFICOWCG"
            and not _uses_transactional_oficowcg_contract(spec)
            and len(body_records) != candidate.upload_record_count
        ):
            raise OutputFileGenerationError(
                "OFICOWCG's QA UNION collapsed or changed rows after identity "
                "coverage was validated; generation was stopped because the "
                "final cursor no longer contains one row per IFICOWCG upload"
            )
        if (
            spec.input_code == "IFCHKPRT"
            and len(body_records) != candidate.upload_record_count
        ):
            raise OutputFileGenerationError(
                "IFCHKPRT UNION rows do not match its detected upload rows; "
                "generation was stopped instead of creating a partial file"
            )
        if (
            spec.input_code == "IFIWDCLG"
            and len(body_records) != candidate.upload_record_count
        ):
            raise OutputFileGenerationError(
                "IFIWDCLG UNION rows do not match its detected upload rows; "
                "mandatory clearing-log data is incomplete"
            )
        if (
            spec.input_code == "IFIWADOC"
            and len(body_records) != candidate.upload_record_count
        ):
            raise OutputFileGenerationError(
                "IFIWADOC body rows do not match its detected upload rows; "
                "mandatory GITM_CLEARING_ADOC_LOG data is incomplete"
            )
        if spec.input_code == "IFQSIMTP":
            if qsimtp_snapshot is None:
                raise OutputFileGenerationError(
                    "OFQSIMTP snapshot metadata was not validated"
                )
            expected_status_counts = {
                "P": qsimtp_snapshot.live_rows,
                "E": qsimtp_snapshot.failed_uploads,
            }
            if (
                len(body_records) != qsimtp_snapshot.expected_body_rows
                or any(
                    status_counts.get(status, 0) != expected
                    for status, expected in expected_status_counts.items()
                )
                or set(status_counts) - {"P", "E"}
            ):
                raise OutputFileGenerationError(
                    "OFQSIMTP body does not match its authenticated live snapshot"
                )
        if spec.input_code == "STDINRTS":
            if stdinrou_dependencies is None:
                raise OutputFileGenerationError(
                    "STDINROU dependency metadata was not validated"
                )
            expected_custom_counts = {
                "P": stdinrou_dependencies.custom_processed,
                "<NON_P>": stdinrou_dependencies.custom_unprocessed,
            }
            if (
                len(body_records) != stdinrou_dependencies.custom_rows
                or any(
                    status_counts.get(status, 0) != expected
                    for status, expected in expected_custom_counts.items()
                )
                or set(status_counts) - {"P", "<NON_P>"}
            ):
                raise OutputFileGenerationError(
                    "STDINROU body does not match STDINRTS_UPLOAD_MASTER"
                )
        if (
            spec.input_code == "GIUDFUPD"
            and len(body_records) != candidate.upload_record_count
        ):
            raise OutputFileGenerationError(
                "GIUDFUPD detail rows do not match its upload-master rows; "
                "generation was stopped instead of creating a partial file"
            )
        if (
            spec.input_code == "IFSTDCST"
            and len(body_records) != candidate.upload_record_count
        ):
            raise OutputFileGenerationError(
                "IFSTDCST body rows do not match its detected upload rows; "
                "generation was stopped instead of creating a partial file"
            )
        if (
            spec.input_code == "IFEARLCG"
            and len(body_records) != candidate.upload_record_count
        ):
            raise OutputFileGenerationError(
                "IFEARLCG clearing rows do not match its detected upload rows; "
                "generation was stopped instead of creating a partial file"
            )
        footer_status_counts = status_counts
        footer_status_rows: list[str] = []
        if spec.input_code == "IFLOCREC" or spec.footer_counts_all_upload_rows:
            try:
                # Validate statuses belonging to the body cursor independently
                # before reading a package footer that counts a wider upload
                # population (for example, rows for other TARGET_TABLE values).
                build_footer(spec, len(body_records), status_counts)
            except ValueError as exc:
                raise OutputFileGenerationError(str(exc)) from exc
            footer_status_rows = self._execute(
                runner,
                connection,
                build_footer_status_query(
                    process_ref,
                    spec.input_code,
                    candidate.source,
                ),
                timeout=90,
                cancel_event=cancel_event,
            )
            footer_status_counts = _parse_status_counts(
                footer_status_rows,
                require_explicit_pe=spec.input_code == "IFLOCREC",
                reject_lowercase=spec.input_code == "CHISALCA",
            )
            if stdinrou_dependencies is not None:
                if (
                    footer_status_counts.get("P", 0)
                    != stdinrou_dependencies.standard_processed
                    or footer_status_counts.get("<NON_P>", 0)
                    != stdinrou_dependencies.standard_unprocessed
                    or footer_status_counts.get("<NULL>", 0) != 0
                    or set(footer_status_counts) != {"P", "<NON_P>", "<NULL>"}
                ):
                    raise OutputFileGenerationError(
                        "STDINROU footer counts do not match the authenticated "
                        "standard upload population"
                    )
        try:
            # Validate the complete status domain before auxiliary lookups or a
            # physical-filename clock. OFCRELVP in particular omits U rows from
            # its body while counting them in its footer, so P/E is mandatory.
            footer = build_footer(spec, len(body_records), footer_status_counts)
        except ValueError as exc:
            raise OutputFileGenerationError(str(exc)) from exc

        messages: dict[str, str] = {}
        error_types: dict[str, str] = {}
        message_rows: list[str] = []
        error_codes = _collect_error_codes(spec, body_records)
        if error_codes:
            message_query = (
                build_error_message_details_query(error_codes)
                if spec.contract == "chisalou"
                else build_error_messages_query(error_codes)
            )
            message_rows = self._execute(
                runner,
                connection,
                message_query,
                timeout=120,
                cancel_event=cancel_event,
            )
            if spec.contract == "chisalou":
                messages, error_types = _parse_error_message_details(message_rows)
            else:
                messages = _parse_error_messages(message_rows)
        body_lines = _render_body_records(
            spec,
            body_records,
            messages,
            error_types,
        )

        if spec.input_code in {"IFQSIMTP", "STDINRTS"}:
            # Both QA packages resolve the physical filename only after their
            # first component pass has finished. Capture that clock here;
            # app-only stability re-reads below must not shift the client name.
            filename_time_rows = self._execute(
                runner,
                connection,
                build_database_time_query(spec.database_time_format),
                timeout=60,
                cancel_event=cancel_event,
            )
            filename_database_time = next(
                (row.strip() for row in filename_time_rows if row.strip()),
                "",
            )

        if (
            candidate.source is DataSourceChoice.ACTIVE
            or spec.revalidate_external_inputs
        ):
            self._revalidate_active_inputs(
                runner=runner,
                connection=connection,
                process_ref=process_ref,
                spec=spec,
                candidate=candidate,
                file_date=file_date,
                input_physical_filename=input_physical_filename,
                body_rows=body_rows,
                footer_status_rows=footer_status_rows,
                error_codes=error_codes,
                message_rows=message_rows,
                auxiliary_owner=auxiliary_owner,
                auxiliary_owner_rows=auxiliary_owner_rows,
                interface_last_run_rows=interface_last_run_rows,
                oficowcg_coverage_rows=oficowcg_coverage_rows,
                country=request.target.country,
                qsimtp_snapshot_rows=qsimtp_snapshot_rows,
                stdinrou_dependency_rows=stdinrou_dependency_rows,
                cancel_event=cancel_event,
            )
        elif error_codes:
            # Archived upload rows may be stable while the shared PROD
            # ERTB_MSGS catalogue changes. Re-read only that small dependency
            # for adapters without other live inputs; a full second body query
            # would add substantial cost without improving this race check.
            current_message_rows = self._execute(
                runner,
                connection,
                (
                    build_error_message_details_query(error_codes)
                    if spec.contract == "chisalou"
                    else build_error_messages_query(error_codes)
                ),
                timeout=120,
                cancel_event=cancel_event,
            )
            if current_message_rows != message_rows:
                raise OutputFileGenerationError(
                    "The archived process error-message definitions changed "
                    "while the output was being built; retry the generation"
                )

        self._revalidate_process_contract(
            runner=runner,
            connection=connection,
            process_ref=process_ref,
            spec=spec,
            candidate=candidate,
            cancel_event=cancel_event,
        )

        if (
            spec.database_time_format
            and not database_time
            and not filename_database_time
        ):
            time_rows = self._execute(
                runner,
                connection,
                build_database_time_query(spec.database_time_format),
                timeout=60,
                cancel_event=cancel_event,
            )
            database_time = next((row.strip() for row in time_rows if row.strip()), "")
        if not filename_database_time:
            filename_database_time = database_time

        try:
            header = (
                build_header(
                    spec,
                    file_date,
                    database_time,
                    input_physical_filename,
                    database_date,
                )
                if spec.has_header
                else ""
            )
            output_lines: list[str] = []
            if spec.has_header:
                output_lines.append(header)
            output_lines.extend(body_lines)
            if spec.has_footer:
                output_lines.append(footer)
            lines = tuple(output_lines)
            validate_output_lines(spec, lines)
            payload = serialize_lines(lines)
            output_path = self._output_path(
                request.target.country,
                spec,
                file_date,
                filename_database_time,
            )
        except ValueError as exc:
            raise OutputFileGenerationError(str(exc)) from exc

        _raise_if_cancelled(cancel_event)
        _write_atomic(output_path, payload, overwrite=request.overwrite)
        digest = hashlib.sha256(payload).hexdigest()
        log.info(
            "Output reconstruction completed country=%s tns=%s process_ref=%s input=%s output=%s source=%s rows=%d file=%s sha256=%s",
            request.target.country,
            request.target.tns,
            process_ref,
            spec.input_code,
            spec.output_code,
            candidate.source.value,
            len(body_lines),
            output_path.name,
            digest,
        )
        return GenerationResult(
            output_path=output_path,
            input_code=spec.input_code,
            output_code=spec.output_code,
            process_ref_no=process_ref,
            file_date=file_date,
            source=candidate.source,
            body_count=len(body_lines),
            status_counts=status_counts,
            lines=tuple(lines),
            sha256=digest,
        )

    def _revalidate_process_contract(
        self,
        *,
        runner: SqlclRunner,
        connection: str,
        process_ref: str,
        spec: InterfaceSpec,
        candidate: ProcessCandidate,
        cancel_event: threading.Event | None,
    ) -> None:
        rows = self._execute(
            runner,
            connection,
            build_process_contract_query(
                process_ref,
                spec.input_code,
                spec.output_code,
                candidate.source,
            ),
            timeout=90,
            cancel_event=cancel_event,
        )
        upload_count, mapping_count, expected_mapping_count = (
            _parse_process_contract_metadata(rows)
        )
        if upload_count != candidate.upload_record_count:
            raise OutputFileGenerationError(
                "The process upload rows changed or were purged while the output "
                "was being built; retry after processing has finished"
            )
        if mapping_count != 1 or expected_mapping_count != 1:
            raise OutputFileGenerationError(
                "The interface output mapping changed while the output was being "
                "built; generation was stopped before saving"
            )

    def _revalidate_active_inputs(
        self,
        *,
        runner: SqlclRunner,
        connection: str,
        process_ref: str,
        spec: InterfaceSpec,
        candidate: ProcessCandidate,
        file_date: str,
        input_physical_filename: str,
        body_rows: list[str],
        footer_status_rows: list[str],
        error_codes: set[str],
        message_rows: list[str],
        auxiliary_owner: str,
        auxiliary_owner_rows: list[str],
        interface_last_run_rows: list[str],
        oficowcg_coverage_rows: list[str],
        country: str,
        qsimtp_snapshot_rows: list[str],
        stdinrou_dependency_rows: list[str],
        cancel_event: threading.Event | None,
    ) -> None:
        """Reject mutable inputs that change across the multi-query workflow.

        SQLcl opens a fresh connection for each controlled SELECT. Re-reading
        every adapter input before writing does not replace a database snapshot,
        but it prevents a file assembled from two observably different states.
        Archived upload rows normally do not pay this cost; adapters that join
        live PROD reference tables opt in explicitly.
        """
        mutable_subject = (
            "active process"
            if candidate.source is DataSourceChoice.ACTIVE
            else "archived process dependencies"
        )
        if oficowcg_coverage_rows:
            current_coverage_rows = self._execute(
                runner,
                connection,
                build_oficowcg_coverage_query(
                    process_ref,
                    country=country,
                    file_date=file_date,
                ),
                timeout=300,
                cancel_event=cancel_event,
            )
            if current_coverage_rows != oficowcg_coverage_rows:
                raise OutputFileGenerationError(
                    "OFICOWCG upload/clearing identity coverage changed while "
                    "the output was being built; generation was stopped"
                )
            _parse_oficowcg_coverage_metadata(
                current_coverage_rows,
                candidate_upload_rows=candidate.upload_record_count,
                country=country,
                body_record_count=len(body_rows),
            )

        if qsimtp_snapshot_rows:
            current_snapshot_rows = self._execute(
                runner,
                connection,
                build_qsimtp_snapshot_guard_query(process_ref, candidate.source),
                timeout=300,
                cancel_event=cancel_event,
            )
            if current_snapshot_rows != qsimtp_snapshot_rows:
                raise OutputFileGenerationError(
                    "The authenticated OFQSIMTP live snapshot changed while the "
                    "output was being built; generation was stopped"
                )
            _parse_qsimtp_snapshot_metadata(
                current_snapshot_rows,
                process_ref=process_ref,
                source=candidate.source,
                candidate_upload_rows=candidate.upload_record_count,
            )

        if stdinrou_dependency_rows:
            current_dependency_rows = self._execute(
                runner,
                connection,
                build_stdinrou_dependency_guard_query(
                    process_ref,
                    candidate.source,
                ),
                timeout=300,
                cancel_event=cancel_event,
            )
            if current_dependency_rows != stdinrou_dependency_rows:
                raise OutputFileGenerationError(
                    "The STDINROU custom/standard dependencies changed while "
                    "the output was being built; generation was stopped"
                )
            _parse_stdinrou_dependency_metadata(
                current_dependency_rows,
                candidate_upload_rows=candidate.upload_record_count,
            )

        current_body_rows = self._execute_body_rows(
            runner=runner,
            connection=connection,
            process_ref=process_ref,
            spec=spec,
            source=candidate.source,
            file_date=file_date,
            auxiliary_owner=auxiliary_owner,
            country=country,
            cancel_event=cancel_event,
        )
        if current_body_rows != body_rows:
            raise OutputFileGenerationError(
                f"The {mutable_subject} changed while the output was being built; "
                "retry after processing has finished"
            )

        if auxiliary_owner_rows:
            current_owner_rows = self._execute(
                runner,
                connection,
                build_udf_details_owner_query(),
                timeout=90,
                cancel_event=cancel_event,
            )
            if current_owner_rows != auxiliary_owner_rows:
                raise OutputFileGenerationError(
                    "The visible GIUDFUPD detail source changed while the output "
                    "was being built; retry the generation"
                )

        if interface_last_run_rows:
            current_last_run_rows = self._execute(
                runner,
                connection,
                build_interface_last_run_date_query(spec.input_code),
                timeout=90,
                cancel_event=cancel_event,
            )
            if current_last_run_rows != interface_last_run_rows:
                raise OutputFileGenerationError(
                    "The interface LAST_RUN_DATE changed while the output was "
                    "being built; retry after processing has finished"
                )

        if footer_status_rows:
            current_footer_status_rows = self._execute(
                runner,
                connection,
                build_footer_status_query(
                    process_ref,
                    spec.input_code,
                    candidate.source,
                ),
                timeout=90,
                cancel_event=cancel_event,
            )
            if current_footer_status_rows != footer_status_rows:
                footer_subject = (
                    "active"
                    if candidate.source is DataSourceChoice.ACTIVE
                    else "archived process dependencies"
                )
                raise OutputFileGenerationError(
                    f"The {footer_subject} footer status counts changed while the output was "
                    "being built; retry after processing has finished"
                )

        current_date_rows = self._execute(
            runner,
            connection,
            build_file_date_query(
                process_ref,
                spec.input_code,
                candidate.source,
            ),
            timeout=90,
            cancel_event=cancel_event,
        )
        if _parse_file_date_metadata(current_date_rows) != file_date:
            raise OutputFileGenerationError(
                f"The {mutable_subject} date changed while the output was being built; "
                "retry after processing has finished"
            )

        if spec.header_uses_input_filename:
            current_filename_rows = self._execute(
                runner,
                connection,
                build_input_physical_filename_query(
                    process_ref,
                    spec.input_code,
                    candidate.source,
                ),
                timeout=90,
                cancel_event=cancel_event,
            )
            if (
                _parse_input_physical_filename(current_filename_rows)
                != input_physical_filename
            ):
                filename_subject = (
                    "active"
                    if candidate.source is DataSourceChoice.ACTIVE
                    else "archived process dependencies"
                )
                raise OutputFileGenerationError(
                    f"The {filename_subject} input filename changed while the output was being "
                    "built; retry after processing has finished"
                )

        if error_codes:
            message_query = (
                build_error_message_details_query(error_codes)
                if spec.contract == "chisalou"
                else build_error_messages_query(error_codes)
            )
            current_message_rows = self._execute(
                runner,
                connection,
                message_query,
                timeout=120,
                cancel_event=cancel_event,
            )
            if current_message_rows != message_rows:
                message_subject = (
                    "active"
                    if candidate.source is DataSourceChoice.ACTIVE
                    else "archived process dependencies"
                )
                raise OutputFileGenerationError(
                    f"The {message_subject} error-message definitions changed while the output "
                    "was being built; retry the generation"
                )

    def _execute_body_rows(
        self,
        *,
        runner: SqlclRunner,
        connection: str,
        process_ref: str,
        spec: InterfaceSpec,
        source: DataSourceChoice,
        file_date: str,
        auxiliary_owner: str,
        country: str,
        cancel_event: threading.Event | None,
    ) -> list[str]:
        upload_rows = self._execute(
            runner,
            connection,
            build_body_query(
                process_ref,
                spec.input_code,
                source,
                file_date,
                auxiliary_owner,
                country=country,
            ),
            timeout=300,
            cancel_event=cancel_event,
        )
        if spec.input_code != "CHISALCA":
            return upload_rows

        peru_contract = spec.contract == "chisalou_peru"
        parsed_uploads = _parse_chisalca_upload_rows(
            upload_rows,
            include_ccicode=peru_contract,
        )
        processed_xrefs = sorted(
            {
                row.xref
                for row in parsed_uploads
                if row.raw_status.strip() == "P" and row.xref
            }
        )
        teller_rows: list[str] = []
        for offset in range(0, len(processed_xrefs), _CHISALCA_TELLER_BATCH_SIZE):
            teller_rows.extend(
                self._execute(
                    runner,
                    connection,
                    build_chisalca_teller_query(
                        processed_xrefs[
                            offset : offset + _CHISALCA_TELLER_BATCH_SIZE
                        ]
                    ),
                    timeout=120,
                    cancel_event=cancel_event,
                )
            )
        return _compose_chisalca_body_rows(
            parsed_uploads,
            teller_rows,
            include_ccicode=peru_contract,
        )

    def _execute(
        self,
        runner: SqlclRunner,
        connection: str,
        query: FramedQuery,
        *,
        timeout: float,
        cancel_event: threading.Event | None,
    ) -> list[str]:
        _raise_if_cancelled(cancel_event)
        stage = str(query.purpose or "QUERY").strip().upper()
        started_at = time.monotonic()
        log.info(
            "Output SQL started stage=%s timeout=%ss",
            stage,
            f"{timeout:g}",
        )
        with tempfile.TemporaryDirectory(prefix="oracle_tasks_output_") as temp_dir:
            script_path = Path(temp_dir) / "query.sql"
            script_path.write_text(query.script, encoding="utf-8", newline="\n")
            try:
                result: RunResult = runner.run_script(
                    connection,
                    script_path,
                    timeout=timeout,
                    cancel_event=cancel_event,
                )
            except Exception as exc:
                safe_error = _safe_text_error(str(exc))
                log.warning(
                    "SQLcl execution failed before returning a result "
                    "stage=%s elapsed=%.1fs reason=%s",
                    stage,
                    time.monotonic() - started_at,
                    safe_error,
                )
                raise OutputFileGenerationError(safe_error) from None
        elapsed = time.monotonic() - started_at
        log.info(
            "Output SQL finished stage=%s elapsed=%.1fs exit_code=%s",
            stage,
            elapsed,
            result.exit_code,
        )
        if result.exit_code == 130 or _is_cancelled(cancel_event):
            raise GenerationCancelled("Output generation was cancelled")
        connection_reason = _connection_failure_reason_for_query(result, query)
        if connection_reason:
            raise _RetryableDatabaseConnectionError(
                f"Oracle connection failed ({connection_reason}) while running {stage}",
                reason=connection_reason,
                stage=stage,
            )
        if not result.ok:
            message = _safe_sqlcl_error(result)
            if result.exit_code == 124:
                message = f"{message} while running {stage}"
            raise OutputFileGenerationError(message)
        outside = output_outside_markers(result.stdout, query)
        diagnostic = "\n".join(part for part in (outside, result.stderr) if part)
        if _SQL_ERROR_RE.search(diagnostic):
            raise OutputFileGenerationError(_safe_text_error(diagnostic))
        try:
            return parse_framed_output(result.stdout, query)
        except ValueError as exc:
            log.warning(
                "SQLcl returned malformed framed output "
                "exit_code=%s begin_markers=%d end_markers=%d stderr_sql_error=%s",
                result.exit_code,
                (result.stdout or "").count(query.begin_marker),
                (result.stdout or "").count(query.end_marker),
                bool(_SQL_ERROR_RE.search(result.stderr or "")),
            )
            raise OutputFileGenerationError(
                "SQLcl returned an unexpected response; review app.log for diagnostics"
            ) from exc

    def _output_path(
        self,
        country: str,
        spec: InterfaceSpec,
        file_date: str,
        database_time: str = "",
    ) -> Path:
        folder = _folder_for_country(country)
        root = self.output_root.resolve()
        path = (
            root / folder / physical_filename(spec, file_date, database_time)
        ).resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise OutputFileGenerationError("The output path is outside the managed folder") from exc
        return path


def _folder_for_country(country: str) -> str:
    country_key = (country or "").strip().lower()
    try:
        return _COUNTRY_FOLDERS[country_key]
    except KeyError as exc:
        raise OutputFileGenerationError("The selected country is not supported") from exc


def _catalog_allows_prod_target(country: str, tns: str) -> bool:
    """Reject known QA/DEV/BUP aliases even if their saved bucket says PROD.

    Unknown customer aliases can still rely on the explicit credential bucket,
    but a catalogued alias has authoritative country/environment metadata.
    """
    known = find_db(tns)
    if known is None:
        return True
    return (
        str(known.get("country") or "").strip().lower()
        == str(country or "").strip().lower()
        and str(known.get("env") or "").strip().lower() == "prod"
    )


def _validate_country_contract(country: str, spec: InterfaceSpec) -> InterfaceSpec:
    """Prevent a QA contract verified for one country from leaking into another.

    PROD discovery is intentionally available for all configured countries, but
    exact generation stays country-scoped until the matching QA package has been
    inspected and a country-specific adapter (or verified equivalence) is added.
    """
    country_key = str(country or "").strip().lower()
    if country_key == "chile":
        return spec
    if spec.input_code == "CHISALCA" and country_key in {"colombia", "mexico"}:
        # These QA packages format every FLD199 code. Unlike Chile, they do
        # not discard ERTB_MSGS rows whose TYPE is O.
        return replace(spec, error_message_mode="list_tilde_all")
    if spec.input_code == "CHISALCA" and country_key == "peru":
        # Peru additionally emits FLD43/CCICODE between currency and the Y/N
        # flag, giving its normal P/E records one extra field.
        return replace(
            spec,
            body_field_count=12,
            contract="chisalou_peru",
            error_message_mode="list_tilde_all",
        )
    if country_key == "peru" and spec.input_code == "IFDOBIEL":
        # GIPKS_OFDOBIEL in Peru QA implements the same output contract as
        # Chile. PROD's mapping guard remains authoritative and fail-closed.
        return spec
    if (
        country_key in {"colombia", "mexico", "peru"}
        and spec.input_code == "IFICOWCG"
    ):
        return replace(
            spec,
            body_field_count=14,
            error_body_field_count=16,
            contract=f"oficowcg_{country_key}",
            revalidate_external_inputs=True,
        )
    country_label = _COUNTRY_FOLDERS.get(country_key, country_key.title())
    if spec.input_code == "IFDOBIEL" and country_key in {"colombia", "mexico"}:
        raise OutputFileGenerationError(
            f"OFDOBIEL generation for {country_label} is not available: "
            f"GIPKS_OFDOBIEL and IFDOBIEL->OFDOBIEL are absent from "
            f"{country_label} QA. Generation was stopped instead of reusing "
            "another country's package contract"
        )
    raise OutputFileGenerationError(
        f"{spec.output_code} generation for {country_label} is not enabled yet: "
        f"verify GIPKS_{spec.output_code} in {country_label} QA and add or "
        "approve its country-specific adapter. PROD discovery and mapping "
        "completed, but the Chile QA contract will not be reused automatically"
    )


def _resolve_requested_interface(value: str | None) -> InterfaceSpec | None:
    """Resolve a manual input/output code against the implemented allowlist."""
    if value is None:
        return None
    try:
        return spec_for_code(str(value))
    except (TypeError, ValueError) as exc:
        raise OutputFileGenerationError(
            "The selected interface/output is not supported"
        ) from exc


def _parse_candidates(rows: list[str]) -> list[ProcessCandidate]:
    collapsed: dict[tuple[DataSourceChoice, str], ProcessCandidate] = {}
    for row in rows:
        parts = row.split("|", 3)
        if len(parts) != 4:
            raise OutputFileGenerationError("Oracle returned malformed process metadata")
        evidence_raw, source_raw, code_raw, count_raw = (
            part.strip() for part in parts
        )
        try:
            if evidence_raw not in {"UPLOAD_MASTER", "FILE_LOG"}:
                raise ValueError("Unknown discovery evidence")
            source = DataSourceChoice(source_raw.lower())
            code = normalize_interface_code(code_raw)
            count = int(count_raw)
        except (TypeError, ValueError) as exc:
            raise OutputFileGenerationError("Oracle returned invalid process metadata") from exc
        if count < 0:
            raise OutputFileGenerationError("Oracle returned invalid process metadata")
        if count > 0:
            key = (source, code)
            previous = collapsed.get(key)
            # UPLOAD_MASTER and FILE_LOG are independent evidence of the same
            # process/interface. Keep one candidate, retain both evidence kinds,
            # and never add their unrelated row counts.
            evidence = frozenset((evidence_raw,))
            upload_count = count if evidence_raw == "UPLOAD_MASTER" else 0
            if previous is None:
                collapsed[key] = ProcessCandidate(
                    source,
                    code,
                    count,
                    evidence,
                    upload_count,
                )
            else:
                collapsed[key] = ProcessCandidate(
                    source,
                    code,
                    max(previous.record_count, count),
                    previous.evidence_kinds | evidence,
                    max(previous.upload_record_count, upload_count),
                )
    return sorted(
        collapsed.values(),
        key=lambda candidate: (candidate.source.value, candidate.input_code),
    )


def _parse_file_date_metadata(rows: list[str]) -> str:
    values = [row.strip() for row in rows if row.strip()]
    if len(values) != 1:
        raise OutputFileGenerationError("Oracle returned invalid file-date metadata")
    parts = values[0].split("|", 2)
    if len(parts) != 3:
        raise OutputFileGenerationError("Oracle returned invalid file-date metadata")
    date_raw, upload_count_raw, start_count_raw = (part.strip() for part in parts)
    try:
        upload_count = int(upload_count_raw)
        start_count = int(start_count_raw)
    except ValueError as exc:
        raise OutputFileGenerationError("Oracle returned invalid file-date metadata") from exc
    if upload_count < 0 or start_count < 0:
        raise OutputFileGenerationError("Oracle returned invalid file-date metadata")
    if upload_count > 1:
        raise OutputFileGenerationError(
            "The process log contains more than one upload date; automatic date detection is ambiguous"
        )
    if upload_count == 0 and start_count > 1:
        raise OutputFileGenerationError(
            "The process log contains more than one start date; automatic date detection is ambiguous"
        )
    if not date_raw:
        return ""
    return _as_generation_error(validate_file_date, date_raw)


def _parse_auxiliary_owner(rows: list[str]) -> str:
    values = [_decode_utf8_hex(row).strip().upper() for row in rows if row.strip()]
    if len(values) != 1 or not _ORACLE_IDENTIFIER_RE.fullmatch(values[0]):
        raise OutputFileGenerationError(
            "Oracle did not expose one unambiguous GIUDFUPD detail-table owner"
        )
    return values[0]


def _parse_interface_last_run_date(rows: list[str]) -> str:
    values = [row.strip() for row in rows if row.strip()]
    if len(values) != 1:
        raise OutputFileGenerationError("Oracle returned invalid LAST_RUN_DATE metadata")
    parts = values[0].split("|", 1)
    if len(parts) != 2:
        raise OutputFileGenerationError("Oracle returned invalid LAST_RUN_DATE metadata")
    date_raw, count_raw = (part.strip() for part in parts)
    try:
        count = int(count_raw)
    except ValueError as exc:
        raise OutputFileGenerationError(
            "Oracle returned invalid LAST_RUN_DATE metadata"
        ) from exc
    if count != 1 or not date_raw:
        raise OutputFileGenerationError(
            "The interface LAST_RUN_DATE is missing or ambiguous"
        )
    return _as_generation_error(validate_file_date, date_raw)


def _parse_process_contract_metadata(rows: list[str]) -> tuple[int, int, int]:
    values = [row.strip() for row in rows if row.strip()]
    if len(values) != 1:
        raise OutputFileGenerationError(
            "Oracle returned invalid process-contract metadata"
        )
    parts = values[0].split("|")
    if len(parts) != 3:
        raise OutputFileGenerationError(
            "Oracle returned invalid process-contract metadata"
        )
    try:
        counts = tuple(int(part.strip()) for part in parts)
    except ValueError as exc:
        raise OutputFileGenerationError(
            "Oracle returned invalid process-contract metadata"
        ) from exc
    if any(count < 0 for count in counts):
        raise OutputFileGenerationError(
            "Oracle returned invalid process-contract metadata"
        )
    return counts


def _parse_oficowcg_coverage_metadata(
    rows: list[str],
    *,
    candidate_upload_rows: int,
    country: str = "chile",
    body_record_count: int | None = None,
) -> _OficowcgCoverageMetadata | _OficowcgRegionalCoverageMetadata:
    values = [row.strip() for row in rows if row.strip()]
    if len(values) != 1:
        raise OutputFileGenerationError(
            "Oracle returned invalid OFICOWCG identity metadata"
        )
    parts = values[0].split("|")
    if len(parts) != 8:
        raise OutputFileGenerationError(
            "Oracle returned invalid OFICOWCG identity metadata"
        )
    try:
        counts = tuple(int(value.strip()) for value in parts)
    except ValueError as exc:
        raise OutputFileGenerationError(
            "Oracle returned invalid OFICOWCG identity metadata"
        ) from exc
    if any(value < 0 for value in counts):
        raise OutputFileGenerationError(
            "Oracle returned invalid OFICOWCG identity metadata"
        )

    country_key = str(country or "").strip().lower()
    if country_key in {"colombia", "mexico", "peru"}:
        country_label = _COUNTRY_FOLDERS[country_key]
        metadata = _OficowcgRegionalCoverageMetadata(*counts)
        if metadata.upload_rows <= 0 or metadata.upload_rows != candidate_upload_rows:
            raise OutputFileGenerationError(
                f"IFICOWCG upload rows changed while {country_label} clearing "
                "coverage was validated"
            )
        if (
            metadata.nonnull_record_references != metadata.upload_rows
            or metadata.distinct_record_references != metadata.upload_rows
        ):
            raise OutputFileGenerationError(
                f"{country_label} IFICOWCG RECORD_REFERENCE values must be "
                "non-null and unique"
            )
        if metadata.uploads_with_output != metadata.upload_rows:
            raise OutputFileGenerationError(
                f"Each {country_label} IFICOWCG upload must resolve at least one of the four "
                "QA OFICOWCG cursor branches"
            )
        if metadata.processed_gic_rows_without_one_owner != 0:
            raise OutputFileGenerationError(
                f"A {country_label} OFICOWCG clearing row is not owned by exactly one "
                "IFICOWCG upload from this process"
            )
        if (
            body_record_count is None
            or body_record_count < 0
            or metadata.cursor_rows <= 0
            or metadata.cursor_rows != body_record_count
        ):
            raise OutputFileGenerationError(
                f"{country_label} OFICOWCG cursor coverage does not match the "
                "reconstructed body"
            )
        if metadata.cursor_rows_with_multiple_rejections != 0:
            raise OutputFileGenerationError(
                f"A {country_label} OFICOWCG transaction has more than one clearing rejection; "
                "the QA package would not produce an unambiguous output"
            )
        if metadata.rejected_rows_with_multiple_error_lookups != 0:
            raise OutputFileGenerationError(
                f"A {country_label} OFICOWCG rejected transaction resolves more than one "
                "error-code row"
            )
        return metadata
    if country_key != "chile":
        raise OutputFileGenerationError(
            "OFICOWCG identity metadata is not verified for this country"
        )

    metadata = _OficowcgCoverageMetadata(*counts)
    if metadata.upload_rows <= 0 or metadata.upload_rows != candidate_upload_rows:
        raise OutputFileGenerationError(
            "IFICOWCG upload rows changed while clearing identity was validated"
        )
    if (
        metadata.nonnull_record_references != metadata.upload_rows
        or metadata.distinct_record_references != metadata.upload_rows
    ):
        raise OutputFileGenerationError(
            "IFICOWCG upload RECORD_REFERENCE values must be non-null and unique "
            "for exact OFICOWCG reconstruction"
        )
    if metadata.uploads_with_one_gic != metadata.upload_rows:
        raise OutputFileGenerationError(
            "Each IFICOWCG upload row must resolve exactly one matching "
            "GITM_CLEARING_LOG row; generation was stopped"
        )
    if (
        metadata.uploads_with_one_output
        + metadata.uploads_without_output
        + metadata.uploads_with_multiple_outputs
        != metadata.upload_rows
    ):
        raise OutputFileGenerationError(
            "Oracle returned inconsistent OFICOWCG identity metadata"
        )
    if (
        metadata.uploads_with_one_output != metadata.upload_rows
        or metadata.uploads_without_output != 0
        or metadata.uploads_with_multiple_outputs != 0
    ):
        raise OutputFileGenerationError(
            "Each IFICOWCG upload row must resolve exactly one pre-UNION "
            "OFICOWCG clearing row; generation was stopped instead of creating "
            "a missing or duplicated output"
        )
    if metadata.branch_b_rows_without_one_owner != 0:
        raise OutputFileGenerationError(
            "OFICOWCG's IFTB_CLEARING_UPLOAD branch contains a row that is not "
            "owned by exactly one IFICOWCG upload; generation was stopped"
        )
    return metadata


def _parse_qsimtp_snapshot_metadata(
    rows: list[str],
    *,
    process_ref: str,
    source: DataSourceChoice,
    candidate_upload_rows: int,
) -> _QsimtpSnapshotMetadata:
    values = [row.strip() for row in rows if row.strip()]
    if len(values) != 1:
        raise OutputFileGenerationError(
            "Oracle returned invalid OFQSIMTP snapshot metadata"
        )
    parts = values[0].split("|")
    if len(parts) != 22:
        raise OutputFileGenerationError(
            "Oracle returned invalid OFQSIMTP snapshot metadata"
        )
    numeric_indexes = (*range(19), 21)
    numeric: dict[int, int] = {}
    try:
        for index in numeric_indexes:
            numeric[index] = int(parts[index].strip())
    except ValueError as exc:
        raise OutputFileGenerationError(
            "Oracle returned invalid OFQSIMTP snapshot metadata"
        ) from exc
    if any(value < 0 for value in numeric.values()):
        raise OutputFileGenerationError(
            "Oracle returned invalid OFQSIMTP snapshot metadata"
        )

    metadata = _QsimtpSnapshotMetadata(
        upload_rows=numeric[0],
        distinct_record_references=numeric[1],
        distinct_alt_accounts=numeric[2],
        processed_uploads=numeric[3],
        failed_uploads=numeric[4],
        other_status_rows=numeric[5],
        processed_with_ten_rows=numeric[6],
        processed_bad_log_rows=numeric[7],
        failed_with_log_rows=numeric[8],
        matched_live_rows=numeric[9],
        live_rows=numeric[10],
        unique_live_keys=numeric[11],
        live_accounts=numeric[12],
        live_sim_dates=numeric[13],
        null_live_keys=numeric[14],
        cursor_ties=numeric[15],
        active_upload_rows=numeric[16],
        active_file_log_rows=numeric[17],
        active_requested_file_log_rows=numeric[18],
        latest_archive_upload_process=parts[19].strip(),
        latest_archive_file_log_process=parts[20].strip(),
        in_progress_control_rows=numeric[21],
    )

    if metadata.upload_rows <= 0 or metadata.upload_rows != candidate_upload_rows:
        raise OutputFileGenerationError(
            "OFQSIMTP snapshot upload count does not match the detected process"
        )
    if (
        metadata.distinct_record_references != metadata.upload_rows
        or metadata.distinct_alt_accounts != metadata.upload_rows
    ):
        raise OutputFileGenerationError(
            "OFQSIMTP upload RECORD_REFERENCE and ALT account keys must be unique"
        )
    if (
        metadata.other_status_rows != 0
        or metadata.processed_uploads + metadata.failed_uploads
        != metadata.upload_rows
    ):
        raise OutputFileGenerationError(
            "OFQSIMTP snapshot supports only exact P/E upload statuses"
        )
    if (
        metadata.processed_with_ten_rows != metadata.processed_uploads
        or metadata.processed_bad_log_rows != 0
        or metadata.failed_with_log_rows != 0
    ):
        raise OutputFileGenerationError(
            "OFQSIMTP requires exactly ten live log rows for every P upload and "
            "zero for every E upload"
        )
    expected_live_rows = metadata.processed_uploads * 10
    expected_sim_dates = 10 if metadata.processed_uploads else 0
    if (
        metadata.live_rows != expected_live_rows
        or metadata.matched_live_rows != metadata.live_rows
        or metadata.unique_live_keys != metadata.live_rows
        or metadata.live_accounts != metadata.processed_uploads
        or metadata.live_sim_dates != expected_sim_dates
        or metadata.null_live_keys != 0
    ):
        raise OutputFileGenerationError(
            "OFQSIMTP live log is incomplete, ambiguous, or is not wholly "
            "consumed by the requested process"
        )
    if metadata.cursor_ties != 0:
        raise OutputFileGenerationError(
            "OFQSIMTP has ambiguous RECORD_REFERENCE/SIM_DATE ordering ties"
        )
    if metadata.in_progress_control_rows != 0:
        raise OutputFileGenerationError(
            "OFQSIMTP input helper is still truncating or populating its live log"
        )

    normalized_ref = str(int(process_ref))
    if source is DataSourceChoice.ARCHIVE:
        if metadata.active_upload_rows != 0 or metadata.active_file_log_rows != 0:
            raise OutputFileGenerationError(
                "Archived OFQSIMTP reconstruction requires no active or newer "
                "IFQSIMTP process"
            )
        if (
            metadata.latest_archive_upload_process != normalized_ref
            or metadata.latest_archive_file_log_process != normalized_ref
            or metadata.live_rows == 0
        ):
            raise OutputFileGenerationError(
                "Archived OFQSIMTP can be reconstructed only for the latest "
                "authenticated upload and file-log snapshot"
            )
    else:
        if metadata.active_upload_rows != metadata.upload_rows:
            raise OutputFileGenerationError(
                "Active OFQSIMTP reconstruction requires one isolated current process"
            )
        if (
            metadata.active_requested_file_log_rows <= 0
            or metadata.active_file_log_rows
            != metadata.active_requested_file_log_rows
        ):
            raise OutputFileGenerationError(
                "Active OFQSIMTP file-log context is missing or belongs to another process"
            )
    return metadata


def _parse_stdinrou_dependency_metadata(
    rows: list[str],
    *,
    candidate_upload_rows: int,
) -> _StdinrouDependencyMetadata:
    values = [row.strip() for row in rows if row.strip()]
    if len(values) != 1:
        raise OutputFileGenerationError(
            "Oracle returned invalid STDINROU dependency metadata"
        )
    parts = values[0].split("|")
    if len(parts) != 15:
        raise OutputFileGenerationError(
            "Oracle returned invalid STDINROU dependency metadata"
        )
    try:
        counts = tuple(int(value.strip()) for value in parts[:14])
    except ValueError as exc:
        raise OutputFileGenerationError(
            "Oracle returned invalid STDINROU dependency metadata"
        ) from exc
    if any(value < 0 for value in counts):
        raise OutputFileGenerationError(
            "Oracle returned invalid STDINROU dependency metadata"
        )
    filename = _decode_utf8_hex(parts[14])
    metadata = _StdinrouDependencyMetadata(*counts, filename)

    if metadata.custom_rows <= 0 or metadata.standard_rows <= 0:
        raise OutputFileGenerationError(
            "STDINROU requires both custom body and standard footer rows"
        )
    if metadata.standard_interface_rows != candidate_upload_rows:
        raise OutputFileGenerationError(
            "STDINROU standard interface row count changed or was purged"
        )
    if metadata.custom_rows != metadata.custom_distinct_references:
        raise OutputFileGenerationError(
            "STDINRTS body RECORD_REFERENCE values must be unique"
        )
    if (
        metadata.custom_distinct_references
        != metadata.standard_distinct_references
        or metadata.custom_missing_from_standard != 0
        or metadata.standard_missing_from_custom != 0
    ):
        raise OutputFileGenerationError(
            "STDINRTS custom/standard reference coverage must be bidirectional"
        )
    if metadata.custom_null_statuses or metadata.standard_null_statuses:
        raise OutputFileGenerationError(
            "STDINROU cannot reproduce body/footer semantics with NULL status"
        )
    if (
        metadata.custom_processed + metadata.custom_unprocessed
        != metadata.custom_rows
        or metadata.standard_processed + metadata.standard_unprocessed
        != metadata.standard_rows
    ):
        raise OutputFileGenerationError(
            "STDINROU status counts do not fully cover their source populations"
        )
    if (
        metadata.custom_filename_count != 1
        or not metadata.custom_physical_filename
        or ";" in metadata.custom_physical_filename
        or "\r" in metadata.custom_physical_filename
        or "\n" in metadata.custom_physical_filename
    ):
        raise OutputFileGenerationError(
            "STDINRTS requires exactly one valid custom physical filename"
        )
    return metadata


def _parse_status_counts(
    rows: list[str],
    *,
    require_explicit_pe: bool = False,
    reject_lowercase: bool = False,
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        parts = row.split("|")
        if len(parts) != 2:
            raise OutputFileGenerationError("Oracle returned invalid footer status metadata")
        status, raw_count = (part.strip() for part in parts)
        if not status or status in counts:
            raise OutputFileGenerationError("Oracle returned invalid footer status metadata")
        if reject_lowercase and status != status.upper():
            raise OutputFileGenerationError(
                "CHISALCA contains a lowercase status that the QA package compares "
                "case-sensitively; generation was stopped"
            )
        try:
            count = int(raw_count)
        except ValueError as exc:
            raise OutputFileGenerationError(
                "Oracle returned invalid footer status metadata"
            ) from exc
        if count < 0:
            raise OutputFileGenerationError("Oracle returned invalid footer status metadata")
        counts[status.upper()] = count
    if require_explicit_pe and set(counts) != {"P", "E"}:
        raise OutputFileGenerationError("Oracle returned incomplete footer status metadata")
    if not counts:
        raise OutputFileGenerationError("Oracle returned incomplete footer status metadata")
    return counts


def _parse_input_physical_filename(rows: list[str]) -> str:
    values = [row.strip() for row in rows if row.strip()]
    if len(values) != 1:
        raise OutputFileGenerationError(
            "Oracle returned invalid physical input filename metadata"
        )
    parts = values[0].split("|", 1)
    if len(parts) != 2:
        raise OutputFileGenerationError(
            "Oracle returned invalid physical input filename metadata"
        )
    count_raw, filename_hex = parts
    try:
        count = int(count_raw)
    except ValueError as exc:
        raise OutputFileGenerationError(
            "Oracle returned invalid physical input filename metadata"
        ) from exc
    if count == 0:
        raise OutputFileGenerationError(
            "No physical input filename was found for this process"
        )
    if count != 1:
        raise OutputFileGenerationError(
            "More than one physical input filename was found; automatic header "
            "generation is ambiguous"
        )
    filename = _decode_utf8_hex(filename_hex).strip()
    _validate_output_field(filename, "physical input filename")
    if not filename:
        raise OutputFileGenerationError(
            "The physical input filename is empty"
        )
    return filename


def _select_candidate(
    candidates: list[ProcessCandidate],
    mappings: dict[str, set[str]],
    *,
    selected_spec: InterfaceSpec | None = None,
) -> tuple[InterfaceSpec, ProcessCandidate]:
    supported_by_input = {spec.input_code: spec for spec in supported_specs()}
    if selected_spec is not None:
        selected = [
            candidate
            for candidate in candidates
            if candidate.input_code == selected_spec.input_code
        ]
        if not selected:
            raise OutputFileGenerationError(
                f"The selected interface {selected_spec.input_code} "
                f"({selected_spec.output_code}) was not found for this process"
            )
        reconstructible = [
            candidate
            for candidate in selected
            if (
                not selected_spec.requires_upload_master
                or "UPLOAD_MASTER" in candidate.evidence_kinds
            )
        ]
        if not reconstructible:
            raise OutputFileGenerationError(
                f"The selected interface {selected_spec.input_code} was found only "
                "in the file log, but its upload rows are no longer available; "
                "refusing to generate an empty client file"
            )
        sources = {candidate.source for candidate in reconstructible}
        if len(sources) != 1:
            raise OutputFileGenerationError(
                f"The selected interface {selected_spec.input_code} exists in active "
                "and archived data; automatic source detection is ambiguous"
            )
        return selected_spec, reconstructible[0]

    if not candidates:
        raise OutputFileGenerationError(
            "No current evidence for this process was found in the GI repositories "
            "(GITU_UPLOAD_MASTER, GITA_UPLOAD_MASTER, GITB_FILE_LOG, or "
            "GITA_FILE_LOG). Verify PROD and the process number; the process may "
            "not exist or may have been purged and cannot be reconstructed "
            "automatically from that number alone"
        )

    unsupported = sorted(
        {
            candidate.input_code
            for candidate in candidates
            if candidate.input_code not in supported_by_input
        }
    )
    if unsupported:
        details: list[str] = []
        for input_code in unsupported:
            observed = [
                candidate
                for candidate in candidates
                if candidate.input_code == input_code
            ]
            evidence_kinds = set().union(
                *(candidate.evidence_kinds for candidate in observed)
            )
            has_upload_rows = "UPLOAD_MASTER" in evidence_kinds
            log_tables = sorted(
                {
                    (
                        "GITB_FILE_LOG"
                        if candidate.source is DataSourceChoice.ACTIVE
                        else "GITA_FILE_LOG"
                    )
                    for candidate in observed
                    if "FILE_LOG" in candidate.evidence_kinds
                }
            )
            unavailable = ""
            if not has_upload_rows:
                repositories = "/".join(log_tables) or "the GI file log"
                unavailable = (
                    f"found only in {repositories}; upload rows are unavailable, "
                    "so no reconstructible body remains; "
                )
            output_codes = sorted(mappings.get(input_code, set()))
            if output_codes:
                details.extend(
                    f"{input_code}->{output_code} ({unavailable}output adapter "
                    f"based on QA package GIPKS_{output_code} is not implemented yet)"
                    for output_code in output_codes
                )
            else:
                details.append(
                    f"{input_code} ({unavailable}the detected interface has no "
                    "OUTGOING_INTERFACE configured in GITM_INTERFACE_DEFINITION)"
                )
        raise OutputFileGenerationError(
            "The process contains interface mapping(s) that cannot be generated: "
            + ", ".join(details)
        )

    usable = [candidate for candidate in candidates if candidate.input_code in supported_by_input]
    reconstructible = [
        candidate
        for candidate in usable
        if (
            not supported_by_input[candidate.input_code].requires_upload_master
            or "UPLOAD_MASTER" in candidate.evidence_kinds
        )
    ]
    if not reconstructible:
        raise OutputFileGenerationError(
            "The process was found only in the file log, but its upload rows "
            "are no longer available; refusing to generate an empty client file"
        )
    input_codes = {candidate.input_code for candidate in reconstructible}
    if len(input_codes) != 1:
        raise OutputFileGenerationError(
            "The process contains more than one supported interface; automatic interface detection is ambiguous"
        )
    sources = {candidate.source for candidate in reconstructible}
    if len(sources) != 1:
        raise OutputFileGenerationError(
            "The process exists in active and archived data; automatic source detection is ambiguous"
        )
    candidate = reconstructible[0]
    spec = supported_by_input[candidate.input_code]
    return spec, candidate


def _validate_manual_process_scope(
    spec: InterfaceSpec,
    candidate: ProcessCandidate,
    candidates: list[ProcessCandidate],
) -> None:
    """Block QA contracts whose footer cannot isolate a shared process."""
    if spec.contract not in {"dcstout", "stdinrou"}:
        return
    other_upload_interfaces = sorted(
        {
            observed.input_code
            for observed in candidates
            if observed.source is candidate.source
            and observed.input_code != spec.input_code
            and "UPLOAD_MASTER" in observed.evidence_kinds
        }
    )
    if not other_upload_interfaces:
        return
    raise OutputFileGenerationError(
        f"{spec.output_code} cannot safely isolate this shared process: its QA "
        "footer counts all upload rows for the process, including "
        + ", ".join(other_upload_interfaces)
    )


def _parse_mappings(rows: list[str]) -> dict[str, set[str]]:
    mappings: dict[str, set[str]] = {}
    for row in rows:
        parts = row.split("|", 1)
        if len(parts) != 2:
            raise OutputFileGenerationError("Oracle returned malformed interface mapping metadata")
        try:
            input_code = normalize_interface_code(parts[0])
            output_code = normalize_interface_code(parts[1])
        except (TypeError, ValueError) as exc:
            raise OutputFileGenerationError(
                "Oracle returned invalid interface mapping metadata"
            ) from exc
        mappings.setdefault(input_code, set()).add(output_code)
    return mappings


def _validate_mapping(spec: InterfaceSpec, mappings: dict[str, set[str]]) -> None:
    outputs = mappings.get(spec.input_code, set())
    if outputs == {spec.output_code}:
        return
    if spec.output_code not in outputs:
        found = ", ".join(
            f"{spec.input_code}->{output_code}" for output_code in sorted(outputs)
        )
        suffix = f"; Oracle returned {found}" if found else ""
        raise OutputFileGenerationError(
            f"Oracle does not confirm {spec.input_code}->{spec.output_code}{suffix}"
        )
    found = ", ".join(
        f"{spec.input_code}->{output_code}" for output_code in sorted(outputs)
    )
    raise OutputFileGenerationError(
        "Oracle returned more than one outgoing interface for the detected "
        f"input; automatic mapping is ambiguous: {found}"
    )


def _validate_oficowcg_transaction_status_pair(
    status: str,
    txn_status: str,
) -> None:
    """Validate the status pairs emitted by the regional four-branch cursor."""
    allowed = {
        "P": {"SUCC", "REJR"},
        # The DD non-processed branch uses DECODE(E, 'U', 'NOPR'), so an E
        # record can contractually carry an empty transaction status.
        "E": {"ERRO", "NOPR", ""},
        "U": {"NOPR"},
    }
    if txn_status not in allowed.get(status, set()):
        shown = txn_status or "<NULL>"
        raise OutputFileGenerationError(
            f"Oracle returned an invalid OFICOWCG status pair: "
            f"{status or '<NULL>'}/{shown}"
        )


def _parse_body_records(
    rows: list[str],
    spec: InterfaceSpec,
) -> tuple[list[_BodyRecord], dict[str, int]]:
    records: list[_BodyRecord] = []
    counts: dict[str, int] = {}
    adapter = upload_body_adapter(spec.input_code)
    for row in rows:
        if spec.input_code == "IFDOBIEL":
            parts = row.split("|", 3)
            if len(parts) != 4:
                raise OutputFileGenerationError("Oracle returned a malformed body record")
            status, error_hex, param_hex, body = parts
            record = _BodyRecord(
                status=status.strip().upper(),
                body_prefix=body,
                error_code=_decode_utf8_hex(error_hex),
                error_param=_decode_utf8_hex(param_hex),
            )
        elif spec.input_code == "IFICOWCG":
            parts = row.split("|")
            transactional = _uses_transactional_oficowcg_contract(spec)
            expected_parts = 15 if transactional else 14
            if len(parts) != expected_parts:
                raise OutputFileGenerationError("Oracle returned a malformed body record")
            decoded = [_decode_utf8_hex(value) for value in parts]
            raw_status = decoded[0]
            status = raw_status.strip().upper()
            if raw_status != status:
                raise OutputFileGenerationError(
                    "IFICOWCG contains a non-canonical status that the QA "
                    "package emits verbatim; generation was stopped"
                )
            error_code = decoded[1]
            error_param = decoded[2]
            if transactional:
                body_fields = decoded[3:-1]
                txn_status = decoded[-1].strip().upper()
                _validate_oficowcg_transaction_status_pair(status, txn_status)
            else:
                body_fields = decoded[3:]
                txn_status = ""
            for field in body_fields:
                _validate_output_field(field, "OFICOWCG body field")
            record = _BodyRecord(
                status=status,
                body_prefix="BDY;" + ";".join(body_fields) + f";{status};",
                error_code=error_code,
                error_param=error_param,
                output_status=txn_status,
            )
            if status in {"E", "U"} or txn_status == "REJR":
                _validate_output_field(error_code, "OFICOWCG error code")
        elif spec.input_code == "IACMCLOS":
            parts = row.split("|")
            if len(parts) != 5:
                raise OutputFileGenerationError("Oracle returned a malformed body record")
            decoded = [_decode_utf8_hex(value) for value in parts]
            status = decoded[0].strip().upper()
            branch, account, error_code, error_param = decoded[1:]
            _validate_output_field(branch, "OACMCLOS branch")
            _validate_output_field(account, "OACMCLOS account")
            _validate_output_text(error_code, "OACMCLOS error list")
            _validate_output_text(error_param, "OACMCLOS error-parameter list")
            record = _BodyRecord(
                status=status,
                body_prefix=(
                    f"BDY;{branch};{account};"
                    + ("Y" if status == "P" else "N")
                    + ";"
                ),
                error_code=error_code,
                error_param=error_param,
            )
        elif spec.input_code == "CLADCHG":
            parts = row.split("|")
            if len(parts) != 5:
                raise OutputFileGenerationError("Oracle returned a malformed body record")
            status, account, internal_ref, error_code, error_param = (
                _decode_utf8_hex(value) for value in parts
            )
            for value, label in (
                (status, "CLADCHGO status"),
                (account, "CLADCHGO account"),
                (internal_ref, "CLADCHGO internal reference"),
                (error_code, "CLADCHGO error code"),
                (error_param, "CLADCHGO error parameters"),
            ):
                _validate_output_field(value, label, delimiter="^")
            record = _BodyRecord(
                status=status,
                body_prefix=f"02{account}^{internal_ref}^{status}^",
                error_code=error_code,
                error_param=error_param,
            )
        elif spec.input_code == "IFCRELVP":
            parts = row.split("|")
            if len(parts) != 6:
                raise OutputFileGenerationError("Oracle returned a malformed body record")
            decoded = [_decode_utf8_hex(value) for value in parts]
            _validate_output_field(
                decoded[0],
                "OFCRELVP status",
                delimiter="^",
            )
            status = decoded[0].strip().upper()
            body_fields = decoded[1:]
            for field in body_fields:
                _validate_output_field(
                    field,
                    "OFCRELVP body field",
                    delimiter="^",
                )
            if status == "P":
                body = "BH^" + "^".join((*body_fields[:3], "Y"))
            else:
                body = "BH^" + "^".join(body_fields)
            record = _BodyRecord(status=status, body_prefix=body)
        elif spec.input_code == "IFCHKPRT":
            parts = row.split("|")
            if len(parts) != 29:
                raise OutputFileGenerationError(
                    "Oracle returned a malformed OFCHKPRT body record"
                )
            decoded = tuple(_decode_utf8_hex(value) for value in parts)
            status, output_status = decoded[:2]
            fields = decoded[2:]
            expected_status = "S" if output_status == "S" else "<NON_S>"
            if status != expected_status:
                raise OutputFileGenerationError(
                    "Oracle returned inconsistent OFCHKPRT status metadata"
                )
            for field in (output_status, *fields):
                _validate_output_text(field, "OFCHKPRT source field")
            for index, label in (
                (0, "UNION row count"),
                (1, "REC_REF tie count"),
                (3, "upload-reference match count"),
                (4, "cheque-status match count"),
                (5, "error-message dependency count"),
            ):
                if not re.fullmatch(r"0|[1-9][0-9]*", fields[index]):
                    raise OutputFileGenerationError(
                        f"Oracle returned malformed OFCHKPRT {label} metadata"
                    )
            if not fields[2]:
                raise OutputFileGenerationError(
                    "Oracle returned an empty OFCHKPRT REC_REF"
                )
            document_type = fields[12].strip() or "01"
            if document_type != "01" and (
                fields[4] != "0" or fields[24]
            ):
                raise OutputFileGenerationError(
                    "OFCHKPRT queried cheque status for a non-cheque document"
                )
            if fields[4] != "1" and fields[24]:
                raise OutputFileGenerationError(
                    "OFCHKPRT cheque status does not match SELECT INTO cardinality"
                )
            record = _BodyRecord(
                status=status,
                body_prefix="",
                error_code=fields[25],
                error_param=fields[26],
                fields=fields,
                output_status=output_status,
            )
        elif spec.input_code == "IFIWDCLG":
            parts = row.split("|")
            if len(parts) != 24:
                raise OutputFileGenerationError(
                    "Oracle returned a malformed OFIWDCLG body record"
                )
            decoded = tuple(_decode_utf8_hex(value) for value in parts)
            status, output_status = decoded[:2]
            fields = decoded[2:]
            expected_status = "P" if output_status == "P" else "<NON_P>"
            if status != expected_status:
                raise OutputFileGenerationError(
                    "Oracle returned inconsistent OFIWDCLG status metadata"
                )
            for field in (output_status, *fields):
                _validate_output_text(field, "OFIWDCLG source field")
            for index, label in (
                (0, "UNION row count"),
                (1, "RECORD_REFERENCE tie count"),
                (3, "upload-reference match count"),
                (4, "clearing-log match count"),
                (5, "clearing-upload match count"),
                (6, "clearing-master match count"),
            ):
                if not re.fullmatch(r"0|[1-9][0-9]*", fields[index]):
                    raise OutputFileGenerationError(
                        f"Oracle returned malformed OFIWDCLG {label} metadata"
                    )
            if not fields[2]:
                raise OutputFileGenerationError(
                    "Oracle returned an empty OFIWDCLG RECORD_REFERENCE"
                )
            if fields[3] != "1" or fields[4] != "1":
                raise OutputFileGenerationError(
                    "OFIWDCLG requires exactly one upload row and clearing-log "
                    "row per RECORD_REFERENCE"
                )
            txn_status = fields[18]
            if txn_status == "NOPR":
                if fields[5] != "0" or fields[6] != "0":
                    raise OutputFileGenerationError(
                        "OFIWDCLG NOPR cardinality is inconsistent with QA"
                    )
            else:
                if fields[5] != "1":
                    raise OutputFileGenerationError(
                        "OFIWDCLG clearing upload is missing or ambiguous"
                    )
                expected_master_count = "1" if output_status == "P" else "0"
                if fields[6] != expected_master_count:
                    raise OutputFileGenerationError(
                        "OFIWDCLG clearing master is missing or ambiguous"
                    )
            record = _BodyRecord(
                status=status,
                body_prefix="",
                error_code=fields[20],
                error_param=fields[21],
                fields=fields,
                output_status=output_status,
            )
        elif spec.input_code == "IFIWADOC":
            parts = row.split("|")
            if len(parts) != 18:
                raise OutputFileGenerationError(
                    "Oracle returned a malformed OFIWADOC body record"
                )
            decoded = tuple(_decode_utf8_hex(value) for value in parts)
            status, output_status = decoded[:2]
            fields = decoded[2:]
            expected_status = "P" if output_status == "P" else "<NON_P>"
            if status != expected_status:
                raise OutputFileGenerationError(
                    "Oracle returned inconsistent OFIWADOC status metadata"
                )
            for field in (output_status, *fields):
                _validate_output_text(field, "OFIWADOC source field")
            for index, label in (
                (0, "source row count"),
                (1, "ADOC-log match count"),
                (2, "RECORD_REFERENCE tie count"),
            ):
                if not re.fullmatch(r"0|[1-9][0-9]*", fields[index]):
                    raise OutputFileGenerationError(
                        f"Oracle returned malformed OFIWADOC {label} metadata"
                    )
            if fields[1] != "1":
                raise OutputFileGenerationError(
                    "IFIWADOC requires exactly one GITM_CLEARING_ADOC_LOG row "
                    "per upload record"
                )
            if fields[2] != "1":
                raise OutputFileGenerationError(
                    "OFIWADOC has a duplicate RECORD_REFERENCE, so QA's ordering "
                    "is not byte-exact"
                )
            error_code = "GI-INT214" if output_status == "U" else fields[14]
            record = _BodyRecord(
                status=status,
                body_prefix="",
                error_code=error_code,
                error_param=fields[15],
                fields=fields,
                output_status=output_status,
            )
        elif spec.input_code == "IFQSIMTP":
            parts = row.split("|")
            if len(parts) != 15:
                raise OutputFileGenerationError(
                    "Oracle returned a malformed OFQSIMTP body record"
                )
            decoded = tuple(_decode_utf8_hex(value) for value in parts)
            status, output_status = decoded[:2]
            fields = decoded[2:]
            if output_status not in {"P", "E"} or status != output_status:
                raise OutputFileGenerationError(
                    "OFQSIMTP preserves and supports only exact P/E raw statuses"
                )
            for field in (output_status, *fields):
                _validate_output_text(field, "OFQSIMTP source field")
            if not re.fullmatch(r"[0-9]+", fields[0]):
                raise OutputFileGenerationError(
                    "Oracle returned malformed OFQSIMTP RECORD_REFERENCE metadata"
                )
            if not re.fullmatch(r"0|[1-9][0-9]*", fields[1]):
                raise OutputFileGenerationError(
                    "Oracle returned malformed OFQSIMTP cursor-tie metadata"
                )
            if fields[1] != "1":
                raise OutputFileGenerationError(
                    "OFQSIMTP has ambiguous RECORD_REFERENCE/SIM_DATE ordering ties"
                )
            compamt = fields[12]
            if compamt and (
                not compamt.endswith(";")
                or len(compamt[:-1].split(";")) != 34
            ):
                raise OutputFileGenerationError(
                    "OFQSIMTP COMPAMT does not contain exactly 17 name/amount pairs"
                )
            record = _BodyRecord(
                status=status,
                body_prefix="",
                error_code=fields[5],
                error_param=fields[6],
                fields=fields,
                output_status=output_status,
            )
        elif spec.input_code == "STDINRTS":
            parts = row.split("|")
            if len(parts) != 10:
                raise OutputFileGenerationError(
                    "Oracle returned a malformed STDINROU body record"
                )
            decoded = tuple(_decode_utf8_hex(value) for value in parts)
            status, raw_status = decoded[:2]
            fields = decoded[2:]
            expected_status = "P" if raw_status == "P" else "<NON_P>"
            if not raw_status or status != expected_status:
                raise OutputFileGenerationError(
                    "Oracle returned inconsistent STDINROU status metadata"
                )
            for field in (raw_status, *fields):
                _validate_output_text(field, "STDINROU source field")
            if not re.fullmatch(r"[0-9]+", fields[0]):
                raise OutputFileGenerationError(
                    "Oracle returned malformed STDINROU RECORD_REFERENCE metadata"
                )
            if not re.fullmatch(r"0|[1-9][0-9]*", fields[1]):
                raise OutputFileGenerationError(
                    "Oracle returned malformed STDINROU RECORD_REFERENCE tie metadata"
                )
            if fields[1] != "1":
                raise OutputFileGenerationError(
                    "STDINRTS body RECORD_REFERENCE values must be unique"
                )
            record = _BodyRecord(
                status=status,
                body_prefix="",
                error_code=fields[6],
                error_param=fields[7],
                fields=fields,
                output_status=raw_status,
            )
        elif spec.input_code == "IFEARLCG":
            parts = row.split("|")
            if len(parts) != 17:
                raise OutputFileGenerationError(
                    "Oracle returned a malformed IFOARLCG body record"
                )
            decoded = tuple(_decode_utf8_hex(value) for value in parts)
            status, raw_status = decoded[:2]
            metadata = decoded[2:5]
            data = decoded[5:16]
            error_code = decoded[16]
            expected_status = {"SUCC": "P", "ERR": "E"}.get(raw_status)
            if expected_status is None:
                raise OutputFileGenerationError(
                    "IFEARLCG clearing status must be exactly SUCC or ERR"
                )
            if status != expected_status:
                raise OutputFileGenerationError(
                    "Oracle returned inconsistent IFOARLCG status metadata"
                )
            for value, label in zip(
                metadata,
                ("source row count", "child match count", "XREF count"),
            ):
                if not re.fullmatch(r"0|[1-9][0-9]*", value):
                    raise OutputFileGenerationError(
                        f"Oracle returned malformed IFOARLCG {label} metadata"
                    )
            for field in data:
                _validate_output_text(field, "IFOARLCG source field")
            _validate_output_text(error_code, "IFOARLCG error-code list")
            record = _BodyRecord(
                status=status,
                body_prefix="",
                error_code=error_code,
                fields=(*metadata, *data),
                output_status=raw_status,
            )
        elif spec.input_code == "IFSTDCST":
            parts = row.split("|")
            if len(parts) != 20:
                raise OutputFileGenerationError(
                    "Oracle returned a malformed DCSTOUT body record"
                )
            decoded = tuple(_decode_utf8_hex(value) for value in parts)
            status = decoded[0]
            output_status = decoded[1]
            fields = decoded[2:]
            for field in fields:
                _validate_output_text(field, "DCSTOUT source field")
            if status == "<NULL>" or not output_status:
                raise OutputFileGenerationError(
                    "IFSTDCST contains NULL status; its QA footer would not "
                    "represent that body row"
                )
            if status not in {"P", "<NON_P>"}:
                raise OutputFileGenerationError(
                    "Oracle returned malformed DCSTOUT status metadata"
                )
            if fields[0] != "IFSTDCST":
                raise OutputFileGenerationError(
                    "The process contains mixed interface rows; DCSTOUT generation "
                    "was stopped"
                )
            for index, label in (
                (1, "process row count"),
                (2, "interface row count"),
                (3, "file-log context count"),
                (4, "file-log fallback flag"),
                (7, "record-reference match count"),
                (15, "error-message match count"),
            ):
                if not re.fullmatch(r"0|[1-9][0-9]*", fields[index]):
                    raise OutputFileGenerationError(
                        f"Oracle returned malformed DCSTOUT {label} metadata"
                    )
            if fields[4] not in {"0", "1"}:
                raise OutputFileGenerationError(
                    "Oracle returned malformed DCSTOUT file-log fallback metadata"
                )
            if (
                fields[4] == "1"
                and (fields[3] != "1" or not fields[5] or not fields[6])
            ):
                raise OutputFileGenerationError(
                    "IFSTDCST did not resolve one coherent, non-empty USER_ID/"
                    "BRANCH_CODE tuple from its file log"
                )
            if fields[4] == "0" and (
                fields[3] != "0" or fields[5] or fields[6]
            ):
                raise OutputFileGenerationError(
                    "Oracle returned inconsistent DCSTOUT file-log fallback metadata"
                )
            if fields[7] != "1":
                raise OutputFileGenerationError(
                    "IFSTDCST RECORD_REFERENCE is not unique in its upload source; "
                    "generation was stopped"
                )
            record = _BodyRecord(
                status=status,
                body_prefix="",
                error_code=fields[14],
                fields=fields,
                output_status=output_status,
            )
        elif adapter is not None:
            parts = row.split("|")
            expected = len(adapter.fields) + 2
            if spec.contract == "chisalou_peru":
                expected += 1
            if len(parts) != expected:
                raise OutputFileGenerationError(
                    "Oracle returned a malformed declarative body record"
                )
            decoded = tuple(_decode_utf8_hex(value) for value in parts)
            status = decoded[0].strip().upper()
            output_status = decoded[1]
            fields = decoded[2:]
            for field in fields:
                _validate_output_text(field, f"{spec.output_code} source field")
            if (
                adapter.style in {"chiclou", "cmrclou"}
                and output_status not in {"P", "E"}
            ):
                # Both QA packages simply omit statuses outside their two
                # explicit IF/ELSIF branches. Keep that row-level behavior;
                # their LF footer carries no counts that would expose it.
                continue
            if adapter.style in {
                "chisalou",
                "giupd",
                "oacmassc",
            }:
                compared_status = output_status.strip()
                if compared_status != compared_status.upper():
                    raise OutputFileGenerationError(
                        f"{spec.input_code} contains a lowercase status that the QA "
                        "package compares case-sensitively; generation was stopped"
                    )
            if adapter.style in {"chiclou", "cmrclou"}:
                account_count = fields[3]
                if not re.fullmatch(r"0|[1-9][0-9]*", account_count):
                    raise OutputFileGenerationError(
                        f"Oracle returned malformed {spec.output_code} account metadata"
                    )
                if adapter.style == "chiclou" and not re.fullmatch(
                    r"0|[1-9][0-9]*", fields[5]
                ):
                    raise OutputFileGenerationError(
                        "Oracle returned malformed CHICLOU message metadata"
                    )
                if status == "P" and int(account_count) > 1:
                    raise OutputFileGenerationError(
                        f"A processed {spec.input_code} account resolved more than one "
                        "CLTB_ACCOUNT_MASTER row; generation was stopped"
                    )
            if adapter.style == "stdcifom":
                if output_status not in {"P", "E"} or status != output_status:
                    raise OutputFileGenerationError(
                        "STDCIFMO status metadata is inconsistent with the QA contract"
                    )
                if not re.fullmatch(r"0|[1-9][0-9]*", fields[2]):
                    raise OutputFileGenerationError(
                        "Oracle returned malformed STDCIFOM message metadata"
                    )
            if adapter.style == "chisalou":
                teller_count = fields[0]
                if not re.fullmatch(r"0|[1-9][0-9]*", teller_count):
                    raise OutputFileGenerationError(
                        "Oracle returned malformed CHISALOU teller metadata"
                    )
                if status == "P" and teller_count != "1":
                    raise OutputFileGenerationError(
                        "A processed CHISALCA XREF did not resolve exactly one "
                        "DETB_RTL_TELLER row; generation was stopped"
                    )
            record = _BodyRecord(
                status=status,
                body_prefix="",
                error_code=fields[-2],
                error_param=fields[-1],
                fields=fields,
                output_status=output_status,
            )
        else:
            parts = row.split("|", 1)
            if len(parts) != 2:
                raise OutputFileGenerationError("Oracle returned a malformed body record")
            status, body = parts
            record = _BodyRecord(status=status.strip().upper(), body_prefix=body)
        if spec.input_code in {
            "IFCHKPRT",
            "IFIWADOC",
            "IFIWDCLG",
            "IFQSIMTP",
            "STDINRTS",
            "IFEARLCG",
            "IFSTDCST",
        }:
            malformed = not record.status or not record.fields
        elif adapter is not None:
            malformed = not record.status
        else:
            if spec.input_code == "IFCRELVP":
                expected_prefix = "BH^"
            elif spec.input_code == "CLADCHG":
                expected_prefix = "02"
            else:
                expected_prefix = "BDY;"
            malformed = (
                not record.status
                or not record.body_prefix.startswith(expected_prefix)
            )
        if malformed:
            raise OutputFileGenerationError("Oracle returned a malformed body record")
        counts[record.status] = counts.get(record.status, 0) + 1
        records.append(record)
    if spec.input_code == "IFCHKPRT":
        if not records:
            raise OutputFileGenerationError(
                "IFCHKPRT contains no reconstructible UNION body rows"
            )
        union_counts = {int(record.fields[0]) for record in records}
        if union_counts != {len(records)}:
            raise OutputFileGenerationError(
                "IFCHKPRT UNION body is incomplete or changed during extraction"
            )
        tied = sorted(
            record.fields[2]
            for record in records
            if record.fields[1] != "1"
        )
        if tied:
            raise OutputFileGenerationError(
                "OFCHKPRT has distinct UNION rows tied on REC_REF: "
                + ", ".join(tied)
            )
        rec_refs = [record.fields[2] for record in records]
        if len(set(rec_refs)) != len(rec_refs):
            raise OutputFileGenerationError(
                "OFCHKPRT REC_REF ordering is not unique"
            )
        if any(record.fields[3] != "1" for record in records):
            raise OutputFileGenerationError(
                "OFCHKPRT UNION rows do not map one-to-one to upload records"
            )
    if spec.input_code == "IFIWDCLG":
        if not records:
            raise OutputFileGenerationError(
                "IFIWDCLG has upload rows but no reconstructible body: mandatory "
                "GITM_CLEARING_LOG rows are missing or were purged"
            )
        union_counts = {int(record.fields[0]) for record in records}
        if union_counts != {len(records)}:
            raise OutputFileGenerationError(
                "IFIWDCLG UNION body is incomplete or changed during extraction"
            )
        tied = sorted(
            record.fields[2]
            for record in records
            if record.fields[1] != "1"
        )
        if tied:
            raise OutputFileGenerationError(
                "OFIWDCLG has distinct UNION rows tied on RECORD_REFERENCE: "
                + ", ".join(tied)
            )
        record_references = [record.fields[2] for record in records]
        if len(set(record_references)) != len(record_references):
            raise OutputFileGenerationError(
                "OFIWDCLG RECORD_REFERENCE ordering is not unique"
            )
    if spec.input_code == "IFIWADOC":
        if not records:
            raise OutputFileGenerationError(
                "IFIWADOC has upload rows but no reconstructible body: mandatory "
                "GITM_CLEARING_ADOC_LOG rows are missing or were purged"
            )
        source_counts = {int(record.fields[0]) for record in records}
        if source_counts != {len(records)}:
            raise OutputFileGenerationError(
                "IFIWADOC source row count does not match its joined body; "
                "mandatory ADOC-log data is incomplete or changed"
            )
    if spec.input_code == "IFQSIMTP":
        if not records:
            raise OutputFileGenerationError(
                "IFQSIMTP contains no authenticated body rows"
            )
        order_keys = [
            (int(record.fields[0]), record.fields[11] or "99999999")
            for record in records
        ]
        if len(set(order_keys)) != len(order_keys):
            raise OutputFileGenerationError(
                "OFQSIMTP has duplicate RECORD_REFERENCE/SIM_DATE keys"
            )
        if order_keys != sorted(order_keys):
            raise OutputFileGenerationError(
                "Oracle returned OFQSIMTP rows outside QA cursor order"
            )
    if spec.input_code == "STDINRTS":
        if not records:
            raise OutputFileGenerationError(
                "STDINRTS_UPLOAD_MASTER contains no reconstructible body rows"
            )
        record_references = [int(record.fields[0]) for record in records]
        if len(set(record_references)) != len(record_references):
            raise OutputFileGenerationError(
                "STDINRTS body RECORD_REFERENCE values must be unique"
            )
        if record_references != sorted(record_references):
            raise OutputFileGenerationError(
                "Oracle returned STDINROU rows outside QA cursor order"
            )
    if spec.input_code == "IFEARLCG":
        if not records:
            raise OutputFileGenerationError(
                "IFEARLCG contains no reconstructible clearing rows"
            )
        if any(record.fields[1] != "1" for record in records):
            raise OutputFileGenerationError(
                "Each IFEARLCG source row must resolve exactly one "
                "IFTB_CLEARING_UPLOAD_C row"
            )
        if any(record.fields[2] != "1" for record in records):
            raise OutputFileGenerationError(
                "IFEARLCG contains duplicate XREF values, so QA's ORDER BY XREF "
                "does not define a byte-exact row order"
            )
        source_counts = {int(record.fields[0]) for record in records}
        if source_counts != {len(records)}:
            raise OutputFileGenerationError(
                "IFEARLCG source row count changed or its clearing join is incomplete"
            )
    if spec.input_code == "IFSTDCST":
        if not records:
            raise OutputFileGenerationError(
                "IFSTDCST contains no reconstructible upload rows"
            )
        process_counts = {int(record.fields[1]) for record in records}
        interface_counts = {int(record.fields[2]) for record in records}
        if (
            len(process_counts) != 1
            or len(interface_counts) != 1
            or process_counts != interface_counts
            or process_counts != {len(records)}
        ):
            raise OutputFileGenerationError(
                "The process contains mixed interface rows or changed while "
                "DCSTOUT was being reconstructed"
            )
    return records, counts


def _parse_chisalca_upload_rows(
    rows: list[str],
    *,
    include_ccicode: bool = False,
) -> list[_ChisalcaUploadRow]:
    parsed: list[_ChisalcaUploadRow] = []
    for raw_row in rows:
        parts = raw_row.split("|")
        expected_parts = 11 if include_ccicode else 10
        if len(parts) != expected_parts:
            raise OutputFileGenerationError(
                "Oracle returned a malformed CHISALCA upload record"
            )
        decoded = tuple(_decode_utf8_hex(value) for value in parts)
        for value in decoded:
            _validate_output_text(value, "CHISALCA source field")
        parsed.append(
            _ChisalcaUploadRow(
                normalized_status=decoded[0],
                raw_status=decoded[1],
                xref=decoded[2],
                fallback_account=decoded[3],
                fallback_branch=decoded[4],
                fallback_amount=decoded[5],
                fallback_date=decoded[6],
                fallback_currency=decoded[7],
                ccicode=decoded[8] if include_ccicode else "",
                error_code=decoded[9] if include_ccicode else decoded[8],
                error_param=decoded[10] if include_ccicode else decoded[9],
            )
        )
    return parsed


def _parse_chisalca_teller_rows(
    rows: list[str],
    requested_xrefs: set[str],
) -> dict[str, _ChisalcaTellerRow]:
    parsed: dict[str, _ChisalcaTellerRow] = {}
    for raw_row in rows:
        parts = raw_row.split("|")
        if len(parts) != 9:
            raise OutputFileGenerationError(
                "Oracle returned malformed CHISALCA teller metadata"
            )
        decoded = tuple(_decode_utf8_hex(value) for value in parts)
        for value in decoded:
            _validate_output_text(value, "CHISALCA teller field")
        xref, count = decoded[:2]
        if xref not in requested_xrefs:
            raise OutputFileGenerationError(
                "Oracle returned unexpected CHISALCA teller metadata"
            )
        if xref in parsed:
            raise OutputFileGenerationError(
                "Oracle returned ambiguous CHISALCA teller metadata"
            )
        if not re.fullmatch(r"[1-9][0-9]*", count):
            raise OutputFileGenerationError(
                "Oracle returned malformed CHISALCA teller metadata"
            )
        parsed[xref] = _ChisalcaTellerRow(
            xref=xref,
            count=count,
            transaction_reference=decoded[2],
            output_xref=decoded[3],
            account=decoded[4],
            branch=decoded[5],
            amount=decoded[6],
            transaction_date=decoded[7],
            currency=decoded[8],
        )
    return parsed


def _compose_chisalca_body_rows(
    upload_rows: list[_ChisalcaUploadRow],
    teller_rows: list[str],
    *,
    include_ccicode: bool = False,
) -> list[str]:
    requested_xrefs = {
        row.xref
        for row in upload_rows
        if row.raw_status.strip() == "P" and row.xref
    }
    tellers = _parse_chisalca_teller_rows(teller_rows, requested_xrefs)
    composed: list[str] = []
    for upload in upload_rows:
        if upload.raw_status.strip() == "P":
            teller = tellers.get(upload.xref)
            contract_fields = (
                (
                    teller.count,
                    teller.transaction_reference,
                    teller.output_xref,
                    teller.account,
                    teller.branch,
                    teller.amount,
                    teller.transaction_date,
                    teller.currency,
                    *((upload.ccicode,) if include_ccicode else ()),
                )
                if teller is not None
                else (
                    "0",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    *((upload.ccicode,) if include_ccicode else ()),
                )
            )
        else:
            contract_fields = (
                "0",
                "",
                upload.xref,
                upload.fallback_account,
                upload.fallback_branch,
                upload.fallback_amount,
                upload.fallback_date,
                upload.fallback_currency,
                *((upload.ccicode,) if include_ccicode else ()),
            )
        composed.append(
            "|".join(
                _encode_utf8_hex(value)
                for value in (
                    upload.normalized_status,
                    upload.raw_status,
                    *contract_fields,
                    upload.error_code,
                    upload.error_param,
                )
            )
        )
    return composed


_U_STATUS_FALLBACK_CONTRACTS = {
    "cmrcifou",
    "ouchbkcu",
    "stdcifou",
    "stdcrdou",
}


def _effective_error_fields(
    spec: InterfaceSpec,
    record: _BodyRecord,
) -> tuple[str, str]:
    if spec.contract == "giupd" and record.status == "U":
        return "GI-INT214;", record.error_param
    if (
        spec.contract in _U_STATUS_FALLBACK_CONTRACTS
        and record.status == "U"
    ):
        return "GI-INT214", "Failed to process data"
    return record.error_code, record.error_param


def _collect_error_codes(
    spec: InterfaceSpec,
    records: list[_BodyRecord],
) -> set[str]:
    """Return only the ERTB_MSGS keys the QA contract actually resolves."""
    if spec.input_code == "IFCHKPRT":
        return {
            code
            for record in records
            if record.status != "S"
            for code in split_oacmclos_error_codes(record.error_code)
        }
    if spec.input_code == "IFIWDCLG":
        return {
            code
            for record in records
            if record.output_status in {"E", "U"} or record.fields[18] == "REJR"
            for code in split_oacmclos_error_codes(record.error_code)
        }
    if spec.input_code == "IFIWADOC":
        return {
            code
            for record in records
            if record.output_status in {"E", "U"}
            for code in split_oacmclos_error_codes(record.error_code)
        }
    if spec.input_code == "IFQSIMTP":
        return {
            lookup
            for record in records
            if record.error_code
            for lookup in (record.error_code.replace(";", ""),)
            if lookup
        }
    if spec.input_code == "STDINRTS":
        # GIPKS_STDINROU calls PR_GET_ERRMSG for every body row, including P.
        # Its helper walks the positional semicolon list only up to EOPL.
        return {
            code
            for record in records
            for code in split_oacmclos_error_codes(record.error_code)
        }
    if spec.input_code == "IFOBTUPD":
        # GIPKS_OFOBTUPD compares the untrimmed STATUS value. An ERROR stored
        # on P/U/NULL (or even " E ") is emitted nowhere and must not trigger
        # an ERTB_MSGS lookup.
        return {
            code
            for record in records
            if record.output_status == "E" and record.error_code
            for code in split_oacmclos_error_codes(record.error_code)
        }
    if spec.input_code == "IFICOWCG":
        return {
            record.error_code
            for record in records
            if (
                record.status in {"E", "U"}
                or (
                    _uses_transactional_oficowcg_contract(spec)
                    and record.output_status == "REJR"
                )
            )
            and record.error_code
        }
    if spec.input_code == "IACMCLOS":
        return {
            code
            for record in records
            for code in split_oacmclos_error_codes(record.error_code)
        }
    if spec.input_code == "CHISALCA":
        return {
            code
            for record in records
            if record.status == "E"
            for code in split_oacmclos_error_codes(record.error_code)
        }
    if spec.input_code == "CLADCHG":
        return {
            record.error_code.replace(";", "")
            for record in records
            if record.error_code and record.error_code.replace(";", "")
        }
    if spec.input_code == "IFDOBIEL":
        return {
            code
            for record in records
            for code in split_error_codes(record.error_code)
            if code not in {"I-SUCCESS", "EOPL"}
        }
    if spec.input_code == "CMRCLUPD":
        # FN_GET_ERROR_DESC does not treat a literal EOPL stored in ERROR as
        # its parser sentinel; it attempts that lookup like any other code.
        return {
            code
            for record in records
            for code in split_error_codes(record.error_code)
        }

    mode = spec.error_message_mode
    if not mode:
        return set()
    codes: set[str] = set()
    for record in records:
        error_code, _ = _effective_error_fields(spec, record)
        if not error_code:
            continue
        if mode == "list_tilde_nonp" and record.status == "P":
            continue
        if spec.contract in {"ofmdcgen", "ofmdsupd"} and record.status != "E":
            continue
        if spec.contract == "cmrrelvo" and record.status == "P":
            continue
        if mode in {
            "list_tilde",
            "list_tilde_all",
            "list_tilde_nonp",
            "list_prefixed",
        }:
            codes.update(split_oacmclos_error_codes(error_code))
        elif mode == "list_plain_trailing":
            codes.update(split_oacmclos_error_codes(error_code))
        elif mode in {"list_plain", "list_escaped"}:
            codes.update(
                code
                for code in split_error_codes(error_code)
                if code not in {"I-SUCCESS", "EOPL"}
            )
        elif mode == "single_strip_semicolons":
            lookup = error_code.replace(";", "")
            if lookup:
                codes.add(lookup)
        elif mode == "single_after_escape":
            lookup = error_code.replace(";", "~")
            if lookup:
                codes.add(lookup)
        else:
            raise OutputFileGenerationError(
                f"Unsupported error-message mode for {spec.input_code}"
            )
    return codes


def _format_tilde_message_list(
    error_code: str,
    error_param: str,
    messages: dict[str, str],
) -> str:
    """Reproduce the PR_GET_ERRMSG helpers used by upload packages."""
    if not error_code:
        return error_param
    codes = split_oacmclos_error_codes(error_code)
    params = str(error_param or "").split(";")
    return "".join(
        format_form_message(
            code,
            params[index] if index < len(params) else "",
            messages,
        )
        + "~"
        for index, code in enumerate(codes)
    )


def _format_debit_message_list(
    error_code: str,
    error_param: str,
    messages: dict[str, str],
) -> str:
    """Reproduce STPKS_DEBIT_CARD_CUSTOM.FN_GET_ERR_MSG."""
    if not error_code:
        return ""
    codes = split_oacmclos_error_codes(error_code)
    params = str(error_param or "").split(";")
    return "".join(
        format_form_message(
            code,
            params[index] if index < len(params) else "",
            messages,
        )
        + ";"
        for index, code in enumerate(codes)
    )


def _format_prefixed_message_list(
    error_code: str,
    error_param: str,
    messages: dict[str, str],
) -> str:
    """Build OFGLCRTE's ``;message`` list with tilde-terminated params."""
    if not error_code:
        return ""
    codes = split_oacmclos_error_codes(error_code)
    params = str(error_param or "").split(";")
    return "".join(
        ";"
        + format_form_message(
            code,
            (params[index] if index < len(params) else "") + "~",
            messages,
        )
        for index, code in enumerate(codes)
    )


def _validate_delimited_values(
    values: tuple[str, ...] | list[str],
    label: str,
    *,
    delimiter: str = ";",
) -> None:
    for value in values:
        _validate_output_field(value, label, delimiter=delimiter)


def _fixed_field(value: str, width: int) -> str:
    return str(value or "")[:width].ljust(width)


def _star_field(value: str, width: int) -> str:
    text = str(value or "")
    return text[:width].ljust(width, "*") if text else "*" * width


def _render_upload_body_record(
    spec: InterfaceSpec,
    record: _BodyRecord,
    messages: dict[str, str],
    error_types: dict[str, str] | None = None,
    direct_message: str | None = None,
) -> str:
    adapter = upload_body_adapter(spec.input_code)
    compatible_style = adapter is not None and (
        adapter.style == spec.contract
        or (
            adapter.style == "chisalou"
            and spec.contract in CHISALOU_CONTRACTS
        )
    )
    if not compatible_style:
        raise OutputFileGenerationError(
            f"Missing declarative body renderer for {spec.input_code}"
        )
    fields = record.fields
    style = adapter.style
    error_code, error_param = _effective_error_fields(spec, record)
    message_types = error_types or {}

    if style == "chisalou":
        data = fields[1:9] if spec.contract == "chisalou_peru" else fields[1:8]
        _validate_delimited_values(data, "CHISALOU body field")
        _validate_output_text(error_code, "CHISALOU error-code list")
        _validate_output_text(error_param, "CHISALOU error-parameter list")
        prefix = "BDY;" + ";".join(data) + ";"
        if record.status == "P":
            return prefix + "Y;" + error_code + ";" + error_param + ";"
        if record.status == "U":
            # GIPKS_CHISALOU omits the delimiter after its N flag. This typo is
            # part of the client-visible contract and must remain byte-exact.
            return prefix + "NGI-INT214*Failed to process data*;"

        codes = split_oacmclos_error_codes(error_code)
        param_groups = str(error_param or "").split(";")
        kept: list[tuple[str, str]] = []
        for index, code in enumerate(codes):
            if (
                spec.error_message_mode != "list_tilde_all"
                and message_types.get(code, "") == "O"
            ):
                continue
            param = param_groups[index] if index < len(param_groups) else ""
            kept.append((code, param))
        if not kept:
            return prefix + "N;;"
        code_output = ";".join(code for code, _param in kept)
        description = "".join(
            format_form_message(code, param, messages) + "~"
            for code, param in kept
        )
        _validate_output_text(description, "CHISALOU error description")
        return prefix + "N;" + code_output + ";" + description + ";"

    if style == "giupd":
        data = fields[:4]
        _validate_delimited_values(data, "GIUPDSTS body field")
        prefix = "BDY01;" + ";".join(data)
        if record.status == "P":
            return prefix + ";Y;"
        prefix += ";N;"
        if record.status == "U":
            description = _format_tilde_message_list(
                error_code,
                error_param,
                messages,
            )
            _validate_output_field(description, "GIUPDSTS error description")
            return prefix + "GI-INT214;;" + description + ";"
        error_output = error_code.replace(";", "~")
        _validate_output_field(error_output, "GIUPDSTS error code")
        if not error_code:
            return prefix + ";"
        description = _format_tilde_message_list(
            error_code,
            error_param,
            messages,
        )
        _validate_output_field(description, "GIUPDSTS error description")
        return prefix + error_output + ";" + description + ";"

    if style == "oacmassc":
        data = fields[:8]
        _validate_delimited_values(data, "OACMASSC body field")
        if record.status == "P":
            error_output = ""
            description = ""
        else:
            error_output = format_error_code(error_code)
            description = _format_tilde_message_list(
                error_code,
                error_param,
                messages,
            )
        _validate_delimited_values(
            [error_output, description],
            "OACMASSC error field",
        )
        return (
            "BDY;"
            + ";".join(
                (*data, "Y" if record.status == "P" else "N", error_output, description)
            )
            + ";"
        )

    if style == "chbookou":
        data = fields[:6]
        _validate_delimited_values(data, "CHBOOKOU body field")
        error_output = error_code[:36]
        description = _format_tilde_message_list(
            error_code,
            error_param,
            messages,
        )
        _validate_output_text(error_output, "CHBOOKOU error field")
        _validate_output_text(description, "CHBOOKOU error description")
        return (
            "BDY;"
            + ";".join(
                (*data, "Y" if record.status == "P" else "N", error_output, description)
            )
            + ";"
        )

    if style in {"cloirfap", "oxcgrate"}:
        data = fields[:3]
        output_status = record.output_status
        _validate_delimited_values(data, f"{spec.output_code} body field")
        error_output = format_error_code(error_code)
        description = format_error_description(
            error_code,
            error_param,
            messages,
        )
        _validate_delimited_values(
            [output_status, error_output, description],
            f"{spec.output_code} status/error field",
        )
        prefix = "BDY" if style == "cloirfap" else "BHD"
        return (
            prefix
            + ";"
            + ";".join((*data, output_status, error_output, description))
            + ";"
        )

    if style == "closlres":
        data = fields[:2]
        output_status = record.output_status
        _validate_delimited_values(
            [*data, output_status],
            "CLOSLRES body field",
        )
        _validate_output_text(error_code, "CLOSLRES error code")
        _validate_output_text(error_param, "CLOSLRES error parameters")
        return "BDY;" + ";".join((*data, output_status, error_code, error_param)) + ";"

    if style in {"chiclou", "cmrclou"}:
        alt_account, account, branch = fields[:3]
        _validate_delimited_values(
            [alt_account, account, branch],
            f"{spec.output_code} account field",
            delimiter="^",
        )
        if record.status == "P":
            account_status = fields[4]
            _validate_output_field(
                account_status,
                f"{spec.output_code} account status",
                delimiter="^",
            )
            return f"{alt_account}^{account}^{branch}^{account_status}^P^"

        if style == "chiclou":
            # QA selects ERTB_MSGS.MESSAGE directly, without language filtering
            # or FN_FORMMSG parameter substitution. Its SELECT INTO yields no
            # message unless the error code identifies exactly one row.
            description = (
                direct_message
                if direct_message is not None
                else (fields[6] if fields[5] == "1" else "")
            )
        else:
            # QA delegates CMRCLUPD errors to
            # GIPKS_#CLDPYMNT.FN_GET_ERROR_DESC; reproduce that helper locally
            # from SELECTed ERTB_MSGS templates and the stored parameter list.
            description = format_error_description_list(
                error_code,
                error_param,
                messages,
            ).rstrip()
        _validate_delimited_values(
            [error_code, description],
            f"{spec.output_code} error field",
            delimiter="^",
        )
        return f"{alt_account}^{account}^{branch}^E^{error_code}^{description}^"

    if style == "cmradcho":
        account, internal_ref = fields[:2]
        output_status = record.output_status
        _validate_delimited_values(
            [account, internal_ref, output_status],
            "CMRADCHO body field",
            delimiter="^",
        )
        line = f"02{account}^{internal_ref}^{output_status}^"
        if error_code:
            description = format_form_message(
                error_code.replace(";", ""),
                error_param,
                messages,
            )
            _validate_delimited_values(
                [error_code, description],
                "CMRADCHO error field",
                delimiter="^",
            )
            line += error_code + "^" + description
        return line

    if style in {"cmrcifou", "stdcifou"}:
        data = fields[:2]
        error_output = error_code.replace(";", "~")
        description = _format_tilde_message_list(
            error_code,
            error_param,
            messages,
        )
        _validate_delimited_values(
            [*data, error_output, description],
            f"{spec.output_code} body field",
        )
        return (
            "BH;"
            + ";".join(
                (*data, "Y" if record.status == "P" else "N", error_output, description)
            )
            + ";"
        )

    if style == "fixed_payment":
        description = format_error_description_list(
            error_code,
            error_param,
            messages,
        )
        values = (
            (fields[0], 20),
            (fields[1], 10),
            (fields[2], 20),
            (fields[3], 4),
            (fields[4], 8),
            (fields[5], 8),
            (fields[6], 4),
            (error_code, 100),
            (description, 250),
        )
        return (
            "02"
            + "".join(_fixed_field(value, width) for value, width in values)
            + ("Y" if record.status == "P" else "N")
        )

    if style == "cmrrelvo":
        data = fields[:3]
        _validate_delimited_values(
            list(data),
            "CMRRELVO body field",
            delimiter="^",
        )
        prefix = "BH^" + "^".join(data) + "^"
        if record.status == "P":
            return prefix + "Y"
        description = format_error_description_list(
            error_code,
            error_param,
            messages,
        )
        _validate_delimited_values(
            [error_code, description],
            "CMRRELVO error field",
            delimiter="^",
        )
        return prefix + "N^" + error_code + "^" + description

    if style in {"ofmdcgen", "ofmdsupd"}:
        data_count = 6 if style == "ofmdcgen" else 3
        data = fields[:data_count]
        output_status = record.output_status
        _validate_delimited_values(data, f"{spec.output_code} body field")
        description = ""
        if record.status == "E" and error_code:
            description = _format_debit_message_list(
                error_code,
                error_param,
                messages,
            )
        _validate_output_text(error_code, f"{spec.output_code} error code")
        _validate_output_text(description, f"{spec.output_code} error description")
        return (
            "02;"
            + ";".join((*data, output_status, error_code, description))
            + ";"
        )

    if style == "ofobtupd":
        data = fields[:3]
        output_status = record.output_status
        _validate_delimited_values(data, "OFOBTUPD body field")
        _validate_output_field(output_status, "OFOBTUPD status")
        prefix = "02;" + ";".join((*data, output_status)) + ";"
        if output_status != "E" or not error_code:
            return prefix

        # QA appends ERROR itself and then the first description. It does not
        # insert a delimiter between them; a separator is visible only when
        # the stored ERROR list already ends in one.
        description = _format_debit_message_list(
            error_code,
            error_param,
            messages,
        )
        _validate_output_text(error_code, "OFOBTUPD error-code list")
        _validate_output_text(description, "OFOBTUPD error description")
        return prefix + error_code + description

    if style == "ofddissu":
        data = fields[:55]
        # QA selects TRIM(STATUS) for this contract, unlike several older
        # packages that emit the database value verbatim.
        output_status = record.output_status.strip()
        error_output = format_error_code(error_code)
        # PR_GET_ERRMSG returns immediately when ERROR is NULL, so a stray
        # ERROR_PARAM must not leak into the client file on its own.
        description = (
            _format_tilde_message_list(error_code, error_param, messages)
            if error_code
            else ""
        )
        _validate_delimited_values(data, "OFDDISSU body field")
        _validate_delimited_values(
            [output_status, error_output, description],
            "OFDDISSU status/error field",
        )
        return (
            "LB;"
            + ";".join((*data, output_status, error_output, description))
            + ";"
        )

    if style == "glcrte_fixed":
        description = _format_prefixed_message_list(
            error_code,
            error_param,
            messages,
        )
        return (
            "LB"
            + _star_field(fields[0], 36)
            + _star_field(fields[1], 20)
            + _star_field(record.output_status, 7)
            + _star_field(error_code, 255)
            + _star_field(description, 255)
        )

    if style == "ouchbkcu":
        data = fields[:5]
        error_output = error_code.replace(";", "~")
        description = _format_tilde_message_list(
            error_code,
            error_param,
            messages,
        )
        _validate_delimited_values(
            [*data, error_output, description],
            "OUCHBKCU body field",
        )
        return (
            "BH;"
            + ";".join(
                (*data, "Y" if record.status == "P" else "N", error_output, description)
            )
            + ";"
        )

    if style == "locamtou":
        data = fields[:3]
        error_output = error_code.replace(";", "~")
        if error_output:
            first_param = str(error_param or "").split(";", 1)[0]
            description = (
                format_form_message(error_output, first_param, messages) + "~"
            )
        else:
            description = error_param
        _validate_delimited_values(
            [*data, error_output, description],
            "LOCAMTOU body field",
        )
        return (
            "BDY;"
            + ";".join(
                (*data, "Y" if record.status == "P" else "N", error_output, description)
            )
            + ";"
        )

    if style == "stdcrdou":
        data = fields[:9]
        error_output = error_code.replace(";", "~")
        description = _format_tilde_message_list(
            error_code,
            error_param,
            messages,
        )
        _validate_delimited_values(
            [*data, error_output, description],
            "STDCRDOU body field",
        )
        return (
            "BDY;"
            + ";".join(
                (*data, "Y" if record.status == "P" else "N", error_output, description)
            )
            + ";"
        )

    raise OutputFileGenerationError(
        f"Missing declarative body renderer for {spec.input_code}"
    )


def _render_body_records(
    spec: InterfaceSpec,
    records: list[_BodyRecord],
    messages: dict[str, str],
    error_types: dict[str, str] | None = None,
) -> list[str]:
    adapter = upload_body_adapter(spec.input_code)
    if adapter is not None and adapter.style == "stdcifom":
        # QA declares L_ERR_DESC outside the upload cursor. A successful
        # one-row ERTB_MSGS SELECT replaces it (including with NULL), while a
        # NO_DATA_FOUND/TOO_MANY_ROWS exception leaves the previous value intact.
        last_direct_message = ""
        lines: list[str] = []
        for record in records:
            maintenance_no, customer_no, message_count, direct_message = (
                record.fields[:4]
            )
            _validate_delimited_values(
                [maintenance_no, customer_no],
                "STDCIFOM body field",
                delimiter="^",
            )
            if record.status == "P":
                lines.append(f"{maintenance_no}^{customer_no}^P^")
                continue
            if record.status != "E":
                raise OutputFileGenerationError(
                    "STDCIFMO status is not supported by the QA output contract"
                )
            if message_count == "1":
                last_direct_message = direct_message
            error_code = record.error_code or "ST-SAVE-004"
            _validate_delimited_values(
                [error_code, last_direct_message],
                "STDCIFOM error field",
                delimiter="^",
            )
            lines.append(
                f"{maintenance_no}^{customer_no}^E^"
                f"{error_code}^{last_direct_message}^"
            )
        return lines
    if adapter is not None and adapter.style == "chiclou":
        # QA leaves L_ERR_DESC allocated across cursor iterations. A failed
        # SELECT INTO (zero or multiple ERTB_MSGS rows) therefore retains the
        # last unique message. Preserve this client-visible stateful quirk.
        last_direct_message = ""
        lines: list[str] = []
        for record in records:
            if record.status == "E" and record.fields[5] == "1":
                last_direct_message = record.fields[6]
            lines.append(
                _render_upload_body_record(
                    spec,
                    record,
                    messages,
                    error_types,
                    direct_message=last_direct_message,
                )
            )
        return lines
    if adapter is not None:
        return [
            _render_upload_body_record(spec, record, messages, error_types)
            for record in records
        ]
    if spec.input_code == "IFCHKPRT":
        lines: list[str] = []
        for record in records:
            # Metadata occupies fields 0..5. The next 19 values are the QA
            # body through CHEQ_STAT; raw PROC_STAT follows separately.
            data = record.fields[6:25]
            _validate_delimited_values(data, "OFCHKPRT body field")
            _validate_output_field(record.output_status, "OFCHKPRT process status")
            prefix = "BDY;" + ";".join((*data, record.output_status)) + ";"
            if record.output_status == "S":
                lines.append(prefix)
                continue
            _validate_output_text(record.error_code, "OFCHKPRT error-code list")
            description = _format_tilde_message_list(
                record.error_code,
                record.error_param,
                messages,
            )
            _validate_output_text(description, "OFCHKPRT error description")
            lines.append(prefix + record.error_code + ";" + description + ";")
        return lines
    if spec.input_code == "IFIWDCLG":
        lines: list[str] = []
        for record in records:
            data = record.fields[7:18]
            txn_status = record.fields[18]
            fccref = record.fields[19]
            _validate_delimited_values(data, "OFIWDCLG body field")
            _validate_delimited_values(
                [record.output_status, txn_status, fccref],
                "OFIWDCLG status field",
            )
            prefix = (
                "BDY;"
                + ";".join(
                    (*data, record.output_status, txn_status, fccref)
                )
                + ";"
            )
            emits_error = (
                record.output_status in {"E", "U"} or txn_status == "REJR"
            )
            if not emits_error:
                lines.append(prefix)
                continue
            _validate_output_text(record.error_code, "OFIWDCLG error-code list")
            description = _format_tilde_message_list(
                record.error_code,
                record.error_param,
                messages,
            )
            _validate_output_text(description, "OFIWDCLG error description")
            lines.append(prefix + record.error_code + ";" + description + ";")
        return lines
    if spec.input_code == "IFIWADOC":
        lines: list[str] = []
        for record in records:
            data = record.fields[3:14]
            _validate_delimited_values(data, "OFIWADOC body field")
            _validate_output_field(record.output_status, "OFIWADOC upload status")
            prefix = (
                "BDY;"
                + ";".join((*data, record.output_status, "NOPR", ""))
                + ";"
            )
            if record.output_status not in {"E", "U"}:
                lines.append(prefix)
                continue
            _validate_output_text(record.error_code, "OFIWADOC error-code list")
            description = _format_tilde_message_list(
                record.error_code,
                record.error_param,
                messages,
            )
            _validate_output_text(description, "OFIWADOC error description")
            lines.append(prefix + record.error_code + ";" + description + ";")
        return lines
    if spec.input_code == "IFQSIMTP":
        lines: list[str] = []
        for record in records:
            (
                _record_reference,
                _tie_count,
                alt_account,
                branch,
                account,
                _stored_error,
                _stored_param,
                value_date,
                maturity_date,
                product,
                user_status,
                sim_date,
                compamt,
            ) = record.fields
            _validate_delimited_values(
                [alt_account, branch, account, record.output_status],
                "OFQSIMTP fixed body field",
            )
            _validate_output_text(record.error_code, "OFQSIMTP error code")
            _validate_output_text(record.error_param, "OFQSIMTP error parameters")
            line = (
                f"{alt_account};{branch};{account};{record.output_status};"
            )
            if record.error_code:
                emitted_error = record.error_code.rstrip(";")
                description = format_form_message(
                    record.error_code.replace(";", ""),
                    record.error_param,
                    messages,
                )
                _validate_output_text(description, "OFQSIMTP error description")
                # QA deliberately omits a separator after the message; the
                # following VALUE_DATE becomes part of the same output field.
                line += emitted_error + ";" + description
            else:
                line += ";;"
            line += (
                f"{value_date};{maturity_date};{product};"
                f"{user_status};{sim_date};"
            )
            if compamt:
                line += compamt
            else:
                # NVL fallback in QA emits 35 semicolons, one more empty field
                # than the normal 17 name/amount pairs.
                line += ";" * 35
            lines.append(line)
        return lines
    if spec.input_code == "STDINRTS":
        lines: list[str] = []
        for record in records:
            branch, external_reference, account, product = record.fields[2:6]
            _validate_delimited_values(
                [branch, external_reference, account, product],
                "STDINROU body field",
            )
            _validate_output_text(record.error_code, "STDINROU error-code list")
            _validate_output_text(record.error_param, "STDINROU error parameters")
            description = _format_tilde_message_list(
                record.error_code,
                record.error_param,
                messages,
            )
            _validate_output_text(description, "STDINROU error description")
            processed = "Y" if record.output_status == "P" else "N"
            lines.append(
                "BDY;"
                + ";".join(
                    (
                        branch,
                        external_reference,
                        account,
                        product,
                        processed,
                        record.error_code,
                        description,
                        "",
                    )
                )
            )
        return lines
    if spec.input_code == "IFEARLCG":
        lines: list[str] = []
        for record in records:
            data = record.fields[3:]
            _validate_delimited_values(data, "IFOARLCG body field")
            _validate_output_text(error_code := record.error_code, "IFOARLCG error-code list")
            line = "FB;" + ";".join((*data, record.status))
            if error_code:
                # QA passes a freshly NULL L_ERR_DESC as ERROR_PARAMS, ignoring
                # IFTB's stored parameters entirely, then appends `~` per code.
                description = _format_tilde_message_list(error_code, "", messages)
                _validate_output_text(description, "IFOARLCG error description")
                line += ";" + error_code + ";" + description
            lines.append(line + ";")
        return lines
    if spec.input_code == "IFSTDCST":
        lines: list[str] = []
        for record in records:
            data = record.fields[8:14]
            error_code = record.fields[14]
            message_count = record.fields[15]
            message_type = record.fields[16]
            message = record.fields[17]
            _validate_delimited_values(data, "DCSTOUT body field")
            _validate_output_field(error_code, "DCSTOUT error code")
            values = [*data, error_code]
            if message_count == "1" and message_type == "E":
                _validate_output_field(message, "DCSTOUT error description")
                values.append(message.strip())
            values.append("Y" if record.status == "P" else "N")
            lines.append(";".join(values) + ";")
        return lines
    if spec.input_code == "CLADCHG":
        lines: list[str] = []
        for record in records:
            if not record.error_code:
                lines.append(record.body_prefix)
                continue
            lookup_code = record.error_code.replace(";", "")
            description = format_form_message(
                lookup_code,
                record.error_param,
                messages,
            )
            _validate_output_field(
                description,
                "CLADCHGO error description",
                delimiter="^",
            )
            lines.append(
                record.body_prefix
                + record.error_code
                + "^"
                + description
            )
        return lines
    if spec.input_code == "IACMCLOS":
        lines: list[str] = []
        for record in records:
            descriptions = format_oacmclos_error_descriptions(
                record.error_code,
                record.error_param,
                messages,
            )
            _validate_output_text(descriptions, "OACMCLOS error description")
            lines.append(
                record.body_prefix
                + record.error_code
                + record.error_param
                + descriptions
                + ";"
            )
        return lines
    if spec.input_code == "IFICOWCG":
        lines: list[str] = []
        for record in records:
            include_error = record.status in {"E", "U"} or (
                _uses_transactional_oficowcg_contract(spec)
                and record.output_status == "REJR"
            )
            line = record.body_prefix
            if include_error:
                description = format_form_message(
                    record.error_code,
                    record.error_param,
                    messages,
                )
                _validate_output_field(record.error_code, "OFICOWCG error code")
                _validate_output_field(description, "OFICOWCG error description")
                line += record.error_code + ";" + description + ";"
            if _uses_transactional_oficowcg_contract(spec):
                line += record.output_status + ";"
            lines.append(line)
        return lines
    if spec.input_code != "IFDOBIEL":
        return [record.body_prefix for record in records]
    return [
        record.body_prefix
        + format_error_code(record.error_code)
        + ";"
        + format_error_description(record.error_code, record.error_param, messages)
        + ";"
        for record in records
    ]


def _parse_error_messages(rows: list[str]) -> dict[str, str]:
    messages: dict[str, str] = {}
    for row in rows:
        parts = row.split("|", 2)
        if len(parts) != 3:
            raise OutputFileGenerationError("Oracle returned malformed error-message metadata")
        code_hex, count_raw, message_hex = parts
        try:
            count = int(count_raw)
        except ValueError as exc:
            raise OutputFileGenerationError(
                "Oracle returned malformed error-message metadata"
            ) from exc
        if count == 1:
            messages[_decode_utf8_hex(code_hex)] = _decode_utf8_hex(message_hex)
    return messages


def _parse_error_message_details(
    rows: list[str],
) -> tuple[dict[str, str], dict[str, str]]:
    messages: dict[str, str] = {}
    error_types: dict[str, str] = {}
    seen: set[str] = set()
    for row in rows:
        parts = row.split("|", 3)
        if len(parts) != 4:
            raise OutputFileGenerationError(
                "Oracle returned malformed CHISALOU error metadata"
            )
        code_hex, count_raw, type_hex, message_hex = parts
        try:
            count = int(count_raw)
        except ValueError as exc:
            raise OutputFileGenerationError(
                "Oracle returned malformed CHISALOU error metadata"
            ) from exc
        code = _decode_utf8_hex(code_hex)
        if not code or code in seen or count < 0:
            raise OutputFileGenerationError(
                "Oracle returned malformed CHISALOU error metadata"
            )
        seen.add(code)
        if count > 1:
            raise OutputFileGenerationError(
                f"ERTB_MSGS contains ambiguous metadata for CHISALOU code {code}"
            )
        if count == 1:
            error_type = _decode_utf8_hex(type_hex).strip()
            message = _decode_utf8_hex(message_hex)
            _validate_output_text(error_type, "CHISALOU error type")
            _validate_output_text(message, "CHISALOU error message")
            error_types[code] = error_type
            messages[code] = message
    return messages, error_types


def _decode_utf8_hex(value: str) -> str:
    encoded = (value or "").strip()
    if not encoded:
        return ""
    if len(encoded) % 2 or not re.fullmatch(r"[0-9A-Fa-f]+", encoded):
        raise OutputFileGenerationError("Oracle returned malformed UTF-8 field data")
    try:
        return bytes.fromhex(encoded).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise OutputFileGenerationError("Oracle returned malformed UTF-8 field data") from exc


def _encode_utf8_hex(value: str) -> str:
    return str(value or "").encode("utf-8").hex().upper()


def _validate_output_text(value: str, label: str) -> None:
    if "\r" in value or "\n" in value:
        raise OutputFileGenerationError(
            f"Oracle returned a {label} containing a line break"
        )


def _validate_output_field(value: str, label: str, *, delimiter: str = ";") -> None:
    if delimiter in value or "\r" in value or "\n" in value:
        raise OutputFileGenerationError(
            f"Oracle returned a {label} containing the output delimiter or a line break"
        )


def _write_atomic(path: Path, payload: bytes, *, overwrite: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not overwrite:
        raise OutputFileGenerationError(
            f"{path.name} already exists and this request did not allow replacement"
        )
    temp_path = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    try:
        with temp_path.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if overwrite:
            os.replace(temp_path, path)
        else:
            os.rename(temp_path, path)
    except FileExistsError as exc:
        raise OutputFileGenerationError(
            f"{path.name} already exists and this request did not allow replacement"
        ) from exc
    except OSError as exc:
        raise OutputFileGenerationError(f"Could not save {path.name}: {exc}") from exc
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass


def _is_cancelled(cancel_event: threading.Event | None) -> bool:
    return cancel_event is not None and cancel_event.is_set()


def _raise_if_cancelled(cancel_event: threading.Event | None) -> None:
    if _is_cancelled(cancel_event):
        raise GenerationCancelled("Output generation was cancelled")


def _safe_sqlcl_error(result: RunResult) -> str:
    # A timed-out/nonzero SQLcl process can leave already-fetched client rows in
    # stdout. Never use arbitrary stdout as a diagnostic fallback because the
    # resulting error is displayed and logged by the view. Even text resembling
    # ORA-/SQL errors can occur inside a client field, so nonzero stdout is never
    # diagnostic input. SQLcl stderr is the only detailed source here.
    if (result.stderr or "").strip():
        return _safe_text_error(result.stderr)
    return f"SQLcl failed with exit code {result.exit_code}"


def _safe_text_error(value: str) -> str:
    redacted = _CONNECTION_RE.sub("[connection redacted]", value or "")
    lines = [line.strip() for line in redacted.splitlines() if line.strip()]
    detailed_errors: list[str] = []
    for line in lines:
        match = re.search(
            r"\b((?:ORA|SP2|PLS)-\d{4,5}:\s+.+)$",
            line,
            re.IGNORECASE,
        )
        if match and "docs.oracle.com" not in line.lower():
            detailed_errors.append(match.group(1))
    exact_errors = [
        line
        for line in lines
        if re.match(r"^(?:ORA|SP2|PLS)-\d{4,5}\b", line, re.IGNORECASE)
    ]
    preferred = [line for line in lines if _SQL_ERROR_RE.search(line)]
    selected = (
        detailed_errors[-1]
        if detailed_errors
        else (
            exact_errors[-1]
            if exact_errors
            else (preferred[-1] if preferred else (lines[-1] if lines else "Oracle query failed"))
        )
    )
    return selected[:500]


def _as_generation_error(function, value: str):
    try:
        return function(value)
    except ValueError as exc:
        raise OutputFileGenerationError(str(exc)) from exc
