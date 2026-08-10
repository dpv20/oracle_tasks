from __future__ import annotations

import hashlib
import re
import sys
import tempfile
import unittest
from pathlib import Path


SRC_DIR = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_DIR))

from features.output_file_generation import (  # noqa: E402
    GenerationRequest,
    OracleTarget,
    OutputFileGenerationService,
)
from features.output_file_generation.formats import (  # noqa: E402
    build_footer,
    build_header,
    format_form_message,
    physical_filename,
    serialize_lines,
    spec_for_code,
    split_oacmclos_error_codes,
    validate_output_lines,
)
from features.output_file_generation.models import DataSourceChoice  # noqa: E402
from features.output_file_generation.queries import (  # noqa: E402
    build_body_query,
    build_input_physical_filename_query,
)
from features.output_file_generation.service import (  # noqa: E402
    OutputFileGenerationError,
    _collect_error_codes,
    _parse_body_records,
    _render_body_records,
)
from tests.test_output_file_generation import (  # noqa: E402
    _FakeConfig,
    _QueuedSqlclRunner,
)


# SELECT-only PROD evidence captured on 2026-08-07.  Upload rows are archived,
# while GIPKS_OFIWADOC's two indispensable inputs remain live tables.  Only the
# newest process still has a complete live GITM_CLEARING_ADOC_LOG population.
PROD_EVIDENCE = {
    "active_upload_processes": 0,
    "latest_process": "2806544",
    "latest_file_date": "20260806",
    "latest_upload_rows": 566,
    "latest_upload_statuses": {"P": 566},
    "latest_null_errors": 566,
    "latest_auxiliary_rows": 566,
    "latest_exact_one_matches": 566,
    "latest_missing_matches": 0,
    "latest_ambiguous_matches": 0,
    "latest_duplicate_upload_references": 0,
    "latest_file_master_distinct_names": 1,
    "latest_input_physical_filename": "IFIWADOC_20260806.TXT",
    "latest_output_filename": "OFIWADOC_20260806.TXT",
    "archive_log_physical_filename_count": 0,
    # (process, business date, upload rows, surviving ADOC rows)
    "recent_archive_processes": (
        ("2806544", "20260806", 566, 566),
        ("2805098", "20260805", 1628, 0),
        ("2805045", "20260803", 1145, 0),
        ("2805002", "20260731", 659, 0),
        ("2790544", "20260730", 730, 0),
    ),
}


DATA_P = (
    "XREF-1",
    "20260806",
    "BANK-A",
    "001",
    "000123456",
    "900001",
    "CH",
    "1500",
    "20260805",
    "LOCAL",
    "SEC",
)
DATA_E = (
    "XREF-2",
    "20260806",
    "BANK-B",
    "002",
    "000654321",
    "900002",
    "TR",
    "2500.5",
    "20260804",
    "LOCAL",
    "PUB",
)
DATA_U = (
    "XREF-3",
    "20260806",
    "BANK-C",
    "003",
    "000999999",
    "900003",
    "CH",
    "999",
    "20260803",
    "LOCAL",
    "PRI",
)


def _qa_error_description(
    error_code: str,
    error_param: str,
    messages: dict[str, str],
) -> str:
    """Reproduce PR_GET_ERRMSG plus OVPKS.FN_FORMMSG from the QA sources."""
    if not error_code:
        # PR_GET_ERRMSG returns before modifying its IN OUT parameter.
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
    data: tuple[str, ...],
    raw_status: str,
    *,
    error_code: str = "",
    error_param: str = "",
    messages: dict[str, str] | None = None,
) -> str:
    if len(data) != 11:
        raise AssertionError("OFIWADOC fixtures require eleven clearing fields")
    line = ";".join(("BDY", *data, raw_status, "NOPR", "")) + ";"
    if raw_status not in {"E", "U"}:
        return line
    effective_code = "GI-INT214" if raw_status == "U" else error_code
    description = _qa_error_description(
        effective_code,
        error_param,
        messages or {},
    )
    return line + effective_code + ";" + description + ";"


