"""Conversion progress and result page.

One job is shown as a single hero card. Several jobs get a thin summary strip
above a list whose active rows carry their own bar. A settled queue shows a
centered result summary above the per-video results.
"""

import ctypes
import ctypes.util
import gettext
import locale
import logging
import math
import os
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango

logger = logging.getLogger(__name__)

_ = gettext.gettext
ngettext = gettext.ngettext

TERMINAL_STATES = frozenset({"completed", "failed", "cancelled"})
# Encoder identifiers of the job environment, as people know them.
_RESULT_ICONS = {
    "success": "object-select-symbolic",
    "failed": "dialog-error-symbolic",
    "cancelled": "action-unavailable-symbolic",
    "mixed": "dialog-warning-symbolic",
}


def format_percent(fraction: float) -> str:
    # Floor: "100%" only once the job has really finished.
    percent = math.floor(max(0.0, min(1.0, fraction)) * 100 + 1e-6)
    return _("{percent}%").format(percent=percent)


def _user_decimal_point() -> str:
    """The decimal separator of the user's numeric locale.

    libmpv needs LC_NUMERIC=C for the whole process (python-mpv sets it on
    import), so the global locale cannot answer; a private locale object
    queried through libc does, without touching the process state.
    """
    name = next(
        (
            os.environ[key]
            for key in ("LC_ALL", "LC_NUMERIC", "LANG")
            if os.environ.get(key)
        ),
        "C",
    )
    try:
        libc = ctypes.CDLL(ctypes.util.find_library("c"))
        libc.newlocale.restype = ctypes.c_void_p
        libc.newlocale.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_void_p)
        libc.nl_langinfo_l.restype = ctypes.c_char_p
        libc.nl_langinfo_l.argtypes = (ctypes.c_int, ctypes.c_void_p)
        libc.freelocale.argtypes = (ctypes.c_void_p,)
        handle = libc.newlocale(1 << locale.LC_NUMERIC, name.encode(), None)
        if not handle:
            return "."
        try:
            # RADIXCHAR is the first item of the LC_NUMERIC category.
            return (
                libc.nl_langinfo_l(locale.LC_NUMERIC << 16, handle) or b"."
            ).decode()
        finally:
            libc.freelocale(handle)
    except (OSError, AttributeError, UnicodeDecodeError):
        return "."


_DECIMAL_POINT = None


def format_size(size: int) -> str:
    """GLib.format_size in the user's numeric locale (e.g. "10,8 MB")."""
    global _DECIMAL_POINT
    if _DECIMAL_POINT is None:
        _DECIMAL_POINT = _user_decimal_point()
    text = GLib.format_size(size)
    return text.replace(".", _DECIMAL_POINT) if _DECIMAL_POINT != "." else text


def format_time_left(seconds: float) -> str:
    seconds = max(1, math.ceil(seconds))
    if seconds < 60:
        return ngettext("≈ {seconds} s left", "≈ {seconds} s left", seconds).format(
            seconds=seconds
        )
    minutes = round(seconds / 60)
    if minutes < 60:
        return ngettext("≈ {minutes} min left", "≈ {minutes} min left", minutes).format(
            minutes=minutes
        )
    hours, minutes = divmod(minutes, 60)
    return ngettext(
        "≈ {hours} h {minutes} min left", "≈ {hours} h {minutes} min left", hours
    ).format(hours=hours, minutes=minutes)


