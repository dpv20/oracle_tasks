"""Static catalog of Oracle databases per country and environment.

Mirrors `tnsnames.ora` (kept at repo root for reference). Drives the Spools CL view
dropdowns and any other UI that needs a DB picker.

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

ENVS: tuple[str, ...] = ("prod", "qa", "dev", "bup_qa", "bup_prod")

DATABASES: dict[str, dict[str, list[dict[str, str]]]] = {
    "chile": {
        "prod": [
            {"id": "fxbfcl_19c_prod_oci",    "label": "Chile PROD (OCI)"},
            {"id": "fxbfcl_19c_prod_oci_dr", "label": "Chile PROD DR"},
        ],
        "qa": [
            {"id": "CHILE_QA_19C",  "label": "Chile QA 19c"},
            {"id": "CHILE_QA4_OCI", "label": "Chile QA4 OCI"},
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
