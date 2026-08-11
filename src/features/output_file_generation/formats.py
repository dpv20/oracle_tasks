"""Explicit output contracts for the Generic Interface files we support."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Mapping, Sequence


_PROCESS_REF_RE = re.compile(r"^[0-9]{1,20}$")
_INTERFACE_RE = re.compile(r"^[A-Z][A-Z0-9_$#]{0,29}$")
_TIME_RE = re.compile(r"^[0-9]{6}$")


# These three country packages share the extended OFICOWCG wire contract:
# eleven data values, the upload status, optional error fields, and the final
# clearing transaction status.  Their SELECT cursors are still dispatched by
# country in queries.py because Peru has a material join difference.
OFICOWCG_TRANSACTION_CONTRACTS = frozenset(
    {
        "oficowcg_colombia",
        "oficowcg_mexico",
        "oficowcg_peru",
    }
)
CHISALOU_CONTRACTS = frozenset({"chisalou", "chisalou_peru"})


@dataclass(frozen=True)
class InterfaceSpec:
    input_code: str
    output_code: str
    header_name: str
    body_field_count: int
    footer_record: str
    footer_field_count: int
    allowed_statuses: tuple[str, ...]
    timestamped_header: bool = False
    database_time_format: str = ""
    header_uses_input_filename: bool = False
    error_body_field_count: int = 0
    requires_upload_master: bool = True
    accept_any_non_null_status: bool = False
    header_uses_database_date: bool = False
    contract: str = "standard"
    physical_name_pattern: str = ""
    error_message_mode: str = ""
    footer_counts_all_upload_rows: bool = False
    has_footer: bool = True
    has_header: bool = True
    header_uses_last_run_date: bool = False
    revalidate_external_inputs: bool = False
    accept_null_status: bool = False


_SPECS = (
    InterfaceSpec(
        input_code="ACCBLOCK",
        output_code="ACCBLKOU",
        header_name="ACCBLKOU_{date}.TXT",
        body_field_count=8,
        footer_record="FTR",
        footer_field_count=3,
        allowed_statuses=("P", "E"),
        timestamped_header=True,
        database_time_format="HH24MISS",
        revalidate_external_inputs=True,
    ),
    InterfaceSpec(
        input_code="IFDOBIEL",
        output_code="OFDOBIEL",
        header_name="OFDOBIEL.txt",
        body_field_count=7,
        footer_record="TLR",
        footer_field_count=2,
        allowed_statuses=("P", "E"),
    ),
    InterfaceSpec(
        input_code="IFICOWCG",
        output_code="OFICOWCG",
        header_name="",
        body_field_count=13,
        footer_record="FTR",
        footer_field_count=3,
        allowed_statuses=("P", "E", "U"),
        header_uses_input_filename=True,
        error_body_field_count=15,
    ),
    InterfaceSpec(
        input_code="IFLOCREC",
        output_code="OFLOCREC",
        header_name="OFLOCREC_{date}{time}",
        body_field_count=7,
        footer_record="FTR",
        footer_field_count=3,
        allowed_statuses=("P", "E"),
        timestamped_header=True,
        database_time_format="HHMISS",
    ),
    InterfaceSpec(
        input_code="IACMCLOS",
        output_code="OACMCLOS",
        header_name="OACMCLOS_{date}{time}",
        body_field_count=5,
        footer_record="FTR",
        footer_field_count=3,
        allowed_statuses=("P", "E"),
        timestamped_header=True,
        database_time_format="HH24MISS",
        accept_any_non_null_status=True,
        header_uses_database_date=True,
    ),
    InterfaceSpec(
        input_code="IFCRELVP",
        output_code="OFCRELVP",
        header_name="",
        body_field_count=5,
        footer_record="LF",
        footer_field_count=4,
        allowed_statuses=("P", "E"),
        database_time_format="HH24MISS",
        error_body_field_count=6,
    ),
    InterfaceSpec(
        input_code="CLADCHG",
        output_code="CLADCHGO",
        header_name="",
        body_field_count=4,
        footer_record="03",
        footer_field_count=2,
        allowed_statuses=("P", "E"),
        error_body_field_count=5,
    ),
    InterfaceSpec(
        input_code="CHICLUPD",
        output_code="CHICLOU",
        header_name="CHICLUPD.TXT",
        body_field_count=6,
        footer_record="LF",
        footer_field_count=1,
        allowed_statuses=("P", "E"),
        database_time_format="HH24MISS",
        error_body_field_count=7,
        contract="chiclou",
        physical_name_pattern="{date}{time}",
        revalidate_external_inputs=True,
    ),
    InterfaceSpec(
        input_code="CHISALCA",
        output_code="CHISALOU",
        header_name="",
        body_field_count=11,
        footer_record="FTR",
        footer_field_count=3,
        allowed_statuses=("P", "E", "U"),
        timestamped_header=True,
        database_time_format="HH24MISS",
        header_uses_input_filename=True,
        header_uses_last_run_date=True,
        contract="chisalou",
        physical_name_pattern="CHISALOU_{date}.TXT",
        error_message_mode="list_tilde",
        footer_counts_all_upload_rows=True,
        revalidate_external_inputs=True,
    ),
    InterfaceSpec(
        input_code="CHBOOKIN",
        output_code="CHBOOKOU",
        header_name="CHBOOKOU_{date}{time}",
        body_field_count=10,
        footer_record="FTR",
        footer_field_count=3,
        allowed_statuses=("P", "E"),
        timestamped_header=True,
        database_time_format="HH24MISS",
        contract="chbookou",
        physical_name_pattern="CHBOOKOU_{date}{time}",
        error_message_mode="list_tilde",
    ),
    InterfaceSpec(
        input_code="IACMASSC",
        output_code="OACMASSC",
        header_name="OACMASSC_{date}.TXT",
        body_field_count=12,
        footer_record="FTR",
        footer_field_count=3,
        allowed_statuses=(),
        accept_any_non_null_status=True,
        timestamped_header=True,
        database_time_format="HH24MISS",
        header_uses_database_date=True,
        contract="oacmassc",
        physical_name_pattern="OACMASSC_{date}.TXT",
        error_message_mode="list_tilde_nonp",
    ),
    InterfaceSpec(
        input_code="CLIIRFAP",
        output_code="CLOIRFAP",
        header_name="CLOIRFAP.txt",
        body_field_count=7,
        footer_record="TLR",
        footer_field_count=2,
        allowed_statuses=(),
        accept_any_non_null_status=True,
        contract="cloirfap",
        physical_name_pattern="CLOIRFAP_{date}.TXT",
        error_message_mode="list_escaped",
    ),
    InterfaceSpec(
        input_code="CLISLRES",
        output_code="CLOSLRES",
        header_name="CLOSLRES.txt",
        body_field_count=6,
        footer_record="TLR",
        footer_field_count=2,
        allowed_statuses=(),
        accept_any_non_null_status=True,
        contract="closlres",
        physical_name_pattern="CLOSLRES.txt",
    ),
    InterfaceSpec(
        input_code="CMRADCHG",
        output_code="CMRADCHO",
        header_name="",
        body_field_count=4,
        footer_record="03",
        footer_field_count=2,
        allowed_statuses=(),
        accept_any_non_null_status=True,
        contract="cmradcho",
        physical_name_pattern="CMRADCHO_{date}.TXT",
        error_message_mode="single_strip_semicolons",
        error_body_field_count=5,
    ),
    InterfaceSpec(
        input_code="CMRCLUPD",
        output_code="CMRCLOU",
        header_name="CMRCLUPD.TXT",
        body_field_count=6,
        footer_record="LF",
        footer_field_count=1,
        allowed_statuses=("P", "E"),
        error_body_field_count=7,
        contract="cmrclou",
        physical_name_pattern="CMRCLOU_{date}.TXT",
        error_message_mode="list_plain",
        revalidate_external_inputs=True,
    ),
    InterfaceSpec(
        input_code="CMRCIFUP",
        output_code="CMRCIFOU",
        header_name="",
        body_field_count=6,
        footer_record="SF",
        footer_field_count=3,
        allowed_statuses=("P", "E", "U"),
        timestamped_header=True,
        database_time_format="HH24MISS",
        header_uses_input_filename=True,
        contract="cmrcifou",
        physical_name_pattern="CMRCIFOU_{date}.TXT",
        error_message_mode="list_tilde",
        footer_counts_all_upload_rows=True,
    ),
    InterfaceSpec(
        input_code="CMRLPMNT",
        output_code="CMRLPMTO",
        header_name="",
        body_field_count=427,
        footer_record="03",
        footer_field_count=12,
        allowed_statuses=(),
        accept_any_non_null_status=True,
        contract="fixed_payment",
        physical_name_pattern="CMRLPMTO_{date}.TXT",
        error_message_mode="list_plain",
    ),
    InterfaceSpec(
        input_code="CMRRELVP",
        output_code="CMRRELVO",
        header_name="",
        body_field_count=5,
        footer_record="LF",
        footer_field_count=4,
        allowed_statuses=("P", "E"),
        database_time_format="HH24MISS",
        error_body_field_count=7,
        contract="cmrrelvo",
        physical_name_pattern="CMRRELVO_{date}{time}.TXT",
        error_message_mode="list_plain",
        footer_counts_all_upload_rows=True,
    ),
    InterfaceSpec(
        input_code="IFCHKPRT",
        output_code="OFCHKPRT",
        header_name="",
        body_field_count=21,
        footer_record="TLR",
        footer_field_count=4,
        allowed_statuses=("S", "<NON_S>"),
        header_uses_input_filename=True,
        error_body_field_count=23,
        contract="ofchkprt",
        physical_name_pattern="OFCHKPRT_{date}.TXT",
        error_message_mode="list_tilde",
        revalidate_external_inputs=True,
    ),
    InterfaceSpec(
        input_code="IFIWDCLG",
        output_code="OFIWDCLG",
        header_name="",
        body_field_count=15,
        footer_record="FTR",
        footer_field_count=3,
        allowed_statuses=(),
        accept_any_non_null_status=True,
        header_uses_input_filename=True,
        error_body_field_count=17,
        contract="ofiwdclg",
        physical_name_pattern="OFIWDCLG_{date}.TXT",
        error_message_mode="list_tilde",
        revalidate_external_inputs=True,
    ),
    InterfaceSpec(
        input_code="IFIWADOC",
        output_code="OFIWADOC",
        header_name="",
        body_field_count=15,
        footer_record="FTR",
        footer_field_count=3,
        allowed_statuses=("P", "<NON_P>"),
        header_uses_input_filename=True,
        error_body_field_count=17,
        contract="ofiwadoc",
        physical_name_pattern="OFIWADOC_{date}.TXT",
        error_message_mode="list_tilde",
        revalidate_external_inputs=True,
    ),
    InterfaceSpec(
        input_code="IFQSIMTP",
        output_code="OFQSIMTP",
        header_name="",
        body_field_count=45,
        footer_record="",
        footer_field_count=0,
        allowed_statuses=("P", "E"),
        database_time_format="HH24MISS",
        contract="ofqsimtp",
        physical_name_pattern="OFQSIMTP_{date}_{time}.TXT",
        error_message_mode="single_strip_semicolons",
        has_header=False,
        has_footer=False,
        revalidate_external_inputs=True,
    ),
    InterfaceSpec(
        input_code="IFCLPMNT",
        output_code="IFCLPMTO",
        header_name="",
        body_field_count=427,
        footer_record="03",
        footer_field_count=12,
        allowed_statuses=(),
        accept_any_non_null_status=True,
        contract="fixed_payment",
        physical_name_pattern="IFCLPMTO_{date}.TXT",
        error_message_mode="list_plain",
    ),
    InterfaceSpec(
        input_code="IFDDISSU",
        output_code="OFDDISSU",
        header_name="",
        body_field_count=59,
        footer_record="",
        footer_field_count=0,
        allowed_statuses=(),
        accept_any_non_null_status=True,
        database_time_format="HH24MISS",
        contract="ofddissu",
        physical_name_pattern="IFDDISSU_{date}{time}.TXT",
        error_message_mode="list_tilde",
        has_footer=False,
    ),
    InterfaceSpec(
        input_code="IFGLCRTE",
        output_code="OFGLCRTE",
        header_name="",
        body_field_count=575,
        footer_record="LF",
        footer_field_count=1,
        allowed_statuses=(),
        accept_any_non_null_status=True,
        database_time_format="HH24MISS",
        contract="glcrte_fixed",
        physical_name_pattern="SP{dmy}{time}END",
        error_message_mode="list_prefixed",
    ),
    InterfaceSpec(
        input_code="IFGLMDFY",
        output_code="OFGLMDFY",
        header_name="",
        body_field_count=575,
        footer_record="LF",
        footer_field_count=1,
        allowed_statuses=(),
        accept_any_non_null_status=True,
        database_time_format="HH24MISS",
        contract="glcrte_fixed",
        physical_name_pattern="SP{dmy}{time}END",
        error_message_mode="list_prefixed",
        revalidate_external_inputs=True,
        accept_null_status=True,
    ),
    InterfaceSpec(
        input_code="GIUDFUPD",
        output_code="GIUPDSTS",
        header_name="",
        body_field_count=6,
        footer_record="TRL01",
        footer_field_count=3,
        allowed_statuses=(),
        accept_any_non_null_status=True,
        timestamped_header=True,
        database_time_format="HH24MISS",
        header_uses_input_filename=True,
        header_uses_last_run_date=True,
        error_body_field_count=8,
        contract="giupd",
        physical_name_pattern="GIUPDSTS_{date}.TXT",
        error_message_mode="list_tilde",
    ),
    InterfaceSpec(
        input_code="IFMDCGEN",
        output_code="OFMDCGEN",
        header_name="IFMDCGEN.TXT",
        body_field_count=10,
        footer_record="03",
        footer_field_count=2,
        allowed_statuses=(),
        accept_any_non_null_status=True,
        database_time_format="HH24MISS",
        contract="ofmdcgen",
        physical_name_pattern="OFMDCGEN_{date}{time}.TXT",
        error_message_mode="list_plain_trailing",
    ),
    InterfaceSpec(
        input_code="IFMDSUPD",
        output_code="OFMDSUPD",
        header_name="IFMDSUPD.TXT",
        body_field_count=7,
        footer_record="03",
        footer_field_count=2,
        allowed_statuses=(),
        accept_any_non_null_status=True,
        database_time_format="HH24MISS",
        contract="ofmdsupd",
        physical_name_pattern="OFMDSUPD_{date}{time}.TXT",
        error_message_mode="list_plain_trailing",
    ),
    InterfaceSpec(
        input_code="IFOBTUPD",
        output_code="OFOBTUPD",
        header_name="",
        body_field_count=5,
        footer_record="03",
        footer_field_count=2,
        allowed_statuses=(),
        accept_any_non_null_status=True,
        contract="ofobtupd",
        physical_name_pattern="OFOBTUPD_{date}.TXT",
        error_message_mode="list_plain_trailing",
        revalidate_external_inputs=True,
        accept_null_status=True,
    ),
    InterfaceSpec(
        input_code="IFEARLCG",
        output_code="IFOARLCG",
        header_name="",
        body_field_count=13,
        footer_record="FTR",
        footer_field_count=3,
        allowed_statuses=("P", "E", "<NON_P>"),
        timestamped_header=True,
        database_time_format="HH24MISS",
        header_uses_input_filename=True,
        contract="ifoarlcg",
        physical_name_pattern="IFOARLCG_{date}{time}.TXT",
        error_message_mode="list_tilde",
        footer_counts_all_upload_rows=True,
        header_uses_last_run_date=True,
        revalidate_external_inputs=True,
    ),
    InterfaceSpec(
        input_code="IFSTDCST",
        output_code="DCSTOUT",
        header_name="",
        body_field_count=9,
        footer_record="TLR01",
        footer_field_count=4,
        allowed_statuses=("P", "<NON_P>"),
        timestamped_header=True,
        database_time_format="HH24MISS",
        contract="dcstout",
        physical_name_pattern="DCSTOUT_{date}{time}.TXT",
        footer_counts_all_upload_rows=True,
        revalidate_external_inputs=True,
    ),
    InterfaceSpec(
        input_code="INCHBKPR",
        output_code="OUCHBKCU",
        header_name="",
        body_field_count=9,
        footer_record="SF",
        footer_field_count=3,
        allowed_statuses=("P", "E", "U"),
        timestamped_header=True,
        database_time_format="HH24MISS",
        header_uses_input_filename=True,
        contract="ouchbkcu",
        physical_name_pattern="OUCHBKCU_${dmy}{time}.TXT",
        error_message_mode="list_tilde",
        footer_counts_all_upload_rows=True,
    ),
    InterfaceSpec(
        input_code="IXCGRATE",
        output_code="OXCGRATE",
        header_name="OXCGRATE.txt",
        body_field_count=7,
        footer_record="TLR",
        footer_field_count=2,
        allowed_statuses=(),
        accept_any_non_null_status=True,
        database_time_format="HH24MISS",
        contract="oxcgrate",
        physical_name_pattern="OXCGRATE_{date}.{time}.TXT",
        error_message_mode="list_escaped",
    ),
    InterfaceSpec(
        input_code="LOCAMTIN",
        output_code="LOCAMTOU",
        header_name="LOCAMTOU_{date}.TXT",
        body_field_count=7,
        footer_record="FTR",
        footer_field_count=3,
        allowed_statuses=(),
        accept_any_non_null_status=True,
        timestamped_header=True,
        database_time_format="HH24MISS",
        header_uses_database_date=True,
        contract="locamtou",
        physical_name_pattern="LOCAMTOU_{date}.TXT",
        error_message_mode="single_after_escape",
    ),
    InterfaceSpec(
        input_code="STDCIFMO",
        output_code="STDCIFOM",
        header_name="",
        body_field_count=4,
        footer_record="",
        footer_field_count=0,
        allowed_statuses=("P", "E"),
        database_time_format="HH24MISS",
        error_body_field_count=6,
        contract="stdcifom",
        physical_name_pattern="{date}{time}",
        has_footer=False,
        has_header=False,
        revalidate_external_inputs=True,
    ),
    InterfaceSpec(
        input_code="STDCIFUP",
        output_code="STDCIFOU",
        header_name="",
        body_field_count=6,
        footer_record="SF",
        footer_field_count=3,
        allowed_statuses=(),
        accept_any_non_null_status=True,
        timestamped_header=True,
        database_time_format="HH24MISS",
        header_uses_input_filename=True,
        contract="stdcifou",
        physical_name_pattern="STDCIFOU_{date}.TXT",
        error_message_mode="list_tilde",
        footer_counts_all_upload_rows=True,
    ),
    InterfaceSpec(
        input_code="STDCRDUP",
        output_code="STDCRDOU",
        header_name="",
        body_field_count=13,
        footer_record="FTR",
        footer_field_count=3,
        allowed_statuses=(),
        accept_any_non_null_status=True,
        timestamped_header=True,
        database_time_format="HH24MISS",
        header_uses_input_filename=True,
        contract="stdcrdou",
        physical_name_pattern="STDCRDOU_{date}.TXT",
        error_message_mode="list_tilde",
        footer_counts_all_upload_rows=True,
    ),
    InterfaceSpec(
        input_code="STDINRTS",
        output_code="STDINROU",
        header_name="",
        body_field_count=8,
        footer_record="FTR",
        footer_field_count=3,
        allowed_statuses=("P", "<NON_P>"),
        timestamped_header=True,
        database_time_format="HH24MISS",
        header_uses_input_filename=True,
        header_uses_database_date=True,
        contract="stdinrou",
        physical_name_pattern="STDINROU_${date}{time}.TXT",
        error_message_mode="list_tilde",
        footer_counts_all_upload_rows=True,
        revalidate_external_inputs=True,
    ),
)

_BY_CODE = {
    code: spec
    for spec in _SPECS
    for code in (spec.input_code, spec.output_code)
}
if len(_BY_CODE) != len(_SPECS) * 2:
    raise RuntimeError("Output interface specifications contain duplicate codes")


def supported_specs() -> tuple[InterfaceSpec, ...]:
    return _SPECS


def normalize_interface_code(value: str) -> str:
    code = (value or "").strip().upper()
    if not code or not _INTERFACE_RE.fullmatch(code):
        raise ValueError("Invalid interface code")
    return code


def spec_for_code(value: str) -> InterfaceSpec:
    code = normalize_interface_code(value)
    try:
        return _BY_CODE[code]
    except KeyError as exc:
        raise ValueError(f"Interface {code} is not supported yet") from exc


def validate_process_ref(value: str) -> str:
    process_ref = (value or "").strip()
    if not _PROCESS_REF_RE.fullmatch(process_ref):
        raise ValueError("Process reference must contain 1 to 20 digits")
    return process_ref


def validate_file_date(value: str) -> str:
    file_date = (value or "").strip()
    if not re.fullmatch(r"[0-9]{8}", file_date):
        raise ValueError("File date must use YYYYMMDD")
    try:
        datetime.strptime(file_date, "%Y%m%d")
    except ValueError as exc:
        raise ValueError("File date is not a valid calendar date") from exc
    return file_date


def physical_filename(
    spec: InterfaceSpec,
    file_date: str,
    database_time: str = "",
) -> str:
    date = validate_file_date(file_date)
    if spec.physical_name_pattern:
        time = ""
        if "{time}" in spec.physical_name_pattern:
            time = _validate_database_time(database_time)
        dmy = datetime.strptime(date, "%Y%m%d").strftime("%d%m%Y")
        return spec.physical_name_pattern.format(date=date, dmy=dmy, time=time)
    if spec.input_code == "IFCRELVP":
        time = _validate_database_time(database_time)
        client_date = datetime.strptime(date, "%Y%m%d").strftime("%d%m%Y")
        return f"OFCRELVP_{client_date}_{time}.TXT"
    if spec.input_code == "IACMCLOS":
        time = _validate_database_time(database_time)
        return f"OACMCLOS_{date}{time}"
    return f"{spec.output_code}_{date}.TXT"


def _validate_database_time(value: str) -> str:
    if not _TIME_RE.fullmatch(value or ""):
        raise ValueError("Database time must use HHMMSS")
    try:
        datetime.strptime(value, "%H%M%S")
    except ValueError as exc:
        raise ValueError("Database time is not a valid clock time") from exc
    return value


def build_header(
    spec: InterfaceSpec,
    file_date: str,
    database_time: str = "",
    input_physical_filename: str = "",
    database_date: str = "",
) -> str:
    if not spec.has_header:
        return ""
    date = validate_file_date(file_date)
    if spec.contract == "chiclou":
        client_date = datetime.strptime(date, "%Y%m%d").strftime("%d%m%Y")
        return f"LH^{spec.header_name}^{client_date}^"
    if spec.contract == "cmrclou":
        return f"LH^{spec.header_name}^{date}^"
    if spec.contract in CHISALOU_CONTRACTS:
        header_date = validate_file_date(database_date)
        name = str(input_physical_filename or "").strip()
        if not name or ";" in name or "\r" in name or "\n" in name:
            raise ValueError("A valid physical input filename is required")
        return f"HDR;{name};{header_date}{_validate_database_time(database_time)};"
    if spec.contract == "stdinrou":
        execution_date = validate_file_date(database_date)
        stamp = datetime.strptime(execution_date, "%Y%m%d").strftime("%d%m%Y")
        clock = _validate_database_time(database_time)
        name = str(input_physical_filename or "").strip()
        if not name or ";" in name or "\r" in name or "\n" in name:
            raise ValueError("A valid physical input filename is required")
        # QA uses DDMMYYYYHH24MMSS. Oracle's MM is the month, not minutes,
        # so the header repeats the execution month after the hour. The
        # physical filename still uses the real HH24MISS database clock.
        package_clock = clock[:2] + execution_date[4:6] + clock[4:]
        return f"HDR;{name};{stamp}{package_clock};"
    if spec.contract == "ifoarlcg":
        header_date = validate_file_date(database_date)
        name = str(input_physical_filename or "").strip()
        if not name or ";" in name or "\r" in name or "\n" in name:
            raise ValueError("A valid physical input filename is required")
        return f"HDR;{name};{header_date}{_validate_database_time(database_time)};"
    if spec.contract == "dcstout":
        return f"HDR01;{date}{_validate_database_time(database_time)};"
    if spec.contract == "ofobtupd":
        # GIPKS_OFOBTUPD builds PKG_DATA before assigning PKG_FILE_NAME.
        # Reproduce the deterministic first invocation of a clean package
        # session rather than leaking a prior invocation's package state.
        return f"01;;{date}"
    if spec.contract == "giupd":
        header_date = validate_file_date(database_date)
        name = str(input_physical_filename or "").strip()
        if not name or ";" in name or "\r" in name or "\n" in name:
            raise ValueError("A valid physical input filename is required")
        return f"HDR01;{name};{header_date}{_validate_database_time(database_time)};"
    if spec.contract == "oacmassc":
        execution_date = validate_file_date(database_date)
        stamp = datetime.strptime(execution_date, "%Y%m%d").strftime("%d%m%Y")
        clock = _validate_database_time(database_time)
        stamp += clock[:2] + execution_date[4:6] + clock[4:]
        return f"HDR;OACMASSC_{date}.TXT;{stamp};"
    if spec.contract == "ofddissu":
        return f"LH;{date};"
    if spec.contract == "glcrte_fixed":
        return "LH" + date
    if spec.contract == "fixed_payment":
        return "01" + date + (" " * 4)
    if spec.contract == "cmradcho":
        return f"01^{date}"
    if spec.contract == "cmrrelvo":
        return f"LH^{date}"
    if spec.contract in {"ofmdcgen", "ofmdsupd"}:
        return f"01;{spec.header_name};{date};"
    if spec.contract in {"cmrcifou", "ouchbkcu", "stdcifou", "stdcrdou"}:
        name = str(input_physical_filename or "").strip()
        if not name:
            raise ValueError("Physical input filename is required for this interface")
        delimiter = ";"
        if delimiter in name or "\r" in name or "\n" in name:
            raise ValueError("Physical input filename contains an output delimiter")
        record = "HDR" if spec.contract == "stdcrdou" else "SH"
        return f"{record};{name};{date}{_validate_database_time(database_time)};"
    if spec.contract == "locamtou":
        execution_date = validate_file_date(database_date)
        stamp = datetime.strptime(execution_date, "%Y%m%d").strftime("%d%m%Y")
        clock = _validate_database_time(database_time)
        # QA uses DDMMYYYYHH24MMSS here. Oracle's MM is month (MI would be
        # minutes), so preserve the package's duplicated-month typo.
        stamp += clock[:2] + execution_date[4:6] + clock[4:]
        return f"HDR;{spec.header_name.format(date=date, time=database_time)};{stamp};"
    if spec.input_code == "CLADCHG":
        return f"01^{date}"
    if spec.input_code == "IFCRELVP":
        return f"LH^{date}"
    if spec.header_uses_input_filename:
        name = str(input_physical_filename or "").strip()
        if not name:
            raise ValueError("Physical input filename is required for this interface")
        if ";" in name or "\r" in name or "\n" in name:
            raise ValueError("Physical input filename contains an output delimiter")
        return f"HDR;{name};{date};"
    if spec.timestamped_header:
        _validate_database_time(database_time)
        name = spec.header_name.format(date=date, time=database_time)
        if spec.input_code == "IFLOCREC":
            client_date = datetime.strptime(date, "%Y%m%d").strftime("%d%m%Y")
            return f"HDR;{name};{client_date}{database_time};"
        if spec.input_code == "IACMCLOS":
            execution_date = validate_file_date(database_date)
            client_date = datetime.strptime(execution_date, "%Y%m%d").strftime(
                "%d%m%Y"
            )
            # QA GIPKS_OACMCLOS deliberately formats FN_SYSDATE with
            # DDMMYYYYHH24MMSS.  Oracle's MM is the month, not minutes, so the
            # contractual execution clock is HH + month + seconds.  Preserve
            # that package typo while the filename keeps the real HH24MISS.
            package_clock = (
                database_time[:2] + execution_date[4:6] + database_time[4:]
            )
            return f"HDR;{name};{client_date}{package_clock};"
        return f"HDR;{name};{date}{database_time};"
    return f"HDR;{spec.header_name.format(date=date, time='')};{date};"


def build_footer(
    spec: InterfaceSpec,
    body_count: int,
    status_counts: Mapping[str, int],
) -> str:
    if body_count < 0:
        raise ValueError("Body count cannot be negative")
    normalized: dict[str, int] = {}
    for status, raw_count in status_counts.items():
        key = str(status).strip().upper()
        if not key:
            raise ValueError("Status keys cannot be empty")
        if isinstance(raw_count, bool):
            raise ValueError("Status counts must be non-negative integers")
        try:
            count = int(raw_count)
        except (TypeError, ValueError) as exc:
            raise ValueError("Status counts must be non-negative integers") from exc
        if count < 0:
            raise ValueError("Status counts must be non-negative integers")
        if count:
            normalized[key] = normalized.get(key, 0) + count
    if spec.accept_any_non_null_status:
        if "<NULL>" in normalized and not spec.accept_null_status:
            raise ValueError(
                f"{spec.input_code} contains NULL status; the QA package would "
                "emit a body record but omit it from the footer"
            )
        unexpected: list[str] = []
    else:
        unexpected = sorted(set(normalized) - set(spec.allowed_statuses))
    if unexpected:
        raise ValueError(
            f"{spec.input_code} contains unsupported statuses: "
            + ", ".join(unexpected)
        )
    represented_count = sum(normalized.values())
    if spec.contract == "ifoarlcg":
        if represented_count != body_count:
            raise ValueError("IFEARLCG footer status counts must match its body count")
    elif spec.footer_counts_all_upload_rows:
        if represented_count < body_count:
            raise ValueError(
                f"{spec.input_code} footer status counts cannot be smaller than its body count"
            )
    elif spec.input_code == "IFLOCREC":
        if represented_count < body_count:
            raise ValueError(
                "IFLOCREC footer status counts cannot be smaller than its body count"
            )
    elif represented_count != body_count:
        raise ValueError(
            f"{spec.input_code} body count does not match its status counts"
        )
    if not spec.has_footer:
        return ""
    if spec.contract in {"chiclou", "cmrclou"}:
        return "LF^"
    if spec.contract == "glcrte_fixed":
        return "LF"
    if spec.contract == "giupd":
        processed = normalized.get("P", 0)
        unprocessed = sum(count for status, count in normalized.items() if status != "P")
        return f"TRL01;{processed};{unprocessed};"
    if spec.contract == "dcstout":
        processed = normalized.get("P", 0)
        unprocessed = sum(count for status, count in normalized.items() if status != "P")
        return f"TLR01;{represented_count};{processed};{unprocessed};"
    if spec.contract == "ifoarlcg":
        processed = normalized.get("P", 0)
        unprocessed = sum(count for status, count in normalized.items() if status != "P")
        return f"FTR;{processed};{unprocessed};"
    if spec.contract == "ofchkprt":
        processed = normalized.get("S", 0)
        unprocessed = sum(
            count for status, count in normalized.items() if status != "S"
        )
        return f"TLR;{processed};{unprocessed};{represented_count};"
    if spec.contract in {"ofiwdclg", "ofiwadoc"}:
        processed = normalized.get("P", 0)
        unprocessed = sum(
            count for status, count in normalized.items() if status != "P"
        )
        return f"FTR;{processed};{unprocessed};"
    if spec.contract == "ofobtupd":
        return f"03;{body_count}"
    if spec.contract == "fixed_payment":
        if body_count > 9_999_999_999:
            raise ValueError("Fixed-width footer count exceeds 10 digits")
        return "03" + str(body_count).zfill(10)
    if spec.contract == "cmradcho":
        return f"03^{body_count}"
    if spec.contract == "cmrrelvo":
        processed = normalized.get("P", 0)
        unprocessed = sum(count for status, count in normalized.items() if status != "P")
        return f"LF^{processed}^{unprocessed}^{represented_count}"
    if spec.contract == "stdinrou":
        processed = normalized.get("P", 0)
        unprocessed = sum(
            count for status, count in normalized.items() if status != "P"
        )
        return f"FTR;{processed};{unprocessed};"
    if spec.contract in {
        "chbookou",
        *CHISALOU_CONTRACTS,
        "locamtou",
        "oacmassc",
        "stdcrdou",
    }:
        processed = normalized.get("P", 0)
        unprocessed = sum(count for status, count in normalized.items() if status != "P")
        return f"FTR;{processed};{unprocessed};"
    if spec.contract in {"cmrcifou", "ouchbkcu", "stdcifou"}:
        processed = normalized.get("P", 0)
        unprocessed = sum(count for status, count in normalized.items() if status != "P")
        return f"SF;{processed};{unprocessed};"
    if spec.contract in {"ofmdcgen", "ofmdsupd"}:
        return f"03;{body_count};"
    if spec.input_code == "CLADCHG":
        return f"03^{body_count}"
    if spec.input_code == "IFCRELVP":
        return (
            f"LF^{normalized.get('P', 0)}^{normalized.get('E', 0)}^{body_count}"
        )
    if spec.input_code == "IACMCLOS":
        processed = normalized.get("P", 0)
        unprocessed = sum(
            count for status, count in normalized.items() if status != "P"
        )
        return f"FTR;{processed},{unprocessed},"
    if spec.input_code in {"ACCBLOCK", "IFICOWCG", "IFLOCREC"}:
        processed = normalized.get("P", 0)
        unprocessed = sum(
            count for status, count in normalized.items() if status != "P"
        )
        return f"FTR;{processed};{unprocessed};"
    return f"TLR;{body_count};"


def split_error_codes(error_code_list: str) -> tuple[str, ...]:
    """Split the OFDOBIEL error-code list using the package's rules."""
    normalized = (error_code_list or "").strip(";")
    if not normalized:
        return ()
    codes: list[str] = []
    for code in normalized.split(";"):
        if not code:
            break
        codes.append(code)
    return tuple(codes)


