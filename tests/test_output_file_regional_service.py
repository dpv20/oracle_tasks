from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


SRC_DIR = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_DIR))

from features.output_file_generation import (  # noqa: E402
    DataSourceChoice,
    GenerationRequest,
    OracleTarget,
    OutputFileGenerationService,
)
from tests.test_output_file_generation import (  # noqa: E402
    _FakeConfig,
    _QueuedSqlclRunner,
)


def _hex(value: str) -> str:
    return value.encode("utf-8").hex().upper()


def _chisalca_upload_row(
    *,
    xref: str,
    ccicode: str | None = None,
) -> str:
    values = [
        "P",
        "P",
        xref,
        "UPLOAD-ACCOUNT",
        "999",
        "999.99",
        "20260810",
        "USD",
    ]
    if ccicode is not None:
        values.append(ccicode)
    values.extend(("", ""))
    return "|".join(_hex(value) for value in values)


def _chisalca_teller_row(
    *,
    xref: str,
    transaction_reference: str,
) -> str:
    values = (
        xref,
        "1",
        transaction_reference,
        xref,
        "TELLER-ACCOUNT",
        "010",
        "125.50",
        "20260811",
        "PEN",
    )
    return "|".join(_hex(value) for value in values)


def _regional_oficowcg_row(
    *,
    xref: str,
    txn_status: str = "SUCC",
) -> tuple[str, tuple[str, ...]]:
    data_fields = (
        xref,
        "10",
        "001",
        "20260811",
        "BANK",
        "ACCOUNT-1",
        "INSTR-1",
        "INSTR-2",
        "SEC",
        "100.25",
        "C",
    )
    encoded = "|".join(
        _hex(value)
        for value in ("P", "", "", *data_fields, txn_status)
    )
    return encoded, data_fields


class RegionalPrimaryServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.sqlcl_path = self.root / "sql.exe"
        self.sqlcl_path.touch()
        self.config = _FakeConfig(self.sqlcl_path)

    def _target(self, country: str) -> OracleTarget:
        aliases = {
            "colombia": "BFCO_POCISANTIAGO",
            "peru": "PERU_OCI_PROD",
            "mexico": "MX_PROD_OCI",
        }
        alias = aliases[country]
        database_key = f"{country}-prod-db"
        credential_key = "shared-prod"
        self.config.credentials[country] = {
            database_key.upper(): {
                credential_key.upper(): {
                    **self.config.credential,
                    "tns": alias,
                }
            }
        }
        return OracleTarget(
            country=country,
            database_key=database_key,
            credential_key=credential_key,
            tns=alias,
            label=f"{country.title()} PROD",
        )

    def _service(
        self,
        responses: tuple[list[str], ...],
    ) -> tuple[OutputFileGenerationService, _QueuedSqlclRunner]:
        runner = _QueuedSqlclRunner(responses)
        service = OutputFileGenerationService(
            self.config,
            runner_factory=lambda _path: runner,
            decryptor=lambda _encrypted: "fake-password",
            output_root=self.root / "output",
        )
        return service, runner

    def _generate_chisalou(
        self,
        country: str,
        *,
        ccicode: str | None,
        footer_rows: list[str] | None = None,
    ):
        xref = f"XREF-{country.upper()}"
        transaction_reference = f"FCC-{country.upper()}"
        upload_row = _chisalca_upload_row(xref=xref, ccicode=ccicode)
        teller_row = _chisalca_teller_row(
            xref=xref,
            transaction_reference=transaction_reference,
        )
        input_filename = f"CHISALCA_{country.upper()}_20260811.TXT"
        footer_rows = footer_rows or ["P|1"]
        service, runner = self._service(
            (
                ["UPLOAD_MASTER|ACTIVE|CHISALCA|1"],
                ["CHISALCA|CHISALOU"],
                ["20260811|1|1"],
                ["20260811|1"],
                ["091530"],
                ["1|" + _hex(input_filename)],
                [upload_row],
                [teller_row],
                footer_rows,
                [upload_row],
                [teller_row],
                ["20260811|1"],
                footer_rows,
                ["20260811|1|1"],
                ["1|" + _hex(input_filename)],
                ["1|1|1"],
            )
        )
        result = service.generate(
            GenerationRequest(
                target=self._target(country),
                process_ref_no="872322",
            )
        )
        return result, runner, input_filename, transaction_reference, xref

    def test_chisalou_mixed_target_tables_keep_body_scope_and_package_footer(self) -> None:
        result, runner, input_filename, transaction_reference, xref = (
            self._generate_chisalou(
                "colombia",
                ccicode=None,
                footer_rows=["E|1", "P|1"],
            )
        )

        self.assertEqual(
            result.lines,
            (
                f"HDR;{input_filename};20260811091530;",
                f"BDY;{transaction_reference};{xref};TELLER-ACCOUNT;010;125.50;"
                "20260811;PEN;Y;;;",
                "FTR;1;1;",
            ),
        )
        body_scripts = [
            script for script in runner.scripts if "_CHISALCA_BODY_" in script
        ]
        contract_scripts = [
            script for script in runner.scripts if "_PROCESS_CONTRACT_" in script
        ]
        footer_scripts = [
            script for script in runner.scripts if "_CHISALCA_FOOTER_STATUS_" in script
        ]
        self.assertEqual(len(body_scripts), 2)
        self.assertEqual(len(contract_scripts), 1)
        self.assertEqual(len(footer_scripts), 2)
        self.assertTrue(
            all("target_table = 'DETB_UPLOAD_RTL_TELLER'" in script for script in body_scripts)
        )
        self.assertIn(
            "target_table = 'DETB_UPLOAD_RTL_TELLER'",
            contract_scripts[0],
        )
        self.assertTrue(all("target_table" not in script.lower() for script in footer_scripts))

    def test_peru_chisalou_runs_the_full_service_pipeline_with_ccicode(self) -> None:
        result, runner, input_filename, transaction_reference, xref = (
            self._generate_chisalou("peru", ccicode="CCI-FLD43")
        )

        self.assertIs(result.source, DataSourceChoice.ACTIVE)
        self.assertEqual((result.input_code, result.output_code), ("CHISALCA", "CHISALOU"))
        self.assertEqual(
            result.lines,
            (
                f"HDR;{input_filename};20260811091530;",
                f"BDY;{transaction_reference};{xref};TELLER-ACCOUNT;010;125.50;"
                "20260811;PEN;CCI-FLD43;Y;;;",
                "FTR;1;0;",
            ),
        )
        self.assertEqual(result.output_path.parent.name, "Peru")
        self.assertEqual(result.output_path.name, "CHISALOU_20260811.TXT")
        body_scripts = [
            script
            for script in runner.scripts
            if "_CHISALCA_BODY_" in script and "GITU_UPLOAD_MASTER" in script
        ]
        self.assertEqual(len(body_scripts), 2)
        self.assertTrue(all("trim(u.fld43)" in script.lower() for script in body_scripts))
        self.assertEqual(runner.pending, 0)

    def test_colombia_and_mexico_chisalou_run_when_prod_mapping_exists(self) -> None:
        for country in ("colombia", "mexico"):
            with self.subTest(country=country):
                result, runner, input_filename, transaction_reference, xref = (
                    self._generate_chisalou(country, ccicode=None)
                )

                self.assertEqual(
                    result.lines,
                    (
                        f"HDR;{input_filename};20260811091530;",
                        f"BDY;{transaction_reference};{xref};TELLER-ACCOUNT;010;125.50;"
                        "20260811;PEN;Y;;;",
                        "FTR;1;0;",
                    ),
                )
                self.assertEqual(result.output_path.parent.name, country.title())
                body_scripts = [
                    script
                    for script in runner.scripts
                    if "_CHISALCA_BODY_" in script and "GITU_UPLOAD_MASTER" in script
                ]
                self.assertEqual(len(body_scripts), 2)
                self.assertTrue(
                    all("trim(u.fld43)" not in script.lower() for script in body_scripts)
                )
                self.assertEqual(runner.pending, 0)

    def test_peru_ofdobiel_runs_end_to_end_when_prod_mapping_exists(self) -> None:
        body_rows = ["P|||BDY;C;000000001;E;P;"]
        service, runner = self._service(
            (
                ["UPLOAD_MASTER|ACTIVE|IFDOBIEL|1"],
                ["IFDOBIEL|OFDOBIEL"],
                ["20260731|1|1"],
                body_rows,
                body_rows,
                ["20260731|1|1"],
                ["1|1|1"],
            )
        )

        result = service.generate(
            GenerationRequest(
                target=self._target("peru"),
                process_ref_no="2798503",
            )
        )

        self.assertEqual((result.input_code, result.output_code), ("IFDOBIEL", "OFDOBIEL"))
        self.assertEqual(
            result.lines,
            (
                "HDR;OFDOBIEL.txt;20260731;",
                "BDY;C;000000001;E;P;;;",
                "TLR;1;",
            ),
        )
        self.assertEqual(result.output_path.parent.name, "Peru")
        self.assertEqual(result.output_path.name, "OFDOBIEL_20260731.TXT")
        body_scripts = [
            script
            for script in runner.scripts
            if "_IFDOBIEL_BODY_" in script and "GITU_UPLOAD_MASTER" in script
        ]
        self.assertEqual(len(body_scripts), 2)
        self.assertTrue(all("GIPKS_" not in script.upper() for script in body_scripts))
        self.assertEqual(runner.pending, 0)

    def test_peru_and_mexico_oficowcg_run_when_prod_mapping_exists(self) -> None:
        for country in ("peru", "mexico"):
            with self.subTest(country=country):
                body_row, data_fields = _regional_oficowcg_row(
                    xref=f"XREF-{country.upper()}"
                )
                body_rows = [body_row]
                coverage = ["1|1|1|1|0|1|0|0"]
                input_filename = f"IFICOWCG_{country.upper()}_20260811.TXT"
                service, runner = self._service(
                    (
                        ["UPLOAD_MASTER|ACTIVE|IFICOWCG|1"],
                        ["IFICOWCG|OFICOWCG"],
                        ["20260811|1|1"],
                        ["1|" + _hex(input_filename)],
                        body_rows,
                        coverage,
                        coverage,
                        body_rows,
                        ["20260811|1|1"],
                        ["1|" + _hex(input_filename)],
                        ["1|1|1"],
                    )
                )

                result = service.generate(
                    GenerationRequest(
                        target=self._target(country),
                        process_ref_no="5328716",
                    )
                )

                self.assertEqual(
                    result.lines,
                    (
                        f"HDR;{input_filename};20260811;",
                        "BDY;" + ";".join(data_fields) + ";P;SUCC;",
                        "FTR;1;0;",
                    ),
                )
                self.assertEqual(result.output_path.parent.name, country.title())
                body_scripts = [
                    script
                    for script in runner.scripts
                    if "CSTB_CLEARING_REJECTION" in script
                    and "rawtohex" in script.lower()
                ]
                self.assertEqual(len(body_scripts), 2)
                if country == "peru":
                    self.assertTrue(
                        all("nvl(ifc.instrno2, '##')" in script.lower() for script in body_scripts)
                    )
                else:
                    self.assertTrue(
                        all("nvl(ifc.instrno2, '##')" not in script.lower() for script in body_scripts)
                    )
                self.assertEqual(runner.pending, 0)


if __name__ == "__main__":
    unittest.main()
