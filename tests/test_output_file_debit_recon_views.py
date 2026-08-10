from __future__ import annotations

import hashlib
import unittest
from collections import Counter
from datetime import datetime


QA_CONTRACTS = {
    "IFDMCDRC": {
        "output": "OFDMCDRC",
        "view": "STVW_DEBIT_CARD_OFDMCDRC",
        "function": "FN_GET_OFDMCDRC",
        "archive_title": "==============REGISTROS EN ARCHIVO MCD==============",
        "filename_prefix": "MCDOUT_",
        "append_semicolon": False,
        "tracking_id": False,
    },
    "IFDPAYRC": {
        "output": "OFDPAYRC",
        "view": "STVW_OFDPAYRC",
        "function": "FN_GET_OFDPAYRC",
        # This is the literal QA text, including the apparent VSA copy/paste.
        "archive_title": "==============REGISTROS EN ARCHIVO VSA==============",
        "filename_prefix": "PAY30OUT_",
        "append_semicolon": False,
        "tracking_id": True,
    },
    "IFDTAPRC": {
        "output": "OFDTAPRC",
        "view": "STVW_OFDTAPRC",
        "function": "FN_GET_OFDTAPRC",
        "archive_title": "==============REGISTROS EN ARCHIVO TAP==============",
        "filename_prefix": "TAPIOUT_",
        "append_semicolon": True,
        "tracking_id": False,
    },
    "IFDVSARC": {
        "output": "OFDVSARC",
        "view": "STVW_DEBIT_CARD_OFDVSARC",
        "function": "FN_GET_OFDVSARC",
        "archive_title": "==============REGISTROS EN ARCHIVO VSA==============",
        "filename_prefix": "VSAOUT_",
        "append_semicolon": False,
        "tracking_id": False,
    },
}


QA_SOURCE_CONTRACT = {
    "process_section_table": "SWTB_DEM_RECON",
    "process_section_filters": (
        "UPLOAD_DATE = GLOBAL.APPLICATION_DATE",
        "INTERFACE_CODE = INPUT_CODE",
        "PROCESS_REF_NO = G_PROCESSREFNO",
    ),
    "process_section_order_by": (),
    "online_tables": ("SWTB_TXN_LOG", "SWTB_TXN_LOG_CU"),
    "online_filters": (
        "PURGE_DATE = GLOBAL.APPLICATION_DATE",
        "TXN_DESC = 'B'",
        "RECONSILED NOT IN ('F','A')",
        "BRAND CONFIGURATION / GIPKS_#STDMCDRC PACKAGE STATE",
    ),
    "online_order_by": ("TRN_REF_NO", "MSG_TYPE"),
}


# SELECT-only PROD evidence captured on 2026-08-07.  Counts are restricted to
# the exact SWTB_DEM_RECON target used by the QA incoming package.
PROD_ACTIVE_EVIDENCE = {
    "IFDPAYRC": {
        "process_ref_no": "2808504",
        "file_date": "20260807",
        "file_log_rows": 2,
        "file_log_distinct_dates": 1,
        "file_log_physical_names": 0,
        "file_master_rows": 2,
        "file_master_distinct_names": 1,
        "input_physical_filename": "PAY3007082026.txt",
        "upload_target_rows": 51_253,
        "upload_target_distinct_references": 51_253,
        "upload_target_statuses": {"U": 51_253},
        "dem_rows": 51_253,
        "dem_distinct_references": 51_253,
        "dem_distinct_transaction_ids": 51_253,
        "dem_groups": {
            ("F", "P", "SUCC"): 47,
            ("M", "P", "<NULL>"): 51_206,
        },
        "online_rows": 2,
        "online_distinct_transactions": 2,
        "online_distinct_custom_rows": 2,
        "online_statuses": {"R": 2},
        "online_ambiguous_order_keys": 0,
    },
    "IFDTAPRC": {
        "process_ref_no": "2808503",
        "file_date": "20260807",
        "file_log_rows": 2,
        "file_log_distinct_dates": 1,
        "file_log_physical_names": 0,
        "file_master_rows": 2,
        "file_master_distinct_names": 1,
        "input_physical_filename": "TAPI07082026.TXT",
        "upload_target_rows": 574,
        "upload_target_distinct_references": 574,
        "upload_target_statuses": {"U": 574},
        "dem_rows": 574,
        "dem_distinct_references": 574,
        "dem_distinct_transaction_ids": 574,
        "dem_groups": {
            ("F", "P", "SUCC"): 1,
            ("F", "E", "ERRR"): 35,
            ("M", "P", "<NULL>"): 538,
        },
        "online_rows": 2,
        "online_distinct_transactions": 2,
        "online_distinct_custom_rows": 2,
        "online_statuses": {"R": 2},
        "online_ambiguous_order_keys": 0,
    },
}