def format_error_code(error_code_list: str) -> str:
    """Apply the output package's semicolon escaping to the raw error list."""
    return (error_code_list or "").replace(";", "~").rstrip("~")


def format_error_description(
    error_code_list: str,
    error_param_list: str,
    messages: Mapping[str, str],
) -> str:
    """Reproduce the OFDOBIEL package's error-description field.

    The QA package resolves each code through ERTBS_MSGS (the read-only PROD
    account exposes its ERTB_MSGS synonym), substitutes ``$1``...
    from the matching tilde-delimited parameter group, joins messages with
    semicolons, then converts those semicolons to tildes for the output file.
    """
    return format_error_description_list(
        error_code_list,
        error_param_list,
        messages,
    ).replace(";", "~").rstrip("~")


def format_error_description_list(
    error_code_list: str,
    error_param_list: str,
    messages: Mapping[str, str],
) -> str:
    """Reproduce ``GIPKS_#CLDPYMNT.FN_GET_ERROR_DESC`` exactly."""
    codes = split_error_codes(error_code_list)
    if not codes:
        return ""
    params = (error_param_list or "").rstrip(";").rstrip("~") + "~;"
    param_groups = params.split(";")
    descriptions: list[str] = []
    for index, code in enumerate(codes):
        if code == "I-SUCCESS":
            descriptions.append(code)
            continue
        template = messages.get(code, "Missing Error Code").replace("!", "")
        group = param_groups[index] if index < len(param_groups) else ""
        separator_count = group.count("~")
        if not group:
            values: list[str] = []
        elif separator_count:
            values = group.split("~")[:separator_count]
        else:
            values = [group]
        rendered = template
        for number, value in enumerate(values, start=1):
            rendered = rendered.replace(f"${number}", value)
        descriptions.append(rendered)
    return ";".join(descriptions)


