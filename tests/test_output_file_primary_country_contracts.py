from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path


SRC_DIR = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_DIR))

from features.output_file_generation.formats import (  # noqa: E402
    build_footer,
    build_header,
    spec_for_code,
    validate_output_lines,
)
from features.output_file_generation.models import DataSourceChoice  # noqa: E402
from features.output_file_generation.queries import (  # noqa: E402
    build_body_query,
    build_input_physical_filename_query,
    build_oficowcg_coverage_query,
)
from features.output_file_generation.service import (  # noqa: E402
    OutputFileGenerationError,
    _collect_error_codes,
    _compose_chisalca_body_rows,
    _parse_body_records,
    _parse_chisalca_upload_rows,
    _render_body_records,
    _validate_country_contract,
)


def _hex(value: str) -> str:
    return value.encode("utf-8").hex().upper()


def _compact_sql(script: str) -> str:
    return " ".join(script.lower().split())


def _regional_oficowcg_row(
    status: str,
    data_fields: tuple[str, ...],
    txn_status: str,
    *,
    error_code: str = "",
    error_param: str = "",
) -> str:
    if len(data_fields) != 11:
        raise AssertionError("Regional OFICOWCG fixtures need eleven data fields")
    return "|".join(
        _hex(value)
        for value in (status, error_code, error_param, *data_fields, txn_status)
    )


def _chisalca_error_row(*, error_code: str, error_param: str) -> str:
    # Parser layout: normalized status, raw package status, teller cardinality,
    # then the seven client data values followed by ERROR and ERROR_PARAM.
    values = (
        "E",
        "E",
        "0",
        "",
        "XREF-1",
        "00123456789",
        "010",
        "125.50",
        "20260810",
        "COP",
        error_code,
        error_param,
    )
    return "|".join(_hex(value) for value in values)


def _peru_chisalca_row(
    status: str,
    *,
    fccref: str,
    xref: str,
    ccicode: str,
    error_code: str = "",
    error_param: str = "",
) -> str:
    values = (
        status,
        status,
        "1" if status == "P" else "0",
        fccref,
        xref,
        "00123456789",
        "010",
        "125.50",
        "20260810",
        "PEN",
        ccicode,
        error_code,
        error_param,
    )
    return "|".join(_hex(value) for value in values)


