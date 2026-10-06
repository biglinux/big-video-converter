"""Focused GTK tests for queue/progress semantics and source lifetimes."""

import gc
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "big-video-converter/usr/share/big-video-converter"))

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk, Pango
from queue_manager import QueueManagerMixin
from ui.progress_page import ProgressPage, TimeLeft, format_clock, format_duration, format_time_left


class FakeApp:
    def __init__(self):
        self.conversion_queue = []
        self.active_conversions = []
        self.is_cancellation_requested = False
        self.progress_page_shown = False
        self.returned_to_queue = False
        self.completed_conversions = []

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
        for path, outcome in zip(paths, outcomes, strict=True):
            self.page.finish_pending(
                path,
                success=outcome == "completed",
                cancelled=outcome == "cancelled",
            )
        self.assertTrue(self.page.show_completion_summary())
        return paths

    def test_skip_cancels_a_job_reserved_before_its_row_started(self):
        import threading

        path = "/tmp/reserved.mp4"
        event = threading.Event()
        self.app.active_conversions = [{"file_path": path, "cancel_event": event}]
        self.page.initialize_queue([path])
        self.page.queue_items[path].cancel()
        self.assertTrue(event.is_set())
        self.assertEqual(self.page.queue_items[path].status, "cancelled")

    def test_failure_reason_replaces_the_generic_status(self):
        path = "/tmp/vanished.mp4"
        self.page.initialize_queue([path])
        self.page.finish_pending(path, success=False, reason="File not found")
        row = self.page.queue_items[path]
        self.assertEqual((row.status, row.status_label.get_text()), ("failed", "File not found"))

    def test_strip_counts_failed_and_cancelled_as_finished(self):
        paths = ["/tmp/failed.mp4", "/tmp/skipped.mp4", "/tmp/waiting.mp4"]
        self.page.initialize_queue(paths)

        self.page.finish_pending(paths[0], success=False)
        self.assertEqual(self.page.strip_label.get_text(), "1 of 3 finished · 33%")
        self.page.finish_pending(paths[1], cancelled=True)
        self.assertEqual(self.page.strip_label.get_text(), "2 of 3 finished · 66%")

    def test_pending_text_inherits_readable_foreground(self):
        manager = Adw.StyleManager.get_default()
        original = manager.get_color_scheme()
        manager.set_color_scheme(Adw.ColorScheme.FORCE_LIGHT)
        self.page.initialize_queue(["/tmp/pending.mp4", "/tmp/other.mp4"])
        window = Adw.Window()
        window.set_content(self.page.get_page())
        try:
            window.present()
            pump()
            labels = (
                self.page.strip_label,
                self.page.queue_items["/tmp/pending.mp4"].status_label,
            )
            for label in labels:
                color = label.get_color()
                self.assertLess(max(color.red, color.green, color.blue), 0.8)
                self.assertGreater(color.alpha, 0.5)
        finally:
            window.destroy()
            manager.set_color_scheme(original)

    def test_duplicate_paths_create_one_row_and_one_total(self):
        path = "/tmp/duplicate.mp4"
        self.page.initialize_queue([path, path])
        self.assertEqual(len(self.page.queue_items), 1)
        self.assertFalse(self.page.summary_strip.get_visible())

    def test_single_job_shows_exactly_one_progress_bar(self):
        path = "/tmp/single.mp4"
        self.page.initialize_queue([path])
        row = self.page.add_conversion("single", path, None)
        row.update_progress(0.4)
        bars = visible_bars(self.page.get_page())
        self.assertEqual(bars, [row.progress_bar])
        self.assertTrue(row.has_css_class("bvc-progress-hero"))
        self.assertFalse(self.page.summary_strip.get_visible())
        self.assertFalse(self.page.cancel_all_button.get_visible())
        self.assertEqual(row.percent_label.get_text(), "40%")

    def test_summary_strip_appears_only_with_two_or_more_jobs(self):
        paths = ["/tmp/one.mp4", "/tmp/two.mp4"]
        self.page.initialize_queue(paths)
        row = self.page.add_conversion("one", paths[0], None)
        self.assertTrue(self.page.summary_strip.get_visible())
        self.assertEqual(self.page.strip_label.get_text(), "0 of 2 finished · 0%")
        self.assertFalse(row.has_css_class("bvc-progress-hero"))
        self.assertEqual(
            visible_bars(self.page.get_page()),
            [self.page.overall_progress_bar, row.progress_bar],
        )
        waiting = self.page.queue_items[paths[1]]
        self.assertTrue(waiting.has_css_class("bvc-state-pending"))
        self.assertEqual(waiting.cancel_label.get_label(), "Skip")
        self.assertFalse(waiting.cancel_button.has_css_class("bvc-cancel-job"))
        self.assertFalse(waiting.cancel_button.has_css_class("bvc-danger"))
        self.assertTrue(row.cancel_button.has_css_class("bvc-cancel-job"))

    def test_time_left_is_gated_smoothed_and_formatted(self):
        estimate = TimeLeft()
        self.assertIsNone(estimate.update(0.0, now=0))
        self.assertIsNone(estimate.update(0.01, now=1))  # origin
        self.assertIsNone(estimate.update(0.015, now=2))  # too early, below 2 %
        self.assertIsNone(estimate.update(0.019, now=5))  # still below 2 %
        first = estimate.update(0.05, now=5)
        self.assertAlmostEqual(first, 4 * 0.95 / 0.04)
        # A sudden faster rate moves the estimate only part of the way.
        jumped = estimate.update(0.5, now=6)
        raw = 5 * 0.5 / 0.49
        self.assertTrue(raw < jumped < first)
        self.assertIsNone(estimate.update(0.2, now=7))  # a new pass restarts it

        self.assertEqual(format_time_left(12), "≈ 12 s left")
        self.assertEqual(format_time_left(11.2), "≈ 12 s left")
        self.assertEqual(format_time_left(185), "≈ 3 min left")
        self.assertEqual(format_time_left(3900), "≈ 1 h 5 min left")
        self.assertEqual(format_clock(8), "0:08")
        self.assertEqual(format_clock(3725), "1:02:05")
        self.assertEqual(format_duration(42), "42s")
        self.assertEqual(format_duration(750), "12m 30s")
        self.assertEqual(format_duration(27725), "7h 42m")

    def test_finished_row_shows_how_long_it_took(self):
        path = "/tmp/elapsed.mp4"
        self.page.initialize_queue([path])
        row = self.page.add_conversion("elapsed", path, None)
        row.started_at = time.monotonic() - 750
        row.mark_success()
        self.assertEqual(row.status_label.get_text(), "Completed in 12m 30s")

    def test_hero_shows_time_left_and_metrics_on_the_bar(self):
        path = "/tmp/timed.mp4"
        self.page.initialize_queue([path])
        row = self.page.add_conversion("timed", path, None)
        row.video_codec = "av1"
        row.update_status("Software encoding | 68 fps")
        self.assertEqual(row.status_label.get_text(), "AV1 · Software encoding")
        row._time_left.origin = (time.monotonic() - 10, 0.01)
        row.started_at = time.monotonic() - 12
        row.update_progress(0.5)
        self.assertTrue(row.time_left_label.get_text().startswith("≈ "))
        self.assertTrue(row.time_left_label.get_visible())
        self.assertEqual(row.metrics_label.get_text(), "68 fps · 0:12")
        self.assertNotRegex(row.metrics_label.get_text(), r"\d\d:\d\d:\d\d")

    def test_only_the_running_video_estimates_its_time_left(self):
        paths = ["/tmp/running.mp4", "/tmp/waiting.mp4"]
        self.page.initialize_queue(paths)
        row = self.page.add_conversion("running", paths[0], None)
        row._time_left.origin = (time.monotonic() - 10, 0.01)
        row.update_progress(0.5)
        self.page._update_overall_progress()
        self.assertTrue(row.time_left_label.get_text().startswith("≈ "))
        self.assertEqual(self.page.strip_label.get_text(), "0 of 2 finished · 25%")
        self.assertEqual(self.page.queue_items[paths[1]].time_left_label.get_text(), "")

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
        self.assertFalse(self.page.result_box.get_visible())

    def test_single_success_has_grammatical_summary(self):
        self.settle(["completed"])
        self.assertEqual(self.page.result_heading.get_text(), "Conversion complete")
        self.assertTrue(self.page.result_badge.has_css_class("success"))
        self.assertFalse(self.page.title_label.get_visible())
        self.assertEqual(self.page.primary_button.get_label(), "Convert more videos")
        self.assertEqual(
            self.page.completion_notification(),
            ("Conversion complete", "1 video converted successfully"),
        )
        self.assertIsNone(self.page._completion_source_id)

    def test_completed_summary_reports_published_name_time_and_sizes(self):
        directory = Path(tempfile.mkdtemp(dir=os.environ.get("TMPDIR")))
        self.addCleanup(shutil.rmtree, directory)
        source = directory / "holiday.mp4"
        published = directory / "holiday_1.mp4"
        source.write_bytes(b"x" * 10000)
        published.write_bytes(b"x" * 7000)
        self.page.initialize_queue([str(source)])
        row = self.page.add_conversion("holiday", str(source), None)
        row.job_id = "job-1"
        pump_until(lambda: row.source_size == 10000)

        def supervisor_finish():
            # utils.conversion: settle the row, then record the result.
            row.mark_success()
            self.app.completed_conversions.append({
                "input_file": str(source), "output_file": str(published),
                "success": True, "cancelled": False, "job_id": "job-1",
            })
        supervisor_finish()
        self.assertTrue(self.page.show_completion_summary())
        pump_until(lambda: row.output_size == 7000)

        self.assertEqual(row.filename_label.get_text(), "holiday_1.mp4")
        self.assertEqual(row.size_label.get_text(), GLib.format_size(7000))
        self.assertRegex(self.page.result_stats.get_text(), r"^1 video converted · \d+s$")
        self.assertEqual(
            self.page.result_sizes.get_text(),
            f"{GLib.format_size(10000)} → {GLib.format_size(7000)} (−30%)",
        )
        self.assertTrue(self.page.open_button.get_visible())
        self.assertEqual(self.page.folder_button.get_child().get_label(), "Show in folder")
        self.assertTrue(row.open_button.get_visible())
        self.assertEqual(
            row.open_button.get_tooltip_text(), "Open video"
        )

    def test_primary_action_returns_to_the_queue_and_takes_focus(self):
        window = Adw.Window()
        window.set_content(self.page.get_page())
        try:
            window.present()
            pump()
            self.settle(["completed", "completed"])
            pump()
            self.assertIs(window.get_focus(), self.page.primary_button)
            self.assertTrue(self.page.back_button.get_visible())
            self.page.primary_button.emit("clicked")
            self.assertTrue(self.app.returned_to_queue)
        finally:
            window.destroy()

    def test_all_failed_is_not_reported_as_complete(self):
        self.settle(["failed", "failed"])
        self.assertEqual(self.page.result_heading.get_text(), "Conversions failed")
        self.assertEqual(self.page.result_stats.get_text(), "2 conversions failed")
        self.assertFalse(self.page.result_sizes.get_visible())
        self.assertTrue(self.page.result_badge.has_css_class("failed"))
        self.assertEqual(self.page.primary_button.get_label(), "Back to queue")

    def test_failure_summary_uses_friendly_reasons_only(self):
        raw = (
            "Conversion failed with code 2\n"
            "ERROR: Input file is corrupted or not a valid video file: /home/u/broken.mp4\n"
            "The file container could not be read."
        )
        paths = [f"/tmp/broken-{index}.mp4" for index in range(5)] + ["/tmp/fine.mp4"]
        self.page.initialize_queue(paths)
        for path in paths[:5]:
            row = self.page.add_conversion("x", path, None)
            row.mark_failure("The file container could not be read.", raw)
        self.page.finish_pending(paths[5], success=True)
        self.assertTrue(self.page.show_completion_summary())

        reasons = self.page.result_reason.get_text().split("\n")
        self.assertEqual(reasons[0], "“broken-0.mp4”: The file container could not be read.")
        self.assertEqual(len(reasons), 4)
        self.assertEqual(reasons[-1], "+2 more")
        for text in (self.page.result_reason.get_text(), row.status_label.get_text()):
            self.assertNotIn("ERROR", text)
            self.assertNotIn("/home/u", text)
            self.assertNotIn("code 2", text)
        self.assertEqual(row.status_label.get_tooltip_text(), raw)
        self.assertFalse(row.status_label.has_css_class("bvc-status-error"))
        self.assertEqual(self.page.primary_button.get_label(), "Back to queue")

    def test_mixed_summary_has_one_result_line(self):
        self.settle(["completed", "completed", "failed"])
        self.assertEqual(
            self.page.result_stats.get_text(), "2 videos converted, 1 conversion failed"
        )
        visible = [
            label.get_text()
            for label in (self.page.result_stats, self.page.result_sizes)
            if label.get_visible()
        ]
        self.assertEqual(len(visible), 1)

    def test_all_cancelled_is_distinct_from_failure(self):
        self.settle(["cancelled", "cancelled"])
        self.assertEqual(self.page.result_heading.get_text(), "Conversions cancelled")
        self.assertEqual(self.page.result_stats.get_text(), "2 conversions cancelled")
        self.assertFalse(self.page.result_badge.has_css_class("failed"))

    def test_mixed_result_omits_zero_categories_and_reports_issues(self):
        self.settle(["completed", "failed", "cancelled"])
        detail = self.page.result_stats.get_text()
        self.assertEqual(self.page.result_heading.get_text(), "Queue finished with issues")
        self.assertIn("1 video converted", detail)
        self.assertIn("1 conversion failed", detail)
        self.assertIn("1 conversion cancelled", detail)
        self.assertNotIn("0 ", detail)
        self.assertTrue(self.page.result_badge.has_css_class("mixed"))

    def test_empty_or_unsettled_queue_cannot_show_a_summary(self):
        self.assertFalse(self.page.show_completion_summary())
        self.assertFalse(self.page.result_box.get_visible())
        self.page.initialize_queue(["/tmp/pending.mp4"])
        self.assertFalse(self.page.show_completion_summary())
        self.assertIsNone(self.page.completion_notification())

    def test_pending_and_active_actions_have_visible_precise_labels(self):
        path = "/tmp/long descriptive video name.mp4"
        self.page.initialize_queue([path])
        row = self.page.queue_items[path]

        self.assertEqual(row.cancel_label.get_label(), "Skip")
        self.assertEqual(row.cancel_button.get_tooltip_text(), "Skip this video")
        self.assertFalse(row.details_button.get_visible())

        row.start_conversion(None, "demo")
        self.assertEqual(row.cancel_label.get_label(), "Cancel")
        self.assertEqual(
            row.cancel_button.get_tooltip_text(),
            "Cancel this conversion",
        )
        self.assertEqual(row.details_label.get_label(), "FFmpeg output")
        self.assertIs(row.actions.get_parent(), row.footer)

    def test_bar_exposes_percent_and_time_left_to_assistive_technology(self):
        path = "/tmp/announced.mp4"
        self.page.initialize_queue([path])
        row = self.page.add_conversion("announced", path, None)
        announced = []
        original = row.progress_bar.update_property

        def record(properties, values):
            announced.append(dict(zip(properties, values, strict=True)))
            original(properties, values)

        row.progress_bar.update_property = record
        row._time_left.origin = (time.monotonic() - 10, 0.01)
        row.update_progress(0.5)
        text = announced[-1][Gtk.AccessibleProperty.VALUE_TEXT]
        self.assertTrue(text.startswith("50%, ≈ "), text)

    def test_long_filename_ellipsizes_in_the_middle(self):
        path = "/tmp/a very long descriptive video filename for comparison.mp4"
        self.page.initialize_queue([path])
        label = self.page.queue_items[path].filename_label
        self.assertEqual(label.get_ellipsize(), Pango.EllipsizeMode.MIDDLE)
        self.assertFalse(label.get_wrap())
        self.assertEqual(label.get_tooltip_text(), path)

    def test_pending_cancel_is_counted_once_by_progress_page(self):
        path = "/tmp/pending.mp4"
        self.app.conversion_queue.append(path)
        self.page.initialize_queue([path])
        row = self.page.queue_items[path]
        row.cancel()
        row.cancel()
        self.assertEqual(row.status, "cancelled")
        self.assertEqual(self.page._result_counts()["cancelled"], 1)
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
        self.assertEqual(row.percent_label.get_text(), "100%")

    def test_row_progress_coalesces_and_updates_the_overall_fraction(self):
        paths = ["/tmp/aggregate.mp4", "/tmp/next.mp4"]
        self.page.initialize_queue(paths)
        row = self.page.queue_items[paths[0]]
        row.start_conversion(None, "aggregate")

        row.update_progress(0.20)
        source_id = self.page._aggregate_source_id
        self.assertIsNotNone(source_id)
        row.update_progress(0.40)
        self.assertEqual(self.page._aggregate_source_id, source_id)

        context = GLib.MainContext.default()
        while self.page._aggregate_source_id is not None:
            self.assertTrue(context.iteration(False))
        self.assertEqual(self.page.strip_label.get_text(), "0 of 2 finished · 20%")
        self.assertAlmostEqual(self.page.overall_progress_bar.get_fraction(), 0.20)

    def test_reset_cancels_pending_aggregate_refresh(self):
        path = "/tmp/aggregate-reset.mp4"
        self.page.initialize_queue([path])
        row = self.page.queue_items[path]
        row.start_conversion(None, "aggregate-reset")
        row.update_progress(0.25)
        row.mark_success()
        self.assertIsNotNone(self.page._aggregate_source_id)
        self.assertIsNotNone(self.page._output_source_id)
        self.page.reset()
        self.assertIsNone(self.page._aggregate_source_id)
        self.assertIsNone(self.page._output_source_id)
        self.assertIsNone(self.page._completion_source_id)

    def test_details_toggle_exposes_expanded_state_and_keeps_its_name(self):
        path = "/tmp/details.mp4"
        self.page.initialize_queue([path])
        row = self.page.queue_items[path]
        row.start_conversion(None, "demo")

        self.assertTrue(row.details_button.get_visible())
        self.assertEqual(
            row.details_button.get_tooltip_text(), "Show technical details"
        )
        row.details_button.set_active(True)
        self.assertTrue(row.details_revealer.get_reveal_child())
        self.assertEqual(
            row.details_button.get_tooltip_text(), "Hide technical details"
        )
        self.assertEqual(row.details_label.get_label(), "FFmpeg output")
        row.details_button.set_active(False)
        self.assertFalse(row.details_revealer.get_reveal_child())

    def test_reset_finalizes_rows_and_ignores_late_thumbnails(self):
        manager = FakeThumbnails()
        self.app.conversion_page = SimpleNamespace(thumbnail_manager=manager)
        paths = ["/tmp/thumb-a.mp4", "/tmp/thumb-b.mp4"]
        self.page.initialize_queue(paths)
        row = self.page.add_conversion("a", paths[0], None)
        row.details_button.set_active(True)
        finalized = []
        for item in self.page.queue_items.values():
            item.weak_ref(lambda: finalized.append(True))
        self.assertEqual(len(manager.requests), 2)
        del row, item

        self.page.reset()
        self.assertTrue(all(request.cancelled for request in manager.requests))
        for request in manager.requests:
            request.callback("/nonexistent.png")  # a worker finishing late
        pump()
        gc.collect()
        pump()
        self.assertEqual(len(finalized), 2)


class FakeRequest:
    def __init__(self, callback):
        self.callback = callback
        self.cancelled = False

    def cancel(self):
        self.cancelled = True


class FakeThumbnails:
    def __init__(self):
        self.requests = []

    def request(self, path, callback):
        self.requests.append(FakeRequest(callback))
        return self.requests[-1]


def pump(seconds=0.2):
    context = GLib.MainContext.default()
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        context.iteration(False)
        time.sleep(0.005)


def pump_until(predicate, timeout=5):
    context = GLib.MainContext.default()
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("condition not reached")
        context.iteration(False)
        time.sleep(0.005)


def visible_bars(root):
    """Progress bars a person can see: the bar and all its ancestors visible."""
    found = []

    def walk(widget):
        if not widget.get_visible():
            return
        if isinstance(widget, Gtk.ProgressBar):
            found.append(widget)
        child = widget.get_first_child()
        while child is not None:
            walk(child)
            child = child.get_next_sibling()

    walk(root)
    return found


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
