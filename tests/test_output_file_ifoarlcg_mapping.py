from __future__ import annotations

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
    physical_filename,
    spec_for_code,
    validate_output_lines,
)
from features.output_file_generation.models import DataSourceChoice  # noqa: E402
from features.output_file_generation.queries import (  # noqa: E402
    build_body_query,
    build_footer_status_query,
    build_input_physical_filename_query,
    build_interface_last_run_date_query,
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


def _hex(value: str) -> str:
    return value.encode("utf-8").hex().upper()


def _body_row(
    raw_status: str,
    data: tuple[str, ...],
    error_codes: str = "",
    *,
    source_count: str = "1",
    match_count: str = "1",
    xref_count: str = "1",
) -> str:
    if len(data) != 11:
        raise AssertionError("IFOARLCG fixtures need eleven source fields")
    output_status = {"SUCC": "P", "ERR": "E"}.get(raw_status, "<INVALID>")
    return "|".join(
        _hex(value)
        for value in (
            output_status,
            raw_status,
            source_count,
            match_count,
            xref_count,
            *data,
            error_codes,
        )
    )


class IfoarlcgOutputMappingTests(unittest.TestCase):
    def test_contract_filename_header_footer_and_validator(self) -> None:
        spec = spec_for_code("IFEARLCG")

        self.assertEqual(spec.output_code, "IFOARLCG")
        self.assertIs(spec_for_code("IFOARLCG"), spec)
        self.assertTrue(spec.header_uses_input_filename)
        self.assertTrue(spec.header_uses_last_run_date)
        self.assertTrue(spec.footer_counts_all_upload_rows)
        self.assertTrue(spec.revalidate_external_inputs)
        self.assertEqual(
            physical_filename(spec, "20260807", "142355"),
            "IFOARLCG_20260807142355.TXT",
        )

        header = build_header(
            spec,
            "20260807",
            "142355",
            "IFEARLCG_20260807.TXT",
            "20260806",
        )
        footer = build_footer(spec, 2, {"P": 2, "<NON_P>": 0})
        self.assertEqual(header, "HDR;IFEARLCG_20260807.TXT;20260806142355;")
        self.assertEqual(footer, "FTR;2;0;")

        validate_output_lines(
            spec,
            (
                header,
                "FB;X1;FCC1;CHK1;001;ACC1;2800;CHK2;SEC;BANK;REM;DOC;P;",
                "FB;X2;;;;;3300;CHK3;;BANK;REM;DOC;E;AI-UNC03;No existe~;",
                footer,
            ),
        )

    def test_body_and_metadata_queries_are_select_only_and_source_exact(self) -> None:
        last_run = build_interface_last_run_date_query("IFEARLCG").script
        self.assertIn("GITM_INTERFACE_DEFINITION", last_run)
        self.assertIn("max(last_run_date)", last_run)

        for source, upload_table, filename_table in (
            (DataSourceChoice.ACTIVE, "GITU_UPLOAD_MASTER", "GITB_FILE_MASTER"),
            (DataSourceChoice.ARCHIVE, "GITA_UPLOAD_MASTER", "GITA_FILE_LOG"),
        ):
            with self.subTest(source=source.value):
                body = build_body_query(
                    "2808002",
                    "IFEARLCG",
                    source,
                    "20260807",
                ).script
                compact = " ".join(body.lower().split())
                self.assertIn("from iftb_clearing_upload source_rows", compact)
                self.assertIn("from iftb_clearing_upload_c child_rows", compact)
                self.assertIn("source_rows.scode = 'ifearlcg'", compact)
                self.assertIn("source_rows.unit_id = 2808002", compact)
                self.assertIn(
                    "source_rows.upload_date = to_date('20260807', 'yyyymmdd')",
                    compact,
                )
                self.assertIn("b.scode = a.scode", compact)
                self.assertIn("b.xref = a.xref", compact)
                self.assertIn("b.entry_no = a.entry_no", compact)
                self.assertIn("order by a.xref", compact)
                self.assertIn("to_char(a.instramt, 'tm9'", compact)
                self.assertNotIn("max(process_ref_no)", compact)
                self.assertNotIn("fn_handoff", compact)
                self.assertNotIn("gipks_", compact)
                self.assertNotRegex(
                    compact,
                    r"\b(?:insert|update|delete|merge|commit|rollback)\b",
                )

                footer = build_footer_status_query(
                    "2808002",
                    "IFEARLCG",
                    source,
                ).script
                footer_compact = " ".join(footer.lower().split())
                self.assertIn(f"from {upload_table.lower()}", footer_compact)
                self.assertIn("status = 'p'", footer_compact)
                self.assertIn("status <> 'p'", footer_compact)
                self.assertIn("status is null", footer_compact)
                self.assertIn("interface_code = 'ifearlcg'", footer_compact)
                self.assertNotIn("upper(trim(status))", footer_compact)

                filename = build_input_physical_filename_query(
                    "2808002",
                    "IFEARLCG",
                    source,
                ).script
                self.assertIn(f"from {filename_table}", filename)

    def test_renderer_preserves_null_fields_and_ignores_error_params(self) -> None:
        spec = spec_for_code("IFEARLCG")
        success = (
            "X1",
            "FCC1",
            "CHK1",
            "001",
            "ACC1",
            "2800",
            "CHK2",
            "SEC",
            "BANK",
            "REM",
            "DOC",
        )
        rejected = (
            "X2",
            "",
            "",
            "",
            "",
            "3300",
            "CHK3",
            "",
            "BANK",
            "REM",
            "DOC",
        )
        records, counts = _parse_body_records(
            [
                _body_row("SUCC", success, source_count="2"),
                _body_row(
                    "ERR",
                    rejected,
                    "AI-UNC03;EOPL",
                    source_count="2",
                ),
            ],
            spec,
        )

        self.assertEqual(counts, {"P": 1, "E": 1})
        self.assertEqual(_collect_error_codes(spec, records), {"AI-UNC03"})
        self.assertEqual(
            _render_body_records(
                spec,
                records,
                {"AI-UNC03": "No existe $1!"},
            ),
            [
                "FB;" + ";".join((*success, "P")) + ";",
                "FB;"
                + ";".join((*rejected, "E"))
                + ";AI-UNC03;EOPL;No existe $1~;",
            ],
        )

    def test_join_must_be_complete_unique_and_deterministically_ordered(self) -> None:
        spec = spec_for_code("IFEARLCG")
        data = (
            "X1",
            "FCC1",
            "CHK1",
            "001",
            "ACC1",
            "2800",
            "CHK2",
            "SEC",
            "BANK",
            "REM",
            "DOC",
        )
        cases = (
            (
                _body_row("SUCC", data, match_count="0"),
                "exactly one IFTB_CLEARING_UPLOAD_C row",
            ),
            (
                _body_row("SUCC", data, match_count="2"),
                "exactly one IFTB_CLEARING_UPLOAD_C row",
            ),
            (
                _body_row("SUCC", data, source_count="2"),
                "source row count",
            ),
            (
                _body_row("SUCC", data, xref_count="2"),
                "duplicate XREF",
            ),
        )
        for row, expected in cases:
            with self.subTest(expected=expected):
                with self.assertRaisesRegex(OutputFileGenerationError, expected):
                    _parse_body_records([row], spec)

        with self.assertRaisesRegex(OutputFileGenerationError, "no reconstructible"):
            _parse_body_records([], spec)

    def test_unmapped_clearing_status_is_rejected(self) -> None:
        spec = spec_for_code("IFEARLCG")
        data = ("X1", "", "", "", "", "1", "I2", "", "B", "A", "D")
        with self.assertRaisesRegex(OutputFileGenerationError, "SUCC or ERR"):
            _parse_body_records([_body_row("succ", data)], spec)

    def test_footer_must_account_for_every_body_row(self) -> None:
        spec = spec_for_code("IFEARLCG")
        with self.assertRaisesRegex(ValueError, "match its body count"):
            build_footer(spec, 2, {"P": 1, "<NON_P>": 0})

        header = build_header(
            spec,
            "20260807",
            "142355",
            "IFEARLCG.TXT",
            "20260806",
        )
        with self.assertRaisesRegex(ValueError, "footer does not match its body"):
            validate_output_lines(
                spec,
                (
                    header,
                    "FB;X1;F;I;B;A;1;I2;S;RB;RA;D;P;",
                    "FTR;0;0;",
                ),
            )

    def test_archive_generation_revalidates_every_live_dependency(self) -> None:
        body_row = _body_row(
            "SUCC",
            ("X1", "FCC1", "CHK1", "001", "ACC1", "2800", "CHK2", "SEC", "B", "R", "D"),
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sqlcl_path = root / "sql.exe"
            sqlcl_path.touch()
            runner = _QueuedSqlclRunner(
                (
                    ["UPLOAD_MASTER|ARCHIVE|IFEARLCG|1"],
                    ["IFEARLCG|IFOARLCG"],
                    ["20260807|1|1"],
                    ["20260806|1"],
                    ["142355"],
                    ["1|" + _hex("IFEARLCG_20260807.TXT")],
                    [body_row],
                    ["<NON_P>|0", "<NULL>|0", "P|1"],
                    [body_row],
                    ["20260806|1"],
                    ["<NON_P>|0", "<NULL>|0", "P|1"],
                    ["20260807|1|1"],
                    ["1|" + _hex("IFEARLCG_20260807.TXT")],
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
                GenerationRequest(target=target, process_ref_no="2808002")
            )

            self.assertIs(result.source, DataSourceChoice.ARCHIVE)
            self.assertEqual(result.output_path.name, "IFOARLCG_20260807142355.TXT")
            self.assertEqual(
                result.lines,
                (
                    "HDR;IFEARLCG_20260807.TXT;20260806142355;",
                    "FB;X1;FCC1;CHK1;001;ACC1;2800;CHK2;SEC;B;R;D;P;",
                    "FTR;1;0;",
                ),
            )
            self.assertEqual(
                sum(
                    "from IFTB_CLEARING_UPLOAD source_rows" in script
                    for script in runner.scripts
                ),
                2,
            )
            self.assertEqual(
                sum("max(last_run_date)" in script.lower() for script in runner.scripts),
                2,
            )
            self.assertEqual(runner.pending, 0)


if __name__ == "__main__":
    unittest.main()
