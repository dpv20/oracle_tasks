"""ConfigManager: reads/writes %APPDATA%\\OracleTasksChile\\config.json.

Passwords inside credential dicts are encrypted with DPAPI before being written
to disk and decrypted on read. Encryption is per-Windows-user, so the file is
unreadable by other accounts on the same machine.

Config schema (v7):
{
  "version": 7,
  "language": "en" | "es",
  "theme": "light" | "dark",
  "sqlcl_path": "<absolute path to sql.exe>",
  "fbbatch_root": "<absolute path to FBBatchSetup folder; empty = repo/FBBatchSetup>",
  "oracle_email": "",
  "falabella_email": "",
  "fbbatch_mail_subject": "NSSR : {MONTH_UPPER} {DAY} {YEAR}",
  "fbbatch_mail_from": "",
  "fbbatch_mail_to": "",
  "fbbatch_mail_cc": "",
  "fbbatch_mail_body": "<template>",
  "fbbatch_use_classic_outlook": false,
  "spools_cl_output_dir": "<override; empty = use default in DATA_DIR>",
  "verify_savings_apply": false,
  "start_with_windows": true,
  "vpn_show_forti": true,
  "vpn_show_bice": false,
  "credentials": {
      "chile": {
          "CHILE_QA_19C": {
              "DPAVEZV[FXBFCLPR]": { user, schema?, password_enc, tns, bucket }
          },
          "FXBFCL_19C_PROD_OCI": { ... }
      },
      "peru": { ... },
      "colombia": { ... },
      "mexico": { ... }
  }
}
"""
import base64
import json
import logging
import re
from copy import deepcopy
from typing import Any

from paths import CONFIG_DIR, CONFIG_FILE

log = logging.getLogger(__name__)

DEFAULTS: dict[str, Any] = {
    "version": 7,
    "language": "en",
    "theme": "light",
    "sqlcl_path": "",
    "oracle_email": "",
    "falabella_email": "",
    "fbbatch_root": "",
    "fbbatch_mail_subject": "NSSR : {MONTH_UPPER} {DAY} {YEAR}",
    "fbbatch_mail_from": "",
    "fbbatch_mail_to": '"Michell Zambrano" <michell.zambrano@oracle.com>; "Adarsh Kumar" <adarsh.kumar@oracle.com>; "Jackeline R Diaz Junco" <jroxadiazj@falabella.cl>; "Batch Support Flex FIF" <batchsoporteflexfif@falabella.cl>; "Ricardo Campos Barraza" <riccamposb@falabella.cl>; "Marco Aurelio Luna" <maluna@falabella.cl>; "aechacinm@Falabella.cl" <aechacinm@Falabella.cl>',
    "fbbatch_mail_cc": '"KANNAN MUTHUSAMY" <kannan.m@oracle.com>; "Sharath Pattabiraman" <sharath.pattabiraman@oracle.com>; "Ashwin M" <ashwin.m@oracle.com>; "Diego Pavez" <diego.pavez@oracle.com>',
    "fbbatch_mail_body": "",
    "fbbatch_use_classic_outlook": False,
    "spools_cl_output_dir": "",
    "verify_savings_apply": False,
    "start_with_windows": True,
    "vpn_show_forti": True,
    "vpn_show_bice": False,
    "cisco_cli_path": "",
    "cisco_host": "",
    "cisco_username": "",
    "cisco_password_enc": "",
    "forti_exe_path": "",
    "forti_connect_cmd": "",
    "forti_disconnect_cmd": "",
    "forti_username": "",
    "forti_password_enc": "",
    "forti_flow_mode": "detect",
    "forti_flow_steps": ["username", "password", "mfa"],
    "gp_exe_path": "",
    "gp_username": "",
    "gp_password_enc": "",
    "gp_portal_url": "ext.bice.cl",
    "credentials": {
        "chile": {},
        "peru": {},
        "colombia": {},
        "mexico": {},
    },
}

CRED_BUCKETS = ("shared_prod", "user_qa", "user_dev", "user_bup_qa", "user_bup_prod")
CRED_COUNTRIES = ("chile", "peru", "colombia", "mexico")
DIEGO_CC_RECIPIENT = '"Diego Pavez" <diego.pavez@oracle.com>'
LEGACY_VPN_CONFIG = CONFIG_DIR.parent / "VPNSwitcher" / "config.json"


def _ensure_mail_recipient(raw: str, recipient: str, email: str) -> str:
    if re.search(re.escape(email), raw or "", flags=re.IGNORECASE):
        return raw
    raw = (raw or "").strip()
    return f"{raw}; {recipient}" if raw else recipient


