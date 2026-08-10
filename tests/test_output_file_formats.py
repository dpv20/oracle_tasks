from __future__ import annotations

import sys
import unittest
from pathlib import Path


SRC_DIR = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_DIR))

from features.output_file_generation.formats import (  # noqa: E402
    build_footer,
    build_header,
    format_error_code,
    format_error_description,
    format_form_message,
    format_oacmclos_error_descriptions,
    physical_filename,
    serialize_lines,
    split_error_codes,
    split_oacmclos_error_codes,
    spec_for_code,
    validate_file_date,
    validate_output_lines,
    validate_process_ref,
)


class OutputFileFormatTests(unittest.TestCase):
    def test_input_and_output_codes_resolve_the_same_spec(self) -> None:
        self.assertIs(spec_for_code("ifdobiel"), spec_for_code("OFDOBIEL"))
        self.assertIs(spec_for_code("accblock"), spec_for_code("ACCBLKOU"))
        self.assertIs(spec_for_code("iflocrec"), spec_for_code("OFLOCREC"))
        self.assertIs(spec_for_code("ificowcg"), spec_for_code("OFICOWCG"))
        self.assertIs(spec_for_code("ifcrelvp"), spec_for_code("OFCRELVP"))
        self.assertIs(spec_for_code("iacmclos"), spec_for_code("OACMCLOS"))
        self.assertIs(spec_for_code("cladchg"), spec_for_code("CLADCHGO"))

    def test_process_reference_and_date_are_strict(self) -> None:
        self.assertEqual(validate_process_ref("1234567"), "1234567")
        self.assertEqual(validate_file_date("20260731"), "20260731")
        for value in ("", "27A8503", "1 OR 1=1", "1" * 21):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_process_ref(value)
        for value in ("20260230", "31-07-2026", "2026731"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_file_date(value)

    def test_ofdobiel_contract(self) -> None:
        spec = spec_for_code("IFDOBIEL")
        lines = (
            build_header(spec, "20260731"),
            "BDY;C;000000001;E;P;;;",
            "BDY;C;000000002;D;E;GI-001;Descripción;",
            build_footer(spec, 2, {"P": 1, "E": 1}),
        )
        self.assertEqual(lines[0], "HDR;OFDOBIEL.txt;20260731;")
        self.assertEqual(lines[-1], "TLR;2;")
        self.assertEqual(physical_filename(spec, "20260731"), "OFDOBIEL_20260731.TXT")
        validate_output_lines(spec, lines)

    def test_accblkou_contract(self) -> None:
        spec = spec_for_code("ACCBLOCK")
        lines = (
            build_header(spec, "20240916", "142355"),
            "BDY;A;B;C;D;Y;;;",
            "BDY;A;B;C;D;N;ERR;Mensaje;",
            build_footer(spec, 2, {"P": 1, "E": 1}),
        )
        self.assertEqual(
            lines[0],
            "HDR;ACCBLKOU_20240916.TXT;20240916142355;",
        )
        self.assertEqual(lines[-1], "FTR;1;1;")
        validate_output_lines(spec, lines)

    def test_oflocrec_contract(self) -> None:
        spec = spec_for_code("IFLOCREC")
        lines = (
            build_header(spec, "20240517", "094512"),
            "BDY;000000001;100;CLP;Y;;;",
            "BDY;000000002;200;USD;N;E001;CUENTA~;",
            build_footer(spec, 2, {"P": 1, "E": 1}),
        )

        self.assertEqual(
            lines[0],
            "HDR;OFLOCREC_20240517094512;17052024094512;",
        )
        self.assertEqual(lines[-1], "FTR;1;1;")
        self.assertEqual(
            physical_filename(spec, "20240517"),
            "OFLOCREC_20240517.TXT",
        )
        validate_output_lines(spec, lines)

    def test_oflocrec_footer_may_count_non_body_upload_rows(self) -> None:
        spec = spec_for_code("IFLOCREC")
        lines = (
            build_header(spec, "20240517", "094512"),
            "BDY;000000001;100;CLP;Y;;;",
            "BDY;000000002;200;USD;N;E001;CUENTA~;",
            build_footer(spec, 2, {"P": 2, "E": 1}),
        )

        self.assertEqual(lines[-1], "FTR;2;1;")
        validate_output_lines(spec, lines)
        with self.assertRaisesRegex(ValueError, "cannot be smaller"):
            validate_output_lines(
                spec,
                (*lines[:-1], "FTR;0;1;"),
            )

    def test_oflocrec_requires_one_matching_header_time_and_valid_statuses(self) -> None:
        spec = spec_for_code("IFLOCREC")
        with self.assertRaisesRegex(ValueError, "dates do not match"):
            validate_output_lines(
                spec,
                (
                    "HDR;OFLOCREC_20240517094512;17052024104512;",
                    "FTR;0;0;",
                ),
            )
        with self.assertRaisesRegex(ValueError, "status must be Y or N"):
            validate_output_lines(
                spec,
                (
                    "HDR;OFLOCREC_20240517094512;17052024094512;",
                    "BDY;1;100;CLP;P;;;",
                    "FTR;1;0;",
                ),
            )

    def test_oacmclos_exact_header_body_footer_and_filename_contract(self) -> None:
        spec = spec_for_code("IACMCLOS")
        lines = (
            build_header(
                spec,
                "20240131",
                "142355",
                database_date="20260806",
            ),
            "BDY;999;SYN000000001;Y;;",
            "BDY;999;SYN000000002;N;ZZ-SYN001;Missing Error Code;",
            build_footer(spec, 2, {"P": 1, "E": 1}),
        )

        self.assertEqual(
            lines[0],
            "HDR;OACMCLOS_20240131142355;06082026140855;",
        )
        self.assertEqual(lines[-1], "FTR;1,1,")
        self.assertEqual(
            build_footer(spec, 3, {"P": 1, "E": 1, "U": 1}),
            "FTR;1,2,",
        )
        with self.assertRaisesRegex(ValueError, "NULL status"):
            build_footer(spec, 1, {"<NULL>": 1})
        self.assertEqual(
            physical_filename(spec, "20240131", "142355"),
            "OACMCLOS_20240131142355",
        )
        validate_output_lines(spec, lines)
        self.assertEqual(
            serialize_lines(lines),
            ("\n".join(lines) + "\n").encode("utf-8"),
        )

    def test_oacmclos_multi_error_param_and_eopl_rules(self) -> None:
        codes = "ZZ-SYN001;ZZ-SYN002;EOPL;ZZ-IGNORED"
        params = "UNO;DOS~TRES;IGNORED"
        messages = {
            "ZZ-SYN001": "Primero $1!",
            "ZZ-SYN002": "Segundo $1/$2",
        }

        self.assertEqual(
            split_oacmclos_error_codes(codes),
            ("ZZ-SYN001", "ZZ-SYN002"),
        )
        self.assertEqual(
            format_oacmclos_error_descriptions(codes, params, messages),
            ";Primero UNO;Segundo DOS/TRES",
        )
        self.assertEqual(
            codes
            + params
            + format_oacmclos_error_descriptions(codes, params, messages)
            + ";",
            (
                "ZZ-SYN001;ZZ-SYN002;EOPL;ZZ-IGNORED"
                "UNO;DOS~TRES;IGNORED;Primero UNO;Segundo DOS/TRES;"
            ),
        )
        self.assertEqual(
            format_oacmclos_error_descriptions("ZZ-MISSING", "", {}),
            ";Missing Error Code",
        )

    def test_oacmclos_rejects_footer_or_header_contract_drift(self) -> None:
        spec = spec_for_code("IACMCLOS")
        with self.assertRaisesRegex(ValueError, "footer"):
            validate_output_lines(
                spec,
                (
                    "HDR;OACMCLOS_20240131142355;06082026140855;",
                    "BDY;999;SYN000000001;Y;;",
                    "FTR;1;0;",
                ),
            )
        with self.assertRaisesRegex(ValueError, "do not match"):
            validate_output_lines(
                spec,
                ("HDR;OACMCLOS_20240131142355;06082026142355;", "FTR;0,0,"),
            )

    def test_cladchgo_exact_header_body_footer_and_filename_contract(self) -> None:
        spec = spec_for_code("CLADCHG")
        lines = (
            build_header(spec, "20260806"),
            "02ACC001^REF001^P^",
            "02ACC002^REF002^E^E-ONE;^Cuenta 2 inválida",
            build_footer(spec, 2, {"P": 1, "E": 1}),
        )

        self.assertEqual(lines[0], "01^20260806")
        self.assertEqual(lines[-1], "03^2")
        self.assertEqual(
            physical_filename(spec, "20260806"),
            "CLADCHGO_20260806.TXT",
        )
        validate_output_lines(spec, lines)
        self.assertEqual(
            serialize_lines(lines),
            ("\n".join(lines) + "\n").encode("utf-8"),
        )

    def test_cladchgo_rejects_status_delimiter_and_footer_drift(self) -> None:
        spec = spec_for_code("CLADCHG")
        with self.assertRaisesRegex(ValueError, "unsupported statuses"):
            build_footer(spec, 1, {"U": 1})
        with self.assertRaisesRegex(ValueError, "status must be P or E"):
            validate_output_lines(
                spec,
                ("01^20260806", "02ACC001^REF001^U^", "03^1"),
            )
        with self.assertRaises(ValueError):
            validate_output_lines(
                spec,
                ("01^20260806", "02ACC^INJECT^REF001^P^", "03^1"),
            )
        with self.assertRaisesRegex(ValueError, "footer count"):
            validate_output_lines(
                spec,
                ("01^20260806", "02ACC001^REF001^P^", "03^2"),
            )

    def test_oficowcg_contract_has_variable_error_fields(self) -> None:
        spec = spec_for_code("IFICOWCG")
        lines = (
            build_header(
                spec,
                "20260806",
                input_physical_filename="IFICOWCG_OP_20260806.TXT",
            ),
            "BDY;X1;10;001;20260806;BANK;ACCOUNT;I1;I2;SEC;100.25;C;P;",
            "BDY;X2;20;001;20260806;BANK;ACCOUNT;I3;I4;SEC;200;C;E;E-ONE;Cuenta 20;",
            "BDY;X3;30;001;20260806;BANK;ACCOUNT;I5;I6;SEC;300;C;U;GI-INT214;Pendiente;",
            build_footer(spec, 3, {"P": 1, "E": 1, "U": 1}),
        )

        self.assertEqual(lines[0], "HDR;IFICOWCG_OP_20260806.TXT;20260806;")
        self.assertEqual(lines[-1], "FTR;1;2;")
        self.assertEqual(
            physical_filename(spec, "20260806"),
            "OFICOWCG_20260806.TXT",
        )
        validate_output_lines(spec, lines)

        with self.assertRaisesRegex(ValueError, "must not contain error fields"):
            validate_output_lines(
                spec,
                (
                    lines[0],
                    "BDY;X1;10;001;20260806;BANK;ACCOUNT;I1;I2;SEC;100;C;P;ERR;Message;",
                    "FTR;1;0;",
                ),
            )
        with self.assertRaisesRegex(ValueError, "code and description"):
            validate_output_lines(
                spec,
                (
                    lines[0],
                    "BDY;X2;20;001;20260806;BANK;ACCOUNT;I3;I4;SEC;200;C;E;",
                    "FTR;0;1;",
                ),
            )

    def test_ofcrelvp_contract_uses_carets_without_trailing_delimiters(self) -> None:
        spec = spec_for_code("IFCRELVP")
        lines = (
            build_header(spec, "20240809"),
            "BH^001^123456789^ESN-1^Y",
            "BH^002^987654321^ESN-2^E001^PARAM",
            build_footer(spec, 2, {"P": 1, "E": 1}),
        )

        self.assertEqual(lines[0], "LH^20240809")
        self.assertEqual(lines[-1], "LF^1^1^2")
        self.assertTrue(all(not line.endswith("^") for line in lines))
        self.assertEqual(
            physical_filename(spec, "20240809", "142355"),
            "OFCRELVP_09082024_142355.TXT",
        )
        validate_output_lines(spec, lines)
        self.assertEqual(
            serialize_lines(lines),
            ("\n".join(lines) + "\n").encode("utf-8"),
        )

    def test_ofcrelvp_rejects_contract_drift(self) -> None:
        spec = spec_for_code("IFCRELVP")
        with self.assertRaisesRegex(ValueError, "must end with Y"):
            validate_output_lines(
                spec,
                ("LH^20240809", "BH^001^123^ESN^N", "LF^1^0^1"),
            )
        with self.assertRaisesRegex(ValueError, "must not end"):
            validate_output_lines(spec, ("LH^20240809^", "LF^0^0^0"))
        with self.assertRaisesRegex(ValueError, "footer does not match"):
            validate_output_lines(
                spec,
                ("LH^20240809", "BH^001^123^ESN^Y", "LF^0^1^1"),
            )
        with self.assertRaisesRegex(ValueError, "valid clock time"):
            physical_filename(spec, "20240809", "256099")

    def test_accblock_rejects_unrepresented_statuses(self) -> None:
        spec = spec_for_code("ACCBLOCK")
        with self.assertRaisesRegex(ValueError, "unsupported statuses"):
            build_footer(spec, 2, {"P": 1, "W": 1})
        with self.assertRaisesRegex(ValueError, "does not match"):
            build_footer(spec, 3, {"P": 1, "E": 1})
        with self.assertRaisesRegex(ValueError, "non-negative integers"):
            build_footer(spec, 1, {"P": -1, "E": 2})

    def test_ofdobiel_rejects_unrepresented_statuses(self) -> None:
        spec = spec_for_code("IFDOBIEL")
        with self.assertRaisesRegex(ValueError, "unsupported statuses"):
            build_footer(spec, 1, {"<NULL>": 1})
        with self.assertRaisesRegex(ValueError, "status counts"):
            build_footer(spec, 2, {"P": 1})
        with self.assertRaisesRegex(ValueError, "status must be P or E"):
            validate_output_lines(
                spec,
                ("HDR;OFDOBIEL.txt;20260731;", "BDY;C;1;E;X;;;", "TLR;1;"),
            )

    def test_ofdobiel_error_fields_match_package_rules(self) -> None:
        codes = ";E-ONE;E-TWO;"
        params = "123~;500~CLP~;"
        messages = {
            "E-ONE": "Cuenta $1 inv\u00e1lida!",
            "E-TWO": "Monto $1 $2 rechazado",
        }
        self.assertEqual(split_error_codes(codes), ("E-ONE", "E-TWO"))
        self.assertEqual(format_error_code(codes), "~E-ONE~E-TWO")
        self.assertEqual(
            format_error_description(codes, params, messages),
            "Cuenta 123 inv\u00e1lida~Monto 500 CLP rechazado",
        )
        self.assertEqual(
            format_error_description("I-SUCCESS;", "", {}),
            "I-SUCCESS",
        )
        self.assertEqual(
            format_error_description("E-ONE;", "123~;", {"E-ONE": "$1/$2"}),
            "123/$2",
        )
        self.assertEqual(
            format_error_description(
                "E-ONE;E-TWO;",
                "123~;",
                {"E-ONE": "$1", "E-TWO": "$1"},
            ),
            "123~$1",
        )

    def test_oficowcg_form_message_matches_ovpks_embedstr(self) -> None:
        messages = {"E-ONE": "Cuenta $1 por $2 inválida!"}
        self.assertEqual(
            format_form_message("E-ONE", "123~CLP~", messages),
            "Cuenta 123 por CLP inválida",
        )
        # FN_EMBEDSTR counts separators: without a trailing second separator,
        # only the first placeholder is replaced.
        self.assertEqual(
            format_form_message("E-ONE", "123~CLP", messages),
            "Cuenta 123 por $2 inválida",
        )
        self.assertEqual(
            format_form_message("UNKNOWN", "", messages),
            "Missing Error Code",
        )

    def test_validator_rejects_delimiter_or_newline_drift(self) -> None:
        spec = spec_for_code("IFDOBIEL")
        with self.assertRaisesRegex(ValueError, "7 semicolon"):
            validate_output_lines(
                spec,
                ("HDR;OFDOBIEL.txt;20260731;", "BDY;C;1;E;P;;", "TLR;1;"),
            )
        with self.assertRaisesRegex(ValueError, "line breaks"):
            validate_output_lines(
                spec,
                ("HDR;OFDOBIEL.txt;20260731;", "BDY;C;1;E;P;bad\nvalue;;", "TLR;1;"),
            )
        with self.assertRaisesRegex(ValueError, "footer count"):
            validate_output_lines(
                spec,
                ("HDR;OFDOBIEL.txt;20260731;", "BDY;C;1;E;P;;;", "TLR;2;"),
            )

    def test_invalid_clock_time_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "valid clock time"):
            build_header(spec_for_code("ACCBLOCK"), "20260731", "996099")

    def test_serialization_is_utf8_lf_with_final_newline(self) -> None:
        payload = serialize_lines(("HDR;á;", "TLR;0;"))
        self.assertEqual(payload, "HDR;á;\nTLR;0;\n".encode("utf-8"))
        self.assertNotIn(b"\r\n", payload)


if __name__ == "__main__":
    unittest.main()
