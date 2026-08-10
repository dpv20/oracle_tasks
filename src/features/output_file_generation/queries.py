"""Controlled SQLcl scripts used by output-file reconstruction.

Every public builder accepts only validated scalar values and chooses table names
from an allowlist.  The feature never exposes an arbitrary-SQL entry point.
"""
from __future__ import annotations

import re
import uuid
from collections.abc import Iterable
from dataclasses import dataclass

from .formats import normalize_interface_code, validate_file_date, validate_process_ref
from .models import DataSourceChoice
from .upload_adapters import UploadBodyAdapter, upload_body_adapter


_TABLES = {
    DataSourceChoice.ACTIVE: "GITU_UPLOAD_MASTER",
    DataSourceChoice.ARCHIVE: "GITA_UPLOAD_MASTER",
}
_FILE_LOG_TABLES = {
    DataSourceChoice.ACTIVE: "GITB_FILE_LOG",
    DataSourceChoice.ARCHIVE: "GITA_FILE_LOG",
}
_ORACLE_IDENTIFIER_RE = re.compile(r"^[A-Z][A-Z0-9_$#]{0,127}$")


@dataclass(frozen=True)
class FramedQuery:
    script: str
    begin_marker: str
    end_marker: str


def _hex_utf8(expression: str) -> str:
    return (
        "nvl(rawtohex(utl_i18n.string_to_raw(" + expression
        + ", 'AL32UTF8')), '')"
    )


def _build_upload_adapter_sql(
    *,
    table: str,
    process_ref_no: str,
    input_code: str,
    adapter: UploadBodyAdapter,
) -> str:
    columns = [
        _hex_utf8("nvl(upper(trim(u.status)), '<NULL>')"),
        _hex_utf8("u.status"),
        *(_hex_utf8(expression) for expression in adapter.fields),
    ]
    filters = [
        f"u.process_ref_no = '{process_ref_no}'",
        f"upper(trim(u.interface_code)) = '{input_code}'",
        *adapter.filters,
    ]
    return (
        "select "
        + " || '|' ||\n       ".join(columns)
        + f"\n  from {table} u\n where "
        + "\n   and ".join(filters)
        + f"\n order by {adapter.order_by}"
    )


def _build_chisalca_sql(
    *,
    table: str,
    process_ref_no: str,
    archive: bool,
) -> str:
    """Read only the CHISALCA upload fields needed by its QA contract.

    Teller rows are deliberately resolved in a separate, literal-key query.
    Joining ``GITA_UPLOAD_MASTER`` to ``DETB_RTL_TELLER`` made Oracle scan the
    large historical population even for a five-row process.
    """
    columns = (
        _hex_utf8("nvl(upper(trim(u.status)), '<NULL>')"),
        _hex_utf8("u.status"),
        _hex_utf8("trim(u.fld22)"),
        _hex_utf8("trim(u.fld6)"),
        _hex_utf8("trim(u.fld4)"),
        _hex_utf8("trim(u.fld8)"),
        _hex_utf8(
            "case when trim(u.status) = 'P' then null else "
            "to_char(to_date(trim(u.fld16), 'YYYYMMDD'), 'YYYYMMDD') end"
        ),
        _hex_utf8("trim(u.fld7)"),
        _hex_utf8("trim(u.fld199)"),
        _hex_utf8("trim(u.fld200)"),
    )
    projection = " || '|' ||\n       ".join(columns)
    if archive:
        # GITA_UPLOAD_MASTER has billions of rows in PROD and no
        # PROCESS_REF_NO index. GITA_FILE_LOG retains the non-sensitive index
        # tuple used by CHISALCA; its logical upload name uses underscores
        # where the file log uses dots. The final PROCESS_REF_NO predicate and
        # the service-level row-count check preserve process isolation.
        return f"""
with log_keys as (
       select /*+ materialize */ distinct
              trim(l.external_system) external_system,
              l.archival_date,
              replace(trim(l.file_name), '.', '_') upload_file_name
         from GITA_FILE_LOG l
        where l.process_ref_no = '{process_ref_no}'
          and l.interface_code = 'CHISALCA'
     )
select /*+ leading(k) use_nl(u) index(u INX01_GITA_UPLOAD_MASTER) */
       {projection}
  from log_keys k
  join {table} u
    on u.branch_code is null
   and u.external_system = k.external_system
   and u.interface_code = 'CHISALCA'
   and u.archival_date = k.archival_date
   and u.file_name = k.upload_file_name
   and u.target_table = 'DETB_UPLOAD_RTL_TELLER'
 where u.process_ref_no = '{process_ref_no}'
 order by u.record_reference
"""
    return (
        "select "
        + projection
        + f"\n  from {table} u"
        + f"\n where u.process_ref_no = '{process_ref_no}'"
        + "\n   and upper(trim(u.interface_code)) = 'CHISALCA'"
        + "\n   and u.target_table = 'DETB_UPLOAD_RTL_TELLER'"
        + "\n order by u.record_reference"
    )


def build_chisalca_teller_query(xrefs: Iterable[str]) -> FramedQuery:
    """Resolve a bounded set of Oracle-derived XREF values through its index.

    Values are interpolated only as UTF-8 hex and decoded by Oracle. Keeping
    the function off ``t.xref`` lets ``IND01_DETBS_RTL_TELLER`` service the
    literal IN-list instead of forcing a full table scan.
    """
    encoded_by_xref: dict[str, str] = {}
    for raw_xref in xrefs:
        xref = str(raw_xref or "")
        if not xref or len(xref.encode("utf-8")) > 4000:
            raise ValueError("Oracle returned an invalid CHISALCA XREF")
        encoded_by_xref[xref] = xref.encode("utf-8").hex().upper()
    if not encoded_by_xref:
        raise ValueError("At least one CHISALCA XREF is required")
    if len(encoded_by_xref) > 500:
        raise ValueError("Too many CHISALCA XREF values in one query")

    literals = ",\n       ".join(
        "utl_i18n.raw_to_char(hextoraw('"
        + encoded
        + "'), 'AL32UTF8')"
        for encoded in sorted(encoded_by_xref.values())
    )
    columns = (
        _hex_utf8("trim(t.xref)"),
        _hex_utf8("to_char(count(*))"),
        _hex_utf8("min(trim(t.trn_ref_no))"),
        _hex_utf8("min(trim(t.xref))"),
        _hex_utf8("min(trim(t.txn_acc))"),
        _hex_utf8("min(trim(t.branch_code))"),
        _hex_utf8(
            "to_char(min(t.txn_amount), 'TM9', "
            "'NLS_NUMERIC_CHARACTERS=''.,''')"
        ),
        _hex_utf8("min(to_char(t.trn_dt, 'YYYYMMDD'))"),
        _hex_utf8("min(trim(t.txn_ccy))"),
    )
    return _frame(
        "select /*+ index(t IND01_DETBS_RTL_TELLER) */ "
        + " || '|' ||\n       ".join(columns)
        + "\n  from DETB_RTL_TELLER t"
        + f"\n where t.xref in ({literals})"
        + "\n group by trim(t.xref)"
        + "\n order by trim(t.xref)",
        "CHISALCA_TELLER",
    )


def _build_auxiliary_adapter_sql(
    *,
    owner: str,
    process_ref_no: str,
    adapter: UploadBodyAdapter,
) -> str:
    schema = str(owner or "").strip().upper()
    if not _ORACLE_IDENTIFIER_RE.fullmatch(schema):
        raise ValueError("Oracle returned an invalid auxiliary table owner")
    columns = [
        _hex_utf8("nvl(upper(trim(u.status)), '<NULL>')"),
        _hex_utf8("u.status"),
        *(_hex_utf8(expression) for expression in adapter.fields),
    ]
    return (
        "select "
        + " || '|' ||\n       ".join(columns)
        + f"\n  from {schema}.GITM_UDF_UPLOAD_DETAILS u"
        + f"\n where u.process_ref_no = '{process_ref_no}'"
        + f"\n order by {adapter.order_by}"
    )


def _build_dcstout_sql(
    *,
    table: str,
    file_log_table: str,
    process_ref_no: str,
) -> str:
    """Build the QA-derived DCSTOUT body without invoking its package.

    The package reads GLOBAL.USER_ID/CURRENT_BRANCH when FLD5/FLD6 are NULL.
    Their only process-bound persisted equivalents are the coherent
    USER_ID/BRANCH_CODE tuple in the selected active/archive file log.
    """
    context_filter = (
        f"l.process_ref_no = '{process_ref_no}' "
        "and upper(trim(l.interface_code)) = 'IFSTDCST' "
        "and trim(l.user_id) is not null "
        "and trim(l.branch_code) is not null"
    )
    encoded_tuple = (
        "rawtohex(utl_i18n.string_to_raw(trim(l.user_id), 'AL32UTF8')) || ':' || "
        "rawtohex(utl_i18n.string_to_raw(trim(l.branch_code), 'AL32UTF8'))"
    )
    context_count = (
        f"(select to_char(count(distinct {encoded_tuple})) "
        f"from {file_log_table} l where {context_filter})"
    )
    context_user_lookup = (
        f"(select min(trim(l.user_id)) from {file_log_table} l "
        f"where {context_filter})"
    )
    context_branch_lookup = (
        f"(select min(trim(l.branch_code)) from {file_log_table} l "
        f"where {context_filter})"
    )
    fallback_needed = "u.fld5 is null or u.fld6 is null"
    columns = [
        _hex_utf8(
            "case when u.status is null then '<NULL>' "
            "when u.status = 'P' then 'P' else '<NON_P>' end"
        ),
        _hex_utf8("u.status"),
        _hex_utf8("upper(trim(u.interface_code))"),
        _hex_utf8(
            f"(select to_char(count(*)) from {table} p "
            f"where p.process_ref_no = '{process_ref_no}')"
        ),
        _hex_utf8(
            f"(select to_char(count(*)) from {table} p "
            f"where p.process_ref_no = '{process_ref_no}' "
            "and upper(trim(p.interface_code)) = 'IFSTDCST')"
        ),
        _hex_utf8(
            f"case when {fallback_needed} then {context_count} else '0' end"
        ),
        _hex_utf8(
            f"case when {fallback_needed} then '1' else '0' end"
        ),
        _hex_utf8(
            f"case when {fallback_needed} then {context_user_lookup} else null end"
        ),
        _hex_utf8(
            f"case when {fallback_needed} then {context_branch_lookup} else null end"
        ),
        _hex_utf8(
            f"(select to_char(count(*)) from {table} r "
            "where r.record_reference = u.record_reference)"
        ),
        _hex_utf8("trim(u.fld1)"),
        _hex_utf8("trim(u.fld2)"),
        _hex_utf8("trim(u.fld3)"),
        _hex_utf8("trim(u.fld4)"),
        _hex_utf8(
            f"case when u.fld5 is null then {context_user_lookup} else trim(u.fld5) end"
        ),
        _hex_utf8(
            f"case when u.fld6 is null then {context_branch_lookup} else trim(u.fld6) end"
        ),
        _hex_utf8("trim(u.error)"),
        _hex_utf8(
            "(select to_char(count(*)) from ERTB_MSGS m "
            "where m.err_code = u.error and m.language = 'ESP')"
        ),
        _hex_utf8(
            "(select min(trim(m.type)) from ERTB_MSGS m "
            "where m.err_code = u.error and m.language = 'ESP')"
        ),
        _hex_utf8(
            "(select min(trim(replace(m.message, '!', ''))) from ERTB_MSGS m "
            "where m.err_code = u.error and m.language = 'ESP')"
        ),
    ]
    return (
        "select "
        + " || '|' ||\n       ".join(columns)
        + f"\n  from {table} u"
        + f"\n where u.process_ref_no = '{process_ref_no}'"
        + "\n   and upper(trim(u.interface_code)) = 'IFSTDCST'"
        + "\n order by u.record_reference"
    )


