"""A converting video's row: its bar, live figures, result and actions."""

import gettext
import logging
import os
import threading
import time
import weakref

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from engine.presets import CODEC_NAMES
from gi.repository import GLib, Gtk, Pango

from ui.progress_page import (
    TERMINAL_STATES,
    TimeLeft,
    format_clock,
    format_duration,
    format_percent,
    format_size,
    format_time_left,
    launch_file,
)

logger = logging.getLogger(__name__)

_ = gettext.gettext
ngettext = gettext.ngettext


def _stat_size_async(owner, paths, apply):
    """Sum file sizes on a worker; `apply(owner, size)` runs on GTK if alive."""
    owner_ref = weakref.ref(owner)

    def deliver(size):
        alive = owner_ref()
        if alive is not None:
            apply(alive, size)
        return GLib.SOURCE_REMOVE

    def work():
        try:
            size = sum(os.stat(path).st_size for path in paths)
        except OSError:
            size = None
        GLib.idle_add(deliver, size)

    threading.Thread(target=work, name="bvc-progress-stat", daemon=True).start()


class QueueItemRow(Gtk.ListBoxRow):
    """One queued video: waiting, converting (with its bar) or its result."""

    def __init__(self, app, file_path, progress_page):
        super().__init__()

        self.app = app
        self.file_path = file_path if file_path else ""
        self.progress_page = progress_page
        self.process = None
        self.conversion_id = None
        self.status = "pending"
        self.current_progress = 0.0
        self._cancelled = False

        # Job settings the supervisor (engine/conversion.py) fills in.
        self.delete_original = False
        # Cut from the original: the original stays.
        self.partial_output = False
        self.skip_trash = False
        self.expected_duration = None
        self.is_segment_batch = False
        self.video_codec = ""

        # Monotonic seconds; sizes in bytes, None until known.
        self.started_at = None
        self.ended_at = None
        self.source_size = None
        self.output_size = None
        # Published files, None until the supervisor has recorded them.
        self.output_paths = None

        self._stage = ""
        self._fps = ""
        self.status_text = ""
        self.failure_reason = ""
        self._time_left = TimeLeft()
        self._hero = False
        self._disposed = False
        self._thumbnail_request = None

        self.set_activatable(False)
        self.set_focusable(False)
        self._build_ui()
        self._set_state("pending")
        self._request_thumbnail()

    def _build_ui(self):
        row = weakref.proxy(self)
        self.add_css_class("bvc-progress-card")
        self.display_name = (
            os.path.basename(self.file_path) if self.file_path else _("Unknown video")
        )
        self.main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_child(self.main_box)

        content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        self.thumbnail_frame = Gtk.Box()
        self.thumbnail_frame.add_css_class("bvc-thumbnail-frame")
        self.thumbnail_frame.add_css_class("bvc-dimmable")
        self.thumbnail_frame.set_valign(Gtk.Align.CENTER)
        self.thumbnail_stack = Gtk.Stack(hhomogeneous=True, vhomogeneous=True)
        self.thumbnail_placeholder = Gtk.Image.new_from_icon_name(
            "video-x-generic-symbolic"
        )
        self.thumbnail_placeholder.add_css_class("dim-label")
        self.thumbnail_stack.add_named(self.thumbnail_placeholder, "placeholder")
        self.thumbnail_picture = Gtk.Picture(can_shrink=True)
        self.thumbnail_picture.set_content_fit(Gtk.ContentFit.COVER)
        picture_frame = Gtk.Overlay()
        self._thumbnail_sizer = Gtk.Box()
        picture_frame.set_child(self._thumbnail_sizer)
        picture_frame.add_overlay(self.thumbnail_picture)
        picture_frame.set_measure_overlay(self.thumbnail_picture, False)
        self.thumbnail_stack.add_named(picture_frame, "picture")
        self.thumbnail_frame.append(self.thumbnail_stack)
        content.append(self.thumbnail_frame)

        center = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        center.set_hexpand(True)
        center.set_valign(Gtk.Align.CENTER)
        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        text.set_hexpand(True)
        text.set_valign(Gtk.Align.CENTER)
        text.add_css_class("bvc-dimmable")
        self.filename_label = Gtk.Label(label=self.display_name, xalign=0)
        # One line: the middle ellipsis keeps the start and the extension.
        self.filename_label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        self.filename_label.set_tooltip_text(self.file_path)
        self.filename_label.add_css_class("title-3")
        text.append(self.filename_label)

        status_line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.status_icon = Gtk.Image(
            pixel_size=16,
            valign=Gtk.Align.START,
            accessible_role=Gtk.AccessibleRole.PRESENTATION,
        )
        status_line.append(self.status_icon)
        self.status_label = Gtk.Label(label=_("Waiting in queue"), xalign=0)
        self.status_label.set_wrap(True)
        self.status_label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.status_label.set_lines(3)
        self.status_label.set_ellipsize(Pango.EllipsizeMode.END)
        status_line.append(self.status_label)
        self.size_label = Gtk.Label(xalign=0, valign=Gtk.Align.START)
        self.size_label.add_css_class("dim-label")
        self.size_label.add_css_class("bvc-metric")
        self.size_label.set_visible(False)
        status_line.append(self.size_label)
        text.append(status_line)
        head.append(text)

        figures = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        figures.set_valign(Gtk.Align.CENTER)
        self.percent_label = Gtk.Label(label=format_percent(0), xalign=1)
        self.percent_label.add_css_class("bvc-metric")
        self.percent_label.add_css_class("bvc-percent")
        figures.append(self.percent_label)
        self.time_left_label = Gtk.Label(xalign=1)
        self.time_left_label.add_css_class("dim-label")
        self.time_left_label.add_css_class("bvc-metric")
        figures.append(self.time_left_label)
        self.figures = figures
        head.append(figures)
        center.append(head)

        self.progress_bar = Gtk.ProgressBar()
        self.progress_bar.add_css_class("bvc-progress-bar")
        self.progress_bar.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [_("Conversion progress for “{}”").format(self.display_name)],
        )
        center.append(self.progress_bar)
        self.metrics_label = Gtk.Label(xalign=0)
        self.metrics_label.add_css_class("caption")
        self.metrics_label.add_css_class("dim-label")
        self.metrics_label.add_css_class("bvc-metric")
        center.append(self.metrics_label)
        content.append(center)

        # Actions live beside a waiting or finished row and in the footer of a
        # running one, where the bar needs the full width.
        self.side_slot = Gtk.Box(valign=Gtk.Align.CENTER)
        content.append(self.side_slot)
        self.main_box.append(content)
        self.footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, margin_top=12)
        self.main_box.append(self.footer)

        self.actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.actions.set_hexpand(True)
        self.details_label = Gtk.Label(label=_("FFmpeg output"))
        self.details_arrow = Gtk.Image.new_from_icon_name("pan-down-symbolic")
        # A finished row keeps only the icon: the words repeat on every row.
        self.details_icon = Gtk.Image.new_from_icon_name("utilities-terminal-symbolic")
        details_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        details_box.append(self.details_icon)
        details_box.append(self.details_label)
        details_box.append(self.details_arrow)
        self.details_button = Gtk.ToggleButton(child=details_box)
        self.details_button.add_css_class("bvc-secondary")
        self.details_button.add_css_class("bvc-quiet")
        self.details_button.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [_("FFmpeg output for “{name}”").format(name=self.display_name)],
        )
        self._set_details_action(False)
        self.details_button.connect(
            "toggled", lambda button: row._on_details_toggled(button)
        )
        self.details_button.set_visible(False)
        self.actions.append(self.details_button)
        spacer = Gtk.Box(hexpand=True)
        self.actions.append(spacer)

        self.open_button = Gtk.Button.new_from_icon_name(
            "media-playback-start-symbolic"
        )
        self.open_button.add_css_class("bvc-icon-button")
        self.open_button.add_css_class("bvc-quiet")
        self.open_button.set_tooltip_text(_("Open video"))
        self.open_button.connect("clicked", lambda button: row._on_open_clicked(button))
        self.open_button.set_visible(False)
        self.actions.append(self.open_button)

        # A plain box, not Adw.ButtonContent: that one labels the button by
        # its visible text and hides which video the action applies to.
        self.cancel_icon = Gtk.Image.new_from_icon_name("process-stop-symbolic")
        self.cancel_label = Gtk.Label(label=_("Cancel"))
        cancel_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        cancel_box.append(self.cancel_icon)
        cancel_box.append(self.cancel_label)
        self.cancel_button = Gtk.Button(child=cancel_box)
        self.cancel_button.add_css_class("bvc-secondary")
        self.cancel_button.add_css_class("bvc-quiet")
        self.cancel_button.connect("clicked", lambda _button: row.cancel())
        self.cancel_button.set_visible(False)
        self.actions.append(self.cancel_button)
        # Open, then the log toggle, then cancel at the far end.
        self.actions.reorder_child_after(self.open_button, None)
        self.side_slot.append(self.actions)

        self.details_revealer = Gtk.Revealer()
        self.details_revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        self.details_revealer.set_transition_duration(180)
        details = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        details.add_css_class("bvc-well")
        details.add_css_class("bvc-details")
        details.set_margin_top(12)
        command_title = Gtk.Label(label=_("Command"))
        command_title.set_xalign(0)
        command_title.add_css_class("bvc-body-strong")
        details.append(command_title)
        self.cmd_text = Gtk.Label(label="")
        self.cmd_text.set_selectable(True)
        self.cmd_text.set_wrap(True)
        self.cmd_text.set_wrap_mode(Pango.WrapMode.CHAR)
        self.cmd_text.set_xalign(0)
        self.cmd_text.add_css_class("monospace")
        # A cut with dozens of segments makes a command of many lines: it
        # scrolls past five.
        command_scroll = Gtk.ScrolledWindow(
            child=self.cmd_text,
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            propagate_natural_height=True,
            overflow=Gtk.Overflow.HIDDEN,
            css_classes=["bvc-log"],
        )
        self.cmd_text.connect(
            "realize",
            lambda label: command_scroll.set_max_content_height(
                label.create_pango_layout("\n" * 4).get_pixel_size()[1]
            ),
        )
        details.append(command_scroll)
        output_title = Gtk.Label(label=_("Process output"))
        output_title.set_xalign(0)
        output_title.add_css_class("bvc-body-strong")
        details.append(output_title)
        log_scroll = Gtk.ScrolledWindow()
        log_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        log_scroll.set_min_content_height(120)
        log_scroll.set_max_content_height(240)
        self.terminal_view = Gtk.TextView()
        self.terminal_view.set_editable(False)
        self.terminal_view.set_cursor_visible(False)
        self.terminal_view.set_monospace(True)
        self.terminal_view.add_css_class("bvc-log")
        self.terminal_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.terminal_view.update_property(
            [Gtk.AccessibleProperty.LABEL], [_("Process output")]
        )
        self.terminal_buffer = self.terminal_view.get_buffer()
        log_scroll.set_child(self.terminal_view)
        details.append(log_scroll)
        self.details_revealer.set_child(details)
        self.main_box.append(self.details_revealer)

    def _request_thumbnail(self):
        manager = getattr(
            getattr(self.app, "conversion_page", None), "thumbnail_cache", None
        )
        if manager is None or not self.file_path:
            return
        row_ref = weakref.ref(self)
        expected = self.file_path

        def ready(thumbnail_path):
            # Worker thread: only marshal plain data back to GTK.
            GLib.idle_add(_apply_thumbnail, row_ref, expected, thumbnail_path)

        self._thumbnail_request = manager.request(self.file_path, ready)

    def dispose(self) -> None:
        """Release the thumbnail request; late worker results are ignored."""
        self._disposed = True
        request, self._thumbnail_request = self._thumbnail_request, None
        if request is not None:
            request.cancel()
        self.thumbnail_picture.set_paintable(None)

    def _set_thumbnail_size(self, compact: bool) -> None:
        width, height = (96, 54) if compact else (144, 82)
        self.thumbnail_stack.set_size_request(width, height)
        self._thumbnail_sizer.set_size_request(width, height)
        self.thumbnail_placeholder.set_pixel_size(28 if compact else 42)
        if compact:
            self.thumbnail_frame.add_css_class("bvc-thumb-compact")
        else:
            self.thumbnail_frame.remove_css_class("bvc-thumb-compact")

    def apply_layout(self, *, hero: bool) -> None:
        """Hero card for the only job; compact list row otherwise."""
        if hero == self._hero:
            return
        self._hero = hero
        if hero:
            self.add_css_class("bvc-progress-hero")
            self.percent_label.add_css_class("title-1")
            self.percent_label.remove_css_class("title-4")
        else:
            self.remove_css_class("bvc-progress-hero")
            self.percent_label.add_css_class("title-4")
            self.percent_label.remove_css_class("title-1")
        self._place_actions()

    def _place_actions(self) -> None:
        running = self.status == "active"
        target = self.footer if running else self.side_slot
        if self.actions.get_parent() is not target:
            self.actions.get_parent().remove(self.actions)
            target.append(self.actions)
        self.footer.set_visible(running)
        self.actions.set_hexpand(running)
        self.details_icon.set_visible(not running)
        self.details_label.set_visible(running)
        self.details_arrow.set_visible(running)
        self._set_thumbnail_size(
            compact=not (self._hero and self.status in ("pending", "active"))
        )
        self.open_button.set_visible(
            self.status == "completed" and bool(self.output_paths)
        )

    def set_outputs(self, paths) -> None:
        """Show the files the supervisor published for this job."""
        self.output_paths = list(paths)
        if not self.output_paths:
            return
        if len(self.output_paths) == 1:
            self.display_name = os.path.basename(self.output_paths[0])
        else:
            count = len(self.output_paths)
            self.display_name = ngettext(
                "{count} output file", "{count} output files", count
            ).format(count=count)
        self.filename_label.set_text(self.display_name)
        self.filename_label.set_tooltip_text("\n".join(self.output_paths))
        self.open_button.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [_("Open “{name}”").format(name=self.display_name)],
        )
        self.details_button.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [_("FFmpeg output for “{name}”").format(name=self.display_name)],
        )
        self._place_actions()
        _stat_size_async(self, self.output_paths, QueueItemRow._set_output_size)

    def _set_output_size(self, size) -> None:
        if self._disposed:
            return
        self.output_size = size
        self.size_label.set_text(format_size(size) if size is not None else "")
        self.size_label.set_visible(size is not None and self.status == "completed")
        if self.progress_page:
            self.progress_page._refresh_result_summary()

    def _set_source_size(self, size) -> None:
        if not self._disposed:
            self.source_size = size
            if self.progress_page:
                self.progress_page._refresh_result_summary()

    def _on_open_clicked(self, button) -> None:
        if not self.output_paths:
            return
        if len(self.output_paths) == 1:
            launch_file(self.app, button, self.output_paths[0])
        else:
            launch_file(self.app, button, self.output_paths[0], containing=True)

    def _set_cancel_action(self, pending: bool) -> None:
        if pending:
            tooltip = _("Skip this video")
            label = _("Skip “{}”").format(self.display_name)
            visible_label = _("Skip")
            icon_name = "media-skip-forward-symbolic"
            # Skipping destroys nothing: a neutral action.
            self.cancel_button.remove_css_class("bvc-cancel-job")
        else:
            tooltip = _("Cancel this conversion")
            label = _("Cancel “{}”").format(self.display_name)
            visible_label = _("Cancel")
            icon_name = "process-stop-symbolic"
            self.cancel_button.add_css_class("bvc-cancel-job")
        self.cancel_label.set_label(visible_label)
        self.cancel_icon.set_from_icon_name(icon_name)
        self.cancel_button.set_tooltip_text(tooltip)
        self.cancel_button.update_property([Gtk.AccessibleProperty.LABEL], [label])

    def _set_details_action(self, expanded: bool) -> None:
        self.details_button.set_tooltip_text(
            _("Hide technical details") if expanded else _("Show technical details")
        )
        self.details_arrow.set_from_icon_name(
            "pan-up-symbolic" if expanded else "pan-down-symbolic"
        )
        self.details_button.update_state(
            [Gtk.AccessibleState.EXPANDED], [int(expanded)]
        )

    def _set_state(self, state):
        for name in ("pending", "active", *TERMINAL_STATES):
            self.remove_css_class(f"bvc-state-{name}")
        self.add_css_class(f"bvc-state-{state}")
        for css_class in (
            "bvc-status-success",
            "bvc-status-error",
            "bvc-status-warning",
        ):
            self.status_label.remove_css_class(css_class)
            self.status_icon.remove_css_class(css_class)
        self.status_label.remove_css_class("dim-label")
        running = state == "active"
        self.figures.set_visible(running)
        self.progress_bar.set_visible(running)
        self.metrics_label.set_visible(running and bool(self.metrics_label.get_text()))
        self.size_label.set_visible(
            state == "completed" and self.output_size is not None
        )
        self.status_icon.set_visible(state in TERMINAL_STATES)
        self.cancel_button.set_visible(state in ("pending", "active"))
        if state == "pending":
            self.status_label.set_text(_("Waiting in queue"))
            self.status_label.add_css_class("dim-label")
            self._set_cancel_action(True)
        elif running:
            self.status_label.add_css_class("dim-label")
            self._set_cancel_action(False)
        else:
            accent, icon = {
                "completed": ("bvc-status-success", "object-select-symbolic"),
                "failed": ("bvc-status-error", "dialog-error-symbolic"),
                "cancelled": ("bvc-status-warning", "action-unavailable-symbolic"),
            }[state]
            # Color is an accent: the icon, and only a short verdict in text.
            if state != "failed":
                self.status_label.add_css_class(accent)
            self.status_icon.add_css_class(accent)
            self.status_icon.set_from_icon_name(icon)
        self._place_actions()

    def _on_details_toggled(self, button):
        is_active = button.get_active()
        self.details_revealer.set_reveal_child(is_active)
        self._set_details_action(is_active)

    def set_video_codec(self, codec: str) -> bool:
        """The encoder the script really chose (it may replace the requested one)."""
        self.video_codec = codec
        if self.status == "active":
            self._show_stage()
        return False

    def _show_stage(self) -> None:
        codec = CODEC_NAMES.get(self.video_codec, "")
        text = self._stage or _("Starting process...")
        if codec:
            text = _("{codec} · {status}").format(codec=codec, status=text)
        self.status_label.set_text(text)

    def start_conversion(self, process, conversion_id: str) -> None:
        self.process = process
        self.conversion_id = conversion_id
        self.status = "active"
        self._cancelled = False
        self.started_at = time.monotonic()
        self.ended_at = None
        self._time_left = TimeLeft()
        self._stage = ""
        self._fps = ""
        self.metrics_label.set_text("")

        self._set_state("active")
        self._show_stage()
        self.progress_bar.set_fraction(0)
        self.details_button.set_visible(True)
        if self.file_path and os.path.isabs(self.file_path):
            _stat_size_async(self, [self.file_path], QueueItemRow._set_source_size)

    def _show_figures(self) -> None:
        fraction = self.current_progress
        percent = format_percent(fraction)
        seconds = self._time_left.update(fraction)
        remaining = format_time_left(seconds) if seconds is not None else ""
        if (
            self.percent_label.get_text() != percent
            or self.time_left_label.get_text() != remaining
        ):
            self.percent_label.set_text(percent)
            self.time_left_label.set_text(remaining)
            self.time_left_label.set_visible(bool(remaining))
        value_text = (
            _("{percent}, {remaining}").format(percent=percent, remaining=remaining)
            if remaining
            else percent
        )
        # GtkProgressBar drops the value text whenever its fraction is set.
        self.progress_bar.update_property(
            [Gtk.AccessibleProperty.VALUE_TEXT], [value_text]
        )
        if self.started_at is not None and fraction > 0:
            elapsed = format_clock(time.monotonic() - self.started_at)
            metrics = (
                _("{fps} · {elapsed}").format(fps=self._fps, elapsed=elapsed)
                if self._fps
                else elapsed
            )
            self.metrics_label.set_text(metrics)
            self.metrics_label.set_visible(True)

    def update_progress(self, fraction, text: str | None = None) -> None:
        safe_fraction = max(0.0, min(1.0, float(fraction)))
        self.current_progress = safe_fraction
        self.progress_bar.set_fraction(safe_fraction)
        if text:
            self.update_status(text)
        if self.status == "active":
            self._show_figures()
        if self.progress_page:
            self.progress_page.request_overall_progress_update()

    def update_status(self, status) -> None:
        # The supervisor appends the encoder speed as "stage | 25 fps".
        stage, separator, fps = status.rpartition(" | ")
        if separator and "fps" in fps.lower():
            status, self._fps = stage, fps
        if self.status == "active":
            self._stage = status
            self._show_stage()
        elif self.status == "failed":
            self._set_failure_detail(status)
        else:
            self._show_result_text(status)
            if self.progress_page:
                self.progress_page._refresh_result_summary()

    def _show_result_text(self, text: str) -> None:
        self.status_text = text
        self.status_label.set_text(" ".join(text.split()))
        self.status_label.set_tooltip_text(None)

    def _set_failure_detail(self, detail: str) -> None:
        # The raw error stays reachable; the label keeps the friendly reason.
        self.status_text = detail
        if detail != self.failure_reason:
            self.status_label.set_tooltip_text(detail)

    def add_output_text(self, text: str) -> None:
        if not text:
            return
        self.details_button.set_visible(True)
        # Most callers pass plain messages without a trailing newline; append one
        # so consecutive entries don't end up glued together in the log view.
        if not text.endswith("\n"):
            text += "\n"
        end_iter = self.terminal_buffer.get_end_iter()
        self.terminal_buffer.insert(end_iter, text)
        # Bound the live GTK buffer; high-volume diagnostics must not exhaust
        # memory or make the main loop progressively slower.
        excess = self.terminal_buffer.get_char_count() - 250000
        if excess > 0:
            self.terminal_buffer.delete(
                self.terminal_buffer.get_start_iter(),
                self.terminal_buffer.get_iter_at_offset(excess),
            )

    def mark_complete(self, success: bool = True, reason: str | None = None) -> None:
        self.ended_at = time.monotonic()
        self.process = None

        if success:
            self.status = "completed"
            self._set_state("completed")
            if self.started_at is not None:
                # How long this file took: a slow one overnight points at a
                # performance problem the queue total would hide.
                self.status_label.set_text(
                    _("Completed in {duration}").format(
                        duration=format_duration(self.ended_at - self.started_at)
                    )
                )
            else:
                self.status_label.set_text(_("Completed"))
            if self.progress_page:
                self.progress_page.request_output_lookup()
        else:
            self.status = "failed"
            self._set_state("failed")
            # One friendly sentence; the raw error goes to the tooltip and log.
            self.failure_reason = reason or _("Failed")
            self._show_result_text(self.failure_reason)

    def mark_cancelled(self) -> None:
        self.status = "cancelled"
        self.ended_at = time.monotonic()
        self._set_state("cancelled")
        self.status_label.set_text(_("Cancelled"))

    def cancel(self, cancel_all: bool = False) -> None:
        if self.status not in ("active", "pending"):
            return

        was_pending = self.status == "pending"

        self._cancelled = True
        token = getattr(self, "cancel_event", None)
        if token is None:
            # Reserved but not launched yet (pre-flight probe, size planning,
            # a pending dialog): the row has no token, its job does.
            token = next(
                (
                    info.get("cancel_event")
                    for info in getattr(self.app, "active_conversions", ())
                    if info.get("file_path") == self.file_path
                ),
                None,
            )
        if token is not None:
            token.set()
        if cancel_all:
            self.app.is_cancellation_requested = True

        self.cancel_button.set_sensitive(False)

        # For pending items, also remove from the conversion queue
        if (
            was_pending
            and self.file_path
            and hasattr(self.app, "conversion_queue")
            and self.file_path in self.app.conversion_queue
        ):
            self.app.conversion_queue.remove(self.file_path)
            logger.debug("Removed %s from queue", os.path.basename(self.file_path))

        if was_pending and self.progress_page:
            self.progress_page.finish_pending(self.file_path, cancelled=True)
        else:
            self.mark_cancelled()

        # The supervisor alone completes active jobs, after reaping the child:
        # completing here could release another job's GPU slot.

    def mark_success(self) -> None:
        self.mark_complete(success=True)
        if self.progress_page:
            self.progress_page.mark_conversion_complete(
                self.conversion_id, success=True
            )

    def mark_failure(
        self, reason: str | None = None, detail: str | None = None
    ) -> None:
        """Fail the job: `reason` is one plain sentence, `detail` the raw error."""
        if reason is None and detail:
            reason = _("The conversion failed. The details are in “FFmpeg output”.")
        self.mark_complete(success=False, reason=reason)
        if detail:
            self._set_failure_detail(detail)
        if self.progress_page:
            self.progress_page.mark_conversion_complete(
                self.conversion_id, success=False
            )

    def was_cancelled(self):
        return self._cancelled or self.status == "cancelled"


def _apply_thumbnail(row_ref, expected, thumbnail_path):
    row = row_ref()
    if (
        row is None
        or row._disposed
        or expected != row.file_path
        or not thumbnail_path
        or not os.path.isfile(thumbnail_path)
    ):
        return GLib.SOURCE_REMOVE
    row.thumbnail_picture.set_filename(thumbnail_path)
    row.thumbnail_stack.set_visible_child_name("picture")
    row.thumbnail_picture.update_property(
        [Gtk.AccessibleProperty.LABEL], [_("Video preview")]
    )
    return GLib.SOURCE_REMOVE
