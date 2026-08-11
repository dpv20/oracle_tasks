from __future__ import annotations

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
    build_oficowcg_coverage_query,
)
from features.output_file_generation.service import (  # noqa: E402
    OutputFileGenerationError,
    _collect_error_codes,
    _parse_oficowcg_coverage_metadata,
    _parse_body_records,
    _render_body_records,
    _validate_country_contract,
)


def _hex(value: str) -> str:
    return value.encode("utf-8").hex().upper()


def _colombia_row(
    status: str,
    fields: tuple[str, ...],
    txn_status: str,
    *,
    error_code: str = "",
    error_param: str = "",
) -> str:
    if len(fields) != 11:
        raise AssertionError("Colombia OFICOWCG fixtures need eleven data fields")
    return "|".join(
        _hex(value)
        for value in (status, error_code, error_param, *fields, txn_status)
    )


class ColombiaOficowcgQueryTests(unittest.TestCase):
    def test_body_query_reproduces_all_four_colombia_qa_branches(self) -> None:
        query = build_body_query(
            "5328716",
            "IFICOWCG",
            DataSourceChoice.ACTIVE,
            "20260604",
            country="colombia",
        )
        compact = " ".join(query.script.lower().split())

        # Colombia QA has the two ordinary-instrument branches plus two
        # document-type 02 branches.  The processed branches also depend on
        # IFTB_CLEARING_UPLOAD_C, unlike the Chile contract.
        self.assertGreaterEqual(compact.count(" union "), 3)
        self.assertIn("gitu_upload_master", compact)
        self.assertIn("gitm_clearing_log", compact)
        self.assertIn("iftb_clearing_upload", compact)
        self.assertIn("iftb_clearing_upload_c", compact)
        self.assertIn("record_type", compact)
        self.assertIn("document_type", compact)
        self.assertRegex(compact, r"(?:fld6|document_type)[^=]*=\s*'02'")

        # A successful clearing can be converted to REJR by the retained
        # rejection table.  The output must carry the final transaction state.
        # QA names the CSTBS synonym; PROD grants the equivalent base table.
        self.assertIn("cstb_clearing_rejection", compact)
        self.assertIn("trn_ref_no", compact)
        self.assertIn("fccref", compact)
        for txn_status in ("nopr", "succ", "erro", "rejr"):
            self.assertIn(f"'{txn_status}'", compact)
        self.assertIn("txn_status", compact)

        self.assertIn("process_ref_no = '5328716'", compact)
        self.assertRegex(compact, r"interface_code\)?\)?\s*=\s*'ificowcg'")
        self.assertEqual(compact.count("gic.interface_code = 'ificowcg'"), 4)
        self.assertNotIn("fn_handoff", compact)
        self.assertNotIn("gipks_", compact)
        self.assertNotRegex(
            compact,
            r"\b(?:insert|update|delete|merge|commit|rollback)\b",
        )

    def test_colombia_coverage_models_four_branches_and_processed_ownership(self) -> None:
        query = build_oficowcg_coverage_query(
            "5328716",
            country="colombia",
            file_date="20260604",
        )
        compact = " ".join(query.script.lower().split())

        for branch in (
            "branch_a_count",
            "branch_b_count",
            "branch_c_count",
            "branch_d_count",
        ):
            self.assertIn(branch, compact)
        self.assertIn("iftb_clearing_upload_c", compact)
        self.assertIn("cstb_clearing_rejection", compact)
        self.assertIn("upload_owner_count", compact)
        self.assertIn("count(distinct record_reference)", compact)
        self.assertGreaterEqual(
            compact.count("gic.interface_code = 'ificowcg'"),
            9,
        )
        self.assertNotRegex(
            compact,
            r"\b(?:insert|update|delete|merge|commit|rollback)\b",
        )

    def test_default_chile_query_keeps_its_two_branch_contract(self) -> None:
        query = build_body_query(
            "2806536",
            "IFICOWCG",
            DataSourceChoice.ACTIVE,
            "20260806",
        )
        compact = " ".join(query.script.lower().split())

        self.assertEqual(compact.count(" union "), 1)
        self.assertNotIn("iftb_clearing_upload_c", compact)
        self.assertNotIn("cstb_clearing_rejection", compact)
        self.assertNotIn("txn_status", compact)

    def test_colombia_archive_remains_blocked_before_query_generation(self) -> None:
        with self.assertRaisesRegex(ValueError, "archive reconstruction"):
            build_body_query(
                "5328716",
                "IFICOWCG",
                DataSourceChoice.ARCHIVE,
                "20260604",
                country="colombia",
            )

    def test_oficowcg_builders_accept_the_verified_mexico_and_peru_contracts(
        self,
    ) -> None:
        for country in ("peru", "mexico"):
            with self.subTest(country=country, builder="body"):
                body = build_body_query(
                    "5328716",
                    "IFICOWCG",
                    DataSourceChoice.ACTIVE,
                    "20260604",
                    country=country,
                )
                compact = " ".join(body.script.lower().split())
                self.assertGreaterEqual(compact.count(" union "), 3)
                self.assertIn("iftb_clearing_upload_c", compact)
                self.assertIn("cstb_clearing_rejection", compact)
                self.assertIn("txn_status", compact)
            with self.subTest(country=country, builder="coverage"):
                coverage = build_oficowcg_coverage_query(
                    "5328716",
                    country=country,
                    file_date="20260604",
                )
                compact = " ".join(coverage.script.lower().split())
                for branch in (
                    "branch_a_count",
                    "branch_b_count",
                    "branch_c_count",
                    "branch_d_count",
                ):
                    self.assertIn(branch, compact)
                self.assertIn("upload_owner_count", compact)


