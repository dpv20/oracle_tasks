from __future__ import annotations

import sys
import tempfile
import threading
import unittest
from pathlib import Path
from queue import SimpleQueue
from unittest.mock import Mock, patch


SRC_DIR = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_DIR))

from ui.app import OracleTasksApp  # noqa: E402
from features.vpn.action_arbiter_bancodechile import (  # noqa: E402
    BancoChileActionArbiter,
)
from features.vpn.coordinator_bancodechile import BANCOCHILE  # noqa: E402
from features.vpn.service import CISCO, VPNResult  # noqa: E402


class _ImmediateThread:
    def __init__(self, *, target, daemon=True, **_kwargs) -> None:
        self.target = target

    def start(self) -> None:
        self.target()


class AppBackgroundTests(unittest.TestCase):
    @staticmethod
    def _vpn_app(*, bank_owned: bool = False):
        app = OracleTasksApp.__new__(OracleTasksApp)
        app._views = {}
        app._background_requests = SimpleQueue()
        app._bancochile_action_arbiter = BancoChileActionArbiter()
        app._vpn_result_revision_lock = threading.Lock()
        app._vpn_result_revision = 0
        app.vpn_service = Mock(busy=False)
        app.bancochile_vpn_service = Mock(
            busy=False,
            owns_bancochile=bank_owned,
            last_status=BANCOCHILE if bank_owned else None,
        )
        app.bancochile_vpn_service.visible_status_snapshot.side_effect = (
            lambda status: (0, BANCOCHILE if bank_owned else status)
        )
        app.bancochile_vpn_service.reconcile_monitored_status.side_effect = (
            lambda status: (0, BANCOCHILE if bank_owned else status)
        )
        app.bancochile_vpn_service.is_status_snapshot_current.side_effect = (
            lambda revision: revision == 0
        )
        return app

    def test_normal_legacy_action_uses_original_service_only(self) -> None:
        app = self._vpn_app()
        expected = VPNResult(True, "Oracle connected", CISCO)
        app.vpn_service.switch_to.return_value = expected

        with patch("ui.app.threading.Thread", _ImmediateThread):
            app._dispatch_vpn_action(CISCO)

        app.vpn_service.switch_to.assert_called_once_with(CISCO)
        app.bancochile_vpn_service.prepare_bancochile_operation.assert_not_called()
        app.bancochile_vpn_service.switch_to.assert_not_called()
        self.assertEqual(
            app._background_requests.get_nowait(),
            ("vpn_result", expected, 1),
        )
        self.assertFalse(app._vpn_action_in_progress())
        self.assertEqual(app._bancochile_action_arbiter.legacy_active, 0)

    def test_bancochile_action_uses_only_banco_service_entrypoint(self) -> None:
        app = self._vpn_app()
        token = object()
        expected = VPNResult(True, "Banco connected", BANCOCHILE)
        app.bancochile_vpn_service.prepare_bancochile_operation.return_value = token
        app.bancochile_vpn_service.switch_to.return_value = expected

        with patch("ui.app.threading.Thread", _ImmediateThread):
            app._dispatch_vpn_action(BANCOCHILE)

        app.bancochile_vpn_service.switch_to.assert_called_once_with(
            BANCOCHILE,
            operation_token=token,
        )
        app.vpn_service.switch_to.assert_not_called()

    def test_superseded_tray_result_does_not_overwrite_newer_action(self) -> None:
        app = self._vpn_app()
        app._finish_vpn_action = Mock()
        old_result = VPNResult(True, "Oracle connected", CISCO)
        old_revision = app._advance_vpn_result_revision()
        new_revision = app._advance_vpn_result_revision()

        app._finish_vpn_action_snapshot(old_result, old_revision)

        app._finish_vpn_action.assert_not_called()
        self.assertTrue(app._is_vpn_result_revision_current(new_revision))

    def test_current_tray_result_is_applied(self) -> None:
        app = self._vpn_app()
        app._finish_vpn_action = Mock()
        result = VPNResult(True, "Banco connected", BANCOCHILE)
        revision = app._advance_vpn_result_revision()

        app._finish_vpn_action_snapshot(result, revision)

        app._finish_vpn_action.assert_called_once_with(result)

    def test_legacy_monitor_uses_banco_reconciliation_boundary(self) -> None:
        app = self._vpn_app()

        app._queue_legacy_vpn_status(CISCO)

        app.bancochile_vpn_service.reconcile_monitored_status.assert_called_once_with(
            CISCO
        )
        self.assertEqual(
            app._background_requests.get_nowait(),
            ("vpn_status", CISCO, 0),
        )

    def test_owned_banco_monitor_applies_reconciled_status(self) -> None:
        app = self._vpn_app(bank_owned=True)

        app._queue_legacy_vpn_status(CISCO)

        app.bancochile_vpn_service.reconcile_monitored_status.assert_called_once_with(
            CISCO
        )
        self.assertEqual(
            app._background_requests.get_nowait(),
            ("vpn_status", BANCOCHILE, 0),
        )

    def test_stale_monitor_snapshot_is_dropped_after_banco_transition(self) -> None:
        app = self._vpn_app()
        app._apply_vpn_status = Mock()
        app.bancochile_vpn_service.is_status_snapshot_current.side_effect = None
        app.bancochile_vpn_service.is_status_snapshot_current.return_value = False

        app._apply_vpn_status_snapshot(CISCO, 7)

        app._apply_vpn_status.assert_not_called()

    def test_unrelated_running_view_does_not_change_legacy_tray_path(self) -> None:
        app = self._vpn_app()
        app._views = {"vpn": type("View", (), {"_running": True})()}
        expected = VPNResult(True, "Oracle connected", CISCO)
        app.vpn_service.switch_to.return_value = expected

        with patch("ui.app.threading.Thread", _ImmediateThread):
            app._dispatch_vpn_action(CISCO)

        app.vpn_service.switch_to.assert_called_once_with(CISCO)
        app.bancochile_vpn_service.switch_to.assert_not_called()
        self.assertFalse(app._vpn_action_in_progress())
        self.assertEqual(app._bancochile_action_arbiter.legacy_active, 0)

    def test_busy_legacy_service_keeps_original_tray_guard(self) -> None:
        app = self._vpn_app()
        app.vpn_service.busy = True

        with patch("ui.app.threading.Thread") as thread:
            app._dispatch_vpn_action(CISCO)

        thread.assert_not_called()
        app.vpn_service.switch_to.assert_not_called()
        app.bancochile_vpn_service.switch_to.assert_not_called()

    def test_bancochile_reservation_blocks_legacy_tray_before_worker_spawn(self) -> None:
        app = self._vpn_app()
        self.assertTrue(app._try_begin_vpn_action())

        with patch("ui.app.threading.Thread") as thread:
            app._dispatch_vpn_action(CISCO)

        thread.assert_not_called()
        app.vpn_service.switch_to.assert_not_called()

    def test_legacy_tray_reservation_blocks_bancochile_before_worker_spawn(self) -> None:
        app = self._vpn_app()
        self.assertTrue(app._try_begin_legacy_vpn_action())

        app._dispatch_vpn_action(BANCOCHILE)

        app.bancochile_vpn_service.prepare_bancochile_operation.assert_not_called()
        app.bancochile_vpn_service.switch_to.assert_not_called()
        app._finish_legacy_vpn_action()

    def test_deferred_legacy_worker_blocks_bancochile_dispatch(self) -> None:
        app = self._vpn_app()
        pending = []

        class DeferredThread:
            def __init__(self, *, target, daemon=True, **_kwargs) -> None:
                self.target = target

            def start(self) -> None:
                pending.append(self.target)

        with patch("ui.app.threading.Thread", DeferredThread):
            app._dispatch_vpn_action(CISCO)
            app._dispatch_vpn_action(BANCOCHILE)

        self.assertEqual(len(pending), 1)
        app.bancochile_vpn_service.prepare_bancochile_operation.assert_not_called()
        pending.pop()()
        self.assertEqual(app._bancochile_action_arbiter.legacy_active, 0)

    def test_deferred_bancochile_worker_blocks_legacy_dispatch(self) -> None:
        app = self._vpn_app()
        app.bancochile_vpn_service.prepare_bancochile_operation.return_value = object()
        pending = []

        class DeferredThread:
            def __init__(self, *, target, daemon=True, **_kwargs) -> None:
                self.target = target

            def start(self) -> None:
                pending.append(self.target)

        with patch("ui.app.threading.Thread", DeferredThread):
            app._dispatch_vpn_action(BANCOCHILE)
            app._dispatch_vpn_action(CISCO)

        self.assertEqual(len(pending), 1)
        app.vpn_service.switch_to.assert_not_called()
        pending.pop()()
        self.assertFalse(app._vpn_action_in_progress())

    def test_window_close_hides_instead_of_destroying(self) -> None:
        app = OracleTasksApp.__new__(OracleTasksApp)
        app.root = Mock()

        app._on_close()

        app.root.withdraw.assert_called_once_with()
        app.root.destroy.assert_not_called()

    def test_exit_stops_tray_and_destroys_window(self) -> None:
        app = OracleTasksApp.__new__(OracleTasksApp)
        app.root = Mock()
        app._tray = Mock()
        app._views = {}
        app._shutting_down = False

        app._exit_application()

        app._tray.stop.assert_called_once_with()
        app.root.destroy.assert_called_once_with()

    def test_show_flag_restores_existing_instance(self) -> None:
        app = OracleTasksApp.__new__(OracleTasksApp)
        app.root = Mock()
        app._background_requests = SimpleQueue()
        app._shutting_down = False
        app._show_window = Mock()

        with tempfile.TemporaryDirectory() as temp_dir:
            show_flag = Path(temp_dir) / "show.flag"
            show_flag.write_text("show", encoding="utf-8")
            with patch("ui.app.SHOW_FLAG_PATH", show_flag):
                app._poll_background_requests()

            self.assertFalse(show_flag.exists())

        app._show_window.assert_called_once_with()
        app.root.after.assert_called_once_with(250, app._poll_background_requests)

    def test_reset_app_launches_replacement_then_shuts_down(self) -> None:
        app = OracleTasksApp.__new__(OracleTasksApp)
        app.root = Mock()
        app._has_running_work = Mock(return_value=False)
        app._launch_restart_helper = Mock()
        app._shutdown = Mock()

        app._reset_application()

        app._launch_restart_helper.assert_called_once_with()
        app._shutdown.assert_called_once_with()

    def test_reset_app_is_blocked_while_work_is_running(self) -> None:
        app = OracleTasksApp.__new__(OracleTasksApp)
        app.root = Mock()
        app._has_running_work = Mock(return_value=True)
        app._warn_running_work = Mock()
        app._launch_restart_helper = Mock()
        app._shutdown = Mock()

        app._reset_application()

        app._warn_running_work.assert_called_once_with()
        app._launch_restart_helper.assert_not_called()
        app._shutdown.assert_not_called()


if __name__ == "__main__":
    unittest.main()
