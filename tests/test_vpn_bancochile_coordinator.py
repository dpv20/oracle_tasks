from __future__ import annotations

import threading
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock


SRC_DIR = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_DIR))

from features.vpn.coordinator_bancodechile import (  # noqa: E402
    BANCOCHILE,
    BANCOCHILE_LEGACY_HANDOFF,
    CISCO,
    FORTI,
    GPROT,
    NONE,
    BancoChileVPNCoordinator,
    VPNResult,
)


class _LegacyService:
    def __init__(self) -> None:
        self.config = {"source": "legacy"}
        self.busy = False
        self.last_status = NONE
        self.calls: list[tuple] = []
        self.switch_results: dict[str, VPNResult] = {}
        self.get_status_result = NONE
        self.try_get_status_result = NONE
        self.retry_result = VPNResult(True, "legacy retry", FORTI)

    def switch_to(self, target, progress=None):
        self.calls.append(("switch_to", target, progress))
        result = self.switch_results.get(
            target,
            VPNResult(True, f"legacy {target}", target),
        )
        self.last_status = result.status
        return result

    def get_status(self):
        self.calls.append(("get_status",))
        self.last_status = self.get_status_result
        return self.get_status_result

    def try_get_status(self):
        self.calls.append(("try_get_status",))
        self.last_status = self.try_get_status_result
        return self.try_get_status_result

    def retry_forti_credentials(self):
        self.calls.append(("retry_forti_credentials",))
        self.last_status = self.retry_result.status
        return self.retry_result

    def start_monitor(self, callback):
        self.calls.append(("start_monitor", callback))

    def stop_monitor(self):
        self.calls.append(("stop_monitor",))


class _BancoSwitcher:
    def __init__(self, config, events=None) -> None:
        self.config = config
        self.events = events if events is not None else []
        self.connect_result = (True, "Banco connected")
        self.disconnect_result = (True, "Banco disconnected")
        self.status = BANCOCHILE
        self.cancel_values: list[bool] = []
        self.cancel_current_calls = 0

    def connect_bancochile(self, progress=None):
        self.events.append(("bank_connect", progress))
        return self.connect_result

    def disconnect_bancochile(self):
        self.events.append(("bank_disconnect",))
        if self.disconnect_result[0]:
            self.status = NONE
        return self.disconnect_result

    def get_bancochile_status(self):
        self.events.append(("bank_status",))
        return self.status

    def cancel_current(self):
        self.cancel_current_calls += 1

    def set_cancelled(self, value):
        self.cancel_values.append(bool(value))


