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


PROD_2789583_EVIDENCE = {
    "upload_rows": 6,
    "upload_statuses": {"P": 6},
    "branch_rows": {"A": 0, "B": 0, "C": 6},
    "union_rows": 6,
    "record_references": (
        4501366046,
        4501366047,
        4501366048,
        4501366049,
        4501366050,
        4501366051,
    ),
    "output_statuses": {"S": 6},
    "document_types": {"1": 6},
    "check_lookup_rows": 0,
    "input_physical_filename": "IFCHKPRT_20260807.TXT",
    "output_filename": "OFCHKPRT_20260807.TXT",
    "file_date": "20260807",
    "payload_bytes": 831,
    "payload_sha256": "1b0ec53281c4f9c346989f855059fa583801107787230465d52988679e66669a",
}


SYNTHETIC_LINES = (
    "HDR;IFCHKPRT_20260807.TXT;20260807;",
    "BDY;SC1;001;001;123456;CIF1;CLIENTE;1;000001;001;SEC;CLP;"
    "123450;1000;CLG1;X1;PR1;R1;20260807;;S;",
    "BDY;SC2;002;002;654321;CIF2;CLIENTE2;01;000002;002;SEC;USD;"
    "500000;0;;X2;;R2;20260807;C;E;E1;Mensaje A~;",
    "TLR;1;1;2;",
)
SYNTHETIC_PAYLOAD = ("\n".join(SYNTHETIC_LINES) + "\n").encode("utf-8")
SYNTHETIC_SHA256 = "e8f297b891cef73cd231b503150952a4387c67c689f378e82881ee0abbf8336e"


def _hex(value: str) -> str:
    return value.encode("utf-8").hex().upper()


def _body_row(
    raw_status: str,
    *,
    union_count: str = "1",
    rec_ref_count: str = "1",
    rec_ref: str = "4501366046",
    upload_ref_count: str = "1",
    check_match_count: str = "0",
    message_dependency_count: str = "0",
    scode: str = "SC1",
    txn_brn: str = "001",
    rem_brn: str = "001",
    rem_account: str = "123456",
    rem_cif: str = "CIF1",
    rem_detail: str = "CLIENTE",
    document_type: str = "1",
    instrno1: str = "000001",
    bank_code: str = "001",
    sector_code: str = "SEC",
    txn_ccy: str = "CLP",
    txn_amount: str = "123450",
    tax_amount: str = "1000",
    clg_trn_ref: str = "CLG1",
    xref: str = "X1",
    prot_ref_no: str = "PR1",
    reason_code: str = "R1",
    protest_date: str = "20260807",
    check_status: str = "",
    error_code: str = "",
    error_param: str = "",
) -> str:
    status_class = "S" if raw_status == "S" else "<NON_S>"
    values = (
        status_class,
        raw_status,
        union_count,
        rec_ref_count,
        rec_ref,
        upload_ref_count,
        check_match_count,
        message_dependency_count,
        scode,
        txn_brn,
        rem_brn,
        rem_account,
        rem_cif,
        rem_detail,
        document_type,
        instrno1,
        bank_code,
        sector_code,
        txn_ccy,
        txn_amount,
        tax_amount,
        clg_trn_ref,
        xref,
        prot_ref_no,
        reason_code,
        protest_date,
        check_status,
        error_code,
        error_param,
    )
    return "|".join(_hex(value) for value in values)


@dataclass(frozen=True)
class _UnionFixture:
    rec_ref: int
    payload: tuple[str, ...]


def _safe_oracle_union(*branches: tuple[_UnionFixture, ...]) -> tuple[_UnionFixture, ...]:
    """Reference rule: UNION removes exact rows, then REC_REF must order uniquely."""
    unique = set().union(*branches)
    grouped: dict[int, int] = {}
    for row in unique:
        grouped[row.rec_ref] = grouped.get(row.rec_ref, 0) + 1
    tied = sorted(rec_ref for rec_ref, count in grouped.items() if count > 1)
    if tied:
        raise ValueError(
            "OFCHKPRT has distinct UNION rows tied on REC_REF: "
            + ", ".join(str(value) for value in tied)
        )
    return tuple(sorted(unique, key=lambda row: row.rec_ref))


