"""Logging compatibility for the migrated VPN controller."""
from __future__ import annotations

import logging


def get_logger() -> logging.Logger:
    return logging.getLogger("oracle_tasks.vpn")
