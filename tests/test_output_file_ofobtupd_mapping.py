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
    physical_filename,
    serialize_lines,
    spec_for_code,
    validate_output_lines,
)
from features.output_file_generation.models import DataSourceChoice  # noqa: E402
from features.output_file_generation.queries import build_body_query  # noqa: E402
from features.output_file_generation.service import (  # noqa: E402
    _collect_error_codes,
    _parse_body_records,
    _render_body_records,
)
from features.output_file_generation.upload_adapters import (  # noqa: E402
    upload_body_adapter,
)


DATE = "20260807"
TIME = "142355"


def _hex(value: str) -> str:
    return value.encode("utf-8").hex().upper()


def _adapter_row(
    normalized_status: str,
    raw_status: str,
    *,
    obid: str = "OB-1",
    product: str = "2084",
    customer: str = "123456789",
    error: str = "",
    error_param: str = "",
) -> str:
    values = (
        normalized_status,
        raw_status,
        obid,
        product,
        customer,
        error,
        error_param,
    )
    return "|".join(_hex(value) for value in values)


class OfobtupdContractTests(unittest.TestCase):
    def test_registry_filename_clean_session_header_and_footer(self) -> None:
        spec = spec_for_code("IFOBTUPD")

        self.assertEqual(spec.output_code, "OFOBTUPD")
        self.assertIs(spec_for_code("OFOBTUPD"), spec)
        self.assertEqual(spec.contract, "ofobtupd")
        self.assertEqual(spec.physical_name_pattern, "OFOBTUPD_{date}.TXT")
        self.assertTrue(spec.revalidate_external_inputs)
        self.assertEqual(
            physical_filename(spec, DATE, TIME),
            "OFOBTUPD_20260807.TXT",
        )

        # QA calls FN_PROCESS_COMPONENT before assigning PKG_FILE_NAME.  A clean
        # package session therefore emits an empty second header field.  Keeping
        # that deterministic is preferable to leaking a prior invocation's name.
        self.assertEqual(build_header(spec, DATE, TIME), "01;;20260807")
        self.assertEqual(build_footer(spec, 2, {"P": 1, "E": 1}), "03;2")

    def test_active_and_archive_queries_are_scoped_ordered_selects(self) -> None:
        adapter = upload_body_adapter("IFOBTUPD")
        self.assertIsNotNone(adapter)
        assert adapter is not None
        self.assertEqual(adapter.style, "ofobtupd")
        self.assertEqual(
            adapter.fields,
            (
                "trim(u.fld3)",
                "trim(u.fld4)",
                "trim(u.fld5)",
                "u.error",
                "u.error_param",
            ),
        )

        for source, table in (
            (DataSourceChoice.ACTIVE, "GITU_UPLOAD_MASTER"),
            (DataSourceChoice.ARCHIVE, "GITA_UPLOAD_MASTER"),
        ):
            with self.subTest(source=source.value):
                script = build_body_query("2789582", "IFOBTUPD", source).script
                self.assertIn(f"from {table} u", script)
                self.assertIn("u.process_ref_no = '2789582'", script)
                self.assertIn(
                    "upper(trim(u.interface_code)) = 'IFOBTUPD'",
                    script,
                )
                self.assertIn("order by u.record_reference", script)
                for expression in adapter.fields:
                    self.assertIn(expression, script)
                self.assertEqual(
                    script.count("utl_i18n.string_to_raw("),
                    len(adapter.fields) + 2,
                )
                self.assertNotIn("FN_HANDOFF", script.upper())
                self.assertNotIn("GIPKS_", script.upper())
                self.assertNotRegex(
                    script.lower(),
                    r"\b(?:insert|update|delete|merge|commit|rollback)\b",
                )

    def test_processed_record_ignores_orphan_error_values(self) -> None:
        spec = spec_for_code("IFOBTUPD")
        records, counts = _parse_body_records(
            [
                _adapter_row(
                    "P",
                    "P",
                    error="SHOULD-NOT-BE-EMITTED;",
                    error_param="OR-RESOLVED~;",
                )
            ],
            spec,
        )

        self.assertEqual(counts, {"P": 1})
        self.assertEqual(_collect_error_codes(spec, records), set())
        self.assertEqual(
            _render_body_records(spec, records, {}),
            ["02;OB-1;2084;123456789;P;"],
        )

    def test_error_code_and_first_description_have_no_added_separator(self) -> None:
        spec = spec_for_code("IFOBTUPD")
        records, counts = _parse_body_records(
            [
                _adapter_row(
                    "E",
                    "E",
                    error="ST-ONE",
                    error_param="ACCOUNT~",
                )
            ],
            spec,
        )

        self.assertEqual(counts, {"E": 1})
        self.assertEqual(_collect_error_codes(spec, records), {"ST-ONE"})
        body = _render_body_records(
            spec,
            records,
            {"ST-ONE": "Failed $1"},
        )[0]
        self.assertEqual(
            body,
            "02;OB-1;2084;123456789;E;ST-ONEFailed ACCOUNT;",
        )

    def test_multiple_error_codes_use_matching_parameter_groups(self) -> None:
        spec = spec_for_code("IFOBTUPD")
        records, _counts = _parse_body_records(
            [
                _adapter_row(
                    "E",
                    "E",
                    error="E-ONE;E-TWO;",
                    error_param="FIRST~;SECOND~;",
                )
            ],
            spec,
        )

        self.assertEqual(_collect_error_codes(spec, records), {"E-ONE", "E-TWO"})
        body = _render_body_records(
            spec,
            records,
            {"E-ONE": "One $1", "E-TWO": "Two $1"},
        )[0]
        self.assertEqual(
            body,
            "02;OB-1;2084;123456789;E;"
            "E-ONE;E-TWO;One FIRST;Two SECOND;",
        )

    def test_missing_message_matches_ovpks_fallback(self) -> None:
        spec = spec_for_code("IFOBTUPD")
        records, _counts = _parse_body_records(
            [_adapter_row("E", "E", error="UNKNOWN;", error_param="VALUE~;")],
            spec,
        )

        self.assertEqual(
            _render_body_records(spec, records, {}),
            ["02;OB-1;2084;123456789;E;UNKNOWN;Missing Error Code;"],
        )

    def test_error_branch_uses_exact_raw_status_not_normalized_status(self) -> None:
        spec = spec_for_code("IFOBTUPD")
        records, counts = _parse_body_records(
            [
                _adapter_row(
                    "E",
                    " E ",
                    error="ST-ONE;",
                    error_param="ACCOUNT~;",
                )
            ],
            spec,
        )

        self.assertEqual(counts, {"E": 1})
        self.assertEqual(_collect_error_codes(spec, records), set())
        self.assertEqual(
            _render_body_records(spec, records, {"ST-ONE": "Failed $1"}),
            ["02;OB-1;2084;123456789; E ;"],
        )

    def test_null_status_is_still_a_counted_body_record(self) -> None:
        spec = spec_for_code("IFOBTUPD")
        records, counts = _parse_body_records(
            [_adapter_row("<NULL>", "", error="IGNORED;")],
            spec,
        )

        self.assertEqual(counts, {"<NULL>": 1})
        body = _render_body_records(spec, records, {})[0]
        self.assertEqual(body, "02;OB-1;2084;123456789;;")
        footer = build_footer(spec, 1, counts)
        self.assertEqual(footer, "03;1")
        validate_output_lines(spec, ("01;;20260807", body, footer))

    def test_complete_payload_is_lf_terminated_and_validated(self) -> None:
        spec = spec_for_code("IFOBTUPD")
        records, counts = _parse_body_records(
            [_adapter_row("P", "P")],
            spec,
        )
        body = _render_body_records(spec, records, {})[0]
        lines = (
            build_header(spec, DATE, TIME),
            body,
            build_footer(spec, len(records), counts),
        )

        validate_output_lines(spec, lines)
        self.assertEqual(
            serialize_lines(lines),
            (
                "01;;20260807\n"
                "02;OB-1;2084;123456789;P;\n"
                "03;1\n"
            ).encode("utf-8"),
        )

    def test_validator_rejects_stale_header_name_and_wrong_footer_count(self) -> None:
        spec = spec_for_code("IFOBTUPD")
        body = "02;OB-1;2084;123456789;P;"

        with self.assertRaises(ValueError):
            validate_output_lines(
                spec,
                ("01;OFOBTUPD_20260806.TXT;20260807", body, "03;1"),
            )
        with self.assertRaises(ValueError):
            validate_output_lines(spec, ("01;;20260807", body, "03;2"))

    def test_filename_has_no_clock_component(self) -> None:
        spec = spec_for_code("IFOBTUPD")
        first = physical_filename(spec, DATE, "000000")
        second = physical_filename(spec, DATE, "235959")

        self.assertEqual(first, second)
        self.assertTrue(re.fullmatch(r"OFOBTUPD_[0-9]{8}\.TXT", first))


if __name__ == "__main__":
    unittest.main()