def _build_ifoarlcg_sql(*, process_ref_no: str, file_date: str) -> str:
    """Build IFOARLCG's live clearing cursor with explicit integrity metadata."""
    date = validate_file_date(file_date)
    columns = [
        _hex_utf8("decode(a.status, 'SUCC', 'P', 'ERR', 'E', '<INVALID>')"),
        _hex_utf8("a.status"),
        _hex_utf8("a.source_count"),
        _hex_utf8("nvl(b.child_match_count, '0')"),
        _hex_utf8("a.xref_count"),
        _hex_utf8("a.xref"),
        _hex_utf8("a.fccref"),
        _hex_utf8("a.instrno"),
        _hex_utf8("a.benbranch"),
        _hex_utf8("a.benaccount"),
        _hex_utf8(
            "to_char(a.instramt, 'TM9', "
            "'NLS_NUMERIC_CHARACTERS=''.,''')"
        ),
        _hex_utf8("a.instrno2"),
        _hex_utf8("a.sector_code"),
        _hex_utf8("a.rembank"),
        _hex_utf8("a.remaccount"),
        _hex_utf8("b.document_type"),
        _hex_utf8("a.error_codes"),
    ]
    return (
        "select "
        + " || '|' ||\n       ".join(columns)
        + "\n  from ("
        + "\n        select source_rows.*,"
        + "\n               to_char(count(*) over ()) source_count,"
        + "\n               to_char(count(*) over (partition by xref)) xref_count"
        + "\n          from IFTB_CLEARING_UPLOAD source_rows"
        + "\n         where source_rows.scode = 'IFEARLCG'"
        + f"\n           and source_rows.unit_id = {process_ref_no}"
        + f"\n           and source_rows.upload_date = to_date('{date}', 'YYYYMMDD')"
        + "\n       ) a"
        + "\n  left join ("
        + "\n        select child_rows.*,"
        + "\n               to_char(count(*) over ("
        + "\n                 partition by scode, xref, entry_no"
        + "\n               )) child_match_count"
        + "\n          from IFTB_CLEARING_UPLOAD_C child_rows"
        + "\n         where child_rows.scode = 'IFEARLCG'"
        + "\n       ) b"
        + "\n    on b.scode = a.scode"
        + "\n   and b.xref = a.xref"
        + "\n   and b.entry_no = a.entry_no"
        + "\n order by a.xref"
    )


def _build_ofchkprt_sql(*, process_ref_no: str, file_date: str) -> str:
    """Reproduce QA ``GIPKS_OFCHKPRT.CR_BDY`` using PROD read-only tables.

    The QA package names editioning views with an ``S`` suffix; the PROD
    account exposes the singular base objects used below. ``UNION`` (not
    ``UNION ALL``) and its sole ``REC_REF`` ordering are client-visible parts
    of the package contract. Integrity metadata is transported but never
    emitted, allowing the service to reject incomplete or non-deterministic
    reconstructions before writing a file.
    """
    columns = [
        _hex_utf8("case when r.proc_stat = 'S' then 'S' else '<NON_S>' end"),
        _hex_utf8("r.proc_stat"),
        _hex_utf8("to_char(r.union_count)"),
        _hex_utf8("to_char(r.rec_ref_count)"),
        _hex_utf8("to_char(r.rec_ref)"),
        _hex_utf8("to_char(r.upload_ref_count)"),
        _hex_utf8("to_char(r.check_match_count)"),
        _hex_utf8("to_char(r.message_dependency_count)"),
        _hex_utf8("r.scode"),
        _hex_utf8("r.txn_brn"),
        _hex_utf8("r.rem_brn"),
        _hex_utf8("r.rem_account"),
        _hex_utf8("r.rem_cif"),
        _hex_utf8("r.remdetail1"),
        _hex_utf8("r.documentty"),
        _hex_utf8("r.instrno1"),
        _hex_utf8("r.bank_code"),
        _hex_utf8("r.sector_cod"),
        _hex_utf8("r.txn_ccy"),
        _hex_utf8(
            "to_char(r.txn_amount, 'FM999999999999999V999')"
        ),
        _hex_utf8(
            "to_char(r.tax_amount, 'FM999999999999999V999')"
        ),
        _hex_utf8("r.clg_trn_ref"),
        _hex_utf8("r.xref"),
        _hex_utf8("r.prot_ref_no"),
        _hex_utf8("r.reasoncode"),
        _hex_utf8("to_char(r.prot_date, 'RRRRMMDD')"),
        _hex_utf8("r.cheq_stat"),
        _hex_utf8("r.err_code"),
        _hex_utf8("r.err_msg"),
    ]
    return (
        "with union_rows as (\n"
        "      select trim(gim.fld3) scode,\n"
        "             trim(gim.fld13) txn_brn,\n"
        "             trim(gim.fld13) rem_brn,\n"
        "             trim(gim.fld11) rem_account,\n"
        "             trim(gim.fld16) rem_cif,\n"
        "             trim(gim.fld17) remdetail1,\n"
        "             trim(gim.fld29) documentty,\n"
        "             trim(gim.fld5) instrno1,\n"
        "             trim(gim.fld7) bank_code,\n"
        "             trim(gim.fld8) sector_cod,\n"
        "             trim(gim.fld10) txn_ccy,\n"
        "             to_number(trim(gim.fld9)) txn_amount,\n"
        "             to_number(trim(gim.fld31)) tax_amount,\n"
        "             null clg_trn_ref,\n"
        "             trim(gim.fld30) xref,\n"
        "             null prot_ref_no,\n"
        "             trim(gim.fld20) reasoncode,\n"
        f"             to_date('{file_date}', 'YYYYMMDD') prot_date,\n"
        "             gim.status proc_stat,\n"
        "             gim.error err_code,\n"
        "             gim.error_param err_msg,\n"
        "             gim.record_reference rec_ref\n"
        "        from GITU_UPLOAD_MASTER gim,\n"
        "             GITM_PROTEST_LOG gp\n"
        "       where gim.interface_code = 'IFCHKPRT'\n"
        "         and gim.process_ref_no = gp.process_ref_no\n"
        "         and gim.record_reference = gp.record_reference\n"
        "         and trim(gim.fld30) = gp.xref\n"
        f"         and gim.process_ref_no = '{process_ref_no}'\n"
        "         and gim.status <> 'P'\n"
        "         and not exists (\n"
        "               select 1\n"
        "                 from CGTB_PROTEST_REJECT_UPLOAD pru\n"
        "                where pru.xref = gp.xref\n"
        "                  and pru.instrno1 = gp.instrno1\n"
        "             )\n"
        "      union\n"
        "      select pru.scode,\n"
        "             pru.txn_brn,\n"
        "             pru.rem_acc_branch,\n"
        "             pru.rem_account,\n"
        "             pru.rem_cif,\n"
        "             pru.rem_details1,\n"
        "             pru.document_type,\n"
        "             pru.instrno1,\n"
        "             pru.bank_code,\n"
        "             pru.sector_code,\n"
        "             pru.txn_ccy,\n"
        "             pru.txn_amount,\n"
        "             pru.tax_amount,\n"
        "             null,\n"
        "             pru.xref,\n"
        "             null,\n"
        "             pru.reason_code1,\n"
        "             pru.protest_date,\n"
        "             pru.status,\n"
        "             pru.error_codes,\n"
        "             pru.error_params,\n"
        "             gp.record_reference\n"
        "        from CGTB_PROTEST_REJECT_UPLOAD pru,\n"
        "             GITM_PROTEST_LOG gp\n"
        "       where pru.instrno1 = gp.instrno1\n"
        "         and pru.xref = gp.xref\n"
        f"         and gp.process_ref_no = '{process_ref_no}'\n"
        "         and gp.interface_code = 'IFCHKPRT'\n"
        "         and pru.status <> 'S'\n"
        "      union\n"
        "      select pru.scode,\n"
        "             pru.txn_brn,\n"
        "             pru.rem_acc_branch,\n"
        "             pru.rem_account,\n"
        "             pru.rem_cif,\n"
        "             pru.rem_details1,\n"
        "             pru.document_type,\n"
        "             pru.instrno1,\n"
        "             pru.bank_code,\n"
        "             pru.sector_code,\n"
        "             pru.txn_ccy,\n"
        "             pru.txn_amount,\n"
        "             pru.tax_amount,\n"
        "             cpm.module_reference_no,\n"
        "             pru.xref,\n"
        "             cpm.prot_trn_ref_no,\n"
        "             pru.reason_code1,\n"
        "             pru.protest_date,\n"
        "             pru.status,\n"
        "             pru.error_codes,\n"
        "             pru.error_params,\n"
        "             gp.record_reference\n"
        "        from CGTB_PROTEST_REJECT_UPLOAD pru,\n"
        "             GITM_PROTEST_LOG gp,\n"
        "             CATM_PROTEST_MASTER cpm\n"
        "       where pru.instrno1 = gp.instrno1\n"
        "         and pru.xref = gp.xref\n"
        f"         and gp.process_ref_no = '{process_ref_no}'\n"
        "         and gp.interface_code = 'IFCHKPRT'\n"
        "         and pru.xref = cpm.xref\n"
        "         and pru.status = 'S'\n"
        "), ranked as (\n"
        "      select q.*,\n"
        "             count(*) over () union_count,\n"
        "             count(*) over (partition by rec_ref) rec_ref_count\n"
        "        from union_rows q\n"
        "), resolved as (\n"
        "      select q.*,\n"
        "             (select count(*)\n"
        "                from GITU_UPLOAD_MASTER u\n"
        f"               where u.process_ref_no = '{process_ref_no}'\n"
        "                 and upper(trim(u.interface_code)) = 'IFCHKPRT'\n"
        "                 and u.record_reference = q.rec_ref) upload_ref_count,\n"
        "             case\n"
        "               when nvl(trim(q.documentty), '01') = '01' then\n"
        "                 (select count(*)\n"
        "                    from CATM_CHECK_DETAILS ca\n"
        "                   where ca.branch = q.rem_brn\n"
        "                     and ca.account = q.rem_account\n"
        "                     and ca.check_no = q.instrno1\n"
        "                     and ca.mod_no = (\n"
        "                           select max(ca2.mod_no)\n"
        "                             from CATM_CHECK_DETAILS ca2\n"
        "                            where ca2.branch = q.rem_brn\n"
        "                              and ca2.account = q.rem_account\n"
        "                              and ca2.check_no = q.instrno1\n"
        "                         ))\n"
        "               else 0\n"
        "             end check_match_count,\n"
        "             case\n"
        "               when nvl(trim(q.documentty), '01') = '01' then\n"
        "                 (select case when count(*) = 1 then min(ca.status) else null end\n"
        "                    from CATM_CHECK_DETAILS ca\n"
        "                   where ca.branch = q.rem_brn\n"
        "                     and ca.account = q.rem_account\n"
        "                     and ca.check_no = q.instrno1\n"
        "                     and ca.mod_no = (\n"
        "                           select max(ca2.mod_no)\n"
        "                             from CATM_CHECK_DETAILS ca2\n"
        "                            where ca2.branch = q.rem_brn\n"
        "                              and ca2.account = q.rem_account\n"
        "                              and ca2.check_no = q.instrno1\n"
        "                         ))\n"
        "               else null\n"
        "             end cheq_stat,\n"
        "             case when q.proc_stat = 'S' then 0 else\n"
        "               (select count(*)\n"
        "                  from ERTB_MSGS m\n"
        "                 where instr(';' || q.err_code || ';',\n"
        "                             ';' || m.err_code || ';') > 0)\n"
        "             end message_dependency_count\n"
        "        from ranked q\n"
        ")\n"
        "select "
        + " || '|' ||\n       ".join(columns)
        + "\n  from resolved r\n order by rec_ref"
    )