def format_form_message(
    error_code: str,
    error_params: str,
    messages: Mapping[str, str],
) -> str:
    """Reproduce ``OVPKS.FN_FORMMSG``/``FN_EMBEDSTR`` without calling PL/SQL.

    ``FN_EMBEDSTR`` replaces ``$1``... once per tilde found in the parameter
    list, or once when a non-empty list contains no tilde.  This intentionally
    preserves the package's slightly unusual behavior for a non-terminated
    multi-value parameter list.
    """
    code = str(error_code or "")
    rendered = messages.get(code, "Missing Error Code").replace("!", "")
    params = str(error_params or "")
    if not params:
        return rendered
    count = params.count("~") or 1
    values = params.split("~")[:count]
    for number, value in enumerate(values, start=1):
        rendered = rendered.replace(f"${number}", value)
    return rendered


def split_oacmclos_error_codes(error_code_list: str) -> tuple[str, ...]:
    """Return OACMCLOS error tokens up to NULL/empty or the EOPL sentinel."""
    codes: list[str] = []
    for code in str(error_code_list or "").split(";"):
        if not code or code == "EOPL":
            break
        codes.append(code)
    return tuple(codes)


def format_oacmclos_error_descriptions(
    error_code_list: str,
    error_param_list: str,
    messages: Mapping[str, str],
) -> str:
    """Reproduce OACMCLOS' ``;description`` accumulation exactly.

    Error codes and parameter groups are positional semicolon-delimited lists.
    The package appends ``~`` to each parameter group before resolving the
    message and does not emit a description for its ``EOPL`` sentinel.
    """
    codes = split_oacmclos_error_codes(error_code_list)
    if not codes:
        return ""
    params = str(error_param_list or "").split(";")
    descriptions: list[str] = []
    for index, code in enumerate(codes):
        param = params[index] if index < len(params) else ""
        descriptions.append(format_form_message(code, param + "~", messages))
    return "".join(f";{description}" for description in descriptions)