def _qa_check_status(
    document_type: str | None,
    matches: tuple[tuple[int, str], ...],
) -> str:
    normalized = (document_type or "").strip() or "01"
    if normalized != "01" or not matches:
        return ""
    latest_mod = max(mod_no for mod_no, _status in matches)
    latest = [status for mod_no, status in matches if mod_no == latest_mod]
    # SELECT INTO catches NO_DATA_FOUND/TOO_MANY_ROWS and emits NULL.
    return latest[0] if len(latest) == 1 else ""


def _qa_error_description(
    codes: str,
    params: str,
    messages: dict[str, str],
) -> str:
    if not codes:
        return params
    groups = str(params or "").split(";")
    return "".join(
        format_form_message(
            code,
            groups[index] if index < len(groups) else "",
            messages,
        )
        + "~"
        for index, code in enumerate(split_oacmclos_error_codes(codes))
    )


def _central_mapping_is_ready() -> bool:
    try:
        return spec_for_code("IFCHKPRT").output_code == "OFCHKPRT"
    except ValueError:
        return False


class OfchkprtPreparedContractTests(unittest.TestCase):
    def test_prod_2789583_evidence_is_complete_and_unambiguous(self) -> None:
        evidence = PROD_2789583_EVIDENCE
        self.assertEqual(evidence["upload_rows"], 6)
        self.assertEqual(evidence["branch_rows"], {"A": 0, "B": 0, "C": 6})
        self.assertEqual(sum(evidence["branch_rows"].values()), 6)
        self.assertEqual(evidence["union_rows"], 6)
        self.assertEqual(len(set(evidence["record_references"])), 6)
        self.assertEqual(evidence["output_statuses"], {"S": 6})
        # Current PROD value is literal "1", so QA does not enter its
        # NVL(TRIM(DOCUMENT_TYPE), '01') = '01' cheque-status lookup.
        self.assertEqual(evidence["document_types"], {"1": 6})
        self.assertEqual(evidence["check_lookup_rows"], 0)
        self.assertRegex(evidence["payload_sha256"], r"^[0-9a-f]{64}$")

    def test_union_deduplicates_exact_rows_but_never_invents_a_tie_order(self) -> None:
        first = _UnionFixture(10, ("X", "S"))
        second = _UnionFixture(11, ("Y", "E"))
        self.assertEqual(
            _safe_oracle_union((first,), (first, second), ()),
            (first, second),
        )
        with self.assertRaisesRegex(ValueError, "tied on REC_REF: 10"):
            _safe_oracle_union(
                (first,),
                (_UnionFixture(10, ("DIFFERENT", "E")),),
                (),
            )

    def test_check_status_reproduces_document_type_and_select_into_rules(self) -> None:
        self.assertEqual(_qa_check_status("1", ((1, "C"),)), "")
        self.assertEqual(_qa_check_status(None, ()), "")
        self.assertEqual(_qa_check_status("01", ((1, "A"), (2, "C"))), "C")
        self.assertEqual(
            _qa_check_status("01", ((2, "A"), (2, "C"))),
            "",
        )

    def test_error_messages_are_positional_tilde_terminated_and_stop_at_eopl(self) -> None:
        self.assertEqual(
            _qa_error_description(
                "E1;E2;EOPL",
                "A~;B~;IGNORED~;",
                {"E1": "Uno $1!", "E2": "Dos $1!"},
            ),
            "Uno A~Dos B~",
        )

    def test_synthetic_client_bytes_are_frozen(self) -> None:
        self.assertEqual(len(SYNTHETIC_PAYLOAD), 248)
        self.assertEqual(hashlib.sha256(SYNTHETIC_PAYLOAD).hexdigest(), SYNTHETIC_SHA256)
        self.assertNotIn(b"\r", SYNTHETIC_PAYLOAD)
        self.assertEqual(
            [len(line[:-1].split(";")) for line in SYNTHETIC_LINES],
            [3, 21, 23, 4],
        )


