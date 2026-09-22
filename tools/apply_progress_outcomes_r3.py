#!/usr/bin/env python3
"""Apply the reviewed queue/progress semantics patch exactly once."""

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def replace_once(relative: str, old: str, new: str) -> None:
    path = ROOT / relative
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{relative}: expected one exact match, found {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def replace_regex_once(relative: str, pattern: str, new: str) -> None:
    path = ROOT / relative
    text = path.read_text(encoding="utf-8")
    result, count = re.subn(pattern, new, text, count=1, flags=re.DOTALL)
    if count != 1:
        raise RuntimeError(f"{relative}: expected one regex match, found {count}")
    path.write_text(result, encoding="utf-8")


PROGRESS = "big-video-converter/usr/share/big-video-converter/ui/progress_page.py"
QUEUE = "big-video-converter/usr/share/big-video-converter/queue_manager.py"

replace_once(
    PROGRESS,
    "_ = gettext.gettext\n\n\nclass ProgressPage:\n",
    "_ = gettext.gettext\n"
    "ngettext = gettext.ngettext\n\n"
    "TERMINAL_STATES = frozenset({\"completed\", \"failed\", \"cancelled\"})\n\n\n"
    "class ProgressPage:\n",
)

replace_once(
    PROGRESS,
    '''    def initialize_queue(self, queue_items) -> None:
        """Initialize the queue display with all items as pending"""
        self.reset()
        self.total_queue_items = len(queue_items)

        for file_path in queue_items:
            if file_path not in self.queue_items:
                row = QueueItemRow(self.app, file_path, self)
                self.queue_items[file_path] = row
                self.queue_listbox.append(row)

        self._update_overall_progress()
''',
    '''    def initialize_queue(self, queue_items) -> None:
        """Initialize one stable row per queued input."""
        self.reset()

        for file_path in queue_items:
            if file_path not in self.queue_items:
                row = QueueItemRow(self.app, file_path, self)
                self.queue_items[file_path] = row
                self.queue_listbox.append(row)

        self.total_queue_items = len(self.queue_items)
        self._update_overall_progress()
''',
)

new_progress_helpers = '''    @staticmethod
    def _count_text(singular: str, plural: str, count: int) -> str:
        return ngettext(singular, plural, count).format(count=count)

    def _result_counts(self) -> dict[str, int]:
        return {
            "completed": sum(
                row.status == "completed" for row in self.queue_items.values()
            ),
            "failed": sum(
                row.status == "failed" for row in self.queue_items.values()
            ),
            "cancelled": sum(
                row.status == "cancelled" for row in self.queue_items.values()
            ),
        }

    def _terminal_count(self) -> int:
        return sum(self._result_counts().values())

    def _all_rows_terminal(self) -> bool:
        return (
            bool(self.queue_items)
            and not self.active_conversions
            and all(
                row.status in TERMINAL_STATES
                for row in self.queue_items.values()
            )
        )

    def _result_kind(self, counts: dict[str, int] | None = None) -> str:
        counts = counts or self._result_counts()
        successful = counts["completed"]
        failed = counts["failed"]
        cancelled = counts["cancelled"]
        if successful and not failed and not cancelled:
            return "success"
        if failed and not successful and not cancelled:
            return "failed"
        if cancelled and not successful and not failed:
            return "cancelled"
        if successful or failed or cancelled:
            return "mixed"
        return "empty"

    def _result_summary(self, counts: dict[str, int] | None = None) -> str:
        counts = counts or self._result_counts()
        parts = []
        if counts["completed"]:
            parts.append(
                self._count_text(
                    "{count} video converted",
                    "{count} videos converted",
                    counts["completed"],
                )
            )
        if counts["failed"]:
            parts.append(
                self._count_text(
                    "{count} conversion failed",
                    "{count} conversions failed",
                    counts["failed"],
                )
            )
        if counts["cancelled"]:
            parts.append(
                self._count_text(
                    "{count} conversion cancelled",
                    "{count} conversions cancelled",
                    counts["cancelled"],
                )
            )
        return ", ".join(parts)

    def _result_messages(
        self, counts: dict[str, int] | None = None
    ) -> tuple[str, str, str]:
        counts = counts or self._result_counts()
        total = sum(counts.values())
        kind = self._result_kind(counts)
        if kind == "success":
            heading = ngettext("Conversion complete", "Conversions complete", total)
            detail = self._count_text(
                "{count} video converted successfully",
                "{count} videos converted successfully",
                counts["completed"],
            )
        elif kind == "failed":
            heading = ngettext("Conversion failed", "Conversions failed", total)
            detail = self._count_text(
                "{count} conversion failed",
                "{count} conversions failed",
                counts["failed"],
            )
        elif kind == "cancelled":
            heading = ngettext("Conversion cancelled", "Conversions cancelled", total)
            detail = self._count_text(
                "{count} conversion cancelled",
                "{count} conversions cancelled",
                counts["cancelled"],
            )
        elif kind == "mixed":
            heading = _("Queue finished with issues")
            detail = self._result_summary(counts)
        else:
            heading = _("Conversion progress")
            detail = _("No conversion results are available")
        return kind, heading, detail

    def completion_notification(self) -> tuple[str, str] | None:
        """Return an accurate notification only for a settled queue."""
        if not self._all_rows_terminal():
            return None
        _kind, heading, detail = self._result_messages()
        return heading, detail

    def _update_overall_progress(self):
        """Update title, metrics and aggregate progress from row state."""
        total = len(self.queue_items)
        self.total_queue_items = total
        finished = self._terminal_count()
        self.completed_count = finished
        if total:
            active_fraction = sum(
                row.current_progress
                for row in self.queue_items.values()
                if row.status == "active"
            )
            fraction = max(0.0, min(1.0, (finished + active_fraction) / total))
        else:
            fraction = 0.0
        percent = int(round(fraction * 100))
        self.overall_progress_bar.set_fraction(fraction)
        self.overall_fraction_label.set_text(f"{percent}%")
        self.completed_metric.set_text(
            self._count_text(
                "{count} video finished", "{count} videos finished", finished
            )
        )
        remaining = max(0, total - finished)
        self.remaining_metric.set_text(
            self._count_text(
                "{count} video remaining", "{count} videos remaining", remaining
            )
        )
        self.queue_count_chip.set_text(
            self._count_text("{count} video", "{count} videos", total)
        )
        self.cancel_all_button.set_visible(bool(total and finished < total))
        if total and finished == total:
            _kind, heading, detail = self._result_messages()
            self.title_label.set_text(heading)
            self.progress_context_label.set_text(detail)
            self.overall_detail_label.set_text(
                _("Review the results below or return to the queue")
            )
        elif total:
            self.title_label.set_text(_("Converting videos"))
            self.progress_context_label.set_text(
                _("{} of {} finished").format(finished, total)
            )
            self.overall_detail_label.set_text(
                _("The active job is shown first; pending videos remain unchanged")
            )
        else:
            self.title_label.set_text(_("Conversion progress"))
            self.progress_context_label.set_text(_("Preparing the queue safely"))
            self.overall_detail_label.set_text(_("Waiting for the first video"))

    def add_conversion'''

replace_regex_once(
    PROGRESS,
    r"    def _update_overall_progress\(self\):\n.*?\n    def add_conversion",
    new_progress_helpers,
)

replace_once(
    PROGRESS,
    '''        self._cancel_completion_summary()
        conversion_id = f"conversion_{self.count}"
''',
    '''        self._cancel_completion_summary()
        self.completion_banner.set_revealed(False)
        self.back_button.set_visible(False)
        conversion_id = f"conversion_{self.count}"
''',
)

replace_regex_once(
    PROGRESS,
    r'''    def finish_pending\(self, file_path, \*, success=False, cancelled=False\):\n.*?\n    def _cancel_completion_summary''',
    '''    def finish_pending(
        self, file_path, *, success: bool = False, cancelled: bool = False
    ) -> bool:
        """Settle a job rejected before an active process was registered."""
        row = self.queue_items.get(file_path)
        if row is None or row.status != "pending":
            return False
        if cancelled:
            row.mark_cancelled()
        else:
            row.mark_complete(success)
        self._update_overall_progress()
        self._check_all_complete()
        return True

    def mark_conversion_complete(
        self, conversion_id: str, success: bool = True, output_file: str = None
    ) -> bool:
        """Settle one registered active conversion exactly once."""
        if conversion_id not in self.active_conversions:
            return False
        conv_data = self.active_conversions.pop(conversion_id)
        row = conv_data.get("row")

        if row and row.status not in TERMINAL_STATES:
            row.mark_complete(success, output_file)

        self._update_overall_progress()
        self._check_all_complete()
        return True

    def _cancel_completion_summary''',
)

replace_regex_once(
    PROGRESS,
    r'''    def _check_all_complete\(self\):\n.*?\n    def remove_conversion''',
    '''    def _check_all_complete(self):
        """Schedule one summary after the UI and supervisor have both settled."""
        if self._all_rows_terminal():
            if self._completion_source_id is None:
                self._completion_source_id = GLib.timeout_add(
                    300, self._on_completion_timeout
                )
        else:
            self._cancel_completion_summary()

    def _on_completion_timeout(self):
        self._completion_source_id = None
        self._show_completion_summary()
        return GLib.SOURCE_REMOVE

    def _show_completion_summary(self) -> bool:
        """Show a precise summary for a fully settled queue."""
        if not self._all_rows_terminal():
            self.completion_banner.set_revealed(False)
            return False

        self._update_overall_progress()
        counts = self._result_counts()
        kind, heading, detail = self._result_messages(counts)
        banner_title = (
            _("Queue finished: {results}").format(results=detail)
            if kind == "mixed"
            else detail
        )
        self.completion_banner.set_title(banner_title)
        self.completion_banner.remove_css_class("success")
        self.completion_banner.remove_css_class("error")
        if kind == "success":
            self.completion_banner.add_css_class("success")
        elif kind == "failed":
            self.completion_banner.add_css_class("error")

        self.title_label.set_text(heading)
        self.progress_context_label.set_text(detail)
        self.completion_banner.set_revealed(True)
        self.cancel_all_button.set_visible(False)
        self.back_button.set_visible(True)

        if self.window_buttons_left:
            self.header_bar.set_decoration_layout("close,minimize,maximize:")
        else:
            self.header_bar.set_decoration_layout(":minimize,maximize,close")
        return True

    def remove_conversion''',
)

replace_once(PROGRESS, "def remove_conversion(self, conversion_id: int)", "def remove_conversion(self, conversion_id: str)")
replace_once(PROGRESS, "def start_conversion(self, process, conversion_id: int)", "def start_conversion(self, process, conversion_id: str)")

replace_once(
    PROGRESS,
    '''        self.filename_label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        self.filename_label.set_xalign(0)
''',
    '''        self.filename_label.set_wrap(True)
        self.filename_label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.filename_label.set_lines(2)
        self.filename_label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        self.filename_label.set_xalign(0)
''',
)

replace_once(
    PROGRESS,
    '''        self.details_button = Gtk.ToggleButton(icon_name="view-more-symbolic")
        self.details_button.add_css_class("bvc-icon-button")
        self.details_button.add_css_class("bvc-quiet")
        self._set_details_action(False)
        self.details_button.connect("toggled", self._on_details_toggled)
        self.details_button.set_sensitive(False)
        actions.append(self.details_button)
        self.cancel_button = Gtk.Button(icon_name="process-stop-symbolic")
        self.cancel_button.add_css_class("bvc-icon-button")
        self.cancel_button.add_css_class("bvc-quiet")
        self.cancel_button.add_css_class("bvc-danger")
''',
    '''        self.details_content = Adw.ButtonContent()
        self.details_content.set_icon_name("view-more-symbolic")
        self.details_content.set_label(_("Details"))
        self.details_button = Gtk.ToggleButton()
        self.details_button.set_child(self.details_content)
        self.details_button.add_css_class("bvc-secondary")
        self.details_button.add_css_class("bvc-quiet")
        self._set_details_action(False)
        self.details_button.connect("toggled", self._on_details_toggled)
        self.details_button.set_sensitive(False)
        actions.append(self.details_button)
        self.cancel_content = Adw.ButtonContent()
        self.cancel_content.set_icon_name("process-stop-symbolic")
        self.cancel_content.set_label(_("Cancel"))
        self.cancel_button = Gtk.Button()
        self.cancel_button.set_child(self.cancel_content)
        self.cancel_button.add_css_class("bvc-secondary")
        self.cancel_button.add_css_class("bvc-quiet")
        self.cancel_button.add_css_class("bvc-danger")
''',
)

replace_regex_once(
    PROGRESS,
    r'''    def _set_cancel_action\(self, pending: bool\) -> None:\n.*?\n    def _set_state''',
    '''    def _set_cancel_action(self, pending: bool) -> None:
        if pending:
            tooltip = _("Skip this video")
            label = _('Skip “{}”').format(self.display_name)
            visible_label = _("Skip")
            icon_name = "media-skip-forward-symbolic"
            self.cancel_button.tooltip_key = "progress_skip_file"
        else:
            tooltip = _("Cancel this conversion")
            label = _('Cancel “{}”').format(self.display_name)
            visible_label = _("Cancel")
            icon_name = "process-stop-symbolic"
            self.cancel_button.tooltip_key = "progress_cancel_file"
        self.cancel_content.set_label(visible_label)
        self.cancel_content.set_icon_name(icon_name)
        self.cancel_button.set_tooltip_text(tooltip)
        self.cancel_button.update_property(
            [Gtk.AccessibleProperty.LABEL], [label]
        )

    def _set_details_action(self, expanded: bool) -> None:
        label = _("Hide technical details") if expanded else _("Show technical details")
        self.details_content.set_label(_("Details"))
        self.details_content.set_icon_name(
            "view-more-horizontal-symbolic" if expanded else "view-more-symbolic"
        )
        self.details_button.set_tooltip_text(label)
        self.details_button.update_property(
            [Gtk.AccessibleProperty.LABEL], [label]
        )

    def _set_state''',
)

replace_once(
    PROGRESS,
    '''        self.details_revealer.set_reveal_child(is_active)
        button.set_icon_name(
            "view-more-horizontal-symbolic" if is_active else "view-more-symbolic"
        )
        self._set_details_action(is_active)
''',
    '''        self.details_revealer.set_reveal_child(is_active)
        self._set_details_action(is_active)
''',
)

replace_regex_once(
    PROGRESS,
    r'''        self.mark_cancelled\(\)\n\n        # Update completed count for skipped pending items\n        if was_pending and self.progress_page:\n            self.progress_page.completed_count \+= 1\n            self.progress_page._update_overall_progress\(\)\n            self.progress_page._check_all_complete\(\)\n''',
    '''        if was_pending and self.progress_page:
            self.progress_page.finish_pending(self.file_path, cancelled=True)
        else:
            self.mark_cancelled()
''',
)

replace_once(PROGRESS, "        self.cancel_all_button.set_visible(True)\n        self.back_button.set_visible(False)\n", "        self.cancel_all_button.set_visible(False)\n        self.back_button.set_visible(False)\n")
replace_once(
    PROGRESS,
    '''    def show_completion_summary(self) -> None:
        """Public method called from main.py"""
        self._cancel_completion_summary()
        self._show_completion_summary()
''',
    '''    def show_completion_summary(self) -> bool:
        """Public method called by the queue supervisor."""
        self._cancel_completion_summary()
        return self._show_completion_summary()
''',
)

replace_once(
    QUEUE,
    '''        # Initialize the progress page with the queue items
        if hasattr(self, "progress_page"):
            self.progress_page.reset()
            self.progress_page.initialize_queue(list(self.conversion_queue))
''',
    '''        # Initialize one progress model for this queue generation.
        self._queue_completion_presented = False
        if hasattr(self, "progress_page"):
            self.progress_page.initialize_queue(list(self.conversion_queue))
''',
)

replace_once(
    QUEUE,
    '''        GLib.timeout_add(300, self.process_next_in_queue)

    def convert_current_file(self) -> None:
''',
    '''        GLib.timeout_add(300, self.process_next_in_queue)

    def _present_queue_completion(self) -> bool:
        """Present and notify one settled queue generation exactly once."""
        if getattr(self, "_queue_completion_presented", False):
            return False

        notification = None
        progress_page = getattr(self, "progress_page", None)
        if progress_page is not None:
            shown = progress_page.show_completion_summary()
            if shown is False:
                return False
            notification_getter = getattr(
                progress_page, "completion_notification", None
            )
            if notification_getter is not None:
                notification = notification_getter()

        self._queue_completion_presented = True
        if getattr(self, "is_minimized", False):
            if notification is None:
                notification = (
                    _("Batch Conversion Complete"),
                    _("All queued files have been processed."),
                )
            self.send_system_notification(*notification)
        return True

    def convert_current_file(self) -> None:
''',
)

replace_once(
    QUEUE,
    '''                # Show completion summary on progress page
                if hasattr(self, "progress_page"):
                    self.progress_page.show_completion_summary()

                if hasattr(self, "is_minimized") and self.is_minimized:
                    self.send_system_notification(
                        _("Batch Conversion Complete"),
                        _("All queued files have been processed."),
                    )
''',
    '''                self._present_queue_completion()
''',
)

replace_once(
    QUEUE,
    '''                    self.currently_converting = False
                    self._was_queue_processing = False
                    self.progress_page.show_completion_summary()
''',
    '''                    self.currently_converting = False
                    self._was_queue_processing = False
                    self._present_queue_completion()
''',
)

replace_once(
    "tools/update-translations.sh",
    "--keyword=_ --no-location --no-wrap \\\n",
    "--keyword=_ --keyword=ngettext:1,2 --no-location --no-wrap \\\n",
)

replace_regex_once(
    "tools/ui_demo.py",
    r'''    def _populate_progress\(self, complete: bool = False\) -> None:\n.*?\n    def _show_audio_state''',
    '''    def _populate_progress(self, outcome: str = "active") -> None:
        paths = [os.fspath(path) for path in self.demo_media]
        self.progress_page.initialize_queue(paths)
        rows = list(self.progress_page.queue_items.values())
        if outcome == "active":
            rows[0].start_conversion(None, "demo-0")
            rows[0].update_progress(0.68, "GPU encoding · H.264 | 124 fps")
            rows[0].add_output_text(
                "Preparing the output safely\\n"
                "Hardware encoder validated\\n"
                "Encoding frame 3680 of 5412"
            )
            rows[1].start_conversion(None, "demo-1")
            rows[1].update_progress(0.26, "Preparing audio")
            rows[2]._set_state("pending")
        else:
            outcomes = {
                "success": ("completed", "completed", "completed"),
                "failed": ("failed", "failed", "failed"),
                "cancelled": ("cancelled", "cancelled", "cancelled"),
                "mixed": ("completed", "failed", "cancelled"),
            }[outcome]
            for index, (row, status) in enumerate(zip(rows, outcomes)):
                row.start_conversion(None, f"demo-{index}")
                if status == "completed":
                    row.update_progress(1.0, "Finished")
                    row.mark_complete(True)
                elif status == "failed":
                    row.update_progress(0.72, "Encoder stopped")
                    row.mark_complete(False)
                else:
                    row.update_progress(0.34, "Stopping safely")
                    row.mark_cancelled()
            self.progress_page._show_completion_summary()
        self.progress_page._update_overall_progress()
        self.show_progress_page()

    def _show_audio_state''',
)

replace_once(
    "tools/ui_demo.py",
    '''        elif screen == "progress":
            self._populate_progress(False)
        elif screen == "complete":
            self._populate_progress(True)
''',
    '''        elif screen == "progress":
            self._populate_progress("active")
        elif screen == "complete":
            self._populate_progress("success")
        elif screen == "complete-failed":
            self._populate_progress("failed")
        elif screen == "complete-cancelled":
            self._populate_progress("cancelled")
        elif screen == "complete-mixed":
            self._populate_progress("mixed")
''',
)

replace_once(
    "tools/ui_demo.py",
    '''            "queue-empty", "queue", "progress", "complete",
''',
    '''            "queue-empty", "queue", "progress", "complete",
            "complete-failed", "complete-cancelled", "complete-mixed",
''',
)

replace_once(
    ".github/workflows/exact-ui-review-r2.yml",
    "states=\"audio-copy audio-convert audio-remove noise-off noise-ai progress complete editor editor-crop editor-segments\"",
    "states=\"audio-copy audio-convert audio-remove noise-off noise-ai progress complete complete-failed complete-cancelled complete-mixed editor editor-crop editor-segments\"",
)
replace_once(
    ".github/workflows/exact-ui-review-r2.yml",
    "          expected=40\n",
    "          expected=52\n",
)

(ROOT / "tests/test_progress_page.py").write_text(
    '''"""Focused GTK tests for queue/progress semantics and source lifetimes."""

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "big-video-converter/usr/share/big-video-converter"))

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Pango

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

    def test_details_action_announces_show_and_hide_without_hiding_name(self):
        path = "/tmp/details.mp4"
        self.page.initialize_queue([path])
        row = self.page.queue_items[path]
        row.start_conversion(None, "demo")

        self.assertEqual(row.details_button.get_tooltip_text(), "Show technical details")
        row.details_button.set_active(True)
        self.assertEqual(row.details_button.get_tooltip_text(), "Hide technical details")
        self.assertEqual(row.details_content.get_label(), "Details")
        row.details_button.set_active(False)
        self.assertEqual(row.details_button.get_tooltip_text(), "Show technical details")

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
                    f".bvc-progress-card .status-{status} {{\\n"
                    f"  color: @{color};\\n"
                    "}"
                )
                self.assertIn(rule, css)

    def test_plural_calls_are_extracted_by_translation_tooling(self):
        script = (ROOT / "tools/update-translations.sh").read_text(encoding="utf-8")
        self.assertIn("--keyword=ngettext:1,2", script)

    def test_progress_outcomes_are_part_of_visual_evidence_matrix(self):
        demo = (ROOT / "tools/ui_demo.py").read_text(encoding="utf-8")
        workflow = (
            ROOT / ".github/workflows/exact-ui-review-r2.yml"
        ).read_text(encoding="utf-8")
        for state in (
            "progress",
            "complete",
            "complete-failed",
            "complete-cancelled",
            "complete-mixed",
        ):
            with self.subTest(state=state):
                self.assertIn(f'"{state}"', demo)
                self.assertIn(state, workflow)
        self.assertIn("expected=52", workflow)


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
''',
    encoding="utf-8",
)

print("Applied reviewed progress outcome patch")