def _build_ofiwdclg_sql(*, process_ref_no: str) -> str:
    """Reproduce QA ``GIPKS_OFIWDCLG.CR_FB`` from active PROD sources.

    ``INSTRAMT`` is a NUMBER assigned implicitly to VARCHAR2 by the package.
    PROD was verified with ``NLS_NUMERIC_CHARACTERS='.,'``; explicit ``TM9``
    makes that same conversion deterministic across SQLcl sessions.
    """
    columns = [
        _hex_utf8("case when r.upldstat = 'P' then 'P' else '<NON_P>' end"),
        _hex_utf8("r.upldstat"),
        _hex_utf8("to_char(r.union_count)"),
        _hex_utf8("to_char(r.record_ref_count)"),
        _hex_utf8("r.record_reference"),
        _hex_utf8("to_char(r.upload_ref_count)"),
        _hex_utf8("to_char(r.log_ref_count)"),
        _hex_utf8("to_char(r.ifc_match_count)"),
        _hex_utf8("to_char(r.master_match_count)"),
        _hex_utf8("r.xref"),
        _hex_utf8("to_char(r.txndate, 'RRRRMMDD')"),
        _hex_utf8("r.benbank"),
        _hex_utf8("r.rembrn"),
        _hex_utf8("r.remaccount"),
        _hex_utf8("r.instrno"),
        _hex_utf8("r.doctype"),
        _hex_utf8(
            "to_char(r.instramt, 'TM9', "
            "'NLS_NUMERIC_CHARACTERS=''.,''')"
        ),
        _hex_utf8("to_char(r.instrdate, 'RRRRMMDD')"),
        _hex_utf8("r.clearingtype"),
        _hex_utf8("r.sectorcode"),
        _hex_utf8("r.txn_status"),
        _hex_utf8("r.fccref"),
        _hex_utf8("r.err_code"),
        _hex_utf8("r.error_msg"),
    ]
    return (
        "with union_rows as (\n"
        "      select gic.xref xref,\n"
        "             gic.txndate txndate,\n"
        "             gic.rembank benbank,\n"
        "             gic.txn_brn rembrn,\n"
        "             gic.remaccount remaccount,\n"
        "             gic.instrno instrno,\n"
        "             gic.instrno2 doctype,\n"
        "             gic.instramt instramt,\n"
        "             to_date(giu.fld15, 'RRRRMMDD') instrdate,\n"
        "             gic.clearing_type clearingtype,\n"
        "             gic.sector_code sectorcode,\n"
        "             giu.status upldstat,\n"
        "             'NOPR' txn_status,\n"
        "             null fccref,\n"
        "             decode(giu.status, 'U', 'GI-INT214', giu.error) err_code,\n"
        "             giu.error_param error_msg,\n"
        "             gic.record_reference record_reference\n"
        "        from GITU_UPLOAD_MASTER giu,\n"
        "             GITM_CLEARING_LOG gic\n"
        "       where trim(giu.fld22) = gic.xref\n"
        "         and 1 = gic.entry_no\n"
        "         and trim(giu.fld3) = gic.instrno\n"
        "         and giu.process_ref_no = gic.process_ref_no\n"
        "         and giu.record_reference = gic.record_reference\n"
        "         and giu.interface_code = 'IFIWDCLG'\n"
        f"         and gic.process_ref_no = '{process_ref_no}'\n"
        "         and giu.status <> 'P'\n"
        "         and not exists (\n"
        "               select 1\n"
        "                 from IFTB_CLEARING_UPLOAD ifc\n"
        "                where ifc.xref = gic.xref\n"
        "                  and ifc.entry_no = gic.entry_no\n"
        "                  and ifc.instrno = gic.instrno\n"
        "             )\n"
        "      union\n"
        "      select ifc.xref,\n"
        "             ifc.txndate,\n"
        "             ifc.benbank,\n"
        "             ifc.txn_brn,\n"
        "             ifc.remaccount,\n"
        "             ifc.instrno,\n"
        "             gic.instrno2,\n"
        "             ifc.instramt,\n"
        "             ifc.instrdate,\n"
        "             gic.clearing_type,\n"
        "             ifc.sector_code,\n"
        "             decode(ifc.status, null, 'E', 'ERR', 'E'),\n"
        "             'ERRO',\n"
        "             ifc.fccref,\n"
        "             ifc.error_codes,\n"
        "             ifc.error_params,\n"
        "             gic.record_reference\n"
        "        from IFTB_CLEARING_UPLOAD ifc,\n"
        "             GITM_CLEARING_LOG gic\n"
        "       where ifc.xref = gic.xref\n"
        "         and ifc.entry_no = gic.entry_no\n"
        "         and ifc.instrno = gic.instrno\n"
        f"         and gic.process_ref_no = '{process_ref_no}'\n"
        "         and gic.interface_code = 'IFIWDCLG'\n"
        "         and nvl(ifc.status, 'ERR') = 'ERR'\n"
        "      union\n"
        "      select ifc.xref,\n"
        "             ifc.txndate,\n"
        "             ifc.benbank,\n"
        "             ifc.txn_brn,\n"
        "             ifc.remaccount,\n"
        "             ifc.instrno,\n"
        "             gic.instrno2,\n"
        "             ifc.instramt,\n"
        "             ifc.instrdate,\n"
        "             gic.clearing_type,\n"
        "             ifc.sector_code,\n"
        "             decode(ifc.status, 'SUCC', 'P'),\n"
        "             clm.status,\n"
        "             ifc.fccref,\n"
        "             decode(clm.status, 'REJR', clm.err_code),\n"
        "             null,\n"
        "             gic.record_reference\n"
        "        from IFTB_CLEARING_UPLOAD ifc,\n"
        "             GITM_CLEARING_LOG gic,\n"
        "             CSTB_CLEARING_MASTER clm\n"
        "       where ifc.xref = gic.xref\n"
        "         and ifc.xref = clm.xref\n"
        "         and ifc.fccref = clm.reference_no\n"
        "         and ifc.entry_no = gic.entry_no\n"
        "         and ifc.instrno = gic.instrno\n"
        f"         and gic.process_ref_no = '{process_ref_no}'\n"
        "         and gic.interface_code = 'IFIWDCLG'\n"
        "         and ifc.status = 'SUCC'\n"
        "), ranked as (\n"
        "      select q.*,\n"
        "             count(*) over () union_count,\n"
        "             count(*) over (partition by record_reference) record_ref_count\n"
        "        from union_rows q\n"
        "), resolved as (\n"
        "      select q.*,\n"
        "             (select count(*)\n"
        "                from GITU_UPLOAD_MASTER u\n"
        f"               where u.process_ref_no = '{process_ref_no}'\n"
        "                 and upper(trim(u.interface_code)) = 'IFIWDCLG'\n"
        "                 and u.record_reference = q.record_reference) upload_ref_count,\n"
        "             (select count(*)\n"
        "                from GITM_CLEARING_LOG g\n"
        f"               where g.process_ref_no = '{process_ref_no}'\n"
        "                 and g.interface_code = 'IFIWDCLG'\n"
        "                 and g.record_reference = q.record_reference) log_ref_count,\n"
        "             (select count(*)\n"
        "                from IFTB_CLEARING_UPLOAD i\n"
        "                join GITM_CLEARING_LOG g\n"
        "                  on i.xref = g.xref\n"
        "                 and i.entry_no = g.entry_no\n"
        "                 and i.instrno = g.instrno\n"
        f"               where g.process_ref_no = '{process_ref_no}'\n"
        "                 and g.interface_code = 'IFIWDCLG'\n"
        "                 and g.record_reference = q.record_reference) ifc_match_count,\n"
        "             case when q.upldstat = 'P' then\n"
        "               (select count(*)\n"
        "                  from CSTB_CLEARING_MASTER c\n"
        "                 where c.xref = q.xref\n"
        "                   and c.reference_no = q.fccref)\n"
        "               else 0\n"
        "             end master_match_count\n"
        "        from ranked q\n"
        ")\n"
        "select "
        + " || '|' ||\n       ".join(columns)
        + "\n  from resolved r\n order by record_reference"
    )


def _build_ofiwadoc_sql(*, table: str, process_ref_no: str) -> str:
    """Reproduce QA ``GIPKS_OFIWADOC.CR_FB`` without invoking handoff.

    The package joins upload rows to the live ADOC clearing log by both
    process and record reference, even when the upload-master row has already
    moved to the archive.  Integrity columns are transported (but never
    emitted) so missing/ambiguous live dependencies and ordering ties stop the
    reconstruction before an incomplete client file can be written.
    """
    columns = [
        _hex_utf8("case when u.status = 'P' then 'P' else '<NON_P>' end"),
        _hex_utf8("u.status"),
        _hex_utf8("to_char(u.source_count)"),
        _hex_utf8("to_char(count(*) over (partition by u.record_reference))"),
        _hex_utf8("to_char(u.record_reference_count)"),
        _hex_utf8("g.xref"),
        _hex_utf8("to_char(g.txndate, 'RRRRMMDD')"),
        _hex_utf8("g.rembank"),
        _hex_utf8("g.txn_brn"),
        _hex_utf8("g.remaccount"),
        _hex_utf8("g.instrno"),
        _hex_utf8("g.instrno2"),
        _hex_utf8(
            "to_char(g.instramt, 'TM9', "
            "'NLS_NUMERIC_CHARACTERS=''.,''')"
        ),
        _hex_utf8("to_char(to_date(u.fld15, 'RRRRMMDD'), 'RRRRMMDD')"),
        _hex_utf8("g.clearing_type"),
        _hex_utf8("g.sector_code"),
        _hex_utf8("decode(u.status, 'U', 'GI-INT214', u.error)"),
        _hex_utf8("u.error_param"),
    ]
    return (
        "with upload_rows as (\n"
        "      select u.process_ref_no,\n"
        "             u.record_reference,\n"
        "             u.status,\n"
        "             u.error,\n"
        "             u.error_param,\n"
        "             u.fld15,\n"
        "             count(*) over () source_count,\n"
        "             count(*) over (partition by u.record_reference) "
        "record_reference_count\n"
        f"        from {table} u\n"
        f"       where u.process_ref_no = '{process_ref_no}'\n"
        "         and u.interface_code = 'IFIWADOC'\n"
        ")\n"
        "select "
        + " || '|' ||\n       ".join(columns)
        + "\n  from upload_rows u\n"
        "  join GITM_CLEARING_ADOC_LOG g\n"
        "    on g.process_ref_no = u.process_ref_no\n"
        "   and g.record_reference = u.record_reference\n"
        f"   and g.process_ref_no = '{process_ref_no}'\n"
        " order by u.record_reference"
    )


def _build_ofqsimtp_sql(*, table: str, process_ref_no: str) -> str:
    """Reproduce QA ``GIPKS_OFQSIMTP.CR_BD`` as a read-only outer join."""
    columns = [
        _hex_utf8(
            "case when p.status = 'P' then 'P' "
            "when p.status = 'E' then 'E' else '<UNSUPPORTED>' end"
        ),
        _hex_utf8("p.status"),
        _hex_utf8("to_char(p.record_reference)"),
        _hex_utf8(
            "to_char(count(*) over ("
            "partition by p.record_reference, r.sim_date))"
        ),
        _hex_utf8("trim(p.fld1)"),
        _hex_utf8("r.branch_code"),
        _hex_utf8("r.account_number"),
        _hex_utf8("p.error"),
        _hex_utf8("p.error_param"),
        _hex_utf8("to_char(r.value_date, 'YYYYMMDD')"),
        _hex_utf8("to_char(r.maturity_date, 'YYYYMMDD')"),
        _hex_utf8("r.product_code"),
        _hex_utf8("r.user_defined_status"),
        _hex_utf8("to_char(r.sim_date, 'YYYYMMDD')"),
        _hex_utf8("r.compname_amtout"),
    ]
    return (
        "select "
        + " || '|' ||\n       ".join(columns)
        + f"\n  from {table} p\n"
        "  left join CLTB_IFQSIMTP_LOG r\n"
        "    on trim(p.fld1) = r.alt_acc_no\n"
        f" where p.process_ref_no = '{process_ref_no}'\n"
        "   and p.interface_code = 'IFQSIMTP'\n"
        "   and p.target_table = 'CLTB_ACCOUNT_MASTER'\n"
        " order by p.record_reference, r.sim_date"
    )


def _build_stdinrou_sql(*, process_ref_no: str) -> str:
    """Reproduce QA ``GIPKS_STDINROU.CR_BDY`` from its custom source."""
    columns = [
        _hex_utf8("case when u.status = 'P' then 'P' else '<NON_P>' end"),
        _hex_utf8("u.status"),
        _hex_utf8("to_char(u.record_reference)"),
        _hex_utf8(
            "to_char(count(*) over (partition by u.record_reference))"
        ),
        _hex_utf8("trim(u.fld4)"),
        _hex_utf8("trim(u.fld2)"),
        _hex_utf8("trim(u.fld3)"),
        _hex_utf8("trim(u.fld5)"),
        _hex_utf8("u.error"),
        _hex_utf8("u.error_param"),
    ]
    return (
        "select "
        + " || '|' ||\n       ".join(columns)
        + "\n  from STDINRTS_UPLOAD_MASTER u\n"
        f" where u.process_ref_no = '{process_ref_no}'\n"
        " order by u.record_reference"
    )


def _frame(select_sql: str, purpose: str) -> FramedQuery:
    token = uuid.uuid4().hex.upper()
    label = re.sub(r"[^A-Z0-9_]", "_", purpose.upper())[:24]
    begin = f"__OTC_{label}_{token}_BEGIN__"
    end = f"__OTC_{label}_{token}_END__"
    sql = select_sql.strip().rstrip(";")
    script = f"""whenever oserror exit failure
whenever sqlerror exit sql.sqlcode
set define off
set echo off
set feedback off
set heading off
set pagesize 0
set newpage none
set linesize 32767
set long 1000000
set longchunksize 32767
set trimout on
set trimspool on
set tab off
prompt {begin}
{sql};
prompt {end}
exit success
"""
    return FramedQuery(script=script, begin_marker=begin, end_marker=end)