class PrimaryCountryGateTests(unittest.TestCase):
    def test_colombia_and_mexico_enable_only_the_verified_primary_packages(self) -> None:
        chisalca = spec_for_code("CHISALCA")
        oficowcg = spec_for_code("IFICOWCG")
        ofdobiel = spec_for_code("IFDOBIEL")
        assert chisalca is not None
        assert oficowcg is not None
        assert ofdobiel is not None

        expected_oficowcg_contract = {
            "colombia": "oficowcg_colombia",
            "mexico": "oficowcg_mexico",
        }
        for country in ("colombia", "mexico"):
            with self.subTest(country=country, interface="CHISALCA"):
                regional = _validate_country_contract(country, chisalca)
                self.assertEqual(regional.contract, "chisalou")
                self.assertEqual(regional.error_message_mode, "list_tilde_all")
                self.assertTrue(regional.revalidate_external_inputs)
            with self.subTest(country=country, interface="IFICOWCG"):
                regional = _validate_country_contract(country, oficowcg)
                self.assertEqual(
                    regional.contract,
                    expected_oficowcg_contract[country],
                )
                self.assertEqual(regional.body_field_count, 14)
                self.assertEqual(regional.error_body_field_count, 16)
                self.assertTrue(regional.revalidate_external_inputs)
            with self.subTest(country=country, interface="IFDOBIEL"):
                label = "Colombia" if country == "colombia" else "Mexico"
                with self.assertRaisesRegex(
                    OutputFileGenerationError,
                    rf"(?is)(?:GIPKS_OFDOBIEL.*{label} QA|{label}.*GIPKS_OFDOBIEL.*QA)",
                ):
                    _validate_country_contract(country, ofdobiel)

    def test_peru_enables_verified_ofdobiel_and_regional_oficowcg(self) -> None:
        chisalca = spec_for_code("CHISALCA")
        ofdobiel = spec_for_code("OFDOBIEL")
        oficowcg = spec_for_code("OFICOWCG")
        assert chisalca is not None
        assert ofdobiel is not None
        assert oficowcg is not None

        peru_chisalca = _validate_country_contract("peru", chisalca)
        self.assertEqual(peru_chisalca.contract, "chisalou_peru")
        self.assertEqual(peru_chisalca.body_field_count, 12)
        self.assertEqual(peru_chisalca.error_message_mode, "list_tilde_all")

        peru_ofdobiel = _validate_country_contract("peru", ofdobiel)
        self.assertEqual(peru_ofdobiel.input_code, "IFDOBIEL")
        self.assertEqual(peru_ofdobiel.output_code, "OFDOBIEL")
        self.assertEqual(peru_ofdobiel.contract, "standard")

        peru_oficowcg = _validate_country_contract("peru", oficowcg)
        self.assertEqual(peru_oficowcg.contract, "oficowcg_peru")
        self.assertEqual(peru_oficowcg.body_field_count, 14)
        self.assertEqual(peru_oficowcg.error_body_field_count, 16)
        self.assertTrue(peru_oficowcg.revalidate_external_inputs)

    def test_other_non_chile_adapters_remain_fail_closed(self) -> None:
        unverified = spec_for_code("ACCBLOCK")
        assert unverified is not None
        for country, label in (
            ("colombia", "Colombia"),
            ("peru", "Peru"),
            ("mexico", "Mexico"),
        ):
            with self.subTest(country=country):
                with self.assertRaisesRegex(
                    OutputFileGenerationError,
                    rf"(?is)GIPKS_ACCBLKOU.*{label} QA",
                ):
                    _validate_country_contract(country, unverified)


