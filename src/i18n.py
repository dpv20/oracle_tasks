"""Tiny i18n: dict lookup with format() interpolation.

Usage:
    from i18n import t, set_language
    set_language("es")
    t("home.spools_button")               -> "Spools / Cuentas"
    t("spools.confirm_apply", n=3, db="X") -> "Vas a aplicar 3 cuentas a:\\n   X\\n..."

If a key is missing in the active language, falls back to English.
If missing in English too, returns the key itself (visible debugging signal).
"""
from typing import Any

_DEFAULT = "en"
_current = _DEFAULT

T: dict[str, dict[str, str]] = {
    "en": {
        # Window
        "app.title": "Oracle Tasks Chile",

        # Home view
        "home.spools_button": "Spools / Accounts",
        "home.settings_button": "Settings",
        "home.placeholder": "Select a task above.",
        "home.created_by": "Created by: Diego Pavez Verdi",
        "home.contact": "Contact",

        # Update banner
        "update.available": "Update available — click to install",
        "update.available_v": "⬆  Update available v{version} — click to install",
        "update.installing": "Installing update...",

        # Spools view
        "spools.title": "Spools / Accounts",
        "spools.coming_soon": "Spools workflow — implemented in Phase 3.",
        "spools.country": "Country",
        "spools.source_db": "Source DB",
        "spools.destination_db": "Destination DB",
        "spools.account_number": "Account number",
        "spools.add_account": "+ Add",
        "spools.accounts_summary": "Accounts ({n})",
        "spools.extract_only_header": "Extract ({n})",
        "spools.inject_header": "Inject ({n})",
        "spools.move_to_inject": "Inject",
        "spools.run": "Extract spools",
        "spools.run_extract_only": "Extract",
        "spools.run_extract_apply": "Apply",
        "spools.extracting": "Extracting... {done}/{total}",
        "spools.injecting": "Injecting... {done}/{total}",
        "spools.running": "Running... {done}/{total}",
        "spools.status_extracting": "extracting...",
        "spools.status_spool_saved": "spool saved",
        "spools.status_ready_to_inject": "spool ready",
        "spools.status_injecting": "injecting...",
        "spools.status_injected": "injected",
        "spools.summary_ok": "{ok}/{total} OK",
        "spools.summary_mixed": "{ok}/{total} OK · {err} errors",
        "spools.summary_extract_inject": "Extract {extract_ok}/{extract_total} OK - Inject {inject_ok}/{inject_total} OK - {err} errors",
        "spools.open_folder": "Open spools folder",
        "spools.no_template": "No spool template for {country}. Add it to spools_sql/ first.",
        "spools.no_creds": "No credential saved for {db}. Add one in Settings.",
        "spools.no_sqlcl": "Set the SQLcl path in Settings → General first.",
        "spools.no_pending": "Add at least one account before extracting.",
        "spools.invalid_account": "Invalid account format: {acc}",
        "spools.duplicate_account": "Already in the list.",
        "spools.invalid_db": "Pick a source database.",
        "spools.invalid_destination_db": "Pick a destination database.",
        "spools.same_source_destination": "Source and destination cannot be the same DB.",
        "spools.confirm_title": "Confirm inject",
        "spools.confirm_inject": "You are about to inject {n} accounts into:\n   {db}\n\nAccounts: {accounts}\n\nThis will modify destination data. Continue?",

        # Settings view
        "settings.title": "Settings",
        "settings.tab.credentials": "Credentials",
        "settings.tab.general": "General",
        "settings.tab.about": "About",

        # Settings → Credentials
        "settings.cred.mode.paste": "Paste",
        "settings.cred.mode.form": "Form",
        "settings.cred.paste.help": "One credential per line. Format: user/pass@DB or user[schema]/pass@DB",
        "settings.cred.paste.button": "Parse & save",
        "settings.cred.paste.summary": "{n} credentials parsed:",
        "settings.cred.paste.confirm": "Save these credentials?",
        "settings.cred.paste.invalid": "{n} lines could not be parsed and will be skipped:",
        "settings.cred.paste.empty": "No valid credentials found.",
        "settings.cred.form.help": "Pick country and environment, then enter user, password and DB name. If your user has a proxy schema, type it inline as user[schema].",
        "settings.cred.form.country": "Country",
        "settings.cred.form.env": "Environment",
        "settings.cred.form.user": "User",
        "settings.cred.form.password": "Password",
        "settings.cred.form.tns": "DB name (TNS)",
        "settings.cred.form.save": "Save credential",
        "settings.cred.form.required": "Country, environment, user, password and DB name are all required.",
        "settings.cred.form.invalid": "Could not parse the values. The user field allows letters, digits, underscore and optional [schema].",
        "settings.cred.saved": "Saved.",
        "settings.cred.list.title": "Saved credentials",
        "settings.cred.list.empty": "(none)",
        "settings.cred.list.delete": "Delete",
        "settings.cred.list.edit": "Edit",
        "settings.cred.list.country_empty": "(no credentials)",
        "settings.cred.list.country_count_one": "{n} credential",
        "settings.cred.list.country_count_many": "{n} credentials",
        "settings.cred.list.country_dialog_title": "{country} credentials",
        "settings.cred.list.country_dialog_hint": "Click Edit to modify, Delete to remove. Adding new credentials still happens in the Form/Paste tabs.",
        "settings.cred.edit.title": "Edit credential",
        "settings.cred.edit.save": "Save changes",
        "settings.cred.edit.cancel": "Cancel",
        "settings.cred.edit.delete_confirm": "Delete this credential?",

        # Settings → General
        "settings.general.language": "Language",
        "settings.general.lang.en": "English",
        "settings.general.lang.es": "Spanish",
        "settings.general.theme": "Theme",
        "settings.general.theme.light": "Light",
        "settings.general.theme.dark": "Dark",
        "settings.general.sqlcl": "SQLcl path (sql.exe path, e.g. ...\\sqlcl\\bin\\sql.EXE)",
        "settings.general.sqlcl_not_found": "SQLcl was not found in PATH or common locations. Browse for sql.exe manually.",
        "settings.general.browse": "Browse...",
        "settings.general.detect": "Auto-detect",
        "settings.general.download": "Download from Oracle",
        "settings.general.test": "Test",
        "settings.general.test_section": "Test connection",
        "settings.general.test_db_label": "Credential",
        "settings.general.test_running": "Connecting...",
        "settings.general.test_ok": "Connected — query returned 1",
        "settings.general.test_fail": "Failed (exit {code})",
        "settings.general.test_no_sqlcl": "Set the SQLcl path first.",
        "settings.general.test_no_creds": "No credentials saved. Add one in the Credentials tab.",
        "settings.general.apply": "Apply",
        "settings.general.restart_for_lang": "Language change applied. Some text updates after restart.",

        # Settings → About
        "settings.about.version": "Version",
        "settings.about.repo": "Repository",
        "settings.about.creator": "Creator",
        "settings.about.email": "Email",
        "settings.about.phone": "Phone",
        "settings.about.contact": "Contact",

        # Common
        "common.back": "Back",
        "common.cancel": "Cancel",
        "common.ok": "OK",
        "common.close": "Close",
        "common.error": "Error",
        "common.warning": "Warning",
        "common.info": "Info",
    },

    "es": {
        # Window
        "app.title": "Oracle Tasks Chile",

        # Home view
        "home.spools_button": "Spools / Cuentas",
        "home.settings_button": "Configuración",
        "home.placeholder": "Selecciona una tarea arriba.",
        "home.created_by": "Creado por: Diego Pavez Verdi",
        "home.contact": "Contacto",

        # Update banner
        "update.available": "Actualización disponible — haz click para instalar",
        "update.available_v": "⬆  Actualización v{version} disponible — haz click para instalar",
        "update.installing": "Instalando actualización...",

        # Spools view
        "spools.title": "Spools / Cuentas",
        "spools.coming_soon": "Flujo de spools — se implementa en la Fase 3.",
        "spools.country": "País",
        "spools.source_db": "DB origen",
        "spools.destination_db": "DB destino",
        "spools.account_number": "Número de cuenta",
        "spools.add_account": "+ Agregar",
        "spools.accounts_summary": "Cuentas ({n})",
        "spools.extract_only_header": "Extract ({n})",
        "spools.inject_header": "Inject ({n})",
        "spools.move_to_inject": "Inject",
        "spools.run": "Extraer spools",
        "spools.run_extract_only": "Extract",
        "spools.run_extract_apply": "Apply",
        "spools.extracting": "Extrayendo... {done}/{total}",
        "spools.injecting": "Inyectando... {done}/{total}",
        "spools.running": "Procesando... {done}/{total}",
        "spools.status_extracting": "extrayendo...",
        "spools.status_spool_saved": "spool guardado",
        "spools.status_ready_to_inject": "spool listo",
        "spools.status_injecting": "inyectando...",
        "spools.status_injected": "inyectada",
        "spools.summary_ok": "{ok}/{total} OK",
        "spools.summary_mixed": "{ok}/{total} OK · {err} con error",
        "spools.summary_extract_inject": "Extract {extract_ok}/{extract_total} OK - Inject {inject_ok}/{inject_total} OK - {err} con error",
        "spools.open_folder": "Abrir carpeta de spools",
        "spools.no_template": "No hay template de spool para {country}. Agrégalo a spools_sql/ primero.",
        "spools.no_creds": "No hay credencial para {db}. Agrega una en Configuración.",
        "spools.no_sqlcl": "Configura la ruta de SQLcl en Configuración → General primero.",
        "spools.no_pending": "Agrega al menos una cuenta antes de extraer.",
        "spools.invalid_account": "Formato inválido: {acc}",
        "spools.duplicate_account": "Ya está en la lista.",
        "spools.invalid_db": "Selecciona una DB origen.",
        "spools.invalid_destination_db": "Selecciona una DB destino.",
        "spools.same_source_destination": "Origen y destino no pueden ser la misma DB.",
        "spools.confirm_title": "Confirmar inject",
        "spools.confirm_inject": "Vas a inyectar {n} cuentas en:\n   {db}\n\nCuentas: {accounts}\n\nEsto modificara datos en destino. Continuar?",

        # Settings view
        "settings.title": "Configuración",
        "settings.tab.credentials": "Credenciales",
        "settings.tab.general": "General",
        "settings.tab.about": "Acerca de",

        # Settings → Credentials
        "settings.cred.mode.paste": "Pegar",
        "settings.cred.mode.form": "Formulario",
        "settings.cred.paste.help": "Una credencial por línea. Formato: user/pass@DB o user[schema]/pass@DB",
        "settings.cred.paste.button": "Parsear y guardar",
        "settings.cred.paste.summary": "{n} credenciales parseadas:",
        "settings.cred.paste.confirm": "¿Guardar estas credenciales?",
        "settings.cred.paste.invalid": "{n} líneas no se pudieron parsear y se omitirán:",
        "settings.cred.paste.empty": "No se encontraron credenciales válidas.",
        "settings.cred.form.help": "Selecciona país y ambiente, luego ingresa usuario, password y nombre de la DB. Si tu usuario tiene proxy schema, escríbelo inline como usuario[schema].",
        "settings.cred.form.country": "País",
        "settings.cred.form.env": "Ambiente",
        "settings.cred.form.user": "Usuario",
        "settings.cred.form.password": "Password",
        "settings.cred.form.tns": "Nombre DB (TNS)",
        "settings.cred.form.save": "Guardar credencial",
        "settings.cred.form.required": "País, ambiente, usuario, password y nombre de DB son obligatorios.",
        "settings.cred.form.invalid": "No se pudieron parsear los valores. El campo usuario admite letras, dígitos, guión bajo y un [schema] opcional.",
        "settings.cred.saved": "Guardado.",
        "settings.cred.list.title": "Credenciales guardadas",
        "settings.cred.list.empty": "(ninguna)",
        "settings.cred.list.delete": "Borrar",
        "settings.cred.list.edit": "Editar",
        "settings.cred.list.country_empty": "(sin credenciales)",
        "settings.cred.list.country_count_one": "{n} credencial",
        "settings.cred.list.country_count_many": "{n} credenciales",
        "settings.cred.list.country_dialog_title": "Credenciales — {country}",
        "settings.cred.list.country_dialog_hint": "Click Editar para modificar, Borrar para eliminar. Las credenciales nuevas se agregan en los tabs Formulario/Pegar.",
        "settings.cred.edit.title": "Editar credencial",
        "settings.cred.edit.save": "Guardar cambios",
        "settings.cred.edit.cancel": "Cancelar",
        "settings.cred.edit.delete_confirm": "¿Borrar esta credencial?",

        # Settings → General
        "settings.general.language": "Idioma",
        "settings.general.lang.en": "Inglés",
        "settings.general.lang.es": "Español",
        "settings.general.theme": "Tema",
        "settings.general.theme.light": "Claro",
        "settings.general.theme.dark": "Oscuro",
        "settings.general.sqlcl": "Ruta de SQLcl (el path del .exe, ejemplo ...\\sqlcl\\bin\\sql.EXE)",
        "settings.general.sqlcl_not_found": "SQLcl no se encontró en PATH ni en rutas comunes. Selecciona sql.exe manualmente.",
        "settings.general.browse": "Examinar...",
        "settings.general.detect": "Auto-detectar",
        "settings.general.download": "Descargar de Oracle",
        "settings.general.test": "Probar",
        "settings.general.test_section": "Probar conexión",
        "settings.general.test_db_label": "Credencial",
        "settings.general.test_running": "Conectando...",
        "settings.general.test_ok": "Conectado — el query retornó 1",
        "settings.general.test_fail": "Falló (exit {code})",
        "settings.general.test_no_sqlcl": "Configura primero la ruta de SQLcl.",
        "settings.general.test_no_creds": "No hay credenciales guardadas. Agrega una en el tab Credenciales.",
        "settings.general.apply": "Aplicar",
        "settings.general.restart_for_lang": "Idioma actualizado. Algunos textos se refrescan al reiniciar.",

        # Settings → About
        "settings.about.version": "Versión",
        "settings.about.repo": "Repositorio",
        "settings.about.creator": "Creador",
        "settings.about.email": "Email",
        "settings.about.phone": "Teléfono",
        "settings.about.contact": "Contacto",

        # Common
        "common.back": "Volver",
        "common.cancel": "Cancelar",
        "common.ok": "Aceptar",
        "common.close": "Cerrar",
        "common.error": "Error",
        "common.warning": "Advertencia",
        "common.info": "Info",
    },
}


def set_language(lang: str) -> None:
    global _current
    _current = lang if lang in T else _DEFAULT


def get_language() -> str:
    return _current


def t(key: str, **kwargs: Any) -> str:
    s = T.get(_current, T[_DEFAULT]).get(key)
    if s is None:
        s = T[_DEFAULT].get(key, key)
    return s.format(**kwargs) if kwargs else s
