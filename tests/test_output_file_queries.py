from __future__ import annotations

import sys
import unittest
from pathlib import Path


SRC_DIR = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_DIR))

from features.output_file_generation.models import DataSourceChoice  # noqa: E402
from features.output_file_generation.queries import (  # noqa: E402
    build_body_query,
    build_database_time_query,
    build_discovery_query,
    build_error_messages_query,
    build_file_date_query,
    build_footer_status_query,
    build_input_physical_filename_query,
    build_mapping_query,
    build_mappings_query,
    build_oficowcg_coverage_query,
    build_process_contract_query,
    output_outside_markers,
    parse_framed_output,
)


class OutputFileQueryTests(unittest.TestCase):
    def test_discovery_is_framed_and_checks_both_stores(self) -> None:
        query = build_discovery_query("1234567")
        compact = " ".join(query.script.lower().split())
        self.assertIn("whenever sqlerror exit sql.sqlcode", query.script.lower())
        self.assertIn("set heading off", query.script.lower())
        self.assertIn("GITU_UPLOAD_MASTER", query.script)
        self.assertIn("GITA_UPLOAD_MASTER", query.script)
        self.assertIn("GITB_FILE_LOG", query.script)
        self.assertIn("GITA_FILE_LOG", query.script)
        self.assertIn("'UPLOAD_MASTER|ACTIVE|'", query.script)
        self.assertIn("'FILE_LOG|ARCHIVE|'", query.script)
        self.assertIn("process_ref_no = '1234567'", query.script)
        self.assertEqual(
            compact.count("upper(trim(interface_code)) <> 'chisalca'"),
            2,
        )
        self.assertIn("'upload_master|active|chisalca|' || count(*)", compact)
        self.assertIn("'upload_master|archive|chisalca|' || row_count", compact)
        self.assertIn("with archive_log_keys as", compact)
        self.assertIn("join gita_upload_master u", compact)
        self.assertIn("u.branch_code is null", compact)
        self.assertIn("index(u inx01_gita_upload_master)", compact)
        self.assertIn("u.target_table = 'detb_upload_rtl_teller'", compact)
        self.assertNotIn("or target_table = 'detb_upload_rtl_teller'", compact)
        self.assertNotIn("count(*) || '|'", query.script)

    def test_manual_discovery_is_restricted_to_the_selected_interface(self) -> None:
        query = build_discovery_query("1234567", "ifdobiel")
        compact = " ".join(query.script.lower().split())

        self.assertEqual(query.purpose, "DISCOVERY_SELECTED")
        self.assertIn("from gitu_upload_master", compact)
        self.assertIn("from gita_upload_master", compact)
        self.assertIn("from gitb_file_log", compact)
        self.assertIn("from gita_file_log", compact)
        self.assertEqual(
            compact.count("upper(trim(interface_code)) = 'ifdobiel'"),
            4,
        )
        self.assertNotIn("group by upper(trim(interface_code))", compact)

    def test_manual_chisalca_discovery_uses_indexed_archive_log_keys(self) -> None:
        query = build_discovery_query("2744251", "CHISALCA")
        compact = " ".join(query.script.lower().split())

        self.assertEqual(query.purpose, "DISCOVERY_CHISALCA")
        self.assertIn("with archive_log_keys as", compact)
        self.assertIn("from gita_file_log l", compact)
        self.assertIn("leading(k) use_nl(u)", compact)
        self.assertIn("index(u inx01_gita_upload_master)", compact)
        self.assertIn("u.external_system = k.external_system", compact)
        self.assertIn("u.archival_date = k.archival_date", compact)
        self.assertIn("u.file_name = k.upload_file_name", compact)
        self.assertIn("u.target_table = 'detb_upload_rtl_teller'", compact)
        self.assertNotIn(
            "from gita_upload_master where process_ref_no = '2744251'",
            compact,
        )

    def test_mapping_query_accepts_only_a_validated_observed_set(self) -> None:
        query = build_mappings_query(["ifdobiel", "ACCBLOCK", "IFDOBIEL"])

        self.assertIn("GITM_INTERFACE_DEFINITION", query.script)
        self.assertIn("in ('ACCBLOCK', 'IFDOBIEL')", query.script)
        self.assertIn("trim(outgoing_interface) is not null", query.script.lower())
        self.assertEqual(query.script.count("'IFDOBIEL'"), 1)

        with self.assertRaises(ValueError):
            build_mappings_query([])
        with self.assertRaises(ValueError):
            build_mappings_query(["IFDOBIEL'); delete from x--"])

    def test_file_date_comes_from_original_process_log_not_archive_date(self) -> None:
        active = build_file_date_query(
            "1234567",
            "IFDOBIEL",
            DataSourceChoice.ACTIVE,
        )
        archived = build_file_date_query(
            "1234567",
            "IFDOBIEL",
            DataSourceChoice.ARCHIVE,
        )
        self.assertIn("from GITB_FILE_LOG", active.script)
        self.assertIn("from GITA_FILE_LOG", archived.script)
        for query in (active, archived):
            self.assertIn("max(upload_date)", query.script)
            self.assertIn("max(cast(start_date_stamp as date))", query.script)
            self.assertIn("count(distinct trunc(upload_date))", query.script)
            self.assertNotIn("archival_date", query.script.lower())

    def test_process_contract_rechecks_upload_count_and_unique_mapping(self) -> None:
        query = build_process_contract_query(
            "1234567",
            "IFDOBIEL",
            "OFDOBIEL",
            DataSourceChoice.ARCHIVE,
        )
        compact = " ".join(query.script.lower().split())

        self.assertIn("from gita_upload_master", compact)
        self.assertIn("process_ref_no = '1234567'", compact)
        self.assertIn("gitm_interface_definition", compact)
        self.assertIn("trim(outgoing_interface) is not null", compact)
        self.assertIn("output_code = 'ofdobiel'", compact)

    def test_framed_parser_returns_only_query_rows(self) -> None:
        query = build_mapping_query("IFDOBIEL")
        stdout = (
            "Connected\r\n"
            f"{query.begin_marker}\r\n\f"
            "IFDOBIEL|OFDOBIEL\r\n"
            f"{query.end_marker}\r\n"
            "Disconnected\r\n"
        )
        self.assertEqual(parse_framed_output(stdout, query), ["IFDOBIEL|OFDOBIEL"])
        outside = output_outside_markers(stdout, query)
        self.assertIn("Connected", outside)
        self.assertIn("Disconnected", outside)
        self.assertNotIn("IFDOBIEL|OFDOBIEL", outside)

    def test_parser_rejects_missing_or_duplicate_markers(self) -> None:
        query = build_mapping_query("ACCBLOCK")
        with self.assertRaises(ValueError):
            parse_framed_output("no markers", query)
        duplicated = (
            f"{query.begin_marker}\n{query.begin_marker}\n"
            f"{query.end_marker}\n"
        )
        with self.assertRaises(ValueError):
            parse_framed_output(duplicated, query)

    def test_body_query_uses_allowlisted_selected_table(self) -> None:
        archived = build_body_query(
            "1234567", "IFDOBIEL", DataSourceChoice.ARCHIVE
        ).script
        self.assertIn("from GITA_UPLOAD_MASTER", archived)
        self.assertIn("trim(fld1) = 'BDY'", archived)
        self.assertIn("utl_i18n.string_to_raw(error", archived.lower())
        self.assertNotIn("gipks_#cldpymnt", archived.lower())

        active = build_body_query(
            "7654321", "ACCBLOCK", DataSourceChoice.ACTIVE
        ).script
        self.assertIn("from GITU_UPLOAD_MASTER", active)
        self.assertIn("interface_code)) = 'ACCBLOCK'", active)
        self.assertIn("order by record_reference", active.lower())

    def test_body_query_returns_status_and_body_in_one_snapshot(self) -> None:
        query = build_body_query(
            "1234567",
            "IFDOBIEL",
            DataSourceChoice.ARCHIVE,
        )
        self.assertIn("nvl(upper(trim(status)), '<NULL>') || '|'", query.script)
        self.assertIn("trim(fld1) = 'BDY'", query.script)

    def test_oflocrec_body_query_matches_qa_contract_and_is_deterministic(self) -> None:
        query = build_body_query(
            "2309481",
            "IFLOCREC",
            DataSourceChoice.ARCHIVE,
        )
        compact = " ".join(query.script.lower().split())

        self.assertIn("from gita_upload_master", compact)
        self.assertIn("trim(fld1) || ';' || trim(fld2) || ';'", compact)
        self.assertIn("trim(fld4) || ';' || trim(fld3) || ';'", compact)
        self.assertIn("decode(status, 'p', 'y', 'e', 'n')", compact)
        self.assertIn("error || ';' || error_param || ';'", compact)
        self.assertIn("trim(fld1) = 'bdy'", compact)
        self.assertIn("order by record_reference", compact)
        self.assertNotIn("gipks_", compact)
        self.assertNotIn("fn_handoff", compact)

    def test_oflocrec_footer_status_query_counts_all_upload_rows(self) -> None:
        query = build_footer_status_query(
            "2309481",
            "IFLOCREC",
            DataSourceChoice.ARCHIVE,
        )
        compact = " ".join(query.script.lower().split())

        self.assertIn("from gita_upload_master", compact)
        self.assertIn("and status = 'p'", compact)
        self.assertIn("and status = 'e'", compact)
        self.assertNotIn("group by", compact)
        self.assertNotIn("trim(fld1) = 'bdy'", compact)
        with self.assertRaisesRegex(ValueError, "does not use"):
            build_footer_status_query(
                "2309481",
                "IFDOBIEL",
                DataSourceChoice.ARCHIVE,
            )

    def test_oacmclos_body_query_is_hex_safe_and_matches_qa_contract(self) -> None:
        query = build_body_query(
            "2479263",
            "IACMCLOS",
            DataSourceChoice.ARCHIVE,
        )
        compact = " ".join(query.script.lower().split())

        self.assertIn("from gita_upload_master", compact)
        self.assertIn("process_ref_no = '2479263'", compact)
        self.assertIn("interface_code)) = 'iacmclos'", compact)
        for field in ("fld3", "fld4", "error", "error_param"):
            self.assertRegex(
                compact,
                rf"string_to_raw\(rtrim\({field}\), 'al32utf8'\)",
            )
        self.assertIn("order by record_reference", compact)
        self.assertNotIn("gipks_", compact)
        self.assertNotIn("fn_handoff", compact)
        self.assertNotRegex(
            compact,
            r"\b(?:insert|update|delete|merge|commit|rollback)\b",
        )

    def test_cladchgo_body_query_is_hex_safe_and_matches_qa_contract(self) -> None:
        for source, table in (
            (DataSourceChoice.ACTIVE, "gitu_upload_master"),
            (DataSourceChoice.ARCHIVE, "gita_upload_master"),
        ):
            with self.subTest(source=source):
                query = build_body_query("2806514", "CLADCHG", source)
                compact = " ".join(query.script.lower().split())

                self.assertIn(f"from {table}", compact)
                self.assertIn("process_ref_no = '2806514'", compact)
                self.assertIn("interface_code)) = 'cladchg'", compact)
                self.assertIn("string_to_raw(trim(fld2), 'al32utf8')", compact)
                self.assertIn("string_to_raw(trim(fld99), 'al32utf8')", compact)
                self.assertIn("string_to_raw(error, 'al32utf8')", compact)
                self.assertIn("string_to_raw(error_param, 'al32utf8')", compact)
                self.assertIn("order by record_reference", compact)
                self.assertNotIn("gipks_", compact)
                self.assertNotIn("fn_handoff", compact)
                self.assertNotRegex(
                    compact,
                    r"\b(?:insert|update|delete|merge|commit|rollback)\b",
                )

    def test_ofcrelvp_body_query_is_hex_safe_and_matches_qa_contract(self) -> None:
        query = build_body_query(
            "2513888",
            "IFCRELVP",
            DataSourceChoice.ARCHIVE,
        )
        compact = " ".join(query.script.lower().split())

        self.assertIn("from gita_upload_master", compact)
        self.assertIn("process_ref_no = '2513888'", compact)
        self.assertIn("interface_code)) = 'ifcrelvp'", compact)
        self.assertIn("rtrim(ltrim(fld3, chr(32)), chr(32))", compact)
        self.assertIn("rtrim(fld5, chr(32))", compact)
        for field in ("fld2", "fld3", "fld5", "fld7", "fld8"):
            self.assertIn("string_to_raw", compact)
            self.assertRegex(compact, rf"string_to_raw\([^)]*{field}")
        self.assertIn("order by record_reference", compact)
        # Deliberately inspect all statuses: QA excludes U only from the body,
        # while its footer counts it.  The service must abort that asymmetry.
        self.assertNotIn("status <> 'u'", compact)
        self.assertNotIn("gipks_", compact)
        self.assertNotIn("fn_handoff", compact)
        self.assertNotRegex(compact, r"\b(?:insert|update|delete|merge|commit)\b")

    def test_oficowcg_filename_query_uses_only_active_file_master(self) -> None:
        query = build_input_physical_filename_query(
            "2806536",
            "IFICOWCG",
            DataSourceChoice.ACTIVE,
        )
        compact = " ".join(query.script.lower().split())

        self.assertIn("from gitb_file_master", compact)
        self.assertIn("process_ref_no = '2806536'", compact)
        self.assertIn("gitm_interface_definition", compact)
        self.assertIn("gitm_file_names", compact)
        self.assertIn("m.file_name = h.file_name", compact)
        self.assertIn("m.interface_code = 'ificowcg'", compact)
        self.assertIn("m.upload_status = 'p'", compact)
        self.assertIn("m.process_code = 'fp'", compact)
        self.assertIn("rawtohex(utl_i18n.string_to_raw(", compact)
        self.assertNotIn("gita_file", compact)
        with self.assertRaisesRegex(ValueError, "archived file master"):
            build_input_physical_filename_query(
                "2806536",
                "IFICOWCG",
                DataSourceChoice.ARCHIVE,
            )

    def test_oficowcg_body_query_reproduces_both_qa_cursor_branches(self) -> None:
        query = build_body_query(
            "2806536",
            "IFICOWCG",
            DataSourceChoice.ACTIVE,
            "20260806",
        )
        compact = " ".join(query.script.lower().split())

        self.assertIn("from gitu_upload_master giu, gitm_clearing_log gic", compact)
        self.assertIn("from iftb_clearing_upload ifc, gitm_clearing_log gic", compact)
        self.assertIn("trim(giu.fld27) = gic.xref", compact)
        self.assertIn("ltrim(giu.fld28, 0) = gic.entry_no", compact)
        self.assertIn("trim(giu.fld2) = gic.instrno2", compact)
        self.assertIn("giu.record_reference = gic.record_reference", compact)
        self.assertIn("giu.status <> 'p'", compact)
        self.assertIn("and not exists", compact)
        self.assertIn("decode(ifc.status, 'succ', 'p', 'err', 'e')", compact)
        self.assertIn("decode(giu.status, 'u', 'gi-int214', giu.error)", compact)
        self.assertIn("to_date('20260806', 'yyyymmdd') txn_dt", compact)
        self.assertIn("rawtohex(utl_i18n.string_to_raw(err_code", compact)
        self.assertIn("order by xref, entry_no, fccref", compact)
        self.assertEqual(
            compact.count("upper(trim(gic.interface_code)) = 'ificowcg'"),
            2,
        )
        self.assertNotIn("gipks_", compact)
        self.assertNotIn("fn_handoff", compact)
        self.assertNotRegex(compact, r"\b(?:insert|update|delete|merge|commit)\b")
        with self.assertRaisesRegex(ValueError, "archive reconstruction"):
            build_body_query(
                "2806536",
                "IFICOWCG",
                DataSourceChoice.ARCHIVE,
                "20260806",
            )

    def test_oficowcg_coverage_query_proves_per_upload_identity(self) -> None:
        query = build_oficowcg_coverage_query("2806536")
        compact = " ".join(query.script.lower().split())

        self.assertIn("with uploads as", compact)
        self.assertIn("from gitu_upload_master", compact)
        self.assertIn("from gitm_clearing_log gic", compact)
        self.assertIn("iftb_clearing_upload ifc", compact)
        self.assertIn("gic.record_reference = u.record_reference", compact)
        self.assertIn("trim(u.fld27) = gic.xref", compact)
        self.assertIn("ltrim(u.fld28, 0) = gic.entry_no", compact)
        self.assertIn("trim(u.fld2) = gic.instrno2", compact)
        self.assertIn("branch_a_count", compact)
        self.assertIn("branch_b_count", compact)
        self.assertIn("branch_b_ownership", compact)
        self.assertIn("upload_owner_count <> 1", compact)
        self.assertIn("count(distinct record_reference)", compact)
        self.assertGreaterEqual(
            compact.count("upper(trim(gic.interface_code)) = 'ificowcg'"),
            4,
        )
        self.assertNotIn("fn_handoff", compact)
        self.assertNotIn("gipks_", compact)
        self.assertNotRegex(
            compact,
            r"\b(?:insert|update|delete|merge|commit|rollback)\b",
        )

    def test_database_time_query_allowlists_package_clock_formats(self) -> None:
        self.assertIn(
            "to_char(sysdate, 'HHMISS')",
            build_database_time_query("HHMISS").script,
        )
        self.assertIn(
            "to_char(sysdate, 'HH24MISS')",
            build_database_time_query("HH24MISS").script,
        )
        self.assertIn(
            "to_char(sysdate, 'YYYYMMDDHH24MISS')",
            build_database_time_query("YYYYMMDDHH24MISS").script,
        )
        with self.assertRaises(ValueError):
            build_database_time_query("HHMISS') from dual; delete from x--")

    def test_invalid_values_never_reach_sql(self) -> None:
        with self.assertRaises(ValueError):
            build_discovery_query("1234567 or 1=1")
        with self.assertRaises(ValueError):
            build_mapping_query("IFDOBIEL'; drop table x--")
        with self.assertRaises(ValueError):
            build_body_query("1", "IFDOBIEL", "auto")

    def test_error_message_query_interpolates_only_utf8_hex(self) -> None:
        query = build_error_messages_query(["CL-ONE'", "CL-TWO"])
        self.assertNotIn("CL-ONE'", query.script)
        self.assertIn("434C2D4F4E4527", query.script)
        self.assertIn("ERTB_MSGS", query.script)


if __name__ == "__main__":
    unittest.main()
