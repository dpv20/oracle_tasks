from __future__ import annotations

import hashlib
import re
import sys
import tempfile
import unittest
from pathlib import Path


SRC_DIR = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_DIR))

from features.output_file_generation.formats import (  # noqa: E402
    build_footer,
    build_header,
    format_form_message,
    physical_filename,
    spec_for_code,
    split_oacmclos_error_codes,
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
    build_database_time_query,
    build_footer_status_query,
    build_input_physical_filename_query,
    build_stdinrou_dependency_guard_query,
)
from features.output_file_generation.service import (  # noqa: E402
    OutputFileGenerationError,
    _collect_error_codes,
    _parse_body_records,
    _parse_stdinrou_dependency_metadata,
    _render_body_records,
)
from tests.test_output_file_generation import (  # noqa: E402
    _FakeConfig,
    _QueuedSqlclRunner,
)


# SELECT-only PROD evidence captured on 2026-08-07.  The package's body source
# has one row per input record, while the standard upload archive contains
# multiple target-table rows for some of the same RECORD_REFERENCE values.
PROD_221887_EVIDENCE = {
    "process_ref_no": "221887",
    "active_processes": 0,
    "file_date": "20161005",
    "standard_rows": 228_448,
    "standard_distinct_references": 166_549,
    "custom_rows": 166_549,
    "custom_distinct_references": 166_549,
    "custom_references_missing_from_standard": 0,
    "standard_references_missing_from_custom": 0,
    "standard_statuses": {"P": 226_497, "E": 1_951},
    "custom_statuses": {"P": 165_160, "E": 1_389},
    "standard_null_statuses": 0,
    "custom_null_statuses": 0,
    "custom_error_rows": 1_389,
    "custom_error_non_null_rows": 1_389,
    "custom_distinct_errors": 1,
    "custom_errors_containing_semicolon": 0,
    "custom_processed_null_errors": 165_160,
    "custom_distinct_physical_filenames": 1,
    "file_master_distinct_physical_filenames": 1,
    "input_physical_filename": "STDINRTS.TXT",
    "archive_log_distinct_physical_filenames": 0,
}


# Exact QA GIPKS_STDINROU cursor projection.  Its order is also the output
# field order after the BDY record marker.
QA_BODY_CURSOR = (
    "TRIM(FLD4)",  # BRANCH
    "TRIM(FLD2)",  # EXTREFNO
    "TRIM(FLD3)",  # ACCOUNT
    "TRIM(FLD5)",  # PRODCODE
    "DECODE(STATUS,'P','Y','N')",  # PROCSTAT
    "ERROR",  # ERRCODE
    "ERROR_PARAM",  # ERRDESC, transformed by PR_GET_ERRMSG
)


MESSAGES = {
    "E-ONE": "First $1!",
    "E-TWO": "Second $1!",
    "P-WARN": "Processed warning $1!",
}


def _hex(value: object) -> str:
    return ("" if value is None else str(value)).encode("utf-8").hex().upper()


def _central_body_row(
    record_reference: int,
    fields: tuple[str, str, str, str],
    raw_status: str,
    *,
    error_code: str = "",
    error_param: str = "",
    reference_count: int = 1,
) -> str:
    classification = "P" if raw_status == "P" else "<NON_P>"
    return "|".join(
        _hex(value)
        for value in (
            classification,
            raw_status,
            record_reference,
            reference_count,
            *(str(value or "").strip() for value in fields),
            error_code,
            error_param,
        )
    )


def _dependency_row(
    *,
    custom_rows: int = 3,
    custom_distinct_references: int = 3,
    standard_rows: int = 6,
    standard_distinct_references: int = 3,
    standard_interface_rows: int = 4,
    custom_missing_from_standard: int = 0,
    standard_missing_from_custom: int = 0,
    custom_null_statuses: int = 0,
    standard_null_statuses: int = 0,
    custom_processed: int = 2,
    custom_unprocessed: int = 1,
    standard_processed: int = 4,
    standard_unprocessed: int = 2,
    custom_filename_count: int = 1,
    custom_physical_filename: str = "STDINRTS.TXT",
) -> str:
    return "|".join(
        str(value)
        for value in (
            custom_rows,
            custom_distinct_references,
            standard_rows,
            standard_distinct_references,
            standard_interface_rows,
            custom_missing_from_standard,
            standard_missing_from_custom,
            custom_null_statuses,
            standard_null_statuses,
            custom_processed,
            custom_unprocessed,
            standard_processed,
            standard_unprocessed,
            custom_filename_count,
            _hex(custom_physical_filename),
        )
    )


