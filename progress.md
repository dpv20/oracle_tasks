# Oracle Tasks Chile — Progress Log

Registro cronológico de avance, decisiones y problemas. Entradas más recientes arriba.

Para contexto general del proyecto leer `implementation_plan.md`.

Convención:
- ✅ completado · ⚠️ nota / problema · 🔧 decisión · 🚧 en progreso · ❌ bloqueado

---

## 2026-05-19

### Spools / Savings Accounts — primer flujo funcional
- ✅ Creado dominio Python `src/spools_savings_accounts/` con `SpoolSavingsEngine`: resuelve branch por cuenta desde la DB origen (`STTM_CUST_ACCOUNT` / `ICTB_ACC_PR` / `STTB_ACCOUNT`), renderiza una copia temporal no interactiva de `spools_savings/IC_account_data_falabella_v2.sql`, escribe `IC_account_data_<cuenta>.INC` en `%LOCALAPPDATA%\OracleTasksChile\spools_savings_out\<Pais>` y puede aplicar `.INC`/`.SQL` existentes contra destino.
- ✅ El render temporal Savings elimina los `ACCEPT Branch/Account`, define `Branch`/`Account`, reemplaza el spool legacy `D:\IC_account_data_&Account..INC` por la ruta runtime, agrega `dbms_output.enable(null)` si falta y asegura `exit;` sin modificar el script versionado.
- ✅ Creada vista `src/ui/spools_savings_view.py` con interfaz espejo de CL Accounts: pais, DB origen, DB destino, Extract/Inject por cuenta, carga individual, carga masiva desde Excel/texto, cancelacion, carpeta de spools, Extract/Apply y Apply existing.
- ✅ Home/App routing: `Spools / Savings Accounts` deja de ser placeholder y abre `spools_savings`; `paths.ensure_dirs()` ahora crea carpetas Savings por pais.
- ✅ i18n EN/ES agregado para Savings.
- ✅ Verificacion: `python -m compileall src` OK; smoke render confirma sin `ACCEPT`, sin `D:\`, con output runtime, `dbms_output.enable(null)` y `exit;`; smoke engine con runner falso resuelve branch 001 y genera `.INC`; smoke i18n sin keys faltantes; smoke UI construye `spools_savings`.
- ⚠️ Nota verificacion local: crear las carpetas runtime en `%LOCALAPPDATA%` requirio ejecutar el smoke fuera del sandbox del workspace; creadas `Chile/Peru/Colombia/Mexico` bajo `spools_savings_out`.

### CL Accounts — carga masiva de cuentas
- ✅ `src/ui/spools_cl_view.py`: agregado modo separado `Add many...` / `Agregar varias...` junto al input individual. Abre un dialog con textarea para pegar muchas cuentas desde Excel o texto plano, manteniendo intacto el flujo actual `Account number` + `+ Add`.
- ✅ La carga masiva reutiliza `parse_accounts()`: acepta cuentas separadas por saltos de linea, tabs, espacios, comas o punto y coma; deduplica contra la lista actual, agrega las nuevas a Extract e Inject por defecto, y muestra aviso si hay valores invalidos.
- ✅ i18n EN/ES actualizado para el boton, dialog y mensajes de resultado.
- ✅ Verificacion: `python -m compileall src` OK; smoke parser con columna Excel OK; smoke `_add_bulk_accounts()` agrega nuevas, salta duplicadas y reporta invalidas.

### CL Accounts — Mexico template
- ✅ Creado `spools_CL/CL_ACCOUNT_SPOOL_MEXICO2.sql` copiando el spool no interactivo de Peru y ajustando la ruta hardcoded del `spool` a `Accounts\Mexico`, para que el render temporal escriba en `%LOCALAPPDATA%\OracleTasksChile\spools_CL_out\Mexico`.
- ✅ `src/ui/spools_cl_view.py`: el selector de paises para Extract/Apply ahora se arma desde `spools_cl_accounts.databases.countries()` y `has_cl_template()`, por lo que Mexico aparece automaticamente al existir `CL_ACCOUNT_SPOOL_MEXICO2.sql`.
- ✅ `implementation_plan.md`: actualizado el layout versionado y runtime para incluir Mexico, y cerrada la nota antigua de template faltante.
- ✅ Verificacion: `python -m compileall src` OK; smoke import confirma Mexico en `_EXTRACT_COUNTRIES`; render temporal de Mexico apunta a `spools_CL_out\Mexico`, no deja la ruta legacy activa ni apunta a Peru, y termina con `exit;`.

## 2026-05-14

### Separacion spools CL / Savings
- ✅ Renombrado el dominio generico de spools de cuentas CL a nombres explicitos: carpeta SQL `spools_CL/`, package Python `src/spools_cl_accounts/`, vista `src/ui/spools_cl_view.py`, engine `src/spools_cl_accounts/spool_cl_engine.py`, clase `SpoolCLEngine` y estado `SpoolCLStatus`.
- ✅ Separado el output runtime: CL usa `%LOCALAPPDATA%\OracleTasksChile\spools_CL_out`; Savings queda reservado como `%LOCALAPPDATA%\OracleTasksChile\spools_savings_out`.
- ✅ Creado `spools_savings/` para scripts base Savings / IC y movido ahi `IC_account_data_falabella_v2.sql`. Los artefactos de prueba descartables siguen en `temp_savings/`.
- ✅ Ajustados `agent.md`, `implementation_plan.md`, `.gitignore`, rutas, imports, labels i18n y navegacion Home para reflejar `spools_CL` / `spools_savings` y evitar que `spools` quede como nombre de dominio generico.
- ✅ Verificacion: `python -m compileall src` OK; smoke import confirma `SPOOLS_CL_DIR=spools_CL`, `SPOOLS_SAVINGS_DIR=spools_savings`, outputs `spools_CL_out`/`spools_savings_out`, templates CL para Chile/Peru/Colombia y clase `SpoolCLEngine`.

### Query manual Savings / IC
- ✅ Creado `query_savings_tablas_importantes.sql` como query de revisión manual para cuentas Savings/IC en PL/SQL Developer. Está basada en las tablas y filtros usados por `IC_account_data_falabella_v2.sql`, usando `&acc` como única variable y separando datos base, IC/intereses, cargos, facilities/bloqueos, setup de producto/rules e índices UF/UFR.
- ✅ Ajustado `query_savings_tablas_importantes.sql` para no pedir `&brn`: ahora solo solicita `&acc` y deriva el branch desde `STTM_CUST_ACCOUNT`, con fallback a `ICTB_ACC_PR` y `STTB_ACCOUNT`.
- ✅ Simplificado `query_savings_tablas_importantes.sql` a una revisión rápida tipo `query_tablas_importantes.sql` de CL: solo tablas clave de cuenta/balance/IC entries/accrual/UDE/turnover/bloqueos usando `&acc`.
- ⚠️ `STTM_ACCOUNT_BALANCE` dio ORA-00942 en PL/SQL Developer y el `.INC` generado también la marca como `source object is not visible`; quedó comentada en la query rápida para no cortar la ejecución.
- ✅ Para prueba manual de Savings/IC, ajustado `IC_account_data_falabella_v2_RUN.sql` para spolear en la carpeta actual (`IC_account_data_&Account..INC`) en vez de `D:\` y habilitar `dbms_output.enable(null)` dentro del bloque.
- ⚠️ Hallazgo runtime: durante la ejecución manual, el `.INC` puede quedarse en 0 bytes mientras SQLcl sigue corriendo porque el generador emite todo vía `DBMS_OUTPUT`; SQLcl recién vuelca el contenido al spool cuando termina el bloque PL/SQL.
- ✅ Prueba real: generado `IC_account_data_8000109678685.INC` desde `CHILE_QA_19C` para branch `001`. Archivo quedó en la carpeta del proyecto (~9.5 MB), termina con `COMMIT;`; única advertencia detectada es `STTM_ACCOUNT_BALANCE - source object is not visible`.
- ⚠️ Al aplicar el `.INC` en DEV aparecieron `ORA-00001` en tablas maestras compartidas (`STTM_TRN_CODE`, `ICTM_RATES`). No se considera deal breaker por sí solo: son datos de setup ya existentes y el script corre con `WHENEVER SQLERROR CONTINUE`; validar al final que llegue a `COMMIT` y que las tablas propias de la cuenta existan en DEV.
- ✅ Organización temporal: creado `temp_savings/` y movidos ahí los artefactos de trabajo Savings generados en esta sesión (`query_savings_tablas_importantes.sql`, `IC_account_data_falabella_v2_RUN.sql`, `IC_account_data_8000109678685.INC`). El generador base `IC_account_data_falabella_v2.sql` queda separado en `spools_savings/`.

### Fase 4 — validacion usuario + merge a main
- ✅ Verificacion usuario: probado el funcionamiento de ambientes para el flujo extract/apply. Con esto Fase 4 queda lista para mergear a `main` y avanzar a Fase 5.
- 🔧 Decision: la ruta `Desktop\sqlcl\sqlcl\bin\sql.exe` es una particularidad del equipo de Diego, no un requisito para los companeros. No se considera pendiente del `install.bat`; el instalador debe seguir priorizando PATH/rutas comunes/manual.

### Home / navegación
- ✅ Agregado botón `Create Branch` en Home, visible junto a `Spools / CL Accounts` y `Spools / Savings Accounts`. Queda como placeholder con aviso hasta definir el flujo.

### Fase 5 — Apply Existing ✅ COMPLETADA
- ✅ Verificación usuario: prueba real del modo `Apply Existing` ejecutada con un spool `.SQL` existente contra ambiente destino. La aplicación tomó el archivo, pidió confirmación, ejecutó la inyección y el resultado quedó OK en pantalla. Con esto el hito real de Fase 5 queda validado.
- ✅ Home/task naming: `Spools / Accounts` renombrado a `Spools / CL Accounts` en Home y en la vista de spools. Agregado nuevo botón `Spools / Savings Accounts` en Home con aviso placeholder hasta definir ese flujo.
- ✅ Ícono actualizado: `new_icon.png` movido a `assets/new_icon.png`; `assets/icono.ico` regenerado desde ese PNG multi-resolución. `install.bat` ahora genera/fallback-ea usando `new_icon.png` en vez de `icono.jpg`; `implementation_plan.md` actualizado.
- ✅ Cancelación de runs: el botón principal pasa a `Cancel` / `Cancelar` en rojo mientras corre Extract/Apply/Apply Existing. Al cancelar, se setea un `threading.Event`, se evita lanzar cuentas nuevas y SQLcl activo se termina desde `SqlclRunner`.
- ✅ Resumen por cuenta en pantalla: al terminar o cancelar se muestra qué cuentas quedaron `Extracted + injected`, cuáles `Only extracted` y cuáles `Nothing`. En Apply Existing se muestra `Injected` / `Nothing`.
- ✅ `SpoolStatus.CANCELLED` agregado como estado terminal con glyph propio en `AccountStatusRow`; los callbacks tardíos siguen protegidos por `run_id`/fase.
- ✅ Verificación: `python -m compileall src` OK; smoke de `SqlclRunner` con cancel pre-set retorna exit 130 `Cancelled`; smoke de clasificación devuelve injected/only_extracted/nothing correcto; smoke UI confirma botón rojo `Cancel` y resumen final.
- 🔧 Decision: se elimina `spools_sql/` y la app pasa a usar directamente los scripts no interactivos `spools/CL_ACCOUNT_SPOOL_<PAIS>2.sql`. Los `sin 2` usan `Accept ACC_NO` / `&ACC_NO` y quedan fuera del flujo automatico.
- ✅ `src/spools_accounts/spool_engine.py`: `template_path()` ahora apunta a `spools/CL_ACCOUNT_SPOOL_<PAIS>2.sql`; `_render_template()` crea una copia temporal reemplazando la raiz legacy `...\spools_files\Accounts` por `paths.SPOOLS_OUT_DIR` y sigue agregando `exit;` si falta. Los `.sql` versionados no se editan en runtime.
- ✅ `agent.md` e `implementation_plan.md`: actualizados para reflejar que ya no existen templates `.sql.tmpl` ni carpeta `spools_sql/`.
- ✅ Limpieza repo/local: eliminados los scripts interactivos `spools/CL_ACCOUNT_SPOOL_CHILE.sql`, `...PERU.sql`, `...COLOMBIA.sql`; queda solo la familia `*2.sql`. Eliminada carpeta local ignorada `spools/spools_files/` con spools generados antiguos, porque la app escribe en `%LOCALAPPDATA%\OracleTasksChile\spools_out`.
- ✅ Verificacion: `python -m compileall src` OK; `has_template()` True para Chile/Peru/Colombia y False para Mexico; render temporal de Chile usa `SPOOLS_OUT_DIR`, no deja la ruta legacy activa, y termina con `exit;`.
- ⚠️ Bug encontrado y corregido: callbacks UI tardios (`root.after(0, ...)`) podian ejecutarse despues de `_finish()` y volver a pintar el resumen como `Extracting...` / `Injecting...` aunque SQLcl ya habia terminado. Fix: cada run ahora tiene `run_id` y fase activa (`extract`/`inject`/`apply_existing`); callbacks viejos o de fases cerradas pueden actualizar la fila, pero no pisan el resumen final.
- ✅ `src/ui/spools_view.py`: botón de carpeta actualizado de `Open spools folder` a texto dinámico por país (`Chile spools folder`, `Peru spools folder`, etc.) y refresca al cambiar país/modo.
- ✅ Creada rama `feat/apply-existing-spool` desde `main` actualizado.
- ✅ `src/ui/spools_view.py`: agregado modo `Apply existing` con selector de archivo `.SQL`, ocultando origen/lista de cuentas y usando solo DB destino.
- ✅ Validaciones para spool existente: archivo seleccionado, extension `.SQL`, existencia en disco, SQLcl configurado y credencial destino.
- ✅ Confirmacion obligatoria antes de aplicar un spool existente contra destino.
- ✅ Ejecucion conectada a `SpoolEngine.apply_one()`; se muestra una fila de resultado usando el nombre de cuenta inferido desde `CL_Acc_Spool_<cuenta>.SQL` o el stem del archivo.
- ✅ i18n EN/ES actualizado para modo, selector, validaciones, confirmacion y resumen.
- ✅ Verificacion: `python -m compileall src` OK; smoke import/UI del modo Apply Existing OK; smoke de `SpoolEngine.apply_one()` con runner falso OK.

## 2026-05-12

### Fase 4 — inject FROM/TO con selección por cuenta 🚧
- ✅ `.gitignore`: agregado `to-do.md` como nota local no versionada.
- ✅ `src/ui/spools_view.py`: agregada DB destino (`Destination DB` / `DB destino`) bajo la DB origen. Source sigue mostrando todos los ambientes disponibles para permitir extracciones flexibles; Destination queda limitado a QA/BUP QA/DEV para evitar aplicar accidentalmente sobre PROD.
- ✅ Lista de cuentas rediseñada en dos columnas verticales: `Extract` e `Inject`. `Extract` muestra todas las cuentas que se van a descargar; `Inject` muestra solo las que además se inyectarán. El `x` en `Extract` borra la cuenta completa (también de inject); el `x` en `Inject` solo la quita de inject y la deja en Extract.
- ✅ Botón `Open spools folder` movido al bloque de acciones junto al botón principal, para que esté siempre visible antes/durante/después de correr un proceso.
- ✅ CTA principal: muestra `Apply` cuando hay cuentas en la columna Inject; si no hay cuentas para inject, muestra `Extract`.
- ✅ Flujo de ejecución: primero extrae todas las cuentas desde FROM con máximo 3 workers; luego inyecta solo las cuentas seleccionadas y extraídas correctamente en TO, también con máximo 3 workers. Si no hay ninguna cuenta marcada para inject, corre como extract-only.
- ✅ Confirmación obligatoria antes de inject: muestra destino y cuentas seleccionadas. Se bloquea source == destination.
- ✅ `src/spools_accounts/spool_engine.py`: agregado `apply_one()` / `apply_many()` para ejecutar spools existentes contra destino. Igual que con templates, crea una copia temporal con `exit;` agregado si falta, sin modificar el `.SQL` generado.
- ✅ i18n EN/ES actualizado para labels FROM/TO, estados `extracting/injecting`, resumen extract+inject y validaciones.
- ✅ Verificación: `python -m compileall src` OK; smoke test con runner falso OK para extract+apply de 7 cuentas con `max_active=3`; smoke import de `ui.spools_view` OK.
- ⚠️ Pendiente: prueba real del inject contra ambiente QA/DEV con 1 cuenta antes de usar batch grande.

### Fase 3 — validación real + paralelismo de descarga
- ✅ Verificación usuario: descarga real de cuentas desde ambientes DB funcionando; probada con 3 cuentas y spools generados correctamente.
- 🔧 Decisión: los batches de cuentas corren con máximo 3 ejecuciones paralelas. Si el usuario entrega 1 o 2 cuentas, se usan 1 o 2 workers; si entrega 10/100, se procesan de a 3 hasta terminar. El mismo patrón se reutilizará para upload en Fase 4.
- ✅ `src/spools_accounts/spool_engine.py`: agregado `MAX_PARALLEL_ACCOUNTS = 3`, helper `worker_count_for()` y `extract_many(..., max_workers=3)` con `ThreadPoolExecutor`. Preserva el orden de resultados original y mantiene error por cuenta sin abortar el batch.
- ✅ `src/ui/spools_view.py`: la pantalla Spools ahora ejecuta el batch vía `extract_many()` paralelo y cuenta progreso por cuentas realmente finalizadas, no por índice secuencial, para soportar resultados fuera de orden.
- ✅ Verificación: `python -m compileall src` OK; smoke test con runner falso procesó 7 cuentas con `max_active=3`, resultados OK y orden preservado.

### Fase 2 — arranque: catálogo de DBs + México al MVP
- ⚠️ Diff en `tnsnames.ora`: el usuario cargó credencial real para `MX_PROD_OCI` (formato `<USER>/<PASS>@MX_PROD_OCI`, valores omitidos a propósito) y eliminó el bloque duplicado de SCHEMA ID-PASS.
- 🔧 Decisión: **México entra al MVP** (antes era post-MVP en Fase 7). Razón: el usuario ya tiene credencial real para MX prod y quiere soporte completo.
- ✅ `src/core/config.py`: agregado `"mexico"` a `CRED_COUNTRIES` y a `DEFAULTS["credentials"]`.
- ✅ `src/core/credentials.py`: `TNS_TO_COUNTRY` ahora mapea `MEXICO_QA_OCI`/`MX_PROD_OCI`/`MX_PROD_OCI_DR` a `"mexico"` (antes `None`). `_infer_country` retorna `"mexico"` para substrings `MEXICO`, `_MX_`, `MX_*`, `MEXICO*`. `PROD_TNS_OVERRIDES` ya tenía los TNS MX, así que `shared_prod` se infiere correcto.
- ✅ `src/ui/settings_view.py`: agregado `("mexico", "Mexico")` al selector `COUNTRIES`.
- ✅ `src/paths.py`: `ensure_dirs()` ahora crea `SPOOLS_OUT_DIR/Mexico/`.
- ✅ Creado `src/core/databases.py` con catálogo estático para Chile/Perú/Colombia/México siguiendo §4 del plan. Cada entry tiene `id` (TNS exacto), `label`, y al consultar se enriquece con `env`/`country`. Total: 20 DBs (Chile 7, Perú 6, Colombia 4, México 3). Helpers: `countries()`, `envs_for()`, `databases_for()`, `find_db()`, `all_dbs()`, `cred_bucket_for_env()`. `ENV_TO_BUCKET` mapea `prod/qa/dev/bup_qa/bup_prod` a los buckets de credentials (`shared_prod/user_qa/...`).
- 🔧 Decisión: en el catálogo `bup_qa` y `bup_prod` son envs de primer nivel separados de `qa`/`prod` (no agrupados como sub-entries de qa/prod). Razón: las DBs BUP usan credenciales distintas a las de la DB principal, así que tratarlos como bucket separado simplifica el spool engine de Fase 3.
- ✅ Actualizado `agent.md` y `implementation_plan.md` (§4 catálogo con México, §14 Fase 7 ya no lista a México como pendiente).
- ✅ Verificación: `python -m compileall src` OK; smoke import de `core.databases` con `countries()`, `envs_for('mexico')`, `find_db('mx_prod_oci')`, `all_dbs()` (20) OK; sanity check `ENV_TO_BUCKET.values() ⊂ CRED_BUCKETS` OK; parser de credenciales sobre las tres líneas MX del tnsnames → 2 parsed (`MX_PROD_OCI` shared_prod, `MEXICO_QA_OCI` user_qa) y 1 placeholder skipeado, como debe.
- ⚠️ Pendiente para Fase 3: no existe `spools/CL_ACCOUNT_SPOOL_MEXICO.sql`. México queda registrado en credentials + catálogo + UI pero el flujo de spool de Fase 3 no podrá generar archivos para México hasta que el usuario aporte ese template.

### Rediseño UI Settings → Credentials (tiles + popup por país)
- ⚠️ Hallazgo: aunque añadimos México al MVP, la lista mostraba 0 credenciales porque la importación inicial corrió el 2026-05-05 cuando `TNS_TO_COUNTRY[MEXICO_*]=None` hacía que el parser las descartara.
- ✅ Re-importadas las 2 credenciales MX del `tnsnames.ora` local al `config.json`: `DPAVEZV @ MX_PROD_OCI` (shared_prod) y `dpavezv[FXBFMXPR] @ MEXICO_QA_OCI` (user_qa). Totales por país: Chile 6, Perú 5, Colombia 5, México 2 → 18.
- 🔧 Decisión UX: la sección "Saved credentials" del tab Credentials pasa a ser un **grid 2×2 de tiles** (uno por país, con contador), en vez del listado plano. Click en un tile abre un popup con las credenciales de ese país. Razón: con 18 creds el listado plano se hacía denso; los tiles dan una primera vista sintética y dejan el detalle en una vista dedicada.
- ✅ Creado `CountryCredentialsDialog` (CTkToplevel) que renderiza las credenciales del país agrupadas por env (PROD/QA/DEV/BUP QA/BUP PROD), con botones [Editar]/[Borrar] por fila. Solo aparecen los envs con creds. El popup se refresca tras editar/borrar y dispara un callback para refrescar el conteo de la tile padre.
- ✅ Creado `CredentialEditDialog` (CTkToplevel) con campos prellenados (país, env, user[schema], password descifrado con DPAPI, TNS). Lógica de save: siempre borra la credencial en la ubicación original (country/db/login) y la re-inserta con los nuevos valores → cubre uniformemente cambios de país, bucket, TNS, login o password sin necesitar un `update_credential` separado.
- ✅ Botones [Editar]/[Borrar] con color sólido (azul `#1F6FEB`/`#1A5BBF`, rojo `#D9534F`/`#A8322C`, texto blanco) — el estilo transparente anterior era casi invisible sobre el fondo del popup.
- ⚠️ Bug encontrado y corregido: el centrado de los popups quedaba descentrado a la derecha en monitores con DPI scaling > 100%. Causa raíz: `CTkToplevel.geometry(WxH+X+Y)` aplica scaling a W/H pero NO al offset X/Y, así que `(sw - W) // 2` mal-calculaba el centro usando W lógico contra `winfo_screenwidth()` físico. **Fix:** centrar diferido con `self.after(50, ...)` y medir con `winfo_width()` *después* de que la ventana esté renderizada — ahí ya devuelve píxeles físicos reales y el cálculo cuadra.
- 🔧 Decisión: agregadas i18n keys para `country_count_one/many`, `country_dialog_title/hint`, `edit.title/save/cancel/delete_confirm`. Etiquetas de env en la jerarquía (PROD/QA/DEV/BUP QA/BUP PROD) quedan hardcoded en `ENV_DISPLAY` por ser términos técnicos del mundo Oracle.
- ✅ Eliminado método huérfano `_delete_credential` en `SettingsView` (la lógica de delete vive ahora dentro del CountryCredentialsDialog).
- ✅ Verificación: `python -m compileall src` OK; smoke import de `ui.settings_view` con `CountryCredentialsDialog`/`CredentialEditDialog`/`COUNTRIES` OK; lanzada manualmente, tiles muestran 6/5/5/2, popup abre + agrupa + edita + borra correctamente, popup centrado en pantalla.

