from __future__ import annotations

import re
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
    physical_filename,
    serialize_lines,
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
from features.output_file_generation.upload_adapters import (  # noqa: E402
    upload_body_adapter,
)
from tests.test_output_file_generation import (  # noqa: E402
    _FakeConfig,
    _QueuedSqlclRunner,
)


DATE = "20260807"
TIME = "142355"


def _hex(value: str) -> str:
    return value.encode("utf-8").hex().upper()


def _body_row(
    raw_status: str,
    *,
    maintenance_no: str = "10001",
    customer_no: str = "000123456",
    message_count: str = "0",
    message: str = "",
    error: str = "",
    ignored_error_param: str = "",
) -> str:
    """Encode the two status columns plus the six STDCIFOM adapter fields."""
    normalized_status = raw_status.strip().upper() if raw_status else "<NULL>"
    values = (
        normalized_status,
        raw_status,
        maintenance_no,
        customer_no,
        message_count,
        message,
        error,
        ignored_error_param,
    )
    return "|".join(_hex(value) for value in values)


class StdcifomContractTests(unittest.TestCase):
    def test_registry_is_body_only_timestamp_named_and_archive_revalidated(self) -> None:
        spec = spec_for_code("STDCIFMO")
        adapter = upload_body_adapter("STDCIFMO")

        self.assertIsNotNone(adapter)
        assert adapter is not None
        self.assertEqual(spec.output_code, "STDCIFOM")
        self.assertIs(spec_for_code("STDCIFOM"), spec)
        self.assertEqual(spec.contract, adapter.style)
        self.assertEqual(spec.contract, "stdcifom")
        self.assertEqual(spec.body_field_count, 4)
        self.assertEqual(spec.error_body_field_count, 6)
        self.assertEqual(spec.allowed_statuses, ("P", "E"))
        self.assertFalse(spec.has_header)
        self.assertFalse(spec.has_footer)
        self.assertTrue(spec.revalidate_external_inputs)
        self.assertEqual(spec.database_time_format, "HH24MISS")
        self.assertEqual(spec.physical_name_pattern, "{date}{time}")
        self.assertEqual(physical_filename(spec, DATE, TIME), "20260807142355")

    def test_adapter_uses_exact_one_customer_and_stateful_ertb_metadata(self) -> None:
        adapter = upload_body_adapter("STDCIFMO")
        self.assertIsNotNone(adapter)
        assert adapter is not None

        self.assertEqual(len(adapter.fields), 6)
        self.assertEqual(adapter.fields[0], "rtrim(u.fld2)")

        customer_lookup = " ".join(adapter.fields[1].lower().split())
        self.assertIn("from sttm_upload_customer c", customer_lookup)
        self.assertIn("count(*) = 1", customer_lookup)
        self.assertIn("min(rtrim(c.customer_no))", customer_lookup)
        self.assertIn("c.maintenance_seq_no", customer_lookup)
        self.assertIn("u.fld2", customer_lookup)

        message_count = " ".join(adapter.fields[2].lower().split())
        direct_message = " ".join(adapter.fields[3].lower().split())
        for expression in (message_count, direct_message):
            self.assertIn("from ertb_msgs m", expression)
            self.assertIn("m.err_code", expression)
            self.assertIn("st-save-004", expression)
            self.assertIn("u.status = 'e'", expression)
            self.assertNotIn("m.language", expression)
        self.assertIn("count(*)", message_count)
        self.assertIn("min(rtrim(m.message))", direct_message)
        self.assertEqual(adapter.fields[-2:], ("rtrim(u.error)", "u.error_param"))
        self.assertEqual(adapter.filters, ("u.status in ('P', 'E')",))

    def test_active_and_archive_queries_are_scoped_ordered_selects(self) -> None:
        adapter = upload_body_adapter("STDCIFMO")
        self.assertIsNotNone(adapter)
        assert adapter is not None

        for source, table in (
            (DataSourceChoice.ACTIVE, "GITU_UPLOAD_MASTER"),
            (DataSourceChoice.ARCHIVE, "GITA_UPLOAD_MASTER"),
        ):
            with self.subTest(source=source.value):
                script = build_body_query("7654321", "STDCIFMO", source).script
                compact = " ".join(script.lower().split())
                self.assertIn(f"from {table} u", script)
                self.assertIn("u.process_ref_no = '7654321'", script)
                self.assertIn(
                    "upper(trim(u.interface_code)) = 'STDCIFMO'",
                    script,
                )
                self.assertIn("u.status in ('p', 'e')", compact)
                self.assertIn("order by u.record_reference", compact)
                self.assertIn("from sttm_upload_customer c", compact)
                self.assertIn("from ertb_msgs m", compact)
                for expression in adapter.fields:
                    self.assertIn(expression, script)
                self.assertEqual(
                    script.count("utl_i18n.string_to_raw("),
                    len(adapter.fields) + 2,
                )
                self.assertNotIn("FN_HANDOFF", script.upper())
                self.assertNotIn("GIPKS_", script.upper())
                self.assertNotRegex(
                    compact,
                    r"\b(?:insert|update|delete|merge|commit|rollback)\b",
                )

    def test_processed_and_error_rows_match_the_qa_caret_layout(self) -> None:
        spec = spec_for_code("STDCIFMO")
        records, counts = _parse_body_records(
            [
                _body_row("P"),
                _body_row(
                    "E",
                    maintenance_no="10002",
                    customer_no="000654321",
                    message_count="1",
                    message="Customer save failed",
                    error="CU-001",
                    ignored_error_param="MUST-NOT-APPEAR",
                ),
                _body_row(
                    "E",
                    maintenance_no="10003",
                    customer_no="",
                    message_count="1",
                    message="Default save failure",
                    error="",
                    ignored_error_param="IGNORED",
                ),
            ],
            spec,
        )

        self.assertEqual(counts, {"P": 1, "E": 2})
        self.assertEqual(_collect_error_codes(spec, records), set())
        self.assertEqual(
            _render_body_records(spec, records, {}),
            [
                "10001^000123456^P^",
                "10002^000654321^E^CU-001^Customer save failed^",
                "10003^^E^ST-SAVE-004^Default save failure^",
            ],
        )

    def test_missing_or_ambiguous_customer_lookup_is_rendered_empty(self) -> None:
        spec = spec_for_code("STDCIFMO")
        records, counts = _parse_body_records(
            [
                _body_row("P", maintenance_no="NO-ROW", customer_no=""),
                _body_row("P", maintenance_no="TWO-ROWS", customer_no=""),
            ],
            spec,
        )

        self.assertEqual(counts, {"P": 2})
        self.assertEqual(
            _render_body_records(spec, records, {}),
            ["NO-ROW^^P^", "TWO-ROWS^^P^"],
        )

    def test_failed_ertb_select_retains_the_previous_error_message(self) -> None:
        spec = spec_for_code("STDCIFMO")
        records, _counts = _parse_body_records(
            [
                _body_row(
                    "E",
                    maintenance_no="10001",
                    message_count="1",
                    message="First message",
                    error="CU-ONE",
                ),
                # A P row never executes the package's ERTB SELECT and must not
                # modify the message retained by the previous E row.
                _body_row(
                    "P",
                    maintenance_no="10002",
                    message_count="1",
                    message="Must not replace state",
                    error="IGNORED",
                ),
                _body_row(
                    "E",
                    maintenance_no="10003",
                    message_count="0",
                    message="",
                    error="CU-MISSING",
                ),
                _body_row(
                    "E",
                    maintenance_no="10004",
                    message_count="2",
                    message="Ambiguous minimum must be ignored",
                    error="CU-DUPLICATE",
                ),
                # One matching row whose MESSAGE is NULL succeeds and resets
                # L_ERR_DESC to NULL; the next failed lookup retains that NULL.
                _body_row(
                    "E",
                    maintenance_no="10005",
                    message_count="1",
                    message="",
                    error="CU-NULL-MESSAGE",
                ),
                _body_row(
                    "E",
                    maintenance_no="10006",
                    message_count="0",
                    message="",
                    error="CU-MISSING-AGAIN",
                ),
            ],
            spec,
        )

        self.assertEqual(
            _render_body_records(spec, records, {}),
            [
                "10001^000123456^E^CU-ONE^First message^",
                "10002^000123456^P^",
                "10003^000123456^E^CU-MISSING^First message^",
                "10004^000123456^E^CU-DUPLICATE^First message^",
                "10005^000123456^E^CU-NULL-MESSAGE^^",
                "10006^000123456^E^CU-MISSING-AGAIN^^",
            ],
        )

    def test_message_cardinality_metadata_must_be_a_non_negative_integer(self) -> None:
        spec = spec_for_code("STDCIFMO")

        for invalid in ("", "-1", "1.0", "many"):
            with self.subTest(message_count=invalid):
                with self.assertRaisesRegex(
                    OutputFileGenerationError,
                    "STDCIFOM message metadata",
                ):
                    _parse_body_records(
                        [_body_row("E", message_count=invalid, error="CU-1")],
                        spec,
                    )

    def test_body_only_payload_has_no_blank_header_or_footer(self) -> None:
        spec = spec_for_code("STDCIFMO")
        lines = (
            "10001^000123456^P^",
            "10002^^E^ST-SAVE-004^Unable to save^",
        )

        validate_output_lines(spec, lines)
        self.assertEqual(
            serialize_lines(lines),
            (
                "10001^000123456^P^\n"
                "10002^^E^ST-SAVE-004^Unable to save^\n"
            ).encode("utf-8"),
        )

        for malformed in (
            ("HDR;MO_STDCIF.txt;20260807;", *lines),
            (lines[0], lines[1], "FTR;2;"),
            ("10001^000123456^U^",),
            ("10001^000123456^P",),
            ("10002^^E^^Unable to save^",),
        ):
            with self.subTest(malformed=malformed):
                with self.assertRaises(ValueError):
                    validate_output_lines(spec, malformed)

    def test_archive_generation_revalidates_both_live_lookups(self) -> None:
        body_row = _body_row("P")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sqlcl_path = root / "sql.exe"
            sqlcl_path.touch()
            runner = _QueuedSqlclRunner(
                (
                    ["UPLOAD_MASTER|ARCHIVE|STDCIFMO|1"],
                    ["STDCIFMO|STDCIFOM"],
                    ["20260807|1|1"],
                    [body_row],
                    # The archived upload row is stable, but both live lookup
                    # tables are embedded in and fingerprinted by this rerun.
                    [body_row],
                    ["20260807|1|1"],
                    ["1|1|1"],
                    [TIME],
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
                GenerationRequest(target=target, process_ref_no="7654321")
            )

            self.assertIs(result.source, DataSourceChoice.ARCHIVE)
            self.assertEqual(result.output_path.name, "20260807142355")
            self.assertEqual(result.lines, ("10001^000123456^P^",))
            self.assertEqual(
                result.output_path.read_bytes(),
                b"10001^000123456^P^\n",
            )
            body_scripts = [
                script
                for script in runner.scripts
                if "from GITA_UPLOAD_MASTER u" in script
                and "from STTM_UPLOAD_CUSTOMER c" in script
                and "from ERTB_MSGS m" in script
            ]
            self.assertEqual(len(body_scripts), 2)
            self.assertEqual(runner.pending, 0)

    def test_filename_requires_a_valid_database_clock(self) -> None:
        spec = spec_for_code("STDCIFMO")

        with self.assertRaises(ValueError):
            physical_filename(spec, DATE, "246060")
        self.assertTrue(
            re.fullmatch(r"[0-9]{14}", physical_filename(spec, DATE, TIME))
        )


if __name__ == "__main__":
    unittest.main()
