from __future__ import annotations

"""Banco de Chile switcher and legacy-isolation regression tests."""

import ctypes
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import ANY, Mock
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


def _uia_button(
    name: str,
    *,
    automation_id: str,
    class_name: str = "SystemTray.NormalButton",
    visible: bool = True,
):
    control = Mock()
    control.window_text.return_value = name
    control.element_info = SimpleNamespace(
        control_type="Button",
        name=name,
        automation_id=automation_id,
        class_name=class_name,
    )
    control.is_visible.return_value = visible
    control.is_enabled.return_value = True
    rectangle = Mock()
    rectangle.width.return_value = 24
    rectangle.height.return_value = 24
    control.rectangle.return_value = rectangle
    return control


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
    def setUp(self) -> None:
        self.adapter_status_patcher = patch.object(
            controller,
            "_gp_adapter_status",
            return_value="Disabled",
        )
        self.adapter_status_patcher.start()
        self.adapter_sample_patcher = patch.object(
            controller,
            "_gp_adapter_status_sample",
            side_effect=lambda **_kwargs: (True, controller._gp_adapter_status()),
        )
        self.adapter_sample_patcher.start()

    def tearDown(self) -> None:
        self.adapter_sample_patcher.stop()
        self.adapter_status_patcher.stop()

    def test_switcher_source_has_no_unicode_replacement_characters(self) -> None:
        source = (
            SRC_DIR / "features" / "vpn" / "switcher_bancodechile.py"
        ).read_text(encoding="utf-8")

        self.assertNotIn("\ufffd", source)

    def test_saml_click_uses_one_physical_action_and_never_invoke(self) -> None:
        control = Mock()

        clicked = controller._gp_try_click(control, "saml_submit")

        self.assertTrue(clicked)
        control.click_input.assert_called_once_with()
        control.invoke.assert_not_called()

    def test_saml_click_exception_does_not_issue_a_second_action(self) -> None:
        control = Mock()
        control.click_input.side_effect = RuntimeError("ambiguous delivery")

        clicked = controller._gp_try_click(control, "picker_match")

        self.assertTrue(clicked)
        control.click_input.assert_called_once_with()
        control.invoke.assert_not_called()

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
                "_gp_get_portal_text",
                return_value=controller.BANCOCHILE_PORTAL,
            ),
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
            patch.object(controller, "_gp_list_terminal_windows", return_value=[]),
            patch.object(controller, "_gp_get_last_portal", return_value=""),
            patch.object(controller, "_gp_cleanup_terminal_windows") as cleanup,
            patch.object(controller.time, "sleep", return_value=None),
        ):
            confirmed = controller._gp_cleanup_after_confirmed_connection(
                controller.BANCOCHILE_PORTAL,
                attempts=2,
            )

        self.assertFalse(confirmed)
        cleanup.assert_not_called()

    def test_cleanup_uses_exact_notification_when_main_popup_is_unreadable(self) -> None:
        notification = "GlobalProtect Notification - bchmfa"
        with (
            patch.object(controller, "_gp_get_window", return_value=None),
            patch.object(
                controller,
                "_gp_adapter_status_sample",
                return_value=(True, "Up"),
            ),
            patch.object(
                controller,
                "_gp_list_terminal_windows",
                return_value=[(20, notification)],
            ),
            patch.object(controller, "_gp_cleanup_terminal_windows") as cleanup,
        ):
            confirmed = controller._gp_cleanup_after_confirmed_connection(
                controller.BANCOCHILE_PORTAL,
                attempts=1,
            )

        self.assertTrue(confirmed)
        cleanup.assert_called_once_with(
            controller.BANCOCHILE_PORTAL,
            wait_seconds=1.5,
        )

    def test_cleanup_uses_successful_login_and_exact_persisted_portal(self) -> None:
        with (
            patch.object(controller, "_gp_get_window", return_value=None),
            patch.object(
                controller,
                "_gp_adapter_status_sample",
                return_value=(True, "Up"),
            ),
            patch.object(
                controller,
                "_gp_list_terminal_windows",
                return_value=[(10, controller.GP_LOGIN_TITLE)],
            ),
            patch.object(
                controller,
                "_gp_get_last_portal",
                return_value=controller.BANCOCHILE_PORTAL,
            ),
            patch.object(
                controller,
                "_gp_login_reports_success",
                return_value=True,
            ) as login_success,
            patch.object(controller, "_gp_cleanup_terminal_windows") as cleanup,
        ):
            confirmed = controller._gp_cleanup_after_confirmed_connection(
                controller.BANCOCHILE_PORTAL,
                attempts=1,
                ignored_login_hwnds={9},
            )

        self.assertTrue(confirmed)
        login_success.assert_called_once_with(ignored_hwnds={9})
        cleanup.assert_called_once_with(
            controller.BANCOCHILE_PORTAL,
            wait_seconds=1.5,
        )

    def test_login_success_detection_revalidates_exact_fresh_window(self) -> None:
        window = _Window([_Control("Login Successful!")])
        window.handle = 77
        with (
            patch.object(controller, "_gp_get_login_window", return_value=window),
            patch.object(
                controller,
                "_gp_is_exact_pangpa_window",
                return_value=True,
            ),
        ):
            successful = controller._gp_login_reports_success(
                ignored_hwnds={10},
            )

        self.assertTrue(successful)

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

    def test_connect_transition_does_not_treat_notification_as_fresh_login(self) -> None:
        window = Mock()
        with (
            patch.object(controller, "_gp_get_window", return_value=window),
            patch.object(controller, "_gp_get_status_text", return_value="Disconnected"),
            patch.object(controller, "_gp_get_button_label", return_value="Connect"),
            patch.object(
                controller,
                "_gp_list_terminal_windows",
                return_value=[(20, "GlobalProtect Notification - bchmfa")],
            ),
        ):
            result = controller._gp_wait_for_connect_transition(
                controller.BANCOCHILE_PORTAL,
                attempts=1,
            )

        self.assertEqual(result, "not_started")


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
            patch.object(controller, "_gp_get_monitor_window", return_value=None),
            patch.object(controller, "_gp_adapter_status", return_value=""),
        ):
            status = instance.get_bancochile_status()

        self.assertEqual(status, controller.NONE)

    def test_unidentified_active_globalprotect_is_not_claimed_as_banco(self) -> None:
        instance = controller.BancoChileSwitcher({})

        with (
            patch.object(controller, "_gp_get_monitor_window", return_value=None),
            patch.object(controller, "_gp_adapter_status", return_value="Up"),
        ):
            status = instance.get_bancochile_status()

        self.assertEqual(status, controller.GPROT_UNKNOWN)

    def test_status_trusts_connected_ui_during_adapter_startup(self) -> None:
        instance = controller.BancoChileSwitcher({})
        window = Mock()

        with (
            patch.object(controller, "_gp_get_monitor_window", return_value=window),
            patch.object(
                controller,
                "_gp_get_monitor_portal_text",
                return_value=controller.BANCOCHILE_PORTAL,
            ),
            patch.object(controller, "_gp_get_monitor_status_text", return_value="Connected"),
        ):
            status = instance.get_bancochile_status("Disabled")

        self.assertEqual(status, controller.BANCOCHILE)
        self.assertFalse(instance._gp_disconnect_confirmed)

    def test_manual_disconnect_overrides_stale_connected_ui_after_two_samples(self) -> None:
        instance = controller.BancoChileSwitcher({})
        window = Mock()

        with (
            patch.object(controller, "_gp_get_monitor_window", return_value=window),
            patch.object(
                controller,
                "_gp_get_monitor_portal_text",
                return_value=controller.BANCOCHILE_PORTAL,
            ),
            patch.object(controller, "_gp_get_monitor_status_text", return_value="Connected"),
            patch.object(controller, "_gp_login_window_present", return_value=False),
        ):
            first = instance.get_bancochile_status("Disabled")
            second = instance.get_bancochile_status("Disabled")

        self.assertEqual(first, controller.BANCOCHILE)
        self.assertEqual(second, controller.NONE)

    def test_adapter_up_resets_manual_disconnect_debounce(self) -> None:
        instance = controller.BancoChileSwitcher({})
        window = Mock()

        with (
            patch.object(controller, "_gp_get_monitor_window", return_value=window),
            patch.object(
                controller,
                "_gp_get_monitor_portal_text",
                return_value=controller.BANCOCHILE_PORTAL,
            ),
            patch.object(controller, "_gp_get_monitor_status_text", return_value="Connected"),
            patch.object(controller, "_gp_login_window_present", return_value=False),
        ):
            self.assertEqual(
                instance.get_bancochile_status("Disabled"),
                controller.BANCOCHILE,
            )
            self.assertEqual(
                instance.get_bancochile_status("Up"),
                controller.BANCOCHILE,
            )
            self.assertEqual(
                instance.get_bancochile_status("Disabled"),
                controller.BANCOCHILE,
            )

    def test_status_uses_disabled_adapter_over_stale_ui_after_confirmed_disconnect(
        self,
    ) -> None:
        instance = controller.BancoChileSwitcher({})
        instance._gp_disconnect_confirmed = True
        window = Mock()

        with (
            patch.object(controller, "_gp_get_monitor_window", return_value=window) as get_window,
            patch.object(controller, "_gp_get_monitor_portal_text", return_value=""),
            patch.object(controller, "_gp_get_monitor_status_text", return_value="Connected"),
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
            patch.object(controller, "_gp_get_monitor_window", return_value=window),
            patch.object(controller, "_gp_get_monitor_portal_text", return_value=""),
            patch.object(controller, "_gp_get_monitor_status_text", return_value="Connecting..."),
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
            patch.object(controller, "_gp_get_monitor_window", return_value=window),
            patch.object(controller, "_gp_get_monitor_portal_text", return_value=""),
            patch.object(
                controller,
                "_gp_adapter_status_sample",
                return_value=(False, ""),
            ),
            patch.object(controller, "_gp_get_monitor_status_text", return_value="Connected"),
        ):
            status = instance.get_bancochile_status()

        self.assertEqual(status, controller.GPROT_UNKNOWN)
        self.assertFalse(instance.last_status_probe_ok)
        self.assertTrue(instance._gp_disconnect_confirmed)

    def test_failed_status_probe_preserves_existing_banco_ownership(self) -> None:
        instance = controller.BancoChileSwitcher({})
        instance._last_gp_target = controller.BANCOCHILE
        instance._gp_disconnect_confirmed = True
        instance._terminal_adapter_samples = 1

        with (
            patch.object(
                controller,
                "_gp_adapter_status_sample",
                return_value=(False, ""),
            ),
            patch.object(controller, "_gp_get_monitor_window") as get_window,
        ):
            status = instance.get_bancochile_status()

        self.assertEqual(status, controller.GPROT_UNKNOWN)
        self.assertFalse(instance.last_status_probe_ok)
        self.assertEqual(instance._last_gp_target, controller.BANCOCHILE)
        self.assertTrue(instance._gp_disconnect_confirmed)
        self.assertEqual(instance._terminal_adapter_samples, 1)
        get_window.assert_not_called()

    def test_other_bancochile_domain_is_not_the_configured_portal(self) -> None:
        instance = controller.BancoChileSwitcher(
            {"bancochile_portal_url": controller.BANCOCHILE_PORTAL}
        )
        window = Mock()

        with (
            patch.object(controller, "_gp_get_monitor_window", return_value=window),
            patch.object(
                controller,
                "_gp_get_monitor_portal_text",
                return_value="different.bancochile.cl",
            ),
            patch.object(controller, "_gp_get_monitor_status_text", return_value="Connected"),
        ):
            target = instance._gp_connected_target()
            active = instance.is_bancochile_active()
            status = instance.get_bancochile_status("Up")

        self.assertEqual(target, controller.GPROT_UNKNOWN)
        self.assertFalse(active)
        self.assertEqual(status, controller.GPROT_UNKNOWN)
        self.assertTrue(instance.last_status_probe_ok)

    def test_connected_short_bancochile_portal_is_identified_after_restart(self) -> None:
        instance = controller.BancoChileSwitcher({})

        with (
            patch.object(controller, "_gp_get_monitor_window", return_value=Mock()),
            patch.object(controller, "_gp_get_monitor_portal_text", return_value="bchmfa"),
            patch.object(controller, "_gp_get_last_portal", return_value=""),
        ):
            status = instance._gp_connected_target()

        self.assertEqual(status, controller.BANCOCHILE)

    def test_connected_non_banco_portal_is_reported_unknown_after_restart(self) -> None:
        instance = controller.BancoChileSwitcher({})

        with (
            patch.object(controller, "_gp_get_monitor_window", return_value=Mock()),
            patch.object(controller, "_gp_get_monitor_portal_text", return_value="ext"),
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
            patch.object(controller, "_gp_get_monitor_window", return_value=Mock()),
            patch.object(controller, "_gp_get_monitor_portal_text", return_value=""),
            patch.object(controller, "_gp_get_last_portal") as last_portal,
        ):
            status = instance._gp_connected_target()

        self.assertEqual(status, controller.BANCOCHILE)
        last_portal.assert_not_called()

    def setUp(self) -> None:
        controller._autofill_cancel.clear()
        # Production decisions use the tri-state sampler.  Keep the older
        # per-test string mocks readable while making every sample explicitly
        # successful unless a test overrides the sampler to exercise failure.
        self.adapter_status_patcher = patch.object(
            controller,
            "_gp_adapter_status",
            return_value="Disabled",
        )
        self.adapter_status_patcher.start()
        self.adapter_sample_patcher = patch.object(
            controller,
            "_gp_adapter_status_sample",
            side_effect=lambda **_kwargs: (True, controller._gp_adapter_status()),
        )
        self.adapter_sample_patcher.start()
        self.native_main_visible_patcher = patch.object(
            controller,
            "_gp_any_native_main_window_visible",
            return_value=False,
        )
        self.native_main_visible_patcher.start()
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
        self.native_main_visible_patcher.stop()
        self.adapter_sample_patcher.stop()
        self.adapter_status_patcher.stop()
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
            patch.object(
                controller,
                "_gp_is_exact_pangpa_window",
                return_value=True,
            ) as exact_identity,
        ):
            result = controller._gp_get_login_window(timeout=0.2)

        self.assertIs(result, wrapped)
        self.assertGreaterEqual(exact_identity.call_count, 2)
        desktop.window.assert_called_once_with(handle=4321)
        desktop.windows.assert_not_called()

    def test_login_window_is_not_wrapped_after_native_identity_changes(self) -> None:
        desktop = Mock()

        with (
            patch("pywinauto.Desktop", return_value=desktop),
            patch.object(controller, "_gp_find_login_hwnd", return_value=4321),
            patch.object(
                controller,
                "_gp_is_exact_pangpa_window",
                return_value=False,
            ),
            patch.object(controller.time, "sleep", return_value=None),
            patch.object(
                controller.time,
                "time",
                side_effect=(0.0, 0.0, 1.0),
            ),
        ):
            result = controller._gp_get_login_window(timeout=0.2)

        self.assertIsNone(result)
        desktop.window.assert_not_called()

    def test_login_window_forwards_stale_hwnds_to_native_finder(self) -> None:
        desktop = Mock()
        with (
            patch("pywinauto.Desktop", return_value=desktop),
            patch.object(
                controller,
                "_gp_find_login_hwnd",
                return_value=None,
            ) as find_login,
            patch.object(controller.time, "sleep", return_value=None),
            patch.object(
                controller.time,
                "time",
                side_effect=(0.0, 0.0, 1.0),
            ),
        ):
            result = controller._gp_get_login_window(
                timeout=0.2,
                ignored_hwnds={4321},
            )

        self.assertIsNone(result)
        find_login.assert_called_with(ignored_hwnds={4321})
        desktop.window.assert_not_called()

    def test_monitor_window_wraps_exact_hwnd_without_global_uia_enumeration(self) -> None:
        wrapped = Mock()
        wrapped.is_visible.return_value = True
        desktop = Mock()
        desktop.window.return_value.wrapper_object.return_value = wrapped
        connect_button = Mock()
        connect_button.window_text.return_value = "Connect"
        status_control = Mock()
        status_control.window_text.return_value = "Not Connected"

        with (
            patch("pywinauto.Desktop", return_value=desktop) as desktop_factory,
            patch.object(controller, "_gp_find_main_hwnds", return_value=[1234]),
            patch.object(
                controller,
                "_gp_find_monitor_control",
                side_effect=lambda _window, auto_id: (
                    connect_button
                    if auto_id == controller.GP_BTN_CONNECT_AUTOID
                    else status_control
                ),
            ),
            patch.object(controller, "_gp_find_monitor_portal_control", return_value=None),
        ):
            result = controller._gp_get_monitor_window(timeout=0.2)

        self.assertIs(result, wrapped)
        desktop_factory.assert_called_once_with(backend="win32")
        desktop.window.assert_called_once_with(handle=1234)
        desktop.windows.assert_not_called()
        wrapped.set_focus.assert_not_called()
        wrapped.click_input.assert_not_called()

    def test_tray_activation_clicks_exact_visible_icon_once(self) -> None:
        gp_icon = _uia_button(
            "GlobalProtect Disconnected",
            automation_id=controller.GP_TRAY_ITEM_AUTOID,
        )
        taskbar = _Window([gp_icon])
        taskbar_spec = Mock()
        taskbar_spec.wrapper_object.return_value = taskbar
        desktop = Mock()
        desktop.window.return_value = taskbar_spec

        with (
            patch("pywinauto.Desktop", return_value=desktop),
            patch.object(
                controller,
                "_gp_find_exact_top_level_hwnd",
                side_effect=lambda window_class: (
                    111 if window_class == controller.GP_TASKBAR_CLASS else None
                ),
            ),
            patch.object(controller, "_gp_get_window", return_value=None),
        ):
            activated = controller._gp_activate_from_system_tray(timeout=0.2)

        self.assertTrue(activated)
        gp_icon.click_input.assert_called_once_with()
        gp_icon.invoke.assert_not_called()
        desktop.window.assert_called_once_with(handle=111)
        desktop.windows.assert_not_called()

    def test_tray_activation_refuses_disabled_exact_icon(self) -> None:
        gp_icon = _uia_button(
            "GlobalProtect Disconnected",
            automation_id=controller.GP_TRAY_ITEM_AUTOID,
        )
        gp_icon.is_enabled.return_value = False
        taskbar = _Window([gp_icon])
        taskbar_spec = Mock()
        taskbar_spec.wrapper_object.return_value = taskbar
        desktop = Mock()
        desktop.window.return_value = taskbar_spec

        with (
            patch("pywinauto.Desktop", return_value=desktop),
            patch.object(
                controller,
                "_gp_find_exact_top_level_hwnd",
                side_effect=lambda window_class: (
                    111 if window_class == controller.GP_TASKBAR_CLASS else None
                ),
            ),
            patch.object(controller, "_gp_get_window", return_value=None),
        ):
            activated = controller._gp_activate_from_system_tray(timeout=0.2)

        self.assertFalse(activated)
        gp_icon.click_input.assert_not_called()
        gp_icon.invoke.assert_not_called()

    def test_tray_activation_does_not_hide_visible_unhydrated_native_popup(self) -> None:
        gp_icon = _uia_button(
            "GlobalProtect Disconnected",
            automation_id=controller.GP_TRAY_ITEM_AUTOID,
        )
        taskbar = _Window([gp_icon])
        taskbar_spec = Mock()
        taskbar_spec.wrapper_object.return_value = taskbar
        desktop = Mock()
        desktop.window.return_value = taskbar_spec

        with (
            patch("pywinauto.Desktop", return_value=desktop),
            patch.object(
                controller,
                "_gp_find_exact_top_level_hwnd",
                side_effect=lambda window_class: (
                    111 if window_class == controller.GP_TASKBAR_CLASS else None
                ),
            ),
            patch.object(controller, "_gp_get_window", return_value=None),
            patch.object(
                controller,
                "_gp_any_native_main_window_visible",
                return_value=True,
            ),
        ):
            activated = controller._gp_activate_from_system_tray(timeout=0.2)

        self.assertTrue(activated)
        gp_icon.click_input.assert_not_called()
        gp_icon.invoke.assert_not_called()

    def test_tray_activation_uses_already_open_overflow_without_toggling(self) -> None:
        toggle = _uia_button(
            "Show Hidden Icons Hide",
            automation_id=controller.GP_SHOW_HIDDEN_AUTOID,
        )
        gp_icon = _uia_button(
            "GlobalProtect Connected",
            automation_id=controller.GP_TRAY_ITEM_AUTOID,
        )
        taskbar = _Window([toggle])
        overflow = _Window([gp_icon])
        specs = {111: Mock(), 222: Mock()}
        specs[111].wrapper_object.return_value = taskbar
        specs[222].wrapper_object.return_value = overflow
        desktop = Mock()
        desktop.window.side_effect = lambda *, handle: specs[handle]

        with (
            patch("pywinauto.Desktop", return_value=desktop),
            patch.object(
                controller,
                "_gp_find_exact_top_level_hwnd",
                side_effect=lambda window_class: (
                    111
                    if window_class == controller.GP_TASKBAR_CLASS
                    else 222
                ),
            ),
            patch.object(controller, "_gp_get_window", return_value=None),
        ):
            activated = controller._gp_activate_from_system_tray(timeout=0.2)

        self.assertTrue(activated)
        toggle.invoke.assert_not_called()
        toggle.click_input.assert_not_called()
        gp_icon.click_input.assert_called_once_with()
        gp_icon.invoke.assert_not_called()
        desktop.windows.assert_not_called()

    def test_tray_activation_opens_overflow_then_clicks_gp_once(self) -> None:
        overflow_is_open = False
        toggle = _uia_button(
            "Show Hidden Icons",
            automation_id=controller.GP_SHOW_HIDDEN_AUTOID,
        )
        def _open_overflow():
            nonlocal overflow_is_open
            overflow_is_open = True

        toggle.click_input.side_effect = _open_overflow
        gp_icon = _uia_button(
            "GlobalProtect Disconnected",
            automation_id=controller.GP_TRAY_ITEM_AUTOID,
        )
        taskbar = _Window([toggle])
        overflow = _Window([gp_icon])
        specs = {111: Mock(), 222: Mock()}
        specs[111].wrapper_object.return_value = taskbar
        specs[222].wrapper_object.return_value = overflow
        desktop = Mock()
        desktop.window.side_effect = lambda *, handle: specs[handle]

        def _find_hwnd(window_class):
            if window_class == controller.GP_TASKBAR_CLASS:
                return 111
            return 222 if overflow_is_open else None

        with (
            patch("pywinauto.Desktop", return_value=desktop),
            patch.object(
                controller,
                "_gp_find_exact_top_level_hwnd",
                side_effect=_find_hwnd,
            ),
            patch.object(controller, "_gp_get_window", return_value=None),
        ):
            activated = controller._gp_activate_from_system_tray(timeout=0.2)

        self.assertTrue(activated)
        toggle.click_input.assert_called_once_with()
        toggle.invoke.assert_not_called()
        gp_icon.click_input.assert_called_once_with()
        gp_icon.invoke.assert_not_called()

    def test_existing_visible_popup_never_toggles_tray_or_launches(self) -> None:
        window = Mock()
        window.is_visible.return_value = True

        with (
            patch.object(controller, "_gp_get_window", return_value=window),
            patch.object(controller, "_gp_activate_from_system_tray") as tray,
            patch.object(controller, "_open_gui") as open_gui,
        ):
            result = controller._ensure_gp_main_window("PanGPA.exe")

        self.assertIs(result, window)
        tray.assert_not_called()
        open_gui.assert_not_called()

    def test_cold_start_launches_once_then_activates_tray_once(self) -> None:
        window = Mock()
        window.is_visible.return_value = True

        with (
            patch.object(controller, "_gp_get_window", side_effect=(None, window)),
            patch.object(controller, "_open_gui", return_value=True) as open_gui,
            patch.object(
                controller,
                "_gp_activate_from_system_tray",
                return_value=True,
            ) as tray,
            patch.object(controller, "_gp_process_running", return_value=False),
            patch.object(
                controller.time,
                "monotonic",
                side_effect=(0.0, 2.0, 2.0, 2.1),
            ),
        ):
            result = controller._ensure_gp_main_window("PanGPA.exe")

        self.assertIs(result, window)
        open_gui.assert_called_once_with("PanGPA.exe")
        tray.assert_called_once_with(timeout=8.0)

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

        with (
            patch.object(
                ctypes,
                "windll",
                SimpleNamespace(user32=user32),
                create=True,
            ),
            patch.object(
                controller,
                "_gp_is_exact_pangpa_window",
                return_value=True,
            ),
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
            patch.object(controller, "_gp_get_monitor_window", return_value=window),
            patch.object(
                controller,
                "_gp_get_monitor_portal_text",
                return_value="bchmfa.bancochile.cl",
            ),
        ):
            self.assertEqual(instance._gp_connected_target(), controller.BANCOCHILE)

        with (
            patch.object(controller, "_gp_get_monitor_window", return_value=window),
            patch.object(controller, "_gp_get_monitor_portal_text", return_value="ext.bice.cl"),
        ):
            self.assertEqual(
                instance._gp_connected_target(),
                controller.GPROT_UNKNOWN,
            )

    def test_unrecognized_connected_portal_is_not_mislabeled_as_bice(self) -> None:
        instance = controller.BancoChileSwitcher({})
        window = object()

        with (
            patch.object(controller, "_gp_get_monitor_window", return_value=window),
            patch.object(
                controller,
                "_gp_get_monitor_portal_text",
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

    def test_custom_flow_clicks_only_the_exact_saved_account(self) -> None:
        target = "user@bch.bancodechile.cl"
        account = _Control(target)
        window = _Window([_Control("Pick an account"), account])
        connection_states = iter((False, True))

        with (
            patch.object(controller, "_gp_get_login_window", return_value=window),
            patch.object(controller, "_gp_prepare_login_window", return_value=True),
            patch.object(
                controller,
                "_gp_try_click_with_ancestors",
                return_value=True,
            ) as click,
            patch.object(controller, "_gp_set_edit_value") as set_value,
            patch.object(controller.time, "sleep", return_value=None),
        ):
            result = controller._gp_handle_saml_signin(
                target,
                password_provider=Mock(return_value="must-not-be-read"),
                connected_probe=lambda: next(connection_states),
                flow_mode="custom",
                flow_steps=["account", "mfa"],
            )

        self.assertEqual(result, "connected")
        click.assert_called_once_with(account, "picker_match")
        set_value.assert_not_called()

    def test_custom_flow_can_leave_account_selection_manual(self) -> None:
        target = "user@bch.bancodechile.cl"
        window = _Window([_Control("Pick an account"), _Control(target)])
        connection_states = iter((False, True))
        progress = Mock()

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
                progress=progress,
                flow_mode="custom",
                flow_steps=["mfa"],
            )

        self.assertEqual(result, "connected")
        click.assert_not_called()
        self.assertIn("manually", progress.call_args.args[0])

    def test_custom_flow_can_leave_password_manual_without_reading_secret(self) -> None:
        password_provider = Mock(return_value="must-not-be-read")
        password_edit = _Control("", "Edit", "Password")
        window = _Window(
            [_Control("Enter password"), password_edit, _Control("Sign in", "Button")]
        )
        connection_states = iter((False, True))

        with (
            patch.object(controller, "_gp_get_login_window", return_value=window),
            patch.object(controller, "_gp_prepare_login_window", return_value=True),
            patch.object(controller, "_gp_set_edit_value") as set_value,
            patch.object(controller, "_gp_submit_login_page") as submit,
            patch.object(controller.time, "sleep", return_value=None),
        ):
            result = controller._gp_handle_saml_signin(
                "user@bch.bancodechile.cl",
                password_provider=password_provider,
                connected_probe=lambda: next(connection_states),
                flow_mode="custom",
                flow_steps=["account", "mfa"],
            )

        self.assertEqual(result, "connected")
        password_provider.assert_not_called()
        set_value.assert_not_called()
        submit.assert_not_called()

    def test_bancochile_never_types_a_configured_email(self) -> None:
        email_edit = _Control("", "Edit", "Email")
        window = _Window(
            [_Control("Email, phone, or Skype"), email_edit, _Control("Next", "Button")]
        )
        connection_states = iter((False, True))

        with (
            patch.object(controller, "_gp_get_login_window", return_value=window),
            patch.object(controller, "_gp_prepare_login_window", return_value=True),
            patch.object(controller, "_gp_set_edit_value") as set_value,
            patch.object(controller, "_gp_submit_login_page") as submit,
            patch.object(controller.time, "sleep", return_value=None),
        ):
            result = controller._gp_handle_saml_signin(
                "user@bch.bancodechile.cl",
                password_provider=Mock(return_value="must-not-be-read"),
                connected_probe=lambda: next(connection_states),
            )

        self.assertEqual(result, "connected")
        set_value.assert_not_called()
        submit.assert_not_called()

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
            patch.object(
                controller,
                "_gp_is_exact_pangpa_window",
                return_value=True,
            ),
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
            patch.object(
                controller,
                "_gp_is_exact_pangpa_window",
                return_value=True,
            ),
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

    def test_saml_refuses_a_spoofed_login_window_before_reading_credentials(self) -> None:
        password_provider = Mock(return_value="must-not-be-read")
        window = _Window([_Control("Enter password")])
        window.handle = 9999

        with (
            patch.object(controller, "_gp_get_login_window", return_value=window),
            patch.object(
                controller,
                "_gp_is_exact_pangpa_window",
                return_value=False,
            ),
        ):
            result = controller._gp_handle_saml_signin(
                "user@bch.bancodechile.cl",
                password_provider=password_provider,
                connected_probe=lambda: False,
            )

        self.assertEqual(result, "unknown_page")
        password_provider.assert_not_called()

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

    def test_connected_ui_without_adapter_up_is_not_connection_success(self) -> None:
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
                status_probe=lambda: "Connected",
            )

        self.assertEqual(result, "timeout")
        password_provider.assert_not_called()

    def test_saml_ignores_login_hwnds_from_before_connect(self) -> None:
        with (
            patch.object(controller, "_gp_get_login_window", return_value=None) as get_login,
            patch.object(controller.time, "sleep", return_value=None),
            patch.object(controller.time, "time", side_effect=(0.0, 0.0, 2.0)),
        ):
            result = controller._gp_handle_saml_signin(
                "user@bch.bancodechile.cl",
                total_timeout=1.0,
                connected_probe=lambda: False,
                ignored_login_hwnds={10},
            )

        self.assertEqual(result, "timeout")
        get_login.assert_called_once_with(timeout=0.5, ignored_hwnds={10})

    def test_connected_ui_has_bounded_wait_for_adapter_up(self) -> None:
        with (
            patch.object(controller, "_gp_get_login_window", return_value=None),
            patch.object(controller.time, "sleep", return_value=None),
            patch.object(controller.time, "time", side_effect=(0.0, 0.0, 0.0)),
            patch.object(
                controller.time,
                "monotonic",
                side_effect=(0.0, 0.0, 2.0),
            ),
        ):
            result = controller._gp_handle_saml_signin(
                "user@bch.bancodechile.cl",
                total_timeout=100.0,
                connected_probe=lambda: False,
                status_probe=lambda: "Connected",
                connected_adapter_grace=1.0,
            )

        self.assertEqual(result, "adapter_not_up")

    def test_connected_ui_adapter_wait_remains_cancelable(self) -> None:
        def cancel_during_wait(_seconds: float) -> None:
            controller._autofill_cancel.set()

        with (
            patch.object(controller, "_gp_get_login_window", return_value=None),
            patch.object(controller.time, "sleep", side_effect=cancel_during_wait),
            patch.object(controller.time, "time", return_value=0.0),
            patch.object(controller.time, "monotonic", return_value=0.0),
        ):
            result = controller._gp_handle_saml_signin(
                "user@bch.bancodechile.cl",
                total_timeout=100.0,
                connected_probe=lambda: False,
                status_probe=lambda: "Connected",
                connected_adapter_grace=10.0,
            )

        self.assertEqual(result, "cancelled")

    def test_connected_ui_grace_does_not_expire_during_fresh_mfa_page(self) -> None:
        window = _Window(
            [
                _Control("Approve sign in request"),
                _Control("Enter the number if prompted"),
            ]
        )

        with (
            patch.object(controller, "_gp_get_login_window", return_value=window),
            patch.object(controller, "_gp_prepare_login_window", return_value=True),
            patch.object(controller.time, "sleep", return_value=None),
            patch.object(controller.time, "time", side_effect=(0.0, 0.0, 2.0)),
            patch.object(controller.time, "monotonic", return_value=0.0),
        ):
            result = controller._gp_handle_saml_signin(
                "user@bch.bancodechile.cl",
                total_timeout=1.0,
                connected_probe=lambda: False,
                status_probe=lambda: "Connected",
                connected_adapter_grace=0.0,
            )

        self.assertEqual(result, "mfa_timeout")

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

    def test_connect_aborts_before_action_when_adapter_probe_fails(self) -> None:
        instance = controller.BancoChileSwitcher({})
        window = Mock()

        with (
            patch.object(controller, "_gp_diagnostics"),
            patch.object(controller, "_gp_get_window", return_value=window),
            patch.object(controller, "_gp_get_status_text", return_value="Not Connected"),
            patch.object(controller, "_gp_get_button_label", return_value="Connect"),
            patch.object(
                controller,
                "_gp_adapter_status_sample",
                return_value=(False, ""),
            ),
            patch.object(controller, "_gp_set_portal") as set_portal,
            patch.object(controller, "_gp_invoke_connect_button") as invoke,
        ):
            ok, message = instance.connect_bancochile()

        self.assertFalse(ok)
        self.assertIn("could not verify", message.casefold())
        set_portal.assert_not_called()
        invoke.assert_not_called()

    def test_saml_connected_result_is_rejected_when_adapter_is_not_up(self) -> None:
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
            patch.object(
                controller,
                "_gp_adapter_status",
                side_effect=("Disabled", "Down"),
            ),
            patch.object(controller, "_gp_set_portal", return_value=True),
            patch.object(controller, "_gp_invoke_connect_button", return_value=True),
            patch.object(
                controller,
                "_gp_handle_saml_signin",
                return_value="connected",
            ),
        ):
            ok, message = instance.connect_bancochile()

        self.assertFalse(ok)
        self.assertIn("adapter is not Up", message)
        self.cleanup_after_confirmed_connection.assert_not_called()

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
                side_effect=("Disabled", "Disabled", "Up"),
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

    def test_connect_retry_uses_uia_popup_from_working_release(
        self,
    ) -> None:
        instance = controller.BancoChileSwitcher(
            {
                "bancochile_username": "user@bch.bancodechile.cl",
                "bancochile_password_enc": "ciphertext",
            }
        )
        first_window = Mock(name="first_window")
        action_window = Mock(name="action_window")
        self.wait_for_connect_transition.side_effect = (
            "not_started",
            "started",
        )

        with (
            patch.object(controller, "_gp_diagnostics"),
            patch.object(
                controller,
                "_gp_get_window",
                side_effect=(first_window, action_window),
            ) as get_window,
            patch.object(
                controller,
                "_gp_get_status_text",
                side_effect=("Not Connected", "Not Connected", "Not Connected"),
            ),
            patch.object(controller, "_gp_get_button_label", return_value="Connect"),
            patch.object(
                controller,
                "_gp_adapter_status",
                side_effect=("Disabled", "Disabled", "Up"),
            ),
            patch.object(controller, "_gp_set_portal", return_value=True),
            patch.object(
                controller,
                "_gp_invoke_connect_button",
                return_value=True,
            ) as invoke,
            patch.object(controller, "_gp_handle_saml_signin", return_value="connected"),
            patch.object(controller, "_find_exe", return_value="PanGPA.exe"),
        ):
            ok, message = instance.connect_bancochile()

        self.assertTrue(ok)
        self.assertIn("connected", message.casefold())
        self.assertEqual(
            get_window.call_args_list,
            [call(timeout=2), call(timeout=1.5)],
        )
        self.assertEqual(
            invoke.call_args_list,
            [
                call(first_window, expected_action="connect"),
                call(
                    action_window,
                    expected_action="connect",
                    prefer_click_input=True,
                ),
            ],
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
                side_effect=("Disabled", "Up", "Up"),
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
            patch.object(
                controller,
                "_gp_adapter_status",
                side_effect=("Disabled", "Up", "Up"),
            ),
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

    def test_late_notification_is_not_mistaken_for_a_fresh_login(self) -> None:
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
            patch.object(
                controller,
                "_gp_adapter_status",
                side_effect=("Disabled", "Up", "Up"),
            ),
            patch.object(
                controller,
                "_gp_list_terminal_windows",
                # A Notification is not sign-in-start evidence.  It remains in
                # subsequent snapshots while the fresh adapter sample provides
                # the independent success signal that prevents another toggle.
                side_effect=(
                    [],
                    [],
                    [(100, notification)],
                    [(100, notification)],
                ),
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
        signin.assert_not_called()

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
                side_effect=("Disabled", "Disabled", "Up"),
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
                side_effect=("Disabled", "Disabled", "Disabled", "Up"),
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
            patch.object(
                controller,
                "_gp_adapter_status",
                side_effect=("Down", "Up"),
            ),
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
            patch.object(
                controller,
                "_gp_adapter_status",
                side_effect=("", "Up"),
            ),
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
        self.assertEqual(instance._next_connect_not_before, 0.0)
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
        action_window = Mock()

        with (
            patch.object(controller, "_gp_diagnostics"),
            patch.object(controller, "_gp_get_window", return_value=window),
            patch.object(
                controller,
                "_ensure_gp_main_window",
                side_effect=(window, action_window),
            ) as ensure_window,
            patch.object(
                controller,
                "_gp_get_status_text",
                side_effect=(
                    "Connected",
                    "Connecting...",
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
                side_effect=("Disabled", "Disabled", "Disabled", "Disabled"),
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
            ensure_window.call_args_list,
            [call("PanGPA.exe"), call("PanGPA.exe", deadline=ANY)],
        )
        invoke.assert_called_once_with(action_window, expected_action="disconnect")

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
            patch.object(controller.time, "monotonic", return_value=100.0),
        ):
            ok, message = instance.disconnect_bancochile()

        self.assertTrue(ok)
        self.assertIn("disconnected", message.casefold())
        self.assertIsNone(instance._last_gp_target)
        self.assertTrue(instance._gp_disconnect_confirmed)
        self.assertEqual(
            instance._next_connect_not_before,
            100.0 + controller.GP_POST_DISCONNECT_COOLDOWN_SECONDS,
        )
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
        retry_window = Mock()

        with (
            patch.object(controller, "_gp_diagnostics"),
            patch.object(controller, "_gp_get_window", return_value=window),
            patch.object(
                controller,
                "_ensure_gp_main_window",
                side_effect=(window, retry_window),
            ) as ensure_window,
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
            ensure_window.call_args_list,
            [call("PanGPA.exe"), call("PanGPA.exe", deadline=ANY)],
        )
        self.assertEqual(
            invoke.call_args_list,
            [
                call(window, expected_action="disconnect"),
                call(
                    retry_window,
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

    def test_disconnect_rehydrate_uses_visible_window_helper_not_direct_launch(
        self,
    ) -> None:
        instance = controller.BancoChileSwitcher({})
        instance._last_gp_target = controller.BANCOCHILE
        initial_window = Mock()
        rehydrated_window = Mock()

        with (
            patch.object(controller, "_gp_diagnostics"),
            patch.object(controller, "_gp_get_window", return_value=None),
            patch.object(
                controller,
                "_ensure_gp_main_window",
                side_effect=(initial_window, rehydrated_window),
            ) as ensure_window,
            patch.object(controller, "_gp_get_status_text", return_value="Connected"),
            patch.object(controller, "_gp_get_button_label", return_value="Disconnect"),
            patch.object(controller, "_gp_adapter_status", return_value="Up"),
            patch.object(
                controller,
                "_gp_invoke_connect_button",
                return_value=True,
            ) as invoke,
            patch.object(controller.time, "sleep", return_value=None),
            patch.object(controller, "_find_exe", return_value="PanGPA.exe"),
            patch.object(controller, "_open_gui") as open_gui,
        ):
            ok, message = instance.disconnect_bancochile()

        self.assertFalse(ok)
        self.assertIn("did not finish disconnecting", message)
        self.assertEqual(
            ensure_window.call_args_list,
            [call("PanGPA.exe"), call("PanGPA.exe", deadline=ANY)],
        )
        open_gui.assert_not_called()
        invoke.assert_called_once_with(initial_window, expected_action="disconnect")

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

    def test_disconnect_verification_obeys_wall_clock_deadline(self) -> None:
        instance = controller.BancoChileSwitcher({})
        instance._last_gp_target = controller.BANCOCHILE
        window = Mock()
        deadline = controller.GP_DISCONNECT_VERIFY_TIMEOUT_SECONDS

        with (
            patch.object(controller, "_gp_diagnostics"),
            patch.object(
                controller,
                "_ensure_gp_main_window",
                return_value=window,
            ),
            patch.object(controller, "_gp_get_window") as get_window,
            patch.object(controller, "_gp_get_status_text", return_value="Connected"),
            patch.object(controller, "_gp_get_button_label", return_value="Disconnect"),
            patch.object(controller, "_gp_adapter_status", return_value="Up"),
            patch.object(
                controller,
                "_gp_invoke_connect_button",
                return_value=True,
            ) as invoke,
            patch.object(controller.time, "sleep") as sleep,
            patch.object(
                controller.time,
                "monotonic",
                side_effect=(100.0, 100.0 + deadline + 0.1, 100.0 + deadline + 0.1),
            ),
        ):
            ok, message = instance.disconnect_bancochile()

        self.assertFalse(ok)
        self.assertIn("did not finish disconnecting", message)
        invoke.assert_called_once_with(window, expected_action="disconnect")
        get_window.assert_not_called()
        sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
