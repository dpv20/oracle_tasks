# Oracle Tasks Chile — Implementation Plan

App de escritorio Windows en Python para automatizar tareas Oracle del equipo. Primera tarea: generar spools de cuentas desde DBs de producción y aplicarlos en QA/DEV.

---

## 0. Tracking de progreso — `progress.md` (LEER PRIMERO)

**Regla obligatoria para toda sesión actual y futura:**

Todo lo que hacemos en este proyecto debe quedar registrado en `progress.md` (en la raíz del repo, junto a este plan). Esto permite que cualquier sesión futura (o cualquier persona que retome el trabajo) entienda el estado actual sin tener que adivinar.

### Cuándo escribir en `progress.md`
- Al **completar un paso del plan** (cualquier ítem de §14 Fases)
- Al **completar cualquier subtarea** (crear un archivo, agregar una feature, arreglar un bug)
- Cuando aparece un **problema o blocker** (con descripción de qué pasó y cómo se resolvió o qué quedó pendiente)
- Cuando se toma una **decisión técnica no trivial** que cambia o afina algo del plan
- Cuando se **descubre algo nuevo** del entorno/código que afecta cómo seguir

### Formato
Cada entrada es un bloque con fecha, fase y descripción corta. Las entradas más recientes van **arriba** (orden cronológico inverso). Ejemplo:

```markdown
## 2026-04-30

### Fase 1 — Esqueleto inicial
- ✅ Creado `install.bat` con flujo Python+Git+SQLcl en cascada (PATH → rutas comunes → menú interactivo)
- ✅ Creado `src/core/config.py` con DPAPI encrypt/decrypt
- ⚠️ Encontrado: `customtkinter` 5.2.2 tiene un bug con `CTkTabview` en Windows 11 dark mode → workaround: forzar `appearance_mode=light` al inicio en tabview, switch a dark se aplica al cerrar/reabrir el tab
- 🔧 Decisión: el AUMID quedó como `Oracle.OracleTasksChile.1` (no `dpv20.OracleTasksChile.1`) para mantener consistencia con la app `vpn`
```

Convención de íconos:
- ✅ completado
- ⚠️ problema encontrado / nota importante
- 🔧 decisión técnica
- 🚧 en progreso (tarea grande, varias sesiones)
- ❌ bloqueado / pendiente de resolver

### Cómo lo usan sesiones futuras
Al iniciar cualquier sesión nueva, leer en este orden:
1. `implementation_plan.md` (este archivo) — qué estamos construyendo y por qué
2. `progress.md` — qué se hizo, qué problemas hubo, dónde estamos parados
3. Empezar a trabajar desde la primera tarea del plan que **no** aparezca como ✅ en `progress.md`

---

## 1. Stack & dependencias

| Componente | Elección | Razón |
|---|---|---|
| Lenguaje | Python 3.12 | Disponibilidad, ecosistema, mismo que `vpn` |
| GUI | `customtkinter` | Moderno, light/dark mode nativo, sobre tkinter (sin Qt enredos) |
| Motor SQL | SQLcl (subprocess) | Compatibilidad con scripts existentes, soporta proxy auth `user[schema]/pass` |
| Empaquetado | Repo + `install.bat` (no PyInstaller) | Mismo patrón que `vpn`: install clona repo, ejecuta directo desde `.py` con `pythonw.exe` |
| Auto-update | `git pull` (`update.bat`) | Mismo patrón que `vpn` |
| Encriptación passwords | DPAPI (Windows) vía `pywin32` | User-bound, sin clave maestra que recordar |
| i18n | dict simple `en/es` en módulo `i18n.py` | No vale la pena `gettext` para 2 idiomas y ~50 strings |
| Logging | `logging` stdlib + archivo en `%LOCALAPPDATA%\OracleTasksChile\app.log` | Igual que `vpn` |

### `requirements.txt`
```
customtkinter>=5.2.2
Pillow>=10.0.0
pywin32>=306
requests>=2.31.0
```

