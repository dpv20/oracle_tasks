from __future__ import annotations

import queue
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call, patch


SRC_DIR = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_DIR))

from features.output_file_generation.view import (  # noqa: E402
    OutputFileGenerationView,
    _AUTO_INTERFACE_SEPARATOR,
    _DEFAULT_AUTO_DETECT,
    _DEFAULT_AUTO_DETECT_DATE,
    _DEFAULT_DISCLAIMER_EXPANDED,
    _TARGET_COUNTRIES,
    _filtered_interface_values,
    _interface_options,
)
from features.output_file_generation.models import (  # noqa: E402
    GenerationRequest,
    OracleTarget,
)
from features.output_file_generation.service import (  # noqa: E402
    OutputFileGenerationError,
    OutputFileGenerationService,
)
from i18n import T  # noqa: E402


class _FakeVar:
    def __init__(self, value: object) -> None:
        self.value = value

    def get(self):
        return self.value

    def set(self, value: object) -> None:
        self.value = value


def _target(
    country: str,
    *,
    tns: str | None = None,
    database_key: str | None = None,
    credential_key: str = "SHARED-PROD",
) -> dict[str, object]:
    alias = tns or f"{country.upper()}_PROD"
    return {
        "country": country,
        "env": "prod",
        "id": alias,
        "label": "Shared PROD",
        "database_key": database_key or f"{country.upper()}-PROD-DB",
        "credential_key": credential_key,
        "credential_label": "batch_user",
        "credential_count": 1,
    }