def _fields(line: str) -> list[str]:
    if not line.endswith(";"):
        raise ValueError("Every output line must end with ';'")
    if "\r" in line or "\n" in line:
        raise ValueError("Output fields cannot contain line breaks")
    return line[:-1].split(";")


def validate_output_lines(spec: InterfaceSpec, lines: Sequence[str]) -> None:
    if spec.contract == "stdcifom":
        _validate_stdcifom_lines(lines)
        return
    if spec.contract == "ofchkprt":
        _validate_ofchkprt_lines(lines)
        return
    if spec.contract == "ofiwdclg":
        _validate_ofiwdclg_lines(lines)
        return
    if spec.contract == "ofiwadoc":
        _validate_ofiwadoc_lines(lines)
        return
    if spec.contract == "ofqsimtp":
        _validate_ofqsimtp_lines(lines)
        return
    if spec.contract == "stdinrou":
        _validate_stdinrou_lines(lines)
        return
    if spec.contract not in {"standard", *OFICOWCG_TRANSACTION_CONTRACTS}:
        _validate_declarative_lines(spec, lines)
        return
    if spec.input_code == "CLADCHG":
        _validate_cladchgo_lines(lines)
        return
    if spec.input_code == "IFCRELVP":
        _validate_ofcrelvp_lines(lines)
        return
    if spec.input_code == "IACMCLOS":
        _validate_oacmclos_lines(lines)
        return
    if len(lines) < 2:
        raise ValueError("Output must contain a header and footer")
    header = _fields(lines[0])
    if len(header) != 3 or header[0] != "HDR":
        raise ValueError("Invalid output header")
    if spec.header_uses_input_filename:
        if not header[1]:
            raise ValueError("Invalid output header filename")
        validate_file_date(header[2])
    elif spec.timestamped_header:
        if spec.input_code == "IFLOCREC":
            name_match = re.fullmatch(
                rf"{re.escape(spec.output_code)}_([0-9]{{8}})([0-9]{{6}})",
                header[1],
            )
            if name_match is None or not re.fullmatch(r"[0-9]{14}", header[2]):
                raise ValueError("Invalid output header")
            header_date = validate_file_date(name_match.group(1))
            header_time = name_match.group(2)
            try:
                datetime.strptime(header_time, "%H%M%S")
                client_date = datetime.strptime(header[2][:8], "%d%m%Y").strftime(
                    "%Y%m%d"
                )
            except ValueError as exc:
                raise ValueError("Header timestamp is not valid") from exc
            if client_date != header_date or header[2][8:] != header_time:
                raise ValueError("Header filename and timestamp dates do not match")
        else:
            match = re.fullmatch(
                rf"{re.escape(spec.output_code)}_([0-9]{{8}})\.TXT",
                header[1],
            )
            if match is None or not re.fullmatch(r"[0-9]{14}", header[2]):
                raise ValueError("Invalid output header")
            header_date = validate_file_date(match.group(1))
            if header[2][:8] != header_date:
                raise ValueError("Header filename and timestamp dates do not match")
            try:
                datetime.strptime(header[2], "%Y%m%d%H%M%S")
            except ValueError as exc:
                raise ValueError("Header timestamp is not valid") from exc
    else:
        expected_name = spec.header_name.format(date=header[2])
        if header[1] != expected_name:
            raise ValueError("Invalid output header filename")
        validate_file_date(header[2])

    positive_flags = 0
    negative_flags = 0
    for line in lines[1:-1]:
        fields = _fields(line)
        if not fields or fields[0] != "BDY":
            raise ValueError("Every body line must start with BDY")
        expected_field_count = spec.body_field_count
        if (
            spec.error_body_field_count
            and len(fields) == spec.error_body_field_count
        ):
            expected_field_count = spec.error_body_field_count
        if len(fields) != expected_field_count:
            raise ValueError(
                f"{spec.output_code} body must contain "
                f"{spec.body_field_count}"
                + (
                    f" or {spec.error_body_field_count}"
                    if spec.error_body_field_count
                    else ""
                )
                + " semicolon-delimited fields"
            )
        if spec.input_code == "ACCBLOCK":
            flag = fields[5]
            if flag not in {"Y", "N"}:
                raise ValueError("ACCBLKOU body status must be Y or N")
            positive_flags += int(flag == "Y")
            negative_flags += int(flag == "N")
        elif spec.input_code == "IFLOCREC":
            flag = fields[4]
            if flag not in {"Y", "N"}:
                raise ValueError("OFLOCREC body status must be Y or N")
            positive_flags += int(flag == "Y")
            negative_flags += int(flag == "N")
        elif spec.input_code == "IFICOWCG":
            status = fields[12]
            if status not in spec.allowed_statuses:
                raise ValueError("OFICOWCG body status must be P, E, or U")
            if spec.contract in OFICOWCG_TRANSACTION_CONTRACTS:
                has_error_fields = len(fields) == spec.error_body_field_count
                txn_status = fields[15] if has_error_fields else fields[13]
                allowed_pairs = {
                    "P": {"SUCC", "REJR"},
                    "E": {"ERRO", "NOPR", ""},
                    "U": {"NOPR"},
                }
                if txn_status not in allowed_pairs[status]:
                    raise ValueError("Invalid regional OFICOWCG status pair")
                if status == "P" and txn_status == "SUCC" and has_error_fields:
                    raise ValueError(
                        "Successful OFICOWCG body must not contain error fields"
                    )
                if status == "P" and txn_status == "REJR" and not has_error_fields:
                    raise ValueError(
                        "Rejected OFICOWCG body must contain error fields"
                    )
                if status in {"E", "U"} and not has_error_fields:
                    raise ValueError(
                        "OFICOWCG error body must contain code and description"
                    )
            else:
                if status == "P" and len(fields) != spec.body_field_count:
                    raise ValueError("OFICOWCG processed body must not contain error fields")
                if status in {"E", "U"} and len(fields) != spec.error_body_field_count:
                    raise ValueError("OFICOWCG error body must contain code and description")
            positive_flags += int(status == "P")
            negative_flags += int(status != "P")
        elif spec.input_code == "IFDOBIEL" and fields[4] not in spec.allowed_statuses:
            raise ValueError("OFDOBIEL body status must be P or E")

    footer = lines[-1]
    footer_fields = _fields(footer)
    if not footer_fields or footer_fields[0] != spec.footer_record:
        raise ValueError(f"Footer must start with {spec.footer_record}")
    if len(footer_fields) != spec.footer_field_count:
        raise ValueError("Invalid output footer")
    try:
        counts = [int(value) for value in footer_fields[1:]]
    except ValueError as exc:
        raise ValueError("Footer counts must be non-negative integers") from exc
    if any(value < 0 or str(value) != raw for value, raw in zip(counts, footer_fields[1:])):
        raise ValueError("Footer counts must be non-negative integers")
    body_count = len(lines) - 2
    if spec.input_code in {"ACCBLOCK", "IFICOWCG"}:
        if counts != [positive_flags, negative_flags]:
            raise ValueError(f"{spec.output_code} footer does not match its body statuses")
    elif spec.input_code == "IFLOCREC":
        if counts[0] < positive_flags or counts[1] < negative_flags:
            raise ValueError(
                "OFLOCREC footer counts cannot be smaller than its body statuses"
            )
    elif counts != [body_count]:
        raise ValueError("Output footer count does not match its body records")