class RegionalChisalouTests(unittest.TestCase):
    def test_colombia_and_mexico_use_raw_fld16_in_the_body_query(self) -> None:
        for country in ("colombia", "mexico"):
            with self.subTest(country=country):
                query = build_body_query(
                    "5328716",
                    "CHISALCA",
                    DataSourceChoice.ACTIVE,
                    "20260810",
                    country=country,
                )
                sql = _compact_sql(query.script)
                self.assertIn("trim(u.fld16)", sql)
                self.assertNotIn("to_date(trim(u.fld16)", sql)
                self.assertIn(
                    "u.target_table = 'detb_upload_rtl_teller'",
                    sql,
                )
                self.assertIn("u.interface_code = 'chisalca'", sql)
                self.assertNotIn("upper(trim(u.interface_code))", sql)
                self.assertNotIn("gipks_", sql)
                self.assertNotRegex(
                    sql,
                    r"\b(?:insert|update|delete|merge|commit|rollback)\b",
                )

    def test_colombia_and_mexico_do_not_drop_type_o_messages(self) -> None:
        base = spec_for_code("CHISALCA")
        assert base is not None
        encoded = _chisalca_error_row(
            error_code="OPTIONAL-1;REQUIRED-1",
            error_param="uno;dos",
        )
        messages = {
            "OPTIONAL-1": "Optional $1!",
            "REQUIRED-1": "Required $1!",
        }
        message_types = {"OPTIONAL-1": "O", "REQUIRED-1": "E"}

        chile_records, _ = _parse_body_records([encoded], base)
        chile_line = _render_body_records(
            base,
            chile_records,
            messages,
            message_types,
        )[0]
        self.assertNotIn("OPTIONAL-1", chile_line)
        self.assertNotIn("Optional uno", chile_line)
        self.assertIn("REQUIRED-1", chile_line)

        for country in ("colombia", "mexico"):
            with self.subTest(country=country):
                spec = _validate_country_contract(country, base)
                records, counts = _parse_body_records([encoded], spec)
                line = _render_body_records(
                    spec,
                    records,
                    messages,
                    message_types,
                )[0]

                self.assertEqual(counts, {"E": 1})
                self.assertIn(";N;OPTIONAL-1;REQUIRED-1;", line)
                self.assertIn("Optional uno~", line)
                self.assertIn("Required dos~", line)

                header = build_header(
                    spec,
                    "20260810",
                    database_time="134501",
                    input_physical_filename="CHISALCA_20260810.TXT",
                    database_date="20260810",
                )
                footer = build_footer(spec, 1, counts)
                validate_output_lines(spec, (header, line, footer))

    def test_peru_query_carries_raw_fld16_and_upload_ccicode(self) -> None:
        query = build_body_query(
            "5328716",
            "CHISALCA",
            DataSourceChoice.ACTIVE,
            "20260810",
            country="peru",
        )
        sql = _compact_sql(query.script)
        self.assertIn("trim(u.fld16)", sql)
        self.assertNotIn("to_date(trim(u.fld16)", sql)
        self.assertIn("trim(u.fld43)", sql)
        self.assertIn("u.target_table = 'detb_upload_rtl_teller'", sql)
        self.assertLess(sql.index("trim(u.fld7)"), sql.index("trim(u.fld43)"))
        self.assertLess(sql.index("trim(u.fld43)"), sql.index("trim(u.fld199)"))

    def test_regional_chisalou_archive_remains_fail_closed(self) -> None:
        for country in ("colombia", "mexico", "peru"):
            with self.subTest(country=country):
                with self.assertRaisesRegex(ValueError, "archive reconstruction"):
                    build_body_query(
                        "5328716",
                        "CHISALCA",
                        DataSourceChoice.ARCHIVE,
                        "20260810",
                        country=country,
                    )

    def test_peru_renderer_places_ccicode_before_the_flag_for_every_status(self) -> None:
        base = spec_for_code("CHISALCA")
        assert base is not None
        spec = _validate_country_contract("peru", base)
        rows = [
            _peru_chisalca_row(
                "P",
                fccref="FCC-P",
                xref="XREF-P",
                ccicode="CCI-P",
            ),
            _peru_chisalca_row(
                "E",
                fccref="",
                xref="XREF-E",
                ccicode="CCI-E",
                error_code="OPTIONAL-1",
                error_param="uno",
            ),
            _peru_chisalca_row(
                "U",
                fccref="",
                xref="XREF-U",
                ccicode="CCI-U",
            ),
        ]
        records, counts = _parse_body_records(rows, spec)
        lines = _render_body_records(
            spec,
            records,
            {"OPTIONAL-1": "Optional $1!"},
            {"OPTIONAL-1": "O"},
        )

        self.assertEqual(counts, {"P": 1, "E": 1, "U": 1})
        self.assertEqual(
            lines,
            [
                "BDY;FCC-P;XREF-P;00123456789;010;125.50;"
                "20260810;PEN;CCI-P;Y;;;",
                "BDY;;XREF-E;00123456789;010;125.50;"
                "20260810;PEN;CCI-E;N;OPTIONAL-1;Optional uno~;",
                "BDY;;XREF-U;00123456789;010;125.50;"
                "20260810;PEN;CCI-U;NGI-INT214*Failed to process data*;",
            ],
        )
        header = build_header(
            spec,
            "20260810",
            database_time="134501",
            input_physical_filename="CHISALCA_20260810.TXT",
            database_date="20260810",
        )
        footer = build_footer(spec, len(lines), counts)
        validate_output_lines(spec, (header, *lines, footer))

    def test_peru_raw_upload_pipeline_preserves_ccicode_on_processed_row(self) -> None:
        base = spec_for_code("CHISALCA")
        assert base is not None
        spec = _validate_country_contract("peru", base)

        # Exact projection returned by the Peru body query: normalized/raw
        # status, FLD22/6/4/8/16/7/43, then FLD199/200.
        raw_upload_values = (
            "P",
            "P",
            "XREF-P",
            "UPLOAD-ACCOUNT",
            "999",
            "999.99",
            "20260809",
            "USD",
            "CCI-FROM-FLD43",
            "",
            "",
        )
        raw_upload_row = "|".join(_hex(value) for value in raw_upload_values)
        self.assertEqual(len(raw_upload_row.split("|")), 11)

        uploads = _parse_chisalca_upload_rows(
            [raw_upload_row],
            include_ccicode=True,
        )
        self.assertEqual(len(uploads), 1)
        self.assertEqual(uploads[0].ccicode, "CCI-FROM-FLD43")

        teller_values = (
            "XREF-P",
            "1",
            "FCC-FROM-TELLER",
            "XREF-P",
            "TELLER-ACCOUNT",
            "010",
            "125.50",
            "20260810",
            "PEN",
        )
        teller_row = "|".join(_hex(value) for value in teller_values)
        composed = _compose_chisalca_body_rows(
            uploads,
            [teller_row],
            include_ccicode=True,
        )
        self.assertEqual(len(composed), 1)
        self.assertEqual(len(composed[0].split("|")), 13)

        records, counts = _parse_body_records(composed, spec)
        rendered = _render_body_records(spec, records, {})
        self.assertEqual(counts, {"P": 1})
        self.assertEqual(
            rendered,
            [
                "BDY;FCC-FROM-TELLER;XREF-P;TELLER-ACCOUNT;010;125.50;"
                "20260810;PEN;CCI-FROM-FLD43;Y;;;"
            ],
        )