# ── DPAPI password encryption ────────────────────────────────────────────────

def encrypt_password(plain: str) -> str:
    """Encrypt with Windows DPAPI; only the current Windows user can decrypt."""
    if not plain:
        return ""
    try:
        import win32crypt
        blob = win32crypt.CryptProtectData(
            plain.encode("utf-8"), "OracleTasksChile", None, None, None, 0
        )
        return base64.b64encode(blob).decode("ascii")
    except Exception as e:
        log.error("encrypt_password failed: %s", e)
        return ""


def decrypt_password(enc: str) -> str:
    """Decrypt a DPAPI-encrypted password produced by encrypt_password."""
    if not enc:
        return ""
    try:
        import win32crypt
        blob = base64.b64decode(enc.encode("ascii"))
        _, data = win32crypt.CryptUnprotectData(blob, None, None, None, 0)
        return data.decode("utf-8")
    except Exception as e:
        log.error("decrypt_password failed: %s", e)
        return ""


# ── ConfigManager ────────────────────────────────────────────────────────────

class ConfigManager:
    def __init__(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        self._data: dict[str, Any] = deepcopy(DEFAULTS)
        self.load()

    def load(self) -> None:
        if not CONFIG_FILE.exists():
            self._migrate_legacy_vpn_settings(self._data)
            self.save()
            return
        try:
            with CONFIG_FILE.open("r", encoding="utf-8") as f:
                loaded = json.load(f)
            self._data = self._merge_defaults(loaded)
            if self._data != loaded:
                self.save()
        except (OSError, json.JSONDecodeError) as e:
            log.error("Could not read %s (%s); rewriting with defaults.", CONFIG_FILE, e)
            self._data = deepcopy(DEFAULTS)
            self.save()

    def save(self) -> None:
        try:
            with CONFIG_FILE.open("w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2, ensure_ascii=False)
        except OSError as e:
            log.error("Could not write %s: %s", CONFIG_FILE, e)

    def _merge_defaults(self, loaded: dict[str, Any]) -> dict[str, Any]:
        """Deep-merge loaded config over DEFAULTS and migrate legacy credentials."""
        loaded_version = int(loaded.get("version") or 0)
        merged = deepcopy(DEFAULTS)
        for k, v in loaded.items():
            if k == "credentials":
                merged[k] = self._normalize_credentials(v)
                continue
            if isinstance(v, dict) and isinstance(merged.get(k), dict):
                merged[k] = {**merged[k], **v}
            else:
                merged[k] = v
        if loaded_version < 4:
            merged["verify_savings_apply"] = False
        if loaded_version < 5:
            merged["fbbatch_mail_cc"] = _ensure_mail_recipient(
                str(merged.get("fbbatch_mail_cc") or ""),
                DIEGO_CC_RECIPIENT,
                "diego.pavez@oracle.com",
            )
        if loaded_version < 6:
            merged["vpn_show_bice"] = False
        if loaded_version < 7:
            self._migrate_legacy_vpn_settings(merged)
        merged["version"] = DEFAULTS["version"]
        return merged

    @staticmethod
    def _migrate_legacy_vpn_settings(merged: dict[str, Any]) -> None:
        """Import the per-user VPN Switcher config once during schema v7 migration."""
        try:
            with LEGACY_VPN_CONFIG.open("r", encoding="utf-8") as handle:
                legacy = json.load(handle)
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return
        if not isinstance(legacy, dict):
            return

        direct_keys = (
            "cisco_cli_path",
            "cisco_host",
            "cisco_username",
            "forti_exe_path",
            "forti_connect_cmd",
            "forti_disconnect_cmd",
            "forti_username",
            "forti_password_enc",
            "forti_flow_mode",
            "forti_flow_steps",
            "gp_exe_path",
            "gp_username",
            "gp_password_enc",
            "gp_portal_url",
            "start_with_windows",
        )
        for key in direct_keys:
            if key in legacy:
                merged[key] = legacy[key]

        plain_cisco_password = str(legacy.get("cisco_password") or "")
        if plain_cisco_password:
            merged["cisco_password_enc"] = encrypt_password(plain_cisco_password)
        if "show_forti" in legacy:
            merged["vpn_show_forti"] = bool(legacy["show_forti"])
        if "show_gp" in legacy:
            merged["vpn_show_bice"] = bool(legacy["show_gp"])
        log.info("Migrated VPN settings from %s", LEGACY_VPN_CONFIG)

    def _normalize_credentials(self, value: Any) -> dict[str, dict[str, dict[str, dict[str, str]]]]:
        """Return credentials as country -> DB/TNS -> login -> credential.

        Legacy v1 stored credentials as bucket -> country -> credential. During
        migration each credential moves under its own TNS and login key, and
        keeps bucket as metadata.
        """
        normalized: dict[str, dict[str, dict[str, dict[str, str]]]] = {
            country: {} for country in CRED_COUNTRIES
        }
        if not isinstance(value, dict):
            return normalized

        if any(bucket in value for bucket in CRED_BUCKETS):
            for bucket, by_country in value.items():
                if bucket not in CRED_BUCKETS or not isinstance(by_country, dict):
                    continue
                for country, cred in by_country.items():
                    if country not in CRED_COUNTRIES or not isinstance(cred, dict):
                        continue
                    tns = str(cred.get("tns") or "").strip()
                    if not tns:
                        continue
                    stored = {
                        **cred,
                        "tns": tns,
                        "bucket": bucket,
                    }
                    normalized[country].setdefault(self._db_key(tns), {})[
                        self._credential_key(stored)
                    ] = stored
            return normalized

        for country, by_db in value.items():
            if country not in CRED_COUNTRIES or not isinstance(by_db, dict):
                continue
            for db_name, db_value in by_db.items():
                if not isinstance(db_value, dict):
                    continue
                if "user" in db_value or "password_enc" in db_value:
                    # v2 shape: country -> DB/TNS -> credential
                    credential_items = [(self._credential_key(db_value), db_value)]
                else:
                    # v3 shape: country -> DB/TNS -> login -> credential
                    credential_items = list(db_value.items())

                tns = str(db_name).strip()
                if not tns:
                    continue
                db_key = self._db_key(tns)
                for login_key, cred in credential_items:
                    if not isinstance(cred, dict):
                        continue
                    stored_tns = str(cred.get("tns") or db_name).strip()
                    if not stored_tns:
                        continue
                    stored = {
                        **cred,
                        "tns": stored_tns,
                        "bucket": cred.get("bucket") or "",
                    }
                    normalized[country].setdefault(db_key, {})[
                        self._credential_key(stored) or str(login_key).upper()
                    ] = stored
        return normalized

    @staticmethod
    def _db_key(db_name: str) -> str:
        return db_name.strip().upper()

    @staticmethod
    def _credential_key(cred: dict[str, Any]) -> str:
        user = str(cred.get("user") or "credential").strip()
        schema = str(cred.get("schema") or "").strip()
        return f"{user}[{schema}]".upper() if schema else user.upper()

    # ── simple getters/setters ──
    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value
        self.save()

    def update(self, values: dict[str, Any]) -> None:
        self._data.update(values)
        self.save()

    @property
    def data(self) -> dict[str, Any]:
        return self._data

    # ── credentials helpers ──
    def set_credential(self, country: str, db_name: str, cred: dict[str, str]) -> None:
        """Store a credential. `cred` must already have password_enc set, NOT plain."""
        if country not in CRED_COUNTRIES:
            raise ValueError(f"Unknown credential country: {country}")
        db_key = self._db_key(db_name or cred.get("tns", ""))
        if not db_key:
            raise ValueError("Credential DB name/TNS is required")
        if cred.get("bucket") and cred["bucket"] not in CRED_BUCKETS:
            raise ValueError(f"Unknown credential bucket: {cred['bucket']}")
        stored = {
            **cred,
            "tns": cred.get("tns") or db_name,
        }
        self._data["credentials"].setdefault(country, {}).setdefault(db_key, {})[
            self._credential_key(stored)
        ] = stored
        self.save()

    def get_credential(
        self, country: str, db_name: str, credential_key: str | None = None
    ) -> dict[str, str] | None:
        by_login = self._data["credentials"].get(country, {}).get(self._db_key(db_name), {})
        if credential_key:
            return by_login.get(credential_key.upper())
        if len(by_login) == 1:
            return next(iter(by_login.values()))
        return None

    def delete_credential(self, country: str, db_name: str, credential_key: str | None = None) -> None:
        by_country = self._data["credentials"].get(country, {})
        db_key = self._db_key(db_name)
        if credential_key:
            by_country.get(db_key, {}).pop(credential_key.upper(), None)
            if not by_country.get(db_key):
                by_country.pop(db_key, None)
        else:
            by_country.pop(db_key, None)
        self.save()

    def all_credentials(self) -> dict[str, dict[str, dict[str, dict[str, str]]]]:
        return self._data["credentials"]
