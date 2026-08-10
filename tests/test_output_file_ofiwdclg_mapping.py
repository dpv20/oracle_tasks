from __future__ import annotations

import hashlib
import re
import sys
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path


SRC_DIR = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_DIR))

from features.output_file_generation.formats import (  # noqa: E402
    build_footer,
    build_header,
    format_form_message,
    physical_filename,
    serialize_lines,
    spec_for_code,
    split_oacmclos_error_codes,
    validate_output_lines,
)
from features.output_file_generation import (  # noqa: E402
    GenerationRequest,
    OracleTarget,
    OutputFileGenerationService,
)
from features.output_file_generation.models import DataSourceChoice  # noqa: E402
from features.output_file_generation.queries import (  # noqa: E402
    build_body_query,
    build_input_physical_filename_query,
)
from features.output_file_generation.service import (  # noqa: E402
    OutputFileGenerationError,
    _collect_error_codes,
    _parse_body_records,
    _render_body_records,
)
from tests.test_output_file_generation import (  # noqa: E402
    _FakeConfig,
    _QueuedSqlclRunner,
)


# SELECT-only PROD evidence captured on 2026-08-07.  The upload/clearing
# transaction rows survived, but the package's mandatory GITM_CLEARING_LOG rows
# did not.  Consequently, the QA cursor returns zero rows and a useful 462-line
# reconstruction would have to invent DOCTYPE, CLEARINGTYPE and cursor order.
PROD_2807504_EVIDENCE = {
    "upload_rows": 462,
    "upload_statuses": {"P": 462},
    "clearing_log_rows": 0,
    "branch_rows": {"A": 0, "B": 0, "C": 0},
    "ifc_statuses": {"ERR": 3, "SUCC": 459},
    "upload_to_ifc_cardinality": (462, 462, 462),
    "master_statuses": {"REJR": 6, "SUCC": 453},
    "reject_error_lists": {"CG-REJR-09;": 5, "CG-REJR-09;CG-REJR-03;": 1},
    "spanish_message_rows": {"CG-REJR-03": 1, "CG-REJR-09": 1, "IF-IW002": 1},
    "input_physical_filename": "IFIWDCLG_20260806.TXT",
    "file_date": "20260807",
    "output_filename": "OFIWDCLG_20260807.TXT",
    # This is the exact *current-source* QA-cursor emulation.  It is retained
    # only as blocker evidence and must not be presented as the requested file.
    "empty_cursor_payload_bytes": 45,
    "empty_cursor_payload_sha256": (
        "ac5d8c0affd09a14456f30ffd1a8fb766bc2bd5a230a19c9bd64e314239955d8"
    ),
}


SYNTHETIC_LINES = (
    "HDR;IFIWDCLG_20260806.TXT;20260807;",
    "BDY;X1;20260806;001;123;000111;9001;CH;1500;20260806;LOCAL;SEC;"
    "P;SUCC;FCC1;",
    "BDY;X2;20260806;002;124;000222;9002;CH;2500.5;20260806;LOCAL;SEC;"
    "P;REJR;FCC2;CL-REJ;Rechazado~;",
    "BDY;X3;20260806;003;125;000333;9003;CH;999;20260806;LOCAL;SEC;"
    "E;ERRO;FCC3;IF-IW002;No válido~;",
    "FTR;2;1;",
)
SYNTHETIC_PAYLOAD = ("\n".join(SYNTHETIC_LINES) + "\n").encode("utf-8")
SYNTHETIC_SHA256 = "bb6445da608b7fc43f1726b2a8ada0a4c745a34cd7019fbf942812c659e487c7"


def _hex(value: str) -> str:
    return value.encode("utf-8").hex().upper()


def _body_row(
    raw_status: str,
    *,
    union_count: str = "1",
    record_ref_count: str = "1",
    record_reference: str = "10",
    upload_ref_count: str = "1",
    log_ref_count: str = "1",
    ifc_match_count: str = "1",
    master_match_count: str = "1",
    xref: str = "X1",
    txn_date: str = "20260806",
    ben_bank: str = "001",
    rem_branch: str = "123",
    rem_account: str = "000111",
    instr_no: str = "9001",
    document_type: str = "CH",
    instr_amount: str = "1500",
    instr_date: str = "20260806",
    clearing_type: str = "LOCAL",
    sector_code: str = "SEC",
    txn_status: str = "SUCC",
    fcc_ref: str = "FCC1",
    error_code: str = "",
    error_param: str = "",
) -> str:
    status_class = "P" if raw_status == "P" else "<NON_P>"
    values = (
        status_class,
        raw_status,
        union_count,
        record_ref_count,
        record_reference,
        upload_ref_count,
        log_ref_count,
        ifc_match_count,
        master_match_count,
        xref,
        txn_date,
        ben_bank,
        rem_branch,
        rem_account,
        instr_no,
        document_type,
        instr_amount,
        instr_date,
        clearing_type,
        sector_code,
        txn_status,
        fcc_ref,
        error_code,
        error_param,
    )
    return "|".join(_hex(value) for value in values)


