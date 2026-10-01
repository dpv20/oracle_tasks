"""Banco de Chile configuration defaults, isolated from legacy VPN keys."""
from __future__ import annotations

from typing import Any


_MISSING = object()
_PORTAL = "bchmfa.bancochile.cl"


def ensure_bancochile_profile(config: Any) -> None:
    """Create only Banco keys without reading or changing a legacy profile."""
    values: dict[str, Any] = {}
    if config.get("bancochile_username", _MISSING) is _MISSING:
        values["bancochile_username"] = ""
    if config.get("bancochile_password_enc", _MISSING) is _MISSING:
        values["bancochile_password_enc"] = ""
    if config.get("bancochile_portal_url", _MISSING) is _MISSING:
        values["bancochile_portal_url"] = _PORTAL
    if config.get("bancochile_gp_exe_path", _MISSING) is _MISSING:
        values["bancochile_gp_exe_path"] = ""
    if config.get("bancochile_flow_mode", _MISSING) is _MISSING:
        values["bancochile_flow_mode"] = "detect"
    if config.get("bancochile_flow_steps", _MISSING) is _MISSING:
        values["bancochile_flow_steps"] = ["account", "password", "mfa"]
    if config.get("vpn_show_bancochile", _MISSING) is _MISSING:
        values["vpn_show_bancochile"] = True

    if values:
        config.update(values)
