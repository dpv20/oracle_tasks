from __future__ import annotations

import hashlib
import re
import sys
import tempfile
import unittest
from collections import Counter
from dataclasses import dataclass
from pathlib import Path


SRC_DIR = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_DIR))

from features.output_file_generation.formats import (  # noqa: E402
    format_form_message,
    physical_filename,
    serialize_lines,
    spec_for_code,
    validate_output_lines,
)
from features.output_file_generation import (  # noqa: E402
    GenerationRequest,
    OracleTarget,
    OutputFileGenerationService,
)
from features.output_file_generation.models import DataSourceChoice  # noqa: E402
from features.output_file_generation.queries import (  # noqa: E402
    build_body_query,
    build_qsimtp_snapshot_guard_query,
)
from features.output_file_generation.service import (  # noqa: E402
    OutputFileGenerationError,
    _collect_error_codes,
    _parse_body_records,
    _parse_qsimtp_snapshot_metadata,
    _render_body_records,
)
from tests.test_output_file_generation import (  # noqa: E402
    _FakeConfig,
    _QueuedSqlclRunner,
)


# SELECT-only PROD evidence captured on 2026-08-07. The QA input helper
# truncates CLTB_IFQSIMTP_LOG before every run, so this table can authenticate
# only the latest completed process. It has no process/timestamp column and
# cannot reconstruct arbitrary older archives.
PROD_ARCHIVE_EVIDENCE = {
    "2806513": {
        "file_date": "20260805",
        "upload_rows": 5469,
        "distinct_record_references": 5469,
        "distinct_alt_accounts": 5469,
        "upload_statuses": {"P": 5449, "E": 20},
        "hybrid_rows": 54510,
        "distinct_live_log_rows": 54490,
        "unmatched_uploads": 20,
        "uploads_with_ten_log_rows": 5449,
        "max_log_rows_per_upload": 10,
        "ambiguous_order_ties": 0,
        "field_shapes": {45: 54510},
    },
    "2768556": {
        "file_date": "20260605",
        "upload_rows": 5564,
        "distinct_record_references": 5564,
        "distinct_alt_accounts": 5564,
        "upload_statuses": {"P": 5544, "E": 20},
        "hybrid_rows": 54560,
        "distinct_live_log_rows": 54440,
        "unmatched_uploads": 120,
        "uploads_with_ten_log_rows": 5444,
        "max_log_rows_per_upload": 10,
        "ambiguous_order_ties": 0,
        "field_shapes": {44: 60, 45: 54394, 46: 106},
    },
    "2741974": {
        "file_date": "20260305",
        "upload_rows": 5769,
        "distinct_record_references": 5769,
        "distinct_alt_accounts": 5769,
        "upload_statuses": {"P": 5744, "E": 25},
        "hybrid_rows": 54702,
        "distinct_live_log_rows": 54370,
        "unmatched_uploads": 332,
        "uploads_with_ten_log_rows": 5437,
        "max_log_rows_per_upload": 10,
        "ambiguous_order_ties": 0,
        "field_shapes": {44: 130, 45: 54252, 46: 320},
    },
}
LIVE_LOG_DATE_EVIDENCE = {
    process_ref: (10, "20260805", "20260818")
    for process_ref in PROD_ARCHIVE_EVIDENCE
}
LATEST_SNAPSHOT_EVIDENCE = {
    "active_upload_rows": 0,
    "active_file_log_rows": 0,
    "archive_processes": 63,
    "archive_file_log_processes": 64,
    "latest_archive_upload_process": "2806513",
    "latest_archive_file_log_process": "2806513",
    "live_log_rows": 54490,
    "live_log_accounts": 5449,
    "live_log_sim_dates": 10,
    "unique_account_branch_date_keys": 54490,
    "in_progress_control_rows": 0,
    "successful_uploads_with_ten_rows": 5449,
    "successful_uploads_without_log": 0,
    "failed_uploads_with_log": 0,
    "ambiguous_cursor_order_ties": 0,
}
ERROR_LOOKUP_EVIDENCE = {
    "2806513_codes": {"CL-IFQSTP01": 14, "CL-PMTV34;": 6},
    "spanish_message_rows": {"CL-IFQSTP01": 1, "CL-PMTV34": 1},
    "message_semicolons": 0,
    "parameter_tildes": 0,
}
PROD_2806513_LIVE_REPLAY = {
    "rows": 54510,
    "bytes": 11873864,
    "sha256": "6f0ca77b078dcbc5b397f11695278250931b1f1c489e2bcbcc39d186726c6fae",
    "reread_sha256": "6f0ca77b078dcbc5b397f11695278250931b1f1c489e2bcbcc39d186726c6fae",
    "field_shapes": {45: 54510},
    "qa_chunk_flushes": 3268,
    "qa_chunk_max_buffer_chars": 3869,
}


