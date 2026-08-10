from __future__ import annotations

import sys
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
from features.output_file_generation.queries import build_body_query  # noqa: E402
from features.output_file_generation.service import (  # noqa: E402
    OutputFileGenerationError,
    _collect_error_codes,
    _parse_body_records,
    _render_body_records,
)


def _hex(value: str) -> str:
    return value.encode("utf-8").hex().upper()


def _adapter_row(status: str, fields: tuple[str, ...]) -> str:
    return "|".join(_hex(value) for value in (status, status, *fields))


class AccountUpdateOutputMappingTests(unittest.TestCase):
    def test_chiclou_filename_header_footer_and_validator(self) -> None:
        spec = spec_for_code("CHICLUPD")

        self.assertEqual(spec.output_code, "CHICLOU")
        self.assertIs(spec_for_code("CHICLOU"), spec)
        self.assertTrue(spec.revalidate_external_inputs)
        self.assertEqual(physical_filename(spec, "20260807", "142355"), "20260807142355")
        header = build_header(spec, "20260807", "142355")
        footer = build_footer(spec, 2, {"P": 1, "E": 1})
        self.assertEqual(header, "LH^CHICLUPD.TXT^07082026^")
        self.assertEqual(footer, "LF^")
        validate_output_lines(
            spec,
            (
                header,
                "ALT1^ACC1^001^A^P^",
                "ALT2^ACC2^002^E^CH-001^Mensaje directo!^",
                footer,
            ),
        )

    def test_chiclou_active_and_archive_query_are_select_only_and_hex_safe(self) -> None:
        for source, table in (
            (DataSourceChoice.ACTIVE, "GITU_UPLOAD_MASTER"),
            (DataSourceChoice.ARCHIVE, "GITA_UPLOAD_MASTER"),
        ):
            with self.subTest(source=source.value):
                script = build_body_query("1234567", "CHICLUPD", source).script
                compact = " ".join(script.lower().split())
                self.assertIn(f"from {table} u", script)
                self.assertIn("u.process_ref_no = '1234567'", script)
                self.assertIn("upper(trim(u.interface_code)) = 'CHICLUPD'", script)
                self.assertIn("from cltb_account_master a", compact)
                self.assertIn("a.account_number = rtrim(u.fld31)", compact)
                self.assertIn("a.branch_code = rtrim(u.fld4)", compact)
                self.assertIn("from ertb_msgs m", compact)
                self.assertNotIn("m.language", compact)
                self.assertIn("u.status in ('p', 'e')", compact)
                self.assertIn("rawtohex(utl_i18n.string_to_raw", compact)
                self.assertIn("order by u.record_reference", compact)
                self.assertNotIn("fn_handoff", compact)
                self.assertNotIn("gipks_", compact)
                self.assertNotRegex(
                    compact,
                    r"\b(?:insert|update|delete|merge|commit|rollback)\b",
                )

    def test_chiclou_parser_and_renderer_use_direct_message(self) -> None:
        spec = spec_for_code("CHICLUPD")
        rows = [
            _adapter_row(
                "P",
                ("ALT1", "ACC1", "001", "1", "A", "0", "", "", ""),
            ),
            _adapter_row(
                "E",
                (
                    "ALT2",
                    "ACC2",
                    "002",
                    "0",
                    "",
                    "1",
                    "Mensaje directo!",
                    "CH-001",
                    "IGNORED",
                ),
            ),
        ]

        records, counts = _parse_body_records(rows, spec)
        self.assertEqual(counts, {"P": 1, "E": 1})
        self.assertEqual(_collect_error_codes(spec, records), set())
        self.assertEqual(
            _render_body_records(spec, records, {}),
            [
                "ALT1^ACC1^001^A^P^",
                "ALT2^ACC2^002^E^CH-001^Mensaje directo!^",
            ],
        )

    def test_chiclou_allows_missing_but_aborts_ambiguous_processed_account(self) -> None:
        spec = spec_for_code("CHICLUPD")
        missing_row = _adapter_row(
            "P",
            ("ALT", "ACC", "001", "0", "", "0", "", "", ""),
        )
        records, counts = _parse_body_records([missing_row], spec)
        self.assertEqual(counts, {"P": 1})
        self.assertEqual(
            _render_body_records(spec, records, {}),
            ["ALT^ACC^001^^P^"],
        )

        ambiguous_row = _adapter_row(
            "P",
            ("ALT", "ACC", "001", "2", "A", "0", "", "", ""),
        )
        with self.assertRaisesRegex(
            OutputFileGenerationError,
            "resolved more than one CLTB_ACCOUNT_MASTER row",
        ):
            _parse_body_records([ambiguous_row], spec)

    def test_account_update_contracts_omit_non_literal_statuses(self) -> None:
        cases = (
            (
                "CHICLUPD",
                ("ALT", "ACC", "001", "1", "A", "0", "", "", ""),
            ),
            (
                "CMRCLUPD",
                ("ALT", "ACC", "001", "1", "A", "", ""),
            ),
        )
        for input_code, fields in cases:
            with self.subTest(input_code=input_code):
                row = "|".join(_hex(value) for value in ("P", "p", *fields))
                records, counts = _parse_body_records(
                    [row], spec_for_code(input_code)
                )
                self.assertEqual(records, [])
                self.assertEqual(counts, {})

    def test_chiclou_preserves_previous_direct_message_like_qa(self) -> None:
        spec = spec_for_code("CHICLUPD")
        rows = [
            _adapter_row(
                "E",
                ("ALT1", "ACC1", "001", "0", "", "1", "Primero", "CH-1", ""),
            ),
            _adapter_row(
                "E",
                ("ALT2", "ACC2", "002", "0", "", "0", "Ignorado", "CH-2", ""),
            ),
            _adapter_row(
                "E",
                ("ALT3", "ACC3", "003", "0", "", "2", "Ignorado", "CH-3", ""),
            ),
            _adapter_row(
                "E",
                ("ALT4", "ACC4", "004", "0", "", "1", "", "CH-4", ""),
            ),
        ]
        records, _counts = _parse_body_records(rows, spec)
        self.assertEqual(
            _render_body_records(spec, records, {}),
            [
                "ALT1^ACC1^001^E^CH-1^Primero^",
                "ALT2^ACC2^002^E^CH-2^Primero^",
                "ALT3^ACC3^003^E^CH-3^Primero^",
                "ALT4^ACC4^004^E^CH-4^^",
            ],
        )

    def test_cmrclou_filename_header_footer_and_validator(self) -> None:
        spec = spec_for_code("CMRCLUPD")

        self.assertEqual(spec.output_code, "CMRCLOU")
        self.assertIs(spec_for_code("CMRCLOU"), spec)
        self.assertTrue(spec.revalidate_external_inputs)
        self.assertEqual(
            physical_filename(spec, "20260806"),
            "CMRCLOU_20260806.TXT",
        )
        header = build_header(spec, "20260806")
        footer = build_footer(spec, 2, {"P": 1, "E": 1})
        self.assertEqual(header, "LH^CMRCLUPD.TXT^20260806^")
        self.assertEqual(footer, "LF^")
        validate_output_lines(
            spec,
            (
                header,
                "ALT1^ACC1^001^A^P^",
                "ALT2^ACC2^002^E^CM-001;CM-002^Uno;Dos^",
                footer,
            ),
        )

    def test_cmrclou_active_and_archive_query_are_select_only_and_hex_safe(self) -> None:
        for source, table in (
            (DataSourceChoice.ACTIVE, "GITU_UPLOAD_MASTER"),
            (DataSourceChoice.ARCHIVE, "GITA_UPLOAD_MASTER"),
        ):
            with self.subTest(source=source.value):
                script = build_body_query("2807515", "CMRCLUPD", source).script
                compact = " ".join(script.lower().split())
                self.assertIn(f"from {table} u", script)
                self.assertIn("u.process_ref_no = '2807515'", script)
                self.assertIn("upper(trim(u.interface_code)) = 'CMRCLUPD'", script)
                self.assertIn("from cltb_account_master a", compact)
                self.assertIn("a.account_number = rtrim(u.fld31)", compact)
                self.assertIn("a.branch_code = rtrim(u.fld4)", compact)
                self.assertNotIn("ertb_msgs", compact)
                self.assertIn("u.status in ('p', 'e')", compact)
                self.assertIn("rawtohex(utl_i18n.string_to_raw", compact)
                self.assertIn("order by u.record_reference", compact)
                self.assertNotIn("fn_handoff", compact)
                self.assertNotIn("gipks_", compact)
                self.assertNotRegex(
                    compact,
                    r"\b(?:insert|update|delete|merge|commit|rollback)\b",
                )

    def test_cmrclou_parser_renderer_and_cldpymnt_message_contract(self) -> None:
        spec = spec_for_code("CMRCLUPD")
        rows = [
            _adapter_row(
                "P",
                ("ALT1", "ACC1", "001", "1", "A", "", ""),
            ),
            _adapter_row(
                "E",
                (
                    "ALT2",
                    "ACC2",
                    "002",
                    "0",
                    "",
                    "CM-001;CM-002",
                    "P1~;P2~;",
                ),
            ),
        ]

        records, counts = _parse_body_records(rows, spec)
        self.assertEqual(counts, {"P": 1, "E": 1})
        self.assertEqual(_collect_error_codes(spec, records), {"CM-001", "CM-002"})
        self.assertEqual(
            _render_body_records(
                spec,
                records,
                {"CM-001": "Uno $1!", "CM-002": "Dos $1!"},
            ),
            [
                "ALT1^ACC1^001^A^P^",
                "ALT2^ACC2^002^E^CM-001;CM-002^Uno P1;Dos P2^",
            ],
        )

    def test_cmrclou_allows_missing_but_aborts_ambiguous_processed_account(self) -> None:
        spec = spec_for_code("CMRCLUPD")
        missing_row = _adapter_row(
            "P",
            ("ALT", "ACC", "001", "0", "", "", ""),
        )
        records, counts = _parse_body_records([missing_row], spec)
        self.assertEqual(counts, {"P": 1})
        self.assertEqual(
            _render_body_records(spec, records, {}),
            ["ALT^ACC^001^^P^"],
        )

        ambiguous_row = _adapter_row(
            "P",
            ("ALT", "ACC", "001", "2", "A", "", ""),
        )
        with self.assertRaisesRegex(
            OutputFileGenerationError,
            "resolved more than one CLTB_ACCOUNT_MASTER row",
        ):
            _parse_body_records([ambiguous_row], spec)

    def test_cmrclou_treats_eopl_as_a_code_and_trims_final_spaces(self) -> None:
        spec = spec_for_code("CMRCLUPD")
        row = _adapter_row(
            "E",
            ("ALT", "ACC", "001", "0", "", "EOPL", ""),
        )
        records, _counts = _parse_body_records([row], spec)
        self.assertEqual(_collect_error_codes(spec, records), {"EOPL"})
        self.assertEqual(
            _render_body_records(spec, records, {"EOPL": "Final   "}),
            ["ALT^ACC^001^E^EOPL^Final^"],
        )


if __name__ == "__main__":
    unittest.main()