class RegionalOficowcgQueryTests(unittest.TestCase):
    def test_active_filename_uses_the_trigger_file_master_contract(self) -> None:
        sql = _compact_sql(
            build_input_physical_filename_query(
                "5328716",
                "IFICOWCG",
                DataSourceChoice.ACTIVE,
            ).script
        )
        self.assertIn("gitm_interface_definition", sql)
        self.assertIn("gitm_file_names", sql)
        self.assertIn("a.interface_code = b.interface_code", sql)
        self.assertIn("m.file_name = h.file_name", sql)
        self.assertIn("m.interface_code = 'ificowcg'", sql)
        self.assertIn("m.upload_status = 'p'", sql)
        self.assertIn("m.process_code = 'fp'", sql)

    def test_all_regional_queries_model_the_four_qa_union_branches(self) -> None:
        for country in ("colombia", "mexico", "peru"):
            with self.subTest(country=country):
                body = build_body_query(
                    "5328716",
                    "IFICOWCG",
                    DataSourceChoice.ACTIVE,
                    "20260604",
                    country=country,
                )
                sql = _compact_sql(body.script)
                self.assertGreaterEqual(sql.count(" union "), 3)
                for table in (
                    "gitu_upload_master",
                    "gitm_clearing_log",
                    "iftb_clearing_upload",
                    "iftb_clearing_upload_c",
                    "cstb_clearing_rejection",
                ):
                    self.assertIn(table, sql)
                for txn_status in ("nopr", "succ", "erro", "rejr"):
                    self.assertIn(f"'{txn_status}'", sql)
                # Peru's QA package deliberately omits the GIC interface
                # predicate from its two non-processed branches. Colombia and
                # Mexico include it in all four branches.
                self.assertEqual(
                    sql.count("gic.interface_code = 'ificowcg'"),
                    2 if country == "peru" else 4,
                )
                self.assertNotIn("gipks_", sql)
                self.assertNotRegex(
                    sql,
                    r"\b(?:insert|update|delete|merge|commit|rollback)\b",
                )
                self.assertNotRegex(sql, r"\{[^}]+\}")

    def test_regional_queries_preserve_branch_c_typo_and_branch_d_null_fccref(self) -> None:
        for country in ("colombia", "mexico", "peru"):
            with self.subTest(country=country):
                sql = _compact_sql(
                    build_body_query(
                        "5328716",
                        "IFICOWCG",
                        DataSourceChoice.ACTIVE,
                        "20260604",
                        country=country,
                    ).script
                )
                self.assertIn("ifc.instrno2 = gic.entry_no", sql)
                self.assertRegex(
                    sql,
                    r"union select ifc\.xref xref, to_char\(ifc\.entry_no\) entry_no, "
                    r"cast\(null as varchar2\(4000\)\) fccref,.*?"
                    r"ifcc\.document_type = '02'",
                )

    def test_peru_preserves_its_null_safe_branch_b_join_only(self) -> None:
        expected = re.compile(
            r"\(nvl\(ifc\.instrno2,\s*'##'\)\s*=\s*"
            r"nvl\(gic\.instrno2,\s*'##'\)\s+or\s+"
            r"ifcc\.record_type\s*=\s*'t'\)"
        )
        for builder in (
            lambda country: build_body_query(
                "5328716",
                "IFICOWCG",
                DataSourceChoice.ACTIVE,
                "20260604",
                country=country,
            ),
            lambda country: build_oficowcg_coverage_query(
                "5328716",
                country=country,
                file_date="20260604",
            ),
        ):
            peru_sql = _compact_sql(builder("peru").script)
            self.assertRegex(peru_sql, expected)
            for country in ("colombia", "mexico"):
                with self.subTest(country=country):
                    regional_sql = _compact_sql(builder(country).script)
                    self.assertNotIn("nvl(ifc.instrno2, '##')", regional_sql)
                    self.assertIn(
                        "(ifc.instrno2 = gic.instrno2 or ifcc.record_type = 't')",
                        regional_sql,
                    )

        peru_coverage = _compact_sql(
            build_oficowcg_coverage_query(
                "5328716",
                country="peru",
                file_date="20260604",
            ).script
        )
        # Cursor, per-upload branch guard, and processed-row ownership guard
        # must all use Peru's null-safe branch-B predicate.
        self.assertEqual(peru_coverage.count("nvl(ifc.instrno2, '##')"), 3)

    def test_peru_preserves_missing_gic_filter_in_nonprocessed_branches(self) -> None:
        body = _compact_sql(
            build_body_query(
                "5328716",
                "IFICOWCG",
                DataSourceChoice.ACTIVE,
                "20260604",
                country="peru",
            ).script
        )
        coverage = _compact_sql(
            build_oficowcg_coverage_query(
                "5328716",
                country="peru",
                file_date="20260604",
            ).script
        )
        self.assertEqual(body.count("gic.interface_code = 'ificowcg'"), 2)
        # Coverage contains the body cursor plus its own branch/ownership
        # checks; the exact count guards against accidentally applying the
        # Colombia predicate to Peru's A/C branches.
        self.assertEqual(coverage.count("gic.interface_code = 'ificowcg'"), 5)

    def test_regional_archive_generation_remains_fail_closed(self) -> None:
        for country in ("colombia", "mexico", "peru"):
            with self.subTest(country=country):
                with self.assertRaisesRegex(ValueError, "archive reconstruction"):
                    build_body_query(
                        "5328716",
                        "IFICOWCG",
                        DataSourceChoice.ARCHIVE,
                        "20260604",
                        country=country,
                    )