### Próximos pasos restantes en Fase 2
- 🔧 Decisión: no depender de una ruta fija para SQLcl. `install.bat` debe priorizar PATH y luego tratar rutas comunes solo como best-effort; cualquier instalación en otra carpeta se cubre con ingreso manual/Browse. La ruta nested `sqlcl\sqlcl\bin\` puede agregarse como conveniencia, pero no es requisito bloqueante.

### Fase 3 — Spools view (EXTRACT_ONLY) 🚧
- 🔧 Decisión: México queda fuera del dropdown de Spools por ahora (no hay `CL_ACCOUNT_SPOOL_MEXICO.sql`). Sigue apareciendo en Settings (tile + credenciales). `has_template(country)` filtra automáticamente.
- 🔧 Decisión: dropdown de Source DB **muestra todas las envs** (PROD / BUP PROD / QA / BUP QA / DEV), no solo PROD. Razón: el usuario puede querer extraer desde QA o DEV (ej. mover QA1 → QA2). El plan §4 decía "solo prod" pero la realidad operativa es más flexible. Las opciones están etiquetadas con env tag para que no haya confusión: `QA · Peru QA 19c · PERU_QA_OCI_19C`.
- 🔧 Decisión UI: input de cuentas pasa de textarea a **entry single-line + botón "+ Add" + lista dinámica** con botones [×] rojos por fila. Razón pedida por el usuario: menos propenso a errores de tipeo, deja la lista a la vista y confirma cada cuenta al agregarla (regex `^[A-Za-z0-9_-]{3,40}$`). Enter en el entry también agrega.
- ✅ Templates: creada `spools_sql/` con `CL_ACCOUNT_SPOOL_<COUNTRY>.sql.tmpl` para Chile/Peru/Colombia, derivados de los `*2.sql` (los que usan `&1` posicional, no los interactivos con `Accept PROMPT`). Replace literal: `C:\Users\Diego Pavez\Desktop\sqlcl\spools\spools_files\Accounts` → `{{SPOOL_OUT_DIR}}`. Los originales en `spools/` quedan intactos como referencia.
- ✅ `spools_accounts/sqlcl.py`: extendido con `SqlclRunner.run_script(connection, script_path, args, timeout)`. Refactor para que ambos métodos compartan `_invoke()`.
- ✅ Creado `spools_accounts/spool_engine.py`:
  - `SpoolStatus` enum (PENDING/RUNNING/OK/ERROR), `AccountResult` dataclass.
  - `parse_accounts(text)` y `is_valid_account(s)` para validación.
  - `SpoolEngine.extract_one(country, account, connection, on_status)` y `extract_many()`.
  - Render: lee `.sql.tmpl`, sustituye `{{SPOOL_OUT_DIR}}` por `paths.SPOOLS_OUT_DIR` real, escribe a `%TEMP%\oracle_tasks_<country>_<uuid>.sql`, ejecuta y borra el temp.
  - Pre-clean: elimina spool viejo antes de correr, así que la existencia del `.SQL` post-éxito significa write fresco (no archivo viejo huérfano).
- ⚠️ Bug + fix: los `.sql` originales **no tienen `exit;` al final** (terminan en `SPOOL OFF`). Cuando SQLcl los ejecuta vía `@<file>`, después de procesar todo se queda al prompt esperando input, así que `subprocess.run` no retorna hasta el timeout. Síntoma: la primera cuenta se quedaba en ⟳ ~3 min y recién después marcaba ERROR por timeout. **Fix:** `_render_template` agrega `exit;` al final si el template no termina con uno. No modifica el archivo original (per regla de `agent.md`), solo el .sql temporal.
- 🔧 Decisión timeout: 1800s (30 min) por cuenta. Razón: el usuario reportó que algunas cuentas legítimas tardan 5–10 min en extraerse (red lenta, cuentas grandes). 180s del MVP era demasiado agresivo. `subprocess.run(timeout=N)` es wallclock total, no idle, así que hay que cubrir el peor caso real.
- ✅ Creado widget `AccountStatusRow` en `ui/widgets.py`: glyph + cuenta + mensaje. Estados: `…` gris (pending), `⟳` azul (running), `✓` verde (OK), `✗` rojo (error).
- ✅ Creado `ui/spools_view.py`:
  - Header con Back + título.
  - Form: País dropdown + Source DB dropdown (filtrado por país + ordenado por env).
  - Account number entry + [+ Add] button + Enter binding.
  - `pending_frame` scrollable con la lista de cuentas agregadas (cada una con su [×] rojo).
  - [Extract spools] button + summary label.
  - `results_frame` scrollable con un `AccountStatusRow` por cuenta procesada.
  - [Open spools folder] al fondo (usa `os.startfile` para abrir Explorer).
  - Threading: `_do_run` corre en daemon thread; callbacks per-cuenta se marshalean al UI thread con `app.root.after(0, ...)`. Continúa en error (no aborta el batch).
- ✅ Wire: `app.show_view("spools")`, `HomeView._on_spools` reemplazado para abrir la view real (antes solo mostraba un messagebox de "coming soon").
- ✅ i18n: agregadas keys `spools.*` (country, source_db, account_number, add_account, added_accounts, run, running, summary_ok, summary_mixed, open_folder, no_template, no_creds, no_sqlcl, no_pending, invalid_account, duplicate_account, invalid_db) en EN/ES.
- ✅ Verificación: `python -m compileall src` OK; smoke import de `spools_view`, `spool_engine`, `AccountStatusRow` OK; templates detectados para chile/peru/colombia (mexico=False); `parse_accounts` clasifica correctamente válidos e inválidos; output path se construye en `%LOCALAPPDATA%\OracleTasksChile\spools_out\<Country>\CL_Acc_Spool_<account>.SQL`.

### Próximos pasos
- Validar end-to-end con cuenta real en QA (extracción exitosa → archivo en `spools_out/<Country>/`).
- Commit + push de Fase 3 en `feat/spools-view`.
- Fase 4: dropdown `destination` + modo EXTRACT_AND_APPLY + diálogo de confirmación obligatorio antes de aplicar en QA/DEV.

### SqlclRunner + Test connection
- ✅ Creado `src/spools_accounts/sqlcl.py` con `SqlclRunner.run_query(connection, sql)` → `RunResult(exit_code, stdout, stderr)`. Invoca `sql.exe -S -L <conn>` y alimenta SQL vía stdin (evita problemas de quoting en Windows). `-S` silencia banner, `-L` falla rápido en error de login. `CREATE_NO_WINDOW` evita parpadeo de consola. Timeout 30s por default.
- ✅ Settings → General → sección "Test connection": dropdown con todas las credenciales guardadas (`<País> · <DB> · <login>`) + botón Test que corre `select 1 from dual` en thread separado. Status label muestra OK (verde) o `Falló (exit N)` + última línea del error (rojo). UI no se congela.
- 🔧 Decisión: el dropdown es plano por credencial (no por DB) — así se puede probar específicamente Colombia QA con su segundo login (`prov_oracle_nivel2[FUNREGCOQA]`) sin tener que adivinar cuál se selecciona automáticamente.

### Refactor a estructura por dominio
- 🔧 Decisión: `core/` se elimina y se divide por dominio antes de que Fase 3 agregue más archivos. Razones: dejar `core/` como grab-bag empezaba a confundir; cada nueva tarea (spools_engine, updater) iba a hacerlo peor.
- ✅ Nueva estructura `src/`:
  - `settings/` → `config.py`, `credentials.py` (dominio Settings)
  - `spools_accounts/` → `databases.py`, `sqlcl.py`, `sqlcl_locator.py` (dominio Spools/Accounts)
  - `infra/` → `logger.py`, `updater.py` (cross-cutting)
  - `ui/`, `paths.py`, `i18n.py`, `version.py`, `main.py` → sin cambios
- ✅ Movidos 5 archivos con `git mv` (renames detectados por git, history preservada); `sqlcl.py` quedó como untracked porque era nuevo del mismo día — se agrega con `git add` en el commit.
- ✅ Imports actualizados en `main.py`, `ui/app.py`, `ui/settings_view.py`. `settings/credentials.py` mantiene `from .config import ...` (relative import sigue válido al estar en el mismo paquete).
- ✅ Verificación: `compileall src` OK + smoke import de cada paquete nuevo OK.

### Versión 0.0.1 + auto-updater
- 🔧 Decisión: mirroreamos el patrón de la app vpn (`c:\...\vpn\`): `assets/version.json` para metadata + `src/version.py` como fuente de verdad que el updater consulta vía `git fetch origin main` y `git show origin/main:src/version.py`. **No usamos** `raw.githubusercontent.com` (idea original del plan §11) porque vpn ya probó que git es más confiable detrás del firewall corporativo.
- ✅ Movido `version.json` de la raíz a `assets/version.json` (mirror de vpn) con `git mv`.
- ✅ Bajada la versión a `0.0.1` en `src/version.py` y `assets/version.json`. Razón explícita del usuario: arrancar limpio en `0.0.1` antes de empezar releases reales.
- ✅ Creado `src/infra/updater.py` clon del `_check_for_update` de vpn: thread background al arrancar, `git fetch origin main` + `git show origin/main:src/version.py`, compara tuplas y llama callback solo si remote > local. Silencioso en todos los failure modes (sin git, sin red, repo no-git).
- ✅ Conectado `infra.updater.check_for_update` al startup de `OracleTasksApp.run()`. El callback `_on_remote_version` marshala al UI thread vía `root.after(0, ...)` antes de mostrar el banner.
- ✅ `_on_update_click` ahora lanza `update.bat` con `subprocess.Popen(["cmd","/c","start","",updater,pythonw], creationflags=CREATE_NEW_CONSOLE)` y cierra la app — patrón exacto del vpn.
- ⚠️ Bug + fix: el banner aparecía abajo en vez de arriba. Causa: `container.pack(fill="both", expand=True)` ocupaba toda la pantalla antes de que `banner.show()` corriera, así que el banner caía debajo. **Fix:** `UpdateBanner.show()` ahora acepta `before=widget`; `show_update_banner()` pasa `before=self.container` para forzar el orden.
- ✅ i18n: agregada key `update.available_v` con interpolación de `{version}` ("⬆ Update available v{version} — click to install" / "⬆ Actualización v{version} disponible — haz click para instalar").
- ⚠️ Estado transitorio: al correr la app con local=0.0.1, el banner muestra "Update available v0.1.0" porque `origin/main` aún apunta al commit inicial `570ce8b` que tenía 0.1.0. Se resuelve mergeando esta rama a main: una vez `main` esté en 0.0.1, local == remote y el banner desaparece.

---

## 2026-05-05

### Ajuste UI Settings
- ✅ Settings → General: etiqueta `SQLcl path` actualizada para aclarar que debe apuntar al `.exe` (`...\sqlcl\bin\sql.EXE`). Verificado con `python -m compileall src\i18n.py`.

### Cambio schema credenciales
- ✅ Cambiado `src/core/config.py` a schema v3: `pais -> DB/TNS -> login -> credencial`; `bucket` queda como metadata dentro de cada credencial.
- 🔧 Decisión: se agrega nivel `login` (`USER` o `USER[SCHEMA]`) bajo cada DB porque Colombia QA tiene dos credenciales distintas para `COL_QA_INT_OCI`; así no se pierde ninguna.
- ✅ Migración compatible desde schemas legacy v1 (`bucket -> pais`) y v2 (`pais -> DB`) hacia v3.
- ✅ Actualizado `src/ui/settings_view.py` para guardar/listar/borrar credenciales por país + DB + login.
- ✅ Actualizado `src/core/credentials.py`: el parser ahora detecta `user/pass@DB` o `user[schema]/pass@DB` embebido en líneas comentadas de `tnsnames.ora` y omite placeholders `username/password@...`.
- ✅ Importadas al config real (`%APPDATA%\OracleTasksChile\config.json`) 16 credenciales únicas desde el `tnsnames.ora` local, todas con `password_enc` DPAPI y 0 campos `password` en claro. Conteo: Chile 6, Colombia 5, Perú 5.
- ✅ Actualizado `implementation_plan.md` sección Credenciales para reflejar el schema v3.
- ✅ Actualizado `.gitignore`: `sqlcl-latest.zip` y `tmp*/` para evitar commits de artefactos locales.
- ✅ Verificación: `python -m compileall src`, smoke import de `core.config`, `core.credentials`, `ui.settings_view`, prueba de migración en memoria v1/v2/v3.
- ⚠️ Nota: una prueba fallida de `tempfile` dejó `tmpp_d76bvj` inaccesible dentro del workspace; se intentó borrar con `Remove-Item` normal y elevado, pero Windows devolvió `Access denied`. Quedó cubierto por `.gitignore` con `tmp*/`.

## 2026-04-30

### Setup inicial
- ✅ Implementation plan v1 escrito (`implementation_plan.md`)
- ✅ Sección §0 agregada al plan: regla de tracking en `progress.md` para todas las sesiones
- ✅ Repo creado por el usuario: `https://github.com/dpv20/oracle_tasks`
- 🔧 SQLcl: estrategia de detección en cascada en lugar de descarga forzada (PATH → rutas comunes → menú interactivo en install.bat con opciones manual/download/skip). Razón: el usuario actual ya tiene SQLcl en PATH (`sql credenciales` funciona en cualquier `cmd`), no tiene sentido descargar 95MB redundantes.
- 🔧 Estructura: el repo se construye sobre el folder de trabajo `Spool_maker/` actual. Los archivos de referencia (`spools/`, `tnsnames.ora`, `cuenta_prod_a_QA.txt`, `pantalla_*.png`) se mantienen como material de partida pero no son parte de la app — se decidirá durante Fase 1 si van a `reference/` o se .gitignore.

