from __future__ import annotations

import re
import sys
import tempfile
import threading
import unittest
from collections import deque
from pathlib import Path
from typing import Sequence


SRC_DIR = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_DIR))

from features.output_file_generation import (  # noqa: E402
    DataSourceChoice,
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
from features.output_file_generation.queries import build_body_query  # noqa: E402
from features.output_file_generation.service import (  # noqa: E402
    OutputFileGenerationError,
    _collect_error_codes,
    _parse_auxiliary_owner,
    _parse_body_records,
    _render_body_records,
)
from features.output_file_generation.upload_adapters import (  # noqa: E402
    upload_body_adapter,
)
from spools_cl_accounts.sqlcl import RunResult  # noqa: E402


DATE = "20260807"
TIME = "142355"
DATABASE_DATE = "20260806"
INPUT_FILENAME = "SOURCE_20260807.TXT"


def _hex(value: str) -> str:
    return value.encode("utf-8").hex().upper()


def _adapter_row(status: str, raw_status: str, fields: Sequence[str]) -> str:
    return "|".join(_hex(value) for value in (status, raw_status, *fields))


def _fixed_payment_body(fields: Sequence[str], status: str = "P") -> str:
    widths = (20, 10, 20, 4, 8, 8, 4, 100, 250)
    return (
        "02"
        + "".join(str(value)[:width].ljust(width) for value, width in zip(fields, widths))
        + ("Y" if status == "P" else "N")
    )


def _star_field(value: str, width: int) -> str:
    return str(value)[:width].ljust(width, "*") if value else "*" * width


# These are the upload-master adapters whose output contracts were extracted
# from the corresponding QA GIPKS_<OUTGOING_INTERFACE> package bodies.
CASES = {
    "GIUDFUPD": {
        "output": "GIUPDSTS",
        "style": "giupd",
        "pattern": "GIUPDSTS_{date}.TXT",
        "filename": "GIUPDSTS_20260807.TXT",
        "header": "HDR01;SOURCE_20260807.TXT;20260806142355;",
        "footer": "TRL01;1;0;",
        "fields": ("FUNC", "KEY", "UDF", "VALUE", "", ""),
        "body": "BDY01;FUNC;KEY;UDF;VALUE;Y;",
    },
    "IACMASSC": {
        "output": "OACMASSC",
        "style": "oacmassc",
        "pattern": "OACMASSC_{date}.TXT",
        "filename": "OACMASSC_20260807.TXT",
        "header": "HDR;OACMASSC_20260807.TXT;06082026140855;",
        "footer": "FTR;1;0;",
        "fields": ("1", "2", "3", "4", "5", "6", "7", "8", "", ""),
        "body": "BDY;1;2;3;4;5;6;7;8;Y;;;",
    },
    "CHBOOKIN": {
        "output": "CHBOOKOU",
        "style": "chbookou",
        "pattern": "CHBOOKOU_{date}{time}",
        "filename": "CHBOOKOU_20260807142355",
        "header": "HDR;CHBOOKOU_20260807142355;20260807142355;",
        "footer": "FTR;1;0;",
        "fields": ("BOOK", "001", "003", "002", "004", "005", "", ""),
        "body": "BDY;BOOK;001;003;002;004;005;Y;;;",
    },
    "CLIIRFAP": {
        "output": "CLOIRFAP",
        "style": "cloirfap",
        "pattern": "CLOIRFAP_{date}.TXT",
        "filename": "CLOIRFAP_20260807.TXT",
        "header": "HDR;CLOIRFAP.txt;20260807;",
        "footer": "TLR;1;",
        "fields": ("CLIENT", "REF", "VALUE", "", ""),
        "body": "BDY;CLIENT;REF;VALUE;P;;;",
    },
    "CLISLRES": {
        "output": "CLOSLRES",
        "style": "closlres",
        "pattern": "CLOSLRES.txt",
        "filename": "CLOSLRES.txt",
        "header": "HDR;CLOSLRES.txt;20260807;",
        "footer": "TLR;1;",
        "fields": ("CLIENT", "RESULT", "", ""),
        "body": "BDY;CLIENT;RESULT;P;;;",
    },
    "CMRADCHG": {
        "output": "CMRADCHO",
        "style": "cmradcho",
        "pattern": "CMRADCHO_{date}.TXT",
        "filename": "CMRADCHO_20260807.TXT",
        "header": "01^20260807",
        "footer": "03^1",
        "fields": ("ACC", "REF", "", ""),
        "body": "02ACC^REF^P^",
    },
    "CMRCIFUP": {
        "output": "CMRCIFOU",
        "style": "cmrcifou",
        "pattern": "CMRCIFOU_{date}.TXT",
        "filename": "CMRCIFOU_20260807.TXT",
        "header": "SH;SOURCE_20260807.TXT;20260807142355;",
        "footer": "SF;1;0;",
        "fields": ("M", "C", "", ""),
        "body": "BH;M;C;Y;;;",
    },
    "CMRLPMNT": {
        "output": "CMRLPMTO",
        "style": "fixed_payment",
        "pattern": "CMRLPMTO_{date}.TXT",
        "filename": "CMRLPMTO_20260807.TXT",
        "header": "0120260807    ",
        "footer": "030000000001",
        "fields": (
            "BRANCH",
            "CLP",
            "ACCOUNT",
            "7",
            "20260801",
            "20260831",
            "42",
            "",
            "",
        ),
    },
    "CMRRELVP": {
        "output": "CMRRELVO",
        "style": "cmrrelvo",
        "pattern": "CMRRELVO_{date}{time}.TXT",
        "filename": "CMRRELVO_20260807142355.TXT",
        "header": "LH^20260807",
        "footer": "LF^1^0^1",
        "fields": ("001", "ACCOUNT", "ESN", "", ""),
        "body": "BH^001^ACCOUNT^ESN^Y",
    },
    "IFCLPMNT": {
        "output": "IFCLPMTO",
        "style": "fixed_payment",
        "pattern": "IFCLPMTO_{date}.TXT",
        "filename": "IFCLPMTO_20260807.TXT",
        "header": "0120260807    ",
        "footer": "030000000001",
        "fields": (
            "BRANCH",
            "CLP",
            "ACCOUNT",
            "7",
            "20260801",
            "20260831",
            "42",
            "",
            "",
        ),
    },
    "IFDDISSU": {
        "output": "OFDDISSU",
        "style": "ofddissu",
        "pattern": "IFDDISSU_{date}{time}.TXT",
        "filename": "IFDDISSU_20260807142355.TXT",
        "header": "LH;20260807;",
        "footer": "",
        "fields": (*tuple(str(index) for index in range(2, 57)), "", ""),
        "body": "LB;" + ";".join(str(index) for index in range(2, 57)) + ";P;;;",
    },
    "IFGLCRTE": {
        "output": "OFGLCRTE",
        "style": "glcrte_fixed",
        "pattern": "SP{dmy}{time}END",
        "filename": "SP07082026142355END",
        "header": "LH20260807",
        "footer": "LF",
        "fields": ("F4", "F6", "", ""),
        "body": (
            "LB"
            + _star_field("F4", 36)
            + _star_field("F6", 20)
            + _star_field("P", 7)
            + _star_field("", 255)
            + _star_field("", 255)
        ),
    },
    "IFMDCGEN": {
        "output": "OFMDCGEN",
        "style": "ofmdcgen",
        "pattern": "OFMDCGEN_{date}{time}.TXT",
        "filename": "OFMDCGEN_20260807142355.TXT",
        "header": "01;IFMDCGEN.TXT;20260807;",
        "footer": "03;1;",
        "fields": ("EXT", "PROD", "BRAND", "BIN", "CARD", "REQ", "", ""),
        "body": "02;EXT;PROD;BRAND;BIN;CARD;REQ;P;;;",
    },
    "IFMDSUPD": {
        "output": "OFMDSUPD",
        "style": "ofmdsupd",
        "pattern": "OFMDSUPD_{date}{time}.TXT",
        "filename": "OFMDSUPD_20260807142355.TXT",
        "header": "01;IFMDSUPD.TXT;20260807;",
        "footer": "03;1;",
        "fields": ("EXT", "OLD", "NEW", "", ""),
        "body": "02;EXT;OLD;NEW;P;;;",
    },
    "INCHBKPR": {
        "output": "OUCHBKCU",
        "style": "ouchbkcu",
        "pattern": "OUCHBKCU_${dmy}{time}.TXT",
        "filename": "OUCHBKCU_$07082026142355.TXT",
        "header": "SH;SOURCE_20260807.TXT;20260807142355;",
        "footer": "SF;1;0;",
        "fields": ("A", "B", "C", "D", "E", "", ""),
        "body": "BH;A;B;C;D;E;Y;;;",
    },
    "IXCGRATE": {
        "output": "OXCGRATE",
        "style": "oxcgrate",
        "pattern": "OXCGRATE_{date}.{time}.TXT",
        "filename": "OXCGRATE_20260807.142355.TXT",
        "header": "HDR;OXCGRATE.txt;20260807;",
        "footer": "TLR;1;",
        "fields": ("RATE", "USD", "900", "", ""),
        "body": "BHD;RATE;USD;900;P;;;",
    },
    "LOCAMTIN": {
        "output": "LOCAMTOU",
        "style": "locamtou",
        "pattern": "LOCAMTOU_{date}.TXT",
        "filename": "LOCAMTOU_20260807.TXT",
        "header": "HDR;LOCAMTOU_20260807.TXT;06082026140855;",
        "footer": "FTR;1;0;",
        "fields": ("LOC", "ACC", "100", "", ""),
        "body": "BDY;LOC;ACC;100;Y;;;",
    },
    "STDCIFUP": {
        "output": "STDCIFOU",
        "style": "stdcifou",
        "pattern": "STDCIFOU_{date}.TXT",
        "filename": "STDCIFOU_20260807.TXT",
        "header": "SH;SOURCE_20260807.TXT;20260807142355;",
        "footer": "SF;1;0;",
        "fields": ("CUST", "NAME", "", ""),
        "body": "BH;CUST;NAME;Y;;;",
    },
    "STDCRDUP": {
        "output": "STDCRDOU",
        "style": "stdcrdou",
        "pattern": "STDCRDOU_{date}.TXT",
        "filename": "STDCRDOU_20260807.TXT",
        "header": "HDR;SOURCE_20260807.TXT;20260807142355;",
        "footer": "FTR;1;0;",
        "fields": ("1", "2", "3", "4", "5", "6", "7", "8", "9", "", ""),
        "body": "BDY;1;2;3;4;5;6;7;8;9;Y;;;",
    },
}

for _code in ("CMRLPMNT", "IFCLPMNT"):
    CASES[_code]["body"] = _fixed_payment_body(CASES[_code]["fields"])


class AdapterMappingContractTests(unittest.TestCase):
    def test_all_declarative_adapters_are_registered_with_expected_mapping(self) -> None:
        self.assertEqual(len(CASES), 19)
        for input_code, case in CASES.items():
            with self.subTest(input_code=input_code):
                spec = spec_for_code(input_code)
                adapter = upload_body_adapter(input_code)
                self.assertIsNotNone(adapter)
                assert adapter is not None
                self.assertEqual(spec.output_code, case["output"])
                self.assertEqual(spec.contract, case["style"])
                self.assertEqual(adapter.style, case["style"])
                self.assertEqual(spec.physical_name_pattern, case["pattern"])
                self.assertIs(spec_for_code(str(case["output"])), spec)

    def test_physical_names_headers_and_footers_match_contract_table(self) -> None:
        for input_code, case in CASES.items():
            with self.subTest(input_code=input_code):
                spec = spec_for_code(input_code)
                self.assertEqual(physical_filename(spec, DATE, TIME), case["filename"])
                self.assertEqual(
                    build_header(
                        spec,
                        DATE,
                        TIME,
                        INPUT_FILENAME,
                        DATABASE_DATE,
                    ),
                    case["header"],
                )
                self.assertEqual(build_footer(spec, 1, {"P": 1}), case["footer"])

    def test_active_and_archive_sql_transport_normalized_raw_status_and_fields(self) -> None:
        for input_code in CASES:
            adapter = upload_body_adapter(input_code)
            assert adapter is not None
            if input_code == "GIUDFUPD":
                source_tables = (
                    (DataSourceChoice.ACTIVE, "UDF_OWNER.GITM_UDF_UPLOAD_DETAILS"),
                )
            else:
                source_tables = (
                    (DataSourceChoice.ACTIVE, "GITU_UPLOAD_MASTER"),
                    (DataSourceChoice.ARCHIVE, "GITA_UPLOAD_MASTER"),
                )
            for source, table in source_tables:
                with self.subTest(input_code=input_code, source=source.value):
                    script = build_body_query(
                        "1234567",
                        input_code,
                        source,
                        auxiliary_owner=(
                            "UDF_OWNER" if input_code == "GIUDFUPD" else ""
                        ),
                    ).script
                    normalized = (
                        "nvl(upper(trim(u.status)), '<NULL>')"
                    )
                    raw_status = "utl_i18n.string_to_raw(u.status, 'AL32UTF8')"
                    self.assertIn(f"from {table} u", script)
                    self.assertIn("u.process_ref_no = '1234567'", script)
                    if input_code == "GIUDFUPD":
                        self.assertNotIn("u.interface_code", script.lower())
                        self.assertNotIn("GITU_UPLOAD_MASTER", script)
                    else:
                        self.assertIn(
                            f"upper(trim(u.interface_code)) = '{input_code}'",
                            script,
                        )
                    self.assertIn(normalized, script)
                    self.assertIn(raw_status, script)
                    self.assertLess(script.index(normalized), script.index(raw_status))
                    self.assertEqual(
                        script.count("utl_i18n.string_to_raw("),
                        len(adapter.fields) + 2,
                    )
                    self.assertEqual(script.count("|| '|' ||"), len(adapter.fields) + 1)
                    for expression in adapter.fields:
                        self.assertIn(expression, script)
                    for filter_sql in adapter.filters:
                        self.assertIn(filter_sql, script)
                    self.assertIn(f"order by {adapter.order_by}", script)
                    self.assertNotIn("FN_HANDOFF", script.upper())
                    self.assertNotIn("GIPKS_", script.upper())
                    self.assertNotRegex(
                        script.lower(),
                        r"\b(?:insert|update|delete|merge|commit|rollback)\b",
                    )

        with self.assertRaisesRegex(ValueError, "archive reconstruction"):
            build_body_query(
                "1234567",
                "GIUDFUPD",
                DataSourceChoice.ARCHIVE,
                auxiliary_owner="UDF_OWNER",
            )
        with self.assertRaisesRegex(ValueError, "auxiliary table owner"):
            build_body_query(
                "1234567",
                "GIUDFUPD",
                DataSourceChoice.ACTIVE,
            )

    def test_parser_renderer_and_validator_goldens_cover_every_contract_style(self) -> None:
        observed_styles: set[str] = set()
        for input_code, case in CASES.items():
            with self.subTest(input_code=input_code):
                spec = spec_for_code(input_code)
                fields = tuple(case["fields"])
                row = _adapter_row("P", "P", fields)
                records, counts = _parse_body_records([row], spec)

                self.assertEqual(counts, {"P": 1})
                self.assertEqual(records[0].status, "P")
                self.assertEqual(records[0].output_status, "P")
                self.assertEqual(records[0].fields, fields)
                self.assertEqual(_render_body_records(spec, records, {}), [case["body"]])

                lines = (
                    (str(case["header"]), str(case["body"]), str(case["footer"]))
                    if spec.has_footer
                    else (str(case["header"]), str(case["body"]))
                )
                validate_output_lines(spec, lines)
                observed_styles.add(spec.contract)

        self.assertEqual(observed_styles, {case["style"] for case in CASES.values()})

    def test_parser_requires_the_raw_status_transport_column(self) -> None:
        spec = spec_for_code("CLIIRFAP")
        fields = tuple(CASES["CLIIRFAP"]["fields"])
        row_without_raw_status = "|".join(_hex(value) for value in ("P", *fields))
        with self.assertRaisesRegex(
            OutputFileGenerationError,
            "malformed declarative body record",
        ):
            _parse_body_records([row_without_raw_status], spec)

    def test_ofddissu_trims_status_and_ignores_orphan_error_params(self) -> None:
        spec = spec_for_code("IFDDISSU")
        fields = (*tuple(str(index) for index in range(2, 57)), "", "ORPHAN")
        records, counts = _parse_body_records(
            [_adapter_row("P", " P ", fields)],
            spec,
        )

        body = _render_body_records(spec, records, {})[0]

        self.assertEqual(counts, {"P": 1})
        self.assertTrue(body.endswith(";P;;;"))
        self.assertNotIn("ORPHAN", body)
        validate_output_lines(spec, ("LH;20260807;", body))

    def test_oacmassc_multiple_errors_match_qa_lists_and_footer_counts(self) -> None:
        spec = spec_for_code("IACMASSC")
        records, counts = _parse_body_records(
            [
                _adapter_row(
                    "E",
                    "E",
                    ("1", "2", "3", "4", "5", "6", "7", "8", "E1;E2;", "P1;P2"),
                )
            ],
            spec,
        )

        self.assertEqual(_collect_error_codes(spec, records), {"E1", "E2"})
        body = _render_body_records(
            spec,
            records,
            {"E1": "First $1", "E2": "Second $1"},
        )[0]
        self.assertEqual(
            body,
            "BDY;1;2;3;4;5;6;7;8;N;E1~E2;First P1~Second P2~;",
        )
        footer = build_footer(spec, 1, counts)
        self.assertEqual(footer, "FTR;0;1;")
        validate_output_lines(
            spec,
            (
                build_header(spec, DATE, TIME, INPUT_FILENAME, DATABASE_DATE),
                body,
                footer,
            ),
        )

    def test_giupdsts_multiple_errors_preserve_qa_tilde_termination(self) -> None:
        spec = spec_for_code("GIUDFUPD")
        records, counts = _parse_body_records(
            [
                _adapter_row(
                    "E",
                    "E",
                    ("FUNC", "KEY", "UDF", "VALUE", "E1;E2;", "P1;P2"),
                )
            ],
            spec,
        )

        self.assertEqual(_collect_error_codes(spec, records), {"E1", "E2"})
        body = _render_body_records(
            spec,
            records,
            {"E1": "First $1", "E2": "Second $1"},
        )[0]
        self.assertEqual(
            body,
            "BDY01;FUNC;KEY;UDF;VALUE;N;E1~E2~;First P1~Second P2~;",
        )
        footer = build_footer(spec, 1, counts)
        self.assertEqual(footer, "TRL01;0;1;")
        validate_output_lines(
            spec,
            (
                build_header(spec, DATE, TIME, INPUT_FILENAME, DATABASE_DATE),
                body,
                footer,
            ),
        )

    def test_ofglcrte_multiple_errors_prefix_messages_and_truncate_fixed_fields(self) -> None:
        spec = spec_for_code("IFGLCRTE")
        records, counts = _parse_body_records(
            [
                _adapter_row(
                    "E",
                    "E",
                    ("X" * 40, "Y" * 25, "E1;E2;", "P1;P2"),
                )
            ],
            spec,
        )

        self.assertEqual(_collect_error_codes(spec, records), {"E1", "E2"})
        body = _render_body_records(
            spec,
            records,
            {"E1": "First $1", "E2": "Second $1"},
        )[0]
        self.assertEqual(len(body), 575)
        self.assertEqual(body[:2], "LB")
        self.assertEqual(body[2:38], "X" * 36)
        self.assertEqual(body[38:58], "Y" * 20)
        self.assertEqual(body[58:65], "E" + "*" * 6)
        self.assertTrue(body[65:320].startswith("E1;E2;"))
        self.assertTrue(body[320:].startswith(";First P1;Second P2"))
        self.assertTrue(body.endswith("*"))
        validate_output_lines(
            spec,
            (build_header(spec, DATE, TIME), body, build_footer(spec, 1, counts)),
        )

    def test_case_sensitive_qa_adapters_reject_lowercase_raw_status(self) -> None:
        cases = {
            "IACMASSC": ("1", "2", "3", "4", "5", "6", "7", "8", "", ""),
            "GIUDFUPD": ("FUNC", "KEY", "UDF", "VALUE", "", ""),
        }
        for input_code, fields in cases.items():
            with self.subTest(input_code=input_code):
                with self.assertRaisesRegex(OutputFileGenerationError, "lowercase status"):
                    _parse_body_records(
                        [_adapter_row("P", "p", fields)],
                        spec_for_code(input_code),
                    )

    def test_giudfupd_auxiliary_owner_must_be_unique(self) -> None:
        for rows in ([], [_hex("OWNER_A"), _hex("OWNER_B")]):
            with self.subTest(rows=rows):
                with self.assertRaisesRegex(OutputFileGenerationError, "unambiguous"):
                    _parse_auxiliary_owner(rows)

    def test_error_list_raw_status_u_fallback_and_external_footer_edges(self) -> None:
        giupd = spec_for_code("GIUDFUPD")
        records, counts = _parse_body_records(
            [
                _adapter_row(
                    "U",
                    "U",
                    ("FUNC", "KEY", "UDF", "VALUE", "OLD", "DETAIL~"),
                )
            ],
            giupd,
        )
        self.assertEqual(_collect_error_codes(giupd, records), {"GI-INT214"})
        giupd_body = _render_body_records(
            giupd,
            records,
            {"GI-INT214": "Failed $1"},
        )[0]
        self.assertEqual(
            giupd_body,
            "BDY01;FUNC;KEY;UDF;VALUE;N;GI-INT214;;Failed DETAIL~;",
        )
        giupd_footer = build_footer(giupd, 1, counts)
        self.assertEqual(giupd_footer, "TRL01;0;1;")
        validate_output_lines(
            giupd,
            (
                "HDR01;SOURCE_20260807.TXT;20260806142355;",
                giupd_body,
                giupd_footer,
            ),
        )

        chbook = spec_for_code("CHBOOKIN")
        records, counts = _parse_body_records(
            [
                _adapter_row(
                    "E",
                    "E",
                    ("S", "X", "A", "B", "1", "10", "E1;E2;", "P1;P2"),
                )
            ],
            chbook,
        )
        self.assertEqual(_collect_error_codes(chbook, records), {"E1", "E2"})
        chbook_body = _render_body_records(
            chbook,
            records,
            {"E1": "First $1", "E2": "Second $1"},
        )[0]
        self.assertEqual(
            chbook_body,
            "BDY;S;X;A;B;1;10;N;E1;E2;;First P1~Second P2~;",
        )
        validate_output_lines(
            chbook,
            (
                build_header(chbook, DATE, TIME),
                chbook_body,
                build_footer(chbook, 1, counts),
            ),
        )

        closlres = spec_for_code("CLISLRES")
        records, counts = _parse_body_records(
            [_adapter_row("E", " e ", ("C", "R", "E1;E2;", "P1;P2;"))],
            closlres,
        )
        closlres_body = _render_body_records(closlres, records, {})[0]
        self.assertEqual(closlres_body, "BDY;C;R; e ;E1;E2;;P1;P2;;")
        validate_output_lines(
            closlres,
            (
                build_header(closlres, DATE),
                closlres_body,
                build_footer(closlres, 1, counts),
            ),
        )

        cmrcif = spec_for_code("CMRCIFUP")
        records, body_counts = _parse_body_records(
            [_adapter_row("U", "U", ("M", "C", "OLD", "OLD PARAM"))],
            cmrcif,
        )
        self.assertEqual(_collect_error_codes(cmrcif, records), {"GI-INT214"})
        cmrcif_body = _render_body_records(
            cmrcif,
            records,
            {"GI-INT214": "$1"},
        )[0]
        self.assertEqual(
            cmrcif_body,
            "BH;M;C;N;GI-INT214;Failed to process data~;",
        )
        footer = build_footer(cmrcif, 1, {"P": 2, "E": 1, "U": 1})
        self.assertEqual(footer, "SF;2;2;")
        validate_output_lines(
            cmrcif,
            (
                build_header(cmrcif, DATE, TIME, INPUT_FILENAME),
                cmrcif_body,
                footer,
            ),
        )
        self.assertEqual(body_counts, {"U": 1})

        debit = spec_for_code("IFMDSUPD")
        records, counts = _parse_body_records(
            [_adapter_row("E", "E", ("X", "A", "PAN", "I-SUCCESS;E2;", ";P2"))],
            debit,
        )
        self.assertEqual(
            _collect_error_codes(debit, records),
            {"I-SUCCESS", "E2"},
        )
        debit_body = _render_body_records(
            debit,
            records,
            {"I-SUCCESS": "OK", "E2": "Error $1"},
        )[0]
        self.assertEqual(debit_body, "02;X;A;PAN;E;I-SUCCESS;E2;;OK;Error P2;;")
        validate_output_lines(
            debit,
            (
                build_header(debit, DATE),
                debit_body,
                build_footer(debit, 1, counts),
            ),
        )


class _FakeConfig:
    def __init__(self, sqlcl_path: Path) -> None:
        self.sqlcl_path = sqlcl_path
        self.credential = {
            "user": "fake_user",
            "schema": "",
            "password_enc": "encrypted-for-test",
            "tns": "FXBFCL_19C_PROD_OCI",
            "bucket": "shared_prod",
        }
        self.credentials = {
            "chile": {"PROD-DB": {"SHARED-PROD": self.credential}}
        }

    def get(self, key: str, default=None):
        if key == "sqlcl_path":
            return str(self.sqlcl_path)
        return default

    def get_credential(
        self,
        country: str,
        database_key: str,
        credential_key: str,
    ) -> dict[str, str] | None:
        if (country.lower(), database_key.upper(), credential_key.upper()) == (
            "chile",
            "PROD-DB",
            "SHARED-PROD",
        ):
            return self.credential
        return None

    def all_credentials(self) -> dict:
        return self.credentials


_QueuedResponse = Sequence[str] | RunResult


class _QueuedSqlclRunner:
    def __init__(self, responses: Sequence[_QueuedResponse]) -> None:
        self.responses = deque(responses)
        self.scripts: list[str] = []

    @property
    def pending(self) -> int:
        return len(self.responses)

    def run_script(
        self,
        connection: str,
        script_path: str | Path,
        args: list[str] | None = None,
        timeout: float | None = None,
        cancel_event: threading.Event | None = None,
    ) -> RunResult:
        del connection, args, timeout, cancel_event
        if not self.responses:
            raise AssertionError("SQLcl was called more times than expected")
        script = Path(script_path).read_text(encoding="utf-8")
        self.scripts.append(script)
        response = self.responses.popleft()
        if isinstance(response, RunResult):
            return response
        begin = re.search(r"^prompt (.+_BEGIN__)$", script, re.MULTILINE)
        end = re.search(r"^prompt (.+_END__)$", script, re.MULTILINE)
        if begin is None or end is None:
            raise AssertionError("The generated SQL script is not framed")
        rows = "\n".join(response)
        return RunResult(0, f"{begin.group(1)}\n{rows}\n{end.group(1)}\n", "")


class AdapterMappingIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.sqlcl_path = self.root / "sql.exe"
        self.sqlcl_path.touch()
        self.config = _FakeConfig(self.sqlcl_path)
        self.target = OracleTarget(
            country="chile",
            database_key="prod-db",
            credential_key="shared-prod",
            tns="FXBFCL_19C_PROD_OCI",
            label="Chile PROD",
        )

    def _service(self, runner: _QueuedSqlclRunner) -> OutputFileGenerationService:
        return OutputFileGenerationService(
            self.config,
            runner_factory=lambda _path: runner,
            decryptor=lambda _encrypted: "fake-password",
            output_root=self.root / "output",
        )

    def _generate(self, responses: Sequence[_QueuedResponse]):
        runner = _QueuedSqlclRunner(responses)
        service = self._service(runner)
        result = service.generate(
            GenerationRequest(target=self.target, process_ref_no="1234567")
        )
        return result, runner

    def test_giupdsts_active_integration_autodetects_owner_and_last_run_date(self) -> None:
        owner_rows = [_hex("UDF_OWNER")]
        last_run_rows = ["20260806|1"]
        filename_rows = ["1|" + _hex("SOURCE_20260807.TXT")]
        fields = ("FUNC", "KEY", "UDF", "VALUE", "OLD", "DETAIL~")
        body_rows = [_adapter_row("U", "U", fields)]
        message_rows = [
            _hex("GI-INT214") + "|1|" + _hex("Failed $1")
        ]
        date_rows = ["20260807|1|1"]

        result, runner = self._generate(
            [
                ["UPLOAD_MASTER|ACTIVE|GIUDFUPD|1"],
                ["GIUDFUPD|GIUPDSTS"],
                owner_rows,
                date_rows,
                last_run_rows,
                ["142355"],
                filename_rows,
                body_rows,
                message_rows,
                body_rows,
                owner_rows,
                last_run_rows,
                date_rows,
                filename_rows,
                message_rows,
                ["1|1|1"],
            ]
        )

        self.assertEqual(
            result.lines,
            (
                "HDR01;SOURCE_20260807.TXT;20260806142355;",
                "BDY01;FUNC;KEY;UDF;VALUE;N;GI-INT214;;Failed DETAIL~;",
                "TRL01;0;1;",
            ),
        )
        self.assertEqual(result.output_path.name, "GIUPDSTS_20260807.TXT")
        self.assertEqual(result.status_counts, {"U": 1})
        self.assertEqual(runner.pending, 0)

        owner_scripts = [
            script
            for script in runner.scripts
            if "ALL_TAB_COLUMNS" in script
            and "GITM_UDF_UPLOAD_DETAILS" in script
        ]
        self.assertEqual(len(owner_scripts), 2)
        last_run_scripts = [
            script
            for script in runner.scripts
            if "max(last_run_date)" in script.lower()
            and "GITM_INTERFACE_DEFINITION" in script
        ]
        self.assertEqual(len(last_run_scripts), 2)
        self.assertTrue(
            all(
                "from UDF_OWNER.GITM_UDF_UPLOAD_DETAILS u" in script
                for script in runner.scripts
                if "GITM_UDF_UPLOAD_DETAILS u" in script
            )
        )

    def test_giupdsts_stops_when_detail_count_differs_from_upload_master(self) -> None:
        runner = _QueuedSqlclRunner(
            [
                ["UPLOAD_MASTER|ACTIVE|GIUDFUPD|2"],
                ["GIUDFUPD|GIUPDSTS"],
                [_hex("UDF_OWNER")],
                ["20260807|1|1"],
                ["20260806|1"],
                ["142355"],
                ["1|" + _hex("SOURCE_20260807.TXT")],
                [_adapter_row("P", "P", ("FUNC", "KEY", "UDF", "VALUE", "", ""))],
            ]
        )

        with self.assertRaisesRegex(OutputFileGenerationError, "detail rows do not match"):
            self._service(runner).generate(
                GenerationRequest(target=self.target, process_ref_no="1234567")
            )

        self.assertEqual(runner.pending, 0)

    def test_giupdsts_archive_reconstruction_is_rejected_before_detail_queries(self) -> None:
        runner = _QueuedSqlclRunner(
            [
                ["UPLOAD_MASTER|ARCHIVE|GIUDFUPD|1"],
                ["GIUDFUPD|GIUPDSTS"],
            ]
        )

        with self.assertRaisesRegex(
            OutputFileGenerationError,
            "archive reconstruction is unavailable",
        ):
            self._service(runner).generate(
                GenerationRequest(target=self.target, process_ref_no="1234567")
            )

        self.assertEqual(runner.pending, 0)
        self.assertFalse(
            any("GITM_UDF_UPLOAD_DETAILS" in script for script in runner.scripts)
        )

    def test_cloirfap_archive_integration_resolves_and_escapes_error(self) -> None:
        fields = ("CLIENT", "REF", "VALUE", ";E-ONE;", "ACCOUNT~;")
        result, runner = self._generate(
            [
                ["UPLOAD_MASTER|ARCHIVE|CLIIRFAP|1"],
                ["CLIIRFAP|CLOIRFAP"],
                ["20260807|1|1"],
                [_adapter_row("E", "E", fields)],
                [_hex("E-ONE") + "|1|" + _hex("Cuenta $1")],
                [_hex("E-ONE") + "|1|" + _hex("Cuenta $1")],
                ["1|1|1"],
            ]
        )

        self.assertEqual(result.lines[0], "HDR;CLOIRFAP.txt;20260807;")
        self.assertEqual(
            result.lines[1],
            "BDY;CLIENT;REF;VALUE;E;~E-ONE;Cuenta ACCOUNT;",
        )
        self.assertEqual(result.lines[-1], "TLR;1;")
        self.assertEqual(result.output_path.name, "CLOIRFAP_20260807.TXT")
        self.assertEqual(result.status_counts, {"E": 1})
        self.assertEqual(runner.pending, 0)
        self.assertTrue(
            any("from GITA_UPLOAD_MASTER u" in script for script in runner.scripts)
        )

    def test_cmrlpmnt_archive_integration_writes_fixed_width_contract(self) -> None:
        fields = tuple(CASES["CMRLPMNT"]["fields"])
        expected_body = str(CASES["CMRLPMNT"]["body"])
        result, runner = self._generate(
            [
                ["UPLOAD_MASTER|ARCHIVE|CMRLPMNT|1"],
                ["CMRLPMNT|CMRLPMTO"],
                ["20260807|1|1"],
                [_adapter_row("P", "P", fields)],
                ["1|1|1"],
            ]
        )

        self.assertEqual(result.lines, ("0120260807    ", expected_body, "030000000001"))
        self.assertEqual(len(result.lines[1]), 427)
        self.assertEqual(result.output_path.name, "CMRLPMTO_20260807.TXT")
        self.assertEqual(
            result.output_path.read_bytes(),
            ("\n".join(result.lines) + "\n").encode("utf-8"),
        )
        self.assertEqual(runner.pending, 0)

    def test_ofddissu_archive_integration_omits_footer(self) -> None:
        fields = tuple(CASES["IFDDISSU"]["fields"])
        result, runner = self._generate(
            [
                ["UPLOAD_MASTER|ARCHIVE|IFDDISSU|1"],
                ["IFDDISSU|OFDDISSU"],
                ["20260807|1|1"],
                [_adapter_row("P", "P", fields)],
                ["1|1|1"],
                ["142355"],
            ]
        )

        self.assertEqual(
            result.lines,
            (str(CASES["IFDDISSU"]["header"]), str(CASES["IFDDISSU"]["body"])),
        )
        self.assertEqual(result.output_path.name, "IFDDISSU_20260807142355.TXT")
        self.assertNotIn("LF", result.lines)
        self.assertEqual(runner.pending, 0)


if __name__ == "__main__":
    unittest.main()