@dataclass(frozen=True)
class _CursorRow:
    record_reference: int
    payload: tuple[str, ...]


def _safe_qa_union(*branches: tuple[_CursorRow, ...]) -> tuple[_CursorRow, ...]:
    """Model QA UNION and reject its unresolved RECORD_REFERENCE ties."""
    unique = set().union(*branches)
    counts: dict[int, int] = {}
    for row in unique:
        counts[row.record_reference] = counts.get(row.record_reference, 0) + 1
    tied = sorted(key for key, count in counts.items() if count > 1)
    if tied:
        raise ValueError(
            "OFIWDCLG has distinct UNION rows tied on RECORD_REFERENCE: "
            + ", ".join(str(value) for value in tied)
        )
    return tuple(sorted(unique, key=lambda row: row.record_reference))


def _qa_error_description(
    codes: str,
    params: str,
    messages: dict[str, str],
) -> str:
    """Model PR_GET_ERRMSG: positional semicolon groups and trailing tildes."""
    param_groups = str(params or "").split(";")
    return "".join(
        format_form_message(
            code,
            param_groups[index] if index < len(param_groups) else "",
            messages,
        )
        + "~"
        for index, code in enumerate(split_oacmclos_error_codes(codes))
    )


def _central_mapping_is_ready() -> bool:
    try:
        return spec_for_code("IFIWDCLG").output_code == "OFIWDCLG"
    except ValueError:
        return False


class OfiwdclgPreparedContractTests(unittest.TestCase):
    def test_prod_2807504_proves_the_required_live_log_is_missing(self) -> None:
        evidence = PROD_2807504_EVIDENCE
        self.assertEqual(evidence["upload_rows"], 462)
        self.assertEqual(evidence["upload_statuses"], {"P": 462})
        self.assertEqual(evidence["clearing_log_rows"], 0)
        self.assertEqual(evidence["branch_rows"], {"A": 0, "B": 0, "C": 0})
        self.assertEqual(evidence["ifc_statuses"], {"ERR": 3, "SUCC": 459})
        self.assertEqual(sum(evidence["ifc_statuses"].values()), 462)
        # total join rows, distinct upload rows, distinct IFTB rows
        self.assertEqual(evidence["upload_to_ifc_cardinality"], (462, 462, 462))
        self.assertEqual(evidence["master_statuses"], {"REJR": 6, "SUCC": 453})
        self.assertEqual(sum(evidence["master_statuses"].values()), 459)
        self.assertEqual(sum(evidence["reject_error_lists"].values()), 6)
        self.assertTrue(all(count == 1 for count in evidence["spanish_message_rows"].values()))

    def test_empty_cursor_hash_is_blocker_evidence_not_a_valid_reconstruction(self) -> None:
        evidence = PROD_2807504_EVIDENCE
        payload = (
            "HDR;IFIWDCLG_20260806.TXT;20260807;\n"
            "FTR;0;0;\n"
        ).encode("utf-8")
        self.assertEqual(len(payload), evidence["empty_cursor_payload_bytes"])
        self.assertEqual(
            hashlib.sha256(payload).hexdigest(),
            evidence["empty_cursor_payload_sha256"],
        )
        self.assertGreater(evidence["upload_rows"], 0)
        self.assertEqual(evidence["clearing_log_rows"], 0)

    def test_union_deduplicates_exact_rows_and_refuses_ambiguous_ties(self) -> None:
        first = _CursorRow(10, ("X", "P", "SUCC"))
        second = _CursorRow(11, ("Y", "E", "ERRO"))
        self.assertEqual(
            _safe_qa_union((first,), (first, second), ()),
            (first, second),
        )
        with self.assertRaisesRegex(ValueError, "tied on RECORD_REFERENCE: 10"):
            _safe_qa_union(
                (first,),
                (_CursorRow(10, ("DIFFERENT", "P", "REJR")),),
                (),
            )

    def test_error_messages_are_positional_and_tilde_terminated(self) -> None:
        self.assertEqual(
            _qa_error_description(
                "E1;E2;EOPL",
                "A~;B~;IGNORED~;",
                {"E1": "Uno $1!", "E2": "Dos $1!"},
            ),
            "Uno A~Dos B~",
        )

    def test_synthetic_client_bytes_and_variable_body_width_are_frozen(self) -> None:
        self.assertEqual(len(SYNTHETIC_PAYLOAD), 313)
        self.assertEqual(hashlib.sha256(SYNTHETIC_PAYLOAD).hexdigest(), SYNTHETIC_SHA256)
        self.assertNotIn(b"\r", SYNTHETIC_PAYLOAD)
        self.assertIn("No válido".encode("utf-8"), SYNTHETIC_PAYLOAD)
        self.assertNotIn("No vÃ¡lido".encode("utf-8"), SYNTHETIC_PAYLOAD)
        self.assertEqual(
            [len(line[:-1].split(";")) for line in SYNTHETIC_LINES],
            [3, 15, 17, 17, 3],
        )