def format_clock(seconds: float) -> str:
    hours, rest = divmod(int(seconds), 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def format_duration(seconds: float) -> str:
    mins, secs = divmod(round(seconds), 60)
    hours, mins = divmod(mins, 60)
    if hours:
        # A night-long queue reads as "7h 42m", not "462m 5s".
        return _("{hours}h {mins}m").format(hours=hours, mins=mins)
    if mins:
        return _("{mins}m {secs}s").format(mins=mins, secs=secs)
    return _("{secs}s").format(secs=secs)


class TimeLeft:
    """Smoothed seconds left, from the rate measured since progress began.

    The origin is the first non-zero fraction, so probing and GPU checks
    before the encode do not slow the estimate down.
    """

    MIN_SECONDS = 3.0
    MIN_FRACTION = 0.02
    SMOOTHING_SECONDS = 3.0

    def __init__(self):
        self.origin = None
        self.value = None
        self.stamp = None
        self.last = 0.0

    def update(self, fraction: float, now: float | None = None) -> float | None:
        now = time.monotonic() if now is None else now
        restarted = fraction < self.last
        self.last = fraction
        if fraction <= 0 or restarted:
            # Not started, or a new pass restarted the bar.
            self.origin = self.value = None
            if fraction <= 0:
                return None
        if self.origin is None:
            self.origin = (now, fraction)
            return None
        start, first = self.origin
        elapsed = now - start
        if (
            elapsed < self.MIN_SECONDS
            or fraction < self.MIN_FRACTION
            or fraction <= first
        ):
            return None
        raw = elapsed * (1.0 - fraction) / (fraction - first)
        if self.value is None:
            self.value = raw
        else:
            predicted = max(0.0, self.value - (now - self.stamp))
            weight = 1.0 - math.exp(-(now - self.stamp) / self.SMOOTHING_SECONDS)
            self.value = predicted + weight * (raw - predicted)
        self.stamp = now
        return self.value


def launch_file(app, widget, path: str, *, containing: bool = False) -> None:
    """Open a file in its default application, or show it in its folder.

    Opening goes straight to the default handler: Gtk.FileLauncher routes
    through the OpenURI portal, which asks for an application first.
    """
    name = os.path.basename(path.rstrip(os.sep)) or path

    def done(source, result):
        try:
            if containing:
                source.open_containing_folder_finish(result)
            else:
                Gio.AppInfo.launch_default_for_uri_finish(result)
        except GLib.Error as error:
            if error.matches(Gtk.dialog_error_quark(), Gtk.DialogError.DISMISSED):
                return
            logger.warning("Could not open %s: %s", path, error.message)
            if hasattr(app, "show_error_dialog"):
                app.show_error_dialog(
                    _("Could not open “{name}”: {error}").format(
                        name=name, error=error.message
                    )
                )

    file = Gio.File.new_for_path(path)
    if containing:
        parent = widget.get_root() if widget is not None else None
        Gtk.FileLauncher.new(file).open_containing_folder(parent, None, done)
    else:
        display = (
            widget.get_display() if widget is not None else Gdk.Display.get_default()
        )
        Gio.AppInfo.launch_default_for_uri_async(
            file.get_uri(), display.get_app_launch_context(), None, done
        )


class ProgressPage:
    """Progress of a queue generation and its result summary."""

    def __init__(self, app):
        self.app = app
        self.toolbar_view = Adw.ToolbarView()
        self.toolbar_view.add_css_class("bvc-shell")

        self.header_bar = Adw.HeaderBar()
        self.window_buttons_left = self.app._window_buttons_on_left()
        self.header_bar.set_decoration_layout(
            "minimize,maximize:" if self.window_buttons_left else ":minimize,maximize"
        )

        self.back_button = Gtk.Button.new_from_icon_name("go-previous-symbolic")
        self.back_button.add_css_class("bvc-icon-button")
        self.back_button.add_css_class("bvc-quiet")
        self.back_button.set_tooltip_text(_("Back to the queue"))
        self.back_button.update_property(
            [Gtk.AccessibleProperty.LABEL], [_("Back to the queue")]
        )
        self.back_button.connect("clicked", self._on_back_clicked)
        self.back_button.set_visible(False)
        self.header_bar.pack_start(self.back_button)

        self.title_label = Gtk.Label(label=_("Conversion progress"))
        self.title_label.add_css_class("heading")
        self.title_label.set_ellipsize(Pango.EllipsizeMode.END)
        # The result heading replaces the progress title once the queue settles.
        self.header_title = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.header_title.append(self.title_label)
        self.header_bar.set_title_widget(self.header_title)

        self.cancel_all_button = Gtk.Button(label=_("Cancel all"))
        self.cancel_all_button.add_css_class("bvc-secondary")
        self.cancel_all_button.add_css_class("bvc-danger")
        self.cancel_all_button.connect("clicked", self._on_cancel_all_clicked)
        self.cancel_all_button.set_visible(False)
        self.header_bar.pack_end(self.cancel_all_button)
        self.toolbar_view.add_top_bar(self.header_bar)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        # One job and the result summary sit in the middle of the window;
        # a running list grows from the top.
        self.clamp = Adw.Clamp(maximum_size=880, tightening_threshold=640)
        self.clamp.set_margin_start(24)
        self.clamp.set_margin_end(24)
        self.clamp.set_margin_top(24)
        self.clamp.set_margin_bottom(32)
        self.content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        self.clamp.set_child(self.content_box)
        scroll.set_child(self.clamp)
        self.toolbar_view.set_content(scroll)

        # Several jobs: one line and one thin bar for the whole queue.
        self.summary_strip = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.summary_strip.add_css_class("bvc-progress-card")
        self.summary_strip.add_css_class("bvc-summary-strip")
        self.strip_label = Gtk.Label(xalign=0, wrap=True)
        self.strip_label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.strip_label.add_css_class("bvc-body-strong")
        self.strip_label.add_css_class("bvc-metric")
        self.summary_strip.append(self.strip_label)
        self.overall_progress_bar = Gtk.ProgressBar()
        self.overall_progress_bar.add_css_class("bvc-progress-bar")
        self.overall_progress_bar.add_css_class("bvc-thin")
        self.overall_progress_bar.update_property(
            [Gtk.AccessibleProperty.LABEL], [_("Overall conversion progress")]
        )
        self.summary_strip.append(self.overall_progress_bar)
        self.summary_strip.set_visible(False)
        self.content_box.append(self.summary_strip)

        # The outcome takes the top of the window: a band on the sidebar plane
        # that the header bar is part of, over the list of files.
        self.result_box = self._build_result_summary()
        self.result_box.set_visible(False)
        result_band = Adw.Clamp(maximum_size=880, tightening_threshold=640)
        result_band.set_margin_start(24)
        result_band.set_margin_end(24)
        result_band.set_child(self.result_box)
        self.toolbar_view.add_top_bar(result_band)

        self.queue_listbox = Gtk.ListBox()
        self.queue_listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        self.queue_listbox.set_show_separators(False)
        self.queue_listbox.add_css_class("bvc-progress-list")
        self.content_box.append(self.queue_listbox)

        self.queue_items = {}
        self.active_conversions = {}
        self.count = 0
        self._summary_shown = False
        self._completion_source_id = None
        self._aggregate_source_id = None
        self._output_source_id = None
        self._setup_css()

    def _build_result_summary(self):
        # The heading is the header bar's title; the figures and actions sit
        # centered under it, and the files follow below.
        title = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        title.set_visible(False)
        self.result_badge = Gtk.Box(valign=Gtk.Align.CENTER)
        self.result_badge.add_css_class("bvc-result-badge")
        # The role is given at construction: set_accessible_role() is
        # GtkWidgetClass's and would hide every label or image made after.
        self.result_icon = Gtk.Image(
            pixel_size=16, accessible_role=Gtk.AccessibleRole.PRESENTATION
        )
        self.result_badge.append(self.result_icon)
        title.append(self.result_badge)
        self.result_heading = Gtk.Label()
        self.result_heading.set_ellipsize(Pango.EllipsizeMode.END)
        self.result_heading.add_css_class("heading")
        title.append(self.result_heading)
        self.result_title = title
        self.header_title.append(title)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.set_halign(Gtk.Align.CENTER)
        box.add_css_class("bvc-result-summary")

        facts = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        facts.set_halign(Gtk.Align.CENTER)
        facts.add_css_class("bvc-result-stats")
        self.result_stats = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER)
        self.result_sizes = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER)
        self.result_sizes_separator = Gtk.Label(
            label="·", accessible_role=Gtk.AccessibleRole.PRESENTATION
        )
        for label in (
            self.result_stats,
            self.result_sizes_separator,
            self.result_sizes,
        ):
            label.add_css_class("bvc-metric")
            facts.append(label)
        box.append(facts)
        self.result_reason = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER)
        self.result_reason.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.result_reason.set_selectable(True)
        self.result_reason.add_css_class("bvc-result-reason")
        box.append(self.result_reason)

        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        buttons.set_halign(Gtk.Align.CENTER)
        buttons.set_margin_top(10)
        self.primary_button = Gtk.Button(label=_("Convert more videos"))
        self.primary_button.add_css_class("suggested-action")
        self.primary_button.connect("clicked", self._on_back_clicked)
        buttons.append(self.primary_button)
        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.open_button = Gtk.Button(
            child=Adw.ButtonContent(
                label=_("Open video"), icon_name="media-playback-start-symbolic"
            )
        )
        self.folder_button = Gtk.Button(
            child=Adw.ButtonContent(
                label=_("Show in folder"), icon_name="folder-open-symbolic"
            )
        )
        for button in (self.open_button, self.folder_button):
            actions.append(button)
        self.open_button.connect("clicked", self._on_open_result)
        self.folder_button.connect("clicked", self._on_open_result_folder)
        self.result_actions = actions
        buttons.append(actions)
        box.append(buttons)
        return box

    def _setup_css(self):
        provider = Gtk.CssProvider()
        provider.load_from_path(os.path.join(os.path.dirname(__file__), "progress.css"))
        Gtk.StyleContext.add_provider_for_display(
            self.toolbar_view.get_display(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

    def get_page(self):
        return self.toolbar_view

    def initialize_queue(self, queue_items) -> None:
        """Initialize one stable row per queued input."""
        # The row module imports this one's formatters, so it loads on use.
        from ui.queue_item_row import QueueItemRow

        self.reset()

        for file_path in queue_items:
            if file_path not in self.queue_items:
                row = QueueItemRow(self.app, file_path, self)
                self.queue_items[file_path] = row
                self.queue_listbox.append(row)

        self._update_overall_progress()

    @staticmethod
    def _count_text(singular: str, plural: str, count: int) -> str:
        return ngettext(singular, plural, count).format(count=count)

    def _result_counts(self) -> dict[str, int]:
        counts = {"completed": 0, "failed": 0, "cancelled": 0}
        for row in self.queue_items.values():
            if row.status in counts:
                counts[row.status] += 1
        return counts

    def _all_rows_terminal(self) -> bool:
        return (
            bool(self.queue_items)
            and not self.active_conversions
            and all(row.status in TERMINAL_STATES for row in self.queue_items.values())
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
        """Update aggregate state and the layout in one pass over the queue."""
        self._cancel_overall_progress_update()
        total = len(self.queue_items)
        finished = 0
        active_fraction = 0.0
        for row in self.queue_items.values():
            if row.status in TERMINAL_STATES:
                finished += 1
            elif row.status == "active":
                active_fraction += row.current_progress

        fraction = (
            max(0.0, min(1.0, (finished + active_fraction) / total)) if total else 0.0
        )
        settled = self._summary_shown
        multi = total > 1 and not settled
        self.summary_strip.set_visible(multi)
        self.result_box.set_visible(settled)
        self.result_title.set_visible(settled)
        if settled:
            self.toolbar_view.add_css_class("bvc-result-shown")
        else:
            self.toolbar_view.remove_css_class("bvc-result-shown")
        # A single job and the result summary are centered vertically.
        self.clamp.set_valign(Gtk.Align.START if multi or settled else Gtk.Align.CENTER)
        for row in self.queue_items.values():
            row.apply_layout(hero=total == 1 and not settled)

        # Only the running video has a time left: the length of the videos still
        # waiting is unknown, so a queue-wide estimate would be a guess.
        percent = format_percent(fraction)
        strip = ngettext(
            "{finished} of {total} finished · {percent}",
            "{finished} of {total} finished · {percent}",
            total,
        ).format(finished=finished, total=total, percent=percent)
        value_text = percent
        if self.strip_label.get_text() != strip:
            self.strip_label.set_text(strip)
        # GtkProgressBar drops the value text whenever its fraction is set.
        self.overall_progress_bar.set_fraction(fraction)
        self.overall_progress_bar.update_property(
            [Gtk.AccessibleProperty.VALUE_TEXT], [value_text]
        )

        self.cancel_all_button.set_visible(total > 1 and finished < total)
        self.title_label.set_visible(not settled)
        if total and finished < total:
            self.title_label.set_text(
                ngettext("Converting video", "Converting videos", total)
            )
        elif not total:
            self.title_label.set_text(_("Conversion progress"))

    def add_conversion(self, command_title, input_file: str, process):
        """Start tracking a conversion for a file"""
        from ui.queue_item_row import QueueItemRow

        self._cancel_completion_summary()
        self._set_summary_shown(False)
        conversion_id = f"conversion_{self.count}"
        self.count += 1

        if not input_file:
            input_file = command_title or "unknown"

        if len(self.active_conversions) == 0:
            self.app.show_progress_page()

        if input_file in self.queue_items:
            row = self.queue_items[input_file]
        else:
            row = QueueItemRow(self.app, input_file, self)
            self.queue_items[input_file] = row
            self.queue_listbox.prepend(row)

        row.start_conversion(process, conversion_id)

        self.active_conversions[conversion_id] = {
            "row": row,
            "input_file": input_file,
        }

        self._update_overall_progress()
        return row

    def finish_pending(
        self,
        file_path,
        *,
        success: bool = False,
        cancelled: bool = False,
        reason: str | None = None,
    ) -> bool:
        """Settle a job rejected before an active process was registered."""
        row = self.queue_items.get(file_path)
        if row is None or row.status != "pending":
            return False
        if cancelled:
            row.mark_cancelled()
        else:
            row.mark_complete(success, reason)
        self._update_overall_progress()
        self._check_all_complete()
        return True

    def mark_conversion_complete(
        self, conversion_id: str, success: bool = True
    ) -> bool:
        """Settle one registered active conversion exactly once."""
        if conversion_id not in self.active_conversions:
            return False
        conv_data = self.active_conversions.pop(conversion_id)
        row = conv_data.get("row")

        if row and row.status not in TERMINAL_STATES:
            row.mark_complete(success)

        self._update_overall_progress()
        self._check_all_complete()
        return True

    def _cancel_overall_progress_update(self) -> None:
        if self._aggregate_source_id is not None:
            GLib.source_remove(self._aggregate_source_id)
            self._aggregate_source_id = None

    def request_overall_progress_update(self) -> None:
        """Coalesce frequent per-frame updates into one main-loop refresh."""
        if self._aggregate_source_id is None:
            self._aggregate_source_id = GLib.idle_add(self._on_overall_progress_update)

    def _on_overall_progress_update(self):
        self._aggregate_source_id = None
        self._update_overall_progress()
        return GLib.SOURCE_REMOVE

    def request_output_lookup(self) -> None:
        """Find published outputs once the supervisor has recorded them.

        The supervisor settles a row before it appends the job's result to
        `app.completed_conversions`, so the lookup waits for an idle.
        """
        if self._output_source_id is None:
            self._output_source_id = GLib.idle_add(self._on_output_lookup)

    def _on_output_lookup(self):
        self._output_source_id = None
        self._resolve_outputs()
        return GLib.SOURCE_REMOVE

    def _resolve_outputs(self) -> None:
        records = getattr(self.app, "completed_conversions", None) or []
        for row in self.queue_items.values():
            if row.status != "completed" or row.output_paths is not None:
                continue
            job_id = getattr(row, "job_id", None)
            for record in reversed(records):
                matches = (
                    record.get("job_id") == job_id
                    if job_id
                    else record.get("input_file") == row.file_path
                )
                if matches and record.get("success"):
                    paths = record.get("output_files") or [record.get("output_file")]
                    row.set_outputs([path for path in paths if path])
                    break

    def _cancel_completion_summary(self) -> None:
        if self._completion_source_id is not None:
            GLib.source_remove(self._completion_source_id)
            self._completion_source_id = None

    def _check_all_complete(self):
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

    def _set_summary_shown(self, shown: bool) -> None:
        self._summary_shown = shown
        self.back_button.set_visible(shown)
        # The window can only be closed once nothing is running.
        if self.window_buttons_left:
            layout = "close,minimize,maximize:" if shown else "minimize,maximize:"
        else:
            layout = ":minimize,maximize,close" if shown else ":minimize,maximize"
        self.header_bar.set_decoration_layout(layout)

    def _show_completion_summary(self) -> bool:
        """Show a precise summary for a fully settled queue."""
        if not self._all_rows_terminal():
            self._set_summary_shown(False)
            self._update_overall_progress()
            return False

        first_time = not self._summary_shown
        self._resolve_outputs()
        self._set_summary_shown(True)
        self._update_overall_progress()
        self._refresh_result_summary()
        if first_time:
            self.primary_button.grab_focus()
        return True

    def _successful_rows(self):
        return [row for row in self.queue_items.values() if row.status == "completed"]

    def _result_line(self, counts) -> str:
        """What happened, and how long the queue took: one line."""
        results = self._result_summary(counts)
        started = [row for row in self.queue_items.values() if row.started_at]
        if not started:
            return results
        span = max(row.ended_at or row.started_at for row in started) - min(
            row.started_at for row in started
        )
        return _("{results} · {duration}").format(
            results=results, duration=format_duration(span)
        )

    @staticmethod
    def _size_line(successful) -> str:
        before = [row.source_size for row in successful]
        after = [row.output_size for row in successful]
        if not successful or None in before or None in after or not sum(before):
            return ""
        before, after = sum(before), sum(after)
        change = round((after - before) * 100 / before)
        sign = "−" if change < 0 else "+" if change > 0 else ""
        return _("{before} → {after} ({change}%)").format(
            before=format_size(before),
            after=format_size(after),
            change=f"{sign}{abs(change)}",
        )

    def _failure_lines(self) -> str:
        failed = [row for row in self.queue_items.values() if row.status == "failed"]
        lines = [
            _("“{name}”: {reason}").format(
                name=row.display_name, reason=row.failure_reason
            )
            for row in failed[:3]
        ]
        if len(failed) > 3:
            extra = len(failed) - 3
            lines.append(
                ngettext("+{count} more", "+{count} more", extra).format(count=extra)
            )
        return "\n".join(lines)

    def _refresh_result_summary(self) -> None:
        if not self._summary_shown:
            return
        counts = self._result_counts()
        kind, heading, _detail = self._result_messages(counts)
        for name in _RESULT_ICONS:
            self.result_badge.remove_css_class(name)
        if kind in _RESULT_ICONS:
            self.result_badge.add_css_class(kind)
            self.result_icon.set_from_icon_name(_RESULT_ICONS[kind])
        self.result_heading.set_text(heading)

        successful = self._successful_rows()
        line = self._result_line(counts)
        self.result_stats.set_text(line)
        self.result_stats.set_visible(bool(line))
        sizes = self._size_line(successful)
        self.result_sizes.set_text(sizes)
        self.result_sizes.set_visible(bool(sizes))
        self.result_sizes_separator.set_visible(bool(line and sizes))
        reasons = self._failure_lines()
        self.result_reason.set_text(reasons)
        self.result_reason.set_visible(bool(reasons))

        self.primary_button.set_label(
            _("Convert more videos") if kind == "success" else _("Back to queue")
        )

        outputs = [path for row in successful for path in (row.output_paths or [])]
        folders = {os.path.dirname(path) for path in outputs}
        single = len(successful) == 1 and len(outputs) == 1
        self.open_button.set_visible(single)
        folder_content = self.folder_button.get_child()
        folder_content.set_label(_("Show in folder") if single else _("Open folder"))
        self.folder_button.set_visible(bool(outputs) and len(folders) == 1)
        self.result_actions.set_visible(
            self.open_button.get_visible() or self.folder_button.get_visible()
        )

    def _result_outputs(self):
        return [
            path for row in self._successful_rows() for path in (row.output_paths or [])
        ]

    def _on_open_result(self, button):
        outputs = self._result_outputs()
        if outputs:
            launch_file(self.app, button, outputs[0])

    def _on_open_result_folder(self, button):
        outputs = self._result_outputs()
        if len(outputs) == 1:
            launch_file(self.app, button, outputs[0], containing=True)
        elif outputs:
            launch_file(self.app, button, os.path.dirname(outputs[0]))

    def _on_cancel_all_clicked(self, button):
        """Show confirmation dialog before cancelling all"""
        dialog = Adw.AlertDialog()
        dialog.set_heading(_("Cancel all conversions?"))
        dialog.set_body(
            _(
                "This will stop all active conversions and skip all pending "
                "items. This action cannot be undone."
            )
        )
        dialog.add_response("cancel", _("Keep converting"))
        dialog.add_response("confirm", _("Cancel all"))
        dialog.set_response_appearance("confirm", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._on_cancel_all_response)
        # Use the app's window as parent, not the app itself
        window = (
            self.app.get_active_window()
            if hasattr(self.app, "get_active_window")
            else self.app
        )
        dialog.present(window)

    def _on_cancel_all_response(self, dialog, response):
        if response == "confirm":
            self._do_cancel_all()

    def _do_cancel_all(self):
        self.app.is_cancellation_requested = True
        for info in list(getattr(self.app, "active_conversions", [])):
            token = info.get("cancel_event")
            if token is not None:
                token.set()
        self.app.conversion_queue.clear()

        for conv_data in list(self.active_conversions.values()):
            row = conv_data.get("row")
            if row:
                row.cancel(cancel_all=True)

        for row in self.queue_items.values():
            if row.status == "pending":
                row.cancel(cancel_all=True)

    def _on_back_clicked(self, button):
        """Go back to main view"""
        self.app.return_to_main_view()

    def reset(self) -> None:
        self._cancel_completion_summary()
        self._cancel_overall_progress_update()
        if self._output_source_id is not None:
            GLib.source_remove(self._output_source_id)
            self._output_source_id = None
        for row in self.queue_items.values():
            row.dispose()
        self.active_conversions.clear()
        self.queue_items.clear()
        self.count = 0

        while True:
            row = self.queue_listbox.get_first_child()
            if row:
                self.queue_listbox.remove(row)
            else:
                break

        self._set_summary_shown(False)
        self._update_overall_progress()

    def show_completion_summary(self) -> bool:
        """Public method called by the queue supervisor."""
        self._cancel_completion_summary()
        return self._show_completion_summary()
