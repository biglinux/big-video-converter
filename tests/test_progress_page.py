"""Focused GTK tests for queue/progress semantics and source lifetimes."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "big-video-converter/usr/share/big-video-converter"))

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Pango
from queue_manager import QueueManagerMixin
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

    def settle(self, outcomes):
        paths = [f"/tmp/item-{index}.mp4" for index in range(len(outcomes))]
        self.page.initialize_queue(paths)
        for path, outcome in zip(paths, outcomes):
            self.page.finish_pending(
                path,
                success=outcome == "completed",
                cancelled=outcome == "cancelled",
            )
        self.assertTrue(self.page.show_completion_summary())
        return paths

    def test_terminal_metric_counts_failed_and_cancelled_as_finished(self):
        paths = ["/tmp/failed.mp4", "/tmp/skipped.mp4"]
        self.page.initialize_queue(paths)

        self.page.finish_pending(paths[0], success=False)
        self.assertEqual(self.page.completed_metric.get_text(), "1 video finished")
        self.assertEqual(self.page.remaining_metric.get_text(), "1 video remaining")
        self.assertEqual(self.page.queue_items[paths[0]].status, "failed")

        self.page.finish_pending(paths[1], cancelled=True)
        self.assertEqual(self.page.completed_metric.get_text(), "2 videos finished")
        self.assertEqual(self.page.remaining_metric.get_text(), "0 videos remaining")

    def test_pending_text_inherits_readable_foreground(self):
        manager = Adw.StyleManager.get_default()
        original = manager.get_color_scheme()
        manager.set_color_scheme(Adw.ColorScheme.FORCE_LIGHT)
        self.page.initialize_queue(["/tmp/pending.mp4"])
        window = Adw.Window()
        window.set_content(self.page.get_page())
        try:
            window.present()
            context = GLib.MainContext.default()
            for _ in range(100):
                context.iteration(False)
            labels = (
                self.page.overall_detail_label,
                self.page.queue_items["/tmp/pending.mp4"].status_label,
            )
            for label in labels:
                color = label.get_color()
                self.assertLess(max(color.red, color.green, color.blue), 0.8)
                self.assertGreater(color.alpha, 0.5)
        finally:
            window.destroy()
            manager.set_color_scheme(original)

    def test_counter_drift_is_repaired_from_row_state(self):
        self.page.initialize_queue(["/tmp/a.mp4", "/tmp/b.mp4"])
        self.page.completed_count = 99
        self.page._update_overall_progress()
        self.assertEqual(self.page.completed_count, 0)
        self.assertEqual(self.page.overall_fraction_label.get_text(), "0%")

    def test_duplicate_paths_create_one_row_and_one_total(self):
        path = "/tmp/duplicate.mp4"
        self.page.initialize_queue([path, path])
        self.assertEqual(len(self.page.queue_items), 1)
        self.assertEqual(self.page.total_queue_items, 1)
        self.assertEqual(self.page.queue_count_chip.get_text(), "1 video")

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
        self.settle(["completed"])
        self.assertEqual(
            self.page.completion_banner.get_title(),
            "1 video converted successfully",
        )
        self.assertEqual(self.page.title_label.get_text(), "Conversion complete")
        self.assertEqual(
            self.page.completion_notification(),
            ("Conversion complete", "1 video converted successfully"),
        )
        self.assertIsNone(self.page._completion_source_id)

    def test_all_failed_is_not_reported_as_complete(self):
        self.settle(["failed", "failed"])
        self.assertEqual(self.page.title_label.get_text(), "Conversions failed")
        self.assertEqual(
            self.page.completion_banner.get_title(), "2 conversions failed"
        )
        self.assertTrue(self.page.completion_banner.has_css_class("error"))

    def test_all_cancelled_is_distinct_from_failure(self):
        self.settle(["cancelled", "cancelled"])
        self.assertEqual(self.page.title_label.get_text(), "Conversions cancelled")
        self.assertEqual(
            self.page.completion_banner.get_title(), "2 conversions cancelled"
        )
        self.assertFalse(self.page.completion_banner.has_css_class("error"))

    def test_mixed_result_omits_zero_categories_and_reports_issues(self):
        self.settle(["completed", "failed", "cancelled"])
        title = self.page.completion_banner.get_title()
        self.assertEqual(self.page.title_label.get_text(), "Queue finished with issues")
        self.assertIn("1 video converted", title)
        self.assertIn("1 conversion failed", title)
        self.assertIn("1 conversion cancelled", title)
        self.assertNotIn("0 ", title)

    def test_empty_or_unsettled_queue_cannot_show_a_completion_banner(self):
        self.assertFalse(self.page.show_completion_summary())
        self.assertFalse(self.page.completion_banner.get_revealed())
        self.page.initialize_queue(["/tmp/pending.mp4"])
        self.assertFalse(self.page.show_completion_summary())
        self.assertIsNone(self.page.completion_notification())

    def test_reset_stops_row_pulse_source(self):
        path = "/tmp/pulse.mp4"
        self.page.initialize_queue([path])
        row = self.page.queue_items[path]
        row.start_conversion(None, "demo")
        row.start_pulse()
        self.assertIsNotNone(row._pulse_source_id)

        self.page.reset()
        self.assertIsNone(row._pulse_source_id)

    def test_pending_and_active_actions_have_visible_precise_labels(self):
        path = "/tmp/long descriptive video name.mp4"
        self.page.initialize_queue([path])
        row = self.page.queue_items[path]

        self.assertEqual(row.details_content.get_label(), "Details")
        self.assertEqual(row.cancel_content.get_label(), "Skip")
        self.assertEqual(row.cancel_button.get_tooltip_text(), "Skip this video")

        row.start_conversion(None, "demo")
        self.assertEqual(row.cancel_content.get_label(), "Cancel")
        self.assertEqual(
            row.cancel_button.get_tooltip_text(),
            "Cancel this conversion",
        )

    def test_long_filename_can_use_two_lines_before_middle_ellipsis(self):
        path = "/tmp/a very long descriptive video filename for comparison.mp4"
        self.page.initialize_queue([path])
        label = self.page.queue_items[path].filename_label
        self.assertTrue(label.get_wrap())
        self.assertEqual(label.get_lines(), 2)
        self.assertEqual(label.get_ellipsize(), Pango.EllipsizeMode.MIDDLE)

    def test_pending_cancel_is_counted_once_by_progress_page(self):
        path = "/tmp/pending.mp4"
        self.app.conversion_queue.append(path)
        self.page.initialize_queue([path])
        row = self.page.queue_items[path]
        row.cancel()
        row.cancel()
        self.assertEqual(row.status, "cancelled")
        self.assertEqual(self.page.completed_count, 1)
        self.assertEqual(self.page.completed_metric.get_text(), "1 video finished")
        self.assertNotIn(path, self.app.conversion_queue)

    def test_active_cancellation_waits_for_supervisor_before_summary(self):
        active = "/tmp/active.mp4"
        pending = "/tmp/pending.mp4"
        self.page.initialize_queue([active, pending])
        active_row = self.page.add_conversion("active", active, None)
        pending_row = self.page.queue_items[pending]
        pending_row.cancel()
        active_row.cancel()
        self.assertIsNone(self.page._completion_source_id)

        self.assertTrue(
            self.page.mark_conversion_complete(active_row.conversion_id, False)
        )
        self.assertIsNotNone(self.page._completion_source_id)

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

    def test_row_progress_coalesces_and_updates_the_overall_fraction(self):
        path = "/tmp/aggregate.mp4"
        self.page.initialize_queue([path])
        row = self.page.queue_items[path]
        row.start_conversion(None, "aggregate")

        row.update_progress(0.20)
        source_id = self.page._aggregate_source_id
        self.assertIsNotNone(source_id)
        row.update_progress(0.40)
        self.assertEqual(self.page._aggregate_source_id, source_id)

        context = GLib.MainContext.default()
        while self.page._aggregate_source_id is not None:
            self.assertTrue(context.iteration(False))
        self.assertEqual(self.page.overall_fraction_label.get_text(), "40%")
        self.assertAlmostEqual(self.page.overall_progress_bar.get_fraction(), 0.40)

    def test_reset_cancels_pending_aggregate_refresh(self):
        path = "/tmp/aggregate-reset.mp4"
        self.page.initialize_queue([path])
        row = self.page.queue_items[path]
        row.start_conversion(None, "aggregate-reset")
        row.update_progress(0.25)
        self.assertIsNotNone(self.page._aggregate_source_id)
        self.page.reset()
        self.assertIsNone(self.page._aggregate_source_id)

    def test_details_action_announces_show_and_hide_without_hiding_name(self):
        path = "/tmp/details.mp4"
        self.page.initialize_queue([path])
        row = self.page.queue_items[path]
        row.start_conversion(None, "demo")

        self.assertEqual(
            row.details_button.get_tooltip_text(), "Show technical details"
        )
        row.details_button.set_active(True)
        self.assertEqual(
            row.details_button.get_tooltip_text(), "Hide technical details"
        )
        self.assertEqual(row.details_content.get_label(), "Details")
        row.details_button.set_active(False)
        self.assertEqual(
            row.details_button.get_tooltip_text(), "Show technical details"
        )


class FakeProgressSummary:
    def __init__(self, shown=True):
        self.shown = shown
        self.show_calls = 0

    def show_completion_summary(self):
        self.show_calls += 1
        return self.shown

    def completion_notification(self):
        return ("Queue finished with issues", "1 conversion failed")


class QueueCompletionHarness(QueueManagerMixin):
    def __init__(self, shown=True):
        self.progress_page = FakeProgressSummary(shown)
        self.is_minimized = True
        self.notifications = []

    def send_system_notification(self, title, body):
        self.notifications.append((title, body))


class QueueCompletionTests(unittest.TestCase):
    def test_completion_is_presented_and_notified_once_per_generation(self):
        harness = QueueCompletionHarness()
        self.assertTrue(harness._present_queue_completion())
        self.assertFalse(harness._present_queue_completion())
        self.assertEqual(harness.progress_page.show_calls, 1)
        self.assertEqual(
            harness.notifications,
            [("Queue finished with issues", "1 conversion failed")],
        )

    def test_unsettled_progress_does_not_consume_completion_generation(self):
        harness = QueueCompletionHarness(shown=False)
        self.assertFalse(harness._present_queue_completion())
        self.assertFalse(getattr(harness, "_queue_completion_presented", False))
        self.assertEqual(harness.notifications, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