def parse_framed_output(stdout: str, query: FramedQuery) -> list[str]:
    lines = (stdout or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    begin_positions = [i for i, line in enumerate(lines) if line.strip() == query.begin_marker]
    end_positions = [i for i, line in enumerate(lines) if line.strip() == query.end_marker]
    if len(begin_positions) != 1 or len(end_positions) != 1:
        raise ValueError("SQLcl output markers are missing or duplicated")
    begin = begin_positions[0]
    end = end_positions[0]
    if end <= begin:
        raise ValueError("SQLcl output markers are out of order")
    # SQLcl can prefix the first selected row with a form-feed even with
    # PAGESIZE 0.  Remove that transport character without stripping spaces
    # that belong to the generated client record.
    selected = [line.lstrip("\f").rstrip() for line in lines[begin + 1 : end]]
    return [line for line in selected if line.strip()]


def output_outside_markers(stdout: str, query: FramedQuery) -> str:
    text = (stdout or "").replace("\r\n", "\n").replace("\r", "\n")
    start = text.find(query.begin_marker)
    end = text.find(query.end_marker)
    if start < 0 or end < start:
        return text
    return text[:start] + text[end + len(query.end_marker) :]


def build_discovery_query(process_ref_no: str) -> FramedQuery:
    ref = validate_process_ref(process_ref_no)
    return _frame(
        f"""
select 'UPLOAD_MASTER|ACTIVE|' || upper(trim(interface_code)) || '|' || count(*)
  from GITU_UPLOAD_MASTER
 where process_ref_no = '{ref}'
 group by upper(trim(interface_code))
union all
select 'UPLOAD_MASTER|ARCHIVE|' || upper(trim(interface_code)) || '|' || count(*)
  from GITA_UPLOAD_MASTER
 where process_ref_no = '{ref}'
 group by upper(trim(interface_code))
union all
select 'FILE_LOG|ACTIVE|' || upper(trim(interface_code)) || '|' || count(*)
  from GITB_FILE_LOG
 where process_ref_no = '{ref}'
 group by upper(trim(interface_code))
union all
select 'FILE_LOG|ARCHIVE|' || upper(trim(interface_code)) || '|' || count(*)
  from GITA_FILE_LOG
 where process_ref_no = '{ref}'
 group by upper(trim(interface_code))
order by 1
""",
        "DISCOVERY",
    )


def build_mapping_query(input_code: str) -> FramedQuery:
    return build_mappings_query((input_code,))


def build_mappings_query(input_codes: Iterable[str]) -> FramedQuery:
    """Read outgoing mappings for a controlled set of observed interfaces."""
    codes = sorted({normalize_interface_code(code) for code in input_codes})
    if not codes:
        raise ValueError("At least one interface code is required")
    if len(codes) > 1000:
        raise ValueError("Too many interface codes")
    literals = ", ".join(f"'{code}'" for code in codes)
    return _frame(
        f"""
select distinct upper(trim(interface_code)) || '|' ||
       upper(trim(outgoing_interface))
  from GITM_INTERFACE_DEFINITION
 where upper(trim(interface_code)) in ({literals})
   and trim(outgoing_interface) is not null
 order by 1
""",
        "MAPPING",
    )


def build_process_contract_query(
    process_ref_no: str,
    input_code: str,
    output_code: str,
    source: DataSourceChoice,
) -> FramedQuery:
    """Recheck upload existence/count and mapping immediately before save."""
    ref = validate_process_ref(process_ref_no)
    input_value = normalize_interface_code(input_code)
    output_value = normalize_interface_code(output_code)
    table = _table_for(source)
    if (
        input_value == "CHISALCA"
        and DataSourceChoice(source) is DataSourceChoice.ARCHIVE
    ):
        return _frame(
            f"""
with log_keys as (
       select /*+ materialize */ distinct
              trim(l.external_system) external_system,
              l.archival_date,
              replace(trim(l.file_name), '.', '_') upload_file_name
         from GITA_FILE_LOG l
        where l.process_ref_no = '{ref}'
          and l.interface_code = 'CHISALCA'
     ),
     upload_rows as (
       select /*+ leading(k) use_nl(u) index(u INX01_GITA_UPLOAD_MASTER) */
              u.record_reference
         from log_keys k
         join GITA_UPLOAD_MASTER u
           on u.branch_code is null
          and u.external_system = k.external_system
          and u.interface_code = 'CHISALCA'
          and u.archival_date = k.archival_date
          and u.file_name = k.upload_file_name
        where u.process_ref_no = '{ref}'
     )
select (select count(*) from upload_rows) || '|' ||
       (
         select count(*)
           from (
                 select distinct upper(trim(outgoing_interface)) output_code
                   from GITM_INTERFACE_DEFINITION
                  where upper(trim(interface_code)) = '{input_value}'
                    and trim(outgoing_interface) is not null
                )
       ) || '|' ||
       (
         select count(*)
           from (
                 select distinct upper(trim(outgoing_interface)) output_code
                   from GITM_INTERFACE_DEFINITION
                  where upper(trim(interface_code)) = '{input_value}'
                    and trim(outgoing_interface) is not null
                )
          where output_code = '{output_value}'
       )
  from dual
""",
            "PROCESS_CONTRACT",
        )
    return _frame(
        f"""
select (
         select count(*)
           from {table}
          where process_ref_no = '{ref}'
            and upper(trim(interface_code)) = '{input_value}'
       ) || '|' ||
       (
         select count(*)
           from (
                 select distinct upper(trim(outgoing_interface)) output_code
                   from GITM_INTERFACE_DEFINITION
                  where upper(trim(interface_code)) = '{input_value}'
                    and trim(outgoing_interface) is not null
                )
       ) || '|' ||
       (
         select count(*)
           from (
                 select distinct upper(trim(outgoing_interface)) output_code
                   from GITM_INTERFACE_DEFINITION
                  where upper(trim(interface_code)) = '{input_value}'
                    and trim(outgoing_interface) is not null
                )
          where output_code = '{output_value}'
       )
  from dual
""",
        "PROCESS_CONTRACT",
    )


def build_file_date_query(
    process_ref_no: str,
    input_code: str,
    source: DataSourceChoice,
) -> FramedQuery:
    """Resolve the package business date from the original PROD file log.

    QA output packages format ``GLOBAL.APPLICATION_DATE``.  For reconstruction,
    ``UPLOAD_DATE`` in the matching active/archive file log is its persisted
    per-process equivalent; ``START_DATE_STAMP`` is used only for legacy rows
    without it. ``GITA_UPLOAD_MASTER.ARCHIVAL_DATE`` must not be used here.
    """
    ref = validate_process_ref(process_ref_no)
    code = normalize_interface_code(input_code)
    log_table = _file_log_table_for(source)
    return _frame(
        f"""
select nvl(
         to_char(
           coalesce(max(upload_date), max(cast(start_date_stamp as date))),
           'YYYYMMDD'
         ),
         ''
       ) || '|' ||
       count(distinct trunc(upload_date)) || '|' ||
       count(distinct trunc(cast(start_date_stamp as date)))
  from {log_table}
 where process_ref_no = '{ref}'
   and upper(trim(interface_code)) = '{code}'
""",
        "FILE_DATE",
    )


def build_database_time_query(format_model: str = "HH24MISS") -> FramedQuery:
    """Read one database clock/timestamp using an allowlisted package format."""
    normalized = str(format_model or "").strip().upper()
    if normalized not in {"HH24MISS", "HHMISS", "YYYYMMDDHH24MISS"}:
        raise ValueError("Unsupported database time format")
    return _frame(
        f"select to_char(sysdate, '{normalized}') from dual",
        "DB_TIME",
    )


def build_interface_last_run_date_query(input_code: str) -> FramedQuery:
    code = normalize_interface_code(input_code)
    if code not in {"CHISALCA", "GIUDFUPD", "IFEARLCG"}:
        raise ValueError(f"Interface {code} does not use LAST_RUN_DATE in its header")
    return _frame(
        f"""
select nvl(to_char(max(last_run_date), 'YYYYMMDD'), '') || '|' ||
       count(distinct trunc(last_run_date))
  from GITM_INTERFACE_DEFINITION
 where upper(trim(interface_code)) = '{code}'
""",
        "INTERFACE_LAST_RUN_DATE",
    )


def build_udf_details_owner_query() -> FramedQuery:
    """Discover the visible owner without hardcoding a PROD username/schema."""
    return _frame(
        """
select rawtohex(utl_i18n.string_to_raw(owner, 'AL32UTF8'))
  from ALL_TAB_COLUMNS
 where table_name = 'GITM_UDF_UPLOAD_DETAILS'
   and column_name in (
       'PROCESS_REF_NO', 'SEQ_NUM', 'FUNCTION_ID', 'REC_KEY', 'UDF_NAME',
       'UDF_VALUE', 'STATUS', 'ERR_CODE', 'ERR_DESC'
   )
 group by owner
having count(distinct column_name) = 9
 order by owner
""",
        "UDF_DETAILS_OWNER",
    )


def build_input_physical_filename_query(
    process_ref_no: str,
    input_code: str,
    source: DataSourceChoice,
) -> FramedQuery:
    """Resolve the original physical filename required by an output header.

    Active packages read ``g_phy_file_name`` from ``GITB_FILE_MASTER``.  Most
    archived adapters use ``GITA_FILE_LOG.PHY_FILE_NAME`` as its persisted
    equivalent.  CHISALCA is different: historical file-log rows can leave
    that column NULL, while every archived upload row retains the physical
    filename supplied to the original trigger.  For that adapter the upload
    population is authoritative and any retained file-log/file-master values
    are corroborating evidence only.
    """
    ref = validate_process_ref(process_ref_no)
    code = normalize_interface_code(input_code)
    try:
        selected_source = DataSourceChoice(source)
    except ValueError as exc:
        raise ValueError("A concrete active/archive source is required") from exc
    supported = {
        "CHISALCA",
        "IFCHKPRT",
        "IFEARLCG",
        "IFIWADOC",
        "IFIWDCLG",
        "IFICOWCG",
        "GIUDFUPD",
        "CMRCIFUP",
        "INCHBKPR",
        "STDCIFUP",
        "STDCRDUP",
        "STDINRTS",
    }
    if code not in supported:
        raise ValueError(f"Interface {code} does not use an input physical filename")
    if (
        code in {"IFCHKPRT", "IFIWDCLG", "IFICOWCG", "GIUDFUPD"}
        and selected_source is DataSourceChoice.ARCHIVE
    ):
        reason = (
            "archived file master/body source"
            if code in {"IFCHKPRT", "IFIWDCLG", "IFICOWCG"}
            else "archived detail-table/body source"
        )
        raise ValueError(f"{code} has no verified {reason}")
    if code == "CHISALCA" and selected_source is DataSourceChoice.ARCHIVE:
        return _frame(
            f"""
with log_keys as (
       select /*+ materialize */ distinct
              trim(l.external_system) external_system,
              l.archival_date,
              replace(trim(l.file_name), '.', '_') upload_file_name
         from GITA_FILE_LOG l
        where l.process_ref_no = '{ref}'
          and l.interface_code = 'CHISALCA'
     ),
     upload_source as (
       select /*+ leading(k) use_nl(u) index(u INX01_GITA_UPLOAD_MASTER) */
              count(*) upload_row_count,
               count(trim(u.phy_file_name)) named_upload_row_count,
               count(distinct trim(u.phy_file_name)) distinct_name_count,
               max(trim(u.phy_file_name)) phy_file_name
         from log_keys k
         join GITA_UPLOAD_MASTER u
           on u.branch_code is null
          and u.external_system = k.external_system
          and u.interface_code = 'CHISALCA'
          and u.archival_date = k.archival_date
          and u.file_name = k.upload_file_name
        where u.process_ref_no = '{ref}'
     ),
     file_log_source as (
       select count(distinct trim(phy_file_name)) distinct_name_count,
              max(trim(phy_file_name)) phy_file_name
         from GITA_FILE_LOG
        where process_ref_no = '{ref}'
          and upper(trim(interface_code)) = 'CHISALCA'
          and trim(phy_file_name) is not null
     ),
     file_master_source as (
       select count(distinct trim(phy_file_name)) distinct_name_count,
              max(trim(phy_file_name)) phy_file_name
         from GITB_FILE_MASTER
        where process_ref_no = '{ref}'
          and upper(trim(interface_code)) = 'CHISALCA'
          and trim(phy_file_name) is not null
     )
select case
         when u.upload_row_count > 0
          and u.named_upload_row_count = u.upload_row_count
          and u.distinct_name_count = 1
          and f.distinct_name_count <= 1
          and (f.distinct_name_count = 0 or f.phy_file_name = u.phy_file_name)
          and m.distinct_name_count <= 1
          and (m.distinct_name_count = 0 or m.phy_file_name = u.phy_file_name)
         then 1
         when u.upload_row_count = 0 or u.named_upload_row_count = 0
         then 0
         else 2
       end || '|' ||
       nvl(rawtohex(utl_i18n.string_to_raw(u.phy_file_name, 'AL32UTF8')), '')
  from upload_source u
 cross join file_log_source f
 cross join file_master_source m
""",
            "INPUT_PHYSICAL_FILENAME",
        )
    source_table = (
        "GITB_FILE_MASTER"
        if code in {"IFIWADOC", "STDINRTS"}
        or selected_source is DataSourceChoice.ACTIVE
        else "GITA_FILE_LOG"
    )
    return _frame(
        f"""
select count(*) || '|' ||
       nvl(max(rawtohex(utl_i18n.string_to_raw(phy_file_name, 'AL32UTF8'))), '')
  from (
        select distinct trim(phy_file_name) phy_file_name
          from {source_table}
         where process_ref_no = '{ref}'
           and upper(trim(interface_code)) = '{code}'
           and trim(phy_file_name) is not null
       )
""",
        "INPUT_PHYSICAL_FILENAME",
    )


def _build_oficowcg_colombia_coverage_query(
    *,
    process_ref_no: str,
    file_date: str,
) -> FramedQuery:
    cursor_sql = _oficowcg_colombia_cursor_sql(
        process_ref_no=process_ref_no,
        file_date=file_date,
    )
    return _frame(
        f"""
with uploads as (
       select record_reference,
              fld27,
              fld28,
              fld2,
              fld6,
              status
         from GITU_UPLOAD_MASTER
        where process_ref_no = '{process_ref_no}'
          and interface_code = 'IFICOWCG'
     ),
     upload_branch_counts as (
       select u.record_reference,
              (select count(*)
                 from GITM_CLEARING_LOG gic
                where trim(u.fld27) = gic.xref
                  and ltrim(u.fld28, 0) = gic.entry_no
                  and trim(u.fld2) = gic.instrno2
                  and gic.process_ref_no = '{process_ref_no}'
                  and gic.record_reference = u.record_reference
                  and u.status <> 'P'
                  and not exists (
                        select 1
                          from IFTB_CLEARING_UPLOAD ifc
                         where ifc.xref = gic.xref
                           and ifc.entry_no = gic.entry_no
                           and ifc.instrno2 = gic.instrno2
                      )) branch_a_count,
              (select count(*)
                 from GITM_CLEARING_LOG gic,
                      IFTB_CLEARING_UPLOAD ifc,
                      IFTB_CLEARING_UPLOAD_C ifcc
                where trim(u.fld27) = gic.xref
                  and ltrim(u.fld28, 0) = gic.entry_no
                  and gic.record_reference = u.record_reference
                  and gic.process_ref_no = '{process_ref_no}'
                  and gic.interface_code = 'IFICOWCG'
                  and ifc.xref = gic.xref
                  and ifc.xref = ifcc.xref
                  and ifc.entry_no = gic.entry_no
                  and (ifc.instrno2 = gic.instrno2 or ifcc.record_type = 'T')) branch_b_count,
              (select count(*)
                 from GITM_CLEARING_LOG gic
                where trim(u.fld27) = gic.xref
                  and ltrim(u.fld28, 0) = gic.entry_no
                  and trim(u.fld6) = '02'
                  and gic.process_ref_no = '{process_ref_no}'
                  and gic.record_reference = u.record_reference
                  and u.status <> 'P'
                  and not exists (
                        select 1
                          from IFTB_CLEARING_UPLOAD ifc
                         where ifc.xref = gic.xref
                           and ifc.entry_no = gic.entry_no
                           and ifc.instrno2 = gic.entry_no
                      )) branch_c_count,
              (select count(*)
                 from GITM_CLEARING_LOG gic,
                      IFTB_CLEARING_UPLOAD ifc,
                      IFTB_CLEARING_UPLOAD_C ifcc
                where trim(u.fld27) = gic.xref
                  and ltrim(u.fld28, 0) = gic.entry_no
                  and gic.record_reference = u.record_reference
                  and gic.process_ref_no = '{process_ref_no}'
                  and gic.interface_code = 'IFICOWCG'
                  and ifc.xref = gic.xref
                  and ifc.xref = ifcc.xref
                  and ifc.entry_no = gic.entry_no
                  and ifcc.document_type = '02') branch_d_count
         from uploads u
     ),
     processed_gic_ownership as (
       select (select count(*)
                 from uploads u
                where u.record_reference = gic.record_reference
                  and trim(u.fld27) = gic.xref
                  and ltrim(u.fld28, 0) = gic.entry_no) upload_owner_count
         from GITM_CLEARING_LOG gic
        where gic.process_ref_no = '{process_ref_no}'
          and gic.interface_code = 'IFICOWCG'
          and (
              exists (
                select 1
                  from IFTB_CLEARING_UPLOAD ifc,
                       IFTB_CLEARING_UPLOAD_C ifcc
                 where ifc.xref = gic.xref
                   and ifc.xref = ifcc.xref
                   and ifc.entry_no = gic.entry_no
                   and (ifc.instrno2 = gic.instrno2 or ifcc.record_type = 'T')
              )
              or exists (
                select 1
                  from IFTB_CLEARING_UPLOAD ifc,
                       IFTB_CLEARING_UPLOAD_C ifcc
                 where ifc.xref = gic.xref
                   and ifc.xref = ifcc.xref
                   and ifc.entry_no = gic.entry_no
                   and ifcc.document_type = '02'
              )
          )
     ),
     cursor_rows as (
       {cursor_sql}
     ),
     rejection_counts as (
       select q.xref,
              q.fccref,
              q.txn_status,
              (select count(*)
                 from CSTB_CLEARING_REJECTION rej
                where rej.trn_ref_no = q.fccref) rejection_count,
              (select count(*)
                 from IFTB_CLEARING_UPLOAD ifc_rej,
                      CSTB_CLEARING_REJECTION rej
                where ifc_rej.fccref = rej.trn_ref_no
                  and ifc_rej.xref = q.xref) error_lookup_count
         from cursor_rows q
        where q.txn_status = 'SUCC'
     )
select (select count(*) from uploads) || '|' ||
       (select count(record_reference) from uploads) || '|' ||
       (select count(distinct record_reference) from uploads) || '|' ||
       (select count(*)
          from upload_branch_counts
         where branch_a_count + branch_b_count + branch_c_count + branch_d_count > 0) || '|' ||
       (select count(*)
          from processed_gic_ownership
         where upload_owner_count <> 1) || '|' ||
       (select count(*) from cursor_rows) || '|' ||
       (select count(*)
          from rejection_counts
         where rejection_count > 1) || '|' ||
       (select count(*)
          from rejection_counts
         where rejection_count = 1
           and error_lookup_count > 1)
  from dual
""",
        "OFICOWCG_CO_COVERAGE",
    )


def build_oficowcg_coverage_query(
    process_ref_no: str,
    *,
    country: str = "chile",
    file_date: str = "",
) -> FramedQuery:
    """Prove the ACTIVE OFICOWCG cursor has one owned row per upload.

    The package cursor's second branch starts from ``GITM_CLEARING_LOG`` and
    ``IFTB_CLEARING_UPLOAD`` rather than from ``GITU_UPLOAD_MASTER``.  A total
    row-count comparison therefore cannot detect a missing upload row that is
    offset by an orphan or duplicate clearing match.  This query models both QA
    branches for each physical upload row and separately verifies ownership of
    every row emitted by the second branch.  It intentionally does not replace
    the contractual UNION used by ``build_body_query``.
    """
    ref = validate_process_ref(process_ref_no)
    country_key = str(country or "").strip().lower()
    if country_key == "colombia":
        return _build_oficowcg_colombia_coverage_query(
            process_ref_no=ref,
            file_date=validate_file_date(file_date),
        )
    if country_key != "chile":
        raise ValueError("OFICOWCG coverage is not verified for this country")
    return _frame(
        f"""
with uploads as (
       select record_reference,
              fld27,
              fld28,
              fld2,
              status
         from GITU_UPLOAD_MASTER
        where process_ref_no = '{ref}'
          and upper(trim(interface_code)) = 'IFICOWCG'
     ),
     upload_match_counts as (
       select u.record_reference,
              (select count(*)
                 from GITM_CLEARING_LOG gic
                where gic.process_ref_no = '{ref}'
                  and gic.record_reference = u.record_reference
                  and trim(u.fld27) = gic.xref
                  and ltrim(u.fld28, 0) = gic.entry_no
                  and trim(u.fld2) = gic.instrno2) gic_match_count,
              (select count(*)
                 from GITM_CLEARING_LOG gic
                where gic.process_ref_no = '{ref}'
                  and gic.record_reference = u.record_reference
                  and trim(u.fld27) = gic.xref
                  and ltrim(u.fld28, 0) = gic.entry_no
                  and trim(u.fld2) = gic.instrno2
                  and u.status <> 'P'
                  and not exists (
                        select 1
                          from IFTB_CLEARING_UPLOAD ifc
                         where ifc.xref = gic.xref
                           and ifc.entry_no = gic.entry_no
                           and ifc.instrno2 = gic.instrno2
                      )) branch_a_count,
              (select count(*)
                 from GITM_CLEARING_LOG gic,
                      IFTB_CLEARING_UPLOAD ifc
                where gic.process_ref_no = '{ref}'
                  and upper(trim(gic.interface_code)) = 'IFICOWCG'
                  and gic.record_reference = u.record_reference
                  and trim(u.fld27) = gic.xref
                  and ltrim(u.fld28, 0) = gic.entry_no
                  and trim(u.fld2) = gic.instrno2
                  and ifc.xref = gic.xref
                  and ifc.entry_no = gic.entry_no
                  and ifc.instrno2 = gic.instrno2) branch_b_count
         from uploads u
     ),
     branch_b_ownership as (
       select (select count(*)
                 from uploads u
                where u.record_reference = gic.record_reference
                  and trim(u.fld27) = gic.xref
                  and ltrim(u.fld28, 0) = gic.entry_no
                  and trim(u.fld2) = gic.instrno2) upload_owner_count
         from GITM_CLEARING_LOG gic,
              IFTB_CLEARING_UPLOAD ifc
        where gic.process_ref_no = '{ref}'
          and upper(trim(gic.interface_code)) = 'IFICOWCG'
          and ifc.xref = gic.xref
          and ifc.entry_no = gic.entry_no
          and ifc.instrno2 = gic.instrno2
     )
select (select count(*) from uploads) || '|' ||
       (select count(record_reference) from uploads) || '|' ||
       (select count(distinct record_reference) from uploads) || '|' ||
       (select count(*)
          from upload_match_counts
         where gic_match_count = 1) || '|' ||
       (select count(*)
          from upload_match_counts
         where branch_a_count + branch_b_count = 1) || '|' ||
       (select count(*)
          from upload_match_counts
         where branch_a_count + branch_b_count = 0) || '|' ||
       (select count(*)
          from upload_match_counts
         where branch_a_count + branch_b_count > 1) || '|' ||
       (select count(*)
          from branch_b_ownership
         where upload_owner_count <> 1)
  from dual
""",
        "OFICOWCG_COVERAGE",
    )


def build_qsimtp_snapshot_guard_query(
    process_ref_no: str,
    source: DataSourceChoice,
) -> FramedQuery:
    """Authenticate the process-less live QSIMTP log before reconstruction.

    The QA input helper truncates and repopulates ``CLTB_IFQSIMTP_LOG``.  Its
    rows carry no process identifier, so an archived process is safe only when
    it is the latest upload/file-log process and the complete live snapshot is
    still attributable one-to-one to it.  ``CLTB_ASCII_UPLOAD_CONTROL`` is the
    helper's own T/W concurrency signal.
    """
    ref = validate_process_ref(process_ref_no)
    table = _table_for(source)
    return _frame(
        f"""
with requested as (
       select /*+ materialize */
              p.record_reference,
              trim(p.fld1) alt_acc_no,
              p.status
         from {table} p
        where p.process_ref_no = '{ref}'
          and p.interface_code = 'IFQSIMTP'
          and p.target_table = 'CLTB_ACCOUNT_MASTER'
     ),
     live_log as (
       select /*+ materialize */
              trim(l.alt_acc_no) alt_acc_no,
              l.branch_code,
              l.sim_date
         from CLTB_IFQSIMTP_LOG l
     ),
     log_counts as (
       select alt_acc_no, count(*) log_count
         from live_log
        group by alt_acc_no
     ),
     upload_stats as (
       select count(*) upload_rows,
              count(distinct u.record_reference) distinct_record_references,
              count(distinct u.alt_acc_no) distinct_alt_accounts,
              nvl(sum(case when u.status = 'P' then 1 else 0 end), 0) p_rows,
              nvl(sum(case when u.status = 'E' then 1 else 0 end), 0) e_rows,
              nvl(sum(case when u.status not in ('P', 'E') or u.status is null
                           then 1 else 0 end), 0) other_status_rows,
              nvl(sum(case when u.status = 'P' and nvl(c.log_count, 0) = 10
                           then 1 else 0 end), 0) p_with_ten_rows,
              nvl(sum(case when u.status = 'P' and nvl(c.log_count, 0) <> 10
                           then 1 else 0 end), 0) p_bad_log_rows,
              nvl(sum(case when u.status = 'E' and nvl(c.log_count, 0) <> 0
                           then 1 else 0 end), 0) e_with_log_rows,
              nvl(sum(nvl(c.log_count, 0)), 0) matched_live_rows
         from requested u
         left join log_counts c
           on c.alt_acc_no = u.alt_acc_no
     ),
     live_stats as (
       select count(*) live_rows,
              count(distinct alt_acc_no) live_accounts,
              count(distinct sim_date) live_sim_dates,
              nvl(sum(case when alt_acc_no is null
                            or branch_code is null
                            or sim_date is null then 1 else 0 end), 0)
                null_key_rows
         from live_log
     ),
     unique_live_keys as (
       select count(*) unique_key_rows
         from (
               select alt_acc_no, branch_code, sim_date
                 from live_log
                group by alt_acc_no, branch_code, sim_date
              )
     ),
     cursor_ties as (
       select count(*) tie_groups
         from (
               select u.record_reference, l.sim_date
                 from requested u
                 join live_log l
                   on l.alt_acc_no = u.alt_acc_no
                group by u.record_reference, l.sim_date
               having count(*) > 1
              )
     )
select to_char(us.upload_rows) || '|' ||
       to_char(us.distinct_record_references) || '|' ||
       to_char(us.distinct_alt_accounts) || '|' ||
       to_char(us.p_rows) || '|' ||
       to_char(us.e_rows) || '|' ||
       to_char(us.other_status_rows) || '|' ||
       to_char(us.p_with_ten_rows) || '|' ||
       to_char(us.p_bad_log_rows) || '|' ||
       to_char(us.e_with_log_rows) || '|' ||
       to_char(us.matched_live_rows) || '|' ||
       to_char(ls.live_rows) || '|' ||
       to_char(uk.unique_key_rows) || '|' ||
       to_char(ls.live_accounts) || '|' ||
       to_char(ls.live_sim_dates) || '|' ||
       to_char(ls.null_key_rows) || '|' ||
       to_char(ct.tie_groups) || '|' ||
       to_char((select count(*)
                  from GITU_UPLOAD_MASTER a
                 where a.interface_code = 'IFQSIMTP'
                   and a.target_table = 'CLTB_ACCOUNT_MASTER')) || '|' ||
       to_char((select count(*)
                  from GITB_FILE_LOG f
                 where upper(trim(f.interface_code)) = 'IFQSIMTP')) || '|' ||
       to_char((select count(*)
                  from GITB_FILE_LOG f
                 where f.process_ref_no = '{ref}'
                   and upper(trim(f.interface_code)) = 'IFQSIMTP')) || '|' ||
       nvl((select to_char(max(to_number(trim(a.process_ref_no))))
              from GITA_UPLOAD_MASTER a
             where a.interface_code = 'IFQSIMTP'
               and a.target_table = 'CLTB_ACCOUNT_MASTER'
               and regexp_like(trim(a.process_ref_no), '^[0-9]+$')), '') || '|' ||
       nvl((select to_char(max(to_number(trim(f.process_ref_no))))
              from GITA_FILE_LOG f
             where upper(trim(f.interface_code)) = 'IFQSIMTP'
               and regexp_like(trim(f.process_ref_no), '^[0-9]+$')), '') || '|' ||
       to_char((select count(*)
                  from CLTB_ASCII_UPLOAD_CONTROL c
                 where upper(trim(c.interface_code)) = 'IFQSIMTP'
                   and c.status in ('T', 'W')))
  from upload_stats us
 cross join live_stats ls
 cross join unique_live_keys uk
 cross join cursor_ties ct
""",
        "QSIMTP_SNAPSHOT",
    )


def build_stdinrou_dependency_guard_query(
    process_ref_no: str,
    source: DataSourceChoice,
) -> FramedQuery:
    """Authenticate STDINROU's custom body and wider standard footer sources."""
    ref = validate_process_ref(process_ref_no)
    table = _table_for(source)
    return _frame(
        f"""
with custom_rows as (
       select /*+ materialize */
              c.record_reference,
              c.status,
              trim(c.phy_file_name) phy_file_name
         from STDINRTS_UPLOAD_MASTER c
        where c.process_ref_no = '{ref}'
     ),
     standard_rows as (
       select /*+ materialize */
              s.record_reference,
              s.status,
              s.interface_code
         from {table} s
        where s.process_ref_no = '{ref}'
     )
select to_char((select count(*) from custom_rows)) || '|' ||
       to_char((select count(distinct record_reference) from custom_rows)) || '|' ||
       to_char((select count(*) from standard_rows)) || '|' ||
       to_char((select count(distinct record_reference) from standard_rows)) || '|' ||
       to_char((select count(*)
                  from standard_rows
                 where upper(trim(interface_code)) = 'STDINRTS')) || '|' ||
       to_char((select count(*)
                  from (select distinct record_reference from custom_rows) c
                 where not exists (
                       select 1
                         from standard_rows s
                        where s.record_reference = c.record_reference
                 ))) || '|' ||
       to_char((select count(*)
                  from (select distinct record_reference from standard_rows) s
                 where not exists (
                       select 1
                         from custom_rows c
                        where c.record_reference = s.record_reference
                 ))) || '|' ||
       to_char((select count(*) from custom_rows where status is null)) || '|' ||
       to_char((select count(*) from standard_rows where status is null)) || '|' ||
       to_char((select count(*) from custom_rows where status = 'P')) || '|' ||
       to_char((select count(*)
                  from custom_rows
                 where status is not null and status <> 'P')) || '|' ||
       to_char((select count(*) from standard_rows where status = 'P')) || '|' ||
       to_char((select count(*)
                  from standard_rows
                 where status is not null and status <> 'P')) || '|' ||
       to_char((select count(distinct phy_file_name)
                  from custom_rows
                 where phy_file_name is not null)) || '|' ||
       nvl((select max(rawtohex(utl_i18n.string_to_raw(
                    phy_file_name, 'AL32UTF8')))
              from custom_rows
             where phy_file_name is not null), '')
  from dual
""",
        "STDINROU_DEPENDENCIES",
    )


def _build_oficowcg_chile_sql(*, process_ref_no: str, file_date: str) -> str:
    """Reproduce the two-branch Chile QA OFICOWCG cursor."""
    return f"""
select {_hex_utf8("nvl(status, '<NULL>')")} || '|' ||
       {_hex_utf8('err_code')} || '|' ||
       {_hex_utf8('error_msg')} || '|' ||
       {_hex_utf8('xref')} || '|' ||
       {_hex_utf8('entry_no')} || '|' ||
       {_hex_utf8('txn_brn')} || '|' ||
       {_hex_utf8("to_char(txn_dt, 'YYYYMMDD')")} || '|' ||
       {_hex_utf8('rembank')} || '|' ||
       {_hex_utf8('remaccount')} || '|' ||
       {_hex_utf8('instrno')} || '|' ||
       {_hex_utf8('instrno2')} || '|' ||
       {_hex_utf8('sector_code')} || '|' ||
       {_hex_utf8("to_char(instramt, 'TM9', 'NLS_NUMERIC_CHARACTERS=''.,''')")} || '|' ||
       {_hex_utf8('clearing_type')}
  from (
        select gic.xref xref,
               to_char(gic.entry_no) entry_no,
               cast(null as varchar2(4000)) fccref,
               gic.txn_brn txn_brn,
               to_date('{file_date}', 'YYYYMMDD') txn_dt,
               gic.rembank rembank,
               gic.remaccount remaccount,
               gic.instrno instrno,
               gic.instrno2 instrno2,
               gic.sector_code sector_code,
               gic.instramt instramt,
               gic.clearing_type clearing_type,
               giu.status status,
               decode(giu.status, 'U', 'GI-INT214', giu.error) err_code,
               giu.error_param error_msg
          from GITU_UPLOAD_MASTER giu,
               GITM_CLEARING_LOG gic
         where trim(giu.fld27) = gic.xref
           and ltrim(giu.fld28, 0) = gic.entry_no
           and trim(giu.fld2) = gic.instrno2
           and giu.process_ref_no = gic.process_ref_no
           and giu.record_reference = gic.record_reference
           and upper(trim(giu.interface_code)) = 'IFICOWCG'
           and gic.process_ref_no = '{process_ref_no}'
           and giu.status <> 'P'
           and not exists (
                 select 1
                   from IFTB_CLEARING_UPLOAD ifc
                  where ifc.xref = gic.xref
                    and ifc.entry_no = gic.entry_no
                    and ifc.instrno2 = gic.instrno2
               )
        union
        select ifc.xref xref,
               to_char(ifc.entry_no) entry_no,
               ifc.fccref fccref,
               ifc.txn_brn txn_brn,
               ifc.txndate txn_dt,
               ifc.rembank rembank,
               ifc.remaccount remaccount,
               ifc.instrno instrno,
               ifc.instrno2 instrno2,
               ifc.sector_code sector_code,
               ifc.instramt instramt,
               gic.clearing_type clearing_type,
               decode(ifc.status, 'SUCC', 'P', 'ERR', 'E') status,
               ifc.error_codes err_code,
               ifc.error_params error_msg
          from IFTB_CLEARING_UPLOAD ifc,
               GITM_CLEARING_LOG gic
         where ifc.xref = gic.xref
           and gic.process_ref_no = '{process_ref_no}'
           and upper(trim(gic.interface_code)) = 'IFICOWCG'
           and ifc.entry_no = gic.entry_no
           and ifc.instrno2 = gic.instrno2
       )
 order by xref, entry_no, fccref, txn_brn, txn_dt, rembank, remaccount,
          instrno, instrno2, sector_code, instramt, clearing_type, status,
          err_code, error_msg
"""


def _oficowcg_colombia_cursor_sql(*, process_ref_no: str, file_date: str) -> str:
    """Return Colombia QA's four UNION branches before rejection rendering.

    Colombia QA spells the upload source ``GITM_UPLOAD_MASTER``; its private
    synonym resolves exactly to ``GITU_UPLOAD_MASTER``. PROD exposes that target
    table to the read-only login, so the controlled SQL names the target
    directly. ``FCCREF`` is deliberately retained in this set even though the
    client file does not print it: QA includes it in the UNION identity and later
    uses it to detect autoprotest rejections. The apparently incorrect DD
    comparison of ``IFC.INSTRNO2`` with ``GIC.ENTRY_NO`` is also contractual
    package behavior.
    """
    return f"""
        select gic.xref xref,
               to_char(gic.entry_no) entry_no,
               cast(null as varchar2(4000)) fccref,
               gic.txn_brn txn_brn,
               to_date('{file_date}', 'YYYYMMDD') txn_dt,
               gic.rembank rembank,
               gic.remaccount remaccount,
               gic.instrno instrno,
               gic.instrno2 instrno2,
               gic.sector_code sector_code,
               gic.instramt instramt,
               gic.clearing_type clearing_type,
               giu.status status,
               decode(giu.status, 'U', 'GI-INT214', giu.error) err_code,
               giu.error_param error_msg,
               'NOPR' txn_status
          from GITU_UPLOAD_MASTER giu,
               GITM_CLEARING_LOG gic
         where trim(giu.fld27) = gic.xref
           and ltrim(giu.fld28, 0) = gic.entry_no
           and trim(giu.fld2) = gic.instrno2
           and giu.process_ref_no = gic.process_ref_no
           and giu.record_reference = gic.record_reference
           and giu.interface_code = 'IFICOWCG'
           and gic.process_ref_no = '{process_ref_no}'
           and giu.status <> 'P'
           and not exists (
                 select 1
                   from IFTB_CLEARING_UPLOAD ifc
                  where ifc.xref = gic.xref
                    and ifc.entry_no = gic.entry_no
                    and ifc.instrno2 = gic.instrno2
               )
        union
        select ifc.xref xref,
               to_char(ifc.entry_no) entry_no,
               ifc.fccref fccref,
               ifc.txn_brn txn_brn,
               ifc.txndate txn_dt,
               ifc.rembank rembank,
               ifc.remaccount remaccount,
               ifc.instrno instrno,
               ifc.instrno2 instrno2,
               ifc.sector_code sector_code,
               ifc.instramt instramt,
               gic.clearing_type clearing_type,
               decode(ifc.status, 'SUCC', 'P', 'ERR', 'E') status,
               ifc.error_codes err_code,
               ifc.error_params error_msg,
               decode(ifc.status, 'SUCC', 'SUCC', 'ERR', 'ERRO') txn_status
          from IFTB_CLEARING_UPLOAD ifc,
               GITM_CLEARING_LOG gic,
               IFTB_CLEARING_UPLOAD_C ifcc
         where ifc.xref = gic.xref
           and ifc.xref = ifcc.xref
           and gic.process_ref_no = '{process_ref_no}'
           and gic.interface_code = 'IFICOWCG'
           and ifc.entry_no = gic.entry_no
           and (ifc.instrno2 = gic.instrno2 or ifcc.record_type = 'T')
        union
        select gic.xref xref,
               to_char(gic.entry_no) entry_no,
               cast(null as varchar2(4000)) fccref,
               gic.txn_brn txn_brn,
               to_date('{file_date}', 'YYYYMMDD') txn_dt,
               gic.rembank rembank,
               gic.remaccount remaccount,
               gic.instrno instrno,
               gic.instrno2 instrno2,
               gic.sector_code sector_code,
               gic.instramt instramt,
               gic.clearing_type clearing_type,
               giu.status status,
               decode(giu.status, 'U', 'GI-INT214', giu.error) err_code,
               giu.error_param error_msg,
               decode(giu.status, 'U', 'NOPR') txn_status
          from GITU_UPLOAD_MASTER giu,
               GITM_CLEARING_LOG gic
         where trim(giu.fld27) = gic.xref
           and ltrim(giu.fld28, 0) = gic.entry_no
           and trim(giu.fld6) = '02'
           and giu.process_ref_no = gic.process_ref_no
           and giu.record_reference = gic.record_reference
           and giu.interface_code = 'IFICOWCG'
           and gic.process_ref_no = '{process_ref_no}'
           and giu.status <> 'P'
           and not exists (
                 select 1
                   from IFTB_CLEARING_UPLOAD ifc
                  where ifc.xref = gic.xref
                    and ifc.entry_no = gic.entry_no
                    and ifc.instrno2 = gic.entry_no
               )
        union
        select ifc.xref xref,
               to_char(ifc.entry_no) entry_no,
               cast(null as varchar2(4000)) fccref,
               ifc.txn_brn txn_brn,
               to_date('{file_date}', 'YYYYMMDD') txn_dt,
               ifc.rembank rembank,
               ifc.remaccount remaccount,
               ifc.instrno instrno,
               ifc.instrno2 instrno2,
               ifc.sector_code sector_code,
               ifc.instramt instramt,
               gic.clearing_type clearing_type,
               decode(ifc.status, 'SUCC', 'P', 'ERR', 'E') status,
               ifc.error_codes err_code,
               ifc.error_params error_msg,
               decode(ifc.status, 'SUCC', 'SUCC', 'ERR', 'ERRO') txn_status
          from IFTB_CLEARING_UPLOAD ifc,
               GITM_CLEARING_LOG gic,
               IFTB_CLEARING_UPLOAD_C ifcc
         where ifc.xref = gic.xref
           and ifc.xref = ifcc.xref
           and ifc.entry_no = gic.entry_no
           and ifcc.document_type = '02'
           and gic.interface_code = 'IFICOWCG'
           and gic.process_ref_no = '{process_ref_no}'
"""


def _build_oficowcg_colombia_sql(*, process_ref_no: str, file_date: str) -> str:
    cursor_sql = _oficowcg_colombia_cursor_sql(
        process_ref_no=process_ref_no,
        file_date=file_date,
    )
    return f"""
with cursor_rows as (
       {cursor_sql}
     ),
     rejection_rows as (
       select q.*,
              case
                when q.txn_status = 'SUCC'
                then nvl((select 1
                            from CSTB_CLEARING_REJECTION rej
                           where rej.trn_ref_no = q.fccref), 0)
                else 0
              end rejection_marker
         from cursor_rows q
     ),
     final_rows as (
       select q.*,
              case
                when q.rejection_marker > 0 then 'REJR'
                else q.txn_status
              end final_txn_status,
              case
                when q.rejection_marker = 0 then q.err_code
                when exists (
                     select 1
                       from IFTB_CLEARING_UPLOAD ifc_rej,
                            CSTB_CLEARING_REJECTION rej
                      where ifc_rej.fccref = rej.trn_ref_no
                        and ifc_rej.xref = q.xref
                ) then (
                     select substr(rej.err_code, 1, instr(rej.err_code, ';', 1, 1) - 1)
                       from IFTB_CLEARING_UPLOAD ifc_rej,
                            CSTB_CLEARING_REJECTION rej
                      where ifc_rej.fccref = rej.trn_ref_no
                        and ifc_rej.xref = q.xref
                )
                else q.err_code
              end final_err_code
         from rejection_rows q
     )
select {_hex_utf8("nvl(status, '<NULL>')")} || '|' ||
       {_hex_utf8('final_err_code')} || '|' ||
       {_hex_utf8('error_msg')} || '|' ||
       {_hex_utf8('xref')} || '|' ||
       {_hex_utf8('entry_no')} || '|' ||
       {_hex_utf8('txn_brn')} || '|' ||
       {_hex_utf8("to_char(txn_dt, 'YYYYMMDD')")} || '|' ||
       {_hex_utf8('rembank')} || '|' ||
       {_hex_utf8('remaccount')} || '|' ||
       {_hex_utf8('instrno')} || '|' ||
       {_hex_utf8('instrno2')} || '|' ||
       {_hex_utf8('sector_code')} || '|' ||
       {_hex_utf8("to_char(instramt, 'TM9', 'NLS_NUMERIC_CHARACTERS=''.,''')")} || '|' ||
       {_hex_utf8('clearing_type')} || '|' ||
       {_hex_utf8('final_txn_status')}
  from final_rows
 order by xref, entry_no, fccref, txn_brn, txn_dt, rembank, remaccount,
          instrno, instrno2, sector_code, instramt, clearing_type, status,
          err_code, error_msg, txn_status
"""


def build_body_query(
    process_ref_no: str,
    input_code: str,
    source: DataSourceChoice,
    file_date: str = "",
    auxiliary_owner: str = "",
    *,
    country: str = "chile",
) -> FramedQuery:
    ref = validate_process_ref(process_ref_no)
    code = normalize_interface_code(input_code)
    country_key = str(country or "").strip().lower()
    if country_key not in {"chile", "peru", "colombia", "mexico"}:
        raise ValueError("Unsupported country for output reconstruction")
    table = _table_for(source)
    if code == "IFCHKPRT":
        if DataSourceChoice(source) is not DataSourceChoice.ACTIVE:
            raise ValueError("IFCHKPRT archive reconstruction is not supported")
        sql = _build_ofchkprt_sql(
            process_ref_no=ref,
            file_date=validate_file_date(file_date),
        )
    elif code == "IFIWDCLG":
        if DataSourceChoice(source) is not DataSourceChoice.ACTIVE:
            raise ValueError("IFIWDCLG archive reconstruction is not supported")
        validate_file_date(file_date)
        sql = _build_ofiwdclg_sql(process_ref_no=ref)
    elif code == "IFIWADOC":
        validate_file_date(file_date)
        sql = _build_ofiwadoc_sql(table=table, process_ref_no=ref)
    elif code == "IFQSIMTP":
        validate_file_date(file_date)
        sql = _build_ofqsimtp_sql(table=table, process_ref_no=ref)
    elif code == "STDINRTS":
        validate_file_date(file_date)
        sql = _build_stdinrou_sql(process_ref_no=ref)
    elif code == "IFEARLCG":
        sql = _build_ifoarlcg_sql(process_ref_no=ref, file_date=file_date)
    elif code == "IFSTDCST":
        sql = _build_dcstout_sql(
            table=table,
            file_log_table=_file_log_table_for(source),
            process_ref_no=ref,
        )
    elif code == "CHISALCA":
        sql = _build_chisalca_sql(
            table=table,
            process_ref_no=ref,
            archive=DataSourceChoice(source) is DataSourceChoice.ARCHIVE,
        )
    elif code == "GIUDFUPD":
        if DataSourceChoice(source) is not DataSourceChoice.ACTIVE:
            raise ValueError("GIUDFUPD archive reconstruction is not supported")
        adapter = upload_body_adapter(code)
        if adapter is None:
            raise ValueError("GIUDFUPD body adapter is missing")
        sql = _build_auxiliary_adapter_sql(
            owner=auxiliary_owner,
            process_ref_no=ref,
            adapter=adapter,
        )
    elif code == "IFDOBIEL":
        sql = f"""
select nvl(upper(trim(status)), '<NULL>') || '|' ||
       nvl(rawtohex(utl_i18n.string_to_raw(error, 'AL32UTF8')), '') || '|' ||
       nvl(rawtohex(utl_i18n.string_to_raw(error_param, 'AL32UTF8')), '') || '|' ||
       trim(fld1) || ';' ||
       trim(fld2) || ';' ||
       trim(fld3) || ';' ||
       trim(fld4) || ';' ||
       status || ';'
  from {table}
 where process_ref_no = '{ref}'
   and upper(trim(interface_code)) = 'IFDOBIEL'
   and trim(fld1) = 'BDY'
 order by record_reference
"""
    elif code == "ACCBLOCK":
        sql = f"""
select nvl(upper(trim(status)), '<NULL>') || '|' ||
       'BDY;' ||
       rtrim(fld4) || ';' ||
       rtrim(fld5) || ';' ||
       rtrim(fld7) || ';' ||
       rtrim(fld10) || ';' ||
       decode(rtrim(status), 'P', 'Y', 'N') || ';' ||
       rtrim(error) || ';' ||
       (select message
          from ERTB_MSGS
         where err_code = rtrim({table}.error)
           and language = 'ESP') || ';'
  from {table}
 where process_ref_no = '{ref}'
   and upper(trim(interface_code)) = 'ACCBLOCK'
 order by record_reference
"""
    elif code == "IFLOCREC":
        # QA GIPKS_OFLOCREC emits these columns in this non-sequential order.
        # RECORD_REFERENCE is added solely to make a reconstruction stable;
        # the package cursor itself does not promise an explicit order.
        sql = f"""
select nvl(upper(trim(status)), '<NULL>') || '|' ||
       trim(fld1) || ';' ||
       trim(fld2) || ';' ||
       trim(fld4) || ';' ||
       trim(fld3) || ';' ||
       decode(status, 'P', 'Y', 'E', 'N') || ';' ||
       error || ';' ||
       error_param || ';'
  from {table}
 where process_ref_no = '{ref}'
   and upper(trim(interface_code)) = 'IFLOCREC'
   and trim(fld1) = 'BDY'
 order by record_reference
"""
    elif code == "CLADCHG":
        # QA GIPKS_CLADCHGO emits TRIM(FLD2), TRIM(FLD99), STATUS and,
        # conditionally, ERROR plus an OVPKS-rendered message.  Transport every
        # source value as UTF-8 hex so Oracle data cannot inject either the
        # parser pipe or the contractual caret delimiter.
        sql = f"""
select nvl(rawtohex(utl_i18n.string_to_raw(
         nvl(upper(trim(status)), '<NULL>'), 'AL32UTF8'
       )), '') || '|' ||
       nvl(rawtohex(utl_i18n.string_to_raw(trim(fld2), 'AL32UTF8')), '') || '|' ||
       nvl(rawtohex(utl_i18n.string_to_raw(trim(fld99), 'AL32UTF8')), '') || '|' ||
       nvl(rawtohex(utl_i18n.string_to_raw(error, 'AL32UTF8')), '') || '|' ||
       nvl(rawtohex(utl_i18n.string_to_raw(error_param, 'AL32UTF8')), '')
  from {table}
 where process_ref_no = '{ref}'
   and upper(trim(interface_code)) = 'CLADCHG'
 order by record_reference
"""
    elif code == "IACMCLOS":
        # QA GIPKS_OACMCLOS reads FLD3/FLD4 and concatenates ERROR directly
        # with ERROR_PARAM before appending resolved descriptions.  Transport
        # each value as UTF-8 hex so neither the framing pipe nor contractual
        # semicolons inside the error lists can corrupt the parser boundary.
        sql = f"""
select nvl(rawtohex(utl_i18n.string_to_raw(
         nvl(upper(trim(status)), '<NULL>'), 'AL32UTF8'
       )), '') || '|' ||
       nvl(rawtohex(utl_i18n.string_to_raw(rtrim(fld3), 'AL32UTF8')), '') || '|' ||
       nvl(rawtohex(utl_i18n.string_to_raw(rtrim(fld4), 'AL32UTF8')), '') || '|' ||
       nvl(rawtohex(utl_i18n.string_to_raw(rtrim(error), 'AL32UTF8')), '') || '|' ||
       nvl(rawtohex(utl_i18n.string_to_raw(rtrim(error_param), 'AL32UTF8')), '')
  from {table}
 where process_ref_no = '{ref}'
   and upper(trim(interface_code)) = 'IACMCLOS'
 order by record_reference
"""
    elif code == "IFCRELVP":
        # QA GIPKS_OFCRELVP excludes U rows from its body while its footer
        # counts every status.  Read every row here so the service can reject
        # U, NULL, or any future status instead of producing an asymmetric
        # body/footer.  RECORD_REFERENCE is a deterministic reconstruction
        # order; the package cursor itself has no explicit ORDER BY.
        #
        # Every Oracle value is transported as UTF-8 hex.  The framing pipe
        # and the client caret delimiter therefore cannot be injected by data.
        sql = f"""
select nvl(rawtohex(utl_i18n.string_to_raw(
         nvl(upper(trim(status)), '<NULL>'), 'AL32UTF8'
       )), '') || '|' ||
       nvl(rawtohex(utl_i18n.string_to_raw(fld2, 'AL32UTF8')), '') || '|' ||
       nvl(rawtohex(utl_i18n.string_to_raw(
         rtrim(ltrim(fld3, chr(32)), chr(32)), 'AL32UTF8'
       )), '') || '|' ||
       nvl(rawtohex(utl_i18n.string_to_raw(
         rtrim(fld5, chr(32)), 'AL32UTF8'
       )), '') || '|' ||
       nvl(rawtohex(utl_i18n.string_to_raw(fld7, 'AL32UTF8')), '') || '|' ||
       nvl(rawtohex(utl_i18n.string_to_raw(fld8, 'AL32UTF8')), '')
  from {table}
 where process_ref_no = '{ref}'
   and upper(trim(interface_code)) = 'IFCRELVP'
 order by record_reference
"""
    elif code == "IFICOWCG":
        if DataSourceChoice(source) is not DataSourceChoice.ACTIVE:
            raise ValueError(
                "IFICOWCG archive reconstruction is not implemented because "
                "its file-master and clearing sources have not been verified"
            )
        date = validate_file_date(file_date)
        if country_key == "colombia":
            sql = _build_oficowcg_colombia_sql(
                process_ref_no=ref,
                file_date=date,
            )
        elif country_key == "chile":
            sql = _build_oficowcg_chile_sql(
                process_ref_no=ref,
                file_date=date,
            )
        else:
            raise ValueError("OFICOWCG body is not verified for this country")
    else:
        adapter = upload_body_adapter(code)
        if adapter is None:
            raise ValueError(f"Interface {code} is not supported yet")
        sql = _build_upload_adapter_sql(
            table=table,
            process_ref_no=ref,
            input_code=code,
            adapter=adapter,
        )
    return _frame(sql, f"{code}_BODY")


def build_footer_status_query(
    process_ref_no: str,
    input_code: str,
    source: DataSourceChoice,
) -> FramedQuery:
    """Read the status population used by a package footer outside its body cursor.

    ``GIPKS_OFLOCREC`` filters ``FLD1='BDY'`` in its body cursor but counts
    statuses across every upload-master row for the process/interface.  Keep
    that asymmetry explicit instead of deriving its footer from body records.
    """
    ref = validate_process_ref(process_ref_no)
    code = normalize_interface_code(input_code)
    supported = {
        "CHISALCA",
        "IFEARLCG",
        "IFSTDCST",
        "IFLOCREC",
        "CMRCIFUP",
        "CMRRELVP",
        "INCHBKPR",
        "STDCIFUP",
        "STDCRDUP",
        "STDINRTS",
    }
    if code not in supported:
        raise ValueError(f"Interface {code} does not use a separate footer status query")
    table = _table_for(source)
    if code == "STDINRTS":
        # QA intentionally counts every standard upload row for the process,
        # while its body comes from STDINRTS_UPLOAD_MASTER. Preserve the raw,
        # case-sensitive P/non-P split and surface NULL independently.
        return _frame(
            f"""
select 'P|' || count(*)
  from {table}
 where process_ref_no = '{ref}'
   and status = 'P'
union all
select '<NON_P>|' || count(*)
  from {table}
 where process_ref_no = '{ref}'
   and status is not null
   and status <> 'P'
union all
select '<NULL>|' || count(*)
  from {table}
 where process_ref_no = '{ref}'
   and status is null
order by 1
""",
            "STDINROU_FOOTER_STATUS",
        )
    if code == "IFEARLCG":
        # QA compares STATUS and INTERFACE_CODE case-sensitively and excludes
        # NULL status from both counters. Surface NULL independently so the
        # service refuses an internally incomplete trailer.
        return _frame(
            f"""
select 'P|' || count(*)
  from {table}
 where process_ref_no = '{ref}'
   and interface_code = 'IFEARLCG'
   and status = 'P'
union all
select '<NON_P>|' || count(*)
  from {table}
 where process_ref_no = '{ref}'
   and interface_code = 'IFEARLCG'
   and status is not null
   and status <> 'P'
union all
select '<NULL>|' || count(*)
  from {table}
 where process_ref_no = '{ref}'
   and interface_code = 'IFEARLCG'
   and status is null
order by 1
""",
            "IFEARLCG_FOOTER_STATUS",
        )
    if code == "IFSTDCST":
        # Preserve the package's case-sensitive P comparison, but surface NULL
        # explicitly: QA would count it in TOTAL while omitting it from both
        # SUCCESS and FAIL, producing an internally inconsistent trailer.
        return _frame(
            f"""
select 'P|' || count(*)
  from {table}
 where process_ref_no = '{ref}'
   and status = 'P'
union all
select '<NON_P>|' || count(*)
  from {table}
 where process_ref_no = '{ref}'
   and status is not null
   and status <> 'P'
union all
select '<NULL>|' || count(*)
  from {table}
 where process_ref_no = '{ref}'
   and status is null
order by 1
""",
            "IFSTDCST_FOOTER_STATUS",
        )
    if code == "CHISALCA":
        # The QA package compares STATUS case-sensitively. Preserve the raw
        # trimmed value so service validation can reject lowercase rows even
        # when they belong to a TARGET_TABLE omitted from the body cursor.
        if DataSourceChoice(source) is DataSourceChoice.ARCHIVE:
            return _frame(
                f"""
with log_keys as (
       select /*+ materialize */ distinct
              trim(l.external_system) external_system,
              l.archival_date,
              replace(trim(l.file_name), '.', '_') upload_file_name
         from GITA_FILE_LOG l
        where l.process_ref_no = '{ref}'
          and l.interface_code = 'CHISALCA'
     ),
     upload_rows as (
       select /*+ leading(k) use_nl(u) index(u INX01_GITA_UPLOAD_MASTER) */
              u.status
         from log_keys k
         join GITA_UPLOAD_MASTER u
           on u.branch_code is null
          and u.external_system = k.external_system
          and u.interface_code = 'CHISALCA'
          and u.archival_date = k.archival_date
          and u.file_name = k.upload_file_name
        where u.process_ref_no = '{ref}'
     )
select nvl(trim(status), '<NULL>') || '|' || count(*)
  from upload_rows
 group by nvl(trim(status), '<NULL>')
 order by 1
""",
                "CHISALCA_FOOTER_STATUS",
            )
        return _frame(
            f"""
select nvl(trim(status), '<NULL>') || '|' || count(*)
  from {table}
 where process_ref_no = '{ref}'
   and upper(trim(interface_code)) = 'CHISALCA'
 group by nvl(trim(status), '<NULL>')
 order by 1
""",
            "CHISALCA_FOOTER_STATUS",
        )
    if code != "IFLOCREC":
        return _frame(
            f"""
select nvl(upper(trim(status)), '<NULL>') || '|' || count(*)
  from {table}
 where process_ref_no = '{ref}'
   and upper(trim(interface_code)) = '{code}'
 group by nvl(upper(trim(status)), '<NULL>')
 order by 1
""",
            f"{code}_FOOTER_STATUS",
        )
    return _frame(
        f"""
select 'P|' || count(*)
  from {table}
 where process_ref_no = '{ref}'
   and upper(trim(interface_code)) = 'IFLOCREC'
   and status = 'P'
union all
select 'E|' || count(*)
  from {table}
 where process_ref_no = '{ref}'
   and upper(trim(interface_code)) = 'IFLOCREC'
   and status = 'E'
order by 1
""",
        "IFLOCREC_FOOTER_STATUS",
    )


def build_error_messages_query(error_codes: Iterable[str]) -> FramedQuery:
    """Read the Spanish message templates needed by an OFDOBIEL result.

    Values originate in Oracle, but only their UTF-8 hex representation is
    interpolated.  That keeps this controlled builder free of arbitrary SQL.
    """
    encoded: list[str] = []
    for raw_code in sorted(set(error_codes)):
        code = str(raw_code or "")
        if not code or len(code) > 255 or "\r" in code or "\n" in code:
            raise ValueError("Oracle returned an invalid error code")
        encoded.append(code.encode("utf-8").hex().upper())
    if not encoded:
        raise ValueError("At least one error code is required")
    if len(encoded) > 1000:
        raise ValueError("Too many distinct Oracle error codes")
    literals = ", ".join(f"'{value}'" for value in encoded)
    return _frame(
        f"""
select code_hex || '|' || count(*) || '|' || nvl(max(message_hex), '')
  from (
        select rawtohex(utl_i18n.string_to_raw(err_code, 'AL32UTF8')) code_hex,
               rawtohex(
                 utl_i18n.string_to_raw(replace(message, '!', ''), 'AL32UTF8')
               ) message_hex
          from ERTB_MSGS
         where language = 'ESP'
       )
 where code_hex in ({literals})
 group by code_hex
 order by code_hex
""",
        "ERROR_MESSAGES",
    )


def build_error_message_details_query(error_codes: Iterable[str]) -> FramedQuery:
    """Read CHISALOU message templates together with their ERTB type.

    ``GIPKS_CHISALOU`` discards TYPE ``O`` entries before it calls
    ``OVPKS.FN_FORMMSG``. Only UTF-8 hex literals are interpolated, preserving
    the same controlled-SELECT boundary as the generic message lookup.
    """
    encoded: list[str] = []
    for raw_code in sorted(set(error_codes)):
        code = str(raw_code or "")
        if not code or len(code) > 255 or "\r" in code or "\n" in code:
            raise ValueError("Oracle returned an invalid error code")
        encoded.append(code.encode("utf-8").hex().upper())
    if not encoded:
        raise ValueError("At least one error code is required")
    if len(encoded) > 1000:
        raise ValueError("Too many distinct Oracle error codes")
    literals = ", ".join(f"'{value}'" for value in encoded)
    return _frame(
        f"""
select code_hex || '|' || count(*) || '|' ||
       nvl(max(type_hex), '') || '|' || nvl(max(message_hex), '')
  from (
        select rawtohex(utl_i18n.string_to_raw(err_code, 'AL32UTF8')) code_hex,
               rawtohex(utl_i18n.string_to_raw(trim(type), 'AL32UTF8')) type_hex,
               rawtohex(
                 utl_i18n.string_to_raw(replace(message, '!', ''), 'AL32UTF8')
               ) message_hex
          from ERTB_MSGS
         where language = 'ESP'
       )
 where code_hex in ({literals})
 group by code_hex
 order by code_hex
""",
        "ERROR_MESSAGE_DETAILS",
    )


def _table_for(source: DataSourceChoice) -> str:
    try:
        return _TABLES[DataSourceChoice(source)]
    except (KeyError, ValueError) as exc:
        raise ValueError("A concrete active/archive source is required") from exc


def _file_log_table_for(source: DataSourceChoice) -> str:
    try:
        return _FILE_LOG_TABLES[DataSourceChoice(source)]
    except (KeyError, ValueError) as exc:
        raise ValueError("A concrete active/archive source is required") from exc
