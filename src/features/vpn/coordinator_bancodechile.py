"""Banco de Chile orchestration around the unchanged legacy VPN service.

The legacy :mod:`features.vpn.service` remains the sole owner of Oracle,
Falabella and BICE. This object is a separate Banco-only service. It receives
the already-created legacy service solely to disconnect it before entering
Banco, or to connect a legacy target after Banco has been disconnected.
"""
from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Mapping
from typing import Any

from features.vpn.service import (
    CISCO,
    FORTI,
    GPROT,
    NONE,
    VPNResult,
    status_display_name as _legacy_status_display_name,
)
from settings.config import ConfigManager

log = logging.getLogger(__name__)

BANCOCHILE = "bancochile"
GPROT_UNKNOWN = "globalprotect_unknown"
BANCOCHILE_LEGACY_HANDOFF = "__BANCOCHILE_LEGACY_HANDOFF__"
VPN_TARGETS = (CISCO, FORTI, GPROT, BANCOCHILE, NONE)
LEGACY_TARGETS = (CISCO, FORTI, GPROT, NONE)

ProgressCallback = Callable[[str], None]
SwitcherFactory = Callable[[dict[str, Any]], Any]

_PASSWORD_REQUIRED = "__GP_PASSWORD_REQUIRED__"
_USERNAME_REQUIRED = "__GP_USERNAME_REQUIRED__"
_WRONG_PASSWORD = "__GP_WRONG_PASSWORD__"
_CANCELLED = "__GP_CANCELLED__"

_BANCOCHILE_CONFIG_KEYS = (
    "bancochile_username",
    "bancochile_password_enc",
    "bancochile_portal_url",
    "bancochile_gp_exe_path",
    "bancochile_flow_mode",
    "bancochile_flow_steps",
)


def status_display_name(status: str) -> str:
    """Return the legacy display names plus Banco de Chile's extra states."""
    if status == BANCOCHILE:
        return "Banco de Chile VPN (GlobalProtect)"
    if status == GPROT_UNKNOWN:
        return "GlobalProtect VPN (unknown portal)"
    return _legacy_status_display_name(status)


def _default_switcher_factory(config: dict[str, Any]):
    # Deliberately lazy: importing this module loads pywinauto-facing Banco
    # helpers.  A legacy-only process must never pay that cost or run a probe.
    from features.vpn.switcher_bancodechile import BancoChileSwitcher

    return BancoChileSwitcher(config)


def _emit(callback: ProgressCallback | None, message: str) -> None:
    if callback is None:
        return
    try:
        callback(message)
    except Exception:
        log.exception("VPN progress callback failed")


