from __future__ import annotations

"""Banco de Chile switcher and legacy-isolation regression tests."""

import ctypes
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from unittest.mock import call
from unittest.mock import patch


SRC_DIR = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_DIR))

from features.vpn import controller as legacy_controller  # noqa: E402
from features.vpn import switcher_bancodechile as controller  # noqa: E402

class _Control:
    def __init__(
        self,
        text: str,
        control_type: str = "Text",
        name: str = "",
        automation_id: str = "",
    ) -> None:
        self.text = text
        self.element_info = SimpleNamespace(
            control_type=control_type,
            name=name,
            automation_id=automation_id,
        )

    def window_text(self) -> str:
        return self.text


class _Window:
    def __init__(self, controls) -> None:
        self._controls = list(controls)

    def descendants(self):
        return list(self._controls)


class _PortalControl(_Control):
    def __init__(self, text: str, *, update_on_set: bool = True) -> None:
        super().__init__(text, "Edit", "Portal", controller.GP_PORTAL_AUTOIDS[0])
        self.update_on_set = update_on_set
        self.set_calls: list[str] = []

    def set_edit_text(self, value: str) -> None:
        self.set_calls.append(value)
        if self.update_on_set:
            self.text = value


class GlobalProtectTerminalWindowTests(unittest.TestCase):
    def test_button_action_mismatch_is_refused_without_clicking(self) -> None:
        for expected, actual in (("connect", "Disconnect"), ("disconnect", "Connect")):
            with self.subTest(expected=expected, actual=actual):
                ctrl = Mock()
                ctrl.window_text.return_value = actual
                with patch.object(
                    controller,
                    "_gp_find_descendant_by_autoid",
                    return_value=ctrl,
                ):
                    invoked = controller._gp_invoke_connect_button(
                        Mock(),
                        expected_action=expected,
                    )

                self.assertFalse(invoked)
                ctrl.invoke.assert_not_called()
                ctrl.click_input.assert_not_called()

    def test_ambiguous_action_exception_never_runs_a_second_toggle(self) -> None:
        ctrl = Mock()
        ctrl.window_text.return_value = "Connect"
        ctrl.invoke.side_effect = RuntimeError("Invoke raised after delivering click")

        with (
            patch.object(
                controller,
                "_gp_find_descendant_by_autoid",
                return_value=ctrl,
            ) as find_control,
        ):
            invoked = controller._gp_invoke_connect_button(
                Mock(),
                expected_action="connect",
                prefer_click_input=False,
            )

        self.assertTrue(invoked)
        find_control.assert_called_once()
        ctrl.invoke.assert_called_once_with()
        ctrl.click_input.assert_not_called()

    def test_default_button_action_uses_one_physical_click(self) -> None:
        ctrl = Mock()
        ctrl.window_text.return_value = "Connect"

        with patch.object(
            controller,
            "_gp_find_descendant_by_autoid",
            return_value=ctrl,
        ):
            invoked = controller._gp_invoke_connect_button(
                Mock(),
                expected_action="connect",
            )

        self.assertTrue(invoked)
        ctrl.click_input.assert_called_once_with()
        ctrl.invoke.assert_not_called()

    def test_hwnd_value_accepts_ctypes_and_plain_handles(self) -> None:
        self.assertEqual(controller._gp_hwnd_value(123), 123)
        self.assertEqual(controller._gp_hwnd_value(ctypes.c_void_p(456)), 456)

    def test_terminal_title_scope_excludes_main_and_other_portal(self) -> None:
        self.assertTrue(
            controller._gp_terminal_title_matches(
                "GlobalProtect Login",
                controller.BANCOCHILE_PORTAL,
            )
        )
        self.assertTrue(
            controller._gp_terminal_title_matches(
                "GlobalProtect Notification - bchmfa",
                controller.BANCOCHILE_PORTAL,
            )
        )
        self.assertFalse(
            controller._gp_terminal_title_matches(
                "GlobalProtect Notification - ext",
                controller.BANCOCHILE_PORTAL,
            )
        )
        self.assertFalse(
            controller._gp_terminal_title_matches(
                "GlobalProtect",
                controller.BANCOCHILE_PORTAL,
            )
        )

    def test_cleanup_closes_notification_before_login_and_never_main(self) -> None:
        notification = "GlobalProtect Notification - bchmfa"
        windows = [
            (10, controller.GP_LOGIN_TITLE),
            (20, notification),
        ]

        with (
            patch.object(
                controller,
                "_gp_list_terminal_windows",
                return_value=windows,
            ),
            patch.object(
                controller,
                "_gp_post_close_terminal_window",
                return_value=True,
            ) as post_close,
        ):
            closed = controller._gp_cleanup_terminal_windows(
                controller.BANCOCHILE_PORTAL,
            )

        self.assertEqual(closed, 2)
        self.assertEqual(
            post_close.call_args_list,
            [
                call(20, notification),
                call(10, controller.GP_LOGIN_TITLE),
            ],
        )

    def test_cleanup_catches_a_notification_that_appears_late(self) -> None:
        notification = "GlobalProtect Notification - bchmfa"
        login = (10, controller.GP_LOGIN_TITLE)
        both = [login, (20, notification)]

        with (
            patch.object(
                controller,
                "_gp_list_terminal_windows",
                side_effect=([login], both, both),
            ),
            patch.object(
                controller,
                "_gp_post_close_terminal_window",
                return_value=True,
            ) as post_close,
            patch.object(controller.time, "sleep", return_value=None),
        ):
            closed = controller._gp_cleanup_terminal_windows(
                controller.BANCOCHILE_PORTAL,
                wait_seconds=0.5,
            )

        self.assertEqual(closed, 2)
        self.assertEqual(
            post_close.call_args_list,
            [
                call(10, controller.GP_LOGIN_TITLE),
                call(20, notification),
            ],
        )

    def test_confirmed_connection_waits_for_adapter_before_cleanup(self) -> None:
        window = Mock()
        with (
            patch.object(controller, "_gp_get_window", return_value=window),
            patch.object(controller, "_gp_get_status_text", return_value="Connected"),
            patch.object(
                controller,
                "_gp_adapter_status",
                side_effect=("Down", "Up"),
            ),
            patch.object(controller, "_gp_cleanup_terminal_windows") as cleanup,
            patch.object(controller.time, "sleep", return_value=None),
        ):
            confirmed = controller._gp_cleanup_after_confirmed_connection(
                controller.BANCOCHILE_PORTAL,
                attempts=2,
            )

        self.assertTrue(confirmed)
        cleanup.assert_called_once_with(
            controller.BANCOCHILE_PORTAL,
            wait_seconds=1.5,
        )

    def test_adapter_only_success_does_not_close_active_login(self) -> None:
        window = Mock()
        with (
            patch.object(controller, "_gp_get_window", return_value=window),
            patch.object(controller, "_gp_get_status_text", return_value="Connecting..."),
            patch.object(controller, "_gp_adapter_status", return_value="Up"),
            patch.object(controller, "_gp_cleanup_terminal_windows") as cleanup,
            patch.object(controller.time, "sleep", return_value=None),
        ):
            confirmed = controller._gp_cleanup_after_confirmed_connection(
                controller.BANCOCHILE_PORTAL,
                attempts=2,
            )

        self.assertFalse(confirmed)
        cleanup.assert_not_called()

    def test_connect_transition_fails_fast_when_invoke_was_a_noop(self) -> None:
        window = Mock()

        with (
            patch.object(controller, "_gp_get_window", return_value=window),
            patch.object(controller, "_gp_get_status_text", return_value="Disconnected"),
            patch.object(controller, "_gp_get_button_label", return_value="Connect"),
            patch.object(controller, "_gp_list_terminal_windows", return_value=[]),
            patch.object(controller, "_gp_adapter_status", return_value="Disabled"),
            patch.object(controller.time, "sleep", return_value=None),
        ):
            result = controller._gp_wait_for_connect_transition(
                controller.BANCOCHILE_PORTAL,
                attempts=4,
            )

        self.assertEqual(result, "not_started")

    def test_post_disconnect_transition_rejects_stale_connected_ui(self) -> None:
        window = Mock()

        with (
            patch.object(controller, "_gp_get_window", return_value=window),
            patch.object(controller, "_gp_get_status_text", return_value="Connected"),
            patch.object(controller, "_gp_get_button_label", return_value="Disconnect"),
            patch.object(controller, "_gp_list_terminal_windows", return_value=[]),
            patch.object(controller, "_gp_adapter_status", return_value="Disabled"),
            patch.object(controller.time, "sleep", return_value=None),
        ):
            result = controller._gp_wait_for_connect_transition(
                controller.BANCOCHILE_PORTAL,
                attempts=4,
                require_adapter_for_terminal_ui=True,
            )

        self.assertEqual(result, "not_started")

    def test_connect_transition_ignores_an_old_login_but_accepts_a_new_one(self) -> None:
        window = Mock()
        with (
            patch.object(controller, "_gp_get_window", return_value=window),
            patch.object(controller, "_gp_get_status_text", return_value="Disconnected"),
            patch.object(controller, "_gp_get_button_label", return_value="Connect"),
            patch.object(controller, "_gp_adapter_status", return_value="Disabled"),
            patch.object(
                controller,
                "_gp_list_terminal_windows",
                return_value=[(10, controller.GP_LOGIN_TITLE)],
            ),
        ):
            stale = controller._gp_wait_for_connect_transition(
                controller.BANCOCHILE_PORTAL,
                ignored_login_hwnds={10},
                attempts=1,
            )

        with (
            patch.object(controller, "_gp_get_window", return_value=window),
            patch.object(controller, "_gp_get_status_text", return_value="Disconnected"),
            patch.object(controller, "_gp_get_button_label", return_value="Connect"),
            patch.object(controller, "_gp_adapter_status", return_value="Disabled"),
            patch.object(
                controller,
                "_gp_list_terminal_windows",
                return_value=[(11, controller.GP_LOGIN_TITLE)],
            ),
        ):
            fresh = controller._gp_wait_for_connect_transition(
                controller.BANCOCHILE_PORTAL,
                ignored_login_hwnds={10},
                attempts=1,
            )

        self.assertEqual(stale, "not_started")
        self.assertEqual(fresh, "started")