LIVE_COMPAMT = "".join(f"C{index:02d};{index};" for index in range(1, 18))
MESSAGES = {"E-ONE": "Mensaje $1!"}


@dataclass(frozen=True)
class _BodyFixture:
    alt_account: str
    branch: str
    account: str
    status: str
    error: str = ""
    error_param: str = ""
    value_date: str = "20260805"
    maturity_date: str = "20261231"
    product: str = "PRD"
    user_status: str = "ACT"
    sim_date: str = "20260818"
    compamt: str | None = LIVE_COMPAMT


def _render_qa_line(row: _BodyFixture) -> str:
    """Reproduce QA's missing error/date separator and COMPAMT fallback."""
    line = f"{row.alt_account};{row.branch};{row.account};{row.status};"
    if row.error:
        line += row.error.rstrip(";") + ";"
        line += format_form_message(
            row.error.replace(";", ""),
            row.error_param,
            MESSAGES,
        )
        # Deliberately no semicolon here: QA concatenates VALUE_DATE directly.
    else:
        line += ";;"
    line += (
        f"{row.value_date};{row.maturity_date};{row.product};"
        f"{row.user_status};{row.sim_date};"
    )
    # QA asks for 17 name/amount pairs (34 fields) but emits 35 semicolons
    # when COMPAMT is NULL, yielding one additional empty field.
    line += row.compamt if row.compamt else ";" * 35
    return line


def _qa_chunk_write(lines: tuple[str, ...], limit: int = 500) -> tuple[bytes, int, int]:
    """Model LIMIT 500 and QA's triangular L_TOTAL_LENGHT bookkeeping bug."""
    writes: list[str] = []
    flushes = 0
    max_buffer = 0
    for offset in range(0, len(lines), limit):
        data = "R"
        total_length = 0
        for line in lines[offset : offset + limit]:
            data += line + "\n"
            max_buffer = max(max_buffer, len(data))
            # QA adds the whole accumulated buffer length on every row, not
            # merely the new record length.
            total_length += len(data)
            if total_length > 32000:
                writes.append(data[1 : 1 + total_length])
                flushes += 1
                total_length = 0
                data = "R"
        if total_length > 0:
            writes.append(data[1 : 1 + total_length])
            flushes += 1
    return "".join(writes).encode("utf-8"), flushes, max_buffer


def _safe_cursor_order(
    rows: tuple[tuple[int, str, str], ...],
) -> tuple[tuple[int, str, str], ...]:
    """Sort by QA's two keys, rejecting ties whose emitted rows differ."""
    variants: dict[tuple[int, str], set[str]] = {}
    for record_reference, sim_date, payload in rows:
        variants.setdefault((record_reference, sim_date), set()).add(payload)
    ambiguous = sorted(key for key, values in variants.items() if len(values) > 1)
    if ambiguous:
        raise ValueError(f"OFQSIMTP has ambiguous cursor-order ties: {ambiguous!r}")
    return tuple(sorted(rows, key=lambda row: (row[0], row[1])))