### Fase 1 — Esqueleto + Settings + Home ✅ COMPLETADA
- ✅ Creado `agent.md` — prompt de orientación para sesiones futuras (apunta a `implementation_plan.md` y `progress.md` como fuentes de verdad). Editado por el usuario para hacerlo agnóstico al tipo de agente.
- ✅ Creado `README.md` (instrucciones para usuarios finales y devs)
- ✅ Creado `requirements.txt` (customtkinter, Pillow, pywin32, requests)
- ✅ Creado `version.json` (v0.1.0, apunta a release del repo `dpv20/oracle_tasks`)
- ✅ Creado `.gitignore` (excluye config local, logs, sqlcl/, spools_out/, material de referencia)
- ⚠️ Bug propio: en el primer Write de `.gitignore` accidentalmente escribí el contenido de README.md. Corregido en segunda pasada.
- ✅ Creado `install.bat` con detección de SQLcl en cascada (PATH → rutas comunes → menú interactivo manual/download/skip), generación de `.ico` desde `.jpg` con Pillow, persistencia de `sqlcl_path` en config.json, AppUserModelID via PowerShell helper.
- ✅ Creado `update.bat` (idéntico al de `vpn` salvo paths y window title) y `uninstall.bat` (con confirmación "YES").
- ✅ Creado `tools/set_aumid.ps1` con interop COM C# inline para escribir PKEY_AppUserModel_ID en el `.lnk`.
- ✅ Movido `icono.jpg` a `assets/` y generado `assets/icono.ico` multi-resolución (16/32/48/64/128/256).
- ✅ Creado `src/main.py` (DPI awareness, AUMID, single-instance mutex con flag file `show.flag`).
- ✅ Creado `src/paths.py` (`REPO_ROOT`, `CONFIG_DIR`, `DATA_DIR`, `SPOOLS_OUT_DIR`, etc.) y `ensure_dirs()`.
- ✅ Creado `src/version.py` (v0.1.0).
- ✅ Creado `src/i18n.py` con dict EN/ES (~50 strings) y `t(key, **kwargs)` con fallback a EN.
- ✅ Creado `src/core/logger.py` (RotatingFileHandler en `DATA_DIR/app.log`, 2MB × 3 backups).
- ✅ Creado `src/core/config.py` con DPAPI encrypt/decrypt (vía `win32crypt`), schema versionado, deep-merge con defaults, helpers `set_credential` / `get_credential` / `delete_credential`.
- ✅ Creado `src/core/credentials.py` con regex parser para `user[schema]/pass@DB`, inferencia automática de país (chile/peru/colombia) y bucket (shared_prod/user_qa/user_dev/user_bup_qa/user_bup_prod) desde el TNS name. Override list para TNS de prod que no contienen "PROD" en el nombre (FXBFCL_19C_PROD_OCI, BFCO_POCISANTIAGO, BUP_CL_2024, etc.).
- ✅ Creado `src/ui/app.py` (router de views: `home`/`settings`, `apply_language`/`apply_theme` con rebuild de views).
- ✅ Creado `src/ui/widgets.py` (`UpdateBanner`, `IconButton`, `SectionLabel`).
- ✅ Creado `src/ui/home_view.py` (toolbar con botón "Spools / Cuentas" + 3 placeholders, botón Settings abajo-izquierda, credit + version abajo-derecha).
- ✅ Creado `src/ui/settings_view.py` con 3 tabs: Credentials (sub-tabs Paste/Form + lista de credenciales guardadas con delete), General (radio idioma EN/ES, radio tema claro/oscuro, paths SQLcl y spools_out con browse), About (versión + repo + contacto).
- ✅ Smoke test: `python src/main.py` sin mainloop → construye `OracleTasksApp`, switchea entre views home↔settings, no errores. Imports de todos los módulos OK.
- 🔧 Decisión: en el `set_credential` del paste-mode, la inferencia de bucket desde TNS gana; en el form-mode el usuario elige país/bucket explícitamente y se ignora la inferencia. Razón: el form es el modo "preciso", el paste es el modo "rápido".
- 🔧 Decisión: switch de idioma rebuild-ea todas las views inmediatamente (no requiere reiniciar). Switch de tema solo aplica `set_appearance_mode` sin rebuild.
- ⚠️ Pendiente para el usuario: probar manualmente el flujo completo — `python src/main.py` → Settings → pegar credenciales reales → cambiar idioma/tema → cerrar → reabrir → verificar que todo se persistió correctamente. La app abre bien programáticamente pero el test interactivo lo tiene que hacer él.

