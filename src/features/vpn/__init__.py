"""VPN feature: controller, service, settings panel, and view."""

from .service import CISCO, FORTI, GPROT, NONE, VPNResult, VPNService

__all__ = [
    "CISCO",
    "FORTI",
    "GPROT",
    "NONE",
    "VPNResult",
    "VPNService",
]