class LegacyControllerIsolationRegressionTests(unittest.TestCase):
    def setUp(self) -> None:
        legacy_controller._autofill_cancel.clear()

    def tearDown(self) -> None:
        legacy_controller._autofill_cancel.clear()

    def test_interruptible_wait_reports_cancelled(self) -> None:
        legacy_controller._autofill_cancel.set()

        with patch.object(legacy_controller, "_forti_client_running") as client_running:
            result = legacy_controller._forti_wait(30.0)

        self.assertEqual(result, "cancelled")
        client_running.assert_not_called()

    def test_detected_saml_flow_propagates_cancelled_wait(self) -> None:
        sign_in_window = SimpleNamespace(handle=123)

        with (
            patch.object(legacy_controller, "_forti_client_running", return_value=True),
            patch.object(
                legacy_controller,
                "_find_signin_window",
                return_value=sign_in_window,
            ),
            patch.object(legacy_controller, "_forti_wait", return_value="cancelled"),
            patch.object(legacy_controller, "_detect_signin_page") as detect_page,
        ):
            result = legacy_controller._forti_autofill_signin(
                "user@example.com",
                "secret",
            )

        self.assertEqual(result, "ok")
        detect_page.assert_called_once_with(sign_in_window.handle)

    def test_custom_saml_flow_propagates_cancelled_wait(self) -> None:
        sign_in_window = SimpleNamespace(handle=123)

        with (
            patch.object(legacy_controller, "_forti_client_running", return_value=True),
            patch.object(
                legacy_controller,
                "_find_signin_window",
                return_value=sign_in_window,
            ),
            patch.object(legacy_controller, "_forti_wait", return_value="cancelled"),
        ):
            result = legacy_controller._forti_autofill_custom_flow(
                "user@example.com",
                "secret",
                ["username", "password", "mfa"],
            )

        self.assertEqual(result, "ok")

    def test_connect_forti_maps_cancelled_autofill_to_service_sentinel(self) -> None:
        instance = legacy_controller.VPNController(
            {
                "forti_username": "user@example.com",
                "forti_password_enc": "ciphertext",
                "forti_flow_mode": "detect",
            }
        )
        window = Mock()

        with (
            patch.object(instance, "_forti_connected", return_value=False),
            patch.object(legacy_controller, "_forti_get_window", return_value=window),
            patch.object(legacy_controller, "_forti_click_button", return_value=True),
            patch.object(
                legacy_controller,
                "_forti_autofill_signin",
                return_value="cancelled",
            ),
            patch.object(legacy_controller, "decrypt_password", return_value="secret"),
        ):
            ok, message = instance.connect_forti()

        self.assertTrue(ok)
        self.assertIn("Credentials submitted", message)

    def test_retry_forti_cancelled_during_settle_never_types_password(self) -> None:
        instance = legacy_controller.VPNController(
            {"forti_password_enc": "ciphertext"}
        )

        def cancel_during_sleep(_seconds):
            legacy_controller._autofill_cancel.set()

        with (
            patch.object(
                legacy_controller.time,
                "sleep",
                side_effect=cancel_during_sleep,
            ),
            patch.object(
                legacy_controller,
                "_find_signin_window",
                return_value=None,
            ) as find_window,
            patch.object(legacy_controller, "_focus_and_type") as focus_and_type,
        ):
            ok, message = instance.retry_forti_credentials()

        self.assertFalse(ok)
        self.assertIn("sign-in", message)
        find_window.assert_called_once_with()
        focus_and_type.assert_not_called()


class LegacyGlobalProtectRegressionTests(unittest.TestCase):
    def test_bice_status_uses_only_the_legacy_detectors(self) -> None:
        instance = legacy_controller.VPNController({})

        with (
            patch.object(instance, "_cisco_connected", return_value=False),
            patch.object(instance, "_forti_connected", return_value=False),
            patch.object(instance, "_gp_connected", return_value=True) as gp_connected,
            patch.object(
                legacy_controller,
                "_get_adapter_status",
                return_value={"globalprotect": "Up"},
            ),
        ):
            status = instance.get_status()

        self.assertEqual(status, legacy_controller.GPROT)
        gp_connected.assert_called_once_with({"globalprotect": "Up"})
        self.assertFalse(hasattr(instance, "_get_bancochile_adapter"))

    def test_bice_connect_keeps_legacy_single_invoke_signature(self) -> None:
        instance = legacy_controller.VPNController(
            {"gp_portal_url": "ext.bice.cl"}
        )
        window = Mock()

        with (
            patch.object(legacy_controller, "_gp_diagnostics"),
            patch.object(legacy_controller, "_gp_get_window", return_value=window),
            patch.object(
                legacy_controller,
                "_gp_get_status_text",
                return_value="Not Connected",
            ),
            patch.object(
                legacy_controller,
                "_gp_get_button_label",
                return_value="Connect",
            ),
            patch.object(
                legacy_controller,
                "_gp_invoke_connect_button",
                return_value=True,
            ) as invoke,
            patch.object(legacy_controller, "_gp_adapter_status", return_value="Up"),
            patch.object(legacy_controller.time, "sleep", return_value=None),
        ):
            ok, message = instance.connect_globalprotect()

        self.assertTrue(ok)
        self.assertEqual(message, "GlobalProtect connected.")
        invoke.assert_called_once_with(window)
        self.assertFalse(hasattr(instance, "_bancochile_adapter"))

    def test_bice_disconnect_keeps_legacy_single_invoke_signature(self) -> None:
        instance = legacy_controller.VPNController({})
        window = Mock()

        with (
            patch.object(legacy_controller, "_gp_diagnostics"),
            patch.object(legacy_controller, "_gp_get_window", return_value=window),
            patch.object(
                legacy_controller,
                "_gp_get_status_text",
                side_effect=("Connected", "Disconnected"),
            ),
            patch.object(
                legacy_controller,
                "_gp_get_button_label",
                return_value="Disconnect",
            ),
            patch.object(
                legacy_controller,
                "_gp_invoke_connect_button",
                return_value=True,
            ) as invoke,
            patch.object(legacy_controller.time, "sleep", return_value=None),
        ):
            ok, message = instance.disconnect_globalprotect()

        self.assertTrue(ok)
        self.assertEqual(message, "GlobalProtect disconnected.")
        invoke.assert_called_once_with(window)
        self.assertFalse(hasattr(instance, "_bancochile_adapter"))

    def test_bice_disconnect_timeout_keeps_legacy_best_effort_success(self) -> None:
        instance = legacy_controller.VPNController({})
        window = Mock()

        with (
            patch.object(legacy_controller, "_gp_diagnostics"),
            patch.object(legacy_controller, "_gp_get_window", return_value=window),
            patch.object(
                legacy_controller,
                "_gp_get_status_text",
                return_value="Connected",
            ),
            patch.object(
                legacy_controller,
                "_gp_get_button_label",
                return_value="Disconnect",
            ),
            patch.object(
                legacy_controller,
                "_gp_invoke_connect_button",
                return_value=True,
            ) as invoke,
            patch.object(legacy_controller.time, "time", side_effect=(0, 16)),
        ):
            ok, message = instance.disconnect_globalprotect()

        self.assertTrue(ok)
        self.assertIn("may still be finishing", message)
        invoke.assert_called_once_with(window)


