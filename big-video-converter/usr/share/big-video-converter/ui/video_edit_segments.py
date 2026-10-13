"""Video editor segments mixin: marking, listing and editing the cut segments."""

import gettext

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk

_ = gettext.gettext


class VideoEditSegmentsMixin:
    """Mixin keeping the cut segments in the metadata and the editor."""

    def on_mark_segment_point(self, button) -> None:
        if self.first_segment_point is None:
            # First click: store point and show feedback
            self.first_segment_point = self.current_position
            self.ui.mark_time_label.set_text(
                self.format_time_precise(self.first_segment_point)
            )
            self.ui.mark_time_label.set_visible(True)
            self.ui.mark_cancel_button.set_visible(True)
            self.ui.update_segment_markers()  # Show first mark immediately
        else:
            # Second click: create segment
            second_point = self.current_position
            start_time = min(self.first_segment_point, second_point)
            end_time = max(self.first_segment_point, second_point)
            # mpv's clock can run a little past the probed duration.
            if self.video_duration > 0:
                end_time = min(end_time, self.video_duration)

            if end_time > start_time:
                new_segment = {"start": start_time, "end": end_time}
                self.trim_segments.append(new_segment)
                self.trim_segments.sort(key=lambda s: s["start"])
                self._save_file_metadata()
                self._update_segments_listbox()
                self.ui.update_segment_markers()

            self.on_mark_cancel(None)

    def on_mark_cancel(self, button) -> None:
        self.first_segment_point = None
        self.ui.mark_time_label.set_visible(False)
        self.ui.mark_cancel_button.set_visible(False)
        self.ui.update_segment_markers()  # Hide first mark line

    def _update_segments_listbox(self):
        if not hasattr(self.ui, "segment_items"):
            return
        # The rows read their segment by position when they come on screen.
        items = self.ui.segment_items
        items.splice(
            0, items.get_n_items(), [str(i) for i in range(len(self.trim_segments))]
        )
        self.ui.fit_segment_list(len(self.trim_segments))
        self.ui.segments_stack.set_visible_child_name(
            "list" if self.trim_segments else "empty"
        )
        self.ui.update_segment_actions(len(self.trim_segments))
        self.ui.update_segment_markers()
        if hasattr(self.ui, "pause_panel"):
            self.ui.pause_panel.segments_changed()

    def _on_goto_segment_clicked(self, button, start_time):
        self.ui.position_scale.set_value(start_time)

    def _create_time_input_fields(self, time_seconds=0):
        """Hours, minutes, seconds and milliseconds fields showing ``time_seconds``.

        Rounded as a whole, like format_time_precise: 3.3 is stored as 3.2999…,
        and truncating each part would move an untouched cut.
        """
        total, milliseconds = divmod(round(time_seconds * 1000), 1000)
        hours, total = divmod(total, 3600)
        minutes, seconds = divmod(total, 60)

        fields_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)

        # Hours
        fields_box.append(Gtk.Label(label="h"))
        h_spin = Gtk.SpinButton()
        h_spin.set_range(0, 99)
        h_spin.set_increments(1, 1)
        h_spin.set_value(hours)
        h_spin.set_width_chars(3)
        h_spin.set_tooltip_text(_("Hours"))
        h_spin.update_property([Gtk.AccessibleProperty.LABEL], [_("Hours")])
        fields_box.append(h_spin)

        # Minutes
        fields_box.append(Gtk.Label(label="m"))
        m_spin = Gtk.SpinButton()
        m_spin.set_range(0, 59)
        m_spin.set_increments(1, 1)
        m_spin.set_value(minutes)
        m_spin.set_width_chars(3)
        m_spin.set_tooltip_text(_("Minutes"))
        m_spin.update_property([Gtk.AccessibleProperty.LABEL], [_("Minutes")])
        fields_box.append(m_spin)

        # Seconds
        fields_box.append(Gtk.Label(label="s"))
        s_spin = Gtk.SpinButton()
        s_spin.set_range(0, 59)
        s_spin.set_increments(1, 1)
        s_spin.set_value(seconds)
        s_spin.set_width_chars(3)
        s_spin.set_tooltip_text(_("Seconds"))
        s_spin.update_property([Gtk.AccessibleProperty.LABEL], [_("Seconds")])
        fields_box.append(s_spin)

        fields_box.append(Gtk.Label(label="ms"))
        ms_spin = Gtk.SpinButton()
        ms_spin.set_range(0, 999)
        ms_spin.set_increments(1, 100)
        ms_spin.set_value(milliseconds)
        ms_spin.set_width_chars(4)
        ms_spin.set_tooltip_text(_("Milliseconds"))
        ms_spin.update_property([Gtk.AccessibleProperty.LABEL], [_("Milliseconds")])
        fields_box.append(ms_spin)

        return fields_box, (h_spin, m_spin, s_spin, ms_spin)

    def _get_time_from_spinbuttons(self, spinbuttons):
        hours, minutes, seconds, milliseconds = (
            spin.get_value_as_int() for spin in spinbuttons
        )
        return ((hours * 3600 + minutes * 60 + seconds) * 1000 + milliseconds) / 1000

    def _on_edit_segment_clicked(self, button, segment_index):
        """Show dialog to edit segment start and end times"""
        if segment_index >= len(self.trim_segments):
            return

        segment = self.trim_segments[segment_index]

        dialog = Adw.AlertDialog.new(_("Edit Segment"), None)
        dialog.set_body(_("Edit start and end times for this segment"))

        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        content_box.set_margin_top(12)
        content_box.set_margin_bottom(12)
        content_box.set_margin_start(12)
        content_box.set_margin_end(12)

        start_label = Gtk.Label(label=_("Start Time:"), xalign=0)
        content_box.append(start_label)
        start_fields_box, start_spinbuttons = self._create_time_input_fields(
            segment["start"]
        )
        content_box.append(start_fields_box)

        # End time fields
        end_label = Gtk.Label(label=_("End Time:"), xalign=0)
        end_label.set_margin_top(6)
        content_box.append(end_label)
        end_fields_box, end_spinbuttons = self._create_time_input_fields(segment["end"])
        content_box.append(end_fields_box)

        dialog.set_extra_child(content_box)

        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("save", _("Save"))
        dialog.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("save")

        dialog.connect(
            "response",
            self._on_edit_segment_dialog_response_spinbuttons,
            segment_index,
            start_spinbuttons,
            end_spinbuttons,
        )
        dialog.present(self.app.window)

    def _on_edit_segment_dialog_response_spinbuttons(
        self, dialog, response, segment_index, start_spinbuttons, end_spinbuttons
    ):
        if response == "save":
            try:
                times = self._segment_times(start_spinbuttons, end_spinbuttons)
                if times is None:
                    return

                self.trim_segments[segment_index]["start"] = times[0]
                self.trim_segments[segment_index]["end"] = times[1]
                self.trim_segments.sort(key=lambda s: s["start"])
                self._update_segments_listbox()
                self._save_file_metadata()

            except (ValueError, IndexError) as e:
                self._show_error_dialog(_("Error"), str(e))

    def _segment_times(self, start_spinbuttons, end_spinbuttons):
        """(start, end) typed into a segment dialog, the end clamped to the
        video, or None after telling the user why the times cannot be used."""
        start_time = self._get_time_from_spinbuttons(start_spinbuttons)
        end_time = self._get_time_from_spinbuttons(end_spinbuttons)
        if start_time < 0 or end_time < 0:
            self._show_error_dialog(
                _("Invalid Time"), _("Time values cannot be negative")
            )
            return None
        if self.video_duration > 0:
            # A cut past the end never reaches 100%, so the conversion
            # would not count as finished.
            if start_time >= self.video_duration:
                self._show_error_dialog(
                    _("Invalid Time Range"),
                    _(
                        "Start time must be before the end of the video ({duration})"
                    ).format(duration=self.format_time_precise(self.video_duration)),
                )
                return None
            end_time = min(end_time, self.video_duration)
        if start_time >= end_time:
            self._show_error_dialog(
                _("Invalid Time Range"), _("Start time must be before end time")
            )
            return None
        return start_time, end_time

    def _show_error_dialog(self, title, message):
        error_dialog = Adw.AlertDialog.new(title, None)
        error_dialog.set_body(message)
        error_dialog.add_response("ok", _("OK"))
        error_dialog.set_default_response("ok")
        error_dialog.present(self.app.window)

    def _on_remove_segment_clicked(self, button, segment):
        # Equal start times do not make two selections the same segment.
        self.trim_segments = [s for s in self.trim_segments if s is not segment]
        self._save_file_metadata()
        self._update_segments_listbox()

    def _on_clear_all_segments_with_confirmation(self, button):
        if not self.trim_segments:
            return  # Nothing to clear

        dialog = Adw.AlertDialog.new(_("Clear All Segments?"), None)
        dialog.set_body(
            _("This will remove all trim segments. This action cannot be undone.")
        )

        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("clear", _("Clear All"))
        dialog.set_response_appearance("clear", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")

        dialog.connect("response", self._on_clear_confirmation_response)
        dialog.present(self.app.window)

    def _on_clear_confirmation_response(self, dialog, response):
        if response == "clear":
            self.trim_segments = []
            self._save_file_metadata()
            self._update_segments_listbox()

    def _on_add_manual_segment_clicked(self, button):
        dialog = Adw.AlertDialog.new(_("Add Segment Manually"), None)
        dialog.set_body(_("Enter start and end times for the new segment"))

        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        content_box.set_margin_top(12)
        content_box.set_margin_bottom(12)
        content_box.set_margin_start(12)
        content_box.set_margin_end(12)

        start_label = Gtk.Label(label=_("Start Time:"), xalign=0)
        content_box.append(start_label)
        start_fields_box, start_spinbuttons = self._create_time_input_fields(0)
        content_box.append(start_fields_box)

        # End time fields
        end_label = Gtk.Label(label=_("End Time:"), xalign=0)
        end_label.set_margin_top(6)
        content_box.append(end_label)
        end_fields_box, end_spinbuttons = self._create_time_input_fields(0)
        content_box.append(end_fields_box)

        dialog.set_extra_child(content_box)

        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("add", _("Add Segment"))
        dialog.set_response_appearance("add", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("add")

        dialog.connect(
            "response",
            self._on_add_manual_segment_response_spinbuttons,
            start_spinbuttons,
            end_spinbuttons,
        )
        dialog.present(self.app.window)

    def _on_add_manual_segment_response_spinbuttons(
        self, dialog, response, start_spinbuttons, end_spinbuttons
    ):
        if response == "add":
            try:
                times = self._segment_times(start_spinbuttons, end_spinbuttons)
                if times is None:
                    return

                new_segment = {"start": times[0], "end": times[1]}
                self.trim_segments.append(new_segment)
                self.trim_segments.sort(key=lambda s: s["start"])
                self._update_segments_listbox()
                self._save_file_metadata()

            except (ValueError, IndexError) as e:
                self._show_error_dialog(_("Error"), str(e))

    def _on_output_mode_changed(self, combo, pspec):
        selected = combo.get_selected()
        self.output_mode = "join" if selected == 0 else "split"
        self._save_file_metadata()
        # Save to global settings as the new default for future videos
        if not self._restoring_metadata:
            self.settings.save_setting("multi-segment-output-mode", self.output_mode)
