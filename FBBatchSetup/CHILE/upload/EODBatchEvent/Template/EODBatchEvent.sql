SELECT 'DEVENGO DE CREDITOS ACTIVOS' AS TYPE_OF_TXN, COUNT(1) AS RECORDS_COUNT
  FROM CLTB_ACCOUNT_MASTER
 WHERE account_status = 'A'
   AND NVL(STOP_ACCRUALS, 'N') <> 'Y'
   AND book_date <= to_date('%PROCESSING_DATE%','dd-mm-yyyy')
--SPLIT--
SELECT 'CALCULO DE DEVENGO-'||b.aclass || '-' || d.product_description  AS TYPE_OF_TXN, COUNT(1) AS RECORDS_COUNT
  FROM ICTB_ACC_PR A, ICTM_PR_INT_ACLASS b,  CSTM_PRODUCT D
 WHERE Last_accr_Dt = to_date('%DAY_BEFORE_TODAY%','dd-mm-yyyy')
   AND NVL(stop_ic, 'N') = 'N'
   AND a.prod = b.product_code
	 AND d.product_code = b.product_code
 GROUP BY 'CALCULO DE DEVENGO-'||b.aclass || '-' || d.product_description
 ORDER BY 'CALCULO DE DEVENGO-'||b.aclass || '-' || d.product_description
--SPLIT--
 SELECT 'CUENTAS CASA - CALCULO DE SALDO A LA FECHA DE VALOR' AS TYPE_OF_TXN, COUNT(1) AS RECORDS_COUNT FROM ACTB_VD_BAL WHERE val_dt = to_date('%PROCESSING_DATE%','dd-mm-yyyy')
--SPLIT--
SELECT /*+parallel(10)*/ 'CUENTAS CASA - CALCULO DE SALDO A LA FECHA DE TRANSACCION' AS TYPE_OF_TXN,
 COUNT(1) AS RECORDS_COUNT
  FROM ACTB_ACCBAL_HISTORY
 WHERE bkg_date = to_date('%PROCESSING_DATE%','dd-mm-yyyy')
--SPLIT--
SELECT 'CANTIDAD DE RESOLUCIONES DE IC DEL DÍA' AS TYPE_OF_TXN, COUNT(1) AS RECORDS_COUNT
  FROM ICTB_MAINT_QUEUE
 WHERE TRUNC(TIMESTAMP) = to_date('%PROCESSING_DATE%','dd-mm-yyyy')
   AND status = 'P'
   AND brn IN
       (SELECT branch_code FROM STTM_BRANCH WHERE parent_branch = '001')
--SPLIT--
SELECT /*+parallel(10)*/ 'TRANSFERENCIAS DE LINEA DE CREDITO' AS TYPE_OF_TXN,
 COUNT(DISTINCT trn_ref_no) AS RECORDS_COUNT
  FROM ACTB_HISTORY
 WHERE trn_dt = to_date('%PROCESSING_DATE%','dd-mm-yyyy')
   AND event = 'BTRF'
--SPLIT--
SELECT /*+parallel(10)*/ 'RECUPERACION DE LINEA DE CREDITO' AS TYPE_OF_TXN,
 COUNT(DISTINCT trn_ref_no) AS RECORDS_COUNT
  FROM ACTB_HISTORY
 WHERE trn_dt = to_date('%PROCESSING_DATE%','dd-mm-yyyy')
   AND event = 'LOCR'
--SPLIT--
SELECT 'LIBERACION DE MONTOS BLOQUEADOS' AS TYPE_OF_TXN, COUNT(1) AS RECORDS_COUNT FROM CATM_AMOUNT_BLOCKS WHERE Expiry_Date = to_date('%PROCESSING_DATE%','dd-mm-yyyy')
--SPLIT--
SELECT  'REGISTROS PARA HANDOFF FILE - HANDOFF' AS TYPE_OF_TXN, COUNT(1)AS RECORDS_COUNT FROM egtb_handoff_dly
--SPLIT--
SELECT 'REGISTROS PARA HANDOFF FILE - GL' AS TYPE_OF_TXN, COUNT(1)AS RECORDS_COUNT FROM actb_cust_gl_ent_dly
--SPLIT--
SELECT 'REGISTROS PARA HANDOFF FILE - ENTRIES' AS TYPE_OF_TXN, COUNT(1)AS RECORDS_COUNT FROM egtb_entries_dly_log
--SPLIT--
SELECT 'CREACIÓN DE CRÉDITOS POR SOBREGIRO DE CUENTA VISTA' AS TYPE_OF_TXN, COUNT(1)AS RECORDS_COUNT FROM STTM_CUSAC_OD_LN_DETAILS WHERE loan_value_date >= to_date('%PROCESSING_DATE%','dd-mm-yyyy') GROUP BY loan_value_date
--SPLIT--
SELECT /*+parallel(10)*/ 'PRE-GENERACION DE CARTOLA - CUENTA CORRIENTE y CUENTA AHORRO' AS TYPE_OF_TXN,
  COUNT(1) AS RECORDS_COUNT