### Ajustes post-Fase 1 (feedback del usuario)
- ✅ About tab: agregado creador (Diego Pavez Verdi), email (diego.pavez@oracle.com), teléfono (+569 95293023). Traducciones EN/ES.
- 🔧 Decisión: removida la opción "Spools output folder" de Settings → General. La carpeta donde se guarda cada spool se decide **per-task** en la pantalla de Spools (Fase 3) cuando el usuario marque "Save spool only" o quiera guardar en carpeta custom. La carpeta default sigue siendo `%LOCALAPPDATA%\OracleTasksChile\spools_out\<Pais>\`.
- ✅ Creado `src/core/sqlcl_locator.py` (extraído como helper para usarlo también en Fase 2). Cascada: `where sql` → rutas comunes (incluye `Desktop\sqlcl\sqlcl\bin\` que es la del usuario actual con doble nesting) → None.
- ✅ Settings → General: botón "Auto-detect" que llama al locator y rellena el campo SQLcl path automáticamente.
- ✅ SQLcl detectado en este ambiente: `C:\Users\Diego Pavez\Desktop\sqlcl\sqlcl\bin\sql.EXE`. Persistido en `config.json`.
- ⚠️ Hallazgo: la instalación del usuario tiene el binario en `sqlcl\sqlcl\bin\` (carpeta nested), no en `sqlcl\bin\`. Agregada esa ruta a `COMMON_PATHS` en el locator y al `install.bat` debería actualizarse también si llegamos a re-instalar (TODO Fase 2).

### Cargado set completo de credenciales del usuario
- ✅ Cargadas 14 credenciales reales del usuario (Chile/Peru/Colombia, todos los envs).
- ⚠️ Bug en `_infer_country`: TNS como `fxbfcl_19c_prod_oci` y `BFCO_POCISANTIAGO` no contienen "CHILE"/"COL" literalmente, así que la heurística los marcaba como `country=None` y se descartaban como unparsed. **Fix:** agregada tabla `TNS_TO_COUNTRY` explícita en `credentials.py` con todos los TNS conocidos, antes del fallback heurístico. Re-parseado: 14/14 OK.
- 🔧 Limitación detectada: el schema solo permite **una credencial por (country, bucket)**. Chile y Colombia tienen 2 DBs de PROD cada uno con mismo user/pass (`PROD_OCI` + `PROD_OCI_DR`, `BFCO_POCISANTIAGO` + `BFCO_POCISAOPALO`), así que la segunda sobrescribe el `tns` de la primera. **Esto es OK** porque al ejecutar un spool, la pantalla de Spools (Fase 3) elegirá el TNS desde el catálogo de DBs y combinará con el (user, pass) de la credencial — el campo `tns` guardado queda como informativo. No requiere cambio de schema.

### Próximos pasos (Fase 2)
- Crear `src/core/databases.py` con catálogo Chile/Peru/Colombia parseado desde `tnsnames.ora`
- Crear `src/core/sqlcl.py` con `SqlclRunner` que use `sqlcl_locator.locate_sqlcl()`
- Botón "Test connection" en Settings → General que corre `select 1 from dual` contra una DB elegida
- Mantener detección de SQLcl flexible: PATH primero, rutas comunes como ayuda, y ruta manual para cualquier instalación no estándar.