def _plain_count(value: str) -> int:
    if not re.fullmatch(r"0|[1-9][0-9]*", value or ""):
        raise ValueError("Footer counts must be non-negative integers")
    return int(value)


def _validate_stdcifom_lines(lines: Sequence[str]) -> None:
    if not lines:
        raise ValueError("STDCIFOM output must contain at least one body record")
    if any("\r" in line or "\n" in line for line in lines):
        raise ValueError("Output fields cannot contain line breaks")

    for line in lines:
        fields = line.split("^")
        if len(fields) == 4 and fields[2] == "P" and fields[3] == "":
            continue
        if (
            len(fields) == 6
            and fields[2] == "E"
            and fields[3]
            and fields[5] == ""
        ):
            continue
        raise ValueError("Invalid STDCIFOM body record")


def _validate_ofchkprt_lines(lines: Sequence[str]) -> None:
    if len(lines) < 3:
        raise ValueError("OFCHKPRT output must contain a header, body, and footer")
    if any("\r" in line or "\n" in line for line in lines):
        raise ValueError("Output fields cannot contain line breaks")

    header = _fields(lines[0])
    if len(header) != 3 or header[0] != "HDR" or not header[1]:
        raise ValueError("Invalid OFCHKPRT header")
    validate_file_date(header[2])

    processed = 0
    unprocessed = 0
    for line in lines[1:-1]:
        # ERROR_CODES and the formatted message are package-owned semicolon
        # lists. Split only through the 21 stable fields ending at PROC_STAT.
        prefix = line.split(";", 21)
        if len(prefix) != 22 or prefix[0] != "BDY":
            raise ValueError("Invalid OFCHKPRT body record")
        protest_date = prefix[18]
        if protest_date:
            validate_file_date(protest_date)
        status = prefix[20]
        tail = prefix[21]
        if status == "S":
            if tail:
                raise ValueError("Processed OFCHKPRT body has error fields")
            processed += 1
        else:
            if not tail.endswith(";") or tail.count(";") < 2:
                raise ValueError("Unprocessed OFCHKPRT body lacks error fields")
            unprocessed += 1

    footer = _fields(lines[-1])
    if len(footer) != 4 or footer[0] != "TLR":
        raise ValueError("Invalid OFCHKPRT footer")
    counts = [_plain_count(value) for value in footer[1:]]
    if counts != [processed, unprocessed, processed + unprocessed]:
        raise ValueError("OFCHKPRT footer does not match its body")