class RegionalOficowcgWireContractTests(unittest.TestCase):
    def test_raw_noncanonical_upload_status_is_never_silently_normalized(self) -> None:
        base = spec_for_code("IFICOWCG")
        assert base is not None
        data = (
            "XREF-1", "10", "001", "20260604", "BANK", "ACCOUNT-1",
            "INSTR-1", "INSTR-2", "SEC", "100.25", "C",
        )
        for country in ("chile", "colombia", "mexico", "peru"):
            with self.subTest(country=country):
                spec = _validate_country_contract(country, base)
                if country == "chile":
                    row = "|".join(
                        _hex(value)
                        for value in (" e ", "ERR", "PARAM", *data)
                    )
                else:
                    row = _regional_oficowcg_row(" e ", data, "NOPR")
                with self.assertRaisesRegex(
                    OutputFileGenerationError,
                    "non-canonical status",
                ):
                    _parse_body_records([row], spec)

    def test_parser_renderer_and_validator_apply_to_all_three_countries(self) -> None:
        base = spec_for_code("IFICOWCG")
        assert base is not None
        success = (
            "XREF-1",
            "10",
            "001",
            "20260604",
            "BANK",
            "ACCOUNT-1",
            "INSTR-1",
            "INSTR-2",
            "SEC",
            "100.25",
            "C",
        )
        rejected = (
            "XREF-2",
            "20",
            "002",
            "20260604",
            "BANK",
            "ACCOUNT-2",
            "INSTR-3",
            "INSTR-4",
            "SEC",
            "200",
            "C",
        )
        rows = [
            _regional_oficowcg_row("P", success, "SUCC"),
            _regional_oficowcg_row(
                "P",
                rejected,
                "REJR",
                error_code="REJ-ONE",
                error_param="XREF-2~",
            ),
        ]

        for country in ("colombia", "mexico", "peru"):
            with self.subTest(country=country):
                spec = _validate_country_contract(country, base)
                records, counts = _parse_body_records(rows, spec)
                self.assertEqual(_collect_error_codes(spec, records), {"REJ-ONE"})
                lines = _render_body_records(
                    spec,
                    records,
                    {"REJ-ONE": "Documento $1 rechazado!"},
                )

                self.assertEqual(counts, {"P": 2})
                self.assertEqual(
                    lines,
                    [
                        "BDY;" + ";".join(success) + ";P;SUCC;",
                        "BDY;"
                        + ";".join(rejected)
                        + ";P;REJ-ONE;Documento XREF-2 rechazado;REJR;",
                    ],
                )
                output = (
                    build_header(
                        spec,
                        "20260604",
                        input_physical_filename="IFICOWCG_20260604.TXT",
                    ),
                    *lines,
                    build_footer(spec, len(lines), counts),
                )
                validate_output_lines(spec, output)

    def test_validator_rejects_invalid_transaction_status_for_every_region(self) -> None:
        base = spec_for_code("IFICOWCG")
        assert base is not None
        data = "X;1;001;20260604;BANK;ACCOUNT;I1;I2;SEC;100;C"
        invalid = f"BDY;{data};P;NOPR;"
        for country in ("colombia", "mexico", "peru"):
            with self.subTest(country=country):
                spec = _validate_country_contract(country, base)
                with self.assertRaisesRegex(ValueError, "status pair"):
                    validate_output_lines(
                        spec,
                        (
                            "HDR;IFICOWCG_20260604.TXT;20260604;",
                            invalid,
                            "FTR;1;0;",
                        ),
                    )


