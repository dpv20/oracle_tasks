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
)
from features.output_file_generation.service import (  # noqa: E402
    OutputFileGenerationError,
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
    status: str = "P",
    raw_status: str = "P",
    *,
    process_count: str = "1",
    interface_count: str = "1",
    context_count: str = "0",
    fallback_needed: str = "0",
    context_user: str = "",
    context_branch: str = "",
    reference_count: str = "1",
    data: tuple[str, ...] = ("BDY01", "CARD1", "A", "R1", "USR1", "002"),
    error_code: str = "",
    message_count: str = "0",
    message_type: str = "",
    message: str = "",
) -> str:
    if len(data) != 6:
        raise AssertionError("DCSTOUT fixtures require six data fields")
    values = (
        status,
        raw_status,
        "IFSTDCST",
        process_count,
        interface_count,
        context_count,
        fallback_needed,
        context_user,
        context_branch,
        reference_count,
        *data,
        error_code,
        message_count,
        message_type,
        message,
    )
    return "|".join(_hex(value) for value in values)


class DcstoutOutputMappingTests(unittest.TestCase):
    def test_contract_filename_header_footer_and_variable_body_shape(self) -> None:
        spec = spec_for_code("IFSTDCST")

        self.assertEqual(spec.output_code, "DCSTOUT")
        self.assertIs(spec_for_code("DCSTOUT"), spec)
        self.assertTrue(spec.timestamped_header)
        self.assertTrue(spec.footer_counts_all_upload_rows)
        self.assertTrue(spec.revalidate_external_inputs)
        self.assertEqual(
            physical_filename(spec, "20260805", "142355"),
            "DCSTOUT_20260805142355.TXT",
        )
        header = build_header(spec, "20260805", "142355")
        footer = build_footer(spec, 2, {"P": 1, "<NON_P>": 1})
        self.assertEqual(header, "HDR01;20260805142355;")
        self.assertEqual(footer, "TLR01;2;1;1;")
        validate_output_lines(
            spec,
            (
                header,
                "BDY01;CARD1;A;R1;USR1;001;;Y;",
                "BDY01;CARD2;B;R2;USR2;002;E1;Error de tarjeta;N;",
                footer,
            ),
        )

    def test_body_query_uses_source_file_log_and_only_selects(self) -> None:
        for source, upload_table, log_table in (
            (DataSourceChoice.ACTIVE, "GITU_UPLOAD_MASTER", "GITB_FILE_LOG"),
            (DataSourceChoice.ARCHIVE, "GITA_UPLOAD_MASTER", "GITA_FILE_LOG"),
        ):
            with self.subTest(source=source.value):
                script = build_body_query("2808006", "IFSTDCST", source).script
                compact = " ".join(script.lower().split())

                self.assertIn(f"from {upload_table} u", script)
                self.assertIn(f"from {log_table} l", script)
                self.assertIn("u.process_ref_no = '2808006'", script)
                self.assertIn("upper(trim(u.interface_code)) = 'IFSTDCST'", script)
                self.assertIn("case when u.fld5 is null then", compact)
                self.assertIn("case when u.fld6 is null then", compact)
                self.assertIn(
                    "case when u.fld5 is null or u.fld6 is null then '1' else '0' end",
                    compact,
                )
                self.assertIn("else trim(u.fld5) end", compact)
                self.assertIn("else trim(u.fld6) end", compact)
                self.assertNotIn("nvl(trim(u.fld5)", compact)
                self.assertNotIn("nvl(trim(u.fld6)", compact)
                self.assertIn("trim(l.user_id) is not null", compact)
                self.assertIn("trim(l.branch_code) is not null", compact)
                self.assertIn("count(distinct rawtohex", compact)
                self.assertIn("from ertb_msgs m", compact)
                self.assertIn("m.language = 'esp'", compact)
                self.assertIn("min(trim(m.type))", compact)
                self.assertIn("order by u.record_reference", compact)
                self.assertNotIn("error_param", compact)
                self.assertNotIn("fn_handoff", compact)
                self.assertNotIn("gipks_", compact)
                self.assertNotRegex(
                    compact,
                    r"\b(?:insert|update|delete|merge|commit|rollback)\b",
                )

    def test_parser_renderer_resolve_message_and_case_sensitive_status(self) -> None:
        spec = spec_for_code("IFSTDCST")
        rows = [
            _body_row(),
            _body_row(
                "<NON_P>",
                "E",
                process_count="3",
                interface_count="3",
                data=("BDY01", "CARD2", "B", "R2", "USRLOG", "001"),
                error_code="E1",
                message_count="1",
                message_type="E",
                message="Error de tarjeta",
            ),
            _body_row(
                "<NON_P>",
                "p",
                process_count="3",
                interface_count="3",
                data=("BDY01", "CARD3", "C", "R3", "", ""),
                error_code="W1",
                message_count="1",
                message_type="W",
                message="No debe salir",
            ),
        ]
        # Keep the process totals coherent for the first row too.
        rows[0] = _body_row(process_count="3", interface_count="3")

        records, counts = _parse_body_records(rows, spec)
        self.assertEqual(counts, {"P": 1, "<NON_P>": 2})
        self.assertEqual(
            _render_body_records(spec, records, {}),
            [
                "BDY01;CARD1;A;R1;USR1;002;;Y;",
                "BDY01;CARD2;B;R2;USRLOG;001;E1;Error de tarjeta;N;",
                "BDY01;CARD3;C;R3;;;W1;N;",
            ],
        )

    def test_missing_or_ambiguous_file_context_is_rejected(self) -> None:
        spec = spec_for_code("IFSTDCST")
        cases = (
            _body_row(context_count="0", fallback_needed="1"),
            _body_row(context_count="2", fallback_needed="1"),
            _body_row(context_user="", fallback_needed="1"),
            _body_row(context_branch="", fallback_needed="1"),
        )
        for row in cases:
            with self.subTest(row=row):
                with self.assertRaisesRegex(
                    OutputFileGenerationError,
                    "one coherent, non-empty USER_ID/BRANCH_CODE tuple",
                ):
                    _parse_body_records([row], spec)

        # When both raw fields were non-NULL, QA never consults GLOBAL and a
        # missing/ambiguous file-log tuple must not block the reconstruction.
        records, _counts = _parse_body_records(
            [_body_row(context_count="0", context_user="", context_branch="")],
            spec,
        )
        self.assertEqual(len(records), 1)

    def test_null_status_mixed_interface_and_reference_collision_are_rejected(self) -> None:
        spec = spec_for_code("IFSTDCST")
        with self.assertRaisesRegex(OutputFileGenerationError, "NULL status"):
            _parse_body_records([_body_row("<NULL>", "")], spec)
        with self.assertRaisesRegex(OutputFileGenerationError, "mixed interface"):
            _parse_body_records(
                [_body_row(process_count="2", interface_count="1")],
                spec,
            )
        with self.assertRaisesRegex(OutputFileGenerationError, "not unique"):
            _parse_body_records([_body_row(reference_count="2")], spec)
        with self.assertRaisesRegex(OutputFileGenerationError, "no reconstructible"):
            _parse_body_records([], spec)

    def test_description_is_omitted_unless_lookup_is_exactly_one_type_e(self) -> None:
        spec = spec_for_code("IFSTDCST")
        for count, message_type in (("0", ""), ("2", "E"), ("1", "O")):
            with self.subTest(count=count, message_type=message_type):
                records, _counts = _parse_body_records(
                    [
                        _body_row(
                            "<NON_P>",
                            "E",
                            error_code="E1",
                            message_count=count,
                            message_type=message_type,
                            message="No debe salir",
                        )
                    ],
                    spec,
                )
                self.assertEqual(
                    _render_body_records(spec, records, {}),
                    ["BDY01;CARD1;A;R1;USR1;002;E1;N;"],
                )

    def test_footer_counts_all_process_rows_and_exposes_null_status(self) -> None:
        for source, table in (
            (DataSourceChoice.ACTIVE, "GITU_UPLOAD_MASTER"),
            (DataSourceChoice.ARCHIVE, "GITA_UPLOAD_MASTER"),
        ):
            with self.subTest(source=source.value):
                script = build_footer_status_query(
                    "2808006",
                    "IFSTDCST",
                    source,
                ).script
                compact = " ".join(script.lower().split())
                self.assertIn(f"from {table}", script)
                self.assertIn("status = 'p'", compact)
                self.assertIn("status <> 'p'", compact)
                self.assertIn("status is null", compact)
                self.assertIn("'<non_p>|'", compact)
                self.assertIn("'<null>|'", compact)
                self.assertNotIn("interface_code", compact)

    def test_archive_generation_revalidates_file_context_and_ertb_lookup(self) -> None:
        body_row = _body_row(
            context_count="1",
            fallback_needed="1",
            context_user="USRLOG",
            context_branch="001",
            data=("BDY01", "CARD1", "A", "R1", "USRLOG", "001"),
        )
        footer_rows = ["<NON_P>|0", "<NULL>|0", "P|1"]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sqlcl_path = root / "sql.exe"
            sqlcl_path.touch()
            config = _FakeConfig(sqlcl_path)
            runner = _QueuedSqlclRunner(
                (
                    ["UPLOAD_MASTER|ARCHIVE|IFSTDCST|1"],
                    ["IFSTDCST|DCSTOUT"],
                    ["20260805|1|1"],
                    ["142355"],
                    [body_row],
                    footer_rows,
                    # External file-log and ERTB inputs are embedded in this
                    # second body SELECT even for an archived upload source.
                    [body_row],
                    footer_rows,
                    ["20260805|1|1"],
                    ["1|1|1"],
                )
            )
            service = OutputFileGenerationService(
                config,
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
                GenerationRequest(target=target, process_ref_no="2808006")
            )

            self.assertIs(result.source, DataSourceChoice.ARCHIVE)
            self.assertEqual(result.lines[0], "HDR01;20260805142355;")
            self.assertEqual(result.lines[-1], "TLR01;1;1;0;")
            self.assertEqual(result.output_path.name, "DCSTOUT_20260805142355.TXT")
            body_scripts = [
                script
                for script in runner.scripts
                if "from GITA_UPLOAD_MASTER u" in script
                and "from GITA_FILE_LOG l" in script
                and "from ERTB_MSGS m" in script
            ]
            self.assertEqual(len(body_scripts), 2)
            self.assertEqual(runner.pending, 0)


if __name__ == "__main__":
    unittest.main()