def _latest_archive_snapshot_is_safe(
    requested_process: str,
    *,
    active_upload_rows: int,
    active_file_log_rows: int,
    latest_archive_upload_process: str,
    latest_archive_file_log_process: str,
    in_progress_control_rows: int,
    live_log_rows: int,
    unique_live_log_keys: int,
    successful_uploads: int,
    live_log_accounts: int,
    failed_uploads: int,
    unmatched_uploads: int,
    successful_uploads_with_ten_rows: int,
    successful_uploads_without_log: int,
    failed_uploads_with_log: int,
    distinct_sim_dates: int,
    ambiguous_cursor_order_ties: int,
    first_body_sha256: str,
    reread_body_sha256: str,
) -> bool:
    """Reference guard justified by the QA helper's truncate-and-repopulate flow."""
    return (
        active_upload_rows == 0
        and active_file_log_rows == 0
        and in_progress_control_rows == 0
        and requested_process == latest_archive_upload_process
        and requested_process == latest_archive_file_log_process
        and live_log_rows > 0
        and live_log_rows == unique_live_log_keys
        and successful_uploads == live_log_accounts
        and failed_uploads == unmatched_uploads
        and successful_uploads == successful_uploads_with_ten_rows
        and successful_uploads_without_log == 0
        and failed_uploads_with_log == 0
        and live_log_rows == successful_uploads * 10
        and distinct_sim_dates == 10
        and ambiguous_cursor_order_ties == 0
        and bool(first_body_sha256)
        and first_body_sha256 == reread_body_sha256
    )


SYNTHETIC_LINES = (
    _render_qa_line(_BodyFixture("A1", "001", "ACC-A1", "P")),
    _render_qa_line(
        _BodyFixture("A2", "001", "ACC-A2", "E", "E-ONE;", "P")
    ),
    _render_qa_line(_BodyFixture("A3", "001", "ACC-A3", "P", compamt=None)),
    _render_qa_line(
        _BodyFixture(
            "A4",
            "001",
            "ACC-A4",
            "E",
            "E-ONE;",
            "Q",
            compamt=None,
        )
    ),
)
SYNTHETIC_PAYLOAD = ("\n".join(SYNTHETIC_LINES) + "\n").encode("utf-8")
SYNTHETIC_SHA256 = "ccdbe89cdc321ed88f38b5aed3290a3ef7df15e62cd88bc129b9685bf89e50eb"


def _central_mapping_is_ready() -> bool:
    try:
        return spec_for_code("IFQSIMTP").output_code == "OFQSIMTP"
    except ValueError:
        return False


def _hex(value: str) -> str:
    return value.encode("utf-8").hex().upper()


def _central_body_row(
    fixture: _BodyFixture,
    *,
    record_reference: int,
    tie_count: int = 1,
) -> str:
    values = (
        fixture.status,
        fixture.status,
        str(record_reference),
        str(tie_count),
        fixture.alt_account,
        fixture.branch,
        fixture.account,
        fixture.error,
        fixture.error_param,
        fixture.value_date,
        fixture.maturity_date,
        fixture.product,
        fixture.user_status,
        fixture.sim_date,
        fixture.compamt or "",
    )
    return "|".join(_hex(value) for value in values)


def _snapshot_row(
    *,
    process_ref: str = "2806513",
    upload_rows: int = 2,
    processed: int = 1,
    failed: int = 1,
    active_upload_rows: int = 0,
    active_file_log_rows: int = 0,
    active_requested_file_log_rows: int = 0,
    latest_archive_upload_process: str | None = None,
    latest_archive_file_log_process: str | None = None,
    control_rows: int = 0,
) -> str:
    live_rows = processed * 10
    values = (
        upload_rows,
        upload_rows,
        upload_rows,
        processed,
        failed,
        0,
        processed,
        0,
        0,
        live_rows,
        live_rows,
        live_rows,
        processed,
        10 if processed else 0,
        0,
        0,
        active_upload_rows,
        active_file_log_rows,
        active_requested_file_log_rows,
        latest_archive_upload_process or process_ref,
        latest_archive_file_log_process or process_ref,
        control_rows,
    )
    return "|".join(str(value) for value in values)