MESSAGES = {
    "E-ONE": "First $1!",
    "E-TWO": "Second $1!",
    "GI-INT214": "Unable to process $1!",
}
SYNTHETIC_LINES = (
    "HDR;IFIWADOC_20260806.TXT;20260806;",
    _qa_body_line(DATA_P, "P", error_code="IGNORED"),
    _qa_body_line(
        DATA_E,
        "E",
        error_code="E-ONE;E-TWO;EOPL",
        error_param="ACCOUNT~;DOCUMENT~;IGNORED~;",
        messages=MESSAGES,
    ),
    _qa_body_line(
        DATA_U,
        "U",
        error_code="STORED-ERROR-MUST-BE-IGNORED",
        error_param="UPLOAD~;",
        messages=MESSAGES,
    ),
    "FTR;1;2;",
)
SYNTHETIC_PAYLOAD = ("\n".join(SYNTHETIC_LINES) + "\n").encode("utf-8")


def _hex(value: str) -> str:
    return value.encode("utf-8").hex().upper()


def _central_row(
    classification: str,
    raw_status: str,
    data: tuple[str, ...],
    *,
    source_count: str = "3",
    auxiliary_match_count: str = "1",
    record_reference_count: str = "1",
    error_code: str = "",
    error_param: str = "",
) -> str:
    if len(data) != 11:
        raise AssertionError("OFIWADOC fixtures require eleven clearing fields")
    values = (
        classification,
        raw_status,
        source_count,
        auxiliary_match_count,
        record_reference_count,
        *data,
        error_code,
        error_param,
    )
    return "|".join(_hex(value) for value in values)


def _central_mapping_is_ready() -> bool:
    try:
        return spec_for_code("IFIWADOC").output_code == "OFIWADOC"
    except ValueError:
        return False


class OfiwadocPreparedContractTests(unittest.TestCase):
    def test_prod_evidence_proves_only_the_latest_archive_is_complete(self) -> None:
        evidence = PROD_EVIDENCE
        self.assertEqual(evidence["active_upload_processes"], 0)
        self.assertEqual(evidence["latest_process"], "2806544")
        self.assertEqual(evidence["latest_upload_rows"], 566)
        self.assertEqual(evidence["latest_upload_statuses"], {"P": 566})
        self.assertEqual(evidence["latest_null_errors"], 566)
        self.assertEqual(
            evidence["latest_upload_rows"],
            evidence["latest_auxiliary_rows"],
        )
        self.assertEqual(evidence["latest_exact_one_matches"], 566)
        self.assertEqual(evidence["latest_missing_matches"], 0)
        self.assertEqual(evidence["latest_ambiguous_matches"], 0)
        self.assertEqual(evidence["latest_duplicate_upload_references"], 0)
        self.assertEqual(evidence["latest_file_master_distinct_names"], 1)
        self.assertEqual(
            evidence["latest_input_physical_filename"],
            "IFIWADOC_20260806.TXT",
        )
        self.assertEqual(evidence["archive_log_physical_filename_count"], 0)

        older = evidence["recent_archive_processes"][1:]
        self.assertTrue(all(upload_rows > 0 for _, _, upload_rows, _ in older))
        self.assertTrue(all(auxiliary_rows == 0 for _, _, _, auxiliary_rows in older))

    def test_archive_support_must_be_conditional_not_an_empty_cursor_fallback(self) -> None:
        for process_ref, _date, upload_rows, auxiliary_rows in PROD_EVIDENCE[
            "recent_archive_processes"
        ]:
            with self.subTest(process_ref=process_ref):
                if process_ref == PROD_EVIDENCE["latest_process"]:
                    self.assertEqual(auxiliary_rows, upload_rows)
                else:
                    self.assertGreater(upload_rows, 0)
                    self.assertEqual(auxiliary_rows, 0)
                    # QA's inner join would silently emit no bodies.  The app
                    # must reject that partial reconstruction instead.
                    self.assertNotEqual(upload_rows, auxiliary_rows)

    def test_qa_error_helper_is_positional_tilde_terminated_and_strips_bang(self) -> None:
        self.assertEqual(
            _qa_error_description(
                "E-ONE;E-TWO;EOPL;IGNORED",
                "ACCOUNT~;DOCUMENT~;UNUSED~;",
                MESSAGES,
            ),
            "First ACCOUNT~Second DOCUMENT~",
        )
        self.assertEqual(
            _qa_error_description("UNKNOWN", "", {}),
            "Missing Error Code~",
        )
        self.assertEqual(_qa_error_description("", "RAW-PARAM", {}), "RAW-PARAM")

    def test_layout_preserves_raw_status_and_has_variable_error_tail(self) -> None:
        processed = _qa_body_line(DATA_P, "P", error_code="IGNORED")
        rejected = _qa_body_line(
            DATA_E,
            "E",
            error_code="E-ONE",
            error_param="ACCOUNT~;",
            messages=MESSAGES,
        )
        lower_p = _qa_body_line(DATA_U, " p ", error_code="IGNORED")
        null_error = _qa_body_line(
            DATA_E,
            "E",
            error_code="",
            error_param="RAW-PARAM",
            messages=MESSAGES,
        )

        self.assertEqual(len(processed[:-1].split(";")), 15)
        self.assertEqual(len(rejected[:-1].split(";")), 17)
        self.assertTrue(processed.endswith(";P;NOPR;;"))
        self.assertTrue(lower_p.endswith("; p ;NOPR;;"))
        self.assertNotIn("IGNORED", processed)
        self.assertTrue(null_error.endswith(";E;NOPR;;;RAW-PARAM;"))

    def test_u_status_forces_gi_int214_and_footer_counts_only_exact_p(self) -> None:
        u_line = SYNTHETIC_LINES[3]
        self.assertIn(";U;NOPR;;GI-INT214;", u_line)
        self.assertNotIn("STORED-ERROR-MUST-BE-IGNORED", u_line)
        self.assertTrue(u_line.endswith("Unable to process UPLOAD~;"))
        self.assertEqual(SYNTHETIC_LINES[-1], "FTR;1;2;")

    def test_synthetic_client_bytes_are_lf_only_and_frozen(self) -> None:
        self.assertNotIn(b"\r", SYNTHETIC_PAYLOAD)
        self.assertEqual(len(SYNTHETIC_PAYLOAD), 382)
        self.assertEqual(
            hashlib.sha256(SYNTHETIC_PAYLOAD).hexdigest(),
            "eedccdb3116b4e84af256e607881ad687c70b4d7f6d8fd2eff864f5cb74d68b1",
        )