def _validate_ofiwdclg_lines(lines: Sequence[str]) -> None:
    if len(lines) < 3:
        raise ValueError("OFIWDCLG output must contain a header, body, and footer")
    if any("\r" in line or "\n" in line for line in lines):
        raise ValueError("Output fields cannot contain line breaks")

    header = _fields(lines[0])
    if len(header) != 3 or header[0] != "HDR" or not header[1]:
        raise ValueError("Invalid OFIWDCLG header")
    validate_file_date(header[2])

    processed = 0
    unprocessed = 0
    for line in lines[1:-1]:
        # ERROR_CODE and ERROR_MSG may themselves contain semicolon lists.
        # The first 15 fields through FCCREF have a stable QA layout.
        prefix = line.split(";", 15)
        if len(prefix) != 16 or prefix[0] != "BDY":
            raise ValueError("Invalid OFIWDCLG body record")
        for index in (2, 9):
            if prefix[index]:
                validate_file_date(prefix[index])
        status = prefix[12]
        txn_status = prefix[13]
        tail = prefix[15]
        emits_error = status in {"E", "U"} or txn_status == "REJR"
        if emits_error:
            if not tail.endswith(";") or tail.count(";") < 2:
                raise ValueError("OFIWDCLG error body lacks error fields")
        elif tail:
            raise ValueError("OFIWDCLG non-error body has unexpected error fields")
        processed += int(status == "P")
        unprocessed += int(status != "P")

    footer = _fields(lines[-1])
    if len(footer) != 3 or footer[0] != "FTR":
        raise ValueError("Invalid OFIWDCLG footer")
    counts = [_plain_count(value) for value in footer[1:]]
    if counts != [processed, unprocessed]:
        raise ValueError("OFIWDCLG footer does not match its body")


def _validate_ofiwadoc_lines(lines: Sequence[str]) -> None:
    if len(lines) < 3:
        raise ValueError("OFIWADOC output must contain a header, body, and footer")
    if any("\r" in line or "\n" in line for line in lines):
        raise ValueError("Output fields cannot contain line breaks")

    header = _fields(lines[0])
    if len(header) != 3 or header[0] != "HDR" or not header[1]:
        raise ValueError("Invalid OFIWADOC header")
    validate_file_date(header[2])

    processed = 0
    unprocessed = 0
    for line in lines[1:-1]:
        # The first 15 fields through the deliberately empty FCCREF are stable;
        # ERROR_CODE/ERROR_MSG form the variable package-owned tail.
        prefix = line.split(";", 15)
        if len(prefix) != 16 or prefix[0] != "BDY":
            raise ValueError("Invalid OFIWADOC body record")
        for index in (2, 9):
            if prefix[index]:
                validate_file_date(prefix[index])
        status = prefix[12]
        txn_status = prefix[13]
        fccref = prefix[14]
        tail = prefix[15]
        if txn_status != "NOPR" or fccref:
            raise ValueError("Invalid OFIWADOC NOPR/FCCREF fields")
        if status in {"E", "U"}:
            if not tail.endswith(";") or tail.count(";") < 2:
                raise ValueError("OFIWADOC error body lacks error fields")
        elif tail:
            raise ValueError("OFIWADOC non-error body has unexpected error fields")
        processed += int(status == "P")
        unprocessed += int(status != "P")

    footer = _fields(lines[-1])
    if len(footer) != 3 or footer[0] != "FTR":
        raise ValueError("Invalid OFIWADOC footer")
    counts = [_plain_count(value) for value in footer[1:]]
    if counts != [processed, unprocessed]:
        raise ValueError("OFIWADOC footer does not match its body")


def _validate_ofqsimtp_lines(lines: Sequence[str]) -> None:
    if not lines:
        raise ValueError("OFQSIMTP output must contain body records")
    for line in lines:
        if "\r" in line or "\n" in line:
            raise ValueError("Output fields cannot contain line breaks")
        if line.startswith(("HDR;", "FTR;", "TLR;")) or not line.endswith(";"):
            raise ValueError("Invalid OFQSIMTP body-only record")
        fields = line[:-1].split(";")
        if len(fields) not in {44, 45, 46} or fields[3] not in {"P", "E"}:
            raise ValueError("Invalid OFQSIMTP body record width or status")

        # With an error, QA omits the separator between the formatted message
        # and VALUE_DATE; all later fixed fields therefore shift left by one.
        has_error = bool(fields[4])
        maturity_index = 6 if has_error else 7
        sim_date_index = 9 if has_error else 10
        if not has_error and fields[6]:
            validate_file_date(fields[6])
        if fields[maturity_index]:
            validate_file_date(fields[maturity_index])
        if fields[sim_date_index]:
            validate_file_date(fields[sim_date_index])


def _validate_stdinrou_lines(lines: Sequence[str]) -> None:
    if len(lines) < 3:
        raise ValueError("STDINROU output must contain a header, body, and footer")
    if any("\r" in line or "\n" in line for line in lines):
        raise ValueError("Output fields cannot contain line breaks")

    header = _fields(lines[0])
    if (
        len(header) != 3
        or header[0] != "HDR"
        or not header[1]
        or re.fullmatch(r"[0-9]{14}", header[2]) is None
    ):
        raise ValueError("Invalid STDINROU header")
    try:
        datetime.strptime(header[2], "%d%m%Y%H%M%S")
    except ValueError as exc:
        raise ValueError("Invalid STDINROU execution timestamp") from exc

    for line in lines[1:-1]:
        # ERROR and its formatted description are open semicolon lists.  The
        # six fields through PROCSTAT are the stable part of the QA contract.
        prefix = line.split(";", 6)
        if (
            len(prefix) != 7
            or prefix[0] != "BDY"
            or prefix[5] not in {"Y", "N"}
            or not prefix[6].endswith(";")
        ):
            raise ValueError("Invalid STDINROU body record")

    footer = _fields(lines[-1])
    if len(footer) != 3 or footer[0] != "FTR":
        raise ValueError("Invalid STDINROU footer")
    processed = _plain_count(footer[1])
    unprocessed = _plain_count(footer[2])
    if processed + unprocessed < len(lines) - 2:
        raise ValueError("STDINROU footer population is smaller than its body")