FROM MSTB_ACC_STMT_MASTER_FAL
WHERE BRANCH_DATE = to_date('%PROCESSING_DATE%','dd-mm-yyyy')  -- Replace <date> with your desired statement date (e.g., '31-JUL-2025')
GROUP BY MSG_GEN_STAT
--SPLIT--
SELECT /*+parallel(10)*/ 'PRE-GENERACION DE CARTOLA - LDC' AS TYPE_OF_TXN,
  COUNT(1) AS RECORDS_COUNT
FROM MSTB_LOC_STMT_MASTER_FAL
WHERE BRANCH_DATE  = to_date('%PROCESSING_DATE%','dd-mm-yyyy')   -- Replace <date> with your desired statement date (e.g., '31-JUL-2025')
GROUP BY MSG_GEN_STAT
--SPLIT--
SELECT 'COMP. DE LIQ DE INTERESES-' || e.account_class || '-' || d.product_description AS TYPE_OF_TXN,  -- 15th query execute on 1st working day of month
       COUNT(1) AS RECORDS_COUNT
  FROM ICTB_ACC_PR A, CSTM_PRODUCT d, STTM_CUST_ACCOUNT e
 WHERE Last_liq_Dt = to_date('%PREV_MONTH_END_DATE%','dd-mm-yyyy')
   AND NVL(stop_ic, 'N') = 'N'
   AND a.prod = d.product_code
   AND a.acc = e.cust_ac_no
   AND a.brn = e.branch_code
   AND e.record_stat = 'O'
   AND NOT EXISTS (SELECT 1
          FROM STTM_CUST_ACCOUNT_CLOSURE b
         WHERE b.cust_ac_no = e.cust_ac_no
           AND b.branch_code = e.branch_code
           AND b.record_stat = 'O')
 GROUP BY 'COMP. DE LIQ DE INTERESES-' || e.account_class || '-' || d.product_description
 ORDER BY 'COMP. DE LIQ DE INTERESES-' || e.account_class || '-' || d.product_description
--SPLIT--
SELECT 'COBRO DE LIQ DE INTERESES-' || c.account_class || '-' || d.product_description AS TYPE_OF_TXN,  --16th query  execute on 1st working day of month
       COUNT(1) AS RECORDS_COUNT
  FROM ICTB_ENTRIES_HISTORY A,
       STTM_CUST_ACCOUNT    B,
       STTM_ACCOUNT_CLASS   C,
       CSTM_PRODUCT         D
 WHERE ent_dt = to_date('%PREV_MONTH_END_DATE%','dd-mm-yyyy')
   AND liqn = 'Y'
   AND Entry_Passed = 'Y'
   AND a.acc = b.cust_Ac_no
   AND a.brn = b.branch_code
   AND b.account_class = c.account_class
   AND a.prod = d.product_code
   AND c.ac_class_type = 'S'
   AND b.record_stat = 'O'
   AND NOT EXISTS (SELECT 1
          FROM STTM_CUST_ACCOUNT_CLOSURE e
         WHERE b.cust_ac_no = e.cust_ac_no
           AND b.branch_code = e.branch_code
           AND e.record_stat = 'O')
 GROUP BY 'COBRO DE LIQ DE INTERESES-' || c.account_class || '-' || d.product_description
 ORDER BY 'COBRO DE LIQ DE INTERESES-' || c.account_class || '-' || d.product_description
--SPLIT--
SELECT 'COBRO DE LIQ DE INTERESES-' || c.account_class || '-' || d.product_description AS TYPE_OF_TXN,  -- 17th query execute on 2nd working day of month
       COUNT(1) AS RECORDS_COUNT
  FROM ICTB_ENTRIES_HISTORY A,
       STTM_CUST_ACCOUNT    B,
       STTM_ACCOUNT_CLASS   C,
       CSTM_PRODUCT         D
 WHERE ent_dt = to_date('%PREV_MONTH_END_DATE%','dd-mm-yyyy')
   AND liqn = 'Y'
   AND Entry_Passed = 'Y'
   AND a.acc = b.cust_Ac_no
   AND a.brn = b.branch_code
   AND b.account_class = c.account_class
   AND a.prod = d.product_code
   AND c.ac_class_type = 'U'
   AND b.record_stat = 'O'
   AND NOT EXISTS (SELECT 1
          FROM STTM_CUST_ACCOUNT_CLOSURE e
         WHERE b.cust_ac_no = e.cust_ac_no
           AND b.branch_code = e.branch_code
           AND e.record_stat = 'O')
 GROUP BY 'COBRO DE LIQ DE INTERESES-' || c.account_class || '-' || d.product_description
 ORDER BY 'COBRO DE LIQ DE INTERESES-' || c.account_class || '-' || d.product_description