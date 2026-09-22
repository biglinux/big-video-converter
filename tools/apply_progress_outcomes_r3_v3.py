#!/usr/bin/env python3
"""Repair the staged generator, apply it, and add aggregate progress updates."""

from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
ORIGINAL = ROOT / "tools/apply_progress_outcomes_r3.py"
PROGRESS = ROOT / "big-video-converter/usr/share/big-video-converter/ui/progress_page.py"
TESTS = ROOT / "tests/test_progress_page.py"


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected one exact match, found {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def replace_regex_once(path: Path, pattern: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    result, count = re.subn(pattern, new, text, count=1, flags=re.DOTALL)
    if count != 1:
        raise RuntimeError(f"{path}: expected one regex match, found {count}")
    path.write_text(result, encoding="utf-8")


# The first generator was intentionally exact, but a nested Python string made
# newline escaping dependent on the transport. Replace that block with source
# assembled from explicit lines, so indentation and escapes cannot drift.
text = ORIGINAL.read_text(encoding="utf-8")
pattern = (
    r'            rows\[0\]\.add_output_text\(\n'
    r'.*?'
    r'            \)\n'
    r'            rows\[1\]\.start_conversion'
)
replacement = "\n".join(
    [
        "            rows[0].add_output_text(",
        "                os.linesep.join(",
        "                    (",
        '                        "Preparing the output safely",',
        '                        "Hardware encoder validated",',
        '                        "Encoding frame 3680 of 5412",',
        "                    )",
        "                )",
        "            )",
        "            rows[1].start_conversion",
    ]
)
text, count = re.subn(pattern, replacement, text, count=1, flags=re.DOTALL)
if count != 1:
    raise RuntimeError(f"generator: expected one ui_demo block, found {count}")
ORIGINAL.write_text(text, encoding="utf-8")

subprocess.run([sys.executable, str(ORIGINAL)], cwd=ROOT, check=True)

replace_once(
    PROGRESS,
    "        self._completion_source_id = None\n",
    "        self._completion_source_id = None\n"
    "        self._aggregate_source_id = None\n",
)

replace_once(
    PROGRESS,
    '''    def _cancel_completion_summary(self):
''',
    '''    def _cancel_overall_progress_update(self) -> None:
        if self._aggregate_source_id is not None:
            GLib.source_remove(self._aggregate_source_id)
            self._aggregate_source_id = None

    def request_overall_progress_update(self) -> None:
        """Coalesce frequent per-frame updates into one main-loop refresh."""
        if self._aggregate_source_id is None:
            self._aggregate_source_id = GLib.idle_add(
                self._on_overall_progress_update
            )

    def _on_overall_progress_update(self):
        self._aggregate_source_id = None
        self._update_overall_progress()
        return GLib.SOURCE_REMOVE

    def _cancel_completion_summary(self):
''',
)

replace_regex_once(
    PROGRESS,
    r'''    def _result_counts\(self\) -> dict\[str, int\]:\n.*?\n    def _terminal_count''',
    '''    def _result_counts(self) -> dict[str, int]:
        counts = {"completed": 0, "failed": 0, "cancelled": 0}
        for row in self.queue_items.values():
            if row.status in counts:
                counts[row.status] += 1
        return counts

    def _terminal_count''',
)

replace_regex_once(
    PROGRESS,
    r'''    def _update_overall_progress\(self\):\n.*?\n    def add_conversion''',
    '''    def _update_overall_progress(self):
        """Update aggregate state in one pass over the queue."""
        self._cancel_overall_progress_update()
        total = len(self.queue_items)
        self.total_queue_items = total
        counts = {"completed": 0, "failed": 0, "cancelled": 0}
        active_fraction = 0.0
        for row in self.queue_items.values():
            if row.status in counts:
                counts[row.status] += 1
            elif row.status == "active":
                active_fraction += row.current_progress

        finished = sum(counts.values())
        self.completed_count = finished
        fraction = (
            max(0.0, min(1.0, (finished + active_fraction) / total))
            if total
            else 0.0
        )
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
            _kind, heading, detail = self._result_messages(counts)
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

    def add_conversion''',
)

replace_once(
    PROGRESS,
    '''        else:
            self.status_label.set_text(
                f"{_('Converting')}... {int(safe_fraction * 100)}%"
            )

    def update_status(self, status) -> None:
''',
    '''        else:
            self.status_label.set_text(
                f"{_('Converting')}... {int(safe_fraction * 100)}%"
            )
        if self.progress_page:
            self.progress_page.request_overall_progress_update()

    def update_status(self, status) -> None:
''',
)

replace_once(
    PROGRESS,
    '''    def reset(self):
        """Reset progress page for new batch"""
        self._cancel_completion_summary()
''',
    '''    def reset(self):
        """Reset progress page for new batch"""
        self._cancel_completion_summary()
        self._cancel_overall_progress_update()
''',
)

replace_once(
    TESTS,
    "from gi.repository import Adw, Pango\n",
    "from gi.repository import Adw, GLib, Pango\n",
)

replace_once(
    TESTS,
    '''    def test_details_action_announces_show_and_hide_without_hiding_name(self):
''',
    '''    def test_row_progress_coalesces_and_updates_the_overall_fraction(self):
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
''',
)

print("Applied robust progress outcome patch with coalesced aggregate updates")