def _validate_declarative_lines(
    spec: InterfaceSpec,
    lines: Sequence[str],
) -> None:
    if len(lines) < 2:
        raise ValueError("Output must contain a header and body")
    if any("\r" in line or "\n" in line for line in lines):
        raise ValueError("Output fields cannot contain line breaks")

    contract = spec.contract
    header_line = lines[0]
    if contract == "chiclou":
        header = header_line.split("^")
        if (
            len(header) != 4
            or header[0] != "LH"
            or header[1] != "CHICLUPD.TXT"
            or header[3] != ""
        ):
            raise ValueError("Invalid CHICLOU header")
        try:
            datetime.strptime(header[2], "%d%m%Y")
        except ValueError as exc:
            raise ValueError("Invalid CHICLOU header date") from exc
    elif contract == "cmrclou":
        header = header_line.split("^")
        if (
            len(header) != 4
            or header[0] != "LH"
            or header[1] != "CMRCLUPD.TXT"
            or header[3] != ""
        ):
            raise ValueError("Invalid CMRCLOU header")
        validate_file_date(header[2])
    elif contract == "ofobtupd":
        match = re.fullmatch(r"01;;([0-9]{8})", header_line)
        if match is None:
            raise ValueError("Invalid OFOBTUPD header")
        validate_file_date(match.group(1))
    elif contract == "glcrte_fixed":
        match = re.fullmatch(r"LH([0-9]{8})", header_line)
        if match is None:
            raise ValueError("Invalid OFGLCRTE header")
        validate_file_date(match.group(1))
    elif contract == "ofddissu":
        header = _fields(header_line)
        if len(header) != 2 or header[0] != "LH":
            raise ValueError("Invalid OFDDISSU header")
        validate_file_date(header[1])
    elif contract == "fixed_payment":
        match = re.fullmatch(r"01([0-9]{8}) {4}", header_line)
        if match is None:
            raise ValueError("Invalid fixed-width output header")
        validate_file_date(match.group(1))
    elif contract == "cmradcho":
        header = header_line.split("^")
        if len(header) != 2 or header[0] != "01":
            raise ValueError("Invalid CMRADCHO header")
        validate_file_date(header[1])
    elif contract == "cmrrelvo":
        header = header_line.split("^")
        if len(header) != 2 or header[0] != "LH":
            raise ValueError("Invalid CMRRELVO header")
        validate_file_date(header[1])
    else:
        header = _fields(header_line)
        expected_header_fields = 2 if contract == "dcstout" else 3
        if len(header) != expected_header_fields:
            raise ValueError(f"Invalid {spec.output_code} header")
        if contract in CHISALOU_CONTRACTS:
            if header[0] != "HDR" or not header[1]:
                raise ValueError("Invalid CHISALOU header")
            if not re.fullmatch(r"[0-9]{14}", header[2]):
                raise ValueError("Invalid CHISALOU header timestamp")
            validate_file_date(header[2][:8])
            _validate_database_time(header[2][8:])
        elif contract == "ifoarlcg":
            if header[0] != "HDR" or not header[1]:
                raise ValueError("Invalid IFOARLCG header")
            if not re.fullmatch(r"[0-9]{14}", header[2]):
                raise ValueError("Invalid IFOARLCG header timestamp")
            validate_file_date(header[2][:8])
            _validate_database_time(header[2][8:])
        elif contract == "dcstout":
            if header[0] != "HDR01" or not re.fullmatch(r"[0-9]{14}", header[1]):
                raise ValueError("Invalid DCSTOUT header")
            validate_file_date(header[1][:8])
            _validate_database_time(header[1][8:])
        elif contract == "giupd":
            if header[0] != "HDR01" or not header[1]:
                raise ValueError("Invalid GIUPDSTS header")
            if not re.fullmatch(r"[0-9]{14}", header[2]):
                raise ValueError("Invalid GIUPDSTS header timestamp")
            validate_file_date(header[2][:8])
            _validate_database_time(header[2][8:])
        elif contract == "oacmassc":
            if header[0] != "HDR":
                raise ValueError("Invalid OACMASSC header")
            name_match = re.fullmatch(r"OACMASSC_([0-9]{8})\.TXT", header[1])
            if name_match is None or not re.fullmatch(r"[0-9]{14}", header[2]):
                raise ValueError("Invalid OACMASSC header")
            validate_file_date(name_match.group(1))
            try:
                execution_date = datetime.strptime(header[2][:8], "%d%m%Y")
                hour = int(header[2][8:10])
                seconds = int(header[2][12:14])
            except ValueError as exc:
                raise ValueError("Invalid OACMASSC header timestamp") from exc
            if not 0 <= hour <= 23 or not 0 <= seconds <= 59:
                raise ValueError("Invalid OACMASSC header timestamp")
            if header[2][10:12] != f"{execution_date.month:02d}":
                raise ValueError("Invalid OACMASSC package clock")
        elif contract in {"ofmdcgen", "ofmdsupd"}:
            if header[0] != "01" or header[1] != spec.header_name:
                raise ValueError(f"Invalid {spec.output_code} header")
            validate_file_date(header[2])
        elif contract in {"cmrcifou", "ouchbkcu", "stdcifou", "stdcrdou"}:
            expected_record = "HDR" if contract == "stdcrdou" else "SH"
            if header[0] != expected_record or not header[1]:
                raise ValueError(f"Invalid {spec.output_code} header")
            if not re.fullmatch(r"[0-9]{14}", header[2]):
                raise ValueError(f"Invalid {spec.output_code} header timestamp")
            validate_file_date(header[2][:8])
            _validate_database_time(header[2][8:])
        elif contract == "locamtou":
            if header[0] != "HDR":
                raise ValueError("Invalid LOCAMTOU header")
            name_match = re.fullmatch(r"LOCAMTOU_([0-9]{8})\.TXT", header[1])
            if name_match is None or not re.fullmatch(r"[0-9]{14}", header[2]):
                raise ValueError("Invalid LOCAMTOU header")
            validate_file_date(name_match.group(1))
            try:
                execution_date = datetime.strptime(header[2][:8], "%d%m%Y")
                hour = int(header[2][8:10])
                seconds = int(header[2][12:14])
            except ValueError as exc:
                raise ValueError("Invalid LOCAMTOU header timestamp") from exc
            if not 0 <= hour <= 23 or not 0 <= seconds <= 59:
                raise ValueError("Invalid LOCAMTOU header timestamp")
            if header[2][10:12] != f"{execution_date.month:02d}":
                raise ValueError("Invalid LOCAMTOU package clock")
        elif contract == "chbookou":
            if header[0] != "HDR":
                raise ValueError("Invalid CHBOOKOU header")
            match = re.fullmatch(r"CHBOOKOU_([0-9]{8})([0-9]{6})", header[1])
            if match is None or header[2] != match.group(1) + match.group(2):
                raise ValueError("Invalid CHBOOKOU header")
            validate_file_date(match.group(1))
            _validate_database_time(match.group(2))
        else:
            expected_names = {
                "cloirfap": "CLOIRFAP.txt",
                "closlres": "CLOSLRES.txt",
                "oxcgrate": "OXCGRATE.txt",
            }
            if header[0] != "HDR" or header[1] != expected_names.get(contract):
                raise ValueError(f"Invalid {spec.output_code} header")
            validate_file_date(header[2])

    positive = 0
    negative = 0
    body_lines = lines[1:-1] if spec.has_footer else lines[1:]
    body_count = len(body_lines)
    for line in body_lines:
        if contract in {"chiclou", "cmrclou"}:
            fields = line.split("^")
            if len(fields) == 6 and fields[4] == "P" and fields[5] == "":
                continue
            if len(fields) == 7 and fields[3] == "E" and fields[6] == "":
                continue
            raise ValueError(f"Invalid {spec.output_code} body")
        if contract in CHISALOU_CONTRACTS:
            # ERROR/FLD199 is a raw semicolon list, so validate only the stable
            # package prefix and leave the package-owned tail structurally open.
            status_index = 9 if contract == "chisalou_peru" else 8
            prefix = line.split(";", status_index + 1)
            if (
                len(prefix) != status_index + 2
                or prefix[0] != "BDY"
                or not line.endswith(";")
            ):
                raise ValueError("Invalid CHISALOU body record")
            if prefix[6]:
                validate_file_date(prefix[6])
            if prefix[status_index] == "Y":
                positive += 1
            elif prefix[status_index] == "N":
                negative += 1
            elif (
                prefix[1] == ""
                and prefix[status_index] == "NGI-INT214*Failed to process data*"
                and prefix[status_index + 1] == ""
            ):
                # Preserve the missing delimiter in the QA package's U branch.
                negative += 1
            else:
                raise ValueError("CHISALOU body status must be Y or N")
            continue
        if contract == "ifoarlcg":
            # ERROR_CODES is emitted verbatim and can itself be a semicolon
            # list. Validate the stable prefix through the decoded status and
            # leave only that package-owned error/message tail structurally open.
            prefix = line.split(";", 13)
            if (
                len(prefix) != 14
                or prefix[0] != "FB"
                or not line.endswith(";")
                or prefix[12] not in {"P", "E"}
            ):
                raise ValueError("Invalid IFOARLCG body record")
            positive += int(prefix[12] == "P")
            negative += int(prefix[12] == "E")
            continue
        if contract == "dcstout":
            fields = _fields(line)
            if len(fields) not in {8, 9}:
                raise ValueError("Invalid DCSTOUT body field count")
            flag = fields[-1]
            if flag not in {"Y", "N"}:
                raise ValueError("DCSTOUT body status must be Y or N")
            positive += int(flag == "Y")
            negative += int(flag == "N")
            continue
        if contract == "ofobtupd":
            # ERROR is a package-owned semicolon list concatenated directly
            # with its first rendered message. Validate only the five stable
            # fields; the tail is intentionally structurally open.
            prefix = line.split(";", 5)
            if len(prefix) != 6 or prefix[0] != "02":
                raise ValueError("Invalid OFOBTUPD body record")
            continue
        if contract == "glcrte_fixed":
            if len(line) != 575 or not line.startswith("LB"):
                raise ValueError("OFGLCRTE body records must contain 575 characters")
            continue
        if contract == "fixed_payment":
            if len(line) != 427 or not line.startswith("02"):
                raise ValueError("Fixed-width body records must contain 427 characters")
            for start in (56, 64):
                raw_date = line[start : start + 8].strip()
                if raw_date:
                    validate_file_date(raw_date)
            flag = line[-1]
            if flag not in {"Y", "N"}:
                raise ValueError("Fixed-width body status must be Y or N")
        elif contract == "cmradcho":
            fields = line.split("^")
            if not line.startswith("02") or len(fields) not in {4, 5}:
                raise ValueError("Invalid CMRADCHO body")
            if len(fields) == 4 and fields[-1] != "":
                raise ValueError("Invalid CMRADCHO body")
            if len(fields) == 5 and not fields[3]:
                raise ValueError("CMRADCHO error body requires an error code")
            continue
        elif contract == "cmrrelvo":
            fields = line.split("^")
            if not fields or fields[0] != "BH":
                raise ValueError("Invalid CMRRELVO body")
            if len(fields) == 5 and fields[4] == "Y":
                flag = "Y"
            elif len(fields) == 7 and fields[4] == "N":
                flag = "N"
            else:
                raise ValueError("Invalid CMRRELVO body")
        else:
            if contract == "giupd":
                fields = _fields(line)
                if not fields or fields[0] != "BDY01":
                    raise ValueError("Invalid GIUPDSTS body record")
                if len(fields) == 6 and fields[5] == "Y":
                    flag = "Y"
                elif (
                    len(fields) in {7, 8}
                    and fields[5] == "N"
                ):
                    flag = "N"
                elif (
                    len(fields) == 9
                    and fields[5] == "N"
                    and fields[6] == "GI-INT214"
                    and fields[7] == ""
                ):
                    flag = "N"
                else:
                    raise ValueError("Invalid GIUPDSTS body record")
                positive += int(flag == "Y")
                negative += int(flag == "N")
                continue
            if contract == "chbookou":
                # QA writes the semicolon-delimited ERROR list raw. Split only
                # through the fixed prefix/status and treat the remainder as
                # the package-owned error/message tail.
                prefix = line.split(";", 8)
                if (
                    len(prefix) != 9
                    or prefix[0] != "BDY"
                    or not prefix[8].endswith(";")
                ):
                    raise ValueError("Invalid CHBOOKOU body record")
                flag = prefix[7]
                if flag not in {"Y", "N"}:
                    raise ValueError("CHBOOKOU body status must be Y or N")
                positive += int(flag == "Y")
                negative += int(flag == "N")
                continue
            if contract == "closlres":
                # ERROR and ERROR_PARAM are emitted raw and can themselves be
                # semicolon lists, so only the stable left-hand fields can be
                # counted structurally.
                prefix = line.split(";", 4)
                if (
                    len(prefix) != 5
                    or prefix[0] != "BDY"
                    or not prefix[3]
                    or not prefix[4].endswith(";")
                ):
                    raise ValueError("Invalid CLOSLRES body record")
                continue
            fields = _fields(line)
            prefixes = {
                "oacmassc": "BDY",
                "cloirfap": "BDY",
                "cmrcifou": "BH",
                "ofmdcgen": "02",
                "ofmdsupd": "02",
                "ofddissu": "LB",
                "ouchbkcu": "BH",
                "oxcgrate": "BHD",
                "locamtou": "BDY",
                "stdcifou": "BH",
                "stdcrdou": "BDY",
            }
            if not fields or fields[0] != prefixes.get(contract):
                raise ValueError(f"Invalid {spec.output_code} body record")
            if contract in {"ofmdcgen", "ofmdsupd"}:
                if len(fields) < spec.body_field_count:
                    raise ValueError(f"Invalid {spec.output_code} body field count")
            elif len(fields) != spec.body_field_count:
                raise ValueError(f"Invalid {spec.output_code} body field count")

            flag_indexes = {
                "oacmassc": 9,
                "cmrcifou": 3,
                "ouchbkcu": 6,
                "locamtou": 4,
                "stdcifou": 3,
                "stdcrdou": 10,
            }
            if contract in flag_indexes:
                flag = fields[flag_indexes[contract]]
                if flag not in {"Y", "N"}:
                    raise ValueError(f"{spec.output_code} body status must be Y or N")
            else:
                status_indexes = {
                    "cloirfap": 4,
                    "ofddissu": 56,
                    "ofmdcgen": 7,
                    "ofmdsupd": 4,
                    "oxcgrate": 4,
                }
                status = fields[status_indexes[contract]]
                if not status:
                    raise ValueError(f"{spec.output_code} body status is empty")
                continue
        positive += int(flag == "Y")
        negative += int(flag == "N")

    if not spec.has_footer:
        return
    footer_line = lines[-1]
    if contract in {"chiclou", "cmrclou"}:
        if footer_line != "LF^":
            raise ValueError(f"Invalid {spec.output_code} footer")
        return
    if contract == "glcrte_fixed":
        if footer_line != "LF":
            raise ValueError("Invalid OFGLCRTE footer")
        return
    if contract == "ofobtupd":
        footer = footer_line.split(";")
        if len(footer) != 2 or footer[0] != "03":
            raise ValueError("Invalid OFOBTUPD footer")
        if _plain_count(footer[1]) != body_count:
            raise ValueError("OFOBTUPD footer does not match its body")
        return
    if contract == "fixed_payment":
        match = re.fullmatch(r"03([0-9]{10})", footer_line)
        if match is None or int(match.group(1)) != body_count:
            raise ValueError("Invalid fixed-width output footer")
        return
    if contract == "cmradcho":
        footer = footer_line.split("^")
        if len(footer) != 2 or footer[0] != "03":
            raise ValueError("Invalid CMRADCHO footer")
        if _plain_count(footer[1]) != body_count:
            raise ValueError("CMRADCHO footer does not match its body")
        return
    if contract == "cmrrelvo":
        footer = footer_line.split("^")
        if len(footer) != 4 or footer[0] != "LF":
            raise ValueError("Invalid CMRRELVO footer")
        counts = [_plain_count(value) for value in footer[1:]]
        if counts != [positive, negative, body_count]:
            raise ValueError("CMRRELVO footer does not match its body")
        return

    footer = _fields(footer_line)
    if len(footer) != spec.footer_field_count or footer[0] != spec.footer_record:
        raise ValueError(f"Invalid {spec.output_code} footer")
    counts = [_plain_count(value) for value in footer[1:]]
    if contract == "dcstout":
        if counts != [body_count, positive, negative]:
            raise ValueError("DCSTOUT footer does not match its body")
    elif contract == "ifoarlcg":
        # QA's trailer counts upload-master P/non-P statuses, not the SUCC/ERR
        # statuses rendered from IFTB_CLEARING_UPLOAD. Only their total is
        # expected to match the one-to-one body population.
        if sum(counts) != body_count:
            raise ValueError("IFOARLCG footer does not match its body")
    elif contract in {"chbookou", "giupd", "locamtou", "oacmassc"}:
        if counts != [positive, negative]:
            raise ValueError(f"{spec.output_code} footer does not match its body")
    elif contract in {
        *CHISALOU_CONTRACTS,
        "cmrcifou",
        "ouchbkcu",
        "stdcifou",
        "stdcrdou",
    }:
        if counts[0] < positive or counts[1] < negative:
            raise ValueError(
                f"{spec.output_code} footer counts are smaller than its body"
            )
    elif counts != [body_count]:
        raise ValueError(f"{spec.output_code} footer does not match its body")