class BancoChileCoordinatorIsolationTests(unittest.TestCase):
    def _coordinator(self):
        legacy = _LegacyService()
        created: list[_BancoSwitcher] = []

        def factory(config):
            switcher = _BancoSwitcher(config)
            created.append(switcher)
            return switcher

        coordinator = BancoChileVPNCoordinator(
            {},
            legacy_service=legacy,
            switcher_factory=factory,
        )
        return coordinator, legacy, created

    def test_inactive_legacy_targets_are_rejected_without_delegation(self) -> None:
        progress = lambda _message: None
        for target in (CISCO, FORTI, GPROT, NONE):
            with self.subTest(target=target):
                coordinator, legacy, created = self._coordinator()

                actual = coordinator.switch_to(target, progress)

                self.assertFalse(actual.ok)
                self.assertIn("use the legacy VPN service", actual.message)
                self.assertEqual(legacy.calls, [])
                self.assertEqual(created, [])

    def test_idle_banco_status_never_calls_or_monitors_legacy(self) -> None:
        coordinator, legacy, created = self._coordinator()
        legacy.get_status_result = CISCO
        legacy.try_get_status_result = GPROT

        self.assertEqual(coordinator.get_status(), NONE)
        self.assertEqual(coordinator.try_get_status(), NONE)
        self.assertFalse(hasattr(coordinator, "retry_forti_credentials"))
        self.assertFalse(hasattr(coordinator, "start_monitor"))
        self.assertEqual(legacy.calls, [])
        self.assertEqual(created, [])

    def test_monitor_snapshot_becomes_stale_when_banco_ownership_changes(self) -> None:
        coordinator, _legacy, _created = self._coordinator()

        revision, status = coordinator.visible_status_snapshot(CISCO)
        self.assertEqual(status, CISCO)
        self.assertTrue(coordinator.is_status_snapshot_current(revision))

        coordinator._set_owned(True, BANCOCHILE, confirmed=False)

        self.assertFalse(coordinator.is_status_snapshot_current(revision))
        new_revision, new_status = coordinator.visible_status_snapshot(CISCO)
        self.assertEqual(new_status, BANCOCHILE)
        self.assertTrue(coordinator.is_status_snapshot_current(new_revision))

    def test_monitor_snapshots_are_invalidated_at_banco_operation_boundaries(self) -> None:
        coordinator, _legacy, _created = self._coordinator()

        before_revision, _status = coordinator.visible_status_snapshot(CISCO)
        token = coordinator._claim_operation(None)
        self.assertIsNotNone(token)
        self.assertFalse(coordinator.is_status_snapshot_current(before_revision))

        during_revision, _status = coordinator.visible_status_snapshot(NONE)
        coordinator._release_operation(token)

        self.assertFalse(coordinator.is_status_snapshot_current(during_revision))

    def test_abandoned_prepared_operation_can_be_reserved_again(self) -> None:
        coordinator, legacy, created = self._coordinator()
        token = coordinator.prepare_bancochile_operation()

        self.assertTrue(coordinator.abandon_prepared_operation(token))
        self.assertFalse(coordinator.busy)
        self.assertIsNotNone(coordinator.prepare_bancochile_operation())
        self.assertEqual(legacy.calls, [])
        self.assertEqual(created, [])

    def test_switcher_receives_only_bancochile_configuration(self) -> None:
        legacy = _LegacyService()
        captured = []
        coordinator = BancoChileVPNCoordinator(
            {
                "bancochile_username": "bank-user",
                "bancochile_password_enc": "bank-secret",
                "bancochile_portal_url": "bchmfa.bancochile.cl",
                "bancochile_gp_exe_path": "PanGPA.exe",
                "gp_username": "bice-user",
                "gp_password_enc": "bice-secret",
                "gp_portal_url": "ext.bice.cl",
            },
            legacy_service=legacy,
            switcher_factory=lambda config: captured.append(dict(config))
            or _BancoSwitcher(config),
        )

        coordinator._get_switcher()

        self.assertEqual(
            captured,
            [
                {
                    "bancochile_username": "bank-user",
                    "bancochile_password_enc": "bank-secret",
                    "bancochile_portal_url": "bchmfa.bancochile.cl",
                    "bancochile_gp_exe_path": "PanGPA.exe",
                }
            ],
        )

    def test_connecting_bancochile_disconnects_legacy_then_uses_switcher(self) -> None:
        events: list[tuple] = []
        legacy = _LegacyService()

        def legacy_switch(target, progress=None):
            events.append(("legacy_switch", target, progress))
            return VPNResult(True, "all legacy disconnected", NONE)

        legacy.switch_to = legacy_switch
        switcher = _BancoSwitcher({"initial": True}, events)
        coordinator = BancoChileVPNCoordinator(
            {"bancochile_username": "user@example.test"},
            legacy_service=legacy,
            switcher_factory=lambda config: switcher,
        )
        progress = lambda _message: None

        result = coordinator.switch_to(BANCOCHILE, progress)

        self.assertEqual(
            events,
            [
                ("legacy_switch", NONE, progress),
                ("bank_connect", progress),
                ("bank_status",),
            ],
        )
        self.assertEqual(result, VPNResult(True, "Banco connected", BANCOCHILE))
        self.assertEqual(coordinator.last_status, BANCOCHILE)
        self.assertEqual(
            switcher.config,
            {"bancochile_username": "user@example.test"},
        )

    def test_leaving_owned_bancochile_disconnects_before_exact_legacy_result(self) -> None:
        events: list[tuple] = []
        legacy = _LegacyService()
        expected = VPNResult(False, "legacy sentinel", CISCO, "legacy-code")

        def legacy_switch(target, progress=None):
            events.append(("legacy_switch", target, progress))
            if target == NONE:
                return VPNResult(True, "legacy down", NONE)
            return expected

        legacy.switch_to = legacy_switch
        switcher = _BancoSwitcher({}, events)
        coordinator = BancoChileVPNCoordinator(
            {},
            legacy_service=legacy,
            switcher_factory=lambda config: switcher,
        )
        coordinator.switch_to(BANCOCHILE)
        events.clear()
        progress = lambda _message: None

        actual = coordinator.switch_to(CISCO, progress)

        self.assertIs(actual, expected)
        self.assertEqual(
            events,
            [
                ("bank_status",),
                ("bank_disconnect",),
                ("bank_status",),
                ("legacy_switch", CISCO, progress),
            ],
        )

    def test_password_username_wrong_password_and_mfa_errors_are_mapped(self) -> None:
        cases = (
            ("__GP_PASSWORD_REQUIRED__", "gp_password_required"),
            ("__GP_USERNAME_REQUIRED__", "gp_username_required"),
            ("__GP_WRONG_PASSWORD__", "gp_wrong_password"),
            ("Microsoft denied the MFA sign-in request.", "gp_mfa_denied"),
            (
                "Timed out waiting for Microsoft Authenticator approval.",
                "gp_mfa_timeout",
            ),
        )
        for message, error_code in cases:
            with self.subTest(message=message):
                legacy = _LegacyService()
                switcher = _BancoSwitcher({})
                switcher.connect_result = (False, message)
                switcher.status = NONE
                coordinator = BancoChileVPNCoordinator(
                    {},
                    legacy_service=legacy,
                    switcher_factory=lambda config, item=switcher: item,
                )

                result = coordinator.switch_to(BANCOCHILE)

                self.assertFalse(result.ok)
                self.assertEqual(result.error_code, error_code)
                self.assertEqual(result.status, NONE)

    def test_retry_after_password_prompt_resumes_banco_without_touching_legacy(self) -> None:
        legacy = _LegacyService()
        switcher = _BancoSwitcher({})
        attempts = iter(
            (
                (False, "__GP_PASSWORD_REQUIRED__"),
                (True, "Banco connected"),
            )
        )

        def connect(progress=None):
            switcher.events.append(("bank_connect", progress))
            return next(attempts)

        switcher.connect_bancochile = connect
        coordinator = BancoChileVPNCoordinator(
            {},
            legacy_service=legacy,
            switcher_factory=lambda config: switcher,
        )

        first = coordinator.switch_to(BANCOCHILE)
        second = coordinator.switch_to(BANCOCHILE)

        self.assertEqual(first.error_code, "gp_password_required")
        self.assertTrue(second.ok)
        self.assertEqual(second.status, BANCOCHILE)
        self.assertEqual(
            [call for call in legacy.calls if call[0] == "switch_to"],
            [("switch_to", NONE, None)],
        )

    def test_cancel_does_not_reach_banco_after_legacy_handoff_begins(self) -> None:
        switcher = _BancoSwitcher({})
        legacy = _LegacyService()
        coordinator = BancoChileVPNCoordinator(
            {},
            legacy_service=legacy,
            switcher_factory=lambda config: switcher,
        )
        self.assertTrue(coordinator.switch_to(BANCOCHILE).ok)

        expected = VPNResult(True, "Oracle connected", CISCO)

        def legacy_switch(target, progress=None):
            coordinator.cancel_current()
            return expected

        legacy.switch_to = legacy_switch
        actual = coordinator.switch_to(CISCO)

        self.assertIs(actual, expected)
        self.assertEqual(switcher.cancel_current_calls, 0)

    def test_switcher_initialization_failure_does_not_capture_legacy_targets(self) -> None:
        legacy = _LegacyService()
        coordinator = BancoChileVPNCoordinator(
            {},
            legacy_service=legacy,
            switcher_factory=lambda _config: (_ for _ in ()).throw(
                RuntimeError("switcher unavailable")
            ),
        )

        failed = coordinator.switch_to(BANCOCHILE)
        expected = VPNResult(True, "legacy cisco", CISCO)
        legacy.switch_results[CISCO] = expected
        rejected = coordinator.switch_to(CISCO)
        recovered = legacy.switch_to(CISCO)

        self.assertFalse(failed.ok)
        self.assertIn("switcher unavailable", failed.message)
        self.assertFalse(rejected.ok)
        self.assertIs(recovered, expected)

    def test_unknown_portal_is_never_disconnected_by_banco_switcher(self) -> None:
        events: list[tuple] = []
        legacy = _LegacyService()
        switcher = _BancoSwitcher({}, events)
        coordinator = BancoChileVPNCoordinator(
            {},
            legacy_service=legacy,
            switcher_factory=lambda config: switcher,
        )
        self.assertTrue(coordinator.switch_to(BANCOCHILE).ok)
        events.clear()
        legacy.calls.clear()
        switcher.status = "globalprotect_unknown"

        result = coordinator.switch_to(CISCO)

        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "gp_portal_unknown")
        self.assertEqual(events, [("bank_status",)])
        self.assertEqual(legacy.calls, [])

    def test_cancel_is_noop_for_legacy_but_reaches_active_banco_switcher(self) -> None:
        coordinator, legacy, created = self._coordinator()
        coordinator.cancel_current()
        self.assertEqual(created, [])
        self.assertEqual(legacy.calls, [])

        switcher = _BancoSwitcher({})
        started = threading.Event()
        release = threading.Event()

        def blocking_connect(progress=None):
            started.set()
            release.wait(timeout=2)
            return False, "__GP_CANCELLED__"

        switcher.connect_bancochile = blocking_connect
        coordinator = BancoChileVPNCoordinator(
            {},
            legacy_service=_LegacyService(),
            switcher_factory=lambda config: switcher,
        )
        result_holder = []
        worker = threading.Thread(
            target=lambda: result_holder.append(coordinator.switch_to(BANCOCHILE))
        )
        worker.start()
        self.assertTrue(started.wait(timeout=1))

        coordinator.cancel_current()
        release.set()
        worker.join(timeout=2)

        self.assertFalse(worker.is_alive())
        self.assertEqual(switcher.cancel_current_calls, 1)
        self.assertEqual(result_holder[0].error_code, "cancelled")

    def test_cancel_before_worker_starts_prevents_all_vpn_actions(self) -> None:
        coordinator, legacy, created = self._coordinator()
        legacy.last_status = CISCO

        token = coordinator.prepare_bancochile_operation()
        self.assertIsNotNone(token)
        coordinator.cancel_current(token)
        result = coordinator.switch_to(BANCOCHILE, operation_token=token)

        self.assertEqual(result.error_code, "cancelled")
        self.assertEqual(result.status, CISCO)
        self.assertEqual(legacy.calls, [])
        self.assertEqual(created, [])

    def test_cancel_during_switcher_creation_never_calls_connect(self) -> None:
        legacy = _LegacyService()
        switcher = _BancoSwitcher({})
        coordinator = None

        def factory(_config):
            coordinator.cancel_current()
            return switcher

        coordinator = BancoChileVPNCoordinator(
            {},
            legacy_service=legacy,
            switcher_factory=factory,
        )
        token = coordinator.prepare_bancochile_operation()
        self.assertIsNotNone(token)

        result = coordinator.switch_to(BANCOCHILE, operation_token=token)

        self.assertEqual(result.error_code, "cancelled")
        self.assertNotIn(("bank_connect", None), switcher.events)
        self.assertEqual(
            [call for call in legacy.calls if call[0] == "switch_to"],
            [("switch_to", NONE, None)],
        )

    def test_prepared_token_cannot_be_stolen_or_cancelled_by_another_request(self) -> None:
        coordinator, _legacy, _created = self._coordinator()
        token = coordinator.prepare_bancochile_operation()
        self.assertIsNotNone(token)

        self.assertIsNone(coordinator.prepare_bancochile_operation())
        coordinator.cancel_current(object())
        result = coordinator.switch_to(BANCOCHILE, operation_token=token)

        self.assertTrue(result.ok)
        self.assertEqual(result.status, BANCOCHILE)
        self.assertFalse(coordinator.busy)

    def test_cancel_during_final_disconnect_sample_stops_legacy_handoff(self) -> None:
        legacy = _LegacyService()
        switcher = _BancoSwitcher({})
        coordinator = BancoChileVPNCoordinator(
            {},
            legacy_service=legacy,
            switcher_factory=lambda _config: switcher,
        )
        self.assertTrue(coordinator.switch_to(BANCOCHILE).ok)
        legacy.calls.clear()
        original_status = switcher.get_bancochile_status

        def status_with_cancel():
            status = original_status()
            if status == NONE:
                coordinator.cancel_current()
            return status

        switcher.get_bancochile_status = status_with_cancel

        result = coordinator.switch_to(CISCO)

        self.assertEqual(result.error_code, "cancelled")
        self.assertEqual(result.status, NONE)
        self.assertEqual(legacy.calls, [])

    def test_legacy_handoff_is_announced_after_banco_is_confirmed_down(self) -> None:
        events: list[tuple] = []
        legacy = _LegacyService()

        def legacy_switch(target, progress=None):
            events.append(("legacy_switch", target))
            return VPNResult(True, "Oracle connected", target)

        legacy.switch_to = legacy_switch
        switcher = _BancoSwitcher({}, events)
        coordinator = BancoChileVPNCoordinator(
            {},
            legacy_service=legacy,
            switcher_factory=lambda _config: switcher,
        )
        self.assertTrue(coordinator.switch_to(BANCOCHILE).ok)
        events.clear()

        result = coordinator.switch_to(
            CISCO,
            lambda message: events.append(("progress", message)),
        )

        self.assertTrue(result.ok)
        self.assertEqual(
            events,
            [
                ("progress", "Disconnecting Banco de Chile VPN (GlobalProtect)..."),
                ("bank_status",),
                ("bank_disconnect",),
                ("bank_status",),
                ("progress", BANCOCHILE_LEGACY_HANDOFF),
                ("legacy_switch", CISCO),
            ],
        )


if __name__ == "__main__":
    unittest.main()
