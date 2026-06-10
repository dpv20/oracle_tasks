-- IC account data .INC generator for Falabella / FLEXCUBE Chile.
--
-- Output format follows the existing .INC style:
--   DELETE FROM TABLE WHERE ...;
--   INSERT INTO TABLE(COL1,COL2,...) VALUES (...);
--
-- The generated .INC starts with WHENEVER SQLERROR CONTINUE so that missing
-- tables, duplicate rows, or conversion issues are logged by the SQL client and
-- execution proceeds to the next statement.

accept Branch  prompt 'Enter the Branch  > '
accept Account prompt 'Enter the Account > '

set verify off
set feedback off
set heading off
set pagesize 0
set linesize 32767
set trimspool on
set tab off
set serveroutput on size unlimited format wrapped

alter session set nls_language = 'AMERICAN';
alter session set nls_date_language = 'AMERICAN';
alter session set nls_date_format = 'DD-MM-YYYY';
alter session set nls_numeric_characters = '.,';

column from_date   new_value from_date   noprint
column aclass      new_value aclass      noprint
column currency    new_value currency    noprint
column customer_no new_value customer_no noprint

select to_char(today, 'DD-MM-YYYY') from_date
  from sttm_dates
 where branch_code = upper('&Branch');

select max(aclass) aclass
  from (
        select account_class aclass
          from sttm_cust_account
         where branch_code = upper('&Branch')
           and cust_ac_no = '&Account'
        union all
        select aclass
          from ictb_acc_pr
         where brn = upper('&Branch')
           and acc = '&Account'
       );

select max(currency) currency
  from (
        select ccy currency
          from sttm_cust_account
         where branch_code = upper('&Branch')
           and cust_ac_no = '&Account'
        union all
        select ccy
          from ictb_acc_pr
         where brn = upper('&Branch')
           and acc = '&Account'
        union all
        select ac_gl_ccy
          from sttb_account
         where branch_code = upper('&Branch')
           and ac_gl_no = '&Account'
       );

select max(customer_no) customer_no
  from (
        select cust_no customer_no
          from sttm_cust_account
         where branch_code = upper('&Branch')
           and cust_ac_no = '&Account'
        union all
        select cust_no
          from sttb_account
         where branch_code = upper('&Branch')
           and ac_gl_no = '&Account'
       );

spool D:\IC_account_data_&Account..INC