@unittest.skipUnless(
    _central_mapping_is_ready(),
    "IFCHKPRT->OFCHKPRT central adapter is intentionally not integrated yet",
)
class OfchkprtCentralAdapterContractTests(unittest.TestCase):
    """Automatically activates once the central mapping is registered."""

    def test_filename_header_footer_validator_and_exact_serialization(self) -> None:
        spec = spec_for_code("IFCHKPRT")
        self.assertEqual(spec.output_code, "OFCHKPRT")
        self.assertEqual(
            physical_filename(spec, "20260807"),
            "OFCHKPRT_20260807.TXT",
        )
        self.assertEqual(
            build_header(
                spec,
                "20260807",
                input_physical_filename="IFCHKPRT_20260807.TXT",
            ),
            SYNTHETIC_LINES[0],
        )
        self.assertEqual(
            build_footer(spec, 2, {"S": 1, "<NON_S>": 1}),
            SYNTHETIC_LINES[-1],
        )
        validate_output_lines(spec, SYNTHETIC_LINES)
        self.assertEqual(serialize_lines(SYNTHETIC_LINES), SYNTHETIC_PAYLOAD)

    def test_body_query_has_all_three_union_branches_and_no_write_path(self) -> None:
        script = build_body_query(
            "2789583",
            "IFCHKPRT",
            DataSourceChoice.ACTIVE,
            "20260807",
        ).script
        compact = " ".join(script.lower().split())
        self.assertIn("from gitu_upload_master", compact)
        self.assertIn("gitm_protest_log", compact)
        self.assertIn("cgtb_protest_reject_upload", compact)
        self.assertIn("catm_protest_master", compact)
        self.assertIn("catm_check_details", compact)
        self.assertIn("ertb_msgs", compact)
        self.assertNotIn("cgtbs_protest_reject_upload", compact)
        self.assertNotIn("catms_protest_master", compact)
        self.assertEqual(len(re.findall(r"\bunion\b", compact)), 2)
        self.assertNotIn("union all", compact)
        self.assertIn("status <> 'p'", compact)
        self.assertIn("pru.status <> 's'", compact)
        self.assertIn("pru.status = 's'", compact)
        self.assertIn("fld29", compact)
        self.assertIn("fm999999999999999v999", compact)
        self.assertIn("order by rec_ref", compact)
        self.assertNotIn("order by rec_ref,", compact)
        self.assertNotIn("fn_handoff", compact)
        self.assertNotIn("gipks_", compact)
        self.assertNotRegex(
            compact,
            r"\b(?:insert|update|delete|merge|commit|rollback)\b",
        )
        with self.assertRaisesRegex(ValueError, "archive"):
            build_body_query(
                "2789583",
                "IFCHKPRT",
                DataSourceChoice.ARCHIVE,
                "20260807",
            )

    def test_parser_renderer_and_integrity_metadata_match_qa(self) -> None:
        spec = spec_for_code("IFCHKPRT")
        rows = [
            _body_row("S", union_count="2"),
            _body_row(
                "E",
                union_count="2",
                rec_ref="4501366047",
                check_match_count="1",
                message_dependency_count="1",
                scode="SC2",
                txn_brn="002",
                rem_brn="002",
                rem_account="654321",
                rem_cif="CIF2",
                rem_detail="CLIENTE2",
                document_type="01",
                instrno1="000002",
                bank_code="002",
                txn_ccy="USD",
                txn_amount="500000",
                tax_amount="0",
                clg_trn_ref="",
                xref="X2",
                prot_ref_no="",
                reason_code="R2",
                check_status="C",
                error_code="E1",
                error_param="A~;",
            ),
        ]

        records, counts = _parse_body_records(rows, spec)
        self.assertEqual(counts, {"S": 1, "<NON_S>": 1})
        self.assertEqual(_collect_error_codes(spec, records), {"E1"})
        self.assertEqual(
            _render_body_records(spec, records, {"E1": "Mensaje $1!"}),
            list(SYNTHETIC_LINES[1:3]),
        )

    def test_parser_rejects_empty_incomplete_or_tied_union_results(self) -> None:
        spec = spec_for_code("IFCHKPRT")
        with self.assertRaisesRegex(OutputFileGenerationError, "no reconstructible"):
            _parse_body_records([], spec)
        with self.assertRaisesRegex(OutputFileGenerationError, "incomplete"):
            _parse_body_records([_body_row("S", union_count="2")], spec)
        with self.assertRaisesRegex(OutputFileGenerationError, "tied on REC_REF"):
            _parse_body_records(
                [
                    _body_row(
                        "S",
                        union_count="2",
                        rec_ref_count="2",
                        rec_ref="10",
                    ),
                    _body_row(
                        "E",
                        union_count="2",
                        rec_ref_count="2",
                        rec_ref="10",
                    ),
                ],
                spec,
            )
        with self.assertRaisesRegex(OutputFileGenerationError, "one-to-one"):
            _parse_body_records(
                [_body_row("S", upload_ref_count="0")],
                spec,
            )

    def test_active_generation_writes_the_frozen_client_payload(self) -> None:
        first = _body_row("S", union_count="2")
        second = _body_row(
            "E",
            union_count="2",
            rec_ref="4501366047",
            check_match_count="1",
            message_dependency_count="1",
            scode="SC2",
            txn_brn="002",
            rem_brn="002",
            rem_account="654321",
            rem_cif="CIF2",
            rem_detail="CLIENTE2",
            document_type="01",
            instrno1="000002",
            bank_code="002",
            txn_ccy="USD",
            txn_amount="500000",
            tax_amount="0",
            clg_trn_ref="",
            xref="X2",
            prot_ref_no="",
            reason_code="R2",
            check_status="C",
            error_code="E1",
            error_param="A~;",
        )
        filename_metadata = "1|" + _hex("IFCHKPRT_20260807.TXT")
        message_metadata = _hex("E1") + "|1|" + _hex("Mensaje $1!")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sqlcl_path = root / "sql.exe"
            sqlcl_path.touch()
            runner = _QueuedSqlclRunner(
                (
                    ["UPLOAD_MASTER|ACTIVE|IFCHKPRT|2"],
                    ["IFCHKPRT|OFCHKPRT"],
                    ["20260807|1|1"],
                    [filename_metadata],
                    [first, second],
                    [message_metadata],
                    [first, second],
                    ["20260807|1|1"],
                    [filename_metadata],
                    [message_metadata],
                    ["2|1|1"],
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
                GenerationRequest(target=target, process_ref_no="2789583")
            )

            self.assertIs(result.source, DataSourceChoice.ACTIVE)
            self.assertEqual(result.output_path.name, "OFCHKPRT_20260807.TXT")
            self.assertEqual(result.lines, SYNTHETIC_LINES)
            self.assertEqual(result.output_path.read_bytes(), SYNTHETIC_PAYLOAD)
            self.assertEqual(result.sha256, SYNTHETIC_SHA256)
            self.assertEqual(runner.pending, 0)
            body_scripts = [
                script
                for script in runner.scripts
                if "from CGTB_PROTEST_REJECT_UPLOAD" in script
            ]
            self.assertEqual(len(body_scripts), 2)

    def test_generation_aborts_when_a_live_union_source_changes(self) -> None:
        original = _body_row("S")
        changed = _body_row("S", scode="CHANGED")
        filename_metadata = "1|" + _hex("IFCHKPRT_20260807.TXT")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sqlcl_path = root / "sql.exe"
            sqlcl_path.touch()
            runner = _QueuedSqlclRunner(
                (
                    ["UPLOAD_MASTER|ACTIVE|IFCHKPRT|1"],
                    ["IFCHKPRT|OFCHKPRT"],
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
            target = OracleTarget(
                country="chile",
                database_key="prod-db",
                credential_key="shared-prod",
                tns="FXBFCL_19C_PROD_OCI",
                label="Chile PROD",
            )

            with self.assertRaisesRegex(
                OutputFileGenerationError,
                "active process changed",
            ):
                service.generate(
                    GenerationRequest(target=target, process_ref_no="2789583")
                )
            self.assertEqual(runner.pending, 0)
            self.assertFalse((root / "output").exists())

    def test_input_physical_filename_is_active_only_and_unambiguous(self) -> None:
        active = build_input_physical_filename_query(
            "2789583",
            "IFCHKPRT",
            DataSourceChoice.ACTIVE,
        ).script
        compact = " ".join(active.lower().split())
        self.assertIn("from gitb_file_master", compact)
        self.assertIn("select distinct trim(phy_file_name)", compact)
        with self.assertRaisesRegex(ValueError, "archive"):
            build_input_physical_filename_query(
                "2789583",
                "IFCHKPRT",
                DataSourceChoice.ARCHIVE,
            )


if __name__ == "__main__":
    unittest.main()