@unittest.skipUnless(
    _central_mapping_is_ready(),
    "IFIWDCLG->OFIWDCLG central adapter is intentionally not integrated yet",
)
class OfiwdclgCentralAdapterContractTests(unittest.TestCase):
    """Automatically activates once the central mapping is registered."""

    def test_filename_header_footer_validator_and_exact_serialization(self) -> None:
        spec = spec_for_code("IFIWDCLG")
        self.assertEqual(spec.output_code, "OFIWDCLG")
        self.assertEqual(
            physical_filename(spec, "20260807"),
            "OFIWDCLG_20260807.TXT",
        )
        self.assertEqual(
            build_header(
                spec,
                "20260807",
                input_physical_filename="IFIWDCLG_20260806.TXT",
            ),
            SYNTHETIC_LINES[0],
        )
        # QA counts UPLDSTAT=P as processed even when TXN_STATUS=REJR.
        self.assertEqual(build_footer(spec, 3, {"P": 2, "E": 1}), SYNTHETIC_LINES[-1])
        validate_output_lines(spec, SYNTHETIC_LINES)
        self.assertEqual(serialize_lines(SYNTHETIC_LINES), SYNTHETIC_PAYLOAD)

    def test_body_query_has_all_three_qa_union_branches_and_no_write_path(self) -> None:
        script = build_body_query(
            "2807504",
            "IFIWDCLG",
            DataSourceChoice.ACTIVE,
            "20260807",
        ).script
        compact = " ".join(script.lower().split())
        self.assertIn("gitu_upload_master", compact)
        self.assertIn("gitm_clearing_log", compact)
        self.assertIn("iftb_clearing_upload", compact)
        self.assertIn("cstb_clearing_master", compact)
        self.assertEqual(len(re.findall(r"\bunion\b", compact)), 2)
        self.assertNotIn("union all", compact)
        self.assertIn("status <> 'p'", compact)
        self.assertIn("nvl(ifc.status, 'err') = 'err'", compact)
        self.assertIn("ifc.status = 'succ'", compact)
        self.assertIn("to_char(r.instramt, 'tm9'", compact)
        self.assertIn("nls_numeric_characters=''.,''", compact)
        self.assertIn("order by record_reference", compact)
        self.assertNotIn("order by record_reference,", compact)
        self.assertNotIn("fn_handoff", compact)
        self.assertNotRegex(
            compact,
            r"\b(?:insert|update|delete|merge|commit|rollback)\b",
        )
        with self.assertRaisesRegex(ValueError, "archive"):
            build_body_query(
                "2807504",
                "IFIWDCLG",
                DataSourceChoice.ARCHIVE,
                "20260807",
            )

    def test_parser_renderer_and_error_rules_match_qa(self) -> None:
        spec = spec_for_code("IFIWDCLG")
        rows = [
            _body_row("P", union_count="3"),
            _body_row(
                "P",
                union_count="3",
                record_reference="11",
                xref="X2",
                ben_bank="002",
                rem_branch="124",
                rem_account="000222",
                instr_no="9002",
                instr_amount="2500.5",
                txn_status="REJR",
                fcc_ref="FCC2",
                error_code="CL-REJ",
            ),
            _body_row(
                "E",
                union_count="3",
                record_reference="12",
                master_match_count="0",
                xref="X3",
                ben_bank="003",
                rem_branch="125",
                rem_account="000333",
                instr_no="9003",
                instr_amount="999",
                txn_status="ERRO",
                fcc_ref="FCC3",
                error_code="IF-IW002",
            ),
        ]

        records, counts = _parse_body_records(rows, spec)
        self.assertEqual(counts, {"P": 2, "<NON_P>": 1})
        self.assertEqual(
            _collect_error_codes(spec, records),
            {"CL-REJ", "IF-IW002"},
        )
        self.assertEqual(
            _render_body_records(
                spec,
                records,
                {"CL-REJ": "Rechazado", "IF-IW002": "No válido"},
            ),
            list(SYNTHETIC_LINES[1:4]),
        )

    def test_parser_rejects_empty_incomplete_logs_cardinality_and_ties(self) -> None:
        spec = spec_for_code("IFIWDCLG")
        with self.assertRaisesRegex(OutputFileGenerationError, "GITM_CLEARING_LOG"):
            _parse_body_records([], spec)
        with self.assertRaisesRegex(OutputFileGenerationError, "incomplete"):
            _parse_body_records([_body_row("P", union_count="2")], spec)
        with self.assertRaisesRegex(OutputFileGenerationError, "clearing-log"):
            _parse_body_records([_body_row("P", log_ref_count="0")], spec)
        with self.assertRaisesRegex(OutputFileGenerationError, "clearing upload"):
            _parse_body_records([_body_row("E", ifc_match_count="2")], spec)
        with self.assertRaisesRegex(OutputFileGenerationError, "tied on RECORD_REFERENCE"):
            _parse_body_records(
                [
                    _body_row(
                        "P",
                        union_count="2",
                        record_ref_count="2",
                    ),
                    _body_row(
                        "E",
                        union_count="2",
                        record_ref_count="2",
                        master_match_count="0",
                        txn_status="ERRO",
                    ),
                ],
                spec,
            )

    def test_generation_rejects_2807504_style_empty_body(self) -> None:
        filename_metadata = "1|" + _hex("IFIWDCLG_20260806.TXT")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sqlcl_path = root / "sql.exe"
            sqlcl_path.touch()
            runner = _QueuedSqlclRunner(
                (
                    ["UPLOAD_MASTER|ACTIVE|IFIWDCLG|462"],
                    ["IFIWDCLG|OFIWDCLG"],
                    ["20260807|1|1"],
                    [filename_metadata],
                    [],
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
                "GITM_CLEARING_LOG",
            ):
                service.generate(
                    GenerationRequest(target=target, process_ref_no="2807504")
                )
            self.assertEqual(runner.pending, 0)
            self.assertFalse((root / "output").exists())

    def test_generation_rejects_partial_body_and_live_source_changes(self) -> None:
        filename_metadata = "1|" + _hex("IFIWDCLG_20260806.TXT")
        target = OracleTarget(
            country="chile",
            database_key="prod-db",
            credential_key="shared-prod",
            tns="FXBFCL_19C_PROD_OCI",
            label="Chile PROD",
        )

        with self.subTest(case="partial"):
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                sqlcl_path = root / "sql.exe"
                sqlcl_path.touch()
                runner = _QueuedSqlclRunner(
                    (
                        ["UPLOAD_MASTER|ACTIVE|IFIWDCLG|2"],
                        ["IFIWDCLG|OFIWDCLG"],
                        ["20260807|1|1"],
                        [filename_metadata],
                        [_body_row("P")],
                    )
                )
                service = OutputFileGenerationService(
                    _FakeConfig(sqlcl_path),
                    runner_factory=lambda _path: runner,
                    decryptor=lambda _encrypted: "fake-password",
                    output_root=root / "output",
                )
                with self.assertRaisesRegex(
                    OutputFileGenerationError,
                    "mandatory clearing-log data is incomplete",
                ):
                    service.generate(
                        GenerationRequest(target=target, process_ref_no="2807504")
                    )
                self.assertEqual(runner.pending, 0)

        with self.subTest(case="live_change"):
            original = _body_row("P")
            changed = _body_row("P", instr_amount="1500.25")
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                sqlcl_path = root / "sql.exe"
                sqlcl_path.touch()
                runner = _QueuedSqlclRunner(
                    (
                        ["UPLOAD_MASTER|ACTIVE|IFIWDCLG|1"],
                        ["IFIWDCLG|OFIWDCLG"],
                        ["20260807|1|1"],
                        [filename_metadata],
                        [original],
                        [changed],
                    )
                )
                service = OutputFileGenerationService(
                    _FakeConfig(sqlcl_path),
                    runner_factory=lambda _path: runner,
                    decryptor=lambda _encrypted: "fake-password",
                    output_root=root / "output",
                )
                with self.assertRaisesRegex(
                    OutputFileGenerationError,
                    "active process changed",
                ):
                    service.generate(
                        GenerationRequest(target=target, process_ref_no="2807504")
                    )
                self.assertEqual(runner.pending, 0)

    def test_input_physical_filename_is_active_only_and_unambiguous(self) -> None:
        active = build_input_physical_filename_query(
            "2807504",
            "IFIWDCLG",
            DataSourceChoice.ACTIVE,
        ).script
        compact = " ".join(active.lower().split())
        self.assertIn("from gitb_file_master", compact)
        self.assertIn("select distinct trim(phy_file_name)", compact)
        with self.assertRaisesRegex(ValueError, "archive"):
            build_input_physical_filename_query(
                "2807504",
                "IFIWDCLG",
                DataSourceChoice.ARCHIVE,
            )


if __name__ == "__main__":
    unittest.main()
