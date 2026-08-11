from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


SRC_DIR = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_DIR))

from features.output_file_generation.formats import (  # noqa: E402
    build_footer,
    build_header,
    physical_filename,
    spec_for_code,
    validate_output_lines,
)
from features.output_file_generation.models import DataSourceChoice  # noqa: E402
from features.output_file_generation import (  # noqa: E402
    GenerationRequest,
    OracleTarget,
    OutputFileGenerationService,
)
from features.output_file_generation.queries import (  # noqa: E402
    build_body_query,
    build_chisalca_teller_query,
    build_error_message_details_query,
    build_footer_status_query,
    build_input_physical_filename_query,
    build_interface_last_run_date_query,
    build_process_contract_query,
)
from features.output_file_generation.service import (  # noqa: E402
    OutputFileGenerationError,
    _collect_error_codes,
    _compose_chisalca_body_rows,
    _parse_chisalca_upload_rows,
    _parse_body_records,
    _parse_error_message_details,
    _parse_input_physical_filename,
    _parse_status_counts,
    _render_body_records,
)
from tests.test_output_file_generation import (  # noqa: E402
    _FakeConfig,
    _QueuedSqlclRunner,
)


def _hex(value: str) -> str:
    return value.encode("utf-8").hex().upper()


def _adapter_row(
    status: str,
    fields: tuple[str, ...],
    *,
    raw_status: str | None = None,
) -> str:
    return "|".join(
        _hex(value)
        for value in (status, raw_status if raw_status is not None else status, *fields)
    )


def _chisalca_upload_row(
    status: str,
    xref: str,
    *,
    account: str = "",
    branch: str = "",
    amount: str = "",
    transaction_date: str = "",
    currency: str = "",
    error_code: str = "",
    error_param: str = "",
    raw_status: str | None = None,
) -> str:
    return "|".join(
        _hex(value)
        for value in (
            status,
            raw_status if raw_status is not None else status,
            xref,
            account,
            branch,
            amount,
            transaction_date,
            currency,
            error_code,
            error_param,
        )
    )


def _chisalca_teller_row(
    xref: str,
    transaction_reference: str,
    account: str,
    branch: str,
    amount: str,
    transaction_date: str,
    currency: str,
    *,
    count: str = "1",
) -> str:
    return "|".join(
        _hex(value)
        for value in (
            xref,
            count,
            transaction_reference,
            xref,
            account,
            branch,
            amount,
            transaction_date,
            currency,
        )
    )


CHISALOU_2744251_EML_BODY = (
    "BDY;0010286260970001;021202604071000;030010340484;001;716416541;"
    "20221026;CLP;Y;;;",
    "BDY;0016664260970001;016202604071000;039956271801;001;47553419334;"
    "20221026;CLP;Y;;;",
    "BDY;0016669260970001;015202604071000;030010340484;001;2016358897;"
    "20221026;CLP;Y;;;",
    "BDY;00166702609700WH;020202604071000;039956271801;001;98093757731;"
    "20221026;CLP;Y;;;",
    "BDY;0016667260970001;017202604071000;030010340484;001;5558920335;"
    "20221026;CLP;Y;;;",
)