---

## 2. Repo & estructura de instalación

**Repo GitHub:** `https://github.com/dpv20/oracle_tasks.git` (crear nuevo, público o privado — ver §13)

### Layout del repo
```
oracle_tasks/
├── README.md
├── install.bat                    ← descarga Python, Git, SQLcl, clona repo, crea acceso directo
├── update.bat                     ← git fetch + reset --hard origin/main + pip install + relanza
├── uninstall.bat
├── requirements.txt
├── .gitignore
├── version.json                   ← {"version": "1.0.0", "download_url": "..."}
├── assets/
│   ├── icono.jpg                  ← original
│   ├── icono.ico                  ← convertido para shortcut/taskbar
│   ├── icono_192.png
│   └── flags/
│       ├── chile.png  peru.png  colombia.png
├── spools_sql/                    ← scripts SQL versionados (templates)
│   ├── CL_ACCOUNT_SPOOL_CHILE.sql.tmpl
│   ├── CL_ACCOUNT_SPOOL_CHILE2.sql.tmpl
│   ├── CL_ACCOUNT_SPOOL_PERU.sql.tmpl
│   ├── CL_ACCOUNT_SPOOL_PERU2.sql.tmpl
│   ├── CL_ACCOUNT_SPOOL_COLOMBIA.sql.tmpl
│   └── CL_ACCOUNT_SPOOL_COLOMBIA2.sql.tmpl
├── tools/
│   ├── set_aumid.ps1              ← copiado de vpn (taskbar icon)
│   └── download_sqlcl.ps1         ← descarga + extracción SQLcl
└── src/
    ├── main.py                    ← entry point, single-instance guard, DPI, AUMID
    ├── version.py                 ← __version__ = "1.0.0"
    ├── ui/
    │   ├── __init__.py
    │   ├── app.py                 ← OracleTasksApp (CTk window + nav)
    │   ├── home_view.py           ← pantalla inicial: botón "Spools cuentas" + Settings
    │   ├── spools_view.py         ← pantalla principal de spools
    │   ├── settings_view.py       ← credenciales, idioma, tema, paths
    │   └── widgets.py             ← AccountStatusRow (cuenta + spinner + ✓/✗), banner update
    ├── core/
    │   ├── __init__.py
    │   ├── config.py              ← ConfigManager (lee/escribe %APPDATA%\OracleTasksChile\config.json)
    │   ├── credentials.py         ← parser de strings tipo `user[schema]/pass@db`, encrypt/decrypt
    │   ├── databases.py           ← catálogo estático Chile/Peru/Colombia → {prod: [...], qa: [...], dev: [...]}
    │   ├── sqlcl.py               ← SqlclRunner: localiza sql.exe, ejecuta scripts, captura stdout/stderr
    │   ├── spool_engine.py        ← orquestador: render template → ejecuta source → ejecuta destination
    │   ├── updater.py             ← chequeo de version.json en GitHub, banner trigger
    │   └── logger.py
    ├── i18n.py                    ← T = {"en": {...}, "es": {...}}; t(key) según config["language"]
    └── paths.py                   ← INSTALL_DIR, CONFIG_DIR, SPOOLS_OUT_DIR, SQLCL_DIR (helpers)
```

### Layout post-instalación (en máquina del usuario)
```
%LOCALAPPDATA%\OracleTasksChile\
├── app\                           ← clon del repo (auto-update vía git)
│   └── (todo el contenido del repo)
├── sqlcl\                         ← descargado por install.bat (~95MB, no en git)
│   ├── bin\sql.exe
│   └── ...
├── spools_out\                    ← spools generados, estructura: spools_out\<Pais>\CL_Acc_Spool_<n>.SQL
│   ├── Chile\
│   ├── Peru\
│   └── Colombia\
└── app.log

%APPDATA%\OracleTasksChile\
└── config.json                    ← per-user, NO en git
```

---

## 3. install.bat — flujo