class PeruOfdobielContractTests(unittest.TestCase):
    def test_peru_ofdobiel_uses_the_qa_body_and_wire_contract(self) -> None:
        base = spec_for_code("IFDOBIEL")
        assert base is not None
        spec = _validate_country_contract("peru", base)
        query = build_body_query(
            "2798503",
            "IFDOBIEL",
            DataSourceChoice.ACTIVE,
            "20260731",
            country="peru",
        )
        sql = _compact_sql(query.script)
        self.assertIn("from gitu_upload_master", sql)
        self.assertIn("interface_code", sql)
        self.assertIn("'ifdobiel'", sql)
        self.assertIn("trim(fld1) = 'bdy'", sql)
        self.assertIn("order by record_reference", sql)
        self.assertNotIn("gipks_", sql)
        self.assertNotRegex(
            sql,
            r"\b(?:insert|update|delete|merge|commit|rollback)\b",
        )

        lines = (
            build_header(spec, "20260731"),
            "BDY;C;000000001;E;P;;;",
            "BDY;C;000000002;D;E;X01;Descripcion invalida;",
            build_footer(spec, 2, {"P": 1, "E": 1}),
        )
        self.assertEqual(lines[0], "HDR;OFDOBIEL.txt;20260731;")
        self.assertEqual(lines[-1], "TLR;2;")
        validate_output_lines(spec, lines)

    def test_peru_ofdobiel_archive_remains_fail_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "archive reconstruction"):
            build_body_query(
                "2798503",
                "IFDOBIEL",
                DataSourceChoice.ARCHIVE,
                "20260731",
                country="peru",
            )


if __name__ == "__main__":
    unittest.main()