PROD_PROCESS_INVENTORY = {
    "IFDMCDRC": {
        "active": (),
        "archive": ("2806541", "2806012", "2798525"),
    },
    "IFDPAYRC": {
        "active": ("2808504",),
        "archive": ("2807012", "2805090", "2805075"),
    },
    "IFDTAPRC": {
        "active": ("2808503",),
        "archive": ("2806010", "2791005", "2789571"),
    },
    "IFDVSARC": {
        "active": (),
        "archive": ("2807015", "2806016", "2805096"),
    },
}


ONLINE_TITLE = "==============REGISTROS ONLINE REVERSADOS=============="
STANDARD_ARCHIVE_HEADER = (
    "LB*Cuenta*Tarjeta*TipoMovimiento*Monto*FechaTnx*"
    "Estado*CodError*ParamError*"
)
TRACKING_ARCHIVE_HEADER = STANDARD_ARCHIVE_HEADER + "TrackingId*"
STANDARD_ONLINE_HEADER = (
    "LB*Cuenta*Tarjeta*Monto*FechaTnx*NumRefTnx*Estado*CodError*ParamError*"
)
TRACKING_ONLINE_HEADER = STANDARD_ONLINE_HEADER + "TrackingId*"
TAPI_ARCHIVE_HEADER = (
    "LB*Cuenta*Tarjeta*TipoMovimiento*Monto*FechaTnx*"
    "Estado*CodError*ParamError*NumRefTnx"
)
TAPI_ONLINE_HEADER = (
    "LB*Cuenta*Tarjeta*Monto*FechaTnx*NumRefTnx*Estado*CodError*ParamError"
)


def _physical_filename(interface_code: str, file_date: str) -> str:
    contract = QA_CONTRACTS[interface_code]
    dmy = datetime.strptime(file_date, "%Y%m%d").strftime("%d%m%Y")
    return str(contract["filename_prefix"]) + dmy + ".TXT"


def _qa_package_payload(interface_code: str, view_values: tuple[str, ...]) -> bytes:
    """Model the outgoing wrapper around each pipelined view value."""
    suffix = ";\n" if QA_CONTRACTS[interface_code]["append_semicolon"] else "\n"
    return "".join(value + suffix for value in view_values).encode("utf-8")


def _exact_adapter_can_be_enabled(
    evidence: dict[str, object],
    *,
    process_cursor_has_order_by: bool,
    first_fingerprint: str,
    reread_fingerprint: str,
) -> bool:
    """Minimum exactness guard; a hash cannot repair unspecified row order."""
    dem_rows = int(evidence["dem_rows"])
    return (
        dem_rows == int(evidence["dem_distinct_references"])
        and dem_rows == int(evidence["upload_target_rows"])
        and int(evidence["file_log_distinct_dates"]) == 1
        and int(evidence["file_master_distinct_names"]) == 1
        and int(evidence["online_ambiguous_order_keys"]) == 0
        and bool(first_fingerprint)
        and first_fingerprint == reread_fingerprint
        and (dem_rows <= 1 or process_cursor_has_order_by)
    )


