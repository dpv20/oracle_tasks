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
    gl_code: str = "GL-100",
    alt_gl_no: str = "ALT-200",
    error: str = "",
    error_param: str = "",
) -> str:
    values = (
        normalized_status,
        raw_status,
        gl_code,
        alt_gl_no,
        error,
        error_param,
    )
    return "|".join(_hex(value) for value in values)


def _star_field(value: str, width: int) -> str:
    text = str(value or "")
    return text[:width].ljust(width, "*") if text else "*" * width


def _expected_body(
    *,
    gl_code: str,
    alt_gl_no: str,
    status: str,
    error: str = "",
    description: str = "",
) -> str:
    return (
        "LB"
        + _star_field(gl_code, 36)
        + _star_field(alt_gl_no, 20)
        + _star_field(status, 7)
        + _star_field(error, 255)
        + _star_field(description, 255)
    )


class OfglmdfyContractTests(unittest.TestCase):
    def test_registry_filename_header_footer_and_archive_revalidation(self) -> None:
        spec = spec_for_code("IFGLMDFY")
        adapter = upload_body_adapter("IFGLMDFY")

        self.assertIsNotNone(adapter)
        assert adapter is not None
        self.assertEqual(spec.output_code, "OFGLMDFY")
        self.assertIs(spec_for_code("OFGLMDFY"), spec)
        self.assertEqual(spec.contract, adapter.style)
        self.assertEqual(spec.body_field_count, 575)
        self.assertEqual(spec.physical_name_pattern, "SP{dmy}{time}END")
        self.assertEqual(spec.error_message_mode, "list_prefixed")
        self.assertTrue(spec.revalidate_external_inputs)

        self.assertEqual(
            physical_filename(spec, DATE, TIME),
            "SP07082026142355END",
        )
        self.assertEqual(build_header(spec, DATE, TIME), "LH20260807")
        self.assertEqual(build_footer(spec, 1, {"P": 1}), "LF")

    def test_adapter_uses_cardinality_one_live_gl_lookup_and_not_fld6(self) -> None:
        adapter = upload_body_adapter("IFGLMDFY")
        self.assertIsNotNone(adapter)
        assert adapter is not None

        self.assertEqual(len(adapter.fields), 4)
        self.assertEqual(adapter.fields[0], "rtrim(u.fld4, ' ')")
        lookup = adapter.fields[1]
        self.assertIn("GLTM_GLMASTER_C", lookup)
        self.assertIn("count(*)", lookup.lower())
        self.assertIn("count(*) = 1", lookup.lower())
        self.assertIn("min(g.alt_gl_no)", lookup.lower())
        self.assertIn("g.gl_code = rtrim(u.fld4, ' ')", lookup)
        self.assertNotIn("u.fld6", " ".join(adapter.fields).lower())
        self.assertEqual(adapter.fields[-2:], ("u.error", "u.error_param"))

    def test_active_and_archive_queries_are_scoped_ordered_selects(self) -> None:
        adapter = upload_body_adapter("IFGLMDFY")
        self.assertIsNotNone(adapter)
        assert adapter is not None

        for source, table in (
            (DataSourceChoice.ACTIVE, "GITU_UPLOAD_MASTER"),
            (DataSourceChoice.ARCHIVE, "GITA_UPLOAD_MASTER"),
        ):
            with self.subTest(source=source.value):
                script = build_body_query("7654321", "IFGLMDFY", source).script
                self.assertIn(f"from {table} u", script)
                self.assertIn("u.process_ref_no = '7654321'", script)
                self.assertIn(
                    "upper(trim(u.interface_code)) = 'IFGLMDFY'",
                    script,
                )
                self.assertIn("order by u.record_reference", script)
                self.assertIn("GLTM_GLMASTER_C", script)
                self.assertNotIn("u.fld6", script.lower())
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

    def test_processed_body_has_exact_575_character_boundaries(self) -> None:
        spec = spec_for_code("IFGLMDFY")
        records, counts = _parse_body_records(
            [_adapter_row("P", "P")],
            spec,
        )
        body = _render_body_records(spec, records, {})[0]

        self.assertEqual(counts, {"P": 1})
        self.assertEqual(
            body,
            _expected_body(gl_code="GL-100", alt_gl_no="ALT-200", status="P"),
        )
        self.assertEqual(len(body), 575)
        self.assertEqual(body[:2], "LB")
        self.assertEqual(body[2:38], _star_field("GL-100", 36))
        self.assertEqual(body[38:58], _star_field("ALT-200", 20))
        self.assertEqual(body[58:65], _star_field("P", 7))
        self.assertEqual(body[65:320], "*" * 255)
        self.assertEqual(body[320:575], "*" * 255)

    def test_missing_gl_lookup_is_twenty_stars(self) -> None:
        spec = spec_for_code("IFGLMDFY")
        records, _counts = _parse_body_records(
            [_adapter_row("P", "P", alt_gl_no="")],
            spec,
        )
        body = _render_body_records(spec, records, {})[0]

        self.assertEqual(body[38:58], "*" * 20)
        self.assertEqual(len(body), 575)

    def test_every_fixed_field_is_truncated_at_its_qa_width(self) -> None:
        spec = spec_for_code("IFGLMDFY")
        records, _counts = _parse_body_records(
            [
                _adapter_row(
                    "STATUS-IS-LONG",
                    "STATUS-IS-LONG",
                    gl_code="G" * 40,
                    alt_gl_no="A" * 25,
                    error="E" * 260,
                    error_param="",
                )
            ],
            spec,
        )
        body = _render_body_records(spec, records, {})[0]

        self.assertEqual(len(body), 575)
        self.assertEqual(body[2:38], "G" * 36)
        self.assertEqual(body[38:58], "A" * 20)
        self.assertEqual(body[58:65], "STATUS-")
        self.assertEqual(body[65:320], "E" * 255)

    def test_error_descriptions_are_semicolon_prefixed_and_parameter_matched(self) -> None:
        spec = spec_for_code("IFGLMDFY")
        records, counts = _parse_body_records(
            [
                _adapter_row(
                    "E",
                    "E",
                    error="E-ONE;E-TWO;",
                    error_param="FIRST;SECOND;",
                )
            ],
            spec,
        )

        self.assertEqual(counts, {"E": 1})
        self.assertEqual(_collect_error_codes(spec, records), {"E-ONE", "E-TWO"})
        description = ";First FIRST;Second SECOND"
        body = _render_body_records(
            spec,
            records,
            {"E-ONE": "First $1", "E-TWO": "Second $1"},
        )[0]
        self.assertEqual(
            body,
            _expected_body(
                gl_code="GL-100",
                alt_gl_no="ALT-200",
                status="E",
                error="E-ONE;E-TWO;",
                description=description,
            ),
        )
        self.assertEqual(body[320 : 320 + len(description)], description)
        self.assertEqual(len(body), 575)

    def test_missing_error_template_uses_prefixed_ovpks_fallback(self) -> None:
        spec = spec_for_code("IFGLMDFY")
        records, _counts = _parse_body_records(
            [_adapter_row("E", "E", error="UNKNOWN;", error_param="VALUE;")],
            spec,
        )
        body = _render_body_records(spec, records, {})[0]

        self.assertTrue(body[320:].startswith(";Missing Error Code"))
        self.assertEqual(len(body), 575)

    def test_complete_payload_is_lf_terminated_and_validated(self) -> None:
        spec = spec_for_code("IFGLMDFY")
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
        payload = serialize_lines(lines)
        self.assertEqual(payload, ("\n".join(lines) + "\n").encode("utf-8"))
        self.assertTrue(payload.startswith(b"LH20260807\nLB"))
        self.assertTrue(payload.endswith(b"\nLF\n"))

    def test_validator_rejects_wrong_width_header_and_footer(self) -> None:
        spec = spec_for_code("IFGLMDFY")
        body = _expected_body(gl_code="GL", alt_gl_no="ALT", status="P")

        with self.assertRaises(ValueError):
            validate_output_lines(spec, ("LH07082026", body, "LF"))
        with self.assertRaises(ValueError):
            validate_output_lines(spec, ("LH20260807", body[:-1], "LF"))
        with self.assertRaises(ValueError):
            validate_output_lines(spec, ("LH20260807", body, "LF;"))

    def test_filename_requires_a_valid_database_clock(self) -> None:
        spec = spec_for_code("IFGLMDFY")

        with self.assertRaises(ValueError):
            physical_filename(spec, DATE, "246060")
        filename = physical_filename(spec, DATE, TIME)
        self.assertTrue(re.fullmatch(r"SP[0-9]{14}END", filename))


if __name__ == "__main__":
    unittest.main()