def _validate_cladchgo_lines(lines: Sequence[str]) -> None:
    if len(lines) < 2:
        raise ValueError("Output must contain a header and footer")
    for line in lines:
        if "\r" in line or "\n" in line:
            raise ValueError("Output fields cannot contain line breaks")

    header = lines[0].split("^")
    if len(header) != 2 or header[0] != "01":
        raise ValueError("Invalid CLADCHGO header")
    validate_file_date(header[1])

    for line in lines[1:-1]:
        if not line.startswith("02"):
            raise ValueError("Every CLADCHGO body line must start with 02")
        fields = line.split("^")
        if len(fields) == 4:
            if fields[3] != "":
                raise ValueError("Invalid CLADCHGO body")
        elif len(fields) == 5:
            if not fields[3]:
                raise ValueError("CLADCHGO error body must contain an error code")
        else:
            raise ValueError(
                "CLADCHGO body must contain 4 base or 5 error caret-delimited fields"
            )
        if fields[2] not in {"P", "E"}:
            raise ValueError("CLADCHGO body status must be P or E")

    footer = lines[-1].split("^")
    if len(footer) != 2 or footer[0] != "03":
        raise ValueError("Invalid CLADCHGO footer")
    try:
        count = int(footer[1])
    except ValueError as exc:
        raise ValueError("Footer count must be a non-negative integer") from exc
    if count < 0 or str(count) != footer[1]:
        raise ValueError("Footer count must be a non-negative integer")
    if count != len(lines) - 2:
        raise ValueError("CLADCHGO footer count does not match its body records")


def _validate_ofcrelvp_lines(lines: Sequence[str]) -> None:
    if len(lines) < 2:
        raise ValueError("Output must contain a header and footer")
    for line in lines:
        if "\r" in line or "\n" in line:
            raise ValueError("Output fields cannot contain line breaks")
        if line.endswith("^"):
            raise ValueError("OFCRELVP lines must not end with '^'")

    header = lines[0].split("^")
    if len(header) != 2 or header[0] != "LH":
        raise ValueError("Invalid OFCRELVP header")
    validate_file_date(header[1])

    processed = 0
    errors = 0
    for line in lines[1:-1]:
        fields = line.split("^")
        if not fields or fields[0] != "BH":
            raise ValueError("Every OFCRELVP body line must start with BH")
        if len(fields) == 5:
            if fields[4] != "Y":
                raise ValueError("OFCRELVP processed body must end with Y")
            processed += 1
        elif len(fields) == 6:
            errors += 1
        else:
            raise ValueError(
                "OFCRELVP body must contain 5 processed or 6 error "
                "caret-delimited fields"
            )

    footer = lines[-1].split("^")
    if len(footer) != 4 or footer[0] != "LF":
        raise ValueError("Invalid OFCRELVP footer")
    try:
        counts = [int(value) for value in footer[1:]]
    except ValueError as exc:
        raise ValueError("Footer counts must be non-negative integers") from exc
    if any(value < 0 or str(value) != raw for value, raw in zip(counts, footer[1:])):
        raise ValueError("Footer counts must be non-negative integers")
    body_count = len(lines) - 2
    if counts != [processed, errors, body_count]:
        raise ValueError("OFCRELVP footer does not match its body statuses")


def _validate_oacmclos_lines(lines: Sequence[str]) -> None:
    if len(lines) < 2:
        raise ValueError("Output must contain a header and footer")
    if any("\r" in line or "\n" in line for line in lines):
        raise ValueError("Output fields cannot contain line breaks")

    header = _fields(lines[0])
    if len(header) != 3 or header[0] != "HDR":
        raise ValueError("Invalid OACMCLOS header")
    name_match = re.fullmatch(r"OACMCLOS_([0-9]{8})([0-9]{6})", header[1])
    if name_match is None or not re.fullmatch(r"[0-9]{14}", header[2]):
        raise ValueError("Invalid OACMCLOS header")
    filename_date = validate_file_date(name_match.group(1))
    filename_time = name_match.group(2)
    try:
        datetime.strptime(filename_time, "%H%M%S")
        execution_date = datetime.strptime(header[2][:8], "%d%m%Y").strftime(
            "%Y%m%d"
        )
    except ValueError as exc:
        raise ValueError("OACMCLOS header timestamp is not valid") from exc
    package_clock = filename_time[:2] + execution_date[4:6] + filename_time[4:]
    if header[2][8:] != package_clock:
        raise ValueError("OACMCLOS header filename and timestamp do not match")

    processed = 0
    unprocessed = 0
    for line in lines[1:-1]:
        if not line.endswith(";"):
            raise ValueError("Every OACMCLOS body line must end with ';'")
        fields = line.split(";", 4)
        if len(fields) != 5 or fields[0] != "BDY":
            raise ValueError("Invalid OACMCLOS body")
        flag = fields[3]
        if flag not in {"Y", "N"}:
            raise ValueError("OACMCLOS body status must be Y or N")
        processed += int(flag == "Y")
        unprocessed += int(flag == "N")

    footer_match = re.fullmatch(r"FTR;([0-9]+),([0-9]+),", lines[-1])
    if footer_match is None:
        raise ValueError("Invalid OACMCLOS footer")
    raw_counts = footer_match.groups()
    counts = [int(value) for value in raw_counts]
    if any(str(value) != raw for value, raw in zip(counts, raw_counts)):
        raise ValueError("Footer counts must be non-negative integers")
    if counts != [processed, unprocessed]:
        raise ValueError("OACMCLOS footer does not match its body statuses")


def serialize_lines(lines: Sequence[str]) -> bytes:
    return ("\n".join(lines) + "\n").encode("utf-8")