class ChisalouOutputMappingTests(unittest.TestCase):
    def test_contract_filename_header_footer_and_validator(self) -> None:
        spec = spec_for_code("CHISALCA")

        self.assertEqual(spec.output_code, "CHISALOU")
        self.assertIs(spec_for_code("CHISALOU"), spec)
        self.assertTrue(spec.header_uses_input_filename)
        self.assertTrue(spec.header_uses_last_run_date)
        self.assertTrue(spec.footer_counts_all_upload_rows)
        self.assertTrue(spec.revalidate_external_inputs)
        self.assertEqual(
            physical_filename(spec, "20260805", "142355"),
            "CHISALOU_20260805.TXT",
        )
        header = build_header(
            spec,
            "20260805",
            "142355",
            "CHISALCA.TXT",
            "20260807",
        )
        footer = build_footer(spec, 3, {"P": 2, "E": 1, "U": 1})

        self.assertEqual(header, "HDR;CHISALCA.TXT;20260807142355;")
        self.assertEqual(footer, "FTR;2;2;")
        validate_output_lines(
            spec,
            (
                header,
                "BDY;TRN1;X1;ACC1;001;10.5;20260805;CLP;Y;;;",
                "BDY;;X2;ACC2;002;20;;CLP;N;AC-ERR;Mensaje~;",
                "BDY;;X3;ACC3;003;30;20260805;CLP;"
                "NGI-INT214*Failed to process data*;",
                footer,
            ),
        )

    def test_active_and_archive_queries_are_select_only_and_contract_exact(self) -> None:
        for source, table in (
            (DataSourceChoice.ACTIVE, "GITU_UPLOAD_MASTER"),
            (DataSourceChoice.ARCHIVE, "GITA_UPLOAD_MASTER"),
        ):
            with self.subTest(source=source.value):
                script = build_body_query("2808006", "CHISALCA", source).script
                compact = " ".join(script.lower().split())

                if source is DataSourceChoice.ARCHIVE:
                    self.assertIn(f"join {table} u", script)
                    self.assertIn("from GITA_FILE_LOG l", script)
                    self.assertIn("leading(k) use_nl(u)", script)
                    self.assertIn("index(u INX01_GITA_UPLOAD_MASTER)", script)
                    self.assertIn("u.branch_code is null", script)
                    self.assertIn(
                        "replace(trim(l.file_name), '.', '_')", script
                    )
                    self.assertIn("u.file_name = k.upload_file_name", script)
                    self.assertIn("u.archival_date = k.archival_date", script)
                    self.assertIn("u.interface_code = 'CHISALCA'", script)
                else:
                    self.assertIn(f"from {table} u", script)
                    self.assertIn("u.interface_code = 'CHISALCA'", script)
                    self.assertNotIn("upper(trim(u.interface_code))", script)
                self.assertIn("u.process_ref_no = '2808006'", script)
                self.assertIn("u.target_table = 'DETB_UPLOAD_RTL_TELLER'", script)
                self.assertNotIn("detb_rtl_teller", compact)
                self.assertIn("trim(u.fld22)", compact)
                self.assertIn("trim(u.fld6)", compact)
                self.assertIn("trim(u.fld199)", compact)
                self.assertIn("rawtohex(utl_i18n.string_to_raw", compact)
                self.assertIn("order by u.record_reference", compact)
                self.assertNotIn("fn_handoff", compact)
                self.assertNotIn("gipks_", compact)
                self.assertNotRegex(
                    compact,
                    r"\b(?:insert|update|delete|merge|commit|rollback)\b",
                )

        xref = "X'1"
        teller_script = build_chisalca_teller_query([xref, "X2", xref]).script
        compact = " ".join(teller_script.lower().split())
        self.assertIn("from detb_rtl_teller t", compact)
        self.assertIn("where t.xref in", compact)
        self.assertIn("index(t ind01_detbs_rtl_teller)", compact)
        self.assertIn("group by trim(t.xref)", compact)
        self.assertIn("to_char(count(*))", compact)
        self.assertIn(_hex(xref), teller_script)
        self.assertNotIn(xref, teller_script)
        self.assertEqual(teller_script.count("hextoraw("), 2)
        self.assertNotRegex(
            compact,
            r"\b(?:insert|update|delete|merge|commit|rollback)\b",
        )
        with self.assertRaises(ValueError):
            build_chisalca_teller_query([])

    def test_header_and_footer_queries_preserve_required_source_semantics(self) -> None:
        last_run = build_interface_last_run_date_query("CHISALCA").script
        self.assertIn("GITM_INTERFACE_DEFINITION", last_run)
        self.assertIn("GITM_FILE_NAMES", last_run)
        self.assertIn("a.interface_code = b.interface_code", last_run)
        self.assertIn("max(a.last_run_date)", last_run)
        self.assertIn("count(*)", last_run)

        active_filename = build_input_physical_filename_query(
            "2808006",
            "CHISALCA",
            DataSourceChoice.ACTIVE,
        ).script
        self.assertIn("from GITB_FILE_MASTER", active_filename)
        self.assertIn("GITM_FILE_NAMES", active_filename)
        self.assertIn("m.file_name = h.file_name", active_filename)
        self.assertIn("m.upload_status = 'P'", active_filename)
        self.assertIn("m.process_code = 'FP'", active_filename)
        self.assertNotIn("GITA_UPLOAD_MASTER", active_filename)

        archive_filename = build_input_physical_filename_query(
            "2744251",
            "CHISALCA",
            DataSourceChoice.ARCHIVE,
        ).script
        compact_filename = " ".join(archive_filename.lower().split())
        self.assertIn("join gita_upload_master u", compact_filename)
        self.assertIn("from gita_file_log", compact_filename)
        self.assertIn("from gitb_file_master", compact_filename)
        self.assertIn("leading(k) use_nl(u)", compact_filename)
        self.assertIn("index(u inx01_gita_upload_master)", compact_filename)
        self.assertIn("u.file_name = k.upload_file_name", compact_filename)
        self.assertIn("count(*) upload_row_count", compact_filename)
        self.assertIn(
            "count(trim(u.phy_file_name)) named_upload_row_count",
            compact_filename,
        )
        self.assertIn(
            "count(distinct trim(u.phy_file_name)) distinct_name_count",
            compact_filename,
        )
        self.assertIn(
            "u.named_upload_row_count = u.upload_row_count",
            compact_filename,
        )
        self.assertIn("f.phy_file_name = u.phy_file_name", compact_filename)
        self.assertIn("m.phy_file_name = u.phy_file_name", compact_filename)
        self.assertNotRegex(
            compact_filename,
            r"\b(?:insert|update|delete|merge|commit|rollback)\b",
        )

        for source in (DataSourceChoice.ACTIVE, DataSourceChoice.ARCHIVE):
            with self.subTest(source=source.value):
                footer = build_footer_status_query(
                    "2808006",
                    "CHISALCA",
                    source,
                ).script
                self.assertIn(
                    "group by nvl(trim(status), '<NULL>')",
                    " ".join(footer.split()),
                )
                self.assertNotIn("upper(trim(status))", footer.lower())
                self.assertNotIn("target_table", footer.lower())
                if source is DataSourceChoice.ARCHIVE:
                    compact_footer = " ".join(footer.lower().split())
                    self.assertIn("from gita_file_log l", compact_footer)
                    self.assertIn("join gita_upload_master u", compact_footer)
                    self.assertIn(
                        "index(u inx01_gita_upload_master)", compact_footer
                    )

        contract = build_process_contract_query(
            "2744251",
            "CHISALCA",
            "CHISALOU",
            DataSourceChoice.ARCHIVE,
        ).script
        compact_contract = " ".join(contract.lower().split())
        self.assertIn("from gita_file_log l", compact_contract)
        self.assertIn("join gita_upload_master u", compact_contract)
        self.assertIn("index(u inx01_gita_upload_master)", compact_contract)
        self.assertIn(
            "u.target_table = 'detb_upload_rtl_teller'",
            compact_contract,
        )

        active_contract = build_process_contract_query(
            "2808006",
            "CHISALCA",
            "CHISALOU",
            DataSourceChoice.ACTIVE,
        ).script
        self.assertIn(
            "target_table = 'detb_upload_rtl_teller'",
            " ".join(active_contract.lower().split()),
        )

    def test_2744251_eml_golden_accepts_retained_archive_filename(self) -> None:
        spec = spec_for_code("CHISALCA")
        # The accepted EML attachment has the same five body rows and footer,
        # but its manually generated header contains a contaminated ...08 input
        # name.  Archive evidence is unanimous on ...07, so the safe fixture
        # deliberately normalizes only that header field.
        header = build_header(
            spec,
            "20260407",
            "120149",
            "CHISALCA_MOC_SAT_20260407.TXT",
            "20260408",
        )
        lines = (
            header,
            *CHISALOU_2744251_EML_BODY,
            "FTR;5;0;",
        )

        self.assertEqual(
            lines[0],
            "HDR;CHISALCA_MOC_SAT_20260407.TXT;20260408120149;",
        )
        self.assertEqual(lines[-1], "FTR;5;0;")
        self.assertEqual(len(lines), 7)
        validate_output_lines(spec, lines)

    def test_2744251_service_generates_the_five_eml_body_rows_and_footer(self) -> None:
        eml_fields = [line.split(";")[1:8] for line in CHISALOU_2744251_EML_BODY]
        upload_rows = [
            _chisalca_upload_row("P", fields[1]) for fields in eml_fields
        ]
        teller_rows = [
            _chisalca_teller_row(
                fields[1],
                fields[0],
                fields[2],
                fields[3],
                fields[4],
                fields[5],
                fields[6],
            )
            for fields in eml_fields
        ]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sqlcl_path = root / "sql.exe"
            sqlcl_path.touch()
            config = _FakeConfig(sqlcl_path)
            runner = _QueuedSqlclRunner(
                (
                    ["UPLOAD_MASTER|ARCHIVE|CHISALCA|5"],
                    ["CHISALCA|CHISALOU"],
                    ["20260407|1|1"],
                    ["20260408|1"],
                    ["120149"],
                    ["1|" + _hex("CHISALCA_MOC_SAT_20260407.TXT")],
                    upload_rows,
                    teller_rows,
                    ["P|5"],
                    upload_rows,
                    teller_rows,
                    ["20260408|1"],
                    ["P|5"],
                    ["20260407|1|1"],
                    ["1|" + _hex("CHISALCA_MOC_SAT_20260407.TXT")],
                    ["5|1|1"],
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
                GenerationRequest(target=target, process_ref_no="2744251")
            )

            self.assertEqual(
                result.lines,
                (
                    "HDR;CHISALCA_MOC_SAT_20260407.TXT;20260408120149;",
                    *CHISALOU_2744251_EML_BODY,
                    "FTR;5;0;",
                ),
            )
            self.assertEqual(result.body_count, 5)
            self.assertEqual(result.status_counts, {"P": 5})
            self.assertEqual(result.output_path.name, "CHISALOU_20260407.TXT")
            self.assertEqual(runner.pending, 0)

    def test_archive_index_keys_cannot_return_a_partial_chisalca_body(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sqlcl_path = root / "sql.exe"
            sqlcl_path.touch()
            runner = _QueuedSqlclRunner(
                (
                    ["UPLOAD_MASTER|ARCHIVE|CHISALCA|2"],
                    ["CHISALCA|CHISALOU"],
                    ["20260407|1|1"],
                    ["20260408|1"],
                    ["120149"],
                    ["1|" + _hex("CHISALCA_MOC_SAT_20260407.TXT")],
                    [_chisalca_upload_row("P", "X1")],
                    [
                        _chisalca_teller_row(
                            "X1", "TRN1", "ACC1", "001", "10", "20260407", "CLP"
                        )
                    ],
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

            with self.assertRaisesRegex(
                OutputFileGenerationError,
                "body rows do not cover all detected upload rows",
            ):
                service.generate(
                    GenerationRequest(target=target, process_ref_no="2744251")
                )
            self.assertEqual(runner.pending, 0)

    def test_filename_resolution_requires_exactly_one_physical_name(self) -> None:
        self.assertEqual(
            _parse_input_physical_filename(["1|" + _hex("CHISALCA.TXT")]),
            "CHISALCA.TXT",
        )
        for row in ("0|", "2|" + _hex("CHISALCA.TXT")):
            with self.subTest(row=row):
                with self.assertRaises(OutputFileGenerationError):
                    _parse_input_physical_filename([row])

    def test_processed_xref_must_resolve_exactly_one_teller_row(self) -> None:
        spec = spec_for_code("CHISALCA")
        for count in ("0", "2"):
            with self.subTest(count=count):
                row = _adapter_row(
                    "P",
                    (
                        count,
                        "TRN1",
                        "X1",
                        "ACC1",
                        "001",
                        "10",
                        "20260805",
                        "CLP",
                        "",
                        "",
                    ),
                )
                with self.assertRaisesRegex(
                    OutputFileGenerationError,
                    "did not resolve exactly one DETB_RTL_TELLER row",
                ):
                    _parse_body_records([row], spec)

        parsed_uploads = _parse_chisalca_upload_rows(
            [_chisalca_upload_row("P", "MISSING")]
        )
        composed = _compose_chisalca_body_rows(parsed_uploads, [])
        with self.assertRaisesRegex(
            OutputFileGenerationError,
            "did not resolve exactly one DETB_RTL_TELLER row",
        ):
            _parse_body_records(composed, spec)

    def test_lowercase_status_is_rejected_in_body_and_wider_footer(self) -> None:
        spec = spec_for_code("CHISALCA")
        row = _adapter_row(
            "P",
            (
                "1",
                "TRN1",
                "X1",
                "ACC1",
                "001",
                "10",
                "20260805",
                "CLP",
                "",
                "",
            ),
            raw_status="p",
        )
        with self.assertRaisesRegex(
            OutputFileGenerationError,
            "lowercase status.*case-sensitively",
        ):
            _parse_body_records([row], spec)
        with self.assertRaisesRegex(OutputFileGenerationError, "lowercase status"):
            _parse_status_counts(["p|1"], reject_lowercase=True)

    def test_renderer_filters_type_o_formats_messages_and_preserves_u_bug(self) -> None:
        spec = spec_for_code("CHISALCA")
        rows = [
            _adapter_row(
                "P",
                (
                    "1",
                    "TRN1",
                    "X1",
                    "ACC1",
                    "001",
                    "10.5",
                    "20260805",
                    "CLP",
                    "",
                    "",
                ),
            ),
            _adapter_row(
                "E",
                (
                    "0",
                    "",
                    "X2",
                    "ACC2",
                    "002",
                    "20",
                    "20260805",
                    "CLP",
                    "O-INFO;AC-ERR;EOPL",
                    "IGNORED~;A~B~;",
                ),
            ),
            _adapter_row(
                "U",
                (
                    "0",
                    "",
                    "X3",
                    "ACC3",
                    "003",
                    "30",
                    "20260805",
                    "CLP",
                    "IGNORED",
                    "IGNORED",
                ),
            ),
        ]

        records, counts = _parse_body_records(rows, spec)
        self.assertEqual(counts, {"P": 1, "E": 1, "U": 1})
        self.assertEqual(_collect_error_codes(spec, records), {"O-INFO", "AC-ERR"})
        bodies = _render_body_records(
            spec,
            records,
            {"O-INFO": "Informativo", "AC-ERR": "Cuenta $1 / $2!"},
            {"O-INFO": "O", "AC-ERR": "E"},
        )
        self.assertEqual(
            bodies,
            [
                "BDY;TRN1;X1;ACC1;001;10.5;20260805;CLP;Y;;;",
                "BDY;;X2;ACC2;002;20;20260805;CLP;N;AC-ERR;Cuenta A / B~;",
                "BDY;;X3;ACC3;003;30;20260805;CLP;"
                "NGI-INT214*Failed to process data*;",
            ],
        )

    def test_error_metadata_query_and_parser_include_type(self) -> None:
        query = build_error_message_details_query(["AC-ERR'", "O-INFO"])
        compact = " ".join(query.script.lower().split())
        self.assertNotIn("AC-ERR'", query.script)
        self.assertIn(_hex("AC-ERR'"), query.script)
        self.assertIn("ertb_msgs", compact)
        self.assertIn("trim(type)", compact)
        self.assertIn("language = 'esp'", compact)
        self.assertNotRegex(
            compact,
            r"\b(?:insert|update|delete|merge|commit|rollback)\b",
        )

        messages, types = _parse_error_message_details(
            [
                _hex("AC-ERR")
                + "|1|"
                + _hex("E")
                + "|"
                + _hex("Cuenta $1!"),
                _hex("O-INFO") + "|1|" + _hex("O") + "|" + _hex("Info"),
            ]
        )
        self.assertEqual(messages, {"AC-ERR": "Cuenta $1!", "O-INFO": "Info"})
        self.assertEqual(types, {"AC-ERR": "E", "O-INFO": "O"})

        with self.assertRaisesRegex(OutputFileGenerationError, "ambiguous metadata"):
            _parse_error_message_details(
                [_hex("AC-ERR") + "|2|" + _hex("E") + "|" + _hex("x")]
            )

    def test_archive_generation_revalidates_live_teller_and_header_inputs(self) -> None:
        upload_row = _chisalca_upload_row("P", "X1")
        teller_row = _chisalca_teller_row(
            "X1",
            "TRN1",
            "ACC1",
            "001",
            "10",
            "20260805",
            "CLP",
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sqlcl_path = root / "sql.exe"
            sqlcl_path.touch()
            config = _FakeConfig(sqlcl_path)
            runner = _QueuedSqlclRunner(
                (
                    ["UPLOAD_MASTER|ARCHIVE|CHISALCA|1"],
                    ["CHISALCA|CHISALOU"],
                    ["20260805|1|1"],
                    ["20260807|1"],
                    ["142355"],
                    ["1|" + _hex("CHISALCA.TXT")],
                    [upload_row],
                    [teller_row],
                    ["P|1"],
                    # CHISALOU opts into the full second read even though its
                    # upload rows came from GITA_UPLOAD_MASTER.
                    [upload_row],
                    [teller_row],
                    ["20260807|1"],
                    ["P|1"],
                    ["20260805|1|1"],
                    ["1|" + _hex("CHISALCA.TXT")],
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
            self.assertEqual(result.lines[0], "HDR;CHISALCA.TXT;20260807142355;")
            self.assertEqual(result.lines[-1], "FTR;1;0;")
            body_scripts = [
                script
                for script in runner.scripts
                if "_CHISALCA_BODY_" in script
                and "join GITA_UPLOAD_MASTER u" in script
                and "u.target_table = 'DETB_UPLOAD_RTL_TELLER'" in script
            ]
            self.assertEqual(len(body_scripts), 2)
            teller_scripts = [
                script
                for script in runner.scripts
                if "from DETB_RTL_TELLER t" in script
            ]
            self.assertEqual(len(teller_scripts), 2)
            self.assertEqual(
                sum("max(a.last_run_date)" in script.lower() for script in runner.scripts),
                2,
            )
            self.assertEqual(
                sum(
                    "upload_source as" in script.lower()
                    and "join GITA_UPLOAD_MASTER u" in script
                    for script in runner.scripts
                ),
                2,
            )
            self.assertEqual(runner.pending, 0)


if __name__ == "__main__":
    unittest.main()
