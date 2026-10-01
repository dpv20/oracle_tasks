from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


SRC_DIR = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_DIR))

from features.vpn.service import CISCO, FORTI, GPROT, NONE, VPNResult  # noqa: E402
from features.vpn.coordinator_bancodechile import BANCOCHILE  # noqa: E402
from features.vpn.view import VPNView  # noqa: E402


class _Variable:
    def __init__(self, value: bool) -> None:
        self.value = value

    def get(self) -> bool:
        return self.value

    def set(self, value: bool) -> None:
        self.value = value


class VPNViewTests(unittest.TestCase):
    def test_stale_manual_refresh_snapshot_is_not_applied(self) -> None:
        bank_service = Mock()
        bank_service.is_status_snapshot_current.return_value = False
        view = SimpleNamespace(
            bancochile_service=bank_service,
            _apply_status=Mock(),
        )

        VPNView._apply_monitored_status(view, BANCOCHILE, 4)

        bank_service.is_status_snapshot_current.assert_called_once_with(4)
        view._apply_status.assert_not_called()

    def test_active_bancochile_button_disconnects_instead_of_reconnecting(self) -> None:
        view = SimpleNamespace(
            _status=BANCOCHILE,
            _switch_to=Mock(),
            _switch_bancochile=Mock(),
            app=SimpleNamespace(_vpn_action_in_progress=lambda: False),
            bancochile_service=SimpleNamespace(owns_bancochile=True),
        )

        VPNView._on_target_button(view, BANCOCHILE)

        view._switch_bancochile.assert_called_once_with(NONE)
        view._switch_to.assert_not_called()

    def test_leaving_bancochile_uses_the_isolated_transition(self) -> None:
        view = SimpleNamespace(
            _status=BANCOCHILE,
            _switch_to=Mock(),
            _switch_bancochile=Mock(),
            app=SimpleNamespace(_vpn_action_in_progress=lambda: False),
            bancochile_service=SimpleNamespace(owns_bancochile=True),
        )

        VPNView._on_target_button(view, CISCO)

        view._switch_bancochile.assert_called_once_with(CISCO)
        view._switch_to.assert_not_called()

    def test_inactive_legacy_target_uses_the_unchanged_legacy_path(self) -> None:
        view = SimpleNamespace(
            _status=NONE,
            _running=False,
            _switch_to=Mock(),
            _switch_bancochile=Mock(),
            app=SimpleNamespace(
                _vpn_action_in_progress=lambda: False,
                _advance_vpn_result_revision=Mock(),
            ),
            bancochile_service=SimpleNamespace(owns_bancochile=False),
        )

        VPNView._on_target_button(view, CISCO)

        view._switch_to.assert_called_once_with(CISCO)
        view._switch_bancochile.assert_not_called()
        view.app._advance_vpn_result_revision.assert_called_once_with()

    def test_tray_gate_blocks_view_request_before_either_service_is_called(self) -> None:
        view = SimpleNamespace(
            _status=NONE,
            _switch_to=Mock(),
            _switch_bancochile=Mock(),
            app=SimpleNamespace(_vpn_action_in_progress=lambda: True),
            bancochile_service=SimpleNamespace(owns_bancochile=False),
        )

        VPNView._on_target_button(view, CISCO)

        view._switch_to.assert_not_called()
        view._switch_bancochile.assert_not_called()

    def test_refresh_gate_blocks_connection_card_requests(self) -> None:
        view = SimpleNamespace(
            _status=NONE,
            _refreshing=True,
            _switch_to=Mock(),
            _switch_bancochile=Mock(),
            app=SimpleNamespace(_vpn_action_in_progress=lambda: False),
            bancochile_service=SimpleNamespace(owns_bancochile=False),
        )

        VPNView._on_target_button(view, CISCO)

        view._switch_to.assert_not_called()
        view._switch_bancochile.assert_not_called()

    def test_active_vpn_button_remains_enabled_for_disconnect(self) -> None:
        buttons = {
            target: Mock(name=f"button_{target}")
            for target in (CISCO, FORTI, GPROT, BANCOCHILE, NONE)
        }
        view = SimpleNamespace(_status=BANCOCHILE, _buttons=buttons)

        VPNView._set_controls_enabled(view, True)

        buttons[BANCOCHILE].configure.assert_called_once_with(state="normal")

    def test_bancochile_cancel_button_requests_service_cancellation(self) -> None:
        token = object()
        bank_service = Mock()
        view = SimpleNamespace(
            _running=True,
            _operation_is_bancochile=True,
            _bancochile_operation_token=token,
            bancochile_service=bank_service,
            cancel_button=Mock(),
            message_label=Mock(),
        )

        with patch("features.vpn.view.t", return_value="Cancelling..."):
            VPNView._cancel_current(view)

        bank_service.cancel_current.assert_called_once_with(token)
        view.cancel_button.configure.assert_called_once_with(state="disabled")
        view.message_label.configure.assert_called_once_with(text="Cancelling...")

    def test_bancochile_operation_is_armed_before_worker_starts(self) -> None:
        bank_service = Mock()
        token = object()
        bank_service.prepare_bancochile_operation.return_value = token
        thread = Mock()
        app = SimpleNamespace(
            _try_begin_vpn_action=Mock(return_value=True),
            _finish_vpn_action_gate=Mock(),
            _advance_vpn_result_revision=Mock(),
        )
        view = SimpleNamespace(
            _running=False,
            app=app,
            bancochile_service=bank_service,
            _bancochile_operation_token=None,
            _requested_target=None,
            _operation_is_bancochile=False,
            _set_controls_enabled=Mock(),
            refresh_button=Mock(),
            cancel_button=Mock(),
            _set_bancochile_cancel_visible=Mock(),
            progress=Mock(),
            message_label=Mock(),
        )

        with (
            patch("features.vpn.view.t", return_value="Working..."),
            patch("features.vpn.view.threading.Thread", return_value=thread),
        ):
            VPNView._switch_bancochile(view, BANCOCHILE)

        bank_service.prepare_bancochile_operation.assert_called_once_with()
        app._try_begin_vpn_action.assert_called_once_with()
        app._advance_vpn_result_revision.assert_called_once_with()
        self.assertIs(view._bancochile_operation_token, token)
        thread.start.assert_called_once_with()

    def test_bancochile_handoff_hides_cancel_before_legacy_phase(self) -> None:
        view = SimpleNamespace(
            _operation_is_bancochile=True,
            _bancochile_operation_token=object(),
            cancel_button=Mock(),
            _set_bancochile_cancel_visible=Mock(),
        )

        VPNView._finish_bancochile_phase(view)

        view.cancel_button.configure.assert_called_once_with(state="disabled")
        view._set_bancochile_cancel_visible.assert_called_once_with(False)
        self.assertFalse(view._operation_is_bancochile)
        self.assertIsNone(view._bancochile_operation_token)

    def test_banco_to_falabella_wrong_password_keeps_legacy_prompt(self) -> None:
        view = SimpleNamespace(
            _running=True,
            _operation_is_bancochile=False,
            _bancochile_operation_token=None,
            progress=Mock(),
            refresh_button=Mock(),
            cancel_button=Mock(),
            _set_bancochile_cancel_visible=Mock(),
            _apply_status=Mock(),
            _handle_wrong_password=Mock(),
            _handle_gp_password=Mock(),
        )
        result = VPNResult(False, "Wrong password", NONE, "wrong_password")

        VPNView._finish_bancochile(view, result)

        view._handle_wrong_password.assert_called_once_with()
        view._handle_gp_password.assert_not_called()

    def test_bancochile_preferences_wrap_the_unchanged_legacy_callback(self) -> None:
        app = SimpleNamespace(config=Mock())
        app.config.get.return_value = False
        view = SimpleNamespace(
            app=app,
            show_bancochile_var=_Variable(True),
            _refresh_preferences=Mock(),
            _retry_after_save=False,
            _retry_gp_target_after_save=BANCOCHILE,
            tabs=Mock(),
            _connections_tab="Connections",
            after=Mock(),
            _switch_bancochile=Mock(),
        )

        VPNView._refresh_preferences_with_bancochile(view)

        self.assertFalse(view.show_bancochile_var.get())
        view._refresh_preferences.assert_called_once_with()
        view.tabs.set.assert_called_once_with("Connections")
        view.after.assert_called_once()

    def test_forti_retry_is_reserved_during_the_bancochile_wrapper_delay(self) -> None:
        scheduled = []
        app = SimpleNamespace(
            config=Mock(),
            _vpn_action_in_progress=lambda: False,
            _advance_vpn_result_revision=Mock(),
        )
        app.config.get.return_value = True
        view = SimpleNamespace(
            app=app,
            show_bancochile_var=_Variable(True),
            _refresh_preferences=Mock(),
            _retry_after_save=True,
            _retry_gp_target_after_save=BANCOCHILE,
            tabs=Mock(),
            _connections_tab="Connections",
            _running=False,
            after=lambda delay, callback: scheduled.append((delay, callback)),
            bancochile_service=SimpleNamespace(owns_bancochile=False),
            _retry_forti_credentials=Mock(),
        )
        view._run_pending_forti_retry = lambda: VPNView._run_pending_forti_retry(view)

        VPNView._refresh_preferences_with_bancochile(view)

        self.assertTrue(view._running)
        self.assertFalse(view._retry_after_save)
        self.assertIsNone(view._retry_gp_target_after_save)
        view._refresh_preferences.assert_called_once_with()
        self.assertEqual(scheduled[0][0], 300)
        scheduled[0][1]()
        self.assertFalse(view._running)
        view._retry_forti_credentials.assert_called_once_with()
        app._advance_vpn_result_revision.assert_called_once_with()

    def test_pending_forti_retry_aborts_if_bancochile_now_owns_the_session(self) -> None:
        view = SimpleNamespace(
            _running=True,
            app=SimpleNamespace(_vpn_action_in_progress=lambda: False),
            bancochile_service=SimpleNamespace(owns_bancochile=True),
            _retry_forti_credentials=Mock(),
        )

        VPNView._run_pending_forti_retry(view)

        self.assertFalse(view._running)
        view._retry_forti_credentials.assert_not_called()

    @patch("features.vpn.view.messagebox.askokcancel", return_value=True)
    def test_bancochile_prompt_clears_a_stale_forti_retry(self, _ask) -> None:
        view = SimpleNamespace(
            _retry_after_save=True,
            _retry_gp_target_after_save=None,
            _requested_target=BANCOCHILE,
            settings_panel=Mock(),
            tabs=Mock(),
            _settings_tab="Settings",
        )

        VPNView._handle_gp_password(view, "Password required")

        self.assertFalse(view._retry_after_save)
        self.assertEqual(view._retry_gp_target_after_save, BANCOCHILE)
        view.settings_panel.show_profile.assert_called_once_with(BANCOCHILE)

    def test_cancel_is_ignored_for_legacy_vpn_operation(self) -> None:
        view = SimpleNamespace(
            _running=True,
            _operation_is_bancochile=False,
            service=Mock(),
            cancel_button=Mock(),
            message_label=Mock(),
        )

        VPNView._cancel_current(view)

        view.service.cancel_current.assert_not_called()
        view.cancel_button.configure.assert_not_called()
        view.message_label.configure.assert_not_called()

    def test_bancochile_is_a_distinct_connection_card(self) -> None:
        rows = {target: (title, subtitle) for target, title, subtitle in VPNView._CARD_DATA}

        self.assertEqual(
            rows[BANCOCHILE],
            ("vpn.bancochile", "vpn.bancochile.subtitle"),
        )
        self.assertIn(GPROT, rows)
        self.assertNotEqual(BANCOCHILE, GPROT)

    def test_bancochile_visibility_does_not_require_bice_visibility(self) -> None:
        cards = {
            target: Mock(name=f"card_{target}")
            for target in (CISCO, FORTI, GPROT, BANCOCHILE, NONE)
        }
        view = SimpleNamespace(
            _cards=cards,
            app=SimpleNamespace(
                config=SimpleNamespace(
                    get=lambda key, default=None: True
                    if key == "vpn_show_forti"
                    else default
                )
            ),
            show_bice_var=_Variable(False),
            show_bancochile_var=_Variable(True),
        )

        VPNView._apply_bice_visibility(view)

        cards[BANCOCHILE].grid.assert_called_once()
        cards[GPROT].grid.assert_not_called()
        cards[CISCO].grid.assert_called_once()
        cards[FORTI].grid.assert_called_once()
        cards[NONE].grid.assert_called_once()

    def test_bancochile_checkbox_persists_and_refreshes_tray(self) -> None:
        config = Mock()
        tray = Mock()
        view = SimpleNamespace(
            app=SimpleNamespace(config=config, _tray=tray),
            show_bancochile_var=_Variable(False),
            _apply_bice_visibility=Mock(),
        )

        VPNView._toggle_bancochile(view)

        config.set.assert_called_once_with("vpn_show_bancochile", False)
        view._apply_bice_visibility.assert_called_once_with()
        tray.refresh_menu.assert_called_once_with()

    def test_falabella_checkbox_persists_and_refreshes_tray(self) -> None:
        config = Mock()
        tray = Mock()
        view = SimpleNamespace(
            app=SimpleNamespace(config=config, _tray=tray),
            show_forti_var=_Variable(False),
            _apply_bice_visibility=Mock(),
        )

        VPNView._toggle_forti(view)

        config.set.assert_called_once_with("vpn_show_forti", False)
        view._apply_bice_visibility.assert_called_once_with()
        tray.refresh_menu.assert_called_once_with()

    def test_bancochile_credential_error_opens_matching_settings_tab(self) -> None:
        view = SimpleNamespace(
            _requested_target=BANCOCHILE,
            _retry_gp_target_after_save=None,
            settings_panel=Mock(),
            tabs=Mock(),
            _settings_tab="Settings",
        )

        with patch(
            "features.vpn.view.messagebox.askokcancel",
            return_value=True,
        ):
            VPNView._handle_gp_password(view, "Password required")

        self.assertEqual(view._retry_gp_target_after_save, BANCOCHILE)
        view.settings_panel.show_profile.assert_called_once_with(BANCOCHILE)
        view.tabs.set.assert_called_once_with("Settings")


if __name__ == "__main__":
    unittest.main()