class BancoChileSwitcherTests(unittest.TestCase):
    def test_failed_attempt_with_no_banco_evidence_releases_ownership(self) -> None:
        instance = controller.BancoChileSwitcher({})

        with (
            patch.object(controller, "_gp_get_window", return_value=None),
            patch.object(controller, "_gp_adapter_status", return_value=""),
        ):
            status = instance.get_bancochile_status()

        self.assertEqual(status, controller.NONE)

    def test_unidentified_active_globalprotect_is_not_claimed_as_banco(self) -> None:
        instance = controller.BancoChileSwitcher({})

        with (
            patch.object(controller, "_gp_get_window", return_value=None),
            patch.object(controller, "_gp_adapter_status", return_value="Up"),
        ):
            status = instance.get_bancochile_status()

        self.assertEqual(status, controller.GPROT_UNKNOWN)

    def test_status_trusts_connected_ui_during_adapter_startup(self) -> None:
        instance = controller.BancoChileSwitcher({})
        window = Mock()

        with (
            patch.object(controller, "_gp_get_window", return_value=window),
            patch.object(
                controller,
                "_gp_get_portal_text",
                return_value=controller.BANCOCHILE_PORTAL,
            ),
            patch.object(controller, "_gp_get_status_text", return_value="Connected"),
        ):
            status = instance.get_bancochile_status("Disabled")

        self.assertEqual(status, controller.BANCOCHILE)
        self.assertFalse(instance._gp_disconnect_confirmed)

    def test_status_uses_disabled_adapter_over_stale_ui_after_confirmed_disconnect(
        self,
    ) -> None:
        instance = controller.BancoChileSwitcher({})
        instance._gp_disconnect_confirmed = True
        window = Mock()

        with (
            patch.object(controller, "_gp_get_window", return_value=window) as get_window,
            patch.object(controller, "_gp_get_portal_text", return_value=""),
            patch.object(controller, "_gp_get_status_text", return_value="Connected"),
        ):
            status = instance.get_bancochile_status("Disabled")

        self.assertEqual(status, controller.NONE)
        get_window.assert_called_once_with(timeout=0.4)

    def test_status_keeps_confirmed_disconnect_while_old_ui_still_connecting(
        self,
    ) -> None:
        instance = controller.BancoChileSwitcher({})
        instance._gp_disconnect_confirmed = True
        window = Mock()

        with (
            patch.object(controller, "_gp_get_window", return_value=window),
            patch.object(controller, "_gp_get_portal_text", return_value=""),
            patch.object(controller, "_gp_get_status_text", return_value="Connecting..."),
        ):
            status = instance.get_bancochile_status("Disabled")

        self.assertEqual(status, controller.NONE)
        self.assertTrue(instance._gp_disconnect_confirmed)

    def test_status_rejects_stale_connected_ui_when_adapter_probe_is_unknown(
        self,
    ) -> None:
        instance = controller.BancoChileSwitcher({})
        instance._gp_disconnect_confirmed = True
        window = Mock()

        with (
            patch.object(controller, "_gp_get_window", return_value=window),
            patch.object(controller, "_gp_get_portal_text", return_value=""),
            patch.object(controller, "_gp_adapter_status", return_value=""),
            patch.object(controller, "_gp_get_status_text", return_value="Connected"),
        ):
            status = instance.get_bancochile_status()

        self.assertEqual(status, controller.GPROT_UNKNOWN)
        self.assertTrue(instance._gp_disconnect_confirmed)

    def test_connected_short_bancochile_portal_is_identified_after_restart(self) -> None:
        instance = controller.BancoChileSwitcher({})

        with (
            patch.object(controller, "_gp_get_window", return_value=Mock()),
            patch.object(controller, "_gp_get_portal_text", return_value="bchmfa"),
            patch.object(controller, "_gp_get_last_portal", return_value=""),
        ):
            status = instance._gp_connected_target()

        self.assertEqual(status, controller.BANCOCHILE)

    def test_connected_non_banco_portal_is_reported_unknown_after_restart(self) -> None:
        instance = controller.BancoChileSwitcher({})

        with (
            patch.object(controller, "_gp_get_window", return_value=Mock()),
            patch.object(controller, "_gp_get_portal_text", return_value="ext"),
            patch.object(controller, "_gp_get_last_portal", return_value=""),
        ):
            status = instance._gp_connected_target()

        self.assertEqual(status, controller.GPROT_UNKNOWN)

    def test_connecting_attempt_prefers_local_target_over_stale_registry_portal(
        self,
    ) -> None:
        instance = controller.BancoChileSwitcher({})
        instance._last_gp_target = controller.BANCOCHILE

        with (
            patch.object(controller, "_gp_get_window", return_value=Mock()),
            patch.object(controller, "_gp_get_portal_text", return_value=""),
            patch.object(controller, "_gp_get_last_portal") as last_portal,
        ):
            status = instance._gp_connected_target()

        self.assertEqual(status, controller.BANCOCHILE)
        last_portal.assert_not_called()

    def setUp(self) -> None:
        controller._autofill_cancel.clear()
        self.cleanup_patcher = patch.object(
            controller,
            "_gp_cleanup_terminal_windows",
            return_value=0,
        )
        self.cleanup_terminal_windows = self.cleanup_patcher.start()
        self.confirmed_cleanup_patcher = patch.object(
            controller,
            "_gp_cleanup_after_confirmed_connection",
            return_value=True,
        )
        self.cleanup_after_confirmed_connection = self.confirmed_cleanup_patcher.start()
        self.terminal_list_patcher = patch.object(
            controller,
            "_gp_list_terminal_windows",
            return_value=[],
        )
        self.terminal_list_patcher.start()
        self.transition_patcher = patch.object(
            controller,
            "_gp_wait_for_connect_transition",
            return_value="started",
        )
        self.wait_for_connect_transition = self.transition_patcher.start()

    def tearDown(self) -> None:
        self.transition_patcher.stop()
        self.terminal_list_patcher.stop()
        self.confirmed_cleanup_patcher.stop()
        self.cleanup_patcher.stop()
        controller._autofill_cancel.clear()

    def test_portal_normalization_accepts_url_form_without_changing_host(self) -> None:
        self.assertEqual(
            controller._normalize_gp_portal(" HTTPS://BCHMFA.BANCOCHILE.CL/ "),
            "bchmfa.bancochile.cl",
        )
        self.assertEqual(
            controller._normalize_gp_portal("ext.bice.cl"),
            "ext.bice.cl",
        )

    def test_login_window_falls_back_to_wrapping_win32_hwnd(self) -> None:
        wrapped = object()
        desktop = Mock()
        desktop.windows.return_value = []
        desktop.window.return_value.wrapper_object.return_value = wrapped

        with (
            patch("pywinauto.Desktop", return_value=desktop),
            patch.object(controller, "_gp_find_login_hwnd", return_value=4321),
        ):
            result = controller._gp_get_login_window(timeout=0.2)

        self.assertIs(result, wrapped)
        desktop.window.assert_called_once_with(handle=4321)

    def test_close_login_window_posts_wm_close_only_after_exact_identity(self) -> None:
        user32 = SimpleNamespace(
            GetWindowTextLengthW=Mock(return_value=len(controller.GP_LOGIN_TITLE)),
            GetWindowTextW=Mock(
                side_effect=lambda _hwnd, buffer, _size: setattr(
                    buffer, "value", controller.GP_LOGIN_TITLE
                )
            ),
            GetClassNameW=Mock(
                side_effect=lambda _hwnd, buffer, _size: setattr(
                    buffer, "value", controller.GP_CLASS
                )
            ),
            PostMessageW=Mock(return_value=1),
        )

        with patch.object(
            ctypes,
            "windll",
            SimpleNamespace(user32=user32),
            create=True,
        ):
            closed = controller._gp_close_login_window(4321)

        self.assertTrue(closed)
        user32.PostMessageW.assert_called_once_with(4321, 0x0010, 0, 0)

    def test_portal_selection_changes_and_verifies_bancochile_portal(self) -> None:
        portal = _PortalControl("ext.bice.cl")
        window = _Window([portal])

        with patch.object(controller.time, "sleep", return_value=None):
            selected = controller._gp_set_portal(
                window,
                "https://BCHMFA.BANCOCHILE.CL/",
            )

        self.assertTrue(selected)
        self.assertEqual(portal.set_calls, ["bchmfa.bancochile.cl"])
        self.assertEqual(
            controller._gp_get_portal_text(window),
            "bchmfa.bancochile.cl",
        )

    def test_portal_selection_fails_when_pangpa_does_not_keep_value(self) -> None:
        portal = _PortalControl("ext.bice.cl", update_on_set=False)
        window = _Window([portal])

        with patch.object(controller.time, "sleep", return_value=None):
            selected = controller._gp_set_portal(
                window,
                "bchmfa.bancochile.cl",
            )

        self.assertFalse(selected)

    def test_connected_target_is_classified_by_selected_portal(self) -> None:
        instance = controller.BancoChileSwitcher(
            {
                "gp_portal_url": "ext.bice.cl",
                "bancochile_portal_url": "bchmfa.bancochile.cl",
            }
        )
        window = object()

        with (
            patch.object(controller, "_gp_get_window", return_value=window),
            patch.object(
                controller,
                "_gp_get_portal_text",
                return_value="bchmfa.bancochile.cl",
            ),
        ):
            self.assertEqual(instance._gp_connected_target(), controller.BANCOCHILE)

        with (
            patch.object(controller, "_gp_get_window", return_value=window),
            patch.object(controller, "_gp_get_portal_text", return_value="ext.bice.cl"),
        ):
            self.assertEqual(
                instance._gp_connected_target(),
                controller.GPROT_UNKNOWN,
            )

    def test_unrecognized_connected_portal_is_not_mislabeled_as_bice(self) -> None:
        instance = controller.BancoChileSwitcher({})
        window = object()

        with (
            patch.object(controller, "_gp_get_window", return_value=window),
            patch.object(
                controller,
                "_gp_get_portal_text",
                return_value="another.example.test",
            ),
        ):
            target = instance._gp_connected_target()

        self.assertEqual(target, controller.GPROT_UNKNOWN)

    def test_password_is_requested_only_after_password_page_is_identified(self) -> None:
        password_provider = Mock(return_value="secret")
        password_edit = _Control("", "Edit", "Password")
        window = _Window(
            [
                _Control("Enter password"),
                password_edit,
                _Control("Sign in", "Button"),
            ]
        )
        connection_states = iter((False, True))

        with (
            patch.object(controller, "_gp_get_login_window", return_value=window),
            patch.object(controller, "_gp_prepare_login_window", return_value=True),
            patch.object(controller, "_gp_set_edit_value", return_value=True) as set_value,
            patch.object(controller, "_gp_submit_login_page", return_value=True),
            patch.object(controller.time, "sleep", return_value=None),
        ):
            result = controller._gp_handle_saml_signin(
                "user@bch.bancodechile.cl",
                password_provider=password_provider,
                connected_probe=lambda: next(connection_states),
            )

        self.assertEqual(result, "connected")
        password_provider.assert_called_once_with()
        set_value.assert_called_once_with(password_edit, "secret")

    def test_cancel_after_password_lookup_never_types_or_submits_secret(self) -> None:
        password_edit = _Control("", "Edit", "Password")
        window = _Window(
            [
                _Control("Enter password"),
                password_edit,
                _Control("Sign in", "Button"),
            ]
        )

        def password_provider():
            controller._autofill_cancel.set()
            return "secret"

        with (
            patch.object(controller, "_gp_get_login_window", return_value=window),
            patch.object(controller, "_gp_prepare_login_window", return_value=True),
            patch.object(controller, "_gp_set_edit_value") as set_value,
            patch.object(controller, "_gp_submit_login_page") as submit,
        ):
            result = controller._gp_handle_saml_signin(
                "user@bch.bancodechile.cl",
                password_provider=password_provider,
                connected_probe=lambda: False,
            )

        self.assertEqual(result, "cancelled")
        set_value.assert_not_called()
        submit.assert_not_called()

    def test_retry_writes_updated_password_on_existing_incorrect_password_page(
        self,
    ) -> None:
        password_provider = Mock(return_value="updated-secret")
        password_edit = _Control("", "Edit", "Password")
        window = _Window(
            [
                _Control("Your account or password is incorrect"),
                password_edit,
                _Control("Sign in", "Button"),
            ]
        )
        connected_states = iter((False, True))

        with (
            patch.object(controller, "_gp_get_login_window", return_value=window),
            patch.object(controller, "_gp_prepare_login_window", return_value=True),
            patch.object(controller, "_gp_set_edit_value", return_value=True) as set_value,
            patch.object(controller, "_gp_submit_login_page", return_value=True) as submit,
            patch.object(controller.time, "sleep", return_value=None),
        ):
            result = controller._gp_handle_saml_signin(
                "user@bch.bancodechile.cl",
                password_provider=password_provider,
                connected_probe=lambda: next(connected_states),
            )

        self.assertEqual(result, "connected")
        password_provider.assert_called_once_with()
        set_value.assert_called_once_with(password_edit, "updated-secret")
        submit.assert_called_once()

    def test_updated_password_is_reported_wrong_only_after_resubmission(self) -> None:
        password_provider = Mock(return_value="still-wrong")
        password_edit = _Control("", "Edit", "Password")
        window = _Window(
            [
                _Control("Password is incorrect"),
                password_edit,
                _Control("Sign in", "Button"),
            ]
        )

        with (
            patch.object(controller, "_gp_get_login_window", return_value=window),
            patch.object(controller, "_gp_prepare_login_window", return_value=True),
            patch.object(controller, "_gp_set_edit_value", return_value=True) as set_value,
            patch.object(controller, "_gp_submit_login_page", return_value=True) as submit,
            patch.object(controller.time, "sleep", return_value=None),
        ):
            result = controller._gp_handle_saml_signin(
                "user@bch.bancodechile.cl",
                password_provider=password_provider,
                connected_probe=lambda: False,
            )

        self.assertEqual(result, "wrong_password")
        password_provider.assert_called_once_with()
        set_value.assert_called_once_with(password_edit, "still-wrong")
        submit.assert_called_once()

    def test_corrected_password_allows_stale_error_to_linger_while_processing(
        self,
    ) -> None:
        password_provider = Mock(return_value="corrected-secret")
        password_edit = _Control("", "Edit", "Password")
        window = _Window(
            [
                _Control("Password is incorrect"),
                password_edit,
                _Control("Sign in", "Button"),
            ]
        )
        # The old validation text remains exposed for several UIA frames even
        # though Microsoft is accepting and processing the corrected password.
        connected_probe = Mock(side_effect=([False] * 7 + [True]))

        with (
            patch.object(controller, "_gp_get_login_window", return_value=window),
            patch.object(controller, "_gp_prepare_login_window", return_value=True),
            patch.object(controller, "_gp_set_edit_value", return_value=True) as set_value,
            patch.object(controller, "_gp_submit_login_page", return_value=True) as submit,
            patch.object(controller.time, "sleep", return_value=None),
        ):
            result = controller._gp_handle_saml_signin(
                "user@bch.bancodechile.cl",
                password_provider=password_provider,
                connected_probe=connected_probe,
            )

        self.assertEqual(result, "connected")
        password_provider.assert_called_once_with()
        set_value.assert_called_once_with(password_edit, "corrected-secret")
        submit.assert_called_once()

    def test_unknown_page_never_requests_or_types_password(self) -> None:
        password_provider = Mock(return_value="must-not-be-read")
        unknown_edit = _Control("", "Edit", "Search")
        window = _Window([_Control("Unexpected page"), unknown_edit])
        connection_states = iter((False, True))

        with (
            patch.object(controller, "_gp_get_login_window", return_value=window),
            patch.object(controller, "_gp_prepare_login_window", return_value=True),
            patch.object(controller, "_gp_set_edit_value") as set_value,
            patch.object(controller.time, "sleep", return_value=None),
        ):
            result = controller._gp_handle_saml_signin(
                "user@bch.bancodechile.cl",
                password_provider=password_provider,
                connected_probe=lambda: next(connection_states),
            )

        self.assertEqual(result, "connected")
        password_provider.assert_not_called()
        set_value.assert_not_called()

    def test_password_help_text_never_exposes_secret_to_email_edit(self) -> None:
        password_provider = Mock(return_value="must-not-be-read")
        email_edit = _Control("", "Edit", "Email")
        window = _Window(
            [
                _Control("¿Olvidó su contraseña?"),
                email_edit,
            ]
        )
        connection_states = iter((False, True))

        with (
            patch.object(controller, "_gp_get_login_window", return_value=window),
            patch.object(controller, "_gp_prepare_login_window", return_value=True),
            patch.object(controller, "_gp_set_edit_value") as set_value,
            patch.object(controller.time, "sleep", return_value=None),
        ):
            result = controller._gp_handle_saml_signin(
                "user@bch.bancodechile.cl",
                password_provider=password_provider,
                connected_probe=lambda: next(connection_states),
            )

        self.assertEqual(result, "connected")
        password_provider.assert_not_called()
        set_value.assert_not_called()

    def test_password_prompt_without_verified_password_edit_fails_closed(self) -> None:
        password_provider = Mock(return_value="must-not-be-read")
        window = _Window(
            [
                _Control("Enter password"),
                _Control("", "Edit", "Email"),
            ]
        )

        with (
            patch.object(controller, "_gp_get_login_window", return_value=window),
            patch.object(controller, "_gp_prepare_login_window", return_value=True),
            patch.object(controller.time, "sleep", return_value=None),
        ):
            result = controller._gp_handle_saml_signin(
                "user@bch.bancodechile.cl",
                password_provider=password_provider,
                connected_probe=lambda: False,
            )

        self.assertEqual(result, "unknown_page")
        password_provider.assert_not_called()

    def test_email_page_without_configured_username_requests_settings(self) -> None:
        password_provider = Mock(return_value="must-not-be-read")
        window = _Window(
            [
                _Control("Email, phone, or Skype"),
                _Control("", "Edit", "Email"),
                _Control("Next", "Button"),
            ]
        )

        with (
            patch.object(controller, "_gp_get_login_window", return_value=window),
            patch.object(controller, "_gp_prepare_login_window", return_value=True),
        ):
            result = controller._gp_handle_saml_signin(
                "",
                password_provider=password_provider,
                connected_probe=lambda: False,
            )

        self.assertEqual(result, "username_required")
        password_provider.assert_not_called()

    def test_mfa_is_reported_but_never_automated(self) -> None:
        password_provider = Mock(return_value="must-not-be-read")
        progress = Mock()
        window = _Window(
            [
                _Control("Approve sign in request"),
                _Control("Enter the number if prompted"),
                _Control("54"),
            ]
        )
        connection_states = iter((False, True))

        with (
            patch.object(controller, "_gp_get_login_window", return_value=window),
            patch.object(controller, "_gp_prepare_login_window", return_value=True),
            patch.object(controller, "_gp_set_edit_value") as set_value,
            patch.object(controller.time, "sleep", return_value=None),
        ):
            result = controller._gp_handle_saml_signin(
                "user@bch.bancodechile.cl",
                password_provider=password_provider,
                connected_probe=lambda: next(connection_states),
                progress=progress,
            )

        self.assertEqual(result, "connected")
        password_provider.assert_not_called()
        set_value.assert_not_called()
        progress.assert_called_once()
        self.assertIn("MFA", progress.call_args.args[0])

    def test_authenticator_method_choice_is_not_misclassified_as_mfa_request(self) -> None:
        password_provider = Mock(return_value="must-not-be-read")
        progress = Mock()
        window = _Window([_Control("Choose Microsoft Authenticator as a sign-in method")])
        connection_states = iter((False, True))

        with (
            patch.object(controller, "_gp_get_login_window", return_value=window),
            patch.object(controller, "_gp_prepare_login_window", return_value=True),
            patch.object(controller.time, "sleep", return_value=None),
        ):
            result = controller._gp_handle_saml_signin(
                "user@bch.bancodechile.cl",
                password_provider=password_provider,
                connected_probe=lambda: next(connection_states),
                progress=progress,
            )

        self.assertEqual(result, "connected")
        password_provider.assert_not_called()
        self.assertTrue(progress.called)
        self.assertNotIn("MFA", progress.call_args.args[0])

    def test_account_picker_does_not_select_similar_email(self) -> None:
        target = "user@bch.bancodechile.cl"
        window = _Window([_Control("Pick an account"), _Control(f"old-{target}")])
        connection_states = iter((False, True))

        with (
            patch.object(controller, "_gp_get_login_window", return_value=window),
            patch.object(controller, "_gp_prepare_login_window", return_value=True),
            patch.object(controller, "_gp_try_click_with_ancestors") as click,
            patch.object(controller.time, "sleep", return_value=None),
        ):
            result = controller._gp_handle_saml_signin(
                target,
                password_provider=Mock(return_value="must-not-be-read"),
                connected_probe=lambda: next(connection_states),
            )

        self.assertEqual(result, "connected")
        click.assert_not_called()

    def test_email_header_is_not_clicked_after_password_submission(self) -> None:
        target = "user@bch.bancodechile.cl"
        password_edit = _Control("", "Edit", "Password")
        password_window = _Window(
            [
                _Control("Enter password"),
                password_edit,
                _Control("Sign in", "Button"),
            ]
        )
        post_password_window = _Window(
            [_Control("Pick an account"), _Control(target)]
        )
        connection_states = iter((False, False, True))

        with (
            patch.object(
                controller,
                "_gp_get_login_window",
                side_effect=(password_window, post_password_window),
            ),
            patch.object(controller, "_gp_prepare_login_window", return_value=True),
            patch.object(controller, "_gp_set_edit_value", return_value=True),
            patch.object(controller, "_gp_submit_login_page", return_value=True),
            patch.object(controller, "_gp_try_click_with_ancestors") as click,
            patch.object(controller.time, "sleep", return_value=None),
        ):
            result = controller._gp_handle_saml_signin(
                target,
                password_provider=Mock(return_value="secret"),
                connected_probe=lambda: next(connection_states),
            )

        self.assertEqual(result, "connected")
        click.assert_not_called()

    def test_authentication_failed_page_stops_and_closes_login_window(self) -> None:
        window = _Window(
            [
                _Control("Authentication Failed"),
                _Control("Please contact the administrator for further assistance"),
            ]
        )
        window.handle = 4321

        with (
            patch.object(controller, "_gp_get_login_window", return_value=window),
            patch.object(controller, "_gp_prepare_login_window", return_value=True),
            patch.object(controller, "_gp_close_login_window") as close_login,
        ):
            result = controller._gp_handle_saml_signin(
                "user@bch.bancodechile.cl",
                password_provider=Mock(return_value="must-not-be-read"),
                connected_probe=lambda: False,
            )

        self.assertEqual(result, "authentication_failed")
        close_login.assert_called_once_with(4321)

    def test_cached_connection_finishes_without_opening_login_or_password(self) -> None:
        password_provider = Mock(return_value="must-not-be-read")

        with patch.object(controller, "_gp_get_login_window") as get_login:
            result = controller._gp_handle_saml_signin(
                "user@bch.bancodechile.cl",
                password_provider=password_provider,
                connected_probe=lambda: True,
            )

        self.assertEqual(result, "connected")
        get_login.assert_not_called()
        password_provider.assert_not_called()

    def test_login_window_is_prepared_only_once_for_the_same_hwnd(self) -> None:
        window = _Window(
            [
                _Control("Approve sign in request"),
                _Control("Open your Authenticator app and approve"),
            ]
        )
        window.handle = 2468
        connection_states = iter((False, False, True))

        with (
            patch.object(controller, "_gp_get_login_window", return_value=window),
            patch.object(controller, "_gp_prepare_login_window") as prepare,
            patch.object(controller.time, "sleep", return_value=None),
        ):
            result = controller._gp_handle_saml_signin(
                "user@bch.bancodechile.cl",
                password_provider=Mock(return_value="must-not-be-read"),
                connected_probe=lambda: next(connection_states),
            )

        self.assertEqual(result, "connected")
        prepare.assert_called_once_with(window)

    def test_saml_timeout_is_not_reported_as_connected(self) -> None:
        password_provider = Mock(return_value="must-not-be-read")

        with (
            patch.object(controller, "_gp_get_login_window", return_value=None),
            patch.object(controller.time, "sleep", return_value=None),
            patch.object(controller.time, "time", side_effect=(0.0, 0.0, 2.0)),
        ):
            result = controller._gp_handle_saml_signin(
                "user@bch.bancodechile.cl",
                password_provider=password_provider,
                total_timeout=1.0,
                connected_probe=lambda: False,
            )

        self.assertEqual(result, "timeout")
        password_provider.assert_not_called()

    def test_saml_stops_when_connecting_returns_to_not_connected(self) -> None:
        states = iter(("Connecting...", "Not Connected", "Not Connected", "Not Connected"))

        with (
            patch.object(controller, "_gp_get_login_window", return_value=None),
            patch.object(controller.time, "sleep", return_value=None),
        ):
            result = controller._gp_handle_saml_signin(
                "user@bch.bancodechile.cl",
                password_provider=Mock(return_value="must-not-be-read"),
                connected_probe=lambda: False,
                status_probe=lambda: next(states),
            )

        self.assertEqual(result, "connection_failed")

    def test_connection_failed_stops_before_reading_login_descendants(self) -> None:
        window = Mock()
        states = iter(("Connecting...", "Connection Failed"))

        with (
            patch.object(
                controller,
                "_gp_get_login_window",
                side_effect=(None, window),
            ) as get_login,
            patch.object(controller, "_gp_close_login_window") as close_login,
            patch.object(controller.time, "sleep", return_value=None),
        ):
            result = controller._gp_handle_saml_signin(
                "user@bch.bancodechile.cl",
                password_provider=Mock(return_value="must-not-be-read"),
                connected_probe=lambda: False,
                status_probe=lambda: next(states),
            )

        self.assertEqual(result, "connection_failed")
        get_login.assert_called_once_with(timeout=0.5)
        window.descendants.assert_not_called()
        close_login.assert_called_once_with()

    def test_bancochile_cached_connection_returns_before_portal_or_login_work(self) -> None:
        instance = controller.BancoChileSwitcher({})
        window = Mock()

        with (
            patch.object(controller, "_gp_diagnostics"),
            patch.object(controller, "_gp_get_window", return_value=window),
            patch.object(controller, "_gp_get_status_text", return_value="Connected"),
            patch.object(controller, "_gp_get_button_label", return_value="Disconnect"),
            patch.object(controller, "_gp_adapter_status", return_value="Up"),
            patch.object(
                instance,
                "_gp_connected_target",
                return_value=controller.BANCOCHILE,
            ),
            patch.object(controller, "_gp_set_portal") as set_portal,
            patch.object(controller, "_gp_invoke_connect_button") as invoke,
            patch.object(controller, "_gp_handle_saml_signin") as signin,
        ):
            ok, message = instance.connect_bancochile()

        self.assertTrue(ok)
        self.assertIn("already connected", message)
        set_portal.assert_not_called()
        invoke.assert_not_called()
        signin.assert_not_called()
        self.cleanup_after_confirmed_connection.assert_called_once_with(
            controller.BANCOCHILE_PORTAL
        )

    def test_bancochile_connection_timeout_returns_failure(self) -> None:
        instance = controller.BancoChileSwitcher(
            {
                "bancochile_username": "user@bch.bancodechile.cl",
                "bancochile_password_enc": "ciphertext",
            }
        )
        window = Mock()

        with (
            patch.object(controller, "_gp_diagnostics"),
            patch.object(controller, "_gp_get_window", return_value=window),
            patch.object(controller, "_gp_get_status_text", return_value="Not Connected"),
            patch.object(controller, "_gp_get_button_label", return_value="Connect"),
            patch.object(controller, "_gp_adapter_status", return_value="Down"),
            patch.object(controller, "_gp_set_portal", return_value=True),
            patch.object(controller, "_gp_invoke_connect_button", return_value=True),
            patch.object(controller, "_gp_handle_saml_signin", return_value="timeout"),
            patch.object(controller, "decrypt_password") as decrypt,
        ):
            ok, message = instance.connect_bancochile()

        self.assertFalse(ok)
        self.assertIn("timeout", message.casefold())
        decrypt.assert_not_called()

    def test_cancel_after_portal_selection_never_clicks_connect(self) -> None:
        instance = controller.BancoChileSwitcher(
            {
                "bancochile_username": "user@bch.bancodechile.cl",
                "bancochile_password_enc": "ciphertext",
            }
        )
        window = Mock()

        def select_then_cancel(_window, _portal):
            controller._autofill_cancel.set()
            return True

        with (
            patch.object(controller, "_gp_diagnostics"),
            patch.object(controller, "_gp_get_window", return_value=window),
            patch.object(controller, "_gp_get_status_text", return_value="Not Connected"),
            patch.object(controller, "_gp_get_button_label", return_value="Connect"),
            patch.object(controller, "_gp_adapter_status", return_value="Disabled"),
            patch.object(controller, "_gp_set_portal", side_effect=select_then_cancel),
            patch.object(controller, "_gp_invoke_connect_button") as invoke,
        ):
            ok, message = instance.connect_bancochile()

        self.assertFalse(ok)
        self.assertEqual(message, "__GP_CANCELLED__")
        invoke.assert_not_called()

    def test_bancochile_authentication_failed_explains_saml_rejection(self) -> None:
        instance = controller.BancoChileSwitcher(
            {
                "bancochile_username": "user@bch.bancodechile.cl",
                "bancochile_password_enc": "ciphertext",
            }
        )
        window = Mock()

        with (
            patch.object(controller, "_gp_diagnostics"),
            patch.object(controller, "_gp_get_window", return_value=window),
            patch.object(controller, "_gp_get_status_text", return_value="Not Connected"),
            patch.object(controller, "_gp_get_button_label", return_value="Connect"),
            patch.object(controller, "_gp_adapter_status", return_value="Down"),
            patch.object(controller, "_gp_set_portal", return_value=True),
            patch.object(controller, "_gp_invoke_connect_button", return_value=True),
            patch.object(
                controller,
                "_gp_handle_saml_signin",
                return_value="authentication_failed",
            ),
        ):
            ok, message = instance.connect_bancochile()

        self.assertFalse(ok)
        self.assertIn("Authentication Failed", message)
        self.assertIn("SAML", message)

    def test_silent_connect_click_reuses_window_for_one_safe_retry(self) -> None:
        instance = controller.BancoChileSwitcher(
            {
                "bancochile_username": "user@bch.bancodechile.cl",
                "bancochile_password_enc": "ciphertext",
            }
        )
        window = Mock()
        self.wait_for_connect_transition.side_effect = (
            "not_started",
            "started",
        )

        with (
            patch.object(controller, "_gp_diagnostics"),
            patch.object(controller, "_gp_get_window", return_value=window),
            patch.object(
                controller,
                "_gp_get_status_text",
                side_effect=("Not Connected", "Not Connected", "Not Connected"),
            ),
            patch.object(controller, "_gp_get_button_label", return_value="Connect"),
            patch.object(
                controller,
                "_gp_adapter_status",
                side_effect=("Disabled", "Disabled"),
            ),
            patch.object(controller, "_gp_set_portal", return_value=True),
            patch.object(
                controller,
                "_gp_invoke_connect_button",
                return_value=True,
            ) as invoke,
            patch.object(controller, "_gp_handle_saml_signin", return_value="connected"),
            patch.object(controller, "_open_gui") as open_gui,
            patch.object(controller, "_find_exe", return_value="PanGPA.exe"),
        ):
            ok, message = instance.connect_bancochile()

        self.assertTrue(ok)
        self.assertIn("connected", message.casefold())
        self.assertEqual(
            invoke.call_args_list,
            [
                call(window, expected_action="connect"),
                call(
                    window,
                    expected_action="connect",
                    prefer_click_input=True,
                ),
            ],
        )
        open_gui.assert_not_called()
        self.cleanup_after_confirmed_connection.assert_called_once_with(
            controller.BANCOCHILE_PORTAL
        )

    def test_physical_connect_retry_is_skipped_when_adapter_became_up(self) -> None:
        instance = controller.BancoChileSwitcher(
            {
                "bancochile_username": "user@bch.bancodechile.cl",
                "bancochile_password_enc": "ciphertext",
            }
        )
        window = Mock()
        self.wait_for_connect_transition.return_value = "not_started"

        with (
            patch.object(controller, "_gp_diagnostics"),
            patch.object(controller, "_gp_get_window", return_value=window),
            patch.object(controller, "_gp_get_status_text", return_value="Not Connected"),
            patch.object(controller, "_gp_get_button_label", return_value="Connect"),
            patch.object(
                controller,
                "_gp_adapter_status",
                side_effect=("Disabled", "Up"),
            ),
            patch.object(controller, "_gp_set_portal", return_value=True),
            patch.object(
                controller,
                "_gp_invoke_connect_button",
                return_value=True,
            ) as invoke,
            patch.object(controller, "_gp_handle_saml_signin") as signin,
            patch.object(controller, "_open_gui"),
            patch.object(controller, "_find_exe", return_value="PanGPA.exe"),
        ):
            ok, message = instance.connect_bancochile()

        self.assertTrue(ok)
        self.assertIn("connected", message.casefold())
        invoke.assert_called_once_with(window, expected_action="connect")
        signin.assert_not_called()

    def test_late_login_window_prevents_cleanup_and_second_connect_toggle(self) -> None:
        instance = controller.BancoChileSwitcher(
            {
                "bancochile_username": "user@bch.bancodechile.cl",
                "bancochile_password_enc": "ciphertext",
            }
        )
        window = Mock()
        self.wait_for_connect_transition.return_value = "not_started"

        with (
            patch.object(controller, "_gp_diagnostics"),
            patch.object(controller, "_gp_get_window", return_value=window),
            patch.object(controller, "_gp_get_status_text", return_value="Not Connected"),
            patch.object(controller, "_gp_get_button_label", return_value="Connect"),
            patch.object(controller, "_gp_adapter_status", return_value="Disabled"),
            patch.object(
                controller,
                "_gp_list_terminal_windows",
                side_effect=([], [(99, controller.GP_LOGIN_TITLE)]),
            ),
            patch.object(controller, "_gp_set_portal", return_value=True),
            patch.object(
                controller,
                "_gp_invoke_connect_button",
                return_value=True,
            ) as invoke,
            patch.object(
                controller,
                "_gp_handle_saml_signin",
                return_value="connected",
            ) as signin,
            patch.object(controller, "_open_gui"),
            patch.object(controller, "_find_exe", return_value="PanGPA.exe"),
        ):
            ok, message = instance.connect_bancochile()

        self.assertTrue(ok)
        self.assertIn("connected", message.casefold())
        invoke.assert_called_once_with(window, expected_action="connect")
        signin.assert_called_once()
        # Only the pre-connect cleanup is allowed; the newly-created Login
        # window must remain available to the SAML handler.
        self.cleanup_terminal_windows.assert_called_once_with(
            controller.BANCOCHILE_PORTAL,
            wait_seconds=0.5,
        )

    def test_late_success_notification_prevents_second_connect_toggle(self) -> None:
        instance = controller.BancoChileSwitcher(
            {
                "bancochile_username": "user@bch.bancodechile.cl",
                "bancochile_password_enc": "ciphertext",
            }
        )
        window = Mock()
        self.wait_for_connect_transition.return_value = "not_started"
        notification = "GlobalProtect Notification - bchmfa"

        with (
            patch.object(controller, "_gp_diagnostics"),
            patch.object(controller, "_gp_get_window", return_value=window),
            patch.object(controller, "_gp_get_status_text", return_value="Not Connected"),
            patch.object(controller, "_gp_get_button_label", return_value="Connect"),
            patch.object(controller, "_gp_adapter_status", return_value="Disabled"),
            patch.object(
                controller,
                "_gp_list_terminal_windows",
                # It appears only after the first retry snapshot, at the exact
                # point where the old code used to close it and click again.
                side_effect=([], [], [(100, notification)]),
            ),
            patch.object(controller, "_gp_set_portal", return_value=True),
            patch.object(
                controller,
                "_gp_invoke_connect_button",
                return_value=True,
            ) as invoke,
            patch.object(
                controller,
                "_gp_handle_saml_signin",
                return_value="connected",
            ) as signin,
            patch.object(controller, "_open_gui"),
            patch.object(controller, "_find_exe", return_value="PanGPA.exe"),
        ):
            ok, message = instance.connect_bancochile()

        self.assertTrue(ok)
        self.assertIn("connected", message.casefold())
        invoke.assert_called_once_with(window, expected_action="connect")
        signin.assert_called_once()

    def test_two_silent_connect_actions_fail_without_entering_long_saml_wait(self) -> None:
        instance = controller.BancoChileSwitcher(
            {
                "bancochile_username": "user@bch.bancodechile.cl",
                "bancochile_password_enc": "ciphertext",
            }
        )
        window = Mock()
        instance._gp_disconnect_confirmed = True
        self.wait_for_connect_transition.side_effect = (
            "not_started",
            "not_started",
        )

        with (
            patch.object(controller, "_gp_diagnostics"),
            patch.object(controller, "_gp_get_window", return_value=window),
            patch.object(controller, "_gp_get_status_text", return_value="Not Connected"),
            patch.object(controller, "_gp_get_button_label", return_value="Connect"),
            patch.object(controller, "_gp_adapter_status", return_value="Disabled"),
            patch.object(controller, "_gp_set_portal", return_value=True),
            patch.object(
                controller,
                "_gp_invoke_connect_button",
                return_value=True,
            ) as invoke,
            patch.object(controller, "_gp_handle_saml_signin") as signin,
            patch.object(controller, "_open_gui"),
            patch.object(controller, "_find_exe", return_value="PanGPA.exe"),
        ):
            ok, message = instance.connect_bancochile()

        self.assertFalse(ok)
        self.assertIn("ignored both Connect attempts", message)
        self.assertEqual(
            invoke.call_args_list,
            [
                call(window, expected_action="connect"),
                call(
                    window,
                    expected_action="connect",
                    prefer_click_input=True,
                ),
            ],
        )
        signin.assert_not_called()
        self.assertTrue(instance._gp_disconnect_confirmed)

    def test_post_disconnect_reconnect_waits_for_safe_connect_control(self) -> None:
        instance = controller.BancoChileSwitcher(
            {
                "bancochile_username": "user@bch.bancodechile.cl",
                "bancochile_password_enc": "ciphertext",
            }
        )
        instance._gp_disconnect_confirmed = True
        window = Mock()

        with (
            patch.object(controller, "_gp_diagnostics"),
            patch.object(controller, "_gp_get_window", return_value=window),
            patch.object(
                controller,
                "_gp_get_status_text",
                side_effect=("Connected", "Not Connected"),
            ),
            patch.object(
                controller,
                "_gp_get_button_label",
                side_effect=("Disconnect", "Connect"),
            ),
            patch.object(
                controller,
                "_gp_adapter_status",
                side_effect=("Disabled", "Disabled"),
            ),
            patch.object(
                controller,
                "_gp_login_window_present",
                return_value=True,
            ) as login_present,
            patch.object(controller, "_gp_set_portal", return_value=True),
            patch.object(
                controller,
                "_gp_invoke_connect_button",
                return_value=True,
            ) as invoke,
            patch.object(controller, "_gp_handle_saml_signin", return_value="connected"),
            patch.object(controller.time, "sleep", return_value=None),
        ):
            ok, message = instance.connect_bancochile()

        self.assertTrue(ok)
        self.assertIn("connected", message.casefold())
        invoke.assert_called_once_with(window, expected_action="connect")
        login_present.assert_not_called()
        self.assertFalse(instance._gp_disconnect_confirmed)

    def test_post_disconnect_stale_connecting_waits_then_sends_one_connect(
        self,
    ) -> None:
        instance = controller.BancoChileSwitcher(
            {
                "bancochile_username": "user@bch.bancodechile.cl",
                "bancochile_password_enc": "ciphertext",
            }
        )
        instance._gp_disconnect_confirmed = True
        window = Mock()

        with (
            patch.object(controller, "_gp_diagnostics"),
            patch.object(controller, "_gp_get_window", return_value=window),
            patch.object(
                controller,
                "_gp_get_status_text",
                side_effect=("Connecting...", "Connecting...", "Not Connected"),
            ),
            patch.object(controller, "_gp_get_button_label", return_value="Connect"),
            patch.object(
                controller,
                "_gp_adapter_status",
                side_effect=("Disabled", "Disabled", "Disabled"),
            ),
            patch.object(controller, "_gp_login_window_present") as login_present,
            patch.object(controller, "_gp_set_portal", return_value=True),
            patch.object(
                controller,
                "_gp_invoke_connect_button",
                return_value=True,
            ) as invoke,
            patch.object(
                controller,
                "_gp_handle_saml_signin",
                return_value="connected",
            ) as signin,
            patch.object(controller.time, "sleep", return_value=None),
        ):
            ok, message = instance.connect_bancochile()

        self.assertTrue(ok)
        self.assertIn("connected", message.casefold())
        invoke.assert_called_once_with(window, expected_action="connect")
        signin.assert_called_once()
        login_present.assert_not_called()
        self.assertFalse(instance._gp_disconnect_confirmed)

    def test_restart_does_not_trust_stale_connected_ui_with_disabled_adapter(
        self,
    ) -> None:
        instance = controller.BancoChileSwitcher(
            {
                "bancochile_username": "user@bch.bancodechile.cl",
                "bancochile_password_enc": "ciphertext",
            }
        )
        window = Mock()

        with (
            patch.object(controller, "_gp_diagnostics"),
            patch.object(controller, "_gp_get_window", return_value=window),
            patch.object(controller, "_gp_get_status_text", return_value="Connected"),
            patch.object(controller, "_gp_get_button_label", return_value="Disconnect"),
            patch.object(controller, "_gp_adapter_status", return_value="Disabled"),
            patch.object(controller, "_gp_set_portal") as set_portal,
            patch.object(controller, "_gp_invoke_connect_button") as invoke,
            patch.object(controller.time, "sleep", return_value=None),
        ):
            ok, message = instance.connect_bancochile()

        self.assertFalse(ok)
        self.assertIn("still settling", message)
        set_portal.assert_not_called()
        invoke.assert_not_called()

    def test_post_disconnect_stale_connected_retry_never_presses_toggle_twice(
        self,
    ) -> None:
        instance = controller.BancoChileSwitcher(
            {
                "bancochile_username": "user@bch.bancodechile.cl",
                "bancochile_password_enc": "ciphertext",
            }
        )
        instance._gp_disconnect_confirmed = True
        window = Mock()
        self.wait_for_connect_transition.return_value = "not_started"

        with (
            patch.object(controller, "_gp_diagnostics"),
            patch.object(controller, "_gp_get_window", return_value=window),
            patch.object(
                controller,
                "_gp_get_status_text",
                side_effect=("Not Connected", "Connected"),
            ),
            patch.object(
                controller,
                "_gp_get_button_label",
                side_effect=("Connect", "Disconnect"),
            ),
            patch.object(controller, "_gp_adapter_status", return_value="Disabled"),
            patch.object(controller, "_gp_set_portal", return_value=True),
            patch.object(
                controller,
                "_gp_invoke_connect_button",
                return_value=True,
            ) as invoke,
            patch.object(controller, "_gp_handle_saml_signin") as signin,
            patch.object(controller, "_open_gui"),
            patch.object(controller, "_find_exe", return_value="PanGPA.exe"),
        ):
            ok, message = instance.connect_bancochile()

        self.assertFalse(ok)
        self.assertIn("No second button was pressed", message)
        invoke.assert_called_once_with(window, expected_action="connect")
        signin.assert_not_called()
        self.assertTrue(instance._gp_disconnect_confirmed)

    def test_connecting_bancochile_is_resumed_not_reported_as_connected(self) -> None:
        instance = controller.BancoChileSwitcher(
            {
                "bancochile_username": "user@bch.bancodechile.cl",
                "bancochile_password_enc": "ciphertext",
            }
        )
        window = Mock()

        with (
            patch.object(controller, "_gp_diagnostics"),
            patch.object(controller, "_gp_get_window", return_value=window),
            patch.object(controller, "_gp_get_status_text", return_value="Connecting..."),
            patch.object(controller, "_gp_get_button_label", return_value="Disconnect"),
            patch.object(controller, "_gp_adapter_status", return_value="Down"),
            patch.object(
                instance,
                "_gp_connected_target",
                return_value=controller.BANCOCHILE,
            ),
            patch.object(controller, "_gp_login_window_present", return_value=True),
            patch.object(controller, "_gp_set_portal") as set_portal,
            patch.object(controller, "_gp_invoke_connect_button") as invoke,
            patch.object(
                controller,
                "_gp_handle_saml_signin",
                return_value="connected",
            ) as signin,
        ):
            ok, message = instance.connect_bancochile()

        self.assertTrue(ok)
        self.assertNotIn("already connected", message)
        set_portal.assert_not_called()
        invoke.assert_not_called()
        signin.assert_called_once()

    def test_connecting_bancochile_resumes_when_button_label_lags_on_connect(
        self,
    ) -> None:
        instance = controller.BancoChileSwitcher(
            {
                "bancochile_username": "user@bch.bancodechile.cl",
                "bancochile_password_enc": "ciphertext",
            }
        )
        window = Mock()

        with (
            patch.object(controller, "_gp_diagnostics"),
            patch.object(controller, "_gp_get_window", return_value=window),
            patch.object(controller, "_gp_get_status_text", return_value="Connecting..."),
            patch.object(controller, "_gp_get_button_label", return_value="Connect"),
            patch.object(controller, "_gp_adapter_status", return_value=""),
            patch.object(
                instance,
                "_gp_connected_target",
                return_value=controller.BANCOCHILE,
            ),
            patch.object(controller, "_gp_set_portal") as set_portal,
            patch.object(controller, "_gp_invoke_connect_button") as invoke,
            patch.object(
                controller,
                "_gp_handle_saml_signin",
                return_value="connected",
            ) as signin,
        ):
            ok, message = instance.connect_bancochile()

        self.assertTrue(ok)
        self.assertNotIn("already connected", message)
        set_portal.assert_not_called()
        invoke.assert_not_called()
        signin.assert_called_once()

    def test_disconnect_never_clicks_a_visible_connect_control(self) -> None:
        instance = controller.BancoChileSwitcher({})
        instance._last_gp_target = controller.BANCOCHILE
        window = Mock()

        with (
            patch.object(controller, "_gp_diagnostics"),
            patch.object(controller, "_gp_get_window", return_value=window),
            patch.object(controller, "_gp_get_status_text", return_value="Connection Failed"),
            patch.object(controller, "_gp_get_button_label", return_value="Connect"),
            patch.object(
                controller,
                "_gp_adapter_status",
                side_effect=("Disabled", "Disabled"),
            ),
            patch.object(controller, "_gp_invoke_connect_button") as invoke,
            patch.object(controller.time, "sleep", return_value=None),
        ):
            ok, message = instance.disconnect_bancochile()

        self.assertTrue(ok)
        self.assertIn("already disconnected", message)
        self.assertIsNone(instance._last_gp_target)
        invoke.assert_not_called()

    def test_disconnect_does_not_treat_connecting_with_lagging_connect_as_clear(
        self,
    ) -> None:
        instance = controller.BancoChileSwitcher({})
        instance._last_gp_target = controller.BANCOCHILE
        window = Mock()

        with (
            patch.object(controller, "_gp_diagnostics"),
            patch.object(controller, "_gp_get_window", return_value=window),
            patch.object(controller, "_gp_get_status_text", return_value="Connecting..."),
            patch.object(controller, "_gp_get_button_label", return_value="Connect"),
            patch.object(controller, "_gp_adapter_status", return_value="Disabled") as adapter,
            patch.object(controller, "_gp_invoke_connect_button") as invoke,
            patch.object(controller.time, "sleep", return_value=None),
        ):
            ok, message = instance.disconnect_bancochile()

        self.assertFalse(ok)
        self.assertIn("did not finish disconnecting", message)
        self.assertFalse(instance._gp_disconnect_confirmed)
        self.assertEqual(adapter.call_count, 1)
        invoke.assert_not_called()

    def test_disconnect_never_toggles_stale_connected_ui_with_disabled_adapter(
        self,
    ) -> None:
        instance = controller.BancoChileSwitcher({})
        instance._last_gp_target = controller.BANCOCHILE
        window = Mock()

        with (
            patch.object(controller, "_gp_diagnostics"),
            patch.object(controller, "_gp_get_window", return_value=window),
            patch.object(controller, "_gp_get_status_text", return_value="Connected"),
            patch.object(controller, "_gp_get_button_label", return_value="Disconnect"),
            patch.object(controller, "_gp_adapter_status", return_value="Disabled"),
            patch.object(controller, "_gp_invoke_connect_button") as invoke,
            patch.object(controller.time, "sleep", return_value=None),
        ):
            ok, message = instance.disconnect_bancochile()

        self.assertFalse(ok)
        self.assertIn("did not finish disconnecting", message)
        self.assertFalse(instance._gp_disconnect_confirmed)
        invoke.assert_not_called()

    def test_disconnect_cancels_fresh_connecting_state_after_stale_ui(self) -> None:
        instance = controller.BancoChileSwitcher({})
        instance._last_gp_target = controller.BANCOCHILE
        window = Mock()

        with (
            patch.object(controller, "_gp_diagnostics"),
            patch.object(controller, "_gp_get_window", return_value=window),
            patch.object(
                controller,
                "_gp_get_status_text",
                side_effect=(
                    "Connected",
                    "Connecting...",
                    "Connecting...",
                    "Disconnected",
                    "Disconnected",
                ),
            ),
            patch.object(controller, "_gp_get_button_label", return_value="Disconnect"),
            patch.object(
                controller,
                "_gp_adapter_status",
                side_effect=("Disabled", "Disabled", "Disabled"),
            ),
            patch.object(
                controller,
                "_gp_invoke_connect_button",
                return_value=True,
            ) as invoke,
            patch.object(controller.time, "sleep", return_value=None),
        ):
            ok, message = instance.disconnect_bancochile()

        self.assertTrue(ok)
        self.assertIn("disconnected", message.casefold())
        invoke.assert_called_once_with(window, expected_action="disconnect")

    def test_disconnect_waits_when_ui_already_reports_disconnecting(self) -> None:
        instance = controller.BancoChileSwitcher({})
        instance._last_gp_target = controller.BANCOCHILE
        window = Mock()

        with (
            patch.object(controller, "_gp_diagnostics"),
            patch.object(controller, "_gp_get_window", return_value=window),
            patch.object(controller, "_gp_get_status_text", return_value="Disconnecting..."),
            patch.object(controller, "_gp_get_button_label", return_value="Disconnect"),
            patch.object(controller, "_gp_adapter_status", return_value="Disabled"),
            patch.object(controller, "_gp_invoke_connect_button") as invoke,
            patch.object(controller.time, "sleep", return_value=None),
        ):
            ok, message = instance.disconnect_bancochile()

        self.assertTrue(ok)
        self.assertIn("disconnected", message.casefold())
        self.assertTrue(instance._gp_disconnect_confirmed)
        invoke.assert_not_called()

    def test_disconnect_bancochile_clicks_disconnect_and_confirms_state(self) -> None:
        instance = controller.BancoChileSwitcher({})
        instance._last_gp_target = controller.BANCOCHILE
        window = Mock()

        with (
            patch.object(controller, "_gp_diagnostics"),
            patch.object(controller, "_gp_get_window", return_value=window),
            patch.object(
                controller,
                "_gp_get_status_text",
                side_effect=("Connected", "Disconnected", "Disconnected"),
            ),
            patch.object(controller, "_gp_get_button_label", return_value="Disconnect"),
            patch.object(
                controller,
                "_gp_adapter_status",
                side_effect=("Up", "Disabled", "Disabled"),
            ),
            patch.object(controller, "_gp_invoke_connect_button", return_value=True) as invoke,
            patch.object(controller.time, "sleep", return_value=None),
        ):
            ok, message = instance.disconnect_bancochile()

        self.assertTrue(ok)
        self.assertIn("disconnected", message.casefold())
        self.assertIsNone(instance._last_gp_target)
        self.assertTrue(instance._gp_disconnect_confirmed)
        invoke.assert_called_once_with(window, expected_action="disconnect")

    def test_cancel_after_disconnect_click_still_records_confirmed_teardown(self) -> None:
        instance = controller.BancoChileSwitcher({})
        instance._last_gp_target = controller.BANCOCHILE
        window = Mock()

        def click_then_cancel(*_args, **_kwargs):
            controller._autofill_cancel.set()
            return True

        with (
            patch.object(controller, "_gp_diagnostics"),
            patch.object(controller, "_gp_get_window", return_value=window),
            patch.object(controller, "_gp_get_status_text", return_value="Connected"),
            patch.object(controller, "_gp_get_button_label", return_value="Disconnect"),
            patch.object(
                controller,
                "_gp_adapter_status",
                side_effect=("Up", "Disabled", "Disabled"),
            ),
            patch.object(
                controller,
                "_gp_invoke_connect_button",
                side_effect=click_then_cancel,
            ) as invoke,
            patch.object(controller.time, "sleep", return_value=None),
        ):
            ok, message = instance.disconnect_bancochile()

        self.assertFalse(ok)
        self.assertEqual(message, "__GP_CANCELLED__")
        self.assertTrue(instance._gp_disconnect_confirmed)
        self.assertIsNone(instance._last_gp_target)
        invoke.assert_called_once_with(window, expected_action="disconnect")

    def test_disconnect_retries_one_silent_action_with_physical_click(self) -> None:
        instance = controller.BancoChileSwitcher({})
        instance._last_gp_target = controller.BANCOCHILE
        window = Mock()

        with (
            patch.object(controller, "_gp_diagnostics"),
            patch.object(controller, "_gp_get_window", return_value=window),
            patch.object(
                controller,
                "_gp_get_status_text",
                side_effect=(
                    "Connected",
                    "Connected",
                    "Connected",
                    "Connected",
                    "Connected",
                    "Connected",
                    "Disconnected",
                    "Disconnected",
                ),
            ),
            patch.object(controller, "_gp_get_button_label", return_value="Disconnect"),
            patch.object(
                controller,
                "_gp_adapter_status",
                side_effect=("Up", "Up", "Up", "Disabled", "Disabled"),
            ),
            patch.object(
                controller,
                "_gp_invoke_connect_button",
                return_value=True,
            ) as invoke,
            patch.object(controller.time, "sleep", return_value=None),
            patch.object(controller, "_find_exe", return_value="PanGPA.exe"),
        ):
            ok, message = instance.disconnect_bancochile()

        self.assertTrue(ok)
        self.assertIn("disconnected", message.casefold())
        self.assertEqual(
            invoke.call_args_list,
            [
                call(window, expected_action="disconnect"),
                call(
                    window,
                    expected_action="disconnect",
                    prefer_click_input=True,
                ),
            ],
        )

    def test_disconnect_skips_physical_retry_when_adapter_already_went_down(
        self,
    ) -> None:
        instance = controller.BancoChileSwitcher({})
        instance._last_gp_target = controller.BANCOCHILE
        window = Mock()

        with (
            patch.object(controller, "_gp_diagnostics"),
            patch.object(controller, "_gp_get_window", return_value=window),
            patch.object(controller, "_gp_get_status_text", return_value="Connected"),
            patch.object(controller, "_gp_get_button_label", return_value="Disconnect"),
            patch.object(
                controller,
                "_gp_adapter_status",
                side_effect=("Up", "Up", "Disabled", "Disabled", "Disabled"),
            ),
            patch.object(
                controller,
                "_gp_invoke_connect_button",
                return_value=True,
            ) as invoke,
            patch.object(controller.time, "sleep", return_value=None),
        ):
            ok, message = instance.disconnect_bancochile()

        self.assertTrue(ok)
        self.assertIn("disconnected", message.casefold())
        invoke.assert_called_once_with(window, expected_action="disconnect")

    def test_disconnect_accepts_two_disabled_adapter_samples_when_ui_disappears(self) -> None:
        instance = controller.BancoChileSwitcher({})
        instance._last_gp_target = controller.BANCOCHILE
        window = Mock()

        with (
            patch.object(controller, "_gp_diagnostics"),
            patch.object(
                controller,
                "_gp_get_window",
                side_effect=(window, None, None, None, None, None, None),
            ),
            patch.object(controller, "_gp_get_status_text", return_value="Connected"),
            patch.object(controller, "_gp_get_button_label", return_value="Disconnect"),
            patch.object(
                controller,
                "_gp_adapter_status",
                side_effect=("Up", "Disabled", "Disabled"),
            ) as adapter_status,
            patch.object(
                controller,
                "_gp_invoke_connect_button",
                return_value=True,
            ) as invoke,
            patch.object(controller.time, "sleep", return_value=None),
            patch.object(controller, "_open_gui") as open_gui,
            patch.object(controller, "_find_exe", return_value="PanGPA.exe"),
        ):
            ok, message = instance.disconnect_bancochile()

        self.assertTrue(ok)
        self.assertIn("disconnected", message.casefold())
        open_gui.assert_not_called()
        self.assertEqual(adapter_status.call_count, 3)
        invoke.assert_called_once_with(window, expected_action="disconnect")

    def test_disconnect_does_not_trust_disconnected_ui_while_adapter_is_up(self) -> None:
        instance = controller.BancoChileSwitcher({})
        instance._last_gp_target = controller.BANCOCHILE
        window = Mock()

        with (
            patch.object(controller, "_gp_diagnostics"),
            patch.object(controller, "_gp_get_window", return_value=window),
            patch.object(controller, "_gp_get_status_text", return_value="Disconnected"),
            patch.object(controller, "_gp_get_button_label", return_value="Connect"),
            patch.object(controller, "_gp_adapter_status", return_value="Up"),
            patch.object(controller, "_gp_invoke_connect_button") as invoke,
            patch.object(controller.time, "sleep", return_value=None),
        ):
            ok, message = instance.disconnect_bancochile()

        self.assertFalse(ok)
        self.assertIn("did not finish disconnecting", message)
        self.assertEqual(instance._last_gp_target, controller.BANCOCHILE)
        invoke.assert_not_called()

    def test_disconnect_bancochile_timeout_is_a_failure(self) -> None:
        instance = controller.BancoChileSwitcher({})
        window = Mock()

        with (
            patch.object(controller, "_gp_diagnostics"),
            patch.object(controller, "_gp_get_window", return_value=window),
            patch.object(controller, "_gp_get_status_text", return_value="Connected"),
            patch.object(controller, "_gp_get_button_label", return_value="Disconnect"),
            patch.object(controller, "_gp_adapter_status", return_value="Up"),
            patch.object(controller, "_gp_invoke_connect_button", return_value=True),
            patch.object(controller.time, "sleep", return_value=None),
        ):
            ok, message = instance.disconnect_bancochile()

        self.assertFalse(ok)
        self.assertIn("did not finish disconnecting", message)


if __name__ == "__main__":
    unittest.main()
