"""Declarative, QA-verified upload-master body contracts.

Only static SQL fragments live here.  Runtime values (process number, source
table and interface code) are validated and supplied by ``queries.py``.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class UploadBodyAdapter:
    fields: tuple[str, ...]
    style: str
    filters: tuple[str, ...] = ()
    order_by: str = "u.record_reference"


_ADAPTERS: dict[str, UploadBodyAdapter] = {
    "CHISALCA": UploadBodyAdapter(
        fields=(
            "case when trim(u.status) = 'P' then "
            "(select to_char(count(*)) from DETB_RTL_TELLER t "
            "where t.xref = trim(u.fld22)) else '0' end",
            "case when trim(u.status) = 'P' then "
            "(select min(trim(t.trn_ref_no)) from DETB_RTL_TELLER t "
            "where t.xref = trim(u.fld22)) else null end",
            "case when trim(u.status) = 'P' then "
            "(select min(trim(t.xref)) from DETB_RTL_TELLER t "
            "where t.xref = trim(u.fld22)) else trim(u.fld22) end",
            "case when trim(u.status) = 'P' then "
            "(select min(trim(t.txn_acc)) from DETB_RTL_TELLER t "
            "where t.xref = trim(u.fld22)) else trim(u.fld6) end",
            "case when trim(u.status) = 'P' then "
            "(select min(trim(t.branch_code)) from DETB_RTL_TELLER t "
            "where t.xref = trim(u.fld22)) else trim(u.fld4) end",
            "case when trim(u.status) = 'P' then "
            "(select to_char(min(t.txn_amount), 'TM9', "
            "'NLS_NUMERIC_CHARACTERS=''.,''') from DETB_RTL_TELLER t "
            "where t.xref = trim(u.fld22)) else trim(u.fld8) end",
            "case when trim(u.status) = 'P' then "
            "(select min(to_char(t.trn_dt, 'YYYYMMDD')) from DETB_RTL_TELLER t "
            "where t.xref = trim(u.fld22)) else "
            "to_char(to_date(trim(u.fld16), 'YYYYMMDD'), 'YYYYMMDD') end",
            "case when trim(u.status) = 'P' then "
            "(select min(trim(t.txn_ccy)) from DETB_RTL_TELLER t "
            "where t.xref = trim(u.fld22)) else trim(u.fld7) end",
            "trim(u.fld199)",
            "trim(u.fld200)",
        ),
        style="chisalou",
        filters=("u.target_table = 'DETB_UPLOAD_RTL_TELLER'",),
    ),
    "CHICLUPD": UploadBodyAdapter(
        fields=(
            "rtrim(u.fld3)",
            "rtrim(u.fld31)",
            "rtrim(u.fld4)",
            "(select to_char(count(*)) from CLTB_ACCOUNT_MASTER a "
            "where a.account_number = rtrim(u.fld31) "
            "and a.branch_code = rtrim(u.fld4))",
            "(select min(rtrim(a.account_status)) from CLTB_ACCOUNT_MASTER a "
            "where a.account_number = rtrim(u.fld31) "
            "and a.branch_code = rtrim(u.fld4))",
            "(select to_char(count(*)) from ERTB_MSGS m "
            "where m.err_code = rtrim(u.error))",
            "(select min(rtrim(m.message)) from ERTB_MSGS m "
            "where m.err_code = rtrim(u.error))",
            "rtrim(u.error)",
            "u.error_param",
        ),
        style="chiclou",
        filters=("u.status in ('P', 'E')",),
    ),
    "GIUDFUPD": UploadBodyAdapter(
        fields=(
            "u.function_id",
            "trim(u.rec_key)",
            "trim(u.udf_name)",
            "trim(u.udf_value)",
            "u.err_code",
            "u.err_desc",
        ),
        style="giupd",
        order_by="u.seq_num",
    ),
    "IACMASSC": UploadBodyAdapter(
        fields=(
            "rtrim(u.fld3)",
            "rtrim(u.fld10)",
            "rtrim(u.fld4)",
            "rtrim(u.fld30)",
            "rtrim(u.fld7)",
            "rtrim(u.fld8)",
            "rtrim(u.fld31)",
            "rtrim(u.fld6)",
            "rtrim(u.error)",
            "rtrim(u.error_param)",
        ),
        style="oacmassc",
    ),
    "CHBOOKIN": UploadBodyAdapter(
        fields=(
            "rtrim(u.fld2)",
            "rtrim(u.fld3)",
            "rtrim(u.fld5)",
            "rtrim(u.fld4)",
            "rtrim(u.fld6)",
            "rtrim(u.fld7)",
            "rtrim(u.error)",
            "rtrim(u.error_param)",
        ),
        style="chbookou",
    ),
    "CLIIRFAP": UploadBodyAdapter(
        fields=(
            "trim(u.fld2)",
            "trim(u.fld3)",
            "trim(u.fld4)",
            "u.error",
            "u.error_param",
        ),
        style="cloirfap",
        filters=("trim(u.fld1) = 'BDY'",),
    ),
    "CLISLRES": UploadBodyAdapter(
        fields=(
            "trim(u.fld2)",
            "trim(u.fld3)",
            "u.error",
            "u.error_param",
        ),
        style="closlres",
        filters=("trim(u.fld1) = 'BDY'",),
    ),
    "CMRADCHG": UploadBodyAdapter(
        fields=("trim(u.fld2)", "trim(u.fld99)", "u.error", "u.error_param"),
        style="cmradcho",
    ),
    "CMRCLUPD": UploadBodyAdapter(
        fields=(
            "rtrim(u.fld3)",
            "rtrim(u.fld31)",
            "rtrim(u.fld4)",
            "(select to_char(count(*)) from CLTB_ACCOUNT_MASTER a "
            "where a.account_number = rtrim(u.fld31) "
            "and a.branch_code = rtrim(u.fld4))",
            "(select min(rtrim(a.account_status)) from CLTB_ACCOUNT_MASTER a "
            "where a.account_number = rtrim(u.fld31) "
            "and a.branch_code = rtrim(u.fld4))",
            "rtrim(u.error)",
            "u.error_param",
        ),
        style="cmrclou",
        filters=("u.status in ('P', 'E')",),
    ),
    "CMRCIFUP": UploadBodyAdapter(
        fields=(
            "trim(u.fld2)",
            "trim(u.fld3)",
            "trim(u.fld199)",
            "trim(u.fld200)",
        ),
        style="cmrcifou",
        filters=("u.target_table = 'STTMS_UPLOAD_CUSTOMER'",),
    ),
    "CMRLPMNT": UploadBodyAdapter(
        fields=(
            "trim(u.fld2)",
            "trim(u.fld8)",
            "trim(u.fld3)",
            "to_char(to_number(trim(u.fld200)), 'TM9', "
            "'NLS_NUMERIC_CHARACTERS=''.,''')",
            "to_char(to_date(trim(u.fld5), 'YYYYMMDD'), 'YYYYMMDD')",
            "to_char(to_date(trim(u.fld6), 'YYYYMMDD'), 'YYYYMMDD')",
            "trim(u.fld9)",
            "trim(u.error)",
            "u.error_param",
        ),
        style="fixed_payment",
    ),
    "CMRRELVP": UploadBodyAdapter(
        fields=(
            "u.fld2",
            "rtrim(ltrim(u.fld3, chr(32)), chr(32))",
            "rtrim(u.fld5, chr(32))",
            "u.fld7",
            "u.fld8",
        ),
        style="cmrrelvo",
    ),
    "IFCLPMNT": UploadBodyAdapter(
        fields=(
            "trim(u.fld2)",
            "trim(u.fld8)",
            "trim(u.fld3)",
            "to_char(to_number(trim(u.fld200)), 'TM9', "
            "'NLS_NUMERIC_CHARACTERS=''.,''')",
            "to_char(to_date(trim(u.fld5), 'YYYYMMDD'), 'YYYYMMDD')",
            "to_char(to_date(trim(u.fld6), 'YYYYMMDD'), 'YYYYMMDD')",
            "trim(u.fld9)",
            "trim(u.error)",
            "u.error_param",
        ),
        style="fixed_payment",
    ),
    "IFDDISSU": UploadBodyAdapter(
        fields=(
            "trim(u.fld2)",
            "trim(u.fld3)",
            "trim(u.fld4)",
            "trim(u.fld5)",
            "trim(u.fld6)",
            "trim(u.fld7)",
            "trim(u.fld8)",
            "trim(u.fld9)",
            "trim(u.fld10)",
            "trim(u.fld11)",
            "trim(u.fld12)",
            "trim(u.fld13)",
            "trim(u.fld14)",
            "trim(u.fld15)",
            "trim(u.fld16)",
            "trim(u.fld17)",
            "trim(u.fld18)",
            "trim(u.fld19)",
            "trim(u.fld20)",
            "trim(u.fld21)",
            "trim(u.fld22)",
            "trim(u.fld23)",
            "trim(u.fld24)",
            "trim(u.fld25)",
            "trim(u.fld26)",
            "trim(u.fld27)",
            "trim(u.fld28)",
            "trim(u.fld29)",
            "trim(u.fld30)",
            "trim(u.fld31)",
            "trim(u.fld32)",
            "trim(u.fld33)",
            "trim(u.fld34)",
            "trim(u.fld35)",
            "trim(u.fld36)",
            "trim(u.fld37)",
            "trim(u.fld38)",
            "trim(u.fld39)",
            "trim(u.fld40)",
            "trim(u.fld41)",
            "trim(u.fld42)",
            "trim(u.fld43)",
            "trim(u.fld44)",
            "trim(u.fld45)",
            "trim(u.fld46)",
            "trim(u.fld47)",
            "trim(u.fld48)",
            "trim(u.fld49)",
            "trim(u.fld50)",
            "trim(u.fld51)",
            "trim(u.fld52)",
            "trim(u.fld53)",
            "trim(u.fld54)",
            "trim(u.fld55)",
            "trim(u.fld56)",
            "trim(u.error)",
            "trim(u.error_param)",
        ),
        style="ofddissu",
    ),
    "IFGLCRTE": UploadBodyAdapter(
        fields=(
            "rtrim(u.fld4)",
            "rtrim(u.fld6)",
            "u.error",
            "u.error_param",
        ),
        style="glcrte_fixed",
    ),
    "IFGLMDFY": UploadBodyAdapter(
        fields=(
            "rtrim(u.fld4, ' ')",
            "(select case when count(*) = 1 then min(g.alt_gl_no) else null end "
            "from GLTM_GLMASTER_C g "
            "where g.gl_code = rtrim(u.fld4, ' '))",
            "u.error",
            "u.error_param",
        ),
        style="glcrte_fixed",
    ),
    "IFMDCGEN": UploadBodyAdapter(
        fields=(
            "trim(u.fld2)",
            "trim(u.fld5)",
            "trim(u.fld40)",
            "trim(u.fld6)",
            "trim(u.fld190)",
            "trim(u.fld191)",
            "u.error",
            "u.error_param",
        ),
        style="ofmdcgen",
        filters=("u.target_table = 'STTMS_DEBIT_CARD_MASTER'",),
    ),
    "IFMDSUPD": UploadBodyAdapter(
        fields=(
            "trim(u.fld2)",
            "trim(u.fld4)",
            "trim(u.fld5)",
            "u.error",
            "u.error_param",
        ),
        style="ofmdsupd",
        filters=("u.target_table = 'STTMS_DEBIT_CARD_MASTER'",),
    ),
    "IFOBTUPD": UploadBodyAdapter(
        fields=(
            "trim(u.fld3)",
            "trim(u.fld4)",
            "trim(u.fld5)",
            "u.error",
            "u.error_param",
        ),
        style="ofobtupd",
    ),
    "INCHBKPR": UploadBodyAdapter(
        fields=(
            "trim(u.fld1)",
            "trim(u.fld8)",
            "trim(u.fld16)",
            "trim(u.fld9)",
            "trim(u.fld10)",
            "trim(u.error)",
            "trim(u.error_param)",
        ),
        style="ouchbkcu",
        filters=("u.target_table = 'CATMS_CHECK_BOOK'",),
    ),
    "IXCGRATE": UploadBodyAdapter(
        fields=(
            "trim(u.fld2)",
            "trim(u.fld3)",
            "trim(u.fld4)",
            "u.error",
            "u.error_param",
        ),
        style="oxcgrate",
        filters=("trim(u.fld1) = 'BHD'",),
    ),
    "LOCAMTIN": UploadBodyAdapter(
        fields=(
            "trim(u.fld2)",
            "trim(u.fld3)",
            "trim(u.fld4)",
            "rtrim(u.error)",
            "rtrim(u.error_param)",
        ),
        style="locamtou",
        filters=("u.target_table = 'STTB_LOCAMT_MODIFY_UPLOAD'",),
    ),
    "STDCIFMO": UploadBodyAdapter(
        fields=(
            "rtrim(u.fld2)",
            "(select case when count(*) = 1 "
            "then min(rtrim(c.customer_no)) else null end "
            "from STTM_UPLOAD_CUSTOMER c "
            "where c.maintenance_seq_no = u.fld2)",
            "case when u.status = 'E' then "
            "(select to_char(count(*)) from ERTB_MSGS m "
            "where m.err_code = nvl(rtrim(u.error), 'ST-SAVE-004')) "
            "else '0' end",
            "case when u.status = 'E' then "
            "(select min(rtrim(m.message)) from ERTB_MSGS m "
            "where m.err_code = nvl(rtrim(u.error), 'ST-SAVE-004')) "
            "else null end",
            "rtrim(u.error)",
            "u.error_param",
        ),
        style="stdcifom",
        filters=("u.status in ('P', 'E')",),
    ),
    "STDCIFUP": UploadBodyAdapter(
        fields=(
            "trim(u.fld2)",
            "trim(u.fld3)",
            "trim(u.fld199)",
            "trim(u.fld200)",
        ),
        style="stdcifou",
        filters=("u.target_table = 'STTMS_UPLOAD_CUSTOMER'",),
    ),
    "STDCRDUP": UploadBodyAdapter(
        fields=(
            "trim(u.fld2)",
            "trim(u.fld3)",
            "trim(u.fld4)",
            "trim(u.fld6)",
            "trim(u.fld5)",
            "trim(u.fld19)",
            "trim(u.fld198)",
            "trim(u.fld36)",
            "trim(u.fld13)",
            "trim(u.fld199)",
            "trim(u.fld200)",
        ),
        style="stdcrdou",
        filters=("u.target_table = 'STTB_DEBIT_CARD_UPLOAD_CU'",),
    ),
}


def upload_body_adapter(input_code: str) -> UploadBodyAdapter | None:
    return _ADAPTERS.get(input_code)