class OutputFileGenerationViewTests(unittest.TestCase):
    def _bare_view(self) -> OutputFileGenerationView:
        view = object.__new__(OutputFileGenerationView)
        view._db_lookup = {}
        view.db_var = _FakeVar("—")
        view.db_menu = Mock()
        view._interface_lookup = {
            "CHISALOU (CHISALCA)": "CHISALCA",
            "OFDOBIEL": "IFDOBIEL",
            "OFICOWCG": "IFICOWCG",
        }
        view._interface_values = (
            "CHISALOU (CHISALCA)",
            "OFDOBIEL",
            "OFICOWCG",
        )
        view._last_manual_interface = "CHISALOU (CHISALCA)"
        view.interface_var = _FakeVar("CHISALOU (CHISALCA)")
        view.interface_menu = Mock()
        view.auto_detect_var = _FakeVar(False)
        view.auto_detect_checkbox = Mock()
        view._selected_input_date = None
        view.input_date_display_var = _FakeVar("Choose a date")
        view.auto_detect_date_var = _FakeVar(False)
        view.auto_detect_date_checkbox = Mock()
        view.input_date_frame = Mock()
        view.input_date_entry = Mock()
        view.input_date_calendar_button = Mock()
        view.disclaimer_expanded_var = _FakeVar(
            _DEFAULT_DISCLAIMER_EXPANDED
        )
        view.disclaimer_button = Mock()
        view.disclaimer_body = Mock()
        view.status_label = Mock()
        view.status_label.cget.return_value = "ready"
        return view

    def test_interface_options_start_with_requested_priority_and_cover_all_specs(self) -> None:
        specs = OutputFileGenerationService.supported_interfaces()
        options = _interface_options(specs)
        labels = [label for label, _input_code in options]
        input_codes = [input_code for _label, input_code in options]

        self.assertEqual(
            labels[:3],
            ["CHISALOU (CHISALCA)", "OFDOBIEL", "OFICOWCG"],
        )
        self.assertEqual(len(labels), len(set(labels)))
        self.assertEqual(set(input_codes), {spec.input_code for spec in specs})
        self.assertEqual(dict(options)["CHISALOU (CHISALCA)"], "CHISALCA")
        self.assertEqual(dict(options)["CHICLOU"], "CHICLUPD")
        self.assertFalse(_DEFAULT_AUTO_DETECT)

    def test_auto_detection_toggle_shows_separator_and_restores_manual_choice(self) -> None:
        view = self._bare_view()
        view.interface_var.set("ofdobiel")
        view.auto_detect_var.set(True)

        view._on_auto_detect_changed()

        self.assertEqual(view.interface_var.get(), _AUTO_INTERFACE_SEPARATOR)
        self.assertEqual(view._last_manual_interface, "OFDOBIEL")
        view.interface_menu.configure.assert_called_with(
            values=[_AUTO_INTERFACE_SEPARATOR],
            state="disabled",
        )

        view.interface_menu.reset_mock()
        view.auto_detect_var.set(False)
        view._on_auto_detect_changed()

        self.assertEqual(view.interface_var.get(), "OFDOBIEL")
        view.interface_menu.configure.assert_called_with(
            values=["CHISALOU (CHISALCA)", "OFDOBIEL", "OFICOWCG"],
            state="normal",
        )

    def test_interface_filter_is_case_insensitive_prefix_search(self) -> None:
        values = tuple(
            label
            for label, _input_code in _interface_options(
                OutputFileGenerationService.supported_interfaces()
            )
        )

        self.assertEqual(
            _filtered_interface_values(values, "c"),
            (
                "CHISALOU (CHISALCA)",
                "CHBOOKOU",
                "CHICLOU",
                "CLADCHGO",
                "CLOIRFAP",
                "CLOSLRES",
                "CMRADCHO",
                "CMRCIFOU",
                "CMRCLOU",
                "CMRLPMTO",
                "CMRRELVO",
            ),
        )
        self.assertEqual(
            _filtered_interface_values(values, "cHi"),
            ("CHISALOU (CHISALCA)", "CHICLOU"),
        )
        self.assertEqual(
            _filtered_interface_values(values, "cHiSaLcA"),
            ("CHISALOU (CHISALCA)",),
        )
        self.assertEqual(
            _filtered_interface_values(values, "cHiSaLoU"),
            ("CHISALOU (CHISALCA)",),
        )
        outputs = _filtered_interface_values(values, "O")
        self.assertEqual(outputs[:2], ("OFDOBIEL", "OFICOWCG"))
        self.assertTrue(all(value.startswith("O") for value in outputs))
        self.assertEqual(_filtered_interface_values(values, "  "), values)
        self.assertEqual(_filtered_interface_values(values, "ZZZ"), ())

    def test_typing_filters_without_posting_a_blocking_dropdown(self) -> None:
        view = self._bare_view()
        view._interface_values = (
            "CHISALOU (CHISALCA)",
            "CHICLOU",
            "OFDOBIEL",
        )
        view._show_interface_suggestions = Mock()
        view._hide_interface_suggestions = Mock()
        native_dropdown = view.interface_menu._dropdown_menu

        with patch(
            "features.output_file_generation.view.messagebox"
        ) as modal_dialogs:
            for typed, keysym, expected in (
                ("c", "c", ["CHISALOU (CHISALCA)", "CHICLOU"]),
                ("ch", "h", ["CHISALOU (CHISALCA)", "CHICLOU"]),
                ("chi", "i", ["CHISALOU (CHISALCA)", "CHICLOU"]),
                ("ch", "BackSpace", ["CHISALOU (CHISALCA)", "CHICLOU"]),
                (
                    "",
                    "BackSpace",
                    ["CHISALOU (CHISALCA)", "CHICLOU", "OFDOBIEL"],
                ),
            ):
                with self.subTest(typed=typed, keysym=keysym):
                    view.interface_var.set(typed)
                    self.assertIsNone(
                        view._on_interface_key_release(
                            SimpleNamespace(keysym=keysym)
                        )
                    )
                    view.interface_menu.configure.assert_called_with(
                        values=expected
                    )
                    view._show_interface_suggestions.assert_called_with(
                        tuple(expected)
                    )
                    view._hide_interface_suggestions.assert_not_called()
                    self.assertEqual(view.interface_var.get(), typed)
                    view._show_interface_suggestions.reset_mock()

        # CTkComboBox uses a native Tk menu on Windows. Posting it from every
        # KeyRelease steals the keyboard event flow and makes typing/backspace
        # feel blocked. Suggestions must instead remain in the inline widget
        # tree, where they can be updated without capturing the keyboard.
        view.interface_menu._open_dropdown_menu.assert_not_called()
        native_dropdown.post.assert_not_called()
        native_dropdown.tk_popup.assert_not_called()
        native_dropdown.grab_set.assert_not_called()
        native_dropdown.wait_window.assert_not_called()
        self.assertEqual(modal_dialogs.method_calls, [])

    def test_interface_suggestions_delegate_to_the_inline_selector(self) -> None:
        view = self._bare_view()
        values = ("CHISALOU (CHISALCA)", "CHICLOU")

        view._show_interface_suggestions(values)
        view.interface_menu.show_suggestions.assert_called_once_with(values)

        view._hide_interface_suggestions()
        view.interface_menu.hide_suggestions.assert_called_once_with()

    def test_interface_filter_hides_inline_suggestions_when_nothing_matches(self) -> None:
        view = self._bare_view()
        view._interface_values = (
            "CHISALOU (CHISALCA)",
            "OFDOBIEL",
            "OFICOWCG",
        )
        view._show_interface_suggestions = Mock()
        view._hide_interface_suggestions = Mock()
        view.interface_var.set("zzz")

        view._on_interface_key_release(SimpleNamespace(keysym="z"))

        view.interface_menu.configure.assert_called_once_with(values=[])
        view._show_interface_suggestions.assert_not_called()
        view._hide_interface_suggestions.assert_called_once_with()
        view.interface_menu._open_dropdown_menu.assert_not_called()

    def test_interface_filter_ignores_typing_while_disabled_or_running(self) -> None:
        for automatic, running in ((True, False), (False, True)):
            with self.subTest(automatic=automatic, running=running):
                view = self._bare_view()
                view.auto_detect_var.set(automatic)
                view._running = running
                view.interface_var.set("chi")

                self.assertIsNone(
                    view._on_interface_key_release(
                        SimpleNamespace(keysym="i")
                    )
                )

                view.interface_menu.configure.assert_not_called()
                view.interface_menu._open_dropdown_menu.assert_not_called()

    def test_exact_typed_interface_is_normalized_and_unique_prefix_completes(self) -> None:
        view = self._bare_view()

        self.assertEqual(view._matching_interface_label(" ofdobiel "), "OFDOBIEL")
        self.assertEqual(
            view._matching_interface_label(" chisalou "),
            "CHISALOU (CHISALCA)",
        )
        self.assertEqual(
            view._matching_interface_label(" cHiSaLcA "),
            "CHISALOU (CHISALCA)",
        )
        self.assertIsNone(view._matching_interface_label("ofd"))

        view.interface_var.set("ofdob")
        self.assertEqual(view._on_interface_return(), "break")
        self.assertEqual(view.interface_var.get(), "OFDOBIEL")
        self.assertEqual(view._last_manual_interface, "OFDOBIEL")

        for typed in ("CHISALOU", "chisalca"):
            with self.subTest(typed=typed):
                view.interface_var.set(typed)
                self.assertEqual(view._on_interface_return(), "break")
                self.assertEqual(
                    view.interface_var.get(),
                    "CHISALOU (CHISALCA)",
                )
                self.assertEqual(
                    view._interface_lookup[view.interface_var.get()],
                    "CHISALCA",
                )

    def test_chisalou_selection_keeps_chiclou_as_a_separate_interface(self) -> None:
        for selected in (
            "CHISALOU (CHISALCA)",
            "CHISALOU",
            "CHISALCA",
        ):
            with self.subTest(selected=selected):
                view = self._bare_view()
                view._interface_lookup["CHICLOU"] = "CHICLUPD"
                view._interface_values = (*view._interface_values, "CHICLOU")
                view._hide_interface_suggestions = Mock()

                view._on_interface_selected(selected)

                self.assertEqual(
                    view.interface_var.get(),
                    "CHISALOU (CHISALCA)",
                )
                self.assertEqual(
                    view._interface_lookup[view.interface_var.get()],
                    "CHISALCA",
                )
                self.assertEqual(view._interface_lookup["CHICLOU"], "CHICLUPD")

    def test_date_auto_detection_defaults_to_unchecked_with_manual_calendar_visible(self) -> None:
        view = self._bare_view()

        self.assertFalse(_DEFAULT_AUTO_DETECT_DATE)
        self.assertFalse(view.auto_detect_date_var.get())
        self.assertIsNone(view._selected_input_date)

        view._sync_input_date_controls()

        view.input_date_frame.grid.assert_called_once_with()
        view.input_date_entry.configure.assert_called_once_with(state="readonly")
        view.input_date_calendar_button.configure.assert_called_once_with(
            state="normal"
        )

    def test_disclaimer_defaults_to_collapsed_and_toggles_without_running_generation(self) -> None:
        view = self._bare_view()
        view.service = Mock()

        self.assertFalse(_DEFAULT_DISCLAIMER_EXPANDED)
        self.assertFalse(view.disclaimer_expanded_var.get())

        with patch(
            "features.output_file_generation.view.t",
            side_effect=lambda key, **_kwargs: key,
        ):
            view._sync_disclaimer_visibility()

            view.disclaimer_body.grid_remove.assert_called_once_with()
            view.disclaimer_button.configure.assert_called_with(
                text="output_files.disclaimer_show"
            )

            view.disclaimer_body.reset_mock()
            view.disclaimer_button.reset_mock()
            view._toggle_disclaimer()

            self.assertTrue(view.disclaimer_expanded_var.get())
            view.disclaimer_body.grid.assert_called_once_with()
            view.disclaimer_body.grid_remove.assert_not_called()
            view.disclaimer_button.configure.assert_called_with(
                text="output_files.disclaimer_hide"
            )

            view.disclaimer_body.reset_mock()
            view.disclaimer_button.reset_mock()
            view._toggle_disclaimer()

        self.assertFalse(view.disclaimer_expanded_var.get())
        view.disclaimer_body.grid_remove.assert_called_once_with()
        view.disclaimer_body.grid.assert_not_called()
        view.disclaimer_button.configure.assert_called_with(
            text="output_files.disclaimer_show"
        )
        self.assertEqual(view.service.mock_calls, [])

    def test_disclaimer_has_complete_english_and_spanish_copy(self) -> None:
        keys = (
            "output_files.disclaimer_show",
            "output_files.disclaimer_hide",
            "output_files.disclaimer_body",
        )
        for language in ("en", "es"):
            with self.subTest(language=language):
                copy = {key: T[language].get(key) for key in keys}
                self.assertTrue(all(copy.values()))
                self.assertNotEqual(
                    copy["output_files.disclaimer_show"],
                    copy["output_files.disclaimer_hide"],
                )
                body = str(copy["output_files.disclaimer_body"])
                self.assertIn("Oracle", body)
                self.assertIn("PROD", body)
                self.assertIn("CHISALOU", body)
                self.assertIn("OFDOBIEL", body)
                self.assertIn("OFICOWCG", body)

        for key in keys:
            self.assertNotEqual(T["en"][key], T["es"][key])

    def test_disclaimer_control_is_focusable_and_keyboard_activatable(self) -> None:
        view = object.__new__(OutputFileGenerationView)
        view.service = Mock()
        view.service.supported_interfaces.return_value = ()

        def widget(*_args, **_kwargs):
            return Mock()

        def variable(*_args, **kwargs):
            return _FakeVar(kwargs.get("value"))

        button_factory = Mock(side_effect=widget)
        with (
            patch(
                "features.output_file_generation.view.CardFrame",
                side_effect=widget,
            ),
            patch(
                "features.output_file_generation.view._SearchableComboBox",
                side_effect=widget,
            ),
            patch(
                "features.output_file_generation.view.ctk.CTkScrollableFrame",
                side_effect=widget,
            ),
            patch(
                "features.output_file_generation.view.ctk.CTkFrame",
                side_effect=widget,
            ),
            patch(
                "features.output_file_generation.view.ctk.CTkLabel",
                side_effect=widget,
            ),
            patch(
                "features.output_file_generation.view.ctk.CTkOptionMenu",
                side_effect=widget,
            ),
            patch(
                "features.output_file_generation.view.ctk.CTkEntry",
                side_effect=widget,
            ),
            patch(
                "features.output_file_generation.view.ctk.CTkButton",
                button_factory,
            ),
            patch(
                "features.output_file_generation.view.ctk.CTkCheckBox",
                side_effect=widget,
            ),
            patch(
                "features.output_file_generation.view.ctk.CTkTextbox",
                side_effect=widget,
            ),
            patch(
                "features.output_file_generation.view.ctk.CTkFont",
                side_effect=widget,
            ),
            patch(
                "features.output_file_generation.view.ctk.StringVar",
                side_effect=variable,
            ),
            patch(
                "features.output_file_generation.view.ctk.BooleanVar",
                side_effect=variable,
            ),
            patch(
                "features.output_file_generation.view.tk.Misc.bind"
            ) as bind,
            patch(
                "features.output_file_generation.view.tk.Frame.configure"
            ) as native_configure,
            patch(
                "features.output_file_generation.view.t",
                side_effect=lambda key, **_kwargs: key,
            ),
        ):
            view._build_body()

        disclaimer_call = next(
            item
            for item in button_factory.call_args_list
            if item.kwargs.get("text") == "output_files.disclaimer_show"
        )
        self.assertNotIn("takefocus", disclaimer_call.kwargs)
        native_configure.assert_called_once_with(
            view.disclaimer_button,
            takefocus=True,
        )
        bind.assert_has_calls(
            [
                call(
                    view.disclaimer_button,
                    "<Return>",
                    view._activate_disclaimer_from_keyboard,
                    add="+",
                ),
                call(
                    view.disclaimer_button,
                    "<space>",
                    view._activate_disclaimer_from_keyboard,
                    add="+",
                ),
            ]
        )

        view._toggle_disclaimer = Mock()
        self.assertEqual(view._activate_disclaimer_from_keyboard(), "break")
        view._toggle_disclaimer.assert_called_once_with()

    def test_unchecked_date_detection_without_a_choice_blocks_before_thread(self) -> None:
        view = self._bare_view()
        view._db_lookup = {"selected": _target("chile")}
        view.db_var.set("selected")
        view._running = False
        view.process_ref_entry = Mock()
        view.process_ref_entry.get.return_value = "2798503"
        view.auto_detect_date_var.set(False)
        view._selected_input_date = None

        with (
            patch(
                "features.output_file_generation.view.t",
                side_effect=lambda key, **_kwargs: key,
            ),
            patch(
                "features.output_file_generation.view.messagebox.showerror"
            ) as showerror,
            patch(
                "features.output_file_generation.view.threading.Thread"
            ) as thread_class,
        ):
            view._start()

        showerror.assert_called_once_with(
            "common.error",
            "output_files.input_date_required",
            parent=view,
        )
        thread_class.assert_not_called()
        self.assertFalse(view._running)

    def test_manual_selected_input_date_is_sent_to_request_as_yyyymmdd(self) -> None:
        view = self._bare_view()
        database = _target("chile", tns="FXBFCL_19C_PROD_OCI")
        view._db_lookup = {"selected": database}
        view.db_var.set("selected")
        view._running = False
        view.process_ref_entry = Mock()
        view.process_ref_entry.get.return_value = "2798503"
        view.auto_detect_date_var.set(False)
        view._set_controls_running = Mock()
        view._set_preview = Mock()
        view.progress = Mock()
        view.after = Mock()
        view._worker = Mock()

        view._set_input_date(date(2026, 8, 11))

        self.assertEqual(view._selected_input_date, date(2026, 8, 11))
        self.assertEqual(view.input_date_display_var.get(), "2026-08-11")
        with patch(
            "features.output_file_generation.view.threading.Thread"
        ) as thread_class:
            view._start()

        request, _cancel_event = thread_class.call_args.kwargs["args"]
        self.assertEqual(request.input_file_date, "20260811")
        self.assertTrue(request.overwrite)
        thread_class.return_value.start.assert_called_once_with()

    def test_checking_date_auto_detection_clears_and_hides_manual_date(self) -> None:
        view = self._bare_view()
        view.auto_detect_date_var.set(True)
        view._selected_input_date = date(2026, 8, 11)
        view.input_date_display_var.set("2026-08-11")

        with patch(
            "features.output_file_generation.view.t",
            side_effect=lambda key, **_kwargs: key,
        ):
            view._on_auto_detect_date_changed()

        self.assertIsNone(view._selected_input_date)
        self.assertEqual(
            view.input_date_display_var.get(),
            "output_files.choose_input_date",
        )
        view.input_date_frame.grid_remove.assert_called_once_with()
        view.input_date_entry.configure.assert_called_once_with(state="disabled")
        view.input_date_calendar_button.configure.assert_called_once_with(
            state="disabled"
        )

    def test_checked_date_auto_detection_sends_none_and_always_overwrites(self) -> None:
        view = self._bare_view()
        # The view intentionally has no overwrite checkbox/variable anymore;
        # generation must still request replacement unconditionally.
        self.assertFalse(hasattr(view, "overwrite_var"))
        self.assertFalse(hasattr(view, "overwrite_checkbox"))
        database = _target("chile", tns="FXBFCL_19C_PROD_OCI")
        view._db_lookup = {"selected": database}
        view.db_var.set("selected")
        view._running = False
        view.process_ref_entry = Mock()
        view.process_ref_entry.get.return_value = "2798503"
        view.auto_detect_date_var.set(True)
        view._selected_input_date = None
        view._set_controls_running = Mock()
        view._set_preview = Mock()
        view.progress = Mock()
        view.after = Mock()
        view._worker = Mock()

        with patch(
            "features.output_file_generation.view.threading.Thread"
        ) as thread_class:
            view._start()

        request, _cancel_event = thread_class.call_args.kwargs["args"]
        self.assertIsNone(request.input_file_date)
        self.assertTrue(request.overwrite)
        thread_class.return_value.start.assert_called_once_with()

    def test_running_state_disables_date_auto_checkbox_and_calendar(self) -> None:
        view = self._bare_view()
        view.process_ref_entry = Mock()
        view.generate_button = Mock()
        view.cancel_button = Mock()
        view.auto_detect_date_var.set(False)

        view._set_controls_running(True)

        view.auto_detect_date_checkbox.configure.assert_called_with(
            state="disabled"
        )
        view.input_date_calendar_button.configure.assert_called_with(
            state="disabled"
        )
        view.input_date_entry.configure.assert_called_with(state="disabled")

        view.auto_detect_date_checkbox.reset_mock()
        view.input_date_calendar_button.reset_mock()
        view.input_date_entry.reset_mock()
        view._set_controls_running(False)

        view.auto_detect_date_checkbox.configure.assert_called_with(state="normal")
        view.input_date_calendar_button.configure.assert_called_with(state="normal")
        view.input_date_entry.configure.assert_called_with(state="readonly")

    def test_refresh_targets_combines_all_supported_countries(self) -> None:
        view = self._bare_view()
        rows = {country: [_target(country)] for country in _TARGET_COUNTRIES}
        view.service = Mock()
        view.service.targets.side_effect = lambda country: rows[country]

        view._refresh_targets()

        self.assertEqual(
            view.service.targets.call_args_list,
            [call(country) for country in _TARGET_COUNTRIES],
        )
        self.assertEqual(len(view._db_lookup), 4)
        self.assertEqual(
            {str(target["country"]) for target in view._db_lookup.values()},
            set(_TARGET_COUNTRIES),
        )
        for country, display_name in (
            ("chile", "Chile"),
            ("peru", "Peru"),
            ("colombia", "Colombia"),
            ("mexico", "Mexico"),
        ):
            self.assertTrue(
                any(
                    display_name in label
                    and str(target["country"]) == country
                    and str(target["id"]) in label
                    for label, target in view._db_lookup.items()
                )
            )

    def test_database_labels_disambiguate_country_and_database(self) -> None:
        chile = _target("chile", tns="SHARED_PROD")
        colombia = _target("colombia", tns="SHARED_PROD")

        chile_label = OutputFileGenerationView._database_label(chile)
        colombia_label = OutputFileGenerationView._database_label(colombia)

        self.assertNotEqual(chile_label, colombia_label)
        self.assertIn("Chile", chile_label)
        self.assertIn("Colombia", colombia_label)
        self.assertIn("SHARED_PROD", chile_label)
        self.assertIn("SHARED_PROD", colombia_label)

    def test_matching_database_label_preserves_exact_country(self) -> None:
        view = self._bare_view()
        chile = _target("chile", tns="SHARED_PROD", database_key="PROD-DB")
        colombia = _target(
            "colombia",
            tns="SHARED_PROD",
            database_key="PROD-DB",
        )
        chile_label = OutputFileGenerationView._database_label(chile)
        colombia_label = OutputFileGenerationView._database_label(colombia)
        view._db_lookup = {
            colombia_label: colombia,
            chile_label: chile,
        }

        self.assertEqual(view._matching_database_label(chile), chile_label)
        self.assertEqual(view._matching_database_label(colombia), colombia_label)

    def test_start_builds_target_with_selected_country(self) -> None:
        view = self._bare_view()
        database = _target(
            "colombia",
            tns="BFCO_POCISANTIAGO",
            database_key="CO-PROD-DB",
            credential_key="CO-SHARED-PROD",
        )
        view._db_lookup = {"selected": database}
        view.db_var.set("selected")
        view._running = False
        view.process_ref_entry = Mock()
        view.process_ref_entry.get.return_value = "5328716"
        view.interface_var.set(" ofdobiel ")
        view.auto_detect_date_var.set(True)
        view._set_controls_running = Mock()
        view._set_preview = Mock()
        view.progress = Mock()
        view.after = Mock()
        view._worker = Mock()

        with patch(
            "features.output_file_generation.view.threading.Thread"
        ) as thread_class:
            view._start()

        request, cancel_event = thread_class.call_args.kwargs["args"]
        self.assertEqual(request.target.country, "colombia")
        self.assertEqual(request.target.database_key, "CO-PROD-DB")
        self.assertEqual(request.target.credential_key, "CO-SHARED-PROD")
        self.assertEqual(request.target.tns, "BFCO_POCISANTIAGO")
        self.assertEqual(request.process_ref_no, "5328716")
        self.assertEqual(request.interface_code, "IFDOBIEL")
        self.assertIsNone(request.input_file_date)
        self.assertTrue(request.overwrite)
        self.assertIs(cancel_event, view._cancel_event)
        thread_class.return_value.start.assert_called_once_with()

    def test_start_sends_canonical_chisalca_for_input_or_output_alias(self) -> None:
        for typed in ("CHISALOU", "chisalca"):
            with self.subTest(typed=typed):
                view = self._bare_view()
                database = _target("chile", tns="FXBFCL_19C_PROD_OCI")
                view._db_lookup = {"selected": database}
                view.db_var.set("selected")
                view._running = False
                view.process_ref_entry = Mock()
                view.process_ref_entry.get.return_value = "5350740"
                view.interface_var.set(typed)
                view.auto_detect_date_var.set(True)
                view._set_controls_running = Mock()
                view._set_preview = Mock()
                view.progress = Mock()
                view.after = Mock()
                view._worker = Mock()

                with patch(
                    "features.output_file_generation.view.threading.Thread"
                ) as thread_class:
                    view._start()

                request, _cancel_event = thread_class.call_args.kwargs["args"]
                self.assertEqual(request.process_ref_no, "5350740")
                self.assertEqual(request.interface_code, "CHISALCA")
                self.assertEqual(
                    view.interface_var.get(),
                    "CHISALOU (CHISALCA)",
                )
                thread_class.return_value.start.assert_called_once_with()

    def test_start_uses_none_when_automatic_interface_detection_is_enabled(self) -> None:
        view = self._bare_view()
        database = _target("chile", tns="FXBFCL_19C_PROD_OCI")
        view._db_lookup = {"selected": database}
        view.db_var.set("selected")
        view._running = False
        view.process_ref_entry = Mock()
        view.process_ref_entry.get.return_value = "2798503"
        view.auto_detect_var.set(True)
        view.interface_var.set(_AUTO_INTERFACE_SEPARATOR)
        view.auto_detect_date_var.set(True)
        view._set_controls_running = Mock()
        view._set_preview = Mock()
        view.progress = Mock()
        view.after = Mock()
        view._worker = Mock()

        with patch(
            "features.output_file_generation.view.threading.Thread"
        ) as thread_class:
            view._start()

        request, _cancel_event = thread_class.call_args.kwargs["args"]
        self.assertIsNone(request.interface_code)
        self.assertIsNone(request.input_file_date)
        self.assertTrue(request.overwrite)
        thread_class.return_value.start.assert_called_once_with()

    def test_controls_keep_interface_disabled_after_automatic_run(self) -> None:
        view = self._bare_view()
        view.process_ref_entry = Mock()
        view.generate_button = Mock()
        view.cancel_button = Mock()
        view.auto_detect_var.set(True)

        view._set_controls_running(False)

        view.interface_menu.configure.assert_called_with(state="disabled")
        view.auto_detect_var.set(False)
        view.interface_menu.reset_mock()

        view._set_controls_running(False)

        view.interface_menu.configure.assert_called_with(state="normal")

    def test_open_folder_uses_selected_country(self) -> None:
        view = self._bare_view()
        view._db_lookup = {"selected": _target("colombia")}
        view.db_var.set("selected")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with (
                patch(
                    "features.output_file_generation.view.OUTPUT_FILES_OUT_DIR",
                    root,
                ),
                patch(
                    "features.output_file_generation.view.os.startfile",
                    create=True,
                ) as startfile,
            ):
                view._open_folder()

            expected = root / "Colombia"
            self.assertTrue(expected.is_dir())
            startfile.assert_called_once_with(str(expected))

    def test_open_folder_keeps_the_last_successful_result_country(self) -> None:
        view = self._bare_view()
        view._db_lookup = {"selected": _target("chile")}
        view.db_var.set("selected")

        with tempfile.TemporaryDirectory() as temp_dir:
            expected = Path(temp_dir) / "Colombia"
            view._last_output_folder = expected
            with patch(
                "features.output_file_generation.view.os.startfile",
                create=True,
            ) as startfile:
                view._open_folder()

            self.assertTrue(expected.is_dir())
            startfile.assert_called_once_with(str(expected))

    def test_expected_error_is_logged_with_safe_request_metadata(self) -> None:
        view = self._bare_view()
        view._events = queue.Queue()
        view.service = Mock()
        view.service.generate.side_effect = OutputFileGenerationError(
            "batch_user[proxy]/very-secret@BFCO_POCISANTIAGO "
            "ORA-01017: invalid login\nretry the request"
        )
        request = GenerationRequest(
            target=OracleTarget(
                country="colombia",
                database_key="CO-PROD-DB",
                credential_key="CO-SHARED-PROD",
                tns="BFCO_POCISANTIAGO",
                label="Colombia PROD Santiago",
            ),
            process_ref_no="5328716",
        )

        with self.assertLogs(
            "features.output_file_generation.view",
            level="WARNING",
        ) as captured:
            view._worker(request, Mock())

        log_text = "\n".join(captured.output)
        self.assertIn("country=colombia", log_text)
        self.assertIn("tns=BFCO_POCISANTIAGO", log_text)
        self.assertIn("process_ref=5328716", log_text)
        self.assertIn("[connection redacted]", log_text)
        self.assertIn("ORA-01017: invalid login retry the request", log_text)
        self.assertNotIn("very-secret", log_text)
        self.assertNotIn("CO-SHARED-PROD", log_text)
        self.assertEqual(
            view._events.get_nowait(),
            (
                "error",
                "batch_user[proxy]/very-secret@BFCO_POCISANTIAGO "
                "ORA-01017: invalid login\nretry the request",
            ),
        )


if __name__ == "__main__":
    unittest.main()