PAY_VIEW_VALUES = (
    "LH*",
    QA_CONTRACTS["IFDPAYRC"]["archive_title"],
    TRACKING_ARCHIVE_HEADER,
    "LB*0001*411111*10*500*0708120000*Match*P**ERR**TRACK-1*",
    "\n",
    ONLINE_TITLE,
    TRACKING_ONLINE_HEADER,
    "LB*0001*411111*500*0708120000*TRN-1*Reversado**ERR**TRACK-1*",
    "LF*",
)
PAY_PAYLOAD = _qa_package_payload("IFDPAYRC", PAY_VIEW_VALUES)


TAPI_VIEW_VALUES = (
    "LH*",
    QA_CONTRACTS["IFDTAPRC"]["archive_title"],
    TAPI_ARCHIVE_HEADER,
    "LB*0002*422222*10*600*0708120000*Inyectado*E*ERRR*E-1*PARAM*TRN-2",
    "\n",
    ONLINE_TITLE,
    TAPI_ONLINE_HEADER,
    "LB*0002*422222*600*0708120000*TRN-2*Reversado**E-2*PARAM",
    "LF*",
)
TAPI_PAYLOAD = _qa_package_payload("IFDTAPRC", TAPI_VIEW_VALUES)


class DebitReconPreparedContractTests(unittest.TestCase):
    def test_all_four_outputs_are_process_plus_live_date_global_hybrids(self) -> None:
        self.assertEqual(
            set(QA_CONTRACTS),
            {"IFDMCDRC", "IFDPAYRC", "IFDTAPRC", "IFDVSARC"},
        )
        self.assertEqual(QA_SOURCE_CONTRACT["process_section_order_by"], ())
        self.assertEqual(
            QA_SOURCE_CONTRACT["online_order_by"],
            ("TRN_REF_NO", "MSG_TYPE"),
        )
        self.assertIn(
            "PROCESS_REF_NO = G_PROCESSREFNO",
            QA_SOURCE_CONTRACT["process_section_filters"],
        )
        self.assertNotIn(
            "PROCESS_REF_NO = G_PROCESSREFNO",
            QA_SOURCE_CONTRACT["online_filters"],
        )

    def test_exact_qa_views_and_output_names_are_frozen(self) -> None:
        expected = {
            "IFDMCDRC": ("OFDMCDRC", "STVW_DEBIT_CARD_OFDMCDRC", "MCDOUT_07082026.TXT"),
            "IFDPAYRC": ("OFDPAYRC", "STVW_OFDPAYRC", "PAY30OUT_07082026.TXT"),
            "IFDTAPRC": ("OFDTAPRC", "STVW_OFDTAPRC", "TAPIOUT_07082026.TXT"),
            "IFDVSARC": ("OFDVSARC", "STVW_DEBIT_CARD_OFDVSARC", "VSAOUT_07082026.TXT"),
        }
        for code, (output, view, filename) in expected.items():
            with self.subTest(interface_code=code):
                self.assertEqual(QA_CONTRACTS[code]["output"], output)
                self.assertEqual(QA_CONTRACTS[code]["view"], view)
                self.assertEqual(_physical_filename(code, "20260807"), filename)

    def test_pay30_preserves_copy_paste_title_tracking_and_double_blank_row(self) -> None:
        self.assertEqual(
            QA_CONTRACTS["IFDPAYRC"]["archive_title"],
            "==============REGISTROS EN ARCHIVO VSA==============",
        )
        self.assertIn(b"*TrackingId*\n", PAY_PAYLOAD)
        # The function pipes CHR(10), then the wrapper appends another LF.
        self.assertIn(b"\n\n\n" + ONLINE_TITLE.encode("utf-8") + b"\n", PAY_PAYLOAD)
        self.assertNotIn(b";\n", PAY_PAYLOAD)
        self.assertTrue(PAY_PAYLOAD.endswith(b"LF*\n"))

    def test_tapi_wrapper_adds_semicolon_even_to_the_embedded_newline_row(self) -> None:
        self.assertTrue(TAPI_PAYLOAD.startswith(b"LH*;\n"))
        self.assertIn(TAPI_ARCHIVE_HEADER.encode("utf-8") + b";\n", TAPI_PAYLOAD)
        self.assertIn(
            b"\n\n;\n" + ONLINE_TITLE.encode("utf-8") + b";\n",
            TAPI_PAYLOAD,
        )
        self.assertTrue(TAPI_PAYLOAD.endswith(b"LF*;\n"))

    def test_active_process_rows_have_complete_unique_source_coverage(self) -> None:
        for code, evidence in PROD_ACTIVE_EVIDENCE.items():
            with self.subTest(interface_code=code):
                self.assertEqual(
                    evidence["dem_rows"],
                    evidence["dem_distinct_references"],
                )
                self.assertEqual(
                    evidence["dem_rows"],
                    evidence["dem_distinct_transaction_ids"],
                )
                self.assertEqual(
                    evidence["dem_rows"],
                    evidence["upload_target_rows"],
                )
                self.assertEqual(
                    evidence["upload_target_rows"],
                    evidence["upload_target_distinct_references"],
                )
                self.assertEqual(sum(evidence["dem_groups"].values()), evidence["dem_rows"])
                self.assertEqual(evidence["file_log_distinct_dates"], 1)
                self.assertEqual(evidence["file_master_distinct_names"], 1)

    def test_active_online_sections_are_current_clean_but_not_process_scoped(self) -> None:
        for code, evidence in PROD_ACTIVE_EVIDENCE.items():
            with self.subTest(interface_code=code):
                self.assertEqual(evidence["online_rows"], 2)
                self.assertEqual(evidence["online_distinct_transactions"], 2)
                self.assertEqual(evidence["online_distinct_custom_rows"], 2)
                self.assertEqual(evidence["online_statuses"], {"R": 2})
                self.assertEqual(evidence["online_ambiguous_order_keys"], 0)

    def test_pay_and_tapi_cannot_be_enabled_despite_stable_fingerprints(self) -> None:
        for code, evidence in PROD_ACTIVE_EVIDENCE.items():
            with self.subTest(interface_code=code):
                self.assertGreater(evidence["dem_rows"], 1)
                self.assertFalse(
                    _exact_adapter_can_be_enabled(
                        evidence,
                        process_cursor_has_order_by=False,
                        first_fingerprint="same-body-hash",
                        reread_fingerprint="same-body-hash",
                    )
                )

    def test_same_unordered_rows_can_produce_different_client_bytes(self) -> None:
        first = ("LB*A*", "LB*B*")
        second = tuple(reversed(first))
        self.assertEqual(Counter(first), Counter(second))
        self.assertNotEqual(
            _qa_package_payload("IFDPAYRC", first),
            _qa_package_payload("IFDPAYRC", second),
        )

    def test_only_pay_and_tapi_have_active_processes_in_the_observed_inventory(self) -> None:
        self.assertEqual(PROD_PROCESS_INVENTORY["IFDMCDRC"]["active"], ())
        self.assertEqual(PROD_PROCESS_INVENTORY["IFDVSARC"]["active"], ())
        self.assertEqual(PROD_PROCESS_INVENTORY["IFDPAYRC"]["active"], ("2808504",))
        self.assertEqual(PROD_PROCESS_INVENTORY["IFDTAPRC"]["active"], ("2808503",))

    def test_synthetic_payloads_are_lf_only_and_frozen(self) -> None:
        self.assertNotIn(b"\r", PAY_PAYLOAD)
        self.assertNotIn(b"\r", TAPI_PAYLOAD)
        self.assertEqual(len(PAY_PAYLOAD), 405)
        self.assertEqual(len(TAPI_PAYLOAD), 406)
        self.assertEqual(
            hashlib.sha256(PAY_PAYLOAD).hexdigest(),
            "fa4b9e1008f458356bfff8e2e680d525699f2efd2c6121ed5976a5184f7f01cd",
        )
        self.assertEqual(
            hashlib.sha256(TAPI_PAYLOAD).hexdigest(),
            "97de1d5bd22254aa415129aa6ed9cd3ea8e97eab57286840f006fcc6ea4d9f4c",
        )


if __name__ == "__main__":
    unittest.main()