Mirror del `install.bat` de `vpn`, con paso extra de SQLcl:

1. **Check Python ≥3.8** → si falta, descarga `python-3.12.4-amd64.exe` silencioso per-user
2. **Check Git** → si falta, descarga `Git-2.45.2-64-bit.exe` silencioso per-user
3. **Clona/actualiza repo** en `%LOCALAPPDATA%\OracleTasksChile\app` (`git clone --depth 1 --branch main https://github.com/dpv20/oracle_tasks.git`)
4. **Localiza SQLcl** — flujo en cascada, orden de prioridad:
   1. **Check PATH:** `where sql.exe` (caso típico del usuario actual — `sql credenciales` ya funciona en cualquier `cmd`). Si responde, usa esa ruta.
   2. **Check rutas comunes:** `%USERPROFILE%\Desktop\sqlcl\bin\sql.exe`, `C:\sqlcl\bin\sql.exe`, `%LOCALAPPDATA%\OracleTasksChile\sqlcl\bin\sql.exe`.
   3. **Si no se encontró:** dialog interactivo en el `install.bat`:
      ```
      SQLcl was not found on your system.

      Choose an option:
        [1] I already have SQLcl — let me enter the path to sql.exe
        [2] Download SQLcl from Oracle (~95MB) — recommended
        [3] Skip for now (configure later in Settings)
      ```
      - Opción **[1]** → prompt path → valida que `sql.exe` exista → guarda en `config.json["sqlcl_path"]`
      - Opción **[2]** → descarga `https://download.oracle.com/otn_software/java/sqldeveloper/sqlcl-latest.zip` → extrae a `%LOCALAPPDATA%\OracleTasksChile\sqlcl\` → guarda esa ruta en `config.json["sqlcl_path"]`. Si la descarga falla, vuelve a mostrar el menú.
      - Opción **[3]** → instala la app pero al primer Run le pedirá lo mismo desde Settings.
   4. La ruta resuelta se persiste en `config.json["sqlcl_path"]` para no repreguntar en updates.
5. **`pip install -r requirements.txt`**
6. **Crea acceso directo en escritorio** apuntando a `pythonw.exe "...\app\src\main.py"`, con `icono.ico`
7. **Setea AppUserModelID** del shortcut para que el taskbar muestre el ícono
8. **Lanza la app**

> **Settings → General → SQLcl path** replica la misma cascada: campo de ruta editable, botón "Browse…", botón "Download from Oracle", botón "Auto-detect" (re-corre los pasos 1–2). Esto cubre el caso de un usuario que instaló la app sin SQLcl y lo agrega después.

`update.bat` es idéntico al de `vpn`: `git fetch + reset --hard origin/main + pip install -r requirements.txt + relanzar`.

---

## 4. Catálogo de databases (`core/databases.py`)

Estático, parseado desde `tnsnames.ora` y volcado a Python como dict. Cada DB tiene `country`, `env` (`prod` | `qa` | `dev` | `bup_qa` | `bup_prod`), `tns_name` y `description`.

```python
DATABASES = {
    "chile": {
        "prod":     [{"id": "fxbfcl_19c_prod_oci",  "label": "Chile PROD (OCI)"}, ...],
        "qa":       [{"id": "CHILE_QA_19C",         "label": "Chile QA 19c"},     ...],
        "dev":      [{"id": "CHILE_DEV",            "label": "Chile DEV"}],
        "bup_qa":   [{"id": "BUP_QA_CL",            "label": "Chile BUP QA"}],
        "bup_prod": [{"id": "BUP_CL_2024",          "label": "Chile BUP PROD"}],
    },
    "peru":     { ... },
    "colombia": { ... },
    "mexico":   { ... },
}
```

`bup_qa`/`bup_prod` se separan de `qa`/`prod` porque corresponden a DBs distintas (BUP) con su propia credencial; el spool engine las trata como buckets separados.

UI:
- Dropdown **País** filtra los demás dropdowns
- Dropdown **Source** = solo `prod` del país elegido
- Dropdown **Destination** = `qa` + `dev` + opción especial `__SAVE_ONLY__` ("Save spool only — don't apply")

---

## 5. Credenciales (`core/credentials.py` + `config.json`)

### Schema de `config.json`
```json
{
  "version": 3,
  "language": "en",
  "theme": "light",
  "sqlcl_path": "",
  "spools_output_dir": "",
  "credentials": {
    "chile": {
      "CHILE_QA_19C": {
        "DPAVEZV[FXBFCLPR]": {
          "user": "dpavezv",
          "schema": "fxbfclpr",
          "password_enc": "<dpapi_blob>",
          "tns": "CHILE_QA_19C",
          "bucket": "user_qa"
        }
      },
      "FXBFCL_19C_PROD_OCI": { "...": "..." }
    },
    "peru": { "...": "..." },
    "colombia": {
      "COL_QA_INT_OCI": {
        "DPAVEZV[FUNREGCOQA]": { "...": "..." },
        "PROV_ORACLE_NIVEL2[FUNREGCOQA]": { "...": "..." }
      }
    }
  }
}
```

### Settings — entrada de credenciales
La jerarquia es **pais -> DB/TNS -> login -> credencial**. El `bucket`
(`shared_prod`, `user_qa`, `user_dev`, `user_bup_qa`, `user_bup_prod`) queda
como metadata dentro de cada credencial para no perder multiples DBs por pais
ni multiples usuarios sobre una misma DB.

**Dos modos en la misma pantalla, tabs:**

**Tab "Paste"** (rápido — el preferido)
- Textarea grande
- Cada línea formato libre: `user/pass@DB` o `user[schema]/pass@DB`
- Botón "Parse & save" → regex search de `user/pass@DB` o `user[schema]/pass@DB`
- Para cada match: detecta el país por nombre TNS (`*CHILE*` → chile, etc.) y el env (`*QA*` → qa, etc.)
- Confirma en tabla "estas credenciales se guardarán como: chile-qa, peru-prod, …" antes de aplicar
- Parser actual detecta credenciales incluso si vienen embebidas en lineas comentadas de `tnsnames.ora`
- Passwords encriptados con DPAPI antes de tocar disco

**Tab "Form"** (ergonómico)
- Selector país + env → 4 campos: `username`, `proxy_schema` (opcional), `password`, `tns_name`
- Mismo encrypt + save

### Parser de credenciales
```python
import re
CRED_RE = re.compile(r"^([A-Za-z0-9_]+)(?:\[([A-Za-z0-9_]+)\])?/([^@\s]+)@([A-Za-z0-9_]+)$")

