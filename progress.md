# Oracle Tasks Chile — Progress Log

Registro cronológico de avance, decisiones y problemas. Entradas más recientes arriba.

Para contexto general del proyecto leer `implementation_plan.md`.

Convención:
- ✅ completado · ⚠️ nota / problema · 🔧 decisión · 🚧 en progreso · ❌ bloqueado

---

## 2026-05-12

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
- Actualizar `install.bat` para detectar también `sqlcl\sqlcl\bin\` (instalación nested del usuario).

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
- Actualizar `install.bat` para que también busque en `sqlcl\sqlcl\bin\` (nested install)