def _qa_error_description(
    error_code: str,
    error_param: str,
    messages: dict[str, str],
) -> str:
    """Reproduce PR_GET_ERRMSG plus OVPKS.FN_FORMMSG from QA."""
    if not error_code:
        # PR_GET_ERRMSG returns without changing its IN OUT parameter.
        return error_param
    params = str(error_param or "").split(";")
    return "".join(
        format_form_message(
            code,
            params[index] if index < len(params) else "",
            messages,
        )
        + "~"
        for index, code in enumerate(split_oacmclos_error_codes(error_code))
    )


def _qa_body_line(
    fields: tuple[str, str, str, str],
    raw_status: str,
    *,
    error_code: str = "",
    error_param: str = "",
    messages: dict[str, str] | None = None,
) -> str:
    # Only FLD4/2/3/5 are trimmed by the package.  STATUS, ERROR and
    # ERROR_PARAM deliberately retain their raw semantics.
    data = tuple(str(value or "").strip() for value in fields)
    processed = "Y" if raw_status == "P" else "N"
    description = _qa_error_description(
        error_code,
        error_param,
        messages or {},
    )
    return ";".join(
        ("BDY", *data, processed, str(error_code or ""), description, "")
    )


def _assert_safe_reconstruction(evidence: dict[str, object]) -> None:
    """State the minimum guards for active/archive exact reconstruction."""
    custom_rows = int(evidence["custom_rows"])
    custom_distinct = int(evidence["custom_distinct_references"])
    standard_rows = int(evidence["standard_rows"])
    standard_distinct = int(evidence["standard_distinct_references"])
    if custom_rows != custom_distinct:
        raise ValueError("STDINRTS body RECORD_REFERENCE must be unique")
    if (
        custom_distinct != standard_distinct
        or int(evidence["custom_references_missing_from_standard"]) != 0
        or int(evidence["standard_references_missing_from_custom"]) != 0
    ):
        raise ValueError("STDINRTS/custom reference coverage must be bidirectional")

    standard_statuses = evidence["standard_statuses"]
    custom_statuses = evidence["custom_statuses"]
    assert isinstance(standard_statuses, dict)
    assert isinstance(custom_statuses, dict)
    if sum(int(value) for value in standard_statuses.values()) != standard_rows:
        raise ValueError("standard footer status counts must cover its source")
    if sum(int(value) for value in custom_statuses.values()) != custom_rows:
        raise ValueError("custom body status counts must cover its source")
    if int(evidence["standard_null_statuses"]) or int(
        evidence["custom_null_statuses"]
    ):
        raise ValueError("NULL status would make body/footer semantics ambiguous")
    if int(evidence["file_master_distinct_physical_filenames"]) != 1:
        raise ValueError("one input physical filename is required")
    filename = str(evidence["input_physical_filename"] or "")
    if not filename or ";" in filename or "\r" in filename or "\n" in filename:
        raise ValueError("input physical filename is invalid")
    if not re.fullmatch(r"[0-9]{8}", str(evidence["file_date"] or "")):
        raise ValueError("one YYYYMMDD business date is required")


SAMPLE_LINES = (
    "HDR;STDINRTS.TXT;07082026140855;",
    _qa_body_line((" 001 ", " EXT-1 ", " 000123 ", " SAV "), "P"),
    _qa_body_line(
        ("002", "EXT-2", "000456", "CUR"),
        "E",
        error_code="E-ONE;E-TWO;EOPL",
        error_param="ACCOUNT~;PRODUCT~;IGNORED~;",
        messages=MESSAGES,
    ),
    _qa_body_line(
        ("003", "EXT-3", "000789", "DDA"),
        "P",
        error_code="P-WARN",
        error_param="REVIEW~;",
        messages=MESSAGES,
    ),
    # Deliberately comes from a wider synthetic standard-upload population.
    "FTR;4;2;",
)
SAMPLE_PAYLOAD = ("\n".join(SAMPLE_LINES) + "\n").encode("utf-8")


def _central_mapping_is_ready() -> bool:
    try:
        return spec_for_code("STDINRTS").output_code == "STDINROU"
    except ValueError:
        return False