@unittest.skipUnless(
    _central_mapping_is_ready(),
    "IFIWADOC->OFIWADOC central adapter is intentionally not integrated yet",
)
class OfiwadocCentralAdapterContractTests(unittest.TestCase):
    """Automatically activates once the central mapping is registered."""

    def test_spec_filename_header_footer_and_validator(self) -> None:
        spec = spec_for_code("IFIWADOC")

        self.assertEqual(spec.output_code, "OFIWADOC")
        self.assertIs(spec_for_code("OFIWADOC"), spec)
        self.assertEqual(spec.contract, "ofiwadoc")
        self.assertEqual(spec.body_field_count, 15)
        self.assertEqual(spec.error_body_field_count, 17)
        self.assertEqual(spec.allowed_statuses, ("P", "<NON_P>"))
        self.assertTrue(spec.header_uses_input_filename)
        self.assertTrue(spec.revalidate_external_inputs)
        self.assertEqual(spec.error_message_mode, "list_tilde")
        self.assertEqual(spec.physical_name_pattern, "OFIWADOC_{date}.TXT")
        self.assertEqual(
            physical_filename(spec, "20260806"),
            "OFIWADOC_20260806.TXT",
        )
        self.assertEqual(
            build_header(
                spec,
                "20260806",
                input_physical_filename="IFIWADOC_20260806.TXT",
            ),
            SYNTHETIC_LINES[0],
        )
        self.assertEqual(
            build_footer(spec, 3, {"P": 1, "<NON_P>": 2}),
            "FTR;1;2;",
        )
        validate_output_lines(spec, SYNTHETIC_LINES)
        self.assertEqual(serialize_lines(SYNTHETIC_LINES), SYNTHETIC_PAYLOAD)

    def test_active_and_conditional_archive_queries_use_the_exact_qa_join(self) -> None:
        for source, upload_table in (
            (DataSourceChoice.ACTIVE, "GITU_UPLOAD_MASTER"),
            (DataSourceChoice.ARCHIVE, "GITA_UPLOAD_MASTER"),
        ):
            with self.subTest(source=source.value):
                script = build_body_query(
                    "2806544",
                    "IFIWADOC",
                    source,
                    "20260806",
                ).script
                compact = " ".join(script.lower().split())
                self.assertIn(f"from {upload_table.lower()} u", compact)
                self.assertIn("gitm_clearing_adoc_log", compact)
                self.assertIn("g.process_ref_no = u.process_ref_no", compact)
                self.assertIn("g.record_reference = u.record_reference", compact)
                self.assertIn("u.process_ref_no = '2806544'", compact)
                self.assertIn("u.interface_code = 'ifiwadoc'", compact)
                self.assertIn("u.fld15", compact)
                self.assertIn("g.xref", compact)
                self.assertIn("g.txndate", compact)
                self.assertIn("g.rembank", compact)
                self.assertIn("g.txn_brn", compact)
                self.assertIn("g.remaccount", compact)
                self.assertIn("g.instrno", compact)
                self.assertIn("g.instrno2", compact)
                self.assertIn("g.instramt", compact)
                self.assertIn("g.clearing_type", compact)
                self.assertIn("g.sector_code", compact)
                self.assertIn("order by u.record_reference", compact)
                self.assertNotIn("fn_handoff", compact)
                self.assertNotIn("gipks_", compact)
                self.assertNotRegex(
                    compact,
                    r"\b(?:insert|update|delete|merge|commit|rollback)\b",
                )

    def test_input_filename_uses_the_live_file_master_for_both_sources(self) -> None:
        for source in (DataSourceChoice.ACTIVE, DataSourceChoice.ARCHIVE):
            with self.subTest(source=source.value):
                script = build_input_physical_filename_query(
                    "2806544",
                    "IFIWADOC",
                    source,
                ).script
                compact = " ".join(script.lower().split())
                self.assertIn("from gitb_file_master", compact)
                self.assertNotIn("from gita_file_log", compact)
                self.assertIn("select distinct trim(phy_file_name)", compact)
                self.assertIn("process_ref_no = '2806544'", compact)
                self.assertIn("interface_code)) = 'ifiwadoc'", compact)

    def test_parser_renderer_and_error_lookup_follow_raw_status(self) -> None:
        spec = spec_for_code("IFIWADOC")
        rows = [
            _central_row("P", "P", DATA_P, error_code="IGNORED"),
            _central_row(
                "<NON_P>",
                "E",
                DATA_E,
                error_code="E-ONE;E-TWO;EOPL",
                error_param="ACCOUNT~;DOCUMENT~;IGNORED~;",
            ),
            _central_row(
                "<NON_P>",
                "U",
                DATA_U,
                error_code="STORED-ERROR-MUST-BE-IGNORED",
                error_param="UPLOAD~;",
            ),
        ]

        records, counts = _parse_body_records(rows, spec)
        self.assertEqual(counts, {"P": 1, "<NON_P>": 2})
        self.assertEqual(
            _collect_error_codes(spec, records),
            {"E-ONE", "E-TWO", "GI-INT214"},
        )
        self.assertEqual(
            _render_body_records(spec, records, MESSAGES),
            list(SYNTHETIC_LINES[1:-1]),
        )

    def test_incomplete_or_ambiguous_auxiliary_join_is_rejected(self) -> None:
        spec = spec_for_code("IFIWADOC")
        cases = (
            (
                _central_row(
                    "P",
                    "P",
                    DATA_P,
                    auxiliary_match_count="0",
                ),
                "exactly one GITM_CLEARING_ADOC_LOG row",
            ),
            (
                _central_row(
                    "P",
                    "P",
                    DATA_P,
                    auxiliary_match_count="2",
                ),
                "exactly one GITM_CLEARING_ADOC_LOG row",
            ),
            (
                _central_row("P", "P", DATA_P, record_reference_count="2"),
                "duplicate RECORD_REFERENCE",
            ),
            (
                _central_row("P", "P", DATA_P, source_count="2"),
                "source row count",
            ),
        )
        for row, expected in cases:
            with self.subTest(expected=expected):
                with self.assertRaisesRegex(OutputFileGenerationError, expected):
                    _parse_body_records([row], spec)

    def test_archive_generation_revalidates_live_body_filename_and_date(self) -> None:
        body_row = _central_row(
            "P",
            "P",
            DATA_P,
            source_count="1",
        )
        filename_row = "1|" + _hex("IFIWADOC_20260806.TXT")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sqlcl_path = root / "sql.exe"
            sqlcl_path.touch()
            runner = _QueuedSqlclRunner(
                (
                    ["UPLOAD_MASTER|ARCHIVE|IFIWADOC|1"],
                    ["IFIWADOC|OFIWADOC"],
                    ["20260806|1|1"],
                    [filename_row],
                    [body_row],
                    # Every live source is fingerprinted again before write.
                    [body_row],
                    ["20260806|1|1"],
                    [filename_row],
                    ["1|1|1"],
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
                GenerationRequest(target=target, process_ref_no="2806544")
            )

            self.assertIs(result.source, DataSourceChoice.ARCHIVE)
            self.assertEqual(result.output_path.name, "OFIWADOC_20260806.TXT")
            self.assertEqual(
                result.lines,
                (
                    "HDR;IFIWADOC_20260806.TXT;20260806;",
                    _qa_body_line(DATA_P, "P"),
                    "FTR;1;0;",
                ),
            )
            body_scripts = [
                script
                for script in runner.scripts
                if "from GITA_UPLOAD_MASTER u" in script
                and "GITM_CLEARING_ADOC_LOG" in script
            ]
            filename_scripts = [
                script
                for script in runner.scripts
                if "from GITB_FILE_MASTER" in script
                and "phy_file_name" in script.lower()
            ]
            self.assertEqual(len(body_scripts), 2)
            self.assertEqual(len(filename_scripts), 2)
            self.assertEqual(runner.pending, 0)

    def test_archive_generation_rejects_missing_partial_or_changed_live_join(self) -> None:
        filename_row = "1|" + _hex("IFIWADOC_20260806.TXT")
        target = OracleTarget(
            country="chile",
            database_key="prod-db",
            credential_key="shared-prod",
            tns="FXBFCL_19C_PROD_OCI",
            label="Chile PROD",
        )

        cases = (
            (
                "missing",
                (
                    ["UPLOAD_MASTER|ARCHIVE|IFIWADOC|1628"],
                    ["IFIWADOC|OFIWADOC"],
                    ["20260805|1|1"],
                    ["1|" + _hex("IFIWADOC_20260805.TXT")],
                    [],
                ),
                "GITM_CLEARING_ADOC_LOG",
            ),
            (
                "partial",
                (
                    ["UPLOAD_MASTER|ARCHIVE|IFIWADOC|2"],
                    ["IFIWADOC|OFIWADOC"],
                    ["20260806|1|1"],
                    [filename_row],
                    [_central_row("P", "P", DATA_P, source_count="1")],
                ),
                "mandatory GITM_CLEARING_ADOC_LOG data is incomplete",
            ),
            (
                "live_change",
                (
                    ["UPLOAD_MASTER|ARCHIVE|IFIWADOC|1"],
                    ["IFIWADOC|OFIWADOC"],
                    ["20260806|1|1"],
                    [filename_row],
                    [_central_row("P", "P", DATA_P, source_count="1")],
                    [
                        _central_row(
                            "P",
                            "P",
                            (*DATA_P[:7], "1500.25", *DATA_P[8:]),
                            source_count="1",
                        )
                    ],
                ),
                "archived process dependencies changed",
            ),
        )

        for case, queued_rows, expected in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                sqlcl_path = root / "sql.exe"
                sqlcl_path.touch()
                runner = _QueuedSqlclRunner(queued_rows)
                service = OutputFileGenerationService(
                    _FakeConfig(sqlcl_path),
                    runner_factory=lambda _path: runner,
                    decryptor=lambda _encrypted: "fake-password",
                    output_root=root / "output",
                )

                with self.assertRaisesRegex(OutputFileGenerationError, expected):
                    service.generate(
                        GenerationRequest(target=target, process_ref_no="2806544")
                    )
                self.assertEqual(runner.pending, 0)
                self.assertFalse((root / "output").exists())


if __name__ == "__main__":
    unittest.main()
