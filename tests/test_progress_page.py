"""Focused GTK tests for queue/progress state semantics and source lifetimes."""

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "big-video-converter/usr/share/big-video-converter"))

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw

from ui.progress_page import ProgressPage


class FakeApp:
    def __init__(self):
        self.conversion_queue = []
        self.active_conversions = []
        self.is_cancellation_requested = False
        self.progress_page_shown = False
        self.returned_to_queue = False

    def _window_buttons_on_left(self):
        return False

    def show_progress_page(self):
        self.progress_page_shown = True

    def return_to_main_view(self):
        self.returned_to_queue = True


class ProgressPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Adw.init()

    def setUp(self):
        self.app = FakeApp()
        self.page = ProgressPage(self.app)

    def tearDown(self):
        self.page.reset()

    def test_terminal_metric_counts_failed_and_cancelled_as_finished(self):
        paths = ["/tmp/failed.mp4", "/tmp/skipped.mp4"]
        self.page.initialize_queue(paths)

        self.page.finish_pending(paths[0], success=False)
        self.assertEqual(self.page.completed_metric.get_text(), "1 finished")
        self.assertEqual(self.page.remaining_metric.get_text(), "1 remaining")
        self.assertEqual(self.page.queue_items[paths[0]].status, "failed")

        self.page.finish_pending(paths[1], cancelled=True)
        self.assertEqual(self.page.completed_metric.get_text(), "2 finished")
        self.assertEqual(self.page.remaining_metric.get_text(), "0 remaining")

    def test_completion_summary_is_scheduled_only_once_and_reset_cancels_it(self):
        path = "/tmp/only.mp4"
        self.page.initialize_queue([path])
        self.page.finish_pending(path, success=True)
        source_id = self.page._completion_source_id
        self.assertIsNotNone(source_id)

        self.page._check_all_complete()
        self.assertEqual(self.page._completion_source_id, source_id)

        self.page.reset()
        self.assertIsNone(self.page._completion_source_id)
        self.assertFalse(self.page.completion_banner.get_revealed())

    def test_single_success_has_grammatical_summary_and_stable_title(self):
        path = "/tmp/only.mp4"
        self.page.initialize_queue([path])
        self.page.finish_pending(path, success=True)
        self.page.show_completion_summary()

        self.assertEqual(
            self.page.completion_banner.get_title(),
            "1 video converted successfully",
        )
        self.assertEqual(self.page.title_label.get_text(), "Conversion complete")
        self.assertIsNone(self.page._completion_source_id)

    def test_reset_stops_row_pulse_source(self):
        path = "/tmp/pulse.mp4"
        self.page.initialize_queue([path])
        row = self.page.queue_items[path]
        row.start_conversion(None, "demo")
        row.start_pulse()
        self.assertIsNotNone(row._pulse_source_id)

        self.page.reset()
        self.assertIsNone(row._pulse_source_id)

    def test_pending_and_active_actions_match_their_real_effect(self):
        path = "/tmp/long descriptive video name.mp4"
        self.page.initialize_queue([path])
        row = self.page.queue_items[path]

        self.assertTrue(row.status_label.has_css_class("dim-label"))
        self.assertEqual(row.cancel_button.get_tooltip_text(), "Skip this video")

        row.start_conversion(None, "demo")
        self.assertFalse(row.status_label.has_css_class("dim-label"))
        self.assertEqual(
            row.cancel_button.get_tooltip_text(),
            "Cancel this conversion",
        )

    def test_progress_fraction_is_clamped_before_storage_and_display(self):
        path = "/tmp/clamped.mp4"
        self.page.initialize_queue([path])
        row = self.page.queue_items[path]
        row.start_conversion(None, "demo")

        row.update_progress(-0.25)
        self.assertEqual(row.current_progress, 0.0)
        self.assertEqual(row.progress_bar.get_fraction(), 0.0)

        row.update_progress(1.25)
        self.assertEqual(row.current_progress, 1.0)
        self.assertEqual(row.progress_bar.get_fraction(), 1.0)
        self.assertIn("100%", row.status_label.get_text())

    def test_details_action_announces_show_and_hide(self):
        path = "/tmp/details.mp4"
        self.page.initialize_queue([path])
        row = self.page.queue_items[path]
        row.start_conversion(None, "demo")

        self.assertEqual(
            row.details_button.get_tooltip_text(),
            "Show technical details",
        )
        row.details_button.set_active(True)
        self.assertEqual(
            row.details_button.get_tooltip_text(),
            "Hide technical details",
        )
        row.details_button.set_active(False)
        self.assertEqual(
            row.details_button.get_tooltip_text(),
            "Show technical details",
        )

    def test_semantic_status_text_uses_readable_theme_colors(self):
        css = (
            ROOT
            / "big-video-converter/usr/share/big-video-converter/ui/premium.css"
        ).read_text(encoding="utf-8")
        expected = {
            "active": "accent_color",
            "success": "success_color",
            "error": "error_color",
            "warning": "warning_color",
        }
        for status, color in expected.items():
            with self.subTest(status=status):
                rule = (
                    f".bvc-progress-card .status-{status} {{\n"
                    f"  color: @{color};\n"
                    "}"
                )
                self.assertIn(rule, css)

    def test_progress_states_are_part_of_visual_evidence_matrix(self):
        demo = (ROOT / "tools/ui_demo.py").read_text(encoding="utf-8")
        workflow = (
            ROOT / ".github/workflows/exact-ui-review-r2.yml"
        ).read_text(encoding="utf-8")
        for state in ("progress", "complete"):
            with self.subTest(state=state):
                self.assertIn(f'"{state}"', demo)
                self.assertIn(state, workflow)
        self.assertIn("expected=28", workflow)


if __name__ == "__main__":
    unittest.main(verbosity=2)