def parse(line: str) -> Credential | None:
    m = CRED_RE.match(line.strip())
    if not m: return None
    user, schema, pwd, tns = m.groups()
    return Credential(user, schema, pwd, tns)

def to_sqlcl_arg(c: Credential) -> str:
    proxy = f"[{c.schema}]" if c.schema else ""
    return f"{c.user}{proxy}/{c.password}@{c.tns}"
```

---

## 6. Templates de scripts SQL

Los `.sql` actuales tienen ruta hardcoded:
```
spool "C:\Users\Diego Pavez\Desktop\sqlcl\spools\spools_files\Accounts\Chile\CL_Acc_Spool_&ACC_NO..SQL"
```

**Solución:** los `.sql` originales se versionan con un placeholder, y la app renderea una copia temporal antes de ejecutar.

`spools_sql/CL_ACCOUNT_SPOOL_CHILE2.sql.tmpl`:
```sql
spool "{{SPOOL_OUT_DIR}}\Chile\CL_Acc_Spool_&1..SQL"
```

`SqlclRunner.run_script()`:
1. Lee `.sql.tmpl`
2. Reemplaza `{{SPOOL_OUT_DIR}}` por `paths.SPOOLS_OUT_DIR` (resuelto a `%LOCALAPPDATA%\OracleTasksChile\spools_out`)
3. Escribe a `%TEMP%\oracle_tasks_<uuid>.sql`
4. Ejecuta `sql.exe <cred> @<tempfile> <account>`
5. Borra el temp al finalizar

Esto preserva los scripts originales **sin modificarlos** y permite que `spools_output_dir` sea configurable en Settings.

---

## 7. Motor de ejecución (`core/sqlcl.py` + `core/spool_engine.py`)

### `SqlclRunner`
```python
class SqlclRunner:
    def __init__(self, sqlcl_exe: Path):
        self.exe = sqlcl_exe

    def run_script(
        self,
        connection: str,           # "user[schema]/pass@DB"
        script_path: Path,
        args: list[str],
        on_stdout: Callable[[str], None] = None,
    ) -> RunResult:
        proc = subprocess.Popen(
            [str(self.exe), "-S", connection, f"@{script_path}", *args],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        for line in proc.stdout:
            if on_stdout: on_stdout(line.rstrip())
        proc.wait()
        return RunResult(exit_code=proc.returncode, ...)
```

`-S` = silent (sin banner). Streaming de stdout permite mostrar progreso en vivo.

### `SpoolEngine`
Orquesta los 3 modos:

```python
class SpoolMode(Enum):
    EXTRACT_ONLY = "extract_only"        # solo source → genera spool, no aplica
    EXTRACT_AND_APPLY = "extract_apply"  # source → genera spool → destination → ejecuta
    APPLY_EXISTING = "apply_existing"    # solo destination → ejecuta archivo .SQL existente

def process_account(
    self,
    country: str,
    account: str,
    mode: SpoolMode,
    source_db: str | None,       # None si APPLY_EXISTING
    destination_db: str | None,  # None si EXTRACT_ONLY
    existing_spool_path: Path | None,  # solo si APPLY_EXISTING
    on_status: Callable[[AccountStatus], None],
) -> AccountResult:
    on_status(RUNNING_SOURCE)
    if mode != APPLY_EXISTING:
        # render template, run on source
        spool_path = ...
    else:
        spool_path = existing_spool_path
    on_status(RUNNING_DEST)
    if mode != EXTRACT_ONLY:
        # run spool on destination
        ...
    on_status(OK or ERROR)
```

### Batch
- `process_accounts(accounts: list[str], ...)` itera, **continúa en error** (recomendación E)
- Cada cuenta genera un `AccountResult` que se renderiza en su `AccountStatusRow` en la UI
- Al final, summary: "5/7 OK, 2 errors — see log"

---

## 8. UI — pantallas y flujos

### Home view
Mockup matching:
- Title bar: "Oracle Tasks Chile"
- Top tabs/buttons: **[Spools cuentas]** (otros placeholders gris para futuro)
- Content area: vacío (placeholder "Select a task above")
- Bottom-left: **[Settings]**
- Bottom-right: "Created by: Diego Pavez Verdi · Contact: …" (texto pequeño)
- **Banner superior** (si hay update disponible): `🔔 Update available → click to install` → click llama `update.bat`

### Spools view
- **País** dropdown (Chile/Peru/Colombia)
- **Mode** radio buttons:
  - `( ) Extract from production only (save spool)`
  - `(•) Extract & apply to QA/DEV`
  - `( ) Apply existing spool file`
- **Source DB** dropdown (filtrado a `prod` del país; deshabilitado si mode=APPLY_EXISTING)
- **Destination DB** dropdown (filtrado a `qa`+`dev` del país; deshabilitado si mode=EXTRACT_ONLY)
- **Si mode=APPLY_EXISTING:** botón "Browse spool file…" en lugar de Source
- **Accounts** textarea multilínea (placeholder: `One account number per line`)
- **Save spool checkbox** (default ON; si OFF y mode=EXTRACT_ONLY, prompt al usuario por carpeta destino al final)
- **[Open spools folder]** button → `os.startfile(SPOOLS_OUT_DIR / country)`
- **[Run]** button (grande, primary)
- **Progress area** (debajo): un `AccountStatusRow` por cuenta:
  ```
  [⏳ spinning]  201709682301   running on source...
  [✅]            235332210426   done
  [❌]            999999999999   error: account not found  [view log]
  ```
- **Bottom-left:** [← Back to home]
- **Bottom-right:** [Credentials] (atajo a Settings → tab credentials)

### Confirmation dialog (antes de Apply)
Cuando mode incluye DEST y se hace click en Run:
```
You are about to apply 5 accounts to:
   Chile QA 19c  (CHILE_QA_19C)

Accounts: 201709682301, 235332210426, ...

This will modify QA data. Continue?
[Cancel]  [Apply]
```
Default focus en `Cancel`.

### Settings view
Tabs:
1. **Credentials** — los dos modos descritos en §5
2. **General**
   - Language: `en` / `es`
   - Theme: `light` / `dark` (default `light`)
   - Spool output folder: text + browse button
   - SQLcl path: text + browse + "Test" button
3. **About** — version, link a GitHub, contact

---

## 9. Internacionalización (`i18n.py`)

```python
T = {
    "en": {
        "home.title": "Oracle Tasks Chile",
        "home.spools_button": "Spools / Accounts",
        "home.settings_button": "Settings",
        "spools.country": "Country",
        "spools.source": "Source DB",
        "spools.destination": "Destination",
        "spools.mode.extract": "Extract from production only",
        "spools.mode.extract_apply": "Extract & apply to QA/DEV",
        "spools.mode.apply_existing": "Apply existing spool file",
        "spools.accounts_placeholder": "One account number per line",
        "spools.run": "Run",
        "spools.confirm_apply": "You are about to apply {n} accounts to:\n   {db}\n\nThis will modify data. Continue?",
        "spools.status.running_source": "running on source...",
        "spools.status.running_dest": "applying to destination...",
        "spools.status.ok": "done",
        "spools.status.error": "error: {msg}",
        "settings.language": "Language",
        "settings.theme": "Theme",
        "settings.theme.light": "Light",
        "settings.theme.dark": "Dark",
        "update.available": "Update available — click to install",
        ...
    },
    "es": {
        "home.title": "Oracle Tasks Chile",
        "home.spools_button": "Spools / Cuentas",
        ...
    },
}

def t(key: str, **kwargs) -> str:
    lang = config.get("language", "en")
    s = T.get(lang, T["en"]).get(key, key)
    return s.format(**kwargs) if kwargs else s
```

Errores crudos de SQLcl/Oracle se muestran tal cual (siempre en inglés, como pediste).

---

## 10. Tema (light/dark)

`customtkinter.set_appearance_mode("light"|"dark")`. Toggle en Settings → General. Persistido en `config.json["theme"]`. Default `light`.

---

## 11. Auto-update

`core/updater.py`:
- Al iniciar la app (background thread), `requests.get('https://raw.githubusercontent.com/dpv20/oracle_tasks/main/version.json')`
- Compara con `version.py.__version__` (semver tuple compare)
- Si remote > local → emite evento → UI muestra banner clickable
- Click → llama `update.bat <pythonw_path>` y cierra la app (update.bat hace `git pull` + relanza)

Mismo patrón exacto que `vpn`.

---

## 12. Tamaño de ventana, single-instance, taskbar

- **Tamaño:** redimensionable, mínimo `700x550`, default `900x650` (estándar para apps tipo settings/utility)
- **Estado al inicio:** ventana normal (no maximizada)
- **Single-instance:** named mutex `OracleTasksChile_SingleInstance` + flag file (mismo patrón que `vpn`)
- **Taskbar:** AppUserModelID `Oracle.OracleTasksChile.1` seteado por `tools/set_aumid.ps1` durante install + `ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID` en `main.py`
- **Ícono:** `icono.jpg` → conversión a `icono.ico` (vía Pillow durante build, o pre-generado y commiteado)

---

## 13. Repo de GitHub

**URL:** `https://github.com/dpv20/oracle_tasks` (creado por el usuario).

Hardcodeada en:
- `install.bat` → `set "REPO_URL=https://github.com/dpv20/oracle_tasks.git"`
- `update.bat` → opera sobre el clone, no necesita URL
- `src/core/updater.py` → `https://raw.githubusercontent.com/dpv20/oracle_tasks/main/version.json`

Si el repo es privado, `install.bat` requiere que `git` ya esté autenticado (credential manager / token / SSH). Recomendado: **público**, ya que las credenciales nunca tocan el repo.

---

## 14. Plan de implementación por fases

Cada fase es un commit/PR funcional y testeable.

### Fase 1 — Esqueleto + Settings + Home (sin spools)
- `install.bat`, `update.bat`, `requirements.txt`, `version.json`
- Estructura `src/` con `main.py`, `paths.py`, `i18n.py`, `core/config.py`, `core/credentials.py`, `core/logger.py`
- UI: Home view con botón Spools (placeholder) y botón Settings
- Settings view completa: tabs Credentials (paste + form), General, About
- Toggle light/dark, switch en/es, persistencia en `config.json`
- DPAPI encrypt/decrypt de passwords
- **Hito de verificación:** instalar limpio en máquina, abrir app, pegar credenciales, cambiar idioma/tema, reiniciar y verificar persistencia.

### Fase 2 — Catálogo de DBs + integración SQLcl
- `core/databases.py` con catálogo Chile/Peru/Colombia poblado del `tnsnames.ora`
- `core/sqlcl.py` con `SqlclRunner` y `locate_sqlcl()`:
  1. Si `config.json["sqlcl_path"]` apunta a un `sql.exe` existente → úsalo
  2. `where sql.exe` (PATH del sistema)
  3. Rutas comunes (`Desktop\sqlcl\bin\`, `C:\sqlcl\bin\`, `%LOCALAPPDATA%\OracleTasksChile\sqlcl\bin\`)
  4. Si nada → retorna `None`, la UI muestra dialog "SQLcl not found" con opciones: enter path / download / cancel
- Lógica de detección + descarga interactiva en `install.bat` (ver §3)
- Settings → General → "SQLcl path" con botones Browse / Download / Auto-detect
- **Hito de verificación:** botón "Test connection" en Settings que conecta a una DB y corre `select 1 from dual`. Probar con Chile QA y verificar que el output aparece en log. Probar también: borrar `sqlcl_path` del config, reabrir, debe detectar SQLcl del PATH automáticamente.

### Fase 3 — Spools view + modo Extract Only
- `spools_view.py` con dropdowns país/source y textarea de cuentas
- Templates `.sql.tmpl` (los originales con `{{SPOOL_OUT_DIR}}`)
- `core/spool_engine.py` modo `EXTRACT_ONLY`
- `widgets.py` con `AccountStatusRow` (spinner + status text)
- Threading: ejecución en thread, UI updates vía `app.after(0, ...)`
- Botón "Open spools folder"
- **Hito de verificación:** sacar 3 cuentas de Chile PROD a la carpeta local, verificar que los `.SQL` quedan idénticos a los que generas hoy manualmente.

### Fase 4 — Modo Extract & Apply + confirmación
- Dropdown destination
- Modo `EXTRACT_AND_APPLY` en `SpoolEngine`: ejecuta source, luego destination con el spool generado
- Diálogo de confirmación obligatorio antes de ejecutar destination
- Manejo de errores per-cuenta (continúa el batch)
- **Hito de verificación:** ciclo completo PROD → QA Chile, una cuenta. Luego batch de 3 cuentas con una inválida, verificar que las 2 buenas pasan y la mala muestra error.

### Fase 5 — Modo Apply Existing
- Modo `APPLY_EXISTING`: file picker para seleccionar `.SQL`
- UI condicional: oculta source, muestra "Browse spool file"
- **Hito de verificación:** seleccionar un spool existente, aplicar a QA, verificar.

### Fase 6 — Auto-update + polish
- `core/updater.py` chequeando `version.json` en repo
- Banner en home view con click handler
- AppUserModelID + single-instance guard
- Conversión `icono.jpg` → `icono.ico`
- Acceso directo en escritorio con ícono correcto
- README con instrucciones para usuarios
- **Hito de verificación:** bump version, push, abrir app instalada y verificar que aparece banner; click hace update.

### Fase 7 — Mejoras opcionales (post-MVP)
- Save-as dialog cuando mode=EXTRACT_ONLY y user marca "Save to custom location"
- Histórico de runs en Settings → "Recent activity"
- Validación: detectar si una cuenta ya existe en QA antes de aplicar

> Nota: México entró al MVP el 2026-05-12 (ya está en credentials/databases/UI). Falta el template `CL_ACCOUNT_SPOOL_MEXICO.sql` para que el flujo sea utilizable end-to-end en Fase 3.

---

## 15. Riesgos & decisiones abiertas

| Riesgo | Mitigación |
|---|---|
| Descarga de SQLcl bloqueada por firewall corporativo | El instalador siempre prioriza SQLcl ya instalado (PATH + rutas comunes) antes de descargar; si la descarga falla el usuario puede ingresar ruta manual. Caso del usuario actual: ya tiene SQLcl en `Desktop\sqlcl`, así que ni se intenta descargar. |
| `download.oracle.com` cambia URL del zip | Pin a versión específica (ej. `sqlcl-23.4.0.023.2321.zip`); reviews periódicos |
| Credenciales prod compartidas en `config.json` | DPAPI las protege per-Windows-user. Si se requiere más, futuro: master password |
| Usuario hace click en "Apply" por error | Confirmación obligatoria con DB destino visible y focus en Cancel |
| Encoding issues en output de SQLcl con caracteres latinos | `subprocess.Popen(..., encoding="utf-8", errors="replace")` |
| Spool file paths con espacios (`Diego Pavez`) | Siempre quotear paths en argumentos a `sql.exe` |
| Race condition al actualizar mientras app corre | `update.bat` hace `taskkill` por window title antes de `git reset` |

---

## 16. Mapa de equivalencias con `vpn` (para reuso)

| `vpn` | `oracle_tasks` | Cambio |
|---|---|---|
| `install.bat` | `install.bat` | + paso de descarga de SQLcl |
| `update.bat` | `update.bat` | Solo cambia paths (`OracleTasksChile`) |
| `uninstall.bat` | `uninstall.bat` | Igual estructura |
| `tools/set_aumid.ps1` | igual | Sin cambios |
| `src/main.py` (DPI, mutex, AUMID) | `src/main.py` | Sin cambios estructurales, solo nombres |
| `src/config_manager.py` (DPAPI) | `src/core/config.py` + `core/credentials.py` | Schema diferente |
| `src/version.py` + `assets/version.json` | igual | Sin cambios |
| `VPNSwitcher.spec` (PyInstaller) | (no se usa por ahora) | Decisión: ejecutar desde fuente como `vpn` hace en producción |

---

## 17. Resumen ejecutivo

App Python con UI customtkinter, instalada vía `install.bat` que se ocupa de Python+Git+SQLcl. Repo público en GitHub (`dpv20/oracle_tasks`) con auto-update por `git pull`. Pantalla home minimalista; pantalla principal de spools con tres modos (extract, extract+apply, apply-existing), batch multilínea, confirmación obligatoria antes de tocar QA/DEV. Credenciales en JSON local con passwords DPAPI; soporta proxy auth `user[schema]/pass`. SQLcl ejecuta los scripts existentes sin modificarlos vía templates con `{{SPOOL_OUT_DIR}}`. i18n EN/ES, light/dark mode. Mismo patrón de instalación/update probado en `vpn`.

**Próximo paso:** crear el repo `dpv20/oracle_tasks` en GitHub, confirmar URL, y arrancamos con Fase 1.