class StdinrouPreparedContractTests(unittest.TestCase):
    def test_qa_cursor_uses_the_custom_table_fields_in_exact_output_order(self) -> None:
        self.assertEqual(
            QA_BODY_CURSOR,
            (
                "TRIM(FLD4)",
                "TRIM(FLD2)",
                "TRIM(FLD3)",
                "TRIM(FLD5)",
                "DECODE(STATUS,'P','Y','N')",
                "ERROR",
                "ERROR_PARAM",
            ),
        )
        self.assertEqual(
            SAMPLE_LINES[1],
            "BDY;001;EXT-1;000123;SAV;Y;;;",
        )

    def test_reference_coverage_is_exact_despite_duplicate_standard_rows(self) -> None:
        evidence = PROD_221887_EVIDENCE
        _assert_safe_reconstruction(evidence)

        self.assertEqual(
            evidence["custom_rows"],
            evidence["custom_distinct_references"],
        )
        self.assertEqual(
            evidence["custom_distinct_references"],
            evidence["standard_distinct_references"],
        )
        self.assertEqual(evidence["custom_references_missing_from_standard"], 0)
        self.assertEqual(evidence["standard_references_missing_from_custom"], 0)
        self.assertGreater(
            evidence["standard_rows"],
            evidence["standard_distinct_references"],
        )

    def test_body_order_is_unique_but_standard_footer_rows_need_no_order(self) -> None:
        evidence = PROD_221887_EVIDENCE
        self.assertEqual(
            evidence["custom_rows"],
            evidence["custom_distinct_references"],
        )
        self.assertNotEqual(
            evidence["standard_rows"],
            evidence["standard_distinct_references"],
        )
        # QA orders only STDINRTS_UPLOAD_MASTER by RECORD_REFERENCE.  The
        # duplicated GITA rows feed aggregate counts, never body ordering.
        self.assertEqual(QA_BODY_CURSOR[:4], (
            "TRIM(FLD4)",
            "TRIM(FLD2)",
            "TRIM(FLD3)",
            "TRIM(FLD5)",
        ))

    def test_footer_intentionally_counts_the_wider_standard_population(self) -> None:
        evidence = PROD_221887_EVIDENCE
        standard = evidence["standard_statuses"]
        custom = evidence["custom_statuses"]
        assert isinstance(standard, dict)
        assert isinstance(custom, dict)

        self.assertEqual(sum(standard.values()), evidence["standard_rows"])
        self.assertEqual(sum(custom.values()), evidence["custom_rows"])
        self.assertEqual(standard, {"P": 226_497, "E": 1_951})
        self.assertEqual(custom, {"P": 165_160, "E": 1_389})
        self.assertEqual("FTR;{};{};".format(standard["P"], standard["E"]),
                         "FTR;226497;1951;")
        self.assertGreater(sum(standard.values()), sum(custom.values()))

    def test_all_statuses_render_and_error_helper_runs_even_for_processed_rows(self) -> None:
        self.assertTrue(SAMPLE_LINES[2].startswith(
            "BDY;002;EXT-2;000456;CUR;N;E-ONE;E-TWO;EOPL;"
        ))
        self.assertTrue(SAMPLE_LINES[2].endswith(
            "First ACCOUNT~Second PRODUCT~;"
        ))
        self.assertEqual(
            SAMPLE_LINES[3],
            "BDY;003;EXT-3;000789;DDA;Y;P-WARN;Processed warning REVIEW~;",
        )
        self.assertEqual(
            _qa_body_line(
                ("004", "EXT-4", "000999", "SAV"),
                "p",
                error_param="RAW-PARAM",
            ),
            "BDY;004;EXT-4;000999;SAV;N;;RAW-PARAM;",
        )

    def test_error_helper_is_positional_stops_at_eopl_and_has_fallback(self) -> None:
        self.assertEqual(
            _qa_error_description(
                "E-ONE;E-TWO;EOPL;IGNORED",
                "ACCOUNT~;PRODUCT~;UNUSED~;",
                MESSAGES,
            ),
            "First ACCOUNT~Second PRODUCT~",
        )
        self.assertEqual(
            _qa_error_description("UNKNOWN", "", {}),
            "Missing Error Code~",
        )
        self.assertEqual(_qa_error_description("", "RAW-PARAM", {}), "RAW-PARAM")

    def test_header_uses_execution_clock_but_filename_uses_business_date(self) -> None:
        # QA's HH24MMSS typo repeats the execution month (08) where minutes
        # would normally appear. The physical filename retains real minutes.
        self.assertEqual(SAMPLE_LINES[0], "HDR;STDINRTS.TXT;07082026140855;")
        # PROD's raw mask escapes a literal '$' before its date tokens.
        self.assertEqual(
            "STDINROU_$" + PROD_221887_EVIDENCE["file_date"] + "142355.TXT",
            "STDINROU_$20161005142355.TXT",
        )
        self.assertEqual(
            PROD_221887_EVIDENCE["archive_log_distinct_physical_filenames"],
            0,
        )
        self.assertEqual(
            PROD_221887_EVIDENCE["file_master_distinct_physical_filenames"],
            1,
        )

    def test_reconstruction_guards_reject_partial_or_ambiguous_live_data(self) -> None:
        cases = (
            ("custom_distinct_references", 166_548, "must be unique"),
            (
                "custom_references_missing_from_standard",
                1,
                "coverage must be bidirectional",
            ),
            ("standard_references_missing_from_custom", 1, "coverage must be bidirectional"),
            ("standard_null_statuses", 1, "NULL status"),
            ("file_master_distinct_physical_filenames", 2, "one input physical"),
            ("file_date", "2016-10-05", "YYYYMMDD"),
        )
        for key, value, expected in cases:
            with self.subTest(key=key):
                changed = dict(PROD_221887_EVIDENCE)
                changed[key] = value
                with self.assertRaisesRegex(ValueError, expected):
                    _assert_safe_reconstruction(changed)

        changed = dict(PROD_221887_EVIDENCE)
        changed["standard_statuses"] = {"P": 226_497, "E": 1_950}
        with self.assertRaisesRegex(ValueError, "footer status counts"):
            _assert_safe_reconstruction(changed)

        changed = dict(PROD_221887_EVIDENCE)
        changed["custom_statuses"] = {"P": 165_160, "E": 1_388}
        with self.assertRaisesRegex(ValueError, "body status counts"):
            _assert_safe_reconstruction(changed)

    def test_sample_client_bytes_are_lf_only_and_frozen(self) -> None:
        self.assertNotIn(b"\r", SAMPLE_PAYLOAD)
        self.assertEqual(len(SAMPLE_PAYLOAD), 208)
        self.assertEqual(
            hashlib.sha256(SAMPLE_PAYLOAD).hexdigest(),
            "6277bd2da4d301b81762ee0fc8e1d9537f672df47d76fdfa6a616e30b3e1bdaf",
        )


