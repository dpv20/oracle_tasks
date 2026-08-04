"""Oracle database metadata and configured-database helpers.

The static catalog provides friendly labels for known aliases. Runtime dropdowns
are built from saved credentials so each user only sees TNS aliases they can use.

Each entry is `{"id": <TNS name>, "label": <human label>}`:
- `id` MUST match the TNS name exactly as it appears in `tnsnames.ora` — SQLcl
  uses it to resolve the connection.
- `label` is shown in dropdowns.

Env values: `prod` | `qa` | `dev` | `bup_qa` | `bup_prod`.
The matching credential bucket for each env lives in `ENV_TO_BUCKET`.
"""
from __future__ import annotations

from typing import Iterable

# env name in this catalog -> bucket key used in config.json credentials
ENV_TO_BUCKET: dict[str, str] = {
    "prod":     "shared_prod",
    "qa":       "user_qa",
    "dev":      "user_dev",
    "bup_qa":   "user_bup_qa",
    "bup_prod": "user_bup_prod",
}
BUCKET_TO_ENV: dict[str, str] = {bucket: env for env, bucket in ENV_TO_BUCKET.items()}

ENVS: tuple[str, ...] = ("prod", "qa", "dev", "bup_qa", "bup_prod")

DATABASES: dict[str, dict[str, list[dict[str, str]]]] = {
    "chile": {
        "prod": [
            {"id": "fxbfcl_19c_prod_oci",    "label": "Chile PROD (OCI)"},
            {"id": "fxbfcl_19c_prod_oci_dr", "label": "Chile PROD DR"},
        ],
        "qa": [
            {"id": "CHILE_QA_19C",   "label": "Chile QA 19c"},
            {"id": "FXBFCL_19C_QA", "label": "Chile QA 19c"},
            {"id": "CHILE_QA4_OCI",  "label": "Chile QA4 OCI"},
        ],
        "dev": [
            {"id": "CHILE_DEV", "label": "Chile DEV"},
        ],
        "bup_qa": [
            {"id": "BUP_QA_CL", "label": "Chile BUP QA"},
        ],
        "bup_prod": [
            {"id": "BUP_CL_2024", "label": "Chile BUP PROD"},
        ],
    },
    "peru": {
        "prod": [
            {"id": "PERU_OCI_PROD", "label": "Peru PROD (OCI)"},
        ],
        "qa": [
            {"id": "PERU_QA_OCI_19C", "label": "Peru QA 19c"},
        ],
        "dev": [
            {"id": "PERU_DEV", "label": "Peru DEV"},
        ],
        "bup_qa": [
            {"id": "PeruBUPOCIQA", "label": "Peru BUP QA"},
        ],
        "bup_prod": [
            {"id": "PeruBUPOCIProd",   "label": "Peru BUP PROD"},
            {"id": "PeruBUPOCIProdDR", "label": "Peru BUP PROD DR"},
        ],
    },
    "colombia": {
        "prod": [
            {"id": "BFCO_POCISANTIAGO", "label": "Colombia PROD Santiago"},
            {"id": "BFCO_POCISAOPALO",  "label": "Colombia PROD Sao Paulo"},
        ],
        "qa": [
            {"id": "COL_QA_INT_OCI", "label": "Colombia QA Int OCI"},
        ],
        "dev": [
            {"id": "COL_DEV", "label": "Colombia DEV"},
        ],
        "bup_qa":   [],
        "bup_prod": [],
    },
    "mexico": {
        "prod": [
            {"id": "MX_PROD_OCI",    "label": "Mexico PROD (OCI)"},
            {"id": "MX_PROD_OCI_DR", "label": "Mexico PROD DR"},
        ],
        "qa": [
            {"id": "MEXICO_QA_OCI", "label": "Mexico QA OCI"},
        ],
        "dev":      [],
        "bup_qa":   [],
        "bup_prod": [],
    },
}


def countries() -> tuple[str, ...]:
    return tuple(DATABASES.keys())


def envs_for(country: str) -> tuple[str, ...]:
    by_env = DATABASES.get(country, {})
    return tuple(env for env in ENVS if by_env.get(env))


def databases_for(country: str, env: str | None = None) -> list[dict[str, str]]:
    """Return DB entries for a country, optionally filtered by env."""
    by_env = DATABASES.get(country, {})
    if env is None:
        flat: list[dict[str, str]] = []
        for e in ENVS:
            for db in by_env.get(e, []):
                flat.append({**db, "env": e, "country": country})
        return flat
    return [
        {**db, "env": env, "country": country}
        for db in by_env.get(env, [])
    ]


def configured_databases(
    credentials: dict,
    country: str,
    envs: Iterable[str] | None = None,
) -> list[dict[str, str | int]]:
    """Return saved credential aliases as database-picker entries.

    One entry is returned per login. When an alias has more than one saved login,
    callers can use ``credential_count`` and ``credential_label`` to distinguish
    them in the UI. Unknown TNS aliases are supported as long as their credential
    has an environment bucket selected in Settings.
    """
    env_order = tuple(envs) if envs is not None else ENVS
    allowed_envs = set(env_order)
    env_position = {env: index for index, env in enumerate(env_order)}
    rows: list[dict[str, str | int]] = []

    by_database = credentials.get(country, {}) if isinstance(credentials, dict) else {}
    if not isinstance(by_database, dict):
        return rows

    for database_key, by_login in by_database.items():
        if not isinstance(by_login, dict):
            continue
        for credential_key, credential in by_login.items():
            if not isinstance(credential, dict):
                continue
            tns = str(credential.get("tns") or database_key).strip()
            if not tns:
                continue
            known = find_db(tns)
            if known is not None and known["country"] != country:
                known = None
            env = BUCKET_TO_ENV.get(str(credential.get("bucket") or ""))
            if env is None and known is not None:
                env = known["env"]
            if env not in allowed_envs:
                continue

            user = str(credential.get("user") or credential_key).strip()
            schema = str(credential.get("schema") or "").strip()
            login_label = f"{user}[{schema}]" if schema else user
            fallback_label = f"{country.title()} {env.replace('_', ' ').upper()}"
            rows.append({
                "id": tns,
                "label": known["label"] if known is not None else fallback_label,
                "env": env,
                "country": country,
                "database_key": str(database_key).upper(),
                "credential_key": str(credential_key).upper(),
                "credential_label": login_label,
            })

    counts: dict[tuple[str, str], int] = {}
    for row in rows:
        key = (str(row["env"]), str(row["id"]).upper())
        counts[key] = counts.get(key, 0) + 1
    for row in rows:
        row["credential_count"] = counts[(str(row["env"]), str(row["id"]).upper())]

    return sorted(
        rows,
        key=lambda row: (
            env_position.get(str(row["env"]), len(env_position)),
            str(row["id"]).upper(),
            str(row["credential_label"]).upper(),
        ),
    )


def find_db(tns: str) -> dict[str, str] | None:
    """Look up a DB entry by TNS name (case-insensitive). Includes env/country."""
    needle = tns.strip().upper()
    if not needle:
        return None
    for country, by_env in DATABASES.items():
        for env, dbs in by_env.items():
            for db in dbs:
                if db["id"].upper() == needle:
                    return {**db, "env": env, "country": country}
    return None


def all_dbs() -> Iterable[dict[str, str]]:
    """Iterate every DB in the catalog, each enriched with country/env."""
    for country, by_env in DATABASES.items():
        for env, dbs in by_env.items():
            for db in dbs:
                yield {**db, "env": env, "country": country}


def cred_bucket_for_env(env: str) -> str | None:
    return ENV_TO_BUCKET.get(env)
