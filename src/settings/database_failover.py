"""Known pairs of equivalent PROD databases used by supported workflows."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class DatabaseFailoverPair:
    key: str
    country: str
    first_alias: str
    second_alias: str

    @property
    def aliases(self) -> tuple[str, str]:
        return self.first_alias, self.second_alias


# These are explicit business relationships. Do not infer equivalence from
# alias names such as PROD/DR; other similarly named databases may differ.
DATABASE_FAILOVER_PAIRS: tuple[DatabaseFailoverPair, ...] = (
    DatabaseFailoverPair(
        key="chile_prod_oci",
        country="chile",
        first_alias="FXBFCL_19C_PROD_OCI",
        second_alias="FXBFCL_19C_PROD_OCI_DR",
    ),
    DatabaseFailoverPair(
        key="colombia_prod_oci",
        country="colombia",
        first_alias="BFCO_POCISANTIAGO",
        second_alias="BFCO_POCISAOPALO",
    ),
)

DEFAULT_DATABASE_FAILOVER: dict[str, bool] = {
    pair.key: True for pair in DATABASE_FAILOVER_PAIRS
}

DEFAULT_DATABASE_PREFERENCES: dict[str, str] = {
    # Night Shift historically connected directly to the Chile DR endpoint.
    # Keep it as the first choice while retaining the normal endpoint as failover.
    "chile": "FXBFCL_19C_PROD_OCI_DR",
    "colombia": "BFCO_POCISANTIAGO",
}


def normalize_database_preferences(
    values: Mapping[str, object] | None,
) -> dict[str, str]:
    """Return only valid, explicitly paired aliases, with safe defaults."""
    raw = values if isinstance(values, Mapping) else {}
    normalized: dict[str, str] = {}
    for pair in DATABASE_FAILOVER_PAIRS:
        aliases = {alias.upper(): alias for alias in pair.aliases}
        default_alias = DEFAULT_DATABASE_PREFERENCES.get(pair.country, pair.first_alias)
        selected = str(raw.get(pair.country, default_alias) or "").strip().upper()
        normalized[pair.country] = aliases.get(selected, default_alias)
    return normalized


def alternative_alias(
    country: str,
    selected_alias: str,
    enabled_pairs: Mapping[str, object] | None,
) -> str | None:
    """Return the other alias in an enabled, explicitly known pair."""
    country_key = (country or "").strip().lower()
    alias_key = (selected_alias or "").strip().upper()
    flags = enabled_pairs or {}
    for pair in DATABASE_FAILOVER_PAIRS:
        if pair.country != country_key or not bool(flags.get(pair.key, False)):
            continue
        first, second = (alias.upper() for alias in pair.aliases)
        if alias_key == first:
            return pair.second_alias
        if alias_key == second:
            return pair.first_alias
    return None