class ColombiaOficowcgContractTests(unittest.TestCase):
    def setUp(self) -> None:
        base_spec = spec_for_code("IFICOWCG")
        assert base_spec is not None
        self.base_spec = base_spec
        self.spec = _validate_country_contract("colombia", base_spec)

    def test_each_verified_country_gets_its_specific_oficowcg_contract(self) -> None:
        self.assertEqual(self.spec.contract, "oficowcg_colombia")
        self.assertEqual(self.spec.body_field_count, 14)
        self.assertEqual(self.spec.error_body_field_count, 16)
        self.assertIs(_validate_country_contract("chile", self.base_spec), self.base_spec)

        for country in ("peru", "mexico"):
            with self.subTest(country=country):
                regional = _validate_country_contract(country, self.base_spec)
                self.assertEqual(regional.contract, f"oficowcg_{country}")
                self.assertEqual(regional.body_field_count, 14)
                self.assertEqual(regional.error_body_field_count, 16)
                self.assertTrue(regional.revalidate_external_inputs)

        other_spec = spec_for_code("IFDOBIEL")
        assert other_spec is not None
        with self.assertRaisesRegex(OutputFileGenerationError, "Colombia"):
            _validate_country_contract("colombia", other_spec)
        with self.assertRaisesRegex(OutputFileGenerationError, "Mexico"):
            _validate_country_contract("mexico", other_spec)
        self.assertIs(_validate_country_contract("peru", other_spec), other_spec)

    def test_coverage_parser_uses_colombia_specific_cardinalities(self) -> None:
        valid = ["3|3|3|3|0|3|0|0"]
        _parse_oficowcg_coverage_metadata(
            valid,
            candidate_upload_rows=3,
            country="colombia",
            body_record_count=3,
        )

        invalid = (
            "2|2|2|2|0|2|0|0",  # detected upload count changed
            "3|2|2|2|0|3|0|0",  # NULL record reference
            "3|3|2|2|0|3|0|0",  # duplicate record reference
            "3|3|3|2|0|3|0|0",  # an upload reaches no UNION branch
            "3|3|3|3|1|3|0|0",  # processed GIC has no unique owner
            "3|3|3|3|0|2|0|0",  # UNION output differs from parsed body
            "3|3|3|3|0|3|1|0",  # ambiguous rejection lookup
            "3|3|3|3|0|3|0|1",  # ambiguous rejection error lookup
        )
        for metadata in invalid:
            with self.subTest(metadata=metadata):
                with self.assertRaises(OutputFileGenerationError):
                    _parse_oficowcg_coverage_metadata(
                        [metadata],
                        candidate_upload_rows=3,
                        country="colombia",
                        body_record_count=3,
                    )

        with self.assertRaises(OutputFileGenerationError):
            _parse_oficowcg_coverage_metadata(
                valid,
                candidate_upload_rows=3,
                country="colombia",
            )

    def test_renderer_places_txn_status_last_and_emits_rejection_errors(self) -> None:
        success = (
            "XREF-1", "10", "001", "20260604", "BANK", "ACCOUNT-1",
            "INSTR-1", "INSTR-2", "SEC", "100.25", "C",
        )
        failed = (
            "XREF-2", "20", "002", "20260604", "BANK", "ACCOUNT-2",
            "INSTR-3", "INSTR-4", "SEC", "200", "C",
        )
        rejected = (
            "XREF-3", "30", "003", "20260604", "BANK", "ACCOUNT-3",
            "INSTR-5", "INSTR-6", "SEC", "300", "C",
        )
        branch_c_error = (
            "XREF-4", "40", "004", "20260604", "BANK", "ACCOUNT-4",
            "INSTR-7", "INSTR-8", "SEC", "400", "C",
        )
        rows = [
            _colombia_row("P", success, "SUCC"),
            _colombia_row(
                "E",
                failed,
                "ERRO",
                error_code="E-ONE",
                error_param="20~",
            ),
            # Colombia QA changes a successful clearing to REJR when the FCC
            # reference appears in QA's CSTBS rejection synonym (the PROD
            # query uses CSTB_CLEARING_REJECTION). It must emit the rejection
            # error even though the clearing status remains P.
            _colombia_row(
                "P",
                rejected,
                "REJR",
                error_code="R-ONE",
                error_param="XREF-3~",
            ),
            _colombia_row(
                "E",
                branch_c_error,
                "",
                error_code="E-TWO",
                error_param="40~",
            ),
        ]

        records, status_counts = _parse_body_records(rows, self.spec)
        self.assertEqual(
            _collect_error_codes(self.spec, records),
            {"E-ONE", "R-ONE", "E-TWO"},
        )
        lines = _render_body_records(
            self.spec,
            records,
            {
                "E-ONE": "Cuenta $1 invalida!",
                "R-ONE": "Documento $1 rechazado!",
                "E-TWO": "Registro $1 invalido!",
            },
        )

        self.assertEqual(status_counts, {"P": 2, "E": 2})
        self.assertEqual(
            lines,
            [
                "BDY;" + ";".join(success) + ";P;SUCC;",
                "BDY;" + ";".join(failed) + ";E;E-ONE;Cuenta 20 invalida;ERRO;",
                "BDY;"
                + ";".join(rejected)
                + ";P;R-ONE;Documento XREF-3 rechazado;REJR;",
                "BDY;"
                + ";".join(branch_c_error)
                + ";E;E-TWO;Registro 40 invalido;;",
            ],
        )

        output = (
            build_header(
                self.spec,
                "20260604",
                input_physical_filename="IFICOWCG_20260604.TXT",
            ),
            *lines,
            build_footer(self.spec, len(lines), status_counts),
        )
        self.assertEqual(output[0], "HDR;IFICOWCG_20260604.TXT;20260604;")
        self.assertEqual(output[-1], "FTR;2;2;")
        validate_output_lines(self.spec, output)

    def test_validator_enforces_colombia_status_transaction_matrix(self) -> None:
        header = "HDR;IFICOWCG_20260604.TXT;20260604;"
        data = "X;1;001;20260604;BANK;ACCOUNT;I1;I2;SEC;100;C"

        def body(status: str, txn_status: str, *, error: bool) -> str:
            prefix = f"BDY;{data};{status};"
            if error:
                prefix += "E-ONE;Description;"
            return prefix + txn_status + ";"

        valid_bodies = (
            body("P", "SUCC", error=False),
            body("P", "REJR", error=True),
            body("E", "ERRO", error=True),
            body("E", "NOPR", error=True),
            # QA branch C leaves TXN_STATUS NULL for its error path.
            body("E", "", error=True),
            body("U", "NOPR", error=True),
        )
        for valid in valid_bodies:
            with self.subTest(valid=valid):
                status = valid.split(";")[12]
                footer = "FTR;1;0;" if status == "P" else "FTR;0;1;"
                validate_output_lines(self.spec, (header, valid, footer))

        invalid_bodies = (
            body("P", "", error=False),
            body("P", "NOPR", error=False),
            body("P", "ERRO", error=True),
            body("P", "SUCC", error=True),
            body("P", "REJR", error=False),
            body("E", "SUCC", error=True),
            body("E", "REJR", error=True),
            body("E", "ERRO", error=False),
            body("E", "NOPR", error=False),
            body("U", "", error=True),
            body("U", "SUCC", error=True),
            body("U", "ERRO", error=True),
            body("U", "REJR", error=True),
            body("U", "NOPR", error=False),
            body("P", "UNKNOWN", error=False),
        )
        for body in invalid_bodies:
            with self.subTest(body=body):
                with self.assertRaises(ValueError):
                    validate_output_lines(self.spec, (header, body, "FTR;1;0;"))


if __name__ == "__main__":
    unittest.main()