declare
    g_branch      varchar2(20)  := upper('&Branch');
    g_account     varchar2(100) := '&Account';
    g_from_date   varchar2(20)  := '&from_date';
    g_aclass      varchar2(100) := '&aclass';
    g_currency    varchar2(20)  := '&currency';
    g_customer_no varchar2(100) := '&customer_no';
    g_fatal_generation_errors pls_integer := 0;
    g_checkpoint_interval constant pls_integer := 800;
    g_dml_since_commit pls_integer := 0;
    g_total_dml pls_integer := 0;

    type t_text_tab is table of varchar2(32767) index by pls_integer;

    function lit(p_value in varchar2) return varchar2 is
    begin
        if p_value is null then
            return '''''';
        end if;

        return '''' || replace(p_value, '''', '''''') || '''';
    end lit;

    procedure put_line(p_text in varchar2) is
    begin
        dbms_output.put_line(p_text);
    end put_line;

    procedure emit_commit_checkpoint(
        p_reason in varchar2,
        p_force  in boolean default false
    ) is
    begin
        if g_dml_since_commit = 0 then
            return;
        end if;

        if p_force or g_dml_since_commit >= g_checkpoint_interval then
            put_line('COMMIT;');
            put_line('SELECT ''IC_DB_CHECKPOINT_OK|' || g_total_dml || '|' || replace(p_reason, '''', '''''') || ''' FROM DUAL;');
            put_line('PROMPT IC checkpoint commit after ' || g_total_dml || ' DML statements - ' || p_reason);
            put_line(null);
            g_dml_since_commit := 0;
        end if;
    end emit_commit_checkpoint;

    procedure note_dml(p_reason in varchar2) is
    begin
        g_dml_since_commit := g_dml_since_commit + 1;
        g_total_dml := g_total_dml + 1;
        emit_commit_checkpoint(p_reason);
    end note_dml;

    procedure append_text(
        p_clob in out nocopy clob,
        p_text in varchar2
    ) is
    begin
        if p_text is null then
            return;
        end if;

        if p_clob is null then
            dbms_lob.createtemporary(p_clob, true);
        end if;

        dbms_lob.writeappend(p_clob, length(p_text), p_text);
    end append_text;

    procedure append_clob(
        p_target in out nocopy clob,
        p_source in clob
    ) is
    begin
        if p_source is null then
            return;
        end if;

        if p_target is null then
            dbms_lob.createtemporary(p_target, true);
        end if;

        dbms_lob.append(p_target, p_source);
    end append_clob;

    procedure resolve_table(
        p_table in varchar2,
        p_owner out varchar2,
        p_object_type out varchar2
    ) is
    begin
        select owner, object_type
          into p_owner, p_object_type
          from (
                select owner, object_type
                  from all_objects
                 where object_name = upper(p_table)
                   and object_type in ('TABLE', 'VIEW')
                 order by case
                              when owner = sys_context('USERENV', 'CURRENT_SCHEMA') then 1
                              when owner = user then 2
                              else 3
                          end,
                          owner
               )
         where rownum = 1;
    exception
        when no_data_found then
            p_owner := null;
            p_object_type := null;
    end resolve_table;

    function select_expr(p_column_name in varchar2, p_data_type in varchar2) return varchar2 is
        l_col  varchar2(300) := '"' || replace(upper(p_column_name), '"', '""') || '"';
        l_type varchar2(128) := upper(p_data_type);
    begin
        if l_type in ('CHAR', 'VARCHAR2', 'NCHAR', 'NVARCHAR2') then
            return 'substr(' || l_col || ', 1, 3500)';
        elsif l_type in ('CLOB', 'NCLOB') then
            return 'dbms_lob.substr(' || l_col || ', 3500, 1)';
        elsif l_type = 'DATE' then
            return 'to_char(' || l_col || q'[, 'DD-MM-YYYY')]';
        elsif l_type like 'TIMESTAMP%WITH TIME ZONE' then
            return 'to_char(' || l_col || q'[, 'YYYY-MM-DD HH24:MI:SS.FF TZH:TZM')]';
        elsif l_type like 'TIMESTAMP%' then
            return 'to_char(' || l_col || q'[, 'YYYY-MM-DD HH24:MI:SS.FF6')]';
        elsif l_type in ('NUMBER', 'FLOAT', 'BINARY_FLOAT', 'BINARY_DOUBLE') then
            return 'to_char(' || l_col || q'[, 'TM9', 'NLS_NUMERIC_CHARACTERS = ''.,''')]';
        elsif l_type = 'RAW' then
            return 'rawtohex(' || l_col || ')';
        else
            return 'to_char(' || l_col || ')';
        end if;
    end select_expr;

    function sql_literal(p_value in varchar2) return varchar2 is
    begin
        if p_value is null then
            return '''''';
        end if;

        return '''' ||
               replace(
                   replace(
                       replace(substr(p_value, 1, 3500), '''', ''''''),
                       chr(13),
                       '''||chr(13)||'''
                   ),
                   chr(10),
                   '''||chr(10)||'''
               ) ||
               '''';
    end sql_literal;

    function value_token(p_value in varchar2, p_data_type in varchar2) return varchar2 is
        l_type varchar2(128) := upper(p_data_type);
    begin
        if p_value is null then
            return '''''';
        elsif l_type like 'TIMESTAMP%WITH TIME ZONE' then
            return 'TO_TIMESTAMP_TZ(' || sql_literal(p_value) || ',''YYYY-MM-DD HH24:MI:SS.FF TZH:TZM'')';
        elsif l_type like 'TIMESTAMP%' then
            return 'TO_TIMESTAMP(' || sql_literal(p_value) || ',''YYYY-MM-DD HH24:MI:SS.FF6'')';
        elsif l_type = 'RAW' then
            return 'HEXTORAW(' || sql_literal(p_value) || ')';
        else
            return sql_literal(p_value);
        end if;
    end value_token;

    function supported_column(p_data_type in varchar2) return boolean is
        l_type varchar2(128) := upper(p_data_type);
    begin
        return l_type not in ('LONG', 'LONG RAW', 'BLOB', 'BFILE');
    end supported_column;

    procedure parse_sql(p_cursor in integer, p_sql in clob) is
        l_chunks dbms_sql.varchar2a;
        l_pos    pls_integer := 1;
        l_len    pls_integer;
        l_idx    pls_integer := 1;
    begin
        l_len := dbms_lob.getlength(p_sql);

        while l_pos <= l_len loop
            l_chunks(l_idx) := dbms_lob.substr(p_sql, 32767, l_pos);
            l_pos := l_pos + 32767;
            l_idx := l_idx + 1;
        end loop;

        dbms_sql.parse(p_cursor, l_chunks, 1, l_idx - 1, false, dbms_sql.native);
    end parse_sql;

    procedure emit_token_list(
        p_prefix in varchar2,
        p_tokens in t_text_tab,
        p_count  in pls_integer,
        p_suffix in varchar2
    ) is
        l_line varchar2(32767) := p_prefix;
        l_part varchar2(32767);
    begin
        for i in 1 .. p_count loop
            l_part := p_tokens(i) || case when i < p_count then ',' else null end;

            if length(l_line) + length(l_part) + 1 > 1800 then
                put_line(l_line);
                l_line := '       ' || l_part;
            else
                l_line := l_line || l_part;
            end if;
        end loop;

        put_line(l_line || p_suffix);
    end emit_token_list;

    procedure emit_insert(
        p_table in varchar2,
        p_cols  in t_text_tab,
        p_vals  in t_text_tab,
        p_count in pls_integer
    ) is
    begin
        emit_token_list('INSERT INTO ' || upper(p_table) || '(', p_cols, p_count, ')');
        emit_token_list(' VALUES (', p_vals, p_count, ');');
        put_line(null);
        note_dml('insert ' || upper(p_table));
    end emit_insert;

    procedure emit_delete(
        p_table in varchar2,
        p_where in varchar2
    ) is
        l_owner varchar2(128);
        l_type  varchar2(30);
    begin
        if p_where is null then
            return;
        end if;

        resolve_table(p_table, l_owner, l_type);

        if l_owner is null then
            put_line('-- ERROR: DELETE skipped for ' || upper(p_table) || ' - source object is not visible.');
        elsif l_type <> 'TABLE' then
            put_line('-- INFO: DELETE skipped for ' || upper(p_table) || ' - source object is a ' || l_type || '.');
        else
            put_line('DELETE FROM ' || upper(p_table) || ' WHERE ' || p_where || ';');
            note_dml('delete ' || upper(p_table));
        end if;
    end emit_delete;

    procedure emit_table(
        p_table    in varchar2,
        p_where    in varchar2 default null,
        p_order_by in varchar2 default null
    ) is
        l_owner      varchar2(128);
        l_type       varchar2(30);
        l_cols       t_text_tab;
        l_types      t_text_tab;
        l_vals       t_text_tab;
        l_exprs      clob;
        l_sql        clob;
        l_cur        integer := null;
        l_status     integer;
        l_col_count  pls_integer := 0;
        l_value      varchar2(32767);
        l_row_count  pls_integer := 0;
        l_stage      varchar2(200) := 'start';
        l_column     varchar2(128);
    begin
        resolve_table(p_table, l_owner, l_type);

        if l_owner is null then
            put_line('-- ERROR: INSERT skipped for ' || upper(p_table) || ' - source object is not visible.');
            put_line(null);
            return;
        elsif l_type <> 'TABLE' then
            put_line('-- INFO: INSERT skipped for ' || upper(p_table) || ' - source object is a ' || l_type || '.');
            put_line(null);
            return;
        end if;

        for r in (
            select column_name, data_type
              from all_tab_columns
             where owner = l_owner
               and table_name = upper(p_table)
             order by column_id
        ) loop
            l_stage := 'building expression';
            l_column := r.column_name;

            if not supported_column(r.data_type) then
                put_line('-- INFO: Column skipped for ' || upper(p_table) || '.' || r.column_name || ' - unsupported datatype ' || r.data_type || '.');
                continue;
            end if;

            l_col_count := l_col_count + 1;
            l_cols(l_col_count) := r.column_name;
            l_types(l_col_count) := r.data_type;

            if l_col_count > 1 then
                append_text(l_exprs, ',');
            end if;

            append_text(l_exprs, select_expr(r.column_name, r.data_type) || ' C' || l_col_count);
        end loop;

        if l_col_count = 0 then
            put_line('-- ERROR: INSERT skipped for ' || upper(p_table) || ' - no visible source columns.');
            put_line(null);
            return;
        end if;

        append_text(l_sql, 'select ');
        append_clob(l_sql, l_exprs);
        append_text(l_sql, ' from "' || l_owner || '"."' || upper(p_table) || '"');

        if p_where is not null then
            append_text(l_sql, ' where ' || p_where);
        end if;

        if p_order_by is not null then
            append_text(l_sql, ' order by ' || p_order_by);
        end if;

        l_cur := dbms_sql.open_cursor;
        l_stage := 'parsing dynamic select';
        parse_sql(l_cur, l_sql);

        l_stage := 'defining columns';
        for i in 1 .. l_col_count loop
            dbms_sql.define_column(l_cur, i, l_value, 32767);
        end loop;

        l_stage := 'executing dynamic select';
        l_status := dbms_sql.execute(l_cur);

        l_stage := 'fetching rows';
        while dbms_sql.fetch_rows(l_cur) > 0 loop
            l_row_count := l_row_count + 1;

            for i in 1 .. l_col_count loop
                l_stage := 'reading column ' || i;
                dbms_sql.column_value(l_cur, i, l_value);
                l_vals(i) := value_token(l_value, l_types(i));
            end loop;

            l_stage := 'emitting insert row ' || l_row_count;
            emit_insert(p_table, l_cols, l_vals, l_col_count);
        end loop;

        dbms_sql.close_cursor(l_cur);
        put_line('-- Rows generated from ' || upper(p_table) || ': ' || l_row_count);
        if upper(p_table) = 'STTM_CUST_ACCOUNT' and l_row_count = 0 then
            put_line('-- ERROR: Required table STTM_CUST_ACCOUNT generated zero rows for account ' || g_account || '.');
            g_fatal_generation_errors := g_fatal_generation_errors + 1;
        end if;
        put_line(null);
    exception
        when others then
            if l_cur is not null and dbms_sql.is_open(l_cur) then
                dbms_sql.close_cursor(l_cur);
            end if;

            put_line('-- ERROR: INSERT generation failed for ' || upper(p_table) || ' - ' || sqlerrm);
            put_line('-- ERROR_DETAIL: Stage=' || l_stage || ', LastColumn=' || nvl(l_column, '<none>'));
            put_line('-- ERROR_BACKTRACE: ' || replace(dbms_utility.format_error_backtrace, chr(10), ' '));
            put_line(null);
            g_fatal_generation_errors := g_fatal_generation_errors + 1;
    end emit_table;

    procedure emit_deletes is
    begin
        put_line('-- Delete section');
        put_line('-- The generated script continues on errors and logs ORA messages in the SQL client output.');
        put_line(null);

        emit_delete('ICTB_ACC_PR', 'BRN = ' || lit(g_branch) || ' AND ACC = ' || lit(g_account));
        emit_delete('ICTB_ACC_PR_HISTORY', 'BRN = ' || lit(g_branch) || ' AND ACC = ' || lit(g_account));
        emit_delete('ICTB_ENTRIES', 'BRN = ' || lit(g_branch) || ' AND ACC = ' || lit(g_account));
        emit_delete('ICTB_ENTRIES_HISTORY', 'BRN = ' || lit(g_branch) || ' AND ACC = ' || lit(g_account));
        emit_delete('ICTB_ADJ_INTEREST', 'BRN = ' || lit(g_branch) || ' AND ACC = ' || lit(g_account));
        emit_delete('ICTB_ADJ_INTEREST_HISTORY', 'BRN = ' || lit(g_branch) || ' AND ACC = ' || lit(g_account));
        emit_delete('ICTB_IS_VALS', 'BRN = ' || lit(g_branch) || ' AND ACC = ' || lit(g_account));
        emit_delete('ICTB_BOOK_ERR', 'ACC = ' || lit(g_account));
        emit_delete('ICTB_CALC_ERR', 'ACC = ' || lit(g_account));
        emit_delete('ACTB_VD_BAL', 'BRN = ' || lit(g_branch) || ' AND ACC = ' || lit(g_account));
        emit_delete('ICTB_UDEVALS', 'PROD IN (SELECT DISTINCT PROD FROM ICTB_ACC_PR WHERE BRN = ' || lit(g_branch) || ' AND ACC = ' || lit(g_account) || ') AND COND_TYPE = 0 AND COND_KEY = ' || lit(g_branch || g_account));
        emit_delete('ICTB_ITM_TOV', 'ACC = ' || lit(g_account));
        emit_delete('ICTB_BACK_DATED_UDEVALS', 'COND_KEY LIKE ''%' || replace(g_account, '''', '''''') || '%''');
        emit_delete('ICTB_BACK_DATED_EVENTS', 'ACC LIKE ' || lit(g_account));
        emit_delete('STTM_CUST_ACCOUNT', 'CUST_AC_NO = ' || lit(g_account));
        emit_delete('STTM_ACCOUNT_BALANCE', 'CUST_AC_NO = ' || lit(g_account));
        emit_delete('STTM_CUST_ACCOUNT_DORMANCY', 'CUST_AC_NO = ' || lit(g_account));
        emit_delete('STTB_ACCOUNT', 'AC_GL_NO = ' || lit(g_account));
        emit_delete('ICTM_ACC', 'ACC = ' || lit(g_account));
        emit_delete('ICTM_ACC_PR', 'ACC = ' || lit(g_account));
        emit_delete('ICTM_ACC_EFFDT', 'ACC = ' || lit(g_account));
        emit_delete('ICTM_ACC_UDEVALS', 'ACC = ' || lit(g_account));
        emit_delete('ICTB_DR_INT_DUE', 'ACC = ' || lit(g_account));
        emit_delete('ICTB_DR_INT_PAID', 'ACC = ' || lit(g_account));
        emit_delete('ICTB_CHG_VAL', 'ACC = ' || lit(g_account));
        emit_delete('ICTB_CHG_VAL_HISTORY', 'ACC = ' || lit(g_account));
        emit_delete('ICTB_CHG_DUE', 'ACC = ' || lit(g_account));
        emit_delete('ICTB_ACC_ACCR_BAL_BREAKUP', 'ACCOUNT_NUMBER = ' || lit(g_account));
        emit_delete('ICTB_CHG_ERR', 'ACC = ' || lit(g_account));
        emit_delete('LMTB_LINEACC_UTIL', 'ACC = ' || lit(g_account));
        emit_delete('STTM_AC_STAT_CHANGE', 'BRANCH_CODE = ' || lit(g_branch) || ' AND CUST_AC_NO = ' || lit(g_account));
        emit_delete('CSTB_AUTO_SETTLE_BLOCK', 'MODULE = ''IC'' AND ACCOUNT_NO = ' || lit(g_account));
        emit_commit_checkpoint('delete section complete', true);
        put_line(null);
    end emit_deletes;

    procedure emit_inserts is
        l_acc_products varchar2(32767) :=
            'PRODUCT_CODE IN (SELECT DISTINCT PROD FROM ICTB_ACC_PR WHERE BRN = ' || lit(g_branch) || ' AND ACC = ' || lit(g_account) || ')';
        l_int_products varchar2(32767) :=
            'PRODUCT_CODE IN (SELECT PRODUCT_CODE FROM ICTM_PR_INT_ACLASS WHERE ACLASS = ' || lit(g_aclass) ||
            ' AND CCY = ' || lit(g_currency) || ' AND RECORD_STAT <> ''C'')';
        l_chg_products varchar2(32767) :=
            'PRODUCT_CODE IN (SELECT PRODUCT_CODE FROM ICTM_PR_CHG_ACLASS WHERE ACLASS = ' || lit(g_aclass) ||
            ' AND CCY = ' || lit(g_currency) || ' AND RECORD_STAT <> ''C'')';
        l_all_products varchar2(32767) :=
            'PRODUCT_CODE IN (SELECT PRODUCT_CODE FROM ICTM_PR_INT_ACLASS WHERE ACLASS = ' || lit(g_aclass) ||
            ' AND CCY = ' || lit(g_currency) || ' AND RECORD_STAT <> ''C'' UNION SELECT PRODUCT_CODE FROM ICTM_PR_CHG_ACLASS WHERE ACLASS = ' ||
            lit(g_aclass) || ' AND CCY = ' || lit(g_currency) || ' AND RECORD_STAT <> ''C'')';
        l_rules varchar2(32767) :=
            'RULE_ID IN (SELECT RULE FROM ICTM_PR_INT WHERE PRODUCT_CODE IN (SELECT PRODUCT_CODE FROM ICTM_PR_INT_ACLASS WHERE ACLASS = ' ||
            lit(g_aclass) || ' AND CCY = ' || lit(g_currency) || ' AND RECORD_STAT <> ''C''))';
        l_index_rate_filter varchar2(32767) :=
            'INDEX_CCY = ''UFR'' AND BASE_CCY = ' || lit(g_currency);
    begin
        put_line('-- Insert section');
        put_line(null);

        emit_table('STTM_DATES', 'BRANCH_CODE = ' || lit(g_branch));
        emit_table('ICTM_PRODUCT_DEFINITION', l_acc_products, 'PRODUCT_CODE');
        emit_table('ICTB_ACC_PR', 'BRN = ' || lit(g_branch) || ' AND ACC = ' || lit(g_account));
        emit_table('ICTB_ACC_PR_HISTORY', 'BRN = ' || lit(g_branch) || ' AND ACC = ' || lit(g_account));
        emit_table('ICTB_ENTRIES', 'BRN = ' || lit(g_branch) || ' AND ACC = ' || lit(g_account));
        emit_table('ICTB_ENTRIES_HISTORY', 'BRN = ' || lit(g_branch) || ' AND ACC = ' || lit(g_account), 'ENT_DT');
        emit_table('ICTB_ADJ_INTEREST', 'BRN = ' || lit(g_branch) || ' AND ACC = ' || lit(g_account));
        emit_table('ICTB_ADJ_INTEREST_HISTORY', 'BRN = ' || lit(g_branch) || ' AND ACC = ' || lit(g_account));
        emit_table('ICTB_IS_VALS', 'BRN = ' || lit(g_branch) || ' AND ACC = ' || lit(g_account));
        emit_table('ICTB_BOOK_ERR', 'ACC = ' || lit(g_account));
        emit_table('ICTB_CALC_ERR', 'ACC = ' || lit(g_account));
        emit_table('ACTB_VD_BAL', 'BRN = ' || lit(g_branch) || ' AND ACC = ' || lit(g_account), 'VAL_DT');
        emit_table('ICTB_UDEVALS', 'PROD IN (SELECT DISTINCT PROD FROM ICTB_ACC_PR WHERE BRN = ' || lit(g_branch) || ' AND ACC = ' || lit(g_account) || ') AND COND_TYPE = 1 AND COND_KEY LIKE ''%' || replace(g_aclass, '''', '''''') || '%'' AND ROWNUM <= 500', 'UDE_EFF_DT DESC');
        emit_table('ICTB_UDEVALS', 'PROD IN (SELECT DISTINCT PROD FROM ICTB_ACC_PR WHERE BRN = ' || lit(g_branch) || ' AND ACC = ' || lit(g_account) || ') AND COND_TYPE = 0 AND COND_KEY = ' || lit(g_branch || g_account) || ' AND ROWNUM <= 500', 'PROD, UDE_EFF_DT DESC');
        emit_table('ICTB_ITM_TOV', 'ACC = ' || lit(g_account));
        emit_table('ICTB_BACK_DATED_UDEVALS', 'COND_KEY LIKE ''%' || replace(g_aclass, '''', '''''') || '%''');
        emit_table('ICTB_BACK_DATED_UDEVALS', 'COND_KEY LIKE ''%' || replace(g_account, '''', '''''') || '%''');
        emit_table('ICTB_BACK_DATED_EVENTS', 'ACC LIKE ' || lit(g_account));
        emit_commit_checkpoint('account IC history/balance section complete', true);
        emit_table('STTM_CUSTOMER', 'CUSTOMER_NO = ' || lit(g_customer_no));
        emit_table('STTM_ACCOUNT_CLASS', 'ACCOUNT_CLASS = ' || lit(g_aclass));
        emit_table('STTB_ACCOUNT', 'AC_GL_NO = ' || lit(g_account));
        emit_table('STTM_CUST_ACCOUNT', 'CUST_AC_NO = ' || lit(g_account));
        emit_table('STTM_ACCOUNT_BALANCE', 'CUST_AC_NO = ' || lit(g_account));
        emit_table('STTM_CUST_ACCOUNT_DORMANCY', 'CUST_AC_NO = ' || lit(g_account));
        emit_table('ICTM_ACC', 'ACC = ' || lit(g_account));
        emit_table('ICTM_ACC_PR', 'ACC = ' || lit(g_account));
        emit_table('ICTM_ACC_EFFDT', 'ACC = ' || lit(g_account));
        emit_table('ICTM_ACC_UDEVALS', 'ACC = ' || lit(g_account));
        emit_table('ICTB_DR_INT_DUE', 'ACC = ' || lit(g_account));
        emit_table('ICTB_DR_INT_PAID', 'ACC = ' || lit(g_account));
        emit_table('ICTB_CHG_VAL', 'ACC = ' || lit(g_account));
        emit_table('ICTB_CHG_VAL_HISTORY', 'ACC = ' || lit(g_account));
        emit_table('ICTB_CHG_DUE', 'ACC = ' || lit(g_account));
        emit_table('ICTB_ACC_ACCR_BAL_BREAKUP', 'ACCOUNT_NUMBER = ' || lit(g_account));
        emit_table('ICTB_CHG_ERR', 'ACC = ' || lit(g_account));
        emit_commit_checkpoint('account/customer section complete', true);
        emit_table('ICTM_BRANCH_PARAMETERS', 'BRANCH_CODE = ' || lit(g_branch));
        emit_table('CSTM_PRODUCT_EVENT_ACCT_ENTRY', l_all_products, 'PRODUCT_CODE, EVENT_CODE, AMT_TAG, DR_CR_INDICATOR');
        emit_table('CSTM_PRODUCT_ACCROLE', l_all_products, 'PRODUCT_CODE');
        emit_table('STTM_TRN_CODE', 'TRN_CODE IN (SELECT TRANSACTION_CODE FROM CSTM_PRODUCT_EVENT_ACCT_ENTRY WHERE ' || l_all_products || ')');
        emit_table('STTM_TRN_CODE', 'IC_TXN_COUNT = ''Y'' OR IC_TOVER_INCLUSION = ''Y''');
        emit_table('CSTM_PRODUCT_STATUS_GL', 'PRODUCT IN (SELECT PRODUCT_CODE FROM ICTM_PR_INT_ACLASS WHERE ACLASS = ' || lit(g_aclass) || ' AND CCY = ' || lit(g_currency) || ' AND RECORD_STAT <> ''C'' UNION SELECT PRODUCT_CODE FROM ICTM_PR_CHG_ACLASS WHERE ACLASS = ' || lit(g_aclass) || ' AND CCY = ' || lit(g_currency) || ' AND RECORD_STAT <> ''C'')');
        emit_table('ICTM_PR_INT', l_int_products, 'PRODUCT_CODE');
        emit_table('ICTM_PR_INT_ACLASS', l_int_products, 'PRODUCT_CODE');
        emit_table('ICTM_PR_INT_EFFDT', l_int_products);
        emit_table('ICTM_PR_INT_UDEVALS', l_int_products);
        emit_table('ICTM_PR_CHG', l_chg_products);
        emit_table('ICTM_PR_CHG_ACLASS', 'ACLASS = ' || lit(g_aclass) || ' AND CCY = ' || lit(g_currency) || ' AND RECORD_STAT <> ''C''', 'PRODUCT_CODE');
        emit_table('ICTM_PR_CHG_CONSOL', 'PROD IN (SELECT PRODUCT_CODE FROM ICTM_PR_CHG_ACLASS WHERE ACLASS = ' || lit(g_aclass) || ' AND CCY = ' || lit(g_currency) || ' AND RECORD_STAT <> ''C'')', 'PROD');
        emit_table('ICTM_PR_CHG_SLAB', l_chg_products, 'PRODUCT_CODE');
        emit_table('ICTM_PR_CHG_TXN', 'PROD IN (SELECT PRODUCT_CODE FROM ICTM_PR_CHG_ACLASS WHERE ACLASS = ' || lit(g_aclass) || ' AND CCY = ' || lit(g_currency) || ' AND RECORD_STAT <> ''C'')', 'PROD');
        emit_table('ICTM_PR_CHG_PRODS', 'PRODUCT IN (SELECT PRODUCT_CODE FROM ICTM_PR_CHG_ACLASS WHERE ACLASS = ' || lit(g_aclass) || ' AND CCY = ' || lit(g_currency) || ' AND RECORD_STAT <> ''C'')');
        emit_table('ICTM_RULE', l_rules);
        emit_table('ICTM_RULE_FRM', l_rules, 'RULE_ID, FRM_NO');
        emit_table('ICTM_EXPR', l_rules, 'RULE_ID, FRM_NO, EXPR_LINE');
        emit_table('ICTM_RULE_FRM_ELEMENTS', l_rules);
        emit_table('ICTM_RULE_SDE', l_rules);
        emit_table('ICTM_RULE_UDE', l_rules);
        emit_commit_checkpoint('product/rule dependency section complete', true);
        emit_table('STTM_LCL_HOLIDAY', 'BRANCH_CODE = ' || lit(g_branch) || ' AND YEAR = (SELECT TO_CHAR(TODAY, ''YYYY'') FROM STTM_DATES WHERE BRANCH_CODE = ' || lit(g_branch) || ')');
        emit_table('GETM_FACILITY', 'LIAB_ID = (SELECT ID FROM GETM_LIAB, STTM_CUST_ACCOUNT WHERE LIAB_NO = CUST_NO AND CUST_AC_NO = ' || lit(g_account) || ')');
        emit_table('GETM_FACILITY_VD_DETAILS', 'ID IN (SELECT ID FROM GETM_FACILITY WHERE LIAB_ID = (SELECT ID FROM GETM_LIAB, STTM_CUST_ACCOUNT WHERE LIAB_NO = CUST_NO AND CUST_AC_NO = ' || lit(g_account) || '))');
        emit_table('GETB_UTILS', 'LIAB_ID = (SELECT ID FROM GETM_LIAB, STTM_CUST_ACCOUNT WHERE LIAB_NO = CUST_NO AND CUST_AC_NO = ' || lit(g_account) || ')');
        emit_table('GETM_LIAB', 'LIAB_NO = (SELECT CUST_NO FROM STTM_CUST_ACCOUNT WHERE CUST_AC_NO = ' || lit(g_account) || ')');
        emit_table('LMTB_LINEACC_UTIL', 'ACC = ' || lit(g_account));
        emit_table('STTM_AC_STAT_CHANGE', 'BRANCH_CODE = ' || lit(g_branch) || ' AND CUST_AC_NO = ' || lit(g_account));
        emit_table('CSTB_AUTO_SETTLE_BLOCK', 'MODULE = ''IC'' AND ACCOUNT_NO = ' || lit(g_account));
        emit_commit_checkpoint('liability/utilization section complete', true);
        emit_table('ICTM_RATE_DEF');
        emit_table('ICTM_RATES');
        emit_commit_checkpoint('interest rate dependency section complete', true);

        put_line('-- Chile UF/UFR index data for readjustment calculations');
        put_line('-- CYPKS_UTILS.FN_INDEX_RATE reads CYTMS_INDEX_RATES, backed here by CYTM_INDEX_RATES.');
        emit_table('CYTM_CCY_DEFN', 'CCY_CODE IN (' || lit(g_currency) || ', ''UFR'') OR INDEX_FLAG = ''Y''', 'CCY_CODE');
        emit_table('CYTM_INDEX_PAIRS', '(INDEX_CCY = ''UFR'' AND BASE_CCY = ' || lit(g_currency) || ') OR INDEX_CCY IN (SELECT CCY_CODE FROM CYTM_CCY_DEFN WHERE INDEX_FLAG = ''Y'')', 'INDEX_CCY, BASE_CCY, BRANCH_CODE');
        emit_table('CYTM_INDEX_RATES', l_index_rate_filter, 'INDEX_CCY, BASE_CCY, RATE_DATE');
        emit_commit_checkpoint('currency index dependency section complete', true);
    end emit_inserts;
begin
    put_line('WHENEVER SQLERROR CONTINUE;');
    put_line('SET DEFINE OFF;');
    put_line('SET SQLBLANKLINES ON;');
    put_line('SET HEADING OFF;');
    put_line('SET FEEDBACK OFF;');
    put_line('ALTER SESSION SET NLS_LANGUAGE = ''AMERICAN'';');
    put_line('ALTER SESSION SET NLS_DATE_LANGUAGE = ''AMERICAN'';');
    put_line('ALTER SESSION SET NLS_DATE_FORMAT = ''DD-MM-YYYY'';');
    put_line('ALTER SESSION SET NLS_NUMERIC_CHARACTERS = ''.,'';');
    put_line(null);
    put_line('-- IC account data generated for branch ' || g_branch || ' account ' || g_account);
    put_line('-- Account class: ' || nvl(g_aclass, '<not found>'));
    put_line('-- Currency     : ' || nvl(g_currency, '<not found>'));
    put_line('-- Customer     : ' || nvl(g_customer_no, '<not found>'));
    put_line(null);

    emit_deletes;
    emit_inserts;

    if g_fatal_generation_errors > 0 then
        put_line('ROLLBACK;');
        put_line('PROMPT ============================================================');
        put_line('PROMPT IC ACCOUNT DATA SCRIPT COMPLETED WITH GENERATION ERRORS');
        put_line('PROMPT ROLLBACK EXECUTED. DATA WAS NOT COMMITTED.');
        put_line('PROMPT Fix the generated ERROR lines before executing this INC again.');
        put_line('PROMPT ============================================================');
    else
        put_line('COMMIT;');
        put_line('SELECT ''IC_DB_FINAL_OK|' || g_total_dml || '|complete'' FROM DUAL;');
        put_line('PROMPT ============================================================');
        put_line('PROMPT IC ACCOUNT DATA SCRIPT EXECUTION COMPLETE AND COMMITTED');
        put_line('PROMPT Review any ORA-/SP2-/PLS- messages above; script continues on errors.');
        put_line('PROMPT ============================================================');
    end if;
    put_line('WHENEVER SQLERROR CONTINUE;');
    put_line('SET DEFINE ON;');
end;
/

spool off

set feedback on
set heading on
set verify on

prompt Generated D:\IC_account_data_&Account..INC