class OfqsimtpPreparedContractTests(unittest.TestCase):
    def test_live_log_cannot_reconstruct_arbitrary_older_archives(self) -> None:
        self.assertEqual(PROD_ARCHIVE_EVIDENCE["2806513"]["file_date"], "20260805")
        self.assertEqual(PROD_ARCHIVE_EVIDENCE["2768556"]["file_date"], "20260605")
        self.assertEqual(PROD_ARCHIVE_EVIDENCE["2741974"]["file_date"], "20260305")
        # All three archived processes now see the same August-only live rows.
        self.assertEqual(len(set(LIVE_LOG_DATE_EVIDENCE.values())), 1)
        self.assertEqual(next(iter(LIVE_LOG_DATE_EVIDENCE.values())), (10, "20260805", "20260818"))
        self.assertEqual(
            [
                PROD_ARCHIVE_EVIDENCE[ref]["unmatched_uploads"]
                for ref in ("2806513", "2768556", "2741974")
            ],
            [20, 120, 332],
        )

    def test_latest_archived_process_has_a_complete_authenticated_live_snapshot(self) -> None:
        state = LATEST_SNAPSHOT_EVIDENCE
        latest = PROD_ARCHIVE_EVIDENCE["2806513"]
        latest_guard = {
            "active_upload_rows": state["active_upload_rows"],
            "active_file_log_rows": state["active_file_log_rows"],
            "latest_archive_upload_process": state["latest_archive_upload_process"],
            "latest_archive_file_log_process": state["latest_archive_file_log_process"],
            "in_progress_control_rows": state["in_progress_control_rows"],
            "live_log_rows": state["live_log_rows"],
            "unique_live_log_keys": state["unique_account_branch_date_keys"],
            "successful_uploads": latest["upload_statuses"]["P"],
            "live_log_accounts": state["live_log_accounts"],
            "failed_uploads": latest["upload_statuses"]["E"],
            "unmatched_uploads": latest["unmatched_uploads"],
            "successful_uploads_with_ten_rows": state["successful_uploads_with_ten_rows"],
            "successful_uploads_without_log": state["successful_uploads_without_log"],
            "failed_uploads_with_log": state["failed_uploads_with_log"],
            "distinct_sim_dates": state["live_log_sim_dates"],
            "ambiguous_cursor_order_ties": state["ambiguous_cursor_order_ties"],
            "first_body_sha256": PROD_2806513_LIVE_REPLAY["sha256"],
            "reread_body_sha256": PROD_2806513_LIVE_REPLAY["reread_sha256"],
        }
        self.assertTrue(_latest_archive_snapshot_is_safe("2806513", **latest_guard))
        changed_guard = dict(latest_guard)
        changed_guard["reread_body_sha256"] = "0" * 64
        self.assertFalse(_latest_archive_snapshot_is_safe("2806513", **changed_guard))
        for unsafe_process in ("2768556", "2741974"):
            self.assertFalse(
                _latest_archive_snapshot_is_safe(
                    unsafe_process,
                    active_upload_rows=0,
                    active_file_log_rows=0,
                    latest_archive_upload_process="2806513",
                    latest_archive_file_log_process="2806513",
                    in_progress_control_rows=0,
                    live_log_rows=state["live_log_rows"],
                    unique_live_log_keys=state["unique_account_branch_date_keys"],
                    successful_uploads=PROD_ARCHIVE_EVIDENCE[unsafe_process]["upload_statuses"]["P"],
                    live_log_accounts=state["live_log_accounts"],
                    failed_uploads=PROD_ARCHIVE_EVIDENCE[unsafe_process]["upload_statuses"]["E"],
                    unmatched_uploads=PROD_ARCHIVE_EVIDENCE[unsafe_process]["unmatched_uploads"],
                    successful_uploads_with_ten_rows=PROD_ARCHIVE_EVIDENCE[unsafe_process]["uploads_with_ten_log_rows"],
                    successful_uploads_without_log=0,
                    failed_uploads_with_log=0,
                    distinct_sim_dates=10,
                    ambiguous_cursor_order_ties=0,
                    first_body_sha256=PROD_2806513_LIVE_REPLAY["sha256"],
                    reread_body_sha256=PROD_2806513_LIVE_REPLAY["sha256"],
                )
            )

    def test_prod_cardinality_and_order_evidence_is_internally_consistent(self) -> None:
        for evidence in PROD_ARCHIVE_EVIDENCE.values():
            self.assertEqual(sum(evidence["upload_statuses"].values()), evidence["upload_rows"])
            self.assertEqual(evidence["distinct_record_references"], evidence["upload_rows"])
            self.assertEqual(evidence["distinct_alt_accounts"], evidence["upload_rows"])
            self.assertEqual(sum(evidence["field_shapes"].values()), evidence["hybrid_rows"])
            self.assertEqual(evidence["max_log_rows_per_upload"], 10)
            self.assertEqual(evidence["ambiguous_order_ties"], 0)
            self.assertEqual(
                evidence["distinct_live_log_rows"],
                evidence["uploads_with_ten_log_rows"] * 10,
            )
            self.assertEqual(
                evidence["hybrid_rows"],
                evidence["distinct_live_log_rows"] + evidence["unmatched_uploads"],
            )

    def test_compamt_and_error_separator_bugs_produce_44_45_46_fields(self) -> None:
        self.assertEqual(LIVE_COMPAMT.count(";"), 34)
        self.assertTrue(LIVE_COMPAMT.endswith(";"))
        self.assertEqual(
            [len(line[:-1].split(";")) for line in SYNTHETIC_LINES],
            [45, 44, 46, 45],
        )
        # The error message and VALUE_DATE are one field in QA.
        self.assertIn(";E-ONE;Mensaje P20260805;20261231;", SYNTHETIC_LINES[1])
        # NULL COMPAMT emits 35 delimiters instead of the live value's 34.
        self.assertTrue(SYNTHETIC_LINES[2].endswith(";" * 36))

    def test_error_lookup_uses_one_code_after_removing_all_semicolons(self) -> None:
        evidence = ERROR_LOOKUP_EVIDENCE
        self.assertEqual(sum(evidence["2806513_codes"].values()), 20)
        self.assertEqual(evidence["spanish_message_rows"], {"CL-IFQSTP01": 1, "CL-PMTV34": 1})
        self.assertEqual(evidence["message_semicolons"], 0)
        self.assertEqual(evidence["parameter_tildes"], 0)
        self.assertEqual("CL-PMTV34;".replace(";", ""), "CL-PMTV34")

    def test_chunk_bug_flushes_too_often_but_preserves_the_payload(self) -> None:
        lines = tuple(SYNTHETIC_LINES[index % len(SYNTHETIC_LINES)] for index in range(1200))
        direct = ("\n".join(lines) + "\n").encode("utf-8")
        chunked, flushes, max_buffer = _qa_chunk_write(lines)
        self.assertEqual(chunked, direct)
        self.assertGreater(flushes, 3)  # Three LIMIT-500 fetches would suffice.
        self.assertLess(max_buffer, 32766)

    def test_cursor_order_requires_two_keys_and_rejects_distinct_ties(self) -> None:
        rows = ((2, "20260806", "B"), (1, "20260807", "A2"), (1, "20260805", "A1"))
        self.assertEqual(
            _safe_cursor_order(rows),
            ((1, "20260805", "A1"), (1, "20260807", "A2"), (2, "20260806", "B")),
        )
        with self.assertRaisesRegex(ValueError, "ambiguous cursor-order ties"):
            _safe_cursor_order(((1, "20260805", "A"), (1, "20260805", "B")))

    def test_synthetic_and_live_replay_hashes_are_frozen_with_scope_labels(self) -> None:
        self.assertEqual(len(SYNTHETIC_PAYLOAD), 532)
        self.assertEqual(hashlib.sha256(SYNTHETIC_PAYLOAD).hexdigest(), SYNTHETIC_SHA256)
        self.assertNotIn(b"\r", SYNTHETIC_PAYLOAD)
        replay = PROD_2806513_LIVE_REPLAY
        self.assertEqual(replay["rows"], 54510)
        self.assertEqual(replay["field_shapes"], {45: 54510})
        self.assertRegex(replay["sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(replay["reread_sha256"], replay["sha256"])
        # The malformed accumulator never approached VARCHAR2's 32766 limit.
        self.assertEqual(replay["qa_chunk_max_buffer_chars"], 3869)


@unittest.skipUnless(
    _central_mapping_is_ready(),
    "IFQSIMTP->OFQSIMTP central adapter is intentionally not integrated yet",
)
class OfqsimtpCentralAdapterContractTests(unittest.TestCase):
    """Automatically activates once the central mapping is registered."""

    def test_registry_is_body_only_timestamp_named_and_externally_revalidated(self) -> None:
        spec = spec_for_code("IFQSIMTP")
        self.assertEqual(spec.output_code, "OFQSIMTP")
        self.assertEqual(spec.contract, "ofqsimtp")
        self.assertFalse(spec.has_header)
        self.assertFalse(spec.has_footer)
        self.assertTrue(spec.revalidate_external_inputs)
        self.assertEqual(spec.database_time_format, "HH24MISS")
        self.assertEqual(
            physical_filename(spec, "20260805", "142530"),
            "OFQSIMTP_20260805_142530.TXT",
        )

    def test_active_query_preserves_outer_join_and_exact_two_key_order(self) -> None:
        script = build_body_query(
            "2806513",
            "IFQSIMTP",
            DataSourceChoice.ACTIVE,
            "20260805",
        ).script
        compact = " ".join(script.lower().split())
        self.assertIn("gitu_upload_master", compact)
        self.assertIn("cltb_ifqsimtp_log", compact)
        self.assertTrue("left join" in compact or "alt_acc_no(+)" in compact)
        self.assertIn("target_table = 'cltb_account_master'", compact)
        self.assertIn("order by", compact)
        self.assertRegex(compact, r"order by [^;]*record_reference[^;]*sim_date")
        self.assertNotIn("fn_handoff", compact)
        self.assertNotIn("fn_write_file", compact)
        self.assertNotRegex(
            compact,
            r"\b(?:insert|update|delete|merge|commit|rollback)\b",
        )

    def test_archive_query_uses_gita_but_requires_the_external_latest_snapshot_guard(self) -> None:
        script = build_body_query(
            "2806513",
            "IFQSIMTP",
            DataSourceChoice.ARCHIVE,
            "20260805",
        ).script
        compact = " ".join(script.lower().split())
        self.assertIn("gita_upload_master", compact)
        self.assertIn("cltb_ifqsimtp_log", compact)
        self.assertIn("order by", compact)
        self.assertTrue(spec_for_code("IFQSIMTP").revalidate_external_inputs)

        guard = build_qsimtp_snapshot_guard_query(
            "2806513",
            DataSourceChoice.ARCHIVE,
        ).script
        guard_compact = " ".join(guard.lower().split())
        for table in (
            "gitu_upload_master",
            "gita_upload_master",
            "gitb_file_log",
            "gita_file_log",
            "cltb_ifqsimtp_log",
            "cltb_ascii_upload_control",
        ):
            self.assertIn(table, guard_compact)
        self.assertIn("status in ('t', 'w')", guard_compact)
        self.assertIn("max(to_number(trim(a.process_ref_no)))", guard_compact)
        self.assertIn("max(to_number(trim(f.process_ref_no)))", guard_compact)
        self.assertIn("regexp_like(trim(a.process_ref_no), '^[0-9]+$')", guard_compact)
        self.assertNotRegex(
            guard_compact,
            r"\b(?:insert|update|delete|merge|commit|rollback|truncate)\b",
        )

    def test_body_only_serializer_accepts_all_three_qa_widths(self) -> None:
        spec = spec_for_code("IFQSIMTP")
        validate_output_lines(spec, SYNTHETIC_LINES)
        self.assertEqual(serialize_lines(SYNTHETIC_LINES), SYNTHETIC_PAYLOAD)
        for marker in ("HDR;", "FTR;", "TLR;"):
            self.assertTrue(all(not line.startswith(marker) for line in SYNTHETIC_LINES))

    def test_parser_renderer_preserve_qa_delimiter_and_compamt_bugs(self) -> None:
        spec = spec_for_code("IFQSIMTP")
        fixtures = (
            _BodyFixture("A1", "001", "ACC-A1", "P"),
            _BodyFixture("A2", "001", "ACC-A2", "E", "E-ONE;", "P"),
            _BodyFixture("A3", "001", "ACC-A3", "P", compamt=None),
            _BodyFixture(
                "A4",
                "001",
                "ACC-A4",
                "E",
                "E-ONE;",
                "Q",
                compamt=None,
            ),
        )
        rows = [
            _central_body_row(fixture, record_reference=index)
            for index, fixture in enumerate(fixtures, start=1)
        ]
        records, counts = _parse_body_records(rows, spec)
        self.assertEqual(counts, {"P": 2, "E": 2})
        self.assertEqual(_collect_error_codes(spec, records), {"E-ONE"})
        self.assertEqual(
            _render_body_records(spec, records, MESSAGES),
            list(SYNTHETIC_LINES),
        )
        with self.assertRaisesRegex(OutputFileGenerationError, "ordering ties"):
            _parse_body_records(
                [_central_body_row(fixtures[0], record_reference=1, tie_count=2)],
                spec,
            )

    def test_snapshot_guard_rejects_old_archive_control_work_and_incomplete_active(self) -> None:
        archive = _parse_qsimtp_snapshot_metadata(
            [_snapshot_row()],
            process_ref="2806513",
            source=DataSourceChoice.ARCHIVE,
            candidate_upload_rows=2,
        )
        self.assertEqual(archive.expected_body_rows, 11)

        with self.assertRaisesRegex(OutputFileGenerationError, "latest authenticated"):
            _parse_qsimtp_snapshot_metadata(
                [_snapshot_row(process_ref="2806513")],
                process_ref="2768556",
                source=DataSourceChoice.ARCHIVE,
                candidate_upload_rows=2,
            )
        with self.assertRaisesRegex(OutputFileGenerationError, "still truncating"):
            _parse_qsimtp_snapshot_metadata(
                [_snapshot_row(control_rows=1)],
                process_ref="2806513",
                source=DataSourceChoice.ARCHIVE,
                candidate_upload_rows=2,
            )
        with self.assertRaisesRegex(OutputFileGenerationError, "file-log context"):
            _parse_qsimtp_snapshot_metadata(
                [_snapshot_row(active_upload_rows=2)],
                process_ref="2806513",
                source=DataSourceChoice.ACTIVE,
                candidate_upload_rows=2,
            )
        active = _parse_qsimtp_snapshot_metadata(
            [
                _snapshot_row(
                    active_upload_rows=2,
                    active_file_log_rows=1,
                    active_requested_file_log_rows=1,
                )
            ],
            process_ref="2806513",
            source=DataSourceChoice.ACTIVE,
            candidate_upload_rows=2,
        )
        self.assertEqual(active.upload_rows, 2)

    def test_archive_generation_revalidates_guard_body_date_and_messages(self) -> None:
        spec = spec_for_code("IFQSIMTP")
        processed_fixtures = tuple(
            _BodyFixture(
                "A1",
                "001",
                "ACC-A1",
                "P",
                sim_date=f"202608{day:02d}",
            )
            for day in range(1, 11)
        )
        failed_fixture = _BodyFixture(
            "A2",
            "",
            "",
            "E",
            "E-ONE;",
            "P",
            value_date="",
            maturity_date="",
            product="",
            user_status="",
            sim_date="",
            compamt=None,
        )
        body_rows = [
            *(
                _central_body_row(fixture, record_reference=1)
                for fixture in processed_fixtures
            ),
            _central_body_row(failed_fixture, record_reference=2),
        ]
        snapshot = [_snapshot_row()]
        message_rows = [_hex("E-ONE") + "|1|" + _hex("Mensaje $1!")]

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sqlcl_path = root / "sql.exe"
            sqlcl_path.touch()
            runner = _QueuedSqlclRunner(
                (
                    ["UPLOAD_MASTER|ARCHIVE|IFQSIMTP|2"],
                    ["IFQSIMTP|OFQSIMTP"],
                    ["20260805|1|1"],
                    snapshot,
                    body_rows,
                    message_rows,
                    ["142530"],
                    snapshot,
                    body_rows,
                    ["20260805|1|1"],
                    message_rows,
                    ["2|1|1"],
                )
            )
            service = OutputFileGenerationService(
                _FakeConfig(sqlcl_path),
                runner_factory=lambda _path: runner,
                decryptor=lambda _encrypted: "fake-password",
                output_root=root / "output",
            )
            target = OracleTarget(
                country="chile",
                database_key="prod-db",
                credential_key="shared-prod",
                tns="FXBFCL_19C_PROD_OCI",
                label="Chile PROD",
            )

            result = service.generate(
                GenerationRequest(target=target, process_ref_no="2806513")
            )

            self.assertIs(result.source, DataSourceChoice.ARCHIVE)
            self.assertEqual(result.output_path.name, "OFQSIMTP_20260805_142530.TXT")
            self.assertEqual(result.body_count, 11)
            self.assertEqual(result.status_counts, {"P": 10, "E": 1})
            self.assertFalse(any(line.startswith("HDR;") for line in result.lines))
            self.assertFalse(any(line.startswith("FTR;") for line in result.lines))
            validate_output_lines(spec, result.lines)
            self.assertEqual(runner.pending, 0)
            self.assertEqual(
                sum("CLTB_ASCII_UPLOAD_CONTROL" in script for script in runner.scripts),
                2,
            )
            self.assertEqual(
                sum(
                    "from GITA_UPLOAD_MASTER p" in script
                    and "left join CLTB_IFQSIMTP_LOG" in script
                    for script in runner.scripts
                ),
                2,
            )
            clock_indexes = [
                index
                for index, script in enumerate(runner.scripts)
                if "to_char(sysdate, 'HH24MISS')" in script
            ]
            body_indexes = [
                index
                for index, script in enumerate(runner.scripts)
                if "from GITA_UPLOAD_MASTER p" in script
                and "left join CLTB_IFQSIMTP_LOG" in script
            ]
            guard_indexes = [
                index
                for index, script in enumerate(runner.scripts)
                if "CLTB_ASCII_UPLOAD_CONTROL" in script
            ]
            self.assertEqual(len(clock_indexes), 1)
            self.assertLess(body_indexes[0], clock_indexes[0])
            self.assertLess(clock_indexes[0], guard_indexes[1])
            self.assertLess(clock_indexes[0], body_indexes[1])


if __name__ == "__main__":
    unittest.main()