@unittest.skipUnless(
    _central_mapping_is_ready(),
    "STDINRTS->STDINROU central adapter is intentionally not integrated yet",
)
class StdinrouCentralAdapterContractTests(unittest.TestCase):
    """Automatically activates when the mapping is registered centrally."""

    def test_spec_reproduces_header_filename_footer_and_wider_counts(self) -> None:
        spec = spec_for_code("STDINRTS")

        self.assertEqual(spec.output_code, "STDINROU")
        self.assertIs(spec_for_code("STDINROU"), spec)
        self.assertEqual(spec.contract, "stdinrou")
        self.assertEqual(spec.body_field_count, 8)
        self.assertEqual(spec.footer_record, "FTR")
        self.assertEqual(spec.footer_field_count, 3)
        self.assertTrue(spec.timestamped_header)
        self.assertTrue(spec.header_uses_database_date)
        self.assertTrue(spec.header_uses_input_filename)
        self.assertTrue(spec.footer_counts_all_upload_rows)
        self.assertTrue(spec.revalidate_external_inputs)
        self.assertEqual(spec.error_message_mode, "list_tilde")
        self.assertEqual(spec.physical_name_pattern, "STDINROU_${date}{time}.TXT")
        self.assertEqual(
            physical_filename(spec, "20161005", "142355"),
            "STDINROU_$20161005142355.TXT",
        )
        self.assertEqual(
            build_header(
                spec,
                "20161005",
                "142355",
                "STDINRTS.TXT",
                "20260807",
            ),
            "HDR;STDINRTS.TXT;07082026140855;",
        )
        self.assertEqual(
            build_footer(spec, 166_549, {"P": 226_497, "<NON_P>": 1_951}),
            "FTR;226497;1951;",
        )

    def test_validator_accepts_open_error_tail_and_wider_footer(self) -> None:
        spec = spec_for_code("STDINRTS")
        validate_output_lines(spec, SAMPLE_LINES)

    def test_queries_preserve_custom_body_standard_footer_and_file_master_split(self) -> None:
        header_clock = build_database_time_query("YYYYMMDDHH24MISS").script.lower()
        filename_clock = build_database_time_query("HH24MISS").script.lower()
        self.assertIn("to_char(sysdate, 'yyyymmddhh24miss')", header_clock)
        self.assertIn("to_char(sysdate, 'hh24miss')", filename_clock)

        for source, standard_table in (
            (DataSourceChoice.ACTIVE, "gitu_upload_master"),
            (DataSourceChoice.ARCHIVE, "gita_upload_master"),
        ):
            with self.subTest(source=source):
                body = " ".join(
                    build_body_query(
                        "221887",
                        "STDINRTS",
                        source,
                        "20161005",
                    ).script.lower().split()
                )
                self.assertIn("from stdinrts_upload_master u", body)
                self.assertIn("order by u.record_reference", body)
                self.assertNotIn(standard_table, body)

                guard = " ".join(
                    build_stdinrou_dependency_guard_query(
                        "221887",
                        source,
                    ).script.lower().split()
                )
                self.assertIn("from stdinrts_upload_master c", guard)
                self.assertIn(f"from {standard_table} s", guard)
                self.assertGreaterEqual(guard.count("not exists"), 2)

                footer = " ".join(
                    build_footer_status_query(
                        "221887",
                        "STDINRTS",
                        source,
                    ).script.lower().split()
                )
                self.assertIn(f"from {standard_table}", footer)
                self.assertNotIn("interface_code", footer)

                filename = " ".join(
                    build_input_physical_filename_query(
                        "221887",
                        "STDINRTS",
                        source,
                    ).script.lower().split()
                )
                self.assertIn("from gitb_file_master", filename)
                self.assertNotIn("gita_file_log", filename)

                for script in (body, guard, footer, filename):
                    self.assertNotRegex(
                        script,
                        r"\b(?:insert|update|delete|merge|commit|rollback|truncate)\b",
                    )

    def test_parser_renderer_runs_positional_error_helper_for_every_status(self) -> None:
        spec = spec_for_code("STDINRTS")
        rows = [
            _central_body_row(1, ("001", "EXT-1", "000123", "SAV"), "P"),
            _central_body_row(
                2,
                ("002", "EXT-2", "000456", "CUR"),
                "E",
                error_code="E-ONE;E-TWO;EOPL",
                error_param="ACCOUNT~;PRODUCT~;IGNORED~;",
            ),
            _central_body_row(
                3,
                ("003", "EXT-3", "000789", "DDA"),
                "P",
                error_code="P-WARN",
                error_param="REVIEW~;",
            ),
        ]

        records, counts = _parse_body_records(rows, spec)

        self.assertEqual(counts, {"P": 2, "<NON_P>": 1})
        self.assertEqual(
            _collect_error_codes(spec, records),
            {"E-ONE", "E-TWO", "P-WARN"},
        )
        self.assertEqual(
            _render_body_records(spec, records, MESSAGES),
            list(SAMPLE_LINES[1:4]),
        )

    def test_parser_rejects_null_status_duplicate_reference_and_wrong_order(self) -> None:
        spec = spec_for_code("STDINRTS")
        base = ("001", "EXT-1", "000123", "SAV")
        with self.assertRaisesRegex(OutputFileGenerationError, "status metadata"):
            _parse_body_records([_central_body_row(1, base, "")], spec)
        with self.assertRaisesRegex(OutputFileGenerationError, "must be unique"):
            _parse_body_records(
                [_central_body_row(1, base, "P", reference_count=2)],
                spec,
            )
        with self.assertRaisesRegex(OutputFileGenerationError, "must be unique"):
            _parse_body_records(
                [
                    _central_body_row(1, base, "P"),
                    _central_body_row(1, base, "E"),
                ],
                spec,
            )
        with self.assertRaisesRegex(OutputFileGenerationError, "cursor order"):
            _parse_body_records(
                [
                    _central_body_row(2, base, "P"),
                    _central_body_row(1, base, "E"),
                ],
                spec,
            )

    def test_dependency_guard_never_equates_custom_body_rows_to_standard_rows(self) -> None:
        metadata = _parse_stdinrou_dependency_metadata(
            [_dependency_row()],
            candidate_upload_rows=4,
        )
        self.assertEqual(metadata.custom_rows, 3)
        self.assertEqual(metadata.standard_rows, 6)
        self.assertEqual(metadata.standard_distinct_references, 3)

        with self.assertRaisesRegex(OutputFileGenerationError, "bidirectional"):
            _parse_stdinrou_dependency_metadata(
                [_dependency_row(custom_missing_from_standard=1)],
                candidate_upload_rows=4,
            )
        with self.assertRaisesRegex(OutputFileGenerationError, "unique"):
            _parse_stdinrou_dependency_metadata(
                [_dependency_row(custom_distinct_references=2)],
                candidate_upload_rows=4,
            )

    def test_archive_generation_revalidates_every_live_dependency_before_save(self) -> None:
        body_rows = [
            _central_body_row(1, ("001", "EXT-1", "000123", "SAV"), "P"),
            _central_body_row(
                2,
                ("002", "EXT-2", "000456", "CUR"),
                "E",
                error_code="E-ONE;E-TWO;EOPL",
                error_param="ACCOUNT~;PRODUCT~;IGNORED~;",
            ),
            _central_body_row(
                3,
                ("003", "EXT-3", "000789", "DDA"),
                "P",
                error_code="P-WARN",
                error_param="REVIEW~;",
            ),
        ]
        dependencies = [_dependency_row()]
        footer_rows = ["P|4", "<NON_P>|2", "<NULL>|0"]
        filename_rows = ["1|" + _hex("STDINRTS.TXT")]
        message_rows = [
            _hex(code) + "|1|" + _hex(message)
            for code, message in sorted(MESSAGES.items())
        ]

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sqlcl_path = root / "sql.exe"
            sqlcl_path.touch()
            runner = _QueuedSqlclRunner(
                (
                    ["UPLOAD_MASTER|ARCHIVE|STDINRTS|4"],
                    ["STDINRTS|STDINROU"],
                    ["20161005|1|1"],
                    dependencies,
                    ["20260807142355"],
                    filename_rows,
                    body_rows,
                    footer_rows,
                    message_rows,
                    ["173045"],
                    dependencies,
                    body_rows,
                    footer_rows,
                    ["20161005|1|1"],
                    filename_rows,
                    message_rows,
                    ["4|1|1"],
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
                GenerationRequest(target=target, process_ref_no="221887")
            )

            self.assertIs(result.source, DataSourceChoice.ARCHIVE)
            self.assertEqual(result.output_path.name, "STDINROU_$20161005173045.TXT")
            self.assertEqual(result.body_count, 3)
            self.assertEqual(result.status_counts, {"P": 2, "<NON_P>": 1})
            self.assertEqual(result.lines, SAMPLE_LINES)
            self.assertEqual(result.output_path.read_bytes(), SAMPLE_PAYLOAD)
            self.assertEqual(runner.pending, 0)
            self.assertEqual(
                sum("STDINROU_DEPENDENCIES" in script for script in runner.scripts),
                2,
            )
            self.assertEqual(
                sum("STDINROU_FOOTER_STATUS" in script for script in runner.scripts),
                2,
            )
            stdinrou_clock_scripts = [
                script for script in runner.scripts if "to_char(sysdate" in script
            ]
            self.assertEqual(len(stdinrou_clock_scripts), 2)
            self.assertIn("'YYYYMMDDHH24MISS'", stdinrou_clock_scripts[0])
            self.assertIn("'HH24MISS'", stdinrou_clock_scripts[1])
            body_indexes = [
                index
                for index, script in enumerate(runner.scripts)
                if "from STDINRTS_UPLOAD_MASTER u" in script
            ]
            dependency_indexes = [
                index
                for index, script in enumerate(runner.scripts)
                if "STDINROU_DEPENDENCIES" in script
            ]
            clock_indexes = [
                index
                for index, script in enumerate(runner.scripts)
                if "to_char(sysdate" in script
            ]
            self.assertLess(clock_indexes[0], body_indexes[0])
            self.assertLess(body_indexes[0], clock_indexes[1])
            self.assertLess(clock_indexes[1], dependency_indexes[1])
            self.assertLess(clock_indexes[1], body_indexes[1])


if __name__ == "__main__":
    unittest.main()
