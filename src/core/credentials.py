"""Credential parser + bucket inference.

Supports two formats:
    user/pass@DB
    user[schema]/pass@DB    (SQLcl proxy auth)

Inference rules from TNS name (case-insensitive):
- country: substring match for chile/peru/colombia
- env:     keyword match — order matters (BUP wins over PROD/QA prefix)
            "BUP*PROD" -> user_bup_prod
            "BUP*QA"   -> user_bup_qa
            "*DEV*"    -> user_dev
            "*QA*"     -> user_qa
            "*PROD*"   -> shared_prod
- TNS-name fallbacks for production (no PROD substring): explicit list below.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .config import encrypt_password

CRED_PATTERN = r"([A-Za-z0-9_]+)(?:\[([A-Za-z0-9_]+)\])?/([^@\s]+)@([A-Za-z0-9_]+)"
CRED_RE = re.compile(rf"^{CRED_PATTERN}$")
CRED_SEARCH_RE = re.compile(CRED_PATTERN)

# TNS names that don't contain "PROD" but ARE production
PROD_TNS_OVERRIDES = {
    "FXBFCL_19C_PROD_OCI",      # Chile prod
    "FXBFCL_19C_PROD_OCI_DR",
    "BFCO_POCISANTIAGO",         # Colombia prod
    "BFCO_POCISAOPALO",
    "BUP_CL_2024",               # Chile BUP prod
    "PERUBUPOCIPROD",
    "PERUBUPOCIPRODDR",
    "MX_PROD_OCI",
    "MX_PROD_OCI_DR",
}

# Explicit TNS → country mapping for names that the heuristic can't infer.
# Anything not here falls through to substring matching in _infer_country.
TNS_TO_COUNTRY = {
    # Chile
    "CHILE_DEV": "chile",
    "CHILE_QA_19C": "chile",
    "CHILE_QA4_OCI": "chile",
    "BUP_QA_CL": "chile",
    "BUP_CL_2024": "chile",
    "FXBFCL_19C_PROD_OCI": "chile",
    "FXBFCL_19C_PROD_OCI_DR": "chile",
    # Colombia
    "COL_DEV": "colombia",
    "COL_QA_INT_OCI": "colombia",
    "BFCO_POCISANTIAGO": "colombia",
    "BFCO_POCISAOPALO": "colombia",
    # Peru
    "PERU_DEV": "peru",
    "PERU_QA_OCI_19C": "peru",
    "PERU_OCI_PROD": "peru",
    "PERUBUPOCIQA": "peru",
    "PERUBUPOCIPROD": "peru",
    "PERUBUPOCIPRODDR": "peru",
    # Mexico
    "MEXICO_QA_OCI": "mexico",
    "MX_PROD_OCI": "mexico",
    "MX_PROD_OCI_DR": "mexico",
}


@dataclass
class ParsedCredential:
    user: str
    schema: str | None
    password: str          # PLAIN — caller must encrypt before persisting
    tns: str
    country: str | None    # "chile" | "peru" | "colombia" | None
    bucket: str | None     # "shared_prod" | "user_qa" | ... | None
    raw: str

    @property
    def is_complete(self) -> bool:
        return bool(self.country and self.bucket)


def parse(line: str) -> ParsedCredential | None:
    """Parse one line. Returns None if it doesn't match the credential regex."""
    s = line.strip()
    if not s:
        return None
    m = CRED_RE.match(s) or CRED_SEARCH_RE.search(s)
    if not m:
        return None
    user, schema, password, tns = m.groups()
    if user.lower() == "username" and password.lower() == "password":
        return None
    country = _infer_country(tns)
    bucket = _infer_bucket(tns)
    return ParsedCredential(user, schema, password, tns, country, bucket, s)


def parse_many(text: str) -> tuple[list[ParsedCredential], list[str]]:
    """Parse a multiline text. Returns (parsed, unparsed_lines)."""
    parsed: list[ParsedCredential] = []
    unparsed: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        c = parse(stripped)
        if c is None:
            low = stripped.lower()
            if "/" not in stripped or "@" not in stripped or "username/password@" in low:
                continue
            unparsed.append(stripped)
        elif not c.is_complete:
            unparsed.append(stripped)
        else:
            parsed.append(c)
    return parsed, unparsed


def to_sqlcl_arg(user: str, schema: str | None, password: str, tns: str) -> str:
    """Build the connection string SQLcl expects: user[schema]/pass@DB."""
    proxy = f"[{schema}]" if schema else ""
    return f"{user}{proxy}/{password}@{tns}"


def credential_dict(c: ParsedCredential, bucket: str | None = None, tns: str | None = None) -> dict[str, str]:
    """Shape a ParsedCredential as the dict we persist in config.json."""
    db_name = tns or c.tns
    return {
        "user": c.user,
        "schema": c.schema or "",
        "password_enc": encrypt_password(c.password),
        "tns": db_name,
        "bucket": bucket or c.bucket or "",
    }


# ── inference ────────────────────────────────────────────────────────────────

def _infer_country(tns: str) -> str | None:
    up = tns.upper()
    if up in TNS_TO_COUNTRY:
        return TNS_TO_COUNTRY[up]
    if "CHILE" in up or "_CL_" in up or up.endswith("_CL"):
        return "chile"
    if "PERU" in up or "_PE_" in up:
        return "peru"
    if "COL" in up or "_CO_" in up:
        return "colombia"
    if "MEXICO" in up or "_MX_" in up or up.startswith("MX_") or up.startswith("MEXICO"):
        return "mexico"
    return None


def _infer_bucket(tns: str) -> str | None:
    up = tns.upper()
    is_bup = "BUP" in up
    is_prod = ("PROD" in up) or (up in PROD_TNS_OVERRIDES)
    is_qa = "QA" in up
    is_dev = "DEV" in up

    if is_bup and is_prod:
        return "user_bup_prod"
    if is_bup and is_qa:
        return "user_bup_qa"
    if is_dev:
        return "user_dev"
    if is_qa:
        return "user_qa"
    if is_prod:
        return "shared_prod"
    return None