class BancoChileVPNCoordinator:
    """Banco-only transitions beside, never in front of, the legacy service."""

    def __init__(
        self,
        config: ConfigManager | Mapping[str, Any] | None = None,
        *,
        legacy_service: Any | None = None,
        switcher_factory: SwitcherFactory | None = None,
    ) -> None:
        if legacy_service is None:
            raise TypeError(
                "BancoChileVPNCoordinator requires the app's existing "
                "legacy VPNService instance."
            )
        if config is None:
            config = getattr(legacy_service, "config", None)
        self.config = config if config is not None else ConfigManager()
        self._legacy = legacy_service
        self._switcher_factory = switcher_factory or _default_switcher_factory
        self._switcher = None

        # This lock serializes only transitions that enter or leave Banco.
        self._operation_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._request_lock = threading.Lock()
        self._reserved_operation: object | None = None
        self._active_operation: object | None = None
        self._cancellation_open = False
        self._cancel_requested = threading.Event()
        self._bank_owned = False
        self._bank_connected_confirmed = False
        self._bank_last_status: str | None = None
        self._state_revision = 0
        self._status_probe_failed = False

    @property
    def busy(self) -> bool:
        with self._request_lock:
            return self._reserved_operation is not None

    @property
    def owns_bancochile(self) -> bool:
        """Whether this Banco-only service owns the current GP session."""
        return self._owns_bancochile()

    @property
    def last_status(self) -> str | None:
        with self._state_lock:
            if self._bank_owned:
                return self._bank_last_status or BANCOCHILE
        return None

    def visible_status_snapshot(self, legacy_status: str) -> tuple[int, str]:
        """Atomically label a monitor sample with the current Banco state."""
        with self._state_lock:
            visible = (
                self._bank_last_status or BANCOCHILE
                if self._bank_owned
                else legacy_status
            )
            return self._state_revision, visible

    def reconcile_monitored_status(self, legacy_status: str) -> tuple[int, str]:
        """Reconcile cached Banco ownership with one live monitor sample.

        The legacy monitor deliberately remains the owner of Oracle, Falabella
        and BICE.  A live Banco probe is only needed while Banco is already
        owned, or when the legacy monitor sees an active GlobalProtect tunnel
        that may have been connected manually.  The operation lock keeps this
        read-only reconciliation out of an in-flight connect/disconnect.
        """
        if not self._operation_lock.acquire(blocking=False):
            return self.visible_status_snapshot(legacy_status)
        try:
            owned = self._owns_bancochile()
            if not owned and legacy_status != GPROT:
                return self.visible_status_snapshot(legacy_status)

            # The unchanged legacy monitor has just sampled the same adapter.
            # When it reports no active VPN, reuse that terminal evidence for
            # Banco's debounce instead of spawning another Get-NetAdapter
            # PowerShell process (twice on the manual-disconnect path).
            adapter_hint = "Up" if legacy_status == GPROT else "Disabled"
            banco_status = self._safe_bancochile_status(adapter_hint)
            if self._status_probe_failed:
                return self.visible_status_snapshot(legacy_status)
            if banco_status == BANCOCHILE:
                self._set_owned(True, BANCOCHILE, confirmed=True)
                return self.visible_status_snapshot(BANCOCHILE)

            if owned:
                # NONE means a manual teardown was observed.  UNKNOWN means a
                # different live GlobalProtect portal replaced Banco.  In both
                # cases Banco must release ownership so the unchanged legacy
                # status can become visible again.
                self._set_owned(False, NONE, confirmed=False)
                return self.visible_status_snapshot(legacy_status)

            # A live GlobalProtect tunnel that is not Banco belongs to the
            # original legacy service (normally BICE).
            return self.visible_status_snapshot(legacy_status)
        finally:
            self._operation_lock.release()

    def is_status_snapshot_current(self, revision: int) -> bool:
        with self._state_lock:
            return revision == self._state_revision

    def get_status(self) -> str:
        """Read only a coordinator-owned Banco session."""
        with self._operation_lock:
            if not self._owns_bancochile():
                return NONE
            return self._read_owned_bancochile_status()

    def try_get_status(self) -> str | None:
        """Non-blocking equivalent of :meth:`get_status`."""
        if not self._operation_lock.acquire(blocking=False):
            return None
        try:
            if not self._owns_bancochile():
                return NONE
            return self._read_owned_bancochile_status()
        finally:
            self._operation_lock.release()

    def try_get_monitored_status(self) -> tuple[int, str] | None:
        """Read one unified monitor sample without slowing an owned Banco VPN.

        While Banco owns GlobalProtect, its dedicated native-window probe is
        enough and reacts immediately to a manual teardown. Otherwise the
        unchanged legacy targets are sampled by the legacy service's fast
        background path, then GlobalProtect is classified as Banco or BICE.
        """
        released_banco = False
        if self._owns_bancochile():
            if not self._operation_lock.acquire(blocking=False):
                return self.visible_status_snapshot(NONE)
            try:
                if self._owns_bancochile():
                    banco_status = self._safe_bancochile_status()
                    if self._status_probe_failed:
                        return self.visible_status_snapshot(NONE)
                    if banco_status == BANCOCHILE:
                        self._set_owned(True, BANCOCHILE, confirmed=True)
                        return self.visible_status_snapshot(BANCOCHILE)
                    self._set_owned(False, NONE, confirmed=False)
                    released_banco = True
            finally:
                self._operation_lock.release()

        reader = getattr(
            self._legacy,
            "try_get_monitor_status",
            self._legacy.try_get_status,
        )
        legacy_status = reader()
        if legacy_status is None:
            return self.visible_status_snapshot(NONE) if released_banco else None
        if released_banco:
            return self.visible_status_snapshot(legacy_status)
        return self.reconcile_monitored_status(legacy_status)

    def switch_to(
        self,
        target: str,
        progress: ProgressCallback | None = None,
        *,
        operation_token: object | None = None,
    ) -> VPNResult:
        if target not in VPN_TARGETS:
            return VPNResult(False, f"Unsupported VPN target: {target}")

        token = self._claim_operation(operation_token)
        if token is None:
            return VPNResult(
                False,
                "Another VPN operation is already running.",
                self.last_status or self._legacy.last_status or NONE,
                "busy",
            )
        try:
            with self._operation_lock:
                if target == BANCOCHILE:
                    return self._switch_to_bancochile(progress)

                # A legacy target belongs to this object only while leaving a
                # Banco session. Normal Oracle/Falabella/BICE actions call the
                # original VPNService directly and can never arrive here.
                if not self._owns_bancochile():
                    return VPNResult(
                        False,
                        "Banco de Chile VPN is not active; use the legacy VPN service.",
                        self._legacy.last_status or NONE,
                    )

                return self._switch_from_bancochile(target, progress)
        finally:
            self._release_operation(token)

    def prepare_bancochile_operation(self) -> object | None:
        """Arm cancellation before the Banco worker thread is started.

        The view calls this synchronously. It closes the small window where a
        user could press Cancel after the UI became busy but before
        :meth:`switch_to` entered the Banco-specific code.
        """
        with self._request_lock:
            if (
                self._reserved_operation is not None
                or self._operation_lock.locked()
                or bool(self._legacy.busy)
            ):
                return None
            token = object()
            self._reserved_operation = token
            self._cancellation_open = True
            self._cancel_requested.clear()
            self._set_switcher_cancel(False)
            return token

    def abandon_prepared_operation(self, token: object | None) -> bool:
        """Release a reservation when its worker could not be started."""
        if token is None:
            return False
        with self._request_lock:
            if (
                token is not self._reserved_operation
                or self._active_operation is not None
            ):
                return False
            self._reserved_operation = None
            self._cancellation_open = False
            self._cancel_requested.clear()
            self._set_switcher_cancel(False)
            return True

    def cancel_current(self, operation_token: object | None = None) -> None:
        """Cancel only a pending/active Banco operation; legacy stays untouched."""
        with self._request_lock:
            token = operation_token or self._active_operation
            if (
                token is None
                or token is not self._reserved_operation
                or not self._cancellation_open
            ):
                return
            self._cancel_requested.set()
            switcher = self._switcher
            if switcher is not None:
                cancel = getattr(switcher, "cancel_current", None)
                if callable(cancel):
                    cancel()
            self._set_switcher_cancel(True)

    # -- Banco transition -------------------------------------------------

    def _switch_to_bancochile(
        self,
        progress: ProgressCallback | None,
    ) -> VPNResult:
        if self._cancel_requested.is_set():
            return self._cancelled_result(self._legacy.last_status)

        resume_owned_attempt = self._owns_bancochile()
        if resume_owned_attempt:
            status = self._read_owned_bancochile_status()
            if status == BANCOCHILE and self._is_bancochile_confirmed():
                return VPNResult(True, "VPN is already connected.", BANCOCHILE)
            if status == GPROT_UNKNOWN:
                return VPNResult(
                    False,
                    "Could not identify the active GlobalProtect portal. "
                    "No disconnect action was sent.",
                    GPROT_UNKNOWN,
                    "gp_portal_unknown",
                )
            resume_owned_attempt = status == BANCOCHILE

        # Legacy owns all legacy detection/disconnection. Banco code is not
        # involved until the shared fast monitor sample identifies the active
        # adapter. This avoids a stale PanGPA Connected label making the legacy
        # BICE disconnect path toggle a tunnel that is already down.
        if not resume_owned_attempt:
            legacy_status = self._read_legacy_monitor_status()
            if legacy_status is None:
                return VPNResult(
                    False,
                    "Could not verify the current VPN adapter state. "
                    "No connection or disconnect action was sent.",
                    self.last_status or self._legacy.last_status or NONE,
                    "vpn_status_unknown",
                )
            if legacy_status == GPROT:
                banco_status = self._safe_bancochile_status("Up")
                if self._status_probe_failed:
                    return VPNResult(
                        False,
                        "Could not identify the active GlobalProtect portal. "
                        "No disconnect action was sent.",
                        GPROT_UNKNOWN,
                        "gp_portal_unknown",
                    )
                if banco_status == BANCOCHILE:
                    self._set_owned(True, BANCOCHILE, confirmed=True)
                    return VPNResult(True, "VPN is already connected.", BANCOCHILE)
                if banco_status == GPROT_UNKNOWN:
                    return VPNResult(
                        False,
                        "Could not identify the active GlobalProtect portal. "
                        "No disconnect action was sent.",
                        GPROT_UNKNOWN,
                        "gp_portal_unknown",
                    )
            if legacy_status != NONE:
                legacy_result = self._legacy.switch_to(NONE, progress)
                if not legacy_result.ok:
                    return legacy_result
            if self._cancel_requested.is_set():
                return self._cancelled_result(NONE)

        try:
            switcher = self._get_switcher()
        except Exception as exc:
            log.exception("Could not initialize Banco de Chile VPN automation")
            self._set_owned(False, NONE, confirmed=False)
            return VPNResult(
                False,
                f"Could not initialize Banco de Chile VPN automation: {exc}",
                NONE,
            )
        if self._cancel_requested.is_set():
            self._set_switcher_cancel(True)
            status = self._safe_bancochile_status()
            self._set_owned(status != NONE, status, confirmed=False)
            return self._cancelled_result(status)
        self._set_owned(True, BANCOCHILE, confirmed=False)
        _emit(progress, f"Connecting {status_display_name(BANCOCHILE)}...")
        try:
            ok, message = switcher.connect_bancochile(progress=progress)
        except Exception as exc:
            log.exception("Banco de Chile VPN connection failed unexpectedly")
            status = self._safe_bancochile_status()
            self._set_owned(status != NONE, status, confirmed=False)
            return VPNResult(False, str(exc), status)

        if self._cancel_requested.is_set() or message == _CANCELLED:
            status = self._safe_bancochile_status()
            self._set_owned(status != NONE, status, confirmed=False)
            return self._cancelled_result(status)

        error = self._connection_error(message)
        if error is not None:
            status = self._safe_bancochile_status()
            self._set_owned(status != NONE, status, confirmed=False)
            return VPNResult(False, error[0], status, error[1])

        if not ok:
            status = self._safe_bancochile_status()
            self._set_owned(status != NONE, status, confirmed=False)
            return VPNResult(False, message, status)

        if not self._close_cancellation_phase():
            # A successful switcher result already means the configured portal
            # and a live adapter at Up were both confirmed. Preserve that real
            # state even if cancellation arrived immediately after the tunnel
            # completed; no extra status probe is needed here.
            self._set_owned(True, BANCOCHILE, confirmed=True)
            return self._cancelled_result(BANCOCHILE)
        self._set_owned(True, BANCOCHILE, confirmed=True)
        return VPNResult(True, message, BANCOCHILE)

    def _switch_from_bancochile(
        self,
        target: str,
        progress: ProgressCallback | None,
    ) -> VPNResult:
        if self._cancel_requested.is_set():
            return self._cancelled_result(BANCOCHILE)

        _emit(progress, f"Disconnecting {status_display_name(BANCOCHILE)}...")
        disconnected = self._disconnect_owned_bancochile()
        if not disconnected.ok:
            return disconnected
        if not self._close_cancellation_phase():
            return self._cancelled_result(NONE)

        # Banco is confirmed down. Cancellation remains associated with this
        # request but is no longer presented once the exact legacy service takes
        # control; it never propagates into that service.
        _emit(progress, BANCOCHILE_LEGACY_HANDOFF)
        return self._legacy.switch_to(target, progress)

    def _disconnect_owned_bancochile(self) -> VPNResult:
        switcher = self._get_switcher()
        status = self._safe_bancochile_status()
        if status == NONE:
            self._set_owned(False, NONE, confirmed=False)
            return VPNResult(True, "Banco de Chile VPN is already disconnected.", NONE)
        if status == GPROT_UNKNOWN:
            return VPNResult(
                False,
                "Could not identify the active GlobalProtect portal. "
                "No disconnect action was sent.",
                GPROT_UNKNOWN,
                "gp_portal_unknown",
            )
        try:
            ok, message = switcher.disconnect_bancochile()
        except Exception as exc:
            log.exception("Banco de Chile VPN disconnect failed unexpectedly")
            status = self._safe_bancochile_status()
            self._set_owned(status != NONE, status, confirmed=False)
            return VPNResult(False, str(exc), status)
        if self._cancel_requested.is_set() or message == _CANCELLED:
            status = self._safe_bancochile_status()
            self._set_owned(status != NONE, status, confirmed=False)
            return self._cancelled_result(status)
        if not ok:
            status = self._safe_bancochile_status()
            self._set_owned(status != NONE, status, confirmed=False)
            return VPNResult(False, message, status)
        # ``disconnect_bancochile`` returns success only after the switcher has
        # observed two terminal adapter samples.  Do not immediately perform a
        # third independent probe here: a transient Get-NetAdapter failure (or a
        # stale PanGPA frame) must not turn that strong confirmation into a false
        # failure and leave the coordinator stuck owning Banco.
        self._set_owned(False, NONE, confirmed=False)
        return VPNResult(True, message, NONE)

    # -- Switcher ownership/config/cancellation --------------------------

    def _runtime_config(self) -> dict[str, Any]:
        source = getattr(self.config, "data", self.config)
        if not isinstance(source, Mapping):
            return {}
        return {key: source[key] for key in _BANCOCHILE_CONFIG_KEYS if key in source}

    def _get_switcher(self):
        runtime = self._runtime_config()
        if self._switcher is None:
            self._switcher = self._switcher_factory(runtime)
        self._switcher.config = runtime
        return self._switcher

    def _claim_operation(self, prepared_token: object | None) -> object | None:
        """Atomically claim one Banco request without blocking another request."""
        with self._request_lock:
            if prepared_token is not None:
                if (
                    prepared_token is not self._reserved_operation
                    or self._active_operation is not None
                ):
                    return None
                token = prepared_token
            else:
                if self._reserved_operation is not None or bool(self._legacy.busy):
                    return None
                token = object()
                self._reserved_operation = token
                self._cancellation_open = True
                self._cancel_requested.clear()
                self._set_switcher_cancel(False)
            self._active_operation = token
        # Invalidate any legacy monitor sample captured before this Banco-only
        # transition began, even when the attempt fails before ownership changes.
        self._advance_state_revision()
        return token

    def _release_operation(self, token: object) -> None:
        with self._request_lock:
            if token is not self._reserved_operation:
                return
            self._active_operation = None
            self._reserved_operation = None
            self._cancellation_open = False
            self._cancel_requested.clear()
            self._set_switcher_cancel(False)
        # A sample captured while Banco was disconnecting (for example after
        # ownership became NONE but before the legacy connect completed) must
        # not be applied after the transition has finished.
        self._advance_state_revision()

    def _close_cancellation_phase(self) -> bool:
        """Atomically close cancellation before returning or invoking legacy."""
        with self._request_lock:
            if self._cancel_requested.is_set():
                return False
            self._cancellation_open = False
            self._cancel_requested.clear()
            self._set_switcher_cancel(False)
            return True

    def _set_switcher_cancel(self, value: bool) -> None:
        # Do not import Banco solely to clear an event before first use.
        if self._switcher is None:
            return
        setter = getattr(self._switcher, "set_cancelled", None)
        if callable(setter):
            setter(value)
            return
        try:
            from features.vpn import switcher_bancodechile

            event = getattr(switcher_bancodechile, "_autofill_cancel", None)
            if event is not None:
                event.set() if value else event.clear()
        except Exception:
            log.exception("Could not update Banco de Chile cancellation state")

    def _owns_bancochile(self) -> bool:
        with self._state_lock:
            return self._bank_owned

    def _advance_state_revision(self) -> None:
        with self._state_lock:
            self._state_revision += 1

    def _is_bancochile_confirmed(self) -> bool:
        with self._state_lock:
            return self._bank_owned and self._bank_connected_confirmed

    def _set_owned(
        self,
        owned: bool,
        status: str | None = None,
        *,
        confirmed: bool | None = None,
    ) -> None:
        with self._state_lock:
            next_status = status if owned else None
            next_confirmed = (
                False
                if not owned
                else self._bank_connected_confirmed
                if confirmed is None
                else confirmed
            )
            if (
                self._bank_owned != owned
                or self._bank_last_status != next_status
                or self._bank_connected_confirmed != next_confirmed
            ):
                self._state_revision += 1
            self._bank_owned = owned
            self._bank_last_status = next_status
            self._bank_connected_confirmed = next_confirmed

    def _safe_bancochile_status(self, adapter_status: str = "") -> str:
        self._status_probe_failed = False
        try:
            switcher = self._get_switcher()
            if adapter_status:
                status = str(
                    switcher.get_bancochile_status(adapter_status=adapter_status)
                )
            else:
                status = str(switcher.get_bancochile_status())
            if getattr(switcher, "last_status_probe_ok", None) is False:
                self._status_probe_failed = True
        except Exception:
            self._status_probe_failed = True
            log.exception("Banco de Chile status detection failed")
            return GPROT_UNKNOWN
        if status in (BANCOCHILE, GPROT_UNKNOWN, NONE):
            return status
        log.warning("Unexpected Banco de Chile status: %r", status)
        return GPROT_UNKNOWN

    def _read_legacy_monitor_status(self) -> str | None:
        reader = getattr(self._legacy, "get_monitor_status", None)
        if callable(reader):
            status = reader()
            return None if status is None else str(status)
        quick_reader = getattr(self._legacy, "try_get_monitor_status", None)
        if callable(quick_reader):
            status = quick_reader()
            if status is not None:
                return str(status)
        return str(self._legacy.get_status())

    def _read_owned_bancochile_status(self) -> str:
        status = self._safe_bancochile_status()
        if self._status_probe_failed:
            with self._state_lock:
                return self._bank_last_status or BANCOCHILE
        if status == NONE:
            self._set_owned(False, NONE, confirmed=False)
            return NONE
        with self._state_lock:
            if self._bank_last_status != status:
                self._state_revision += 1
            self._bank_last_status = status
        return status

    @staticmethod
    def _connection_error(message: str) -> tuple[str, str] | None:
        errors = {
            _PASSWORD_REQUIRED: (
                "Banco de Chile VPN (GlobalProtect) requested a password. "
                "Save it in Settings > VPN and retry.",
                "gp_password_required",
            ),
            _USERNAME_REQUIRED: (
                "Banco de Chile VPN (GlobalProtect) requested an email address. "
                "Save it in Settings > VPN and retry.",
                "gp_username_required",
            ),
            _WRONG_PASSWORD: (
                "Banco de Chile VPN (GlobalProtect) rejected the saved password. "
                "Update it in Settings > VPN.",
                "gp_wrong_password",
            ),
            "Microsoft denied the MFA sign-in request.": (
                "Microsoft denied the MFA sign-in request.",
                "gp_mfa_denied",
            ),
            "Timed out waiting for Microsoft Authenticator approval.": (
                "Timed out waiting for Microsoft Authenticator approval.",
                "gp_mfa_timeout",
            ),
        }
        return errors.get(message)

    @staticmethod
    def _cancelled_result(status: str | None = None) -> VPNResult:
        visible_status = status if status in (*VPN_TARGETS, GPROT_UNKNOWN) else NONE
        return VPNResult(
            False,
            "VPN operation was cancelled.",
            visible_status,
            "cancelled",
        )

__all__ = [
    "BANCOCHILE",
    "BANCOCHILE_LEGACY_HANDOFF",
    "CISCO",
    "FORTI",
    "GPROT",
    "GPROT_UNKNOWN",
    "NONE",
    "VPNResult",
    "VPN_TARGETS",
    "BancoChileVPNCoordinator",
    "status_display_name",
]
